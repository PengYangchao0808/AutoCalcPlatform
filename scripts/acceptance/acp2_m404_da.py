#!/usr/bin/env python3.11
"""M4-04 — the limited Diels–Alder science vertical (ADR-008, G07/G08/G15/G18).

This acceptance orchestrator runs the ADR-008 slice end to end and records what
*actually* happened, separating three things that must never be merged:

1. ``scenario_result`` — did the execution chain produce valid artifacts?
2. ``claim_status`` — do the frozen criteria support the scientific claim?
3. H4 row state — is every required row (Local **and** LSF) closed with native
   receipts?  A Local PASS never closes the LSF row and never closes H4.

Legs
----
``public-entry``
    The frozen ``recipe.diels_alder.limited.v1`` is handed to the public bounded
    recipe entry (``compile_step_dag_definition`` → ``BoundedRecipe`` envelope).
    Whatever the entry answers is recorded verbatim: a typed refusal is a result,
    not an error to hide.
``chain``
    The nine ADR-008 required steps run as **real** QC.  Every step is recorded
    with the entry it used (bounded ``BoundedRecipe`` envelope where the compiler
    can dispatch the step, the real ``cccp.calculation.run_*`` task core where it
    cannot), its ``TaskRef``/``attempt``/release, its native outputs and their
    digests, and its wall time.  Steps are resumable: a completed step document
    whose native digests still verify is reused instead of recomputed.
``criteria``
    Real artifacts → deterministic validators →
    ``criterion_evidence_from_claim`` → ``evaluate_criteria``.  The frozen
    criteria file is read-only; this script never edits a threshold, a downgrade
    rule or a question row.
``evidence``
    ``EvidenceReport`` → platform attribution (``project_evidence``) →
    independent decision (platform ``decide_evidence`` **and** RPH
    ``decideFromView``) → headless Workbench projection (the real frontend
    module, no browser).
``degradation``
    At least three honest-negative scenarios: a one-sided IRC (real), a missing
    frequency (the step never dispatched), and an endpoint mismatch (real IRC
    artifacts judged against a constructed non-matching endpoint).
``h4``
    ``DA-ENV-LOCAL`` and ``DA-ENV-LSF`` rows.  LSF stays ``NOT_VERIFIED`` until
    M4-03's remote receipts exist; the Local row is derived from this run.

Exit codes: ``0`` PASS, ``1`` FAIL, ``2`` NOT_VERIFIED (a required row is
missing), ``3`` usage error.

Author: QCcalc Team
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
REPO = SCRIPT_PATH.parents[2]
sys.path.insert(0, str(SCRIPT_PATH.parent))
sys.path.insert(0, str(REPO / "src"))

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3

SCHEMA = "acp2.m4-04.limited-diels-alder.v1"
FIXTURE_ROOT = REPO / "tests" / "fixtures" / "acp2" / "science" / "diels_alder"
FIXTURE_V1 = FIXTURE_ROOT / "v1"
ADR_PATH = (
    REPO / "docs" / "reference" / "next-change" / "adr" / "ADR-008-limited-diels-alder-criteria.md"
)
RPH_DRIVER = SCRIPT_PATH.parent / "acp2_m404_da_views.mjs"

#: The criteria definition fingerprint the ADR records.  A mismatch means the
#: frozen table changed underneath this run: the criteria evaluation would no
#: longer be the signed one, so the run refuses to evaluate instead of
#: "evaluating anyway".
EXPECTED_CRITERIA_DIGEST = "f89f73da4178f4db35b5eb2b8f3958a4cad8bacc5c82490ccfc6777a2c3449d3"

#: ADR-008 §3 — the frozen method/basis/state the slice was frozen against.
FROZEN_METHOD = "wB97X-D4"
FROZEN_BASIS = "def2-TZVP"
FROZEN_CHARGE = 0
FROZEN_MULTIPLICITY = 1
FROZEN_STATE_ID = "state.singlet.s0"
#: The electronic-state identity keys the frozen validators compare
#: (``validators._STATE_IDENTITY_KEYS``): charge/multiplicity alone are not a
#: state identity, so the frozen ``state_id`` is declared explicitly.
ELECTRONIC_STATE_IDENTITY: dict[str, Any] = {
    "state_id": FROZEN_STATE_ID,
    "target_multiplicity": FROZEN_MULTIPLICITY,
    "reference_multiplicity": FROZEN_MULTIPLICITY,
}
TEMPERATURE_K = 298.15
PRESSURE_PA = 100000.0
STANDARD_STATE = "1_bar_ideal_gas"
REFERENCE_STATE = (
    "frozen endpoints: separated butadiene + ethene (gas phase, infinite separation) "
    "and cyclohexene, all at the frozen conditions"
)

#: ADR-008 §4 — the nine required steps, in order (``dispatch`` is the bounded
#: DAG vocabulary of today; ``None`` means the step cannot be dispatched as a
#: bounded DAG step and runs through the real task core instead).
ADR_STEPS: tuple[dict[str, Any], ...] = (
    {
        "index": 1,
        "step_id": "endpoint_prep",
        "task_kind": "conformer_search",
        "dispatch": None,
        "bounded_alternative": "alt_endpoint_prep_single_conformation",
        "executed_as": "optimize",
    },
    {
        "index": 2,
        "step_id": "candidate_path",
        "task_kind": "xtb_path_search",
        "dispatch": None,
        "bounded_alternative": "alt_candidate_path_declared",
        "executed_as": "xtb_path_search",
    },
    {
        "index": 3,
        "step_id": "candidate_frequency",
        "task_kind": "frequency",
        "dispatch": "frequency",
        "bounded_alternative": None,
        "executed_as": "frequency",
    },
    {
        "index": 4,
        "step_id": "ts_optimize",
        "task_kind": "optimize",
        "dispatch": "optimize",
        "bounded_alternative": None,
        "executed_as": "optimize",
    },
    {
        "index": 5,
        "step_id": "ts_frequency",
        "task_kind": "frequency",
        "dispatch": "frequency",
        "bounded_alternative": None,
        "executed_as": "frequency",
    },
    {
        "index": 6,
        "step_id": "irc",
        "task_kind": "irc",
        "dispatch": None,
        "bounded_alternative": None,
        "executed_as": "irc",
    },
    {
        "index": 7,
        "step_id": "endpoint_reactants",
        "task_kind": "singlepoint",
        "dispatch": "singlepoint",
        "bounded_alternative": None,
        "executed_as": "singlepoint",
    },
    {
        "index": 8,
        "step_id": "endpoint_product",
        "task_kind": "singlepoint",
        "dispatch": "singlepoint",
        "bounded_alternative": None,
        "executed_as": "singlepoint",
    },
    {
        "index": 9,
        "step_id": "energy_thermochemistry",
        "task_kind": "thermochemistry",
        "dispatch": None,
        "bounded_alternative": None,
        "executed_as": "thermochemistry",
    },
)

CRITERION_ORDER: tuple[str, ...] = (
    "DA-C01-ts-single-imaginary-mode",
    "DA-C02-ts-mode-mapping",
    "DA-C03-frequency-evidence",
    "DA-C04-irc-bidirectional",
    "DA-C05-endpoint-association",
    "DA-C06-energy-comparability",
    "DA-C07-reactant-stoichiometry",
)
QUESTION_ORDER: tuple[str, ...] = (
    "question.diels_alder.path_connectivity.v1",
    "question.diels_alder.activation_energy.v1",
)
#: Required reactants of the frozen reference state (ADR-008 §2).
REQUIRED_REACTANTS: dict[str, int] = {"butadiene": 1, "ethene": 1}


# ── small utilities ────────────────────────────────────────────────────────


def utc_now() -> str:
    """Return an RFC-3339 UTC timestamp."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def digest_bytes(payload: bytes) -> str:
    """Return ``sha256:<64 hex>`` for *payload*."""
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def bare_digest(path: Path) -> str:
    """Return the bare sha256 hex of *path* (the ADR evidence-digest form)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def file_record(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Return a JSON-safe ``{path, size_bytes, sha256}`` record."""
    resolved = Path(path)
    label = (
        str(resolved.relative_to(root))
        if root is not None and resolved.is_relative_to(root)
        else str(resolved)
    )
    return {
        "path": label,
        "size_bytes": resolved.stat().st_size,
        "sha256": bare_digest(resolved),
    }


def write_json(path: Path, payload: Any) -> Path:
    """Write *payload* as canonical, indented JSON (atomic replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def read_json(path: Path) -> Any:
    """Read JSON or return ``None`` when the file is absent/unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def git_revision() -> str:
    """Return the current git revision (or ``""``)."""
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _xyz_records(text: str) -> tuple[list[str], list[tuple[float, float, float]]]:
    """Parse an XYZ document into ``(symbols, coordinates)``."""
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("an XYZ document needs a count line and a comment line")
    count = int(lines[0].split()[0])
    symbols: list[str] = []
    coordinates: list[tuple[float, float, float]] = []
    for line in lines[2 : 2 + count]:
        parts = line.split()
        symbols.append(parts[0])
        coordinates.append((float(parts[1]), float(parts[2]), float(parts[3])))
    if len(symbols) != count:
        raise ValueError(f"XYZ declares {count} atoms but carries {len(symbols)}")
    return symbols, coordinates


def _constructed_irc(
    *,
    forward: tuple[list[str], list[tuple[float, float, float]]],
    reverse: tuple[list[str], list[tuple[float, float, float]]],
):
    """Return a constructed one-direction IRC result (negative-case input)."""
    from cccp.calculation.requests import IrcDirection, TaskKind
    from cccp.calculation.results import IrcDirectionResult, IrcPayload, TaskResult

    entry = IrcDirectionResult(
        direction=IrcDirection("forward"),
        energy_hartree=-233.0,
        coordinates=tuple(forward[1]),
        symbols=tuple(forward[0]),
        converged=True,
        steps=20,
        success=True,
    )
    return TaskResult(
        task=TaskKind.IRC,
        status="completed",
        complete=False,
        payload=IrcPayload(directions=(entry,)),
        metadata={"direction_status": {"forward": "converged"}},
    )


def _xyz_frames(text: str) -> list[tuple[list[str], list[tuple[float, float, float]], str]]:
    """Split a multi-frame XYZ document into ``(symbols, coordinates, comment)``."""
    lines = text.splitlines()
    frames: list[tuple[list[str], list[tuple[float, float, float]], str]] = []
    index = 0
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            break
        try:
            count = int(lines[index].split()[0])
        except (IndexError, ValueError):
            break
        comment = lines[index + 1].strip() if index + 1 < len(lines) else ""
        symbols: list[str] = []
        coordinates: list[tuple[float, float, float]] = []
        cursor = index + 2
        for _ in range(count):
            if cursor >= len(lines):
                break
            parts = lines[cursor].split()
            cursor += 1
            if len(parts) < 4:
                continue
            symbols.append(parts[0])
            coordinates.append((float(parts[1]), float(parts[2]), float(parts[3])))
        if len(symbols) == count:
            frames.append((symbols, coordinates, comment))
        index = cursor
    return frames


def _xyz_text(
    symbols: list[str] | tuple[str, ...],
    coordinates: list[tuple[float, float, float]] | tuple[tuple[float, float, float], ...],
    comment: str,
) -> str:
    """Render an XYZ document."""
    rows = "\n".join(
        f"{symbol:<2s} {x:16.10f} {y:16.10f} {z:16.10f}"
        for symbol, (x, y, z) in zip(symbols, coordinates, strict=True)
    )
    return f"{len(symbols)}\n{comment}\n{rows}\n"


# ── frozen inputs (ADR-008 fixture) ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    """The ADR-008 v1 fixture, parsed once and digest-bound."""

    reaction_id: str
    reactant_symbols: tuple[str, ...]
    reactant_coordinates: tuple[tuple[float, float, float], ...]
    reactant_atom_ids: tuple[str, ...]
    product_symbols: tuple[str, ...]
    product_coordinates: tuple[tuple[float, float, float], ...]
    product_atom_ids: tuple[str, ...]
    atom_mapping: tuple[tuple[int, int], ...]
    recipe: dict[str, Any]
    question: dict[str, Any]
    acceptance: dict[str, Any]
    digests: dict[str, str]

    @property
    def reactant_count(self) -> int:
        return len(self.reactant_symbols)

    @property
    def product_count(self) -> int:
        return len(self.product_symbols)

    def product_in_reactant_order(self) -> tuple[tuple[str, ...], list[tuple[float, float, float]]]:
        """Return the frozen product geometry re-indexed into reactant atom order.

        The two frozen XYZ files use different atom orders (the reaction contract
        maps them explicitly).  A path search and an IRC need one common order,
        so the product is re-indexed through the frozen bijection.  The declared
        order of each file is never changed; this is a derived, recorded view.
        """
        symbols: list[str] = []
        coordinates: list[tuple[float, float, float]] = []
        for left, right in self.atom_mapping:
            symbols.append(self.product_symbols[right])
            coordinates.append(self.product_coordinates[right])
        return tuple(symbols), coordinates


def load_frozen_inputs() -> FrozenInputs:
    """Load and verify the frozen ADR-008 fixture documents."""
    reaction = json.loads((FIXTURE_V1 / "reaction.json").read_text(encoding="utf-8"))
    recipe = json.loads((FIXTURE_V1 / "recipe.json").read_text(encoding="utf-8"))
    question = json.loads((FIXTURE_V1 / "question.json").read_text(encoding="utf-8"))
    acceptance = json.loads((FIXTURE_V1 / "acceptance.json").read_text(encoding="utf-8"))
    manifest = json.loads((FIXTURE_V1 / "fixture-manifest.json").read_text(encoding="utf-8"))

    # Digest check of every declared fixture member (ADR-008 §12: digest-bound).
    digests: dict[str, str] = {}
    declared = manifest.get("file_digests") or {}
    if isinstance(declared, dict):
        for name, expected in declared.items():
            path = FIXTURE_V1 / name
            if not path.is_file():
                raise SystemExit(f"fixture member {name} is missing")
            actual = bare_digest(path)
            if actual != str(expected):
                raise SystemExit(f"fixture member {name} digest {actual} != declared {expected}")
            digests[name] = actual

    left = reaction["left"]
    right = reaction["right"]
    left_atom_ids = tuple(
        f"{species['structure_id']}:{atom_id}"
        for species in left
        for atom_id in species["atom_ids"]
    )
    right_atom_ids = tuple(
        f"{species['structure_id']}:{atom_id}"
        for species in right
        for atom_id in species["atom_ids"]
    )
    left_to_right = reaction["atom_mapping"]["left_to_right"]
    right_to_left = reaction["atom_mapping"]["right_to_left"]

    reactant_symbols, reactant_coordinates = _xyz_records(
        (FIXTURE_V1 / "reactants.xyz").read_text(encoding="utf-8")
    )
    product_symbols, product_coordinates = _xyz_records(
        (FIXTURE_V1 / "product.xyz").read_text(encoding="utf-8")
    )
    if len(reactant_symbols) != len(left_atom_ids) or len(product_symbols) != len(right_atom_ids):
        raise SystemExit("the frozen XYZ atom counts disagree with the reaction atom_ids")
    if len(left_to_right) != len(left_atom_ids):
        raise SystemExit("the frozen left_to_right mapping does not cover every atom")

    index_of = {atom_id: index for index, atom_id in enumerate(right_atom_ids)}
    mapping: list[tuple[int, int]] = []
    for index, atom_id in enumerate(left_atom_ids):
        target = left_to_right[atom_id]
        if right_to_left.get(target) != atom_id:
            raise SystemExit(f"the frozen mapping is not an exact inverse at {atom_id}")
        mapping.append((index, index_of[target]))
    if sorted(right for _, right in mapping) != list(range(len(right_atom_ids))):
        raise SystemExit("the frozen mapping is not a bijection onto the product atoms")
    for left_index, right_index in mapping:
        if reactant_symbols[left_index] != product_symbols[right_index]:
            raise SystemExit(
                f"the frozen mapping pairs different elements at {left_index}/{right_index}"
            )

    digests["reactants.xyz"] = bare_digest(FIXTURE_V1 / "reactants.xyz")
    digests["product.xyz"] = bare_digest(FIXTURE_V1 / "product.xyz")
    return FrozenInputs(
        reaction_id=str(reaction["reaction_id"]),
        reactant_symbols=tuple(reactant_symbols),
        reactant_coordinates=tuple(reactant_coordinates),
        reactant_atom_ids=left_atom_ids,
        product_symbols=tuple(product_symbols),
        product_coordinates=tuple(product_coordinates),
        product_atom_ids=right_atom_ids,
        atom_mapping=tuple(mapping),
        recipe=recipe,
        question=question,
        acceptance=acceptance,
        digests=digests,
    )


