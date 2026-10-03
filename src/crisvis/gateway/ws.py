"""El WebSocket /ws: el único canal entre la cara y el cerebro.

Para abrirlo hace falta un ticket de un solo uso (``/ws?ticket=…``) emitido a
un cliente con sesión válida (ver ``crisvis.security.auth``), un Host local y
el mismo Origin que pidió el ticket. Cada trama pasa por un esquema estricto
(``frames.py``); el cliente no puede fijar el modo, el riesgo, la aprobación ni
la identidad. Los intentos inválidos quedan en la auditoría.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from crisvis.gateway.frames import MAX_ASK_CHARS, FrameError, parse
from crisvis.gateway.protocol import PROTOCOL_VERSION
from crisvis.gateway.session import Session, SessionRegistry
from crisvis.security.auth import LocalAuth
from crisvis.security.ratelimit import SlidingWindow
from crisvis.security.telemetry import SOURCES, InputTelemetry

log = logging.getLogger(__name__)

MAX_INVALID_FRAMES = 20
FRAMES_PER_10S = 60
ASKS_PER_MINUTE = 20


def ws_router(
    registry: SessionRegistry,
    auth: LocalAuth,
    telemetry: InputTelemetry,
    *,
    max_connections: int = 6,
) -> APIRouter:
    router = APIRouter()
    audit = registry.audit

    @router.websocket("/ws")
    async def socket(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        host = ws.headers.get("host")
        if not auth.host_allowed(host) or not auth.origin_ok(origin):
            audit.event(
                "ws_rechazado", motivo="host u origen", origen=origin or "", host=host or ""
            )
            log.warning("WebSocket rechazado: host=%r origen=%r", host, origin)
            await ws.close(code=4403)
            return
        ticket = auth.redeem_ticket(ws.query_params.get("ticket"), origin)
        if ticket is None:
            audit.event("ws_rechazado", motivo="ticket ausente, inválido, caducado o reutilizado",
                        origen=origin or "")
            await ws.close(code=4401)
            return
        if registry.connections >= max_connections:
            audit.event("ws_rechazado", motivo="demasiadas conexiones", origen=origin or "")
            await ws.close(code=4429)
            return
        await ws.accept()

        async def send(frame: dict[str, Any]) -> None:
            await ws.send_text(json.dumps(frame, ensure_ascii=False))

        async def close(code: int, reason: str) -> None:
            await ws.close(code=code, reason=reason[:120])

        frames = SlidingWindow(FRAMES_PER_10S, 10)
        asks = SlidingWindow(ASKS_PER_MINUTE, 60)
        session: Session = registry.create(ticket.client)
        await session.attach(send, close)
        session.send(
            registry.status_frame(
                "ready", session=session.id, resumed=False, protocol=PROTOCOL_VERSION
            )
        )
        greeted = False
        invalid = 0

        def reject(reason: str, kind: str = "") -> None:
            audit.event(
                "trama_invalida", sesion=session.id[:8], tipo=kind, motivo=reason,
                origen=origin or "",
            )
            session.send({"type": "error", "message": f"Trama rechazada: {reason}."})

        try:
            while True:
                raw = await ws.receive_text()
                if session._send is not send:
                    # Otra pestaña retomó esta sesión: este socket ya no manda.
                    await ws.close(code=4409)
                    return
                if not frames.allow("f"):
                    audit.event("ws_limite", sesion=session.id[:8], origen=origin or "")
                    await ws.close(code=4429)
                    return
                try:
                    msg = parse(raw, greeted=greeted)
                except FrameError as exc:
                    invalid += 1
                    reject(exc.reason, exc.kind)
                    if invalid >= MAX_INVALID_FRAMES:
                        await ws.close(code=4400)
                        return
                    continue
                kind = msg["type"]

                if kind == "hello":
                    greeted = True
                    wanted = msg.get("session")
                    previous = registry.get(wanted) if isinstance(wanted, str) and wanted else None
                    if (
                        previous is not None
                        and previous is not session
                        and previous.owner == ticket.client
                    ):
                        await session.detach(send)
                        registry.drop(session)
                        session = previous
                        await session.attach(send, close)
                        session.send(
                            registry.status_frame(
                                "ready", session=session.id, resumed=True, protocol=PROTOCOL_VERSION
                            )
                        )
                    elif previous is not None and previous.owner != ticket.client:
                        audit.event("sesion_ajena", sesion=session.id[:8], origen=origin or "")

                elif kind == "ask":
                    text = str(msg.get("text") or "").strip()[:MAX_ASK_CHARS]
                    source = msg.get("source") if msg.get("source") in SOURCES else "desconocido"
                    ask_id = msg.get("id") if isinstance(msg.get("id"), str) else None
                    accepted = bool(text) and asks.allow("a")
                    event_id = telemetry.record(
                        text=text,
                        session=session.id,
                        source=str(source),
                        authenticated=True,
                        accepted=accepted,
                        origin=origin,
                        reason="" if accepted else ("vacía" if not text else "límite"),
                    )
                    session.send(
                        {
                            "type": "input",
                            "ask": ask_id,
                            "event": event_id,
                            "accepted": accepted,
                            **({} if accepted else {"message": "Entrada rechazada."}),
                        }
                    )
                    if accepted:
                        await session.ask(text, ask_id)

                elif kind == "interrupt":
                    await session.stop_turn()

                elif kind == "reply":
                    session.on_reply(msg["id"], msg)

                elif kind == "briefing":
                    session.spawn(session.briefing_request(msg))

                elif kind == "propose":
                    session.spawn(session.propose(msg["proposal"]))
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            # Starlette lanza RuntimeError si el socket se cierra a mitad de lectura.
            log.debug("socket cerrado: %s", exc)
        finally:
            await session.detach(send)

    return router
