"""Poner música o un vídeo de YouTube: buscar, elegir el primer resultado y abrirlo.

La búsqueda lee la página pública de resultados de www.youtube.com (sin cuenta ni
clave). Solo se extrae el identificador del vídeo, validado con su formato fijo,
y su título; la URL que se abre se construye aquí, nunca con texto del modelo.
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

import httpx

SEARCH = "https://www.youtube.com/results?search_query="
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_RENDERER = re.compile(r'"videoRenderer":\{"videoId":"([A-Za-z0-9_-]{11})"')
_TITLE = re.compile(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"')
MAX_QUERY = 200


def search_url(query: str) -> str:
    return SEARCH + quote_plus(query.strip()[:MAX_QUERY])


def watch_url(video_id: str) -> str:
    if not _VIDEO_ID.match(video_id):
        raise ValueError("identificador de vídeo inválido")
    return f"https://www.youtube.com/watch?v={video_id}"


def first_video(html: str) -> tuple[str, str] | None:
    """(id, título) del primer vídeo de una página de resultados; los anuncios no cuentan."""
    found = _RENDERER.search(html)
    if not found:
        return None
    title = ""
    named = _TITLE.search(html, found.end(), found.end() + 4000)
    if named:
        try:
            title = json.loads(f'"{named.group(1)}"')
        except ValueError:
            title = ""
    return found.group(1), title


def search_video(query: str, *, timeout: float = 8.0) -> tuple[str, str] | None:
    try:
        res = httpx.get(
            search_url(query),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                "Accept-Language": "es-ES,es;q=0.9",
            },
            timeout=timeout,
            follow_redirects=False,
        )
    except httpx.HTTPError:
        return None
    if res.status_code != 200:
        return None
    return first_video(res.text)
