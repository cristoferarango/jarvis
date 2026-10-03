"""``shell_exec`` cancelable: sustituye al de OpenJarvis dentro de las sesiones.

Mismo contrato (command, timeout, working_dir), pero el proceso corre en un
job object y muere entero (con sus hijos) al pulsar Stop o al vencer el
timeout. No acepta ``env_passthrough``: el entorno es siempre el mínimo.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjarvis.core.types import ToolResult

from crisvis.body.base import FaceTool, schema
from crisvis.execution import CancellationToken, run_process

MAX_TIMEOUT = 300
DEFAULT_TIMEOUT = 30
KILL_WAIT = 5.0

_ENV_KEYS = ("PATH", "HOME", "USER", "LANG", "TERM")
_WIN_ENV_KEYS = (
    "SystemRoot", "SystemDrive", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
    "LOCALAPPDATA", "APPDATA",
)


def _env() -> dict[str, str]:
    keys = _ENV_KEYS + (_WIN_ENV_KEYS if os.name == "nt" else ())
    return {k: os.environ[k] for k in keys if k in os.environ}


def shell_tool(token: Callable[[], CancellationToken | None]) -> FaceTool:
    def run(args: dict[str, Any]) -> ToolResult:
        command = str(args.get("command") or "").strip()
        if not command:
            return ToolResult("shell_exec", "No command provided.", success=False)
        try:
            timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        timeout = min(MAX_TIMEOUT, max(1, timeout))
        cwd = args.get("working_dir")
        if cwd is not None:
            path = Path(str(cwd))
            if not path.is_dir():
                return ToolResult(
                    "shell_exec", f"Working directory not found: {cwd}", success=False
                )
        outcome = run_process(
            command, shell=True, timeout=timeout, token=token(), cwd=cwd, env=_env(),
            kill_wait_s=KILL_WAIT,
        )
        meta = {"status": outcome.status, "returncode": outcome.returncode}
        if outcome.status == "cancelled":
            return ToolResult("shell_exec", "Cancelado por el usuario; el proceso y sus hijos "
                              "se han terminado.", success=False, metadata=meta)
        if outcome.status == "timed_out":
            return ToolResult("shell_exec", f"Command timed out after {timeout} seconds; "
                              "process tree terminated.", success=False, metadata=meta)
        if outcome.status == "cancel_failed":
            meta["survivors"] = outcome.survivors
            return ToolResult(
                "shell_exec",
                "No se pudo terminar todo el árbol de procesos "
                f"(siguen vivos: {outcome.survivors}). Requiere intervención.",
                success=False,
                metadata=meta,
            )
        if outcome.status == "error":
            return ToolResult("shell_exec", f"OS error: {outcome.detail}", success=False,
                              metadata=meta)
        sections = []
        if outcome.stdout:
            sections.append(f"=== STDOUT ===\n{outcome.stdout}")
        if outcome.stderr:
            sections.append(f"=== STDERR ===\n{outcome.stderr}")
        return ToolResult(
            "shell_exec",
            "\n".join(sections) or "(no output)",
            success=outcome.status == "completed",
            metadata=meta,
        )

    return FaceTool(
        "shell_exec",
        "Execute a shell command and return its stdout/stderr. Runs with a minimal environment; "
        "the user can stop it at any time.",
        schema(
            {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {"type": "integer", "description": "Seconds (default 30, max 300)."},
                "working_dir": {"type": "string", "description": "Existing directory."},
            },
            ["command"],
        ),
        run,
        category="system",
        timeout=MAX_TIMEOUT + KILL_WAIT + 10,
        requires_confirmation=True,
        required_capabilities=["code:execute"],
    )
