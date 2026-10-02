"""Configuración de Crisvis.

Se lee de ``crisvis.toml`` (``$CRISVIS_CONFIG`` o ``~/.crisvis/crisvis.toml``) y
de unas pocas variables de entorno. Este módulo no importa OpenJarvis a
propósito: la carpeta de datos del cerebro (``OPENJARVIS_HOME``) tiene que
fijarse antes de que OpenJarvis calcule sus rutas al importarse.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PERMISSION_MODES = ("lectura", "confirmar", "libre")


@dataclass
class ServerSettings:
    host: str = "127.0.0.1"
    port: int = 8787
    # Orígenes extra que pueden abrir el WebSocket, además de la propia app y
    # los puertos de desarrollo de Vite en localhost.
    origenes_permitidos: list[str] = field(default_factory=list)
    # Clientes sin cabecera Origin (curl, scripts). Desactivado: es también el
    # aspecto que tiene el malware local.
    permitir_sin_origen: bool = False
    abrir_navegador: bool = True


@dataclass
class AssistantSettings:
    nombre: str = "Jarvis"
    idioma: str = "es-ES"
    tratamiento: str = "señor"
    # Palabras de activación. La interfaz acepta además variantes que el
    # reconocedor suele confundir (ver apps/face/src/lib/persona.ts).
    palabras_activacion: list[str] = field(default_factory=lambda: ["jarvis", "crisvis"])


@dataclass
class BrainSettings:
    # Motor de OpenJarvis ("ollama", "llamacpp", "vllm", "lmstudio", "cloud"...).
    # Vacío = el preferido en la configuración de OpenJarvis o el primero sano.
    motor: str = ""
    # Modelo. Vacío = el predeterminado de OpenJarvis o el primero disponible.
    modelo: str = ""
    # Modelo con visión para la cámara y la pantalla. Vacío = el mejor de los
    # instalados en Ollama (qwen3-vl:4b-instruct si está), o sin visión.
    modelo_vision: str = ""
    herramientas: list[str] = field(
        default_factory=lambda: [
            "web_search",
            "calculator",
            "get_weather",
            "file_read",
            "memory_search",
            "memory_store",
            "file_write",
            "shell_exec",
        ]
    )
    # Herramientas de la interfaz (display, blade, ui_*). Los modelos locales
    # pequeños rinden mejor con menos herramientas; se pueden apagar.
    herramientas_interfaz: bool = True
    herramientas_camara: bool = True
    # Control del equipo (pc_*): apps, ventanas, ratón, teclado, pantalla,
    # archivos y energía. Cada acción pasa por la política de permisos.
    herramientas_pc: bool = True
    # Una tarea en el PC (abrir, mirar, pulsar, escribir, comprobar) encadena
    # muchas herramientas.
    max_turnos: int = 16
    temperatura: float = 0.6
    max_tokens: int = 800
    # Ventana de contexto pedida al motor (num_ctx en Ollama). Las herramientas
    # del PC y las listas de elementos de una ventana no caben en 8k.
    contexto: int = 16384
    # Razonamiento de modelos que lo admiten (qwen3, deepseek-r1, gpt-oss…):
    # "auto" = solo al decidir qué herramienta usar, "siempre" o "nunca".
    razonar: str = "auto"
    # Mensajes de conversación que se conservan por sesión.
    historial: int = 24
    memoria: bool = True
    # Aplicaciones MCP. Lo normal es declararlas en <home>/mcp.json (formato de
    # Claude/Cursor); aquí también valen:
    # [{name, command, args, env} | {name, url, token, headers}].
    mcp: list[dict[str, Any]] = field(default_factory=list)
    # Herramientas de aplicaciones conectadas que se ofrecen al modelo en cada
    # orden (las más relacionadas con lo pedido). Un modelo local pequeño se
    # pierde con cien.
    mcp_max_herramientas: int = 12
    # Usar ~/.openjarvis (compartido con la CLI `jarvis`) en vez de la carpeta
    # propia de Crisvis.
    compartir_openjarvis: bool = False


@dataclass
class PermissionSettings:
    # lectura   = solo consulta; nada que cambie el mundo.
    # confirmar = lo que cambia algo se pide de viva voz o con un clic.
    # libre     = escritura permitida; lo peligroso se sigue confirmando.
    modo: str = "confirmar"
    permitir: list[str] = field(default_factory=list)
    confirmar: list[str] = field(default_factory=list)
    denegar: list[str] = field(default_factory=list)
    segundos_confirmacion: int = 30


@dataclass
class VoiceSettings:
    # Voz del navegador: "original" = la británica masculina con la que arranca
    # el JARVIS original (hablando en el idioma del asistente); "nativa" = una
    # voz nativa del idioma del asistente.
    estilo: str = "original"
    # Voz clonada local (services/voz, XTTS-v2). Solo actúa si está instalada;
    # las muestras de la voz van en `clonada_carpeta` (vacío = <home>/voz).
    clonada: bool = True
    clonada_puerto: int = 8788
    clonada_carpeta: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voz: str = "JBFqnCBsd6RMkjVDRZzb"
    # Transcripción local con faster-whisper si el extra `voz-local` está instalado.
    whisper_local: bool = True
    whisper_modelo: str = "small"


@dataclass
class MediaSettings:
    # Carpetas extra desde las que /file puede servir imágenes.
    carpetas: list[str] = field(default_factory=list)


@dataclass
class Settings:
    home: Path = field(default_factory=lambda: Path.home() / ".crisvis")
    servidor: ServerSettings = field(default_factory=ServerSettings)
    asistente: AssistantSettings = field(default_factory=AssistantSettings)
    cerebro: BrainSettings = field(default_factory=BrainSettings)
    permisos: PermissionSettings = field(default_factory=PermissionSettings)
    voz: VoiceSettings = field(default_factory=VoiceSettings)
    medios: MediaSettings = field(default_factory=MediaSettings)

    @property
    def brain_home(self) -> Path:
        return self.home / "cerebro"

    @property
    def audit_path(self) -> Path:
        return self.home / "auditoria.jsonl"

    def public_dict(self) -> dict[str, Any]:
        """Lo que se puede mostrar sin filtrar secretos."""
        data = asdict(self)
        data["home"] = str(self.home)
        data["voz"]["elevenlabs_api_key"] = "***" if self.voz.elevenlabs_api_key else ""
        for server in data["cerebro"]["mcp"]:
            if "token" in server:
                server["token"] = "***"
            for key in ("env", "headers"):
                if isinstance(server.get(key), dict):
                    server[key] = {k: "***" for k in server[key]}
        return data


def load_dotenv(path: Path) -> None:
    """Lee ``CLAVE=valor`` de un .env sin pisar variables ya definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def default_config_path() -> Path:
    explicit = os.environ.get("CRISVIS_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("CRISVIS_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".crisvis"
    return base / "crisvis.toml"


def _merge(target: Any, data: dict[str, Any], where: str) -> None:
    known = {f.name: f for f in fields(target)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"{where}: clave desconocida '{key}'")
        current = getattr(target, key)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"{where}.{key}: se esperaba una tabla")
            _merge(current, value, f"{where}.{key}")
        else:
            setattr(target, key, value)