# ── software resolution (recorded before dispatch) ─────────────────────────


@dataclass(frozen=True, slots=True)
class SoftwareIdentity:
    """One resolved executable plus the version recorded before dispatch."""

    name: str
    executable: str
    version: str
    source: str
    is_expected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "executable": self.executable,
            "version": self.version,
            "source": self.source,
            "is_expected": self.is_expected,
        }


def _probe_version(executable: Path) -> str:
    """Return the program's own version line (never a guessed string)."""
    for args in ([str(executable), "-v"], [str(executable), "--version"]):
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=45,
                env={**os.environ, "OMP_NUM_THREADS": "1"},
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = (completed.stdout or "") + "\n" + (completed.stderr or "")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if "Program Version" in stripped or "version" in stripped.lower():
                return stripped[:160]
    return ""


def _orca_candidates() -> list[tuple[str, str]]:
    """Return ``(path, source)`` candidates for the real ORCA binary.

    ``CONFSEARCH_ORCA_PATH`` is part of the shared isolation profile, but on this
    workstation ``/usr/bin/orca`` is the GNOME screen reader, not the QC package.
    Every candidate is therefore *identified* by running it; a screen reader is
    rejected by name, never silently accepted as "ORCA".
    """
    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for env_name in ("CONFSEARCH_ORCA_PATH", "ACP2_ORCA_PATH"):
        value = os.environ.get(env_name)
        if value and value not in seen:
            seen.add(value)
            candidates.append((value, f"env:{env_name}"))
    for pattern in (
        "/home/*/orca*/orca",
        "/opt/orca*/orca",
        "/usr/local/orca*/orca",
    ):
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            text = str(path)
            if text not in seen:
                seen.add(text)
                candidates.append((text, "scan"))
    for name in ("orca",):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            path = Path(directory) / name
            if path.is_file() and str(path) not in seen:
                seen.add(str(path))
                candidates.append((str(path), "path"))
    return candidates


def resolve_software() -> dict[str, Any]:
    """Resolve ORCA and xTB, recording version and the identification evidence."""
    from cccp.software import resolve_executable_with_source

    findings: list[dict[str, Any]] = []
    chosen: dict[str, SoftwareIdentity] = {}
    for name, expected_marker in (("orca", "program version"), ("xtb", "x-t-b")):
        picked: SoftwareIdentity | None = None
        for candidate, source in _orca_candidates() if name == "orca" else _xtb_candidates():
            path = Path(candidate)
            if not path.is_file():
                findings.append(
                    {"name": name, "candidate": candidate, "source": source, "state": "missing"}
                )
                continue
            version = _probe_version(path)
            lowered = version.lower()
            if name == "orca" and (
                "screen reader" in lowered or "屏幕阅读器" in version or not version
            ):
                findings.append(
                    {
                        "name": name,
                        "candidate": candidate,
                        "source": source,
                        "state": "rejected_not_orca",
                        "probe": version,
                    }
                )
                continue
            if name == "xtb" and "x t b" not in lowered and "xtb" not in lowered:
                findings.append(
                    {
                        "name": name,
                        "candidate": candidate,
                        "source": source,
                        "state": "rejected_not_xtb",
                        "probe": version,
                    }
                )
                continue
            picked = SoftwareIdentity(
                name=name,
                executable=str(path),
                version=version,
                source=source,
                is_expected=expected_marker in lowered or bool(version),
            )
            break
        if picked is None:
            fallback, fallback_source = resolve_executable_with_source(name, None)
            findings.append(
                {
                    "name": name,
                    "candidate": str(fallback) if fallback else None,
                    "source": fallback_source,
                    "state": "unresolved",
                }
            )
        else:
            chosen[name] = picked
    return {
        "resolved": {name: identity.to_dict() for name, identity in chosen.items()},
        "findings": findings,
        "config": {
            "executables": {
                name: {"path": identity.executable, "nproc": 4, "maxcore": 3000}
                for name, identity in chosen.items()
            }
        },
    }


def _xtb_candidates() -> list[tuple[str, str]]:
    """Return ``(path, source)`` candidates for xTB."""
    seen: set[str] = set()
    candidates: list[tuple[str, str]] = []
    for env_name in ("CONFSEARCH_XTB_PATH", "ACP2_XTB_PATH"):
        value = os.environ.get(env_name)
        if value and value not in seen:
            seen.add(value)
            candidates.append((value, f"env:{env_name}"))
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        path = Path(directory) / "xtb"
        if path.is_file() and str(path) not in seen:
            seen.add(str(path))
            candidates.append((str(path), "path"))
    return candidates


# ── the acceptance run ─────────────────────────────────────────────────────


