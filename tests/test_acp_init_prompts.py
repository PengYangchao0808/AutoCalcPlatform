"""TDD tests for acp.init_wizard.prompts — interactive input robustness core.

Covers plan task 2 acceptance criteria:
(a) scripted menu input picks option 2; (b) EOFError → WizardAborted;
(c) KeyboardInterrupt → WizardAborted; (d) invalid menu choice re-prompts;
(e) ask_remote_dir validation; plus menu `q` → 0, ask default/validate,
ask_secret TTY contract, ask_local_path is_file + X_OK gating.
"""

from __future__ import annotations

import builtins
import getpass
import sys
from pathlib import Path
from typing import Any

import pytest

from acp.init_wizard.prompts import (
    WizardAborted,
    ask,
    ask_local_path,
    ask_remote_dir,
    ask_secret,
    menu,
)


def _script_input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    """Feed a scripted answer sequence to builtins.input."""
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(it))


def _script_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every input() raise EOFError (end of scripted stdin)."""
    def _raise(prompt: str = "") -> str:
        raise EOFError
    monkeypatch.setattr(builtins, "input", _raise)


def _script_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every input() raise KeyboardInterrupt (Ctrl-C)."""
    def _raise(prompt: str = "") -> str:
        raise KeyboardInterrupt
    monkeypatch.setattr(builtins, "input", _raise)


# ---------------------------------------------------------------------------
# (a) scripted input sequence picks menu option 2
# ---------------------------------------------------------------------------

def test_menu_scripted_input_picks_option_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["2"])
    assert menu("选择资源", ["本地 local", "节点 A"]) == 2


def test_menu_q_returns_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["q"])
    assert menu("选择资源", ["本地 local", "节点 A"]) == 0


def test_menu_q_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["Q"])
    assert menu("选择资源", ["本地 local"]) == 0


def test_menu_without_q_treats_q_as_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["q", "1"])
    assert menu("选择资源", ["本地 local"], allow_q=False) == 1


def test_menu_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["9", "0", "2"])
    assert menu("选择资源", ["本地 local", "节点 A"]) == 2


# ---------------------------------------------------------------------------
# (d) invalid menu choice re-prompts then accepts valid
# ---------------------------------------------------------------------------

def test_menu_invalid_then_valid_reprompts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _script_input(monkeypatch, ["abc", "", "2"])
    assert menu("选择资源", ["本地 local", "节点 A"]) == 2
    out = capsys.readouterr().out
    assert "输入无效" in out
    assert "本地 local" in out  # options were rendered 1-based


# ---------------------------------------------------------------------------
# (b) EOFError → WizardAborted (no traceback leak; tests escape loops via EOF)
# ---------------------------------------------------------------------------

