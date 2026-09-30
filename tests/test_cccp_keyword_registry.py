"""Unit tests for the cccp unified keyword registry (T2 keystone contract).

Pins the registry API that T3/T4/T5/T6/T9/T11/T12/T13 consume:

* :func:`method_family` / :func:`resolve_implementation` classification matrix,
* :func:`resolve` enum canonicalization (superseding orca.py/orca_ts.py maps),
* (family x implementation) applicability (GFN strips basis/dispersion/grid),
* platform policy (ORCA GFN solvent {none, ALPB}; GFN0-xTB xTB-binary-only;
  GFN+NMR default reject).

Catalog-parity: every enum value declared in ``acp.catalog`` (METHOD_META,
METHOD_SCHEMAS-referenced fields, and the FIELD_DEFINITIONS option tables)
must resolve — including the ``normal``/``none`` no-ops. T22 probe parity:
every verdict in ``tests/fixtures/orca_keyword_probe.json`` maps to a
registry decision expressed as *calls*, not prose.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from acp.catalog import FIELD_DEFINITIONS, METHOD_META, METHOD_SCHEMAS
from cccp.qc.keyword_registry import (
    APPLICABILITY_TABLE,
    APPLICABLE_FIELDS,
    ENUM_DOMAINS,
    FAMILIES,
    FREE_FORM_DOMAINS,
    GFN_NMR_DEFAULT_ALLOWED,
    IMPL_ORCA_DFT,
    IMPL_ORCA_EXTERNAL_XTB,
    IMPL_ORCA_NATIVE,
    IMPL_XTB_BINARY,
    IMPLEMENTATIONS,
    KeywordValueError,
    calculation_policy,
    canonical_token,
    is_applicable,
    legal_values,
    method_family,
    method_policy,
    resolve,
    resolve_implementation,
)

_PROBE_FIXTURE = Path(__file__).parent / "fixtures" / "orca_keyword_probe.json"

_DFT_CTX = ("conventional_dft", IMPL_ORCA_DFT)
_ALL_CONTEXTS: list[tuple[str, str]] = [
    ("conventional_dft", IMPL_ORCA_DFT),
    ("composite_3c", IMPL_ORCA_DFT),
    ("gfn", IMPL_ORCA_EXTERNAL_XTB),
    ("gfn", IMPL_XTB_BINARY),
    ("gfnff", IMPL_ORCA_NATIVE),
]


# ─────────────────────────────────────────────────────────────────────────
# Catalog-derived enum harvesting (test-side acp import is expected)
# ─────────────────────────────────────────────────────────────────────────

# Catalog field name -> registry enum domain.  Covers every enumerated
# keyword field the catalog declares (METHOD_SCHEMAS references these by
# name; FIELD_DEFINITIONS carries the option lists).
_FIELD_TO_DOMAIN: dict[str, str] = {
    "opt_level": "opt_level",
    "opt_convergence": "opt_level",
    "scf_convergence": "scf_convergence",
    "scan_optimizer_scf_convergence": "scf_convergence",
    "minimum_scf_convergence": "scf_convergence",
    "scf_strategy": "scf_strategy",
    "minimum_scf_strategy": "scf_strategy",
    "transition_state_scf_strategy": "scf_strategy",
    "grid": "grid",
    "scan_optimizer_grid": "grid",
    "dispersion": "dispersion",
    "scan_optimizer_dispersion": "dispersion",
}


def _method_schema_referenced_fields() -> set[str]:
    """Field names referenced by METHOD_SCHEMAS (``fields`` / ``inherits``)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "fields" and isinstance(value, list):
                    found.update(str(item) for item in value)
                elif key == "inherits" and isinstance(value, str):
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(METHOD_SCHEMAS)
    return found


