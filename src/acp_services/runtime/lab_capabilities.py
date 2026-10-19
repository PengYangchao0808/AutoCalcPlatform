"""Lab-tier capability detection for init/doctor (B09-5 / plan todo 52).

The Lab deployment tier reuses an institution's **existing** infrastructure
(database / HTTPS certificate / LSF identity mapping / storage quota) instead of
provisioning a new stack.  This module makes that reuse checkable and keeps the
verdict honest: every capability is reported in exactly one of **four distinct
states** and the states are never conflated.

Four-state rule (never conflated)
---------------------------------
* ``PASS`` — the capability was actually verified in this environment (a live
  probe passed, a certificate parsed, a hard quota was reported, an isolation
  gate enabled the tier).
* ``FAIL`` — declared but broken (an unresolved reference, an invalid
  certificate, a requested-but-absent quota, pseudo-"isolation").
* ``NOT_VERIFIED`` — present/declared but the enforcement path was **not**
  exercised here (e.g. an env/file reference resolves but no live probe ran; an
  LSF mapping is configured but no live cluster is attached).  It is *not* a
  pass and never counts toward enablement.
* ``UNAVAILABLE`` — the capability is absent/not implemented (no OIDC adapter
  exists, no backup destination configured, no database reference provided).

Hard rules encoded here
-----------------------
* **Refuse, never downgrade.**  When a capability is not ``PASS`` the consumers
  call :meth:`LabCapabilityReport.require_enabled` (typed refusal) or
  :meth:`LabCapabilityReport.refuse_downgrade` — an authentication-less Lab may
  never fall back to anonymous, a quota-less storage may never run with no
  quota, an HTTPS-less entry may never serve plaintext.
* **Slurm is demand-only and never blocks Lab.**  ``Slurm`` is reported in its
  own section with ``required=False`` / ``blocks_lab=False``; a missing Slurm
  never changes the Lab enablement verdict.
* **Identity is declared truthfully.**  The OIDC row is derived from
  :data:`acp_services.workspace.identity_lifecycle.OIDC_SUPPORTED` (currently
  ``False``) and can therefore never be ``PASS``; the Key-only scope is stated
  where applicable.  This mirrors
  ``docs/reference/next-change/ACP_Services_Identity_Support_Scope.md``.
* **Isolation is consumed, not re-derived.**  The ``isolation`` row reuses the
  todo-50 capability matrix / :class:`~acp_services.runtime.isolation.IsolationGate`
  (same-UID directories / ``OMP_NUM_THREADS`` / login page are pseudo-isolation).

Dependency direction
--------------------
``src/acp_services/**`` must never import ``acp``.  This module imports only the
stdlib plus sibling Services modules (contracts / isolation / identity
lifecycle); the acp-side bridge lives in ``acp.init_wizard.lab_doctor``.

Author: ACP Services (B lane, todo 52).
"""

from __future__ import annotations

import logging
import os
import ssl
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp_services.contracts.access import DeploymentTier, ExecutionMode
from acp_services.runtime.isolation import (
    PSEUDO_MECHANISMS,
    REAL_ISOLATION_MECHANISMS,
    IsolationConfig,
    IsolationDecision,
    IsolationGate,
    IsolationMechanism,
    IsolationReason,
    LocalIsolationAdapter,
    LsfExecutionAccount,
    LsfIsolationAdapter,
    OsIdentity,
)
from acp_services.workspace.identity_lifecycle import (
    OIDC_SUPPORTED,
    OIDC_UNAVAILABLE_REASON,
    IdentitySupportScope,
    identity_support_scope,
)

logger = logging.getLogger(__name__)

__all__ = [
    # vocabularies
    "LabCapabilityName",
    "LabCapabilityState",
    "LabDowngradeKind",
    "LabRefusalReason",
    "ReferenceState",
    # value objects
    "LabCapability",
    "LabEnvironment",
    "LabProbes",
    "LabCapabilityReport",
    # errors
    "LabCapabilityError",
    "LabCapabilityRefusedError",
    # helpers / builders
    "reference_target",
    "resolve_reference",
    "build_lab_capability_report",
    "render_lab_capability_report",
]


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------


