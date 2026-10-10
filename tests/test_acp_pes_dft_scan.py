# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
"""PES DFT-scan extension acceptance tests (plan ACP_PES_DFT_Scan_Extension_Plan §6).

Covers the first-phase gates:
- V-1/V-2: ORCA input emission for 3c composite and B3LYP levels;
- V-3: every scan-optimizer parameter reaches the backend call (G3/G4/G6);
- V-4: execution_mode + frame traceability on native and pointwise paths;
- V-5: legacy xtb-engine migration + schema defaults for old tasks;
- V-10: SP failures never backfill the single-point series with scan energies.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.calculations.levels import (
    CalculationLevel,
    LevelEntryContext,
    canonical_level,
    changed_level_fields,
    level_fingerprint,
    normalize_method_alias,
    resolve_level_for_entry,
    scan_optimization_methods,
    validate_level_for_purpose,
)
from acp.calculations.pes.contracts import (
    ScanCoordinate,
    ScanProtocol,
    SinglePointSpec,
    validate_scan_protocol,
)
from acp.calculations.pes.outputs import persist_pes_outputs
from acp.calculations.pes.scan import run_pes_scan
from cccp.qc.interfaces.base import QCResult
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult
from tests.conftest import FakeBackend

_ETHYLENE_XYZ = """6
ethylene
C   0.000000   0.000000   0.000000
C   1.339000   0.000000   0.000000
H  -0.506000   0.934000   0.000000
H  -0.506000  -0.934000   0.000000
H   1.845000   0.934000   0.000000
H   1.845000  -0.934000   0.000000
"""

_ETHYLENE_COORDS = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.339, 0.0, 0.0],
        [-0.506, 0.934, 0.0],
        [-0.506, -0.934, 0.0],
        [1.845, 0.934, 0.0],
        [1.845, -0.934, 0.0],
    ]
)
_ETHYLENE_SYMBOLS = ["C", "C", "H", "H", "H", "H"]


def _bare_orca() -> ORCAInterface:
    """ORCAInterface instance for pure input rendering (no executable)."""
    iface = ORCAInterface.__new__(ORCAInterface)
    iface.method = "GFN2-xTB"
    iface.basis = ""
    iface.solvent = None
    iface.solvent_model = "none"
    iface.maxcore = 1000
    iface.nproc = 1
    iface.mem_mb = iface.maxcore * iface.nproc
    iface._maxcore_safety = 0.8
    iface._maxcore_pin = iface.maxcore
    iface.charge = 0
    iface.multiplicity = 1
    iface.config = {}
    return iface


def _scan_point(
    index: int,
    target: float,
    *,
    energy: float | None = -1.0,
    success: bool = True,
    metadata: dict[str, Any] | None = None,
) -> RelaxedScanPoint:
    coords = _ETHYLENE_COORDS.copy()
    coords[1, 0] = coords[0, 0] + target
    return RelaxedScanPoint(
        frame_index=index,
        progress=index / 4.0,
        coordinates=coords if success else None,
        symbols=list(_ETHYLENE_SYMBOLS) if success else None,
        energy_hartree=energy if success else None,
        success=success,
        coordinate_values={"distance": target},
        metadata=metadata or {},
    )


def _fake_scan_result(n_points: int, output_dir: Path, **kwargs: Any) -> RelaxedScanResult:
    points = [
        _scan_point(i, 1.2 + i * (2.5 - 1.2) / max(n_points - 1, 1), **kwargs)
        for i in range(n_points)
    ]
    return RelaxedScanResult(
        points=points,
        input_xyz=output_dir / "input.xyz",
        scan_dir=output_dir,
        success=True,
    )


def _dft_request(scan_optimizer: dict[str, Any], n_points: int = 5) -> dict[str, Any]:
    return {
        "mode": "bond_length_scan",
        "source": {
            "source_type": "xyz_text",
            "xyz_text": _ETHYLENE_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "coordinate": {
            "kind": "distance",
            "atoms": [0, 1],
            "start": 1.2,
            "end": 2.5,
            "n_points": n_points,
        },
        "protocol": {
            "scan_optimizer": scan_optimizer,
            "single_point": {"enabled": False},
        },
    }


# ── levels.py shared model ─────────────────────────────────────────────


class TestCalculationLevels:
    def test_method_alias_normalization(self) -> None:
        assert normalize_method_alias("b973c") == "B97-3c"
        assert normalize_method_alias("R2SCAN-3C") == "r2SCAN-3c"
        assert normalize_method_alias("r2scan3c") == "r2SCAN-3c"
        assert normalize_method_alias("gfn2") == "GFN2-xTB"
        assert normalize_method_alias("gfnff") == "GFN-FF"
        assert normalize_method_alias("b3lyp") == "B3LYP"
        assert normalize_method_alias("Unknown-Method") == "Unknown-Method"

    def test_gfn0_alias_maps_to_gfn0_xtb(self) -> None:
        from acp.calculations.levels import _METHOD_ALIASES

        assert _METHOD_ALIASES["gfn0"] == "GFN0-xTB"
        assert normalize_method_alias("gfn0") == "GFN0-xTB"
        assert normalize_method_alias("GFN0") == "GFN0-xTB"
        assert normalize_method_alias("gfn-ff") == "GFN-FF"

    def test_canonical_level_locks_composite_3c(self) -> None:
        level = canonical_level(
            CalculationLevel(
                method="B97-3c",
                basis="def2-SVP",
                dispersion="D4",
                ri_approximation="RIJCOSX",
            )
        )
        assert level.basis is None
        assert level.dispersion is None
        assert level.ri_approximation == "none"
        assert level.aux_j_basis is None

    def test_canonical_level_clears_gfn_basis_dispersion_ri(self) -> None:
        for method in ("GFN2-xTB", "GFN1-xTB", "GFN0-xTB", "GFN-FF", "gfn2"):
            level = canonical_level(
                CalculationLevel(
                    method=method,
                    basis="def2-SVP",
                    dispersion="D4",
                    ri_approximation="RIJCOSX",
                    aux_j_basis="def2/J",
                )
            )
            assert level.basis is None, method
            assert level.dispersion is None, method
            assert level.ri_approximation == "none", method
            assert level.aux_j_basis is None, method

    def test_canonical_level_applies_basis_inline_defaults(self) -> None:
        level = canonical_level(CalculationLevel(method="B3LYP"))
        assert level.basis == "def2-TZVPP"
        assert level.dispersion == "D4"

    def test_solvent_none_is_explicit_and_never_inherited(self) -> None:
        level = canonical_level(
            CalculationLevel(method="B3LYP", solvent_model=None or "none", solvent="water")
        )
        assert level.solvent_model == "none"
        assert level.solvent is None

    def test_validate_rejects_non_scan_capable_method(self) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="wB97M-V"), purpose="scan_optimization"
        )
        assert any("scan_optimization" in error for error in errors)

    def test_validate_rejects_composite_with_explicit_basis(self) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="r2SCAN-3c", basis="def2-SVP"),
            purpose="scan_optimization",
        )
        assert any("built-in basis" in error for error in errors)

    def test_validate_requires_solvent_name_with_model(self) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="B3LYP", solvent_model="smd"),
            purpose="scan_optimization",
        )
        assert any("solvent is required" in error for error in errors)

    def test_scan_method_list_is_capability_filtered(self) -> None:
        methods = scan_optimization_methods()
        assert "B97-3c" in methods and "r2SCAN-3c" in methods
        assert "B3LYP" in methods and "GFN2-xTB" in methods
        assert "DLPNO-CCSD(T)" not in methods
        assert "wB97M-V" not in methods

    def test_fingerprint_is_canonical_and_stable(self) -> None:
        a = level_fingerprint(CalculationLevel(method="b973c", basis="def2-SVP"))
        b = level_fingerprint(CalculationLevel(method="B97-3c"))
        c = level_fingerprint(CalculationLevel(method="B3LYP"))
        assert a == b  # alias + composite locking converge
        assert a != c
        assert len(a) == 16


# ── Q7/Q8: GFN + single_point validation (T13) ────────────────────────


class TestGfnLevelValidation:
    @pytest.mark.parametrize("method", ["GFN2-xTB", "GFN1-xTB", "GFN-FF", "gfn2"])
    def test_gfn_with_explicit_basis_raises(self, method: str) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method=method, basis="def2-SVP"), purpose="scan_optimization"
        )
        assert any("GFN" in error and "basis" in error for error in errors), errors

    @pytest.mark.parametrize("method", ["GFN2-xTB", "GFN1-xTB", "GFN-FF"])
    def test_gfn_with_explicit_dispersion_or_ri_raises(self, method: str) -> None:
        for override in (
            {"dispersion": "D4"},
            {"ri_approximation": "RIJCOSX"},
            {"aux_j_basis": "def2/J"},
        ):
            errors = validate_level_for_purpose(
                CalculationLevel(method=method, **override), purpose="scan_optimization"
            )
            assert errors, (method, override)

    @pytest.mark.parametrize("method", ["GFN2-xTB", "GFN1-xTB", "GFN-FF"])
    def test_gfn_with_alpb_passes(self, method: str) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method=method, solvent_model="alpb", solvent="water"),
            purpose="scan_optimization",
        )
        assert errors == [], errors

    @pytest.mark.parametrize("model", ["smd", "cpcm", "gbsa"])
    def test_gfn_rejects_non_alpb_solvent_models(self, model: str) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="GFN2-xTB", solvent_model=model, solvent="water"),
            purpose="scan_optimization",
        )
        assert errors and "ALPB" in errors[0], errors

    def test_gfn0_rejected_on_orca_path_with_three_level_record(self) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="GFN0-xTB"), purpose="scan_optimization"
        )
        assert errors, errors
        message = errors[0]
        assert "capability" in message and "dependency" in message and "policy" in message
        assert "param_gfn0-xtb.txt" in message
        assert "xTB" in message

    def test_gfnff_offer_stands_on_orca_path(self) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="GFN-FF", solvent_model="ALPB", solvent="toluene"),
            purpose="scan_optimization",
        )
        assert errors == [], errors

    def test_single_point_purpose_validates_level(self) -> None:
        errors = validate_level_for_purpose(
            CalculationLevel(method="GFN2-xTB", basis="def2-SVP"), purpose="single_point"
        )
        assert any("GFN" in error for error in errors), errors
        errors = validate_level_for_purpose(
            CalculationLevel(method="GFN0-xTB"), purpose="single_point"
        )
        assert any("policy" in error for error in errors), errors
        assert validate_level_for_purpose(CalculationLevel(method="B97-3c"), "single_point") == []
        assert (
            validate_level_for_purpose(
                CalculationLevel(method="GFN2-xTB", solvent_model="alpb", solvent="water"),
                "single_point",
            )
            == []
        )
        with pytest.raises(ValueError, match="purpose"):
            validate_level_for_purpose(CalculationLevel(method="B3LYP"), "unknown_purpose")


class TestHistoricalMigrationLane:
    def test_canonical_level_warns_on_gfn_clear(self) -> None:
        warnings: list[str] = []
        level = canonical_level(
            CalculationLevel(
                method="GFN2-xTB",
                basis="def2-SVP",
                dispersion="D4",
                ri_approximation="RIJCOSX",
            ),
            warnings=warnings,
        )
        assert level.basis is None
        assert level.dispersion is None
        assert level.ri_approximation == "none"
        assert len(warnings) == 3, warnings
        assert all("GFN" in message for message in warnings)

    @pytest.mark.parametrize("model", ["cpcm", "smd", "gbsa"])
    def test_canonical_level_migrates_solvent_model_with_warning(self, model: str) -> None:
        warnings: list[str] = []
        level = canonical_level(
            CalculationLevel(method="GFN2-xTB", solvent_model=model, solvent="water"),
            warnings=warnings,
        )
        assert level.solvent_model == "alpb"
        assert level.solvent == "water"
        assert warnings and "alpb" in warnings[0]

    def test_migration_context_normalizes_and_warns(self) -> None:
        result = resolve_level_for_entry(
            CalculationLevel(
                method="GFN2-xTB", basis="def2-SVP", solvent_model="cpcm", solvent="water"
            ),
            purpose="scan_optimization",
            context=LevelEntryContext.MIGRATION,
        )
        assert result.errors == [], result.errors
        assert result.level.basis is None
        assert result.level.solvent_model == "alpb"
        assert len(result.warnings) == 2
        assert set(result.normalized_fields) == {"basis", "solvent_model"}

    def test_strict_context_rejects_same_config(self) -> None:
        result = resolve_level_for_entry(
            CalculationLevel(
                method="GFN2-xTB", basis="def2-SVP", solvent_model="smd", solvent="water"
            ),
            purpose="scan_optimization",
            context=LevelEntryContext.STRICT,
        )
        assert len(result.errors) == 2, result.errors

    def test_edit_context_is_strict_per_changed_field_only(self) -> None:
        raw = CalculationLevel(
            method="GFN2-xTB", basis="def2-SVP", solvent_model="cpcm", solvent="water"
        )
        changed_only_basis = resolve_level_for_entry(
            raw,
            purpose="scan_optimization",
            context=LevelEntryContext.EDIT,
            changed_fields={"basis"},
        )
        assert len(changed_only_basis.errors) == 1
        assert "basis" in changed_only_basis.errors[0]
        assert changed_only_basis.normalized_fields == ["solvent_model"]

        nothing_changed = resolve_level_for_entry(
            raw,
            purpose="scan_optimization",
            context=LevelEntryContext.EDIT,
            changed_fields=set(),
        )
        assert nothing_changed.errors == []
        assert set(nothing_changed.normalized_fields) == {"basis", "solvent_model"}

        unknown_baseline = resolve_level_for_entry(
            raw,
            purpose="scan_optimization",
            context=LevelEntryContext.EDIT,
            changed_fields=None,
        )
        assert unknown_baseline.errors

    def test_composite_3c_never_migrates(self) -> None:
        result = resolve_level_for_entry(
            CalculationLevel(method="B97-3c", basis="def2-SVP"),
            purpose="scan_optimization",
            context=LevelEntryContext.MIGRATION,
        )
        assert any("built-in basis" in error for error in result.errors), result.errors

    def test_changed_level_fields_value_semantics(self) -> None:
        baseline = CalculationLevel(method="B97-3c", basis="def2-SVP")
        assert changed_level_fields(baseline, baseline) == set()
        assert changed_level_fields(
            CalculationLevel(method="b973c", basis="def2-SVP"), baseline
        ) == set()
        assert changed_level_fields(
            CalculationLevel(method="B97-3c", basis=""), baseline
        ) == {"basis"}
        assert changed_level_fields(CalculationLevel(method="B3LYP"), baseline) == {
            "method",
            "basis",
        }


class TestSinglePointLevelEndToEnd:
    def test_validate_scan_protocol_rejects_invalid_sp_level(self) -> None:
        coordinate = ScanCoordinate(kind="distance", atoms=(0, 1), start=1.0, end=3.0, n_points=21)
        protocol_payload: dict[str, Any] = {
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.0,
                "end": 3.0,
                "n_points": 21,
            },
            "scan_optimizer": {"method": "GFN2-xTB"},
        }
        protocol = ScanProtocol.from_dict(
            dict(
                protocol_payload,
                single_point={"enabled": True, "method": "GFN2-xTB", "basis": "def2-SVP"},
            )
        )
        with pytest.raises(ValueError, match="single_point level"):
            validate_scan_protocol(coordinate, protocol)
        protocol = ScanProtocol.from_dict(
            dict(protocol_payload, single_point={"enabled": True, "method": "GFN0-xTB"})
        )
        with pytest.raises(ValueError, match="policy"):
            validate_scan_protocol(coordinate, protocol)

    def test_single_point_level_passes_through_canonical_level(self) -> None:
        from acp.calculations.pes.scan import single_point_level

        warnings: list[str] = []
        level = single_point_level(
            SinglePointSpec(method="gfn2", basis="def2-SVP"), warnings=warnings
        )
        assert level.method == "GFN2-xTB"
        assert level.basis is None
        assert warnings

    def test_run_single_points_forwards_canonical_sp_level(
        self, fake_backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, Any] = {}

        class _SpyExecutor:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

            def run(self) -> Any:
                from acp.calculations.batch._singlepoint_models import (
                    BatchSinglePointFrameResult,
                    BatchSinglePointResult,
                )

                return BatchSinglePointResult(
                    {
                        frame_id: BatchSinglePointFrameResult(
                            frame_id=frame_id,
                            energy_hartree=-1.0,
                            status="completed",
                            cache_key="",
                        )
                        for frame_id in captured["frame_ids"]
                    }
                )

        monkeypatch.setattr("acp.calculations.pes.scan.BatchSinglePointExecutor", _SpyExecutor)
        fake_backend.set_result("relaxed_scan", _fake_scan_result(3, tmp_path))
        request = _dft_request({"method": "GFN2-xTB"}, n_points=3)
        request["protocol"]["single_point"] = {
            "enabled": True,
            "method": "gfn2",
            "solvent_model": "none",
            "solvent": "water",
        }
        run_pes_scan(request=request, output_dir=tmp_path)
        assert captured["method"] == "GFN2-xTB"
        assert captured["solvent"] is None
        assert captured["solvent_model"] == "none"


class TestBondScanEntryContext:
    """_prepare_bond_scan_input threads the entry context explicitly."""

    @staticmethod
    def _job_input(protocol: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": {
                "source_type": "xyz_text",
                "xyz_text": _ETHYLENE_XYZ,
                "charge": 0,
                "multiplicity": 1,
            },
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.2,
                "end": 2.5,
                "n_points": 5,
            },
            "protocol": protocol,
        }

    def test_strict_submission_rejects_gfn_basis(self) -> None:
        from fastapi import HTTPException

        from acp.api.v1_routes import _prepare_bond_scan_input

        inp = self._job_input({"scan_optimizer": {"method": "GFN2-xTB", "basis": "def2-SVP"}})
        with pytest.raises(HTTPException) as excinfo:
            _prepare_bond_scan_input(inp, object(), entry="strict")
        assert excinfo.value.status_code == 422
        assert "basis" in str(excinfo.value.detail)

    def test_strict_submission_rejects_gfn_solvent_model_and_gfn0(self) -> None:
        from fastapi import HTTPException

        from acp.api.v1_routes import _prepare_bond_scan_input

        inp = self._job_input(
            {"scan_optimizer": {"method": "GFN2-xTB", "solvent_model": "gbsa", "solvent": "water"}}
        )
        with pytest.raises(HTTPException) as excinfo:
            _prepare_bond_scan_input(inp, object(), entry="strict")
        assert "ALPB" in str(excinfo.value.detail)

        inp = self._job_input({"scan_optimizer": {"method": "gfn0"}})
        with pytest.raises(HTTPException) as excinfo:
            _prepare_bond_scan_input(inp, object(), entry="strict")
        assert "policy" in str(excinfo.value.detail)

    def test_strict_submission_validates_single_point_level(self) -> None:
        from fastapi import HTTPException

        from acp.api.v1_routes import _prepare_bond_scan_input

        inp = self._job_input(
            {"single_point": {"enabled": True, "method": "GFN2-xTB", "basis": "def2-SVP"}}
        )
        with pytest.raises(HTTPException) as excinfo:
            _prepare_bond_scan_input(inp, object(), entry="strict")
        assert "single_point level" in str(excinfo.value.detail)

    def test_strict_clean_payload_is_byte_identical(self) -> None:
        from acp.api.v1_routes import _prepare_bond_scan_input

        protocol: dict[str, Any] = {
            "scan_optimizer": {"method": "GFN2-xTB", "solvent_model": "ALPB", "solvent": "water"},
            "single_point": {"enabled": True, "method": "B97-3c"},
        }
        prepared = _prepare_bond_scan_input(self._job_input(protocol), object(), entry="strict")
        assert prepared["scan_request"]["protocol"] == protocol
        assert "level_warnings" not in prepared["scan_request"]

    def test_edit_unchanged_fields_migrate_with_warnings(self) -> None:
        from acp.api.v1_routes import _prepare_bond_scan_input

        historical = {
            "scan_optimizer": {
                "method": "GFN2-xTB",
                "basis": "def2-SVP",
                "solvent_model": "cpcm",
                "solvent": "water",
            }
        }
        prepared = _prepare_bond_scan_input(
            self._job_input(historical),
            object(),
            entry="edit",
            baseline_protocol=historical,
        )
        scan_optimizer = prepared["scan_request"]["protocol"]["scan_optimizer"]
        assert scan_optimizer["basis"] is None
        assert scan_optimizer["solvent_model"] == "alpb"
        assert scan_optimizer["solvent"] == "water"
        warnings = prepared["scan_request"]["level_warnings"]
        assert len(warnings) == 2
        assert all("historical" in message for message in warnings)

    def test_edit_changed_field_is_strict(self) -> None:
        from fastapi import HTTPException

        from acp.api.v1_routes import _prepare_bond_scan_input

        baseline = {"scan_optimizer": {"method": "GFN2-xTB", "basis": "def2-SVP"}}
        edited = {"scan_optimizer": {"method": "GFN2-xTB", "basis": "ma-def2-SVP"}}
        with pytest.raises(HTTPException) as excinfo:
            _prepare_bond_scan_input(
                self._job_input(edited),
                object(),
                entry="edit",
                baseline_protocol=baseline,
            )
        assert "basis" in str(excinfo.value.detail)


# ── V-1 / V-2: ORCA input emission ─────────────────────────────────────


class TestOrcaInputEmission:
    def test_v1_composite_3c_has_no_basis_dispersion_ri(self) -> None:
        iface = _bare_orca()
        for method in ("r2SCAN-3c", "B97-3c"):
            inp, _ = iface._build_input_blocks(
                "opt",
                method=method,
                basis=None,
                recalc_hess=0,
                geom_maxiter=250,
                opt_level="tight",
                scf_convergence="tight",
                scf_maxiter=200,
                grid="DefGrid2",
                dispersion="D3BJ",  # must be stripped for composite methods
                aux_j_basis="def2/J",
            )
            route = inp.splitlines()[0]
            assert route.startswith(f"! {method} Opt"), route
            assert "def2" not in route and "mTZVP" not in route
            assert "D3" not in route and "D4" not in route
            assert "%basis" not in inp and "auxJ" not in inp and "auxC" not in inp
            assert "TightOpt" in route and "TightSCF" in route and "DefGrid2" in route
            assert "MaxIter 250" in inp

    def test_v2_b3lyp_full_level_enters_input(self) -> None:
        iface = _bare_orca()
        inp, _ = iface._build_input_blocks(
            "opt",
            method="B3LYP",
            basis="def2-SVP",
            recalc_hess=0,
            geom_maxiter=250,
            opt_level="very_tight",
            scf_convergence="tight",
            scf_maxiter=200,
            grid="DefGrid2",
            dispersion="D3BJ",
            solvent="water",
            solvent_model="smd",
        )
        route = inp.splitlines()[0]
        assert "B3LYP" in route and "def2-SVP" in route and "Opt" in route
        assert "D3BJ" in route and "DefGrid2" in route
        assert "VeryTightOpt" in route and "TightSCF" in route
        assert "%geom" in inp and "MaxIter 250" in inp
        assert "%scf" in inp and "MaxIter 200" in inp
        assert "smd true" in inp and 'SMDsolvent "Water"' in inp
        assert "%maxcore" in inp and "%pal nprocs" in inp

    def test_gfn_method_ignores_grid_and_dispersion(self) -> None:
        iface = _bare_orca()
        inp, _ = iface._build_input_blocks(
            "opt", method="GFN2-xTB", recalc_hess=0, grid="DefGrid2", dispersion="D4"
        )
        route = inp.splitlines()[0]
        assert "DefGrid" not in route and "D4" not in route

    def test_single_point_forwards_grid_and_dispersion(self) -> None:
        """G6 spillover: the SP chain consumes the same named parameters."""
        iface = _bare_orca()
        inp, _ = iface._build_input_blocks(
            "sp", method="B3LYP", basis="def2-TZVP", grid="defgrid3", dispersion="d4"
        )
        route = inp.splitlines()[0]
        assert "DefGrid3" in route and "D4" in route


# ── V-3: parameters reach the backend call ─────────────────────────────


class TestParameterFlow:
    def test_v3_scan_level_kwargs_reach_backend(
        self, fake_backend: FakeBackend, tmp_path: Path
    ) -> None:
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path))
        result = run_pes_scan(
            request=_dft_request(
                {
                    "method": "b3lyp",
                    "basis": "def2-SVP",
                    "dispersion": "D3BJ",
                    "solvent_model": "smd",
                    "solvent": "water",
                    "grid": "DefGrid2",
                    "scf_convergence": "tight",
                    "scf_max_iterations": 300,
                    "convergence": "tight",
                    "retry_count": 3,
                    "retry_strategy": "looser_convergence",
                    "max_iterations": 150,
                }
            ),
            output_dir=tmp_path,
            config={"resources": {"nproc": 2}},
        )
        scan_calls = [call for call in fake_backend.calls if call.method == "relaxed_scan"]
        assert len(scan_calls) == 1
        kwargs = scan_calls[0].kwargs
        assert kwargs["method"] == "B3LYP"  # alias normalised
        assert kwargs["basis"] == "def2-SVP"
        assert kwargs["dispersion"] == "D3BJ"
        assert kwargs["solvent"] == "water"
        assert kwargs["solvent_model"] == "smd"
        assert kwargs["grid"] == "DefGrid2"
        assert kwargs["scf_convergence"] == "tight"
        assert kwargs["scf_maxiter"] == 300
        assert kwargs["opt_level"] == "tight"  # G3: convergence wired
        assert kwargs["geom_maxiter"] == 150
        assert kwargs["retry_count"] == 3  # G4
        assert kwargs["retry_strategy"] == "looser_convergence"
        assert kwargs["failure_policy"] == "retry_previous"
        assert result["execution_mode"] == "native_scan"

    def test_v3_composite_method_locked_before_backend(
        self, fake_backend: FakeBackend, tmp_path: Path
    ) -> None:
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path))
        run_pes_scan(
            request=_dft_request({"method": "r2SCAN-3c", "grid": "DefGrid3"}),
            output_dir=tmp_path,
        )
        kwargs = [c for c in fake_backend.calls if c.method == "relaxed_scan"][0].kwargs
        assert kwargs["method"] == "r2SCAN-3c"
        assert kwargs["basis"] is None
        assert kwargs["dispersion"] is None
        assert kwargs["grid"] == "DefGrid3"

    def test_v3_single_point_receives_solvent(
        self, fake_backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G5: SinglePointSpec.solvent must reach BatchSinglePointExecutor."""
        captured: dict[str, Any] = {}

        class _SpyExecutor:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

            def run(self) -> Any:
                from acp.calculations.batch._singlepoint_models import (
                    BatchSinglePointFrameResult,
                    BatchSinglePointResult,
                )

                return BatchSinglePointResult(
                    {
                        frame_id: BatchSinglePointFrameResult(
                            frame_id=frame_id,
                            energy_hartree=-1.0,
                            status="completed",
                            cache_key="",
                        )
                        for frame_id in captured["frame_ids"]
                    }
                )

        monkeypatch.setattr(
            "acp.calculations.pes.scan.BatchSinglePointExecutor", _SpyExecutor
        )
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path))
        request = _dft_request({"method": "GFN2-xTB"})
        request["protocol"]["single_point"] = {
            "enabled": True,
            "method": "B3LYP",
            "basis": "def2-TZVP",
            "solvent_model": "smd",
            "solvent": "water",
        }
        run_pes_scan(request=request, output_dir=tmp_path)
        assert captured["solvent"] == "water"
        assert captured["solvent_model"] == "smd"
        assert captured["cache_profile"].startswith("pes_scan:")

    def test_sp_cache_scope_changes_with_scan_level(
        self, fake_backend: FakeBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captures: list[dict[str, Any]] = []

        class _SpyExecutor:
            def __init__(self, **kwargs: Any) -> None:
                captures.append(kwargs)

            def run(self) -> Any:
                from acp.calculations.batch._singlepoint_models import (
                    BatchSinglePointFrameResult,
                    BatchSinglePointResult,
                )

                return BatchSinglePointResult(
                    {
                        frame_id: BatchSinglePointFrameResult(
                            frame_id=frame_id,
                            energy_hartree=-1.0,
                            status="completed",
                            cache_key="",
                        )
                        for frame_id in captures[-1]["frame_ids"]
                    }
                )

        monkeypatch.setattr("acp.calculations.pes.scan.BatchSinglePointExecutor", _SpyExecutor)
        for index, method in enumerate(("GFN2-xTB", "B97-3c")):
            fake_backend.set_result(
                "relaxed_scan", _fake_scan_result(3, tmp_path / f"run{index}")
            )
            request = _dft_request({"method": method}, n_points=3)
            request["protocol"]["single_point"] = {"enabled": True, "method": "B97-3c"}
            run_pes_scan(request=request, output_dir=tmp_path / f"run{index}")
        profiles = [capture["cache_profile"] for capture in captures]
        assert profiles[0] != profiles[1]

# ── V-4: execution modes + frame traceability ──────────────────────────


class TestExecutionModes:
    def test_native_mode_frames_carry_level_and_empty_retry_history(
        self, fake_backend: FakeBackend, tmp_path: Path
    ) -> None:
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path))
        result = run_pes_scan(
            request=_dft_request({"method": "r2SCAN-3c", "convergence": "tight"}),
            output_dir=tmp_path,
        )
        assert result["execution_mode"] == "native_scan"
        assert result["protocol"]["execution_mode"] == "native_scan"
        assert result["optimization_level"]["method"] == "r2SCAN-3c"
        assert len(result["optimization_level_fingerprint"]) == 16
        frame = result["frames"][0]
        assert frame["optimizer_level"]["method"] == "r2SCAN-3c"
        assert frame["optimizer_engine"] == "orca"
        assert frame["retry_history"] == []
        assert frame["scf_converged"] is None

    def test_pointwise_mode_marks_execution_mode(
        self, fake_backend: FakeBackend, tmp_path: Path
    ) -> None:
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path))
        request = _dft_request({"method": "GFN2-xTB"})
        request["coordinates"] = [
            {"kind": "distance", "atoms": [0, 2], "start": 1.0, "end": 2.0, "n_points": 5},
            {"kind": "distance", "atoms": [1, 4], "start": 1.0, "end": 2.0, "n_points": 5},
        ]
        result = run_pes_scan(request=request, output_dir=tmp_path)
        assert result["execution_mode"] == "pointwise"

    def test_pointwise_retry_history_recorded(self, tmp_path: Path) -> None:
        """ORCAInterface._run_synchronous_relaxed_scan retries per point."""
        iface = _bare_orca()
        attempts: list[int] = []

        def fake_constrained_optimize(coordinates, symbols, constraints, **kwargs):  # noqa: ANN001, ANN202
            attempts.append(kwargs.get("output_name", ""))
            if len(attempts) == 1:
                return QCResult(
                    success=False,
                    error_message="ORCA constrained optimization failed [geometry_not_converged]",
                    log_file=tmp_path / "missing.out",
                )
            coords = np.asarray(coordinates, dtype=float)
            return QCResult(
                success=True,
                energy=-1.0,
                coordinates=coords,
                symbols=list(symbols),
                converged=True,
            )

        iface.constrained_optimize = fake_constrained_optimize  # type: ignore[method-assign]
        plan = ReactionCoordinatePlan(
            coordinates=(
                CoordinateSpec(
                    id="distance", kind="distance", atoms=(0, 1), start=1.2, end=2.0
                ),
                CoordinateSpec(
                    id="coordinate_2", kind="distance", atoms=(2, 3), start=1.2, end=2.0
                ),
            ),
            points=2,
        )
        result = iface._run_synchronous_relaxed_scan(
            _ETHYLENE_COORDS,
            _ETHYLENE_SYMBOLS,
            plan,
            charge=0,
            multiplicity=1,
            output_dir=tmp_path,
            output_name="scan",
            method="B97-3c",
            basis=None,
            solvent=None,
            solvent_model="none",
            geom_maxiter=100,
            recalc_hess=0,
            route_extras=None,
            retry_count=2,
            retry_strategy="previous_geometry",
            failure_policy="abort",
        )
        assert result.success is True
        assert len(attempts) == 3  # point 0 fails once + retry, point 1 succeeds at once
        first_point = result.points[0]
        assert first_point.success is True
        history = first_point.metadata["retry_history"]
        assert history[0]["failure_class"] is not None
        assert history[1]["failure_class"] is None
        assert first_point.metadata["scf_converged"] is True

    def test_pointwise_mark_failed_continue_records_failed_frame(self, tmp_path: Path) -> None:
        iface = _bare_orca()

        def always_fail(coordinates, symbols, constraints, **kwargs):  # noqa: ANN001, ANN202
            return QCResult(
                success=False,
                error_message="ORCA constrained optimization failed [scf_failure]",
                log_file=tmp_path / "missing.out",
            )

        iface.constrained_optimize = always_fail  # type: ignore[method-assign]
        plan = ReactionCoordinatePlan(
            coordinates=(
                CoordinateSpec(
                    id="distance", kind="distance", atoms=(0, 1), start=1.2, end=2.0
                ),
                CoordinateSpec(
                    id="coordinate_2", kind="distance", atoms=(2, 3), start=1.2, end=2.0
                ),
            ),
            points=3,
        )
        result = iface._run_synchronous_relaxed_scan(
            _ETHYLENE_COORDS,
            _ETHYLENE_SYMBOLS,
            plan,
            charge=0,
            multiplicity=1,
            output_dir=tmp_path,
            output_name="scan",
            method="B97-3c",
            basis=None,
            solvent=None,
            solvent_model="none",
            geom_maxiter=100,
            recalc_hess=0,
            route_extras=None,
            retry_count=1,
            retry_strategy="previous_geometry",
            failure_policy="mark_failed_continue",
        )
        assert result.success is True
        assert len(result.points) == 3  # all frames attempted despite failures
        assert all(not point.success for point in result.points)

    def test_pointwise_abort_stops_at_first_failure(self, tmp_path: Path) -> None:
        iface = _bare_orca()

        def always_fail(coordinates, symbols, constraints, **kwargs):  # noqa: ANN001, ANN202
            return QCResult(
                success=False,
                error_message="ORCA constrained optimization failed [unknown]",
                log_file=None,
            )

        iface.constrained_optimize = always_fail  # type: ignore[method-assign]
        plan = ReactionCoordinatePlan(
            coordinates=(
                CoordinateSpec(
                    id="distance", kind="distance", atoms=(0, 1), start=1.2, end=2.0
                ),
                CoordinateSpec(
                    id="coordinate_2", kind="distance", atoms=(2, 3), start=1.2, end=2.0
                ),
            ),
            points=3,
        )
        result = iface._run_synchronous_relaxed_scan(
            _ETHYLENE_COORDS,
            _ETHYLENE_SYMBOLS,
            plan,
            charge=0,
            multiplicity=1,
            output_dir=tmp_path,
            output_name="scan",
            method="B97-3c",
            basis=None,
            solvent=None,
            solvent_model="none",
            geom_maxiter=100,
            recalc_hess=0,
            route_extras=None,
            retry_count=0,
            retry_strategy="previous_geometry",
            failure_policy="abort",
        )
        assert result.success is False
        assert len(result.points) == 1


