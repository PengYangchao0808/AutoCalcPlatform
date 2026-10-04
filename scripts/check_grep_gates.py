#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"# noqa: SIZE_OK"

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/check_grep_gates.py --list-gates
# 3. Or make executable and run:
#      chmod +x scripts/check_grep_gates.py && ./scripts/check_grep_gates.py --list-gates
# ──────────────────
# pyright: reportAny=false, reportUnknownVariableType=false
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
EX_LINE_PATTERN: Final[str] = "".join(
    (
        r"^\s*#|status.*retired|_reject_retired_workflow\(|",
        r'^\s*"(dft_)?(optfreq|optfreqsp|low_confirm|high_confirm)":\s*\{|',
        r'"(id|label|method_schema_id|level_id)":\s*"[^"]*',
        r'(optfreq|optfreqsp|[Ll]owconfirm|[Hh]ighconfirm|low_confirm|high_confirm)[^"]*"',
        r'|(S[34])\b.*(?:contract|confirmation|profile|→)',  # S3/S4 in historical descriptions
        r'|"optfreq":\s*',  # historical schema keys
        r'|optfreq.*scan',  # backend capability lists
    )
)
EX_PATH_PREFIXES: Final[tuple[str, ...]] = ("docs/", "tests/fixtures/")
WAVE6_SCHEDULER_PATTERN: Final[str] = "".join(
    (
        r"BOND_SCAN_STAGES|resolve_study_layout|find_study_layout|find_reaction_json|",
        r"copy_handoff_payload|stage_batch_request|prepare_stage_batch_config|",
        r"pessearch_method_flags|lowconfirm_method_flags|highconfirm_method_flags|",
        r"mechanism_method_flags|mechanism_resolved_settings|write_mechanism_job_config|",
        r"write_mechanism_reaction_json|validate_stage_artifact|MechanismProjectStore|",
        r"mechanism_config|mechanism_reaction|s2_path_manifest|s3_lowconfirm_manifest|",
        r"s4_highconfirm_manifest|_mechanism_role_source|materialized_role_paths|",
        r'MECHANISM_CONFIG_FILENAME|--mechanism-config|wf == "mechanism"',
    )
)
WAVE7_DB_WRITES_PATTERN: Final[str] = "".join(
    (
        r"upsert_mechanism_study|update_mechanism_study_reaction|",
        r"update_mechanism_study_plan|upsert_decision_point|INSERT INTO mechanism_|",
        r"UPDATE mechanism_|MechanismProjectStore\(",
    )
)
FRONTEND_REMOVED_TOKENS_PATTERN: Final[str] = "".join(
    (
        r"STAGE_WORKFLOW_IDS|STAGE_DEFAULT_ARTIFACTS|MECH_PROJECT_STAGES|",
        r"S[1-4] (Confsearch|PESsearch|Lowconfirm|Highconfirm)|",
        r"previewReactionDefinition|confirmReactionDefinition|confirmMechanismPlan|",
        r"submitS2Review|loadMechanismReview|/promote|/reviews/\{|stage-batch-|",
        r"Lowconfirm|Highconfirm",
    )
)
MECHANISM_IMPORT_PATTERN: Final[str] = "".join(
    (
        r"from acp\.mechanism|import acp\.mechanism|acp\.mechanism\.|",
        r"from acp import mechanism",
    )
)
FINAL_FORBIDDEN_PATTERN: Final[str] = "".join(
    (
        r"StudyOrchestrator|MechanismProjectStore|LowConfirmProfile|",
        r"HighConfirmProfile|run_low_confirm|run_high_confirm|mechanism_project_id",
    )
)
WAVE6_PATTERN: Final[str] = (
    r"Lowconfirm|Highconfirm|optfreq|optfreqsp|mechanism_project_id|study phase|S3|S4"
)
FINAL_STAGE_PATTERN: Final[str] = r"Lowconfirm|Highconfirm|mechanism_project_id|MechanismProject"
FINAL_OPTFREQ_PATTERN: Final[str] = r"run_optfreq|run_optfreqsp|optfreqsp|optfreq"
CALCULATIONS_TERMS_PATTERN: Final[str] = r"study|stage_|S3|S4|promotion|review gate"
COMMENT_LINE_PATTERN: Final[str] = r"^\s*#"
WAVE2_OPTFREQ_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/cccp/",
    "src/acp/workflows/simple.py",
)
WAVE5_S2MANIFEST_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/acp/compat/legacy/",
    "src/acp/mechanism/",
)
WAVE6_SCHEDULER_ALLOWED_PATHS: Final[tuple[str, ...]] = ("src/acp/scheduler/store.py",)
WAVE7_SCOPE_PATHS: Final[tuple[str, ...]] = ("src/acp/api", "src/acp/scheduler")
FINAL_FORBIDDEN_ALLOWED_PATHS: Final[tuple[str, ...]] = ("src/acp/compat/legacy/",)
FINAL_STAGE_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/acp/compat/legacy/",
    "src/acp/api/mechanism_readonly.py",
    "src/acp/api/mechanism_readonly_schemas.py",
)
FINAL_OPTFREQ_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/acp/results/energy_graph.py",
)
FINAL_SHERMO_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/cccp/",
    "src/acp/calculations/primitives/thermochemistry.py",
    "src/acp/workflows/energy_shared.py",
)
SCOPE_SRC: Final[tuple[str, ...]] = ("src/",)
SCOPE_ACP: Final[tuple[str, ...]] = ("src/acp",)
SCOPE_CALCULATIONS: Final[tuple[str, ...]] = ("src/acp/calculations/",)
SCOPE_BATCH: Final[tuple[str, ...]] = ("src/acp/calculations/batch/",)
SCOPE_WORKFLOWS: Final[tuple[str, ...]] = ("src/acp/workflows/",)
SCOPE_CONFIRM: Final[tuple[str, ...]] = ("src/acp/mechanism/stages/confirm.py",)
SCOPE_CLI_API_SCHEDULER: Final[tuple[str, ...]] = (
    "src/acp/cli.py",
    "src/acp/api",
    "src/acp/scheduler",
)
SCOPE_SCHEDULER: Final[tuple[str, ...]] = ("src/acp/scheduler/",)
SCOPE_SRC_TESTS: Final[tuple[str, ...]] = ("src/", "tests/")
SCOPE_ACP_FRONTEND: Final[tuple[str, ...]] = ("src/acp", "frontend/")
SCOPE_FRONTEND: Final[tuple[str, ...]] = ("frontend/",)
SCOPE_CCCP: Final[tuple[str, ...]] = ("src/cccp/",)
SCOPE_README: Final[tuple[str, ...]] = ("README.md",)
SCOPE_RUN_PRIMITIVE: Final[tuple[str, ...]] = ("src/acp", "src/cccp/")
# Dual-position primitive implementation files (acp legacy shim + cccp task):
# the only places allowed to define the ``run_scan`` / ``run_irc`` primitives.
# Hard switch to the single cccp root is deferred to todo 23 (the acp allows
# must stay until the cccp task files exist — removing them early reddens the
# gate).  Workflow-layer same-name wrappers (e.g. ``workflows/simple.py::
# run_scan``) are entry-point wrappers, not primitive implementations, and are
# excluded by scope via SCOPE_WORKFLOWS below (chosen mechanism: a narrow
# workflow-layer exclusion — everything outside it still blocks).
PRIMITIVE_SCAN_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/acp/calculations/primitives/scan.py",
    "src/cccp/calculation/tasks/scan.py",
)
PRIMITIVE_IRC_ALLOWED_PATHS: Final[tuple[str, ...]] = (
    "src/acp/calculations/primitives/irc.py",
    "src/cccp/calculation/tasks/irc.py",
)
SCOPE_MECHANISM: Final[tuple[str, ...]] = ("src/acp/mechanism/",)
RETIRED_MAP_LINE_PATTERN: Final[str] = r"退役|retired|→"

