"""Real-QC smoke skeleton for the ``cccp.calculation`` task layer (A9).

Wave 0 skeleton (todo 5). Each test asserts the FINAL expected chain goes
through ``cccp.calculation`` run_* task entries for a small molecule sample
(ORCA sp/opt/freq, xTB opt, CREST search, ISOSTAT/CENSO/Shermo small samples).

``cccp.calculation`` does not exist yet, so every smoke test is individually
tracked with a strict xfail (NEVER whole-file xfail, NEVER a mock masquerading
as real computation):

    pytest.mark.xfail(strict=True, raises=ImportError, reason="pending todo 41")

Gating (both layers, either one may skip before the xfail applies):
- ``@pytest.mark.slow`` + ``@pytest.mark.integration``: default run (no
  ``--run-integration``) skips every smoke case cleanly — never errors.
- ``@requires_orca/crest/xtb/isostat/shermo`` (``tests.conftest``, shutil.which
  at conftest import): missing binaries skip — never error.

Why ``raises=ImportError`` (verified empirically 2026-10-03): today
``from cccp.calculation import run_singlepoint`` raises ``ModuleNotFoundError``;
once the package lands (todo 8/11) but a run_* entry is not exported yet, the
same import raises ``ImportError: cannot import name``. ``ImportError`` covers
both states, so tests stay xfail (not unexpected-fail) through partial landing
and turn XPASS exactly when their own task core lands — at which point todo 41
must remove the xfail mark (strict XPASS fails the run until it is removed).

Lifecycle: todo 41 executes/records the real runs and removes ALL xfail marks
(``grep -rn xfail tests/test_cccp_real_qc_smoke.py`` must be empty at plan
acceptance, then re-run with ``--runxfail``). Evidence conventions for the
recorded runs live in ``.omo/evidence/acp-cccp-remediation/README.md``.

The request-construction and result-extraction blocks are best-effort shapes of
the planned Task API (``run_*(request, *, context=None) -> TaskResult``); todo
41 aligns them to the frozen spec in ``docs/ACP_CCCP_Task_API_DevDoc.md`` with
minimal edits. The assertion helpers encode the real acceptance shape: energy /
coordinate / frequency validity, artifact existence with correct structure
correspondence, numeric tolerance for stable samples, atom order / record
identity, and software version / input / log recording.
"""

from __future__ import annotations

import inspect
import math
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import (
    requires_crest,
    requires_isostat,
    requires_orca,
    requires_shermo,
    requires_xtb,
)

# Pending-todo-41 tracker: one per test, never at module level (whole-file
# xfail is forbidden — enforced by test_xfail_marks_are_strict_and_explicit).
_PENDING_TODO_41 = pytest.mark.xfail(
    strict=True,
    raises=ImportError,
    reason="pending todo 41",
)

# CENSO has no tests.conftest marker; gate with the same shutil.which semantics
# (CONFSEARCH_CENSO_PATH override mirrors conftest._resolve_executable_path).
_HAS_CENSO = shutil.which(os.environ.get("CONFSEARCH_CENSO_PATH") or "censo") is not None
requires_censo = pytest.mark.skipif(not _HAS_CENSO, reason="CENSO not available")

# --- small stable samples ---------------------------------------------------
# Water at a near-equilibrium geometry (C2v): stable sample for sp/opt/freq.
_WATER_SYMBOLS = ("O", "H", "H")
_WATER_COORDINATES = np.array(
    [
        [0.000000, 0.000000, 0.117300],
        [0.000000, 0.757200, -0.469200],
        [0.000000, -0.757200, -0.469200],
    ]
)

# Loose physical total-energy bands (Hartree) for the stable samples. These are
# non-vacuous numeric tolerances for the skeleton; todo 41 replaces each band
# with the pinned golden value +/- tolerance from
# tests/baseline/cccp_calculation_goldens/ once real runs are recorded.
_ENERGY_BAND_HARTREE = {
    "water_hf_def2svp": (-77.5, -75.0),
    "water_gfn2xtb": (-50.0, -1.0),
}

_WATER_FREQ_WAVENUMBER_BAND = (0.0, 4500.0)  # O-H stretches top out ~3900 cm^-1