def _harvest_catalog_values() -> dict[str, dict[str, set[str]]]:
    """Return ``domain -> scope -> values`` declared in acp.catalog.

    Scopes: ``"global"`` values are declared without backend restriction and
    must resolve under every (family, implementation) context; ``"xtb"`` /
    ``"orca"`` values are declared for one backend and resolve under that
    backend's implementations.
    """
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    # METHOD_META: per-method dispersion tuples (global declarations).
    for meta in METHOD_META.values():
        for value in meta.get("dispersion") or ():
            out["dispersion"]["global"].add(str(value))

    # METHOD_SCHEMAS-referenced fields + every catalog enum field.
    field_names = (_method_schema_referenced_fields() | set(_FIELD_TO_DOMAIN)) & set(
        _FIELD_TO_DOMAIN
    )
    for name in sorted(field_names):
        domain = _FIELD_TO_DOMAIN[name]
        field_def = FIELD_DEFINITIONS.get(name) or {}
        for value in field_def.get("options") or ():
            out[domain]["global"].add(str(value))
        for backend, values in (field_def.get("per_backend") or {}).items():
            if isinstance(values, list):
                for value in values:
                    out[domain][str(backend)].add(str(value))

    # METHOD_SCHEMAS may also carry inline option lists (future-proof).
    def walk_options(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            if key in _FIELD_TO_DOMAIN and isinstance(node.get("options"), list):
                for value in node["options"]:
                    out[_FIELD_TO_DOMAIN[key]]["global"].add(str(value))
            for child_key, value in node.items():
                walk_options(value, child_key)
        elif isinstance(node, list):
            for item in node:
                walk_options(item, key)

    walk_options(METHOD_SCHEMAS)
    return out


# ─────────────────────────────────────────────────────────────────────────
# method_family
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,family",
    [
        ("GFN2-xTB", "gfn"),
        ("GFN1-xTB", "gfn"),
        ("GFN0-xTB", "gfn"),
        ("gfn2-xtb", "gfn"),
        ("  GFN2 - XTB  ", "gfn"),  # case/whitespace-insensitive
        ("GFN-FF", "gfnff"),
        ("gfn-ff", "gfnff"),
        ("  GFN-FF ", "gfnff"),
        ("B97-3c", "composite_3c"),
        ("r2SCAN-3c", "composite_3c"),
        ("PBEh-3c", "composite_3c"),
        ("wB97X-D4", "conventional_dft"),
        ("PBE0", "conventional_dft"),
        ("B3LYP", "conventional_dft"),
        ("mPW1PW", "conventional_dft"),
        ("mPW1PW91", "conventional_dft"),
        ("Native-GFN2-xTB", "gfn"),  # native is an implementation, not a family
        ("Native-GFN-FF", "gfnff"),
        ("BogusMethod", "unknown"),
        ("6-31G", "unknown"),
    ],
)
def test_method_family_classification(method: str, family: str) -> None:
    assert method_family(method) == family


def test_gfnff_and_gfn0_gaps_covered() -> None:
    """T3 regression pins: GFN-FF/GFN0-xTB classify (the old heuristic missed GFN-FF)."""
    assert method_family("GFN-FF") == "gfnff"
    assert method_family("GFN0-xTB") == "gfn"
    assert method_family("GFN-FF") != method_family("GFN2-xTB")
    assert method_family("B97-3c") != "gfn"
    assert method_family("r2SCAN-3c") != "gfn"


