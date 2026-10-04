# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Single-point energy — ACP compat wrapper (plan todo 17).

The task core lives in :mod:`cccp.calculation.tasks.singlepoint`.  This
module is the ACP-side compat surface: legacy ``CalculationRequest`` → typed
``TaskRequest`` conversion (``acp.calculations.legacy_adapters``), ACP-side
product registration through the publication contract
(:mod:`acp.calculations.result_publication`), and the legacy
``CalculationResult`` envelope mapping.  ``run_singlepoint`` is a pure
forwarder (the dual-root uniqueness guard classifies it as a shim).
"""

from __future__ import annotations

import logging
from pathlib import Path

from acp.calculations.contracts import CalculationRequest, CalculationResult
from acp.calculations.legacy_adapters import to_legacy_result, to_task_request
from acp.calculations.primitives._common import capability_kwargs
from acp.calculations.result_publication import (
    ArtifactReference,
    PublicationOutcome,
    ScientificResultRecord,
    recover_publication,
)
from acp.storage.manifest import ProductKind, ResultManifest
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from cccp.calculation.tasks.singlepoint import (
    run_singlepoint as _cccp_run_singlepoint,
)

logger = logging.getLogger(__name__)


def run_singlepoint(req: CalculationRequest) -> CalculationResult:
    """Run a single-point energy calculation through the cccp task core."""
    return execute_singlepoint(req)


def execute_singlepoint(req: CalculationRequest) -> CalculationResult:
    """ACP compat wrapper: cccp task core + legacy envelope mapping.

    Verbatim legacy capability kwargs (``scf_maxiter``, ``output_name``, …)
    ride along as ``capability_extras`` (translation cleanup: plan todo 25).
    """
    task_request, binding = to_task_request(req, TaskKind.SINGLEPOINT)
    context = TaskContext(
        config=binding.config,
        workdir=binding.artifact_root,
        capability_extras=capability_kwargs(req),
    )
    task_result = _cccp_run_singlepoint(task_request, context=context)
    return legacy_result(task_result, binding)


def legacy_result(task_result: TaskResult, binding: object) -> CalculationResult:
    """Map one typed ``TaskResult`` back to the legacy envelope (lossless)."""
    legacy = to_legacy_result(task_result, binding)  # type: ignore[arg-type]
    if not task_result.metadata:
        return legacy
    metadata = dict(task_result.metadata)
    metadata.update(legacy.metadata)
    return CalculationResult(
        energy=legacy.energy,
        coords=legacy.coords,
        frequencies=legacy.frequencies,
        artifacts=legacy.artifacts,
        status=legacy.status,
        errors=legacy.errors,
        provenance=legacy.provenance,
        metadata=metadata,
    )


# ── ACP-side product registration (publication contract, todo 16) ────────


def scientific_record(
    result: CalculationResult,
    *,
    result_id: str,
    result_dir: Path | str,
    kind: str = "singlepoint",
) -> ScientificResultRecord:
    """Build the contract-① record for one legacy result (data transform)."""
    root = Path(result_dir)
    artifacts: list[ArtifactReference] = []
    for artifact in result.artifacts:
        path = Path(artifact.path)
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            rel = path
        artifacts.append(ArtifactReference(path=str(rel), type=artifact.type))
    summary: dict[str, object] = {
        "status": result.status,
        "errors": list(result.errors),
    }
    if result.energy is not None:
        summary["energy_hartree"] = result.energy
    return ScientificResultRecord(
        result_id=result_id,
        kind=kind,
        artifacts=tuple(artifacts),
        summary=summary,
    )


def _default_manifest(record: ScientificResultRecord) -> ResultManifest:
    manifest = ResultManifest(task_id=record.result_id, workflow=record.kind, status="completed")
    for artifact in record.artifacts:
        manifest.add_product(
            artifact.path, artifact.path, artifact.path, ProductKind.FILE
        )
    return manifest


def recover_singlepoint(
    req: CalculationRequest,
    *,
    result_dir: Path | str,
    result_id: str,
    manifest: ResultManifest | None = None,
) -> PublicationOutcome:
    """ACP recovery entry: check the stored scientific result FIRST.

    A valid contract-① record for *result_id* means only publication is
    retried (the QC callable is never invoked — fault-injection contract:
    "科学结果已存 + 发布失败 → 恢复只重试发布、QC 调用不增"); otherwise the
    calculation runs once and the full sequence ①②③ publishes.
    """

    def _execute_qc() -> ScientificResultRecord:
        result = execute_singlepoint(req)
        return scientific_record(result, result_id=result_id, result_dir=result_dir)

    def _build_manifest(record: ScientificResultRecord) -> ResultManifest:
        return manifest if manifest is not None else _default_manifest(record)

    return recover_publication(
        result_dir,
        result_id=result_id,
        execute_qc=_execute_qc,
        build_manifest=_build_manifest,
    )


__all__ = ["execute_singlepoint", "recover_singlepoint", "run_singlepoint"]
