"""Conectores MCP: otras aplicaciones como herramientas del cerebro.

Los servidores se declaran en ``~/.crisvis/mcp.json`` con el formato estándar
que usan Claude Desktop, Cursor y la documentación de cada servidor::

    {"mcpServers": {
        "github": {"url": "https://api.githubcopilot.com/mcp/",
                   "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}},
        "notion": {"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
                   "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"}}
    }}

(y también en ``[[cerebro.mcp]]`` de crisvis.toml). ``${VAR}`` se lee del
entorno o del .env, para no dejar claves en el archivo.

Sobre el transporte y el adaptador de OpenJarvis se añade lo que faltaba:
variables de entorno, cabeceras HTTP, ``npx``/``uvx`` en Windows sin ventanas
de consola, nombres ``mcp__servidor__herramienta`` (la política de permisos
clasifica por el verbo de la herramienta), conexión en paralelo con límite de
tiempo y recarga en caliente cuando cambia el archivo.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crisvis.body.desktop import normalize
from crisvis.settings import Settings

log = logging.getLogger(__name__)

CONNECT_SECONDS = 60.0
_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME_CHARS = re.compile(r"[^a-z0-9_-]+")
_REMOTE_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


# -- especificación -------------------------------------------------------------


@dataclass
class ServerSpec:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    disabled: bool = False
    source: str = ""
    # Para expandir ${VAR}: el entorno más <home>/.env (leído en cada recarga).
    variables: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def slug(self) -> str:
        return _NAME_CHARS.sub("_", self.name.lower()).strip("_")[:20] or "mcp"

    def _lookup(self, name: str) -> str:
        return self.variables.get(name) or os.environ.get(name, "")

    def expand(self, value: str) -> str:
        return _ENV_REF.sub(lambda m: self._lookup(m.group(1)), value)

    def missing_env(self) -> list[str]:
        """Variables ${X} que se usan y no están definidas."""
        blobs = [self.url, self.command, *self.args, *self.env.values(), *self.headers.values()]
        refs = {m for b in blobs for m in _ENV_REF.findall(b)}
        return sorted(r for r in refs if not self._lookup(r))


def secrets_path(settings: Settings) -> Path:
    return settings.home / ".env"


def read_env(path: Path) -> dict[str, str]:
    """CLAVE=valor de un .env, sin tocar os.environ."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def set_secret(settings: Settings, key: str, value: str) -> None:
    """Guarda o cambia CLAVE=valor en <home>/.env, conservando el resto."""
    path = secrets_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    out, done = [], False
    for line in lines:
        name = line.partition("=")[0].strip().removeprefix("export ").strip()
        if name == key and "=" in line and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def parse_server(name: str, raw: dict[str, Any], source: str = "") -> ServerSpec:
    """Una entrada de mcpServers (o de [[cerebro.mcp]]) a ServerSpec."""
    headers = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}
    token = raw.get("token")
    if token and not any(k.lower() == "authorization" for k in headers):
        headers["Authorization"] = f"Bearer {token}"
    return ServerSpec(
        name=str(raw.get("name") or name),
        command=str(raw.get("command") or ""),
        args=[str(a) for a in raw.get("args") or []],
        env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
        url=str(raw.get("url") or raw.get("serverUrl") or ""),
        headers=headers,
        include=[str(t) for t in raw.get("include_tools") or raw.get("includeTools") or []],
        exclude=[str(t) for t in raw.get("exclude_tools") or raw.get("excludeTools") or []],
        disabled=bool(raw.get("disabled", False)),
        source=source,
    )


def config_path(settings: Settings) -> Path:
    return settings.home / "mcp.json"


