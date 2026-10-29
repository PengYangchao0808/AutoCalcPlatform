"""xTB single-geometry native constrained optimization (PES2TS G2-AB3 WP-1).

Thin ACP wrapper (root AGENTS.md ANTI #17): validates the frozen
``pes2ts_xtb_constrained_opt_request_v1`` payload, adapts it onto a typed
``cccp.calculation`` task request (``legacy_adapters``), executes the
``run_xtb_constrained_opt`` task core, and publishes ACP-standard
``RESULT/`` products (result manifest v2 +
``pes2ts_xtb_constrained_opt_evidence_v1``).

Contract highlights (PES2TS ADR-0012 / G2-AB3 design):

* The optimization runs entirely inside one xTB process (ANCopt); the host
  never iterates per internal gradient evaluation.
* Success of the *calculation* (xTB ran, log + geometry captured) is kept
  separate from *convergence* of the geometry optimization. A run that
  terminates normally but prints ``FAILED TO CONVERGE GEOMETRY
  OPTIMIZATION`` completes this workflow with
  ``evidence.opt.converged_geometry=false``; the accept/reject decision is
  owned by PES2TS.
* ``TOTAL ENERGY`` of a constrained optimization includes the restraint
  potential (``E_effective``). When ``recipe.physical_sp`` is true the
  workflow additionally runs an unrestrained ``--sp`` at the final geometry
  and records it as ``energy_physical_hartree``.
* Atom indices in the request are 0-based positions in the frozen atom
  order (the request carries the full element list; the xcontrol compiler
  converts to xTB's 1-based convention internally). Map ids must never be
  used as indices by the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from acp.calculations.legacy_adapters import pes2ts_xtb_constrained_opt_to_task_request
from acp.calculations.pes.outputs import copy_xyz_atomic
from acp.core.workflow import WorkflowResult
from acp.storage.manifest import ProductKind, ResultManifest
from acp.workflows._helpers import write_result_summary
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import (
    BackendUnavailableError,
    TaskInputError,
    UnsupportedCapabilityError,
)
from cccp.calculation.tasks.xtb_constrained_opt import (
    parse_opt_log,
    run_xtb_constrained_opt as run_xtb_constrained_opt_task,
    xcontrol_fix_block,
)
from cccp.software import SoftwareNotFoundError

logger = logging.getLogger(__name__)

# ── contract constants ──────────────────────────────────────────────────

XTC_SCHEMA = "pes2ts_xtb_constrained_opt_request_v1"
"""Frozen request schema version (PES2TS ADR-0012 appendix)."""

XTC_EVIDENCE_SCHEMA = "pes2ts_xtb_constrained_opt_evidence_v1"
"""Result evidence product schema version."""

XTC_WORKFLOW = "XtbConstrainedOpt"
"""Result-manifest ``workflow`` value for this module."""

XTC_STAGE = "07_CONSTRAINED_OPT"
"""Zone-B WORK stage directory."""

XTC_DIR_NAME = "xtb_constopt_001"
"""Per-task run directory under ``WORK/07_CONSTRAINED_OPT/``."""

XTC_RESULT_DIR_RELATIVE = "pes2ts/xtb_constopt"
"""RESULT subdirectory holding the evidence + geometry products."""

XTC_STAGES: tuple[str, ...] = ("prepare", "run_constrained_opt", "physical_sp", "finalize")
"""Stage names reported to the progress reporter / WorkflowResult."""

# ── error codes ─────────────────────────────────────────────────────────

XTC_E_SCHEMA = "XTC_E_SCHEMA"
XTC_E_SOURCE = "XTC_E_SOURCE"
XTC_E_RECIPE = "XTC_E_RECIPE"
XTC_E_XTB = "XTC_E_XTB"
XTC_E_OUTPUT = "XTC_E_OUTPUT"


class XtbConstrainedOptInputError(ValueError):
    """Raised when a ``pes2ts_xtb_constrained_opt_request_v1`` payload is unusable."""


@dataclass(frozen=True)
class XtbConstrainedOptError(Exception):
    """Structured workflow error with code and message."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class XtbConstraintSpec:
    """One validated constraint (0-based atoms, target with unit)."""

    kind: str  # distance | angle | dihedral
    atoms: tuple[int, ...]
    target: float
    unit: str


