"""Abrir apps por como las dice el usuario, con faltas o rutas inventadas."""

from __future__ import annotations

from typing import Any

import pytest

from crisvis.body import desktop
from crisvis.body.desktop import DesktopError, best_match
from crisvis.security.guard import app_name_allowed

START_MENU = [
    ("Adobe After Effects (Beta)", "{X}\\Adobe\\Adobe After Effects (Beta)\\AfterFX (Beta).exe"),
    ("Adobe After Effects 2026", "{X}\\Adobe\\Adobe After Effects 2026\\AfterFX.exe"),
    ("Adobe Premiere Pro (Beta)", "{X}\\Adobe\\Adobe Premiere Pro (Beta)\\Premiere (Beta).exe"),
    ("Adobe Premiere Pro 2026", "{X}\\Adobe\\Adobe Premiere Pro 2026\\Adobe Premiere Pro.exe"),
    ("Uninstall Twixtor v7 for After Effects and Premiere Pro", "{X}\\Twixtor7AE_uninstall.exe"),
    ("Bloc de notas", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    ("Blender 5.2", "{X}\\Blender Foundation\\Blender 5.2\\blender-launcher.exe"),
    ("Google Chrome", "Chrome"),
    ("Windows PowerShell", "{X}\\WindowsPowerShell\\v1.0\\powershell.exe"),
]
NAMES = [n for n, _ in START_MENU]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Premier Beta", "Adobe Premiere Pro (Beta)"),
        ("premiere pro beta", "Adobe Premiere Pro (Beta)"),
        ("premier", "Adobe Premiere Pro 2026"),
        ("after efcet", "Adobe After Effects 2026"),
        ("affter efect", "Adobe After Effects 2026"),
        ("after effects beta", "Adobe After Effects (Beta)"),
        ("el programa blender", "Blender 5.2"),
    ],
)
def test_misspelt_or_partial_names_find_the_app(query: str, expected: str) -> None:
    index = best_match(query, NAMES, cutoff=0.7)
    assert index is not None and NAMES[index] == expected


@pytest.mark.parametrize("query", ["spotify", "word", "xyz programa raro", "photoshop"])
def test_apps_that_are_not_installed_find_nothing(query: str) -> None:
    assert best_match(query, NAMES, cutoff=0.7) is None


def test_uninstallers_are_only_picked_when_asked_for() -> None:
    assert best_match("twixtor", NAMES, cutoff=0.7) is None
    index = best_match("desinstalar twixtor", NAMES, cutoff=0.7)
    assert index is not None and NAMES[index].startswith("Uninstall Twixtor")


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda argv, **_: calls.append(argv))

    def no_startfile(target: str) -> None:
        raise AssertionError(f"startfile({target!r})")

    monkeypatch.setattr(desktop, "_startfile", no_startfile)
    return calls


def _desk() -> desktop.Desktop:
    d = desktop.Desktop()
    d._apps = list(START_MENU)
    return d


def test_open_launches_the_start_menu_app_for_a_spoken_name(launched: list[Any]) -> None:
    what = _desk().open("Premier Beta", check=app_name_allowed)
    assert what == "la aplicación Adobe Premiere Pro (Beta)"
    assert launched == [["explorer.exe", f"shell:AppsFolder\\{START_MENU[2][1]}"]]


def test_an_invented_path_falls_back_to_the_app_by_its_last_name(
    launched: list[Any], tmp_path: Any
) -> None:
    fake = tmp_path / "VIDEOS" / "AFFTER EFCET"
    what = _desk().open(str(fake), check=app_name_allowed)
    assert what == "la aplicación Adobe After Effects 2026"
    assert len(launched) == 1


def test_a_missing_path_with_no_app_explains_what_to_do(launched: list[Any], tmp_path: Any) -> None:
    with pytest.raises(DesktopError, match="pc_find_files"):
        _desk().open(str(tmp_path / "no-existe" / "informe raro.docx"), check=app_name_allowed)
    assert launched == []


def test_the_path_fallback_still_refuses_consoles(launched: list[Any], tmp_path: Any) -> None:
    with pytest.raises(DesktopError, match="Bloqueado por política"):
        _desk().open(str(tmp_path / "Windows PowerShell"), check=app_name_allowed)
    assert launched == []
