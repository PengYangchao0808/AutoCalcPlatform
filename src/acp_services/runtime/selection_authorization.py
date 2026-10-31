"""Bounded automatic-authorization for PES selection publication (round-5 PR-06).

The platform administrator (an approval-capable principal) creates a *bounded*
authorization: owner principal/project, policy revision, expiry, allowed source
job/attempts, actions, workflow, candidate roles, methods, resource ceiling and
persistent consumption limits.  Agents may only *use* an existing authorization
inside its scope; they can never create, widen or renew one.

Every actual action appends a digest + decision reference to the authorization
record through the existing Data research-resource CAS store, so replayed
actions do not re-consume the limits and usage survives a restart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from acp_services.contracts.selection import (
    SELECTION_ACTIONS,
    SELECTION_AUTHORIZATION_SCHEMA,
    SELECTION_CONTRACT_VERSION,
    SelectionAuthorization,
    SelectionDecision,
    SelectionLimits,
    SelectionPublicationRequest,
)

__all__ = [
    "SelectionAuthorizationError",
    "SelectionAuthorizationService",
    "authorization_resource_id",
]

_AUTH_RESOURCE_PREFIX = "selection-auth:"


def authorization_resource_id(authorization_id: str) -> str:
    return f"{_AUTH_RESOURCE_PREFIX}{authorization_id}"


class SelectionAuthorizationError(ValueError):
    """A typed authorization refusal (never a silent allow)."""

    def __init__(self, message: str, *, code: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SelectionAuthorizationService:
    """Create and consume bounded selection authorizations."""

    def __init__(
        self,
        store: Any,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or _utc_now

    # ── administration (never reachable by an agent credential) ──────────

    def create(
        self,
        authorization: SelectionAuthorization,
        *,
        is_admin: bool,
        principal_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """Persist a new authorization; agents cannot self-authorize."""
        if not is_admin:
            raise SelectionAuthorizationError(
                "only an approval-capable administrator may create a selection authorization",
                code="permission_denied",
            )
        if authorization.owner_principal_id != principal_id or (
            authorization.owner_project_id != project_id
        ):
            raise SelectionAuthorizationError(
                "the authorization owner must match the creating principal/project",
                code="permission_denied",
            )
        record = self._store.create(
            _resource_request(authorization, principal_id=principal_id, project_id=project_id)
        )
        return record.document

    # ── agent use ────────────────────────────────────────────────────────

    def validate(
        self,
        request: SelectionPublicationRequest,
        *,
        action: str = "publish_pes_selection",
        latest_attempt: int | None = None,
        action_digest: str | None = None,
    ) -> SelectionDecision:
        """Check every scope bound to *action*; raise a typed refusal.

        ``action_digest`` marks a retried action: when that digest is already in
        the actions log the quota check is skipped (a replay never re-charges).
        """
        document = self._read(
            request.authorization_id,
            principal_id=request.actor_principal_id,
            project_id=request.project_id,
        )
        authorization = SelectionAuthorization.from_dict(document)
        record = self._read_record(
            request.authorization_id,
            principal_id=request.actor_principal_id,
            project_id=request.project_id,
        )
        revision = int(record.revision)
        digest = str(record.digest)

        def refuse(reason: str, code: str, **details: Any) -> None:
            raise SelectionAuthorizationError(
                reason,
                code=code,
                details={
                    "authorization_id": authorization.authorization_id,
                    "authorization_revision": revision,
                    **details,
                },
            )

        if authorization.revoked:
            refuse("the selection authorization was revoked", "authorization_revoked")
        if authorization.actions and action not in authorization.actions:
            refuse(
                f"action {action!r} is not allowed by this authorization",
                "action_not_allowed",
            )
        if action not in SELECTION_ACTIONS:
            refuse(f"unknown selection action {action!r}", "action_not_allowed")
        if authorization.owner_project_id != request.project_id:
            refuse("authorization belongs to a different project", "project_mismatch")
        if authorization.owner_principal_id != request.actor_principal_id:
            refuse(
                "authorization belongs to a different principal",
                "principal_mismatch",
            )
        valid_until = _parse_time(authorization.valid_until)
        if valid_until is None or valid_until <= self._clock():
            refuse("the selection authorization expired", "authorization_expired")
        if request.source.job_id != authorization.source_job_id:
            refuse(
                "the selection source task is outside this authorization",
                "source_mismatch",
            )
        if latest_attempt is not None and request.source.attempt != int(latest_attempt):
            refuse(
                "the selection source attempt is stale",
                "stale_source",
                request_attempt=request.source.attempt,
                latest_attempt=int(latest_attempt),
            )
        if (
            authorization.allowed_attempts
            and request.source.attempt not in authorization.allowed_attempts
        ):
            refuse(
                "the selection source attempt is not authorized",
                "attempt_not_allowed",
            )
        if request.workflow != authorization.workflow:
            refuse("workflow is outside this authorization", "workflow_mismatch")
        roles = request.roles()
        if authorization.roles and any(role not in authorization.roles for role in roles):
            refuse("candidate role is outside this authorization", "role_not_allowed")
        if authorization.methods and request.method:
            method = request.method
            if not any(method.startswith(prefix) for prefix in authorization.methods):
                refuse("method is outside this authorization", "method_not_allowed")
        if authorization.nproc_max and request.nproc > authorization.nproc_max:
            refuse(
                "requested resources exceed the authorization ceiling",
                "resource_exceeded",
                nproc=request.nproc,
                nproc_max=authorization.nproc_max,
            )
        replay = False
        if action_digest:
            replay = any(
                str(entry.get("action") or "") == action
                and str(entry.get("digest") or "") == action_digest
                for entry in authorization.actions_log
            )
        if action == "publish_pes_selection" and not replay:
            amount = len(request.candidates)
            remaining = authorization.limits.remaining(action)
            if amount > remaining:
                refuse(
                    "candidate budget exhausted",
                    "quota_exceeded",
                    requested=amount,
                    remaining=remaining,
                )
        return SelectionDecision(
            allowed=True,
            reason="authorized",
            authorization_id=authorization.authorization_id,
            authorization_revision=revision,
            authorization_digest=digest,
            policy_revision=authorization.policy_revision,
            remaining=authorization.limits.remaining(action),
        )

    def consume(
        self,
        request: SelectionPublicationRequest,
        decision: SelectionDecision,
        *,
        action: str = "publish_pes_selection",
        amount: int | None = None,
        action_digest: str = "",
    ) -> dict[str, Any]:
        """Consume quota once per (action, digest); retries never double-charge."""
        record = self._read_record(
            request.authorization_id,
            principal_id=request.actor_principal_id,
            project_id=request.project_id,
        )
        document = dict(record.document)
        authorization = SelectionAuthorization.from_dict(document)
        if authorization.revoked:
            raise SelectionAuthorizationError(
                "the selection authorization was revoked",
                code="authorization_revoked",
            )
        digest_key = action_digest or request.idempotency_key or ""
        if digest_key:
            for entry in authorization.actions_log:
                if (
                    str(entry.get("action") or "") == action
                    and str(entry.get("digest") or "") == digest_key
                ):
                    # a replayed action never re-consumes quota
                    return authorization.to_dict()
        if decision.authorization_revision and decision.authorization_revision != int(
            record.revision
        ):
            raise SelectionAuthorizationError(
                "the authorization changed since the decision was made",
                code="stale_decision",
                details={"decision_revision": decision.authorization_revision},
            )
        consumed = int(amount if amount is not None else len(request.candidates))
        limits = authorization.limits
        enough = limits.remaining(action)
        if consumed > enough:
            raise SelectionAuthorizationError(
                "selection authorization quota exhausted",
                code="quota_exceeded",
                details={"requested": consumed, "remaining": enough},
            )
        updated = SelectionLimits(
            max_candidates=limits.max_candidates,
            max_batch_jobs=limits.max_batch_jobs,
            max_retries=limits.max_retries,
            used_candidates=limits.used_candidates
            + (consumed if action == "publish_pes_selection" else 0),
            used_batch_jobs=limits.used_batch_jobs
            + (consumed if action == "batch_consume" else 0),
            used_retries=limits.used_retries
            + (consumed if action == "retry_consume" else 0),
        )
        log_entry = {
            "action": action,
            "digest": digest_key,
            "decision": "allowed",
            "amount": consumed,
            "at": self._clock().isoformat(timespec="seconds"),
            "contract_version": SELECTION_CONTRACT_VERSION,
        }
        new_document = {
            **document,
            "limits": updated.to_dict(),
            "actions_log": [*document.get("actions_log", []), log_entry],
        }
        try:
            self._store.compare_and_swap(
                authorization_resource_id(request.authorization_id),
                expected_revision=int(record.revision),
                document=new_document,
                principal_id=request.actor_principal_id,
                project_id=request.project_id,
                schema=document.get("schema") or None,
            )
        except Exception as exc:  # CAS conflict or access error → typed refusal
            raise SelectionAuthorizationError(
                "could not record the authorization consumption",
                code="authorization_cas_conflict",
            ) from exc
        return new_document

    # ── internals ────────────────────────────────────────────────────────

    def _read_record(
        self, authorization_id: str, *, principal_id: str, project_id: str
    ) -> Any:
        from acp_services.data.research_resources import ResearchResourceRevokedError

        try:
            return self._store.read(
                authorization_resource_id(authorization_id),
                principal_id=principal_id,
                project_id=project_id,
            )
        except ResearchResourceRevokedError as exc:
            raise SelectionAuthorizationError(
                "the selection authorization was revoked",
                code="authorization_revoked",
            ) from exc
        except Exception as exc:
            # unknown and cross-project ids share one answer (no existence leak)
            raise SelectionAuthorizationError(
                f"selection authorization {authorization_id!r} is not available",
                code="authorization_not_found",
            ) from exc

    def _read(
        self, authorization_id: str, *, principal_id: str, project_id: str
    ) -> dict[str, Any]:
        return dict(self._read_record(
            authorization_id, principal_id=principal_id, project_id=project_id
        ).document)


def _resource_request(
    authorization: SelectionAuthorization, *, principal_id: str, project_id: str
) -> Any:
    from acp_services.data.research_resources import ResearchResourceRequest

    return ResearchResourceRequest(
        resource_id=authorization_resource_id(authorization.authorization_id),
        schema=SELECTION_AUTHORIZATION_SCHEMA,
        document=authorization.to_dict(),
        principal_id=principal_id,
        project_id=project_id,
    )


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
