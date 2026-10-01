"""T18: end-to-end propylene PESsearch acceptance re-run (slow-gated).

Re-runs the historical ``propylene_PESsearch`` job's exact scan config
(propylene xyz_text, dihedral atoms 4-0-1-2, 1°→359°, 90 points;
scan_optimizer GFN2-xTB, single_point B97-3c/mTZVP/``UltraFine``/TightSCF)
through the LOCAL CLI (``acp run PESsearch --mode bond_length_scan
--scan-config ... ``) with the fixed code, and asserts the post-fix
acceptance contract against the recorded pre-fix baseline.

Baseline grounding happens FIRST and fails loudly on drift: the historical
profile must still show the buggy fingerprint (90 frames,
``single_point_status=="failed"`` ×90, ``constraint_residual_ok==false`` ×90,
frame 0 dihedral residual ≈ −180.000097) — that is the "before" evidence.

Post-fix gates (the raw scan config bypasses catalog normalization, so the
cccp renderer's legacy grid alias must kick in):

* the generated ``sp_0000.inp`` contains NO ``UltraFine`` (aliased to
  ``DefGrid3``) — ORCA 6.1.1 rejects the legacy alias at input parse;
* 90/90 frames report ``single_point_status=="completed"`` with a parsed
  SP energy;
* zero off-constraint frames (T1 dihedral convention + T21 angular residual
  wrapping);
* non-empty TS/INT candidate recommendations.

Runs only under ``--run-slow``/``--run-integration``; skips when the real
ORCA binary or the historical baseline job dir is absent.  Timebox ≤ 2 h
(the historical job itself completes in minutes on this molecule).

Evidence: the after-run summary (counts, max residual, ORCA binary
path/sha256, wall time) is persisted under ``/tmp/opencode/t18-e2e/``
(override with ``ACP_PES_E2E_EVIDENCE_DIR``).

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ORCA_BIN = os.environ.get("ACP_PES_E2E_ORCA_BIN", "/home/xieningke/orca611/orca")
BASELINE_DIR = Path(
    os.environ.get(
        "ACP_PES_E2E_BASELINE_DIR", "/var/lib/acp/runs/uncategorized/propylene_PESsearch"
    )
)
EVIDENCE_DIR = Path(os.environ.get("ACP_PES_E2E_EVIDENCE_DIR", "/tmp/opencode/t18-e2e"))
BASELINE_PROFILE = BASELINE_DIR / "RESULT" / "pes_search" / "pes_profile.json"
BASELINE_SCAN_CONFIG = BASELINE_DIR / "scan_config.json"
E2E_TIMEOUT_S = 7200  # plan timebox: ≤ 2 h
FRAME0_RESIDUAL_TARGET = -180.000097  # buggy dihedral-convention fingerprint
FRAME0_RESIDUAL_TOL = 1e-3

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ground_baseline() -> dict:
    """Pre-assert and RECORD the buggy pre-fix fingerprint (Oracle rr-cccp-01 B2)."""
    if not BASELINE_PROFILE.is_file():
        pytest.skip(f"historical baseline profile absent: {BASELINE_PROFILE}")
    profile = json.loads(BASELINE_PROFILE.read_text(encoding="utf-8"))
    frames = profile.get("frames") or []
    assert len(frames) == 90, (
        f"baseline frame count drifted: expected 90, got {len(frames)} "
        f"({BASELINE_PROFILE}) — re-record the before-evidence before trusting this E2E"
    )
    failed = [f for f in frames if f.get("single_point_status") == "failed"]
    assert len(failed) == 90, (
        f"baseline SP-failure count drifted: expected 90/90 failed, got {len(failed)} "
        f"— the historical job no longer carries the buggy fingerprint"
    )
    off_constraint = [f for f in frames if f.get("constraint_residual_ok") is False]
    assert len(off_constraint) == 90, (
        f"baseline off-constraint count drifted: expected 90/90 false, got {len(off_constraint)}"
    )
    residual = (frames[0].get("constraint_residuals") or {}).get("dihedral")
    assert residual is not None and abs(residual - FRAME0_RESIDUAL_TARGET) <= FRAME0_RESIDUAL_TOL, (
        f"baseline frame-0 dihedral residual drifted: expected ≈ "
        f"{FRAME0_RESIDUAL_TARGET}, got {residual!r}"
    )
    return {
        "baseline_profile": str(BASELINE_PROFILE),
        "frames": len(frames),
        "single_point_failed": len(failed),
        "constraint_residual_ok_false": len(off_constraint),
        "frame0_residual_dihedral": residual,
    }


@pytest.fixture()
def scan_config_copy(tmp_path: Path) -> Path:
    """Copy the historical job's scan config (READ-ONLY source) into scratch."""
    if not BASELINE_SCAN_CONFIG.is_file():
        pytest.skip(f"historical scan config absent: {BASELINE_SCAN_CONFIG}")
    copy = tmp_path / "scan_config.json"
    shutil.copyfile(BASELINE_SCAN_CONFIG, copy)
    return copy


