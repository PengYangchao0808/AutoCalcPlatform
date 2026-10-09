"""Level-1 real-QC smoke for the NMR workchain (plan todos 49/51).

Gating (plan todo 49)
---------------------
Every real case is gated twice and may only end in PASS, FAIL or
``NOT_VERIFIED``:

- ``@pytest.mark.slow`` + ``@pytest.mark.integration``: not executed unless
  ``--run-slow``/``--run-integration`` is passed.
- ``@requires_orca`` / ``@requires_crest`` / ``@requires_censo`` (add
  ``@requires_xtb`` when a case needs it): detection is the conftest
  production resolver — ``cccp.config.load_config`` +
  ``cccp.software.resolve_executable``, resolved once at import over
  ``~/.cccp.yaml`` and ``CONFSEARCH_<NAME>_PATH`` (never ``shutil.which``
  over the bare binary name).

A skip is ``NOT_VERIFIED`` — never a green pass — and the literal token is
carried in every binary-gate skip reason (``pytest -rs`` shows it).

Chain under test (level 1: connectivity/parse only)
---------------------------------------------------
Fixed molecule set (rigid + flexible, H/C, multi-conformer):

* ethane   ``CC``        — rigid single minimum, H/C
* ethanol  ``CCO``       — flexible anti/gauche, H/C/O
* n-butane ``CCCC``      — flexible anti/gauche, H/C
* toluene  ``Cc1ccccc1`` — rigid aromatic ring + methyl rotor, H/C

Three real cases run where applicable:

1. RDKit-embedded initial geometry -> ORCA GIAO shielding (HF/def2-SVP, the
   light level used by ``tests/test_cccp_real_qc_smoke.py``);
2. CREST GFN2 conformer search -> ORCA GIAO on the two lowest conformers
   (ethanol, n-butane);
3. CREST -> CENSO ``censo-light`` ranking -> ORCA GIAO on the top-ranked
   original conformer (ethanol).

Every real run asserts: GIAO status/complete, atom identity and symbol
order, exactly the NMR-active atom shieldings parsed, and covalent-radii
connectivity identical to the SMILES reference graph.  Evidence JSON (ORCA
input/log paths, parse counts, connectivity result, timestamps) is written
per molecule under
``.omo/evidence/acp-nmr-goodman-gap-remediation/task-51-real-qc-<molecule>.json``
with raw ORCA logs copied beside it.

Level-1 conclusions are connectivity/parse-only: **no accuracy or
calibration claim may be derived from this file**, and no mock may stand in
for a real run.  Binary absence skips the case as ``NOT_VERIFIED``.

Real-run environment: this host configures binaries in ``~/.cccp.yaml``,
which is NOT on ``PATH``, so export the real paths first (ORCA/CREST/XTB/
CENSO correspond to ``executables.{orca,crest,xtb,censo}.path`` in that
file):

    export CONFSEARCH_ORCA_PATH=/home/<user>/orca611/orca
    export CONFSEARCH_CREST_PATH=/home/<user>/crest/crest
    export CONFSEARCH_XTB_PATH=/home/<user>/xtb-dist/bin/xtb
    export CONFSEARCH_CENSO_PATH=/home/<user>/anaconda3/bin/censo

    PYTHONPATH=src python3.11 -m pytest --run-slow --run-integration \\
        tests/test_acp_nmr_qc_smoke.py -q

The two no-binary logic tests at the end cover the evidence-recorder and
connectivity/reference-graph code paths without any QC subprocess.
"""

from __future__ import annotations

import copy
import functools
import json
import math
import os
import shutil
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import numpy as np
import pytest

from tests.conftest import requires_censo, requires_crest, requires_orca

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
_EVIDENCE_DIR: Final[Path] = _REPO_ROOT / ".omo" / "evidence" / "acp-nmr-goodman-gap-remediation"
_EVIDENCE_LOG_ROOT: Final[Path] = _EVIDENCE_DIR / "task-51-real-qc-logs"
_EVIDENCE_SCHEMA: Final[str] = "acp-nmr-qc-smoke-evidence-v1"
_SCOPE_NOTE: Final[str] = (
    "Level-1 real-QC smoke: connectivity/parsing of the GIAO chain only. "
    "No accuracy or calibration claim may be derived from this evidence."
)
_GIAO_METHOD: Final[str] = "HF"
_GIAO_BASIS: Final[str] = "def2-SVP"
_NMR_ACTIVE_ELEMENTS: Final[frozenset[str]] = frozenset({"H", "C", "N", "F", "P"})
_ORCA_TIMEOUT_S: Final[int] = 900
_CREST_TIMEOUT_S: Final[int] = 900
_CENSO_TIMEOUT_S: Final[int] = 1800
_CREST_ENERGY_WINDOW_KCAL: Final[float] = 6.0
_MAX_SHIELDED_CONFORMERS: Final[int] = 2