@dataclass(frozen=True)
class XtbConstrainedOptRequest:
    """Validated ``pes2ts_xtb_constrained_opt_request_v1`` payload."""

    reaction_id: str
    geometry: tuple[tuple[float, float, float], ...]
    elements: tuple[str, ...]
    charge: int
    multiplicity: int
    gfn_level: int
    constraints: tuple[XtbConstraintSpec, ...]
    force_constant: float | None
    opt_level: str
    max_cycle: int | None
    fix_atoms: tuple[int, ...] | None
    physical_sp: bool
    threads: int
    timeout_seconds: int | None
    request_sha256: str | None
    plan_sha256: str | None
    point_id: str | None
    parent_frame_sha256: str | None


_VALID_OPT_LEVELS = ("crude", "sloppy", "loose", "normal", "tight", "verytight")
_VALID_KINDS = {
    "distance": (2, "angstrom"),
    "angle": (3, "degree"),
    "dihedral": (4, "degree"),
}


def _require_mapping(value: Any, field: str, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise XtbConstrainedOptInputError(
            f"[{code}] {field} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_int(value: Any, field: str, code: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise XtbConstrainedOptInputError(f"[{code}] {field} must be an int, got {value!r}")
    if minimum is not None and value < minimum:
        raise XtbConstrainedOptInputError(f"[{code}] {field} must be >= {minimum}, got {value}")
    return int(value)


def _optional_int(value: Any, field: str, code: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _require_int(value, field, code, minimum=minimum)


def _require_float(value: Any, field: str, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise XtbConstrainedOptInputError(f"[{code}] {field} must be a number, got {value!r}")
    return float(value)


def _optional_float(value: Any, field: str, code: str) -> float | None:
    if value is None:
        return None
    return _require_float(value, field, code)


def _require_geometry(payload_value: Any, n_expected: int | None, field: str, code: str):
    if not isinstance(payload_value, (list, tuple)):
        raise XtbConstrainedOptInputError(f"[{code}] {field} must be a list, got {type(payload_value).__name__}")
    rows: list[tuple[float, float, float]] = []
    for row in payload_value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise XtbConstrainedOptInputError(f"[{code}] {field} rows must be [x, y, z]")
        rows.append(
            (
                _require_float(row[0], f"{field}[x]", code),
                _require_float(row[1], f"{field}[y]", code),
                _require_float(row[2], f"{field}[z]", code),
            )
        )
    if not rows:
        raise XtbConstrainedOptInputError(f"[{code}] {field} must not be empty")
    if n_expected is not None and len(rows) != n_expected:
        raise XtbConstrainedOptInputError(
            f"[{code}] {field} has {len(rows)} rows but {n_expected} expected"
        )
    return tuple(rows)


def _validate_constraints(raw: Any, n_atoms: int) -> tuple[XtbConstraintSpec, ...]:
    if not isinstance(raw, (list, tuple)) or not raw:
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_RECIPE}] recipe.constraints must be a non-empty list"
        )
    specs: list[XtbConstraintSpec] = []
    for entry in raw:
        entry = _require_mapping(entry, "recipe.constraints[]", XTC_E_RECIPE)
        kind = entry.get("kind")
        if kind not in _VALID_KINDS:
            raise XtbConstrainedOptInputError(
                f"[{XTC_E_RECIPE}] recipe.constraints[].kind must be one of "
                f"{sorted(_VALID_KINDS)}, got {kind!r}"
            )
        arity, expected_unit = _VALID_KINDS[kind]
        atoms_raw = entry.get("atoms")
        if not isinstance(atoms_raw, (list, tuple)) or len(atoms_raw) != arity:
            raise XtbConstrainedOptInputError(
                f"[{XTC_E_RECIPE}] recipe.constraints[].atoms must list exactly "
                f"{arity} atom indices for kind={kind!r}"
            )
        atoms: list[int] = []
        for value in atoms_raw:
            index = _require_int(value, "recipe.constraints[].atoms[]", XTC_E_RECIPE, minimum=0)
            if index >= n_atoms:
                raise XtbConstrainedOptInputError(
                    f"[{XTC_E_RECIPE}] atom index {index} is out of range for "
                    f"{n_atoms} atoms (0-based positions required, never map ids)"
                )
            atoms.append(index)
        if len(set(atoms)) != arity:
            raise XtbConstrainedOptInputError(
                f"[{XTC_E_RECIPE}] recipe.constraints[].atoms must be distinct"
            )
        unit = entry.get("unit")
        if unit != expected_unit:
            raise XtbConstrainedOptInputError(
                f"[{XTC_E_RECIPE}] kind={kind!r} requires unit {expected_unit!r}, got {unit!r}"
            )
        target = _require_float(entry.get("target"), "recipe.constraints[].target", XTC_E_RECIPE)
        if target <= 0.0:
            raise XtbConstrainedOptInputError(
                f"[{XTC_E_RECIPE}] recipe.constraints[].target must be positive"
            )
        specs.append(
            XtbConstraintSpec(kind=kind, atoms=tuple(atoms), target=target, unit=unit)
        )
    return tuple(specs)


def _validate_fix_atoms(raw: Any, n_atoms: int) -> tuple[int, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_RECIPE}] recipe.fix_atoms must be a list of 0-based indices or null"
        )
    fix: list[int] = []
    for value in raw:
        index = _require_int(value, "recipe.fix_atoms[]", XTC_E_RECIPE, minimum=0)
        if index >= n_atoms:
            raise XtbConstrainedOptInputError(
                f"[{XTC_E_RECIPE}] fix_atoms index {index} is out of range for {n_atoms} atoms"
            )
        fix.append(index)
    return tuple(sorted(set(fix)))


