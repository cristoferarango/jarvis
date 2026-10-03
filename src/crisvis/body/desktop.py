"""El escritorio: ventanas, elementos, teclado, ratón, pantalla, apps y sistema.

Capa de bajo nivel debajo de las herramientas ``pc_*``. Pensada para Windows
(UI Automation, Win32, menú Inicio); en otros sistemas funciona lo portable
(ratón, teclado, capturas, procesos) y lo demás lo dice.

La ventana "objetivo" nunca es la propia interfaz de Crisvis: cuando el usuario
habla, la que tiene el foco es el navegador con Crisvis, y lo que quiere tocar
es lo que tenía delante antes.
"""

from __future__ import annotations

import base64
import ctypes
import difflib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WINDOWS = sys.platform == "win32"

_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
_OWN_TITLE = re.compile(r"^crisvis\b", re.IGNORECASE)
# Ventanas del propio Windows que no son de nadie y solo meten ruido.
_SYSTEM_TITLES = {
    "Program Manager",
    "Experiencia de entrada de Windows",
    "Windows Input Experience",
    "Host de ventanas emergentes",
    "Popup Host",
    "NVIDIA GeForce Overlay",
}
TITLE_CHARS = 90
NAME_CHARS = 60
MAX_ELEMENTS = 80
WALK_SECONDS = 5.0
WALK_NODES = 4000

# Tipos de control de UI Automation que se pueden tocar, con su nombre hablado.
_KINDS = {
    "ButtonControl": "botón",
    "SplitButtonControl": "botón",
    "EditControl": "campo",
    "DocumentControl": "documento",
    "ComboBoxControl": "desplegable",
    "MenuItemControl": "menú",
    "ListItemControl": "elemento",
    "TabItemControl": "pestaña",
    "HyperlinkControl": "enlace",
    "CheckBoxControl": "casilla",
    "RadioButtonControl": "opción",
    "TreeItemControl": "nodo",
    "DataItemControl": "fila",
    "SliderControl": "control deslizante",
}
_UNNAMED_OK = {"EditControl", "DocumentControl", "ComboBoxControl"}

_KNOWN_FOLDERS = {
    "escritorio": "Desktop",
    "desktop": "Desktop",
    "descargas": "Downloads",
    "downloads": "Downloads",
    "documentos": "Documents",
    "documents": "Documents",
    "imagenes": "Pictures",
    "fotos": "Pictures",
    "pictures": "Pictures",
    "musica": "Music",
    "music": "Music",
    "videos": "Videos",
    "inicio": "",
    "home": "",
    "carpeta personal": "",
}

_KEY_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "mayus": "shift",
    "mayusculas": "shift",
    "windows": "win",
    "tecla windows": "win",
    "super": "win",
    "intro": "enter",
    "return": "enter",
    "retorno": "enter",
    "esc": "escape",
    "escapar": "escape",
    "espacio": "space",
    "tabulador": "tab",
    "suprimir": "delete",
    "supr": "delete",
    "del": "delete",
    "borrar": "backspace",
    "retroceso": "backspace",
    "inicio": "home",
    "fin": "end",
    "arriba": "up",
    "abajo": "down",
    "izquierda": "left",
    "derecha": "right",
    "repag": "pageup",
    "re pag": "pageup",
    "avpag": "pagedown",
    "av pag": "pagedown",
    "insertar": "insert",
    "imprimir pantalla": "printscreen",
    "impr pant": "printscreen",
}


