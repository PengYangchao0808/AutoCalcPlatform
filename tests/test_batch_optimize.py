"""Tests for the mechanism-free BatchOptimize input models."""
# pyright: basic, reportArgumentType=false, reportIndexIssue=false, reportOptionalSubscript=false, reportCallIssue=false, reportAny=false

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from acp.calculations.batch.models import (
    JsonObject,
    build_tag_title,
    load_batch_request,
    load_items_from_result_manifest,
    load_items_from_s2_path_manifest,
    parse_tag_comment,
)
from acp.calculations.contracts import StructureRole

FIXTURES = Path(__file__).parent / "fixtures"


def _write_xyz(path: Path, comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"2\n{comment}\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
        encoding="utf-8",
    )


def test_models(tmp_path: Path) -> None:
    title = build_tag_title("TS", candidate_id="ts_001", source="test", frame=4)
    assert title == "TAG: TS | candidate_id=ts_001 | source=test | frame=004"
    parsed = parse_tag_comment(title)
    assert parsed == {
        "tag": "TS",
        "candidate_id": "ts_001",
        "source": "test",
        "frame": "004",
    }

    result_task = tmp_path / "result_task"
    _write_xyz(result_task / "RESULT" / "structures" / "ts_001.xyz", "result TS")
    _write_xyz(result_task / "RESULT" / "structures" / "int_001.xyz", "result INT")
    (result_task / "RESULT" / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": [
                    {
                        "id": "candidate_ts_001",
                        "label": "TS candidate",
                        "path": "structures/ts_001.xyz",
                        "kind": "structure",
                        "role": "transition_state",
                        "candidate_id": "ts_001",
                    },
                    {
                        "id": "candidate_int_001",
                        "label": "Minimum candidate",
                        "path": "structures/int_001.xyz",
                        "kind": "structure",
                        "role": "minimum",
                        "candidate_id": "int_001",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    result_items = load_items_from_result_manifest(result_task)
    assert [(item.candidate_id, item.role) for item in result_items] == [
        ("ts_001", StructureRole.TRANSITION_STATE),
        ("int_001", StructureRole.MINIMUM),
    ]

    legacy_payload: JsonObject = json.loads(
        (FIXTURES / "legacy_s2_path_manifest.json").read_text(encoding="utf-8")
    )
    legacy_task = tmp_path / "legacy_task" / "RESULT" / "mechanism"
    _write_xyz(legacy_task / "input" / "ts_legacy.xyz", "legacy TS")
    legacy_payload["recommendations"]["ts"][0]["geometry_path"] = "input/ts_legacy.xyz"
    legacy_manifest = legacy_task / "s2_path_manifest.json"
    legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    legacy_manifest.write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy_items, legacy_read = load_items_from_s2_path_manifest(legacy_manifest)
    assert legacy_read["schema_version"] == "s2_path_v2"
    assert [(item.candidate_id, item.tag) for item in legacy_items] == [("ts_guess_001", "TS")]

    request_items = load_batch_request(FIXTURES / "batch_structures_v1.json")
    assert [(item.item_id, item.role) for item in request_items] == [
        ("candidate_001", StructureRole.TRANSITION_STATE),
        ("int_001", StructureRole.MINIMUM),
    ]


def test_manifest_without_structures_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir()
    (result_dir / "result_manifest.json").write_text(
        json.dumps(
            {
                "version": 2,
                "workflow": "PESsearch",
                "status": "completed",
                "products": [{"id": "report", "path": "report.json", "kind": "report"}],
            }
        ),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING)
    assert load_items_from_result_manifest(tmp_path) == []
    assert "no structure products" in caplog.text
