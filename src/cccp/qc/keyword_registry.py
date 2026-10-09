"""
Unified Keyword Registry
========================

Single source of truth for method-family classification, engine-specific
method implementations, per-domain keyword canonicalization, (family x
implementation) applicability, and platform policy decisions.

Scope discipline (T2 contract):

* Keyword/value logic ONLY — no I/O, no subprocess, no route-line assembly.
  Renderers (``!`` simple-input lines, xTB argv) live in the interface layer
  and consume the canonical tokens produced here.
* No ``acp`` import — ``acp.catalog`` derives its dropdowns FROM this
  registry's vocabulary (T11), never the other way round.
* Fail-fast on unknown enum values; free-form values pass through unchanged
  (never case-fold free text — AGENTS anti-pattern #33).

The three orthogonal decision layers are kept strictly distinct:

* **capability** — what the software can do (ORCA 6.1 external-interface
  table lists GFN0-xTB; the T22 probe completes when ``XTBPATH`` is fixed).
* **dependency** — what the local deployment provides (``param_gfn0-xtb.txt``
  is missing from ORCA-resolvable paths).
* **policy** — what the platform allows (GFN0-xTB is xTB-binary-only on the
  ORCA path; GFN solvent under ORCA is ``{none, ALPB}``; GFN+NMR default
  reject). Probe verdicts update capability/dependency records; they NEVER
  auto-rewrite policy.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "APPLICABILITY_TABLE",
    "APPLICABLE_FIELDS",
    "CalculationPolicy",
    "ENUM_DOMAINS",
    "ENGINES",
    "FREE_FORM_DOMAINS",
    "FAMILIES",
    "GFN_NMR_DEFAULT_ALLOWED",
    "IMPLEMENTATIONS",
    "IMPL_ORCA_DFT",
    "IMPL_ORCA_EXTERNAL_XTB",
    "IMPL_ORCA_NATIVE",
    "IMPL_XTB_BINARY",
    "KeywordValueError",
    "MethodPolicy",
    "ORCA_FUNCTIONAL_ALIASES",
    "PolicyDecision",
    "canonical_token",
    "calculation_policy",
    "is_applicable",
    "legal_values",
    "method_family",
    "method_policy",
    "orca_native_functional",
    "resolve",
    "resolve_implementation",
]


# ── Typed error (contractual name) ──────────────────────────────────────


class KeywordValueError(ValueError):
    """Raised for illegal keyword values, domains, or method/engine pairs.

    One typed error for the whole registry so callers can guard a single
    exception type. Every message names the offending domain/value or
    method/engine plus the legal alternatives.
    """


# ── Vocabularies ────────────────────────────────────────────────────────

FAMILIES: frozenset[str] = frozenset(
    {"conventional_dft", "composite_3c", "gfn", "gfnff", "unknown"}
)

IMPL_ORCA_EXTERNAL_XTB = "orca_external_xtb"
IMPL_ORCA_NATIVE = "orca_native"
IMPL_XTB_BINARY = "xtb_binary"
IMPL_ORCA_DFT = "orca_dft"

IMPLEMENTATIONS: frozenset[str] = frozenset(
    {IMPL_ORCA_EXTERNAL_XTB, IMPL_ORCA_NATIVE, IMPL_XTB_BINARY, IMPL_ORCA_DFT}
)

ENGINES: frozenset[str] = frozenset({"orca", "xtb"})

#: Enumerated domains: unknown values raise :class:`KeywordValueError`.
ENUM_DOMAINS: frozenset[str] = frozenset(
    {"opt_level", "scf_convergence", "scf_strategy", "grid", "dispersion"}
)

#: Free-form domains: values pass through unchanged (never case-folded).
FREE_FORM_DOMAINS: frozenset[str] = frozenset({"basis", "ri", "aux", "solvent", "solvent_model"})

#: Every field governed by the applicability table (enum + free-form).
APPLICABLE_FIELDS: frozenset[str] = ENUM_DOMAINS | FREE_FORM_DOMAINS

#: Token-less values whose semantics are "emit nothing" (true no-ops).
_NOOP_KEYS: frozenset[str] = frozenset({"normal", "none"})

# GFN+NMR policy switch (T8). CLOSED by default: GFN+NMR is rejected until
# artifact-level support is proven (a completed ORCA run with NON-EMPTY
# parsed shielding tensors — rc=0 is NOT sufficient, see T22 case 11).
GFN_NMR_DEFAULT_ALLOWED: bool = False


# ── Method families ─────────────────────────────────────────────────────

_NATIVE_PREFIX = "NATIVE-"

# Normalized (whitespace-stripped, upper-case) method spellings. Anything
# not listed and not matched by a documented rule classifies as ``unknown``.
_METHOD_FAMILY_TABLE: dict[str, str] = {
    # GFN semi-empirical — ORCA EXTERNAL xTB-interface keywords.
    "GFN2-XTB": "gfn",
    "GFN1-XTB": "gfn",
    "GFN0-XTB": "gfn",
    "GFN-FF": "gfnff",
    "GFNFF": "gfnff",
    # 3c composite methods (built-in basis + dispersion).
    "B97-3C": "composite_3c",
    "R2SCAN-3C": "composite_3c",
    "PBEH-3C": "composite_3c",
    # Conventional ORCA electronic-structure methods (DFT and post-HF);
    # "conventional_dft" is the contractual family name for this bucket.
    "WB97X-D4": "conventional_dft",
    "WB97M-V": "conventional_dft",
    "PBE0": "conventional_dft",
    "B3LYP": "conventional_dft",
    "MPW1PW": "conventional_dft",
    "MPW1PW91": "conventional_dft",
    "M062X": "conventional_dft",
    "PWPB95": "conventional_dft",
    "REVDSD-PBEP86": "conventional_dft",
    "DLPNO-CCSD(T)": "conventional_dft",
}


def _normalize_method(method: str) -> str:
    """Return the case/whitespace-insensitive lookup key for ``method``."""
    return "".join(str(method).split()).upper()


def method_family(method: str) -> str:
    """Classify ``method`` into its method family.

    Case- and whitespace-insensitive. ``Native-*`` spellings classify by
    their suffix (native vs external is an *implementation* distinction,
    not a family distinction).

    Args:
        method: Method spelling, e.g. ``"GFN2-xTB"``, ``"GFN-FF"``,
            ``"B97-3c"``, ``"wB97X-D4"``.

    Returns:
        One of ``"conventional_dft"``, ``"composite_3c"``, ``"gfn"``,
        ``"gfnff"``, ``"unknown"``.
    """
    normalized = _normalize_method(method)
    if normalized.startswith(_NATIVE_PREFIX):
        inner = method_family(normalized[len(_NATIVE_PREFIX) :])
        return inner if inner in {"gfn", "gfnff"} else "unknown"
    if normalized in _METHOD_FAMILY_TABLE:
        return _METHOD_FAMILY_TABLE[normalized]
    # Documented rule: any *-3c composite (e.g. future 3c variants).
    if normalized.endswith("-3C"):
        return "composite_3c"
    return "unknown"


def _is_native_method(method: str) -> bool:
    """Return True for ORCA-native ``Native-*`` method spellings."""
    return _normalize_method(method).startswith(_NATIVE_PREFIX)


# ── ORCA-native functional aliases (emission layer, T16) ────────────────
#
# ORCA >= 6 rejects some legacy functional spellings in the simple-input
# line ("UNRECOGNIZED OR DUPLICATED KEYWORD(S)") and exposes the same
# functional under a shorter native keyword. This registry maps the
# REQUESTED level name (what METHOD_META, the Goodman error-model binding
# and every receipt keep verbatim) to the NATIVE token an engine actually
# parses. Consumers are emission/provenance sites only — the ORCA NMR
# input renderer and the NMR workflow's executed-keyword receipt — NEVER a
# rewrite of recorded levels (Goodman level semantics stay on the request).

#: Requested functional spelling (case/whitespace-folded) → ORCA-native keyword.
ORCA_FUNCTIONAL_ALIASES: dict[str, str] = {
    # ORCA 6.x spells the modified-PW 1-parameter hybrid as ``mPW1PW``.
    "MPW1PW91": "mPW1PW",
}


def orca_native_functional(method: str) -> str:
    """Return the ORCA-native simple-input keyword for a requested functional.

    Case/whitespace-insensitive alias lookup. A spelling without an alias
    returns unchanged — the requested name IS the emitted keyword.

    Args:
        method: Requested functional spelling, e.g. ``"mPW1PW91"``.

    Returns:
        The ORCA-native keyword (``"mPW1PW"`` for ``"mPW1PW91"``), or
        *method* verbatim when no alias applies.
    """
    return ORCA_FUNCTIONAL_ALIASES.get(_normalize_method(method), str(method))


# ── Implementations ─────────────────────────────────────────────────────


def _implementation_class(implementation: str) -> str:
    """Map an implementation id to its token dialect ("orca" or "xtb")."""
    return "xtb" if implementation == IMPL_XTB_BINARY else "orca"


def _validate_family(family: str) -> None:
    if family not in FAMILIES:
        raise KeywordValueError(
            f"Unknown method family {family!r}; legal families: {sorted(FAMILIES)}"
        )


def _validate_implementation(implementation: str) -> None:
    if implementation not in IMPLEMENTATIONS:
        raise KeywordValueError(
            f"Unknown implementation {implementation!r}; "
            f"legal implementations: {sorted(IMPLEMENTATIONS)}"
        )


def _invalid_pair(method: str, engine: str, detail: str) -> KeywordValueError:
    """Build the typed error for an invalid (method, engine) pair.

    The message always names method, engine, and the legal implementations.
    """
    return KeywordValueError(
        f"Invalid (method, engine) pair: method={method!r}, engine={engine!r}. "
        f"{detail} Legal implementations: {sorted(IMPLEMENTATIONS)}"
    )


def resolve_implementation(method: str, *, engine: str) -> str:
    """Resolve the concrete implementation of ``method`` on ``engine``.

    The same method resolves differently per engine: ``GFN2-xTB`` is ORCA's
    EXTERNAL xTB-interface keyword on the ORCA engine (``orca_external_xtb``)
    but the standalone xTB binary on the ``xtb`` engine (``xtb_binary``);
    ``Native-GFN2-xTB`` is the ORCA-native implementation (``orca_native``).

    Note:
        ``GFN0-xTB`` resolves to ``orca_external_xtb`` on ORCA (it is a legal
        external keyword — capability) but is REJECTED by platform policy;
        query :func:`method_policy` for that gate.

    Args:
        method: Method spelling (case/whitespace-insensitive).
        engine: ``"orca"`` or ``"xtb"`` (case/whitespace-insensitive).

    Returns:
        One of :data:`IMPLEMENTATIONS`.

    Raises:
        KeywordValueError: Invalid (method, engine) combination; the message
            names method, engine, and the legal implementations.
    """
    engine_key = str(engine).strip().lower()
    if engine_key not in ENGINES:
        raise _invalid_pair(
            method,
            engine,
            f"Unknown engine {engine!r}; legal engines: {sorted(ENGINES)}.",
        )
    family = method_family(method)
    if family == "unknown":
        raise _invalid_pair(
            method,
            engine,
            "Method is not in the registry's method table (family 'unknown').",
        )
    if _is_native_method(method):
        if engine_key == "orca":
            return IMPL_ORCA_NATIVE
        raise _invalid_pair(
            method,
            engine,
            "Native-* methods are ORCA-native implementations only; "
            "the standalone xTB engine uses the plain GFN* spellings.",
        )
    if family in {"gfn", "gfnff"}:
        return IMPL_ORCA_EXTERNAL_XTB if engine_key == "orca" else IMPL_XTB_BINARY
    # conventional_dft / composite_3c
    if engine_key == "orca":
        return IMPL_ORCA_DFT
    raise _invalid_pair(
        method,
        engine,
        f"Family {family!r} methods run on the ORCA engine only; "
        "the standalone xTB binary implements GFN methods exclusively.",
    )


# ── Applicability (family x implementation) ─────────────────────────────

# Fields stripped for the whole GFN family on EVERY implementation: no xTB
# method (external interface, native, or standalone binary) consumes a DFT
# basis, dispersion, grid, RI, or auxiliary basis. T22 confirmed ORCA even
# accepts-but-ignores basis/D4/DefGrid3 on the external path — so we strip
# them here and never emit them (T6).
_GFN_STRIP_FIELDS: frozenset[str] = frozenset({"basis", "dispersion", "grid", "ri", "aux"})

_STRIP_BY_FAMILY: dict[str, frozenset[str]] = {
    "gfn": _GFN_STRIP_FIELDS,
    "gfnff": _GFN_STRIP_FIELDS,
}

#: Full (family, implementation) -> applicable-fields table. Anything not
#: listed applies everywhere (e.g. opt_level / scf_convergence / scf_strategy
#: are governed by their enum tables but never stripped).
APPLICABILITY_TABLE: dict[tuple[str, str], frozenset[str]] = {
    (family, implementation): APPLICABLE_FIELDS - _STRIP_BY_FAMILY.get(family, frozenset())
    for family in sorted(FAMILIES)
    for implementation in sorted(IMPLEMENTATIONS)
}


def is_applicable(field: str, *, family: str, implementation: str) -> bool:
    """Return whether ``field`` applies for (family, implementation).

    Args:
        field: Domain/field name (see :data:`APPLICABLE_FIELDS`).
        family: Method family from :func:`method_family`.
        implementation: Implementation id from :func:`resolve_implementation`.

    Returns:
        True when the field may produce a keyword; False when its values are
        stripped (never emitted) for this (family, implementation).

    Raises:
        KeywordValueError: Unknown family, implementation, or field.
    """
    _validate_family(family)
    _validate_implementation(implementation)
    field_key = str(field).strip().lower()
    if field_key not in APPLICABLE_FIELDS:
        raise KeywordValueError(
            f"Unknown applicability field {field!r}; legal fields: {sorted(APPLICABLE_FIELDS)}"
        )
    return field_key in APPLICABILITY_TABLE[(family, implementation)]


# ── Enum tables (canonical tokens) ──────────────────────────────────────
#
# Each entry maps a normalized input key to ``(orca_token, xtb_token)``.
# ``None`` means "emit no keyword".  Dialect notes:
#
# * opt_level has genuinely engine-native spellings (xtb ``--opt <level>``
#   consumes ``crude``/``tight``/``verytight``; ORCA consumes ``LooseOpt``/
#   ``TightOpt``/``VeryTightOpt``), so the two columns differ and a value
#   with no token on the current dialect resolves to ``(None, warning)``.
# * The remaining domains emit ORCA simple-input keywords; renderers for
#   other engines interpret these canonical tokens.
#
# Semantics superseded (must be preserved exactly) from
# ``cccp.qc.interfaces.orca``: ``_OPT_LEVEL_MAP`` (tight / verytight /
# very_tight alias / loose; ``normal`` absent = no-op),
# ``_SCF_CONVERGENCE_MAP`` (tight / verytight / loose),
# ``_SCF_STRATEGY_MAP`` (slowconv / soscf), ``_GRID_KEYWORD_MAP``
# (defgrid1/2/3), ``_DISPERSION_KEYWORD_MAP`` (d3 / d3bj / d4 / vv10), and
# ``cccp.qc.interfaces.orca_ts``: ``_OPT_LEVEL_KEYWORDS`` (loose / normal
# -> None / tight / verytight).

_ENUM_TABLES: dict[str, dict[str, tuple[str | None, str | None]]] = {
    "opt_level": {
        "loose": ("LooseOpt", None),
        "normal": (None, None),
        "tight": ("TightOpt", "tight"),
        "verytight": ("VeryTightOpt", "verytight"),
        # PES contracts spell the level "very_tight"; accepted alias of
        # "verytight" (orca.py _OPT_LEVEL_MAP G3 fix), no migration warning.
        "very_tight": ("VeryTightOpt", "verytight"),
        # xTB-binary-only level (xtbmd_censo_energy _OPT_LEVELS); no ORCA
        # token exists -> resolves to (None, warning) on ORCA implementations.
        "crude": (None, "crude"),
    },
    "scf_convergence": {
        "loose": ("LooseSCF", "LooseSCF"),
        "normal": (None, None),
        "tight": ("TightSCF", "TightSCF"),
        "verytight": ("VeryTightSCF", "VeryTightSCF"),
    },
    "scf_strategy": {
        "normal": (None, None),
        "slowconv": ("SlowConv", "SlowConv"),
        "soscf": ("SOSCF", "SOSCF"),
    },
    "grid": {
        "defgrid1": ("DefGrid1", "DefGrid1"),
        "defgrid2": ("DefGrid2", "DefGrid2"),
        "defgrid3": ("DefGrid3", "DefGrid3"),
        # Legacy (Gaussian-era) aliases -> canonical ORCA tokens with a
        # migration warning; the legacy token is never emitted. UltraFine
        # is rejected by ORCA 6.1 (T22 case 13) so the alias mapping is
        # mandatory pre-assembly, not cosmetic.
        "sg1": ("DefGrid1", "DefGrid1"),
        "fine": ("DefGrid2", "DefGrid2"),
        "ultrafine": ("DefGrid3", "DefGrid3"),
        "superfine": ("DefGrid3", "DefGrid3"),
    },
    "dispersion": {
        "none": (None, None),
        "d3": ("D3", "D3"),
        "d3bj": ("D3BJ", "D3BJ"),
        "d4": ("D4", "D4"),
        "vv10": ("VV10", "VV10"),
    },
}

#: Input keys that are legacy aliases (canonicalized with a migration
#: warning; the legacy token is never emitted).
_LEGACY_ALIAS_KEYS: dict[str, frozenset[str]] = {
    "opt_level": frozenset(),
    "scf_convergence": frozenset(),
    "scf_strategy": frozenset(),
    "grid": frozenset({"sg1", "fine", "ultrafine", "superfine"}),
    "dispersion": frozenset(),
}


def legal_values(domain: str) -> tuple[str, ...]:
    """Return every accepted input spelling for an enumerated ``domain``.

    Includes legacy aliases (they are legal *inputs*, canonicalized on
    resolve). Free-form domains have no enumerated legal set and return an
    empty tuple.

    Args:
        domain: Enumerated or free-form domain name.

    Returns:
        Sorted accepted input spellings, or ``()`` for free-form domains.

    Raises:
        KeywordValueError: Unknown domain.
    """
    domain_key = _normalize_domain(domain)
    if domain_key in ENUM_DOMAINS:
        return tuple(sorted(_ENUM_TABLES[domain_key]))
    return ()


def canonical_token(domain: str, value: str, *, implementation: str) -> tuple[str | None, bool]:
    """Return ``(token, is_true_noop)`` for an enum value on ``implementation``.

    Low-level table probe used by :func:`resolve`; exposed for renderer-side
    queries that need the token without applicability/policy handling.

    Args:
        domain: Enumerated domain name.
        value: Input spelling (case/whitespace-insensitive).
        implementation: Implementation id.

    Returns:
        ``(token, is_true_noop)`` — token is the canonical keyword or None;
        ``is_true_noop`` is True only for engine-default values such as
        ``normal``/``none`` (no keyword AND no warning).

    Raises:
        KeywordValueError: Unknown domain, implementation, or enum value.
    """
    _validate_implementation(implementation)
    domain_key = _normalize_domain(domain)
    if domain_key not in ENUM_DOMAINS:
        raise KeywordValueError(
            f"Unknown enum domain {domain!r}; legal enum domains: {sorted(ENUM_DOMAINS)}"
        )
    key = _lookup_key(value)
    table = _ENUM_TABLES[domain_key]
    if key not in table:
        raise _unknown_enum_error(domain_key, value, table)
    orca_token, xtb_token = table[key]
    token = xtb_token if _implementation_class(implementation) == "xtb" else orca_token
    is_true_noop = orca_token is None and xtb_token is None
    return token, is_true_noop


def _normalize_domain(domain: str) -> str:
    domain_key = str(domain).strip().lower()
    if domain_key not in ENUM_DOMAINS and domain_key not in FREE_FORM_DOMAINS:
        raise KeywordValueError(
            f"Unknown keyword domain {domain!r}; legal domains: "
            f"{sorted(ENUM_DOMAINS | FREE_FORM_DOMAINS)}"
        )
    return domain_key


def _lookup_key(value: str) -> str:
    return str(value).strip().lower()


def _unknown_enum_error(
    domain: str, value: str, table: dict[str, tuple[str | None, str | None]]
) -> KeywordValueError:
    """Typed error for an unknown enum value: domain + value + legal values."""
    tokens = sorted({token for pair in table.values() for token in pair if token})
    return KeywordValueError(
        f"Illegal value {value!r} for domain {domain!r}; "
        f"legal values: {sorted(table)}; canonical tokens: {tokens}"
    )


# ── GFN solvent policy (family x implementation) ────────────────────────
#
# PLATFORM POLICY — deliberately distinct from software capability:
# GBSA is not an ORCA keyword at all (capability: ORCA 6.1.1 rejects
# ``GBSA(water)`` as UNRECOGNIZED — T22 case 4), while the platform's
# ORCA-GFN restriction to {none, ALPB} is a policy choice that would hold
# even if ORCA gained GBSA support tomorrow. The standalone xTB binary
# implements both ALPB and GBSA (capability) and the policy allows both.

_GFN_SOLVENT_MODELS: dict[str, frozenset[str]] = {
    "orca": frozenset({"none", "ALPB"}),
    "xtb": frozenset({"none", "ALPB", "GBSA"}),
}

_GFN_FAMILIES: frozenset[str] = frozenset({"gfn", "gfnff"})


# ── resolve() ───────────────────────────────────────────────────────────


def resolve(
    domain: str,
    value: str | None,
    *,
    family: str,
    implementation: str,
) -> tuple[str | None, str | None]:
    """Resolve one keyword value to ``(canonical, warning)``.

    Behavior by domain kind:

    * **Enum domains** (``opt_level``, ``scf_convergence``, ``scf_strategy``,
      ``grid``, ``dispersion``): case-insensitive lookup canonicalized to the
      registry spelling (``defgrid3`` -> ``DefGrid3``; ``very_tight`` ==
      ``verytight``); legacy aliases migrate with a warning; engine-default
      values (``normal``, ``none``) resolve to ``(None, None)``; unknown
      values raise :class:`KeywordValueError` naming domain, value, and the
      legal values — validation happens BEFORE applicability stripping so a
      bogus value can never hide behind a stripped field.
    * **Free-form domains** (``basis``, ``ri``, ``aux``, ``solvent``,
      ``solvent_model``): pass through UNCHANGED (``canonical == value``,
      case preserved — never fold free text).

    Applicability: a value whose field is not applicable for
    (family, implementation) resolves to ``(None, warning)`` — stripped,
    never emitted (e.g. GFN x basis/D4/DefGrid3). GFN solvent models are
    policy-checked per implementation: under ORCA only ``{none, ALPB}`` and
    ``GBSA`` raises (no silent coercion); the xTB binary also allows GBSA.

    Args:
        domain: Keyword domain (see :data:`ENUM_DOMAINS` /
            :data:`FREE_FORM_DOMAINS`).
        value: Input value; ``None``/empty means "unset" -> ``(None, None)``.
        family: Method family from :func:`method_family`.
        implementation: Implementation id from :func:`resolve_implementation`.

    Returns:
        ``(canonical_or_None, warning_or_None)``. ``canonical is None`` means
        "emit nothing"; a warning is present whenever a non-trivial value was
        suppressed or canonicalized.

    Raises:
        KeywordValueError: Unknown domain/value for enum domains, invalid
            family/implementation, or a GFN solvent-model policy violation.
    """
    _validate_family(family)
    _validate_implementation(implementation)
    domain_key = _normalize_domain(domain)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, None
    if domain_key in FREE_FORM_DOMAINS:
        return _resolve_free_form(domain_key, value, family=family, implementation=implementation)
    return _resolve_enum(domain_key, value, family=family, implementation=implementation)


def _resolve_free_form(
    domain: str,
    value: str,
    *,
    family: str,
    implementation: str,
) -> tuple[str | None, str | None]:
    """Pass free-form values through unchanged, minus applicability/policy."""
    if not is_applicable(domain, family=family, implementation=implementation):
        return None, _strip_warning(domain, value, family, implementation)
    if domain == "solvent_model" and family in _GFN_FAMILIES:
        impl_class = _implementation_class(implementation)
        allowed = _GFN_SOLVENT_MODELS[impl_class]
        key = _lookup_key(value)
        if key not in {model.lower() for model in allowed}:
            if impl_class == "orca":
                detail = (
                    "PLATFORM POLICY — ORCA GFN solvent is restricted to "
                    "{none, ALPB}; GBSA is not an ORCA keyword (software "
                    "capability) and the platform never silently coerces "
                    f"models — use engine 'xtb' / implementation "
                    f"{IMPL_XTB_BINARY!r} for GBSA"
                )
            else:
                detail = (
                    "PLATFORM POLICY — GFN solvent on the standalone xTB "
                    "binary is restricted to {none, ALPB, GBSA}; the platform "
                    "never silently coerces models"
                )
            raise KeywordValueError(
                f"Illegal value {value!r} for domain 'solvent_model' with "
                f"family {family!r} on implementation {implementation!r}: "
                f"legal values: {sorted(allowed)} ({detail})"
            )
    return value, None


def _resolve_enum(
    domain: str,
    value: str,
    *,
    family: str,
    implementation: str,
) -> tuple[str | None, str | None]:
    """Canonicalize an enum value with fail-fast validation."""
    table = _ENUM_TABLES[domain]
    key = _lookup_key(value)
    if key not in table:
        raise _unknown_enum_error(domain, value, table)
    orca_token, xtb_token = table[key]
    token = xtb_token if _implementation_class(implementation) == "xtb" else orca_token
    other_token = orca_token if _implementation_class(implementation) == "xtb" else xtb_token
    is_true_noop = orca_token is None and xtb_token is None

    notes: list[str] = []
    if key in _LEGACY_ALIAS_KEYS[domain]:
        shown = token if token is not None else other_token
        notes.append(
            f"{domain} value {value!r} is a legacy alias; canonicalized to "
            f"{shown!r} (the legacy token is never emitted)"
        )
    elif token is None and not is_true_noop:
        notes.append(
            f"{domain} value {value!r} has no keyword equivalent on "
            f"implementation {implementation!r}; no keyword will be emitted"
        )

    if not is_applicable(domain, family=family, implementation=implementation):
        if is_true_noop and not notes:
            return None, None
        notes.insert(0, _strip_warning(domain, value, family, implementation))
        return None, "; ".join(notes)

    if is_true_noop:
        return None, ("; ".join(notes) or None)
    return token, ("; ".join(notes) or None)


def _strip_warning(domain: str, value: str, family: str, implementation: str) -> str:
    """Standard warning emitted when a value is stripped as inapplicable."""
    return (
        f"{domain} is not applicable for (family={family!r}, "
        f"implementation={implementation!r}); value {value!r} is stripped and "
        f"never emitted"
    )


# ── Policy queries (capability / dependency / policy, three levels) ─────


@dataclass(frozen=True)
class PolicyDecision:
    """One platform-policy verdict with its three-level evidence record.

    Attributes:
        allowed: Whether the platform permits the combination.
        reason: Human-readable reason for the verdict.
        capability: Software-capability record (what the software can do).
        dependency: Local-dependency record (what the deployment provides).
        policy: Platform-policy statement (what the platform allows).
    """

    allowed: bool
    reason: str
    capability: str
    dependency: str
    policy: str


# Backwards-facing alias names for the two query flavors (T13 consumes the
# same decision shape either way).
MethodPolicy = PolicyDecision
CalculationPolicy = PolicyDecision

_ALLOWED_CAPABILITY = "supported (no capability restriction recorded)"
_ALLOWED_DEPENDENCY = "no local dependency restriction recorded"
_ALLOWED_POLICY = "allowed by default (no platform policy restriction)"

# GFN0-xTB: capability vs dependency vs policy — three DISTINCT levels.
# The T22 probe recorded outcome_class "missing_dependency" (rc=0 trap) —
# that verdict updates the DEPENDENCY record only. Policy is unchanged.
_GFN0_ORCA_CAPABILITY = (
    "supported: the ORCA 6.1 external-interface table lists GFN0-xTB and the "
    "T22 probe completes normally with XTBPATH fixed "
    "(capability = supported)"
)
_GFN0_ORCA_DEPENDENCY = (
    "local dependency gap: param_gfn0-xtb.txt is missing from ORCA-resolvable "
    "paths (it exists only under the xTB share directory); the probe failed "
    "for this reason alone (dependency, NOT capability)"
)
_GFN0_ORCA_POLICY = (
    "GFN0-xTB is xTB-binary-only on the ORCA path (PLATFORM POLICY, not probe-derived)"
)


def method_policy(method: str, *, engine: str) -> PolicyDecision:
    """Return the platform-policy verdict for ``method`` on ``engine``.

    Queryable method-level gate for entry layers (T13). The flagship rule:
    ``GFN0-xTB`` on the ORCA path is REJECTED (xTB-binary-only) even though
    the software capability exists and the local failure was a dependency
    gap — capability/dependency records never flip policy.

    Args:
        method: Method spelling (case/whitespace-insensitive).
        engine: ``"orca"`` or ``"xtb"``.

    Returns:
        :class:`PolicyDecision` with the three evidence levels filled in.

    Raises:
        KeywordValueError: Unknown engine or method (family ``unknown``).
    """
    implementation = resolve_implementation(method, engine=engine)
    engine_key = str(engine).strip().lower()
    if engine_key == "orca" and _normalize_method(method) == "GFN0-XTB":
        return PolicyDecision(
            allowed=False,
            reason=(
                "GFN0-xTB is rejected on the ORCA path; run it on the "
                "standalone xTB binary (engine 'xtb')"
            ),
            capability=_GFN0_ORCA_CAPABILITY,
            dependency=_GFN0_ORCA_DEPENDENCY,
            policy=_GFN0_ORCA_POLICY,
        )
    return PolicyDecision(
        allowed=True,
        reason=f"method {method!r} is allowed on engine {engine!r} ({implementation})",
        capability=_ALLOWED_CAPABILITY,
        dependency=_ALLOWED_DEPENDENCY,
        policy=_ALLOWED_POLICY,
    )


def calculation_policy(
    calculation: str,
    *,
    family: str,
    implementation: str,
) -> PolicyDecision:
    """Return the platform-policy verdict for a calculation type.

    Queryable calculation-level gate (T13/T8). The flagship rule: GFN+NMR is
    rejected by default (:data:`GFN_NMR_DEFAULT_ALLOWED` is False) because
    the xTB path emits NO shielding artifacts (T22 case 11: rc=0, normal
    termination, ``NmrShieldingParser`` -> 0 atoms). "ORCA exited 0" is not
    an artifact; the switch opens only on artifact-level evidence (T17).

    Args:
        calculation: Calculation kind, e.g. ``"nmr"`` (case-insensitive).
        family: Method family from :func:`method_family`.
        implementation: Implementation id from :func:`resolve_implementation`.

    Returns:
        :class:`PolicyDecision` with the three evidence levels filled in.

    Raises:
        KeywordValueError: Unknown family or implementation.
    """
    _validate_family(family)
    _validate_implementation(implementation)
    calc_key = _lookup_key(calculation)
    if calc_key == "nmr" and family in _GFN_FAMILIES and not GFN_NMR_DEFAULT_ALLOWED:
        return PolicyDecision(
            allowed=False,
            reason=("GFN+NMR is rejected by default (GFN_NMR_DEFAULT_ALLOWED switch is CLOSED)"),
            capability=(
                "artifact-level gap: ORCA accepts the NMR flag on the xTB path "
                "but emits NO shielding tensors (T22 case 11; "
                "NmrShieldingParser returns 0 atoms despite rc=0)"
            ),
            dependency=("no dependency file can produce the missing shielding artifact"),
            policy=(
                "GFN+NMR default reject unless artifact-level support "
                "(PLATFORM POLICY; T8 switch closed — a probe verdict never "
                "auto-flips policy)"
            ),
        )
    return PolicyDecision(
        allowed=True,
        reason=f"calculation {calculation!r} is allowed for family {family!r}",
        capability=_ALLOWED_CAPABILITY,
        dependency=_ALLOWED_DEPENDENCY,
        policy=_ALLOWED_POLICY,
    )
