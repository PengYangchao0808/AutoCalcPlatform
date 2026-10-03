from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from scripts.check_grep_gates import (
    ARCHITECTURE_ALLOWLIST,
    ARCHITECTURE_SUITE_GATES,
    HISTORICAL_GATE_NAMES,
    classify_text,
)

SCRIPT: Final[Path] = Path(__file__).parents[1] / "scripts" / "check_grep_gates.py"
SRC_ROOT: Final[Path] = Path(__file__).parents[1] / "src"
EXPECTED_GATE_NAMES: Final[tuple[str, ...]] = (
    "compat_no_writers",
    "wave2_optfreq",
    "wave2_shermo_external",
    "wave2_no_result_summary",
    "wave3_confirmengine",
    "wave3_no_batchmanifest",
    "wave3_no_legacy_manifests",
    "wave3_batch_no_stages",
    "wave4_endpointprovider",
    "wave4_confirm_no_irc",
    "wave4_batch_no_irc",
    "wave5_s2manifest",
    "wave5_layout_imports",
    "wave6",
    "wave6_scheduler_mechanism",
    "wave6_zero",
    "wave7_no_db_writes",
    "wave8_confsearch_decoupled",
    "wave8_cccp_engine_gone",
    "wave8_engine_import_gone",
    "wave8_optfreq_all",
    "docs_retired_map_only",
    "frontend_removed_tokens",
    "final_mechanism_imports",
    "pre_delete_mechanism_external",
    "final_stage_terms",
    "final_optfreq_terms",
    "final_forbidden_symbols",
    "final_shermo",
    "unique_run_scan",
    "unique_run_irc",
    "calculations_no_mechanism_terms",
    "cccp_imports_acp",
    "workflow_executes_qc",
    "workflow_route_assembly",
    "backend_imports_task_module",
    "compat_forwarders_are_pure",
    "legacy_batch_quarantine",
)
WAVE6_SCHEDULER_SYMBOLS: Final[tuple[str, ...]] = (
    "BOND_SCAN_STAGES",
    "resolve_study_layout",
    "find_study_layout",
    "find_reaction_json",
    "copy_handoff_payload",
    "stage_batch_request",
    "prepare_stage_batch_config",
    "pessearch_method_flags",
    "lowconfirm_method_flags",
    "highconfirm_method_flags",
    "mechanism_method_flags",
    "mechanism_resolved_settings",
    "write_mechanism_job_config",
    "write_mechanism_reaction_json",
    "validate_stage_artifact",
    "MechanismProjectStore",
    "mechanism_config",
    "mechanism_reaction",
    "s2_path_manifest",
    "s3_lowconfirm_manifest",
    "s4_highconfirm_manifest",
    "_mechanism_role_source",
    "materialized_role_paths",
    "MECHANISM_CONFIG_FILENAME",
    "--mechanism-config",
    'wf == "mechanism"',
)
WAVE7_DB_WRITE_PATTERNS: Final[tuple[str, ...]] = (
    "upsert_mechanism_study",
    "update_mechanism_study_reaction",
    "update_mechanism_study_plan",
    "upsert_decision_point",
    "INSERT INTO mechanism_",
    "UPDATE mechanism_",
    "MechanismProjectStore(",
)


def _statuses(gate_name: str, relative_path: str, text: str) -> tuple[bool, ...]:
    return tuple(finding.allowed for finding in classify_text(gate_name, relative_path, text))


@pytest.mark.parametrize(
    ("gate_name", "relative_path", "text", "expected"),
    (
        ("wave2_shermo_external", "src/acp/backends/external_backend.py", "run_shermo()", (False,)),
        ("wave5_s2manifest", "src/acp/workflows/legacy.py", "# s2_path_manifest", (True,)),
        ("wave2_optfreq", "src/cccp/qc/interfaces/base.py", "opt_freq()", (True,)),
        ("wave6", "src/acp/catalog.py", '    "dft_optfreq": {', (True,)),
        (
            "pre_delete_mechanism_external",
            "src/acp/mechanism/internal.py",
            "from acp" + ".mechanism import models",
            (),
        ),
        ("docs_retired_map_only", "README.md", "Lowconfirm →", (True,)),
    ),
)
def test_six_fixture_line_classes(
    gate_name: str, relative_path: str, text: str, expected: tuple[bool, ...]
) -> None:
    assert _statuses(gate_name, relative_path, text) == expected


