"""FCHL ≥86-atom fragment path (todo 37 / gap G12).

Scope: the DP5.py training-set switch at 86 atoms — ``atomic_reps.gz``
(whole-molecule atoms, width 86) below the threshold, ``frag_reps.gz``
(openbabel radius-3 fragment central atoms, width 53) at/above it.

Two facts this suite pins:

1. **The fragment path is not numerically equivalent to the atomic path.**
   Different training entities, descriptor capacity and similarity/residual
   indexing. The suite never asserts equivalence; it asserts the wiring,
   the radius-3 BFS order (central atom first, rest sorted — ``DP5.py:580``)
   and the explicit typed states.
2. **The shipped upstream assets do not satisfy the KDE index pairing.**
   ``frag_reps.gz`` holds 63 541 fragments while ``folded_scaled_errors.p``
   holds 106 416 residuals (= 2 × 53 208 atomic training entries, added in a
   later upstream commit than ``frag_reps.gz``). ``DP5.py:89-92`` doubles
   ``K_sim`` and feeds it as ``gaussian_kde`` weights, so the fragment branch
   as shipped raises inside scipy. The tests verify this correspondence
   rather than assuming it, and the implementation refuses with a typed
   ``residual-index-mismatch`` instead of swapping the atomic array for the
   fragment array.

Three-state rule: the real openbabel fragmentation case is skipped with an
explicit ``NOT_VERIFIED`` reason when the bindings are not importable — a
skip is never a pass, and no stub stands in for the real fragmentation.
Fake/monkeypatched coverage (boundary + index logic) runs unconditionally.
"""

from __future__ import annotations

import gzip
import pickle
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from acp.core.models import Structure
from acp.nmr.error_model import GoodmanDP5Model, dp5_model_available
from acp.nmr.fchl import (
    C_DISTANCE,
    FRAG_ATOM_THRESHOLD,
    FRAG_MAX_SIZE,
    FRAGMENT_RADIUS,
    FRAGMENT_STATUS_ASSETS_MISSING,
    FRAGMENT_STATUS_AVAILABLE,
    FRAGMENT_STATUS_INDEX_MISMATCH,
    FRAGMENT_STATUS_OPENBABEL_MISSING,
    FRAGMENT_STATUSES,
    FragmentPathStatus,
    FragmentPathUnavailableError,
    atomic_numbers,
    build_fragment_representations,
    fragment_atom_indices,
    fragment_path_status,
    fragment_residual_index_ok,
    fragment_status_fallback_reason,
    generate_fchl_representation,
    load_atomic_reps,
    openbabel_available,
)
from acp.nmr.models import Assignment, CandidateResult, ConformerShielding, NmrConfig, SignalGroup
from acp.workflows.nmr import _compute_candidate_dp5

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "src" / "acp" / "nmr" / "models"

#: Shipped upstream counts (dp5@b6cf5590 assets) — the correspondence facts.
SHIPPED_N_FRAGMENT_TRAIN = 63541
SHIPPED_N_FOLDED_ERRORS = 106416
SHIPPED_N_ATOMIC_TRAIN = 53208

REAL_CASE_SKIP_REASON = (
    "NOT_VERIFIED: openbabel python bindings not importable — the real "
    "radius-3 fragmentation was not executed; skipped, never counted as a "
    "pass (install 'openbabel>=3.1' to verify)"
)


