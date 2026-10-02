"""`display`, `blade` y `probe_url`: la pantalla de JARVIS.

En vez de rellenar plantillas, el modelo compone el panel con un sistema de
diseño fijo (clases .hud-*), de modo que todo lo que construye sigue pareciendo
una sola interfaz. La cara sanea el marcado antes de pintarlo.
"""

from __future__ import annotations

import json
import re
from typing import Any

from crisvis.body.base import FaceTool, new_id, ok, refuse, schema
from crisvis.body.surface import Surface
from crisvis.media.page import probe_url

DESIGN_SYSTEM = """\
CLASES (usa SOLO estas; cualquier otra se elimina):
.hud-rows lista vertical · .hud-row fila con .hud-idx (índice), .hud-main (columna de texto) y
.hud-tag (etiqueta a la derecha) · .hud-label línea principal · .hud-sub línea secundaria ·
.hud-metric cifra enorme · .hud-unit pie de la cifra · .hud-note prosa breve · .hud-img imagen ancha ·
.hud-caption pie de imagen · .hud-grid dos columnas · .hud-bar barra (style="--v:0.62") ·
.hud-dim atenuar · .hud-hot resaltar · .hud-gallery rejilla de .hud-thumb · .hud-video <video> ·
.hud-embed envoltorio 16:9 para <iframe> de YouTube/Vimeo · .hud-figure imagen+pie.

REGLAS: sin colores en línea, sin <style>/<script>/<form>. Imágenes web y rutas locales
(file:///ruta o ruta absoluta) se muestran: el núcleo las descarga por ti. Nunca inventes una URL:
usa solo las que aparecieron tal cual en el resultado de una herramienta. Máximo ~6 filas o 40 palabras.

EJEMPLOS
<div class="hud-rows"><div class="hud-row"><span class="hud-idx">01</span><span class="hud-main">\
<span class="hud-label">Titular</span><span class="hud-sub">Detalle</span></span>\
<span class="hud-tag">fuente</span></div></div>
<div><span class="hud-metric">1.284</span><span class="hud-unit">sin leer desde el lunes</span></div>
<p class="hud-note">Tres de cuatro servicios en orden. <span class="hud-hot">Uno degradado</span>.</p>"""

DISPLAY_DESCRIPTION = f"""Muestra algo en la pantalla de la interfaz mientras hablas.
Úsalo cuando la respuesta merezca verse: resultados de búsqueda, una cifra, una lista,
una imagen. Llámalo ANTES o MIENTRAS hablas. Nunca leas el panel en voz alta: di lo que
significa.

{DESIGN_SYSTEM}"""

BLADE_DESCRIPTION = """Abre algo en las "blades", la superficie grande, para mirarlo de verdad.
kind: article (página web; mode "reader" = solo el texto, "live" = la página real), image,
gallery (varias imágenes en `images`), video (.mp4/.webm), embed (YouTube/Vimeo), markup
(tu HTML .hud-*), camera (la cámara en vivo; sin url). size: compact | tall (para leer) |
wide (imágenes, tablas) | full. Usa probe_url si no sabes qué hay en una URL.
No abras nada que el usuario no haya pedido."""

PROBE_DESCRIPTION = """Averigua qué hay realmente en una URL antes de mostrarla: tipo
(imagen, vídeo, página), si responde, título y cuánta prosa legible tiene. Devuelve una
sugerencia (blade_reader, blade_live, blade_image, blade_video, speak_only, unavailable)
que es solo un consejo."""

_ANIMS = ["materialise", "sweep", "unfold", "stagger", "snap"]
_KINDS = ["article", "image", "gallery", "video", "embed", "markup", "camera"]


def _text_of(html: str) -> str:
    return re.sub(r"<[^>]*>", "", html).strip()