# --- acceptance-shape helpers (placeholder assertions for todo 41 runs) -----


def _assert_energy_valid(energy_hartree: float, *, band: tuple[float, float]) -> None:
    """Energy acceptance shape: finite Hartree-range value inside the sample band."""
    assert energy_hartree is not None
    assert math.isfinite(energy_hartree)
    lo, hi = band
    assert lo < energy_hartree < hi, f"energy {energy_hartree} outside [{lo}, {hi}] Ha"


def _assert_structure_correspondence(
    symbols: list[str] | tuple[str, ...],
    coordinates: np.ndarray,
    *,
    expected_symbols: tuple[str, ...],
) -> None:
    """Structure acceptance shape: atom order/record identity + valid geometry."""
    assert tuple(symbols) == expected_symbols, "atom order/record identity must be preserved"
    coords = np.asarray(coordinates, dtype=float)
    assert coords.shape == (len(expected_symbols), 3), "one (x, y, z) row per input atom"
    assert np.isfinite(coords).all(), "coordinates must be finite"
    if coords.shape[0] > 1:
        deltas = coords[:, None, :] - coords[None, :, :]
        distances = np.sqrt((deltas**2).sum(axis=-1))
        iu = np.triu_indices(coords.shape[0], k=1)
        assert distances[iu].min() > 0.5, "no collapsed/duplicate atoms"


def _assert_frequencies_valid(
    frequencies_cm1: np.ndarray,
    *,
    n_atoms: int,
    allow_imaginary: bool,
) -> None:
    """Frequency acceptance shape: mode count matches 3N-6 (nonlinear sample)."""
    freqs = np.asarray(frequencies_cm1, dtype=float)
    assert freqs.ndim == 1
    assert freqs.size == 3 * n_atoms - 6, "nonlinear stable sample must have 3N-6 modes"
    assert np.isfinite(freqs).all(), "frequencies must be finite"
    if not allow_imaginary:
        assert (freqs > 0.0).all(), f"stable sample must have no imaginary modes: {freqs}"


def _assert_artifact_rows_match_structures(
    structures: list[object],
    energy_rows: list[float] | np.ndarray,
    *,
    expected_symbols: tuple[str, ...],
) -> None:
    """Ensemble acceptance shape: per-row record identity + table length match."""
    assert len(structures) >= 1, "at least one structure must be produced"
    assert len(energy_rows) == len(structures), "one energy row per structure record"
    for struct in structures:
        _assert_structure_correspondence(
            struct.symbols,
            np.asarray(struct.coordinates, dtype=float),
            expected_symbols=expected_symbols,
        )


def _assert_run_recorded(result: object) -> None:
    """Provenance acceptance shape: software version, exact input, and log recorded."""
    provenance = result.provenance
    assert provenance.software_version, "software version must be recorded"
    artifacts = result.artifacts
    kinds = {artifact.kind for artifact in artifacts}
    assert "input" in kinds, "exact generated input must be recorded as an artifact"
    assert "log" in kinds, "raw program output/log must be recorded as an artifact"


def _assert_converged(result: object) -> None:
    """Task-level completion semantics: converged run, complete result."""
    assert result.complete is True, "smoke sample must converge"
    assert result.error is None, "converged smoke sample must not report an error"


def _task_context(work_dir: Path):
    """Best-effort TaskContext — todo 41 aligns to the frozen Task API."""
    from cccp.calculation import TaskContext

    return TaskContext(work_dir=work_dir)


