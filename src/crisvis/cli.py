"""Línea de órdenes: ``crisvis`` arranca todo; el resto son utilidades.

crisvis                 arranca el núcleo y abre la interfaz
crisvis init            crea ~/.crisvis/crisvis.toml
crisvis doctor          comprueba motor, modelos, interfaz y voz
crisvis preguntar TEXTO una pregunta por terminal, sin interfaz
crisvis voz [AUDIO...]  usa esos audios como muestra de la voz clonada
crisvis voz --preparar CARPETA   limpia y une muchos clips en una sola muestra
crisvis permisos libre [--minutos N]   LIBRE (acción de administración; 0 = sin caducidad)
crisvis permisos confirmar             volver a CONFIRMAR
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from crisvis import __version__
from crisvis.settings import (
    DEFAULT_TOML,
    default_config_path,
    load_dotenv,
    load_settings,
    prepare_environment,
)


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _config_path(args: argparse.Namespace) -> Path | None:
    return Path(args.config).expanduser() if getattr(args, "config", None) else None


def cmd_init(args: argparse.Namespace) -> int:
    path = _config_path(args) or default_config_path()
    if path.exists() and not args.force:
        print(f"Ya existe: {path}  (usa --force para reescribirlo)")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_TOML, encoding="utf-8")
    print(f"Configuración creada en {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    settings = load_settings(_config_path(args))
    if args.host:
        settings.servidor.host = args.host
    if args.port:
        settings.servidor.port = args.port
    if args.no_browser:
        settings.servidor.abrir_navegador = False

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname).1s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore", "openjarvis.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from crisvis.app import STATIC_DIR, create_app

    app = create_app(settings)
    host, port = settings.servidor.host, settings.servidor.port
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    url = f"http://{shown}:{port}"
    print(f"\n  CRISVIS {__version__}  ·  {url}\n")
    if not (STATIC_DIR / "index.html").exists():
        print("  (La interfaz no está compilada: ejecuta `npm run build`.)\n")
    if settings.servidor.abrir_navegador:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning", ws_max_size=16 * 1024 * 1024)
    return 0


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "OK " if ok else "-- "
    print(f"  [{mark}] {label}" + (f": {detail}" if detail else ""))
    return ok


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(_config_path(args))
    prepare_environment(settings)
    import os

    print(f"\nCrisvis {__version__} · Python {sys.version.split()[0]}")
    print(f"  Configuración: {default_config_path() if not args.config else args.config}")
    brain_home = os.environ.get("OPENJARVIS_HOME", "~/.openjarvis")
    print(f"  Datos del cerebro (OPENJARVIS_HOME): {brain_home}\n")

    from importlib.metadata import version

    _check(True, "OpenJarvis", version("openjarvis"))
    from crisvis.brain.memory import native_available

    native = native_available()
    _check(
        native,
        "Extensión nativa de OpenJarvis (openjarvis_rust)",
        "instalada"
        if native
        else "no instalada: memoria con el backend propio de Crisvis; "
        "sin escáneres de secretos/PII ni limitador. Ver docs/ARQUITECTURA.md",
    )

    from crisvis.brain.openjarvis_brain import OpenJarvisBrain

    brain = OpenJarvisBrain(settings)
    status = brain.start()
    _check(
        status.ok,
        "Motor de IA",
        f"{status.engine} / {status.model}" if status.ok else status.message,
    )
    if status.ok and status.message:
        print(f"         {status.message}")
    _check(bool(status.memory), "Memoria", status.memory or "desactivada")
    _check(bool(status.tools), "Herramientas de OpenJarvis", ", ".join(status.tools) or "ninguna")
    if settings.cerebro.mcp:
        _check(
            bool(status.servers), "Servidores MCP", ", ".join(status.servers) or "ninguno respondió"
        )
    _check(
        bool(brain.vision_model),
        "Visión (cámara y pantalla)",
        brain.vision_model or "ninguna; `ollama pull qwen3-vl:4b-instruct` para activarla",
    )
    brain.close()

    from crisvis.app import STATIC_DIR

    _check((STATIC_DIR / "index.html").exists(), "Interfaz compilada", str(STATIC_DIR))
    eleven = bool(settings.voz.elevenlabs_api_key)
    whisper = importlib.util.find_spec("faster_whisper") is not None
    _check(eleven, "ElevenLabs (voz premium)", "configurado" if eleven else "no; voz del navegador")
    _check(
        whisper,
        "Transcripción local (faster-whisper)",
        "instalada" if whisper else "no; `uv sync --extra voz-local` para activarla",
    )
    from crisvis.voice.cloned import ClonedVoiceService, bundled_samples

    cloned = ClonedVoiceService(settings)
    samples = _voice_samples(cloned.folder)
    bundled = bundled_samples()
    if not cloned.installed():
        detail = "no instalada; `npm run voz:instalar` para activarla"
    elif not samples and bundled:
        samples = bundled
        detail = f"lista con la voz de serie ({', '.join(p.name for p in bundled)})"
    elif not samples:
        detail = f"instalada, sin muestra: `npm run voz -- archivo.mp3` (carpeta {cloned.folder})"
    else:
        detail = f"lista con {', '.join(p.name for p in samples)}"
    _check(cloned.installed() and bool(samples), "Voz clonada local (XTTS-v2)", detail)
    print()
    return 0 if status.ok else 1


def _voice_samples(folder: Path) -> list[Path]:
    from crisvis.voice.cloned import voice_samples

    return voice_samples(folder)


def cmd_voice(args: argparse.Namespace) -> int:
    import shutil

    from crisvis.voice.cloned import SAMPLE_EXTENSIONS, ClonedVoiceService

    settings = load_settings(_config_path(args))
    cloned = ClonedVoiceService(settings)
    folder = cloned.folder
    folder.mkdir(parents=True, exist_ok=True)

    if args.preparar:
        import subprocess

        from crisvis.voice.cloned import service_dir, service_python

        if not args.audios:
            print("Indica la carpeta o los audios a preparar.", file=sys.stderr)
            return 1
        if not cloned.installed():
            print("El motor de voz no está instalado: ejecuta `npm run voz:instalar`.")
            return 1
        out = folder / f"{args.nombre}.wav"
        staging = folder / f".{args.nombre}.preparando"
        cmd = [str(service_python()), "-W", "ignore", "-m", "crisvis_voz.preparar"]
        cmd += [str(Path(a).expanduser()) for a in args.audios] + ["--salida", str(staging)]
        if args.sin_separar:
            cmd.append("--sin-separar")
        code = subprocess.call(cmd, cwd=str(service_dir()))
        if code != 0 or not staging.is_file():
            staging.unlink(missing_ok=True)
            return code or 1
        for old in _voice_samples(folder):
            old.unlink()
        staging.replace(out)
    elif args.audios:
        sources = [Path(a).expanduser() for a in args.audios]
        for src in sources:
            if not src.is_file():
                print(f"No existe: {src}", file=sys.stderr)
                return 1
            if src.suffix.lower() not in SAMPLE_EXTENSIONS:
                print(
                    f"Formato no admitido ({src.name}): usa wav, mp3, flac u ogg", file=sys.stderr
                )
                return 1
        if args.reemplazar:
            for old in _voice_samples(folder):
                old.unlink()
        for src in sources:
            shutil.copy2(src, folder / src.name)
            print(f"Muestra añadida: {src.name}")

    samples = _voice_samples(folder)
    print(f"\nCarpeta de la voz: {folder}")
    print("Muestras: " + (", ".join(p.name for p in samples) if samples else "ninguna"))
    if not cloned.installed():
        print("El motor de voz no está instalado: ejecuta `npm run voz:instalar`.")
    elif samples:
        print("Con Crisvis en marcha, la nueva voz se aplica sola en unos segundos.")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    settings = load_settings(_config_path(args))
    prepare_environment(settings)
    logging.basicConfig(level=logging.WARNING)

    from crisvis.brain.events import Finished, TextDelta, ToolFinished, ToolStarted
    from crisvis.brain.openjarvis_brain import OpenJarvisBrain
    from crisvis.security import Decision, PermissionPolicy

    policy = PermissionPolicy(settings.permisos)
    brain = OpenJarvisBrain(settings)
    status = brain.start()
    if not status.ok:
        print(status.message, file=sys.stderr)
        return 1

    from crisvis.security.guard import Risk, SequenceGuard, assess

    sequence = SequenceGuard()

    async def gate(name: str, call_args: dict, spec: object) -> str | None:
        verdict = policy.evaluate(name)
        decision = verdict.decision
        risk = sequence.check(name, call_args) or assess(name, call_args, verdict.tier)
        if risk.blocked:
            return f"Bloqueado por la política de seguridad: {risk.reason}."
        if decision is Decision.ALLOW and (
            getattr(spec, "requires_confirmation", False) or risk.risk is Risk.HIGH
        ):
            decision = Decision.CONFIRM
        if decision is Decision.DENY:
            return f"Bloqueado por el modo de permisos ({policy.mode})."
        sequence.commit(name, call_args, risk.risk)
        if decision is Decision.CONFIRM:
            answer = await asyncio.to_thread(input, f"\n¿Permitir {name} {call_args}? [s/N] ")
            if answer.strip().lower() not in ("s", "si", "sí", "y", "yes"):
                return "El usuario no lo autorizó."
        return None

    async def run() -> None:
        agent = brain.new_agent(visible=policy.visible, gate=gate)
        async for event in agent.run_stream(args.texto, brain.context_for(args.texto, [])):
            if isinstance(event, TextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, ToolStarted):
                print(f"  [{event.name} {event.args}]", file=sys.stderr, flush=True)
            elif isinstance(event, ToolFinished) and not event.ok:
                print(f"  [{event.name}: falló]", file=sys.stderr, flush=True)
            elif isinstance(event, Finished):
                print()

    try:
        asyncio.run(run())
    finally:
        brain.close()
    return 0


def cmd_permissions(args: argparse.Namespace) -> int:
    """Administración del modo: solo desde una terminal del propio usuario.

    Lee el token de administración de <home>/admin.token (lo escribe el núcleo al
    arrancar) y habla con el núcleo en loopback. El navegador no puede hacerlo.
    """
    import getpass
    import json

    import httpx

    from crisvis.security.modes import LIBRE_PHRASE

    settings = load_settings(_config_path(args))
    port = args.port or settings.servidor.port
    base = f"http://127.0.0.1:{port}"
    try:
        token = settings.admin_token_path.read_text(encoding="utf-8").strip()
    except OSError:
        print("El núcleo no está en marcha (no hay admin.token).", file=sys.stderr)
        return 1
    headers = {"x-crisvis-admin": token}

    if args.accion == "estado":
        state = settings.permissions_state_path
        print(f"Persistido: {state.read_text(encoding='utf-8') if state.exists() else '(nada)'}")
        return 0
    if args.accion == "confirmar":
        res = httpx.post(f"{base}/api/admin/confirmar", headers=headers, timeout=10)
    else:
        print(
            "\n  ATENCIÓN: en LIBRE el asistente escribe archivos y abre aplicaciones sin "
            "preguntar.\n  Teclado, ratón, portapapeles, órdenes y acciones de sistema se siguen "
            "aprobando una a una;\n  lo bloqueado por política sigue bloqueado.\n"
            + (
                f"  Caduca a los {args.minutos} minutos y al reiniciar vuelve a CONFIRMAR.\n"
                if args.minutos
                else "  Sin caducidad: dura hasta `crisvis permisos confirmar` o reiniciar.\n"
            )
        )
        typed = input(f"  Escribe «{LIBRE_PHRASE}» para continuar: ").strip()
        if typed != LIBRE_PHRASE:
            print("Cancelado.")
            return 1
        res = httpx.post(
            f"{base}/api/admin/libre",
            headers=headers,
            json={
                "minutos": args.minutos,
                "confirmacion": typed,
                "quien": f"cli:{getpass.getuser()}",
            },
            timeout=10,
        )
    if res.status_code != 200:
        print(f"Rechazado ({res.status_code}): {res.text}", file=sys.stderr)
        return 1
    print(json.dumps(res.json(), ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crisvis", description="Asistente personal local.")
    parser.add_argument("--version", action="version", version=f"crisvis {__version__}")
    parser.add_argument("--config", help="Ruta a crisvis.toml")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="Crear el archivo de configuración")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="Comprobar la instalación")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("preguntar", help="Una pregunta por terminal")
    p.add_argument("texto")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("voz", help="Muestras de la voz clonada")
    p.add_argument("audios", nargs="*", help="Audios limpios de la voz (wav/mp3/flac/ogg)")
    p.add_argument("--reemplazar", action="store_true", help="Quitar las muestras anteriores")
    p.add_argument(
        "--preparar",
        action="store_true",
        help="Limpiar y unir clips (aísla la voz, descarta los que no encajan) en una muestra",
    )
    p.add_argument("--nombre", default="voz", help="Nombre de la muestra preparada")
    p.add_argument("--sin-separar", action="store_true", help="No aislar la voz con Demucs")
    p.set_defaults(func=cmd_voice)

    p = sub.add_parser("iniciar", help="Arrancar (lo mismo que sin orden)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("permisos", help="Administrar el modo de permisos (LIBRE temporal)")
    p.add_argument("accion", choices=["libre", "confirmar", "estado"])
    p.add_argument("--minutos", type=float, default=10)
    p.set_defaults(func=cmd_permissions)
    return parser


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    func = getattr(args, "func", cmd_run)
    try:
        return int(func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except ValueError as exc:
        print(f"Error de configuración: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
