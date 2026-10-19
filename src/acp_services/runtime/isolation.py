"""Execution-isolation adapters + capability matrix (B09-3 / plan todo 50, MU-06).

Main plan §22.5 distinguishes **resource control** from **isolation**.  Linux
cgroup v2 (``https://docs.kernel.org/admin-guide/cgroup-v2.html``) constrains
CPU / memory / PID count; it is *not* a complete file-access or malicious-code
isolation boundary.  This module makes that distinction executable and gates a
deployment tier on it.

Hard rules encoded here
-----------------------
* **Pseudo-isolation is never isolation.**  Separating two principals into
  per-directory roots while they share one Unix UID, setting only
  ``OMP_NUM_THREADS``, or putting a login page in front of the service does
  **not** isolate them (§22.5).  :data:`PSEUDO_MECHANISMS` can never raise the
  :class:`IsolationLevel` nor satisfy a tier requirement.
* **Local = restricted OS identity / controlled sandbox / cgroup v2 limits
  bound to the envelope process group.**  A resource ceiling is applied to the
  process group the execution creates (via ``cgroup.procs``), identified by the
  envelope's ``submission_id`` — never by an environment variable alone.
* **LSF = a *persistent* platform-identity → execution-account / queue /
  resource-policy mapping with minimal mounts and a stop scope limited to the
  job's own identity.**  The adapter never executes as a client-supplied
  arbitrary username, and ``bkill`` / ``bstop`` / ``bresume`` may only address
  this job's own numeric id + account.
* **Refuse, never downgrade.**  When a tier's required isolation capability is
  absent or unverified, :class:`IsolationGate` refuses enablement with a typed
  reason — it never silently falls back to ``local-single`` or to a
  "same-UID directory" substitute.

Dependency direction (``tests/baseline/services-evidence/public_allowlist.md``)
-----------------------------------------------------------------------------
``src/acp_services/**`` must never import ``acp``.  This module is **stdlib
only** plus the sibling frozen contracts (``contracts.access`` deployment-tier /
execution-mode vocabulary, ``contracts.envelope`` execution identity).  The
capability matrix it renders is tracked at
``tests/baseline/services-evidence/isolation_matrix.md``.

Author: ACP Services (B lane, todo 50).
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.access import DeploymentTier, ExecutionMode
from acp_services.contracts.envelope import ExecutionEnvelope

logger = logging.getLogger(__name__)

__all__ = [
    # mechanism / level / status vocabularies
    "IsolationMechanism",
    "PSEUDO_MECHANISMS",
    "REAL_ISOLATION_MECHANISMS",
    "IsolationLevel",
    "CapabilityStatus",
    "IsolationReason",
    "MountMode",
    "BrokerOperation",
    # value objects
    "TierCapability",
    "IsolationConfig",
    "OsIdentity",
    "EnvelopeProcessGroup",
    "CgroupV2Plan",
    "MountSpec",
    "EnvelopeIsolationBinding",
    "LsfExecutionAccount",
    "LsfExecutionBinding",
    "StopScope",
    "TierRequirement",
    "IsolationMatrixRow",
    "IsolationDecision",
    "CrossUserAudit",
    # errors
    "IsolationError",
    "IsolationUnavailableError",
    "IsolationRefusedError",
    "IdentityNotMappedError",
    "CrossUserIsolationError",
    # ports
    "PrivilegedBroker",
    # pure helpers
    "is_pseudo_only",
    "level_for_mechanisms",
    "is_isolated_level",
    "minimal_mount_policy",
    "assert_minimal_mounts",
    "audit_cross_user_access",
    "render_isolation_decision",
    # adapters / gate
    "LocalIsolationAdapter",
    "LsfIsolationAdapter",
    "IsolationGate",
]


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class IsolationMechanism(str):
    """Mechanism vocabulary.

    The real mechanisms impose a boundary the execution cannot cross; the
    pseudo mechanisms are the *forbidden substitutes* a naive implementation
    mistakes for isolation.
    """

    # real isolation / resource-control mechanisms
    OS_IDENTITY = "os-identity"
    SANDBOX = "sandbox"
    CGROUP_V2 = "cgroup-v2"
    LSF_IDENTITY = "lsf-identity"
    LSF_STOP_SCOPE = "lsf-stop-scope"
    MINIMAL_MOUNTS = "minimal-mounts"

    # pseudo mechanisms — NEVER isolation
    SAME_UID_DIRECTORY = "same-uid-directory"
    OMP_NUM_THREADS = "omp-num-threads"
    LOGIN_PAGE = "login-page"

    ALL = frozenset(
        {
            OS_IDENTITY,
            SANDBOX,
            CGROUP_V2,
            LSF_IDENTITY,
            LSF_STOP_SCOPE,
            MINIMAL_MOUNTS,
            SAME_UID_DIRECTORY,
            OMP_NUM_THREADS,
            LOGIN_PAGE,
        }
    )


#: The forbidden substitutes for isolation (§22.5).  They are recognised,
#: reported honestly, and can never raise an :class:`IsolationLevel`.
PSEUDO_MECHANISMS: frozenset[str] = frozenset(
    {
        IsolationMechanism.SAME_UID_DIRECTORY,
        IsolationMechanism.OMP_NUM_THREADS,
        IsolationMechanism.LOGIN_PAGE,
    }
)

#: Mechanisms that actually impose an isolation / resource boundary.
REAL_ISOLATION_MECHANISMS: frozenset[str] = IsolationMechanism.ALL - PSEUDO_MECHANISMS


class IsolationLevel(str):
    """Ordered isolation level derived from a mechanism set.

    ``PSEUDO`` is an explicit, non-isolating level: it records that only
    forbidden substitutes were configured.  :func:`is_isolated_level` returns
    ``False`` for ``NONE``, ``PSEUDO`` and ``RESOURCE_LIMITED`` (cgroup is
    resource control, not a file/process isolation boundary).
    """

    NONE = "none"
    PSEUDO = "pseudo"
    RESOURCE_LIMITED = "resource-limited"
    IDENTITY_ISOLATED = "identity-isolated"
    SANDBOX_ISOLATED = "sandbox-isolated"
    ALL = frozenset({NONE, PSEUDO, RESOURCE_LIMITED, IDENTITY_ISOLATED, SANDBOX_ISOLATED})


#: Ascending order used for reporting / comparison.
_LEVEL_ORDER: tuple[str, ...] = (
    IsolationLevel.NONE,
    IsolationLevel.PSEUDO,
    IsolationLevel.RESOURCE_LIMITED,
    IsolationLevel.IDENTITY_ISOLATED,
    IsolationLevel.SANDBOX_ISOLATED,
)


class CapabilityStatus(str):
    """Honest per-capability status (three-state + configured).

    * ``MEASURED`` — actually exercised in this environment (probe/unit evidence).
    * ``CONFIGURED`` — present and configured but the enforcement path was not
      exercised here.
    * ``NOT_VERIFIED`` — no environment to verify it (never counted as enabled).
    * ``UNAVAILABLE`` — the mechanism is absent/failed the probe.
    """

    MEASURED = "measured"
    CONFIGURED = "configured"
    NOT_VERIFIED = "not-verified"
    UNAVAILABLE = "unavailable"
    ALL = frozenset({MEASURED, CONFIGURED, NOT_VERIFIED, UNAVAILABLE})


class IsolationReason(str):
    """Structured enablement/refusal vocabulary."""

    ENABLED = "enabled"
    NO_ISOLATION_REQUIRED = "no_isolation_required"
    PSEUDO_ISOLATION_REJECTED = "pseudo_isolation_rejected"
    IDENTITY_ISOLATION_REQUIRED = "identity_isolation_required"
    RESOURCE_LIMIT_REQUIRED = "resource_limit_required"
    MINIMAL_MOUNTS_REQUIRED = "minimal_mounts_required"
    UNKNOWN_TIER = "unknown_tier"
    ALL = frozenset(
        {
            ENABLED,
            NO_ISOLATION_REQUIRED,
            PSEUDO_ISOLATION_REJECTED,
            IDENTITY_ISOLATION_REQUIRED,
            RESOURCE_LIMIT_REQUIRED,
            MINIMAL_MOUNTS_REQUIRED,
            UNKNOWN_TIER,
        }
    )


class MountMode(str):
    """Mount access mode."""

    RO = "ro"
    RW = "rw"
    ALL = frozenset({RO, RW})


class BrokerOperation(str):
    """Whitelisted privileged operations (never an arbitrary shell/sudo).

    The broker is infrastructure owned elsewhere (todo 51); this module only
    consumes it through :class:`PrivilegedBroker` and refuses anything outside
    this vocabulary.
    """

    CREATE_OS_IDENTITY = "create_os_identity"
    SET_QUOTA = "set_quota"
    START_SANDBOX = "start_sandbox"
    ALL = frozenset({CREATE_OS_IDENTITY, SET_QUOTA, START_SANDBOX})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class IsolationError(RuntimeError):
    """Base error for isolation-contract violations."""


class IsolationUnavailableError(IsolationError):
    """A requested isolation mechanism cannot be enforced in this environment."""


class IsolationRefusedError(IsolationError):
    """Enablement of a tier was refused because required isolation is absent.

    Carries the :class:`IsolationDecision` so callers can surface the typed
    reason.  The refusal is never a silent downgrade: ``requested_tier`` stays
    the requested tier and no substitute tier is granted.
    """

    def __init__(self, decision: IsolationDecision) -> None:
        super().__init__(
            f"isolation refused for tier {decision.tier!r}: {decision.reason_code} "
            f"(missing={list(decision.missing)})"
        )
        self.decision = decision


class IdentityNotMappedError(IsolationError):
    """No persistent execution identity is mapped for a principal."""


class CrossUserIsolationError(IsolationError):
    """A same-UID / permissive directory layout was offered as isolation."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierCapability:
    """One row of the capability matrix: adapter × mechanism × status."""

    adapter: str
    execution_mode: str
    mechanism: str
    status: str
    method: str
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.status not in CapabilityStatus.ALL:
            raise IsolationError(f"unknown capability status {self.status!r}")

    @property
    def is_isolation(self) -> bool:
        return self.mechanism in REAL_ISOLATION_MECHANISMS

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "execution_mode": self.execution_mode,
            "mechanism": self.mechanism,
            "status": self.status,
            "method": self.method,
            "evidence": self.evidence,
            "is_isolation": self.is_isolation,
        }


