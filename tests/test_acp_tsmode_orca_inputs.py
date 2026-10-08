"""TS Mode input-generation and rescue-guard tests (plan §9, §10.1)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.orca_ts import ts_geom_block

COORDS = np.array([[0.0, 0.0, 0.0], [1.1, 0.0, 0.0], [0.0, 1.0, 0.0]])
SYMBOLS = ["H", "O", "H"]


class TestTsGeomBlockHessianRead:
    def test_hess_file_emits_inhess_read(self):
        block = ts_geom_block("read", 0, 0.15, ts_mode=2, hess_file_name="source.hess")
        assert "InHess Read" in block
        assert 'InHessName "source.hess"' in block
        assert "TS_Mode {M 2} end" in block
        assert "Calc_Hess true" not in block

    def test_hess_file_with_calculate_rejected(self):
        with pytest.raises(ValueError, match="read Hessian"):
            ts_geom_block("calculate", 5, 0.15, hess_file_name="source.hess")

    def test_no_calc_hess_when_reading(self):
        block = ts_geom_block("read", 0, 0.15, hess_file_name="x.hess")
        assert "Calc_Hess" not in block

    def test_ts_mode_int_and_bool(self):
        assert "TS_Mode {M 0} end" in ts_geom_block("model", 0, 0.1, ts_mode=True)
        assert "TS_Mode {M 3} end" in ts_geom_block("model", 0, 0.1, ts_mode=3)
        with pytest.raises(TypeError):
            ts_geom_block("model", 0, 0.1, ts_mode="3")
        with pytest.raises(ValueError):
            ts_geom_block("model", 0, 0.1, ts_mode=-1)


class TestTransitionStateOptHessStaging:
    @pytest.fixture()
    def interface(self):
        return ORCAInterface(config={}, method="r2SCAN-3c", basis="")

    @pytest.fixture()
    def hess_source(self, tmp_path):
        source = tmp_path / "upstream.hess"
        source.write_text(
            "$atoms\n1\nH 1.007 0.0 0.0 0.0\n$hessian\n1\n1.0\n$act_energy\n  -100.123456\n",
            encoding="utf-8",
        )
        return source

    def test_hess_staged_and_referenced(self, tmp_path, interface, hess_source, monkeypatch):
        captured = {}

        def fake_run_orca(inp, out, output_callback=None):
            captured["inp"] = Path(inp).read_text(encoding="utf-8")
            Path(out).write_text("", encoding="utf-8")
            return True

        monkeypatch.setattr(interface, "_run_orca", fake_run_orca)
        work = tmp_path / "work"
        result = interface.transition_state_opt(
            COORDS,
            SYMBOLS,
            output_dir=work,
            output_name="ts",
            initial_hessian="read",
            recalc_hess=0,
            ts_mode=1,
            hess_file=hess_source,
        )
        staged = work / "ts.hess"
        assert staged.is_file()
        # Byte-identical raw copy: unparsed sections ($act_energy, …) survive
        # staging — the Python-side matrix is never re-flattened.
        assert staged.read_bytes() == hess_source.read_bytes()
        assert b"$act_energy" in staged.read_bytes()
        text = captured["inp"]
        assert "InHess Read" in text
        assert 'InHessName "ts.hess"' in text
        assert "TS_Mode {M 1} end" in text
        assert "Calc_Hess true" not in text
        # The ORCA input references the staged file only — no $hessian
        # section or matrix values re-emitted into the input.
        assert "$hessian" not in text
        assert "$orca_hessian" not in text
        assert "$act_energy" not in text
        assert result.output_file is not None

    def test_calculate_with_hess_rejected(self, tmp_path, interface, hess_source):
        with pytest.raises(ValueError, match="cannot be combined"):
            interface.transition_state_opt(
                COORDS,
                SYMBOLS,
                output_dir=tmp_path,
                initial_hessian="calculate",
                hess_file=hess_source,
            )


class TestExplicitTargetRescueGuard:
    def test_run_optimize_preserves_explicit_target(self, tmp_path, monkeypatch):
        from acp.calculations.contracts import (
            CalculationRequest,
            StructureArtifact,
            StructureRole,
        )
        from acp.calculations.primitives import optimize as optimize_module

        attempts: list[dict[str, object]] = []

        class FailingBackend:
            def transition_state_opt(
                self, coordinates, symbols, charge=0, multiplicity=1, output_dir=None, **kwargs
            ):
                attempts.append(dict(kwargs))
                raise RuntimeError("optimization failed [geometry_not_converged]")

        monkeypatch.setattr(
            optimize_module, "backend_for_request", lambda req, name: FailingBackend()
        )

        request = CalculationRequest(
            input_artifact=StructureArtifact(
                path=tmp_path / "input.xyz",
                elements=list(SYMBOLS),
                role=StructureRole.TRANSITION_STATE,
                source="tsmode",
            ),
            method="r2SCAN-3c",
            resources={
                "backend": "orca",
                "coordinates": COORDS.tolist(),
                "symbols": list(SYMBOLS),
                "charge": 0,
                "multiplicity": 1,
                "structure_kind": "ts",
                "output_dir": str(tmp_path / "opt"),
                "ts_mode": 2,
                "initial_hessian": "read",
                "hess_file": str(tmp_path / "source.hess"),
                "opt_rescue_policy": "adaptive",
                "opt_max_rescue": 2,
            },
            workflow="tsmode",
        )
        result = optimize_module.run_optimize(request)

        assert result.status == "failed"
        assert attempts, "first attempt must run"
        assert attempts[0]["ts_mode"] == 2
        assert attempts[0]["hess_file"].endswith("source.hess")
        # geometry_not_converged + explicit target → terminal; no rescue that
        # would override ts_mode with the legacy bool (plan §10.1)
        rescue_kwargs = [kwargs for kwargs in attempts[1:]]
        assert all(kwargs.get("ts_mode") == 2 for kwargs in rescue_kwargs)
        assert result.metadata.get("tsmode_explicit_target") == 2
        assert result.metadata.get("rescue_terminal") is True
        assert result.metadata.get("rescue_actions") == []

    def test_scf_rescue_keeps_target(self, tmp_path, monkeypatch):
        from acp.calculations.contracts import (
            CalculationRequest,
            StructureArtifact,
            StructureRole,
        )
        from acp.calculations.primitives import optimize as optimize_module

        attempts: list[dict[str, object]] = []

        class ScfFailingBackend:
            def transition_state_opt(
                self, coordinates, symbols, charge=0, multiplicity=1, output_dir=None, **kwargs
            ):
                attempts.append(dict(kwargs))
                if len(attempts) == 1:
                    raise RuntimeError("SCF failure [scf_failure]")
                from acp.backends.base import QCResult

                return QCResult(
                    success=True,
                    energy=-76.0,
                    coordinates=np.asarray(COORDS),
                    symbols=list(SYMBOLS),
                    converged=True,
                )

        monkeypatch.setattr(
            optimize_module, "backend_for_request", lambda req, name: ScfFailingBackend()
        )
        request = CalculationRequest(
            input_artifact=StructureArtifact(
                path=tmp_path / "input.xyz",
                elements=list(SYMBOLS),
                role=StructureRole.TRANSITION_STATE,
                source="tsmode",
            ),
            method="r2SCAN-3c",
            resources={
                "backend": "orca",
                "coordinates": COORDS.tolist(),
                "symbols": list(SYMBOLS),
                "structure_kind": "ts",
                "output_dir": str(tmp_path / "opt"),
                "ts_mode": 1,
                "initial_hessian": "read",
            },
            workflow="tsmode",
        )
        result = optimize_module.run_optimize(request)
        assert result.status == "completed"
        assert len(attempts) == 2
        assert attempts[0]["ts_mode"] == 1
        assert attempts[1]["ts_mode"] == 1
        assert attempts[1]["scf_maxiter"] == 500
        assert result.metadata.get("rescue_attempts") == 1


def _tsmode_bundle(tmp_path, level):
    from acp.calculations.tsmode.source import load_bundle_from_files
    from tests.tsmode_synthetic import make_consistent_pair

    out_path, hess_path, _coords, freqs, modes = make_consistent_pair(tmp_path)
    bundle = load_bundle_from_files(out_path, hess_path, level=level)
    return bundle, freqs, modes


def _tsmode_request():
    from acp.calculations.tsmode.contracts import (
        TsmodeOptimizationSettings,
        TsmodeRequest,
    )

    return TsmodeRequest(
        source={"kind": "test"},
        source_mode_index=8,
        optimization=TsmodeOptimizationSettings(require_verified_mapping=False),
        request_id="req_render",
    )


def _route_lines(captured: list[str]) -> list[str]:
    return [line for text in captured for line in text.splitlines() if line.startswith("!")]


def _run_capture(tmp_path, monkeypatch, level, *, config=None, fail_first=False):
    from acp.calculations.tsmode.engine import TsmodeEngine
    from tests.tsmode_synthetic import write_out_file

    bundle, freqs, modes = _tsmode_bundle(tmp_path, level)
    optimized = np.asarray(bundle.coordinates_angstrom) + 0.02
    captured: list[str] = []
    calls = {"count": 0}

    def fake_run_orca(self, input_file, output_file, output_callback=None):
        captured.append(Path(input_file).read_text(encoding="utf-8"))
        calls["count"] += 1
        if fail_first and calls["count"] == 1:
            Path(output_file).write_text("SCF NOT CONVERGED\n", encoding="utf-8")
            return False
        write_out_file(Path(output_file), optimized, freqs, modes)
        return True

    monkeypatch.setattr("cccp.qc.interfaces.orca.ORCAInterface._run_orca", fake_run_orca)
    result = TsmodeEngine(config=config or {}).run(_tsmode_request(), bundle, tmp_path / "task")
    return result, captured


class TestSourceLevelRenderedIntoOrcaInput:
    def test_optimize_and_frequency_inputs_carry_source_level(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="D4")
        result, captured = _run_capture(tmp_path, monkeypatch, level)

        assert result.workflow_result.status == "completed"
        assert len(captured) == 2, captured
        for text in captured:
            assert "def2-TZVP" in text, text
            assert "D4" in text, text

    def test_successful_rescue_input_carries_source_level(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="D4")
        result, captured = _run_capture(tmp_path, monkeypatch, level, fail_first=True)

        assert result.workflow_result.status == "completed"
        assert len(captured) >= 3, captured
        for text in captured:
            assert "def2-TZVP" in text, text
            assert "D4" in text, text

    def test_source_level_wins_over_default_config(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="D4")
        config = {"theory": {"dft": {"basis": "def2-SVP", "dispersion": "D3BJ"}}}
        _result, captured = _run_capture(tmp_path, monkeypatch, level, config=config)

        for text in captured:
            assert "def2-TZVP" in text, text
            assert "D4" in text, text
            assert "ma-def2-SVP" not in text, text
            assert "D3BJ" not in text, text

    def test_explicit_none_dispersion_emits_no_token(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="none")
        _result, captured = _run_capture(tmp_path, monkeypatch, level)

        for text in captured:
            assert "def2-TZVP" in text, text
            assert "D4" not in text, text
            assert "D3BJ" not in text, text

    def test_composite_method_carries_no_fabricated_basis_or_dispersion(
        self, tmp_path, monkeypatch
    ):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="r2SCAN-3c", basis="")
        _result, captured = _run_capture(tmp_path, monkeypatch, level)

        for text in captured:
            assert "r2SCAN-3c" in text, text
            assert "D4" not in text, text
            assert "D3BJ" not in text, text
            assert "def2-" not in text.lower(), text

    def test_scf_canonical_token_completes_and_renders_once(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(
            method="PBE0", basis="def2-TZVP", dispersion="D4", scf="TightSCF"
        )
        result, captured = _run_capture(tmp_path, monkeypatch, level)

        assert result.workflow_result.status == "completed", result.report.attempts
        routes = _route_lines(captured)
        assert len(routes) == 2, routes
        for route in routes:
            assert route.count("TightSCF") == 1, route
            assert " tight" not in route, route

    def test_scf_domain_token_completes_and_renders_once(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", dispersion="D4", scf="tight")
        result, captured = _run_capture(tmp_path, monkeypatch, level)

        assert result.workflow_result.status == "completed", result.report.attempts
        routes = _route_lines(captured)
        assert len(routes) == 2, routes
        for route in routes:
            assert route.count("TightSCF") == 1, route
            assert " tight" not in route, route

    def test_scf_canonical_token_rescue_renders_once(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(
            method="PBE0", basis="def2-TZVP", dispersion="D4", scf="TightSCF"
        )
        result, captured = _run_capture(tmp_path, monkeypatch, level, fail_first=True)

        assert result.workflow_result.status == "completed", result.report.attempts
        routes = _route_lines(captured)
        assert len(routes) >= 3, routes
        for route in routes:
            assert route.count("TightSCF") == 1, route
            assert " tight" not in route, route

    def test_unknown_scf_token_is_not_silently_dropped(self, tmp_path, monkeypatch):
        from acp.calculations.tsmode.contracts import SourceLevelOfTheory

        level = SourceLevelOfTheory(method="PBE0", basis="def2-TZVP", scf="BogusSCF")
        result, captured = _run_capture(tmp_path, monkeypatch, level)

        assert result.workflow_result.status == "failed"
        assert captured == []
        errors = " ".join(
            str(error) for attempt in result.report.attempts for error in attempt.get("errors", [])
        )
        assert "BogusSCF" in errors, errors
