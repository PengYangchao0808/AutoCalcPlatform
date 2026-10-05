"""Translation single-source tests (plan todos 25 + 37/A5).

Covers the cccp render API (``cccp.qc.translation``): route-line + CENSO
template unit cases, summary-vs-actual-route consistency (display ==
execution), a no-duplicate-defaults guard over the batch effective config,
and the A5 acceptance rows — xTB argv/control normalization single-sourced
through ``cccp.utils.solvent_map`` / ``cccp.qc.interfaces.xtb_scan``, and
explicit consistency assertions across the three upstream ACP callers that
previously hand-built ``"! "`` route lines.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.keyword_registry import KeywordValueError
from cccp.qc.translation import (
    OrcaOptSpec,
    render_censo_template_lines,
    render_opt_geom_lines,
    render_orca_opt,
)

ROOT = Path(__file__).resolve().parent.parent


# ── CENSO template lines ──────────────────────────────────────────────────


class TestCensoTemplateLines:
    def test_empty_extras_render_no_line(self) -> None:
        assert render_censo_template_lines([]) == []

    def test_single_extra(self) -> None:
        assert render_censo_template_lines(["RI"]) == ["! RI"]

    def test_multiple_extras_join_single_space(self) -> None:
        assert render_censo_template_lines(["RI", "def2/J"]) == ["! RI def2/J"]

    def test_literals_emit_verbatim(self) -> None:
        assert render_censo_template_lines(["NoFrozenCore", "VeryTightSCF"]) == [
            "! NoFrozenCore VeryTightSCF"
        ]


# ── render_orca_opt: route / geom / summary unit cases ────────────────────


class TestRenderOrcaOpt:
    def test_tight_route_tokens(self) -> None:
        render = render_orca_opt(
            OrcaOptSpec(
                opt_level="tight",
                scf_convergence="tight",
                scf_strategy="slowconv",
            )
        )
        assert render.route_tokens == ("TightOpt", "TightSCF", "SlowConv")
        assert render.route_line() == "! TightOpt TightSCF SlowConv"

    def test_case_folding_via_registry(self) -> None:
        render = render_orca_opt(
            OrcaOptSpec(opt_level="VeryTight", scf_convergence="VERYTIGHT")
        )
        assert render.route_tokens == ("VeryTightOpt", "VeryTightSCF")

    def test_normal_noops_render_no_tokens(self) -> None:
        render = render_orca_opt(
            OrcaOptSpec(opt_level="normal", scf_convergence="normal", scf_strategy="normal")
        )
        assert render.route_tokens == ()
        assert render.summary_tokens == ("Opt",)

    def test_unknown_enum_fails_fast(self) -> None:
        with pytest.raises(KeywordValueError):
            render_orca_opt(OrcaOptSpec(opt_level="SuperTight"))
        with pytest.raises(KeywordValueError):
            render_orca_opt(OrcaOptSpec(scf_strategy="turbo"))

    def test_geom_lines_execution_order(self) -> None:
        render = render_orca_opt(
            OrcaOptSpec(
                max_cycles=200,
                trust_radius=0.3,
                initial_hessian="calculate",
                recalc_hess=5,
            )
        )
        assert render.geom_lines == (
            "  Calc_Hess true",
            "  Recalc_Hess 5",
            "  Trust 0.3",
            "  MaxIter 200",
        )
        assert render.geom_block().splitlines()[0] == "%geom"
        assert render.geom_block().splitlines()[-1] == "end"

    def test_summary_display_order(self) -> None:
        render = render_orca_opt(
            OrcaOptSpec(
                opt_level="tight",
                scf_convergence="tight",
                scf_strategy="soscf",
                max_cycles=400,
                trust_radius=0.1,
                initial_hessian="calculate",
                recalc_hess=5,
            )
        )
        assert render.summary_tokens == (
            "TightOpt",
            "TightSCF",
            "MaxIter 400",
            "Trust 0.1",
            "Calc_Hess",
            "Recalc_Hess 5",
            "SOSCF",
        )

    def test_no_calc_hess_for_model(self) -> None:
        render = render_orca_opt(
            OrcaOptSpec(initial_hessian="model", recalc_hess=5, max_cycles=100)
        )
        assert "Calc_Hess" not in render.summary_tokens
        assert all("Calc_Hess" not in line for line in render.geom_lines)

    def test_render_opt_geom_lines_matches_interface_body(self) -> None:
        lines = render_opt_geom_lines(
            initial_hessian="calculate",
            recalc_hess_interval=3,
            trust_radius=0.2,
            max_cycles=50,
            extra_lines=["  InHess Read"],
        )
        assert lines == [
            "  Calc_Hess true",
            "  Recalc_Hess 3",
            "  Trust 0.2",
            "  MaxIter 50",
            "  InHess Read",
        ]


# ── summary vs actual route: display == execution ────────────────────────


def _rendered_input(effective: dict[str, object]) -> str:
    interface = ORCAInterface.__new__(ORCAInterface)
    interface.method = "B3LYP"
    interface.basis = "def2-SVP"
    interface.solvent = None
    interface.solvent_model = None
    interface.maxcore = 4000
    interface.nproc = 4
    interface.config = {"optimization_control": {"recalc_hess": 0}}
    blocks, _resolution = interface._build_input_blocks(
        "opt",
        geom_maxiter=effective.get("max_cycles"),
        trust_radius=effective.get("opt_trust_radius"),
        initial_hessian=effective.get("opt_initial_hessian"),
        recalc_hess=effective.get("opt_recalc_hess"),
        opt_level=effective.get("opt_level"),
        scf_convergence=effective.get("scf_convergence"),
        scf_strategy=effective.get("scf_strategy"),
        symbols=["C"],
    )
    return blocks


class TestSummaryConsistency:
    @pytest.mark.parametrize(
        "effective",
        [
            {
                "opt_level": "tight",
                "scf_convergence": "tight",
                "scf_strategy": "normal",
                "max_cycles": 200,
                "opt_trust_radius": 0.3,
                "opt_initial_hessian": "calculate",
                "opt_recalc_hess": 5,
            },
            {
                "opt_level": "normal",
                "scf_convergence": "normal",
                "scf_strategy": "slowconv",
                "max_cycles": 400,
            },
            {
                "opt_level": "VeryTight",
                "scf_convergence": "verytight",
                "scf_strategy": "soscf",
                "max_cycles": 100,
                "opt_trust_radius": 0.1,
                "opt_initial_hessian": "calculate",
                "opt_recalc_hess": 3,
            },
            {
                "opt_level": "loose",
                "scf_convergence": "loose",
                "scf_strategy": "normal",
                "max_cycles": 25,
            },
        ],
    )
    def test_every_summary_token_appears_in_executed_input(
        self, effective: dict[str, object]
    ) -> None:
        from acp.calculations.batch.effective_config import build_orca_summary

        summary = build_orca_summary(dict(effective))
        assert summary, "summary must not be empty for a full effective dict"
        input_text = _rendered_input(effective)
        route_tokens = input_text.splitlines()[0].split()
        for token in summary:
            assert token in route_tokens or token in input_text, (
                f"display token {token!r} missing from executed input"
            )

    def test_summary_equals_translation_layer_projection(self) -> None:
        from acp.calculations.batch.effective_config import build_orca_summary

        effective = {
            "opt_level": "tight",
            "scf_convergence": "tight",
            "scf_strategy": "slowconv",
            "max_cycles": 200,
            "opt_trust_radius": 0.3,
            "opt_initial_hessian": "calculate",
            "opt_recalc_hess": 5,
        }
        render = render_orca_opt(
            OrcaOptSpec(
                opt_level=effective["opt_level"],
                scf_convergence=effective["scf_convergence"],
                scf_strategy=effective["scf_strategy"],
                max_cycles=effective["max_cycles"],
                trust_radius=effective["opt_trust_radius"],
                initial_hessian=effective["opt_initial_hessian"],
                recalc_hess=effective["opt_recalc_hess"],
            )
        )
        assert build_orca_summary(dict(effective)) == list(render.summary_tokens)

    def test_geom_lines_match_executed_geom_block(self) -> None:
        effective = {
            "opt_level": "tight",
            "max_cycles": 200,
            "opt_trust_radius": 0.3,
            "opt_initial_hessian": "calculate",
            "opt_recalc_hess": 5,
        }
        input_text = _rendered_input(effective)
        rendered = render_opt_geom_lines(
            initial_hessian="calculate",
            recalc_hess_interval=5,
            trust_radius=0.3,
            max_cycles=200,
        )
        for line in rendered:
            assert line in input_text.splitlines()


# ── no-duplicate-defaults guard ──────────────────────────────────────────
#
# Scope note (evidence task-25): the ``_base_route_extras`` template maps in
# ``acp/workflows/energy_shared.py`` / ``acp/confsearch/shared/helpers.py``
# are TEST-PINNED CENSO template projections (``tests/test_acp_censo_p5_acceptance.py``
# asserts the uppercase DEFGRID spellings and imports ``_base_route_extras``);
# their default-omission semantics differ from ``keyword_registry.resolve``
# and must stay byte-identical.  The guard below covers the summary/execution
# translation path, where a private keyword table is forbidden outright.


class TestNoDuplicateDefaults:
    def test_effective_config_has_no_private_keyword_tables(self) -> None:
        src = (
            ROOT / "src" / "acp" / "calculations" / "batch" / "effective_config.py"
        ).read_text(encoding="utf-8")
        for name in ("_OPT_LEVEL_KEYWORDS", "_SCF_CONVERGENCE_KEYWORDS", "_SCF_STRATEGY_KEYWORDS"):
            assert name not in src, f"{name} must not be duplicated — use cccp.qc.translation"
        for token in ("LooseOpt", "TightOpt", "VeryTightOpt", "SlowConv", "SOSCF"):
            assert token not in src, f"keyword token {token!r} is registry-owned"

    def test_calculations_layer_has_no_keyword_tables(self) -> None:
        offenders = []
        for path in (ROOT / "src" / "acp" / "calculations").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for name in (
                "_OPT_LEVEL_KEYWORDS",
                "_SCF_CONVERGENCE_KEYWORDS",
                "_SCF_STRATEGY_KEYWORDS",
            ):
                if name in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{name}")
        assert offenders == [], f"keyword tables leaked into calculations: {offenders}"

    def test_summary_tokens_come_from_registry_not_catalog(self) -> None:
        from acp.calculations.batch.effective_config import build_orca_summary
        from cccp.qc.keyword_registry import IMPL_ORCA_DFT, canonical_token

        expected = {
            ("opt_level", "tight"): "TightOpt",
            ("opt_level", "verytight"): "VeryTightOpt",
            ("opt_level", "loose"): "LooseOpt",
            ("scf_convergence", "tight"): "TightSCF",
            ("scf_strategy", "slowconv"): "SlowConv",
            ("scf_strategy", "soscf"): "SOSCF",
        }
        for (domain, value), token in expected.items():
            registry_token, _noop = canonical_token(domain, value, implementation=IMPL_ORCA_DFT)
            assert registry_token == token
        summary = build_orca_summary(
            {
                "opt_level": "tight",
                "scf_convergence": "tight",
                "scf_strategy": "slowconv",
                "max_cycles": 200,
            }
        )
        for token in ("TightOpt", "TightSCF", "SlowConv"):
            assert token in summary, f"{token} must flow from the registry into the summary"

    def test_build_orca_summary_delegates_to_translation(self) -> None:
        from acp.calculations.batch import effective_config

        assert "render_orca_opt" in inspect.getsource(effective_config.build_orca_summary)


# ── xTB argv/control single source (plan todo 37, A5) ─────────────────────

_XTB_ENTRY_SOURCES = (
    "src/cccp/qc/interfaces/xtb.py",
    "src/cccp/qc/interfaces/xtb_path.py",
    "src/cccp/qc/interfaces/xtb_thermo.py",
    "src/cccp/qc/interfaces/molclus.py",
    "src/cccp/qc/interfaces/crest.py",
)


class TestXtbSingleSource:
    @pytest.mark.parametrize("relpath", _XTB_ENTRY_SOURCES)
    def test_entry_points_share_solvent_normalization(self, relpath: str) -> None:
        src = (ROOT / relpath).read_text(encoding="utf-8")
        assert "xtb_solvent_args" in src, f"{relpath} must use the shared solvent args"
        assert "xtb_method_name" in src, f"{relpath} must use the shared method normalizer"

    def test_xtb_interface_uses_shared_constraint_block(self) -> None:
        src = (ROOT / "src" / "cccp" / "qc" / "interfaces" / "xtb.py").read_text(encoding="utf-8")
        assert "xcontrol_constraint_block" in src
        assert "from cccp.qc.interfaces.xtb_scan import" in src

    def test_solvent_args_delegate_to_shared_helper_at_runtime(self) -> None:
        from cccp.qc.interfaces.xtb import XTBInterface
        from cccp.utils.solvent_map import xtb_method_name, xtb_solvent_args

        xtb = XTBInterface.__new__(XTBInterface)
        xtb.solvent = "water"
        xtb.solvent_model = "alpb"
        xtb.gfn_level = 2
        assert xtb._solvent_args() == xtb_solvent_args(
            "water", method=xtb_method_name(2), solvent_model="alpb"
        )


class TestNoWorkflowBackendSyntax:
    """ACP workflows must not assemble backend input syntax (A5)."""

    def test_workflows_do_not_build_xtb_argv(self) -> None:
        offenders: list[str] = []
        roots = [ROOT / "src" / "acp" / "workflows", ROOT / "src" / "acp" / "confsearch"]
        for base in roots:
            for path in base.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for flag in ('"--gfn"', '"--opt"', '"--uhf"', '"--chrg"', '"--input"'):
                    if flag in text:
                        offenders.append(f"{path.relative_to(ROOT)}:{flag}")
        assert offenders == [], f"workflow assembled xTB argv: {offenders}"

    def test_acceptance_grep_route_line_concatenation_empty(self) -> None:
        # grep -rn '"! "\s*+' src/acp must be empty (route lines never concatenated).
        pattern = re.compile(r'"! "\s*\+')
        offenders = [
            f"{path.relative_to(ROOT)}:{match.start()}"
            for path in (ROOT / "src" / "acp").rglob("*.py")
            for match in pattern.finditer(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], f"hand-built route line(s): {offenders}"


# ── three upstream ACP callers route through the translation layer (A5) ────

_UPSTREAM_CALLERS = (
    ("src/acp/calculations/batch/effective_config.py", "render_orca_opt"),
    ("src/acp/workflows/energy_shared.py", "render_censo_template_lines"),
    ("src/acp/confsearch/shared/helpers.py", "render_censo_template_lines"),
)


class TestUpstreamCallerConsistency:
    @pytest.mark.parametrize(
        "relpath,expected_symbol",
        _UPSTREAM_CALLERS,
        ids=[path.rsplit("/", 1)[-1] for path, _ in _UPSTREAM_CALLERS],
    )
    def test_caller_imports_translation_layer(self, relpath: str, expected_symbol: str) -> None:
        src = (ROOT / relpath).read_text(encoding="utf-8")
        assert "from cccp.qc.translation import" in src, f"{relpath} bypasses translation"
        assert expected_symbol in src, f"{relpath} must use {expected_symbol}"
        assert re.search(r'"! "\s*\+', src) is None, f"{relpath} hand-builds a route line"

    def test_censo_template_round_trips_through_renderer(self) -> None:
        from acp.workflows.energy_shared import _part_template_tokens

        lines = render_censo_template_lines(["RI", "def2/J", "NoFrozenCore"])
        recovered = _part_template_tokens({"part_a": lines})
        assert recovered == {"part_a": ["RI", "def2/J", "NoFrozenCore"]}
        assert render_censo_template_lines(recovered["part_a"]) == lines

    def test_summary_matches_rendered_route_tokens_for_role_matrix(self) -> None:
        from acp.calculations.batch.effective_config import build_orca_summary

        cases = [
            {"opt_level": "loose", "max_cycles": 30},
            {"opt_level": "tight", "scf_convergence": "tight", "scf_strategy": "slowconv"},
            {
                "opt_level": "VeryTight",
                "scf_convergence": "VeryTight",
                "opt_initial_hessian": "calculate",
                "opt_recalc_hess": 4,
            },
        ]
        for effective in cases:
            render = render_orca_opt(
                OrcaOptSpec(
                    opt_level=effective.get("opt_level"),
                    scf_convergence=effective.get("scf_convergence"),
                    scf_strategy=effective.get("scf_strategy"),
                    max_cycles=effective.get("max_cycles"),
                    trust_radius=effective.get("opt_trust_radius"),
                    initial_hessian=effective.get("opt_initial_hessian"),
                    recalc_hess=effective.get("opt_recalc_hess"),
                )
            )
            assert build_orca_summary(dict(effective)) == list(render.summary_tokens)
            for token in render.route_tokens:
                assert token in render.route_line(), token
