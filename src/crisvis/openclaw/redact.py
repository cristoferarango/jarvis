"""Redacción de secretos antes de que algo llegue a la auditoría, a la cara o a un log."""

from __future__ import annotations

import re
from typing import Any

MASK = "***"
_MAX_TEXT = 600
_SECRET_WORDS = {
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "bearer",
    "key",
    "pin",
}
_SECRET_SUFFIXES = ("token", "secret", "password", "apikey", "key", "credentials")
# Formatos de credencial conocidos. Mejor tapar de más que filtrar una.
_SECRET_VALUES = [
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bbasic\s+[a-z0-9+/=]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(token|password|secret|api[_-]?key)\s*[=:]\s*[^\s,;\"']{4,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]


def is_secret_key(key: str) -> bool:
    low = key.lower()
    words = set(re.split(r"[\W_]+", low))
    return bool(words & _SECRET_WORDS) or low.replace("_", "").replace("-", "").endswith(
        _SECRET_SUFFIXES
    )


def redact_text(text: str, limit: int = _MAX_TEXT) -> str:
    for pattern in _SECRET_VALUES:
        text = pattern.sub(MASK, text)
    return text if len(text) <= limit else text[:limit] + "…"


def redact(value: Any, limit: int = _MAX_TEXT, _depth: int = 0) -> Any:
    """Copia de ``value`` sin secretos: por nombre de clave y por formato del valor."""
    if _depth > 8:
        return MASK
    if isinstance(value, dict):
        return {
            str(k): MASK if is_secret_key(str(k)) else redact(v, limit, _depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, limit, _depth + 1) for v in value[:100]]
    if isinstance(value, str):
        return redact_text(value, limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value), limit)


def safe_error(exc: BaseException) -> str:
    """Mensaje de error apto para la cara: sin rutas, trazas ni secretos."""
    kind = type(exc).__name__
    text = redact_text(str(exc), 160)
    text = re.sub(r"[A-Za-z]:\\[^\s]+|/(?:home|Users|root)/[^\s]+", "<ruta>", text)
    return f"{kind}: {text}" if text else kind