def test_menu_eof_raises_wizard_aborted(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_eof(monkeypatch)
    with pytest.raises(WizardAborted):
        menu("选择资源", ["本地 local"])


def test_ask_eof_raises_wizard_aborted(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_eof(monkeypatch)
    with pytest.raises(WizardAborted):
        ask("节点名称", default="node1")


def test_ask_remote_dir_eof_escapes_validation_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid answers loop forever; only EOF escapes (never returns bad value)."""
    answers = iter(["/a b", "rel/path", ""])

    def _fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", _fake_input)
    with pytest.raises(WizardAborted):
        ask_remote_dir("远程工作目录", default="")


# ---------------------------------------------------------------------------
# (c) KeyboardInterrupt → WizardAborted
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("helper", ["menu", "ask", "ask_local_path"])
def test_keyboard_interrupt_raises_wizard_aborted(
    monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    _script_interrupt(monkeypatch)
    funcs: dict[str, Any] = {
        "menu": lambda: menu("选择资源", ["本地 local"]),
        "ask": lambda: ask("节点名称"),
        "ask_local_path": lambda: ask_local_path("xtb 路径"),
    }
    with pytest.raises(WizardAborted):
        funcs[helper]()


# ---------------------------------------------------------------------------
# ask: default-on-empty, allow_empty, validate reject-then-accept
# ---------------------------------------------------------------------------

def test_ask_returns_default_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, [""])
    assert ask("节点名称", default="node1") == "node1"


def test_ask_default_hint_rendered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[str] = []

    def _fake_input(prompt: str = "") -> str:
        seen.append(prompt)
        return "x"

    monkeypatch.setattr(builtins, "input", _fake_input)
    ask("节点名称", default="node1")
    assert "node1" in seen[0]


def test_ask_empty_no_default_reprompts_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _script_input(monkeypatch, ["", "hello"])
    assert ask("节点名称") == "hello"


def test_ask_allow_empty_returns_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, [""])
    assert ask("备注", allow_empty=True) == ""


def test_ask_validate_reject_then_accept(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _numeric_only(value: str) -> str | None:
        return None if value.isdigit() else "必须为数字"

    _script_input(monkeypatch, ["abc", "42"])
    assert ask("并发数", validate=_numeric_only) == "42"
    assert "必须为数字" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ask_secret: getpass on TTY; non-TTY → WizardAborted (D15 defined behavior)
# ---------------------------------------------------------------------------

def test_ask_secret_uses_getpass_on_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "s3cret")
    assert ask_secret("密码") == "s3cret"


def test_ask_secret_non_tty_raises_wizard_aborted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(WizardAborted):
        ask_secret("密码")


def test_ask_secret_getpass_eof_raises_wizard_aborted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def _raise(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(getpass, "getpass", _raise)
    with pytest.raises(WizardAborted):
        ask_secret("密码")


def test_ask_secret_keyboard_interrupt_raises_wizard_aborted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def _raise(prompt: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(getpass, "getpass", _raise)
    with pytest.raises(WizardAborted):
        ask_secret("密码")


# ---------------------------------------------------------------------------
# ask_local_path: existing file + X_OK; empty/missing/dir/non-exec rejected
# ---------------------------------------------------------------------------

@pytest.fixture
def executable_file(tmp_path: Path) -> Path:
    f = tmp_path / "xtb"
    f.write_text("#!/bin/sh\n", encoding="utf-8")
    f.chmod(0o755)
    return f


def test_ask_local_path_accepts_executable_file(
    monkeypatch: pytest.MonkeyPatch, executable_file: Path
) -> None:
    _script_input(monkeypatch, [str(executable_file)])
    assert ask_local_path("xtb 路径") == executable_file


def test_ask_local_path_tilde_expansion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "orca"
    f.write_text("#!/bin/sh\n", encoding="utf-8")
    f.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    _script_input(monkeypatch, ["~/orca"])
    assert ask_local_path("orca 路径") == Path("~/orca").expanduser()


def test_ask_local_path_rejects_empty_missing_dir_then_accepts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, executable_file: Path
) -> None:
    _script_input(
        monkeypatch,
        ["", str(tmp_path / "missing"), str(tmp_path), str(executable_file)],
    )
    assert ask_local_path("xtb 路径") == executable_file


def test_ask_local_path_rejects_non_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, executable_file: Path
) -> None:
    plain = tmp_path / "plain.txt"
    plain.write_text("data", encoding="utf-8")
    plain.chmod(0o644)
    _script_input(monkeypatch, [str(plain), str(executable_file)])
    assert ask_local_path("xtb 路径") == executable_file


# ---------------------------------------------------------------------------
# (e) ask_remote_dir validation: non-empty, space-free, absolute or ~/-prefixed
# ---------------------------------------------------------------------------

def test_ask_remote_dir_accepts_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["/scratch/user/acp_jobs"])
    assert ask_remote_dir("远程工作目录", default="~/acp_jobs") == "/scratch/user/acp_jobs"


def test_ask_remote_dir_accepts_tilde(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, ["~/acp_jobs"])
    assert ask_remote_dir("远程工作目录", default="~/acp_jobs") == "~/acp_jobs"


def test_ask_remote_dir_default_accepted_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_input(monkeypatch, [""])
    assert ask_remote_dir("远程工作目录", default="~/acp_jobs") == "~/acp_jobs"


def test_ask_remote_dir_rejects_space_relative_and_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["/a b", "rel/path", "", "/scratch/user/acp_jobs"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    result = ask_remote_dir("远程工作目录", default="")
    assert result == "/scratch/user/acp_jobs"
    out = capsys.readouterr().out
    assert "空格" in out
    assert "绝对路径" in out
    assert "不能为空" in out


def test_ask_remote_dir_empty_with_empty_default_never_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """default='' gives no escape hatch — empty input re-prompts (EOF escapes)."""
    answers = iter([""])

    def _fake_input(prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(builtins, "input", _fake_input)
    with pytest.raises(WizardAborted):
        ask_remote_dir("远程工作目录", default="")
