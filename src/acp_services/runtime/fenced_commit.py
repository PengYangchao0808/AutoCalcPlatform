"""M2-04c — ``FencedCommitPort``: the only authoritative step/checkpoint commit.

Before M2-04 the only execution-right object was the *check*
:class:`acp_services.runtime.execution_right.ExecutionRightPort` (``acquire`` /
``verify``) with an ``AlwaysHeldExecutionRight`` transitional default, and every
writer wrote its own file with no fence at the write point.  A worker that lost
the right between its last check and the write could still publish an
authoritative checkpoint, step result or publication record.

This module adds the missing **fenced commit**:

1. the caller stages the candidate bytes/payload in a **non-authoritative**
   staging area (``WORK/00_RUNTIME/.commit_staging/<name>/…``);
2. :meth:`FencedCommitPort.commit_if_fenced` re-checks, at the single commit
   point, the fence (``attempt`` + ``owner`` + ``fencing`` against the frozen
   envelope) **and** the current execution right (``verify``), then
3. atomically replaces the authoritative path (``os.replace``) and records a
   commit record.

A fence or right failure raises :class:`FenceLostError` and leaves the
authoritative path untouched — the staged file is *not* promoted.  The fenced
commit is therefore a compare-and-swap between the writer's believed fence and
the platform's current fence, not a "check then write".

``AlwaysHeldExecutionRight`` remains, but only for tests and single-holder
fixtures: :func:`acp_services.runtime.fenced_commit.assert_production_fence`
refuses it on a production seam (guarded by
``tests/test_acp2_runtime_bounded_recipe_dag_c.py``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from acp_services.contracts.envelope import ExecutionEnvelope
from acp_services.runtime.errors import WorkerError
from acp_services.runtime.execution_right import (
    AlwaysHeldExecutionRight,
    ExecutionRightPort,
    ExecutionRightState,
    ExecutionRightVerdict,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COMMIT_RECORD_SCHEMA",
    "COMMIT_STAGING_DIRNAME",
    "AlwaysHeldCommitPort",
    "CommitFence",
    "CommitRecord",
    "FencedCommitError",
    "FencedCommitPort",
    "FileFencedCommit",
    "FenceLostError",
    "StagedWrite",
    "assert_production_fence",
]

#: Non-authoritative staging root, relative to the attempt work dir.
COMMIT_STAGING_DIRNAME: Final = ".commit_staging"

#: Wire schema of the append-only commit record.
COMMIT_RECORD_SCHEMA: Final = "acp2.runtime.fenced-commit.v1"

_RUNTIME_DIR: Final = ("WORK", "00_RUNTIME")


class FencedCommitError(WorkerError):
    """The commit could not be performed (typed, never a silent success)."""


class FenceLostError(FencedCommitError):
    """The writer no longer holds the fence/execution right at the commit point."""

    def __init__(self, reason: str, *, detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(f"fenced commit refused: {reason}")
        self.reason = reason
        self.detail = dict(detail or {})


@dataclass(frozen=True, slots=True)
class CommitFence:
    """The fence a writer believes it holds for this commit."""

    attempt: int
    owner: str
    fencing: str

    @classmethod
    def from_envelope(cls, envelope: ExecutionEnvelope) -> CommitFence:
        return cls(
            attempt=int(envelope.attempt),
            owner=str(envelope.owner or ""),
            fencing=str(envelope.fencing_token or ""),
        )

    def mismatch(self, envelope: ExecutionEnvelope) -> str:
        """Return a stable reason when this fence is not the envelope's fence."""
        if int(envelope.attempt) != self.attempt:
            return f"attempt {self.attempt} != current {envelope.attempt}"
        if str(envelope.owner or "") != self.owner:
            return f"owner {self.owner!r} != current {envelope.owner!r}"
        if str(envelope.fencing_token or "") != self.fencing:
            return f"fencing {self.fencing!r} != current {envelope.fencing_token!r}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {"attempt": self.attempt, "owner": self.owner, "fencing": self.fencing}


@dataclass(frozen=True, slots=True)
class CommitRecord:
    """One committed authoritative write (append-only evidence)."""

    name: str
    relative_path: str
    digest: str
    size_bytes: int
    fence: CommitFence
    sequence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COMMIT_RECORD_SCHEMA,
            "name": self.name,
            "relative_path": self.relative_path,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "fence": self.fence.to_dict(),
            "sequence": self.sequence,
        }


@dataclass(frozen=True, slots=True)
class StagedWrite:
    """A candidate payload staged outside the authoritative tree."""

    name: str
    staging_path: Path
    relative_path: str
    digest: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "staging_path": str(self.staging_path),
            "relative_path": self.relative_path,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
        }


@runtime_checkable
class FencedCommitPort(Protocol):
    """The commit seam every authoritative step/checkpoint write must use."""

    def stage(self, relative_path: str, payload: bytes | Mapping[str, Any] | str) -> StagedWrite:
        """Write the candidate into the non-authoritative staging area."""
        ...

    def commit_if_fenced(self, staged: StagedWrite) -> CommitRecord:
        """Promote *staged* only when the fence and the right still hold."""
        ...

    def discard(self, staged: StagedWrite) -> None:
        """Drop a staged candidate (never touches the authoritative path)."""
        ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _bytes_of(payload: bytes | Mapping[str, Any] | str) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return (json.dumps(dict(payload), sort_keys=True, indent=2) + "\n").encode("utf-8")


