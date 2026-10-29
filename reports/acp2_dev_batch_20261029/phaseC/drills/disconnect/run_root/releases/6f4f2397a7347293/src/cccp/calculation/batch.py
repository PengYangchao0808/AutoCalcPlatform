"""Generic homogeneous-task batch executor: concurrency, cache, progress.

Task-agnostic orchestration for N homogeneous single-task items.  The
executor receives a *single-task execution function* (e.g. the future
``run_singlepoint``) and calls it per item; it never calls backend
capability methods (``single_point`` / ``optimize`` / ``frequency``) and
never imports task-execution modules.  Multi-capability step chains
(opt -> freq -> sp -> thermochemistry) stay in ACP.

Per-item pipeline (plan todo 12): validate request -> resolve effective
scientific parameters -> generate cache identity -> validate cache record
and artifacts -> only on a miss run the runtime precheck and call the
injected single-task execution function.  Parameter resolution happens once
per item through :func:`resolve_effective_params` (backed by the shared
``cccp.qc.resolved_spec`` rules); the resolved object feeds BOTH the cache
identity and the execution call, so the single-task core must not re-resolve.

Cache identity covers task / backend / effective parameters / electronic
state / input contents / auxiliary input (Hessian, orbital) contents /
version constraint / cache schema policy.  The cache record stores the
*actual* software version; version matching uses
:func:`version_matches`.  A cache record whose completeness cannot be
proven (old schema, missing ``complete`` flag, unreadable payload) is an
explicit miss.  A complete, version-matched cache hit does NOT require the
backend binaries to be available — scientific validation and runtime
precheck are separated; only real execution runs the precheck.

Batch resources are separate from per-item resources: ``concurrency`` is
how many items run in parallel, ``per_item_cores`` is what each item's
execution receives, and ``concurrency * per_item_cores`` must fit the
``total_core_budget`` (one ``nproc`` is never both).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, TypeAlias

from cccp.calculation.errors import TaskInputError
from cccp.qc.resolved_spec import ResolvedCalculationSpec, resolve_calculation_spec

logger = logging.getLogger(__name__)

CACHE_SCHEMA_VERSION = 1

RequestPayload: TypeAlias = Mapping[str, object]

__all__ = [
    "CACHE_SCHEMA_VERSION",
    "ArtifactRecord",
    "BatchEntry",
    "BatchItemResult",
    "BatchResources",
    "BatchRunResult",
    "CacheIdentity",
    "CacheRecord",
    "CacheStore",
    "CacheVersionPolicy",
    "EffectiveTaskParams",
    "FileSystemCacheStore",
    "ItemResources",
    "ItemRunOutcome",
    "MemoryCacheStore",
    "SingleTaskExecutor",
    "cache_identity",
    "resolve_effective_params",
    "run_batch",
    "version_matches",
]


class CacheVersionPolicy(str, Enum):
    """Cache compatibility rule when the caller gives no version constraint."""

    #: Default: a hit requires the record to name the actual software
    #: version that produced it; missing version info cannot prove
    #: compatibility and is judged an explicit miss.
    REQUIRE_RECORDED = "require_recorded"
    #: Opt-in permissive policy: any recorded (or absent) version is
    #: accepted when no constraint is given.
    ANY = "any"


def _sha256_text(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _canonical_json(payload: object) -> str:
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise TaskInputError(f"not JSON-serialisable: {exc}") from exc


@dataclass(frozen=True)
class BatchEntry:
    """One homogeneous batch item: stable id + serialisable request payload."""

    entry_id: str
    request: RequestPayload


@dataclass(frozen=True)
class EffectiveTaskParams:
    """Effective scientific parameters for one item (single resolution point)."""

    task: str
    backend: str
    method: str | None
    resolved: ResolvedCalculationSpec
    charge: int
    multiplicity: int
    electronic_state: object | None
    input_hashes: tuple[tuple[str, str], ...]
    version_constraint: str | None

    def identity_payload(self) -> dict[str, Any]:
        """Cache-identity payload: science + contents + version + schema."""
        return {
            "cache_schema": CACHE_SCHEMA_VERSION,
            "task": self.task,
            "backend": self.backend,
            "effective_params": self.resolved.cache_signature(),
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "electronic_state": self.electronic_state,
            "inputs": dict(self.input_hashes),
            "version_constraint": self.version_constraint,
        }


@dataclass(frozen=True)
class CacheIdentity:
    """Deterministic cache identity for one item."""

    digest: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class ArtifactRecord:
    """One produced artifact: name, absolute path, sha256 of its content."""

    name: str
    path: str
    sha256: str


@dataclass(frozen=True)
class CacheRecord:
    """Persisted cache entry; completeness and schema are proven, not assumed."""

    identity_digest: str
    schema_version: int
    complete: bool
    software_version: str | None
    artifacts: tuple[ArtifactRecord, ...]
    result_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_digest": self.identity_digest,
            "schema_version": self.schema_version,
            "complete": self.complete,
            "software_version": self.software_version,
            "artifacts": [
                {"name": a.name, "path": a.path, "sha256": a.sha256} for a in self.artifacts
            ],
            "result_payload": self.result_payload,
        }

    @classmethod
    def from_dict(cls, payload: object) -> CacheRecord:
        """Strict parse: any missing/unknown-typed field means "cannot prove"."""
        if not isinstance(payload, Mapping):
            raise TaskInputError("cache record must be a mapping")
        for key in (
            "identity_digest",
            "schema_version",
            "complete",
            "software_version",
            "artifacts",
            "result_payload",
        ):
            if key not in payload:
                raise TaskInputError(f"cache record missing '{key}'")
        digest = payload["identity_digest"]
        schema = payload["schema_version"]
        complete = payload["complete"]
        version = payload["software_version"]
        artifacts = payload["artifacts"]
        result = payload["result_payload"]
        if not isinstance(digest, str) or not digest:
            raise TaskInputError("cache record identity_digest invalid")
        if isinstance(schema, bool) or not isinstance(schema, int):
            raise TaskInputError("cache record schema_version invalid")
        if not isinstance(complete, bool):
            raise TaskInputError("cache record complete flag invalid")
        if version is not None and not isinstance(version, str):
            raise TaskInputError("cache record software_version invalid")
        if not isinstance(artifacts, list) or not isinstance(result, Mapping):
            raise TaskInputError("cache record artifacts/result_payload invalid")
        parsed: list[ArtifactRecord] = []
        for item in artifacts:
            if not isinstance(item, Mapping):
                raise TaskInputError("cache artifact entry invalid")
            name, path, sha = item.get("name"), item.get("path"), item.get("sha256")
            if not all(isinstance(v, str) and v for v in (name, path, sha)):
                raise TaskInputError("cache artifact entry invalid")
            parsed.append(ArtifactRecord(name=name, path=path, sha256=sha))
        return cls(
            identity_digest=digest,
            schema_version=schema,
            complete=complete,
            software_version=version,
            artifacts=tuple(parsed),
            result_payload=dict(result),
        )


class CacheStore(Protocol):
    """Storage seam for cache records."""

    def read(self, digest: str) -> CacheRecord | None: ...

    def write(self, record: CacheRecord) -> None: ...


class MemoryCacheStore:
    """In-memory cache store (tests, ephemeral runs)."""

    def __init__(self) -> None:
        self._records: dict[str, CacheRecord] = {}

    def read(self, digest: str) -> CacheRecord | None:
        return self._records.get(digest)

    def write(self, record: CacheRecord) -> None:
        self._records[record.identity_digest] = record


class FileSystemCacheStore:
    """JSON-per-identity cache store with atomic writes and strict reads."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def read(self, digest: str) -> CacheRecord | None:
        path = self._root / f"{digest}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError):
            logger.warning("unreadable cache record %s — explicit miss", path)
            return None
        try:
            return CacheRecord.from_dict(payload)
        except TaskInputError as exc:
            logger.warning("unprovable cache record %s (%s) — explicit miss", path, exc)
            return None

    def write(self, record: CacheRecord) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        target = self._root / f"{record.identity_digest}.json"
        handle = tempfile.NamedTemporaryFile(
            dir=str(self._root), suffix=".tmp", delete=False, mode="w", encoding="utf-8"
        )
        try:
            json.dump(record.to_dict(), handle, indent=2, sort_keys=True)
            handle.close()
            os.replace(handle.name, target)
        except OSError:
            handle.close()
            try:
                os.unlink(handle.name)
            except OSError:
                logger.exception("failed to remove temporary cache file %s", handle.name)
            raise


