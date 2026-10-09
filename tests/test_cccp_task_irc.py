"""cccp IRC task core — plan todo 21 (bidirectional IRC in cccp, products in ACP).

Pure-cccp tests (never import ``acp``): an independent call drives the
bidirectional IRC internally as ONE capability call and returns the typed
``IrcPayload`` (per-direction endpoint / energy / converged / steps), keeps
valid sub-results on one-way failure, enforces the transition-state role,
carries NO ``ts_mode``, and never joins a ``StepKind``/plan.  The pure
endpoint classification (``cccp.calculation.irc_endpoints``) is exercised
from its new home.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import StructureRole
from cccp.calculation.errors import TaskInputError
from cccp.calculation.requests import (
    IrcDirection,
    IrcOptions,
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, IrcPayload
from cccp.calculation.tasks.irc import (
    completed_directions,
    resolve_direction,
    run_irc,
)

_COORDS = (
    (0.0, 0.0, 0.0),
    (1.5, 0.0, 0.0),
    (-0.5, 0.9, 0.0),
    (-0.5, -0.9, 0.0),
    (2.0, 0.9, 0.0),
    (2.0, -0.9, 0.0),
)
_SYMBOLS = ("C", "C", "H", "H", "H", "H")
_SOURCES = (
    "cccp/calculation/tasks/irc.py",
    "cccp/calculation/irc_endpoints.py",
    "cccp/calculation/irc_trajectory.py",
)


class _RecordingBackend:
    """Minimal ``irc`` capability fake recording call kwargs."""

    name = "orca"

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def irc(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        self.calls.append(
            {
                "symbols": list(symbols),
                "charge": charge,
                "multiplicity": multiplicity,
                "output_dir": output_dir,
                "kwargs": dict(kwargs),
            }
        )
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        from cccp.backends.base import QCResult

        return QCResult(success=True, symbols=list(symbols))


def _request(
    *,
    options: IrcOptions | None = None,
    output_dir: Path | None = None,
    role: StructureRole = StructureRole.TRANSITION_STATE,
    task: TaskKind = TaskKind.IRC,
) -> TaskRequest:
    return TaskRequest(
        task=task,
        structure=StructureInput(coordinates=_COORDS, symbols=_SYMBOLS, role=role),
        charge=0,
        multiplicity=1,
        level=MethodSpec(method="r2SCAN-3c"),
        backend="orca",
        options=options,
        output_dir=output_dir,
    )


def _write_endpoint(path: Path, offset: float) -> None:
    from cccp.utils import file_io

    coords = np.asarray(_COORDS, dtype=float) + offset
    file_io.write_xyz(path, coords, list(_SYMBOLS), title="IRC endpoint")


def _write_trj(path: Path, direction: str, energies: tuple[float, ...]) -> None:
    blocks: list[str] = []
    coords = np.asarray(_COORDS, dtype=float)
    for index, energy in enumerate(energies):
        rows = "\n".join(
            f"{symbol:2s} {coord[0]:15.10f} {coord[1]:15.10f} {coord[2]:15.10f}"
            for symbol, coord in zip(_SYMBOLS, coords + 0.01 * index)
        )
        blocks.append(f"{len(_SYMBOLS)}\nIRC {direction} point {index} E {energy:.12f}\n{rows}\n")
    path.write_text("".join(blocks), encoding="utf-8")


def _qc_result(**fields: Any) -> Any:
    from cccp.backends.base import QCResult

    return QCResult(**fields)


def _run(
    backend: _RecordingBackend,
    *,
    options: IrcOptions | None = None,
    target_dir: Path,
) -> Any:
    context = TaskContext(backend=backend, workdir=target_dir)
    return run_irc(_request(options=options, output_dir=target_dir), context=context)


# ── happy path: bidirectional inside one task call ───────────────────────


def test_bidirectional_irc_is_one_call_with_per_direction_payload(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    _write_endpoint(target_dir / "ts_IRC_F.xyz", 0.1)
    _write_endpoint(target_dir / "ts_IRC_B.xyz", -0.1)
    _write_trj(target_dir / "ts_IRC_F_trj.xyz", "forward", (-77.20, -77.10))
    _write_trj(target_dir / "ts_IRC_B_trj.xyz", "reverse", (-77.30, -77.05))
    backend = _RecordingBackend(
        [
            _qc_result(
                success=True,
                energy=-77.1,
                symbols=list(_SYMBOLS),
                metadata={
                    "direction_status": {"forward": "completed", "reverse": "completed"},
                    "endpoints": {
                        "forward": str(target_dir / "ts_IRC_F.xyz"),
                        "reverse": str(target_dir / "ts_IRC_B.xyz"),
                    },
                },
            )
        ]
    )

    result = _run(backend, target_dir=target_dir)

    assert result.status == "completed"
    assert result.complete is True
    assert not result.errors
    assert len(backend.calls) == 1
    assert backend.calls[0]["kwargs"]["direction"] == "both"

    payload = result.payload
    assert isinstance(payload, IrcPayload)
    assert [entry.direction for entry in payload.directions] == [
        IrcDirection.FORWARD,
        IrcDirection.REVERSE,
    ]
    forward, reverse = payload.directions
    assert forward.success is True and forward.converged is True
    assert reverse.success is True and reverse.converged is True
    assert forward.coordinates is not None and len(forward.coordinates) == 6
    assert forward.symbols == _SYMBOLS
    assert forward.steps == 2
    assert forward.energy_hartree == pytest.approx(-77.10)
    assert reverse.steps == 2
    assert reverse.energy_hartree == pytest.approx(-77.05)
    assert result.metadata["endpoint_count"] == 2
    assert result.metadata["directions"] == ["forward", "reverse"]
    assert result.metadata["trajectory_frame_count"] == 4


def test_one_way_completion_keeps_the_valid_direction(tmp_path: Path) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    _write_endpoint(target_dir / "end_f.xyz", 0.1)
    backend = _RecordingBackend(
        [
            _qc_result(
                success=True,
                energy=-77.0,
                symbols=list(_SYMBOLS),
                metadata={
                    "direction_status": {
                        "forward": "completed",
                        "reverse": "max_iterations",
                    },
                    "endpoints": {"forward": str(target_dir / "end_f.xyz")},
                },
            )
        ]
    )

    result = _run(backend, target_dir=target_dir)

    assert result.status == "completed"
    assert result.complete is False
    payload = result.payload
    forward, reverse = payload.directions
    assert forward.success is True and forward.converged is True
    assert forward.coordinates is not None
    assert reverse.success is False and reverse.converged is False
    assert result.metadata["endpoint_count"] == 1
    assert result.metadata["forward_endpoint"] == str(target_dir / "end_f.xyz")
    assert "reverse_endpoint" not in result.metadata


def test_requested_order_is_kept_for_reverse_first_requests(tmp_path: Path) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    _write_endpoint(target_dir / "end_r.xyz", -0.1)
    backend = _RecordingBackend(
        [
            _qc_result(
                success=True,
                metadata={
                    "direction_status": {"reverse": "completed"},
                    "endpoints": {"reverse": str(target_dir / "end_r.xyz")},
                },
            )
        ]
    )
    options = IrcOptions(directions=(IrcDirection.REVERSE, IrcDirection.FORWARD))

    result = _run(backend, options=options, target_dir=target_dir)

    assert backend.calls[0]["kwargs"]["direction"] == "both"
    assert [entry.direction for entry in result.payload.directions] == [
        IrcDirection.REVERSE,
        IrcDirection.FORWARD,
    ]
    assert result.complete is False


# ── failure semantics ────────────────────────────────────────────────────


def test_one_way_failure_returns_structured_failure(tmp_path: Path) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    backend = _RecordingBackend(
        [
            _qc_result(
                success=False,
                error_message="IRC forward direction failed to converge",
                metadata={"direction_status": {"forward": "failed"}},
            )
        ]
    )
    options = IrcOptions(directions=(IrcDirection.FORWARD,))

    result = _run(backend, options=options, target_dir=target_dir)

    assert result.status == "failed"
    assert result.complete is False
    assert result.errors == ("IRC forward direction failed to converge",)
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert len(result.payload.directions) == 1
    assert result.payload.directions[0].success is False
    assert result.metadata["endpoint_count"] == 0
    assert backend.calls[0]["kwargs"]["direction"] == "forward"


def test_backend_success_without_endpoints_is_failure(tmp_path: Path) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    backend = _RecordingBackend([_qc_result(success=True, symbols=list(_SYMBOLS))])

    result = _run(backend, target_dir=target_dir)

    assert result.status == "failed"
    assert any("no endpoint" in error for error in result.errors)
    assert result.metadata["endpoint_count"] == 0


def test_backend_exception_returns_structured_failure_with_empty_payload(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    backend = _RecordingBackend([RuntimeError("ORCA IRC crashed")])

    result = _run(backend, target_dir=target_dir)

    assert result.status == "failed"
    assert result.complete is False
    assert any("ORCA IRC crashed" in error for error in result.errors)
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.payload == IrcPayload()
    assert result.metadata["directions"] == ["forward", "reverse"]


def test_transition_state_role_is_required(tmp_path: Path) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    backend = _RecordingBackend()
    context = TaskContext(backend=backend, workdir=target_dir)

    with pytest.raises(TaskInputError, match="transition-state"):
        run_irc(_request(output_dir=target_dir, role=StructureRole.MINIMUM), context=context)
    assert backend.calls == []


def test_request_validation_rejects_wrong_task() -> None:
    with pytest.raises(TaskInputError, match="run_irc requires task 'irc'"):
        run_irc(_request(task=TaskKind.SCAN))


def test_request_validation_rejects_wrong_options() -> None:
    from cccp.calculation.requests import FrequencyOptions

    with pytest.raises(TaskInputError, match="expected IrcOptions"):
        run_irc(_request(options=FrequencyOptions()))


# ── typed options contract (no ts_mode) + kwargs translation ─────────────


def test_irc_options_carry_no_ts_mode() -> None:
    assert not hasattr(IrcOptions, "ts_mode")
    options = IrcOptions.from_dict({"ts_mode": 3, "maxpoints": 25})
    assert "ts_mode" not in options.to_dict()
    assert options.maxpoints == 25


def test_options_translate_to_backend_keywords(tmp_path: Path) -> None:
    target_dir = tmp_path / "irc_work"
    target_dir.mkdir()
    _write_endpoint(target_dir / "ts_IRC_F.xyz", 0.1)
    _write_endpoint(target_dir / "ts_IRC_B.xyz", -0.1)
    backend = _RecordingBackend(
        [
            _qc_result(
                success=True,
                metadata={
                    "direction_status": {"forward": "completed", "reverse": "completed"},
                    "endpoints": {
                        "forward": str(target_dir / "ts_IRC_F.xyz"),
                        "reverse": str(target_dir / "ts_IRC_B.xyz"),
                    },
                },
            )
        ]
    )
    options = IrcOptions(maxpoints=25, step=0.15, initial_hessian="calculate")

    result = _run(backend, options=options, target_dir=target_dir)

    kwargs = backend.calls[0]["kwargs"]
    assert kwargs["direction"] == "both"
    assert kwargs["max_iter"] == 25
    assert kwargs["step"] == pytest.approx(0.15)
    assert kwargs["initial_hessian"] == "calculate"
    assert result.status == "completed"


# ── direction / completion semantics (goldens vocabulary) ────────────────


def test_resolve_direction_mapping() -> None:
    assert resolve_direction(("forward", "reverse")) == "both"
    assert resolve_direction(("forward",)) == "forward"
    assert resolve_direction(("reverse",)) == "reverse"
    assert resolve_direction(()) == "both"


def test_completed_direction_semantics() -> None:
    raw = _qc_result(
        success=True,
        metadata={"direction_status": {"forward": "completed", "reverse": "max_iterations"}},
    )
    assert completed_directions(raw, {"forward": 1, "reverse": 2}, True) == {"forward"}
    raw_fail = _qc_result(success=False, metadata={"direction_status": {"forward": "completed"}})
    assert completed_directions(raw_fail, {"forward": 1}, False) == {"forward"}
    plain = _qc_result(success=True, metadata={})
    assert completed_directions(plain, {"forward": 1}, True) == {"forward"}
    assert completed_directions(plain, {"forward": 1}, False) == set()


def test_completed_directions_parse_log_when_no_metadata(tmp_path: Path) -> None:
    log_path = tmp_path / "irc.log"
    log_path.write_text(
        "FORWARD IRC\nIRC convergence reached\n"
        "BACKWARD IRC\nMAXIMUM NUMBER OF ITERATIONS REACHED\n",
        encoding="utf-8",
    )
    raw = _qc_result(success=True, log_file=log_path, metadata={})

    assert completed_directions(raw, {"forward": 1, "reverse": 2}, True) == {"forward"}


# ── payload / endpoint classification record identity ────────────────────


def test_payload_round_trips_with_request_order() -> None:
    from cccp.calculation.contracts import ArtifactRef
    from cccp.calculation.results import IrcDirectionResult

    payload = IrcPayload(
        directions=(
            IrcDirectionResult(
                direction=IrcDirection.REVERSE,
                energy_hartree=-2.0,
                coordinates=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
                symbols=("H", "H"),
                converged=True,
                steps=7,
                trajectory_ref=ArtifactRef(
                    path=Path("irc/irc_reverse_path.xyz"), type="trajectory"
                ),
            ),
            IrcDirectionResult(direction=IrcDirection.FORWARD, success=False),
        )
    )
    restored = IrcPayload.from_dict(payload.to_dict())
    assert restored == payload
    assert [entry.direction for entry in restored.directions] == [
        IrcDirection.REVERSE,
        IrcDirection.FORWARD,
    ]
    assert restored.directions[0].steps == 7
    assert restored.directions[1].success is False


def test_endpoint_classification_lives_in_cccp() -> None:
    from cccp.calculation.irc_endpoints import (
        classify_endpoint_geometry,
        perceive_connectivity,
    )

    edges = perceive_connectivity(
        ["O", "H", "H"], np.array([[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]])
    )
    assert (0, 1) in edges and (0, 2) in edges

    symbols = ["C", "C", "H", "H"]
    coords = np.array([[0.0, 0.0, 0.0], [1.34, 0.0, 0.0], [0.5, 0.9, 0.0], [1.5, 0.9, 0.0]])
    match = classify_endpoint_geometry(symbols, coords + 0.01, symbols, coords)
    assert match.verdict == "MATCH"
    assert match.connectivity_matches_reference is True
    stretched = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.5, 2.0, 0.0], [3.5, 2.0, 0.0]])
    different = classify_endpoint_geometry(symbols, stretched, symbols, coords)
    assert different.verdict == "DIFFERENT"


# ── boundary guards: no plan integration, no acp imports ─────────────────


def test_validate_plan_still_rejects_irc() -> None:
    code = (
        "import importlib\n"
        "contracts = importlib.import_module('acp.calculations.contracts')\n"
        "plan = contracts.CalculationPlan(workflow='batch', steps=[{'kind': 'irc'}])\n"
        "errors = contracts.validate_plan(plan)\n"
        "assert any('IRC' in error for error in errors), errors\n"
        "kinds = [kind.value for kind in contracts.StepKind]\n"
        "assert 'irc' not in kinds, kinds\n"
        "print('ok')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_cccp_irc_modules_never_import_the_acp_package() -> None:
    root = Path(__file__).resolve().parent.parent / "src"
    prefix = ("acp",)
    for relative in _SOURCES:
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(prefix), f"{relative}:{node.lineno}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(prefix), f"{relative}:{node.lineno}"
