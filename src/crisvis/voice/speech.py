"""/health, /tts y /stt: la voz del lado del núcleo.

Sin nada configurado, la cara usa el reconocimiento y la voz del propio
navegador. Con la voz clonada instalada (services/voz), el núcleo habla con
ella sin salir del equipo. Con una clave de ElevenLabs, el núcleo transcribe
(Scribe) y habla con su voz; la clave nunca llega al navegador. Con el extra
``voz-local`` instalado, la transcripción se hace en este equipo con
faster-whisper (vía OpenJarvis) y el audio no sale de la máquina.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from crisvis import __version__
from crisvis.settings import Settings
from crisvis.voice.cloned import ClonedVoiceService

log = logging.getLogger(__name__)

MAX_TTS_CHARS = 4000
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MIN_AUDIO_BYTES = 1200


class LocalWhisper:
    """faster-whisper de OpenJarvis, cargado al primer uso."""

    def __init__(self, model_size: str) -> None:
        self._model_size = model_size
        self._backend: Any = None
        self._lock = threading.Lock()

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("faster_whisper") is not None

    def transcribe(self, audio: bytes, fmt: str, language: str) -> str:
        with self._lock:
            if self._backend is None:
                from openjarvis.speech.faster_whisper import FasterWhisperBackend

                self._backend = FasterWhisperBackend(
                    model_size=self._model_size, device="auto", compute_type="int8"
                )
            result = self._backend.transcribe(audio, format=fmt, language=language)
        return result.text.strip()


def _ext_for(content_type: str) -> str:
    if "ogg" in content_type:
        return "ogg"
    if "mp4" in content_type or "mpeg" in content_type:
        return "mp4"
    if "wav" in content_type:
        return "wav"
    return "webm"


def speech_router(
    settings: Settings, brain: Any, cloned: ClonedVoiceService | None = None
) -> APIRouter:
    router = APIRouter()
    voz = settings.voz
    whisper = (
        LocalWhisper(voz.whisper_modelo) if voz.whisper_local and LocalWhisper.installed() else None
    )
    language = settings.asistente.idioma.split("-")[0].lower()

    def eleven_key() -> str:
        return voz.elevenlabs_api_key

    @router.get("/health")
    async def health() -> dict[str, Any]:
        status = brain.status
        cloned_status = await cloned.status() if cloned else None
        use_cloned = cloned is not None and await cloned.available()
        tts_engine = "clonada" if use_cloned else ("elevenlabs" if eleven_key() else "navegador")
        return {
            "ok": True,
            "version": __version__,
            "tts": tts_engine != "navegador",
            "ttsEngine": tts_engine,
            "clonedVoice": cloned_status,
            "stt": bool(eleven_key()) or whisper is not None,
            "sttEngine": "elevenlabs" if eleven_key() else ("whisper" if whisper else "browser"),
            "name": settings.asistente.nombre,
            "lang": settings.asistente.idioma,
            "wake": settings.asistente.palabras_activacion,
            "address": settings.asistente.tratamiento,
            "voiceStyle": settings.voz.estilo,
            "brain": status.as_frame(),
        }

    @router.post("/tts")
    async def tts(request: Request) -> Response:
        key = eleven_key()
        use_cloned = cloned is not None and await cloned.available()
        if not key and not use_cloned:
            return Response("sin voz en el núcleo", status_code=503)
        body = await request.body()
        if len(body) > 64 * 1024:
            return Response("cuerpo demasiado grande", status_code=400)
        try:
            payload = await request.json()
        except ValueError:
            return Response("JSON inválido", status_code=400)
        text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""
        if not text:
            return Response("sin texto", status_code=400)

        if use_cloned:
            assert cloned is not None
            code, audio = await cloned.synthesize(text[:MAX_TTS_CHARS])
            if code == 200:
                return Response(
                    audio, media_type="audio/wav", headers={"cache-control": "no-cache"}
                )
            if not key:
                # 503 mientras carga: la cara dice esta frase con la voz del navegador.
                return Response(audio, status_code=503 if code == 503 else 502)
            log.info("voz clonada no disponible (%s); se usa ElevenLabs", code)

        client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        req = client.build_request(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voz.elevenlabs_voz}/stream"
            "?output_format=mp3_22050_32&optimize_streaming_latency=3",
            headers={"xi-api-key": key, "content-type": "application/json"},
            json={
                "text": text[:MAX_TTS_CHARS],
                # Flash v2.5 es multilingüe y el de menor latencia.
                "model_id": "eleven_flash_v2_5",
                "language_code": language,
                "voice_settings": {"stability": 0.4, "similarity_boost": 0.75, "speed": 1.05},
            },
        )
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            return Response(str(exc), status_code=502)
        if upstream.status_code != 200:
            detail = (await upstream.aread()).decode("utf-8", "replace")[:500]
            await upstream.aclose()
            await client.aclose()
            return Response(detail, status_code=upstream.status_code)

        async def audio() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            audio(), media_type="audio/mpeg", headers={"cache-control": "no-cache"}
        )

    @router.post("/stt")
    async def stt(request: Request) -> Response:
        key = eleven_key()
        if not key and whisper is None:
            return Response("sin transcriptor en el núcleo", status_code=503)
        ctype = request.headers.get("content-type") or "audio/webm"
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                return Response("audio demasiado grande", status_code=413)
            chunks.append(chunk)
        if size < MIN_AUDIO_BYTES:
            return JSONResponse({"text": ""})
        audio = b"".join(chunks)
        ext = _ext_for(ctype)

        if key:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10)) as client:
                    upstream = await client.post(
                        "https://api.elevenlabs.io/v1/speech-to-text",
                        headers={"xi-api-key": key},
                        data={"model_id": "scribe_v1", "language_code": language},
                        files={"file": (f"speech.{ext}", audio, ctype)},
                    )
            except httpx.HTTPError as exc:
                return Response(str(exc), status_code=502)
            if upstream.status_code != 200:
                return Response(upstream.text[:500], status_code=upstream.status_code)
            return JSONResponse({"text": str(upstream.json().get("text") or "").strip()})

        assert whisper is not None
        try:
            text = await asyncio.to_thread(whisper.transcribe, audio, ext, language)
        except Exception as exc:  # noqa: BLE001
            log.warning("faster-whisper falló: %s", exc)
            return Response(f"transcripción local fallida: {exc}", status_code=502)
        return JSONResponse({"text": text})

    return router
