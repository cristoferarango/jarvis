"""Aprobaciones de un solo uso, ligadas a la huella de la acción exacta."""

from __future__ import annotations

import hmac
import secrets
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from crisvis.openclaw.contracts import ApprovalRequest, ApprovalState, ProposedAction


class ApprovalError(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApprovalManager:
    def __init__(self, seconds: int, clock: Callable[[], datetime] = _utcnow) -> None:
        self.seconds = max(10, seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, ApprovalRequest] = {}

    def _expire(self) -> None:
        now = self._clock()
        for item in self._items.values():
            if item.state in (ApprovalState.PENDING, ApprovalState.APPROVED) and (
                now >= item.expires_at
            ):
                item.state = ApprovalState.EXPIRED

    def create(self, action: ProposedAction, dry_run: bool) -> ApprovalRequest:
        now = self._clock()
        request = ApprovalRequest(
            id=f"ap-{secrets.token_urlsafe(9)}",
            action=action,
            fingerprint=action.fingerprint(),
            created_at=now,
            expires_at=now + timedelta(seconds=self.seconds),
            dry_run=dry_run,
        )
        with self._lock:
            self._items[request.id] = request
            # Sin memoria infinita: se olvidan las ya cerradas más antiguas.
            if len(self._items) > 200:
                closed = [k for k, v in self._items.items() if v.state != ApprovalState.PENDING]
                for key in closed[: len(self._items) - 200]:
                    self._items.pop(key, None)
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._lock:
            self._expire()
            return self._items.get(approval_id)

    def pending(self) -> list[ApprovalRequest]:
        with self._lock:
            self._expire()
            return [i for i in self._items.values() if i.state is ApprovalState.PENDING]

    def resolve(self, approval_id: str, approved: bool, fingerprint: str) -> ApprovalRequest:
        with self._lock:
            self._expire()
            item = self._items.get(approval_id)
            if item is None:
                raise ApprovalError("aprobación desconocida")
            if item.state is not ApprovalState.PENDING:
                raise ApprovalError(f"la aprobación ya está {item.state.value}")
            if not hmac.compare_digest(item.fingerprint, fingerprint or ""):
                raise ApprovalError("la aprobación no corresponde a esta acción exacta")
            item.state = ApprovalState.APPROVED if approved else ApprovalState.DENIED
            return item

    def consume(self, approval_id: str, action: ProposedAction) -> ApprovalRequest:
        """Gasta la aprobación. Solo vale para la acción con la misma huella, una vez."""
        with self._lock:
            self._expire()
            item = self._items.get(approval_id)
            if item is None:
                raise ApprovalError("aprobación desconocida")
            if item.state is not ApprovalState.APPROVED:
                raise ApprovalError(f"la aprobación está {item.state.value}")
            if not hmac.compare_digest(item.fingerprint, action.fingerprint()):
                raise ApprovalError("la acción cambió desde que se aprobó")
            item.state = ApprovalState.USED
            return item