def load_settings(path: Path | None = None) -> Settings:
    settings = Settings()
    config_path = path or default_config_path()
    settings.home = config_path.parent

    if config_path.exists():
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        _merge(settings, data, config_path.name)

    env = os.environ
    if env.get("CRISVIS_HOST"):
        settings.servidor.host = env["CRISVIS_HOST"]
    if env.get("CRISVIS_PORT"):
        settings.servidor.port = int(env["CRISVIS_PORT"])
    if env.get("CRISVIS_MODELO"):
        settings.cerebro.modelo = env["CRISVIS_MODELO"]
    if env.get("CRISVIS_MOTOR"):
        settings.cerebro.motor = env["CRISVIS_MOTOR"]
    if env.get("CRISVIS_PERMISOS"):
        settings.permisos.modo = env["CRISVIS_PERMISOS"]
    if env.get("ELEVENLABS_API_KEY") and not settings.voz.elevenlabs_api_key:
        settings.voz.elevenlabs_api_key = env["ELEVENLABS_API_KEY"]

    if settings.permisos.modo not in PERMISSION_MODES:
        raise ValueError(
            f"permisos.modo='{settings.permisos.modo}' no es uno de {', '.join(PERMISSION_MODES)}"
        )
    return settings


def prepare_environment(settings: Settings) -> None:
    """Fija la carpeta de datos de OpenJarvis. Llamar antes de importar openjarvis."""
    settings.home.mkdir(parents=True, exist_ok=True)
    if settings.cerebro.compartir_openjarvis or os.environ.get("OPENJARVIS_HOME"):
        return
    settings.brain_home.mkdir(parents=True, exist_ok=True)
    os.environ["OPENJARVIS_HOME"] = str(settings.brain_home)


