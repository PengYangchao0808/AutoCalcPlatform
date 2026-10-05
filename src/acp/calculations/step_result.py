"""V02 — durable per-step scientific results and resume-source resolution.

Contract C (shared with the publication seam) fixes the persistence order of
one executed step:

1. ``step_result.json`` + its artifact digests are written first (durable
   science, atomically);
2. the checkpoint then *references* the result (``StepState.result_ref``);
3. the platform manifests are published;
4. the publish-complete marker flips.

``step_result.json`` self-certifies a step: ``schema_version`` +
``step_identity`` (the todo-10 v2 science identity, or the batch item's
resolved ``item_cache_key``) + per-artifact ``sha256`` digests.  Platform
execution identity (``job_id``/``attempt``/``code_release``) is recorded but
never part of the science hash.

:func:`resolve_resume_source` is the ONE parser of ``resume_source.json``
shared by the executor and the batch engine: ``continue``/edit-recalculate
read the archived attempt it points to, ``rerun`` never sees the receipt
(it is archived away before the reset).  Compatibility (protocol markers of
the old attempt) is decided *before* archiving via
:func:`check_resume_protocol` and persisted as ``compatible`` on the
receipt; readers refuse adoption when it is ``False``.

Unknown-field policy everywhere: readers ignore unknown keys and fall back
to field defaults for type-invalid values (a newer writer stays readable).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "STEP_RESULT_FILENAME",
    "STEP_RESULT_SCHEMA_VERSION",
    "RESUME_SOURCE_FILENAME",
    "SUPPORTED_STEP_RESULT_SCHEMAS",
    "ResumeSource",
    "check_resume_protocol",
    "dependency_artifacts",
    "file_sha256",
    "json_safe",
    "locate_recorded_file",
    "portable_path",
    "read_step_result",
    "resolve_resume_source",
    "verify_step_result",
    "write_step_result",
]

STEP_RESULT_FILENAME: Final = "step_result.json"
RESUME_SOURCE_FILENAME: Final = "resume_source.json"
#: ``step_result.json`` schema written by this release.
STEP_RESULT_SCHEMA_VERSION: Final = 1
#: Schema versions a reader may adopt (older/newer → conservative recompute).
SUPPORTED_STEP_RESULT_SCHEMAS: Final[frozenset[int]] = frozenset({1})

_CHECKPOINT_FILENAME: Final = "checkpoint.json"
_IDENTITY_SCHEMA_V2: Final = 2

#: Resource keys whose value is a dependency artifact LOCATION.
_DEPENDENCY_ARTIFACT_KEYS: Final[frozenset[str]] = frozenset(
    {"freq_log_path", "geometry_file", "hessian_file", "coordinates_file"}
)


# ── content binding ──────────────────────────────────────────────────────


def file_sha256(path: Path | str) -> str | None:
    """Return the sha256 hex digest of *path*, or ``None`` when unreadable."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def portable_path(path: Path | str, root: Path | str | None) -> str:
    """Return *path* relative to *root* when inside it (POSIX), else absolute.

    Relative paths survive attempt archiving: ``attempts/<n>/`` mirrors the
    task tree, so the same recorded path resolves under either root.
    """
    target = Path(path)
    if root is None:
        return target.as_posix()
    try:
        return target.resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return target.as_posix()


def locate_recorded_file(roots: Sequence[Path], recorded: str) -> Path | None:
    """Resolve a recorded artifact path against *roots* (first hit wins).

    Absolute records are checked as-is; relative records are joined onto
    each root in order — the archived attempt first, then the active task
    root (partial archives keep artifacts in place).
    """
    candidate = Path(recorded)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    seen: set[str] = set()
    for root in roots:
        base = Path(root)
        key = str(base)
        if key in seen:
            continue
        seen.add(key)
        target = base / candidate
        if target.is_file():
            return target
    return None


def json_safe(value: Any) -> Any:
    """Coerce *value* to the JSON-safe subset (drop everything else).

    ``None``/``bool``/``int``/``float``/``str`` pass through, ``Path`` and
    ``Enum`` become their value, mappings/sequences recurse; any other type
    is dropped (``None``) — the persisted ``metadata`` is a *subset*.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, raw in value.items():
            safe = json_safe(raw)
            if safe is not None or raw is None:
                out[str(key)] = safe
        return out
    if isinstance(value, (list, tuple)):
        items: list[Any] = []
        for raw in value:
            safe = json_safe(raw)
            if safe is not None or raw is None:
                items.append(safe)
        return items
    return None


# ── step_result.json I/O ─────────────────────────────────────────────────


def write_step_result(path: Path | str, payload: Mapping[str, Any]) -> str:
    """Atomically write one ``step_result.json``; returns the file's sha256."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, target)
    digest = file_sha256(target)
    if digest is None:  # pragma: no cover — file was just written
        raise OSError(f"step_result.json unreadable after write: {target}")
    return digest


