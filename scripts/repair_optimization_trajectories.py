#!/usr/bin/env python
"""One-time repair: rebuild dangling optimization-trajectory geometry refs.

Historical jobs could persist ``optimization_trajectory.json`` files whose
cycles carry ``geometry_ref`` entries pointing at ``cycles/cycle_NNNN.xyz``
files that were never written (the final ORCA ``.out`` re-parse ran with
persistence disabled).  This script walks every task directory under the
run root, finds such trajectories, and re-runs the authoritative finalize
so the missing per-cycle XYZ files are materialized from the ORCA output.

Usage (inside the repository environment):
    python scripts/repair_optimization_trajectories.py [RUN_ROOT] [--dry-run]

The omitted RUN_ROOT uses ACP_RUN_ROOT or the platform default.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol, TypeAlias, final

from acp.calculations.primitives.optimization_trajectory import (
    finalize_optimization_trajectory,
)
from acp.core.paths import resolve_run_root

logger = logging.getLogger(__name__)

TRAJECTORY_NAME: Final = "optimization_trajectory.json"
RepairStatus = Literal["OK", "REPAIRED", "UNREPAIRABLE", "DRY-RUN"]
JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class _JsonLoader(Protocol):
    def loads(self, text: str, /) -> JsonValue: ...


_JSON_LOADER: _JsonLoader = json


@dataclass(frozen=True, slots=True)
class _Trajectory:
    item_id: str
    geometry_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Outcome:
    status: RepairStatus
    detail: str


@final
class _TrajectoryFormatError(ValueError):
    path: Path
    reason: str

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(reason)

    def __str__(self) -> str:
        return f"{self.path}: {self.reason}"


def _discover_trajectories(run_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for opt_root in sorted(run_root.glob("*/WORK/03_OPT")):
        if not opt_root.is_dir():
            continue
        for path in sorted(opt_root.rglob(TRAJECTORY_NAME)):
            relative_parts = path.relative_to(opt_root).parts[:-1]
            if path.is_file() and not any(part.startswith("rescue_") for part in relative_parts):
                candidates.append(path)
    return candidates


def _load_trajectory(path: Path) -> _Trajectory:
    raw = _JSON_LOADER.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise _TrajectoryFormatError(path, "JSON root is not an object")

    raw_cycles = raw.get("cycles")
    if raw_cycles is not None and not isinstance(raw_cycles, list):
        raise _TrajectoryFormatError(path, "cycles is not an array")

    refs: list[str] = []
    for raw_cycle in raw_cycles or []:
        if not isinstance(raw_cycle, dict):
            continue
        geometry_ref = raw_cycle.get("geometry_ref")
        if isinstance(geometry_ref, str) and geometry_ref:
            refs.append(geometry_ref)
    return _Trajectory(item_id=str(raw.get("item_id") or ""), geometry_refs=tuple(refs))


def _dangling_count(trajectory: _Trajectory, base_dir: Path) -> int:
    return sum(not (base_dir / reference).is_file() for reference in trajectory.geometry_refs)


def _source_outputs(attempt_dir: Path) -> list[Path]:
    outputs = [path for path in attempt_dir.glob("*.out") if path.is_file()]
    outputs.extend(path for path in attempt_dir.glob("rescue_*/*.out") if path.is_file())
    return outputs


def _process_trajectory(path: Path, *, dry_run: bool) -> _Outcome:
    try:
        before = _load_trajectory(path)
    except (OSError, json.JSONDecodeError, _TrajectoryFormatError) as exc:
        return _Outcome("UNREPAIRABLE", f"cannot read trajectory: {exc}")

    dangling_before = _dangling_count(before, path.parent)
    if dangling_before == 0:
        return _Outcome("OK", "dangling 0")

    if not _source_outputs(path.parent):
        return _Outcome("UNREPAIRABLE", f"dangling {dangling_before}; no ORCA output")

    if dry_run:
        return _Outcome("DRY-RUN", f"dangling {dangling_before}; source output present")

    _ = finalize_optimization_trajectory(path.parent, item_id=before.item_id)
    try:
        after = _load_trajectory(path)
    except (OSError, json.JSONDecodeError, _TrajectoryFormatError) as exc:
        return _Outcome("UNREPAIRABLE", f"cannot read finalized trajectory: {exc}")

    dangling_after = _dangling_count(after, path.parent)
    if dangling_after == 0:
        return _Outcome("REPAIRED", f"dangling {dangling_before}->{dangling_after}")
    return _Outcome(
        "UNREPAIRABLE",
        f"dangling {dangling_before}->{dangling_after} after finalize",
    )


def _run(run_root: Path, *, dry_run: bool) -> int:
    candidates = _discover_trajectories(run_root)
    ok = repaired = unrepairable = dry_run_count = 0
    for path in candidates:
        outcome = _process_trajectory(path, dry_run=dry_run)
        print(f"{outcome.status} ({outcome.detail}) | {path}")
        ok += outcome.status == "OK"
        repaired += outcome.status == "REPAIRED"
        unrepairable += outcome.status == "UNREPAIRABLE"
        dry_run_count += outcome.status == "DRY-RUN"

    summary_fields = [
        f"total={len(candidates)}",
        f"ok={ok}",
        f"repaired={repaired}",
        f"unrepairable={unrepairable}",
        f"dry_run={dry_run_count}",
    ]
    print("SUMMARY | " + " ".join(summary_fields))
    return 0


class _ParsedArguments(argparse.Namespace):
    run_root: Path
    dry_run: bool

    def __init__(self) -> None:
        super().__init__()
        self.run_root = Path()
        self.dry_run = False


def main(argv: list[str] | None = None) -> int:
    """Repair dangling optimization-trajectory geometry references."""
    parser = argparse.ArgumentParser(
        description="Rebuild missing optimization trajectory XYZ files from ORCA outputs."
    )
    _ = parser.add_argument(
        "run_root",
        nargs="?",
        type=Path,
        default=resolve_run_root(None),
        help="ACP data run root (defaults to ACP_RUN_ROOT or the platform default)",
    )
    _ = parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report repairable trajectories without changing files",
    )
    args = _ParsedArguments()
    _ = parser.parse_args(argv, namespace=args)
    try:
        return _run(resolve_run_root(args.run_root), dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001, BROAD_EXCEPT_OK
        logger.exception("optimization trajectory repair failed")
        print(f"ERROR | {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