class LabCapabilityName(str):
    """The Lab capability dimensions an init/doctor report covers."""

    AUTHENTICATION = "authentication"
    DATABASE = "database"
    HTTPS = "https"
    LSF_MAPPING = "lsf-mapping"
    STORAGE_QUOTA = "storage-quota"
    ISOLATION = "isolation"
    KEYS = "keys"
    BACKUP = "backup"
    OIDC = "identity-oidc"

    ALL = frozenset(
        {
            AUTHENTICATION,
            DATABASE,
            HTTPS,
            LSF_MAPPING,
            STORAGE_QUOTA,
            ISOLATION,
            KEYS,
            BACKUP,
            OIDC,
        }
    )


class LabCapabilityState(str):
    """Honest per-capability status — four states, never conflated."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_VERIFIED = "NOT_VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"
    ALL = frozenset({PASS, FAIL, NOT_VERIFIED, UNAVAILABLE})


class LabDowngradeKind(str):
    """Forbidden substitutes a capability-less Lab must refuse."""

    ANONYMOUS = "anonymous"
    NO_QUOTA = "no-quota"
    PLAINTEXT = "plaintext"
    ALL = frozenset({ANONYMOUS, NO_QUOTA, PLAINTEXT})


class LabRefusalReason(str):
    """Typed reason codes for a capability refusal."""

    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    CAPABILITY_FAILED = "capability_failed"
    CAPABILITY_NOT_VERIFIED = "capability_not_verified"
    ANONYMOUS_DOWNGRADE_REFUSED = "anonymous_downgrade_refused"
    NO_QUOTA_DOWNGRADE_REFUSED = "no_quota_downgrade_refused"
    PLAINTEXT_DOWNGRADE_REFUSED = "plaintext_downgrade_refused"
    UNKNOWN_CAPABILITY = "unknown_capability"
    UNKNOWN_DOWNGRADE = "unknown_downgrade"
    ALL = frozenset(
        {
            CAPABILITY_UNAVAILABLE,
            CAPABILITY_FAILED,
            CAPABILITY_NOT_VERIFIED,
            ANONYMOUS_DOWNGRADE_REFUSED,
            NO_QUOTA_DOWNGRADE_REFUSED,
            PLAINTEXT_DOWNGRADE_REFUSED,
            UNKNOWN_CAPABILITY,
            UNKNOWN_DOWNGRADE,
        }
    )


class ReferenceState(str):
    """Outcome of resolving a ``source:ref`` configuration reference."""

    RESOLVED = "resolved"
    UNVERIFIED = "unverified"  # provider-managed: declared, cannot be probed here
    UNRESOLVED = "unresolved"
    MALFORMED = "malformed"
    ALL = frozenset({RESOLVED, UNVERIFIED, UNRESOLVED, MALFORMED})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LabCapabilityError(RuntimeError):
    """Base error for Lab capability contract violations."""


class LabCapabilityRefusedError(LabCapabilityError):
    """Enabling a Lab capability (or a downgrade) was refused.

    Carries the typed :attr:`reason_code`, the offending :attr:`capability` and
    its observed :attr:`state`; the refusal never grants a substitute
    capability.
    """

    def __init__(
        self,
        capability: str,
        state: str,
        reason_code: str,
        detail: str = "",
    ) -> None:
        self.capability = str(capability)
        self.state = str(state)
        self.reason_code = str(reason_code)
        self.detail = detail or (
            f"lab capability {self.capability!r} is {self.state}: {self.reason_code}"
        )
        super().__init__(self.detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "state": self.state,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Reference resolution (env / file / provider)
# ---------------------------------------------------------------------------


def reference_target(reference: str) -> tuple[str, str] | None:
    """Split ``source:ref`` into ``(source, ref)`` or ``None`` if malformed."""
    text = str(reference or "").strip()
    if not text or ":" not in text:
        return None
    source, _, ref = text.partition(":")
    source = source.strip().lower()
    ref = ref.strip()
    if not source or not ref:
        return None
    return source, ref


def resolve_reference(
    reference: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """Resolve a secret/resource reference without ever reading a secret value.

    ``env:NAME`` resolves when the environment variable is set; ``file:PATH``
    resolves when the file exists; ``provider:KEY`` is *declared* but cannot be
    probed here (``UNVERIFIED``).  A bare value (no scheme) is ``MALFORMED``.
    """
    target = reference_target(reference or "")
    if target is None:
        return ReferenceState.MALFORMED, f"reference {reference!r} is not source:ref"
    source, ref = target
    environment = os.environ if env is None else env
    if source == "env":
        if environment.get(ref):
            return ReferenceState.RESOLVED, f"env var {ref} is set"
        return ReferenceState.UNRESOLVED, f"env var {ref} is not set"
    if source == "file":
        if Path(ref).exists():
            return ReferenceState.RESOLVED, f"path {ref} exists"
        return ReferenceState.UNRESOLVED, f"path {ref} is not present"
    if source == "provider":
        return ReferenceState.UNVERIFIED, f"provider-managed reference {ref} (not examined here)"
    return ReferenceState.MALFORMED, f"unknown reference source {source!r}"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabCapability:
    """One row of the Lab capability report."""

    name: str
    state: str
    detail: str = ""
    required: bool = False
    reused: bool = False
    dependency: str | None = None
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.name not in LabCapabilityName.ALL:
            raise LabCapabilityError(f"unknown lab capability {self.name!r}")
        if self.state not in LabCapabilityState.ALL:
            raise LabCapabilityError(f"unknown lab capability state {self.state!r}")

    @property
    def enabled(self) -> bool:
        return self.state == LabCapabilityState.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "detail": self.detail,
            "required": self.required,
            "reused": self.reused,
            "dependency": self.dependency,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class LabProbes:
    """Optional live probes; ``None`` means "cannot exercise here".

    A missing probe is what keeps a declared capability at ``NOT_VERIFIED``
    instead of an over-claimed ``PASS``.
    """

    database: Callable[[str], bool] | None = None
    lsf: Callable[[], bool] | None = None
    backup: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class LabEnvironment:
    """The observed Lab environment handed to the detector.

    ``execution_mode`` is a recorded axis (local/lsf/slurm/podman) that never
    grants identity by itself — it is passed through to the identity support
    scope unchanged.
    """

    execution_mode: str = ExecutionMode.LOCAL
    database_ref: str | None = None
    https_cert_ref: str | None = None
    https_requested: bool = False
    storage_quota_bytes: int | None = None
    storage_quota_source: str = ""
    quota_requested: bool = False
    identity_map: Mapping[str, OsIdentity] = field(default_factory=dict)
    lsf_accounts: Mapping[str, LsfExecutionAccount] = field(default_factory=dict)
    secret_refs: tuple[tuple[str, str], ...] = ()
    backup_ref: str | None = None
    isolation_mechanisms: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.execution_mode not in ExecutionMode.ALL:
            raise LabCapabilityError(f"unknown execution mode {self.execution_mode!r}")
        if self.storage_quota_bytes is not None and int(self.storage_quota_bytes) < 0:
            raise LabCapabilityError("storage_quota_bytes must be non-negative or None")


@dataclass(frozen=True)
class LabCapabilityReport:
    """The doctor-facing Lab capability report (four states kept apart)."""

    tier: str
    execution_mode: str
    capabilities: tuple[LabCapability, ...]
    identity: IdentitySupportScope
    slurm: Mapping[str, Any] = field(default_factory=dict)

    # -- lookups ------------------------------------------------------------

    def get(self, name: str) -> LabCapability | None:
        for capability in self.capabilities:
            if capability.name == str(name):
                return capability
        return None

    def state_of(self, name: str) -> str:
        capability = self.get(name)
        return capability.state if capability is not None else LabCapabilityState.UNAVAILABLE

    def by_state(self, state: str) -> tuple[LabCapability, ...]:
        return tuple(cap for cap in self.capabilities if cap.state == str(state))

    # -- aggregate verdicts -------------------------------------------------

    @property
    def enabled(self) -> tuple[str, ...]:
        """Capabilities in the ``PASS`` state (the only enabled ones)."""
        return tuple(sorted(cap.name for cap in self.capabilities if cap.enabled))

    @property
    def refused(self) -> tuple[str, ...]:
        """Capabilities that are not ``PASS`` (enabling them is refused)."""
        return tuple(sorted(cap.name for cap in self.capabilities if not cap.enabled))

    @property
    def unverified(self) -> tuple[str, ...]:
        return tuple(sorted(c.name for c in self.by_state(LabCapabilityState.NOT_VERIFIED)))

    @property
    def unavailable(self) -> tuple[str, ...]:
        return tuple(sorted(c.name for c in self.by_state(LabCapabilityState.UNAVAILABLE)))

    @property
    def integrity_ok(self) -> bool:
        """True when no capability is in the ``FAIL`` state."""
        return not self.by_state(LabCapabilityState.FAIL)

    @property
    def ok(self) -> bool:
        """True only when every *required* capability is ``PASS``.

        A ``NOT_VERIFIED``/``UNAVAILABLE`` required capability keeps the Lab
        environment from being certified — the report never shows an unverified
        combination as available.
        """
        required = [cap for cap in self.capabilities if cap.required]
        return bool(required) and all(cap.enabled for cap in required)

    # -- refusal gates ------------------------------------------------------

    def require_enabled(self, name: str) -> LabCapability:
        """Return the capability or raise a typed refusal.

        Raises:
            LabCapabilityRefusedError: with ``UNKNOWN_CAPABILITY`` when *name*
                is not a Lab capability, else with a reason derived from the
                observed state.
        """
        capability = self.get(name)
        if capability is None:
            raise LabCapabilityRefusedError(
                str(name),
                LabCapabilityState.UNAVAILABLE,
                LabRefusalReason.UNKNOWN_CAPABILITY,
                f"unknown lab capability {name!r}",
            )
        if capability.enabled:
            return capability
        reason = {
            LabCapabilityState.FAIL: LabRefusalReason.CAPABILITY_FAILED,
            LabCapabilityState.NOT_VERIFIED: LabRefusalReason.CAPABILITY_NOT_VERIFIED,
            LabCapabilityState.UNAVAILABLE: LabRefusalReason.CAPABILITY_UNAVAILABLE,
        }.get(capability.state, LabRefusalReason.CAPABILITY_UNAVAILABLE)
        raise LabCapabilityRefusedError(
            capability.name,
            capability.state,
            reason,
            f"lab capability {capability.name!r} is {capability.state}: {capability.detail}",
        )

    def refuse_downgrade(self, kind: str) -> None:
        """Refuse a forbidden substitute when its capability is not enabled.

        ``anonymous`` requires the authentication capability; ``no-quota``
        requires the storage quota capability; ``plaintext`` requires the HTTPS
        capability.  Each raises a typed refusal instead of silently degrading.
        """
        downgrade = str(kind)
        if downgrade not in LabDowngradeKind.ALL:
            raise LabCapabilityRefusedError(
                "",
                LabCapabilityState.UNAVAILABLE,
                LabRefusalReason.UNKNOWN_DOWNGRADE,
                f"unknown downgrade kind {kind!r}",
            )
        target, reason, detail = {
            LabDowngradeKind.ANONYMOUS: (
                LabCapabilityName.AUTHENTICATION,
                LabRefusalReason.ANONYMOUS_DOWNGRADE_REFUSED,
                "refusing anonymous fallback: authentication capability is not enabled",
            ),
            LabDowngradeKind.NO_QUOTA: (
                LabCapabilityName.STORAGE_QUOTA,
                LabRefusalReason.NO_QUOTA_DOWNGRADE_REFUSED,
                "refusing no-quota operation: no hard storage quota is enabled",
            ),
            LabDowngradeKind.PLAINTEXT: (
                LabCapabilityName.HTTPS,
                LabRefusalReason.PLAINTEXT_DOWNGRADE_REFUSED,
                "refusing plaintext fallback: HTTPS capability is not enabled",
            ),
        }[downgrade]
        capability = self.get(target)
        if capability is None or not capability.enabled:
            raise LabCapabilityRefusedError(
                target,
                capability.state if capability is not None else LabCapabilityState.UNAVAILABLE,
                reason,
                detail,
            )

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Doctor report schema (stable keys; four states never conflated)."""
        return {
            "tier": self.tier,
            "execution_mode": self.execution_mode,
            "ok": self.ok,
            "integrity_ok": self.integrity_ok,
            "capabilities": [cap.to_dict() for cap in self.capabilities],
            "states": {
                state: [cap.name for cap in self.by_state(state)]
                for state in (
                    LabCapabilityState.PASS,
                    LabCapabilityState.FAIL,
                    LabCapabilityState.NOT_VERIFIED,
                    LabCapabilityState.UNAVAILABLE,
                )
            },
            "enabled": list(self.enabled),
            "refused": list(self.refused),
            "identity": self.identity.to_dict(),
            "oidc": {
                "supported": OIDC_SUPPORTED,
                "state": self.state_of(LabCapabilityName.OIDC),
                "reason": OIDC_UNAVAILABLE_REASON,
            },
            "slurm": dict(self.slurm),
        }


