# pyright: reportUnknownParameterType=false, reportMissingParameterType=false, reportUnknownArgumentType=false
from __future__ import annotations

import pytest

from acp.api.job_status_view import (
    build_live_status,
    normalize_live_metrics,
    resolve_display_method,
    resolve_stage_label,
)
from acp.api.v1_schemas import JobLiveStatus


def test_normalize_live_metrics_sanitizes_orders_deduplicates_and_caps() -> None:
    # Given: valid metrics, a duplicate key, malformed entries, and four candidates.
    raw_metrics = [
        {
            "key": "zeta",
            "label_key": "live.old",
            "label": "Old",
            "value": "old",
            "kind": "text",
            "priority": 0,
            "detail": "old detail",
        },
        {
            "key": "zeta",
            "label_key": "live.current",
            "label": "Current",
            "value": "<b>last</b>",
            "kind": "status",
            "priority": 20,
            "detail": "current detail",
        },
        {
            "key": "long",
            "value": "<span>" + "x" * 60 + "</span>",
            "kind": "text",
            "priority": 10,
        },
        {
            "key": "alpha",
            "value": 14,
            "kind": "count",
            "priority": 10,
        },
        {"key": "over-cap", "value": "not shown", "kind": "text", "priority": 1},
        {"key": "missing-kind", "value": "drop me"},
        {"key": "bad-kind", "value": "drop me", "kind": "unknown"},
        {"key": 1, "value": "drop me", "kind": "text"},
        1,
    ]

    # When: the raw state payload is normalized.
    result = normalize_live_metrics(raw_metrics)

    # Then: the last duplicate wins, ties sort by key, values are safe, and output is capped.
    assert [metric["key"] for metric in result] == ["zeta", "alpha", "long"]
    assert result[0]["value"] == "last"
    assert result[1]["value"] == "14"
    assert result[2]["value"] == "x" * 48
    assert all("<" not in metric["value"] and ">" not in metric["value"] for metric in result)
    assert all(
        set(metric) == {"key", "label_key", "label", "value", "kind", "priority", "detail"}
        for metric in result
    )


@pytest.mark.parametrize(
    "raw_metrics",
    [
        None,
        "garbage",
        {},
        [1, 2, {"key": 1}],
        [{"key": "missing-value", "kind": "text"}],
        [{"key": "missing-kind", "value": "value"}],
    ],
)
def test_normalize_live_metrics_ignores_malformed_payloads(raw_metrics) -> None:
    # Given: a state value that is not a usable metric list.

    # When: normalization is requested.
    result = normalize_live_metrics(raw_metrics)

    # Then: the boundary returns a harmless plain list with no more than three entries.
    assert isinstance(result, list)
    assert len(result) <= 3


@pytest.mark.parametrize(
    ("stage_key", "expected"),
    [
        (None, None),
        ("", None),
        ("run_single_points", "单点计算"),
        ("unknown_stage", "Unknown Stage"),
    ],
)
def test_resolve_stage_label_uses_localized_labels_and_fallback(
    stage_key: str | None, expected: str | None
) -> None:
    # Given: a known, unknown, empty, or absent stage key.

    # When: the stage label is resolved.
    result = resolve_stage_label(stage_key)

    # Then: known labels and the stage-label fallback are exposed without inventing a label.
    assert result == expected


def test_build_live_status_projects_stage_and_metrics() -> None:
    # Given: a state.json-shaped payload for the PES single-point stage.
    state_data = {
        "current_stage": "run_single_points",
        "stage_index": 6,
        "stage_total": 9,
        "live_metrics": [
            {
                "key": "completed_total",
                "label_key": "live.single_points",
                "value": "14 / 25",
                "kind": "count",
                "priority": 100,
            }
        ],
    }

    # When: the state payload is projected into the API model.
    result = build_live_status(state_data)

    # Then: the typed live status retains stage position and display-ready metric data.
    assert isinstance(result, JobLiveStatus)
    assert result.stage_label == "单点计算"
    assert result.stage_index == 6
    assert result.stage_total == 9
    assert len(result.metrics) == 1
    assert result.metrics[0].key == "completed_total"
    assert result.metrics[0].value == "14 / 25"


