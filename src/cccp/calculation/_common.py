"""Shared single-task pipeline for :mod:`cccp.calculation` tasks (todos 17–22).

Migrated from ``acp.calculations.primitives._common`` (plan todo 17): the
scientific pipeline — geometry loading, electronic-state scf options, quality
gate, state artifacts, capability dispatch, QC-result artifact collection and
the translation-layer minimal entry — lives here exactly once so every task
core (``tasks/singlepoint.py`` …) builds on it and no cccp module ever imports
``acp``.  The ACP side keeps only legacy-shape parsing and result-envelope
conversion around these functions.

Translation-layer minimal public entry (plan todo 17, review fix):

* :func:`resolve_spec` — the single parameter-resolution point
  (:class:`~cccp.qc.resolved_spec.ResolvedCalculationSpec`);
* :func:`render_backend_input` — resolved spec + electronic state → backend
  capability keyword arguments.  Verbatim capability residue passes through
  unchanged until the full translation-layer cleanup (plan todo 25).

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.backends.base import QCResult, to_qc_result
from cccp.calculation.contracts import (
    ArtifactRef,
    ElectronicStateSpec,
    GuessStrategy,
    JsonValue,
    SpatialSymmetryMode,
    SpinMode,
    StabilityMode,
    WavefunctionSource,
    electron_parity_ok,
    expected_s2_for_multiplicity,
    orca_xyz_multiplicity,
)
from cccp.calculation.errors import UnsupportedCapabilityError
from cccp.calculation.requests import TaskResources
from cccp.calculation.results import ErrorKind
from cccp.qc.resolved_spec import ResolvedCalculationSpec, resolve_calculation_spec
from cccp.utils.resource_utils import normalize_memory

logger = logging.getLogger(__name__)

_SYMBOL_TO_Z = {
    "H": 1, "HE": 2, "LI": 3, "BE": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9,
    "NE": 10, "NA": 11, "MG": 12, "AL": 13, "SI": 14, "P": 15, "S": 16, "CL": 17,
    "AR": 18, "K": 19, "CA": 20, "SC": 21, "TI": 22, "V": 23, "CR": 24, "MN": 25,
    "FE": 26, "CO": 27, "NI": 28, "CU": 29, "ZN": 30, "GA": 31, "GE": 32, "AS": 33,
    "SE": 34, "BR": 35, "KR": 36, "RB": 37, "SR": 38, "Y": 39, "ZR": 40, "NB": 41,
    "MO": 42, "TC": 43, "RU": 44, "RH": 45, "PD": 46, "AG": 47, "CD": 48, "IN": 49,
    "SN": 50, "SB": 51, "TE": 52, "I": 53, "XE": 54, "CS": 55, "BA": 56, "LA": 57,
    "W": 74, "PT": 78, "AU": 79, "HG": 80, "PB": 82, "BI": 83,
}


def electron_count(symbols: tuple[str, ...], charge: int) -> int | None:
    """Total electron count from element symbols, or ``None`` when unknown."""
    total = 0
    for symbol in symbols:
        z_value = _SYMBOL_TO_Z.get(symbol.strip().upper())
        if z_value is None:
            return None
        total += z_value
    return total - charge


@dataclass(frozen=True, slots=True)
class CalculationInputs:
    """Parsed geometry and electronic-state values sent to a capability."""

    coordinates: NDArray[np.float64]
    symbols: tuple[str, ...]
    charge: int
    multiplicity: int
    scf_options: dict[str, Any] | None = None
    electronic_state: ElectronicStateSpec | None = None


# ── geometry loading (cccp.io — never acp.io) ───────────────────────────


def load_geometry(
    *,
    path: Path | str | None = None,
    coordinates: Any = None,
    symbols: Sequence[str] | None = None,
    elements: Sequence[str] = (),
) -> tuple[NDArray[np.float64], tuple[str, ...]]:
    """Normalise one structure into ``(coordinates, symbols)``.

    Inline geometry overrides the file when both are present (legacy
    ``load_inputs`` precedence).  File input goes through
    :meth:`cccp.io.input_handler.MolecularInputHandler.from_source` — the
    task layer never touches ``acp.io``.

    Raises:
        ValueError: On a shape/length mismatch or an unparsable source.
    """
    if coordinates is None:
        if path is None:
            raise ValueError("structure input requires a path or inline coordinates")
        from cccp.io.input_handler import MolecularInputHandler

        molecule = MolecularInputHandler.from_source(Path(path))
        parsed_coordinates = np.asarray(molecule.coordinates, dtype=np.float64)
        parsed_symbols: tuple[str, ...] = tuple(str(s) for s in molecule.symbols)
    else:
        parsed_coordinates = np.asarray(coordinates, dtype=np.float64)
        parsed_symbols = (
            tuple(str(value) for value in symbols) if symbols is not None else ()
        )

    normalized = np.asarray(parsed_coordinates, dtype=np.float64)
    if normalized.ndim != 2 or normalized.shape[1] != 3:
        raise ValueError("calculation coordinates must have shape (N, 3)")

    resolved_symbols = tuple(str(e) for e in elements) or parsed_symbols
    if not resolved_symbols or len(resolved_symbols) != normalized.shape[0]:
        raise ValueError("calculation coordinates and element symbols must have equal length")
    return normalized, resolved_symbols


def resolve_multiplicity(
    state: ElectronicStateSpec | None,
    symbols: tuple[str, ...],
    charge: int,
    default: int,
) -> int:
    """Resolve the effective spin multiplicity for one calculation.

    An electronic state overrides the request multiplicity after an
    electron-parity compatibility check (``orca_xyz_multiplicity``).
    """
    if state is None:
        return default
    n_electrons = electron_count(symbols, charge)
    if n_electrons is not None and not electron_parity_ok(
        n_electrons, state.target_multiplicity
    ):
        message = (
            f"electronic state {state.state_id!r} multiplicity "
            f"{state.target_multiplicity} is incompatible with the electron count"
        )
        raise ValueError(message)
    return orca_xyz_multiplicity(state)


# ── electronic-state scf options / quality gate ─────────────────────────


def build_state_scf_options(state: ElectronicStateSpec) -> dict[str, Any]:
    """Map one electronic state to ORCA-flavoured ``scf_options`` (§9)."""
    options: dict[str, Any] = {}
    if state.spin_mode is SpinMode.RESTRICTED:
        options["hf_typ"] = "RHF"
    elif state.spin_mode in (SpinMode.UNRESTRICTED, SpinMode.BROKEN_SYMMETRY):
        options["hf_typ"] = "UHF"

    guess = state.guess
    if guess.strategy is GuessStrategy.GUESSMIX:
        options.setdefault("hf_typ", "UHF")
        options["guess_mix_angle"] = guess.guess_mix_angle
    elif guess.strategy is GuessStrategy.FLIPSPIN:
        options["hf_typ"] = "UHF"
        options["flip_spin_atoms"] = list(guess.orca_flip_atoms())
        if guess.final_ms is not None:
            options["final_ms"] = guess.final_ms
    elif guess.strategy is GuessStrategy.BROKEN_SYM:
        options.setdefault("hf_typ", "UHF")
        if guess.broken_sym_na is not None and guess.broken_sym_nb is not None:
            options["broken_sym_na"] = guess.broken_sym_na
            options["broken_sym_nb"] = guess.broken_sym_nb
    elif guess.strategy in (GuessStrategy.MOREAD, GuessStrategy.STABILITY_RESTART):
        source = guess.orbital_source or (
            state.wavefunction.artifact_path
            if state.wavefunction.source is WavefunctionSource.ARTIFACT
            else None
        )
        if source is not None:
            options["mo_read_path"] = str(source)

    if state.wavefunction.source is WavefunctionSource.ARTIFACT and "mo_read_path" not in options:
        if state.wavefunction.artifact_path is not None:
            options["mo_read_path"] = str(state.wavefunction.artifact_path)

    if state.spatial_symmetry is SpatialSymmetryMode.DISABLE:
        options["no_use_sym"] = True
    elif state.spatial_symmetry is SpatialSymmetryMode.PRESERVE:
        options["use_sym"] = True

    if state.diagnostics.write_spin_density:
        options["write_spin_density"] = True
    return options


def apply_stability_options(
    scf_options: Mapping[str, Any] | None,
    *,
    state: ElectronicStateSpec | None,
    stability_check: bool | None = None,
) -> dict[str, Any]:
    """Fold ``stability_check`` / state diagnostics into SP stability flags.

    Stability analysis is restricted to SP-like nodes (§3.4, §13.4): an
    OPT/FREQ request never receives ``STABPerform`` — the plan executor
    appends a dedicated SP diagnostic node instead.
    """
    options = dict(scf_options or {})
    requested = bool(stability_check) or (
        state is not None and state.diagnostics.stability is not StabilityMode.NONE
    )
    if requested:
        options.setdefault("stab_perform", True)
        options.setdefault("stab_restart", True)
    return options


def assess_state_quality(
    state: ElectronicStateSpec,
    diagnostics: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """Three-state collapse verdict: accepted | warning | collapsed (§10.3).

    ``<S²>`` alone is never a hard diradical criterion; the verdict combines
    the gate window with the sign structure of the atomic spin populations.
    """
    verdict = "accepted"
    notes: list[str] = []
    gate = state.quality_gate
    s2 = diagnostics.get("s2")
    raw_populations = diagnostics.get("mulliken_spin_populations") or diagnostics.get(
        "loewdin_spin_populations"
    )
    populations = raw_populations if isinstance(raw_populations, Mapping) else {}
    positive, negative = _spin_centers(populations, gate.spin_center_threshold)
    is_broken_symmetry = state.spin_mode is SpinMode.BROKEN_SYMMETRY

    collapsed = False
    if is_broken_symmetry:
        if populations and not positive and not negative:
            collapsed = True
            notes.append(
                "no significant local spin density: alpha/beta densities are identical"
            )
        if s2 is not None and gate.s2_min is not None and s2 < gate.s2_min:
            if not positive or not negative:
                collapsed = True
            notes.append(f"<S^2>={s2:.4f} below gate s2_min={gate.s2_min:g}")

    if collapsed:
        return "collapsed", notes

    if is_broken_symmetry:
        if gate.require_opposite_spin_centers and populations and not (positive and negative):
            verdict = "warning"
            notes.append("opposite-sign spin centers required but not observed")
        if s2 is not None and gate.s2_max is not None and s2 > gate.s2_max:
            verdict = "warning"
            notes.append(f"<S^2>={s2:.4f} above gate s2_max={gate.s2_max:g}")
        if s2 is not None and gate.s2_min is not None and s2 < gate.s2_min:
            verdict = "warning"
    return verdict, notes


def state_result_metadata(
    state: ElectronicStateSpec,
    diagnostics: Mapping[str, Any],
) -> tuple[dict[str, JsonValue], list[str], str | None]:
    """Build the §12.1 ``electronic_state`` metadata block and gate outcome.

    Returns:
        ``(metadata, errors, forced_status)`` — ``forced_status`` is
        ``"failed"`` when the collapse policy escalates to error.
    """
    verdict, notes = assess_state_quality(state, diagnostics)
    metadata: dict[str, JsonValue] = {
        "state_id": state.state_id,
        "spin_mode": state.spin_mode.value,
        "target_multiplicity": state.target_multiplicity,
        "reference_multiplicity": state.guess.reference_multiplicity
        if state.guess.strategy is GuessStrategy.FLIPSPIN
        else None,
        "final_ms": (
            state.guess.final_ms if state.guess.strategy is GuessStrategy.FLIPSPIN else None
        ),
        "guess_strategy": state.guess.strategy.value,
        "expected_s2": expected_s2_for_multiplicity(state.target_multiplicity),
        "state_status": verdict,
    }
    s2_value = diagnostics.get("s2")
    if isinstance(s2_value, (int, float)):
        metadata["s2"] = float(s2_value)
    raw_populations = diagnostics.get("mulliken_spin_populations") or diagnostics.get(
        "loewdin_spin_populations"
    )
    if isinstance(raw_populations, Mapping):
        positive, negative = _spin_centers(
            raw_populations, state.quality_gate.spin_center_threshold
        )
        metadata["positive_spin_centers"] = positive
        metadata["negative_spin_centers"] = negative

    errors: list[str] = []
    forced_status: str | None = None
    if verdict == "collapsed":
        collapse_message = (
            f"electronic state {state.state_id!r} collapsed to a "
            + "restricted-like solution: "
            + "; ".join(notes)
        )
        if state.quality_gate.collapse_policy.value == "error":
            errors.append(collapse_message)
            forced_status = "failed"
        elif state.quality_gate.collapse_policy.value == "warning":
            metadata["state_notes"] = notes
            logger.warning("%s", collapse_message)
        else:
            metadata["state_notes"] = notes
    elif notes:
        metadata["state_notes"] = notes
    return metadata, errors, forced_status


def write_state_artifacts(
    state: ElectronicStateSpec | None,
    diagnostics: Mapping[str, Any],
    target_dir: Path | None,
    backend: str,
) -> list[ArtifactRef]:
    """Persist ``electronic_state.json`` / ``spin_diagnostics.json`` (§12.3)."""
    if state is None or target_dir is None:
        return []

    artifacts: list[ArtifactRef] = []
    metadata, _, _ = state_result_metadata(state, diagnostics)

    state_payload: dict[str, Any] = dict(metadata)
    state_payload["quality_gate"] = {
        "collapse_policy": state.quality_gate.collapse_policy.value,
        "s2_min": state.quality_gate.s2_min,
        "s2_max": state.quality_gate.s2_max,
        "require_opposite_spin_centers": state.quality_gate.require_opposite_spin_centers,
    }
    state_file = target_dir / "electronic_state.json"
    _write_json_artifact(state_file, state_payload)
    artifacts.append(_artifact_ref(state_file, "electronic_state", backend))

    if diagnostics:
        diagnostics_file = target_dir / "spin_diagnostics.json"
        _write_json_artifact(diagnostics_file, dict(diagnostics))
        artifacts.append(_artifact_ref(diagnostics_file, "spin_diagnostics", backend))

    if state.wavefunction.inherit_between_steps:
        for candidate in sorted(target_dir.glob("*.gbw")):
            artifacts.append(_artifact_ref(candidate, "wavefunction", backend))
            break
    return artifacts


def _spin_centers(
    populations: Mapping[str, Any] | Mapping[int, float], threshold: float
) -> tuple[list[int], list[int]]:
    positive: list[int] = []
    negative: list[int] = []
    for raw_index, value in populations.items():
        try:
            atom_index = int(raw_index)
            spin_value = float(value)
        except (TypeError, ValueError):
            continue
        if spin_value >= threshold:
            positive.append(atom_index)
        elif spin_value <= -threshold:
            negative.append(atom_index)
    return sorted(positive), sorted(negative)


# ── capability dispatch ─────────────────────────────────────────────────


def backend_for_request(
    name: str,
    config: Mapping[str, Any] | None = None,
    constructor_kwargs: Mapping[str, Any] | None = None,
    *,
    acquire: Any = None,
    resources: TaskResources | None = None,
) -> Any:
    """Resolve a backend instance through a registry seam.

    A registered class is constructed with *config* / *constructor_kwargs*;
    an already-built instance passes through unchanged (legacy instance
    seams and tests).  *acquire* overrides the lookup (default: the shared
    ``cccp.backends.registry`` singleton).
    Explicit task resources override a detached constructor config; callers
    supplying an instance own its resource allocation.
    """
    if acquire is None:
        from cccp.backends.registry import get_backend as acquire

    reference = acquire(name)
    if isinstance(reference, type):
        effective_config = dict(config or {})
        if resources is not None:
            resource_config = dict(effective_config.get("resources") or {})
            executables = dict(effective_config.get("executables") or {})
            orca = dict(executables.get("orca") or {})
            if resources.nproc is not None:
                resource_config["nproc"] = resources.nproc
                orca["nproc"] = resources.nproc
            if resources.mem is not None:
                resource_config["mem"] = normalize_memory(resources.mem, default_unit="MB")
            if resources.maxcore is not None:
                orca["maxcore"] = resources.maxcore
            effective_config["resources"] = resource_config
            executables["orca"] = orca
            effective_config["executables"] = executables
        return reference(effective_config, **dict(constructor_kwargs or {}))
    return reference


def call_capability(
    backend: Any,
    capability: str,
    inputs: CalculationInputs,
    target_dir: Path | None,
    kwargs: Mapping[str, Any],
) -> QCResult:
    """Call one capability and normalize its legacy or standard result.

    A backend that does not implement *capability* (e.g. ``xtb`` has no
    ``frequency`` method) is rejected here with a structured
    ``UnsupportedCapabilityError`` before anything launches — an
    ``AttributeError`` must never escape (delta D4).
    """
    operation = getattr(backend, capability, None)
    if not callable(operation):
        raise UnsupportedCapabilityError(
            f"backend {type(backend).__name__} does not implement capability {capability!r}"
        )
    final_kwargs = dict(kwargs)
    if inputs.scf_options:
        options = dict(inputs.scf_options)
        if capability == "single_point" and inputs.electronic_state is not None:
            if inputs.electronic_state.diagnostics.stability is not StabilityMode.NONE:
                options.setdefault("stab_perform", True)
                options.setdefault("stab_restart", True)
        if options:
            final_kwargs["scf_options"] = options
    raw_result = operation(
        inputs.coordinates,
        list(inputs.symbols),
        charge=inputs.charge,
        multiplicity=inputs.multiplicity,
        output_dir=target_dir,
        **final_kwargs,
    )
    return to_qc_result(raw_result)


def artifacts_from_qc(
    qc_result: QCResult,
    backend: str,
    existing: list[ArtifactRef] | None = None,
) -> list[ArtifactRef]:
    """Collect file references exposed by a QC result without requiring files."""
    artifacts = list(existing or [])
    known_paths = {artifact.path for artifact in artifacts}
    for field_name, artifact_type in (
        ("output_file", "output"),
        ("log_file", "log"),
        ("freq_log_file", "frequency_log"),
    ):
        raw_path = getattr(qc_result, field_name, None)
        if not isinstance(raw_path, (str, Path)) or not str(raw_path):
            continue
        path = Path(raw_path)
        if path in known_paths:
            continue
        artifacts.append(
            ArtifactRef(
                path=path,
                type=artifact_type,
                checksum=_checksum(path),
                source=backend,
            )
        )
        known_paths.add(path)
    return artifacts


def error_text(error: BaseException) -> str:
    """Return a stable non-empty message for a backend exception."""
    message = str(error).strip()
    return message or type(error).__name__


_STALE_RUN_FILE_SUFFIXES: frozenset[str] = frozenset(
    {
        # ORCA guess files: a stale same-named copy makes SCF abort with
        # "Input geometry does not match current geometry" under MPI when the
        # geometry changed between batch runs.
        ".ges",
        ".gbw",
        # Residual scratch/temp artifacts that ORCA would otherwise try to
        # reuse or that shadow the regenerated run.
        ".tmp",
        ".mdci",
        ".densities",
        ".hess",
        ".property.txt",
        ".int",
        ".sharkinp",
    }
)


def clear_stale_run_files(run_dir: Path | str, output_name: str) -> None:
    """Remove stale same-named scratch/output guess files from a reused run dir.

    Run directories are stable across batch runs, so a previous run can leave
    an ``<output_name>.ges`` (or ``.gbw``) behind; ORCA then tries to reuse it
    and aborts when the geometry changed.  Deleting these files before each
    execution keeps every run independent of prior executions.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        return
    # ORCA emits both dotted (``sp_0000.out``) and underscored
    # (``sp_0000_property.txt``) artifact names; accept either separator.
    prefixes = (f"{output_name}.", f"{output_name}_")
    for path in run_dir.iterdir():
        if not path.is_file() or not path.name.startswith(prefixes):
            continue
        lower_name = path.name.lower()
        if any(
            lower_name.endswith(f"{separator}{suffix.lstrip('.')}")
            for separator in (".", "_")
            for suffix in _STALE_RUN_FILE_SUFFIXES
        ):
            try:
                path.unlink()
            except OSError:
                logger.warning("failed to remove stale run file %s", path)


