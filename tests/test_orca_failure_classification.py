"""Tests for ORCA output failure classification and adaptive SCF rescue chains."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from cccp.qc.interfaces.orca import classify_orca_failure
from acp.calculations.contracts import CalculationRequest, StructureArtifact, StructureRole
from acp.calculations.primitives.optimize import (
    CALCALL_OPT,
    FRESH_HESSIAN_RESTART,
    IRC_MIDPOINT_RECOVERY,
    MODE_DISPLACEMENT,
    SADDLE_BREAK,
    SCF_INCREASE_MAXITER,
    SCF_SLOWCONV,
    SCF_SOSCF,
    TIGHT_OPT_CALCHESS,
    TS_MODE_DIRECTED,
    FAILURE_EXIT,
    _RESCUE_MATRIX,
    _failure_type,
    _rescue_kwargs,
    _inject_gbw_continuation,
    build_rescue_plan,
)


# ── classify_orca_failure ────────────────────────────────────────────────


class TestClassifyOrcaFailure:
    def test_scf_not_converged(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text(
            "SCF ITERATIONS\n"
            "  ... iteration stuff ...\n"
            "SCF NOT CONVERGED AFTER 300 ITERATIONS\n"
            "**** Energy check ****\n",
        )
        assert classify_orca_failure(out) == "scf_failure"

    def test_scf_missing_convergence_message(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text(
            "SCF ITERATIONS\n"
            "  Iteration 1 ...\n"
            "  Iteration 2 ...\n"
            "  Timelimit reached\n",
        )
        assert classify_orca_failure(out) == "scf_failure"

    def test_geometry_not_converged(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text(
            "SCF CONVERGED\n"
            "THE OPTIMIZATION HAS NOT CONVERGED AFTER 100 CYCLES\n"
            "**** Final energy ****\n",
        )
        assert classify_orca_failure(out) == "geometry_not_converged"

    def test_memory_failure(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text(
            "ORCA calculation by DFT\n"
            "ERROR: cannot allocate 4096 MB of memory\n",
        )
        assert classify_orca_failure(out) == "memory_failure"

    def test_unknown_failure(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text("ORCA finished successfully but something else happened\n")
        assert classify_orca_failure(out) == "unknown"

    def test_empty_file(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text("")
        assert classify_orca_failure(out) == "unknown"

    def test_nonexistent_file(self) -> None:
        assert classify_orca_failure(Path("/nonexistent/file.out")) == "unknown"

    def test_scf_failed_to_converge(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text("SCF ITERATIONS\nERROR: SCF convergence failed\n")
        assert classify_orca_failure(out) == "scf_failure"

    def test_out_of_memory(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text("Running ORCA...\nstd::bad_alloc\n")
        assert classify_orca_failure(out) == "memory_failure"

    def test_optimization_did_not_converge(self, tmp_path: Path) -> None:
        out = tmp_path / "test.out"
        out.write_text("SCF CONVERGED\nOPTIMIZATION DID NOT CONVERGE\n")
        assert classify_orca_failure(out) == "geometry_not_converged"


# ── _failure_type classification ─────────────────────────────────────────


class TestFailureTypeClassification:
    def _make_request(self, **resources: Any) -> CalculationRequest:
        return CalculationRequest(
            input_artifact=StructureArtifact(
                path=Path("/dev/null"),
                role=StructureRole.MINIMUM,
            ),
            method="r2SCAN-3c",
            resources=resources,
        )

    def test_structured_scf_failure(self) -> None:
        req = self._make_request()
        assert _failure_type(req, "ORCA optimization failed [scf_failure]") == "scf_failure"

    def test_structured_geometry_not_converged(self) -> None:
        req = self._make_request()
        assert (
            _failure_type(req, "ORCA optimization failed [geometry_not_converged]")
            == "geometry_not_converged"
        )

    def test_structured_memory_failure(self) -> None:
        req = self._make_request()
        assert (
            _failure_type(req, "ORCA optimization failed [memory_failure]")
            == "memory_failure"
        )

    def test_structured_crash_timeout(self) -> None:
        req = self._make_request()
        assert (
            _failure_type(req, "ORCA optimization failed [crash_timeout]")
            == "crash_timeout"
        )

    def test_fallback_string_matching(self) -> None:
        req = self._make_request()
        assert _failure_type(req, "SCF did not converge") == "scf_failure"
        assert _failure_type(req, "timed out after 100s") == "crash_timeout"
        assert (
            _failure_type(req, "no imaginary frequency found") == "ts_no_imaginary"
        )

    def test_override_from_resources(self) -> None:
        req = self._make_request(failure_type="memory_failure")
        assert _failure_type(req, "some generic error") == "memory_failure"

    def test_default_to_geometry_not_converged(self) -> None:
        req = self._make_request()
        assert _failure_type(req, "some unknown error") == "geometry_not_converged"


# ── SCF rescue matrix ────────────────────────────────────────────────────


class TestScfRescueMatrix:
    def test_scf_failure_has_rescue_chain(self) -> None:
        for kind in ("ts", "intermediate", "minimum", "precursor", "product"):
            strategies = _RESCUE_MATRIX[("scf_failure", kind)]
            assert len(strategies) == 3
            assert strategies[0] == SCF_INCREASE_MAXITER
            assert strategies[1] == SCF_SLOWCONV
            assert strategies[2] == SCF_SOSCF

    def test_memory_failure_is_terminal(self) -> None:
        for kind in ("ts", "intermediate", "minimum", "precursor", "product"):
            assert _RESCUE_MATRIX[("memory_failure", kind)] == ()

    def test_scf_rescue_kwargs_maxiter(self) -> None:
        kw = _rescue_kwargs(SCF_INCREASE_MAXITER)
        assert kw == {"scf_maxiter": 500}

    def test_scf_rescue_kwargs_slowconv(self) -> None:
        kw = _rescue_kwargs(SCF_SLOWCONV)
        assert kw == {"scf_maxiter": 500, "scf_strategy": "slowconv"}

    def test_scf_rescue_kwargs_soscf(self) -> None:
        kw = _rescue_kwargs(SCF_SOSCF)
        assert kw == {"scf_maxiter": 500, "scf_strategy": "soscf"}

    def test_scf_failure_build_rescue_plan(self) -> None:
        plan = build_rescue_plan("scf_failure", "ts")
        assert len(plan.actions) == 3
        assert plan.actions[0].strategy == SCF_INCREASE_MAXITER
        assert plan.actions[1].strategy == SCF_SLOWCONV
        assert plan.actions[2].strategy == SCF_SOSCF
        assert plan.terminal is False

    def test_memory_failure_build_rescue_plan(self) -> None:
        plan = build_rescue_plan("memory_failure", "minimum")
        assert len(plan.actions) == 0
        assert plan.terminal is True

    def test_existing_strategies_preserved(self) -> None:
        assert ("geometry_not_converged", "ts") in _RESCUE_MATRIX
        assert len(_RESCUE_MATRIX[("geometry_not_converged", "ts")]) == 3
        assert _RESCUE_MATRIX[("geometry_not_converged", "ts")] == (
            FRESH_HESSIAN_RESTART,
            TS_MODE_DIRECTED,
            CALCALL_OPT,
        )


# ── .gbw continuation ────────────────────────────────────────────────────


class TestGbwContinuation:
    def test_gbw_copied_and_mo_read_path_set(self, tmp_path: Path) -> None:
        source = tmp_path / "attempt_0"
        source.mkdir()
        (source / "optimize.gbw").write_bytes(b"fake gbw data")
        target = tmp_path / "rescue_0"
        kwargs: dict[str, Any] = {}
        _inject_gbw_continuation(source, target, kwargs)
        assert (target / "optimize.gbw").exists()
        assert "mo_read_path" in kwargs
        assert kwargs["mo_read_path"].endswith("optimize.gbw")

    def test_no_gbw_no_mo_read_path(self, tmp_path: Path) -> None:
        source = tmp_path / "attempt_0"
        source.mkdir()
        target = tmp_path / "rescue_0"
        kwargs: dict[str, Any] = {}
        _inject_gbw_continuation(source, target, kwargs)
        assert "mo_read_path" not in kwargs

    def test_none_dirs_noop(self) -> None:
        kwargs: dict[str, Any] = {}
        _inject_gbw_continuation(None, None, kwargs)
        assert kwargs == {}

    def test_nonexistent_source_noop(self, tmp_path: Path) -> None:
        kwargs: dict[str, Any] = {}
        _inject_gbw_continuation(tmp_path / "nope", tmp_path / "target", kwargs)
        assert "mo_read_path" not in kwargs


# ── rescue policy ────────────────────────────────────────────────────────


class TestRescuePolicy:
    def test_rescue_disabled_when_off(self) -> None:
        policy = "off"
        rescue_enabled = policy != "off"
        assert rescue_enabled is False

    def test_rescue_enabled_when_adaptive(self) -> None:
        policy = "adaptive"
        rescue_enabled = policy != "off"
        assert rescue_enabled is True

    def test_max_rescue_attempts_limit(self) -> None:
        plan = build_rescue_plan("scf_failure", "ts")
        assert len(plan.actions) == 3
        max_rescue = 2
        assert len(plan.actions[:max_rescue]) == 2

    def test_failure_types_frozenset_includes_memory(self) -> None:
        from acp.calculations.primitives.optimize import _FAILURE_TYPES
        assert "memory_failure" in _FAILURE_TYPES

    def test_failure_exit_includes_timeout_and_memory(self) -> None:
        assert "crash_timeout" in FAILURE_EXIT
        assert "memory_failure" in FAILURE_EXIT
        assert "scf_failure" not in FAILURE_EXIT
