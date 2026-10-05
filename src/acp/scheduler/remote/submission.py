"""Submission identity + submit-right lease for the D02 protocol.

Pure-stdlib helpers shared by the manager (intent/lease writer) and the
remote runner (``reconcile_submission`` reader).  Importing this module
never requires the ``paramiko`` extra.

Contract A (plan acp-execution-integrity-remediation):

* ``submission_id = "sub_" + sha256(f"{job_id}:{attempt}")[:16]`` is
  persisted **before** ``bsub`` and names the LSF job (``-J
  acp_<submission_id>``).
* ``submit_state="intent"`` carries ``submit_owner`` (owner token =
  pid + process start time + thread ident + attempt) and
  ``lease_expires_at`` (wall-clock UTC — never ``time.monotonic()``,
  which is per-process and meaningless across restarts).
* While the lease is valid (submit worker registered, heartbeat-fresh
  and not explicitly released, or — cross-process/restart — before
  ``lease_expires_at`` + clock-drift tolerance) reconcile must never
  judge the submission ``not_accepted``.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

__all__ = [
    "LEASE_DRIFT_TOLERANCE_SECONDS",
    "SUBMIT_WORKERS",
    "SubmitWorker",
    "build_owner_token",
    "heartbeat_submit_worker",
    "lease_deadline_iso",
    "lease_ttl_seconds",
    "register_submit_worker",
    "release_submit_worker",
    "submission_id_for",
    "submission_lsf_name",
    "submit_lease_valid",
]

# Extra seconds added on top of the derived TTL so a lease never expires
# mid-``bsub`` even when the SSH layer retries once at full timeout.
_LEASE_MARGIN_SECONDS = 60
# Wall-clock tolerance when evaluating another process's lease (NTP drift,
# slow DB reads after a restart).
LEASE_DRIFT_TOLERANCE_SECONDS = 30


def submission_id_for(job_id: str, attempt: int | None) -> str:
    """Contract-A submission id: ``sub_`` + sha256(job_id:attempt)[:16]."""
    material = f"{job_id}:{int(attempt or 1)}"
    return "sub_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def submission_lsf_name(submission_id: str) -> str:
    """LSF job name used by ``#BSUB -J`` for *submission_id*."""
    return f"acp_{submission_id}"


def lease_ttl_seconds(config: object | None) -> int:
    """TTL derived from real config (``config.py``: connect/read + bsub).

    ``ttl >= max(connect_timeout, read_timeout, submission_timeout) +
    margin``.  Falls back to the documented defaults (10/30/60) when no
    config object is available, so the TTL can never be smaller than the
    longest blocking submit operation.
    """
    connect = int(getattr(config, "connect_timeout", 10) or 10)
    read = int(getattr(config, "read_timeout", 30) or 30)
    submit = int(getattr(config, "submission_timeout", 60) or 60)
    return max(connect, read, submit) + _LEASE_MARGIN_SECONDS


def lease_deadline_iso(ttl_seconds: int) -> str:
    """Wall-clock UTC deadline (ISO-8601) for a lease of *ttl_seconds*."""
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _parse_iso(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


@dataclass
class SubmitWorker:
    """In-process submit-worker registry entry (owner liveness evidence)."""

    worker_id: str
    submission_id: str
    registered_at: str
    heartbeat_at: str
    released: bool = False

    def touch(self) -> None:
        self.heartbeat_at = datetime.now(timezone.utc).isoformat()


# submission_id -> registry entry.  Guarded by its own lock; consulted by
# the reconcile path of the SAME process that owns the submit worker.
SUBMIT_WORKERS: dict[str, SubmitWorker] = {}
_WORKERS_LOCK = threading.Lock()


def register_submit_worker(submission_id: str, owner: str, ttl_seconds: int) -> SubmitWorker:
    """Register the submit worker for *submission_id* (fresh heartbeat)."""
    now = datetime.now(timezone.utc).isoformat()
    worker = SubmitWorker(
        worker_id=owner,
        submission_id=submission_id,
        registered_at=now,
        heartbeat_at=now,
    )
    with _WORKERS_LOCK:
        SUBMIT_WORKERS[submission_id] = worker
    return worker


def heartbeat_submit_worker(submission_id: str) -> None:
    """Refresh the wall-clock heartbeat while ``bsub`` blocks."""
    with _WORKERS_LOCK:
        worker = SUBMIT_WORKERS.get(submission_id)
        if worker is not None:
            worker.touch()


def release_submit_worker(submission_id: str) -> None:
    """Explicitly release the submit right (outcome already persisted)."""
    with _WORKERS_LOCK:
        worker = SUBMIT_WORKERS.get(submission_id)
        if worker is not None:
            worker.released = True
            worker.touch()


def _worker_active_in_process(submission_id: str, ttl_seconds: int) -> bool | None:
    """Registry verdict for this process: True/False, or None if unregistered."""
    with _WORKERS_LOCK:
        worker = SUBMIT_WORKERS.get(submission_id)
    if worker is None:
        return None
    if worker.released:
        return False
    heartbeat = _parse_iso(worker.heartbeat_at)
    if heartbeat is None:
        return False
    # Stale heartbeat = the submit thread died without releasing.
    return (time.time() - heartbeat) <= (ttl_seconds * 1.5)


def submit_lease_valid(remote_meta: object, *, ttl_seconds: int | None = None) -> bool:
    """True while the submit right for this intent is still held.

    In-process first (worker registry: registered + heartbeat-fresh +
    not released), then the persisted wall-clock ``lease_expires_at``
    with drift tolerance for cross-process / post-restart evaluation.
    A missing/undecodable lease on an ``intent`` record is treated as
    valid (never judge an intent terminal on absent evidence).
    """
    if not isinstance(remote_meta, dict):
        return False
    submission_id = remote_meta.get("submission_id")
    ttl = int(ttl_seconds if ttl_seconds is not None else 60 + _LEASE_MARGIN_SECONDS)
    if isinstance(submission_id, str) and submission_id:
        verdict = _worker_active_in_process(submission_id, ttl)
        if verdict is not None:
            return verdict
        # Fall through to wall-clock when this process has no registry
        # entry (worker from another process, or after a restart).
    expires = _parse_iso(remote_meta.get("lease_expires_at"))
    if expires is None:
        # No decodable deadline: only an explicit release may have
        # happened; without one, keep the lease conservatively valid.
        return True
    return time.time() <= expires + LEASE_DRIFT_TOLERANCE_SECONDS


def build_owner_token(attempt: int | None) -> str:
    """Owner token = pid + process start time + thread ident + attempt."""
    try:
        proc_start = os.stat(f"/proc/{os.getpid()}").st_mtime_ns
    except OSError:  # pragma: no cover - non-Linux fallback
        proc_start = 0
    return f"{os.getpid()}:{proc_start}:{threading.get_ident()}:{int(attempt or 1)}"
