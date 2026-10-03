"""Auditoría local del adaptador: un AuditEvent por línea, siempre redactado."""

from __future__ import annotations

import json
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from crisvis.openclaw.contracts import AuditEvent, Risk
from crisvis.openclaw.redact import redact


class AdapterAudit:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record(
        self,
        kind: str,
        *,
        session: str = "",
        source: str = "",
        tool: str = "",
        risk: Risk | None = None,
        decision: str = "",
        outcome: str = "",
        dry_run: bool = True,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            ts=datetime.now(timezone.utc),
            kind=kind,
            session=session[:8],
            source=source,
            tool=tool,
            risk=risk,
            decision=decision,
            outcome=redact(outcome, 300),
            dry_run=dry_run,
            details=redact(details or {}),
        )
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return event

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        tail: deque[str] = deque(maxlen=max(1, min(limit, 500)))
        with self._lock, self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    tail.append(line)
        out: list[dict[str, Any]] = []
        for line in reversed(tail):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
