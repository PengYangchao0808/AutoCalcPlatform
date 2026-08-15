# pyright: reportMissingImports=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false
"""Tests for the native censo-lite mechanism ensemble provider."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypedDict, cast

import numpy as np
import pytest
from numpy.typing import NDArray
from rdkit import Chem
from rdkit.Chem import AllChem

from acp.backends.base import QCResult
from acp.mechanism.models import ArtifactRef, StableState
from acp.mechanism.providers import native_censo_lite
from acp.mechanism.providers.native_censo_lite import NativeCensoLiteProvider

FloatArray = NDArray[np.float64]


class FramePayload(TypedDict):
    id: str
    title: str
    coordinates: FloatArray
    symbols: list[str]
    xtb_energy: float
    sp_energy: float
    mrrho_gibbs: float | None


BackendFactory = Callable[[Mapping[str, object] | None], object]


def _butane_at_dihedral(angle_deg: float) -> tuple[Chem.Mol, FloatArray]:
    mol = Chem.AddHs(Chem.MolFromSmiles("CCCC"))
    embed_molecule = cast(Callable[..., int], getattr(AllChem, "EmbedMolecule"))
    set_dihedral_deg = cast(Callable[..., None], getattr(AllChem, "SetDihedralDeg"))
    embed_code = embed_molecule(mol, randomSeed=42)
    assert embed_code == 0
    conf = mol.GetConformer()
    set_dihedral_deg(conf, 0, 1, 2, 3, angle_deg)
    return mol, np.asarray(conf.GetPositions(), dtype=float)


def _xyz_text(symbols: list[str], coordinates: FloatArray, title: str) -> str:
    lines = [str(len(symbols)), title]
    for atom_index, symbol in enumerate(symbols):
        x = float(coordinates.item((atom_index, 0)))
        y = float(coordinates.item((atom_index, 1)))
        z = float(coordinates.item((atom_index, 2)))
        lines.append(f"{symbol} {x:.8f} {y:.8f} {z:.8f}")
    return "\n".join(lines) + "\n"


def _stable_state(tmp_path: Path, symbols: list[str], coordinates: FloatArray) -> StableState:
    canonical_xyz = tmp_path / "canonical.xyz"
    written_chars = canonical_xyz.write_text(
        _xyz_text(symbols, coordinates, "canonical"),
        encoding="utf-8",
    )
    assert written_chars >= 0
    return StableState(
        state_id="state_r",
        role="reactant",
        canonical_geometry=ArtifactRef(
            path=str(canonical_xyz),
            sha256="sha256:canonical",
            kind="stable_state_geometry",
        ),
        charge=0,
        multiplicity=1,
        identity_fingerprint="sha256:state_r",
        metadata={"smiles": "CCCC", "symbols": symbols, "coordinates": coordinates.tolist()},
    )


def _frame_payloads() -> list[FramePayload]:
    mol, anti = _butane_at_dihedral(180.0)
    _, gauche = _butane_at_dihedral(60.0)
    _, eclipsed = _butane_at_dihedral(0.0)
    symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
    return [
        {
            "id": "conf_0000",
            "title": "anti",
            "coordinates": anti,
            "symbols": symbols,
            "xtb_energy": -10.0000,
            "sp_energy": -100.0100,
            "mrrho_gibbs": -9.9885,
        },
        {
            "id": "conf_0001",
            "title": "anti_dup",
            "coordinates": anti + 0.01,
            "symbols": symbols,
            "xtb_energy": -9.9995,
            "sp_energy": -100.0095,
            "mrrho_gibbs": -9.9882,
        },
        {
            "id": "conf_0002",
            "title": "gauche",
            "coordinates": gauche,
            "symbols": symbols,
            "xtb_energy": -9.9900,
            "sp_energy": -100.0000,
            "mrrho_gibbs": None,
        },
        {
            "id": "conf_0003",
            "title": "eclipsed",
            "coordinates": eclipsed,
            "symbols": symbols,
            "xtb_energy": -9.9800,
            "sp_energy": -100.0050,
            "mrrho_gibbs": -9.9700,
        },
    ]


def _match_payload(frame_payloads: list[FramePayload], coordinates: FloatArray) -> FramePayload:
    return min(
        frame_payloads,
        key=lambda payload: float(
            np.linalg.norm(coordinates - np.asarray(payload["coordinates"], dtype=float))
        ),
    )


class _FakeCrestBackend:
    def __init__(self, frame_payloads: list[FramePayload]) -> None:
        self._frame_payloads: list[FramePayload] = frame_payloads

    def search(
        self,
        initial_xyz: Path,
        charge: int,
        multiplicity: int,
        output_dir: Path,
        **_: object,
    ) -> Path:
        del initial_xyz, charge, multiplicity
        output_dir.mkdir(parents=True, exist_ok=True)
        ensemble_xyz = output_dir / "crest_ensemble.xyz"
        symbols = list(self._frame_payloads[0]["symbols"])
        text = "".join(
            _xyz_text(
                symbols,
                np.asarray(payload["coordinates"], dtype=float),
                str(payload["title"]),
            )
            for payload in self._frame_payloads
        )
        written_chars = ensemble_xyz.write_text(text, encoding="utf-8")
        assert written_chars >= 0
        return ensemble_xyz


class _FakeXtbBackend:
    def __init__(self, frame_payloads: list[FramePayload]) -> None:
        self._frame_payloads: list[FramePayload] = frame_payloads

    def single_point(
        self,
        coordinates: FloatArray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str | None = None,
        **_: object,
    ) -> QCResult:
        del symbols, charge, multiplicity
        payload = _match_payload(self._frame_payloads, np.asarray(coordinates, dtype=float))
        target = (output_dir or Path.cwd()) / f"{output_name or 'xtb'}.out"
        target.parent.mkdir(parents=True, exist_ok=True)
        written_chars = target.write_text("xtb ok\n", encoding="utf-8")
        assert written_chars >= 0
        return QCResult(success=True, energy=float(payload["xtb_energy"]), output_file=target)

    def enso_thermo(
        self,
        coordinates: FloatArray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **_: object,
    ) -> QCResult:
        del symbols, charge, multiplicity
        payload = _match_payload(self._frame_payloads, np.asarray(coordinates, dtype=float))
        target_dir = output_dir or Path.cwd()
        target_dir.mkdir(parents=True, exist_ok=True)
        gibbs = payload["mrrho_gibbs"]
        if gibbs is None:
            return QCResult(success=False, error_message="mrrho failed", metadata={})
        return QCResult(
            success=True,
            gibbs=float(gibbs),
            output_file=target_dir / "xtb_enso.json",
            metadata={"thermo": {"g_total": float(gibbs)}},
        )


class _FakeOrcaBackend:
    def __init__(self, frame_payloads: list[FramePayload]) -> None:
        self._frame_payloads: list[FramePayload] = frame_payloads

    def single_point(
        self,
        coordinates: FloatArray,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        output_name: str | None = None,
        **_: object,
    ) -> QCResult:
        del symbols, charge, multiplicity
        payload = _match_payload(self._frame_payloads, np.asarray(coordinates, dtype=float))
        target = (output_dir or Path.cwd()) / f"{output_name or 'orca'}.out"
        target.parent.mkdir(parents=True, exist_ok=True)
        written_chars = target.write_text("orca ok\n", encoding="utf-8")
        assert written_chars >= 0
        return QCResult(success=True, energy=float(payload["sp_energy"]), output_file=target)


def test_native_censo_lite_builds_manifest_and_merges_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_payloads = _frame_payloads()
    symbols = list(frame_payloads[0]["symbols"])
    state = _stable_state(
        tmp_path,
        symbols,
        np.asarray(frame_payloads[0]["coordinates"], dtype=float),
    )

    fake_crest = _FakeCrestBackend(frame_payloads)
    fake_xtb = _FakeXtbBackend(frame_payloads)
    fake_orca = _FakeOrcaBackend(frame_payloads)

    def crest_factory(config: Mapping[str, object] | None) -> _FakeCrestBackend:
        del config
        return fake_crest

    def xtb_factory(config: Mapping[str, object] | None) -> _FakeXtbBackend:
        del config
        return fake_xtb

    def orca_factory(config: Mapping[str, object] | None) -> _FakeOrcaBackend:
        del config
        return fake_orca

    def fake_get_backend(name: str) -> BackendFactory:
        mapping: dict[str, BackendFactory] = {
            "crest": crest_factory,
            "xtb": xtb_factory,
            "orca": orca_factory,
        }
        return mapping[name]

    monkeypatch.setattr(native_censo_lite, "get_backend", fake_get_backend)

    provider = NativeCensoLiteProvider(
        config={"resources": {"nproc": 2}},
        work_root=tmp_path / "native_censo_work",
    )

    ensemble = provider.generate(state, {"name": "rph-censo-lite"})

    assert ensemble.metadata["provider"] == "acp-native-censo-lite"
    assert ensemble.metadata["profile_id"] == "rph-censo-lite"
    assert ensemble.metadata["xtb_cross_validated"] is False
    assert len(ensemble.records) == 3
    assert sum(record.weight or 0.0 for record in ensemble.records) == pytest.approx(1.0)
    assert Path(str(ensemble.metadata["selected_xyz"])).exists()

    manifest_path = Path(str(ensemble.metadata["manifest_path"]))
    manifest = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
    manifest_candidates = cast(list[dict[str, object]], manifest["candidates"])
    assert set(manifest) == {
        "schema_version",
        "candidates",
        "selected",
        "ensemble_thermodynamics",
    }
    assert manifest["schema_version"] == "s1_censo_light_ranking_v4"
    assert len(manifest_candidates) == 3
    assert manifest["selected"] == ensemble.metadata["selected_id"]
    thermodynamics = cast(dict[str, object], manifest["ensemble_thermodynamics"])
    assert set(thermodynamics) == {"total_gibbs_hartree", "temperature_k"}
    assert thermodynamics["temperature_k"] == pytest.approx(298.15)

    expected_candidate_keys = {
        "id",
        "xyz",
        "electronic_energy_hartree",
        "gibbs_free_energy_hartree",
        "boltzmann_population",
        "degeneracy",
        "relative_energy_kcal",
        "relative_free_energy_kcal",
        "xtb_mrrho_thermal_correction_hartree",
    }
    assert {frozenset(candidate) for candidate in manifest_candidates} == {
        frozenset(expected_candidate_keys)
    }

    merged_candidate = next(
        candidate for candidate in manifest_candidates if candidate["id"] == "conf_0000"
    )
    assert merged_candidate["degeneracy"] == 2
    assert any(
        candidate["xtb_mrrho_thermal_correction_hartree"] is None
        for candidate in manifest_candidates
    )
    assert any(
        record.id == "conf_0003" for record in ensemble.records
    ), "min_keep should retain 3rd candidate"


def test_native_censo_lite_wraps_missing_crest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_payloads = _frame_payloads()
    symbols = list(frame_payloads[0]["symbols"])
    state = _stable_state(
        tmp_path,
        symbols,
        np.asarray(frame_payloads[0]["coordinates"], dtype=float),
    )

    class _MissingCrestBackend:
        def search(self, *args: object, **kwargs: object) -> Path:
            del args, kwargs
            raise RuntimeError("crest binary missing")

    def crest_factory(config: Mapping[str, object] | None) -> _MissingCrestBackend:
        del config
        return _MissingCrestBackend()

    def xtb_factory(config: Mapping[str, object] | None) -> _FakeXtbBackend:
        del config
        return _FakeXtbBackend(frame_payloads)

    def orca_factory(config: Mapping[str, object] | None) -> _FakeOrcaBackend:
        del config
        return _FakeOrcaBackend(frame_payloads)

    def fake_get_backend(name: str) -> BackendFactory:
        mapping: dict[str, BackendFactory] = {
            "crest": crest_factory,
            "xtb": xtb_factory,
            "orca": orca_factory,
        }
        return mapping[name]

    monkeypatch.setattr(native_censo_lite, "get_backend", fake_get_backend)

    provider = NativeCensoLiteProvider(work_root=tmp_path / "missing_crest")

    with pytest.raises(
        RuntimeError,
        match="CREST unavailable for native censo-lite: crest binary missing",
    ):
        _ = provider.generate(state, {"name": "rph-censo-lite"})
