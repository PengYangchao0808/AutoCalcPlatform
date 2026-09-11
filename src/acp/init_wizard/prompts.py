"""Interactive prompt helpers for the `acp init` wizard.

Input-robustness core (draft decisions D15/D12/D18b): every raw
``input()``/``getpass()`` call goes through these helpers so that
EOF/Ctrl-C uniformly raises :class:`WizardAborted` and invalid answers
re-prompt with Chinese UI messages.  ``print``/``input`` are the UI
channel; the module ``logger`` is for diagnostics only.
"""

from __future__ import annotations

import getpass
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "WizardAborted",
    "ask",
    "ask_local_path",
    "ask_remote_dir",
    "ask_secret",
    "menu",
]


class WizardAborted(Exception):  # noqa: N818 — name pinned by the wizard plan contract
    """User aborted the wizard via EOF or Ctrl-C (D15 exit contract)."""


def _read_input(prompt: str) -> str:
    """Read one stripped line; EOF/Ctrl-C aborts the wizard."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt) as exc:
        logger.debug("wizard input aborted: %s", exc.__class__.__name__)
        raise WizardAborted from None


def menu(title: str, options: list[str], allow_q: bool = True) -> int:
    """Show a 1-based numbered menu and return the chosen 1-based index.

    ``q`` (when ``allow_q``) returns the sentinel ``0`` meaning "quit";
    downstream flows map 0 to a graceful exit.  Invalid or empty choices
    re-prompt.
    """
    numbered = "\n".join(f"  {i}) {opt}" for i, opt in enumerate(options, start=1))
    quit_line = "  q) 退出" if allow_q else ""
    while True:
        print(f"{title}\n{numbered}{quit_line}")
        raw = _read_input("请选择: ")
        if allow_q and raw.lower() == "q":
            return 0
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        print("输入无效" + (f"，请输入 1-{len(options)} 之间的数字" if allow_q else ""))
        logger.debug("invalid menu choice: %r", raw)


def ask(
    prompt: str,
    default: str | None = None,
    validate: Callable[[str], str | None] | None = None,
    allow_empty: bool = False,
) -> str:
    """Ask for a free-form string with optional default and validator.

    Empty input returns ``default`` when set; otherwise re-prompts unless
    ``allow_empty``.  ``validate`` returns an error message (shown, then
    re-prompt) or ``None`` to accept.
    """
    shown = f"{prompt} [默认: {default}]: " if default is not None else f"{prompt}: "
    while True:
        raw = _read_input(shown)
        if not raw:
            if default is not None:
                return default
            if allow_empty:
                return ""
            print("输入无效，不能为空")
            continue
        if validate is not None:
            error = validate(raw)
            if error is not None:
                print(error)
                logger.debug("validation rejected %r: %s", raw, error)
                continue
        return raw


def ask_secret(prompt: str) -> str:
    """Read a secret via ``getpass``; non-TTY stdin aborts (D15).

    Piped/closed stdin cannot supply a hidden password, so non-TTY input
    is defined as an immediate EOF-style abort.
    """
    if not sys.stdin.isatty():
        logger.debug("ask_secret on non-TTY stdin — aborting")
        raise WizardAborted
    try:
        return getpass.getpass(f"{prompt}: ")
    except (EOFError, KeyboardInterrupt):
        logger.debug("secret input aborted")
        raise WizardAborted from None


def ask_local_path(prompt: str) -> Path:
    """Ask for a path to an existing executable file (is_file + X_OK).

    Returns the ``expanduser``-resolved :class:`Path`; empty or invalid
    input re-prompts.
    """
    shown = f"{prompt}: "
    while True:
        raw = _read_input(shown)
        if not raw:
            print("输入无效，不能为空")
            continue
        path = Path(raw).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return path
        print("路径必须是存在的可执行文件")
        logger.debug("rejected local path %r", raw)


def ask_remote_dir(prompt: str, default: str) -> str:
    """Ask for a remote directory path (D12): non-empty, space-free,
    absolute or ``~/``-prefixed.  Empty input accepts ``default``; with an
    empty default there is no escape hatch — invalid input keeps
    re-prompting (EOF aborts).
    """
    shown = f"{prompt} [默认: {default}]: " if default else f"{prompt}: "
    while True:
        raw = _read_input(shown)
        if not raw:
            if default:
                return default
            print("目录不能为空")
            continue
        if " " in raw:
            print("目录不能包含空格")
            logger.debug("rejected remote dir %r (space)", raw)
            continue
        if not (raw.startswith("/") or raw.startswith("~/")):
            print("目录必须是绝对路径或以 ~/ 开头")
            logger.debug("rejected remote dir %r (relative)", raw)
            continue
        return raw