# ── acp→cccp architecture remediation gates (Wave 0 / todo 4) ──────────────
CCCP_IMPORTS_ACP_PATTERN: Final[str] = r"from acp|import acp"
WORKFLOW_EXECUTES_QC_PATTERN: Final[str] = "".join(
    (r"get_backend\b|require_backend\b|run_shermo\b|CensoBackend\b")
)
WORKFLOW_ROUTE_ASSEMBLY_PATTERN: Final[str] = r'"! "\s*\+'
BACKEND_TASK_IMPORT_PATTERN: Final[str] = "".join(
    (
        r"cccp\.calculation(?!\.(?:errors|contracts)\b)",
        r"(?!\s+import\s+(?:errors|contracts)\b)",
        r"|run_singlepoint|run_optimize|run_thermochemistry",
    )
)
LEGACY_BATCH_IMPORT_PATTERN: Final[str] = "".join(
    (
        r"from acp\.backends\.batch\b|import acp\.backends\.batch\b|",
        r"from acp\.backends import batch\b",
    )
)
COMPAT_FORWARDER_HINT_PATTERN: Final[str] = (
    r"subprocess|get_backend\s*\(|require_backend\s*\("
)
SCOPE_WORKFLOWS_CONFSEARCH_NMR_CALC: Final[tuple[str, ...]] = (
    "src/acp/workflows/",
    "src/acp/confsearch/",
    "src/acp/nmr/",
    "src/acp/calculations/",
)
SCOPE_WORKFLOWS_CONFSEARCH: Final[tuple[str, ...]] = ("src/acp/workflows/", "src/acp/confsearch/")
# Capability modules (backend layer) that must not reach the task execution
# surface of ``cccp.calculation`` (only the pure-type modules are whitelisted).
BACKEND_CAPABILITY_SCOPES: Final[tuple[str, ...]] = (
    "src/cccp/backends/",
    "src/acp/backends/matrix.py",
    "src/acp/backends/base.py",
    "src/acp/backends/capabilities.py",
    "src/acp/backends/registry.py",
    "src/acp/backends/orca.py",
    "src/acp/backends/crest.py",
    "src/acp/backends/xtb.py",
    "src/acp/backends/censo_backend.py",
    "src/acp/backends/isostat_backend.py",
    "src/acp/backends/molclus_backend.py",
    "src/acp/backends/external_backend.py",
    "src/acp/backends/external.py",
)
# ``acp.calculations.contracts`` is intentionally NOT in scope: it keeps the
# ACP-side orchestration contracts (todo 11) and its relocated types are
# verified as ``A is B`` identity re-exports by tests instead.
COMPAT_FORWARDER_SCOPES: Final[tuple[str, ...]] = (
    "src/acp/backends/__init__.py",
    "src/acp/chem/composition.py",
    "src/acp/core/registry.py",
)
LEGACY_BATCH_EXCLUDED_PATHS: Final[tuple[str, ...]] = (
    "src/acp/backends/batch.py",
    "src/acp/backends/__init__.py",
)
LEGACY_BATCH_SYMBOL: Final[str] = "acp.backends.batch"
LEGACY_BATCH_EXEC_SYMBOL: Final[str] = "batch_single_point"
# Pure-type dependency whitelist: ``cccp.calculation.errors`` / ``contracts``
# may be imported by capability modules; every other ``cccp.calculation.*``
# execution surface is forbidden (both dotted and from-import forms).
PURE_CCCP_CALCULATION_MODULES: Final[frozenset[str]] = frozenset(
    {"cccp.calculation.errors", "cccp.calculation.contracts"}
)
EXEC_TASK_SYMBOLS: Final[frozenset[str]] = frozenset(
    {"run_singlepoint", "run_optimize", "run_thermochemistry"}
)
# QC execution calls forbidden inside compat forwarder modules.  ``get_backend``
# / ``require_backend`` are additionally forbidden as bare-Name call-form
# acquisitions (attribute-form passthrough is allowed only in a pure
# ``return``-delegation body).
FORWARDER_FORBIDDEN_CALLS: Final[frozenset[str]] = frozenset(
    {
        "get_backend",
        "require_backend",
        "run_shermo",
        "run_singlepoint",
        "run_optimize",
        "run_thermochemistry",
        "batch_single_point",
        "batch_process_thermo",
        "CensoBackend",
        "opt_freq",
        "subprocess",
    }
)
FORWARDER_DELEGATION_OK_CALLS: Final[frozenset[str]] = frozenset({"get_backend", "require_backend"})
PY_ONLY_SUFFIXES: Final[tuple[str, ...]] = (".py",)


