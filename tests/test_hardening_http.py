"""R-29: endpoints mutativos o con coste autenticados, con Origin/Host y límites."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from app_helpers import EVIL, ORIGIN, auth, login, make_app


def test_tts_without_token_is_401(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    assert client.post("/tts", json={"text": "hola"}).status_code == 401
    assert client.post("/stt", content=b"x", headers={"origin": ORIGIN}).status_code == 401


def test_valid_client_can_speak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    res = client.post("/tts", json={"text": "Buenas tardes"}, headers=auth(token))
    assert res.status_code == 200
    assert res.content == b"RIFFwav"


def test_expired_token_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    app.state.auth._clock = lambda: time.time() + 3600
    assert client.post("/tts", json={"text": "hola"}, headers=auth(token)).status_code == 401


def test_token_is_bound_to_its_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    other_local = "http://localhost:8787"
    res = client.post("/tts", json={"text": "hola"}, headers=auth(token, other_local))
    assert res.status_code == 401


def test_foreign_origin_is_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    assert client.post("/tts", json={"text": "x"}, headers=auth(token, EVIL)).status_code == 403
    assert client.post("/api/sesion", headers={"origin": EVIL}).status_code == 403


def test_cross_site_fetch_is_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    headers = {**auth(token), "sec-fetch-site": "cross-site"}
    assert client.post("/tts", json={"text": "x"}, headers=headers).status_code == 403


def test_dns_rebinding_host_is_421(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    assert client.get("/health", headers={"host": "evil.example:8787"}).status_code == 421
    assert client.get("/", headers={"host": "127.0.0.1:9999"}).status_code == 421


def test_session_needs_the_boot_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    res = client.post("/api/sesion", headers={"origin": ORIGIN})
    assert res.status_code == 401
    assert res.json()["reload"] is True


def test_boot_cookie_is_httponly_and_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    cookie = client.get("/").headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_rate_limit_returns_429(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def tight(settings) -> None:
        settings.seguridad.tts_por_minuto = 2

    _app, client = make_app(tmp_path, monkeypatch, configure=tight)
    token = login(client)
    codes = [
        client.post("/tts", json={"text": "hola"}, headers=auth(token)).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


def test_mcp_reload_needs_token_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = make_app(tmp_path, monkeypatch)
    assert client.post("/api/mcp/recargar", json={"confirmacion": "RECARGAR"}).status_code == 401
    token = login(client)
    assert client.post("/api/mcp/recargar", json={}, headers=auth(token)).status_code == 400
    ok = client.post("/api/mcp/recargar", json={"confirmacion": "RECARGAR"}, headers=auth(token))
    assert ok.status_code == 200
    assert app.state.brain.reloads == 1


def test_read_endpoints_need_a_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    for path in ("/api/estado", "/api/mcp", "/api/permisos", "/api/openclaw/estado"):
        assert client.get(path).status_code == 401, path
    token = login(client)
    assert client.get("/api/estado", headers=auth(token)).status_code == 200


def test_ui_can_lower_but_never_raise_to_libre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    raise_ = client.post("/api/permisos", json={"modo": "libre"}, headers=auth(token))
    assert raise_.status_code == 403
    res = client.post("/api/permisos", json={"modo": "lectura"}, headers=auth(token))
    assert res.status_code == 200 and res.json()["mode"] == "lectura"


def test_admin_libre_only_from_the_cli_with_the_admin_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = make_app(tmp_path, monkeypatch)
    admin = (tmp_path / "home" / "admin.token").read_text("utf-8")
    body = {"minutos": 5, "confirmacion": "ACTIVAR LIBRE"}
    assert client.post("/api/admin/libre", json=body).status_code == 403
    # Un navegador siempre manda Origin: aunque tuviera el token, se rechaza.
    from_browser = {"x-crisvis-admin": admin, "origin": ORIGIN}
    assert client.post("/api/admin/libre", json=body, headers=from_browser).status_code == 403
    wrong_phrase = {"minutos": 5, "confirmacion": "si"}
    cli = {"x-crisvis-admin": admin}
    assert client.post("/api/admin/libre", json=wrong_phrase, headers=cli).status_code == 403
    res = client.post("/api/admin/libre", json=body, headers=cli)
    assert res.status_code == 200 and res.json()["mode"] == "libre"
    assert res.json()["libreUntil"]
    assert client.post("/api/admin/confirmar", headers=cli).json()["mode"] == "confirmar"
    assert app.state.modes.mode == "confirmar"


def test_elevenlabs_stays_off_unless_explicitly_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from crisvis.settings import Settings
    from crisvis.voice.speech import speech_router

    settings = Settings(home=tmp_path)
    settings.voz.elevenlabs_api_key = "clave-de-prueba"
    brain = SimpleNamespace(status=SimpleNamespace(as_frame=lambda: {"ok": True}))
    app = FastAPI()
    app.include_router(speech_router(settings, brain, None))
    health = TestClient(app).get("/health").json()
    assert health["ttsEngine"] == "navegador" and health["sttEngine"] != "elevenlabs"
    settings.voz.elevenlabs_habilitado = True
    assert TestClient(app).get("/health").json()["ttsEngine"] == "elevenlabs"


def test_tokens_and_tickets_never_reach_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from crisvis.app import _RedactTickets

    caplog.set_level(logging.DEBUG)
    app, client = make_app(tmp_path, monkeypatch)
    token = login(client)
    client.post("/tts", json={"text": "hola"}, headers=auth(token))
    client.post("/tts", json={"text": "hola"}, headers=auth("token-falso"))
    ticket = client.post("/api/sesion/ticket", headers=auth(token)).json()["ticket"]
    audit = (tmp_path / "home" / "auditoria.jsonl").read_text("utf-8")
    for secret in (token, ticket, app.state.auth.admin_token, app.state.auth.boot_secret):
        assert secret not in caplog.text
        assert secret not in audit
    line = ("1.2.3.4", f"GET /ws?ticket={ticket} HTTP/1.1")
    record = logging.LogRecord("uvicorn.access", 20, "", 0, '%s "%s"', line, None)
    _RedactTickets().filter(record)
    assert ticket not in str(record.args)