# ─────────────────────────────────────────────────────────────────────────
# resolve_implementation
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "method,engine,implementation",
    [
        ("GFN2-xTB", "orca", IMPL_ORCA_EXTERNAL_XTB),
        ("GFN2-xTB", "xtb", IMPL_XTB_BINARY),
        ("GFN1-xTB", "orca", IMPL_ORCA_EXTERNAL_XTB),
        ("GFN1-xTB", "xtb", IMPL_XTB_BINARY),
        ("GFN0-xTB", "orca", IMPL_ORCA_EXTERNAL_XTB),
        ("GFN0-xTB", "xtb", IMPL_XTB_BINARY),
        ("GFN-FF", "orca", IMPL_ORCA_EXTERNAL_XTB),
        ("GFN-FF", "xtb", IMPL_XTB_BINARY),
        ("Native-GFN2-xTB", "orca", IMPL_ORCA_NATIVE),
        ("Native-GFN1-xTB", "orca", IMPL_ORCA_NATIVE),
        ("Native-GFN0-xTB", "orca", IMPL_ORCA_NATIVE),
        ("Native-GFN-FF", "orca", IMPL_ORCA_NATIVE),
        ("wB97X-D4", "orca", IMPL_ORCA_DFT),
        ("r2SCAN-3c", "orca", IMPL_ORCA_DFT),
        ("B3LYP", "orca", IMPL_ORCA_DFT),
        # engine spelling is case/whitespace-insensitive too
        (" gfn2-xtb ", " ORCA ", IMPL_ORCA_EXTERNAL_XTB),
    ],
)
def test_resolve_implementation_matrix(method: str, engine: str, implementation: str) -> None:
    assert resolve_implementation(method, engine=engine) == implementation


@pytest.mark.parametrize(
    "method,engine",
    [
        ("Native-GFN2-xTB", "xtb"),  # Native-* is ORCA-native only
        ("Native-GFN-FF", "xtb"),
        ("wB97X-D4", "xtb"),  # DFT is ORCA-only
        ("B97-3c", "xtb"),
        ("NoSuchMethod", "orca"),  # family unknown
        ("GFN2-xTB", "gaussian"),  # unknown engine
        ("", "orca"),
    ],
)
def test_resolve_implementation_invalid_pair_names_method_engine_and_legal_list(
    method: str, engine: str
) -> None:
    with pytest.raises(KeywordValueError) as exc:
        resolve_implementation(method, engine=engine)
    message = str(exc.value)
    assert repr(method) in message
    assert repr(engine) in message
    for implementation in sorted(IMPLEMENTATIONS):
        assert implementation in message


# ─────────────────────────────────────────────────────────────────────────
# resolve(): superseded map semantics (orca.py / orca_ts.py)
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "domain,value,expected",
    [
        # orca.py _OPT_LEVEL_MAP + orca_ts.py _OPT_LEVEL_KEYWORDS
        ("opt_level", "loose", "LooseOpt"),
        ("opt_level", "normal", None),
        ("opt_level", "tight", "TightOpt"),
        ("opt_level", "verytight", "VeryTightOpt"),
        ("opt_level", "very_tight", "VeryTightOpt"),
        # orca.py _SCF_CONVERGENCE_MAP (incl. the normal no-op)
        ("scf_convergence", "loose", "LooseSCF"),
        ("scf_convergence", "normal", None),
        ("scf_convergence", "tight", "TightSCF"),
        ("scf_convergence", "verytight", "VeryTightSCF"),
        # orca.py _SCF_STRATEGY_MAP
        ("scf_strategy", "normal", None),
        ("scf_strategy", "slowconv", "SlowConv"),
        ("scf_strategy", "soscf", "SOSCF"),
        # orca.py _GRID_KEYWORD_MAP
        ("grid", "defgrid1", "DefGrid1"),
        ("grid", "defgrid2", "DefGrid2"),
        ("grid", "defgrid3", "DefGrid3"),
        # orca.py _DISPERSION_KEYWORD_MAP
        ("dispersion", "none", None),
        ("dispersion", "d3", "D3"),
        ("dispersion", "d3bj", "D3BJ"),
        ("dispersion", "d4", "D4"),
        ("dispersion", "vv10", "VV10"),
    ],
)
def test_superseded_map_semantics_preserved(domain: str, value: str, expected: str | None) -> None:
    """Exact token semantics of the maps T4 will delete."""
    canonical, warning = resolve(domain, value, family="composite_3c", implementation=IMPL_ORCA_DFT)
    assert canonical == expected
    assert warning is None


