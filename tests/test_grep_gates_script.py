from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from scripts.check_grep_gates import classify_text

SCRIPT: Final[Path] = Path(__file__).parents[1] / "scripts" / "check_grep_gates.py"
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
            "from acp.mechanism import models",
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
    assert len(names) == 32
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
        "from acp.mechanism import models",
        "import acp.mechanism",
        "x = acp.mechanism.models",
        "from acp import mechanism",
    ),
)
def test_final_mechanism_imports_intercepts_all_four_valid_forms(line: str) -> None:
    assert _statuses("final_mechanism_imports", "src/acp/workflows/example.py", line) == (False,)


def test_pre_delete_mechanism_external_allows_internal_self_references_only() -> None:
    assert (
        _statuses(
            "pre_delete_mechanism_external",
            "src/acp/mechanism/stages/confirm.py",
            "from acp.mechanism import models",
        )
        == ()
    )
    assert _statuses(
        "pre_delete_mechanism_external",
        "src/acp/workflows/example.py",
        "from acp.mechanism import models",
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
