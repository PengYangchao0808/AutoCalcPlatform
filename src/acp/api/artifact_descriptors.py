"""Bounded, read-only artifact discovery for the task status payload.

The manifest (``RESULT/result_manifest.json``) is the product index; it does
not carry digest/generation, so this projection never invents them.  A
descriptor is *not* a prepared ``ArtifactRef``: ``acp_prepare_artifact`` is
still the step that produces the frozen reference, and only its answer is
readable.

Round-8: a descriptor is the **pre-prepare view but it is actionable**.  It
carries the exact next call (``prepare.tool``/``prepare.arguments``) and the
product's on-disk ``size_bytes`` (a plain ``stat`` of the already-validated
``RESULT/<relative>`` path, ``None`` on any error).  Building a descriptor is
still zero-side-effect: no operation row, no prepare, no transfer.

Round-9:
* the ``prepare`` template is now **complete** — it carries the manifest
  product's ``relative_path`` and, when the extension implies one, its
  ``media_type`` (M6).  Copy-pasting the arguments into
  ``acp_prepare_artifact`` is the whole call; ``resolve_prepare_target`` is the
  one server-side derivation used when a caller omits the path.
* ``prepared`` reflects the authoritative prepare state (M15).  The descriptor
  only *reads* the existing durable operation row (``PrepareOperationService
  .find``): no enqueue, no expiry, no artifact-store write.  When the service
  is not available the flag stays ``False`` and the descriptor says why
  (``prepared_unknown_reason``) instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.results.manifest import MANIFEST_FILENAME, load_result_manifest
from acp_services.data.artifacts import (
    ArtifactPathRefusedError,
    is_safe_artifact_token,
    validate_artifact_relative_path,
)
from acp_services.data.operations import PrepareOperationService, PrepareState
from acp_services.entrypoints.application_params import media_type_for_path

__all__ = [
    "MAX_ARTIFACT_DESCRIPTORS",
    "MAX_MANIFEST_BYTES",
    "PrepareTarget",
    "PrepareTargetRefusedError",
    "describe_job_artifacts",
    "resolve_prepare_target",
]

#: Hard bound so a polling status call cannot walk an arbitrarily large tree.
MAX_ARTIFACT_DESCRIPTORS = 50

#: A status poll may not pay for an arbitrarily large manifest.
MAX_MANIFEST_BYTES = 2 * 1024 * 1024

#: MCP-facing ``kind`` aliases for manifest product kinds whose wire name would
#: bury the payload.  The manifest enum (``ProductKind``) is NOT changed —
#: only the descriptor an agent sees (round-7 #4: thermochemistry).
_KIND_ALIASES: dict[str, str] = {"thermo_report": "thermochemistry"}

#: Every manifest product path is published under the task's ``RESULT/``; the
#: prepare/read wire contract names the body with that prefix.
_RESULT_PREFIX = "RESULT/"


@dataclass(frozen=True)
class PrepareTarget:
    """Server-derived prepare inputs for one manifest product (M6)."""

    relative_path: str
    media_type: str | None = None


class PrepareTargetRefusedError(ValueError):
    """Typed refusal of one prepare-target derivation (M6/M7).

    Carries the same facts as a command error envelope: ``code`` names the
    outcome class, ``params`` always has ``field`` + ``rule`` so the caller can
    tell "fix the request" from "wait and retry", and ``retryable`` classifies
    the race (manifest/product not materialized yet) apart from a genuine
    parameter error.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        params: dict[str, Any],
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.params = dict(params)
        self.retryable = bool(retryable)


def _safe_relative(path: str) -> str | None:
    """Return the prepare-valid task-relative path, or None.

    Uses the same authority as ``prepare`` (``validate_artifact_relative_path``)
    so a discovered descriptor is always directly usable; Windows separators,
    absolute paths and traversal are refused there, not re-implemented here.
    """
    try:
        return validate_artifact_relative_path(str(path or ""))
    except (ArtifactPathRefusedError, ValueError):
        return None


def _safe_token(value: object) -> str | None:
    """A resource id prepare accepts — the same single authority."""
    text = str(value or "").strip()
    return text if text and is_safe_artifact_token(text) else None


def _product_size_bytes(root: Path, relative: str) -> int | None:
    """On-disk size of ``RESULT/<relative>``, or ``None`` (never invented).

    ``relative`` has already passed :func:`validate_artifact_relative_path`, so
    this is a bounded ``stat`` under the caller's controlled read root.  Any
    error (missing file, a directory, a permission problem, a race) means
    "unknown", never a fabricated zero.
    """
    try:
        return int((root / "RESULT" / relative).stat().st_size)
    except (OSError, ValueError):
        return None


def _product_readable(root: Path, relative: str) -> bool:
    """True when the product body is already materialized under ``RESULT/``."""
    try:
        return (root / "RESULT" / relative).is_file()
    except (OSError, ValueError):
        return False