@pytest.mark.parametrize(
    "state_data",
    [
        {},
        {"current_stage": None, "live_metrics": "garbage"},
        {"current_stage": "", "live_metrics": []},
        None,
        [],
        "garbage",
    ],
)
def test_build_live_status_returns_none_without_stage_or_metrics(state_data) -> None:
    # Given: a state payload with neither a usable stage nor a usable metric.

    # When: live status projection is requested.
    result = build_live_status(state_data)

    # Then: no empty status shell is returned.
    assert result is None


def test_build_live_status_keeps_metrics_without_a_stage() -> None:
    # Given: a state payload containing only a valid live metric.
    state_data = {
        "live_metrics": [{"key": "status", "value": "running", "kind": "status", "priority": 1}]
    }

    # When: live status projection is requested.
    result = build_live_status(state_data)

    # Then: metrics alone are sufficient to produce a typed live status.
    assert isinstance(result, JobLiveStatus)
    assert result.stage_label is None
    assert result.metrics[0].value == "running"


def test_resolve_display_method_uses_pes_single_point_settings_first() -> None:
    # Given: input and method manifest paths disagree about the PES protocol.
    spec_input = {
        "scan_request": {
            "protocol": {
                "single_point": {"method": "B97-3c", "basis": None},
                "scan_optimizer": {"method": "GFN2-xTB", "basis": "minimal"},
            }
        }
    }
    spec_method = {
        "scan_request": {"protocol": {"single_point": {"method": "wrong", "basis": "wrong"}}}
    }

    # When: the display method is requested during PES single-point execution.
    result = resolve_display_method(spec_method, spec_input, "PESsearch", "run_single_points")

    # Then: the input protocol wins and a composite method with no basis shows only its method.
    assert result == "B97-3c"


def test_resolve_display_method_uses_pes_scan_optimizer_for_scan_stage() -> None:
    # Given: a PES protocol with distinct scan and single-point methods.
    protocol = {
        "single_point": {"method": "B97-3c", "basis": "def2-SVP"},
        "scan_optimizer": {"method": "r2SCAN-3c", "basis": "def2-TZVP"},
    }

    # When: the display method is requested during a relaxed scan.
    result = resolve_display_method(
        {}, {"scan_request": {"protocol": protocol}}, "PESsearch", "run_relaxed_scan"
    )

    # Then: the scan optimizer pair is selected.
    assert result == "r2SCAN-3c / def2-TZVP"


def test_resolve_display_method_reads_pes_protocol_from_method_manifest() -> None:
    # Given: scheduler-manifest mode stores the PES protocol under spec_method.
    spec_method = {
        "scan_request": {
            "protocol": {
                "single_point": {"method": "B97-3c", "basis": "def2-SVP"},
                "scan_optimizer": {"method": "GFN-FF", "basis": None},
            }
        }
    }

    # When: an early scan-ish stage is rendered.
    result = resolve_display_method({}, {}, "PESsearch", "prepare")
    method_result = resolve_display_method(spec_method, {}, "PESsearch", "validate_coordinate")

    # Then: the method manifest fallback works for scan stages without an input protocol.
    assert result is None
    assert method_result == "GFN-FF"


def test_resolve_display_method_batch_single_point_falls_back_to_optimization() -> None:
    # Given: BatchOptimize has no dedicated single-point method or basis.
    spec_method = {
        "optimization_method": "r2SCAN-3c",
        "optimization_basis": "def2-SVP",
        "single_point_method": "",
        "single_point_basis": None,
    }

    # When: the single-point stage is rendered.
    result = resolve_display_method(spec_method, {}, "BatchOptimize", "single_point")

    # Then: BatchMethodOptions.for_step-compatible fallback uses optimization settings.
    assert result == "r2SCAN-3c / def2-SVP"