def test_normal_is_an_explicit_noop_everywhere_legal() -> None:
    for domain in ("opt_level", "scf_convergence", "scf_strategy"):
        for family, implementation in _ALL_CONTEXTS:
            assert resolve(domain, "normal", family=family, implementation=implementation) == (
                None,
                None,
            )
    assert resolve(
        "dispersion", "none", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ) == (None, None)


def test_opt_level_engine_native_spellings() -> None:
    """opt_level has real per-engine spellings (xtb ``--opt <level>``)."""
    xtb = {"family": "gfn", "implementation": IMPL_XTB_BINARY}
    assert resolve("opt_level", "crude", **xtb) == ("crude", None)
    assert resolve("opt_level", "tight", **xtb) == ("tight", None)
    assert resolve("opt_level", "verytight", **xtb) == ("verytight", None)
    assert resolve("opt_level", "very_tight", **xtb) == ("verytight", None)
    assert resolve("opt_level", "normal", **xtb) == (None, None)
    # 'loose' has no xTB level (catalog v1.4) -> warned no-op, never coerced.
    canonical, warning = resolve("opt_level", "loose", **xtb)
    assert canonical is None
    assert warning is not None and "no keyword equivalent" in warning
    # 'crude' has no ORCA keyword -> warned no-op on the ORCA path.
    canonical, warning = resolve(
        "opt_level", "crude", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    )
    assert canonical is None
    assert warning is not None and "no keyword equivalent" in warning


# ─────────────────────────────────────────────────────────────────────────
# resolve(): applicability (GFN) + solvent policy
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("family", ["gfn", "gfnff"])
@pytest.mark.parametrize("implementation", sorted(IMPLEMENTATIONS))
def test_gfn_forbids_basis_dispersion_grid_ri_aux(family: str, implementation: str) -> None:
    for field, value in (
        ("basis", "def2-SVP"),
        ("dispersion", "D4"),
        ("grid", "DefGrid3"),
        ("ri", "RIJCOSX"),
        ("aux", "AutoAux"),
    ):
        assert not is_applicable(field, family=family, implementation=implementation), field
        canonical, warning = resolve(field, value, family=family, implementation=implementation)
        assert canonical is None, field
        assert warning is not None and "never emitted" in warning, field
    # solvent handling is governed by the policy layer, not stripped
    assert is_applicable("solvent_model", family=family, implementation=implementation)


def test_gfn_orca_solvent_policy_none_alpb_only() -> None:
    """PLATFORM POLICY (not software capability): ORCA GFN solvent {none, ALPB}."""
    for implementation in (IMPL_ORCA_EXTERNAL_XTB, IMPL_ORCA_NATIVE):
        assert resolve("solvent_model", "ALPB", family="gfn", implementation=implementation) == (
            "ALPB",
            None,
        )
        assert resolve("solvent_model", "none", family="gfn", implementation=implementation) == (
            "none",
            None,
        )
        for bad in ("GBSA", "gbsa", "CPCM", "SMD"):
            with pytest.raises(KeywordValueError) as exc:
                resolve("solvent_model", bad, family="gfn", implementation=implementation)
            message = str(exc.value)
            assert "PLATFORM POLICY" in message
            assert repr(bad) in message
            assert "ALPB" in message
    # the standalone xTB binary allows GBSA (capability + policy)
    assert resolve("solvent_model", "GBSA", family="gfn", implementation=IMPL_XTB_BINARY) == (
        "GBSA",
        None,
    )
    assert resolve("solvent_model", "ALPB", family="gfn", implementation=IMPL_XTB_BINARY) == (
        "ALPB",
        None,
    )
    with pytest.raises(KeywordValueError):
        resolve("solvent_model", "CPCM", family="gfn", implementation=IMPL_XTB_BINARY)


# ─────────────────────────────────────────────────────────────────────────
# resolve(): free-form passthrough, casing, aliases, fail-fast
# ─────────────────────────────────────────────────────────────────────────


