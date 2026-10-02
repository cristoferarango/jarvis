"""Las herramientas `ui_*`: el control de JARVIS sobre su propia cara.

No es decoración: una interfaz que se pone roja antes de que diga una palabra
transmite significado más rápido que la voz. Cada herramienta emite exactamente
una trama {type: 'ui', op, args}, el contrato que la cara ya entiende.
"""

from __future__ import annotations

import itertools
from typing import Any

from crisvis.body.base import (
    MISSING,
    FaceTool,
    clamp,
    new_id,
    ok,
    refuse,
    schema,
    to_bool,
    to_colour,
)
from crisvis.body.surface import Surface

PHASES = {
    "offline": "antes de desbloquear el audio",
    "boot": "la secuencia de arranque",
    "dormant": "en reposo, esperando la palabra de activación",
    "waking": "acaba de oír su nombre",
    "listening": "escuchando",
    "thinking": "pensando la respuesta",
    "tooling": "ejecutando una herramienta",
    "speaking": "hablando",
}

GOLDEN_ANGLE = 137.507764
_orbit_seq = itertools.count()

THEME = """Retiñe toda la interfaz. accent sustituye el color de fase en todas partes;
background aclara u oscurece el fondo (debe seguir siendo oscuro); phase_colors cambia el color
de fases concretas. Úsalo cuando el color SIGNIFIQUE algo (rojo por un fallo, ámbar esperando).
Restáuralo con ui_reset cuando pase el momento. No lo anuncies."""

REACTOR = """Cambia el reactor central: color, scale (0.2-3), intensity (0-3), spin (0-5),
style (ring | sphere | wire), visible. Cambia una cosa cada vez y con intención."""

ORBIT = """Pon en órbita alrededor del reactor una imagen que TÚ hayas generado o capturado
(ruta absoluta o file:///). Las imágenes web se rechazan. action: add | remove | clear.
Tres o cuatro objetos como mucho; quítalos cuando cambie el tema."""

CHROME = """Muestra u oculta el mobiliario: systems, transcript, tool_badge, suggestions, brand
(true = mostrar, false = ocultar). Ocúltalo solo cuando la ausencia ayude y vuelve a mostrarlo."""

EFFECT = """Un efecto puntual: glitch (datos corruptos), pulse (algo completado), scan (analizando),
shake (impacto; una vez), flash (alerta). Como mucho uno por turno."""

SCREEN = """Despeja la pantalla: panels, transcript o all."""

RESET = (
    """Devuelve toda la interfaz a su estado original (colores, reactor, órbitas, mobiliario)."""
)


