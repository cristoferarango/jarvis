import json
from pathlib import Path

import pytest

from crisvis.gateway import protocol
from crisvis.gateway.origin import origin_allowed
from crisvis.settings import ServerSettings, load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("origin", "ok"),
    [
        ("http://localhost:8787", True),
        ("http://127.0.0.1:8787", True),
        ("http://localhost:5173", True),
        ("http://127.0.0.1:5199", True),
        ("http://localhost:3000", False),
        ("https://evil.example", False),
        ("http://localhost.evil.example:8787", False),
        ("null", False),
        ("file://", False),
    ],
)
def test_origin(origin: str, ok: bool) -> None:
    assert origin_allowed(origin, ServerSettings()) is ok


def test_missing_origin_is_opt_in() -> None:
    assert not origin_allowed(None, ServerSettings())
    assert origin_allowed(None, ServerSettings(permitir_sin_origen=True))


def test_explicit_origin() -> None:
    server = ServerSettings(origenes_permitidos=["https://casa.example/"])
    assert origin_allowed("https://casa.example", server)


def test_protocol_matches_shared_contract() -> None:
    spec = json.loads((ROOT / "packages" / "protocol" / "frames.json").read_text("utf-8"))
    assert spec["version"] == protocol.PROTOCOL_VERSION
    assert list(protocol.CLIENT_FRAMES) == spec["client"]
    assert list(protocol.CORE_FRAMES) == spec["core"]


def test_dotenv_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comentario\nCRISVIS_T1=uno\nexport CRISVIS_T2='dos'\nCRISVIS_T3=\"tres\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CRISVIS_T1", "ya")
    monkeypatch.delenv("CRISVIS_T2", raising=False)
    monkeypatch.delenv("CRISVIS_T3", raising=False)
    load_dotenv(env)
    import os

    assert os.environ["CRISVIS_T1"] == "ya"
    assert os.environ["CRISVIS_T2"] == "dos"
    assert os.environ["CRISVIS_T3"] == "tres"