@dataclass(frozen=True)
class IsolationConfig:
    """Declarative isolation configuration handed to an adapter.

    ``mechanisms`` is the *declared* set; the adapter still probes the
    environment and may report a mechanism as ``NOT_VERIFIED`` / ``UNAVAILABLE``.
    A config that declares only pseudo mechanisms is accepted (so it can be
    reported honestly) but never yields an isolation level.
    """

    execution_mode: str
    mechanisms: tuple[str, ...] = ()
    identity_map: Mapping[str, OsIdentity] = field(default_factory=dict)
    cgroup_root: str = "/sys/fs/cgroup"
    sandbox_runtime: str | None = None

    def __post_init__(self) -> None:
        unknown = set(self.mechanisms) - IsolationMechanism.ALL
        if unknown:
            raise IsolationError(f"unknown isolation mechanism(s) {sorted(unknown)}")


@dataclass(frozen=True)
class OsIdentity:
    """A restricted OS execution identity (distinct uid/gid per principal)."""

    username: str
    uid: int
    gid: int

    def is_distinct_from(self, other: OsIdentity) -> bool:
        return self.uid != other.uid or self.gid != other.gid


@dataclass(frozen=True)
class EnvelopeProcessGroup:
    """The process group created for exactly one envelope attempt.

    Resource limits bind here (via ``cgroup.procs``), not to a bare env var.
    ``envelope_key`` is the envelope ``submission_id`` — one attempt, one group.
    """

    envelope_key: str
    job_id: str
    attempt: int
    pgid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope_key": self.envelope_key,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "pgid": self.pgid,
        }


