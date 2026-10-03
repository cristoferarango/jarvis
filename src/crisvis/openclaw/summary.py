"""El resumen hablado del informe. Solo cuenta lo que las fuentes devolvieron."""

from __future__ import annotations

from datetime import datetime

from crisvis.openclaw.contracts import ConnectorMode, ConnectorResult, SourceState

_OK = (SourceState.SUCCESS, SourceState.PARTIAL)
_PLURAL_NONE = {
    "ningún correo prioritario": "correos prioritarios",
    "ninguna reunión pendiente": "reuniones pendientes",
    "ninguna tarea vencida": "tareas vencidas",
    "ningún servicio crítico caído": "servicios críticos caídos",
}
_NUM = {
    1: "uno",
    2: "dos",
    3: "tres",
    4: "cuatro",
    5: "cinco",
    6: "seis",
    7: "siete",
    8: "ocho",
    9: "nueve",
    10: "diez",
    11: "once",
    12: "doce",
}


def _count(n: int, singular: str, plural: str, feminine: bool = False) -> str:
    if n == 1:
        return f"{'una' if feminine else 'un'} {singular}"
    return f"{_NUM.get(n, str(n))} {plural}"


def _hour(when: datetime) -> str:
    h = when.hour % 12 or 12
    article = "a la" if h == 1 else "a las"
    words = _NUM[h]
    tail = {0: "", 15: " y cuarto", 30: " y media", 45: " menos cuarto"}
    if when.minute in tail:
        if when.minute == 45:
            h = (when.hour + 1) % 12 or 12
            article = "a la" if h == 1 else "a las"
            words = _NUM[h]
        return f"{article} {words}{tail[when.minute]}"
    return f"{article} {when.strftime('%H:%M')}"


def _greeting(now: datetime) -> str:
    if now.hour < 12:
        return "Buenos días"
    if now.hour < 20:
        return "Buenas tardes"
    return "Buenas noches"


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " y " + parts[-1]


def voice_summary(results: list[ConnectorResult], now: datetime, user: str) -> str:
    by = {r.source: r for r in results}
    parts: list[str] = []

    mail = by.get("correo")
    if mail and mail.state in _OK:
        n = sum(
            1
            for i in mail.items
            if i.meta.get("prioridad") == "alta" and i.meta.get("leido") is False
        )
        parts.append(
            "ningún correo prioritario"
            if n == 0
            else _count(n, "correo prioritario", "correos prioritarios")
        )

    agenda = by.get("agenda")
    if agenda and agenda.state in _OK:
        upcoming = sorted(
            (i.when for i in agenda.items if i.when is not None and i.when >= now),
        )
        if not upcoming:
            parts.append("ninguna reunión pendiente")
        elif len(upcoming) == 1:
            parts.append(f"una reunión {_hour(upcoming[0].astimezone(now.tzinfo))}")
        else:
            first = _hour(upcoming[0].astimezone(now.tzinfo))
            parts.append(
                f"{_count(len(upcoming), 'reunión', 'reuniones', True)}, la primera {first}"
            )

    tasks = by.get("tareas")
    if tasks and tasks.state in _OK:
        n = sum(1 for i in tasks.items if i.meta.get("vencida") is True)
        parts.append(
            "ninguna tarea vencida"
            if n == 0
            else _count(n, "tarea vencida", "tareas vencidas", True)
        )

    system = [by[s] for s in ("sistema", "docker") if s in by and by[s].state in _OK]
    if system:
        down = sum(
            1
            for r in system
            for i in r.items
            if i.meta.get("estado") == "critical"
            or (i.meta.get("critico") is True and i.meta.get("estado") not in ("ok", "running"))
        )
        parts.append(
            "ningún servicio crítico caído"
            if down == 0
            else _count(down, "servicio crítico con problemas", "servicios críticos con problemas")
        )

    name = f", {user}" if user else ""
    text = f"{_greeting(now)}{name}. Tengo preparado tu informe."
    positives = [p for p in parts if not p.startswith("ningun")]
    negatives = [p for p in parts if p.startswith("ningun")]
    if positives:
        # "Hay tres correos prioritarios … y ningún servicio crítico caído."
        text += f" Hay {_join(positives + negatives)}."
    elif negatives:
        plural = [_PLURAL_NONE.get(p, p) for p in negatives]
        tail = f"{', '.join(plural[:-1])} ni {plural[-1]}" if len(plural) > 1 else plural[0]
        text += f" No hay {tail}."

    failed = [r.name for r in results if r.state not in _OK]
    if failed:
        text += (
            f" No pude consultar {_join(failed)}."
            if len(failed) <= 3
            else f" {_NUM.get(len(failed), str(len(failed))).capitalize()} fuentes no respondieron."
        )
    if any(r.mode is ConnectorMode.MOCK for r in results):
        text += " Ojo: parte del informe son datos de prueba; aún no hay cuentas conectadas."
    text += " El detalle está disponible en pantalla."
    return text