def test_pes_e2e_propylene_rerun_acceptance(scan_config_copy: Path, tmp_path: Path) -> None:
    """Ground the buggy baseline, re-run the scan locally, assert the fixed contract."""
    before = _ground_baseline()

    assert Path(ORCA_BIN).is_file(), f"real ORCA binary absent: {ORCA_BIN}"
    orca_sha256 = _sha256(Path(ORCA_BIN))

    output_dir = tmp_path / "out"
    cmd = [
        sys.executable,
        "-m",
        "acp.cli",
        "run",
        "PESsearch",
        "--mode",
        "bond_length_scan",
        "--scan-config",
        str(scan_config_copy),
        "--output",
        str(output_dir),
        "--nproc",
        "8",
        "--mem",
        "16GB",
    ]
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=E2E_TIMEOUT_S,
        check=False,
    )
    elapsed_s = time.monotonic() - started
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cli_stdout.log").write_text(proc.stdout, encoding="utf-8")
    (output_dir / "cli_stderr.log").write_text(proc.stderr, encoding="utf-8")
    assert proc.returncode == 0, (
        f"local PESsearch CLI failed (rc={proc.returncode}, {elapsed_s:.0f}s); "
        f"stderr tail: {proc.stderr[-2000:]}"
    )

    # -- generated SP input: legacy grid alias must be resolved (T5/T2) --
    sp_inputs = sorted(output_dir.glob("WORK/**/sp_0000/sp_0000.inp"))
    assert sp_inputs, f"no sp_0000.inp generated under {output_dir}"
    sp_input_text = sp_inputs[0].read_text(encoding="utf-8", errors="replace")
    assert "UltraFine" not in sp_input_text, (
        f"legacy grid alias leaked into the rendered SP input {sp_inputs[0]} — "
        f"the T5 UltraFine→DefGrid3 alias did not apply"
    )
    assert "DefGrid3" in sp_input_text, f"expected the aliased DefGrid3 keyword in {sp_inputs[0]}"

    # -- profile contract: 90/90 SP success, zero off-constraint frames --
    profile_path = output_dir / "RESULT" / "pes_search" / "pes_profile.json"
    assert profile_path.is_file(), f"pes_profile.json not written: {profile_path}"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    frames = profile.get("frames") or []
    assert len(frames) == 90, f"expected 90 scan frames, got {len(frames)}"

    sp_ok = [f for f in frames if f.get("single_point_status") == "completed"]
    assert len(sp_ok) == 90, (
        f"SP success count: expected 90/90 completed, got {len(sp_ok)} "
        f"(statuses: {sorted({f.get('single_point_status') for f in frames})})"
    )
    energies_missing = [f["index"] for f in frames if f.get("single_point_energy_hartree") is None]
    assert not energies_missing, f"frames without a parsed SP energy: {energies_missing}"

    off_constraint = [f["index"] for f in frames if f.get("constraint_residual_ok") is False]
    assert not off_constraint, (
        f"off-constraint frames: {off_constraint} — the T1 dihedral convention / "
        f"T21 angular-residual wrapping regressed"
    )
    residual_ok_count = sum(1 for f in frames if f.get("constraint_residual_ok") is True)
    assert residual_ok_count == 90, (
        f"expected 90/90 frames with constraint_residual_ok=true, got {residual_ok_count}"
    )
    residuals = [f.get("constraint_residuals", {}).get("dihedral") for f in frames]
    max_abs_residual = max(abs(r) for r in residuals if r is not None)

    # -- candidate recommendation: non-empty TS/INT set --
    ts_candidates = profile.get("ts_candidates") or []
    int_candidates = profile.get("int_candidates") or []
    assert len(ts_candidates) + len(int_candidates) > 0, (
        "candidate recommendation set is empty — expected TS/INT guesses from the dihedral profile"
    )

    after = {
        "output_dir": str(output_dir),
        "sp_input": str(sp_inputs[0]),
        "sp_input_has_ultrafine": "UltraFine" in sp_input_text,
        "frames": len(frames),
        "single_point_completed": len(sp_ok),
        "constraint_residual_ok_true": residual_ok_count,
        "max_abs_dihedral_residual": max_abs_residual,
        "ts_candidates": len(ts_candidates),
        "int_candidates": len(int_candidates),
        "orca_bin": ORCA_BIN,
        "orca_sha256": orca_sha256,
        "elapsed_s": round(elapsed_s, 1),
    }

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "results.json").write_text(
        json.dumps({"before": before, "after": after}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