def read_file(path: Path) -> dict[str, dict[str, Any]]:
    """El bloque mcpServers de un archivo; vacío si no existe o no se entiende."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer %s: %s", path, exc)
        return {}
    servers = data.get("mcpServers", data.get("servers", {})) if isinstance(data, dict) else {}
    if not isinstance(servers, dict):
        return {}
    return {k: v for k, v in servers.items() if isinstance(v, dict)}


def write_file(path: Path, servers: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    data["mcpServers"] = servers
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_specs(settings: Settings, extra: Iterable[dict[str, Any]] = ()) -> list[ServerSpec]:
    specs: dict[str, ServerSpec] = {}
    for i, raw in enumerate(extra):
        spec = parse_server(str(raw.get("name") or f"openjarvis{i}"), raw, "openjarvis")
        specs[spec.name] = spec
    for raw in settings.cerebro.mcp:
        spec = parse_server(str(raw.get("name") or "mcp"), raw, "crisvis.toml")
        specs[spec.name] = spec
    for name, raw in read_file(config_path(settings)).items():
        specs[name] = parse_server(name, raw, "mcp.json")
    secrets = read_env(secrets_path(settings))
    for spec in specs.values():
        spec.variables = secrets
    return list(specs.values())


def _stamp(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return "-"
    return f"{st.st_mtime_ns}:{st.st_size}"


def signature(settings: Settings) -> str:
    """Cambia cuando cambia la configuración de conectores (para recargar)."""
    files = f"{_stamp(config_path(settings))}|{_stamp(secrets_path(settings))}"
    return f"{files}|{json.dumps(settings.cerebro.mcp, sort_keys=True, default=str)}"


# -- importar de otras aplicaciones ----------------------------------------------


def import_sources() -> dict[str, Path]:
    """Dónde guardan sus servidores MCP otras aplicaciones de este equipo."""
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    return {
        "claude": appdata / "Claude" / "claude_desktop_config.json"
        if sys.platform == "win32"
        else home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        "cursor": home / ".cursor" / "mcp.json",
        "vscode": appdata / "Code" / "User" / "mcp.json",
        "windsurf": home / ".codeium" / "windsurf" / "mcp_config.json",
    }


# -- transportes ------------------------------------------------------------------


def _resolve_command(command: str) -> str:
    """'npx' -> ruta completa a npx.cmd en Windows (Popen no busca .cmd sin shell)."""
    if os.path.isabs(command):
        return command
    found = shutil.which(command)
    return found or command


def _stdio_transport(spec: ServerSpec) -> Any:
    from openjarvis.mcp.transport import StdioTransport

    env = {**os.environ, **{k: spec.expand(v) for k, v in spec.env.items()}}
    command = [_resolve_command(spec.expand(spec.command)), *(spec.expand(a) for a in spec.args)]

    class Transport(StdioTransport):
        def _start(self) -> None:  # noqa: D401 - igual que el original, con env y sin consola
            self._process = subprocess.Popen(
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=0x08000000 if sys.platform == "win32" else 0,
            )
            self._reader_thread = threading.Thread(
                target=self._read_stdout, name=f"mcp-{spec.slug}-stdout", daemon=True
            )
            self._reader_thread.start()
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(self._process,),
                name=f"mcp-{spec.slug}-stderr",
                daemon=True,
            )
            self._stderr_thread.start()

    return Transport(command, response_timeout=120.0)


def _http_transport(spec: ServerSpec) -> Any:
    from openjarvis.mcp.transport import StreamableHTTPTransport

    headers = {k: spec.expand(v) for k, v in spec.headers.items()}

    class Transport(StreamableHTTPTransport):
        def _build_headers(self) -> dict:
            return {**super()._build_headers(), **{k: v for k, v in headers.items() if v}}

    return Transport(url=spec.expand(spec.url), request_timeout=120.0)


# -- herramientas -----------------------------------------------------------------


def _clean_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    clean = {k: v for k, v in schema.items() if k not in ("$schema", "$id")}
    clean.setdefault("type", "object")
    clean.setdefault("properties", {})
    return clean


def tool_name(server: ServerSpec, remote: str) -> str:
    return f"mcp__{server.slug}__{_REMOTE_CHARS.sub('_', remote)}"[:64]


def _wrap(client: Any, original: Any, server: ServerSpec) -> Any:
    """La herramienta remota con nombre mcp__servidor__herramienta."""
    from openjarvis.core.types import ToolResult
    from openjarvis.tools._stubs import BaseTool, ToolSpec

    local_name = tool_name(server, original.name)
    parameters = _clean_schema(original.parameters)
    description = f"[{server.name}] {original.description or ''}".strip()

    class Remote(BaseTool):
        tool_id = "mcp_adapter"
        connector = server.name
        remote_name = original.name

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec(
                name=local_name,
                description=description[:600],
                parameters=parameters,
                category=f"mcp:{server.slug}",
                timeout_seconds=120,
            )

        def execute(self, **params: Any) -> ToolResult:
            try:
                result = client.call_tool(original.name, params)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(local_name, f"Error del conector {server.name}: {exc}", False)
            parts = result.get("content", []) if isinstance(result, dict) else []
            text = "\n".join(
                str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("text")
            ) or "(sin texto)"
            failed = bool(result.get("isError")) if isinstance(result, dict) else False
            return ToolResult(local_name, text[:8000], not failed)

    return Remote()


@dataclass
class Connection:
    spec: ServerSpec
    state: str = "pendiente"  # pendiente | conectando | conectado | error | desactivado
    error: str = ""
    tools: list[Any] = field(default_factory=list)
    client: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "state": self.state,
            "tools": len(self.tools),
            "error": self.error,
            "source": self.spec.source,
        }


def connect(conn: Connection) -> Connection:
    """Bloqueante: arranca o contacta el servidor y descubre sus herramientas.

    ``conn.client`` se rellena en cuanto existe, para que quien espera pueda
    cerrarlo (y así desbloquear la lectura) si el servidor no contesta.
    """
    spec = conn.spec
    if spec.disabled:
        conn.state = "desactivado"
        return conn
    missing = spec.missing_env()
    if missing:
        conn.state, conn.error = "error", f"faltan variables: {', '.join(missing)}"
        return conn
    if not spec.url and not spec.command:
        conn.state, conn.error = "error", "sin 'command' ni 'url'"
        return conn
    from openjarvis.mcp.client import MCPClient

    conn.state = "conectando"
    try:
        transport = _http_transport(spec) if spec.url else _stdio_transport(spec)
        conn.client = MCPClient(transport)
        conn.client.initialize()
        remote = conn.client.list_tools()
    except FileNotFoundError:
        _close(conn)
        conn.state, conn.error = "error", f"no se encontró el programa '{spec.command}'"
        return conn
    except Exception as exc:  # noqa: BLE001
        _close(conn)
        if conn.state == "conectando":
            conn.state, conn.error = "error", str(exc)[:300] or type(exc).__name__
        return conn
    if conn.state != "conectando":
        return conn
    if spec.include:
        remote = [t for t in remote if t.name in spec.include]
    if spec.exclude:
        remote = [t for t in remote if t.name not in spec.exclude]
    conn.tools = [_wrap(conn.client, t, spec) for t in remote]
    conn.state = "conectado"
    return conn


class Connectors:
    """Todos los conectores: conexión en paralelo, estado, cierre y recarga."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[str, Connection] = {}
        self._connecting: dict[str, Connection] = {}

    def connect_all(self, specs: list[ServerSpec], timeout: float = CONNECT_SECONDS) -> None:
        """Conecta todos en paralelo; los que tardan más de ``timeout`` quedan en error.

        Mientras tanto siguen funcionando los conectores anteriores: se
        sustituyen de golpe cuando la nueva tanda termina.
        """
        if not specs:
            self.close()
            return
        pending = {s.name: Connection(s, state="conectando") for s in specs}
        with self._lock:
            self._connecting = pending
        pool = ThreadPoolExecutor(max_workers=min(8, len(specs)), thread_name_prefix="mcp")
        try:
            futures = {pool.submit(connect, c): c for c in pending.values()}
            _done, not_done = wait(futures, timeout=timeout)
            for fut in not_done:
                conn = futures[fut]
                conn.state, conn.error = "error", f"no respondió en {int(timeout)} s"
                conn.tools = []
                _close(conn)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for conn in pending.values():
            if conn.state == "conectado":
                log.info("Conector %s: %d herramientas", conn.spec.name, len(conn.tools))
            elif conn.state == "error":
                log.warning("Conector %s no disponible: %s", conn.spec.name, conn.error)
        with self._lock:
            old, self._connections = self._connections, pending
            self._connecting = {}
        for conn in old.values():
            _close(conn)

    def tools(self) -> list[Any]:
        with self._lock:
            return [t for c in self._connections.values() for t in c.tools]

    def connected(self) -> list[str]:
        with self._lock:
            return [n for n, c in self._connections.items() if c.state == "conectado"]

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            shown = {**self._connections, **self._connecting}
            return [c.as_dict() for c in shown.values()]

    def close(self) -> None:
        with self._lock:
            old, self._connections = self._connections, {}
        for conn in old.values():
            _close(conn)


