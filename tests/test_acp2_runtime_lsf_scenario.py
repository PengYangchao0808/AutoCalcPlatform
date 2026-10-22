"""B11 — simulated scheduler-boundary contract for the LSF singlepoint scenario.

The live LSF run is `NOT_VERIFIED` on this host (no LSF client/queue), so the
remote contract is proved at the SIMULATED scheduler boundary instead of
claiming a generated command as real execution:

* scheduler markers and the verified release binding are in place before bsub;
* a submission id is derived and handed to bsub exactly once;
* a lost bsub reply reconciles the SAME submission/attempt without a second bsub;
* prepare/pull verifies the artifact digest and generation, a cold cache answers
  not_prepared without a fetch, and an old attempt is refused;
* remote reads go through the controlled cache (``_job_read_root``), never the
  remote job's ``record.work_dir``.

Run with: pytest tests/test_acp2_runtime_lsf_scenario.py \\
    tests/test_remote_phase1.py tests/test_remote_phase4.py -q
"""

from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.api.v1_routes import _job_read_root
from acp.results.remote_structure_cache import RemoteStructureCache
from acp.scheduler.events import JobEventLog
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote import ssh as ssh_mod
from acp.scheduler.remote.config import RemoteExecutionConfig
from acp.scheduler.remote.release import ReleaseBinding
from acp.scheduler.remote.runner import (
    RemoteJobRunner,
    RemotePollObservation,
    RemoteSubmissionIndeterminate,
    RemoteSubmissionRejected,
)
from acp.scheduler.remote.sftp import FileStager
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.remote.submission import submission_id_for, submission_lsf_name
from acp.services_data_bootstrap import (
    build_cache_only_resource_reader,
    build_local_single_transfer_authorization,
    build_transfer_pipeline,
)
from acp_services.data import transfer as tx
from acp_services.data.read_root import Availability, Freshness
from tests.test_remote_phase2 import FakeSFTP, FakeSSHClient, make_node
from tests.test_remote_submission_protocol import _make_manager, _remote_meta, _seed_queued

_XYZ = "2\nH2\nH 0 0 0\nH 0 0 0.74\n"
_METHOD = {
    "levels": {"single_point": {"method": "r2SCAN-3c", "basis": "def2-TZVPPD"}},
    "charge": 0,
    "multiplicity": 1,
}
_RELEASE = "rel_b11_simulated"
_RELATIVE = "projB/lsf-singlepoint"
_MANIFEST_REL = "RESULT/result_manifest.json"


def _simulated_local_release(project_root: str | Path):
    """Return a fake local release whose id matches the simulated remote binding.

    The phase-2 fake node advertises ``_RELEASE``; the real ``build_local_release``
    would derive the id from this checkout, which is unrelated to the simulated
    remote release.  Replacing only that builder keeps the release *verification*
    path real (the runner still compares the verified id before freezing).
    """
    from acp.calculations.attempt_freeze import LocalRelease, ReleaseIdentity

    root = Path(project_root)
    return LocalRelease(
        identity=ReleaseIdentity(
            project_root=root,
            release_id=_RELEASE,
            release_digest="sha256:" + "0" * 64,
            files={},
        ),
        root=root,
        unversioned=True,
    )


def _patch_simulated_release(mp: pytest.MonkeyPatch, harness: _LsfHarness) -> None:
    binding = ReleaseBinding(
        node=harness.node.name,
        release_id=_RELEASE,
        release_dir=posixpath.join(harness.node.remote_code_dir, "releases", _RELEASE),
        ref_id="ref-b11",
        source="remote",
    )
    mp.setattr(ssh_mod, "_create_client", lambda node, timeout=30: harness.client)
    mp.setattr(
        "acp.scheduler.remote.runner.verify_existing_release",
        lambda node, release_id, *, stager, ssh, timeout=60: binding,
    )
    mp.setattr(
        "acp.scheduler.remote.runner.release_release_ref",
        lambda node, release_id, ref_id, *, stager, ssh: None,
    )
    mp.setattr(
        "acp.calculations.attempt_freeze.build_local_release",
        lambda project_root, run_root: _simulated_local_release(project_root),
    )


