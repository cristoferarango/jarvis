"""R-16: CONFIRMAR persistente, LIBRE solo por administración, temporal y auditado."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crisvis.security import PermissionPolicy
from crisvis.security.audit import AuditLog
from crisvis.security.modes import LIBRE_PHRASE, ModeController, ModeError
from crisvis.settings import PermissionSettings, load_settings


def controller(tmp: Path, mode: str = "confirmar", clock=None) -> ModeController:
    kwargs = {"clock": clock} if clock else {}
    return ModeController(
        PermissionPolicy(PermissionSettings(modo=mode)),
        tmp / "permisos.json",
        AuditLog(tmp / "auditoria.jsonl"),
        max_libre_minutes=30,
        **kwargs,
    )


def events(tmp: Path) -> list[dict]:
    return [json.loads(x) for x in (tmp / "auditoria.jsonl").read_text("utf-8").splitlines()]


def test_default_is_confirmar_and_libre_is_unavailable(tmp_path: Path) -> None:
    modes = controller(tmp_path)
    assert modes.mode == "confirmar"
    assert modes.status() == {"mode": "confirmar", "libreUntil": None, "libreAvailable": False}


def test_settings_default_and_migration_from_libre(tmp_path: Path, capsys) -> None:
    assert load_settings(tmp_path / "no-existe.toml").permisos.modo == "confirmar"
    cfg = tmp_path / "crisvis.toml"
    cfg.write_text('[permisos]\nmodo = "libre"\n', encoding="utf-8")
    assert load_settings(cfg).permisos.modo == "confirmar"
    assert "ya no se admite" in capsys.readouterr().err


def test_env_cannot_force_libre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRISVIS_PERMISOS", "libre")
    assert load_settings(tmp_path / "no-existe.toml").permisos.modo == "confirmar"


def test_lectura_and_confirmar_persist_across_restarts(tmp_path: Path) -> None:
    controller(tmp_path).set_user_mode("lectura", who="interfaz", how="api")
    assert controller(tmp_path).mode == "lectura"
    controller(tmp_path).set_user_mode("confirmar", who="interfaz", how="api")
    assert controller(tmp_path).mode == "confirmar"


def test_ui_cannot_raise_to_libre(tmp_path: Path) -> None:
    modes = controller(tmp_path)
    with pytest.raises(ModeError):
        modes.set_user_mode("libre", who="interfaz", how="api")
    assert modes.mode == "confirmar"
    assert events(tmp_path)[-1]["evento"] == "modo_rechazado"


def test_libre_needs_the_phrase_and_a_short_duration(tmp_path: Path) -> None:
    modes = controller(tmp_path)
    with pytest.raises(ModeError):
        modes.enable_libre(10, who="admin", how="cli", confirmation="si")
    with pytest.raises(ModeError):
        modes.enable_libre(600, who="admin", how="cli", confirmation=LIBRE_PHRASE)
    assert modes.mode == "confirmar"


def test_libre_is_audited_expires_and_never_survives_a_restart(tmp_path: Path) -> None:
    now = [1_000.0]
    modes = controller(tmp_path, clock=lambda: now[0])
    seen: list[tuple[str, str]] = []
    modes.on_change(lambda a, b: seen.append((a, b)))
    until = modes.enable_libre(5, who="admin", how="cli", confirmation=LIBRE_PHRASE)
    assert until == 1_300.0
    assert modes.status()["libreUntil"] == 1_300.0
    granted = [e for e in events(tmp_path) if e["evento"] == "libre_habilitado"][-1]
    assert granted["quien"] == "admin" and granted["como"] == "cli" and granted["minutos"] == 5

    # Reinicio con LIBRE activo: vuelve a CONFIRMAR (LIBRE no se escribe a disco).
    assert controller(tmp_path).mode == "confirmar"

    now[0] = 1_301.0
    assert modes.mode == "confirmar"
    assert events(tmp_path)[-1]["evento"] == "libre_caducado"
    assert seen == [("confirmar", "libre"), ("libre", "confirmar")]


def test_disable_libre_goes_back_to_confirmar(tmp_path: Path) -> None:
    modes = controller(tmp_path)
    modes.enable_libre(2, who="admin", how="cli", confirmation=LIBRE_PHRASE)
    modes.disable_libre(who="admin", how="cli")
    assert modes.mode == "confirmar"
