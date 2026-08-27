"""Tests for BatchOptimizeEngine and mechanism-free batch models."""
# pyright: basic, reportArgumentType=false, reportIndexIssue=false, reportOptionalSubscript=false, reportCallIssue=false, reportAny=false

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.batch.engine import (
    BatchOptimizeEngine,
    _count_significant_imaginary,
    _PROFILE_STEPS,
    _ts_frequency_judgment,
)
from acp.calculations.batch.models import (
    BatchStructureItem,
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


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture()
def batch_items_ts_int() -> list[BatchStructureItem]:
    return [
        BatchStructureItem(
            item_id="candidate_001",
            name="TS candidate",
            tag="TS",
            xyz="2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="candidate_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT candidate",
            tag="INT",
            xyz="2\nTAG: INT | candidate_id=int_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="int_001",
        ),
    ]


@pytest.fixture()
def engine(tmp_path: Path, fake_backend: object) -> BatchOptimizeEngine:
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    return BatchOptimizeEngine(work_root=work_root, result_root=result_root)


# ── existing model tests ─────────────────────────────────────────────────


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


# ── TS imaginary-frequency judgment unit tests ───────────────────────────


def test_ts_imaginary_judgment_valid() -> None:
    """Exactly one imaginary below -50 cm⁻¹ is valid."""
    valid, msg = _ts_frequency_judgment([-500.0, 100.0, 200.0])
    assert valid is True
    assert msg == ""


def test_ts_imaginary_judgment_too_many() -> None:
    """Multiple significant imaginaries → higher_order_saddle."""
    valid, msg = _ts_frequency_judgment([-500.0, -200.0, 100.0])
    assert valid is False
    assert "higher_order_saddle" in msg


def test_ts_imaginary_judgment_none() -> None:
    """No significant imaginary → ts_no_imaginary."""
    valid, msg = _ts_frequency_judgment([-10.0, 100.0, 200.0])
    assert valid is False
    assert "ts_no_imaginary" in msg


def test_count_significant_imaginary() -> None:
    assert _count_significant_imaginary([-500.0, -10.0, 100.0], cutoff=-50.0) == 1
    assert _count_significant_imaginary([-500.0, -60.0, 100.0], cutoff=-50.0) == 2
    assert _count_significant_imaginary([100.0, 200.0], cutoff=-50.0) == 0


# ── profile steps ────────────────────────────────────────────────────────


def test_four_profiles_have_correct_steps() -> None:
    assert _PROFILE_STEPS["opt_only"] == ("optimize",)
    assert _PROFILE_STEPS["opt_freq"] == ("optimize", "frequency")
    assert _PROFILE_STEPS["opt_freq_sp"] == ("optimize", "frequency", "singlepoint")
    assert _PROFILE_STEPS["opt_freq_sp_thermo"] == (
        "optimize",
        "frequency",
        "singlepoint",
        "thermochemistry",
    )


# ── IRC rejection ────────────────────────────────────────────────────────


def test_reject_irc_in_request() -> None:
    """IRC is not a StepKind; plans with unsupported step kinds are rejected."""
    from acp.calculations.contracts import CalculationPlan, CalculationStep, validate_plan

    plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[],
        steps=[CalculationStep(kind="optimize")],
    )
    errors = validate_plan(plan)
    assert errors == []

    bad_plan = CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[],
        steps=[{"kind": "irc"}],
    )
    errors = validate_plan(bad_plan)
    assert len(errors) == 1
    assert "irc" in errors[0].lower()


# ── engine happy path (opt_freq_sp_thermo, mixed TS+INT) ────────────────


def test_mixed_ts_int_opt_freq_sp_thermo(
    tmp_path: Path,
    fake_backend: object,
) -> None:
    """QA happy: 2 items TS+INT → RESULT/structures products + manifest."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"

    items = [
        BatchStructureItem(
            item_id="candidate_001",
            name="TS candidate",
            tag="TS",
            xyz="2\nTAG: TS | candidate_id=candidate_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="candidate_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT candidate",
            tag="INT",
            xyz="2\nTAG: INT | candidate_id=int_001\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="int_001",
        ),
    ]

    ts_freq_log = work_root / "03_OPT" / "batch" / "candidate_001" / "frequency" / "frequency.log"
    int_freq_log = work_root / "03_OPT" / "batch" / "int_001" / "frequency" / "frequency.log"
    ts_freq_log.parent.mkdir(parents=True, exist_ok=True)
    ts_freq_log.write_text("freq output", encoding="utf-8")
    int_freq_log.parent.mkdir(parents=True, exist_ok=True)
    int_freq_log.write_text("freq output", encoding="utf-8")

    fake_backend.set_results(
        "frequency",
        [
            QCResult(
                success=True,
                energy=-1.1,
                coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
                symbols=["H", "H"],
                frequencies=[-500.0, 100.0, 200.0],
                has_frequencies=True,
                log_file=str(ts_freq_log),
            ),
            QCResult(
                success=True,
                energy=-1.0,
                coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
                symbols=["H", "H"],
                frequencies=[100.0, 200.0, 300.0],
                has_frequencies=True,
                log_file=str(int_freq_log),
            ),
        ],
    )

    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)

    with patch(
        "acp.calculations.primitives.thermochemistry.run_shermo"
    ) as mock_shermo:
        mock_shermo.return_value = {"g_sum": -1.2, "h_sum": -1.1, "s_sum": 0.01}

        outcome = engine.run(items, profile="opt_freq_sp_thermo", charge=0)

    assert len(outcome.items) == 2
    assert all(item.status == "completed" for item in outcome.items)
    assert outcome.errors == []

    result_manifest_path = engine._result_root / "result_manifest.json"
    assert result_manifest_path.exists()
    manifest_data = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    product_ids = [p["id"] for p in manifest_data["products"]]
    assert "batch_candidate_001" in product_ids
    assert "batch_int_001" in product_ids

    structures_dir = engine._result_root / "structures"
    assert (structures_dir / "candidate_001__TAG_TS__optimized.xyz").exists()
    assert (structures_dir / "int_001__TAG_INT__optimized.xyz").exists()

    method_counts: dict[str, int] = {}
    for call in fake_backend.calls:
        method_counts[call.method] = method_counts.get(call.method, 0) + 1
    assert method_counts.get("optimize", 0) == 1
    assert method_counts.get("transition_state_opt", 0) == 1
    assert method_counts.get("frequency", 0) >= 2
    assert method_counts.get("single_point", 0) >= 2


# ── engine failure isolation ─────────────────────────────────────────────


def test_item_failure_isolated(
    tmp_path: Path,
    fake_backend: object,
) -> None:
    """QA failure: item2 raises → item1 completes, item2 failed, structured record."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)

    items = [
        BatchStructureItem(
            item_id="ts_001",
            name="TS",
            tag="TS",
            xyz="2\nTAG: TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="ts_001",
        ),
        BatchStructureItem(
            item_id="int_001",
            name="INT",
            tag="INT",
            xyz="2\nTAG: INT\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
            candidate_id="int_001",
        ),
    ]

    def _failing_opt(*args: object, **kwargs: object) -> QCResult:
        raise RuntimeError("fake optimize failure")

    fake_backend.optimize = _failing_opt  # type: ignore[method-assign]

    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)
    outcome = engine.run(items, profile="opt_only", charge=0)

    item1 = next(i for i in outcome.items if i.item_id == "ts_001")
    item2 = next(i for i in outcome.items if i.item_id == "int_001")
    assert item1.status == "completed"
    assert item2.status == "failed"
    assert "fake optimize failure" in item2.error

    assert len(outcome.manifest.items) == 2
    assert outcome.manifest.counts["completed"] == 1
    assert outcome.manifest.counts["failed"] == 1


