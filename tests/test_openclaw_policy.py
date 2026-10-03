"""Motor de políticas, aprobaciones exactas y ejecución (siempre simulada en estas fases)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from crisvis.openclaw.adapter import AdapterError, OpenClawAdapter
from crisvis.openclaw.approvals import ApprovalError, ApprovalManager
from crisvis.openclaw.contracts import ConnectorMode, ProposedAction, Risk
from crisvis.openclaw.policy import ActionPolicy, Verdict
from crisvis.openclaw.providers.mock import mock_providers
from crisvis.settings import OpenClawSettings, Settings


def _action(tool: str, risk: Risk = Risk.READ_ONLY, **kw: object) -> ProposedAction:
    return ProposedAction(
        id=kw.pop("id", "p1"),  # type: ignore[arg-type]
        tool=tool,
        source=kw.pop("source", "correo"),  # type: ignore[arg-type]
        risk=risk,
        service="Correo",
        target=kw.pop("target", "ana@example.com"),  # type: ignore[arg-type]
        params=kw.pop("params", {"asunto": "Hola"}),  # type: ignore[arg-type]
        preview="Texto exacto",
    )


@pytest.mark.parametrize(
    ("tool", "verdict"),
    [
        ("correo.list_priority", Verdict.ALLOW),
        ("sandbox.save_briefing", Verdict.BLOCK),  # LOW deshabilitado por defecto
        ("correo.create_draft", Verdict.APPROVE),
        ("navegador.open_url", Verdict.APPROVE),
        ("correo.send", Verdict.APPROVE),
        ("mensajeria.send", Verdict.APPROVE),
        ("git.push", Verdict.APPROVE),
        ("correo.delete", Verdict.BLOCK),
        ("pagos.pay", Verdict.BLOCK),
        ("correo.bulk_send", Verdict.BLOCK),
        ("execute.anything", Verdict.BLOCK),
        ("shell_exec", Verdict.BLOCK),
    ],
)
def test_policy_matrix(tool: str, verdict: Verdict) -> None:
    policy = ActionPolicy(OpenClawSettings())
    assert policy.evaluate(_action(tool), ConnectorMode.MOCK).verdict is verdict


def test_low_only_when_configured() -> None:
    policy = ActionPolicy(OpenClawSettings(permitir_bajo=True))
    decision = policy.evaluate(_action("sandbox.save_briefing"), ConnectorMode.MOCK)
    assert decision.verdict is Verdict.ALLOW
    assert decision.simulate is True


def test_critical_needs_manual_enable_and_then_approval() -> None:
    policy = ActionPolicy(OpenClawSettings(critico_habilitado=True))
    assert policy.evaluate(_action("correo.delete"), ConnectorMode.REAL).verdict is Verdict.APPROVE


def test_proposer_cannot_lower_the_risk() -> None:
    policy = ActionPolicy(OpenClawSettings())
    sneaky = _action("correo.send", Risk.READ_ONLY)
    decision = policy.evaluate(sneaky, ConnectorMode.REAL)
    assert decision.risk is Risk.HIGH
    assert decision.verdict is Verdict.APPROVE


def test_not_configured_connector_blocks_everything() -> None:
    policy = ActionPolicy(OpenClawSettings())
    decision = policy.evaluate(_action("correo.list_priority"), ConnectorMode.NOT_CONFIGURED)
    assert decision.verdict is Verdict.BLOCK


def test_dry_run_simulates_even_real_connectors() -> None:
    policy = ActionPolicy(OpenClawSettings(dry_run=True))
    assert policy.evaluate(_action("correo.send"), ConnectorMode.REAL).simulate is True


# -- aprobaciones ---------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 3, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


def test_approval_is_exact_and_single_use() -> None:
    manager = ApprovalManager(60)
    action = _action("correo.send", Risk.HIGH)
    request = manager.create(action, dry_run=True)
    with pytest.raises(ApprovalError):
        manager.resolve(request.id, True, "huella-falsa")
    manager.resolve(request.id, True, request.fingerprint)
    # Otra acción (otro destinatario) no puede usar esta aprobación.
    other = _action("correo.send", Risk.HIGH, target="otro@example.com")
    with pytest.raises(ApprovalError):
        manager.consume(request.id, other)
    manager.consume(request.id, action)
    with pytest.raises(ApprovalError):
        manager.consume(request.id, action)


def test_approval_expires() -> None:
    clock = Clock()
    manager = ApprovalManager(30, clock=clock)
    action = _action("correo.send", Risk.HIGH)
    request = manager.create(action, dry_run=True)
    clock.now += timedelta(seconds=31)
    with pytest.raises(ApprovalError, match="expired"):
        manager.resolve(request.id, True, request.fingerprint)


def test_approved_then_expired_cannot_be_used() -> None:
    clock = Clock()
    manager = ApprovalManager(30, clock=clock)
    action = _action("correo.send", Risk.HIGH)
    request = manager.create(action, dry_run=True)
    manager.resolve(request.id, True, request.fingerprint)
    clock.now += timedelta(seconds=40)
    with pytest.raises(ApprovalError):
        manager.consume(request.id, action)


def test_denied_cannot_be_used() -> None:
    manager = ApprovalManager(60)
    action = _action("correo.send", Risk.HIGH)
    request = manager.create(action, dry_run=True)
    manager.resolve(request.id, False, request.fingerprint)
    with pytest.raises(ApprovalError):
        manager.consume(request.id, action)


# -- ejecución a través del adaptador ----------------------------------------------


def _adapter(tmp_path: Path, **cfg: object) -> OpenClawAdapter:
    settings = Settings()
    settings.home = tmp_path
    for key, value in cfg.items():
        setattr(settings.openclaw, key, value)
    return OpenClawAdapter(settings, mock_providers())


def test_send_is_never_executed_without_approval(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    out = adapter.execute(_action("correo.send", Risk.HIGH))
    assert out["status"] == "needs_approval"
    assert out["executed"] is False


def test_delete_is_blocked(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    out = adapter.execute(_action("correo.delete", Risk.CRITICAL))
    assert out["status"] == "blocked"
    assert out["executed"] is False


def test_write_with_exact_approval_is_only_simulated(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    action = _action("correo.send", Risk.HIGH)
    request = adapter.request_approval(action)
    adapter.resolve_approval(request.id, True, request.fingerprint)
    out = adapter.execute(action, request.id)
    assert out["status"] == "simulated"
    assert out["executed"] is False
    # La misma aprobación no sirve dos veces.
    again = adapter.execute(action, request.id)
    assert again["status"] == "blocked"


def test_tampered_approval_is_rejected(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    action = _action("correo.send", Risk.HIGH)
    request = adapter.request_approval(action)
    with pytest.raises(AdapterError):
        adapter.resolve_approval(request.id, True, "0" * 64)


def test_every_decision_is_audited_without_secrets(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    action = _action("correo.send", Risk.HIGH, params={"api_key": "sk-" + "x" * 30})
    adapter.execute(action)
    events = adapter.recent_audit()
    kinds = {e["kind"] for e in events}
    assert {"policy", "action"} <= kinds
    raw = adapter.audit.path.read_text("utf-8")
    assert "sk-xxxx" not in raw