# ── candidate gating ───────────────────────────────────────────────────


class TestCandidateGating:
    def test_unconverged_frames_never_enter_candidates(
        self, fake_backend: FakeBackend, tmp_path: Path
    ) -> None:
        points = [
            _scan_point(i, 1.2 + i * (2.5 - 1.2) / 4, energy=-1.0 + (0.3 if i == 2 else 0.0))
            for i in range(5)
        ]
        # Frame 2 (the peak) failed to converge — it must not be recommended.
        points[2] = _scan_point(2, 1.85, energy=None, success=False)
        fake_backend.set_result(
            "relaxed_scan",
            RelaxedScanResult(
                points=points,
                input_xyz=tmp_path / "input.xyz",
                scan_dir=tmp_path,
                success=True,
            ),
        )
        result = run_pes_scan(request=_dft_request({"method": "GFN2-xTB"}), output_dir=tmp_path)
        assert result["selection_mode"] == "manual_only"
        assert result["ts_recommendations"] == []
        assert result["int_recommendations"] == []
        assert result["frames"][2]["optimization_converged"] is False


# ── V-5: legacy compatibility ──────────────────────────────────────────


class TestLegacyCompatibility:
    def test_legacy_xtb_engine_migrates_to_orca(self) -> None:
        from acp.catalog import normalize_legacy_method

        method = {
            "levels": {
                "scan_optimizer": {"engine": "xtb", "scan_optimizer_method": "GFN2-xTB"}
            }
        }
        migrated = normalize_legacy_method(method)
        assert migrated["levels"]["scan_optimizer"]["engine"] == "orca"

    def test_migrated_legacy_spec_passes_validation(self) -> None:
        from acp.catalog import (
            get_method_schema,
            normalize_and_validate_method_config,
            normalize_legacy_method,
        )

        legacy = {
            "levels": {
                "scan_coordinate": {"engine": "orca"},
                "scan_driver": {"engine": "orca"},
                "scan_optimizer": {"engine": "xtb", "scan_optimizer_method": "GFN2-xTB"},
                "single_point": {"engine": "orca", "functional": "B97-3c"},
            }
        }
        normalized, errors = normalize_and_validate_method_config(
            normalize_legacy_method(legacy), get_method_schema("pes_scan")
        )
        assert errors == []
        assert normalized["scan_optimizer"]["engine"] == "orca"
        # New fields fall back to schema defaults rather than failing.
        assert normalized["scan_optimizer"]["scan_optimizer_solvent_model"] == "none"

    def test_legacy_protocol_payload_parses_unchanged(self) -> None:
        protocol = ScanProtocol.from_dict(
            {
                "coordinate": {
                    "kind": "distance",
                    "atoms": [0, 1],
                    "start": 1.0,
                    "end": 3.0,
                    "n_points": 21,
                },
                "scan_optimizer": {"method": "GFN2-xTB", "max_iterations": 250},
            }
        )
        optimizer = protocol.scan_optimizer
        assert optimizer.basis is None
        assert optimizer.solvent_model == "none"
        assert optimizer.grid is None
        assert optimizer.retry_count == 2
        assert protocol.execution_mode is None
        # Old-style payloads still validate.
        validate_scan_protocol(
            ScanCoordinate(kind="distance", atoms=(0, 1), start=1.0, end=3.0, n_points=21),
            protocol,
        )

    def test_default_profile_is_unchanged_xtb_quick_scan(self) -> None:
        from acp.catalog import get_method_profiles

        profiles = {p["profile_id"]: p for p in get_method_profiles("pes_scan")}
        default_optimizer = profiles["default"]["levels"]["scan_optimizer"]
        assert default_optimizer["scan_optimizer_method"] == "GFN2-xTB"
        assert profiles["default"]["levels"]["single_point"]["functional"] == "B97-3c"
        for pid in ("economy-dft", "standard-dft", "hybrid-dft"):
            assert pid in profiles
            assert profiles[pid]["levels"]["single_point"]["_disabled"] is True