def test_list_gates_matches_authoritative_names() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--list-gates"],
        capture_output=True,
        text=True,
        check=False,
    )

    names = tuple(result.stdout.splitlines())
    assert result.returncode == 0
    assert len(names) == 38
    assert names == EXPECTED_GATE_NAMES


@pytest.mark.parametrize("pattern", WAVE7_DB_WRITE_PATTERNS)
def test_wave7_no_db_writes_intercepts_every_frozen_pattern(pattern: str) -> None:
    assert _statuses("wave7_no_db_writes", "src/acp/api/v1_routes.py", pattern) == (False,)


def test_wave8_optfreq_all_intercepts_calc_type_line() -> None:
    assert _statuses(
        "wave8_optfreq_all", "src/cccp/qc/interfaces/base.py", 'calc_type == "optfreq"'
    ) == (False,)


@pytest.mark.parametrize("symbol", WAVE6_SCHEDULER_SYMBOLS)
def test_wave6_scheduler_mechanism_intercepts_every_frozen_symbol(symbol: str) -> None:
    assert _statuses("wave6_scheduler_mechanism", "src/acp/scheduler/job.py", symbol) == (False,)


@pytest.mark.parametrize(
    "line",
    (
        "from acp" + ".mechanism import models",
        "import acp" + ".mechanism",
        "x = acp" + ".mechanism.models",
        "from acp" + " import mechanism",
    ),
)
def test_final_mechanism_imports_intercepts_all_four_valid_forms(line: str) -> None:
    assert _statuses("final_mechanism_imports", "src/acp/workflows/example.py", line) == (False,)


def test_pre_delete_mechanism_external_allows_internal_self_references_only() -> None:
    assert (
        _statuses(
            "pre_delete_mechanism_external",
            "src/acp/mechanism/stages/confirm.py",
            "from acp" + ".mechanism import models",
        )
        == ()
    )
    assert _statuses(
        "pre_delete_mechanism_external",
        "src/acp/workflows/example.py",
        "from acp" + ".mechanism import models",
    ) == (False,)


@pytest.mark.parametrize(
    ("gate_name", "line", "expected"),
    (
        ("wave6", '"dft_optfreq": {', True),
        ("wave6", '"id": "optfreq"', True),
        ("wave6", "Lowconfirm()", False),
        ("final_stage_terms", '"id": "Lowconfirm"', True),
        ("final_stage_terms", '"label": "Highconfirm"', True),
        ("final_stage_terms", "Lowconfirm()", False),
        ("final_optfreq_terms", '"dft_optfreq": {', True),
        ("final_optfreq_terms", '"id": "optfreq"', True),
        ("final_optfreq_terms", "run_optfreq()", False),
    ),
)
def test_historical_schema_keys_are_exempt_but_active_calls_block(
    gate_name: str, line: str, expected: bool
) -> None:
    assert _statuses(gate_name, "src/acp/workflows/example.py", line) == (expected,)


def test_final_forbidden_symbols_ignore_only_comments_not_retired_text() -> None:
    assert _statuses(
        "final_forbidden_symbols", "src/acp/workflows/example.py", "StudyOrchestrator()  # retired"
    ) == (False,)
    assert _statuses(
        "final_forbidden_symbols", "src/acp/workflows/example.py", "# StudyOrchestrator"
    ) == (True,)