# --- smoke tests (final expected chain through cccp.calculation) ------------


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
def test_orca_singlepoint_real_smoke(tmp_path: Path) -> None:
    """ORCA singlepoint via the frozen cccp task API (todo 17 landed).

    The ImportError xfail placeholder is removed now that ``run_singlepoint``
    is callable against the frozen ``TaskRequest`` envelope; golden pinning
    of recorded real runs remains todo 41.
    """
    from cccp.calculation import (
        MethodSpec,
        StructureInput,
        TaskContext,
        TaskKind,
        TaskRequest,
        run_singlepoint,
    )

    workdir = tmp_path / "sp"
    workdir.mkdir()
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(
            coordinates=tuple(tuple(float(c) for c in row) for row in _WATER_COORDINATES),
            symbols=_WATER_SYMBOLS,
        ),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="HF", basis="def2-SVP"),
        backend="orca",
        output_dir=workdir,
    )
    result = run_singlepoint(request, context=TaskContext(workdir=workdir))

    assert result.status == "completed"
    assert result.complete is True
    assert result.errors == ()
    _assert_energy_valid(result.energy_hartree, band=_ENERGY_BAND_HARTREE["water_hf_def2svp"])
    # Singlepoint must return the input geometry unchanged (record identity).
    _assert_structure_correspondence(
        list(result.symbols or ()),
        np.asarray(result.coordinates, dtype=float),
        expected_symbols=_WATER_SYMBOLS,
    )
    assert result.provenance is not None and result.provenance.backend == "orca"
    artifact_types = {artifact.type for artifact in result.artifacts}
    assert "log" in artifact_types or "output" in artifact_types, (
        "the raw program output/log must be recorded as an artifact"
    )


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
def test_orca_optimize_real_smoke(tmp_path: Path) -> None:
    """ORCA geometry optimization via the frozen cccp task API (todo 18 landed).

    The ImportError xfail placeholder is removed now that ``run_optimize``
    is callable against the frozen ``TaskRequest`` envelope; golden pinning
    of recorded real runs remains todo 41.
    """
    from cccp.calculation import (
        MethodSpec,
        StructureInput,
        TaskContext,
        TaskKind,
        TaskRequest,
        run_optimize,
    )
    from cccp.calculation.results import OptimizePayload

    workdir = tmp_path / "opt"
    workdir.mkdir()
    request = TaskRequest(
        task=TaskKind.OPTIMIZE,
        structure=StructureInput(
            coordinates=tuple(tuple(float(c) for c in row) for row in _WATER_COORDINATES),
            symbols=_WATER_SYMBOLS,
        ),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="HF", basis="def2-SVP"),
        backend="orca",
        output_dir=workdir,
    )
    result = run_optimize(request, context=TaskContext(workdir=workdir))

    assert result.status == "completed"
    assert result.complete is True
    assert result.errors == ()
    assert isinstance(result.payload, OptimizePayload)
    assert result.payload.optimization_status == "converged"
    _assert_energy_valid(result.energy_hartree, band=_ENERGY_BAND_HARTREE["water_hf_def2svp"])
    # Optimized geometry keeps atom order/record identity of the input.
    _assert_structure_correspondence(
        list(result.symbols or ()),
        np.asarray(result.coordinates, dtype=float),
        expected_symbols=_WATER_SYMBOLS,
    )
    assert result.provenance is not None and result.provenance.backend == "orca"
    artifact_types = {artifact.type for artifact in result.artifacts}
    assert "log" in artifact_types or "output" in artifact_types, (
        "the raw program output/log must be recorded as an artifact"
    )


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
@_PENDING_TODO_41
def test_orca_frequency_real_smoke(tmp_path: Path) -> None:
    """ORCA frequency via cccp.calculation.run_frequency (todo 19)."""
    from cccp.calculation import TaskRequest, run_frequency

    request = TaskRequest(
        kind="frequency",
        options={
            "backend": "orca",
            "method": "HF",
            "basis": "def2-SVP",
            "charge": 0,
            "multiplicity": 1,
        },
        symbols=list(_WATER_SYMBOLS),
        coordinates=_WATER_COORDINATES.tolist(),
    )
    result = run_frequency(request, context=_task_context(tmp_path / "freq"))

    _assert_converged(result)
    freqs = np.asarray(result.payload.frequencies_cm1, dtype=float)
    _assert_frequencies_valid(freqs, n_atoms=len(_WATER_SYMBOLS), allow_imaginary=False)
    lo, hi = _WATER_FREQ_WAVENUMBER_BAND
    assert ((freqs > lo) & (freqs < hi)).all(), f"frequencies outside [{lo}, {hi}] cm^-1"
    _assert_run_recorded(result)


