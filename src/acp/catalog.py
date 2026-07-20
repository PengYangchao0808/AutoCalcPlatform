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
        "status": "active",
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
        "status": "active",
    },
    {
        "id": "frequency",
        "label": "Frequency Calculation",
        "label_zh": "频率计算",
        "category": "simple",
        "description": "Compute vibrational frequencies",
        "method_schema_id": "dft_frequency",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "active",
    },
    {
        "id": "optfreq",
        "label": "Optimization + Frequency",
        "label_zh": "优化+频率",
        "category": "simple",
        "description": "Optimize then compute frequencies",
        "method_schema_id": "dft_optfreq",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "active",
    },
    {
        "id": "optfreqsp",
        "label": "Opt+Freq+SP+Thermo",
        "label_zh": "优化+频率+单点+热化学",
        "category": "simple",
        "description": "ORCA opt → freq → SP → Shermo free energy",
        "method_schema_id": "dft_optfreqsp",
        "default_backend": "orca",
        "requires_binaries": ["orca", "shermo"],
        "status": "active",
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
        "label_zh": "构象搜索",
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
        "label_zh": "构象生成",
        "category": "preset",
        "description": "CREST + CENSO → Boltzmann conformer ensemble",
        "method_schema_id": "censo_ensemble",
        "default_backend": "censo",
        "requires_binaries": ["crest", "censo", "orca"],
        "status": "active",
    },
    {
        "id": "energy",
        "label": "Conformer Energy",
        "label_zh": "构象能量",
        "category": "preset",
        "description": "CREST + CENSO → 99% ensemble → refined free energies",
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

# ── Functional → basis set + dispersion mapping ──────────────────────────
# Each functional defines which basis sets and dispersion corrections are
# chemically valid. The UI filters basis/dispersion dropdowns dynamically
# based on the selected functional.
# NOTE: _ALL_BASIS_SETS is defined *before* FIELD_DEFINITIONS so it can be
# referenced by the "basis" field.

_ALL_BASIS_SETS: list[str] = [
    "def2-SV(P)", "def2-SVP", "def2-SVPD",
    "def2-TZVP", "def2-TZVPP", "def2-TZVPPD",
    "def2-QZVP", "def2-QZVPP", "def2-QZVPPD",
    "ma-def2-SVP", "ma-def2-TZVP", "ma-def2-TZVPP", "ma-def2-QZVPP",
    "cc-pVDZ", "cc-pVTZ", "cc-pVQZ", "cc-pV5Z",
    "aug-cc-pVDZ", "aug-cc-pVTZ", "aug-cc-pVQZ",
    "cc-pwCVDZ", "cc-pwCVTZ", "cc-pwCVQZ",
    "cc-pCVDZ", "cc-pCVTZ",
]

FUNCTIONAL_OPTIONS_MAP: dict[str, dict[str, list[str]]] = {
    "B3LYP": {
        "basis": _ALL_BASIS_SETS,
        "dispersion": ["none", "D3", "D3BJ", "D4"],
    },
    "PBE0": {
        "basis": _ALL_BASIS_SETS,
        "dispersion": ["none", "D3", "D3BJ", "D4"],
    },
    "wB97X-D4": {
        "basis": _ALL_BASIS_SETS,
        "dispersion": ["none"],
    },
    "wB97M-V": {
        "basis": _ALL_BASIS_SETS,
        "dispersion": ["none"],
    },
    "r2SCAN-3c": {
        "basis": [""],
        "dispersion": ["none"],
    },
    "DLPNO-CCSD(T)": {
        "basis": ["def2-TZVPP"],
        "dispersion": ["none"],
    },
}

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
            "orca": _ALL_BASIS_SETS,
        },
        "default": {"*": ""},
        "supports_custom": True,
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
    "recalc_hess": {
        "type": "int",
        "label": "Hessian Recalc Interval",
        "min": 1,
        "max": 1000,
        "default": {"*": 10},
    },
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
                        "basis": "",
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
                        "basis": "",
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
                "summary": "CREST GFN2 | r2SCAN-3c opt | wB97M-V/def2-TZVPP SP",
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
                        "basis": "",
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
                "level_id": "single_point",
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
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default (wB97M-V)",
                "summary": "wB97M-V/def2-TZVPP Single Point",
                "levels": {
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                },
            },
        ],
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
                    "recalc_hess",
                ],
            }
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default (r2SCAN-3c)",
                "summary": "r2SCAN-3c Optimization",
                "levels": {
                    "optimize": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                },
            },
        ],
    },
    "dft_frequency": {
        "method_levels": [
            {
                "level_id": "frequency",
                "label": "Frequency",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                ],
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
                    "grid",
                    "scf_convergence",
                    "max_steps",
                    "recalc_hess",
                ],
            }
        ],
        "profiles": [],
    },
    "dft_optfreqsp": {
        "method_levels": [
            {
                "level_id": "optfreq",
                "label": "Opt+Freq",
                "label_zh": "优化+频率（同级）",
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
                    "recalc_hess",
                ],
            },
            {
                "level_id": "single_point",
                "label": "Single Point",
                "label_zh": "单点能（高精度）",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "aux_basis",
                    "ri_approximation",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                ],
            },
            {
                "level_id": "thermo",
                "label": "Thermochemistry",
                "label_zh": "热化学修正",
                "required": False,
                "allowed_engines": ["shermo"],
                "fields": ["temperature", "pressure", "scale_factor"],
            },
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default (r2SCAN-3c / wB97M-V)",
                "summary": "r2SCAN-3c Opt+Freq | wB97M-V/def2-TZVPP SP | Shermo",
                "levels": {
                    "optfreq": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
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
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
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
                "label_zh": "CENSO 构象系综",
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
                "summary": "CREST + CENSO screening → Boltzmann ensemble (recommended)",
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
                "summary": "Full CENSO funnel (Part0–2, high-accuracy DFT opt)",
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
                "summary": "CREST xTB passthrough (no CENSO, fastest)",
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
                "label_zh": "CENSO 筛选",
                "required": True,
                "allowed_engines": ["censo"],
                "fields": ["ewin", "charge", "multiplicity"],
            },
            {
                "level_id": "dft_opt",
                "label": "DFT Optimization",
                "label_zh": "DFT 结构优化",
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
                "summary": "CREST + CENSO → 99% ensemble → DFT refinement (recommended)",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
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
                "summary": "Full CENSO Part0–3 → 99% refinement (~10x light cost)",
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
                "summary": "CREST xTB → 99% ensemble → DFT refinement (cheapest)",
                "levels": {
                    "censo": {
                        "engine": "censo",
                        "ewin": 6.0,
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
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
            "supports": ["singlepoint", "optimize", "frequency", "optfreq", "nmr"],
        },
        {"id": "xtb", "label": "xTB (GFN2)", "supports": ["singlepoint", "optimize"]},
        {"id": "crest", "label": "CREST", "supports": ["conformer_search"]},
    ],
    "field_definitions": FIELD_DEFINITIONS,
    "method_schemas": METHOD_SCHEMAS,
    "functional_options_map": FUNCTIONAL_OPTIONS_MAP,
}