# ── traceability persisted into pes_profile.json ───────────────────────


class TestProfileTraceability:
    def test_pes_profile_carries_level_and_execution_mode(
        self, fake_backend: FakeBackend, tmp_path: Path
    ) -> None:
        fake_backend.set_result("relaxed_scan", _fake_scan_result(5, tmp_path))
        result = run_pes_scan(
            request=_dft_request({"method": "B97-3c"}), output_dir=tmp_path
        )
        profile_path, _ = persist_pes_outputs(tmp_path, scan_result=result, task_id="t1")
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        assert payload["optimization_level"]["method"] == "B97-3c"
        assert payload["optimization_level_fingerprint"]
        assert payload["execution_mode"] == "native_scan"
        assert payload["protocol"]["execution_mode"] == "native_scan"
        frame = payload["frames"][0]
        assert frame["optimizer_level"]["method"] == "B97-3c"
        assert "retry_history" in frame and "scf_converged" in frame


# ── V-10: SP failures never backfill the SP series ─────────────────────


class TestEnergySeriesSeparation:
    def test_failed_sp_frames_do_not_fall_back_to_scan_energy(self) -> None:
        from acp.results.energy_graph import build_s2_energy_graph

        frames = []
        for index in range(4):
            frames.append(
                {
                    "index": index,
                    "target_coordinate": 1.2 + 0.3 * index,
                    "actual_coordinate": 1.2 + 0.3 * index,
                    "scan_energy_hartree": -1.0 + 0.01 * index,
                    "single_point_energy_hartree": None if index == 2 else -1.01 + 0.01 * index,
                    "single_point_status": "failed" if index == 2 else "completed",
                    "optimization_converged": True,
                    "geometry_path": f"scan_frames/frame_{index:03d}.xyz",
                }
            )
        payload = {
            "schema_version": "pes_profile_v2",
            "scan": {"frames": frames, "coordinate": {"kind": "distance"}},
            "energy_profile": {
                "energy_source": "scan",
                "unit": "kcal/mol",
                "reference_index": 0,
                "relative_energies_kcal_mol": [0.0, 1.0, 2.0, 3.0],
                "raw_hartree": [-1.0, -0.99, -0.98, -0.97],
                "sp_incomplete": True,
            },
        }
        graph = build_s2_energy_graph("job-x", payload)
        series = {item["id"]: item["values"] for item in graph["series"]}
        assert series["single_point_energy"][2] is None
        assert series["scan_energy"][2] is not None


