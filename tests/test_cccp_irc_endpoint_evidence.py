"""Round-5 PR-04: per-direction IRC evidence and endpoint diagnosis.

The real agent had a 60-frame two-direction trajectory and a terminal item that
still reported FAILED with "IRC produced no endpoint geometries".  This suite
pins the plan's §8.3 acceptance list:

* the H2O2 fixture (trajectory + single-frame files, both directions stopped at
  the iteration limit) can never claim bidirectional convergence;
* missing directional evidence reports ``unknown`` instead of inheriting the
  process success;
* a trajectory's last frame is a ``last_observed_geometry``, never a formal
  endpoint;
* forward/reverse are processed separately (the full trajectory's last frame
  does not represent both ends);
* one converged + one limit-stopped direction is reported per direction and as
  incomplete overall;
* diagnostics survive when the task fails.

The concrete complained run remains NOT_VERIFIED until the user supplies the raw
sample (plan §8.4); these fixtures prove the surrounding logic only.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "irc"


def _raw_result(log_path: Path | None, *, success: bool = True, metadata: dict | None = None):
    return SimpleNamespace(
        log_file=str(log_path) if log_path is not None else None,
        metadata=dict(metadata or {}),
        success=success,
        error_message=None,
    )


def _endpoint(path: Path) -> dict:
    return {"path": str(path), "coordinates": ((0.0, 0.0, 0.0),), "symbols": ("H",)}


# --------------------------------------------------------------------------- #
# H2O2 fixture: found endpoints but a limit-stopped run is not convergence
# --------------------------------------------------------------------------- #


def test_h2o2_fixture_cannot_claim_bidirectional_convergence() -> None:
    from cccp.calculation.tasks.irc import (
        _direction_diagnostics,
        _overall_convergence,
        completed_directions,
    )

    raw = _raw_result(_FIXTURES / "orca_irc_h2o2.out")
    endpoints = {
        "forward": _endpoint(_FIXTURES / "h2o2_IRC_F.xyz"),
        "reverse": _endpoint(_FIXTURES / "h2o2_IRC_B.xyz"),
    }
    completed = completed_directions(raw, endpoints, True)
    assert completed == set()
    diagnostics = _direction_diagnostics(raw, endpoints, completed, ("forward", "reverse"))
    for direction in ("forward", "reverse"):
        entry = diagnostics[direction]
        assert entry["endpoint_discovery"] == "found"
        assert entry["convergence"] == "not_converged"
        assert entry["convergence_evidence"] == "iteration_limit"
    assert _overall_convergence(("forward", "reverse"), completed, diagnostics) is False


def test_missing_log_reports_unknown_not_converged() -> None:
    from cccp.calculation.tasks.irc import (
        _direction_diagnostics,
        _overall_convergence,
        completed_directions,
    )

    raw = _raw_result(None)
    endpoints = {"forward": _endpoint(_FIXTURES / "h2o2_IRC_F.xyz")}
    completed = completed_directions(raw, endpoints, True)
    diagnostics = _direction_diagnostics(raw, endpoints, completed, ("forward", "reverse"))
    assert diagnostics["forward"]["convergence"] == "unknown"
    assert diagnostics["reverse"]["convergence"] == "unknown"
    assert diagnostics["reverse"]["endpoint_discovery"] == "missing"
    assert _overall_convergence(("forward", "reverse"), completed, diagnostics) is None


def test_direction_status_metadata_wins() -> None:
    from cccp.calculation.tasks.irc import _direction_diagnostics, completed_directions

    raw = _raw_result(
        None,
        metadata={"direction_status": {"forward": "completed", "reverse": "iteration_limit"}},
    )
    endpoints = {"forward": _endpoint(_FIXTURES / "h2o2_IRC_F.xyz")}
    completed = completed_directions(raw, endpoints, True)
    assert completed == {"forward"}
    diagnostics = _direction_diagnostics(raw, endpoints, completed, ("forward", "reverse"))
    assert diagnostics["forward"]["convergence"] == "converged"
    assert diagnostics["reverse"]["convergence"] == "not_converged"
    assert diagnostics["reverse"]["convergence_evidence"] == "direction_status=iteration_limit"


def test_one_converged_one_limit_stopped_is_incomplete() -> None:
    from cccp.calculation.tasks.irc import (
        _direction_diagnostics,
        _overall_convergence,
        completed_directions,
    )

    log_text = (
        "FORWARD IRC\n"
        "IRC CONVERGENCE ACHIEVED\n"
        "BACKWARD IRC\n"
        "MAXIMUM NUMBER OF ITERATIONS REACHED - STOPPING IRC RUN\n"
    )
    log_path = _FIXTURES / "_tmp_one_sided.out"
    log_path.write_text(log_text, encoding="utf-8")
    try:
        raw = _raw_result(log_path)
        endpoints = {
            "forward": _endpoint(_FIXTURES / "h2o2_IRC_F.xyz"),
            "reverse": _endpoint(_FIXTURES / "h2o2_IRC_B.xyz"),
        }
        completed = completed_directions(raw, endpoints, True)
        assert completed == {"forward"}
        diagnostics = _direction_diagnostics(raw, endpoints, completed, ("forward", "reverse"))
        assert diagnostics["forward"]["convergence"] == "converged"
        assert diagnostics["reverse"]["convergence"] == "not_converged"
        assert _overall_convergence(("forward", "reverse"), completed, diagnostics) is False
    finally:
        log_path.unlink()


# --------------------------------------------------------------------------- #
# last observed geometry: readable, never a formal endpoint
# --------------------------------------------------------------------------- #


def _trajectory() -> dict:
    return {
        "frames": [
            {"index": 0, "direction": "forward", "energy_hartree": -151.52},
            {"index": 1, "direction": "forward", "energy_hartree": -151.53},
            {"index": 2, "direction": "forward", "energy_hartree": -151.54},
        ],
        "geometry_files": {"forward": "trajectories/irc_forward.xyz"},
    }


def test_last_observed_geometry_is_not_an_endpoint() -> None:
    from cccp.calculation.results import IrcDirectionResult
    from cccp.calculation.tasks.irc import _direction_entries, _last_observed_geometries

    observed = _last_observed_geometries(_trajectory(), ("forward", "reverse"))
    assert observed["forward"]["frame_index"] == 2
    assert "not a formal endpoint" in observed["forward"]["note"]
    assert "reverse" not in observed

    entries = _direction_entries(
        ("forward", "reverse"),
        {},
        set(),
        _trajectory(),
        _FIXTURES,
        "orca",
        diagnostics={
            "forward": {"endpoint_discovery": "missing", "convergence": "unknown"},
            "reverse": {"endpoint_discovery": "missing", "convergence": "unknown"},
        },
    )
    by_direction = {entry.direction.value: entry for entry in entries}
    forward = by_direction["forward"]
    assert isinstance(forward, IrcDirectionResult)
    assert forward.coordinates is None  # no formal endpoint
    assert forward.endpoint_discovery == "missing"
    assert forward.last_observed_geometry is not None
    assert forward.last_observed_geometry["frame_index"] == 2
    assert forward.frame_identity == "forward:frame_0002"
    assert by_direction["reverse"].last_observed_geometry is None


def test_one_sided_trajectory_does_not_represent_both_ends() -> None:
    from cccp.calculation.tasks.irc import _last_observed_geometries

    forward_only = _last_observed_geometries(_trajectory(), ("forward", "reverse"))
    assert set(forward_only) == {"forward"}


# --------------------------------------------------------------------------- #
# the task metadata keeps diagnostics (also on failure)
# --------------------------------------------------------------------------- #


def test_task_metadata_keeps_direction_diagnostics(tmp_path: Path) -> None:
    """A limit-stopped run keeps per-direction facts and reports no convergence."""
    from tests.test_cccp_task_irc import (
        _qc_result,
        _run,
        _RecordingBackend,
        _write_endpoint,
    )

    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    _write_endpoint(target_dir / "ts_IRC_F.xyz", 0.1)
    backend = _RecordingBackend(
        [
            _qc_result(
                success=True,
                log_file=str(_FIXTURES / "orca_irc_h2o2.out"),
                metadata={
                    "endpoints": {"forward": str(target_dir / "ts_IRC_F.xyz")},
                },
            )
        ]
    )

    result = _run(backend, target_dir=target_dir)

    # the task may keep its historical failure mapping, but the diagnostics and
    # filtered counts are present and the overall convergence is not faked
    metadata = result.metadata
    forward = metadata["direction_diagnostics"]["forward"]
    assert forward["endpoint_discovery"] == "found"
    assert forward["convergence"] == "not_converged"
    assert forward["convergence_evidence"] == "iteration_limit"
    assert metadata["filtered_endpoint_count"] == 1
    assert metadata["endpoint_count"] == 0
    assert result.converged is False
    assert len(result.payload.directions) == 2
