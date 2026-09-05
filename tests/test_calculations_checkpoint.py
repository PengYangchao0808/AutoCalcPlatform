import json
from pathlib import Path

import pytest

from acp.calculations import Checkpoint
from acp.calculations.checkpoint import (
    CheckpointMismatchError,
    load_checkpoint,
    write_checkpoint,
)


def _checkpoint() -> Checkpoint:
    return Checkpoint(
        task_id="task-001",
        workflow="BatchOptimize",
        plan_fingerprint="fingerprint-001",
        step_states=[{"kind": "optimize", "status": "completed"}, "pending"],
        items_state={"item-001": {"status": "completed", "cache_key": "cache-001"}},
        attempts=2,
    )


def test_roundtrip(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    checkpoint = _checkpoint()

    write_checkpoint(checkpoint_dir, checkpoint)

    assert load_checkpoint(checkpoint_dir, checkpoint.plan_fingerprint) == checkpoint


def test_fingerprint_mismatch_raises(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    checkpoint = _checkpoint()
    write_checkpoint(checkpoint_dir, checkpoint)

    checkpoint_path = checkpoint_dir / "checkpoint.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["plan_fingerprint"] = "tampered-fingerprint"
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CheckpointMismatchError):
        load_checkpoint(checkpoint_dir, checkpoint.plan_fingerprint)


def test_corrupt_json_returns_none(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "checkpoint.json").write_text("{not valid json", encoding="utf-8")

    assert load_checkpoint(checkpoint_dir, "fingerprint-001") is None
