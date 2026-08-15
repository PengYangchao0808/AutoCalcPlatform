from __future__ import annotations

# pyright: reportAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportExplicitAny=false, reportMissingTypeArgument=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedCallResult=false
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.mechanism.models import ArtifactRef, Provenance, StationaryPointRequest
from acp.mechanism.presets import FidelityProfile
from acp.mechanism.providers.native_refinement import NativeRefinementProvider
from cccp.qc.interfaces.constraints import (
    CoordinateSpec,
    DistanceConstraint,
    ReactionCoordinatePlan,
)
from cccp.qc.interfaces.orca_ts import TsOptResult

SYMBOLS = ["C", "H", "H", "H"]
SEED_COORDS = np.asarray(
    [
        [0.0, 0.0, 0.0],
        [1.1, 0.0, 0.0],
        [2.4, 0.0, 0.0],
        [3.5, 0.0, 0.0],
    ],
    dtype=float,
)
WARMUP_COORDS = np.asarray(
    [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [2.3, 0.0, 0.0],
        [3.3, 0.0, 0.0],
    ],
    dtype=float,
)
TS_COORDS = np.asarray(
    [
        [0.0, 0.0, 0.0],
        [1.2, 0.1, 0.0],
        [2.3, -0.1, 0.0],
        [3.4, 0.0, 0.0],
    ],
    dtype=float,
)
MIN_COORDS = np.asarray(
    [
        [0.0, 0.0, 0.0],
        [1.1, 0.0, 0.0],
        [2.2, 0.0, 0.0],
        [3.3, 0.0, 0.0],
    ],
    dtype=float,
)
MODE_VECTOR = np.ones_like(TS_COORDS)


def _write_xyz(path: Path, coordinates: np.ndarray, symbols: list[str] | None = None) -> Path:
    atoms = symbols or SYMBOLS
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [str(len(atoms)), path.stem]
    for symbol, (x_coord, y_coord, z_coord) in zip(atoms, coordinates, strict=True):
        lines.append(f"{symbol} {x_coord:.8f} {y_coord:.8f} {z_coord:.8f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _artifact(path: Path, kind: str = "geometry") -> ArtifactRef:
    return ArtifactRef(path=str(path), sha256=f"sha256:{path.name}", kind=kind)


def _provenance() -> Provenance:
    return Provenance(
        provider="test",
        provider_version="1.0",
        provider_commit="test-commit",
        strategy="guided-scan",
        strategy_version="1.0",
        profile_id="s3",
        schema_version="m0",
        input_signature="sha256:test-input",
    )


def _plan() -> ReactionCoordinatePlan:
    return ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(id="rc1", kind="distance", atoms=(0, 1), start=2.2, end=1.1),
            CoordinateSpec(id="rc2", kind="distance", atoms=(2, 3), start=2.2, end=1.1),
        ),
        points=7,
    )


def _profile() -> FidelityProfile:
    return FidelityProfile(
        name="s3",
        ts_method="B97-3c",
        freq_method="B97-3c",
        sp_method="r2SCAN-3c",
        warmup_max_cycles_intermediate=40,
        warmup_max_cycles_ts=50,
        max_cycles_minimum=60,
        max_cycles_intermediate=60,
        max_cycles_ts=60,
    )


def _request(
    tmp_path: Path,
    request_id: str,
    *,
    role: str,
    kind: str,
    coordinates: np.ndarray | None = None,
) -> StationaryPointRequest:
    xyz_path = _write_xyz(
        tmp_path / f"{request_id}.xyz",
        coordinates if coordinates is not None else SEED_COORDS,
    )
    return StationaryPointRequest(
        id=request_id,
        role=role,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        input_geometry=_artifact(xyz_path, "seed_geometry"),
        coordinate_plan=_plan(),
        fallback_geometries=[],
        source_stage="S2",
        charge=0,
        multiplicity=1,
        atom_mapping=None,
        parent_state_id="state-1",
        route_id="route-1",
        ensemble_correction=None,
        provenance=_provenance(),
    )