# ---------------------------------------------------------------------------
# Individual capability detectors
# ---------------------------------------------------------------------------


def _isolation_decision(env: LabEnvironment, probes: LabProbes) -> IsolationDecision:
    """Reuse todo-50's adapters + gate to decide Lab isolation enablement."""
    observed: set[str] = set()
    declared: set[str] = set()

    if env.execution_mode == ExecutionMode.LSF:
        adapter = LsfIsolationAdapter(accounts=env.lsf_accounts)
        observed |= set(adapter.verified_mechanisms())
        declared |= {IsolationMechanism.LSF_IDENTITY, IsolationMechanism.MINIMAL_MOUNTS}
        if env.isolation_mechanisms:
            declared |= set(env.isolation_mechanisms)
        if probes.lsf is not None and probes.lsf():
            observed |= {IsolationMechanism.LSF_IDENTITY, IsolationMechanism.MINIMAL_MOUNTS}
    else:
        mechanisms = (
            tuple(env.isolation_mechanisms)
            if env.isolation_mechanisms
            else (IsolationMechanism.OS_IDENTITY, IsolationMechanism.MINIMAL_MOUNTS)
        )
        declared |= set(mechanisms)
        adapter = LocalIsolationAdapter(
            IsolationConfig(
                execution_mode=env.execution_mode,
                mechanisms=mechanisms,
                identity_map=dict(env.identity_map),
            )
        )
        observed |= set(adapter.verified_mechanisms())

    pseudo = declared & PSEUDO_MECHANISMS
    # §22.5: a pseudo-only declaration must reach `pseudo_isolation_rejected`;
    # passing the always-measured minimal-mounts would mask it.
    declared_real = declared & REAL_ISOLATION_MECHANISMS
    if declared and not declared_real:
        return IsolationGate().evaluate(DeploymentTier.LAB, pseudo)
    return IsolationGate().evaluate(DeploymentTier.LAB, observed | pseudo)


