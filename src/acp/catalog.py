from __future__ import annotations

from typing import Any

WORKFLOW_CATALOG: list[dict[str, Any]] = [
    {
        "id": "singlepoint",
        "label": "Single Point Energy",
        "label_zh": "\u5355\u70b9\u80fd\u8ba1\u7b97",
        "category": "simple",
        "description": "Compute energy at current geometry",
        "method_schema_id": "dft_singlepoint",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "planned",
    },
    {
        "id": "optimize",
        "label": "Geometry Optimization",
        "label_zh": "\u51e0\u4f55\u4f18\u5316",
        "category": "simple",
        "description": "Optimize molecular geometry",
        "method_schema_id": "dft_optimize",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "planned",
    },
    {
        "id": "frequency",
        "label": "Frequency Calculation",
        "label_zh": "\u9891\u7387\u8ba1\u7b97",
        "category": "simple",
        "description": "Compute vibrational frequencies",
        "method_schema_id": "dft_frequency",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "planned",
    },
    {
        "id": "optfreq",
        "label": "Optimization + Frequency",
        "label_zh": "\u4f18\u5316+\u9891\u7387",
        "category": "simple",
        "description": "Optimize then compute frequencies",
        "method_schema_id": "dft_optfreq",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "planned",
    },
    {
        "id": "xtb_optimize",
        "label": "xTB Optimization",
        "label_zh": "xTB \u4f18\u5316",
        "category": "simple",
        "description": "Fast semi-empirical optimization with xTB",
        "method_schema_id": "xtb_optimize",
        "default_backend": "xtb",
        "requires_binaries": ["xtb"],
        "status": "planned",
    },
    {
        "id": "conformer",
        "label": "Conformer Search",
        "label_zh": "\u6784\u8c61\u641c\u7d22",
        "category": "preset",
        "description": "CREST conformer search + DFT refinement + thermo",
        "method_schema_id": "confsearch",
        "default_backend": "crest",
        "requires_binaries": ["crest", "orca"],
        "status": "active",
    },
    {
        "id": "nmr",
        "label": "NMR Shielding",
        "label_zh": "NMR \u5c4f\u853d\u8ba1\u7b97",
        "category": "preset",
        "description": "GIAO NMR shielding + Boltzmann averaging",
        "method_schema_id": "nmr",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "active",
    },
    {
        "id": "benchmark",
        "label": "Benchmark",
        "label_zh": "\u57fa\u51c6\u6d4b\u8bd5",
        "category": "preset",
        "description": "Compare protocols/methods across test set",
        "method_schema_id": "benchmark",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "active",
    },
    {
        "id": "ensemble",
        "label": "Ensemble Generation",
        "label_zh": "\u6784\u8c61\u751f\u6210",
        "category": "preset",
        "description": "CREST conformer search + CENSO prescreening/screening",
        "method_schema_id": "censo_ensemble",
        "default_backend": "censo",
        "requires_binaries": ["crest", "censo", "orca"],
        "status": "active",
    },
    {
        "id": "energy",
        "label": "Conformer Energy",
        "label_zh": "\u6784\u8c61\u80fd\u91cf",
        "category": "preset",
        "description": "CREST + CENSO screening + rank1 DFT refinement (opt+freq+SP+Shermo)",
        "method_schema_id": "censo_energy",
        "default_backend": "censo",
        "requires_binaries": ["crest", "censo", "orca"],
        "status": "active",
    },
    {
        "id": "mechanism",
        "label": "Mechanism / TS",
        "label_zh": "\u673a\u7406\u7814\u7a76 / \u8fc7\u6e21\u6001",
        "category": "preset",
        "description": "TS guess + optimization + IRC verification",
        "method_schema_id": "mechanism",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "planned",
    },
    {
        "id": "custom_sequence",
        "label": "Custom Task Sequence",
        "label_zh": "\u81ea\u5b9a\u4e49\u4efb\u52a1\u5e8f\u5217",
        "category": "custom",
        "description": "Build a linear pipeline of calculation blocks",
        "method_schema_id": "custom",
        "default_backend": "",
        "requires_binaries": [],
        "status": "planned",
    },
]

