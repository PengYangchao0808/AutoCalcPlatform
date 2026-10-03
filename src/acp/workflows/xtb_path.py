"""GFN2-xTB PATH metadynamics workflow (PES2TS → ACP execution unification).

Implements work unit X1′-B: accepts the frozen ``pes2ts_xtb_path_request_v1``
payload, executes ``xtb --path`` through the ACP xTB backend, and persists
ACP-standard ``RESULT/`` products (result manifest v2 + ``pes_profile_v2``).

The recipe (``path.inp`` text, ``--gfn``/``--uhf``, charge/multiplicity,
threads, timeout, seed, extra args) is owned by PES2TS — this workflow
validates and forwards it verbatim and never fills defaults.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.backends.registry import get_backend
from acp.calculations.pes.outputs import PES_PROFILE_RELATIVE_PATH, copy_xyz_atomic
from acp.calculations.pes.path_analysis import (
    PathProfile,
    build_xtb_path_profile,
    compute_path_arclength,
)
from acp.core.workflow import WorkflowResult
from acp.storage.manifest import ProductKind, ResultManifest
from acp.workflows._helpers import write_result_summary

logger = logging.getLogger(__name__)

# ── contract constants ──────────────────────────────────────────────────

XTB_PATH_SCHEMA = "pes2ts_xtb_path_request_v1"
"""Frozen request schema version (PES2TS ADR-0002 appendix A)."""

XTB_PATH_WORKFLOW = "XtbPathSearch"
"""Result-manifest ``workflow`` value for this module."""

XTB_PATH_STAGE = "07_PATH"
"""Zone-B WORK stage shared with PESsearch path intermediates."""

XTB_PATH_DIR_NAME = "xtb_path_001"
"""Per-task xTB PATH run directory under ``WORK/07_PATH/``."""

XTB_PATH_PES_DIR_RELATIVE = str(Path(PES_PROFILE_RELATIVE_PATH).parent)
"""``RESULT/pes_search`` — profile/frames/trajectory home (scan_dir value)."""

XTB_PATH_TRAJECTORY_NAME = "xtbpath.xyz"
"""Raw multi-frame trajectory filename under ``RESULT/pes_search/``."""

XTB_PATH_FRAME_PATTERN = "path_frame_%03d.xyz"
"""Per-frame XYZ filename pattern under ``RESULT/pes_search/path_frames/``."""

XTB_PATH_STAGES: tuple[str, ...] = ("prepare", "run_path_search", "finalize")
"""Stage names reported to the progress reporter / WorkflowResult."""

# ── error codes ─────────────────────────────────────────────────────────

XTB_PATH_E_SCHEMA = "XTB_PATH_E_SCHEMA"
"""Wrong or missing ``schema_version``."""

XTB_PATH_E_SOURCE = "XTB_PATH_E_SOURCE"
"""Missing/empty start or end XYZ text (or bad ``source_type``)."""

XTB_PATH_E_CHARGE = "XTB_PATH_E_CHARGE"
"""Charge/multiplicity missing or not ints."""

XTB_PATH_E_RECIPE = "XTB_PATH_E_RECIPE"
"""Missing/invalid recipe fields (``path_inp_text``/gfn/uhf/threads/...)."""

XTB_PATH_E_XTB = "XTB_PATH_E_XTB"
"""xTB backend failed, unavailable, or produced no frames."""

XTB_PATH_E_OUTPUT = "XTB_PATH_E_OUTPUT"
"""xTB succeeded but required outputs are missing/malformed."""


class XtbPathInputError(ValueError):
    """Raised when a ``pes2ts_xtb_path_request_v1`` payload is unusable."""


@dataclass(frozen=True)
class XtbPathSearchError(Exception):
    """Structured xTB path-search error with code and message."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class XtbPathRequest:
    """Validated ``pes2ts_xtb_path_request_v1`` payload (recipe never defaulted)."""

    reaction_id: str
    start_xyz_text: str
    end_xyz_text: str
    charge: int
    multiplicity: int
    path_inp_text: str
    gfn_level: int
    uhf: int
    threads: int
    timeout_seconds: int | None
    seed: int | None
    extra_args: tuple[str, ...]
    request_sha256: str | None = None
    config_digest: str | None = None
    adapter_version: str | None = None
    plan_sha256: str | None = None


