# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Vibrational frequency — ACP compat wrapper (plan todo 19).

The task core lives in :mod:`cccp.calculation.tasks.frequency`; the single
scientific parse of frequencies / vibration vectors / IR intensities lives
in :mod:`cccp.calculation.frequency_parse` and rides back on
``FrequencyPayload.analysis``.  This module is the ACP-side compat surface:
legacy ``CalculationRequest`` → typed ``TaskRequest`` conversion
(``acp.calculations.legacy_adapters``), the legacy backend registry seam,
and the **publication half** of the old ``_try_materialize_normal_modes``
split — the ``normal_modes.json`` product format, geometry binding and
manifest registration stay ACP-side (geometry binding is applied by the
plan/batch consumers from the registered product).  ``run_frequency`` is a
pure forwarder (the dual-root uniqueness guard classifies it as a shim).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from acp.calculations.contracts import ArtifactRef, CalculationRequest, CalculationResult
from acp.calculations.legacy_adapters import to_legacy_result, to_task_request
from acp.calculations.primitives._common import (
    backend_for_request,
    backend_name,
    capability_kwargs,
    output_dir,
)
from cccp import calculation as _cccp_calculation
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import FrequencyPayload, TaskResult

logger = logging.getLogger(__name__)


def run_frequency(req: CalculationRequest) -> CalculationResult:
    """Run a frequency calculation through the cccp task core."""
    return execute_frequency(req)


def execute_frequency(req: CalculationRequest) -> CalculationResult:
    """ACP compat wrapper: cccp task core + product publication.

    Verbatim legacy capability kwargs ride along as ``capability_extras``
    (translation cleanup: plan todo 25); the ``normal_modes.json`` view
    product is published from the typed payload (no re-parse of the QC
    output) and registered as a ``normal_modes`` artifact.
    """
    task_request, binding = to_task_request(req, TaskKind.FREQUENCY)
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    context = TaskContext(
        config=binding.config,
        workdir=binding.artifact_root,
        backend=backend,
        capability_extras=capability_kwargs(req),
    )
    task_result = _cccp_calculation.run_frequency(task_request, context=context)
    legacy = _legacy_result(task_result, binding)
    published = _publish_normal_modes(
        task_request,
        task_result,
        output_dir(req),
        selected_backend,
        legacy.artifacts,
    )
    if not published:
        return legacy
    return CalculationResult(
        energy=legacy.energy,
        coords=legacy.coords,
        frequencies=legacy.frequencies,
        artifacts=[*legacy.artifacts, *published],
        status=legacy.status,
        errors=legacy.errors,
        provenance=legacy.provenance,
        metadata=legacy.metadata,
    )


def _legacy_result(task_result: TaskResult, binding: Any) -> CalculationResult:
    """Map one typed ``TaskResult`` back to the legacy envelope (lossless)."""
    legacy = to_legacy_result(task_result, binding)
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


# ── ACP-side product publication (parse half lives in cccp) ────────────


def _publish_normal_modes(
    task_request: Any,
    task_result: TaskResult,
    out_dir: Path | None,
    backend_label: str,
    existing: list[ArtifactRef],
) -> list[ArtifactRef]:
    """Write ``normal_modes.json`` from the typed payload (publication half).

    Product format (``normal_modes_v1``) is ACP's; the scientific data comes
    from ``FrequencyPayload.analysis`` — the QC output is never re-parsed
    here.  Never raises: missing/partial modes just skip the product (the
    frequency step is never failed due to missing modes alone).
    """
    if out_dir is None or task_result.status != "completed":
        return []
    payload = task_result.payload
    analysis = payload.analysis if isinstance(payload, FrequencyPayload) else None
    if analysis is None or not analysis.mode_vectors:
        logger.debug("frequency: no parsed normal modes; skipping normal_modes.json")
        return []
    if any(artifact.type == "normal_modes" for artifact in existing):
        return []

    from acp.results.frequencies import build_normal_modes_product

    atom_count = _atom_count(task_request, task_result)
    try:
        product = build_normal_modes_product(
            analysis, geometry_product_id=None, atom_count=atom_count
        )
    except Exception:  # noqa: BLE001 — publication never fails the step
        logger.debug("frequency: build_normal_modes_product failed; skipping", exc_info=True)
        return []
    if not product.get("modes"):
        logger.debug("frequency: normal_modes product has no valid modes; skipping")
        return []

    nm_path = out_dir / "normal_modes.json"
    try:
        nm_path.write_text(json.dumps(product, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.debug("frequency: could not write %s; skipping", nm_path, exc_info=True)
        return []

    logger.debug(
        "frequency: wrote normal_modes.json with %d modes to %s",
        len(product["modes"]),
        nm_path,
    )
    return [ArtifactRef(path=nm_path, type="normal_modes", source=backend_label)]


def _atom_count(task_request: Any, task_result: TaskResult) -> int:
    """Atom count of the input geometry (the product's vector-length check)."""
    if task_result.symbols:
        return len(task_result.symbols)
    structure = getattr(task_request, "structure", None)
    for attribute in ("symbols", "coordinates", "elements"):
        values = getattr(structure, attribute, None)
        if values:
            return len(values)
    return 0


__all__ = ["execute_frequency", "run_frequency"]
