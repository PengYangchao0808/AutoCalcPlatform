# pyright: reportMissingImports=false, reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportArgumentType=false
"""Tests for the RPH mechanism-study adapter layer."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from acp.mechanism.models import (
    ArtifactRef,
    AtomIdentityMap,
    Provenance,
    StableState,
    StationaryPointRequest,
    ThermoCorrection,
)
from acp.mechanism.presets import (
    RPH_CENSO_LITE_MODE,
    RPH_PROFILE_IDS,
    XTB_FAST_MODE,
    rph_profile_id,
)
from cccp.qc.interfaces.constraints import CoordinateSpec, ReactionCoordinatePlan


def _xyz(path: Path, *, symbols: list[str] | None = None) -> Path:
    atoms = symbols or ["C", "H", "H"]
    lines = [
        str(len(atoms)),
        path.stem,
        "C 0.000000 0.000000 0.000000",
        "H 1.000000 0.000000 0.000000",
        "H 0.000000 1.000000 0.000000",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _artifact(path: Path, kind: str) -> ArtifactRef:
    return ArtifactRef(path=str(path), sha256=f"sha256:{path.name}", kind=kind)


def _provenance() -> Provenance:
    return Provenance(
        provider="fake",
        provider_version="1.0",
        provider_commit="fake",
        strategy="guided-scan",
        strategy_version="1.0",
        profile_id="s3",
        schema_version="m0",
        input_signature="sha256:input",
    )


def _plan() -> ReactionCoordinatePlan:
    return ReactionCoordinatePlan(
        coordinates=(
            CoordinateSpec(
                id="rc1",
                kind="distance",
                atoms=(0, 1),
                start=2.5,
                end=1.5,
            ),
        ),
        points=5,
    )


def _request(tmp_path: Path) -> StationaryPointRequest:
    input_xyz = _xyz(tmp_path / "ts_seed.xyz")
    fallback_xyz = _xyz(tmp_path / "fallback.xyz")
    atom_map = AtomIdentityMap(
        uid_to_structure_index={"a1": 0, "a2": 1, "a3": 2},
        mapping={"mapped": {"a1": 0, "a2": 1, "a3": 2}},
    )
    thermo = ThermoCorrection(
        ensemble_delta_g_hartree=0.0012,
        metadata={
            "s1_manifest": "memory://s1/manifest.json",
            "s1_ensemble_thermodynamics": {"temperature": 298.15, "g_total": -10.5},
            "s1_thermochemistry_status": "complete",
        },
    )
    return StationaryPointRequest(
        id="req_ts_01",
        role="transition_state",
        kind="ts",
        input_geometry=_artifact(input_xyz, "ts_seed_geometry"),
        coordinate_plan=_plan(),
        fallback_geometries=[_artifact(fallback_xyz, "fallback_geometry")],
        source_stage="S2",
        charge=-1,
        multiplicity=2,
        atom_mapping=atom_map,
        parent_state_id="state_A",
        route_id="route_main",
        ensemble_correction=thermo,
        provenance=_provenance(),
    )


def _state(tmp_path: Path, state_id: str, role: str, *, smiles: str | None = None) -> StableState:
    xyz = _xyz(tmp_path / f"{state_id}.xyz")
    metadata: dict[str, object] = {
        "coordinates": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "symbols": ["C", "H", "H"],
        "route_id": "route_main",
    }
    if smiles is not None:
        metadata["smiles"] = smiles
    return StableState(
        state_id=state_id,
        role=role,  # type: ignore[arg-type]
        canonical_geometry=_artifact(xyz, "stable_state_geometry"),
        charge=0,
        multiplicity=1,
        identity_fingerprint=f"sha256:{state_id}",
        metadata=metadata,
    )


def _register_module(monkeypatch: pytest.MonkeyPatch, name: str) -> ModuleType:
    module = ModuleType(name)
    module.__dict__.setdefault("__package__", name.rsplit(".", 1)[0] if "." in name else "")
    if "." not in name:
        module.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _install_rph_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "rph_core",
        "rph_core.steps",
        "rph_core.steps.conformer_search",
        "rph_core.steps.refinement",
        "rph_core.steps.step2_retro",
    ):
        package = _register_module(monkeypatch, name)
        package.__path__ = []  # type: ignore[attr-defined]


def test_adapter_module_import_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACP_RPH_PATH", raising=False)
    for name in list(sys.modules):
        if name.startswith("rph_core"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    module = importlib.import_module("acp.mechanism.providers.rph_adapter")

    assert module.RPHUnavailableError is not None
    assert "rph_core" not in sys.modules


def test_resolve_rph_repo_path_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from acp.mechanism.providers.rph_adapter import resolve_rph_repo_path

    explicit = tmp_path / "explicit"
    config_path = tmp_path / "config"
    env_path = tmp_path / "env"
    explicit.mkdir()
    config_path.mkdir()
    env_path.mkdir()
    monkeypatch.setenv("ACP_RPH_PATH", str(env_path))

    assert resolve_rph_repo_path(explicit, {"rph": {"path": str(config_path)}}) == explicit
    assert resolve_rph_repo_path(None, {"rph": {"path": str(config_path)}}) == config_path
    assert resolve_rph_repo_path(None, {}) == env_path


def test_rph_unavailable_error_on_import_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acp.mechanism.providers.rph_adapter import RPHUnavailableError, rph_version

    missing = tmp_path / "missing-rph"
    monkeypatch.delenv("ACP_RPH_PATH", raising=False)
    for name in list(sys.modules):
        if name.startswith("rph_core"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    with pytest.raises(RPHUnavailableError, match="ACP_RPH_PATH"):
        rph_version(missing)


def test_build_rph_structure_request_payload_maps_all_fields(tmp_path: Path) -> None:
    from acp.mechanism.providers.rph_adapter import build_rph_structure_request_payload

    request = _request(tmp_path)
    payload = build_rph_structure_request_payload(
        request,
        input_xyz=Path(request.input_geometry.path),
        fallback_xyz=Path(request.fallback_geometries[0].path),
        atom_mapping_path=tmp_path / "atom_mapping.json",
    )

    assert payload["id"] == "req_ts_01"
    assert payload["role"] == "ts"
    assert payload["kind"] == "ts"
    assert payload["input_xyz"] == request.input_geometry.path
    assert payload["fallback_xyz"] == request.fallback_geometries[0].path
    assert payload["forming_bonds"] == [[0, 1]]
    assert payload["seed_state"] == "ts_search_seed"
    assert payload["charge"] == -1
    assert payload["multiplicity"] == 2
    assert payload["atom_mapping"] == str(tmp_path / "atom_mapping.json")
    assert payload["structure_id"] == "req_ts_01"
    assert payload["variant_id"] == "route_main"
    assert payload["branch_id"] == "route_main"
    assert payload["pathway_id"] == "route_main"
    assert payload["parent_structure_id"] == "state_A"
    assert payload["mapping_audit"] == "sha256:input"
    assert payload["mapping_required"] is True
    assert payload["s1_manifest"] == "memory://s1/manifest.json"
    assert payload["s1_ensemble_thermodynamics"] == {"temperature": 298.15, "g_total": -10.5}
    assert payload["s1_thermochemistry_status"] == "complete"
    assert payload["ensemble_thermochemistry_correction_hartree"] == pytest.approx(0.0012)


def test_rph_profile_id_mapping_constants() -> None:
    assert RPH_PROFILE_IDS == {
        "s3": "b97_3c_r2scan_3c_v1",
        "s4": "m062x_wb97mv_v1",
    }
    assert rph_profile_id("s3") == "b97_3c_r2scan_3c_v1"
    assert rph_profile_id("s4") == "m062x_wb97mv_v1"
    assert RPH_CENSO_LITE_MODE == "rph-censo-lite"
    assert XTB_FAST_MODE == "xtb-fast"


def test_convert_rph_refinement_manifest(tmp_path: Path) -> None:
    from acp.mechanism.providers.rph_adapter import convert_rph_refinement_manifest

    canonical_xyz = _xyz(tmp_path / "canonical.xyz")
    opt_out = tmp_path / "opt.out"
    freq_out = tmp_path / "freq.out"
    sp_out = tmp_path / "sp.out"
    manifest_path = tmp_path / "manifest.json"
    opt_out.write_text("opt", encoding="utf-8")
    freq_out.write_text("freq", encoding="utf-8")
    sp_out.write_text("sp", encoding="utf-8")
    manifest_path.write_text("{}", encoding="utf-8")
    payload = {
        "schema_version": "refinement_manifest_v1",
        "stage": "S3",
        "fidelity": "low",
        "profile_id": "b97_3c_r2scan_3c_v1",
        "run_id": "run-001",
        "summary": {"complete": 1},
        "structures": [
            {
                "id": "req_ts_01",
                "role": "ts",
                "kind": "ts",
                "charge": -1,
                "multiplicity": 2,
                "status": "complete",
                "canonical_xyz": str(canonical_xyz),
                "opt_output": str(opt_out),
                "canonical_frequency_output": str(freq_out),
                "sp_output": str(sp_out),
                "opt_energy_hartree": -100.123,
                "sp_energy_hartree": -100.456,
                "canonical_imaginary_frequencies_cm1": [-321.5],
                "ts_classification": {
                    "stationary_point_class": "valid_target_ts",
                    "mode_match_score": 0.83,
                },
                "pathway_id": "route_main",
                "parent_structure_id": "state_A",
                "thermochemistry": {"gibbs_hartree": -100.333},
            }
        ],
    }

    manifest = convert_rph_refinement_manifest(
        payload,
        manifest_path=manifest_path,
        fidelity="s3",
        provider_version="4.0.1",
        input_signature="sha256:test-manifest",
    )

    assert manifest.manifest_id == "run-001"
    assert manifest.manifest_hash.startswith("sha256:")
    assert manifest.fidelity == "s3"
    assert manifest.canonical_winner is not None
    assert manifest.canonical_winner.point_id == "req_ts_01"
    assert manifest.canonical_winner.role == "transition_state"
    assert manifest.canonical_winner.kind == "ts"
    assert manifest.canonical_winner.energy_hartree == pytest.approx(-100.456)
    assert manifest.canonical_winner.identity is not None
    assert manifest.canonical_winner.identity.mode_match_score == pytest.approx(0.83)
    assert manifest.attempts[0].status == "success"
    assert manifest.metadata["profile_id"] == "b97_3c_r2scan_3c_v1"
    assert manifest.metadata["structures"][0]["canonical_xyz"] == str(canonical_xyz)


def test_seed_selection_to_seed_candidates() -> None:
    from acp.mechanism.providers.rph_adapter import seed_selection_to_seed_candidates

    selection = SimpleNamespace(
        seed_evidence="peak_knee_shifted",
        ts_search_seed={"frame_index": 3, "xyz": "/tmp/ts.xyz", "confidence": "high"},
        int_search_seed={
            "frame_index": 4,
            "xyz": "/tmp/int.xyz",
            "shared_with_ts": False,
            "selection_mode": "stretch_plateau",
        },
        has_independent_int=True,
    )

    candidates = seed_selection_to_seed_candidates(
        selection,
        point_ids_by_frame={3: "p003", 4: "p004"},
    )

    assert [candidate.kind for candidate in candidates] == ["ts_seed", "intermediate_seed"]
    assert candidates[0].selection_mode == "endpoint_knee_shift_midpoint_v1"
    assert candidates[0].stationary_point_claimed is False
    assert candidates[0].evidence["point_id"] == "p003"
    assert candidates[1].evidence["shared_with_ts"] is False
    assert candidates[1].evidence["has_independent_int"] is True


def test_ensemble_provider_uses_stubbed_rph_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_rph_packages(monkeypatch)
    version_module = _register_module(monkeypatch, "rph_core.version")
    version_module.__version__ = "4.0.1"
    censo_module = _register_module(monkeypatch, "rph_core.steps.conformer_search.censo_lite")

    candidate_xyz = _xyz(tmp_path / "candidate.xyz")
    manifest_path = tmp_path / "s1_manifest.json"
    manifest_payload = {
        "schema_version": "s1_censo_light_ranking_v4",
        "selected": "cand-1",
        "ensemble_thermodynamics": {"temperature_k": 298.15},
        "candidates": [
            {
                "id": "cand-1",
                "xyz": str(candidate_xyz),
                "s1_score_hartree": -10.5,
                "boltzmann_population": 0.82,
                "degeneracy": 2,
                "relative_energy_kcal": 0.0,
                "relative_free_energy_kcal": 0.1,
                "xtb_mrrho_thermal_correction_hartree": 0.005,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    class FakeCensoLiteEngine:
        last_config: dict[str, Any] | None = None

        def __init__(
            self,
            config: dict[str, Any],
            work_dir: Path,
            molecule_name: str,
            **_: Any,
        ) -> None:
            FakeCensoLiteEngine.last_config = config
            self.work_dir = work_dir
            self.molecule_name = molecule_name

        def run(self, smiles_or_input: str) -> dict[str, Any]:
            assert smiles_or_input == "CCO"
            return {
                "manifest": manifest_path,
                "selected_xyz": candidate_xyz,
                "atom_mapping": tmp_path / "atom_mapping.json",
                "data": manifest_payload,
            }

    censo_module.CensoLiteEngine = FakeCensoLiteEngine

    from acp.mechanism.providers.rph_adapter import RPHEnsembleProvider, _cached_rph_version

    _cached_rph_version.cache_clear()
    provider = RPHEnsembleProvider(rph_path=tmp_path)
    ensemble = provider.generate(
        _state(tmp_path, "state_A", "reactant", smiles="CCO"),
        RPH_CENSO_LITE_MODE,
    )

    assert len(ensemble.records) == 1
    assert ensemble.records[0].structure.id == "cand-1"
    assert ensemble.records[0].weight == pytest.approx(0.82)
    assert ensemble.records[0].properties["degeneracy"] == 2
    provenance = ensemble.metadata["provenance"]
    assert provenance["provider"] == "rph"
    assert provenance["provider_version"] == "4.0.1"
    assert FakeCensoLiteEngine.last_config is not None
    assert FakeCensoLiteEngine.last_config["step1"]["censo_lite"]["ranking"]["charge"] == 0


def test_refinement_provider_end_to_end_with_stubbed_rph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_rph_packages(monkeypatch)
    version_module = _register_module(monkeypatch, "rph_core.version")
    version_module.__version__ = "4.0.1"
    refinement_module = sys.modules["rph_core.steps.refinement"]
    manifest_io_module = _register_module(monkeypatch, "rph_core.steps.refinement.manifest_io")

    canonical_xyz = _xyz(tmp_path / "canonical.xyz")
    opt_out = tmp_path / "opt.out"
    freq_out = tmp_path / "freq.out"
    sp_out = tmp_path / "sp.out"
    opt_out.write_text("opt", encoding="utf-8")
    freq_out.write_text("freq", encoding="utf-8")
    sp_out.write_text("sp", encoding="utf-8")
    manifest_payload = {
        "schema_version": "refinement_manifest_v1",
        "stage": "S3",
        "fidelity": "low",
        "profile_id": "b97_3c_r2scan_3c_v1",
        "run_id": "stub-run",
        "structures": [
            {
                "id": "req_ts_01",
                "role": "ts",
                "kind": "ts",
                "charge": -1,
                "multiplicity": 2,
                "status": "complete",
                "canonical_xyz": str(canonical_xyz),
                "opt_output": str(opt_out),
                "canonical_frequency_output": str(freq_out),
                "sp_output": str(sp_out),
                "sp_energy_hartree": -77.11,
                "canonical_imaginary_frequencies_cm1": [-450.0],
                "ts_classification": {
                    "stationary_point_class": "valid_target_ts",
                    "mode_match_score": 0.91,
                },
            }
        ],
    }

    class FakeRphFidelityProfile:
        def __init__(self, stage: str, fidelity: str, profile_id: str) -> None:
            self.stage = stage
            self.fidelity = fidelity
            self.profile_id = profile_id

        @classmethod
        def from_config(cls, config: dict[str, Any], stage: str) -> FakeRphFidelityProfile:
            if stage == "S3":
                return cls("S3", "low", config["refinement"]["s3"]["profile_id"])
            return cls("S4", "high", config["refinement"]["s4"]["profile_id"])

    class FakeRefinementEngine:
        last_requests: list[dict[str, Any]] | None = None
        last_kwargs: dict[str, Any] | None = None

        def __init__(
            self,
            config: dict[str, Any],
            profile: FakeRphFidelityProfile,
            **_: Any,
        ) -> None:
            self.config = config
            self.profile = profile

        def run(
            self,
            requests: list[dict[str, Any]],
            output_dir: Path,
            event_callback: Any = None,
            **kwargs: Any,
        ) -> Path:
            FakeRefinementEngine.last_requests = requests
            FakeRefinementEngine.last_kwargs = kwargs
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / "manifest.json"
            path.write_text(json.dumps({"ok": True}), encoding="utf-8")
            return path

    def fake_read_refinement_manifest(path: Path) -> dict[str, Any]:
        assert path.name == "manifest.json"
        return manifest_payload

    refinement_module.FidelityProfile = FakeRphFidelityProfile
    refinement_module.RefinementEngine = FakeRefinementEngine
    manifest_io_module.read_refinement_manifest = fake_read_refinement_manifest

    from acp.mechanism.providers.rph_adapter import RPHRefinementProvider, _cached_rph_version

    _cached_rph_version.cache_clear()
    provider = RPHRefinementProvider(
        rph_path=tmp_path,
        resume_incomplete=True,
        structure_ids=["req_ts_01"],
        rescue_only=True,
    )
    manifest = provider.refine([_request(tmp_path)], "s3")

    assert manifest.manifest_id == "stub-run"
    assert manifest.canonical_winner is not None
    assert manifest.canonical_winner.provenance is not None
    assert manifest.canonical_winner.provenance.provider_version == "4.0.1"
    assert FakeRefinementEngine.last_kwargs == {
        "resume_incomplete": True,
        "structure_ids": ["req_ts_01"],
        "rescue_only": True,
    }
    assert FakeRefinementEngine.last_requests is not None
    mapped = FakeRefinementEngine.last_requests[0]
    assert mapped["id"] == "req_ts_01"
    assert mapped["role"] == "ts"
    assert mapped["forming_bonds"] == [[0, 1]]
    assert mapped["charge"] == -1
    assert mapped["multiplicity"] == 2
    assert mapped["s1_manifest"] == "memory://s1/manifest.json"
    assert mapped["ensemble_thermochemistry_correction_hartree"] == pytest.approx(0.0012)


def test_path_strategy_search_with_stubbed_rph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_rph_packages(monkeypatch)
    version_module = _register_module(monkeypatch, "rph_core.version")
    version_module.__version__ = "4.0.1"
    peb_module = _register_module(monkeypatch, "rph_core.steps.step2_retro.peb_scanner")
    selector_module = _register_module(monkeypatch, "rph_core.steps.step2_retro.path_selector")
    profile_module = _register_module(monkeypatch, "rph_core.steps.step2_retro.path_profile")

    frame_paths = [_xyz(tmp_path / f"frame_{idx}.xyz") for idx in range(3)]
    profile_path = tmp_path / "scan_profile.json"
    profile_path.write_text("{}", encoding="utf-8")
    payload = {
        "profile_schema_version": "s2_scan_profile_v10",
        "selection_source": "orca_relaxed_scan",
        "generation_method": "stubbed",
        "frames": [str(path) for path in frame_paths],
        "energy_curves": {"b973c": {"energies_hartree": [-10.0, -9.8, -9.9]}},
        "composite_profile": {
            "points": [
                {"point_id": "p000"},
                {"point_id": "p001"},
                {"point_id": "p002"},
            ]
        },
    }

    fake_frames = [
        SimpleNamespace(
            frame_index=idx,
            xyz=frame_paths[idx],
            energy_hartree=-10.0 + idx * 0.1,
            reaction_coordinates=(3.0 - 0.5 * idx,),
            progress=idx / 2,
            topology_valid=True,
            topology_reason=None,
            rmsd_to_product=0.1 * idx,
            neighbor_rmsd=0.05,
            gradient_proxy=0.2,
            curvature_proxy=0.3,
            source="orca_relaxed_scan",
        )
        for idx in range(3)
    ]
    fake_profile = SimpleNamespace(
        source="orca_relaxed_scan",
        complete=True,
        topology_valid_intervals=((0, 2),),
        frames=tuple(fake_frames),
    )
    fake_selection = SimpleNamespace(
        seed_evidence="peak_knee_shifted",
        ts_search_seed={"frame_index": 1, "xyz": str(frame_paths[1]), "confidence": "high"},
        int_search_seed={
            "frame_index": 2,
            "xyz": str(frame_paths[2]),
            "shared_with_ts": False,
            "selection_mode": "stretch_plateau",
        },
        diagnostics={"selection_algorithm": "endpoint_knee_shift_midpoint_v1"},
        endpoint_evidence={"effective_endpoint_index": 2},
        knee_evidence={"frame_index": 1},
        has_independent_int=True,
    )

    class FakePEBScanner:
        def __init__(self, *_: Any, **__: Any) -> None:
            self.last_profile_payload = payload

        def run(
            self,
            product_xyz: Path,
            output_dir: Path,
            forming_bonds: list[tuple[int, int]],
        ) -> tuple[Any, ...]:
            assert product_xyz.exists()
            assert output_dir.exists()
            assert forming_bonds == [(0, 1)]
            return (
                frame_paths[1],
                frame_paths[2],
                frame_paths[2],
                tuple(forming_bonds),
                profile_path,
                "COMPLETE",
                "high",
                (),
            )

    peb_module.PEBScanner = FakePEBScanner
    selector_module.policy_from_config = lambda selection_cfg, rescue_cfg=None: {
        "selection": selection_cfg,
        "rescue": rescue_cfg,
    }
    selector_module.select_path_seeds = lambda profile, policy: fake_selection
    profile_module.build_orca_scan_profile = lambda **kwargs: fake_profile

    from acp.mechanism.providers.rph_adapter import RPHPathSearchStrategy, _cached_rph_version

    _cached_rph_version.cache_clear()
    strategy = RPHPathSearchStrategy(rph_path=tmp_path)
    result = strategy.search(
        _state(tmp_path, "state_A", "reactant", smiles="CCO"),
        _state(tmp_path, "state_B", "product"),
        _plan(),
        "s3",
    )

    assert result.strategy == "rph-reverse"
    assert result.strategy_id == "rph-reverse"
    assert result.strategy_version == "4.0.1"
    assert result.complete is True
    assert len(result.points) == 3
    assert [candidate.kind for candidate in result.seed_candidates] == [
        "ts_seed",
        "intermediate_seed",
    ]
    assert result.selected_ts_id == "ts_candidate_p001"
    assert result.selected_int_id == "int_candidate_p002"
    assert result.metadata["gate_policies"]["G2"]["b97_full_coverage"] is True
    assert result.endpoint_evidence["effective_endpoint_index"] == 2
    assert result.artifacts["scan_profile"] == str(profile_path)


@pytest.mark.skipif(
    not Path("/mnt/e/Calculations/Common_Script/Auto_Calc_Platform/ReactionProfileHunter").exists(),
    reason="Local ReactionProfileHunter checkout not present",
)
def test_real_rph_import_smoke() -> None:
    from acp.mechanism.providers.rph_adapter import (
        DEFAULT_RPH_REPO_PATH,
        RPHUnavailableError,
        default_rph_config,
        resolve_rph_repo_path,
        rph_version,
    )

    repo_path = resolve_rph_repo_path(DEFAULT_RPH_REPO_PATH)
    try:
        version = rph_version(repo_path)
    except RPHUnavailableError as exc:  # pragma: no cover - environment-dependent
        pytest.skip(str(exc))

    assert version == "4.0.1"
    sys.path.insert(0, str(repo_path))
    try:
        refinement_module = importlib.import_module("rph_core.steps.refinement")
        profile = refinement_module.FidelityProfile.from_config(default_rph_config(), "S3")
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Real RPH smoke import unavailable: {exc}")
    finally:
        if sys.path and sys.path[0] == str(repo_path):
            sys.path.pop(0)

    assert profile.profile_id == "b97_3c_r2scan_3c_v1"
