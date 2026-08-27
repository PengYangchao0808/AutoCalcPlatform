"""BatchConfirmEngine — one batch stationary-point engine behind S3/S4.

Lowconfirm (``profile="s3"``) and Highconfirm (``profile="s4"``) share the
scientific core (:class:`acp.mechanism.stages.confirm.ConfirmEngine` +
:class:`NativeRefinementProvider`); this layer adds the batch contract
(batch plan §7/§8):

* input items → per-item work dirs (``WORK/03_OPT/batch/<item_id>/``);
* per-item status records + persisted ``batch_calculation_manifest.json``;
* resume: items whose cache key is already ``completed`` are skipped and
  their outputs carried over;
* result products: optimized structures land under
  ``RESULT/structures/<item_id>__TAG_<TS|INT>__optimized.xyz`` and are
  registered in ``RESULT/result_manifest.json`` (``kind: "structure"``) +
  ``RESULT/result_summary.json`` so the task-result list can offer them.

The engine never re-implements QC logic — TS items run
``transition_state_opt`` semantics and INT items plain optimization inside
the shared provider, selected purely by the item TAG.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.confsearch.shared.provenance import utc_now_iso
from acp.storage.manifest import ResultManifest
from acp.workflows._helpers import write_result_summary

from .batch_models import (
    BatchCalculationItem,
    BatchCalculationManifest,
    BatchStructureItem,
    _rewrite_comment,
    build_tag_title,
    item_cache_key,
    parse_tag_comment,
)
from .models import ArtifactRef, Provenance, StationaryPointRequest
from .presets import FidelityProfile
from .stages.confirm import ConfirmEngine, ConfirmProfile, ConfirmRunOutcome, LowConfirmProfile

logger = logging.getLogger(__name__)

__all__ = [
    "BATCH_MANIFEST_NAME",
    "BatchConfirmEngine",
    "BatchRunOutcome",
]

BATCH_MANIFEST_NAME = "batch_calculation_manifest.json"
BATCH_STRUCTURES_SUBDIR = "structures"


@dataclass
class BatchRunOutcome:
    """Aggregated result of one batch run (executed + carried items)."""

    profile_level: str
    manifest: BatchCalculationManifest
    confirm: ConfirmRunOutcome | None = None
    carried_items: list[BatchCalculationItem] = field(default_factory=list)

    @property
    def items(self) -> list[BatchCalculationItem]:
        return self.manifest.items

    @property
    def errors(self) -> list[str]:
        return list(self.confirm.errors) if self.confirm is not None else []


def _rel_to(task_root: Path, path: Path) -> str:
    """Path relative to the task root (portable across local/remote runs)."""
    try:
        return path.resolve().relative_to(task_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


class BatchConfirmEngine:
    """Unified batch confirmation engine for Lowconfirm/Highconfirm."""

    def __init__(
        self,
        *,
        config: dict[str, Any] | None = None,
        work_root: Path | None = None,
        profile: ConfirmProfile | None = None,
        refinement_provider: Any | None = None,
        result_root: Path | None = None,
        resume: bool = True,
    ) -> None:
        self.config = config
        self.profile: ConfirmProfile = profile or LowConfirmProfile()
        self.work_root = Path(work_root) if work_root is not None else Path.cwd() / "acp_calc"
        self.result_root = (
            Path(result_root)
            if result_root is not None
            else self.work_root.parent.parent / "RESULT"
        )
        self.resume = resume
        self._engine = ConfirmEngine(
            config=config,
            work_root=self.work_root,
            profile=self.profile,
            refinement_provider=refinement_provider,
        )

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def batch_root(self) -> Path:
        """Per-item input dirs live under ``WORK/03_OPT/batch``."""
        return self.work_root / "batch"

    @property
    def manifest_path(self) -> Path:
        return self.result_root / "mechanism" / BATCH_MANIFEST_NAME

    @property
    def task_root(self) -> Path:
        return self.result_root.parent

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def run(
        self,
        items: list[BatchStructureItem],
        *,
        charge: int,
        multiplicity: int,
        coordinate_plan: Any | None = None,
        workflow: str = "",
    ) -> BatchRunOutcome:
        """Execute (or resume) the batch for *items*.

        Args:
            items: Input structures with resolved TAGs.
            charge: Job-level charge default (item-level values win).
            multiplicity: Job-level multiplicity default (item-level wins).
            coordinate_plan: Optional S2 reaction-coordinate plan forwarded
                to TS refinements.
            workflow: Workflow label persisted in the batch manifest.

        Returns:
            The aggregated outcome; ``confirm`` is ``None`` when every item
            was carried over from a previous completed run.
        """
        if not items:
            raise ValueError("Batch run requires at least one structure item")

        fidelity = self.profile.fidelity_profile()
        profile_key = self._profile_key(fidelity)
        previous = self._previous_manifest()
        prev_by_key = previous.by_cache_key() if previous is not None else {}

        records: list[BatchCalculationItem] = []
        to_run: list[tuple[BatchStructureItem, BatchCalculationItem]] = []
        request_ids = self._unique_request_ids(items)
        carried: list[BatchCalculationItem] = []

        for item in items:
            record = BatchCalculationItem.from_item(item, charge, multiplicity)
            record.cache_key = item_cache_key(item, profile_key)
            item_dir = self.batch_root / item.item_id
            input_path = self._materialize_item_input(item, item_dir)
            record.input_xyz = _rel_to(self.task_root, input_path)
            record.work_dir = _rel_to(self.task_root, item_dir)

            previous_record = prev_by_key.get(record.cache_key)
            if (
                previous_record is not None
                and previous_record.status == "completed"
                and previous_record.optimized_xyz
            ):
                record.status = "skipped"
                record.optimized_xyz = previous_record.optimized_xyz
                record.frequency = dict(previous_record.frequency)
                record.single_point = dict(previous_record.single_point)
                record.thermochemistry = dict(previous_record.thermochemistry)
                carried.append(record)
                records.append(record)
                logger.info(
                    "Batch item %s skipped (cache hit from previous completed run)",
                    item.item_id,
                )
                continue
            records.append(record)
            to_run.append((item, record))

        confirm_outcome: ConfirmRunOutcome | None = None
        if to_run:
            requests = [
                self._build_request(
                    item,
                    request_ids[item.item_id],
                    record.cache_key,
                    charge,
                    multiplicity,
                    coordinate_plan,
                )
                for item, record in to_run
            ]
            confirm_outcome = self._engine.confirm(requests)
            self._apply_confirm_records(confirm_outcome, to_run, request_ids)

        manifest = BatchCalculationManifest(
            profile_level=self.profile.level,
            items=records,
            workflow=workflow,
            created_at=(
                previous.created_at
                if previous is not None and previous.created_at
                else utc_now_iso()
            ),
            updated_at=utc_now_iso(),
        )
        self._materialize_result_products(manifest)
        manifest.write(self.manifest_path)

        logger.info(
            "Batch %s run: %d items (%d executed, %d carried) → %s",
            self.profile.level,
            len(records),
            len(to_run),
            len(carried),
            self.manifest_path,
        )
        return BatchRunOutcome(
            profile_level=self.profile.level,
            manifest=manifest,
            confirm=confirm_outcome,
            carried_items=carried,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _previous_manifest(self) -> BatchCalculationManifest | None:
        """Load the prior batch manifest when it matches this profile."""
        if not self.resume:
            return None
        previous = BatchCalculationManifest.read(self.manifest_path)
        if previous is None:
            return None
        if previous.profile_level and previous.profile_level != self.profile.level:
            logger.info(
                "Previous batch manifest is %s — not reusable for %s; full re-run",
                previous.profile_level,
                self.profile.level,
            )
            return None
        return previous

    def _profile_key(self, fidelity: FidelityProfile) -> str:
        return "|".join(
            str(part)
            for part in (
                self.profile.level,
                fidelity.ts_method,
                fidelity.ts_basis,
                fidelity.freq_method,
                fidelity.sp_method,
                fidelity.sp_basis,
                getattr(self.profile, "max_cycles", ""),
                getattr(self.profile, "run_irc", ""),
            )
        )

    def _unique_request_ids(self, items: list[BatchStructureItem]) -> dict[str, str]:
        """Map item_id → request id (candidate id, de-duplicated)."""
        assigned: dict[str, str] = {}
        used: set[str] = set()
        for item in items:
            base = item.candidate_id or item.item_id
            request_id = base
            suffix = 0
            while request_id in used:
                suffix += 1
                request_id = f"{base}__{suffix}"
            used.add(request_id)
            assigned[item.item_id] = request_id
        return assigned

    def _materialize_item_input(self, item: BatchStructureItem, item_dir: Path) -> Path:
        """Write the TAG-annotated input geometry under the item's dir."""
        item_dir.mkdir(parents=True, exist_ok=True)
        input_path = item_dir / "input.xyz"
        xyz = item.xyz
        lines = xyz.splitlines()
        tag_info = parse_tag_comment(lines[1] if len(lines) > 1 else "")
        if tag_info["tag"] is None:
            xyz = _rewrite_comment(
                xyz,
                build_tag_title(item.tag, candidate_id=item.candidate_id, source=item.source_type),
            )
        input_path.write_text(xyz if xyz.endswith("\n") else xyz + "\n", encoding="utf-8")
        return input_path

    def _build_request(
        self,
        item: BatchStructureItem,
        request_id: str,
        cache_key: str,
        charge: int,
        multiplicity: int,
        coordinate_plan: Any | None,
    ) -> StationaryPointRequest:
        """TAG → request kind/role: TS → transition_state, INT → minimum."""
        input_path = self.batch_root / item.item_id / "input.xyz"
        return StationaryPointRequest(
            id=request_id,
            role=item.role,  # type: ignore[arg-type]
            kind=item.kind,  # type: ignore[arg-type]
            input_geometry=ArtifactRef(
                path=str(input_path),
                sha256="",
                kind="batch_input_geometry",
            ),
            coordinate_plan=coordinate_plan,
            fallback_geometries=[],
            source_stage=self.profile.level.upper(),
            charge=item.resolved_charge(charge),
            multiplicity=item.resolved_multiplicity(multiplicity),
            atom_mapping=None,
            parent_state_id=None,
            route_id="route_001",
            ensemble_correction=None,
            provenance=Provenance(
                provider="acp-batchconfirm",
                provider_version="1.0",
                provider_commit=self.profile.level,
                strategy="batch-confirmation",
                strategy_version="1.0",
                profile_id=self.profile.level,
                schema_version="m0",
                input_signature=cache_key or item.item_id,
            ),
        )

    def _apply_confirm_records(
        self,
        outcome: ConfirmRunOutcome,
        to_run: list[tuple[BatchStructureItem, BatchCalculationItem]],
        request_ids: dict[str, str],
    ) -> None:
        by_request = {candidate.candidate_id: candidate for candidate in outcome.candidates}
        for item, record in to_run:
            request_id = request_ids[item.item_id]
            candidate = by_request.get(request_id)
            if candidate is None:
                record.status = "failed"
                record.error = "no refinement attempt recorded"
                continue
            record.frequency = dict(candidate.frequency)
            record.single_point = (
                {"energy_hartree": candidate.sp_energy_hartree}
                if candidate.sp_energy_hartree is not None
                else {}
            )
            if candidate.gibbs_hartree is not None:
                record.thermochemistry = {"gibbs_hartree": candidate.gibbs_hartree}
            if candidate.status == "confirmed" and candidate.optimized_xyz:
                record.status = "completed"
                record.optimized_xyz = _rel_to(self.task_root, Path(candidate.optimized_xyz))
            else:
                record.status = "failed"
                evidence = dict(candidate.evidence or {})
                record.error = str(evidence.get("error") or "refinement failed")

    def _materialize_result_products(self, manifest: BatchCalculationManifest) -> None:
        """Copy optimized geometries to ``RESULT/structures`` + register products.

        Freshly-completed items get their optimized geometry copied (with a
        TAG-normalized title) into ``RESULT/structures/`` and registered in
        ``result_manifest.json`` (kind ``structure``) plus the legacy
        ``result_summary.json`` pointer.          Carried items are re-registered so
        the result list stays complete after a resumed run.
        """
        structures_dir = self.result_root / BATCH_STRUCTURES_SUBDIR
        completed = [item for item in manifest.items if item.status in {"completed", "skipped"}]
        if not completed:
            return
        structures_dir.mkdir(parents=True, exist_ok=True)

        result_manifest = self._read_result_manifest()
        summary_products: list[dict[str, Any]] = []
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
                source=f"batch-{self.profile.level}",
            )
            target.write_text(_rewrite_comment(target.read_text(encoding="utf-8"), title))
            item.optimized_xyz = target.relative_to(self.result_root).as_posix()
            result_manifest.add_product(
                f"batch_{item.item_id}",
                f"{item.name} ({item.tag}, {self.profile.level})",
                item.optimized_xyz,
                "structure",
            )
            summary_products.append(
                {
                    "label": f"{item.name} ({item.tag})",
                    "path": item.optimized_xyz,
                    "kind": "xyz",
                    "role": "final_stable_structure",
                }
            )
        result_manifest.write(self.result_root)
        write_result_summary(self.result_root, f"batch_{self.profile.level}", summary_products)

    def _resolve_output_geometry(self, item: BatchCalculationItem) -> Path | None:
        """Resolve an item's optimized geometry (task-root or RESULT relative)."""
        if not item.optimized_xyz:
            return None
        raw = Path(item.optimized_xyz)
        if raw.is_absolute() and raw.is_file():
            return raw
        for probe in (
            self.result_root / item.optimized_xyz,
            self.task_root / item.optimized_xyz,
        ):
            if probe.is_file():
                return probe
        return None

    def _read_result_manifest(self) -> ResultManifest:
        path = self.result_root / "result_manifest.json"
        if path.is_file():
            try:
                return ResultManifest.read(self.result_root)
            except (OSError, ValueError):
                logger.warning("Unreadable result_manifest.json — rewriting it", exc_info=True)
        workflow = "Lowconfirm" if self.profile.level == "s3" else "Highconfirm"
        return ResultManifest(task_id="", workflow=workflow, status="completed")
