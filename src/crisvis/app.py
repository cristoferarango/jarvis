"""La aplicación: un único proceso que sirve la cara y aloja el cerebro.

/            la interfaz (apps/face compilada en crisvis/static), con CSP y nonce
/ws          el canal cara <-> cerebro (ticket de un solo uso)
/health      capacidades de voz y estado del cerebro
/tts /stt    voz opcional en el núcleo (token de sesión + límite de frecuencia)
/img /media /page /file   medios a través de la barrera SSRF
/api/sesion  token de sesión corto y tickets del WebSocket
/api/permisos            bajar a LECTURA o volver a CONFIRMAR (token)
/api/admin/libre         LIBRE temporal (token de administración, solo CLI)
/api/estado  configuración efectiva (sin secretos)
/api/openclaw/*  estado del adaptador, auditoría y último informe (solo lectura)

Todo /api/* salvo /api/sesion exige ``X-Crisvis-Token``. Toda petición exige un
Host de loopback (contra DNS rebinding) y, si trae Origin, que sea uno local.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from crisvis import __version__
from crisvis.gateway.origin import origin_allowed
from crisvis.settings import Settings, load_settings, prepare_environment

log = logging.getLogger(__name__)

_TICKET_IN_URL = re.compile(r"(ticket=)[^&\s\"]+")


class _RedactTickets(logging.Filter):
    """Los registros de acceso de uvicorn no deben guardar tickets del WebSocket."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TICKET_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        if isinstance(record.msg, str):
            record.msg = _TICKET_IN_URL.sub(r"\1***", record.msg)
        return True


