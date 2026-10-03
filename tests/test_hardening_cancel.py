"""R-28: Stop cancela de verdad (proceso y descendientes) y la auditoría lo cuenta."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest
from openjarvis.core.types import ToolResult

import crisvis.gateway.session as session_mod
from crisvis.body.shell import shell_tool
from crisvis.execution import CancellationToken, ExecutionTracker, run_process
from crisvis.security.audit import AuditLog
from gate_helpers import SPEC, GateSession

PARENT = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open(sys.argv[1], "w").write(str(child.pid))
time.sleep(120)
"""


def tree_script(tmp: Path) -> tuple[list[str], Path]:
    script = tmp / "padre.py"
    script.write_text(PARENT, encoding="utf-8")
    pid_file = tmp / "hijo.pid"
    return [sys.executable, str(script), str(pid_file)], pid_file


def wait_for(path: Path, seconds: float = 15) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.05)
    raise AssertionError("el proceso hijo no arrancó")


def gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_stop_kills_the_process_and_its_children(tmp_path: Path) -> None:
    command, pid_file = tree_script(tmp_path)
    token = CancellationToken()
    pids: list[int] = []

    def stop_when_child_runs() -> None:
        pids.append(wait_for(pid_file))
        token.cancel()

    threading.Thread(target=stop_when_child_runs, daemon=True).start()
    outcome = run_process(command, shell=False, timeout=60, token=token)
    assert outcome.status == "cancelled"
    assert outcome.survivors == []
    assert outcome.duration_s < 30
    assert pids and gone(pids[0])


def test_timeout_kills_the_tree_and_is_reported_as_timed_out(tmp_path: Path) -> None:
    command, pid_file = tree_script(tmp_path)
    outcome = run_process(command, shell=False, timeout=3)
    assert outcome.status == "timed_out"
    assert gone(wait_for(pid_file, 1))


def test_completed_process_reports_output() -> None:
    outcome = run_process([sys.executable, "-c", "print('hola')"], shell=False, timeout=30)
    assert outcome.status == "completed"
    assert "hola" in outcome.stdout


def test_shell_exec_reports_cancelled_never_completed(tmp_path: Path) -> None:
    command, pid_file = tree_script(tmp_path)
    token = CancellationToken()
    threading.Thread(target=lambda: (wait_for(pid_file), token.cancel()), daemon=True).start()
    quoted = " ".join(f'"{part}"' for part in command)
    result = shell_tool(lambda: token).execute(command=quoted, timeout=60)
    assert not result.success
    assert result.metadata["status"] == "cancelled"
    assert "Cancelado" in result.content


def events(path: Path) -> list[dict]:
    import json

    return [json.loads(x) for x in path.read_text("utf-8").splitlines()]


def test_tracker_audits_the_whole_cycle_with_one_correlation_id(tmp_path: Path) -> None:
    path = tmp_path / "auditoria.jsonl"
    tracker = ExecutionTracker(AuditLog(path), "sesion-xyz-123")
    tracker.new_turn()
    tracker.prepared = {"tool": "shell_exec", "exec": "x-1", "risk": "high", "cancellable": True}
    ex = tracker.begin("shell_exec", {"command": "dir"})
    ex.cancel_requested = True
    tracker.end(ex, ToolResult("shell_exec", "Cancelado", success=False,
                               metadata={"status": "cancelled"}), None)
    log = events(path)
    assert [e["evento"] for e in log] == ["iniciada", "cancelled"]
    assert {e["exec"] for e in log} == {"x-1"}
    assert "duracion_ms" in log[-1]


def test_tracker_never_says_completed_for_timeouts_or_late_cancels(tmp_path: Path) -> None:
    path = tmp_path / "auditoria.jsonl"
    tracker = ExecutionTracker(AuditLog(path), "sesion-xyz-123")
    ex = tracker.begin("pc_click", {})
    tracker.end(ex, ToolResult("pc_click", "Tool 'pc_click' timed out after 30s.",
                               success=False), None)
    assert ex.status == "timed_out"
    late = tracker.begin("pc_click", {})
    late.cancel_requested = True
    tracker.end(late, ToolResult("pc_click", "ok"), None)
    assert late.status == "completada_tras_cancelar"
    assert [e["evento"] for e in events(path)] == ["timed_out", "completada_tras_cancelar"]


def stoppable(tmp: Path) -> GateSession:
    session = GateSession(tmp)
    session._turn = None
    session._pending = {}
    session.sent = []
    session.send = session.sent.append  # type: ignore[method-assign]
    return session


async def test_stop_on_a_non_cancellable_action_says_it_could_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_mod, "CANCEL_WAIT_SECONDS", 0.3)
    session = stoppable(tmp_path)
    session.tracker.prepared = {"tool": "pc_type", "exec": "x-2", "risk": "medium",
                                "cancellable": False}
    session.tracker.begin("pc_type", {"text": "hola"})
    await session.stop_turn()
    phases = [(f["phase"], f["cancellable"]) for f in session.sent]
    assert phases == [("requested", False), ("failed", False)]
    assert "No se pudo cancelar" in session.sent[-1]["message"]
    kinds = [e.get("evento") for e in session.events()]
    assert "cancelacion_solicitada" in kinds and "cancel_failed" in kinds


async def test_stop_cancels_a_running_shell_command(tmp_path: Path) -> None:
    session = stoppable(tmp_path)
    command, pid_file = tree_script(tmp_path)
    tracker = session.tracker
    tracker.prepared = {"tool": "shell_exec", "exec": "x-3", "risk": "high", "cancellable": True}
    ex = tracker.begin("shell_exec", {})
    tool = shell_tool(lambda: tracker.token)
    quoted = " ".join(f'"{part}"' for part in command)

    def run() -> None:
        tracker.end(ex, tool.execute(command=quoted, timeout=60), None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    child = await asyncio.to_thread(wait_for, pid_file)
    await session.stop_turn()
    assert [f["phase"] for f in session.sent] == ["requested", "cancelled"]
    assert ex.status == "cancelled"
    assert gone(child)
    assert [e["evento"] for e in session.events() if e.get("exec") == "x-3"] == [
        "iniciada", "cancelacion_solicitada", "cancelled",
    ]


async def test_confirmation_warns_when_an_action_cannot_be_cancelled(tmp_path: Path) -> None:
    session = GateSession(tmp_path)
    assert await session.gate("pc_type", {"text": "hola"}, SPEC) is None
    shell_spec = SimpleNamespace(requires_confirmation=True, timeout_seconds=315)
    assert await session.gate("shell_exec", {"command": "dir"}, shell_spec) is None
    by_tool = {p["tool"]: p for p in session.asked}
    assert by_tool["pc_type"]["cancellable"] is False
    assert by_tool["shell_exec"]["cancellable"] is True
    assert by_tool["shell_exec"]["risk"] == "high"