FIELD_DEFINITIONS: dict[str, Any] = {
    "functional": {
        "type": "select",
        "per_backend": {            "orca": ["B3LYP", "PBE0", "wB97X-D4", "wB97M-V", "r2SCAN-3c", "DLPNO-CCSD(T)"],
            "xtb": ["GFN0-xTB", "GFN1-xTB", "GFN2-xTB"],
        },
        "default": {"*": "r2SCAN-3c"},
    },
    "basis": {
        "type": "select",
        "per_backend": {
            "orca": [
                "def2-SVP",
                "def2-TZVP",
                "def2-mTZVPP",
                "def2-TZVPP",
                "def2-TZVPPD",
                "cc-pVTZ",
                "cc-pwCVTZ",
            ],
        },
        "default": {"*": "def2-SVP"},
    },
    "dispersion": {
        "type": "select",
        "options": ["none", "D3", "D3BJ", "D4"],
        "default": {"*": "D4"},
    },
    "solvent_model": {
        "type": "select",
        "per_backend": {
            "orca": ["none", "CPCM", "SMD"],
            "xtb": ["none", "ALPB", "GBSA"],
        },
        "default": {"*": "none"},
    },
    "solvent": {
        "type": "select",
        "options": [
            "none",
            "water",
            "methanol",
            "ethanol",
            "acetone",
            "dichloromethane",
            "toluene",
            "THF",
            "DMSO",
            "acetonitrile",
            "chloroform",
            "hexane",
            "benzene",
        ],
        "default": {"*": "none"},
        "depends_on": {"field": "solvent_model", "not_values": ["none"]},
    },
    "grid": {
        "type": "select",
        "options": ["SG1", "Fine", "UltraFine", "SuperFine"],
        "default": {"*": "UltraFine"},
    },
    "scf_convergence": {
        "type": "select",
        "options": ["Normal", "Tight", "VeryTight"],
        "default": {"*": "Tight"},
    },
    "opt_convergence": {
        "type": "select",
        "options": ["Loose", "Normal", "Tight", "VeryTight"],
        "default": {"*": "Tight"},
    },
    "max_steps": {"type": "int", "min": 1, "max": 10000, "default": {"*": 100}},
    "temperature": {"type": "float", "min": 0, "max": 10000, "default": {"*": 298.15}, "unit": "K"},
    "pressure": {"type": "float", "min": 0, "max": 100000, "default": {"*": 1.0}, "unit": "atm"},
    "scale_factor": {"type": "float", "min": 0, "max": 1.0, "default": {"*": 1.0}},
    "ewin": {
        "type": "float",
        "label": "Energy Window",
        "min": 0,
        "default": {"*": 6.0},
        "unit": "kcal/mol",
    },
    "rthr": {
        "type": "float",
        "label": "RMSD Threshold",
        "min": 0,
        "default": {"*": 0.125},
        "unit": "Angstrom",
    },
    "gfn": {
        "type": "select",
        "per_backend": {"xtb": ["GFN0-xTB", "GFN1-xTB", "GFN2-xTB"]},
        "default": {"*": "GFN2-xTB"},
    },
    "charge": {"type": "int", "default": {"*": 0}},
    "multiplicity": {"type": "int", "default": {"*": 1}},
    "aux_basis": {
        "type": "select",
        "per_backend": {            "orca": ["", "def2-TZVPP/C", "cc-pVTZ/C"],
        },
        "default": {"*": ""},
    },
    "ri_approximation": {
        "type": "select",
        "per_backend": {"orca": ["none", "RI", "RIJCOSX", "RIJK"]},
        "default": {"*": "none"},
    },
}