def _job_files(output_dir: Path, output_name: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_file = output_dir / f"{output_name}.inp"
    log_file = output_dir / f"{output_name}.out"
    input_file.write_text("input", encoding="utf-8")
    log_file.write_text("output", encoding="utf-8")
    return input_file, log_file


def _qc_result(
    *,
    output_dir: Path,
    output_name: str,
    coordinates: np.ndarray,
    energy: float,
    frequencies: list[float] | None = None,
    gibbs: float | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> QCResult:
    input_file, log_file = _job_files(output_dir, output_name)
    return QCResult(
        success=success,
        energy=energy,
        coordinates=np.asarray(coordinates, dtype=float),
        symbols=list(SYMBOLS),
        converged=success,
        output_file=input_file,
        log_file=log_file,
        error_message=error_message,
        frequencies=list(frequencies) if frequencies is not None else None,
        has_frequencies=bool(frequencies),
        gibbs=gibbs,
    )


def _ts_result(
    *,
    output_dir: Path,
    output_name: str,
    coordinates: np.ndarray,
    energy: float,
    imaginary_frequencies: list[float],
    all_frequencies: list[float] | None = None,
    mode_vector: np.ndarray | None = MODE_VECTOR,
    success: bool = True,
    error_message: str | None = None,
) -> TsOptResult:
    input_file, log_file = _job_files(output_dir, output_name)
    return TsOptResult(
        success=success,
        energy_hartree=energy,
        coordinates=np.asarray(coordinates, dtype=float) if success else None,
        symbols=list(SYMBOLS),
        converged=success,
        imaginary_frequencies=list(imaginary_frequencies),
        all_frequencies=list(all_frequencies or imaginary_frequencies),
        output_file=input_file,
        log_file=log_file,
        error_message=error_message,
        mode_vector=np.asarray(mode_vector, dtype=float) if mode_vector is not None else None,
    )


class FakeOrca:
    def __init__(self, **scenarios: list[Any]) -> None:
        self.scenarios = {name: list(values) for name, values in scenarios.items()}
        self.calls: dict[str, list[dict[str, Any]]] = {
            "constrained_optimize": [],
            "transition_state_opt": [],
            "optimize": [],
            "frequency": [],
            "single_point": [],
        }

    def _consume(self, name: str, payload: dict[str, Any], default: Any) -> Any:
        self.calls[name].append(payload)
        scenario = self.scenarios.get(name, [])
        if not scenario:
            return default
        outcome = scenario.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(payload) if callable(outcome) else outcome

    def constrained_optimize(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        *,
        output_dir: Path,
        output_name: str,
        constraints: list[DistanceConstraint],
        **kwargs: Any,
    ) -> QCResult:
        payload = {
            "coordinates": np.asarray(coordinates, dtype=float),
            "symbols": list(symbols),
            "output_dir": Path(output_dir),
            "output_name": output_name,
            "constraints": list(constraints),
            "kwargs": dict(kwargs),
        }
        default = _qc_result(
            output_dir=Path(output_dir),
            output_name=output_name,
            coordinates=np.asarray(coordinates, dtype=float),
            energy=-10.0,
        )
        return self._consume("constrained_optimize", payload, default)

    def transition_state_opt(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        *,
        output_dir: Path,
        output_name: str,
        **kwargs: Any,
    ) -> TsOptResult:
        payload = {
            "coordinates": np.asarray(coordinates, dtype=float),
            "symbols": list(symbols),
            "output_dir": Path(output_dir),
            "output_name": output_name,
            "kwargs": dict(kwargs),
        }
        default = _ts_result(
            output_dir=Path(output_dir),
            output_name=output_name,
            coordinates=np.asarray(coordinates, dtype=float),
            energy=-100.0,
            imaginary_frequencies=[-800.0],
            all_frequencies=[-800.0, 120.0, 240.0],
        )
        return self._consume("transition_state_opt", payload, default)

    def optimize(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        *,
        output_dir: Path,
        output_name: str,
        **kwargs: Any,
    ) -> QCResult:
        payload = {
            "coordinates": np.asarray(coordinates, dtype=float),
            "symbols": list(symbols),
            "output_dir": Path(output_dir),
            "output_name": output_name,
            "kwargs": dict(kwargs),
        }
        default = _qc_result(
            output_dir=Path(output_dir),
            output_name=output_name,
            coordinates=np.asarray(coordinates, dtype=float),
            energy=-50.0,
        )
        return self._consume("optimize", payload, default)

    def frequency(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        *,
        output_dir: Path,
        output_name: str,
        **kwargs: Any,
    ) -> QCResult:
        payload = {
            "coordinates": np.asarray(coordinates, dtype=float),
            "symbols": list(symbols),
            "output_dir": Path(output_dir),
            "output_name": output_name,
            "kwargs": dict(kwargs),
        }
        default = _qc_result(
            output_dir=Path(output_dir),
            output_name=output_name,
            coordinates=np.asarray(coordinates, dtype=float),
            energy=-49.5,
            frequencies=[150.0, 240.0, 350.0],
            gibbs=-49.2,
        )
        return self._consume("frequency", payload, default)

    def single_point(
        self,
        coordinates: np.ndarray,
        symbols: list[str],
        *,
        output_dir: Path,
        output_name: str,
        **kwargs: Any,
    ) -> QCResult:
        payload = {
            "coordinates": np.asarray(coordinates, dtype=float),
            "symbols": list(symbols),
            "output_dir": Path(output_dir),
            "output_name": output_name,
            "kwargs": dict(kwargs),
        }
        default = _qc_result(
            output_dir=Path(output_dir),
            output_name=output_name,
            coordinates=np.asarray(coordinates, dtype=float),
            energy=-50.5,
        )
        return self._consume("single_point", payload, default)


def _patch_backend(monkeypatch: pytest.MonkeyPatch, fake_orca: FakeOrca) -> None:
    monkeypatch.setattr(
        "acp.mechanism.providers.native_refinement.get_backend",
        lambda name: (lambda config: fake_orca),
    )


def test_native_refinement_ts_happy_path_writes_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_orca = FakeOrca(
        constrained_optimize=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=WARMUP_COORDS,
                energy=-10.0,
            )
        ],
        transition_state_opt=[
            lambda call: _ts_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.1,
                imaginary_frequencies=[-800.0],
                all_frequencies=[-800.0, 120.0, 250.0],
            )
        ],
        frequency=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.0,
                frequencies=[-800.0, 120.0, 250.0],
                gibbs=-99.7,
            )
        ],
        single_point=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.5,
            )
        ],
    )
    _patch_backend(monkeypatch, fake_orca)
    provider = NativeRefinementProvider(config={}, work_root=tmp_path / "native")

    manifest = provider.refine(
        [_request(tmp_path, "ts-happy", role="transition_state", kind="ts")],
        _profile(),
    )

    assert len(fake_orca.calls["constrained_optimize"]) == 1
    assert len(fake_orca.calls["constrained_optimize"][0]["constraints"]) == 2
    assert len(fake_orca.calls["transition_state_opt"]) == 1
    assert len(fake_orca.calls["frequency"]) == 1
    assert len(fake_orca.calls["single_point"]) == 1
    assert manifest.canonical_winner is not None
    assert manifest.canonical_winner.kind == "ts"
    assert manifest.canonical_winner.identity is not None
    assert manifest.canonical_winner.identity.valid is True
    assert manifest.attempts[0].status == "success"
    manifest_path = Path(str(manifest.metadata["manifest_path"]))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["summary"] == {"request_count": 1, "n_success": 1, "n_failed": 0}
    assert payload["structures"][0]["canonical_xyz"] == manifest.canonical_winner.geometry.path
    assert manifest_path.is_file()
    assert Path(manifest.canonical_winner.geometry.path).is_file()


