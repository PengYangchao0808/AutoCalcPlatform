from __future__ import annotations

import os
import shutil
import subprocess
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
        "visible": True,
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
        "visible": True,
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
        "visible": True,
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
        "visible": True,
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
        "visible": True,
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
        "status": "active",
        "visible": True,
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
        # R21: hidden from the Workbench wizard (legacy entry point),
        # but still executable via direct scheduler/CLI invocation.
        "visible": False,
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
        "visible": True,
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
        # R21: hidden from the Workbench wizard (no first-class UI flow).
        "visible": False,
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
        "visible": True,
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
        "visible": True,
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
        # Phase 1 verification step 4: mechanism is now implemented and active.
        "status": "active",
        "visible": True,
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
        "visible": True,
    },
]

# ── Functional → basis set + dispersion mapping ──────────────────────────
# Each functional defines which basis sets and dispersion corrections are
# chemically valid. The UI filters basis/dispersion dropdowns dynamically
# based on the selected functional.
# NOTE: _ALL_BASIS_SETS is defined *before* FIELD_DEFINITIONS so it can be
# referenced by the "basis" field. It is a ``tuple`` (not ``list``) so that
# the shared reference cannot be mutated in place by any consumer — R24
# recommended this in its "或将其改为 tuple（不可变）" alternative. The
# JSON encoder serialises tuples and lists identically, so the API surface
# is unchanged.

BASIS_CATALOG: dict[str, dict[str, str | None]] = {
    "def2-SV(P)":  {"aux_j": "def2/J", "aux_c": None},
    "def2-SVP":    {"aux_j": "def2/J", "aux_c": "def2-SVP/C"},
    "def2-SVPD":   {"aux_j": "def2/J", "aux_c": "def2-SVPD/C"},
    "def2-TZVP":   {"aux_j": "def2/J", "aux_c": "def2-TZVP/C"},
    "def2-TZVPP":  {"aux_j": "def2/J", "aux_c": "def2-TZVPP/C"},
    "def2-TZVPPD": {"aux_j": "def2/J", "aux_c": "def2-TZVPP/C"},
    "def2-QZVP":   {"aux_j": "def2/J", "aux_c": None},
    "def2-QZVPP":  {"aux_j": "def2/J", "aux_c": "def2-QZVPP/C"},
    "def2-QZVPPD": {"aux_j": "def2/J", "aux_c": "def2-QZVPP/C"},

    "ma-def2-SVP":   {"aux_j": "def2/J", "aux_c": None},
    "ma-def2-TZVP":  {"aux_j": "def2/J", "aux_c": None},
    "ma-def2-TZVPP": {"aux_j": "def2/J", "aux_c": None},
    "ma-def2-QZVPP": {"aux_j": "def2/J", "aux_c": None},

    "cc-pVDZ":     {"aux_j": None, "aux_c": "cc-pVDZ/C"},
    "cc-pVTZ":     {"aux_j": None, "aux_c": "cc-pVTZ/C"},
    "cc-pVQZ":     {"aux_j": None, "aux_c": "cc-pVQZ/C"},
    "cc-pV5Z":     {"aux_j": None, "aux_c": "cc-pV5Z/C"},
    "aug-cc-pVDZ": {"aux_j": None, "aux_c": "aug-cc-pVDZ/C"},
    "aug-cc-pVTZ": {"aux_j": None, "aux_c": "aug-cc-pVTZ/C"},
    "aug-cc-pVQZ": {"aux_j": None, "aux_c": "aug-cc-pVQZ/C"},

    "cc-pwCVDZ": {"aux_j": None, "aux_c": None},
    "cc-pwCVTZ": {"aux_j": None, "aux_c": None},
    "cc-pwCVQZ": {"aux_j": None, "aux_c": None},
    "cc-pCVDZ":  {"aux_j": None, "aux_c": None},
    "cc-pCVTZ":  {"aux_j": None, "aux_c": None},

    "def2-mTZVPP": {"aux_j": None, "aux_c": None},
    "def2-mSVP":   {"aux_j": None, "aux_c": None},
    "mTZVP":       {"aux_j": None, "aux_c": None},
}