METHOD_SCHEMAS: dict[str, Any] = {
    "confsearch": {
        "method_levels": [
            {
                "level_id": "preopt",
                "label": "xTB Pre-optimization",
                "label_zh": "xTB \u9884\u4f18\u5316",
                "required": True,
                "allowed_engines": ["xtb"],
                "fields": ["gfn", "solvent_model"],
            },
            {
                "level_id": "crest",
                "label": "Conformer Search",
                "label_zh": "\u6784\u8c61\u641c\u7d22",
                "required": True,
                "allowed_engines": ["crest"],
                "fields": ["gfn", "ewin", "rthr", "solvent_model", "charge", "multiplicity"],
            },
            {
                "level_id": "dft_opt",
                "label": "DFT Optimization",
                "label_zh": "DFT \u4f18\u5316",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                    "max_steps",
                    "charge",
                    "multiplicity",
                ],
            },
            {
                "level_id": "single_point",
                "label": "Single Point Energy",
                "label_zh": "\u5355\u70b9\u80fd",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "aux_basis",
                    "dispersion",
                    "ri_approximation",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                    "charge",
                    "multiplicity",
                ],
            },
            {
                "level_id": "thermo",
                "label": "Thermochemistry",
                "label_zh": "\u70ed\u529b\u5b66\u4fee\u6b63",
                "required": False,
                "allowed_engines": ["shermo"],
                "fields": ["temperature", "pressure", "scale_factor"],
            },
        ],
        "profiles": [
            {
                "profile_id": "ext",
                "label": "ext (CREST two-stage)",
                "summary": "CREST GFN0 => GFN2 | DFT opt | SP",
                "levels": {
                    "preopt": {"engine": "xtb", "gfn": "GFN2-xTB", "solvent_model": "none"},
                    "crest": {
                        "engine": "crest",
                        "gfn": "GFN2-xTB",
                        "ewin": 6.0,
                        "rthr": 0.125,
                        "solvent_model": "none",
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "def2-mTZVPP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "aux_basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                        "scale_factor": 1.0,
                    },
                },
            },
            {
                "profile_id": "lite",
                "label": "Lite Protocol",
                "summary": "CREST GFN2 | r2SCAN-3c opt (ORCA) | wB97M-V SP (ORCA)",
                "levels": {
                    "preopt": {"engine": "xtb", "gfn": "GFN2-xTB", "solvent_model": "none"},
                    "crest": {
                        "engine": "crest",
                        "gfn": "GFN2-xTB",
                        "ewin": 6.0,
                        "rthr": 0.125,
                        "solvent_model": "none",
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "def2-mTZVPP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "aux_basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                        "scale_factor": 1.0,
                    },
                },
            },
            {
                "profile_id": "full",
                "label": "Full Protocol",
                "summary": "CREST GFN2 | r2SCAN-3c/def2-mTZVPP opt | wB97M-V/def2-TZVPP SP",
                "levels": {
                    "preopt": {"engine": "xtb", "gfn": "GFN2-xTB", "solvent_model": "none"},
                    "crest": {
                        "engine": "crest",
                        "gfn": "GFN2-xTB",
                        "ewin": 6.0,
                        "rthr": 0.125,
                        "solvent_model": "none",
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "def2-mTZVPP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "aux_basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                        "scale_factor": 1.0,
                    },
                },
            },
        ],
    },
    "dft_singlepoint": {
        "method_levels": [
            {
                "level_id": "singlepoint",
                "label": "Single Point",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                ],
            }
        ],
        "profiles": [],
    },
    "dft_optimize": {
        "method_levels": [
            {
                "level_id": "optimize",
                "label": "Optimization",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                    "max_steps",
                    "opt_convergence",
                ],
            }
        ],
        "profiles": [],
    },
    "dft_frequency": {
        "method_levels": [
            {
                "level_id": "frequency",
                "label": "Frequency",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": ["functional", "basis", "temperature", "pressure", "scale_factor"],
            }
        ],
        "profiles": [],
    },
    "dft_optfreq": {
        "method_levels": [
            {
                "level_id": "optfreq",
                "label": "Opt+Freq",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "temperature",
                    "pressure",
                ],
            }
        ],
        "profiles": [],
    },
    "xtb_optimize": {
        "method_levels": [
            {
                "level_id": "xtb_opt",
                "label": "xTB Opt",
                "required": True,
                "allowed_engines": ["xtb"],
                "fields": ["gfn", "solvent_model", "solvent", "max_steps"],
            }
        ],
        "profiles": [],
    },
    "nmr": {
        "method_levels": [
            {
                "level_id": "shielding",
                "label": "NMR Shielding",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": ["functional", "basis"],
            }
        ],
        "profiles": [],
    },
    "censo_ensemble": {
        "method_levels": [
            {
                "level_id": "censo",
                "label": "CENSO Ensemble",
                "label_zh": "CENSO \u6784\u8c61\u7cfb\u7efc",
                "required": True,
                "allowed_engines": ["censo"],
                "fields": ["ewin", "charge", "multiplicity"],
            },
            {
                "level_id": "thermo",
                "label": "Thermochemistry",
                "label_zh": "\u70ed\u529b\u5b66\u4fee\u6b63",
                "required": False,
                "allowed_engines": ["shermo"],
                "fields": ["temperature", "pressure"],
            },
        ],
        "profiles": [
            {
                "profile_id": "censo-light",
                "label": "CENSO-light",
                "summary": "CREST + CENSO prescreening + B97-3c screening",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                    },
                },
            },
            {
                "profile_id": "censo-default",
                "label": "CENSO-default",
                "summary": "CREST + full CENSO Part0-Part2 (prescreening+screening+optimization)",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                    },
                },
            },
            {
                "profile_id": "censo-zero",
                "label": "CENSO-zero",
                "summary": "CREST only, xTB energy sort, direct ensemble export",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                    },
                },
            },
        ],
    },
    "censo_energy": {
        "method_levels": [
            {
                "level_id": "censo",
                "label": "CENSO Screening",
                "label_zh": "CENSO \u7b5b\u9009",
                "required": True,
                "allowed_engines": ["censo"],
                "fields": ["ewin", "charge", "multiplicity"],
            },
            {
                "level_id": "dft_opt",
                "label": "DFT Optimization",
                "label_zh": "DFT \u4f18\u5316",
                "required": False,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                    "opt_convergence",
                    "max_steps",
                    "charge",
                    "multiplicity",
                ],
            },
            {
                "level_id": "refinement_sp",
                "label": "Single Point Energy",
                "label_zh": "\u5355\u70b9\u80fd",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "aux_basis",
                    "dispersion",
                    "ri_approximation",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                    "charge",
                    "multiplicity",
                ],
            },
            {
                "level_id": "thermo",
                "label": "Thermochemistry",
                "label_zh": "\u70ed\u529b\u5b66\u4fee\u6b63",
                "required": False,
                "allowed_engines": ["shermo"],
                "fields": ["temperature", "pressure", "scale_factor"],
            },
        ],
        "profiles": [
            {
                "profile_id": "censo-light",
                "label": "CENSO-light",
                "summary": "CREST + CENSO PS + rank1 r2SCAN-3c opt/freq + wB97M-V SP + Shermo",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "def2-mTZVPP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                        "opt_convergence": "Normal",
                        "max_steps": 200,
                    },
                    "refinement_sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "aux_basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                        "scale_factor": 0.9905,
                    },
                },
            },
            {
                "profile_id": "censo-default",
                "label": "CENSO-default",
                "summary": "Full Part0-Part3 + ACP freq/Shermo on 99% survivors",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "refinement_sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "aux_basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                        "scale_factor": 0.9905,
                    },
                },
            },
            {
                "profile_id": "censo-zero",
                "label": "CENSO-zero",
                "summary": "CREST + xTB rank1 + r2SCAN-3c opt/freq + wB97M-V SP + Shermo",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "def2-mTZVPP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                        "opt_convergence": "Normal",
                        "max_steps": 200,
                    },
                    "refinement_sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "aux_basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                        "scale_factor": 0.9905,
                    },
                },
            },
        ],
    },
}

