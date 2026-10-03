"""Lo común a todas las fuentes del informe."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from crisvis.openclaw.contracts import (
    Access,
    BriefingItem,
    ConnectorMode,
    ConnectorResult,
    ConnectorStatus,
    DailyBriefingRequest,
    ProposedAction,
    SourceState,
    Tab,
)


class SourceError(Exception):
    """Fallo esperado de una fuente, con un mensaje apto para la cara."""

    def __init__(self, state: SourceState, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.message = message


@dataclass
class FetchContext:
    request: DailyBriefingRequest
    now: datetime  # con zona horaria del usuario


@dataclass
class Fetched:
    items: list[BriefingItem] = field(default_factory=list)
    proposals: list[ProposedAction] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    state: SourceState = SourceState.SUCCESS
    message: str = ""


class Provider:
    id: str = ""
    name: str = ""
    tab: Tab = "resumen"
    mode: ConnectorMode = ConnectorMode.NOT_CONFIGURED
    access: Access = "read_only"
    integration: str = "custom"
    tool: str = ""
    approval: str = "no (solo lectura)"

    async def fetch(self, ctx: FetchContext) -> Fetched:  # pragma: no cover - interfaz
        raise NotImplementedError

    def status(self, last: ConnectorResult | None = None) -> ConnectorStatus:
        return ConnectorStatus(
            id=self.id,
            name=self.name,
            tab=self.tab,
            mode=self.mode,
            access=self.access,
            integration=self.integration,
            state=last.state if last else None,
            message=last.message if last else self.hint(),
            checked_at=last.fetched_at if last else None,
            approval=self.approval,
        )

    def hint(self) -> str:
        return ""

    def result(self, fetched: Fetched, duration_ms: int) -> ConnectorResult:
        return ConnectorResult(
            source=self.id,
            name=self.name,
            tab=self.tab,
            mode=self.mode,
            state=fetched.state,
            fetched_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            items=fetched.items,
            proposals=fetched.proposals,
            counts=fetched.counts,
            message=fetched.message,
        )

    def failure(self, state: SourceState, message: str, duration_ms: int) -> ConnectorResult:
        return self.result(Fetched(state=state, message=message), duration_ms)


class NotConfiguredProvider(Provider):
    """Una fuente real a la que todavía no se le ha conectado una cuenta."""

    def __init__(self, base: Provider, why: str) -> None:
        self.id, self.name, self.tab = base.id, base.name, base.tab
        self.integration, self.tool, self.approval = base.integration, base.tool, base.approval
        self.mode = ConnectorMode.NOT_CONFIGURED
        self._why = why

    def hint(self) -> str:
        return self._why

    async def fetch(self, ctx: FetchContext) -> Fetched:
        raise SourceError(SourceState.UNAVAILABLE, self._why)


def meta(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}