@pytest.mark.slow
@pytest.mark.integration
@requires_xtb
def test_xtb_optimize_real_smoke(tmp_path: Path) -> None:
    """xTB (GFN2-xTB) optimization via the frozen cccp task API (todo 18 landed).

    The ImportError xfail placeholder is removed now that ``run_optimize``
    is callable against the frozen ``TaskRequest`` envelope; golden pinning
    of recorded real runs remains todo 41.
    """
    from cccp.calculation import (
        MethodSpec,
        StructureInput,
        TaskContext,
        TaskKind,
        TaskRequest,
        run_optimize,
    )
    from cccp.calculation.results import OptimizePayload

    workdir = tmp_path / "xtb_opt"
    workdir.mkdir()
    request = TaskRequest(
        task=TaskKind.OPTIMIZE,
        structure=StructureInput(
            coordinates=tuple(tuple(float(c) for c in row) for row in _WATER_COORDINATES),
            symbols=_WATER_SYMBOLS,
        ),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="GFN2-xTB"),
        backend="xtb",
        output_dir=workdir,
    )
    result = run_optimize(request, context=TaskContext(workdir=workdir))

    assert result.status == "completed"
    assert result.complete is True
    assert result.errors == ()
    assert isinstance(result.payload, OptimizePayload)
    assert result.payload.optimization_status == "converged"
    _assert_energy_valid(result.energy_hartree, band=_ENERGY_BAND_HARTREE["water_gfn2xtb"])
    _assert_structure_correspondence(
        list(result.symbols or ()),
        np.asarray(result.coordinates, dtype=float),
        expected_symbols=_WATER_SYMBOLS,
    )
    assert result.provenance is not None and result.provenance.backend == "xtb"
    artifact_types = {artifact.type for artifact in result.artifacts}
    assert "log" in artifact_types or "output" in artifact_types, (
        "the raw program output/log must be recorded as an artifact"
    )


@pytest.mark.slow
@pytest.mark.integration
@requires_crest
@_PENDING_TODO_41
def test_crest_conformer_search_real_smoke(tmp_path: Path) -> None:
    """CREST conformer search via cccp.calculation.run_conformer_search (todo 42)."""
    from cccp.calculation import TaskRequest, run_conformer_search

    request = TaskRequest(
        kind="conformer_search",
        options={
            "backend": "crest",
            "gfn_level": 2,
            "charge": 0,
            "multiplicity": 1,
        },
        symbols=list(_WATER_SYMBOLS),
        coordinates=_WATER_COORDINATES.tolist(),
    )
    result = run_conformer_search(request, context=_task_context(tmp_path / "crest"))

    _assert_converged(result)
    _assert_artifact_rows_match_structures(
        result.payload.conformers,
        result.payload.energies,
        expected_symbols=_WATER_SYMBOLS,
    )
    _assert_run_recorded(result)


@pytest.mark.slow
@pytest.mark.integration
@requires_isostat
@_PENDING_TODO_41
def test_isostat_clustering_real_smoke(tmp_path: Path) -> None:
    """ISOSTAT clustering via cccp.calculation.run_clustering (todo 42)."""
    from cccp.calculation import TaskRequest, run_clustering

    request = TaskRequest(
        kind="clustering",
        options={
            "backend": "isostat",
            "charge": 0,
            "multiplicity": 1,
        },
        symbols=list(_WATER_SYMBOLS),
        coordinates=[_WATER_COORDINATES.tolist()] * 4,
    )
    result = run_clustering(request, context=_task_context(tmp_path / "isostat"))

    _assert_converged(result)
    labels = list(result.payload.cluster_labels)
    representatives = result.payload.representatives
    assert len(labels) >= 1, "every input frame must receive a cluster label"
    assert 1 <= len(representatives) <= len(labels), "representative per cluster"
    for struct in representatives:
        _assert_structure_correspondence(
            struct.symbols,
            np.asarray(struct.coordinates, dtype=float),
            expected_symbols=_WATER_SYMBOLS,
        )
    _assert_run_recorded(result)


