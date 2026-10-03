"""Poner música en YouTube, reutilizar la pestaña abierta y LIBRE sin caducidad."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from crisvis.body import desktop, youtube
from crisvis.body.desktop import Desktop, Window
from crisvis.body.pc import pc_tools
from crisvis.security.audit import AuditLog
from crisvis.security.guard import Risk, assess
from crisvis.security.modes import LIBRE_PHRASE, ModeController, ModeError
from crisvis.security.policy import Decision, PermissionPolicy, Tier
from crisvis.settings import PermissionSettings

RESULTS = (
    '{"adSlotRenderer":{"videoId":"AAAAAAAAAAA"}},'
    '{"videoRenderer":{"videoId":"_PJvpq8uOZM","thumbnail":{},'
    '"title":{"runs":[{"text":"BAD BUNNY - MONACO \\u0026 m\\u00e1s"}]}}},'
    '{"videoRenderer":{"videoId":"BBBBBBBBBBB","title":{"runs":[{"text":"otro"}]}}}'
)


def test_first_real_video_is_chosen_and_ads_are_skipped() -> None:
    assert youtube.first_video(RESULTS) == ("_PJvpq8uOZM", "BAD BUNNY - MONACO & más")
    assert youtube.first_video("<html>nada</html>") is None


def test_urls_are_built_here_never_from_model_text() -> None:
    assert youtube.watch_url("_PJvpq8uOZM") == "https://www.youtube.com/watch?v=_PJvpq8uOZM"
    for bad in ("abc", "_PJvpq8uOZM&x=1", "../../../etc", "javascript:x"):
        with pytest.raises(ValueError):
            youtube.watch_url(bad)
    assert youtube.search_url("bad bunny & co") == (
        "https://www.youtube.com/results?search_query=bad+bunny+%26+co"
    )


# -- reutilizar la pestaña ------------------------------------------------------


class FakeDesk(Desktop):
    def __init__(self, window: Window | None, foreground: int | None = None) -> None:
        super().__init__()
        self.window = window
        self.foreground = foreground if foreground is not None else (window.hwnd if window else 0)
        self.steps: list[Any] = []

    def find_site_window(self, site: str) -> Window | None:
        return self.window if self.window and site in self.window.title else None

    def activate(self, window: Window) -> None:
        self.steps.append(("focus", window.hwnd))

    def hotkey(self, keys: list[str], times: int = 1) -> None:
        self.steps.append(("keys", "+".join(keys)))

    def type_text(self, text: str) -> None:
        self.steps.append(("type", text))


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"opened": [], "fg": 0}
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url: state["opened"].append(url))
    monkeypatch.setattr(
        desktop, "_user32", lambda: SimpleNamespace(GetForegroundWindow=lambda: state["fg"])
    )
    return state


YT = Window(7, "Algo - YouTube - Google Chrome", "chrome.exe", False)
WATCH = "https://www.youtube.com/watch?v=_PJvpq8uOZM"


def test_existing_youtube_tab_is_reused(browser: dict[str, Any]) -> None:
    desk = FakeDesk(YT)
    browser["fg"] = 7
    where = desk.open_web(WATCH)
    assert browser["opened"] == []
    assert desk.steps == [
        ("focus", 7), ("keys", "ctrl+l"), ("type", WATCH), ("keys", "delete"), ("keys", "enter")
    ]
    assert "ya estaba abierta" in where


def test_opening_the_site_home_only_brings_the_tab_forward(browser: dict[str, Any]) -> None:
    desk = FakeDesk(YT)
    browser["fg"] = 7
    desk.open_web("https://www.youtube.com/")
    assert desk.steps == [("focus", 7)]
    assert browser["opened"] == []


def test_without_a_tab_of_that_site_one_new_tab_opens(browser: dict[str, Any]) -> None:
    desk = FakeDesk(Window(8, "Recibidos - Gmail - Google Chrome", "chrome.exe", False))
    desk.open_web(WATCH)
    assert browser["opened"] == [WATCH]
    assert desk.steps == []


def test_never_types_if_the_browser_did_not_come_forward(browser: dict[str, Any]) -> None:
    desk = FakeDesk(YT)
    browser["fg"] = 99
    desk.open_web(WATCH)
    assert ("type", WATCH) not in desk.steps
    assert browser["opened"] == [WATCH]


def test_never_types_unsafe_addresses(browser: dict[str, Any]) -> None:
    desk = FakeDesk(YT)
    browser["fg"] = 7
    desk.open_web("http://www.youtube.com/watch?v=x")
    assert not any(step[0] == "type" for step in desk.steps)


def test_pc_youtube_plays_the_first_result(
    browser: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(youtube, "search_video", lambda q: ("_PJvpq8uOZM", "MONACO"))
    desk = FakeDesk(None)
    tool = next(t for t in pc_tools(see=None, desk=desk) if t.spec.name == "pc_youtube")
    result = tool.execute(query="bad bunny monaco")
    assert "Reproduciendo «MONACO»" in str(result.content)
    assert browser["opened"] == [WATCH]


def test_pc_youtube_falls_back_to_the_results_page(
    browser: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(youtube, "search_video", lambda q: None)
    tool = next(t for t in pc_tools(see=None, desk=FakeDesk(None)) if t.spec.name == "pc_youtube")
    tool.execute(query="lofi")
    assert browser["opened"] == ["https://www.youtube.com/results?search_query=lofi"]


def test_pc_youtube_runs_without_asking_only_in_libre() -> None:
    assert assess("pc_youtube", {"query": "x"}, Tier.ESCRITURA).risk is Risk.MEDIUM
    free = PermissionPolicy(PermissionSettings(modo="libre"))
    assert free.evaluate("pc_youtube").decision is Decision.ALLOW
    confirm = PermissionPolicy(PermissionSettings(modo="confirmar"))
    assert confirm.evaluate("pc_youtube").decision is Decision.CONFIRM


# -- LIBRE sin caducidad --------------------------------------------------------


def _modes(tmp_path: Path, clock: list[float]) -> ModeController:
    return ModeController(
        PermissionPolicy(PermissionSettings()),
        tmp_path / "permisos.json",
        AuditLog(tmp_path / "a.jsonl"),
        clock=lambda: clock[0],
    )


def test_libre_without_expiry_lasts_until_disabled(tmp_path: Path) -> None:
    clock = [1000.0]
    modes = _modes(tmp_path, clock)
    assert modes.enable_libre(0, who="cli", how="t", confirmation=LIBRE_PHRASE) is None
    clock[0] += 24 * 3600
    assert modes.mode == "libre"
    assert modes.status()["libreUntil"] is None
    modes.disable_libre(who="cli", how="t")
    assert modes.mode == "confirmar"


def test_libre_without_expiry_still_needs_the_phrase_and_never_persists(tmp_path: Path) -> None:
    clock = [1000.0]
    modes = _modes(tmp_path, clock)
    with pytest.raises(ModeError):
        modes.enable_libre(0, who="cli", how="t", confirmation="")
    modes.enable_libre(0, who="cli", how="t", confirmation=LIBRE_PHRASE)
    assert _modes(tmp_path, clock).mode == "confirmar"
    with pytest.raises(ModeError):
        modes.enable_libre(-5, who="cli", how="t", confirmation=LIBRE_PHRASE)
