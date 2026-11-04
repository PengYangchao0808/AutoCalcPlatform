"""Persistent molecule identities (chemistry-network B1b) — where ``mol_`` keys live.

B1a (:mod:`cccp.analysis.structure.identity`) decides *what* a molecule is; this
module only *stores* that decision.  It is deliberately computation-free: no
RDKit, no SMILES parsing, no key derivation and no import of ``cccp`` — the
identity scheme (``mol_identity_v1``) arrives as data in each entry so the
constant keeps its single source in ``cccp`` (ANTI #9).  A caller that has no
resolved key must skip the entry; a key is never invented from a name, a file
path or a checksum.

Table (migration ``029``):

    structure_identities — (project_id, identity_key) → scheme + metadata

``first_seen_at`` is written once and preserved by every re-upsert;
``updated_at`` moves on every write.  Because later passes may carry less
metadata than earlier ones (an XYZ-only pass has no InChIKey), an upsert never
replaces a stored non-empty value with an empty/NULL incoming one.

Every function takes a raw :class:`sqlite3.Connection` and returns plain dicts;
locking, transaction scope and ``commit()`` belong to the caller.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable
from typing import Any

from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso

logger = logging.getLogger(__name__)

TABLE = "structure_identities"
"""Table holding one row per ``(project_id, identity_key)``."""

_COLUMNS: tuple[str, ...] = (
    "project_id",
    "identity_key",
    "scheme",
    "canonical_smiles",
    "charge",
    "multiplicity",
    "formula",
    "inchikey",
    "first_seen_at",
    "updated_at",
)
"""Column order used for every ``SELECT`` so row→dict mapping stays stable."""

_SELECT_COLUMNS = ", ".join(_COLUMNS)

# ``scheme`` is required on every write, so it is always refreshed.  The other
# metadata columns keep their stored value when the incoming one is empty/NULL
# (``CASE`` for the ``NOT NULL DEFAULT ''`` text columns, ``COALESCE`` for the
# nullable ones) so a later, poorer pass cannot erase what an earlier one knew.
_UPSERT_SQL = f"""
INSERT INTO {TABLE} (
    project_id, identity_key, scheme, canonical_smiles, charge, multiplicity,
    formula, inchikey, first_seen_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id, identity_key) DO UPDATE SET
    scheme = excluded.scheme,
    canonical_smiles = CASE
        WHEN excluded.canonical_smiles <> '' THEN excluded.canonical_smiles
        ELSE {TABLE}.canonical_smiles
    END,
    charge = COALESCE(excluded.charge, {TABLE}.charge),
    multiplicity = COALESCE(excluded.multiplicity, {TABLE}.multiplicity),
    formula = CASE
        WHEN excluded.formula <> '' THEN excluded.formula
        ELSE {TABLE}.formula
    END,
    inchikey = COALESCE(NULLIF(excluded.inchikey, ''), {TABLE}.inchikey),
    updated_at = excluded.updated_at
"""


def _require_non_empty_text(entry: dict[str, Any], field: str) -> str:
    """Return ``entry[field]`` as a non-empty ``str`` or raise ``ValueError``.

    Args:
        entry: Caller-supplied identity entry.
        field: Required key of *entry*.

    Returns:
        The validated value, returned verbatim (never stripped or rewritten: the
        stored key must be exactly the one ``cccp`` computed).

    Raises:
        ValueError: If the field is missing, not a ``str``, or blank.
    """
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"molecule identity entry requires a non-empty string {field!r}; "
            f"got {value!r}. Never invent or derive an identity key — skip "
            "unresolved entries instead."
        )
    return value


def _optional_text(entry: dict[str, Any], field: str) -> str:
    """Return a ``str`` metadata value, mapping missing/``None`` to ``''``."""
    value = entry.get(field)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _optional_int(entry: dict[str, Any], field: str) -> int | None:
    """Return an ``int`` metadata value, ``None`` when absent or unusable."""
    value = entry.get(field)
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        logger.debug("dropping non-integer %s=%r: %s", field, value, exc)
        return None


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Map one result row (``sqlite3.Row`` or plain tuple) onto :data:`_COLUMNS`."""
    if isinstance(row, sqlite3.Row):
        return {name: row[name] for name in _COLUMNS}
    return dict(zip(_COLUMNS, tuple(row), strict=True))


def upsert_identities(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    identities: Iterable[dict[str, Any]],
    now: str | None = None,
) -> int:
    """Insert or refresh molecule identities for one project.

    Each entry must carry ``identity_key`` and ``scheme`` (non-empty ``str``) and
    may carry ``canonical_smiles``, ``charge``, ``multiplicity``, ``formula`` and
    ``inchikey``.  Re-upserting an existing ``(project_id, identity_key)``
    refreshes metadata, preserves ``first_seen_at`` and bumps ``updated_at``;
    stored non-empty metadata survives an empty/NULL incoming value.

    Args:
        conn: Open connection (the caller owns the transaction; nothing is
            committed here).
        project_id: Owning project; part of the primary key.
        identities: Identity entries, typically built from resolved
            ``MoleculeIdentity`` values.
        now: Timestamp override for ``updated_at`` / ``first_seen_at``
            (defaults to :func:`acp_services.runtime.helpers.utc_now_iso`).

    Returns:
        Number of rows written (one per entry upserted); entries sharing one
        ``identity_key`` collapse onto a single row but still count individually.

    Raises:
        ValueError: If any entry lacks a non-empty ``identity_key`` or ``scheme``.
            Validation runs over the whole batch first, so a rejected batch
            writes nothing.
    """
    entries = list(identities)
    prepared: list[tuple[Any, ...]] = []
    timestamp = now or _utc_now_iso()
    for entry in entries:
        identity_key = _require_non_empty_text(entry, "identity_key")
        scheme = _require_non_empty_text(entry, "scheme")
        prepared.append(
            (
                project_id,
                identity_key,
                scheme,
                _optional_text(entry, "canonical_smiles"),
                _optional_int(entry, "charge"),
                _optional_int(entry, "multiplicity"),
                _optional_text(entry, "formula"),
                entry.get("inchikey") or None,
                timestamp,
                timestamp,
            )
        )

    if not prepared:
        return 0
    conn.executemany(_UPSERT_SQL, prepared)
    return len(prepared)


def get_identity(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    identity_key: str,
) -> dict[str, Any] | None:
    """Return one stored identity row, or ``None`` when the key is unknown."""
    row = conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM {TABLE} WHERE project_id=? AND identity_key=?",
        (project_id, identity_key),
    ).fetchone()
    return None if row is None else _row_to_dict(row)


def list_project_identities(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    limit: int = 200,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return a page of one project's identities, ordered by ``identity_key``.

    Args:
        conn: Open connection.
        project_id: Project whose identities are read.
        limit: Page size, clamped to ``1..500``.
        offset: Rows to skip, clamped to ``>= 0``.

    Returns:
        Deterministic list of row dicts (``[]`` when the project has none).
    """
    page_size = max(1, min(int(limit), 500))
    skip = max(0, int(offset))
    rows = conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM {TABLE} WHERE project_id=? "
        "ORDER BY identity_key ASC LIMIT ? OFFSET ?",
        (project_id, page_size, skip),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def count_project_identities(conn: sqlite3.Connection, *, project_id: str) -> int:
    """Return how many identity rows the project owns."""
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM {TABLE} WHERE project_id=?",
        (project_id,),
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


__all__ = [
    "TABLE",
    "count_project_identities",
    "get_identity",
    "list_project_identities",
    "upsert_identities",
]
