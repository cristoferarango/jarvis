"""¿Esta frase pide el informe del día?

Se compara texto normalizado (sin tildes, signos ni mayúsculas) y sin
vocativos ni muletillas, para que "CRISVIS, dame mi informe, por favor" cuente
y "buenos días, ¿qué tiempo hace en Lima?" no.
"""

from __future__ import annotations

import re
import unicodedata

from crisvis.openclaw.contracts import Detail

_FILLER = {
    "crisvis",
    "jarvis",
    "chris",
    "cris",
    "señor",
    "senor",
    "hola",
    "oye",
    "por",
    "favor",
    "porfa",
    "ya",
    "y",
    "me",
    "el",
    "la",
    "de",
    "hoy",
    "ahora",
    "quiero",
    "puedes",
    "darme",
    "dame",
    "dime",
    "mi",
    "tu",
    "quisiera",
    "ver",
    "que",
    "tengo",
    "para",
    "buenos",
    "buenas",
    "dias",
    "tardes",
    "noches",
    "informe",
    "del",
    "dia",
    "resumen",
    "rapido",
    "corto",
    "breve",
    "completo",
    "detallado",
    "entero",
}
_QUICK = {"rapido", "corto", "breve"}
_COMPLETE = {"completo", "detallado", "entero"}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9ñ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def match_trigger(text: str, phrases: list[str]) -> Detail | None:
    """Detalle pedido si la frase es un disparador; si no, None."""
    if len(text) > 160:
        return None
    norm = normalise(text)
    padded = f" {norm} "
    if not any(f" {p} " in padded for p in phrases):
        return None
    words = norm.split()
    # Todo lo que no sea disparador ni muletilla es otra petición: no se secuestra.
    if any(w not in _FILLER for w in words):
        return None
    if set(words) & _QUICK:
        return "quick"
    if set(words) & _COMPLETE:
        return "complete"
    return "standard"
