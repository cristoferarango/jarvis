"""R-31: WebSocket con ticket de un solo uso, Origin, esquema estricto y permisos en el núcleo."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from app_helpers import EVIL, ORIGIN, auth, login, make_app
from crisvis.gateway.frames import FrameError, parse


def ws_headers(origin: str = ORIGIN) -> dict[str, str]:
    return {"origin": origin, "host": "127.0.0.1:8787"}


def ticket_for(client, token: str) -> str:
    res = client.post("/api/sesion/ticket", headers=auth(token))
    assert res.status_code == 200
    return res.json()["ticket"]


def closed_with(client, url: str, origin: str = ORIGIN) -> int:
    with pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect(url, headers=ws_headers(origin)) as ws:
            ws.receive_text()
    return info.value.code


def audit(tmp: Path) -> list[dict]:
    path = tmp / "home" / "auditoria.jsonl"
    return [json.loads(x) for x in path.read_text("utf-8").splitlines()] if path.exists() else []


def test_without_ticket_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    assert closed_with(client, "/ws") == 4401
    assert any(e.get("evento") == "ws_rechazado" for e in audit(tmp_path))


def test_invalid_ticket_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    assert closed_with(client, "/ws?ticket=inventado") == 4401


def test_expired_ticket_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, client = make_app(tmp_path, monkeypatch)
    ticket = ticket_for(client, login(client))
    app.state.auth._clock = lambda: time.time() + 120
    assert closed_with(client, f"/ws?ticket={ticket}") == 4401


def test_ticket_is_single_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    ticket = ticket_for(client, login(client))
    with client.websocket_connect(f"/ws?ticket={ticket}", headers=ws_headers()) as ws:
        assert json.loads(ws.receive_text())["type"] == "ready"
    assert closed_with(client, f"/ws?ticket={ticket}") == 4401


def test_forged_origin_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    ticket = ticket_for(client, login(client))
    assert closed_with(client, f"/ws?ticket={ticket}", origin=EVIL) == 4403
    # Otro origen local que no pidió el ticket tampoco vale.
    ticket = ticket_for(client, login(client))
    assert closed_with(client, f"/ws?ticket={ticket}", origin="http://localhost:8787") == 4401


def connect(client):
    ticket = ticket_for(client, login(client))
    return client.websocket_connect(f"/ws?ticket={ticket}", headers=ws_headers())


def test_valid_client_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    with connect(client) as ws:
        ready = json.loads(ws.receive_text())
        assert ready["type"] == "ready" and ready["permissions"]["mode"] == "confirmar"
        ws.send_text(json.dumps({"type": "hello", "session": "", "protocol": 2}))
        ws.send_text(json.dumps({"type": "ask", "text": "hola", "id": "a1", "source": "teclado"}))
        accepted = json.loads(ws.receive_text())
        assert accepted["type"] == "input" and accepted["accepted"] is True
        assert accepted["ask"] == "a1" and accepted["event"]
    # La telemetría de entradas no guarda el texto.
    lines = (tmp_path / "home" / "telemetria-entradas.jsonl").read_text("utf-8").splitlines()
    record = json.loads(lines[-1])
    assert record["source_type"] == "teclado" and record["length"] == 4
    assert record["authenticated"] is True and record["accepted"] is True
    assert "hola" not in lines[-1]


def test_permissions_frame_is_rejected_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = make_app(tmp_path, monkeypatch)
    with connect(client) as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "hello", "session": "", "protocol": 2}))
        ws.send_text(json.dumps({"type": "permissions", "mode": "libre"}))
        error = json.loads(ws.receive_text())
        assert error["type"] == "error"
    assert app.state.modes.mode == "confirmar"
    assert any(e.get("evento") == "trama_invalida" and e.get("tipo") == "permissions"
               for e in audit(tmp_path))


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "ask", "text": "hola", "mode": "libre"},
        {"type": "ask", "text": "hola", "tool": "shell_exec"},
        {"type": "ask", "text": "hola", "risk": "low"},
        {"type": "reply", "id": "q1", "approved": True, "approval": "todo"},
        {"type": "ask", "text": "hola", "identity": "admin"},
        {"type": "ask", "text": "hola", "result": "hecho"},
        {"type": "reply", "id": "q1", "approved": "sí"},
        {"type": "ask"},
        {"type": "nuevo"},
    ],
)
def test_client_cannot_decide_for_the_core(frame: dict) -> None:
    with pytest.raises(FrameError):
        parse(json.dumps(frame), greeted=True)


def test_first_frame_must_be_hello() -> None:
    with pytest.raises(FrameError):
        parse(json.dumps({"type": "ask", "text": "hola"}), greeted=False)
    with pytest.raises(FrameError):
        parse(json.dumps({"type": "hello"}), greeted=True)


def test_oversized_payload_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(FrameError):
        parse(json.dumps({"type": "ask", "text": "x" * 60_000}), greeted=True)
    with pytest.raises(FrameError):
        parse(json.dumps({"type": "hello", "session": "s" * 1000}), greeted=False)
    _app, client = make_app(tmp_path, monkeypatch)
    with connect(client) as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "hello", "session": "", "protocol": 2}))
        ws.send_text(json.dumps({"type": "ask", "text": "x" * 60_000}))
        assert json.loads(ws.receive_text())["type"] == "error"


def test_mode_change_closes_connections_so_they_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    with client:
        token = login(client)
        ticket = ticket_for(client, token)
        with client.websocket_connect(f"/ws?ticket={ticket}", headers=ws_headers()) as ws:
            ws.receive_text()
            ws.send_text(json.dumps({"type": "hello", "session": "", "protocol": 2}))
            res = client.post("/api/permisos", json={"modo": "lectura"}, headers=auth(token))
            assert res.status_code == 200
            with pytest.raises(WebSocketDisconnect) as info:
                while True:
                    ws.receive_text()
            assert info.value.code == 4001


def test_connection_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def one(settings) -> None:
        settings.seguridad.max_conexiones = 1

    _app, client = make_app(tmp_path, monkeypatch, configure=one)
    with client:
        with connect(client) as ws:
            ws.receive_text()
            ticket = ticket_for(client, login(client))
            assert closed_with(client, f"/ws?ticket={ticket}") == 4429


def test_session_of_another_client_cannot_be_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    with client:
        with connect(client) as first:
            victim = json.loads(first.receive_text())["session"]
            first.send_text(json.dumps({"type": "hello", "session": "", "protocol": 2}))
            with connect(client) as second:
                mine = json.loads(second.receive_text())["session"]
                second.send_text(json.dumps({"type": "hello", "session": victim, "protocol": 2}))
                second.send_text(json.dumps({"type": "interrupt"}))
                assert mine != victim
    assert any(e.get("evento") == "sesion_ajena" for e in audit(tmp_path))
