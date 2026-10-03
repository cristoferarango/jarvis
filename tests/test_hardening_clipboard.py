"""R-27: el portapapeles ya no es 'lectura' de pc_system."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from crisvis.body import build_body_tools
from crisvis.body.pc import clipboard_tool, mask_secrets, pc_tools
from crisvis.security.policy import Tier, classify
from crisvis.settings import load_settings
from gate_helpers import SPEC, GateSession

SECRET = "hunter2-SuperSecreta"


class Surface:
    def push(self, frame):  # pragma: no cover - no se usa
        pass


def test_pc_system_no_longer_reads_the_clipboard() -> None:
    system = next(t for t in pc_tools(see=None) if t.spec.name == "pc_system")
    assert "portapapeles" not in system.spec.parameters["properties"]["info"]["enum"]
    result = system.execute(info="portapapeles")
    assert not result.success
    assert "read_clipboard" in result.content
    assert classify("pc_system") is Tier.LECTURA


def test_read_clipboard_is_dangerous_and_confirmed() -> None:
    tool = clipboard_tool()
    assert classify("read_clipboard") is Tier.PELIGROSO
    assert tool.spec.requires_confirmation


async def test_read_clipboard_is_blocked_without_approval(tmp_path: Path) -> None:
    session = GateSession(tmp_path, reply=lambda _p: {"approved": False})
    refusal = await session.gate("read_clipboard", {}, SPEC)
    assert refusal and "no autorizó" in refusal
    asked = session.asked[0]
    assert asked["risk"] == "high"
    assert "contraseñas" in asked["warning"]


async def test_read_clipboard_asks_every_time_even_in_libre(tmp_path: Path) -> None:
    session = GateSession(tmp_path, mode="libre")
    assert await session.gate("read_clipboard", {}, SPEC) is None
    assert await session.gate("read_clipboard", {}, SPEC) is None
    assert len(session.asked) == 2


def test_clipboard_content_is_masked_and_never_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = types.ModuleType("pyperclip")
    fake.paste = lambda: f"contraseña: {SECRET}\ntarjeta 4111 1111 1111 1111\nhola"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyperclip", fake)
    caplog.set_level("DEBUG")
    result = clipboard_tool().execute()
    assert result.success
    assert SECRET not in result.content
    assert "4111 1111 1111 1111" not in result.content
    assert "hola" in result.content
    assert SECRET not in caplog.text


async def test_audit_has_no_clipboard_content(tmp_path: Path) -> None:
    session = GateSession(tmp_path)
    assert await session.gate("read_clipboard", {}, SPEC) is None
    raw = session.audit_path.read_text("utf-8")
    assert "read_clipboard" in raw
    assert SECRET not in raw


def test_masking_handles_common_secrets() -> None:
    text = mask_secrets("api_key=sk-abcdefghijklmnopqrstuvwxyz0123456789 pin: 1234 ok")
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in text
    assert "1234" not in text
    assert "ok" in text


def test_clipboard_tool_can_be_disabled_by_config(tmp_path: Path) -> None:
    cfg = tmp_path / "crisvis.toml"
    cfg.write_text('[seguridad]\nportapapeles = "deshabilitado"\n', encoding="utf-8")
    assert load_settings(cfg).seguridad.portapapeles == "deshabilitado"
    on = build_body_tools(Surface(), interface=False, camera=False, see=None, pc=True,
                          clipboard=True)
    off = build_body_tools(Surface(), interface=False, camera=False, see=None, pc=True,
                           clipboard=False)
    assert "read_clipboard" in {t.spec.name for t in on}
    assert "read_clipboard" not in {t.spec.name for t in off}


def test_bad_clipboard_setting_is_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "crisvis.toml"
    cfg.write_text('[seguridad]\nportapapeles = "siempre"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(cfg)
