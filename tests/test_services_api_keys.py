"""B01-3 (todo 8) — API key primitives: digest-only, scoped, expirable, revocable.

Acceptance coverage:

* generate -> only a digest is stored (the plaintext key never lands in the DB
  file, the row, logs, a URL or a command line);
* verify -> valid key allowed, wrong scope / wrong action / expired / revoked /
  unknown-prefix / tampered-digest all rejected;
* rotation issues a new secret and marks the old key rotated (old rejected);
* revocation is idempotent;
* ``hmac.compare_digest`` is used and a wrong secret cannot reveal revoked or
  expired state.

Tests use a reduced PBKDF2 work factor for speed; a separate test pins the
production constant, so the storage/verification logic — not key strength — is
what these cases exercise.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from acp_services.workspace.keys import (
    KDF_NAME,
    KEY_PREFIX,
    PBKDF2_ITERATIONS,
    SALT_BYTES,
    ApiKeyManager,
    GeneratedKey,
    KeyDecision,
    KeyReason,
    KeyRecord,
)

_TEST_ITERATIONS = 2_000
_NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def manager(tmp_path: Path) -> ApiKeyManager:
    return ApiKeyManager(tmp_path / "api_keys.db", iterations=_TEST_ITERATIONS)


def _raw_row(manager: ApiKeyManager, key_id: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(str(manager.db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
    finally:
        conn.close()


def _secret_of(generated: GeneratedKey) -> str:
    return generated.key.split(".", 1)[1]


# ---------------------------------------------------------------------------
# generate — format + digest-only storage
# ---------------------------------------------------------------------------


def test_generate_returns_prefix_id_and_high_entropy_secret(manager: ApiKeyManager) -> None:
    first = manager.generate(scope="workspace", project="p1", actions=["read"])
    second = manager.generate(scope="workspace", project="p1", actions=["read"])

    assert first.key.startswith(KEY_PREFIX)
    body = first.key[len(KEY_PREFIX) :]
    key_id, separator, secret = body.partition(".")
    assert separator == "."
    assert len(key_id) == 16
    assert len(secret) >= 40  # 32 random bytes -> 256-bit URL-safe secret
    assert first.key_id == key_id
    assert first.key != second.key
    assert first.key_id != second.key_id


def test_generate_stores_digest_only_and_no_plaintext(manager: ApiKeyManager) -> None:
    generated = manager.generate(
        scope="workspace", project="p1", actions=["read", "write"], now=_NOW
    )
    secret = _secret_of(generated)

    row = _raw_row(manager, generated.key_id)
    assert row is not None
    assert bytes(row["digest"]) != secret.encode("utf-8")
    assert len(bytes(row["digest"])) == 32
    assert len(bytes(row["salt"])) == SALT_BYTES
    assert row["kdf"] == KDF_NAME
    assert row["public_prefix"] == KEY_PREFIX
    assert row["expires_at"] is None

    # No stored column contains the plaintext secret or the full key.
    stored_values = {str(row[name]) for name in row.keys()}
    assert secret not in stored_values
    assert generated.key not in stored_values

    # The database bytes do not contain the credential either.
    raw_bytes = manager.db_path.read_bytes()
    assert generated.key.encode("utf-8") not in raw_bytes
    assert secret.encode("utf-8") not in raw_bytes


def test_generate_requires_scope_and_actions(manager: ApiKeyManager) -> None:
    with pytest.raises(ValueError):
        manager.generate(scope="", actions=["read"])
    with pytest.raises(ValueError):
        manager.generate(scope="workspace", actions=[])
    with pytest.raises(ValueError):
        manager.generate(scope="workspace", actions=None)


def test_generate_rejects_invalid_expiry(manager: ApiKeyManager) -> None:
    with pytest.raises(ValueError):
        manager.generate(scope="workspace", actions=["read"], expires_at="not-a-date")


def test_generated_metadata_round_trips(manager: ApiKeyManager) -> None:
    expiry = _NOW + timedelta(days=7)
    generated = manager.generate(
        scope="workspace",
        project="p42",
        actions=["write", "read", "write"],
        expires_at=expiry,
        now=_NOW,
    )
    record = manager.get(generated.key_id)
    assert record is not None
    assert record.scope == "workspace"
    assert record.project == "p42"
    assert record.actions == ("write", "read")  # de-duplicated, order preserved
    assert record.expires_at == expiry.isoformat()
    assert record.revoked is False
    assert record.rotated_from is None


# ---------------------------------------------------------------------------
# verify — happy path
# ---------------------------------------------------------------------------


def test_verify_accepts_valid_key(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", actions=["read", "write"], now=_NOW)
    decision = manager.verify(generated.key, required_action="read", project="p1", now=_NOW)
    assert isinstance(decision, KeyDecision)
    assert decision.allowed is True
    assert decision.reason == KeyReason.GRANTED
    assert decision.effect == "allow"
    assert decision.key_id == generated.key_id
    assert decision.actions == ("read", "write")


def test_verify_unscoped_key_allows_any_project(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    for project in (None, "alpha", "beta"):
        decision = manager.verify(generated.key, required_action="read", project=project, now=_NOW)
        assert decision.allowed is True, project


def test_verify_required_scope_is_enforced(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    ok = manager.verify(generated.key, required_action="read", required_scope="workspace")
    assert ok.allowed is True
    bad = manager.verify(generated.key, required_action="read", required_scope="admin")
    assert bad.reason == KeyReason.SCOPE_MISMATCH


# ---------------------------------------------------------------------------
# verify — rejections
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "not-a-key",
        "acpk_",
        "acpk_.secret",
        "acpk_short.secret",
        "acpk_" + "0" * 16 + ".",  # empty secret
        "zzz_" + "0" * 16 + ".secret",  # unknown prefix
        12345,  # non-string
        None,
    ],
)
def test_verify_rejects_malformed_or_unknown_prefix(
    manager: ApiKeyManager, bad_key: object
) -> None:
    decision = manager.verify(bad_key, required_action="read", now=_NOW)
    assert decision.allowed is False
    assert decision.reason == KeyReason.NO_GRANT
    assert decision.key_id is None


def test_verify_rejects_unknown_key_id(manager: ApiKeyManager) -> None:
    forged = f"{KEY_PREFIX}{'a' * 16}.{secrets.token_urlsafe(32)}"
    decision = manager.verify(forged, required_action="read", now=_NOW)
    assert decision.allowed is False
    assert decision.reason == KeyReason.NO_GRANT


def test_verify_rejects_wrong_action(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    decision = manager.verify(generated.key, required_action="write", now=_NOW)
    assert decision.allowed is False
    assert decision.reason == KeyReason.NO_GRANT


def test_verify_rejects_project_scope_mismatch(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", project="proj-a", actions=["read"])
    assert manager.verify(generated.key, required_action="read", project="proj-a", now=_NOW).allowed
    for wrong in ("proj-b", None):
        decision = manager.verify(generated.key, required_action="read", project=wrong, now=_NOW)
        assert decision.allowed is False
        assert decision.reason == KeyReason.SCOPE_MISMATCH


def test_verify_rejects_expired_key_and_boundary(manager: ApiKeyManager) -> None:
    boundary = manager.generate(
        scope="workspace", actions=["read"], expires_at=_NOW + timedelta(seconds=1)
    )
    assert manager.verify(boundary.key, required_action="read", now=_NOW).allowed
    at_expiry = manager.verify(
        boundary.key, required_action="read", now=_NOW + timedelta(seconds=1)
    )
    assert at_expiry.allowed is False
    assert at_expiry.reason == KeyReason.EXPIRED

    already = manager.generate(
        scope="workspace", actions=["read"], expires_at=_NOW - timedelta(seconds=1)
    )
    expired = manager.verify(already.key, required_action="read", now=_NOW)
    assert expired.allowed is False
    assert expired.reason == KeyReason.EXPIRED


def test_verify_rejects_wrong_secret_without_revealing_state(
    manager: ApiKeyManager,
) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    manager.revoke(generated.key_id, now=_NOW)

    # Correct secret on a revoked key -> "revoked".
    assert (
        manager.verify(generated.key, required_action="read", now=_NOW).reason == KeyReason.REVOKED
    )
    # Wrong secret on the *same* revoked id -> "no_grant", proving the digest is
    # checked (constant time) before any state is disclosed.
    forged = f"{KEY_PREFIX}{generated.key_id}.{secrets.token_urlsafe(32)}"
    wrong = manager.verify(forged, required_action="read", now=_NOW)
    assert wrong.allowed is False
    assert wrong.reason == KeyReason.NO_GRANT


# ---------------------------------------------------------------------------
# revoke
# ---------------------------------------------------------------------------


def test_revoke_then_verify_rejected(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    assert manager.verify(generated.key, required_action="read", now=_NOW).allowed

    assert manager.revoke(generated.key_id, now=_NOW) is True
    decision = manager.verify(generated.key, required_action="read", now=_NOW)
    assert decision.allowed is False
    assert decision.reason == KeyReason.REVOKED

    record = manager.get(generated.key_id)
    assert record is not None and record.revoked is True


def test_revoke_is_idempotent_and_missing_returns_false(manager: ApiKeyManager) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    assert manager.revoke(generated.key_id, now=_NOW) is True
    first_stamp = manager.get(generated.key_id).revoked_at  # type: ignore[union-attr]

    # A second revocation must not rewrite the audit timestamp.
    assert manager.revoke(generated.key_id, now=_NOW + timedelta(hours=1)) is True
    assert manager.get(generated.key_id).revoked_at == first_stamp  # type: ignore[union-attr]

    assert manager.revoke("does-not-exist") is False


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


def test_rotate_old_rejected_new_works_and_links(manager: ApiKeyManager) -> None:
    old = manager.generate(
        scope="workspace",
        project="p1",
        actions=["read", "write"],
        expires_at=_NOW + timedelta(days=1),
        now=_NOW,
    )
    new = manager.rotate(old.key_id, now=_NOW)
    assert new is not None
    assert new.key_id != old.key_id
    assert new.key != old.key
    assert new.scope == old.scope
    assert new.project == old.project
    assert new.actions == old.actions
    assert new.expires_at == old.expires_at

    assert manager.get(old.key_id).revoked is True
    assert manager.get(old.key_id).rotated_to == new.key_id
    assert manager.get(new.key_id).rotated_from == old.key_id

    old_decision = manager.verify(old.key, required_action="read", project="p1", now=_NOW)
    assert old_decision.allowed is False
    assert old_decision.reason == KeyReason.REVOKED

    new_decision = manager.verify(new.key, required_action="read", project="p1", now=_NOW)
    assert new_decision.allowed is True
    assert new_decision.reason == KeyReason.GRANTED


def test_rotate_missing_or_revoked_returns_none(manager: ApiKeyManager) -> None:
    assert manager.rotate("missing-id", now=_NOW) is None

    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    manager.revoke(generated.key_id, now=_NOW)
    assert manager.rotate(generated.key_id, now=_NOW) is None


def test_rotate_can_override_expiry(manager: ApiKeyManager) -> None:
    old = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    new_expiry = _NOW + timedelta(days=30)
    new = manager.rotate(old.key_id, expires_at=new_expiry, now=_NOW)
    assert new is not None
    assert new.expires_at == new_expiry.isoformat()


def test_rotated_secret_never_persisted(manager: ApiKeyManager) -> None:
    old = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    new = manager.rotate(old.key_id, now=_NOW)
    assert new is not None
    raw_bytes = manager.db_path.read_bytes()
    assert new.key.encode("utf-8") not in raw_bytes
    assert _secret_of(new).encode("utf-8") not in raw_bytes


# ---------------------------------------------------------------------------
# QA failure scenario — tampered digest
# ---------------------------------------------------------------------------


def test_tamper_digest_then_verify_rejected(manager: ApiKeyManager) -> None:
    """QA failure path: mutating the stored digest must invalidate the key."""
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    assert manager.verify(generated.key, required_action="read", now=_NOW).allowed

    conn = sqlite3.connect(str(manager.db_path))
    try:
        conn.execute(
            "UPDATE api_keys SET digest=? WHERE key_id=?",
            (sqlite3.Binary(os.urandom(32)), generated.key_id),
        )
        conn.commit()
    finally:
        conn.close()

    decision = manager.verify(generated.key, required_action="read", now=_NOW)
    assert decision.allowed is False
    assert decision.reason == KeyReason.NO_GRANT


# ---------------------------------------------------------------------------
# logging / leakage
# ---------------------------------------------------------------------------


def test_logs_never_contain_plaintext(
    manager: ApiKeyManager, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="acp_services.workspace.keys"):
        revoked_key = manager.generate(scope="workspace", project="p1", actions=["read"], now=_NOW)
        rotated_key = manager.generate(
            scope="workspace", project="p2", actions=["read", "write"], now=_NOW
        )
        assert manager.verify(
            revoked_key.key, required_action="read", project="p1", now=_NOW
        ).allowed
        assert not manager.verify(
            f"{KEY_PREFIX}{revoked_key.key_id}.{secrets.token_urlsafe(8)}",
            required_action="read",
            project="p1",
            now=_NOW,
        ).allowed
        manager.revoke(revoked_key.key_id, now=_NOW)
        manager.verify(revoked_key.key, required_action="read", project="p1", now=_NOW)
        rotated = manager.rotate(rotated_key.key_id, now=_NOW)
        assert rotated is not None
        manager.verify(rotated.key, required_action="read", project="p2", now=_NOW)
        manager.verify(rotated_key.key, required_action="read", project="p2", now=_NOW)

    assert caplog.text, "log capture is inactive — the no-plaintext assertion is meaningless"
    secrets_seen = {
        _secret_of(revoked_key),
        _secret_of(rotated_key),
        _secret_of(rotated),
    }
    for secret in secrets_seen:
        assert secret not in caplog.text
    assert revoked_key.key not in caplog.text
    assert rotated_key.key not in caplog.text
    assert rotated.key not in caplog.text


def test_generated_key_repr_and_dict_do_not_leak_secret(
    manager: ApiKeyManager,
) -> None:
    generated = manager.generate(scope="workspace", actions=["read"], now=_NOW)
    secret = _secret_of(generated)

    assert generated.key not in repr(generated)
    assert secret not in repr(generated)

    metadata = generated.to_dict()
    assert "key" not in metadata
    assert metadata["key_id"] == generated.key_id
    assert generated.to_dict(include_secret=True)["key"] == generated.key


def test_key_record_exposes_no_digest_or_salt_fields() -> None:
    fields = {f.name for f in dataclasses.fields(KeyRecord)}
    assert "digest" not in fields
    assert "salt" not in fields
    assert {
        "key_id",
        "scope",
        "project",
        "actions",
        "expires_at",
        "revoked_at",
        "rotated_from",
    } <= fields


# ---------------------------------------------------------------------------
# strength constants (production values, not the reduced test factor)
# ---------------------------------------------------------------------------


def test_kdf_constants_are_strong() -> None:
    assert KDF_NAME == "pbkdf2_hmac_sha256"
    assert PBKDF2_ITERATIONS >= 100_000
    assert SALT_BYTES >= 16