@dataclass(frozen=True, slots=True)
class GateSpec:
    name: str
    pattern: str
    scope_paths: tuple[str, ...]
    allow_path_prefixes: tuple[str, ...] = ()
    allow_line_pattern: str | None = None
    use_shared_exemptions: bool = False
    excluded_path_prefixes: tuple[str, ...] = ()
    symbol_pattern: str | None = None
    symbol_map: tuple[tuple[str, str], ...] = ()
    ast_check: str | None = None
    grep_layer: bool = True
    file_suffixes: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line_number: int
    text: str
    allowed: bool
    symbol: str = ""


@dataclass(frozen=True, slots=True)
class ParsedArguments:
    list_gates: bool
    gate_and_paths: tuple[str, ...] | None
    suite: str | None = None
    root: str | None = None


class GateInputError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── Pending-redirect pin list (acp→cccp architecture remediation) ──
# Four frozen gates pin paths that move when calculation primitives migrate
# from ``src/acp/calculations/primitives`` to ``src/cccp/calculation``:
#
#   gate                  pinned path / constant                        redirect owner
#   --------------------  --------------------------------------------  --------------------
#   unique_run_scan       PRIMITIVE_SCAN_ALLOWED_PATHS (acp shim +      done (todo 15, dual
#                         cccp task), workflows excluded from scope     position; switch: todo 23)
#   unique_run_irc        PRIMITIVE_IRC_ALLOWED_PATHS (acp shim +       done (todo 15, dual
#                         cccp task), workflows excluded from scope     position; switch: todo 23)
#   wave2_shermo_external external_backend.py (acp shim + cccp impl)    done (todo 12, dual position)
#   final_shermo          FINAL_SHERMO_ALLOWED_PATHS (primitives/       comment allowance done
#                         thermochemistry.py, workflows/energy_shared,  (todo 15); hard switch
#                         src/cccp/) + COMMENT_LINE_PATTERN             todo 23
#
# When a pinned path moves, the gate spec must be redirected in the same todo
# that moves the code — never disabled, never silently widened.  Evidence:
# .omo/evidence/acp-cccp-remediation/task-1-guard-check.txt.
GATE_REGISTRY: Final[tuple[GateSpec, ...]] = (
    GateSpec("compat_no_writers", r"def write_", ("src/acp/compat/",)),
    GateSpec("wave2_optfreq", r"opt_freq\(", SCOPE_SRC, WAVE2_OPTFREQ_ALLOWED_PATHS),
    GateSpec(
        "wave2_shermo_external",
        r"run_shermo",
        ("src/acp/backends/external_backend.py", "src/cccp/backends/external_backend.py"),
    ),
    GateSpec("wave2_no_result_summary", r"write_result_summary", ("src/acp/workflows/simple.py",)),
    GateSpec("wave3_confirmengine", r"ConfirmEngine", ("src/acp/calculations/",)),
    GateSpec("wave3_no_batchmanifest", r"batch_calculation_manifest", ("src/acp/calculations/",)),
    GateSpec(
        "wave3_no_legacy_manifests",
        r"s3_lowconfirm_manifest|s4_highconfirm_manifest|s2_path_manifest",
        SCOPE_CALCULATIONS + SCOPE_WORKFLOWS,
    ),
    GateSpec("wave3_batch_no_stages", r"s3|s4|S3|S4", SCOPE_BATCH,
             allow_line_pattern=r"load_items_from_s[234]_manifest|read_s[234]_|s[234]_manifest"),
    GateSpec("wave4_endpointprovider", r"EndpointProvider", SCOPE_BATCH),
    GateSpec("wave4_confirm_no_irc", r"_run_irc_for_canonical|run_irc", SCOPE_CONFIRM),
    GateSpec("wave4_batch_no_irc", r"irc", SCOPE_BATCH,
             allow_line_pattern=r'"irc" in raw|reject.*irc|irc.*reject'),
    GateSpec(
        "wave5_s2manifest",
        r"s2_path_manifest|s2_candidate_manifest",
        ("src/acp/",),
        WAVE5_S2MANIFEST_ALLOWED_PATHS,
        COMMENT_LINE_PATTERN,
    ),
    GateSpec(
        "wave5_layout_imports", r"from acp\.mechanism\.layout import", SCOPE_CLI_API_SCHEDULER
    ),
    GateSpec("wave6", WAVE6_PATTERN, (), use_shared_exemptions=True),
    GateSpec(
        "wave6_scheduler_mechanism",
        WAVE6_SCHEDULER_PATTERN,
        SCOPE_SCHEDULER,
        WAVE6_SCHEDULER_ALLOWED_PATHS,
        COMMENT_LINE_PATTERN,
    ),
    GateSpec("wave6_zero", r"run_optfreq|run_optfreqsp|opt_freq\(", SCOPE_WORKFLOWS),
    GateSpec("wave7_no_db_writes", WAVE7_DB_WRITES_PATTERN, WAVE7_SCOPE_PATHS),
    GateSpec("wave8_confsearch_decoupled", r"from acp\.mechanism", ("src/acp/confsearch/",)),
    GateSpec("wave8_cccp_engine_gone", r"ConformerEngine", SCOPE_CCCP),
    GateSpec("wave8_engine_import_gone", r"cccp\.core\.engine|ConformerEngine", SCOPE_SRC),
    GateSpec("wave8_optfreq_all", r"opt_freq|optfreqsp|optfreq", SCOPE_CCCP),
    GateSpec(
        "docs_retired_map_only",
        r"Lowconfirm|Highconfirm",
        SCOPE_README,
        allow_line_pattern=RETIRED_MAP_LINE_PATTERN,
    ),
    GateSpec("frontend_removed_tokens", FRONTEND_REMOVED_TOKENS_PATTERN, SCOPE_FRONTEND),
    GateSpec("final_mechanism_imports", MECHANISM_IMPORT_PATTERN, SCOPE_SRC_TESTS),
    GateSpec(
        "pre_delete_mechanism_external",
        MECHANISM_IMPORT_PATTERN,
        SCOPE_SRC_TESTS,
        excluded_path_prefixes=SCOPE_MECHANISM,
    ),
    GateSpec(
        "final_stage_terms", FINAL_STAGE_PATTERN, SCOPE_ACP_FRONTEND,
        FINAL_STAGE_ALLOWED_PATHS, use_shared_exemptions=True,
    ),
    GateSpec(
        "final_optfreq_terms", FINAL_OPTFREQ_PATTERN, SCOPE_ACP_FRONTEND,
        FINAL_OPTFREQ_ALLOWED_PATHS, use_shared_exemptions=True,
    ),
    GateSpec(
        "final_forbidden_symbols",
        FINAL_FORBIDDEN_PATTERN,
        SCOPE_ACP,
        FINAL_FORBIDDEN_ALLOWED_PATHS,
        COMMENT_LINE_PATTERN,
    ),
    GateSpec(
        "final_shermo", r"run_shermo", SCOPE_SRC, FINAL_SHERMO_ALLOWED_PATHS,
        COMMENT_LINE_PATTERN,
    ),
    GateSpec(
        "unique_run_scan", r"^def run_scan\(", SCOPE_RUN_PRIMITIVE, PRIMITIVE_SCAN_ALLOWED_PATHS,
        excluded_path_prefixes=SCOPE_WORKFLOWS,
        symbol_pattern=r"run_scan",
    ),
    GateSpec(
        "unique_run_irc", r"^def run_irc\(", SCOPE_RUN_PRIMITIVE, PRIMITIVE_IRC_ALLOWED_PATHS,
        excluded_path_prefixes=SCOPE_WORKFLOWS,
        symbol_pattern=r"run_irc",
    ),
    GateSpec(
        "calculations_no_mechanism_terms",
        CALCULATIONS_TERMS_PATTERN,
        SCOPE_CALCULATIONS,
        allow_line_pattern=COMMENT_LINE_PATTERN,
    ),
    GateSpec(
        "cccp_imports_acp",
        CCCP_IMPORTS_ACP_PATTERN,
        SCOPE_CCCP,
        allow_line_pattern=COMMENT_LINE_PATTERN,
        symbol_pattern=CCCP_IMPORTS_ACP_PATTERN,
        file_suffixes=PY_ONLY_SUFFIXES,
    ),
    GateSpec(
        "workflow_executes_qc",
        WORKFLOW_EXECUTES_QC_PATTERN,
        SCOPE_WORKFLOWS_CONFSEARCH_NMR_CALC,
        symbol_pattern=WORKFLOW_EXECUTES_QC_PATTERN,
        file_suffixes=PY_ONLY_SUFFIXES,
    ),
    GateSpec(
        "workflow_route_assembly",
        WORKFLOW_ROUTE_ASSEMBLY_PATTERN,
        SCOPE_WORKFLOWS_CONFSEARCH,
        symbol_pattern=r'"! "',
        file_suffixes=PY_ONLY_SUFFIXES,
    ),
    GateSpec(
        "backend_imports_task_module",
        BACKEND_TASK_IMPORT_PATTERN,
        BACKEND_CAPABILITY_SCOPES,
        symbol_pattern=r"cccp\.calculation(?:\.\w+)*|run_singlepoint|run_optimize|run_thermochemistry",
        ast_check="backend_task_imports",
        file_suffixes=PY_ONLY_SUFFIXES,
    ),
    GateSpec(
        "compat_forwarders_are_pure",
        COMPAT_FORWARDER_HINT_PATTERN,
        COMPAT_FORWARDER_SCOPES,
        ast_check="pure_forwarder",
        grep_layer=False,
        file_suffixes=PY_ONLY_SUFFIXES,
    ),
    GateSpec(
        "legacy_batch_quarantine",
        LEGACY_BATCH_IMPORT_PATTERN,
        SCOPE_ACP,
        excluded_path_prefixes=LEGACY_BATCH_EXCLUDED_PATHS,
        symbol_map=(
            ("from acp.backends import batch", LEGACY_BATCH_SYMBOL),
            ("from acp.backends.batch", LEGACY_BATCH_SYMBOL),
            ("import acp.backends.batch", LEGACY_BATCH_SYMBOL),
        ),
        ast_check="legacy_batch",
        file_suffixes=PY_ONLY_SUFFIXES,
    ),
)

