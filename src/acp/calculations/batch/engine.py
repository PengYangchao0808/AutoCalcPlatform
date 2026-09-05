"""BatchOptimizeEngine — profile-driven optimization for one or more structures.

**Layering decision**: the engine calls calculation primitives directly
(``run_optimize``, ``run_frequency``, ``run_singlepoint``,
``ThermochemistryCalculator``) rather than delegating per-item plans to
:class:`~acp.calculations.executor.CalculationPlanExecutor`.  Rationale:

1. The executor is designed for single-item plan execution with step-level
   checkpoint/resume — it creates ``WORK/`` + ``RESULT/`` and writes a
   ``result_manifest.json`` per invocation.
2. The batch engine needs item-level isolation (one item failure does NOT
   abort other items).
3. The batch engine needs TS-specific logic (``transition_state_opt``,
   imaginary-frequency judgment).
4. Item failure isolation and per-item cache-key skip are batch-level
   concerns that don't fit the executor's per-step checkpoint model.

The engine has no dependency on retired workflow orchestrators. Product names
follow the canonical
``RESULT/structures/<item_id>__TAG_<TS|INT>__optimized.xyz`` convention
parsed by ``structure_sources.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal, TypeAlias

from acp.api.stage_labels import stage_label
from acp.calculations.batch._items import (
    BatchCalculationItem,
    BatchStructureItem,
    item_cache_key,
)
from acp.calculations.batch._items import (
    JsonValue as BatchJsonValue,
)
from acp.calculations.batch._manifest import BatchCalculationManifest
from acp.calculations.batch._tag import build_tag_title, parse_tag_comment
from acp.calculations.checkpoint import load_checkpoint, write_checkpoint
from acp.calculations.contracts import (
    CalculationRequest,
    CalculationResult,
    Checkpoint,
    JsonValue,
    StepKind,
    StructureArtifact,
    StructureRole,
)
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import run_optimize
from acp.calculations.primitives.singlepoint import run_singlepoint
from acp.calculations.primitives.thermochemistry import ThermochemistryCalculator
from acp.calculations.progress import LiveMetric, ProgressReporter
from acp.storage.manifest import ProductKind, ResultManifest

from .options import BatchMethodOptions
from .profiles import BATCH_PROFILE_STEPS

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_STRUCTURES_SUBDIR",
    "BatchLayoutMode",
    "BatchOptimizeEngine",
    "BatchRunOutcome",
    "TERMINAL_ITEM_STATUSES",
    "batch_stage_names",
]

BATCH_STRUCTURES_SUBDIR = "structures"
TERMINAL_ITEM_STATUSES = frozenset({"completed", "failed", "skipped"})
_BATCH_CHECKPOINT_METADATA_KEY: Final = "__batch__"
BatchLayoutMode: TypeAlias = Literal["batch", "single_flat"]
_BATCH_LAYOUT_MODES: Final = frozenset({"batch", "single_flat"})
_SINGLE_ITEM_STAGE_DIRS: Final[dict[StepKind, str]] = {
    StepKind.OPTIMIZE: "03_OPT",
    StepKind.FREQUENCY: "04_FREQ",
    StepKind.SINGLEPOINT: "05_SP",
    StepKind.THERMOCHEMISTRY: "06_THERMO",
}
_STEP_STAGE_KEYS: Final[dict[StepKind, str]] = {
    StepKind.OPTIMIZE: "optimize",
    StepKind.FREQUENCY: "frequency",
    StepKind.SINGLEPOINT: "single_point",
    StepKind.THERMOCHEMISTRY: "thermochemistry",
    StepKind.SCAN: "scan",
}

# ── profile → ordered step kinds ─────────────────────────────────────────
# Kept as a private import alias for callers that used the old test seam.
_PROFILE_STEPS = BATCH_PROFILE_STEPS


def batch_stage_names(profile: str) -> list[str]:
    """Return canonical progress stage keys for a BatchOptimize profile."""
    return [_STEP_STAGE_KEYS[step] for step in _PROFILE_STEPS[profile]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel_to(task_root: Path, path: Path) -> str:
    """Return a POSIX path relative to *task_root*."""
    try:
        return path.resolve().relative_to(task_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _rewrite_xyz_comment(xyz: str, comment: str) -> str:
    """Replace the comment line (line 2) of an XYZ string."""
    lines = xyz.strip().splitlines()
    if not lines:
        return xyz
    try:
        count = int(lines[0].strip())
    except ValueError:
        return xyz
    if len(lines) < count + 1:
        return xyz
    return "\n".join([lines[0], comment, *lines[2 : count + 2]]) + "\n"


def _batch_plan_fingerprint(
    items: list[BatchStructureItem],
    profile: str,
    methods: BatchMethodOptions | None = None,
    layout_mode: BatchLayoutMode = "batch",
) -> str:
    """Return a stable fingerprint for the batch profile and ordered inputs."""
    resolved_methods = methods or BatchMethodOptions()
    item_signature = [
        {
            "item_id": item.item_id,
            "cache_key": item_cache_key(item, profile, resolved_methods.cache_key),
        }
        for item in items
    ]
    payload_data: dict[str, object] = {
        "profile": profile,
        "methods": resolved_methods.cache_key,
        "items": item_signature,
    }
    # Keep the historical fingerprint byte-for-byte stable for the default
    # multi-item layout.  Only the new flat mode needs a distinct checkpoint
    # namespace so it cannot accidentally reuse nested-path records.
    if layout_mode != "batch":
        payload_data["layout_mode"] = layout_mode
    payload = json.dumps(payload_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_layout_mode(value: str) -> BatchLayoutMode:
    """Validate the on-disk layout selected for this BatchOptimize run."""
    normalized = str(value or "batch").strip().lower().replace("-", "_")
    if normalized not in _BATCH_LAYOUT_MODES:
        allowed = ", ".join(sorted(_BATCH_LAYOUT_MODES))
        raise ValueError(f"unknown BatchOptimize layout mode {value!r}; expected {allowed}")
    return normalized  # type: ignore[return-value]


def _to_checkpoint_json(value: BatchJsonValue) -> JsonValue:
    """Convert the batch model's recursive JSON alias to the checkpoint alias."""
    if isinstance(value, Mapping):
        return {key: _to_checkpoint_json(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_to_checkpoint_json(nested) for nested in value]
    return value


def _to_batch_json(value: JsonValue) -> BatchJsonValue:
    """Convert checkpoint JSON back to the batch model's recursive alias."""
    if isinstance(value, dict):
        return {key: _to_batch_json(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_to_batch_json(nested) for nested in value]
    return value


def _json_coordinates(coords: Sequence[Sequence[float]] | None) -> list[JsonValue]:
    """Build a checkpoint-compatible JSON value for coordinate rows."""
    coordinates: list[JsonValue] = []
    for row in coords or ():
        values: list[JsonValue] = []
        for value in row:
            values.append(float(value))
        coordinates.append(values)
    return coordinates


def _json_text_list(values: Sequence[str]) -> list[JsonValue]:
    """Build a checkpoint-compatible JSON value for element symbols."""
    result: list[JsonValue] = []
    for value in values:
        result.append(value)
    return result


# ── outcome ──────────────────────────────────────────────────────────────


@dataclass
class BatchRunOutcome:
    """Aggregated result of one batch run (executed + carried items)."""

    profile: str
    manifest: BatchCalculationManifest
    carried_items: list[BatchCalculationItem] = field(default_factory=list)

    @property
    def items(self) -> list[BatchCalculationItem]:
        return self.manifest.items

    @property
    def errors(self) -> list[str]:
        return [item.error for item in self.items if item.error]


@dataclass(frozen=True, slots=True)
class _BatchProgress:
    reporter: ProgressReporter
    item_number: int
    item_total: int


# ── TS imaginary-frequency judgment ──────────────────────────────────────


def _count_significant_imaginary(frequencies: list[float], cutoff: float = -50.0) -> int:
    """Count frequencies at or below *cutoff* (cm⁻¹)."""
    return sum(1 for f in frequencies if float(f) <= cutoff)


def _ts_frequency_judgment(
    frequencies: list[float],
    *,
    cutoff: float = -50.0,
) -> tuple[bool, str]:
    """Validate transition-state frequency signature.

    Returns ``(valid, message)`` — valid when exactly one significant
    imaginary frequency exists.
    """
    count = _count_significant_imaginary(frequencies, cutoff)
    if count > 1:
        return False, f"higher_order_saddle ({count} significant imaginary frequencies)"
    if count == 0:
        return False, "ts_no_imaginary (no frequency <= -50 cm⁻¹)"
    return True, ""


# ── engine ───────────────────────────────────────────────────────────────


class BatchOptimizeEngine:
    """Profile-driven, mechanism-free BatchOptimize calculation engine.

    Profiles:
        ``opt_only``          — optimize only
        ``opt_freq``          — optimize + frequency
        ``opt_freq_sp``       — optimize + frequency + single-point
        ``opt_freq_sp_thermo`` — optimize + frequency + single-point + thermochemistry

    TS items use ``transition_state_opt`` and include an imaginary-frequency
    judgment (≤ -50 cm⁻¹).  INT items use ordinary ``optimize``.

    Item failure isolation: a failed item is recorded as ``"failed"`` in
    ``items_state`` but does NOT abort other items.  On re-run, items whose
    ``item_cache_key`` matches a previously ``"completed"`` record are skipped.

    ``layout_mode="batch"`` keeps one work directory per item for a direct
    multi-item CLI invocation.  ``layout_mode="single_flat"`` is the scheduler
    adapter for a one-structure task and writes directly to the canonical
    ``WORK/<stage>`` directories.
    """

    def __init__(
        self,
        *,
        config: Mapping[str, JsonValue] | None = None,
        backend_factory: Callable[..., object] | None = None,
        work_root: Path | None = None,
        result_root: Path | None = None,
        methods: BatchMethodOptions | None = None,
        progress_reporter: ProgressReporter | None = None,
    ) -> None:
        self._config: Mapping[str, JsonValue] | None = config
        self._backend_factory: Callable[..., object] | None = backend_factory
        self._work_root: Path = (
            Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        )
        self._result_root: Path = (
            Path(result_root) if result_root is not None else self._work_root / "RESULT"
        )
        self._active_methods: BatchMethodOptions = methods or BatchMethodOptions()
        self._active_layout_mode: BatchLayoutMode = "batch"
        self._progress_reporter: ProgressReporter | None = progress_reporter

    @property
    def batch_root(self) -> Path:
        """Per-item work dirs for a real multi-item CLI batch."""
        return self._work_root / "03_OPT" / "batch"

    @property
    def task_root(self) -> Path:
        """Task root is one level above ``WORK/``."""
        return self._work_root.parent

    def _item_work_dir(self, item: BatchStructureItem) -> Path:
        """Return the work root that owns *item* in the active layout."""
        if self._active_layout_mode == "single_flat":
            return self._work_root
        return self.batch_root / item.item_id

    def _item_input_path(self, item: BatchStructureItem) -> Path:
        """Return the input path for *item* in the active layout."""
        if self._active_layout_mode == "single_flat":
            return self.task_root / "input.xyz"
        return self._item_work_dir(item) / "input.xyz"

    def _step_dir(self, item: BatchStructureItem, step_kind: StepKind) -> Path:
        """Return the canonical output directory for one item step."""
        if self._active_layout_mode == "single_flat":
            return self._work_root / _SINGLE_ITEM_STAGE_DIRS[step_kind]
        return self._item_work_dir(item) / step_kind.value

    # ── public entry point ───────────────────────────────────────────────

    def run(
        self,
        items: list[BatchStructureItem],
        *,
        profile: str,
        charge: int = 0,
        multiplicity: int = 1,
        workflow: str = "BatchOptimize",
        methods: BatchMethodOptions | None = None,
        layout_mode: BatchLayoutMode = "batch",
        progress_reporter: ProgressReporter | None = None,
    ) -> BatchRunOutcome:
        """Execute (or resume) the batch for *items*.

        Args:
            items: Input structures with resolved TAGs.
            profile: One of ``opt_only``, ``opt_freq``, ``opt_freq_sp``,
                ``opt_freq_sp_thermo``.
            charge: Job-level charge default (item-level values win).
            multiplicity: Job-level multiplicity default.
            workflow: Workflow label persisted in the checkpoint and result manifest.
            methods: Optional role-specific method and basis overrides.
            layout_mode: ``batch`` keeps per-item directories for a real multi-item
                CLI run; ``single_flat`` matches the scheduler's one-structure
                task layout and requires exactly one item.
            progress_reporter: Optional state reporter for per-item stage progress.

        Returns:
            The aggregated outcome.
        """
        active_progress_reporter = progress_reporter or self._progress_reporter
        if profile not in _PROFILE_STEPS:
            raise ValueError(f"unknown batch profile: {profile!r}")
        if not items:
            if active_progress_reporter is None:
                raise ValueError("Batch run requires at least one structure item")
            timestamp = _utc_now_iso()
            return BatchRunOutcome(
                profile=profile,
                manifest=BatchCalculationManifest(
                    profile=profile,
                    workflow=workflow,
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
            )
        resolved_layout = _normalize_layout_mode(layout_mode)
        if resolved_layout == "single_flat" and len(items) != 1:
            raise ValueError("single_flat BatchOptimize layout requires exactly one item")
        steps = _PROFILE_STEPS[profile]
        resolved_methods = methods or self._active_methods
        self._active_methods = resolved_methods
        self._active_layout_mode = resolved_layout

        fingerprint = _batch_plan_fingerprint(items, profile, resolved_methods, resolved_layout)
        runtime_dir = self._work_root / "00_RUNTIME"
        checkpoint = load_checkpoint(runtime_dir, fingerprint)
        previous_by_id = self._checkpoint_items(checkpoint)
        checkpoint_items_state: dict[str, JsonValue] = (
            dict(checkpoint.items_state) if checkpoint is not None else {}
        )
        attempts = checkpoint.attempts + 1 if checkpoint is not None else 0

        records: list[BatchCalculationItem] = []
        carried: list[BatchCalculationItem] = []
        executed_count = 0

        for index, item in enumerate(items):
            record = BatchCalculationItem.from_item(item, charge, multiplicity)
            record.cache_key = item_cache_key(item, profile, resolved_methods.cache_key)

            item_dir = self._item_work_dir(item)
            item_dir.mkdir(parents=True, exist_ok=True)
            input_path = self._materialize_item_input(item, self._item_input_path(item))
            record.input_xyz = _rel_to(self.task_root, input_path)
            record.work_dir = _rel_to(self.task_root, item_dir)

            prev_record = previous_by_id.get(item.item_id)
            if (
                prev_record is not None
                and prev_record.cache_key == record.cache_key
                and prev_record.status in {"completed", "skipped"}
                and prev_record.optimized_xyz
            ):
                record.status = "skipped"
                record.optimized_xyz = prev_record.optimized_xyz
                record.frequency = dict(prev_record.frequency)
                record.single_point = dict(prev_record.single_point)
                record.thermochemistry = dict(prev_record.thermochemistry)
                carried.append(record)
                logger.info("Batch item %s skipped (cache hit)", item.item_id)
            else:
                executed_count += 1
                progress = (
                    _BatchProgress(
                        reporter=active_progress_reporter,
                        item_number=index + 1,
                        item_total=len(items),
                    )
                    if active_progress_reporter is not None
                    else None
                )
                try:
                    if progress is None:
                        self._process_item(item, record, steps, charge, multiplicity)
                    else:
                        self._process_item(
                            item,
                            record,
                            steps,
                            charge,
                            multiplicity,
                            progress=progress,
                        )
                except Exception as exc:
                    record.status = "failed"
                    record.error = str(exc) or type(exc).__name__
                    if active_progress_reporter is not None:
                        current_stage = active_progress_reporter.current_stage
                        if current_stage is not None:
                            active_progress_reporter.fail_stage(current_stage, record.error)
                    logger.warning("Batch item %s failed: %s", item.item_id, exc)

            records.append(record)
            checkpoint_items_state[item.item_id] = _to_checkpoint_json(record.to_dict())
            next_index = index + 1
            checkpoint_items_state[_BATCH_CHECKPOINT_METADATA_KEY] = {
                "profile": profile,
                "next_item_index": next_index,
                "next_item_id": items[next_index].item_id if next_index < len(items) else "",
                "last_item_id": item.item_id,
            }
            write_checkpoint(
                runtime_dir,
                Checkpoint(
                    task_id="batch",
                    workflow=workflow,
                    plan_fingerprint=fingerprint,
                    step_states=[],
                    items_state=checkpoint_items_state,
                    attempts=attempts,
                ),
            )

        manifest = BatchCalculationManifest(
            profile=profile,
            items=records,
            workflow=workflow,
            created_at=_utc_now_iso(),
            updated_at=_utc_now_iso(),
        )
        self._materialize_result_products(manifest)

        logger.info(
            "Batch %s: %d items (%d executed, %d carried, %d failed)",
            profile,
            len(records),
            executed_count,
            len(carried),
            sum(1 for r in records if r.status == "failed"),
        )
        return BatchRunOutcome(profile=profile, manifest=manifest, carried_items=carried)

    # ── per-item processing ──────────────────────────────────────────────

    def _process_item(
        self,
        item: BatchStructureItem,
        record: BatchCalculationItem,
        steps: tuple[StepKind, ...],
        charge: int,
        multiplicity: int,
        methods: BatchMethodOptions | None = None,
        *,
        progress: _BatchProgress | None = None,
    ) -> None:
        """Run all profile steps for one item, serially.  Raises on failure.

        Items are the outer loop in :meth:`run`, so repeated profile stages are
        reported for each item.  ``current_stage`` therefore identifies the
        step of the item currently being processed, while the reporter's
        canonical stage order supplies the workflow position.
        """
        resolved_methods = methods or self._active_methods
        item_dir = self._item_work_dir(item)
        input_path = self._item_input_path(item)
        is_ts = item.tag == "TS"
        item_charge = item.resolved_charge(charge)
        item_multiplicity = item.resolved_multiplicity(multiplicity)

        opt_kwargs = self._optimization_kwargs(is_ts)
        current_result: CalculationResult | None = None
        current_symbols: list[str] = []
        frequency_log_path: Path | None = None
        sp_energy: float | None = None
        optimized_coords: list[list[float]] | None = None

        for step_kind in steps:
            if progress is not None:
                stage_key = _STEP_STAGE_KEYS[step_kind]
                progress.reporter.start_stage(stage_key)
                progress.reporter.set_live_metrics(
                    [
                        LiveMetric(
                            key="batch_item",
                            label_key="live.batch_item",
                            value=f"{progress.item_number} / {progress.item_total}",
                            kind="count",
                            priority=100,
                        ),
                        LiveMetric(
                            key="batch_step",
                            label_key="live.batch_step",
                            label=stage_label(stage_key),
                            value=stage_key,
                            kind="status",
                            priority=90,
                        ),
                    ]
                )
            step_dir = self._step_dir(item, step_kind)
            step_dir.mkdir(parents=True, exist_ok=True)

            if step_kind is StepKind.OPTIMIZE:
                req = self._build_opt_request(
                    input_path,
                    is_ts,
                    item_charge,
                    item_multiplicity,
                    step_dir,
                    opt_kwargs,
                    resolved_methods,
                    trajectory_item_id=item.item_id,
                )
                if progress is None:
                    current_result = run_optimize(req)
                else:
                    current_result = run_optimize(req, progress_reporter=progress.reporter)
                if current_result.status == "failed":
                    raise RuntimeError(
                        f"optimization failed for {item.item_id}: "
                        + "; ".join(current_result.errors)
                    )
                if not current_symbols and current_result.coords is not None:
                    current_symbols = self._read_symbols_from_xyz(
                        input_path, len(current_result.coords)
                    )
                if current_result.coords is not None:
                    optimized_coords = [[float(v) for v in row] for row in current_result.coords]

            elif step_kind is StepKind.FREQUENCY:
                if current_result is None or current_result.coords is None:
                    raise RuntimeError(f"no optimized geometry for frequency: {item.item_id}")
                req = self._build_freq_request(
                    current_result,
                    item,
                    item_charge,
                    item_multiplicity,
                    step_dir,
                    current_symbols,
                    resolved_methods,
                )
                current_result = run_frequency(req)
                if current_result.status == "failed":
                    raise RuntimeError(
                        f"frequency failed for {item.item_id}: " + "; ".join(current_result.errors)
                    )
                frequency_log_path = self._extract_freq_log(current_result)
                frequency_values: list[BatchJsonValue] = []
                for frequency in current_result.frequencies:
                    frequency_values.append(float(frequency))
                record.frequency.clear()
                record.frequency["frequencies"] = frequency_values
                record.frequency["status"] = "completed"
                if is_ts:
                    valid, msg = _ts_frequency_judgment(current_result.frequencies)
                    if not valid:
                        raise RuntimeError(
                            f"TS frequency judgment failed for {item.item_id}: {msg}"
                        )

            elif step_kind is StepKind.SINGLEPOINT:
                if current_result is None or current_result.coords is None:
                    raise RuntimeError(f"no geometry for single-point: {item.item_id}")
                req = self._build_sp_request(
                    current_result,
                    item,
                    item_charge,
                    item_multiplicity,
                    step_dir,
                    current_symbols,
                    resolved_methods,
                )
                current_result = run_singlepoint(req)
                if current_result.status == "failed":
                    raise RuntimeError(
                        f"single-point failed for {item.item_id}: "
                        + "; ".join(current_result.errors)
                    )
                sp_energy = current_result.energy
                record.single_point = {
                    "energy_hartree": sp_energy,
                    "status": "completed",
                }

            elif step_kind is StepKind.THERMOCHEMISTRY:
                if frequency_log_path is None or sp_energy is None:
                    raise RuntimeError(f"thermochemistry requires freq + sp for {item.item_id}")
                current_result = ThermochemistryCalculator(
                    config=self._config,
                    output_dir=step_dir,
                    runner_options={"scl_zpe": resolved_methods.scale_factor},
                ).compute(
                    freq_log_path=frequency_log_path,
                    sp_energy_hartree=sp_energy,
                    temperature=resolved_methods.temperature,
                    pressure=resolved_methods.pressure,
                    standard_state="1atm",
                )
                thermochemistry: dict[str, BatchJsonValue] = {
                    "status": current_result.status,
                }
                for key, value in current_result.metadata.items():
                    thermochemistry[key] = _to_batch_json(value)
                record.thermochemistry.clear()
                record.thermochemistry.update(thermochemistry)

            if progress is not None:
                progress.reporter.complete_stage(_STEP_STAGE_KEYS[step_kind])

        if optimized_coords is not None:
            optimized_path = (
                self._step_dir(item, StepKind.OPTIMIZE) / "optimized.xyz"
                if self._active_layout_mode == "single_flat"
                else item_dir / "optimized.xyz"
            )
            self._write_xyz(optimized_path, optimized_coords, current_symbols, item.item_id)
            record.optimized_xyz = _rel_to(self.task_root, optimized_path)

        record.status = "completed"

    # ── request builders ─────────────────────────────────────────────────

    def _optimization_kwargs(self, is_ts: bool) -> dict[str, JsonValue]:
        """Build optimization keyword arguments for a request.

        TS items use ``OptimizationMode.TRANSITION_STATE`` semantics:
        trust_radius, recalc_hess, initial_hessian.  INT items use plain
        optimization defaults.
        """
        if is_ts:
            return {
                "initial_hessian": "calculate",
                "recalc_hess": 5,
                "trust_radius": 0.3,
                "max_cycles": 200,
                "structure_kind": "ts",
            }
        return {
            "max_cycles": 200,
            "structure_kind": "minimum",
        }

    def _build_opt_request(
        self,
        input_path: Path,
        is_ts: bool,
        charge: int,
        multiplicity: int,
        output_dir: Path,
        opt_kwargs: dict[str, JsonValue],
        methods: BatchMethodOptions,
        *,
        trajectory_item_id: str = "",
    ) -> CalculationRequest:
        role = StructureRole.TRANSITION_STATE if is_ts else StructureRole.MINIMUM
        method, basis = methods.for_step(StepKind.OPTIMIZE, is_ts)
        resources: dict[str, JsonValue] = {
            "output_dir": str(output_dir),
            "trajectory_item_id": trajectory_item_id or input_path.parent.name,
            "charge": charge,
            "multiplicity": multiplicity,
            **opt_kwargs,
        }
        if basis:
            resources["basis"] = basis
        return CalculationRequest(
            input_artifact=StructureArtifact(path=input_path, role=role),
            method=method,
            resources=resources,
            workflow="BatchOptimize",
        )

    def _build_freq_request(
        self,
        opt_result: CalculationResult,
        item: BatchStructureItem,
        charge: int,
        multiplicity: int,
        output_dir: Path,
        symbols: list[str],
        methods: BatchMethodOptions,
    ) -> CalculationRequest:
        coordinates = _json_coordinates(opt_result.coords)
        symbols_json = _json_text_list(symbols)
        method, basis = methods.for_step(StepKind.FREQUENCY, item.tag == "TS")
        resources: dict[str, JsonValue] = {
            "output_dir": str(output_dir),
            "charge": charge,
            "multiplicity": multiplicity,
            "coordinates": coordinates,
            "symbols": symbols_json,
        }
        if basis:
            resources["basis"] = basis
        return CalculationRequest(
            input_artifact=StructureArtifact(
                path=self._item_input_path(item),
                role=StructureRole.TRANSITION_STATE if item.tag == "TS" else StructureRole.MINIMUM,
            ),
            method=method,
            resources=resources,
            workflow="BatchOptimize",
        )

    def _build_sp_request(
        self,
        result: CalculationResult,
        item: BatchStructureItem,
        charge: int,
        multiplicity: int,
        output_dir: Path,
        symbols: list[str],
        methods: BatchMethodOptions,
    ) -> CalculationRequest:
        coordinates = _json_coordinates(result.coords)
        symbols_json = _json_text_list(symbols)
        method, basis = methods.for_step(StepKind.SINGLEPOINT, item.tag == "TS")
        resources: dict[str, JsonValue] = {
            "output_dir": str(output_dir),
            "charge": charge,
            "multiplicity": multiplicity,
            "coordinates": coordinates,
            "symbols": symbols_json,
        }
        if basis:
            resources["basis"] = basis
        return CalculationRequest(
            input_artifact=StructureArtifact(
                path=self._item_input_path(item),
                role=StructureRole.TRANSITION_STATE if item.tag == "TS" else StructureRole.MINIMUM,
            ),
            method=method,
            resources=resources,
            workflow="BatchOptimize",
        )

    # ── item input materialization ───────────────────────────────────────

    def _materialize_item_input(self, item: BatchStructureItem, input_path: Path) -> Path:
        """Write the TAG-annotated input geometry at its canonical path."""
        input_path.parent.mkdir(parents=True, exist_ok=True)
        xyz = item.xyz
        lines = xyz.splitlines()
        tag_info = parse_tag_comment(lines[1] if len(lines) > 1 else "")
        if tag_info["tag"] is None:
            xyz = _rewrite_xyz_comment(
                xyz,
                build_tag_title(
                    item.tag,
                    candidate_id=item.candidate_id,
                    source=item.source_type,
                ),
            )
        _ = input_path.write_text(xyz if xyz.endswith("\n") else xyz + "\n", encoding="utf-8")
        return input_path

    # ── result products ──────────────────────────────────────────────────

    def _materialize_result_products(self, manifest: BatchCalculationManifest) -> None:
        """Copy optimized geometries/trajectories to ``RESULT/`` products."""
        structures_dir = self._result_root / BATCH_STRUCTURES_SUBDIR
        completed = [i for i in manifest.items if i.status in {"completed", "skipped"}]
        if not completed:
            return
        structures_dir.mkdir(parents=True, exist_ok=True)

        result_manifest = self._read_result_manifest()
        for item in completed:
            self._materialize_optimization_trajectory(item)
            source = self._resolve_output_geometry(item)
            if source is None:
                logger.warning(
                    "Batch item %s finished but optimized geometry missing: %s",
                    item.item_id,
                    item.optimized_xyz,
                )
                continue
            target = structures_dir / f"{item.item_id}__TAG_{item.tag}__optimized.xyz"
            if source.resolve() != target.resolve():
                _ = shutil.copy2(source, target)
            title = build_tag_title(
                item.tag,
                candidate_id=item.candidate_id,
                source=f"batch-{manifest.profile}",
            )
            _ = target.write_text(
                _rewrite_xyz_comment(target.read_text(encoding="utf-8"), title),
                encoding="utf-8",
            )
            item.optimized_xyz = target.relative_to(self._result_root).as_posix()
            _ = result_manifest.add_product(
                f"batch_{item.item_id}",
                f"{item.name} ({item.tag}, {manifest.profile})",
                item.optimized_xyz,
                ProductKind.STRUCTURE,
            )
        _ = result_manifest.write(self._result_root)

    def _materialize_optimization_trajectory(self, item: BatchCalculationItem) -> None:
        """Copy a live item trajectory into the stable RESULT tree.

        Trajectories are intentionally not added to the batch result manifest:
        that manifest's public product list is reserved for reusable
        structures.  The energy-graph projection discovers this canonical
        trajectory directory directly.
        """
        source_dir = self._step_dir(item, StepKind.OPTIMIZE)
        sources = [
            source_dir / "optimization_trajectory.json",
            *source_dir.glob("rescue_*/optimization_trajectory.json"),
        ]
        existing = [path for path in sources if path.is_file()]
        if not existing:
            return
        source = max(existing, key=lambda path: path.stat().st_mtime_ns)
        target_dir = self._result_root / "trajectories"
        if self._active_layout_mode != "single_flat":
            target_dir /= item.item_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "optimization.json"
        if source.resolve() != target.resolve():
            _ = shutil.copy2(source, target)
        cycles = source.parent / "cycles"
        if cycles.is_dir():
            _ = shutil.copytree(cycles, target_dir / "cycles", dirs_exist_ok=True)

    def _resolve_output_geometry(self, item: BatchCalculationItem) -> Path | None:
        """Resolve an item's optimized geometry."""
        if not item.optimized_xyz:
            return None
        raw = Path(item.optimized_xyz)
        if raw.is_absolute() and raw.is_file():
            return raw
        for probe in (self._result_root / item.optimized_xyz, self.task_root / item.optimized_xyz):
            if probe.is_file():
                return probe
        return None

    def _read_result_manifest(self) -> ResultManifest:
        path = self._result_root / "result_manifest.json"
        if path.is_file():
            try:
                return ResultManifest.read(self._result_root)
            except (OSError, ValueError):
                logger.warning("Unreadable result_manifest.json — rewriting it", exc_info=True)
        return ResultManifest(task_id="", workflow="BatchOptimize", status="completed")

    # ── checkpoint / cache ───────────────────────────────────────────────

    @staticmethod
    def _checkpoint_items(
        checkpoint: Checkpoint | None,
    ) -> dict[str, BatchCalculationItem]:
        """Extract item records from the batch checkpoint state."""
        if checkpoint is None:
            return {}

        previous: dict[str, BatchCalculationItem] = {}
        for item_id, state in checkpoint.items_state.items():
            if item_id == _BATCH_CHECKPOINT_METADATA_KEY or not isinstance(state, dict):
                continue
            try:
                record = BatchCalculationItem.from_dict(
                    {key: _to_batch_json(value) for key, value in state.items()}
                )
            except (TypeError, ValueError) as exc:
                logger.warning("Ignoring malformed batch checkpoint item %s: %s", item_id, exc)
                continue
            if record.item_id == item_id:
                previous[item_id] = record
        return previous

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _read_symbols_from_xyz(path: Path, expected_count: int) -> list[str]:
        """Read element symbols from an XYZ file."""
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) < 2:
                return ["X"] * expected_count
            symbols: list[str] = []
            for line in lines[2 : 2 + expected_count]:
                parts = line.split()
                symbols.append(parts[0] if parts else "X")
            return symbols if len(symbols) == expected_count else ["X"] * expected_count
        except (OSError, ValueError):
            return ["X"] * expected_count

    @staticmethod
    def _write_xyz(
        path: Path,
        coords: list[list[float]] | None,
        symbols: list[str] | None,
        title: str,
    ) -> None:
        """Write a minimal XYZ file."""
        if coords is None:
            return
        n = len(coords)
        sym = symbols or ["X"] * n
        lines = [str(n), title]
        for i, row in enumerate(coords):
            s = sym[i] if i < len(sym) else "X"
            lines.append(f"{s}  {row[0]:.10f}  {row[1]:.10f}  {row[2]:.10f}")
        _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _extract_freq_log(result: CalculationResult) -> Path | None:
        for artifact in reversed(result.artifacts):
            if artifact.type in {"frequency_log", "log"}:
                return artifact.path
        return None