@dataclass(frozen=True, slots=True)
class _MoleculeCase:
    """One fixed molecule of the level-1 smoke set."""

    name: str
    smiles: str
    character: str
    flexible: bool


_CASES: Final[tuple[_MoleculeCase, ...]] = (
    _MoleculeCase("ethane", "CC", "rigid single minimum; H/C", False),
    _MoleculeCase("ethanol", "CCO", "flexible anti/gauche; H/C/O", True),
    _MoleculeCase("n-butane", "CCCC", "flexible anti/gauche; H/C", True),
    _MoleculeCase("toluene", "Cc1ccccc1", "rigid aromatic ring + methyl rotor; H/C", False),
)
_CONFORMER_CASES: Final[tuple[_MoleculeCase, ...]] = tuple(case for case in _CASES if case.flexible)
_CHAIN_CASE: Final[_MoleculeCase] = _CASES[1]  # ethanol: smallest flexible case


@dataclass(frozen=True, slots=True)
class _MoleculeGeometry:
    """A 3D geometry derived from a SMILES string via RDKit."""

    symbols: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]
    charge: int
    multiplicity: int
    rdkit_version: str


# --- real-binary environment (never mock) -----------------------------------


@pytest.fixture(autouse=True)
def _enable_real_orca_mpi_sniff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's suite-wide MPI-sniff disable for these real-binary cases.

    ``tests.conftest`` sets ``ACP_DISABLE_MPI_SNIFF=1`` so mock-subprocess
    tests never observe the login-shell probe.  Real ORCA launches need that
    probe (it locates ORCA's bundled OpenMPI and runtime environment), so the
    smoke suite re-enables it — matching the precedent in
    ``tests/test_cccp_real_qc_smoke.py``.
    """
    monkeypatch.delenv("ACP_DISABLE_MPI_SNIFF", raising=False)


@functools.lru_cache(maxsize=1)
def _base_config() -> dict[str, Any]:
    """The operator config (``~/.cccp.yaml``) that pins real binary paths."""
    from cccp.config import load_config

    return dict(load_config())


def _bounded_config() -> dict[str, Any]:
    """Operator config copy with explicit per-step wall-clock bounds.

    Only the test-local copy is changed — global defaults and
    ``~/.cccp.yaml`` stay untouched.  Bounds: ORCA subprocess timeout
    (``optimization_control.timeout.default_seconds``) and the CENSO
    subprocess timeout (``censo.timeout_seconds``).
    """
    config = copy.deepcopy(_base_config())
    control = config.setdefault("optimization_control", {})
    if not isinstance(control, dict):
        control = {}
        config["optimization_control"] = control
    control["timeout"] = {"default_seconds": _ORCA_TIMEOUT_S}
    censo = config.setdefault("censo", {})
    if not isinstance(censo, dict):
        censo = {}
        config["censo"] = censo
    censo["timeout_seconds"] = _CENSO_TIMEOUT_S
    return config


def _context(workdir: Path, **kwargs: Any) -> Any:
    """A ``TaskContext`` bound to real configured binaries with bounded timeouts."""
    from cccp.calculation import TaskContext

    return TaskContext(config=_bounded_config(), workdir=workdir, **kwargs)


# --- structures, reference graph, connectivity ------------------------------


def _embed_smiles(smiles: str) -> _MoleculeGeometry:
    """Embed a SMILES string to 3D (the same path as ``acp run nmr --input``)."""
    from rdkit import rdBase

    from cccp.io.input_handler import MolecularInputHandler

    molecule = MolecularInputHandler.parse_smiles(smiles)
    return _MoleculeGeometry(
        symbols=tuple(str(symbol) for symbol in molecule.symbols),
        coordinates=tuple(tuple(float(c) for c in row) for row in molecule.coordinates),
        charge=int(molecule.charge),
        multiplicity=int(molecule.multiplicity),
        rdkit_version=str(rdBase.rdkitVersion),
    )


def _reference_graph(smiles: str) -> tuple[tuple[str, ...], set[tuple[int, int]]]:
    """Atom order + undirected bond graph of a SMILES, via the same RDKit order.

    ``MolecularInputHandler.parse_smiles`` builds ``MolFromSmiles`` then
    ``AddHs`` (never reordering atoms), so the reference graph's integer
    indexing matches the embedded geometry exactly.
    """
    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        raise ValueError(f"invalid SMILES: {smiles!r}")
    molecule = Chem.AddHs(molecule)
    symbols = tuple(atom.GetSymbol() for atom in molecule.GetAtoms())
    bonds: set[tuple[int, int]] = set()
    for bond in molecule.GetBonds():
        begin, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        bonds.add((min(begin, end), max(begin, end)))
    return symbols, bonds


def _connectivity_result(
    symbols: tuple[str, ...],
    coordinates: Sequence[Sequence[float]],
    reference_bonds: set[tuple[int, int]],
) -> dict[str, Any]:
    """Geometric connectivity vs the reference graph (pure function, no QC)."""
    from cccp.calculation.irc_endpoints import perceive_connectivity

    perceived = perceive_connectivity(list(symbols), np.asarray(coordinates, dtype=float))
    return {
        "method": "cccp.calculation.irc_endpoints.perceive_connectivity (covalent-radii graph)",
        "reference_bond_count": len(reference_bonds),
        "perceived_bond_count": len(perceived),
        "matched": perceived == reference_bonds,
        "missing_reference_bonds": sorted(reference_bonds - perceived),
        "spurious_perceived_bonds": sorted(perceived - reference_bonds),
    }


def _read_ensemble_frames(
    ensemble_path: Path,
    expected_symbols: tuple[str, ...],
) -> list[tuple[tuple[float, float, float], ...]]:
    """Read a multi-frame XYZ into per-frame coordinate tuples (order-checked)."""
    from cccp.utils.file_io import read_xyz_multiframe

    stacked, symbols = read_xyz_multiframe(Path(ensemble_path))
    if tuple(symbols) != expected_symbols:
        raise ValueError(f"ensemble atom order {tuple(symbols)!r} != expected {expected_symbols!r}")
    if len(symbols) == 0 or len(stacked) % len(symbols) != 0:
        raise ValueError(f"invalid ensemble shape for {ensemble_path}")
    n_atoms = len(symbols)
    frames: list[tuple[tuple[float, float, float], ...]] = []
    for index in range(len(stacked) // n_atoms):
        block = stacked[index * n_atoms : (index + 1) * n_atoms]
        frames.append(tuple(tuple(float(c) for c in row) for row in block))
    return frames


# --- evidence recorder (pure functions; exercised by the logic tests) -------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _record_section(
    evidence_dir: Path,
    case: _MoleculeCase,
    symbols: tuple[str, ...],
    section: str,
    payload: dict[str, Any],
) -> Path:
    """Merge one evidence section into ``task-51-real-qc-<molecule>.json``."""
    path = evidence_dir / f"task-51-real-qc-{case.name}.json"
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            data = loaded
    data["schema"] = _EVIDENCE_SCHEMA
    data["task"] = "todo 51 - Level-1 real QC smoke (connectivity/parsing only)"
    data["plan"] = ".omo/plans/acp-nmr-goodman-gap-remediation.md"
    data["scope"] = _SCOPE_NOTE
    data["updated_at"] = _utc_now()
    data["molecule"] = {
        "name": case.name,
        "smiles": case.smiles,
        "character": case.character,
        "symbols": list(symbols),
    }
    sections = data.setdefault("sections", {})
    sections[section] = payload
    _atomic_write_json(path, data)
    return path


def _preserve_logs(
    case_name: str,
    section: str,
    paths: Sequence[Path | None],
    evidence_log_root: Path = _EVIDENCE_LOG_ROOT,
) -> list[dict[str, str]]:
    """Copy raw QC logs beside the evidence JSON; absolute paths are recorded."""
    records: list[dict[str, str]] = []
    for source in paths:
        if source is None:
            continue
        source_path = Path(source)
        if not source_path.is_file():
            continue
        target_dir = evidence_log_root / case_name / section
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source_path.name
        shutil.copy2(source_path, target)
        records.append(
            {"source_path": str(source_path.resolve()), "evidence_path": str(target.resolve())}
        )
    return records


# --- real QC steps -----------------------------------------------------------


def _artifact_path(result: Any, artifact_type: str) -> Path | None:
    for artifact in getattr(result, "artifacts", ()) or ():
        if getattr(artifact, "type", None) == artifact_type:
            path = getattr(artifact, "path", None)
            if path is not None:
                return Path(path)
    return None


def _orca_version_line(log_path: Path | None) -> str | None:
    if log_path is None or not Path(log_path).is_file():
        return None
    text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if "Program Version" in line:
            return line.strip()
    return None


def _finite_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _run_giao_section(
    case: _MoleculeCase,
    *,
    symbols: tuple[str, ...],
    coordinates: tuple[tuple[float, float, float], ...],
    charge: int,
    multiplicity: int,
    workdir: Path,
    section_label: str,
    geometry_label: str,
) -> tuple[dict[str, Any], list[str]]:
    """Run one real ORCA GIAO task and build its evidence section.

    Returns ``(section, problems)``: the section is always returned (so a
    failing case still leaves evidence), problems lists every failed check.
    """
    from cccp.calculation import (
        MethodSpec,
        StructureInput,
        TaskKind,
        TaskRequest,
        run_nmr_shielding,
    )
    from cccp.calculation.requests import NmrShieldingOptions
    from cccp.calculation.results import NmrShieldingPayload

    workdir.mkdir(parents=True, exist_ok=True)
    request = TaskRequest(
        task=TaskKind.NMR_SHIELDING,
        structure=StructureInput(coordinates=coordinates, symbols=symbols),
        charge=charge,
        multiplicity=multiplicity,
        level=MethodSpec(method=_GIAO_METHOD, basis=_GIAO_BASIS),
        backend="orca",
        options=NmrShieldingOptions(),
        output_dir=workdir,
    )
    started_at = _utc_now()
    started = time.monotonic()
    result = run_nmr_shielding(request, context=_context(workdir))
    wall_seconds = time.monotonic() - started
    finished_at = _utc_now()

    payload = result.payload
    shieldings = dict(payload.shieldings) if isinstance(payload, NmrShieldingPayload) else {}
    parsed_symbols = tuple(result.symbols or ())
    active = {index for index, symbol in enumerate(symbols) if symbol in _NMR_ACTIVE_ELEMENTS}
    parsed = set(shieldings)
    symbol_mismatches = [
        {"atom_index": index, "parsed": shieldings[index].symbol, "expected": symbols[index]}
        for index in sorted(parsed)
        if index < len(symbols) and shieldings[index].symbol != symbols[index]
    ]

    reference_symbols, reference_bonds = _reference_graph(case.smiles)
    if reference_symbols != symbols:
        connectivity: dict[str, Any] = {
            "matched": False,
            "error": "input symbols differ from the SMILES reference atom order",
        }
    else:
        connectivity = _connectivity_result(symbols, coordinates, reference_bonds)

    orca_input = _artifact_path(result, "output")
    orca_log = _artifact_path(result, "log")
    raw_logs = _preserve_logs(case.name, section_label, [orca_input, orca_log])

    problems: list[str] = []
    if result.status != "completed":
        problems.append(f"status={result.status!r} errors={list(result.errors)!r}")
    if not result.complete:
        problems.append(f"result.complete is False; errors={list(result.errors)!r}")
    if parsed_symbols != symbols:
        problems.append(f"parsed atom order {parsed_symbols!r} != input {symbols!r}")
    if parsed != active:
        problems.append(
            f"parsed shielding atoms {sorted(parsed)} != NMR-active atoms {sorted(active)}"
        )
    if symbol_mismatches:
        problems.append(f"symbol mismatches: {symbol_mismatches!r}")
    if not connectivity.get("matched"):
        problems.append(f"connectivity mismatch: {connectivity!r}")

    section: dict[str, Any] = {
        "status": "PASS" if not problems else "FAIL",
        "label": section_label,
        "geometry": geometry_label,
        "level": {"method": _GIAO_METHOD, "basis": _GIAO_BASIS},
        "started_at": started_at,
        "finished_at": finished_at,
        "wall_seconds": round(wall_seconds, 3),
        "orca_input_path": str(orca_input.resolve()) if orca_input is not None else None,
        "orca_log_path": str(orca_log.resolve()) if orca_log is not None else None,
        "orca_version": _orca_version_line(orca_log),
        "parse_counts": {
            "atoms_parsed": len(parsed_symbols),
            "active_atoms": len(active),
            "shieldings_parsed": len(parsed),
            "conformers": 1,
        },
        "atom_identity": {
            "symbols_match_input_order": parsed_symbols == symbols,
            "missing_active_atom_indices": sorted(active - parsed),
            "unexpected_atom_indices": sorted(parsed - active),
            "symbol_mismatches": symbol_mismatches,
        },
        "connectivity": connectivity,
        "energy_hartree": _finite_or_none(result.energy_hartree),
        "errors": list(result.errors),
        "raw_logs": raw_logs,
        "limits": {"orca_timeout_seconds": _ORCA_TIMEOUT_S},
        "problems": problems,
    }
    return section, problems


def _run_crest_search(
    case: _MoleculeCase,
    geometry: _MoleculeGeometry,
    workdir: Path,
) -> tuple[Any, dict[str, Any]]:
    """Run one real CREST GFN2 conformer search (bounded by a request timeout)."""
    from cccp.calculation import (
        MethodSpec,
        StructureInput,
        TaskKind,
        TaskRequest,
        TaskResources,
        run_conformer_search,
    )
    from cccp.calculation.requests import ConformerSearchOptions

    workdir.mkdir(parents=True, exist_ok=True)
    request = TaskRequest(
        task=TaskKind.CONFORMER_SEARCH,
        structure=StructureInput(coordinates=geometry.coordinates, symbols=geometry.symbols),
        charge=geometry.charge,
        multiplicity=geometry.multiplicity,
        level=MethodSpec(),
        backend="crest",
        options=ConformerSearchOptions(gfn_level=2, energy_window=_CREST_ENERGY_WINDOW_KCAL),
        resources=TaskResources(timeout_s=_CREST_TIMEOUT_S),
        output_dir=workdir,
    )
    started_at = _utc_now()
    started = time.monotonic()
    result = run_conformer_search(request, context=_context(workdir))
    wall_seconds = time.monotonic() - started
    meta = {
        "started_at": started_at,
        "finished_at": _utc_now(),
        "wall_seconds": round(wall_seconds, 3),
        "backend": "crest",
        "gfn_level": 2,
        "energy_window_kcal": _CREST_ENERGY_WINDOW_KCAL,
        "timeout_seconds": _CREST_TIMEOUT_S,
    }
    return result, meta


def _crest_section(
    case: _MoleculeCase,
    crest_result: Any,
    crest_meta: dict[str, Any],
    reference_symbols: tuple[str, ...],
) -> tuple[dict[str, Any], list[tuple[tuple[float, float, float], ...]], list[str]]:
    """Validate a CREST search and build its evidence section + frames."""
    from cccp.calculation.results import ConformerSearchPayload

    problems: list[str] = []
    payload = crest_result.payload
    conformer_count = 0
    ensemble_path: Path | None = None
    energy_table: list[dict[str, Any]] = []
    frames: list[tuple[tuple[float, float, float], ...]] = []

    if crest_result.status != "completed" or not crest_result.complete:
        problems.append(
            f"CREST status={crest_result.status!r} errors={list(crest_result.errors)!r}"
        )
    if isinstance(payload, ConformerSearchPayload):
        conformer_count = payload.conformer_count
        if payload.ensemble_ref is not None:
            ensemble_path = Path(payload.ensemble_ref.path)
        energy_table = [
            {
                "conf_id": row.conf_id,
                "frame_index": row.frame_index,
                "energy_hartree": _finite_or_none(row.energy_hartree),
            }
            for row in payload.energy_table
        ]
    else:
        problems.append(f"unexpected CREST payload type: {type(payload).__name__}")
    if conformer_count < 2:
        problems.append(f"multi-conformer case found {conformer_count} conformers (<2)")
    if ensemble_path is not None:
        try:
            frames = _read_ensemble_frames(ensemble_path, reference_symbols)
        except (AssertionError, ValueError) as error:
            problems.append(f"ensemble unreadable: {error}")
    else:
        problems.append("CREST ensemble artifact missing")
    if frames and len(frames) != conformer_count:
        problems.append(f"ensemble frames {len(frames)} != conformer_count {conformer_count}")

    section: dict[str, Any] = {
        "search": dict(crest_meta),
        "conformer_count": conformer_count,
        "ensemble_path": str(ensemble_path.resolve()) if ensemble_path is not None else None,
        "energy_table": energy_table,
        "limits": {
            "crest_timeout_seconds": _CREST_TIMEOUT_S,
            "orca_timeout_seconds": _ORCA_TIMEOUT_S,
            "censo_timeout_seconds": _CENSO_TIMEOUT_S,
        },
    }
    return section, frames, problems


def _run_censo_light(
    ensemble_path: Path,
    geometry: _MoleculeGeometry,
    workdir: Path,
    input_base: Path,
) -> tuple[Any, dict[str, Any]]:
    """Run one real CENSO ``censo-light`` refinement of a CREST ensemble."""
    from cccp.calculation import (
        MethodSpec,
        StructureInput,
        TaskKind,
        TaskRequest,
        run_censo_refine,
    )
    from cccp.calculation.requests import CensoRefineOptions

    workdir.mkdir(parents=True, exist_ok=True)
    request = TaskRequest(
        task=TaskKind.CENSO_REFINE,
        structure=StructureInput(path=ensemble_path),
        charge=geometry.charge,
        multiplicity=geometry.multiplicity,
        level=MethodSpec(),
        options=CensoRefineOptions(preset="censo-light"),
        output_dir=workdir,
    )
    started_at = _utc_now()
    started = time.monotonic()
    result = run_censo_refine(request, context=_context(workdir, input_base=input_base))
    wall_seconds = time.monotonic() - started
    meta = {
        "started_at": started_at,
        "finished_at": _utc_now(),
        "wall_seconds": round(wall_seconds, 3),
        "preset": "censo-light",
        "timeout_seconds": _CENSO_TIMEOUT_S,
    }
    return result, meta


# --- real smoke tests (level 1: connectivity/parse only) ---------------------


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
@pytest.mark.parametrize("case", _CASES, ids=[case.name for case in _CASES])
def test_giao_initial_geometry_real_smoke(case: _MoleculeCase, tmp_path: Path) -> None:
    """ORCA GIAO on the RDKit-embedded geometry: parse + connectivity only."""
    geometry = _embed_smiles(case.smiles)
    reference_symbols, reference_bonds = _reference_graph(case.smiles)
    assert geometry.symbols == reference_symbols, (
        "embedded atom order must match the SMILES reference graph"
    )

    section, problems = _run_giao_section(
        case,
        symbols=geometry.symbols,
        coordinates=geometry.coordinates,
        charge=geometry.charge,
        multiplicity=geometry.multiplicity,
        workdir=tmp_path / "giao_initial",
        section_label="giao_initial_geometry",
        geometry_label=(f"RDKit ETKDGv3(seed=42)+MMFF initial geometry ({geometry.rdkit_version})"),
    )
    section["molecule_source"] = {
        "smiles": case.smiles,
        "rdkit_version": geometry.rdkit_version,
        "embedding": "ETKDGv3 seed=42 + MMFF94",
    }
    section["reference_bonds"] = sorted(reference_bonds)
    _record_section(_EVIDENCE_DIR, case, geometry.symbols, "giao_initial_geometry", section)
    assert not problems, f"{case.name}: " + "; ".join(problems)


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
@requires_crest
@pytest.mark.parametrize("case", _CONFORMER_CASES, ids=[case.name for case in _CONFORMER_CASES])
def test_crest_giao_multi_conformer_real_smoke(case: _MoleculeCase, tmp_path: Path) -> None:
    """CREST GFN2 conformers -> ORCA GIAO on the two lowest: parse + connectivity."""
    geometry = _embed_smiles(case.smiles)
    reference_symbols, reference_bonds = _reference_graph(case.smiles)
    assert geometry.symbols == reference_symbols

    crest_dir = tmp_path / "crest"
    crest_result, crest_meta = _run_crest_search(case, geometry, crest_dir)
    section, frames, problems = _crest_section(case, crest_result, crest_meta, reference_symbols)

    ranked = sorted(
        (row for row in section["energy_table"] if row["energy_hartree"] is not None),
        key=lambda row: row["energy_hartree"],
    )
    shielded: list[dict[str, Any]] = []
    for rank, row in enumerate(ranked[:_MAX_SHIELDED_CONFORMERS]):
        index = int(row["frame_index"])
        if not 0 <= index < len(frames):
            problems.append(f"frame_index {index} outside ensemble ({len(frames)} frames)")
            continue
        giao_section, giao_problems = _run_giao_section(
            case,
            symbols=reference_symbols,
            coordinates=frames[index],
            charge=geometry.charge,
            multiplicity=geometry.multiplicity,
            workdir=crest_dir / "giao" / f"conf_{index:03d}",
            section_label=f"crest_giao_multi_conformer/conf_{index:03d}",
            geometry_label=f"CREST GFN2 conformer frame_index={index} (energy rank {rank + 1})",
        )
        giao_section["conf_id"] = row["conf_id"]
        giao_section["frame_index"] = index
        giao_section["energy_hartree"] = row["energy_hartree"]
        shielded.append(giao_section)
        problems.extend(f"{case.name}: {problem}" for problem in giao_problems)
    if len(shielded) < min(_MAX_SHIELDED_CONFORMERS, len(ranked)):
        problems.append(
            f"shielded {len(shielded)} conformers but ranking had {len(ranked)} usable rows"
        )

    section["shielded_conformers"] = shielded
    section["parse_counts_total"] = {
        "conformers_shielded": len(shielded),
        "atoms_parsed": sum(entry["parse_counts"]["atoms_parsed"] for entry in shielded),
        "shieldings_parsed": sum(entry["parse_counts"]["shieldings_parsed"] for entry in shielded),
    }
    section["status"] = "PASS" if not problems else "FAIL"
    section["problems"] = problems
    _record_section(_EVIDENCE_DIR, case, reference_symbols, "crest_giao_multi_conformer", section)
    assert not problems, f"{case.name}: " + "; ".join(problems)


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
@requires_crest
@requires_censo
def test_crest_censo_giao_chain_real_smoke(tmp_path: Path) -> None:
    """CREST -> CENSO censo-light -> ORCA GIAO: full chain parse + connectivity."""
    from cccp.calculation.results import CensoRefinePayload

    case = _CHAIN_CASE
    geometry = _embed_smiles(case.smiles)
    reference_symbols, reference_bonds = _reference_graph(case.smiles)
    assert geometry.symbols == reference_symbols

    crest_dir = tmp_path / "crest"
    crest_result, crest_meta = _run_crest_search(case, geometry, crest_dir)
    section, frames, problems = _crest_section(case, crest_result, crest_meta, reference_symbols)

    censo_dir = tmp_path / "censo"
    ensemble_path = Path(section["ensemble_path"]) if section["ensemble_path"] else None
    records_payload: list[dict[str, Any]] = []
    best_conf_id: str | None = None
    best_frame_index: int | None = None
    refined_ensemble_path: str | None = None
    if ensemble_path is None:
        problems.append("cannot run CENSO without the CREST ensemble artifact")
        censo_meta: dict[str, Any] = {}
        best = None
    else:
        censo_result, censo_meta = _run_censo_light(ensemble_path, geometry, censo_dir, tmp_path)
        censo_payload = censo_result.payload
        if censo_result.status != "completed" or not censo_result.complete:
            problems.append(
                f"CENSO status={censo_result.status!r} errors={list(censo_result.errors)!r}"
            )
        best = None
        if isinstance(censo_payload, CensoRefinePayload):
            for record in censo_payload.records:
                records_payload.append(
                    {
                        "conf_id": record.conf_id,
                        "frame_index": record.frame_index,
                        "energy_hartree": _finite_or_none(record.energy_hartree),
                        "free_energy_hartree": _finite_or_none(record.free_energy_hartree),
                        "weight": _finite_or_none(record.weight),
                    }
                )
            if censo_payload.refined_ensemble_ref is not None:
                refined_ensemble_path = str(Path(censo_payload.refined_ensemble_ref.path).resolve())
            weights = [row["weight"] for row in records_payload if row["weight"] is not None]
            if weights and not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-3):
                problems.append(f"CENSO weights sum {sum(weights)} != 1 within 1e-3")
            if not records_payload:
                problems.append("CENSO returned no surviving records")
            elif weights:
                best = max(censo_payload.records, key=lambda row: float(row.weight))
            else:
                best = min(
                    censo_payload.records,
                    key=lambda row: (
                        row.free_energy_hartree
                        if row.free_energy_hartree is not None
                        else float("inf")
                    ),
                )
        else:
            problems.append(f"unexpected CENSO payload type: {type(censo_payload).__name__}")

    giao_section: dict[str, Any] | None = None
    if best is not None:
        best_conf_id = str(best.conf_id)
        best_frame_index = int(best.frame_index)
        if 0 <= best_frame_index < len(frames):
            giao_section, giao_problems = _run_giao_section(
                case,
                symbols=reference_symbols,
                coordinates=frames[best_frame_index],
                charge=geometry.charge,
                multiplicity=geometry.multiplicity,
                workdir=censo_dir / "giao",
                section_label=f"crest_censo_giao_chain/giao_{best_conf_id}",
                geometry_label=(
                    "CREST ensemble frame at CensoRefineRecord.frame_index "
                    f"({best_conf_id}, original-frame identity)"
                ),
            )
            giao_section["conf_id"] = best_conf_id
            giao_section["frame_index"] = best_frame_index
            problems.extend(f"{case.name}: {problem}" for problem in giao_problems)
        else:
            problems.append(
                f"CENSO best frame_index {best_frame_index} outside ensemble ({len(frames)} frames)"
            )

    section["censo"] = {
        **censo_meta,
        "records": records_payload,
        "best_conf_id": best_conf_id,
        "best_frame_index": best_frame_index,
        "refined_ensemble_path": refined_ensemble_path,
    }
    section["giao"] = giao_section
    section["status"] = "PASS" if not problems else "FAIL"
    section["problems"] = problems
    _record_section(_EVIDENCE_DIR, case, reference_symbols, "crest_censo_giao_chain", section)
    assert not problems, f"{case.name}: " + "; ".join(problems)


# --- no-binary logic tests (evidence path + connectivity helpers) ------------


def test_evidence_recorder_logic(tmp_path: Path) -> None:
    """Evidence merge/atomic-write path, without any QC binary."""
    case = _CASES[0]
    symbols = ("C", "C", "H", "H", "H", "H", "H", "H")
    path = _record_section(tmp_path, case, symbols, "synthetic_one", {"value": 1})
    assert path == tmp_path / "task-51-real-qc-ethane.json"
    _record_section(tmp_path, case, symbols, "synthetic_two", {"value": [1, 2]})

    # A corrupt stale file must not abort evidence collection.
    path.write_text("{not json", encoding="utf-8")
    _record_section(tmp_path, case, symbols, "synthetic_three", {"value": 3})

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["schema"] == _EVIDENCE_SCHEMA
    assert data["scope"] == _SCOPE_NOTE
    assert data["molecule"]["name"] == "ethane"
    assert data["molecule"]["symbols"] == list(symbols)
    assert data["sections"]["synthetic_three"] == {"value": 3}
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["task-51-real-qc-ethane.json"]
    assert not list(tmp_path.glob("*.tmp")), "atomic write must not leave temp files"


def test_connectivity_and_reference_graph_logic() -> None:
    """Reference-graph atom order + covalent-radii connectivity, no QC binary."""
    for case in _CASES:
        reference_symbols, reference_bonds = _reference_graph(case.smiles)
        geometry = _embed_smiles(case.smiles)
        assert geometry.symbols == reference_symbols, (
            f"{case.name}: embedded order must match the reference graph"
        )
        assert reference_bonds, f"{case.name}: reference graph must have bonds"

    water_symbols = ("O", "H", "H")
    water_coordinates = (
        (0.0, 0.0, 0.1173),
        (0.0, 0.7572, -0.4692),
        (0.0, -0.7572, -0.4692),
    )
    reference = {(0, 1), (0, 2)}
    intact = _connectivity_result(water_symbols, water_coordinates, reference)
    assert intact["matched"] is True
    assert intact["missing_reference_bonds"] == []
    assert intact["spurious_perceived_bonds"] == []

    broken = _connectivity_result(
        water_symbols,
        ((0.0, 0.0, 0.1173), (0.0, 0.7572, -0.4692), (5.0, 5.0, 5.0)),
        reference,
    )
    assert broken["matched"] is False
    assert broken["missing_reference_bonds"] == [(0, 2)]
    assert broken["spurious_perceived_bonds"] == []
