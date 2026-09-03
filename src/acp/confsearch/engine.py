# pyright: basic, reportExplicitAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportMissingTypeArgument=false, reportUnannotatedClassAttribute=false
"""ConfsearchEngine — the single Confsearch entry point (plan §14).

All upper layers (CLI, scheduler, API, NMR conformer generation, the
mechanism S1 stage) construct a :class:`ConfsearchRequest` and call
:meth:`ConfsearchEngine.run`; none of them re-implement CREST/CENSO/xTB
orchestration or conformer dedup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

from acp.calculations.progress import LiveMetric, ProgressReporter

from .contracts import (
    ConfsearchRequest,
    ConfsearchResult,
    ProtocolOutcome,
    validate_request,
)
from .manifest import (
    build_manifest_payload,
    confsearch_result_dir,
    write_conformer_geometries,
    write_ensemble_table,
    write_manifest,
)
from .profiles import profile_overlay
from .protocols import PROTOCOL_RUNNERS
from .result_helpers import build_entries, quality_gates, refinement_block
from .selection import select_for_refinement
from .shared.provenance import input_block, provenance_block

logger = logging.getLogger(__name__)

CONFSEARCH_STAGES: Final[tuple[str, ...]] = (
    "prepare",
    "sampling",
    "energy",
    "dedup",
    "refinement",
    "finalize",
)


class ConfsearchEngine:
    """Run one Confsearch request through its protocol and write the S1 manifest."""

    def __init__(self, *, progress_reporter: ProgressReporter | None = None) -> None:
        self._progress_reporter = progress_reporter

    def run(
        self,
        request: ConfsearchRequest,
        *,
        progress_reporter: ProgressReporter | None = None,
    ) -> ConfsearchResult:
        """Execute the request; never raises for workflow-level failures.

        Protocol/argument errors raise ``ValueError`` (fail fast at the
        boundary); execution failures are reported via
        ``ConfsearchResult.status == "failed"`` + ``error``.
        """
        reporter = progress_reporter if progress_reporter is not None else self._progress_reporter
        if reporter is not None:
            reporter.initialize()
            reporter.start_stage("prepare")
        try:
            validate_request(request)
            overlay = profile_overlay(request.protocol, request.profile)
        except ValueError as exc:
            self._fail_progress(reporter, str(exc))
            raise

        if reporter is not None:
            reporter.complete_stage("prepare")
            reporter.start_stage("sampling")
        try:
            outcome = self._run_protocol(request, overlay)
        except Exception as exc:  # noqa: BLE001 - surfaced via result
            logger.exception("Confsearch protocol %s failed: %s", request.protocol, exc)
            self._fail_progress(reporter, str(exc))
            return ConfsearchResult(
                status="failed",
                protocol=request.protocol,
                profile=request.profile,
                refinement_policy=request.refinement_policy,
                error=str(exc),
            )

        if reporter is not None:
            reporter.complete_stage("sampling")
            reporter.start_stage("energy")

        try:
            result = self._finalize(request, outcome, progress_reporter=reporter)
        except Exception as exc:  # noqa: BLE001 - surfaced via result
            logger.exception("Confsearch finalization failed: %s", exc)
            self._fail_progress(reporter, str(exc))
            return ConfsearchResult(
                status="failed",
                protocol=request.protocol,
                profile=request.profile,
                refinement_policy=request.refinement_policy,
                error=str(exc),
            )
        if reporter is not None:
            reporter.complete()
        return result

    @staticmethod
    def _fail_progress(reporter: ProgressReporter | None, error: str) -> None:
        """Mark the active progress stage failed when a run cannot continue."""
        if reporter is None:
            return
        current_stage = reporter.current_stage
        if current_stage is None:
            reporter.fail(error)
        else:
            reporter.fail_stage(current_stage, error)

    @staticmethod
    def _sampled_count(outcome: ProtocolOutcome | None) -> int | None:
        """Return the count of protocol-returned candidate records, if available."""
        records = getattr(outcome, "records", None)
        if isinstance(records, list):
            return len(records)
        return None

    def _run_protocol(self, request: ConfsearchRequest, overlay: dict[str, Any]) -> ProtocolOutcome:
        runner = PROTOCOL_RUNNERS[request.protocol]
        return runner(request, overlay)

    # ── finalization: entries → unified tree → manifest ─────────────────

    def _finalize(
        self,
        request: ConfsearchRequest,
        outcome: ProtocolOutcome,
        *,
        progress_reporter: ProgressReporter | None = None,
    ) -> ConfsearchResult:
        entries = build_entries(outcome)
        if progress_reporter is not None:
            progress_reporter.complete_stage("energy")
            progress_reporter.start_stage("dedup")
            progress_reporter.complete_stage("dedup")
            progress_reporter.start_stage("refinement")
        confsearch_dir = self._confsearch_dir(request)
        write_conformer_geometries(confsearch_dir, entries, outcome.records)
        write_ensemble_table(confsearch_dir, entries)

        selected = select_for_refinement(
            request.refinement_policy,
            entries,
            threshold=float((request.levels or {}).get("refinement_threshold") or 0.99),
        )
        if progress_reporter is not None:
            sampled_count = self._sampled_count(outcome)
            if sampled_count is not None:
                progress_reporter.set_live_metrics(
                    [
                        LiveMetric(
                            key="conformers_sampled",
                            label_key="live.conformers_sampled",
                            value=str(sampled_count),
                            kind="count",
                            priority=100,
                        ),
                        LiveMetric(
                            key="conformers_kept",
                            label_key="live.conformers_kept",
                            value=str(len(selected)),
                            kind="count",
                            priority=90,
                        ),
                    ]
                )
        gates = quality_gates(entries, outcome, selected)

        if progress_reporter is not None:
            progress_reporter.complete_stage("refinement")
            progress_reporter.start_stage("finalize")

        payload = build_manifest_payload(
            protocol=request.protocol,
            profile=request.profile,
            refinement_policy=request.refinement_policy,
            backend=request.backend,
            input_block=input_block(request.input_source, request.charge, request.multiplicity),
            sampling=dict(outcome.sampling),
            conformers=entries,
            selected_conformers=selected,
            refinement=refinement_block(request, outcome, selected),
            provenance=provenance_block(
                request.protocol,
                request.profile,
                request.refinement_policy,
                request.backend,
                extra={"temperature_k": outcome.temperature_k},
            ),
            quality_gates=gates,
        )
        manifest_path = write_manifest(confsearch_dir, payload)
        logger.info("Confsearch manifest written: %s", manifest_path)
        if progress_reporter is not None:
            progress_reporter.complete_stage("finalize")
        return ConfsearchResult(
            status="completed",
            protocol=request.protocol,
            profile=request.profile,
            refinement_policy=request.refinement_policy,
            conformers=entries,
            selected_conformers=selected,
            manifest_path=manifest_path,
            quality_gates=gates,
            metadata=dict(outcome.workflow_metadata),
        )

    def _confsearch_dir(self, request: ConfsearchRequest) -> Path:
        from acp.io.structures import StructureReader
        from acp.workflows._helpers import resolve_task_output_root, sanitize_job_name

        reader = StructureReader()
        structure = reader.read(
            request.input_source,
            charge=request.charge,
            multiplicity=request.multiplicity,
            name=request.name,
        )
        safe_name = sanitize_job_name(structure.id)
        mol_dir = resolve_task_output_root(request.output_dir.resolve(), safe_name)
        confsearch_dir = confsearch_result_dir(mol_dir)
        confsearch_dir.mkdir(parents=True, exist_ok=True)
        return confsearch_dir


__all__ = ["CONFSEARCH_STAGES", "ConfsearchEngine"]
