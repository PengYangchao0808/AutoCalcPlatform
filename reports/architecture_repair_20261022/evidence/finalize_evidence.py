"""Finalize evidence only after the frozen candidate's full pytest run ends."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    full = Path("/tmp/acp-repair-nonslow-final.xml")
    tree = ET.parse(full).getroot()
    cases = tree.findall(".//testcase")
    failures = [c for c in cases if c.find("failure") is not None or c.find("error") is not None]
    assert not failures, [(c.get("classname"), c.get("name")) for c in failures]
    skipped = [c for c in cases if c.find("skipped") is not None]
    results = {(c.get("classname"), c.get("name")): c for c in cases}
    for module, expected in (
        ("tests.test_services_worker_artifact_root", 3),
        ("tests.test_services_cgroup_attempt_recovery", 15),
        ("tests.test_acp_frontend_browser", 4),
    ):
        cohort = [c for c in cases if c.get("classname") == module]
        assert len(cohort) == expected, (module, len(cohort))
        assert not [c for c in cohort if c.find("skipped") is not None], module
    original = (
        ET.parse(ROOT / "reports/architecture_debug_20261021/evidence/integration-final.xml")
        .getroot()
        .findall(".//testcase")
    )
    assert len(original) == 759
    for case in original:
        key = (case.get("classname"), case.get("name"))
        assert key in results, key
        assert results[key].find("skipped") is None, key

    snapshot = json.loads((EVIDENCE / "candidate-snapshot.json").read_text())
    prefixes = ("src/", "tests/", "frontend/", "scripts/", "packaging/", "config/")
    runtime_roots = {"pyproject.toml", "requirements-node.txt"}
    drift = [
        {"path": path, "frozen": digest, "current": sha(ROOT / path)}
        for path, digest in snapshot["files"].items()
        if sha(ROOT / path) != digest
    ]
    assert not [d for d in drift if d["path"].startswith(prefixes) or d["path"] in runtime_roots]
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    new = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    current_runtime = {p for p in tracked + new if p.startswith(prefixes)}
    frozen_runtime = {p for p in snapshot["files"] if p.startswith(prefixes)}
    current_runtime.discard("tests/baseline/refactor-evidence/e2e-task-root.txt")
    assert current_runtime == frozen_runtime, current_runtime ^ frozen_runtime

    marker = ROOT / "tests/baseline/refactor-evidence/e2e-task-root.txt"
    assert sha(marker) == sha(Path("/tmp/acp-repair-before-full-e2e-task-root.txt"))
    log = Path("/tmp/acp-repair-nonslow-final.log").read_text()
    final_line = next(line for line in reversed(log.splitlines()) if " passed" in line)
    assert " failed" not in final_line and " error" not in final_line
    summary = {
        "candidate_id": snapshot["candidate_id"],
        "full_result": "PASS",
        "full_summary": final_line,
        "full_counts": {"passed": len(cases) - len(skipped), "skipped": len(skipped), "failed": 0},
        "original_759_passed": 759,
        "runtime_source_drift": [],
        "post_freeze_nonruntime_drift": drift,
        "existing_e2e_marker_restored": True,
        "skips": [
            {
                "classname": c.get("classname"),
                "name": c.get("name"),
                "reason": c.find("skipped").get("message") or c.find("skipped").text,
            }
            for c in skipped
        ],
    }
    for suffix in ("xml", "log"):
        shutil.copy2(
            f"/tmp/acp-repair-nonslow-final.{suffix}", EVIDENCE / f"nonslow-final.{suffix}"
        )
    (EVIDENCE / "verification-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "skips"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
