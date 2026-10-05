import json
from pathlib import Path

from acp.calculations import Checkpoint
from acp.calculations.checkpoint import (
    load_checkpoint,
    write_checkpoint,
)
from acp.calculations.identity import IDENTITY_SCHEMA


def _checkpoint(identity_schema: int | None = None) -> Checkpoint:
    kwargs = {} if identity_schema is None else {"identity_schema": identity_schema}
    return Checkpoint(
        task_id="task-001",
        workflow="BatchOptimize",
        plan_fingerprint="fingerprint-001",
        step_states=[{"kind": "optimize", "status": "completed"}, "pending"],
        items_state={"item-001": {"status": "completed", "cache_key": "cache-001"}},
        attempts=2,
        **kwargs,
    )


def test_roundtrip(tmp_path: Path, caplog) -> None:
    """schema=1 → default returns None (conservative); batch-style compat
    switch + v2 checkpoints round-trip."""
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    checkpoint = _checkpoint()
    write_checkpoint(checkpoint_dir, checkpoint)

    with caplog.at_level("INFO", logger="acp.calculations.checkpoint"):
        assert load_checkpoint(checkpoint_dir, checkpoint.plan_fingerprint) is None
    assert "identity_unverifiable_legacy" in caplog.text

    reused = load_checkpoint(
        checkpoint_dir, checkpoint.plan_fingerprint, allow_legacy_fingerprint=True
    )
    assert reused == checkpoint

    v2 = _checkpoint(IDENTITY_SCHEMA)
    write_checkpoint(checkpoint_dir, v2)
    assert load_checkpoint(checkpoint_dir, v2.plan_fingerprint) == v2


def test_fingerprint_mismatch_raises(tmp_path: Path, caplog) -> None:
    """Historically raised ``CheckpointMismatchError``; D06: never raises —
    both the v2 and the legacy-compat paths return ``None`` + event."""
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    v2 = _checkpoint(IDENTITY_SCHEMA)
    write_checkpoint(checkpoint_dir, v2)

    checkpoint_path = checkpoint_dir / "checkpoint.json"
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["plan_fingerprint"] = "tampered-fingerprint"
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")

    with caplog.at_level("INFO", logger="acp.calculations.checkpoint"):
        assert load_checkpoint(checkpoint_dir, v2.plan_fingerprint) is None
        assert (
            load_checkpoint(checkpoint_dir, v2.plan_fingerprint, allow_legacy_fingerprint=True)
            is None
        )
    assert "identity_fingerprint_mismatch" in caplog.text

    legacy = _checkpoint()
    write_checkpoint(checkpoint_dir, legacy)
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    payload["plan_fingerprint"] = "tampered-fingerprint"
    checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")
    caplog.clear()
    with caplog.at_level("INFO", logger="acp.calculations.checkpoint"):
        assert (
            load_checkpoint(checkpoint_dir, legacy.plan_fingerprint, allow_legacy_fingerprint=True)
            is None
        )
    assert "identity_unverifiable_legacy" in caplog.text


def test_corrupt_json_returns_none(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "WORK" / "00_RUNTIME"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "checkpoint.json").write_text("{not valid json", encoding="utf-8")

    assert load_checkpoint(checkpoint_dir, "fingerprint-001") is None
