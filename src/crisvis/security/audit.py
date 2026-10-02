"""Registro de auditoría de cada decisión sobre herramientas (JSON por línea)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_MAX_ARG_CHARS = 400


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in args.items():
        if any(s in key.lower() for s in ("token", "password", "secret", "key", "auth")):
            out[key] = "***"
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        out[key] = text if len(text) <= _MAX_ARG_CHARS else text[:_MAX_ARG_CHARS] + "…"
    return out


class AuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def record(
        self,
        *,
        session: str,
        tool: str,
        tier: str,
        decision: str,
        outcome: str,
        args: dict[str, Any] | None = None,
    ) -> None:
        entry = {
            "ts": round(time.time(), 3),
            "sesion": session,
            "herramienta": tool,
            "nivel": tier,
            "decision": decision,
            "resultado": outcome,
            "args": _redact(args or {}),
        }
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