def validate_request(payload: Any) -> XtbConstrainedOptRequest:
    """Validate a frozen ``pes2ts_xtb_constrained_opt_request_v1`` payload.

    Raises:
        XtbConstrainedOptInputError: Typed field errors; never defaults.
    """
    payload = _require_mapping(payload, "request", XTC_E_SCHEMA)
    if payload.get("schema_version") != XTC_SCHEMA:
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_SCHEMA}] schema_version must be {XTC_SCHEMA!r}, "
            f"got {payload.get('schema_version')!r}"
        )

    source = _require_mapping(payload.get("source"), "source", XTC_E_SOURCE)
    elements_raw = source.get("elements")
    if not isinstance(elements_raw, (list, tuple)) or not elements_raw:
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_SOURCE}] source.elements must be a non-empty list of symbols"
        )
    elements = tuple(str(e) for e in elements_raw)
    n_atoms = len(elements)
    geometry = _require_geometry(source.get("geometry"), n_atoms, "source.geometry", XTC_E_SOURCE)
    charge = _require_int(source.get("charge"), "source.charge", XTC_E_SOURCE)
    multiplicity = _require_int(
        source.get("multiplicity"), "source.multiplicity", XTC_E_SOURCE, minimum=1
    )

    recipe = _require_mapping(payload.get("recipe"), "recipe", XTC_E_RECIPE)
    gfn_level = _require_int(recipe.get("gfn_level"), "recipe.gfn_level", XTC_E_RECIPE, minimum=0)
    constraints = _validate_constraints(recipe.get("constraints"), n_atoms)
    force_constant = _optional_float(recipe.get("force_constant"), "recipe.force_constant", XTC_E_RECIPE)
    if force_constant is not None and force_constant <= 0.0:
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_RECIPE}] recipe.force_constant must be positive when present"
        )
    opt_level = recipe.get("opt_level", "normal")
    if opt_level not in _VALID_OPT_LEVELS:
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_RECIPE}] recipe.opt_level must be one of {_VALID_OPT_LEVELS}, got {opt_level!r}"
        )
    max_cycle = _optional_int(recipe.get("max_cycle"), "recipe.max_cycle", XTC_E_RECIPE, minimum=1)
    fix_atoms = _validate_fix_atoms(recipe.get("fix_atoms"), n_atoms)
    physical_sp_raw = recipe.get("physical_sp", False)
    if not isinstance(physical_sp_raw, bool):
        raise XtbConstrainedOptInputError(
            f"[{XTC_E_RECIPE}] recipe.physical_sp must be a boolean, got {physical_sp_raw!r}"
        )
    threads = _require_int(recipe.get("threads"), "recipe.threads", XTC_E_RECIPE, minimum=1)
    timeout_seconds = _optional_int(
        recipe.get("timeout_seconds"), "recipe.timeout_seconds", XTC_E_RECIPE, minimum=1
    )

    provenance = payload.get("provenance")
    provenance = _require_mapping(provenance, "provenance", XTC_E_SCHEMA) if provenance is not None else {}
    reaction_id_raw = payload.get("reaction_id")
    point_id_raw = provenance.get("point_id")

    return XtbConstrainedOptRequest(
        reaction_id=str(reaction_id_raw) if reaction_id_raw is not None else "",
        geometry=geometry,
        elements=elements,
        charge=charge,
        multiplicity=multiplicity,
        gfn_level=gfn_level,
        constraints=constraints,
        force_constant=force_constant,
        opt_level=opt_level,
        max_cycle=max_cycle,
        fix_atoms=fix_atoms,
        physical_sp=physical_sp_raw,
        threads=threads,
        timeout_seconds=timeout_seconds,
        request_sha256=provenance.get("request_sha256"),
        plan_sha256=provenance.get("plan_sha256"),
        point_id=str(point_id_raw) if point_id_raw is not None else None,
        parent_frame_sha256=provenance.get("parent_frame_sha256"),
    )


