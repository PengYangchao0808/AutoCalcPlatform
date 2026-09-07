# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Vibrational frequency calculation primitive."""

from __future__ import annotations

import logging

from acp.calculations.contracts import CalculationRequest, CalculationResult

from ._common import (
    artifacts_from_qc,
    backend_for_request,
    backend_name,
    call_capability,
    capability_kwargs,
    electronic_state_result_metadata,
    error_text,
    load_inputs,
    output_dir,
    result_from_qc,
    write_state_artifacts,
)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)
logger = logging.getLogger(__name__)


def run_frequency(req: CalculationRequest) -> CalculationResult:
    """Run a frequency calculation through a backend capability."""
    inputs = load_inputs(req)
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    try:
        qc_result = call_capability(
            backend,
            "frequency",
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
    state_metadata, state_errors, forced_status = electronic_state_result_metadata(
        inputs, qc_result
    )
    artifacts.extend(write_state_artifacts(inputs, qc_result, output_dir(req), selected_backend))
    if not qc_result.success:
        message = qc_result.error_message or "frequency calculation failed"
        return result_from_qc(req, selected_backend, qc_result, [message], artifacts)
    if state_errors:
        return result_from_qc(
            req,
            selected_backend,
            qc_result,
            state_errors,
            artifacts,
            metadata={"electronic_state": state_metadata},
            status=forced_status or "failed",
        )
    if state_metadata:
        return result_from_qc(
            req,
            selected_backend,
            qc_result,
            [],
            artifacts,
            metadata={"electronic_state": state_metadata},
        )
    return result_from_qc(req, selected_backend, qc_result, [], artifacts)


__all__ = ["run_frequency"]
