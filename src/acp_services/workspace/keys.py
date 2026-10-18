"""API key primitives — digest-only, scoped, expirable, revocable (B01-3 / todo 8).

The single implementation body for issuing and checking programmatic API keys in
the ``local-single`` deployment tier.  Keys are *output* credentials only: a key
is generated, returned to the caller **once**, and from then on only an
irreversible digest is persisted.  There is no "read the key back" path and no
plaintext echo route.

Key shape
---------
``acpk_<key_id>.<secret>``

* ``acpk_`` — constant public prefix (``KEY_PREFIX``); makes keys recognisable
  without revealing anything.
* ``key_id`` — a public, non-secret lookup handle (``secrets.token_hex(8)``).
  Rotation/revocation target this id; it is safe to appear in audit records.
* ``secret`` — the high-entropy credential (``secrets.token_urlsafe(32)`` =
  256 bits from the OS CSPRNG).  Only this part is digest-protected; it is never
  stored, logged, placed in a URL/query string, or passed on a command line.

Digest choice
-------------
``digest = PBKDF2-HMAC-SHA256(secret, per_key_random_salt, PBKDF2_ITERATIONS)``
(see :data:`KDF_NAME`).  A high-entropy secret does not strictly need a slow KDF,
but a per-key salted, iterated derivation means a stolen database never yields a
directly usable key or an offline-comparable digest, and the module never falls
back to bare ``sha256(secret)``.  This is deliberately **not** a reuse of any
low-entropy password hashing scheme.  Verification compares digests with
:func:`hmac.compare_digest` (constant time).

Dependency direction
--------------------
``src/acp_services/**`` must never import ``acp`` (see
``tests/baseline/services-evidence/public_allowlist.md``).  This module is
**stdlib only** (``sqlite3`` is the storage backend).  It defines its own reason
vocabulary whose values mirror ``acp_services.contracts.access.ReasonCode`` so an
authorisation layer can map them 1:1 without this module importing that one.

Deferred to B09 / MU-02 (todo 73) — NOT implemented here
--------------------------------------------------------
Account **registration**, **invitation**, **login sessions** and the
**first-admin bootstrap** flow are explicitly out of scope for this slice.  This
module supplies the *key* primitive only (generate / verify / revoke / rotate);
identity lifecycle and onboarding belong to plan todo 73 (B09-6, MU-02 / MU-04)
and must build on this primitive rather than reinvent key storage.  No plaintext
echo UI is provided (a key is returned exactly once by :meth:`ApiKeyManager.generate`
or :meth:`ApiKeyManager.rotate`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

__all__ = [
    "KEY_PREFIX",
    "KDF_NAME",
    "PBKDF2_ITERATIONS",
    "SALT_BYTES",
    "SECRET_BYTES",
    "ApiKeyManager",
    "GeneratedKey",
    "KeyDecision",
    "KeyReason",
    "KeyRecord",
]

# ---------------------------------------------------------------------------
# Key material constants
# ---------------------------------------------------------------------------

#: Public, non-secret prefix every key carries (``acpk_`` = ACP key).
KEY_PREFIX = "acpk_"
_FIELD_SEPARATOR = "."
#: Digest algorithm label recorded per row (documentation/forensics, not a secret).
KDF_NAME = "pbkdf2_hmac_sha256"
#: PBKDF2 work factor.  High because it is cheap enough per request; the secret
#: entropy is the primary defence and the KDF is defence-in-depth.
PBKDF2_ITERATIONS = 150_000
SALT_BYTES = 16
#: 32 random bytes -> 256 bits of entropy encoded as URL-safe base64.
SECRET_BYTES = 32
_KEY_ID_BYTES = 8

#: Salt used only to spend a comparable KDF when a key id is unknown, so an
#: attacker cannot use response timing to enumerate valid key ids.
_DUMMY_SALT = b"\x00" * SALT_BYTES

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS api_keys (
    key_id          TEXT PRIMARY KEY,
    public_prefix   TEXT NOT NULL,
    digest          BLOB NOT NULL,
    salt            BLOB NOT NULL,
    kdf             TEXT NOT NULL,
    kdf_iterations  INTEGER NOT NULL,
    scope           TEXT NOT NULL,
    project         TEXT,
    actions         TEXT NOT NULL,
    expires_at      TEXT,
    created_at      TEXT NOT NULL,
    revoked_at      TEXT,
    rotated_from    TEXT,
    rotated_to      TEXT
)
"""