# Log parsing (convergence evidence) and the xcontrol ``$fix`` compiler live
# in the cccp task core; re-exported here so the frozen workflow contract
# (and its tests) keep a single import surface.
_fix_block = xcontrol_fix_block


# ── IO helpers (atomic, mirror xtb_path.py) ─────────────────────────────


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), suffix=".tmp", delete=False, mode="w", encoding="utf-8"
    )
    try:
        json.dump(_json_safe(payload), handle, indent=2, sort_keys=True, default=str, allow_nan=False)
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), suffix=".tmp", delete=False, mode="w", encoding="utf-8"
    )
    try:
        handle.write(text)
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _executable_provenance(config: Mapping[str, Any]) -> dict[str, Any]:
    """Best-effort xTB executable provenance (path + sha256 + version line)."""
    from cccp.software import detect_version, resolve_executable

    executables = config.get("executables") if isinstance(config, Mapping) else None
    entry = executables.get("xtb") if isinstance(executables, Mapping) else None
    configured = entry.get("path") if isinstance(entry, Mapping) else None
    executable = resolve_executable(
        "xtb", configured if isinstance(configured, str) and configured else None
    )
    provenance: dict[str, Any] = {
        "xtb_executable": str(executable) if executable else None,
        "xtb_executable_sha256": None,
        "xtb_version": None,
    }
    if executable is not None:
        executable_path = Path(executable)
        provenance["xtb_executable_sha256"] = _sha256_file(executable_path)
        provenance["xtb_version"] = detect_version("xtb", executable_path)
    return provenance


# ── persistence ─────────────────────────────────────────────────────────


