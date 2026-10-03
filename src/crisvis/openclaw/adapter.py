"""OpenClawAdapter: el único camino entre la cara de CRISVIS y OpenClaw.

La cara habla con el núcleo (WebSocket/REST); el núcleo habla con este
adaptador; y solo el adaptador habla con el Gateway de OpenClaw (en loopback)
o con las fuentes. Aquí se validan los contratos, se aplica la política, se
exigen aprobaciones exactas y se audita todo.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from crisvis.openclaw.approvals import ApprovalError, ApprovalManager
from crisvis.openclaw.audit import AdapterAudit
from crisvis.openclaw.briefing import BriefingEngine, OnSource, zone
from crisvis.openclaw.contracts import (
    ApprovalRequest,
    ConnectorMode,
    ConnectorResult,
    ConnectorStatus,
    DailyBriefingRequest,
    DailyBriefingResult,
    Detail,
    ProposedAction,
    Risk,
)
from crisvis.openclaw.gateway import GatewayClient, GatewayError
from crisvis.openclaw.policy import ActionPolicy, PolicyDecision, Verdict
from crisvis.openclaw.providers.base import Provider
from crisvis.openclaw.providers.local import DockerProvider, LocalSystemProvider
from crisvis.openclaw.providers.mock import mock_providers
from crisvis.openclaw.ratelimit import RateLimiter
from crisvis.openclaw.redact import safe_error
from crisvis.openclaw.triggers import match_trigger
from crisvis.openclaw.workflow import WorkflowSpec, load_workflow
from crisvis.settings import Settings

log = logging.getLogger(__name__)


class AdapterError(Exception):
    """Rechazo con un mensaje apto para la cara."""


def default_providers(
    settings: Settings, brain_status: Callable[[], dict[str, Any]]
) -> dict[str, Provider]:
    """Fuentes del informe: estado local real + el resto de prueba hasta conectar cuentas."""
    providers = mock_providers(settings.openclaw.simular_fallos)
    providers["sistema"] = LocalSystemProvider(brain_status)
    providers["docker"] = DockerProvider()
    return providers


class OpenClawAdapter:
    def __init__(
        self,
        settings: Settings,
        providers: dict[str, Provider] | None = None,
        *,
        brain_status: Callable[[], dict[str, Any]] | None = None,
        gateway: GatewayClient | None = None,
        spec: WorkflowSpec | None = None,
    ) -> None:
        self.settings = settings
        self.cfg = settings.openclaw
        self.spec = spec or load_workflow()
        self.providers = providers or default_providers(settings, brain_status or (lambda: {}))
        self.audit = AdapterAudit(settings.adapter_audit_path)
        self.policy = ActionPolicy(self.cfg)
        self.approvals = ApprovalManager(self.cfg.segundos_aprobacion)
        self.limiter = RateLimiter(self.cfg.max_informes_por_minuto, 60.0)
        self.engine = BriefingEngine(self.spec, self.providers, self.audit)
        self.last: DailyBriefingResult | None = None
        self.last_by_source: dict[str, ConnectorResult] = {}
        self.gateway: GatewayClient | None = gateway
        self.gateway_error = ""
        if gateway is None:
            try:
                self.gateway = GatewayClient(self.cfg.gateway_url, self.cfg.cli)
            except GatewayError as exc:
                self.gateway_error = str(exc)

    # -- informe del día -----------------------------------------------------

    @property
    def dry_run(self) -> bool:
        return self.cfg.dry_run

    def trigger(self, text: str) -> Detail | None:
        if not self.cfg.habilitado:
            return None
        return match_trigger(text, self.spec.disparadores)

    def build_request(
        self,
        trigger: str,
        detail: str | None = None,
        sources: list[str] | None = None,
    ) -> DailyBriefingRequest:
        params = self.spec.parametros
        tz = self.cfg.zona_horaria or params.zona_horaria
        return DailyBriefingRequest(
            user=self.user_name(),
            timezone=tz,
            date=datetime.now(zone(tz)).strftime("%Y-%m-%d"),
            detail=detail or self.cfg.detalle or params.detalle,
            sources=[s for s in (sources or self.cfg.fuentes) if s in self.providers],
            timeout_s=self.cfg.timeout_global or params.timeout_global,
            source_timeout_s=self.cfg.timeout_fuente or params.timeout_fuente,
            dry_run=True if self.cfg.dry_run else params.dry_run,
            trigger=trigger[:80],
        )

    def user_name(self) -> str:
        return self.cfg.usuario or self.settings.asistente.tratamiento

    async def daily_briefing(
        self,
        request: DailyBriefingRequest,
        *,
        session: str = "",
        on_source: OnSource | None = None,
    ) -> DailyBriefingResult:
        if not self.limiter.allow(f"briefing:{session}"):
            self.audit.record("briefing.request", session=session, outcome="limitado")
            raise AdapterError("Demasiados informes seguidos; espera un minuto.")

        async def remember(result: ConnectorResult) -> None:
            self.last_by_source[result.source] = result
            if on_source is not None:
                await on_source(result)

        result = await self.engine.run(
            request, session=session, user=request.user, on_source=remember
        )
        self.last = result
        return result

    async def refresh_source(self, source: str, *, session: str = "") -> ConnectorResult:
        provider = self.providers.get(source)
        if provider is None:
            raise AdapterError(f"Fuente desconocida: {source}")
        if not self.limiter.allow(f"refresh:{session}:{source}"):
            raise AdapterError("Espera un momento antes de volver a actualizar.")
        request = self.build_request("refresh", sources=[source])
        result = await self.engine.fetch_one(provider, self.engine.context(request), session)
        self.last_by_source[source] = result
        if self.last is not None:
            self.last.results = [result if r.source == source else r for r in self.last.results]
        return result

    # -- estado --------------------------------------------------------------

    def connector_statuses(self) -> list[ConnectorStatus]:
        order = self.spec.sources()
        return [
            self.providers[s].status(self.last_by_source.get(s))
            for s in order
            if s in self.providers
        ]

    async def gateway_status(self, fresh: bool = False) -> dict[str, Any]:
        if self.gateway is None:
            return {"state": "bloqueado", "message": self.gateway_error, "installed": False}
        try:
            return await self.gateway.status(fresh=fresh)
        except Exception as exc:  # noqa: BLE001
            return {"state": "error", "message": safe_error(exc)}

    async def overview(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.habilitado,
            "dryRun": self.dry_run,
            "policy": {
                "READ_ONLY": "automática si el conector está conectado",
                "LOW": "permitida" if self.cfg.permitir_bajo else "deshabilitada",
                "MEDIUM": "vista previa + aprobación",
                "HIGH": "aprobación exacta por acción",
                "CRITICAL": "aprobación exacta" if self.cfg.critico_habilitado else "bloqueada",
            },
            "gateway": await self.gateway_status(),
            "connectors": [c.model_dump(mode="json") for c in self.connector_statuses()],
            "pending": [a.model_dump(mode="json") for a in self.approvals.pending()],
            "workflow": {"id": self.spec.id, "version": self.spec.version},
        }

    # -- acciones ------------------------------------------------------------

    def find_proposal(self, proposal_id: str) -> ProposedAction | None:
        for result in self.last_by_source.values():
            for action in result.proposals:
                if action.id == proposal_id:
                    return action
        return None

    def mode_of(self, action: ProposedAction) -> ConnectorMode:
        provider = self.providers.get(action.source)
        return provider.mode if provider else ConnectorMode.NOT_CONFIGURED

    def evaluate(self, action: ProposedAction, *, session: str = "") -> PolicyDecision:
        decision = self.policy.evaluate(action, self.mode_of(action))
        self.audit.record(
            "policy",
            session=session,
            source=action.source,
            tool=action.tool,
            risk=decision.risk,
            decision=decision.verdict.value,
            outcome=decision.reason,
            dry_run=decision.simulate,
            details={"target": action.target, "params": action.params},
        )
        return decision

    def request_approval(self, action: ProposedAction, *, session: str = "") -> ApprovalRequest:
        request = self.approvals.create(action, dry_run=self.dry_run)
        self.audit.record(
            "approval",
            session=session,
            source=action.source,
            tool=action.tool,
            risk=action.risk,
            decision="pendiente",
            dry_run=self.dry_run,
            details={"approval": request.id, "fingerprint": request.fingerprint[:16]},
        )
        return request

    def resolve_approval(
        self, approval_id: str, approved: bool, fingerprint: str, *, session: str = ""
    ) -> ApprovalRequest:
        try:
            item = self.approvals.resolve(approval_id, approved, fingerprint)
        except ApprovalError as exc:
            self.audit.record(
                "approval",
                session=session,
                decision="rechazada",
                outcome=str(exc),
                details={"approval": approval_id},
            )
            raise AdapterError(str(exc)) from exc
        self.audit.record(
            "approval",
            session=session,
            source=item.action.source,
            tool=item.action.tool,
            risk=item.action.risk,
            decision="aprobada" if approved else "denegada",
            dry_run=item.dry_run,
            details={"approval": approval_id},
        )
        return item

    def execute(
        self, action: ProposedAction, approval_id: str | None = None, *, session: str = ""
    ) -> dict[str, Any]:
        """Ejecuta (o simula) una acción ya decidida. Nunca sin política ni aprobación."""
        decision = self.evaluate(action, session=session)
        outcome: dict[str, Any] = {
            "action": action.id,
            "tool": action.tool,
            "risk": decision.risk.value,
            "executed": False,
            "simulated": False,
        }
        if decision.verdict is Verdict.BLOCK:
            outcome.update(status="blocked", message=f"Bloqueada: {decision.reason}.")
            self._record_action(action, decision, outcome, session)
            return outcome
        if decision.verdict is Verdict.APPROVE:
            if not approval_id:
                outcome.update(status="needs_approval", message="Requiere aprobación exacta.")
                self._record_action(action, decision, outcome, session)
                return outcome
            try:
                self.approvals.consume(approval_id, action)
            except ApprovalError as exc:
                outcome.update(status="blocked", message=f"Bloqueada: {exc}.")
                self._record_action(action, decision, outcome, session)
                return outcome
        if decision.simulate or decision.risk is not Risk.READ_ONLY:
            # En las fases 0 a 3 no hay ejecutores reales de escritura: todo se simula.
            outcome.update(
                status="simulated",
                simulated=True,
                message="Simulado (DRY_RUN): no se ha hecho nada fuera de CRISVIS.",
            )
        else:
            outcome.update(status="done", message="Lectura completada.")
        self._record_action(action, decision, outcome, session)
        return outcome

    def _record_action(
        self,
        action: ProposedAction,
        decision: PolicyDecision,
        outcome: dict[str, Any],
        session: str,
    ) -> None:
        self.audit.record(
            "action",
            session=session,
            source=action.source,
            tool=action.tool,
            risk=decision.risk,
            decision=decision.verdict.value,
            outcome=outcome["status"],
            dry_run=decision.simulate,
            details={"target": action.target, "message": outcome.get("message", "")},
        )

    # -- auditoría -----------------------------------------------------------

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.audit.recent(limit)

    @staticmethod
    def frame(result: DailyBriefingResult | ConnectorResult) -> dict[str, Any]:
        return json.loads(result.model_dump_json())