def classify_failure(
    *,
    raised: BaseException | None = None,
    error_message: str | None = None,
    missing_energy: bool = False,
) -> ErrorKind:
    """Classify one scientific/runtime failure into the closed taxonomy.

    Pre-launch rejections (``TaskInputError`` / ``UnsupportedCapabilityError``
    / ``BackendUnavailableError``) are raised, never classified here.
    """
    if raised is not None:
        if isinstance(raised, TimeoutError):
            return ErrorKind.TIMEOUT
        return ErrorKind.BACKEND_FAILURE
    if missing_energy:
        return ErrorKind.PARSE_FAILURE
    text = (error_message or "").lower()
    if any(
        token in text for token in ("not converge", "did not converge", "scf_conv", "convergence")
    ):
        return ErrorKind.NOT_CONVERGED
    if "timeout" in text or "time limit" in text:
        return ErrorKind.TIMEOUT
    return ErrorKind.BACKEND_FAILURE


# ── translation-layer minimal entry (resolve_spec + render_backend_input) ─

#: ``MethodSpec`` field → ``ResolvedCalculationSpec`` field name.
_LEVEL_SPEC_FIELDS: tuple[tuple[str, str], ...] = (
    ("basis", "basis"),
    ("dispersion", "dispersion"),
    ("solvent", "solvent"),
    ("solvent_model", "solvent_model"),
    ("integration_grid", "grid"),
    ("scf", "scf_convergence"),
    ("ri_approximation", "ri_approximation"),
    ("auxiliary_basis_j", "aux_j_basis"),
    ("auxiliary_basis_c", "aux_c_basis"),
)

