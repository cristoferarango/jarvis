"""OpenJarvis lee OPENJARVIS_HOME al importarse: se fija antes que nada para que
los tests no toquen la configuración ni la memoria reales del usuario."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_HOME = Path(tempfile.mkdtemp(prefix="crisvis-tests-"))
os.environ["OPENJARVIS_HOME"] = str(_HOME / "openjarvis")
os.environ["CRISVIS_HOME"] = str(_HOME / "crisvis")
# Sin servicio de voz instalado: los tests nunca lanzan XTTS.
os.environ["CRISVIS_VOZ_DIR"] = str(_HOME / "sin-voz")
for var in ("CRISVIS_CONFIG", "CRISVIS_MODELO", "CRISVIS_MOTOR", "CRISVIS_PERMISOS"):
    os.environ.pop(var, None)