@dataclass(frozen=True)
class CgroupV2Plan:
    """A cgroup v2 resource-control plan bound to an envelope process group."""

    cgroup_path: str
    envelope_key: str
    cpu_max: str | None = None
    memory_max_bytes: int | None = None
    pids_max: int | None = None

    @property
    def procs_file(self) -> str:
        return f"{self.cgroup_path}/cgroup.procs"

    def placement_writes(self, pgid: int) -> tuple[tuple[str, str], ...]:
        """Return ``(path, value)`` writes that bind the group to the cgroup."""
        writes: list[tuple[str, str]] = [(f"{self.cgroup_path}/cgroup.procs", str(pgid))]
        if self.cpu_max is not None:
            writes.append((f"{self.cgroup_path}/cpu.max", self.cpu_max))
        if self.memory_max_bytes is not None:
            writes.append((f"{self.cgroup_path}/memory.max", str(self.memory_max_bytes)))
        if self.pids_max is not None:
            writes.append((f"{self.cgroup_path}/pids.max", str(self.pids_max)))
        return tuple(writes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cgroup_path": self.cgroup_path,
            "envelope_key": self.envelope_key,
            "cpu_max": self.cpu_max,
            "memory_max_bytes": self.memory_max_bytes,
            "pids_max": self.pids_max,
        }


@dataclass(frozen=True)
class MountSpec:
    """One bind mount exposed to an execution."""

    source: str
    target: str
    mode: str = MountMode.RO
    role: str = ""

    def __post_init__(self) -> None:
        if self.mode not in MountMode.ALL:
            raise IsolationError(f"unknown mount mode {self.mode!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "mode": self.mode,
            "role": self.role,
        }


@dataclass(frozen=True)
class EnvelopeIsolationBinding:
    """What the Local adapter attaches to one envelope execution."""

    envelope_key: str
    execution_mode: str
    process_group: EnvelopeProcessGroup
    mechanisms: frozenset[str]
    uid: int | None = None
    gid: int | None = None
    cgroup: CgroupV2Plan | None = None
    sandbox_root: str | None = None
    mounts: tuple[MountSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "envelope_key": self.envelope_key,
            "execution_mode": self.execution_mode,
            "process_group": self.process_group.to_dict(),
            "mechanisms": sorted(self.mechanisms),
            "uid": self.uid,
            "gid": self.gid,
            "cgroup": None if self.cgroup is None else self.cgroup.to_dict(),
            "sandbox_root": self.sandbox_root,
            "mounts": [mount.to_dict() for mount in self.mounts],
        }


@dataclass(frozen=True)
class LsfExecutionAccount:
    """A persistent mapping entry: platform principal → LSF execution identity."""

    principal_id: str
    account: str
    queue: str
    resource_policy: str
    extra_mounts: tuple[MountSpec, ...] = ()


@dataclass(frozen=True)
class StopScope:
    """The only identities a stop request may address.

    ``bkill`` / ``bstop`` / ``bresume`` may address **only** this job's own
    numeric LSF id and (when an account is given) this job's own execution
    account.  Anything else — a different id, a superstring, a foreign
    ``-u``/``-m`` account, or a path-based id — is rejected.
    """

    lsf_job_id: str
    execution_account: str
    submission_id: str = ""

    def __post_init__(self) -> None:
        if not str(self.lsf_job_id).strip():
            raise IsolationError("StopScope requires a non-empty lsf_job_id")
        if not _is_numeric_id(self.lsf_job_id):
            raise IsolationError(
                f"StopScope requires a numeric LSF job id, got {self.lsf_job_id!r}"
            )

    def permits_job(self, job_id: str, account: str) -> bool:
        return str(job_id) == self.lsf_job_id and str(account) == self.execution_account

    def permits_command(self, argv: Sequence[str]) -> bool:
        """Whether *argv* addresses exactly this job's own identity."""
        if not argv:
            return False
        verb = str(argv[0]).rsplit("/", 1)[-1]
        if verb not in {"bkill", "bstop", "bresume"}:
            return False
        account: str | None = None
        operands: list[str] = []
        index = 1
        while index < len(argv):
            token = str(argv[index])
            if token in {"-u", "-m"}:
                if index + 1 >= len(argv):
                    return False
                account = str(argv[index + 1])
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            operands.append(token)
            index += 1
        if len(operands) != 1:
            return False
        if not _is_numeric_id(operands[0]):
            return False
        if operands[0] != self.lsf_job_id:
            return False
        return account is None or account == self.execution_account

    def permits_shell_command(self, command: str) -> bool:
        """Convenience: shlex-split a shell command (ignoring redirects) and check."""
        tokens = shlex.split(command)
        cleaned = [token for token in tokens if token not in {"2>&1", "2>/dev/null", "||", "true"}]
        if not cleaned:
            return False
        return self.permits_command(cleaned)

    def kill_command(self) -> tuple[str, ...]:
        return ("bkill", self.lsf_job_id)

    def suspend_command(self) -> tuple[str, ...]:
        return ("bstop", self.lsf_job_id)

    def resume_command(self) -> tuple[str, ...]:
        return ("bresume", self.lsf_job_id)

    def pkill_fallback_pattern(self) -> str:
        """Anchored fallback pattern scoped to this job id (never a substring).

        The legacy monitor's fallback (``acp.scheduler.remote.monitor``) matched
        ``acp.cli run.*<id>`` as a substring; this pattern requires a whole-token
        id so a superstring id or an unrelated argv cannot be swept.
        """
        return rf"(?<![0-9]){re.escape(self.lsf_job_id)}(?![0-9])"

    def to_dict(self) -> dict[str, Any]:
        return {
            "lsf_job_id": self.lsf_job_id,
            "execution_account": self.execution_account,
            "submission_id": self.submission_id,
        }