GATE_NAMES: Final[tuple[str, ...]] = tuple(gate.name for gate in GATE_REGISTRY)
_GATES_BY_NAME: Final[dict[str, GateSpec]] = {gate.name: gate for gate in GATE_REGISTRY}
_EX_LINE_RE: Final[re.Pattern[str]] = re.compile(EX_LINE_PATTERN)


def _get_gate(name: str) -> GateSpec:
    gate = _GATES_BY_NAME.get(name)
    if gate is None:
        raise GateInputError(f"unknown gate: {name}")
    return gate


def _path_matches(path: str, prefix: str) -> bool:
    normalized_prefix = prefix.rstrip("/")
    return path == normalized_prefix or path.startswith(f"{normalized_prefix}/")


def _line_is_allowed(gate: GateSpec, relative_path: str, line: str) -> bool:
    if any(_path_matches(relative_path, prefix) for prefix in gate.allow_path_prefixes):
        return True
    if gate.allow_line_pattern is not None and re.search(gate.allow_line_pattern, line):
        return True
    if not gate.use_shared_exemptions:
        return False
    if any(_path_matches(relative_path, prefix) for prefix in EX_PATH_PREFIXES):
        return True
    return _EX_LINE_RE.search(line) is not None


def _try_parse(text: str) -> ast.Module | None:
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_string_expr(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _ast_check_backend_task_imports(text: str) -> tuple[tuple[int, str], ...]:
    tree = _try_parse(text)
    if tree is None:
        return ()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module
            if module == "cccp.calculation":
                for alias in node.names:
                    if alias.name not in {"errors", "contracts"}:
                        out.append((node.lineno, alias.name))
            elif module.startswith("cccp.calculation.") and module not in PURE_CCCP_CALCULATION_MODULES:
                out.append((node.lineno, module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cccp.calculation" or (
                    alias.name.startswith("cccp.calculation.")
                    and alias.name not in PURE_CCCP_CALCULATION_MODULES
                ):
                    out.append((node.lineno, alias.name))
        elif isinstance(node, ast.Attribute) and node.attr in EXEC_TASK_SYMBOLS:
            out.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and node.id in EXEC_TASK_SYMBOLS:
            out.append((node.lineno, node.id))
    return tuple(out)


def _collect_forbidden_calls(
    node: ast.AST, allow_delegation_passthrough: bool, out: list[tuple[int, str]]
) -> None:
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        name = _callee_name(sub.func)
        if name is None or name not in FORWARDER_FORBIDDEN_CALLS:
            continue
        if (
            allow_delegation_passthrough
            and name in FORWARDER_DELEGATION_OK_CALLS
            and isinstance(sub.func, ast.Attribute)
        ):
            continue
        out.append((sub.lineno, name))


def _ast_check_pure_forwarder(text: str) -> tuple[tuple[int, str], ...]:
    tree = _try_parse(text)
    if tree is None:
        return ()
    out: list[tuple[int, str]] = []
    for node in tree.body:
        if _is_string_expr(node):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                    out.append((node.lineno, "subprocess"))
            continue
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "subprocess" or module.startswith("subprocess."):
                out.append((node.lineno, "subprocess"))
            continue
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if targets == ["__all__"] and isinstance(node.value, (ast.List, ast.Tuple)) and all(
                isinstance(element, ast.Constant) and isinstance(element.value, str)
                for element in node.value.elts
            ):
                continue
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, (ast.Name, ast.Attribute))
            ):
                continue
            out.append((node.lineno, targets[0] if targets else "assignment"))
            _collect_forbidden_calls(node, False, out)
            continue
        if isinstance(node, ast.FunctionDef):
            body = [item for item in node.body if not _is_string_expr(item)]
            if not body:
                continue
            if len(body) == 1 and isinstance(body[0], ast.Return) and body[0].value is not None:
                if isinstance(body[0].value, (ast.Call, ast.Attribute, ast.Name, ast.Subscript)):
                    _collect_forbidden_calls(node, True, out)
                    continue
            out.append((node.lineno, f"non-pure-def:{node.name}"))
            _collect_forbidden_calls(node, False, out)
            continue
        out.append((node.lineno, f"non-pure-statement:{type(node).__name__}"))
        _collect_forbidden_calls(node, False, out)
    return tuple(out)


