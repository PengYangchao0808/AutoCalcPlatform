"""BatchOptimizeEngine — multi-item batch optimization with profile-driven steps.

This engine is the mechanism-free replacement for ``mechanism/batch_confirm.py``
and the item-orchestration part of ``mechanism/providers/native_refinement.py``.

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

The engine has no dependency on the mechanism orchestrator layer (Wave 8
deletes the old mechanism modules).  Product names follow the canonical
``RESULT/structures/<item_id>__TAG_<TS|INT>__optimized.xyz`` convention
parsed by ``structure_sources.py``.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from acp.backends.base import QCResult
from acp.calculations.batch._items import (
    BatchCalculationItem,
    BatchStructureItem,
    item_cache_key,
)
from acp.calculations.batch._manifest import BatchCalculationManifest
from acp.calculations.batch._tag import build_tag_title, parse_tag_comment
from acp.calculations.contracts import (
    CalculationRequest,
    CalculationResult,
    JsonValue,
    OptimizationMode,
    OptimizationSpec,
    StructureArtifact,
    StructureRole,
    StepKind,
)
from acp.calculations.primitives.frequency import run_frequency
from acp.calculations.primitives.optimize import FAILURE_EXIT, run_optimize
from acp.calculations.primitives.singlepoint import run_singlepoint
from acp.calculations.primitives.thermochemistry import ThermochemistryCalculator
from acp.storage.manifest import ProductKind, ResultManifest

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_STRUCTURES_SUBDIR",
    "BatchOptimizeEngine",
    "BatchRunOutcome",
    "TERMINAL_ITEM_STATUSES",
]

BATCH_STRUCTURES_SUBDIR = "structures"
TERMINAL_ITEM_STATUSES = frozenset({"completed", "failed", "skipped"})

# ── profile → ordered step kinds ─────────────────────────────────────────
_PROFILE_STEPS: dict[str, tuple[StepKind, ...]] = {
    "opt_only": (StepKind.OPTIMIZE,),
    "opt_freq": (StepKind.OPTIMIZE, StepKind.FREQUENCY),
    "opt_freq_sp": (StepKind.OPTIMIZE, StepKind.FREQUENCY, StepKind.SINGLEPOINT),
    "opt_freq_sp_thermo": (
        StepKind.OPTIMIZE,
        StepKind.FREQUENCY,
        StepKind.SINGLEPOINT,
        StepKind.THERMOCHEMISTRY,
    ),
}


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
    """Multi-item batch optimization engine (profile-driven, mechanism-free).

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
    """

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        backend_factory: Callable[..., object] | None = None,
        work_root: Path | None = None,
        result_root: Path | None = None,
    ) -> None:
        self._config = config
        self._backend_factory = backend_factory
        self._work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        self._result_root = (
            Path(result_root) if result_root is not None else self._work_root / "RESULT"
        )

    @property
    def batch_root(self) -> Path:
        """Per-item work dirs live under ``WORK/03_OPT/batch``."""
        return self._work_root / "03_OPT" / "batch"

    @property
    def task_root(self) -> Path:
        """Task root is one level above ``WORK/``."""
        return self._work_root.parent

    # ── public entry point ───────────────────────────────────────────────

    def run(
        self,
        items: list[BatchStructureItem],
        *,
        profile: str,
        charge: int = 0,
        multiplicity: int = 1,
        workflow: str = "BatchOptimize",
    ) -> BatchRunOutcome:
        """Execute (or resume) the batch for *items*.

        Args:
            items: Input structures with resolved TAGs.
            profile: One of ``opt_only``, ``opt_freq``, ``opt_freq_sp``,
                ``opt_freq_sp_thermo``.
            charge: Job-level charge default (item-level values win).
            multiplicity: Job-level multiplicity default.
            workflow: Workflow label persisted in the batch manifest.

        Returns:
            The aggregated outcome.
        """
        if not items:
            raise ValueError("Batch run requires at least one structure item")
        if profile not in _PROFILE_STEPS:
            raise ValueError(f"unknown batch profile: {profile!r}")
        steps = _PROFILE_STEPS[profile]

        previous = self._load_previous_manifest(profile)
        prev_by_key = previous.by_cache_key() if previous is not None else {}

        records: list[BatchCalculationItem] = []
        to_run: list[tuple[BatchStructureItem, BatchCalculationItem]] = []
        carried: list[BatchCalculationItem] = []

        for item in items:
            record = BatchCalculationItem.from_item(item, charge, multiplicity)
            record.cache_key = item_cache_key(item, profile)

            item_dir = self.batch_root / item.item_id
            item_dir.mkdir(parents=True, exist_ok=True)
            input_path = self._materialize_item_input(item, item_dir)
            record.input_xyz = _rel_to(self.task_root, input_path)
            record.work_dir = _rel_to(self.task_root, item_dir)

            prev_record = prev_by_key.get(record.cache_key)
            if (
                prev_record is not None
                and prev_record.status == "completed"
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
                to_run.append((item, record))
            records.append(record)

        for item, record in to_run:
            try:
                self._process_item(item, record, steps, charge, multiplicity)
            except Exception as exc:
                record.status = "failed"
                record.error = str(exc) or type(exc).__name__
                logger.warning("Batch item %s failed: %s", item.item_id, exc)

        manifest = BatchCalculationManifest(
            profile=profile,
            items=records,
            workflow=workflow,
            created_at=(
                previous.created_at
                if previous is not None and previous.created_at
                else _utc_now_iso()
            ),
            updated_at=_utc_now_iso(),
        )
        self._materialize_result_products(manifest)
        self._write_batch_manifest(manifest)

        logger.info(
            "Batch %s: %d items (%d executed, %d carried, %d failed)",
            profile,
            len(records),
            len(to_run),
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
    ) -> None:
        """Run all profile steps for one item.  Raises on failure."""
        item_dir = self.batch_root / item.item_id
        input_path = item_dir / "input.xyz"
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
            step_dir = item_dir / step_kind.value
            step_dir.mkdir(parents=True, exist_ok=True)

            if step_kind is StepKind.OPTIMIZE:
                req = self._build_opt_request(
                    input_path, is_ts, item_charge, item_multiplicity, step_dir, opt_kwargs,
                )
                current_result = run_optimize(req)
                if current_result.status == "failed":
                    raise RuntimeError(
                        f"optimization failed for {item.item_id}: "
                        + "; ".join(current_result.errors)
                    )
                if not current_symbols and current_result.coords is not None:
                    current_symbols = self._read_symbols_from_xyz(input_path, len(current_result.coords))
                if current_result.coords is not None:
                    optimized_coords = [[float(v) for v in row] for row in current_result.coords]

            elif step_kind is StepKind.FREQUENCY:
                if current_result is None or current_result.coords is None:
                    raise RuntimeError(f"no optimized geometry for frequency: {item.item_id}")
                req = self._build_freq_request(
                    current_result, item, item_charge, item_multiplicity, step_dir, current_symbols,
                )
                current_result = run_frequency(req)
                if current_result.status == "failed":
                    raise RuntimeError(
                        f"frequency failed for {item.item_id}: "
                        + "; ".join(current_result.errors)
                    )
                frequency_log_path = self._extract_freq_log(current_result)
                record.frequency = {
                    "frequencies": current_result.frequencies,
                    "status": "completed",
                }
                if is_ts:
                    valid, msg = _ts_frequency_judgment(current_result.frequencies)
                    if not valid:
                        raise RuntimeError(f"TS frequency judgment failed for {item.item_id}: {msg}")

            elif step_kind is StepKind.SINGLEPOINT:
                if current_result is None or current_result.coords is None:
                    raise RuntimeError(f"no geometry for single-point: {item.item_id}")
                req = self._build_sp_request(
                    current_result, item, item_charge, item_multiplicity, step_dir, current_symbols,
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
                    raise RuntimeError(
                        f"thermochemistry requires freq + sp for {item.item_id}"
                    )
                current_result = ThermochemistryCalculator(
                    config=self._config,
                    output_dir=step_dir,
                ).compute(
                    freq_log_path=frequency_log_path,
                    sp_energy_hartree=sp_energy,
                    temperature=298.15,
                    pressure=1.0,
                    standard_state="1atm",
                )
                record.thermochemistry = {
                    "status": current_result.status,
                    **(current_result.metadata if current_result.metadata else {}),
                }

        if optimized_coords is not None:
            optimized_path = item_dir / "optimized.xyz"
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
    ) -> CalculationRequest:
        role = StructureRole.TRANSITION_STATE if is_ts else StructureRole.MINIMUM
        resources: dict[str, JsonValue] = {
            "output_dir": str(output_dir),
            "charge": charge,
            "multiplicity": multiplicity,
            **opt_kwargs,
        }
        return CalculationRequest(
            input_artifact=StructureArtifact(path=input_path, role=role),
            method="",
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
    ) -> CalculationRequest:
        return CalculationRequest(
            input_artifact=StructureArtifact(
                path=self.batch_root / item.item_id / "input.xyz",
                role=StructureRole.TRANSITION_STATE if item.tag == "TS" else StructureRole.MINIMUM,
            ),
            method="",
            resources={
                "output_dir": str(output_dir),
                "charge": charge,
                "multiplicity": multiplicity,
                "coordinates": [[float(v) for v in row] for row in (opt_result.coords or [])],
                "symbols": symbols,
            },
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
    ) -> CalculationRequest:
        return CalculationRequest(
            input_artifact=StructureArtifact(
                path=self.batch_root / item.item_id / "input.xyz",
                role=StructureRole.TRANSITION_STATE if item.tag == "TS" else StructureRole.MINIMUM,
            ),
            method="",
            resources={
                "output_dir": str(output_dir),
                "charge": charge,
                "multiplicity": multiplicity,
                "coordinates": [[float(v) for v in row] for row in (result.coords or [])],
                "symbols": symbols,
            },
            workflow="BatchOptimize",
        )

    # ── item input materialization ───────────────────────────────────────

    def _materialize_item_input(self, item: BatchStructureItem, item_dir: Path) -> Path:
        """Write the TAG-annotated input geometry under the item's dir."""
        input_path = item_dir / "input.xyz"
        xyz = item.xyz
        lines = xyz.splitlines()
        tag_info = parse_tag_comment(lines[1] if len(lines) > 1 else "")
        if tag_info["tag"] is None:
            xyz = _rewrite_xyz_comment(
                xyz,
                build_tag_title(
                    item.tag, candidate_id=item.candidate_id, source=item.source_type,
                ),
            )
        input_path.write_text(xyz if xyz.endswith("\n") else xyz + "\n", encoding="utf-8")
        return input_path

    # ── result products ──────────────────────────────────────────────────

    def _materialize_result_products(self, manifest: BatchCalculationManifest) -> None:
        """Copy optimized geometries to ``RESULT/structures/`` + register products."""
        structures_dir = self._result_root / BATCH_STRUCTURES_SUBDIR
        completed = [i for i in manifest.items if i.status in {"completed", "skipped"}]
        if not completed:
            return
        structures_dir.mkdir(parents=True, exist_ok=True)

        result_manifest = self._read_result_manifest()
        for item in completed:
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
                shutil.copy2(source, target)
            title = build_tag_title(
                item.tag,
                candidate_id=item.candidate_id,
                source=f"batch-{manifest.profile}",
            )
            target.write_text(
                _rewrite_xyz_comment(target.read_text(encoding="utf-8"), title),
                encoding="utf-8",
            )
            item.optimized_xyz = target.relative_to(self._result_root).as_posix()
            result_manifest.add_product(
                f"batch_{item.item_id}",
                f"{item.name} ({item.tag}, {manifest.profile})",
                item.optimized_xyz,
                ProductKind.STRUCTURE,
            )
        result_manifest.write(self._result_root)

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

    @property
    def batch_manifest_path(self) -> Path:
        """Path to the batch items manifest used for resume."""
        return self._result_root / "batch_items.json"

    def _write_batch_manifest(self, manifest: BatchCalculationManifest) -> None:
        """Persist the batch manifest for resume/cache."""
        manifest.write(self.batch_manifest_path)

    def _load_previous_manifest(self, profile: str) -> BatchCalculationManifest | None:
        """Load the prior batch manifest when it matches *profile*."""
        manifest_path = self.batch_manifest_path
        if not manifest_path.is_file():
            return None
        previous = BatchCalculationManifest.read(manifest_path)
        if previous is None:
            return None
        if previous.profile and previous.profile != profile:
            logger.info(
                "Previous batch manifest is %s — not reusable for %s; full re-run",
                previous.profile,
                profile,
            )
            return None
        return previous

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _read_symbols_from_xyz(path: Path, expected_count: int) -> list[str]:
        """Read element symbols from an XYZ file."""
        try:
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            if len(lines) < 2:
                return ["X"] * expected_count
            symbols = []
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
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _extract_freq_log(result: CalculationResult) -> Path | None:
        for artifact in reversed(result.artifacts):
            if artifact.type in {"frequency_log", "log"}:
                return artifact.path
        return None