METHOD_CATALOG: dict[str, Any] = {
    "backends": [
        {
            "id": "orca",
            "label": "ORCA",
            "supports": ["singlepoint", "optimize", "frequency", "nmr"],
        },
        {"id": "xtb", "label": "xTB (GFN2)", "supports": ["singlepoint", "optimize"]},
        {"id": "crest", "label": "CREST", "supports": ["conformer_search"]},
    ],
    "field_definitions": FIELD_DEFINITIONS,
    "method_schemas": METHOD_SCHEMAS,
}


# ---------------------------------------------------------------------------
# 验证 + 标准化函数
# ---------------------------------------------------------------------------


def _resolve_field_options(field_name: str, engine: str) -> list[str] | None:
    """Return allowed options for a field given a specific engine."""
    fd = FIELD_DEFINITIONS.get(field_name)
    if not fd:
        return None
    if "options" in fd:
        return fd["options"]
    if "per_backend" in fd:
        return fd["per_backend"].get(
            engine, list(fd["per_backend"].values())[0] if fd["per_backend"] else []
        )
    return None


def _resolve_field_default(field_name: str, engine: str) -> Any:
    fd = FIELD_DEFINITIONS.get(field_name)
    if not fd:
        return ""
    dflt = fd.get("default", {})
    if isinstance(dflt, dict):
        if engine in dflt:
            return dflt[engine]
        if "*" in dflt:
            return dflt["*"]
    return dflt if not isinstance(dflt, dict) else ""


