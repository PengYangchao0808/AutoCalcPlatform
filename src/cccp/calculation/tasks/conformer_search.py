"""Conformer-search task core (plan todo 42 — CREST ensemble execution).

Pipeline: request validation → two-step backend selection (semantic + this
call's runtime precheck) → translation (geometry → input XYZ, typed options
→ capability kwargs) → interface execution → typed
:class:`~cccp.calculation.results.ConformerSearchPayload`.

Scientific scope only: one conformer search producing an ensemble artifact,
a conformer count and an energy table whose rows keep the original ensemble
frame indices.  CREST→CENSO composition, energy-window ensemble filtering
and protocol screening policies are ACP-side workflow concerns and are
deliberately NOT implemented here (``energy_window`` is passed through as
the CREST ``-ewin`` run knob only).

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.calculation._common import (
    backend_for_request,
    classify_failure,
    error_text,
    load_geometry,
    qc_metadata_json,
)
from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import ArtifactRef, JsonValue, Provenance
from cccp.calculation.errors import TaskInputError, UnsupportedCapabilityError
from cccp.calculation.requests import (
    ConformerSearchOptions,
    TaskKind,
    TaskRequest,
    validate_request,
)
from cccp.calculation.results import (
    ConformerEnergy,
    ConformerSearchPayload,
    ErrorKind,
    TaskResult,
)
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.utils.file_io import write_xyz

logger = logging.getLogger(__name__)

_BACKEND_FAILURES = (OSError, RuntimeError, ValueError)

#: Semantic capability → backend execution method and its call shape.
#: ``geometry`` = (coordinates, symbols, …) entry; ``path`` = single-XYZ
#: ``search(initial_xyz, …)`` entry (CENSO/Molclus ConformerSearcher shape).
_CAPABILITY_METHODS: tuple[tuple[str, str], ...] = (
    ("run_conformer_search", "geometry"),
    ("search", "path"),
    ("conformer_search", "geometry"),
)

_ENERGY_TITLE_RE = re.compile(
    r"(?:energy|e)\s*[:=]\s*([-+]?\d+(?:\.\d+)?(?:[EeDd][+-]?\d+)?)",
    re.IGNORECASE,
)
_TITLE_FLOAT_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?(?:[EeDd][+-]?\d+)?$")


# ── shared P2 helpers (also used by md_sampling/clustering/xtb_path_search) ──


@dataclass(frozen=True, slots=True)
class EnsembleFrame:
    """One parsed ensemble frame (``index`` keeps the original file order)."""

    index: int
    symbols: tuple[str, ...]
    coordinates: NDArray[np.float64]
    energy_hartree: float | None = None
    title: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityOutcome:
    """Normalised view of one legacy capability result shape."""

    success: bool
    error_message: str | None
    output_file: Path | None
    metadata: dict[str, object]
    raw: object


def artifact_ref(path: Path | str, artifact_type: str, source: str) -> ArtifactRef:
    """Build one artifact reference with a content checksum when readable."""
    import hashlib

    resolved = Path(path)
    checksum = ""
    if resolved.is_file():
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        checksum = f"sha256:{digest}"
    return ArtifactRef(path=resolved, type=artifact_type, checksum=checksum, source=source)


def parse_ensemble_frames(path: Path) -> tuple[list[EnsembleFrame], list[str]]:
    """Parse a multi-frame XYZ into per-frame records (original indices kept).

    Frames that fail to parse are skipped and reported in the returned error
    list; the surviving frames keep their original file indices (record
    identity).  Per-frame energies are read from the title line when present
    (``Energy: X`` / ``energy = X`` / bare number).
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    frames: list[EnsembleFrame] = []
    errors: list[str] = []
    cursor = 0
    index = 0
    while cursor < len(lines):
        header = lines[cursor].strip()
        if not header:
            cursor += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            errors.append(f"frame {index}: unparsable atom-count line {header!r}")
            index += 1
            cursor += 1
            continue
        if atom_count <= 0:
            break
        end = cursor + 2 + atom_count
        if end > len(lines):
            errors.append(f"frame {index}: truncated frame ({atom_count} atoms declared)")
            break
        title = lines[cursor + 1].strip() if cursor + 1 < len(lines) else ""
        symbols: list[str] = []
        rows: list[tuple[float, float, float]] = []
        bad = False
        for line in lines[cursor + 2 : end]:
            parts = line.split()
            if len(parts) < 4:
                errors.append(f"frame {index}: unparsable coordinate line {line!r}")
                bad = True
                break
            try:
                rows.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                errors.append(f"frame {index}: unparsable coordinate line {line!r}")
                bad = True
                break
            symbols.append(parts[0])
        if not bad and len(rows) == atom_count:
            frames.append(
                EnsembleFrame(
                    index=index,
                    symbols=tuple(symbols),
                    coordinates=np.asarray(rows, dtype=np.float64),
                    energy_hartree=energy_from_title(title),
                    title=title,
                )
            )
        elif not bad:
            errors.append(f"frame {index}: atom count mismatch")
        index += 1
        cursor = end
    return frames, errors