def _record(harness: _LsfHarness, job_id: str) -> tuple[JobRecord, JobEventLog]:
    work_dir = Path(harness.tmp) / "runs" / "projB" / job_id
    work_dir.mkdir(parents=True, exist_ok=True)
    sid = submission_id_for(job_id, 1)
    remote_dir = posixpath.join(harness.node.remote_work_dir, _RELATIVE)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="singlepoint",
            input={"source_type": "xyz_text", "source": _XYZ, "charge": 0, "multiplicity": 1},
            method=_METHOD,
            resources={"cores": 1, "nproc": 1, "mem": "1024MB"},
        ),
        status=JobStatus.STARTING,
        work_dir=str(work_dir),
        result={
            "remote_dir": remote_dir,
            "remote": {
                "schema": 1,
                "relative": _RELATIVE,
                "attempt": 1,
                "node": harness.node.name,
                "submission_id": sid,
                "submit_state": "intent",
                "lease_expires_at": "2099-01-01T00:00:00+00:00",
            },
        },
        attempt=1,
    )
    return record, JobEventLog(work_dir / "events.jsonl")


class _LsfHarness:
    """Real RemoteJobRunner over the phase-2 FakeSSH/FakeSFTP pair."""

    def __init__(self, tmp: str, *, bsub_result: tuple[int, str, str] | None = None) -> None:
        self.tmp = tmp
        self.node = make_node(pinned_release=_RELEASE)
        self.config = RemoteExecutionConfig(
            execution_mode="remote", auto_sync=False, nodes=[self.node]
        )
        self.pool = SSHConnectionPool()
        self.sftp = FakeSFTP()
        self.client = FakeSSHClient(self.sftp)
        self.bsub_calls = 0
        self.bsub_result = bsub_result or (
            0,
            "Job <54321> is submitted to queue <normal>.\n",
            "",
        )
        self.query_handler = None
        self.snapshot: dict[str, object] = {}
        self.order: list[str] = []

        def cmd_handler(cmd: str) -> tuple[int, str, str]:
            if "bsub" in cmd and "<" in cmd:
                self.bsub_calls += 1
                return self.bsub_result
            if self.query_handler is not None:
                result = self.query_handler(cmd)
                if result is not None:
                    return result
            return (0, "", "")

        self.client.cmd_handler = cmd_handler
        self.runner = RemoteJobRunner(
            self.pool,
            self.config,
            stager=FileStager(self.pool),
            poll_interval=0,
        )

    def submit(
        self,
        record: JobRecord,
        event_log: JobEventLog,
        *,
        on_submitted=None,
        on_code_release_bound=None,
    ) -> str:
        original = self.runner._submit_lsf

        def spy(node, script_remote_path, remote_job_dir, *, submission_id=None):
            self.order.append("bsub")
            self.snapshot = {
                "markers_present": {
                    name: posixpath.join(remote_job_dir, name) in self.sftp.files
                    for name in ("job.json", "task.json")
                },
                "submit_script_present": script_remote_path in self.sftp.files,
                "submit_script": self.sftp.files.get(script_remote_path, b"").decode("utf-8"),
                "release_bound": ((record.result or {}).get("remote") or {}).get("code_release"),
                "submission_id": submission_id,
                "events_before_bsub": [event.get("type") for event in event_log.read_all()],
            }
            return original(node, script_remote_path, remote_job_dir, submission_id=submission_id)

        self.runner._submit_lsf = spy  # type: ignore[assignment]
        with pytest.MonkeyPatch.context() as mp:
            _patch_simulated_release(mp, self)
            return self.runner.submit_remote(
                record,
                event_log,
                on_submitted=on_submitted,
                submission_id=submission_id_for(record.id, record.attempt),
                on_code_release_bound=on_code_release_bound,
            )

    def reconcile(self, record: JobRecord) -> str:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ssh_mod, "_create_client", lambda node, timeout=30: self.client)
            return self.runner.reconcile_submission(record)

    def close(self) -> None:
        self.pool.close()


