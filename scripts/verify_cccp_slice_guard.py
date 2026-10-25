#!/usr/bin/env python3
"""Per-slice dependency / uniqueness / isolation guard harness (plan-3 T48).

Runs repo-wide checks that later migration slices (T8 - T50) execute and extend:

1. ``static-reverse-dependency``  — AST scan of ``src/cccp/**`` for real
   reverse dependencies: ``import acp`` / ``from acp`` (static), importlib /
   dynamic string targets, registry string targets and CLI detours (running the
   ``acp`` CLI through a subprocess).  ``acp.*`` text inside docstrings/comments
   and provenance output is deliberately NOT a reverse dependency.
2. ``public-api-import``  — a fresh child interpreter imports the public
   surface of every cccp package in a genuinely ``acp``-free environment
   (``find_spec("acp") is None`` proven before any cccp import).
3. ``callable-task-core``  — delegates to ``scripts/verify_cccp_isolation.py``
   and cross-checks that every discovered task core (``run_*`` in
   ``src/cccp/calculation/tasks/``) is covered by that callable probe.
4. ``workflow-task-map``  — parses the tracked
   ``tests/baseline/cccp-evidence/workflow_task_map.md`` registry (15 active
   workflows → cccp task core + helper modules + blocker class + probe status)
   and verifies each referenced task core resolves and its declared probe
   status matches the real isolation coverage.
5. ``science-selftest``  — one deterministic success scientific path (ORCA
   optimize input generation) plus one typed error path, both in the acp-free
   child.  Real QC binaries are never launched.

Extension point: ``cccp.analysis`` does not exist yet.  Discovery is dynamic —
when ``src/cccp/analysis/**`` appears its package/modules are automatically
included in the static scan and the acp-free import probe, and any task cores
it adds are automatically required to be isolation-covered.  No code change is
needed in this harness for that to take effect.

The negative-injection proof is ``--injection-selftest``: it copies the cccp
tree into a temp dir, injects a static and a dynamic ``acp`` reference, and
asserts the scan detects both, then removes the temp copy.  ``--compare-identity
OLD NEW`` is the old/new object hook for migration slices.

Usage::

    python3.11 scripts/verify_cccp_slice_guard.py
    python3.11 scripts/verify_cccp_slice_guard.py --injection-selftest
    python3.11 scripts/verify_cccp_slice_guard.py --cccp-root /tmp/wt/src/cccp
    python3.11 scripts/verify_cccp_slice_guard.py --compare-identity acp.X:f cccp.Y:g
"""

from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DEFAULT_CCCP_ROOT: Final[Path] = REPO_ROOT / "src" / "cccp"
DEFAULT_MAP: Final[Path] = (
    REPO_ROOT / "tests" / "baseline" / "cccp-evidence" / "workflow_task_map.md"
)
ISOLATION_SCRIPT: Final[Path] = REPO_ROOT / "scripts" / "verify_cccp_isolation.py"

CHECK_MARKER: Final[str] = "::guard-check::"
MODULE_MARKER: Final[str] = "::guard-module::"

#: The 15 active workflows (plan registry, T2 ledger order).
ACTIVE_WORKFLOWS: Final[tuple[str, ...]] = (
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
    "XtbConstrainedOpt",
    "irc",
    "scan",
    "tsmode",
    "casscf",
    "nmr",
    "singlepoint",
    "optimize",
    "frequency",
    "xtb_optimize",
)

#: Callee names that pull an import target from a string argument.
_IMPORT_CALL_NAMES: Final[frozenset[str]] = frozenset(
    {"import_module", "find_spec", "__import__", "spec_from_file_location", "load_module"}
)
#: Callee names that launch a process (CLI detour surface).
_CLI_CALL_NAMES: Final[frozenset[str]] = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "system", "popen", "which"}
)
_ACP_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|[^A-Za-z0-9_])acp(?:[^A-Za-z0-9_]|$)")
#: Embedded ``from acp`` / ``import acp`` text in a string constant (e.g. for
#: ``exec``).  Free-form provenance/output text containing the ``acp`` word
#: (``"generated_by=acp.calculations.pes.atom_mapping"``) is deliberately exempt
#: per the module docstring and plan-3 §105; only real import text counts.
_EMBEDDED_IMPORT_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:from|import)\s+acp\b")
_TASK_REF_RE: Final[re.Pattern[str]] = re.compile(r"src/cccp/[A-Za-z0-9_./-]+\.py(?::\d+)?")


