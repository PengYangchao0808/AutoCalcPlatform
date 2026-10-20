"""Single execution-chain dispatcher + frozen workflow/release capability table.

B06-3 (plan todo 32). The local subprocess entry (``acp.scheduler.runner``) and
the remote LSF entry (``acp.scheduler.remote.script_gen``) must not each decide
*how* a job runs: exactly one dispatcher consults the frozen
workflow/release capability table, freezes the chosen execution protocol on the
attempt, and returns the entry command. This module **is** that dispatcher and
the table.

Decisions frozen here
---------------------

* **One dispatcher, one entry per attempt.** :func:`resolve_attempt_choice`
  reads the attempt's recorded choice and reuses it verbatim when the attempt
  number is unchanged (resume / reconcile never re-select the entry); a *new*
  attempt re-selects. A recorded choice is the single authority for which entry
  a given attempt ran, so the same attempt can never dispatch twice.
* **Frozen capability table.** :data:`FROZEN_WORKFLOW_CAPABILITY_TABLE` is the
  C01 (HN-01) closure snapshot: the eight workflows whose exhaustive
  per-workflow closure was delivered are ``closure="integrated"`` and are the
  *enabled* scope; the remaining seven stay ``closure="legacy"`` and keep the
  legacy path (never claimed independent).
* **Envelope-ready gate.** A workflow whose closure is integrated still only
  takes the envelope entry when a frozen envelope/plan pair is present for the
  attempt (:func:`frozen_artifacts_present`). Until the controller freezes one,
  the dispatcher deterministically selects the legacy entry — the chain is
  switched, but a workflow is never half-switched (the whole closure uses one
  entry, recorded).
* **Single worker argv.** :func:`worker_argv` is the only place the
  ``python -m acp_services.worker_bootstrap`` string entry is built; the remote
  entry (:func:`acp.scheduler.remote.script_gen.build_remote_worker_command`)
  delegates to it.

This module imports no ``acp`` package (AST + string gate in
``tests/test_services_chain_switch.py``); the frozen-group reader lazily
imports the dependency-free ``acp_services.contracts.envelope`` for its
manifest/envelope cross-check only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

__all__ = [
    "ATTEMPT_PROTOCOL_FILENAME",
    "BINDING_FILENAME",
    "CURRENT_SWITCH_SCHEMA",
    "ENVELOPE_FILENAME",
    "EXECUTION_ENTRY_ENVELOPE",
    "EXECUTION_ENTRY_LEGACY",
    "EXECUTION_PROTOCOL_LOCAL",
    "EXECUTION_PROTOCOL_LSF",
    "FROZEN_GROUP_DIRNAME",
    "FROZEN_GROUP_MANIFEST_FILENAME",
    "FROZEN_GROUP_SCHEMA",
    "FROZEN_WORKFLOW_CAPABILITY_TABLE",
    "PLAN_FILENAME",
    "RUNTIME_PROTOCOL_DIR",
    "WORKER_MODULE",
    "AttemptExecutionChoice",
    "AttemptProtocolError",
    "ExecutionLaunch",
    "ExecutionEntrySelection",
    "FrozenAttemptMismatchError",
    "FrozenGroup",
    "FrozenGroupError",
    "FrozenGroupManifest",
    "UnsupportedExecutionWorkflowError",
    "WorkflowExecutionCapability",
    "attempt_choice_from_result",
    "capability_for",
    "frozen_artifacts_present",
    "frozen_group_dir",
    "inspect_frozen_group_dir",
    "load_frozen_group",
    "local_launch",
    "read_attempt_choice",
    "resolve_attempt_choice",
    "select_execution_entry",
    "with_attempt_choice",
    "worker_argv",
    "write_attempt_choice",
]

#: Execution entry vocabulary (frozen on the attempt record).
EXECUTION_ENTRY_ENVELOPE: Final = "envelope"
EXECUTION_ENTRY_LEGACY: Final = "legacy"

#: Execution protocol vocabulary (matches ``envelope.SUPPORTED_EXECUTION_PROTOCOLS``).
EXECUTION_PROTOCOL_LOCAL: Final = "local"
EXECUTION_PROTOCOL_LSF: Final = "lsf"

#: Wire version of the attempt protocol record.
CURRENT_SWITCH_SCHEMA: Final = 1

#: Runtime dir (relative to the task work dir) holding per-attempt runtime files.
RUNTIME_PROTOCOL_DIR: Final = ("WORK", "00_RUNTIME")
ATTEMPT_PROTOCOL_FILENAME: Final = "execution_protocol.json"
ENVELOPE_FILENAME: Final = "envelope.json"
PLAN_FILENAME: Final = "plan.json"
BINDING_FILENAME: Final = "binding.json"

#: Frozen-attempt input group (07-A / todo 24).  The immutable frozen inputs
#: are published as ONE directory under the runtime dir; ``frozen_group.json``
#: is the group's sole commit marker and member hash manifest.  The
#: execution-right record (``attempt.lock``) and the post-run
#: ``execution_receipt.json``/``task_result.json`` are **not** group members.
FROZEN_GROUP_DIRNAME: Final = "frozen"
FROZEN_GROUP_MANIFEST_FILENAME: Final = "frozen_group.json"
FROZEN_GROUP_SCHEMA: Final = 1

#: Services worker module entry (never the legacy CLI).
WORKER_MODULE: Final = "acp_services.worker_bootstrap"

#: Member files a group manifest may list (the manifest itself is implicit).
_GROUP_MEMBER_NAMES: Final[frozenset[str]] = frozenset(
    {ENVELOPE_FILENAME, PLAN_FILENAME, BINDING_FILENAME}
)
_SHA256_HEX: Final = re.compile(r"^[0-9a-f]{64}$")


class UnsupportedExecutionWorkflowError(Exception):
    """No execution capability is registered for the workflow."""

    def __init__(self, workflow: str) -> None:
        super().__init__(f"no execution capability registered for workflow: {workflow!r}")
        self.workflow = workflow


# ── frozen capability table ─────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkflowExecutionCapability:
    """Frozen execution capability of one active workflow.

    ``closure`` is the C01 (HN-01) per-workflow closure verdict — ``integrated``
    when the exhaustive closure was delivered *and* applied by the consumer,
    ``legacy`` otherwise. ``remote_supported`` mirrors the remote allow-list
    (``script_gen._ALLOWED_REMOTE_WORKFLOWS``); a workflow that cannot run on a
    node keeps the legacy local-only assignment even when it is integrated.
    """

    workflow: str
    capabilities: tuple[str, ...]
    closure: str
    remote_supported: bool
    release_required: bool = True


#: C01 (HN-01) delivered-and-applied closure: the eight workflows whose
#: per-workflow closure record is `integrated` (todo 32 applies HN-01).
_C01_INTEGRATED: Final[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
    ("singlepoint", ("singlepoint",), True),
    ("optimize", ("optimize",), True),
    ("frequency", ("frequency",), True),
    ("scan", ("scan",), True),
    ("irc", ("irc",), True),
    ("casscf", ("casscf",), False),
    ("xtb_optimize", ("optimize",), True),
    ("OrcaGradient", ("orca_gradient",), False),
)

#: Not yet closed by C01: keep the legacy path; never claimed independent.
_LEGACY_SCOPE: Final[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
    ("Confsearch", ("conformer_search", "md_sampling", "clustering", "censo_refine"), True),
    ("PESsearch", ("scan", "singlepoint"), True),
    ("BatchOptimize", ("optimize", "frequency", "singlepoint", "thermochemistry"), True),
    ("XtbPathSearch", ("xtb_path_search",), False),
    ("XtbConstrainedOpt", ("xtb_constrained_opt",), False),
    ("tsmode", ("frequency", "optimize"), True),
    ("nmr", ("nmr_shielding", "conformer_search", "censo_refine"), True),
)


def _build_table() -> dict[str, WorkflowExecutionCapability]:
    table: dict[str, WorkflowExecutionCapability] = {}
    for workflow, capabilities, remote_supported in _C01_INTEGRATED:
        table[workflow] = WorkflowExecutionCapability(
            workflow=workflow,
            capabilities=capabilities,
            closure="integrated",
            remote_supported=remote_supported,
        )
    for workflow, capabilities, remote_supported in _LEGACY_SCOPE:
        table[workflow] = WorkflowExecutionCapability(
            workflow=workflow,
            capabilities=capabilities,
            closure="legacy",
            remote_supported=remote_supported,
        )
    return table


#: The frozen workflow/release capability table (single authority).
FROZEN_WORKFLOW_CAPABILITY_TABLE: Final[dict[str, WorkflowExecutionCapability]] = _build_table()

#: The enabled scope (C01 integrated) — recorded for the enablement matrix.
ENABLED_WORKFLOWS: Final[frozenset[str]] = frozenset(
    name for name, cap in FROZEN_WORKFLOW_CAPABILITY_TABLE.items() if cap.closure == "integrated"
)


def capability_for(workflow: str) -> WorkflowExecutionCapability:
    """Return the frozen capability of *workflow* (raises when unknown)."""
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None:
        raise UnsupportedExecutionWorkflowError(workflow)
    return capability


# ── dispatcher ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionEntrySelection:
    """The dispatcher's entry decision for one attempt."""

    entry: str
    protocol: str
    closure: str


