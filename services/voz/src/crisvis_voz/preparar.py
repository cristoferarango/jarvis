"""Convierte un montón de clips de una voz en una sola muestra limpia para XTTS.

Para cada clip: aísla la voz de la música y los efectos (Demucs), recorta los
silencios, nivela el volumen y compara su timbre con el del resto; los clips
que suenan a otra persona o que eran casi todo fondo se descartan. Los buenos
se unen, los más limpios primero, en un único WAV.

    python -m crisvis_voz.preparar CARPETA_O_AUDIOS... --salida voz.wav
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg"}
OUT_RATE = 22050
GAP_SECONDS = 0.3
TARGET_RMS_DB = -20.0
MIN_SECONDS = 0.4
# Por debajo de esta similitud de timbre con el resto, el clip no es la misma voz.
MIN_SIMILARITY = 0.55
# Si más de esta fracción de la energía era fondo, la voz aislada queda dañada.
MAX_BACKGROUND = 0.75

log = logging.getLogger("crisvis_voz.preparar")


@dataclass
class Clip:
    path: Path
    voice: np.ndarray  # mono, OUT_RATE
    background: float  # fracción de energía que no era voz
    similarity: float = 1.0
    kept: bool = True
    reason: str = ""

    @property
    def seconds(self) -> float:
        return len(self.voice) / OUT_RATE


def collect(inputs: list[Path]) -> tuple[list[Path], list[Path]]:
    found: list[Path] = []
    skipped: list[Path] = []
    for item in inputs:
        paths = sorted(item.iterdir()) if item.is_dir() else [item]
        for p in paths:
            if not p.is_file():
                continue
            (found if p.suffix.lower() in AUDIO_EXTENSIONS else skipped).append(p)
    return found, skipped


def load(path: Path, rate: int):
    import soundfile as sf
    import torch
    import torchaudio.functional as AF

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.T.copy())
    if sr != rate:
        wav = AF.resample(wav, sr, rate)
    return wav


def isolate(paths: list[Path], device: str, separate: bool) -> list[Clip]:
    import librosa
    import torch
    import torchaudio.functional as AF

    model = None
    if separate:
        from demucs.apply import apply_model
        from demucs.pretrained import get_model

        model = get_model("htdemucs").to(device).eval()
        vocals_index = model.sources.index("vocals")
        rate = model.samplerate
    else:
        rate = OUT_RATE

    clips: list[Clip] = []
    for path in paths:
        try:
            wav = load(path, rate)
        except Exception as exc:  # noqa: BLE001
            log.warning("  %-45s no se puede leer (%s)", path.name, type(exc).__name__)
            continue
        if model is not None:
            if wav.shape[0] == 1:
                wav = wav.repeat(2, 1)
            wav = wav[:2]
            ref = wav.mean(0)
            mean, std = ref.mean(), ref.std() + 1e-8
            with torch.no_grad():
                sources = apply_model(
                    model, ((wav - mean) / std)[None].to(device), device=device, progress=False
                )[0]
            sources = sources * std + mean
            vocals = sources[vocals_index].cpu()
            rest = (sources.sum(0) - sources[vocals_index]).cpu()
            total = float((vocals**2).sum() + (rest**2).sum()) + 1e-12
            background = float((rest**2).sum()) / total
            mono = AF.resample(vocals.mean(0, keepdim=True), rate, OUT_RATE)[0].numpy()
        else:
            background = 0.0
            mono = wav.mean(0).numpy()

        mono, _ = librosa.effects.trim(mono, top_db=40)
        rms = float(np.sqrt(np.mean(mono**2))) if len(mono) else 0.0
        if rms > 0:
            mono = mono * (10 ** (TARGET_RMS_DB / 20) / rms)
            peak = float(np.max(np.abs(mono)))
            if peak > 0.95:
                mono = mono * (0.95 / peak)
        clips.append(Clip(path, mono.astype(np.float32), background))
    return clips


def score_speakers(clips: list[Clip], device: str) -> None:
    """Similitud coseno de cada clip con el timbre medio de los demás."""
    import torch

    os.environ.setdefault("COQUI_TOS_AGREED", "1")
    from TTS.api import TTS

    xtts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False).to(device)
    model = xtts.synthesizer.tts_model
    embeddings = []
    with torch.no_grad():
        for clip in clips:
            audio = torch.from_numpy(clip.voice)[None].to(device)
            emb = model.get_speaker_embedding(audio, OUT_RATE).flatten()
            embeddings.append(torch.nn.functional.normalize(emb, dim=0))
    stacked = torch.stack(embeddings)
    for i, clip in enumerate(clips):
        others = torch.cat([stacked[:i], stacked[i + 1 :]]).mean(0)
        clip.similarity = float(
            torch.dot(stacked[i], torch.nn.functional.normalize(others, dim=0))
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara una muestra de voz a partir de clips")
    parser.add_argument("entradas", nargs="+", type=Path, help="Carpetas o audios")
    parser.add_argument("--salida", type=Path, required=True, help="WAV resultante")
    parser.add_argument("--sin-separar", action="store_true", help="No aislar la voz con Demucs")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("TTS", "trainer", "fsspec"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    import soundfile as sf
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    paths, skipped = collect([p.expanduser() for p in args.entradas])
    for p in skipped:
        log.info("Se omite %s (formato no admitido: usa wav, mp3, flac u ogg)", p.name)
    if not paths:
        log.error("No hay audios que preparar.")
        return 1

    log.info(
        "Aislando la voz de %d clip(s) en %s%s…",
        len(paths),
        device,
        "" if not args.sin_separar else " (sin separar)",
    )
    clips = isolate(paths, device, separate=not args.sin_separar)
    for c in clips:
        if c.seconds < MIN_SECONDS:
            c.kept, c.reason = False, "demasiado corto"

    usable = [c for c in clips if c.kept]
    if len(usable) >= 3:
        log.info("Comparando timbres…")
        score_speakers(usable, device)
    for c in usable:
        if c.background > MAX_BACKGROUND:
            c.kept, c.reason = False, "casi todo era fondo"
        elif c.similarity < MIN_SIMILARITY:
            c.kept, c.reason = False, "no suena a la misma voz"

    log.info("\n  %-45s %6s %7s %6s  %s", "clip", "seg", "fondo", "timbre", "")
    for c in sorted(clips, key=lambda c: (not c.kept, -c.similarity)):
        log.info(
            "  %-45s %5.1fs %6.0f%% %6.2f  %s",
            c.path.name[:45],
            c.seconds,
            c.background * 100,
            c.similarity,
            "ok" if c.kept else f"DESCARTADO: {c.reason}",
        )

    kept = sorted((c for c in clips if c.kept), key=lambda c: c.background - c.similarity)
    if not kept:
        log.error("Ningún clip sirve como muestra.")
        return 1
    gap = np.zeros(int(GAP_SECONDS * OUT_RATE), dtype=np.float32)
    parts: list[np.ndarray] = []
    for c in kept:
        parts += [c.voice, gap]
    out = np.concatenate(parts[:-1])
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.salida), out, OUT_RATE, format="WAV", subtype="PCM_16")
    log.info(
        "\nMuestra lista: %s (%.1f s de voz de %d clip(s); %d descartado(s))",
        args.salida,
        len(out) / OUT_RATE,
        len(kept),
        len(clips) - len(kept),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