_INSERT_SQL = """
INSERT INTO api_keys (
    key_id, public_prefix, digest, salt, kdf, kdf_iterations, scope, project,
    actions, expires_at, created_at, revoked_at, rotated_from, rotated_to
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
"""


class KeyReason(str):
    """Decision reason vocabulary.

    Values mirror the frozen Access contract's ``ReasonCode`` (contract §1.4)
    without importing it, so this stdlib-only module stays dependency-free while
    remaining 1:1 mappable for an authorisation layer.
    """

    GRANTED = "granted"
    NO_GRANT = "no_grant"
    EXPIRED = "expired"
    SCOPE_MISMATCH = "scope_mismatch"
    REVOKED = "revoked"
    ALL = frozenset({GRANTED, NO_GRANT, EXPIRED, SCOPE_MISMATCH, REVOKED})


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _coerce_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def _parse_iso(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _coerce_expiry(expires_at: datetime | str | None) -> str | None:
    """Normalise an expiry into an ISO-8601 UTC string (or ``None``)."""
    if expires_at is None:
        return None
    if isinstance(expires_at, str):
        parsed = _parse_iso(expires_at)
        if parsed is None:
            raise ValueError(f"expires_at is not a valid ISO-8601 timestamp: {expires_at!r}")
        return parsed.isoformat()
    return _coerce_now(expires_at).isoformat()


def _is_expired(expires_at: str | None, now: datetime) -> bool:
    if not expires_at:
        return False
    expiry = _parse_iso(expires_at)
    if expiry is None:
        return False
    return now >= expiry


def _normalize_actions(actions: Iterable[str] | str | None) -> tuple[str, ...]:
    if actions is None:
        return ()
    items = [actions] if isinstance(actions, str) else list(actions)
    ordered: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in ordered:
            ordered.append(text)
    return tuple(ordered)


# ---------------------------------------------------------------------------
# Digest + key parsing
# ---------------------------------------------------------------------------


def _derive_digest(secret: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)


def _split_key(key: Any) -> tuple[str, str] | None:
    """Parse ``acpk_<key_id>.<secret>``; ``None`` for anything unrecognised."""
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):
        return None
    key_id, separator, secret = key[len(KEY_PREFIX) :].partition(_FIELD_SEPARATOR)
    if not separator or not key_id or not secret:
        return None
    if len(key_id) != _KEY_ID_BYTES * 2:
        return None
    return key_id, secret


def _project_allowed(key_project: str | None, requested: str | None) -> bool:
    """An unscoped key (``project=None``) matches any project; a scoped one
    requires an exact project match."""
    if key_project is None:
        return True
    return requested == key_project


# ---------------------------------------------------------------------------
# Public data shapes (no digest/salt ever leaves storage)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedKey:
    """A freshly minted key plus its metadata.

    ``key`` is the **only** time the plaintext credential exists in memory: it is
    returned to the caller once and never reconstructed from storage.  It is
    excluded from ``repr`` so accidental logging cannot leak it.
    """

    key_id: str
    key: str = field(repr=False)
    scope: str = ""
    project: str | None = None
    actions: tuple[str, ...] = ()
    expires_at: str | None = None
    created_at: str = ""

    def to_dict(self, *, include_secret: bool = False) -> dict[str, Any]:
        """Metadata projection; excludes the secret unless explicitly requested
        for a one-time creation response."""
        data: dict[str, Any] = {
            "key_id": self.key_id,
            "scope": self.scope,
            "project": self.project,
            "actions": list(self.actions),
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }
        if include_secret:
            data["key"] = self.key
        return data


@dataclass(frozen=True)
class KeyRecord:
    """Stored key metadata; deliberately has no digest/salt fields."""

    key_id: str
    public_prefix: str
    scope: str
    project: str | None
    actions: tuple[str, ...]
    expires_at: str | None
    created_at: str
    revoked_at: str | None = None
    rotated_from: str | None = None
    rotated_to: str | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