@dataclass
class Finding:
    path: str
    line: int
    category: str
    detail: str


@dataclass
class CheckResult:
    name: str
    passed: bool
    checked: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    detail: str = ""


def _iter_python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_acp_module_token(value: str) -> bool:
    return value == "acp" or value.startswith("acp.")


def _is_cli_acp_token(value: str) -> bool:
    if value in {"acp", "acp.cli", "acp.cli.py", "acp.py"}:
        return True
    return bool(_ACP_TOKEN_RE.search(value))


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def scan_reverse_dependencies(cccp_root: Path) -> list[Finding]:
    """Return real reverse-dependency findings under *cccp_root*.

    Only static imports, importlib/dynamic string targets, registry string
    targets and CLI detours count.  Docstrings and non-code text are ignored.
    """
    findings: list[Finding] = []
    for path in _iter_python_files(cccp_root):
        rel = _relative(path, cccp_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            findings.append(Finding(rel, exc.lineno or 0, "parse_error", str(exc)))
            continue
        doc_ids = _docstring_constant_ids(tree)
        cli_const_ids: set[int] = set()
        import_const_ids: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _callee_name(node.func)
            if name in _CLI_CALL_NAMES:
                target = cli_const_ids
            elif name in _IMPORT_CALL_NAMES:
                target = import_const_ids
            else:
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    target.add(id(sub))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_acp_module_token(alias.name):
                        findings.append(
                            Finding(rel, node.lineno, "static_import", f"import {alias.name}")
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module and _is_acp_module_token(node.module):
                    names = ", ".join(alias.name for alias in node.names)
                    findings.append(
                        Finding(
                            rel, node.lineno, "static_import", f"from {node.module} import {names}"
                        )
                    )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                if id(node) in doc_ids:
                    continue
                if id(node) in cli_const_ids and _is_cli_acp_token(value):
                    findings.append(Finding(rel, node.lineno, "cli_detour", repr(value)))
                elif id(node) in import_const_ids and _is_acp_module_token(value):
                    findings.append(Finding(rel, node.lineno, "dynamic_import_target", repr(value)))
                elif _is_acp_module_token(value):
                    findings.append(Finding(rel, node.lineno, "string_reference", repr(value)))
                elif _EMBEDDED_IMPORT_RE.search(value):
                    findings.append(Finding(rel, node.lineno, "string_reference", repr(value)))
    return findings


def discover_top_level_modules(cccp_root: Path) -> list[str]:
    """Public cccp surface to import acp-free: package inits + top modules."""
    modules = ["cccp"]
    for child in sorted(cccp_root.iterdir()):
        if child.is_dir() and (child / "__init__.py").exists():
            modules.append(f"cccp.{child.name}")
        elif child.suffix == ".py" and child.stem != "__init__":
            modules.append(f"cccp.{child.stem}")
    tasks_init = cccp_root / "calculation" / "tasks" / "__init__.py"
    if tasks_init.exists() and "cccp.calculation.tasks" not in modules:
        modules.append("cccp.calculation.tasks")
    return modules


def discover_task_cores(cccp_root: Path) -> dict[str, tuple[str, int]]:
    """Map ``run_*`` top-level task-core name -> (relative path, line)."""
    cores: dict[str, tuple[str, int]] = {}
    tasks_dir = cccp_root / "calculation" / "tasks"
    if not tasks_dir.is_dir():
        return cores
    for path in sorted(tasks_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "run_"
            ):
                cores[node.name] = (_relative(path, cccp_root), node.lineno)
    return cores


def load_expected_tasks() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("_slice_guard_isolation", ISOLATION_SCRIPT)
    if spec is None or spec.loader is None:
        return ()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(getattr(module, "EXPECTED_TASKS", ()))


_IMPORT_CHILD = r"""
import importlib
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path


class BlockACP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "acp" or fullname.startswith("acp."):
            raise ModuleNotFoundError("audit blocked " + fullname, name=fullname)


allowed = sys.argv[1]
modules = json.loads(sys.argv[2])
kept = []
for entry in sys.path:
    if entry == allowed or entry == "":
        kept.append(entry)
        continue
    base = Path(entry or ".")
    try:
        exposes = (
            (base / "acp").exists()
            or (base / "acp.py").exists()
            or (base / "cccp").exists()
            or (base / "cccp.py").exists()
        )
    except OSError:
        exposes = False
    if exposes:
        continue
    kept.append(entry)
sys.path[:] = kept
if allowed not in sys.path:
    sys.path.insert(0, allowed)
if importlib.util.find_spec("acp") is not None:
    print("::guard-module::" + json.dumps({"fatal": "acp still importable"}))
    sys.exit(2)
sys.meta_path.insert(0, BlockACP())
results = []
for name in modules:
    try:
        importlib.import_module(name)
        results.append({"module": name, "ok": True, "error": None})
    except BaseException as exc:
        results.append(
            {"module": name, "ok": False, "error": type(exc).__name__ + ": " + str(exc)[:200]}
        )
leaked = sorted(n for n in sys.modules if n == "acp" or n.startswith("acp."))
print("::guard-module::" + json.dumps({"results": results, "leaked": leaked}))
sys.exit(0 if all(r["ok"] for r in results) and not leaked else 1)
"""

_SCIENCE_CHILD = r"""
import importlib
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch


class BlockACP(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "acp" or fullname.startswith("acp."):
            raise ModuleNotFoundError("audit blocked " + fullname, name=fullname)


allowed = sys.argv[1]
kept = []
for entry in sys.path:
    if entry == allowed or entry == "":
        kept.append(entry)
        continue
    base = Path(entry or ".")
    try:
        exposes = (base / "acp").exists() or (base / "acp.py").exists() or (base / "cccp").exists()
    except OSError:
        exposes = False
    if exposes:
        continue
    kept.append(entry)
sys.path[:] = kept
if allowed not in sys.path:
    sys.path.insert(0, allowed)
if importlib.util.find_spec("acp") is not None:
    print("::guard-science::" + json.dumps({"fatal": "acp still importable"}))
    sys.exit(2)
sys.meta_path.insert(0, BlockACP())

from cccp.calculation.requests import (
    OptimizeOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
    validate_request,
)
from cccp.calculation.errors import TaskInputError
from cccp.qc.interfaces.orca import ORCAInterface

with patch("cccp.qc.interfaces.orca.resolve_executable", return_value=None):
    interface = ORCAInterface({}, method="B3LYP", basis="def2-SVP")
blocks, _meta = interface._build_input_blocks(
    calc_type="opt", symbols=["H", "H"], recalc_hess=10
)
success = "%geom" in blocks and "Recalc_Hess 10" in blocks

error_ok = False
try:
    validate_request(
        TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
            options=OptimizeOptions(),
        )
    )
except TaskInputError:
    error_ok = True

print(
    "::guard-science::"
    + json.dumps(
        {
            "success_geom_block": "%geom" in blocks,
            "success_recalc_hess": "Recalc_Hess 10" in blocks,
            "error_typed": error_ok,
            "leaked": sorted(n for n in sys.modules if n == "acp" or n.startswith("acp.")),
        }
    )
)
sys.exit(0 if success and error_ok else 1)
"""


def _copy_cccp_tree(cccp_root: Path, dest: Path) -> Path:
    shutil.copytree(
        cccp_root,
        dest / "cccp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return dest


def _run_child(
    script: str, tree_root: Path, argv: list[str], timeout: int
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree_root)
    env["ACP_DISABLE_MPI_SNIFF"] = "1"
    return subprocess.run(
        [sys.executable, "-c", script, str(tree_root), *argv],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tree_root),
        timeout=timeout,
    )


def check_public_api_import(cccp_root: Path, timeout: int) -> CheckResult:
    modules = discover_top_level_modules(cccp_root)
    with tempfile.TemporaryDirectory(prefix="slice_guard_import_") as tmp:
        tree = _copy_cccp_tree(cccp_root, Path(tmp))
        proc = _run_child(_IMPORT_CHILD, tree, [json.dumps(modules)], timeout)
    payload: dict[str, object] = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith(MODULE_MARKER):
            payload = json.loads(line[len(MODULE_MARKER) :])
    results = payload.get("results") or []
    leaked = payload.get("leaked") or []
    failures = [f"{r['module']}: {r['error']}" for r in results if not r.get("ok")]
    if isinstance(payload, dict) and payload.get("fatal"):
        failures.insert(0, str(payload["fatal"]))
    if leaked:
        failures.append(f"leaked acp modules: {leaked}")
    if proc.returncode != 0 and not failures:
        failures.append(f"child exited {proc.returncode} with no structured result")
    return CheckResult(
        name="public-api-import",
        passed=not failures,
        checked=list(modules),
        findings=failures,
        detail=f"imported {len(results)}/{len(modules)} modules acp-free",
    )


def check_callable_task_cores(timeout: int) -> CheckResult:
    expected = load_expected_tasks()
    cores = discover_task_cores(DEFAULT_CCCP_ROOT)
    findings: list[str] = []
    for run_name, (rel, line) in sorted(cores.items()):
        task_id = run_name[len("run_") :]
        if task_id not in expected:
            findings.append(f"{rel}:{line} {run_name} not covered by isolation probe")
    for task_id in expected:
        if f"run_{task_id}" not in cores:
            findings.append(
                f"isolation probe declares {task_id} but no run_{task_id} task core exists"
            )
    proc = subprocess.run(
        [sys.executable, str(ISOLATION_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=timeout,
    )
    tail = (proc.stdout or "").strip().splitlines()
    verdict = tail[-1] if tail else ""
    if proc.returncode != 0 or "RESULT: PASS" not in verdict:
        findings.append(f"verify_cccp_isolation.py failed: {verdict or proc.returncode}")
    return CheckResult(
        name="callable-task-core",
        passed=not findings,
        checked=[f"{run}({rel}:{line})" for run, (rel, line) in sorted(cores.items())],
        findings=findings,
        detail=f"isolation surface: {len(expected)} task cores; verdict: {verdict}",
    )


def parse_workflow_map(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if header is None:
            if cells and cells[0] == "workflow":
                header = cells
            continue
        if all(set(cell) <= {"-", ":"} and cell for cell in cells):
            continue
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells)))
    return rows


def check_workflow_map(map_path: Path, cccp_root: Path) -> CheckResult:
    findings: list[str] = []
    if not map_path.exists():
        return CheckResult(
            name="workflow-task-map",
            passed=False,
            findings=[f"mapping registry missing: {map_path}"],
            detail="registry not found",
        )
    rows = parse_workflow_map(map_path)
    expected = load_expected_tasks()
    seen: list[str] = []
    checked: list[str] = []
    for row in rows:
        workflow = row.get("workflow", "").strip("`* ")
        if not workflow:
            continue
        seen.append(workflow)
        core_cell = row.get("cccp task core (file:line)", "")
        refs = _TASK_REF_RE.findall(core_cell)
        if not refs:
            findings.append(f"{workflow}: no resolvable cccp task core reference")
            continue
        covered = True
        for ref in refs:
            rel = ref.split(":", 1)[0]
            file_path = REPO_ROOT / rel
            if not file_path.exists():
                findings.append(f"{workflow}: task core file missing {rel}")
                covered = False
                continue
            stem = Path(rel).stem
            if stem not in expected:
                covered = False
        probe_cell = row.get("T48 probe status", "").strip()
        declared = "GAP" if probe_cell.upper().startswith("GAP") else "PASS"
        if declared == "PASS" and not covered:
            findings.append(f"{workflow}: declared PASS but a task core is not isolation-covered")
        if declared == "GAP" and covered:
            findings.append(f"{workflow}: declared GAP but every task core is isolation-covered")
        checked.append(f"{workflow}({('PASS' if covered else 'GAP')})")
    for workflow in ACTIVE_WORKFLOWS:
        if workflow not in seen:
            findings.append(f"active workflow missing from registry: {workflow}")
    duplicates = sorted({name for name in seen if seen.count(name) > 1})
    if duplicates:
        findings.append(f"duplicate workflow rows: {duplicates}")
    return CheckResult(
        name="workflow-task-map",
        passed=not findings,
        checked=checked,
        findings=findings,
        detail=f"{len(rows)} rows parsed; {len(ACTIVE_WORKFLOWS)} active workflows expected",
    )


def check_static_scan(cccp_root: Path) -> CheckResult:
    findings = scan_reverse_dependencies(cccp_root)
    rendered = [f"{f.category} {f.path}:{f.line} {f.detail}" for f in findings]
    files = [str(p.relative_to(cccp_root)) for p in _iter_python_files(cccp_root)]
    scope = _relative(cccp_root, REPO_ROOT) or str(cccp_root)
    return CheckResult(
        name="static-reverse-dependency",
        passed=not findings,
        checked=[f"{len(files)} python files scanned under {scope}"],
        findings=rendered,
        detail="no real acp reverse dependency (import/importlib/registry/CLI detour)",
    )


def check_science_selftest(cccp_root: Path, timeout: int) -> CheckResult:
    with tempfile.TemporaryDirectory(prefix="slice_guard_science_") as tmp:
        tree = _copy_cccp_tree(cccp_root, Path(tmp))
        proc = _run_child(_SCIENCE_CHILD, tree, [], timeout)
    payload: dict[str, object] = {}
    for line in (proc.stdout or "").splitlines():
        if line.startswith("::guard-science::"):
            payload = json.loads(line[len("::guard-science::") :])
    findings: list[str] = []
    if not payload:
        findings.append(f"science selftest produced no result (rc={proc.returncode})")
    else:
        if not payload.get("success_geom_block") or not payload.get("success_recalc_hess"):
            findings.append("success path: ORCA optimize input block missing geom/Recalc_Hess")
        if not payload.get("error_typed"):
            findings.append("error path: invalid request did not raise typed TaskInputError")
        if payload.get("leaked"):
            findings.append(f"acp leaked: {payload['leaked']}")
    return CheckResult(
        name="science-selftest",
        passed=not findings,
        checked=["success: ORCA optimize input generation", "error: typed TaskInputError"],
        findings=findings,
        detail="deterministic input-generation success + typed error path (no QC binary)",
    )


@dataclass
class InjectionResult:
    detected_static: bool
    detected_dynamic: bool
    findings: list[str]
    cleanup_ok: bool


def injection_selftest(cccp_root: Path) -> InjectionResult:
    tmp_path = Path(tempfile.mkdtemp(prefix="slice_guard_inject_"))
    try:
        tree = _copy_cccp_tree(cccp_root, tmp_path)
        constants = tree / "cccp" / "utils" / "constants.py"
        scan = tree / "cccp" / "calculation" / "tasks" / "scan.py"
        constants.write_text(
            constants.read_text(encoding="utf-8") + "\nimport acp  # injection selftest\n",
            encoding="utf-8",
        )
        scan.write_text(
            scan.read_text(encoding="utf-8")
            + "\nimport importlib\n"
            + '_injected = importlib.import_module("acp.calculations.identity")\n',
            encoding="utf-8",
        )
        findings = scan_reverse_dependencies(tree / "cccp")
        detected_static = any(f.category == "static_import" for f in findings)
        detected_dynamic = any(f.category == "dynamic_import_target" for f in findings)
        rendered = [f"{f.category} {f.path}:{f.line} {f.detail}" for f in findings]
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
    return InjectionResult(detected_static, detected_dynamic, rendered, not tmp_path.exists())


def compare_identity(old_spec: str, new_spec: str) -> int:
    def _load(spec: str):
        module_name, _, attr = spec.partition(":")
        if not attr:
            print(f"invalid spec (want module:attr): {spec}", file=sys.stderr)
            raise SystemExit(2)
        if str(REPO_ROOT / "src") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "src"))
        return getattr(importlib.import_module(module_name), attr)

    old = _load(old_spec)
    new = _load(new_spec)
    same = old is new
    print(f"compare-identity: {old_spec} is {new_spec} -> {'PASS' if same else 'FAIL'}")
    if not same:
        print(f"  old={type(old).__module__}.{type(old).__qualname__}")
        print(f"  new={type(new).__module__}.{type(new).__qualname__}")
    return 0 if same else 1