@pytest.fixture(autouse=True)
def _isolate_fchl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No stubbed ``qml`` and no numpy opt-in leaks across tests."""
    for name in ("qml", "qml.fchl"):
        sys.modules.pop(name, None)
    monkeypatch.delenv("ACP_FCHL_NUMPY", raising=False)
    yield
    for name in ("qml", "qml.fchl"):
        sys.modules.pop(name, None)


# ---------------------------------------------------------------------------
# Fixtures: DP5 stand-in, atom builders, reduced corresponding asset dir
# ---------------------------------------------------------------------------


class _FragmentDP5Model:
    """DP5 stand-in recording which training-set path the workflow requested."""

    model_id = "goodman-dp5"

    def __init__(self, *, fchl_available: bool = True, models_dir: Path | None = None) -> None:
        self.fchl_available = fchl_available
        self.models_dir = models_dir
        self.calls: list[dict[str, object]] = []

    def probability_per_conformer(self, shifts, exp, weights) -> float:
        self.calls.append({"path": "fallback"})
        return 0.5

    def probability_per_conformer_fchl(
        self, shifts, exp, weights, reps, *, use_fragment_reps: bool = False
    ) -> float:
        self.calls.append({"path": "fchl", "fragment": use_fragment_reps, "n_reps": len(reps)})
        return 0.6


def _symbols(n_atoms: int) -> list[str]:
    """Atom-0-carbon symbol list: 28 C + 58 H at 86 atoms, 27 C + 58 H at 85."""
    carbons = 28 if n_atoms >= FRAG_ATOM_THRESHOLD else 27
    return ["C"] * carbons + ["H"] * (n_atoms - carbons)


def _structure(n_atoms: int, *, with_graph: bool = False) -> Structure:
    symbols = _symbols(n_atoms)
    metadata: dict[str, object] = {}
    if with_graph:
        from rdkit import Chem

        metadata["nmr_topology_mol"] = Chem.AddHs(Chem.MolFromSmiles("C" * 28))
    return Structure(
        id=f"cand_{n_atoms}",
        charge=0,
        multiplicity=1,
        symbols=symbols,
        coordinates=np.zeros((n_atoms, 3)),
        metadata=metadata,
    )


def _candidate(n_atoms: int, *, signal_group: SignalGroup | None = None) -> CandidateResult:
    return CandidateResult(
        index=0,
        label=f"cand_{n_atoms}",
        assignments=[
            Assignment(
                atom_label="C1",
                element="C",
                exp_ppm=85.0,
                calc_ppm=85.0,
                scaled_ppm=85.0,
                residual=0.0,
                signal_group=signal_group,
            )
        ],
        conformer_shieldings=[
            ConformerShielding(
                conformer_id="conf_0",
                boltzmann_weight=1.0,
                shieldings={
                    0: {"symbol": "C", "isotropic": 100.0},
                    1: {"symbol": "C", "isotropic": 102.0},
                },
                coordinates=np.zeros((n_atoms, 3)),
                symbols=_symbols(n_atoms),
            )
        ],
    )


def _chain_adjacency(n_atoms: int) -> list[list[int]]:
    adjacency: list[list[int]] = []
    for index in range(n_atoms):
        neighbours = []
        if index > 0:
            neighbours.append(index - 1)
        if index + 1 < n_atoms:
            neighbours.append(index + 1)
        adjacency.append(neighbours)
    return adjacency


def _chain_coordinates(n_atoms: int, spacing: float = 1.5) -> np.ndarray:
    return np.array([[index * spacing, 0.0, 0.0] for index in range(n_atoms)], dtype=float)


def _build_reduced_fragment_models_dir(workdir: Path, n_frag: int = 8) -> Path:
    """Model dir where frag_reps[:n] *does* pair with folded[:2n] (mechanism test).

    The atomic side is symlinked from the real asset: if the fragment path
    silently used ``atomic_reps.gz`` (53 208 entries) the doubled similarity
    vector (106 416) would not match the 2n residuals and scipy would raise —
    so a successful run proves the fragment training set was consumed.
    """
    reduced = workdir / "reduced_fragment_models"
    reduced.mkdir(parents=True, exist_ok=True)
    fragment_reps = load_atomic_reps(use_frag=True)
    with (MODELS_DIR / "folded_scaled_errors.p").open("rb") as handle:
        folded_errors = np.asarray(pickle.load(handle))
    for name in ("c_w_kde_mean_s_0.025.p", "i_w_kde_mean_s_0.025.p", "atomic_reps.gz"):
        (reduced / name).symlink_to(MODELS_DIR / name)
    with gzip.open(reduced / "frag_reps.gz", "wb") as handle:
        pickle.dump(fragment_reps[:n_frag], handle)
    with (reduced / "folded_scaled_errors.p").open("wb") as handle:
        pickle.dump(folded_errors[: 2 * n_frag], handle)
    return reduced


# ---------------------------------------------------------------------------
# Availability: closed vocabulary + tri-state (+ index-mismatch) with reasons
# ---------------------------------------------------------------------------


def test_fragment_status_vocabulary_and_reason_mapping() -> None:
    assert FRAGMENT_STATUSES == (
        FRAGMENT_STATUS_AVAILABLE,
        FRAGMENT_STATUS_OPENBABEL_MISSING,
        FRAGMENT_STATUS_ASSETS_MISSING,
        FRAGMENT_STATUS_INDEX_MISMATCH,
    )
    assert fragment_status_fallback_reason(FRAGMENT_STATUS_OPENBABEL_MISSING) == (
        "fchl_fragment_openbabel_missing"
    )
    assert fragment_status_fallback_reason(FRAGMENT_STATUS_ASSETS_MISSING) == (
        "fchl_fragment_assets_missing"
    )
    assert fragment_status_fallback_reason(FRAGMENT_STATUS_INDEX_MISMATCH) == (
        "fchl_fragment_residual_index_mismatch"
    )
    with pytest.raises(ValueError, match="available"):
        fragment_status_fallback_reason(FRAGMENT_STATUS_AVAILABLE)


def test_fragment_status_openbabel_missing_is_typed() -> None:
    with patch("acp.nmr.fchl.openbabel_available", return_value=False):
        status = fragment_path_status()
    assert status.status == FRAGMENT_STATUS_OPENBABEL_MISSING
    assert status.available is False
    assert status.reason and "openbabel" in status.reason


def test_fragment_status_assets_missing_is_typed(tmp_path: Path) -> None:
    with patch("acp.nmr.fchl.openbabel_available", return_value=True):
        status = fragment_path_status(tmp_path)
    assert status.status == FRAGMENT_STATUS_ASSETS_MISSING
    assert status.available is False
    assert status.reason and "frag_reps.gz" in status.reason


def test_shipped_fragment_assets_fail_the_residual_index_correspondence() -> None:
    """Verify (not assume) the fragment↔residual training-index correspondence.

    ``DP5.py:89-92`` doubles ``K_sim`` before it becomes ``gaussian_kde``
    weights over ``folded_scaled_errors``; the pairing is defined only when
    ``2 * n_fragment_train == n_folded_errors``. The shipped fragment asset
    predates the shipped residual file and does not pair — this is the fact
    that forbids the naive "load frag_reps where atomic_reps was" swap.
    """
    fragment_reps = load_atomic_reps(use_frag=True)
    atomic_reps = load_atomic_reps()
    with (MODELS_DIR / "folded_scaled_errors.p").open("rb") as handle:
        folded_errors = np.asarray(pickle.load(handle))

    assert fragment_reps.shape == (SHIPPED_N_FRAGMENT_TRAIN, 5, 53)
    assert atomic_reps.shape[0] == SHIPPED_N_ATOMIC_TRAIN
    assert folded_errors.size == SHIPPED_N_FOLDED_ERRORS
    # atomic pairing holds; fragment pairing does not (63 541 * 2 != 106 416)
    assert fragment_residual_index_ok(atomic_reps.shape[0], folded_errors.size) is True
    assert fragment_residual_index_ok(fragment_reps.shape[0], folded_errors.size) is False
    assert 2 * fragment_reps.shape[0] == 127082


def test_fragment_status_index_mismatch_on_shipped_assets() -> None:
    with patch("acp.nmr.fchl.openbabel_available", return_value=True):
        status = fragment_path_status(MODELS_DIR)
    assert status.status == FRAGMENT_STATUS_INDEX_MISMATCH
    assert status.available is False
    assert status.n_fragment_train == SHIPPED_N_FRAGMENT_TRAIN
    assert status.n_folded_errors == SHIPPED_N_FOLDED_ERRORS
    assert status.representation_size == 53
    assert status.reason and "127082" in status.reason


def test_fragment_status_available_on_corresponding_reduced_assets(tmp_path: Path) -> None:
    reduced = _build_reduced_fragment_models_dir(tmp_path)
    with patch("acp.nmr.fchl.openbabel_available", return_value=True):
        status = fragment_path_status(reduced)
    assert status.status == FRAGMENT_STATUS_AVAILABLE
    assert status.available is True
    assert status.reason is None
    assert status.n_fragment_train == 8
    assert status.n_folded_errors == 16
    assert status.representation_size == 53


# ---------------------------------------------------------------------------
# Radius-3 BFS order + central-atom descriptor mapping (pure numpy)
# ---------------------------------------------------------------------------


def test_fragment_atom_indices_radius3_chain_and_branch() -> None:
    chain = _chain_adjacency(7)
    # exactly three bond hops from the centre, central atom first, rest sorted
    assert fragment_atom_indices(chain, 0) == [0, 1, 2, 3]
    assert fragment_atom_indices(chain, 3) == [3, 0, 1, 2, 4, 5, 6]
    assert FRAGMENT_RADIUS == 3
    assert fragment_atom_indices(chain, 0, radius=1) == [0, 1]

    # Y-branch: the fourth-hop leaf must be excluded at radius 3
    branch = [[1], [0, 2, 5], [1, 3], [2, 4], [3], [1, 6], [5]]
    assert fragment_atom_indices(branch, 0) == [0, 1, 2, 3, 5, 6]
    with pytest.raises(IndexError, match="out of range"):
        fragment_atom_indices(chain, 99)


def test_fragment_descriptor_maps_to_requested_center_atom() -> None:
    """Each returned descriptor is the radius-3 fragment centred on its atom.

    Reproduces the upstream ``mol_fragments`` ordering (``DP5.py:580``:
    central atom first, remaining fragment atoms sorted) and asserts the
    per-atom index mapping bit-for-bit against manually assembled fragments.
    """
    coordinates = _chain_coordinates(7)
    symbols = ["C"] * 7
    adjacency = _chain_adjacency(7)

    reps = build_fragment_representations(coordinates, symbols, [3, 0], adjacency=adjacency)
    assert [rep.shape for rep in reps] == [(5, FRAG_MAX_SIZE), (5, FRAG_MAX_SIZE)]

    manual_center_3 = generate_fchl_representation(
        coordinates[[3, 0, 1, 2, 4, 5, 6]],
        atomic_numbers(["C"] * 7),
        FRAG_MAX_SIZE,
        C_DISTANCE,
    )[0]
    manual_center_0 = generate_fchl_representation(
        coordinates[[0, 1, 2, 3]], atomic_numbers(["C"] * 4), FRAG_MAX_SIZE, C_DISTANCE
    )[0]
    np.testing.assert_array_equal(reps[0], manual_center_3)
    np.testing.assert_array_equal(reps[1], manual_center_0)
    assert not np.array_equal(reps[0], reps[1])


def test_fragment_builder_rejects_bad_inputs() -> None:
    coordinates = _chain_coordinates(5)
    with pytest.raises(ValueError, match="mol_block or adjacency"):
        build_fragment_representations(coordinates, ["C"] * 5, [0])
    with pytest.raises(ValueError, match="must match"):
        build_fragment_representations(coordinates, ["C"] * 4, [0], adjacency=_chain_adjacency(5))
    with pytest.raises(IndexError, match="out of range"):
        build_fragment_representations(coordinates, ["C"] * 5, [9], adjacency=_chain_adjacency(5))


def test_fragment_builder_never_silently_passes_without_openbabel() -> None:
    coordinates = _chain_coordinates(5)
    with patch("acp.nmr.fchl._import_openbabel", side_effect=ImportError("no openbabel")):
        with pytest.raises(ImportError):
            build_fragment_representations(
                coordinates, ["C"] * 5, [0], mol_block="(fake mol block)"
            )


# ---------------------------------------------------------------------------
# Workflow boundary: 85 atomic, 86 fragment (or typed unavailable)
# ---------------------------------------------------------------------------


def test_boundary_85_atoms_uses_atomic_path() -> None:
    model = _FragmentDP5Model(fchl_available=True)
    with (
        patch("acp.nmr.fchl.kernel_backend", return_value="numpy"),
        patch(
            "acp.nmr.fchl.fragment_path_status",
            side_effect=AssertionError("fragment path must not be consulted below the threshold"),
        ),
        patch(
            "acp.nmr.fchl.build_atom_representations",
            return_value=[np.ones((5, 86))],
        ) as atomic_builder,
    ):
        outcome = _compute_candidate_dp5(_candidate(85), _structure(85), NmrConfig(), model)
    assert outcome.mode == "fchl"
    assert outcome.kernel == "numpy"
    assert model.calls == [{"path": "fchl", "fragment": False, "n_reps": 1}]
    assert atomic_builder.call_count == 1
    assert "fchl_fragment_status" not in outcome.diagnostics[0]


def test_boundary_86_atoms_runs_fragment_path_when_available() -> None:
    status = FragmentPathStatus(
        status=FRAGMENT_STATUS_AVAILABLE,
        n_fragment_train=8,
        n_folded_errors=16,
        representation_size=FRAG_MAX_SIZE,
    )
    model = _FragmentDP5Model(fchl_available=True)
    with (
        patch("acp.nmr.fchl.kernel_backend", return_value="numpy"),
        patch("acp.nmr.fchl.fragment_path_status", return_value=status),
        patch(
            "acp.nmr.fchl.build_fragment_representations",
            return_value=[np.ones((5, FRAG_MAX_SIZE))],
        ) as fragment_builder,
        patch(
            "acp.nmr.fchl.build_atom_representations",
            side_effect=AssertionError("atomic descriptors must not run at >=86 atoms"),
        ),
    ):
        outcome = _compute_candidate_dp5(
            _candidate(86), _structure(86, with_graph=True), NmrConfig(), model
        )
    assert outcome.mode == "fchl"
    assert model.calls == [{"path": "fchl", "fragment": True, "n_reps": 1}]
    assert outcome.diagnostics[0]["fchl_fragment_status"] == FRAGMENT_STATUS_AVAILABLE
    assert fragment_builder.call_count == 1
    kwargs = fragment_builder.call_args.kwargs
    assert kwargs["max_size"] == FRAG_MAX_SIZE
    assert "V2000" in kwargs["mol_block"]


def test_boundary_86_atoms_real_environment_reports_typed_state() -> None:
    """Derived from the real probe: available → fragment path; else typed reason.

    In this environment openbabel is absent, so the workflow must report
    ``fchl_fragment_openbabel_missing``; on a host with openbabel the shipped
    assets report ``fchl_fragment_residual_index_mismatch``. The old
    size-forced ``fchl_unavailable`` must never appear while the kernel is
    active — that vocabulary entry is reserved for a missing kernel/assets.
    """
    status = fragment_path_status()
    model = _FragmentDP5Model(fchl_available=True)
    outcome = _compute_candidate_dp5(
        _candidate(86), _structure(86, with_graph=True), NmrConfig(), model
    )
    assert outcome.diagnostics[0]["fchl_fragment_status"] == status.status
    if status.available:
        assert outcome.mode == "fchl"
        assert model.calls[-1]["fragment"] is True
    else:
        assert outcome.mode == "fallback"
        assert outcome.diagnostics[0]["fchl_attempted"] is False
        assert outcome.diagnostics[0]["fallback_reason"] == (
            fragment_status_fallback_reason(status.status)
        )
        assert outcome.diagnostics[0]["fallback_reason"] != "fchl_unavailable"


def test_boundary_86_without_kernel_keeps_stable_fchl_unavailable_reason() -> None:
    model = _FragmentDP5Model(fchl_available=False)
    with patch(
        "acp.nmr.fchl.fragment_path_status",
        side_effect=AssertionError("no kernel means no fragment-path probe"),
    ):
        outcome = _compute_candidate_dp5(_candidate(86), _structure(86), NmrConfig(), model)
    assert outcome.mode == "fallback"
    assert outcome.diagnostics[0]["fallback_reason"] == "fchl_unavailable"
    assert outcome.diagnostics[0]["fchl_attempted"] is False
    assert "fchl_fragment_status" not in outcome.diagnostics[0]


def test_boundary_86_multi_member_signal_still_blocks_fchl() -> None:
    """T35 semantics stay intact on the fragment branch: no descriptor averaging."""
    status = FragmentPathStatus(status=FRAGMENT_STATUS_AVAILABLE, representation_size=FRAG_MAX_SIZE)
    group = SignalGroup(
        atom_uids=("C1", "C2"),
        coefficients=(0.5, 0.5),
        equivalence_basis="explicit",
    )
    model = _FragmentDP5Model(fchl_available=True)
    with patch("acp.nmr.fchl.fragment_path_status", return_value=status):
        outcome = _compute_candidate_dp5(
            _candidate(86, signal_group=group),
            _structure(86, with_graph=True),
            NmrConfig(),
            model,
        )
    assert outcome.mode == "fallback"
    assert outcome.diagnostics[0]["fallback_reason"] == "fchl_multi_member_signal"
    assert outcome.diagnostics[0]["fchl_attempted"] is False
    assert model.calls == [{"path": "fallback"}]


def test_boundary_86_fragment_available_but_graph_missing_degrades_explicitly() -> None:
    status = FragmentPathStatus(status=FRAGMENT_STATUS_AVAILABLE, representation_size=FRAG_MAX_SIZE)
    model = _FragmentDP5Model(fchl_available=True)
    with patch("acp.nmr.fchl.fragment_path_status", return_value=status):
        outcome = _compute_candidate_dp5(
            _candidate(86), _structure(86, with_graph=False), NmrConfig(), model
        )
    assert outcome.mode == "fallback"
    assert outcome.diagnostics[0]["fallback_reason"] == "fchl_representations_incomplete"
    assert model.calls == [{"path": "fallback"}]


# ---------------------------------------------------------------------------
# Model-level fragment path (error_model additive surface)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _dp5_assets() -> None:
    if not dp5_model_available():
        pytest.skip("Goodman DP5 model files not present")


def test_model_fragment_path_refuses_mismatched_shipped_assets(
    _dp5_assets: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped mismatch must raise typed, never reach scipy with bad weights."""
    monkeypatch.setenv("ACP_FCHL_NUMPY", "1")
    model = GoodmanDP5Model()
    with pytest.raises(FragmentPathUnavailableError, match="folded_scaled_errors"):
        model.probability_per_conformer_fchl(
            [[40.0]],
            [40.0],
            [1.0],
            [[np.zeros((5, FRAG_MAX_SIZE))]],
            use_fragment_reps=True,
        )