# ---------------------------------------------------------------------------
# 验证 + 标准化函数
# ---------------------------------------------------------------------------


def _resolve_field_options(
    field_name: str, engine: str, functional: str | None = None,
) -> list[str] | None:
    """Return allowed options for a field given a specific engine.

    For ``basis`` and ``dispersion`` fields, when a functional is provided
    the options are resolved from ``FUNCTIONAL_OPTIONS_MAP`` so that only
    chemically valid combinations are offered.
    """
    if functional and field_name in ("basis", "dispersion"):
        mapping = FUNCTIONAL_OPTIONS_MAP.get(functional)
        if mapping and field_name in mapping:
            return mapping[field_name]
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


def _resolve_field_default(
    field_name: str, engine: str, functional: str | None = None,
) -> Any:
    """Return the default value for a field, respecting functional context.

    If the functional restricts ``basis`` or ``dispersion`` options,
    the first allowed option is returned *unless* the global default
    (from ``FIELD_DEFINITIONS``) is also valid for the functional.
    """
    fd = FIELD_DEFINITIONS.get(field_name)
    global_default: Any = ""
    if fd:
        dflt = fd.get("default", {})
        if isinstance(dflt, dict):
            global_default = dflt.get(engine, dflt.get("*", ""))
        else:
            global_default = dflt

    if functional and field_name in ("basis", "dispersion"):
        mapping = FUNCTIONAL_OPTIONS_MAP.get(functional)
        if mapping and field_name in mapping:
            opts = mapping[field_name]
            if opts:
                if global_default and global_default in opts:
                    return global_default
                return opts[0]

    return global_default