def energy_from_title(title: str) -> float | None:
    """Extract a per-frame energy from a title line (None when absent)."""
    text = title.strip()
    if not text:
        return None
    match = _ENERGY_TITLE_RE.search(text)
    if match is not None:
        try:
            return float(match.group(1).replace("D", "E").replace("d", "e"))
        except ValueError:
            return None
    if _TITLE_FLOAT_RE.match(text):
        try:
            return float(text.replace("D", "E").replace("d", "e"))
        except ValueError:
            return None
    return None


def normalize_capability_result(raw: object) -> CapabilityOutcome:
    """Fold legacy capability result shapes (``QCResult`` / ``Path``) into one record."""
    if isinstance(raw, (str, Path)):
        return CapabilityOutcome(
            success=True, error_message=None, output_file=Path(raw), metadata={}, raw=raw
        )
    success = bool(getattr(raw, "success", False))
    error_message = getattr(raw, "error_message", None)
    output_raw = getattr(raw, "output_file", None)
    output_file = Path(output_raw) if isinstance(output_raw, (str, Path)) else None
    if output_file is None:
        traj_raw = getattr(raw, "metadata", {})
        if isinstance(traj_raw, Mapping):
            candidate = traj_raw.get("trajectory_file")
            if isinstance(candidate, (str, Path)):
                output_file = Path(candidate)
    metadata_raw = getattr(raw, "metadata", {})
    metadata = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    return CapabilityOutcome(
        success=success,
        error_message=str(error_message) if error_message else None,
        output_file=output_file,
        metadata=metadata,
        raw=raw,
    )


def resolve_operation(backend: object, methods: Sequence[tuple[str, str]]) -> tuple[str, str, Any]:
    """Return the first callable capability method on *backend* (shape included)."""
    for name, shape in methods:
        operation = getattr(backend, name, None)
        if callable(operation):
            return name, shape, operation
    known = ", ".join(name for name, _ in methods)
    raise UnsupportedCapabilityError(
        f"backend {type(backend).__name__} does not implement any of: {known}"
    )


