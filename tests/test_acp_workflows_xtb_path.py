"""Tests for the XtbPathSearch workflow (PES2TS → ACP, work unit X1′-B).

Uses a FAKE xtb binary + patched ``subprocess.run`` (pattern from
``tests/test_cccp_xtb_path.py``) so no real QC runs by default; the single
real-binary smoke test is gated behind ``--run-slow`` / ``requires_xtb``.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from acp.results.pes_profile import load_pes_profile, normalize_pes_profile
from acp.storage.manifest import ResultManifest
from acp.workflows.xtb_path import (
    XTB_PATH_E_CHARGE,
    XTB_PATH_E_OUTPUT,
    XTB_PATH_E_RECIPE,
    XTB_PATH_E_SCHEMA,
    XTB_PATH_E_SOURCE,
    XTB_PATH_E_XTB,
    XtbPathInputError,
    XtbPathSearchError,
    run_xtb_path_search,
)
from tests.conftest import requires_xtb

# ── fixtures ────────────────────────────────────────────────────────────

START_XYZ = (
    "3\nH3 start\n"
    "H 0.000000 0.000000 0.000000\n"
    "H 1.000000 0.000000 0.000000\n"
    "H 0.000000 1.000000 0.000000\n"
)

END_XYZ = (
    "3\nH3 end\n"
    "H 0.000000 0.000000 0.000000\n"
    "H 1.500000 0.000000 0.000000\n"
    "H 0.000000 1.000000 0.000000\n"
)

RECIPE_PATH_INP = (
    "$path\n"
    "   nrun=1\n"
    "   npoint=50\n"
    "   anopt=10\n"
    "   kpush=0.003\n"
    "   kpull=-0.015\n"
    "   ppull=0.05\n"
    "   alp=0.5\n"
    "$end\n"
)

REQUEST_SHA256 = "reqsha256abc000000000000000000000000000000000000000000000000000"
CONFIG_DIGEST = "cfgdigest123"

FAKE_XTB_TRAJECTORY = """3
Frame 0 | energy: -100.10000000
H 0.000000 0.000000 0.000000
H 1.000000 0.000000 0.000000
H 0.000000 1.000000 0.000000
3
Frame 1 | energy=-100.05000000
H 0.000000 0.000000 0.000000
H 1.500000 0.000000 0.000000
H 0.000000 1.000000 0.000000
"""


def _base_request() -> dict[str, Any]:
    return {
        "schema_version": "pes2ts_xtb_path_request_v1",
        "reaction_id": "RXN_0000000001",
        "source": {
            "source_type": "xyz_text_pair",
            "start_xyz": START_XYZ,
            "end_xyz": END_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "recipe": {
            "path_inp_text": RECIPE_PATH_INP,
            "gfn_level": 2,
            "uhf": 0,
            "threads": 4,
            "timeout_seconds": 1800,
            "seed": None,
            "extra_args": [],
        },
        "provenance": {
            "plan_sha256": None,
            "config_digest": CONFIG_DIGEST,
            "request_sha256": REQUEST_SHA256,
            "adapter_version": "pes2ts_xtb_path_request_v1",
        },
    }


def _with_recipe(recipe: dict[str, Any]) -> dict[str, Any]:
    request = _base_request()
    request["recipe"] = {**request["recipe"], **recipe}
    return request


def _with_source(source: dict[str, Any]) -> dict[str, Any]:
    request = _base_request()
    request["source"] = {**request["source"], **source}
    return request


def _write_fake_xtb(tmp_path: Path) -> Path:
    """Write an executable stub that advertises a version line."""
    exe = tmp_path / "fake_xtb"
    exe.write_text("#!/bin/sh\necho 'xtb 6.7.1'\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def _config_with_fake_xtb(fake_exe: Path, sample_config: dict[str, object]) -> dict[str, Any]:
    config = copy.deepcopy(sample_config)
    executables = dict(config.get("executables") or {})  # type: ignore[arg-type]
    executables["xtb"] = {"path": str(fake_exe)}
    config["executables"] = executables
    return config  # type: ignore[return-value]


def _install_fake_xtb_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    trajectory: str | None = FAKE_XTB_TRAJECTORY,
    returncode: int = 0,
) -> dict[str, object]:
    """Patch ``subprocess.run`` with a recording fake.

    The patch lands on the shared ``subprocess`` module (same idiom as
    ``tests/test_cccp_xtb_path.py``), so the fake also answers the version
    probe issued by ``cccp.software.detect_version`` (``--version``, no cwd).
    """
    captured: dict[str, object] = {}

    def _fake_run(
        cmd: list[str],
        *,
        cwd: Path | None = None,
        capture_output: bool = False,
        text: bool = False,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        _ = text
        _ = timeout
        _ = kwargs
        if "--version" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="xtb 6.7.1\n", stderr=""
            )
        if cwd is not None and "--path" in cmd:
            captured["cmd"] = cmd
            captured["env"] = env or {}
            if trajectory is not None:
                (Path(cwd) / "xtbpath.xyz").write_text(trajectory, encoding="utf-8")
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout="path ok\n", stderr=""
        )

    monkeypatch.setattr("cccp.qc.interfaces.xtb_path.subprocess.run", _fake_run)
    return captured


def _assert_xyz_atom_consistent(path: Path, expected_atoms: int) -> None:
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    declared = int(lines[0].strip())
    assert declared == expected_atoms
    coord_lines = [line for line in lines[2:] if line.strip()]
    assert len(coord_lines) == declared
    for line in coord_lines:
        parts = line.split()
        assert len(parts) >= 4
        float(parts[1])
        float(parts[2])
        float(parts[3])


def _run_fake_path_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
    *,
    request: dict[str, Any] | None = None,
    trajectory: str | None = FAKE_XTB_TRAJECTORY,
    returncode: int = 0,
) -> tuple[Any, dict[str, object], Path]:
    fake_exe = _write_fake_xtb(tmp_path)
    config = _config_with_fake_xtb(fake_exe, sample_config)
    captured = _install_fake_xtb_run(monkeypatch, trajectory=trajectory, returncode=returncode)
    output_dir = tmp_path / "task_out"
    result = run_xtb_path_search(
        request if request is not None else _base_request(),
        output_dir=output_dir,
        config=config,
    )
    return result, captured, output_dir


# ── success path ────────────────────────────────────────────────────────


def test_run_xtb_path_search_writes_acp_standard_products(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
) -> None:
    result, captured, output_dir = _run_fake_path_search(tmp_path, monkeypatch, sample_config)

    assert result.status == "completed"
    assert result.metadata["frames_count"] == 2
    assert result.metadata["request_sha256"] == REQUEST_SHA256
    assert result.metadata["workflow"] == "XtbPathSearch"
    assert result.metadata["xtb_executable_sha256"] is not None
    assert result.metadata["xtb_version"] == "xtb 6.7.1"

    # Recipe faithfulness: path.inp verbatim + argv carries recipe knobs.
    run_dir = output_dir / "WORK" / "07_PATH" / "xtb_path_001"
    assert (run_dir / "path.inp").read_text(encoding="utf-8") == RECIPE_PATH_INP
    assert (run_dir / "start.xyz").read_text(encoding="utf-8") == START_XYZ
    assert (run_dir / "end.xyz").read_text(encoding="utf-8") == END_XYZ
    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("--gfn") + 1] == "2"
    assert cmd[cmd.index("--uhf") + 1] == "0"
    assert cmd[cmd.index("--chrg") + 1] == "0"
    assert cmd[cmd.index("-P") + 1] == "4"
    assert isinstance(env, dict)
    assert env["OMP_NUM_THREADS"] == "4"

    # (a) result manifest v2 with expected products.
    manifest_path = output_dir / "RESULT" / "result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 2
    assert manifest["workflow"] == "XtbPathSearch"
    assert manifest["status"] == "completed"
    products = {entry["id"]: entry for entry in manifest["products"]}
    assert products["pes_profile"]["kind"] == "pes_profile"
    assert products["pes_profile"]["path"] == "pes_search/pes_profile.json"
    assert products["pes_profile"]["metadata"]["request_sha256"] == REQUEST_SHA256
    assert products["trajectory"]["kind"] == "trajectory"
    assert products["trajectory"]["path"] == "pes_search/xtbpath.xyz"
    assert products["path_frame_000"]["kind"] == "structure"
    assert products["path_frame_000"]["path"] == "pes_search/path_frames/path_frame_000.xyz"
    assert products["path_frame_000"]["metadata"]["frame_index"] == 0
    assert products["path_frame_001"]["kind"] == "structure"
    assert "xtb_executable_sha256" in products["pes_profile"]["metadata"]

    # Round-trip through the ACP manifest reader.
    loaded = ResultManifest.read(output_dir / "RESULT")
    assert loaded.workflow == "XtbPathSearch"
    assert loaded.status == "completed"
    assert len(loaded.products) == 4

    # (b) pes_profile.json with pes_profile_v2 + viewer frame fields.
    profile_path = output_dir / "RESULT" / "pes_search" / "pes_profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert profile["schema_version"] == "pes_profile_v2"
    assert profile["source"] == "xtb_peb"
    assert profile["frames_count"] == 2
    assert len(profile["frames"]) == 2
    for frame in profile["frames"]:
        assert isinstance(frame["index"], int)
        assert frame["geometry_path"].startswith("path_frames/path_frame_")
        assert frame["scan_energy_hartree"] is not None
    assert profile["frames"][0]["scan_energy_hartree"] == pytest.approx(-100.1)
    assert profile["frames"][1]["scan_energy_hartree"] == pytest.approx(-100.05)
    assert profile["provenance"]["request_sha256"] == REQUEST_SHA256
    assert profile["provenance"]["path_inp_sha256"]
    assert profile["path_profile"]["source"] == "xtb_peb"

    # (c) per-frame xyz atom-consistent + raw trajectory present.
    frame_0 = output_dir / "RESULT" / "pes_search" / "path_frames" / "path_frame_000.xyz"
    _assert_xyz_atom_consistent(frame_0, expected_atoms=3)
    frame_1 = frame_0.with_name("path_frame_001.xyz")
    _assert_xyz_atom_consistent(frame_1, expected_atoms=3)
    trajectory = output_dir / "RESULT" / "pes_search" / "xtbpath.xyz"
    trajectory_text = trajectory.read_text(encoding="utf-8")
    assert trajectory_text == FAKE_XTB_TRAJECTORY
    assert trajectory_text.count("\n3\n") + (1 if trajectory_text.startswith("3\n") else 0) >= 2

    # Zone-C pointer file written for the file-tree summary view.
    summary = json.loads((output_dir / "result_summary.json").read_text(encoding="utf-8"))
    assert summary["workflow"] == "XtbPathSearch"
    assert any(item["path"] == "RESULT/pes_search/pes_profile.json" for item in summary["products"])


def test_pes_profile_normalizes_for_s2_viewer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
) -> None:
    """A ``source="xtb_peb"`` pes_profile_v2 must satisfy the /s2/profile route."""
    _result, _captured, output_dir = _run_fake_path_search(tmp_path, monkeypatch, sample_config)
    profile_path = output_dir / "RESULT" / "pes_search" / "pes_profile.json"

    normalized = load_pes_profile(profile_path, source_path="RESULT/pes_search/pes_profile.json")
    assert normalized["workflow"] == "XtbPathSearch"
    assert normalized["mode"] == "xtb_path"
    scan = normalized["scan"]
    assert scan["frame_count"] == 2
    frames = scan["frames"]
    assert len(frames) == 2
    assert normalized["energy_profile"]["raw_hartree"] == [
        pytest.approx(-100.1),
        pytest.approx(-100.05),
    ]

    from acp.api.v1_schemas import S2FrameModel

    for frame in frames:
        model = S2FrameModel(**frame)
        assert model.geometry_path.startswith("path_frames/path_frame_")
        assert model.scan_energy_hartree is not None

    # Direct normalize call (no file IO) also accepts the payload.
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    direct = normalize_pes_profile(payload)
    assert direct["scan"]["frame_count"] == 2


def test_recipe_seed_and_extra_args_forwarded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
) -> None:
    request = _with_recipe({"seed": 42, "extra_args": ["--norestart", "--cma"], "threads": 2})
    _result, captured, output_dir = _run_fake_path_search(
        tmp_path, monkeypatch, sample_config, request=request
    )
    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("--seed") + 1] == "42"
    assert cmd[-2:] == ["--norestart", "--cma"]
    assert isinstance(env, dict)
    assert env["OMP_NUM_THREADS"] == "2"
    assert (output_dir / "WORK" / "07_PATH" / "xtb_path_001" / "path.inp").read_text(
        encoding="utf-8"
    ) == RECIPE_PATH_INP


# ── typed validation failures ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("request_factory", "expected_code"),
    [
        (
            lambda: {**_base_request(), "schema_version": "pes2ts_xtb_path_request_v2"},
            XTB_PATH_E_SCHEMA,
        ),
        (lambda: {**_base_request(), "schema_version": None}, XTB_PATH_E_SCHEMA),
        (lambda: _with_source({"start_xyz": "   "}), XTB_PATH_E_SOURCE),
        (lambda: _with_source({"end_xyz": ""}), XTB_PATH_E_SOURCE),
        (lambda: _with_source({"start_xyz": None}), XTB_PATH_E_SOURCE),
        (lambda: _with_source({"charge": None}), XTB_PATH_E_CHARGE),
        (lambda: _with_source({"charge": "0"}), XTB_PATH_E_CHARGE),
        (lambda: _with_source({"multiplicity": True}), XTB_PATH_E_CHARGE),
        (lambda: _with_recipe({"path_inp_text": None}), XTB_PATH_E_RECIPE),
        (lambda: _with_recipe({"path_inp_text": "  "}), XTB_PATH_E_RECIPE),
        (lambda: _with_recipe({"gfn_level": None}), XTB_PATH_E_RECIPE),
        (lambda: _with_recipe({"uhf": "0"}), XTB_PATH_E_RECIPE),
        (lambda: _with_recipe({"threads": None}), XTB_PATH_E_RECIPE),
        (lambda: {**_base_request(), "recipe": None}, XTB_PATH_E_RECIPE),
        (lambda: {**_base_request(), "source": None}, XTB_PATH_E_SOURCE),
    ],
    ids=[
        "wrong_schema_version",
        "missing_schema_version",
        "empty_start_xyz",
        "empty_end_xyz",
        "missing_start_xyz",
        "missing_charge",
        "charge_not_int",
        "multiplicity_bool",
        "missing_path_inp_text",
        "blank_path_inp_text",
        "missing_gfn_level",
        "uhf_not_int",
        "missing_threads",
        "missing_recipe",
        "missing_source",
    ],
)
def test_invalid_request_raises_typed_error(
    request_factory: Any,
    expected_code: str,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "never_created"
    with pytest.raises(XtbPathInputError) as excinfo:
        run_xtb_path_search(request_factory(), output_dir=output_dir)
    assert expected_code in str(excinfo.value)
    assert not output_dir.exists()


# ── xTB / output failures: typed, never status="completed" ─────────────


def test_xtb_failure_raises_and_writes_no_completed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
) -> None:
    with pytest.raises(XtbPathSearchError) as excinfo:
        _run_fake_path_search(tmp_path, monkeypatch, sample_config, returncode=1, trajectory=None)
    assert XTB_PATH_E_XTB in str(excinfo.value)
    manifest_path = tmp_path / "task_out" / "RESULT" / "result_manifest.json"
    assert not manifest_path.exists()


def test_missing_trajectory_raises_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
) -> None:
    # returncode 0 but no trajectory file → interface reports failure.
    with pytest.raises(XtbPathSearchError) as excinfo:
        _run_fake_path_search(tmp_path, monkeypatch, sample_config, returncode=0, trajectory=None)
    assert XTB_PATH_E_XTB in str(excinfo.value) or XTB_PATH_E_OUTPUT in str(excinfo.value)
    manifest_path = tmp_path / "task_out" / "RESULT" / "result_manifest.json"
    assert not manifest_path.exists()


def test_backend_unavailable_raises_typed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
) -> None:
    config = _config_with_fake_xtb(tmp_path / "missing_xtb", sample_config)
    monkeypatch.setattr("cccp.qc.interfaces.xtb_path.resolve_executable", lambda *a, **k: None)
    monkeypatch.setattr("cccp.qc.interfaces.xtb.resolve_executable", lambda *a, **k: None)
    with pytest.raises(XtbPathSearchError) as excinfo:
        run_xtb_path_search(_base_request(), output_dir=tmp_path / "out", config=config)
    assert XTB_PATH_E_XTB in str(excinfo.value)
    assert not (tmp_path / "out" / "RESULT" / "result_manifest.json").exists()


# ── real-binary smoke (gated) ───────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.integration
@requires_xtb
def test_real_xtb_path_search_smoke(
    tmp_path: Path,
    sample_config: dict[str, object],
) -> None:
    """Real-xTB smoke: completed-with-artifacts or a typed XtbPathSearchError."""
    request = _base_request()
    request["source"] = {
        "source_type": "xyz_text_pair",
        "start_xyz": ("2\nH2 start\nH 0.000000 0.000000 0.000000\nH 0.740000 0.000000 0.000000\n"),
        "end_xyz": ("2\nH2 end\nH 0.000000 0.000000 0.000000\nH 0.900000 0.000000 0.000000\n"),
        "charge": 0,
        "multiplicity": 1,
    }
    request["recipe"] = {
        "path_inp_text": (
            "$path\n"
            "   nrun=1\n"
            "   npoint=10\n"
            "   anopt=5\n"
            "   kpush=0.003\n"
            "   kpull=-0.015\n"
            "   ppull=0.05\n"
            "   alp=0.5\n"
            "$end\n"
        ),
        "gfn_level": 2,
        "uhf": 0,
        "threads": 1,
        "timeout_seconds": 120,
        "seed": None,
        "extra_args": [],
    }
    output_dir = tmp_path / "real_out"
    try:
        result = run_xtb_path_search(request, output_dir=output_dir, config=sample_config)
    except XtbPathSearchError as exc:
        assert exc.code in {XTB_PATH_E_XTB, XTB_PATH_E_OUTPUT}
        manifest_path = output_dir / "RESULT" / "result_manifest.json"
        assert not manifest_path.exists() or (
            json.loads(manifest_path.read_text(encoding="utf-8")).get("status") != "completed"
        )
        return
    assert result.status == "completed"
    manifest = json.loads(
        (output_dir / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "completed"
    assert manifest["workflow"] == "XtbPathSearch"
