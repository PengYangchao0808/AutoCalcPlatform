"""Explicit artifact prepare/read lifecycle + the frozen ``ArtifactRef`` (B12).

This module owns the **artifact** half of the Data prepare/read seam.  It does
not introduce a second state machine: a prepare operation is still a persistent
:class:`acp_services.data.operations.PrepareOperationService` row, and its
``payload``/``sha256``/``size_bytes``/``resource_generation`` columns carry the
frozen artifact facts.  What this module adds is:

* :class:`ArtifactRef` — the one verifiable reference
  ``(job_id, attempt, resource_id, generation, digest)`` plus schema/version,
  size, availability and the authorization projection.  A read is served only
  when every field agrees with the prepared body.
* :class:`ArtifactStore` — a content-addressed, atomic, local-only publication
  store keyed by ``job/attempt/resource/generation/digest``.  A READ has no
  network and no prepare reference: it opens the published body and verifies
  the digest before returning a single byte.  A missing body is a named
  ``not_prepared`` error, never an empty substitute.
* :class:`ArtifactPrepareExecutor` — the injected prepare mechanism for
  ``purpose=artifact`` leases.  It reuses
  :class:`~acp_services.data.transfer.ExpectedDigest` / ``sha256_bytes`` for the
  verification policy and publishes a verified copy.  The source read is an
  injected :class:`ArtifactSourcePort` so this module never touches a job root
  itself and never fetches.
* :func:`resolve_artifact_read` — the query-only read resolver.  It refuses
  stale generations, cross-attempt reads, missing bodies and digest mismatches
  with named errors before the caller can serve bytes.

**EvidenceReport storage, not interpretation.**  When a prepare payload carries
an ``evidence_report`` document the executor stores those exact bytes as an
artifact and records the science schema / source refs.  The platform never
parses a claim and never turns any status into ``claim supported``; the bytes
are opaque to this layer.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package.  The source adapter that reads a job's controlled result root
is injected from ``acp.services_data_bootstrap``.

Author: ACP Services (B lane, todo 13/B12).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.data.contracts import Availability
from acp_services.data.operations import (
    PrepareExecutionResult,
    PrepareLease,
    PrepareOperationRecord,
    PrepareOperationService,
    PrepareState,
)
from acp_services.data.transfer import ExpectedDigest, normalize_relative_path, sha256_bytes

__all__ = [
    "ARTIFACT_REF_SCHEMA",
    "ARTIFACT_REF_VERSION",
    "MAX_ARTIFACT_BYTES",
    "ArtifactAuthorization",
    "ArtifactError",
    "ArtifactNotPreparedError",
    "ArtifactDigestMismatchError",
    "ArtifactGenerationMismatchError",
    "ArtifactAttemptMismatchError",
    "ArtifactPathRefusedError",
    "ArtifactAuthorizationError",
    "ArtifactRemoteCacheMissingError",
    "ArtifactRef",
    "ArtifactRead",
    "ArtifactSourcePort",
    "ArtifactSourceRead",
    "ArtifactStore",
    "ArtifactPrepareExecutor",
    "is_safe_artifact_token",
    "normalize_artifact_digest",
    "resolve_artifact_read",
    "validate_artifact_relative_path",
    "validation_errors_for_artifact",
]

#: Frozen wire schema id of :class:`ArtifactRef`.
ARTIFACT_REF_SCHEMA = "acp2.artifact-ref.v1"
#: Frozen ArtifactRef version (bumped only with the contract).
ARTIFACT_REF_VERSION = 1
#: Hard cap on a stored artifact body (local verification reads it whole).
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

#: Job/attempt/resource tokens never carry a path separator or traversal.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PREFIXED = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA256_BARE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Errors (every refusal is named; none is an empty body)
# ---------------------------------------------------------------------------


class ArtifactError(RuntimeError):
    """Base error for the artifact lifecycle."""

    code = "artifact_error"
    http_status = 500

    def __init__(self, message: str = "", *, params: Mapping[str, Any] | None = None) -> None:
        super().__init__(message or self.code)
        self.params: dict[str, Any] = dict(params or {})


class ArtifactNotPreparedError(ArtifactError):
    """The referenced artifact has no prepared body (cold cache / evicted)."""

    code = "not_prepared"
    http_status = 409


class ArtifactDigestMismatchError(ArtifactError):
    """The requested or stored digest does not match the bytes."""

    code = "digest_mismatch"
    http_status = 409


class ArtifactGenerationMismatchError(ArtifactError):
    """The requested generation is stale (a newer generation is authoritative)."""

    code = "generation_stale"
    http_status = 409


class ArtifactAttemptMismatchError(ArtifactError):
    """The requested attempt is not the attempt the body belongs to."""

    code = "attempt_mismatch"
    http_status = 409


class ArtifactPathRefusedError(ArtifactError):
    """A token or relative path would escape the controlled artifact root."""

    code = "path_traversal"
    http_status = 400


class ArtifactAuthorizationError(ArtifactError):
    """The authorization projection denies this artifact operation."""

    code = "permission_revoked"
    http_status = 403


class ArtifactRemoteCacheMissingError(ArtifactNotPreparedError):
    """A remote artifact is absent from the controlled cache (no fetch on read)."""

    code = "remote_cache_missing"


# ---------------------------------------------------------------------------
# Frozen reference
# ---------------------------------------------------------------------------


def is_safe_artifact_token(value: str) -> bool:
    """True when *value* is a single, non-traversing job/artifact token."""
    return bool(_SAFE_TOKEN.match(str(value or "")))


def normalize_artifact_digest(value: str | None) -> str:
    """Return a ``sha256:<64 hex>`` digest, or ``""`` when not supplied.

    Raises:
        ArtifactError: The value is present but not a sha256 digest.
    """
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if _SHA256_BARE.match(text):
        text = "sha256:" + text
    if not _SHA256_PREFIXED.match(text):
        raise ArtifactError(
            f"invalid artifact digest {value!r}",
            params={"field": "digest", "rule": "sha256"},
        )
    return text


def validate_artifact_relative_path(value: str) -> str:
    """Refuse absolute/traversing artifact source paths (no filesystem access)."""
    text = str(value or "")
    if not text or "\x00" in text:
        raise ArtifactPathRefusedError("artifact relative_path is required")
    if text.startswith(("/", "\\")) or (len(text) > 1 and text[1] == ":"):
        raise ArtifactPathRefusedError(f"unsafe artifact relative_path: {text!r}")
    normalized = normalize_relative_path(text)
    parts = Path(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArtifactPathRefusedError(f"unsafe artifact relative_path: {text!r}")
    return normalized


#: Backwards-compatible private alias (the public name is the frozen one).
_validate_relative_artifact_path = validate_artifact_relative_path


@dataclass(frozen=True)
class ArtifactAuthorization:
    """The authorization projection frozen onto an artifact ref.

    This is *evidence of the boundary that admitted the operation*, not a new
    authority: the route still calls ``_require_entry_access`` on every request.
    """

    principal_id: str = ""
    project_id: str = ""
    scope: str = "local-single"
    action: str = "read"
    allowed: bool = True
    authorization_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "project_id": self.project_id,
            "scope": self.scope,
            "action": self.action,
            "allowed": bool(self.allowed),
            "authorization_version": int(self.authorization_version),
        }


@dataclass(frozen=True)
class ArtifactRef:
    """The only verifiable artifact reference (B12).

    ``digest`` is ``sha256:<64 hex>`` and ``size_bytes`` is the exact published
    size; both are committed by the prepare operation and re-verified by every
    read.  ``authorization`` is an inert projection (it never widens access on
    its own).  ``source_schema``/``source_refs`` record where the bytes came
    from (a task-relative file or A's EvidenceReport schema) without the
    platform interpreting them.
    """

    job_id: str
    attempt: int
    resource_id: str
    generation: int
    digest: str = ""
    size_bytes: int = 0
    availability: Availability = Availability.ABSENT
    schema: str = ARTIFACT_REF_SCHEMA
    version: int = ARTIFACT_REF_VERSION
    media_type: str = "application/octet-stream"
    relative_path: str = ""
    source_schema: str = ""
    source_refs: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    authorization: ArtifactAuthorization = field(default_factory=ArtifactAuthorization)
    operation_id: str = ""

    def to_dict(self, *, include_body_ref: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": self.schema,
            "version": int(self.version),
            "job_id": self.job_id,
            "attempt": int(self.attempt),
            "resource_id": self.resource_id,
            "generation": int(self.generation),
            "digest": self.digest,
            "size_bytes": int(self.size_bytes),
            "availability": self.availability.value,
            "media_type": self.media_type,
            "relative_path": self.relative_path,
            "source_schema": self.source_schema,
            "source_refs": [dict(item) for item in self.source_refs],
            "authorization": self.authorization.to_dict(),
            "operation_id": self.operation_id,
        }
        if include_body_ref:
            payload["body_ref"] = None
        return payload


def validation_errors_for_artifact(ref: ArtifactRef) -> list[str]:
    """Return structural errors for an ArtifactRef (empty when valid)."""
    errors: list[str] = []
    if ref.schema != ARTIFACT_REF_SCHEMA:
        errors.append("artifact_ref.invalid_schema")
    if int(ref.version) != ARTIFACT_REF_VERSION:
        errors.append("artifact_ref.invalid_version")
    if not is_safe_artifact_token(ref.job_id):
        errors.append("artifact_ref.invalid_job_id")
    if not is_safe_artifact_token(ref.resource_id):
        errors.append("artifact_ref.invalid_resource_id")
    if int(ref.attempt) < 1:
        errors.append("artifact_ref.invalid_attempt")
    if int(ref.generation) < 1:
        errors.append("artifact_ref.invalid_generation")
    try:
        digest = normalize_artifact_digest(ref.digest)
    except ArtifactError:
        errors.append("artifact_ref.invalid_digest")
    else:
        if not digest:
            errors.append("artifact_ref.missing_digest")
    if int(ref.size_bytes) < 0:
        errors.append("artifact_ref.invalid_size")
    return errors


# ---------------------------------------------------------------------------
# Source port (implemented on the legacy side; never fetches)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactSourceRead:
    """The result of reading one artifact source without any fetch.

    ``reason`` is a named refusal token (``not_prepared``,
    ``remote_cache_missing``, ``path_traversal``) and is empty on success.
    """

    present: bool
    data: bytes = b""
    reason: str = ""


@runtime_checkable
class ArtifactSourcePort(Protocol):
    """Read one artifact body from a controlled root (no network side effects)."""

    def read(
        self,
        *,
        job_id: str,
        attempt: int,
        relative_path: str,
        max_bytes: int,
    ) -> ArtifactSourceRead:
        """Return the source bytes, or a named absence; never fetch."""
        ...


# ---------------------------------------------------------------------------
# Store (content-addressed, atomic, local-only)
# ---------------------------------------------------------------------------


class ArtifactStore:
    """Atomic artifact publication store rooted at a controlled directory.

    Layout::

        <root>/<job_id>/<attempt>/<resource_id>/<generation>/<digest>.body
        <root>/<job_id>/<attempt>/<resource_id>/<generation>/<digest>.meta.json

    Every path component is a validated token and every integer is positive, so
    a caller-controlled id or relative path can never escape *root*.  The body
    is written to a sibling ``.tmp`` file and ``os.replace``d; the metadata
    sidecar is written last.  A read recomputes the digest from the opened body
    and compares it with both the sidecar and the requested ref before returning.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # -- layout ---------------------------------------------------------

    def _generation_dir(
        self,
        *,
        job_id: str,
        attempt: int,
        resource_id: str,
        generation: int,
    ) -> Path:
        if not is_safe_artifact_token(job_id):
            raise ArtifactPathRefusedError(f"unsafe artifact job id: {job_id!r}")
        if not is_safe_artifact_token(resource_id):
            raise ArtifactPathRefusedError(f"unsafe artifact resource id: {resource_id!r}")
        if int(attempt) < 1:
            raise ArtifactPathRefusedError(f"invalid artifact attempt: {attempt!r}")
        if int(generation) < 1:
            raise ArtifactPathRefusedError(f"invalid artifact generation: {generation!r}")
        return self.root / str(job_id) / str(int(attempt)) / str(resource_id) / str(int(generation))

    def _body_path(self, ref: ArtifactRef) -> Path:
        if not ref.digest.startswith("sha256:"):
            raise ArtifactDigestMismatchError("artifact ref carries no digest")
        return (
            self._generation_dir(
                job_id=ref.job_id,
                attempt=ref.attempt,
                resource_id=ref.resource_id,
                generation=ref.generation,
            )
            / f"{ref.digest.removeprefix('sha256:')}.body"
        )

    def _meta_path(self, body: Path) -> Path:
        return body.with_suffix(".meta.json")

    # -- publish --------------------------------------------------------

    def publish(
        self,
        ref: ArtifactRef,
        body: bytes,
        *,
        expected: ExpectedDigest | None = None,
    ) -> ArtifactRef:
        """Verify and atomically publish one artifact body.

        Args:
            ref: Identity of the artifact (digest/size are re-derived here).
            body: The exact bytes to publish.
            expected: Optional trusted expected digest/size (a supplied
                ``expected_digest`` from the prepare request).  A mismatch is a
                named :class:`ArtifactDigestMismatchError`; the bytes are never
                published.

        Returns:
            The committed ref (``digest``/``size_bytes``/``availability`` set).
        """
        size = len(body)
        if size > MAX_ARTIFACT_BYTES:
            raise ArtifactError(
                f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes",
                params={"field": "size_bytes", "rule": "too_large"},
            )
        observed_hex = sha256_bytes(body)
        observed = "sha256:" + observed_hex
        if expected is not None and not expected.matches(sha256=observed_hex, size_bytes=size):
            raise ArtifactDigestMismatchError(
                "artifact body does not match the expected digest/size",
                params={
                    "expected": normalize_artifact_digest(expected.sha256),
                    "observed": observed,
                    "expected_size_bytes": expected.size_bytes,
                    "observed_size_bytes": size,
                },
            )
        requested = normalize_artifact_digest(ref.digest)
        if requested and requested != observed:
            raise ArtifactDigestMismatchError(
                "artifact body does not match the requested digest",
                params={"expected": requested, "observed": observed},
            )
        committed = replace(
            ref,
            digest=observed,
            size_bytes=size,
            availability=Availability.READY,
        )
        body_path = self._body_path(committed)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, tmp_name = tempfile.mkstemp(
            prefix=".artifact-", suffix=".tmp", dir=str(body_path.parent)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, body_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        meta = committed.to_dict()
        meta["published_at"] = _utc_now_iso()
        meta_path = self._meta_path(body_path)
        descriptor, tmp_meta = tempfile.mkstemp(
            prefix=".artifact-", suffix=".meta.tmp", dir=str(meta_path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_meta, meta_path)
        except BaseException:
            try:
                os.unlink(tmp_meta)
            except OSError:
                pass
            raise
        return committed

    # -- query ----------------------------------------------------------

    def lookup(
        self,
        *,
        job_id: str,
        attempt: int,
        resource_id: str,
        generation: int,
        digest: str | None = None,
    ) -> ArtifactRef | None:
        """Return the published ref for one generation (or ``None``)."""
        directory = self._generation_dir(
            job_id=job_id,
            attempt=attempt,
            resource_id=resource_id,
            generation=generation,
        )
        if not directory.is_dir():
            return None
        requested = normalize_artifact_digest(digest)
        candidates = sorted(directory.glob("*.meta.json"))
        if requested:
            wanted = directory / f"{requested.removeprefix('sha256:')}.meta.json"
            candidates = [wanted] if wanted.is_file() else []
        for meta_path in candidates:
            try:
                payload = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            try:
                ref = _ref_from_projection(payload)
            except (KeyError, TypeError, ValueError):
                continue
            # A metadata sidecar without its body is an evicted/partial
            # publication: it must never be reported as a prepared ref.
            if self._body_path(ref).is_file():
                return ref
        return None

    def has_newer_generation(
        self,
        *,
        job_id: str,
        attempt: int,
        resource_id: str,
        generation: int,
    ) -> bool:
        """True when a generation strictly newer than *generation* is published."""
        parent = self._generation_dir(
            job_id=job_id,
            attempt=attempt,
            resource_id=resource_id,
            generation=1,
        ).parent
        if not parent.is_dir():
            return False
        for entry in parent.iterdir():
            if not entry.is_dir() or not entry.name.isdigit():
                continue
            if int(entry.name) > int(generation) and any(entry.glob("*.body")):
                return True
        return False

    def read(self, ref: ArtifactRef) -> bytes:
        """Open and verify one published body; no fetch, no parse.

        Raises:
            ArtifactNotPreparedError: The body or metadata sidecar is missing.
            ArtifactDigestMismatchError: The stored bytes do not match the ref.
        """
        body_path = self._body_path(ref)
        meta_path = self._meta_path(body_path)
        try:
            meta_payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ArtifactNotPreparedError(
                f"artifact body not prepared: {ref.resource_id}",
                params={"reason": "artifact_body_missing"},
            ) from exc
        except ValueError as exc:
            raise ArtifactDigestMismatchError(
                f"artifact metadata is corrupt: {ref.resource_id}",
                params={"reason": "artifact_metadata_corrupt"},
            ) from exc
        if not isinstance(meta_payload, dict):
            raise ArtifactDigestMismatchError(
                f"artifact metadata is corrupt: {ref.resource_id}",
                params={"reason": "artifact_metadata_corrupt"},
            )
        try:
            with body_path.open("rb") as handle:
                body = handle.read()
        except OSError as exc:
            raise ArtifactNotPreparedError(
                f"artifact body not prepared: {ref.resource_id}",
                params={"reason": "artifact_body_missing"},
            ) from exc
        observed = "sha256:" + sha256_bytes(body)
        recorded = normalize_artifact_digest(str(meta_payload.get("digest") or ""))
        if observed != recorded or observed != normalize_artifact_digest(ref.digest):
            raise ArtifactDigestMismatchError(
                "stored artifact bytes do not match the committed digest",
                params={"expected": ref.digest, "observed": observed},
            )
        if int(meta_payload.get("size_bytes") or -1) != len(body):
            raise ArtifactDigestMismatchError(
                "stored artifact size does not match its metadata",
                params={"field": "size_bytes"},
            )
        return body

    def evict(
        self,
        *,
        job_id: str,
        attempt: int,
        resource_id: str,
        generation: int,
    ) -> bool:
        """Remove one published generation (idempotent)."""
        directory = self._generation_dir(
            job_id=job_id,
            attempt=attempt,
            resource_id=resource_id,
            generation=generation,
        )
        if not directory.is_dir():
            return False
        for entry in directory.iterdir():
            try:
                entry.unlink()
            except OSError:
                return False
        try:
            directory.rmdir()
        except OSError:
            pass
        return True


def _ref_from_projection(payload: Mapping[str, Any]) -> ArtifactRef:
    """Rebuild an :class:`ArtifactRef` from its stored JSON projection."""
    authorization = payload.get("authorization")
    auth = ArtifactAuthorization(
        principal_id=str((authorization or {}).get("principal_id") or ""),
        project_id=str((authorization or {}).get("project_id") or ""),
        scope=str((authorization or {}).get("scope") or "local-single"),
        action=str((authorization or {}).get("action") or "read"),
        allowed=bool((authorization or {}).get("allowed", True)),
        authorization_version=int((authorization or {}).get("authorization_version") or 0),
    )
    refs = payload.get("source_refs")
    return ArtifactRef(
        job_id=str(payload["job_id"]),
        attempt=int(payload["attempt"]),
        resource_id=str(payload["resource_id"]),
        generation=int(payload["generation"]),
        digest=str(payload.get("digest") or ""),
        size_bytes=int(payload.get("size_bytes") or 0),
        availability=Availability(str(payload.get("availability") or "absent")),
        schema=str(payload.get("schema") or ARTIFACT_REF_SCHEMA),
        version=int(payload.get("version") or ARTIFACT_REF_VERSION),
        media_type=str(payload.get("media_type") or "application/octet-stream"),
        relative_path=str(payload.get("relative_path") or ""),
        source_schema=str(payload.get("source_schema") or ""),
        source_refs=tuple(item for item in (refs or []) if isinstance(item, Mapping)),
        authorization=auth,
        operation_id=str(payload.get("operation_id") or ""),
    )


def _utc_now_iso() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# ---------------------------------------------------------------------------
# Prepare executor
# ---------------------------------------------------------------------------


class ArtifactPrepareExecutor:
    """Prepare one artifact from a controlled source or an inline evidence doc.

    This is the injected mechanism behind a ``purpose=artifact`` lease.  It
    performs exactly one of:

    * read the task-relative source file through
      :class:`ArtifactSourcePort` (cache-only; a miss is named), or
    * serialize the caller-supplied ``evidence_report`` mapping verbatim.

    then verifies the optional expected digest/size, publishes the bytes to the
    :class:`ArtifactStore`, and reports ``prepared=True`` with the committed
    size/digest.  It never marks availability itself — the prepare worker's
    completion CAS does that.
    """

    def __init__(
        self,
        *,
        source: ArtifactSourcePort,
        store: ArtifactStore,
        authorization: Callable[[PrepareLease], bool] | None = None,
        max_bytes: int = MAX_ARTIFACT_BYTES,
    ) -> None:
        self._source = source
        self._store = store
        self._authorization = authorization
        self._max_bytes = max(1, int(max_bytes))

    def execute(self, lease: PrepareLease) -> PrepareExecutionResult:
        payload = dict(lease.payload or {})
        if str(payload.get("purpose") or "") != "artifact":
            return _refuse("unsupported_capability")
        job_id = str(lease.job_id)
        artifact_id = str(lease.resource_id)
        attempt = int(lease.attempt)
        generation = int(lease.resource_generation or payload.get("generation") or 1)
        try:
            if not is_safe_artifact_token(job_id) or not is_safe_artifact_token(artifact_id):
                raise ArtifactPathRefusedError("unsafe artifact token")
            if attempt < 1 or generation < 1:
                raise ArtifactPathRefusedError("invalid artifact attempt/generation")
            if self._authorization is not None and not self._authorization(lease):
                raise ArtifactAuthorizationError("artifact prepare authorization denied")
            evidence_report = payload.get("evidence_report")
            if evidence_report is not None:
                if not isinstance(evidence_report, Mapping):
                    return _refuse("invalid_parameter")
                body = _canonical_json_bytes(evidence_report)
                source_schema = str(payload.get("source_schema") or "acp2.science.evidence.v1")
                source_refs = _source_refs(payload.get("source_refs"))
                relative_path = str(payload.get("relative_path") or f"{artifact_id}.json")
                try:
                    relative_path = validate_artifact_relative_path(relative_path)
                except ArtifactPathRefusedError:
                    relative_path = f"{artifact_id}.json"
            else:
                raw_relative_path = str(payload.get("relative_path") or "")
                if not raw_relative_path:
                    return _refuse("invalid_parameter")
                relative_path = validate_artifact_relative_path(raw_relative_path)
                try:
                    read = self._source.read(
                        job_id=job_id,
                        attempt=attempt,
                        relative_path=relative_path,
                        max_bytes=self._max_bytes,
                    )
                except (OSError, ValueError) as exc:
                    return _refuse(
                        "path_traversal" if "path" in str(exc).lower() else "not_prepared"
                    )
                if not read.present:
                    return _refuse(read.reason or "not_prepared")
                body = read.data
                source_schema = str(payload.get("source_schema") or "")
                source_refs = _source_refs(payload.get("source_refs"))
            if len(body) > self._max_bytes:
                return _refuse("artifact_too_large")
            expected = self._expected(payload)
            ref = ArtifactRef(
                job_id=job_id,
                attempt=attempt,
                resource_id=artifact_id,
                generation=generation,
                digest="",
                size_bytes=len(body),
                availability=Availability.PREPARING,
                media_type=str(payload.get("media_type") or _media_type(relative_path)),
                relative_path=relative_path,
                source_schema=source_schema,
                source_refs=source_refs,
            )
            published = self._store.publish(ref, body, expected=expected)
        except ArtifactAuthorizationError:
            return _refuse("permission_revoked")
        except ArtifactPathRefusedError:
            return _refuse("path_traversal")
        except ArtifactDigestMismatchError as exc:
            return _refuse(f"digest_mismatch:{exc}", retryable=False)
        except ArtifactError:
            return _refuse("artifact_error", retryable=False)
        return PrepareExecutionResult(
            prepared=True,
            availability=Availability.READY,
            detail="artifact_ready",
            retryable=False,
            size_bytes=published.size_bytes,
            sha256=published.digest.removeprefix("sha256:"),
        )

    @staticmethod
    def _expected(payload: Mapping[str, Any]) -> ExpectedDigest | None:
        raw = payload.get("expected_digest") or payload.get("content_digest")
        digest = normalize_artifact_digest(str(raw or ""))
        if not digest:
            return None
        size = payload.get("expected_size_bytes")
        return ExpectedDigest(
            sha256=digest.removeprefix("sha256:"),
            size_bytes=None if size is None else int(size),
            source="artifact_ref",
        )


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"evidence report is not JSON-serialisable: {exc}") from exc
    return text.encode("utf-8")


def _source_refs(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict(item) for item in value if isinstance(item, Mapping))


def _media_type(relative_path: str) -> str:
    suffix = Path(relative_path).suffix.lower()
    if suffix == ".json":
        return "application/json"
    if suffix == ".xyz":
        return "chemical/x-xyz"
    if suffix in {".txt", ".log", ".out"}:
        return "text/plain"
    return "application/octet-stream"


def _refuse(reason: str, *, retryable: bool = False) -> PrepareExecutionResult:
    availability = Availability.PREPARING if retryable else Availability.UNAVAILABLE
    return PrepareExecutionResult(
        prepared=False,
        availability=availability,
        detail=reason,
        retryable=retryable,
    )


# ---------------------------------------------------------------------------
# Read resolution (query-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRead:
    """The verified result of one artifact read (no unverified bytes)."""

    ref: ArtifactRef
    body: bytes
    encoding: str = "identity"
    truncated: bool = False

    @property
    def digest(self) -> str:
        return self.ref.digest

    @property
    def size_bytes(self) -> int:
        return int(self.ref.size_bytes)

    @property
    def generation(self) -> int:
        return int(self.ref.generation)

    def to_dict(self) -> dict[str, Any]:
        import base64

        body_text: str | None
        encoding = self.encoding
        if self.encoding == "base64":
            body_text = base64.b64encode(self.body).decode("ascii")
        else:
            try:
                body_text = self.body.decode("utf-8")
            except UnicodeDecodeError:
                # The bytes are not UTF-8 (or a truncation split a character):
                # report what the body actually is instead of mislabelling it.
                body_text = base64.b64encode(self.body).decode("ascii")
                encoding = "base64"
        return {
            "schema": self.ref.schema,
            "version": self.ref.version,
            "artifact_ref": self.ref.to_dict(),
            "media_type": self.ref.media_type,
            "size_bytes": self.ref.size_bytes,
            "digest": self.ref.digest,
            "generation": self.ref.generation,
            "availability": self.ref.availability.value,
            "encoding": encoding,
            "body": body_text,
            "body_ref": None,
            "truncated": bool(self.truncated),
            "source_schema": self.ref.source_schema,
            "source_refs": [dict(item) for item in self.ref.source_refs],
        }


def resolve_artifact_read(
    service: PrepareOperationService,
    store: ArtifactStore,
    *,
    job_id: str,
    attempt: int,
    resource_id: str,
    generation: int,
    digest: str,
    permission_scope: str = "local-single",
    authorization: ArtifactAuthorization | None = None,
    max_bytes: int = 256 * 1024,
    encoding: str = "identity",
) -> ArtifactRead:
    """Resolve and verify one prepared artifact without any side effect.

    Checks, in order (every refusal is named, none substitutes empty bytes):

    1. a prepare operation exists for the logical ``job/attempt/resource``;
    2. the requested attempt equals the operation's attempt;
    3. no newer generation is authoritative (an old generation is stale);
    4. the operation is ``succeeded`` and ``ready``;
    5. the requested generation and digest equal the committed values;
    6. the published body exists (an evicted body is ``not_prepared``);
    7. the bytes recompute to the committed digest.

    Args:
        service: The persistent operation authority (read-only here).
        store: The artifact publication store (read-only here).
        job_id: Task id.
        attempt: Exact attempt the caller wants.
        resource_id: Artifact id (the operation's ``resource_id``).
        generation: Exact resource generation.
        digest: Requested ``sha256:`` digest (required).
        permission_scope: Scope used to look the operation up.
        authorization: Inert projection to freeze onto the returned ref.
        max_bytes: Bound for the returned body.
        encoding: ``identity`` or ``base64``.

    Raises:
        ArtifactNotPreparedError: No operation/body (cold cache).
        ArtifactAttemptMismatchError: Cross-attempt read.
        ArtifactGenerationMismatchError: Old generation.
        ArtifactDigestMismatchError: Digest/corruption mismatch.
    """
    if not is_safe_artifact_token(job_id) or not is_safe_artifact_token(resource_id):
        raise ArtifactPathRefusedError("unsafe artifact token")
    if int(attempt) < 1 or int(generation) < 1:
        raise ArtifactPathRefusedError("invalid attempt/generation")
    requested_digest = normalize_artifact_digest(digest)
    if not requested_digest:
        raise ArtifactDigestMismatchError(
            "an artifact read requires an expected digest",
            params={"field": "digest", "rule": "required"},
        )
    record = service.find(
        job_id=job_id,
        resource_id=resource_id,
        attempt=int(attempt),
        permission_scope=permission_scope,
    )
    if record is None:
        raise ArtifactNotPreparedError(
            "artifact is not prepared", params={"reason": "no_operation"}
        )
    if int(record.attempt) != int(attempt):
        raise ArtifactAttemptMismatchError(
            "artifact belongs to a different attempt",
            params={"requested_attempt": int(attempt), "operation_attempt": int(record.attempt)},
        )
    if int(record.resource_generation) > int(generation):
        raise ArtifactGenerationMismatchError(
            "artifact generation is stale",
            params={
                "requested_generation": int(generation),
                "current_generation": int(record.resource_generation),
            },
        )
    if store.has_newer_generation(
        job_id=job_id,
        attempt=int(attempt),
        resource_id=resource_id,
        generation=int(generation),
    ):
        raise ArtifactGenerationMismatchError(
            "artifact generation is stale",
            params={"requested_generation": int(generation), "reason": "newer_generation"},
        )
    if record.state is not PrepareState.SUCCEEDED:
        raise ArtifactNotPreparedError(
            "artifact is not ready",
            params={"reason": f"operation_{record.state.value}"},
        )
    if record.availability is not Availability.READY:
        raise ArtifactNotPreparedError(
            "artifact is not ready",
            params={"reason": f"availability_{record.availability.value}"},
        )
    if int(record.resource_generation) != int(generation):
        raise ArtifactGenerationMismatchError(
            "requested generation does not match the committed generation",
            params={
                "requested_generation": int(generation),
                "committed_generation": int(record.resource_generation),
            },
        )
    committed = normalize_artifact_digest("sha256:" + str(record.sha256 or ""))
    if not committed or committed != requested_digest:
        raise ArtifactDigestMismatchError(
            "requested digest does not match the committed digest",
            params={"requested": requested_digest, "committed": committed or None},
        )
    payload = dict(record.payload or {})
    stored = store.lookup(
        job_id=job_id,
        attempt=int(attempt),
        resource_id=resource_id,
        generation=int(generation),
        digest=committed,
    )
    if stored is None:
        raise ArtifactNotPreparedError(
            "artifact body is not prepared",
            params={"reason": "artifact_body_missing"},
        )
    if int(stored.size_bytes) != int(record.size_bytes or -1):
        raise ArtifactDigestMismatchError(
            "artifact size does not match the committed size",
            params={
                "committed": int(record.size_bytes or -1),
                "stored": int(stored.size_bytes),
            },
        )
    body = store.read(stored)
    ref = replace(
        stored,
        operation_id=record.operation_id,
        authorization=authorization or stored.authorization,
        availability=Availability.READY,
        relative_path=str(payload.get("relative_path") or stored.relative_path),
        source_schema=str(payload.get("source_schema") or stored.source_schema),
    )
    total = len(body)
    limit = max(0, int(max_bytes))
    truncated = limit < total
    return ArtifactRead(
        ref=ref,
        body=body[:limit] if truncated else body,
        encoding="base64" if str(encoding) == "base64" else "identity",
        truncated=truncated,
    )


def operation_to_artifact_ref(
    record: PrepareOperationRecord,
    *,
    authorization: ArtifactAuthorization | None = None,
) -> ArtifactRef | None:
    """Project an artifact prepare-operation row onto a frozen ArtifactRef."""
    payload = dict(record.payload or {})
    if str(payload.get("purpose") or "") != "artifact":
        return None
    digest = ""
    if record.sha256:
        digest = normalize_artifact_digest("sha256:" + str(record.sha256))
    else:
        try:
            digest = normalize_artifact_digest(str(payload.get("expected_digest") or ""))
        except ArtifactError:
            digest = ""
    return ArtifactRef(
        job_id=record.job_id,
        attempt=int(record.attempt),
        resource_id=record.resource_id,
        generation=int(record.resource_generation),
        digest=digest,
        size_bytes=int(record.size_bytes or 0),
        availability=record.availability,
        media_type=str(
            payload.get("media_type") or _media_type(str(payload.get("relative_path") or ""))
        ),
        relative_path=str(payload.get("relative_path") or ""),
        source_schema=str(payload.get("source_schema") or ""),
        source_refs=_source_refs(payload.get("source_refs")),
        authorization=authorization or ArtifactAuthorization(),
        operation_id=record.operation_id,
    )