def _read_manifest(task_dir: str | Path | None) -> tuple[Any | None, str]:
    """Load the bounded result manifest; ``reason`` names why it could not be.

    ``reason`` is empty on success, otherwise one of ``no_task_dir`` /
    ``missing`` / ``oversized`` / ``unreadable`` — the caller decides whether an
    absent manifest is the retryable publication race or an empty projection.
    """
    if not task_dir:
        return None, "no_task_dir"
    root = Path(task_dir)
    manifest_path = root / "RESULT" / MANIFEST_FILENAME
    try:
        if not manifest_path.is_file():
            return None, "missing"
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return None, "oversized"
    except OSError:
        return None, "unreadable"
    manifest = load_result_manifest(root)
    if manifest is None:
        return None, "unreadable"
    return manifest, ""


def _prepare_step(job_id: str | None, resource_id: str, relative_path: str) -> dict[str, Any]:
    """The exact ``acp_prepare_artifact`` call this descriptor points at."""
    arguments: dict[str, Any] = {
        "purpose": "artifact",
        "artifact_id": resource_id,
        "relative_path": relative_path,
    }
    derived_media_type = media_type_for_path(relative_path)
    if derived_media_type is not None:
        arguments["media_type"] = derived_media_type
    if job_id:
        arguments = {"job_id": job_id, **arguments}
    return {"tool": "acp_prepare_artifact", "arguments": arguments}


def _prepared_projection(
    prepare_service: PrepareOperationService | None,
    *,
    job_id: str | None,
    attempt: int | None,
    resource_id: str,
) -> dict[str, Any]:
    """Authoritative prepare state for one descriptor (M15, read-only).

    ``find`` is a pure row read — it never enqueues, leases, expires or writes,
    and it never touches the artifact store.  ``prepared`` is true only for a
    ``succeeded`` operation that committed a digest (the completion CAS records
    the published bytes, so a digest-less success is not "published").

    A missing service or key is **never** guessed into ``False`` as fact: the
    flag stays ``False`` and ``prepared_unknown_reason`` names what was unknown.
    """
    if prepare_service is None:
        return {"prepared": False, "prepared_unknown_reason": "no_prepare_service"}
    if not job_id or attempt is None:
        return {"prepared": False, "prepared_unknown_reason": "no_prepare_key"}
    record = prepare_service.find(
        job_id=str(job_id),
        resource_id=resource_id,
        attempt=int(attempt),
    )
    if record is None:
        return {"prepared": False, "prepared_state": "absent"}
    projection: dict[str, Any] = {
        "prepared": False,
        "prepared_state": record.state.value,
        "prepared_operation_id": record.operation_id,
    }
    digest = str(record.sha256 or "")
    if record.state is PrepareState.SUCCEEDED and digest:
        projection.update(
            {
                "prepared": True,
                "prepared_generation": int(record.resource_generation),
                "prepared_digest": digest if digest.startswith("sha256:") else f"sha256:{digest}",
                "prepared_at": record.updated_at,
            }
        )
    return projection


def resolve_prepare_target(
    task_dir: str | Path | None,
    *,
    artifact_id: str,
    relative_path: str = "",
    media_type: str = "",
) -> PrepareTarget:
    """Resolve the one product a prepare names, from the manifest authority.

    Round-9 M6/M7: ``acp_prepare_artifact`` may omit ``relative_path`` (the
    pre-round-9 status template did) — the product whose ``id`` equals
    ``artifact_id`` supplies it, so there is exactly one truth source.  A
    client-supplied path is validated with the same prepare authority and must
    name that same product; it never overrides the manifest.  ``media_type`` is
    derived from the extension when not supplied.

    Raises:
        PrepareTargetRefusedError: typed refusal carrying ``code``, ``params``
            (``field``/``rule``) and ``retryable``.  An absent/unreadable
            manifest, or a product listed in it whose body is not materialized
            yet, is the **retryable** ``resource_not_ready`` race (rule
            ``not_ready_yet``); an id the readable manifest does not contain is
            the genuine ``input_invalid``/``unknown_artifact`` parameter error.
    """
    resource_id = _safe_token(artifact_id)
    if resource_id is None:
        raise PrepareTargetRefusedError(
            f"unsafe artifact id: {artifact_id!r}",
            code="input_invalid",
            params={"field": "artifact_id", "rule": "path_traversal"},
            retryable=False,
        )
    claimed = ""
    if relative_path:
        try:
            claimed = validate_artifact_relative_path(relative_path)
        except (ArtifactPathRefusedError, ValueError) as exc:
            raise PrepareTargetRefusedError(
                str(exc) or "unsafe artifact relative_path",
                code="input_invalid",
                params={"field": "relative_path", "rule": "path_traversal"},
                retryable=False,
            ) from exc
    manifest, reason = _read_manifest(task_dir)
    if manifest is None:
        raise PrepareTargetRefusedError(
            f"result manifest is not materialized yet ({reason})",
            code="resource_not_ready",
            params={
                "field": "artifact_id",
                "rule": "not_ready_yet",
                "manifest_reason": reason,
            },
            retryable=True,
        )
    assert task_dir is not None  # a loaded manifest implies a non-empty root
    product = next(
        (item for item in manifest.products if _safe_token(getattr(item, "id", "")) == resource_id),
        None,
    )
    if product is None:
        raise PrepareTargetRefusedError(
            f"artifact {resource_id!r} is not a product of this task's manifest",
            code="input_invalid",
            params={
                "field": "artifact_id",
                "rule": "unknown_artifact",
                "hint": "acp_status artifacts[].resource_id lists the manifest ids",
            },
            retryable=False,
        )
    relative = _safe_relative(getattr(product, "path", ""))
    if relative is None:
        raise PrepareTargetRefusedError(
            f"product {resource_id!r} has an unsafe manifest path",
            code="input_invalid",
            params={"field": "artifact_id", "rule": "unsafe_manifest_path"},
            retryable=False,
        )
    expected = f"{_RESULT_PREFIX}{relative}"
    if claimed and claimed != expected:
        raise PrepareTargetRefusedError(
            f"artifact {resource_id!r} is published at {expected!r}, not {claimed!r}",
            code="revision_conflict",
            params={
                "field": "relative_path",
                "rule": "manifest_mismatch",
                "manifest_relative_path": expected,
            },
            retryable=False,
        )
    if not _product_readable(Path(task_dir), relative):
        raise PrepareTargetRefusedError(
            f"artifact {resource_id!r} is listed in the manifest but not readable yet",
            code="resource_not_ready",
            params={
                "field": "artifact_id",
                "rule": "not_ready_yet",
                "relative_path": expected,
            },
            retryable=True,
        )
    return PrepareTarget(
        relative_path=expected,
        media_type=str(media_type or "") or media_type_for_path(expected),
    )


