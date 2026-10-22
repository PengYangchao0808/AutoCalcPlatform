#!/usr/bin/env python3.11
"""Test-only Controller launcher with a file-armed publication fault.

B13 needs a *real* Controller whose publication write fails once, after the real
QC process has already completed, so the harness can prove that clearing the
fault retries **publication only** (no second QC spawn / ``bsub``).  The
production publication sequence already funnels every display-manifest write
through one seam: :func:`acp.calculations.result_publication.register_result_manifest`.
This launcher wraps that seam and raises :class:`OSError` while a fault file
exists; when the file is removed the original writer runs unchanged.

It is deliberately a *launcher*, not a second publisher: the real Controller
still owns the job, store, receipt gate and terminal CAS.  The wrapper adds no
publication implementation and never imports ``acp`` before installing the
fault, so the Controller's own module graph resolves the patched seam.

Usage::

    python -m acp2_controller_fault --fault-file /path/fault.json -- \
        run serve --host 127.0.0.1 --port 18771 --run-root /path/run-root

Exit codes follow ``acp``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

__all__ = ["PUBLICATION_FAULT_CODE", "install_publication_fault", "main"]

#: Stable prefix so the harness can distinguish this injected fault from a real
#: integrity failure in the event log / job error.
PUBLICATION_FAULT_CODE = "ACP2_ACCEPTANCE_PUBLICATION_FAULT"


def install_publication_fault(result_publication: Any, fault_file: Path) -> Any:
    """Patch ``result_publication.register_result_manifest`` file-conditionally.

    Args:
        result_publication: The already-imported
            :mod:`acp.calculations.result_publication` module.
        fault_file: While this file exists every display-manifest write raises
            :class:`OSError`.  Removing it restores the original writer on the
            very next call (no process restart needed).

    Returns:
        The original ``register_result_manifest`` callable, for assertions.
    """

    real = result_publication.register_result_manifest

    def register_result_manifest(
        result_dir: Path | str,
        manifest: Any,
        *,
        publisher: Any | None = None,
    ) -> Path:
        if fault_file.exists():
            raise OSError(
                f"{PUBLICATION_FAULT_CODE}: publication write refused while {fault_file} exists"
            )
        return real(result_dir, manifest, publisher=publisher)

    result_publication.register_result_manifest = register_result_manifest
    return real


def _split_argv(argv: list[str]) -> tuple[Path, list[str]]:
    if "--" not in argv:
        raise ValueError("missing '--' separator before acp arguments")
    head, tail = argv[: argv.index("--")], argv[argv.index("--") + 1 :]
    fault_file: Path | None = None
    index = 0
    while index < len(head):
        token = head[index]
        if token == "--fault-file" and index + 1 < len(head):
            fault_file = Path(head[index + 1])
            index += 2
            continue
        raise ValueError(f"unknown launcher argument: {token!r}")
    if fault_file is None:
        raise ValueError("missing --fault-file")
    if not tail:
        raise ValueError("missing acp arguments after '--'")
    return fault_file, tail


def main(argv: list[str] | None = None) -> int:
    """Install the fault seam and run the real ``acp`` CLI unchanged."""
    import acp.calculations.result_publication as result_publication

    try:
        fault_file, acp_argv = _split_argv(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        print(f"acp2_controller_fault: {exc}", file=sys.stderr)
        return 3
    install_publication_fault(result_publication, fault_file)

    from acp.cli import main as acp_main

    return int(acp_main(acp_argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