def test_native_refinement_higher_order_saddle_runs_multiple_rescues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_orca = FakeOrca(
        transition_state_opt=[
            lambda call: _ts_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.0,
                imaginary_frequencies=[-900.0, -250.0],
                all_frequencies=[-900.0, -250.0, 100.0],
            ),
            lambda call: _ts_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.2,
                imaginary_frequencies=[-700.0, -180.0],
                all_frequencies=[-700.0, -180.0, 110.0],
            ),
            lambda call: _ts_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.3,
                imaginary_frequencies=[-650.0],
                all_frequencies=[-650.0, 120.0, 220.0],
            ),
        ],
        frequency=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-99.9,
                frequencies=[-900.0, -250.0, 100.0],
                gibbs=-99.7,
            ),
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.0,
                frequencies=[-700.0, -180.0, 110.0],
                gibbs=-99.8,
            ),
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.1,
                frequencies=[-650.0, 120.0, 220.0],
                gibbs=-99.9,
            ),
        ],
    )
    _patch_backend(monkeypatch, fake_orca)
    provider = NativeRefinementProvider(config={}, work_root=tmp_path / "native")

    manifest = provider.refine(
        [_request(tmp_path, "ts-rescue", role="transition_state", kind="ts")],
        _profile(),
    )

    assert len(fake_orca.calls["transition_state_opt"]) == 3
    assert fake_orca.calls["transition_state_opt"][1]["kwargs"][
        "mode_displacement"
    ] == pytest.approx(0.30)
    assert fake_orca.calls["transition_state_opt"][2]["kwargs"]["ts_mode"] is True
    assert manifest.canonical_winner is not None
    assert manifest.canonical_winner.identity is not None
    assert manifest.canonical_winner.identity.valid is True


