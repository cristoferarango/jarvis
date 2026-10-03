"""Ejecución del flujo ``daily-briefing``.

registrar solicitud → consultar fuentes en paralelo (timeout por fuente y
global) → normalizar → clasificar alertas → resumen de voz → (la cara lo
muestra) → auditoría. Sin acciones externas: las fuentes solo leen y las
propuestas quedan como propuestas.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone, tzinfo

from crisvis.openclaw.audit import AdapterAudit
from crisvis.openclaw.contracts import (
    ALERT_ORDER,
    AlertLevel,
    BriefingAlert,
    ConnectorResult,
    DailyBriefingRequest,
    DailyBriefingResult,
    SourceState,
)
from crisvis.openclaw.providers.base import FetchContext, Provider, SourceError
from crisvis.openclaw.redact import redact_text, safe_error
from crisvis.openclaw.summary import voice_summary
from crisvis.openclaw.workflow import WorkflowSpec

log = logging.getLogger(__name__)
OnSource = Callable[[ConnectorResult], Awaitable[None]]

_STATE_ALERT = {
    SourceState.TIMEOUT: (AlertLevel.LOW, "no respondió a tiempo"),
    SourceState.UNAVAILABLE: (AlertLevel.LOW, "no disponible"),
    SourceState.ERROR: (AlertLevel.MEDIUM, "falló al consultarse"),
    SourceState.UNAUTHORIZED: (AlertLevel.MEDIUM, "sin autorización: revisa la conexión"),
    SourceState.PARTIAL: (AlertLevel.LOW, "respondió de forma incompleta"),
}


def zone(name: str) -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - sin tzdata en Windows: Lima no tiene horario de verano
        return (
            timezone(timedelta(hours=-5), "America/Lima")
            if name == "America/Lima"
            else (timezone.utc)
        )


class BriefingEngine:
    def __init__(self, spec: WorkflowSpec, providers: dict[str, Provider], audit: AdapterAudit):
        self.spec = spec
        self.providers = providers
        self.audit = audit

    def selected(self, request: DailyBriefingRequest) -> list[Provider]:
        order = self.spec.sources()
        wanted = request.sources or order
        return [self.providers[s] for s in order if s in wanted and s in self.providers]

    def context(self, request: DailyBriefingRequest) -> FetchContext:
        tz = zone(request.timezone)
        now = datetime.now(tz)
        if request.date:
            day = datetime.strptime(request.date, "%Y-%m-%d").date()
            now = now.replace(year=day.year, month=day.month, day=day.day)
        return FetchContext(request=request, now=now)

    def normalise(self, result: ConnectorResult, request: DailyBriefingRequest) -> ConnectorResult:
        cap = self.spec.elementos_por_detalle.get(request.detail, 8)
        items = sorted(
            result.items,
            key=lambda i: (i.when is None, i.when or datetime.min.replace(tzinfo=timezone.utc)),
        )
        for item in items:
            item.title = redact_text(item.title, 200)
            item.subtitle = redact_text(item.subtitle, 200)
        result.items = items[:cap]
        result.message = redact_text(result.message, 200)
        return result

    def classify(self, result: ConnectorResult) -> list[BriefingAlert]:
        alerts: list[BriefingAlert] = []
        if result.state in _STATE_ALERT and result.state is not SourceState.SUCCESS:
            level, text = _STATE_ALERT[result.state]
            alerts.append(
                BriefingAlert(
                    id=f"{result.source}:estado",
                    level=level,
                    source=result.source,
                    title=f"{result.name}: {text}",
                    detail=result.message,
                )
            )
        seen: set[str] = set()
        for rule in self.spec.alertas:
            if rule.fuente != result.source:
                continue
            for item in result.items:
                if rule.matches(item):
                    alert = rule.alert(result.source, item)
                    if alert.id not in seen:
                        seen.add(alert.id)
                        alerts.append(alert)
        return alerts

    async def fetch_one(
        self, provider: Provider, ctx: FetchContext, session: str
    ) -> ConnectorResult:
        request = ctx.request
        start = time.monotonic()

        def took() -> int:
            return int((time.monotonic() - start) * 1000)

        try:
            fetched = await asyncio.wait_for(provider.fetch(ctx), request.source_timeout_s)
            result = provider.result(fetched, took())
        except asyncio.TimeoutError:
            result = provider.failure(
                SourceState.TIMEOUT,
                f"No respondió en {request.source_timeout_s:g} s.",
                took(),
            )
        except SourceError as exc:
            result = provider.failure(exc.state, exc.message, took())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Fuente %s falló: %s", provider.id, safe_error(exc))
            result = provider.failure(
                SourceState.ERROR, "Error interno al consultar la fuente.", took()
            )
        result = self.normalise(result, request)
        result.alerts = self.classify(result)
        self.audit.record(
            "briefing.source",
            session=session,
            source=provider.id,
            tool=provider.tool,
            decision="read_only",
            outcome=result.state.value,
            dry_run=request.dry_run,
            details={"ms": result.duration_ms, "items": len(result.items), "mode": result.mode},
        )
        return result

    async def run(
        self,
        request: DailyBriefingRequest,
        *,
        session: str = "",
        user: str = "",
        on_source: OnSource | None = None,
    ) -> DailyBriefingResult:
        briefing = DailyBriefingResult(
            id=f"inf-{secrets.token_urlsafe(6)}",
            request=request,
            started_at=datetime.now(timezone.utc),
            dry_run=request.dry_run,
        )
        providers = self.selected(request)
        self.audit.record(
            "briefing.request",
            session=session,
            decision="read_only",
            outcome="iniciado",
            dry_run=request.dry_run,
            details={
                "id": briefing.id,
                "trigger": request.trigger,
                "detail": request.detail,
                "sources": [p.id for p in providers],
            },
        )
        ctx = self.context(request)
        results: dict[str, ConnectorResult] = {}

        async def one(provider: Provider) -> None:
            result = await self.fetch_one(provider, ctx, session)
            results[provider.id] = result
            if on_source is not None:
                await on_source(result)

        tasks = {asyncio.create_task(one(p)): p for p in providers}
        try:
            _done, pending = await asyncio.wait(tasks, timeout=request.timeout_s)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.audit.record(
                "briefing.done",
                session=session,
                outcome="cancelado",
                dry_run=request.dry_run,
                details={"id": briefing.id, "completed": len(results)},
            )
            raise
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for provider in tasks.values():
            if provider.id not in results:
                late = provider.failure(
                    SourceState.TIMEOUT,
                    f"Superó el tiempo total del informe ({request.timeout_s:g} s).",
                    int(request.timeout_s * 1000),
                )
                late.alerts = self.classify(late)
                results[provider.id] = late
                if on_source is not None:
                    await on_source(late)

        ordered = [results[p.id] for p in providers]
        briefing.results = ordered
        briefing.alerts = sorted(
            (a for r in ordered for a in r.alerts), key=lambda a: ALERT_ORDER[a.level]
        )
        briefing.voice_summary = voice_summary(ordered, ctx.now, user)
        briefing.finished_at = datetime.now(timezone.utc)
        self.audit.record(
            "briefing.done",
            session=session,
            decision="read_only",
            outcome="completado",
            dry_run=request.dry_run,
            details={
                "id": briefing.id,
                "states": {r.source: r.state.value for r in ordered},
                "alerts": {
                    lvl.value: sum(1 for a in briefing.alerts if a.level is lvl)
                    for lvl in AlertLevel
                },
                "external_actions": 0,
            },
        )
        return briefing
