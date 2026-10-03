"""Pestañas que se abren en el navegador al pedir el informe del día.

Las direcciones salen solo de ``[openclaw] abrir_al_informe`` (configuración
del usuario), nunca del modelo ni del contenido de un correo o una página. Se
abren en el navegador predeterminado, como pestañas nuevas de la ventana que ya
está abierta, una tras otra. No inicia sesión en nada ni lee nada: abre la
página y el navegador usa la sesión que el usuario ya tenga.
"""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from collections.abc import Callable, Iterable
from urllib.parse import urlsplit

from crisvis.openclaw.audit import AdapterAudit

log = logging.getLogger(__name__)

MAX_TABS = 8
TOOL = "navegador.abrir_pestana"


def _open(url: str) -> bool:
    return webbrowser.open_new_tab(url)


def safe_tab_url(url: str) -> str | None:
    """Solo https con nombre de host y sin credenciales en la dirección."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    return parts.geturl()


def tab_urls(urls: Iterable[str]) -> tuple[list[str], list[str]]:
    """(válidas sin repetir, rechazadas), con un máximo de MAX_TABS."""
    ok: list[str] = []
    bad: list[str] = []
    for raw in urls:
        url = safe_tab_url(str(raw))
        if url is None:
            bad.append(str(raw))
        elif url not in ok and len(ok) < MAX_TABS:
            ok.append(url)
    return ok, bad


async def open_tabs(
    urls: Iterable[str],
    *,
    interval: float,
    audit: AdapterAudit,
    session: str = "",
    opener: Callable[[str], bool] | None = None,
) -> int:
    """Abre las pestañas de una en una. Cancelar la tarea detiene las que faltan."""
    ok, bad = tab_urls(urls)
    for raw in bad:
        audit.record(
            "briefing.tab", session=session, tool=TOOL, decision="config",
            outcome="rechazada: solo https sin credenciales", details={"url": raw[:120]},
        )
    opened = 0
    for i, url in enumerate(ok):
        if i:
            await asyncio.sleep(max(0.0, interval))
        host = urlsplit(url).hostname or ""
        try:
            done = await asyncio.to_thread(opener or _open, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo abrir %s: %s", host, exc)
            done = False
        opened += bool(done)
        audit.record(
            "briefing.tab", session=session, tool=TOOL, decision="config",
            outcome="abierta" if done else "error", details={"host": host},
        )
    return opened
