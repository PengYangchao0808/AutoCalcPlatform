"""METHOD_META calculation semantics — the single source of truth.

Moved verbatim from ``acp.catalog`` (plan todo 7, F5): the basis catalog,
the per-functional calculation-semantics metadata (``METHOD_META``) and its
registry derivation live here so that ``cccp`` never reverse-imports ``acp``.
``acp.catalog`` re-exports these tables and keeps only UI composition
(``FIELD_DEFINITIONS`` / ``METHOD_SCHEMAS`` / the
``FUNCTIONAL_OPTIONS_MAP`` projection / legacy field migration).

Layering contract (plan todo 7 boundary):

* **ACP** decides which workflow level/profile applies (orchestration).
* **CCCP** (this module + :mod:`cccp.qc.resolved_spec`) interprets the level's
  scientific parameters, fills method-inherent defaults and renders inputs.
  Everything is resolved from explicitly passed context — execution never
  re-reads global configuration.

Every value here is consumed byte-identically by the ``/api/v1/method-catalog``
payload (``tests/test_p0_characterization.py::TestMethodMetaSnapshot``) and by
the ORCA route renderer goldens.  Values must not drift.
"""

from __future__ import annotations

from typing import Any

from cccp.qc.keyword_registry import (
    KeywordValueError,
    method_family,
    resolve,
    resolve_implementation,
    scf_rank,
)

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


# ── Public aliases (drop-in names for the moved tables) ──────────────────
# ``acp.catalog`` re-exports these; the underscore names above are kept so
# the moved derivation code reads exactly as it did in its old home.
ALL_BASIS_SETS = _ALL_BASIS_SETS
COMPOSITE_BASIS_SETS = _COMPOSITE_BASIS_SETS
BASIS_CATALOG_REF = _BASIS_CATALOG_REF
AUX_J_BASIS_FALLBACK = _AUX_J_BASIS_FALLBACK
AUX_C_BASIS_FALLBACK = _AUX_C_BASIS_FALLBACK

# Aux fitting-basis field defaults — SINGLE source shared with
# ``acp.catalog.FIELD_DEFINITIONS`` (which references these constants) and
# with ``cccp.qc.resolved_spec`` clamp replacements.
AUX_J_BASIS_DEFAULT: str = "AutoAux"
AUX_C_BASIS_DEFAULT: str = "AutoAux"

# ── Per-functional metadata (basis_inline, ri_support, defaults, etc.) ──
# Single source of truth for frontend data-driven UI logic.
# Key = functional name (standard casing).  Use _case_insensitive_get()
# for lookups to tolerate user-input case variance.
#
# FUNCTIONAL_OPTIONS_MAP (below) is auto-derived from this dict to keep the
# two structures permanently in sync — DevDoc §2.1 specifies that
# ``functional_options_map`` values are "由 METHOD_META 自动生成".

# Solvent-model spellings probed against the registry policy when deriving
# the per-method ``solvent_models`` offer (union of the catalog's
# per-backend solvent_model options).
_SOLVENT_MODEL_PROBE: tuple[str, ...] = ("none", "CPCM", "SMD", "ALPB", "GBSA")


def _derive_registry_fields(method: str) -> dict[str, Any]:
    """Derive ``family`` / ``implementation`` / ``solvent_models`` for *method*.

    Everything comes from ``cccp.qc.keyword_registry`` (the single
    authority): family via :func:`method_family`, the ORCA implementation
    via :func:`resolve_implementation`, and ``solvent_models`` only when the
    registry POLICY restricts the set for the family (e.g. GFN under ORCA
    is ``{none, ALPB}`` — GBSA/CPCM/SMD raise ``KeywordValueError``).
    Unrestricted methods get no ``solvent_models`` key (the field-level
    ``per_backend`` options remain their offer).

    Raises:
        ValueError: The method is unknown to the registry.  Fix by extending
            ``cccp.qc.keyword_registry._METHOD_FAMILY_TABLE`` (+ its tests),
            never by silently gating the method.
    """
    family = method_family(method)
    if family == "unknown":
        raise ValueError(
            f"METHOD_META method {method!r} classifies as 'unknown' in "
            "cccp.qc.keyword_registry; extend _METHOD_FAMILY_TABLE there "
            "(and its tests) instead of gating it silently"
        )
    implementation = resolve_implementation(method, engine="orca")
    derived: dict[str, Any] = {"family": family, "implementation": implementation}
    allowed: list[str] = []
    restricted = False
    for model in _SOLVENT_MODEL_PROBE:
        try:
            resolve("solvent_model", model, family=family, implementation=implementation)
        except KeywordValueError:
            restricted = True
            continue
        allowed.append(model)
    if restricted:
        derived["solvent_models"] = allowed
    return derived


