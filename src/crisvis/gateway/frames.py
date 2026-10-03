"""Esquema estricto de las tramas que manda la cara.

Cada tipo tiene sus claves permitidas, sus tipos, su tamaño máximo y su lugar
en la secuencia (``hello`` primero). Una trama que intente fijar el modo, la
herramienta, el riesgo, la aprobación, la identidad o un resultado se rechaza:
eso lo decide solo el núcleo.
"""

from __future__ import annotations

import json
from typing import Any

MAX_FRAME_CHARS = 12 * 1024 * 1024  # solo `reply` (una rejilla de cámara en base64)
MAX_ASK_CHARS = 8000

_SIZE = {
    "hello": 512,
    "ask": 48_000,
    "interrupt": 128,
    "reply": MAX_FRAME_CHARS,
    "briefing": 1024,
    "propose": 512,
}
_FIELDS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "hello": {"session": str, "protocol": int},
    "ask": {"text": str, "id": str, "source": str},
    "interrupt": {},
    "reply": {
        "id": str,
        "approved": bool,
        "fingerprint": str,
        "grant": str,
        "nonce": str,
        "data": str,
        "mimeType": str,
        "error": str,
    },
    "briefing": {"action": str, "source": str},
    "propose": {"proposal": str},
}
_REQUIRED = {"ask": {"text"}, "reply": {"id"}, "briefing": {"action"}, "propose": {"proposal"}}
_SHORT = {"session": 64, "id": 64, "source": 40, "grant": 64, "nonce": 64, "fingerprint": 128,
          "mimeType": 40, "error": 500, "action": 20, "proposal": 80}
# Claves con las que un cliente intentaría decidir por el núcleo.
FORBIDDEN = frozenset(
    {
        "mode", "modo", "permissions", "permisos", "tool", "risk", "tier", "decision",
        "identity", "user", "role", "admin", "result", "outcome", "approval", "libre",
        "executed", "status",
    }
)
REJECTED_TYPES = {"permissions": "el modo de permisos solo se cambia en el núcleo"}


class FrameError(ValueError):
    def __init__(self, reason: str, kind: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


def parse(raw: str, *, greeted: bool) -> dict[str, Any]:
    if len(raw) > MAX_FRAME_CHARS:
        raise FrameError("trama demasiado grande")
    try:
        msg = json.loads(raw)
    except ValueError as exc:
        raise FrameError("JSON inválido") from exc
    if not isinstance(msg, dict):
        raise FrameError("la trama no es un objeto")
    kind = msg.get("type")
    if not isinstance(kind, str):
        raise FrameError("trama sin tipo")
    if kind in REJECTED_TYPES:
        raise FrameError(REJECTED_TYPES[kind], kind)
    if kind not in _FIELDS:
        raise FrameError("tipo de trama desconocido", kind[:20])
    if len(raw) > _SIZE[kind]:
        raise FrameError("trama demasiado grande para su tipo", kind)
    if not greeted and kind != "hello":
        raise FrameError("la primera trama debe ser hello", kind)
    if greeted and kind == "hello":
        raise FrameError("hello repetido", kind)
    allowed = _FIELDS[kind]
    for key, value in msg.items():
        if key == "type":
            continue
        if key in FORBIDDEN:
            raise FrameError(f"clave reservada al núcleo: {key}", kind)
        if key not in allowed:
            raise FrameError(f"clave no permitida: {key}", kind)
        expected = allowed[key]
        if value is None:
            continue
        if expected is int and isinstance(value, bool):
            raise FrameError(f"tipo incorrecto en {key}", kind)
        if not isinstance(value, expected):
            raise FrameError(f"tipo incorrecto en {key}", kind)
        limit = _SHORT.get(key)
        if limit is not None and isinstance(value, str) and len(value) > limit:
            raise FrameError(f"{key} demasiado largo", kind)
    missing = _REQUIRED.get(kind, set()) - {k for k, v in msg.items() if v is not None}
    if missing:
        raise FrameError(f"faltan claves: {', '.join(sorted(missing))}", kind)
    return msg
