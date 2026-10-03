"""Riesgo de cada acción concreta, decidido por reglas y no por el modelo.

La política de permisos (``policy.py``) mira el nombre de la herramienta. Eso no
basta para el control del PC: ``pc_keys`` con ``ctrl+s`` es inocuo y con
``win+r`` abre la puerta a cualquier orden. Aquí se mira la acción entera
(herramienta + parámetros + lo que ya pasó en el turno) y se decide:

* ``Risk``: LOW (sin preguntar), MEDIUM / HIGH (aprobación individual) o
  CRITICAL (bloqueada por política, ni se pregunta).
* Si la acción se puede cancelar una vez iniciada.

Ante la duda, se bloquea. Ninguna regla depende de lo que diga el LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PureWindowsPath
from typing import Any

from crisvis.security.policy import Tier


class Risk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_ORDER = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2, Risk.CRITICAL: 3}


def at_least(a: Risk, b: Risk) -> Risk:
    return a if _ORDER[a] >= _ORDER[b] else b


@dataclass(frozen=True)
class Assessment:
    risk: Risk
    reason: str = ""
    cancellable: bool = False
    warning: str = ""

    @property
    def blocked(self) -> bool:
        return self.risk is Risk.CRITICAL


# Herramientas cuyo proceso se puede matar a mitad (ProcessRunner + job object).
CANCELLABLE = frozenset({"shell_exec"})

# -- listas de política ---------------------------------------------------------

# Programas que abren una shell, ejecutan scripts o cambian el sistema.
SHELL_PROGRAMS = frozenset(
    {
        "cmd", "powershell", "pwsh", "powershell_ise", "wt", "windowsterminal", "conhost",
        "openconsole", "bash", "wsl", "regedit", "regedt32", "reg", "mshta", "rundll32",
        "regsvr32", "wscript", "cscript", "taskkill", "tskill", "shutdown", "bcdedit",
        "schtasks", "at", "certutil", "bitsadmin", "msiexec", "wmic", "diskpart", "format",
        "netsh", "sc", "net", "net1", "taskmgr", "mmc", "gpedit", "secpol", "cmstp",
        "installutil", "msbuild", "forfiles", "pcalua", "control", "msconfig", "vssadmin",
        "wbadmin", "cipher", "takeown", "icacls", "attrib", "python", "pythonw", "py",
        "node", "deno", "ruby", "perl", "java", "javaw",
    }
)
# Nombres con los que se piden esas mismas cosas de palabra.
SHELL_NAMES = (
    "simbolo del sistema", "command prompt", "terminal", "consola", "powershell",
    "editor del registro", "registry editor", "administrador de tareas", "task manager",
    "ejecutar", "run dialog", "cuadro ejecutar", "directiva de grupo", "group policy",
    "configuracion del sistema", "panel de control", "control panel", "servicios",
    "services", "programador de tareas", "task scheduler", "simbolo de sistema",
)
EXEC_EXTENSIONS = frozenset(
    {
        ".exe", ".bat", ".cmd", ".com", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js",
        ".jse", ".wsf", ".wsh", ".hta", ".msi", ".msp", ".msc", ".scr", ".cpl", ".lnk",
        ".reg", ".pif", ".jar", ".py", ".pyw", ".appref-ms", ".application", ".url",
        ".inf", ".sys", ".dll",
    }
)
# Esquemas de URL que pc_open acepta. Todo lo demás (shell:, file:, ms-msdt:,
# search-ms:, javascript:, vbscript:…) se bloquea.
URL_SCHEMES = {"http": Risk.MEDIUM, "https": Risk.MEDIUM, "mailto": Risk.MEDIUM,
               "spotify": Risk.MEDIUM, "ms-settings": Risk.HIGH}
PROTECTED_DIRS = ("\\windows\\", "\\program files", "\\programdata\\", "\\system32",
                  "\\syswow64", "\\$recycle.bin")

# Ventanas donde teclear o pulsar equivale a ejecutar órdenes.
SHELL_PROCESSES = frozenset(
    {
        "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "windowsterminal.exe",
        "wt.exe", "openconsole.exe", "conhost.exe", "bash.exe", "wsl.exe", "mintty.exe",
        "regedit.exe", "taskmgr.exe", "mmc.exe", "mshta.exe", "wscript.exe", "cscript.exe",
        "searchhost.exe", "searchapp.exe", "searchui.exe", "startmenuexperiencehost.exe",
        "msconfig.exe", "control.exe", "systemsettingsadminflows.exe", "consent.exe",
    }
)
_SHELL_TITLE = re.compile(
    r"^(ejecutar|run)$|administrador de tareas|task manager|editor del registro|"
    r"registry editor|s[ií]mbolo del sistema|command prompt|powershell|windows terminal|"
    r"^administrador:|^administrator:|\bcmd(\.exe)?\b|control de cuentas de usuario|"
    r"user account control",
    re.IGNORECASE,
)

# Teclas: combinaciones que abren un lanzador, el administrador de tareas o
# elevan privilegios. Cualquier otra con Windows que no esté en la lista
# blanca también se bloquea.
_BLOCKED_COMBOS = {
    frozenset({"win", "r"}),
    frozenset({"win", "x"}),
    frozenset({"win", "s"}),
    frozenset({"win", "q"}),
    frozenset({"win"}),
    frozenset({"ctrl", "escape"}),
    frozenset({"ctrl", "shift", "escape"}),
    frozenset({"ctrl", "alt", "delete"}),
    frozenset({"ctrl", "shift", "enter"}),
}
_WIN_ALLOWED = {
    frozenset({"win", "d"}),
    frozenset({"win", "e"}),
    frozenset({"win", "up"}),
    frozenset({"win", "down"}),
    frozenset({"win", "left"}),
    frozenset({"win", "right"}),
    frozenset({"win", "tab"}),
    frozenset({"win", "shift", "s"}),
}
_PASTE = {frozenset({"ctrl", "v"}), frozenset({"shift", "insert"})}
_SUBMIT = {frozenset({"enter"}), frozenset({"ctrl", "enter"})}

# Texto que, escrito en cualquier sitio, parece una orden de consola.
_COMMAND_TEXT = re.compile(
    r"(?im)"
    r"^\s*(?:start\s+|&\s*)?(?:cmd(?:\.exe)?(?:\s+/|\s*$)|(?:powershell|pwsh)(?:\.exe)?\b|"
    r"wt(?:\.exe)?(?:\s|$)|regedit\b|mshta\b|rundll32\b|regsvr32\b|wscript\b|cscript\b|"
    r"taskkill\b|shutdown(?:\.exe)?(?:\s+[/-]|\s*$)|bcdedit\b|schtasks\b|certutil\b|"
    r"bitsadmin\b|msiexec\b|wmic\b|diskpart\b|vssadmin\b|"
    r"reg\s+(?:add|delete|import|load|save|copy)\b|net\s+(?:user|localgroup|share|use|stop|start)\b|"
    r"sc\s+(?:create|config|delete|stop|start)\b|format\s+[a-z]:|netsh\b|takeown\b|icacls\b|"
    r"set-executionpolicy\b|start-process\b|invoke-\w+|iex\b|del\s+/|rd\s+/s|rmdir\s+/s|"
    r"rm\s+-rf?\b|curl\s+\S+.*\|\s*\w+|wget\s+\S+.*\|)"
    r"|\b[\w.-]+\.(?:exe|bat|cmd|ps1|vbs|vbe|hta|msi|scr|jse|wsf)\b"
    r"|-enc(?:odedcommand)?\s|\|\s*iex\b|invoke-expression|downloadstring|frombase64string"
    r"|%comspec%|\$env:comspec|&&|\|\|"
)


def looks_like_command(text: str) -> bool:
    return bool(_COMMAND_TEXT.search(text or ""))


def _norm(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text).strip().lower()


def is_shell_window(title: str | None, process: str | None) -> bool:
    """Ventanas donde teclear, pegar o pulsar Intro ejecuta órdenes."""
    proc = (process or "").lower()
    if proc in SHELL_PROCESSES:
        return True
    return bool(_SHELL_TITLE.search(title or ""))


def _combo(spec: str) -> list[str]:
    from crisvis.body.desktop import parse_keys

    try:
        return parse_keys(spec)
    except Exception:  # noqa: BLE001
        return [spec.strip().lower()]


def keys_assessment(spec: str) -> Assessment:
    combo = frozenset(_combo(spec))
    if not combo:
        return Assessment(Risk.CRITICAL, "combinación de teclas vacía o ilegible")
    if combo in _BLOCKED_COMBOS:
        return Assessment(
            Risk.CRITICAL,
            f"«{spec}» abre un lanzador, el administrador de tareas o eleva privilegios",
        )
    if "win" in combo and combo not in _WIN_ALLOWED:
        return Assessment(Risk.CRITICAL, f"«{spec}» usa la tecla Windows fuera de la lista blanca")
    if combo == frozenset({"alt", "f4"}):
        return Assessment(
            Risk.HIGH,
            "Alt+F4 cierra la ventana (y en el escritorio abre el diálogo de apagado)",
            warning="Puede cerrar trabajo sin guardar.",
        )
    return Assessment(Risk.MEDIUM, "pulsación de teclas en la ventana activa")


def _is_web_page(raw: str) -> bool:
    """Dominio o sitio por su nombre que ``Desktop.open`` manda al navegador por https.

    «youtube.com» acaba en .com igual que un ejecutable de MS-DOS, pero nunca llega a
    ``os.startfile``: se abre como https://youtube.com. El resto de extensiones de
    ejecutable sigue bloqueado aunque parezca un dominio.
    """
    from crisvis.body.desktop import web_url

    if web_url(raw) is None:
        return False
    host = raw.strip().split("/", 1)[0]
    ext = PureWindowsPath(host).suffix.lower()
    stem = PureWindowsPath(host).stem.lower()
    if stem in SHELL_PROGRAMS:
        return False
    return ext == ".com" or ext not in EXEC_EXTENSIONS


def open_assessment(target: str, allowed_paths: list[str] | tuple[str, ...] = ()) -> Assessment:
    raw = (target or "").strip()
    if not raw:
        return Assessment(Risk.CRITICAL, "no se indicó qué abrir")
    allowed = {_norm(p) for p in allowed_paths}
    if _norm(raw) in allowed:
        return Assessment(Risk.MEDIUM, "programa en seguridad.apps_permitidas")
    scheme = re.match(r"^([a-z][a-z0-9+.-]*):", raw, re.IGNORECASE)
    if scheme and len(scheme.group(1)) > 1:
        risk = URL_SCHEMES.get(scheme.group(1).lower())
        if risk is None:
            return Assessment(Risk.CRITICAL, f"el esquema «{scheme.group(1)}:» no está permitido")
        return Assessment(risk, "abrir una dirección")
    if _is_web_page(raw):
        return Assessment(Risk.MEDIUM, "abrir una página web en el navegador")
    name = _norm(raw)
    stem = PureWindowsPath(name).stem.lower()
    ext = PureWindowsPath(name).suffix.lower()
    if stem in SHELL_PROGRAMS or name in SHELL_PROGRAMS:
        return Assessment(Risk.CRITICAL, f"«{raw}» es una consola o herramienta de sistema")
    if any(n == name or name.startswith(n + " ") or name.endswith(" " + n) for n in SHELL_NAMES):
        return Assessment(Risk.CRITICAL, f"«{raw}» es una consola o herramienta de sistema")
    if ext in EXEC_EXTENSIONS:
        return Assessment(Risk.CRITICAL, f"«{raw}» es un ejecutable o script")
    lowered = name.replace("/", "\\")
    if any(d in lowered for d in PROTECTED_DIRS):
        return Assessment(Risk.CRITICAL, "ruta de sistema protegida")
    if looks_like_command(raw):
        return Assessment(Risk.CRITICAL, "el objetivo parece una orden de consola")
    return Assessment(Risk.MEDIUM, "abrir una aplicación, archivo o carpeta")


def app_name_allowed(name: str) -> str | None:
    """Comprobación final de la app del menú Inicio que se va a lanzar (nombre o AppID).

    Los AppID de apps de escritorio suelen ser rutas a un .exe (Chrome, Word), así
    que aquí no cuenta la extensión: solo si es una consola o herramienta de sistema.
    """
    norm = _norm(name)
    stem = PureWindowsPath(norm).stem.lower()
    if stem in SHELL_PROGRAMS or norm in SHELL_PROGRAMS:
        return f"«{name}» es una consola o herramienta de sistema"
    if any(n == norm or norm.startswith(n + " ") or norm.endswith(" " + n) for n in SHELL_NAMES):
        return f"«{name}» es una consola o herramienta de sistema"
    if "windowsterminal" in norm or "powershell" in norm:
        return f"«{name}» es una consola"
    return None


def _path_risk(raw: str) -> Risk:
    lowered = _norm(raw).replace("/", "\\")
    if any(d in lowered for d in PROTECTED_DIRS):
        return Risk.CRITICAL
    if PureWindowsPath(lowered).suffix in EXEC_EXTENSIONS:
        return Risk.HIGH
    return Risk.MEDIUM


def assess(
    tool: str, args: dict[str, Any], tier: Tier, *, apps: tuple[str, ...] = ()
) -> Assessment:
    """Riesgo de una llamada aislada. ``SequenceGuard`` añade el contexto del turno."""
    cancellable = tool in CANCELLABLE
    if tier is Tier.INTERFAZ:
        return Assessment(Risk.LOW, "interfaz", cancellable)
    if tool == "read_clipboard":
        return Assessment(
            Risk.HIGH,
            "leer el portapapeles",
            warning=(
                "El portapapeles puede contener contraseñas, códigos o datos personales. "
                "El contenido solo se usa en esta conversación y no se guarda en registros."
            ),
        )
    if tool == "pc_keys":
        return keys_assessment(str(args.get("keys") or ""))
    if tool == "pc_type":
        text = str(args.get("text") or "")
        if looks_like_command(text):
            return Assessment(Risk.CRITICAL, "el texto parece una orden de consola")
        return Assessment(Risk.MEDIUM, "escribir en la ventana activa")
    if tool == "pc_open":
        return open_assessment(str(args.get("target") or ""), apps)
    if tool == "pc_youtube":
        return Assessment(Risk.MEDIUM, "poner música o un vídeo en YouTube")
    if tool == "pc_click":
        return Assessment(Risk.MEDIUM, "pulsar en la ventana activa")
    if tool == "pc_scroll":
        return Assessment(Risk.LOW, "desplazar")
    if tool == "pc_window":
        action = str(args.get("action") or "focus").lower()
        if action == "close":
            return Assessment(Risk.MEDIUM, "cerrar una ventana", warning="Puede perder cambios.")
        return Assessment(Risk.LOW, "colocar una ventana")
    if tool == "pc_file_manage":
        risk = at_least(
            _path_risk(str(args.get("path") or "")), _path_risk(str(args.get("dest") or ""))
        )
        if risk is Risk.CRITICAL:
            return Assessment(risk, "ruta de sistema protegida")
        if str(args.get("action") or "").lower() == "trash":
            risk = at_least(risk, Risk.HIGH)
        return Assessment(risk, "gestionar archivos")
    if tool in ("pc_kill", "pc_power"):
        return Assessment(Risk.HIGH, "acción de sistema", warning="No se puede deshacer.")
    if tool == "shell_exec":
        return Assessment(Risk.HIGH, "ejecutar una orden", cancellable=True)
    if tier is Tier.LECTURA:
        return Assessment(Risk.LOW, "consulta", cancellable)
    if tier is Tier.PELIGROSO:
        return Assessment(Risk.HIGH, "herramienta peligrosa", cancellable)
    return Assessment(Risk.MEDIUM, "cambia algo", cancellable)


@dataclass
class SequenceGuard:
    """Lo que ya pasó en este turno. Bloquea cadenas que, por piezas, llegan a una shell.

    ``pc_type`` "c", luego "md", luego Intro no pasa: se mira el texto acumulado.
    Abrir un lanzador y después escribir o pegar tampoco.
    """

    typed: str = ""
    launcher: bool = False
    history: list[tuple[str, str]] = field(default_factory=list)

    def check(self, tool: str, args: dict[str, Any]) -> Assessment | None:
        if tool == "pc_type":
            text = str(args.get("text") or "")
            if self.launcher:
                return Assessment(Risk.CRITICAL, "escribir tras abrir un lanzador del sistema")
            if looks_like_command(self.typed + text) or looks_like_command(
                (self.typed + " " + text).strip()
            ):
                return Assessment(Risk.CRITICAL, "el texto acumulado del turno forma una orden")
        if tool == "pc_keys":
            combo = frozenset(_combo(str(args.get("keys") or "")))
            if combo in _SUBMIT | _PASTE:
                if self.launcher:
                    return Assessment(Risk.CRITICAL, "confirmar o pegar en un lanzador del sistema")
                if looks_like_command(self.typed):
                    return Assessment(
                        Risk.CRITICAL, "Intro tras escribir algo que parece una orden"
                    )
        return None

    def commit(self, tool: str, args: dict[str, Any], risk: Risk) -> None:
        self.history.append((tool, risk.value))
        if tool == "pc_type":
            self.typed = (self.typed + str(args.get("text") or ""))[-2000:]
        elif tool == "pc_keys":
            combo = frozenset(_combo(str(args.get("keys") or "")))
            if combo in _BLOCKED_COMBOS:
                self.launcher = True
            if combo in _SUBMIT:
                self.typed = ""
        elif tool in ("pc_open", "pc_youtube", "pc_window", "pc_click"):
            # Cambió el foco: lo escrito antes ya no está en la misma caja.
            self.typed = ""
            self.launcher = False