@pytest.mark.parametrize(
    ("gate_name", "relative_path", "text", "expected"),
    (
        # wave3_batch_no_stages: legacy plumbing names are ALLOWED
        (
            "wave3_batch_no_stages",
            "src/acp/calculations/batch/loaders.py",
            "    items.extend(load_items_from_s3_manifest((base_dir / artifact).resolve())[0])",
            (True,),
        ),
        # wave3_batch_no_stages: genuine stage semantics still BLOCK
        (
            "wave3_batch_no_stages",
            "src/acp/calculations/batch/engine.py",
            "profile_s3 = True",
            (False,),
        ),
        (
            "wave3_batch_no_stages",
            "src/acp/calculations/batch/engine.py",
            "stage_s4 = 'high'",
            (False,),
        ),
        # wave4_batch_no_irc: schema-rejection guard is ALLOWED
        (
            "wave4_batch_no_irc",
            "src/acp/calculations/batch/loaders.py",
            '    if "irc" in raw:',
            (True,),
        ),
        # wave4_batch_no_irc: genuine irc execution still BLOCK
        (
            "wave4_batch_no_irc",
            "src/acp/calculations/batch/engine.py",
            "backend.irc(coords, symbols)",
            (False,),
        ),
        (
            "wave4_batch_no_irc",
            "src/acp/calculations/batch/engine.py",
            "result.irc_products = []",
            (False,),
        ),
        (
            "wave4_batch_no_irc",
            "src/acp/calculations/batch/engine.py",
            "run_irc(request)",
            (False,),
        ),
    ),
)
def test_wave3_wave4_batch_gate_exemptions_have_teeth(
    gate_name: str, relative_path: str, text: str, expected: tuple[bool, ...]
) -> None:
    assert _statuses(gate_name, relative_path, text) == expected


@pytest.mark.parametrize(
    ("gate_name", "relative_path", "text", "expected"),
    (
        # wave5_s2manifest: mechanism/ is ALLOWED (D9 legacy, Wave-8 deletion)
        (
            "wave5_s2manifest",
            "src/acp/mechanism/stages/pes_search.py",
            '    payload["s2_path_manifest"] = manifest_path',
            (True,),
        ),
        (
            "wave5_s2manifest",
            "src/acp/mechanism/bond_scan.py",
            "    s2_candidate_manifest = {}",
            (True,),
        ),
        # wave5_s2manifest: api/ is STILL BLOCKING (Wave-7 cleanup)
        (
            "wave5_s2manifest",
            "src/acp/api/v1_routes.py",
            "    manifest = read_s2_path_manifest(path)",
            (False,),
        ),
        # wave5_s2manifest: calculations/ is STILL BLOCKING (new code)
        (
            "wave5_s2manifest",
            "src/acp/calculations/pes/engine.py",
            "    s2_path_manifest = {}",
            (False,),
        ),
    ),
)
def test_wave5_s2manifest_mechanism_allowed_api_still_blocking(
    gate_name: str, relative_path: str, text: str, expected: tuple[bool, ...]
) -> None:
    assert _statuses(gate_name, relative_path, text) == expected


