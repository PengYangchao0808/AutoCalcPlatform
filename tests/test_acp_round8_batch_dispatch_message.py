"""Round-8 一.1(4): BatchOptimize dispatch names ``input.source_type``.

The submit boundary already refuses ``input.items`` without
``input.source_type="batch_structures"``.  These tests pin the defence-in-depth
guard for direct/legacy callers: the local dispatcher and the remote argv
builder fail with the *same* greppable message before anything is spawned, and
every valid carrier keeps its old behaviour.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from acp.scheduler.jobs import JobSpec
from acp.scheduler.remote.script_gen import build_remote_cli_command
from acp.scheduler.runner import (
    BATCH_ITEMS_SOURCE_TYPE_ERROR,
    JobRunner,
    materialize_job_input,
)

_XYZ = "3\nitem\nO 0 0 0\nH 1 0 0\nH 0 1 0\n"


def _spec(**input_payload: object) -> JobSpec:
    return JobSpec(workflow="BatchOptimize", name="round8", input=input_payload, method={})


def _runner() -> JobRunner:
    return JobRunner(python_executable=sys.executable)


def test_items_without_source_type_are_not_materialized(tmp_path: Path) -> None:
    """The payload shape that used to die late: items exist, nothing is staged."""
    item_input = {"items": [{"xyz": _XYZ, "role": "ts", "tag": "TS"}]}
    materialized = materialize_job_input(item_input, tmp_path / "INPUT", tmp_path)
    assert materialized is None


def test_local_dispatch_names_source_type_and_never_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _record(*args: object, **kwargs: object) -> None:
        spawned.append((args, kwargs))

    monkeypatch.setattr(subprocess, "Popen", _record)
    monkeypatch.setattr(subprocess, "run", _record)

    spec = _spec(items=[{"xyz": _XYZ, "role": "ts", "tag": "TS"}])
    with pytest.raises(ValueError) as caught:
        _runner()._build_cmd(spec, tmp_path)

    message = str(caught.value)
    assert message == BATCH_ITEMS_SOURCE_TYPE_ERROR
    assert message.startswith("BatchOptimize input items were not materialized")
    assert "input.source_type" in message
    assert '"batch_structures"' in message
    assert "from_artifact/items_file" in message
    assert spawned == []


def test_local_dispatch_rejects_items_under_another_source_type(
    tmp_path: Path,
) -> None:
    spec = _spec(
        source_type="xyz_text",
        source=_XYZ,
        items=[{"xyz": _XYZ}],
    )
    with pytest.raises(ValueError) as caught:
        _runner()._build_cmd(spec, tmp_path)
    assert str(caught.value) == BATCH_ITEMS_SOURCE_TYPE_ERROR


def test_remote_dispatch_uses_the_same_message() -> None:
    """The remote builder must not silently downgrade items to one staged XYZ."""
    spec = _spec(items=[{"xyz": _XYZ, "role": "ts", "tag": "TS"}])
    with pytest.raises(ValueError) as caught:
        build_remote_cli_command(spec)
    assert str(caught.value) == BATCH_ITEMS_SOURCE_TYPE_ERROR

    # Even a staged input path (the remote default) must not mask the payload
    # error: otherwise the node runs one structure while items[] is ignored.
    with pytest.raises(ValueError) as caught_with_path:
        build_remote_cli_command(spec, input_path="inputs/input.xyz")
    assert str(caught_with_path.value) == BATCH_ITEMS_SOURCE_TYPE_ERROR


def test_valid_batch_structures_payload_still_dispatches(tmp_path: Path) -> None:
    item_input = {
        "source_type": "batch_structures",
        "items": [{"xyz": _XYZ, "role": "ts", "tag": "TS"}],
    }
    items_path = materialize_job_input(item_input, tmp_path / "INPUT", tmp_path)
    assert items_path is not None and items_path.is_file()

    cmd = _runner()._build_cmd(_spec(**item_input), tmp_path, str(items_path))
    assert cmd[:5] == [sys.executable, "-m", "acp.cli", "run", "BatchOptimize"]
    assert cmd[cmd.index("--items-file") + 1] == str(items_path)
    assert cmd[cmd.index("--layout-mode") + 1] == "single_flat"

    remote = build_remote_cli_command(
        JobSpec(
            workflow="BatchOptimize",
            name="round8",
            input=item_input,
            method={},
            resources={"nproc": 1, "mem": "2GB"},
        ),
        input_path="inputs/batch_items.json",
    )
    assert remote[remote.index("--items-file") + 1] == "inputs/batch_items.json"


def test_artifact_and_items_file_carriers_are_unchanged(tmp_path: Path) -> None:
    artifact_cmd = _runner()._build_cmd(_spec(from_artifact="RESULT/batch.json"), tmp_path)
    assert artifact_cmd[artifact_cmd.index("--from-artifact") + 1] == "RESULT/batch.json"

    items_cmd = _runner()._build_cmd(_spec(items_file="items/batch_items.json"), tmp_path)
    assert items_cmd[items_cmd.index("--items-file") + 1] == "items/batch_items.json"

    remote = build_remote_cli_command(_spec(from_artifact="RESULT/batch.json"))
    assert remote[remote.index("--from-artifact") + 1] == "RESULT/batch.json"


def test_empty_items_list_is_not_a_batch_payload(tmp_path: Path) -> None:
    """An empty ``items`` list must not trip the guard (nothing to materialize)."""
    spec = _spec(items=[], source="CCO")
    staged = materialize_job_input(spec.input, tmp_path / "INPUT", tmp_path)
    assert staged is not None and staged.is_file()
    cmd = _runner()._build_cmd(spec, tmp_path, str(staged))
    assert cmd[cmd.index("--items-file") + 1] == str(staged)
