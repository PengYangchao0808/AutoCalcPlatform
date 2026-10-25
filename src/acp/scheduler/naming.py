"""Molecule naming — compat forwarder (B05-7 / todo 77).

The implementation moved in place to :mod:`acp_services.store.naming`; this
module re-exports the same objects. Do not re-add an implementation here.
"""

from __future__ import annotations

from acp_services.store.naming import (
    canonical_molecule_name,
    molecule_group_key,
    molecule_name_from_input,
)

__all__ = ["canonical_molecule_name", "molecule_group_key", "molecule_name_from_input"]
