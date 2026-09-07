# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""CASSCF / NEVPT2 single-point calculation primitive (design doc §11)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from acp.backends.base import QCResult
from acp.calculations.contracts import (
    ArtifactRef,
    CalculationRequest,
    CalculationResult,
    CASSCFSpec,
    JsonValue,
    casscf_spec_from_dict,
    validate_casscf_spec,
)

from ._common import (
    CalculationInputs,
    artifacts_from_qc,
    backend_for_request,
    backend_name,
    capability_kwargs,
    electron_count,
    error_text,
    load_inputs,
    output_dir,
    result_from_qc,
)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)
logger = logging.getLogger(__name__)


def run_casscf(req: CalculationRequest) -> CalculationResult:
    """Run a CASSCF (optionally NEVPT2) calculation on one structure.

    The active space arrives as ``resources["casscf"]``; missing or
    invalid active-space information fails the step before any QC
    subprocess is spawned (§19 acceptance item 12).
    """
    inputs = load_inputs(req)
    selected_backend = backend_name(req)
    if selected_backend != "orca":
        return result_from_qc(
            req,
            selected_backend,
            None,
            [f"CASSCF is only supported on the ORCA backend (got {selected_backend!r})"],
            [],
            status="failed",
        )

    raw_spec = req.resources.get("casscf")
    try:
        spec = casscf_spec_from_dict(raw_spec if isinstance(raw_spec, dict) else None)
    except ValueError as error:
        return result_from_qc(
            req,
            selected_backend,
            None,
            [error_text(error)],
            [],
            status="failed",
        )

    n_electrons = _electron_count(inputs)
    validation_errors = validate_casscf_spec(spec, n_electrons=n_electrons)
    if validation_errors:
        return result_from_qc(
            req,
            selected_backend,
            None,
            validation_errors,
            [],
            status="failed",
        )

    backend = backend_for_request(req, selected_backend)
    target_dir = output_dir(req)
    kwargs: dict[str, Any] = dict(capability_kwargs(req))
    kwargs.pop("casscf", None)
    kwargs.update(
        {
            "active_electrons": spec.active_electrons,
            "active_orbitals": spec.active_orbitals,
            "nroots": spec.nroots,
            "state_weights": list(spec.state_weights),
            "dynamic_correlation": spec.dynamic_correlation.value,
            "active_orbital_indices": list(spec.active_orbital_indices),
            "frozen_core": spec.frozen_core,
        }
    )
    if spec.orbital_source is not None:
        kwargs["orbital_source"] = str(spec.orbital_source)
    if spec.max_iterations is not None:
        kwargs["max_iterations"] = spec.max_iterations
    if inputs.scf_options:
        kwargs["scf_options"] = dict(inputs.scf_options)

    multiplicity = spec.multiplicity
    if inputs.electronic_state is not None:
        multiplicity = inputs.electronic_state.target_multiplicity

    try:
        qc_result = backend.casscf(
            inputs.coordinates,
            list(inputs.symbols),
            charge=inputs.charge,
            multiplicity=multiplicity,
            output_dir=target_dir,
            **kwargs,
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
        message = qc_result.error_message or "CASSCF calculation failed"
        return result_from_qc(req, selected_backend, qc_result, [message], artifacts)
    if qc_result.energy is None:
        return result_from_qc(
            req,
            selected_backend,
            qc_result,
            ["CASSCF calculation returned no energy"],
            artifacts,
            status="failed",
        )

    multireference = _multireference_metadata(qc_result, spec, multiplicity)
    artifacts.extend(_write_active_space_artifacts(target_dir, multireference, selected_backend))
    return result_from_qc(
        req,
        selected_backend,
        qc_result,
        [],
        artifacts,
        {"multireference": multireference},
    )


def _electron_count(inputs: CalculationInputs) -> int | None:
    return electron_count(inputs.symbols, inputs.charge)


def _multireference_metadata(
    qc_result: QCResult,
    spec: CASSCFSpec,
    multiplicity: int,
) -> dict[str, JsonValue]:
    raw = qc_result.metadata.get("casscf") if isinstance(qc_result.metadata, dict) else {}
    parsed = raw if isinstance(raw, dict) else {}
    return {
        "active_electrons": spec.active_electrons,
        "active_orbitals": spec.active_orbitals,
        "multiplicity": multiplicity,
        "nroots": spec.nroots,
        "state_weights": list(spec.state_weights),
        "active_orbital_indices": list(spec.active_orbital_indices),
        "orbital_source": str(spec.orbital_source) if spec.orbital_source else None,
        "dynamic_correlation": spec.dynamic_correlation.value,
        "active_space_signature": spec.active_space_signature(),
        "casscf_energy_hartree": parsed.get("casscf_energy_hartree"),
        "nevpt2_correction_hartree": parsed.get("nevpt2_correction_hartree"),
        "correlated_energy_hartree": parsed.get("correlated_energy_hartree"),
        "natural_occupations": parsed.get("natural_occupations") or [],
        "nevpt2_roots": parsed.get("nevpt2_roots") or [],
        "converged": bool(parsed.get("converged")),
    }


def _write_active_space_artifacts(
    target_dir: Path | None,
    multireference: dict[str, JsonValue],
    backend: str,
) -> list[ArtifactRef]:
    if target_dir is None:
        return []
    artifacts: list[ArtifactRef] = []
    try:
        active_space_file = target_dir / "active_space.json"
        active_space_file.write_text(
            json.dumps(multireference, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        artifacts.append(ArtifactRef(path=active_space_file, type="active_space", source=backend))
        occupations = multireference.get("natural_occupations")
        if isinstance(occupations, list) and occupations:
            occupations_file = target_dir / "natural_occupations.json"
            occupations_file.write_text(
                json.dumps({"occupations": occupations}, indent=2),
                encoding="utf-8",
            )
            artifacts.append(
                ArtifactRef(path=occupations_file, type="natural_occupations", source=backend)
            )
    except OSError as exc:
        logger.warning("failed to write CASSCF artifacts in %s: %s", target_dir, exc)
    return artifacts


__all__ = ["run_casscf"]
