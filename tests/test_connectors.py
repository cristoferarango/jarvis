"""Elección de modelos y conectores MCP, sin arrancar ningún servidor."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crisvis.brain.connectors import (
    load_specs,
    read_env,
    select_mcp_tools,
    set_secret,
    signature,
    tool_name,
)
from crisvis.brain.openjarvis_brain import OpenJarvisBrain, pick_vision_model
from crisvis.settings import Settings


class FakeEngine:
    def __init__(self, models: list[str]) -> None:
        self.models = models

    def list_models(self) -> list[str]:
        return self.models


def test_a_vision_model_is_never_picked_as_the_chat_model() -> None:
    brain = OpenJarvisBrain(Settings())
    model, _ = brain._pick_model(FakeEngine(["qwen3-vl:4b-instruct", "qwen3:8b"]), "")
    assert model == "qwen3:8b"


def test_the_best_installed_vision_model_is_found() -> None:
    models = ["qwen3:8b", "llava:7b", "qwen3-vl:4b", "qwen3-vl:4b-instruct", "nomic-embed-text"]
    assert pick_vision_model(models) == "qwen3-vl:4b-instruct"
    assert pick_vision_model(["qwen3:8b"]) == ""


def _settings(tmp_path: Path, servers: dict[str, Any]) -> Settings:
    settings = Settings(home=tmp_path)
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return settings


def test_mcp_json_in_the_claude_and_cursor_format(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        {
            "notion": {
                "command": "npx",
                "args": ["-y", "@notionhq/notion-mcp-server"],
                "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"},
            },
            "github": {"url": "https://api.githubcopilot.com/mcp/", "token": "abc"},
            "viejo": {"command": "x", "disabled": True},
        },
    )
    specs = {s.name: s for s in load_specs(settings)}
    assert specs["notion"].command == "npx"
    assert specs["notion"].missing_env() == ["NOTION_TOKEN"]
    assert specs["github"].headers == {"Authorization": "Bearer abc"}
    assert specs["viejo"].disabled


def test_keys_come_from_the_crisvis_env_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path, {"n": {"command": "npx", "env": {"T": "${NOTION_TOKEN}"}}})
    before = signature(settings)
    set_secret(settings, "NOTION_TOKEN", "ntn_123")
    set_secret(settings, "NOTION_TOKEN", "ntn_456")
    assert read_env(tmp_path / ".env") == {"NOTION_TOKEN": "ntn_456"}
    (spec,) = load_specs(settings)
    assert spec.missing_env() == []
    assert spec.expand("${NOTION_TOKEN}") == "ntn_456"
    assert signature(settings) != before


def _tool(name: str, description: str, connector: str) -> Any:
    return SimpleNamespace(
        spec=SimpleNamespace(name=name, description=description), connector=connector
    )


def test_only_the_tools_that_matter_reach_a_small_model() -> None:
    tools = [_tool(f"mcp__github__t{i}", "issues and pull requests", "github") for i in range(30)]
    tools.append(_tool("mcp__notion__search", "Search pages in the workspace", "notion"))
    chosen = select_mcp_tools("busca en notion la página de la reunión", tools, 12)
    assert chosen[0].spec.name == "mcp__notion__search"
    assert len(select_mcp_tools("abre github y mira los issues", tools, 12)) == 12
    assert select_mcp_tools("qué hora es", tools, 12) == []


def test_remote_names_are_prefixed_and_safe() -> None:
    from crisvis.brain.connectors import ServerSpec

    assert tool_name(ServerSpec(name="Home Assistant"), "HassTurnOn") == (
        "mcp__home_assistant__HassTurnOn"
    )
    assert tool_name(ServerSpec(name="x"), "get file.contents") == "mcp__x__get_file_contents"
