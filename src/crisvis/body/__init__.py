"""El cuerpo: herramientas de OpenJarvis que actúan sobre la cara y sobre el equipo."""

from __future__ import annotations

from crisvis.body.base import FaceTool
from crisvis.body.display import display_tools
from crisvis.body.interface import interface_tools
from crisvis.body.surface import Surface
from crisvis.body.vision import SeeFn, vision_tools

__all__ = ["FaceTool", "SeeFn", "Surface", "build_body_tools"]


def build_body_tools(
    surface: Surface,
    *,
    interface: bool,
    camera: bool,
    see: SeeFn | None,
    pc: bool = False,
) -> list[FaceTool]:
    tools: list[FaceTool] = []
    if interface:
        tools += display_tools(surface)
        tools += interface_tools(surface)
    if camera:
        tools += vision_tools(surface, see)
    if pc:
        from crisvis.body.pc import pc_tools

        tools += pc_tools(see)
    return tools
