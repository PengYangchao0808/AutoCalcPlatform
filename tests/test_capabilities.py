"""
Submission-time capability derivation + matching tests (plan W2-T3).

Covers the acceptance criteria of todo 3 in
``.omo/plans/node-selection-at-submission.md`` (design
``.omo/drafts/node-selection-design.md`` §1.3/§2.1, decisions D8/D13/D14):

1. Every ``WORKFLOW_SOFTWARE_REQUIREMENTS`` row (including the Confsearch
   protocol/policy and BatchOptimize profile sub-tables) derives the exact
   frozenset, with each test docstring citing the file:line code evidence
   that was re-verified against the sources (not just the seed rows).
2. The method-schema ``engine`` mapping reads the real catalog field name
   (``profiles[].levels.<level_id>.engine`` and
   ``method_levels[].allowed_engines`` in ``catalog.py``); unknown engines
   are ignored with a warning.
3. ``matches_capabilities`` implements the D8 three-state model and the
   D13 tags-never-satisfied-on-undeclared-nodes rule.
4. ``local_satisfies`` resolves through ``cccp.software.resolve_executable``
   seeded with the configured ``executables.<name>.path`` (all monkeypatched
   — deterministic regardless of the host's QC installs).
5. ``is_degraded`` is the single load/disk predicate.
6. Error classes carry machine-readable ``code`` / ``missing_*`` fields.
7. ``JobSpec.node_tags`` round-trips through the store, and old
   ``spec_json`` rows without the key deserialize to ``[]``.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.scheduler.capabilities import (
    ENGINE_TO_SOFTWARE,
    NoCapableNodeError,
    derive_required_software,
    is_degraded,
    local_satisfies,
    matches_capabilities,
)
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.nodes import ExecutionTargetError
from acp.scheduler.remote.config import NodeCapabilities
from acp.scheduler.store import JobStore

_CAP_LOGGER = "acp.scheduler.capabilities"


def _spec(workflow: str, **method: object) -> JobSpec:
    """Build a minimal JobSpec with a method dict from kwargs."""
    return JobSpec(workflow=workflow, method=dict(method))


# ---------------------------------------------------------------------- #
# 1a. Confsearch — protocol/policy fixed rows
# ---------------------------------------------------------------------- #


def test_confsearch_xtb_crest_row() -> None:
    """Confsearch xtb-crest → {xtb, crest} for ANY refinement policy.

    Evidence: confsearch/protocols/xtb_crest.py:1-5 declares the protocol
    "Pure xTB — no CENSO, no ORCA" and calls run_ensemble_generation with
    preset="censo-zero" (xtb_crest.py:24-27); ensemble.py:335-347 shows
    censo-zero bypasses CENSO entirely (xTB passthrough); ensemble.py:303
    builds the CREST backend; cccp/qc/interfaces/crest.py:56-57 resolves
    ``executables.xtb.path`` (CREST runs on the xTB binary). Non-screen
    policies are coerced to "screen" for pure-xTB protocols
    (confsearch/contracts.py:154-160), so no policy delta exists.
    """
    assert derive_required_software(_spec("Confsearch", protocol="xtb-crest")) == frozenset(
        {"xtb", "crest"}
    )
    # Pure-xTB protocols coerce non-screen policies to screen at runtime
    # (contracts.py:154-160) — the derivation must mirror that coercion
    # instead of demanding Shermo for a DFT stage that never runs.
    assert derive_required_software(
        _spec("Confsearch", protocol="xtb-crest", refinement_policy="rank1")
    ) == frozenset({"xtb", "crest"})


def test_confsearch_xtb_md_row() -> None:
    """Confsearch xtb-md → {xtb, molclus, isostat}.

    Evidence: confsearch/protocols/xtb_md.py:1-6 ("Pure xTB — no CENSO, no
    ORCA"); MD sampling via get_backend("molclus") (workflows/xtbmd_md.py:127);
    GFN1 batch opt via get_backend("xtb") (xtb_md.py:37 imports
    _batch_opt_frames → xtbmd_censo_energy.py:703); ISOSTAT dedup via
    get_backend("isostat") (xtb_md.py:122). Policy coercion as above.
    """
    assert derive_required_software(_spec("Confsearch", protocol="xtb-md")) == frozenset(
        {"xtb", "molclus", "isostat"}
    )
    assert derive_required_software(
        _spec("Confsearch", protocol="xtb-md", refinement_policy="cumulative-99")
    ) == frozenset({"xtb", "molclus", "isostat"})


def test_confsearch_censo_crest_screen_row() -> None:
    """Confsearch censo-crest + screen → {xtb, crest, censo, orca}.

    Evidence: screen routes to run_ensemble_generation
    (confsearch/protocols/censo_crest.py:33-37) → CREST
    (ensemble.py:303) → CensoBackend (ensemble.py:354). ORCA is required
    even at screen: the CENSO rcfile pins ``prog = orca`` for every active
    part (cccp/qc/interfaces/censo.py:295) and the censo-light preset runs
    prescreening/screening at the B97-3c DFT functional (censo.py:116-122).
    xTB backs CREST (crest.py:56-57) and is pinned in the rcfile [paths]
    (censo.py:323-324).
    """
    got = derive_required_software(_spec("Confsearch", protocol="censo-crest"))
    assert got == frozenset({"xtb", "crest", "censo", "orca"})


@pytest.mark.parametrize("policy", ["rank1", "cumulative-99", "all"])
def test_confsearch_censo_crest_dft_policies_add_shermo(policy: str) -> None:
    """censo-crest + rank1/cumulative-99/all → base ∪ {shermo}.

    Evidence: non-screen policies route to run_conformer_energy
    (confsearch/protocols/censo_crest.py:56-64) whose rank1 handoff runs
    ORCA opt→freq→SP (workflows/energy_shared.py:344/382/427/459) plus a
    Shermo correction (energy_shared.py:489 run_shermo). "all" is a real
    policy (contracts.py:42) taking the same non-screen route. ORCA is
    already in the base (censo.py:295), so the delta is Shermo only.
    """
    got = derive_required_software(
        _spec("Confsearch", protocol="censo-crest", refinement_policy=policy)
    )
    assert got == frozenset({"xtb", "crest", "censo", "orca", "shermo"})


def test_confsearch_xtbmd_censo_rows() -> None:
    """xtbmd-censo: screen → {xtb, molclus, isostat, censo, orca};
    non-screen adds {shermo}.

    Evidence: the xtb-md chain (molclus/xtb/isostat — see
    test_confsearch_xtb_md_row) plus CENSO: confsearch/protocols/
    xtbmd_censo.py:34-70 delegates to run_xtbmd_censo_energy; with
    no_opt=True (screen, xtbmd_censo.py:44) the CENSO branch still runs
    (xtbmd_censo_energy.py:1740-1743 — the censo-zero fast path at :1689
    requires opt_enabled), and CENSO needs orca+xtb (censo.py:295/323-324).
    Non-screen enables the ACP handoff (xtbmd_censo_energy.py:1759-1764)
    → orca + shermo via energy_shared.py:382/459/489.
    """
    assert derive_required_software(_spec("Confsearch", protocol="xtbmd-censo")) == frozenset(
        {"xtb", "molclus", "isostat", "censo", "orca"}
    )
    assert derive_required_software(
        _spec("Confsearch", protocol="xtbmd-censo", refinement_policy="cumulative-99")
    ) == frozenset({"xtb", "molclus", "isostat", "censo", "orca", "shermo"})


def test_confsearch_protocol_resolved_via_profile_id() -> None:
    """profile_id doubles as the protocol selector (jobs.py:405-407 parity)."""
    assert derive_required_software(_spec("Confsearch", profile_id="xtb-crest")) == frozenset(
        {"xtb", "crest"}
    )
    # An explicit protocol wins over profile_id.
    assert derive_required_software(
        _spec("Confsearch", profile_id="censo-crest", protocol="xtb-md")
    ) == frozenset({"xtb", "molclus", "isostat"})


def test_confsearch_empty_method_uses_cli_default_protocol() -> None:
    """Empty method → CLI default protocol censo-crest at screen policy.

    Evidence: cli.py:419-422 (``--protocol`` default "censo-crest"),
    cli.py:430-435 (``--refinement-policy`` default "screen"); the flag is
    only emitted when method carries it explicitly (jobs.py:412-414).
    """
    assert derive_required_software(_spec("Confsearch")) == frozenset(
        {"xtb", "crest", "censo", "orca"}
    )


def test_confsearch_quality_profile_does_not_change_software() -> None:
    """``profile`` (light/default/high resource knob) is not a protocol."""
    assert derive_required_software(
        _spec("Confsearch", protocol="censo-crest", profile="light")
    ) == frozenset({"xtb", "crest", "censo", "orca"})


def test_confsearch_unknown_protocol_warns_and_derives_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unknown protocol → warning + empty set (generic fallback, D14)."""
    with caplog.at_level(logging.WARNING, logger=_CAP_LOGGER):
        got = derive_required_software(_spec("Confsearch", protocol="nope"))
    assert got == frozenset()
    assert any("nope" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------- #
# 1b. BatchOptimize — profile rows
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("opt_only", frozenset({"orca"})),
        ("opt_freq", frozenset({"orca"})),
        ("opt_freq_sp", frozenset({"orca"})),
        ("opt_freq_sp_thermo", frozenset({"orca", "shermo"})),
    ],
)
def test_batchoptimize_profile_rows(profile: str, expected: frozenset[str]) -> None:
    """BatchOptimize profiles → {orca}, +shermo for opt_freq_sp_thermo.

    Evidence: opt/freq/SP steps default to the ORCA backend
    (calculations/primitives/_common.py:238-240 ``backend_name`` default
    "orca"; calculations/batch/engine.py:675 hardcodes backend="orca" for
    electronic-state validation). Only opt_freq_sp_thermo adds the
    THERMOCHEMISTRY step (calculations/batch/profiles.py:13-22), which
    runs Shermo (batch/engine.py:967 → primitives/thermochemistry.py:10
    import + :73 run_shermo call). The batch_optimize schema profiles
    carry no per-level engines (catalog.py:2670-2683).
    """
    assert derive_required_software(_spec("BatchOptimize", profile=profile)) == expected


