"""ORCA single-point gradient workflow (PES2TS → ACP, work unit X4′-A).

Runs an ORCA ``EnGrad`` single-point gradient from a frozen
``pes2ts_orca_gradient_request_v1`` payload delivered via ``--gradient-config``
and persists ACP-standard ``RESULT/`` products (manifest v2 + machine-readable
gradient/geometry/energy products). Gradient values are the ORCA-printed
energy gradient dE/dX in Hartree/bohr — never forces, never fabricated.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from acp.backends.orca import BOHR_ANGSTROM
from acp.calculations.legacy_adapters import pes2ts_orca_gradient_to_task_request
from acp.core.workflow import WorkflowResult
from acp.storage.manifest import ProductKind, ResultManifest
from acp.workflows._helpers import write_result_summary
from cccp.calculation.context import TaskContext
from cccp.calculation.errors import (
    BackendUnavailableError,
    TaskInputError,
    UnsupportedCapabilityError,
)
from cccp.calculation.tasks.orca_gradient import run_orca_gradient as run_orca_gradient_task
from cccp.software import SoftwareNotFoundError

logger = logging.getLogger(__name__)

ORCA_GRADIENT_SCHEMA = "pes2ts_orca_gradient_request_v1"
ORCA_GRADIENT_WORKFLOW = "OrcaGradient"
ORCA_GRADIENT_STAGE = "08_GRADIENT"
ORCA_GRADIENT_DIR_NAME = "orca_gradient_001"
ORCA_GRADIENT_STAGES: tuple[str, ...] = ("prepare", "run_gradient", "finalize")
GRADIENT_PRODUCT_RELATIVE_PATH = "RESULT/gradient/gradient.json"
GEOMETRY_PRODUCT_RELATIVE_PATH = "RESULT/geometry/geometry.xyz"
ENERGY_PRODUCT_RELATIVE_PATH = "RESULT/energy/energy.json"
GRADIENT_PRODUCT_SCHEMA = "orca_gradient_product_v1"
ENERGY_PRODUCT_SCHEMA = "orca_energy_product_v1"

ORCA_GRADIENT_E_SCHEMA = "ORCA_GRADIENT_E_SCHEMA"
ORCA_GRADIENT_E_GEOMETRY = "ORCA_GRADIENT_E_GEOMETRY"
ORCA_GRADIENT_E_ELECTRONIC_STATE = "ORCA_GRADIENT_E_ELECTRONIC_STATE"
ORCA_GRADIENT_E_BACKEND = "ORCA_GRADIENT_E_BACKEND"
ORCA_GRADIENT_E_GRADIENT = "ORCA_GRADIENT_E_GRADIENT"
ORCA_GRADIENT_E_OUTPUT = "ORCA_GRADIENT_E_OUTPUT"

_ELEMENTS = frozenset(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I "
    "Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt "
    "Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr "
    "Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split()
)


class OrcaGradientInputError(ValueError):
    """Frozen-request validation failure (maps to CLI exit code 2)."""


@dataclass(frozen=True)
class OrcaGradientError(Exception):
    """Typed workflow/execution failure (maps to CLI exit code 1)."""

    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class OrcaGradientRequest:
    """Validated frozen ``pes2ts_orca_gradient_request_v1`` payload."""

    schema_version: str
    coordinates: NDArray[np.float64]
    symbols: tuple[str, ...]
    method: str
    basis: str
    charge: int
    multiplicity: int
    route_extras: tuple[str, ...]
    timeout_seconds: int | None
    nproc: int | None
    extra_blocks: tuple[str, ...]
    scf_convergence: str | None
    output_name: str
    request_sha256: str

    @property
    def xyz_text(self) -> str:
        """XYZ rendering of the validated geometry (Angstrom)."""
        lines = [str(len(self.symbols)), "OrcaGradient input geometry"]
        for symbol, coord in zip(self.symbols, self.coordinates):
            lines.append(f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}")
        return "\n".join(lines) + "\n"


def _request_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_xyz_text(text: str) -> tuple[NDArray[np.float64], tuple[str, ...]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_GEOMETRY}] xyz text too short")
    try:
        natoms = int(lines[0].strip())
    except ValueError as exc:
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_GEOMETRY}] xyz first line must be an atom count"
        ) from exc
    if natoms <= 0 or len(lines) < natoms + 2:
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_GEOMETRY}] xyz declares {natoms} atoms but has "
            f"{max(0, len(lines) - 2)} coordinate lines"
        )
    coordinates: list[list[float]] = []
    symbols: list[str] = []
    for line in lines[2 : 2 + natoms]:
        fields = line.split()
        if len(fields) != 4 or fields[0] not in _ELEMENTS:
            raise OrcaGradientInputError(
                f"[{ORCA_GRADIENT_E_GEOMETRY}] invalid xyz atom line: {line!r}"
            )
        try:
            coords = [float(v) for v in fields[1:]]
        except ValueError as exc:
            raise OrcaGradientInputError(
                f"[{ORCA_GRADIENT_E_GEOMETRY}] non-numeric xyz coordinate: {line!r}"
            ) from exc
        if not all(math.isfinite(v) for v in coords):
            raise OrcaGradientInputError(
                f"[{ORCA_GRADIENT_E_GEOMETRY}] non-finite xyz coordinate: {line!r}"
            )
        coordinates.append(coords)
        symbols.append(fields[0])
    return np.asarray(coordinates, dtype=np.float64), tuple(symbols)


def _validate_gradient_request(payload: Mapping[str, Any]) -> OrcaGradientRequest:
    """Validate the frozen request strictly — never default recipe knobs."""
    if not isinstance(payload, Mapping):
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_SCHEMA}] request must be a JSON object")
    schema_version = payload.get("schema_version")
    if schema_version != ORCA_GRADIENT_SCHEMA:
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_SCHEMA}] schema_version must be "
            f"{ORCA_GRADIENT_SCHEMA!r}, got {schema_version!r}"
        )

    xyz_text = payload.get("xyz")
    geometry = payload.get("geometry")
    elements = payload.get("elements")
    if xyz_text is not None:
        if not isinstance(xyz_text, str) or not xyz_text.strip():
            raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_GEOMETRY}] xyz must be non-empty text")
        if geometry is not None or elements is not None:
            raise OrcaGradientInputError(
                f"[{ORCA_GRADIENT_E_GEOMETRY}] provide exactly one of xyz or "
                "geometry+elements, not both"
            )
        coordinates, symbols = _parse_xyz_text(xyz_text)
    else:
        if not isinstance(geometry, list) or not geometry:
            raise OrcaGradientInputError(
                f"[{ORCA_GRADIENT_E_GEOMETRY}] request requires xyz text or "
                "a geometry list of [x, y, z] rows"
            )
        if not isinstance(elements, list) or len(elements) != len(geometry):
            raise OrcaGradientInputError(
                f"[{ORCA_GRADIENT_E_GEOMETRY}] elements must be a list matching the geometry length"
            )
        rows: list[list[float]] = []
        symbols_list: list[str] = []
        for row, element in zip(geometry, elements):
            if not isinstance(row, (list, tuple)) or len(row) != 3:
                raise OrcaGradientInputError(
                    f"[{ORCA_GRADIENT_E_GEOMETRY}] geometry rows must be [x, y, z]: {row!r}"
                )
            if not isinstance(element, str) or element not in _ELEMENTS:
                raise OrcaGradientInputError(
                    f"[{ORCA_GRADIENT_E_GEOMETRY}] invalid element symbol: {element!r}"
                )
            try:
                coords = [float(v) for v in row]
            except (TypeError, ValueError) as exc:
                raise OrcaGradientInputError(
                    f"[{ORCA_GRADIENT_E_GEOMETRY}] non-numeric geometry row: {row!r}"
                ) from exc
            if not all(math.isfinite(v) for v in coords):
                raise OrcaGradientInputError(
                    f"[{ORCA_GRADIENT_E_GEOMETRY}] non-finite geometry row: {row!r}"
                )
            rows.append(coords)
            symbols_list.append(element)
        coordinates = np.asarray(rows, dtype=np.float64)
        symbols = tuple(symbols_list)

    method = payload.get("method")
    if not isinstance(method, str) or not method.strip():
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_ELECTRONIC_STATE}] method is required")
    if "basis" not in payload or not isinstance(payload.get("basis"), str):
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_ELECTRONIC_STATE}] basis is required "
            "(empty string is legal for GFN/composite methods)"
        )
    basis = payload["basis"]

    charge = payload.get("charge")
    if isinstance(charge, bool) or not isinstance(charge, int):
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_ELECTRONIC_STATE}] charge must be an int")
    multiplicity = payload.get("multiplicity")
    if isinstance(multiplicity, bool) or not isinstance(multiplicity, int) or multiplicity < 1:
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_ELECTRONIC_STATE}] multiplicity must be an int >= 1"
        )

    route_extras_raw = payload.get("route_extras") or []
    if not isinstance(route_extras_raw, list) or not all(
        isinstance(x, str) for x in route_extras_raw
    ):
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_SCHEMA}] route_extras must be a list of strings"
        )
    timeout_seconds = payload.get("timeout_seconds")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or timeout_seconds < 1
    ):
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_SCHEMA}] timeout_seconds must be an int >= 1"
        )
    nproc = payload.get("nproc")
    if nproc is not None and (isinstance(nproc, bool) or not isinstance(nproc, int) or nproc < 1):
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_SCHEMA}] nproc must be an int >= 1")
    extra_blocks_raw = payload.get("extra_blocks") or []
    if not isinstance(extra_blocks_raw, list) or not all(
        isinstance(x, str) for x in extra_blocks_raw
    ):
        raise OrcaGradientInputError(
            f"[{ORCA_GRADIENT_E_SCHEMA}] extra_blocks must be a list of strings"
        )
    scf_convergence = payload.get("scf_convergence")
    if scf_convergence is not None and not isinstance(scf_convergence, str):
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_SCHEMA}] scf_convergence must be a string")
    output_name = payload.get("output_name") or "grad"
    if not isinstance(output_name, str) or not output_name.strip():
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_SCHEMA}] output_name must be non-empty")

    return OrcaGradientRequest(
        schema_version=ORCA_GRADIENT_SCHEMA,
        coordinates=coordinates,
        symbols=symbols,
        method=method.strip(),
        basis=basis,
        charge=charge,
        multiplicity=multiplicity,
        route_extras=tuple(route_extras_raw),
        timeout_seconds=timeout_seconds,
        nproc=nproc,
        extra_blocks=tuple(extra_blocks_raw),
        scf_convergence=scf_convergence,
        output_name=output_name.strip(),
        request_sha256=_request_digest(dict(payload)),
    )


def _writable_config_layer(value: Any, layer: str) -> dict[str, Any]:
    """Detach a config layer for mutation — missing/null becomes a fresh dict.

    Args:
        value: raw layer value from the caller config; ``None`` (absent or
            explicit null) is normalized into a new writable mapping.
        layer: dotted layer name used in the error message.

    Returns:
        Deep-copied dict that is safe to mutate without touching the caller.

    Raises:
        OrcaGradientInputError: layer present but not a JSON object.
    """
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    raise OrcaGradientInputError(
        f"[{ORCA_GRADIENT_E_SCHEMA}] config {layer} must be a JSON object, got {value!r}"
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent), suffix=".tmp", delete=False, mode="w", encoding="utf-8"
    )
    try:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str, allow_nan=False)
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


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not Path(path).is_file():
        return None
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _orca_executable_provenance(config: Mapping[str, Any]) -> dict[str, Any]:
    from cccp.software import detect_version, resolve_executable

    executables = config.get("executables") if isinstance(config, Mapping) else None
    entry = executables.get("orca") if isinstance(executables, Mapping) else None
    configured = entry.get("path") if isinstance(entry, Mapping) else None
    executable = resolve_executable(
        "orca", configured if isinstance(configured, str) and configured else None
    )
    provenance: dict[str, Any] = {"orca_executable": str(executable) if executable else None}
    try:
        provenance["orca_executable_sha256"] = _sha256_file(
            Path(executable) if executable else None
        )
    except OSError:
        provenance["orca_executable_sha256"] = None
    try:
        provenance["orca_version"] = detect_version("orca", executable)
    except (OSError, RuntimeError, ValueError):
        provenance["orca_version"] = None
    return provenance


def _persist_orca_gradient_outputs(
    *,
    output_root: Path,
    request: OrcaGradientRequest,
    energy: float,
    gradient: NDArray[np.float64],
    gradient_source: str,
    provenance: dict[str, Any],
) -> tuple[Path, Path]:
    result_dir = output_root / "RESULT"
    geometry_dir = result_dir / "geometry"
    gradient_dir = result_dir / "gradient"
    energy_dir = result_dir / "energy"
    geometry_dir.mkdir(parents=True, exist_ok=True)
    gradient_dir.mkdir(parents=True, exist_ok=True)
    energy_dir.mkdir(parents=True, exist_ok=True)

    geometry_path = geometry_dir / "geometry.xyz"
    _write_text_atomic(geometry_path, request.xyz_text)

    gradient_bohr = gradient.tolist()
    gradient_angstrom = (gradient / BOHR_ANGSTROM).tolist()
    product_meta = {
        "request_sha256": request.request_sha256,
        "schema_version": ORCA_GRADIENT_SCHEMA,
        "workflow": ORCA_GRADIENT_WORKFLOW,
        "gradient_source": gradient_source,
        "gradient_unit": "hartree/bohr",
        "gradient_convention": "energy_gradient_dE_dX",
        "gradient_sign_note": (
            "ORCA-printed energy gradient dE/dX; forces = -gradient; no sign flip applied"
        ),
        "energy_hartree": energy,
        "method": request.method,
        "basis": request.basis,
        "charge": request.charge,
        "multiplicity": request.multiplicity,
        **provenance,
    }
    gradient_path = gradient_dir / "gradient.json"
    _write_json_atomic(
        gradient_path,
        {
            **product_meta,
            "schema_version": GRADIENT_PRODUCT_SCHEMA,
            "gradient_hartree_per_bohr": gradient_bohr,
            "gradient_hartree_per_angstrom": gradient_angstrom,
            "gradient_conversion": "hartree_per_bohr / 0.529177210903",
            "symbols": list(request.symbols),
        },
    )
    energy_path = energy_dir / "energy.json"
    _write_json_atomic(
        energy_path,
        {
            **product_meta,
            "schema_version": ENERGY_PRODUCT_SCHEMA,
            "workflow": ORCA_GRADIENT_WORKFLOW,
            "energy_hartree": energy,
            "energy_unit": "hartree",
            "method": request.method,
            "basis": request.basis,
            "charge": request.charge,
            "multiplicity": request.multiplicity,
        },
    )

    try:
        manifest = ResultManifest.read(result_dir)
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError, TypeError, KeyError):
        manifest = ResultManifest()
    manifest.workflow = ORCA_GRADIENT_WORKFLOW
    manifest.status = "completed"
    manifest.add_product(
        id="gradient",
        label="ORCA single-point gradient (orca_gradient_product_v1)",
        path="gradient/gradient.json",
        kind=ProductKind.FILE,
        metadata=product_meta,
    )
    manifest.add_product(
        id="geometry",
        label="ORCA gradient input geometry",
        path="geometry/geometry.xyz",
        kind=ProductKind.STRUCTURE,
        metadata={"request_sha256": request.request_sha256},
    )
    manifest.add_product(
        id="energy",
        label="ORCA single-point energy",
        path="energy/energy.json",
        kind=ProductKind.ENERGY_REPORT,
        metadata=product_meta,
    )
    manifest_path = manifest.write(result_dir)

    write_result_summary(
        output_root,
        ORCA_GRADIENT_WORKFLOW,
        [
            {
                "label": "ORCA gradient",
                "path": GRADIENT_PRODUCT_RELATIVE_PATH,
                "kind": "report",
            },
            {
                "label": "ORCA gradient geometry",
                "path": GEOMETRY_PRODUCT_RELATIVE_PATH,
                "kind": "xyz",
            },
            {
                "label": "ORCA gradient energy",
                "path": ENERGY_PRODUCT_RELATIVE_PATH,
                "kind": "report",
            },
        ],
    )
    return gradient_path, manifest_path


def run_orca_gradient(
    *,
    request: Mapping[str, Any],
    output_dir: str | Path,
    config: Mapping[str, Any] | None = None,
    progress_reporter: Any | None = None,
) -> WorkflowResult:
    """Run an ORCA single-point gradient and persist ACP-standard products.

    Validates the frozen ``pes2ts_orca_gradient_request_v1`` payload, runs the
    ``EnGrad`` single point through :class:`acp.backends.orca.ORCABackend`,
    and writes ``RESULT/result_manifest.json`` (v2, ``status="completed"``)
    plus ``RESULT/gradient/gradient.json`` (per-atom gradient + units +
    provenance), ``RESULT/geometry/geometry.xyz``, and
    ``RESULT/energy/energy.json``. Failure raises a typed error — a
    ``completed`` manifest is never written on failure.
    """
    validated = _validate_gradient_request(request)
    output_root = Path(output_dir).expanduser()
    run_dir = output_root / "WORK" / ORCA_GRADIENT_STAGE / ORCA_GRADIENT_DIR_NAME
    run_dir.mkdir(parents=True, exist_ok=True)

    if progress_reporter is not None:
        progress_reporter.start_stage("prepare")
    _write_text_atomic(run_dir / "input.xyz", validated.xyz_text)

    # Detach the whole config so neither this function's writes nor any
    # downstream TaskContext use can ever pollute the caller's dict.
    cfg: dict[str, Any] = copy.deepcopy(dict(config)) if config is not None else {}
    resources = _writable_config_layer(cfg.get("resources"), "resources")
    if validated.nproc is not None:
        resources["nproc"] = validated.nproc
        executables = _writable_config_layer(cfg.get("executables"), "executables")
        orca_entry = _writable_config_layer(executables.get("orca"), "executables.orca")
        orca_entry["nproc"] = validated.nproc
        executables["orca"] = orca_entry
        cfg["executables"] = executables
    cfg["resources"] = resources
    if validated.timeout_seconds is not None:
        opt_control = _writable_config_layer(
            cfg.get("optimization_control"), "optimization_control"
        )
        timeout_cfg = _writable_config_layer(
            opt_control.get("timeout"), "optimization_control.timeout"
        )
        timeout_cfg["default_seconds"] = validated.timeout_seconds
        opt_control["timeout"] = timeout_cfg
        cfg["optimization_control"] = opt_control
    if progress_reporter is not None:
        progress_reporter.complete_stage("prepare")

    if progress_reporter is not None:
        progress_reporter.start_stage("run_gradient")
    task_request, _binding = pes2ts_orca_gradient_to_task_request(validated)
    task_request = replace(task_request, output_dir=run_dir)
    try:
        task_result = run_orca_gradient_task(
            task_request,
            context=TaskContext(config=cfg, workdir=run_dir),
        )
    except (BackendUnavailableError, UnsupportedCapabilityError, SoftwareNotFoundError) as error:
        raise OrcaGradientError(
            code=ORCA_GRADIENT_E_BACKEND,
            message=f"ORCA backend unavailable: {error}",
        ) from error
    except TaskInputError as error:
        raise OrcaGradientInputError(f"[{ORCA_GRADIENT_E_ELECTRONIC_STATE}] {error}") from error

    payload = getattr(task_result, "payload", None)
    rows = list(getattr(payload, "gradients", ()) or ())
    energy = getattr(task_result, "energy_hartree", None)
    if energy is None:
        energy = getattr(payload, "energy_hartree", None)
    if task_result.status != "completed" or not rows or energy is None:
        message = "; ".join(task_result.errors) or "ORCA gradient failed"
        if _is_unavailable_failure(message):
            raise OrcaGradientError(
                code=ORCA_GRADIENT_E_BACKEND,
                message=f"ORCA backend unavailable: {message}",
            )
        raise OrcaGradientError(
            code=ORCA_GRADIENT_E_GRADIENT,
            message=message,
        )
    gradient = np.asarray(rows, dtype=np.float64)
    gradient_source = _gradient_source_from_task(task_result)
    if progress_reporter is not None:
        progress_reporter.complete_stage("run_gradient")

    if progress_reporter is not None:
        progress_reporter.start_stage("finalize")
    provenance = {
        "request_sha256": validated.request_sha256,
        "gradient_source": gradient_source,
        "run_dir": str(run_dir),
        "input_file": _artifact_path(task_result, "output"),
        "log_file": _artifact_path(task_result, "log"),
        **_orca_executable_provenance(cfg),
    }
    try:
        gradient_path, manifest_path = _persist_orca_gradient_outputs(
            output_root=output_root,
            request=validated,
            energy=float(energy),
            gradient=gradient,
            gradient_source=gradient_source or "unknown",
            provenance=provenance,
        )
    except OSError as exc:
        raise OrcaGradientError(
            code=ORCA_GRADIENT_E_OUTPUT,
            message=f"failed to persist OrcaGradient outputs: {exc}",
        ) from exc
    if progress_reporter is not None:
        progress_reporter.complete_stage("finalize")

    return WorkflowResult(
        status="completed",
        stages_completed=list(ORCA_GRADIENT_STAGES),
        metadata={
            "output_dir": str(output_root),
            "workflow": ORCA_GRADIENT_WORKFLOW,
            "request_sha256": validated.request_sha256,
            "energy_hartree": float(energy),
            "gradient_unit": getattr(payload, "gradient_unit", "hartree/bohr"),
            "gradient_convention": getattr(payload, "gradient_convention", "energy_gradient_dE_dX"),
            "gradient_source": gradient_source,
            "run_dir": str(run_dir),
            "gradient_product_path": str(gradient_path),
            "result_manifest_path": str(manifest_path),
            "orca_executable": provenance.get("orca_executable"),
            "orca_executable_sha256": provenance.get("orca_executable_sha256"),
            "orca_version": provenance.get("orca_version"),
        },
    )


def _artifact_path(task_result: Any, artifact_type: str) -> str | None:
    for artifact in getattr(task_result, "artifacts", ()) or ():
        if getattr(artifact, "type", None) == artifact_type:
            return str(artifact.path)
    return None


def _gradient_source_from_task(task_result: Any) -> str | None:
    for artifact in getattr(task_result, "artifacts", ()) or ():
        if getattr(artifact, "type", None) == "engrad":
            return f"engrad_file:{Path(str(artifact.path)).name}"
    payload = getattr(task_result, "payload", None)
    if getattr(payload, "gradients", None):
        return "output_block:CARTESIAN GRADIENT"
    return None


def _is_unavailable_failure(message: str) -> bool:
    lowered = message.lower()
    if "gradient" in lowered:
        return False
    return "executable" in lowered or "unavailable" in lowered or "not found" in lowered


__all__ = [
    "ENERGY_PRODUCT_RELATIVE_PATH",
    "ENERGY_PRODUCT_SCHEMA",
    "GEOMETRY_PRODUCT_RELATIVE_PATH",
    "GRADIENT_PRODUCT_RELATIVE_PATH",
    "GRADIENT_PRODUCT_SCHEMA",
    "ORCA_GRADIENT_DIR_NAME",
    "ORCA_GRADIENT_E_BACKEND",
    "ORCA_GRADIENT_E_ELECTRONIC_STATE",
    "ORCA_GRADIENT_E_GEOMETRY",
    "ORCA_GRADIENT_E_GRADIENT",
    "ORCA_GRADIENT_E_OUTPUT",
    "ORCA_GRADIENT_E_SCHEMA",
    "ORCA_GRADIENT_SCHEMA",
    "ORCA_GRADIENT_STAGE",
    "ORCA_GRADIENT_STAGES",
    "ORCA_GRADIENT_WORKFLOW",
    "OrcaGradientError",
    "OrcaGradientInputError",
    "OrcaGradientRequest",
    "run_orca_gradient",
]