def test_cli_prints_both_sections_and_blocks_a_zero_gate(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.py"
    _ = fixture.write_text("run_shermo()\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--gate", "wave2_shermo_external", str(fixture)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "ALLOWED:" in result.stdout
    assert "BLOCKING:" in result.stdout
    assert "run_shermo()" in result.stdout


# ── architecture-remediation suite (todo 4) ────────────────────────────────

WAVE0_ALLOWLIST_BASELINE: Final[frozenset[tuple[str, str, str]]] = frozenset(
    {
        ("cccp_imports_acp", "src/cccp/core/protocols.py", "from acp"),
        ("cccp_imports_acp", "src/cccp/qc/interfaces/orca.py", "from acp"),
        ("final_shermo", "src/acp/scheduler/capabilities.py", "run_shermo"),
        (
            "legacy_batch_quarantine",
            "src/acp/calculations/batch/_singlepoint_execution.py",
            "acp.backends.batch",
        ),
        (
            "legacy_batch_quarantine",
            "src/acp/calculations/batch/_singlepoint_execution.py",
            "batch_single_point",
        ),
        ("unique_run_scan", "src/acp/workflows/simple.py", "run_scan"),
        ("workflow_executes_qc", "src/acp/calculations/batch/singlepoint.py", "get_backend"),
        ("workflow_executes_qc", "src/acp/calculations/executor.py", "get_backend"),
        ("workflow_executes_qc", "src/acp/calculations/pes/scan.py", "get_backend"),
        ("workflow_executes_qc", "src/acp/calculations/primitives/_common.py", "get_backend"),
        (
            "workflow_executes_qc",
            "src/acp/calculations/primitives/thermochemistry.py",
            "run_shermo",
        ),
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
    }
)
ALLOWLIST_COUNT_PIN: Final[int] = 24
CAPABILITY_MODULE_FILES: Final[tuple[str, ...]] = (
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
    "src/cccp/backends/",
)
PURE_CCCP_CALCULATION_SURFACE: Final[tuple[str, ...]] = (
    "cccp.calculation.errors",
    "cccp.calculation.contracts",
)
WORKFLOW_PROBE_SCOPES: Final[tuple[str, ...]] = (
    "src/acp/workflows/",
    "src/acp/confsearch/",
    "src/acp/nmr/",
    "src/acp/calculations/",
)
WORKFLOW_PROBE_CALLS: Final[frozenset[str]] = frozenset(
    {"get_backend", "require_backend", "run_shermo", "CensoBackend"}
)


@dataclass(frozen=True)
class DependencyRule:
    scope: tuple[str, ...]
    forbidden_prefixes: tuple[str, ...] = ()
    forbidden_exceptions: tuple[str, ...] = ()
    allowed_prefixes: tuple[str, ...] = ()


DEPENDENCY_RULES: Final[tuple[DependencyRule, ...]] = (
    DependencyRule(scope=("src/cccp/",), forbidden_prefixes=("acp",)),
    DependencyRule(
        scope=CAPABILITY_MODULE_FILES,
        forbidden_prefixes=("cccp.calculation",),
        forbidden_exceptions=PURE_CCCP_CALCULATION_SURFACE,
    ),
    DependencyRule(
        scope=("src/acp/backends/__init__.py",),
        allowed_prefixes=("acp.backends", "typing", "collections.abc", "__future__"),
    ),
)


def _has_blocking(gate_name: str, relative_path: str, text: str) -> bool:
    return any(not finding.allowed for finding in classify_text(gate_name, relative_path, text))


def _module_package(module_path: str) -> str:
    rel = module_path.replace("\\", "/").removeprefix("src/").removesuffix(".py")
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    elif "/" in rel:
        rel = rel.rsplit("/", 1)[0]
    else:
        rel = ""
    return rel.replace("/", ".")


def _resolved_imports(module_path: str, source: str) -> frozenset[str]:
    package = _module_package(module_path)
    tree = ast.parse(source)
    resolved: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level > 0:
                parts = package.split(".") if package else []
                prefix = ".".join(parts[: len(parts) - (node.level - 1)])
                target = f"{prefix}.{node.module}" if node.module else prefix
            else:
                target = node.module or ""
            resolved.add(target)
        elif isinstance(node, ast.Import):
            resolved.update(alias.name for alias in node.names)
    return frozenset(resolved)


def _dependency_violations(rule: DependencyRule, module_path: str, source: str) -> frozenset[str]:
    def _matches_module(target: str, prefix: str) -> bool:
        return target == prefix or target.startswith(f"{prefix}.")

    def _matches_scope(path: str, scope: str) -> bool:
        normalized = scope.rstrip("/")
        return path == normalized or path.startswith(f"{normalized}/")

    if not any(_matches_scope(module_path, scope) for scope in rule.scope):
        return frozenset()
    bad: set[str] = set()
    for target in _resolved_imports(module_path, source):
        if rule.allowed_prefixes and not any(
            _matches_module(target, prefix) for prefix in rule.allowed_prefixes
        ):
            bad.add(target)
        if any(_matches_module(target, prefix) for prefix in rule.forbidden_prefixes) and (
            target not in rule.forbidden_exceptions
        ):
            bad.add(target)
    return frozenset(bad)


def _workflow_call_probe(source: str) -> frozenset[str]:
    tree = ast.parse(source)
    hits: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name is not None and name in WORKFLOW_PROBE_CALLS:
                hits.add(name)
    return frozenset(hits)


@dataclass(frozen=True)
class InjectionCase:
    name: str
    gate: str
    module: str
    symbol: str
    target: str
    snippet: str
    create: bool = False


INJECTION_CASES: Final[tuple[InjectionCase, ...]] = (
    InjectionCase(
        "reverse_import",
        "cccp_imports_acp",
        "src/cccp/_neg_inject.py",
        "from acp",
        "src/cccp/_neg_inject.py",
        "from acp import chem\n",
        create=True,
    ),
    InjectionCase(
        "second_qc_path",
        "workflow_executes_qc",
        "src/acp/workflows/simple.py",
        "get_backend",
        "src/acp/workflows/simple.py",
        '\n_probe_backend = get_backend("orca")\n',
    ),
    InjectionCase(
        "route_concatenation",
        "workflow_route_assembly",
        "src/acp/workflows/simple.py",
        '"! "',
        "src/acp/workflows/simple.py",
        '\n_probe_route_lines = ["! " + extra]\n',
    ),
    InjectionCase(
        "backend_to_task_call",
        "backend_imports_task_module",
        "src/acp/backends/base.py",
        "cccp.calculation.tasks",
        "src/acp/backends/base.py",
        "\nfrom cccp.calculation.tasks import run_singlepoint\n",
    ),
    InjectionCase(
        "forwarder_default_table",
        "compat_forwarders_are_pure",
        "src/acp/backends/__init__.py",
        "DEFAULT_GRID",
        "src/acp/backends/__init__.py",
        '\nDEFAULT_GRID = {"grid": "defgrid2"}\n',
    ),
    InjectionCase(
        "forwarder_qc_call",
        "compat_forwarders_are_pure",
        "src/acp/backends/__init__.py",
        "get_backend",
        "src/acp/backends/__init__.py",
        "\n_probe_orca = get_backend('orca')\n",
    ),
    InjectionCase(
        "legacy_batch_root_alias_import",
        "legacy_batch_quarantine",
        "src/acp/calculations/batch/engine.py",
        "acp.backends.batch",
        "src/acp/calculations/batch/engine.py",
        "\nfrom acp.backends import batch\n",
    ),
    InjectionCase(
        "legacy_batch_import_alias",
        "legacy_batch_quarantine",
        "src/acp/calculations/batch/engine.py",
        "acp.backends.batch",
        "src/acp/calculations/batch/engine.py",
        "\nimport acp.backends.batch as batch_backend\n",
    ),
    InjectionCase(
        "legacy_batch_multiline_import",
        "legacy_batch_quarantine",
        "src/acp/calculations/batch/engine.py",
        "acp.backends.batch",
        "src/acp/calculations/batch/engine.py",
        "\nfrom acp.backends import (\n    batch,\n)\n",
    ),
    InjectionCase(
        "legacy_batch_root_alias_attr_call",
        "legacy_batch_quarantine",
        "src/acp/calculations/batch/engine.py",
        "batch_single_point",
        "src/acp/calculations/batch/engine.py",
        "\nimport acp.backends as backends\n_probe_batch = backends.batch_single_point(1, 2)\n",
    ),
)


def _run_suite(root: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), "--suite", "architecture-remediation"]
    if root is not None:
        command += ["--root", str(root)]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _copy_src_tree(tmp_path: Path) -> Path:
    destination = tmp_path / "repo"
    shutil.copytree(SRC_ROOT, destination / "src", ignore=shutil.ignore_patterns("__pycache__"))
    return destination


def test_suite_gate_membership_is_disjoint_from_historical() -> None:
    assert set(ARCHITECTURE_SUITE_GATES) <= set(EXPECTED_GATE_NAMES)
    assert set(HISTORICAL_GATE_NAMES) == set(EXPECTED_GATE_NAMES) - set(ARCHITECTURE_SUITE_GATES)
    assert {
        "backend_imports_task_module",
        "compat_forwarders_are_pure",
        "legacy_batch_quarantine",
    } <= set(ARCHITECTURE_SUITE_GATES)
    assert "calculations_no_mechanism_terms" in HISTORICAL_GATE_NAMES


def test_suite_exits_zero_on_current_tree() -> None:
    result = _run_suite()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "blocking findings: 0" in result.stdout
    assert "stale allowlist entries: 0" in result.stdout
    assert "HISTORICAL GATES" in result.stdout


def test_suite_zero_control_on_temp_copy(tmp_path: Path) -> None:
    result = _run_suite(_copy_src_tree(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("case", INJECTION_CASES, ids=lambda case: case.name)
def test_suite_fails_when_violation_injected(case: InjectionCase, tmp_path: Path) -> None:
    root = _copy_src_tree(tmp_path)
    target = root / case.target
    if case.create:
        _ = target.write_text(case.snippet, encoding="utf-8")
    else:
        _ = target.write_text(target.read_text(encoding="utf-8") + case.snippet, encoding="utf-8")

    result = _run_suite(root)

    assert result.returncode == 1, f"injection {case.name} not detected:\n{result.stdout}"
    assert f"[{case.gate}|{case.module}|{case.symbol}]" in result.stdout


def test_suite_flags_stale_allowlist_entries_when_violation_disappears(tmp_path: Path) -> None:
    root = _copy_src_tree(tmp_path)
    (root / "src/acp/confsearch/shared/helpers.py").unlink()

    result = _run_suite(root)

    assert result.returncode == 1, result.stdout
    assert "stale allowlist entries: 1" in result.stdout
    assert "workflow_route_assembly|src/acp/confsearch/shared/helpers.py" in result.stdout


def test_allowlist_only_shrinks_from_wave0_baseline() -> None:
    assert set(ARCHITECTURE_ALLOWLIST) <= set(WAVE0_ALLOWLIST_BASELINE)


def test_allowlist_count_is_pinned() -> None:
    assert len(ARCHITECTURE_ALLOWLIST) == ALLOWLIST_COUNT_PIN
    assert len(set(ARCHITECTURE_ALLOWLIST)) == len(ARCHITECTURE_ALLOWLIST)


@pytest.mark.parametrize(
    ("relative_path", "text", "expected"),
    (
        ("src/cccp/core/protocols.py", "from acp.chem.composition import x", True),
        ("src/cccp/core/protocols.py", "import acp.catalog", True),
        ("src/cccp/qc/interfaces/orca.py", "    from acp.catalog import METHOD_META", True),
        ("src/cccp/core/protocols.py", "if TYPE_CHECKING:\n    from acp import chem", True),
        ("src/cccp/core/protocols.py", "# from acp import chem", False),
        ("src/cccp/core/protocols.py", "from cccp.utils import geometry", False),
    ),
)
def test_cccp_imports_acp_covers_lazy_and_type_checking_forms(
    relative_path: str, text: str, expected: bool
) -> None:
    assert _has_blocking("cccp_imports_acp", relative_path, text) is expected


@pytest.mark.parametrize(
    "text",
    (
        'backend = get_backend("orca")',
        "cls = require_backend('orca')",
        "raw = run_shermo(input_file, output_dir)",
        "backend = CensoBackend(cfg)",
        "# primitives resolve backends internally via get_backend().",
    ),
)
def test_workflow_executes_qc_blocks_all_four_execution_tokens(text: str) -> None:
    assert _has_blocking("workflow_executes_qc", "src/acp/workflows/example.py", text)


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ('lines = ["! " + " ".join(extras)]', True),
        ('route = ["! " + x] if x else []', True),
        ('route = ["! D4"]', False),
    ),
)
def test_workflow_route_assembly_blocks_concatenated_routes(text: str, expected: bool) -> None:
    assert _has_blocking("workflow_route_assembly", "src/acp/workflows/example.py", text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("from cccp.calculation.errors import CalculationSpec", False),
        ("from cccp.calculation.contracts import ResolvedSpec", False),
        ("from cccp.calculation import errors", False),
        ("from cccp.calculation import contracts", False),
        ("import cccp.calculation.errors", False),
        ("from cccp.calculation.tasks import run_singlepoint", True),
        ("import cccp.calculation.tasks as t", True),
        ("from cccp.calculation import run_singlepoint", True),
        ("from cccp.calculation.executor import CalculationPlanExecutor", True),
        ("result = run_thermochemistry(spec)", True),
        ("result = run_optimize(spec)", True),
    ),
)
def test_backend_imports_task_module_whitelists_only_pure_types(
    text: str, expected: bool
) -> None:
    assert _has_blocking("backend_imports_task_module", "src/acp/backends/base.py", text) is expected


