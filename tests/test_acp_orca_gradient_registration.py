# ruff: noqa: E501
"""OrcaGradient registration tests (PES2TS → ACP work unit X4′-A).

Covers the wiring layer only — catalog entry, workflow registry, CLI
parse/dispatch, job-edit coverage — not the workflow engine itself
(``tests/test_acp_workflows_orca_gradient.py`` owns ``run_orca_gradient``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
from acp.core.workflow import WorkflowResult
from acp.scheduler.job_edit import EDIT_ACTIVE_WORKFLOWS, audit_workflow_edit_coverage
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS
from acp.workflows.orca_gradient import (
    ORCA_GRADIENT_STAGES,
    ORCA_GRADIENT_WORKFLOW,
    OrcaGradientError,
    OrcaGradientInputError,
)
from acp.workflows.registry import get_workflow_entry

_WORKFLOW_ID = "OrcaGradient"


def _catalog_entry() -> dict[str, Any]:
    return next(item for item in WORKFLOW_CATALOG if item["id"] == _WORKFLOW_ID)


def test_catalog_entry_is_active_orca_workflow() -> None:
    entry = _catalog_entry()
    assert entry["status"] == "active"
    assert entry["visible"] is True
    assert entry["default_backend"] == "orca"
    assert entry["requires_binaries"] == ["orca"]
    assert entry["method_schema_id"] in METHOD_SCHEMAS


def test_supported_workflows_derives_orca_gradient() -> None:
    assert _WORKFLOW_ID in SUPPORTED_WORKFLOWS
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")
    assert SUPPORTED_WORKFLOWS == active_ids + ("fake",)


def test_registry_entry_exposes_orca_binary_requirement() -> None:
    entry = get_workflow_entry(_WORKFLOW_ID)
    assert entry is not None
    assert entry.name == _WORKFLOW_ID
    assert entry.label == "ORCA Single-Point Gradient"
    assert entry.requires_binaries == ["orca"]


def test_method_schema_block_exists_with_stage_declaration() -> None:
    schema = METHOD_SCHEMAS["orca_gradient"]
    assert schema["stages"]["mode"] == "static"
    assert schema["stages"]["static"] == ["prepare", "run_gradient", "finalize"]
    assert schema["stages"]["static"] == list(ORCA_GRADIENT_STAGES)
    assert schema["method_levels"] and schema["profiles"]


def test_edit_coverage_audit_includes_orca_gradient() -> None:
    assert _WORKFLOW_ID in EDIT_ACTIVE_WORKFLOWS
    assert audit_workflow_edit_coverage() == {"missing": [], "stale": []}


def _parse(argv: list[str]) -> Any:
    from acp.cli import build_parser

    return build_parser().parse_args(argv)


def test_cli_parser_accepts_gradient_config_form() -> None:
    args = _parse(["run", _WORKFLOW_ID, "--gradient-config", "request.json", "--output", "./out"])
    assert args.workflow == _WORKFLOW_ID
    assert args.gradient_config == "request.json"
    assert args.output == "./out"
    assert args.log_level == "INFO"


def test_cli_gradient_config_is_required() -> None:
    import pytest

    from acp.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", _WORKFLOW_ID, "--output", "./out"])


def test_cli_dispatch_table_contains_orca_gradient() -> None:
    import ast
    import inspect
    import textwrap

    from acp import cli

    tree = ast.parse(textwrap.dedent(inspect.getsource(cli.main)))
    dispatch_keys: set[str] = set()
    for node in ast.walk(tree):
        is_dispatch_target = False
        value: Any = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            is_dispatch_target = node.target.id == "dispatch"
            value = node.value
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "dispatch" for t in node.targets
        ):
            is_dispatch_target = True
            value = node.value
        if not is_dispatch_target or not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                dispatch_keys.add(key.value)
    assert _WORKFLOW_ID in dispatch_keys
    assert _WORKFLOW_ID not in cli._CLI_REMOVED_WORKFLOWS


def _completed_result() -> WorkflowResult:
    return WorkflowResult(
        status="completed",
        stages_completed=list(ORCA_GRADIENT_STAGES),
        metadata={
            "workflow": ORCA_GRADIENT_WORKFLOW,
            "energy_hartree": -1.166,
            "gradient_source": "engrad_file:grad.engrad",
            "gradient_unit": "hartree/bohr",
            "result_manifest_path": "RESULT/result_manifest.json",
        },
    )


def test_cli_handler_success_returns_zero(tmp_path: Path) -> None:
    from acp.cli import main

    request_file = tmp_path / "request.json"
    request_file.write_text("{}", encoding="utf-8")
    output_dir = tmp_path / "out"
    with patch(
        "acp.workflows.orca_gradient.run_orca_gradient", return_value=_completed_result()
    ) as mocked:
        rc = main(
            [
                "run",
                _WORKFLOW_ID,
                "--gradient-config",
                str(request_file),
                "--output",
                str(output_dir),
            ]
        )
    assert rc == 0
    mocked.assert_called_once()
    call_args, call_kwargs = mocked.call_args
    assert call_args == ()
    assert call_kwargs["request"] == {}
    assert call_kwargs["output_dir"] == output_dir


def test_cli_handler_input_error_exits_nonzero(tmp_path: Path) -> None:
    from acp.cli import main

    request_file = tmp_path / "request.json"
    request_file.write_text("{}", encoding="utf-8")
    with patch(
        "acp.workflows.orca_gradient.run_orca_gradient",
        side_effect=OrcaGradientInputError("[ORCA_GRADIENT_E_SCHEMA] bad schema"),
    ):
        rc = main(
            [
                "run",
                _WORKFLOW_ID,
                "--gradient-config",
                str(request_file),
                "--output",
                str(tmp_path / "out"),
            ]
        )
    assert rc != 0


def test_cli_handler_gradient_error_exits_nonzero(tmp_path: Path) -> None:
    from acp.cli import main

    request_file = tmp_path / "request.json"
    request_file.write_text("{}", encoding="utf-8")
    with patch(
        "acp.workflows.orca_gradient.run_orca_gradient",
        side_effect=OrcaGradientError(code="ORCA_GRADIENT_E_GRADIENT", message="gradient missing"),
    ):
        rc = main(
            [
                "run",
                _WORKFLOW_ID,
                "--gradient-config",
                str(request_file),
                "--output",
                str(tmp_path / "out"),
            ]
        )
    assert rc != 0


def test_cli_handler_bad_gradient_config_json_exits_nonzero(tmp_path: Path) -> None:
    from acp.cli import main

    request_file = tmp_path / "request.json"
    request_file.write_text("{not-json", encoding="utf-8")
    rc = main(
        [
            "run",
            _WORKFLOW_ID,
            "--gradient-config",
            str(request_file),
            "--output",
            str(tmp_path / "out"),
        ]
    )
    assert rc != 0


__all__ = [
    "test_catalog_entry_is_active_orca_workflow",
    "test_cli_dispatch_table_contains_orca_gradient",
    "test_cli_handler_bad_gradient_config_json_exits_nonzero",
    "test_cli_handler_gradient_error_exits_nonzero",
    "test_cli_handler_input_error_exits_nonzero",
    "test_cli_handler_success_returns_zero",
    "test_cli_parser_accepts_gradient_config_form",
    "test_cli_gradient_config_is_required",
    "test_edit_coverage_audit_includes_orca_gradient",
    "test_method_schema_block_exists_with_stage_declaration",
    "test_registry_entry_exposes_orca_binary_requirement",
    "test_supported_workflows_derives_orca_gradient",
]
