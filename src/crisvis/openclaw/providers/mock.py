"""Fuentes de prueba: datos ficticios, deterministas y marcados como tales.

Sirven para ensayar el informe sin conectar ninguna cuenta. Cada fuente puede
simular un fallo (timeout, unauthorized, unavailable, error, partial) para
probar cómo se muestran los errores.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta

from crisvis.openclaw.contracts import (
    BriefingItem,
    ConnectorMode,
    ProposedAction,
    Risk,
    SourceState,
    Tab,
)
from crisvis.openclaw.providers.base import FetchContext, Fetched, Provider, SourceError, meta

FAILURES = ("timeout", "unauthorized", "unavailable", "error", "partial")
_ACCOUNT = "cuenta de prueba"


def _at(ctx: FetchContext, hour: int, minute: int = 0, days: int = 0) -> datetime:
    day = ctx.now + timedelta(days=days)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _correo(ctx: FetchContext) -> Fetched:
    rows = [
        ("Contrato de mantenimiento: falta tu firma", "Ana Torres", "alta", False, 7, 45),
        ("Factura vencida del proveedor de hosting", "Facturación", "alta", False, 8, 10),
        ("Cambio de hora en la revisión de sprint", "Luis Pérez", "alta", False, 8, 32),
        ("Boletín semanal de la comunidad", "Newsletter", "normal", False, 6, 0),
        ("Re: propuesta de diseño", "Marta Ruiz", "normal", True, 7, 5),
    ]
    items = [
        BriefingItem(
            id=f"mail-{i}",
            title=subject,
            subtitle=sender,
            when=_at(ctx, h, m),
            meta=meta(de=sender, prioridad=prio, leido=read),
        )
        for i, (subject, sender, prio, read, h, m) in enumerate(rows, 1)
    ]
    first = items[0]
    proposals = [
        ProposedAction(
            id="mail-draft-1",
            tool="correo.create_draft",
            source="correo",
            risk=Risk.MEDIUM,
            service="Correo",
            account=_ACCOUNT,
            target=str(first.meta["de"]),
            params={"asunto": f"Re: {first.title}"},
            preview="Hola Ana, lo reviso hoy y te devuelvo el contrato firmado. Gracias.",
            impact="Crea un borrador en el buzón. No se envía nada.",
        ),
        ProposedAction(
            id="mail-send-1",
            tool="correo.send",
            source="correo",
            risk=Risk.HIGH,
            service="Correo",
            account=_ACCOUNT,
            target=str(first.meta["de"]),
            params={"asunto": f"Re: {first.title}"},
            preview="Hola Ana, lo reviso hoy y te devuelvo el contrato firmado. Gracias.",
            impact="Envía un correo real a un tercero. No se puede deshacer.",
        ),
        ProposedAction(
            id="mail-delete-1",
            tool="correo.delete",
            source="correo",
            risk=Risk.CRITICAL,
            service="Correo",
            account=_ACCOUNT,
            target="Boletines de más de 30 días",
            params={"cantidad": 40},
            preview="Borrar 40 boletines antiguos.",
            impact="Borrado masivo. Bloqueado por defecto.",
        ),
    ]
    unread_high = sum(1 for r in rows if r[2] == "alta" and not r[3])
    return Fetched(
        items=items, proposals=proposals, counts={"prioritarios": unread_high, "total": len(rows)}
    )


def _agenda(ctx: FetchContext) -> Fetched:
    rows = [
        ("Revisión de sprint", 11, 0, 60, "Meet"),
        ("Llamada con cliente: renovación", 16, 30, 30, "Teléfono"),
    ]
    items = []
    for i, (title, h, m, minutes, place) in enumerate(rows, 1):
        start = _at(ctx, h, m)
        items.append(
            BriefingItem(
                id=f"ev-{i}",
                title=title,
                subtitle=place,
                when=start,
                meta=meta(
                    fin=(start + timedelta(minutes=minutes)).strftime("%H:%M"),
                    lugar=place,
                    minutos_para=int((start - ctx.now).total_seconds() // 60),
                ),
            )
        )
    return Fetched(items=items, counts={"reuniones": len(items)})


def _tareas(ctx: FetchContext) -> Fetched:
    rows = [
        ("Enviar presupuesto a Botwoot", -2, "alta"),
        ("Actualizar dependencias de CRISVIS", -1, "media"),
        ("Preparar demo del informe diario", 0, "alta"),
        ("Revisar métricas de n8n", 3, "baja"),
    ]
    today = ctx.now.date()
    items = []
    for i, (title, delta, prio) in enumerate(rows, 1):
        due = today + timedelta(days=delta)
        items.append(
            BriefingItem(
                id=f"task-{i}",
                title=title,
                subtitle=f"Vence {due.isoformat()}",
                meta=meta(vence=due.isoformat(), vencida=delta < 0, prioridad=prio),
            )
        )
    overdue = sum(1 for r in rows if r[1] < 0)
    return Fetched(items=items, counts={"vencidas": overdue, "abiertas": len(rows)})


def _repo(kind: str) -> Callable[[FetchContext], Fetched]:
    def build(ctx: FetchContext) -> Fetched:
        if kind == "gitlab":
            rows = [
                ("Pipeline main #482", "pipeline", "failed", "crisvis/core", 7, 50),
                ("MR !31: informe diario", "mr", "open", "crisvis/core", 9, 0),
            ]
        else:
            rows = [
                ("PR #12: tests del adaptador", "pr", "open", "crisvis/face", 8, 15),
                ("Issue #40: voz entrecortada", "issue", "open", "crisvis/face", 6, 40),
            ]
        items = [
            BriefingItem(
                id=f"{kind}-{i}",
                title=title,
                subtitle=repo,
                when=_at(ctx, h, m),
                meta=meta(tipo=t, estado=state, repo=repo),
            )
            for i, (title, t, state, repo, h, m) in enumerate(rows, 1)
        ]
        failed = sum(1 for r in rows if r[2] == "failed")
        return Fetched(items=items, counts={"fallidos": failed, "abiertos": len(rows) - failed})

    return build


def _documentos(ctx: FetchContext) -> Fetched:
    rows = [
        ("Propuesta comercial Q4.docx", "Ana Torres", True, -1),
        ("Arquitectura CRISVIS.pdf", "Tú", False, 0),
    ]
    items = [
        BriefingItem(
            id=f"doc-{i}",
            title=name,
            subtitle=f"Modificado por {who}",
            when=_at(ctx, 9, 0, days=days),
            meta=meta(modificado_por=who, compartido=shared),
        )
        for i, (name, who, shared, days) in enumerate(rows, 1)
    ]
    return Fetched(items=items, counts={"recientes": len(items)})


def _sistema(ctx: FetchContext) -> Fetched:
    rows = [
        ("Disco C:", "disco_libre_pct", 38, "ok"),
        ("Memoria RAM", "ram_pct", 62, "ok"),
        ("VRAM", "vram_pct", 81, "ok"),
        ("Ollama", "servicio", 1, "ok"),
    ]
    items = [
        BriefingItem(
            id=f"sys-{i}",
            title=name,
            subtitle=f"{value}%" if metric.endswith("pct") else "activo",
            meta=meta(metrica=metric, valor=value, estado=state),
        )
        for i, (name, metric, value, state) in enumerate(rows, 1)
    ]
    return Fetched(items=items, counts={"criticos": 0})


def _docker(ctx: FetchContext) -> Fetched:
    rows = [("n8n", "running", True), ("chatwoot", "running", True), ("redis", "exited", False)]
    items = [
        BriefingItem(
            id=f"ctr-{i}",
            title=name,
            subtitle=state,
            meta=meta(estado=state, critico=critical),
        )
        for i, (name, state, critical) in enumerate(rows, 1)
    ]
    down = sum(1 for r in rows if r[2] and r[1] != "running")
    return Fetched(items=items, counts={"caidos_criticos": down})


def _n8n(ctx: FetchContext) -> Fetched:
    rows = [("Leads a CRM", "success", 7, 0), ("Resumen de tickets", "error", 7, 30)]
    items = [
        BriefingItem(
            id=f"wf-{i}",
            title=name,
            subtitle=state,
            when=_at(ctx, h, m),
            meta=meta(estado=state, workflow=name),
        )
        for i, (name, state, h, m) in enumerate(rows, 1)
    ]
    return Fetched(items=items, counts={"errores": sum(1 for r in rows if r[1] == "error")})


def _crm(ctx: FetchContext) -> Fetched:
    rows = [
        ("Consulta de precios", "WhatsApp", "open", True),
        ("Soporte: no llega el código", "Web", "open", False),
        ("Renovación anual", "Correo", "pending", False),
    ]
    items = [
        BriefingItem(
            id=f"conv-{i}",
            title=title,
            subtitle=channel,
            meta=meta(canal=channel, estado=state, sla_vencido=late),
        )
        for i, (title, channel, state, late) in enumerate(rows, 1)
    ]
    return Fetched(
        items=items,
        counts={"abiertas": sum(1 for r in rows if r[2] == "open"), "sla_vencido": 1},
    )


SPECS: dict[str, tuple[str, Tab, str, Callable[[FetchContext], Fetched]]] = {
    "correo": ("Correo", "correo", "correo.list_priority", _correo),
    "agenda": ("Calendario", "agenda", "agenda.list_today", _agenda),
    "tareas": ("Tareas", "tareas", "tareas.list_open", _tareas),
    "github": ("GitHub", "proyectos", "github.list_activity", _repo("github")),
    "gitlab": ("GitLab", "proyectos", "gitlab.list_activity", _repo("gitlab")),
    "documentos": ("Drive / documentos", "documentos", "documentos.list_recent", _documentos),
    "sistema": ("Sistema local", "sistema", "sistema.snapshot", _sistema),
    "docker": ("Docker", "sistema", "docker.list_containers", _docker),
    "n8n": ("n8n", "automatizaciones", "n8n.list_executions", _n8n),
    "crm": ("Botwoot / CRM", "negocio", "crm.list_conversations", _crm),
}


class MockProvider(Provider):
    mode = ConnectorMode.MOCK
    integration = "mock"

    def __init__(self, source: str, failure: str = "", delay: float = 0.05) -> None:
        name, tab, tool, build = SPECS[source]
        self.id, self.name, self.tab, self.tool = source, name, tab, tool
        self._build = build
        self.fail_mode = failure if failure in FAILURES else ""
        self.delay = delay

    def hint(self) -> str:
        return "Datos de prueba: no hay ninguna cuenta conectada."

    async def fetch(self, ctx: FetchContext) -> Fetched:
        await asyncio.sleep(self.delay)
        if self.fail_mode == "timeout":
            await asyncio.sleep(3600)
        if self.fail_mode == "unauthorized":
            raise SourceError(
                SourceState.UNAUTHORIZED, "Credenciales no válidas o revocadas (simulado)."
            )
        if self.fail_mode == "unavailable":
            raise SourceError(SourceState.UNAVAILABLE, "El servicio no responde (simulado).")
        if self.fail_mode == "error":
            raise RuntimeError("fallo interno simulado")
        fetched = self._build(ctx)
        fetched.message = "Datos de prueba."
        if self.fail_mode == "partial":
            fetched.items = fetched.items[: max(1, len(fetched.items) // 2)]
            fetched.state = SourceState.PARTIAL
            fetched.message = "Respuesta incompleta (simulado): faltan elementos."
        return fetched


def mock_providers(failures: dict[str, str] | None = None) -> dict[str, Provider]:
    failures = failures or {}
    return {source: MockProvider(source, failures.get(source, "")) for source in SPECS}
