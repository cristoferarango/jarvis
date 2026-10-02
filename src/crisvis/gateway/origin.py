"""Quién puede hablar con el núcleo.

El WebSocket da acceso a un agente con herramientas en esta máquina, así que
no basta con escuchar en 127.0.0.1: cualquier página abierta en el navegador
puede intentar conectarse a localhost. Se acepta la propia aplicación, los
puertos de desarrollo de Vite y lo que el usuario añada en la configuración.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from crisvis.settings import ServerSettings

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
_DEV_PORTS = set(range(5173, 5200)) | set(range(4173, 4200))


def origin_allowed(origin: str | None, server: ServerSettings) -> bool:
    if not origin:
        return server.permitir_sin_origen
    if origin.rstrip("/") in {o.rstrip("/") for o in server.origenes_permitidos}:
        return True
    try:
        parts = urlsplit(origin)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or (parts.hostname or "") not in _LOCAL_HOSTS:
        return False
    return port == server.port or port in _DEV_PORTS