@dataclass(frozen=True)
class KeyDecision:
    """Result of :meth:`ApiKeyManager.verify` (never raises on a denial)."""

    allowed: bool
    reason: str
    key_id: str | None = None
    scope: str | None = None
    project: str | None = None
    actions: tuple[str, ...] = ()
    decided_at: str = ""

    @property
    def effect(self) -> str:
        return "allow" if self.allowed else "deny"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "effect": self.effect,
            "reason": self.reason,
            "key_id": self.key_id,
            "scope": self.scope,
            "project": self.project,
            "actions": list(self.actions),
            "decided_at": self.decided_at,
        }


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class ApiKeyManager:
    """SQLite-backed API key store: generate / verify / revoke / rotate.

    Args:
        db_path: Explicit path to the SQLite database.  The table is created
            idempotently; the caller owns the database location.
        iterations: PBKDF2 work factor (defaults to :data:`PBKDF2_ITERATIONS`).
        clock: Optional ``() -> datetime`` override for the current time, used to
            stamp records.  Verification/revocation/rotation also accept an
            explicit ``now`` for deterministic callers.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        iterations: int = PBKDF2_ITERATIONS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._iterations = int(iterations)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._ensure_schema()

    # -- schema / connection ------------------------------------------------

    def _ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(_CREATE_TABLE_SQL)
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self, now: datetime | None) -> datetime:
        return _coerce_now(now if now is not None else self._clock())

    def _fetch_row(self, key_id: str) -> sqlite3.Row | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
                return cast("sqlite3.Row | None", row)
            finally:
                conn.close()

    # -- lifecycle ----------------------------------------------------------

    def generate(
        self,
        *,
        scope: str,
        actions: Iterable[str] | str | None = None,
        project: str | None = None,
        expires_at: datetime | str | None = None,
        now: datetime | None = None,
    ) -> GeneratedKey:
        """Mint a new key and persist only its digest + metadata.

        The plaintext ``key`` is returned exactly once in the result; it is never
        written to the database, logs, URLs or command lines.
        """
        if not scope:
            raise ValueError("scope is required")
        action_tuple = _normalize_actions(actions)
        if not action_tuple:
            raise ValueError("at least one action is required")
        created_at = self._now(now).isoformat()
        expiry = _coerce_expiry(expires_at)

        key_id = secrets.token_hex(_KEY_ID_BYTES)
        secret = secrets.token_urlsafe(SECRET_BYTES)
        salt = secrets.token_bytes(SALT_BYTES)
        digest = _derive_digest(secret, salt, self._iterations)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    _INSERT_SQL,
                    (
                        key_id,
                        KEY_PREFIX,
                        sqlite3.Binary(digest),
                        sqlite3.Binary(salt),
                        KDF_NAME,
                        self._iterations,
                        scope,
                        project,
                        json.dumps(list(action_tuple), ensure_ascii=False),
                        expiry,
                        created_at,
                        None,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info("api key generated key_id=%s scope=%s project=%s", key_id, scope, project)
        return GeneratedKey(
            key_id=key_id,
            key=f"{KEY_PREFIX}{key_id}{_FIELD_SEPARATOR}{secret}",
            scope=scope,
            project=project,
            actions=action_tuple,
            expires_at=expiry,
            created_at=created_at,
        )

    def get(self, key_id: str) -> KeyRecord | None:
        """Return stored metadata for *key_id* (never the secret/digest)."""
        row = self._fetch_row(key_id)
        return _row_to_record(row) if row is not None else None

    def verify(
        self,
        key: Any,
        *,
        required_action: str,
        project: str | None = None,
        required_scope: str | None = None,
        now: datetime | None = None,
    ) -> KeyDecision:
        """Check *key* against an action/project requirement.

        Rejects (never raises) unknown prefix, malformed key, unknown id, digest
        mismatch, revoked, expired and out-of-scope keys.  Digest comparison is
        constant time; a wrong secret is always reported as ``no_grant`` so a
        caller cannot learn revoked/expired state without presenting the secret.
        """
        reference = self._now(now)
        decided_at = reference.isoformat()

        def deny(reason: str, record: KeyRecord | None = None) -> KeyDecision:
            return KeyDecision(
                allowed=False,
                reason=reason,
                key_id=record.key_id if record is not None else None,
                scope=record.scope if record is not None else None,
                project=record.project if record is not None else None,
                actions=record.actions if record is not None else (),
                decided_at=decided_at,
            )

        parsed = _split_key(key)
        if parsed is None:
            logger.debug("api key rejected: malformed key or unknown prefix")
            return deny(KeyReason.NO_GRANT)
        key_id, secret = parsed

        row = self._fetch_row(key_id)
        if row is None:
            # Spend a comparable KDF so key-id existence is not a timing oracle.
            _derive_digest(secret, _DUMMY_SALT, self._iterations)
            logger.debug("api key rejected: unknown key_id")
            return deny(KeyReason.NO_GRANT)

        record = _row_to_record(row)
        candidate = _derive_digest(secret, bytes(row["salt"]), int(row["kdf_iterations"]))
        if not hmac.compare_digest(candidate, bytes(row["digest"])):
            logger.debug("api key rejected: digest mismatch key_id=%s", record.key_id)
            return deny(KeyReason.NO_GRANT, record)

        if record.revoked_at is not None:
            logger.debug("api key rejected: revoked key_id=%s", record.key_id)
            return deny(KeyReason.REVOKED, record)
        if _is_expired(record.expires_at, reference):
            logger.debug("api key rejected: expired key_id=%s", record.key_id)
            return deny(KeyReason.EXPIRED, record)
        if required_scope is not None and required_scope != record.scope:
            logger.debug("api key rejected: scope mismatch key_id=%s", record.key_id)
            return deny(KeyReason.SCOPE_MISMATCH, record)
        if not _project_allowed(record.project, project):
            logger.debug("api key rejected: project mismatch key_id=%s", record.key_id)
            return deny(KeyReason.SCOPE_MISMATCH, record)
        if required_action not in record.actions:
            logger.debug("api key rejected: action not allowed key_id=%s", record.key_id)
            return deny(KeyReason.NO_GRANT, record)

        logger.debug("api key accepted key_id=%s", record.key_id)
        return KeyDecision(
            allowed=True,
            reason=KeyReason.GRANTED,
            key_id=record.key_id,
            scope=record.scope,
            project=record.project,
            actions=record.actions,
            decided_at=decided_at,
        )

    def revoke(self, key_id: str, *, now: datetime | None = None) -> bool:
        """Mark *key_id* revoked (idempotent).  ``False`` if it does not exist."""
        revoked_at = self._now(now).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT revoked_at FROM api_keys WHERE key_id=?", (key_id,)
                ).fetchone()
                if row is None:
                    return False
                if row["revoked_at"] is None:
                    conn.execute(
                        "UPDATE api_keys SET revoked_at=? WHERE key_id=?",
                        (revoked_at, key_id),
                    )
                    conn.commit()
                    logger.info("api key revoked key_id=%s", key_id)
                return True
            finally:
                conn.close()

    def rotate(
        self,
        key_id: str,
        *,
        expires_at: datetime | str | None = None,
        now: datetime | None = None,
    ) -> GeneratedKey | None:
        """Issue a replacement key and revoke the old one atomically.

        The new key inherits the old key's scope/project/actions (and expiry when
        no explicit ``expires_at`` is given).  The old key is marked revoked and
        linked via ``rotated_to``; the new row records ``rotated_from``.  Returns
        ``None`` if *key_id* does not exist or is already revoked.
        """
        reference = self._now(now)
        created_at = reference.isoformat()

        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
                if row is None:
                    return None
                old = _row_to_record(row)
                if old.revoked_at is not None:
                    return None

                new_key_id = secrets.token_hex(_KEY_ID_BYTES)
                secret = secrets.token_urlsafe(SECRET_BYTES)
                salt = secrets.token_bytes(SALT_BYTES)
                digest = _derive_digest(secret, salt, self._iterations)
                new_expiry = (
                    _coerce_expiry(expires_at) if expires_at is not None else old.expires_at
                )

                conn.execute(
                    _INSERT_SQL,
                    (
                        new_key_id,
                        KEY_PREFIX,
                        sqlite3.Binary(digest),
                        sqlite3.Binary(salt),
                        KDF_NAME,
                        self._iterations,
                        old.scope,
                        old.project,
                        json.dumps(list(old.actions), ensure_ascii=False),
                        new_expiry,
                        created_at,
                        old.key_id,
                    ),
                )
                conn.execute(
                    "UPDATE api_keys SET revoked_at=?, rotated_to=? WHERE key_id=?",
                    (created_at, new_key_id, key_id),
                )
                conn.commit()
            finally:
                conn.close()

        logger.info("api key rotated old_key_id=%s new_key_id=%s", key_id, new_key_id)
        return GeneratedKey(
            key_id=new_key_id,
            key=f"{KEY_PREFIX}{new_key_id}{_FIELD_SEPARATOR}{secret}",
            scope=old.scope,
            project=old.project,
            actions=old.actions,
            expires_at=new_expiry,
            created_at=created_at,
        )


def _row_to_record(row: sqlite3.Row) -> KeyRecord:
    try:
        actions = tuple(json.loads(row["actions"]) or [])
    except (json.JSONDecodeError, TypeError):
        actions = ()
    return KeyRecord(
        key_id=row["key_id"],
        public_prefix=row["public_prefix"],
        scope=row["scope"],
        project=row["project"],
        actions=actions,
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
        rotated_from=row["rotated_from"],
        rotated_to=row["rotated_to"],
    )
