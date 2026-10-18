"""Structure intake parsers — compatibility forwarder (plan todo 16).

The six-format structure parsers and format detection moved verbatim to
``cccp.analysis.structure.parsers``; this module re-exports the *same
functions* so the historical ``acp.intake.parsers`` import surface stays
stable.  It contains no implementation of its own.

Private counter helpers are re-exported for callers that relied on the old
module namespace; they operate on the single (cccp) counter.
"""

from __future__ import annotations

from cccp.analysis.structure.parsers import (  # noqa: F401 - historical module surface
    Path_safe,
    _hill_formula,
    _next_asset_id,
    _reset_asset_counter,
    detect_and_parse,
    detect_format,
    normalize_molblock,
    parse_gjf_text,
    parse_mol_text,
    parse_orca_inp_text,
    parse_qc_output_text,
    parse_sdf_text,
    parse_smiles_list,
    parse_structure_text,
    parse_xyz_text,
)

__all__ = [
    "detect_and_parse",
    "detect_format",
    "normalize_molblock",
    "parse_gjf_text",
    "parse_mol_text",
    "parse_orca_inp_text",
    "parse_qc_output_text",
    "parse_sdf_text",
    "parse_smiles_list",
    "parse_structure_text",
    "parse_xyz_text",
]