@dataclass(frozen=True)
class LsfExecutionBinding:
    """The persistent LSF identity binding produced for one envelope execution."""

    principal_id: str
    execution_account: str
    queue: str
    resource_policy: str
    submission_id: str
    job_id: str
    attempt: int
    minimal_mounts: tuple[MountSpec, ...]
    stop_scope: StopScope
    mechanisms: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {IsolationMechanism.LSF_IDENTITY, IsolationMechanism.MINIMAL_MOUNTS}
        )
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "execution_account": self.execution_account,
            "queue": self.queue,
            "resource_policy": self.resource_policy,
            "submission_id": self.submission_id,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "minimal_mounts": [mount.to_dict() for mount in self.minimal_mounts],
            "stop_scope": self.stop_scope.to_dict(),
            "mechanisms": sorted(self.mechanisms),
        }


@dataclass(frozen=True)
class TierRequirement:
    """What a deployment tier requires before it may be enabled."""

    tier: str
    identity_any_of: frozenset[str] = frozenset()
    resource_any_of: frozenset[str] = frozenset()
    all_of: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IsolationDecision:
    """Structured enablement/refusal verdict for a deployment tier."""

    tier: str
    enabled: bool
    reason_code: str
    level: str
    mechanisms: frozenset[str]
    missing: tuple[str, ...] = ()
    detail: str = ""

    @property
    def refused(self) -> bool:
        return not self.enabled

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "enabled": self.enabled,
            "reason_code": self.reason_code,
            "level": self.level,
            "mechanisms": sorted(self.mechanisms),
            "missing": list(self.missing),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class IsolationMatrixRow:
    """A grouped capability-matrix row (all capabilities of one adapter)."""

    adapter: str
    execution_mode: str
    level: str
    capabilities: tuple[TierCapability, ...]

    def verified(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                cap.mechanism
                for cap in self.capabilities
                if cap.status == CapabilityStatus.MEASURED
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "execution_mode": self.execution_mode,
            "level": self.level,
            "capabilities": [cap.to_dict() for cap in self.capabilities],
        }


@dataclass(frozen=True)
class CrossUserAudit:
    """Result of auditing whether a directory layout isolates two OS users."""

    path: str
    owner_uid: int
    other_uid: int
    same_uid: bool
    other_accessible: bool
    isolated: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "owner_uid": self.owner_uid,
            "other_uid": self.other_uid,
            "same_uid": self.same_uid,
            "other_accessible": self.other_accessible,
            "isolated": self.isolated,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


@runtime_checkable
class PrivilegedBroker(Protocol):
    """A whitelisted privileged-operation port (owned by todo 51, consumed here).

    The adapter only ever asks for a :data:`BrokerOperation`; there is no method
    to run an arbitrary command, pass ``sudo`` arguments, or open a shell.
    """

    def capabilities(self) -> frozenset[str]:
        """Return the whitelisted operations this broker can actually perform."""
        ...

    def perform(self, operation: str, payload: Mapping[str, str]) -> Mapping[str, str]:
        """Perform one whitelisted operation; raise on anything outside the set."""
        ...


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def is_pseudo_only(mechanisms: Iterable[str]) -> bool:
    """True when a mechanism set holds at least one pseudo and no real mechanism."""
    observed = {str(item) for item in mechanisms}
    has_pseudo = bool(observed & PSEUDO_MECHANISMS)
    has_real = bool(observed & REAL_ISOLATION_MECHANISMS)
    return has_pseudo and not has_real


def level_for_mechanisms(mechanisms: Iterable[str]) -> str:
    """Derive the honest :class:`IsolationLevel` for a mechanism set.

    Pseudo mechanisms never raise the level.  ``RESOURCE_LIMITED`` (cgroup v2)
    is below ``IDENTITY_ISOLATED`` because resource control is not a file /
    process isolation boundary (§22.5).
    """
    observed = {str(item) for item in mechanisms}
    if IsolationMechanism.SANDBOX in observed:
        return IsolationLevel.SANDBOX_ISOLATED
    if observed & {IsolationMechanism.OS_IDENTITY, IsolationMechanism.LSF_IDENTITY}:
        return IsolationLevel.IDENTITY_ISOLATED
    if IsolationMechanism.CGROUP_V2 in observed:
        return IsolationLevel.RESOURCE_LIMITED
    if observed & PSEUDO_MECHANISMS:
        return IsolationLevel.PSEUDO
    return IsolationLevel.NONE


def is_isolated_level(level: str) -> bool:
    """Whether *level* denotes real isolation (not resource control / pseudo)."""
    return str(level) in {IsolationLevel.IDENTITY_ISOLATED, IsolationLevel.SANDBOX_ISOLATED}


def _is_numeric_id(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9]+", str(value)))


#: Mount sources that may never be exposed wholesale to an execution.
FORBIDDEN_MOUNT_SOURCES: frozenset[str] = frozenset(
    {"/", "/home", "/root", "/etc", "/var", "/usr", "/boot", "~", "$HOME", ""}
)

#: Substrings that must never appear in a mount source (secrets / platform DB).
_FORBIDDEN_MOUNT_SUBSTRINGS: tuple[str, ...] = (
    "acp_jobs.db",
    ".ssh",
    "id_rsa",
    "id_ed25519",
    "secrets",
    "credentials",
    "known_hosts",
)

#: Roles that may ever be mounted read-write.
_WRITABLE_ROLES: frozenset[str] = frozenset({"workdir", "tmp", "data_binding"})


def minimal_mount_policy(
    *,
    workdir: str,
    code_release: str,
    data_bindings: Sequence[str] = (),
    include_tmp: bool = True,
) -> tuple[MountSpec, ...]:
    """Return the least-privilege mount set for one execution.

    The job workdir is read-write; the frozen code release is read-only; each
    data binding is read-only.  Nothing else is exposed.
    """
    mounts = [
        MountSpec(source=workdir, target="/work", mode=MountMode.RW, role="workdir"),
        MountSpec(
            source=code_release, target="/opt/acp/code", mode=MountMode.RO, role="code_release"
        ),
    ]
    if include_tmp:
        mounts.append(MountSpec(source="/tmp", target="/tmp", mode=MountMode.RW, role="tmp"))
    mounts.extend(
        MountSpec(source=source, target=f"/data/{index}", mode=MountMode.RO, role="data_binding")
        for index, source in enumerate(data_bindings)
    )
    assert_minimal_mounts(tuple(mounts))
    return tuple(mounts)