#: ``ResolvedCalculationSpec`` effective field → backend capability kwarg.
_SPEC_BACKEND_FIELDS: tuple[tuple[str, str], ...] = (
    ("basis", "basis"),
    ("dispersion", "dispersion"),
    ("solvent", "solvent"),
    ("solvent_model", "solvent_model"),
    ("grid", "grid"),
    ("scf_convergence", "scf_convergence"),
    ("scf_strategy", "scf_strategy"),
    ("ri_approximation", "ri_approximation"),
    ("aux_j_basis", "aux_j_basis"),
    ("aux_c_basis", "aux_c_basis"),
)


def theory_run_config(config: Mapping[str, Any] | None) -> dict[str, object] | None:
    """Flat ``theory.*`` run-config layer for ``resolve_spec`` (config default)."""
    if not isinstance(config, Mapping):
        return None
    theory = config.get("theory")
    if not isinstance(theory, Mapping):
        return None
    flat: dict[str, object] = {}
    for section in theory.values():
        if isinstance(section, Mapping):
            for key, value in section.items():
                flat.setdefault(str(key), value)
    return flat or None


def level_explicit_fields(level: Any) -> dict[str, Any]:
    """Project a ``MethodSpec`` onto ``resolve_calculation_spec`` explicit keys."""
    explicit: dict[str, Any] = {}
    for level_field, spec_field in _LEVEL_SPEC_FIELDS:
        value = getattr(level, level_field, None)
        if value is not None:
            explicit[spec_field] = value
    return explicit


