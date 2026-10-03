"""Una sesión: la conversación entre la cara y el cerebro.

En la interfaz original el socket ERA la sesión: si se caía, JARVIS olvidaba la
conversación aunque la pantalla siguiera mostrándola. Aquí la sesión vive en el
núcleo con su propio identificador y el socket solo se engancha a ella, así que
una reconexión retoma la charla donde estaba (ver ``SessionRegistry``).

La sesión es también la ``Surface`` del cuerpo: las herramientas de la cara
(display, blade, ui_*, look/watch) empujan tramas por aquí desde los hilos del
ToolExecutor de OpenJarvis.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

from crisvis.body import build_body_tools
from crisvis.body.base import new_id
from crisvis.brain.events import Finished, TextDelta, ToolFinished, ToolStarted
from crisvis.execution import ExecutionTracker
from crisvis.security import AuditLog, Decision, PermissionPolicy, Tier
from crisvis.security.grants import GrantBook
from crisvis.security.guard import Risk, SequenceGuard, assess

log = logging.getLogger(__name__)

T = TypeVar("T")
Send = Callable[[dict[str, Any]], Awaitable[None]]
Close = Callable[[int, str], Awaitable[None]]

SESSION_TTL_SECONDS = 15 * 60
SETTLE_SECONDS = 0.4
# Cuánto se espera a que una herramienta cancelada termine de verdad.
CANCEL_WAIT_SECONDS = 6.0
INTERRUPTED_MARK = " [interrumpido]"
# Teclado y ratón: nunca se ejecutan sin aprobación individual, ni en LIBRE.
GUI_INPUT = frozenset({"pc_click", "pc_type", "pc_keys"})
# Herramientas cuya aprobación queda ligada a la ventana que hay delante.
WINDOW_BOUND = GUI_INPUT | {"pc_scroll", "pc_window"}


def foreground_label() -> str:
    """Título y proceso de la ventana sobre la que actuaría el control del PC."""
    try:
        from crisvis.body.desktop import DESKTOP, WINDOWS

        if not WINDOWS:
            return ""
        win = DESKTOP.target()
        return f"{win.title} [{win.process}]" if win else ""
    except Exception:  # noqa: BLE001
        return ""


def summarise(tool: str, args: dict[str, Any]) -> str:
    """Una frase que el usuario pueda aprobar sin leer JSON."""

    def arg(*names: str) -> str:
        for n in names:
            v = args.get(n)
            if v:
                text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                return text if len(text) <= 160 else text[:160] + "…"
        return ""

    if tool == "shell_exec":
        return f"Ejecutar la orden: {arg('command', 'cmd')}"
    if tool in ("file_write", "apply_patch"):
        return f"Escribir en el archivo {arg('path', 'file_path', 'filename')}"
    if tool == "git_commit":
        return f"Hacer un commit: {arg('message')}"
    if tool == "http_request":
        return f"Petición {arg('method') or 'GET'} a {arg('url')}"
    if tool in ("code_interpreter", "repl", "code_interpreter_docker"):
        return f"Ejecutar código: {arg('code', 'source')}"
    pc = {
        "pc_open": lambda: f"abrir {arg('target')}",
        "pc_youtube": lambda: f"poner en YouTube «{arg('query')}»",
        "pc_window": lambda: f"{arg('action')} la ventana {arg('window')}",
        "pc_click": lambda: f"pulsar {arg('target') or arg('element') or arg('x')}",
        "pc_type": lambda: f"escribir «{arg('text')}»",
        "pc_keys": lambda: f"pulsar las teclas {arg('keys')}",
        "pc_scroll": lambda: f"desplazar hacia {arg('direction') or 'abajo'}",
        "pc_file_manage": lambda: f"{arg('action')} {arg('path')} {arg('dest')}".strip(),
    }
    if tool in pc:
        return f"Controlar el PC: {pc[tool]()}"
    if tool == "read_clipboard":
        return "Leer el contenido del portapapeles"
    if tool == "pc_kill":
        return f"Forzar el cierre de {arg('process')}"
    if tool == "pc_power":
        names = {
            "lock": "Bloquear el equipo",
            "sleep": "Suspender el equipo",
            "restart": "Reiniciar el equipo",
            "shutdown": "Apagar el equipo",
            "logout": "Cerrar la sesión de Windows",
            "cancel": "Cancelar el apagado",
        }
        return names.get(arg("action"), f"Energía: {arg('action')}")
    detail = ", ".join(f"{k}={arg(k)}" for k in list(args)[:3] if k != "_taint")
    return f"Usar {tool}" + (f" ({detail})" if detail else "")


class Session:
    def __init__(
        self, registry: SessionRegistry, loop: asyncio.AbstractEventLoop, owner: str = ""
    ) -> None:
        self.id = secrets.token_urlsafe(18)
        # Cliente autenticado dueño de la sesión: solo él puede retomarla.
        self.owner = owner
        self.registry = registry
        self.loop = loop
        self.history: list[tuple[str, str]] = []
        self.last_seen = time.monotonic()
        self._send: Send | None = None
        self._close: Close | None = None
        self._outbox: asyncio.Queue[dict[str, Any] | None] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._turn: asyncio.Task[None] | None = None
        self._answering: str | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seq = 0
        self._jobs: set[asyncio.Task[None]] = set()
        self.tracker = ExecutionTracker(registry.audit, self.id)
        self.grants = GrantBook()
        self.sequence = SequenceGuard()
        self.window_label: Callable[[], str] = foreground_label
        settings = registry.settings
        security = getattr(settings, "seguridad", None)
        self._apps = tuple(getattr(security, "apps_permitidas", ()) or ())
        shell = "shell_exec" in getattr(settings.cerebro, "herramientas", ())
        self.body = build_body_tools(
            self,
            interface=settings.cerebro.herramientas_interfaz,
            camera=settings.cerebro.herramientas_camara,
            see=registry.brain.see
            if getattr(registry.brain, "vision_model", settings.cerebro.modelo_vision)
            else None,
            pc=settings.cerebro.herramientas_pc,
            clipboard=getattr(security, "portapapeles", "confirmar") == "confirmar",
            apps=self._apps,
            shell_token=(lambda: self.tracker.token) if shell else None,
        )

    # -- transporte ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._send is not None

    async def attach(self, send: Send, close: Close | None = None) -> None:
        await self.detach()
        self._send = send
        self._close = close
        self._outbox = asyncio.Queue()
        self._writer = asyncio.create_task(self._pump(self._outbox, send))
        self.last_seen = time.monotonic()

    async def detach(self, send: Send | None = None) -> None:
        """El socket se fue. La conversación se queda; el turno en curso no.

        Con ``send``, solo desengancha si ese sigue siendo el socket actual: una
        pestaña vieja que se cierra no debe cortar a la que retomó la sesión.
        """
        if self._send is None or (send is not None and self._send is not send):
            return
        self._send = None
        self._close = None
        await self.stop_turn()
        for job in list(self._jobs):
            job.cancel()
        if self._outbox is not None:
            self._outbox.put_nowait(None)
        if self._writer is not None:
            try:
                await asyncio.wait_for(self._writer, timeout=1)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                self._writer.cancel()
        self._outbox = None
        self._writer = None
        self.last_seen = time.monotonic()

    @staticmethod
    async def _pump(queue: asyncio.Queue[dict[str, Any] | None], send: Send) -> None:
        # Un único escritor por socket: el orden de las tramas es el orden en
        # que se generaron, también las que llegan desde hilos de herramientas.
        while True:
            frame = await queue.get()
            if frame is None:
                return
            try:
                await send(frame)
            except Exception:  # noqa: BLE001
                return

    def send(self, frame: dict[str, Any]) -> None:
        """Encolar una trama. Solo desde el bucle asyncio."""
        if self._outbox is not None:
            self._outbox.put_nowait(frame)

    # -- Surface (desde hilos de herramientas) ------------------------------

    def push(self, frame: dict[str, Any]) -> None:
        self.loop.call_soon_threadsafe(self.send, frame)

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Trabajo aparte del turno (p. ej. una aprobación) que no debe bloquear el socket."""
        job = asyncio.create_task(coro)
        self._jobs.add(job)
        job.add_done_callback(self._jobs.discard)

    def run(self, coro: Coroutine[Any, Any, T], timeout: float) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def request(self, kind: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        return self.run(self.ask_face(kind, payload, timeout), timeout + 2)

    async def ask_face(self, kind: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("la interfaz no está conectada")
        self._seq += 1
        rid = f"q{self._seq}"
        fut: asyncio.Future[dict[str, Any]] = self.loop.create_future()
        self._pending[rid] = fut
        self.send({"type": kind, "id": rid, **payload})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("la interfaz no respondió a tiempo") from exc
        finally:
            self._pending.pop(rid, None)

    def on_reply(self, rid: str, frame: dict[str, Any]) -> None:
        fut = self._pending.get(rid)
        if fut is not None and not fut.done():
            fut.set_result(frame)

    # -- permisos ------------------------------------------------------------

    async def gate(self, name: str, args: dict[str, Any], spec: Any) -> str | None:
        """Política por herramienta + riesgo de la acción concreta + aprobación individual.

        * CRITICAL (``guard``): bloqueada por política; ni se pregunta.
        * HIGH: aprobación individual siempre, en cualquier modo y aunque la
          configuración la permita.
        * MEDIUM: aprobación individual salvo en LIBRE; teclado y ratón la
          piden también en LIBRE.
        * LOW: sin preguntar (desplazar, colocar ventanas, consultas).
        Cada aprobación vale para una acción: herramienta, parámetros, ventana,
        timeout, sesión y turno exactos, con nonce de un solo uso.
        """
        registry = self.registry
        modes = getattr(registry, "modes", None)
        if modes is not None:
            modes.tick()
        policy: PermissionPolicy = registry.policy
        audit: AuditLog = registry.audit
        verdict = policy.evaluate(name)
        decision = verdict.decision
        assessment = assess(name, args, verdict.tier, apps=self._apps)
        chained = self.sequence.check(name, args)
        if chained is not None:
            assessment = chained
        exec_id = new_id("x")
        turn = self.tracker.turn

        if (
            decision is Decision.ALLOW
            and getattr(spec, "requires_confirmation", False)
            and verdict.tier is not Tier.INTERFAZ
        ):
            decision = Decision.CONFIRM
        if decision is not Decision.DENY and not assessment.blocked:
            from_matrix = verdict.reason.startswith("modo ")
            if assessment.risk is Risk.HIGH:
                decision = Decision.CONFIRM
            elif assessment.risk is Risk.MEDIUM and name in GUI_INPUT:
                decision = Decision.CONFIRM
            elif (
                assessment.risk is Risk.LOW
                and decision is Decision.CONFIRM
                and from_matrix
                and name.startswith("pc_")
                and not getattr(spec, "requires_confirmation", False)
            ):
                # Lista blanca de acciones benignas: desplazar, colocar ventanas.
                decision = Decision.ALLOW

        def record(outcome: str, **extra: Any) -> None:
            if verdict.tier is Tier.INTERFAZ:
                return
            audit.record(
                session=self.id[:8],
                tool=name,
                tier=verdict.tier.value,
                decision=decision.value,
                outcome=outcome,
                args=args,
            )
            audit.event(
                outcome,
                exec=exec_id,
                sesion=self.id[:8],
                turno=turn,
                herramienta=name,
                riesgo=assessment.risk.value,
                modo=policy.mode,
                **extra,
            )

        if assessment.blocked:
            record("bloqueada", motivo=assessment.reason)
            return (
                f"Bloqueado por la política de seguridad: {assessment.reason}. No lo intentes por "
                "otra vía (teclado, ratón, otra ventana u otra herramienta). Díselo al usuario en "
                "una frase."
            )
        if decision is Decision.DENY:
            record("denegada")
            return (
                f"Bloqueado: la herramienta '{name}' no está permitida en el modo de permisos "
                f"actual ({policy.mode}). Díselo al usuario en una frase."
            )

        def prepare() -> None:
            self.tracker.prepared = {
                "tool": name,
                "exec": exec_id,
                "risk": assessment.risk.value,
                "cancellable": assessment.cancellable,
            }
            self.sequence.commit(name, args, assessment.risk)

        if decision is Decision.CONFIRM:
            seconds = float(registry.settings.permisos.segundos_confirmacion)
            timeout = float(getattr(spec, "timeout_seconds", 0) or 0)
            target = await asyncio.to_thread(self.window_label) if name in WINDOW_BOUND else ""
            grant = self.grants.issue(
                session=self.id, turn=turn, tool=name, args=args, target=target,
                timeout=timeout, ttl_s=seconds + 5,
            )
            record("propuesta")
            try:
                reply = await self.ask_face(
                    "confirm",
                    {
                        "tool": name,
                        "tier": verdict.tier.value,
                        "risk": assessment.risk.value,
                        "summary": summarise(name, args),
                        "warning": assessment.warning,
                        "cancellable": assessment.cancellable,
                        "target": target,
                        "timeout": timeout,
                        "seconds": seconds,
                        "ask": self._answering,
                        "grant": grant.id,
                        "nonce": grant.nonce,
                    },
                    seconds,
                )
            except (RuntimeError, asyncio.CancelledError):
                reply = {}
            if reply.get("approved") is not True:
                record("denegada_por_usuario")
                return (
                    f"El usuario no autorizó '{name}'. No lo intentes de nuevo; confirma en una "
                    "frase que no se ha hecho."
                )
            now_target = (
                await asyncio.to_thread(self.window_label) if name in WINDOW_BOUND else ""
            )
            valid, why = self.grants.redeem(
                str(reply.get("grant") or ""),
                str(reply.get("nonce") or ""),
                session=self.id,
                turn=self.tracker.turn,
                tool=name,
                args=args,
                target=now_target,
                timeout=timeout,
            )
            if not valid:
                record("aprobacion_invalida", motivo=why)
                return (
                    f"La aprobación de '{name}' no es válida ({why}). No se ha hecho nada; "
                    "díselo al usuario."
                )
            record("aprobada")
            prepare()
            return None
        record("permitida")
        prepare()
        return None

    def announcement(self, name: str, spec_confirms: bool) -> str:
        """Cuándo enseñar la herramienta en el HUD: 'ya', 'al_terminar' o 'nunca'.

        Lo que seguro se ejecuta se anuncia al empezar; lo que puede rechazarse
        espera al resultado, para no encender el indicador por algo que no pasó.
        Las herramientas de la propia interfaz no se anuncian: el usuario ya ve
        el efecto.
        """
        verdict = self.registry.policy.evaluate(name)
        if verdict.tier is Tier.INTERFAZ:
            return "nunca"
        if verdict.decision is Decision.ALLOW and not spec_confirms:
            return "ya"
        return "al_terminar"

    # -- turnos --------------------------------------------------------------

    async def stop_turn(self, reason: str = "usuario") -> None:
        """Corta el turno y cancela de verdad lo que esté ejecutándose.

        La cara recibe ``cancel``: ``requested`` al pedirlo, y luego
        ``cancelled`` o ``failed`` (no se pudo, o la acción ya había terminado).
        """
        turn = self._turn
        self._turn = None
        running = self.tracker.running()
        self.tracker.token.cancel()
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        for ex in running:
            ex.cancel_requested = True
            self.registry.audit.event(
                "cancelacion_solicitada", exec=ex.id, sesion=self.id[:8], turno=ex.turn,
                herramienta=ex.tool, cancelable=ex.cancellable, motivo=reason,
            )
            self.send(
                {
                    "type": "cancel",
                    "phase": "requested",
                    "exec": ex.id,
                    "tool": ex.tool,
                    "cancellable": ex.cancellable,
                    "message": "Cancelando…",
                }
            )
        if turn is not None and not turn.done():
            turn.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(turn), timeout=SETTLE_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for ex in running:
            finished = await asyncio.to_thread(ex.done.wait, CANCEL_WAIT_SECONDS)
            if finished and ex.status in ("cancelled", "timed_out"):
                phase, message = "cancelled", "Cancelado."
            elif finished:
                phase = "failed"
                message = "No se pudo cancelar; la acción ya había terminado."
            else:
                phase = "failed"
                message = "No se pudo cancelar; el proceso sigue o requiere intervención."
                self.registry.audit.event(
                    "cancel_failed", exec=ex.id, sesion=self.id[:8], turno=ex.turn,
                    herramienta=ex.tool, motivo="no terminó a tiempo",
                )
            self.send(
                {
                    "type": "cancel",
                    "phase": phase,
                    "exec": ex.id,
                    "tool": ex.tool,
                    "cancellable": ex.cancellable,
                    "message": message,
                }
            )

    async def revoke(self, code: int, reason: str) -> None:
        """Cambió el modo de permisos o se cerró la sesión: nada aprobado antes sigue vivo."""
        await self.stop_turn(reason)
        self.grants.revoke_all()
        close = self._close
        if close is not None:
            try:
                await close(code, reason)
            except Exception:  # noqa: BLE001
                pass

    async def ask(self, text: str, ask_id: str | None) -> None:
        await self.stop_turn()
        self._answering = ask_id
        adapter = self.registry.adapter
        detail = adapter.trigger(text) if adapter is not None else None
        if detail is not None:
            self._turn = asyncio.create_task(self._run_briefing(text, ask_id, detail))
        else:
            self._turn = asyncio.create_task(self._run_turn(text, ask_id))


    # -- informe del día (OpenClawAdapter) -------------------------------------

    async def _run_briefing(self, text: str, ask_id: str | None, detail: str) -> None:
        """El informe no pasa por el modelo: lo hace el flujo declarativo del adaptador."""
        from crisvis.openclaw import AdapterError
        from crisvis.openclaw.tabs import open_tabs

        adapter = self.registry.adapter

        def tagged(frame: dict[str, Any]) -> None:
            self.send({**frame, "ask": ask_id})

        request = adapter.build_request(text, detail=detail)
        self.send(
            {
                "type": "briefing",
                "phase": "start",
                "request": request.model_dump(mode="json"),
                "tabs": adapter.spec.pestanas,
                "connectors": [c.model_dump(mode="json") for c in adapter.connector_statuses()],
            }
        )
        tabs = (
            asyncio.create_task(
                open_tabs(
                    adapter.cfg.abrir_al_informe,
                    interval=adapter.cfg.abrir_intervalo,
                    audit=adapter.audit,
                    session=self.id,
                )
            )
            if adapter.cfg.abrir_al_informe
            else None
        )

        async def on_source(result: Any) -> None:
            self.send({"type": "briefing", "phase": "source", "result": adapter.frame(result)})

        try:
            result = await adapter.daily_briefing(request, session=self.id, on_source=on_source)
        except asyncio.CancelledError:
            if tabs is not None:
                tabs.cancel()
            self.send({"type": "briefing", "phase": "cancelled"})
            self._remember(text, INTERRUPTED_MARK.strip())
            raise
        except AdapterError as exc:
            self.send({"type": "briefing", "phase": "error", "message": str(exc)})
            tagged({"type": "text", "delta": str(exc)})
            tagged({"type": "done", "text": str(exc)})
            return
        except Exception:  # noqa: BLE001
            log.exception("Informe del día fallido")
            message = "No he podido preparar el informe. El detalle está en el registro."
            self.send({"type": "briefing", "phase": "error", "message": message})
            tagged({"type": "error", "message": message})
            return
        self.send({"type": "briefing", "phase": "done", "result": adapter.frame(result)})
        tagged({"type": "text", "delta": result.voice_summary})
        tagged({"type": "done", "text": result.voice_summary})
        self._remember(text, result.voice_summary)

    async def briefing_request(self, msg: dict[str, Any]) -> None:
        """Peticiones de la pestaña: actualizar una fuente o pedir el estado. Solo lectura."""
        from crisvis.openclaw import AdapterError

        adapter = self.registry.adapter
        if adapter is None:
            self.send({"type": "briefing", "phase": "error", "message": "Adaptador desactivado."})
            return
        action = msg.get("action")
        if action == "status":
            overview = await adapter.overview()
            self.send({"type": "briefing", "phase": "status", "overview": overview})
        elif action == "refresh" and isinstance(msg.get("source"), str):
            source = msg["source"]
            try:
                result = await adapter.refresh_source(source, session=self.id)
            except AdapterError as exc:
                self.send(
                    {"type": "briefing", "phase": "error", "source": source, "message": str(exc)}
                )
                return
            self.send({"type": "briefing", "phase": "source", "result": adapter.frame(result)})
        elif action == "audit":
            self.send({"type": "briefing", "phase": "audit", "events": adapter.recent_audit(50)})

    async def propose(self, proposal_id: str) -> None:
        """Una acción propuesta en el informe: política → aprobación exacta → ejecutar o simular."""
        from crisvis.openclaw import AdapterError
        from crisvis.openclaw.policy import Verdict

        adapter = self.registry.adapter
        action = adapter.find_proposal(proposal_id) if adapter is not None else None
        if action is None:
            self.send(
                {
                    "type": "outcome",
                    "proposal": proposal_id,
                    "status": "error",
                    "message": "Esa propuesta ya no existe; actualiza el informe.",
                }
            )
            return
        decision = adapter.policy.evaluate(action, adapter.mode_of(action))
        approval_id = None
        if decision.verdict is Verdict.APPROVE:
            request = adapter.request_approval(action, session=self.id)
            approval_id = request.id
            seconds = float(adapter.cfg.segundos_aprobacion)
            try:
                reply = await self.ask_face(
                    "approval", {"request": json.loads(request.model_dump_json())}, seconds
                )
            except (RuntimeError, asyncio.CancelledError):
                reply = {"approved": False, "fingerprint": request.fingerprint}
            approved = reply.get("approved") is True
            fingerprint = reply.get("fingerprint")
            try:
                adapter.resolve_approval(
                    request.id,
                    approved,
                    fingerprint if isinstance(fingerprint, str) else "",
                    session=self.id,
                )
            except AdapterError as exc:
                self.send(
                    {
                        "type": "outcome",
                        "proposal": proposal_id,
                        "status": "blocked",
                        "message": str(exc),
                    }
                )
                return
            if not approved:
                self.send(
                    {
                        "type": "outcome",
                        "proposal": proposal_id,
                        "status": "denied",
                        "message": "Denegada. No se ha hecho nada.",
                    }
                )
                return
        outcome = adapter.execute(action, approval_id, session=self.id)
        self.send({"type": "outcome", "proposal": proposal_id, **outcome})

    def _remember(self, user: str, assistant: str) -> None:
        self.history.append(("user", user))
        if assistant:
            self.history.append(("assistant", assistant))
        keep = max(2, self.registry.settings.cerebro.historial)
        if len(self.history) > keep:
            self.history = self.history[-keep:]

    async def _run_turn(self, text: str, ask_id: str | None) -> None:
        brain = self.registry.brain
        spoken: list[str] = []
        held: set[str] = set()
        self.tracker.new_turn()
        self.sequence = SequenceGuard()

        def tagged(frame: dict[str, Any]) -> None:
            self.send({**frame, "ask": ask_id})

        try:
            agent = await asyncio.to_thread(
                brain.new_agent,
                extra_tools=self.body,
                visible=self.registry.policy.visible,
                gate=self.gate,
                query=text,
                hooks=self.tracker,
            )
            confirms = {s.name for s in agent.tool_specs if s.requires_confirmation}
            ctx = await asyncio.to_thread(brain.context_for, text, list(self.history))
            async for event in agent.run_stream(text, ctx):
                if isinstance(event, TextDelta):
                    spoken.append(event.text)
                    tagged({"type": "text", "delta": event.text})
                elif isinstance(event, ToolStarted):
                    when = self.announcement(event.name, event.name in confirms)
                    if when == "ya":
                        tagged({"type": "tool", "name": event.name})
                    elif when == "al_terminar":
                        held.add(event.id)
                elif isinstance(event, ToolFinished):
                    if event.id in held:
                        held.discard(event.id)
                        if event.ok:
                            tagged({"type": "tool", "name": event.name})
                elif isinstance(event, Finished):
                    self._remember(text, event.text)
                    tagged({"type": "done", "text": event.text})
        except asyncio.CancelledError:
            partial = "".join(spoken).strip()
            self._remember(text, partial + INTERRUPTED_MARK if partial else "")
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Turno fallido")
            brain.mark_engine_failed(exc)
            message = str(exc) or exc.__class__.__name__
            tagged({"type": "error", "message": message})
            self.registry.broadcast_status()


class SessionRegistry:
    """Sesiones vivas, con caducidad para las que se quedan sin socket."""

    def __init__(
        self,
        settings: Any,
        brain: Any,
        policy: PermissionPolicy,
        audit: AuditLog,
        adapter: Any = None,
    ) -> None:
        self.settings = settings
        self.brain = brain
        self.policy = policy
        self.audit = audit
        # OpenClawAdapter (crisvis.openclaw). None = informe del día desactivado.
        self.adapter = adapter
        # ModeController (crisvis.security.modes); lo fija create_app.
        self.modes: Any = None
        self._sessions: dict[str, Session] = {}
        self._revoking: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        return len(self._sessions)

    @property
    def connections(self) -> int:
        return sum(1 for s in self._sessions.values() if s.connected)

    def create(self, owner: str = "") -> Session:
        self.reap()
        session = Session(self, asyncio.get_running_loop(), owner)
        self._sessions[session.id] = session
        return session

    def on_mode_change(self, before: str, after: str) -> None:
        """Cualquier cambio de modo corta turnos, anula aprobaciones y fuerza a reconectar."""
        reason = f"permisos {before} -> {after}"
        for s in list(self._sessions.values()):
            if not s.connected:
                continue
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is s.loop:
                task = running.create_task(s.revoke(4001, reason))
                self._revoking.add(task)
                task.add_done_callback(self._revoking.discard)
            else:
                asyncio.run_coroutine_threadsafe(s.revoke(4001, reason), s.loop)

    async def revoke_owner(self, owner: str, reason: str) -> None:
        for s in list(self._sessions.values()):
            if s.owner == owner and s.connected:
                await s.revoke(4001, reason)

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def drop(self, session: Session) -> None:
        self._sessions.pop(session.id, None)

    def reap(self) -> None:
        now = time.monotonic()
        for sid, s in list(self._sessions.items()):
            if not s.connected and now - s.last_seen > SESSION_TTL_SECONDS:
                self._sessions.pop(sid, None)

    def status_frame(self, kind: str = "status", **extra: Any) -> dict[str, Any]:
        status = self.brain.status
        return {
            "type": kind,
            "servers": status.servers,
            "brain": status.as_frame(),
            "permissions": self.modes.status() if self.modes else {"mode": self.policy.mode},
            **extra,
        }

    def broadcast_status(self) -> None:
        frame = self.status_frame()
        for s in self._sessions.values():
            if s.connected:
                s.send(frame)

    async def close(self) -> None:
        for s in list(self._sessions.values()):
            await s.detach()
        self._sessions.clear()
