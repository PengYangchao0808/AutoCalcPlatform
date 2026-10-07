"""TODO 36 (A3/A4): workflow → task-entry call matrix + single execution core.

Guards, with AST + runtime assertions, that:

1. The matrix document ``docs/ACP_CCCP_Call_Matrix.md`` covers all 14 active
   workflows and their legal branches (protocols / profiles / refinement
   policies), not just the default path.
2. Every workflow's execution route references a unique
   ``cccp.calculation`` task core (``run_*`` / ``run_batch``), and those cores
   really exist in the ``cccp`` task layer at runtime.
3. No executable direct backend/runner call (``get_backend`` /
   ``require_backend`` / ``run_shermo`` / backend capability methods) remains
   in the ACP workflow surface outside the sanctioned isolated legacy shim.
4. There is no second implementation of optimize / single-point / frequency /
   thermochemistry on the ACP side: the only ACP-side
   ``def run_singlepoint|run_optimize|run_frequency|run_thermochemistry`` are
   the compat shims and the plan adapters.

Acceptance criteria for plan todo 36 live here.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
ACP = SRC / "acp"
MATRIX_DOC = REPO / "docs" / "ACP_CCCP_Call_Matrix.md"

#: The 14 active QC workflows (excludes the ``fake`` demo workflow).
ACTIVE_WORKFLOWS: tuple[str, ...] = (
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
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

#: Unique ``cccp.calculation`` task cores and the module that defines each.
TASK_CORE_MODULES: dict[str, str] = {
    "run_singlepoint": "cccp.calculation.tasks.singlepoint",
    "run_optimize": "cccp.calculation.tasks.optimize",
    "run_frequency": "cccp.calculation.tasks.frequency",
    "run_scan": "cccp.calculation.tasks.scan",
    "run_irc": "cccp.calculation.tasks.irc",
    "run_casscf": "cccp.calculation.tasks.casscf",
    "run_thermochemistry": "cccp.calculation.tasks.thermochemistry",
    "run_conformer_search": "cccp.calculation.tasks.conformer_search",
    "run_censo_refine": "cccp.calculation.tasks.censo_refine",
    "run_md_sampling": "cccp.calculation.tasks.md_sampling",
    "run_clustering": "cccp.calculation.tasks.clustering",
    "run_nmr_shielding": "cccp.calculation.tasks.nmr_shielding",
    "run_xtb_path_search": "cccp.calculation.tasks.xtb_path_search",
    "run_orca_gradient": "cccp.calculation.tasks.orca_gradient",
    "run_batch": "cccp.calculation.batch",
}

#: Per-workflow execution route (ACP modules on the path) and the cccp task
#: cores it must reach.  ``route`` is intentionally a superset including every
#: legal branch's engine so a route cannot silently regress to a direct call.
WORKFLOW_ROUTES: dict[str, dict[str, object]] = {
    "Confsearch": {
        "route": (
            "src/acp/confsearch/engine.py",
            "src/acp/confsearch/protocols/xtb_crest.py",
            "src/acp/confsearch/protocols/censo_crest.py",
            "src/acp/confsearch/protocols/xtb_md.py",
            "src/acp/confsearch/protocols/xtbmd_censo.py",
            "src/acp/workflows/ensemble.py",
            "src/acp/workflows/energy.py",
            "src/acp/workflows/energy_shared.py",
            "src/acp/workflows/xtbmd_censo_energy.py",
            "src/acp/workflows/xtbmd_md.py",
        ),
        "tasks": (
            "run_conformer_search",
            "run_censo_refine",
            "run_md_sampling",
            "run_clustering",
            "run_optimize",
            "run_frequency",
            "run_singlepoint",
            "run_thermochemistry",
        ),
    },
    "PESsearch": {
        "route": (
            "src/acp/workflows/pes_search.py",
            "src/acp/calculations/pes/engine.py",
            "src/acp/calculations/pes/scan.py",
            "src/acp/calculations/batch/singlepoint.py",
            "src/acp/calculations/batch/_singlepoint_execution.py",
        ),
        "tasks": ("run_scan", "run_singlepoint", "run_batch"),
    },
    "BatchOptimize": {
        "route": (
            "src/acp/workflows/batch_optimize.py",
            "src/acp/calculations/batch/engine.py",
            "src/acp/calculations/primitives/optimize.py",
            "src/acp/calculations/primitives/frequency.py",
            "src/acp/calculations/primitives/singlepoint.py",
            "src/acp/calculations/primitives/thermochemistry.py",
        ),
        "tasks": (
            "run_optimize",
            "run_frequency",
            "run_singlepoint",
            "run_thermochemistry",
        ),
    },
    "irc": {
        "route": (
            "src/acp/workflows/irc.py",
            "src/acp/calculations/primitives/irc.py",
        ),
        "tasks": ("run_irc",),
    },
    "scan": {
        "route": (
            "src/acp/workflows/simple.py",
            "src/acp/calculations/primitives/scan.py",
        ),
        "tasks": ("run_scan",),
    },
    "tsmode": {
        "route": (
            "src/acp/workflows/tsmode.py",
            "src/acp/calculations/tsmode/engine.py",
            "src/acp/calculations/primitives/optimize.py",
            "src/acp/calculations/primitives/frequency.py",
        ),
        "tasks": ("run_optimize", "run_frequency"),
    },
    "casscf": {
        "route": (
            "src/acp/workflows/simple.py",
            "src/acp/calculations/primitives/casscf.py",
        ),
        "tasks": ("run_casscf",),
    },
    "nmr": {
        "route": (
            "src/acp/workflows/nmr.py",
            "src/acp/workflows/energy_shared.py",
        ),
        "tasks": (
            "run_conformer_search",
            "run_censo_refine",
            "run_nmr_shielding",
        ),
    },
    "singlepoint": {
        "route": (
            "src/acp/workflows/simple.py",
            "src/acp/calculations/primitives/singlepoint.py",
        ),
        "tasks": ("run_singlepoint",),
    },
    "optimize": {
        "route": (
            "src/acp/workflows/simple.py",
            "src/acp/calculations/primitives/optimize.py",
        ),
        "tasks": ("run_optimize",),
    },
    "frequency": {
        "route": (
            "src/acp/workflows/simple.py",
            "src/acp/calculations/primitives/frequency.py",
        ),
        "tasks": ("run_frequency",),
    },
    "xtb_optimize": {
        "route": (
            "src/acp/workflows/simple.py",
            "src/acp/calculations/primitives/optimize.py",
        ),
        "tasks": ("run_optimize",),
    },
    "XtbPathSearch": {
        "route": ("src/acp/workflows/xtb_path.py",),
        "tasks": ("run_xtb_path_search",),
    },
    "OrcaGradient": {
        "route": ("src/acp/workflows/orca_gradient.py",),
        "tasks": ("run_orca_gradient",),
    },
}

#: Executable backend/runner entry points that must never appear in ACP code.
FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset({"get_backend", "require_backend", "run_shermo"})
FORBIDDEN_CALL_ATTRS: frozenset[str] = frozenset(
    {"optimize_geometry", "single_point", "run_md", "conformer_search"}
)

#: Sanctioned isolated legacy backend surface (re-export shim package).  The
#: ACP workflow routes must not import it; its own module internals are exempt
#: from the direct-call scan by design (root AGENTS.md ANTI #17).
SANCTIONED_BACKEND_SHIM_PREFIX = "src/acp/backends/"

#: ACP-side definitions of the shared cores that are allowed to exist because
#: they are pure compat shims / plan adapters — never QC implementations.
WRAPPER_DEF_ALLOWLIST: dict[str, frozenset[str]] = {
    "src/acp/calculations/primitives/singlepoint.py": frozenset({"run_singlepoint"}),
    "src/acp/calculations/primitives/optimize.py": frozenset({"run_optimize"}),
    "src/acp/calculations/primitives/frequency.py": frozenset({"run_frequency"}),
    "src/acp/calculations/primitives/thermochemistry.py": frozenset({"run_thermochemistry"}),
    "src/acp/workflows/simple.py": frozenset({"run_singlepoint", "run_optimize", "run_frequency"}),
}
GUARDED_CORE_DEFS: frozenset[str] = frozenset(
    {"run_singlepoint", "run_optimize", "run_frequency", "run_thermochemistry"}
)


# ── helpers ─────────────────────────────────────────────────────────────


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _iter_acp_py() -> list[Path]:
    return sorted(ACP.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _callable_name(node: ast.Call) -> tuple[str | None, str | None]:
    """Return (name, attr) of a call target; one of them is None."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id, None
    if isinstance(func, ast.Attribute):
        base = func.value
        base_name = base.id if isinstance(base, ast.Name) else None
        return base_name, func.attr
    return None, None