def test_compat_forwarders_allow_reexports_and_pure_delegation() -> None:
    pure = (
        '"""Quantum chemistry backend abstraction layer."""\n'
        "from acp.backends.base import QCBackend\n"
        "from acp.backends.registry import get_backend, require_backend\n"
        'from .batch import batch_single_point\n'
        '__all__ = ["QCBackend", "get_backend", "require_backend", "batch_single_point"]\n'
    )
    assert not _has_blocking("compat_forwarders_are_pure", "src/acp/backends/__init__.py", pure)
    delegation = (
        "from acp.backends.registry import get_backend as _get_backend\n"
        "def get_backend(*args, **kwargs):\n"
        "    return _get_backend(*args, **kwargs)\n"
    )
    assert not _has_blocking("compat_forwarders_are_pure", "src/acp/backends/__init__.py", delegation)
    attribute_delegation = (
        "from acp.backends import registry as _registry\n"
        "def require_backend(*args, **kwargs):\n"
        "    return _registry.require_backend(*args, **kwargs)\n"
    )
    assert not _has_blocking(
        "compat_forwarders_are_pure", "src/acp/backends/__init__.py", attribute_delegation
    )


@pytest.mark.parametrize(
    "text",
    (
        'DEFAULT_GRID = {"grid": "defgrid2"}\n',
        "import subprocess\n",
        "from subprocess import run\n",
        'def acquire():\n    return get_backend("orca")\n',
        'result = get_backend("orca")\n',
        'result = run_shermo(input_file, output_dir)\n',
        'def run_all():\n    orca = get_backend("orca")\n    return orca\n',
    ),
)
def test_compat_forwarders_forbid_defaults_subprocess_and_qc_calls(text: str) -> None:
    assert _has_blocking("compat_forwarders_are_pure", "src/acp/backends/__init__.py", text)


