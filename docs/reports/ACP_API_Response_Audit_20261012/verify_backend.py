"""Reproduce API audit findings against isolated data; not a CI regression suite.

Some assertions intentionally describe current defects. Run this file explicitly
with pytest; all data lives in pytest's temporary directories, with no live SSH.
"""

from __future__ import annotations

import asyncio
import builtins
import json
import os
import queue
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from acp.api import routes, v1_routes
from acp.results import structure_viewer
from acp.scheduler.events import JobEventLog
from acp.scheduler.files import build_manifest
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote.config import RemoteNode
from acp.scheduler.remote.ssh import SSHConnectionPool
from acp.scheduler.structure_sources import StructureSourceService
from acp.storage.manifest import ResultManifest

os.environ["ACP_RUN_ROOT"] = tempfile.mkdtemp(prefix="acp_response_audit_")
os.environ["ACP_EXECUTION_MODE"] = "local"

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE: dict = {}
XYZ = "2\naudit\nH 0 0 0\nH 0 0 0.74\n"


@pytest.fixture(scope="session", autouse=True)
def save_evidence():
    yield
    (Path(__file__).parent / "backend_evidence.json").write_text(
        json.dumps(EVIDENCE, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def request_for(manager):
    async def disconnected():
        return False

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(job_manager=manager)),
        headers={},
        query_params={},
        is_disconnected=disconnected,
    )


class ReadMeter:
    def __init__(self, handle, counters):
        self.handle = handle
        self.counters = counters

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *args):
        return self.handle.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def read(self, *args):
        data = self.handle.read(*args)
        self.counters["bytes"] += len(data)
        return data

    def readline(self, *args):
        data = self.handle.readline(*args)
        self.counters["readlines"] += 1
        self.counters["bytes"] += len(data)
        return data

    def __iter__(self):
        return self

    def __next__(self):
        data = next(self.handle)
        self.counters["readlines"] += 1
        self.counters["bytes"] += len(data.encode("utf-8") if isinstance(data, str) else data)
        return data