_ALL_BASIS_SETS: tuple[str, ...] = tuple(BASIS_CATALOG.keys())

_AUX_J_BASIS_FALLBACK = ["AutoAux", "def2/J"]
_AUX_C_BASIS_FALLBACK = ["AutoAux"]

# v1.3: 3c composite-only basis sets — not shown in non-composite functional options
_COMPOSITE_BASIS_SETS: tuple[str, ...] = ("def2-mTZVPP", "def2-mSVP", "mTZVP")

# ── Phase 4.2: basis-catalog deduplication sentinel ────────────────────
# Instead of storing the full 28-element basis list in every METHOD_META
# entry, we use a module-level sentinel that _derive_functional_options_map
# and get_method_catalog() understand. The API response puts the tuple
# once as a top-level ``basis_catalog`` field; metadata entries that
# reference it carry ``basis_ref: "basis_catalog"`` instead of a
# duplicated array.
_BASIS_CATALOG_REF = "<basis-catalog>"

# ── Per-functional metadata (basis_inline, ri_support, defaults, etc.) ──
# Single source of truth for frontend data-driven UI logic.
# Key = functional name (standard casing).  Use _case_insensitive_get()
# for lookups to tolerate user-input case variance.
#
# FUNCTIONAL_OPTIONS_MAP (below) is auto-derived from this dict to keep the
# two structures permanently in sync — DevDoc §2.1 specifies that
# ``functional_options_map`` values are "由 METHOD_META 自动生成".

METHOD_META: dict[str, dict[str, Any]] = {
    # ── 3c composite methods (built-in basis set, RI fully fixed) ──
    "r2SCAN-3c": {
        "basis_inline": False,
        "ri_support": "composite",
        "basis": ("def2-mTZVPP",),
        "dispersion": ("D4", "none"),
        "builtin_dispersion": "D4",
        "default_basis": "def2-mTZVPP",
        "default_dispersion": "none",
    },
    "PBEh-3c": {
        "basis_inline": False,
        "ri_support": "composite",
        "basis": ("def2-mSVP",),
        "dispersion": ("D3BJ", "none"),
        "builtin_dispersion": "D3BJ",
        "default_basis": "def2-mSVP",
        "default_dispersion": "none",
    },
    "B97-3c": {
        "basis_inline": False,
        "ri_support": "composite",
        "basis": ("mTZVP",),
        "dispersion": ("D3BJ", "none"),
        "builtin_dispersion": "D3BJ",
        "default_basis": "mTZVP",
        "default_dispersion": "none",
    },

    # ── Ordinary hybrid functionals (user-selectable RI, no /C needed) ──
    "B3LYP": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": False,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("none", "D3", "D3BJ", "D4"),
        "builtin_dispersion": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D4",
    },
    "PBE0": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": False,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("none", "D3", "D3BJ", "D4"),
        "builtin_dispersion": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D4",
    },
    "M062X": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": False,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("none", "D3", "D3BJ"),
        "builtin_dispersion": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
    },

    # ── Range-separated single-hybrid functionals ──
    "wB97X-D4": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": False,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("D4", "none"),
        "builtin_dispersion": "D4",
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
    },
    "wB97M-V": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": False,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("VV10", "none"),
        "builtin_dispersion": "VV10",
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
    },

    # ── Double-hybrid functionals (need /J + /C) ──
    "PWPB95": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": True,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("D3BJ", "D4", "D3", "none"),
        "builtin_dispersion": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D3BJ",
    },

    # ── Post-HF wavefunction methods ──
    "DLPNO-CCSD(T)": {
        "basis_inline": False,
        "ri_support": "automatic",
        "needs_aux_c": True,
        "basis": ("def2-TZVPP",),
        "dispersion": ("none",),
        "builtin_dispersion": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "none",
        "default_aux_j": "def2/J",
        "default_aux_c": "def2-TZVPP/C",
    },
}