def test_native_refinement_scf_failure_is_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_orca = FakeOrca(
        transition_state_opt=[
            lambda call: _ts_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=TS_COORDS,
                energy=-100.0,
                imaginary_frequencies=[],
                success=False,
                error_message="SCF failed to converge",
            )
        ]
    )
    _patch_backend(monkeypatch, fake_orca)
    provider = NativeRefinementProvider(config={}, work_root=tmp_path / "native")

    manifest = provider.refine(
        [_request(tmp_path, "ts-scf", role="transition_state", kind="ts")],
        _profile(),
    )

    assert manifest.canonical_winner is None
    assert manifest.attempts[0].status == "failed"
    assert len(fake_orca.calls["transition_state_opt"]) == 1
    assert fake_orca.calls["frequency"] == []
    assert fake_orca.calls["single_point"] == []


def test_native_refinement_intermediate_rescues_imaginary_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_orca = FakeOrca(
        optimize=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-50.1,
            ),
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-50.2,
            ),
        ],
        frequency=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-49.9,
                frequencies=[-30.0, 120.0, 240.0],
                gibbs=-49.7,
            ),
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-50.0,
                frequencies=[120.0, 240.0, 360.0],
                gibbs=-49.8,
            ),
        ],
    )
    _patch_backend(monkeypatch, fake_orca)
    provider = NativeRefinementProvider(config={}, work_root=tmp_path / "native")

    manifest = provider.refine(
        [_request(tmp_path, "int-rescue", role="intermediate", kind="minimum")],
        _profile(),
    )

    assert len(fake_orca.calls["constrained_optimize"]) == 1
    assert len(fake_orca.calls["optimize"]) == 2
    assert "rescue_00_mode_displacement" in str(fake_orca.calls["optimize"][1]["output_dir"])
    assert manifest.attempts[0].status == "success"
    assert manifest.canonical_winner is not None
    assert manifest.canonical_winner.kind == "minimum"
    assert manifest.canonical_winner.identity is None


def test_native_refinement_batch_keeps_second_success_after_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_orca = FakeOrca(
        transition_state_opt=[RuntimeError("boom")],
        optimize=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-60.0,
            )
        ],
        frequency=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-59.8,
                frequencies=[120.0, 210.0, 340.0],
                gibbs=-59.5,
            )
        ],
        single_point=[
            lambda call: _qc_result(
                output_dir=call["output_dir"],
                output_name=call["output_name"],
                coordinates=MIN_COORDS,
                energy=-60.4,
            )
        ],
    )
    _patch_backend(monkeypatch, fake_orca)
    provider = NativeRefinementProvider(config={}, work_root=tmp_path / "native")
    ts_request = _request(tmp_path, "ts-fail", role="transition_state", kind="ts")
    product_request = _request(tmp_path, "prod-ok", role="product", kind="minimum")

    manifest = provider.refine([ts_request, product_request], _profile())

    assert [attempt.status for attempt in manifest.attempts] == ["failed", "success"]
    assert manifest.canonical_winner is not None
    assert manifest.canonical_winner.point_id == "prod-ok"
    manifest_path = Path(str(manifest.metadata["manifest_path"]))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["summary"] == {"request_count": 2, "n_success": 1, "n_failed": 1}
    assert [row["id"] for row in payload["structures"]] == ["ts-fail", "prod-ok"]
