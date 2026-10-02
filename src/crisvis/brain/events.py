"""Lo que el cerebro le cuenta a la interfaz mientras piensa.

Son tipos propios y pequeños a propósito: el gateway no importa nada de
OpenJarvis, solo esto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolStarted:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolFinished:
    id: str
    name: str
    ok: bool


@dataclass(frozen=True)
class Finished:
    text: str
    turns: int
    tools: tuple[str, ...] = ()
    truncated: bool = False


BrainEvent = Union[TextDelta, ToolStarted, ToolFinished, Finished]
