"""Evento «como»: cada entrada deja huella de su origen, nunca su contenido."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from crisvis.security.telemetry import InputTelemetry


def test_entries_are_traceable_without_their_text(tmp_path: Path) -> None:
    telemetry = InputTelemetry(tmp_path / "telemetria-entradas.jsonl")
    event = telemetry.record(text="como", session="sesion-123456789", source="voz",
                             authenticated=True, accepted=True, origin="http://127.0.0.1:8787")
    raw = (tmp_path / "telemetria-entradas.jsonl").read_text("utf-8")
    entry = json.loads(raw)
    assert entry["event_id"] == event
    assert entry["session_id"] == "sesion-1"
    assert entry["source_type"] == "voz"
    assert entry["length"] == 4
    assert entry["authenticated"] is True and entry["accepted"] is True
    assert "como" not in raw
    # Un hash sin clave de un texto corto se adivinaría con un diccionario.
    assert hashlib.sha256(b"como").hexdigest()[:16] not in raw
    # Con la clave local, el dueño sí puede comprobar un texto candidato.
    assert telemetry.fingerprint("como") == entry["hmac"]


def test_unknown_sources_and_rejections_are_recorded(tmp_path: Path) -> None:
    telemetry = InputTelemetry(tmp_path / "t.jsonl")
    telemetry.record(text="", session=None, source="script", authenticated=True,
                     accepted=False, reason="vacía")
    entry = json.loads((tmp_path / "t.jsonl").read_text("utf-8"))
    assert entry["source_type"] == "desconocido"
    assert entry["accepted"] is False and entry["reason"] == "vacía"


def test_the_key_survives_restarts(tmp_path: Path) -> None:
    first = InputTelemetry(tmp_path / "t.jsonl").fingerprint("hola")
    assert InputTelemetry(tmp_path / "t.jsonl").fingerprint("hola") == first
