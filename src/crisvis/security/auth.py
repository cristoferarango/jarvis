"""Autenticación local de la interfaz.

Antes, cualquier proceso o página que llegara a 127.0.0.1:8787 podía hablar
con /tts, /api/mcp/recargar o abrir el WebSocket (sin Origin bastaba con
``permitir_sin_origen``; con Origin, con imitar uno local). Ahora:

1. Al servir la interfaz, el núcleo fija una cookie de arranque
   (``crisvis_arranque``: HttpOnly, SameSite=Strict, nueva en cada arranque).
2. ``POST /api/sesion`` exige Host local, Origin permitido y esa cookie, y
   devuelve un token de sesión corto (15 min) ligado a ese Origin.
3. Las rutas que cambian algo o cuestan dinero exigen ``X-Crisvis-Token``.
4. El WebSocket se abre con un ticket de un solo uso (30 s) que se pide con el
   token, ligado al mismo Origin y al mismo cliente.

En desarrollo (``CRISVIS_DEV=1``, lo pone ``npm run dev``) Vite sirve la página
y no hay cookie: se acepta un Origin de los puertos de Vite en localhost.

Límite conocido: un proceso local del mismo usuario puede imitar al navegador
entero (pedir la página, leer la cookie, fijar Origin). Esto protege frente a
otras webs abiertas en el navegador, DNS rebinding y clientes sin sesión, no
frente a malware que ya corre como el usuario.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from crisvis.gateway.origin import _DEV_PORTS, _LOCAL_HOSTS, origin_allowed
from crisvis.settings import ServerSettings

BOOT_COOKIE = "crisvis_arranque"
TOKEN_HEADER = "x-crisvis-token"
ADMIN_HEADER = "x-crisvis-admin"


@dataclass
class ClientSession:
    token: str
    client: str
    origin: str
    expires_at: float


@dataclass
class Ticket:
    client: str
    origin: str
    expires_at: float


class LocalAuth:
    def __init__(
        self,
        server: ServerSettings,
        *,
        dev: bool = False,
        session_ttl_s: float = 15 * 60,
        ticket_ttl_s: float = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.server = server
        self.dev = dev
        self.session_ttl_s = session_ttl_s
        self.ticket_ttl_s = ticket_ttl_s
        self._clock = clock
        self.boot_secret = secrets.token_urlsafe(32)
        self.admin_token = secrets.token_urlsafe(32)
        self._sessions: dict[str, ClientSession] = {}
        self._tickets: dict[str, Ticket] = {}
        self._lock = threading.Lock()

    # -- Host / Origin ----------------------------------------------------------

    def host_allowed(self, host: str | None) -> bool:
        """Solo nombres de loopback y nuestro puerto (o los de Vite): contra DNS rebinding."""
        if not host:
            return False
        parts = urlsplit(f"//{host}")
        name = (parts.hostname or "").lower()
        if name not in _LOCAL_HOSTS and f"[{name}]" not in _LOCAL_HOSTS:
            return False
        try:
            port = parts.port
        except ValueError:
            return False
        if port is None:
            return False
        return port == self.server.port or port in _DEV_PORTS

    def origin_ok(self, origin: str | None) -> bool:
        return bool(origin) and origin_allowed(origin, self.server)

    def is_dev_origin(self, origin: str | None) -> bool:
        if not origin:
            return False
        parts = urlsplit(origin)
        try:
            port = parts.port
        except ValueError:
            return False
        return (parts.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"} and (
            port in _DEV_PORTS
        )

    def boot_cookie_ok(self, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(value or "", self.boot_secret)

    def admin_ok(self, value: str | None) -> bool:
        return bool(value) and hmac.compare_digest(value or "", self.admin_token)

    # -- sesiones -----------------------------------------------------------------

    def issue_session(self, origin: str, client: str | None = None) -> ClientSession:
        now = self._clock()
        session = ClientSession(
            token=secrets.token_urlsafe(32),
            client=client or secrets.token_urlsafe(12),
            origin=origin,
            expires_at=now + self.session_ttl_s,
        )
        with self._lock:
            self._prune(now)
            self._sessions[session.token] = session
        return session

    def verify(self, token: str | None, origin: str | None) -> ClientSession | None:
        """El token vale si existe, no ha caducado y (si hay Origin) coincide con el suyo."""
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if self._clock() >= session.expires_at:
                self._sessions.pop(token, None)
                return None
        if origin and origin != session.origin:
            return None
        return session

    def renew(self, token: str, origin: str | None) -> ClientSession | None:
        current = self.verify(token, origin)
        if current is None:
            return None
        with self._lock:
            self._sessions.pop(token, None)
        return self.issue_session(current.origin, current.client)

    def revoke(self, token: str) -> ClientSession | None:
        with self._lock:
            session = self._sessions.pop(token, None)
            if session is not None:
                for tid, t in list(self._tickets.items()):
                    if t.client == session.client:
                        self._tickets.pop(tid, None)
        return session

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._tickets.clear()

    # -- tickets del WebSocket -------------------------------------------------------

    def issue_ticket(self, session: ClientSession) -> str:
        ticket = secrets.token_urlsafe(24)
        with self._lock:
            self._tickets[ticket] = Ticket(
                session.client, session.origin, self._clock() + self.ticket_ttl_s
            )
        return ticket

    def redeem_ticket(self, ticket: str | None, origin: str | None) -> Ticket | None:
        if not ticket:
            return None
        with self._lock:
            found = self._tickets.pop(ticket, None)
        if found is None or self._clock() >= found.expires_at:
            return None
        if not origin or origin != found.origin:
            return None
        return found

    def _prune(self, now: float) -> None:
        for token, s in list(self._sessions.items()):
            if now >= s.expires_at:
                self._sessions.pop(token, None)
        for tid, t in list(self._tickets.items()):
            if now >= t.expires_at:
                self._tickets.pop(tid, None)