@pytest.mark.parametrize(
    "text",
    (
        "from acp.backends import batch\n",
        "from acp.backends import batch as batch_backend\n",
        "import acp.backends.batch as batch_backend\n",
        "from acp.backends.batch import batch_single_point\n",
        "from acp.backends import (\n    batch,\n)\n",
        "import acp.backends as backends\nresult = backends.batch_single_point(1, 2)\n",
    ),
)
def test_legacy_batch_quarantine_covers_aliases_multiline_and_root_attr_calls(text: str) -> None:
    assert _has_blocking("legacy_batch_quarantine", "src/acp/calculations/batch/engine.py", text)


@pytest.mark.parametrize(
    ("relative_path", "text"),
    (
        ("src/acp/calculations/batch/engine.py", "from acp.backends import batch_process_thermo\n"),
        ("src/acp/calculations/batch/engine.py", "from acp.backends import BatchSpResult\n"),
        ("src/acp/backends/batch.py", "from acp.backends import batch\n"),
        ("src/acp/backends/__init__.py", "from acp.backends import batch as batch_backend\n"),
        ("src/acp/calculations/batch/engine.py", "from acp.backends.registry import get_backend\n"),
    ),
)
def test_legacy_batch_quarantine_false_positive_guards(relative_path: str, text: str) -> None:
    assert not _has_blocking("legacy_batch_quarantine", relative_path, text)


