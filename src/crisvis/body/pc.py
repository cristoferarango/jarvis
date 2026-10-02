"""Las manos: herramientas ``pc_*`` para manejar el equipo como lo haría una persona.

El flujo que el modelo sigue: ``pc_open`` o ``pc_window`` para llegar a la
ventana, ``pc_inspect`` para ver sus botones y campos numerados, y
``pc_click`` / ``pc_type`` / ``pc_keys`` para actuar. Si la ventana no expone
sus controles (juegos, lienzos, vídeo), ``pc_screen`` la mira con el modelo de
visión y ``pc_click`` con ``target`` localiza el punto en la captura.

Cada herramienta tiene su nivel en la política de permisos: mirar es lectura,
tocar es escritura, apagar o matar procesos es peligroso.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from crisvis.body.base import FaceTool, clamp, ok, refuse, schema, to_bool
from crisvis.body.desktop import (
    DESKTOP,
    WINDOWS,
    Desktop,
    DesktopError,
    parse_keys,
    resolve_path,
    short,
    volume,
)

# (imagen_base64, pregunta) -> respuesta
SeeFn = Callable[[str, str], str]

# Recorte de la segunda pasada de localización, en píxeles de pantalla.
ZOOM_W, ZOOM_H = 640, 400

_SKIP_DIRS = {"appdata", "node_modules", ".git", "$recycle.bin", "windows", ".venv", "__pycache__"}


def parse_point(reply: str | None) -> tuple[float, float] | None:
    """Los dos primeros números de la respuesta, en 0-1000.

    Los modelos pequeños escriben el JSON a medias ('{"x": 500, 930}'); lo que
    importa son los dos números, no la sintaxis.
    """
    found = re.search(r"\{[^{}]*\}", reply or "")
    if not found or "null" in found.group(0):
        return None
    numbers = re.findall(r"-?\d+(?:\.\d+)?", found.group(0))
    if len(numbers) < 2:
        return None
    x, y = float(numbers[0]), float(numbers[1])
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    return x, y


def _guard(name: str, fn: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
    """Traduce los fallos del escritorio en una frase para el modelo."""

    def run(args: dict[str, Any]) -> Any:
        try:
            return fn(args)
        except DesktopError as exc:
            return refuse(name, str(exc))
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "FailSafeException":
                return refuse(
                    name,
                    "El usuario detuvo el control llevando el ratón a la esquina. No sigas "
                    "actuando sobre el equipo en este turno.",
                )
            return refuse(name, f"Falló: {exc}")

    return run


def _describe(desk: Desktop) -> str:
    snap = desk.inspect()
    if snap.window is None:
        return "No hay ninguna ventana abierta aparte de Crisvis."
    lines = [f"Ventana activa: «{snap.window.label}»."]
    others = [w.label for w in desk.windows() if w.hwnd != snap.window.hwnd][:12]
    if others:
        lines.append("Otras ventanas: " + "; ".join(f"«{t}»" for t in others) + ".")
    if snap.elements:
        lines.append("Elementos (usa el número con pc_click o pc_type):")
        lines += [e.label for e in snap.elements]
        if snap.truncated:
            lines.append("(hay más; desplázate o usa pc_screen si no ves lo que buscas)")
    else:
        lines.append(
            "Esta ventana no expone sus controles. Usa pc_screen para verla y pc_click con "
            "target para pulsar."
        )
    return "\n".join(lines)


def pc_tools(see: SeeFn | None, desk: Desktop = DESKTOP) -> list[FaceTool]:
    # -- mirar -------------------------------------------------------------------

    def inspect(_args: dict[str, Any]) -> Any:
        return ok("pc_inspect", _describe(desk))

    def screen(args: dict[str, Any]) -> Any:
        if see is None:
            return refuse(
                "pc_screen",
                "No hay modelo de visión (cerebro.modelo_vision). Usa pc_inspect en su lugar.",
            )
        desk.ensure_target()
        image, _w, _h = desk.screenshot()
        question = str(args.get("question") or "").strip()[:200] or (
            "Describe qué hay en la pantalla: qué aplicación, qué se ve y qué se puede pulsar."
        )
        prompt = (
            "Esta es una captura de la pantalla del ordenador del usuario. "
            f"{question} Responde en español, breve y concreto."
        )
        return ok("pc_screen", f"En la pantalla: {see(image, prompt)}")

    def ground(image: str, target: str) -> tuple[float, float] | None:
        """Centro de ``target`` en la imagen, en 0-1000 (la escala de Qwen-VL)."""
        assert see is not None
        reply = see(
            image,
            f"Localiza en esta captura de pantalla: «{target}». Responde SOLO con JSON "
            '{"x": X, "y": Y} con el centro del elemento en coordenadas relativas de 0 a 1000. '
            'Si no aparece, responde {"x": null, "y": null}.',
        )
        return parse_point(reply)

    def locate(target: str) -> tuple[int, int] | None:
        """Dos pasadas: la pantalla entera para saber dónde, y un recorte a
        resolución completa alrededor para acertar en controles pequeños."""
        if see is None:
            return None
        desk.ensure_target()
        image, width, height = desk.screenshot()
        rough = ground(image, target)
        if rough is None:
            return None
        cx, cy = rough[0] * width / 1000, rough[1] * height / 1000
        zw, zh = min(ZOOM_W, width), min(ZOOM_H, height)
        left = int(min(max(cx - zw / 2, 0), width - zw))
        top = int(min(max(cy - zh / 2, 0), height - zh))
        crop, _cw, _ch = desk.screenshot(max_width=zw, region=(left, top, zw, zh))
        fine = ground(crop, target)
        if fine is None:
            return round(cx), round(cy)
        return round(left + fine[0] * zw / 1000), round(top + fine[1] * zh / 1000)

    def system(args: dict[str, Any]) -> Any:
        import psutil

        what = str(args.get("info") or "resumen").lower()
        if what.startswith("proc"):
            procs = []
            for p in psutil.process_iter(["name", "memory_info"]):
                mem = p.info.get("memory_info")
                if p.info.get("name") and mem:
                    procs.append((mem.rss, p.info["name"]))
            totals: dict[str, int] = {}
            for rss, name in procs:
                totals[name] = totals.get(name, 0) + rss
            top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:15]
            return ok(
                "pc_system",
                "Procesos por memoria: "
                + "; ".join(f"{n} {r / 2**20:.0f} MB" for n, r in top),
            )
        if what.startswith("port") or what.startswith("clip"):
            import pyperclip

            text = pyperclip.paste() or ""
            if not text:
                return ok("pc_system", "El portapapeles está vacío.")
            return ok("pc_system", f"Portapapeles: «{short(text, 1500)}»")
        parts = [f"CPU al {psutil.cpu_percent(interval=0.3):.0f} %"]
        mem = psutil.virtual_memory()
        parts.append(f"memoria {mem.used / 2**30:.1f} de {mem.total / 2**30:.0f} GB")
        try:
            disk = psutil.disk_usage(str(Path.home().anchor or "/"))
            parts.append(f"disco principal con {disk.free / 2**30:.0f} GB libres")
        except OSError:
            pass
        battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        if battery is not None:
            plugged = "enchufado" if battery.power_plugged else "con batería"
            parts.append(f"batería al {battery.percent:.0f} % ({plugged})")
        uptime = time.time() - psutil.boot_time()
        parts.append(f"encendido hace {uptime / 3600:.0f} horas")
        if WINDOWS:
            try:
                level, muted = volume()
                parts.append(f"volumen {level} %" + (" silenciado" if muted else ""))
            except Exception:  # noqa: BLE001
                pass
            win = desk.target()
            if win:
                parts.append(f"ventana activa «{win.label}»")
        return ok("pc_system", ", ".join(parts) + ".")

    def find_files(args: dict[str, Any]) -> Any:
        query = str(args.get("query") or "").strip()
        folder = resolve_path(str(args.get("folder") or "~"))
        if not folder.is_dir():
            return refuse("pc_find_files", f"No existe la carpeta {folder}.")
        if not query:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            listed = [f"{p.name}{'/' if p.is_dir() else ''}" for p in entries[:60]]
            more = f" (y {len(entries) - 60} más)" if len(entries) > 60 else ""
            return ok("pc_find_files", f"{folder}: " + ", ".join(listed) + more)
        pattern = query if any(c in query for c in "*?") else f"*{query}*"
        found: list[str] = []
        deadline = time.monotonic() + 6
        stack = [folder]
        while stack and len(found) < 30 and time.monotonic() < deadline:
            current = stack.pop()
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.match(pattern) or child.name.lower().find(query.lower()) >= 0:
                    found.append(str(child))
                if child.is_dir() and child.name.lower() not in _SKIP_DIRS:
                    stack.append(child)
        if not found:
            return ok("pc_find_files", f"No hay nada que coincida con «{query}» en {folder}.")
        return ok("pc_find_files", "Encontrado: " + "; ".join(found[:30]))

    # -- tocar -------------------------------------------------------------------

    def open_(args: dict[str, Any]) -> Any:
        target = str(args.get("target") or "")
        before = {w.hwnd for w in desk.windows()} if WINDOWS else set()
        what = desk.open(target)
        win = desk.wait_for_window(before) if WINDOWS else None
        tail = f" Ventana: «{win.label}»." if win else ""
        return ok("pc_open", f"Abierto {what}.{tail}")

    def window(args: dict[str, Any]) -> Any:
        action = str(args.get("action") or "focus").lower()
        name = str(args.get("window") or "")
        if not name:
            return refuse("pc_window", "Falta el nombre de la ventana.")
        win = desk.window_action(name, action)
        verbs = {
            "focus": "Al frente",
            "minimize": "Minimizada",
            "maximize": "Maximizada",
            "restore": "Restaurada",
            "close": "Cerrando",
        }
        return ok("pc_window", f"{verbs.get(action, action)}: «{win.label}».")

    def click(args: dict[str, Any]) -> Any:
        button = str(args.get("button") or "left").lower()
        button = {"izquierdo": "left", "derecho": "right", "central": "middle"}.get(button, button)
        if button not in ("left", "right", "middle"):
            button = "left"
        double = bool(to_bool(args.get("double")))
        label = ""
        index = clamp(args.get("element"), 1, 10_000)
        target = str(args.get("target") or "").strip()
        x, y = clamp(args.get("x"), 0, 100_000), clamp(args.get("y"), 0, 100_000)
        if index is not None:
            element = desk.element(int(index))
            point, label = (element.x, element.y), f"«{element.name}»"
        elif target:
            desk.ensure_target()
            element = desk.element_by_name(target)
            if element is not None:
                point, label = (element.x, element.y), f"«{element.name}»"
            else:
                found = locate(target)
                if found is None:
                    return refuse(
                        "pc_click",
                        f"No encuentro «{target}» en la ventana. Mira con pc_inspect o pc_screen.",
                    )
                point, label = found, f"«{target}» (localizado en la captura)"
        elif x is not None and y is not None:
            desk.ensure_target()
            point, label = (int(x), int(y)), f"({int(x)}, {int(y)})"
        else:
            return refuse("pc_click", "Indica element (número de pc_inspect), target o x e y.")
        desk.click(*point, button=button, double=double)
        time.sleep(0.4)
        win = desk.target()
        after = f" Ventana activa: «{win.label}»." if win else ""
        kind = "Doble clic" if double else ("Clic derecho" if button == "right" else "Clic")
        return ok("pc_click", f"{kind} en {label}.{after}")

    def type_(args: dict[str, Any]) -> Any:
        text = str(args.get("text") or "")
        if not text:
            return refuse("pc_type", "No hay texto que escribir.")
        index = clamp(args.get("element"), 1, 10_000)
        if index is not None:
            element = desk.element(int(index))
            desk.click(element.x, element.y)
            time.sleep(0.2)
        else:
            desk.ensure_target()
        win = desk.target()
        if to_bool(args.get("replace")):
            desk.hotkey(["ctrl", "a"])
        desk.type_text(text[:5000])
        if to_bool(args.get("enter")):
            desk.hotkey(["enter"])
        where = f" en «{win.label}»" if win else ""
        return ok("pc_type", f"Escrito{where} ({len(text)} caracteres).")

    def keys(args: dict[str, Any]) -> Any:
        spec = str(args.get("keys") or "").strip()
        if not spec:
            return refuse("pc_keys", "Indica las teclas, por ejemplo ctrl+s.")
        times = int(clamp(args.get("times"), 1, 50) or 1)
        combo = parse_keys(spec)
        if combo and combo[0] != "win":
            desk.ensure_target()
        desk.hotkey(combo, times)
        repeat = f" {times} veces." if times > 1 else "."
        return ok("pc_keys", f"Pulsado {'+'.join(combo)}{repeat}")

    def scroll(args: dict[str, Any]) -> Any:
        direction = str(args.get("direction") or "down").lower()
        direction = {"arriba": "up", "abajo": "down", "izquierda": "left", "derecha": "right"}.get(
            direction, direction
        )
        if direction not in ("up", "down", "left", "right"):
            direction = "down"
        amount = int(clamp(args.get("amount"), 1, 30) or 5)
        index = clamp(args.get("element"), 1, 10_000)
        at = None
        if index is not None:
            element = desk.element(int(index))
            at = (element.x, element.y)
        desk.ensure_target()
        desk.scroll(direction, amount, at)
        return ok("pc_scroll", "Desplazado.")

    def media(args: dict[str, Any]) -> Any:
        action = str(args.get("action") or "").lower()
        level = clamp(args.get("level"), 0, 100)
        if action == "set_volume" or (level is not None and not action):
            if level is None:
                return refuse("pc_media", "Falta level (0-100).")
            now, _muted = volume(int(level), mute=False)
            return ok("pc_media", f"Volumen al {now} %.")
        if action in ("mute", "unmute"):
            now, muted = volume(mute=action == "mute")
            return ok("pc_media", "Silenciado." if muted else f"Sonido activado, volumen {now} %.")
        keymap = {
            "play_pause": "playpause",
            "next": "nexttrack",
            "previous": "prevtrack",
            "stop": "stop",
            "volume_up": "volumeup",
            "volume_down": "volumedown",
        }
        key = keymap.get(action)
        if key is None:
            return refuse("pc_media", f"Acción desconocida: {action}")
        presses = 5 if key.startswith("volume") else 1
        desk.hotkey([key], presses)
        return ok("pc_media", "Hecho.")

    def file_manage(args: dict[str, Any]) -> Any:
        action = str(args.get("action") or "").lower()
        raw = str(args.get("path") or "")
        if not raw:
            return refuse("pc_file_manage", "Falta path.")
        path = resolve_path(raw)
        dest_raw = str(args.get("dest") or "")
        if action == "mkdir":
            path.mkdir(parents=True, exist_ok=True)
            return ok("pc_file_manage", f"Carpeta creada: {path.name}.")
        if not path.exists():
            return refuse("pc_file_manage", f"No existe {path}.")
        if action == "trash":
            from send2trash import send2trash

            send2trash(str(path))
            return ok("pc_file_manage", f"{path.name} está en la papelera.")
        if not dest_raw:
            return refuse("pc_file_manage", "Falta dest.")
        if action == "rename":
            dest = path.with_name(Path(dest_raw).name)
        else:
            dest = resolve_path(dest_raw)
            if dest.is_dir():
                dest = dest / path.name
        if dest.exists():
            return refuse("pc_file_manage", f"Ya existe {dest}; no se sobrescribe.")
        if action in ("move", "rename"):
            shutil.move(str(path), str(dest))
        elif action == "copy":
            if path.is_dir():
                shutil.copytree(path, dest)
            else:
                shutil.copy2(path, dest)
        else:
            return refuse("pc_file_manage", f"Acción desconocida: {action}")
        verb = {"move": "Movido", "rename": "Renombrado", "copy": "Copiado"}[action]
        return ok("pc_file_manage", f"{verb}: {path.name} → {dest}.")

    # -- peligroso ---------------------------------------------------------------

    def kill(args: dict[str, Any]) -> Any:
        import psutil

        name = str(args.get("process") or "").strip().lower()
        if not name:
            return refuse("pc_kill", "Falta el nombre del proceso.")
        stem = name.removesuffix(".exe")
        protected = {"crisvis", "python", "explorer", "winlogon", "csrss", "lsass", "ollama"}
        if stem in protected or stem.startswith("system"):
            return refuse("pc_kill", f"No se cierra {name}: el equipo o Crisvis dependen de él.")
        victims = [
            p
            for p in psutil.process_iter(["name"])
            if (p.info.get("name") or "").lower().removesuffix(".exe") == stem
        ]
        if not victims:
            return refuse("pc_kill", f"No hay ningún proceso llamado {name}.")
        for p in victims:
            try:
                p.terminate()
            except psutil.Error:
                pass
        _gone, alive = psutil.wait_procs(victims, timeout=4)
        for p in alive:
            try:
                p.kill()
            except psutil.Error:
                pass
        return ok("pc_kill", f"Cerrado {name} ({len(victims)} proceso(s)).")

    def power(args: dict[str, Any]) -> Any:
        action = str(args.get("action") or "").lower()
        if not WINDOWS:
            return refuse("pc_power", "Solo disponible en Windows.")
        commands = {
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            "restart": ["shutdown", "/r", "/t", "15"],
            "shutdown": ["shutdown", "/s", "/t", "15"],
            "logout": ["shutdown", "/l"],
            "cancel": ["shutdown", "/a"],
        }
        if action == "lock":
            import ctypes

            ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
            return ok("pc_power", "Equipo bloqueado.")
        cmd = commands.get(action)
        if cmd is None:
            return refuse("pc_power", f"Acción desconocida: {action}")
        subprocess.Popen(cmd, creationflags=0x08000000)
        said = {
            "sleep": "Suspendiendo.",
            "restart": "Reinicio en quince segundos; se puede cancelar.",
            "shutdown": "Apagado en quince segundos; se puede cancelar.",
            "logout": "Cerrando la sesión.",
            "cancel": "Apagado cancelado.",
        }
        return ok("pc_power", said[action])

    # -- catálogo ----------------------------------------------------------------

    def tool(
        name: str,
        description: str,
        props: dict[str, Any],
        fn: Any,
        required: list[str] | None = None,
        timeout: float = 30.0,
    ) -> FaceTool:
        return FaceTool(
            name,
            description,
            schema(props, required),
            _guard(name, fn),
            category="pc",
            timeout=timeout,
        )

    element = {"type": ["integer", "string"], "description": "Número según pc_inspect."}
    return [
        tool(
            "pc_inspect",
            "Mira la ventana activa del PC: su título, las demás ventanas abiertas y sus botones, "
            "campos y menús numerados. Úsala antes de pulsar o escribir.",
            {},
            inspect,
        ),
        tool(
            "pc_screen",
            "Mira la pantalla con visión y responde a una pregunta sobre ella. Para lo que "
            "pc_inspect no muestra (imágenes, vídeos, juegos, lienzos).",
            {"question": {"type": "string"}},
            screen,
            timeout=120,
        ),
        tool(
            "pc_system",
            "Estado del PC. info: resumen (CPU, memoria, disco, batería, volumen), procesos o "
            "portapapeles.",
            {"info": {"type": "string", "enum": ["resumen", "procesos", "portapapeles"]}},
            system,
        ),
        tool(
            "pc_find_files",
            "Busca archivos por nombre en una carpeta (por defecto la personal) o, sin query, "
            "lista su contenido. folder admite 'descargas', 'escritorio', 'documentos'…",
            {"query": {"type": "string"}, "folder": {"type": "string"}},
            find_files,
        ),
        tool(
            "pc_open",
            "Abre una aplicación por su nombre (Spotify, Word, calculadora…), un archivo, una "
            "carpeta o una web.",
            {"target": {"type": "string"}},
            open_,
            required=["target"],
        ),
        tool(
            "pc_window",
            "Gestiona una ventana abierta por su nombre: focus, minimize, maximize, restore o "
            "close.",
            {
                "window": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["focus", "minimize", "maximize", "restore", "close"],
                },
            },
            window,
            required=["window", "action"],
        ),
        tool(
            "pc_click",
            "Pulsa en la ventana: element (número de pc_inspect), o target (texto o descripción "
            "de lo que pulsar), o x e y. button: left/right; double: doble clic.",
            {
                "element": element,
                "target": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right"]},
                "double": {"type": "boolean"},
            },
            click,
            timeout=120,
        ),
        tool(
            "pc_type",
            "Escribe texto en la ventana activa o en el campo element. enter: pulsa Intro al "
            "final. replace: borra TODO lo que hubiera; solo si el usuario pide sustituirlo.",
            {
                "text": {"type": "string"},
                "element": element,
                "replace": {"type": "boolean"},
                "enter": {"type": "boolean"},
            },
            type_,
            required=["text"],
        ),
        tool(
            "pc_keys",
            "Pulsa una tecla o combinación en la ventana activa: 'enter', 'ctrl+s', 'alt+f4', "
            "'win+d', 'ctrl+shift+esc'. times: repeticiones.",
            {"keys": {"type": "string"}, "times": {"type": "integer"}},
            keys,
            required=["keys"],
        ),
        tool(
            "pc_scroll",
            "Desplaza la ventana activa (o el elemento element). direction: up/down/left/right; "
            "amount: 1-30.",
            {
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer"},
                "element": element,
            },
            scroll,
        ),
        tool(
            "pc_media",
            "Música y sonido del PC. action: play_pause, next, previous, stop, volume_up, "
            "volume_down, mute, unmute o set_volume con level 0-100.",
            {
                "action": {
                    "type": "string",
                    "enum": [
                        "play_pause",
                        "next",
                        "previous",
                        "stop",
                        "volume_up",
                        "volume_down",
                        "mute",
                        "unmute",
                        "set_volume",
                    ],
                },
                "level": {"type": "integer"},
            },
            media,
        ),
        tool(
            "pc_file_manage",
            "Gestiona archivos: move, copy, rename (dest = nombre nuevo), mkdir o trash (a la "
            "papelera, recuperable).",
            {
                "action": {"type": "string", "enum": ["move", "copy", "rename", "mkdir", "trash"]},
                "path": {"type": "string"},
                "dest": {"type": "string"},
            },
            file_manage,
            required=["action", "path"],
        ),
        tool(
            "pc_kill",
            "Fuerza el cierre de un programa por su nombre de proceso (spotify, chrome…). Solo si "
            "cerrar su ventana no basta.",
            {"process": {"type": "string"}},
            kill,
            required=["process"],
        ),
        tool(
            "pc_power",
            "Energía del PC: lock, sleep, restart, shutdown, logout o cancel (anula un apagado).",
            {
                "action": {
                    "type": "string",
                    "enum": ["lock", "sleep", "restart", "shutdown", "logout", "cancel"],
                }
            },
            power,
            required=["action"],
        ),
    ]
