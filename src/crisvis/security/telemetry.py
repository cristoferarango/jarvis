"""Telemetría mínima de cada entrada de usuario (sin su contenido).

Nació del análisis del evento «como» (docs/INCIDENT_COMO_ANALYSIS.md): no
había forma de saber de dónde vino una entrada. Cada ``ask`` deja aquí su
huella: id, sesión, tipo de fuente declarado por la interfaz, huella y longitud
del texto, si el canal estaba autenticado y si se aceptó. El texto nunca.

La huella es un HMAC con una clave local (``telemetria.key`` junto al registro):
un hash sin clave de "como" se adivina con un diccionario. Con la clave, el
dueño del equipo puede comprobar si una entrada fue un texto concreto
(``fingerprint``); con solo el registro, no.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from crisvis.body.base import new_id

SOURCES = ("voz", "voz_activacion", "teclado", "boton", "desconocido")


def _load_key(path: Path) -> bytes:
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return key


class InputTelemetry:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._key = _load_key(path.with_name("telemetria.key"))

    def fingerprint(self, text: str) -> str:
        return hmac.new(self._key, text.encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def record(
        self,
        *,
        text: str,
        session: str | None,
        source: str,
        authenticated: bool,
        accepted: bool,
        channel: str = "ws",
        origin: str | None = None,
        reason: str = "",
    ) -> str:
        event_id = new_id("in")
        entry: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "event_id": event_id,
            "session_id": (session or "")[:8],
            "source_type": source if source in SOURCES else "desconocido",
            "channel": channel,
            "origin": origin or "",
            "hmac": self.fingerprint(text),
            "length": len(text),
            "authenticated": authenticated,
            "accepted": accepted,
        }
        if reason:
            entry["reason"] = reason
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return event_id
