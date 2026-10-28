"""Isolation gate for the ACP Services package (B01-1 / todo 6).

Four acceptance clauses:

1. Static ban — ``src/acp_services/**`` must not import, lazily import,
   ``TYPE_CHECKING``-import, string-reflect, ``importlib``/``__import__`` load,
   ``sys.modules``-look-up or ``getattr``-resolve the ``acp`` package.  Same
   rule the composition seam relies on (``public_allowlist.md``).
2. Single implementation — ``acp.scheduler.projects.ProjectManager`` **is**
   ``acp_services.workspace.projects.ProjectManager`` (one object, not a copy).
3. Services migration-ledger hygiene — every row of
   ``tests/baseline/services-evidence/migration_ledger.md`` carries the six
   frozen columns and a ``src/...py`` station (mirrors
   ``tests/test_architecture_invariants.py::test_migration_ledger_rows_have_six_columns``).
4. Packaging guard — ``pyproject.toml`` includes ``acp_services*`` so the
   package ships on install.

Plus the Access contract semantics owned by this todo (frozen dataclasses,
local principal trust boundary, minimal allow/explicit deny/revocable policy).
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _REPO_ROOT / "src" / "acp_services"
_LEDGER_PATH = _REPO_ROOT / "tests" / "baseline" / "services-evidence" / "migration_ledger.md"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------
# (a) acp-import scanner (AST + raw text; same defence-in-depth as todo 72)
# ---------------------------------------------------------------------------

#: The single reviewed station for ``acp.*`` wire literals (M1-01c).  The
#: allowlist is read from the live module so governance is data, not prose: a new
#: literal is permitted only after it is declared there and reviewed.
try:  # pragma: no cover - import failure is exercised by test_wire_* tests
    from acp_services.entrypoints import wire_identifiers as _wire_identifiers
except ImportError:  # pragma: no cover - the module is part of the deliverable
    _wire_identifiers = None

WIRE_IDENTIFIER_STATION = (
    _wire_identifiers.WIRE_IDENTIFIERS_MODULE
    if _wire_identifiers is not None
    else "acp_services.entrypoints.wire_identifiers"
)
WIRE_IDENTIFIER_VALUES = (
    frozenset(_wire_identifiers.REVIEWED_WIRE_IDENTIFIERS)
    if _wire_identifiers is not None
    else frozenset()
)


def _is_acp_module(name: str) -> bool:
    return name == "acp" or name.startswith("acp.")


def _is_allowed_wire_literal(value: str, module: str | None) -> bool:
    """Whether *value* is a reviewed wire literal in its single allowed station.

    Governance (M1-01c) is deliberately value **and** module scoped: the literal
    is accepted only inside :data:`WIRE_IDENTIFIER_STATION`.  It is never an
    import exemption, so :func:`scan_for_acp_imports` still reports every
    import/reflection/lookup form everywhere, including in that module.
    """
    return (
        module is not None
        and module == WIRE_IDENTIFIER_STATION
        and value.strip() in WIRE_IDENTIFIER_VALUES
    )


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
    """Return code tokens with STRING/COMMENT tokens stripped (no doc false positives)."""
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


def scan_for_acp_imports(source: str, *, module: str | None = None) -> list[str]:
    """Return descriptors of any forbidden ``acp`` access (empty == clean).

    Args:
        source: Python source text to inspect.
        module: Dotted module name of *source* when known.  Only
            :data:`WIRE_IDENTIFIER_STATION` may carry a reviewed wire literal;
            omitting the name means "no wire-literal allowance" (the strictest
            reading, used by the snippet self-tests).
    """
    violations: list[str] = []
    tree = ast.parse(source)
    doc_ids = _docstring_node_ids(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_acp_module(alias.name):
                    violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            imported = node.module or ""
            if _is_acp_module(imported):
                violations.append(f"from {'.' * node.level}{imported} import ...")
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
                if _is_allowed_wire_literal(node.value, module):
                    continue
                violations.append(f"string {node.value!r}")
        elif isinstance(node, (ast.BinOp, ast.JoinedStr)):
            folded = _fold_string(node)
            if folded is not None and _is_acp_module(folded.strip()):
                if _is_allowed_wire_literal(folded, module):
                    continue
                violations.append(f"folded string {folded!r}")

    code = _code_only_text(source)
    for pattern in _RAW_CODE_PATTERNS:
        match = pattern.search(code)
        if match:
            violations.append(f"raw-code match {match.group(0)!r}")
    return violations


def _services_python_files() -> list[Path]:
    return sorted(p for p in _SERVICES_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _module_name(path: Path, *, root: Path = _SERVICES_ROOT, package: str = "acp_services") -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join((package, *parts)) if parts else package


def test_services_package_has_no_acp_import() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _services_python_files():
        violations = scan_for_acp_imports(
            path.read_text(encoding="utf-8"), module=_module_name(path)
        )
        if violations:
            offenders[str(path.relative_to(_REPO_ROOT))] = violations
    assert not offenders, f"acp imports detected on the Services side: {offenders}"


def test_wire_identifier_station_holds_the_reviewed_literals() -> None:
    """M1-01c: one reviewed station, one literal per constant, no assembly."""
    station = _SERVICES_ROOT / "entrypoints" / "wire_identifiers.py"
    assert station.is_file(), "the reviewed wire-id station is missing"
    source = station.read_text(encoding="utf-8")
    assert scan_for_acp_imports(source, module=_module_name(station)) == []
    tree = ast.parse(source)
    assert not [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "chr"
    ], "the wire id must be a plain reviewed literal, never assembled"
    module = _wire_identifiers
    assert module is not None
    assert module.WIRE_IDENTIFIERS_MODULE == _module_name(station)
    assert module.REVIEWED_WIRE_IDENTIFIERS
    for value in module.REVIEWED_WIRE_IDENTIFIERS:
        # Each reviewed value is declared exactly once, as a plain literal.
        assert source.count(f'"{value}"') == 1, value
        assert module.is_reviewed_wire_identifier(value)
    assert module.is_reviewed_wire_identifier(module.NEGOTIATION_EXTENSION_ID)


def test_wire_literal_outside_the_station_is_rejected() -> None:
    """A second copy of a reviewed wire literal in another module is a failure."""
    value = next(iter(WIRE_IDENTIFIER_VALUES))
    table = {
        "acp_services.entrypoints.mcp": [
            f'ID = "{value}"',
            'ID = "ac" + "p.negotiation.v1"',
        ]
    }
    for module_name, snippets in table.items():
        for snippet in snippets:
            assert scan_for_acp_imports(snippet, module=module_name), snippet
    # The same value is accepted only in the reviewed station.
    assert scan_for_acp_imports(f'ID = "{value}"', module=WIRE_IDENTIFIER_STATION) == []


def test_wire_allowlist_never_exempts_imports_or_reflection() -> None:
    """The station allowance is literal-only: every bypass stays a hard failure."""
    value = next(iter(WIRE_IDENTIFIER_VALUES))
    snippets = (
        f'ID = "{value}"\nimport acp\n',
        f'ID = "{value}"\nimport importlib\nimportlib.import_module("acp.scheduler.store")\n',
        f'ID = "{value}"\nx = __import__("acp")\n',
        f'ID = "{value}"\nimport sys\nsys.modules["acp.scheduler.store"]\n',
        f'ID = "{value}"\nimport os\ngetattr(os, "acp.scheduler.store")\n',
    )
    for snippet in snippets:
        for module_name in (WIRE_IDENTIFIER_STATION, "acp_services.entrypoints.mcp", None):
            assert scan_for_acp_imports(snippet, module=module_name), (snippet, module_name)


def test_wire_literal_shipped_without_a_reviewed_constant_is_rejected() -> None:
    """An unreviewed ``acp.*`` literal is still a violation in the station."""
    assert scan_for_acp_imports('ID = "acp.unreviewed.v9"', module=WIRE_IDENTIFIER_STATION)


def test_workspace_skeleton_present() -> None:
    for rel in (
        "contracts/access.py",
        "workspace/__init__.py",
        "workspace/projects.py",
    ):
        assert (_SERVICES_ROOT / rel).is_file(), f"missing {rel}"


def test_workspace_init_is_reexport_only() -> None:
    tree = ast.parse((_SERVICES_ROOT / "workspace" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        assert not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)), (
            f"workspace/__init__.py defines implementation: {type(node).__name__}"
        )


@pytest.mark.parametrize(
    "snippet",
    [
        "import acp\n",
        "import acp.scheduler.jobs\n",
        "from acp.scheduler import jobs\n",
        "from acp import scheduler\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import acp\n",
        "def f():\n    import acp.scheduler.store\n",
        "import importlib\nimportlib.import_module('acp.scheduler.store')\n",
        "x = __import__('acp')\n",
        "home = 'ac' + 'p.scheduler.jobs'\n",
        "import sys\nmod = sys.modules['acp.scheduler.store']\n",
        "import os\nm = getattr(os, 'acp.scheduler')\n",
    ],
)
def test_scanner_catches_obfuscated_acp_access(snippet: str) -> None:
    assert scan_for_acp_imports(snippet), f"scanner missed: {snippet!r}"


def test_scanner_allows_docstring_and_package_name_mentions() -> None:
    source = (
        '"""This module must never import acp; see acp.scheduler.projects."""\n'
        "\n"
        'PKG = "acp_services.workspace"\n'
        "def f():\n"
        '    """acp is legacy here."""\n'
        "    return PKG\n"
    )
    assert scan_for_acp_imports(source) == []


def test_negative_injected_acp_import_into_projects_is_detected(tmp_path: Path) -> None:
    """QA failure scenario: injecting ``import acp.scheduler.jobs`` must FAIL the gate."""
    real = _SERVICES_ROOT / "workspace" / "projects.py"
    original = real.read_text(encoding="utf-8")
    mutated = original.replace(
        "from __future__ import annotations",
        "from __future__ import annotations\n\nimport acp.scheduler.jobs  # injected",
        1,
    )
    assert mutated != original, "mutation did not apply"
    temp_copy = tmp_path / "projects_injected.py"
    temp_copy.write_text(mutated, encoding="utf-8")

    violations = scan_for_acp_imports(temp_copy.read_text(encoding="utf-8"))
    assert violations, "injected acp import not detected on temp copy"
    assert any("acp.scheduler.jobs" in item for item in violations)
    # The real Services file stays clean.
    assert scan_for_acp_imports(original) == []


# ---------------------------------------------------------------------------
# (b) single-implementation identity
# ---------------------------------------------------------------------------


def test_project_manager_is_single_implementation() -> None:
    import acp.scheduler.projects as legacy
    import acp_services.workspace.projects as moved

    assert legacy.ProjectManager is moved.ProjectManager
    assert legacy.sanitize_project_dir_name is moved.sanitize_project_dir_name
    # Package-level re-export resolves to the same object too.
    import acp_services.workspace as workspace_pkg

    assert workspace_pkg.ProjectManager is moved.ProjectManager


# ---------------------------------------------------------------------------
# (c) Services migration ledger — six frozen columns
# ---------------------------------------------------------------------------

_LEDGER_COLUMNS = ("能力", "当前实现驻点", "兼容入口", "生产消费者", "退出条件", "验收测试")
_LEDGER_PATH_RE = re.compile(r"src/[A-Za-z0-9_./-]+\.py")


def _ledger_rows() -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    in_table = False
    for line in _LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if not in_table:
            if len(cells) >= 2 and cells[0] == _LEDGER_COLUMNS[0]:
                in_table = True
            continue
        if all(cell and set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def test_services_migration_ledger_rows_have_six_columns() -> None:
    rows = _ledger_rows()
    assert rows, f"no ledger rows parsed from {_LEDGER_PATH}"
    for cells in rows:
        assert len(cells) == len(_LEDGER_COLUMNS), (
            f"ledger row has {len(cells)} columns (expected 6): {cells}"
        )
        assert _LEDGER_PATH_RE.search(cells[1]), f"row station missing src path: {cells}"


def test_services_migration_ledger_has_project_owner_row() -> None:
    rows = _ledger_rows()
    project_rows = [cells for cells in rows if "ProjectManager" in cells[0]]
    assert project_rows, "ProjectManager row missing from services migration ledger"


# ---------------------------------------------------------------------------
# (d) packaging guard
# ---------------------------------------------------------------------------


def _packaging_include() -> list[str]:
    text = _PYPROJECT.read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        import tomli as tomllib
    if tomllib is not None:
        data = tomllib.loads(text)
        return list(data["tool"]["setuptools"]["packages"]["find"]["include"])
    section = text.split("[tool.setuptools.packages.find]", 1)[1].split("[", 1)[0]
    match = re.search(r"include\s*=\s*\[(.*?)\]", section, re.S)
    assert match, "packages.find.include not found"
    return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))


def test_pyproject_packages_acp_services() -> None:
    include = _packaging_include()
    assert "acp_services*" in include, f"pyproject include missing acp_services*: {include}"
    assert "cccp*" in include and "acp*" in include


# ---------------------------------------------------------------------------
# Access contract semantics (todo 6 / B01-1)
# ---------------------------------------------------------------------------


def test_access_dataclasses_frozen_with_contract_fields() -> None:
    import dataclasses

    from acp_services.contracts import access

    expected = {
        "Principal": {
            "principal_id",
            "kind",
            "display_name",
            "tenant_id",
            "roles",
            "deployment_tier",
        },
        "AccessScope": {"scope_id", "tenant_id", "resource_kinds", "action", "project_ids"},
        "ResourceGrant": {
            "grant_id",
            "principal_id",
            "scope_id",
            "resource_id",
            "actions",
            "granted_at",
            "expires_at",
            "basis_revision",
        },
        "AccessDecision": {
            "decision_id",
            "effect",
            "principal_id",
            "scope_id",
            "reason_code",
            "decided_at",
            "available_actions",
        },
    }
    assert access.Decision is access.AccessDecision
    for name, fields in expected.items():
        cls = getattr(access, name)
        assert dataclasses.is_dataclass(cls), name
        assert {f.name for f in dataclasses.fields(cls)} == fields, name
        params = cls.__dataclass_params__
        assert params.frozen, f"{name} must be frozen"


def test_local_principal_fixed_and_ignores_client_tenant() -> None:
    from acp_services.contracts.access import LOCAL_TENANT_ID, LocalPrincipal

    principal = LocalPrincipal.resolve(
        source_host="127.0.0.1",
        requested_tenant_id="someone-elses-tenant",
        display_name="Local User",
        roles=("admin",),
    )
    assert principal.principal_id == LocalPrincipal.principal_id
    assert principal.tenant_id == LOCAL_TENANT_ID
    assert principal.roles == ("admin",)
    assert principal.display_name == "Local User"

    # A hostile client tenant never moves the boundary.
    hostile = LocalPrincipal.resolve(requested_tenant_id="attacker")
    assert hostile.tenant_id == LOCAL_TENANT_ID


def test_local_single_access_policy_allow_deny_and_revocation() -> None:
    from datetime import datetime, timezone

    from acp_services.contracts.access import (
        AccessScope,
        DecisionEffect,
        LocalPrincipal,
        LocalSingleAccessPolicy,
        ReasonCode,
        ResourceGrant,
    )

    principal = LocalPrincipal.resolve(roles=())
    scope = AccessScope(
        scope_id="s1",
        tenant_id=principal.tenant_id,
        action="read",
        resource_kinds=("project",),
        project_ids=("proj1",),
    )
    grant = ResourceGrant(
        grant_id="g1",
        principal_id=principal.principal_id,
        scope_id="s1",
        resource_id="proj1",
        actions=("read",),
        granted_at="2026-10-18T00:00:00Z",
        expires_at=None,
        basis_revision=1,
    )
    policy = LocalSingleAccessPolicy(grants=[grant], scopes=[scope])

    allowed = policy.authorize(principal, scope, resource_id="proj1", action="read")
    assert allowed.effect == DecisionEffect.ALLOW
    assert allowed.reason_code == ReasonCode.GRANTED
    assert allowed.available_actions == ("read",)

    # No grant for a different project -> explicit deny, no anonymous fallback.
    denied = policy.authorize(principal, scope, resource_id="proj2", action="read")
    assert denied.effect == DecisionEffect.DENY
    assert denied.reason_code == ReasonCode.NO_GRANT
    assert denied.available_actions is None

    # Revocation is testable and reversible.
    policy.revoke("g1")
    revoked = policy.authorize(principal, scope, resource_id="proj1", action="read")
    assert revoked.effect == DecisionEffect.DENY
    assert revoked.reason_code == ReasonCode.REVOKED
    policy.restore("g1")
    assert policy.authorize(principal, scope, resource_id="proj1", action="read").effect == (
        DecisionEffect.ALLOW
    )

    # Tenant mismatch and expiry are explicit denies, not silent allows.
    foreign_scope = AccessScope(
        scope_id="s1", tenant_id="other-tenant", action="read", project_ids=("proj1",)
    )
    mismatch = policy.authorize(principal, foreign_scope, resource_id="proj1", action="read")
    assert mismatch.reason_code == ReasonCode.SCOPE_MISMATCH

    expired_grant = ResourceGrant(
        grant_id="g2",
        principal_id=principal.principal_id,
        scope_id="s1",
        resource_id="proj1",
        actions=("read",),
        granted_at="2026-01-01T00:00:00Z",
        expires_at="2026-01-02T00:00:00Z",
        basis_revision=1,
    )
    expired_policy = LocalSingleAccessPolicy(grants=[expired_grant], scopes=[scope])
    expired = expired_policy.authorize(
        principal,
        scope,
        resource_id="proj1",
        action="read",
        now=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert expired.effect == DecisionEffect.DENY
    assert expired.reason_code == ReasonCode.EXPIRED
