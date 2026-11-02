"""Selection publication use-case (round-5 PR-05).

Orchestrates the *publication group* of a manual PES selection: one
authoritative review, the materialised structures, the result-manifest switch
and the rotation backup.  The group is persisted through an injected intent
store so a crash mid-publish is recoverable and a retried request is idempotent
instead of silently creating a second revision.

This module is pure orchestration: no filesystem, no ACP imports.  The ACP side
provides the intent store (atomic JSON) and the publication steps (the single
review writer); services own the idempotency/recovery decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = [
    "PublicationConflictError",
    "PublicationDecision",
    "PublicationInProgressError",
    "PublicationIntentStore",
    "PublicationLeaseLostError",
    "SelectionPublicationService",
]

#: How long one publisher owns an unfinished intent before another may take over.
DEFAULT_LEASE_SECONDS = 120


class PublicationConflictError(ValueError):
    """The same idempotency key was reused with a different payload."""


class PublicationInProgressError(ValueError):
    """Another publication with a different payload is still unfinished."""


class PublicationLeaseLostError(ValueError):
    """This publisher's lease was taken over; it must not commit."""


class PublicationIntentStore(Protocol):
    """Persistence port for one publication intent (per task root)."""

    def load(self) -> dict[str, Any] | None: ...

    def save(self, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class PublicationDecision:
    """What the caller should do before running the publication steps."""

    action: str  # publish | replay | recovering
    reason: str = ""
    intent: dict[str, Any] = field(default_factory=dict)


class SelectionPublicationService:
    """Idempotent/recoverable publication decisions over an intent store."""

    def __init__(
        self,
        store: PublicationIntentStore,
        *,
        clock: Any | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        self._store = store
        self._clock = clock
        self._lease_seconds = lease_seconds

    def _now_dt(self) -> Any:
        from datetime import datetime, timezone

        return self._clock() if self._clock is not None else datetime.now(timezone.utc)

    def _now(self) -> str:
        return self._now_dt().isoformat(timespec="seconds")

    def _lease_until(self) -> str:
        from datetime import timedelta

        return (self._now_dt() + timedelta(seconds=self._lease_seconds)).isoformat(
            timespec="seconds"
        )

    @staticmethod
    def _lease_active(intent: dict[str, Any], now: str) -> bool:
        raw = intent.get("lease_until")
        if not isinstance(raw, str) or not raw:
            return False
        return raw > now

    def begin(
        self,
        *,
        key: str | None,
        digest: str,
        expected_revision: int | None,
        current_revision: int,
        owner_id: str = "",
    ) -> PublicationDecision:
        """Decide publish / replay / recover, or raise a typed conflict."""
        intent = self._store.load()
        if intent is None:
            return PublicationDecision("publish", "no previous publication intent")
        status = str(intent.get("status") or "")
        now = self._now()
        if (
            status == "pending"
            and self._lease_active(intent, now)
            and str(intent.get("owner_id") or "") not in ("", owner_id)
        ):
            raise PublicationInProgressError(
                "another publisher holds an active lease on this task"
            )
        previous_key = intent.get("idempotency_key")
        previous_digest = intent.get("payload_digest")
        same_key = key is not None and previous_key == key
        if status == "published":
            if same_key:
                if previous_digest == digest:
                    return PublicationDecision(
                        "replay",
                        "already published with the same key and payload digest",
                        intent=dict(intent),
                    )
                raise PublicationConflictError(
                    "idempotency key was already used for a different publication payload"
                )
            return PublicationDecision(
                "publish", "a new publication after a completed one", intent=dict(intent)
            )
        if status in {"pending", "failed"}:
            if same_key:
                if previous_digest != digest:
                    raise PublicationConflictError(
                        "idempotency key is bound to a different unpublished payload"
                    )
                return PublicationDecision(
                    "recovering",
                    "resume the unfinished publication (same key and payload)",
                    intent=dict(intent),
                )
            if status == "pending":
                raise PublicationInProgressError(
                    "another publication is still in progress for this task"
                )
            return PublicationDecision(
                "publish", "a new publication after a failed one", intent=dict(intent)
            )
        return PublicationDecision("publish", f"unrecognized previous state {status!r}")

    def record(
        self,
        *,
        job_id: str,
        key: str | None,
        digest: str,
        expected_revision: int | None,
        target_revision: int,
        stages: tuple[str, ...],
        completed: tuple[str, ...],
        status: str,
        owner_id: str = "",
        error: str | None = None,
    ) -> dict[str, Any]:
        """Persist the current publication state (called after every step).

        Refuses to write when another owner took the lease over (fencing).
        """
        current = self._store.load()
        if current is not None:
            previous_owner = str(current.get("owner_id") or "")
            if (
                previous_owner
                and owner_id
                and previous_owner != owner_id
                and str(current.get("status") or "") == "pending"
                and self._lease_active(current, self._now())
            ):
                raise PublicationLeaseLostError(
                    "another publisher took over this task; this publication cannot commit"
                )
        payload: dict[str, Any] = {
            "schema": "pes_publication_intent_v1",
            "job_id": job_id,
            "idempotency_key": key,
            "payload_digest": digest,
            "expected_revision": expected_revision,
            "target_revision": target_revision,
            "stages": list(stages),
            "completed_stages": list(completed),
            "status": status,
            "owner_id": owner_id,
            "lease_until": (
                self._lease_until() if status in {"pending", "published"} else None
            ),
            "updated_at": self._now(),
        }
        if error is not None:
            payload["error"] = error
        self._store.save(payload)
        return payload
