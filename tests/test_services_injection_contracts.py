"""Guard tests for the transitional Services dependency-injection seam (todo 72).

Covers four acceptance clauses:

1. Static ban — no ``acp`` import of any kind on the ``src/acp_services`` side
   (plain, ``from``, ``TYPE_CHECKING``, lazy in-function, string reflection,
   ``importlib``, ``__import__``, ``sys.modules``, ``getattr`` on a module).
2. The legacy assembly injection path is runnable and production entry uses the
   injected instances (call assertions).
3. Every injection-registry row carries a concrete migration task, removal task
   and acceptance path.
4. Negative: an injected ``import acp.scheduler.store`` on a **temp copy** of a
   Services file is detected by the scanner (real files never mutated).
"""

from __future__ import annotations

import ast
import dataclasses
import io
import re
import tokenize
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _REPO_ROOT / "src" / "acp_services"
_REGISTRY_PATH = _REPO_ROOT / "tests" / "baseline" / "services-evidence" / "injection_registry.md"

_EXPECTED_PORTS = {
    "TaskIndexPort",
    "TaskViewQueryPort",
    "JobControlPort",
    "PlanExecutionPort",
    "StepRequirementPort",
    "ResultPublicationPort",
    "MethodConfigPort",
    "RemotePathsPort",
    "RemoteReleasePort",
    "RemoteSubmissionPort",
    "ProjectReadPort",
}


# ---------------------------------------------------------------------------
# Scanner: AST + raw-text (defense in depth against obfuscation)
# ---------------------------------------------------------------------------


def _is_acp_module(name: str) -> bool:
    return name == "acp" or name.startswith("acp.")


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _fold_string(node: ast.AST) -> str | None:
    """Statically fold string constants: Constant, ``+`` concat, f-string parts."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _fold_string(node.left)
        right = _fold_string(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None


_RAW_CODE_PATTERNS = (
    re.compile(r"(?<![\w.])import\s+acp(?![_\w])"),
    re.compile(r"(?<![\w.])from\s+acp(?![_\w])"),
    re.compile(r"import_module\s*\(\s*['\"]acp"),
    re.compile(r"__import__\s*\(\s*['\"]acp"),
    re.compile(r"\bmodules\s*\[\s*['\"]acp"),
)


def _code_only_text(source: str) -> str:
    """Return code tokens with STRING/COMMENT tokens removed.

    Docstrings and comments mention ``acp`` benignly; stripping them lets the
    raw pass look for real import syntax without false positives.
    """
    tokens: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (
                tokenize.STRING,
                tokenize.COMMENT,
                tokenize.NL,
                tokenize.NEWLINE,
            ):
                continue
            tokens.append(tok.string)
    except tokenize.TokenError:  # pragma: no cover - malformed snippets
        return source
    return " ".join(tokens)


def scan_for_acp_imports(source: str) -> list[str]:
    """Return a list of forbidden-``acp``-access descriptions (empty == clean)."""
    violations: list[str] = []
    tree = ast.parse(source)
    doc_ids = _docstring_node_ids(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_acp_module(alias.name):
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_acp_module(module):
                violations.append(f"from {'.' * node.level}{module} import ...")
        elif isinstance(node, ast.Call):
            func = node.func
            attr = func.attr if isinstance(func, ast.Attribute) else None
            name = func.id if isinstance(func, ast.Name) else None
            if attr == "import_module" and node.args:
                folded = _fold_string(node.args[0])
                if folded is not None and _is_acp_module(folded):
                    violations.append(f"import_module({folded!r})")
            elif name == "__import__" and node.args:
                folded = _fold_string(node.args[0])
                if folded is not None and _is_acp_module(folded):
                    violations.append(f"__import__({folded!r})")
            elif name == "getattr" and len(node.args) >= 2:
                folded = _fold_string(node.args[1])
                if folded is not None and _is_acp_module(folded):
                    violations.append(f"getattr(..., {folded!r})")
        elif isinstance(node, ast.Subscript):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == "modules":
                folded = _fold_string(node.slice)
                if folded is not None and _is_acp_module(folded):
                    violations.append(f"sys.modules[{folded!r}]")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids:
                continue
            if _is_acp_module(node.value.strip()):
                violations.append(f"string {node.value!r}")
        elif isinstance(node, (ast.BinOp, ast.JoinedStr)):
            folded = _fold_string(node)
            if folded is not None and _is_acp_module(folded.strip()):
                violations.append(f"folded string {folded!r}")

    code = _code_only_text(source)
    for pattern in _RAW_CODE_PATTERNS:
        match = pattern.search(code)
        if match:
            violations.append(f"raw-code match {match.group(0)!r}")
    return violations


def _services_python_files() -> list[Path]:
    return sorted(_SERVICES_ROOT.rglob("*.py"))


# ---------------------------------------------------------------------------
# (1) static ban + skeleton hygiene
# ---------------------------------------------------------------------------


def test_package_skeleton_present() -> None:
    for rel in (
        "__init__.py",
        "contracts/__init__.py",
        "contracts/injection.py",
        "bootstrap.py",
    ):
        assert (_SERVICES_ROOT / rel).is_file(), f"missing {rel}"


def test_services_side_has_no_acp_import() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _services_python_files():
        violations = scan_for_acp_imports(path.read_text(encoding="utf-8"))
        if violations:
            offenders[str(path.relative_to(_REPO_ROOT))] = violations
    assert not offenders, f"acp imports detected on the Services side: {offenders}"


def test_init_files_contain_no_implementation() -> None:
    """ANTI #5: package ``__init__`` files only document / re-export."""
    for rel in ("__init__.py", "contracts/__init__.py"):
        tree = ast.parse((_SERVICES_ROOT / rel).read_text(encoding="utf-8"))
        for node in tree.body:
            assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)), (
                f"{rel} defines implementation: {type(node).__name__}"
            )


