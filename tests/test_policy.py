import pytest

from crisvis.security import Decision, PermissionPolicy, Tier, classify
from crisvis.settings import PermissionSettings


def policy(**kw) -> PermissionPolicy:
    return PermissionPolicy(PermissionSettings(**kw))


@pytest.mark.parametrize(
    ("tool", "tier"),
    [
        ("display", Tier.INTERFAZ),
        ("web_search", Tier.LECTURA),
        ("memory_store", Tier.LECTURA),
        ("file_write", Tier.ESCRITURA),
        ("shell_exec", Tier.PELIGROSO),
        ("mcp__github__list_issues", Tier.LECTURA),
        ("mcp__gmail__send_email", Tier.ESCRITURA),
        ("mcp__box__run_command", Tier.PELIGROSO),
        ("mcp__raro__frobnicate", Tier.ESCRITURA),
    ],
)
def test_classify(tool: str, tier: Tier) -> None:
    assert classify(tool) is tier


@pytest.mark.parametrize(
    ("mode", "tool", "decision"),
    [
        ("lectura", "web_search", Decision.ALLOW),
        ("lectura", "file_write", Decision.DENY),
        ("lectura", "shell_exec", Decision.DENY),
        ("confirmar", "display", Decision.ALLOW),
        ("confirmar", "file_write", Decision.CONFIRM),
        ("confirmar", "shell_exec", Decision.CONFIRM),
        ("libre", "file_write", Decision.ALLOW),
        ("libre", "shell_exec", Decision.CONFIRM),
    ],
)
def test_matrix(mode: str, tool: str, decision: Decision) -> None:
    assert policy(modo=mode).evaluate(tool).decision is decision


def test_lists_take_precedence_in_order() -> None:
    p = policy(
        modo="libre",
        denegar=["shell_*"],
        permitir=["shell_exec", "file_write"],
        confirmar=["file_*"],
    )
    # denegar gana a permitir
    assert p.evaluate("shell_exec").decision is Decision.DENY
    # permitir gana a confirmar
    assert p.evaluate("file_write").decision is Decision.ALLOW
    assert p.evaluate("file_read").decision is Decision.CONFIRM


def test_denied_tools_are_hidden() -> None:
    p = policy(modo="lectura")
    assert p.visible("web_search")
    assert not p.visible("shell_exec")


def test_set_mode_validates() -> None:
    p = policy()
    p.set_mode("libre")
    assert p.mode == "libre"
    with pytest.raises(ValueError):
        p.set_mode("todo-vale")