@contextmanager
def metered_path(monkeypatch, target):
    counters = {"bytes": 0, "readlines": 0, "opens": 0}
    original = Path.open

    def opening(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        if path == target:
            counters["opens"] += 1
            return ReadMeter(handle, counters)
        return handle

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", opening)
        yield counters


def terminal_manager(event_log):
    record = SimpleNamespace(
        status=JobStatus.COMPLETED, progress=1.0, current_stage=None, completed_at=None
    )
    return SimpleNamespace(event_log=lambda _: event_log, get=lambda _: record)


@pytest.mark.parametrize("resume", [False, True])
def test_sse_bounded_response_still_scans_history(tmp_path, monkeypatch, resume):
    path = tmp_path / "events.jsonl"
    line = json.dumps({"type": "progress", "message": "x" * 256}).encode() + b"\n"
    slots = 50_000
    path.write_bytes(line * slots)
    log = JobEventLog(path)

    async def consume():
        response = await routes.stream_job_events(
            "audit",
            request_for(terminal_manager(log)),
            after_seq=slots - 1 if resume else 0,
            history=200,
        )
        return "".join([part async for part in response.body_iterator])

    with metered_path(monkeypatch, path) as meter:
        started = time.perf_counter()
        body = asyncio.run(consume())
        elapsed = time.perf_counter() - started
    key = "sse_resume" if resume else "sse_initial"
    EVIDENCE[key] = {
        "file_bytes": path.stat().st_size,
        "events": slots,
        "io": meter,
        "elapsed_ms": round(elapsed * 1000, 3),
        "response_progress_events": body.count("event: progress\n"),
    }
    assert body.count("event: progress\n") == (1 if resume else 200)
    assert meter["bytes"] >= path.stat().st_size * (1 if resume else 2)
    if resume:
        assert meter["readlines"] >= slots


def test_sse_disk_work_blocks_event_loop(tmp_path, monkeypatch):
    log = JobEventLog(tmp_path / "events.jsonl")
    log.append("progress", index=1)
    original = log.count
    count_threads = []

    def delayed_count():
        count_threads.append(threading.get_ident())
        time.sleep(0.04)  # Controlled slow-disk injection, not a production timing.
        return original()

    monkeypatch.setattr(log, "count", delayed_count)

    async def probe():
        loop = asyncio.get_running_loop()
        response = await routes.stream_job_events(
            "audit", request_for(terminal_manager(log)), after_seq=0, history=200
        )
        await anext(response.body_iterator)  # retry frame precedes disk work
        callbacks = []
        start = time.perf_counter()
        loop.call_later(0.01, lambda: callbacks.append(time.perf_counter() - start))
        await anext(response.body_iterator)
        elapsed = time.perf_counter() - start
        blocked = not callbacks
        await asyncio.sleep(0.01)
        await response.body_iterator.aclose()
        return blocked, elapsed, callbacks, threading.get_ident()

    blocked, elapsed, callbacks, loop_thread = asyncio.run(probe())
    EVIDENCE["sse_loop_block"] = {
        "injected_count_delay_ms": 40,
        "count_calls": len(count_threads),
        "elapsed_ms": round(elapsed * 1000, 3),
        "timer_due_ms": 10,
        "timer_fired_ms": round(callbacks[0] * 1000, 3),
        "same_thread": all(t == loop_thread for t in count_threads),
    }
    assert blocked and elapsed >= 0.075
    assert all(t == loop_thread for t in count_threads)


def make_batch(root, count=200):
    manifest = ResultManifest(workflow="BatchOptimize")
    for index in range(count):
        relative = f"structures/{index:04d}.xyz"
        path = root / "RESULT" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(XYZ, encoding="utf-8")
        manifest.add_product(f"batch_{index:04d}", str(index), relative, "structure")
    manifest.write(root / "RESULT")
    return JobRecord(
        id="batch-audit",
        spec=JobSpec(workflow="BatchOptimize"),
        status=JobStatus.COMPLETED,
        work_dir=str(root),
    )


def test_geometry_and_vibration_rebuild_whole_catalog(tmp_path, monkeypatch):
    record = make_batch(tmp_path)
    manager = SimpleNamespace(get=lambda _: record)
    request = request_for(manager)
    original = structure_viewer.build_structure_viewer_payload
    sizes = []

    def traced(*args, **kwargs):
        payload = original(*args, **kwargs)
        sizes.append(len(payload.entries))
        return payload

    monkeypatch.setattr(structure_viewer, "build_structure_viewer_payload", traced)
    for _ in range(2):
        response = v1_routes.get_structure_viewer_geometry(
            record.id, "batch_0000", request, fetch=False
        )
        assert response.status_code == 200
    v1_routes.get_structure_viewer_vibrations(record.id, "batch_0000", request)
    EVIDENCE["catalog_rebuild"] = {"requests": 3, "entry_counts_per_build": sizes}
    assert sizes == [200, 200, 200]


def test_detail_summary_reads_every_xyz_then_drops_text(tmp_path, monkeypatch):
    record = make_batch(tmp_path)
    original = Path.read_text
    reads = []

    def traced(path, *args, **kwargs):
        text = original(path, *args, **kwargs)
        if path.suffix == ".xyz":
            reads.append(len(text.encode("utf-8")))
        return text

    monkeypatch.setattr(Path, "read_text", traced)
    summary = v1_routes._structure_result_summary(request_for(None), record)
    EVIDENCE["detail_xyz_reads"] = {
        "products": 200,
        "xyz_reads": len(reads),
        "bytes_read": sum(reads),
        "summary_count": summary["count"],
        "response_has_xyz_text": "xyz_text" in json.dumps(summary),
    }
    assert len(reads) == 200
    assert summary["count"] == 200
    assert "xyz_text" not in json.dumps(summary)


def test_log_cursor_still_counts_entire_files(tmp_path, monkeypatch):
    runtime = tmp_path / "WORK" / "00_RUNTIME"
    runtime.mkdir(parents=True)
    stdout = runtime / "stdout.log"
    stdout.write_text("old line\n" * 100_000, encoding="utf-8")
    (runtime / "stderr.log").write_text("", encoding="utf-8")
    manager = SimpleNamespace(work_dir_of=lambda _: tmp_path)
    original = builtins.open
    meter = {"bytes": 0, "readlines": 0}

    def traced(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        return ReadMeter(handle, meter) if Path(path) == stdout else handle

    monkeypatch.setattr(builtins, "open", traced)
    response = v1_routes.get_job_logs(
        "audit", request_for(manager), lines=300, stdout_offset=100_000, stderr_offset=0
    )
    body = json.loads(response.body)
    EVIDENCE["log_cursor_scan"] = {
        "stdout_bytes": stdout.stat().st_size,
        "io": meter,
        "new_stdout_lines": len(body["stdout_lines"]),
    }
    assert body["stdout_lines"] == []
    assert meter["readlines"] == 100_000


def test_large_file_is_invisibly_omitted(tmp_path):
    path = tmp_path / "large.out"
    with path.open("wb") as handle:
        handle.truncate(200 * 1024 * 1024 + 1)  # Sparse, no 200 MiB allocation.
    result = build_manifest(tmp_path)
    EVIDENCE["large_file_listing"] = {
        "file_bytes": path.stat().st_size,
        "listed": result["files"],
        "truncated": result["truncated"],
    }
    assert result["files"] == [] and result["truncated"] is False


def test_structure_source_load_bypasses_geometry_cache(tmp_path):
    record = JobRecord(
        id="remote-audit",
        spec=JobSpec(workflow="optimize"),
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path),
        remote_job_id="123",
        result={"node": "audit"},
    )
    store = SimpleNamespace(get=lambda _: record)
    fetcher = SimpleNamespace(read_file=MagicMock(return_value=XYZ.encode()))
    service = StructureSourceService(store, tmp_path, fetcher)
    service.get("job_remote-audit:RESULT/structures/optimized.xyz")
    service.get("job_remote-audit:RESULT/structures/optimized.xyz")
    EVIDENCE["source_detail_remote_reads"] = {
        "loads": 2,
        "read_file_calls": fetcher.read_file.call_count,
    }
    assert fetcher.read_file.call_count == 2


def test_missing_remote_cache_entry_has_no_negative_cache(tmp_path):
    from acp.results.remote_structure_cache import RemoteStructureCache

    fetcher = SimpleNamespace(
        read_file=MagicMock(side_effect=FileNotFoundError("absent")),
        list_files=MagicMock(return_value=[]),
    )
    cache = RemoteStructureCache(tmp_path, lambda _: fetcher)
    record = JobRecord(
        id="missing-audit",
        spec=JobSpec(workflow="optimize"),
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path / "work"),
        remote_job_id="123",
        result={"node": "audit"},
    )
    cache.fetch(record, "missing.xyz")
    cache.fetch(record, "missing.xyz")
    EVIDENCE["negative_cache"] = {"calls": fetcher.read_file.call_count}
    assert fetcher.read_file.call_count == 2


