"""Solvent-name mapping for QC backends.

Two layers (plan T9 / IS-4):

1. **Name alias resolution** — case/whitespace-folded input is mapped to one
   canonical xTB spelling (``dichloromethane``/``dcm`` → ``ch2cl2``,
   ``chloroform`` → ``chcl3``, ``meacn`` → ``acetonitrile``, …).  Unknown
   names raise :class:`SolventValueError` with the legal list; they are never
   passed through raw.
2. **Combination legality** — validated as implementation × solvent model ×
   solvent.  ALPB and GBSA have DIFFERENT official solvent sets and GBSA
   carries per-method restrictions (``benzene`` is GFN1-only, ``dmf`` and
   ``n-hexane`` are GFN2-only); GFN0-xTB has no ALPB parameterization.

The validation entry point (:func:`resolve_xtb_solvent`) receives the FINAL
EFFECTIVE method and solvent model — callers must pass call-time overrides of
initialization defaults, never the initialization defaults alone.  The
implementation is resolved through
:func:`cccp.qc.keyword_registry.resolve_implementation`.

The canonical spelling is one name per solvent identity (the ALPB spelling),
chosen so the xTB binary accepts it under both models (xTB 6.7.1 aliases
``water``/``h2o`` and ``hexane``/``n-hexane`` internally — verified).  The
official per-model spelling sets are exposed as :data:`XTB_ALPB_SOLVENTS` /
:data:`XTB_GBSA_SOLVENTS` and drive legality checks and error messages.

ORCA solvation (:func:`orca_smd_solvent`) is intentionally untouched: long
names are already valid for ORCA ALPB/SMD (Table 3.24, case-insensitive).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

SOLVENT_ALIASES: Dict[str, str] = {
    "acetone": "Acetone",
    "water": "Water",
    "dmso": "DimethylSulfoxide",
    "acetonitrile": "Acetonitrile",
    "meacn": "Acetonitrile",
    "me cn": "Acetonitrile",
    "dichloromethane": "Dichloromethane",
    "dcm": "Dichloromethane",
    "thf": "TetraHydroFuran",
    "methanol": "Methanol",
    "ethanol": "Ethanol",
    "toluene": "Toluene"
}


class SolventValueError(ValueError):
    """Unknown solvent name or an illegal xTB solvent combination.

    Raised by :func:`xtb_solvent` / :func:`resolve_xtb_solvent` instead of
    passing unrecognised names through to the xTB binary.
    """


# ── Official per-model solvent sets (xTB documentation) ─────────────────

#: Official ALPB solvent names (24).
XTB_ALPB_SOLVENTS: frozenset[str] = frozenset({
    "acetone", "acetonitrile", "aniline", "benzaldehyde", "benzene",
    "ch2cl2", "chcl3", "cs2", "dioxane", "dmf", "dmso", "ether",
    "ethylacetate", "furane", "hexadecane", "hexane", "methanol",
    "nitromethane", "octanol", "woctanol", "phenol", "toluene", "thf",
    "water",
})

#: Official GBSA solvent names (14).  ``h2o`` and ``n-hexane`` are the GBSA
#: spellings of the ``water`` / ``hexane`` identities.
XTB_GBSA_SOLVENTS: frozenset[str] = frozenset({
    "acetone", "acetonitrile", "benzene", "ch2cl2", "chcl3", "cs2", "dmf",
    "dmso", "ether", "h2o", "methanol", "n-hexane", "thf", "toluene",
})

#: GBSA names that are parameterized for specific GFN methods only.
#: Keyed by the official GBSA spelling; the value is the set of allowed
#: GFN variants (``gfn0``/``gfn1``/``gfn2``/``gfnff``).
XTB_GBSA_METHOD_RESTRICTIONS: Dict[str, frozenset[str]] = {
    "benzene": frozenset({"gfn1"}),
    "dmf": frozenset({"gfn2"}),
    "n-hexane": frozenset({"gfn2"}),
}

# Canonical identity names (the ALPB spelling).  The GBSA-only spellings
# ``h2o`` / ``n-hexane`` map back to these identities.
_WATER_IDENTITY = "water"
_HEXANE_IDENTITY = "hexane"

#: Official GBSA spelling per identity where it differs from the canonical.
_GBSA_OFFICIAL_BY_IDENTITY: Dict[str, str] = {
    _WATER_IDENTITY: "h2o",
    _HEXANE_IDENTITY: "n-hexane",
}

#: Canonical identities legal under GBSA (official spellings in
#: :data:`XTB_GBSA_SOLVENTS`, minus the two identity renames).
_GBSA_IDENTITIES: frozenset[str] = frozenset({
    "acetone", "acetonitrile", "benzene", "ch2cl2", "chcl3", "cs2", "dmf",
    "dmso", "ether", "methanol", "thf", "toluene",
    _WATER_IDENTITY, _HEXANE_IDENTITY,
})

# ── Layer (a): alias resolution ─────────────────────────────────────────
# Keys are pre-folded (whitespace-stripped, lower-case); values are the
# canonical identity names.

XTB_SOLVENT_ALIASES: Dict[str, str] = {
    # identity spellings (canonical names themselves)
    "acetone": "acetone",
    "acetonitrile": "acetonitrile",
    "aniline": "aniline",
    "benzaldehyde": "benzaldehyde",
    "benzene": "benzene",
    "ch2cl2": "ch2cl2",
    "chcl3": "chcl3",
    "cs2": "cs2",
    "dioxane": "dioxane",
    "dmf": "dmf",
    "dmso": "dmso",
    "ether": "ether",
    "ethylacetate": "ethylacetate",
    "furane": "furane",
    "hexadecane": "hexadecane",
    "hexane": _HEXANE_IDENTITY,
    "methanol": "methanol",
    "nitromethane": "nitromethane",
    "octanol": "octanol",
    "woctanol": "woctanol",
    "phenol": "phenol",
    "toluene": "toluene",
    "thf": "thf",
    "water": _WATER_IDENTITY,
    # documented aliases (pre-folded keys: whitespace stripped, lower-case)
    "dichloromethane": "ch2cl2",
    "dcm": "ch2cl2",
    "methylenechloride": "ch2cl2",
    "chloroform": "chcl3",
    "meacn": "acetonitrile",
    "mecn": "acetonitrile",
    "carbondisulfide": "cs2",
    "dimethylformamide": "dmf",
    "dimethylsulfoxide": "dmso",
    "diethylether": "ether",
    "et2o": "ether",
    "etoac": "ethylacetate",
    "furan": "furane",
    "tetrahydrofuran": "thf",
    "meoh": "methanol",
    "methylbenzene": "toluene",
    "1octanol": "octanol",
    "1-octanol": "octanol",
    "wetoctanol": "woctanol",
    "wet-octanol": "woctanol",
    "14dioxane": "dioxane",
    "1,4-dioxane": "dioxane",
    "h2o": _WATER_IDENTITY,
    "nhexane": _HEXANE_IDENTITY,
    "n-hexane": _HEXANE_IDENTITY,
}


def _fold(solvent: Optional[str]) -> str:
    """Fold case and whitespace; other punctuation is preserved."""
    if not solvent:
        return ""
    return re.sub(r"\s+", "", solvent).lower()


def _normalize(solvent: Optional[str]) -> str:
    if not solvent:
        return ""
    return re.sub(r"\s+", "", solvent).lower()


def _legal_names_message(model: str) -> str:
    """Return the legal-name listing for *model* (``"any"`` = union)."""
    if model == "alpb":
        names: List[str] = sorted(XTB_ALPB_SOLVENTS)
    elif model == "gbsa":
        names = sorted(XTB_GBSA_SOLVENTS)
    else:
        names = sorted(XTB_ALPB_SOLVENTS | XTB_GBSA_SOLVENTS)
    return f"legal xTB {model} solvents: {', '.join(names)}"


def xtb_solvent(solvent: Optional[str]) -> str:
    """Return the canonical xTB solvent name (layer a: alias resolution).

    Case/whitespace folding plus alias resolution
    (``dichloromethane``/``dcm`` → ``ch2cl2``, ``chloroform`` → ``chcl3``,
    ``meacn`` → ``acetonitrile``, …).  Empty input returns ``""``.

    Args:
        solvent: User-supplied solvent spelling.

    Returns:
        The canonical (lower-case) xTB solvent name.

    Raises:
        SolventValueError: The name is not a known xTB solvent; the message
            lists the legal names (never raw passthrough).
    """
    if not solvent:
        return ""
    key = _fold(solvent)
    canonical = XTB_SOLVENT_ALIASES.get(key)
    if canonical is None:
        raise SolventValueError(
            f"Unknown xTB solvent {solvent!r}; {_legal_names_message('any')}"
        )
    return canonical


# ── Layer (b): combination legality (implementation × model × solvent) ──

_GFN_VARIANT_BY_METHOD: Dict[str, str] = {
    "GFN0-XTB": "gfn0",
    "GFN1-XTB": "gfn1",
    "GFN2-XTB": "gfn2",
    "GFN-FF": "gfnff",
    "GFNFF": "gfnff",
}

_METHOD_BY_GFN_VARIANT: Dict[str, str] = {
    "gfn0": "GFN0-xTB",
    "gfn1": "GFN1-xTB",
    "gfn2": "GFN2-xTB",
    "gfnff": "GFN-FF",
}

LEGAL_SOLVENT_MODELS: frozenset[str] = frozenset({"none", "alpb", "gbsa"})


def xtb_method_name(gfn: int | str) -> str:
    """Map a GFN level (int or loose spelling) to the registry method name.

    Args:
        gfn: ``0``/``1``/``2``, ``"gfnff"``, ``"gfn2"``, ``"GFN-FF"``, …

    Returns:
        A :func:`cccp.qc.keyword_registry.resolve_implementation`-compatible
        method spelling (``"GFN0-xTB"``, ``"GFN1-xTB"``, ``"GFN2-xTB"``,
        ``"GFN-FF"``).

    Raises:
        ValueError: The level/spelling is not a known GFN method.
    """
    if isinstance(gfn, int) and not isinstance(gfn, bool):
        variant = {0: "gfn0", 1: "gfn1", 2: "gfn2"}.get(gfn)
        if variant is None:
            raise ValueError(
                f"Unknown GFN level {gfn!r}; legal levels: 0, 1, 2 ('gfnff')"
            )
        return _METHOD_BY_GFN_VARIANT[variant]
    key = _fold(gfn)
    table = {
        "0": "gfn0", "gfn0": "gfn0", "gfn0-xtb": "gfn0", "gfn0xtb": "gfn0",
        "1": "gfn1", "gfn1": "gfn1", "gfn1-xtb": "gfn1", "gfn1xtb": "gfn1",
        "2": "gfn2", "gfn2": "gfn2", "gfn2-xtb": "gfn2", "gfn2xtb": "gfn2",
        "ff": "gfnff", "gfnff": "gfnff", "gfn-ff": "gfnff", "gfnff-xtb": "gfnff",
    }
    variant = table.get(key)
    if variant is None:
        raise ValueError(
            f"Unknown GFN method {gfn!r}; legal spellings: "
            f"{', '.join(sorted(_METHOD_BY_GFN_VARIANT.values()))}"
        )
    return _METHOD_BY_GFN_VARIANT[variant]


def _gfn_variant(method: str) -> str:
    """Return the GFN variant key for a resolved registry method spelling."""
    normalized = "".join(str(method).split()).upper()
    variant = _GFN_VARIANT_BY_METHOD.get(normalized)
    if variant is None:
        raise SolventValueError(
            f"Cannot determine the GFN variant of method {method!r}; "
            f"legal methods: {', '.join(sorted(_METHOD_BY_GFN_VARIANT.values()))}"
        )
    return variant


def _official_name(identity: str, model: str) -> str:
    """Return the official *model* spelling of a canonical identity."""
    if model == "gbsa":
        return _GBSA_OFFICIAL_BY_IDENTITY.get(identity, identity)
    return identity


def _normalize_model(solvent_model: Optional[str]) -> str:
    model = (solvent_model or "none").strip().lower()
    if model not in LEGAL_SOLVENT_MODELS:
        raise SolventValueError(
            f"Unknown xTB solvent model {solvent_model!r}; "
            f"legal solvent models: {', '.join(sorted(LEGAL_SOLVENT_MODELS))}"
        )
    return model


def resolve_xtb_solvent(
    solvent: Optional[str],
    *,
    method: str,
    solvent_model: str,
    engine: str = "xtb",
) -> str:
    """Return the canonical xTB solvent name, validating the combination.

    Layers (a) + (b): alias resolution and implementation × solvent model ×
    solvent legality.  This is THE validation entry point for the xTB-binary
    interfaces; callers must pass the FINAL EFFECTIVE method and solvent
    model (including call-time overrides of initialization defaults).

    Args:
        solvent: User-supplied solvent spelling (any case/spacing).
        method: Effective method spelling (``"GFN2-xTB"``, ``"GFN-FF"``, …),
            e.g. via :func:`xtb_method_name`.
        solvent_model: Effective solvent model (``"none"``, ``"alpb"``,
            ``"gbsa"``).
        engine: Engine for
            :func:`cccp.qc.keyword_registry.resolve_implementation`
            (default ``"xtb"`` — the standalone xTB binary).

    Returns:
        The canonical solvent name, or ``""`` when no solvation applies
        (empty solvent or model ``"none"``).

    Raises:
        SolventValueError: Unknown solvent name, unknown solvent model, or an
            illegal method/model/solvent combination.  The message explains
            the violation and lists the legal names.
        cccp.qc.keyword_registry.KeywordValueError: ``method``/``engine`` do
            not resolve in the keyword registry.
    """
    if not solvent:
        return ""
    model = _normalize_model(solvent_model)
    if model == "none":
        return ""
    # Imported lazily: cccp.qc.__init__ imports the QC interfaces, which
    # import this module (circular at module-import time).
    from cccp.qc.keyword_registry import IMPL_XTB_BINARY, resolve_implementation

    implementation = resolve_implementation(method, engine=engine)
    if implementation != IMPL_XTB_BINARY:
        raise SolventValueError(
            f"Solvent rendering for implementation {implementation!r} is not "
            f"the xTB-binary path (method={method!r}, engine={engine!r}); "
            "the ORCA path has its own solvent policy (ALPB/SMD names)."
        )
    variant = _gfn_variant(method)
    canonical = xtb_solvent(solvent)  # layer (a) — raises with legal list

    if model == "alpb":
        if variant == "gfn0":
            raise SolventValueError(
                "GFN0-xTB has no ALPB parameterization (method=GFN0-xTB, "
                f"solvent_model=alpb, solvent={canonical!r}); use GBSA or "
                "another GFN method; "
                f"{_legal_names_message('alpb')}"
            )
        if canonical not in XTB_ALPB_SOLVENTS:
            raise SolventValueError(
                f"Solvent {_official_name(canonical, 'alpb')!r} is not "
                f"parameterized for ALPB; {_legal_names_message('alpb')}"
            )
        return canonical

    official = _official_name(canonical, "gbsa")
    if canonical not in _GBSA_IDENTITIES:
        raise SolventValueError(
            f"Solvent {official!r} is not parameterized for GBSA; "
            f"{_legal_names_message('gbsa')}"
        )
    allowed = XTB_GBSA_METHOD_RESTRICTIONS.get(official)
    if allowed is not None and variant not in allowed:
        labels = "/".join(item.upper() for item in sorted(allowed))
        methods = ", ".join(
            _METHOD_BY_GFN_VARIANT[item] for item in sorted(allowed)
        )
        raise SolventValueError(
            f"{official} is {labels}-only for GBSA "
            f"(allowed methods: {methods}; effective method={method!r})"
        )
    return canonical


def xtb_solvent_args(
    solvent: Optional[str],
    *,
    method: str,
    solvent_model: str,
    engine: str = "xtb",
) -> List[str]:
    """Return the xTB solvation flags (``[]``, or ``--alpb``/``--gbsa`` + name).

    Thin wrapper over :func:`resolve_xtb_solvent` so every interface emits
    the same flags from the same validated canonical name.

    Args:
        solvent: Effective (call-time) solvent spelling.
        method: Effective method spelling.
        solvent_model: Effective solvent model.
        engine: Engine for implementation resolution.

    Returns:
        ``[]`` when no solvation applies, else ``[flag, canonical_name]``.
    """
    name = resolve_xtb_solvent(
        solvent, method=method, solvent_model=solvent_model, engine=engine
    )
    if not name:
        return []
    model = _normalize_model(solvent_model)
    flag = "--gbsa" if model == "gbsa" else "--alpb"
    return [flag, name]


def orca_smd_solvent(solvent: Optional[str]) -> str:
    if not solvent:
        return ""
    key = _normalize(solvent)
    return SOLVENT_ALIASES.get(key, solvent)