def select_execution_entry(
    workflow: str,
    *,
    remote: bool,
    envelope_available: bool,
) -> ExecutionEntrySelection:
    """Select the execution entry for *workflow* using the frozen table.

    Envelope only when the closure is integrated, the target protocol supports
    the workflow, and a frozen envelope/plan pair is available for the attempt;
    otherwise the legacy entry. The remote protocol is ``lsf``, the local one
    ``local``. A workflow outside the active table (retired / synthetic) is
    total-mapped to the legacy entry so existing paths never break.
    """
    protocol = EXECUTION_PROTOCOL_LSF if remote else EXECUTION_PROTOCOL_LOCAL
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None:
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, "legacy")
    if capability.closure != "integrated":
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, capability.closure)
    if remote and not capability.remote_supported:
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, capability.closure)
    if not envelope_available:
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, capability.closure)
    return ExecutionEntrySelection(EXECUTION_ENTRY_ENVELOPE, protocol, capability.closure)


# ── per-attempt protocol record ─────────────────────────────────────────────


class AttemptProtocolError(ValueError):
    """The per-attempt protocol record exists but cannot be decoded safely."""


@dataclass(frozen=True)
class AttemptExecutionChoice:
    """The entry/protocol frozen on one attempt (the unique-dispatch record)."""

    attempt: int
    workflow: str
    entry: str
    protocol: str
    closure: str = ""
    release_id: str = ""
    schema: int = CURRENT_SWITCH_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "attempt": self.attempt,
            "workflow": self.workflow,
            "entry": self.entry,
            "protocol": self.protocol,
            "closure": self.closure,
            "release_id": self.release_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttemptExecutionChoice:
        if not isinstance(payload, Mapping):
            raise ValueError("attempt execution record must be a mapping")
        raw_attempt = payload.get("attempt", 1)
        attempt = int(raw_attempt) if isinstance(raw_attempt, (int, str)) else 1
        return cls(
            attempt=attempt,
            workflow=str(payload.get("workflow") or ""),
            entry=str(payload.get("entry") or EXECUTION_ENTRY_LEGACY),
            protocol=str(payload.get("protocol") or ""),
            closure=str(payload.get("closure") or ""),
            release_id=str(payload.get("release_id") or ""),
            schema=int(payload.get("schema", CURRENT_SWITCH_SCHEMA)),
        )