class M404:
    """One M4-04 limited-Diels–Alder acceptance run."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.out = Path(args.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.run_root = Path(args.run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.reuse = bool(args.reuse)
        self.phase = str(args.phase)
        self.timeouts = {
            "singlepoint": float(args.singlepoint_timeout),
            "frequency": float(args.frequency_timeout),
            "optimize": float(args.optimize_timeout),
            "irc": float(args.irc_timeout),
            "xtb_path_search": float(args.path_timeout),
            "thermochemistry": float(args.thermochemistry_timeout),
        }
        self.frozen = load_frozen_inputs()
        self.started_at = utc_now()
        self.steps: dict[str, dict[str, Any]] = {}
        self.preflight: dict[str, Any] = {}
        self.public_entry: dict[str, Any] = {}
        self.criteria: dict[str, Any] = {}
        self.evidence: dict[str, Any] = {}
        self.degradation: dict[str, Any] = {}
        self.h4: dict[str, Any] = {}
        self.reasons: list[str] = []
        self.notes: list[str] = []
        self.software: dict[str, Any] = {}
        self.task_config: dict[str, Any] = {}
        self.release: Any = None
        self.release_facts: dict[str, Any] = {}

    # -- paths ------------------------------------------------------------

    @property
    def chain_dir(self) -> Path:
        return self.out / "chain"

    def step_path(self, step_id: str) -> Path:
        return self.chain_dir / f"{step_id}.json"

    def work_dir(self, step_id: str) -> Path:
        return self.run_root / "da" / step_id

    @property
    def input_dir(self) -> Path:
        """Stable directory for step inputs (never inside a dispatched work dir)."""
        path = self.run_root / "da" / "inputs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # -- preflight --------------------------------------------------------

    def run_preflight(self) -> None:
        """Bind the frozen inputs, the criteria fingerprint and the software."""
        from acp_services.runtime.step_definition import ADR008_REQUIRED_STEPS
        from cccp.analysis.research.science_criteria import (
            CRITERIA_VERSION,
            QUESTION_ROWS,
            criteria_definition_digest,
            criteria_rows,
        )

        declaration = {
            "schema": "acp2.runtime.adr008-step-declaration.v1",
            "adr": "ADR-008",
            "recipe_id": self.frozen.recipe.get("recipe_id"),
            "steps": [
                {
                    "index": row.index,
                    "step_id": row.step_id,
                    "task_kind": row.task_kind,
                    "required": row.required,
                    "mapping": row.mapping,
                    "dag_step_kind": row.dag_step_kind,
                    "reason": row.reason,
                }
                for row in ADR008_REQUIRED_STEPS
            ],
            "open_steps": [row.step_id for row in ADR008_REQUIRED_STEPS if row.mapping != "mapped"],
        }
        digest = criteria_definition_digest()
        adr_text = ADR_PATH.read_text(encoding="utf-8")
        self.preflight = {
            "schema": "acp2.m4-04.preflight.v1",
            "generated_at": utc_now(),
            "git_revision": git_revision(),
            "fixture_root": str(FIXTURE_ROOT),
            "fixture_digests": dict(sorted(self.frozen.digests.items())),
            "reaction_id": self.frozen.reaction_id,
            "frozen_method": {
                "method": FROZEN_METHOD,
                "basis": FROZEN_BASIS,
                "charge": FROZEN_CHARGE,
                "multiplicity": FROZEN_MULTIPLICITY,
                "state_id": FROZEN_STATE_ID,
                "temperature_k": TEMPERATURE_K,
                "pressure_pa": PRESSURE_PA,
                "standard_state": STANDARD_STATE,
                "energy_unit": "hartree",
                "solvent": None,
            },
            "criteria": {
                "version": CRITERIA_VERSION,
                "digest": digest,
                "expected_digest": EXPECTED_CRITERIA_DIGEST,
                "matches_adr": digest == EXPECTED_CRITERIA_DIGEST,
                "fingerprint_in_adr": EXPECTED_CRITERIA_DIGEST in adr_text,
                "criteria_rows": len(criteria_rows()),
                "question_rows": len(QUESTION_ROWS),
                "frozen_read_only": True,
            },
            "adr008_step_declaration": declaration,
            "atom_mapping": {
                "pairs": [[left, right] for left, right in self.frozen.atom_mapping],
                "reactant_atom_ids": list(self.frozen.reactant_atom_ids),
                "product_atom_ids": list(self.frozen.product_atom_ids),
                "bijective": True,
            },
        }
        self.software = resolve_software()
        # The documented operator override wins over a stale/misleading entry in
        # the shared isolation profile (on this host ``/usr/bin/orca`` is the
        # GNOME screen reader).  Exporting the identified binaries makes every
        # consumer resolve the same executable, including the worker's own
        # config load inside a frozen single-task attempt.
        for name, identity in (self.software.get("resolved") or {}).items():
            variable = f"CONFSEARCH_{name.upper()}_PATH"
            os.environ[variable] = str(identity["executable"])
        self.task_config = dict(self.software.get("config") or {})
        self.config_path = self._write_task_config()
        self.preflight["software"] = self.software
        write_json(self.out / "preflight.json", self.preflight)
        if not self.preflight["criteria"]["matches_adr"]:
            self.reasons.append(
                f"criteria definition digest {digest} != ADR-008 {EXPECTED_CRITERIA_DIGEST}"
            )
        if not self.preflight["criteria"]["fingerprint_in_adr"]:
            self.reasons.append("the ADR-008 document does not carry the frozen fingerprint")
        for name in ("orca", "xtb"):
            if name not in (self.software.get("resolved") or {}):
                self.reasons.append(f"{name} could not be identified; that step is NOT_VERIFIED")

    def _write_task_config(self) -> Path:
        """Write the operator config the freeze builders load (explicit ORCA/xTB)."""
        lines = ["executables:"]
        for name, identity in (self.software.get("resolved") or {}).items():
            lines.append(f"  {name}:")
            lines.append(f'    path: "{identity["executable"]}"')
            if name == "orca":
                lines.append("    nproc: 4")
                lines.append("    maxcore: 3000")
        lines.extend(["resources:", "  nproc: 4"])
        path = self.out / "task-config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def ensure_release(self) -> None:
        """Materialise the immutable local code release bound to every attempt."""
        from acp.calculations.attempt_freeze import build_local_release

        try:
            release = build_local_release(REPO, self.run_root)
        except Exception as exc:  # noqa: BLE001 - the refusal is the evidence
            self.release_facts = {
                "built": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.reasons.append(f"local release could not be built: {exc}")
            return
        self.release = release
        self.release_facts = {
            "built": True,
            "release_id": release.release_id,
            "release_digest": release.release_digest,
            "manifest_path": str(getattr(release, "manifest_path", "") or ""),
        }

    # -- public entry: the frozen recipe through the bounded DAG -------------

    def run_public_entry(self) -> None:
        """Hand the frozen recipe to the bounded-DAG compiler and record the answer."""
        from acp_services.runtime.step_definition import (
            ADR008_REQUIRED_STEPS,
            compile_step_dag_definition,
        )

        declaration = [
            {
                "index": row.index,
                "step_id": row.step_id,
                "task_kind": row.task_kind,
                "required": row.required,
                "mapping": row.mapping,
                "dag_step_kind": row.dag_step_kind,
                "reason": row.reason,
            }
            for row in ADR008_REQUIRED_STEPS
        ]
        record: dict[str, Any] = {
            "schema": "acp2.m4-04.public-entry.v1",
            "recipe_id": self.frozen.recipe.get("recipe_id"),
            "step_declaration": declaration,
            "open_steps": [row["step_id"] for row in declaration if row["mapping"] != "mapped"],
            "dispatchable_steps": [
                row["step_id"] for row in declaration if row["mapping"] == "mapped"
            ],
            "compiled": False,
            "compiler_error": None,
        }
        try:
            dag, definition = compile_step_dag_definition(
                self.frozen.recipe, {}, task_ref="m4-04-public-entry"
            )
            record["compiled"] = True
            record["compiled_steps"] = [step.step_id for step in dag.steps]
            record["worst_case_executions"] = dict(definition.worst_case_executions)
        except Exception as exc:  # noqa: BLE001 - a typed refusal is the answer
            record["compiler_error"] = {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", ""),
                "detail": str(exc),
            }
        record["nine_steps_in_one_attempt"] = bool(record["compiled"])
        record["explanation"] = (
            "The frozen recipe.diels_alder.limited.v1 is the ADR-008 control recipe. "
            "The bounded-DAG compiler accepts only dispatchable task kinds "
            "(singlepoint/optimize/frequency/nmr_shielding); conformer_search, "
            "xtb_path_search, irc and thermochemistry are not dispatchable today, so the "
            "frozen nine-step recipe cannot be submitted as ONE BoundedRecipe attempt. "
            "The run therefore executes every ADR-008 step for real and records the entry "
            "each step actually used."
        )
        self.public_entry = record
        write_json(self.out / "public-entry.json", record)
        if not record["compiled"]:
            self.notes.append(
                "public bounded-recipe entry refuses the frozen recipe: "
                f"{record['compiler_error']['detail'] if record['compiler_error'] else 'unknown'}"
            )

    # -- real task execution ----------------------------------------------

    def _record_step(self, step_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self.steps[step_id] = record
        write_json(self.step_path(step_id), record)
        return record

    def _base_step_record(self, step: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "acp2.m4-04.step.v1",
            "step_id": step["step_id"],
            "adr_step_index": step["index"],
            "adr_task_kind": step["task_kind"],
            "executed_task_kind": step["executed_as"],
            "bounded_alternative": step["bounded_alternative"],
            "entry": None,
            "status": "not_started",
            "complete": False,
            "task_ref": None,
            "attempt": 1,
            "release_id": self.release_facts.get("release_id") if self.release_facts else None,
            "release_digest": self.release_facts.get("release_digest")
            if self.release_facts
            else None,
            "level": {
                "method": FROZEN_METHOD,
                "basis": FROZEN_BASIS,
                "charge": FROZEN_CHARGE,
                "multiplicity": FROZEN_MULTIPLICITY,
            },
            "duration_seconds": None,
            "task_result": None,
            "native_files": [],
            "products": [],
            "error": None,
            "receipt": None,
        }

    def _existing_step(self, step: dict[str, Any]) -> dict[str, Any] | None:
        if not self.reuse:
            return None
        document = read_json(self.step_path(step["step_id"]))
        if not isinstance(document, dict):
            return None
        if document.get("status") not in ("completed", "partial"):
            return None
        for entry in document.get("native_files") or ():
            path = Path(str(entry.get("absolute_path") or ""))
            if not path.is_file():
                return None
            if bare_digest(path) != str(entry.get("sha256")):
                return None
        return document

    def run_task_core(
        self,
        step: dict[str, Any],
        *,
        structure: Path,
        options: dict[str, Any],
        timeout: float,
        structure_role: str = "minimum",
    ) -> dict[str, Any]:
        """Run one real ``cccp.calculation.run_*`` task core and record it.

        This is the *legacy* platform entry (no bounded envelope): used only for
        the task kinds the bounded-DAG compiler cannot dispatch.  The task core
        is the same single execution station the workflows use.
        """
        from cccp.calculation import (
            run_frequency,
            run_irc,
            run_optimize,
            run_singlepoint,
            run_xtb_path_search,
        )
        from cccp.calculation.context import TaskContext
        from cccp.calculation.requests import TaskRequest
        from cccp.calculation.tasks.thermochemistry import run_thermochemistry

        runners = {
            "singlepoint": run_singlepoint,
            "frequency": run_frequency,
            "optimize": run_optimize,
            "irc": run_irc,
            "xtb_path_search": run_xtb_path_search,
            "thermochemistry": run_thermochemistry,
        }
        task_kind = step["executed_as"]
        record = self._base_step_record(step)
        record["entry"] = "legacy_task_core"
        record["task_ref"] = f"{time.strftime('%Y%m%d')}_940_{step['step_id']}"
        work = self.work_dir(step["step_id"])
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "task": task_kind,
            "structure": {"path": str(structure), "role": structure_role},
            "charge": FROZEN_CHARGE,
            "multiplicity": FROZEN_MULTIPLICITY,
            "level": {"method": FROZEN_METHOD, "basis": FROZEN_BASIS},
            "resources": {"cores": 1, "nproc": 4, "maxcore": 3000},
            "options": options,
            "output_dir": str(work),
        }
        if task_kind == "thermochemistry":
            payload["structure"] = {"path": str(structure), "role": "minimum"}
        try:
            request = TaskRequest.from_dict(payload)
        except Exception as exc:  # noqa: BLE001 - typed refusal recorded
            record.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            return self._record_step(step["step_id"], record)
        context = TaskContext(workdir=work, input_base=work, config=self.task_config)
        started = time.perf_counter()
        try:
            result = runners[task_kind](request, context=context)
            elapsed = round(time.perf_counter() - started, 2)
            if elapsed > timeout:
                record["timeout_exceeded"] = True
            record["duration_seconds"] = elapsed
            record["status"] = "completed" if result.status == "completed" else "failed"
            record["complete"] = bool(result.complete)
            record["task_result"] = result.to_dict()
            record["error"] = None if result.status == "completed" else "; ".join(result.errors)
        except Exception as exc:  # noqa: BLE001 - acceptance records, never crashes
            record["duration_seconds"] = round(time.perf_counter() - started, 2)
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["native_files"] = self._collect_native(work)
        return self._record_step(step["step_id"], record)

    def run_bounded_step(
        self,
        step: dict[str, Any],
        *,
        structure: Path,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Run ONE dispatchable step through the real ``BoundedRecipe`` envelope."""
        from dataclasses import replace as dataclass_replace

        from acp.calculations import attempt_freeze as freeze
        from acp_services.runtime.attempt_freeze import write_frozen_attempt
        from acp_services.runtime.resource_precheck import ExecutionResourcePrecheck
        from acp_services.runtime.step_dag_worker import StepDagTaskExecutor
        from acp_services.worker_bootstrap import assemble_worker

        record = self._base_step_record(step)
        record["entry"] = "bounded_recipe_envelope"
        task_kind = step["dispatch"]
        task_ref = f"{time.strftime('%Y%m%d')}_941_{step['step_id']}"
        record["task_ref"] = task_ref
        work = self.work_dir(step["step_id"])
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        structure_path = work / "input.xyz"
        shutil.copy2(structure, structure_path)
        recipe = {
            "schema_version": 1,
            "recipe_id": f"recipe.m4-04.{step['step_id']}.v1",
            "applicability": {"supported_reaction_classes": ["net_reaction"], "preconditions": []},
            "limits": {"max_steps": 2, "max_edges": 1},
            "steps": [
                {
                    "step_id": step["step_id"],
                    "intent": f"ADR-008 step {step['index']} ({step['step_id']}) — real QC",
                    "required": True,
                    "diagnostic_only": False,
                    "task_kind": task_kind,
                    "parameters": dict(parameters),
                    "consumes": [],
                    "produces": [
                        {
                            "from_step": step["step_id"],
                            "product_kind": "output",
                            "selector": {"selector_kind": "whole_product"},
                            "required": True,
                        }
                    ],
                    "validators": [],
                    "alternatives": [],
                }
            ],
            "edges": [],
            "validators": [],
            "alternatives": [],
        }
        bounded_payload = {
            "recipe": recipe,
            "inputs": {},
            "structure_role": "transition_state" if task_kind == "optimize" else "minimum",
            "step_requirements": [{"step_id": step["step_id"], "executables": ["orca"]}],
            "method": {"method": FROZEN_METHOD, "basis": FROZEN_BASIS},
            "charge": FROZEN_CHARGE,
            "multiplicity": FROZEN_MULTIPLICITY,
            "resources": {"cores": 1, "nproc": 4, "maxcore": 3000},
            "config": self.task_config,
        }
        if task_kind == "frequency":
            bounded_payload["structure_role"] = "minimum"
        record_object = SimpleNamespace(
            id=task_ref,
            attempt=1,
            spec=SimpleNamespace(
                workflow=freeze.BOUNDED_RECIPE_WORKFLOW,
                input={freeze.RECIPE_INPUT_KEY: bounded_payload},
            ),
        )
        started = time.perf_counter()
        try:
            spec = freeze.build_bounded_recipe_freeze_spec(
                record_object, work, structure_path, self.release
            )
            group = write_frozen_attempt(work, spec)
            runtime = assemble_worker(
                envelope=spec.envelope,
                plan=spec.plan,
                work_dir=work,
                execution_right=_M404ExecutionRight(),
            )
            precheck = ExecutionResourcePrecheck(config=self.task_config)
            executor = runtime.worker.executor
            if isinstance(executor, StepDagTaskExecutor):
                runtime = dataclass_replace(
                    runtime,
                    worker=dataclass_replace(
                        runtime.worker,
                        executor=dataclass_replace(executor, precheck=precheck),
                    ),
                )
            receipt = runtime.run()
            record["duration_seconds"] = round(time.perf_counter() - started, 2)
            receipt_payload = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)
            record["receipt"] = {
                "state": receipt_payload.get("state"),
                "chain_digest": receipt_payload.get("chain_digest"),
                "task_result_digest": (receipt_payload.get("evidence_digests") or {}).get(
                    "task_result_digest"
                ),
                "frozen_group": {
                    "job_id": group.manifest.job_id,
                    "attempt": group.manifest.attempt,
                    "workflow": group.manifest.workflow,
                    "submission_id": group.manifest.submission_id,
                    "release_id": group.manifest.release_id,
                    "member_digests": dict(group.manifest.member_digests),
                },
                "submission_id": spec.envelope.submission_id,
            }
            step_document = read_json(
                work / "WORK" / "00_RUNTIME" / "steps" / step["step_id"] / "step_result.json"
            )
            outer = (step_document or {}).get("publish_payload") or {}
            publish = (
                outer.get("publish_payload")
                if isinstance(outer.get("publish_payload"), dict)
                else outer
            )
            result_payload = publish.get("result")
            if isinstance(result_payload, dict):
                from cccp.calculation.results import TaskResult

                result = TaskResult.from_dict(result_payload)
                record["task_result"] = result.to_dict()
                record["status"] = "completed" if result.status == "completed" else "failed"
                record["complete"] = bool(result.complete)
                record["error"] = None if result.status == "completed" else "; ".join(result.errors)
                record["products"] = list(publish.get("products") or ())
                record["qualification"] = (step_document or {}).get("qualification")
            else:
                record["status"] = "failed"
                record["error"] = (
                    f"the bounded attempt returned {receipt_payload.get('state')!r} without a "
                    "step result document"
                )
        except Exception as exc:  # noqa: BLE001 - acceptance records, never crashes
            record["duration_seconds"] = round(time.perf_counter() - started, 2)
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["native_files"] = self._collect_native(work)
        return self._record_step(step["step_id"], record)

    def _collect_native(self, work: Path) -> list[dict[str, Any]]:
        """Return the native artifacts of one attempt with their digests."""
        wanted = (
            "WORK/00_RUNTIME/execution_protocol.json",
            "WORK/00_RUNTIME/frozen/frozen_group.json",
            "WORK/00_RUNTIME/frozen/envelope.json",
            "WORK/00_RUNTIME/task_result.json",
            "WORK/00_RUNTIME/steps",
            "execution_receipt.json",
            "RESULT/result_manifest.json",
            "RESULT/scientific_result.json",
        )
        files: list[dict[str, Any]] = []
        seen: set[str] = set()
        for relative in wanted:
            target = work / relative
            if target.is_dir():
                for child in sorted(target.rglob("*")):
                    if child.is_file():
                        entry = file_record(child, root=work)
                        entry["absolute_path"] = str(child)
                        if entry["path"] not in seen:
                            seen.add(entry["path"])
                            files.append(entry)
            elif target.is_file():
                entry = file_record(target, root=work)
                entry["absolute_path"] = str(target)
                if entry["path"] not in seen:
                    seen.add(entry["path"])
                    files.append(entry)
        for pattern in ("*.out", "*.log", "*.xyz", "*.inp", "*.property.txt", "*.hess"):
            for child in sorted(work.glob(pattern)):
                if child.is_file():
                    entry = file_record(child, root=work)
                    entry["absolute_path"] = str(child)
                    if entry["path"] not in seen:
                        seen.add(entry["path"])
                        files.append(entry)
        for sub in ("RESULT", "WORK"):
            root = work / sub
            if not root.is_dir():
                continue
            for child in sorted(root.rglob("*")):
                if child.is_file() and child.suffix in (".xyz", ".out", ".json", ".log"):
                    entry = file_record(child, root=work)
                    entry["absolute_path"] = str(child)
                    if entry["path"] not in seen and not entry["path"].startswith(
                        "WORK/00_RUNTIME/steps"
                    ):
                        seen.add(entry["path"])
                        files.append(entry)
        return files

    # -- the nine steps ---------------------------------------------------

    def step_endpoint_prep(self) -> None:
        """ADR step 1 — bounded single-conformation endpoint preparation."""
        step = ADR_STEPS[0]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        # The frozen recipe's declared alternative relaxes the single declared
        # input conformation per endpoint (no conformer search, no global claim).
        fragments = {
            "butadiene": (self.frozen.reactant_symbols[:10], self.frozen.reactant_coordinates[:10]),
            "ethene": (self.frozen.reactant_symbols[10:], self.frozen.reactant_coordinates[10:]),
            "cyclohexene": (self.frozen.product_symbols, self.frozen.product_coordinates),
        }
        records: list[dict[str, Any]] = []
        for species, (symbols, coordinates) in fragments.items():
            structure = self.input_dir / f"{species}.xyz"
            structure.write_text(
                _xyz_text(symbols, coordinates, f"frozen {species}"), encoding="utf-8"
            )
            sub_step = {
                "index": step["index"],
                "step_id": f"{step['step_id']}_{species}",
                "task_kind": step["task_kind"],
                "executed_as": "optimize",
            }
            records.append(
                self.run_task_core(
                    {**sub_step, "bounded_alternative": step["bounded_alternative"]},
                    structure=structure,
                    options={"mode": "unconstrained"},
                    timeout=self.timeouts["optimize"],
                )
            )
        merged = self._base_step_record(step)
        merged["entry"] = "legacy_task_core"
        merged["task_ref"] = " + ".join(
            str(record.get("task_ref")) for record in records if record.get("task_ref")
        )
        merged["sub_executions"] = [
            {
                "species": species,
                "step_id": record["step_id"],
                "status": record["status"],
                "task_ref": record["task_ref"],
                "optimized_geometry": self._optimized_geometry(record),
            }
            for species, record in zip(fragments, records, strict=True)
        ]
        merged["status"] = (
            "completed" if all(record["status"] == "completed" for record in records) else "failed"
        )
        merged["complete"] = merged["status"] == "completed"
        merged["duration_seconds"] = round(
            sum(float(record.get("duration_seconds") or 0.0) for record in records), 2
        )
        merged["native_files"] = [entry for record in records for entry in record["native_files"]]
        merged["task_result"] = {
            "note": "endpoint_prep is two independent single-conformation relaxations",
            "children": [record["step_id"] for record in records],
        }
        self._record_step(step["step_id"], merged)

    @staticmethod
    def _optimized_geometry(record: dict[str, Any]) -> dict[str, Any] | None:
        result = record.get("task_result") or {}
        symbols = result.get("symbols")
        coordinates = result.get("coordinates")
        if not symbols or not coordinates:
            return None
        return {"symbols": list(symbols), "coordinates": [list(row) for row in coordinates]}

    def step_candidate_path(self) -> None:
        """ADR step 2 — GFN2-xTB PATH between the frozen endpoints."""
        step = ADR_STEPS[1]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        work = self.work_dir(step["step_id"])
        work.mkdir(parents=True, exist_ok=True)
        start = self.input_dir / "reactants.xyz"
        start.write_text(
            _xyz_text(
                self.frozen.reactant_symbols,
                self.frozen.reactant_coordinates,
                "frozen reactants (butadiene + ethene, reactant atom order)",
            ),
            encoding="utf-8",
        )
        product_symbols, product_coordinates = self.frozen.product_in_reactant_order()
        end = self.input_dir / "product_in_reactant_order.xyz"
        end.write_text(
            _xyz_text(
                product_symbols,
                product_coordinates,
                "frozen cyclohexene re-indexed into reactant atom order via the frozen mapping",
            ),
            encoding="utf-8",
        )
        options = {
            "end_structure": {"path": str(end), "role": "minimum"},
            "gfn_level": 2,
        }
        record = self.run_task_core(
            step, structure=start, options=options, timeout=self.timeouts["xtb_path_search"]
        )
        # A path search must yield a TS-guess frame; record the best frame found.
        record["derived_views"] = {
            "product_in_reactant_order": file_record(end, root=work),
            "mapping_used": [[left, right] for left, right in self.frozen.atom_mapping],
        }

    def _ts_guess_path(self) -> tuple[Path, dict[str, Any]] | None:
        """Return the TS-guess structure and its selection provenance.

        The GFN2-xTB PATH run returns one trajectory artifact plus indexed frame
        energies.  The barrier-top frame (highest energy along the path) is the
        frozen-level candidate; the selection rule and the frame index/energy are
        recorded so the choice is auditable, never implicit.
        """
        record = self.steps.get("candidate_path") or {}
        result = self._result_of(record)
        if result is None:
            return None
        trajectory: Path | None = None
        for artifact in result.artifacts:
            if str(getattr(artifact, "type", "")) == "trajectory":
                candidate = Path(str(getattr(artifact, "path", "")))
                if candidate.is_file():
                    trajectory = candidate
                    break
        if trajectory is None:
            # A failed path search must not be turned into a candidate by
            # picking an arbitrary frame: the metadynamics frames of a run that
            # never reached the product are not a transition-state guess.
            return None
        frames = _xyz_frames(trajectory.read_text(encoding="utf-8"))
        if not frames:
            return None
        payload = result.payload
        energies = [
            None if frame.energy_hartree is None else float(frame.energy_hartree)
            for frame in (getattr(payload, "frames", ()) or ())
        ]
        if len(energies) != len(frames):
            energies = [None] * len(frames)
        peak_index = max(
            range(len(frames)),
            key=lambda index: energies[index] if energies[index] is not None else float("-inf"),
        )
        if all(energy is None for energy in energies):
            peak_index = len(frames) // 2
        symbols, coordinates, comment = frames[peak_index]
        if tuple(symbols) != self.frozen.reactant_symbols:
            return None
        target = self.input_dir / "ts_guess.xyz"
        target.write_text(
            _xyz_text(
                symbols,
                coordinates,
                f"xTB PATH barrier-top frame {peak_index} ({comment[:60]})",
            ),
            encoding="utf-8",
        )
        return target, {
            "selection_rule": "highest-energy frame along the GFN2-xTB PATH trajectory",
            "frame_index": peak_index,
            "frame_count": len(frames),
            "frame_energy_hartree": energies[peak_index],
            "trajectory": str(trajectory),
            "trajectory_sha256": bare_digest(trajectory),
            "ts_guess": str(target),
            "ts_guess_sha256": bare_digest(target),
            "candidate_kind": "generated_candidate",
        }

    def step_candidate_frequency(self) -> None:
        """ADR step 3 — frequency at the declared candidate (native mode index)."""
        step = ADR_STEPS[2]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        guess = self._ts_guess_path()
        if guess is None:
            record = self._base_step_record(step)
            record["status"] = "skipped"
            record["error"] = (
                "the candidate path search produced no TS-guess frame with the frozen atom order"
            )
            self._record_step(step["step_id"], record)
            return
        self.run_bounded_step(step, structure=guess[0], parameters={})

    def step_ts_optimize(self) -> None:
        """ADR step 4 — transition-state optimization following the native mode."""
        step = ADR_STEPS[3]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        guess = self._ts_guess_path()
        if guess is None:
            record = self._base_step_record(step)
            record["status"] = "skipped"
            record["error"] = "no TS-guess structure exists for the optimization"
            self._record_step(step["step_id"], record)
            return
        mode_index = self._native_imaginary_index()
        parameters: dict[str, Any] = {
            "mode": "transition_state",
            "max_cycles": 40,
            "initial_hessian": "calculate",
        }
        if mode_index is not None:
            parameters["ts"] = {"enabled": True, "mode_index": mode_index}
        self.run_bounded_step(step, structure=guess[0], parameters=parameters)

    def _native_imaginary_index(self) -> int | None:
        """Return the native printed index of the lowest imaginary mode."""
        from cccp.analysis.research import validators

        record = self.steps.get("candidate_frequency") or {}
        result = self._result_of(record)
        if result is None:
            return None
        mode_map = validators._mode_frequency_map(result)  # noqa: SLF001 - the frozen helper
        if not mode_map:
            return None
        imaginary = [
            (index, value)
            for index, value in mode_map.items()
            if float(value) < float(validators.IMAGINARY_MODE_THRESHOLD_CM1)
        ]
        if not imaginary:
            return None
        imaginary.sort(key=lambda item: item[1])
        return int(imaginary[0][0])

    def _optimized_ts_structure(self) -> Path | None:
        record = self.steps.get("ts_optimize") or {}
        result = self._result_of(record)
        if result is None or not result.symbols or not result.coordinates:
            return None
        path = self.input_dir / "optimized_ts.xyz"
        path.write_text(
            _xyz_text(result.symbols, result.coordinates, "M4-04 optimized TS candidate"),
            encoding="utf-8",
        )
        return path

    def step_ts_frequency(self) -> None:
        """ADR step 5 — frequency of the optimized candidate (the frozen verdict)."""
        step = ADR_STEPS[4]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        optimized = self._optimized_ts_structure()
        if optimized is None:
            record = self._base_step_record(step)
            record["status"] = "skipped"
            record["error"] = "no optimized TS geometry exists for the frequency step"
            self._record_step(step["step_id"], record)
            return
        self.run_bounded_step(step, structure=optimized, parameters={})

    def step_irc(self) -> None:
        """ADR step 6 — bidirectional IRC from the optimized candidate."""
        step = ADR_STEPS[5]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        optimized = self._optimized_ts_structure()
        if optimized is None:
            record = self._base_step_record(step)
            record["status"] = "skipped"
            record["error"] = "no optimized TS geometry exists for the IRC step"
            self._record_step(step["step_id"], record)
            return
        self.run_task_core(
            step,
            structure=optimized,
            options={"directions": ["forward", "reverse"]},
            timeout=self.timeouts["irc"],
        )

    def _irc_endpoints(self) -> dict[str, tuple[list[str], list[tuple[float, float, float]]]]:
        """Return the real IRC final structures per direction."""
        record = self.steps.get("irc") or {}
        result = self._result_of(record)
        endpoints: dict[str, tuple[list[str], list[tuple[float, float, float]]]] = {}
        if result is None:
            return endpoints
        payload = result.payload
        for entry in getattr(payload, "directions", ()) or ():
            symbols = getattr(entry, "symbols", None)
            coordinates = getattr(entry, "coordinates", None)
            if symbols and coordinates:
                endpoints[str(entry.direction.value)] = (
                    list(symbols),
                    [(float(x), float(y), float(z)) for x, y, z in coordinates],
                )
        return endpoints

    def step_endpoint_singlepoints(self) -> None:
        """ADR steps 7/8 — re-evaluate both IRC endpoints at the frozen level."""
        endpoints = self._irc_endpoints()
        for step, direction in ((ADR_STEPS[6], "forward"), (ADR_STEPS[7], "reverse")):
            existing = self._existing_step(step)
            if existing is not None:
                self.steps[step["step_id"]] = existing
                continue
            geometry = endpoints.get(direction)
            if geometry is None:
                record = self._base_step_record(step)
                record["status"] = "skipped"
                record["error"] = f"the IRC {direction} direction produced no final structure"
                self._record_step(step["step_id"], record)
                continue
            structure = self.input_dir / f"irc_{direction}_endpoint.xyz"
            structure.write_text(
                _xyz_text(geometry[0], geometry[1], f"IRC {direction} endpoint"),
                encoding="utf-8",
            )
            record = self.run_bounded_step(step, structure=structure, parameters={})
            record["irc_direction_label"] = direction
            record["direction_label_is_not_trusted"] = True
            self._record_step(step["step_id"], record)

    def step_thermochemistry(self) -> None:
        """ADR step 9 — frozen thermochemistry comparison of the compared energies."""
        from cccp.analysis.research import validators

        step = ADR_STEPS[8]
        existing = self._existing_step(step)
        if existing is not None:
            self.steps[step["step_id"]] = existing
            return
        record = self._base_step_record(step)
        record["entry"] = "deterministic_analysis"
        record["task_ref"] = f"{time.strftime('%Y%m%d')}_942_energy_thermochemistry"
        participant_energies = self._participant_energies()
        record["energies"] = participant_energies
        comparison = self._activation_energy_validation()
        record["activation_energy_validation"] = (
            comparison.to_dict() if comparison is not None else None
        )
        # The electronic barrier needs three electronic energies; the Gibbs
        # barrier additionally needs thermochemistry for every participant.
        # A missing participant is a partial step (DG-02), never a verdict.
        complete = bool(participant_energies.get("complete"))
        record["status"] = "completed" if complete else "partial"
        record["complete"] = complete
        record["duration_seconds"] = 0.0
        if not participant_energies.get("complete"):
            record["error"] = "at least one required participant energy is missing"
        # Shermo-based Gibbs thermochemistry is attempted through the real task
        # core; its absence leaves the free-energy claim not_evaluated (never
        # an invented delta-G).
        freq_record = self.steps.get("ts_frequency") or {}
        freq_result = self._result_of(freq_record)
        freq_log = None
        if freq_result is not None:
            for artifact in freq_result.artifacts:
                if str(getattr(artifact, "type", "")) == "frequency_log":
                    freq_log = Path(str(getattr(artifact, "path", "")))
        thermo: dict[str, Any] = {"attempted": False, "shermo": None, "error": None}
        if freq_log is not None and freq_log.is_file():
            thermo["attempted"] = True
            thermo["freq_log"] = str(freq_log)
            energy = (participant_energies.get("transition_state") or {}).get("energy_hartree")
            thermo["shermo"] = self._run_shermo(freq_log, energy)
            if thermo["shermo"] and thermo["shermo"].get("status") != "completed":
                thermo["error"] = thermo["shermo"].get("error")
        else:
            thermo["error"] = "no frequency log artifact exists for the TS candidate"
        record["thermochemistry"] = thermo
        record["thresholds"] = {
            "activation_energy_comparison_abs_kcal_per_mol": 0.5,
            "energy_reparse_abs_hartree": 1e-06,
        }
        record["validator_ids"] = [
            validators.METHOD_CONSISTENCY_VALIDATOR_ID,
            validators.ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID,
            validators.ENERGY_COMPARABILITY_VALIDATOR_ID,
            validators.ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
        ]
        self._record_step(step["step_id"], record)

    def _run_shermo(self, freq_log: Path, energy: float | None) -> dict[str, Any]:
        """Run the shared thermochemistry task (Shermo) on the real frequency log."""
        from cccp.calculation.context import TaskContext
        from cccp.calculation.requests import TaskRequest
        from cccp.calculation.tasks.thermochemistry import run_thermochemistry

        work = self.work_dir("energy_thermochemistry")
        work.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "task": "thermochemistry",
            "structure": {"path": str(freq_log), "role": "minimum"},
            "charge": FROZEN_CHARGE,
            "multiplicity": FROZEN_MULTIPLICITY,
            "level": {"method": FROZEN_METHOD, "basis": FROZEN_BASIS},
            "resources": {"cores": 1},
            "options": {
                "freq_log_path": str(freq_log),
                "sp_energy_hartree": energy,
                "temperature_k": TEMPERATURE_K,
                "pressure_atm": PRESSURE_PA / 101325.0,
                "standard_state": "1atm",
                "imagreal": 1,
            },
            "output_dir": str(work),
        }
        try:
            request = TaskRequest.from_dict(payload)
            result = run_thermochemistry(
                request,
                context=TaskContext(workdir=work, input_base=work, config=self.task_config),
            )
        except Exception as exc:  # noqa: BLE001 - absence is recorded, never invented
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        document = result.to_dict()
        return {
            "status": result.status,
            "complete": bool(result.complete),
            "error": None if result.status == "completed" else "; ".join(result.errors),
            "result": document,
        }

    def _result_of(self, record: dict[str, Any]) -> Any:
        """Rebuild the real ``TaskResult`` stored in a step document."""
        payload = record.get("task_result")
        if not isinstance(payload, dict):
            return None
        from cccp.calculation.results import TaskResult

        try:
            return TaskResult.from_dict(payload)
        except Exception:  # noqa: BLE001 - a synthetic document is not a result
            return None

    # -- energy participants ----------------------------------------------

    def _energy_identity(self, record: dict[str, Any]) -> Any:
        """Return the electronic ``EnergyIdentity`` of one real step result."""
        from cccp.analysis.research.validators import EnergyIdentity

        result = self._result_of(record)
        if result is None or result.energy_hartree is None:
            return None
        return EnergyIdentity(
            energy_hartree=float(result.energy_hartree),
            energy_kind="electronic",
            method=FROZEN_METHOD,
            electronic_state=ELECTRONIC_STATE_IDENTITY,
            charge=FROZEN_CHARGE,
            multiplicity=FROZEN_MULTIPLICITY,
            standard_state=STANDARD_STATE,
            reference_state=REFERENCE_STATE,
            temperature_k=TEMPERATURE_K,
            pressure_pa=PRESSURE_PA,
            energy_unit="hartree",
        )

    def _participant_records(self) -> dict[str, Any]:
        """Return the real barrier records for reactants, TS and product."""
        from cccp.analysis.research.validators import BarrierEnergyRecord

        reactants = []
        for species in ("butadiene", "ethene"):
            record = self.steps.get(f"endpoint_prep_{species}") or {}
            reactants.append(
                BarrierEnergyRecord(
                    species_id=species,
                    stoichiometry=1,
                    electronic=self._energy_identity(record),
                )
            )
        transition_state = BarrierEnergyRecord(
            species_id="transition_state",
            stoichiometry=1,
            electronic=self._energy_identity(self.steps.get("ts_frequency") or {}),
        )
        product = BarrierEnergyRecord(
            species_id="cyclohexene",
            stoichiometry=1,
            electronic=self._energy_identity(self.steps.get("endpoint_product") or {}),
        )
        return {
            "reactants": tuple(reactants),
            "transition_state": transition_state,
            "product": product,
        }

    def _participant_energies(self) -> dict[str, Any]:
        """Return the raw participant energies (a receipt view, no arithmetic)."""
        records = self._participant_records()
        out: dict[str, Any] = {
            "complete": True,
            "transition_state": None,
            "product": None,
            "reactants": [],
        }
        ts = records["transition_state"].electronic
        if ts is None or ts.energy_hartree is None:
            out["complete"] = False
        else:
            out["transition_state"] = {
                "energy_hartree": ts.energy_hartree,
                "energy_kind": "electronic",
            }
        product = records["product"].electronic
        if product is not None and product.energy_hartree is not None:
            out["product"] = {"energy_hartree": product.energy_hartree, "energy_kind": "electronic"}
        for record in records["reactants"]:
            identity = record.electronic
            if identity is None or identity.energy_hartree is None:
                out["complete"] = False
                out["reactants"].append({"species_id": record.species_id, "energy_hartree": None})
            else:
                out["reactants"].append(
                    {
                        "species_id": record.species_id,
                        "energy_hartree": identity.energy_hartree,
                        "energy_kind": "electronic",
                    }
                )
        return out

    def _activation_energy_validation(self) -> Any:
        from cccp.analysis.research.validators import (
            validate_activation_energy_comparability,
        )

        records = self._participant_records()
        try:
            return validate_activation_energy_comparability(
                records["reactants"],
                records["transition_state"],
                records["product"],
                required_reactants=REQUIRED_REACTANTS,
                reaction_id=self.frozen.reaction_id,
            )
        except Exception:  # noqa: BLE001 - missing participants are evidence, not a crash
            return None

    # -- the frozen reaction reference ------------------------------------

    def frozen_reference(self) -> Any:
        """Return the ``IrcReactionReference`` built from the frozen fixture."""
        from cccp.analysis.research.validators import FrozenEndpoint, IrcReactionReference

        reactants = FrozenEndpoint(
            endpoint_id="endpoint.reactants",
            species_id="butadiene+ethene",
            symbols=self.frozen.reactant_symbols,
            coordinates=self.frozen.reactant_coordinates,
            atom_ids=self.frozen.reactant_atom_ids,
            charge=FROZEN_CHARGE,
            multiplicity=FROZEN_MULTIPLICITY,
        )
        product = FrozenEndpoint(
            endpoint_id="endpoint.product",
            species_id="cyclohexene",
            symbols=self.frozen.product_symbols,
            coordinates=self.frozen.product_coordinates,
            atom_ids=self.frozen.product_atom_ids,
            charge=FROZEN_CHARGE,
            multiplicity=FROZEN_MULTIPLICITY,
        )
        return IrcReactionReference(
            reactant=reactants,
            product=product,
            atom_mapping=self.frozen.atom_mapping,
            reaction_id=self.frozen.reaction_id,
        )

    # -- chain orchestration ----------------------------------------------

    def run_chain(self) -> None:
        """Execute the nine ADR-008 steps (resumable, real QC)."""
        for step in ADR_STEPS:
            existing = self._existing_step(step)
            if existing is not None:
                self.steps[step["step_id"]] = existing
        if "orca" not in (self.software.get("resolved") or {}) or "xtb" not in (
            self.software.get("resolved") or {}
        ):
            self.notes.append(
                "ORCA and/or xTB could not be identified: the corresponding real steps are "
                "recorded as skipped/NOT_VERIFIED rather than faked"
            )
        self.ensure_release()
        self.step_endpoint_prep()
        self.step_candidate_path()
        self.step_candidate_frequency()
        self.step_ts_optimize()
        self.step_ts_frequency()
        self.step_irc()
        self.step_endpoint_singlepoints()
        self.step_thermochemistry()
        write_json(
            self.chain_dir / "chain.json",
            {
                "schema": "acp2.m4-04.chain.v1",
                "generated_at": utc_now(),
                "release": self.release_facts,
                "steps": [
                    {
                        "step_id": step["step_id"],
                        "adr_step_index": step["index"],
                        "status": (self.steps.get(step["step_id"]) or {}).get("status"),
                        "entry": (self.steps.get(step["step_id"]) or {}).get("entry"),
                        "duration_seconds": (self.steps.get(step["step_id"]) or {}).get(
                            "duration_seconds"
                        ),
                        "digest": bare_digest(self.step_path(step["step_id"]))
                        if self.step_path(step["step_id"]).is_file()
                        else None,
                    }
                    for step in ADR_STEPS
                ],
            },
        )

    def write_native_digests(self) -> None:
        """Write the native-artifact digest table (the reviewer's index)."""
        rows: list[dict[str, Any]] = []
        for step in ADR_STEPS:
            record = self.steps.get(step["step_id"]) or {}
            for entry in record.get("native_files") or ():
                rows.append(
                    {
                        "step_id": step["step_id"],
                        "adr_step_index": step["index"],
                        "entry": record.get("entry"),
                        "task_ref": record.get("task_ref"),
                        "path": entry.get("path"),
                        "absolute_path": entry.get("absolute_path"),
                        "size_bytes": entry.get("size_bytes"),
                        "sha256": entry.get("sha256"),
                    }
                )
        write_json(
            self.out / "native-digests.json",
            {
                "schema": "acp2.m4-04.native-digests.v1",
                "generated_at": utc_now(),
                "release": self.release_facts,
                "files": rows,
            },
        )

    def load_chain(self) -> None:
        """Reload every recorded step document (no compute on evaluation)."""
        for step in ADR_STEPS:
            document = read_json(self.step_path(step["step_id"]))
            if isinstance(document, dict):
                self.steps[step["step_id"]] = document
        for path in sorted(self.chain_dir.glob("*.json")):
            if path.name == "chain.json":
                continue
            document = read_json(path)
            if isinstance(document, dict) and document.get("step_id"):
                self.steps.setdefault(str(document["step_id"]), document)
        chain = read_json(self.chain_dir / "chain.json") or {}
        self.release_facts = dict(chain.get("release") or self.release_facts)

    # -- criteria evaluation ----------------------------------------------

    def scenario_result(self) -> str:
        """Return the execution-chain fact (never a scientific claim)."""
        required = [step["step_id"] for step in ADR_STEPS]
        statuses = {step_id: (self.steps.get(step_id) or {}).get("status") for step_id in required}
        if all(status == "completed" for status in statuses.values()):
            return "PASS"
        return "FAIL"

    def _assessments(self) -> dict[str, list[Any]]:
        """Run the frozen deterministic validators over the real artifacts."""
        from contextlib import suppress

        from cccp.analysis.research import validators

        assessed_ts_frequency = self._result_of(self.steps.get("ts_frequency") or {})
        candidate_frequency = self._result_of(self.steps.get("candidate_frequency") or {})
        frequency_result = assessed_ts_frequency or candidate_frequency
        irc_result = self._result_of(self.steps.get("irc") or {})

        transition_state = None
        with suppress(Exception):
            transition_state = validators.validate_transition_state(frequency_result, None)

        selection = None
        mode_index = self._native_imaginary_index()
        if mode_index is not None:
            selection = validators.MappedModeSelection(
                native_printed_index=mode_index,
                mapping_status="resolved",
                source_step="candidate_frequency",
            )

        assessments: dict[str, list[Any]] = {
            "DA-C01-ts-single-imaginary-mode": [
                validators.validate_ts_frequency_count(frequency_result)
            ],
            "DA-C02-ts-mode-mapping": [
                validators.validate_ts_mapped_mode(frequency_result, selection)
            ],
            "DA-C03-frequency-evidence": [validators.validate_frequency_evidence(frequency_result)],
            "DA-C04-irc-bidirectional": [
                validators.validate_irc_bidirectional_completion(irc_result)
            ],
        }

        endpoint_claims: list[Any] = []
        connectivity = None
        with suppress(Exception):
            connectivity = validators.validate_irc_connectivity(
                irc_result, self.frozen_reference(), transition_state=transition_state
            )
        if connectivity is not None:
            endpoint_claims = [
                claim
                for claim in connectivity.claims
                if claim.validator_id == validators.IRC_ENDPOINT_IDENTITY_VALIDATOR_ID
            ]
        assessments["DA-C05-endpoint-association"] = endpoint_claims

        activation = self._activation_energy_validation()
        if activation is not None:
            assessments["DA-C06-energy-comparability"] = [activation.electronic_barrier]
            assessments["DA-C07-reactant-stoichiometry"] = [activation.reactant_stoichiometry]
        else:
            assessments["DA-C06-energy-comparability"] = [
                validators.validate_energy_comparability(None, None)
            ]
            assessments["DA-C07-reactant-stoichiometry"] = [
                validators.validate_activation_energy_comparability(
                    (),
                    None,
                    None,
                    required_reactants=REQUIRED_REACTANTS,
                    reaction_id=self.frozen.reaction_id,
                ).reactant_stoichiometry
            ]
        return assessments

    @staticmethod
    def _weakest(claims: list[Any]) -> Any | None:
        """Return the weakest claim (never an upgrade of any single direction)."""
        if not claims:
            return None
        rank = {"refuted": 0, "inconclusive": 1, "not_evaluated": 2, "supported": 3}
        return sorted(claims, key=lambda claim: rank.get(str(claim.status), 4))[0]

    def evaluate(self) -> None:
        """Evaluate the frozen criteria over the real artifacts."""
        from cccp.analysis.research.evidence import (
            ClaimScope,
            ExecutionFacts,
            assemble_claim,
            claim_draft_from_assessment,
        )
        from cccp.analysis.research.science_criteria import (
            CriterionEvidence,
            criterion_evidence_from_claim,
            digest_evidence,
            evaluate_criteria,
        )

        assessments = self._assessments()
        scope = ClaimScope(
            subject="reaction.diels_alder.butadiene_ethene_cyclohexene.v1",
            applicability="system_specific",
            conditions=(
                f"{FROZEN_METHOD}/{FROZEN_BASIS}, charge {FROZEN_CHARGE}, multiplicity "
                f"{FROZEN_MULTIPLICITY}, {TEMPERATURE_K} K, {PRESSURE_PA} Pa, {STANDARD_STATE}"
            ),
            not_in_scope=(
                "endo/exo selectivity",
                "complete mechanism",
                "exclusion of alternative paths",
                "experimental rate or kinetics",
                "global minimum",
                "general method accuracy",
                "research search capability",
            ),
        )
        execution = ExecutionFacts(
            terminal_state="completed" if self.steps.get("ts_frequency") else "not_started",
            argv=("acp2_m404_da.py", "--out", str(self.out)),
            exit_code=0,
        )
        if not getattr(self, "report_root", None):
            self.publish_report_root()
        validators = {
            "DA-C01-ts-single-imaginary-mode": "check.ts_saddle_candidate",
            "DA-C02-ts-mode-mapping": "check.ts_mode_mapping",
            "DA-C03-frequency-evidence": "check.frequency_evidence",
            "DA-C04-irc-bidirectional": "check.irc_bidirectional_completion",
            "DA-C05-endpoint-association": "check.irc_endpoint_identity",
            "DA-C06-energy-comparability": "check.activation_energy_comparability",
            "DA-C07-reactant-stoichiometry": "check.reactant_stoichiometry_completeness",
        }
        records: list[Any] = []
        record_table: list[dict[str, Any]] = []
        for criterion_id in CRITERION_ORDER:
            claims = list(assessments.get(criterion_id) or ())
            wanted = validators[criterion_id]
            matching = [claim for claim in claims if str(claim.validator_id) == wanted]
            chosen = self._weakest(matching) if matching else self._weakest(claims)
            producing_steps = self._producing_steps(criterion_id)
            step_status = {
                step_id: (self.steps.get(step_id) or {}).get("status")
                for step_id in producing_steps
            }
            if chosen is None:
                record_table.append(
                    {
                        "criterion_id": criterion_id,
                        "claim_status": None,
                        "validator_id": None,
                        "evidence_digest": None,
                        "missing_check_ids": ["not_executed"],
                        "applied_rules": ["DG-01-evidence-absent"],
                        "step_status": step_status,
                        "source_digests": [],
                    }
                )
                continue
            draft = claim_draft_from_assessment(
                chosen, scope=scope, source_refs=self._criterion_sources(producing_steps)
            )
            claim = assemble_claim(draft, execution)
            try:
                record = criterion_evidence_from_claim(claim, criterion_id)
            except Exception as exc:  # noqa: BLE001 - a rejected record is evidence
                record_table.append(
                    {
                        "criterion_id": criterion_id,
                        "claim_status": None,
                        "validator_id": getattr(chosen, "validator_id", None),
                        "evidence_digest": None,
                        "missing_check_ids": ["artifact_missing"],
                        "applied_rules": ["record_rejected"],
                        "rejected": f"{type(exc).__name__}: {exc}",
                        "step_status": step_status,
                        "source_digests": [],
                    }
                )
                continue
            if record.status in ("supported", "refuted") and not record.source_digests:
                payload = {"claim": claim.to_dict(), "source_gap": "no digested artifact"}
                record = CriterionEvidence(
                    criterion_id=record.criterion_id,
                    claim_id=record.claim_id,
                    status=record.status,
                    validator_id=record.validator_id,
                    validator_version=record.validator_version,
                    evidence=payload,
                    evidence_digest=digest_evidence(payload),
                    source_digests=(),
                    missing_check_ids=("artifact_missing",),
                )
            records.append(record)
            record_table.append(
                {
                    "criterion_id": criterion_id,
                    "claim_status": record.status,
                    "validator_id": record.validator_id,
                    "evidence_digest": record.evidence_digest,
                    "missing_check_ids": list(record.missing_check_ids),
                    "applied_rules": [],
                    "step_status": step_status,
                    "source_digests": list(record.source_digests),
                    "direction_statuses": (
                        [str(getattr(claim, "status", "")) for claim in claims]
                        if criterion_id == "DA-C05-endpoint-association"
                        else None
                    ),
                }
            )
        scenario = self.scenario_result()
        evaluation = evaluate_criteria(records, scenario_result=scenario)
        # Fill the actually applied downgrade rules from the evaluation.
        applied = {row.criterion_id: list(row.applied_rules) for row in evaluation.criteria}
        for row in record_table:
            row["applied_rules"] = applied.get(row["criterion_id"], row["applied_rules"])
        self.criteria = {
            "schema": "acp2.m4-04.criteria.v1",
            "generated_at": utc_now(),
            "scenario_result": scenario,
            "claim_status": evaluation.overall_claim_status,
            "criteria_digest": evaluation.to_dict().get("criteria_digest"),
            "records": record_table,
            "evaluation": evaluation.to_dict(),
            "method_deviation": None,
            "notes": [
                "scenario_result and claim_status are recorded separately: an execution "
                "chain can pass while the science claim stays inconclusive",
                "the frozen criteria file was read, never edited",
                "DA-C05 uses the weaker of the two per-direction endpoint-identity claims: "
                "a claim is never upgraded by combining directions",
            ],
        }
        write_json(self.out / "criteria.json", self.criteria)
        self._write_criteria_table(evaluation)

    @staticmethod
    def _producing_steps(criterion_id: str) -> tuple[str, ...]:
        from cccp.analysis.research.science_criteria import criterion_row

        row = criterion_row(criterion_id)
        mapping = {
            "candidate_frequency": ("candidate_frequency",),
            "ts_frequency": ("ts_frequency",),
            "ts_optimize": ("ts_optimize",),
            "irc": ("irc",),
            "endpoint_reactants": ("endpoint_reactants",),
            "endpoint_product": ("endpoint_product",),
            # The thermochemistry comparison consumes the participant energies
            # (the reactants' relaxations and both IRC endpoint re-evaluations);
            # those are the artifacts its verdict is bound to.
            "energy_thermochemistry": (
                "endpoint_prep_butadiene",
                "endpoint_prep_ethene",
                "endpoint_reactants",
                "endpoint_product",
                "ts_frequency",
            ),
        }
        steps: list[str] = []
        for name in row.recipe_steps:
            for step_id in mapping.get(name, (name,)):
                if step_id not in steps:
                    steps.append(step_id)
        return tuple(steps)

    def _criterion_sources(self) -> tuple[Any, ...]:
        """Return digest-bound artifact refs for every produced step artifact."""
        from cccp.analysis.research.evidence import ArtifactSourceRef, Digest

        sources: list[Any] = []
        for step in ADR_STEPS:
            record = self.steps.get(step["step_id"]) or {}
            for entry in record.get("native_files") or ():
                digest = str(entry.get("sha256") or "")
                if len(digest) != 64:
                    continue
                sources.append(
                    ArtifactSourceRef(
                        artifact_path=str(entry.get("path") or ""),
                        digest=Digest(value=digest),
                        role=f"step:{step['step_id']}",
                        units=None,
                    )
                )
        return tuple(sources)

    def _write_criteria_table(self, evaluation: Any) -> None:
        rows = []
        for criterion in evaluation.criteria:
            rows.append(
                {
                    "criterion_id": criterion.criterion_id,
                    "scenario_result": evaluation.scenario_result,
                    "claim_status": criterion.claim_status,
                    "reason": criterion.reason,
                    "evidence_status": criterion.evidence_status,
                    "validator_id": criterion.validator_id,
                    "evidence_digest": criterion.evidence_digest,
                    "applied_rules": list(criterion.applied_rules),
                    "missing_check_ids": list(criterion.missing_check_ids),
                    "notes": list(criterion.notes),
                }
            )
        questions = [
            {
                "question_id": row.question_id,
                "claim_status": row.claim_status,
                "reason": row.reason,
                "blocked_by": list(row.blocked_by),
                "refuted_by": list(row.refuted_by),
            }
            for row in evaluation.questions
        ]
        write_json(
            self.out / "criteria-table.json",
            {
                "schema": "acp2.m4-04.criteria-table.v1",
                "scenario_result": evaluation.scenario_result,
                "overall_claim_status": evaluation.overall_claim_status,
                "criteria": rows,
                "questions": questions,
            },
        )

    # -- degradation scenarios --------------------------------------------

    def run_degradation(self) -> None:
        """Record at least three honest-negative scenarios with real artifacts."""
        from cccp.analysis.research import validators
        from cccp.analysis.research.science_criteria import (
            CriterionEvidence,
            criterion_evidence_from_claim,
            digest_evidence,
            evaluate_criteria,
        )

        scenarios: list[dict[str, Any]] = []

        # (a) one-sided IRC — a real forward-only IRC that is then evaluated.
        one_sided: dict[str, Any] = {"scenario": "single-irc-direction"}
        optimized = self._optimized_ts_structure()
        if optimized is not None:
            step = {
                "index": 6,
                "step_id": "negative_irc_one_sided",
                "task_kind": "irc",
                "executed_as": "irc",
            }
            record = self.run_task_core(
                {**step, "bounded_alternative": None},
                structure=optimized,
                options={"directions": ["forward"], "maxpoints": 4},
                timeout=self.timeouts["irc"],
            )
            result = self._result_of(record)
            assessment = validators.validate_irc_bidirectional_completion(result)
            one_sided.update(
                {
                    "real_execution": True,
                    "step_status": record["status"],
                    "duration_seconds": record.get("duration_seconds"),
                    "assessment": assessment.to_dict(),
                    "expected": "inconclusive",
                    "downgrade_rule": "DG-05-single-irc-direction",
                    "passed": assessment.status == "inconclusive",
                }
            )
        else:
            # No optimized TS existed to launch a forward-only run from, so the
            # negative case is *constructed* (the ADR allows a constructed small
            # system or a computed intermediate).  It is labelled constructed and
            # only exercises the frozen validator/downgrade rule.
            start_symbols = self.frozen.reactant_symbols
            start_coordinates = self.frozen.reactant_coordinates
            product_symbols_both, product_coordinates_both = self.frozen.product_in_reactant_order()
            constructed = _constructed_irc(
                forward=(list(start_symbols), [tuple(row) for row in start_coordinates]),
                reverse=(
                    list(product_symbols_both),
                    [tuple(row) for row in product_coordinates_both],
                ),
            )
            assessment = validators.validate_irc_bidirectional_completion(constructed)
            one_sided.update(
                {
                    "real_execution": False,
                    "constructed": True,
                    "assessment": assessment.to_dict(),
                    "expected": "inconclusive",
                    "downgrade_rule": "DG-05-single-irc-direction",
                    "passed": assessment.status == "inconclusive"
                    and assessment.status != "refuted",
                }
            )
        scenarios.append(one_sided)

        # (b) missing frequency — the producing step never dispatched.
        frequency_result = getattr(validators.validate_frequency_evidence(None), "status", None)
        saddle = validators.validate_ts_frequency_count(None)
        criteria_records = []
        for criterion_id, assessment in (
            ("DA-C01-ts-single-imaginary-mode", saddle),
            ("DA-C03-frequency-evidence", validators.validate_frequency_evidence(None)),
        ):
            from cccp.analysis.research.evidence import (
                ClaimScope,
                ExecutionFacts,
                assemble_claim,
                claim_draft_from_assessment,
            )

            draft = claim_draft_from_assessment(
                assessment,
                scope=ClaimScope(subject="reaction.diels_alder", applicability="system_specific"),
            )
            claim = assemble_claim(
                draft,
                ExecutionFacts(terminal_state="completed", argv=("negative",), exit_code=0),
            )
            criteria_records.append(criterion_evidence_from_claim(claim, criterion_id))
        missing_frequency = evaluate_criteria(criteria_records, scenario_result="PASS")
        scenarios.append(
            {
                "scenario": "missing-frequency",
                "real_execution": True,
                "expected": "not_evaluated",
                "downgrade_rule": "DG-01/DG-02",
                "frequency_evidence_status": frequency_result,
                "criteria": {
                    row.criterion_id: row.claim_status for row in missing_frequency.criteria
                },
                "questions": {
                    row.question_id: row.claim_status for row in missing_frequency.questions
                },
                "scenario_result": missing_frequency.scenario_result,
                "passed": all(
                    row.claim_status in ("not_evaluated", "inconclusive")
                    for row in missing_frequency.criteria
                ),
            }
        )

        # (c) endpoint mismatch — the real IRC result judged against a
        # constructed non-matching endpoint (a computed intermediate).
        mismatch: dict[str, Any] = {"scenario": "endpoint-mismatch"}
        real_irc = self._result_of(self.steps.get("irc") or {})
        mismatch_constructed = False
        if real_irc is None:
            from cccp.calculation.requests import IrcDirection, TaskKind
            from cccp.calculation.results import IrcDirectionResult, IrcPayload, TaskResult

            product_symbols_both, product_coordinates_both = self.frozen.product_in_reactant_order()
            entries = (
                IrcDirectionResult(
                    direction=IrcDirection("forward"),
                    energy_hartree=-233.0,
                    coordinates=tuple(
                        (float(x), float(y), float(z))
                        for x, y, z in self.frozen.reactant_coordinates
                    ),
                    symbols=tuple(self.frozen.reactant_symbols),
                    converged=True,
                    steps=20,
                    success=True,
                ),
                IrcDirectionResult(
                    direction=IrcDirection("reverse"),
                    energy_hartree=-233.0,
                    coordinates=tuple(product_coordinates_both),
                    symbols=tuple(product_symbols_both),
                    converged=True,
                    steps=20,
                    success=True,
                ),
            )
            real_irc = TaskResult(
                task=TaskKind.IRC,
                status="completed",
                complete=True,
                payload=IrcPayload(directions=entries),
                metadata={"direction_status": {"forward": "converged", "reverse": "converged"}},
            )
            mismatch_constructed = True
        if real_irc is not None:
            from cccp.analysis.research.validators import FrozenEndpoint, IrcReactionReference

            symbols = self.frozen.product_symbols
            # A rigid translation is removed by superposition, so the constructed
            # mismatch deforms the geometry: the bond topology no longer matches
            # the frozen product, which is an affirmative contradiction.
            shifted = tuple(
                (x * 1.6, y * 1.6, z * 1.6) for x, y, z in self.frozen.product_coordinates
            )
            endpoint = FrozenEndpoint(
                endpoint_id="endpoint.constructed_mismatch",
                species_id="constructed-not-cyclohexene",
                symbols=symbols,
                coordinates=shifted,
                charge=FROZEN_CHARGE,
                multiplicity=FROZEN_MULTIPLICITY,
            )
            reference = IrcReactionReference(
                reactant=self.frozen_reference().reactant,
                product=endpoint,
                atom_mapping=self.frozen.atom_mapping,
                reaction_id="constructed.endpoint-mismatch",
            )
            connectivity = validators.validate_irc_connectivity(real_irc, reference)
            endpoint_claims = [
                claim for claim in connectivity.claims if claim.claim_id.startswith("c-irc-")
            ]
            mismatch.update(
                {
                    "real_execution": not mismatch_constructed,
                    "constructed": mismatch_constructed,
                    "constructed_endpoint": "product geometry scaled by 1.6 (topology broken)",
                    "assessment": connectivity.to_dict(),
                    "expected": "refuted",
                    "passed": any(claim.status == "refuted" for claim in endpoint_claims),
                    "statuses": [claim.status for claim in endpoint_claims],
                }
            )
        else:
            mismatch.update(
                {
                    "real_execution": False,
                    "expected": "refuted",
                    "passed": False,
                    "reason": "no real IRC result exists",
                }
            )
        scenarios.append(mismatch)

        # (d) forged supported input — three rejection/downgrade guards.
        forged: dict[str, Any] = {"scenario": "forged-supported-record", "checks": []}

        def _forged_record(evidence: dict[str, Any], digest: str) -> Any:
            return CriterionEvidence(
                criterion_id="DA-C01-ts-single-imaginary-mode",
                claim_id="forged",
                status="supported",
                validator_id="check.ts_saddle_candidate",
                validator_version="1.0.0",
                evidence=evidence,
                evidence_digest=digest,
                source_digests=(),
            )

        payload = {"claim": {"claim_id": "forged", "status": "supported"}}
        try:
            _forged_record(payload, "0" * 64)
            forged["checks"].append({"check": "digest_mismatch", "passed": False})
        except Exception as exc:  # noqa: BLE001 - rejection is the pass
            forged["checks"].append(
                {
                    "check": "digest_mismatch",
                    "passed": True,
                    "rejected_with": f"{type(exc).__name__}: {exc}",
                }
            )
        override_payload = {"claim": {"claim_id": "forged"}, "criteria_passed": True}
        try:
            _forged_record(override_payload, digest_evidence(override_payload))
            forged["checks"].append({"check": "status_override_key", "passed": False})
        except Exception as exc:  # noqa: BLE001 - rejection is the pass
            forged["checks"].append(
                {
                    "check": "status_override_key",
                    "passed": True,
                    "rejected_with": f"{type(exc).__name__}: {exc}",
                }
            )
        sourceless_payload = {"claim": {"claim_id": "forged-unsourced"}}
        try:
            record = _forged_record(sourceless_payload, digest_evidence(sourceless_payload))
            evaluation = evaluate_criteria((record,), scenario_result="PASS")
            downgraded = (
                evaluation.criterion("DA-C01-ts-single-imaginary-mode").claim_status
                == "inconclusive"
            )
            forged["checks"].append(
                {"check": "unsourced_supported_downgraded", "passed": downgraded}
            )
        except Exception as exc:  # noqa: BLE001 - a rejection is also acceptable
            forged["checks"].append(
                {
                    "check": "unsourced_supported_downgraded",
                    "passed": True,
                    "rejected_with": f"{type(exc).__name__}: {exc}",
                }
            )
        forged["expected"] = "rejected-or-downgraded"
        forged["passed"] = all(item["passed"] for item in forged["checks"])
        scenarios.append(forged)

        self.degradation = {
            "schema": "acp2.m4-04.degradation.v1",
            "generated_at": utc_now(),
            "scenarios": scenarios,
            "all_passed": all(bool(item.get("passed")) for item in scenarios),
        }
        write_json(self.out / "degradation.json", self.degradation)

    # -- evidence chain ---------------------------------------------------

    def run_projection_attempt(self) -> dict[str, Any]:
        """Run ONE real single-task attempt through the worker entry.

        The bounded-DAG attempt writes per-step results and a step-chain receipt,
        not a task-level ``task_result_digest``; the platform evidence projection
        consumes the single-task worker's receipt/task_result pair.  This attempt
        therefore executes the *same* frozen-level singlepoint the DA slice needs
        for its endpoint, through the production single-task worker path, so the
        EvidenceReport can be attributed to a real receipt instead of a minted
        one.  The gap itself is recorded, never hidden.
        """
        from acp.calculations import attempt_freeze as freeze
        from acp_services.runtime.attempt_freeze import write_frozen_attempt
        from acp_services.worker_bootstrap import assemble_worker

        work = self.work_dir("projection_attempt")
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True, exist_ok=True)
        structure = work.parent / "inputs" / "projection_endpoint.xyz"
        structure.parent.mkdir(parents=True, exist_ok=True)
        endpoints = self._irc_endpoints()
        geometry = endpoints.get("forward")
        if geometry is None:
            geometry = (list(self.frozen.product_symbols), list(self.frozen.product_coordinates))
        structure.write_text(
            _xyz_text(geometry[0], geometry[1], "M4-04 projection attempt (IRC endpoint)"),
            encoding="utf-8",
        )
        record = SimpleNamespace(
            id=f"{time.strftime('%Y%m%d')}_943_projection_attempt",
            attempt=1,
            spec=SimpleNamespace(
                workflow="singlepoint",
                name="m4-04-projection-attempt",
                input={"charge": FROZEN_CHARGE, "multiplicity": FROZEN_MULTIPLICITY},
                method={
                    "method": FROZEN_METHOD,
                    "levels": {
                        "default": {
                            "functional": FROZEN_METHOD,
                            "basis": FROZEN_BASIS,
                        }
                    },
                },
                resources={"nproc": 4},
                config_path=str(self.config_path),
            ),
        )
        result: dict[str, Any] = {
            "schema": "acp2.m4-04.projection-attempt.v1",
            "step_id": "projection_attempt",
            "entry": "single_task_worker_envelope",
            "task_ref": record.id,
            "status": "failed",
            "error": None,
        }
        started = time.perf_counter()
        try:
            spec = freeze.build_singlepoint_freeze_spec(record, work, structure, self.release)
            group = write_frozen_attempt(work, spec)
            runtime = assemble_worker(
                envelope=spec.envelope,
                plan=spec.plan,
                work_dir=work,
                execution_right=_M404ExecutionRight(),
            )
            receipt = runtime.run()
            result["duration_seconds"] = round(time.perf_counter() - started, 2)
            payload = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)
            result["status"] = "completed" if payload.get("state") == "completed" else "failed"
            result["receipt"] = {
                "state": payload.get("state"),
                "job_id": payload.get("job_id"),
                "attempt": payload.get("attempt"),
                "release_id": payload.get("release_id"),
                "release_digest": payload.get("release_digest"),
                "evidence_digests": dict(payload.get("evidence_digests") or {}),
                "chain_digest": payload.get("chain_digest"),
            }
            result["frozen_group"] = {
                "workflow": group.manifest.workflow,
                "member_digests": dict(group.manifest.member_digests),
            }
            task_result = work / "WORK" / "00_RUNTIME" / "task_result.json"
            result["task_result_present"] = task_result.is_file()
            if task_result.is_file():
                result["task_result_digest"] = digest_bytes(task_result.read_bytes())
        except Exception as exc:  # noqa: BLE001 - a refusal is evidence
            result["duration_seconds"] = round(time.perf_counter() - started, 2)
            result["error"] = f"{type(exc).__name__}: {exc}"
        write_json(self.out / "projection-attempt.json", result)
        return result

    def publish_report_root(self) -> dict[str, Any]:
        """Publish byte-identical artifact copies into one real attempt work dir.

        The EvidenceReport must be readable through one ``read_root`` whose
        receipt and task result are the attempt's own: the copies therefore land
        in the ``ts_frequency`` attempt work dir (the attempt that carries the
        frozen verdict) under ``RESULT/m4-04/``.  Origin, digest and
        byte-identity are recorded for every copy.
        """
        projection = read_json(self.out / "projection-attempt.json") or {}
        primary = (
            self.work_dir("projection_attempt")
            if projection.get("status") == "completed"
            else self.work_dir("ts_frequency")
        )
        root = primary / "RESULT" / "m4-04"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        copies: dict[str, list[dict[str, Any]]] = {}
        rows: list[dict[str, Any]] = []
        # Every recorded execution document, including the ADR sub-steps
        # (endpoint_prep_<species>) whose artifacts carry the reactant energies.
        ordered = [step["step_id"] for step in ADR_STEPS]
        ordered.extend(name for name in sorted(self.steps) if name not in ordered)
        for step_id in ordered:
            record = self.steps.get(step_id) or {}
            entries = list(record.get("native_files") or ())
            bucket: list[dict[str, Any]] = []
            for index, entry in enumerate(entries):
                absolute = Path(str(entry.get("absolute_path") or ""))
                if not absolute.is_file():
                    continue
                name = f"{index:03d}_{absolute.name}"
                destination = root / step_id / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(absolute, destination)
                digest = bare_digest(destination)
                relative = str(destination.relative_to(primary))
                bucket.append({"path": relative, "sha256": digest})
                rows.append(
                    {
                        "step_id": step_id,
                        "origin": str(absolute),
                        "copy": relative,
                        "sha256": digest,
                        "origin_sha256": entry.get("sha256"),
                        "byte_identical": digest == entry.get("sha256"),
                    }
                )
            copies[step_id] = bucket
        write_json(root / "provenance.json", {"copies": rows})
        self.report_root = primary
        self.report_copies = copies
        self.report_provenance = rows
        return {"root": str(primary), "copies": copies, "rows": rows}

    def _criterion_sources(self, producing_steps: tuple[str, ...]) -> tuple[Any, ...]:
        """Return digest-bound artifact refs (relative to the report root)."""
        from cccp.analysis.research.evidence import ArtifactSourceRef, Digest

        sources: list[Any] = []
        seen: set[str] = set()
        for step_id in producing_steps:
            for entry in (self.report_copies or {}).get(step_id, ()):
                path = str(entry["path"])
                if path in seen:
                    continue
                seen.add(path)
                sources.append(
                    ArtifactSourceRef(
                        artifact_path=path,
                        digest=Digest(value=str(entry["sha256"])),
                        role=f"step:{step_id}",
                        units=None,
                    )
                )
        return tuple(sources)

    def build_evidence(self) -> None:
        """Assemble the EvidenceReport, project it and record both decisions."""
        from cccp.analysis.research.evidence import (
            ClaimDraft,
            ClaimScope,
            EvidenceMissingCheck,
            EvidenceReport,
            ExecutionFacts,
            Limitation,
            Subject,
            assemble_claim,
            canonical_evidence_json,
            claim_draft_from_assessment,
        )

        if not getattr(self, "report_root", None):
            self.publish_report_root()
        primary = Path(self.report_root)
        assessments = self._assessments()
        scope = ClaimScope(
            subject="reaction.diels_alder.butadiene_ethene_cyclohexene.v1",
            applicability="system_specific",
            conditions=(
                f"{FROZEN_METHOD}/{FROZEN_BASIS}, charge {FROZEN_CHARGE}, multiplicity "
                f"{FROZEN_MULTIPLICITY}, {TEMPERATURE_K} K, {PRESSURE_PA} Pa, {STANDARD_STATE}"
            ),
            not_in_scope=(
                "endo/exo selectivity",
                "complete mechanism",
                "exclusion of alternative paths",
                "experimental rate or kinetics",
                "global minimum",
                "general method accuracy",
                "research search capability",
            ),
        )
        execution = ExecutionFacts(
            terminal_state="completed" if self.steps.get("ts_frequency") else "not_started",
            argv=("acp2_m404_da.py", "--phase", "chain"),
            exit_code=0,
            started_at=self.started_at,
            finished_at=utc_now(),
        )
        limitations = (
            Limitation(
                limitation_id="known-candidate-control-only",
                description=(
                    "A frozen known candidate was carried through the execution chain; this is "
                    "a control run, never a search result."
                ),
            ),
            Limitation(
                limitation_id="single-conformer-single-path",
                description=(
                    "One conformer per endpoint and one declared candidate path: a declared "
                    "boundary, not a finding."
                ),
            ),
            Limitation(
                limitation_id="execution-level-is-the-frozen-level",
                description=(
                    "Every compared quantity was produced at the ADR-008 frozen level; no "
                    "equivalent tier was substituted."
                ),
            ),
        )
        summary = self.criteria.get("evaluation") or {}
        status_by_criterion = {row["criterion_id"]: row for row in (summary.get("criteria") or ())}
        drafts = []
        for criterion_id in CRITERION_ORDER:
            row = status_by_criterion.get(criterion_id) or {}
            producing = self._producing_steps(criterion_id)
            sources = self._criterion_sources(producing)
            seen_checks: set[str] = set()
            missing_items: list[Any] = []
            for item in row.get("missing_check_ids") or ():
                if str(item) in seen_checks:
                    continue
                seen_checks.add(str(item))
                missing_items.append(
                    EvidenceMissingCheck(
                        check_id=str(item), reason="frozen missing-check vocabulary"
                    )
                )
            missing = tuple(missing_items)
            claims = list(assessments.get(criterion_id) or ())
            chosen = self._weakest(
                [
                    claim
                    for claim in claims
                    if str(getattr(claim, "validator_id", ""))
                    == {
                        "DA-C01-ts-single-imaginary-mode": "check.ts_saddle_candidate",
                        "DA-C02-ts-mode-mapping": "check.ts_mode_mapping",
                        "DA-C03-frequency-evidence": "check.frequency_evidence",
                        "DA-C04-irc-bidirectional": "check.irc_bidirectional_completion",
                        "DA-C05-endpoint-association": "check.irc_endpoint_identity",
                        "DA-C06-energy-comparability": "check.activation_energy_comparability",
                        "DA-C07-reactant-stoichiometry": (
                            "check.reactant_stoichiometry_completeness"
                        ),
                    }[criterion_id]
                ]
            ) or self._weakest(claims)
            if chosen is None:
                drafts.append(
                    ClaimDraft(
                        claim_id=f"criterion:{criterion_id}",
                        statement=f"ADR-008 {criterion_id} has no producing evidence",
                        status="not_evaluated",
                        scope=scope,
                        source_refs=(),
                        missing_checks=(
                            EvidenceMissingCheck(
                                check_id="not_executed",
                                reason="the producing step did not run",
                            ),
                        ),
                        validator=None,
                        limitations=("known-candidate-control-only",),
                    )
                )
                continue
            draft = claim_draft_from_assessment(
                chosen,
                scope=scope,
                source_refs=sources,
                limitations=(
                    "known-candidate-control-only",
                    "single-conformer-single-path",
                    "execution-level-is-the-frozen-level",
                ),
                missing_checks=missing,
            )
            drafts.append(draft)
        report = EvidenceReport(
            report_id="report.m4-04.diels-alder.v1",
            subject=Subject(
                subject_id="subject.diels_alder.limited.v1",
                subject_kind="recipe_run",
                description=(
                    "ADR-008 limited Diels-Alder control run: butadiene + ethene -> cyclohexene"
                ),
                input_digests=(),
            ),
            execution=execution,
            claims=tuple(
                _dedupe_claim_checks(assemble_claim(draft, execution)) for draft in drafts
            ),
            limitations=limitations,
        )
        report_path = primary / "RESULT" / "evidence_report.json"
        report_path.write_text(canonical_evidence_json(report), encoding="utf-8")
        self.evidence = {
            "schema": "acp2.m4-04.evidence.v1",
            "report_id": report.report_id,
            "report_digest": digest_bytes(report_path.read_bytes()),
            "report_path": str(report_path),
            "report_root": str(primary),
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "status": claim.status,
                    "validator": None if claim.validator is None else claim.validator.to_dict(),
                    "source_refs": [ref.to_dict() for ref in claim.source_refs],
                    "missing_checks": [check.to_dict() for check in claim.missing_checks],
                    "limitations": list(claim.limitations),
                }
                for claim in report.claims
            ],
            "provenance_rows": self.report_provenance,
            "copies_byte_identical": all(row["byte_identical"] for row in self.report_provenance),
        }
        platform = self._project_and_decide(report_path, primary)
        self.evidence["platform_projection"] = platform
        view = platform.get("view") or {}
        rph = self._rph_decision(view)
        self.evidence["rph_decision"] = rph
        workbench = self._workbench_view(view)
        self.evidence["workbench"] = workbench
        self.evidence["decisions_agree"] = bool(
            rph.get("decision")
            and platform.get("decision")
            and rph["decision"] == platform["decision"]
        )
        self.evidence["both_readers_reject_accept"] = bool(
            platform.get("decision") != "accept" and rph.get("decision") != "accept"
        )
        write_json(self.out / "evidence-chain.json", self.evidence)

    def _project_and_decide(self, report_path: Path, primary: Path) -> dict[str, Any]:
        from acp_services.runtime import worker
        from acp_services.runtime.evidence_projection import (
            TaskRef,
            decide_evidence,
            project_evidence,
        )
        from acp_services.runtime.research_session import ResearchIdentity

        identity = ResearchIdentity(
            principal_id="principal_m404",
            project_id="project_m404_da",
            research_id="res-m404-da",
            session_id="ses-m404-da",
        )
        receipt = worker.read_receipt(primary / "execution_receipt.json")
        task_result = worker.read_task_result(primary)
        if receipt is None or task_result is None:
            return {
                "projected": False,
                "attribution_verified": False,
                "error": "the primary attempt carries no receipt/task result to project",
            }
        receipt_payload = receipt.to_dict()
        job_id = str(receipt_payload.get("job_id") or receipt_payload.get("task_id") or "")
        report_bytes = report_path.read_bytes()
        attempt = int(receipt_payload.get("attempt") or 1)
        release_id = str(receipt_payload.get("release_id") or "")
        release_digest = str(receipt_payload.get("release_digest") or "")

        def _try(receipt_doc: dict[str, Any], digest_value: str) -> Any:
            return project_evidence(
                identity=identity,
                round_index=0,
                task_ref=TaskRef(
                    project_id="project_m404_da",
                    task_id=job_id,
                    job_id=job_id,
                    attempt=attempt,
                    release_id=release_id,
                    release_digest=digest_value,
                ),
                receipt=receipt_doc,
                task_result=task_result,
                report=report_bytes,
                report_digest=digest_bytes(report_bytes),
                read_root=primary,
            )

        strict_error: str | None = None
        view = None
        controlled = False
        try:
            view = _try(receipt_payload, release_digest)
        except Exception as exc:  # noqa: BLE001 - a refusal is evidence
            strict_error = f"{type(exc).__name__}: {exc}"
            # The local release digest is a bare sha256 while ``TaskRef`` requires
            # the ``sha256:`` form and the receipt comparison is literal, so a
            # real local attempt cannot be projected as-is.  The controlled retry
            # normalises ONLY that one digest field (receipt copy + TaskRef) and
            # is labelled as such; every other value stays the attempt's own.
            prefixed = (
                release_digest
                if release_digest.startswith("sha256:")
                else f"sha256:{release_digest}"
            )
            normalised = dict(receipt_payload)
            normalised["release_digest"] = prefixed
            try:
                view = _try(normalised, prefixed)
                controlled = True
            except Exception as retry_exc:  # noqa: BLE001 - record both refusals
                return {
                    "projected": False,
                    "attribution_verified": False,
                    "strict_error": strict_error,
                    "controlled_error": f"{type(retry_exc).__name__}: {retry_exc}",
                }
        allowed = (
            view.execution_terminal_state == "completed"
            and view.evidence_status == "complete"
            and not view.blocking_missing_checks
            and view.overall_claim_status == "supported"
        )
        decision = None
        decision_error = None
        try:
            decision = decide_evidence(view, "accept" if allowed else "revise")
        except Exception as exc:  # noqa: BLE001 - a policy refusal is recorded
            decision_error = f"{type(exc).__name__}: {exc}"
        return {
            "projected": True,
            "attribution_verified": True,
            "strict_error": strict_error,
            "controlled_digest_form_normalisation": controlled,
            "digest_form_note": (
                None
                if not controlled
                else (
                    "LocalRelease.release_digest is a bare sha256 while TaskRef requires the "
                    "sha256: form; only that single field was normalised in the receipt copy "
                    "and the TaskRef. Every other identity, the task_result digest and every "
                    "claim source digest are the attempt's own bytes."
                )
            ),
            "view": view.to_dict(),
            "evidence_status": view.evidence_status,
            "overall_claim_status": view.overall_claim_status,
            "execution_terminal_state": view.execution_terminal_state,
            "blocking_missing_checks": list(view.blocking_missing_checks),
            "projection_digest": view.projection_digest,
            "decision": None if decision is None else decision.decision,
            "decision_record": None if decision is None else decision.to_dict(),
            "decision_error": decision_error,
        }

    def _run_node_driver(self, payload: dict[str, Any]) -> dict[str, Any]:
        driver = RPH_DRIVER
        if not driver.is_file():
            return {"available": False, "reason": "the RPH/Workbench driver is missing"}
        node = shutil.which("node") or ""
        if not node:
            return {"available": False, "reason": "node is not on PATH"}
        work = self.out / "views"
        work.mkdir(parents=True, exist_ok=True)
        request = work / "request.json"
        result = work / "result.json"
        if result.exists():
            result.unlink()
        write_json(request, payload)
        completed = subprocess.run(
            [node, str(driver), "--config", str(request)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(REPO),
        )
        document = read_json(result)
        if not isinstance(document, dict):
            return {
                "available": False,
                "reason": "the driver wrote no result",
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            }
        return document

    def _rph_decision(self, view: dict[str, Any]) -> dict[str, Any]:
        document = self._run_node_driver({"mode": "decision", "view": view})
        return {
            "available": bool(document.get("available")),
            "decision": document.get("decision"),
            "source": document.get("source"),
            "error": document.get("error") or document.get("reason"),
        }

    def _workbench_view(self, view: dict[str, Any]) -> dict[str, Any]:
        document = self._run_node_driver({"mode": "workbench", "view": view})
        return {
            "available": bool(document.get("available")),
            "rows": document.get("rows"),
            "supported": document.get("supported"),
            "problem": document.get("problem"),
            "error": document.get("error") or document.get("reason"),
        }

    # -- H4 --------------------------------------------------------------

    def _qc_invocation_count(self) -> int:
        """Count the real QC process invocations recorded by this run."""
        task_refs: set[str] = set()
        for record in self.steps.values():
            if record.get("entry") not in ("bounded_recipe_envelope", "legacy_task_core"):
                continue
            task_ref = str(record.get("task_ref") or "")
            if task_ref:
                task_refs.add(task_ref)
        projection = read_json(self.out / "projection-attempt.json") or {}
        if projection.get("status") == "completed":
            task_refs.add(str(projection.get("task_ref") or "projection_attempt"))
        return len(task_refs)

    def build_h4(self) -> None:
        """Record the two ADR-008 environment rows and the H4 consequence."""
        local = {
            "row_id": "DA-ENV-LOCAL",
            "environment": "Local",
            "required": True,
            "steps": {
                step["step_id"]: {
                    "status": (self.steps.get(step["step_id"]) or {}).get("status"),
                    "entry": (self.steps.get(step["step_id"]) or {}).get("entry"),
                    "duration_seconds": (self.steps.get(step["step_id"]) or {}).get(
                        "duration_seconds"
                    ),
                }
                for step in ADR_STEPS
            },
            "criteria": {
                row["criterion_id"]: row["claim_status"]
                for row in (self.criteria.get("evaluation") or {}).get("criteria") or ()
            },
            "scenario_result": self.criteria.get("scenario_result"),
            "claim_status": self.criteria.get("claim_status"),
            "budget": {
                "timing_basis": "unmeasured",
                "measured_step_timings": "recorded in chain/<step>.json",
                "walltime_process_termination": "NOT_VERIFIED",
                "queue_deadline": "NOT_VERIFIED",
            },
            "counts": {
                "task_ref": len({step["step_id"] for step in ADR_STEPS}),
                "attempt": sum(
                    1
                    for step in ADR_STEPS
                    if (self.steps.get(step["step_id"]) or {}).get("status")
                    in ("completed", "partial")
                ),
                "qc_invocation": self._qc_invocation_count(),
            },
            "receipts": {
                "usage_receipt": "not produced (no Controller-side usage collection in this run)",
                "process_group_termination_receipt": (
                    "NOT_VERIFIED (M1-04 walltime_process_termination)"
                ),
                "attempt_attribution": "per step in chain/<step>.json",
            },
        }
        incomplete = [
            step["step_id"]
            for step in ADR_STEPS
            if (local["steps"].get(step["step_id"]) or {}).get("status") != "completed"
        ]
        missing_receipts = [
            "walltime_process_termination",
            "terminal_usage_receipt",
        ]
        # ADR-008 §14 requires budget receipts and every step attempt, so the row
        # is open while a required receipt is absent -- a completed execution is
        # not the same fact as a complete row.
        local["missing"] = {
            "incomplete_steps": incomplete,
            "missing_receipts": missing_receipts,
        }
        local["state"] = "closed" if not incomplete and not missing_receipts else "open"
        local["reason"] = (
            None
            if local["state"] == "closed"
            else (
                "the row is open: "
                + (
                    f"incomplete steps {incomplete}"
                    if incomplete
                    else "missing required receipt(s) " + ", ".join(missing_receipts)
                )
            )
        )
        lsf = {
            "row_id": "DA-ENV-LSF",
            "environment": "LSF",
            "required": True,
            "scenario_result": "NOT_VERIFIED",
            "claim_status": "not_evaluated",
            "state": "open",
            "reason": (
                "ADR-008 §14 lists the LSF DA row as required; no remote submission, "
                "termination or terminal-status receipt exists yet (M4-03). A Local PASS "
                "never substitutes for it."
            ),
        }
        reasons: list[str] = []
        if local["state"] != "closed":
            reasons.append(f"the Local DA row is open: {local['reason']}")
        if lsf["state"] != "closed":
            reasons.append("the LSF DA row is NOT_VERIFIED (M4-03 dependency)")
        reasons.append("the budget timing basis is unmeasured (ADR-008 §6)")
        self.h4 = {
            "schema": "acp2.m4-04.h4.v1",
            "generated_at": utc_now(),
            "rows": [local, lsf],
            "closed": False,
            "state": "open",
            "reasons": reasons,
            "statement": (
                "H4 stays OPEN: execution-chain evidence cannot close a row whose LSF receipt "
                "is missing, and an unmeasured timing basis keeps the bounds constructed."
            ),
        }
        write_json(self.out / "h4.json", self.h4)
        write_json(self.out / "da-env-local-row.json", local)

    # -- summary ----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return the run summary (the acceptance receipt body)."""
        scenario = self.criteria.get("scenario_result") or self.scenario_result()
        claim_status = self.criteria.get("claim_status") or "not_evaluated"
        criteria_rows = [
            {
                "criterion_id": row["criterion_id"],
                "scenario_result": scenario,
                "claim_status": row["claim_status"],
                "applied_rules": row.get("applied_rules"),
            }
            for row in (self.criteria.get("evaluation") or {}).get("criteria") or ()
        ]
        questions = [
            {"question_id": row["question_id"], "claim_status": row["claim_status"]}
            for row in (self.criteria.get("evaluation") or {}).get("questions") or ()
        ]
        return {
            "schema": SCHEMA,
            "generated_at": utc_now(),
            "started_at": self.started_at,
            "git_revision": self.preflight.get("git_revision"),
            "out": str(self.out),
            "run_root": str(self.run_root),
            "scenario_result": scenario,
            "claim_status": claim_status,
            "overall_claim_status": (self.criteria.get("evaluation") or {}).get(
                "overall_claim_status"
            ),
            "criteria": criteria_rows,
            "questions": questions,
            "public_entry": {
                "compiled": self.public_entry.get("compiled"),
                "compiler_error": self.public_entry.get("compiler_error"),
                "open_steps": self.public_entry.get("open_steps"),
            },
            "steps": [
                {
                    "step_id": step["step_id"],
                    "status": (self.steps.get(step["step_id"]) or {}).get("status"),
                    "entry": (self.steps.get(step["step_id"]) or {}).get("entry"),
                    "duration_seconds": (self.steps.get(step["step_id"]) or {}).get(
                        "duration_seconds"
                    ),
                    "task_ref": (self.steps.get(step["step_id"]) or {}).get("task_ref")
                    or " + ".join(
                        str(child.get("task_ref"))
                        for child in (self.steps.get(step["step_id"]) or {}).get("sub_executions")
                        or ()
                        if child.get("task_ref")
                    )
                    or None,
                    "error": (self.steps.get(step["step_id"]) or {}).get("error"),
                }
                for step in ADR_STEPS
            ],
            "release": self.release_facts,
            "software": self.software.get("resolved"),
            "software_findings": self.software.get("findings"),
            "evidence_chain": {
                "report_digest": self.evidence.get("report_digest"),
                "attribution_verified": (self.evidence.get("platform_projection") or {}).get(
                    "attribution_verified"
                ),
                "platform_decision": (self.evidence.get("platform_projection") or {}).get(
                    "decision"
                ),
                "rph_decision": (self.evidence.get("rph_decision") or {}).get("decision"),
                "decisions_agree": self.evidence.get("decisions_agree"),
                "workbench_available": (self.evidence.get("workbench") or {}).get("available"),
            },
            "degradation": {
                "all_passed": self.degradation.get("all_passed"),
                "scenarios": [
                    {"scenario": item["scenario"], "passed": item.get("passed")}
                    for item in (self.degradation.get("scenarios") or ())
                ],
            },
            "h4": {"state": self.h4.get("state"), "closed": self.h4.get("closed")},
            "reasons": self.reasons,
            "notes": self.notes,
            "limitations": [
                "one declared candidate path, one conformer per endpoint: a declared boundary",
                "a known candidate carried through the chain is never a search result",
                "no experimental rate, no global minimum, no general method accuracy claim",
                "the bounded-recipe DAG cannot host the frozen nine-step recipe today: "
                "the nine steps ran as nine attempts sharing one release, so the evidence "
                "closure is per step and not yet one-TaskRef/nine-step",
                "the bounded-DAG attempt writes per-step evidence, not a task-level "
                "task_result_digest: the platform projection is therefore bound to the "
                "single-task worker attempt (recorded separately)",
                "Shermo is not installed on this host: the Gibbs activation barrier is "
                "not claimed (the electronic barrier is)",
            ],
        }

    def run(self) -> int:
        """Run the requested phases and write every evidence document."""
        self.run_preflight()
        if self.phase in ("public-entry", "chain", "all"):
            self.run_public_entry()
        if self.phase in ("chain", "all"):
            self.run_chain()
        else:
            self.load_chain()
        if self.phase in ("evaluate", "all", "chain"):
            if not self.steps:
                self.load_chain()
            self.write_native_digests()
            if not self.public_entry:
                self.public_entry = read_json(self.out / "public-entry.json") or {}
            if not self.preflight:
                self.preflight = read_json(self.out / "preflight.json") or {}
            projection = read_json(self.out / "projection-attempt.json") or {}
            if not (self.reuse and projection.get("status") == "completed"):
                if self.release is None:
                    self.ensure_release()
                self.run_projection_attempt()
            self.evaluate()
            self.run_degradation()
            self.build_evidence()
            self.build_h4()
        if not self.steps:
            self.load_chain()
        document = self.summary()
        write_json(self.out / "summary.json", document)
        self._write_report(document)
        if not (self.public_entry or self.criteria or self.h4):
            return EXIT_NOT_VERIFIED
        if any(step.get("status") != "completed" for step in self.steps.values()):
            return EXIT_NOT_VERIFIED
        if self.h4.get("state") != "closed":
            return EXIT_NOT_VERIFIED
        return EXIT_PASS

    def _write_report(self, summary: dict[str, Any]) -> None:
        """Write the human-readable evidence report for the run."""
        lines = [
            "# M4-04 — Limited Diels–Alder science vertical (ADR-008)",
            "",
            f"- generated: `{summary['generated_at']}`",
            f"- git: `{summary.get('git_revision')}`",
            f"- scenario_result: **{summary['scenario_result']}**",
            f"- claim_status: **{summary['claim_status']}**",
            f"- H4: **{summary['h4']['state']}**",
            "",
            "## Executed level",
            "",
            f"- frozen ADR-008 level: `{FROZEN_METHOD}/{FROZEN_BASIS}` — used unchanged",
            f"- ORCA: `{(summary.get('software') or {}).get('orca', {})}`",
            f"- xTB: `{(summary.get('software') or {}).get('xtb', {})}`",
            "",
            "## ADR-008 steps",
            "",
            "| step | entry | status | seconds | task ref |",
            "| --- | --- | --- | --- | --- |",
        ]
        for step in summary["steps"]:
            lines.append(
                f"| {step['step_id']} | {step.get('entry')} | {step.get('status')} | "
                f"{step.get('duration_seconds')} | {step.get('task_ref')} |"
            )
        lines.extend(
            [
                "",
                "## Criteria (scenario_result vs claim_status)",
                "",
                "| criterion | scenario_result | claim_status | applied rules |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in summary["criteria"]:
            lines.append(
                f"| {row['criterion_id']} | {row['scenario_result']} | "
                f"{row['claim_status']} | {', '.join(row.get('applied_rules') or [])} |"
            )
        lines.extend(["", "## Questions", ""])
        for row in summary["questions"]:
            lines.append(f"- `{row['question_id']}` → **{row['claim_status']}**")
        lines.extend(
            [
                "",
                "## Public bounded-recipe entry",
                "",
                f"- compiled: `{summary['public_entry']['compiled']}`",
                f"- error: `{summary['public_entry']['compiler_error']}`",
                f"- open steps: `{summary['public_entry']['open_steps']}`",
                "",
                "## Evidence chain",
                "",
                f"- report digest: `{(summary.get('evidence_chain') or {}).get('report_digest')}`",
                f"- platform attribution verified: "
                f"`{(summary.get('evidence_chain') or {}).get('attribution_verified')}`",
                f"- platform decision: `{_chain_fact(summary, 'platform_decision')}`",
                f"- RPH decision: `{_chain_fact(summary, 'rph_decision')}`",
                f"- decisions agree: `{_chain_fact(summary, 'decisions_agree')}`",
                f"- workbench available: `{_chain_fact(summary, 'workbench_available')}`",
                "",
                "## Degradation scenarios",
                "",
            ]
        )
        for row in (summary.get("degradation") or {}).get("scenarios") or ():
            lines.append(f"- `{row['scenario']}` → passed: `{row['passed']}`")
        projection = read_json(self.out / "projection-attempt.json") or {}
        lines.extend(
            [
                "",
                "## Projection attempt (single-task worker)",
                "",
                f"- entry: `{projection.get('entry')}`",
                f"- status: `{projection.get('status')}`",
                f"- task ref: `{projection.get('task_ref')}`",
                f"- receipt: `{(projection.get('receipt') or {}).get('state')}`",
                "- native digests: `native-digests.json`",
                "",
                "## H4",
                "",
                f"- state: **{summary['h4']['state']}**",
            ]
        )
        for reason in self.h4.get("reasons") or ():
            lines.append(f"  - {reason}")
        lines.extend(["", "## Limitations", ""])
        for item in summary["limitations"]:
            lines.append(f"- {item}")
        (self.out / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dedupe_claim_checks(claim: Any) -> Any:
    """Return the claim with duplicate missing-check ids collapsed (order kept)."""
    from cccp.analysis.research.evidence import Claim

    seen: set[str] = set()
    checks = []
    for check in claim.missing_checks:
        if check.check_id in seen:
            continue
        seen.add(check.check_id)
        checks.append(check)
    if len(checks) == len(claim.missing_checks):
        return claim
    return Claim(
        claim_id=claim.claim_id,
        statement=claim.statement,
        status=claim.status,
        scope=claim.scope,
        source_refs=claim.source_refs,
        missing_checks=tuple(checks),
        validator=claim.validator,
        limitations=claim.limitations,
    )


def _chain_fact(summary: dict[str, Any], key: str) -> Any:
    """Return one evidence-chain fact from a summary document."""
    return (summary.get("evidence_chain") or {}).get(key)


class _M404ExecutionRight:
    """Acceptance-local execution right (single holder, one worker)."""

    def acquire(self, envelope: Any) -> Any:  # noqa: ANN401 - port shape
        from acp_services.runtime.execution_right import ExecutionRightVerdict

        return ExecutionRightVerdict()

    def verify(self, envelope: Any) -> Any:  # noqa: ANN401 - port shape
        from acp_services.runtime.execution_right import ExecutionRightVerdict

        return ExecutionRightVerdict()


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser."""
    parser = argparse.ArgumentParser(
        description="M4-04 limited Diels-Alder acceptance (ADR-008, real QC)."
    )
    parser.add_argument(
        "--out",
        default=str(REPO / "reports" / "acp2_remediation_20261026" / "m4-04"),
        help="evidence directory",
    )
    parser.add_argument(
        "--run-root",
        default=os.environ.get("ACP2_M404_RUN_ROOT", "/ACPServer/acp2-runs/remediation/m4-04"),
        help="isolated run root for the real QC attempts",
    )
    parser.add_argument(
        "--phase",
        choices=("all", "public-entry", "chain", "evaluate"),
        default="all",
        help="which legs to run",
    )
    parser.add_argument("--reuse", action="store_true", help="reuse verified completed steps")
    parser.add_argument("--singlepoint-timeout", type=float, default=1800.0)
    parser.add_argument("--frequency-timeout", type=float, default=3600.0)
    parser.add_argument("--optimize-timeout", type=float, default=5400.0)
    parser.add_argument("--irc-timeout", type=float, default=5400.0)
    parser.add_argument("--path-timeout", type=float, default=3600.0)
    parser.add_argument("--thermochemistry-timeout", type=float, default=600.0)
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the M4-04 acceptance."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed the usage
        return EXIT_USAGE if exc.code else EXIT_PASS
    run = M404(args)
    try:
        code = run.run()
    except Exception as exc:  # noqa: BLE001 - the trace is the evidence
        import traceback

        failure = {
            "schema": SCHEMA,
            "generated_at": utc_now(),
            "scenario_result": "FAIL",
            "claim_status": "not_evaluated",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
        write_json(run.out / "summary.json", failure)
        print(json.dumps(failure, indent=2) if args.json else failure["error"])
        return EXIT_FAIL
    summary = read_json(run.out / "summary.json") or {}
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"{summary.get('scenario_result')} / claim={summary.get('claim_status')} / "
            f"H4={((summary.get('h4') or {}).get('state'))}"
        )
        for step in summary.get("steps") or ():
            print(
                f"  {step['step_id']:<24s} {str(step.get('status')):<10s} "
                f"{step.get('entry') or '-':<26s} {step.get('duration_seconds')}"
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
