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
import re
import sys
from collections.abc import Sequence
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
SCOPE_PRIMITIVE_SCAN: Final[tuple[str, ...]] = ("src/acp/calculations/primitives/scan.py",)
SCOPE_PRIMITIVE_IRC: Final[tuple[str, ...]] = ("src/acp/calculations/primitives/irc.py",)
SCOPE_MECHANISM: Final[tuple[str, ...]] = ("src/acp/mechanism/",)
RETIRED_MAP_LINE_PATTERN: Final[str] = r"退役|retired|→"


@dataclass(frozen=True, slots=True)
class GateSpec:
    name: str
    pattern: str
    scope_paths: tuple[str, ...]
    allow_path_prefixes: tuple[str, ...] = ()
    allow_line_pattern: str | None = None
    use_shared_exemptions: bool = False
    excluded_path_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line_number: int
    text: str
    allowed: bool


@dataclass(frozen=True, slots=True)
class ParsedArguments:
    list_gates: bool
    gate_and_paths: tuple[str, ...] | None


class GateInputError(Exception):
    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


GATE_REGISTRY: Final[tuple[GateSpec, ...]] = (
    GateSpec("compat_no_writers", r"def write_", ("src/acp/compat/",)),
    GateSpec("wave2_optfreq", r"opt_freq\(", SCOPE_SRC, WAVE2_OPTFREQ_ALLOWED_PATHS),
    GateSpec("wave2_shermo_external", r"run_shermo", ("src/acp/backends/external_backend.py",)),
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
        "final_stage_terms", FINAL_STAGE_PATTERN, SCOPE_ACP_FRONTEND, use_shared_exemptions=True
    ),
    GateSpec(
        "final_optfreq_terms", FINAL_OPTFREQ_PATTERN, SCOPE_ACP_FRONTEND, use_shared_exemptions=True
    ),
    GateSpec(
        "final_forbidden_symbols",
        FINAL_FORBIDDEN_PATTERN,
        SCOPE_ACP,
        FINAL_FORBIDDEN_ALLOWED_PATHS,
        COMMENT_LINE_PATTERN,
    ),
    GateSpec("final_shermo", r"run_shermo", SCOPE_SRC, FINAL_SHERMO_ALLOWED_PATHS),
    GateSpec("unique_run_scan", r"^def run_scan\(", SCOPE_ACP, SCOPE_PRIMITIVE_SCAN),
    GateSpec("unique_run_irc", r"^def run_irc\(", SCOPE_ACP, SCOPE_PRIMITIVE_IRC),
    GateSpec(
        "calculations_no_mechanism_terms",
        CALCULATIONS_TERMS_PATTERN,
        SCOPE_CALCULATIONS,
        allow_line_pattern=COMMENT_LINE_PATTERN,
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


def classify_text(gate_name: str, relative_path: str, text: str) -> tuple[Finding, ...]:
    """Classify pattern hits in text without touching the filesystem."""
    gate = _get_gate(gate_name)
    normalized_path = relative_path.replace("\\", "/").removeprefix("./")
    if any(_path_matches(normalized_path, prefix) for prefix in gate.excluded_path_prefixes):
        return ()
    pattern = re.compile(gate.pattern)
    return tuple(
        Finding(normalized_path, line_number, line, _line_is_allowed(gate, normalized_path, line))
        for line_number, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line) is not None
    )


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


def _input_files(raw_paths: Sequence[str]) -> tuple[Path, ...]:
    files: set[Path] = set()
    for raw_path in raw_paths:
        path = _resolve_input_path(raw_path)
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(
                child.resolve()
                for child in path.rglob("*")
                if child.is_file() and _is_scanable_source(child)
            )
        else:
            raise FileNotFoundError(path)
    return tuple(sorted(files, key=lambda path: path.as_posix()))


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def classify_paths(gate_name: str, raw_paths: Sequence[str]) -> tuple[Finding, ...]:
    """Classify all matching lines in the supplied files and directories."""
    findings: list[Finding] = []
    for path in _input_files(raw_paths):
        text = path.read_text(encoding="utf-8", errors="replace")
        findings.extend(classify_text(gate_name, _relative_path(path), text))
    return tuple(findings)


def _format_finding(finding: Finding) -> str:
    return f"{finding.path}:{finding.line_number}: {finding.text}"


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
    )
    return parser


def _parse_arguments(argv: Sequence[str] | None) -> ParsedArguments:
    namespace = _build_parser().parse_args(argv)
    list_gates = getattr(namespace, "list_gates", None)
    gate = getattr(namespace, "gate", None)
    if not isinstance(list_gates, bool):
        raise GateInputError("argparse produced an invalid --list-gates value")
    if gate is None:
        return ParsedArguments(list_gates, None)
    if not isinstance(gate, list):
        raise GateInputError("argparse produced an invalid --gate value")
    gate_values: list[str] = []
    for value in gate:
        if not isinstance(value, str):
            raise GateInputError("argparse produced a non-string gate argument")
        gate_values.append(value)
    return ParsedArguments(list_gates, tuple(gate_values))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the requested gate and return zero only when no hit is blocking."""
    args = _parse_arguments(argv)
    if args.list_gates:
        for name in GATE_NAMES:
            print(name)
        return 0

    if args.gate_and_paths is None or len(args.gate_and_paths) < 2:
        print("--gate requires a gate name and at least one path", file=sys.stderr)
        return 2
    gate_name, *raw_paths = args.gate_and_paths
    try:
        findings = classify_paths(gate_name, raw_paths)
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