# ── cache hit skips completed ────────────────────────────────────────────


def test_cache_hit_skips_completed(
    tmp_path: Path,
    fake_backend: object,
    batch_items_ts_int: list[BatchStructureItem],
) -> None:
    """Re-run with same profile skips previously completed items."""
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)

    outcome1 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "completed" for item in outcome1.items)
    calls_after_first = len(fake_backend.calls)
    assert calls_after_first > 0

    outcome2 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "skipped" for item in outcome2.items)
    assert len(outcome2.carried_items) == 2
    assert len(fake_backend.calls) == calls_after_first


# ── profile mismatch triggers full re-run ────────────────────────────────


def test_profile_mismatch_full_rerun(
    tmp_path: Path,
    fake_backend: object,
    batch_items_ts_int: list[BatchStructureItem],
) -> None:
    """Changing profile triggers full re-run even with same items."""
    work_root = tmp_path / "task" / "WORK"
    result_root = tmp_path / "task" / "RESULT"
    engine = BatchOptimizeEngine(work_root=work_root, result_root=result_root)

    outcome1 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "completed" for item in outcome1.items)
    calls_after_first = len(fake_backend.calls)

    outcome2 = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "skipped" for item in outcome2.items)
    assert len(fake_backend.calls) == calls_after_first


# ── engine with opt_only profile ─────────────────────────────────────────


def test_opt_only_profile(
    engine: BatchOptimizeEngine,
    batch_items_ts_int: list[BatchStructureItem],
    fake_backend: object,
) -> None:
    """opt_only profile: only optimize calls, no frequency/sp/thermo."""
    outcome = engine.run(batch_items_ts_int, profile="opt_only", charge=0)
    assert all(item.status == "completed" for item in outcome.items)

    methods = [call.method for call in fake_backend.calls]
    assert "frequency" not in methods
    assert "single_point" not in methods


# ── invalid profile rejected ─────────────────────────────────────────────


def test_invalid_profile_rejected(engine: BatchOptimizeEngine) -> None:
    item = BatchStructureItem(
        item_id="x", name="x", tag="INT",
        xyz="2\ncomment\nH 0 0 0\nH 0 0 1\n",
    )
    with pytest.raises(ValueError, match="unknown batch profile"):
        engine.run([item], profile="invalid_profile")


# ── empty items rejected ────────────────────────────────────────────────


def test_empty_items_rejected(engine: BatchOptimizeEngine) -> None:
    with pytest.raises(ValueError, match="at least one"):
        engine.run([], profile="opt_only")


# ── TS frequency failure aborts item ─────────────────────────────────────


def test_ts_frequency_failure_aborts_item(
    engine: BatchOptimizeEngine,
    fake_backend: object,
) -> None:
    """TS with no significant imaginary frequencies → item fails."""
    from tests.conftest import FakeBackend

    assert isinstance(fake_backend, FakeBackend)
    ts_item = BatchStructureItem(
        item_id="ts_001",
        name="TS",
        tag="TS",
        xyz="2\nTAG: TS\nH 0.0 0.0 0.0\nH 0.0 0.0 0.7\n",
        candidate_id="ts_001",
    )
    # All positive frequencies → TS judgment fails
    fake_backend.set_result(
        "frequency",
        QCResult(
            success=True,
            energy=-1.1,
            coordinates=np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.7]]),
            symbols=["H", "H"],
            frequencies=[100.0, 200.0, 300.0],
            has_frequencies=True,
        ),
    )

    outcome = engine.run([ts_item], profile="opt_freq", charge=0)
    assert outcome.items[0].status == "failed"
    assert "ts_no_imaginary" in outcome.items[0].error
