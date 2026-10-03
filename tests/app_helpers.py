"""La aplicación de verdad (create_app) con un cerebro y una voz de mentira."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

BASE = "http://127.0.0.1:8787"
ORIGIN = BASE
EVIL = "http://evil.example"


class FakeBrain:
    vision_model = None
    see = None

    def __init__(self) -> None:
        self.status = SimpleNamespace(
            ok=True, message="", servers=[], engine="prueba", model="prueba",
            as_frame=lambda: {"ok": True},
        )
        self.reloads = 0

    def start(self) -> None: ...
    def close(self) -> None: ...
    def connect_engine(self, force: bool = False) -> bool:
        return True

    def connectors_changed(self) -> bool:
        return False

    def connector_status(self) -> list[Any]:
        return []

    def reload_connectors(self, force: bool = False) -> None:
        self.reloads += 1

    def mark_engine_failed(self, _exc: BaseException) -> None: ...

    def new_agent(self, **_kw: Any) -> Any:
        raise RuntimeError("sin cerebro en las pruebas")

    def context_for(self, *_a: Any) -> Any:
        return None


class FakeCloned:
    def __init__(self, _settings: Any) -> None:
        self.texts: list[str] = []

    def start(self) -> None: ...
    def stop(self) -> None: ...

    async def available(self) -> bool:
        return True

    async def status(self) -> dict[str, Any]:
        return {"estado": "lista", "mensaje": ""}

    async def synthesize(self, text: str) -> tuple[int, bytes]:
        self.texts.append(text)
        return 200, b"RIFFwav"


INDEX = """<!doctype html><html><head><meta charset="utf-8">
<script type="module" crossorigin src="/assets/index.js"></script>
<link rel="stylesheet" href="/assets/index.css"></head><body><div id="root"></div></body></html>"""


def make_app(
    tmp: Path, monkeypatch: pytest.MonkeyPatch, *, configure: Any = None
) -> tuple[Any, TestClient]:
    import crisvis.app as app_mod
    import crisvis.voice.cloned as cloned_mod
    from crisvis.settings import Settings

    static = tmp / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(INDEX, encoding="utf-8")
    (static / "assets" / "index.js").write_text("console.log('cara')", encoding="utf-8")
    monkeypatch.setattr(app_mod, "STATIC_DIR", static)
    monkeypatch.setattr(cloned_mod, "ClonedVoiceService", FakeCloned)
    monkeypatch.delenv("CRISVIS_DEV", raising=False)
    settings = Settings(home=tmp / "home")
    settings.openclaw.habilitado = False
    if configure:
        configure(settings)
    app = app_mod.create_app(settings, brain=FakeBrain())
    return app, TestClient(app, base_url=BASE)


def login(client: TestClient, origin: str = ORIGIN) -> str:
    """Lo que hace la página: cargarse (cookie de arranque) y pedir un token."""
    assert client.get("/").status_code == 200
    res = client.post("/api/sesion", headers={"origin": origin})
    assert res.status_code == 200, res.text
    return res.json()["token"]


def auth(token: str, origin: str = ORIGIN) -> dict[str, str]:
    return {"x-crisvis-token": token, "origin": origin}
