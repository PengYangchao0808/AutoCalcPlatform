"""End-to-end chain tests for advanced BatchOptimize ORCA controls (plan §7).

Locks acceptance criteria #3–#8: a user-set value travels
scheduler method dict / CLI → ``BatchMethodOptions`` → engine kwargs →
ORCA ``.inp`` text, and the SCF trio reaches freq/SP request resources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest

from acp.calculations.batch.engine import BatchOptimizeEngine
from acp.calculations.batch.models import BatchStructureItem
from acp.calculations.batch.options import BatchMethodOptions
from acp.calculations.contracts import CalculationResult
from cccp.qc.interfaces.orca import ORCAInterface

FIXTURES = Path(__file__).parent / "fixtures"

# All-light molecule: the auto Hessian policy resolves to interval 0, so an
# unset ``recalc_hess`` never renders a ``Recalc_Hess`` line.
_SYMBOLS = ["C", "H", "H", "H", "H"]
_COORDS = np.array(
    [
        [0.0, 0.0, 0.0],
        [0.63, 0.63, 0.63],
        [-0.63, -0.63, 0.63],
        [-0.63, 0.63, -0.63],
        [0.63, -0.63, -0.63],
    ],
    dtype=float,
)

_ADVANCED_FLAGS: list[str] = [
    "--opt-max-iter",
    "400",
    "--opt-convergence",
    "verytight",
    "--opt-trust-radius",
    "0.1",
    "--opt-initial-hessian",
    "model",
    "--opt-recalc-hess",
    "20",
    "--opt-rescue-policy",
    "off",
    "--opt-max-rescue",
    "5",
    "--scf-max-iter",
    "500",
    "--scf-convergence",
    "tight",
    "--scf-strategy",
    "soscf",
    "--no-scf-orbital-inherit",
]

_ADVANCED_METHOD: dict[str, object] = {
    "opt_max_iter": 400,
    "opt_convergence": "verytight",
    "opt_trust_radius": 0.1,
    "opt_initial_hessian": "model",
    "opt_recalc_hess": 20,
    "opt_rescue_policy": "off",
    "opt_max_rescue": 5,
    "scf_max_iter": 500,
    "scf_convergence": "tight",
    "scf_strategy": "soscf",
    "scf_orbital_inherit": False,
}

_EXPECTED_ADVANCED = BatchMethodOptions(
    opt_max_iter=400,
    opt_convergence="verytight",
    opt_trust_radius=0.1,
    opt_initial_hessian="model",
    opt_recalc_hess=20,
    opt_rescue_policy="off",
    opt_max_rescue=5,
    scf_max_iter=500,
    scf_convergence="tight",
    scf_strategy="soscf",
    scf_orbital_inherit=False,
)


def _make_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 4},
    }
    config.update(overrides)
    return config


def _engine(tmp_path: Path, methods: BatchMethodOptions | None = None) -> BatchOptimizeEngine:
    return BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=tmp_path / "task" / "RESULT",
        methods=methods,
    )


def _render_opt_input(tmp_path: Path, methods: BatchMethodOptions, *, is_ts: bool) -> str:
    """Engine kwargs → ORCA input text, mirroring the backend handoff."""
    kwargs = _engine(tmp_path, methods)._optimization_kwargs(is_ts)
    orca = ORCAInterface(_make_config(), method="r2SCAN-3c")
    blocks, _ = orca._build_input_blocks(
        "opt",
        geom_maxiter=kwargs.get("max_cycles"),
        recalc_hess=kwargs.get("recalc_hess"),
        trust_radius=kwargs.get("trust_radius"),
        initial_hessian=kwargs.get("initial_hessian"),
        opt_level=kwargs.get("opt_level"),
        scf_maxiter=kwargs.get("scf_maxiter"),
        scf_convergence=kwargs.get("scf_convergence"),
        scf_strategy=kwargs.get("scf_strategy"),
        symbols=_SYMBOLS,
    )
    return blocks


def _block(text: str, header: str) -> str:
    """Return the ``header`` … ``end`` sub-block of an ORCA input text."""
    start = text.index(header)
    end = text.index("end", start)
    return text[start:end]


def _ts_item() -> BatchStructureItem:
    return BatchStructureItem(
        item_id="candidate_001",
        name="TS candidate",
        tag="TS",
        xyz="2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
        candidate_id="candidate_001",
    )


def _options_from_cli(tmp_path: Path, extra: list[str]) -> BatchMethodOptions:
    """Parse extra BatchOptimize flags and capture the built options."""
    from acp.cli import _handle_batch_optimize, build_parser
    from acp.core.workflow import WorkflowResult

    args = build_parser().parse_args(
        [
            "run",
            "BatchOptimize",
            "--items-file",
            str(FIXTURES / "batch_structures_v1.json"),
            "--output",
            str(tmp_path / "batch_output"),
            *extra,
        ]
    )
    with patch("acp.workflows.batch_optimize.run_batch_optimize") as run:
        run.return_value = WorkflowResult(status="completed")
        assert _handle_batch_optimize(args) == 0
    return run.call_args.kwargs["methods"]


# ── user value → engine kwargs → ORCA .inp ───────────────────────────────


class TestEngineKwargsToOrcaInput:
    """Acceptance #3–#7: explicit user values reach the rendered input."""

    def test_opt_max_iter_400_reaches_geom_block(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(opt_max_iter=400), is_ts=False)
        assert "MaxIter 400" in _block(blocks, "%geom")

    def test_trust_radius_renders_trust_and_never_trustradius(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(opt_trust_radius=0.10), is_ts=False)
        assert "Trust 0.1" in _block(blocks, "%geom")
        assert "TrustRadius" not in blocks

    def test_initial_hessian_calculate_renders_calc_hess(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(
            tmp_path, BatchMethodOptions(opt_initial_hessian="calculate"), is_ts=False
        )
        assert "Calc_Hess true" in _block(blocks, "%geom")

    def test_recalc_hess_5_renders_interval(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(opt_recalc_hess=5), is_ts=False)
        assert "Recalc_Hess 5" in _block(blocks, "%geom")

    @pytest.mark.parametrize("value", [0, "0"])
    def test_recalc_hess_off_omits_line(self, tmp_path: Path, value: object) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(opt_recalc_hess=value), is_ts=False)
        assert "Recalc_Hess" not in blocks

    def test_scf_max_iter_500_reaches_scf_block(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(scf_max_iter=500), is_ts=False)
        assert "MaxIter 500" in _block(blocks, "%scf")

    def test_scf_convergence_tight_keyword_exactly_once(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(
            tmp_path, BatchMethodOptions(scf_convergence="tight"), is_ts=False
        )
        route_line = blocks.splitlines()[0]
        assert route_line.count("TightSCF") == 1

    def test_scf_strategy_slowconv_keyword_exactly_once(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(
            tmp_path, BatchMethodOptions(scf_strategy="slowconv"), is_ts=False
        )
        route_line = blocks.splitlines()[0]
        assert route_line.count("SlowConv") == 1

    def test_scf_strategy_soscf_keyword_exactly_once(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(scf_strategy="soscf"), is_ts=False)
        route_line = blocks.splitlines()[0]
        assert route_line.count("SOSCF") == 1

    def test_opt_convergence_verytight_reaches_route(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(
            tmp_path, BatchMethodOptions(opt_convergence="verytight"), is_ts=False
        )
        route_line = blocks.splitlines()[0]
        assert "VeryTightOpt" in route_line


# ── CLI flags → BatchMethodOptions ───────────────────────────────────────


class TestCliFlagRoundTrip:
    """Acceptance #3–#7 over the CLI seam."""

    def test_all_advanced_flags_reconstruct_options(self, tmp_path: Path) -> None:
        assert _options_from_cli(tmp_path, _ADVANCED_FLAGS) == _EXPECTED_ADVANCED

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("20", 20), ("auto", "auto"), ("0", 0)],
    )
    def test_recalc_hess_forms_normalized(self, tmp_path: Path, raw: str, expected: object) -> None:
        options = _options_from_cli(tmp_path, ["--opt-recalc-hess", raw])
        assert options.opt_recalc_hess == expected

    @pytest.mark.parametrize(
        ("flag", "expected"),
        [("--no-scf-orbital-inherit", False), ("--scf-orbital-inherit", True)],
    )
    def test_orbital_inherit_flags(self, tmp_path: Path, flag: str, expected: bool) -> None:
        options = _options_from_cli(tmp_path, [flag])
        assert options.scf_orbital_inherit is expected


# ── scheduler method dict → flags → CLI → options (serialization parity) ─


class TestSchedulerFlagParity:
    """Scheduler flag emission and CLI parsing reconstruct the same options."""

    def test_method_dict_round_trips_through_flags_and_cli(self, tmp_path: Path) -> None:
        from acp.scheduler.jobs import batchoptimize_method_flags

        flags = batchoptimize_method_flags(_ADVANCED_METHOD)
        assert _options_from_cli(tmp_path, flags) == _EXPECTED_ADVANCED

    def test_orbital_inherit_true_emits_no_flag_and_keeps_default(self, tmp_path: Path) -> None:
        from acp.scheduler.jobs import batchoptimize_method_flags

        method = {**_ADVANCED_METHOD, "scf_orbital_inherit": True}
        flags = batchoptimize_method_flags(method)
        assert "--no-scf-orbital-inherit" not in flags
        assert "--scf-orbital-inherit" not in flags
        assert _options_from_cli(tmp_path, flags).scf_orbital_inherit is True


# ── INT vs TS role defaults through the full .inp path ───────────────────


class TestRoleDefaultsThroughInputPath:
    """Acceptance #8: TS/INT role defaults are independent and .inp-visible."""

    def test_ts_defaults_render_role_default_lines(self, tmp_path: Path) -> None:
        geom = _block(_render_opt_input(tmp_path, BatchMethodOptions(), is_ts=True), "%geom")
        assert "Trust 0.3" in geom
        assert "Calc_Hess true" in geom
        assert "Recalc_Hess 5" in geom
        assert "MaxIter 200" in geom

    def test_int_defaults_omit_ts_role_lines(self, tmp_path: Path) -> None:
        blocks = _render_opt_input(tmp_path, BatchMethodOptions(), is_ts=False)
        geom = _block(blocks, "%geom")
        assert "Trust" not in geom
        assert "Calc_Hess" not in geom
        assert "Recalc_Hess" not in geom
        assert "MaxIter 200" in geom


# ── SCF trio reaches freq/SP request resources ───────────────────────────


class TestScfTrioFreqAndSpRequests:
    """The SCF trio is forwarded to frequency and single-point requests."""

    def test_frequency_request_carries_scf_trio(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(
            scf_max_iter=500,
            scf_convergence="verytight",
            scf_strategy="slowconv",
        )
        engine = _engine(tmp_path, methods)
        result = CalculationResult(coords=_COORDS.tolist())
        request = engine._build_freq_request(
            result, _ts_item(), 0, 1, tmp_path / "freq", _SYMBOLS, methods
        )
        assert request.resources["scf_maxiter"] == 500
        assert request.resources["scf_convergence"] == "verytight"
        assert request.resources["scf_strategy"] == "slowconv"

    def test_singlepoint_request_carries_scf_trio(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(
            scf_max_iter=600,
            scf_convergence="tight",
            scf_strategy="soscf",
        )
        engine = _engine(tmp_path, methods)
        result = CalculationResult(coords=_COORDS.tolist())
        request = engine._build_sp_request(
            result, _ts_item(), 0, 1, tmp_path / "sp", _SYMBOLS, methods
        )
        assert request.resources["scf_maxiter"] == 600
        assert request.resources["scf_convergence"] == "tight"
        assert request.resources["scf_strategy"] == "soscf"


# ── per-role overrides through the full .inp path (P2a) ──────────────────


class TestRoleOverridesThroughInputPath:
    """Acceptance #8: per-role overrides are independent and .inp-visible."""

    def test_ts_role_override_trust_radius_renders(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(transition_state_opt_trust_radius=0.15)
        geom = _block(_render_opt_input(tmp_path, methods, is_ts=True), "%geom")
        assert "Trust 0.15" in geom

    def test_ts_role_override_initial_hessian_renders(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(transition_state_opt_initial_hessian="model")
        geom = _block(_render_opt_input(tmp_path, methods, is_ts=True), "%geom")
        assert "Calc_Hess true" not in geom

    def test_ts_role_override_recalc_hess_renders(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(transition_state_opt_recalc_hess=3)
        geom = _block(_render_opt_input(tmp_path, methods, is_ts=True), "%geom")
        assert "Recalc_Hess 3" in geom

    def test_int_role_override_trust_radius_renders(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(minimum_opt_trust_radius=0.10)
        geom = _block(_render_opt_input(tmp_path, methods, is_ts=False), "%geom")
        assert "Trust 0.1" in geom

    def test_int_role_override_initial_hessian_renders(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(minimum_opt_initial_hessian="calculate")
        geom = _block(_render_opt_input(tmp_path, methods, is_ts=False), "%geom")
        assert "Calc_Hess true" in geom

    def test_int_role_override_recalc_hess_renders(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(minimum_opt_recalc_hess=2)
        geom = _block(_render_opt_input(tmp_path, methods, is_ts=False), "%geom")
        assert "Recalc_Hess 2" in geom

    def test_ts_override_does_not_affect_int(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(
            transition_state_opt_trust_radius=0.15,
            transition_state_opt_initial_hessian="model",
            transition_state_opt_recalc_hess=3,
        )
        int_geom = _block(_render_opt_input(tmp_path, methods, is_ts=False), "%geom")
        assert "Trust" not in int_geom
        assert "Calc_Hess" not in int_geom
        assert "Recalc_Hess" not in int_geom

    def test_int_override_does_not_affect_ts(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(
            minimum_opt_trust_radius=0.10,
            minimum_opt_initial_hessian="calculate",
            minimum_opt_recalc_hess=2,
        )
        ts_geom = _block(_render_opt_input(tmp_path, methods, is_ts=True), "%geom")
        assert "Trust 0.3" in ts_geom
        assert "Calc_Hess true" in ts_geom
        assert "Recalc_Hess 5" in ts_geom

    def test_role_override_beats_common_in_inp(self, tmp_path: Path) -> None:
        methods = BatchMethodOptions(
            opt_trust_radius=0.25,
            transition_state_opt_trust_radius=0.15,
        )
        ts_geom = _block(_render_opt_input(tmp_path, methods, is_ts=True), "%geom")
        assert "Trust 0.15" in ts_geom
        int_geom = _block(_render_opt_input(tmp_path, methods, is_ts=False), "%geom")
        assert "Trust 0.25" in int_geom

    def test_scheduler_flags_round_trip_to_role_options(self, tmp_path: Path) -> None:
        from acp.scheduler.jobs import batchoptimize_method_flags

        method = {
            "transition_state_opt_trust_radius": 0.15,
            "transition_state_opt_initial_hessian": "model",
            "transition_state_opt_recalc_hess": 3,
            "minimum_opt_trust_radius": 0.10,
            "minimum_opt_initial_hessian": "calculate",
            "minimum_opt_recalc_hess": 2,
        }
        flags = batchoptimize_method_flags(method)
        options = _options_from_cli(tmp_path, flags)
        assert options.transition_state_opt_trust_radius == 0.15
        assert options.transition_state_opt_initial_hessian == "model"
        assert options.transition_state_opt_recalc_hess == 3
        assert options.minimum_opt_trust_radius == 0.10
        assert options.minimum_opt_initial_hessian == "calculate"
        assert options.minimum_opt_recalc_hess == 2