def _close(conn: Connection) -> None:
    if conn.client is not None:
        try:
            conn.client.close()
        except Exception:  # noqa: BLE001
            pass
        conn.client = None


# -- qué herramientas ofrecer en cada orden -----------------------------------------

_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {
    "the", "and", "for", "with", "una", "uno", "los", "las", "del", "que", "por", "para",
    "con", "este", "esta", "eso", "esto", "mis", "tus", "sus", "como", "pero", "más",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(normalize(text)) if w not in _STOP}


def select_mcp_tools(query: str, tools: list[Any], limit: int) -> list[Any]:
    """Las herramientas MCP más relevantes para la orden, como mucho ``limit``.

    Un modelo local de 8B no puede cargar con las 40 herramientas de GitHub más
    las de Notion más las suyas: se ofrecen las del conector que se nombra y
    las que comparten palabras con la petición.
    """
    if len(tools) <= limit:
        return list(tools)
    asked = _words(query)
    scored = []
    for order, tool in enumerate(tools):
        connector = normalize(getattr(tool, "connector", ""))
        spec = tool.spec
        text = f"{spec.name.replace('_', ' ')} {spec.description}"
        score = len(asked & _words(text))
        if connector and (connector in normalize(query) or connector in asked):
            score += 5
        scored.append((score, -order, tool))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [t for s, _o, t in scored if s > 0][:limit]
