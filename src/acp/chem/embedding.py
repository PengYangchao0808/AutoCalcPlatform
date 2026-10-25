"""ACP chemistry embedding helpers — compatibility forwarder (plan todo 16).

The RDKit-based molecular embedding helpers moved verbatim to
``cccp.analysis.structure.embedding``; this module re-exports the *same
functions* so the historical ``acp.chem.embedding`` import surface stays
stable.  It contains no implementation of its own.
"""

from __future__ import annotations

from cccp.analysis.structure.embedding import (
    count_elements_from_xyz,
    enumerate_embeddings,
    molfile_to_xyz,
    parse_xyz_first_frame,
    smiles_to_xyz,
    split_xyz_frames,
    xyz_formula,
    xyz_to_multiframe_demo,
)

__all__ = [
    "count_elements_from_xyz",
    "enumerate_embeddings",
    "molfile_to_xyz",
    "parse_xyz_first_frame",
    "smiles_to_xyz",
    "split_xyz_frames",
    "xyz_formula",
    "xyz_to_multiframe_demo",
]
