"""Agente de voz de Crisvis, construido sobre las piezas de OpenJarvis.

Los agentes de OpenJarvis devuelven la respuesta cuando ya está entera. Para
voz eso es demasiado tarde: la interfaz empieza a hablar con la primera frase.
Este agente hace el mismo bucle de herramientas que ``native_openhands`` pero
en streaming (``engine.stream_full``), y delega cada ejecución en el
``ToolExecutor`` de OpenJarvis, así que conserva RBAC, límites de frecuencia,
timeouts, eventos del bus y detección de taint.

Antes de ejecutar nada pasa por ``gate``: la política de permisos de Crisvis,
que puede pedir confirmación al usuario por voz o con un clic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from openjarvis.agents._stubs import AgentContext, AgentResult, ToolUsingAgent
from openjarvis.core.events import EventType
from openjarvis.core.registry import AgentRegistry
from openjarvis.core.types import Message, Role, ToolCall, ToolResult
from openjarvis.tools._stubs import ToolSpec

from crisvis.brain.events import BrainEvent, Finished, TextDelta, ToolFinished, ToolStarted

log = logging.getLogger(__name__)

# Devuelve None si la herramienta puede ejecutarse, o el motivo de la negativa.
Gate = Callable[[str, dict[str, Any], ToolSpec], Awaitable[str | None]]

AGENT_ID = "crisvis_voz"
MAX_TURNS_TEXT = "He dado demasiadas vueltas sin llegar a una respuesta, señor. ¿Lo replanteamos?"


class ThinkFilter:
    """Quita los bloques <think>…</think> de un flujo de texto troceado.

    Algunos modelos razonan en voz alta dentro del contenido; eso nunca debe
    llegar al sintetizador de voz.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    @staticmethod
    def _partial(text: str, tag: str) -> int:
        for n in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:n]):
                return n
        return 0

    def feed(self, text: str) -> str:
        self._buf += text
        out: list[str] = []
        while self._buf:
            if self._inside:
                i = self._buf.find(self.CLOSE)
                if i < 0:
                    self._buf = self._buf[-(len(self.CLOSE) - 1) :]
                    break
                self._buf = self._buf[i + len(self.CLOSE) :].lstrip()
                self._inside = False
                continue
            i = self._buf.find(self.OPEN)
            if i >= 0:
                out.append(self._buf[:i])
                self._buf = self._buf[i + len(self.OPEN) :]
                self._inside = True
                continue
            hold = self._partial(self._buf, self.OPEN)
            out.append(self._buf[: len(self._buf) - hold])
            self._buf = self._buf[len(self._buf) - hold :]
            break
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = ("" if self._inside else self._buf), ""
        return rest


def merge_tool_fragments(acc: list[dict[str, Any]], fragments: list[dict[str, Any]]) -> None:
    """Junta fragmentos de tool_calls estilo OpenAI (o completos, estilo Ollama)."""
    for frag in fragments:
        fn = frag.get("function") or {}
        name = fn.get("name") or ""
        index = frag.get("index", len(acc))
        current = next((c for c in acc if c["index"] == index and not c["closed"]), None)
        if current is None or (name and current["name"]):
            if current is not None:
                current["closed"] = True
            current = {"index": index, "id": "", "name": "", "arguments": "", "closed": False}
            acc.append(current)
        if frag.get("id"):
            current["id"] = frag["id"]
        if name:
            current["name"] = name
        args = fn.get("arguments")
        if isinstance(args, dict):
            current["arguments"] = json.dumps(args, ensure_ascii=False)
        elif args:
            current["arguments"] += str(args)