def _ast_check_legacy_batch(text: str) -> tuple[tuple[int, str], ...]:
    tree = _try_parse(text)
    if tree is None:
        return ()
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "acp.backends" and any(alias.name == "batch" for alias in node.names):
                out.append((node.lineno, LEGACY_BATCH_SYMBOL))
            elif module == LEGACY_BATCH_SYMBOL or module.startswith(f"{LEGACY_BATCH_SYMBOL}."):
                out.append((node.lineno, LEGACY_BATCH_SYMBOL))
            for alias in node.names:
                if alias.name == LEGACY_BATCH_EXEC_SYMBOL:
                    out.append((node.lineno, LEGACY_BATCH_EXEC_SYMBOL))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == LEGACY_BATCH_SYMBOL or alias.name.startswith(f"{LEGACY_BATCH_SYMBOL}."):
                    out.append((node.lineno, LEGACY_BATCH_SYMBOL))
        elif isinstance(node, ast.Attribute) and node.attr == LEGACY_BATCH_EXEC_SYMBOL:
            out.append((node.lineno, LEGACY_BATCH_EXEC_SYMBOL))
        elif isinstance(node, ast.Name) and node.id == LEGACY_BATCH_EXEC_SYMBOL:
            out.append((node.lineno, LEGACY_BATCH_EXEC_SYMBOL))
    return tuple(out)


