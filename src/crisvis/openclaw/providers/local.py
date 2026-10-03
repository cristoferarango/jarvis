"""Fuentes reales del propio equipo (solo lectura, sin cuentas).

Las órdenes externas son fijas (nvidia-smi, git, docker), con lista de
argumentos, sin shell y con tiempo máximo. Nada de lo que diga el modelo o el
usuario llega a estas órdenes.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import psutil

from crisvis.openclaw.contracts import BriefingItem, ConnectorMode, SourceState
from crisvis.openclaw.providers.base import FetchContext, Fetched, Provider, SourceError, meta

REPO_ROOT = Path(__file__).resolve().parents[4]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_fixed(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """Lanza una orden fija del adaptador. Nunca con shell."""
    exe = shutil.which(argv[0])
    if exe is None:
        raise FileNotFoundError(argv[0])
    proc = subprocess.run(  # noqa: S603 - argv fijo, sin shell
        [exe, *argv[1:]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        creationflags=_NO_WINDOW,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode, proc.stdout


def _level(value: float, warn: float, critical: float, higher_is_worse: bool = True) -> str:
    if higher_is_worse:
        return "critical" if value >= critical else "warn" if value >= warn else "ok"
    return "critical" if value <= critical else "warn" if value <= warn else "ok"


def _gpu() -> list[BriefingItem]:
    try:
        code, out = run_fixed(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=4,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return [
            BriefingItem(
                id="gpu",
                title="GPU",
                subtitle="nvidia-smi no disponible",
                meta=meta(metrica="gpu", estado="unknown"),
            )
        ]
    items = []
    for i, line in enumerate(out.strip().splitlines()):
        parts = [p.strip() for p in line.split(",")]
        if code != 0 or len(parts) < 5:
            continue
        name, used, total, util, temp = parts[:5]
        try:
            pct = round(100 * float(used) / float(total))
            temp_c = float(temp)
        except ValueError:
            continue
        state = _level(pct, 92, 98)
        if temp_c >= 85:
            state = "warn" if state == "ok" else state
        items.append(
            BriefingItem(
                id=f"vram-{i}",
                title=f"VRAM · {name}",
                subtitle=f"{float(used) / 1024:.1f} de {float(total) / 1024:.1f} GB "
                f"({pct}%) · GPU {util}% · {temp} °C",
                meta=meta(metrica="vram_pct", valor=pct, estado=state, temperatura=temp_c),
            )
        )
    return items


def _ollama(base: str) -> list[BriefingItem]:
    try:
        with httpx.Client(base_url=base, timeout=2.5) as client:
            tags = client.get("/api/tags").json().get("models", [])
            loaded = client.get("/api/ps").json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return [
            BriefingItem(
                id="ollama",
                title="Ollama",
                subtitle="no responde en 127.0.0.1:11434",
                meta=meta(metrica="servicio", servicio="ollama", estado="critical", critico=True),
            )
        ]
    names = ", ".join(m.get("name", "?") for m in loaded) or "ninguno cargado"
    return [
        BriefingItem(
            id="ollama",
            title="Ollama",
            subtitle=f"{len(tags)} modelos instalados · en memoria: {names}",
            meta=meta(metrica="servicio", servicio="ollama", estado="ok", critico=True),
        )
    ]


def _git() -> list[BriefingItem]:
    try:
        code, out = run_fixed(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain=v1", "--branch"], timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if code != 0:
        return []
    lines = out.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else "?"
    changed = [ln for ln in lines[1:] if ln and not ln[3:].startswith(".venv")]
    return [
        BriefingItem(
            id="git",
            title="Git · CRISVIS",
            subtitle=f"{head} · {len(changed)} cambios sin commit",
            meta=meta(metrica="git", estado="ok", cambios=len(changed), rama=head),
        )
    ]


def snapshot(brain_status: Callable[[], dict[str, Any]], ollama_url: str) -> Fetched:
    items: list[BriefingItem] = []
    try:
        brain = brain_status() or {}
    except Exception:  # noqa: BLE001
        brain = {"ok": False}
    ok = brain.get("ok") is True
    items.append(
        BriefingItem(
            id="crisvis",
            title="CRISVIS",
            subtitle=f"cerebro {brain.get('engine', '?')}/{brain.get('model', '?')} · "
            + ("listo" if ok else "con fallos"),
            meta=meta(
                metrica="servicio",
                servicio="crisvis",
                estado="ok" if ok else "critical",
                critico=True,
            ),
        )
    )
    uptime_h = (time.time() - psutil.boot_time()) / 3600
    items.append(
        BriefingItem(
            id="windows",
            title="Windows",
            subtitle=f"{platform.platform(terse=True)} · encendido hace {uptime_h:.0f} h",
            meta=meta(metrica="so", estado="ok"),
        )
    )
    mem = psutil.virtual_memory()
    items.append(
        BriefingItem(
            id="ram",
            title="Memoria RAM",
            subtitle=f"{mem.used / 2**30:.1f} de {mem.total / 2**30:.1f} GB ({mem.percent:.0f}%)",
            meta=meta(
                metrica="ram_pct", valor=round(mem.percent), estado=_level(mem.percent, 85, 95)
            ),
        )
    )
    drive = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
    disk = psutil.disk_usage(drive)
    free_pct = round(100 - disk.percent)
    items.append(
        BriefingItem(
            id="disco",
            title=f"Disco {drive}",
            subtitle=f"{disk.free / 2**30:.0f} GB libres ({free_pct}%)",
            meta=meta(
                metrica="disco_libre_pct",
                valor=free_pct,
                estado=_level(free_pct, 20, 10, higher_is_worse=False),
            ),
        )
    )
    items.extend(_gpu())
    items.extend(_ollama(ollama_url))
    items.extend(_git())
    critical = sum(1 for i in items if i.meta.get("estado") == "critical")
    return Fetched(items=items, counts={"criticos": critical}, message="Estado real del equipo.")


class LocalSystemProvider(Provider):
    id, name, tab, tool = "sistema", "Sistema local", "sistema", "sistema.snapshot"
    mode = ConnectorMode.REAL
    integration = "custom (psutil, nvidia-smi, Ollama, git)"

    def __init__(
        self,
        brain_status: Callable[[], dict[str, Any]],
        ollama_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self._brain_status = brain_status
        self._ollama = ollama_url

    def hint(self) -> str:
        return "Lectura real del equipo, sin cuentas."

    async def fetch(self, ctx: FetchContext) -> Fetched:
        return await asyncio.to_thread(snapshot, self._brain_status, self._ollama)


def _containers() -> Fetched:
    if shutil.which("docker") is None:
        raise SourceError(SourceState.UNAVAILABLE, "Docker no está instalado en este equipo.")
    try:
        code, out = run_fixed(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=6)
    except subprocess.TimeoutExpired as exc:
        raise SourceError(SourceState.TIMEOUT, "Docker no respondió a tiempo.") from exc
    if code != 0:
        raise SourceError(SourceState.UNAVAILABLE, "Docker está instalado pero no está en marcha.")
    items = []
    for i, line in enumerate(out.splitlines()):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        state = str(row.get("State", "")).lower()
        items.append(
            BriefingItem(
                id=f"ctr-{i}",
                title=str(row.get("Names", "?")),
                subtitle=str(row.get("Status", state)),
                meta=meta(estado=state, imagen=str(row.get("Image", ""))),
            )
        )
    running = sum(1 for i in items if i.meta.get("estado") == "running")
    return Fetched(
        items=items,
        counts={"en_marcha": running, "parados": len(items) - running},
        message="Contenedores reales." if items else "Docker funciona, sin contenedores.",
    )


class DockerProvider(Provider):
    id, name, tab, tool = "docker", "Docker", "sistema", "docker.list_containers"
    mode = ConnectorMode.REAL
    integration = "custom (docker CLI, solo lectura)"

    def hint(self) -> str:
        return "Lectura real de `docker ps`, si Docker está instalado."

    async def fetch(self, ctx: FetchContext) -> Fetched:
        return await asyncio.to_thread(_containers)