@pytest.mark.slow
@pytest.mark.integration
@requires_censo
@_PENDING_TODO_41
def test_censo_refine_real_smoke(tmp_path: Path) -> None:
    """CENSO refinement via cccp.calculation.run_censo_refine (todo 43)."""
    from cccp.calculation import TaskRequest, run_censo_refine

    request = TaskRequest(
        kind="censo_refine",
        options={
            "backend": "censo",
            "preset": "light",
            "charge": 0,
            "multiplicity": 1,
        },
        symbols=list(_WATER_SYMBOLS),
        coordinates=[_WATER_COORDINATES.tolist()] * 2,
    )
    result = run_censo_refine(request, context=_task_context(tmp_path / "censo"))

    _assert_converged(result)
    rows = result.payload.rows
    weights = np.asarray([row.weight for row in rows], dtype=float)
    energies = np.asarray([row.energy for row in rows], dtype=float)
    assert len(rows) >= 1, "refined ensemble must keep at least one conformer record"
    assert np.isfinite(energies).all(), "per-conformer energies must be finite"
    assert (weights >= 0.0).all(), "Boltzmann weights must be non-negative"
    assert math.isclose(float(weights.sum()), 1.0, rel_tol=0.0, abs_tol=1e-3), (
        "weights must sum to 1 within 1e-3"
    )
    # Record identity: each refined row maps back to an original conformer index.
    for row in rows:
        assert isinstance(row.source_index, int) and row.source_index >= 0
    _assert_run_recorded(result)


@pytest.mark.slow
@pytest.mark.integration
@requires_shermo
@_PENDING_TODO_41
def test_shermo_thermochemistry_real_smoke(tmp_path: Path) -> None:
    """Shermo thermochemistry via cccp.calculation.run_thermochemistry (todo 22)."""
    from cccp.calculation import TaskRequest, run_thermochemistry

    request = TaskRequest(
        kind="thermochemistry",
        options={
            "backend": "shermo",
            "temperature_k": 298.15,
            "standard_state": "1atm",
            "charge": 0,
            "multiplicity": 1,
        },
        symbols=list(_WATER_SYMBOLS),
        coordinates=_WATER_COORDINATES.tolist(),
        frequencies_cm1=[1595.0, 3657.0, 3756.0],
    )
    result = run_thermochemistry(request, context=_task_context(tmp_path / "shermo"))

    _assert_converged(result)
    payload = result.payload
    assert math.isclose(payload.temperature_k, 298.15, rel_tol=0.0, abs_tol=1e-6)
    for name in ("enthalpy", "gibbs_free_energy", "entropy"):
        value = getattr(payload, name)
        assert math.isfinite(value), f"{name} must be finite"
    _assert_energy_valid(payload.gibbs_free_energy, band=_ENERGY_BAND_HARTREE["water_hf_def2svp"])
    _assert_run_recorded(result)


# --- skeleton self-guard (ungated: runs in `-m "not slow"` CI) ---------------


def test_xfail_marks_are_strict_and_explicit() -> None:
    """Any xfail in this file must be strict + raises-bound — no sloppy masks.

    Guard against whole-file xfail and reason-less/raises-less xfail masks.
    Smoke tests without an xfail mark are fine (the post-todo-41 state).
    """
    module = sys.modules[__name__]
    module_marks = [mark for mark in getattr(module, "pytestmark", []) if mark.name == "xfail"]
    assert not module_marks, "whole-file xfail is forbidden; track each test individually"

    expected_kwargs = {"strict": True, "raises": ImportError, "reason": "pending todo 41"}
    smoke_tests = 0
    for name, func in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        marks = getattr(func, "pytestmark", [])
        xfail_marks = [mark for mark in marks if mark.name == "xfail"]
        if not any(mark.name == "slow" for mark in marks):
            assert not xfail_marks, f"{name}: xfail only allowed on slow smoke tests"
            continue
        smoke_tests += 1
        if not xfail_marks:
            continue  # mark already removed after its task core landed
        assert len(xfail_marks) == 1, f"{name}: exactly one xfail mark expected"
        assert xfail_marks[0].kwargs == expected_kwargs, (
            f"{name}: xfail must be strict with raises=ImportError and the todo 41 reason"
        )
    assert smoke_tests == 8, "skeleton must track the 8 planned real-QC smoke samples"