def display_tools(surface: Surface) -> list[FaceTool]:
    def display(args: dict[str, Any]) -> Any:
        html = str(args.get("html") or "")
        # Rechazar en vez de avisar: un panel vacío en pantalla parece la
        # interfaz rota, y devuelto como error el modelo tiene otra oportunidad.
        if not _text_of(html) and not re.search(r"<(img|video|iframe|source)\b", html, re.I):
            return refuse(
                "display",
                "No mostrado: el cuerpo del panel estaba vacío. Vuelve a llamar a display con "
                "el contenido compuesto en el argumento html.",
            )
        surface.push(
            {
                "type": "blade",
                "blade": {
                    "id": new_id("p"),
                    "title": str(args.get("title") or "").strip() or "PANTALLA",
                    "kind": "markup",
                    "html": html,
                    "size": "wide" if args.get("slot") == "wide" else "compact",
                    "hold": "sticky" if args.get("hold") == "sticky" else "turn",
                },
            }
        )
        return ok("display", "En pantalla.")

    def blade(args: dict[str, Any]) -> Any:
        kind = args.get("kind")
        if kind not in _KINDS:
            return refuse("blade", f"No abierto: kind debe ser uno de {', '.join(_KINDS)}.")
        url = str(args.get("url") or "").strip()
        images = [str(i) for i in (args.get("images") or []) if i]
        html = str(args.get("html") or "")
        if kind == "gallery" and not images:
            return refuse(
                "blade", "No abierto: una galería necesita al menos una imagen en images."
            )
        if kind == "markup" and not html.strip():
            return refuse("blade", 'No abierto: kind "markup" necesita html.')
        if kind in ("article", "image", "video", "embed") and not url:
            return refuse("blade", f'No abierto: kind "{kind}" necesita url.')
        mode = "live" if args.get("mode") == "live" else "reader"
        size = args.get("size")
        if size not in ("compact", "tall", "wide", "full"):
            size = "tall" if kind == "article" else "wide"
        title = str(args.get("title") or "").strip() or "PANTALLA"
        payload: dict[str, Any] = {
            "id": new_id("b"),
            "title": title,
            "kind": kind,
            "mode": mode,
            "size": size,
            "hold": "sticky" if args.get("hold") == "sticky" else "turn",
        }
        if url:
            payload["url"] = url
        if images:
            payload["images"] = images[:8]
        if html:
            payload["html"] = html
        surface.push({"type": "blade", "blade": payload})
        return ok("blade", f'Abierto en las blades como "{title}".')

    def probe(args: dict[str, Any]) -> Any:
        url = str(args.get("url") or "").strip()
        if not url:
            return refuse("probe_url", "Falta url.")
        report = surface.run(probe_url(url), timeout=30)
        return ok("probe_url", json.dumps(report, ensure_ascii=False, indent=1))

    return [
        FaceTool(
            "display",
            DISPLAY_DESCRIPTION,
            schema(
                {
                    "title": {"type": "string", "description": "Encabezado de 2 a 4 palabras."},
                    "html": {"type": "string", "description": "Fragmento HTML con clases .hud-*."},
                    "anim": {"type": "string", "enum": _ANIMS},
                    "slot": {"type": "string", "enum": ["right", "left", "wide"]},
                    "accent": {
                        "type": "string",
                        "enum": ["default", "amber", "violet", "green", "red"],
                    },
                    "hold": {"type": "string", "enum": ["turn", "sticky"]},
                },
                ["title", "html"],
            ),
            display,
        ),
        FaceTool(
            "blade",
            BLADE_DESCRIPTION,
            schema(
                {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": _KINDS},
                    "url": {"type": "string"},
                    "images": {"type": "array", "items": {"type": "string"}},
                    "html": {"type": "string"},
                    "mode": {"type": "string", "enum": ["reader", "live"]},
                    "size": {"type": "string", "enum": ["compact", "tall", "wide", "full"]},
                    "hold": {"type": "string", "enum": ["turn", "sticky"]},
                },
                ["title", "kind"],
            ),
            blade,
        ),
        FaceTool(
            "probe_url",
            PROBE_DESCRIPTION,
            schema({"url": {"type": "string", "description": "URL absoluta."}}, ["url"]),
            probe,
            category="web",
            timeout=35,
        ),
    ]
