"""Structure intake science — models, parsers, and RDKit-based embedding.

Relocated from ``acp.intake.{models,parsers}`` and ``acp.chem.embedding`` by
plan todo 16 (C03-s1).  These modules are the single implementation of the
six-format structure intake (xyz/sdf/mol/gjf/orca-inp/smiles), QC-output
geometry extraction, and RDKit embedding helpers; the historical ``acp``
paths re-export the same objects.

``__init__`` carries re-exports only (no implementation) per repo ANTI #5.
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
from cccp.analysis.structure.models import StructureAsset, StructureParseResult
from cccp.analysis.structure.parsers import (
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
from cccp.analysis.structure.structures import (
    Structure,
    StructureEnsemble,
    StructureRecord,
)
from cccp.analysis.structure.topology import (
    NMR_TOPOLOGY_SDF,
    NMR_TOPOLOGY_SMILES,
    NMR_TOPOLOGY_SOURCES,
    NMR_TOPOLOGY_XYZ_INFERRED,
    NMR_TOPOLOGY_XYZ_UNAVAILABLE,
    NmrTopologyCapture,
    TopologyUnavailableError,
    capture_nmr_topology,
)

__all__ = [
    "NMR_TOPOLOGY_SDF",
    "NMR_TOPOLOGY_SMILES",
    "NMR_TOPOLOGY_SOURCES",
    "NMR_TOPOLOGY_XYZ_INFERRED",
    "NMR_TOPOLOGY_XYZ_UNAVAILABLE",
    "NmrTopologyCapture",
    "Structure",
    "StructureAsset",
    "StructureEnsemble",
    "StructureParseResult",
    "StructureRecord",
    "TopologyUnavailableError",
    "capture_nmr_topology",
    "count_elements_from_xyz",
    "detect_and_parse",
    "detect_format",
    "enumerate_embeddings",
    "molfile_to_xyz",
    "normalize_molblock",
    "parse_gjf_text",
    "parse_mol_text",
    "parse_orca_inp_text",
    "parse_qc_output_text",
    "parse_sdf_text",
    "parse_smiles_list",
    "parse_structure_text",
    "parse_xyz_first_frame",
    "parse_xyz_text",
    "smiles_to_xyz",
    "split_xyz_frames",
    "xyz_formula",
    "xyz_to_multiframe_demo",
]