# ── CLI flags → scan-config pass-through ───────────────────────────────


class TestCliFlags:
    def _namespace(self, **overrides: Any) -> argparse.Namespace:
        base = {
            "scan_config": None,
            "source_type": None,
            "xyz_text": _ETHYLENE_XYZ,
            "asset_path": None,
            "from_manifest": None,
            "from_job": None,
            "from_frame": None,
            "charge": None,
            "multiplicity": None,
            "scan_kind": None,
            "scan_bond_type": None,
            "selection_kind": None,
            "scan_atoms": "0,1",
            "scan_start": 1.2,
            "scan_end": 2.5,
            "scan_points": 5,
            "scan_method": None,
            "scan_basis": None,
            "scan_dispersion": None,
            "scan_solvent_model": None,
            "scan_solvent": None,
            "scan_grid": None,
            "scan_scf_convergence": None,
            "scan_scf_max_iter": None,
            "scan_ri_approximation": None,
            "sp_method": None,
            "sp_basis": None,
            "no_sp": False,
            "max_iterations": None,
        }
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_new_scan_level_flags_reach_protocol(self) -> None:
        from acp.cli import _build_bond_scan_request

        request = _build_bond_scan_request(
            self._namespace(
                scan_method="b973c",
                scan_grid="DefGrid2",
                scan_scf_convergence="tight",
                scan_scf_max_iter=300,
                scan_solvent_model="SMD",
                scan_solvent="water",
                scan_ri_approximation="none",
            )
        )
        optimizer = request["protocol"]["scan_optimizer"]
        assert optimizer["method"] == "b973c"
        assert optimizer["grid"] == "DefGrid2"
        assert optimizer["scf_convergence"] == "tight"
        assert optimizer["scf_max_iterations"] == 300
        assert optimizer["solvent_model"] == "SMD"
        assert optimizer["solvent"] == "water"

    def test_scan_config_wins_over_cli_flags(self, tmp_path: Path) -> None:
        from acp.cli import _build_bond_scan_request

        scan_config = {
            "source": {"source_type": "xyz_text", "xyz_text": _ETHYLENE_XYZ},
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.2,
                "end": 2.5,
                "n_points": 5,
            },
            "protocol": {"scan_optimizer": {"method": "B97-3c", "grid": "DefGrid3"}},
        }
        config_path = tmp_path / "scan_config.json"
        config_path.write_text(json.dumps(scan_config), encoding="utf-8")
        request = _build_bond_scan_request(
            self._namespace(scan_config=str(config_path), scan_atoms=None, scan_start=None,
                            scan_end=None, scan_points=None, scan_grid="DefGrid1",
                            scan_scf_convergence="tight")
        )
        optimizer = request["protocol"]["scan_optimizer"]
        assert optimizer["grid"] == "DefGrid3"  # scheduler payload wins
        assert optimizer["scf_convergence"] == "tight"  # CLI fills gaps
