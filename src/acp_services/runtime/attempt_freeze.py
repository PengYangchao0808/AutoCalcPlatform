"""Services-native frozen-attempt producer + persistent execution claim (07-A / todo 24).

Two primitives live here, both consumed by the Wave-4 default worker chain:

**The group-committed freeze producer.**  :func:`write_frozen_attempt` turns a
frozen :class:`AttemptFreezeSpec` (typed envelope + frozen plan + optional
storage binding — never a second envelope/plan DTO) into the immutable frozen
input group under ``WORK/00_RUNTIME/frozen/``.  The group is published with
ONE atomic rename: every member is staged under
``WORK/00_RUNTIME/.staging/<attempt>/``, fsynced, recorded in a
``frozen_group.json`` manifest (per-member sha256 + full freeze identity), and
the staging directory is renamed into place.  ``frozen_group.json`` is the
sole commit marker — readers
(:func:`acp_services.runtime.execution_chain.load_frozen_group` /
``frozen_artifacts_present`` / the worker) accept only a complete,
hash-verified manifest and never adopt a partial or mixed group.  A writer
killed between group files leaves nothing but a stale staging directory.

**The persistent attempt execution claim.**
:class:`AttemptLockExecutionRight` implements the D02
:class:`~acp_services.runtime.execution_right.ExecutionRightPort` with an
``attempt.lock`` owner-token + lease file: the worker must atomically acquire
(O_CREAT|O_EXCL) the exclusive execution right BEFORE invoking the executor —
controller authorization to dispatch is not the same fact as holding the
claim.  Only the holder may write the authoritative
``execution_receipt.json``/``task_result.json``; a competition loser,
duplicate delivery, or a worker that lost the right gets a
``WorkerRefusal`` and writes only the separate ``<receipt>.refused.json``
diagnostic — never a same-path failed receipt and never a job failure CAS.
A crashed or unknown claim is never stolen blindly: the local holder must be
verifiably dead AND an injected recovery authority must confirm that its
execution children have stopped.  Parent pid death alone leaves possible
orphaned QC processes, so the default is a non-authoritative refusal pending
checked recovery.  Unknown/foreign hosts are never reclaimed locally.

Import direction: stdlib + the dependency-free Services contracts/runtime
modules only (never ``acp``); ``worker_bootstrap`` imports this module, never
the reverse.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import socket
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Protocol

from acp_services.contracts.envelope import (
    ExecutionEnvelope,
    StorageBinding,
    expected_submission_id,
)
from acp_services.runtime.execution_chain import (
    BINDING_FILENAME,
    ENVELOPE_FILENAME,
    FROZEN_GROUP_MANIFEST_FILENAME,
    FROZEN_GROUP_SCHEMA,
    PLAN_FILENAME,
    RUNTIME_PROTOCOL_DIR,
    FrozenGroup,
    FrozenGroupError,
    FrozenGroupManifest,
    frozen_group_dir,
    load_frozen_group,
)
from acp_services.runtime.execution_right import (
    ExecutionRightState,
    ExecutionRightVerdict,
)
from acp_services.runtime.worker import FrozenPlan, frozen_plan_body_digest, frozen_plan_to_dict

__all__ = [
    "ATTEMPT_LOCK_FILENAME",
    "ATTEMPT_LOCK_GUARD_FILENAME",
    "CLAIM_SCHEMA",
    "DEFAULT_LEASE_SECONDS",
    "STAGING_DIRNAME",
    "AttemptFreezeError",
    "AttemptFreezeSpec",
    "AttemptLockClaim",
    "AttemptLockExecutionRight",
    "AttemptRecoveryProof",
    "CheckedAttemptRecoveryPort",
    "AttemptExecutionScope",
    "attempt_lock_path",
    "read_attempt_lock",
    "write_frozen_attempt",
]


class AttemptFreezeError(Exception):
    """The frozen-attempt group cannot be produced or must not be replaced."""


#: Staging root (inside the runtime dir) for single-writer group publication.
STAGING_DIRNAME: Final = ".staging"

#: Persistent execution-claim file (NOT a member of the frozen input group).
ATTEMPT_LOCK_FILENAME: Final = "attempt.lock"

#: Recovery-mutex file (``flock``) serializing dead-claim replacement.  Every
#: taker/recoverer follows the same protocol: the initial ``O_CREAT|O_EXCL``
#: acquire stays lock-free; anything that must observe-and-replace an existing
#: claim (dead-holder recovery) holds this guard so two recoverers can never
#: unlink each other's fresh claims (R03 — one attempt, one holder).
ATTEMPT_LOCK_GUARD_FILENAME: Final = "attempt.lock.guard"

#: Wire schema of the attempt execution claim document.
CLAIM_SCHEMA: Final = "attempt_execution_claim"

#: Default claim lease, in seconds (wall-clock UTC deadline inside the claim).
DEFAULT_LEASE_SECONDS: Final = 3600.0


# ── the frozen spec ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AttemptFreezeSpec:
    """Everything one attempt's frozen input group is built from.

    Carries the existing typed contracts verbatim — the frozen
    :class:`~acp_services.contracts.envelope.ExecutionEnvelope` (job/attempt/
    submission/release identity, owner/fencing, protocol, entrypoint,
    capabilities, scientific closure), the frozen
    :class:`~acp_services.runtime.worker.FrozenPlan` (payload/steps/item
    symbols + the declared two-layer plan identity) and the optional
    :class:`~acp_services.contracts.envelope.StorageBinding`.  ``workflow`` is
    the dispatcher's workflow id recorded in the group manifest.
    """

    workflow: str
    envelope: ExecutionEnvelope
    plan: FrozenPlan
    binding: StorageBinding | None = None


def _json_bytes(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_staged_member(path: Path, blob: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())


def _sha256_hex(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _validated_manifest(spec: AttemptFreezeSpec) -> FrozenGroupManifest:
    """Validate the freeze identity and return the manifest it would commit.

    Checks (each a typed refusal, never a silent fix-up):

    * ``workflow`` and the release/owner/fencing identity are non-empty;
    * ``submission_id`` is exactly the deterministic
      :func:`expected_submission_id` derivation of ``(job_id, attempt)``;
    * the plan body digest is RECOMPUTED here and must equal both the plan's
      declared ``plan_digest`` and the envelope's frozen ``plan_digest`` (plus
      plan/step identity agreement);
    * a supplied binding must be declared by the envelope's ``data_bindings``,
      and a binding IS required whenever the envelope declares any.
    """
    envelope = spec.envelope
    if not spec.workflow:
        raise AttemptFreezeError("AttemptFreezeSpec.workflow must not be empty")
    for field_name in ("release_id", "owner", "fencing_token"):
        if not getattr(envelope, field_name):
            raise AttemptFreezeError(f"envelope {field_name!r} must not be empty to freeze")
    derived = expected_submission_id(envelope.job_id, envelope.attempt)
    if envelope.submission_id != derived:
        raise AttemptFreezeError(
            f"submission_id {envelope.submission_id!r} != expected derivation {derived!r} "
            f"for ({envelope.job_id!r}, attempt {envelope.attempt})"
        )
    identity = spec.plan.identity
    if identity is None:
        raise AttemptFreezeError(
            "frozen plan carries no declared identity; build it with freeze_frozen_plan"
        )
    recomputed = frozen_plan_body_digest(spec.plan)
    if identity.plan_digest != recomputed:
        raise AttemptFreezeError(
            f"plan body digest {recomputed!r} != declared plan_digest {identity.plan_digest!r} "
            "(tampered plan body)"
        )
    if envelope.plan_digest != recomputed:
        raise AttemptFreezeError(
            f"envelope plan_digest {envelope.plan_digest!r} != recomputed body "
            f"digest {recomputed!r}"
        )
    if envelope.plan_identity != identity.plan_identity:
        raise AttemptFreezeError("envelope plan_identity != frozen plan identity")
    if tuple(envelope.step_identities) != tuple(identity.step_identities):
        raise AttemptFreezeError("envelope step_identities != frozen plan step identities")

    declared_ids = {binding.binding_id for binding in envelope.data_bindings}
    if spec.binding is not None:
        if spec.binding.binding_id not in declared_ids:
            raise AttemptFreezeError(
                f"storage binding {spec.binding.binding_id!r} is not declared by the envelope "
                f"(declared: {sorted(declared_ids)})"
            )
    elif declared_ids:
        raise AttemptFreezeError(
            "the envelope declares data_bindings, so binding.json is a required group member "
            f"(declared: {sorted(declared_ids)})"
        )

    members = {
        ENVELOPE_FILENAME: _sha256_hex(_json_bytes(envelope.to_dict())),
        PLAN_FILENAME: _sha256_hex(_json_bytes(frozen_plan_to_dict(spec.plan))),
    }
    if spec.binding is not None:
        members[BINDING_FILENAME] = _sha256_hex(_json_bytes(spec.binding.to_dict()))
    return FrozenGroupManifest(
        schema=FROZEN_GROUP_SCHEMA,
        job_id=envelope.job_id,
        attempt=envelope.attempt,
        workflow=spec.workflow,
        submission_id=envelope.submission_id,
        release_id=envelope.release_id,
        release_digest=envelope.release_digest,
        owner=envelope.owner,
        fencing_token=envelope.fencing_token,
        plan_digest=recomputed,
        plan_identity=identity.plan_identity,
        member_digests=members,
    )


def _existing_group_or_refuse(work_dir: Path, candidate: FrozenGroupManifest) -> FrozenGroup:
    """Reuse a matching published group verbatim; refuse anything else."""
    try:
        group = load_frozen_group(work_dir)
    except FrozenGroupError as exc:
        raise AttemptFreezeError(
            f"an existing frozen group is present but invalid; refusing to delete or replace "
            f"it (checked recovery required): {exc}"
        ) from exc
    if group.manifest.identity_key != candidate.identity_key:
        raise AttemptFreezeError(
            "a frozen group for a different attempt/identity is already published; refusing to "
            "delete or replace it (archive the previous attempt first per the same-attempt "
            f"protocol): existing attempt {group.manifest.attempt} != spec attempt "
            f"{candidate.attempt}"
        )
    return group


def write_frozen_attempt(work_dir: Path | str, spec: AttemptFreezeSpec) -> FrozenGroup:
    """Freeze one attempt's input group; idempotent for the same spec.

    Publication is a single-writer staging group + one atomic rename whose
    commit marker IS ``frozen_group.json``: members are staged (fsynced) under
    ``WORK/00_RUNTIME/.staging/<attempt>/``, the manifest is written last into
    the staging directory, and the directory is renamed onto
    ``WORK/00_RUNTIME/frozen/``.  Re-running the SAME spec returns the existing
    complete group without rewriting it; a different attempt/identity — or an
    existing invalid group — is a typed refusal (never a delete/replace).
    """
    candidate = _validated_manifest(spec)
    work = Path(work_dir)
    runtime = work.joinpath(*RUNTIME_PROTOCOL_DIR)
    group_dir = frozen_group_dir(work)
    if group_dir.exists():
        return _existing_group_or_refuse(work, candidate)

    staging = runtime / STAGING_DIRNAME / str(spec.envelope.attempt)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    members: dict[str, bytes] = {
        ENVELOPE_FILENAME: _json_bytes(spec.envelope.to_dict()),
        PLAN_FILENAME: _json_bytes(frozen_plan_to_dict(spec.plan)),
    }
    if spec.binding is not None:
        members[BINDING_FILENAME] = _json_bytes(spec.binding.to_dict())
    manifest_blob = _json_bytes(candidate.to_dict())
    for name, blob in members.items():
        _write_staged_member(staging / name, blob)
    _write_staged_member(staging / FROZEN_GROUP_MANIFEST_FILENAME, manifest_blob)
    _fsync_dir(staging)
    try:
        os.rename(staging, group_dir)
    except OSError as exc:
        if exc.errno in (errno.EEXIST, errno.ENOTEMPTY):
            # A concurrent publisher won the rename: fall back to the
            # idempotent/refuse path against what is now on disk.
            return _existing_group_or_refuse(work, candidate)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _fsync_dir(runtime)
    return load_frozen_group(work)


# ── persistent attempt execution claim (attempt.lock) ───────────────────────


def attempt_lock_path(work_dir: Path | str) -> Path:
    """Canonical ``WORK/00_RUNTIME/attempt.lock`` path for a task work dir."""
    return Path(work_dir).joinpath(*RUNTIME_PROTOCOL_DIR, ATTEMPT_LOCK_FILENAME)


def _proc_start_time(pid: int) -> str:
    """/proc start-time (field 22) of *pid* — the pid-recycling guard ("" if unknown)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    tail = stat.rpartition(")")[2].split()
    if len(tail) < 20:
        return ""
    return tail[19]