def test_submission_orders_release_markers_and_submission_id_before_bsub(tmp_path: Path) -> None:
    harness = _LsfHarness(str(tmp_path))
    try:
        record, event_log = _record(harness, "b11-singlepoint")
        sid = submission_id_for(record.id, 1)
        bound: list[str] = []
        persisted: list[str] = []

        def on_bound(release_id: str) -> None:
            harness.order.append("bind")
            bound.append(release_id)

        def on_submitted(lsf_job_id: str) -> None:
            harness.order.append("persist")
            persisted.append(lsf_job_id)

        lsf_id = harness.submit(
            record,
            event_log,
            on_submitted=on_submitted,
            on_code_release_bound=on_bound,
        )

        assert lsf_id == "54321"
        assert harness.bsub_calls == 1, "exactly one bsub for one attempt"
        assert harness.order == ["bind", "bsub", "persist"]
        assert bound == [_RELEASE]
        assert persisted == ["54321"]
        assert harness.snapshot["markers_present"] == {"job.json": True, "task.json": True}
        assert harness.snapshot["submit_script_present"] is True
        assert harness.snapshot["release_bound"] == _RELEASE
        assert harness.snapshot["submission_id"] == sid
        script = str(harness.snapshot["submit_script"])
        assert f"#BSUB -J acp_{sid}" in script
        assert f"releases/{_RELEASE}/src" in script
        events = set(harness.snapshot["events_before_bsub"] or [])
        assert {"remote.markers_uploaded", "remote.code_release_ready"} <= events
        assert ((record.result or {}).get("remote") or {}).get("code_release") == _RELEASE
    finally:
        harness.close()


def test_release_verification_failure_aborts_before_bsub(tmp_path: Path) -> None:
    harness = _LsfHarness(str(tmp_path))
    try:
        record, event_log = _record(harness, "b11-release-gap")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ssh_mod, "_create_client", lambda node, timeout=30: harness.client)
            mp.setattr(
                "acp.scheduler.remote.runner.verify_existing_release",
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    RuntimeError("release hash mismatch")
                ),
            )
            with pytest.raises(RemoteSubmissionRejected):
                harness.runner.submit_remote(record, event_log)
        assert harness.bsub_calls == 0, "an unverified release must never reach bsub"
        event_types = [event.get("type") for event in event_log.read_all()]
        assert "remote.code_release_failed" in event_types
    finally:
        harness.close()


def test_submit_intent_with_submission_id_is_persisted_before_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mgr = _make_manager(tmp_path, monkeypatch)
    try:
        job_id = "b11-intent"
        _seed_queued(mgr, tmp_path, job_id)
        observed: dict[str, object] = {}

        class _IntentBoundaryRunner:
            def __init__(self) -> None:
                self.submit_calls = 0

            def submit_remote(
                self,
                record,
                event_log,
                target_node=None,
                *,
                remote_job_dir=None,
                on_submitted=None,
                submission_id=None,
                on_code_release_bound=None,
            ) -> str:
                self.submit_calls += 1
                stored = mgr.store.get(record.id)
                observed["stored"] = stored
                observed["submission_id"] = submission_id
                if on_submitted is not None:
                    on_submitted("4242")
                return "4242"

            def reconcile_submission(self, record) -> str:
                return "found"

            def recover_job_state(self, record) -> bool:
                return bool(record.remote_job_id)

            def poll_remote(self, record, event_log, cancel_event):
                return RemotePollObservation(terminal=False)

            def apply_terminal_side_effects(self, record, event_log, stage_events=()) -> None:
                return None

            def cancel_remote(self, job_id, record=None) -> bool:
                return True

        runner = _IntentBoundaryRunner()
        mgr.remote_runner = runner  # type: ignore[assignment]
        assert mgr._submit_job(job_id) is True

        stored = observed["stored"]
        assert stored is not None
        meta = (stored.result or {}).get("remote") or {}
        sid = submission_id_for(job_id, stored.attempt)
        assert observed["submission_id"] == sid
        assert meta.get("submission_id") == sid
        assert meta.get("submit_state") == "intent"
        assert meta.get("lease_expires_at")
        assert runner.submit_calls == 1
        final = mgr.store.get(job_id)
        assert final is not None and final.status == JobStatus.PENDING
        assert final.remote_job_id == "4242"
        assert _remote_meta(mgr, job_id).get("submit_state") == "submitted"
    finally:
        mgr.shutdown()


