"""/tts con la voz clonada: la usa si está lista y, si no, deja hablar al navegador."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from crisvis.settings import Settings
from crisvis.voice.cloned import ClonedVoiceService, seed_samples, voice_samples
from crisvis.voice.speech import speech_router


class FakeCloned:
    def __init__(self, code: int, body: bytes, estado: str = "lista") -> None:
        self.code, self.body, self.estado = code, body, estado
        self.texts: list[str] = []

    async def available(self) -> bool:
        return self.estado != "error"

    async def status(self) -> dict[str, Any]:
        return {"estado": self.estado, "mensaje": ""}

    async def synthesize(self, text: str) -> tuple[int, bytes]:
        self.texts.append(text)
        return self.code, self.body


def _client(cloned: Any, eleven: str = "") -> TestClient:
    settings = Settings()
    settings.voz.elevenlabs_api_key = eleven
    brain = SimpleNamespace(status=SimpleNamespace(as_frame=lambda: {"ok": True}))
    app = FastAPI()
    app.include_router(speech_router(settings, brain, cloned))
    return TestClient(app)


def test_speaks_with_the_cloned_voice() -> None:
    cloned = FakeCloned(200, b"RIFFwav")
    client = _client(cloned)
    health = client.get("/health").json()
    assert health["tts"] is True
    assert health["ttsEngine"] == "clonada"
    res = client.post("/tts", json={"text": "Buenas tardes, señor."})
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.content == b"RIFFwav"
    assert cloned.texts == ["Buenas tardes, señor."]


def test_while_loading_the_browser_voice_covers() -> None:
    client = _client(FakeCloned(503, b"{}", estado="cargando"))
    assert client.post("/tts", json={"text": "Hola"}).status_code == 503


def test_a_broken_service_is_not_offered() -> None:
    client = _client(FakeCloned(500, b"", estado="error"))
    health = client.get("/health").json()
    assert health["tts"] is False
    assert health["ttsEngine"] == "navegador"
    assert client.post("/tts", json={"text": "Hola"}).status_code == 503


def test_the_project_voice_travels_with_the_repo() -> None:
    bundled = Path(__file__).resolve().parents[1] / "services" / "voz" / "muestras"
    assert voice_samples(bundled), "falta la voz de serie en services/voz/muestras"


def test_a_fresh_machine_gets_the_bundled_voice(tmp_path: Path, monkeypatch: Any) -> None:
    service = tmp_path / "servicio"
    (service / "muestras").mkdir(parents=True)
    (service / "muestras" / "adam.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("CRISVIS_VOZ_DIR", str(service))
    folder = tmp_path / "voz"
    assert [p.name for p in seed_samples(folder)] == ["adam.wav"]
    assert (folder / "adam.wav").read_bytes() == b"RIFF"


def test_a_voice_of_your_own_is_never_replaced(tmp_path: Path, monkeypatch: Any) -> None:
    service = tmp_path / "servicio"
    (service / "muestras").mkdir(parents=True)
    (service / "muestras" / "adam.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("CRISVIS_VOZ_DIR", str(service))
    folder = tmp_path / "voz"
    folder.mkdir()
    (folder / "mia.mp3").write_bytes(b"ID3")
    assert seed_samples(folder) == []
    assert [p.name for p in voice_samples(folder)] == ["mia.mp3"]


def test_not_installed_is_reported_and_never_started(tmp_path: Any) -> None:
    settings = Settings(home=tmp_path)
    service = ClonedVoiceService(settings)
    assert not service.installed()
    service.start()
    assert not service.running()
    assert service.folder == tmp_path / "voz"