def test_ssh_command_retries_and_sftp_channel_timeout(tmp_path):
    node = RemoteNode(
        name="audit",
        host="invalid.test",
        username="audit",
        remote_work_dir="/audit/work",
        remote_code_dir="/audit/code",
    )
    pool = SSHConnectionPool()
    client = MagicMock()
    client.exec_command.side_effect = TimeoutError("injected command timeout")
    pool.borrow = MagicMock(return_value=client)
    pool.release = MagicMock()
    from acp.scheduler.remote.ssh import SSHExecutionError

    with pytest.raises(SSHExecutionError):
        pool.execute(node, "bstop 123", timeout=30)
    channel = MagicMock()
    client.open_sftp.return_value = channel
    with pool.sftp_session(node):
        pass
    EVIDENCE["ssh_budgets"] = {
        "command_attempts": client.exec_command.call_count,
        "timeout_per_command_s": client.exec_command.call_args.kwargs["timeout"],
        "sftp_settimeout_calls": channel.get_channel.return_value.settimeout.call_count,
    }
    assert client.exec_command.call_count == 3
    channel.get_channel.return_value.settimeout.assert_not_called()


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.inline = []
        self.styles = []
        self.i18n_attributes = {}
        self.current = None

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        for key in data:
            if key.startswith("data-i18n"):
                self.i18n_attributes[key] = self.i18n_attributes.get(key, 0) + 1
        if tag == "script":
            if "src" in data:
                self.scripts.append(data)
            else:
                self.inline.append("")
                self.current = self.inline
        elif tag == "style":
            self.styles.append("")
            self.current = self.styles

    def handle_data(self, data):
        if self.current is not None:
            self.current[-1] += data

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.current = None


def test_actual_asset_bytes_and_uncompressed_response(tmp_path):
    from acp.api.server import create_app

    client = TestClient(create_app(run_root=tmp_path))  # No scheduler lifespan.
    response = client.get("/", headers={"Accept-Encoding": "gzip, br"})
    parser = Assets()
    parser.feed(response.text)
    external = [
        {**s, "bytes": (ROOT / "frontend" / s["src"].lstrip("/")).stat().st_size}
        for s in parser.scripts
    ]
    inline_bytes = sum(len(s.encode("utf-8")) for s in parser.inline)
    synchronous = sum(s["bytes"] for s in external if "async" not in s and "defer" not in s)
    dictionary = re.search(r"(?:const|var|let) I18N\s*=\s*\{[\s\S]+?\n\};", response.text)
    EVIDENCE["assets"] = {
        "html_bytes": len(response.content),
        "inline_js_bytes": inline_bytes,
        "inline_css_bytes": sum(len(s.encode("utf-8")) for s in parser.styles),
        "synchronous_external_js_bytes": synchronous,
        "inline_plus_synchronous_js_bytes": inline_bytes + synchronous,
        "external_scripts": external,
        "i18n_attributes": parser.i18n_attributes,
        "i18n_dictionary_bytes": len(dictionary.group().encode("utf-8")) if dictionary else None,
        "content_encoding": response.headers.get("content-encoding"),
    }
    assert response.status_code == 200
    assert "content-encoding" not in response.headers


