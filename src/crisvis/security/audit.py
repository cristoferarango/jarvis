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
        self._write(entry)

    def event(self, kind: str, **fields: Any) -> None:
        """Ciclo de vida de una ejecución o de un cambio de seguridad.

        ``kind``: propuesta, aprobada, denegada, bloqueada, iniciada, completada,
        fallida, timed_out, cancelacion_solicitada, cancelled, cancel_failed,
        modo_*, libre_*, trama_invalida… ``exec`` correlaciona los de una misma
        ejecución. Nunca lleva resultados de herramientas ni texto del usuario.
        """
        entry: dict[str, Any] = {"ts": round(time.time(), 3), "evento": kind}
        for key, value in fields.items():
            if key == "args" and isinstance(value, dict):
                entry[key] = _redact(value)
            elif isinstance(value, (str, int, float, bool)) or value is None:
                entry[key] = value
            else:
                entry[key] = json.loads(json.dumps(value, ensure_ascii=False, default=str))
        self._write(entry)

    def _write(self, entry: dict[str, Any]) -> None:
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
