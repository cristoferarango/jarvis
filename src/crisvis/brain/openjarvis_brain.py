"""El cerebro: OpenJarvis embebido como biblioteca.

Aquí, y solo aquí, se toca OpenJarvis: su configuración, el descubrimiento de
motores (Ollama, llama.cpp, vLLM, LM Studio, nube…), sus guardarraíles de
seguridad, la telemetría, el registro de herramientas, la memoria, los
servidores MCP y los archivos de personalidad (SOUL.md / USER.md / MEMORY.md).

El resto de Crisvis habla con esta clase y con los eventos de
``crisvis.brain.events``; no importa nada de OpenJarvis.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crisvis.brain.connectors import Connectors, load_specs, select_mcp_tools, signature
from crisvis.settings import Settings

log = logging.getLogger(__name__)

_NOT_CHAT = re.compile(r"embed|bge-|nomic|rerank|whisper|minilm|clip", re.I)
# Modelos que solo sirven para ver: nunca se eligen solos como modelo de chat.
_VISION_ONLY = re.compile(r"-vl\b|vl:|\dvl|llava|moondream|minicpm-v|vision", re.I)
# Modelo de visión cuando cerebro.modelo_vision está vacío, por preferencia.
_VISION_PREFERENCE = (
    re.compile(r"^qwen3-vl.*instruct", re.I),
    re.compile(r"^qwen2\.5vl|^qwen2\.5-vl", re.I),
    re.compile(r"^qwen3-vl", re.I),
    re.compile(r"llava|minicpm-v|moondream|vision", re.I),
)
_ENGINE_RETRY_SECONDS = 5.0
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]

# Herramientas de OpenJarvis que necesitan algo más que instanciarse.
_MEMORY_TOOLS = frozenset(
    {"memory_store", "memory_search", "memory_retrieve", "memory_index", "retrieval"}
)
_SKIPPED_TOOLS = frozenset({"channel_send", "channel_list", "channel_status", "llm"})

# Motores que corren en este equipo, por orden de preferencia.
LOCAL_ENGINES = (
    "ollama",
    "lmstudio",
    "llamacpp",
    "vllm",
    "sglang",
    "lemonade",
    "mlx",
    "exo",
    "nexa",
    "uzu",
)

# Modelos de Ollama que aceptan `think`. A los demás no se les manda: Ollama
# respondería 400 y OpenJarvis reintentaría quitando las herramientas.
_THINKING_MODELS = re.compile(
    r"^(qwen3|deepseek-r1|deepseek-v3\.1|gpt-oss|magistral|qwq|cogito)", re.IGNORECASE
)


def pick_vision_model(models: Iterable[str]) -> str:
    """El mejor modelo con visión de los instalados, o "" si no hay ninguno."""
    names = [m for m in models if not _NOT_CHAT.search(m)]
    for pattern in _VISION_PREFERENCE:
        for name in names:
            if pattern.search(name):
                return name
    return ""


def _is_local(engine: Any) -> bool:
    from urllib.parse import urlsplit

    host = getattr(engine, "_host", "") or getattr(engine, "_default_host", "") or ""
    if not host:
        return True
    name = urlsplit(host if "://" in host else f"http://{host}").hostname or ""
    return name in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or name.startswith("127.")


OPENJARVIS_DEFAULT_CONFIG = """\
# Configuración del cerebro OpenJarvis que usa Crisvis.
# Referencia completa: https://open-jarvis.github.io/OpenJarvis/