def test_local_download_range_is_supported(tmp_path):
    from fastapi import FastAPI

    path = tmp_path / "download.txt"
    path.write_bytes(b"0123456789")
    app = FastAPI()
    app.state.job_manager = SimpleNamespace(work_dir_of=lambda _: tmp_path)
    app.include_router(v1_routes.router, prefix="/api/v1")
    response = TestClient(app).get(
        "/api/v1/jobs/audit/files/download.txt", headers={"Range": "bytes=2-5"}
    )
    EVIDENCE["local_range"] = {
        "status": response.status_code,
        "content_range": response.headers.get("content-range"),
        "content": response.text,
    }
    assert response.status_code == 206 and response.content == b"2345"


def test_anyio_default_thread_limit():
    import anyio

    async def capacity():
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    EVIDENCE["anyio_thread_tokens"] = asyncio.run(capacity())
    assert EVIDENCE["anyio_thread_tokens"] == 40


def test_stream_does_not_reserve_worker_between_chunks():
    import anyio
    from starlette.responses import StreamingResponse

    async def probe():
        limiter = anyio.to_thread.current_default_thread_limiter()
        response = StreamingResponse(iter([b"first", b"second"]))
        first = await anext(response.body_iterator)
        between = limiter.borrowed_tokens
        second = await anext(response.body_iterator)
        await response.body_iterator.aclose()
        return first, second, between

    first, second, tokens = asyncio.run(probe())
    EVIDENCE["stream_between_chunks"] = {"borrowed_worker_tokens": tokens}
    assert first == b"first" and second == b"second" and tokens == 0


def test_prefetch_queue_is_fifo_without_worker_retry():
    from acp.scheduler.manager import JobManager

    manager = object.__new__(JobManager)
    manager._catalog_prefetch_queue = queue.Queue()
    for job in ("old-1", "old-2", "new-completion", None):
        manager._catalog_prefetch_queue.put(job)
    calls = []

    def prefetch(job):
        calls.append(job)
        if job == "old-1":
            raise ConnectionError("injected failure")

    manager._prefetch_remote_catalog = prefetch
    manager._catalog_prefetch_loop()
    EVIDENCE["prefetch_fifo"] = {
        "attempt_order": calls,
        "remaining_items": manager._catalog_prefetch_queue.qsize(),
    }
    assert calls == ["old-1", "old-2", "new-completion"]
    assert manager._catalog_prefetch_queue.empty()


def test_remote_index_refresh_ignores_fresh_index_timestamp(tmp_path):
    from acp.scheduler.structure_source_indexer import StructureSourceIndexer

    store = SimpleNamespace()
    source_store = SimpleNamespace(list_by_job=lambda _: [{"source_uid": "existing"}])
    indexer = StructureSourceIndexer(store, source_store, tmp_path)
    indexer._get_indexed_at = lambda _: "2099-01-01T00:00:00Z"
    indexer._index_job = MagicMock()
    record = JobRecord(
        id="audit-remote",
        spec=JobSpec(workflow="optimize"),
        status=JobStatus.COMPLETED,
        work_dir=str(tmp_path),
        remote_job_id="123",
    )
    indexer._refresh_job_if_stale(record)
    indexer._refresh_job_if_stale(record)
    EVIDENCE["remote_index_refresh"] = {"sweeps": 2, "index_calls": indexer._index_job.call_count}
    assert indexer._index_job.call_count == 2


def test_remote_download_does_not_honor_range(tmp_path, monkeypatch):
    from fastapi import FastAPI

    fetcher = SimpleNamespace(
        file_stat=lambda *_: SimpleNamespace(size=10), stream_file=lambda *_: iter([b"0123456789"])
    )
    record = SimpleNamespace()
    monkeypatch.setattr(v1_routes, "_remote_fetcher", lambda _: fetcher)
    monkeypatch.setattr(v1_routes, "_get_remote_job_record", lambda *args, **kwargs: record)
    monkeypatch.setattr(v1_routes, "_log_remote_access", lambda *args: None)
    app = FastAPI()
    app.include_router(v1_routes.router, prefix="/api/v1")
    response = TestClient(app).get(
        "/api/v1/jobs/audit/remote-files/download.txt", headers={"Range": "bytes=2-5"}
    )
    EVIDENCE["remote_range"] = {
        "status": response.status_code,
        "content_range": response.headers.get("content-range"),
        "bytes_returned": len(response.content),
    }
    assert response.status_code == 200 and response.content == b"0123456789"
