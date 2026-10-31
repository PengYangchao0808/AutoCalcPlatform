"""Round-5 PR-05: PES publication group — idempotency, recovery, serialization.

The plan's §9.3/§9.4 invariants: one serialized publication per task root, the
same key+payload replays without a new revision, a different payload conflicts,
a crash at any step leaves a recoverable intent (never a half-consumable
selection), and a source rerun between validation and commit fails closed.
"""

from __future__ import annotations

import json
import multiprocessing
from datetime import datetime
from pathlib import Path

import pytest

from acp.calculations.pes.publication import (
    PUBLICATION_INTENT_RELATIVE_PATH,
    PublicationConflictError,
    PublicationInProgressError,
    PublicationRecoveryError,
    load_publication_intent,
)
from acp.calculations.pes.review import (
    PES_REVIEW_RELATIVE_PATH,
    PesReviewError,
    RevisionConflictError,
    load_pes_review,
    save_pes_review,
)
from acp.storage.manifest import ResultManifest

FIXED_NOW = datetime(2026, 9, 3, 12, 0, 0)


def _xyz(comment: str = "frame") -> str:
    return "3\n" + comment + "\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"


@pytest.fixture()
def pes_task(tmp_path: Path) -> Path:
    root = tmp_path
    scan_dir = root / "WORK" / "07_PATH" / "pes_scan_001" / "scan_frames"
    scan_dir.mkdir(parents=True)
    for index in range(3):
        (scan_dir / f"frame_{index:03d}.xyz").write_text(_xyz(), encoding="utf-8")
    frames = [
        {
            "index": index,
            "target_coordinate": 1.0 + index * 0.1,
            "actual_coordinate": 1.0 + index * 0.1,
            "geometry_path": f"scan_frames/frame_{index:03d}.xyz",
            "scan_energy_hartree": -76.0 - index * 0.01,
        }
        for index in range(3)
    ]
    profile = {
        "schema_version": "pes_profile_v2",
        "workflow": "PESsearch",
        "mode": "bond_length_scan",
        "status": "completed",
        "scan_dir": "WORK/07_PATH/pes_scan_001",
        "frames": frames,
        "frames_count": 3,
        "ts_candidates": [],
        "int_candidates": [],
    }
    pes_dir = root / "RESULT" / "pes_search"
    pes_dir.mkdir(parents=True)
    (pes_dir / "pes_profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return root


def _candidates() -> list[dict]:
    return [
        {"frame_index": 1, "role": "TS", "name": "frame_001__TAG_TS"},
        {"frame_index": 2, "role": "INT"},
    ]


def _save(root: Path, **overrides):
    kwargs = {
        "job_id": "job_001",
        "candidates": _candidates(),
        "note": "Stepwise",
        "now": FIXED_NOW,
    }
    kwargs.update(overrides)
    return save_pes_review(root, **kwargs)


# --------------------------------------------------------------------------- #
# happy path: one publication group with intent + receipt
# --------------------------------------------------------------------------- #


def test_publication_writes_group_and_intent(pes_task: Path) -> None:
    payload = _save(pes_task)
    assert payload["revision"] == 1
    assert payload["publication"]["status"] == "published"
    assert payload["publication"]["replayed"] is False
    assert payload["publication"]["intent_path"] == PUBLICATION_INTENT_RELATIVE_PATH
    intent = load_publication_intent(pes_task)
    assert intent is not None and intent["status"] == "published"
    assert intent["target_revision"] == 1
    assert (pes_task / PES_REVIEW_RELATIVE_PATH).is_file()
    manifest = ResultManifest.read(pes_task / "RESULT")
    prefixed = [p for p in manifest.products if p.id.startswith("pes_candidate_")]
    assert len(prefixed) == 2
    assert (pes_task / "RESULT" / "structures" / "pes_ts_frame_001.xyz").is_file()


# --------------------------------------------------------------------------- #
# idempotency: replay vs conflict
# --------------------------------------------------------------------------- #


def test_same_key_and_payload_replays_without_a_new_revision(pes_task: Path) -> None:
    first = _save(pes_task, idempotency_key="key-1")
    second = _save(pes_task, idempotency_key="key-1")
    assert first["revision"] == 1
    assert second["revision"] == 1
    assert second["publication"]["replayed"] is True
    intent = load_publication_intent(pes_task)
    assert intent is not None and intent["status"] == "published"
    assert intent["idempotency_key"] == "key-1"


def test_same_key_with_different_payload_conflicts(pes_task: Path) -> None:
    _save(pes_task, idempotency_key="key-1")
    with pytest.raises(PublicationConflictError):
        _save(
            pes_task,
            idempotency_key="key-1",
            candidates=[{"frame_index": 0, "role": "TS"}],
        )


# --------------------------------------------------------------------------- #
# failure injection: every crash leaves a recoverable intent
# --------------------------------------------------------------------------- #


def test_manifest_failure_leaves_recoverable_intent(
    pes_task: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import acp.calculations.pes.review as review_mod

    def boom(*args, **kwargs):
        raise OSError("simulated manifest write failure")

    monkeypatch.setattr(review_mod, "_update_result_manifest", boom)
    with pytest.raises(PublicationRecoveryError) as excinfo:
        _save(pes_task, idempotency_key="key-recover")
    intent = load_publication_intent(pes_task)
    assert intent is not None and intent["status"] == "failed"
    assert "backup" in intent["completed_stages"]
    assert "structures" in intent["completed_stages"]
    assert "manifest" not in intent["completed_stages"]
    assert excinfo.value.intent["status"] == "failed"
    # no half-published review yet
    assert load_pes_review(pes_task) is None

    monkeypatch.undo()
    recovered = _save(pes_task, idempotency_key="key-recover")
    assert recovered["revision"] == 1
    assert recovered["publication"]["status"] == "published"
    assert load_pes_review(pes_task)["revision"] == 1
    manifest = ResultManifest.read(pes_task / "RESULT")
    assert len([p for p in manifest.products if p.id.startswith("pes_candidate_")]) == 2


def test_review_failure_leaves_structures_but_no_review(
    pes_task: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import acp.calculations.pes.review as review_mod

    def boom(*args, **kwargs):
        raise OSError("simulated review write failure")

    monkeypatch.setattr(review_mod, "_write_json_atomic", boom)
    with pytest.raises(PublicationRecoveryError):
        _save(pes_task, idempotency_key="key-review-fail")
    intent = load_publication_intent(pes_task)
    assert intent is not None and intent["status"] == "failed"
    assert "manifest_pending" in intent["completed_stages"]
    assert "review" not in intent["completed_stages"]
    assert load_pes_review(pes_task) is None

    monkeypatch.undo()
    recovered = _save(pes_task, idempotency_key="key-review-fail")
    assert recovered["revision"] == 1
    assert recovered["publication"]["replayed"] is False


# --------------------------------------------------------------------------- #
# serialization: two concurrent publishers, one revision
# --------------------------------------------------------------------------- #


def _publish_in_subprocess(root: str, queue) -> None:
    from acp.calculations.pes.review import save_pes_review as save

    try:
        payload = save(
            Path(root),
            job_id="job_001",
            candidates=[
                {"frame_index": 1, "role": "TS", "name": "frame_001__TAG_TS"},
                {"frame_index": 2, "role": "INT"},
            ],
            note="Stepwise",
            expected_revision=0,
            idempotency_key="concurrent",
        )
        queue.put(("ok", int(payload["revision"])))
    except (RevisionConflictError, PublicationInProgressError) as exc:
        queue.put(("conflict", type(exc).__name__))
    except Exception as exc:  # pragma: no cover - diagnostic
        queue.put(("error", f"{type(exc).__name__}: {exc}"))


def test_two_processes_publish_exactly_one_revision(pes_task: Path) -> None:
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_publish_in_subprocess, args=(str(pes_task), queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)
    results = [queue.get(timeout=5) for _ in range(2)]
    statuses = sorted(item[0] for item in results)
    assert statuses == ["conflict", "ok"], results
    ok_revision = [item[1] for item in results if item[0] == "ok"][0]
    assert ok_revision == 1
    assert load_pes_review(pes_task)["revision"] == 1


# --------------------------------------------------------------------------- #
# source rerun between validation and commit fails closed
# --------------------------------------------------------------------------- #


def test_source_change_before_commit_is_refused(
    pes_task: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import acp.calculations.pes.review as review_mod

    original = review_mod._validate_candidates

    def mutate_then_validate(*args, **kwargs):
        result = original(*args, **kwargs)
        profile_path = pes_task / "RESULT" / "pes_search" / "pes_profile.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["frames"][0]["scan_energy_hartree"] = -77.0
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        return result

    monkeypatch.setattr(review_mod, "_validate_candidates", mutate_then_validate)
    with pytest.raises(PesReviewError, match="frame source changed"):
        _save(pes_task)
    assert load_pes_review(pes_task) is None


def test_loader_refuses_a_pending_publication_group(
    pes_task: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash after the manifest marker leaves an explicitly unsettled group."""
    from acp.calculations.batch.loaders import load_items_from_result_manifest
    import acp.calculations.pes.review as review_mod

    def boom(*args, **kwargs):
        raise OSError("simulated review write failure")

    monkeypatch.setattr(review_mod, "_write_json_atomic", boom)
    with pytest.raises(PublicationRecoveryError):
        _save(pes_task, idempotency_key="key-unsettled")
    with pytest.raises(ValueError, match="not settled"):
        load_items_from_result_manifest(pes_task)

    monkeypatch.undo()
    recovered = _save(pes_task, idempotency_key="key-unsettled")
    assert recovered["revision"] == 1
    items = load_items_from_result_manifest(pes_task)
    assert len(items) == 2
    manifest = ResultManifest.read(pes_task / "RESULT")
    assert manifest.publication == {"revision": 1, "status": "committed", "digest": manifest.publication["digest"]}