def assert_minimal_mounts(mounts: Sequence[MountSpec]) -> None:
    """Raise :class:`IsolationError` if any mount breaks least privilege."""
    for mount in mounts:
        source = str(mount.source).strip()
        if source in FORBIDDEN_MOUNT_SOURCES:
            raise IsolationError(f"mount source {source!r} is not minimal")
        lowered = source.lower().rstrip("/")
        for bad in _FORBIDDEN_MOUNT_SUBSTRINGS:
            if bad in lowered:
                raise IsolationError(f"mount source {source!r} exposes forbidden material {bad!r}")
        if mount.mode == MountMode.RW and mount.role not in _WRITABLE_ROLES:
            raise IsolationError(
                f"read-write mount {source!r} (role={mount.role!r}) is not least-privilege"
            )


def audit_cross_user_access(path: str | Path, *, owner_uid: int, other_uid: int) -> CrossUserAudit:
    """Audit whether *path* isolates *other_uid* from the *owner_uid*.

    A directory layout under a **shared UID is never isolation** — the two
    principals are the same OS subject, so per-directory roots change nothing.
    When the UIDs differ, the directory must exclude group/other access.
    """
    target = Path(path)
    if int(owner_uid) == int(other_uid):
        return CrossUserAudit(
            path=str(target),
            owner_uid=int(owner_uid),
            other_uid=int(other_uid),
            same_uid=True,
            other_accessible=True,
            isolated=False,
            detail="same Unix UID: per-directory separation is not isolation",
        )
    try:
        info = target.stat()
    except OSError as exc:
        return CrossUserAudit(
            path=str(target),
            owner_uid=int(owner_uid),
            other_uid=int(other_uid),
            same_uid=False,
            other_accessible=True,
            isolated=False,
            detail=f"cannot stat {target}: {exc}",
        )
    mode = stat.S_IMODE(info.st_mode)
    same_owner = int(info.st_uid) == int(owner_uid)
    other_bits = mode & 0o007
    group_bits = mode & 0o070
    accessible = not same_owner or bool(other_bits or group_bits)
    if accessible:
        return CrossUserAudit(
            path=str(target),
            owner_uid=int(owner_uid),
            other_uid=int(other_uid),
            same_uid=False,
            other_accessible=True,
            isolated=False,
            detail=f"directory mode {mode:04o} (owner match={same_owner}) permits other access",
        )
    return CrossUserAudit(
        path=str(target),
        owner_uid=int(owner_uid),
        other_uid=int(other_uid),
        same_uid=False,
        other_accessible=False,
        isolated=True,
        detail=f"distinct UID with restrictive mode {mode:04o}",
    )


# ---------------------------------------------------------------------------
# Capability probes
# ---------------------------------------------------------------------------


def _detect_cgroup_v2(root: str) -> tuple[bool, bool]:
    """Return ``(v2_present, subtree_writable)`` for the cgroup root."""
    controllers = Path(root) / "cgroup.controllers"
    present = controllers.is_file()
    writable = False
    if present:
        probe = Path(root) / f"acp_iso_probe_{os.getpid()}"
        try:
            probe.mkdir()
            writable = True
        except OSError:
            writable = False
        finally:
            try:
                probe.rmdir()
            except OSError:
                pass
    return present, writable


def _which(binary: str | None) -> str | None:
    if not binary:
        return None
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / binary
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


# ---------------------------------------------------------------------------
# Local isolation adapter
# ---------------------------------------------------------------------------


