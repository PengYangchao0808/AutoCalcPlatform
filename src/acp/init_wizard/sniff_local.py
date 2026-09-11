"""Local software sniffing + manual path specification for `acp init`.

Discovery wraps :func:`cccp.software.discover_all_detailed` with the
wizard's own target file passed as the explicit ``--config`` merge source
(``load_config`` source 4, overriding earlier sources) — so a re-sniff
reflects manual paths written to a NON-DEFAULT target too; for the default
``~/.cccp.yaml`` the double-merge (source 2 + source 4) is idempotent and
harmless.  Manual specification follows decision D10: validate
(is_file + X_OK), display the ``detect_version`` probe as confirmation,
accept the operator's explicit path regardless, empty input = skip.

``print`` is the UI channel; the module ``logger`` is for diagnostics.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.init_wizard.persist import save_target, set_executable_path
from acp.init_wizard.prompts import (
    ask,
    ask_local_path,
    ask_remote_dir,
    ask_secret,
    menu,
)
from cccp import software as cccp_software
from cccp.config import load_config
from cccp.software import SoftwareDiscovery

logger = logging.getLogger(__name__)

__all__ = [
    "PromptBundle",
    "apply_local_spec",
    "manual_spec_local",
    "render_sniff_table",
    "sniff_local",
    "versions_via",
]


@dataclass(frozen=True)
class PromptBundle:
    """Injection seam bundling the wizard prompt helpers (D15 contract).

    T5/T6/T7 define identical copies — keep verbatim for parallel-wave
    consistency.  Tests inject fakes as ``PromptBundle(menu=fake, ask=fake,
    ...)``; production code passes ``PromptBundle()``.
    """

    menu: Callable[..., int] = menu
    ask: Callable[..., str] = ask
    ask_secret: Callable[[str], str] = ask_secret
    ask_local_path: Callable[[str], Path] = ask_local_path
    ask_remote_dir: Callable[[str, str], str] = ask_remote_dir


def sniff_local(target: Path) -> dict[str, SoftwareDiscovery]:
    """Sniff local QC executables through the wizard's target config view.

    ALWAYS passes *target* as the explicit ``config_path`` merge source —
    source 4 overrides earlier sources, so re-sniffing reflects manual
    paths written to a NON-DEFAULT ``--config`` target.  A missing target
    file is simply not merged (no error).

    Args:
        target: Wizard target YAML file path.

    Returns:
        Per-software discovery picture keyed by software name
        (:class:`cccp.software.SoftwareDiscovery`).
    """
    config = load_config(config_path=target)
    return cccp_software.discover_all_detailed(config=config)


def versions_via(entries: dict[str, SoftwareDiscovery]) -> dict[str, str]:
    """Decorate *entries* with cached version probes.

    Args:
        entries: Discovery picture from :func:`sniff_local`.

    Returns:
        Software name → cached normalized version (``""`` when unresolved
        or the probe fails — negative caching mirrors ``version_cached``).
    """
    return {
        name: cccp_software.version_cached(name, entry.resolved) for name, entry in entries.items()
    }


def render_sniff_table(entries: dict[str, SoftwareDiscovery]) -> str:
    """Render *entries* as a plain-text sniff table (no third-party deps).

    Columns: 软件 / 解析路径 / 版本 / 来源.  Missing paths show ``未找到``,
    unknown versions show ``未知``, absent sources show ``-``.

    Args:
        entries: Discovery picture from :func:`sniff_local`.

    Returns:
        The aligned multi-line table string (no trailing newline).
    """
    versions = versions_via(entries)
    headers = ("软件", "解析路径", "版本", "来源")
    rows = [
        (
            name,
            str(entry.resolved) if entry.resolved else "未找到",
            versions.get(name, "") or "未知",
            entry.source or "-",
        )
        for name, entry in entries.items()
    ]
    widths = [
        max(len(column), *(len(row[i]) for row in rows)) if rows else len(column)
        for i, column in enumerate(headers)
    ]

    def _fmt(cells: tuple[str, ...]) -> str:
        padded = (cell.ljust(width) for cell, width in zip(cells, widths, strict=True))
        return "  ".join(padded).rstrip()

    lines = [_fmt(headers), "  ".join("-" * width for width in widths)]
    lines.extend(_fmt(row) for row in rows)
    return "\n".join(lines)


def _validate_executable_file(raw: str) -> str | None:
    """Reject anything that is not an existing executable file (D10)."""
    path = Path(raw).expanduser()
    if path.is_file() and os.access(path, os.X_OK):
        return None
    return "路径必须是存在的可执行文件"


def manual_spec_local(missing: list[str], prompts: PromptBundle) -> dict[str, str]:
    """Interactively pin absolute paths for every missing software (D10).

    Uses ``prompts.ask`` with an is_file + X_OK validator and
    ``allow_empty=True`` — empty input skips that software (no dict entry).
    ``ask_local_path`` is deliberately NOT used: it has no empty-input
    escape and would re-prompt forever.  A valid input is probed with
    :func:`cccp.software.detect_version` and the result (version or
    ``未知``) is displayed as confirmation, but the path is accepted
    regardless — the operator's explicit choice wins; probe failures never
    abort the wizard.

    Args:
        missing: Software names that failed to resolve.
        prompts: Prompt helper bundle (fakes injectable in tests).

    Returns:
        Software name → absolute executable path for every specified entry.
    """
    specs: dict[str, str] = {}
    for name in missing:
        raw = prompts.ask(
            f"请输入 {name} 可执行文件的绝对路径（直接回车跳过）",
            validate=_validate_executable_file,
            allow_empty=True,
        )
        if not raw:
            print(f"已跳过 {name}")
            continue
        path = Path(raw).expanduser()
        try:
            version = cccp_software.detect_version(name, path)
        except Exception as exc:  # probe must never abort the wizard
            logger.debug("version probe failed for %s at %s: %s", name, path, exc)
            version = None
        print(f"{name} -> {path}")
        print(f"  版本: {version if version else '未知'}")
        specs[name] = str(path)
    return specs


def apply_local_spec(
    target_path: Path,
    data: dict[str, Any],
    specs: dict[str, str],
) -> dict[str, SoftwareDiscovery]:
    """Persist manual specs into the raw target file, then re-sniff (D10).

    Calls :func:`acp.init_wizard.persist.set_executable_path` per spec and
    :func:`acp.init_wizard.persist.save_target` (never
    ``cccp.config.save_config``), then re-sniffs via :func:`sniff_local` —
    the explicit target merge makes the fresh picture reflect the just
    written paths.

    Args:
        target_path: Wizard target YAML file path.
        data: Raw target mapping (mutated in place, then saved).
        specs: Software name → absolute executable path.

    Returns:
        The fresh discovery picture after the write.
    """
    for name, raw_path in specs.items():
        set_executable_path(data, name, Path(raw_path))
    save_target(target_path, data)
    return sniff_local(target_path)