def _holder_alive(claim: AttemptLockClaim) -> bool | None:
    """Liveness of the recorded holder: True/False, None when unverifiable.

    A claim recorded on another host is never provably dead from this node
    (the pid is only meaningful locally — shared LSF work dirs), so cross-host
    claims are unverifiable and therefore never stolen.
    """
    if claim.pid <= 0:
        return None
    if not claim.host or claim.host != socket.gethostname():
        return None
    try:
        os.kill(claim.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    if claim.pid_starttime:
        current = _proc_start_time(claim.pid)
        if current and current != claim.pid_starttime:
            return False
    return True


@dataclass(frozen=True)
class AttemptLockClaim:
    """Wire form of the attempt execution claim (owner token + lease)."""

    schema: str = CLAIM_SCHEMA
    job_id: str = ""
    attempt: int = 0
    owner: str = ""
    fencing_token: str = ""
    pid: int = 0
    pid_starttime: str = ""
    host: str = ""
    acquired_at: str = ""
    lease_expires_at: str = ""
    execution_cgroup_path: str = ""
    execution_cgroup_inode: int = 0
    execution_cgroup_device: int = 0
    execution_boot_id: str = ""

    def held_by_current_process(self, envelope: ExecutionEnvelope) -> bool:
        """True only when this claim IS the current process holding *envelope*."""
        if (
            self.schema != CLAIM_SCHEMA
            or self.job_id != envelope.job_id
            or self.attempt != envelope.attempt
            or self.owner != envelope.owner
            or self.fencing_token != envelope.fencing_token
            or self.pid != os.getpid()
        ):
            return False
        if not self.host or self.host != socket.gethostname():
            return False
        if not self.pid_starttime:
            return True
        return self.pid_starttime == _proc_start_time(os.getpid())

    def lease_state(self) -> str:
        """``valid``/``expired``/``unknown`` for diagnostics (never a steal proof)."""
        if not self.lease_expires_at:
            return "unknown"
        try:
            deadline = datetime.fromisoformat(self.lease_expires_at)
        except ValueError:
            return "unknown"
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return "valid" if deadline > datetime.now(timezone.utc) else "expired"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "pid": self.pid,
            "pid_starttime": self.pid_starttime,
            "host": self.host,
            "acquired_at": self.acquired_at,
            "lease_expires_at": self.lease_expires_at,
            "execution_cgroup_path": self.execution_cgroup_path,
            "execution_cgroup_inode": self.execution_cgroup_inode,
            "execution_cgroup_device": self.execution_cgroup_device,
            "execution_boot_id": self.execution_boot_id,
        }

    def content_digest(self) -> str:
        """Bind a recovery decision to every field of this exact claim."""
        return _sha256_hex(_json_bytes(self.to_dict()))

    @classmethod
    def for_envelope(cls, envelope: ExecutionEnvelope, *, lease_seconds: float) -> AttemptLockClaim:
        now = datetime.now(timezone.utc)
        deadline = now.timestamp() + max(lease_seconds, 0.0)
        return cls(
            schema=CLAIM_SCHEMA,
            job_id=envelope.job_id,
            attempt=envelope.attempt,
            owner=envelope.owner,
            fencing_token=envelope.fencing_token,
            pid=os.getpid(),
            pid_starttime=_proc_start_time(os.getpid()),
            host=socket.gethostname(),
            acquired_at=now.isoformat(),
            lease_expires_at=datetime.fromtimestamp(deadline, timezone.utc).isoformat(),
        )

    @classmethod
    def from_dict(cls, payload: Any) -> AttemptLockClaim | None:
        """Tolerant decode: garbage yields ``None`` (never a guessed claim)."""
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                schema=str(payload.get("schema") or ""),
                job_id=str(payload.get("job_id") or ""),
                attempt=int(payload.get("attempt") or 0),
                owner=str(payload.get("owner") or ""),
                fencing_token=str(payload.get("fencing_token") or ""),
                pid=int(payload.get("pid") or 0),
                pid_starttime=str(payload.get("pid_starttime") or ""),
                host=str(payload.get("host") or ""),
                acquired_at=str(payload.get("acquired_at") or ""),
                lease_expires_at=str(payload.get("lease_expires_at") or ""),
                execution_cgroup_path=str(payload.get("execution_cgroup_path") or ""),
                execution_cgroup_inode=int(payload.get("execution_cgroup_inode") or 0),
                execution_cgroup_device=int(payload.get("execution_cgroup_device") or 0),
                execution_boot_id=str(payload.get("execution_boot_id") or ""),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class AttemptRecoveryProof:
    """A trusted controller's stopped-execution proof for one exact claim.

    The controller must reconcile the old worker and ALL of its execution
    children before issuing this proof.  A dead parent pid, an expired lease,
    or an empty process-group observation for a non-exclusive group cannot
    prove that fact.  The complete claim digest prevents reusing a proof for
    another holder or for an updated claim.
    """

    claim_digest: str
    holder_stopped: bool
    children_stopped: bool
    source: str

    def confirms(self, claim: AttemptLockClaim) -> bool:
        """Accept only an explicit, complete proof bound to *claim*."""
        return (
            self.claim_digest == claim.content_digest()
            and self.holder_stopped is True
            and self.children_stopped is True
            and isinstance(self.source, str)
            and bool(self.source.strip())
        )