def resolve_attempt_choice(
    *,
    workflow: str,
    attempt: int,
    remote: bool,
    envelope_available: bool,
    previous: AttemptExecutionChoice | None = None,
    release_id: str = "",
) -> AttemptExecutionChoice:
    """Return the attempt's frozen choice, reusing it for an unchanged attempt.

    Resume/reconcile call with the already-recorded *previous* choice and the
    same attempt number: the choice (entry + protocol + release snapshot) is
    returned verbatim — the entry is never re-selected. A new attempt number
    re-selects from the frozen table.
    """
    if previous is not None and previous.attempt == attempt and previous.workflow == workflow:
        return previous
    selection = select_execution_entry(
        workflow, remote=remote, envelope_available=envelope_available
    )
    return AttemptExecutionChoice(
        attempt=attempt,
        workflow=workflow,
        entry=selection.entry,
        protocol=selection.protocol,
        closure=selection.closure,
        release_id=release_id,
    )


def _protocol_path(work_dir: Path | str) -> Path:
    return Path(work_dir).joinpath(*RUNTIME_PROTOCOL_DIR, ATTEMPT_PROTOCOL_FILENAME)


def read_attempt_choice(work_dir: Path | str) -> AttemptExecutionChoice | None:
    """Read the attempt protocol record from *work_dir* (``None`` when absent).

    A present-but-corrupt record is a typed :class:`AttemptProtocolError`, not
    ``None``: once an attempt's entry was frozen, a damaged record must fail the
    dispatch rather than silently re-selecting (possibly legacy) entry.
    """
    target = _protocol_path(work_dir)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AttemptProtocolError(
            f"attempt protocol record unreadable at {target}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise AttemptProtocolError(f"attempt protocol record at {target} is not a mapping")
    try:
        choice = AttemptExecutionChoice.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise AttemptProtocolError(
            f"attempt protocol record at {target} is invalid: {exc}"
        ) from exc
    if choice.schema != CURRENT_SWITCH_SCHEMA:
        raise AttemptProtocolError(
            f"attempt protocol record at {target} has unsupported schema {choice.schema}"
        )
    if not choice.workflow or not choice.entry or not choice.protocol or choice.attempt < 1:
        raise AttemptProtocolError(f"attempt protocol record at {target} is incomplete")
    return choice


def write_attempt_choice(work_dir: Path | str, choice: AttemptExecutionChoice) -> Path:
    """Atomically write the attempt protocol record into *work_dir*."""
    target = _protocol_path(work_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(choice.to_dict(), sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def attempt_choice_from_result(result: Mapping[str, Any] | None) -> AttemptExecutionChoice | None:
    """Read the choice recorded in a job ``result`` payload (``execution`` key)."""
    if not isinstance(result, Mapping):
        return None
    raw = result.get("execution")
    if not isinstance(raw, Mapping):
        return None
    try:
        return AttemptExecutionChoice.from_dict(raw)
    except (TypeError, ValueError):
        return None


def with_attempt_choice(
    result: Mapping[str, Any] | None, choice: AttemptExecutionChoice
) -> dict[str, Any]:
    """Return a copy of *result* with the attempt choice recorded under ``execution``."""
    updated: dict[str, Any] = dict(result or {})
    updated["execution"] = choice.to_dict()
    return updated


# ── frozen envelope artifacts + worker argv ─────────────────────────────────


class FrozenGroupError(Exception):
    """The frozen-attempt input group is absent, incomplete or tampered."""


class FrozenAttemptMismatchError(FrozenGroupError):
    """A valid frozen group belongs to a different attempt than the current one."""


@dataclass(frozen=True)
class FrozenGroupManifest:
    """Identity + member hash list of one published frozen-attempt group.

    ``member_digests`` maps each member filename (``envelope.json`` /
    ``plan.json`` / optional ``binding.json``) to its sha256 hex digest.  The
    manifest file itself is never a member — its presence inside the group
    directory **is** the commit marker.
    """

    schema: int = FROZEN_GROUP_SCHEMA
    job_id: str = ""
    attempt: int = 0
    workflow: str = ""
    submission_id: str = ""
    release_id: str = ""
    release_digest: str = ""
    owner: str = ""
    fencing_token: str = ""
    plan_digest: str = ""
    plan_identity: str = ""
    member_digests: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "workflow": self.workflow,
            "submission_id": self.submission_id,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "plan_digest": self.plan_digest,
            "plan_identity": self.plan_identity,
            "members": dict(self.member_digests),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> FrozenGroupManifest:
        if not isinstance(payload, Mapping):
            raise FrozenGroupError("frozen group manifest must be a mapping")
        raw_members = payload.get("members")
        if not isinstance(raw_members, Mapping):
            raise FrozenGroupError("frozen group manifest 'members' must be a mapping")
        members: dict[str, str] = {}
        for name, digest in raw_members.items():
            if name not in _GROUP_MEMBER_NAMES:
                raise FrozenGroupError(f"unknown group member {name!r}")
            if not isinstance(digest, str) or not _SHA256_HEX.match(digest):
                raise FrozenGroupError(f"member {name!r} digest is not a sha256 hex string")
            members[name] = digest
        if ENVELOPE_FILENAME not in members or PLAN_FILENAME not in members:
            raise FrozenGroupError(
                f"frozen group manifest must list both {ENVELOPE_FILENAME!r} and {PLAN_FILENAME!r}"
            )
        return cls(
            schema=int(payload.get("schema", 0)),
            job_id=str(payload.get("job_id") or ""),
            attempt=int(payload.get("attempt") or 0),
            workflow=str(payload.get("workflow") or ""),
            submission_id=str(payload.get("submission_id") or ""),
            release_id=str(payload.get("release_id") or ""),
            release_digest=str(payload.get("release_digest") or ""),
            owner=str(payload.get("owner") or ""),
            fencing_token=str(payload.get("fencing_token") or ""),
            plan_digest=str(payload.get("plan_digest") or ""),
            plan_identity=str(payload.get("plan_identity") or ""),
            member_digests=members,
        )

    @property
    def identity_key(self) -> tuple[Any, ...]:
        """Full freeze identity compared for same-attempt idempotency."""
        return (
            self.schema,
            self.job_id,
            self.attempt,
            self.workflow,
            self.submission_id,
            self.release_id,
            self.release_digest,
            self.owner,
            self.fencing_token,
            self.plan_digest,
            self.plan_identity,
            tuple(sorted(self.member_digests.items())),
        )


@dataclass(frozen=True)
class FrozenGroup:
    """A validated frozen-attempt input group on disk."""

    directory: Path
    manifest: FrozenGroupManifest

    @property
    def envelope_path(self) -> Path:
        return self.directory / ENVELOPE_FILENAME

    @property
    def plan_path(self) -> Path:
        return self.directory / PLAN_FILENAME

    @property
    def binding_path(self) -> Path | None:
        if BINDING_FILENAME in self.manifest.member_digests:
            return self.directory / BINDING_FILENAME
        return None


def frozen_group_dir(work_dir: Path | str) -> Path:
    """Canonical frozen-group directory of the task in *work_dir*."""
    return Path(work_dir).joinpath(*RUNTIME_PROTOCOL_DIR, FROZEN_GROUP_DIRNAME)


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_frozen_group_dir(directory: Path | str) -> FrozenGroup:
    """Validate a frozen-group directory whose manifest is present.

    Acceptance is all-or-nothing: the manifest must decode, every listed
    member must exist with a matching sha256, the envelope member must decode
    and agree with the manifest identity, and a ``binding.json`` member must be
    declared by the envelope's ``data_bindings``.  Sibling files that are not
    members are tolerated (a node-side flat layout uploads the group beside
    task files); the strict local reader additionally pins the directory
    listing in :func:`load_frozen_group`.
    """
    # Import stays local: the envelope contract is needed only for the
    # member cross-check, and keeping it out of module scope preserves this
    # module's stdlib-only import profile for its other consumers.
    from acp_services.contracts.envelope import (
        EnvelopeError,
        ExecutionEnvelope,
        StorageBinding,
    )

    target = Path(directory)
    manifest_path = target / FROZEN_GROUP_MANIFEST_FILENAME
    if not target.is_dir() or not manifest_path.is_file():
        raise FrozenGroupError(
            f"no committed frozen group under {target} "
            f"({FROZEN_GROUP_MANIFEST_FILENAME} is the commit marker)"
        )
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FrozenGroupError(
            f"frozen group manifest unreadable at {manifest_path}: {exc}"
        ) from exc
    manifest = FrozenGroupManifest.from_dict(raw)
    if manifest.schema != FROZEN_GROUP_SCHEMA:
        raise FrozenGroupError(
            f"frozen group schema {manifest.schema} != supported {FROZEN_GROUP_SCHEMA}"
        )
    if manifest.attempt < 1:
        raise FrozenGroupError("frozen group manifest attempt must be >= 1")
    for name in (
        "job_id",
        "submission_id",
        "release_id",
        "owner",
        "fencing_token",
        "plan_digest",
        "plan_identity",
    ):
        if not getattr(manifest, name):
            raise FrozenGroupError(f"frozen group manifest field {name!r} must not be empty")

    for name, expected in sorted(manifest.member_digests.items()):
        member = target / name
        if not member.is_file():
            raise FrozenGroupError(f"frozen group member {name!r} missing under {target}")
        actual = _sha256_of(member)
        if actual != expected:
            raise FrozenGroupError(
                f"frozen group member {name!r} hash {actual} != manifest {expected} "
                "(tampered or mixed group)"
            )

    try:
        envelope = ExecutionEnvelope.from_dict(
            json.loads((target / ENVELOPE_FILENAME).read_text(encoding="utf-8"))
        )
    except (OSError, ValueError) as exc:
        raise FrozenGroupError(f"frozen group envelope member undecodable: {exc}") from exc
    except EnvelopeError as exc:
        raise FrozenGroupError(f"frozen group envelope member invalid: {exc}") from exc
    declared = {binding.binding_id for binding in envelope.data_bindings}
    if BINDING_FILENAME in manifest.member_digests:
        try:
            frozen_binding = StorageBinding.from_dict(
                json.loads((target / BINDING_FILENAME).read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as exc:
            raise FrozenGroupError(f"frozen group binding member undecodable: {exc}") from exc
        except EnvelopeError as exc:
            raise FrozenGroupError(f"frozen group binding member invalid: {exc}") from exc
        if frozen_binding.binding_id not in declared:
            raise FrozenGroupError(
                f"frozen group binding {frozen_binding.binding_id!r} is not declared by "
                "the envelope"
            )
    elif declared:
        raise FrozenGroupError(
            "envelope declares data_bindings but the frozen group has no binding.json member"
        )
    mismatches = [
        f"{field}={getattr(envelope, field)!r}"
        for field in ("job_id", "submission_id", "release_id", "owner", "fencing_token")
        if getattr(envelope, field) != getattr(manifest, field)
    ]
    if envelope.attempt != manifest.attempt:
        mismatches.append(f"attempt={envelope.attempt}")
    if envelope.plan_digest != manifest.plan_digest:
        mismatches.append(f"plan_digest={envelope.plan_digest}")
    if mismatches:
        raise FrozenGroupError(
            f"frozen group manifest/envelope identity disagree: {', '.join(mismatches)}"
        )
    return FrozenGroup(directory=target, manifest=manifest)


def load_frozen_group(
    work_dir: Path | str, *, expected_attempt: int | None = None
) -> FrozenGroup:
    """Strict local reader: complete, hash-verified, exactly-membered group.

    The group directory must contain exactly the manifest-listed members plus
    the manifest itself — anything else is a mixed group and is rejected
    without partial adoption.  When *expected_attempt* is supplied, a valid
    group from another attempt is a typed :class:`FrozenAttemptMismatchError`
    (never silently reused or re-selected).
    """
    group = inspect_frozen_group_dir(frozen_group_dir(work_dir))
    expected = set(group.manifest.member_digests) | {FROZEN_GROUP_MANIFEST_FILENAME}
    actual = {entry.name for entry in group.directory.iterdir()}
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise FrozenGroupError(
            f"frozen group directory is mixed (extra={extra}, missing={missing}); "
            "refusing partial adoption"
        )
    if expected_attempt is not None and group.manifest.attempt != int(expected_attempt):
        raise FrozenAttemptMismatchError(
            f"frozen group attempt {group.manifest.attempt} != current attempt "
            f"{expected_attempt}; refusing to reuse a stale envelope"
        )
    return group


@dataclass(frozen=True)
class ExecutionLaunch:
    """Everything the worker entry needs for one attempt."""

    entry: str
    protocol: str
    closure: str
    work_dir: str
    envelope_path: str
    plan_path: str
    binding_path: str | None = None


def frozen_artifacts_present(
    work_dir: Path | str, *, expected_attempt: int | None = None
) -> bool:
    """True when a complete, hash-verified frozen group exists under *work_dir*.

    ``frozen_group.json`` is the sole commit marker: an envelope/plan pair
    without it, a group with a missing/tampered member, or a mixed directory
    all read as absent — the dispatcher then deterministically selects the
    legacy entry instead of adopting a partial freeze.  A valid group for a
    different attempt is absent for *this* attempt.
    """
    try:
        load_frozen_group(work_dir, expected_attempt=expected_attempt)
    except FrozenGroupError:
        return False
    return True


def local_launch(work_dir: Path | str, choice: AttemptExecutionChoice) -> ExecutionLaunch:
    """Build the local (absolute-path) worker launch for *choice*.

    The caller must only call this when ``choice.entry == envelope`` and a
    complete frozen group is present (:func:`frozen_artifacts_present`); a
    missing or invalid group is an explicit failure, never a silent legacy
    hop.
    """
    if choice.entry != EXECUTION_ENTRY_ENVELOPE:
        raise ValueError(f"attempt {choice.attempt} did not select the envelope entry")
    try:
        group = load_frozen_group(work_dir, expected_attempt=choice.attempt)
    except FrozenAttemptMismatchError:
        raise
    except FrozenGroupError as exc:
        raise FileNotFoundError(
            f"no complete frozen group under {frozen_group_dir(work_dir)} "
            f"for attempt {choice.attempt}: {exc}"
        ) from exc
    return ExecutionLaunch(
        entry=choice.entry,
        protocol=choice.protocol,
        closure=choice.closure,
        work_dir=str(work_dir),
        envelope_path=str(group.envelope_path),
        plan_path=str(group.plan_path),
        binding_path=str(group.binding_path) if group.binding_path is not None else None,
    )


def worker_argv(
    python_executable: str,
    launch: ExecutionLaunch,
    *,
    work_dir: str | None = None,
    receipt_path: str | None = None,
    probe_executor: str | None = None,
) -> list[str]:
    """Build the ``python -m acp_services.worker_bootstrap`` argv (single root)."""
    py = python_executable or "python"
    argv: list[str] = [
        py,
        "-m",
        WORKER_MODULE,
        "--envelope",
        str(launch.envelope_path),
        "--plan",
        str(launch.plan_path),
        "--work-dir",
        str(work_dir if work_dir is not None else launch.work_dir),
    ]
    if receipt_path:
        argv += ["--receipt", str(receipt_path)]
    if launch.binding_path:
        argv += ["--binding", str(launch.binding_path)]
    if probe_executor:
        argv += ["--probe-executor", str(probe_executor)]
    return argv


def launch_with_remote_paths(
    launch: ExecutionLaunch,
    *,
    work_dir: str,
    envelope_path: str,
    plan_path: str,
    binding_path: str | None,
) -> ExecutionLaunch:
    """Re-point a launch at the node-side absolute paths (remote upload)."""
    return replace(
        launch,
        work_dir=work_dir,
        envelope_path=envelope_path,
        plan_path=plan_path,
        binding_path=binding_path,
    )


def iter_enabled_workflows() -> Iterable[str]:
    """Iterate the C01-integrated (enabled) workflow ids in table order."""
    return iter(ENABLED_WORKFLOWS)