def test_resolve_display_method_batch_non_single_point_uses_optimization() -> None:
    # Given: BatchOptimize has distinct optimization and single-point settings.
    spec_method = {
        "optimization_method": "r2SCAN-3c",
        "optimization_basis": "def2-SVP",
        "single_point_method": "B97-3c",
        "single_point_basis": "def2-TZVP",
    }

    # When: the frequency stage is rendered.
    result = resolve_display_method(spec_method, {}, "BatchOptimize", "frequency")

    # Then: frequency follows the optimization pair, matching for_step semantics.
    assert result == "r2SCAN-3c / def2-SVP"


def test_resolve_display_method_simple_prefers_workflow_level() -> None:
    # Given: simple workflow levels contain workflow-specific, common, and fallback entries.
    spec_method = {
        "levels": {
            "first": {"functional": "first-method", "basis": "first-basis"},
            "optfreq": {"functional": "common-method", "basis": "common-basis"},
            "optimize": {"functional": "workflow-method", "basis": "workflow-basis"},
        }
    }

    # When: the optimize display method is resolved.
    result = resolve_display_method(spec_method, {}, "optimize", None)

    # Then: the exact workflow level has highest precedence.
    assert result == "workflow-method / workflow-basis"


def test_resolve_display_method_simple_falls_back_to_first_level() -> None:
    # Given: no workflow-specific or optfreq level exists.
    spec_method = {
        "levels": {
            "first": {"functional": "first-method", "basis": "first-basis"},
            "second": {"functional": "second-method", "basis": "second-basis"},
        }
    }

    # When: a simple scan display method is resolved.
    result = resolve_display_method(spec_method, {}, "scan", None)

    # Then: the first level entry supplies the pair.
    assert result == "first-method / first-basis"


def test_resolve_display_method_irc_uses_functional_then_levels_fallback() -> None:
    # Given: IRC has no primary method but exposes a functional and an IRC level fallback.
    spec_method = {
        "method": "",
        "functional": "functional-method",
        "basis": "functional-basis",
        "levels": {"irc": {"method": "irc-method", "basis": "irc-basis"}},
    }

    # When: the IRC display method is resolved.
    result = resolve_display_method(spec_method, {}, "irc", "irc")

    # Then: the functional pair wins before levels.irc.
    assert result == "functional-method / functional-basis"


def test_resolve_display_method_irc_falls_back_to_irc_level() -> None:
    # Given: IRC has neither a top-level method nor functional.
    spec_method = {"levels": {"irc": {"method": "irc-method", "basis": "irc-basis"}}}

    # When: the IRC display method is resolved.
    result = resolve_display_method(spec_method, {}, "irc", None)

    # Then: levels.irc supplies the pair.
    assert result == "irc-method / irc-basis"


@pytest.mark.parametrize("workflow", ["Confsearch", "nmr", "legacy-workflow"])
def test_resolve_display_method_legacy_uses_top_level_pair(workflow: str) -> None:
    # Given: a legacy-style method manifest has only top-level settings.
    spec_method = {"method": "wB97X-D4", "basis": "def2-TZVPPD"}

    # When: the legacy display method is resolved.
    result = resolve_display_method(spec_method, {}, workflow, None)

    # Then: the top-level method pair is rendered.
    assert result == "wB97X-D4 / def2-TZVPPD"


@pytest.mark.parametrize(
    ("spec_method", "spec_input", "workflow", "current_stage"),
    [
        ({}, {}, "Confsearch", None),
        (None, None, "Confsearch", None),
        ([], [], "PESsearch", "run_single_points"),
        ({}, {}, "PESsearch", []),
        ({"levels": "not-a-mapping"}, {}, "singlepoint", None),
        ({"method": None, "basis": []}, {"scan_request": []}, "PESsearch", "prepare"),
    ],
)
def test_resolve_display_method_malformed_inputs_return_none_or_do_not_raise(
    spec_method,
    spec_input,
    workflow,
    current_stage,
) -> None:
    # Given: malformed method/input manifests across supported workflow branches.

    # When: method display resolution is requested.
    result = resolve_display_method(spec_method, spec_input, workflow, current_stage)

    # Then: malformed data never escapes as an exception or an empty string.
    assert result is None or isinstance(result, str)
