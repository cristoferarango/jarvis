"""Política de permisos: quién puede hacer qué, decidido antes de ejecutar.

Una interfaz de voz es un mal sitio para un diálogo de "¿está seguro?" que
aparezca por sorpresa, así que la decisión se toma por adelantado según el
nivel de la herramienta y el modo elegido por el usuario. Lo único que se
pregunta en el momento son las acciones que el modo marca como "confirmar", y
esa pregunta la hace la interfaz (voz o clic), no el modelo.

Esta capa va por delante de la seguridad propia de OpenJarvis (capacidades,
taint, rate limit, escáner de inyecciones), que sigue actuando dentro de su
ToolExecutor. Son dos cerraduras, no una.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import Enum

from crisvis.settings import PermissionSettings


class Tier(str, Enum):
    INTERFAZ = "interfaz"
    LECTURA = "lectura"
    ESCRITURA = "escritura"
    PELIGROSO = "peligroso"


class Decision(str, Enum):
    ALLOW = "permitir"
    CONFIRM = "confirmar"
    DENY = "denegar"


_KNOWN: dict[str, Tier] = {
    # La propia cara: dibuja en nuestra pantalla y no toca nada más.
    "display": Tier.INTERFAZ,
    "blade": Tier.INTERFAZ,
    "ui_theme": Tier.INTERFAZ,
    "ui_reactor": Tier.INTERFAZ,
    "ui_orbit": Tier.INTERFAZ,
    "ui_chrome": Tier.INTERFAZ,
    "ui_effect": Tier.INTERFAZ,
    "ui_screen": Tier.INTERFAZ,
    "ui_reset": Tier.INTERFAZ,
    # Consultar no cambia nada. La cámara cuenta como lectura: la protegen el
    # permiso del navegador y un indicador visible mientras está encendida.
    "probe_url": Tier.LECTURA,
    "look": Tier.LECTURA,
    "watch": Tier.LECTURA,
    "web_search": Tier.LECTURA,
    "calculator": Tier.LECTURA,
    "think": Tier.LECTURA,
    "get_weather": Tier.LECTURA,
    "file_read": Tier.LECTURA,
    "pdf_extract": Tier.LECTURA,
    "memory_search": Tier.LECTURA,
    # Solo escribe en la memoria propia del asistente, local y borrable; pedir
    # permiso para cada "recuerda que…" haría inútil la memoria.
    "memory_store": Tier.LECTURA,
    "memory_retrieve": Tier.LECTURA,
    "retrieval": Tier.LECTURA,
    "scan_chunks": Tier.LECTURA,
    "knowledge_sql": Tier.LECTURA,
    "kg_query": Tier.LECTURA,
    "kg_neighbors": Tier.LECTURA,
    "git_status": Tier.LECTURA,
    "git_diff": Tier.LECTURA,
    "git_log": Tier.LECTURA,
    "calendar_search": Tier.LECTURA,
    "calendar_upcoming": Tier.LECTURA,
    "channel_list": Tier.LECTURA,
    "channel_status": Tier.LECTURA,
    "audio_transcribe": Tier.LECTURA,
    "llm": Tier.LECTURA,
    # Escribir: cambia algo, pero de forma acotada y reversible.
    "file_write": Tier.ESCRITURA,
    "apply_patch": Tier.ESCRITURA,
    "memory_index": Tier.ESCRITURA,
    "memory_manage": Tier.ESCRITURA,
    "user_profile_manage": Tier.ESCRITURA,
    "kg_add_entity": Tier.ESCRITURA,
    "kg_add_relation": Tier.ESCRITURA,
    "git_commit": Tier.ESCRITURA,
    "http_request": Tier.ESCRITURA,
    "channel_send": Tier.ESCRITURA,
    "image_generate": Tier.ESCRITURA,
    "text_to_speech": Tier.ESCRITURA,
    "skill_manage": Tier.ESCRITURA,
    "digest_collect": Tier.LECTURA,
    # Peligroso: ejecución arbitraria.
    "shell_exec": Tier.PELIGROSO,
    "code_interpreter": Tier.PELIGROSO,
    "code_interpreter_docker": Tier.PELIGROSO,
    "docker_shell_exec": Tier.PELIGROSO,
    "repl": Tier.PELIGROSO,
    "db_query": Tier.PELIGROSO,
    # El equipo. Mirar la pantalla o las ventanas no cambia nada; la música y
    # el volumen son tan inocuos como cambiar el color de la interfaz.
    "pc_inspect": Tier.LECTURA,
    "pc_screen": Tier.LECTURA,
    "pc_system": Tier.LECTURA,
    "pc_find_files": Tier.LECTURA,
    "pc_media": Tier.INTERFAZ,
    "pc_open": Tier.ESCRITURA,
    "pc_youtube": Tier.ESCRITURA,
    "pc_window": Tier.ESCRITURA,
    "pc_click": Tier.ESCRITURA,
    "pc_type": Tier.ESCRITURA,
    "pc_keys": Tier.ESCRITURA,
    "pc_scroll": Tier.ESCRITURA,
    "pc_file_manage": Tier.ESCRITURA,
    "pc_kill": Tier.PELIGROSO,
    "pc_power": Tier.PELIGROSO,
    # El portapapeles puede llevar contraseñas: se pregunta siempre, también en LIBRE.
    "read_clipboard": Tier.PELIGROSO,
}

# Para herramientas desconocidas (MCP): el nombre tiene que defenderse solo.
_READ_VERB = re.compile(
    r"^(get|list|read|search|find|query|fetch|check|describe|inspect|show|view|explain|"
    r"screenshot|lookup|status)",
    re.IGNORECASE,
)
_EFFECTFUL = re.compile(
    r"(send|call|post|create|delete|remove|update|edit|write|install|launch|tap|swipe|press|"
    r"type|buy|pay|charge|publish|deploy|outbound|download|upload|move|rename|set_)",
    re.IGNORECASE,
)
_DANGEROUS = re.compile(
    r"(exec|shell|run_command|terminal|eval|sudo|format|wipe|purchase|transfer|payment)",
    re.IGNORECASE,
)

_MATRIX: dict[str, dict[Tier, Decision]] = {
    "lectura": {
        Tier.INTERFAZ: Decision.ALLOW,
        Tier.LECTURA: Decision.ALLOW,
        Tier.ESCRITURA: Decision.DENY,
        Tier.PELIGROSO: Decision.DENY,
    },
    "confirmar": {
        Tier.INTERFAZ: Decision.ALLOW,
        Tier.LECTURA: Decision.ALLOW,
        Tier.ESCRITURA: Decision.CONFIRM,
        Tier.PELIGROSO: Decision.CONFIRM,
    },
    "libre": {
        Tier.INTERFAZ: Decision.ALLOW,
        Tier.LECTURA: Decision.ALLOW,
        Tier.ESCRITURA: Decision.ALLOW,
        Tier.PELIGROSO: Decision.CONFIRM,
    },
}


_MCP_PREFIX = re.compile(r"^mcp__[^_].*?__")


def classify(tool_name: str) -> Tier:
    if tool_name in _KNOWN:
        return _KNOWN[tool_name]
    # `mcp__servidor__herramienta`: el verbo está en la parte de la herramienta.
    verb = _MCP_PREFIX.sub("", tool_name)
    if _DANGEROUS.search(verb):
        return Tier.PELIGROSO
    if _EFFECTFUL.search(verb):
        return Tier.ESCRITURA
    if _READ_VERB.match(verb):
        return Tier.LECTURA
    return Tier.ESCRITURA


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


@dataclass(frozen=True)
class Verdict:
    tool: str
    tier: Tier
    decision: Decision
    reason: str


class PermissionPolicy:
    def __init__(self, settings: PermissionSettings) -> None:
        self._settings = settings

    @property
    def mode(self) -> str:
        return self._settings.modo

    def set_mode(self, mode: str) -> None:
        if mode not in _MATRIX:
            raise ValueError(f"modo desconocido: {mode}")
        self._settings.modo = mode

    def evaluate(self, tool_name: str) -> Verdict:
        tier = classify(tool_name)
        s = self._settings
        if _matches(tool_name, s.denegar):
            return Verdict(tool_name, tier, Decision.DENY, "denegada en la configuración")
        if _matches(tool_name, s.permitir):
            return Verdict(tool_name, tier, Decision.ALLOW, "permitida en la configuración")
        if _matches(tool_name, s.confirmar):
            return Verdict(tool_name, tier, Decision.CONFIRM, "requiere confirmación")
        decision = _MATRIX[s.modo][tier]
        return Verdict(tool_name, tier, decision, f"modo {s.modo}, nivel {tier.value}")

    def visible(self, tool_name: str) -> bool:
        """Las herramientas denegadas ni se ofrecen al modelo."""
        return self.evaluate(tool_name).decision is not Decision.DENY
