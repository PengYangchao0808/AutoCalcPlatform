"""Pure, read-only projection primitives for the ACP2 science routes.

This module contains only functions over plain in-memory data.  It never
creates a task, prepares or transfers a resource, takes a reservation, starts a
QC/subprocess, or performs a GET-time ``fetch_catalog``.  The API layer calls
these helpers to build the wire shapes; the helpers themselves keep no state
and touch no database.

Imports are deliberately limited to metadata sources (``acp.catalog`` and
``cccp.calculation``) plus read-only ``acp_services`` contracts.  Adding an
import that opens a DB or starts a server is a bug.

Author: ACP2 science lane (B1-1).
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import logging
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from acp.catalog import FIELD_DEFINITIONS, METHOD_SCHEMAS, WORKFLOW_CATALOG
from acp.storage.manifest import MANIFEST_FILENAME, ResultManifest
from acp_services.data.contracts import Availability
from acp_services.data.operations import PrepareOperationRecord, PrepareState
from acp_services.entrypoints.mcp import TOOL_ALLOWLIST
from acp_services.entrypoints.status_projection import (
    project_task_status,
    verified_release_facts,
)
from cccp.calculation.capabilities import builtin_catalog, domain_catalog
from cccp.calculation.requests import TaskRequest

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from acp_services.data.artifacts import ArtifactAuthorization, ArtifactStore
    from acp_services.data.operations import PrepareOperationService

__all__ = [
    "SAFE_ARTIFACT_ID_RE",
    "AttemptCacheIdentityUnverified",
    "AttemptHistoryUnavailable",
    "ScienceProjectionError",
    "UnsafeArtifactId",
    "ViewProvenanceUnavailable",
    "artifact_read",
    "attempt_status",
    "attempt_view",
    "is_safe_artifact_id",
    "map_availability",
    "canonical_view_bytes",
    "view_digest",
    "question_specs",
    "science_capabilities",
    "science_preflight",
]

logger = logging.getLogger(__name__)

#: Artifact ids are single path-free tokens (mirrors
#: ``src/acp_services/data/artifacts.py`` ``_SAFE_TOKEN``).
SAFE_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Stable wire protocol version for the capabilities document.
SCIENCE_CAPABILITIES_PROTOCOL_VERSION = "acp2.science.capabilities.v1"

#: Ordered workflow ids backing the three capabilities questions.
_QUESTION_WORKFLOWS = ("singlepoint", "optimize", "frequency")

#: Stable question id per workflow.
_QUESTION_IDS = {
    "singlepoint": "single_point_energy",
    "optimize": "geometry_optimization",
    "frequency": "frequency_analysis",
}

#: Capability descriptor id whose ``limitations`` feed each question.
_QUESTION_CAPABILITY_IDS = {
    "singlepoint": "electronic.energy",
    "optimize": "geometry.optimize",
    "frequency": "vibration.compute",
}

#: Optional nullable keys that are pruned from a view before digesting.  Only
#: these keys, reached through the documented structures, may disappear.
_ARTIFACT_NULLABLE_KEYS = frozenset({"sha256", "size_bytes", "reason", "relative_path"})

#: Shared input model defaults (the dataclass is the single source of truth).
_TASK_REQUEST_DEFAULTS: dict[str, Any] = {
    field.name: field.default
    for field in dataclasses.fields(TaskRequest)
    if field.default is not dataclasses.MISSING
}


# ---------------------------------------------------------------------------
# Safe artifact ids
# ---------------------------------------------------------------------------


def is_safe_artifact_id(value: object) -> bool:
    """Return ``True`` only for a slash-free artifact token.

    The accepted shape is ``^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$``.  A
    ``<job>/<path>`` value or a traversal token is therefore never safe.
    """

    if not isinstance(value, str):
        return False
    return SAFE_ARTIFACT_ID_RE.fullmatch(value) is not None


# ---------------------------------------------------------------------------
# Availability projection
# ---------------------------------------------------------------------------


def _coerce_prepare_state(value: PrepareState | str | None) -> PrepareState | None:
    if value is None:
        return None
    if isinstance(value, PrepareState):
        return value
    try:
        return PrepareState(str(value).strip().lower())
    except ValueError:
        return None


def _coerce_availability(value: Availability | str | None) -> Availability | None:
    if value is None:
        return None
    if isinstance(value, Availability):
        return value
    try:
        return Availability(str(value).strip().lower())
    except ValueError:
        return None


def map_availability(
    state: PrepareState | str | None,
    availability: Availability | str | None,
) -> str:
    """Map a prepare-operation state onto the four-value wire vocabulary.

    Precedence (deterministic, first match wins):

    1. ``Availability.UNAVAILABLE`` or a failed/canceled/expired operation
       yields ``"failed"`` (``SUCCEEDED`` + ``UNAVAILABLE`` is a failure).
    2. No operation (``state is None``, including unknown strings) yields
       ``"unprepared"``.
    3. ``SUCCEEDED`` yields ``"readable"`` only with ``Availability.READY``;
       any other availability (including absent) yields ``"unprepared"``.
    4. ``QUEUED``/``RUNNING`` or ``Availability.PREPARING`` yields
       ``"preparing"``.

    Unknown strings are treated as absent input, so the function is total and
    never invents a fifth state.
    """

    prepare_state = _coerce_prepare_state(state)
    avail = _coerce_availability(availability)

    if avail is Availability.UNAVAILABLE:
        return "failed"
    if prepare_state in (PrepareState.FAILED, PrepareState.CANCELED, PrepareState.EXPIRED):
        return "failed"
    if prepare_state is None:
        return "unprepared"
    if prepare_state is PrepareState.SUCCEEDED:
        return "readable" if avail is Availability.READY else "unprepared"
    if avail is Availability.PREPARING:
        return "preparing"
    if prepare_state in (PrepareState.QUEUED, PrepareState.RUNNING):
        return "preparing"
    return "unprepared"


# ---------------------------------------------------------------------------
# Canonical view digest
# ---------------------------------------------------------------------------


def _prune_task_ref(value: dict[str, Any]) -> None:
    if "task_id" in value and value["task_id"] is None:
        del value["task_id"]


def _prune_artifact(value: dict[str, Any]) -> None:
    for key in _ARTIFACT_NULLABLE_KEYS:
        if key in value and value[key] is None:
            del value[key]


def _prune_claim(value: dict[str, Any]) -> None:
    if "reason" in value and value["reason"] is None:
        del value["reason"]


def _prune_optional_nulls(obj: Any) -> None:
    """Delete ``None`` values for the documented optional nullable paths.

    The walk is recursive so nested copies of the view shape are normalized the
    same way.  Only the named optional keys are removed; a required key whose
    value happens to be ``None`` is left in place on purpose so the digest stays
    stable and schema validation can flag the caller bug.
    """

    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if key == "task_ref" and isinstance(value, dict):
                _prune_task_ref(value)
            elif key == "artifacts" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _prune_artifact(item)
            elif key == "claims" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _prune_claim(item)
            _prune_optional_nulls(value)
    elif isinstance(obj, list):
        for item in obj:
            _prune_optional_nulls(item)


def _nfc_normalize(value: Any) -> Any:
    """Recursively NFC-normalize every string (keys included)."""

    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {_nfc_normalize(key): _nfc_normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nfc_normalize(item) for item in value]
    return value


def canonical_view_bytes(view: Mapping[str, Any]) -> bytes:
    """Return the canonical UTF-8 bytes of a view for digesting.

    Rules (frozen):

    1. Deep-copy the mapping and delete ``provenance.digest`` (if present).
    2. Recursively delete ``None`` values only at the optional nullable paths
       ``task_ref.task_id``, ``artifacts[*].{sha256,size_bytes,reason,
       relative_path}`` and ``claims[*].reason``.
    3. Recursively NFC-normalize every string.
    4. ``json.dumps(..., sort_keys=True, separators=(",", ":"),
       ensure_ascii=False, allow_nan=False)`` encoded as UTF-8.
    5. Non-JSON values, ``NaN`` and ``Infinity`` propagate ``TypeError`` /
       ``ValueError`` from ``json.dumps``; they are never swallowed.
    """

    document = copy.deepcopy(dict(view))

    provenance = document.get("provenance")
    if isinstance(provenance, dict):
        provenance.pop("digest", None)

    _prune_optional_nulls(document)
    document = _nfc_normalize(document)

    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def view_digest(view: Mapping[str, Any]) -> str:
    """Return ``sha256:<64hex>`` over :func:`canonical_view_bytes`."""

    return "sha256:" + hashlib.sha256(canonical_view_bytes(view)).hexdigest()


# ---------------------------------------------------------------------------
# Question metadata
# ---------------------------------------------------------------------------


def _catalog_entry(workflow: str) -> dict[str, Any] | None:
    for entry in WORKFLOW_CATALOG:
        if entry.get("id") == workflow:
            return entry
    return None


def _descriptor_limitations(descriptors: Mapping[str, Any]) -> dict[str, list[str]]:
    """Map capability id -> published limitation texts from a descriptor doc."""

    result: dict[str, list[str]] = {}
    capabilities = descriptors.get("capabilities")
    if not isinstance(capabilities, list):
        return result
    for capability in capabilities:
        if not isinstance(capability, Mapping):
            continue
        capability_id = capability.get("capability_id")
        if not isinstance(capability_id, str):
            continue
        limitations = capability.get("limitations")
        texts: list[str] = []
        if isinstance(limitations, list):
            for limitation in limitations:
                if isinstance(limitation, Mapping):
                    text = limitation.get("text")
                else:
                    text = None
                if isinstance(text, str) and text.strip():
                    texts.append(text)
        result[capability_id] = texts
    return result


def _published_default(field_name: str) -> Any:
    definition = FIELD_DEFINITIONS.get(field_name)
    if not isinstance(definition, Mapping):
        return None
    default = definition.get("default")
    if not isinstance(default, Mapping):
        return None
    return default.get("*")


def question_specs(*, descriptors: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return the three published science questions with UI defaults.

    Only UI metadata is produced.  Every ``defaults`` entry is
    ``{"value": ..., "source": ...}``; the six keys are ``method``, ``charge``,
    ``multiplicity``, ``solvent``, ``temperature`` and ``standardState``.
    """

    descriptor_doc = descriptors if descriptors is not None else builtin_catalog().to_dict()
    limitations_by_capability = _descriptor_limitations(descriptor_doc)

    questions: list[dict[str, Any]] = []
    for workflow in _QUESTION_WORKFLOWS:
        entry = _catalog_entry(workflow)
        if entry is None:
            logger.warning("science question workflow missing from catalog: %s", workflow)
            continue

        schema_id = entry.get("method_schema_id")
        if not isinstance(schema_id, str):
            logger.warning("science question workflow has no method schema: %s", workflow)
            continue
        schema = METHOD_SCHEMAS.get(schema_id) or {}
        method_levels = schema.get("method_levels") or []
        if not method_levels:
            logger.warning("science question schema has no method level: %s", schema_id)
            continue

        level_id = method_levels[0]["level_id"]
        profiles = schema.get("profiles") or []
        profile_level: Mapping[str, Any] | None = None
        if profiles:
            levels = profiles[0].get("levels") or {}
            candidate = levels.get(level_id)
            if isinstance(candidate, Mapping):
                profile_level = candidate

        if profile_level is not None:
            engine = profile_level.get("engine")
            basis: str | None = profile_level.get("basis")
            method_value: str | None = profile_level.get("functional")
            method_source = f"METHOD_SCHEMAS.{schema_id}.profiles[0].levels.{level_id}.functional"
        else:
            engine = (method_levels[0].get("allowed_engines") or [None])[0]
            basis = None
            method_value = None
            method_source = "no published default"

        defaults = {
            "method": {"value": method_value, "source": method_source},
            "charge": {
                "value": _TASK_REQUEST_DEFAULTS.get("charge"),
                "source": "TaskRequest.charge",
            },
            "multiplicity": {
                "value": _TASK_REQUEST_DEFAULTS.get("multiplicity"),
                "source": "TaskRequest.multiplicity",
            },
            "solvent": {
                "value": _published_default("solvent"),
                "source": "FIELD_DEFINITIONS.solvent.default",
            },
            "temperature": {
                "value": _published_default("temperature"),
                "source": "FIELD_DEFINITIONS.temperature.default",
            },
            "standardState": {"value": None, "source": "not_applicable"},
        }

        capability_id = _QUESTION_CAPABILITY_IDS[workflow]
        questions.append(
            {
                "question_id": _QUESTION_IDS[workflow],
                "label": entry.get("label_zh") or entry.get("label"),
                "workflow": workflow,
                "method_schema_id": schema_id,
                "level_id": level_id,
                "engine": engine,
                "basis": basis,
                "defaults": defaults,
                "limitations": list(limitations_by_capability.get(capability_id, [])),
            }
        )

    return questions


