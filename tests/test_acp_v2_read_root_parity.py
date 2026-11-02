"""W-01a: one read-root implementation, behaviour retained.

The V2 plan's W-01a retires the duplicated ``_job_read_root`` mirror in
``acp.api.v2_routes``.  This slice is deliberately **behaviour preserving**:
the helper keeps resolving terminal remote jobs through the manager-owned
``RemoteStructureCache`` and keeps the registered terminal ``fetch_catalog``
transition side effect (closure L2-03).  Removing that side effect is W-01b
and stays blocked by the registered F0/F1 x S0/S1 exit condition
(``tests/baseline/services-evidence/cache_only_switch_matrix.md`` §6).

What is pinned here:

* ``v2_routes`` no longer defines its own copy — it is the *same* function
  object as ``v1_routes._job_read_root`` (single implementation);
* a remote terminal job still resolves to the controlled cache root (never
  ``record.work_dir``) and the terminal catalog fetch still runs;
* a local job still resolves to its ``work_dir`` with no cache call.

Run with: pytest tests/test_acp_v2_read_root_parity.py -q
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from acp.api import v1_routes, v2_routes
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

_REPO_ROOT = Path(__file__).resolve().parents[1]
_V1_SOURCE = _REPO_ROOT / "src" / "acp" / "api" / "v1_routes.py"
_V2_SOURCE = _REPO_ROOT / "src" / "acp" / "api" / "v2_routes.py"


class _FakeCache:
    """Minimal RemoteStructureCache surface used by ``_job_read_root``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.fetch_calls: list[tuple[str, str]] = []

    def job_root(self, job_id: str) -> Path:
        return self.root / jid_dir_name(job_id)

    def fetch_catalog(self, record: JobRecord, workflow: str) -> Path | None:
        self.fetch_calls.append((record.id, workflow))
        return self.job_root(record.id)


def jid_dir_name(job_id: str) -> str:
    """Cache layout helper (``.remote_cache/<job_id>``)."""
    return f".remote_cache/{job_id}"


def _request(cache: _FakeCache) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(job_manager=SimpleNamespace(structure_cache=cache))
        )
    )


def _record(*, job_id: str, work_dir: Path, remote: bool) -> JobRecord:
    result = None
    if remote:
        result = {"node": "node-a", "remote_dir": f"/scratch/qc/acp/{job_id}"}
    return JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow="Confsearch",
            name=job_id,
            input={"source": "CCO"},
        ),
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        result=result,
    )


def _definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def test_v2_imports_the_single_read_root_implementation() -> None:
    """One function object, one definition — the mirror is gone."""
    assert v2_routes._job_read_root is v1_routes._job_read_root
    assert "_job_read_root" in _definitions(_V1_SOURCE)
    assert "_job_read_root" not in _definitions(_V2_SOURCE)
    # The retired mirror reason must not return as a live justification.
    source = _V2_SOURCE.read_text(encoding="utf-8")
    assert "the mirror lives here" not in source


def test_remote_terminal_job_keeps_cache_root_and_registered_fetch(tmp_path: Path) -> None:
    """Behaviour retained: controlled cache + one terminal catalog fetch."""
    cache = _FakeCache(tmp_path / "run")
    record = _record(job_id="w01-remote", work_dir=tmp_path / "work", remote=True)

    root = v1_routes._job_read_root(_request(cache), record)

    assert root == cache.job_root(record.id)
    assert root != Path(record.work_dir)
    assert not Path(record.work_dir).exists(), "the remote work_dir is never the read root"
    assert cache.fetch_calls == [(record.id, "Confsearch")]


def test_local_job_still_resolves_to_work_dir_without_cache(tmp_path: Path) -> None:
    """Local jobs never touch the remote cache (behaviour retained)."""
    cache = _FakeCache(tmp_path / "run")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    record = _record(job_id="w01-local", work_dir=work_dir, remote=False)

    root = v1_routes._job_read_root(_request(cache), record)

    assert root == work_dir
    assert cache.fetch_calls == []
