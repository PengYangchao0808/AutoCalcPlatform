# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Vibrational frequency calculation primitive."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from acp.calculations.contracts import ArtifactRef, CalculationRequest, CalculationResult

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


def _try_materialize_normal_modes(
    qc_result: Any,
    atom_count: int,
    out_dir: Path | None,
    artifacts: list[ArtifactRef],
    backend_name_str: str,
) -> list[ArtifactRef]:
    """Attempt to parse ORCA normal modes and write ``normal_modes.json``.

    Returns the (possibly extended) artifacts list.  Never raises — all
    failures are silently logged at debug level so the frequency step
    is never failed due to missing modes alone.
    """
    if out_dir is None:
        return artifacts

    # Determine the log file to read — prefer freq_log_file, fall back to log_file.
    log_path: Path | None = None
    freq_log = getattr(qc_result, "freq_log_file", None)
    if isinstance(freq_log, (str, Path)) and str(freq_log):
        log_path = Path(freq_log)
    if log_path is None or not log_path.is_file():
        main_log = getattr(qc_result, "log_file", None)
        if isinstance(main_log, (str, Path)) and str(main_log):
            log_path = Path(main_log)
    if log_path is None or not log_path.is_file():
        logger.debug("frequency: no log file found for normal-mode parsing; skipping")
        return artifacts

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("frequency: could not read %s for normal modes; skipping", log_path)
        return artifacts

    try:
        from acp.results.orca_parser import OrcaOutputParser
        calc = OrcaOutputParser().parse_text(text)
    except Exception:
        logger.debug("frequency: OrcaOutputParser failed on %s; skipping", log_path, exc_info=True)
        return artifacts

    if not calc.mode_vectors:
        logger.debug("frequency: no NORMAL MODES section found in %s; skipping", log_path)
        return artifacts

    try:
        from acp.results.frequencies import build_normal_modes_product
        product = build_normal_modes_product(calc, geometry_product_id=None, atom_count=atom_count)
    except Exception:
        logger.debug("frequency: build_normal_modes_product failed; skipping", exc_info=True)
        return artifacts

    if not product.get("modes"):
        logger.debug("frequency: normal_modes product has no valid modes; skipping")
        return artifacts

    nm_path = out_dir / "normal_modes.json"
    try:
        nm_path.write_text(json.dumps(product, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.debug("frequency: could not write %s; skipping", nm_path, exc_info=True)
        return artifacts

    logger.debug("frequency: wrote normal_modes.json with %d modes to %s", len(product["modes"]), nm_path)
    artifacts = list(artifacts)
    artifacts.append(ArtifactRef(path=nm_path, type="normal_modes", source=backend_name_str))
    return artifacts


def run_frequency(req: CalculationRequest) -> CalculationResult:
    """Run a frequency calculation through a backend capability."""
    inputs = load_inputs(req)
    selected_backend = backend_name(req)
    backend = backend_for_request(req, selected_backend)
    out_dir = output_dir(req)
    try:
        qc_result = call_capability(
            backend,
            "frequency",
            inputs,
            out_dir,
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
    artifacts.extend(write_state_artifacts(inputs, qc_result, out_dir, selected_backend))
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

    # Try to materialize normal modes from the ORCA output (Wave 4, todo 22).
    # Never fails the step — all errors are silently swallowed.
    atom_count = len(inputs.symbols)
    artifacts = _try_materialize_normal_modes(
        qc_result, atom_count, out_dir, artifacts, selected_backend
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