def _isolation_capability(decision: IsolationDecision) -> LabCapability:
    if decision.enabled:
        return LabCapability(
            LabCapabilityName.ISOLATION,
            LabCapabilityState.PASS,
            f"identity-isolating Lab execution ({decision.reason_code})",
            required=True,
            evidence=f"level={decision.level}",
        )
    if decision.reason_code in {IsolationReason.PSEUDO_ISOLATION_REJECTED}:
        return LabCapability(
            LabCapabilityName.ISOLATION,
            LabCapabilityState.FAIL,
            "same-UID directories / OMP_NUM_THREADS / login page are not isolation",
            required=True,
            evidence=decision.detail,
        )
    if decision.reason_code == IsolationReason.UNKNOWN_TIER:
        return LabCapability(
            LabCapabilityName.ISOLATION,
            LabCapabilityState.FAIL,
            decision.detail,
            required=True,
        )
    return LabCapability(
        LabCapabilityName.ISOLATION,
        LabCapabilityState.NOT_VERIFIED,
        f"identity isolation not exercised here (missing={list(decision.missing)})",
        required=True,
        evidence=decision.reason_code,
    )


def _authentication_capability(
    identity: IdentitySupportScope, isolation: LabCapability
) -> LabCapability:
    if not identity.enabled:
        return LabCapability(
            LabCapabilityName.AUTHENTICATION,
            LabCapabilityState.UNAVAILABLE,
            identity.detail or "identity capability not available for this tier",
            required=True,
        )
    if not isolation.enabled:
        return LabCapability(
            LabCapabilityName.AUTHENTICATION,
            LabCapabilityState.NOT_VERIFIED,
            "identity enabled but its isolation dependency is not verified",
            required=True,
            dependency=LabCapabilityName.ISOLATION,
        )
    return LabCapability(
        LabCapabilityName.AUTHENTICATION,
        LabCapabilityState.PASS,
        f"{identity.mode}; account/session scope per identity support scope",
        required=True,
        dependency=LabCapabilityName.ISOLATION,
    )