def test_free_form_values_pass_through_case_preserved() -> None:
    assert resolve("basis", "def2-TZVP", family="composite_3c", implementation=IMPL_ORCA_DFT) == (
        "def2-TZVP",
        None,
    )
    assert resolve("solvent", "DMSO", family="conventional_dft", implementation=IMPL_ORCA_DFT) == (
        "DMSO",
        None,
    )
    assert resolve(
        "solvent_model", "DMSO", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ) == ("DMSO", None)
    # free text is NEVER case-folded (AGENTS anti-pattern #33)
    assert resolve(
        "basis", "def2-tzvp", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ) == ("def2-tzvp", None)
    assert resolve("solvent", "dmso", family="conventional_dft", implementation=IMPL_ORCA_DFT) == (
        "dmso",
        None,
    )


def test_enum_lookup_is_case_insensitive() -> None:
    assert resolve("grid", "defgrid3", family="conventional_dft", implementation=IMPL_ORCA_DFT) == (
        "DefGrid3",
        None,
    )
    assert resolve("grid", "DEFGRID3", family="conventional_dft", implementation=IMPL_ORCA_DFT) == (
        "DefGrid3",
        None,
    )
    assert resolve(
        "opt_level", "very_tight", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ) == resolve("opt_level", "verytight", family="conventional_dft", implementation=IMPL_ORCA_DFT)
    assert resolve(
        "opt_level", "VERY_TIGHT", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ) == ("VeryTightOpt", None)
    assert resolve(
        "dispersion", "d3BJ", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ) == ("D3BJ", None)


@pytest.mark.parametrize(
    "value,canonical",
    [
        ("SG1", "DefGrid1"),
        ("Fine", "DefGrid2"),
        ("UltraFine", "DefGrid3"),
        ("SuperFine", "DefGrid3"),
    ],
)
def test_legacy_grid_aliases_map_with_migration_warning(value: str, canonical: str) -> None:
    token, warning = resolve("grid", value, family="conventional_dft", implementation=IMPL_ORCA_DFT)
    assert token == canonical
    assert warning is not None and "legacy alias" in warning
    assert value != token  # the legacy token is never emitted


def test_unknown_enum_raises_with_domain_value_and_legal_values() -> None:
    with pytest.raises(KeywordValueError) as exc:
        resolve("opt_level", "ultrafast", family="conventional_dft", implementation=IMPL_ORCA_DFT)
    message = str(exc.value)
    assert "opt_level" in message
    assert "ultrafast" in message
    assert "verytight" in message  # legal input spellings
    assert "VeryTightOpt" in message  # canonical tokens
    with pytest.raises(KeywordValueError) as exc:
        resolve("grid", "Grid5", family="conventional_dft", implementation=IMPL_ORCA_DFT)
    assert "grid" in str(exc.value)
    with pytest.raises(KeywordValueError) as exc:
        resolve("bogus_domain", "x", family="conventional_dft", implementation=IMPL_ORCA_DFT)
    assert "bogus_domain" in str(exc.value)


def test_unknown_enum_raises_even_when_field_would_be_stripped() -> None:
    """Fail-fast validation runs before applicability stripping (no hiding)."""
    with pytest.raises(KeywordValueError):
        resolve("grid", "Grid5", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB)


def test_unset_values_are_noops() -> None:
    assert resolve("basis", None, family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB) == (
        None,
        None,
    )
    assert resolve("basis", "", family="conventional_dft", implementation=IMPL_ORCA_DFT) == (
        None,
        None,
    )


# ─────────────────────────────────────────────────────────────────────────
# Catalog-parity sweep: EVERY catalog-declared enum value resolves
# ─────────────────────────────────────────────────────────────────────────


