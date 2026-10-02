"""Salida a la red, con la barrera SSRF por delante.

Este proceso, a diferencia de una pestaña del navegador, puede alcanzar la red
local, el router y los endpoints de metadatos de la nube, y todo lo que
descarga lo descarga en una URL que eligió un modelo mientras leía la web. Así
que cada petición saliente pasa por aquí: primero la barrera, luego la descarga.

- Las IPs se juzgan con ``openjarvis.security.ssrf.is_private_ip`` más unos
  rangos que conviene no olvidar (CGNAT/Tailscale, benchmarking, reservados).
- Se resuelve UNA vez, se rechaza el nombre si CUALQUIER respuesta es privada y
  se conecta a la IP ya validada (Host y SNI conservan el nombre). Así no queda
  ventana de DNS rebinding entre comprobar y conectar.
- Las redirecciones se siguen a mano y cada salto se vuelve a validar.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from openjarvis.security.ssrf import is_private_ip

MAX_REDIRECTS = 4

PROXY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

_BLOCKED_HOSTNAME = re.compile(r"(^|\.)(localhost|local|internal|intranet|home\.arpa)$", re.I)

_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / tailnets
    ipaddress.ip_network("192.0.0.0/24"),  # asignaciones de protocolo
    ipaddress.ip_network("198.18.0.0/15"),  # benchmarking
    ipaddress.ip_network("240.0.0.0/4"),  # reservado
]


class ProxyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def blocked_address(ip: str) -> bool:
    addr_text = ip.split("%")[0]
    try:
        addr = ipaddress.ip_address(addr_text)
    except ValueError:
        return True
    if is_private_ip(addr_text):
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return any(addr in net for net in _EXTRA_BLOCKED if addr.version == net.version)


def vet_target(raw: str) -> httpx.URL:
    try:
        url = httpx.URL(str(raw or ""))
    except Exception as exc:  # noqa: BLE001
        raise ProxyError(400, "se necesita una URL http(s) absoluta") from exc
    if url.scheme not in ("http", "https") or not url.host:
        raise ProxyError(400, "se necesita una URL http(s) absoluta")
    host = url.host
    if _BLOCKED_HOSTNAME.search(host):
        raise ProxyError(403, "host bloqueado")
    try:
        ipaddress.ip_address(host)
        is_literal = True
    except ValueError:
        is_literal = False
    if is_literal and blocked_address(host):
        raise ProxyError(403, "host bloqueado")
    if not is_literal:
        # Formas disfrazadas de IPv4 (decimal, hex, 127.1) que el resolvedor acepta.
        try:
            packed = socket.inet_aton(host)
            if blocked_address(str(ipaddress.IPv4Address(packed))):
                raise ProxyError(403, "host bloqueado")
        except OSError:
            pass
    return url


async def _resolve_public(host: str) -> str:
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ProxyError(502, f"no se pudo resolver {host}") from exc
    if not infos:
        raise ProxyError(502, f"no se pudo resolver {host}")
    for info in infos:
        if blocked_address(str(info[4][0])):
            raise ProxyError(403, "host bloqueado")
    return str(infos[0][4][0])


_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def open_remote(
    url: httpx.URL, headers: dict[str, str], timeout: float
) -> tuple[httpx.Response, httpx.URL]:
    client = get_client()
    for hop in range(MAX_REDIRECTS + 1):
        ip = await _resolve_public(url.host)
        pinned = url.copy_with(host=ip)
        host_header = url.host if url.port is None else f"{url.host}:{url.port}"
        request = client.build_request(
            "GET",
            pinned,
            headers={**headers, "Host": host_header},
            extensions={
                "sni_hostname": url.host,
                "timeout": httpx.Timeout(timeout).as_dict(),
            },
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise ProxyError(504, "el origen no respondió a tiempo") from exc
        except httpx.HTTPError as exc:
            raise ProxyError(502, "origen inalcanzable") from exc
        location = response.headers.get("location")
        if 300 <= response.status_code < 400 and location:
            await response.aclose()
            if hop >= MAX_REDIRECTS:
                raise ProxyError(502, "demasiadas redirecciones")
            url = vet_target(urljoin(str(url), location))
            continue
        return response, url
    raise ProxyError(502, "demasiadas redirecciones")  # pragma: no cover


@dataclass
class TextPage:
    text: str
    type: str
    url: str
    bytes: int


def content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


async def fetch_text(raw_url: str, *, max_bytes: int, timeout: float) -> TextPage:
    target = vet_target(raw_url)
    response, final = await open_remote(
        target,
        {
            "user-agent": PROXY_UA,
            "accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "accept-language": "es-ES,es;q=0.9,en;q=0.8",
            "accept-encoding": "identity",
        },
        timeout,
    )
    try:
        if response.status_code != 200:
            raise ProxyError(
                404 if response.status_code == 404 else 502,
                f"el origen respondió {response.status_code}",
            )
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_raw():
            size += len(chunk)
            if size > max_bytes:
                raise ProxyError(413, "página demasiado grande")
            chunks.append(chunk)
    finally:
        await response.aclose()

    raw = b"".join(chunks)
    declared = re.search(r"charset=[\"']?([\w-]+)", response.headers.get("content-type", ""), re.I)
    meta = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4096], re.I)
    charset = (
        declared.group(1)
        if declared
        else meta.group(1).decode("ascii", "ignore")
        if meta
        else "utf-8"
    )
    try:
        text = raw.decode(charset)
    except (LookupError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace")
    return TextPage(text=text, type=content_type(response), url=str(final), bytes=size)


@dataclass
class Peek:
    status: int
    type: str
    bytes: int | None
    url: str


async def peek(raw_url: str, *, timeout: float) -> Peek:
    """Solo cabeceras. GET abandonado en vez de HEAD: medio internet responde mal a HEAD."""
    target = vet_target(raw_url)
    response, final = await open_remote(
        target,
        {"user-agent": PROXY_UA, "accept": "*/*", "accept-encoding": "identity"},
        timeout,
    )
    await response.aclose()
    length = response.headers.get("content-length")
    return Peek(
        status=response.status_code,
        type=content_type(response),
        bytes=int(length) if length and length.isdigit() else None,
        url=str(final),
    )