# ── request validation ──────────────────────────────────────────────────


def _require_mapping(value: Any, field: str, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise XtbPathInputError(
            f"[{code}] {field} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_nonempty_str(value: Any, field: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise XtbPathInputError(f"[{code}] {field} must be a non-empty string, got {value!r}")
    return value


def _require_int(value: Any, field: str, code: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise XtbPathInputError(f"[{code}] {field} must be an int, got {value!r}")
    if minimum is not None and value < minimum:
        raise XtbPathInputError(f"[{code}] {field} must be >= {minimum}, got {value}")
    return int(value)


def _optional_int(value: Any, field: str, code: str, *, minimum: int | None = None) -> int | None:
    if value is None:
        return None
    return _require_int(value, field, code, minimum=minimum)


def _require_str_list(value: Any, field: str, code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise XtbPathInputError(
            f"[{code}] {field} must be a list of strings, got {type(value).__name__}"
        )
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise XtbPathInputError(f"[{code}] {field} entries must be strings, got {entry!r}")
        items.append(entry)
    return tuple(items)


def _optional_provenance_str(provenance: Mapping[str, Any], key: str) -> str | None:
    value = provenance.get(key)
    return str(value) if isinstance(value, str) and value else None


def _validate_path_request(path_request: Mapping[str, Any]) -> XtbPathRequest:
    """Validate a frozen ``pes2ts_xtb_path_request_v1`` payload.

    Raises:
        XtbPathInputError: Typed field errors; never silently defaults.
    """
    payload = _require_mapping(path_request, "path_request", XTB_PATH_E_SCHEMA)
    schema_version = payload.get("schema_version")
    if schema_version != XTB_PATH_SCHEMA:
        raise XtbPathInputError(
            f"[{XTB_PATH_E_SCHEMA}] schema_version must be {XTB_PATH_SCHEMA!r}, "
            f"got {schema_version!r}"
        )

    source = _require_mapping(payload.get("source"), "source", XTB_PATH_E_SOURCE)
    source_type = source.get("source_type")
    if source_type is not None and source_type != "xyz_text_pair":
        raise XtbPathInputError(
            f"[{XTB_PATH_E_SOURCE}] source.source_type must be 'xyz_text_pair', got {source_type!r}"
        )
    start_xyz_text = _require_nonempty_str(
        source.get("start_xyz"), "source.start_xyz", XTB_PATH_E_SOURCE
    )
    end_xyz_text = _require_nonempty_str(source.get("end_xyz"), "source.end_xyz", XTB_PATH_E_SOURCE)
    charge = _require_int(source.get("charge"), "source.charge", XTB_PATH_E_CHARGE)
    multiplicity = _require_int(
        source.get("multiplicity"), "source.multiplicity", XTB_PATH_E_CHARGE, minimum=1
    )

    recipe = _require_mapping(payload.get("recipe"), "recipe", XTB_PATH_E_RECIPE)
    path_inp_text = _require_nonempty_str(
        recipe.get("path_inp_text"), "recipe.path_inp_text", XTB_PATH_E_RECIPE
    )
    gfn_level = _require_int(
        recipe.get("gfn_level"), "recipe.gfn_level", XTB_PATH_E_RECIPE, minimum=1
    )
    uhf = _require_int(recipe.get("uhf"), "recipe.uhf", XTB_PATH_E_RECIPE, minimum=0)
    threads = _require_int(recipe.get("threads"), "recipe.threads", XTB_PATH_E_RECIPE, minimum=1)
    timeout_seconds = _optional_int(
        recipe.get("timeout_seconds"),
        "recipe.timeout_seconds",
        XTB_PATH_E_RECIPE,
        minimum=1,
    )
    seed = _optional_int(recipe.get("seed"), "recipe.seed", XTB_PATH_E_RECIPE)
    extra_args = _require_str_list(recipe.get("extra_args"), "recipe.extra_args", XTB_PATH_E_RECIPE)

    provenance_raw = payload.get("provenance")
    provenance: Mapping[str, Any] = provenance_raw if isinstance(provenance_raw, Mapping) else {}
    reaction_id_raw = payload.get("reaction_id")
    reaction_id = str(reaction_id_raw) if reaction_id_raw is not None else ""

    return XtbPathRequest(
        reaction_id=reaction_id,
        start_xyz_text=start_xyz_text,
        end_xyz_text=end_xyz_text,
        charge=charge,
        multiplicity=multiplicity,
        path_inp_text=path_inp_text,
        gfn_level=gfn_level,
        uhf=uhf,
        threads=threads,
        timeout_seconds=timeout_seconds,
        seed=seed,
        extra_args=extra_args,
        request_sha256=_optional_provenance_str(provenance, "request_sha256"),
        config_digest=_optional_provenance_str(provenance, "config_digest"),
        adapter_version=_optional_provenance_str(provenance, "adapter_version"),
        plan_sha256=_optional_provenance_str(provenance, "plan_sha256"),
    )


# ── small IO / hashing helpers (atomic writes, mirror outputs.py style) ─


def _json_safe(value: Any) -> Any:
    """Recursively replace non-finite floats (NaN/±Inf) with ``None``."""
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically (temporary file followed by ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        json.dump(
            _json_safe(payload), handle, indent=2, sort_keys=True, default=str, allow_nan=False
        )
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
    """Write text atomically (temporary file followed by ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
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


def _executable_provenance(backend: Any) -> dict[str, Any]:
    """Best-effort xTB executable provenance (path + sha256 + version line)."""
    interface = getattr(backend, "_path_interface", None)
    executable = getattr(interface, "executable", None)
    if executable is None:
        from cccp.software import resolve_executable

        executable = resolve_executable("xtb")
    provenance: dict[str, Any] = {
        "xtb_executable": str(executable) if executable else None,
        "xtb_executable_sha256": None,
        "xtb_version": None,
    }
    if executable is not None:
        executable_path = Path(executable)
        provenance["xtb_executable_sha256"] = _sha256_file(executable_path)
        from cccp.software import detect_version

        provenance["xtb_version"] = detect_version("xtb", executable_path)
    return provenance


# ── profile serialization (PathProfile → pes_profile_v2 payload) ────────


def _path_frame_payload(
    evidence: Any,
    *,
    geometry_path: str,
    cumulative_arclength: float | None,
) -> dict[str, Any]:
    """Serialise one :class:`PathFrameEvidence` as a viewer-ready frame dict.

    Carries both the PESsearch frame contract fields (``index`` /
    ``geometry_path`` / ``scan_energy_hartree`` / ``reaction_progress``) that
    ``normalize_pes_profile`` + ``S2FrameModel`` expect, and the raw
    xTB path-analysis evidence fields.
    """
    progress = float(evidence.progress)
    return {
        "index": int(evidence.frame_index),
        "target_coordinate": progress,
        "actual_coordinate": progress,
        "coordinate_unit": "progress",
        "geometry_path": geometry_path,
        "scan_energy_hartree": evidence.energy_hartree,
        "single_point_energy_hartree": None,
        "optimization_converged": True,
        "frame_role": "path_frame",
        "single_point_status": "skipped",
        "source_log": "",
        "energy_hartree": evidence.energy_hartree,
        "relative_energy_kcal_mol": evidence.relative_energy_kcal_mol,
        "progress": progress,
        "topology_valid": bool(evidence.topology_valid),
        "topology_reason": evidence.topology_reason,
        "rmsd_to_product": evidence.rmsd_to_product,
        "neighbor_rmsd": evidence.neighbor_rmsd,
        "gradient_proxy": evidence.gradient_proxy,
        "curvature_proxy": evidence.curvature_proxy,
        "cumulative_arclength_A": cumulative_arclength,
        "reaction_progress": progress,
        "step_rmsd_A": evidence.neighbor_rmsd,
        "source": evidence.source,
    }


def _profile_payload(
    profile: PathProfile,
    *,
    frames: list[dict[str, Any]],
    energies_hartree: list[float | None],
    request: XtbPathRequest,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Build the ``pes_profile_v2`` document for an xTB PATH run."""
    relative_energies = [frame.get("relative_energy_kcal_mol") for frame in frames]
    return {
        "schema_version": "pes_profile_v2",
        "selection_mode": "manual_only",
        "workflow": XTB_PATH_WORKFLOW,
        "mode": "xtb_path",
        "status": "completed",
        "source": profile.source,
        "coordinate": {},
        "coordinates": [],
        "selection": {},
        "protocol": {
            "engine": "xtb",
            "method": "path_metadynamics",
            "gfn_level": request.gfn_level,
            "uhf": request.uhf,
            "threads": request.threads,
            "timeout_seconds": request.timeout_seconds,
            "seed": request.seed,
            "extra_args": list(request.extra_args),
        },
        "optimization_level": {},
        "scan_dir": XTB_PATH_PES_DIR_RELATIVE,
        "frames": frames,
        "profile": {
            "energy_source": "xtb_path",
            "unit": "kcal/mol",
            "reference_index": 0,
            "relative_energies_kcal_mol": relative_energies,
            "raw_hartree": list(energies_hartree),
            "sp_incomplete": False,
        },
        "quality": {
            "status": "completed",
            "scan_complete": bool(profile.complete),
            "sp_incomplete": False,
            "needs_review": not profile.complete,
            "notes": [],
            "constraints_satisfied": True,
            "frame_count": profile.frame_count,
            "excluded_frames": list(profile.excluded_frames),
            "endpoint_direction": profile.endpoint_direction,
        },
        "ts_candidates": [],
        "int_candidates": [],
        "recommendations": {"ts": [], "intermediates": []},
        "frames_count": len(frames),
        "candidate_structures": {},
        "provenance": dict(provenance),
        "path_profile": {
            "source": profile.source,
            "frame_count": profile.frame_count,
            "complete": bool(profile.complete),
            "endpoint_direction": profile.endpoint_direction,
            "excluded_frames": list(profile.excluded_frames),
            "topology_valid_intervals": [
                list(interval) for interval in profile.topology_valid_intervals
            ],
            "forming_bonds": [list(pair) for pair in profile.forming_bonds],
            "source_provenance": dict(profile.source_provenance),
        },
    }


# ── persistence ─────────────────────────────────────────────────────────


def _persist_xtb_path_outputs(
    *,
    output_root: Path,
    request: XtbPathRequest,
    result: Any,
    product_xyz: Path,
    provenance: dict[str, Any],
) -> tuple[Path, Path, Path]:
    """Write RESULT products for a successful xTB PATH run.

    Returns:
        ``(pes_profile_path, result_manifest_path, trajectory_path)``.

    Raises:
        XtbPathSearchError: When the raw trajectory is missing (no
            ``status="completed"`` manifest is written in that case).
    """
    result_dir = output_root / "RESULT"
    pes_dir = result_dir / "pes_search"
    frames_dir = pes_dir / "path_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    frame_paths: list[Path] = []
    for index, source_path in enumerate(result.frame_paths):
        target = frames_dir / (XTB_PATH_FRAME_PATTERN % index)
        copy_xyz_atomic(Path(source_path), target)
        frame_paths.append(target)

    trajectory_source = Path(result.trajectory_file) if result.trajectory_file else None
    if trajectory_source is None or not trajectory_source.is_file():
        raise XtbPathSearchError(
            code=XTB_PATH_E_OUTPUT,
            message="xTB path trajectory file is missing after a successful path_search",
        )
    trajectory_target = pes_dir / XTB_PATH_TRAJECTORY_NAME
    copy_xyz_atomic(trajectory_source, trajectory_target)

    energies_hartree: list[float | None] = [None] * len(frame_paths)
    for index, value in enumerate(list(result.energies_hartree)[: len(frame_paths)]):
        energies_hartree[index] = None if value is None else float(value)

    profile = build_xtb_path_profile(
        frame_paths=frame_paths,
        energies_hartree=energies_hartree,
        forming_bonds=(),
        product_xyz=product_xyz,
        off_path_indices=(),
        source_provenance=dict(provenance),
    )
    try:
        arclength = compute_path_arclength(frame_paths)
    except (ValueError, TypeError, OSError, RuntimeError):
        arclength = None

    frame_payloads = [
        _path_frame_payload(
            evidence,
            geometry_path=f"path_frames/{XTB_PATH_FRAME_PATTERN % index}",
            cumulative_arclength=(
                float(arclength[index])
                if arclength is not None and index < len(arclength)
                else None
            ),
        )
        for index, evidence in enumerate(profile.frames)
    ]
    payload = _profile_payload(
        profile,
        frames=frame_payloads,
        energies_hartree=energies_hartree,
        request=request,
        provenance=provenance,
    )
    profile_path = pes_dir / "pes_profile.json"
    _write_json_atomic(profile_path, payload)

    product_meta = dict(provenance)
    try:
        manifest = ResultManifest.read(result_dir)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = ResultManifest()
    manifest.workflow = XTB_PATH_WORKFLOW
    manifest.status = "completed"
    manifest.add_product(
        id="pes_profile",
        label="xTB path energy profile (pes_profile_v2)",
        path="pes_search/pes_profile.json",
        kind=ProductKind.PES_PROFILE,
        metadata=product_meta,
    )
    manifest.add_product(
        id="trajectory",
        label="xTB path raw multi-frame trajectory",
        path=f"pes_search/{XTB_PATH_TRAJECTORY_NAME}",
        kind=ProductKind.TRAJECTORY,
        metadata=product_meta,
    )
    for index in range(len(frame_payloads)):
        manifest.add_product(
            id=f"path_frame_{index:03d}",
            label=f"xTB path frame {index}",
            path=f"pes_search/path_frames/{XTB_PATH_FRAME_PATTERN % index}",
            kind=ProductKind.STRUCTURE,
            metadata={**product_meta, "frame_index": index},
        )
    manifest_path = manifest.write(result_dir)

    summary_products: list[dict[str, Any]] = [
        {
            "label": "xTB path energy profile",
            "path": f"{XTB_PATH_PES_DIR_RELATIVE}/pes_profile.json",
            "kind": "report",
        },
        {
            "label": "xTB path raw trajectory",
            "path": f"{XTB_PATH_PES_DIR_RELATIVE}/{XTB_PATH_TRAJECTORY_NAME}",
            "kind": "xyz",
        },
    ]
    summary_products.extend(
        {
            "label": f"xTB path frame {index}",
            "path": f"{XTB_PATH_PES_DIR_RELATIVE}/path_frames/{XTB_PATH_FRAME_PATTERN % index}",
            "kind": "xyz",
        }
        for index in range(len(frame_payloads))
    )
    write_result_summary(output_root, XTB_PATH_WORKFLOW, summary_products)
    return profile_path, manifest_path, trajectory_target


# ── workflow entry ──────────────────────────────────────────────────────


def run_xtb_path_search(
    path_request: Mapping[str, Any],
    *,
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    progress_reporter: Any | None = None,
) -> WorkflowResult:
    """Execute a GFN2-xTB PATH metadynamics request (``pes2ts_xtb_path_request_v1``).

    Validates the frozen request (typed errors, never defaults the recipe),
    materialises ``start.xyz``/``end.xyz`` into
    ``WORK/07_PATH/xtb_path_001/``, runs the xTB backend ``path_search``
    with the recipe's ``path_inp_text``/``gfn_level``/``uhf``/``threads``/
    ``timeout``/``seed``/``extra_args``, and persists ACP-standard products:

    * ``RESULT/result_manifest.json`` (v2, ``workflow="XtbPathSearch"``) with
      ``pes_profile`` / ``trajectory`` / per-frame ``structure`` products;
    * ``RESULT/pes_search/pes_profile.json`` (``pes_profile_v2``,
      ``source="xtb_peb"``);
    * ``RESULT/pes_search/path_frames/path_frame_%03d.xyz``;
    * ``RESULT/pes_search/xtbpath.xyz`` (raw multi-frame trajectory).

    Args:
        path_request: Frozen ``pes2ts_xtb_path_request_v1`` payload.
        output_dir: Task output root (CLI ``--output`` / scheduler task dir).
        config: Merged QC config dict (``executables``/``resources``/...).
        progress_reporter: Optional :class:`ProgressReporter`.

    Returns:
        A :class:`WorkflowResult` with ``status="completed"`` on success.

    Raises:
        XtbPathInputError: Invalid request payload (typed field errors).
        XtbPathSearchError: xTB failed or outputs missing/malformed; no
            ``status="completed"`` manifest is written.
    """
    request = _validate_path_request(path_request)
    output_root = Path(output_dir).expanduser()
    run_dir = output_root / "WORK" / XTB_PATH_STAGE / XTB_PATH_DIR_NAME
    run_dir.mkdir(parents=True, exist_ok=True)

    if progress_reporter is not None:
        progress_reporter.start_stage("prepare")
    start_xyz = run_dir / "start.xyz"
    end_xyz = run_dir / "end.xyz"
    _write_text_atomic(start_xyz, request.start_xyz_text)
    _write_text_atomic(end_xyz, request.end_xyz_text)

    cfg: dict[str, Any] = dict(config) if config is not None else {}
    raw_resources = cfg.get("resources")
    resources = dict(raw_resources) if isinstance(raw_resources, Mapping) else {}
    resources["nproc"] = request.threads
    cfg["resources"] = resources
    if progress_reporter is not None:
        progress_reporter.complete_stage("prepare")

    if progress_reporter is not None:
        progress_reporter.start_stage("run_path_search")
    backend = get_backend("xtb")(cfg)
    if not backend.is_available():
        message = "xTB backend is not available; configure executables.xtb.path or install xtb"
        if progress_reporter is not None:
            progress_reporter.fail_stage("run_path_search", message)
        raise XtbPathSearchError(code=XTB_PATH_E_XTB, message=message)

    result = backend.path_search(
        start_xyz,
        end_xyz,
        run_dir,
        charge=request.charge,
        multiplicity=request.multiplicity,
        uhf=request.uhf,
        gfn_level=request.gfn_level,
        timeout=request.timeout_seconds,
        path_inp_text=request.path_inp_text,
        extra_args=list(request.extra_args),
        seed=request.seed,
    )
    if progress_reporter is not None:
        if result.success:
            progress_reporter.complete_stage("run_path_search")
        else:
            progress_reporter.fail_stage(
                "run_path_search", str(result.error_message or "xTB path search failed")
            )

    if not result.success or not result.frame_paths:
        raise XtbPathSearchError(
            code=XTB_PATH_E_XTB,
            message=str(result.error_message or "xTB path search failed without frames"),
        )

    provenance: dict[str, Any] = {
        "reaction_id": request.reaction_id,
        "request_sha256": request.request_sha256,
        "config_digest": request.config_digest,
        "adapter_version": request.adapter_version,
        "plan_sha256": request.plan_sha256,
        "path_inp_sha256": _sha256_text(request.path_inp_text),
        "run_dir": str(run_dir),
        **_executable_provenance(backend),
    }

    if progress_reporter is not None:
        progress_reporter.start_stage("finalize")
    profile_path, manifest_path, trajectory_path = _persist_xtb_path_outputs(
        output_root=output_root,
        request=request,
        result=result,
        product_xyz=end_xyz,
        provenance=provenance,
    )
    if progress_reporter is not None:
        progress_reporter.complete_stage("finalize")

    return WorkflowResult(
        status="completed",
        stages_completed=list(XTB_PATH_STAGES),
        metadata={
            "output_dir": str(output_root),
            "workflow": XTB_PATH_WORKFLOW,
            "reaction_id": request.reaction_id,
            "request_sha256": request.request_sha256,
            "frames_count": len(result.frame_paths),
            "pes_profile_path": str(profile_path),
            "result_manifest_path": str(manifest_path),
            "trajectory_path": str(trajectory_path),
            "run_dir": str(run_dir),
            "xtb_executable": provenance.get("xtb_executable"),
            "xtb_executable_sha256": provenance.get("xtb_executable_sha256"),
            "xtb_version": provenance.get("xtb_version"),
        },
    )


__all__ = [
    "XTB_PATH_DIR_NAME",
    "XTB_PATH_E_CHARGE",
    "XTB_PATH_E_OUTPUT",
    "XTB_PATH_E_RECIPE",
    "XTB_PATH_E_SCHEMA",
    "XTB_PATH_E_SOURCE",
    "XTB_PATH_E_XTB",
    "XTB_PATH_FRAME_PATTERN",
    "XTB_PATH_PES_DIR_RELATIVE",
    "XTB_PATH_SCHEMA",
    "XTB_PATH_STAGE",
    "XTB_PATH_STAGES",
    "XTB_PATH_TRAJECTORY_NAME",
    "XTB_PATH_WORKFLOW",
    "XtbPathInputError",
    "XtbPathRequest",
    "XtbPathSearchError",
    "run_xtb_path_search",
]