def test_every_catalog_declared_enum_value_resolves() -> None:
    harvested = _harvest_catalog_values()
    # Guard against a silent harvest regression (values keep catalog spelling).
    assert {"Loose", "Normal", "Tight", "VeryTight"} <= harvested["opt_level"]["global"]
    assert "crude" in harvested["opt_level"]["xtb"]
    assert "normal" in harvested["scf_strategy"]["global"]
    assert {"SG1", "Fine", "UltraFine", "SuperFine"} <= harvested["grid"]["global"]
    assert {"none", "D3", "D3BJ", "D4", "VV10"} <= harvested["dispersion"]["global"]

    for domain, scopes in harvested.items():
        assert domain in ENUM_DOMAINS, domain
        for scope, values in scopes.items():
            if scope == "global":
                contexts = _ALL_CONTEXTS
            elif scope == "xtb":
                contexts = [("gfn", IMPL_XTB_BINARY)]
            else:
                contexts = [("conventional_dft", IMPL_ORCA_DFT)]
            for value in sorted(values):
                for family, implementation in contexts:
                    canonical, warning = resolve(
                        domain, value, family=family, implementation=implementation
                    )
                    key = value.strip().lower()
                    if canonical is None:
                        # no-op where applicable: engine default (normal/none),
                        # a stripped field, or a value with no token on this
                        # implementation — all of which must warn unless they
                        # are true no-ops.
                        assert key in {"normal", "none"} or warning is not None, (
                            domain,
                            value,
                            family,
                            implementation,
                        )


def test_legal_values_helper_matches_registry_tables() -> None:
    assert "very_tight" in legal_values("opt_level")  # accepted alias spelling
    assert "defgrid3" in legal_values("grid")
    assert legal_values("basis") == ()  # free-form domains have no enum set
    with pytest.raises(KeywordValueError):
        legal_values("not_a_domain")


def test_canonical_token_probe() -> None:
    assert canonical_token("grid", "defgrid3", implementation=IMPL_ORCA_DFT) == (
        "DefGrid3",
        False,
    )
    assert canonical_token("opt_level", "normal", implementation=IMPL_ORCA_DFT) == (
        None,
        True,
    )
    assert canonical_token("opt_level", "crude", implementation=IMPL_XTB_BINARY) == (
        "crude",
        False,
    )
    with pytest.raises(KeywordValueError):
        canonical_token("grid", "nope", implementation=IMPL_ORCA_DFT)
    with pytest.raises(KeywordValueError):
        canonical_token("basis", "def2-SVP", implementation=IMPL_ORCA_DFT)


def test_applicability_table_is_complete_and_queryable() -> None:
    assert set(APPLICABILITY_TABLE) == {
        (family, implementation) for family in FAMILIES for implementation in IMPLEMENTATIONS
    }
    assert APPLICABLE_FIELDS == ENUM_DOMAINS | FREE_FORM_DOMAINS
    with pytest.raises(KeywordValueError):
        is_applicable("bogus_field", family="gfn", implementation=IMPL_XTB_BINARY)
    with pytest.raises(KeywordValueError):
        is_applicable("basis", family="nope", implementation=IMPL_XTB_BINARY)


# ─────────────────────────────────────────────────────────────────────────
# Policy layer (method-level + calculation-level)
# ─────────────────────────────────────────────────────────────────────────


def test_gfn0_orca_path_policy_rejected_with_three_level_record() -> None:
    decision = method_policy("GFN0-xTB", engine="orca")
    assert not decision.allowed
    # capability vs dependency vs policy are three DISTINCT records
    assert "external-interface table lists GFN0-xTB" in decision.capability
    assert "XTBPATH" in decision.capability
    assert "param_gfn0-xtb.txt" in decision.dependency
    assert "dependency" in decision.dependency.lower()
    assert "xTB-binary-only" in decision.policy
    assert "PLATFORM POLICY" in decision.policy
    assert "xtb" in decision.reason.lower()
    assert len({decision.capability, decision.dependency, decision.policy}) == 3
    # GFN0-xTB IS the xTB-binary path — allowed there
    assert method_policy("GFN0-xTB", engine="xtb").allowed
    # everything else on the registry table is allowed by default
    assert method_policy("GFN2-xTB", engine="orca").allowed
    assert method_policy("GFN-FF", engine="orca").allowed
    assert method_policy("Native-GFN2-xTB", engine="orca").allowed
    assert method_policy("r2SCAN-3c", engine="orca").allowed


