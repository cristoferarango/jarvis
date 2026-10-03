"""Carga y validación del flujo declarativo ``daily-briefing``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from crisvis.openclaw.contracts import TABS, AlertLevel, BriefingAlert, BriefingItem, Strict
from crisvis.settings import tomllib

WORKFLOWS = Path(__file__).with_name("workflows")
STEPS = (
    "registrar_solicitud",
    "consultar_fuentes",
    "normalizar",
    "clasificar_alertas",
    "resumen_de_voz",
    "mostrar_panel",
    "guardar_auditoria",
)
_MISSING = object()


class Params(Strict):
    zona_horaria: str = "America/Lima"
    detalle: Literal["quick", "standard", "complete"] = "standard"
    timeout_global: float = Field(default=20.0, gt=0, le=120)
    timeout_fuente: float = Field(default=6.0, gt=0, le=60)
    dry_run: Literal[True] = True


class AlertRule(Strict):
    fuente: str
    campo: str
    igual: str | int | float | bool | None = None
    entre: tuple[float, float] | None = None
    y_campo: str | None = None
    y_igual: str | int | float | bool | None = None
    nivel: AlertLevel
    titulo: str

    @model_validator(mode="after")
    def _one_condition(self) -> AlertRule:
        if (self.igual is None) == (self.entre is None):
            raise ValueError("cada regla necesita 'igual' o 'entre' (solo uno)")
        return self

    def matches(self, item: BriefingItem) -> bool:
        value = item.meta.get(self.campo, _MISSING)
        if value is _MISSING:
            return False
        if self.entre is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            ok = self.entre[0] <= value <= self.entre[1]
        else:
            ok = value == self.igual and type(value) is type(self.igual)
        if ok and self.y_campo is not None:
            ok = item.meta.get(self.y_campo, _MISSING) == self.y_igual
        return ok

    def alert(self, source: str, item: BriefingItem) -> BriefingAlert:
        title = self.titulo.replace("{title}", item.title).replace("{subtitle}", item.subtitle)
        return BriefingAlert(
            id=f"{source}:{item.id}:{self.nivel.value}",
            level=self.nivel,
            source=source,
            title=title,
            detail=item.subtitle,
        )


class WorkflowSpec(Strict):
    id: str
    version: int
    descripcion: str
    acciones_externas: Literal[False]
    disparadores: list[str] = Field(min_length=1)
    pasos: list[str]
    parametros: Params
    elementos_por_detalle: dict[Literal["quick", "standard", "complete"], int]
    pestanas: dict[str, list[str]]
    alertas: list[AlertRule] = Field(default_factory=list)

    @field_validator("pasos")
    @classmethod
    def _steps(cls, value: list[str]) -> list[str]:
        if tuple(value) != STEPS:
            raise ValueError(f"los pasos deben ser exactamente {STEPS}")
        return value

    @field_validator("pestanas")
    @classmethod
    def _tabs(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if tuple(value) != TABS:
            raise ValueError(f"las pestañas deben ser exactamente {TABS}")
        return value

    def sources(self) -> list[str]:
        return [s for tab in self.pestanas.values() for s in tab]


def load_workflow(name: str = "daily-briefing", data: dict[str, Any] | None = None) -> WorkflowSpec:
    if data is None:
        with (WORKFLOWS / f"{name}.toml").open("rb") as fh:
            data = tomllib.load(fh)
    return WorkflowSpec.model_validate(data)
