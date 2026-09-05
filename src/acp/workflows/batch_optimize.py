"""Public BatchOptimize workflow adapter."""
# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from typing_extensions import assert_never

from acp.calculations.batch.engine import BatchLayoutMode, BatchOptimizeEngine
from acp.calculations.batch.models import (
    BatchStructureItem,
    JsonObject,
    load_batch_request,
    load_items_from_result_manifest,
    load_items_from_xyz_file,
)
from acp.calculations.batch.options import BatchMethodOptions
from acp.calculations.contracts import JsonValue
from acp.calculations.progress import ProgressReporter
from acp.core.workflow import WorkflowResult

BatchItemsSource = Path | str | list[BatchStructureItem] | JsonObject
BatchSelection = str | list[str] | tuple[str, ...] | None

_PROFILE_STAGES: Final[dict[str, tuple[str, ...]]] = {
    "opt_only": ("prepare", "optimize", "finalize"),
    "opt_freq": ("prepare", "optimize", "frequency", "finalize"),
    "opt_freq_sp": (
        "prepare",
        "optimize",
        "frequency",
        "single_point",
        "finalize",
    ),
    "opt_freq_sp_thermo": (
        "prepare",
        "optimize",
        "frequency",
        "single_point",
        "thermochemistry",
        "finalize",
    ),
}


class BatchOptimizeInputError(ValueError):
    """Raised when a BatchOptimize source or selection is unusable."""


def _result_task_dir(manifest_path: Path) -> Path:
    if manifest_path.parent.name == "RESULT":
        return manifest_path.parent.parent
    return manifest_path.parent


def _load_items(source: BatchItemsSource) -> list[BatchStructureItem]:
    match source:
        case list():
            return list(source)
        case dict():
            return load_batch_request(source)
        case Path() | str():
            path = Path(source).expanduser()
            if path.is_dir():
                return load_items_from_result_manifest(path)
            if path.suffix.lower() == ".xyz":
                return load_items_from_xyz_file(path)
            if path.suffix.lower() == ".json":
                if path.name == "result_manifest.json":
                    return load_items_from_result_manifest(_result_task_dir(path))
                return load_batch_request(path)
            raise BatchOptimizeInputError(
                f"BatchOptimize source must be a task directory, JSON, or XYZ file: {path}"
            )
        case unreachable:
            assert_never(unreachable)


def _selected_items(
    items: list[BatchStructureItem], selection: BatchSelection
) -> list[BatchStructureItem]:
    match selection:
        case None:
            return items
        case str() as text:
            requested = [part.strip() for part in text.split(",") if part.strip()]
        case list() | tuple() as values:
            requested = [str(value).strip() for value in values if str(value).strip()]
        case unreachable:
            assert_never(unreachable)
    if not requested:
        return items
    requested_ids = set(requested)
    selected: list[BatchStructureItem] = []
    for item in items:
        if requested_ids.intersection((item.item_id, item.candidate_id)):
            selected.append(item)
    found = {item.item_id for item in selected} | {item.candidate_id for item in selected}
    missing = [value for value in requested if value not in found]
    if missing:
        raise BatchOptimizeInputError(
            f"BatchOptimize selection contains unknown item id(s): {', '.join(missing)}"
        )
    return selected


def run_batch_optimize(
    items_source: BatchItemsSource,
    profile: str = "opt_freq",
    *,
    output_dir: str | Path = "./batch_optimize_output",
    config: Mapping[str, JsonValue] | None = None,
    charge: int = 0,
    multiplicity: int = 1,
    select: BatchSelection = None,
    methods: BatchMethodOptions | None = None,
    layout_mode: BatchLayoutMode = "batch",
    progress_reporter: ProgressReporter | None = None,
) -> WorkflowResult:
    """Run BatchOptimize for structures loaded from an artifact or input file.

    ``single_flat`` is used by the scheduler when it fans a multi-structure
    UI submission out into independent one-structure tasks.  A direct
    multi-item CLI invocation keeps the isolated ``batch/<item_id>`` layout.
    """
    profile_key = profile.strip().lower()
    stages = _PROFILE_STAGES.get(profile_key)
    if stages is None:
        known = ", ".join(_PROFILE_STAGES)
        raise BatchOptimizeInputError(
            f"Unknown BatchOptimize profile {profile!r}; expected {known}"
        )

    items = _selected_items(_load_items(items_source), select)
    if not items:
        raise BatchOptimizeInputError("BatchOptimize requires at least one selected structure")

    output_root = Path(output_dir).expanduser()
    engine = BatchOptimizeEngine(
        config=dict(config) if config is not None else None,
        work_root=output_root / "WORK",
        result_root=output_root / "RESULT",
        progress_reporter=progress_reporter,
    )
    outcome = engine.run(
        items,
        profile=profile_key,
        charge=charge,
        multiplicity=multiplicity,
        workflow="BatchOptimize",
        methods=methods,
        layout_mode=layout_mode,
        progress_reporter=progress_reporter,
    )
    errors = outcome.errors
    return WorkflowResult(
        status="failed" if errors else "completed",
        stages_completed=list(stages),
        error="; ".join(errors) if errors else None,
        metadata={
            "output_dir": str(output_root),
            "profile": profile_key,
            "items": [item.to_dict() for item in outcome.items],
            "manifest_path": str(output_root / "RESULT" / "result_manifest.json"),
        },
    )


__all__ = ["BatchOptimizeInputError", "run_batch_optimize"]