@dataclass
class FileFencedCommit:
    """Persistent :class:`FencedCommitPort` over one attempt work directory.

    ``execution_right`` is the *current* right (the persistent claim in
    production); ``envelope`` is the frozen attempt identity the fence is
    checked against.  A commit re-reads both at the commit point — a stale
    "held" observation from the caller is never trusted.
    """

    work_dir: Path
    envelope: ExecutionEnvelope
    execution_right: ExecutionRightPort
    fence: CommitFence | None = None
    _sequence: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.work_dir = Path(self.work_dir)
        if self.fence is None:
            self.fence = CommitFence.from_envelope(self.envelope)

    # -- paths -----------------------------------------------------------

    @property
    def staging_root(self) -> Path:
        """Per-writer staging root (``<attempt>/<owner>-<fencing>``).

        Two workers staging the same relative path must never clobber each
        other's candidate: the staging area is namespaced by the fence, so a
        losing worker's staged bytes cannot invalidate the winner's digest.
        """
        fence = self.fence or CommitFence.from_envelope(self.envelope)
        writer = f"{fence.owner or 'unknown'}-{fence.fencing or 'nofence'}"
        return (
            self.work_dir.joinpath(*_RUNTIME_DIR)
            / COMMIT_STAGING_DIRNAME
            / str(self.envelope.attempt)
            / writer
        )

    @property
    def record_path(self) -> Path:
        return (
            self.work_dir.joinpath(*_RUNTIME_DIR)
            / "commits"
            / f"commit_attempt_{self.envelope.attempt}.jsonl"
        )

    # -- staging ---------------------------------------------------------

    def stage(self, relative_path: str, payload: bytes | Mapping[str, Any] | str) -> StagedWrite:
        """Write the candidate under the non-authoritative staging root."""
        target = Path(relative_path)
        if target.is_absolute() or ".." in target.parts:
            raise FencedCommitError(f"commit target {relative_path!r} must be work-dir relative")
        blob = _bytes_of(payload)
        name = target.name
        staging = self.staging_root / target
        staging.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(staging.parent), prefix=f".{name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, staging)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return StagedWrite(
            name=name,
            staging_path=staging,
            relative_path=str(target),
            digest=_sha256_file(staging),
            size_bytes=len(blob),
        )

    def discard(self, staged: StagedWrite) -> None:
        """Remove the staged candidate (authoritative path is never touched)."""
        staged.staging_path.unlink(missing_ok=True)

    # -- commit ----------------------------------------------------------

    def _check_fence(self) -> None:
        fence = self.fence or CommitFence.from_envelope(self.envelope)
        mismatch = fence.mismatch(self.envelope)
        if mismatch:
            raise FenceLostError(mismatch, detail={"fence": fence.to_dict()})
        verdict = self.execution_right.verify(self.envelope)
        if not verdict.held:
            raise FenceLostError(
                f"execution right {verdict.state.value}: {verdict.detail}".strip(),
                detail={"verdict": verdict.to_dict()},
            )

    def commit_if_fenced(self, staged: StagedWrite) -> CommitRecord:
        """Fence + right + atomic replace, in that order, at one commit point."""
        if not staged.staging_path.is_file():
            raise FencedCommitError(f"staged candidate {staged.staging_path} is missing")
        actual = _sha256_file(staged.staging_path)
        if actual != staged.digest:
            raise FencedCommitError(
                f"staged candidate {staged.name} changed after staging "
                f"({actual} != {staged.digest})"
            )
        self._check_fence()
        authoritative = self.work_dir / staged.relative_path
        authoritative.parent.mkdir(parents=True, exist_ok=True)
        # Re-verify immediately before the rename: the window between the check
        # and the authoritative update is the race this port exists to close.
        self._check_fence()
        os.replace(staged.staging_path, authoritative)
        self._sequence += 1
        record = CommitRecord(
            name=staged.name,
            relative_path=staged.relative_path,
            digest=staged.digest,
            size_bytes=staged.size_bytes,
            fence=self.fence or CommitFence.from_envelope(self.envelope),
            sequence=self._sequence,
        )
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.record_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record


@dataclass(frozen=True)
class AlwaysHeldCommitPort:
    """Test/fixture commit port (no fence, no right, no staging contract).

    Deliberately **not** a production port: :func:`assert_production_fence`
    refuses it, and the worker composition root always injects
    :class:`FileFencedCommit`.
    """

    work_dir: Path

    def stage(self, relative_path: str, payload: bytes | Mapping[str, Any] | str) -> StagedWrite:
        target = Path(self.work_dir) / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = _bytes_of(payload)
        target.write_bytes(blob)
        return StagedWrite(
            name=target.name,
            staging_path=target,
            relative_path=relative_path,
            digest=_sha256_file(target),
            size_bytes=len(blob),
        )

    def discard(self, staged: StagedWrite) -> None:  # pragma: no cover - fixture
        staged.staging_path.unlink(missing_ok=True)

    def commit_if_fenced(self, staged: StagedWrite) -> CommitRecord:
        return CommitRecord(
            name=staged.name,
            relative_path=staged.relative_path,
            digest=staged.digest,
            size_bytes=staged.size_bytes,
            fence=CommitFence(attempt=1, owner="fixture", fencing="fixture"),
        )


def assert_production_fence(port: Any, *, seam: str) -> None:
    """Refuse a non-fenced commit port / always-held right on a production seam."""
    if isinstance(port, AlwaysHeldCommitPort) or isinstance(port, AlwaysHeldExecutionRight):
        raise FencedCommitError(
            f"{seam}: {type(port).__name__} is a test fixture and must never back a "
            "production commit (inject FileFencedCommit with the persistent claim)"
        )
    if isinstance(port, ExecutionRightVerdict) and port.state is not ExecutionRightState.HELD:
        raise FenceLostError(port.detail or port.state.value)
