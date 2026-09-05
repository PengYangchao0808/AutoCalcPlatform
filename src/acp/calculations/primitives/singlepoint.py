# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Single-point energy calculation primitive."""

from __future__ import annotations

import logging

from acp.calculations.contracts import CalculationRequest, CalculationResult

from ._common import (
    artifacts_from_qc,
    backend_for_request,
    backend_name,
    call_capability,
    capability_kwargs,
    error_text,
    load_inputs,
    output_dir,
    result_from_qc,
)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)
logger = logging.getLogger(__name__)


def run_singlepoint(req: CalculationRequest) -> CalculationResult:
    """Run a single-point energy calculation through a backend capability."""
    inputs = load_inputs(req)
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    try:
        qc_result = call_capability(
            backend,
            "single_point",
            inputs,
            output_dir(req),
            capability_kwargs(req),
        )
    except _BACKEND_FAILURES as error:
        return result_from_qc(
            req,
            selected_backend,
            None,
            [error_text(error)],
            [],
            status="failed",
        )

    artifacts = artifacts_from_qc(qc_result, selected_backend)
    if not qc_result.success:
        message = qc_result.error_message or "single-point calculation failed"
        return result_from_qc(req, selected_backend, qc_result, [message], artifacts)
    if qc_result.energy is None:
        return result_from_qc(
            req,
            selected_backend,
            qc_result,
            ["single-point calculation returned no energy"],
            artifacts,
            status="failed",
        )
    return result_from_qc(req, selected_backend, qc_result, [], artifacts)


__all__ = ["run_singlepoint"]
