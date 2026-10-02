"""/img, /media, /page y /file: lo que la cara necesita ver sin salir a internet.

La cara nunca carga nada remoto directamente (la CSP lo impide): el marcado lo
escribe un modelo que acaba de leer páginas no fiables, y una URL remota en él
sería una baliza. Todo pasa por aquí, con la barrera SSRF de ``net``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from crisvis.media.net import PROXY_UA, ProxyError, content_type, open_remote, vet_target
from crisvis.media.page import render_page

log = logging.getLogger(__name__)

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    # .svg falta a propósito: es un documento con scripts servido desde
    # nuestro propio origen, el único que puede abrir el socket del agente.
}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_IMG_BYTES = 15 * 1024 * 1024
MAX_MEDIA_BYTES = 200 * 1024 * 1024
IMG_TIMEOUT = 10.0
MEDIA_TIMEOUT = 30.0


def file_roots(extra: list[str]) -> list[Path]:
    roots = [Path.home(), Path(tempfile.gettempdir())]
    roots += [Path(p).expanduser() for p in extra if p.strip()]
    roots += [
        Path(p) for p in os.environ.get("CRISVIS_FILE_ROOTS", "").split(os.pathsep) if p.strip()
    ]
    out: list[Path] = []
    for root in roots:
        try:
            out.append(root.resolve(strict=False))
        except OSError:
            continue
    return out


def within(real: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            rel = real.relative_to(root)
        except ValueError:
            continue
        if str(rel) not in ("", "."):
            return True
    return False


async def _proxy(
    request: Request, kinds: tuple[str, ...], max_bytes: int, timeout: float, ranged: bool
) -> Response:
    asked = request.query_params.get("url", "")
    target = vet_target(asked)
    headers = {
        "user-agent": PROXY_UA,
        "accept": "*/*" if ranged else "image/*,*/*;q=0.8",
        "accept-encoding": "identity",
    }
    if ranged and request.headers.get("range"):
        headers["range"] = request.headers["range"]

    upstream, _final = await open_remote(target, headers, timeout)
    status = upstream.status_code
    if status not in (200, 206):
        await upstream.aclose()
        raise ProxyError(404 if status == 404 else 502, f"el origen respondió {status}")
    kind = content_type(upstream)
    if not any(kind.startswith(k) for k in kinds):
        await upstream.aclose()
        raise ProxyError(415, f"no es {' ni '.join(kinds)} (es {kind or 'nada'})")
    declared = upstream.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        await upstream.aclose()
        raise ProxyError(413, "demasiado grande")

    out = {
        "content-type": kind,
        "x-content-type-options": "nosniff",
        "cache-control": "private, max-age=600",
    }
    if declared and declared.isdigit():
        out["content-length"] = declared
    if ranged:
        if status == 206 or upstream.headers.get("accept-ranges") == "bytes":
            out["accept-ranges"] = "bytes"
        if upstream.headers.get("content-range"):
            out["content-range"] = upstream.headers["content-range"]

    async def body() -> AsyncIterator[bytes]:
        sent = 0
        try:
            async for chunk in upstream.aiter_raw():
                sent += len(chunk)
                if sent > max_bytes:
                    log.warning("proxy cortado en %s bytes: %s", max_bytes, target)
                    return
                yield chunk
        except httpx.HTTPError:
            return
        finally:
            await upstream.aclose()

    return StreamingResponse(body(), status_code=status, headers=out)


def _error_page(status: int, message: str) -> HTMLResponse:
    safe = message.replace("<", "").replace("&", "")
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'><style>body{margin:0;padding:26px;"
        "background:transparent;color:#7fb6bf;font:400 13px/1.6 ui-monospace,monospace}"
        "b{color:#cfe9ee;font-weight:500;display:block;margin-bottom:6px}</style>"
        f"<b>No se pudo abrir esta página.</b>{safe}",
        status_code=status,
        headers={"content-security-policy": "default-src 'none'; style-src 'unsafe-inline'"},
    )


def media_router(extra_roots: list[str]) -> APIRouter:
    router = APIRouter()
    roots = file_roots(extra_roots)

    @router.get("/file")
    async def local_file(path: str = "") -> Response:
        # Resolver enlaces ANTES de juzgar: un nombre terminado en .png puede
        # apuntar a cualquier archivo del sistema.
        try:
            asked = Path(path)
            real = asked.resolve(strict=True) if asked.is_absolute() else None
        except (OSError, RuntimeError):
            real = None
        ext = real.suffix.lower() if real else ""
        if real is None or ext not in IMAGE_TYPES or not within(real, roots):
            return Response("solo imágenes", status_code=400)
        try:
            if not real.is_file() or real.stat().st_size > MAX_FILE_BYTES:
                return Response("demasiado grande", status_code=413)
            data = await _read(real)
        except OSError:
            return Response("no encontrado", status_code=404)
        return Response(
            data, media_type=IMAGE_TYPES[ext], headers={"x-content-type-options": "nosniff"}
        )

    @router.get("/img")
    async def image(request: Request) -> Response:
        try:
            return await _proxy(request, ("image/",), MAX_IMG_BYTES, IMG_TIMEOUT, ranged=False)
        except ProxyError as exc:
            return Response(exc.message, status_code=exc.status)

    @router.get("/media")
    async def media(request: Request) -> Response:
        try:
            return await _proxy(
                request, ("video/", "audio/"), MAX_MEDIA_BYTES, MEDIA_TIMEOUT, ranged=True
            )
        except ProxyError as exc:
            return Response(exc.message, status_code=exc.status)

    @router.get("/page")
    async def page(request: Request, url: str = "", mode: str = "reader") -> Response:
        origin = f"{request.url.scheme}://{request.url.netloc}"
        try:
            rendered = await render_page(url, "live" if mode == "live" else "reader", origin)
        except ProxyError as exc:
            return _error_page(exc.status, exc.message)
        except Exception as exc:  # noqa: BLE001
            log.warning("página fallida %s: %s", url, exc)
            return _error_page(502, "error inesperado")
        return Response(rendered.body, headers=rendered.headers)

    return router


async def _read(path: Path) -> bytes:
    import asyncio

    return await asyncio.to_thread(path.read_bytes)