def _derive_functional_options_map() -> dict[str, dict[str, list[str]]]:
    """Project ``METHOD_META`` into the legacy ``{func: {basis, dispersion}}``
    shape consumed by the frontend dropdown filter.

    Tuples are converted to lists so the JSON payload serialises as an
    array (the API contract is unchanged).  Phase 4.2: entries whose
    ``basis`` is the ``_BASIS_CATALOG_REF`` sentinel are expanded to the
    full ``_ALL_BASIS_SETS`` list automatically.
    """
    out: dict[str, dict[str, list[str]]] = {}
    for func, meta in METHOD_META.items():
        raw_basis = meta.get("basis", ())
        if raw_basis is _BASIS_CATALOG_REF:
            basis_list = list(_ALL_BASIS_SETS)
            ri_support = meta.get("ri_support", "user")
            if ri_support != "composite":
                basis_list = [b for b in basis_list if b not in _COMPOSITE_BASIS_SETS]
        else:
            basis_list = list(raw_basis)
        out[func] = {
            "basis": basis_list,
            "dispersion": list(meta.get("dispersion", ())),
        }
    return out


FUNCTIONAL_OPTIONS_MAP: dict[str, dict[str, list[str]]] = _derive_functional_options_map()

FIELD_DEFINITIONS: dict[str, Any] = {
    "functional": {
        "type": "select",
        "per_backend": {
            "orca": [
                "r2SCAN-3c", "PBEh-3c", "B97-3c",
                "B3LYP", "PBE0", "M062X",
                "wB97X-D4", "wB97M-V",
                "PWPB95",
                "DLPNO-CCSD(T)",
            ],
            "xtb": ["GFN0-xTB", "GFN1-xTB", "GFN2-xTB"],
        },
        "default": {"*": "r2SCAN-3c"},
    },
    "basis": {
        "type": "select",
        "per_backend": {
            "orca": _BASIS_CATALOG_REF,
        },
        "default": {"*": ""},
        "supports_custom": True,
    },
    "dispersion": {
        "type": "select",
        "options": ["none", "D3", "D3BJ", "D4", "VV10"],
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
        "advanced": True,
        "options": ["SG1", "Fine", "UltraFine", "SuperFine"],
        "default": {"*": "UltraFine"},
    },
    "scf_convergence": {
        "type": "select",
        "advanced": True,
        "options": ["Normal", "Tight", "VeryTight"],
        "default": {"*": "Tight"},
    },
    "opt_convergence": {
        "type": "select",
        "advanced": True,
        "options": ["Loose", "Normal", "Tight", "VeryTight"],
        "default": {"*": "Tight"},
    },
    "max_steps": {"type": "int", "advanced": True, "min": 1, "max": 10000, "default": {"*": 100}},
    "recalc_hess": {
        "type": "int",
        "advanced": True,
        "label": "Hessian Recalc Interval",
        "min": 1,
        "max": 1000,
        "default": {"*": 10},
    },
    "temperature": {"type": "float", "min": 0, "max": 10000, "default": {"*": 298.15}, "unit": "K"},
    "pressure": {"type": "float", "min": 0, "max": 100000, "default": {"*": 1.0}, "unit": "atm"},
    "scale_factor": {"type": "float", "advanced": True, "min": 0, "max": 1.0, "default": {"*": 1.0}},
    "ewin": {
        "type": "float",
        "label": "Energy Window",
        "min": 0,
        "default": {"*": 6.0},
        "unit": "kcal/mol",
    },
    "refinement_threshold": {
        "type": "float",
        "label": "Boltzmann Cutoff",
        "label_zh": "Boltzmann 截断",
        "min": 0.01,
        "max": 1.0,
        "default": {"*": 0.99},
        "step": 0.01,
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
    "opt_level": {
        "type": "select",
        "per_backend": {"xtb": ["crude", "sloppy", "loose", "normal", "tight", "vtight", "extreme"]},
        "default": {"*": "normal"},
    },
    "aux_j_basis": {
        "type": "select",
        "advanced": True,
        "per_backend": {"orca": _AUX_J_BASIS_FALLBACK},
        "default": {"*": "AutoAux"},
        "supports_custom": True,
        "options_source": "dynamic_aux_basis",
        "aux_kind": "j",
        "option_meta": {
            "AutoAux": "ORCA auto-generates /J auxiliary basis (recommended default)",
            "def2/J":  "Weigend universal /J fitting basis (works with any main basis)",
        },
    },
    "aux_c_basis": {
        "type": "select",
        "advanced": True,
        "per_backend": {"orca": _AUX_C_BASIS_FALLBACK},
        "default": {"*": "AutoAux"},
        "supports_custom": True,
        "options_source": "dynamic_aux_basis",
        "aux_kind": "c",
        "option_meta": {
            "AutoAux": "ORCA auto-generates /C auxiliary basis",
        },
    },
    "ri_approximation": {
        "type": "select",
        "advanced": True,
        "per_backend": {"orca": ["none", "RI", "RIJCOSX", "RIJK"]},
        "default": {"*": "RIJCOSX"},
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
                "fields": ["gfn", "ewin", "rthr", "solvent_model"],
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                    "max_steps",
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
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
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
                    "dispersion",
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
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                "fields": ["gfn", "opt_level", "solvent_model", "solvent", "max_steps"],
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
                "fields": ["ewin", "refinement_threshold"],
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
                        "refinement_threshold": 0.99,
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
                        "refinement_threshold": 0.99,
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
                        "refinement_threshold": 0.99,
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
                "fields": ["ewin", "refinement_threshold"],
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
                    "ri_approximation",
                    "aux_j_basis",
                    "aux_c_basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
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
                        "refinement_threshold": 0.99,
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
                        "dispersion": "none",
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                        "refinement_threshold": 0.99,
                    },
                    "refinement_sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                        "refinement_threshold": 0.99,
                    },
                    "dft_opt": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
                        "dispersion": "none",
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
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

# ── Backend discovery (R22 / Phase 4.5) ────────────────────────────────
# Dynamically resolves the availability and version of every external binary
# that the platform depends on so the frontend can surface pre-flight
# environment checks before submitting a workflow.

_BACKEND_BINARIES: dict[str, dict[str, Any]] = {
    "orca":     {"label": "ORCA",           "env_var": "CONFSEARCH_ORCA_PATH",    "default": "orca"},
    "xtb":      {"label": "xTB (GFN2)",      "env_var": "CONFSEARCH_XTB_PATH",     "default": "xtb"},
    "crest":    {"label": "CREST",           "env_var": "CONFSEARCH_CREST_PATH",   "default": "crest"},
    "censo":    {"label": "CENSO",           "env_var": "CONFSEARCH_CENSO_PATH",   "default": "censo"},
    "shermo":   {"label": "Shermo",          "env_var": "CONFSEARCH_SHERMO_PATH",  "default": "Shermo"},
    "isostat":  {"label": "ISOSTAT",         "env_var": "CONFSEARCH_ISOSTAT_PATH", "default": "isostat"},
}

_BACKEND_SUPPORTS: dict[str, list[str]] = {
    "orca":    ["singlepoint", "optimize", "frequency", "optfreq", "nmr"],
    "xtb":     ["singlepoint", "optimize"],
    "crest":   ["conformer_search"],
    "censo":   ["censo_refinement", "censo_energy"],
    "shermo":  ["thermo"],
    "isostat": ["clustering"],
}


def _resolve_backend_path(bid: str) -> str:
    """Resolve binary path for backend *bid* using an env-var override or shutil.which."""
    info = _BACKEND_BINARIES.get(bid, {})
    env_path = os.environ.get(info.get("env_var", ""), "")
    if env_path:
        return env_path
    default = info.get("default", bid)
    return shutil.which(default) or default


def _detect_backend_version(bid: str, binary_path: str) -> str | None:
    """Try to detect the version of backend *bid* by running it with a known flag."""
    version_flags: dict[str, str] = {
        "orca":     "",
        "xtb":      "--version",
        "crest":    "--version",
        "censo":    "--version",
        "shermo":   "",
        "isostat":  "",
    }
    flag = version_flags.get(bid)
    if flag is None:
        return None
    try:
        result = subprocess.run(
            [binary_path, flag], capture_output=True, text=True,
            timeout=10, env={**os.environ, "OMP_NUM_THREADS": "1"},
        )
        first_line = (result.stdout or result.stderr).split("\n")[0].strip()
        if first_line and len(first_line) < 128:
            return first_line
    except Exception:
        pass
    return None


def _discover_backends() -> list[dict[str, Any]]:
    """Build the backends list with dynamic availability and version metadata."""
    backends: list[dict[str, Any]] = []
    for bid, binfo in _BACKEND_BINARIES.items():
        path = _resolve_backend_path(bid)
        available = shutil.which(path) is not None if path else False
        version = _detect_backend_version(bid, path) if available else None
        backends.append({
            "id": bid,
            "label": binfo["label"],
            "supports": _BACKEND_SUPPORTS.get(bid, []),
            "path": path,
            "available": available,
            "version": version,
        })
    return backends


METHOD_CATALOG: dict[str, Any] = {
    "backends": _discover_backends(),
    "field_definitions": FIELD_DEFINITIONS,
    "method_schemas": METHOD_SCHEMAS,
    "functional_options_map": FUNCTIONAL_OPTIONS_MAP,
    "method_meta": METHOD_META,
}


# ---------------------------------------------------------------------------
# 验证 + 标准化函数
# ---------------------------------------------------------------------------


def _case_insensitive_get(mapping: dict[str, Any], key: str) -> Any | None:
    """Look up a key in *mapping* ignoring case.

    Returns the value for the first key whose lowercased form matches
    *key*.lower().  Falls back to ``None`` when no match is found.
    """
    if key in mapping:
        return mapping[key]
    kl = key.lower()
    for k, v in mapping.items():
        if k.lower() == kl:
            return v
    return None


def _resolve_field_options(
    field_name: str, engine: str, functional: str | None = None,
    basis: str | None = None,
) -> list[str] | None:
    """Return allowed options for a field given a specific engine.

    For ``basis`` and ``dispersion`` fields, when a functional is provided
    the options are resolved from ``FUNCTIONAL_OPTIONS_MAP`` so that only
    chemically valid combinations are offered.

    For ``aux_j_basis`` / ``aux_c_basis``, dynamic options are generated
    from ``BASIS_CATALOG`` based on the current *basis*.
    """
    if functional and field_name in ("basis", "dispersion"):
        mapping = FUNCTIONAL_OPTIONS_MAP.get(functional)
        if mapping and field_name in mapping:
            return mapping[field_name]
    fd = FIELD_DEFINITIONS.get(field_name)
    if not fd:
        return None

    if (
        field_name in ("aux_j_basis", "aux_c_basis")
        and fd.get("options_source") == "dynamic_aux_basis"
    ):
        aux_kind = fd.get("aux_kind")
        fallback = fd.get("per_backend", {}).get(engine, [])
        if not isinstance(fallback, list):
            fallback = []

        if functional:
            meta = _case_insensitive_get(METHOD_META, functional)
            if meta and field_name == "aux_c_basis" and not meta.get("needs_aux_c", False):
                return []

        if basis and basis in BASIS_CATALOG:
            tailored = BASIS_CATALOG[basis].get(f"aux_{aux_kind}")
            if tailored and tailored not in fallback:
                return [tailored] + list(fallback)
        return list(fallback)

    if "options" in fd:
        return fd["options"]
    if "per_backend" in fd:
        opts = fd["per_backend"].get(
            engine, list(fd["per_backend"].values())[0] if fd["per_backend"] else []
        )
        if opts is _BASIS_CATALOG_REF:
            return list(_ALL_BASIS_SETS)
        return opts
    return None


def _resolve_field_default(
    field_name: str, engine: str, functional: str | None = None,
    basis: str | None = None,
) -> Any:
    """Return the default value for a field, respecting functional context.

    Resolution order:
      1. ``METHOD_META[functional]`` (functional-level defaults for
         ``basis`` / ``dispersion``; RI/aux forced to none/empty when the
         functional declares ``ri_support != "user"``).
      2. ``BASIS_CATALOG`` derivation for ``aux_j_basis`` / ``aux_c_basis``.
      3. ``FUNCTIONAL_OPTIONS_MAP[functional]`` restriction for
         ``basis`` / ``dispersion`` (first allowed option, or the global
         default when it is also allowed).
      4. ``FIELD_DEFINITIONS`` global default for the field.
    """
    fd = FIELD_DEFINITIONS.get(field_name)
    global_default: Any = ""
    if fd:
        dflt = fd.get("default", {})
        if isinstance(dflt, dict):
            global_default = dflt.get(engine, dflt.get("*", ""))
        else:
            global_default = dflt

    if functional:
        meta = _case_insensitive_get(METHOD_META, functional)
        if meta:
            if field_name == "basis" and meta.get("default_basis") is not None:
                return meta["default_basis"]
            if field_name == "dispersion" and meta.get("default_dispersion") is not None:
                return meta["default_dispersion"]

            ri_support = meta.get("ri_support", "user")
            if ri_support in ("composite", "automatic"):
                if field_name == "ri_approximation":
                    return "none"
                if field_name in ("aux_j_basis", "aux_c_basis"):
                    return ""

            if field_name == "aux_j_basis" and basis:
                basis_meta = BASIS_CATALOG.get(basis)
                if basis_meta and basis_meta.get("aux_j"):
                    return basis_meta["aux_j"]
                return global_default
            if field_name == "aux_c_basis":
                if not meta.get("needs_aux_c", False):
                    return ""
                if basis:
                    basis_meta = BASIS_CATALOG.get(basis)
                    if basis_meta and basis_meta.get("aux_c"):
                        return basis_meta["aux_c"]
                    return global_default

        if field_name in ("basis", "dispersion"):
            mapping = _case_insensitive_get(FUNCTIONAL_OPTIONS_MAP, functional)
            if mapping and field_name in mapping:
                opts = mapping[field_name]
                if opts:
                    if global_default and global_default in opts:
                        return global_default
                    return opts[0]

    return global_default


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
    meta = _case_insensitive_get(METHOD_META, func) if func else None
    ri_support = (meta or {}).get("ri_support", "user")
    if ri_support == "automatic":
        level["aux_c_basis"] = level.pop("aux_basis")
    else:
        level["aux_j_basis"] = level.pop("aux_basis")


def _clamp_to_functional(level: dict[str, Any], method_key: str) -> None:
    """Clamp basis/dispersion to the functional's allowed values.

    Safety net: ensures execution-path converters never pass invalid
    basis/dispersion combinations to backends, even if validation was
    bypassed.

    Also migrates legacy ``aux_basis`` to ``aux_j_basis`` / ``aux_c_basis``
    and enforces ``ri_support`` semantics (composite/automatic → clear RI/aux).

    Comparison is case-insensitive (R20): a current value that matches an
    allowed entry case-insensitively is normalised to the canonical casing
    of that entry (``allowed[idx]``); a current value with no
    case-insensitive match is replaced with ``allowed[0]``. This keeps the
    downstream CLI emit (``.lower()`` for ``_CASE_INSENSITIVE_FIELDS``)
    consistent with the catalog's canonical casing.
    """
    _migrate_legacy_aux_basis(level, method_key)

    func_name = level.get(method_key)
    if not func_name:
        return
    meta = _case_insensitive_get(METHOD_META, func_name)
    mapping = _case_insensitive_get(FUNCTIONAL_OPTIONS_MAP, func_name)
    ri_support = (meta or {}).get("ri_support", "user")

    if ri_support in ("composite", "automatic"):
        level["ri_approximation"] = "none"
        level["aux_j_basis"] = ""
        if ri_support == "composite":
            level["aux_c_basis"] = ""

    if not mapping:
        return
    for key in ("basis", "dispersion"):
        if key not in mapping or key not in level:
            continue
        allowed = mapping[key]
        current = level[key]
        if not current or current == "__custom__":
            continue
        allowed_lower = [str(a).lower() for a in allowed]
        try:
            idx = allowed_lower.index(str(current).lower())
        except ValueError:
            level[key] = allowed[0]
            continue
        level[key] = allowed[idx]

    # Only clamp aux fields for "user" ri_support methods. For "composite" and
    # "automatic", the values are either blanked above or intentionally set by
    # legacy migration (_migrate_legacy_aux_basis) — do not override.
    if ri_support != "user":
        return
    basis = level.get("basis", "")
    for aux_field in ("aux_j_basis", "aux_c_basis"):
        if aux_field in level and level[aux_field]:
            allowed = _resolve_field_options(aux_field, "orca", func_name, basis) or []
            if level[aux_field] not in allowed:
                level[aux_field] = _resolve_field_default(aux_field, "orca", func_name, basis)


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
        if "aux_basis" in level and "aux_j_basis" not in level and "aux_c_basis" not in level:
            func = level.get("functional")
            meta = _case_insensitive_get(METHOD_META, func) if func else None
            ri_support = (meta or {}).get("ri_support", "user")
            if ri_support == "automatic":
                level["aux_c_basis"] = level.pop("aux_basis")
            else:
                level["aux_j_basis"] = level.pop("aux_basis")
    return method


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


def _match_option_case_insensitive(
    options: list[str], value: Any,
) -> tuple[int, str] | None:
    """Case-insensitively match *value* against *options*.

    Returns ``(index, canonical_value)`` on match, ``None`` otherwise.
    Centralises the lookup pattern used by ``normalize_and_validate_method_config``
    for ``_CASE_INSENSITIVE_FIELDS`` (solvent_model, dispersion) and the
    ``functional`` field, which shares the same canonical-normalisation
    semantics but is NOT in ``_CASE_INSENSITIVE_FIELDS`` (CLI emit must
    preserve its case).
    """
    lower_options = [str(o).lower() for o in options]
    try:
        idx = lower_options.index(str(value).lower())
    except ValueError:
        return None
    return idx, options[idx]


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
                    basis=normalized.get("basis") or user_lv.get("basis"),
                )
                if options is not None:
                    if field_name in _CASE_INSENSITIVE_FIELDS or field_name == "functional":
                        # R18/R20: case-insensitive match against the catalog's
                        # canonical-cased option list. ``solvent_model`` is
                        # additionally stored lowercased (legacy convention
                        # downstream code relies on); every other field
                        # (dispersion, functional) normalises to the canonical
                        # casing of the matched option. ``functional`` is NOT
                        # in ``_CASE_INSENSITIVE_FIELDS`` because that set also
                        # drives CLI emit (``.lower()``) in
                        # ``method_levels_to_cli_flags``, which would corrupt
                        # the method name for ORCA.
                        match = _match_option_case_insensitive(options, user_val)
                        if match is None:
                            errors.append(
                                f"Level '{lid}', field '{field_name}': "
                                f"value '{user_lv.get(field_name)}' not in allowed options"
                            )
                            continue
                        _idx, canonical = match
                        if field_name == "solvent_model":
                            user_val = str(user_val).lower()
                        else:
                            user_val = canonical
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
                default_val = _resolve_field_default(
                    field_name, engine, normalized.get("functional"),
                    basis=normalized.get("basis"),
                )
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
        # Extended mappings (R12): cover all known level_ids so the
        # converter no longer silently drops levels it doesn't recognise.
        "refinement_sp": "single_point",
        "censo": "single_point",
        "preopt": "optimization",
        "crest": "crest",
        "optfreq": "optimization",
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
    for old_stage, old_level in levels.items():
        if not isinstance(old_level, dict):
            continue
        # Unknown level_ids are passed through under their original name
        # (R12: "缺失的 level_id 不丢弃") so user-defined/custom stages
        # remain accessible to downstream consumers.
        new_stage = stage_mapping.get(old_stage, old_stage)
        # Merge field-by-field instead of wholesale replace so that two
        # source levels mapped to the same destination stage do not
        # silently clobber each other (e.g. confsearch ``preopt`` +
        # ``dft_opt`` both → ``optimization``; censo_energy ``censo`` +
        # ``refinement_sp`` both → ``single_point``). Later source levels
        # win on overlapping fields, which matches the schema's natural
        # ordering (DFT method info comes after xTB pre-opt, refinement
        # method info comes after CENSO preset).
        new_level = converted.setdefault(new_stage, {})
        for old_key, new_key in field_mapping.items():
            if old_key in old_level:
                value = old_level[old_key]
                if old_key == "solvent_model":
                    value = str(value).lower()
                new_level[new_key] = value

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
            _clamp_to_functional(d, method_key="functional")
            config["optimize"] = d
        if "single_point" in levels:
            d = dict(levels["single_point"])
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
    "aux_j_basis": "aux-j-basis",
    "aux_c_basis": "aux-c-basis",
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
    """Return the method catalog with basis deduplication applied.

    The raw ``METHOD_CATALOG`` stores the ``_BASIS_CATALOG_REF`` sentinel
    in places that would otherwise duplicate the full basis list. This
    function expands the sentinel and adds top-level ``basis_catalog``
    (v1.0 compat, array) and ``basis_catalog_v2`` (v1.1, object with
    aux_j/aux_c metadata) fields.
    """
    import copy
    catalog = copy.deepcopy(METHOD_CATALOG)

    basis_list = list(_ALL_BASIS_SETS)
    catalog["basis_catalog"] = basis_list
    catalog["basis_catalog_v2"] = dict(BASIS_CATALOG)

    meta = catalog.get("method_meta")
    if isinstance(meta, dict):
        for func_meta in meta.values():
            if isinstance(func_meta, dict) and func_meta.get("basis") is _BASIS_CATALOG_REF:
                del func_meta["basis"]
                func_meta["basis_ref"] = "basis_catalog"

    fd = catalog.get("field_definitions")
    if isinstance(fd, dict):
        for field_name, field_def in fd.items():
            if isinstance(field_def, dict):
                pb = field_def.get("per_backend")
                if isinstance(pb, dict):
                    for eng, val in list(pb.items()):
                        if val is _BASIS_CATALOG_REF:
                            pb[eng] = basis_list

    return catalog


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
    "BASIS_CATALOG",
    "FIELD_DEFINITIONS",
    "FUNCTIONAL_OPTIONS_MAP",
    "METHOD_CATALOG",
    "METHOD_META",
    "METHOD_SCHEMAS",
    "WORKFLOW_CATALOG",
    "_case_insensitive_get",
    "_derive_functional_options_map",
    "_match_option_case_insensitive",
    "convert_method_levels_to_protocol_levels",
    "get_method_catalog",
    "get_method_profiles",
    "get_method_schema",
    "get_workflow_by_id",
    "get_workflow_catalog",
    "method_levels_to_cli_flags",
    "method_levels_to_workflow_config",
    "normalize_and_validate_method_config",
    "normalize_legacy_method",
]