METHOD_META: dict[str, dict[str, Any]] = {
    # ── 3c composite methods (built-in basis set, RI fully fixed) ──
    "r2SCAN-3c": {
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
        "basis_inline": False,
        "ri_support": "composite",
        "basis": ("def2-mTZVPP",),
        "dispersion": ("D4", "none"),
        "builtin_dispersion": "D4",
        "default_basis": "def2-mTZVPP",
        "default_dispersion": "none",
    },
    "PBEh-3c": {
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
        "basis_inline": False,
        "ri_support": "composite",
        "basis": ("def2-mSVP",),
        "dispersion": ("D3BJ", "none"),
        "builtin_dispersion": "D3BJ",
        "default_basis": "def2-mSVP",
        "default_dispersion": "none",
    },
    "B97-3c": {
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": True},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "capabilities": {"gradient": True, "optimization": True, "scan_optimization": False},
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
        "scf_convergence": ("tight", "verytight"),
    },
    # ── GFN semi-empirical methods (no basis / dispersion / RI layer) ──
    # ``basis: ()`` -> ``functional_options_map`` derives ``[]`` (NOT
    # ``[""]``): GFN advertises no basis at all.  ``dispersion: ()`` is the
    # same locked/empty set (the built-in correction can never be
    # overridden).  ``ri_support: "composite"`` makes canonical_level /
    # _resolve_field_default clear RI/aux to none/empty.  ``family``,
    # ``implementation`` and ``solvent_models`` are DERIVED from the cccp
    # keyword registry (see _derive_registry_fields) — never hand-encoded.
    "GFN2-xTB": {
        "basis_inline": True,
        "ri_support": "composite",
        "basis": (),
        "dispersion": (),
        "builtin_dispersion": "D4",
        "default_basis": "",
        "default_dispersion": "none",
    },
    "GFN1-xTB": {
        "basis_inline": True,
        "ri_support": "composite",
        "basis": (),
        "dispersion": (),
        "builtin_dispersion": "D3",
        "default_basis": "",
        "default_dispersion": "none",
    },
    "GFN0-xTB": {
        "basis_inline": True,
        "ri_support": "composite",
        "basis": (),
        "dispersion": (),
        "builtin_dispersion": "D4",
        "default_basis": "",
        "default_dispersion": "none",
    },
    "GFN-FF": {
        "basis_inline": True,
        "ri_support": "composite",
        "basis": (),
        "dispersion": (),
        "builtin_dispersion": "builtin",
        "default_basis": "",
        "default_dispersion": "none",
    },
}


for _method_name, _method_meta in METHOD_META.items():
    _method_meta.update(_derive_registry_fields(_method_name))


# ── Query API ────────────────────────────────────────────────────────────


def case_insensitive_get(mapping: dict[str, Any], key: str) -> Any | None:
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


def method_meta(method: str | None) -> dict[str, Any] | None:
    """Return the ``METHOD_META`` entry for *method* (case-insensitive).

    ``None`` when *method* is falsy or not declared.  This is the ONLY
    supported lookup for method semantics (the ORCA renderer, the parameter
    resolver and ``acp.catalog`` all route through it).
    """
    if not method:
        return None
    return case_insensitive_get(METHOD_META, method)


def options_for_meta(meta: dict[str, Any]) -> dict[str, list[str]]:
    """Derive the allowed ``basis``/``dispersion`` option lists from *meta*.

    Single derivation rule used by the UI projection
    (``acp.catalog._derive_functional_options_map``) AND by the parameter
    resolver (``cccp.qc.resolved_spec``) — never re-implement the sentinel
    expansion / composite filtering anywhere else.
    """
    raw_basis = meta.get("basis", ())
    if raw_basis is _BASIS_CATALOG_REF:
        basis_list = list(_ALL_BASIS_SETS)
        ri_support = meta.get("ri_support", "user")
        if ri_support != "composite":
            basis_list = [b for b in basis_list if b not in _COMPOSITE_BASIS_SETS]
    else:
        basis_list = list(raw_basis)
    return {"basis": basis_list, "dispersion": list(meta.get("dispersion", ()))}


