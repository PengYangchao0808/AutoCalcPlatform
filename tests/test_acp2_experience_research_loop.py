"""T-29 / C20a aggregate and research-loop acceptance self-tests.

The three aggregate cases are frozen:

* PASS/PASS -> PASS;
* PASS/NOT_VERIFIED (or a missing required child) -> NOT_VERIFIED with the
  concrete gap listed;
* any executed FAIL -> FAIL.

These are synthetic child results: no Controller, model, DSH or QC process is
started. The test also proves the ``control``/``search`` subcases return
NOT_VERIFIED when their upstream delivery is absent, never PASS.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO_ROOT / "scripts/acceptance/acp2_experience.py"
SCENARIO_PATH = (
    REPO_ROOT / "scripts/acceptance/acp2_experience_scenario_research_loop_live.py"
)
SCENARIOS_PATH = REPO_ROOT / "scripts/acceptance/acp2_experience_scenarios.py"
ACCEPTANCE_DIR = REPO_ROOT / "scripts/acceptance"

EXPECTED = ["experience:control", "experience:search"]
_SCENARIOS = None


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _child(phase: str, result: str) -> dict[str, str]:
    return {
        "schema": "acp2.acceptance.v1",
        "owner": "C",
        "scenario": "experience:research-loop-live",
        "phase": phase,
        "result": result,
        "run_id": "research-loop-live-r1",
    }


def test_aggregate_pass_pass() -> None:
    harness = _load_module("harness_aggregate_pass", HARNESS_PATH)
    result, gaps = harness.aggregate_decision(
        [_child("experience:control", "PASS"), _child("experience:search", "PASS")],
        EXPECTED,
    )
    assert result == "PASS"
    assert gaps == []


def test_aggregate_pass_not_verified_child() -> None:
    harness = _load_module("harness_aggregate_nv", HARNESS_PATH)
    result, gaps = harness.aggregate_decision(
        [
            _child("experience:control", "PASS"),
            _child("experience:search", "NOT_VERIFIED"),
        ],
        EXPECTED,
    )
    assert result == "NOT_VERIFIED"
    assert gaps == ["experience:search"]


def test_aggregate_missing_child_is_not_verified() -> None:
    harness = _load_module("harness_aggregate_missing", HARNESS_PATH)
    result, gaps = harness.aggregate_decision(
        [_child("experience:control", "PASS")],
        EXPECTED,
    )
    assert result == "NOT_VERIFIED"
    assert gaps == ["experience:search"]


def test_aggregate_executed_fail_wins() -> None:
    harness = _load_module("harness_aggregate_fail", HARNESS_PATH)
    result, gaps = harness.aggregate_decision(
        [
            _child("experience:control", "PASS"),
            _child("experience:search", "FAIL"),
        ],
        EXPECTED,
    )
    assert result == "FAIL"
    assert gaps == ["experience:search"]


def _scenario_module():
    global _SCENARIOS
    if _SCENARIOS is None:
        # Loading the registry also discovers every sibling exactly once; the
        # sibling must not be loaded a second time (register() rejects that).
        _load_module("scenarios_t29_registry", SCENARIOS_PATH)
        _SCENARIOS = sys.modules[
            "acp2_experience_scenario_research_loop_live"
        ]
    return _SCENARIOS


def _context(subcase: str):
    return SimpleNamespace(
        identity=SimpleNamespace(
            scenario="experience:research-loop-live",
            scenario_key="research-loop-live",
            subcase=subcase,
        ),
        run_id="research-loop-live-r1",
        out_dir=Path("/tmp/unused-t29"),
        timeout=60.0,
        assertions=[],
        artifacts=[],
        extensions={},
        refs={},
        usage=None,
        reason=None,
    )


def test_control_without_upstream_is_not_verified() -> None:
    scenarios = _scenario_module()
    context = _context("control")
    with mock.patch.object(scenarios, "_env_path", return_value=None):
        code = scenarios.scenario_research_loop_live(context)
    assert code == 2
    assert context.reason == "missing_prerequisite:ACP2_C_SOURCE"
    assert context.assertions[-1]["status"] == "NOT_VERIFIED"


def test_search_without_upstream_is_not_verified() -> None:
    scenarios = _scenario_module()
    context = _context("search")
    with mock.patch.object(scenarios, "_env_path", return_value=None):
        code = scenarios.scenario_research_loop_live(context)
    assert code == 2
    assert context.reason == "missing_prerequisite:ACP2_C_SOURCE"
    assert context.assertions[-1]["status"] == "NOT_VERIFIED"