def test_injection_module_uses_future_annotations() -> None:
    text = (_SERVICES_ROOT / "contracts" / "injection.py").read_text(encoding="utf-8")
    assert "from __future__ import annotations" in text


def test_injection_protocols_defined_and_exported() -> None:
    from acp_services.contracts import __all__ as contracts_all
    from acp_services.contracts import injection

    assert _EXPECTED_PORTS <= set(contracts_all)
    for name in _EXPECTED_PORTS:
        assert isinstance(getattr(injection, name), type), name


# ---------------------------------------------------------------------------
# (4) negative self-tests: the scanner must catch obfuscation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        "import acp\n",
        "import acp.scheduler.store\n",
        "from acp.scheduler import store\n",
        "from acp import scheduler\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import acp\n",
        "def f():\n    import acp.scheduler.store\n",
        "import importlib\nimportlib.import_module('acp.scheduler.store')\n",
        "x = __import__('acp')\n",
        "home = 'ac' + 'p.scheduler.store'\n",
        "import sys\nmod = sys.modules['acp.scheduler.store']\n",
        "import os\nm = getattr(os, 'acp.whatever')\n",
    ],
)
def test_scanner_catches_obfuscated_acp_access(snippet: str) -> None:
    assert scan_for_acp_imports(snippet), f"scanner missed: {snippet!r}"


def test_scanner_allows_docstring_and_plugin_name_mentions() -> None:
    source = (
        '"""This module must never import acp."""\n'
        "\n"
        'PLUGIN = "acp_services.contracts"\n'
        "def f():\n"
        '    """acp.scheduler is legacy."""\n'
        "    return PLUGIN\n"
    )
    assert scan_for_acp_imports(source) == []


def test_negative_injected_acp_import_on_temp_copy_is_detected(tmp_path: Path) -> None:
    real = _SERVICES_ROOT / "contracts" / "injection.py"
    mutated = real.read_text(encoding="utf-8").replace(
        "from __future__ import annotations",
        "from __future__ import annotations\n\nimport acp.scheduler.store  # injected",
        1,
    )
    assert mutated != real.read_text(encoding="utf-8"), "mutation did not apply"
    temp_copy = tmp_path / "injection_injected.py"
    temp_copy.write_text(mutated, encoding="utf-8")

    violations = scan_for_acp_imports(temp_copy.read_text(encoding="utf-8"))
    assert violations, "injected acp import not detected on temp copy"
    assert any("acp.scheduler.store" in item for item in violations)
    # Real file untouched.
    assert scan_for_acp_imports(real.read_text(encoding="utf-8")) == [], (
        "real Services file must remain clean"
    )


# ---------------------------------------------------------------------------
# (3) injection registry gate
# ---------------------------------------------------------------------------