def _normalize_solvent(levels: dict, schema: dict) -> dict:
    """When solvent_model is 'none', force solvent to empty string."""
    for ml in schema.get("method_levels", []):
        lid = ml["level_id"]
        lv_data = levels.get(lid, {})
        if "solvent" in ml.get("fields", []) and lv_data.get("solvent_model") in (None, "", "none"):
            lv_data["solvent"] = ""
        if "solvent" in ml.get("fields", []) and lv_data.get("solvent") is None:
            lv_data["solvent"] = ""
    return levels


def normalize_and_validate_method_config(method: dict, schema: dict) -> tuple[dict, list[str]]:
    """Return (normalized_levels, errors)."""
    errors: list[str] = []

    levels: dict[str, Any] = {}
    for lv_def in schema.get("method_levels", []):
        lid = lv_def["level_id"]
        user_lv = (method.get("levels") or {}).get(lid, {}) or {}
        engine = user_lv.get("engine") or (
            lv_def["allowed_engines"][0] if lv_def.get("allowed_engines") else ""
        )

        if not engine:
            if lv_def.get("required"):
                errors.append(f"Level '{lid}': no engine selected")
            continue
        if engine not in lv_def.get("allowed_engines", []):
            errors.append(f"Level '{lid}': engine '{engine}' not in allowed list")
            continue

        normalized: dict[str, Any] = {"engine": engine}
        for field_name in lv_def.get("fields", []):
            user_val = user_lv.get(field_name)
            if user_val is not None and user_val != "":
                options = _resolve_field_options(field_name, engine)
                if options is not None:
                    # Normalize solvent_model to lowercase for backend compatibility.
                    if field_name == "solvent_model":
                        user_val = str(user_val).lower()
                        lower_options = {str(o).lower() for o in options}
                        if user_val not in lower_options:
                            errors.append(
                                f"Level '{lid}', field '{field_name}': "
                                f"value '{user_lv.get(field_name)}' not in allowed options"
                            )
                            continue
                    elif user_val not in options:
                        errors.append(
                            f"Level '{lid}', field '{field_name}': "
                            f"value '{user_val}' not in allowed options"
                        )
                        continue
                normalized[field_name] = user_val
            else:
                default_val = _resolve_field_default(field_name, engine)
                normalized[field_name] = default_val

        # Composite-method rules: r2SCAN-3c bundles def2-mTZVPP and its dispersion correction.
        if normalized.get("functional") == "r2SCAN-3c":
            normalized["basis"] = "def2-mTZVPP"
            normalized["dispersion"] = "none"
        # wB97M-V bundles the VV10 dispersion correction.
        if normalized.get("functional") == "wB97M-V":
            normalized["dispersion"] = "none"

        levels[lid] = normalized

    # Check required levels
    for lv_def in schema.get("method_levels", []):
        lid = lv_def["level_id"]
        if lv_def.get("required") and lid not in levels:
            errors.append(f"Required level '{lid}' is missing")

    # Normalize solvent
    levels = _normalize_solvent(levels, schema)

    return levels, errors