def resolve_spec(
    method: str | None,
    *,
    explicit: Mapping[str, Any] | None = None,
    task_options: Mapping[str, Any] | None = None,
    run_config: Mapping[str, Any] | None = None,
) -> ResolvedCalculationSpec:
    """Translation-layer entry ① — the single parameter-resolution point.

    Thin alias of :func:`cccp.qc.resolved_spec.resolve_calculation_spec`
    (priority: explicit > task_options > run_config > method defaults).
    """
    return resolve_calculation_spec(
        method,
        explicit=explicit,
        task_options=task_options,
        run_config=run_config,
    )


def render_backend_input(
    spec: ResolvedCalculationSpec,
    *,
    method: str | None = None,
    state_scf_options: Mapping[str, Any] | None = None,
    extras: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Translation-layer entry ② — resolved spec → backend capability kwargs.

    Explicit request values pass through **verbatim** (the clamp layer is a
    UI/catalog concern and must never alter execution values).  Absent values
    are NOT invented here: method-inherent defaults are materialised by the
    backend input renderer from the same cccp method metadata (single
    source) — the pre-migration goldens freeze exactly these effective
    parameters.  Verbatim legacy residue (``output_name``, ``scf_maxiter``,
    ``route_extras``, …) passes through unchanged until the full
    translation-layer cleanup (plan todo 25).
    """
    rendered: dict[str, Any] = {}
    for key, value in (extras or {}).items():
        if value is not None:
            rendered[key] = value

    if method:
        rendered["method"] = method
    for spec_field, kwarg in _SPEC_BACKEND_FIELDS:
        resolution = spec.get(spec_field)
        if resolution is None or resolution.source != "explicit":
            continue
        value = resolution.requested
        if value is None or value == "":
            continue
        if kwarg == "ri_approximation":
            # RI is expressed through the ORCA route/aux rendering, never as
            # a literal backend kwarg value of "none".
            if str(value).lower() == "none":
                rendered.pop("ri_approximation", None)
                continue
            rendered["route_extras"] = _merge_route_extras(
                rendered.get("route_extras"), str(value)
            )
            continue
        rendered[kwarg] = value

    merged_scf: dict[str, Any] = {}
    raw_scf = rendered.get("scf_options")
    if isinstance(raw_scf, Mapping):
        merged_scf.update(raw_scf)
    for key, value in (state_scf_options or {}).items():
        merged_scf.setdefault(key, value)
    if merged_scf:
        rendered["scf_options"] = merged_scf
    elif "scf_options" in rendered:
        rendered.pop("scf_options", None)
    return rendered


def _merge_route_extras(existing: Any, token: str) -> list[str]:
    merged: list[str] = []
    if isinstance(existing, str):
        merged.append(existing)
    elif isinstance(existing, (list, tuple)):
        merged.extend(str(item) for item in existing)
    merged.append(token)
    return merged


# ── artifact / json helpers ─────────────────────────────────────────────


def qc_metadata_json(values: Mapping[str, Any]) -> dict[str, JsonValue]:
    """JSON-project a QC metadata mapping (non-JSON values dropped)."""
    result: dict[str, JsonValue] = {}
    for key, value in values.items():
        parsed = _json_value(value)
        if parsed is not None or value is None:
            result[str(key)] = parsed
    return result


def _json_mapping(values: Mapping[str, Any]) -> dict[str, JsonValue]:
    return qc_metadata_json(values)


def _json_value(value: Any) -> JsonValue | None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        parsed_items = [_json_value(item) for item in value]
        return [item for item in parsed_items if item is not None]
    if isinstance(value, Mapping):
        return _json_mapping(value)
    return None


def _write_json_artifact(path: Path, payload: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(payload), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("failed to write %s: %s", path, exc)


def _artifact_ref(path: Path, artifact_type: str, backend: str) -> ArtifactRef:
    return ArtifactRef(
        path=path,
        type=artifact_type,
        checksum=_checksum(path),
        source=backend,
    )


def _checksum(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


__all__ = [
    "CalculationInputs",
    "artifacts_from_qc",
    "assess_state_quality",
    "apply_stability_options",
    "backend_for_request",
    "build_state_scf_options",
    "call_capability",
    "classify_failure",
    "electron_count",
    "error_text",
    "level_explicit_fields",
    "load_geometry",
    "qc_metadata_json",
    "render_backend_input",
    "resolve_multiplicity",
    "resolve_spec",
    "state_result_metadata",
    "theory_run_config",
    "write_state_artifacts",
]
