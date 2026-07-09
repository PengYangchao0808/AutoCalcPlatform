"""Tests for the legacy↔CENSO adapter bridge."""

from __future__ import annotations

import numpy as np
import pytest

from conformer_search.core.candidates import CandidateSet, ConformerCandidate
from conformer_search.recipes.adapter import (
    candidate_set_from_funnel_records,
    funnel_records_from_candidate_set,
)
from conformer_search.utils.file_io import write_xyz


def test_candidate_set_round_trip_preserves_candidate_fields(tmp_path) -> None:
    """CandidateSet → FunnelRecordSet → CandidateSet preserves core fields."""
    first_xyz = tmp_path / "conf_000.xyz"
    second_xyz = tmp_path / "conf_001.xyz"
    write_xyz(first_xyz, np.array([[0.0, 0.0, 0.0]]), ["C"], title="conf0")
    write_xyz(second_xyz, np.array([[1.0, 0.0, 0.0]]), ["C"], title="conf1")

    original = CandidateSet(
        candidates=[
            ConformerCandidate(
                index=0,
                coordinates=np.array([[0.0, 0.0, 0.0]]),
                symbols=["C"],
                energy=-10.1000,
                gibbs_energy=-10.2000,
                g_conc=-10.2100,
                weight=0.8,
                rank=1,
                source_file=first_xyz,
                metadata={"tag": "first"},
            ),
            ConformerCandidate(
                index=1,
                coordinates=np.array([[1.0, 0.0, 0.0]]),
                symbols=["C"],
                energy=-10.0000,
                gibbs_energy=-10.0500,
                weight=0.2,
                rank=2,
                source_file=second_xyz,
                metadata={"tag": "second"},
            ),
        ],
        reference_energy=-10.1000,
        temperature=300.0,
    )

    records = funnel_records_from_candidate_set(original)

    assert records[0].energies["final_sp"] == pytest.approx(-10.1000)
    assert records[0].energies["final_gibbs"] == pytest.approx(-10.2100)
    assert records[1].energies["final_sp"] == pytest.approx(-10.0000)

    round_tripped = candidate_set_from_funnel_records(records)

    assert round_tripped.temperature == pytest.approx(300.0)
    assert round_tripped.reference_energy == pytest.approx(-10.1000)
    assert [candidate.index for candidate in round_tripped.candidates] == [0, 1]
    assert round_tripped.candidates[0].energy == pytest.approx(-10.1000)
    assert round_tripped.candidates[0].gibbs_energy == pytest.approx(-10.2100)
    assert round_tripped.candidates[0].weight == pytest.approx(0.8)
    assert round_tripped.candidates[0].source_file == first_xyz
    assert round_tripped.candidates[0].metadata["tag"] == "first"


def test_candidate_set_round_trip_uses_embedded_geometry_without_xyz_files() -> None:
    """Embedded adapter metadata can restore candidates without reading XYZ files."""
    original = CandidateSet(
        candidates=[
            ConformerCandidate(
                index=7,
                coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.1]]),
                symbols=["C", "H"],
                energy=-5.0,
                gibbs_energy=-5.1,
                weight=1.0,
                metadata={"label": "embedded-only"},
            )
        ]
    )

    records = funnel_records_from_candidate_set(original)
    records[0].xyz_path = None

    round_tripped = candidate_set_from_funnel_records(records)
    candidate = round_tripped.candidates[0]

    assert candidate.index == 7
    assert candidate.energy == pytest.approx(-5.0)
    assert candidate.gibbs_energy == pytest.approx(-5.1)
    assert candidate.symbols == ["C", "H"]
    assert candidate.metadata["label"] == "embedded-only"
    assert np.allclose(candidate.coordinates, np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 1.1]]))