def convert_method_levels_to_protocol_levels(levels: dict[str, Any]) -> dict[str, Any]:
    """Convert frontend method-level settings into protocol override levels.

    The ACP Workbench (and ``method_levels_to_workflow_config``) uses stage
    names like ``dft_opt`` and ``single_point`` and stores the functional
    under the key ``functional``. The legacy protocol resolver expects
    ``optimization`` / ``single_point`` and ``method`` / ``basis``.
    """
    if not isinstance(levels, dict):
        return {}

    stage_mapping: dict[str, str] = {
        "dft_opt": "optimization",
        "single_point": "single_point",
        "thermo": "thermo",
    }
    field_mapping: dict[str, str] = {
        "functional": "method",
        "engine": "engine",
        "basis": "basis",
        "solvent": "solvent",
        "solvent_model": "solvent_model",
        "temperature": "temperature_k",
        "pressure": "pressure_atm",
        "scale_factor": "scl_zpe",
    }

    converted: dict[str, Any] = {}
    for old_stage, new_stage in stage_mapping.items():
        if old_stage not in levels:
            continue
        old_level = levels[old_stage]
        if not isinstance(old_level, dict):
            continue
        new_level: dict[str, Any] = {}
        for old_key, new_key in field_mapping.items():
            if old_key in old_level:
                value = old_level[old_key]
                if old_key == "solvent_model":
                    value = str(value).lower()
                new_level[new_key] = value
        if new_level:
            converted[new_stage] = new_level

    # The frontend schema for conformer search does not expose a separate
    # frequency level, so frequency should inherit the optimization engine.
    if "optimization" in converted and "frequency" not in converted:
        opt_engine = converted["optimization"].get("engine")
        if opt_engine is not None:
            converted["frequency"] = {"engine": opt_engine}

    return converted


def method_levels_to_workflow_config(levels: dict, schema_id: str, workflow: str) -> dict:
    """Convert normalized method levels to workflow config (written to method_config.json)."""
    config: dict[str, Any] = {}
    if schema_id == "confsearch":
        if "preopt" in levels:
            config["xtb_preopt"] = dict(levels["preopt"])
        if "crest" in levels:
            config["crest"] = dict(levels["crest"])
        if "dft_opt" in levels:
            d = dict(levels["dft_opt"])
            d.pop("charge", None)
            d.pop("multiplicity", None)
            config["optimize"] = d
        if "single_point" in levels:
            d = dict(levels["single_point"])
            d.pop("charge", None)
            d.pop("multiplicity", None)
            config["sp"] = d
        if "thermo" in levels:
            config["thermo"] = dict(levels["thermo"])
    else:
        config = dict(levels)
    return config


# ---------------------------------------------------------------------------
# 原有 API 兼容函数
# ---------------------------------------------------------------------------


def get_workflow_catalog() -> list[dict[str, Any]]:
    return WORKFLOW_CATALOG


def get_method_catalog() -> dict[str, Any]:
    return METHOD_CATALOG


def get_workflow_by_id(wf_id: str) -> dict[str, Any] | None:
    for wf in WORKFLOW_CATALOG:
        if wf["id"] == wf_id:
            return wf
    return None


def get_method_schema(schema_id: str) -> dict[str, Any] | None:
    return METHOD_SCHEMAS.get(schema_id)


def get_method_profiles(schema_id: str) -> list[dict[str, Any]]:
    schema = METHOD_SCHEMAS.get(schema_id, {})
    return schema.get("profiles", [])


__all__ = [
    "FIELD_DEFINITIONS",
    "METHOD_CATALOG",
    "METHOD_SCHEMAS",
    "WORKFLOW_CATALOG",
    "convert_method_levels_to_protocol_levels",
    "get_method_catalog",
    "get_method_profiles",
    "get_method_schema",
    "get_workflow_by_id",
    "get_workflow_catalog",
    "method_levels_to_workflow_config",
    "normalize_and_validate_method_config",
]
