"""`look` y `watch`: los ojos.

La cámara está en el navegador y el modelo aquí, así que es la única capacidad
que pregunta y espera en vez de empujar. El fotograma se pide a la cara, se
describe con el modelo de visión configurado en OpenJarvis y se descarta: no se
guarda ninguno.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from crisvis.body.base import FaceTool, ok, refuse, schema
from crisvis.body.surface import Surface

# (imagen_base64, pregunta) -> descripción
SeeFn = Callable[[str, str], str]

LOOK = """Mira por la cámara a quien está delante de la pantalla (un fotograma) y describe lo
que ves. Úsalo SOLO cuando el usuario te pida mirar ("¿qué tengo en la mano?", "lee esta
etiqueta"). Enciende una cámara apuntando a su cara: nunca por curiosidad."""

WATCH = """Observa por la cámara durante unos segundos y recibe una rejilla de fotogramas en
orden, para entender un movimiento ("¿lo hago bien?", "¿qué acaba de pasar?"). when: now (lo
que viene) o past (lo que ya pasó; solo si la cámara está abierta en pantalla). seconds: 2-15."""


def vision_tools(surface: Surface, see: SeeFn | None) -> list[FaceTool]:
    def capture(name: str, request: dict[str, Any], question: str, timeout: float) -> Any:
        if see is None:
            return refuse(
                name,
                "No hay modelo de visión configurado (cerebro.modelo_vision). Dile al usuario "
                "que no puedes ver hasta que se configure uno.",
            )
        try:
            reply = surface.request("capture", request, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - se cuenta al modelo tal cual
            return refuse(name, f"No se pudo usar la cámara: {exc}. Díselo al usuario y sigue.")
        if reply.get("error"):
            return refuse(name, str(reply["error"]))
        data = reply.get("data")
        if not isinstance(data, str) or not data:
            return refuse(name, "La cámara no devolvió nada.")
        try:
            description = see(data, question)
        except Exception as exc:  # noqa: BLE001
            return refuse(name, f"El modelo de visión falló: {exc}")
        return ok(name, f"Lo que muestra la cámara: {description}")

    def look(args: dict[str, Any]) -> Any:
        reason = str(args.get("reason") or "")[:80]
        question = (
            f"Describe con precisión lo que se ve en esta imagen de la cámara. Foco: {reason}."
            if reason
            else "Describe con precisión lo que se ve en esta imagen de la cámara."
        )
        return capture(
            "look", {"mode": "look", "reason": reason, "seconds": 0, "when": "now"}, question, 25
        )

    def watch(args: dict[str, Any]) -> Any:
        try:
            seconds = int(float(args.get("seconds") or 6))
        except ValueError:
            seconds = 6
        seconds = max(2, min(15, seconds))
        when = "past" if args.get("when") == "past" else "now"
        reason = str(args.get("reason") or "")[:80]
        question = (
            "Esta imagen es una rejilla de fotogramas consecutivos de una cámara, en orden de "
            "izquierda a derecha y de arriba abajo, cada uno con su segundo. Describe qué CAMBIA "
            f"entre ellos, como una secuencia. Foco: {reason or 'lo que hace la persona'}."
        )
        return capture(
            "watch",
            {"mode": "watch", "reason": reason, "seconds": seconds, "when": when},
            question,
            seconds + 40,
        )

    return [
        FaceTool(
            "look",
            LOOK,
            schema({"reason": {"type": "string", "description": "Qué buscas, en pocas palabras."}}),
            look,
            category="camara",
            timeout=120,
        ),
        FaceTool(
            "watch",
            WATCH,
            schema(
                {
                    "seconds": {"type": ["number", "string"]},
                    "when": {"type": "string", "enum": ["now", "past"]},
                    "reason": {"type": "string"},
                }
            ),
            watch,
            category="camara",
            timeout=180,
        ),
    ]