def normalize(text: str) -> str:
    """Minúsculas, sin tildes ni espacios sobrantes: para comparar nombres dichos."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip().lower()


def best_match(query: str, names: list[str], cutoff: float = 0.72) -> int | None:
    """Índice del nombre que mejor encaja con lo pedido, o None."""
    q = normalize(query)
    if not q:
        return None
    norm = [normalize(n) for n in names]
    for test in (
        lambda n: n == q,
        lambda n: n.startswith(q),
        lambda n: re.search(rf"\b{re.escape(q)}\b", n) is not None,
        lambda n: q in n,
    ):
        hits = [i for i, n in enumerate(norm) if n and test(n)]
        if hits:
            return min(hits, key=lambda i: len(norm[i]))
    scored = [(difflib.SequenceMatcher(None, q, n).ratio(), i) for i, n in enumerate(norm) if n]
    if not scored:
        return None
    ratio, index = max(scored)
    return index if ratio >= cutoff else None


def parse_keys(spec: str) -> list[str]:
    """'Ctrl + Mayús + Esc' -> ['ctrl', 'shift', 'escape'] (nombres de pyautogui)."""
    parts = [normalize(p) for p in re.split(r"\s*\+\s*", spec.strip()) if p.strip()]
    keys = []
    for part in parts:
        key = _KEY_ALIASES.get(part, part)
        keys.append(key.replace(" ", ""))
    return keys


def resolve_path(text: str) -> Path:
    """Ruta escrita o dicha: '~', variables, y 'descargas' / 'escritorio'…"""
    raw = os.path.expandvars(text.strip().strip('"').strip("'"))
    key = normalize(raw)
    if key in _KNOWN_FOLDERS:
        sub = _KNOWN_FOLDERS[key]
        home = Path.home()
        if sub and WINDOWS:
            onedrive = home / "OneDrive" / sub
            if not (home / sub).exists() and onedrive.exists():
                return onedrive
        return home / sub if sub else home
    return Path(raw).expanduser()


def looks_like_url(text: str) -> bool:
    t = text.strip().lower()
    if re.match(r"^[a-z][a-z0-9+.-]*://", t) or t.startswith("mailto:"):
        return True
    return bool(re.match(r"^(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/\S*)?$", t))


# Sitios que se piden por su nombre y se abren en el navegador, no como app.
WEB_ALIASES = {
    "youtube": "https://www.youtube.com/",
    "gmail": "https://mail.google.com/",
    "correo de google": "https://mail.google.com/",
    "google mail": "https://mail.google.com/",
    "notion": "https://www.notion.so/",
    "google": "https://www.google.com/",
    "google drive": "https://drive.google.com/",
    "drive": "https://drive.google.com/",
    "google calendar": "https://calendar.google.com/",
    "calendario de google": "https://calendar.google.com/",
    "github": "https://github.com/",
    "whatsapp web": "https://web.whatsapp.com/",
}


# Título que el navegador muestra cuando la pestaña activa es ese sitio.
_SITE_TITLES = {
    "youtube.com": "YouTube",
    "mail.google.com": "Gmail",
    "notion.so": "Notion",
    "drive.google.com": "Google Drive",
    "calendar.google.com": "Google Calendar",
    "web.whatsapp.com": "WhatsApp",
}


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def site_name(url: str) -> str:
    host = _host(url)
    for domain, title in _SITE_TITLES.items():
        if host == domain or host.endswith("." + domain):
            return title
    return ""


def is_site_home(url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return parts.path in ("", "/") and not parts.query and not parts.fragment


def safe_address(url: str) -> str | None:
    """URL que se puede escribir en la barra de direcciones: https, con host, sin
    credenciales, sin espacios ni caracteres de control."""
    from crisvis.openclaw.tabs import safe_tab_url

    if len(url) > 2000 or any(c.isspace() or ord(c) < 32 for c in url):
        return None
    return safe_tab_url(url)


def web_url(target: str) -> str | None:
    """La URL que ``open`` mandará al navegador, o None si no es una web."""
    t = (target or "").strip()
    alias = WEB_ALIASES.get(re.sub(r"\s+", " ", t.lower()))
    if alias:
        return alias
    if not looks_like_url(t):
        return None
    return t if "://" in t or t.lower().startswith("mailto:") else f"https://{t}"


def short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# -- ventanas -----------------------------------------------------------------


@dataclass
class Window:
    hwnd: int
    title: str
    process: str
    minimized: bool

    @property
    def label(self) -> str:
        return short(self.title, TITLE_CHARS)


@dataclass
class Element:
    index: int
    kind: str
    name: str
    x: int
    y: int
    value: str = ""

    @property
    def label(self) -> str:
        extra = f" = «{self.value}»" if self.value else ""
        return f"{self.index} {self.kind} «{self.name}»{extra}"


@dataclass
class Snapshot:
    window: Window | None
    elements: list[Element] = field(default_factory=list)
    truncated: bool = False
    at: float = field(default_factory=time.monotonic)


class DesktopError(RuntimeError):
    """Algo que el modelo debe contar tal cual al usuario."""


def _user32() -> Any:
    return ctypes.windll.user32  # type: ignore[attr-defined]


def _process_name(hwnd: int) -> str:
    if not WINDOWS:
        return ""
    import psutil

    pid = ctypes.c_ulong()
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except (psutil.Error, ValueError):
        return ""


def _is_own(window: Window) -> bool:
    return bool(_OWN_TITLE.match(window.title)) and window.process in _BROWSERS


class Desktop:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._focus: int | None = None
        self._snapshot: Snapshot | None = None
        self._apps: list[tuple[str, str]] | None = None

    # -- ventanas ------------------------------------------------------------

    def windows(self) -> list[Window]:
        """Ventanas visibles con título, de la de delante a la de atrás."""
        if not WINDOWS:
            raise DesktopError("La gestión de ventanas solo está disponible en Windows.")
        import pygetwindow as gw

        out = []
        for w in gw.getAllWindows():
            title = (w.title or "").strip()
            if not title or title in _SYSTEM_TITLES:
                continue
            hwnd = int(w._hWnd)
            if not _user32().IsWindowVisible(hwnd):
                continue
            out.append(Window(hwnd, title, _process_name(hwnd), bool(_user32().IsIconic(hwnd))))
        return out

    def target(self) -> Window | None:
        """La ventana sobre la que actuar: la de delante que no sea Crisvis."""
        wins = self.windows()
        if not wins:
            return None
        fg = _user32().GetForegroundWindow()
        front = next((w for w in wins if w.hwnd == fg), None)
        if front and not _is_own(front):
            return front
        if self._focus:
            kept = next((w for w in wins if w.hwnd == self._focus), None)
            if kept and not kept.minimized:
                return kept
        return next((w for w in wins if not _is_own(w) and not w.minimized), None)

    def find_window(self, query: str) -> Window:
        wins = [w for w in self.windows() if not _is_own(w)]
        labels = [f"{w.title} {Path(w.process).stem}" for w in wins]
        index = best_match(query, labels, cutoff=0.6)
        if index is None:
            raise DesktopError(f"No hay ninguna ventana abierta que se parezca a «{query}».")
        return wins[index]

    def activate(self, window: Window) -> None:
        user32 = _user32()
        if user32.IsIconic(window.hwnd):
            user32.ShowWindow(window.hwnd, 9)  # SW_RESTORE
        # Windows solo cede el foco a quien acaba de recibir una pulsación.
        user32.keybd_event(0x12, 0, 0, 0)  # Alt abajo
        user32.SetForegroundWindow(window.hwnd)
        user32.keybd_event(0x12, 0, 2, 0)  # Alt arriba
        user32.BringWindowToTop(window.hwnd)
        self._focus = window.hwnd
        time.sleep(0.25)

    def ensure_target(self) -> Window | None:
        """Trae al frente la ventana objetivo si Crisvis le robó el foco."""
        win = self.target()
        if win is None:
            return None
        if _user32().GetForegroundWindow() != win.hwnd:
            self.activate(win)
        self._focus = win.hwnd
        return win

    def window_action(self, query: str, action: str) -> Window:
        win = self.find_window(query)
        user32 = _user32()
        if action == "focus":
            self.activate(win)
        elif action == "minimize":
            user32.ShowWindow(win.hwnd, 6)
        elif action == "maximize":
            self.activate(win)
            user32.ShowWindow(win.hwnd, 3)
        elif action == "restore":
            user32.ShowWindow(win.hwnd, 9)
            self.activate(win)
        elif action == "close":
            user32.PostMessageW(win.hwnd, 0x0010, 0, 0)  # WM_CLOSE: la app puede preguntar
        else:
            raise DesktopError(f"Acción de ventana desconocida: {action}")
        return win

    # -- elementos (UI Automation) -----------------------------------------

    def inspect(self) -> Snapshot:
        with self._lock:
            win = self.target()
            snap = Snapshot(win)
            if win is None or not WINDOWS:
                self._snapshot = snap
                return snap
            import uiautomation as auto

            with auto.UIAutomationInitializerInThread(debug=False):
                root = auto.ControlFromHandle(win.hwnd)
                deadline = time.monotonic() + WALK_SECONDS
                visited = 0
                seen: set[tuple[str, str, int, int]] = set()
                for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=40):
                    visited += 1
                    if visited > WALK_NODES or time.monotonic() > deadline:
                        snap.truncated = True
                        break
                    element = self._element(ctrl, len(snap.elements) + 1)
                    if element is None:
                        continue
                    key = (element.kind, element.name, element.x // 8, element.y // 8)
                    if key in seen:
                        continue
                    seen.add(key)
                    snap.elements.append(element)
                    if len(snap.elements) >= MAX_ELEMENTS:
                        snap.truncated = True
                        break
            self._focus = win.hwnd
            self._snapshot = snap
            return snap

    @staticmethod
    def _element(ctrl: Any, index: int) -> Element | None:
        try:
            ctype = ctrl.ControlTypeName
            kind = _KINDS.get(ctype)
            if kind is None or ctrl.IsOffscreen or not ctrl.IsEnabled:
                return None
            name = short(ctrl.Name or "", NAME_CHARS)
            value = ""
            if ctype in _UNNAMED_OK:
                if not name:
                    name = short(ctrl.HelpText or ctrl.AutomationId or "", NAME_CHARS)
                if ctype != "DocumentControl" and not getattr(ctrl, "IsPassword", False):
                    try:
                        value = short(ctrl.GetValuePattern().Value, 40)
                    except Exception:  # noqa: BLE001
                        value = ""
            elif not name:
                return None
            rect = ctrl.BoundingRectangle
            if rect.width() <= 2 or rect.height() <= 2:
                return None
            x, y = rect.xcenter(), rect.ycenter()
        except Exception:  # noqa: BLE001 - un control que muere a mitad de lectura
            return None
        return Element(index, kind, name or "(sin nombre)", int(x), int(y), value)

    def element(self, index: int) -> Element:
        snap = self._snapshot
        if snap is None or not snap.elements:
            raise DesktopError("Primero hay que mirar la ventana con pc_inspect.")
        for e in snap.elements:
            if e.index == index:
                if snap.window:
                    self._focus = snap.window.hwnd
                return e
        raise DesktopError(f"No hay elemento {index}; vuelve a mirar con pc_inspect.")

    def element_by_name(self, query: str) -> Element | None:
        snap = self._snapshot
        if snap is None or time.monotonic() - snap.at > 2 or not snap.elements:
            snap = self.inspect()
        names = [f"{e.name} {e.kind}" for e in snap.elements]
        index = best_match(query, names)
        return snap.elements[index] if index is not None else None

    # -- entrada ---------------------------------------------------------------

    @staticmethod
    def _gui() -> Any:
        import pyautogui

        pyautogui.FAILSAFE = True  # ratón a la esquina superior izquierda = parar
        pyautogui.PAUSE = 0.04
        return pyautogui

    def click(self, x: int, y: int, *, button: str = "left", double: bool = False) -> None:
        gui = self._gui()
        width, height = gui.size()
        if not (0 <= x < width and 0 <= y < height):
            raise DesktopError(f"El punto ({x}, {y}) está fuera de la pantalla ({width}×{height}).")
        if self._focus and WINDOWS and _user32().GetForegroundWindow() != self._focus:
            wins = {w.hwnd: w for w in self.windows()}
            if self._focus in wins:
                self.activate(wins[self._focus])
        gui.click(x=x, y=y, clicks=2 if double else 1, interval=0.08, button=button)
        self._snapshot = None

    def type_text(self, text: str) -> None:
        if not WINDOWS:
            self._gui().write(text, interval=0.01)
            return
        _send_unicode(text)
        self._snapshot = None

    def hotkey(self, keys: list[str], times: int = 1) -> None:
        gui = self._gui()
        unknown = [k for k in keys if k not in gui.KEYBOARD_KEYS]
        if unknown:
            raise DesktopError(f"Tecla desconocida: {', '.join(unknown)}")
        for _ in range(times):
            if len(keys) == 1:
                gui.press(keys[0])
            else:
                gui.hotkey(*keys, interval=0.03)
            time.sleep(0.05)
        self._snapshot = None

    def scroll(self, direction: str, amount: int, at: tuple[int, int] | None = None) -> None:
        gui = self._gui()
        if at is None:
            win = self.target()
            if win is not None and WINDOWS:
                rect = wintypes.RECT()
                _user32().GetWindowRect(win.hwnd, ctypes.byref(rect))
                at = ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        if at is not None:
            gui.moveTo(*at)
        step = 120 * amount
        if direction in ("up", "down"):
            gui.scroll(step if direction == "up" else -step)
        else:
            gui.hscroll(step if direction == "right" else -step)
        self._snapshot = None

    # -- pantalla --------------------------------------------------------------

    @staticmethod
    def screenshot(
        max_width: int = 1280, region: tuple[int, int, int, int] | None = None
    ) -> tuple[str, int, int]:
        """(JPEG en base64, ancho real, alto real) del monitor principal o de
        ``region`` = (izquierda, arriba, ancho, alto) en píxeles de pantalla."""
        import mss
        from PIL import Image

        with mss.mss() as grab:
            mon = grab.monitors[1]
            if region is not None:
                left, top, w, h = region
                mon = {"left": mon["left"] + left, "top": mon["top"] + top, "width": w, "height": h}
            shot = grab.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.rgb)
        width, height = img.size
        if width > max_width:
            img = img.resize((max_width, round(height * max_width / width)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode("ascii"), width, height

    # -- apps y archivos -------------------------------------------------------

    def apps(self) -> list[tuple[str, str]]:
        """(nombre, AppID) del menú Inicio, leído una vez."""
        if self._apps is None:
            if not WINDOWS:
                self._apps = []
            else:
                out = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
                        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress",
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                )
                try:
                    data = json.loads(out.stdout or "[]")
                except ValueError:
                    data = []
                if isinstance(data, dict):
                    data = [data]
                self._apps = [
                    (str(d.get("Name") or ""), str(d.get("AppID") or ""))
                    for d in data
                    if d.get("Name") and d.get("AppID")
                ]
        return self._apps

    def find_site_window(self, site: str) -> Window | None:
        """Ventana de navegador cuya pestaña activa es ``site`` (YouTube, Gmail…)."""
        if not WINDOWS or not site:
            return None
        for w in self.windows():
            if w.process.lower() in _BROWSERS and not _is_own(w) and site in w.title:
                return w
        return None

    def title_of(self, hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        _user32().GetWindowTextW(hwnd, buf, 512)
        return buf.value

    def find_site_tab(self, site: str) -> Window | None:
        """Ventana de navegador con una pestaña de ``site``, ya seleccionada y delante.

        Windows solo enseña el título de la pestaña activa, y mientras se habla con
        Crisvis la activa suele ser la suya: se busca en la tira de pestañas con UI
        Automation y se selecciona la del sitio.
        """
        if not WINDOWS or not site:
            return None
        front = self.find_site_window(site)
        if front is not None:
            self.activate(front)
            return front
        import uiautomation as auto

        with auto.UIAutomationInitializerInThread(debug=False):
            for w in self.windows():
                if w.process.lower() not in _BROWSERS:
                    continue
                tab = auto.ControlFromHandle(w.hwnd).TabItemControl(searchDepth=14, SubName=site)
                if not tab.Exists(0.5, 0.1):
                    continue
                self.activate(w)
                try:
                    tab.GetSelectionItemPattern().Select()
                except Exception:  # noqa: BLE001 - navegadores sin el patrón de selección
                    tab.Click(simulateMove=False)
                time.sleep(0.3)
                return Window(w.hwnd, self.title_of(w.hwnd), w.process, False)
        return None

    def open_web(self, url: str) -> str:
        """Abre ``url`` en la pestaña de ese sitio si ya hay una abierta.

        Sin pestaña de ese sitio, una pestaña nueva en el navegador predeterminado.
        En la pestaña existente solo se escribe una URL https validada en la barra de
        direcciones (Ctrl+L), y solo si esa ventana quedó delante con la pestaña del
        sitio activa.
        """
        site = site_name(url)
        win = self.find_site_tab(site) if site and safe_address(url) else None
        if (
            win is None
            or _user32().GetForegroundWindow() != win.hwnd
            or site not in self.title_of(win.hwnd)
        ):
            webbrowser.open(url)
            return f"la página {short(url, 80)} en una pestaña nueva"
        if not is_site_home(url):
            self.hotkey(["ctrl", "l"])
            self.type_text(url)
            # Quita el autocompletado que la barra añade detrás de lo escrito.
            self.hotkey(["delete"])
            self.hotkey(["enter"])
        return f"{site} en la pestaña que ya estaba abierta"

    def open(self, target: str, check: Callable[[str], str | None] | None = None) -> str:
        """Abre una URL, un archivo, una carpeta o una app. Devuelve qué abrió.

        ``check(nombre)`` devuelve el motivo para no lanzar una app ya resuelta
        (p. ej. "Terminal" encontrado en el menú Inicio), o None.
        """
        target = target.strip()
        if not target:
            raise DesktopError("No se indicó qué abrir.")
        url = web_url(target)
        if url is not None:
            return self.open_web(url)
        path = resolve_path(target)
        if path.exists():
            _startfile(str(path))
            return f"{'la carpeta' if path.is_dir() else 'el archivo'} {path.name or path}"
        apps = self.apps()
        index = best_match(target, [name for name, _ in apps], cutoff=0.7)
        if index is not None:
            name, app_id = apps[index]
            refusal = (check(name) or check(app_id.split("!")[0])) if check else None
            if refusal:
                raise DesktopError(f"Bloqueado por política: {refusal}.")
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
            return f"la aplicación {name}"
        if check is not None:
            # Sin coincidencia en el menú Inicio, Windows resolvería el nombre
            # por PATH/App Paths: eso es lanzar un ejecutable arbitrario.
            raise DesktopError(
                f"No encuentro «{target}» en el menú Inicio; no lanzo ejecutables por nombre."
            )
        try:
            _startfile(target)
        except OSError as exc:
            raise DesktopError(
                f"No encuentro ninguna aplicación ni archivo llamado «{target}»."
            ) from exc
        return target

    def wait_for_window(self, before: set[int], seconds: float = 4.0) -> Window | None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(0.4)
            try:
                fresh = [w for w in self.windows() if w.hwnd not in before and not _is_own(w)]
            except DesktopError:
                return None
            if fresh:
                self._focus = fresh[0].hwnd
                return fresh[0]
        return None


def _startfile(target: str) -> None:
    if WINDOWS:
        os.startfile(target)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", target])


# -- teclado Unicode (Windows) --------------------------------------------------
# pyautogui.write no sabe escribir "ñ" ni tildes; SendInput con KEYEVENTF_UNICODE sí.

if WINDOWS:
    from ctypes import wintypes

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send_unicode(text: str) -> None:
    keyeventf_unicode, keyeventf_keyup = 0x0004, 0x0002
    vk_return = 0x0D
    events = []

    def key(vk: int = 0, scan: int = 0, flags: int = 0) -> None:
        events.append(_INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, None))))

    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            key(vk=vk_return)
            key(vk=vk_return, flags=keyeventf_keyup)
            continue
        data = ch.encode("utf-16-le")
        for i in range(0, len(data), 2):
            unit = int.from_bytes(data[i : i + 2], "little")
            key(scan=unit, flags=keyeventf_unicode)
            key(scan=unit, flags=keyeventf_unicode | keyeventf_keyup)
    send = _user32().SendInput
    for start in range(0, len(events), 64):
        chunk = events[start : start + 64]
        array = (_INPUT * len(chunk))(*chunk)
        send(len(chunk), array, ctypes.sizeof(_INPUT))
        time.sleep(0.01)


# -- sistema ---------------------------------------------------------------------


def volume(level: int | None = None, *, mute: bool | None = None) -> tuple[int, bool]:
    """Fija y devuelve (volumen 0-100, silenciado)."""
    if not WINDOWS:
        raise DesktopError("El volumen solo se controla en Windows.")
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        if level is not None:
            endpoint.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100, None)
        if mute is not None:
            endpoint.SetMute(int(mute), None)
        return round(endpoint.GetMasterVolumeLevelScalar() * 100), bool(endpoint.GetMute())
    finally:
        comtypes.CoUninitialize()


DESKTOP = Desktop()
