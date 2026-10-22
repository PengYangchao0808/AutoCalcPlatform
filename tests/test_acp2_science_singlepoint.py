"""A-10b: installed-singlepoint scenario preflight and native re-parse.

Every run spec and native-output value in this module is **constructed** in a
temporary directory.  No QC executable is started, no run key is issued and no
real energy is claimed.  The positive execution row stays ``NOT_VERIFIED`` on
this host because the platform run spec, QC binary and H2 test candidate are
absent; these tests pin that refusal and exercise the independent parser with a
clearly labelled synthetic fixture.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = REPO_ROOT / "scripts" / "acceptance" / "acp2_science.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "singlepoint"
FIXTURE_MANIFEST = FIXTURE_DIR / "fixture-manifest.json"
CONSTRUCTED_NATIVE = FIXTURE_DIR / "constructed-native-output.json"


def _load_harness() -> Any:
    name = "acp2_science_harness_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _budget(*, total_used: int = 0) -> dict[str, Any]:
    return {
        "caps": {
            "total": {"limit": 2, "used": total_used},
            "phase": {"limit": 2, "used": 0},
            "scenario": {"limit": 2, "used": 0},
            "per_unit": {"limit": 1, "used": 0},
        },
        "counts": {
            "task_ref": {"limit": 1, "used": 0},
            "recipe_step": {"limit": 0, "used": 0},
            "qc_invocation": {"limit": 1, "used": 0},
            "attempt": {"limit": 1, "used": 0},
            "bsub": {"limit": 0, "used": 0},
        },
        "deadlines": {
            "queue_s": 600,
            "compute_s": 900,
            "client_s": 1200,
            "total_s": 1800,
        },
    }


def _run_spec(
    tmp_path: Path,
    *,
    executable: Path | None = None,
    total_used: int = 0,
    approval: bool = False,
) -> dict[str, Any]:
    """Construct an explicitly labelled, non-issued run spec for a test."""
    spec: dict[str, Any] = {
        "run_key": "constructed-test-only-not-issued",
        "interpreter": sys.executable,
        "native_work_root": str(tmp_path / "native-root"),
        "software": {
            "name": "ORCA",
            "version": "0.0-constructed",
            "executable": str(executable or (tmp_path / "missing-orca")),
        },
        "source_digest": "sha256:" + "0" * 64,
        "scheduler": "local",
        "budget": _budget(total_used=total_used),
    }
    if approval:
        spec["run_key_approval"] = {
            "approved": True,
            "authority": "CONSTRUCTED TEST ONLY",
            "issued_at": "2026-01-01T00:00:00Z",
            "budget_ref": "constructed-test",
            "owner": "A",
            "scenario": "science:installed-singlepoint",
            "phase": "main",
            "run_id": "constructed-run",
        }
    return spec


def _write_run_spec(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "run-spec.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _invoke(
    harness: Any,
    tmp_path: Path,
    run_spec: Path | None,
    *,
    out_name: str = "evidence",
    extra: tuple[str, ...] = (),
) -> int:
    argv = [
        "--scenario",
        "science:installed-singlepoint",
        "--fixture",
        str(FIXTURE_MANIFEST),
        "--out",
        str(tmp_path / out_name),
        *extra,
    ]
    if run_spec is not None:
        argv.extend(["--run-spec", str(run_spec)])
    return int(harness.main(argv))


def test_harness_imports_no_project_package_at_module_level() -> None:
    tree = ast.parse(HARNESS_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "cccp" not in imported
    assert "acp" not in imported
    assert "acp_services" not in imported


def test_help_exits_zero_and_lists_singlepoint_scenario(capsys: Any) -> None:
    harness = _load_harness()
    assert harness.main(["--help"]) == harness.EXIT_PASS
    output = capsys.readouterr().out
    assert "science:installed-singlepoint | installed-singlepoint" in output
    assert "0=PASS, 1=FAIL, 2=NOT_VERIFIED, 3=usage" in output


def test_without_run_spec_is_usage(tmp_path: Path) -> None:
    harness = _load_harness()
    assert _invoke(harness, tmp_path, None) == harness.EXIT_USAGE
    assert not (tmp_path / "evidence").exists()


def test_dry_run_with_constructed_spec_creates_nothing(tmp_path: Path) -> None:
    harness = _load_harness()
    run_spec = _write_run_spec(tmp_path, _run_spec(tmp_path))
    assert _invoke(harness, tmp_path, run_spec, extra=("--dry-run",)) == harness.EXIT_PASS
    assert not (tmp_path / "evidence").exists()
    assert not (tmp_path / "native-root").exists()


def test_missing_run_spec_fields_exit_not_verified(tmp_path: Path) -> None:
    harness = _load_harness()
    run_spec = _write_run_spec(tmp_path, {})
    assert _invoke(harness, tmp_path, run_spec) == harness.EXIT_NOT_VERIFIED
    record = json.loads((tmp_path / "evidence" / "result.json").read_text(encoding="utf-8"))
    assert record["result"] == "NOT_VERIFIED"
    assert "run_key" in record["reason"]


def test_exhausted_budget_is_refused_before_submission(tmp_path: Path) -> None:
    harness = _load_harness()
    run_spec = _write_run_spec(tmp_path, _run_spec(tmp_path, total_used=2))
    assert _invoke(harness, tmp_path, run_spec) == harness.EXIT_NOT_VERIFIED
    record = json.loads((tmp_path / "evidence" / "result.json").read_text(encoding="utf-8"))
    assert record["result"] == "NOT_VERIFIED"
    assert "budget refused before submission" in record["reason"]
    assert "cap total exhausted" in record["reason"]
    assert not (tmp_path / "native-root").exists()


def test_missing_binary_is_not_verified_and_records_gaps(tmp_path: Path) -> None:
    harness = _load_harness()
    run_spec = _write_run_spec(tmp_path, _run_spec(tmp_path))
    assert _invoke(harness, tmp_path, run_spec) == harness.EXIT_NOT_VERIFIED
    record = json.loads((tmp_path / "evidence" / "result.json").read_text(encoding="utf-8"))
    science = record["extensions"]["science"]

    assert record["result"] == "NOT_VERIFIED"
    assert "software executable not found" in record["reason"]
    assert "run_key_approval" in record["reason"]
    assert "H2 test candidate not published" in record["reason"]
    assert science["software_check"]["available"] is False
    assert science["run_key_approval"]["approved"] is False
    assert science["run_spec_gaps"] == []
    assert science["candidate"] == {
        "server_revision": None,
        "client_revision": None,
        "harness_revision": None,
    }
    assert science["candidate_status"] == "NOT_VERIFIED"
    assert science["h2_test_candidate"]["candidate_revision"] is None
    assert science["receipt_protocol_gap"] == "NOT_VERIFIED"
    assert not (tmp_path / "native-root").exists()
    assert not (tmp_path / "evidence" / "task-request.json").exists()
    assert not (tmp_path / "evidence" / "native").exists()


def test_constructed_native_parse_matches_within_tolerance() -> None:
    harness = _load_harness()
    fixture = json.loads(CONSTRUCTED_NATIVE.read_text(encoding="utf-8"))
    assert fixture["constructed"] is True
    assert fixture["provenance"] == "constructed"
    assert "NOT A REAL CALCULATION" in fixture["output"]

    parsed = harness._parse_native_energy(fixture["output"], fixture["software"])
    assert parsed["found"] is True
    assert parsed["normal_termination"] is True
    assert parsed["unit"] == "hartree"
    assert parsed["energy_hartree"] == fixture["expected"]["energy_hartree"]

    comparison = harness._compare_energy(fixture["expected"]["energy_hartree"], parsed, 1e-06)
    assert comparison["match"] is True
    assert comparison["delta_hartree"] == 0.0


def test_constructed_native_parse_rejects_mismatch_missing_marker_and_wrong_unit() -> None:
    harness = _load_harness()
    fixture = json.loads(CONSTRUCTED_NATIVE.read_text(encoding="utf-8"))
    parsed = harness._parse_native_energy(fixture["output"], fixture["software"])

    mismatch = harness._compare_energy(fixture["expected"]["energy_hartree"] + 1e-3, parsed, 1e-06)
    assert mismatch["match"] is False
    assert "energy mismatch" in mismatch["reason"]

    missing = harness._parse_native_energy("no energy here\n", "ORCA")
    assert missing["found"] is False
    assert harness._compare_energy(-1.0, missing, 1e-06)["match"] is False

    wrong_unit = dict(parsed)
    wrong_unit["unit"] = "kcal/mol"
    assert harness._compare_energy(-1.0, wrong_unit, 1e-06)["match"] is False

    xtb = harness._parse_native_energy(
        "| TOTAL ENERGY  -5.070000000000 Eh\nnormal termination\n", "xTB"
    )
    assert xtb["found"] is True
    assert xtb["unit"] == "hartree"
    assert xtb["normal_termination"] is True
    assert harness._compare_energy(-5.07, xtb, 1e-06)["match"] is True