def _write_admin_token(path: Path, token: str) -> None:
    """El token de administración vive en un archivo del perfil del usuario, no en la página."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)

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
    from crisvis.security.auth import ADMIN_HEADER, BOOT_COOKIE, TOKEN_HEADER, LocalAuth
    from crisvis.security.headers import SECURITY_HEADERS, build_csp
    from crisvis.security.modes import ModeController, ModeError
    from crisvis.security.ratelimit import RateLimits
    from crisvis.security.telemetry import InputTelemetry
    from crisvis.voice.cloned import ClonedVoiceService
    from crisvis.voice.speech import speech_router

    if brain is None:
        from crisvis.brain.openjarvis_brain import OpenJarvisBrain

        brain = OpenJarvisBrain(settings)
    policy = PermissionPolicy(settings.permisos)
    audit = AuditLog(settings.audit_path)
    sec = settings.seguridad
    auth = LocalAuth(
        settings.servidor,
        dev=os.environ.get("CRISVIS_DEV") == "1",
        session_ttl_s=max(1, sec.sesion_minutos) * 60,
    )
    _write_admin_token(settings.admin_token_path, auth.admin_token)
    modes = ModeController(
        policy, settings.permissions_state_path, audit, max_libre_minutes=sec.libre_max_minutos
    )
    telemetry = InputTelemetry(settings.input_telemetry_path)
    limits = RateLimits(
        {
            "tts": (sec.tts_por_minuto, sec.tts_por_minuto * 3, 60),
            "stt": (sec.stt_por_minuto, sec.stt_por_minuto * 3, 60),
            "sesion": (20, 60, 60),
            "ticket": (30, 90, 60),
            "mcp": (3, 6, 60),
            "permisos": (10, 20, 60),
            "admin": (10, 10, 60),
            "lectura": (120, 360, 60),
        }
    )
    logging.getLogger("uvicorn.access").addFilter(_RedactTickets())
    adapter = None
    if settings.openclaw.habilitado:
        from crisvis.openclaw import OpenClawAdapter

        try:
            adapter = OpenClawAdapter(settings, brain_status=lambda: brain.status.as_frame())
        except Exception:  # noqa: BLE001 - sin adaptador, CRISVIS sigue funcionando
            log.exception("El adaptador de OpenClaw no pudo arrancar")
    registry = SessionRegistry(settings, brain, policy, audit, adapter)
    registry.modes = modes
    modes.on_change(registry.on_mode_change)
    cloned = ClonedVoiceService(settings)

    def client_of(request: Request, limit: str | None = None) -> Any:
        """Sesión de la interfaz que hace la petición; 401/403/429 si no vale."""
        site = request.headers.get("sec-fetch-site")
        if site and site not in ("same-origin", "none"):
            audit.event("http_rechazado", ruta=request.url.path, motivo=f"sec-fetch-site {site}")
            raise HTTPException(403, "petición entre sitios")
        session = auth.verify(request.headers.get(TOKEN_HEADER), request.headers.get("origin"))
        if session is None:
            audit.event("http_rechazado", ruta=request.url.path, motivo="sin token válido")
            raise HTTPException(401, "sesión requerida")
        if limit and not limits.allow(limit, session.client):
            audit.event("http_limite", ruta=request.url.path, limite=limit)
            raise HTTPException(429, "demasiadas peticiones")
        return session

    async def require(request: Request, limit: str) -> None:
        client_of(request, limit)

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
            modes.tick()
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
    app.state.auth = auth
    app.state.modes = modes
    port = settings.servidor.port

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        # Host de loopback siempre: una web con DNS rebinding llega con su
        # propio nombre en Host. Con un Origin ajeno, nada; sin Origin (cargas
        # de <img>, navegación) se sirve, pero /api/* pide además el token.
        if not auth.host_allowed(request.headers.get("host")):
            return Response("host no permitido", status_code=421)
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
            response.headers["access-control-allow-headers"] = f"content-type, {TOKEN_HEADER}"
            response.headers["access-control-allow-methods"] = "GET, POST"
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        response.headers.setdefault(
            "content-security-policy", build_csp(None, port=port, kokoro=sec.csp_kokoro)
        )
        return response

    app.include_router(speech_router(settings, brain, cloned, require=require))
    app.include_router(media_router(settings.medios.carpetas))
    app.include_router(ws_router(registry, auth, telemetry, max_connections=sec.max_conexiones))

    # -- sesión de la interfaz ----------------------------------------------------

    @app.post("/api/sesion")
    async def sesion(request: Request) -> JSONResponse:
        origin = request.headers.get("origin")
        if not auth.origin_ok(origin):
            audit.event("sesion_rechazada", motivo="origen", origen=origin or "")
            raise HTTPException(403, "origen requerido")
        if not limits.allow("sesion", origin or ""):
            raise HTTPException(429, "demasiadas peticiones")
        cookie_ok = auth.boot_cookie_ok(request.cookies.get(BOOT_COOKIE))
        if not cookie_ok and not (auth.dev and auth.is_dev_origin(origin)):
            audit.event("sesion_rechazada", motivo="cookie de arranque", origen=origin or "")
            return JSONResponse(
                {"detail": "la página es de un arranque anterior", "reload": True}, 401
            )
        current = auth.verify(request.headers.get(TOKEN_HEADER), origin)
        session = (
            auth.renew(current.token, origin) if current else auth.issue_session(str(origin))
        )
        assert session is not None
        return JSONResponse(
            {"token": session.token, "expiresIn": round(auth.session_ttl_s)},
            headers={"cache-control": "no-store"},
        )

    @app.post("/api/sesion/ticket")
    async def ticket(request: Request) -> JSONResponse:
        session = client_of(request, "ticket")
        return JSONResponse(
            {"ticket": auth.issue_ticket(session), "expiresIn": round(auth.ticket_ttl_s)},
            headers={"cache-control": "no-store"},
        )

    @app.post("/api/sesion/cerrar")
    async def cerrar(request: Request) -> dict[str, Any]:
        session = client_of(request)
        auth.revoke(session.token)
        await registry.revoke_owner(session.client, "sesión cerrada")
        audit.event("sesion_cerrada")
        return {"ok": True}

    # -- permisos -----------------------------------------------------------------

    @app.get("/api/permisos")
    async def permisos_estado(request: Request) -> dict[str, Any]:
        client_of(request, "lectura")
        return modes.status()

    @app.post("/api/permisos")
    async def permisos(request: Request) -> dict[str, Any]:
        client_of(request, "permisos")
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "JSON inválido") from exc
        mode = body.get("modo") if isinstance(body, dict) else None
        try:
            if mode == "confirmar" and modes.mode == "libre":
                modes.disable_libre(who="interfaz", how="POST /api/permisos")
            modes.set_user_mode(str(mode), who="interfaz", how="POST /api/permisos")
        except ModeError as exc:
            raise HTTPException(403, str(exc)) from exc
        return modes.status()

    def admin_only(request: Request) -> None:
        # Solo la CLI local lee <home>/admin.token. Un navegador manda Origin.
        if request.headers.get("origin") or not auth.admin_ok(request.headers.get(ADMIN_HEADER)):
            audit.event("admin_rechazado", ruta=request.url.path)
            raise HTTPException(403, "requiere el token de administración")
        if not limits.allow("admin", "cli"):
            raise HTTPException(429, "demasiadas peticiones")

    @app.post("/api/admin/libre")
    async def admin_libre(request: Request) -> dict[str, Any]:
        admin_only(request)
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "JSON inválido") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON inválido")
        who = str(body.get("quien") or "cli")[:60]
        try:
            modes.enable_libre(
                body.get("minutos", 10),
                who=who,
                how="crisvis permisos libre",
                confirmation=str(body.get("confirmacion") or ""),
            )
        except ModeError as exc:
            raise HTTPException(403, str(exc)) from exc
        return modes.status()

    @app.post("/api/admin/confirmar")
    async def admin_confirmar(request: Request) -> dict[str, Any]:
        admin_only(request)
        modes.disable_libre(who="cli", how="crisvis permisos confirmar")
        modes.set_user_mode("confirmar", who="cli", how="crisvis permisos confirmar")
        return modes.status()

    # -- consultas (token) -------------------------------------------------------

    @app.get("/api/estado")
    async def estado(request: Request) -> dict[str, Any]:
        client_of(request, "lectura")
        return {
            "version": __version__,
            "config": settings.public_dict(),
            "cerebro": brain.status.as_frame(),
            "servidores_mcp": brain.status.servers,
            "permisos": modes.status(),
            "seguridad": {
                "portapapeles": sec.portapapeles,
                "elevenlabs": settings.voz.elevenlabs_habilitado,
            },
            "sesiones": len(registry),
        }

    def connectors() -> dict[str, Any]:
        status = getattr(brain, "connector_status", lambda: [])
        return {"conectores": status()}

    @app.get("/api/mcp")
    async def mcp_estado(request: Request) -> dict[str, Any]:
        client_of(request, "lectura")
        return connectors()

    # Informe del día y OpenClaw: solo lectura por REST. Las acciones van por el
    # WebSocket, con política y aprobación exacta.
    @app.get("/api/openclaw/estado")
    async def openclaw_estado(request: Request) -> dict[str, Any]:
        client_of(request, "lectura")
        if adapter is None:
            return {"enabled": False}
        return await adapter.overview()

    @app.get("/api/openclaw/auditoria")
    async def openclaw_auditoria(request: Request, limite: int = 50) -> dict[str, Any]:
        client_of(request, "lectura")
        return {"eventos": adapter.recent_audit(limite) if adapter else []}

    @app.get("/api/openclaw/informe")
    async def openclaw_informe(request: Request) -> dict[str, Any]:
        client_of(request, "lectura")
        if adapter is None or adapter.last is None:
            return {"informe": None}
        return {"informe": adapter.frame(adapter.last)}

    @app.post("/api/mcp/recargar")
    async def mcp_recargar(request: Request) -> dict[str, Any]:
        client_of(request, "mcp")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get("confirmacion") != "RECARGAR":
            raise HTTPException(400, "falta la confirmación «RECARGAR»")
        audit.event("mcp_recargar")
        if hasattr(brain, "reload_connectors"):
            await asyncio.to_thread(brain.reload_connectors, force=True)
            registry.broadcast_status()
        return connectors()

    # -- la página ----------------------------------------------------------------

    def page(html: str) -> HTMLResponse:
        nonce = secrets.token_urlsafe(16)
        html = re.sub(r"<script(?![^>]*\snonce=)", f'<script nonce="{nonce}"', html)
        response = HTMLResponse(html, headers={"cache-control": "no-store"})
        response.headers["content-security-policy"] = build_csp(
            nonce, port=port, kokoro=sec.csp_kokoro
        )
        response.set_cookie(
            BOOT_COOKIE, auth.boot_secret, httponly=True, samesite="strict", path="/"
        )
        return response

    index = STATIC_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    async def home() -> HTMLResponse:
        if index.exists():
            return page(index.read_text(encoding="utf-8"))
        return page(FALLBACK_PAGE)

    if index.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="cara")

    return app
