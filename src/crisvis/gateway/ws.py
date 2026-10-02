"""El WebSocket /ws: el único canal entre la cara y el cerebro."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from crisvis.gateway.origin import origin_allowed
from crisvis.gateway.protocol import PROTOCOL_VERSION
from crisvis.gateway.session import Session, SessionRegistry
from crisvis.settings import PERMISSION_MODES, ServerSettings

log = logging.getLogger(__name__)

MAX_FRAME_CHARS = 12 * 1024 * 1024  # una rejilla de cámara en base64 cabe de sobra
MAX_ASK_CHARS = 8000


def ws_router(registry: SessionRegistry, server: ServerSettings) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def socket(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if not origin_allowed(origin, server):
            log.warning("WebSocket rechazado desde el origen %r", origin)
            await ws.close(code=4403)
            return
        await ws.accept()

        async def send(frame: dict[str, Any]) -> None:
            await ws.send_text(json.dumps(frame, ensure_ascii=False))

        session: Session = registry.create()
        await session.attach(send)
        session.send(
            registry.status_frame(
                "ready", session=session.id, resumed=False, protocol=PROTOCOL_VERSION
            )
        )

        try:
            while True:
                raw = await ws.receive_text()
                if session._send is not send:
                    # Otra pestaña retomó esta sesión: este socket ya no manda.
                    await ws.close(code=4409)
                    return
                if len(raw) > MAX_FRAME_CHARS:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")

                if kind == "hello":
                    wanted = msg.get("session")
                    previous = registry.get(wanted) if isinstance(wanted, str) else None
                    if previous is not None and previous is not session:
                        await session.detach(send)
                        registry.drop(session)
                        session = previous
                        await session.attach(send)
                        session.send(
                            registry.status_frame(
                                "ready", session=session.id, resumed=True, protocol=PROTOCOL_VERSION
                            )
                        )

                elif kind == "ask":
                    text = msg.get("text")
                    if isinstance(text, str) and text.strip():
                        ask_id = msg.get("id") if isinstance(msg.get("id"), str) else None
                        await session.ask(text.strip()[:MAX_ASK_CHARS], ask_id)

                elif kind == "interrupt":
                    await session.stop_turn()

                elif kind == "reply":
                    if isinstance(msg.get("id"), str):
                        session.on_reply(msg["id"], msg)

                elif kind == "permissions":
                    mode = msg.get("mode")
                    if mode in PERMISSION_MODES:
                        registry.policy.set_mode(mode)
                        log.info("Modo de permisos: %s", mode)
                        registry.broadcast_status()
        except WebSocketDisconnect:
            pass
        except RuntimeError as exc:
            # Starlette lanza RuntimeError si el socket se cierra a mitad de lectura.
            log.debug("socket cerrado: %s", exc)
        finally:
            await session.detach(send)

    return router
