from typing import Any

from openjarvis.core.types import ToolResult
from openjarvis.engine._stubs import StreamChunk
from openjarvis.tools._stubs import BaseTool, ToolSpec

from crisvis.brain.agent import (
    MAX_TURNS_TEXT,
    CrisvisVoiceAgent,
    ThinkFilter,
    merge_tool_fragments,
)
from crisvis.brain.events import Finished, TextDelta, ToolFinished, ToolStarted


class FakeEngine:
    """Devuelve, turno a turno, las listas de StreamChunk que se le den."""

    engine_id = "fake"

    def __init__(self, turns: list[list[StreamChunk]]) -> None:
        self.turns = list(turns)
        self.calls: list[dict[str, Any]] = []

    async def stream_full(self, messages, *, model, temperature, max_tokens, **kw):
        self.calls.append({"messages": list(messages), **kw})
        for chunk in self.turns.pop(0) if self.turns else [StreamChunk(content="fin")]:
            yield chunk


class Echo(BaseTool):
    tool_id = "echo"

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="echo",
            description="Repite el texto.",
            parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        )

    def execute(self, **params: Any) -> ToolResult:
        self.runs.append(params)
        return ToolResult("echo", f"eco: {params.get('text', '')}", success=True)


def call(name: str, args: dict[str, Any], index: int = 0) -> StreamChunk:
    return StreamChunk(
        tool_calls=[
            {"index": index, "id": f"c{index}", "function": {"name": name, "arguments": args}}
        ]
    )


async def collect(agent: CrisvisVoiceAgent, text: str = "hola") -> list[Any]:
    return [e async for e in agent.run_stream(text)]


def test_think_filter_across_chunks() -> None:
    f = ThinkFilter()
    out = f.feed("Hola <thi") + f.feed("nk>razono</th") + f.feed("ink> mundo") + f.flush()
    assert out == "Hola mundo"


def test_think_filter_unclosed_block_is_dropped() -> None:
    f = ThinkFilter()
    assert f.feed("<think>nunca acaba") + f.flush() == ""


def test_merge_openai_style_fragments() -> None:
    acc: list[dict[str, Any]] = []
    merge_tool_fragments(
        acc, [{"index": 0, "id": "a", "function": {"name": "echo", "arguments": '{"te'}}]
    )
    merge_tool_fragments(acc, [{"index": 0, "function": {"arguments": 'xt": "x"}'}}])
    assert acc[0]["name"] == "echo" and acc[0]["arguments"] == '{"text": "x"}'


def test_merge_ollama_style_restarting_index() -> None:
    acc: list[dict[str, Any]] = []
    merge_tool_fragments(acc, [{"index": 0, "function": {"name": "a", "arguments": {}}}])
    merge_tool_fragments(acc, [{"index": 0, "function": {"name": "b", "arguments": {"k": 1}}}])
    assert [c["name"] for c in acc] == ["a", "b"]


async def test_streams_text_without_tools() -> None:
    engine = FakeEngine(
        [[StreamChunk(content="<think>x</think>Buenas "), StreamChunk(content="tardes")]]
    )
    agent = CrisvisVoiceAgent(engine, "m", tools=[])
    events = await collect(agent)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Buenas tardes"
    done = events[-1]
    assert isinstance(done, Finished) and done.text == "Buenas tardes" and done.turns == 1


async def test_tool_loop_goes_through_gate() -> None:
    tool = Echo()
    engine = FakeEngine([[call("echo", {"text": "hola"})], [StreamChunk(content="Hecho.")]])
    seen: list[str] = []

    async def gate(name, args, spec):
        seen.append(name)
        return None

    agent = CrisvisVoiceAgent(engine, "m", tools=[tool], gate=gate)
    events = await collect(agent)
    assert seen == ["echo"]
    assert tool.runs == [{"text": "hola"}]
    assert any(isinstance(e, ToolStarted) and e.name == "echo" for e in events)
    assert any(isinstance(e, ToolFinished) and e.ok for e in events)
    # El resultado de la herramienta vuelve al modelo en el segundo turno.
    second = engine.calls[1]["messages"]
    assert any("eco: hola" in str(m.content) for m in second)
    assert events[-1].text == "Hecho."


async def test_gate_refusal_is_reported_to_model() -> None:
    tool = Echo()
    engine = FakeEngine([[call("echo", {"text": "x"})], [StreamChunk(content="Entendido.")]])

    async def gate(name, args, spec):
        return "El usuario no lo autorizó."

    agent = CrisvisVoiceAgent(engine, "m", tools=[tool], gate=gate)
    events = await collect(agent)
    assert tool.runs == []
    assert any(isinstance(e, ToolFinished) and not e.ok for e in events)
    assert any("no lo autorizó" in str(m.content) for m in engine.calls[1]["messages"])


async def test_unknown_tool_does_not_crash() -> None:
    engine = FakeEngine([[call("no_existe", {})], [StreamChunk(content="Vale.")]])
    agent = CrisvisVoiceAgent(engine, "m", tools=[Echo()])
    events = await collect(agent)
    assert events[-1].text == "Vale."


async def test_think_only_when_choosing_tools() -> None:
    engine = FakeEngine([[call("echo", {"text": "x"})], [StreamChunk(content="Listo.")]])
    agent = CrisvisVoiceAgent(engine, "m", tools=[Echo()], think="primero")
    await collect(agent)
    assert [c.get("think") for c in engine.calls] == [True, False]


async def test_think_untouched_by_default() -> None:
    engine = FakeEngine([[StreamChunk(content="Hola.")]])
    agent = CrisvisVoiceAgent(engine, "m", tools=[Echo()])
    await collect(agent)
    assert "think" not in engine.calls[0]


async def test_max_turns_is_bounded() -> None:
    engine = FakeEngine([[call("echo", {"text": str(i)})] for i in range(10)])
    agent = CrisvisVoiceAgent(engine, "m", tools=[Echo()], max_turns=3)
    events = await collect(agent)
    done = events[-1]
    assert isinstance(done, Finished) and done.truncated and done.text == MAX_TURNS_TEXT
    assert len(engine.calls) == 3
