"""Selection publication contract (round-5 PR-06).

Versioned vocabulary for an *authorized* PES selection publication: who may
publish, from which source attempt/profile, with which roles/methods/resources
and how many candidates/Batch jobs/retries may be consumed.  The authorization
record is opaque data stored in the existing Data research-resource store; the
policy checks live in ``acp_services.runtime.selection_authorization``.

Agents can never create, extend or renew their own authorization: creation
takes an explicit ``is_admin`` flag and the runtime refuses everything else.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SELECTION_ACTIONS",
    "SELECTION_AUTHORIZATION_SCHEMA",
    "SELECTION_CONTRACT_VERSION",
    "SelectionAuthorization",
    "SelectionDecision",
    "SelectionLimits",
    "SelectionPublicationRequest",
    "SelectionSourceBinding",
]

SELECTION_CONTRACT_VERSION = "acp2.selection.v1"
SELECTION_AUTHORIZATION_SCHEMA = "acp2.selection.authorization.v1"
SELECTION_ACTIONS = frozenset({"publish_pes_selection", "batch_consume"})
SELECTION_SELECTORS = frozenset({"manual", "manual_frame", "agent"})


def _text(payload: Mapping[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key)
    return default if value is None else str(value)


def _int_map(payload: Mapping[str, Any], key: str) -> dict[str, int]:
    raw = payload.get(key)
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


@dataclass(frozen=True, slots=True)
class SelectionSourceBinding:
    """The exact source execution a selection was made on."""

    job_id: str
    attempt: int
    profile_digest: str = ""
    geometry_digest: str = ""
    selection_revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "attempt": int(self.attempt),
            "profile_digest": self.profile_digest,
            "geometry_digest": self.geometry_digest,
            "selection_revision": int(self.selection_revision),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> SelectionSourceBinding:
        data = payload or {}
        return cls(
            job_id=_text(data, "job_id"),
            attempt=int(data.get("attempt") or 0),
            profile_digest=_text(data, "profile_digest"),
            geometry_digest=_text(data, "geometry_digest"),
            selection_revision=int(data.get("selection_revision") or 0),
        )


@dataclass(frozen=True, slots=True)
class SelectionLimits:
    """Persistent consumption limits (candidates / Batch jobs / retries)."""

    max_candidates: int = 0
    max_batch_jobs: int = 0
    max_retries: int = 0
    used_candidates: int = 0
    used_batch_jobs: int = 0
    used_retries: int = 0

    def remaining(self, action: str) -> int:
        if action == "publish_pes_selection":
            return max(0, self.max_candidates - self.used_candidates)
        if action == "batch_consume":
            return max(0, self.max_batch_jobs - self.used_batch_jobs)
        return max(0, self.max_retries - self.used_retries)

    def to_dict(self) -> dict[str, int]:
        return {
            "max_candidates": self.max_candidates,
            "max_batch_jobs": self.max_batch_jobs,
            "max_retries": self.max_retries,
            "used_candidates": self.used_candidates,
            "used_batch_jobs": self.used_batch_jobs,
            "used_retries": self.used_retries,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> SelectionLimits:
        data = payload or {}
        return cls(
            max_candidates=int(data.get("max_candidates") or 0),
            max_batch_jobs=int(data.get("max_batch_jobs") or 0),
            max_retries=int(data.get("max_retries") or 0),
            used_candidates=int(data.get("used_candidates") or 0),
            used_batch_jobs=int(data.get("used_batch_jobs") or 0),
            used_retries=int(data.get("used_retries") or 0),
        )


@dataclass(frozen=True, slots=True)
class SelectionAuthorization:
    """One bounded authorization record (persisted as opaque JSON)."""

    authorization_id: str
    owner_principal_id: str
    owner_project_id: str
    policy_revision: str
    valid_until: str
    actions: tuple[str, ...]
    source_job_id: str
    allowed_attempts: tuple[int, ...]
    workflow: str = "PESsearch"
    roles: tuple[str, ...] = ("TS", "INT")
    methods: tuple[str, ...] = ()
    nproc_max: int = 0
    limits: SelectionLimits = field(default_factory=SelectionLimits)
    revoked: bool = False
    actions_log: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SELECTION_AUTHORIZATION_SCHEMA,
            "contract_version": SELECTION_CONTRACT_VERSION,
            "authorization_id": self.authorization_id,
            "owner_principal_id": self.owner_principal_id,
            "owner_project_id": self.owner_project_id,
            # store-level envelope fields (the Data store validates these)
            "project_id": self.owner_project_id,
            "principal_id": self.owner_principal_id,
            "policy_revision": self.policy_revision,
            "valid_until": self.valid_until,
            "actions": list(self.actions),
            "source_job_id": self.source_job_id,
            "allowed_attempts": [int(item) for item in self.allowed_attempts],
            "workflow": self.workflow,
            "roles": list(self.roles),
            "methods": list(self.methods),
            "nproc_max": self.nproc_max,
            "limits": self.limits.to_dict(),
            "revoked": bool(self.revoked),
            "actions_log": [dict(item) for item in self.actions_log],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> SelectionAuthorization:
        data = payload or {}
        raw_log = data.get("actions_log")
        log: list[dict[str, Any]] = []
        if isinstance(raw_log, list):
            log = [dict(item) for item in raw_log if isinstance(item, Mapping)]
        return cls(
            authorization_id=_text(data, "authorization_id"),
            owner_principal_id=_text(data, "owner_principal_id"),
            owner_project_id=_text(data, "owner_project_id"),
            policy_revision=_text(data, "policy_revision"),
            valid_until=_text(data, "valid_until"),
            actions=tuple(str(item) for item in data.get("actions") or ()),
            source_job_id=_text(data, "source_job_id"),
            allowed_attempts=tuple(int(item) for item in data.get("allowed_attempts") or ()),
            workflow=_text(data, "workflow", "PESsearch"),
            roles=tuple(str(item) for item in data.get("roles") or ("TS", "INT")),
            methods=tuple(str(item) for item in data.get("methods") or ()),
            nproc_max=int(data.get("nproc_max") or 0),
            limits=SelectionLimits.from_dict(data.get("limits")),
            revoked=bool(data.get("revoked", False)),
            actions_log=tuple(log),
        )


@dataclass(frozen=True, slots=True)
class SelectionPublicationRequest:
    """One agent publish request (validated against its authorization)."""

    authorization_id: str
    selector: str
    actor_principal_id: str
    project_id: str
    source: SelectionSourceBinding
    workflow: str
    candidates: tuple[Mapping[str, Any], ...] = ()
    method: str = ""
    nproc: int = 0
    idempotency_key: str | None = None
    expected_revision: int | None = None

    def roles(self) -> tuple[str, ...]:
        roles: list[str] = []
        for item in self.candidates:
            role = str(item.get("role") or "")
            if role:
                roles.append(role)
        return tuple(roles)


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    """The authorization decision bound to one publication."""

    allowed: bool
    reason: str
    authorization_id: str = ""
    authorization_revision: int = 0
    authorization_digest: str = ""
    policy_revision: str = ""
    remaining: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "authorization_id": self.authorization_id,
            "authorization_revision": self.authorization_revision,
            "authorization_digest": self.authorization_digest,
            "policy_revision": self.policy_revision,
            "remaining": self.remaining,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> SelectionDecision:
        data = payload or {}
        return cls(
            allowed=bool(data.get("allowed", False)),
            reason=_text(data, "reason"),
            authorization_id=_text(data, "authorization_id"),
            authorization_revision=int(data.get("authorization_revision") or 0),
            authorization_digest=_text(data, "authorization_digest"),
            policy_revision=_text(data, "policy_revision"),
            remaining=None if data.get("remaining") is None else int(data["remaining"]),
        )
