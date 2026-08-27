from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.store import JobStore


@dataclass(frozen=True, slots=True)
class _LayoutRow:
    record_id: str
    study_id: str
    work_dir: Path
    study_dir: Path
    expected_s2_root: Path | None = None
    expected_ts_root: Path | None = None


def _write_study(study_dir: Path, study_id: str) -> None:
    study_dir.mkdir(parents=True, exist_ok=True)
    payload = {"study_id": study_id, "status": "completed"}
    (study_dir / "study.json").write_text(json.dumps(payload), encoding="utf-8")
    (study_dir / "reaction.json").write_text("{}", encoding="utf-8")


@pytest.fixture
def layout_rows(tmp_path: Path) -> tuple[JobStore, tuple[_LayoutRow, ...]]:
    store = JobStore(tmp_path / "jobs.sqlite3")
    rows: list[_LayoutRow] = []

    legacy_task = tmp_path / "legacy-mechanism-task"
    legacy_study = legacy_task / "mechanism_study" / "study-legacy"
    _write_study(legacy_study, "study-legacy")
    rows.append(
        _LayoutRow(
            record_id="legacy-mechanism",
            study_id="study-legacy",
            work_dir=legacy_study,
            study_dir=legacy_study,
        )
    )

    v2_task = tmp_path / "ethanol_PESsearch_notes"
    v2_study = v2_task / "WORK" / "08_ANALYSIS"
    _write_study(v2_study, "study-v2")
    rows.append(
        _LayoutRow(
            record_id="v2-task-name",
            study_id="study-v2",
            work_dir=v2_task,
            study_dir=v2_study,
        )
    )

    for stage, expected_root_name in (("s2", "s2"), ("s3", "s3s4"), ("s4", "s3s4")):
        task_root = tmp_path / f"legacy-{stage}-task"
        study_dir = task_root / "mechanism_study" / f"study-{stage}"
        _write_study(study_dir, f"study-{stage}")
        stage_root = study_dir / "calc" / expected_root_name
        work_dir = stage_root / stage
        work_dir.mkdir(parents=True)
        rows.append(
            _LayoutRow(
                record_id=f"legacy-{stage}",
                study_id=f"study-{stage}",
                work_dir=work_dir,
                study_dir=study_dir,
                expected_s2_root=stage_root if stage == "s2" else None,
                expected_ts_root=stage_root if stage in {"s3", "s4"} else None,
            )
        )

    for row in rows:
        store.create(
            JobRecord(
                id=row.record_id,
                spec=JobSpec(
                    workflow="mechanism",
                    name=row.record_id,
                    method={"study_id": row.study_id},
                ),
                work_dir=str(row.work_dir),
            )
        )
    return store, tuple(rows)


def _stored_work_dir(store: JobStore, record_id: str) -> str:
    record = store.get(record_id)
    assert record is not None
    return record.work_dir


def test_legacy_workdir_resolves_readonly(
    layout_rows: tuple[JobStore, tuple[_LayoutRow, ...]],
) -> None:
    from acp.compat.legacy.layouts import find_reaction_json, find_study_layout

    store, rows = layout_rows
    before_work_dirs = {row.record_id: _stored_work_dir(store, row.record_id) for row in rows}
    before_files = {
        path: path.read_bytes()
        for row in rows
        for path in row.study_dir.rglob("*")
        if path.is_file()
    }

    for row in rows:
        layout = find_study_layout(row.work_dir, row.study_id)
        assert layout is not None
        assert layout.study_id == row.study_id
        assert layout.study_json == row.study_dir / "study.json"
        assert find_reaction_json(row.work_dir, row.study_id) == row.study_dir / "reaction.json"
        if row.work_dir.name == "ethanol_PESsearch_notes":
            assert layout.legacy is False
        else:
            assert layout.legacy is True
        if row.expected_s2_root is not None:
            assert layout.s2_root == row.expected_s2_root
        if row.expected_ts_root is not None:
            assert layout.ts_root == row.expected_ts_root

    after_work_dirs = {row.record_id: _stored_work_dir(store, row.record_id) for row in rows}
    after_files = {
        path: path.read_bytes()
        for row in rows
        for path in row.study_dir.rglob("*")
        if path.is_file()
    }
    assert after_work_dirs == before_work_dirs
    assert after_files == before_files


def test_non_task_dir_returns_none(tmp_path: Path) -> None:
    from acp.compat.legacy.layouts import find_reaction_json, find_study_layout

    unrelated = tmp_path / "not-a-task"
    unrelated.mkdir()

    assert find_study_layout(unrelated) is None
    assert find_reaction_json(unrelated, "missing-study") is None


def test_new_task_cannot_reuse_legacy_dir(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "mechanism_study" / "study-legacy"
    legacy_dir.mkdir(parents=True)
    spec = JobSpec(
        workflow="mechanism",
        name="study-legacy",
        molecule_name="study-legacy",
        task_name="mechanism",
        remark="resume",
    )

    task_dir_name = spec.task_dir_name()

    assert task_dir_name == "study-legacy_mechanism_resume"
    assert task_dir_name != legacy_dir.name
    assert "mechanism_study" not in task_dir_name


def test_legacy_fallback_can_be_disabled(
    layout_rows: tuple[JobStore, tuple[_LayoutRow, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import acp.compat.legacy.layouts as layouts

    _, rows = layout_rows
    legacy = rows[0]
    monkeypatch.setattr(layouts, "LEGACY_FALLBACK_ENABLED", False)

    assert layouts.find_study_layout(legacy.work_dir, legacy.study_id) is None
    assert layouts.find_reaction_json(legacy.work_dir, legacy.study_id) is None