def test_gfn_nmr_default_reject_policy_present() -> None:
    assert GFN_NMR_DEFAULT_ALLOWED is False  # T8 switch stays CLOSED
    for family in ("gfn", "gfnff"):
        decision = calculation_policy("nmr", family=family, implementation=IMPL_ORCA_EXTERNAL_XTB)
        assert not decision.allowed
        assert "artifact" in decision.capability.lower()
        assert "PLATFORM POLICY" in decision.policy
    # DFT NMR is untouched
    assert calculation_policy(
        "nmr", family="conventional_dft", implementation=IMPL_ORCA_DFT
    ).allowed
    # non-NMR GFN calculations are unrestricted
    assert calculation_policy("sp", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB).allowed


# ─────────────────────────────────────────────────────────────────────────
# T22 probe fixture consistency (verdicts map to registry decisions)
# ─────────────────────────────────────────────────────────────────────────


def _load_probe() -> dict[str, Any]:
    return json.loads(_PROBE_FIXTURE.read_text(encoding="utf-8"))


def test_t22_probe_fixture_schema_and_totals() -> None:
    data = _load_probe()
    assert data["schema"] == "cccp_orca_keyword_probe_v1"
    cases = data["cases"]
    assert len(cases) == 13
    allowed_classes = {
        "valid_completion",
        "syntax_error",
        "missing_dependency",
        "calculation_failure",
    }
    counts = Counter(case["outcome_class"] for case in cases)
    assert set(counts) <= allowed_classes
    for case in cases:  # snake_case outcome classes
        assert case["outcome_class"] == case["outcome_class"].lower().replace(" ", "_")
    totals = data["totals"]
    assert totals["cases"] == 13
    for outcome_class in sorted(allowed_classes):
        assert totals[outcome_class] == counts[outcome_class]
    assert counts["valid_completion"] == 9
    assert counts["syntax_error"] == 2
    assert counts["missing_dependency"] == 1
    assert counts["calculation_failure"] == 1


def _check_gfn2_xtb(case: dict[str, Any]) -> None:
    assert method_family("GFN2-xTB") == "gfn"
    assert resolve_implementation("GFN2-xTB", engine="orca") == IMPL_ORCA_EXTERNAL_XTB
    assert method_policy("GFN2-xTB", engine="orca").allowed


def _check_native_gfn2_xtb(case: dict[str, Any]) -> None:
    assert resolve_implementation("Native-GFN2-xTB", engine="orca") == IMPL_ORCA_NATIVE


def _check_gfn2_xtb_alpb_water(case: dict[str, Any]) -> None:
    canonical, warning = resolve(
        "solvent_model", "ALPB", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    )
    assert canonical == "ALPB"
    assert warning is None
    assert resolve("solvent", "water", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB) == (
        "water",
        None,
    )


def _check_gfn2_xtb_gbsa_water(case: dict[str, Any]) -> None:
    # syntax_error under ORCA == GBSA rejected by the ORCA gfn policy
    with pytest.raises(KeywordValueError) as exc:
        resolve("solvent_model", "GBSA", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB)
    assert "PLATFORM POLICY" in str(exc.value)


def _check_gfn2_xtb_def2_svp(case: dict[str, Any]) -> None:
    # accepted-but-ignored by ORCA == stripped for GFN, never emitted
    canonical, warning = resolve(
        "basis", "def2-SVP", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    )
    assert canonical is None
    assert warning is not None and "never emitted" in warning


def _check_gfn2_xtb_d4(case: dict[str, Any]) -> None:
    canonical, warning = resolve(
        "dispersion", "D4", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    )
    assert canonical is None
    assert warning is not None and "never emitted" in warning


def _check_gfn2_xtb_defgrid3(case: dict[str, Any]) -> None:
    canonical, warning = resolve(
        "grid", "DefGrid3", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    )
    assert canonical is None
    assert warning is not None and "never emitted" in warning