_AST_CHECKS: Final[dict[str, Callable[[str], tuple[tuple[int, str], ...]]]] = {
    "backend_task_imports": _ast_check_backend_task_imports,
    "pure_forwarder": _ast_check_pure_forwarder,
    "legacy_batch": _ast_check_legacy_batch,
}


def _grep_symbol(gate: GateSpec, line: str, matched: str) -> str:
    if gate.symbol_pattern is not None:
        symbol_match = re.search(gate.symbol_pattern, line)
        if symbol_match is not None:
            matched = symbol_match.group(0)
    return dict(gate.symbol_map).get(matched, matched)


def classify_text(gate_name: str, relative_path: str, text: str) -> tuple[Finding, ...]:
    """Classify pattern hits in text without touching the filesystem."""
    gate = _get_gate(gate_name)
    normalized_path = relative_path.replace("\\", "/").removeprefix("./")
    if any(_path_matches(normalized_path, prefix) for prefix in gate.excluded_path_prefixes):
        return ()
    findings: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()
    lines = text.splitlines()

    def _add(line_number: int, symbol: str) -> None:
        key = (normalized_path, line_number, symbol)
        if key in seen:
            return
        seen.add(key)
        line = lines[line_number - 1] if 0 < line_number <= len(lines) else ""
        findings.append(
            Finding(normalized_path, line_number, line, _line_is_allowed(gate, normalized_path, line), symbol)
        )

    if gate.grep_layer:
        pattern = re.compile(gate.pattern)
        for line_number, line in enumerate(lines, start=1):
            match = pattern.search(line)
            if match is not None:
                _add(line_number, _grep_symbol(gate, line, match.group(0)))
    if gate.ast_check is not None:
        checker = _AST_CHECKS[gate.ast_check]
        for line_number, symbol in checker(text):
            _add(line_number, symbol)
    return tuple(findings)


