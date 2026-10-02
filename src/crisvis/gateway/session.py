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
from crisvis.brain.events import Finished, TextDelta, ToolFinished, ToolStarted
from crisvis.security import AuditLog, Decision, PermissionPolicy, Tier

log = logging.getLogger(__name__)

T = TypeVar("T")
Send = Callable[[dict[str, Any]], Awaitable[None]]

SESSION_TTL_SECONDS = 15 * 60
SETTLE_SECONDS = 0.4
INTERRUPTED_MARK = " [interrumpido]"


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
        "pc_window": lambda: f"{arg('action')} la ventana {arg('window')}",
        "pc_click": lambda: f"pulsar {arg('target') or arg('element') or arg('x')}",
        "pc_type": lambda: f"escribir «{arg('text')}»",
        "pc_keys": lambda: f"pulsar las teclas {arg('keys')}",
        "pc_scroll": lambda: f"desplazar hacia {arg('direction') or 'abajo'}",
        "pc_file_manage": lambda: f"{arg('action')} {arg('path')} {arg('dest')}".strip(),
    }
    if tool in pc:
        return f"Controlar el PC: {pc[tool]()} (y el resto de pasos de esta orden)"
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
    def __init__(self, registry: SessionRegistry, loop: asyncio.AbstractEventLoop) -> None:
        self.id = secrets.token_urlsafe(18)
        self.registry = registry
        self.loop = loop
        self.history: list[tuple[str, str]] = []
        self.last_seen = time.monotonic()
        self._send: Send | None = None
        self._outbox: asyncio.Queue[dict[str, Any] | None] | None = None
        self._writer: asyncio.Task[None] | None = None
        self._turn: asyncio.Task[None] | None = None
        self._answering: str | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._seq = 0
        self._pc_granted = False
        settings = registry.settings
        self.body = build_body_tools(
            self,
            interface=settings.cerebro.herramientas_interfaz,
            camera=settings.cerebro.herramientas_camara,
            see=registry.brain.see
            if getattr(registry.brain, "vision_model", settings.cerebro.modelo_vision)
            else None,
            pc=settings.cerebro.herramientas_pc,
        )

    # -- transporte ----------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._send is not None

    async def attach(self, send: Send) -> None:
        await self.detach()
        self._send = send
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
        await self.stop_turn()
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
        policy: PermissionPolicy = self.registry.policy
        audit: AuditLog = self.registry.audit
        verdict = policy.evaluate(name)
        decision = verdict.decision
        # Si OpenJarvis marca la herramienta como sensible, se pregunta siempre.
        if (
            decision is Decision.ALLOW
            and getattr(spec, "requires_confirmation", False)
            and verdict.tier is not Tier.INTERFAZ
        ):
            decision = Decision.CONFIRM
        # Una tarea en el PC son muchos clics y teclas seguidos: el "sí" a la
        # primera acción de escritura vale para las demás de la misma orden.
        # Lo peligroso (apagar, matar procesos) se sigue preguntando una a una.
        pc_write = name.startswith("pc_") and verdict.tier is Tier.ESCRITURA
        if decision is Decision.CONFIRM and pc_write and self._pc_granted:
            decision = Decision.ALLOW

        def record(outcome: str) -> None:
            if verdict.tier is not Tier.INTERFAZ:
                audit.record(
                    session=self.id[:8],
                    tool=name,
                    tier=verdict.tier.value,
                    decision=decision.value,
                    outcome=outcome,
                    args=args,
                )

        if decision is Decision.DENY:
            record("denegada")
            return (
                f"Bloqueado: la herramienta '{name}' no está permitida en el modo de permisos "
                f"actual ({policy.mode}). Díselo al usuario en una frase; puede cambiar el modo "
                "en la interfaz."
            )
        if decision is Decision.CONFIRM:
            seconds = self.registry.settings.permisos.segundos_confirmacion
            try:
                reply = await self.ask_face(
                    "confirm",
                    {
                        "tool": name,
                        "tier": verdict.tier.value,
                        "summary": summarise(name, args),
                        "seconds": seconds,
                        "ask": self._answering,
                    },
                    seconds,
                )
                approved = reply.get("approved") is True
            except RuntimeError:
                approved = False
            if not approved:
                record("rechazada")
                return (
                    f"El usuario no autorizó '{name}'. No lo intentes de nuevo; confirma en una "
                    "frase que no se ha hecho."
                )
            record("aprobada")
            if pc_write:
                self._pc_granted = True
            return None
        record("permitida")
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

    async def stop_turn(self) -> None:
        turn = self._turn
        self._turn = None
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.cancel()
        if turn is None or turn.done():
            return
        turn.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(turn), timeout=SETTLE_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    async def ask(self, text: str, ask_id: str | None) -> None:
        await self.stop_turn()
        self._answering = ask_id
        self._turn = asyncio.create_task(self._run_turn(text, ask_id))

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
        self._pc_granted = False

        def tagged(frame: dict[str, Any]) -> None:
            self.send({**frame, "ask": ask_id})

        try:
            agent = await asyncio.to_thread(
                brain.new_agent,
                extra_tools=self.body,
                visible=self.registry.policy.visible,
                gate=self.gate,
                query=text,
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
        self, settings: Any, brain: Any, policy: PermissionPolicy, audit: AuditLog
    ) -> None:
        self.settings = settings
        self.brain = brain
        self.policy = policy
        self.audit = audit
        self._sessions: dict[str, Session] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def create(self) -> Session:
        self.reap()
        session = Session(self, asyncio.get_running_loop())
        self._sessions[session.id] = session
        return session

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
            "permissions": {"mode": self.policy.mode},
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