def _print_result(result: CheckResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"-- check: {result.name}: {status} --")
    print(f"   detail : {result.detail}")
    for surface in result.checked:
        print(f"   surface: {surface}")
    for finding in result.findings:
        print(f"   finding: {finding}")
    print(
        CHECK_MARKER
        + json.dumps(
            {
                "name": result.name,
                "passed": result.passed,
                "checked": result.checked,
                "findings": result.findings,
            },
            sort_keys=True,
        )
    )


def _run_checks(cccp_root: Path, map_path: Path, timeout: int, skip_callable: bool) -> int:
    root_scoped = cccp_root.resolve() != DEFAULT_CCCP_ROOT.resolve()
    results: list[CheckResult] = [
        check_static_scan(cccp_root),
        check_public_api_import(cccp_root, timeout),
    ]
    if not root_scoped:
        if not skip_callable:
            results.append(check_callable_task_cores(timeout))
        results.append(check_workflow_map(map_path, cccp_root))
        results.append(check_science_selftest(cccp_root, timeout))
    else:
        print(f"[info] root-scoped run on {cccp_root}; skipping callable/map/science live checks")
    analysis = cccp_root / "analysis"
    if analysis.is_dir():
        modules = discover_top_level_modules(cccp_root)
        print(
            "[info] cccp.analysis present — dynamic discovery armed "
            f"({len(modules)} modules probed)"
        )
    else:
        print("[info] cccp.analysis not present yet — dynamic discovery extension point armed")

    for result in results:
        _print_result(result)
    passed = sum(1 for result in results if result.passed)
    print("== summary ==")
    print(f"checks: {len(results)}  passed: {passed}  failed: {len(results) - passed}")
    if passed == len(results):
        print("RESULT: PASS — slice guard checks all green.")
        return 0
    print("RESULT: FAIL — see findings above.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-slice cccp dependency/uniqueness guard harness."
    )
    parser.add_argument("--cccp-root", type=Path, default=DEFAULT_CCCP_ROOT)
    parser.add_argument("--workflow-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--skip-callable", action="store_true")
    parser.add_argument("--injection-selftest", action="store_true")
    parser.add_argument("--compare-identity", nargs=2, metavar=("OLD", "NEW"))
    args = parser.parse_args(argv)

    if args.compare_identity is not None:
        return compare_identity(*args.compare_identity)

    if args.injection_selftest:
        result = injection_selftest(args.cccp_root)
        print(f"injection selftest: static_import detected={result.detected_static}")
        print(f"injection selftest: dynamic_import detected={result.detected_dynamic}")
        for finding in result.findings:
            print(f"   finding: {finding}")
        print(f"injection selftest: temp copy cleaned={result.cleanup_ok}")
        ok = result.detected_static and result.detected_dynamic and result.cleanup_ok
        print(f"RESULT: {'PASS' if ok else 'FAIL'} — injection negative detection selftest.")
        return 0 if ok else 1

    return _run_checks(args.cccp_root, args.workflow_map, args.timeout, args.skip_callable)


if __name__ == "__main__":
    raise SystemExit(main())
