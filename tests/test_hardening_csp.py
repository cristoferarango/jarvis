"""R-30: CSP de producción sin 'unsafe-eval' ni scripts en línea, y cabeceras de seguridad."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app_helpers import make_app
from crisvis.security.headers import build_csp

ROOT = Path(__file__).resolve().parents[1]


def directives(csp: str) -> dict[str, str]:
    parts = (x.strip() for x in csp.split(";"))
    return {p.split(" ", 1)[0]: p.split(" ", 1)[1] for p in parts if p}


def test_page_csp_has_a_nonce_and_no_unsafe_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    res = client.get("/")
    csp = directives(res.headers["content-security-policy"])
    script = csp["script-src"]
    assert "'unsafe-eval'" not in script.replace("'wasm-unsafe-eval'", "")
    assert "'unsafe-inline'" not in script
    nonce = re.search(r"'nonce-([^']+)'", script).group(1)
    assert f'<script nonce="{nonce}"' in res.text
    assert csp["default-src"] == "'self'"
    assert csp["object-src"] == "'none'"
    assert csp["base-uri"] == "'self'"
    assert csp["frame-ancestors"] == "'none'"
    assert csp["form-action"] == "'self'"
    assert csp["script-src-attr"] == "'none'"
    assert "https:" not in csp["connect-src"].split()
    assert "https:" not in csp["img-src"].split()


def test_each_page_load_gets_a_fresh_nonce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    first = client.get("/").headers["content-security-policy"]
    second = client.get("/").headers["content-security-policy"]
    assert first != second


def test_every_response_has_security_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _app, client = make_app(tmp_path, monkeypatch)
    for path in ("/", "/health", "/assets/index.js"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        assert "content-security-policy" in headers
        assert "'unsafe-eval'" not in headers["content-security-policy"].replace(
            "'wasm-unsafe-eval'", ""
        )


def test_kokoro_exception_is_opt_in() -> None:
    assert "'unsafe-eval'" not in build_csp("n", port=8787).replace("'wasm-unsafe-eval'", "")
    assert "'unsafe-eval'" in build_csp("n", port=8787, kokoro=True)


def test_source_index_has_no_meta_csp_or_inline_scripts() -> None:
    html = (ROOT / "apps" / "face" / "index.html").read_text("utf-8")
    assert "Content-Security-Policy" not in html
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, f"script en línea en index.html: {tag}"


def test_built_index_if_present_is_clean() -> None:
    built = ROOT / "src" / "crisvis" / "static" / "index.html"
    if not built.exists():
        pytest.skip("la interfaz no está compilada")
    html = built.read_text("utf-8")
    assert "Content-Security-Policy" not in html
    assert "unsafe-eval" not in html
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, f"script en línea en el build: {tag}"
