"""Legacy method-dict normalization — single implementation root (B05-7 / todo 77).

``store._row_to_record`` normalizes legacy SQLite job configs on read. The two
functions here (``normalize_legacy_method`` and its ``aux_basis`` migration
helper) moved in place out of ``acp.catalog`` so the migrated
:mod:`acp_services.store.store` does not import ``acp``. ``acp.catalog`` keeps
``normalize_legacy_method`` as a same-object re-export.

Only stdlib + the ``cccp`` method metadata are imported here.
"""

from __future__ import annotations

from typing import Any

from cccp.qc.method_meta import METHOD_META, case_insensitive_get

__all__ = ["normalize_legacy_method"]


def _migrate_legacy_aux_basis(level: dict[str, Any], method_key: str) -> None:
    """Migrate legacy ``aux_basis`` field to ``aux_j_basis`` / ``aux_c_basis``.

    For DLPNO (ri_support="automatic"), aux_basis routes to aux_c_basis.
    For all others, aux_basis routes to aux_j_basis.
    """
    if "aux_basis" not in level:
        return
    if "aux_j_basis" in level or "aux_c_basis" in level:
        return
    func = level.get(method_key) or level.get("functional")
    meta = case_insensitive_get(METHOD_META, func) if func else None
    ri_support = (meta or {}).get("ri_support", "user")
    if ri_support == "automatic":
        level["aux_c_basis"] = level.pop("aux_basis")
    else:
        level["aux_j_basis"] = level.pop("aux_basis")


def normalize_legacy_method(method: dict[str, Any]) -> dict[str, Any]:
    """Read-time normalization: migrate v1.0 fields to v1.1 structure.

    Operates on JobSpec.method dict's levels sub-dict. Idempotent: already
    normalized methods pass through unchanged. Called by store._row_to_record
    to normalize legacy SQLite job configs on read.
    """
    if not isinstance(method, dict):
        return method
    levels = method.get("levels")
    if not isinstance(levels, dict):
        return method
    for level_id, level in levels.items():
        if not isinstance(level, dict):
            continue
        if level_id == "scan_optimizer" and str(level.get("engine") or "").lower() == "xtb":
            # PES DFT-scan extension (2026-09): scan_optimizer now executes
            # exclusively through ORCA (allowed_engines ["orca"]); migrate the
            # legacy "xtb" marker at read time so edit-recalculate passes
            # normalize_and_validate. New level fields are NOT injected —
            # missing keys fall back to schema defaults downstream.
            level["engine"] = "orca"
        _migrate_legacy_aux_basis(level, "functional")
    return method
