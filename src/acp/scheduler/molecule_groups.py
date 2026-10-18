"""Molecule grouping — compat forwarder (B05-7 / todo 77).

The implementation moved in place to :mod:`acp_services.store.molecule_groups`;
this module re-exports the same objects. Do not re-add an implementation here.
"""

from __future__ import annotations

from acp_services.store.molecule_groups import (
    _utc_now_iso,  # noqa: F401 - helper alias
    apply_group_merge,
    resolve_molecule_key,
    suggest_group_merges,
)

__all__ = ["apply_group_merge", "resolve_molecule_key", "suggest_group_merges"]
