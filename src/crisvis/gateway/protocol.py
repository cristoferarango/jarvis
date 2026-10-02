"""Tramas del WebSocket /ws. Fuente única: packages/protocol/frames.json."""

from __future__ import annotations

PROTOCOL_VERSION = 1

CLIENT_FRAMES = ("hello", "ask", "interrupt", "reply", "permissions")
CORE_FRAMES = (
    "ready",
    "status",
    "text",
    "tool",
    "done",
    "error",
    "panel",
    "blade",
    "ui",
    "capture",
    "confirm",
)