[analytics]
# Crisvis es local: sin analítica de uso hacia fuera.
enabled = false
"""


@dataclass
class BrainStatus:
    ok: bool = False
    engine: str = ""
    model: str = ""
    visionModel: str = ""
    tools: list[str] = field(default_factory=list)
    memory: str = ""
    message: str = ""
    servers: list[str] = field(default_factory=list)
    connectors: list[dict[str, Any]] = field(default_factory=list)
    native: bool = False

    def as_frame(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "engine": self.engine,
            "model": self.model,
            "visionModel": self.visionModel,
            "tools": list(self.tools),
            "memory": self.memory or None,
            "message": self.message,
            "connectors": [dict(c) for c in self.connectors],
        }


class OpenJarvisBrain:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._started = False
        self._last_attempt = 0.0
        self._config: Any = None
        self._bus: Any = None
        self._engine: Any = None
        self._engine_key = ""
        self._model = ""
        self._vision_model = ""
        self._security: Any = None
        self._tools: list[Any] = []
        self._memory: Any = None
        self._memory_name = ""
        self._connectors = Connectors()
        self._mcp_signature = ""
        self._mcp_lock = threading.Lock()
        self._prompt_builder: Any = None
        self._status = BrainStatus(message="El cerebro aún no ha arrancado.")

    # -- arranque ------------------------------------------------------------

    def _ensure_openjarvis_config(self) -> None:
        if self.settings.cerebro.compartir_openjarvis:
            return
        from openjarvis.core.config import DEFAULT_CONFIG_PATH

        if not DEFAULT_CONFIG_PATH.exists():
            DEFAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEFAULT_CONFIG_PATH.write_text(OPENJARVIS_DEFAULT_CONFIG, encoding="utf-8")

    def start(self, *, wait_connectors: bool = False) -> BrainStatus:
        """Carga todo lo que no depende del motor. Bloqueante: llamar en un hilo.

        Las aplicaciones MCP se conectan en segundo plano (algunas tardan en
        arrancar) salvo que se pida ``wait_connectors``.
        """
        with self._lock:
            if self._started:
                return self._status
            self._ensure_openjarvis_config()

            import openjarvis.agents  # noqa: F401  (registra agentes)
            import openjarvis.tools  # noqa: F401  (registra herramientas)
            from openjarvis.core.config import load_config
            from openjarvis.core.events import get_event_bus

            import crisvis.brain.agent  # noqa: F401  (registra crisvis_voz)
            import crisvis.brain.memory  # noqa: F401  (registra crisvis_sqlite)

            self._config = load_config()
            self._config.analytics.enabled = False
            self._bus = get_event_bus()
            self._load_memory()
            self._load_tools()
            self._load_persona()
            self._started = True
        if wait_connectors:
            self.reload_connectors(force=True)
        else:
            threading.Thread(
                target=self.reload_connectors,
                kwargs={"force": True},
                name="crisvis-mcp",
                daemon=True,
            ).start()
        self.connect_engine(force=True)
        return self.status

    def _load_memory(self) -> None:
        if not self.settings.cerebro.memoria:
            return
        import openjarvis.tools.storage  # noqa: F401
        from openjarvis.core.registry import MemoryRegistry

        from crisvis.brain.memory import BACKEND_ID, native_available

        cfg = self._config.memory
        if native_available() and MemoryRegistry.contains(cfg.default_backend):
            try:
                kwargs = {"db_path": cfg.db_path} if cfg.default_backend == "sqlite" else {}
                self._memory = MemoryRegistry.create(cfg.default_backend, **kwargs)
                self._memory_name = f"openjarvis:{cfg.default_backend}"
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("Memoria nativa de OpenJarvis no disponible: %s", exc)
        from openjarvis.core.config import DEFAULT_CONFIG_DIR

        self._memory = MemoryRegistry.create(
            BACKEND_ID, db_path=DEFAULT_CONFIG_DIR / "memoria_crisvis.db"
        )
        self._memory_name = BACKEND_ID

    def _load_tools(self) -> None:
        from openjarvis.core.registry import ToolRegistry

        tools: list[Any] = []
        for name in dict.fromkeys(n.strip() for n in self.settings.cerebro.herramientas):
            if not name or name in _SKIPPED_TOOLS:
                continue
            if not ToolRegistry.contains(name):
                log.warning("Herramienta desconocida en cerebro.herramientas: %s", name)
                continue
            cls = ToolRegistry.get(name)
            if name in _MEMORY_TOOLS:
                if self._memory is None:
                    continue
                tools.append(cls(backend=self._memory))
            else:
                try:
                    tools.append(cls())
                except Exception as exc:  # noqa: BLE001
                    log.warning("No se pudo cargar la herramienta %s: %s", name, exc)
        self._tools = tools

    def _openjarvis_servers(self) -> list[dict[str, Any]]:
        own = getattr(self._config, "tools", None)
        own = getattr(own, "mcp", None)
        if own is None or not getattr(own, "enabled", True):
            return []
        blob = getattr(own, "servers", None)
        if not blob:
            return []
        import json

        try:
            parsed = json.loads(blob) if isinstance(blob, str) else blob
        except (ValueError, TypeError):
            log.warning("tools.mcp.servers de OpenJarvis no es JSON válido; se ignora")
            return []
        return [s for s in parsed if isinstance(s, dict)] if isinstance(parsed, list) else []

    def reload_connectors(self, *, force: bool = False) -> bool:
        """(Re)conecta las aplicaciones MCP si cambió su configuración. Bloqueante.

        Devuelve True si hubo recarga.
        """
        if not self._mcp_lock.acquire(blocking=False):
            return False
        try:
            sig = signature(self.settings)
            if not force and sig == self._mcp_signature:
                return False
            self._mcp_signature = sig
            self._connectors.connect_all(load_specs(self.settings, self._openjarvis_servers()))
            return True
        finally:
            self._mcp_lock.release()

    def connectors_changed(self) -> bool:
        return self._started and signature(self.settings) != self._mcp_signature

    def connector_status(self) -> list[dict[str, Any]]:
        return self._connectors.status()

    def _load_persona(self) -> None:
        try:
            from openjarvis.prompt.builder import SystemPromptBuilder

            self._prompt_builder = SystemPromptBuilder(
                agent_template="",
                memory_files_config=self._config.memory_files,
                system_prompt_config=self._config.system_prompt,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Sin archivos de personalidad de OpenJarvis: %s", exc)

    # -- motor ---------------------------------------------------------------

    def _pick_model(self, engine: Any, wanted: str) -> tuple[str, str]:
        try:
            models = list(engine.list_models())
        except Exception:  # noqa: BLE001
            models = []
        if wanted:
            known = not models or wanted in models or f"{wanted}:latest" in models
            note = (
                ""
                if known
                else (
                    f"El modelo '{wanted}' no aparece en el motor. Descárgalo (p. ej. "
                    f"`ollama pull {wanted}`)."
                )
            )
            return wanted, note
        usable = [m for m in models if not _NOT_CHAT.search(m)]
        chat = [m for m in usable if not _VISION_ONLY.search(m)] or usable
        if chat:
            return chat[0], ""
        return "", "El motor no tiene ningún modelo. Descarga uno, p. ej. `ollama pull qwen3:8b`."

    def _pick_vision(self, engine: Any) -> str:
        if self.settings.cerebro.modelo_vision:
            return self.settings.cerebro.modelo_vision
        if self._engine_key and self._engine_key != "ollama":
            return ""
        try:
            models = list(engine.list_models())
        except Exception:  # noqa: BLE001
            return ""
        return pick_vision_model(models)

    def _find_local_engine(self, wanted: str) -> tuple[str, Any] | None:
        """Como ``get_engine`` de OpenJarvis, pero sin caer nunca a la nube.

        La autodetección de OpenJarvis sustituye un motor caído por cualquier
        otro sano, incluidos los remotos. Un asistente local no debe mandar la
        conversación fuera porque Ollama no haya arrancado todavía: para usar
        la nube hay que pedirla con ``cerebro.motor``.
        """
        from concurrent.futures import ThreadPoolExecutor

        from openjarvis.core.registry import EngineRegistry
        from openjarvis.engine import get_engine

        # Cada motor apagado deja un aviso; aquí probar y fallar es lo normal.
        logging.getLogger("openjarvis.engine._discovery").setLevel(logging.ERROR)
        default = self._config.engine.default
        order = [k for k in LOCAL_ENGINES if EngineRegistry.contains(k)]
        if default in order:
            order.remove(default)
            order.insert(0, default)

        def probe(key: str) -> tuple[str, Any] | None:
            try:
                found = get_engine(self._config, key, model=wanted or None)
            except Exception:  # noqa: BLE001
                return None
            if found is None or not _is_local(found[1]):
                return None
            return found

        with ThreadPoolExecutor(max_workers=len(order) or 1) as pool:
            results = list(pool.map(probe, order))
        return next((r for r in results if r is not None), None)

    def connect_engine(self, force: bool = False) -> bool:
        """Busca un motor sano. Barato de llamar en cada turno: se limita solo."""
        with self._lock:
            if not self._started:
                return False
            if self._engine is not None and self._model:
                return True
            now = time.monotonic()
            if not force and now - self._last_attempt < _ENGINE_RETRY_SECONDS:
                return False
            self._last_attempt = now

            from openjarvis.engine import get_engine
            from openjarvis.security import setup_security
            from openjarvis.telemetry.instrumented_engine import InstrumentedEngine

            from crisvis.brain.memory import native_available

            wanted = self.settings.cerebro.modelo or self._config.intelligence.default_model or ""
            motor = self.settings.cerebro.motor or None
            try:
                if motor:
                    found = get_engine(self._config, motor, model=wanted or None)
                else:
                    found = self._find_local_engine(wanted)
            except Exception as exc:  # noqa: BLE001
                log.warning("Fallo al buscar motor: %s", exc)
                found = None
            if found is None:
                self._status = self._make_status(
                    ok=False,
                    message=(
                        f"No encuentro el motor '{motor}'. ¿Está arrancado?"
                        if motor
                        else "No hay ningún motor de IA local en marcha. Instala y arranca "
                        "Ollama (https://ollama.com) y descarga un modelo: `ollama pull qwen3:8b`."
                    ),
                )
                return False

            key, raw = found
            model, note = self._pick_model(raw, wanted)
            if not model:
                self._status = self._make_status(ok=False, engine=key, message=note)
                return False

            # Sin openjarvis_rust no hay limitador; el doctor ya lo explica, así
            # que el aviso de cada conexión sobra.
            sec_log = logging.getLogger("openjarvis.security")
            level = sec_log.level
            if not native_available():
                sec_log.setLevel(logging.ERROR)
            try:
                self._security = setup_security(self._config, raw, self._bus)
            finally:
                sec_log.setLevel(level)
            self._engine = InstrumentedEngine(self._security.engine, self._bus)
            self._engine_key = key
            self._model = model
            self._vision_model = self._pick_vision(raw)
            self._status = self._make_status(ok=True, engine=key, model=model, message=note)
            self._status.native = native_available()
            log.info("Cerebro listo: motor=%s modelo=%s", key, model)
            return True

    def _make_status(
        self, *, ok: bool, engine: str = "", model: str = "", message: str = ""
    ) -> BrainStatus:
        return BrainStatus(
            ok=ok,
            engine=engine,
            model=model,
            visionModel=self.vision_model,
            tools=[t.spec.name for t in self._tools],
            memory=self._memory_name,
            message=message,
        )

    def mark_engine_failed(self, error: Exception) -> None:
        """Un turno falló por conexión: olvidar el motor y volver a buscarlo."""
        from openjarvis.engine._base import EngineConnectionError

        if isinstance(error, EngineConnectionError):
            with self._lock:
                self._engine = None
                self._model = ""
                self._status = self._make_status(
                    ok=False,
                    engine=self._engine_key,
                    message=f"El motor dejó de responder: {error}",
                )

    @property
    def status(self) -> BrainStatus:
        status = self._status
        status.tools = [t.spec.name for t in [*self._tools, *self._connectors.tools()]]
        status.servers = self._connectors.connected()
        status.connectors = self._connectors.status()
        return status

    @property
    def vision_model(self) -> str:
        return self._vision_model or self.settings.cerebro.modelo_vision

    @property
    def servers(self) -> list[str]:
        return self._connectors.connected()

    @property
    def tools(self) -> list[Any]:
        return [*self._tools, *self._connectors.tools()]

    # -- turnos --------------------------------------------------------------

    def system_prompt(self, tool_names: Iterable[str]) -> str:
        from crisvis.brain.persona import build_system_prompt

        a = self.settings.asistente
        persona = ""
        if self._prompt_builder is not None:
            try:
                persona = self._prompt_builder.persona_sections()
            except Exception:  # noqa: BLE001
                persona = ""
        now = datetime.now()
        when = (
            f"Ahora mismo es {_DIAS[now.weekday()]} {now.day} de {_MESES[now.month - 1]} "
            f"de {now.year}, {now:%H:%M} (hora local)."
        )
        return build_system_prompt(
            nombre=a.nombre,
            idioma=a.idioma,
            tratamiento=a.tratamiento,
            tools=tool_names,
            extra="\n\n".join(p for p in (when, persona) if p),
        )

    def new_agent(
        self,
        *,
        extra_tools: Iterable[Any] = (),
        visible: Callable[[str], bool] = lambda _name: True,
        gate: Any = None,
        query: str = "",
        hooks: Any = None,
    ) -> Any:
        """Un agente para un turno.

        Con ``query`` (la orden del usuario) solo se ofrecen las herramientas de
        aplicaciones conectadas que tienen que ver con ella; sin ella, todas.
        """
        from crisvis.brain.agent import CrisvisVoiceAgent
        from crisvis.brain.persona import language_reminder

        if not self.connect_engine():
            raise BrainUnavailable(self._status.message)
        b = self.settings.cerebro
        remote = [t for t in self._connectors.tools() if visible(t.spec.name)]
        if query:
            remote = select_mcp_tools(query, remote, b.mcp_max_herramientas)
        extra = list(extra_tools)
        # Las herramientas de la sesión sustituyen a las homónimas del cerebro
        # (p. ej. el shell_exec cancelable en lugar del de OpenJarvis).
        own = {t.spec.name for t in extra}
        tools = [
            t for t in [*(t for t in self._tools if t.spec.name not in own), *extra]
            if visible(t.spec.name)
        ]
        taken = {t.spec.name for t in tools}
        tools += [t for t in remote if t.spec.name not in taken]
        names = [t.spec.name for t in tools]
        options: dict[str, Any] = {}
        think: str | None = None
        if self._engine_key == "ollama":
            options["num_ctx"] = b.contexto
            if _THINKING_MODELS.match(self._model or ""):
                think = {"siempre": "siempre", "nunca": "nunca"}.get(b.razonar, "primero")
        return CrisvisVoiceAgent(
            self._engine,
            self._model,
            tools=tools,
            bus=self._bus,
            max_turns=b.max_turnos,
            temperature=b.temperatura,
            max_tokens=b.max_tokens,
            system_prompt=self.system_prompt(names),
            gate=gate,
            hooks=hooks,
            engine_options=options,
            think=think,
            capability_policy=getattr(self._security, "capability_policy", None),
            rate_limiter=getattr(self._security, "rate_limiter", None),
            language_reminder=language_reminder(self.settings.asistente.idioma),
        )

    def context_for(self, text: str, history: Iterable[tuple[str, str]]) -> Any:
        """Historial de la sesión (pares rol/texto) más lo que la memoria sepa."""
        from openjarvis.agents._stubs import AgentContext
        from openjarvis.core.types import Message, Role

        ctx = AgentContext()
        if self._memory is not None and self._config.agent.context_from_memory:
            try:
                from openjarvis.tools.storage.context import ContextConfig, inject_context

                facts: list[Any] = []
                try:
                    from openjarvis.memory import load_configured_facts

                    facts = list(load_configured_facts(self._config))
                except Exception:  # noqa: BLE001
                    facts = []
                cfg = self._config.memory
                for msg in inject_context(
                    text,
                    [],
                    self._memory,
                    config=ContextConfig(
                        top_k=cfg.context_top_k,
                        min_score=cfg.context_min_score,
                        max_context_tokens=min(cfg.context_max_tokens, 1024),
                    ),
                    facts=facts,
                ):
                    ctx.conversation.add(msg)
            except Exception as exc:  # noqa: BLE001
                log.debug("Sin contexto de memoria: %s", exc)
        for role, content in history:
            ctx.conversation.add(
                Message(role=Role.ASSISTANT if role == "assistant" else Role.USER, content=content)
            )
        return ctx

    def see(self, image_b64: str, question: str) -> str:
        """Describe una imagen con el modelo de visión configurado."""
        from openjarvis.core.types import Message, Role

        model = self.vision_model
        if not model:
            raise BrainUnavailable("No hay modelo de visión configurado.")
        if not self.connect_engine():
            raise BrainUnavailable(self._status.message)
        result = self._engine.generate(
            [Message(role=Role.USER, content=question, images=[image_b64])],
            model=model,
            temperature=0.2,
            max_tokens=500,
        )
        return str(result.get("content") or "").strip() or "No distingo nada claro."

    def close(self) -> None:
        self._connectors.close()
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:  # noqa: BLE001
                pass


class BrainUnavailable(RuntimeError):
    pass