def _persist_products(
    *,
    output_root: Path,
    workflow_status: str,
    evidence: dict[str, Any],
    optimized_xyz: Path | None,
    opt_log_source: Path,
    sp_log_source: Path | None,
    xcontrol_source: Path,
    product_metadata: dict[str, Any],
) -> tuple[Path, Path]:
    """Write RESULT products and the v2 result manifest."""
    result_dir = output_root / "RESULT"
    product_dir = result_dir / XTC_RESULT_DIR_RELATIVE
    product_dir.mkdir(parents=True, exist_ok=True)

    evidence_path = product_dir / "evidence.json"
    _write_json_atomic(evidence_path, evidence)

    copied: list[tuple[str, str, str, ProductKind]] = [
        (
            "evidence",
            "constrained-opt convergence/energy/cost evidence",
            f"{XTC_RESULT_DIR_RELATIVE}/evidence.json",
            ProductKind.REPORT,
        ),
        (
            "xcontrol",
            "exact xcontrol constraints used for the optimization",
            f"{XTC_RESULT_DIR_RELATIVE}/xcontrol.txt",
            ProductKind.FILE,
        ),
    ]
    _write_text_atomic(product_dir / "xcontrol.txt", xcontrol_source.read_text(encoding="utf-8"))

    if optimized_xyz is not None and optimized_xyz.is_file():
        copy_xyz_atomic(optimized_xyz, product_dir / "optimized.xyz")
        copied.append(
            (
                "optimized_geometry",
                "constrained-optimized geometry (atom-ordered xyz)",
                f"{XTC_RESULT_DIR_RELATIVE}/optimized.xyz",
                ProductKind.STRUCTURE,
            )
        )
    if opt_log_source is not None and Path(opt_log_source).is_file():
        _write_text_atomic(
            product_dir / "xtb_opt.log", Path(opt_log_source).read_text(encoding="utf-8")
        )
        copied.append(
            (
                "opt_log",
                "raw xTB constrained optimization log",
                f"{XTC_RESULT_DIR_RELATIVE}/xtb_opt.log",
                ProductKind.FILE,
            )
        )
    if sp_log_source is not None and Path(sp_log_source).is_file():
        _write_text_atomic(
            product_dir / "xtb_sp.log", Path(sp_log_source).read_text(encoding="utf-8")
        )
        copied.append(
            (
                "sp_log",
                "raw xTB unrestrained single-point log (physical energy)",
                f"{XTC_RESULT_DIR_RELATIVE}/xtb_sp.log",
                ProductKind.FILE,
            )
        )

    try:
        manifest = ResultManifest.read(result_dir)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = ResultManifest()
    manifest.workflow = XTC_WORKFLOW
    manifest.status = workflow_status
    for product_id, label, rel_path, kind in copied:
        manifest.add_product(
            id=product_id,
            label=label,
            path=rel_path,
            kind=kind,
            metadata=dict(product_metadata),
        )
    manifest_path = manifest.write(result_dir)

    summary_products = [
        {"label": label, "path": rel_path, "kind": "report" if "log" not in product_id else "log"}
        for product_id, label, rel_path, _kind in copied
    ]
    write_result_summary(output_root, XTC_WORKFLOW, summary_products)
    return evidence_path, manifest_path


# ── workflow entry ──────────────────────────────────────────────────────


