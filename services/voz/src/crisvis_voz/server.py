"""Voz clonada: XTTS-v2 sintetiza con el timbre de las muestras del usuario.

Lo lanza el núcleo de Crisvis como proceso hijo y solo le habla él: escucha en
127.0.0.1 y exige la clave que el núcleo le pasa en ``CRISVIS_VOZ_TOKEN``.

Las muestras son los audios de la carpeta de voz (por defecto
``~/.crisvis/voz``). La huella de la voz (los latentes de XTTS) se calcula una
vez y se guarda junto a ellas; si las muestras cambian, se recalcula sola.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import math
import os
import secrets
import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

log = logging.getLogger("crisvis_voz")

MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
SAMPLE_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}
LATENTS_FILE = ".latentes.pt"
MAX_TEXT_CHARS = 1500
# Idiomas que XTTS-v2 sabe hablar.
LANGUAGES = {
    "es", "en", "fr", "de", "it", "pt", "pl", "tr", "ru", "nl",
    "cs", "ar", "zh-cn", "ja", "hu", "ko", "hi",
}


MAX_REF_SECONDS = 180


def _durations(samples: list[Path]) -> tuple[float, float]:
    """(la más larga, total) en segundos; 0 si no se pueden leer."""
    import soundfile as sf

    lengths = []
    for p in samples:
        try:
            lengths.append(sf.info(str(p)).duration)
        except Exception:  # noqa: BLE001
            lengths.append(0.0)
    return max(lengths, default=0.0), sum(lengths)


class ClonedVoice:
    def __init__(self, folder: Path, language: str) -> None:
        self.folder = folder
        self.language = language if language in LANGUAGES else "es"
        self.state = "cargando"
        self.message = "cargando el modelo XTTS-v2"
        self.device = "cpu"
        self._model: Any = None
        self._latents: tuple[Any, Any] | None = None
        self._signature = ""
        self._lock = threading.Lock()

    # -- muestras ----------------------------------------------------------

    def samples(self) -> list[Path]:
        if not self.folder.is_dir():
            return []
        return sorted(
            p for p in self.folder.iterdir()
            if p.is_file() and p.suffix.lower() in SAMPLE_EXTENSIONS
        )

    @staticmethod
    def _signature_of(samples: list[Path]) -> str:
        h = hashlib.sha256()
        for p in samples:
            st = p.stat()
            h.update(f"{p.name}|{st.st_size}|{st.st_mtime_ns}\n".encode())
        return h.hexdigest()

    # -- ciclo de vida -----------------------------------------------------

    def load(self) -> None:
        try:
            # Sin esto la primera descarga se queda esperando un "y" por consola.
            os.environ.setdefault("COQUI_TOS_AGREED", "1")
            import torch
            from TTS.api import TTS

            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.message = f"cargando XTTS-v2 en {self.device}"
            log.info(self.message)
            tts = TTS(MODEL_NAME, progress_bar=False).to(self.device)
            self._model = tts.synthesizer.tts_model
            with self._lock:
                self._refresh_latents()
        except Exception as exc:  # noqa: BLE001
            log.exception("no se pudo cargar XTTS-v2")
            self.state = "error"
            self.message = f"no se pudo cargar XTTS-v2: {exc}"

    def _refresh_latents(self) -> None:
        """Recalcula la huella si las muestras cambiaron. Llamar con el cerrojo."""
        import torch

        samples = self.samples()
        if not samples:
            self._latents = None
            self._signature = ""
            self.state = "sin_muestra"
            self.message = f"pon un audio de la voz (wav/mp3) en {self.folder}"
            return
        signature = self._signature_of(samples)
        if signature == self._signature and self._latents is not None:
            return

        cache = self.folder / LATENTS_FILE
        if cache.is_file():
            try:
                data = torch.load(cache, map_location=self.device)
                if data.get("firma") == signature:
                    self._latents = (data["gpt"], data["altavoz"])
                    self._signature = signature
                    self._ready(samples)
                    return
            except Exception:  # noqa: BLE001
                log.warning("caché de latentes ilegible; se recalcula")

        self.state = "cargando"
        self.message = f"aprendiendo la voz de {len(samples)} muestra(s)"
        log.info(self.message)
        cfg = self._model.config
        # XTTS por defecto solo mira los primeros 30 s de cada muestra (timbre)
        # y del conjunto (estilo); una muestra larga y limpia se aprovecha entera.
        longest, total = _durations(samples)
        gpt, speaker = self._model.get_conditioning_latents(
            audio_path=[str(p) for p in samples],
            gpt_cond_len=max(cfg.gpt_cond_len, min(math.ceil(total), MAX_REF_SECONDS)),
            max_ref_length=max(cfg.max_ref_len, min(math.ceil(longest), MAX_REF_SECONDS)),
            sound_norm_refs=cfg.sound_norm_refs,
        )
        self._latents = (gpt, speaker)
        self._signature = signature
        try:
            torch.save({"firma": signature, "gpt": gpt, "altavoz": speaker}, cache)
        except OSError:
            log.warning("no se pudo guardar la caché de latentes en %s", cache)
        self._ready(samples)

    def _ready(self, samples: list[Path]) -> None:
        self.state = "lista"
        self.message = f"voz clonada de {', '.join(p.name for p in samples)} ({self.device})"
        log.info(self.message)

    # -- uso ---------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        if self._model is not None and self._lock.acquire(blocking=False):
            try:
                self._refresh_latents()
            except Exception as exc:  # noqa: BLE001
                log.exception("no se pudo aprender la voz")
                self.state = "error"
                self.message = f"no se pudo aprender la voz: {exc}"
            finally:
                self._lock.release()
        return {
            "estado": self.state,
            "mensaje": self.message,
            "dispositivo": self.device,
            "carpeta": str(self.folder),
            "muestras": [p.name for p in self.samples()],
        }

    def synthesize(self, text: str, language: str | None) -> bytes:
        import numpy as np
        import soundfile as sf

        lang = language if language in LANGUAGES else self.language
        with self._lock:
            self._refresh_latents()
            if self._latents is None:
                raise RuntimeError(self.message)
            gpt, speaker = self._latents
            out = self._model.inference(
                text,
                lang,
                gpt,
                speaker,
                temperature=0.7,
                enable_text_splitting=True,
            )
        wav = out["wav"]
        if hasattr(wav, "cpu"):
            wav = wav.cpu().numpy()
        wav = np.clip(np.asarray(wav, dtype=np.float32).squeeze(), -1.0, 1.0)
        buf = io.BytesIO()
        rate = self._model.config.audio.output_sample_rate
        sf.write(buf, wav, rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()


def create_app(voice: ClonedVoice, token: str) -> FastAPI:
    app = FastAPI(title="crisvis-voz", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def only_the_core(request: Request, call_next: Any) -> Response:
        # Un navegador siempre manda Origin en un POST entre orígenes; el núcleo no.
        if request.headers.get("origin") or not secrets.compare_digest(
            request.headers.get("x-crisvis-token", ""), token
        ):
            return Response("prohibido", status_code=403)
        return await call_next(request)

    # Síncrona a propósito: aprender una voz nueva tarda segundos y FastAPI la
    # ejecuta en un hilo en vez de bloquear el bucle.
    @app.get("/estado")
    def estado() -> dict[str, Any]:
        return voice.status()

    @app.post("/tts")
    async def tts(request: Request) -> Response:
        if voice.state != "lista":
            return JSONResponse(voice.status(), status_code=503)
        try:
            payload = await request.json()
        except ValueError:
            return Response("JSON inválido", status_code=400)
        if not isinstance(payload, dict):
            return Response("JSON inválido", status_code=400)
        text = str(payload.get("text") or "").strip()[:MAX_TEXT_CHARS]
        if not text:
            return Response("sin texto", status_code=400)
        language = payload.get("language")
        try:
            import anyio

            audio = await anyio.to_thread.run_sync(
                voice.synthesize, text, language if isinstance(language, str) else None
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("síntesis fallida")
            return Response(f"síntesis fallida: {exc}", status_code=500)
        return Response(audio, media_type="audio/wav", headers={"cache-control": "no-cache"})

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Voz clonada local de Crisvis")
    parser.add_argument("--puerto", type=int, default=8788)
    parser.add_argument("--carpeta", type=Path, default=Path.home() / ".crisvis" / "voz")
    parser.add_argument("--idioma", default="es")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[voz] %(message)s")
    token = os.environ.get("CRISVIS_VOZ_TOKEN", "")
    if not token:
        raise SystemExit("falta CRISVIS_VOZ_TOKEN: este servicio lo arranca el núcleo de Crisvis")

    folder = args.carpeta.expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    voice = ClonedVoice(folder, args.idioma.split("-")[0].lower())
    threading.Thread(target=voice.load, name="xtts-load", daemon=True).start()
    uvicorn.run(
        create_app(voice, token), host="127.0.0.1", port=args.puerto, log_level="warning"
    )


if __name__ == "__main__":
    main()