WAVE0_DEPENDENCY_EXCEPTIONS: Final[frozenset[tuple[str, str]]] = frozenset()
WAVE0_DEPENDENCY_COUNT_PIN: Final[int] = 0


def test_module_allowed_dependency_table_on_current_tree() -> None:
    violations: set[tuple[str, str]] = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        module_path = path.relative_to(SRC_ROOT.parent).as_posix()
        source = path.read_text(encoding="utf-8", errors="replace")
        for rule in DEPENDENCY_RULES:
            for target in _dependency_violations(rule, module_path, source):
                violations.add((module_path, target))
    assert violations <= set(WAVE0_DEPENDENCY_EXCEPTIONS), sorted(violations)
    assert len(violations) == WAVE0_DEPENDENCY_COUNT_PIN


@pytest.mark.parametrize(
    ("module_path", "text", "expected_bad"),
    (
        ("src/cccp/core/protocols.py", "from acp.chem.composition import x", {"acp.chem.composition"}),
        (
            "src/cccp/core/protocols.py",
            "if TYPE_CHECKING:\n    from acp import chem",
            {"acp"},
        ),
        (
            "src/acp/backends/base.py",
            "from cccp.calculation.tasks import run_singlepoint",
            {"cccp.calculation.tasks"},
        ),
        (
            "src/acp/backends/base.py",
            "import cccp.calculation",
            {"cccp.calculation"},
        ),
        ("src/acp/backends/base.py", "from cccp.calculation.errors import X", set()),
        ("src/acp/backends/base.py", "from cccp.calculation.contracts import Y", set()),
    ),
)
def test_module_allowed_dependency_table_detects_injected_violations(
    module_path: str, text: str, expected_bad: set[str]
) -> None:
    bad: set[str] = set()
    for rule in DEPENDENCY_RULES:
        bad |= _dependency_violations(rule, module_path, text)
    assert bad == expected_bad