def test_lost_bsub_reply_reconciles_same_submission_without_resubmit(tmp_path: Path) -> None:
    harness = _LsfHarness(str(tmp_path), bsub_result=(0, "garbled reply without a job id\n", ""))
    try:
        record, event_log = _record(harness, "b11-lost-reply")
        sid = submission_id_for(record.id, 1)
        with pytest.MonkeyPatch.context() as mp:
            _patch_simulated_release(mp, harness)
            with pytest.raises(RemoteSubmissionIndeterminate):
                harness.runner.submit_remote(
                    record,
                    event_log,
                    submission_id=submission_id_for(record.id, record.attempt),
                )

        meta = (record.result or {}).get("remote") or {}
        assert meta.get("submit_state") == "unconfirmed"
        assert meta.get("submission_id") == sid
        assert record.attempt == 1
        assert harness.bsub_calls == 1

        name = submission_lsf_name(sid)

        def queries(cmd: str):
            if "bjobs" in cmd and name in cmd:
                return (
                    0,
                    "JOBID   USER    STAT  QUEUE      JOBNAME          SUBMIT\n"
                    f"54321   qc      RUN   normal    {name}   Thu Oct  1\n",
                    "",
                )
            return None

        harness.query_handler = queries
        assert harness.reconcile(record) == "found"
        assert record.remote_job_id == "54321"
        assert ((record.result or {}).get("remote") or {}).get("submission_id") == sid
        assert harness.bsub_calls == 1, "reconciliation must never submit again"
        assert "remote.submit_unconfirmed" in [event.get("type") for event in event_log.read_all()]
    finally:
        harness.close()


class _CannedFetcher:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = dict(files)
        self.calls: list[str] = []

    def read_file(self, _record, rel_path: str) -> bytes:
        self.calls.append(rel_path)
        if rel_path not in self.files:
            raise FileNotFoundError(rel_path)
        return self.files[rel_path]


def _remote_job(job_id: str, run_root: Path) -> JobRecord:
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="singlepoint",
            input={"source_type": "xyz_text", "source": _XYZ, "charge": 0, "multiplicity": 1},
            method=_METHOD,
        ),
        status=JobStatus.COMPLETED,
        work_dir=str(run_root / "work" / job_id),
        result={"node": "compute-01", "remote_dir": f"/scratch/acp/{job_id}"},
        attempt=1,
    )


def _pipeline_request(payload: bytes, *, job_id: str, attempt: int = 1) -> tx.TransferRequest:
    return tx.TransferRequest(
        job_id=job_id,
        attempt=attempt,
        relative_path=_MANIFEST_REL,
        resource_class=tx.ResourceClass.ATTEMPT_ARTIFACT,
        receipt=tx.TransferReceipt(
            job_id=job_id,
            attempt=attempt,
            relative_path=_MANIFEST_REL,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        ),
    )