def read_step_result(path: Path | str) -> dict[str, Any] | None:
    """Read a ``step_result.json`` payload; unreadable/absent → ``None``."""
    target = Path(path)
    if not target.is_file():
        return None
    try:
        payload: Any = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("unreadable step_result at %s", target)
        return None
    return payload if isinstance(payload, dict) else None


def dependency_artifacts(
    resources: Mapping[str, Any], root: Path | str | None
) -> list[dict[str, Any]]:
    """Record dependency artifact locations + content digests for one step."""
    entries: list[dict[str, Any]] = []
    for key in sorted(_DEPENDENCY_ARTIFACT_KEYS):
        value = resources.get(key)
        if not isinstance(value, str) or not value:
            continue
        target = Path(value)
        if not target.is_absolute() and root is not None:
            target = Path(root) / target
        entries.append(
            {
                "key": key,
                "path": portable_path(target, root),
                "sha256": file_sha256(target),
            }
        )
    return entries


# ── verification (adoption gate) ─────────────────────────────────────────


def verify_step_result(
    payload: Mapping[str, Any],
    *,
    expected_identity: str | None,
    roots: Sequence[Path],
    expected_ref_path: str | None = None,
    expected_ref_sha256: str | None = None,
    expected_config_digest: str | None = None,
) -> str:
    """Verify one step_result payload for adoption; ``""`` means adoptable.

    Checks, in order: supported ``schema_version`` → ``step_identity``
    match → ``config_digest`` match (attempt metadata gate) → completed
    status → the checkpoint's ``result_ref`` sha (when recorded) → every
    recorded artifact / dependency exists with an equal sha256.

    Returns the reason code when any check fails (``identity_artifact_missing``,
    ``step_result_digest_mismatch``, …) — the caller recomputes.
    """
    schema = payload.get("schema_version")
    if not isinstance(schema, int) or isinstance(schema, bool):
        return "step_result_schema_unsupported"
    if schema not in SUPPORTED_STEP_RESULT_SCHEMAS:
        return "step_result_schema_unsupported"

    if expected_identity is None:
        return "step_identity_unavailable"
    if payload.get("step_identity") != expected_identity:
        return "step_identity_mismatch"

    if expected_config_digest is not None:
        stored_digest = payload.get("config_digest")
        if stored_digest != expected_config_digest:
            return "config_digest_mismatch"

    if str(payload.get("status") or "") != "completed":
        return "step_status_not_completed"

    if expected_ref_sha256 is not None and expected_ref_path is not None:
        located_ref = locate_recorded_file(roots, expected_ref_path)
        if located_ref is None:
            return "step_result_ref_missing"
        actual_ref = file_sha256(located_ref)
        if actual_ref is None or actual_ref != expected_ref_sha256:
            return "step_result_ref_mismatch"

    recorded: list[Any] = []
    for key in ("artifacts", "dependency_artifacts"):
        raw = payload.get(key)
        if isinstance(raw, list):
            recorded.extend(raw)
    for entry in recorded:
        if not isinstance(entry, Mapping):
            return "step_result_unreadable"
        recorded_path = entry.get("path")
        if not isinstance(recorded_path, str) or not recorded_path:
            return "identity_artifact_missing"
        located = locate_recorded_file(roots, recorded_path)
        if located is None:
            return "identity_artifact_missing"
        recorded_sha = entry.get("sha256")
        if not isinstance(recorded_sha, str) or not recorded_sha:
            return "step_result_digest_mismatch"
        actual_sha = file_sha256(located)
        if actual_sha != recorded_sha:
            return "step_result_digest_mismatch"
    return ""


# ── protocol compatibility (pre-archive check) ───────────────────────────


def _supported_result_schemas(results: Sequence[Path]) -> tuple[bool, str]:
    for path in results:
        payload = read_step_result(path)
        if payload is None:
            return False, f"step_result_unreadable:{path.name}"
        schema = payload.get("schema_version")
        if not isinstance(schema, int) or isinstance(schema, bool):
            return False, "step_result_schema_unsupported"
        if schema not in SUPPORTED_STEP_RESULT_SCHEMAS:
            return False, f"step_result_schema={schema}"
    return True, ""


