"""La voz clonada: el núcleo arranca services/voz (XTTS-v2) y le pide el audio.

Vive en otro proceso con su propio entorno porque PyTorch y transformers no
deben mezclarse con las dependencias del cerebro. Solo escucha en 127.0.0.1 y
solo atiende a quien traiga la clave que el núcleo genera al lanzarlo.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from crisvis.settings import Settings

log = logging.getLogger(__name__)

STATUS_TTL_SECONDS = 2.0
SAMPLE_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg"})


def service_dir() -> Path:
    explicit = os.environ.get("CRISVIS_VOZ_DIR")
    if explicit:
        return Path(explicit).expanduser()
    return Path(__file__).resolve().parents[3] / "services" / "voz"


def service_python(directory: Path | None = None) -> Path:
    venv = (directory or service_dir()) / ".venv"
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def voice_samples(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SAMPLE_EXTENSIONS
    )


def bundled_samples() -> list[Path]:
    """La voz que viaja con el proyecto (services/voz/muestras)."""
    return voice_samples(service_dir() / "muestras")


def seed_samples(folder: Path) -> list[Path]:
    """Si la carpeta de la voz no tiene muestras, copia en ella las del proyecto.

    Así un equipo recién instalado habla con la voz de serie, y quien ponga la
    suya con `crisvis voz` no la ve sustituida.
    """
    if voice_samples(folder):
        return []
    bundled = bundled_samples()
    if not bundled:
        return []
    folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in bundled:
        dest = folder / src.name
        shutil.copy2(src, dest)
        copied.append(dest)
    log.info("Voz de serie copiada en %s: %s", folder, ", ".join(p.name for p in copied))
    return copied


class ClonedVoiceService:
    def __init__(self, settings: Settings) -> None:
        voz = settings.voz
        self.enabled = bool(voz.clonada)
        self.port = int(voz.clonada_puerto)
        self.folder = (
            Path(voz.clonada_carpeta).expanduser() if voz.clonada_carpeta else settings.home / "voz"
        )
        self.language = settings.asistente.idioma.split("-")[0].lower()
        self._token = secrets.token_urlsafe(32)
        self._proc: subprocess.Popen[bytes] | None = None
        self._status: dict[str, Any] = {}
        self._status_at = 0.0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @staticmethod
    def installed() -> bool:
        return service_python().is_file()

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- ciclo de vida -----------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self.running():
            return
        if not self.installed():
            log.info("Voz clonada no instalada (npm run voz:instalar para activarla)")
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            seed_samples(self.folder)
        except OSError as exc:
            log.warning("No se pudo copiar la voz de serie: %s", exc)
        env = {**os.environ, "CRISVIS_VOZ_TOKEN": self._token, "PYTHONIOENCODING": "utf-8"}
        cmd = [
            str(service_python()),
            "-m",
            "crisvis_voz",
            "--puerto",
            str(self.port),
            "--carpeta",
            str(self.folder),
            "--idioma",
            self.language,
        ]
        log.info("Arrancando la voz clonada en %s (muestras en %s)", self.base_url, self.folder)
        self._proc = subprocess.Popen(cmd, env=env, cwd=str(service_dir()))

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    # -- consulta ----------------------------------------------------------

    def _local_state(self) -> dict[str, Any] | None:
        if not self.enabled:
            return {"estado": "desactivada", "mensaje": "voz.clonada = false"}
        if not self.installed():
            return {
                "estado": "no_instalada",
                "mensaje": "ejecuta `npm run voz:instalar` para clonar voces en este equipo",
            }
        if not self.running():
            return {"estado": "detenida", "mensaje": "el servicio de voz no está en marcha"}
        return None

    async def status(self) -> dict[str, Any]:
        local = self._local_state()
        if local is not None:
            return {**local, "carpeta": str(self.folder)}
        now = time.monotonic()
        if self._status and now - self._status_at < STATUS_TTL_SECONDS:
            return self._status
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3, connect=1)) as client:
                res = await client.get(
                    f"{self.base_url}/estado", headers={"x-crisvis-token": self._token}
                )
            status = res.json() if res.status_code == 200 else {
                "estado": "error",
                "mensaje": f"el servicio respondió {res.status_code}",
            }
        except (httpx.HTTPError, ValueError):
            status = {"estado": "cargando", "mensaje": "arrancando el servicio de voz"}
        status.setdefault("carpeta", str(self.folder))
        self._status, self._status_at = status, now
        return status

    async def available(self) -> bool:
        """Merece la pena pedirle audio: está en marcha y no ha fallado.

        Mientras carga o le falta la muestra también cuenta: responde 503 al
        instante, la cara dice esa frase con la voz del navegador, y en cuanto
        la voz está lista se oye sin recargar la página.
        """
        if not self.running():
            return False
        return (await self.status()).get("estado") != "error"

    async def synthesize(self, text: str) -> tuple[int, bytes]:
        """(código HTTP, cuerpo). 200 trae un WAV; 503 = todavía no está lista."""
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=2)) as client:
                res = await client.post(
                    f"{self.base_url}/tts",
                    headers={"x-crisvis-token": self._token},
                    json={"text": text, "language": self.language},
                )
        except httpx.HTTPError as exc:
            return 502, str(exc).encode()
        if res.status_code == 503:
            with contextlib.suppress(ValueError):
                self._status, self._status_at = res.json(), time.monotonic()
        return res.status_code, res.content
