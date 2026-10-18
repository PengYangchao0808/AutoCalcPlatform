"""External identity binding tests (B01-4 / plan todo 9).

Covers the identity-provider migration contract in
``acp_services.workspace.identity_mapping``: stable ``issuer`` + ``subject``
association, the conflict matrix (duplicate subject / one-to-many /
already-occupied / revoked), the byte-stable side-effect-free migration preview,
rollback that never revives a revoked credential, and the "e-mail is not an
identity key" rule.

The module imports **only** ``acp_services`` (dependency-direction rule:
``acp_services`` never imports the legacy ``acp`` package).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path

import pytest

from acp_services.workspace import identity_mapping


@pytest.fixture()
def store(tmp_path: Path) -> identity_mapping.IdentityMappingStore:
    return identity_mapping.IdentityMappingStore(tmp_path / "identity.db")


def _ext(
    subject: str, *, issuer: str = "oidc.example", email: str | None = None
) -> identity_mapping.ExternalIdentity:
    return identity_mapping.ExternalIdentity(issuer=issuer, subject=subject, email=email)


def _row_counts(db_path: Path) -> tuple[int, int]:
    with sqlite3.connect(str(db_path)) as conn:
        bindings = conn.execute("SELECT COUNT(*) FROM identity_bindings").fetchone()[0]
        conflicts = conn.execute("SELECT COUNT(*) FROM identity_conflicts").fetchone()[0]
    return int(bindings), int(conflicts)


# ---------------------------------------------------------------------------
# shape / local tier
# ---------------------------------------------------------------------------


def test_external_identity_is_frozen_and_local_tier_issuer_is_local() -> None:
    assert identity_mapping.LOCAL_ISSUER == "local"
    identity = identity_mapping.ExternalIdentity(issuer="oidc.example", subject="s1")
    assert dataclasses.is_dataclass(identity)
    assert identity.__dataclass_params__.frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.subject = "s2"  # type: ignore[misc]

    local = identity_mapping.local_identity("u1", email="u1@local")
    assert local.issuer == identity_mapping.LOCAL_ISSUER
    assert local.local is True
    assert identity.local is False


def test_binding_carries_no_roles_and_escalates_nothing() -> None:
    fields = {f.name for f in dataclasses.fields(identity_mapping.IdentityBinding)}
    assert fields.isdisjoint({"roles", "actions", "admin", "scope", "grants"})
    conflict_fields = {f.name for f in dataclasses.fields(identity_mapping.RecordedConflict)}
    assert "roles" not in conflict_fields


def test_issuer_namespace_keeps_local_and_oidc_separate(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    local = identity_mapping.local_identity("shared-subject")
    remote = _ext("shared-subject")
    store.bind("principal_local", local)
    # Same subject, different issuer -> a distinct identity, not a duplicate.
    store.bind("principal_remote", remote)
    assert store.resolve_principal(local) == "principal_local"
    assert store.resolve_principal(remote) == "principal_remote"


# ---------------------------------------------------------------------------
# bind / unbind / restore
# ---------------------------------------------------------------------------


def test_bind_and_unbind_round_trip(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("s1")
    binding = store.bind("p1", ext)
    assert binding.state == identity_mapping.BindingState.ACTIVE
    assert binding.principal_id == "p1"
    assert binding.verified is True
    assert store.resolve_principal(ext) == "p1"
    assert store.get_binding(ext) == binding

    assert store.unbind(ext) is True
    after = store.get_binding(ext)
    assert after is not None and after.state == identity_mapping.BindingState.UNBOUND
    assert store.resolve_principal(ext) is None

    # Restore of an unbound, verified binding is allowed.
    assert store.restore(ext) is True
    assert store.get_binding(ext).state == identity_mapping.BindingState.ACTIVE


def test_principal_may_hold_multiple_externals(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    store.bind("p1", _ext("s1"))
    store.bind("p1", _ext("s2"))
    store.bind("p1", _ext("s3"))
    held = store.list_bindings("p1")
    assert {b.subject for b in held} == {"s1", "s2", "s3"}
    assert all(b.principal_id == "p1" for b in held)


# ---------------------------------------------------------------------------
# conflict matrix (each cell is explicit and recorded)
# ---------------------------------------------------------------------------


def test_duplicate_subject_rejected(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("dup")
    store.bind("p1", ext)
    with pytest.raises(identity_mapping.DuplicateSubjectError) as excinfo:
        store.bind("p1", ext)
    assert excinfo.value.kind == identity_mapping.ConflictKind.DUPLICATE_SUBJECT
    assert excinfo.value.conflict is not None
    assert len(store.conflicts_of_kind(identity_mapping.ConflictKind.DUPLICATE_SUBJECT)) == 1
    # The original binding is untouched.
    assert store.get_binding(ext).principal_id == "p1"


def test_one_to_many_conflict_is_explicit(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("shared")
    store.bind("p1", ext)
    with pytest.raises(identity_mapping.OneToManyConflictError) as excinfo:
        store.bind("p2", ext)
    assert excinfo.value.kind == identity_mapping.ConflictKind.ONE_TO_MANY
    recorded = store.conflicts_of_kind(identity_mapping.ConflictKind.ONE_TO_MANY)
    assert len(recorded) == 1
    assert recorded[0].existing_principal_id == "p1"
    assert recorded[0].attempted_principal_id == "p2"
    # One external still maps to exactly one principal.
    assert store.resolve_principal(ext) == "p1"


def test_already_occupied_after_unbind(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("recycled")
    store.bind("p1", ext)
    store.unbind(ext)
    with pytest.raises(identity_mapping.AlreadyOccupiedError) as excinfo:
        store.bind("p2", ext)
    assert excinfo.value.kind == identity_mapping.ConflictKind.ALREADY_OCCUPIED
    # The same principal may reactivate its own unbound binding.
    rebound = store.bind("p1", ext)
    assert rebound.state == identity_mapping.BindingState.ACTIVE


def test_revoked_cannot_be_rebound(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("revoked-sub")
    store.bind("p1", ext)
    assert store.revoke(ext) is True
    with pytest.raises(identity_mapping.CredentialRevokedError) as excinfo:
        store.bind("p1", ext)
    assert excinfo.value.kind == identity_mapping.ConflictKind.REVOKED
    assert store.get_binding(ext).state == identity_mapping.BindingState.REVOKED


def test_conflicts_of_all_kinds_are_queryable(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("x")
    store.bind("p1", ext)
    for attempt in (lambda: store.bind("p1", ext), lambda: store.bind("p2", ext)):
        with pytest.raises(identity_mapping.IdentityMappingConflictError):
            attempt()
    kinds = {c.kind for c in store.list_conflicts()}
    assert {
        identity_mapping.ConflictKind.DUPLICATE_SUBJECT,
        identity_mapping.ConflictKind.ONE_TO_MANY,
    } <= kinds


def test_empty_principal_or_identity_is_rejected(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    with pytest.raises(ValueError):
        store.bind("", _ext("s1"))
    with pytest.raises(ValueError):
        store.bind("p1", identity_mapping.ExternalIdentity(issuer="", subject="s1"))


# ---------------------------------------------------------------------------
# migration preview: byte-stable and side-effect free
# ---------------------------------------------------------------------------


def test_preview_is_byte_stable_and_has_no_side_effects(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    store.bind("p1", _ext("existing", email="a@x.com"))
    before = _row_counts(store.db_path)

    proposals = [
        ("p1", _ext("new-1")),
        ("p2", _ext("new-2", email="a@x.com")),
    ]
    first = store.migration_preview(proposals)
    second = identity_mapping.migration_preview(store, proposals)

    assert first.to_dict() == second.to_dict()
    assert first.digest == second.digest
    # Canonical serialisation is identical byte-for-byte.
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(
        second.to_dict(), sort_keys=True
    )
    assert first.dry_run is True
    assert first.to_dict()["dry_run"] is True

    # Dry-run wrote nothing at all.
    assert _row_counts(store.db_path) == before


def test_preview_reports_affected_projects_roles_accounts(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    preview = store.migration_preview(
        [("p1", _ext("s1"))],
        principal_facts={
            "p1": identity_mapping.PrincipalFacts(
                principal_id="p1",
                project_ids=("proj-a", "proj-b"),
                roles=("member",),
                account_ids=("acct-1",),
            )
        },
    )
    entry = preview.entries[0]
    assert entry.status == "ready"
    assert entry.affected_projects == ("proj-a", "proj-b")
    assert entry.affected_roles == ("member",)
    assert entry.affected_accounts == ("acct-1",)


def test_preview_reports_hard_conflict_without_writing(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    store.bind("p1", _ext("taken"))
    before = _row_counts(store.db_path)
    preview = store.migration_preview([("p2", _ext("taken"))])
    entry = preview.entries[0]
    assert entry.status == "conflict"
    assert entry.conflict_kind == identity_mapping.ConflictKind.ONE_TO_MANY
    assert {c.kind for c in preview.conflicts} == {identity_mapping.ConflictKind.ONE_TO_MANY}
    assert _row_counts(store.db_path) == before


def test_preview_detects_intra_batch_one_to_many(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    before = _row_counts(store.db_path)
    preview = store.migration_preview([("p1", _ext("same")), ("p2", _ext("same"))])
    statuses = {entry.principal_id: entry.status for entry in preview.entries}
    assert statuses["p1"] == "ready"
    assert statuses["p2"] == "conflict"
    assert any(c.kind == identity_mapping.ConflictKind.ONE_TO_MANY for c in preview.conflicts)
    assert _row_counts(store.db_path) == before


# ---------------------------------------------------------------------------
# rollback semantics
# ---------------------------------------------------------------------------


def test_rollback_restores_verified_only(store: identity_mapping.IdentityMappingStore) -> None:
    verified = _ext("verified")
    unverified = _ext("unverified")
    store.bind("p1", verified, verified=True)
    store.bind("p1", unverified, verified=False)
    store.unbind(verified)
    store.unbind(unverified)

    result = store.rollback()
    assert result.restored == 1
    assert result.skipped_unverified == 1
    assert store.get_binding(verified).state == identity_mapping.BindingState.ACTIVE
    assert store.get_binding(unverified).state == identity_mapping.BindingState.UNBOUND


def test_rollback_does_not_revive_revoked(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("revoked")
    store.bind("p1", ext)
    store.revoke(ext)

    result = store.rollback()
    assert result.skipped_revoked >= 1
    assert result.restored == 0
    assert store.get_binding(ext).state == identity_mapping.BindingState.REVOKED
    # Explicit rollback of that binding id still refuses to revive it.
    binding_id = store.get_binding(ext).binding_id
    explicit = store.rollback([binding_id])
    assert explicit.restored == 0
    assert explicit.skipped_revoked == 1
    assert store.get_binding(ext).state == identity_mapping.BindingState.REVOKED


def test_restore_refuses_revoked(store: identity_mapping.IdentityMappingStore) -> None:
    ext = _ext("doomed")
    store.bind("p1", ext)
    store.revoke(ext)
    with pytest.raises(identity_mapping.CredentialRevokedError):
        store.restore(ext)


# ---------------------------------------------------------------------------
# e-mail is never an identity key (the QA failure branch)
# ---------------------------------------------------------------------------


def test_same_email_different_subject_is_not_merged(
    store: identity_mapping.IdentityMappingStore,
) -> None:
    first = _ext("subject-1", email="shared@example.com")
    second = _ext("subject-2", email="shared@example.com")
    store.bind("p1", first)
    store.bind("p2", second)

    # No merge: the two principals remain distinct owners of distinct subjects.
    assert store.resolve_principal(first) == "p1"
    assert store.resolve_principal(second) == "p2"
    assert store.resolve_principal(first) != store.resolve_principal(second)

    recorded = store.conflicts_of_kind(identity_mapping.ConflictKind.SAME_EMAIL_DISTINCT_SUBJECT)
    assert len(recorded) == 1
    assert recorded[0].existing_principal_id == "p1"
    assert recorded[0].attempted_principal_id == "p2"

    # A preview of the same situation flags the same-e-mail clash too.
    preview = store.migration_preview([("p3", _ext("subject-3", email="shared@example.com"))])
    assert any(
        c.kind == identity_mapping.ConflictKind.SAME_EMAIL_DISTINCT_SUBJECT
        for c in preview.conflicts
    )
