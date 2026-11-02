"""Round-5 PR-03: every audited contract field has a registered disposition.

The registry (``acp.api.parameter_effects``) must enumerate the *existing*
contract fields — a newly declared contract key without a disposition fails
here instead of shipping silently ignored.  Behaviour tests prove the
dispositions themselves (rejections, aliases, noops).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_audited_workflows_have_no_unregistered_contract_fields() -> None:
    from acp.api.parameter_effects import AUDITED_WORKFLOWS, unregistered_contract_fields

    for workflow in AUDITED_WORKFLOWS:
        missing = unregistered_contract_fields(workflow)
        assert missing == [], f"{workflow} contract fields without disposition: {missing}"


def test_a_new_contract_key_fails_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    from acp.api import submission_contract
    from acp.api.parameter_effects import unregistered_contract_fields

    contract = submission_contract.WORKFLOW_CONTRACTS["scan"]
    patched = replace(contract, input_keys=contract.input_keys | {"surprise_field"})
    monkeypatch.setitem(submission_contract.WORKFLOW_CONTRACTS, "scan", patched)
    assert "input.surprise_field" in unregistered_contract_fields("scan")


def test_registered_behaviour_tests_exist() -> None:
    from acp.api.parameter_effects import registry

    for workflow, effects in registry().items():
        for field, effect in effects.items():
            if not effect.behavior_test:
                assert effect.disposition == "declared_contract_only", (workflow, field)
                continue
            path_text, _, test_name = effect.behavior_test.partition("::")
            path = _REPO_ROOT / path_text
            assert path.is_file(), (workflow, field, path_text)
            assert f"def {test_name}(" in path.read_text(encoding="utf-8"), (
                workflow,
                field,
                test_name,
            )


def test_audited_workflow_coverage_is_reported() -> None:
    from acp.api.parameter_effects import coverage

    reported = coverage()
    assert set(reported) == {"PESsearch", "scan", "irc", "tsmode"}
    assert all(item["status"] == "audited" for item in reported.values())


# --------------------------------------------------------------------------- #
# behaviour of the dispositions (PES parameters)
# --------------------------------------------------------------------------- #


def test_scan_driver_retry_alias_and_conflict() -> None:
    from acp.calculations.submission_validation import analyze_pes_submission

    analysis = analyze_pes_submission(
        {
            "source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"},
            "coordinate": {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
            "coordinates": [
                {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
                {"kind": "distance", "atoms": [1, 2], "start": "1.0", "end": "2.0"},
            ],
            "protocol": {"scan_driver": {"retry_count": 3}},
        },
        {},
    )
    dispositions = {item.field: item.disposition for item in analysis.effects}
    assert dispositions["protocol.scan_driver.retry_count"] == "normalized_alias"

    with pytest.raises(ValueError, match="conflicts with"):
        analyze_pes_submission(
            {
                "source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"},
                "coordinate": {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
                "coordinates": [
                    {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
                    {"kind": "distance", "atoms": [1, 2], "start": "1.0", "end": "2.0"},
                ],
                "protocol": {
                    "scan_driver": {"retry_count": 3},
                    "scan_optimizer": {"retry_count": 5},
                },
            },
            {},
        )


def test_native_scan_rejects_pointwise_only_driver_fields() -> None:
    from acp.calculations.submission_validation import analyze_pes_submission

    with pytest.raises(ValueError, match="native_scan does not support"):
        analyze_pes_submission(
            {
                "source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"},
                "coordinate": {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
                "protocol": {"scan_driver": {"failure_policy": "retry_original"}},
            },
            {},
        )


def test_protocol_execution_mode_is_rejected() -> None:
    from acp.calculations.submission_validation import analyze_pes_submission

    with pytest.raises(ValueError, match="execution_mode"):
        analyze_pes_submission(
            {
                "source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"},
                "coordinate": {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
                "protocol": {"execution_mode": "pointwise"},
            },
            {},
        )


def test_guided_scan_is_legacy_noop() -> None:
    from acp.calculations.submission_validation import analyze_pes_submission

    analysis = analyze_pes_submission(
        {"source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"}},
        {"strategy": "guided_scan"},
    )
    dispositions = {item.field: item.disposition for item in analysis.effects}
    assert dispositions["method.strategy=guided_scan"] == "legacy_noop"


def test_unimplemented_strategy_is_rejected() -> None:
    from acp.calculations.submission_validation import analyze_pes_submission

    with pytest.raises(ValueError, match="not implemented"):
        analyze_pes_submission(
            {"source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"}},
            {"strategy": "direct_ts"},
        )


def test_scan_request_carrier_must_agree() -> None:
    """A prepared input.scan_request must not contradict the expanded fields."""
    from acp.calculations.submission_validation import analyze_pes_submission

    base = {
        "source": {"source_type": "xyz_text", "xyz_text": "1\nH\nH 0 0 0\n"},
        "coordinate": {"kind": "distance", "atoms": [0, 1], "start": "1.0", "end": "3.0"},
    }
    agreed = analyze_pes_submission(
        {**base, "scan_request": {"coordinate": dict(base["coordinate"])}}, {}
    )
    dispositions = {item.field: item.disposition for item in agreed.effects}
    assert dispositions["input.scan_request"] == "consumed"

    with pytest.raises(ValueError, match="conflicts"):
        analyze_pes_submission(
            {
                **base,
                "scan_request": {
                    "coordinate": {"kind": "distance", "atoms": [0, 2], "start": "1.0", "end": "3.0"}
                },
            },
            {},
        )
