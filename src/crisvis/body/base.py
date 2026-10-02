"""Base para las herramientas del cuerpo, sobre el BaseTool de OpenJarvis."""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from typing import Any

from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_seq = itertools.count()


def new_id(prefix: str) -> str:
    """Ids únicos: llegan en ráfagas y la hora sola colisiona."""
    return f"{prefix}{int(time.time() * 1000):x}-{next(_seq):x}"


class FaceTool(BaseTool):
    """Una herramienta local de la cara. Corre en este proceso, dibuja en nuestra pantalla."""

    is_local = True

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[[dict[str, Any]], ToolResult],
        *,
        category: str = "interfaz",
        timeout: float = 15.0,
    ) -> None:
        self.tool_id = name
        self._spec = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            category=category,
            timeout_seconds=timeout,
        )
        self._handler = handler

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, **params: Any) -> ToolResult:
        return self._handler(params)


def ok(name: str, text: str) -> ToolResult:
    return ToolResult(tool_name=name, content=text, success=True)


def refuse(name: str, text: str) -> ToolResult:
    return ToolResult(tool_name=name, content=text, success=False)


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


# -- normalización tolerante ---------------------------------------------------
# Los modelos escriben números y booleanos como les parece: '0.5' entre comillas,
# 1 por true, null por "déjalo". Un turno que falla porque una escala venía
# entrecomillada es un turno que el usuario vio romperse, así que nada de esto
# rechaza: lo que no se entiende se ignora y lo que se entiende se acota.

_FALSEY = {"false", "0", "no", "off", "hide", "hidden", "none", "ocultar", "oculto"}
_AUTOMATIC = {"auto", "default", "none", "null", "reset", "clear", "stock", "automatico"}


def clamp(value: Any, lo: float, hi: float) -> float | None:
    if value is None:
        return None
    try:
        n = float(str(value).strip()) if not isinstance(value, (int, float)) else float(value)
    except ValueError:
        return None
    if n != n:  # NaN
        return None
    return min(hi, max(lo, n))


def to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    s = str(value).strip().lower()
    if not s:
        return None
    return s not in _FALSEY


def to_colour(value: Any, *, present: bool) -> str | None | object:
    """None = seguir la fase; MISSING = no mencionado."""
    if not present:
        return MISSING
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in _AUTOMATIC:
        return None
    return s


MISSING = object()
