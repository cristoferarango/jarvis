"""La aplicación: un único proceso que sirve la cara y aloja el cerebro.

/            la interfaz (apps/face compilada en crisvis/static)
/ws          el canal cara <-> cerebro
/health      capacidades de voz y estado del cerebro
/tts /stt    voz opcional en el núcleo
/img /media /page /file   medios a través de la barrera SSRF
/api/estado  configuración efectiva (sin secretos)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from crisvis import __version__
from crisvis.gateway.origin import origin_allowed
from crisvis.settings import Settings, load_settings, prepare_environment

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
ENGINE_RETRY_SECONDS = 10
CONNECTOR_POLL_SECONDS = 3

FALLBACK_PAGE = """<!doctype html><html lang="es"><meta charset="utf-8">
<title>Crisvis</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#03080d;
color:#9fdbe2;font:15px/1.6 ui-monospace,Consolas,monospace}main{max-width:560px;padding:32px}
b{color:#5fd8e0}code{color:#eafcff}</style>
<main><b>CRISVIS · núcleo en marcha</b><p>La interfaz no está compilada todavía.</p>
<p>Desde la carpeta del proyecto ejecuta <code>npm install</code> y <code>npm run build</code>,
o usa <code>npm run dev</code> para desarrollo.</p></main></html>"""


def create_app(settings: Settings | None = None, *, brain: Any = None) -> FastAPI:
    settings = settings or load_settings()
    prepare_environment(settings)

    # Imports tardíos: OpenJarvis fija sus rutas al importarse y
    # prepare_environment tiene que ir antes.
    from crisvis.gateway.session import SessionRegistry
    from crisvis.gateway.ws import ws_router
    from crisvis.media.net import close_client
    from crisvis.media.proxy import media_router
    from crisvis.security import AuditLog, PermissionPolicy
    from crisvis.voice.cloned import ClonedVoiceService
    from crisvis.voice.speech import speech_router

    if brain is None:
        from crisvis.brain.openjarvis_brain import OpenJarvisBrain

        brain = OpenJarvisBrain(settings)
    policy = PermissionPolicy(settings.permisos)
    audit = AuditLog(settings.audit_path)
    registry = SessionRegistry(settings, brain, policy, audit)
    cloned = ClonedVoiceService(settings)

    async def watch_brain() -> None:
        try:
            await asyncio.to_thread(brain.start)
        except Exception as exc:  # noqa: BLE001
            log.exception("El cerebro no pudo arrancar")
            brain.status.message = f"El cerebro no pudo arrancar: {exc}"
        registry.broadcast_status()
        last = brain.status.ok
        if last:
            log.info("Cerebro: %s / %s", brain.status.engine, brain.status.model)
        else:
            log.warning("Cerebro sin motor: %s", brain.status.message)
        while True:
            await asyncio.sleep(ENGINE_RETRY_SECONDS)
            if brain.status.ok and last:
                continue
            ok = await asyncio.to_thread(brain.connect_engine) if not brain.status.ok else True
            if ok != last:
                last = ok
                log.info("Cerebro %s", "listo" if ok else "sin motor")
                registry.broadcast_status()

    async def watch_connectors() -> None:
        """Recarga las apps MCP al editar mcp.json y avisa a la cara de cada cambio."""
        if not hasattr(brain, "connector_status"):
            return
        shown: list[Any] = []
        reload: asyncio.Task[Any] | None = None
        while True:
            await asyncio.sleep(CONNECTOR_POLL_SECONDS)
            try:
                if (reload is None or reload.done()) and brain.connectors_changed():
                    log.info("mcp.json cambió: reconectando aplicaciones")
                    reload = asyncio.create_task(asyncio.to_thread(brain.reload_connectors))
                now = brain.connector_status()
            except Exception:  # noqa: BLE001
                log.exception("Fallo vigilando los conectores MCP")
                continue
            if now != shown:
                shown = now
                registry.broadcast_status()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        watcher = asyncio.create_task(watch_brain())
        connectors = asyncio.create_task(watch_connectors())
        try:
            cloned.start()
        except OSError as exc:
            log.warning("La voz clonada no pudo arrancar: %s", exc)
        try:
            yield
        finally:
            cloned.stop()
            watcher.cancel()
            connectors.cancel()
            await registry.close()
            await close_client()
            await asyncio.to_thread(brain.close)

    app = FastAPI(
        title="Crisvis",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.brain = brain
    app.state.registry = registry

    @app.middleware("http")
    async def guard_origin(request: Request, call_next: Any) -> Response:
        # Sin Origin (cargas de <img>, navegación de iframes) se sirve; con un
        # Origin ajeno, no. Así otra web abierta en el navegador no puede leer
        # nada del núcleo.
        origin = request.headers.get("origin")
        if origin and not origin_allowed(origin, settings.servidor):
            return Response("origen no permitido", status_code=403, headers={"vary": "origin"})
        if request.method == "OPTIONS":
            response: Response = Response(status_code=204)
        else:
            response = await call_next(request)
        response.headers["vary"] = "origin"
        if origin:
            response.headers["access-control-allow-origin"] = origin
            response.headers["access-control-allow-headers"] = "content-type"
        return response

    app.include_router(speech_router(settings, brain, cloned))
    app.include_router(media_router(settings.medios.carpetas))
    app.include_router(ws_router(registry, settings.servidor))

    @app.get("/api/estado")
    async def estado() -> dict[str, Any]:
        return {
            "version": __version__,
            "config": settings.public_dict(),
            "cerebro": brain.status.as_frame(),
            "servidores_mcp": brain.status.servers,
            "permisos": {"modo": policy.mode},
            "sesiones": len(registry),
        }

    @app.get("/api/mcp")
    async def mcp_estado() -> dict[str, Any]:
        status = getattr(brain, "connector_status", lambda: [])
        return {"conectores": status()}

    @app.post("/api/mcp/recargar")
    async def mcp_recargar() -> dict[str, Any]:
        if hasattr(brain, "reload_connectors"):
            await asyncio.to_thread(brain.reload_connectors, force=True)
            registry.broadcast_status()
        return await mcp_estado()

    if (STATIC_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="cara")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def fallback() -> str:
            return FALLBACK_PAGE

    return app