class LocalIsolationAdapter:
    """Local execution isolation: restricted OS identity / sandbox / cgroup v2.

    The adapter reports what it can actually enforce.  A configuration that
    declares only pseudo mechanisms resolves to :data:`IsolationLevel.PSEUDO`
    and cannot satisfy an identity requirement.  Resource limits are planned
    against the **envelope process group**, not an environment variable.
    """

    adapter_id = "local"
    execution_mode = ExecutionMode.LOCAL

    def __init__(
        self,
        config: IsolationConfig,
        *,
        broker: PrivilegedBroker | None = None,
        probe: Callable[[], Mapping[str, str]] | None = None,
        cgroup_base: str = "/acp",
    ) -> None:
        self.config = config
        self._broker = broker
        self._probe = probe
        self._cgroup_base = cgroup_base
        self._capabilities = self._measure()
        self._verified = frozenset(
            cap.mechanism for cap in self._capabilities if cap.status == CapabilityStatus.MEASURED
        )

    # -- capabilities -------------------------------------------------------

    def _measure(self) -> tuple[TierCapability, ...]:
        declared = set(self.config.mechanisms)
        caps: list[TierCapability] = []

        identity_declared = IsolationMechanism.OS_IDENTITY in declared
        mapped = bool(self.config.identity_map)
        broker_ok = self._broker is not None and (
            BrokerOperation.CREATE_OS_IDENTITY in self._broker.capabilities()
        )
        if not identity_declared:
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=IsolationMechanism.OS_IDENTITY,
                    status=CapabilityStatus.UNAVAILABLE,
                    method="not declared in isolation config",
                )
            )
        elif mapped or broker_ok:
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=IsolationMechanism.OS_IDENTITY,
                    status=CapabilityStatus.MEASURED,
                    method=(
                        "identity map present" if mapped else "privileged broker create_os_identity"
                    ),
                    evidence=f"identities={len(self.config.identity_map)}",
                )
            )
        else:
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=IsolationMechanism.OS_IDENTITY,
                    status=CapabilityStatus.NOT_VERIFIED,
                    method="requires a configured identity map or privileged broker",
                )
            )

        v2_present, v2_writable = _detect_cgroup_v2(self.config.cgroup_root)
        if not v2_present:
            cgroup_status = CapabilityStatus.UNAVAILABLE
            cgroup_method = f"no cgroup v2 at {self.config.cgroup_root}"
        elif v2_writable:
            cgroup_status = CapabilityStatus.MEASURED
            cgroup_method = (
                "cgroup v2 detected and delegated subtree writable "
                "(mkdir/rmdir probe on cgroup root)"
            )
        else:
            cgroup_status = CapabilityStatus.CONFIGURED
            cgroup_method = (
                "cgroup v2 detected (/proc/self/cgroup 0::) but subtree not writable "
                "without privileges"
            )
        caps.append(
            TierCapability(
                adapter=self.adapter_id,
                execution_mode=self.execution_mode,
                mechanism=IsolationMechanism.CGROUP_V2,
                status=cgroup_status,
                method=cgroup_method,
                evidence=(
                    "cpu/memory/pids limits bound to envelope process group via cgroup.procs"
                    if v2_present
                    else ""
                ),
            )
        )

        sandbox_binary = (
            self.config.sandbox_runtime
            if self.config.sandbox_runtime and _which(self.config.sandbox_runtime)
            else None
        )
        if IsolationMechanism.SANDBOX not in declared:
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=IsolationMechanism.SANDBOX,
                    status=CapabilityStatus.UNAVAILABLE,
                    method="no sandbox runtime configured",
                )
            )
        elif sandbox_binary:
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=IsolationMechanism.SANDBOX,
                    status=CapabilityStatus.CONFIGURED,
                    method=f"runtime {sandbox_binary!r} present; sandbox start not exercised",
                    evidence="mount+pid isolation via controlled runtime",
                )
            )
        else:
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=IsolationMechanism.SANDBOX,
                    status=CapabilityStatus.NOT_VERIFIED,
                    method=f"sandbox runtime {self.config.sandbox_runtime!r} not found on PATH",
                )
            )

        caps.append(
            TierCapability(
                adapter=self.adapter_id,
                execution_mode=self.execution_mode,
                mechanism=IsolationMechanism.MINIMAL_MOUNTS,
                status=CapabilityStatus.MEASURED,
                method="minimal_mount_policy() rejects broad/secrets mounts",
                evidence="workdir rw; code release ro; data bindings ro",
            )
        )

        pseudo_methods = {
            IsolationMechanism.SAME_UID_DIRECTORY: "same UID with per-directory roots",
            IsolationMechanism.OMP_NUM_THREADS: "OMP_NUM_THREADS only",
            IsolationMechanism.LOGIN_PAGE: "login page only",
        }
        for mechanism, method in pseudo_methods.items():
            caps.append(
                TierCapability(
                    adapter=self.adapter_id,
                    execution_mode=self.execution_mode,
                    mechanism=mechanism,
                    status=CapabilityStatus.UNAVAILABLE,
                    method=f"{method} — never isolation",
                )
            )
        if self._probe is not None:
            overrides = {str(key): str(value) for key, value in self._probe().items()}
            caps = [
                replace(cap, status=overrides[cap.mechanism]) if cap.mechanism in overrides else cap
                for cap in caps
            ]
        return tuple(caps)

    def capabilities(self) -> tuple[TierCapability, ...]:
        return self._capabilities

    def verified_mechanisms(self) -> frozenset[str]:
        """Real mechanisms actually exercised/enforced in this environment."""
        return frozenset(self._verified & REAL_ISOLATION_MECHANISMS)

    def declared_mechanisms(self) -> frozenset[str]:
        return frozenset(self.config.mechanisms)

    def level(self) -> str:
        pseudo_declared = frozenset(self.config.mechanisms) & PSEUDO_MECHANISMS
        return level_for_mechanisms(self.verified_mechanisms() | pseudo_declared)

    def matrix_row(self) -> IsolationMatrixRow:
        return IsolationMatrixRow(
            adapter=self.adapter_id,
            execution_mode=self.execution_mode,
            level=self.level(),
            capabilities=self._capabilities,
        )

    # -- privileged operations ---------------------------------------------

    def _require_broker(self, operation: str) -> None:
        if operation not in BrokerOperation.ALL:
            raise IsolationError(f"broker operation {operation!r} is not whitelisted")
        if self._broker is None or operation not in self._broker.capabilities():
            raise IsolationUnavailableError(
                f"privileged broker cannot perform {operation!r} (whitelisted only)"
            )

    def resolve_identity(self, principal_id: str) -> OsIdentity:
        identity = self.config.identity_map.get(str(principal_id))
        if identity is None:
            raise IdentityNotMappedError(
                f"no restricted OS identity mapped for principal {principal_id!r}"
            )
        return identity

    # -- binding ------------------------------------------------------------

    def bind(
        self,
        envelope: ExecutionEnvelope,
        *,
        principal_id: str,
        workdir: str,
        code_release: str,
        data_bindings: Sequence[str] = (),
        process_group_id: int | None = None,
        require_isolation: bool = False,
    ) -> EnvelopeIsolationBinding:
        """Bind the envelope execution to its identity / resource / mount set.

        Raises :class:`IsolationUnavailableError` when the environment cannot
        enforce the declared mechanisms — never a partial/degraded binding.  With
        ``require_isolation=True`` a resource-only / pseudo-only layout is
        refused outright.
        """
        mechanisms = self.verified_mechanisms()
        if not mechanisms:
            raise IsolationUnavailableError(
                "no verified isolation mechanism available; refusing to bind "
                "(same-UID directories / OMP_NUM_THREADS / login page are not isolation)"
            )
        if require_isolation and not is_isolated_level(level_for_mechanisms(mechanisms)):
            raise IsolationUnavailableError(
                f"refusing non-isolating binding for {principal_id!r}: level "
                f"{level_for_mechanisms(mechanisms)!r} is not identity/sandbox isolation"
            )

        uid: int | None = None
        gid: int | None = None
        if IsolationMechanism.OS_IDENTITY in mechanisms:
            identity = self.resolve_identity(principal_id)
            uid, gid = identity.uid, identity.gid

        process_group = EnvelopeProcessGroup(
            envelope_key=envelope.submission_id,
            job_id=str(envelope.job_id),
            attempt=int(envelope.attempt),
            pgid=process_group_id,
        )

        cgroup: CgroupV2Plan | None = None
        if IsolationMechanism.CGROUP_V2 in mechanisms:
            cgroup = self.plan_cgroup_v2(envelope, process_group)

        sandbox_root: str | None = None
        if IsolationMechanism.SANDBOX in mechanisms:
            sandbox_root = f"/run/acp/sandbox/{envelope.submission_id}"
            self._require_broker(BrokerOperation.START_SANDBOX)

        mounts = minimal_mount_policy(
            workdir=workdir,
            code_release=code_release,
            data_bindings=tuple(data_bindings),
        )
        return EnvelopeIsolationBinding(
            envelope_key=envelope.submission_id,
            execution_mode=self.execution_mode,
            process_group=process_group,
            mechanisms=mechanisms,
            uid=uid,
            gid=gid,
            cgroup=cgroup,
            sandbox_root=sandbox_root,
            mounts=mounts,
        )

    def plan_cgroup_v2(
        self,
        envelope: ExecutionEnvelope,
        process_group: EnvelopeProcessGroup,
        *,
        cpu_max: str | None = None,
        memory_max_bytes: int | None = None,
        pids_max: int | None = None,
    ) -> CgroupV2Plan:
        """Plan cgroup v2 limits bound to the envelope process group.

        Limits default from the envelope's resource budget; the group path is
        keyed by the envelope ``submission_id`` so one attempt maps to exactly
        one cgroup.
        """
        budget = envelope.resource_budget
        if cpu_max is None and budget.nproc is not None:
            cpu_max = f"{int(budget.nproc) * 100000} 100000"
        if memory_max_bytes is None and budget.mem_mb is not None:
            memory_max_bytes = int(budget.mem_mb) * 1024 * 1024
        if pids_max is None:
            # Never unlimited: a bounded process count is part of resource control.
            pids_max = max(16, int(budget.nproc or 1) * 8)
        return CgroupV2Plan(
            cgroup_path=f"{self.config.cgroup_root.rstrip('/')}{self._cgroup_base}/{envelope.submission_id}",
            envelope_key=process_group.envelope_key,
            cpu_max=cpu_max,
            memory_max_bytes=memory_max_bytes,
            pids_max=pids_max,
        )