def interface_tools(surface: Surface) -> list[FaceTool]:
    def emit(op: str, args: dict[str, Any]) -> None:
        surface.push({"type": "ui", "op": op, "args": args})

    def put(target: dict[str, Any], key: str, value: Any) -> None:
        if value is not MISSING and value is not None:
            target[key] = value

    def theme(args: dict[str, Any]) -> Any:
        patch: dict[str, Any] = {}
        for key in ("accent", "background"):
            colour = to_colour(args.get(key), present=key in args)
            if colour is not MISSING:
                patch[key] = colour
        palette = {}
        for phase, value in (args.get("phase_colors") or {}).items():
            colour = to_colour(value, present=True)
            if phase in PHASES and isinstance(colour, str):
                palette[phase] = colour
        if palette:
            patch["palette"] = palette
        if not patch:
            return ok("ui_theme", "Sin cambios: no se dio ningún color.")
        emit("patch", patch)
        return ok("ui_theme", "Interfaz retintada.")

    def reactor(args: dict[str, Any]) -> Any:
        r: dict[str, Any] = {}
        colour = to_colour(args.get("color"), present="color" in args)
        if colour is not MISSING:
            r["color"] = colour
        put(r, "scale", clamp(args.get("scale"), 0.2, 3))
        put(r, "intensity", clamp(args.get("intensity"), 0, 3))
        put(r, "spin", clamp(args.get("spin"), 0, 5))
        if args.get("style") in ("ring", "sphere", "wire"):
            r["style"] = args["style"]
        put(r, "visible", to_bool(args.get("visible")))
        if not r:
            return ok("ui_reactor", "Sin cambios: no se dio ninguna propiedad.")
        emit("patch", {"reactor": r})
        return ok("ui_reactor", "Reactor ajustado.")

    def orbit(args: dict[str, Any]) -> Any:
        action = args.get("action") or "add"
        if action == "clear":
            emit("orbit", {"action": "clear"})
            return ok("ui_orbit", "Órbitas despejadas.")
        if action == "remove":
            oid = str(args.get("id") or "").strip()
            if not oid:
                return refuse(
                    "ui_orbit",
                    'No eliminado: remove necesita el id. Usa action "clear" para quitarlos todos.',
                )
            emit("orbit", {"action": "remove", "id": oid})
            return ok("ui_orbit", "Órbita eliminada.")
        src = str(args.get("src") or "").strip()
        if not src:
            return refuse("ui_orbit", "No añadido: falta src (ruta absoluta o file:///).")
        if src.lower().startswith(("http://", "https://")):
            return refuse(
                "ui_orbit",
                "No añadido: las imágenes remotas están bloqueadas. Usa un archivo de esta "
                "máquina que hayas generado o capturado.",
            )
        n = next(_orbit_seq)
        angle = clamp(args.get("phase"), -1e6, 1e6)
        obj = {
            "action": "add",
            "id": str(args.get("id") or "").strip() or new_id("o"),
            "src": src,
            "radius": clamp(args.get("radius"), 0.1, 1.2) or 0.55,
            "speed": clamp(args.get("speed"), -30, 30) if args.get("speed") is not None else 4,
            "size": clamp(args.get("size"), 16, 400) or 96,
            "tilt": clamp(args.get("tilt"), -80, 80) if args.get("tilt") is not None else 24,
            "opacity": clamp(args.get("opacity"), 0, 1) if args.get("opacity") is not None else 0.9,
            "phase": (n * GOLDEN_ANGLE) % 360 if angle is None else angle % 360,
        }
        emit("orbit", obj)
        return ok("ui_orbit", f'En órbita como "{obj["id"]}".')

    def chrome(args: dict[str, Any]) -> Any:
        c: dict[str, Any] = {}
        for src, dst in (
            ("systems", "systems"),
            ("transcript", "transcript"),
            ("tool_badge", "toolBadge"),
            ("suggestions", "suggestions"),
            ("brand", "brand"),
        ):
            put(c, dst, to_bool(args.get(src)))
        if not c:
            return ok("ui_chrome", "Sin cambios: no se nombró nada.")
        emit("patch", {"chrome": c})
        return ok("ui_chrome", "Mobiliario actualizado.")

    def effect(args: dict[str, Any]) -> Any:
        kind = args.get("kind")
        emit(
            "effect",
            {"kind": kind if kind in ("glitch", "pulse", "scan", "shake", "flash") else "pulse"},
        )
        return ok("ui_effect", "Hecho.")

    def screen(args: dict[str, Any]) -> Any:
        what = args.get("what")
        emit("screen", {"what": what if what in ("all", "panels", "transcript") else "all"})
        return ok("ui_screen", "Despejado.")

    def reset(_: dict[str, Any]) -> Any:
        emit("reset", {})
        return ok("ui_reset", "Interfaz restaurada.")

    colour = {"type": ["string", "null"]}
    num = {"type": ["number", "string"]}
    flag = {"type": ["boolean", "string", "number"]}

    return [
        FaceTool(
            "ui_theme",
            THEME,
            schema(
                {
                    "accent": colour,
                    "background": colour,
                    "phase_colors": {
                        "type": "object",
                        "properties": {p: colour for p in PHASES},
                    },
                }
            ),
            theme,
        ),
        FaceTool(
            "ui_reactor",
            REACTOR,
            schema(
                {
                    "color": colour,
                    "scale": num,
                    "intensity": num,
                    "spin": num,
                    "style": {"type": "string", "enum": ["ring", "sphere", "wire"]},
                    "visible": flag,
                }
            ),
            reactor,
        ),
        FaceTool(
            "ui_orbit",
            ORBIT,
            schema(
                {
                    "action": {"type": "string", "enum": ["add", "remove", "clear"]},
                    "id": {"type": "string"},
                    "src": {"type": "string"},
                    "radius": num,
                    "speed": num,
                    "size": num,
                    "tilt": num,
                    "opacity": num,
                    "phase": num,
                }
            ),
            orbit,
        ),
        FaceTool(
            "ui_chrome",
            CHROME,
            schema(
                {
                    "systems": flag,
                    "transcript": flag,
                    "tool_badge": flag,
                    "suggestions": flag,
                    "brand": flag,
                }
            ),
            chrome,
        ),
        FaceTool(
            "ui_effect",
            EFFECT,
            schema(
                {"kind": {"type": "string", "enum": ["glitch", "pulse", "scan", "shake", "flash"]}},
                ["kind"],
            ),
            effect,
        ),
        FaceTool(
            "ui_screen",
            SCREEN,
            schema({"what": {"type": "string", "enum": ["all", "panels", "transcript"]}}),
            screen,
        ),
        FaceTool("ui_reset", RESET, schema({}), reset),
    ]
