"""Read-only AST inventory for the 2026-10-11 ACP/CCCP code review.

Run from the repository root with Python 3.11+. These are candidate-generating
metrics, not a proof of runtime reachability or a full cyclomatic analysis.
"""

from __future__ import annotations

import ast
import collections
import copy
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent / "evidence"
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def module_name(path: Path) -> str:
    parts = list(path.relative_to(ROOT / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def local_nodes(node: ast.AST):
    """Walk a function without adding nested functions' or classes' decisions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (*FUNCTIONS, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from local_nodes(child)


def branch_score(node: ast.AST) -> int:
    score = 1
    for item in local_nodes(node):
        if isinstance(
            item,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.IfExp,
                ast.ExceptHandler,
                ast.comprehension,
            ),
        ):
            score += 1
        elif isinstance(item, ast.BoolOp):
            score += len(item.values) - 1
        elif isinstance(item, ast.Match):
            score += len(item.cases)
    return score


def is_docstring(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def normalized_body(node: ast.AST) -> str:
    body = copy.deepcopy(node.body)
    if body and is_docstring(body[0]):
        body.pop(0)
    return ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False)


def import_targets(node: ast.AST, module: str, is_package: bool) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    base = node.module or ""
    if node.level:
        package = module.split(".") if is_package else module.split(".")[:-1]
        package = package[: len(package) - node.level + 1]
        base = ".".join([*package, *([base] if base else [])])
    return [base, *[f"{base}.{alias.name}" for alias in node.names]]


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices, low = {}, {}
    stack, onstack, groups = [], set(), []

    def visit(v: str) -> None:
        nonlocal index
        indices[v] = low[v] = index
        index += 1
        stack.append(v)
        onstack.add(v)
        for w in sorted(graph[v]):
            if w not in indices:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            group = []
            while True:
                w = stack.pop()
                onstack.remove(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1:
                groups.append(sorted(group))

    for vertex in sorted(graph):
        if vertex not in indices:
            visit(vertex)
    return sorted(groups, key=lambda x: (-len(x), x))


def main() -> None:
    paths = sorted((ROOT / "src").glob("acp/**/*.py"))
    paths += sorted((ROOT / "src").glob("cccp/**/*.py"))
    parsed = {}
    parse_errors = []
    modules, functions, handlers, duplicate_scopes, unreachable = [], [], [], [], []
    loaded_names = collections.Counter()
    fingerprints = collections.defaultdict(list)
    graph = {module_name(path): set() for path in paths}
    imports = []

    for path in paths:
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT).as_posix()
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as error:
            parse_errors.append({"file": relative, "line": error.lineno, "error": str(error)})
            continue
        parsed[path] = tree
        module = module_name(path)
        all_nodes = list(ast.walk(tree))
        parent = {child: item for item in all_nodes for child in ast.iter_child_nodes(item)}
        modules.append(
            {
                "file": relative,
                "module": module,
                "lines": len(source.splitlines()),
                "functions": sum(isinstance(n, FUNCTIONS) for n in all_nodes),
                "classes": sum(isinstance(n, ast.ClassDef) for n in all_nodes),
            }
        )
        for node in all_nodes:
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                loaded_names[node.id] += 1
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                loaded_names[node.attr] += 1
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in import_targets(node, module, path.name == "__init__.py"):
                    if target.startswith(("acp", "cccp")):
                        imports.append({"file": relative, "line": node.lineno, "target": target})
                    if target in graph and target != module:
                        graph[module].add(target)
            if isinstance(node, FUNCTIONS):
                scopes = []
                current = parent.get(node)
                while current:
                    if isinstance(current, (ast.ClassDef, *FUNCTIONS)):
                        scopes.append(current.name)
                    current = parent.get(current)
                qualified = ".".join([*reversed(scopes), node.name])
                row = {
                    "file": relative,
                    "line": node.lineno,
                    "end_line": node.end_lineno,
                    "name": node.name,
                    "qualified_name": qualified,
                    "lines": node.end_lineno - node.lineno + 1,
                    "branch_score": branch_score(node),
                    "decorators": [ast.unparse(n) for n in node.decorator_list],
                }
                functions.append(row)
                fingerprint = hashlib.sha256(normalized_body(node).encode()).hexdigest()
                fingerprints[fingerprint].append(row)
            if isinstance(node, ast.ExceptHandler):
                exception = ast.unparse(node.type) if node.type else "bare"
                if exception in {"Exception", "BaseException", "bare"}:
                    handlers.append(
                        {
                            "file": relative,
                            "line": node.lineno,
                            "exception": exception,
                            "body": "\n".join(ast.unparse(n) for n in node.body),
                        }
                    )
            if isinstance(node, (ast.Module, ast.ClassDef, *FUNCTIONS)):
                names = collections.defaultdict(list)
                for statement in node.body:
                    if isinstance(statement, (ast.ClassDef, *FUNCTIONS)):
                        names[statement.name].append(statement.lineno)
                for name, lines in names.items():
                    if len(lines) > 1:
                        duplicate_scopes.append(
                            {
                                "file": relative,
                                "scope": getattr(node, "name", "<module>"),
                                "name": name,
                                "lines": lines,
                            }
                        )
            for _field, value in ast.iter_fields(node):
                if (
                    isinstance(value, list)
                    and value
                    and all(isinstance(n, ast.stmt) for n in value)
                ):
                    for i, statement in enumerate(value[:-1]):
                        if isinstance(statement, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
                            unreachable.append(
                                {
                                    "file": relative,
                                    "after_line": statement.lineno,
                                    "unreachable_line": value[i + 1].lineno,
                                }
                            )

    incoming = collections.defaultdict(set)
    for src, targets in graph.items():
        for target in targets:
            incoming[target].add(src)
    large_duplicates = [
        group
        for group in fingerprints.values()
        if len(group) > 1 and min(row["lines"] for row in group) >= 12
    ]
    small_duplicates = [
        group
        for group in fingerprints.values()
        if len(group) > 1 and min(row["lines"] for row in group) < 12
    ]
    inventory = {
        "schema_version": 1,
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "metric_note": (
            "Physical lines include comments/blank lines. Branch score is an AST heuristic; "
            "imports include lazy/TYPE_CHECKING imports and do not prove runtime execution."
        ),
        "summary": {
            pkg: {
                "files": sum(r["module"].split(".")[0] == pkg for r in modules),
                "lines": sum(r["lines"] for r in modules if r["module"].split(".")[0] == pkg),
                "functions": sum(r["file"].startswith(f"src/{pkg}/") for r in functions),
                "broad_handlers": sum(r["file"].startswith(f"src/{pkg}/") for r in handlers),
            }
            for pkg in ("acp", "cccp")
        },
        "parse_errors": parse_errors,
        "modules": sorted(modules, key=lambda r: (-r["lines"], r["file"])),
        "functions": sorted(functions, key=lambda r: (-r["branch_score"], -r["lines"], r["file"])),
        "broad_handlers": handlers,
        "duplicate_definitions_same_scope": duplicate_scopes,
        "unreachable_after_terminator": unreachable,
        "large_exact_body_duplicates": sorted(
            large_duplicates, key=lambda g: -max(r["lines"] for r in g)
        ),
        "small_exact_body_duplicates": small_duplicates,
        "zero_name_load_candidates": [
            r
            for r in functions
            if not loaded_names[r["name"]]
            and not r["decorators"]
            and r["name"].startswith("_")
            and not r["name"].startswith("__")
        ],
        "modules_without_direct_incoming": [r for r in modules if not incoming[r["module"]]],
        "import_sccs": strongly_connected(graph),
        "local_imports": imports,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ast_inventory.json").write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "summary": inventory["summary"],
                "parse_errors": parse_errors,
                "largest_modules": inventory["modules"][:18],
                "largest_functions": inventory["functions"][:25],
                "duplicate_definitions_same_scope": duplicate_scopes,
                "unreachable_after_terminator": unreachable,
                "large_exact_body_duplicates": inventory["large_exact_body_duplicates"][:12],
                "zero_name_load_candidates": inventory["zero_name_load_candidates"],
                "import_sccs": inventory["import_sccs"][:6],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