def write_input_xyz(
    target_dir: Path,
    coordinates: NDArray[np.float64],
    symbols: Sequence[str],
    name: str,
) -> Path:
    """Materialise one input geometry as ``<target_dir>/<name>`` (translation step)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / name
    write_xyz(path, np.asarray(coordinates, dtype=np.float64), list(symbols))
    return path


def execution_provenance(backend: str, request: TaskRequest) -> Provenance:
    """Provenance record free of platform identity (scientific fields only)."""
    return Provenance(
        backend=backend,
        method=request.level.method,
        version="unknown",
        input_signature=str(request.structure.path) if request.structure else "",
    )


# ── task core ───────────────────────────────────────────────────────────


def run_conformer_search(
    request: TaskRequest,
    *,
    context: TaskContext | None = None,
) -> TaskResult:
    """Run one conformer search (ensemble + conformer count + energy table).

    Three-state semantics (``cccp.calculation.requests.P2_TASK_CONTRACTS``):
    success = completed with ``ensemble_ref`` and ``conformer_count >= 1``;
    partial = ``complete=False`` keeping valid conformers at their original
    ensemble indices; empty = zero conformers is a failed result with
    ``error_kind=backend_failure`` (never an empty success).
    """
    validate_request(request)
    if request.task is not TaskKind.CONFORMER_SEARCH:
        message = (
            f"run_conformer_search requires task 'conformer_search', got {request.task.value!r}"
        )
        raise TaskInputError(message)
    if request.options is not None and not isinstance(request.options, ConformerSearchOptions):
        message = "conformer_search requires ConformerSearchOptions"
        raise TaskInputError(message)
    ctx = resolve_context(request, context)

    selection = select_semantic(request)
    if ctx.backend is None:
        selection = precheck_runtime(selection, ctx)

    structure = request.structure
    if structure is None:
        message = "task 'conformer_search' requires a structure input"
        raise TaskInputError(message)
    path = structure.path
    if path is not None and not path.is_absolute():
        path = ctx.input_root() / path
    coordinates, symbols = load_geometry(
        path=path,
        coordinates=structure.coordinates,
        symbols=structure.symbols,
        elements=structure.elements,
    )

    options = request.options if isinstance(request.options, ConformerSearchOptions) else None
    kwargs: dict[str, Any] = dict(ctx.capability_extras or {})
    if options is not None:
        if options.energy_window is not None:
            kwargs["energy_window"] = options.energy_window
        if options.gfn_level is not None:
            kwargs["gfn_level"] = options.gfn_level
    if request.resources.timeout_s is not None:
        kwargs.setdefault("timeout", int(request.resources.timeout_s))

    backend = (
        ctx.backend
        if ctx.backend is not None
        else backend_for_request(selection.backend, config=ctx.config)
    )
    backend_label = str(getattr(backend, "name", selection.backend) or selection.backend)
    target_dir = Path(
        request.output_dir if request.output_dir is not None else (ctx.workdir or Path.cwd())
    )

    _, shape, operation = resolve_operation(backend, _CAPABILITY_METHODS)
    try:
        if shape == "path":
            input_xyz = write_input_xyz(target_dir, coordinates, symbols, "conformer_input.xyz")
            raw_result = operation(
                input_xyz,
                charge=request.charge,
                multiplicity=request.multiplicity,
                output_dir=target_dir,
                **kwargs,
            )
        else:
            raw_result = operation(
                coordinates,
                list(symbols),
                charge=request.charge,
                multiplicity=request.multiplicity,
                output_dir=target_dir,
                **kwargs,
            )
    except _BACKEND_FAILURES as error:
        return TaskResult(
            task=TaskKind.CONFORMER_SEARCH,
            status="failed",
            complete=False,
            error_kind=classify_failure(raised=error),
            errors=(error_text(error),),
            provenance=execution_provenance(backend_label, request),
        )

    outcome = normalize_capability_result(raw_result)
    metadata: dict[str, JsonValue] = qc_metadata_json(
        {key: value for key, value in outcome.metadata.items() if key != "trajectory_file"}
    )
    ensemble_path = outcome.output_file
    frames: list[EnsembleFrame] = []
    parse_errors: list[str] = []
    if ensemble_path is not None and ensemble_path.is_file():
        frames, parse_errors = parse_ensemble_frames(ensemble_path)

    errors: list[str] = []
    if outcome.error_message:
        errors.append(outcome.error_message)
    errors.extend(parse_errors)

    artifacts = []
    ensemble_ref: ArtifactRef | None = None
    if ensemble_path is not None:
        ensemble_ref = artifact_ref(ensemble_path, "ensemble", backend_label)
        artifacts.append(ensemble_ref)

    if not frames:
        # Empty semantics: zero conformers is a failure, never an empty success.
        message = errors[0] if errors else "conformer search returned an empty ensemble"
        return TaskResult(
            task=TaskKind.CONFORMER_SEARCH,
            status="failed",
            complete=False,
            error_kind=ErrorKind.BACKEND_FAILURE,
            errors=(message,),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            metadata=metadata,
        )

    energy_table = tuple(
        ConformerEnergy(
            conf_id=f"conf_{frame.index}",
            frame_index=frame.index,
            energy_hartree=frame.energy_hartree,
        )
        for frame in frames
    )
    payload = ConformerSearchPayload(
        ensemble_ref=ensemble_ref,
        conformer_count=len(energy_table),
        energy_table=energy_table,
    )
    metadata["conformer_count"] = len(energy_table)

    partial = bool(errors) or not outcome.success
    if partial:
        return TaskResult(
            task=TaskKind.CONFORMER_SEARCH,
            status="failed",
            complete=False,
            error_kind=None,
            errors=tuple(errors) or ("conformer search completed with partial output",),
            artifacts=tuple(artifacts),
            provenance=execution_provenance(backend_label, request),
            payload=payload,
            metadata=metadata,
        )
    return TaskResult(
        task=TaskKind.CONFORMER_SEARCH,
        status="completed",
        complete=True,
        errors=(),
        artifacts=tuple(artifacts),
        provenance=execution_provenance(backend_label, request),
        payload=payload,
        metadata=metadata,
    )


__all__ = ["run_conformer_search"]