def _database_capability(env: LabEnvironment, probes: LabProbes) -> LabCapability:
    if not env.database_ref:
        return LabCapability(
            LabCapabilityName.DATABASE,
            LabCapabilityState.UNAVAILABLE,
            "no existing database reference provided (institution DB not reused)",
            required=True,
        )
    state, detail = resolve_reference(env.database_ref, env=env.env)
    if state in {ReferenceState.MALFORMED, ReferenceState.UNRESOLVED}:
        return LabCapability(
            LabCapabilityName.DATABASE,
            LabCapabilityState.FAIL,
            f"database reference unresolved: {detail}",
            required=True,
            reused=True,
            evidence=env.database_ref,
        )
    if state == ReferenceState.UNVERIFIED:
        return LabCapability(
            LabCapabilityName.DATABASE,
            LabCapabilityState.NOT_VERIFIED,
            f"existing institution database reused ({detail})",
            required=True,
            reused=True,
            evidence=env.database_ref,
        )
    if probes.database is None:
        return LabCapability(
            LabCapabilityName.DATABASE,
            LabCapabilityState.NOT_VERIFIED,
            "existing institution database reused; reference resolves but no live probe ran",
            required=True,
            reused=True,
            evidence=env.database_ref,
        )
    ok = bool(probes.database(str(env.database_ref)))
    return LabCapability(
        LabCapabilityName.DATABASE,
        LabCapabilityState.PASS if ok else LabCapabilityState.FAIL,
        "existing institution database reused; live probe passed"
        if ok
        else "existing institution database live probe failed",
        required=True,
        reused=True,
        evidence=env.database_ref,
    )