def _resolve_input_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _is_scanable_source(path: Path) -> bool:
    """True for text source files; excludes binary bytecode caches."""
    if "__pycache__" in path.parts:
        return False
    return path.suffix in {".py", ".pyi", ".json", ".md", ".html", ".js", ".ts", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini", ".sh", ".lsf", ".inp", ".xyz"}


def _input_files(raw_paths: Sequence[str], suffixes: tuple[str, ...] | None = None) -> tuple[Path, ...]:
    files: set[Path] = set()
    for raw_path in raw_paths:
        path = _resolve_input_path(raw_path)
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(
                child.resolve()
                for child in path.rglob("*")
                if child.is_file()
                and _is_scanable_source(child)
                and (suffixes is None or child.suffix in suffixes)
            )
        else:
            raise FileNotFoundError(path)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def _relative_path(path: Path, root: Path = REPO_ROOT) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def classify_paths(
    gate_name: str, raw_paths: Sequence[str], root: Path = REPO_ROOT
) -> tuple[Finding, ...]:
    """Classify all matching lines in the supplied files and directories."""
    gate = _get_gate(gate_name)
    findings: list[Finding] = []
    for path in _input_files(raw_paths, gate.file_suffixes):
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(classify_text(gate_name, _relative_path(path, root), text))
    return tuple(findings)


def _format_finding(finding: Finding) -> str:
    return f"{finding.path}:{finding.line_number}: {finding.text}"


# ── architecture-remediation suite (todo 4) ────────────────────────────────
# Suite gates owned/updated by the acp→cccp remediation plus the four frozen
# pins.  Historical legacy gates are NOT part of this suite: they are listed
# separately below and stay on their own case (never widen exemptions to
# green them).
ARCHITECTURE_SUITE_NAME: Final[str] = "architecture-remediation"
ARCHITECTURE_SUITE_GATES: Final[tuple[str, ...]] = (
    "cccp_imports_acp",
    "workflow_executes_qc",
    "workflow_route_assembly",
    "backend_imports_task_module",
    "compat_forwarders_are_pure",
    "legacy_batch_quarantine",
    "unique_run_scan",
    "unique_run_irc",
    "wave2_shermo_external",
    "final_shermo",
)
SUITE_REGISTRY: Final[dict[str, tuple[str, ...]]] = {
    ARCHITECTURE_SUITE_NAME: ARCHITECTURE_SUITE_GATES,
}
HISTORICAL_GATE_NAMES: Final[tuple[str, ...]] = tuple(
    name for name in GATE_NAMES if name not in ARCHITECTURE_SUITE_GATES
)

# Wave-0 violation allowlist: stable (rule, module, symbol) triples — NOT
# file:line.  Semantics: only blocking hits are tracked; a gate-level allowance
# (e.g. COMMENT_LINE_PATTERN) that stops a hit from blocking makes its entry
# stale and the entry must be removed in the same todo.  The allowlist only
# ever shrinks (tests pin both the entry set and the count): a new violation
# must be fixed, never allowlisted.
ARCHITECTURE_ALLOWLIST: Final[tuple[tuple[str, str, str], ...]] = (
    ("workflow_executes_qc", "src/acp/calculations/batch/singlepoint.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/calculations/executor.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/calculations/pes/scan.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/calculations/primitives/_common.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/confsearch/protocols/xtb_md.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/energy.py", "CensoBackend"),
    ("workflow_executes_qc", "src/acp/workflows/energy.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/energy_shared.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/energy_shared.py", "run_shermo"),
    ("workflow_executes_qc", "src/acp/workflows/ensemble.py", "CensoBackend"),
    ("workflow_executes_qc", "src/acp/workflows/ensemble.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/nmr.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/orca_gradient.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/xtbmd_censo_energy.py", "CensoBackend"),
    ("workflow_executes_qc", "src/acp/workflows/xtbmd_censo_energy.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/xtbmd_md.py", "get_backend"),
    ("workflow_executes_qc", "src/acp/workflows/xtb_path.py", "get_backend"),
    ("workflow_route_assembly", "src/acp/confsearch/shared/helpers.py", '"! "'),
    ("workflow_route_assembly", "src/acp/workflows/energy_shared.py", '"! "'),
)

ALLOWLIST_TRIPLES: Final[frozenset[tuple[str, str, str]]] = frozenset(ARCHITECTURE_ALLOWLIST)


def run_suite(suite_name: str, root: Path) -> int:
    """Run the remediation suite gates and aggregate the exit code."""
    gate_names = SUITE_REGISTRY[suite_name]
    print(f"== suite: {suite_name} (root: {root}) ==")
    observed_blocking: set[tuple[str, str, str]] = set()
    blocking_count = 0
    allowlisted_count = 0
    for gate_name in gate_names:
        gate = _get_gate(gate_name)
        raw_paths = [str(root / scope) for scope in gate.scope_paths if (root / scope).exists()]
        findings = classify_paths(gate_name, raw_paths, root=root) if raw_paths else ()
        print(f"-- gate: {gate_name} --")
        print("ALLOWED:")
        for finding in findings:
            if finding.allowed:
                print(_format_finding(finding))
        print("ALLOWLISTED (wave-0 baseline, must only shrink):")
        for finding in findings:
            key = (gate_name, finding.path, finding.symbol)
            if finding.allowed:
                continue
            observed_blocking.add(key)
            if key in ALLOWLIST_TRIPLES:
                allowlisted_count += 1
                print(f"{_format_finding(finding)} [{gate_name}|{finding.path}|{finding.symbol}]")
        print("BLOCKING:")
        for finding in findings:
            key = (gate_name, finding.path, finding.symbol)
            if not finding.allowed and key not in ALLOWLIST_TRIPLES:
                blocking_count += 1
                print(f"{_format_finding(finding)} [{gate_name}|{finding.path}|{finding.symbol}]")
    stale = sorted(key for key in ARCHITECTURE_ALLOWLIST if key not in observed_blocking)
    print("STALE ALLOWLIST ENTRIES (violations gone — remove the entry in the same todo):")
    for rule, module, symbol in stale:
        print(f"{rule}|{module}|{symbol}")
    print("HISTORICAL GATES (separate case, excluded from this suite — never widen exemptions):")
    for name in HISTORICAL_GATE_NAMES:
        print(name)
    print("== summary ==")
    print(f"gates run: {len(gate_names)}")
    print(f"allowlisted findings: {allowlisted_count}")
    print(f"blocking findings: {blocking_count}")
    print(f"stale allowlist entries: {len(stale)}")
    return int(blocking_count > 0 or bool(stale))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a frozen read-only architecture grep gate.")
    choices = parser.add_mutually_exclusive_group(required=True)
    _actions = (
        choices.add_argument(
            "--list-gates", action="store_true", help="list registered gate names"
        ),
        choices.add_argument(
            "--gate", nargs="+", metavar="VALUE", help="gate name followed by paths"
        ),
        choices.add_argument(
            "--suite",
            choices=sorted(SUITE_REGISTRY),
            help="run a named gate suite against its default scopes",
        ),
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        help="repository root used for scanning and relative paths (default: this repo)",
    )
    return parser


def _parse_arguments(argv: Sequence[str] | None) -> ParsedArguments:
    namespace = _build_parser().parse_args(argv)
    list_gates = getattr(namespace, "list_gates", None)
    gate = getattr(namespace, "gate", None)
    suite = getattr(namespace, "suite", None)
    root = getattr(namespace, "root", None)
    if not isinstance(list_gates, bool):
        raise GateInputError("argparse produced an invalid --list-gates value")
    if suite is not None and not isinstance(suite, str):
        raise GateInputError("argparse produced an invalid --suite value")
    if root is not None and not isinstance(root, str):
        raise GateInputError("argparse produced an invalid --root value")
    if gate is None:
        return ParsedArguments(list_gates, None, suite, root)
    if not isinstance(gate, list):
        raise GateInputError("argparse produced an invalid --gate value")
    gate_values: list[str] = []
    for value in gate:
        if not isinstance(value, str):
            raise GateInputError("argparse produced a non-string gate argument")
        gate_values.append(value)
    return ParsedArguments(list_gates, tuple(gate_values), suite, root)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested gate and return zero only when no hit is blocking."""
    args = _parse_arguments(argv)
    if args.list_gates:
        for name in GATE_NAMES:
            print(name)
        return 0

    root = Path(args.root).resolve() if args.root is not None else REPO_ROOT
    if args.suite is not None:
        return run_suite(args.suite, root)

    if args.gate_and_paths is None or len(args.gate_and_paths) < 2:
        print("--gate requires a gate name and at least one path", file=sys.stderr)
        return 2
    gate_name, *raw_paths = args.gate_and_paths
    try:
        findings = classify_paths(gate_name, raw_paths, root=root)
    except (FileNotFoundError, OSError) as exc:
        print(f"cannot scan gate inputs: {exc}", file=sys.stderr)
        return 2
    except GateInputError as exc:
        print(exc.message, file=sys.stderr)
        return 2

    print("ALLOWED:")
    for finding in findings:
        if finding.allowed:
            print(_format_finding(finding))
    print("BLOCKING:")
    for finding in findings:
        if not finding.allowed:
            print(_format_finding(finding))
    return int(any(not finding.allowed for finding in findings))


if __name__ == "__main__":
    raise SystemExit(main())
