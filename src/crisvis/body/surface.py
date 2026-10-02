"""La superficie: lo único que el cuerpo sabe de la cara.

Las herramientas de OpenJarvis se ejecutan en hilos de su ToolExecutor, y la
cara vive al otro lado de un WebSocket atendido por el bucle asyncio. Este
protocolo es el puente entre ambos mundos y mantiene a las herramientas sin
saber nada del transporte.
"""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class Surface(Protocol):
    def push(self, frame: dict[str, Any]) -> None:
        """Envía una trama a la cara. Seguro desde cualquier hilo; no espera."""

    def request(self, kind: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """Pide algo a la cara y espera la respuesta. Solo desde hilos de herramienta."""

    def run(self, coro: Coroutine[Any, Any, T], timeout: float) -> T:
        """Ejecuta una corrutina en el bucle principal y espera su resultado."""
