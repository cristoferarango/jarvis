"""Aprobaciones de un solo uso para cada acción MEDIUM/HIGH.

Un "sí" vale para una acción, no para una orden entera: la aprobación queda
atada a la herramienta, los parámetros exactos, la ventana objetivo, el
timeout, la sesión y el turno, y lleva un nonce que solo se canjea una vez y
caduca enseguida. Cambiar cualquier cosa (otro texto, otra ventana, otro
turno) invalida la huella.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


def fingerprint(
    *, session: str, turn: int, tool: str, args: dict[str, Any], target: str, timeout: float
) -> str:
    clean = {k: v for k, v in args.items() if not k.startswith("_")}
    blob = json.dumps(
        {
            "session": session,
            "turn": turn,
            "tool": tool,
            "args": clean,
            "target": target,
            "timeout": round(float(timeout), 3),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class Grant:
    id: str
    nonce: str
    fingerprint: str
    tool: str
    expires_at: float
    used: bool = False


class GrantBook:
    def __init__(self, ttl_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_s = ttl_s
        self._clock = clock
        self._grants: dict[str, Grant] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        session: str,
        turn: int,
        tool: str,
        args: dict[str, Any],
        target: str,
        timeout: float,
        ttl_s: float | None = None,
    ) -> Grant:
        grant = Grant(
            id=secrets.token_urlsafe(9),
            nonce=secrets.token_urlsafe(18),
            fingerprint=fingerprint(
                session=session, turn=turn, tool=tool, args=args, target=target, timeout=timeout
            ),
            tool=tool,
            expires_at=self._clock() + (ttl_s if ttl_s is not None else self.ttl_s),
        )
        with self._lock:
            self._prune()
            self._grants[grant.id] = grant
        return grant

    def redeem(
        self,
        grant_id: str,
        nonce: str,
        *,
        session: str,
        turn: int,
        tool: str,
        args: dict[str, Any],
        target: str,
        timeout: float,
    ) -> tuple[bool, str]:
        """Canjea la aprobación si coincide en todo. Devuelve (vale, motivo)."""
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                return False, "aprobación desconocida"
            if grant.used:
                return False, "aprobación ya utilizada"
            if self._clock() > grant.expires_at:
                self._grants.pop(grant_id, None)
                return False, "aprobación caducada"
            if not hmac.compare_digest(grant.nonce, nonce or ""):
                return False, "nonce incorrecto"
            expected = fingerprint(
                session=session, turn=turn, tool=tool, args=args, target=target, timeout=timeout
            )
            if not hmac.compare_digest(grant.fingerprint, expected):
                return False, "la acción no coincide con la aprobada"
            grant.used = True
            return True, "ok"

    def revoke_all(self) -> None:
        with self._lock:
            self._grants.clear()

    def _prune(self) -> None:
        now = self._clock()
        for gid, g in list(self._grants.items()):
            if g.used or now > g.expires_at + 60:
                self._grants.pop(gid, None)
