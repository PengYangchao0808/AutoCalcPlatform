from __future__ import annotations

import numpy as np
import pytest

from acp.calculations.pes.atom_selection import parse_functional_atom_selection
from acp.calculations.pes.contracts import PesScanRequest, ScanCoordinate, build_default_protocol
from acp.calculations.pes.scan import _run_relaxed_scan_backend
from tests.conftest import FakeBackend


def test_contiguous_selection_forms_are_topology_checked() -> None:
    symbols = ["C", "C", "C", "C"]
    coordinates = [
        [0.0, 0.0, 0.0],
        [1.4, 0.0, 0.0],
        [2.8, 0.0, 0.0],
        [4.2, 0.0, 0.0],
    ]

    assert parse_functional_atom_selection(
        "bond_stretch", [0, 1], symbols, coordinates
    ).bond_pairs == ((0, 1),)
    assert parse_functional_atom_selection("angle", [0, 1, 2], symbols, coordinates).kind == "angle"
    assert (
        parse_functional_atom_selection("dihedral", [0, 1, 2, 3], symbols, coordinates).kind
        == "dihedral"
    )


def test_double_bond_scan_accepts_separate_and_adjacent_groups() -> None:
    symbols = ["C"] * 5
    coordinates = [
        [0.0, 0.0, 0.0],
        [1.4, 0.0, 0.0],
        [2.8, 0.0, 0.0],
        [4.2, 0.0, 0.0],
        [5.6, 0.0, 0.0],
    ]

    selection = parse_functional_atom_selection(
        "double_bond_scan", [0, 1, 3, 4], symbols, coordinates
    )
    assert selection.groups == ((0, 1), (3, 4))
    assert selection.bond_pairs == ((0, 1), (3, 4))

    # Regression guard: groups joined by an intervening bond (retro-[2+2],
    # concerted flanking-bond break) are legitimate and must parse.
    selection = parse_functional_atom_selection(
        "double_bond_scan", [0, 1, 2, 3], symbols, coordinates
    )
    assert selection.groups == ((0, 1), (2, 3))
    assert selection.bond_pairs == ((0, 1), (2, 3))

    with pytest.raises(ValueError, match="must be an adjacent pair"):
        parse_functional_atom_selection("double_bond_scan", [0, 2, 3, 4], symbols, coordinates)

    # Shared atoms trip the earlier uniqueness guard, not the group check.
    with pytest.raises(ValueError, match="unique"):
        parse_functional_atom_selection("double_bond_scan", [0, 1, 1, 2], symbols, coordinates)


def test_nonbonded_pair_is_rejected_for_bond_stretch() -> None:
    with pytest.raises(ValueError, match="not a bond"):
        parse_functional_atom_selection(
            "bond_stretch",
            [0, 1],
            ["C", "C"],
            [[0.0, 0.0, 0.0], [4.0, 0.0, 0.0]],
        )


def test_double_scan_request_roundtrip_preserves_both_coordinates() -> None:
    request = PesScanRequest.from_dict(
        {
            "source": {"source_type": "xyz_text", "xyz_text": "2\n\nC 0 0 0\nC 1.4 0 0"},
            "coordinate": {
                "kind": "distance",
                "atoms": [0, 1],
                "start": 1.2,
                "end": 2.2,
                "n_points": 5,
            },
            "coordinates": [
                {
                    "kind": "distance",
                    "atoms": [0, 1],
                    "start": 1.2,
                    "end": 2.2,
                    "n_points": 5,
                },
                {
                    "kind": "distance",
                    "atoms": [3, 4],
                    "start": 1.2,
                    "end": 2.2,
                    "n_points": 5,
                },
            ],
            "selection": {"kind": "double_bond_scan", "atom_indices": [0, 1, 3, 4]},
        }
    )

    restored = PesScanRequest.from_dict(request.to_dict())
    assert len(restored.scan_coordinates) == 2
    assert restored.selection["kind"] == "double_bond_scan"
    assert restored.scan_coordinates[1].atoms == (3, 4)


def test_double_scan_compiles_to_one_synchronous_plan(tmp_path, fake_backend: FakeBackend) -> None:
    coordinates = (
        ScanCoordinate(kind="distance", atoms=(0, 1), start=1.2, end=2.2, n_points=3),
        ScanCoordinate(kind="distance", atoms=(3, 4), start=1.2, end=2.2, n_points=3),
    )
    _run_relaxed_scan_backend(
        coords=np.asarray(
            [[0.0, 0.0, 0.0], [1.4, 0.0, 0.0], [2.8, 0.0, 0.0], [4.2, 0.0, 0.0], [5.6, 0.0, 0.0]],
            dtype=float,
        ),
        symbols=["C"] * 5,
        charge=0,
        multiplicity=1,
        coordinates=coordinates,
        protocol=build_default_protocol(coordinates[0]),
        scan_dir=tmp_path,
        cfg={},
    )

    plan = fake_backend.calls[-1].kwargs["plan"]
    assert len(plan.drive_coordinates()) == 2
    assert plan.coordinate_targets(1)["coordinate_1"] == pytest.approx(1.7)
    assert plan.coordinate_targets(1)["coordinate_2"] == pytest.approx(1.7)