def run_xtb_constrained_opt(
    request_payload: Mapping[str, Any],
    *,
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    progress_reporter: Any | None = None,
) -> WorkflowResult:
    """Execute one xTB native constrained optimization request.

    Args:
        request_payload: Frozen ``pes2ts_xtb_constrained_opt_request_v1``.
        output_dir: Task output root (CLI ``--output``).
        config: Merged QC config dict.
        progress_reporter: Optional :class:`ProgressReporter`.

    Returns:
        WorkflowResult with ``status="completed"`` when the calculation ran
        and evidence/products were persisted (regardless of geometry
        convergence, which is reported in the evidence); ``status="failed"``
        when the backend could not run or no log was produced.

    Raises:
        XtbConstrainedOptInputError: Invalid request payload (typed).
    """
    request = validate_request(request_payload)
    output_root = Path(output_dir).expanduser().resolve()
    run_dir = output_root / "WORK" / XTC_STAGE / XTC_DIR_NAME
    run_dir.mkdir(parents=True, exist_ok=True)

    if progress_reporter is not None:
        progress_reporter.start_stage("prepare")

    cfg: dict[str, Any] = dict(config) if config is not None else {}
    raw_resources = cfg.get("resources")
    resources = dict(raw_resources) if isinstance(raw_resources, Mapping) else {}
    resources["nproc"] = request.threads
    cfg["resources"] = resources
    # The recipe's gfn_level reaches the interface through the config's
    # theory.preoptimization knob (the interface reads it at construction).
    theory = dict(cfg.get("theory") or {})
    preopt = dict(theory.get("preoptimization") or {})
    preopt["gfn_level"] = request.gfn_level
    theory["preoptimization"] = preopt
    cfg["theory"] = theory

    if progress_reporter is not None:
        progress_reporter.complete_stage("prepare")
        progress_reporter.start_stage("run_constrained_opt")

    task_request, _binding = pes2ts_xtb_constrained_opt_to_task_request(request)
    task_request = replace(task_request, output_dir=run_dir)
    try:
        task_result = run_xtb_constrained_opt_task(
            task_request,
            context=TaskContext(config=cfg, workdir=run_dir),
        )
    except (BackendUnavailableError, UnsupportedCapabilityError, SoftwareNotFoundError) as error:
        message = f"xTB backend is unavailable: {error}"
        if progress_reporter is not None:
            progress_reporter.fail_stage("run_constrained_opt", message)
        raise XtbConstrainedOptError(code=XTC_E_XTB, message=message) from error
    except TaskInputError as error:
        if progress_reporter is not None:
            progress_reporter.fail_stage("run_constrained_opt", str(error))
        raise XtbConstrainedOptInputError(f"[{XTC_E_RECIPE}] {error}") from error

    metadata = dict(task_result.metadata or {})
    opt_meta = dict(metadata.get("opt") or {})
    sp_evidence = metadata.get("physical_sp")
    opt_dir = Path(str(metadata.get("opt_dir") or run_dir / "opt"))
    sp_dir = Path(str(metadata.get("sp_dir") or run_dir / "sp"))
    opt_log_path = opt_dir / "xtb.log"
    sp_log_path = sp_dir / "xtb_sp.log"
    xcontrol_path = opt_dir / ".xcontrol"
    optimized_xyz = opt_dir / "xtbopt.xyz"

    if task_result.status != "completed" or not opt_log_path.is_file():
        message = "; ".join(task_result.errors) or "xTB constrained optimization failed"
        if progress_reporter is not None:
            progress_reporter.fail_stage("run_constrained_opt", message)
        raise XtbConstrainedOptError(code=XTC_E_XTB, message=message)

    payload = getattr(task_result, "payload", None)
    converged_geometry = bool(getattr(payload, "converged_geometry", False))
    energy_effective = getattr(payload, "energy_effective_hartree", None)
    energy_physical = getattr(payload, "energy_physical_hartree", None)
    if progress_reporter is not None:
        progress_reporter.complete_stage("run_constrained_opt")

    if progress_reporter is not None:
        progress_reporter.start_stage("physical_sp")
        if isinstance(sp_evidence, Mapping):
            skipped = sp_evidence.get("skipped_reason")
            progress_reporter.complete_stage(
                "physical_sp", {"skipped_reason": skipped} if skipped else None
            )
        else:
            progress_reporter.complete_stage("physical_sp", {"skipped": "not_requested"})

    if progress_reporter is not None:
        progress_reporter.start_stage("finalize")

    final_coords = task_result.coordinates
    geometry_sha256 = None
    if final_coords is not None:
        geometry_lines = [f"{len(request.elements)}", "optimized"]
        for sym, (x, y, z) in zip(request.elements, final_coords):
            geometry_lines.append(f"{sym} {float(x):.6f} {float(y):.6f} {float(z):.6f}")
        geometry_sha256 = _sha256_text("\n".join(geometry_lines) + "\n")

    log_text = opt_log_path.read_text(encoding="utf-8", errors="replace")
    evidence = {
        "schema_version": XTC_EVIDENCE_SCHEMA,
        "workflow": XTC_WORKFLOW,
        "reaction_id": request.reaction_id,
        "point_id": request.point_id,
        "request_sha256": request.request_sha256,
        "plan_sha256": request.plan_sha256,
        "parent_frame_sha256": request.parent_frame_sha256,
        "input_geometry_rows": len(request.geometry),
        "opt": {
            **opt_meta,
            "energy_effective_source": "xtb_opt_log_TOTAL_ENERGY_includes_restraint_potential",
            "n_energy_evaluations_estimate": opt_meta.get("n_optimization_cycles_logged"),
            "n_energy_evaluations_estimate_source": "log_optimization_cycle_lines",
            "wall_seconds": metadata.get("opt_wall_seconds"),
            "timeout_applied_seconds": metadata.get("timeout_applied_seconds"),
            "xcontrol_sha256": (
                _sha256_text(xcontrol_path.read_text(encoding="utf-8"))
                if xcontrol_path.is_file()
                else None
            ),
            "log_sha256": _sha256_text(log_text),
            "optimized_geometry_sha256": geometry_sha256,
        },
        "physical_sp": sp_evidence,
        "provenance": _executable_provenance(cfg),
    }

    optimized_out = optimized_xyz if final_coords is not None else None
    evidence_path, manifest_path = _persist_products(
        output_root=output_root,
        workflow_status="completed",
        evidence=evidence,
        optimized_xyz=optimized_out,
        opt_log_source=opt_log_path,
        sp_log_source=sp_log_path if (sp_log_path.is_file() and isinstance(sp_evidence, Mapping)) else None,
        xcontrol_source=xcontrol_path,
        product_metadata={
            "reaction_id": request.reaction_id,
            "point_id": request.point_id,
            "request_sha256": request.request_sha256,
            "converged_geometry": converged_geometry,
        },
    )

    if progress_reporter is not None:
        progress_reporter.complete_stage("finalize")

    return WorkflowResult(
        status="completed",
        stages_completed=list(XTC_STAGES),
        metadata={
            "output_dir": str(output_root),
            "workflow": XTC_WORKFLOW,
            "reaction_id": request.reaction_id,
            "point_id": request.point_id,
            "request_sha256": request.request_sha256,
            "converged_geometry": converged_geometry,
            "energy_effective_hartree": energy_effective,
            "energy_physical_hartree": energy_physical,
            "evidence_path": str(evidence_path),
            "result_manifest_path": str(manifest_path),
        },
    )


__all__ = [
    "XTC_DIR_NAME",
    "XTC_E_OUTPUT",
    "XTC_E_RECIPE",
    "XTC_E_SCHEMA",
    "XTC_E_SOURCE",
    "XTC_E_XTB",
    "XTC_EVIDENCE_SCHEMA",
    "XTC_RESULT_DIR_RELATIVE",
    "XTC_SCHEMA",
    "XTC_STAGE",
    "XTC_STAGES",
    "XTC_WORKFLOW",
    "XtbConstrainedOptError",
    "XtbConstrainedOptInputError",
    "XtbConstrainedOptRequest",
    "XtbConstraintSpec",
    "parse_opt_log",
    "run_xtb_constrained_opt",
    "validate_request",
]
