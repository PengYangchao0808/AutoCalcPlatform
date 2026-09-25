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
        block = ts_geom_block(
            "read", 0, 0.15, ts_mode=2, hess_file_name="source.hess"
        )
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
        source.write_text("$atoms\n1\nH 1.0\n", encoding="utf-8")
        return source

    def test_hess_staged_and_referenced(
        self, tmp_path, interface, hess_source, monkeypatch
    ):
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
        assert staged.read_text(encoding="utf-8") == hess_source.read_text(encoding="utf-8")
        text = captured["inp"]
        assert "InHess Read" in text
        assert 'InHessName "ts.hess"' in text
        assert "TS_Mode {M 1} end" in text
        assert "Calc_Hess true" not in text
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