class CheckedAttemptRecoveryPort(Protocol):
    """Trusted process-tree reconciliation, invoked under the recovery guard."""

    def confirm_stopped(self, claim: AttemptLockClaim) -> AttemptRecoveryProof | None:
        """Return a claim-bound proof only after all execution processes stopped."""
        ...


class AttemptExecutionScope(Protocol):
    """Bind the holder and its future children before any scientific execution."""

    def bind(self, claim: AttemptLockClaim, envelope: ExecutionEnvelope) -> AttemptLockClaim:
        """Return the claim annotated with a verified kernel execution boundary."""
        ...


def read_attempt_lock(lock_path: Path | str) -> AttemptLockClaim | None:
    """Read a claim document (``None`` when absent OR unreadable)."""
    try:
        payload = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return AttemptLockClaim.from_dict(payload)


def _acquire_exclusive(path: Path, claim: AttemptLockClaim) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_json_bytes(claim.to_dict()))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


class _RecoveryGuard:
    """``flock``-held mutual exclusion for observe-and-replace claim recovery.

    The guard file is created beside the claim; the fd is ``flock``-ed for the
    duration.  A crashed recoverer releases nothing it took — the kernel drops
    the ``flock`` on process death, so the protocol is crash-safe.
    """

    def __init__(self, lock_path: Path) -> None:
        self._guard_path = lock_path.with_name(ATTEMPT_LOCK_GUARD_FILENAME)
        self._fd: int | None = None

    def __enter__(self) -> _RecoveryGuard:
        self._fd = os.open(self._guard_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


@dataclass(frozen=True)
class AttemptLockExecutionRight:
    """Persistent :class:`ExecutionRightPort` over ``WORK/00_RUNTIME/attempt.lock``.

    Exactly one worker process may hold the exclusive right to execute one
    frozen ``(job, attempt)`` and write its authoritative
    receipt/``task_result``.  The claim is taken atomically (O_CREAT|O_EXCL);
    a loser/duplicate delivery observes ``NOT_HELD`` and writes only the
    ``<receipt>.refused.json`` diagnostic; a holder whose claim was taken over
    observes ``LOST`` at the write seam.  Crashed holders are stolen only
    after the recorded local process is verifiably dead AND a trusted recovery
    port confirms all execution children stopped.  Without such proof the
    default refuses recovery; it never kills a process or a shared group.

    Controller authorization to dispatch is a separate, earlier fact — it is
    not this claim and can never substitute for holding it.
    """

    lock_path: Path
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    recovery_check: CheckedAttemptRecoveryPort | None = None
    execution_scope: AttemptExecutionScope | None = None

    def _held_under_guard(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        """Prepare containment while claim recovery/join operations are excluded."""
        if self.execution_scope is None:
            return ExecutionRightVerdict(state=ExecutionRightState.HELD)
        claim = read_attempt_lock(self.lock_path)
        if claim is None or not claim.held_by_current_process(envelope):
            return ExecutionRightVerdict(state=ExecutionRightState.LOST)
        try:
            bound = self.execution_scope.bind(claim, envelope)
            if not bound.held_by_current_process(envelope):
                raise ValueError("execution scope changed the claim owner")
            fd, temporary = tempfile.mkstemp(dir=self.lock_path.parent, prefix=".attempt-claim-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(_json_bytes(bound.to_dict()))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.lock_path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        except (OSError, RuntimeError, ValueError) as exc:
            return ExecutionRightVerdict(
                state=ExecutionRightState.NOT_HELD,
                detail=f"execution containment unavailable; refusing to calculate: {exc}",
            )
        return ExecutionRightVerdict(state=ExecutionRightState.HELD)

    def acquire(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        claim = AttemptLockClaim.for_envelope(envelope, lease_seconds=self.lease_seconds)
        if _acquire_exclusive(self.lock_path, claim):
            with _RecoveryGuard(self.lock_path):
                return self._held_under_guard(envelope)
        # Observe-and-replace recovery is serialized through the guard mutex:
        # a second recoverer re-reads the claim under the guard and sees the
        # first recoverer's live pid, so it can never unlink a fresh claim
        # (R03 — the pre-guard read/unlink race produced two HELD holders).
        with _RecoveryGuard(self.lock_path):
            existing = read_attempt_lock(self.lock_path)
            if existing is None:
                return ExecutionRightVerdict(
                    state=ExecutionRightState.NOT_HELD,
                    detail=(
                        f"attempt.lock at {self.lock_path} is unreadable; refusing to steal an "
                        "unknown claim (checked recovery required)"
                    ),
                )
            if existing.held_by_current_process(envelope):
                return self._held_under_guard(envelope)
            if _holder_alive(existing) is False:
                proof: AttemptRecoveryProof | None = None
                if self.recovery_check is not None:
                    try:
                        proof = self.recovery_check.confirm_stopped(existing)
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                        # A failed reconciliation is not a stopped execution.
                        proof = None
                if not isinstance(proof, AttemptRecoveryProof) or not proof.confirms(existing):
                    return ExecutionRightVerdict(
                        state=ExecutionRightState.NOT_HELD,
                        detail=(
                            f"holder {existing.owner!r} (pid {existing.pid}) ended, but its "
                            "execution children are not confirmed stopped; checked recovery "
                            "required before replacing attempt.lock"
                        ),
                    )
                current = read_attempt_lock(self.lock_path)
                if current is None or current.content_digest() != existing.content_digest():
                    return ExecutionRightVerdict(
                        state=ExecutionRightState.NOT_HELD,
                        detail="attempt.lock changed during checked recovery; refusing stale proof",
                    )
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
                if _acquire_exclusive(self.lock_path, claim):
                    return self._held_under_guard(envelope)
                return ExecutionRightVerdict(
                    state=ExecutionRightState.NOT_HELD,
                    detail=f"verifiably-dead holder {existing.owner!r} (pid {existing.pid}) was "
                    "recovered by another worker first",
                )
            return ExecutionRightVerdict(
                state=ExecutionRightState.NOT_HELD,
                detail=(
                    f"attempt {envelope.attempt} of {envelope.job_id!r} is claimed by "
                    f"{existing.owner!r} (pid {existing.pid}, host {existing.host or '?'}, "
                    f"lease {existing.lease_state()}); this worker waits/refuses"
                ),
            )

    def verify(self, envelope: ExecutionEnvelope) -> ExecutionRightVerdict:
        existing = read_attempt_lock(self.lock_path)
        if existing is None:
            return ExecutionRightVerdict(
                state=ExecutionRightState.LOST,
                detail=f"attempt.lock at {self.lock_path} is missing or unreadable",
            )
        if existing.held_by_current_process(envelope):
            return ExecutionRightVerdict(state=ExecutionRightState.HELD)
        return ExecutionRightVerdict(
            state=ExecutionRightState.LOST,
            detail=(
                f"execution right for attempt {envelope.attempt} of {envelope.job_id!r} is "
                f"held by {existing.owner!r} (pid {existing.pid}); this worker must not write "
                "an authoritative receipt/task_result"
            ),
        )

    def release(self, envelope: ExecutionEnvelope) -> bool:
        """Remove the claim, but only when it is still this process's own."""
        existing = read_attempt_lock(self.lock_path)
        if existing is None or not existing.held_by_current_process(envelope):
            return False
        try:
            self.lock_path.unlink()
        except OSError:
            return False
        return True
