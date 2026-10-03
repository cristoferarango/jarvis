"""Motor de políticas de las acciones del adaptador.

READ_ONLY  automática si el conector está conectado.
LOW        solo con ``permitir_bajo``.
MEDIUM     vista previa + aprobación.
HIGH       aprobación exacta por acción, siempre.
CRITICAL   bloqueada salvo ``critico_habilitado``, y entonces con aprobación.

Con DRY_RUN (o un conector que no sea real) nada se ejecuta: se simula y se
audita.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from crisvis.openclaw.contracts import ConnectorMode, ProposedAction, Risk, ToolDefinition
from crisvis.openclaw.tools import TOOLS
from crisvis.settings import OpenClawSettings

_RANK = {r: i for i, r in enumerate(Risk)}


class Verdict(str, Enum):
    ALLOW = "allow"
    APPROVE = "approve"
    BLOCK = "block"


@dataclass(frozen=True)
class PolicyDecision:
    verdict: Verdict
    risk: Risk
    reason: str
    simulate: bool


class ActionPolicy:
    def __init__(
        self, settings: OpenClawSettings, tools: dict[str, ToolDefinition] | None = None
    ) -> None:
        self.settings = settings
        self.tools = tools if tools is not None else TOOLS

    def risk_of(self, action: ProposedAction) -> Risk | None:
        tool = self.tools.get(action.tool)
        if tool is None:
            return None
        # Quien propone puede subir el riesgo, nunca bajarlo.
        return max(tool.risk, action.risk, key=_RANK.__getitem__)

    def evaluate(self, action: ProposedAction, mode: ConnectorMode) -> PolicyDecision:
        risk = self.risk_of(action)
        if risk is None:
            return PolicyDecision(
                Verdict.BLOCK, Risk.CRITICAL, f"'{action.tool}' no está en el registro", True
            )
        simulate = self.settings.dry_run or mode is not ConnectorMode.REAL
        if mode is ConnectorMode.NOT_CONFIGURED:
            return PolicyDecision(Verdict.BLOCK, risk, "el conector no está configurado", True)
        if risk is Risk.READ_ONLY:
            return PolicyDecision(Verdict.ALLOW, risk, "solo lectura", simulate)
        if risk is Risk.LOW:
            if not self.settings.permitir_bajo:
                return PolicyDecision(
                    Verdict.BLOCK, risk, "nivel LOW deshabilitado en la configuración", simulate
                )
            return PolicyDecision(Verdict.ALLOW, risk, "LOW permitido por configuración", simulate)
        if risk is Risk.CRITICAL and not self.settings.critico_habilitado:
            return PolicyDecision(
                Verdict.BLOCK,
                risk,
                "CRITICAL bloqueado por defecto; requiere habilitación manual",
                simulate,
            )
        return PolicyDecision(Verdict.APPROVE, risk, "requiere aprobación exacta", simulate)
