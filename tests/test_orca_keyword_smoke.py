"""T17: real-ORCA 6.1.1 keyword smoke matrix (slow-gated).

Runs the REAL ORCA binary on generated 3-atom single points and records the
T22 outcome taxonomy (``valid_completion`` / ``syntax_error`` /
``missing_dependency`` / ``calculation_failure``) plus the first error line
for every case.  Verdicts are compared against the committed T22 probe
fixture ``tests/fixtures/orca_keyword_probe.json`` — fixture-backed cases
assert fixture consistency (never hardcoded literals); non-fixture cases are
recorded into the evidence table.

Artifact gates (exit code alone is NEVER a pass):

* SP cases require normal termination AND a parsed
  ``FINAL SINGLE POINT ENERGY``.
* The Q9 arbiter case ``! GFN2-xTB NMR`` requires non-empty
  :class:`~cccp.qc.interfaces.orca.NmrShieldingParser` shielding tensors to
  count as a completion — the fixture verdict (``calculation_failure``, 0
  atoms) must reproduce.

Capability/dependency/policy split: a missing parameter file classifies as
``missing_dependency`` (GFN0-xTB), never "unsupported".  A probe verdict
NEVER flips platform policy (:data:`~cccp.qc.keyword_registry.GFN_NMR_DEFAULT_ALLOWED`
stays CLOSED); a contradicting verdict fails the suite and reopens
T2/T4/T7/T12 instead of silently adjusting expectations.

Evidence: per-case rows are persisted under ``/tmp/opencode/t17-smoke/``
(override with ``ACP_ORCA_SMOKE_EVIDENCE_DIR``) as ``results.json`` +
``results_table.txt``, together with the ORCA binary path/sha256,
``otool_xtb`` version, and parameter-file inventory.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from cccp.qc.interfaces.orca import NmrShieldingParser
from cccp.qc.interfaces.route_render import RouteKeyword, render_route_line
from cccp.qc.keyword_registry import GFN_NMR_DEFAULT_ALLOWED, method_policy
from tests.conftest import get_real_qc_snapshot

# ORCA for this matrix: the ACP_ORCA_SMOKE_BIN override, else the conftest
# production gate path (RealQCSnapshot = cccp.config.load_config +
# cccp.software.resolve_executable, resolved once at collection).  Only an
# EXPLICITLY configured gate (yaml path or CONFSEARCH_ORCA_PATH) opens the
# module: a PATH/scan hit such as the unrelated /usr/bin/orca script must
# never run the ORCA 6.1.1 verdict matrix (BUG-4 retired the hardcoded
# machine default; the env override stays authoritative).
_ORCA_OVERRIDE = os.environ.get("ACP_ORCA_SMOKE_BIN")
_ORCA_GATE = get_real_qc_snapshot().binary("orca")

if _ORCA_OVERRIDE:
    ORCA_BIN = _ORCA_OVERRIDE
    _SKIP_REASON = "" if Path(ORCA_BIN).exists() else f"ORCA binary not present: {ORCA_BIN}"
elif _ORCA_GATE.source in {"config", "env"} and _ORCA_GATE.path is not None:
    ORCA_BIN = str(_ORCA_GATE.path)
    _SKIP_REASON = "" if Path(ORCA_BIN).exists() else f"ORCA binary not present: {ORCA_BIN}"
else:
    ORCA_BIN = ""
    _SKIP_REASON = (
        "production ORCA not explicitly configured "
        f"({_ORCA_GATE.provenance}); set ACP_ORCA_SMOKE_BIN to override"
    )
OTOL_XTB = Path(ORCA_BIN).parent / "otool_xtb"
# T22 deployment default: param_gfn0-xtb.txt lives only under .../share/xtb,
# so this XTBPATH reproduces the recorded missing_dependency verdict.
DEFAULT_XTBPATH = "/home/<user>/xtb-dist/bin"
EVIDENCE_DIR = Path(os.environ.get("ACP_ORCA_SMOKE_EVIDENCE_DIR", "/tmp/opencode/t17-smoke"))
PROBE_FIXTURE = Path(__file__).parent / "fixtures" / "orca_keyword_probe.json"
CASE_TIMEOUT_S = 300
ENERGY_TOL_EH = 1e-6

OUTCOME_CLASSES = frozenset(
    {"valid_completion", "syntax_error", "missing_dependency", "calculation_failure", "timeout"}
)

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(bool(_SKIP_REASON), reason=_SKIP_REASON),
]


def _load_probe() -> dict:
    return json.loads(PROBE_FIXTURE.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SmokeCase:
    """One matrix row: raw ORCA simple-input keywords + expectations.

    ``expected_outcome`` is hard-asserted when set; fixture-backed cases
    derive it from the probe fixture at runtime instead of a literal.
    ``record_only`` cases only assert taxonomy membership and feed the
    evidence table (anti-drift: no hardcoded accept/reject).
    """

    case_id: str
    keywords: str
    required_artifact: str = "energy"  # "energy" | "shieldings"
    expected_outcome: str | None = None
    record_only: bool = False


def _fixture_cases() -> dict[str, SmokeCase]:
    probe = _load_probe()
    cases: dict[str, SmokeCase] = {}
    for case in probe["cases"]:
        cases[case["case_id"]] = SmokeCase(
            case_id=case["case_id"],
            keywords=case["keywords"],
            required_artifact=(
                "shieldings" if case["required_artifact"] == "shielding_atoms" else "energy"
            ),
            expected_outcome=case["outcome_class"],
        )
    return cases


# Non-fixture grid cases: expected verdicts from the T17 task matrix
# (probe-confirmed on ORCA 6.1.1: DefGrid1/2/3 accepted; Gaussian-era
# spellings rejected as UNRECOGNIZED).
_GRID_EXTRA_CASES: tuple[SmokeCase, ...] = (
    SmokeCase("defgrid1", "DEFGRID1", expected_outcome="valid_completion"),
    SmokeCase("defgrid2", "DEFGRID2", expected_outcome="valid_completion"),
    SmokeCase("grid5", "Grid5", expected_outcome="syntax_error"),
    SmokeCase("sg1", "SG1", expected_outcome="syntax_error"),
    SmokeCase("fine", "Fine", expected_outcome="syntax_error"),
    SmokeCase("superfine", "SuperFine", expected_outcome="syntax_error"),
)

# ORCA 6.1 explicitly reports SMD/CPCM + xTB as "not implemented"; the
# registry strips these pre-assembly (policy {none, ALPB}), so the raw
# capability verdict is recorded without a hard accept/reject assertion.
_GFN_SOLVENT_RECORD_CASES: tuple[SmokeCase, ...] = (
    SmokeCase("gfn2-xtb-cpcm-water", "GFN2-xTB CPCM(water)", record_only=True),
    SmokeCase("gfn2-xtb-smd-water", "GFN2-xTB SMD(water)", record_only=True),
)

ALL_CASES: tuple[SmokeCase, ...] = (
    tuple(_fixture_cases().values()) + _GRID_EXTRA_CASES + _GFN_SOLVENT_RECORD_CASES
)


@dataclass
class CaseResult:
    """Observed outcome of one real-ORCA run (evidence row)."""

    case_id: str
    keywords: str
    rc: int
    outcome_class: str
    first_error_line: str | None
    normal_termination: bool
    energy_hartree: float | None
    shielding_atoms: int | None
    wall_s: float
    output_sha256: str
    output_file: str = ""
    notes: list[str] = field(default_factory=list)


_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"UNRECOGNIZED OR DUPLICATED KEYWORD.*"),
    re.compile(r"Parameter file \S+ not found.*"),
    re.compile(r"^.*not implemented\.\s*$", re.M),
    re.compile(r"^\s*INPUT ERROR\s*$", re.M),
    re.compile(r"Error \(ORCA_MAIN\).*"),
    re.compile(r"\[ERROR\] Program stopped due to fatal error.*"),
)

_ENERGY_RE = re.compile(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)")


def _first_error_line(text: str) -> str | None:
    for pattern in _ERROR_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        line = match.group(0).strip()
        # ORCA 6 echoes the offending keyword on the line AFTER the
        # UNRECOGNIZED banner — join them so the evidence matches the
        # fixture's first_error_line style.
        if "UNRECOGNIZED" in line:
            lines_after = text[match.end() :].splitlines()
            tail = next((ln.strip() for ln in lines_after if ln.strip()), "")
            if tail and "INPUT ERROR" not in tail:
                line = f"{line}: {tail}"
        return line
    return None


def _classify(text: str) -> tuple[str, bool, float | None]:
    """Map raw output text to ``(outcome_class, normal_termination, energy)``."""
    normal = "ORCA TERMINATED NORMALLY" in text
    energy_match = _ENERGY_RE.search(text)
    energy = float(energy_match.group(1)) if energy_match else None
    if "UNRECOGNIZED OR DUPLICATED KEYWORD" in text:
        return "syntax_error", normal, energy
    if re.search(r"Parameter file \S+ not found", text):
        # dependency gap (e.g. GFN0-xTB param resolution) — NEVER "unsupported"
        return "missing_dependency", normal, energy
    if normal and energy is not None:
        return "valid_completion", normal, energy
    if _first_error_line(text):
        rejected = "syntax_error" if "not implemented" in text else "calculation_failure"
        return rejected, normal, energy
    return "calculation_failure", normal, energy


def run_orca_case(run_dir: Path, case: SmokeCase) -> CaseResult:
    """Run one real ORCA single point in ``run_dir`` and classify the output."""
    inp_path = run_dir / f"{case.case_id}.inp"
    out_path = run_dir / f"{case.case_id}.out"
    inp_path.write_text(f"! {case.keywords}\n\n{_geometry_block()}\n", encoding="utf-8")

    env = dict(os.environ)
    env["XTBPATH"] = os.environ.get("ACP_ORCA_SMOKE_XTBPATH", DEFAULT_XTBPATH)
    start = time.monotonic()
    rc = -1
    timed_out = False
    with out_path.open("w", encoding="utf-8") as out_handle:
        try:
            proc = subprocess.run(
                [ORCA_BIN, inp_path.name],
                cwd=run_dir,
                stdout=out_handle,
                stderr=subprocess.STDOUT,
                env=env,
                timeout=CASE_TIMEOUT_S,
            )
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    wall_s = time.monotonic() - start

    text = out_path.read_text(encoding="utf-8", errors="replace")
    if timed_out:
        outcome_class, normal, energy = "timeout", False, None
    else:
        outcome_class, normal, energy = _classify(text)

    shielding_atoms: int | None = None
    if case.required_artifact == "shieldings":
        shieldings = NmrShieldingParser.parse(out_path)
        shielding_atoms = len(shieldings)
        # Artifact gate: rc=0 + normal termination + an energy is still NOT a
        # completion unless the parser actually recovered shielding tensors.
        if outcome_class == "valid_completion" and shielding_atoms == 0:
            outcome_class = "calculation_failure"

    return CaseResult(
        case_id=case.case_id,
        keywords=case.keywords,
        rc=rc,
        outcome_class=outcome_class,
        first_error_line=None if outcome_class == "valid_completion" else _first_error_line(text),
        normal_termination=normal,
        energy_hartree=energy,
        shielding_atoms=shielding_atoms,
        wall_s=round(wall_s, 2),
        output_sha256=hashlib.sha256(out_path.read_bytes()).hexdigest(),
        output_file=str(out_path),
    )


def _geometry_block() -> str:
    return _load_probe()["input_xyz"]


def _assert_fixture_consistency(result: CaseResult, probe_case: dict) -> None:
    """Assert a fixture-backed case reproduces the committed T22 verdict."""
    assert result.outcome_class == probe_case["outcome_class"], (
        f"{result.case_id}: real-ORCA verdict {result.outcome_class!r} contradicts "
        f"fixture {probe_case['outcome_class']!r} — reopen T2/T4/T7/T12, do NOT "
        "silently adjust expectations"
    )
    if probe_case["outcome_class"] == "valid_completion":
        assert result.normal_termination, result.case_id
        assert result.energy_hartree is not None, result.case_id
        fixture_energy = probe_case["artifact"]["energy_hartree"]
        assert abs(result.energy_hartree - fixture_energy) < ENERGY_TOL_EH, (
            result.case_id,
            result.energy_hartree,
            fixture_energy,
        )


@pytest.fixture(scope="module")
def evidence():
    """Accumulate CaseResult rows and persist the evidence table at teardown."""
    rows: list[CaseResult] = []
    yield rows

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    probe = _load_probe()
    header = {
        "schema": "cccp_orca_keyword_smoke_v1",
        "orca_path": ORCA_BIN,
        "orca_sha256": _sha256_file(Path(ORCA_BIN)),
        "orca_version": _orca_version(rows),
        "otool_xtb_path": str(OTOL_XTB),
        "otool_xtb_sha256": _sha256_file(OTOL_XTB) if OTOL_XTB.exists() else None,
        "otool_xtb_version": _otool_xtb_version(),
        "xtbpath": os.environ.get("ACP_ORCA_SMOKE_XTBPATH", DEFAULT_XTBPATH),
        "parameter_files": [
            {**pf, "present": Path(pf["path"]).exists()} for pf in probe["parameter_files"]
        ],
        "gfn_nmr_default_allowed": GFN_NMR_DEFAULT_ALLOWED,
    }
    payload = {
        **header,
        "totals": _totals(rows),
        "cases": [row.__dict__ for row in rows],
    }
    (EVIDENCE_DIR / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (EVIDENCE_DIR / "results_table.txt").write_text(_render_table(payload, rows), encoding="utf-8")


def _totals(rows: list[CaseResult]) -> dict[str, int]:
    totals: dict[str, int] = {"cases": len(rows)}
    for row in rows:
        totals[row.outcome_class] = totals.get(row.outcome_class, 0) + 1
    return totals


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _otool_xtb_version() -> str | None:
    if not OTOL_XTB.exists():
        return None
    proc = subprocess.run(
        [str(OTOL_XTB), "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    match = re.search(r"\d+\.\d+\.\d+ \([0-9a-f]+\) compiled [^\n]*", proc.stdout + proc.stderr)
    return match.group(0) if match else (proc.stdout.strip() or None)


def _orca_version(rows: list[CaseResult]) -> str | None:
    for row in rows:
        out = Path(row.output_file)
        if out.exists():
            match = re.search(r"Program Version (\S+)", out.read_text(errors="replace"))
            if match:
                return match.group(1)
    return None


def _render_table(header: dict, rows: list[CaseResult]) -> str:
    orca_sha = header["orca_sha256"][:16]
    otol_sha = (header["otool_xtb_sha256"] or "")[:16]
    lines = [
        "T17 real-ORCA keyword smoke matrix — evidence table",
        f"orca: {header['orca_path']} sha256={orca_sha}… version={header['orca_version']}",
        f"otool_xtb: {header['otool_xtb_version']} (sha256={otol_sha}…)",
        f"XTBPATH={header['xtbpath']}  GFN_NMR_DEFAULT_ALLOWED={header['gfn_nmr_default_allowed']}",
        "parameter files: "
        + ", ".join(
            f"{pf['name']}({'✓' if pf['present'] else '✗'})" for pf in header["parameter_files"]
        ),
        f"totals: {header['totals']}",
        "",
        "| case_id | keywords | rc | outcome_class | first_error_line "
        "| energy/Eh | shieldings | wall_s |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        error = (row.first_error_line or "")[:80].replace("|", "\\|")
        energy = "" if row.energy_hartree is None else f"{row.energy_hartree:.9f}"
        shieldings = "" if row.shielding_atoms is None else str(row.shielding_atoms)
        lines.append(
            f"| {row.case_id} | {row.keywords} | {row.rc} | {row.outcome_class} "
            f"| {error} | {energy} | {shieldings} | {row.wall_s} |"
        )
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize(
    "case",
    ALL_CASES,
    ids=[case.case_id for case in ALL_CASES],
)
def test_orca_keyword_smoke_matrix(case: SmokeCase, tmp_path: Path, evidence: list) -> None:
    probe_by_id = {c["case_id"]: c for c in _load_probe()["cases"]}
    result = run_orca_case(tmp_path, case)
    evidence.append(result)

    assert result.outcome_class in OUTCOME_CLASSES, (case.case_id, result.outcome_class)
    assert result.wall_s < CASE_TIMEOUT_S, case.case_id

    if case.case_id in probe_by_id:
        _assert_fixture_consistency(result, probe_by_id[case.case_id])
        # case-specific verdict details pinned by the T22 probe
        if case.case_id == "gfn0-xtb":
            assert result.first_error_line and "param_gfn0-xtb.txt" in result.first_error_line
            assert result.outcome_class == "missing_dependency"  # dependency, NOT capability
        elif case.case_id == "gfn2-xtb-gbsa-water":
            assert result.first_error_line and "GBSA(WATER)" in result.first_error_line.upper()
        elif case.case_id == "ultrafine":
            assert result.first_error_line and "ULTRAFINE" in result.first_error_line.upper()
        elif case.case_id == "gfn2-xtb-nmr":
            assert result.shielding_atoms == 0, (
                "GFN+NMR produced parsed shielding tensors — artifact-level "
                "capability changed; reopen T8 policy decision explicitly"
            )
            assert GFN_NMR_DEFAULT_ALLOWED is False  # probe never flips policy
    elif case.expected_outcome is not None:
        assert result.outcome_class == case.expected_outcome, (
            case.case_id,
            result.outcome_class,
            result.first_error_line,
        )
    if case.case_id in {"gfn2-xtb-def2-svp", "gfn2-xtb-d4", "gfn2-xtb-defgrid3"}:
        # accepted-but-silently-ignored: energy identical to plain GFN2-xTB
        plain = probe_by_id["gfn2-xtb"]["artifact"]["energy_hartree"]
        assert result.energy_hartree is not None
        assert abs(result.energy_hartree - plain) < ENERGY_TOL_EH, (
            case.case_id,
            result.energy_hartree,
        )


def test_renderer_gfn_output_accepted_by_real_orca(tmp_path: Path, evidence: list) -> None:
    """The fixed renderer's GFN route line contains no DFT tokens and runs."""
    route = render_route_line(
        [
            "GFN2-xTB",
            RouteKeyword("scf_convergence", "Tight"),
            RouteKeyword("grid", "UltraFine"),
            RouteKeyword("dispersion", "D4"),
            RouteKeyword("basis", "def2-SVP"),
        ],
        method="GFN2-xTB",
    )
    assert set(route.split()) == {"!", "GFN2-xTB", "TightSCF"}, route

    case = SmokeCase("renderer-gfn2-xtb", route.removeprefix("! "))
    inp_path = tmp_path / f"{case.case_id}.inp"
    inp_path.write_text(f"{route}\n\n{_geometry_block()}\n", encoding="utf-8")
    env = dict(os.environ)
    env["XTBPATH"] = os.environ.get("ACP_ORCA_SMOKE_XTBPATH", DEFAULT_XTBPATH)
    start = time.monotonic()
    with (tmp_path / f"{case.case_id}.out").open("w", encoding="utf-8") as handle:
        proc = subprocess.run(
            [ORCA_BIN, inp_path.name],
            cwd=tmp_path,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=CASE_TIMEOUT_S,
        )
    wall_s = round(time.monotonic() - start, 2)
    out_path = tmp_path / f"{case.case_id}.out"
    text = out_path.read_text(encoding="utf-8", errors="replace")
    outcome_class, normal, energy = _classify(text)
    evidence.append(
        CaseResult(
            case_id=case.case_id,
            keywords=route,
            rc=proc.returncode,
            outcome_class=outcome_class,
            first_error_line=(
                None if outcome_class == "valid_completion" else _first_error_line(text)
            ),
            normal_termination=normal,
            energy_hartree=energy,
            shielding_atoms=None,
            wall_s=wall_s,
            output_sha256=hashlib.sha256(out_path.read_bytes()).hexdigest(),
            output_file=str(out_path),
        )
    )

    assert outcome_class == "valid_completion", (outcome_class, _first_error_line(text))
    plain = _load_probe_cases_energy("gfn2-xtb")
    assert energy is not None and abs(energy - plain) < ENERGY_TOL_EH, energy


def _load_probe_cases_energy(case_id: str) -> float:
    for c in _load_probe()["cases"]:
        if c["case_id"] == case_id:
            return c["artifact"]["energy_hartree"]
    raise AssertionError(case_id)


def _policy_invariants_unchanged() -> None:
    """The matrix records capability; platform policy decisions stay put."""
    assert GFN_NMR_DEFAULT_ALLOWED is False
    assert not method_policy("GFN0-xTB", engine="orca").allowed
    assert method_policy("GFN0-xTB", engine="xtb").allowed


def test_policy_invariants_after_matrix() -> None:
    _policy_invariants_unchanged()
    # which(abs) is subsumed by the file check (which => exists); keep the
    # contract as a plain presence assert on the production/env-resolved path.
    assert Path(ORCA_BIN).is_file()
