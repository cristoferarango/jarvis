"""Contratos tipados del OpenClawAdapter.

Todo lo que cruza el adaptador (de la cara, de las fuentes o de OpenClaw) se
valida contra estos modelos. Las entradas rechazan claves desconocidas; las
salidas se serializan tal cual a la cara.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class Risk(str, Enum):
    READ_ONLY = "READ_ONLY"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


ALERT_ORDER = {level: i for i, level in enumerate(AlertLevel)}


class SourceState(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    UNAUTHORIZED = "unauthorized"
    UNAVAILABLE = "unavailable"
    ERROR = "error"
    CANCELLED = "cancelled"


class ConnectorMode(str, Enum):
    NOT_CONFIGURED = "no_configurado"
    MOCK = "mock"
    SANDBOX = "sandbox"
    REAL = "real"


Access = Literal["read_only", "draft", "write", "delete"]
Detail = Literal["quick", "standard", "complete"]
Tab = Literal[
    "resumen",
    "correo",
    "agenda",
    "tareas",
    "proyectos",
    "negocio",
    "documentos",
    "sistema",
    "automatizaciones",
    "integraciones",
]
TABS: tuple[str, ...] = Tab.__args__  # type: ignore[attr-defined]


class ToolDefinition(Strict):
    """Una operaciÃ³n que el adaptador sabe hacer. Fuera de este registro no hay nada."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")
    connector: str
    risk: Risk
    description: str
    access: Access = "read_only"
    params: dict[str, str] = Field(default_factory=dict)  # nombre -> tipo


class ConnectorStatus(Strict):
    id: str
    name: str
    tab: Tab
    mode: ConnectorMode
    access: Access = "read_only"
    integration: str  # oficial | plugin | skill | mcp | custom | mock
    state: SourceState | None = None
    message: str = ""
    checked_at: datetime | None = None
    approval: str = "no"  # quÃ© aprobaciÃ³n exige para escribir


class BriefingItem(Strict):
    id: str
    title: str
    subtitle: str = ""
    when: datetime | None = None
    meta: dict[str, str | int | float | bool] = Field(default_factory=dict)
    url: str = ""


class BriefingAlert(Strict):
    id: str
    level: AlertLevel
    source: str
    title: str
    detail: str = ""


class ProposedAction(Strict):
    """Algo que CRISVIS podrÃ­a hacer. Nunca se ejecuta sin pasar por la polÃ­tica."""

    id: str
    tool: str
    source: str
    risk: Risk
    service: str
    account: str = ""
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    preview: str = ""
    impact: str = ""

    def fingerprint(self) -> str:
        """Huella de la acciÃ³n exacta: cualquier cambio en destino o parÃ¡metros la invalida."""
        body = json.dumps(
            {
                "tool": self.tool,
                "service": self.service,
                "account": self.account,
                "target": self.target,
                "params": self.params,
                "preview": self.preview,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ConnectorResult(Strict):
    source: str
    name: str
    tab: Tab
    mode: ConnectorMode
    state: SourceState
    fetched_at: datetime
    duration_ms: int = 0
    items: list[BriefingItem] = Field(default_factory=list)
    alerts: list[BriefingAlert] = Field(default_factory=list)
    proposals: list[ProposedAction] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    message: str = ""


class DailyBriefingRequest(Strict):
    user: str = ""
    timezone: str = "America/Lima"
    date: str = ""  # YYYY-MM-DD; vacÃ­o = hoy en la zona horaria
    detail: Detail = "standard"
    sources: list[str] = Field(default_factory=list)
    timeout_s: float = Field(default=20.0, gt=0, le=120)
    source_timeout_s: float = Field(default=6.0, gt=0, le=60)
    dry_run: bool = True
    trigger: str = "manual"

    @field_validator("date")
    @classmethod
    def _iso_date(cls, value: str) -> str:
        if value:
            datetime.strptime(value, "%Y-%m-%d")
        return value

    @field_validator("sources")
    @classmethod
    def _source_ids(cls, value: list[str]) -> list[str]:
        for source in value:
            if not source.replace("_", "").isalnum():
                raise ValueError(f"fuente no vÃ¡lida: {source!r}")
        return value


class DailyBriefingResult(Strict):
    id: str
    request: DailyBriefingRequest
    started_at: datetime
    finished_at: datetime | None = None
    results: list[ConnectorResult] = Field(default_factory=list)
    alerts: list[BriefingAlert] = Field(default_factory=list)
    voice_summary: str = ""
    dry_run: bool = True
    cancelled: bool = False


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    USED = "used"


class ApprovalRequest(Strict):
    id: str
    action: ProposedAction
    fingerprint: str
    created_at: datetime
    expires_at: datetime
    state: ApprovalState = ApprovalState.PENDING
    dry_run: bool = True


class AuditEvent(Strict):
    ts: datetime
    kind: str  # briefing.request | briefing.source | briefing.done | policy | approval | action
    session: str = ""
    source: str = ""
    tool: str = ""
    risk: Risk | None = None
    decision: str = ""
    outcome: str = ""
    dry_run: bool = True
    details: dict[str, Any] = Field(default_factory=dict)