def _https_capability(env: LabEnvironment) -> LabCapability:
    if not env.https_cert_ref:
        if env.https_requested:
            return LabCapability(
                LabCapabilityName.HTTPS,
                LabCapabilityState.FAIL,
                "HTTPS requested but no certificate reference is configured",
            )
        return LabCapability(
            LabCapabilityName.HTTPS,
            LabCapabilityState.UNAVAILABLE,
            "no HTTPS certificate configured",
        )
    target = reference_target(env.https_cert_ref)
    state, detail = resolve_reference(env.https_cert_ref, env=env.env)
    if state in {ReferenceState.MALFORMED, ReferenceState.UNRESOLVED}:
        return LabCapability(
            LabCapabilityName.HTTPS,
            LabCapabilityState.FAIL,
            f"HTTPS certificate reference unresolved: {detail}",
            evidence=env.https_cert_ref,
        )
    if state == ReferenceState.UNVERIFIED:
        return LabCapability(
            LabCapabilityName.HTTPS,
            LabCapabilityState.NOT_VERIFIED,
            f"provider-managed certificate declared ({detail})",
            evidence=env.https_cert_ref,
        )
    assert target is not None  # RESOLVED implies a parsed source:ref
    source, ref = target
    if source != "file":
        return LabCapability(
            LabCapabilityName.HTTPS,
            LabCapabilityState.NOT_VERIFIED,
            f"certificate reference {env.https_cert_ref!r} declared; not examined here",
            evidence=env.https_cert_ref,
        )
    try:
        pem = Path(ref).read_text(encoding="utf-8")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=pem)
    except (OSError, ValueError, ssl.SSLError) as exc:
        return LabCapability(
            LabCapabilityName.HTTPS,
            LabCapabilityState.FAIL,
            f"certificate {ref} is not a parseable PEM certificate: {exc}",
            evidence=env.https_cert_ref,
        )
    return LabCapability(
        LabCapabilityName.HTTPS,
        LabCapabilityState.PASS,
        f"certificate {ref} parsed as a valid PEM certificate",
        evidence=env.https_cert_ref,
    )