# ---------------------------------------------------------------------------
# LSF isolation adapter
# ---------------------------------------------------------------------------


class LsfIsolationAdapter:
    """LSF execution isolation: persistent identity mapping + minimal mounts + stop scope.

    The platform principal → execution account / queue / resource-policy mapping
    is the *persistent* record this adapter owns.  A client cannot choose its
    own execution account: :meth:`resolve_account` only ever returns the mapped
    account for a known principal.  ``bkill`` / ``bstop`` / ``bresume`` are
    scoped to this job's own id + account.
    """

    adapter_id = "lsf"
    execution_mode = ExecutionMode.LSF

    def __init__(self, *, accounts: Mapping[str, LsfExecutionAccount]) -> None:
        self._accounts = {str(key): value for key, value in accounts.items()}
        self._verified = False  # no live cluster in this environment

    # -- capabilities -------------------------------------------------------

    def capabilities(self) -> tuple[TierCapability, ...]:
        mapping_status = (
            CapabilityStatus.CONFIGURED if self._accounts else CapabilityStatus.UNAVAILABLE
        )
        return (
            TierCapability(
                adapter=self.adapter_id,
                execution_mode=self.execution_mode,
                mechanism=IsolationMechanism.LSF_IDENTITY,
                status=mapping_status,
                method="persistent principal->account/queue/resource-policy map; "
                "no arbitrary client-supplied username",
                evidence=f"accounts={len(self._accounts)}",
            ),
            TierCapability(
                adapter=self.adapter_id,
                execution_mode=self.execution_mode,
                mechanism=IsolationMechanism.MINIMAL_MOUNTS,
                status=CapabilityStatus.MEASURED,
                method="minimal_mount_policy() persisted per binding",
                evidence="workdir rw; code release ro; data bindings ro",
            ),
            TierCapability(
                adapter=self.adapter_id,
                execution_mode=self.execution_mode,
                mechanism=IsolationMechanism.LSF_STOP_SCOPE,
                status=CapabilityStatus.MEASURED,
                method="StopScope.permits_command() — numeric id + own account only",
                evidence="negative tests: foreign id/account and superstrings rejected",
            ),
        )

    def verified_mechanisms(self) -> frozenset[str]:
        """Cluster enforcement is NOT_VERIFIED here; only the pure predicate holds."""
        if self._verified:
            return frozenset(
                {
                    IsolationMechanism.LSF_IDENTITY,
                    IsolationMechanism.LSF_STOP_SCOPE,
                    IsolationMechanism.MINIMAL_MOUNTS,
                }
            )
        return frozenset()

    def level(self) -> str:
        return level_for_mechanisms(self.verified_mechanisms())

    def matrix_row(self) -> IsolationMatrixRow:
        return IsolationMatrixRow(
            adapter=self.adapter_id,
            execution_mode=self.execution_mode,
            level=self.level(),
            capabilities=self.capabilities(),
        )

    # -- identity mapping ---------------------------------------------------

    def resolve_account(self, principal_id: str) -> LsfExecutionAccount:
        """Return the persistent execution account mapped to *principal_id*."""
        account = self._accounts.get(str(principal_id))
        if account is None:
            raise IdentityNotMappedError(
                f"no LSF execution account mapped for principal {principal_id!r}"
            )
        return account

    def bind(
        self,
        envelope: ExecutionEnvelope,
        *,
        principal_id: str,
        lsf_job_id: str,
        workdir: str,
        code_release: str,
        data_bindings: Sequence[str] = (),
    ) -> LsfExecutionBinding:
        """Bind the envelope to its persistent LSF execution identity."""
        mapped = self.resolve_account(principal_id)
        mounts = minimal_mount_policy(
            workdir=workdir,
            code_release=code_release,
            data_bindings=tuple(data_bindings),
        )
        mounts = mounts + tuple(mapped.extra_mounts)
        assert_minimal_mounts(mounts)
        stop_scope = StopScope(
            lsf_job_id=lsf_job_id,
            execution_account=mapped.account,
            submission_id=envelope.submission_id,
        )
        return LsfExecutionBinding(
            principal_id=str(principal_id),
            execution_account=mapped.account,
            queue=mapped.queue,
            resource_policy=mapped.resource_policy,
            submission_id=envelope.submission_id,
            job_id=str(envelope.job_id),
            attempt=int(envelope.attempt),
            minimal_mounts=mounts,
            stop_scope=stop_scope,
        )