def _check_gfn2_xtb_tightscf(case: dict[str, Any]) -> None:
    # TightSCF is EFFECTIVE (forwarded to otool_xtb) — must NOT be stripped
    assert resolve(
        "scf_convergence", "Tight", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    ) == ("TightSCF", None)


def _check_gfn_ff(case: dict[str, Any]) -> None:
    assert method_family("GFN-FF") == "gfnff"
    assert resolve_implementation("GFN-FF", engine="orca") == IMPL_ORCA_EXTERNAL_XTB
    assert method_policy("GFN-FF", engine="orca").allowed


def _check_gfn0_xtb(case: dict[str, Any]) -> None:
    # missing_dependency == policy reject on ORCA + capability/dependency split
    decision = method_policy("GFN0-xTB", engine="orca")
    assert not decision.allowed
    assert "param_gfn0-xtb.txt" in decision.dependency
    assert "external-interface table lists GFN0-xTB" in decision.capability
    assert "xTB-binary-only" in decision.policy
    assert method_policy("GFN0-xTB", engine="xtb").allowed


def _check_gfn2_xtb_nmr(case: dict[str, Any]) -> None:
    # calculation_failure == GFN+NMR default reject policy present
    assert GFN_NMR_DEFAULT_ALLOWED is False
    assert not calculation_policy(
        "nmr", family="gfn", implementation=IMPL_ORCA_EXTERNAL_XTB
    ).allowed


def _check_defgrid3(case: dict[str, Any]) -> None:
    # DEFGRID3 accepted == canonical resolves case-insensitively
    assert resolve("grid", "DEFGRID3", family="conventional_dft", implementation=IMPL_ORCA_DFT) == (
        "DefGrid3",
        None,
    )


def _check_ultrafine(case: dict[str, Any]) -> None:
    # syntax_error == the legacy alias must map to DefGrid3 pre-assembly
    canonical, warning = resolve(
        "grid", "UltraFine", family="conventional_dft", implementation=IMPL_ORCA_DFT
    )
    assert canonical == "DefGrid3"
    assert warning is not None and "legacy alias" in warning


# case_id -> (expected outcome class, registry-decision checker)
_PROBE_CHECKS: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
    "gfn2-xtb": ("valid_completion", _check_gfn2_xtb),
    "native-gfn2-xtb": ("valid_completion", _check_native_gfn2_xtb),
    "gfn2-xtb-alpb-water": ("valid_completion", _check_gfn2_xtb_alpb_water),
    "gfn2-xtb-gbsa-water": ("syntax_error", _check_gfn2_xtb_gbsa_water),
    "gfn2-xtb-def2-svp": ("valid_completion", _check_gfn2_xtb_def2_svp),
    "gfn2-xtb-d4": ("valid_completion", _check_gfn2_xtb_d4),
    "gfn2-xtb-defgrid3": ("valid_completion", _check_gfn2_xtb_defgrid3),
    "gfn2-xtb-tightscf": ("valid_completion", _check_gfn2_xtb_tightscf),
    "gfn-ff": ("valid_completion", _check_gfn_ff),
    "gfn0-xtb": ("missing_dependency", _check_gfn0_xtb),
    "gfn2-xtb-nmr": ("calculation_failure", _check_gfn2_xtb_nmr),
    "defgrid3": ("valid_completion", _check_defgrid3),
    "ultrafine": ("syntax_error", _check_ultrafine),
}


def test_t22_every_probe_verdict_maps_to_a_registry_decision() -> None:
    cases = _load_probe()["cases"]
    by_id = {case["case_id"]: case for case in cases}
    assert set(by_id) == set(_PROBE_CHECKS), "probe/registry case coverage drifted"
    for case_id, (expected_outcome, check) in _PROBE_CHECKS.items():
        case = by_id[case_id]
        assert case["outcome_class"] == expected_outcome, case_id
        check(case)