DEFAULT_TOML = """\
# Configuración de Crisvis. Todo es opcional: borra lo que no quieras cambiar.

[servidor]
host = "127.0.0.1"
port = 8787
abrir_navegador = true

[asistente]
nombre = "Jarvis"
idioma = "es-ES"
tratamiento = "señor"
palabras_activacion = ["jarvis", "crisvis"]

[cerebro]
# Motor de OpenJarvis: "ollama", "llamacpp", "lmstudio", "vllm", "cloud"...
# Vacío = autodetección.
motor = ""
# Modelo, p. ej. "qwen3:8b". Vacío = el primero que ofrezca el motor
# (sin contar los que solo ven).
modelo = ""
# Modelo con visión para la cámara y la pantalla del PC. Vacío = el mejor de los
# instalados; el recomendado es "qwen3-vl:4b-instruct" (sabe señalar dónde está
# cada botón en una captura).
modelo_vision = ""
herramientas = ["web_search", "calculator", "get_weather", "file_read",
                "memory_search", "memory_store", "file_write", "shell_exec"]
herramientas_interfaz = true
herramientas_camara = true
# Manejar el PC: abrir apps, pulsar, escribir, ver la pantalla, archivos, apagar…
herramientas_pc = true
max_turnos = 16
contexto = 16384
# Razonamiento (qwen3, deepseek-r1, gpt-oss…): "auto" lo usa solo al decidir qué
# herramienta llamar; "siempre" es más fiable y más lento; "nunca", al revés.
razonar = "auto"
memoria = true

# Aplicaciones conectadas por MCP: van en mcp.json, junto a este archivo, con el
# mismo formato que Claude Desktop y Cursor ({"mcpServers": {...}}). Las claves,
# como ${NOTION_TOKEN}, se leen del .env de esta carpeta. Se recarga solo.
# Herramientas de esas apps que se ofrecen al modelo en cada orden:
mcp_max_herramientas = 12

[permisos]
# lectura | confirmar | libre
modo = "confirmar"
permitir = []
confirmar = []
denegar = []

[voz]
# "original" = la voz británica del JARVIS original; "nativa" = voz española del sistema.
estilo = "original"
# Voz clonada en este equipo (npm run voz:instalar). Pon uno o varios audios
# limpios de la voz (wav/mp3, 10 s a 2 min) en la carpeta voz/ junto a este archivo.
clonada = true
clonada_puerto = 8788
clonada_carpeta = ""
# Clave opcional de ElevenLabs (mejor voz y transcripción). También vale la
# variable de entorno ELEVENLABS_API_KEY.
elevenlabs_api_key = ""
whisper_local = true
whisper_modelo = "small"
"""
