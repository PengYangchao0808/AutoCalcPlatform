"""Tests for the IRC frame projection (todo 39).

Covers: VIEW_REGISTRY irc entry, block parsing, energy-comment parsing,
two-series path-order projection (NEVER reordered by energy — proven with
non-monotonic fixtures), and the single-direction / missing-file degradations.
"""

from __future__ import annotations

import json
from pathlib import Path

from acp.results.frames import VIEW_REGISTRY
from acp.results.irc_projection import (
    build_irc_energy_graph,
    frame_energy_from_comment,
    parse_irc_xyz_frames,
)


def _frame(comment: str, i: int) -> str:
    return f"3\n{comment}\nO 0 0 0\nH 0 0 {i + 1}\nH 0 {i + 1} 0\n"


def _make_task(
    tmp_path: Path,
    *,
    forward_comments: list[str] | None = None,
    reverse_comments: list[str] | None = None,
) -> Path:
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 2,
        "task_id": "",
        "workflow": "irc",
        "status": "completed",
        "products": [],
    }
    (result_dir / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    irc_dir = result_dir / "irc"
    irc_dir.mkdir(parents=True, exist_ok=True)
    if forward_comments is not None:
        text = "".join(_frame(c, i) for i, c in enumerate(forward_comments))
        (irc_dir / "irc_forward.xyz").write_text(text, encoding="utf-8")
    if reverse_comments is not None:
        text = "".join(_frame(c, i) for i, c in enumerate(reverse_comments))
        (irc_dir / "irc_reverse.xyz").write_text(text, encoding="utf-8")
    return tmp_path


def test_view_registry_has_real_irc_entry() -> None:
    spec = VIEW_REGISTRY["irc"]
    assert spec.x_unit == "frame"
    assert spec.node_type == "irc_point"
    assert spec.view_type == "irc"


def test_parse_irc_xyz_frames_counts_blocks(tmp_path: Path) -> None:
    path = tmp_path / "irc_forward.xyz"
    path.write_text(
        "2\nIRC forward endpoint\nC 0 0 0\nH 0 0 1\n"
        "2\nE = -76.1\nC 0 0 0\nH 0 0 2\n"
        "2\nstep 3\nC 0 0 0\nH 0 0 3\n",
        encoding="utf-8",
    )
    blocks = parse_irc_xyz_frames(path)
    assert [b.index for b in blocks] == [0, 1, 2]
    assert blocks[0].comment == "IRC forward endpoint"
    assert blocks[1].comment == "E = -76.1"
    assert all(b.atom_count == 2 for b in blocks)


def test_parse_irc_xyz_frames_empty_and_missing(tmp_path: Path) -> None:
    assert parse_irc_xyz_frames(tmp_path / "nope.xyz") == []
    empty = tmp_path / "empty.xyz"
    empty.write_text("not an xyz\n", encoding="utf-8")
    assert parse_irc_xyz_frames(empty) == []


def test_frame_energy_from_comment() -> None:
    assert frame_energy_from_comment("E = -76.1234") == -76.1234
    assert frame_energy_from_comment("energy: -0.5") == -0.5
    assert frame_energy_from_comment("Energy = 3.25") == 3.25
    assert frame_energy_from_comment("IRC forward endpoint") is None
    assert frame_energy_from_comment("") is None
    assert frame_energy_from_comment("frame 2.50 endpoint") == 2.50


def test_projection_two_series_path_order(tmp_path: Path) -> None:
    """Non-monotonic energies MUST NOT reorder nodes — file order wins."""
    task = _make_task(
        tmp_path,
        forward_comments=["e = -0.1", "e = 5.0", "e = 1.0"],  # desc-asc mix
        reverse_comments=["e = -0.1", "e = 4.0", "e = 2.0"],
    )
    graph = build_irc_energy_graph("job-1", task)
    assert graph is not None
    assert graph["view_type"] == "irc"
    assert graph["title"] == "IRC 能量剖面"

    nodes = graph["nodes"]
    assert [n["id"] for n in nodes] == [
        "irc_forward_0", "irc_forward_1", "irc_forward_2",
        "irc_reverse_0", "irc_reverse_1", "irc_reverse_2",
    ]
    # Path order = file order: x ascends within each direction, never by energy
    assert [n["x"] for n in nodes] == [0.0, 1.0, 2.0, 0.0, 1.0, 2.0]
    assert [n["frame_index"] for n in nodes] == [0, 1, 2, 0, 1, 2]
    assert all(n["type"] == "irc_point" for n in nodes)
    assert all(n["geometry_ref"] for n in nodes)
    assert nodes[0]["metadata"]["direction"] == "forward"
    assert nodes[3]["metadata"]["direction"] == "reverse"
    # Forward energies descend then ascend — projection must NOT sort them
    fwd_raw = [n["metadata"]["energy_raw"] for n in nodes[:3]]
    assert fwd_raw == [-0.1, 5.0, 1.0]
    # Relative to the global min (-0.1)
    assert nodes[1]["energy"] == 5.1
    assert nodes[3]["energy"] == 0.0

    series_ids = [s["id"] for s in graph["series"]]
    assert series_ids == ["irc_forward", "irc_reverse"]
    assert graph["default_series"] == "irc_forward"
    fwd_series = graph["series"][0]
    # Aligned to the flat node list: reverse slots are None
    assert fwd_series["values"] == [0.0, 5.1, 1.1, None, None, None]
    rev_series = graph["series"][1]
    assert rev_series["values"] == [None, None, None, 0.0, 4.1, 2.1]


def test_projection_no_energy_omits_series(tmp_path: Path) -> None:
    """Writer titles without energy → nodes only, no series, x_unit frame."""
    task = _make_task(
        tmp_path,
        forward_comments=["IRC forward endpoint"],
        reverse_comments=["IRC reverse endpoint"],
    )
    graph = build_irc_energy_graph("job-2", task)
    assert graph is not None
    assert graph["series"] == []
    assert len(graph["nodes"]) == 2
    assert all(n["energy"] is None for n in graph["nodes"])
    assert graph["x_axis"]["unit"] == "frame"
    assert graph["metadata"]["energy_available"] is False


def test_projection_single_direction_warning(tmp_path: Path) -> None:
    """Only one direction file → single series + warning note (plan QA)."""
    task = _make_task(tmp_path, forward_comments=["e = -1.0", "e = 0.5"])
    graph = build_irc_energy_graph("job-3", task)
    assert graph is not None
    assert [s["id"] for s in graph["series"]] == ["irc_forward"]
    assert len(graph["nodes"]) == 2
    assert "missing_direction:reverse" in graph["metadata"]["warnings"]


def test_projection_none_when_missing_both(tmp_path: Path) -> None:
    task = _make_task(tmp_path)
    assert build_irc_energy_graph("job-4", task) is None


def test_projection_endpoint_annotation_on_descent(tmp_path: Path) -> None:
    """Endpoint marker only when the last frame is the directional minimum."""
    task = _make_task(
        tmp_path,
        forward_comments=["e = 5.0", "e = 2.0", "e = 0.0"],
        reverse_comments=["e = 5.0", "e = 0.5", "e = 3.0"],
    )
    graph = build_irc_energy_graph("job-5", task)
    assert graph is not None
    ann_ids = [a["id"] for a in graph["annotations"]]
    assert ann_ids == ["irc_forward_endpoint"]  # reverse ends ABOVE its min


def test_projection_annotations_use_contract_emitters() -> None:
    """Contract lock: projection is emitted via TrajectoryFrame/Annotation only."""
    import acp.results.irc_projection as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "TrajectoryFrame(" in source
    assert "TrajectoryAnnotation(" in source
    assert ".to_node(spec.node_type)" in source
    assert ".to_annotation()" in source
    # Path-order invariant: no energy sort anywhere in the module
    assert "sort" not in source
