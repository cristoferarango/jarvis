"""Pestañas del informe y respuestas siempre en español."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from openjarvis.engine._stubs import StreamChunk

from crisvis.body.desktop import web_url
from crisvis.brain.agent import CrisvisVoiceAgent, strip_cjk
from crisvis.brain.events import TextDelta
from crisvis.brain.persona import build_system_prompt, language_reminder
from crisvis.openclaw.audit import AdapterAudit
from crisvis.openclaw.tabs import open_tabs, safe_tab_url, tab_urls
from crisvis.security.guard import Risk, open_assessment
from crisvis.settings import Settings
from test_agent import Echo, FakeEngine, call, collect

# -- pestañas -------------------------------------------------------------------


def test_briefing_opens_no_tabs_by_default() -> None:
    assert Settings().openclaw.abrir_al_informe == []


# -- abrir un sitio cuando se pide ----------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["youtube.com", "www.youtube.com", "mail.google.com", "www.notion.so", "YouTube", "gmail",
     "Notion", "https://www.youtube.com/watch?v=abc",
     "https://www.youtube.com/results?search_query=bad+bunny+mix"],
)
def test_asking_for_a_website_is_not_blocked(target: str) -> None:
    verdict = open_assessment(target)
    assert not verdict.blocked
    assert verdict.risk is Risk.MEDIUM


@pytest.mark.parametrize(
    "target", ["notepad.exe", "format.com", "cmd.com", "script.ps1", "virus.bat", "cmd"]
)
def test_executables_that_look_like_domains_stay_blocked(target: str) -> None:
    assert open_assessment(target).blocked


def test_web_targets_resolve_to_https() -> None:
    assert web_url("youtube") == "https://www.youtube.com/"
    assert web_url("Gmail") == "https://mail.google.com/"
    assert web_url("notion") == "https://www.notion.so/"
    assert web_url("youtube.com") == "https://youtube.com"
    assert web_url("C:/Users/yo/notas.txt") is None
    assert web_url("Bloc de notas") is None


def test_websites_go_to_the_browser_never_to_startfile(monkeypatch: pytest.MonkeyPatch) -> None:
    from crisvis.body import desktop

    opened: list[str] = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: opened.append(url) or True)

    def no_startfile(target: str) -> None:
        raise AssertionError(f"startfile({target!r})")

    monkeypatch.setattr(desktop, "_startfile", no_startfile)
    d = desktop.Desktop()
    assert "youtube.com" in d.open("youtube.com")
    d.open("YouTube")
    assert opened == ["https://youtube.com", "https://www.youtube.com/"]


def test_only_https_without_credentials_is_opened() -> None:
    assert safe_tab_url("https://www.youtube.com/") == "https://www.youtube.com/"
    for bad in (
        "http://mail.google.com/",
        "javascript:alert(1)",
        "file:///C:/Windows/System32/cmd.exe",
        "https://user:pass@example.com/",
        "https:///sin-host",
        "notion.so",
    ):
        assert safe_tab_url(bad) is None, bad
    ok, rejected = tab_urls(["https://a.com/", "https://a.com/", "http://b.com/"])
    assert ok == ["https://a.com/"] and rejected == ["http://b.com/"]


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_tabs_open_one_after_another_and_are_audited(tmp_path: Path) -> None:
    audit = AdapterAudit(tmp_path / "a.jsonl")
    opened: list[str] = []
    n = await open_tabs(
        ["https://mail.google.com/", "javascript:x", "https://www.youtube.com/"],
        interval=0,
        audit=audit,
        session="sesion-1",
        opener=lambda url: opened.append(url) or True,
    )
    assert n == 2 and opened == ["https://mail.google.com/", "https://www.youtube.com/"]
    events = _events(tmp_path / "a.jsonl")
    assert [e["outcome"] for e in events].count("abierta") == 2
    assert any(e["outcome"].startswith("rechazada") for e in events)
    assert all(e["kind"] == "briefing.tab" for e in events)


async def test_stop_cancels_the_tabs_still_pending(tmp_path: Path) -> None:
    audit = AdapterAudit(tmp_path / "a.jsonl")
    opened: list[str] = []
    task = asyncio.create_task(
        open_tabs(
            ["https://a.com/", "https://b.com/", "https://c.com/"],
            interval=5,
            audit=audit,
            opener=lambda url: opened.append(url) or True,
        )
    )
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert opened == ["https://a.com/"]


def test_tests_never_open_real_tabs(_no_real_browser_tabs: list[str]) -> None:
    from crisvis.openclaw import tabs

    assert tabs._open("https://www.notion.so/") is True
    assert _no_real_browser_tabs == ["https://www.notion.so/"]


# -- español --------------------------------------------------------------------


def test_prompt_demands_spanish_first_and_last() -> None:
    prompt = build_system_prompt(
        nombre="Jarvis", idioma="es-ES", tratamiento="señor", tools=["web_search"]
    )
    assert "IDIOMA, SIN EXCEPCIONES" in prompt
    assert "Nunca escribas caracteres chinos" in prompt
    assert prompt.rstrip().endswith("solo en español.")


def test_prompt_teaches_youtube_search_without_typing() -> None:
    prompt = build_system_prompt(
        nombre="Jarvis", idioma="es-ES", tratamiento="señor", tools=["pc_open"]
    )
    assert "youtube.com/results?search_query=" in prompt


def test_chinese_characters_never_reach_the_voice() -> None:
    assert strip_cjk("Son las tres, 先生 señor.") == "Son las tres,  señor."
    assert strip_cjk("¿Qué tal? Año, niño, acción.") == "¿Qué tal? Año, niño, acción."


async def test_streamed_text_is_cleaned_of_chinese() -> None:
    engine = FakeEngine([[StreamChunk(content="Buenas 你好"), StreamChunk(content=" tardes")]])
    agent = CrisvisVoiceAgent(engine, "m", tools=[])
    events = await collect(agent)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Buenas  tardes"


async def test_tool_results_carry_a_spanish_reminder() -> None:
    engine = FakeEngine([[call("echo", {"text": "hello"})], [StreamChunk(content="Hecho.")]])
    reminder = language_reminder("es-ES")
    agent = CrisvisVoiceAgent(engine, "m", tools=[Echo()], language_reminder=reminder)
    await collect(agent)
    tool_msgs = [m for m in engine.calls[1]["messages"] if getattr(m, "name", None) == "echo"]
    assert tool_msgs and tool_msgs[-1].content.endswith(reminder)
    assert "español" in reminder
