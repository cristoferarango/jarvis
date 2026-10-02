"""Crisvis: asistente personal local.

La cara es la interfaz holográfica de Jarvis (apps/face); el cerebro es
OpenJarvis, embebido como biblioteca (crisvis.brain). Este paquete es el núcleo
que los une en un único proceso.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("crisvis")
except PackageNotFoundError:  # pragma: no cover - ejecutando desde el árbol sin instalar
    __version__ = "0.0.0"