def test_module_allowed_dependency_table_rejects_forwarder_non_reexport_imports() -> None:
    bad: set[str] = set()
    for rule in DEPENDENCY_RULES:
        bad |= _dependency_violations(
            rule, "src/acp/backends/__init__.py", "import subprocess\n"
        )
    assert bad == {"subprocess"}


def test_workflow_call_probes_are_covered_by_allowlist() -> None:
    allowlisted = {
        (module, symbol)
        for rule, module, symbol in ARCHITECTURE_ALLOWLIST
        if rule == "workflow_executes_qc"
    }
    uncovered: list[str] = []
    for scope in WORKFLOW_PROBE_SCOPES:
        for path in sorted((SRC_ROOT.parent / scope).rglob("*.py")):
            module_path = path.relative_to(SRC_ROOT.parent).as_posix()
            for symbol in _workflow_call_probe(path.read_text(encoding="utf-8", errors="replace")):
                if (module_path, symbol) not in allowlisted:
                    uncovered.append(f"{module_path}: {symbol}")
    assert uncovered == []


def test_workflow_call_probe_detects_injected_second_path() -> None:
    probe = _workflow_call_probe('_probe = get_backend("orca")\n')
    assert probe == frozenset({"get_backend"})
    assert ("src/acp/workflows/simple.py", "get_backend") not in {
        (module, symbol) for _rule, module, symbol in ARCHITECTURE_ALLOWLIST
    }


def test_cccp_qc_interfaces_eager_import_surface_has_no_acp_side_effects() -> None:
    probe = (
        "import sys\n"
        "class _Blocker:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        '        if name == "acp" or name.startswith("acp."):\n'
        '            raise ImportError(f"acp import blocked: {name}")\n'
        "        return None\n"
        "sys.meta_path.insert(0, _Blocker())\n"
        "import cccp.qc.interfaces\n"
        'assert "acp" not in sys.modules\n'
        'assert "cccp.qc.interfaces.orca" in sys.modules\n'
        'print("EAGER_OK")\n'
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EAGER_OK" in result.stdout


def test_cccp_calculation_pure_type_modules_stay_pure_when_present() -> None:
    forbidden = ("acp", "cccp.qc.interfaces", "cccp.qc.runners", "cccp.backends", "cccp.calculation.tasks")

    def _violations(path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        bad: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                targets = [node.module]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            for target in targets:
                if any(target == prefix or target.startswith(f"{prefix}.") for prefix in forbidden):
                    bad.append(target)
                if target.startswith("cccp.calculation.") and target not in PURE_CCCP_CALCULATION_SURFACE:
                    bad.append(target)
        return bad

    for name in ("errors.py", "contracts.py"):
        path = SRC_ROOT / "cccp" / "calculation" / name
        if not path.exists():
            continue
        assert _violations(path) == []
