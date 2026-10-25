"""Regression guards for the per-slice isolation/dependency harness (plan-3 T48).

Pure-function tests only (no subprocess probes): they lock the reverse-dependency
scan semantics, the workflow→task/helper registry integrity, the negative
injection detection and the dynamic discovery extension point.  The heavier
acp-free child probes run through ``scripts/verify_cccp_slice_guard.py`` and its
evidence records.
"""

from __future__ import annotations

from pathlib import Path

from scripts.verify_cccp_slice_guard import (
    ACTIVE_WORKFLOWS,
    DEFAULT_CCCP_ROOT,
    DEFAULT_MAP,
    check_workflow_map,
    discover_task_cores,
    discover_top_level_modules,
    injection_selftest,
    load_expected_tasks,
    parse_workflow_map,
    scan_reverse_dependencies,
)

VIOLATION_CATEGORIES = frozenset(
    {"static_import", "dynamic_import_target", "cli_detour", "string_reference"}
)


def test_static_scan_finds_no_reverse_dependency() -> None:
    findings = scan_reverse_dependencies(DEFAULT_CCCP_ROOT)
    violations = [f for f in findings if f.category in VIOLATION_CATEGORIES]
    assert not violations, "\n".join(
        f"{f.category} {f.path}:{f.line} {f.detail}" for f in violations
    )


def test_scan_detects_injected_static_import(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("import acp\n", encoding="utf-8")
    findings = scan_reverse_dependencies(tmp_path)
    assert any(f.category == "static_import" for f in findings)


def test_scan_ignores_docstring_and_comment_references(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        '"""Doc mentioning acp.core.models and from acp import x."""\n'
        "# comment: from acp import y\n"
        "VALUE = 1\n",
        encoding="utf-8",
    )
    assert scan_reverse_dependencies(tmp_path) == []


def test_scan_detects_dynamic_and_cli_targets(tmp_path: Path) -> None:
    (tmp_path / "dyn.py").write_text(
        "import importlib\n"
        "import subprocess\n"
        "m = importlib.import_module('acp.calculations.identity')\n"
        "subprocess.run(['acp', 'run', 'scan'])\n",
        encoding="utf-8",
    )
    categories = {f.category for f in scan_reverse_dependencies(tmp_path)}
    assert "dynamic_import_target" in categories
    assert "cli_detour" in categories


def test_workflow_map_covers_all_active_workflows() -> None:
    result = check_workflow_map(DEFAULT_MAP, DEFAULT_CCCP_ROOT)
    assert result.passed, "\n".join(result.findings)
    rows = parse_workflow_map(DEFAULT_MAP)
    assert {row["workflow"].strip("`* ") for row in rows} >= set(ACTIVE_WORKFLOWS)


def test_task_cores_all_isolation_covered() -> None:
    expected = load_expected_tasks()
    cores = discover_task_cores(DEFAULT_CCCP_ROOT)
    assert cores, "no run_* task cores discovered"
    uncovered = sorted(name for name in cores if name[len("run_") :] not in expected)
    assert not uncovered, f"task cores without isolation coverage: {uncovered}"


def test_discovery_extension_point_includes_analysis() -> None:
    modules = discover_top_level_modules(DEFAULT_CCCP_ROOT)
    assert "cccp" in modules
    assert "cccp.calculation.tasks" in modules
    if (DEFAULT_CCCP_ROOT / "analysis").is_dir():
        assert "cccp.analysis" in modules


def test_injection_selftest_detects_static_and_dynamic_and_cleans() -> None:
    result = injection_selftest(DEFAULT_CCCP_ROOT)
    assert result.detected_static, result.findings
    assert result.detected_dynamic, result.findings
    assert result.cleanup_ok, "injection temp copy was not cleaned"