def _lsf_capability(env: LabEnvironment, probes: LabProbes) -> LabCapability:
    lsf_mode = env.execution_mode == ExecutionMode.LSF
    if not env.lsf_accounts:
        if lsf_mode:
            return LabCapability(
                LabCapabilityName.LSF_MAPPING,
                LabCapabilityState.FAIL,
                "LSF execution selected but no persistent principal->account mapping",
                required=True,
            )
        return LabCapability(
            LabCapabilityName.LSF_MAPPING,
            LabCapabilityState.UNAVAILABLE,
            "no LSF mapping configured (local execution)",
        )
    if probes.lsf is not None and probes.lsf():
        return LabCapability(
            LabCapabilityName.LSF_MAPPING,
            LabCapabilityState.PASS,
            "persistent mapping present and live cluster probe passed",
            required=lsf_mode,
            reused=True,
            evidence=f"accounts={len(env.lsf_accounts)}",
        )
    return LabCapability(
        LabCapabilityName.LSF_MAPPING,
        LabCapabilityState.NOT_VERIFIED,
        "persistent principal->account mapping configured; live cluster not exercised",
        required=lsf_mode,
        reused=True,
        evidence=f"accounts={len(env.lsf_accounts)}",
    )


def _storage_quota_capability(env: LabEnvironment) -> LabCapability:
    if env.storage_quota_bytes is None:
        if env.quota_requested:
            return LabCapability(
                LabCapabilityName.STORAGE_QUOTA,
                LabCapabilityState.FAIL,
                "quota requested but no hard limit (statvfs/quota) is reported",
                required=True,
            )
        return LabCapability(
            LabCapabilityName.STORAGE_QUOTA,
            LabCapabilityState.UNAVAILABLE,
            "no storage hard limit configured",
        )
    source = env.storage_quota_source or "caller"
    return LabCapability(
        LabCapabilityName.STORAGE_QUOTA,
        LabCapabilityState.PASS,
        f"hard limit {int(env.storage_quota_bytes)} bytes from {source}",
        required=env.quota_requested,
        evidence=env.storage_quota_source,
    )


def _keys_capability(env: LabEnvironment) -> LabCapability:
    if not env.secret_refs:
        return LabCapability(
            LabCapabilityName.KEYS,
            LabCapabilityState.NOT_VERIFIED,
            "no credential references configured",
        )
    unresolved: list[str] = []
    provider_managed = False
    for name, reference in env.secret_refs:
        state, _ = resolve_reference(reference, env=env.env)
        if state in {ReferenceState.MALFORMED, ReferenceState.UNRESOLVED}:
            unresolved.append(str(name))
        elif state == ReferenceState.UNVERIFIED:
            provider_managed = True
    if unresolved:
        return LabCapability(
            LabCapabilityName.KEYS,
            LabCapabilityState.FAIL,
            f"unresolved credential references: {'、'.join(sorted(unresolved))}",
        )
    if provider_managed:
        return LabCapability(
            LabCapabilityName.KEYS,
            LabCapabilityState.NOT_VERIFIED,
            "provider-managed credentials declared; not examined here",
        )
    return LabCapability(
        LabCapabilityName.KEYS,
        LabCapabilityState.PASS,
        f"{len(env.secret_refs)} credential reference(s) resolve",
        evidence=f"refs={len(env.secret_refs)}",
    )


def _backup_capability(env: LabEnvironment, probes: LabProbes) -> LabCapability:
    if not env.backup_ref:
        return LabCapability(
            LabCapabilityName.BACKUP,
            LabCapabilityState.UNAVAILABLE,
            "no backup destination configured",
        )
    state, detail = resolve_reference(env.backup_ref, env=env.env)
    if state in {ReferenceState.MALFORMED, ReferenceState.UNRESOLVED}:
        return LabCapability(
            LabCapabilityName.BACKUP,
            LabCapabilityState.FAIL,
            f"backup destination reference unresolved: {detail}",
            evidence=env.backup_ref,
        )
    if state == ReferenceState.UNVERIFIED:
        return LabCapability(
            LabCapabilityName.BACKUP,
            LabCapabilityState.NOT_VERIFIED,
            f"provider-managed backup destination declared ({detail})",
            evidence=env.backup_ref,
        )
    if probes.backup is None:
        return LabCapability(
            LabCapabilityName.BACKUP,
            LabCapabilityState.NOT_VERIFIED,
            "backup destination declared; restore/verify not exercised",
            evidence=env.backup_ref,
        )
    ok = bool(probes.backup(str(env.backup_ref)))
    return LabCapability(
        LabCapabilityName.BACKUP,
        LabCapabilityState.PASS if ok else LabCapabilityState.FAIL,
        "backup destination probe passed" if ok else "backup destination probe failed",
        evidence=env.backup_ref,
    )


