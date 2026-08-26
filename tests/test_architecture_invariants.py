from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS

CURRENT_ACTIVE_IDS = (
    "singlepoint",
    "optimize",
    "frequency",
    "optfreq",
    "optfreqsp",
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "Lowconfirm",
    "Highconfirm",
)
TARGET_ACTIVE_IDS = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "xtb_optimize",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "nmr",
)

TARGET_STATE_ENABLED = False


def _cli_dispatch_ids() -> set[str]:
    from acp import cli

    tree = ast.parse(textwrap.dedent(inspect.getsource(cli.main)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "dispatch":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        annotated_keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                break
            annotated_keys.append(key.value)
        else:
            return set(annotated_keys)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets
        if not any(isinstance(target, ast.Name) and target.id == "dispatch" for target in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        assignment_keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                break
            assignment_keys.append(key.value)
        else:
            return set(assignment_keys)
    raise AssertionError("main() must define a string-keyed workflow dispatch dictionary")


def test_current_active_workflow_ids_are_exact_and_ordered() -> None:
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")

    assert active_ids == CURRENT_ACTIVE_IDS
    assert len(active_ids) == 11
    assert set(active_ids) == {
        "singlepoint",
        "optimize",
        "frequency",
        "optfreq",
        "optfreqsp",
        "xtb_optimize",
        "nmr",
        "Confsearch",
        "PESsearch",
        "Lowconfirm",
        "Highconfirm",
    }


def test_scheduler_workflows_follow_catalog_order_with_fake_hook() -> None:
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")

    assert SUPPORTED_WORKFLOWS == active_ids + ("fake",)


def test_each_active_workflow_has_dispatch_and_method_schema() -> None:
    dispatch_ids = _cli_dispatch_ids()

    for entry in WORKFLOW_CATALOG:
        if entry.get("status") != "active":
            continue
        workflow_id = entry.get("id")
        assert isinstance(workflow_id, str)
        assert workflow_id in dispatch_ids
        assert entry["method_schema_id"] in METHOD_SCHEMAS


@pytest.mark.skipif(not TARGET_STATE_ENABLED, reason="Target-state gate activates at Todo 36")
def test_target_active_workflow_ids_are_exact() -> None:
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")

    assert len(active_ids) == 10
    assert set(active_ids) == {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "xtb_optimize",
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
        "nmr",
    }
