"""Read-only comparison of current sources with recorded acceptance evidence."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare(files: dict[str, str]) -> dict[str, object]:
    changes = []
    for name, recorded in files.items():
        path = ROOT / name
        current = digest(path) if path.is_file() else None
        if current != recorded:
            changes.append({"path": name, "recorded": recorded, "current": current})
    return {"compared_files": len(files), "changes": changes}


def junit(path: Path) -> dict[str, int]:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    counts = {"tests": len(cases)}
    for kind in ("failure", "error", "skipped"):
        counts[kind] = sum(case.find(kind) is not None for case in cases)
    counts["passed"] = len(cases) - sum(counts[k] for k in ("failure", "error", "skipped"))
    return counts


def main() -> None:
    repair = ROOT / "reports/architecture_repair_20261022/evidence"
    snapshot = json.loads((repair / "candidate-snapshot.json").read_text())
    selected = {
        name: value for name, value in snapshot["files"].items()
        if name.startswith(("src/", "frontend/", "packaging/", "scripts/", "tests/"))
        or name in ("pyproject.toml", "requirements-node.txt")
    }
    previous = json.loads((HERE / "source-inventory.json").read_text())
    tree = ast.parse((ROOT / "src/acp_services/runtime/execution_chain.py").read_text())
    workflow_sets = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if name in ("_C01_INTEGRATED", "_LEGACY_SCOPE"):
                workflow_sets[name] = [row[0] for row in ast.literal_eval(node.value)]
            elif name == "DEFAULT_ENVELOPE_WORKFLOWS":
                workflow_sets[name] = sorted(ast.literal_eval(node.value.args[0]))
    bootstrap = ast.parse((ROOT / "src/acp_services/bootstrap.py").read_text())
    ports = next(node for node in bootstrap.body if isinstance(node, ast.ClassDef)
                 and node.name == "ServicesPorts")
    fields = [node.target.id for node in ports.body if isinstance(node, ast.AnnAssign)]
    contract = (ROOT / "frontend/js/workbench/contracts/task_view_contract.js").read_text()
    flags = {key: re.search(rf"\b{key}:\s*(true|false)", contract).group(1) == "true"
             for key in ("eventCursor", "prepareAvailability")}
    roles = {}
    for role in ("cccp", "worker", "web"):
        manifest = tomllib.loads((ROOT / f"packaging/roles/{role}/pyproject.toml").read_text())
        roles[role] = {"distribution": manifest["project"]["name"],
                       "scripts": manifest["project"].get("scripts", {}),
                       "packages": manifest["tool"]["setuptools"]["packages"]["find"]["include"]}
    plan = ROOT / "docs/ACP_Architecture_Adjustment_Plan_20261016.md"
    result = {
        "scope": "2026-10-22 working tree; source inspection and existing evidence, no new full test run",
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "plan_sha256": digest(plan),
        "prior_current_architecture_inventory": compare(previous["source_sha256"]),
        "repair_candidate": snapshot["candidate_id"],
        "repair_candidate_comparison": compare(selected),
        "recorded_test_runs": {
            "current_architecture_targeted": junit(HERE / "architecture-tests.xml"),
            "repair_candidate_full_nonslow": junit(repair / "nonslow-final.xml"),
            "repair_candidate_browser": junit(repair / "browser-official.xml"),
        },
        "workflow_sets": workflow_sets,
        "transitional_services_ports": fields,
        "frontend_optional_capabilities": flags,
        "packaging_roles": roles,
        "frontend_sha256": {path.relative_to(ROOT).as_posix(): digest(path)
                            for path in sorted((ROOT / "frontend").rglob("*"))
                            if path.is_file() and path.suffix in (".js", ".html", ".css")},
    }
    destination = HERE / "plan-comparison-audit.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "frontend_sha256"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