@dataclass(frozen=True)
class BatchResources:
    """Batch-level budget, deliberately separate from per-item resources."""

    concurrency: int
    per_item_cores: int
    total_core_budget: int

    def __post_init__(self) -> None:
        for name, value in (
            ("concurrency", self.concurrency),
            ("per_item_cores", self.per_item_cores),
            ("total_core_budget", self.total_core_budget),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise TaskInputError(f"{name} must be a positive integer")
        if self.concurrency * self.per_item_cores > self.total_core_budget:
            raise TaskInputError(
                f"batch resource budget exceeded: concurrency={self.concurrency} * "
                f"per_item_cores={self.per_item_cores} = "
                f"{self.concurrency * self.per_item_cores} > "
                f"total_core_budget={self.total_core_budget}"
            )


@dataclass(frozen=True)
class ItemResources:
    """Per-item resource grant handed to the single-task execution function."""

    cores: int


@dataclass(frozen=True)
class ItemRunOutcome:
    """Normalized result of one single-task execution call."""

    success: bool
    payload: dict[str, Any] | None = None
    software_version: str | None = None
    artifact_paths: tuple[tuple[str, str], ...] = ()
    error_message: str | None = None


SingleTaskExecutor = Callable[[RequestPayload, EffectiveTaskParams, ItemResources], ItemRunOutcome]
RuntimePrecheck = Callable[[RequestPayload, EffectiveTaskParams], None]


@dataclass(frozen=True)
class BatchItemResult:
    """Per-item batch record."""

    entry_id: str
    status: str
    from_cache: bool
    cache_identity: str
    miss_reason: str | None
    payload: dict[str, Any] | None
    software_version: str | None
    error_message: str | None


@dataclass(frozen=True)
class BatchRunResult:
    """Batch summary with per-item records in input order."""

    results: tuple[BatchItemResult, ...]
    n_total: int
    n_success: int
    n_failed: int
    n_cache_hits: int
    wall_time_s: float


def resolve_effective_params(
    request: RequestPayload,
    *,
    run_config: Mapping[str, Any] | None = None,
) -> EffectiveTaskParams:
    """Resolve one item's effective scientific parameters — the single
    resolution point shared by cache preparation and the single-task core.

    Scientific fields flow through ``resolve_calculation_spec`` (priority:
    explicit > task_options > run_config > method defaults).  The returned
    object is passed to the injected execution function unchanged.
    """
    if not isinstance(request, Mapping):
        raise TaskInputError("batch request must be a mapping")

    def _str_field(key: str) -> str:
        value = request.get(key)
        if not isinstance(value, str) or not value.strip():
            raise TaskInputError(f"batch request '{key}' must be a non-empty string")
        return value

    def _int_field(key: str, default: int) -> int:
        value = request.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TaskInputError(f"batch request '{key}' must be an integer")
        return value

    def _mapping_field(key: str) -> Mapping[str, Any] | None:
        value = request.get(key)
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TaskInputError(f"batch request '{key}' must be a mapping")
        return value

    task = _str_field("task")
    backend = _str_field("backend")
    method_value = request.get("method")
    if method_value is not None and not isinstance(method_value, str):
        raise TaskInputError("batch request 'method' must be a string or null")
    version_value = request.get("version_constraint")
    if version_value is not None and not isinstance(version_value, str):
        raise TaskInputError("batch request 'version_constraint' must be a string or null")

    resolved = resolve_calculation_spec(
        method_value,
        explicit=_mapping_field("parameters"),
        task_options=_mapping_field("task_options"),
        run_config=run_config,
    )

    inputs = _mapping_field("inputs") or {}
    input_hashes: list[tuple[str, str]] = []
    for name in sorted(inputs):
        content = inputs[name]
        if not isinstance(content, (str, bytes)):
            raise TaskInputError(f"batch request input '{name}' content must be str or bytes")
        input_hashes.append((str(name), _sha256_text(content)))

    electronic_state = request.get("electronic_state")
    _canonical_json(electronic_state)

    return EffectiveTaskParams(
        task=task,
        backend=backend,
        method=method_value,
        resolved=resolved,
        charge=_int_field("charge", 0),
        multiplicity=_int_field("multiplicity", 1),
        electronic_state=electronic_state,
        input_hashes=tuple(input_hashes),
        version_constraint=version_value,
    )


def cache_identity(params: EffectiveTaskParams) -> CacheIdentity:
    """Deterministic identity over task/backend/effective science/contents."""
    payload = params.identity_payload()
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return CacheIdentity(digest=digest, payload=payload)


def version_matches(
    constraint: str | None,
    actual: str | None,
    policy: CacheVersionPolicy = CacheVersionPolicy.REQUIRE_RECORDED,
) -> bool:
    """Version-constraint matching for cache hits.

    A caller constraint must match the recorded actual version (exact
    string, or a ``*`` wildcard prefix/suffix).  Without a constraint the
    explicit policy decides: ``REQUIRE_RECORDED`` needs a recorded actual
    version (insufficient version info is an explicit miss); ``ANY`` is the
    opt-in permissive rule.
    """
    if constraint is not None:
        if actual is None:
            return False
        if "*" in constraint:
            prefix, _, suffix = constraint.partition("*")
            return actual.startswith(prefix) and actual.endswith(suffix)
        return actual == constraint
    if actual is None:
        return policy is CacheVersionPolicy.ANY
    return True


def _artifact_digest(path: Path) -> str | None:
    try:
        return _sha256_text(path.read_bytes())
    except OSError:
        return None


def _cache_lookup(
    cache: CacheStore,
    identity: CacheIdentity,
    policy: CacheVersionPolicy,
) -> tuple[CacheRecord | None, str]:
    """Validate a cache record and its artifacts; returns (record, miss_reason)."""
    record = cache.read(identity.digest)
    if record is None:
        return None, "no_record"
    if record.schema_version != CACHE_SCHEMA_VERSION:
        return None, "schema_mismatch"
    if not record.complete:
        return None, "incomplete_record"
    if record.identity_digest != identity.digest:
        return None, "identity_mismatch"
    if not version_matches(identity.payload.get("version_constraint"), record.software_version, policy):
        return None, "version_incompatible"
    for artifact in record.artifacts:
        path = Path(artifact.path)
        if not path.is_file():
            return None, "artifact_missing"
        if _artifact_digest(path) != artifact.sha256:
            return None, "artifact_hash_mismatch"
    return record, ""


def _collect_artifacts(
    artifact_paths: Sequence[tuple[str, str]],
) -> tuple[ArtifactRecord, ...] | None:
    records: list[ArtifactRecord] = []
    for name, raw_path in artifact_paths:
        path = Path(raw_path)
        digest = _artifact_digest(path) if path.is_file() else None
        if digest is None:
            logger.warning("declared artifact %s missing after run — cache not written", path)
            return None
        records.append(ArtifactRecord(name=str(name), path=str(path), sha256=digest))
    return tuple(records)


def run_batch(
    entries: Sequence[BatchEntry],
    execute: SingleTaskExecutor,
    *,
    cache: CacheStore,
    resources: BatchResources,
    run_config: Mapping[str, Any] | None = None,
    precheck: RuntimePrecheck | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    on_item_start: Callable[[str], None] | None = None,
    on_item_done: Callable[[BatchItemResult], None] | None = None,
    version_policy: CacheVersionPolicy = CacheVersionPolicy.REQUIRE_RECORDED,
) -> BatchRunResult:
    """Run homogeneous items concurrently with cache, precheck and progress.

    Raises:
        TaskInputError: For batch-level misuse (duplicate ids, heterogeneous
            items).  Per-item request validation failures are recorded as
            failed items and never abort siblings.
    """
    started = time.monotonic()
    entry_ids = [entry.entry_id for entry in entries]
    if len(set(entry_ids)) != len(entry_ids):
        raise TaskInputError("batch entry ids must be unique")

    total = len(entries)
    done = 0
    progress_lock = Lock()

    def _notify(result: BatchItemResult) -> None:
        nonlocal done
        with progress_lock:
            done += 1
            current = done
            if progress_callback is not None:
                try:
                    progress_callback(current, total)
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.exception("batch progress callback failed at %s/%s", current, total)
            if on_item_done is not None:
                try:
                    on_item_done(result)
                except (OSError, RuntimeError, TypeError, ValueError):
                    logger.exception(
                        "batch on_item_done callback failed for %s", result.entry_id
                    )

    prepared: list[tuple[BatchEntry, EffectiveTaskParams, CacheIdentity]] = []
    outcomes: dict[str, BatchItemResult] = {}
    homogeneity: tuple[object, ...] | None = None
    for entry in entries:
        try:
            params = resolve_effective_params(entry.request, run_config=run_config)
            identity = cache_identity(params)
        except TaskInputError as exc:
            failure = BatchItemResult(
                entry_id=entry.entry_id,
                status="failed",
                from_cache=False,
                cache_identity="",
                miss_reason=None,
                payload=None,
                software_version=None,
                error_message=str(exc),
            )
            outcomes[entry.entry_id] = failure
            _notify(failure)
            continue
        signature = (
            params.task,
            params.backend,
            _canonical_json(params.resolved.cache_signature()),
            params.charge,
            params.multiplicity,
            _canonical_json(params.electronic_state),
            params.version_constraint,
        )
        if homogeneity is None:
            homogeneity = signature
        elif signature != homogeneity:
            raise TaskInputError(
                "batch entries must be homogeneous (same task, backend and "
                "effective scientific parameters)"
            )
        prepared.append((entry, params, identity))

    item_resources = ItemResources(cores=resources.per_item_cores)

    def _run_one(
        entry: BatchEntry, params: EffectiveTaskParams, identity: CacheIdentity
    ) -> BatchItemResult:
        result = _resolve_one(entry, params, identity)
        _notify(result)
        return result

    def _resolve_one(
        entry: BatchEntry, params: EffectiveTaskParams, identity: CacheIdentity
    ) -> BatchItemResult:
        if on_item_start is not None:
            try:
                on_item_start(entry.entry_id)
            except (OSError, RuntimeError, TypeError, ValueError):
                logger.exception("batch on_item_start callback failed for %s", entry.entry_id)
        record, miss_reason = _cache_lookup(cache, identity, version_policy)
        if record is not None:
            return BatchItemResult(
                entry_id=entry.entry_id,
                status="success",
                from_cache=True,
                cache_identity=identity.digest,
                miss_reason=None,
                payload=record.result_payload,
                software_version=record.software_version,
                error_message=None,
            )
        try:
            if precheck is not None:
                precheck(entry.request, params)
            outcome = execute(entry.request, params, item_resources)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            message = str(exc).strip() or type(exc).__name__
            return BatchItemResult(
                entry_id=entry.entry_id,
                status="failed",
                from_cache=False,
                cache_identity=identity.digest,
                miss_reason=miss_reason,
                payload=None,
                software_version=None,
                error_message=message,
            )
        if not outcome.success:
            return BatchItemResult(
                entry_id=entry.entry_id,
                status="failed",
                from_cache=False,
                cache_identity=identity.digest,
                miss_reason=miss_reason,
                payload=None,
                software_version=outcome.software_version,
                error_message=outcome.error_message or "single-task execution failed",
            )
        artifacts = _collect_artifacts(outcome.artifact_paths)
        if artifacts is not None:
            try:
                cache.write(
                    CacheRecord(
                        identity_digest=identity.digest,
                        schema_version=CACHE_SCHEMA_VERSION,
                        complete=True,
                        software_version=outcome.software_version,
                        artifacts=artifacts,
                        result_payload=dict(outcome.payload or {}),
                    )
                )
            except OSError:
                logger.exception("cache write failed for %s — result still returned", entry.entry_id)
        return BatchItemResult(
            entry_id=entry.entry_id,
            status="success",
            from_cache=False,
            cache_identity=identity.digest,
            miss_reason=miss_reason,
            payload=outcome.payload,
            software_version=outcome.software_version,
            error_message=None,
        )

    if prepared:
        max_workers = min(resources.concurrency, len(prepared))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_map = {
                pool.submit(_run_one, entry, params, identity): entry.entry_id
                for entry, params, identity in prepared
            }
            for future, entry_id in future_map.items():
                outcomes[entry_id] = future.result()

    ordered = tuple(outcomes[entry_id] for entry_id in entry_ids)
    n_success = sum(1 for result in ordered if result.status == "success")
    n_hits = sum(1 for result in ordered if result.from_cache)
    return BatchRunResult(
        results=ordered,
        n_total=total,
        n_success=n_success,
        n_failed=total - n_success,
        n_cache_hits=n_hits,
        wall_time_s=time.monotonic() - started,
    )
