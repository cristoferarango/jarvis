"""El flujo daily-briefing de punta a punta con fuentes de prueba."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from crisvis.openclaw.adapter import AdapterError, OpenClawAdapter
from crisvis.openclaw.briefing import zone
from crisvis.openclaw.contracts import (
    BriefingItem,
    ConnectorMode,
    ConnectorResult,
    SourceState,
)
from crisvis.openclaw.providers.base import FetchContext, Fetched, Provider
from crisvis.openclaw.providers.mock import SPECS, MockProvider, mock_providers
from crisvis.openclaw.summary import voice_summary
from crisvis.openclaw.triggers import match_trigger
from crisvis.openclaw.workflow import load_workflow
from crisvis.settings import Settings

PHRASES = load_workflow().disparadores


def _settings(tmp_path: Path, **cfg: Any) -> Settings:
    settings = Settings()
    settings.home = tmp_path
    settings.openclaw.timeout_fuente = 0.3
    settings.openclaw.timeout_global = 2.0
    settings.openclaw.usuario = "Cristofer"
    for key, value in cfg.items():
        setattr(settings.openclaw, key, value)
    return settings


def _adapter(tmp_path: Path, failures: dict[str, str] | None = None, **cfg: Any) -> OpenClawAdapter:
    providers = mock_providers(failures or {})
    for p in providers.values():
        assert isinstance(p, MockProvider)
        p.delay = 0.01
    return OpenClawAdapter(_settings(tmp_path, **cfg), providers)


# -- disparadores --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("Buenos días", "standard"),
        ("Buenos días, informe del día", "standard"),
        ("Informe del día", "standard"),
        ("CRISVIS, dame mi informe", "standard"),
        ("¿Qué tengo hoy?", "standard"),
        ("Jarvis, ¿qué tengo para hoy?", "standard"),
        ("Dame el informe del día completo, por favor", "complete"),
        ("Informe del día rápido", "quick"),
    ],
)
def test_triggers(text: str, detail: str) -> None:
    assert match_trigger(text, PHRASES) == detail


@pytest.mark.parametrize(
    "text",
    [
        "Buenos días, ¿qué tiempo hace en Lima?",
        "¿Qué hora es?",
        "Abre el informe de ventas en Excel",
        "¿Qué tengo que hacer para instalar Docker?",
        "hola",
        "buenos días " * 40,
    ],
)
def test_other_requests_are_not_hijacked(text: str) -> None:
    assert match_trigger(text, PHRASES) is None


# -- flujo completo -------------------------------------------------------------


async def test_ten_mock_sources_in_parallel(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    seen: list[str] = []

    async def on_source(result: ConnectorResult) -> None:
        seen.append(result.source)

    start = asyncio.get_running_loop().time()
    result = await adapter.daily_briefing(adapter.build_request("test"), on_source=on_source)
    elapsed = asyncio.get_running_loop().time() - start
    assert len(result.results) == 10
    assert sorted(seen) == sorted(SPECS)
    assert all(r.state is SourceState.SUCCESS for r in result.results)
    assert all(r.mode is ConnectorMode.MOCK for r in result.results)
    assert elapsed < 1.0  # en paralelo, no 10 × retardo
    assert result.dry_run is True


async def test_timeouts_and_errors_are_reported(tmp_path: Path) -> None:
    adapter = _adapter(
        tmp_path,
        {
            "n8n": "timeout",
            "crm": "unauthorized",
            "gitlab": "unavailable",
            "github": "error",
            "documentos": "partial",
        },
    )
    result = await adapter.daily_briefing(adapter.build_request("test"))
    by = {r.source: r for r in result.results}
    assert by["n8n"].state is SourceState.TIMEOUT
    assert by["crm"].state is SourceState.UNAUTHORIZED
    assert by["gitlab"].state is SourceState.UNAVAILABLE
    assert by["github"].state is SourceState.ERROR
    assert by["documentos"].state is SourceState.PARTIAL
    assert by["correo"].state is SourceState.SUCCESS
    # Mensajes seguros: nada del error interno llega a la cara.
    assert "simulado" not in by["github"].message
    assert by["github"].message == "Error interno al consultar la fuente."
    # Sin datos inventados para lo que falló.
    for source in ("n8n", "crm", "gitlab", "github"):
        assert by[source].items == []
    assert any(a.source == "crm" and a.level.value == "MEDIUM" for a in result.alerts)
    assert "No pude consultar" in result.voice_summary or "fuentes no respondieron" in (
        result.voice_summary
    )


async def test_global_timeout_cuts_slow_sources(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, {"n8n": "timeout"}, timeout_fuente=10.0, timeout_global=0.3)
    result = await adapter.daily_briefing(adapter.build_request("test"))
    by = {r.source: r for r in result.results}
    assert by["n8n"].state is SourceState.TIMEOUT
    assert "tiempo total" in by["n8n"].message


async def test_alerts_are_classified_and_sorted(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    result = await adapter.daily_briefing(adapter.build_request("test"))
    levels = [a.level.value for a in result.alerts]
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
    assert levels == sorted(levels, key=order.index)
    titles = " ".join(a.title for a in result.alerts)
    assert "Correo prioritario" in titles
    assert "Pipeline fallido" in titles
    assert "Tarea vencida" in titles


async def test_detail_limits_items(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    quick = await adapter.daily_briefing(adapter.build_request("t", detail="quick"))
    assert max(len(r.items) for r in quick.results) <= 3


async def test_cancel_stops_every_source(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, {"n8n": "timeout"}, timeout_fuente=30.0, timeout_global=30.0)
    task = asyncio.create_task(adapter.daily_briefing(adapter.build_request("t")))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events = adapter.recent_audit()
    assert any(e["kind"] == "briefing.done" and e["outcome"] == "cancelado" for e in events)


async def test_briefing_is_audited_and_takes_no_external_action(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.daily_briefing(adapter.build_request("Buenos días"), session="sesion-1234567")
    events = adapter.recent_audit(100)
    kinds = [e["kind"] for e in events]
    assert kinds.count("briefing.source") == 10
    assert "briefing.request" in kinds
    done = next(e for e in events if e["kind"] == "briefing.done")
    assert done["details"]["external_actions"] == 0
    assert "action" not in kinds
    assert all(e["dry_run"] for e in events)
    assert all(len(e["session"]) <= 8 for e in events)


async def test_rate_limit(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, max_informes_por_minuto=2)
    await adapter.daily_briefing(adapter.build_request("t"), session="s")
    await adapter.daily_briefing(adapter.build_request("t"), session="s")
    with pytest.raises(AdapterError):
        await adapter.daily_briefing(adapter.build_request("t"), session="s")


async def test_refresh_one_source(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    await adapter.daily_briefing(adapter.build_request("t"))
    result = await adapter.refresh_source("agenda")
    assert result.source == "agenda"
    with pytest.raises(AdapterError):
        await adapter.refresh_source("../../etc")


class LeakyProvider(Provider):
    id, name, tab, tool = "correo", "Correo", "correo", "correo.list_priority"
    mode = ConnectorMode.SANDBOX

    async def fetch(self, ctx: FetchContext) -> Fetched:
        return Fetched(
            items=[
                BriefingItem(id="1", title="Tu token=abcd1234efgh", subtitle="Bearer abcdefghijkl")
            ]
        )


async def test_secrets_in_source_data_are_redacted(tmp_path: Path) -> None:
    adapter = OpenClawAdapter(_settings(tmp_path), {"correo": LeakyProvider()})
    result = await adapter.daily_briefing(adapter.build_request("t"))
    item = result.results[0].items[0]
    assert "abcd1234efgh" not in item.title
    assert "abcdefghijkl" not in item.subtitle
    assert "abcd1234efgh" not in adapter.audit.path.read_text("utf-8")


# -- resumen de voz ---------------------------------------------------------------


async def test_voice_summary_matches_the_expected_shape(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    request = adapter.build_request("t")
    result = await adapter.daily_briefing(request)
    text = result.voice_summary
    assert "Cristofer" in text
    assert "Tengo preparado tu informe" in text
    assert "tres correos prioritarios" in text
    assert "dos tareas vencidas" in text
    assert "datos de prueba" in text
    assert text.endswith("El detalle está disponible en pantalla.")


def _result(source: str, items: list[BriefingItem], state: str = "success") -> ConnectorResult:
    return ConnectorResult(
        source=source,
        name=source,
        tab="correo",
        mode="real",
        state=state,
        fetched_at=datetime.now(zone("America/Lima")),
        items=items,
    )


def test_voice_summary_with_real_data_example() -> None:
    now = datetime(2026, 10, 3, 8, 0, tzinfo=zone("America/Lima"))
    mails = [
        BriefingItem(id=str(i), title="m", meta={"prioridad": "alta", "leido": False})
        for i in range(3)
    ]
    meeting = BriefingItem(id="e", title="r", when=now.replace(hour=11))
    tasks = [BriefingItem(id=str(i), title="t", meta={"vencida": True}) for i in range(2)]
    system = [BriefingItem(id="s", title="ok", meta={"estado": "ok"})]
    text = voice_summary(
        [
            _result("correo", mails),
            _result("agenda", [meeting]),
            _result("tareas", tasks),
            _result("sistema", system),
        ],
        now,
        "Cristofer",
    )
    assert text == (
        "Buenos días, Cristofer. Tengo preparado tu informe. Hay tres correos prioritarios, "
        "una reunión a las once, dos tareas vencidas y ningún servicio crítico caído. "
        "El detalle está disponible en pantalla."
    )


def test_voice_summary_never_invents_numbers_for_failed_sources() -> None:
    now = datetime(2026, 10, 3, 15, 0, tzinfo=zone("America/Lima"))
    text = voice_summary([_result("correo", [], state="unauthorized")], now, "")
    assert "correo" not in text.lower().split("no pude consultar")[0]
    assert "No pude consultar correo" in text
    assert text.startswith("Buenas tardes.")


# -- sesión (lo que recibe la cara) ----------------------------------------------------


async def test_session_runs_the_briefing_without_the_llm(
    tmp_path: Path, _no_real_browser_tabs: list[str]
) -> None:
    from crisvis.gateway.session import SessionRegistry
    from crisvis.security import AuditLog, PermissionPolicy

    settings = _settings(tmp_path)
    adapter = _adapter(
        tmp_path,
        abrir_intervalo=0,
        abrir_al_informe=[
            "https://mail.google.com/",
            "https://www.notion.so/",
            "https://www.youtube.com/",
        ],
    )
    # El cerebro no tiene new_agent: si el informe pasara por el modelo, fallaría.
    brain = SimpleNamespace(
        see=None,
        vision_model="",
        status=SimpleNamespace(servers=[], as_frame=lambda: {"ok": True}),
    )
    registry = SessionRegistry(
        settings,
        brain,
        PermissionPolicy(settings.permisos),
        AuditLog(tmp_path / "a.jsonl"),
        adapter,
    )
    session = registry.create()
    frames: list[dict[str, Any]] = []

    async def send(frame: dict[str, Any]) -> None:
        frames.append(json.loads(json.dumps(frame, default=str)))

    await session.attach(send)
    await session.ask("Buenos días, informe del día", "a1")
    assert session._turn is not None
    await session._turn
    await asyncio.sleep(0.05)
    phases = [f["phase"] for f in frames if f["type"] == "briefing"]
    assert phases[0] == "start"
    assert phases.count("source") == 10
    assert phases[-1] == "done"
    spoken = [f for f in frames if f["type"] == "text"]
    assert spoken and spoken[0]["ask"] == "a1"
    assert any(f["type"] == "done" and f["ask"] == "a1" for f in frames)
    assert _no_real_browser_tabs == [
        "https://mail.google.com/",
        "https://www.notion.so/",
        "https://www.youtube.com/",
    ]
    await session.detach()


async def test_session_approval_flow_is_exact(tmp_path: Path) -> None:
    from crisvis.gateway.session import SessionRegistry
    from crisvis.security import AuditLog, PermissionPolicy

    settings = _settings(tmp_path)
    adapter = _adapter(tmp_path)
    brain = SimpleNamespace(see=None, vision_model="", status=SimpleNamespace(servers=[]))
    registry = SessionRegistry(
        settings,
        brain,
        PermissionPolicy(settings.permisos),
        AuditLog(tmp_path / "a.jsonl"),
        adapter,
    )
    session = registry.create()
    frames: list[dict[str, Any]] = []

    async def send(frame: dict[str, Any]) -> None:
        frames.append(frame)
        if frame["type"] == "approval":
            request = frame["request"]
            assert request["action"]["preview"]
            assert request["action"]["target"]
            assert request["expires_at"]
            session.on_reply(frame["id"], {"approved": True, "fingerprint": request["fingerprint"]})

    await session.attach(send)
    await adapter.daily_briefing(adapter.build_request("t"))
    await session.propose("mail-send-1")
    await session.propose("mail-delete-1")
    await asyncio.sleep(0.05)
    outcomes = {f["proposal"]: f for f in frames if f["type"] == "outcome"}
    assert outcomes["mail-send-1"]["status"] == "simulated"
    assert outcomes["mail-send-1"]["executed"] is False
    assert outcomes["mail-delete-1"]["status"] == "blocked"
    assert sum(1 for f in frames if f["type"] == "approval") == 1
    await session.detach()


async def test_session_rejects_approval_with_wrong_fingerprint(tmp_path: Path) -> None:
    from crisvis.gateway.session import SessionRegistry
    from crisvis.security import AuditLog, PermissionPolicy

    settings = _settings(tmp_path)
    adapter = _adapter(tmp_path)
    brain = SimpleNamespace(see=None, vision_model="", status=SimpleNamespace(servers=[]))
    registry = SessionRegistry(
        settings,
        brain,
        PermissionPolicy(settings.permisos),
        AuditLog(tmp_path / "a.jsonl"),
        adapter,
    )
    session = registry.create()
    frames: list[dict[str, Any]] = []

    async def send(frame: dict[str, Any]) -> None:
        frames.append(frame)
        if frame["type"] == "approval":
            session.on_reply(frame["id"], {"approved": True, "fingerprint": "otra"})

    await session.attach(send)
    await adapter.daily_briefing(adapter.build_request("t"))
    await session.propose("mail-send-1")
    await asyncio.sleep(0.05)
    outcome = next(f for f in frames if f["type"] == "outcome")
    assert outcome["status"] == "blocked"
    await session.detach()
