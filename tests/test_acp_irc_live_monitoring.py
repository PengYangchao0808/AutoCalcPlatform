"""Live IRC API and remote-product regression coverage."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.api.v1_routes import get_energy_graph, get_irc_frame_geometry, get_irc_log_tail
from acp.results.energy_graph import build_energy_graph_from_job
from acp.results.irc_projection import build_irc_energy_graph
from acp.results.irc_remote_live import refresh_remote_irc
from acp.scheduler.jobs import JobStatus


class FakeRemoteFetcher:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def file_stat(self, _record: object, path: str) -> SimpleNamespace:
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(size=len(self.files[path]), mtime=1000.0, is_dir=False)

    def read_file(self, _record: object, path: str) -> bytes:
        return self.files[path]

    def read_range(self, _record: object, path: str, offset: int, limit: int) -> bytes:
        return self.files[path][offset : offset + limit]

    def list_files(self, _record: object, relative_path: str) -> list[SimpleNamespace]:
        prefix = relative_path.rstrip("/") + "/"
        return [
            SimpleNamespace(name=name, size=len(data), mtime=1000.0, is_dir=False)
            for name, data in self.files.items()
            if name.startswith(prefix)
        ]


def _remote_request(tmp_path: Path, files: dict[str, bytes]) -> SimpleNamespace:
    record = SimpleNamespace(
        id="irc-live",
        work_dir=str(tmp_path),
        spec=SimpleNamespace(workflow="irc", method={}),
        status=JobStatus.RUNNING,
        result={"node": "compute-1", "remote_dir": "/remote/irc-live"},
    )
    manager = SimpleNamespace(
        get=lambda _job_id: record,
        remote_fetcher=FakeRemoteFetcher(files),
        structure_cache=SimpleNamespace(
            job_root=lambda _job_id: tmp_path / ".remote_cache" / "irc-live",
        ),
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(job_manager=manager)))


def test_remote_snapshot_graph_and_single_frame_geometry(tmp_path: Path) -> None:
    point = b"2\nIRC forward point 0 E -10.0\nC 0 0 0\nH 0 0 1\n"
    snapshot = {
        "schema": "irc_trajectory_v1",
        "status": "running",
        "complete": False,
        "updated_at": "2026-09-26T12:00:00+00:00",
        "frames": [{
            "direction": "forward", "index": 0, "energy_hartree": -10.0,
            "geometry_ref": "RESULT/irc/irc_forward_point_0000.xyz",
        }],
    }
    files = {
        "RESULT/trajectories/irc_trajectory.json": json.dumps(snapshot).encode(),
        "RESULT/irc/irc_forward_path.xyz": point,
        "RESULT/irc/irc_forward_point_0000.xyz": point,
        "WORK/07_PATH/ORCA/irc.out": b"forward started\nstep 1\npartial",
    }
    request = _remote_request(tmp_path, files)
    graph = get_energy_graph("irc-live", request, view_type="auto", view=None, item_id=None)
    assert graph.view_type == "irc"
    assert graph.metadata["live_source"] == "snapshot"
    assert graph.metadata["directions"]["forward"] == 1
    assert graph.revision
    snapshot["frames"][0]["energy_hartree"] = -11.0
    files["RESULT/trajectories/irc_trajectory.json"] = json.dumps(snapshot).encode()
    updated = get_energy_graph("irc-live", request, view_type="auto", view=None, item_id=None)
    assert updated.revision != graph.revision
    geometry = get_irc_frame_geometry("irc-live", "forward", 0, request)
    assert geometry["xyz"] == point.decode()
    assert "step 1" in get_irc_log_tail("irc-live", request, offset=0)["lines"]
    assert get_irc_log_tail("irc-live", request, offset=0)["next_offset"] == len(
        b"forward started\nstep 1\n"
    )


def test_remote_legacy_orca_trajectory_backfills_live_graph(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "irc" / "h2o2_IRC_F_trj.xyz"
    files = {"WORK/07_PATH/ORCA/irc_IRC_F_trj.xyz": fixture.read_bytes()}
    request = _remote_request(tmp_path, files)
    record = request.app.state.job_manager.get("irc-live")
    assert (
        refresh_remote_irc(record, tmp_path, request.app.state.job_manager.remote_fetcher)
        == "orca_trajectory"
    )
    graph = build_energy_graph_from_job(
        "irc-live", workflow="irc", method={}, work_dir=tmp_path, job_status="running"
    )
    assert graph["metadata"]["directions"]["forward"] == 6
    assert graph["source"] == "WORK/07_PATH/ORCA"
    geometry = get_irc_frame_geometry("irc-live", "forward", 2, request)
    assert "0.0411010000" in geometry["xyz"]
    assert "0.0109900000" not in geometry["xyz"]


def test_terminal_irc_without_points_is_not_reported_as_running(tmp_path: Path) -> None:
    graph = build_energy_graph_from_job(
        "irc-empty", workflow="irc", method={}, work_dir=tmp_path, job_status="failed"
    )
    assert graph["status"] == "failed"
    assert graph["metadata"]["reason"] == "irc_path_missing"


def test_raw_trajectory_outgrowing_snapshot_is_visible(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "irc" / "h2o2_IRC_F_trj.xyz"
    raw = tmp_path / "WORK" / "07_PATH" / "ORCA" / "irc_IRC_F_trj.xyz"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(fixture.read_bytes())
    snapshot = tmp_path / "RESULT" / "trajectories" / "irc_trajectory.json"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        json.dumps({
            "status": "running", "complete": False,
            "frames": [{"direction": "forward", "index": 0, "energy_hartree": -1.0}],
        }),
        encoding="utf-8",
    )
    graph = build_irc_energy_graph("irc-growing", tmp_path)
    assert graph is not None
    assert graph["source"] == "WORK/07_PATH/ORCA"
    assert graph["metadata"]["directions"]["forward"] == 6


def test_frontend_detects_irc_growth_and_follows_active_direction() -> None:
    if shutil.which("node") is None:
        pytest.skip("node unavailable")
    html = (Path(__file__).parents[1] / "frontend" / "ACP_Workbench_v2.html").read_text(
        encoding="utf-8"
    )
    revision_fn = html.split("function energyGraphDataRevision(data) {", 1)[1].split(
        "function optimizationJobItemId", 1
    )[0]
    latest_fn = html.split("function energyIrcLatestNode(data) {", 1)[1].split(
        "async function renderEnergyGraphWorkspace", 1
    )[0]
    script = (
        "function energyGraphDataRevision(data) {" + revision_fn
        + "function energyIrcLatestNode(data) {" + latest_fn
        + "const base={view_type:'irc',revision:'',status:'running',metadata:{},"
        + "nodes:[{id:'irc_forward_0',frame_index:0,metadata:{direction:'forward'}}],series:[]};"
        + "const grown=JSON.parse(JSON.stringify(base));"
        + "grown.nodes.push({id:'irc_reverse_0',frame_index:0,"
        + "metadata:{direction:'reverse'}});"
        + "if(energyGraphDataRevision(base)===energyGraphDataRevision(grown)) process.exit(2);"
        + "if(energyIrcLatestNode(grown).id!=='irc_reverse_0') process.exit(3);"
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