def _registry_rows() -> list[tuple[str, ...]]:
    lines = _REGISTRY_PATH.read_text(encoding="utf-8").splitlines()
    rows: list[tuple[str, ...]] = []
    in_table = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if not in_table:
            if not cells or cells[0] != "旧实现":
                continue
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if following.startswith("|") and all(
                cell.strip() and set(cell.strip()) <= set("-: ")
                for cell in following.strip("|").split("|")
            ):
                in_table = True
            continue
        if all(cell and set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def test_registry_rows_carry_concrete_migration_and_removal() -> None:
    rows = _registry_rows()
    assert len(rows) >= 13, f"expected >= 13 injection rows, got {len(rows)}"
    for cells in rows:
        assert len(cells) == 5, f"registry row must have 5 columns: {cells}"
        legacy, target, migration, removal, acceptance = cells
        for label, value in (
            ("legacy", legacy),
            ("target", target),
            ("migration", migration),
            ("removal", removal),
            ("acceptance", acceptance),
        ):
            assert value, f"empty {label} in row: {cells}"
        assert re.search(r"todo\s+\d+", migration), f"no concrete todo in migration: {cells}"
        assert re.search(r"todo\s+\d+", removal), f"no concrete todo in removal: {cells}"
        for column in (migration, removal):
            assert "later" not in column.lower(), f"placeholder row: {cells}"
        assert "tests/" in acceptance, f"acceptance must name a test path: {cells}"


def test_registry_covers_nine_injection_areas() -> None:
    text = _REGISTRY_PATH.read_text(encoding="utf-8")
    for token in (
        "JobStorePort",
        "MigrationsPort",
        "TaskIndexPort",
        "TaskViewQueryPort",
        "JobControlPort",
        "PlanExecutionPort",
        "StepRequirementPort",
        "ResultPublicationPort",
        "MethodConfigPort",
        "RemotePathsPort",
        "RemoteReleasePort",
        "RemoteSubmissionPort",
    ):
        assert token in text, f"registry missing injection area {token}"
    # Phase discipline: port convergence only, independence is not claimed here.
    assert "端口收敛" in text
    assert "不能声明 Services 已独立" in text


# ---------------------------------------------------------------------------
# (2) frozen context + runnable injection path
# ---------------------------------------------------------------------------


def test_services_ports_is_frozen_and_complete() -> None:
    from acp_services.bootstrap import ServicesPorts

    assert dataclasses.is_dataclass(ServicesPorts)
    field_names = {field.name for field in dataclasses.fields(ServicesPorts)}
    assert {
        "tasks",
        "task_views",
        "control",
        "planning",
        "step_requirements",
        "publication",
        "catalog",
        "remote_paths",
        "remote_release",
        "remote_submission",
        "projects",
    } <= field_names
    # The schema/store closure is Services-native since B05-7 (todo 77).
    assert "job_store" not in field_names
    assert "migrations" not in field_names

    ports = ServicesPorts(**{name: object() for name in field_names})
    with pytest.raises(dataclasses.FrozenInstanceError):
        ports.tasks = object()  # type: ignore[misc]


def test_first_slice_uses_injected_ports(tmp_path: Path) -> None:
    from acp.services_bootstrap import build_services_ports
    from acp_services import bootstrap as services_bootstrap

    ports = build_services_ports(tmp_path)
    observed = {"ensure": 0, "list": 0}

    real_projects = ports.projects

    class _ProjectSpy:
        def get_project(self, project_id: str):  # type: ignore[no-untyped-def]
            return real_projects.get_project(project_id)

        def list_projects(self):  # type: ignore[no-untyped-def]
            observed["list"] += 1
            return real_projects.list_projects()

        def ensure_default_project(self) -> str:
            observed["ensure"] += 1
            return real_projects.ensure_default_project()

    injected = dataclasses.replace(ports, projects=_ProjectSpy())
    listing = services_bootstrap.open_project_read_path(injected)

    # Production entry observed the injected implementation.
    assert observed == {"ensure": 1, "list": 1}
    # Real JobStore-backed result; the native store closure created the schema.
    assert listing
    assert any(row["project_id"] == "uncategorized" for row in listing)
    assert (tmp_path / "acp_jobs.db").is_file()


def test_build_services_ports_offers_real_implementations(tmp_path: Path) -> None:
    from acp.scheduler.projects import ProjectManager
    from acp.services_bootstrap import build_services_ports

    ports = build_services_ports(tmp_path)
    assert isinstance(ports.projects, ProjectManager)
    # The schema/store closure is Services-native (B05-7): no injected port,
    # but the project manager still resolves the shared database path.
    assert ports.projects.db_path == tmp_path / "acp_jobs.db"
    # Control port is an unattached placeholder (no scheduler started) and
    # fails loudly rather than silently doing nothing.
    with pytest.raises(RuntimeError, match="JobManager"):
        ports.control.pause_job("does-not-matter")


def test_build_services_ports_does_not_start_poller(tmp_path: Path) -> None:
    """Importing/wiring must not claim the run root or spawn poller threads."""
    import threading

    from acp.services_bootstrap import build_services_ports

    before = {t.name for t in threading.enumerate()}
    build_services_ports(tmp_path)
    after = {t.name for t in threading.enumerate()}
    assert "acp-poller" not in after - before
    assert "acp-reconciler" not in after - before
