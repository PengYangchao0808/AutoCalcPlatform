"""Round-5 PR-02: IRC accepted-but-inert parameters and first-frame endpoints.

The real test agent passed ``step``, saw "Unused ORCA irc kwargs", and lost a
meaningful IRC step-size control; separately, a multi-frame trajectory must
never be reduced to its first frame and published as a formal IRC endpoint.
These tests pin the plan's §6.3 acceptance list:

* explicit ``step`` is refused at every entry (contract top level and level,
  CLI/workflow, runner/remote argv, typed options, ORCA interface);
* omitting ``step`` stays a valid request (no platform-injected default);
* ``initial_hessian`` is refused before ORCA work; the Hessian *handoff* stays;
* multi-frame / truncated / non-finite / reordered endpoint files are refused,
  and the interface→task path cannot materialize them as endpoints.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cccp.io.xyz_validation import SingleFrameXyzError, read_single_frame_xyz

_H2O = "3\n\nO 0.0 0.0 0.0\nH 0.96 0.0 0.0\nH -0.24 0.93 0.0\n"


# --------------------------------------------------------------------------- #
# step: refused at every entry, omission stays valid
# --------------------------------------------------------------------------- #


def test_contract_refuses_explicit_step_at_top_level() -> None:
    from acp.api.submission_contract import SubmissionContractError, validate_workflow_submission

    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "irc",
            input={"source_job_id": "j", "source_product_id": "p", "ts_source": {"schema": "irc_ts_source_v1"}},
            method={"step": 0.1},
            resources=None,
        )
    assert excinfo.value.params()["rule"] == "unsupported_parameter"
    assert excinfo.value.params()["field"] == "method.step"


def test_contract_refuses_step_inside_the_irc_level() -> None:
    from acp.api.submission_contract import SubmissionContractError, validate_workflow_submission

    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "irc",
            input={"source_job_id": "j", "source_product_id": "p", "ts_source": {"schema": "irc_ts_source_v1"}},
            method={"levels": {"irc": {"step": 0.05}}},
            resources=None,
        )
    assert excinfo.value.params()["field"] == "method.levels.irc.step"


def test_step_omission_is_valid() -> None:
    from acp.api.submission_contract import validate_workflow_submission

    validate_workflow_submission(
        "irc",
        input={"source_job_id": "j", "source_product_id": "p", "ts_source": {"schema": "irc_ts_source_v1"}},
        method={"maxpoints": 40},
        resources=None,
    )


def test_workflow_refuses_explicit_step(tmp_path: Path) -> None:
    from acp.calculations.contracts import StructureArtifact
    from acp.workflows.irc import run_irc_workflow

    artifact = StructureArtifact(path=tmp_path / "ts.xyz", source="test")
    with pytest.raises(ValueError, match="step is not executable"):
        run_irc_workflow(artifact, output_dir=tmp_path / "out", step=0.1)


def test_remote_argv_refuses_a_legacy_step_spec() -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.remote.script_gen import build_remote_irc_tail

    spec = JobSpec(
        workflow="irc",
        input={
            "charge": 0,
            "multiplicity": 1,
            "ts_source": {"schema": "irc_ts_source_v1"},
        },
        method={"step": 0.1},
    )
    with pytest.raises(ValueError, match="step is not executable"):
        build_remote_irc_tail(spec, "input.xyz")


def test_remote_argv_omits_step_for_valid_spec() -> None:
    from acp.scheduler.jobs import JobSpec
    from acp.scheduler.remote.script_gen import build_remote_irc_tail

    spec = JobSpec(
        workflow="irc",
        input={
            "charge": 0,
            "multiplicity": 1,
            "ts_source": {"schema": "irc_ts_source_v1"},
        },
        method={"maxpoints": 40},
    )
    cmd = build_remote_irc_tail(spec, "input.xyz")
    assert "--step" not in cmd
    assert cmd[cmd.index("--maxpoints") + 1] == "40"


def test_typed_options_refuse_step_and_initial_hessian() -> None:
    from cccp.calculation.requests import IrcOptions
    from cccp.calculation.tasks.irc import _irc_capability_kwargs

    class _Level:
        method = "r2SCAN-3c"
        basis = "def3c"

    class _Request:
        level = _Level()

    with pytest.raises(ValueError, match="step is not executable"):
        _irc_capability_kwargs(None, _Request(), IrcOptions(step=0.1))
    with pytest.raises(ValueError, match="initial_hessian"):
        _irc_capability_kwargs(None, _Request(), IrcOptions(initial_hessian="auto"))
    kwargs = _irc_capability_kwargs(None, _Request(), IrcOptions(maxpoints=40))
    assert kwargs["max_iter"] == 40
    assert "step" not in kwargs and "initial_hessian" not in kwargs


# --------------------------------------------------------------------------- #
# ORCA interface boundary: refuse before touching the filesystem
# --------------------------------------------------------------------------- #


def test_orca_irc_refuses_step_before_writing_input(tmp_path: Path) -> None:
    from cccp.qc.interfaces.orca import ORCAInterface

    interface = ORCAInterface.__new__(ORCAInterface)
    out = tmp_path / "irc"
    with pytest.raises(ValueError, match="does not support"):
        interface.irc(
            coordinates=np.zeros((3, 3)),
            symbols=["O", "H", "H"],
            output_dir=out,
            step=0.1,
        )
    assert not (out / "irc.inp").exists()


def test_orca_irc_refuses_initial_hessian_before_writing_input(tmp_path: Path) -> None:
    from cccp.qc.interfaces.orca import ORCAInterface

    interface = ORCAInterface.__new__(ORCAInterface)
    out = tmp_path / "irc"
    with pytest.raises(ValueError, match="does not support"):
        interface.irc(
            coordinates=np.zeros((3, 3)),
            symbols=["O", "H", "H"],
            output_dir=out,
            initial_hessian="auto",
        )
    assert not (out / "irc.inp").exists()


# --------------------------------------------------------------------------- #
# strict single-frame endpoint evidence
# --------------------------------------------------------------------------- #


def test_single_frame_reader_accepts_one_complete_frame(tmp_path: Path) -> None:
    path = tmp_path / "endpoint.xyz"
    path.write_text(_H2O, encoding="utf-8")
    coordinates, symbols = read_single_frame_xyz(path)
    assert symbols == ["O", "H", "H"]
    assert coordinates[0] == [0.0, 0.0, 0.0]


def test_single_frame_reader_rejects_multiple_frames(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.xyz"
    path.write_text(_H2O + _H2O, encoding="utf-8")
    with pytest.raises(SingleFrameXyzError):
        read_single_frame_xyz(path)


def test_single_frame_reader_rejects_truncated_and_non_finite(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.xyz"
    truncated.write_text("3\n\nO 0 0 0\nH 0.96 0 0\n", encoding="utf-8")
    with pytest.raises(SingleFrameXyzError):
        read_single_frame_xyz(truncated)
    nonfinite = tmp_path / "nan.xyz"
    nonfinite.write_text("1\nH\nH 0 0 nan\n", encoding="utf-8")
    with pytest.raises(SingleFrameXyzError):
        read_single_frame_xyz(nonfinite)


def test_endpoint_reader_refuses_multi_frame_files(tmp_path: Path) -> None:
    from cccp.qc.interfaces.orca import _read_endpoint_geometry

    multi = tmp_path / "trajectory.xyz"
    multi.write_text(_H2O + _H2O, encoding="utf-8")
    expected = ["O", "H", "H"]
    assert _read_endpoint_geometry(multi, expected) is None
    single = tmp_path / "endpoint.xyz"
    single.write_text(_H2O, encoding="utf-8")
    geometry = _read_endpoint_geometry(single, expected)
    assert geometry is not None and geometry.shape == (3, 3)


def test_endpoint_reader_refuses_reordered_symbols(tmp_path: Path) -> None:
    from cccp.qc.interfaces.orca import _read_endpoint_geometry

    reordered = tmp_path / "reordered.xyz"
    reordered.write_text("3\n\nH 0.96 0 0\nO 0 0 0\nH -0.24 0.93 0\n", encoding="utf-8")
    assert _read_endpoint_geometry(reordered, ["O", "H", "H"]) is None