def check_resume_protocol(root: Path | str, *, kind: str = "executor") -> tuple[bool, str]:
    """Pre-archive recovery-protocol probe for the attempt under *root*.

    ``kind="executor"`` requires ``checkpoint.identity_schema == 2`` (v2
    science identity) plus supported ``step_result.schema_version`` markers
    whenever the checkpoint carries completed steps.

    ``kind="batch"`` deliberately NEVER consults ``identity_schema`` — batch
    checkpoints are pinned at schema ``1`` by contract, so the only
    discriminator is the presence/schema_version of the per-item
    ``step_result.json`` files.
    """
    base = Path(root)
    checkpoint_path = base / "WORK" / "00_RUNTIME" / _CHECKPOINT_FILENAME
    if not checkpoint_path.is_file():
        return False, "checkpoint_missing"
    try:
        payload: Any = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "checkpoint_unreadable"
    if not isinstance(payload, dict):
        return False, "checkpoint_unreadable"

    work = base / "WORK"
    results = sorted(work.glob(f"**/{STEP_RESULT_FILENAME}")) if work.is_dir() else []

    if kind == "batch":
        if not results:
            return False, "step_result_missing"
        return _supported_result_schemas(results)

    schema = payload.get("identity_schema", 1)
    if not isinstance(schema, int) or isinstance(schema, bool):
        schema = 1
    if schema != _IDENTITY_SCHEMA_V2:
        return False, f"identity_schema={schema}; v2 recovery requires {_IDENTITY_SCHEMA_V2}"
    raw_states = payload.get("step_states")
    completed = [
        state
        for state in (raw_states if isinstance(raw_states, list) else [])
        if isinstance(state, dict) and state.get("status") == "completed"
    ]
    if completed and not results:
        return False, "step_result_missing"
    return _supported_result_schemas(results)


# ── resume_source.json (shared executor/batch parser) ────────────────────


@dataclass(frozen=True)
class ResumeSource:
    """Resolved ``resume_source.json`` receipt for the current attempt."""

    payload: Mapping[str, Any]
    science_root: Path
    compatible: bool
    reason: str
    previous_attempt: int | None

    @property
    def checkpoint_dir(self) -> Path:
        """Directory holding ``checkpoint.json`` for the resume science."""
        if (self.science_root / "WORK" / "00_RUNTIME" / _CHECKPOINT_FILENAME).is_file():
            return self.science_root / "WORK" / "00_RUNTIME"
        if (self.science_root / _CHECKPOINT_FILENAME).is_file():
            return self.science_root
        return self.science_root / "WORK" / "00_RUNTIME"


def _archive_has_science(archive: Path) -> bool:
    """True when the archived attempt still owns checkpoint/step results."""
    if not archive.is_dir():
        return False
    if (archive / "WORK" / "00_RUNTIME" / _CHECKPOINT_FILENAME).is_file():
        return True
    if (archive / _CHECKPOINT_FILENAME).is_file():
        return True
    work = archive / "WORK"
    if work.is_dir():
        for _ in work.glob(f"**/{STEP_RESULT_FILENAME}"):
            return True
    return False


def resolve_resume_source(task_root: Path | str, *, kind: str = "executor") -> ResumeSource | None:
    """Parse ``<task_root>/resume_source.json`` — the shared resume oracle.

    Returns ``None`` when no receipt exists (fresh run, or a ``rerun`` whose
    receipt was archived before the reset).  ``science_root`` is the archived
    attempt when it owns the science, otherwise the active task root (the
    continue path keeps checkpoint/step_result/RESULT in place — contract B).

    ``compatible`` comes from the receipt when the producer wrote it
    (pre-archive check); otherwise it is evaluated here against the resolved
    science root.  Incompatible receipts must never authorize adoption.
    """
    root = Path(task_root)
    path = root / RESUME_SOURCE_FILENAME
    if not path.is_file():
        return None
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("unreadable resume_source.json at %s; ignoring", path)
        return None
    if not isinstance(payload, dict):
        logger.warning("resume_source.json at %s is not an object; ignoring", path)
        return None

    raw_previous = payload.get("previous_attempt")
    previous_attempt = (
        raw_previous
        if isinstance(raw_previous, int) and not isinstance(raw_previous, bool)
        else None
    )
    science_root = root
    if previous_attempt is not None:
        archive = root / "WORK" / "00_RUNTIME" / "attempts" / str(previous_attempt)
        if _archive_has_science(archive):
            science_root = archive

    explicit = payload.get("compatible")
    if isinstance(explicit, bool):
        compatible = explicit
        reason = str(payload.get("incompatible_reason") or "")
    else:
        compatible, reason = check_resume_protocol(science_root, kind=kind)
    if not compatible:
        logger.warning(
            "recovery.protocol_incompatible: resume source %s is not adoptable (%s)",
            path,
            reason or "declared incompatible",
        )
    return ResumeSource(
        payload=payload,
        science_root=science_root,
        compatible=compatible,
        reason=reason,
        previous_attempt=previous_attempt,
    )
