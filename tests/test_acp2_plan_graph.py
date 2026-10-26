"""M0-01 plan-graph gate: parse EXECUTION_PLAN.md instead of restating it.

The frozen vectors below are independent expectations, hand-copied from
``reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md`` §3/§9/§10 and from
the task headings; they are never recomputed from the generator under test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/acceptance/build_acp2_plan_graph.py"
PLAN = REPO_ROOT / "reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md"
GRAPH = REPO_ROOT / "reports/acp2_delivery_gap_audit_20261026/plan-graph.json"

EXPECTED_TASKS = (
    "M0-01",
    "M0-02",
    "M0-03",
    "M0-04",
    "M0-05",
    "M0-06",
    "M0-07",
    "M0-08",
    "M0-09",
    "M0-10",
    "M0-11",
    "M0-12",
    "M0-13",
    "M1-01",
    "M1-02",
    "M1-03",
    "M1-04",
    "M1-05",
    "M1-06",
    "M2-01",
    "M2-02",
    "M2-03",
    "M2-04",
    "M2-05",
    "M3-01",
    "M3-02",
    "M3-03",
    "M3-04",
    "M3-05",
    "M3-06",
    "M4-01",
    "M4-02",
    "M4-03",
    "M4-04",
    "M4-05",
    "M4-06",
    "M5-01",
    "M5-02",
    "M5-03",
)

EXPECTED_CHECKPOINTS = (
    "M0-01a",
    "M0-01b",
    "M0-02a",
    "M0-02b",
    "M0-02c",
    "M0-03a",
    "M0-03b",
    "M0-03c",
    "M1-01a",
    "M1-01b",
    "M1-01c",
    "M1-04a",
    "M1-04b",
    "M1-04c",
    "M2-01a",
    "M2-01b",
    "M2-01c",
    "M2-03a",
    "M2-03b",
    "M2-03c",
    "M2-04a",
    "M2-04b",
    "M2-04c",
    "M2-04d",
    "M3-01a",
    "M3-01b",
    "M3-02a",
    "M3-02b",
    "M3-05a",
    "M3-05b",
    "M4-06a",
    "M4-06b",
)

EXPECTED_START_PATH = (
    "M0-01",
    "M0-04",
    "M0-05",
    "M0-06",
    "M0-11",
    "M0-12",
    "M1-01",
    "M1-06",
    "M2-01",
    "M2-03",
    "M3-03",
    "M3-04",
    "M4-01",
    "M5-01",
    "M5-02",
    "M5-03",
)

EXPECTED_CLOSE_PATH = (
    "M0-01",
    "M0-04",
    "M0-05",
    "M0-06",
    "M0-11",
    "M0-12",
    "M1-01",
    "M1-06",
    "M2-01",
    "M2-03",
    "M3-03",
    "M3-04",
    "M4-01",
    "M4-02",
    "M4-03",
    "M4-06",
    "M5-01",
    "M5-02",
    "M5-03",
)

EXPECTED_EXTERNAL_CLOSE_PATH = (
    "M0-01",
    "M0-04",
    "M0-05",
    "M0-06",
    "M0-11",
    "M0-12",
    "M0-13",
    "M1-01",
    "M1-06",
    "M2-01",
    "M2-03",
    "M3-03",
    "M3-04",
    "M4-01",
    "M4-02",
    "M4-03",
    "M4-06",
    "M5-01",
    "M5-02",
    "M5-03",
)

EXPECTED_M4_01_DEPS = (
    "M2-01",
    "M2-02",
    "M2-03",
    "M2-04",
    "M3-01",
    "M3-02",
    "M3-03",
    "M3-04",
    "M3-05",
    "M0-03",
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def graph_builder():
    return _load("acp2_plan_graph_for_tests", SCRIPT)


@pytest.fixture(scope="module")
def model(graph_builder):
    return graph_builder.load_model(PLAN)


@pytest.fixture(scope="module")
def document(graph_builder, model):
    return graph_builder.build_document(model)


def _nodes(graph: dict) -> dict[str, dict]:
    return {node["id"]: node for node in graph["nodes"]}


def test_every_task_is_parsed_with_owner_size_and_title(model) -> None:
    assert tuple(task.id for task in model.tasks) == EXPECTED_TASKS
    for task in model.tasks:
        assert task.title
        assert task.owner
        assert task.size in {"S", "M", "L", "XL"}


def test_size_histogram_matches_the_task_headings(model) -> None:
    histogram = {"S": 0, "M": 0, "L": 0, "XL": 0}
    for task in model.tasks:
        histogram[task.size] += 1
    # §2.6's prose estimate says 9 XL / 22 L / 7 M / 1 S; the per-task
    # '**规模**' fields (authoritative for the graph) give 6 M and 23 L.
    assert histogram == {"S": 1, "M": 6, "L": 23, "XL": 9}


def test_range_prerequisites_expand_to_every_task(model) -> None:
    tasks = {task.id: task for task in model.tasks}
    assert tasks["M4-01"].start_dependencies == EXPECTED_M4_01_DEPS
    assert tasks["M0-01"].start_dependencies == ()
    assert tasks["M0-02"].start_dependencies == ("M0-01",)
    assert tasks["M0-06"].start_dependencies == ("M0-02", "M0-05")


def test_prose_clauses_are_recorded_as_prerequisite_notes(model) -> None:
    tasks = {task.id: task for task in model.tasks}
    assert tasks["M0-06"].notes == ("与 M0-05 顺序合入同模块",)
    assert tasks["M3-06"].notes == ("不阻塞 M4 的 P2 最小纵切",)
    assert tasks["M0-01"].notes == ()
    assert tasks["M5-01"].notes == ()
    assert tasks["M4-06"].close_only_dependencies == ("M4-03", "M4-05")
    assert tasks["M5-01"].close_only_dependencies == (
        "M4-01",
        "M4-02",
        "M4-03",
        "M4-04",
        "M4-05",
        "M4-06",
    )


def test_conditional_task_keeps_its_edges_out_of_the_default_graphs(graph_builder, model) -> None:
    conditional = [task for task in model.tasks if task.conditional]
    assert [task.id for task in conditional] == ["M0-13"]
    assert model.conditional_edges == {"M1-01": ("M0-13",)}

    start = _nodes(graph_builder.build_graph(model, "start"))
    assert start["M0-13"]["dependencies"] == []
    assert start["M0-13"]["declared_dependencies"] == ["M0-09", "M0-12"]
    assert start["M0-13"]["applies"] is False
    assert "M0-13" not in start["M1-01"]["dependencies"]

    external = _nodes(graph_builder.build_graph(model, "close_external_change"))
    assert external["M0-13"]["dependencies"] == ["M0-09", "M0-12"]
    assert external["M0-13"]["applies"] is True
    assert "M0-13" in external["M1-01"]["dependencies"]


def test_close_only_edges_never_reach_the_start_graph(graph_builder, model) -> None:
    start = _nodes(graph_builder.build_graph(model, "start"))
    close = _nodes(graph_builder.build_graph(model, "close"))

    assert start["M5-01"]["dependencies"] == ["M3-02", "M3-06", "M4-01"]
    assert start["M5-02"]["dependencies"] == ["M0-03", "M5-01"]
    assert start["M4-06"]["dependencies"] == ["M0-04", "M0-05", "M0-06", "M0-10", "M4-01"]

    assert close["M4-06"]["dependencies"] == [
        "M0-04",
        "M0-05",
        "M0-06",
        "M0-10",
        "M4-01",
        "M4-03",
        "M4-05",
    ]
    assert close["M5-01"]["dependencies"] == [
        "M3-02",
        "M3-06",
        "M4-01",
        "M4-02",
        "M4-03",
        "M4-04",
        "M4-05",
        "M4-06",
    ]
    assert close["M5-02"]["dependencies"] == [
        "M0-03",
        "M4-01",
        "M4-02",
        "M4-03",
        "M4-04",
        "M4-05",
        "M4-06",
        "M5-01",
    ]


def test_graph_totals_match_the_frozen_section_3_model(document) -> None:
    start = document["graphs"]["start"]
    close = document["graphs"]["close"]
    external = document["graphs"]["close_external_change"]

    assert (start["node_count"], start["edge_count"]) == (39, 96)
    assert (start["critical_path_length"], start["xl_count"]) == (16, 3)
    assert tuple(start["critical_path"]) == EXPECTED_START_PATH
    assert start["xl_on_critical_path"] == ["M2-01", "M2-03", "M3-04"]
    assert start["xl_total"] == 9

    assert (close["node_count"], close["edge_count"]) == (39, 109)
    assert (close["critical_path_length"], close["xl_count"]) == (19, 6)
    assert tuple(close["critical_path"]) == EXPECTED_CLOSE_PATH

    assert (external["node_count"], external["edge_count"]) == (39, 112)
    assert (external["critical_path_length"], external["xl_count"]) == (20, 6)
    assert tuple(external["critical_path"]) == EXPECTED_EXTERNAL_CLOSE_PATH


def test_edges_are_consistent_with_declared_dependencies(document) -> None:
    for name, graph in document["graphs"].items():
        nodes = _nodes(graph)
        expected = {(dep, node) for node, row in nodes.items() for dep in row["dependencies"]}
        assert {(edge["from"], edge["to"]) for edge in graph["edges"]} == expected, name
        assert len(graph["edges"]) == graph["edge_count"]


def test_every_declared_subcheckpoint_is_registered(model) -> None:
    assert tuple(sorted(model.checkpoint_ids)) == EXPECTED_CHECKPOINTS
    assert len(model.checkpoint_ids) == 32
    assert model.unknown_refs == ()
    for checkpoint in model.checkpoint_ids:
        parent = model.checkpoint_owners[checkpoint]
        assert checkpoint.startswith(parent)
        assert parent in model.task_ids


def test_gap_and_scope_tables_are_parsed(model) -> None:
    gaps = {gap.id: gap for gap in model.gaps}
    assert tuple(sorted(gaps)) == tuple(f"G{index:02d}" for index in range(1, 27))
    assert gaps["G01"].h_rows == ("H2", "H7")
    assert gaps["G01"].tasks == ("M0-02", "M0-08", "M5-03")
    assert gaps["G02"].conditional_tasks == ("M0-13",)
    assert gaps["G18"].h_rows == tuple(f"H{index}" for index in range(8))
    assert "M4-06" in gaps["G18"].tasks
    assert gaps["G20"].notes == ("各 CI 门",)
    assert {key: len(value) for key, value in model.scope_items.items()} == {
        "IN_SCOPE": 5,
        "DEFERRED": 3,
        "OUT_OF_SCOPE": 7,
    }


def test_committed_plan_graph_is_the_current_generator_output(graph_builder, document) -> None:
    payload = graph_builder._serialize(document)
    assert GRAPH.read_text(encoding="utf-8") == payload
    assert "ESTIMATE_ONLY" not in payload
    assert document["source_plan_sha256"] == graph_builder.sha256_file(PLAN)
    assert document["task_count"] == 39
    assert document["checkpoint_count"] == 32
    assert document["unknown_refs"] == []
    assert document["cycles"] == []


def test_check_mode_accepts_the_committed_graph(graph_builder, capsys) -> None:
    assert graph_builder.main(["--check"]) == 0
    assert "VALID" in capsys.readouterr().out


def test_check_mode_rejects_a_stale_graph(graph_builder, tmp_path, capsys) -> None:
    stale = tmp_path / "plan-graph.json"
    stale.write_text(json.dumps({"schema": "stale"}), encoding="utf-8")
    assert graph_builder.main(["--check", "--out", str(stale)]) == 1
    assert "not the current generator output" in capsys.readouterr().err


def test_unknown_prerequisite_token_fails_closed(graph_builder) -> None:
    with pytest.raises(graph_builder.PlanParseError, match="unknown task"):
        graph_builder._expand_token("M9-99", {"M0-01"}, "M0-01")
    with pytest.raises(graph_builder.PlanParseError, match="unparsable prerequisite token"):
        graph_builder._expand_token("稍后补充", {"M0-01"}, "M0-01")


def test_unknown_task_reference_in_the_plan_fails_closed(graph_builder, tmp_path) -> None:
    text = PLAN.read_text(encoding="utf-8").replace(
        "**前置**：M0-02、M0-05；与 M0-05 顺序合入同模块。",
        "**前置**：M0-02、M9-99；与 M0-05 顺序合入同模块。",
    )
    mutated = tmp_path / "EXECUTION_PLAN.md"
    mutated.write_text(text, encoding="utf-8")
    with pytest.raises(graph_builder.PlanParseError, match="unknown task M9-99"):
        graph_builder.load_model(mutated)


def test_task_count_guard_fails_closed(graph_builder, tmp_path) -> None:
    text = PLAN.read_text(encoding="utf-8")
    text += "\n### M9-01 extra task\n\n- **Owner**：B；**规模**：S；**前置**：M0-01。\n"
    mutated = tmp_path / "EXECUTION_PLAN.md"
    mutated.write_text(text, encoding="utf-8")
    with pytest.raises(graph_builder.PlanParseError, match="expected 39"):
        graph_builder.load_model(mutated)


def test_orphan_subcheckpoint_reference_is_reported(graph_builder, model) -> None:
    unknown = graph_builder.find_unknown_checkpoint_refs(
        "see M0-01z and M0-01a for details", model.tasks
    )
    assert unknown == ("M0-01z",)


def test_dependency_cycle_is_rejected(graph_builder) -> None:
    cyclic = {"M0-01": ("M0-02",), "M0-02": ("M0-01",)}
    with pytest.raises(graph_builder.PlanParseError, match="cycle"):
        graph_builder._topological_order(cyclic, "start")