def _oidc_capability() -> LabCapability:
    state = LabCapabilityState.NOT_VERIFIED if OIDC_SUPPORTED else LabCapabilityState.UNAVAILABLE
    return LabCapability(
        LabCapabilityName.OIDC,
        state,
        OIDC_UNAVAILABLE_REASON,
    )


def _slurm_section(env: LabEnvironment, requested: bool) -> dict[str, Any]:
    """Slurm is demand-only: it never becomes a Lab requirement."""
    detected = "not-verified"
    if requested:
        detected = "requested-unavailable"
    return {
        "requested": bool(requested),
        "required": False,
        "blocks_lab": False,
        "detected": detected,
        "execution_mode": env.execution_mode,
    }


# ---------------------------------------------------------------------------
# Report builder / renderer
# ---------------------------------------------------------------------------


def build_lab_capability_report(
    env: LabEnvironment,
    *,
    tier: str = DeploymentTier.LAB,
    capabilities: Sequence[str] = (),
    probes: LabProbes | None = None,
    slurm_requested: bool = False,
) -> LabCapabilityReport:
    """Collect the four-state Lab capability report for *env*.

    Args:
        env: Observed Lab environment (reused refs / identity map / quotas).
        tier: Deployment tier (Lab by default; recorded for the identity scope).
        capabilities: Available capability names (``authentication`` /
            ``isolation``) — usually derived from the selected components.
        probes: Optional live probes; a missing probe keeps a declared
            capability at ``NOT_VERIFIED`` rather than ``PASS``.
        slurm_requested: Whether Slurm was explicitly requested; it never blocks
            Lab enablement.

    Returns:
        A :class:`LabCapabilityReport` with all four states kept apart.
    """
    resolved_probes = probes or LabProbes()
    identity = identity_support_scope(tier, capabilities, execution_mode=env.execution_mode)
    isolation = _isolation_capability(_isolation_decision(env, resolved_probes))

    rows = (
        isolation,
        _authentication_capability(identity, isolation),
        _database_capability(env, resolved_probes),
        _https_capability(env),
        _lsf_capability(env, resolved_probes),
        _storage_quota_capability(env),
        _keys_capability(env),
        _backup_capability(env, resolved_probes),
        _oidc_capability(),
    )
    return LabCapabilityReport(
        tier=str(tier),
        execution_mode=env.execution_mode,
        capabilities=tuple(rows),
        identity=identity,
        slurm=_slurm_section(env, slurm_requested),
    )


def render_lab_capability_report(report: LabCapabilityReport) -> str:
    """Render the Lab report with the four states in separate sections."""
    lines = [
        "Lab 能力报告（PASS / FAIL / NOT_VERIFIED / UNAVAILABLE 分列）",
        f"  tier={report.tier} · execution_mode={report.execution_mode} · "
        f"ok={'是' if report.ok else '否'}",
    ]
    for state in (
        LabCapabilityState.PASS,
        LabCapabilityState.FAIL,
        LabCapabilityState.NOT_VERIFIED,
        LabCapabilityState.UNAVAILABLE,
    ):
        entries = report.by_state(state)
        lines.append(f"  {state}（{len(entries)}）：")
        if not entries:
            lines.append("    - （无）")
        for capability in entries:
            flag = " [必需]" if capability.required else ""
            detail = f" — {capability.detail}" if capability.detail else ""
            lines.append(f"    - {capability.name}{flag}{detail}")
    lines.append(
        "  Slurm：仅按需求，required=False，不阻塞 Lab"
        f"（requested={report.slurm.get('requested', False)}）"
    )
    return "\n".join(lines)
