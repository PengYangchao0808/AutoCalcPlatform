"""IRC path trajectory tests against real ORCA 6.1.1 IRC output.

Covers: trajectory discovery, real frame energy+geometry parsing, the
iteration-table fallback, truncated (running) frames, failure partial-path
retention, projection from the persisted snapshot, WORK back-fill, and the
energy-graph dispatcher branch.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from acp.calculations.primitives.irc_trajectory import (
    IrcTrajectoryRecorder,
    write_irc_trajectory,
)
from acp.results.energy_graph import build_energy_graph_from_job
from acp.results.irc_projection import build_irc_energy_graph
from cccp.qc.interfaces.orca_ts import (
    discover_irc_trajectory_files,
    parse_irc_iteration_energies,
    parse_irc_trajectory_xyz,
)

FIXTURES = Path(__file__).parent / "fixtures" / "irc"

FWD_ENERGIES = [
    -151.527934162309,
    -151.531776722389,
    -151.533189562531,
    -151.533879065725,
    -151.533980312543,
    -151.534042732239,
]
REV_ENERGIES = [
    -151.528529819461,
    -151.532795423387,
    -151.533145924765,
    -151.533232562984,
    -151.533265903525,
    -151.533310405393,
]


def _seed_orca(tmp_path: Path, names: tuple[str, ...]) -> Path:
    orca = tmp_path / "WORK" / "07_PATH" / "ORCA"
    orca.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy(FIXTURES / name, orca / name)
    return orca


def test_discover_prefers_trajectory_and_ignores_full(tmp_path: Path) -> None:
    orca = _seed_orca(
        tmp_path,
        (
            "h2o2_IRC_F_trj.xyz",
            "h2o2_IRC_B_trj.xyz",
            "h2o2_IRC_Full_trj.xyz",
            "h2o2_IRC_F.xyz",
            "h2o2_IRC_B.xyz",
        ),
    )
    files = discover_irc_trajectory_files(orca)
    assert files["forward"].name == "h2o2_IRC_F_trj.xyz"
    assert files["reverse"].name == "h2o2_IRC_B_trj.xyz"


def test_parse_real_forward_trajectory_keeps_order_and_energy(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz",))
    points = parse_irc_trajectory_xyz(orca / "h2o2_IRC_F_trj.xyz", "forward")
    assert [point.index for point in points] == list(range(6))
    assert [point.energy_hartree for point in points] == FWD_ENERGIES
    assert all(point.coordinates is not None and len(point.symbols) == 4 for point in points)
    assert all(point.direction == "forward" for point in points)


def test_parse_real_reverse_trajectory_does_not_mix_directions(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_B_trj.xyz",))
    points = parse_irc_trajectory_xyz(orca / "h2o2_IRC_B_trj.xyz", "reverse")
    assert [point.energy_hartree for point in points] == REV_ENERGIES
    assert all(point.direction == "reverse" for point in points)


def test_parse_iteration_table_from_real_orca_output() -> None:
    text = (FIXTURES / "orca_irc_h2o2.out").read_text(encoding="utf-8", errors="replace")
    energies = parse_irc_iteration_energies(text)
    assert [round(value, 6) for value in energies["forward"]] == [
        round(value, 6) for value in FWD_ENERGIES
    ]
    assert [round(value, 6) for value in energies["reverse"]] == [
        round(value, 6) for value in REV_ENERGIES
    ]


def test_parse_iteration_table_stops_at_path_summary() -> None:
    text = (FIXTURES / "orca_irc_h2o2.out").read_text(encoding="utf-8", errors="replace")
    energies = parse_irc_iteration_energies(text)
    assert len(energies["forward"]) == 6
    assert len(energies["reverse"]) == 6
    assert abs(energies["reverse"][0] - -151.528530) < 1e-6


def test_truncated_tail_frame_is_ignored(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz",))
    original = (orca / "h2o2_IRC_F_trj.xyz").read_text(encoding="utf-8")
    (orca / "h2o2_IRC_F_trj.xyz").write_text(
        original + "4\nCoordinates from ORCA-job h2o2_IRC_F E -151.5", encoding="utf-8"
    )
    points = parse_irc_trajectory_xyz(orca / "h2o2_IRC_F_trj.xyz", "forward")
    assert len(points) == 6
    payload = write_irc_trajectory(tmp_path / "RESULT", target_dir=orca)
    assert payload is not None
    assert len(payload["frames"]) == 6


def test_writer_single_direction_produces_one_series(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz",))
    payload = write_irc_trajectory(tmp_path / "RESULT", target_dir=orca)
    assert payload is not None
    assert payload["directions"] == ["forward"]
    assert (tmp_path / "RESULT" / "irc" / "irc_forward_path.xyz").is_file()
    graph = build_irc_energy_graph("job-single", tmp_path)
    assert graph is not None
    assert [series["id"] for series in graph["series"]] == ["irc_forward"]
    assert graph["series"][0]["unit"] == "Eh"


def test_failed_run_keeps_partial_path(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz",))
    recorder = IrcTrajectoryRecorder(
        tmp_path / "RESULT", orca, directions=("forward", "reverse"), min_interval=0.0
    )
    recorder.feed_line("IRC step 0 -151.5")
    partial = recorder.finish(status="failed", complete=False)
    assert partial is not None
    assert len(partial["frames"]) == 6
    assert partial["status"] == "failed"
    assert partial["complete"] is False
    assert (tmp_path / "RESULT" / "trajectories" / "irc_trajectory.json").is_file()


def test_projection_prefers_persisted_trajectory_over_endpoints(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz", "h2o2_IRC_B_trj.xyz"))
    endpoints = tmp_path / "RESULT" / "irc"
    endpoints.mkdir(parents=True, exist_ok=True)
    (endpoints / "irc_forward.xyz").write_text(
        "4\nIRC forward endpoint\nO 0 0 0\nO 0 0 1\nH 0 0 2\nH 0 0 3\n", encoding="utf-8"
    )
    _ = write_irc_trajectory(tmp_path / "RESULT", target_dir=orca)
    graph = build_irc_energy_graph("job-prefer", tmp_path)
    assert graph is not None
    assert graph["source"] == "RESULT/trajectories/irc_trajectory.json"
    assert len(graph["nodes"]) == 12


def test_projection_backfills_from_work_orca_files(tmp_path: Path) -> None:
    _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz", "h2o2_IRC_B_trj.xyz"))
    graph = build_irc_energy_graph("job-backfill", tmp_path)
    assert graph is not None
    assert graph["source"] == "WORK/07_PATH/ORCA"
    assert len(graph["nodes"]) == 12
    assert [node["metadata"]["energy_raw"] for node in graph["nodes"][:6]] == FWD_ENERGIES


def test_projection_none_keeps_existing_contract(tmp_path: Path) -> None:
    assert build_irc_energy_graph("job-none", tmp_path) is None


def test_dispatcher_irc_branch_returns_irc_or_pending(tmp_path: Path) -> None:
    _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz",))
    graph = build_energy_graph_from_job("job-dispatch", workflow="irc", method=None, work_dir=tmp_path)
    assert graph["view_type"] == "irc"
    assert graph["metadata"].get("reason") != "workflow_has_no_energy_graph"
    assert [series["id"] for series in graph["series"]] == ["irc_forward"]


def test_dispatcher_irc_pending_when_no_path(tmp_path: Path) -> None:
    (tmp_path / "job.json").write_text("{}", encoding="utf-8")
    graph = build_energy_graph_from_job("job-pending", workflow="irc", method=None, work_dir=tmp_path)
    assert graph["view_type"] == "irc"
    assert graph["metadata"]["reason"] == "irc_path_pending"
    assert graph["series"] == []
    assert graph["nodes"] == []


def test_writer_materialises_single_frame_geometry_per_point(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz", "h2o2_IRC_B_trj.xyz"))
    payload = write_irc_trajectory(tmp_path / "RESULT", target_dir=orca)
    assert payload is not None
    for frame in payload["frames"]:
        ref = frame["geometry_ref"]
        assert ref.startswith("RESULT/irc/") and "_point_" in ref, ref
        point_file = tmp_path / ref
        assert point_file.is_file(), ref
        assert len(point_file.read_text(encoding="utf-8").splitlines()) == frame["atom_count"] + 2
    graph = build_irc_energy_graph("job-points", tmp_path)
    assert graph is not None
    for node in graph["nodes"]:
        direction = node["metadata"]["direction"]
        assert node["geometry_ref"] == (
            f"RESULT/irc/irc_{direction}_point_{node['frame_index']:04d}.xyz"
        )


def test_recorder_concurrent_refresh_is_atomic(tmp_path: Path) -> None:
    orca = _seed_orca(tmp_path, ("h2o2_IRC_F_trj.xyz",))
    recorder = IrcTrajectoryRecorder(tmp_path / "RESULT", orca, min_interval=0.0)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(20):
                _ = recorder.refresh(force=True)
        except BaseException as exc:  # noqa: BLE001 - surface any thread failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    payload = json.loads(
        (tmp_path / "RESULT" / "trajectories" / "irc_trajectory.json").read_text(encoding="utf-8")
    )
    assert payload["frames"]
    assert not list((tmp_path / "RESULT").rglob("*.tmp"))