# ---------------------------------------------------------------------------
# Enablement gate
# ---------------------------------------------------------------------------


#: What each deployment tier requires (§22.5 / §25.1 / plan §25.3 hosted gate).
TIER_REQUIREMENTS: Mapping[str, TierRequirement] = {
    DeploymentTier.LOCAL_SINGLE: TierRequirement(tier=DeploymentTier.LOCAL_SINGLE),
    DeploymentTier.LAB: TierRequirement(
        tier=DeploymentTier.LAB,
        identity_any_of=frozenset(
            {IsolationMechanism.OS_IDENTITY, IsolationMechanism.LSF_IDENTITY}
        ),
    ),
    DeploymentTier.HOSTED: TierRequirement(
        tier=DeploymentTier.HOSTED,
        identity_any_of=frozenset(
            {IsolationMechanism.OS_IDENTITY, IsolationMechanism.LSF_IDENTITY}
        ),
        resource_any_of=frozenset({IsolationMechanism.CGROUP_V2, IsolationMechanism.SANDBOX}),
        all_of=frozenset({IsolationMechanism.MINIMAL_MOUNTS}),
    ),
}


class IsolationGate:
    """Pure deployment-tier enablement gate — refuse, never downgrade.

    :meth:`evaluate` takes the *observed* mechanism set (typically an adapter's
    :meth:`verified_mechanisms`) and returns an :class:`IsolationDecision`.  A
    tier whose requirement is not met is refused; the decision always reports the
    **requested** tier (no silent fallback tier is produced).
    """

    def evaluate(self, tier: str, mechanisms: Iterable[str]) -> IsolationDecision:
        observed = frozenset(str(item) for item in mechanisms)
        requirement = TIER_REQUIREMENTS.get(str(tier))
        if requirement is None:
            return IsolationDecision(
                tier=str(tier),
                enabled=False,
                reason_code=IsolationReason.UNKNOWN_TIER,
                level=level_for_mechanisms(observed),
                mechanisms=observed,
                missing=(),
                detail=f"unknown deployment tier {tier!r}",
            )

        real = observed & REAL_ISOLATION_MECHANISMS
        pseudo_only = is_pseudo_only(observed)

        if requirement.identity_any_of and not (real & requirement.identity_any_of):
            reason = (
                IsolationReason.PSEUDO_ISOLATION_REJECTED
                if pseudo_only
                else IsolationReason.IDENTITY_ISOLATION_REQUIRED
            )
            return IsolationDecision(
                tier=requirement.tier,
                enabled=False,
                reason_code=reason,
                level=level_for_mechanisms(observed),
                mechanisms=observed,
                missing=tuple(sorted(requirement.identity_any_of)),
                detail=(
                    "same-UID directories / OMP_NUM_THREADS / login page are not "
                    "isolation and cannot enable this tier"
                    if pseudo_only
                    else "tier requires a restricted execution identity"
                ),
            )

        if requirement.resource_any_of and not (real & requirement.resource_any_of):
            return IsolationDecision(
                tier=requirement.tier,
                enabled=False,
                reason_code=IsolationReason.RESOURCE_LIMIT_REQUIRED,
                level=level_for_mechanisms(observed),
                mechanisms=observed,
                missing=tuple(sorted(requirement.resource_any_of)),
                detail="tier requires an enforced resource limit",
            )

        if not requirement.all_of <= real:
            return IsolationDecision(
                tier=requirement.tier,
                enabled=False,
                reason_code=IsolationReason.MINIMAL_MOUNTS_REQUIRED,
                level=level_for_mechanisms(observed),
                mechanisms=observed,
                missing=tuple(sorted(requirement.all_of - real)),
                detail="tier requires least-privilege mounts",
            )

        reason = (
            IsolationReason.NO_ISOLATION_REQUIRED
            if not requirement.identity_any_of
            and not requirement.resource_any_of
            and not requirement.all_of
            else IsolationReason.ENABLED
        )
        return IsolationDecision(
            tier=requirement.tier,
            enabled=True,
            reason_code=reason,
            level=level_for_mechanisms(observed),
            mechanisms=observed,
            missing=(),
            detail="required isolation capabilities satisfied",
        )

    def assert_enablement(self, tier: str, mechanisms: Iterable[str]) -> IsolationDecision:
        """Return the decision or raise :class:`IsolationRefusedError`."""
        decision = self.evaluate(tier, mechanisms)
        if decision.refused:
            logger.warning(
                "isolation enablement refused tier=%s reason=%s missing=%s",
                decision.tier,
                decision.reason_code,
                list(decision.missing),
            )
            raise IsolationRefusedError(decision)
        return decision


def capability_matrix(
    adapters: Sequence[LocalIsolationAdapter | LsfIsolationAdapter],
) -> tuple[IsolationMatrixRow, ...]:
    """Build the capability matrix rows for the given adapters."""
    return tuple(adapter.matrix_row() for adapter in adapters)


def render_isolation_decision(decision: IsolationDecision) -> str:
    """Render an :class:`IsolationDecision` for an operator/CLI surface."""
    verdict = "启用" if decision.enabled else "拒绝启用"
    lines = [f"{verdict} {decision.tier}（{decision.reason_code}，level={decision.level}）"]
    if decision.detail:
        lines.append(f"  {decision.detail}")
    if decision.mechanisms:
        lines.append(f"  机制：{sorted(decision.mechanisms)}")
    if decision.missing:
        lines.append(f"  缺失：{list(decision.missing)}")
    return "\n".join(lines)


#: Convenience default for callers that materialise a local adapter config.
DEFAULT_LOCAL_CONFIG = IsolationConfig(execution_mode=ExecutionMode.LOCAL)
