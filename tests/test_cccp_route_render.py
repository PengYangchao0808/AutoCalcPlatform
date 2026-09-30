# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
"""T5: single route renderer for every ORCA ``!``-simple-input assembly point.

Locks the renderer contract:
- legacy grid aliases canonicalize (``UltraFine`` -> ``DefGrid3``) with a
  migration warning and the raw token NEVER reaches the ``!`` line —
  through ``render_route_line`` and through every converted site;
- ``normal`` / ``none`` no-ops are skipped silently;
- unknown enum values fail fast with :class:`KeywordValueError` (no raw
  bypass, including ts_opt_route's former verbatim ``grid``/``scf``);
- applicability is consulted for the family x implementation (GFN strips
  grid/dispersion);
- free-form ``route_extras`` passthrough is unchanged;
- the historical site quirks (DLPNO TightSCF suppression, ``ri_support ==
  "user"`` dispersion gate, builtin-dispersion suppression, dedup) survive.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import numpy as np
import pytest

from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.orca_ts import irc_route, ts_opt_route
from cccp.qc.interfaces.route_render import (
    RouteKeyword,
    orca_gfn_solvent_token,
    orca_keyword_context,
    render_route_line,
)
from cccp.qc.keyword_registry import KeywordValueError


def _bare_orca(method: str = "B3LYP", basis: str = "def2-SVP") -> ORCAInterface:
    iface = ORCAInterface.__new__(ORCAInterface)
    iface.method = method
    iface.basis = basis
    iface.solvent = None
    iface.solvent_model = "none"
    iface.maxcore = 1000
    iface.nproc = 1
    iface.charge = 0
    iface.multiplicity = 1
    iface.config = {}
    iface.executable = None
    return iface


def _warned(caplog: pytest.LogCaptureFixture, fragment: str) -> bool:
    return any(fragment in record.getMessage() for record in caplog.records)


# ── renderer core ───────────────────────────────────────────────────────────


def test_renderer_ultrafine_yields_defgrid3_and_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        line = render_route_line(
            [RouteKeyword("grid", "UltraFine")],
            method="B3LYP",
        )
    assert "DefGrid3" in line
    assert "UltraFine" not in line
    assert _warned(caplog, "legacy alias")


def test_renderer_grid_aliases_are_canonicalized(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cases = [("SG1", "DefGrid1"), ("Fine", "DefGrid2"), ("SuperFine", "DefGrid3")]
    for value, canonical in cases:
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            line = render_route_line([RouteKeyword("grid", value)], method="B3LYP")
        assert canonical in line
        assert value not in line
        assert _warned(caplog, "legacy alias")


def test_renderer_noop_normal_none_skipped_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        line = render_route_line(
            [
                "Opt",
                RouteKeyword("opt_level", "normal"),
                RouteKeyword("scf_convergence", "normal"),
                RouteKeyword("scf_strategy", "normal"),
                RouteKeyword("dispersion", "none"),
            ],
            method="B3LYP",
        )
    assert line == "! Opt"
    assert not caplog.records


def test_renderer_unknown_enum_fails_fast() -> None:
    with pytest.raises(KeywordValueError, match="grid"):
        render_route_line([RouteKeyword("grid", "bogus")], method="B3LYP")
    with pytest.raises(KeywordValueError, match="dispersion"):
        render_route_line([RouteKeyword("dispersion", "D5")], method="B3LYP")


def test_renderer_applicability_strips_gfn_grid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    family, implementation = orca_keyword_context("GFN2-xTB")
    assert family == "gfn"
    with caplog.at_level(logging.WARNING):
        line = render_route_line(
            ["OptTS", RouteKeyword("grid", "DefGrid2"), RouteKeyword("dispersion", "D4")],
            method="GFN2-xTB",
        )
    assert line == "! OptTS"
    assert _warned(caplog, "never emitted")


def test_renderer_dedups_governed_against_literals() -> None:
    line = render_route_line(
        ["TightSCF", RouteKeyword("scf_convergence", "tight")],
        method="B3LYP",
    )
    assert line == "! TightSCF"


def test_renderer_case_insensitive_enum_lookup() -> None:
    line = render_route_line([RouteKeyword("grid", "defgrid3")], method="B3LYP")
    assert line == "! DefGrid3"


# ── sites: ts_opt_route (former verbatim grid/scf bypass) ───────────────────


def test_ts_opt_route_grid_ultrafine_never_emits_raw_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        route = ts_opt_route("B3LYP", "def2-SVP", grid="UltraFine")
    assert "DefGrid3" in route
    assert "UltraFine" not in route
    assert _warned(caplog, "legacy alias")


def test_ts_opt_route_scf_is_registry_governed() -> None:
    route = ts_opt_route("B3LYP", "def2-SVP", scf="tight")
    assert "TightSCF" in route
    assert " tight " not in f" {route} "


def test_ts_opt_route_scf_rejects_raw_token_spelling() -> None:
    with pytest.raises(KeywordValueError, match="scf_convergence"):
        ts_opt_route("B3LYP", "def2-SVP", scf="TightSCF")


def test_ts_opt_route_grid_rejects_unknown() -> None:
    with pytest.raises(KeywordValueError, match="grid"):
        ts_opt_route("B3LYP", "def2-SVP", grid="GaussUltra")


def test_ts_opt_route_gfn_grid_is_stripped() -> None:
    route = ts_opt_route("GFN2-xTB", "", grid="DefGrid2", scf="tight")
    assert "DefGrid" not in route
    assert "TightSCF" in route


# ── sites: _build_input_blocks ──────────────────────────────────────────────


def test_build_input_blocks_grid_ultrafine_never_emits_raw_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca()
    with caplog.at_level(logging.WARNING):
        out, _ = iface._build_input_blocks("opt", grid="UltraFine", recalc_hess=0)
    route = out.splitlines()[0]
    assert "DefGrid3" in route
    assert "UltraFine" not in route
    assert _warned(caplog, "legacy alias")


def test_build_input_blocks_bogus_grid_fails_fast() -> None:
    iface = _bare_orca()
    with pytest.raises(KeywordValueError, match="grid"):
        iface._build_input_blocks("opt", grid="HugeGrid", recalc_hess=0)


def test_build_input_blocks_gfn_guard_keeps_grid_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="")
    with caplog.at_level(logging.WARNING):
        out, _ = iface._build_input_blocks("opt", grid="UltraFine", recalc_hess=0)
    route = out.splitlines()[0]
    assert "DefGrid" not in route and "UltraFine" not in route
    assert not caplog.records


def test_build_input_blocks_dlpno_suppresses_duplicate_tightscf() -> None:
    iface = _bare_orca(method="dlpno-ccsd(t)", basis="cc-pVTZ")
    out, _ = iface._build_input_blocks("opt", scf_convergence="tight", recalc_hess=0)
    route = out.splitlines()[0]
    assert route.count("TightSCF") == 1
    assert route.startswith("! DLPNO-CCSD(T) TightSCF Opt")


def test_build_input_blocks_dispersion_gate_for_composite() -> None:
    iface = _bare_orca(method="r2SCAN-3c", basis="")
    out, _ = iface._build_input_blocks("opt", dispersion="D4", recalc_hess=0)
    route = out.splitlines()[0]
    assert "D4" not in route


def test_build_input_blocks_builtin_dispersion_never_emitted() -> None:
    iface = _bare_orca(method="wB97X-D4", basis="def2-SVP")
    out, _ = iface._build_input_blocks("opt", dispersion="d4", recalc_hess=0)
    route = out.splitlines()[0]
    assert " D4" not in f" {route}"
    assert "wB97X-D4" in route


def test_build_input_blocks_free_form_extras_pass_through_verbatim() -> None:
    iface = _bare_orca()
    out, _ = iface._build_input_blocks(
        "sp",
        route_extras=["RIJCOSX", "MyCustomKeyword", "verytightscf"],
        recalc_hess=0,
    )
    route = out.splitlines()[0]
    assert "MyCustomKeyword" in route
    assert "verytightscf" in route
    assert "RIJCOSX" in route


# ── sites: casscf / NMR / irc_route ─────────────────────────────────────────


def test_casscf_route_goes_through_renderer() -> None:
    iface = _bare_orca()
    coords = np.zeros((1, 3))
    with tempfile.TemporaryDirectory() as td:
        iface.casscf(
            coords,
            ["C"],
            0,
            1,
            Path(td),
            "case",
            active_electrons=2,
            active_orbitals=2,
        )
        first = (Path(td) / "case.inp").read_text().splitlines()[0]
    assert first == "! def2-SVP CASSCF TightSCF"


def test_nmr_route_goes_through_renderer() -> None:
    iface = _bare_orca(method="", basis="")
    coords = np.zeros((1, 3))
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "nmr.inp"
        iface._write_nmr_input(
            path,
            coords,
            ["C"],
            0,
            1,
            solvent="toluene",
            solvent_model="SMD",
        )
        lines = path.read_text().splitlines()[:2]
    assert lines[0] == "! mPW1PW91 6-311G(d) TightSCF"
    assert lines[1] == "! SMD(Toluene)"


def test_irc_route_goes_through_renderer() -> None:
    assert irc_route("B3LYP", "def2-SVP") == "! IRC B3LYP def2-SVP"
    assert irc_route("r2SCAN-3c", "x") == "! IRC r2SCAN-3c"


# ── T6: GFN parameter stripping across EVERY entry point ────────────────────
#
# `ts_opt_route` / `irc_route` bypass `_build_input_blocks` entirely, so the
# renderer's applicability gate is the single stripping point. These tests
# pin: no basis token, no %basis block, no governed grid/dispersion/ri/aux
# tokens for the whole GFN family — with a warning — while conventional DFT
# and composite 3c output stays byte-identical.

GFN_METHODS = ["GFN2-xTB", "GFN1-xTB", "GFN0-xTB", "GFN-FF", "Native-GFN2-xTB"]


@pytest.mark.parametrize("calc_type,run_token", [("opt", "Opt"), ("sp", "SP"), ("freq", "Freq")])
@pytest.mark.parametrize("method", GFN_METHODS)
def test_build_input_blocks_gfn_strips_basis_and_basis_block(
    method: str,
    calc_type: str,
    run_token: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method=method, basis="def2-TZVPP")
    with caplog.at_level(logging.WARNING):
        out, _ = iface._build_input_blocks(
            calc_type,
            basis="def2-SVP",
            aux_j_basis="def2/J",
            aux_c_basis="def2-TZVPP/C",
            grid="DefGrid2",
            dispersion="D4",
            recalc_hess=0,
        )
    route = out.splitlines()[0]
    assert route.split() == ["!", method, run_token]
    assert "def2" not in route
    assert "%basis" not in out and "auxJ" not in out and "auxC" not in out
    assert "DefGrid" not in route and "D4" not in route
    assert _warned(caplog, "never emitted")


@pytest.mark.parametrize("method", GFN_METHODS)
def test_build_input_blocks_gfn_strips_inherited_default_basis(
    method: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method=method, basis="def2-TZVPP")
    with caplog.at_level(logging.WARNING):
        out, _ = iface._build_input_blocks("sp", recalc_hess=0)
    route = out.splitlines()[0]
    assert route.split() == ["!", method, "SP"]
    assert "def2-TZVPP" not in out
    assert _warned(caplog, "never emitted")


def test_build_input_blocks_gfn_keeps_route_extras_verbatim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="")
    with caplog.at_level(logging.WARNING):
        out, _ = iface._build_input_blocks(
            "opt", route_extras=["RIJCOSX", "MyCustomKeyword", "def2/J"], recalc_hess=0
        )
    route = out.splitlines()[0]
    assert "RIJCOSX" in route and "MyCustomKeyword" in route
    assert "def2/J" not in out and "%basis" not in out
    assert _warned(caplog, "never emitted")


def test_build_input_blocks_dft_basis_and_basis_block_unchanged() -> None:
    iface = _bare_orca(method="B3LYP", basis="def2-TZVPP")
    out, _ = iface._build_input_blocks("opt", basis="def2-SVP", aux_j_basis="def2/J", recalc_hess=0)
    route = out.splitlines()[0]
    assert route == "! B3LYP def2-SVP Opt"
    assert "%basis" in out and 'auxJ  "def2/J"' in out


@pytest.mark.parametrize("method", GFN_METHODS)
def test_ts_opt_route_gfn_strips_basis_ri_aux(
    method: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        route = ts_opt_route(
            method,
            "def2-SVP",
            grid="DefGrid2",
            scf="tight",
            aux_j="def2/J",
            ri_approximation="RIJCOSX",
        )
    assert route.split() == ["!", method, "TightSCF", "OptTS", "NumFreq"]
    assert "def2" not in route and "RIJCOSX" not in route and "DefGrid" not in route
    assert " aux " not in f" {route} "
    assert _warned(caplog, "never emitted")


def test_ts_opt_route_ri_aux_group_unchanged_for_dft_and_3c() -> None:
    dft = ts_opt_route("B3LYP", "def2-SVP", aux_j="def2/J", ri_approximation="RIJCOSX")
    assert dft == "! B3LYP def2-SVP OptTS NumFreq RIJCOSX aux def2/J"
    composite = ts_opt_route("r2SCAN-3c", "def2-mTZVPP", aux_j="def2/J", ri_approximation="RIJCOSX")
    assert composite == "! r2SCAN-3c OptTS NumFreq RIJCOSX aux def2/J"


@pytest.mark.parametrize("method", GFN_METHODS)
def test_irc_route_gfn_strips_basis(
    method: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        route = irc_route(method, "def2-SVP")
    assert route.split() == ["!", "IRC", method]
    assert "def2" not in route
    assert _warned(caplog, "never emitted")


@pytest.mark.parametrize("method", GFN_METHODS)
def test_nmr_route_gfn_strips_basis(
    method: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method=method, basis="")
    coords = np.zeros((1, 3))
    with caplog.at_level(logging.WARNING):
        iface._write_nmr_input(tmp_path / "nmr.inp", coords, ["C"], 0, 1)
    first = (tmp_path / "nmr.inp").read_text().splitlines()[0]
    assert first.split() == ["!", method, "TightSCF"]
    assert "6-311G(d)" not in first
    assert _warned(caplog, "never emitted")


def test_relaxed_scan_gfn_strips_inherited_default_basis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="def2-TZVPP")
    monkeypatch.setattr(iface, "_run_orca", lambda *args, **kwargs: False)
    with caplog.at_level(logging.WARNING):
        result = iface.relaxed_scan(
            np.zeros((1, 3)),
            ["H"],
            scan_coordinate=CoordinateSpec(
                id="rc1", kind="distance", atoms=(0, 0), start=1.0, end=2.0
            ),
            points=2,
            output_dir=tmp_path,
            output_name="gfn_scan",
        )
    assert result.success is False
    input_text = (tmp_path / "gfn_scan.inp").read_text()
    route = input_text.splitlines()[0]
    assert route.split() == ["!", "GFN2-xTB", "Opt", "ScanTS"]
    assert "def2" not in input_text and "%basis" not in input_text
    assert _warned(caplog, "never emitted")


def test_synchronous_relaxed_scan_gfn_strips_inherited_default_basis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="def2-TZVPP")
    monkeypatch.setattr(iface, "_run_orca", lambda *args, **kwargs: False)
    plan = ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="rc1", kind="distance", atoms=(0, 0), start=1.0, end=2.0),
            CoordinateSpec(id="rc2", kind="distance", atoms=(0, 0), start=3.0, end=4.0),
        ),
        points=2,
    )
    with caplog.at_level(logging.WARNING):
        result = iface.relaxed_scan(
            np.zeros((1, 3)),
            ["H"],
            plan=plan,
            charge=0,
            multiplicity=1,
            output_dir=tmp_path,
            output_name="gfn_sync",
        )
    assert result.success is False
    frame_input = tmp_path / "frame_000" / "gfn_sync_try1.inp"
    input_text = frame_input.read_text()
    route = input_text.splitlines()[0]
    assert route.split() == ["!", "GFN2-xTB", "Opt"]
    assert "def2" not in input_text and "%basis" not in input_text
    assert _warned(caplog, "never emitted")


# ── T7: GFN solvent semantics (ALPB-only under ORCA) ───────────────────────
#
# PLATFORM POLICY (decision Q1: {none, ALPB} only) — distinct from software
# capability: ALPB is implemented by ORCA's external xTB (T22 case 3) and
# GBSA is not an ORCA keyword at all (T22 case 4, rc=4), but the rule is
# policy and would hold even if ORCA gained GBSA. The standalone xTB binary
# keeps {none, ALPB, GBSA} (T9). The user model is never silently rewritten.


def test_gfn_solvent_token_alpb_emits_mapped_orca_name() -> None:
    assert orca_gfn_solvent_token("GFN2-xTB", "water", "ALPB") == "ALPB(Water)"
    assert orca_gfn_solvent_token("GFN2-xTB", "acetone", "alpb") == "ALPB(Acetone)"
    assert orca_gfn_solvent_token("GFN-FF", "toluene", "Alpb") == "ALPB(Toluene)"
    assert orca_gfn_solvent_token("Native-GFN2-xTB", "water", "ALPB") == "ALPB(Water)"


def test_gfn_solvent_token_none_and_unset_emit_nothing() -> None:
    assert orca_gfn_solvent_token("GFN2-xTB", "water", "none") is None
    assert orca_gfn_solvent_token("GFN2-xTB", "water", "None") is None
    assert orca_gfn_solvent_token("GFN2-xTB", "water", None) is None
    assert orca_gfn_solvent_token("GFN2-xTB", "water", "") is None
    assert orca_gfn_solvent_token("GFN2-xTB", None, "ALPB") is None
    assert orca_gfn_solvent_token("GFN2-xTB", None, None) is None


@pytest.mark.parametrize("model", ["GBSA", "gbsa", "CPCM", "SMD", "smd", "custom"])
def test_gfn_solvent_token_gbsa_and_dft_models_rejected(model: str) -> None:
    with pytest.raises(KeywordValueError) as exc:
        orca_gfn_solvent_token("GFN2-xTB", "water", model)
    assert "PLATFORM POLICY" in str(exc.value)


def test_gfn_solvent_token_rejects_non_gfn_misuse() -> None:
    with pytest.raises(ValueError):
        orca_gfn_solvent_token("B3LYP", "water", "ALPB")


def test_ts_opt_route_gfn_alpb_solvent_emits_token() -> None:
    route = ts_opt_route("GFN2-xTB", "", solvent="water", solvent_model="ALPB")
    assert "ALPB(Water)" in route.split()


def test_ts_opt_route_gfn_none_solvent_model_emits_nothing() -> None:
    route = ts_opt_route("GFN2-xTB", "", solvent="water", solvent_model="none")
    assert "ALPB" not in route and "Water" not in route


def test_ts_opt_route_gfn_gbsa_rejected() -> None:
    with pytest.raises(KeywordValueError):
        ts_opt_route("GFN2-xTB", "", solvent="water", solvent_model="GBSA")


def test_irc_route_gfn_solvent_alpb_only() -> None:
    route = irc_route("GFN2-xTB", "", solvent="water", solvent_model="ALPB")
    assert "ALPB(Water)" in route.split()
    assert "ALPB" not in irc_route("GFN2-xTB", "", solvent="water", solvent_model="none")
    with pytest.raises(KeywordValueError):
        irc_route("GFN2-xTB", "", solvent="water", solvent_model="gbsa")


def test_ts_and_irc_route_dft_solvent_verbatim_unchanged() -> None:
    dft_ts = ts_opt_route("B3LYP", "def2-SVP", solvent="toluene", solvent_model="SMD")
    assert dft_ts == "! B3LYP def2-SVP SMD(toluene) OptTS NumFreq"
    dft_irc = irc_route("B3LYP", "def2-SVP", solvent="toluene", solvent_model="SMD")
    assert dft_irc == "! IRC B3LYP def2-SVP SMD(toluene)"


@pytest.mark.parametrize("method", GFN_METHODS)
def test_build_input_blocks_gfn_alpb_solvent_emits_token_no_cpcm(method: str) -> None:
    iface = _bare_orca(method=method, basis="")
    out, _ = iface._build_input_blocks(
        "opt", solvent="water", solvent_model="ALPB", recalc_hess=0
    )
    route = out.splitlines()[0]
    assert "ALPB(Water)" in route.split()
    assert "%cpcm" not in out and "SMDsolvent" not in out


def test_build_input_blocks_gfn_none_solvent_model_emits_nothing() -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="")
    out, _ = iface._build_input_blocks(
        "opt", solvent="water", solvent_model="none", recalc_hess=0
    )
    assert "ALPB" not in out and "Water" not in out and "%cpcm" not in out


def test_build_input_blocks_gfn_gbsa_rejected() -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="")
    with pytest.raises(KeywordValueError):
        iface._build_input_blocks(
            "opt", solvent="water", solvent_model="gbsa", recalc_hess=0
        )


def test_build_input_blocks_dft_solvent_still_cpcm_block() -> None:
    iface = _bare_orca(method="B3LYP", basis="def2-SVP")
    out, _ = iface._build_input_blocks(
        "opt", solvent="water", solvent_model="smd", recalc_hess=0
    )
    assert "%cpcm" in out and "smd true" in out and 'SMDsolvent "Water"' in out
    assert "ALPB" not in out


def test_nmr_route_gfn_alpb_solvent_emits_alpb_token(tmp_path: Path) -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="")
    coords = np.zeros((1, 3))
    iface._write_nmr_input(
        tmp_path / "nmr.inp", coords, ["C"], 0, 1, solvent="water", solvent_model="ALPB"
    )
    lines = (tmp_path / "nmr.inp").read_text().splitlines()
    assert "! ALPB(Water)" in lines
    assert all("CPCM" not in line and "SMD" not in line for line in lines)


def test_nmr_route_gfn_gbsa_rejected(tmp_path: Path) -> None:
    iface = _bare_orca(method="GFN2-xTB", basis="")
    coords = np.zeros((1, 3))
    with pytest.raises(KeywordValueError):
        iface._write_nmr_input(
            tmp_path / "nmr.inp", coords, ["C"], 0, 1, solvent="water", solvent_model="gbsa"
        )


def test_nmr_route_dft_solvent_unchanged(tmp_path: Path) -> None:
    iface = _bare_orca(method="B3LYP", basis="def2-SVP")
    coords = np.zeros((1, 3))
    iface._write_nmr_input(
        tmp_path / "nmr.inp", coords, ["C"], 0, 1, solvent="water", solvent_model="smd"
    )
    lines = (tmp_path / "nmr.inp").read_text().splitlines()
    assert "! SMD(Water)" in lines