def functional_options(method: str | None) -> dict[str, list[str]] | None:
    """Allowed ``basis``/``dispersion`` lists for *method* (case-insensitive).

    ``None`` when the method is unknown (callers treat that as "no opinion").
    """
    meta = method_meta(method)
    if meta is None:
        return None
    return options_for_meta(meta)


def scf_constraint(method: str | None) -> dict[str, Any] | None:
    """Return the method's SCF convergence constraint (``None`` = unconstrained).

    Declared by the optional ``scf_convergence`` allowed-set tuple (same
    pattern as ``basis`` / ``dispersion``).  ``default`` is the minimum-ranked
    allowed value — the platform floor emitted when the caller supplied no
    explicit convergence; weaker explicit values are promoted to it.
    """
    meta = method_meta(method)
    if meta is None:
        return None
    allowed = meta.get("scf_convergence")
    if not allowed:
        return None
    ranked = [(scf_rank(str(value)), str(value)) for value in allowed]
    ranked = [(rank, value) for rank, value in ranked if rank is not None]
    if not ranked:
        return None
    return {"allowed": tuple(str(value) for value in allowed), "default": min(ranked)[1]}


def derive_functional_options_map() -> dict[str, dict[str, list[str]]]:
    """Project ``METHOD_META`` into ``{func: {basis, dispersion}}`` lists."""
    out: dict[str, dict[str, list[str]]] = {}
    for func, meta in METHOD_META.items():
        out[func] = options_for_meta(meta)
    return out


_AUX_FALLBACKS: dict[str, dict[str, list[str]]] = {
    "aux_j_basis": {"orca": AUX_J_BASIS_FALLBACK},
    "aux_c_basis": {"orca": AUX_C_BASIS_FALLBACK},
}


def aux_basis_options(
    field_name: str,
    functional: str | None,
    basis: str | None,
    *,
    engine: str = "orca",
) -> list[str]:
    """Allowed aux fitting-basis options for *field_name* (dynamic derivation).

    Rules (moved from ``acp.catalog._resolve_field_options``'s
    ``dynamic_aux_basis`` branch — single derivation):

    * ``aux_c_basis`` is hidden entirely when the method declares
      ``needs_aux_c`` falsy.
    * a basis-tailored fitting basis is prepended to the fallback list when
      ``BASIS_CATALOG`` carries one for the current *basis*;
    * engines without a declared fallback list (everything but ``orca``)
      offer no options.
    """
    if functional:
        meta = method_meta(functional)
        if meta is not None and field_name == "aux_c_basis" and not meta.get("needs_aux_c", False):
            return []
    fallback = list(_AUX_FALLBACKS.get(field_name, {}).get(engine, []))
    if basis and basis in BASIS_CATALOG:
        aux_kind = "j" if field_name == "aux_j_basis" else "c"
        tailored = BASIS_CATALOG[basis].get(f"aux_{aux_kind}")
        if tailored and tailored not in fallback:
            return [tailored] + fallback
    return fallback


def aux_basis_default(
    field_name: str,
    functional: str | None,
    basis: str | None,
) -> str | None:
    """Method-opinion default aux fitting basis for *field_name*.

    Returns the basis-derived fitting basis, ``""`` when the method declares
    no ``needs_aux_c`` layer, or ``None`` when there is no opinion at all
    (callers fall back to :data:`AUX_J_BASIS_DEFAULT` / :data:`AUX_C_BASIS_DEFAULT`).
    ri_support forced-clear is NOT applied here — that is a method rule
    (see :mod:`cccp.qc.resolved_spec`).
    """
    meta = method_meta(functional) if functional else None
    if meta is None:
        return None
    if field_name == "aux_j_basis":
        if basis:
            basis_meta = BASIS_CATALOG.get(basis)
            if basis_meta and basis_meta.get("aux_j"):
                return basis_meta["aux_j"]
        return None
    if field_name == "aux_c_basis":
        if not meta.get("needs_aux_c", False):
            return ""
        if basis:
            basis_meta = BASIS_CATALOG.get(basis)
            if basis_meta and basis_meta.get("aux_c"):
                return basis_meta["aux_c"]
        return None
    return None
