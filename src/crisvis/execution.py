"""Cancelar de verdad lo que ya empezó.

Antes, Stop cancelaba la tarea asyncio del turno, pero la herramienta seguía
corriendo en su hilo (y su proceso, y los hijos de su proceso). Aquí:

* ``CancellationToken``: uno por turno; Stop lo dispara.
* ``run_process``: lanza un proceso dentro de un job object de Windows
  (``KILL_ON_JOB_CLOSE``) o de un grupo de procesos en POSIX. Al cancelar o
  vencer el timeout se termina el árbol entero, se espera el cierre y se
  comprueba que no queda ningún hijo vivo.
* ``ExecutionTracker``: qué herramienta corre, si se puede cancelar, y el
  registro de su ciclo de vida en la auditoría (un ``exec`` por ejecución).
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crisvis.body.base import new_id

WINDOWS = os.name == "nt"
_MAX_OUTPUT = 100_000


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            callbacks = list(self._callbacks)
        for fn in callbacks:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def on_cancel(self, fn: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(fn)
                return lambda: self._remove(fn)
        fn()
        return lambda: None

    def _remove(self, fn: Callable[[], None]) -> None:
        with self._lock:
            if fn in self._callbacks:
                self._callbacks.remove(fn)

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


# -- job objects (Windows) -------------------------------------------------------


class _Job:
    """Un job object: todo proceso asignado (y sus hijos) muere con él."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_ulonglong)
                for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
                )
            ]

        class BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimit),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW")
        info = ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            k32.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject")
        self.handle = handle

    def assign(self, proc: subprocess.Popen[Any]) -> bool:
        handle = getattr(proc, "_handle", None)
        if handle is None:
            return False
        return bool(self._k32.AssignProcessToJobObject(self.handle, int(handle)))

    def terminate(self) -> None:
        self._k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


@dataclass
class ProcessOutcome:
    # completed | failed | timed_out | cancelled | cancel_failed | error
    status: str
    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    survivors: list[int] = field(default_factory=list)
    detail: str = ""


def _tree(pid: int) -> list[Any]:
    try:
        import psutil

        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except Exception:  # noqa: BLE001
        return []


def _kill_tree(procs: list[Any], wait_s: float) -> list[int]:
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return []
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    _gone, alive = psutil.wait_procs(procs, timeout=wait_s)
    return [p.pid for p in alive if p.is_running()]


def run_process(
    command: str | list[str],
    *,
    shell: bool,
    timeout: float,
    token: CancellationToken | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    kill_wait_s: float = 5.0,
) -> ProcessOutcome:
    started = time.monotonic()
    flags = 0
    kwargs: dict[str, Any] = {}
    if WINDOWS:
        flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    job: _Job | None = None
    if WINDOWS:
        try:
            job = _Job()
        except OSError:
            job = None
    try:
        proc = subprocess.Popen(
            command,
            shell=shell,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            creationflags=flags,
            **kwargs,
        )
    except OSError as exc:
        if job:
            job.close()
        return ProcessOutcome("error", None, "", "", time.monotonic() - started, detail=str(exc))
    if job is not None:
        job.assign(proc)

    out: list[bytes] = []
    err: list[bytes] = []

    def drain(stream: Any, sink: list[bytes]) -> None:
        size = 0
        for chunk in iter(lambda: stream.read(4096), b""):
            if size < _MAX_OUTPUT:
                sink.append(chunk)
                size += len(chunk)
        stream.close()

    readers = [
        threading.Thread(target=drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, err), daemon=True),
    ]
    for r in readers:
        r.start()

    deadline = started + max(1.0, timeout)
    reason = ""
    while True:
        try:
            proc.wait(timeout=0.1)
            break
        except subprocess.TimeoutExpired:
            pass
        if token is not None and token.cancelled:
            reason = "cancelled"
            break
        if time.monotonic() >= deadline:
            reason = "timed_out"
            break

    survivors: list[int] = []
    if reason:
        tree = _tree(proc.pid)
        if job is not None:
            job.terminate()
        survivors = _kill_tree(tree, kill_wait_s)
        try:
            proc.wait(timeout=kill_wait_s)
        except subprocess.TimeoutExpired:
            survivors.append(proc.pid)
    if job is not None:
        job.close()
    for r in readers:
        r.join(timeout=2)

    def text(chunks: list[bytes]) -> str:
        return b"".join(chunks).decode("utf-8", "replace")[:_MAX_OUTPUT]

    duration = time.monotonic() - started
    if reason == "cancelled":
        status = "cancel_failed" if survivors else "cancelled"
    elif reason == "timed_out":
        status = "cancel_failed" if survivors else "timed_out"
    else:
        status = "completed" if proc.returncode == 0 else "failed"
    return ProcessOutcome(
        status, proc.returncode, text(out), text(err), duration, sorted(set(survivors)), reason
    )


# -- seguimiento de ejecuciones ---------------------------------------------------


@dataclass
class Execution:
    id: str
    tool: str
    session: str
    turn: int
    risk: str
    cancellable: bool
    started: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    status: str = "running"
    cancel_requested: bool = False


class ExecutionTracker:
    """Hooks que el agente llama alrededor de cada ejecución (desde el hilo)."""

    def __init__(self, audit: Any, session: str) -> None:
        self.audit = audit
        self.session = session
        self.turn = 0
        self.token = CancellationToken()
        self._running: dict[str, Execution] = {}
        self._lock = threading.Lock()
        # Lo que dejó preparado el gate para la siguiente ejecución.
        self.prepared: dict[str, Any] | None = None

    def new_turn(self) -> CancellationToken:
        self.turn += 1
        self.token = CancellationToken()
        self.prepared = None
        return self.token

    def running(self) -> list[Execution]:
        with self._lock:
            return list(self._running.values())

    def begin(self, tool: str, args: dict[str, Any]) -> Execution:
        prepared = self.prepared if self.prepared and self.prepared.get("tool") == tool else {}
        self.prepared = None
        ex = Execution(
            id=prepared.get("exec") or new_id("x"),
            tool=tool,
            session=self.session,
            turn=self.turn,
            risk=prepared.get("risk", "low"),
            cancellable=bool(prepared.get("cancellable", False)),
        )
        with self._lock:
            self._running[ex.id] = ex
        if ex.risk != "low" or ex.cancellable:
            self.audit.event(
                "iniciada", exec=ex.id, sesion=self.session[:8], turno=self.turn, herramienta=tool,
                riesgo=ex.risk, cancelable=ex.cancellable,
            )
        return ex

    def end(self, ex: Execution, result: Any, error: BaseException | None) -> None:
        meta = getattr(result, "metadata", None) or {}
        content = str(getattr(result, "content", "") or "")
        if error is not None:
            status = "fallida"
        elif meta.get("status") in ("cancelled", "timed_out", "cancel_failed"):
            status = meta["status"]
        elif content.startswith(f"Tool '{ex.tool}' timed out"):
            status = "timed_out"
        elif getattr(result, "success", False):
            status = "completada"
        else:
            status = "fallida"
        if ex.cancel_requested and status == "completada":
            status = "completada_tras_cancelar"
        ex.status = status
        with self._lock:
            self._running.pop(ex.id, None)
        # Primero la auditoría: quien espera a `done` (Stop) no debe adelantarse a ella.
        if ex.risk != "low" or ex.cancellable or status != "completada":
            self.audit.event(
                status, exec=ex.id, sesion=self.session[:8], turno=ex.turn, herramienta=ex.tool,
                duracion_ms=round((time.monotonic() - ex.started) * 1000),
            )
        ex.done.set()
