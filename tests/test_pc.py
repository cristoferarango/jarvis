"""Control del PC: lo que se puede probar sin tocar el escritorio de verdad."""

from __future__ import annotations

from pathlib import Path

from crisvis.body.desktop import best_match, looks_like_url, normalize, parse_keys, resolve_path
from crisvis.body.pc import pc_tools
from crisvis.gateway.session import summarise
from crisvis.security import PermissionPolicy
from crisvis.security.policy import Decision, Tier, classify
from crisvis.settings import PermissionSettings


def test_keys_are_understood_in_spanish() -> None:
    assert parse_keys("Ctrl + Mayús + Esc") == ["ctrl", "shift", "escape"]
    assert parse_keys("alt+f4") == ["alt", "f4"]
    assert parse_keys("Windows + D") == ["win", "d"]
    assert parse_keys("intro") == ["enter"]
    assert parse_keys("Av Pág") == ["pagedown"]


def test_names_match_without_accents_or_case() -> None:
    names = ["Bloc de notas", "Calculadora", "Google Chrome", "Configuración"]
    assert best_match("bloc de notas", names) == 0
    assert best_match("chrome", names) == 2
    assert best_match("configuracion", names) == 3
    assert best_match("calculadra", names) == 1
    assert best_match("photoshop", names) is None
    assert normalize("  Música  ") == "musica"


def test_spoken_folders_resolve_to_the_user_folders() -> None:
    assert resolve_path("~") == Path.home()
    assert resolve_path("descargas").name in ("Downloads", "Descargas")
    assert resolve_path("Escritorio").name in ("Desktop", "Escritorio")


def test_urls_are_told_apart_from_apps_and_paths() -> None:
    assert looks_like_url("youtube.com")
    assert looks_like_url("https://example.org/a?b=1")
    assert looks_like_url("mailto:ana@example.com")
    assert not looks_like_url("spotify")
    assert not looks_like_url("C:/Users/yo/notas.txt")


def test_every_pc_tool_has_a_deliberate_tier() -> None:
    tools = pc_tools(see=None)
    names = {t.spec.name for t in tools}
    assert len(names) == len(tools) == 15
    tiers = {name: classify(name) for name in names}
    assert tiers["pc_inspect"] is Tier.LECTURA
    assert tiers["pc_screen"] is Tier.LECTURA
    assert tiers["pc_media"] is Tier.INTERFAZ
    assert tiers["pc_click"] is Tier.ESCRITURA
    assert tiers["pc_type"] is Tier.ESCRITURA
    assert tiers["pc_youtube"] is Tier.ESCRITURA
    assert tiers["pc_kill"] is Tier.PELIGROSO
    assert tiers["pc_power"] is Tier.PELIGROSO


def test_modes_gate_the_pc() -> None:
    read_only = PermissionPolicy(PermissionSettings(modo="lectura"))
    assert read_only.evaluate("pc_inspect").decision is Decision.ALLOW
    assert read_only.evaluate("pc_click").decision is Decision.DENY
    assert not read_only.visible("pc_power")
    free = PermissionPolicy(PermissionSettings(modo="libre"))
    assert free.evaluate("pc_type").decision is Decision.ALLOW
    assert free.evaluate("pc_power").decision is Decision.CONFIRM


def test_points_survive_sloppy_json() -> None:
    from crisvis.body.pc import parse_point

    assert parse_point('{"x": 334, "y": 966}') == (334, 966)
    assert parse_point('Aquí: {"x": 505, 932}') == (505, 932)
    assert parse_point('{"x": null, "y": null}') is None
    assert parse_point('{"x": 1500, "y": 20}') is None
    assert parse_point("no lo veo") is None


def test_screen_without_vision_model_says_so() -> None:
    screen = next(t for t in pc_tools(see=None) if t.spec.name == "pc_screen")
    result = screen.execute(question="¿qué hay?")
    assert not result.success
    assert "visión" in result.content


def test_confirmations_read_as_plain_spanish() -> None:
    assert summarise("pc_open", {"target": "Spotify"}).startswith("Controlar el PC: abrir Spotify")
    assert summarise("pc_power", {"action": "shutdown"}) == "Apagar el equipo"
    assert summarise("pc_kill", {"process": "chrome"}) == "Forzar el cierre de chrome"


async def test_each_pc_step_needs_its_own_yes(tmp_path: Path) -> None:
    # R-16: antes un "sí" cubría el resto de la cadena. Ahora cada acción que
    # cambia algo pide su propia aprobación.
    from types import SimpleNamespace

    from gate_helpers import GateSession

    session = GateSession(tmp_path, mode="confirmar")
    spec = SimpleNamespace(requires_confirmation=False, timeout_seconds=30)
    assert await session.gate("pc_open", {"target": "bloc de notas"}, spec) is None
    assert await session.gate("pc_click", {"element": 3}, spec) is None
    assert await session.gate("pc_type", {"text": "hola"}, spec) is None
    assert await session.gate("pc_power", {"action": "shutdown"}, spec) is None
    assert [p["tool"] for p in session.asked] == ["pc_open", "pc_click", "pc_type", "pc_power"]
    assert len({p["grant"] for p in session.asked}) == 4
