"""Round-7 #1 — stale cached release recovery at the local singlepoint freeze.

The long-lived runner caches the first :class:`LocalRelease` it builds.  When
the live checkout changes afterwards (merge, deploy, uncommitted edit) that
identity no longer matches the tree and every default-envelope submission
failed with ``local release is stale`` until the service was restarted.

The contract locked here:

1. a stale cached release is discarded, the identity is rebuilt ONCE from the
   live tree through the normal ``build_local_release`` path, and the freeze is
   retried ONCE — the submission succeeds and the frozen group binds the NEW
   release id/digest;
2. nothing is weakened: the old release directory is left byte-identical, a
   tampered release is still a typed refusal (never repaired), and
   ``ACP_ALLOW_UNVERSIONED`` is not consulted on the default path;
3. an explicitly injected release is authoritative — a stale pin fails loudly
   instead of being silently swapped;
4. a second failure is a typed :class:`StaleReleaseRefreshError` with the
   ``rule=stale_release`` params, both digests and the exact administrator
   command;
5. BatchOptimize (legacy path) never touches the release cache.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from acp.calculations import attempt_freeze as freeze
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.runner import JobRunner
from acp_services.runtime.execution_chain import load_frozen_group

_XYZ = """3
water
O 0.000000 0.000000 0.000000
H 0.960000 0.000000 0.000000
H -0.240000 0.930000 0.000000
"""


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _project(tmp_path: Path) -> Path:
    """Minimal source tree that hashes as a valid execution release."""
    project = tmp_path / "project"
    _write(project / "src/acp/__init__.py", '"""acp"""\n')
    _write(project / "src/acp/a.py", "A = 1\n")
    _write(project / "src/acp_services/__init__.py", '"""services"""\n')
    _write(project / "src/acp_services/svc.py", "S = 1\n")
    _write(project / "src/cccp/__init__.py", '"""cccp"""\n')
    _write(project / "src/cccp/c.py", "C = 1\n")
    _write(project / "requirements-node.txt", "numpy\n")
    _write(project / "config/defaults.yaml", "x: 1\n")
    return project


def _record(
    *, workflow: str = "singlepoint", attempt: int = 1, job_id: str = "job-r7"
) -> JobRecord:
    method: dict[str, Any] = {}
    spec = JobSpec(
        workflow=workflow,
        name="round7",
        input={"charge": 0, "multiplicity": 1},
        method=method,
    )
    return JobRecord(id=job_id, spec=spec, attempt=attempt, work_dir="")


def _singlepoint_record(*, attempt: int = 1) -> JobRecord:
    record = _record(attempt=attempt)
    record.spec = JobSpec(
        workflow="singlepoint",
        name="round7",
        input={"charge": 0, "multiplicity": 1},
        method={"levels": {"single_point": {"functional": "wB97X-D4", "basis": "def2-SVP"}}},
    )
    return record


def _work(run_root: Path, *, attempt: int = 1) -> Path:
    work = run_root / "job-r7" / "WORK"
    _write(work / "input.xyz", _XYZ)
    return work


def _runner(project: Path, tmp_path: Path, release: freeze.LocalRelease | None = None) -> JobRunner:
    runner = JobRunner()
    runner._repo_root = project  # type: ignore[attr-defined]
    runner._default_release = release  # type: ignore[attr-defined]
    return runner


def _freeze_once(runner: JobRunner, work: Path, tmp_path: Path, record: JobRecord | None = None):
    log = JobEventLog(tmp_path / "events" / "events.jsonl")
    runner._ensure_default_frozen_attempt(  # type: ignore[attr-defined]
        record or _singlepoint_record(),
        work,
        str(work / "input.xyz"),
        log,
    )
    return log


# ── 1. automatic refresh ────────────────────────────────────────────────


def test_stale_cached_release_is_refreshed_once_and_freeze_succeeds(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    work = _work(run_root)

    stale = freeze.build_local_release(project, run_root)
    stale.assert_current()
    runner = _runner(project, tmp_path, stale)

    # The live tree changes after the cache was built (deploy / merge / edit).
    _write(project / "src/acp_services/svc.py", "S = 2\n")

    with pytest.raises(freeze.StaleReleaseError):
        stale.assert_current()

    log = _freeze_once(runner, work, tmp_path)

    refreshed = runner._default_release  # type: ignore[attr-defined]
    assert refreshed is not None
    assert refreshed.release_id != stale.release_id
    assert refreshed.release_digest != stale.release_digest
    refreshed.assert_current()

    # New immutable release published next to the untouched old one.
    assert (run_root / "releases" / stale.release_id).is_dir()
    assert (run_root / "releases" / refreshed.release_id).is_dir()

    group = load_frozen_group(work)
    assert group.manifest.release_id == refreshed.release_id
    assert group.manifest.release_digest == refreshed.release_digest

    events = log.read_all()
    assert events[-1]["type"] == "attempt.frozen"
    assert events[-1]["release_id"] == refreshed.release_id
    assert events[-1]["release_refreshed"] is True


def test_cold_release_build_is_unchanged_and_not_marked_refreshed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    work = _work(run_root)

    runner = _runner(project, tmp_path)
    log = _freeze_once(runner, work, tmp_path)

    release = runner._default_release  # type: ignore[attr-defined]
    assert release is not None
    assert release.release_id == freeze.local_release_identity(project).release_id
    assert (run_root / "releases" / release.release_id).is_dir()
    group = load_frozen_group(work)
    assert group.manifest.release_id == release.release_id
    assert "release_refreshed" not in log.read_all()[-1]


def test_refresh_is_idempotent_for_an_unchanged_tree(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    first = freeze.build_local_release(project, run_root)
    again = freeze.refresh_local_release(project, run_root)
    assert again.release_id == first.release_id
    assert again.release_digest == first.release_digest
    assert again.root == first.root


# ── 2. the guardrails stay intact ───────────────────────────────────────


def test_tampered_release_is_still_a_typed_refusal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    release = freeze.build_local_release(project, run_root)

    member = release.root / "src/acp/a.py"
    member.write_text("A = 999\n", encoding="utf-8")

    with pytest.raises(freeze.ReleaseIdentityError):
        freeze.refresh_local_release(project, run_root)
    # The refusal never repairs or overwrites the published directory.
    assert member.read_text(encoding="utf-8") == "A = 999\n"


def test_unversioned_escape_hatch_is_not_used_on_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACP_ALLOW_UNVERSIONED", raising=False)
    project = tmp_path / "empty-project"
    (project / "src/acp").mkdir(parents=True)  # source layout, zero source files
    with pytest.raises(freeze.ReleaseIdentityError):
        freeze.refresh_local_release(project, tmp_path / "runs")
    assert not (tmp_path / "runs" / "releases").exists()


# ── 3. an injected release stays authoritative ──────────────────────────


def test_injected_stale_release_is_never_silently_replaced(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    work = _work(run_root)
    injected = freeze.build_local_release(project, run_root)

    runner = JobRunner()
    runner._repo_root = project  # type: ignore[attr-defined]
    runner.freeze_release = injected  # type: ignore[attr-defined]

    _write(project / "src/acp_services/svc.py", "S = 2\n")

    with pytest.raises(freeze.StaleReleaseError):
        _freeze_once(runner, work, tmp_path)
    # No new release was materialised behind the explicit pin (``.staging`` is
    # the never-published builder scratch directory, not a release).
    published = sorted(
        p.name for p in (run_root / "releases").iterdir() if p.name != ".staging"
    )
    assert published == [injected.release_id]


# ── 4. typed double-failure error ───────────────────────────────────────


def test_second_stale_failure_is_typed_with_admin_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    work = _work(run_root)
    cached = freeze.build_local_release(project, run_root)
    runner = _runner(project, tmp_path, cached)

    real_build = freeze.build_freeze_spec
    calls = {"freeze": 0}

    def always_stale(*args: Any, **kwargs: Any) -> Any:
        calls["freeze"] += 1
        if calls["freeze"] <= 2:
            raise freeze.StaleReleaseError("local release is stale: simulated")
        return real_build(*args, **kwargs)

    monkeypatch.setattr(freeze, "build_freeze_spec", always_stale)

    with pytest.raises(freeze.StaleReleaseRefreshError) as excinfo:
        _freeze_once(runner, work, tmp_path)

    error = excinfo.value
    assert calls["freeze"] == 2  # original attempt + exactly one retry
    assert error.rule == "stale_release"
    params = error.params
    assert params["rule"] == "stale_release"
    assert params["stale_release_id"] == cached.release_id
    assert params["stale_release_digest"] == cached.release_digest
    assert params["live_release_id"]
    assert len(params["live_release_digest"]) == 64
    assert params["live_release_digest"] == freeze.local_release_identity(project).release_digest
    assert params["admin_command"] == freeze.local_release_admin_command(project, run_root)
    message = str(error)
    assert "refresh_local_release" in message
    assert params["admin_command"] in message
    assert cached.release_digest in message
    # The live tree itself was never mutated by the failed attempt.
    assert not (work / "00_RUNTIME" / "frozen" / "frozen_group.json").exists()


def test_refresh_failure_is_wrapped_with_the_admin_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    work = _work(run_root)
    cached = freeze.build_local_release(project, run_root)
    runner = _runner(project, tmp_path, cached)

    def stale(*args: Any, **kwargs: Any) -> Any:
        raise freeze.StaleReleaseError("local release is stale: simulated")

    def broken_refresh(*args: Any, **kwargs: Any) -> Any:
        raise freeze.ReleaseIdentityError("release directory is not writable")

    monkeypatch.setattr(freeze, "build_freeze_spec", stale)
    monkeypatch.setattr(freeze, "refresh_local_release", broken_refresh)

    with pytest.raises(freeze.StaleReleaseRefreshError) as excinfo:
        _freeze_once(runner, work, tmp_path)

    assert excinfo.value.params["rule"] == "stale_release"
    assert "not writable" in str(excinfo.value)
    assert excinfo.value.params["admin_command"] in str(excinfo.value)


# ── 5. legacy BatchOptimize is out of scope ─────────────────────────────


def test_batch_optimize_never_builds_or_refreshes_a_release(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run_root = tmp_path / "runs"
    work = _work(run_root)
    runner = _runner(project, tmp_path)

    log = _freeze_once(runner, work, tmp_path, _record(workflow="BatchOptimize"))

    assert runner._default_release is None  # type: ignore[attr-defined]
    assert not (run_root / "releases").exists()
    assert log.read_all() == []


def test_local_release_admin_command_is_runnable_shape() -> None:
    command = freeze.local_release_admin_command("/tmp/project", "/tmp/runs")
    assert command.startswith("/")
    assert "-c " in command
    assert "refresh_local_release" in command
    assert "/tmp/project" in command
    assert "/tmp/runs" in command
