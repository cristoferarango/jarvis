"""OpenJarvis lee OPENJARVIS_HOME al importarse: se fija antes que nada para que
los tests no toquen la configuración ni la memoria reales del usuario."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_HOME = Path(tempfile.mkdtemp(prefix="crisvis-tests-"))
os.environ["OPENJARVIS_HOME"] = str(_HOME / "openjarvis")
os.environ["CRISVIS_HOME"] = str(_HOME / "crisvis")
# Sin servicio de voz instalado: los tests nunca lanzan XTTS.
os.environ["CRISVIS_VOZ_DIR"] = str(_HOME / "sin-voz")
for var in ("CRISVIS_CONFIG", "CRISVIS_MODELO", "CRISVIS_MOTOR", "CRISVIS_PERMISOS"):
    os.environ.pop(var, None)


@pytest.fixture(autouse=True)
def _no_real_browser_tabs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Ningún test abre pestañas de verdad en el navegador del usuario."""
    opened: list[str] = []

    def fake(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr("crisvis.openclaw.tabs._open", fake)
    monkeypatch.setattr("crisvis.body.desktop.webbrowser.open", fake)
    # Ni se busca ni se toca una ventana real del navegador.
    monkeypatch.setattr("crisvis.body.desktop.Desktop.find_site_window", lambda self, site: None)
    monkeypatch.setattr("crisvis.body.desktop.Desktop.find_site_tab", lambda self, site: None)
    return opened