def describe_job_artifacts(
    task_dir: str | Path | None,
    *,
    limit: int = MAX_ARTIFACT_DESCRIPTORS,
    attempt: int | None = None,
    job_id: str | None = None,
    prepare_service: PrepareOperationService | None = None,
    kind: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(descriptors, truncated)`` for one task's manifest products.

    * ``task_dir`` is the caller's controlled read root for the **current**
      attempt (never a walk from the run root).
    * Only ``RESULT/``-relative paths and prepare-safe resource ids are
      published; an unreadable size stays ``None``.
    * ``job_id`` (round-8) is echoed into ``prepare.arguments`` so the next MCP
      call needs no lookup; omit it only when the caller genuinely does not know
      the task (library use) — the key is then left out rather than faked.
    * ``prepare_service`` (round-9) is read-only: when given, ``prepared`` comes
      from the durable operation row for ``(job_id, attempt, resource_id)``.
      When omitted, the flag is ``False`` and ``prepared_unknown_reason`` says
      the prepare service was not available — never a guessed "not prepared".
    * Iteration stops at ``limit + 1`` products: the manifest's size is not
      allowed to cost more than the bound.
    * ``kind`` (round-9 M2) filters by descriptor kind before the bound, so an
      agent can page the frame products of a 200-frame PES scan without raising
      the polling envelope (`artifacts_truncated` still reports the rest).
    """
    manifest, reason = _read_manifest(task_dir)
    if manifest is None:
        return [], reason == "oversized"
    assert task_dir is not None  # a loaded manifest implies a non-empty root
    root = Path(task_dir)
    bounded = max(1, int(limit))
    descriptors: list[dict[str, Any]] = []
    truncated = False
    for product in manifest.products:
        relative = _safe_relative(getattr(product, "path", ""))
        resource_id = _safe_token(getattr(product, "id", ""))
        if relative is None or resource_id is None:
            continue
        raw_kind = getattr(product, "kind", "")
        raw_kind_value = getattr(raw_kind, "value", str(raw_kind))
        descriptor_kind = _KIND_ALIASES.get(raw_kind_value, raw_kind_value)
        if kind is not None and descriptor_kind != kind:
            continue
        # The bound counts *published* descriptors, so a kind filter can page
        # the matching products instead of being starved by unrelated kinds.
        if len(descriptors) >= bounded:
            truncated = True
            break
        relative_path = f"{_RESULT_PREFIX}{relative}"
        descriptor: dict[str, Any] = {
            "resource_id": resource_id,
            "relative_path": relative_path,
            "kind": descriptor_kind,
            "label": str(getattr(product, "label", "")),
            "size_bytes": _product_size_bytes(root, relative),
            **_prepared_projection(
                prepare_service,
                job_id=job_id,
                attempt=attempt,
                resource_id=resource_id,
            ),
            "prepare": _prepare_step(job_id, resource_id, relative_path),
        }
        if attempt is not None:
            descriptor["attempt"] = int(attempt)
        descriptors.append(descriptor)
    return descriptors, truncated


_ = MANIFEST_FILENAME  # re-exported contract name, kept importable for callers