@AgentRegistry.register(AGENT_ID)
class CrisvisVoiceAgent(ToolUsingAgent):
    agent_id = AGENT_ID
    _default_max_turns = 8
    _default_temperature = 0.6
    _default_max_tokens = 800

    def __init__(
        self,
        engine: Any,
        model: str,
        *,
        tools: list[Any] | None = None,
        bus: Any = None,
        max_turns: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_prompt: str | None = None,
        gate: Gate | None = None,
        engine_options: dict[str, Any] | None = None,
        think: str | None = None,
        capability_policy: Any = None,
        rate_limiter: Any = None,
        **_: Any,
    ) -> None:
        super().__init__(
            engine,
            model,
            tools=tools,
            bus=bus,
            max_turns=max_turns if max_turns is not None else self._default_max_turns,
            temperature=temperature if temperature is not None else self._default_temperature,
            max_tokens=max_tokens if max_tokens is not None else self._default_max_tokens,
            capability_policy=capability_policy,
            rate_limiter=rate_limiter,
            # La confirmación real ya la hizo ``gate``; el executor solo
            # comprueba que exista alguien que confirme.
            interactive=True,
            confirm_callback=lambda _prompt: True,
        )
        self._system_prompt = system_prompt
        self._gate = gate
        self._engine_options = dict(engine_options or {})
        # None = no tocar; "primero" = razonar solo al decidir qué herramienta
        # usar; "siempre" / "nunca".
        self._think = think

    @property
    def tool_specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools]

    # -- API síncrona exigida por BaseAgent (CLI, SDK de OpenJarvis) ---------

    def run(self, input: str, context: AgentContext | None = None, **kwargs: Any) -> AgentResult:
        async def collect() -> AgentResult:
            final: Finished | None = None
            async for event in self.run_stream(input, context):
                if isinstance(event, Finished):
                    final = event
            assert final is not None
            return AgentResult(content=final.text, turns=final.turns)

        return asyncio.run(collect())

    # -- bucle en streaming --------------------------------------------------

    def _call_kwargs(self, tools: list[dict[str, Any]], turn: int = 1) -> dict[str, Any]:
        kwargs = {k: v for k, v in self._engine_options.items() if v is not None}
        if tools:
            kwargs["tools"] = tools
        if self._think == "siempre":
            kwargs["think"] = True
        elif self._think == "nunca":
            kwargs["think"] = False
        elif self._think == "primero":
            # Los modelos pequeños sin razonar contestan "lo he guardado" sin
            # llamar a la herramienta. Razonar cuesta segundos, así que solo se
            # paga al decidir; con los resultados delante basta con redactar.
            kwargs["think"] = bool(tools) and turn == 1
        return kwargs

    async def _execute(self, call: ToolCall, taint: Any) -> ToolResult:
        tool = self._executor._tools.get(call.name)
        if tool is None:
            return ToolResult(call.name, f"No existe la herramienta '{call.name}'.", success=False)
        try:
            args = json.loads(call.arguments) if call.arguments else {}
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        if taint:
            from openjarvis.security.taint import check_taint

            violation = check_taint(call.name, taint)
            if violation:
                if self._bus:
                    self._bus.publish(
                        EventType.TAINT_VIOLATION, {"tool": call.name, "violation": violation}
                    )
                return ToolResult(call.name, f"Bloqueado por seguridad: {violation}", success=False)

        if self._gate is not None:
            refusal = await self._gate(call.name, args, tool.spec)
            if refusal:
                return ToolResult(call.name, refusal, success=False)

        return await asyncio.to_thread(self._executor.execute, call)

    async def run_stream(
        self, input: str, context: AgentContext | None = None
    ) -> AsyncIterator[BrainEvent]:
        self._emit_turn_start(input)
        messages = self._build_messages(input, context, system_prompt=self._system_prompt)
        tools = self._executor.get_openai_tools()
        used: list[str] = []
        spoken: list[str] = []
        taint: Any = None

        for turn in range(1, self._max_turns + 1):
            think = ThinkFilter()
            content: list[str] = []
            fragments: list[dict[str, Any]] = []

            async for chunk in self._engine.stream_full(
                messages,
                model=self._model,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                **self._call_kwargs(tools, turn),
            ):
                if chunk.content:
                    content.append(chunk.content)
                    visible = think.feed(chunk.content)
                    if visible:
                        spoken.append(visible)
                        yield TextDelta(visible)
                if chunk.tool_calls:
                    merge_tool_fragments(fragments, chunk.tool_calls)
            tail = think.flush()
            if tail:
                spoken.append(tail)
                yield TextDelta(tail)

            calls = [
                ToolCall(
                    id=f["id"] or f"call_{turn}_{i}",
                    name=f["name"],
                    arguments=f["arguments"] or "{}",
                )
                for i, f in enumerate(fragments)
                if f["name"]
            ]
            raw = "".join(content)
            if not calls:
                self._emit_turn_end(turns=turn)
                yield Finished(
                    text=self._strip_think_tags("".join(spoken)), turns=turn, tools=tuple(used)
                )
                return

            messages.append(
                Message(role=Role.ASSISTANT, content=self._strip_think_tags(raw), tool_calls=calls)
            )
            for call in calls:
                try:
                    args = json.loads(call.arguments)
                except json.JSONDecodeError:
                    args = {}
                yield ToolStarted(call.id, call.name, args if isinstance(args, dict) else {})
                result = await self._execute(call, taint)
                used.append(call.name)
                found = result.metadata.get("_taint") if result.metadata else None
                if found:
                    taint = found if taint is None else taint.union(found)
                yield ToolFinished(call.id, call.name, result.success)
                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=str(result.content),
                        tool_call_id=call.id,
                        name=call.name,
                    )
                )

        self._emit_turn_end(turns=self._max_turns, max_turns_exceeded=True)
        text = self._strip_think_tags("".join(spoken)) or MAX_TURNS_TEXT
        yield Finished(text=text, turns=self._max_turns, tools=tuple(used), truncated=True)