def test_model_fragment_path_runs_on_corresponding_assets(
    _dp5_assets: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end fragment probability on a consistent reduced asset pairing.

    The reduced dir pairs ``frag_reps[:8]`` with ``folded[:16]`` and keeps the
    real 53 208-entry ``atomic_reps.gz`` symlinked: a run that silently used
    the atomic training set would produce a 106 416-long weight vector and
    fail inside scipy, so success proves the fragment set was consumed. The
    resulting probability is a mechanism check, not a calibration claim.
    """
    monkeypatch.setenv("ACP_FCHL_NUMPY", "1")
    reduced = _build_reduced_fragment_models_dir(tmp_path)
    model = GoodmanDP5Model(models_dir=reduced)
    assert model.fchl_available is True

    coordinates = _chain_coordinates(12)
    reps = build_fragment_representations(
        coordinates, ["C"] * 12, [0, 5], adjacency=_chain_adjacency(12)
    )
    loaded_use_frag: list[bool] = []
    real_loader = load_atomic_reps

    def _spy_loader(models_dir=None, use_frag=False):
        loaded_use_frag.append(bool(use_frag))
        return real_loader(models_dir, use_frag)

    with patch("acp.nmr.fchl.load_atomic_reps", side_effect=_spy_loader):
        probability = model.probability_per_conformer_fchl(
            [[40.0, 30.0]],
            [40.0, 30.0],
            [1.0],
            [[reps[0], reps[1]]],
            use_fragment_reps=True,
        )
    assert loaded_use_frag == [True]
    assert 0.0 <= probability <= 1.0


# ---------------------------------------------------------------------------
# Real fragmentation case (openbabel) — NOT_VERIFIED when bindings are absent
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not openbabel_available(), reason=REAL_CASE_SKIP_REASON)
def test_real_openbabel_radius3_fragmentation_case() -> None:
    """Real openbabel parse + radius-3 fragments on a real bonded molecule."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    from acp.nmr.fchl import read_openbabel_adjacency

    mol = Chem.AddHs(Chem.MolFromSmiles("CCCCCCCCCC"))  # n-decane, 32 atoms
    AllChem.EmbedMolecule(mol, randomSeed=42)
    symbols = [atom.GetSymbol() for atom in mol.GetAtoms()]
    coordinates = np.asarray(mol.GetConformer().GetPositions(), dtype=float)
    mol_block = Chem.MolToMolBlock(mol)

    terminal, middle = 0, 4
    reps = build_fragment_representations(
        coordinates, symbols, [terminal, middle], mol_block=mol_block
    )
    assert [rep.shape for rep in reps] == [(5, FRAG_MAX_SIZE), (5, FRAG_MAX_SIZE)]
    assert not np.array_equal(reps[0], reps[1])

    adjacency = read_openbabel_adjacency(mol_block)
    assert len(adjacency) == len(symbols)
    terminal_fragment = fragment_atom_indices(adjacency, terminal)
    assert terminal_fragment[0] == terminal
    assert len(terminal_fragment) < len(symbols)  # radius-3 fragment is a subgraph
    manual = generate_fchl_representation(
        coordinates[terminal_fragment],
        atomic_numbers([symbols[index] for index in terminal_fragment]),
        FRAG_MAX_SIZE,
        C_DISTANCE,
    )[0]
    np.testing.assert_array_equal(reps[0], manual)