def _clamp_to_functional(level: dict[str, Any], method_key: str) -> None:
    """Clamp basis/dispersion to the functional's allowed values.

    Safety net: ensures execution-path converters never pass invalid
    basis/dispersion combinations to backends, even if validation was
    bypassed.
    """
    func_name = level.get(method_key)
    if not func_name:
        return
    mapping = FUNCTIONAL_OPTIONS_MAP.get(func_name)
    if not mapping:
        return
    for key in ("basis", "dispersion"):
        if key not in mapping or key not in level:
            continue
        allowed = mapping[key]
        current = level[key]
        if current and current not in allowed and current != "__custom__":
            level[key] = allowed[0]


_CASE_INSENSITIVE_FIELDS = frozenset({"solvent_model", "dispersion"})


def _normalize_solvent(levels: dict, schema: dict) -> dict:
    """When solvent_model is 'none', force solvent to empty string. Also
    lowercases ``solvent_model`` values for case-insensitive matching."""
    for ml in schema.get("method_levels", []):
        lid = ml["level_id"]
        lv_data = levels.get(lid, {})
        if "solvent" in ml.get("fields", []):
            sm = lv_data.get("solvent_model")
            if sm is not None:
                lv_data["solvent_model"] = str(sm).lower()
            if lv_data.get("solvent_model") in (None, "", "none"):
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
            fd = FIELD_DEFINITIONS.get(field_name)
            if user_val is not None and user_val != "":
                options = _resolve_field_options(
                    field_name, engine, normalized.get("functional"),
                )
                if options is not None:
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
                        if fd and fd.get("supports_custom") and str(user_val).strip() and len(options) > 1:
                            pass
                        else:
                            errors.append(
                                f"Level '{lid}', field '{field_name}': "
                                f"value '{user_val}' not in allowed options"
                            )
                            continue
                normalized[field_name] = user_val
            else:
                default_val = _resolve_field_default(field_name, engine, normalized.get("functional"))
                normalized[field_name] = default_val

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
        "dispersion": "dispersion",
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

    # Clamp basis/dispersion to functional-allowed values (safety net).
    for _stage, level in converted.items():
        _clamp_to_functional(level, method_key="method")

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
            _clamp_to_functional(d, method_key="functional")
            config["optimize"] = d
        if "single_point" in levels:
            d = dict(levels["single_point"])
            d.pop("charge", None)
            d.pop("multiplicity", None)
            _clamp_to_functional(d, method_key="functional")
            config["sp"] = d
        if "thermo" in levels:
            config["thermo"] = dict(levels["thermo"])
    else:
        config = dict(levels)
        for _key, _level in config.items():
            if isinstance(_level, dict):
                _clamp_to_functional(_level, method_key="functional")
    return config


_LEVEL_TO_CLI_FLAG_MAP: dict[str, str] = {
    "functional": "method",
    "basis": "basis",
    "aux_basis": "aux-basis",
    "ri_approximation": "ri-approximation",
    "dispersion": "dispersion",
    "solvent_model": "solvent-model",
    "solvent": "solvent",
    "temperature": "temperature",
    "pressure": "pressure",
    "scale_factor": "scale-factor",
    "max_steps": "geom-maxiter",
    "opt_convergence": "opt-convergence",
    "recalc_hess": "recalc-hess",
}


def method_levels_to_cli_flags(
    levels: dict[str, Any],
    prefix_map: dict[str, str] | None = None,
) -> list[str]:
    """Convert method levels dict to CLI flag list.

    Args:
        levels: Nested dict - e.g. {"optfreq": {...}, "single_point": {...}}.
        prefix_map: level_id -> CLI flag prefix (default: no prefix for all).

    Returns:
        CLI flag list - e.g. ["--method", "wB97M-V", "--basis", "def2-TZVPP"].
    """
    prefix_map = prefix_map or {}
    cmd: list[str] = []
    for level_id, level_config in levels.items():
        prefix = prefix_map.get(level_id, "")
        for field, value in level_config.items():
            if field == "engine" or value is None or value == "":
                continue
            cli_flag = _LEVEL_TO_CLI_FLAG_MAP.get(field)
            if cli_flag is None:
                continue
            str_value = str(value)
            if field in _CASE_INSENSITIVE_FIELDS:
                str_value = str_value.lower()
            flag = f"--{prefix}{cli_flag}"
            cmd += [flag, str_value]
    return cmd


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
    "FUNCTIONAL_OPTIONS_MAP",
    "METHOD_CATALOG",
    "METHOD_SCHEMAS",
    "WORKFLOW_CATALOG",
    "convert_method_levels_to_protocol_levels",
    "get_method_catalog",
    "get_method_profiles",
    "get_method_schema",
    "get_workflow_by_id",
    "get_workflow_catalog",
    "method_levels_to_cli_flags",
    "method_levels_to_workflow_config",
    "normalize_and_validate_method_config",
]
