"""Una sesión mínima para probar ``Session.gate`` sin socket ni cerebro."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crisvis.execution import ExecutionTracker
from crisvis.gateway.session import Session
from crisvis.security import PermissionPolicy
from crisvis.security.audit import AuditLog
from crisvis.security.grants import GrantBook
from crisvis.security.guard import SequenceGuard
from crisvis.settings import PermissionSettings

Reply = Callable[[dict[str, Any]], dict[str, Any]]


def approve(payload: dict[str, Any]) -> dict[str, Any]:
    """Lo que manda la interfaz cuando el usuario pulsa Permitir."""
    return {"approved": True, "grant": payload["grant"], "nonce": payload["nonce"]}


class GateSession(Session):
    def __init__(
        self,
        tmp: Path,
        *,
        mode: str = "confirmar",
        reply: Reply = approve,
        window: str = "Sin título: Bloc de notas",
    ) -> None:
        self.id = "sesion-de-prueba-0123456789"
        self.audit_path = tmp / "auditoria.jsonl"
        audit = AuditLog(self.audit_path)
        self.registry = SimpleNamespace(
            policy=PermissionPolicy(PermissionSettings(modo=mode)),
            audit=audit,
            settings=SimpleNamespace(permisos=SimpleNamespace(segundos_confirmacion=5)),
            modes=None,
        )
        self._answering = None
        self.tracker = ExecutionTracker(audit, self.id)
        self.tracker.new_turn()
        self.grants = GrantBook()
        self.sequence = SequenceGuard()
        self.window = window
        self.window_label = lambda: self.window
        self._apps = ()
        self.reply = reply
        self.asked: list[dict[str, Any]] = []

    async def ask_face(self, kind: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        assert kind == "confirm"
        self.asked.append(payload)
        return self.reply(payload)

    def events(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text("utf-8").splitlines()]


SPEC = SimpleNamespace(requires_confirmation=False, timeout_seconds=30)
