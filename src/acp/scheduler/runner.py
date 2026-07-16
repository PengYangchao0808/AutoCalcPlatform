"""
Scheduler Runner
================

Maps a :class:`JobSpec` to a concrete execution. Real workflows (conformer,
nmr, benchmark, mechanism) run as ``python -m acp.cli run <workflow>`` subprocesses for
crash isolation and clean PID-based cancellation. The ``fake`` workflow runs
in-process so the workbench is demoable without QC binaries.

Stage progress is observed by polling the workflow's ``state.json`` and emitting
``stage.*`` events; stdout/stderr are captured to log files.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from acp.chem.embedding import smiles_to_xyz, xyz_to_multiframe_demo
from acp.scheduler.artifacts import ArtifactRegistry, capture_stage_artifacts
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec
from acp.scheduler.provenance import Provenance, build_provenance_for_job
from acp.scheduler.stage_tasks import StageTaskObserver, StageTaskStore

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 1.0


class JobRunnerRemoteProtocol(Protocol):
    """Structural type shared by :class:`~acp.scheduler.remote.runner.RemoteJobRunner`.

    Avoids importing ``acp.scheduler.remote`` (which requires paramiko) at
    module load time when remote execution is not configured.
    """

    def run(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int: ...


class LocalCleanupProtocol(Protocol):
    """Structural type for :class:`~acp.scheduler.local_cleanup.LocalCleanup`.

    Avoids importing the concrete class at module load time.  Only
    :meth:`pre_submit_housekeeping` is invoked from the runner.
    """

    def pre_submit_housekeeping(self) -> Any: ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_workflow_state(work_dir: Path) -> Path | None:
    """Locate the workflow's ``state.json``, preferring the shallowest match.

    Real workflows nest state.json one level under the output dir
    (``<output>/<mol_id>/state.json``); NMR additionally writes an inner
    conformer state at ``<output>/conformer/<id>/state.json``. Shallowest-first
    selection picks the correct (outer) state in both cases. The ``fake``
    workflow writes directly at the root.
    """
    root_state = work_dir / "state.json"
    if root_state.exists():
        return root_state
    candidates = list(work_dir.rglob("state.json"))
    if not candidates:
        return None

    def _key(path: Path) -> tuple[int, float]:
        try:
            depth = len(path.relative_to(work_dir).parts)
            mtime = path.stat().st_mtime
        except OSError:
            return (99, 0.0)
        return (depth, -mtime)

    return min(candidates, key=_key)


def materialize_job_input(
    inp: dict[str, Any],
    inputs_dir: Path,
    run_root: Path,
) -> Path | None:
    source_type = inp.get("source_type", "")
    source = inp.get("source") or inp.get("input") or inp.get("smiles") or ""

    if not source:
        return None

    inputs_dir.mkdir(parents=True, exist_ok=True)
    dest = inputs_dir / "input.xyz"

    if source_type == "xyz_text":
        dest.write_text(str(source), encoding="utf-8")
        return dest

    if source_type == "structure_asset":
        candidate = (run_root / str(source)).resolve()
        try:
            candidate.relative_to(run_root.resolve())
        except ValueError:
            raise ValueError(f"Asset path escapes run_root: {source}")
        if not candidate.is_file():
            raise ValueError(f"Asset file not found: {source}")
        shutil.copy2(candidate, dest)
        return dest

    if source_type == "smiles" or _looks_like_smiles(str(source)):
        from acp.chem.embedding import smiles_to_xyz

        xyz = smiles_to_xyz(str(source))
        dest.write_text(xyz, encoding="utf-8")
        return dest

    if source.endswith(".xyz") or source.endswith(".gjf") or source.endswith(".sdf"):
        p = Path(source)
        if p.is_file():
            shutil.copy2(p, dest)
            return dest

    from acp.chem.embedding import smiles_to_xyz

    try:
        xyz = smiles_to_xyz(str(source))
        dest.write_text(xyz, encoding="utf-8")
        return dest
    except Exception:
        return None


def _looks_like_smiles(s: str) -> bool:
    stripped = s.strip()
    if not stripped or "\n" in stripped or len(stripped) > 200:
        return False
    if stripped[0].isdigit():
        return False
    return True


class JobRunner:
    """Execute jobs as subprocesses (or in-process for the ``fake`` workflow)."""

    def __init__(
        self,
        python_executable: str | None = None,
        stage_task_observer: StageTaskObserver | None = None,
        remote_runner: JobRunnerRemoteProtocol | None = None,
        local_cleanup: LocalCleanupProtocol | None = None,
    ):
        self.python = python_executable or sys.executable
        self.stage_task_observer = stage_task_observer
        # When set, eligible jobs are dispatched to a remote compute node
        # instead of a local subprocess.  Populated by ``JobManager`` when
        # ``remote_config.is_remote`` is True.
        self.remote_runner = remote_runner
        # Local disk-protection manager (Phase 5B).  When set, the local
        # branch runs pre-submit housekeeping before materializing input.
        # Populated by ``JobManager`` when ``local_retention.enabled``.
        self.local_cleanup = local_cleanup

    def _should_run_remote(self, spec: JobSpec) -> bool:
        """True when this job should be dispatched to a remote compute node.

        Requires ``remote_runner`` to be configured (set by
        :class:`~acp.scheduler.manager.JobManager` when
        ``remote_config.is_remote``) **and** the workflow to be one that
        has a remote CLI mapping (``fake`` is always local).
        """
        if self.remote_runner is None:
            return False
        return spec.workflow != "fake"

    def run(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        """Run the job. Returns process exit code (0 = success)."""
        work_dir = Path(record.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        # Remote dispatch — the fake workflow always stays local so the
        # workbench remains demoable without QC binaries or SSH access.
        if self._should_run_remote(record.spec):
            assert self.remote_runner is not None
            return self.remote_runner.run(record, event_log, cancel_event)

        (work_dir / "inputs").mkdir(exist_ok=True)
        (work_dir / "work").mkdir(exist_ok=True)
        (work_dir / "results").mkdir(exist_ok=True)

        observer = self._observer_for_record(record)
        observer.initialize_job_stages(record.id, record.spec)
        event_log.append("job.started", job_id=record.id, workflow=record.spec.workflow)

        try:
            if record.spec.workflow == "fake":
                exit_code = self._run_fake(record, event_log, cancel_event)
            else:
                exit_code = self._run_subprocess(record, event_log, cancel_event)
        except Exception as exc:
            logger.exception("Job %s crashed", record.id)
            record.error = str(exc)
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            observer.finalize_job(record.id, "failed")
            return 1

        if cancel_event.is_set():
            event_log.append("job.cancelled", job_id=record.id)
        elif exit_code == 0:
            event_log.append("job.completed", job_id=record.id, exit_code=exit_code)
        else:
            event_log.append("job.failed", job_id=record.id, exit_code=exit_code)

        final_status = "failed"
        if cancel_event.is_set() and exit_code != 0:
            final_status = "cancelled"
        elif exit_code == 0:
            final_status = "completed"
        observer.finalize_job(record.id, final_status)
        return exit_code

    def _run_subprocess(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        work_dir = Path(record.work_dir)
        inputs_dir = work_dir / "inputs"
        inputs_dir.mkdir(exist_ok=True)
        run_root = work_dir.parent.parent

        # Phase 5B: local disk-pressure housekeeping before materializing
        # input.  When the run_root filesystem is critically full the job
        # is rejected gracefully (exit_code=1) instead of running out of
        # disk mid-computation.  Local mode does NOT raise — there is no
        # "switch node" option, so we just fail the job.
        skip = self._pre_submit_housekeeping_local(record, event_log)
        if skip:
            return 1

        materialized = None
        try:
            materialized = materialize_job_input(record.spec.input, inputs_dir, run_root)
        except Exception as exc:
            event_log.append("job.failed", job_id=record.id, error=str(exc))
            return 1

        effective_input_path = (
            str(materialized)
            if materialized
            else (record.spec.input.get("source") or record.spec.input.get("input") or "")
        )
        cmd = self._build_cmd(record.spec, work_dir, effective_input_path)
        stdout_path = work_dir / "stdout.log"
        stderr_path = work_dir / "stderr.log"

        event_log.append("process.starting", job_id=record.id, cmd=" ".join(cmd))

        with (
            stdout_path.open("w", encoding="utf-8") as out,
            stderr_path.open("w", encoding="utf-8") as err,
        ):
            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                cmd,
                stdout=out,
                stderr=err,
                cwd=str(work_dir),
                env=env,
            )
            record.pid = proc.pid

        self._observer_for_record(record).initialize_job_stages(record.id, record.spec)
        exit_code = self._monitor(record, proc, event_log, cancel_event)
        if exit_code == 0:
            self._capture_artifacts(record, work_dir)
            self._store_provenance(record, command_line=" ".join(cmd))
        return exit_code

    def _monitor(
        self,
        record: JobRecord,
        proc: subprocess.Popen[Any],
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        seen_stages: set[str] = set()
        observer = self._observer_for_record(record)

        while True:
            state_path = find_workflow_state(Path(record.work_dir))
            self._observe_state(record, event_log, state_path, seen_stages)
            observer.poll_and_mirror(record.id, Path(record.work_dir))
            if cancel_event.is_set():
                self._terminate(proc)
                break
            ret = proc.poll()
            if ret is not None:
                record.exit_code = ret
                state_path = find_workflow_state(Path(record.work_dir))
                self._observe_state(record, event_log, state_path, seen_stages)
                observer.poll_and_mirror(record.id, Path(record.work_dir))
                break
            time.sleep(_POLL_INTERVAL)

        if proc.poll() is None:
            proc.wait(timeout=10)
        return proc.returncode if proc.returncode is not None else 1

    def _observe_state(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        state_path: Path | None,
        seen: set[str],
    ) -> None:
        if state_path is None or not state_path.exists():
            return
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        record.current_stage = data.get("current_stage")
        stages = data.get("stages") if isinstance(data.get("stages"), dict) else {}
        total = max(len(stages), 1)
        done = sum(
            1
            for stage in stages.values()
            if isinstance(stage, dict) and stage.get("status") == "completed"
        )
        record.progress = round(done / total, 3)

        for name, info in stages.items():
            if not isinstance(info, dict):
                continue
            status = info.get("status")
            if status == "running" and f"running:{name}" not in seen:
                seen.add(f"running:{name}")
                event_log.append("stage.started", job_id=record.id, stage=name)
            elif status == "completed" and f"done:{name}" not in seen:
                seen.add(f"done:{name}")
                event_log.append("stage.completed", job_id=record.id, stage=name)
            elif status == "failed" and f"failed:{name}" not in seen:
                seen.add(f"failed:{name}")
                event_log.append(
                    "stage.failed",
                    job_id=record.id,
                    stage=name,
                    error=str(info.get("error", "")),
                )

    def _terminate(self, proc: subprocess.Popen[Any]) -> None:
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            logger.warning("Failed to terminate subprocess", exc_info=True)

    def _pre_submit_housekeeping_local(
        self,
        record: JobRecord,
        event_log: JobEventLog,
    ) -> bool:
        """Run local disk-pressure housekeeping before a local submission.

        Delegates to the injected :class:`LocalCleanup` (Phase 5B) when
        one is configured.  Fail-open for transient errors: a stat or
        sweep failure is logged but does **not** block submission.  Only
        the explicit ``should_skip`` decision blocks it.

        Unlike the remote path, the local branch **returns** ``True`` to
        signal "skip submission" rather than raising — there is no other
        node to fall back to, so the job is simply marked FAILED with a
        disk-full error message.

        Returns:
            ``True`` when submission must be skipped (disk full).
        """
        if self.local_cleanup is None:
            return False

        try:
            decision = self.local_cleanup.pre_submit_housekeeping()
        except Exception as exc:
            # Defensive: housekeeping must never crash the job.  Log and
            # proceed (fail-open).
            logger.warning("Local housekeeping crashed: %s", exc)
            event_log.append(
                "local.housekeeping_error",
                job_id=record.id,
                error=str(exc),
            )
            return False

        removed = len(decision.cleanup.work_dirs_removed) if decision.cleanup else 0
        errors = len(decision.cleanup.errors) if decision.cleanup else 0
        event_log.append(
            "local.housekeeping",
            job_id=record.id,
            disk_before=decision.disk_usage_before,
            disk_after=decision.disk_usage_after,
            should_skip=decision.should_skip,
            removed_dirs=removed,
            cleanup_errors=errors,
            reason=decision.reason,
        )

        if decision.should_skip:
            record.error = (
                f"Local disk full ({decision.disk_usage_after}%), "
                f"submission blocked: {decision.reason}"
            )
            logger.warning("Job %s blocked by disk pressure: %s", record.id, decision.reason)
            return True

        return False

    def _build_cmd(self, spec: JobSpec, work_dir: Path, input_path: str = "") -> list[str]:
        wf = spec.workflow
        if wf not in ("conformer", "nmr", "benchmark", "mechanism"):
            raise ValueError(f"No subprocess mapping for workflow: {wf}")

        if wf == "benchmark":
            cmd: list[str] = [self.python, "-m", "acp.cli", "benchmark"]
        else:
            cmd = [self.python, "-m", "acp.cli", "run", wf]
        inp = spec.input
        method = spec.method
        res = spec.resources

        source = input_path or inp.get("source") or inp.get("input") or inp.get("smiles") or ""
        if not source:
            raise ValueError(f"{wf} job requires a valid input structure")

        if wf in {"conformer", "mechanism"}:
            cmd += ["--input", str(source), "--output", str(work_dir)]
            if wf == "conformer" and method.get("protocol"):
                cmd += ["--protocol", str(method["protocol"])]
            if spec.name:
                cmd += ["--name", spec.name]
            if wf == "conformer" and method.get("levels"):
                cmd += ["--levels", json.dumps(method["levels"])]
        elif wf == "nmr":
            cmd += ["--input", str(source), "--output", str(work_dir)]
            if method.get("protocol"):
                cmd += ["--protocol", str(method["protocol"])]
            if method.get("backend"):
                cmd += ["--backend", str(method["backend"])]
        else:
            cmd += ["--input", str(source), "--output", str(work_dir)]
            if method.get("benchmark_level"):
                cmd += ["--benchmark-level", str(method["benchmark_level"])]
            if method.get("protocols"):
                cmd += ["--protocols", str(method["protocols"])]

        if spec.config_path:
            cmd += ["--config", str(spec.config_path)]
        if res.get("nproc") is not None:
            cmd += ["--nproc", str(res["nproc"])]
        if res.get("mem"):
            cmd += ["--mem", str(res["mem"])]
        if inp.get("charge") is not None:
            cmd += ["--charge", str(inp["charge"])]
        if inp.get("multiplicity") is not None:
            cmd += ["--multiplicity", str(inp["multiplicity"])]
        return cmd

    def _run_fake(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        cancel_event: threading.Event,
    ) -> int:
        from acp.core.state import WorkflowState

        work_dir = Path(record.work_dir)
        state = WorkflowState(work_dir=work_dir, job_name=record.spec.name or record.id)
        state.initialize(input_source="fake")
        observer = self._observer_for_record(record)
        tasks = observer.initialize_job_stages(record.id, record.spec)
        tasks_by_stage = {task.stage_name: task for task in tasks}
        stages = ["init", "compute", "finalize"]

        try:
            for name in stages:
                if cancel_event.is_set():
                    return 130

                task = tasks_by_stage.get(name)
                if task is None:
                    tasks = observer.initialize_job_stages(record.id, record.spec)
                    tasks_by_stage = {item.stage_name: item for item in tasks}
                    task = tasks_by_stage.get(name)
                if task is None:
                    raise ValueError(f"Missing stage task row for fake stage '{name}'")

                started_at = _utc_now_iso()
                self._write_stage_task_event(
                    work_dir,
                    name,
                    task.task_id,
                    "started",
                    {
                        "task_id": task.task_id,
                        "stage": name,
                        "status": "running",
                        "pid": os.getpid(),
                        "started_at": started_at,
                    },
                )
                observer.poll_and_mirror(record.id, work_dir)
                state.set_stage(name)
                event_log.append("stage.started", job_id=record.id, stage=name)
                event_log.append(
                    "log", job_id=record.id, stream="stdout", line=f"[fake] running {name}"
                )
                time.sleep(0.8)
                if cancel_event.is_set():
                    return 130

                state.complete_stage(name, result={"fake": True})
                self._write_stage_task_event(
                    work_dir,
                    name,
                    task.task_id,
                    "completed",
                    {
                        "task_id": task.task_id,
                        "stage": name,
                        "status": "completed",
                        "pid": os.getpid(),
                        "started_at": started_at,
                        "completed_at": _utc_now_iso(),
                        "exit_code": 0,
                        "result": {"fake": True},
                    },
                )
                observer.poll_and_mirror(record.id, work_dir)
                event_log.append("stage.completed", job_id=record.id, stage=name)

            source_type = record.spec.input.get("source_type", "")
            source = (
                record.spec.input.get("source")
                or record.spec.input.get("input")
                or record.spec.input.get("smiles")
            )
            if not source:
                raise ValueError("fake workflow requires input.source")

            if source_type == "xyz_text":
                xyz = str(source)
                if not xyz.strip().startswith(str(xyz.count("\n"))):
                    pass
            elif source_type == "structure_asset":
                run_root = Path(record.work_dir).parent.parent
                asset_path = (run_root / str(source)).resolve()
                try:
                    asset_path.relative_to(run_root.resolve())
                    xyz = asset_path.read_text(encoding="utf-8")
                except (ValueError, OSError) as exc:
                    raise ValueError(f"Cannot read structure asset: {source}") from exc
            else:
                xyz = smiles_to_xyz(str(source))

            (work_dir / "results" / "input_preview.xyz").write_text(
                xyz,
                encoding="utf-8",
            )

            demo_frames = bool(record.spec.input.get("demo_frames"))
            if demo_frames:
                demo_xyz = xyz_to_multiframe_demo(xyz, frames=3)
                (work_dir / "results" / "demo_frames.xyz").write_text(
                    demo_xyz,
                    encoding="utf-8",
                )

            method_levels = record.spec.method.get("levels") if record.spec.method else None
            if method_levels:
                from acp.catalog import method_levels_to_workflow_config

                schema_id = record.spec.method.get("schema_id", "confsearch")
                wf_config = method_levels_to_workflow_config(
                    method_levels, schema_id, record.spec.workflow
                )
                (work_dir / "results" / "method_config.json").write_text(
                    json.dumps(wf_config, indent=2),
                    encoding="utf-8",
                )

            self._capture_artifacts(record, work_dir)
            self._store_provenance(record)
            state.mark_completed()
            return 0
        except Exception as exc:
            current = str(state.state.get("current_stage") or "unknown")
            error_message = str(exc)
            state.fail_stage(current, error=error_message)
            task = tasks_by_stage.get(current)
            if task is not None:
                self._write_stage_task_event(
                    work_dir,
                    current,
                    task.task_id,
                    "failed",
                    {
                        "task_id": task.task_id,
                        "stage": current,
                        "status": "failed",
                        "pid": os.getpid(),
                        "completed_at": _utc_now_iso(),
                        "exit_code": 1,
                        "error": error_message,
                    },
                )
                observer.poll_and_mirror(record.id, work_dir)
            raise

    def _observer_for_record(self, record: JobRecord) -> StageTaskObserver:
        if self.stage_task_observer is None:
            fallback_db = Path(record.work_dir) / ".acp_stage_tasks.db"
            self.stage_task_observer = StageTaskObserver(StageTaskStore(fallback_db))
        return self.stage_task_observer

    def _capture_artifacts(self, record: JobRecord, work_dir: Path) -> None:
        """Register output files as artifacts after job completion."""
        observer = self.stage_task_observer
        store = getattr(observer, "store", None) if observer is not None else None
        db_path = getattr(store, "db_path", None)
        results_dir = work_dir / "results"
        if db_path is None or not results_dir.exists():
            return

        registry = ArtifactRegistry(db_path)
        artifacts = capture_stage_artifacts(
            registry=registry,
            job_id=record.id,
            task_id=None,
            work_dir=work_dir,
            stage_dir=results_dir,
        )
        if not artifacts:
            return

        result = dict(record.result or {})
        result["artifacts"] = [asdict(artifact) for artifact in artifacts]
        record.result = result

    def _store_provenance(self, record: JobRecord, command_line: str = "") -> None:
        if record.completed_at is None:
            record.completed_at = _utc_now_iso()
        result = dict(record.result or {})
        if command_line:
            result["command_line"] = command_line
        if record.spec.workflow == "fake":
            result.setdefault("backend_name", "fake")
            result.setdefault("method", "demo")
        elif "backend_name" not in result and record.spec.method.get("backend") is not None:
            result["backend_name"] = str(record.spec.method["backend"])
        if "method" not in result:
            method = record.spec.method.get("protocol") or record.spec.method.get("benchmark_level")
            if method is not None:
                result["method"] = str(method)
        record.result = result

        provenance = build_provenance_for_job(record.spec, record)
        record.result = dict(record.result or {})
        record.result["provenance"] = asdict(provenance)
        self._persist_provenance_to_stage_tasks(record, provenance)

    def _persist_provenance_to_stage_tasks(self, record: JobRecord, provenance: Provenance) -> None:
        observer = self.stage_task_observer
        store = getattr(observer, "store", None) if observer is not None else None
        if store is None:
            return
        tasks = store.list_by_job(record.id)
        if not tasks:
            return
        task = tasks[-1]
        task.provenance = asdict(provenance)
        task.updated_at = _utc_now_iso()
        store.update(task)

    def _write_stage_task_event(
        self,
        work_dir: Path,
        stage_name: str,
        task_id: str,
        marker: str,
        payload: dict[str, Any],
    ) -> None:
        stage_dir = work_dir / "stage_tasks" / stage_name
        stage_dir.mkdir(parents=True, exist_ok=True)
        path = stage_dir / f"{task_id}.{marker}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


__all__ = ["JobRunner", "JobRunnerRemoteProtocol", "find_workflow_state", "materialize_job_input"]
