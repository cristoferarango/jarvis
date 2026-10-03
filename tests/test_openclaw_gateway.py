"""Cliente del Gateway de OpenClaw: solo loopback, métodos cerrados, sin shell."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from crisvis.openclaw import gateway as gw
from crisvis.openclaw.adapter import OpenClawAdapter
from crisvis.openclaw.gateway import GatewayClient, GatewayError, require_loopback, resolve_cli
from crisvis.openclaw.providers.mock import mock_providers
from crisvis.settings import Settings


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:18789", "http://localhost:18789", "ws://127.0.0.1:18789", "http://[::1]:1"],
)
def test_loopback_is_accepted(url: str) -> None:
    assert require_loopback(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:18789",
        "http://192.168.1.10:18789",
        "https://mi-tunel.trycloudflare.com",
        "http://localhost.evil.example:18789",
        "file:///etc/passwd",
    ],
)
def test_non_loopback_is_refused(url: str) -> None:
    with pytest.raises(GatewayError):
        require_loopback(url)


def test_adapter_refuses_public_gateway(tmp_path: Path) -> None:
    settings = Settings()
    settings.home = tmp_path
    settings.openclaw.gateway_url = "http://0.0.0.0:18789"
    adapter = OpenClawAdapter(settings, mock_providers())
    assert adapter.gateway is None
    assert "loopback" in adapter.gateway_error


async def test_only_allowlisted_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GatewayClient("http://127.0.0.1:18789")
    for method in ("chat.send", "sessions.send", "config.set", "exec", "health; rm -rf /"):
        with pytest.raises(GatewayError):
            await client.call(method)


async def test_cli_is_called_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kw})
        body = {"ok": True, "version": "2026.9.8", "token": "secreto"}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(body), stderr="")

    monkeypatch.setattr(gw, "resolve_cli", lambda _hint="": ["node", "openclaw.mjs"])
    monkeypatch.setattr(gw.subprocess, "run", fake_run)
    client = GatewayClient("http://127.0.0.1:18789")
    out = await client.call("health")
    assert out["ok"] is True
    assert out["token"] == "***"
    call = calls[0]
    assert call["shell"] is False
    assert call["argv"][:2] == ["node", "openclaw.mjs"]
    assert call["argv"][2:5] == ["gateway", "call", "health"]
    assert "--port" in call["argv"] and "18789" in call["argv"]


async def test_status_when_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gw, "resolve_cli", lambda _hint="": None)

    async def no_probe(self: GatewayClient) -> dict[str, bool]:
        return {"live": False, "ready": False}

    monkeypatch.setattr(GatewayClient, "probe", no_probe)
    status = await GatewayClient("http://127.0.0.1:18789").status(fresh=True)
    assert status["state"] == "no_instalado"
    assert status["installed"] is False


def test_cmd_shim_is_never_run_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shim = tmp_path / "openclaw.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(gw.shutil, "which", lambda name: str(shim) if name == "openclaw" else None)
    assert resolve_cli() is None
    pkg = tmp_path / "node_modules" / "openclaw"
    pkg.mkdir(parents=True)
    (pkg / "openclaw.mjs").write_text("", encoding="utf-8")
    (pkg / "package.json").write_text(json.dumps({"bin": {"openclaw": "openclaw.mjs"}}), "utf-8")
    monkeypatch.setattr(
        gw.shutil,
        "which",
        lambda name: {"openclaw": str(shim), "node": "C:/node/node.exe"}.get(name),
    )
    assert resolve_cli() == ["C:/node/node.exe", str(pkg / "openclaw.mjs")]
