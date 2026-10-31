"""PES selection publication adapter (round-5 PR-05).

The ACP side of the publication use-case: it owns the file-based publication
intent (atomic JSON under ``RESULT/pes_search/publication_intent.json``) and the
ordered publication steps (rotation backup, structure materialisation, manifest
switch, authoritative review last).  Idempotency and recovery decisions come
from :class:`acp_services.runtime.selection_publication.SelectionPublicationService`
so there is no second implementation of the rules.

Publication steps are idempotent: a recovered publication re-runs only the
steps the intent does not record as complete, and every step is safe to repeat
with the same selection (same files, same manifest products, same review bytes).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from acp_services.runtime.selection_publication import (
    PublicationConflictError,
    PublicationInProgressError,
    PublicationLeaseLostError,
    SelectionPublicationService,
)

__all__ = [
    "PUBLICATION_INTENT_RELATIVE_PATH",
    "FileIntentStore",
    "PublicationConflictError",
    "PublicationInProgressError",
    "PublicationLeaseLostError",
    "PublicationRecoveryError",
    "StoreIntentStore",
    "load_publication_intent",
    "publication_digest",
    "run_publication",
]

PUBLICATION_INTENT_RELATIVE_PATH = "RESULT/pes_search/publication_intent.json"


class PublicationRecoveryError(ValueError):
    """A publication step failed; the intent records what a retry can resume."""

    def __init__(self, message: str, *, intent: Mapping[str, Any], completed: Sequence[str]) -> None:
        super().__init__(message)
        self.intent = dict(intent)
        self.completed = tuple(completed)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_publication_intent(task_root: Path | str) -> dict[str, Any] | None:
    """Read the publication intent (None when no publication was attempted)."""
    path = Path(task_root) / PUBLICATION_INTENT_RELATIVE_PATH
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class StoreIntentStore:
    """Publication intent persisted in the existing JobStore (plan §9.3).

    The API path uses this adapter; CLI/no-store paths fall back to
    :class:`FileIntentStore` so the pure review function stays usable.
    """

    def __init__(self, store: Any, job_id: str) -> None:
        self._store = store
        self._job_id = job_id

    def load(self) -> dict[str, Any] | None:
        loader = getattr(self._store, "load_selection_publication", None)
        if loader is None:
            raise RuntimeError(
                "the configured store has no selection-publication support"
            )
        return loader(self._job_id)

    def save(self, payload: dict[str, Any]) -> None:
        saver = getattr(self._store, "save_selection_publication", None)
        if saver is None:
            raise RuntimeError(
                "the configured store has no selection-publication support"
            )
        saver(self._job_id, payload)


class FileIntentStore:
    """Publication intent persisted next to the review it protects."""

    def __init__(self, task_root: Path | str) -> None:
        self._path = Path(task_root) / PUBLICATION_INTENT_RELATIVE_PATH

    def load(self) -> dict[str, Any] | None:
        if not self._path.is_file():
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save(self, payload: dict[str, Any]) -> None:
        _write_json_atomic(self._path, payload)


def publication_digest(
    *,
    job_id: str,
    candidates: Sequence[Mapping[str, Any]],
    note: str,
    restored_from: int | None,
    expected_revision: int | None,
    source_digest: str,
    source_task_status: str,
    frame_source: str,
) -> str:
    """Deterministic digest of the requested selection (idempotency payload)."""
    canonical = {
        "job_id": job_id,
        "candidates": [dict(item) for item in candidates],
        "note": note,
        "restored_from": restored_from,
        "expected_revision": expected_revision,
        "source_digest": source_digest,
        "source_task_status": source_task_status,
        "frame_source": frame_source,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def run_publication(
    task_root: Path | str,
    *,
    job_id: str,
    key: str | None,
    digest: str,
    expected_revision: int | None,
    current_revision: int,
    stages: Sequence[tuple[str, Callable[[], Any]]],
    intent_store: Any | None = None,
) -> tuple[Any, bool, dict[str, Any]]:
    """Run (or resume) the publication group; returns ``(review, replayed, intent)``.

    Raises:
        PublicationConflictError: key bound to a different payload.
        PublicationInProgressError: another unpublished publication is active.
        PublicationRecoveryError: a step failed; the persisted intent records
            the completed stages so the caller can retry the same request.
    """
    import uuid

    store = intent_store if intent_store is not None else FileIntentStore(task_root)
    service = SelectionPublicationService(store)
    owner_id = uuid.uuid4().hex
    decision = service.begin(
        key=key,
        digest=digest,
        expected_revision=expected_revision,
        current_revision=current_revision,
        owner_id=owner_id,
    )
    if decision.action == "replay":
        return None, True, decision.intent
    completed: list[str] = (
        list(decision.intent.get("completed_stages") or [])
        if decision.action == "recovering"
        else []
    )
    stage_names = tuple(name for name, _ in stages)
    target_revision = current_revision + 1
    value: Any = None
    for name, step in stages:
        if name in completed:
            continue
        try:
            value = step()
        except Exception as exc:
            intent = service.record(
                job_id=job_id,
                key=key,
                digest=digest,
                expected_revision=expected_revision,
                target_revision=target_revision,
                stages=stage_names,
                completed=tuple(completed),
                status="failed",
                owner_id=owner_id,
                error=str(exc),
            )
            raise PublicationRecoveryError(
                f"publication failed at stage {name!r}: {exc}",
                intent=intent,
                completed=completed,
            ) from exc
        completed.append(name)
        service.record(
            job_id=job_id,
            key=key,
            digest=digest,
            expected_revision=expected_revision,
            target_revision=target_revision,
            stages=stage_names,
            completed=tuple(completed),
            status="pending",
            owner_id=owner_id,
        )
    intent = service.record(
        job_id=job_id,
        key=key,
        digest=digest,
        expected_revision=expected_revision,
        target_revision=target_revision,
        stages=stage_names,
        completed=tuple(completed),
        status="published",
        owner_id=owner_id,
    )
    return value, False, intent


# --------------------------------------------------------------------------- #
# process-level serialization (one publication per task root at a time)
# --------------------------------------------------------------------------- #

LOCK_RELATIVE_PATH = "RESULT/pes_search/publication.lock"


def acquire_publication_lock(task_root: Path | str, *, timeout: float = 30.0) -> Any:
    """Take the exclusive per-task publication lock (fcntl flock, non-blocking).

    Raises PublicationInProgressError when another process holds it past the
    timeout; a waiting request then reports a typed 409 instead of racing.
    """
    import fcntl
    import time as _time

    path = Path(task_root) / LOCK_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    deadline = _time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return handle
        except BlockingIOError:
            if _time.monotonic() > deadline:
                handle.close()
                raise PublicationInProgressError(
                    "another publication is in progress for this task"
                ) from None
            _time.sleep(0.05)


def release_publication_lock(handle: Any) -> None:
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