# ---------------------------------------------------------------------------
# Capabilities document
# ---------------------------------------------------------------------------


def science_capabilities(
    *,
    descriptors: Mapping[str, Any] | None = None,
    domain: Mapping[str, Any] | None = None,
    analysis: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the read-only ``GET /api/v2/science/capabilities`` document.

    ``descriptors``/``domain`` may be injected by the route (cached once per
    app) or by tests (to avoid executable probing).  ``analysis`` is the frozen
    ``registered/executable/unavailable`` projection of the injected
    application analysis registry (M2-02); a caller that does not pass it gets
    an empty table, never a fabricated state.  The top level contains exactly
    ``protocol_version``, ``tools`` and ``capabilities``.
    """

    descriptor_doc = descriptors if descriptors is not None else builtin_catalog().to_dict()
    domain_doc = domain if domain is not None else domain_catalog().to_dict()

    return {
        "protocol_version": SCIENCE_CAPABILITIES_PROTOCOL_VERSION,
        "tools": sorted(TOOL_ALLOWLIST),
        "capabilities": {
            "questions": question_specs(descriptors=descriptor_doc),
            "descriptors": descriptor_doc,
            "domain": domain_doc,
            "analysis": [dict(entry) for entry in (analysis or ())],
        },
    }


# ---------------------------------------------------------------------------
# Domain errors (the API layer maps these to HTTP later; this module never does)
# ---------------------------------------------------------------------------


class ScienceProjectionError(Exception):
    """Base error for the read-only science projections."""

    code = "science_projection_error"

    def __init__(self, message: str = "", *, reason: str = "") -> None:
        super().__init__(message or self.code)
        self.reason = reason


class AttemptHistoryUnavailable(ScienceProjectionError):  # noqa: N818
    """The requested attempt has no provable archived/historical snapshot."""

    code = "attempt_history_unavailable"


class AttemptCacheIdentityUnverified(ScienceProjectionError):  # noqa: N818
    """Neither the manifest nor the release facts prove the requested attempt."""

    code = "attempt_cache_identity_unverified"


class ViewProvenanceUnavailable(ScienceProjectionError):  # noqa: N818
    """No provenance identity/timestamp can be obtained for the view."""

    code = "view_provenance_unavailable"


class UnsafeArtifactId(ScienceProjectionError):  # noqa: N818
    """An artifact id is not a single path-free token."""

    code = "path_traversal"


#: Wire version of the attempt-status projection.
_ATTEMPT_STATUS_SCHEMA_VERSION = 1

_BARE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_CLAIM_STATUSES = frozenset({"supported", "refuted", "inconclusive", "not_evaluated"})
_ARTIFACT_ROLE_FALLBACK = "artifact"

_RECEIPT_FILENAME = "execution_receipt.json"
_MANIFEST_ATTEMPT_SIDECAR_SUFFIX = ".attempt"


# ---------------------------------------------------------------------------
# Small record/file helpers (read-only, no DB, no network)
# ---------------------------------------------------------------------------


def _coerce_attempt(value: object) -> int | None:
    """Best-effort attempt coercion mirroring the status projection gate."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _record_mapping(record: object) -> dict[str, Any]:
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    return {}


def _record_project_id(record: object) -> str:
    project_id = getattr(record, "project_id", None)
    if isinstance(project_id, str) and project_id:
        return project_id
    spec = getattr(record, "spec", None)
    spec_project_id = getattr(spec, "project_id", None)
    return spec_project_id if isinstance(spec_project_id, str) and spec_project_id else ""


def _record_workflow(record: object) -> str:
    spec = getattr(record, "spec", None)
    workflow = getattr(spec, "workflow", None)
    if isinstance(workflow, str):
        return workflow
    mapping = _record_mapping(record)
    spec_mapping = mapping.get("spec")
    if isinstance(spec_mapping, Mapping):
        value = spec_mapping.get("workflow")
        if isinstance(value, str):
            return value
    return ""


def _record_updated_at(record: object) -> str:
    value = getattr(record, "updated_at", None)
    return value if isinstance(value, str) else ""


def _bare_sha256(value: object) -> str | None:
    """Return a bare 64-hex digest or ``None`` (never a ``sha256:`` prefix)."""

    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text.removeprefix("sha256:")
    return text if _BARE_SHA256_RE.fullmatch(text) is not None else None


def _sha256_prefixed(value: object) -> str | None:
    bare = _bare_sha256(value)
    return "sha256:" + bare if bare is not None else None


def _iso_utc(value: object) -> str | None:
    """Normalise an ISO-8601 string to an offset-bearing datetime (or ``None``)."""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _file_mtime_utc_iso(path: Path) -> str | None:
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def _read_json_mapping(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _canonical_json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# 1. Attempt status (current row or historical receipt)
# ---------------------------------------------------------------------------


def attempt_status(record: object, attempt: int, read_root: object) -> dict[str, Any]:
    """Project one attempt's status without leaking internal execution facts.

    Current attempt (``attempt == record.attempt``) is derived from the shared
    :func:`project_task_status` normalisation and then filtered down to
    ``schema_version``/``task_ref``/``status``/``revision``.  A historical
    attempt is read from its own archived ``execution_receipt.json``; a missing,
    unparseable or mismatched receipt is :class:`AttemptHistoryUnavailable`.
    """

    attempt_value = int(attempt)
    if attempt_value < 1:
        raise ValueError("invalid_attempt")

    if getattr(read_root, "found", False) is not True:
        if str(getattr(read_root, "reason", "") or "") == "historical_attempt_missing":
            raise AttemptHistoryUnavailable("historical attempt is unavailable")
        raise ValueError("attempt_not_found")

    job_id = str(getattr(record, "id", "") or "")
    record_attempt = _coerce_attempt(getattr(record, "attempt", None))
    if record_attempt is None:
        record_attempt = 1

    if attempt_value == record_attempt:
        projection = project_task_status(
            _record_mapping(record),
            fallback_task_id=job_id,
        )
        task_ref = projection.get("task_ref")
        task_id = task_ref.get("task_id") if isinstance(task_ref, Mapping) else job_id
        status = projection.get("status") or projection.get("state") or "unknown"
        revision = projection.get("revision")
        return {
            "schema_version": _ATTEMPT_STATUS_SCHEMA_VERSION,
            "task_ref": {
                "job_id": job_id,
                "attempt": attempt_value,
                "task_id": task_id or None,
            },
            "status": str(status),
            "revision": int(revision) if isinstance(revision, int) else 0,
        }

    root_path = str(getattr(read_root, "path", "") or "")
    receipt = _read_json_mapping(Path(root_path) / _RECEIPT_FILENAME) if root_path else None
    if receipt is None or _coerce_attempt(receipt.get("attempt")) != attempt_value:
        raise AttemptHistoryUnavailable("historical attempt is unavailable")

    revision = _coerce_attempt(receipt.get("revision"))
    status = receipt.get("state")
    return {
        "schema_version": _ATTEMPT_STATUS_SCHEMA_VERSION,
        "task_ref": {"job_id": job_id, "attempt": attempt_value, "task_id": None},
        "status": str(status) if isinstance(status, str) and status else "unknown",
        "revision": revision if revision is not None else 0,
    }


# ---------------------------------------------------------------------------
# 2. Attempt view (frozen view-model.schema.json shape)
# ---------------------------------------------------------------------------


def _historical_receipt_written_at(root_path: str) -> str | None:
    if not root_path:
        return None
    receipt = _read_json_mapping(Path(root_path) / _RECEIPT_FILENAME)
    if receipt is None:
        return None
    return _iso_utc(receipt.get("written_at"))


def _read_manifest_for_attempt(
    root_path: str,
    attempt: int,
    *,
    is_remote: bool,
) -> tuple[ResultManifest | None, Path | None]:
    """Read the result manifest when (and only when) it belongs to *attempt*.

    Local roots are attempt-scoped and their looked-up copy has no sidecar, so
    the manifest is read directly.  A remote root is a shared cache: only the
    ``result_manifest.json.attempt`` sidecar can prove the cached manifest is
    the requested attempt's copy.
    """

    if not root_path:
        return None, None
    result_dir = Path(root_path) / "RESULT"
    manifest_path = result_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None, manifest_path

    if is_remote:
        sidecar_path = manifest_path.with_name(
            manifest_path.name + _MANIFEST_ATTEMPT_SIDECAR_SUFFIX
        )
        sidecar = _read_json_mapping(sidecar_path)
        if sidecar is None or _coerce_attempt(sidecar.get("attempt")) != attempt:
            return None, manifest_path

    try:
        return ResultManifest.read(result_dir), manifest_path
    except (OSError, ValueError):
        return None, manifest_path


def _manifest_artifact_entries(manifest: ResultManifest | None) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    if manifest is None:
        return entries
    for product in manifest.products:
        artifact_id = str(getattr(product, "id", "") or "")
        if not is_safe_artifact_id(artifact_id):
            continue
        raw_path = str(getattr(product, "path", "") or "")
        if raw_path and not raw_path.startswith("RESULT/"):
            relative_path: str | None = "RESULT/" + raw_path
        else:
            relative_path = raw_path or None
        kind = getattr(product, "kind", None)
        role = getattr(kind, "value", None) or _ARTIFACT_ROLE_FALLBACK
        entries[artifact_id] = {
            "artifact_id": artifact_id,
            "role": str(role) or _ARTIFACT_ROLE_FALLBACK,
            "relative_path": relative_path,
            "availability": "unprepared",
            "generation": 0,
            "sha256": None,
            "size_bytes": None,
            "reason": None,
        }
    return entries


def _apply_prepare_operation(
    entries: dict[str, dict[str, Any]],
    operation: PrepareOperationRecord,
) -> None:
    artifact_id = str(operation.resource_id)
    if not is_safe_artifact_id(artifact_id):
        return
    entry = entries.get(artifact_id)
    if entry is None:
        entry = {
            "artifact_id": artifact_id,
            "role": _ARTIFACT_ROLE_FALLBACK,
            "relative_path": None,
            "availability": "unprepared",
            "generation": 0,
            "sha256": None,
            "size_bytes": None,
            "reason": None,
        }
    availability = map_availability(operation.state, operation.availability)
    size_bytes = operation.size_bytes
    entry["availability"] = availability
    entry["generation"] = int(operation.resource_generation or 0)
    entry["sha256"] = _bare_sha256(operation.sha256)
    entry["size_bytes"] = (
        int(size_bytes)
        if isinstance(size_bytes, int) and not isinstance(size_bytes, bool)
        else None
    )
    entry["reason"] = (
        str(operation.error_code) if availability == "failed" and operation.error_code else None
    )
    payload = dict(operation.payload or {})
    relative_path = payload.get("relative_path")
    if isinstance(relative_path, str) and relative_path:
        entry["relative_path"] = relative_path
    entries[artifact_id] = entry


def _project_claim(claim: object) -> dict[str, Any] | None:
    """Project only the five view-model claim fields (never scope/source_refs)."""

    if not isinstance(claim, Mapping):
        return None
    claim_id = claim.get("claim_id")
    statement = claim.get("statement")
    status = claim.get("status")
    if not isinstance(claim_id, str) or not claim_id:
        return None
    if not isinstance(statement, str) or not statement:
        return None
    if not isinstance(status, str) or status not in _CLAIM_STATUSES:
        return None

    evidence_refs: list[str] = []
    raw_refs = claim.get("source_refs")
    if isinstance(raw_refs, list):
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, Mapping):
                continue
            artifact_path = raw_ref.get("artifact_path")
            if isinstance(artifact_path, str) and artifact_path:
                if artifact_path not in evidence_refs:
                    evidence_refs.append(artifact_path)

    reason = claim.get("reason")
    return {
        "claim_id": claim_id,
        "statement": statement,
        "status": status,
        "evidence_refs": evidence_refs,
        "reason": reason if isinstance(reason, str) else None,
    }


def _claim_authorizer_allows(authorizer: Any, operation: PrepareOperationRecord) -> bool:
    """Call an injected claim authorizer (accepting 0- or 1-argument callables)."""

    try:
        decision = authorizer(operation)
    except TypeError:
        decision = authorizer()
    if decision is True:
        return True
    return getattr(decision, "allowed", False) is True


def _published_report_claims(
    operation: PrepareOperationRecord,
    artifact_store: ArtifactStore,
) -> list[dict[str, Any]] | None:
    """Return the verified published report's claims, or ``None`` when unproven.

    A prepare *request* payload is never trusted: the operation must have
    reached ``SUCCEEDED`` + ``READY`` with a committed digest, its evidence body
    must be present in the artifact store, and the body bytes must re-verify to
    the committed digest/size.
    """

    # Local import keeps the module's import surface light (no store at import).
    from acp_services.data.artifacts import ArtifactError

    if not is_safe_artifact_id(operation.resource_id):
        return None
    if operation.state is not PrepareState.SUCCEEDED:
        return None
    if operation.availability is not Availability.READY:
        return None
    digest_hex = _bare_sha256(operation.sha256)
    if digest_hex is None:
        return None
    payload = dict(operation.payload or {})
    if str(payload.get("purpose") or "") != "artifact":
        return None
    if "evidence_report" not in payload:
        return None
    generation = int(operation.resource_generation or 0)
    if generation < 1:
        return None

    digest = "sha256:" + digest_hex
    try:
        stored = artifact_store.lookup(
            job_id=operation.job_id,
            attempt=int(operation.attempt),
            resource_id=operation.resource_id,
            generation=generation,
            digest=digest,
        )
    except ArtifactError:
        return None
    except (OSError, ValueError):
        return None
    if stored is None:
        return None

    try:
        body = artifact_store.read(stored)
    except ArtifactError:
        return None
    except (OSError, ValueError):
        return None

    observed = "sha256:" + hashlib.sha256(body).hexdigest()
    if observed != digest or stored.digest != digest or int(stored.size_bytes) != len(body):
        return None
    if operation.size_bytes is not None and int(operation.size_bytes) != int(stored.size_bytes):
        return None

    try:
        report = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(report, Mapping):
        return None
    raw_claims = report.get("claims")
    if not isinstance(raw_claims, list):
        return []

    projected: list[dict[str, Any]] = []
    for claim in raw_claims:
        entry = _project_claim(claim)
        if entry is not None:
            projected.append(entry)
    return projected


def attempt_view(
    record: object,
    attempt: int,
    read_root: object,
    prepare_ops: PrepareOperationService,
    artifact_store: ArtifactStore,
    *,
    project_id: str | None = None,
    claim_authorizer: Any = None,
) -> dict[str, Any]:
    """Project the frozen read-only view model for one attempt.

    Provenance is resolved in the documented order — verified execution release
    facts, then the result manifest, then the task record — reading only the
    controlled ``read_root.path`` (never ``record.work_dir``).  Claims are only
    those of a published, digest-verified EvidenceReport.
    """

    attempt_value = int(attempt)
    if attempt_value < 1:
        raise ValueError("invalid_attempt")

    root_path = str(getattr(read_root, "path", "") or "")
    is_remote = bool(getattr(read_root, "is_remote", False))
    historical = bool(getattr(read_root, "historical", False))

    job_id = str(getattr(record, "id", "") or "")
    revision = getattr(record, "revision", 0)
    snapshot_revision = int(revision) if isinstance(revision, int) else 0
    resolved_project_id = str(project_id) if project_id is not None else _record_project_id(record)

    release_facts = verified_release_facts(root_path or None, attempt_value)
    release_revision = _sha256_prefixed(release_facts.get("release_digest"))
    release_verified = bool(release_facts)

    manifest, manifest_path = _read_manifest_for_attempt(
        root_path,
        attempt_value,
        is_remote=is_remote,
    )
    manifest_verified = manifest is not None

    if is_remote:
        if historical:
            if not manifest_verified and not release_verified:
                raise AttemptHistoryUnavailable("historical attempt is unavailable")
        elif not manifest_verified and not release_verified:
            raise AttemptCacheIdentityUnverified(
                "remote cache identity does not prove the requested attempt"
            )

    entries = _manifest_artifact_entries(manifest)
    operations = prepare_ops.list_for_job(job_id=job_id, attempt=attempt_value)
    for operation in operations:
        _apply_prepare_operation(entries, operation)

    claims: list[dict[str, Any]] = []
    if artifact_store is not None:
        for operation in operations:
            if claim_authorizer is not None:
                if not _claim_authorizer_allows(claim_authorizer, operation):
                    claims = []
                    break
            published = _published_report_claims(operation, artifact_store)
            if published:
                claims.extend(published)

    provenance: dict[str, Any]
    if release_revision is not None:
        produced_at = (
            _historical_receipt_written_at(root_path)
            if historical
            else _iso_utc(_record_updated_at(record))
        )
        if produced_at is not None:
            provenance = {
                "source": "execution_release",
                "producer_revision": release_revision,
                "produced_at": produced_at,
                "digest": "",
            }
        else:
            provenance = _manifest_or_task_record_provenance(
                record,
                attempt_value,
                resolved_project_id,
                manifest=manifest,
                manifest_path=manifest_path,
            )
    else:
        provenance = _manifest_or_task_record_provenance(
            record,
            attempt_value,
            resolved_project_id,
            manifest=manifest,
            manifest_path=manifest_path,
        )

    view: dict[str, Any] = {
        "snapshot_revision": snapshot_revision,
        "project_id": resolved_project_id,
        "task_ref": {
            "job_id": job_id,
            "attempt": attempt_value,
            "task_id": job_id or None,
        },
        "manifest_generation": 0,
        "artifacts": list(entries.values()),
        "claims": claims,
        "clarifications": [],
        "provenance": provenance,
        "read_only": True,
        "not_authoritative_for_science": True,
    }
    view["provenance"]["digest"] = view_digest(view)
    return view


def _manifest_or_task_record_provenance(
    record: object,
    attempt: int,
    project_id: str,
    *,
    manifest: ResultManifest | None,
    manifest_path: Path | None,
) -> dict[str, Any]:
    """Second/third provenance levels (result manifest, then task record)."""

    if manifest is not None and manifest_path is not None:
        canonical = _canonical_json_sha256(manifest.to_dict())
        produced_at = _file_mtime_utc_iso(manifest_path)
        if produced_at is not None:
            return {
                "source": "result_manifest",
                "producer_revision": canonical,
                "produced_at": produced_at,
                "digest": "",
            }

    updated_at_raw = _record_updated_at(record)
    updated_at = _iso_utc(updated_at_raw)
    if updated_at is None:
        raise ViewProvenanceUnavailable("no provenance identity/timestamp is obtainable")

    revision = getattr(record, "revision", 0)
    identity = {
        "job_id": str(getattr(record, "id", "") or ""),
        "attempt": attempt,
        "workflow": _record_workflow(record),
        "project_id": project_id,
        "revision": int(revision) if isinstance(revision, int) else 0,
        "updated_at": updated_at_raw,
    }
    return {
        "source": "task_record",
        "producer_revision": _canonical_json_sha256(identity),
        "produced_at": updated_at,
        "digest": "",
    }


# ---------------------------------------------------------------------------
# 3. Artifact read (generation resolution + delegation only)
# ---------------------------------------------------------------------------


def artifact_read(
    prepare_ops: PrepareOperationService,
    artifact_store: ArtifactStore,
    *,
    job_id: str,
    attempt: int,
    artifact_id: str,
    generation: int,
    digest: str,
    max_bytes: int = 256 * 1024,
    permission_scope: str = "local-single",
    authorization: ArtifactAuthorization | None = None,
) -> dict[str, Any]:
    """Resolve one prepared artifact read without any prepare/transfer side effect.

    A missing/zero generation is resolved to the operation's committed
    generation before delegating to the canonical
    :func:`acp_services.data.artifacts.resolve_artifact_read`; its errors are
    intentionally not caught here (the route maps them).
    """

    if not is_safe_artifact_id(artifact_id):
        raise UnsafeArtifactId(f"unsafe artifact id: {artifact_id!r}")

    from acp_services.data.artifacts import resolve_artifact_read

    attempt_value = int(attempt)
    generation_value = int(generation)
    if generation_value < 1:
        operation = prepare_ops.find(
            job_id=str(job_id),
            resource_id=str(artifact_id),
            attempt=attempt_value,
            permission_scope=permission_scope,
        )
        generation_value = int(operation.resource_generation) if operation else 1

    read = resolve_artifact_read(
        prepare_ops,
        artifact_store,
        job_id=str(job_id),
        attempt=attempt_value,
        resource_id=str(artifact_id),
        generation=generation_value,
        digest=digest if isinstance(digest, str) else "",
        permission_scope=permission_scope,
        authorization=authorization,
        max_bytes=max_bytes,
    )
    return read.to_dict()


# ---------------------------------------------------------------------------
# 4. Preflight projection reshape (B1-3 v2 wire shape)
# ---------------------------------------------------------------------------


def _first_reason(reasons: object) -> str:
    """Return the first human-readable reason string from a preflight list."""

    if not isinstance(reasons, list) or not reasons:
        return ""
    first = reasons[0]
    if isinstance(first, Mapping):
        candidate = first.get("message") or first.get("code")
        return candidate if isinstance(candidate, str) else str(candidate or "")
    return first if isinstance(first, str) else ""


def science_preflight(
    projection: Mapping[str, Any],
    *,
    resources: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reshape a v1 preflight projection onto the v2 science wire shape.

    This is pure reshaping: the single v1 preflight implementation owns every
    validation and computes the digest.  That digest is emitted twice on
    purpose — as ``plan.plan_digest`` and as ``preflight_fingerprint`` — so the
    two values can never diverge (the fingerprint is exactly the plan digest a
    submit is checked against).

    ``resources`` is the caller's real resource request; it becomes the basis
    of ``estimate`` so no default/resource is invented here.
    """

    plan_digest = projection.get("plan_digest")

    plan_summary = projection.get("plan_summary")
    plan: dict[str, Any] = dict(plan_summary) if isinstance(plan_summary, Mapping) else {}
    plan["plan_digest"] = plan_digest

    if isinstance(resources, Mapping):
        estimate = dict(resources)
    else:
        raw_estimate = projection.get("estimate")
        estimate = dict(raw_estimate) if isinstance(raw_estimate, Mapping) else {}
    estimate["feasible"] = bool(projection.get("feasible"))
    estimate["reason"] = _first_reason(projection.get("reasons"))

    return {
        "schema_version": 1,
        "executed": False,
        "metering_consumed": False,
        "transfers_attempted": False,
        "plan": plan,
        "preflight_fingerprint": plan_digest,
        "estimate": estimate,
    }
