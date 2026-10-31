#!/usr/bin/env python3.11
"""Acceptance-only Controller launcher for the B13 publication fault.

The B13 evidence must be runnable *exactly as recorded*.  The stock Controller
has no fault switch (and B13 must not add one to product code), so this file is
the acceptance-only seam:

* it is invoked by absolute path, not by ``-m`` and not by an editable install;
* ``--source`` puts the frozen P1 worktree on ``sys.path`` before ``acp`` is
  imported, so the same Controller code runs from any working directory;
* ``--fault-file`` points at a persisted JSON descriptor.  Only
  ``fault=publication_failure`` / ``seam=register_result_manifest`` is honoured;
  while ``armed`` is ``true`` that one display-manifest write raises, and when
  the harness rewrites the descriptor with ``armed`` ``false`` the original
  writer is used again;
* the real ``acp`` CLI still owns the Controller, store, receipt gate, terminal
  CAS and publication implementation.  No second publisher/executor exists and
  no product module imports this file.

Usage (run from the repository root)::

    /ACPServer/acp2-development/venvs/b/bin/python \
        /ACPServer/AutoCalcPlatform/scripts/acceptance/acp2_fault_controller.py \
        --source /ACPServer/acp2-development/B/src \
        --fault-file /ACPServer/AutoCalcPlatform/reports/acp2_implementation/bb45a46/\
B/B13/fault/publication-fault.json \
        --config /ACPServer/acp2-development/data/b/controller-config/cccp.yaml \
        --candidate-revision 10eb71e4bc1811054c9d19ae4fd71d974e027f8a \
        --orca /home/xieningke/orca611/orca \
        --orca-ld-library-path /home/xieningke/openmpi418/lib:/home/xieningke/orca611 \
        -- run serve --host 127.0.0.1 --port 18771 \
        --run-root /ACPServer/acp2-development/data/b/run-root-b13 \
        --poll-interval 1 --no-browser

Exit codes follow the wrapped ``acp`` CLI.  This file is acceptance tooling; it
must never be imported by ``src/**``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

__all__ = [
    "FAULT_CODE",
    "SUPPORTED_FAULT",
    "SUPPORTED_SEAM",
    "install_publication_fault",
    "load_fault_descriptor",
    "main",
]

#: Stable marker embedded in the injected ``OSError`` and in the job event.
FAULT_CODE = "ACP2_ACCEPTANCE_PUBLICATION_FAULT"
SUPPORTED_FAULT = "publication_failure"
SUPPORTED_SEAM = "register_result_manifest"


def load_fault_descriptor(fault_file: Path) -> dict[str, Any]:
    """Read the persisted JSON descriptor; missing means disarmed."""

    if not fault_file.is_file():
        return {"armed": False, "fault": None, "seam": None}
    try:
        document = json.loads(fault_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable fault descriptor {fault_file}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"fault descriptor {fault_file} must be a JSON object")
    fault = document.get("fault")
    seam = document.get("seam", SUPPORTED_SEAM)
    if fault not in {None, SUPPORTED_FAULT}:
        raise ValueError(f"unsupported acceptance fault {fault!r} in {fault_file}")
    if seam not in {None, SUPPORTED_SEAM}:
        raise ValueError(f"unsupported acceptance seam {seam!r} in {fault_file}")
    return document


def install_publication_fault(result_publication: Any, fault_file: Path) -> Any:
    """Patch only ``register_result_manifest`` and re-read the descriptor per call.

    Args:
        result_publication: The already-imported
            :mod:`acp.calculations.result_publication` module.
        fault_file: Persisted JSON descriptor.  ``armed=true`` raises for the
            single supported publication fault; any other value forwards to the
            original writer unchanged.

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
        descriptor = load_fault_descriptor(fault_file)
        if descriptor.get("armed") is True:
            if descriptor.get("fault") != SUPPORTED_FAULT:
                raise ValueError(f"armed descriptor {fault_file} does not name {SUPPORTED_FAULT!r}")
            raise OSError(f"{FAULT_CODE}: publication write refused while {fault_file} is armed")
        return real(result_dir, manifest, publisher=publisher)

    result_publication.register_result_manifest = register_result_manifest
    return real


def _split_argv(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    if "--" not in argv:
        raise ValueError("missing '--' separator before acp arguments")
    split = argv.index("--")
    head, tail = argv[:split], argv[split + 1 :]
    parser = argparse.ArgumentParser(prog="acp2_fault_controller", add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--fault-file", required=True)
    parser.add_argument("--config")
    parser.add_argument("--candidate-revision")
    parser.add_argument("--orca")
    parser.add_argument("--orca-ld-library-path")
    try:
        parsed, unknown = parser.parse_known_args(head)
    except SystemExit as exc:
        raise ValueError(f"invalid launcher arguments: {exc}") from exc
    if unknown:
        raise ValueError(f"unknown launcher argument(s): {unknown}")
    if not tail or tail[0] != "run" or len(tail) < 2 or tail[1] != "serve":
        raise ValueError("this acceptance seam only wraps 'run serve'")
    return {
        "source": Path(parsed.source),
        "fault_file": Path(parsed.fault_file),
        "config": Path(parsed.config) if parsed.config else None,
        "candidate_revision": parsed.candidate_revision,
        "orca": Path(parsed.orca) if parsed.orca else None,
        "orca_ld_library_path": parsed.orca_ld_library_path,
    }, tail


def _configure_environment(options: dict[str, Any]) -> None:
    source = options["source"]
    if source.name == "src":
        source_root = source.parent
    else:
        source_root = source
    sys.path.insert(0, str(source / "src" if source.name != "src" else source))
    os.environ["ACP2_SOURCE"] = str(source_root)
    if options.get("config") is not None:
        os.environ["ACP_CONFIG"] = str(options["config"])
    if options.get("candidate_revision"):
        os.environ["ACP_CANDIDATE_REVISION"] = str(options["candidate_revision"])
    if options.get("orca") is not None:
        os.environ["CONFSEARCH_ORCA_PATH"] = str(options["orca"])
    if options.get("orca_ld_library_path"):
        os.environ["CONFSEARCH_ORCA_LD_LIBRARY_PATH"] = str(options["orca_ld_library_path"])
        os.environ["LD_LIBRARY_PATH"] = str(options["orca_ld_library_path"])


def main(argv: list[str] | None = None) -> int:
    """Install the JSON-armed publication seam, then run the real ``acp`` CLI."""

    try:
        options, acp_argv = _split_argv(list(sys.argv[1:] if argv is None else argv))
    except ValueError as exc:
        print(f"acp2_fault_controller: {exc}", file=sys.stderr)
        return 3
    try:
        load_fault_descriptor(options["fault_file"])
    except ValueError as exc:
        print(f"acp2_fault_controller: {exc}", file=sys.stderr)
        return 3
    _configure_environment(options)

    import acp.calculations.result_publication as result_publication

    install_publication_fault(result_publication, options["fault_file"])

    from acp.cli import main as acp_main

    return int(acp_main(acp_argv) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