def _route_cccp_symbols(tree: ast.Module) -> set[str]:
    """Collect cccp.calculation task-core symbols referenced by a module.

    Handles both ``from cccp.calculation.tasks.x import run_y`` (original
    import name, so ``as`` aliases resolve correctly) and the
    ``_cccp_calculation.run_y`` attribute form used by the primitives.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "cccp.calculation" or module.startswith("cccp.calculation."):
                if module == "cccp.calculation.batch":
                    found.add("run_batch")
                for alias in node.names:
                    found.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cccp.calculation":
                    found.add(f"__module__:{alias.asname or alias.name}")
        elif isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name) and base.id in {
                "_cccp_calculation",
                "cccp_calculation",
            }:
                found.add(node.attr)
    return found


def _module_imports_acp_backends_batch(tree: ast.Module) -> bool:
    """True only for the isolated legacy batch surface, not the shim package."""
    batch = "acp.backends.batch"
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == batch or module.startswith(batch + "."):
                return True
        if isinstance(node, ast.Import):
            if any(
                alias.name == batch or alias.name.startswith(batch + ".") for alias in node.names
            ):
                return True
    return False


# ── 1. coverage: registry + document ────────────────────────────────────


def test_active_workflow_set_matches_matrix() -> None:
    from acp.scheduler.jobs import PUBLIC_WORKFLOWS, SUPPORTED_WORKFLOWS

    runtime = set(PUBLIC_WORKFLOWS)
    assert runtime == set(ACTIVE_WORKFLOWS), (
        "active workflow set drifted from the matrix; update ACTIVE_WORKFLOWS "
        "and docs/ACP_CCCP_Call_Matrix.md"
    )
    assert "fake" in SUPPORTED_WORKFLOWS
    assert set(WORKFLOW_ROUTES) == set(ACTIVE_WORKFLOWS)


def test_matrix_document_covers_all_workflows_and_branches() -> None:
    assert MATRIX_DOC.is_file(), f"missing matrix document: {MATRIX_DOC}"
    text = MATRIX_DOC.read_text(encoding="utf-8")

    for workflow in ACTIVE_WORKFLOWS:
        assert workflow in text, f"matrix document omits workflow {workflow!r}"

    # Every branch token the matrix promises to cover.
    branch_tokens = (
        "xtb-crest",
        "xtb-md",
        "censo-crest",
        "xtbmd-censo",
        "screen",
        "rank1",
        "cumulative-99",
        "all",
        "opt_only",
        "opt_freq",
        "opt_freq_sp",
        "opt_freq_sp_thermo",
        "guided_scan",
        "reverse_peb",
        "direct_ts",
        "forward",
        "reverse",
        "both",
    )
    for token in branch_tokens:
        assert token in text, f"matrix document omits branch token {token!r}"


# ── 2. route → unique task core ─────────────────────────────────────────


@pytest.mark.parametrize("workflow", ACTIVE_WORKFLOWS)
def test_workflow_route_reaches_cccp_task_core(workflow: str) -> None:
    spec = WORKFLOW_ROUTES[workflow]
    route = [REPO / rel for rel in spec["route"]]  # type: ignore[union-attr]
    expected = set(spec["tasks"])  # type: ignore[arg-type]

    for module_path in route:
        assert module_path.is_file(), f"missing route module {_rel(module_path)}"

    referenced: set[str] = set()
    for module_path in route:
        referenced |= _route_cccp_symbols(_parse(module_path))

    missing = expected - referenced
    assert not missing, (
        f"workflow {workflow!r} route does not reference task core(s) "
        f"{sorted(missing)}; found {sorted(referenced)}"
    )

    # The cores must not be reached from the sanctioned legacy backend shim.
    for module_path in route:
        if SANCTIONED_BACKEND_SHIM_PREFIX in _rel(module_path):
            continue
        assert not _module_imports_acp_backends_batch(_parse(module_path)), (
            f"workflow {workflow!r} route imports the isolated legacy backend "
            f"surface {_rel(module_path)}"
        )


def test_every_referenced_task_core_exists_in_cccp() -> None:
    referenced: set[str] = set()
    for spec in WORKFLOW_ROUTES.values():
        referenced |= set(spec["tasks"])  # type: ignore[arg-type]

    for symbol in sorted(referenced):
        assert symbol in TASK_CORE_MODULES, f"unknown task core {symbol!r}"
        module = importlib.import_module(TASK_CORE_MODULES[symbol])
        assert hasattr(module, symbol), (
            f"{TASK_CORE_MODULES[symbol]}.{symbol} is missing — the unique "
            "execution core does not define it"
        )


def test_cccp_task_cores_are_not_reimplemented_in_acp() -> None:
    """ACP must not define any ``cccp.calculation`` task-core function."""
    for symbol, module_path in TASK_CORE_MODULES.items():
        if module_path == "cccp.calculation.batch":
            continue
        cccp_def = SRC / (module_path.replace(".", "/") + ".py")
        assert cccp_def.is_file(), f"missing cccp task core module {cccp_def}"
        cccp_names = {
            node.name
            for node in ast.walk(_parse(cccp_def))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert symbol in cccp_names, f"{module_path} must define the unique task core {symbol!r}"


# ── 3. no executable direct backend/runner calls ────────────────────────


def test_no_executable_backend_or_runner_calls_in_acp() -> None:
    offenders: list[str] = []
    for path in _iter_acp_py():
        rel = _rel(path)
        if rel.startswith(SANCTIONED_BACKEND_SHIM_PREFIX):
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name, attr = _callable_name(node)
            if name in FORBIDDEN_CALL_NAMES:
                offenders.append(f"{rel}:{node.lineno}: {name}(...)")
            if attr in FORBIDDEN_CALL_ATTRS:
                offenders.append(f"{rel}:{node.lineno}: .{attr}(...)")
    assert not offenders, "executable direct backend/runner call(s) remain in ACP: " + "; ".join(
        offenders
    )


def test_isolated_legacy_backend_surface_is_not_on_any_route() -> None:
    route_modules = {
        REPO / rel
        for spec in WORKFLOW_ROUTES.values()
        for rel in spec["route"]  # type: ignore[union-attr]
    }
    for module_path in route_modules:
        assert not _module_imports_acp_backends_batch(_parse(module_path)), (
            f"{_rel(module_path)} imports the isolated legacy backend surface"
        )


# ── 4. no second optimize/SP/frequency/thermochemistry implementation ───


def test_no_second_core_implementation_in_acp() -> None:
    found: dict[str, set[str]] = {}
    for path in _iter_acp_py():
        rel = _rel(path)
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                node.name in GUARDED_CORE_DEFS
            ):
                found.setdefault(rel, set()).add(node.name)
                assert rel in WRAPPER_DEF_ALLOWLIST, (
                    f"unexpected ACP-side definition of {node.name!r} in {rel}:"
                    " only compat shims / plan adapters may define it"
                )
                assert node.name in WRAPPER_DEF_ALLOWLIST[rel], (
                    f"{rel} may not define {node.name!r}"
                )

    # Acceptance grep parity: exactly the sanctioned wrapper files define them.
    assert set(found) <= set(WRAPPER_DEF_ALLOWLIST)


def _function_defs(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _body_call_targets(node: ast.AST) -> set[tuple[str | None, str | None]]:
    return {_callable_name(child) for child in ast.walk(node) if isinstance(child, ast.Call)}


def _cccp_calculation_alias(tree: ast.Module) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cccp.calculation":
                    return alias.asname or alias.name
        if isinstance(node, ast.ImportFrom) and node.module == "cccp":
            for alias in node.names:
                if alias.name == "calculation":
                    return alias.asname or alias.name
    return None


PRIMITIVE_CORE_PAIRS: dict[str, tuple[str, str]] = {
    "src/acp/calculations/primitives/singlepoint.py": ("run_singlepoint", "run_singlepoint"),
    "src/acp/calculations/primitives/optimize.py": ("run_optimize", "run_optimize"),
    "src/acp/calculations/primitives/frequency.py": ("run_frequency", "run_frequency"),
    "src/acp/calculations/primitives/thermochemistry.py": (
        "run_thermochemistry",
        "run_thermochemistry",
    ),
}


@pytest.mark.parametrize("rel, pair", sorted(PRIMITIVE_CORE_PAIRS.items()))
def test_primitives_are_thin_forwarders(rel: str, pair: tuple[str, str]) -> None:
    wrapper_name, core_name = pair
    tree = _parse(REPO / rel)
    funcs = _function_defs(tree)
    assert wrapper_name in funcs, f"{rel} missing {wrapper_name}"

    worker_name = "execute_" + wrapper_name.split("_", 1)[1]
    wrapper_targets = _body_call_targets(funcs[wrapper_name])
    assert any(target[0] == worker_name for target in wrapper_targets), (
        f"{rel}::{wrapper_name} is not a forwarding shim to {worker_name}"
    )
    statements = [
        statement
        for statement in funcs[wrapper_name].body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    assert len(statements) == 1, (
        f"{rel}::{wrapper_name} has extra body statements (must be a one-line forwarder)"
    )

    assert worker_name in funcs, f"{rel} missing {worker_name}"
    alias = _cccp_calculation_alias(tree)
    assert alias is not None, f"{rel} does not import cccp.calculation"
    worker_targets = _body_call_targets(funcs[worker_name])
    assert any(target[0] == alias and target[1] == core_name for target in worker_targets), (
        f"{rel}::{worker_name} does not call {alias}.{core_name}"
    )


@pytest.mark.parametrize("name", ["run_singlepoint", "run_optimize", "run_frequency"])
def test_simple_adapters_build_plan_and_execute(name: str) -> None:
    tree = _parse(SRC / "acp" / "workflows" / "simple.py")
    funcs = _function_defs(tree)
    assert name in funcs
    targets = _body_call_targets(funcs[name])
    called = {t[0] for t in targets if t[0] is not None}
    assert "_build_plan" in called, f"simple.py::{name} does not build a plan"
    assert "_execute" in called, f"simple.py::{name} does not run the executor"


def test_wrapper_bodies_contain_no_qc_execution() -> None:
    """The ACP-side wrapper defs must be thin — no QC execution inside."""
    for path in _iter_acp_py():
        tree = _parse(path)
        module_has_forbidden_import = any(
            isinstance(node, ast.Import)
            and node.names
            and any(alias.name == "subprocess" for alias in node.names)
            for node in ast.walk(tree)
        )
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in GUARDED_CORE_DEFS:
                continue
            calls = {
                name
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
                for name, _ in [_callable_name(child)]
                if name is not None
            }
            assert not (calls & FORBIDDEN_CALL_NAMES), (
                f"{_rel(path)}::{node.name} calls a backend/runner entry: "
                f"{sorted(calls & FORBIDDEN_CALL_NAMES)}"
            )
            assert not module_has_forbidden_import, (
                f"{_rel(path)} defines task wrapper {node.name!r} but imports "
                "subprocess (QC must run in cccp.calculation)"
            )
