# ruff: noqa: E501
# pyright: reportAny=false, reportExplicitAny=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportMissingTypeArgument=false, reportPrivateUsage=false, reportUnusedVariable=false, reportRedeclaration=false, reportUnnecessaryIsInstance=false, reportUnreachable=false, reportUnusedParameter=false, reportImplicitStringConcatenation=false, reportPrivateImportUsage=false, reportArgumentType=false

from __future__ import annotations

from typing import Any

from acp.chem.composition import normalize_recalc_hess

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
        "id": "scan",
        "label": "Relaxed Scan",
        "label_zh": "松弛扫描",
        "category": "simple",
        "description": "Scan an internal coordinate while relaxing the remaining geometry",
        "method_schema_id": "dft_scan",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "status": "active",
        "visible": True,
    },
    {
        "id": "irc",
        "label": "Intrinsic Reaction Coordinate",
        "label_zh": "内禀反应坐标",
        "category": "simple",
        "description": "Follow both directions of a transition-state IRC",
        "method_schema_id": "irc",
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
        "description": (
            "Retired in the 2026-08 refactor; use optimize + frequency or "
            "BatchOptimize; historical jobs are read-only"
        ),
        "description_zh": "已由 BatchOptimize 取代，历史任务只读",
        "method_schema_id": "dft_optfreq",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "reason": "2026-08 refactor",
        "replacements": ["optimize + frequency", "BatchOptimize"],
        "status": "retired",
        "visible": False,
    },
    {
        "id": "optfreqsp",
        "label": "Opt+Freq+SP+Thermo",
        "label_zh": "优化+频率+单点+热化学",
        "category": "simple",
        "description": (
            "Retired in the 2026-08 refactor; use BatchOptimize for optimization, "
            "frequency, single-point, and thermochemistry; historical jobs are read-only"
        ),
        "description_zh": "已由 BatchOptimize 取代，历史任务只读",
        "method_schema_id": "dft_optfreqsp",
        "default_backend": "orca",
        "requires_binaries": ["orca", "shermo"],
        "reason": "2026-08 refactor",
        "replacements": ["BatchOptimize"],
        "status": "retired",
        "visible": False,
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
        # Retired (Phase A): the standalone conformer workflow has been
        # removed. The entry is kept so historical jobs still resolve a
        # label/schema; ``status="retired"`` makes ``_derive_supported_workflows``
        # drop it from the active set (submission auto-rejected).
        "status": "retired",
        "visible": False,
    },
    {
        "id": "nmr",
        "label": "NMR + DP4/DP5",
        "label_zh": "NMR + DP4/DP5 \u7acb\u4f53\u5f52\u5c5e",
        "category": "preset",
        "description": (
            "GIAO NMR shieldings + Boltzmann averaging + DP4/DP5 stereochemistry assignment"
        ),
        "method_schema_id": "nmr",
        "default_backend": "orca",
        "requires_binaries": ["crest", "censo", "orca"],
        # Reactivated (P1a, 2026-08-07): NMR + DP4/DP5 workflow revived.
        # ``status="active"`` makes ``_derive_supported_workflows`` expose
        # it (DevDoc §11). The placeholder error model ships with P1a;
        # P1b swaps in the trained Goodman parameters.
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
        # Retired (Phase A): benchmark workflow removed; entry kept for
        # historical job display only.
        "status": "retired",
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
        # Retired (Confsearch v1.0, 2026-08-23): unified into
        # Confsearch + protocol=censo-crest + refinement_policy=screen.
        "status": "retired",
        "visible": False,
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
        # Retired (Confsearch v1.0, 2026-08-23): rank1-only maps to
        # Confsearch + censo-crest + rank1; full-ensemble to cumulative-99.
        "status": "retired",
        "visible": False,
    },
    {
        "id": "xtbmd_censo_energy",
        "label": "xTB-MD CENSO Energy",
        "label_zh": "xTB 动力学构象自由能",
        "category": "preset",
        "description": "GFN-FF MD sampling → GFN1 batch opt → isostat dedup → CENSO → fine DFT",
        "method_schema_id": "xtbmd_censo_energy",
        "default_backend": "censo",
        "requires_binaries": ["xtb", "isostat", "censo", "orca"],
        # Retired (Confsearch v1.0, 2026-08-23): the full chain is now the
        # single protocol Confsearch + protocol=xtbmd-censo.
        "status": "retired",
        "visible": False,
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
        # Retired for new runs (Confsearch v1.0, 2026-08-23): the one-shot
        # S0→S4 study is replaced by the four independent stage workflows
        # Confsearch / PESsearch / Lowconfirm / Highconfirm. Historical
        # studies remain viewable read-only.
        "status": "retired",
        "visible": False,
    },
    {
        "id": "custom_sequence",
        "label": "Custom Task Sequence",
        "label_zh": "自定义任务序列",
        "category": "custom",
        "description": "Build a linear pipeline of calculation blocks",
        "method_schema_id": "custom",
        "default_backend": "",
        "requires_binaries": [],
        "status": "planned",
        "visible": False,
    },
    {
        "id": "mech-conf",
        "label": "Mechanism Conformer / Stable State",
        "label_zh": "机理构象 / 稳定态",
        "category": "mechanism",
        "description": "Standalone conformer search for one mechanism stable state",
        "method_schema_id": "mech_conf",
        "default_backend": "orca",
        "requires_binaries": ["crest", "orca"],
        # Retired (Confsearch v1.0, 2026-08-23): superseded by Confsearch.
        "status": "retired",
        "visible": False,
    },
    {
        "id": "mech-step",
        "label": "Mechanism Elementary Step",
        "label_zh": "机理基元步骤",
        "category": "mechanism",
        "description": "Elementary step: PEB path -> coarse refine -> IRC -> endpoints",
        "method_schema_id": "mech_step",
        "default_backend": "orca",
        "requires_binaries": ["orca", "xtb"],
        # Retired (Confsearch v1.0, 2026-08-23): split into PESsearch (S2)
        # + Lowconfirm (S3).
        "status": "retired",
        "visible": False,
    },
    {
        "id": "mech-confirm",
        "label": "Mechanism High-Fidelity Confirmation",
        "label_zh": "机理高精度确认",
        "category": "mechanism",
        "description": "High-fidelity confirmation of one elementary-step artifact",
        "method_schema_id": "mech_confirm",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        # Retired (Confsearch v1.0, 2026-08-23): superseded by Highconfirm (S4).
        "status": "retired",
        "visible": False,
    },
    {
        "id": "mech-chain",
        "label": "Mechanism Chain",
        "label_zh": "机理模块链",
        "category": "mechanism",
        "description": "Declarative composition of standalone mechanism modules",
        "method_schema_id": "mech_chain",
        "default_backend": "",
        "requires_binaries": [],
        # Retired (Confsearch v1.0, 2026-08-23): the four-stage manual flow
        # (Confsearch → PESsearch → Lowconfirm → Highconfirm) replaces
        # module chaining.
        "status": "retired",
        "visible": False,
    },
    {
        "id": "Confsearch",
        "label": "Conformer Search",
        "label_zh": "构象搜索",
        "category": "stages",
        "description": (
            "Unified conformer search + energies: protocols xtb-crest / "
            "xtb-md / censo-crest / xtbmd-censo"
        ),
        "method_schema_id": "confsearch_unified",
        "default_backend": "censo",
        "requires_binaries": ["crest", "xtb", "isostat", "censo", "orca"],
        "status": "active",
        "visible": True,
    },
    {
        "id": "PESsearch",
        "label": "PES Search",
        "label_zh": "势能面搜索",
        "category": "stages",
        "description": "One-dimensional PES scan from an XYZ structure with atom-coordinate selection (S2)",
        "description_zh": "以 XYZ 结构为输入的单坐标势能面扫描，可在结构预览中选择原子或键（S2）",
        "method_schema_id": "pes_scan",
        "default_backend": "orca",
        "requires_binaries": ["orca", "xtb"],
        "status": "active",
        "visible": True,
    },
    {
        "id": "BatchOptimize",
        "label": "Batch Optimization",
        "label_zh": "批量优化",
        "category": "preset",
        "description": "Batch optimization and optional frequency, single-point, and thermochemistry steps",
        "description_zh": "批量执行结构优化，并可选执行频率、单点能和热化学步骤",
        "method_schema_id": "batch_optimize",
        "default_backend": "orca",
        "requires_binaries": ["orca", "shermo"],
        "status": "active",
        "visible": True,
    },
    {
        "id": "Lowconfirm",
        "label": "Low Confirmation",
        "label_zh": "粗优化",
        "category": "stages",
        "description": (
            "Retired in the 2026-08 refactor; use BatchOptimize and the standalone "
            "IRC workflow when needed; historical jobs are read-only"
        ),
        "description_zh": "已由 BatchOptimize 取代，历史任务只读",
        "method_schema_id": "low_confirm",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "reason": "2026-08 refactor",
        "replacements": ["BatchOptimize", "irc"],
        "status": "retired",
        "visible": False,
    },
    {
        "id": "Highconfirm",
        "label": "High Confirmation",
        "label_zh": "精细优化",
        "category": "stages",
        "description": (
            "Retired in the 2026-08 refactor; use BatchOptimize and the standalone "
            "IRC workflow when needed; historical jobs are read-only"
        ),
        "description_zh": "已由 BatchOptimize 取代，历史任务只读",
        "method_schema_id": "high_confirm",
        "default_backend": "orca",
        "requires_binaries": ["orca"],
        "reason": "2026-08 refactor",
        "replacements": ["BatchOptimize", "irc"],
        "status": "retired",
        "visible": False,
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
    "def2-SV(P)": {"aux_j": "def2/J", "aux_c": None},
    "def2-SVP": {"aux_j": "def2/J", "aux_c": "def2-SVP/C"},
    "def2-SVPD": {"aux_j": "def2/J", "aux_c": "def2-SVPD/C"},
    "def2-TZVP": {"aux_j": "def2/J", "aux_c": "def2-TZVP/C"},
    "def2-TZVPP": {"aux_j": "def2/J", "aux_c": "def2-TZVPP/C"},
    "def2-TZVPPD": {"aux_j": "def2/J", "aux_c": "def2-TZVPP/C"},
    "def2-QZVP": {"aux_j": "def2/J", "aux_c": None},
    "def2-QZVPP": {"aux_j": "def2/J", "aux_c": "def2-QZVPP/C"},
    "def2-QZVPPD": {"aux_j": "def2/J", "aux_c": "def2-QZVPP/C"},
    "ma-def2-SVP": {"aux_j": "def2/J", "aux_c": None},
    "ma-def2-TZVP": {"aux_j": "def2/J", "aux_c": None},
    "ma-def2-TZVPP": {"aux_j": "def2/J", "aux_c": None},
    "ma-def2-QZVPP": {"aux_j": "def2/J", "aux_c": None},
    "cc-pVDZ": {"aux_j": None, "aux_c": "cc-pVDZ/C"},
    "cc-pVTZ": {"aux_j": None, "aux_c": "cc-pVTZ/C"},
    "cc-pVQZ": {"aux_j": None, "aux_c": "cc-pVQZ/C"},
    "cc-pV5Z": {"aux_j": None, "aux_c": "cc-pV5Z/C"},
    "aug-cc-pVDZ": {"aux_j": None, "aux_c": "aug-cc-pVDZ/C"},
    "aug-cc-pVTZ": {"aux_j": None, "aux_c": "aug-cc-pVTZ/C"},
    "aug-cc-pVQZ": {"aux_j": None, "aux_c": "aug-cc-pVQZ/C"},
    "cc-pwCVDZ": {"aux_j": None, "aux_c": None},
    "cc-pwCVTZ": {"aux_j": None, "aux_c": None},
    "cc-pwCVQZ": {"aux_j": None, "aux_c": None},
    "cc-pCVDZ": {"aux_j": None, "aux_c": None},
    "cc-pCVTZ": {"aux_j": None, "aux_c": None},
    "def2-mTZVPP": {"aux_j": None, "aux_c": None},
    "def2-mSVP": {"aux_j": None, "aux_c": None},
    "mTZVP": {"aux_j": None, "aux_c": None},
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
    # Goodman GIAO NMR level (DP4/DP5 error model) — Pople-style basis,
    # no dispersion correction in the original parametrisation.
    "mPW1PW91": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": False,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("none", "D3", "D3BJ", "D4"),
        "builtin_dispersion": None,
        "default_basis": "6-311G(d)",
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
    "revDSD-PBEP86": {
        "basis_inline": True,
        "ri_support": "user",
        "needs_aux_c": True,
        "basis": _BASIS_CATALOG_REF,
        "dispersion": ("D4", "D3BJ", "none"),
        "builtin_dispersion": None,
        "default_basis": "def2-TZVPP",
        "default_dispersion": "D4",
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
        "label": "Electronic Structure Method",
        "label_zh": "电子结构方法",
        "per_backend": {
            "orca": [
                "r2SCAN-3c",
                "PBEh-3c",
                "B97-3c",
                "B3LYP",
                "PBE0",
                "M062X",
                "mPW1PW91",
                "wB97X-D4",
                "wB97M-V",
                "PWPB95",
                "revDSD-PBEP86",
                "DLPNO-CCSD(T)",
            ],
            "xtb": ["GFN0-xTB", "GFN1-xTB", "GFN2-xTB"],
        },
        "default": {"*": "r2SCAN-3c"},
    },
    "basis": {
        "type": "select",
        "label": "Basis Set",
        "label_zh": "基组",
        "per_backend": {
            "orca": _BASIS_CATALOG_REF,
        },
        "default": {"*": ""},
        "supports_custom": True,
    },
    "dispersion": {
        "type": "select",
        "label": "Dispersion Correction",
        "label_zh": "色散校正",
        "options": ["none", "D3", "D3BJ", "D4", "VV10"],
        "option_labels_zh": {"none": "无", "D3": "D3", "D3BJ": "D3BJ", "D4": "D4", "VV10": "VV10"},
        "default": {"*": "D4"},
    },
    "solvent_model": {
        "type": "select",
        "label": "Solvation Model",
        "label_zh": "溶剂模型",
        "per_backend": {
            "orca": ["none", "CPCM", "SMD"],
            "xtb": ["none", "ALPB", "GBSA"],
        },
        "option_labels_zh": {
            "none": "无",
            "CPCM": "CPCM（连续介质）",
            "SMD": "SMD（溶剂化）",
            "ALPB": "ALPB",
            "GBSA": "GBSA",
        },
        "default": {"*": "none"},
    },
    "solvent": {
        "type": "select",
        "label": "Solvent",
        "label_zh": "溶剂",
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
        "option_labels_zh": {
            "none": "无",
            "water": "水",
            "methanol": "甲醇",
            "ethanol": "乙醇",
            "acetone": "丙酮",
            "dichloromethane": "二氯甲烷",
            "toluene": "甲苯",
            "THF": "四氢呋喃（THF）",
            "DMSO": "二甲基亚砜（DMSO）",
            "acetonitrile": "乙腈",
            "chloroform": "氯仿",
            "hexane": "正己烷",
            "benzene": "苯",
        },
        "default": {"*": "none"},
        "depends_on": {"field": "solvent_model", "not_values": ["none"]},
    },
    "grid": {
        "type": "select",
        "advanced": True,
        "label": "Integration Grid",
        "label_zh": "积分网格",
        "options": ["SG1", "Fine", "UltraFine", "SuperFine"],
        "option_labels_zh": {
            "SG1": "SG1（粗）",
            "Fine": "Fine（细）",
            "UltraFine": "UltraFine（超细）",
            "SuperFine": "SuperFine（特细）",
        },
        "default": {"*": "UltraFine"},
    },
    "scf_convergence": {
        "type": "select",
        "advanced": True,
        "label": "SCF Convergence",
        "label_zh": "SCF 收敛标准",
        "options": ["Normal", "Tight", "VeryTight"],
        "option_labels_zh": {
            "Normal": "标准",
            "Tight": "严格",
            "VeryTight": "非常严格",
        },
        "default": {"*": "Tight"},
    },
    "opt_convergence": {
        "type": "select",
        "advanced": True,
        "options": ["Loose", "Normal", "Tight", "VeryTight"],
        "default": {"*": "Tight"},
    },
    "max_steps": {"type": "int", "advanced": True, "min": 1, "max": 10000, "default": {"*": 100}},
    "method": {
        "type": "select",
        "label": "IRC Method",
        "label_zh": "IRC 方法",
        "per_backend": {"orca": list(METHOD_META)},
        "default": {"*": "r2SCAN-3c"},
    },
    "maxpoints": {
        "type": "int",
        "label": "IRC Maximum Points",
        "label_zh": "IRC 最大步数",
        "min": 1,
        "max": 10000,
        "default": {"*": 100},
    },
    "step": {
        "type": "float",
        "label": "IRC Step",
        "label_zh": "IRC 步长",
        "min": 0,
        "default": {"*": 0.1},
    },
    "recalc_hess": {
        "type": "hessian_interval",
        "advanced": True,
        "label": "Hessian Recalculation",
        "default": {"*": "auto"},
        "min_interval": 1,
        "max_interval": 1000,
        "nullable": True,
        "widget": "hessian_toggle",
        "help": (
            "auto = infer from elements (light=off; others=10); "
            "0 = never compute exact Hessian (approximate + BFGS); "
            "1-1000 = recalculation interval"
        ),
    },
    "temperature": {"type": "float", "min": 0, "max": 10000, "default": {"*": 298.15}, "unit": "K"},
    "pressure": {"type": "float", "min": 0, "max": 100000, "default": {"*": 1.0}, "unit": "atm"},
    "scale_factor": {
        "type": "float",
        "advanced": True,
        "min": 0,
        "max": 1.0,
        "default": {"*": 0.9905},
        "help": "Frequency scale factor for ZPE/thermo (default 0.9905)",
    },
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
        "advanced": True,
        # v1.4: corrected to the xTB legal set (crude/normal/tight/verytight);
        # "loose" is not a legal xTB value (xtbmd_censo_energy _OPT_LEVELS).
        "per_backend": {"xtb": ["crude", "normal", "tight", "verytight"]},
        "default": {"*": "normal"},
    },
    "aux_j_basis": {
        "type": "select",
        "advanced": True,
        "label": "Auxiliary /J Basis",
        "label_zh": "辅助基组 /J",
        "per_backend": {"orca": _AUX_J_BASIS_FALLBACK},
        "default": {"*": "AutoAux"},
        "supports_custom": True,
        "options_source": "dynamic_aux_basis",
        "aux_kind": "j",
        "option_meta": {
            "AutoAux": "ORCA auto-generates /J auxiliary basis (recommended default)",
            "def2/J": "Weigend universal /J fitting basis (works with any main basis)",
        },
    },
    "aux_c_basis": {
        "type": "select",
        "advanced": True,
        "label": "Auxiliary /C Basis",
        "label_zh": "辅助基组 /C",
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
        "label": "RI Approximation",
        "label_zh": "RI 近似",
        "per_backend": {"orca": ["none", "RI", "RIJCOSX", "RIJK"]},
        "option_labels_zh": {
            "none": "不使用",
            "RI": "RI",
            "RIJCOSX": "RIJCOSX",
            "RIJK": "RIJK",
        },
        "default": {"*": "RIJCOSX"},
    },
    # ── xtbmd_censo_energy control group (DevDoc §10.1) ────────────────
    # All new workflow controls enter the frontend exclusively through
    # this table (advanced only decides the rendering section: regular
    # vs. the collapsed "高级" area — md_temperature / md_seeds are the
    # two high-frequency user controls and stay in the regular section).
    # CLI flag ↔ runner ↔ script_gen ↔ FIELD_DEFINITIONS ↔ frontend
    # parity is enforced by tests/test_acp_xtbmd_platform_phase5.py (E7).
    "md_temperature": {
        "type": "float",
        "label": "MD Temperature",
        "label_zh": "MD 温度",
        "min": 0,
        "default": {"*": 400.0},
        "unit": "K",
    },
    "md_time_ps": {
        "type": "float",
        "advanced": True,
        "label": "MD Time",
        "label_zh": "MD 时长",
        "min": 0,
        "default": {"*": 100.0},
        "unit": "ps",
    },
    "md_dump_fs": {
        "type": "float",
        "advanced": True,
        "label": "MD Dump Interval",
        "label_zh": "MD 帧间隔",
        "min": 0,
        "default": {"*": 100.0},
        "unit": "fs",
    },
    "md_step_fs": {
        "type": "float",
        "advanced": True,
        "label": "MD Time Step",
        "label_zh": "MD 步长",
        "min": 0,
        "default": {"*": 1.0},
        "unit": "fs",
    },
    "md_hmass": {
        "type": "float",
        "advanced": True,
        "label": "H Mass Scaling",
        "label_zh": "氢原子质量",
        "min": 0,
        "default": {"*": 1.0},
    },
    "md_shake": {
        "type": "bool",
        "advanced": True,
        "label": "SHAKE X–H Bonds",
        "label_zh": "SHAKE 键约束",
        "default": {"*": True},
    },
    "md_nvt": {
        "type": "bool",
        "advanced": True,
        "label": "NVT Ensemble",
        "label_zh": "NVT 系综",
        "default": {"*": True},
    },
    "md_seed": {
        "type": "int",
        "advanced": True,
        "label": "MD Seed",
        "label_zh": "MD 随机种子",
        "min": 0,
        "default": {"*": 42},
    },
    "md_seeds": {
        "type": "int",
        "label": "MD Replicas",
        "label_zh": "MD 副本数",
        "min": 1,
        "default": {"*": 1},
        "help": (
            "Replica trajectories (>=3 recommended for flexible molecules; "
            "each replica starts from a distinct RDKit embedding)"
        ),
    },
    "md_method": {
        "type": "select",
        "advanced": True,
        "label": "MD Hamiltonian",
        "label_zh": "MD 哈密顿量",
        "options": ["gfnff", "gfn0", "gfn1", "gfn2"],
        "default": {"*": "gfnff"},
    },
    "conv_check": {
        "type": "bool",
        "advanced": True,
        "label": "Sampling Convergence Check",
        "label_zh": "采样收敛诊断",
        "default": {"*": True},
    },
    "conv_novelty_max": {
        "type": "float",
        "advanced": True,
        "label": "Novelty Cap",
        "label_zh": "新增构象占比上限",
        "min": 0,
        "max": 1.0,
        "default": {"*": 0.10},
        "step": 0.01,
    },
    "conv_rmsd": {
        "type": "float",
        "advanced": True,
        "label": "Conv RMSD Threshold",
        "label_zh": "收敛诊断 RMSD 阈值",
        "min": 0,
        "default": {"*": 0.5},
        "unit": "Å",
    },
    "max_frames": {
        "type": "int",
        "advanced": True,
        "label": "Max Frames",
        "label_zh": "批量优化帧数上限",
        "min": 0,
        "default": {"*": 500},
        "help": "Frame cap for batch optimization (0 = unlimited; uniform subsampling)",
    },
    "opt_gfn": {
        "type": "select",
        "advanced": True,
        "label": "Batch Opt GFN",
        "label_zh": "批量优化 GFN 等级",
        "options": ["0", "1", "2"],
        "default": {"*": "1"},
    },
    "opt_timeout": {
        "type": "int",
        "advanced": True,
        "label": "Per-Frame Timeout",
        "label_zh": "单帧优化超时",
        "min": 0,
        "default": {"*": 300},
        "unit": "s",
        "help": "Per-frame xTB optimization timeout (0 = unlimited)",
    },
    "edis": {
        "type": "float",
        "advanced": True,
        "label": "ISOSTAT Energy Threshold",
        "label_zh": "ISOSTAT 能量阈值",
        "min": 0,
        "default": {"*": 0.5},
        "unit": "kcal/mol",
    },
    "gdis": {
        "type": "float",
        "advanced": True,
        "label": "ISOSTAT RMSD Threshold",
        "label_zh": "ISOSTAT 结构阈值",
        "min": 0,
        "default": {"*": 0.25},
        "unit": "Å",
    },
    "resume": {
        "type": "bool",
        "advanced": True,
        "label": "Resume from Checkpoints",
        "label_zh": "断点续跑",
        "default": {"*": False},
    },
    "path_strategy": {
        "type": "select",
        "options": ["guided-scan", "reverse-peb", "rph-reverse", "direct-ts"],
        "default": {"*": "guided-scan"},
        "label": "Path Search Strategy",
        "label_zh": "路径搜索策略",
    },
    # PESsearch coordinate + relaxed-scan protocol fields. The structure
    # picker supplies atom indices; task kind, range, and QC settings are all
    # rendered by the shared method-protocol dialog.
    "scan_coordinate_kind": {
        "type": "select",
        "options": ["distance", "angle", "dihedral"],
        "option_labels_zh": {
            "distance": "键长扫描",
            "angle": "键角扫描",
            "dihedral": "二面角扫描",
        },
        "default": {"*": "distance"},
        "label": "Coordinate Type",
        "label_zh": "扫描坐标类型",
        "help": "Choose bond length, bond angle, or dihedral scanning. Atom selection stays in the structure preview.",
    },
    "scan_bond_type": {
        "type": "select",
        "options": ["auto", "single", "double", "multiple", "aromatic"],
        "option_labels_zh": {
            "auto": "自动识别",
            "single": "单键",
            "double": "双键",
            "multiple": "多键",
            "aromatic": "芳香键",
        },
        "default": {"*": "auto"},
        "label": "Bond Type",
        "label_zh": "键类型",
        "help": "Used for the task label and scan provenance; auto keeps the structure-derived bond type when available.",
    },
    "scan_coordinate_start": {
        "type": "float",
        "default": {"*": 1.0},
        "label": "Start Coordinate",
        "label_zh": "起始值",
        "help": "Start of the coordinate range. Distances use Å; angles and dihedrals use degrees.",
    },
    "scan_coordinate_end": {
        "type": "float",
        "default": {"*": 3.0},
        "label": "End Coordinate",
        "label_zh": "终止值",
        "help": "End of the coordinate range. Distances use Å; angles and dihedrals use degrees.",
    },
    "scan_coordinate_points": {
        "type": "int",
        "min": 3,
        "max": 101,
        "default": {"*": 21},
        "label": "Scan Points",
        "label_zh": "扫描点数",
        "help": "Number of equally spaced points in the coordinate scan.",
    },
    "scan_mode": {
        "type": "select",
        "options": ["relaxed_scan"],
        "option_labels_zh": {"relaxed_scan": "松弛扫描"},
        "default": {"*": "relaxed_scan"},
        "label": "Scan Mode",
        "label_zh": "扫描模式",
        "help": "Relaxed scan optimizes the remaining geometry at every coordinate point.",
        "help_zh": "固定扫描坐标后，逐点优化其余分子几何结构。",
    },
    "scan_reuse_previous_geometry": {
        "type": "bool",
        "default": {"*": True},
        "label": "Reuse Previous Geometry",
        "label_zh": "沿用前一点几何",
        "help": "Use the converged geometry from the previous point as the next point's initial guess.",
        "help_zh": "使用前一个扫描点的收敛结构作为下一个扫描点的初始结构。",
    },
    "scan_full_scan": {
        "type": "bool",
        "default": {"*": True},
        "label": "Run Full Scan",
        "label_zh": "执行完整扫描",
        "help": "Keep all requested coordinate points even when a local rescue is needed.",
        "help_zh": "尽量执行并保留全部扫描点；遇到局部困难时交由失败处理策略处理。",
    },
    "scan_failure_policy": {
        "type": "select",
        "options": ["retry_previous", "retry_original", "mark_failed_continue", "abort"],
        "option_labels_zh": {
            "retry_previous": "沿用前一点重试",
            "retry_original": "使用原始结构重试",
            "mark_failed_continue": "标记失败并继续",
            "abort": "立即终止扫描",
        },
        "default": {"*": "retry_previous"},
        "label": "Point Failure Policy",
        "label_zh": "扫描点失败处理",
        "help": "Controls how a point is handled when its geometry optimization does not converge.",
        "help_zh": "单个扫描点优化不收敛时，选择重试、跳过或终止扫描。",
    },
    "scan_retry_count": {
        "type": "int",
        "min": 0,
        "max": 5,
        "default": {"*": 2},
        "label": "Point Retry Count",
        "label_zh": "扫描点失败重试次数",
        "help": "Maximum retries for an individual scan point.",
        "help_zh": "每个扫描点允许的最大重试次数。",
    },
    "scan_use_scants": {
        "type": "bool",
        "default": {"*": False},
        "label": "Use ORCA ScanTS",
        "label_zh": "使用 ORCA ScanTS",
        "help": "Use the ScanTS route for transition-state-oriented scans; normally leave disabled.",
        "help_zh": "面向过渡态的扫描才启用；普通键长、键角和二面角扫描通常关闭。",
    },
    "scan_max_iterations": {
        "type": "int",
        "min": 1,
        "max": 10000,
        "default": {"*": 250},
        "label": "Per-Point Optimization Max Iterations",
        "label_zh": "每个扫描点最大优化迭代",
        "help": "Maximum geometry-optimization iterations allowed for each scan point.",
    },
    "scan_optimizer_method": {
        "type": "select",
        "options": ["GFN2-xTB", "GFN1-xTB", "GFN-FF"],
        "default": {"*": "GFN2-xTB"},
        "label": "Scan Optimization Method",
        "label_zh": "扫描点优化方法",
        "help": "Low-cost method used to relax each point on the PES scan.",
    },
    "scan_optimizer_max_iterations": {
        "type": "int",
        "min": 1,
        "max": 10000,
        "default": {"*": 250},
        "label": "Optimization Max Iterations",
        "label_zh": "逐点优化最大迭代",
        "help": "Maximum optimization cycles for the per-point optimizer.",
    },
    "scan_optimizer_convergence": {
        "type": "select",
        "options": ["normal", "tight", "very_tight"],
        "option_labels_zh": {
            "normal": "标准",
            "tight": "严格",
            "very_tight": "非常严格",
        },
        "default": {"*": "normal"},
        "label": "Optimization Convergence",
        "label_zh": "逐点优化收敛等级",
        "help": "Convergence level applied to each optimized scan point.",
    },
    "scan_optimizer_retries": {
        "type": "int",
        "min": 0,
        "max": 5,
        "default": {"*": 2},
        "label": "Optimization Retries",
        "label_zh": "优化失败重试次数",
        "help": "Maximum retries before the point follows the selected failure policy.",
    },
    "scan_optimizer_retry_strategy": {
        "type": "select",
        "options": ["previous_geometry", "original_geometry", "looser_convergence"],
        "option_labels_zh": {
            "previous_geometry": "前一点几何",
            "original_geometry": "原始结构",
            "looser_convergence": "放宽收敛条件",
        },
        "default": {"*": "previous_geometry"},
        "label": "Optimization Retry Strategy",
        "label_zh": "优化失败重试策略",
        "help": "Initial geometry or convergence fallback used for a retry.",
    },
    "single_point_resume": {
        "type": "bool",
        "default": {"*": True},
        "label": "Resume Single Points",
        "label_zh": "单点能断点续算",
        "help": "Reuse completed single-point results when resuming an interrupted scan.",
    },
    "protocol": {
        "type": "select",
        "options": ["xtb-crest", "xtb-md", "censo-crest", "xtbmd-censo"],
        "option_labels_zh": {
            "xtb-crest": "xTB + CREST",
            "xtb-md": "xTB-MD",
            "censo-crest": "CREST + CENSO",
            "xtbmd-censo": "xTB-MD + CENSO + DFT",
        },
        "default": {"*": "censo-crest"},
        "label": "Confsearch Protocol",
        "label_zh": "计算协议",
    },
    "confsearch_profile": {
        "type": "select",
        "options": ["light", "default", "high"],
        "default": {"*": "default"},
        "label": "Quality Profile",
        "label_zh": "精度档位",
    },
    "refinement_policy": {
        "type": "select",
        "options": ["screen", "rank1", "cumulative-99", "all"],
        "option_labels_zh": {
            "screen": "仅筛选",
            "rank1": "Rank 1",
            "cumulative-99": "累计 Boltzmann 99%",
            "all": "全部构象",
        },
        "default": {"*": "screen"},
        "label": "Refinement Policy",
        "label_zh": "精修策略",
    },
    "fidelity": {
        "type": "select",
        "options": ["s3", "s4"],
        "default": {"*": "s3"},
        "label": "Refinement Fidelity",
        "label_zh": "精化精度",
    },
    "irc_points": {
        "type": "int",
        "min": 5,
        "max": 200,
        "default": {"*": 30},
        "advanced": True,
        "label": "IRC Points",
        "label_zh": "IRC 点数",
    },
    "scan_points": {
        "type": "int",
        "min": 5,
        "max": 100,
        "default": {"*": 21},
        "advanced": True,
        "label": "Scan Points",
        "label_zh": "扫描点数",
    },
    "ts_initial_hessian": {
        "type": "select",
        "options": ["calculate", "model", "read"],
        "default": {"*": "calculate"},
        "advanced": True,
        "label": "TS Initial Hessian",
        "label_zh": "TS 初猜 Hessian",
    },
    "conformer_mode": {
        "type": "select",
        "options": ["auto", "censo-lite", "xtb-fast"],
        "default": {"*": "auto"},
        "advanced": True,
        "label": "Conformer Mode",
        "label_zh": "构象模式",
    },
    "max_elementary_steps": {
        "type": "int",
        "min": 1,
        "max": 20,
        "default": {"*": 3},
        "advanced": True,
        "label": "Max Elementary Steps",
        "label_zh": "最大基元步骤数",
    },
    "int_extension": {
        "type": "bool",
        "advanced": True,
        "default": {"*": False},
        "label": "Intermediate Extension",
        "label_zh": "中间体扩展",
    },
    "promotion_policy": {
        "type": "select",
        "options": ["all_confirmed", "rate_relevant", "user_selected"],
        "default": {"*": "all_confirmed"},
        "advanced": True,
        "label": "Promotion Policy",
        "label_zh": "升级策略",
    },
    "auto_converge": {
        "type": "bool",
        "advanced": True,
        "default": {"*": False},
        "label": "Auto Converge",
        "label_zh": "自动收敛",
    },
    # ── NMR-specific fields (P1a, 2026-08-07) ───────────────────────────
    "nuclei": {
        "type": "select",
        "multi": True,
        "label": "Target Nuclei",
        "label_zh": "目标核",
        "options": ["1H", "13C", "15N", "19F", "31P"],
        "default": {"*": ["1H", "13C"]},
        "help": "NMR-active nuclei to compute (also select 1H for proton spectra)",
    },
    "boltzmann_temp": {
        "type": "float",
        "label": "Boltzmann Temperature",
        "label_zh": "Boltzmann 温度",
        "min": 0,
        "default": {"*": 298.15},
        "unit": "K",
    },
    "tms_shielding_h": {
        "type": "float",
        "advanced": True,
        "label": "TMS ¹H Shielding",
        "label_zh": "TMS ¹H 屏蔽",
        "default": {"*": ""},
        "unit": "ppm",
        "help": "TMS ¹H reference shielding at the GIAO level (empty = solvent-aware Goodman table lookup)",
    },
    "tms_shielding_c": {
        "type": "float",
        "advanced": True,
        "label": "TMS ¹³C Shielding",
        "label_zh": "TMS ¹³C 屏蔽",
        "default": {"*": ""},
        "unit": "ppm",
        "help": "TMS ¹³C reference shielding at the GIAO level (empty = solvent-aware Goodman table lookup)",
    },
    "profile": {
        "type": "select",
        "label": "Batch Profile",
        "label_zh": "批量配置档位",
        "options": ["opt_only", "opt_freq", "opt_freq_sp", "opt_freq_sp_thermo"],
        "default": {"*": "opt_freq"},
    },
    "items": {
        "type": "json",
        "label": "Structure Items",
        "label_zh": "结构项目",
        "default": {"*": []},
        "help": "Structure artifact items consumed by BatchOptimize.",
        "help_zh": "BatchOptimize 消费的结构 Artifact 项目。",
    },
    "minimum_method": {
        "type": "select",
        "label": "Minimum Method Override",
        "label_zh": "最低点方法覆盖",
        "per_backend": {"orca": ["r2SCAN-3c", "B3LYP", "PBE0", "M062X", "wB97X-D4", "wB97M-V"]},
        "default": {"*": ""},
    },
    "minimum_basis": {
        "type": "select",
        "label": "Minimum Basis Override",
        "label_zh": "最低点基组覆盖",
        "per_backend": {"orca": _BASIS_CATALOG_REF},
        "default": {"*": ""},
        "supports_custom": True,
    },
    "transition_state_method": {
        "type": "select",
        "label": "Transition-State Method Override",
        "label_zh": "过渡态方法覆盖",
        "per_backend": {"orca": ["r2SCAN-3c", "B3LYP", "PBE0", "M062X", "wB97X-D4", "wB97M-V"]},
        "default": {"*": ""},
    },
    "transition_state_basis": {
        "type": "select",
        "label": "Transition-State Basis Override",
        "label_zh": "过渡态基组覆盖",
        "per_backend": {"orca": _BASIS_CATALOG_REF},
        "default": {"*": ""},
        "supports_custom": True,
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
                    "recalc_hess",
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
        "stages": {"mode": "static", "static": ["single_point"]},
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
        "stages": {"mode": "static", "static": ["optimize"]},
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
        "stages": {"mode": "static", "static": ["frequency"]},
        "profiles": [],
    },
    "dft_scan": {
        "method_levels": [
            {
                "level_id": "scan",
                "label": "Relaxed Scan",
                "label_zh": "松弛扫描",
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
                    "scan_coordinate_kind",
                    "scan_coordinate_start",
                    "scan_coordinate_end",
                    "scan_coordinate_points",
                    "scan_mode",
                    "scan_reuse_previous_geometry",
                    "scan_full_scan",
                    "scan_failure_policy",
                    "scan_retry_count",
                    "scan_use_scants",
                    "scan_max_iterations",
                    "scan_optimizer_method",
                    "scan_optimizer_max_iterations",
                    "scan_optimizer_convergence",
                    "scan_optimizer_retries",
                    "scan_optimizer_retry_strategy",
                ],
            }
        ],
        "stages": {"mode": "static", "static": ["scan"]},
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default Relaxed Scan",
                "label_zh": "标准松弛扫描",
                "summary": "r2SCAN-3c/ORCA relaxed scan over a distance coordinate",
                "levels": {
                    "scan": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
                        "dispersion": "none",
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
                        "solvent_model": "none",
                        "solvent": "",
                        "scan_coordinate_kind": "distance",
                        "scan_coordinate_start": 1.0,
                        "scan_coordinate_end": 3.0,
                        "scan_coordinate_points": 21,
                        "scan_mode": "relaxed_scan",
                        "scan_reuse_previous_geometry": True,
                        "scan_full_scan": True,
                        "scan_failure_policy": "retry_previous",
                        "scan_retry_count": 2,
                        "scan_use_scants": False,
                        "scan_max_iterations": 250,
                        "scan_optimizer_method": "GFN2-xTB",
                        "scan_optimizer_max_iterations": 250,
                        "scan_optimizer_convergence": "normal",
                        "scan_optimizer_retries": 2,
                        "scan_optimizer_retry_strategy": "previous_geometry",
                    }
                },
            }
        ],
    },
    "irc": {
        "method_levels": [
            {
                "level_id": "irc",
                "label": "Intrinsic Reaction Coordinate",
                "label_zh": "\u5185\u7968\u53cd\u5e94\u5750\u6807",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": ["method", "basis", "maxpoints", "step"],
            }
        ],
        "stages": {"mode": "static", "static": ["irc"]},
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default IRC",
                "label_zh": "标准 IRC",
                "summary": "r2SCAN-3c IRC in both directions",
                "levels": {
                    "irc": {
                        "engine": "orca",
                        "method": "r2SCAN-3c",
                        "basis": "",
                        "maxpoints": 100,
                        "step": 0.1,
                    }
                },
            }
        ],
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
        "stages": {"mode": "static", "static": ["xtb_optimize"]},
        "profiles": [],
    },
    "mech_conf": {
        "method_levels": [
            {
                "level_id": "module",
                "label": "Module",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [],
            }
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default",
                "summary": "CENSO-lite conformer search for one stable state",
                "levels": {},
            }
        ],
    },
    "mech_step": {
        "method_levels": [
            {
                "level_id": "module",
                "label": "Module",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [],
            }
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default",
                "summary": "Elementary step: PEB path -> coarse refine -> IRC -> endpoints",
                "levels": {},
            }
        ],
    },
    "mech_confirm": {
        "method_levels": [
            {
                "level_id": "module",
                "label": "Module",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [],
            }
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default",
                "summary": "High-fidelity (S4) confirmation of one step artifact",
                "levels": {},
            }
        ],
    },
    "mech_chain": {
        "method_levels": [
            {
                "level_id": "module",
                "label": "Module",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [],
            }
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default",
                "summary": "Declarative composition of standalone mechanism modules",
                "levels": {},
            }
        ],
    },
    "confsearch_unified": {
        "method_levels": [
            {
                "level_id": "confsearch",
                "label": "Conformer Search Protocol",
                "label_zh": "构象搜索协议",
                "required": True,
                "allowed_engines": ["crest", "xtb", "censo"],
                "fields": [
                    "protocol",
                    "confsearch_profile",
                    "refinement_policy",
                    "ewin",
                    "refinement_threshold",
                ],
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
                "label_zh": "单点能",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "ri_approximation",
                    "aux_j_basis",
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
                "label_zh": "热力学修正",
                "required": False,
                "allowed_engines": ["shermo"],
                "fields": ["temperature", "pressure", "scale_factor"],
            },
        ],
        "profiles": [
            {
                "profile_id": "xtb-crest",
                "label": "xTB + CREST",
                "summary": "CREST → xTB energies → Boltzmann (pure xTB, fastest)",
                "levels": {
                    "confsearch": {
                        "engine": "crest",
                        "protocol": "xtb-crest",
                        "confsearch_profile": "default",
                        "refinement_policy": "screen",
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
                "profile_id": "xtb-md",
                "label": "xTB-MD",
                "summary": "GFN-FF MD → GFN1 opt → ISOSTAT dedup → Boltzmann (pure xTB)",
                "levels": {
                    "confsearch": {
                        "engine": "xtb",
                        "protocol": "xtb-md",
                        "confsearch_profile": "default",
                        "refinement_policy": "screen",
                    },
                    "thermo": {
                        "engine": "shermo",
                        "temperature": 298.15,
                        "pressure": 1.0,
                    },
                },
            },
            {
                "profile_id": "censo-crest",
                "label": "CREST + CENSO",
                "summary": "CREST → CENSO free energies → rank1 DFT refinement (recommended)",
                "levels": {
                    "confsearch": {
                        "engine": "censo",
                        "protocol": "censo-crest",
                        "confsearch_profile": "default",
                        "refinement_policy": "rank1",
                        "ewin": 6.0,
                        "refinement_threshold": 0.99,
                    },
                    "refinement_sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
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
                "profile_id": "xtbmd-censo",
                "label": "xTB-MD + CENSO + DFT",
                "summary": "GFN-FF MD → GFN1 opt → ISOSTAT → CENSO → fine DFT (full chain)",
                "levels": {
                    "confsearch": {
                        "engine": "censo",
                        "protocol": "xtbmd-censo",
                        "confsearch_profile": "default",
                        "refinement_policy": "cumulative-99",
                        "refinement_threshold": 0.99,
                    },
                    "refinement_sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
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
    "pes_scan": {
        "method_levels": [
            {
                "level_id": "scan_coordinate",
                "label": "Scan Coordinate",
                "label_zh": "扫描坐标与范围",
                "required": True,
                "allowed_engines": ["orca"],
                "hide_engine": True,
                "fields": [
                    "scan_coordinate_kind",
                    "scan_bond_type",
                    "scan_coordinate_start",
                    "scan_coordinate_end",
                    "scan_coordinate_points",
                ],
            },
            {
                "level_id": "scan_driver",
                "label": "Scan Driver",
                "label_zh": "扫描驱动",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "scan_mode",
                    "scan_reuse_previous_geometry",
                    "scan_full_scan",
                    "scan_failure_policy",
                    "scan_retry_count",
                    "scan_use_scants",
                ],
            },
            {
                "level_id": "scan_optimizer",
                "label": "Per-Point Optimization",
                "label_zh": "扫描点优化",
                "required": True,
                "allowed_engines": ["xtb"],
                "fields": [
                    "scan_optimizer_method",
                    "scan_optimizer_max_iterations",
                    "scan_optimizer_convergence",
                    "scan_optimizer_retries",
                    "scan_optimizer_retry_strategy",
                ],
            },
            {
                "level_id": "single_point",
                "label": "Single Point Energy",
                "label_zh": "单点能",
                "required": False,
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
                    "single_point_resume",
                ],
            },
        ],
        "stages": {
            "mode": "static",
            "static": [
                "prepare",
                "materialize_input",
                "validate_coordinate",
                "run_relaxed_scan",
                "extract_frames",
                "run_single_points",
                "build_profile",
                "select_candidates",
                "finalize",
            ],
        },
        "profiles": [
            {
                "profile_id": "default",
                "label": "Default PES Scan",
                "label_zh": "标准 PES 扫描",
                "summary": "ORCA relaxed scan | GFN2-xTB point optimization | B97-3c single points",
                "summary_zh": "ORCA 松弛扫描｜GFN2-xTB 扫描点优化｜B97-3c 单点能",
                "levels": {
                    "scan_coordinate": {
                        "engine": "orca",
                        "scan_coordinate_kind": "distance",
                        "scan_bond_type": "auto",
                        "scan_coordinate_start": 1.0,
                        "scan_coordinate_end": 3.0,
                        "scan_coordinate_points": 21,
                    },
                    "scan_driver": {
                        "engine": "orca",
                        "scan_mode": "relaxed_scan",
                        "scan_reuse_previous_geometry": True,
                        "scan_full_scan": True,
                        "scan_failure_policy": "retry_previous",
                        "scan_retry_count": 2,
                        "scan_use_scants": False,
                    },
                    "scan_optimizer": {
                        "engine": "xtb",
                        "scan_optimizer_method": "GFN2-xTB",
                        "scan_optimizer_max_iterations": 250,
                        "scan_optimizer_convergence": "normal",
                        "scan_optimizer_retries": 2,
                        "scan_optimizer_retry_strategy": "previous_geometry",
                    },
                    "single_point": {
                        "engine": "orca",
                        "functional": "B97-3c",
                        "basis": "",
                        "dispersion": "none",
                        "ri_approximation": "none",
                        "aux_j_basis": "",
                        "aux_c_basis": "",
                        "solvent_model": "none",
                        "solvent": "",
                        "grid": "UltraFine",
                        "scf_convergence": "Tight",
                        "single_point_resume": True,
                    },
                },
                "stages": {
                    "mode": "static",
                    "static": [
                        "prepare",
                        "materialize_input",
                        "validate_coordinate",
                        "run_relaxed_scan",
                        "extract_frames",
                        "run_single_points",
                        "build_profile",
                        "select_candidates",
                        "finalize",
                    ],
                },
            },
        ],
    },
    "low_confirm": {
        "method_levels": [
            {
                "level_id": "confirm",
                "label": "Low Confirmation",
                "label_zh": "粗优化确认",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": ["scan_points"],
            },
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "B97-3c + r2SCAN-3c SP",
                "summary": "B97-3c Opt/TS + freq + preliminary IRC + r2SCAN-3c SP",
                "levels": {},
            },
        ],
    },
    "high_confirm": {
        "method_levels": [
            {
                "level_id": "confirm",
                "label": "High Confirmation",
                "label_zh": "高精度确认",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": ["scan_points"],
            },
        ],
        "profiles": [
            {
                "profile_id": "default",
                "label": "M062X + wB97M-V SP",
                "summary": "M062X/def2-SVP Opt/TS + freq + wB97M-V/def2-TZVPP SP + thermo",
                "levels": {},
            },
        ],
    },
    "nmr": {
        # NMR + DP4/DP5 method schema (P1a, 2026-08-07). Two levels:
        # - ``conformer`` (CENSO, censo-light preset) — screening-level
        #   conformer geometry + Boltzmann weights;
        # - ``giaoa`` (ORCA, mPW1PW91/6-311G(d)) — GIAO absolute shielding.
        # The GIAO level MUST stay mPW1PW91/6-311G(d) to keep the Goodman
        # error model valid (DevDoc §8.0/§10.2); switching levels requires
        # a retrained error model.
        "method_levels": [
            {
                "level_id": "conformer",
                "label": "Conformer Generation",
                "label_zh": "\u6784\u8c61\u751f\u6210",
                "required": True,
                "allowed_engines": ["censo"],
                "fields": ["ewin", "refinement_threshold"],
            },
            {
                "level_id": "giaoa",
                "label": "GIAO NMR",
                "label_zh": "GIAO NMR \u5c4f\u853d",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "solvent_model",
                    "solvent",
                    "nuclei",
                    "boltzmann_temp",
                    "tms_shielding_h",
                    "tms_shielding_c",
                ],
            },
        ],
        "profiles": [
            {
                "profile_id": "nmr-goodman",
                "label": "Goodman (mPW1PW91/6-311G(d))",
                "summary": (
                    "CREST+CENSO conformers + mPW1PW91/6-311G(d) GIAO + "
                    "Goodman DP4/DP5 (recommended)"
                ),
                "levels": {
                    "conformer": {
                        "engine": "censo",
                        "ewin": 6.0,
                        "refinement_threshold": 0.99,
                    },
                    "giaoa": {
                        "engine": "orca",
                        "functional": "mPW1PW91",
                        "basis": "6-311G(d)",
                        "solvent_model": "cpcm",
                        "solvent": "chloroform",
                        "nuclei": ["1H", "13C"],
                        "boltzmann_temp": 298.15,
                        # TMS references intentionally NOT pinned here: the
                        # workflow resolves them solvent-aware from the
                        # Goodman TMSdata table (tms_shielding_h/c remain
                        # advanced manual overrides).
                    },
                },
            },
        ],
    },
    "batch_optimize": {
        "method_levels": [
            {
                "level_id": "batch",
                "label": "Batch Optimization",
                "label_zh": "\u6279\u91cf\u4f18\u5316",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "profile",
                    "items",
                    "minimum_method",
                    "minimum_basis",
                    "transition_state_method",
                    "transition_state_basis",
                ],
            },
        ],
        "stages": {
            "mode": "by_profile",
            "by_profile": {
                "opt_only": ["prepare", "optimize", "finalize"],
                "opt_freq": ["prepare", "optimize", "frequency", "finalize"],
                "opt_freq_sp": ["prepare", "optimize", "frequency", "single_point", "finalize"],
                "opt_freq_sp_thermo": [
                    "prepare", "optimize", "frequency", "single_point", "thermochemistry", "finalize",
                ],
            },
        },
        "profiles": [
            {
                "profile_id": "opt_only",
                "label": "Optimization Only",
                "label_zh": "仅优化",
                "summary": "Optimize every selected structure.",
                "levels": {"batch": {"steps": ["optimize"]}},
            },
            {
                "profile_id": "opt_freq",
                "label": "Optimization + Frequency",
                "label_zh": "优化 + 频率",
                "summary": "Optimize and calculate frequencies.",
                "levels": {"batch": {"steps": ["optimize", "frequency"]}},
            },
            {
                "profile_id": "opt_freq_sp",
                "label": "Optimization + Frequency + SP",
                "label_zh": "优化 + 频率 + 单点能",
                "summary": "Optimize, calculate frequencies, and run single points.",
                "levels": {"batch": {"steps": ["optimize", "frequency", "singlepoint"]}},
            },
            {
                "profile_id": "opt_freq_sp_thermo",
                "label": "Optimization + Frequency + SP + Thermochemistry",
                "label_zh": "优化 + 频率 + 单点能 + 热化学",
                "summary": "Run the complete optimization, frequency, single-point, and thermochemistry chain.",
                "levels": {
                    "batch": {"steps": ["optimize", "frequency", "singlepoint", "thermochemistry"]}
                },
            },
        ],
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
                "label_zh": "DFT \u7ed3\u6784\u4f18\u5316",
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
                    "recalc_hess",
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
    "xtbmd_censo_energy": {
        # DevDoc §10.1: xTB-MD conformer-search free-energy pipeline.
        # levels = xtb_md / xtb_opt / isostat / censo / dft_opt (optional,
        # mirrors censo_energy) / refinement_sp / thermo. The dft_opt level
        # is required for the frontend no_opt derivation (an absent
        # dft_opt level maps to --no-opt in the energy-like submit branch).
        "method_levels": [
            {
                "level_id": "xtb_md",
                "label": "xTB MD Sampling",
                "label_zh": "xTB 动力学采样",
                "required": True,
                "allowed_engines": ["molclus"],
                "fields": [
                    "md_temperature",
                    "md_time_ps",
                    "md_dump_fs",
                    "md_step_fs",
                    "md_hmass",
                    "md_shake",
                    "md_nvt",
                    "md_seed",
                    "md_seeds",
                    "md_method",
                    "resume",
                ],
            },
            {
                "level_id": "xtb_opt",
                "label": "GFN1 Batch Optimization",
                "label_zh": "GFN1 批量优化",
                "required": True,
                "allowed_engines": ["xtb"],
                "fields": [
                    "opt_gfn",
                    "opt_level",
                    "opt_timeout",
                    "max_frames",
                    "conv_check",
                    "conv_novelty_max",
                    "conv_rmsd",
                ],
            },
            {
                "level_id": "isostat",
                "label": "ISOSTAT Deduplication",
                "label_zh": "ISOSTAT 去重",
                "required": True,
                "allowed_engines": ["isostat"],
                "fields": ["edis", "gdis"],
            },
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
                "label_zh": "DFT \u7ed3\u6784\u4f18\u5316",
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
                    "recalc_hess",
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
                "summary": "xTB-MD → GFN1 → CENSO → 99% ensemble → DFT refinement (recommended)",
                "levels": {
                    "xtb_md": {
                        "engine": "molclus",
                        "md_temperature": 400.0,
                        "md_time_ps": 100.0,
                        "md_dump_fs": 100.0,
                        "md_step_fs": 1.0,
                        "md_hmass": 1.0,
                        "md_shake": True,
                        "md_nvt": True,
                        "md_seed": 42,
                        "md_seeds": 1,
                        "md_method": "gfnff",
                        "resume": False,
                    },
                    "xtb_opt": {
                        "engine": "xtb",
                        "opt_gfn": "1",
                        "opt_level": "normal",
                        "opt_timeout": 300,
                        "max_frames": 500,
                        "conv_check": True,
                        "conv_novelty_max": 0.10,
                        "conv_rmsd": 0.5,
                    },
                    "isostat": {
                        "engine": "isostat",
                        "edis": 0.5,
                        "gdis": 0.25,
                    },
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
                "summary": "xTB-MD → GFN1 → full CENSO Part0–3 → 99% refinement (~10x light cost)",
                "levels": {
                    "xtb_md": {
                        "engine": "molclus",
                        "md_temperature": 400.0,
                        "md_time_ps": 100.0,
                        "md_dump_fs": 100.0,
                        "md_step_fs": 1.0,
                        "md_hmass": 1.0,
                        "md_shake": True,
                        "md_nvt": True,
                        "md_seed": 42,
                        "md_seeds": 1,
                        "md_method": "gfnff",
                        "resume": False,
                    },
                    "xtb_opt": {
                        "engine": "xtb",
                        "opt_gfn": "1",
                        "opt_level": "normal",
                        "opt_timeout": 300,
                        "max_frames": 500,
                        "conv_check": True,
                        "conv_novelty_max": 0.10,
                        "conv_rmsd": 0.5,
                    },
                    "isostat": {
                        "engine": "isostat",
                        "edis": 0.5,
                        "gdis": 0.25,
                    },
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
                "summary": "xTB-MD → GFN1 → xTB passthrough → DFT refinement (cheapest)",
                "levels": {
                    "xtb_md": {
                        "engine": "molclus",
                        "md_temperature": 400.0,
                        "md_time_ps": 100.0,
                        "md_dump_fs": 100.0,
                        "md_step_fs": 1.0,
                        "md_hmass": 1.0,
                        "md_shake": True,
                        "md_nvt": True,
                        "md_seed": 42,
                        "md_seeds": 1,
                        "md_method": "gfnff",
                        "resume": False,
                    },
                    "xtb_opt": {
                        "engine": "xtb",
                        "opt_gfn": "1",
                        "opt_level": "normal",
                        "opt_timeout": 300,
                        "max_frames": 500,
                        "conv_check": True,
                        "conv_novelty_max": 0.10,
                        "conv_rmsd": 0.5,
                    },
                    "isostat": {
                        "engine": "isostat",
                        "edis": 0.5,
                        "gdis": 0.25,
                    },
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
    "mechanism": {
        "method_levels": [
            {
                "level_id": "scan",
                "label": "Path Scan",
                "label_zh": "路径扫描",
                "required": True,
                "allowed_engines": ["xtb"],
                "fields": [
                    "scan_points",
                    "path_strategy",
                    "fidelity",
                    "conformer_mode",
                    "max_elementary_steps",
                    "int_extension",
                    "promotion_policy",
                    "auto_converge",
                ],
            },
            {
                "level_id": "ts_opt",
                "label": "TS Optimization",
                "label_zh": "过渡态优化",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "dispersion",
                    "grid",
                    "scf_convergence",
                    "max_steps",
                    "solvent_model",
                    "solvent",
                    "ts_initial_hessian",
                ],
            },
            {
                "level_id": "freq",
                "label": "Frequency",
                "label_zh": "频率",
                "required": False,
                "allowed_engines": ["orca"],
                "fields": ["functional", "basis", "solvent_model", "solvent"],
            },
            {
                "level_id": "sp",
                "label": "Single Point Energy",
                "label_zh": "单点能",
                "required": True,
                "allowed_engines": ["orca"],
                "fields": [
                    "functional",
                    "basis",
                    "ri_approximation",
                    "aux_j_basis",
                    "dispersion",
                    "solvent_model",
                    "solvent",
                    "grid",
                    "scf_convergence",
                ],
            },
            {
                "level_id": "irc",
                "label": "IRC Validation",
                "label_zh": "IRC 验证",
                "required": False,
                "allowed_engines": ["orca"],
                "fields": ["irc_points", "functional", "basis"],
            },
        ],
        "profiles": [
            {
                "profile_id": "rph-s3",
                "label": "RPH Low-Fidelity (B97-3c → r2SCAN-3c)",
                "summary": "Guided relaxed scan (xTB) + B97-3c OptTS/Freq + r2SCAN-3c SP (RPH S3 contract)",
                "levels": {
                    "scan": {
                        "engine": "xtb",
                        "path_strategy": "guided-scan",
                        "fidelity": "s3",
                        "scan_points": 21,
                        "conformer_mode": "censo-lite",
                        "max_elementary_steps": 3,
                        "int_extension": False,
                        "promotion_policy": "all_confirmed",
                        "auto_converge": False,
                    },
                    "ts_opt": {
                        "engine": "orca",
                        "functional": "B97-3c",
                        "basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "ts_initial_hessian": "calculate",
                    },
                    "freq": {"engine": "orca", "functional": "B97-3c", "basis": ""},
                    "sp": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "irc": {
                        "engine": "orca",
                        "functional": "B97-3c",
                        "basis": "",
                        "irc_points": 30,
                    },
                },
            },
            {
                "profile_id": "rph-s4",
                "label": "RPH High-Fidelity (M062X → wB97M-V)",
                "summary": "Guided relaxed scan (xTB) + M062X/def2-SVP OptTS/Freq + wB97M-V/def2-TZVPP SP (RPH S4 contract)",
                "levels": {
                    "scan": {
                        "engine": "xtb",
                        "path_strategy": "guided-scan",
                        "fidelity": "s4",
                        "scan_points": 25,
                        "conformer_mode": "censo-lite",
                        "max_elementary_steps": 3,
                        "int_extension": False,
                        "promotion_policy": "all_confirmed",
                        "auto_converge": False,
                    },
                    "ts_opt": {
                        "engine": "orca",
                        "functional": "M062X",
                        "basis": "def2-SVP",
                        "dispersion": "none",
                        "grid": "DefGrid3",
                        "scf_convergence": "Tight",
                        "solvent_model": "none",
                        "solvent": "",
                        "ts_initial_hessian": "calculate",
                    },
                    "freq": {"engine": "orca", "functional": "M062X", "basis": "def2-SVP"},
                    "sp": {
                        "engine": "orca",
                        "functional": "wB97M-V",
                        "basis": "def2-TZVPP",
                        "ri_approximation": "RIJCOSX",
                        "aux_j_basis": "def2/J",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "irc": {
                        "engine": "orca",
                        "functional": "M062X",
                        "basis": "def2-SVP",
                        "irc_points": 40,
                    },
                },
            },
            {
                "profile_id": "guided-scan-fast",
                "label": "Guided Scan Fast (xTB-fast → S3)",
                "summary": "xTB-fast conformer intake + guided relaxed scan + B97-3c OptTS/Freq + r2SCAN-3c SP",
                "levels": {
                    "scan": {
                        "engine": "xtb",
                        "path_strategy": "guided-scan",
                        "fidelity": "s3",
                        "scan_points": 21,
                        "conformer_mode": "xtb-fast",
                        "max_elementary_steps": 3,
                        "int_extension": False,
                        "promotion_policy": "all_confirmed",
                        "auto_converge": False,
                    },
                    "ts_opt": {
                        "engine": "orca",
                        "functional": "B97-3c",
                        "basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                        "ts_initial_hessian": "calculate",
                    },
                    "freq": {"engine": "orca", "functional": "B97-3c", "basis": ""},
                    "sp": {
                        "engine": "orca",
                        "functional": "r2SCAN-3c",
                        "basis": "",
                        "dispersion": "none",
                        "solvent_model": "none",
                        "solvent": "",
                    },
                    "irc": {
                        "engine": "orca",
                        "functional": "B97-3c",
                        "basis": "",
                        "irc_points": 30,
                    },
                },
            },
        ],
    },
}

# Historical frontend/task records used ``pes_bond_scan``. Keep it as a
# read-compatible alias while ``pes_scan`` becomes the canonical schema for
# distance, angle, and dihedral PES tasks.
METHOD_SCHEMAS["pes_bond_scan"] = METHOD_SCHEMAS["pes_scan"]

# ── Backend discovery (R22 / Phase 4.5) ────────────────────────────────
# Dynamically resolves the availability and version of every external binary
# that the platform depends on so the frontend can surface pre-flight
# environment checks before submitting a workflow.

_BACKEND_BINARIES: dict[str, dict[str, Any]] = {
    "orca": {"label": "ORCA", "env_var": "CONFSEARCH_ORCA_PATH", "default": "orca"},
    "xtb": {"label": "xTB (GFN2)", "env_var": "CONFSEARCH_XTB_PATH", "default": "xtb"},
    "crest": {"label": "CREST", "env_var": "CONFSEARCH_CREST_PATH", "default": "crest"},
    "censo": {"label": "CENSO", "env_var": "CONFSEARCH_CENSO_PATH", "default": "censo"},
    "shermo": {"label": "Shermo", "env_var": "CONFSEARCH_SHERMO_PATH", "default": "Shermo"},
    "isostat": {"label": "ISOSTAT", "env_var": "CONFSEARCH_ISOSTAT_PATH", "default": "isostat"},
}

_BACKEND_SUPPORTS: dict[str, list[str]] = {
    "orca": ["singlepoint", "optimize", "frequency", "optfreq", "scan"],
    "xtb": ["singlepoint", "optimize"],
    "crest": ["conformer_search"],
    "censo": ["censo_refinement", "censo_energy"],
    "shermo": ["thermo"],
    "isostat": ["clustering"],
}


def _resolve_backend_path(bid: str) -> str:
    """Resolve the backend binary path for *bid* via the centralized resolver."""
    from cccp.software import resolve_executable

    path = resolve_executable(bid)
    return str(path) if path else bid


def _detect_backend_version(bid: str, binary_path: str) -> str | None:
    """Try to detect the version of backend *bid* via the centralized helper."""
    from pathlib import Path

    from cccp.software import detect_version

    return detect_version(bid, Path(binary_path) if binary_path else None)


def _discover_backends() -> list[dict[str, Any]]:
    """Build the backends list with dynamic availability metadata.

    Version probing is deliberately deferred to :func:`refresh_backend_versions`
    so importing this module never spawns subprocesses (version probes are
    only needed when the API or ``acp doctor`` asks for them).
    """
    backends: list[dict[str, Any]] = []
    for bid, binfo in _BACKEND_BINARIES.items():
        path = _resolve_backend_path(bid)
        available = path != bid
        backends.append(
            {
                "id": bid,
                "label": binfo["label"],
                "supports": _BACKEND_SUPPORTS.get(bid, []),
                "path": path,
                "available": available,
                "version": None,
            }
        )
    return backends


def refresh_backend_versions() -> None:
    """Probe versions of all resolvable backends, in place.

    Updates ``METHOD_CATALOG["backends"]`` entries in place. Called lazily by
    the API layer; never at import time.
    """
    for entry in METHOD_CATALOG["backends"]:
        if entry["available"]:
            entry["version"] = _detect_backend_version(entry["id"], entry["path"])


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
    field_name: str,
    engine: str,
    functional: str | None = None,
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
    field_name: str,
    engine: str,
    functional: str | None = None,
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
    options: list[str],
    value: Any,
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
            # hessian_interval is a self-validating scalar: route through the
            # shared normaliser so CLI/API/catalog/scheduler all agree.
            if fd and fd.get("type") == "hessian_interval":
                if user_val is None or user_val == "":
                    default_val = _resolve_field_default(
                        field_name, engine, normalized.get("functional")
                    )
                    normalized[field_name] = default_val
                    continue
                try:
                    normalized[field_name] = normalize_recalc_hess(user_val)
                except ValueError as exc:
                    errors.append(f"Level '{lid}', field '{field_name}': {exc}")
                continue
            if user_val is not None and user_val != "":
                # Multi-select fields (e.g. NMR ``nuclei``): accept a scalar
                # or a list, validate every item against the allowed options,
                # and normalise to a list so downstream CLI-flag emission
                # (``--nuclei 1H,13C``) always sees a sequence.
                if fd and fd.get("multi"):
                    vals = list(user_val) if isinstance(user_val, (list, tuple)) else [user_val]
                    multi_options = _resolve_field_options(
                        field_name,
                        engine,
                        normalized.get("functional"),
                        basis=normalized.get("basis") or user_lv.get("basis"),
                    )
                    if multi_options is not None:
                        opt_strs = [str(o) for o in multi_options]
                        if any(str(v) not in opt_strs for v in vals):
                            errors.append(
                                f"Level '{lid}', field '{field_name}': "
                                f"value '{user_val}' not in allowed options"
                            )
                            continue
                    normalized[field_name] = vals
                    continue
                options = _resolve_field_options(
                    field_name,
                    engine,
                    normalized.get("functional"),
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
                    elif str(user_val) not in [str(o) for o in options]:
                        if (
                            fd
                            and fd.get("supports_custom")
                            and str(user_val).strip()
                            and len(options) > 1
                        ):
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
                    field_name,
                    engine,
                    normalized.get("functional"),
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
        "recalc_hess": "recalc_hess",
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
    # NOTE: recalc_hess is handled inline in method_levels_to_cli_flags()
    # because the new CLI surface is --calc-hess / --no-calc-hess (mutually
    # exclusive) rather than a single --recalc-hess flag.
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
            # recalc_hess maps to the mutually-exclusive --calc-hess /
            # --no-calc-hess CLI surface. It is only meaningful on the
            # unprefixed opt level; prefixed levels (e.g. sp-) are skipped
            # because single-point calculations do not optimise.
            if field == "recalc_hess":
                try:
                    normalized = normalize_recalc_hess(value)
                except ValueError:
                    continue
                if normalized is None:
                    continue
                if prefix:
                    continue
                if normalized == "auto":
                    cmd += ["--calc-hess", "auto"]
                elif normalized == 0:
                    cmd += ["--no-calc-hess"]
                else:
                    cmd += ["--calc-hess", str(normalized)]
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

    refresh_backend_versions()
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
    "refresh_backend_versions",
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
