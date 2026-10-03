"""El cuerpo: herramientas de OpenJarvis que actúan sobre la cara y sobre el equipo."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
    clipboard: bool = False,
    apps: tuple[str, ...] = (),
    shell_token: Callable[[], Any] | None = None,
) -> list[FaceTool]:
    tools: list[FaceTool] = []
    if interface:
        tools += display_tools(surface)
        tools += interface_tools(surface)
    if camera:
        tools += vision_tools(surface, see)
    if pc:
        from crisvis.body.pc import clipboard_tool, pc_tools

        tools += pc_tools(see, apps=apps)
        if clipboard:
            tools.append(clipboard_tool())
    if shell_token is not None:
        from crisvis.body.shell import shell_tool

        tools.append(shell_tool(shell_token))
    return tools
