"""Cliente del Gateway de OpenClaw, solo loopback.

Dos vías, ambas documentadas por OpenClaw:

* Sondas HTTP ``/healthz`` (vivo) y ``/readyz`` (listo) en el puerto del Gateway.
* La CLI oficial (``openclaw health --json``, ``openclaw gateway status --json``,
  ``openclaw gateway call <método> --json``). La CLI lee su propio token de
  ``~/.openclaw/openclaw.json``: CRISVIS nunca ve ni guarda ese secreto.

Solo se permiten métodos de la lista ``ALLOWED_RPC``. No se pasan prompts ni
texto libre del usuario o del modelo a OpenClaw.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from crisvis.openclaw.redact import redact, safe_error

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
ALLOWED_RPC = frozenset({"health", "status"})
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GatewayError(Exception):
    pass


def require_loopback(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "ws", "wss"):
        raise GatewayError("el gateway debe ser http(s) o ws(s)")
    if (parsed.hostname or "") not in LOOPBACK:
        raise GatewayError("OpenClaw solo se acepta en loopback (127.0.0.1 / localhost)")
    return url.rstrip("/")


def resolve_cli(explicit: str = "") -> list[str] | None:
    """Cómo lanzar la CLI sin pasar por cmd.exe.

    En Windows npm instala un ``openclaw.cmd``; ejecutar .cmd con argumentos
    reabre la puerta a la inyección por cmd.exe. Se lanza ``node`` con el
    script del paquete directamente.
    """
    found = explicit or shutil.which("openclaw")
    if not found:
        return None
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".ps1", ".bat", "") and path.parent.is_dir():
        pkg = path.parent / "node_modules" / "openclaw"
        manifest = pkg / "package.json"
        node = shutil.which("node")
        if manifest.is_file() and node:
            try:
                bin_field = json.loads(manifest.read_text("utf-8")).get("bin")
            except (OSError, json.JSONDecodeError):
                bin_field = None
            script = bin_field.get("openclaw") if isinstance(bin_field, dict) else bin_field
            if isinstance(script, str) and (pkg / script).is_file():
                return [node, str(pkg / script)]
        if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
            return None
    return [str(path)]


class GatewayClient:
    def __init__(self, url: str, cli: str = "", cache_s: float = 10.0) -> None:
        self.url = require_loopback(url)
        self.port = urlparse(self.url).port or 18789
        self._cli_hint = cli
        self._cache_s = cache_s
        self._cached: tuple[float, dict[str, Any]] | None = None

    @property
    def cli(self) -> list[str] | None:
        return resolve_cli(self._cli_hint)

    async def probe(self) -> dict[str, Any]:
        out = {"live": False, "ready": False}
        try:
            async with httpx.AsyncClient(base_url=self.url, timeout=1.5) as client:
                live = await client.get("/healthz")
                out["live"] = live.status_code == 200
                ready = await client.get("/readyz")
                out["ready"] = ready.status_code == 200
        except httpx.HTTPError:
            pass
        return out

    def _run_cli(self, args: list[str], timeout: float) -> dict[str, Any]:
        base = self.cli
        if base is None:
            raise GatewayError("la CLI de OpenClaw no está instalada")
        proc = subprocess.run(  # noqa: S603 - argv construido aquí, sin shell
            [*base, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            creationflags=_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
        text = proc.stdout.strip()
        start = text.find("{")
        if start < 0:
            raise GatewayError(f"la CLI no devolvió JSON (código {proc.returncode})")
        try:
            data = json.loads(text[start:])
        except json.JSONDecodeError as exc:
            raise GatewayError("respuesta JSON no válida de la CLI") from exc
        return redact(data) if isinstance(data, dict) else {"value": redact(data)}

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in ALLOWED_RPC:
            raise GatewayError(f"método no permitido: {method!r}")
        # --port fija el destino al Gateway local en loopback (no a uno remoto configurado).
        args = ["gateway", "call", method, "--port", str(self.port), "--json", "--timeout", "8000"]
        if params:
            args += ["--params", json.dumps(params, ensure_ascii=True)]
        return await asyncio.to_thread(self._run_cli, args, 12.0)

    async def health(self) -> dict[str, Any]:
        return await self.call("health")

    async def status(self, fresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not fresh and self._cached and now - self._cached[0] < self._cache_s:
            return self._cached[1]
        installed = self.cli is not None
        probe = await self.probe()
        state = "no_instalado"
        message = "OpenClaw no está instalado."
        if installed:
            state, message = "detenido", "Instalado; el Gateway no responde en loopback."
        if probe["live"]:
            state = "listo" if probe["ready"] else "arrancando"
            message = "Gateway en loopback." if probe["ready"] else "Gateway vivo, aún no listo."
        info: dict[str, Any] = {
            "state": state,
            "message": message,
            "url": self.url,
            "installed": installed,
            **probe,
        }
        if probe["live"] and installed:
            try:
                health = await self.health()
                info["health"] = {
                    k: health[k] for k in ("ok", "version", "uptimeMs", "status") if k in health
                }
            except (GatewayError, subprocess.TimeoutExpired, OSError) as exc:
                info["health_error"] = safe_error(exc)
        self._cached = (now, info)
        return info