def test_batchoptimize_profile_id_key_and_cli_default() -> None:
    """Profile resolution mirrors batchoptimize_method_flags (jobs.py:464):
    ``method.profile`` then ``method.profile_id``; CLI default opt_freq
    (cli.py:627-632)."""
    assert derive_required_software(
        _spec("BatchOptimize", profile_id="opt_freq_sp_thermo")
    ) == frozenset({"orca", "shermo"})
    assert derive_required_software(_spec("BatchOptimize")) == frozenset({"orca"})


def test_batchoptimize_unknown_profile_warns_and_derives_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_CAP_LOGGER):
        got = derive_required_software(_spec("BatchOptimize", profile="mega"))
    assert got == frozenset()
    assert any("mega" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------- #
# 1c. nmr — fixed row
# ---------------------------------------------------------------------- #


def test_nmr_row() -> None:
    """nmr → {xtb, crest, censo, orca}.

    Evidence: workflows/nmr.py:6-16 — conformer generation reuses
    run_ensemble_generation with the censo-light preset (CREST at
    ensemble.py:303 + CensoBackend at ensemble.py:354), then per-conformer
    GIAO shieldings via get_backend("orca") (nmr.py:437-444). CENSO pins
    ``prog = orca`` (cccp censo.py:295) and both orca+xtb paths in the
    rcfile [paths] (censo.py:323-324); CREST resolves executables.xtb.path
    (crest.py:56-57). The method-schema union (nmr schema single profile
    engines {censo, orca}, catalog.py:2599-2627) is a subset of the fixed
    row, so the result is stable regardless of schema contribution.
    """
    for spec in (
        _spec("nmr"),
        _spec("nmr", schema_id="nmr"),
        _spec("nmr", profile_id="nmr-goodman"),
    ):
        assert derive_required_software(spec) == frozenset({"xtb", "crest", "censo", "orca"})


# ---------------------------------------------------------------------- #
# 2. Method-schema engine mapping
# ---------------------------------------------------------------------- #


def test_engine_field_name_is_the_catalog_reality() -> None:
    """Drift lock: METHOD_SCHEMAS levels carry an ``engine`` field.

    catalog.py:1824-1832 — dft_singlepoint default profile's single_point
    level carries ``"engine": "orca"``. If the catalog ever renames the
    field, this fails before the derivation silently degrades to the
    allowed_engines fallback.
    """
    from acp.catalog import METHOD_SCHEMAS

    level = METHOD_SCHEMAS["dft_singlepoint"]["profiles"][0]["levels"]["single_point"]
    assert level["engine"] == "orca"
    assert "orca" in ENGINE_TO_SOFTWARE


@pytest.mark.parametrize(
    ("workflow", "expected"),
    [
        # catalog.py:1799-1835 — dft_singlepoint, single default profile, engine orca
        ("singlepoint", frozenset({"orca"})),
        # catalog.py:1837-1882 — dft_optimize
        ("optimize", frozenset({"orca"})),
        # catalog.py:1883-1905 — dft_frequency: profiles=[], allowed_engines ["orca"]
        ("frequency", frozenset({"orca"})),
        # catalog.py:1906-1981 — dft_scan default profile, engine orca
        ("scan", frozenset({"orca"})),
        # catalog.py:1982-2011 — irc default profile, engine orca
        ("irc", frozenset({"orca"})),
        # catalog.py:2131-2143 — xtb_optimize: profiles=[], allowed_engines ["xtb"]
        ("xtb_optimize", frozenset({"xtb"})),
    ],
)
def test_simple_workflow_rows(workflow: str, expected: frozenset[str]) -> None:
    """Simple/irc workflows derive purely from the method-schema engines.

    Each schema id resolves via the workflow catalog's method_schema_id
    (catalog.py:17/29/41/53/65/126); profile-less schemas (frequency,
    xtb_optimize) fall back to the union of method_levels allowed_engines.
    """
    assert derive_required_software(_spec(workflow)) == expected


def test_pessearch_row() -> None:
    """PESsearch → {orca, xtb} via the pes_scan default profile.

    Evidence: catalog.py:2378-2523 — the single "default" profile carries
    scan_coordinate/scan_driver/single_point engines "orca" (:2468/2476/
    2493) and scan_optimizer engine "xtb" (:2485).
    """
    assert derive_required_software(_spec("PESsearch")) == frozenset({"orca", "xtb"})


def test_explicit_levels_engines_win() -> None:
    """Explicit method.levels engines take precedence over profile defaults
    (mirrors runner.py:1242-1246 converting levels to CLI flags)."""
    got = derive_required_software(
        _spec("singlepoint", levels={"single_point": {"engine": "xtb", "gfn": "GFN2-xTB"}})
    )
    assert got == frozenset({"xtb"})


def test_explicit_schema_id_wins_over_catalog_default() -> None:
    """An explicit method.schema_id overrides the workflow's catalog default."""
    assert derive_required_software(_spec("optimize", schema_id="xtb_optimize")) == frozenset(
        {"xtb"}
    )


def test_unknown_engine_ignored_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown engine values are ignored with a warning (design §2.1)."""
    with caplog.at_level(logging.WARNING, logger=_CAP_LOGGER):
        got = derive_required_software(
            _spec("singlepoint", levels={"single_point": {"engine": "psi4"}})
        )
    assert got == frozenset()
    assert any("psi4" in r.getMessage() for r in caplog.records)


def test_unknown_profile_in_profiled_schema_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A profile id missing from a schema that HAS profiles warns and falls
    back (dft_singlepoint has exactly one profile, catalog.py:1819-1835)."""
    with caplog.at_level(logging.WARNING, logger=_CAP_LOGGER):
        got = derive_required_software(_spec("singlepoint", profile_id="nonexistent"))
    assert got == frozenset({"orca"})  # allowed_engines fallback
    assert any("nonexistent" in r.getMessage() for r in caplog.records)


def test_profileless_schema_with_profile_id_no_spurious_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """dft_frequency has NO profiles (catalog.py:1904) — a stray profile_id
    must not log a bogus "profile not found" warning.

    Profile-less schemas legitimately reach the allowed_engines fallback;
    warning there would be noise for every wizard-submitted job that
    carries a default profile_id.
    """
    with caplog.at_level(logging.WARNING, logger=_CAP_LOGGER):
        got = derive_required_software(_spec("frequency", profile_id="default"))
    assert got == frozenset({"orca"})
    assert not [r for r in caplog.records if "not found in schema" in r.getMessage()]


def test_unknown_workflow_derives_empty() -> None:
    """Unknown workflow → empty set (generic auto-selection fallback, D14)."""
    assert derive_required_software(_spec("DoesNotExist")) == frozenset()


# ---------------------------------------------------------------------- #
# 3. matches_capabilities — D8 three states + D13 tags rule
# ---------------------------------------------------------------------- #


def test_match_declared_satisfies() -> None:
    """Declared node: declaration authoritative for software AND tags;
    probe results are display-only."""
    declared = NodeCapabilities(software=("orca", "xtb", "crest"), tags=("gpu", "fast"))
    result = matches_capabilities(["orca", "xtb"], ["gpu"], declared=declared, probed_software=())
    assert result.satisfies is True
    assert result.missing_software == ()
    assert result.missing_tags == ()
    assert result.reasons == ()


def test_match_declared_missing_software() -> None:
    declared = NodeCapabilities(software=("orca",), tags=())
    result = matches_capabilities(
        ["orca", "censo", "crest"], [], declared=declared, probed_software=("orca", "censo")
    )
    assert result.satisfies is False
    assert result.missing_software == ("censo", "crest")  # sorted
    assert result.missing_tags == ()
    assert len(result.reasons) == 1
    assert "censo" in result.reasons[0] and "crest" in result.reasons[0]
    # Declaration wins even when the probe found the binary (D8).
    assert "censo" not in result.missing_software.__class__("")


def test_match_declared_missing_tags() -> None:
    declared = NodeCapabilities(software=("orca",), tags=("gpu",))
    result = matches_capabilities(["orca"], ["gpu", "mpi"], declared=declared, probed_software=None)
    assert result.satisfies is False
    assert result.missing_software == ()
    assert result.missing_tags == ("mpi",)
    assert any("mpi" in r for r in result.reasons)


def test_match_probe_inferred_software_judged_by_probe() -> None:
    """Undeclared + probe succeeded → software judged against the probe set."""
    ok = matches_capabilities(["orca"], [], declared=None, probed_software=["orca", "xtb"])
    assert ok.satisfies is True
    bad = matches_capabilities(
        ["orca", "censo"], [], declared=None, probed_software=["orca", "xtb"]
    )
    assert bad.satisfies is False
    assert bad.missing_software == ("censo",)


def test_match_probe_inferred_tags_never_satisfied() -> None:
    """D13: tags cannot be probed — ANY tag requirement fails on an
    undeclared node, even when the software side is fully satisfied."""
    result = matches_capabilities(["orca"], ["gpu"], declared=None, probed_software=["orca"])
    assert result.satisfies is False
    assert result.missing_software == ()
    assert result.missing_tags == ("gpu",)


@pytest.mark.parametrize("probed", [None, [], ()])
def test_match_unknown_state_software_generic_tags_never(probed: object) -> None:
    """D8 unknown state: no declaration + empty/None probe → software
    requirements fall back to "generic" (satisfied); D13: tags still never
    satisfied."""
    ok = matches_capabilities(["orca", "shermo"], [], declared=None, probed_software=probed)
    assert ok.satisfies is True
    assert ok.missing_software == ()
    tagged = matches_capabilities([], ["gpu"], declared=None, probed_software=probed)
    assert tagged.satisfies is False
    assert tagged.missing_tags == ("gpu",)


def test_match_empty_requirements_satisfy_everyone() -> None:
    """Empty requirements → every node state satisfies (generic jobs)."""
    for declared, probed in (
        (NodeCapabilities(software=(), tags=()), ()),
        (None, ("orca",)),
        (None, None),
    ):
        result = matches_capabilities([], [], declared=declared, probed_software=probed)
        assert result.satisfies is True
        assert result.reasons == ()


def test_match_result_is_frozen() -> None:
    result = matches_capabilities(["orca"], [], declared=None, probed_software=["orca"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.satisfies = False  # type: ignore[misc]


# ---------------------------------------------------------------------- #
# 4. local_satisfies — config-aware monkeypatched resolver
# ---------------------------------------------------------------------- #


def _fake_resolver(found: set[str]):
    def resolve(name: str, configured_path: object = None) -> Path | None:
        return Path(f"/opt/fake/{name}") if name in found else None

    return resolve


def _patch_config(monkeypatch: pytest.MonkeyPatch, executables: dict) -> None:
    """Pin capabilities.load_config to a static ``executables`` table."""
    import acp.scheduler.capabilities as cap

    monkeypatch.setattr(cap, "load_config", lambda: {"executables": executables})


def test_local_satisfies_all_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    import acp.scheduler.capabilities as cap

    _patch_config(monkeypatch, {})
    monkeypatch.setattr(cap, "resolve_executable", _fake_resolver({"orca", "xtb"}))
    assert local_satisfies(["orca", "xtb"]) is True


def test_local_satisfies_one_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import acp.scheduler.capabilities as cap

    _patch_config(monkeypatch, {})
    monkeypatch.setattr(cap, "resolve_executable", _fake_resolver({"orca"}))
    assert local_satisfies(["orca", "crest"]) is False


def test_local_satisfies_empty_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    import acp.scheduler.capabilities as cap

    def _fail_load() -> dict:
        raise AssertionError("load_config must not run for empty requirements")

    calls: list[str] = []
    monkeypatch.setattr(cap, "load_config", _fail_load)
    monkeypatch.setattr(
        cap, "resolve_executable", lambda name, configured_path=None: calls.append(name) or None
    )
    assert local_satisfies([]) is True
    assert calls == []


def test_local_satisfies_forwards_configured_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """``executables.<name>.path`` is forwarded to the resolver (config fix).

    Mirrors cli.py ``_preflight_workflow``: dict entries contribute
    ``path``; non-dict entries are ignored.  The bug this locks: bare
    ``resolve_executable(name)`` never saw ``~/.cccp.yaml``, so config-only
    installs mis-reported False (spurious auto→remote upgrade + bogus
    "本机缺少所需软件" hint).
    """
    import acp.scheduler.capabilities as cap

    received: dict[str, object] = {}

    def fake_resolve(name: str, configured_path: object = None) -> Path | None:
        received[name] = configured_path
        return Path(f"/opt/fake/{name}")

    _patch_config(
        monkeypatch,
        {
            "orca": {"path": "/opt/orca/orca"},
            "xtb": "/not/a/dict",  # non-dict entry → ignored (cli.py parity)
        },
    )
    monkeypatch.setattr(cap, "resolve_executable", fake_resolve)
    assert local_satisfies(["orca", "xtb"]) is True
    assert received["orca"] == "/opt/orca/orca"
    assert received["xtb"] is None


def test_local_satisfies_config_only_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """Software absent from PATH but configured in ``~/.cccp.yaml`` → True.

    The fake resolver succeeds only through an explicit configured_path,
    so this fails against the old bare-resolution implementation.
    """
    import acp.scheduler.capabilities as cap

    def resolve(name: str, configured_path: object = None) -> Path | None:
        return Path(str(configured_path)) if configured_path else None

    _patch_config(monkeypatch, {"crest": {"path": "/opt/censo/crest"}})
    monkeypatch.setattr(cap, "resolve_executable", resolve)
    assert local_satisfies(["crest"]) is True


def test_local_satisfies_missing_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    """No config entry and nothing resolvable → False."""
    import acp.scheduler.capabilities as cap

    _patch_config(monkeypatch, {})
    monkeypatch.setattr(cap, "resolve_executable", _fake_resolver(set()))
    assert local_satisfies(["censo"]) is False


def test_local_satisfies_tolerates_config_load_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable/malformed config degrades to bare env/PATH resolution
    (cli.py ``_preflight_workflow`` except-branch parity)."""
    import acp.scheduler.capabilities as cap

    def _boom() -> dict:
        raise RuntimeError("malformed yaml")

    monkeypatch.setattr(cap, "load_config", _boom)
    monkeypatch.setattr(cap, "resolve_executable", _fake_resolver({"orca"}))
    assert local_satisfies(["orca"]) is True


# ---------------------------------------------------------------------- #
# 5. is_degraded — single load/disk predicate
# ---------------------------------------------------------------------- #


def _status(running: int, max_jobs: int, disk: float) -> SimpleNamespace:
    return SimpleNamespace(running_jobs=running, max_jobs=max_jobs, disk_usage_pct=disk)


def test_is_degraded_full_node() -> None:
    assert is_degraded(_status(running=5, max_jobs=5, disk=10.0)) is True
    assert is_degraded(_status(running=7, max_jobs=5, disk=10.0)) is True


def test_is_degraded_bad_disk() -> None:
    assert is_degraded(_status(running=1, max_jobs=5, disk=90.0)) is True
    assert is_degraded(_status(running=1, max_jobs=5, disk=99.5)) is True


def test_is_degraded_healthy() -> None:
    assert is_degraded(_status(running=2, max_jobs=5, disk=61.0)) is False
    assert is_degraded(_status(running=0, max_jobs=1, disk=89.9)) is False


def test_is_degraded_missing_axes_disable_them() -> None:
    """Missing/non-positive max_jobs disables the load axis; a missing
    disk_usage_pct disables the disk axis."""
    assert is_degraded(SimpleNamespace(running_jobs=99, max_jobs=None, disk_usage_pct=50)) is False
    assert is_degraded(SimpleNamespace(running_jobs=1, max_jobs=0, disk_usage_pct=95)) is True
    assert is_degraded(SimpleNamespace(running_jobs=3, max_jobs=5)) is False


# ---------------------------------------------------------------------- #
# 6. Error classes
# ---------------------------------------------------------------------- #


def test_no_capable_node_error_fields() -> None:
    err = NoCapableNodeError(
        "no node can run this job",
        missing_software=["censo", "orca"],
        missing_tags=["gpu"],
    )
    assert isinstance(err, RuntimeError)
    assert err.code == "no_capable_node"
    assert err.missing_software == ("censo", "orca")  # sorted
    assert err.missing_tags == ("gpu",)
    assert "no node can run this job" in str(err)
    bare = NoCapableNodeError("tags only", missing_tags=["mpi"])
    assert bare.missing_software == ()


def test_execution_target_error_backward_compatible_defaults() -> None:
    """Legacy positional construction keeps codeless behaviour."""
    err = ExecutionTargetError("target_node 'x' not found")
    assert err.code is None
    assert err.missing_software == ()
    assert err.missing_tags == ()


def test_execution_target_error_extended_fields() -> None:
    err = ExecutionTargetError(
        "target node lacks required software",
        code="target_node_incapable",
        missing_software=["shermo", "orca"],
        missing_tags=["gpu"],
    )
    assert isinstance(err, RuntimeError)
    assert err.code == "target_node_incapable"
    assert err.missing_software == ("orca", "shermo")  # sorted
    assert err.missing_tags == ("gpu",)


# ---------------------------------------------------------------------- #
# 7. JobSpec.node_tags + store round-trip tolerance
# ---------------------------------------------------------------------- #


def test_jobspec_node_tags_default_empty_list() -> None:
    spec = JobSpec(workflow="nmr")
    assert spec.node_tags == []
    assert spec.to_dict()["node_tags"] == []


def test_store_roundtrip_preserves_node_tags(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.db")
    spec = JobSpec(
        workflow="Confsearch",
        name="t",
        method={"protocol": "xtb-crest"},
        node_tags=["gpu", "fast-scratch"],
    )
    store.create(JobRecord(id="job-1", spec=spec))
    loaded = store.get("job-1")
    assert loaded is not None
    assert loaded.spec.node_tags == ["gpu", "fast-scratch"]


def test_store_tolerates_spec_json_without_node_tags(tmp_path: Path) -> None:
    """Rows written before the field existed deserialize to the default [].

    The tolerance lives in store.py:452 (``spec_raw.get("node_tags", [])``).
    """
    store = JobStore(tmp_path / "jobs.db")
    store.create(JobRecord(id="job-2", spec=JobSpec(workflow="nmr", name="old")))
    with store._connect() as conn:
        row = conn.execute("SELECT spec_json FROM jobs WHERE id='job-2'").fetchone()
        payload = json.loads(row["spec_json"])
        payload.pop("node_tags", None)  # simulate a pre-field row
        conn.execute(
            "UPDATE jobs SET spec_json=? WHERE id='job-2'",
            (json.dumps(payload),),
        )
        conn.commit()
    loaded = store.get("job-2")
    assert loaded is not None
    assert loaded.spec.node_tags == []


# ---------------------------------------------------------------------- #
# 8. Module purity — no node_manager import (cycle guard for T2)
# ---------------------------------------------------------------------- #


def test_capabilities_module_never_imports_node_manager() -> None:
    """capabilities.py must stay importable without node_manager (context
    constraint): a static AST guard over every import in the module."""
    import acp.scheduler.capabilities as cap

    for node in ast.walk(ast.parse(inspect.getsource(cap))):
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "node_manager" not in node.module, (
                f"forbidden import: {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "node_manager" not in alias.name, f"forbidden import: {alias.name}"


def test_scheduler_package_reexports_capabilities_symbols() -> None:
    import acp.scheduler as sched

    for name in (
        "MatchResult",
        "NoCapableNodeError",
        "derive_required_software",
        "is_degraded",
        "local_satisfies",
        "matches_capabilities",
    ):
        assert hasattr(sched, name)
        assert name in sched.__all__


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