def test_prepare_digest_generation_cold_cache_and_old_attempt(tmp_path: Path) -> None:
    payload = b'{"version": 2, "products": []}'
    run_root = tmp_path / "runs"
    run_root.mkdir()
    job_id = "b11-remote-read"
    record = _remote_job(job_id, run_root)
    fetcher = _CannedFetcher({_MANIFEST_REL: payload})
    cache = RemoteStructureCache(run_root, fetcher_factory=lambda _jid: fetcher)
    cache.purge_job(job_id)
    reader = build_cache_only_resource_reader(job_lookup=lambda _jid: record, cache=cache)

    cold = reader.serve("manifest", job_id=job_id, workflow="singlepoint", attempt=1)
    assert cold.served is False
    assert cold.source == "not_prepared"
    assert fetcher.calls == [], "a cold-cache read must never fetch"

    pipeline = build_transfer_pipeline(
        cache=cache,
        authorization=build_local_single_transfer_authorization(),
        identity_check=lambda: None,
        db_path=tmp_path / "publications.db",
    )
    prepared = pipeline.prepare(_pipeline_request(payload, job_id=job_id))
    assert prepared.accepted is True
    assert prepared.sha256 == hashlib.sha256(payload).hexdigest()
    after_prepare = list(fetcher.calls)
    assert after_prepare == [_MANIFEST_REL], "prepare downloads exactly once"

    served = reader.serve("manifest", job_id=job_id, workflow="singlepoint", attempt=1)
    assert served.served is True
    assert served.data == payload
    assert hashlib.sha256(served.data or b"").hexdigest() == prepared.sha256
    assert fetcher.calls == after_prepare, "serving a prepared artifact stays cache-only"

    attempt, generation = cache._read_fence(job_id)
    assert attempt == 1
    assert generation >= 1
    fresh = reader.availability(
        "manifest",
        job_id=job_id,
        workflow="singlepoint",
        attempt=1,
        expected_revision=generation,
    )
    stale = reader.availability(
        "manifest",
        job_id=job_id,
        workflow="singlepoint",
        attempt=1,
        expected_revision=generation + 1,
    )
    assert fresh.availability is Availability.READY
    assert fresh.freshness is Freshness.CURRENT
    assert fresh.basis_revision == generation
    assert stale.freshness is not Freshness.CURRENT
    assert fetcher.calls == after_prepare

    old_attempt = reader.serve("manifest", job_id=job_id, workflow="singlepoint", attempt=2)
    assert old_attempt.served is False
    assert fetcher.calls == after_prepare, "an old-attempt read must not fall back to a fetch"

    mismatch_payload = payload.replace(b"[]", b"{}")
    mismatch_fetcher = _CannedFetcher({_MANIFEST_REL: mismatch_payload})
    mismatch_cache = RemoteStructureCache(
        tmp_path / "mismatch-runs", fetcher_factory=lambda _jid: mismatch_fetcher
    )
    mismatch_pipeline = build_transfer_pipeline(
        cache=mismatch_cache,
        authorization=build_local_single_transfer_authorization(),
        identity_check=lambda: None,
        db_path=tmp_path / "mismatch-publications.db",
    )
    rejected = mismatch_pipeline.prepare(_pipeline_request(payload, job_id="b11-digest-mismatch"))
    assert rejected.accepted is False
    assert rejected.rejection is tx.RejectionCode.DIGEST_MISMATCH


def test_remote_result_root_uses_controlled_cache_not_work_dir(tmp_path: Path) -> None:
    payload = b'{"version": 2, "products": []}'
    run_root = tmp_path / "runs"
    run_root.mkdir()
    job_id = "b11-read-root"
    record = _remote_job(job_id, run_root)
    fetcher = _CannedFetcher({_MANIFEST_REL: payload})
    cache = RemoteStructureCache(run_root, fetcher_factory=lambda _jid: fetcher)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(job_manager=SimpleNamespace(structure_cache=cache))
        )
    )

    root = _job_read_root(request, record)

    assert root == cache.job_root(job_id)
    assert root != Path(record.work_dir)
    assert not Path(record.work_dir).exists(), "the remote work_dir is never the read root"
    assert _MANIFEST_REL in fetcher.calls
    assert (root / _MANIFEST_REL).is_file()
