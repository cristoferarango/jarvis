"""El modo de permisos, gobernado solo por el núcleo.

* CONFIRMAR es el modo por defecto y, como LECTURA, persiste entre reinicios
  (``<home>/permisos.json``).
* LIBRE nunca persiste. Solo lo habilita una acción de administración (la CLI
  ``crisvis permisos libre``, con el token de ``<home>/admin.token``), con una
  frase de confirmación, auditoría y caducidad corta (o ninguna, con 0
  minutos, si el usuario lo decide). Al caducar, al desactivarlo o al
  reiniciar, vuelve a CONFIRMAR.
* La interfaz puede bajar privilegios (LECTURA, CONFIRMAR) con su token de
  sesión, pero nunca subir a LIBRE. El WebSocket no puede cambiar nada.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from crisvis.security.audit import AuditLog
from crisvis.security.policy import PermissionPolicy
from crisvis.settings import PERSISTENT_MODES

log = logging.getLogger(__name__)

LIBRE_PHRASE = "ACTIVAR LIBRE"


class ModeError(PermissionError):
    pass


class ModeController:
    def __init__(
        self,
        policy: PermissionPolicy,
        state_path: Path,
        audit: AuditLog,
        *,
        max_libre_minutes: int = 30,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.policy = policy
        self.state_path = state_path
        self.audit = audit
        self.max_libre_minutes = max(1, int(max_libre_minutes))
        self._clock = clock
        self._lock = threading.RLock()
        self._libre_until: float | None = None
        self._listeners: list[Callable[[str, str], None]] = []
        self._startup()

    # -- arranque ------------------------------------------------------------

    def _startup(self) -> None:
        mode = self.policy.mode if self.policy.mode in PERSISTENT_MODES else "confirmar"
        saved = self._read_state()
        if saved in PERSISTENT_MODES:
            mode = saved
        self.policy.set_mode(mode)
        self.audit.event("modo_arranque", modo=mode, origen="permisos.json" if saved else "config")

    def _read_state(self) -> str | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        mode = data.get("modo") if isinstance(data, dict) else None
        return mode if mode in PERSISTENT_MODES else None

    def _write_state(self, mode: str) -> None:
        if mode not in PERSISTENT_MODES:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"modo": mode, "actualizado": round(self._clock(), 3)}), encoding="utf-8"
        )
        tmp.replace(self.state_path)

    # -- consulta ------------------------------------------------------------

    def on_change(self, fn: Callable[[str, str], None]) -> None:
        self._listeners.append(fn)

    @property
    def mode(self) -> str:
        """El modo vigente. Comprueba la caducidad de LIBRE en cada lectura."""
        self.tick()
        return self.policy.mode

    def tick(self) -> None:
        with self._lock:
            if (
                self.policy.mode == "libre"
                and self._libre_until is not None
                and self._clock() >= self._libre_until
            ):
                self._apply("confirmar", who="sistema", how="caducidad", event="libre_caducado")

    def status(self) -> dict[str, Any]:
        mode = self.mode
        until = self._libre_until if mode == "libre" else None
        return {
            "mode": mode,
            "libreUntil": round(until, 3) if until else None,
            "libreAvailable": False,
        }

    # -- cambios -------------------------------------------------------------

    def set_user_mode(self, mode: str, *, who: str, how: str) -> None:
        """Desde la interfaz: solo LECTURA o CONFIRMAR."""
        if mode not in PERSISTENT_MODES:
            self.audit.event("modo_rechazado", modo=mode, quien=who, como=how)
            raise ModeError("LIBRE solo se habilita con una acción de administración.")
        with self._lock:
            self._write_state(mode)
            self._apply(mode, who=who, how=how, event="modo_cambiado")

    def enable_libre(
        self, minutes: float, *, who: str, how: str, confirmation: str
    ) -> float | None:
        """``minutes = 0``: sin caducidad, hasta ``crisvis permisos confirmar`` o reiniciar."""
        if confirmation != LIBRE_PHRASE:
            self.audit.event("libre_rechazado", quien=who, como=how, motivo="sin confirmación")
            raise ModeError(f"Falta la confirmación «{LIBRE_PHRASE}».")
        try:
            minutes = float(minutes)
        except (TypeError, ValueError) as exc:
            raise ModeError("Minutos inválidos.") from exc
        if minutes != 0 and not 0 < minutes <= self.max_libre_minutes:
            raise ModeError(
                f"LIBRE dura entre 1 y {self.max_libre_minutes} minutos, o 0 (sin caducidad)."
            )
        with self._lock:
            self._libre_until = self._clock() + minutes * 60 if minutes else None
            self._apply(
                "libre",
                who=who,
                how=how,
                event="libre_habilitado",
                hasta=round(self._libre_until, 3) if self._libre_until else None,
                minutos=minutes or "sin caducidad",
            )
            return self._libre_until

    def disable_libre(self, *, who: str, how: str) -> None:
        with self._lock:
            if self.policy.mode == "libre":
                self._apply("confirmar", who=who, how=how, event="libre_deshabilitado")

    def _apply(self, mode: str, *, who: str, how: str, event: str, **extra: Any) -> None:
        before = self.policy.mode
        self.policy.set_mode(mode)
        if mode != "libre":
            self._libre_until = None
        self.audit.event(event, antes=before, modo=mode, quien=who, como=how, **extra)
        log.warning("Modo de permisos: %s -> %s (%s, %s)", before, mode, who, how)
        if before != mode:
            for fn in list(self._listeners):
                try:
                    fn(before, mode)
                except Exception:  # noqa: BLE001
                    log.exception("Fallo avisando del cambio de modo")
