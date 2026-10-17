"""Pure-cccp xtb_constrained_opt task tests — never import acp.

Independent call surface: ``run_xtb_constrained_opt(request, context=…)``
consumes the CCCP typed envelope (single structure + typed constraint
options) against a fake xTB backend; success / empty semantics follow
``P2_TASK_CONTRACTS`` (convergence evidence + effective/physical energies).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.requests import (
    StructureInput,
    TaskKind,
    TaskRequest,
    XtbConstrainedOptConstraint,
    XtbConstrainedOptOptions,
)
from cccp.calculation.results import ErrorKind, XtbConstrainedOptPayload
from cccp.calculation.tasks.xtb_constrained_opt import (
    parse_opt_log,
    run_xtb_constrained_opt,
    xcontrol_fix_block,
)

_START = ((0.0, 0.0, 0.0), (1.3, 0.0, 0.0), (2.5, 0.0, 0.0))
_SYMBOLS = ("H", "C", "O")

CONVERGED_LOG = """\
   * total energy :  -33.3464041 Eh    change  -0.13E+01
   * total energy :  -33.8098037 Eh    change  -0.57E-03
   *** GEOMETRY OPTIMIZATION CONVERGED AFTER 27 ITERATIONS ***
           | TOTAL ENERGY              -33.811067821211 Eh   |
normal termination of xtb
"""

NOT_CONVERGED_LOG = """\
   * total energy :  -33.3464041 Eh    change  -0.13E+01
   *** FAILED TO CONVERGE GEOMETRY OPTIMIZATION IN 3 ITERATIONS ***
           | TOTAL ENERGY              -33.888973158288 Eh   |
normal termination of xtb
"""

SP_LOG = """\
           | TOTAL ENERGY              -33.900000000000 Eh   |
normal termination of xtb
"""


def _options(**overrides: Any) -> XtbConstrainedOptOptions:
    fields: dict[str, Any] = {
        "constraints": (
            XtbConstrainedOptConstraint(kind="distance", atoms=(1, 2), target=1.1, unit="angstrom"),
        ),
        "force_constant": 50.0,
        "gfn_level": 2,
        "opt_level": "normal",
        "max_steps": 500,
        "physical_sp": True,
    }
    fields.update(overrides)
    return XtbConstrainedOptOptions(**fields)


def _request(tmp_path: Path, **overrides: Any) -> TaskRequest:
    options: XtbConstrainedOptOptions = overrides.pop("options", _options())
    payload: dict[str, Any] = {
        "task": TaskKind.XTB_CONSTRAINED_OPT,
        "structure": StructureInput(coordinates=_START, symbols=_SYMBOLS),
        "options": options,
        "output_dir": tmp_path / "run",
    }
    payload.update(overrides)
    return TaskRequest(**payload)


class _QCResult:
    def __init__(self, **fields: Any) -> None:
        self.success = fields.get("success", True)
        self.coordinates = fields.get("coordinates")
        self.energy = fields.get("energy")
        self.error_message = fields.get("error_message")


class _FakeXtbBackend:
    """xTB-shaped fake: ``constrained_optimize`` + ``single_point``."""

    name = "xtb"

    def __init__(self, opt_factory: Any, sp_factory: Any | None = None) -> None:
        self._opt_factory = opt_factory
        self._sp_factory = sp_factory
        self.opt_calls: list[dict[str, Any]] = []
        self.sp_calls: list[dict[str, Any]] = []

    def constrained_optimize(
        self,
        coordinates: Any,
        symbols: Any,
        output_dir: Any,
        constraints: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.opt_calls.append(
            {
                "output_dir": Path(output_dir),
                "constraints": list(constraints or ()),
                **kwargs,
            }
        )
        return self._opt_factory(Path(output_dir))

    def single_point(
        self, coordinates: Any, symbols: Any, output_dir: Any, **kwargs: Any
    ) -> Any:
        self.sp_calls.append({"output_dir": Path(output_dir), **kwargs})
        if self._sp_factory is None:
            return _QCResult(success=True, energy=-33.9)
        return self._sp_factory(Path(output_dir))


def _converged_factory(target: Path) -> _QCResult:
    # ``target`` is the opt directory itself (the interface writes in place).
    target.mkdir(parents=True, exist_ok=True)
    (target / "xtb.log").write_text(CONVERGED_LOG, encoding="utf-8")
    (target / ".xcontrol").write_text("$constrain\n$end\n", encoding="utf-8")
    (target / "xtbopt.xyz").write_text(
        "3\noptimized\nH 0 0 0\nC 1.1 0 0\nO 2.2 0 0\n", encoding="utf-8"
    )
    return _QCResult(success=True, coordinates=((0.0, 0.0, 0.0), (1.1, 0.0, 0.0), (2.2, 0.0, 0.0)))


def _sp_factory(target: Path) -> _QCResult:
    target.mkdir(parents=True, exist_ok=True)
    (target / "xtb_sp.log").write_text(SP_LOG, encoding="utf-8")
    return _QCResult(success=True, energy=-33.9)


# ── independent cccp call: success state ───────────────────────────────


def test_success_maps_energies_and_runs_physical_sp(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(_converged_factory, _sp_factory)
    result = run_xtb_constrained_opt(
        _request(tmp_path),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )
    assert result.status == "completed"
    assert result.converged is True
    assert isinstance(result.payload, XtbConstrainedOptPayload)
    assert result.payload.converged_geometry is True
    assert result.payload.energy_effective_hartree == pytest.approx(-33.811067821211)
    assert result.payload.energy_physical_hartree == pytest.approx(-33.9)
    assert result.metadata["opt"]["convergence_marker"].startswith("GEOMETRY_OPTIMIZATION")
    assert result.metadata["physical_sp"]["success"] is True
    assert len(backend.opt_calls) == 1
    assert len(backend.sp_calls) == 1
    call = backend.opt_calls[0]
    assert call["opt_level"] == "normal"
    assert call["max_steps"] == 500
    assert call["constraints"][0].force_constant == pytest.approx(50.0)


def test_fix_atoms_compile_into_extra_control_text(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(_converged_factory)
    options = _options(fix_atoms=(0, 1, 2, 5))
    run_xtb_constrained_opt(
        _request(tmp_path, options=options),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )
    call = backend.opt_calls[0]
    assert call["extra_control_text"] == "$fix\n  atoms: 1-3, 6\n$end\n"


def test_non_converged_geometry_still_completes(tmp_path: Path) -> None:
    def factory(target: Path) -> _QCResult:
        target.mkdir(parents=True, exist_ok=True)
        (target / "xtb.log").write_text(NOT_CONVERGED_LOG, encoding="utf-8")
        (target / ".xcontrol").write_text("$constrain\n$end\n", encoding="utf-8")
        return _QCResult(success=False, coordinates=None)

    backend = _FakeXtbBackend(factory, _sp_factory)
    result = run_xtb_constrained_opt(
        _request(tmp_path),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )
    assert result.status == "completed"
    assert result.converged is False
    assert result.payload.converged_geometry is False
    assert result.payload.energy_physical_hartree is None
    assert result.metadata["physical_sp"]["skipped_reason"] == "no_converged_geometry_available"
    assert backend.sp_calls == []


def test_physical_sp_not_requested_skips_single_point(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(_converged_factory, _sp_factory)
    result = run_xtb_constrained_opt(
        _request(tmp_path, options=_options(physical_sp=False)),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )
    assert result.status == "completed"
    assert result.payload.energy_physical_hartree is None
    assert result.metadata["physical_sp"] is None
    assert backend.sp_calls == []


# ── failure states ──────────────────────────────────────────────────────


def test_backend_failure_is_failed(tmp_path: Path) -> None:
    def factory(target: Path) -> _QCResult:
        raise RuntimeError("xtb binary missing")

    backend = _FakeXtbBackend(factory)
    result = run_xtb_constrained_opt(
        _request(tmp_path),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )
    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is not None
    assert "xtb binary missing" in result.errors[0]


def test_missing_log_is_backend_failure(tmp_path: Path) -> None:
    def factory(target: Path) -> _QCResult:
        return _QCResult(success=False, coordinates=None, error_message="no log")

    backend = _FakeXtbBackend(factory)
    result = run_xtb_constrained_opt(
        _request(tmp_path),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )
    assert result.status == "failed"
    assert result.error_kind == ErrorKind.BACKEND_FAILURE
    assert "no log" in result.errors[0]


# ── typed-contract guards ───────────────────────────────────────────────


def test_wrong_task_kind_is_rejected(tmp_path: Path) -> None:
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(coordinates=_START, symbols=_SYMBOLS),
    )
    with pytest.raises(ValueError, match="xtb_constrained_opt"):
        backend = _FakeXtbBackend(_converged_factory)
        run_xtb_constrained_opt(request, context=TaskContext(backend=backend))


def test_empty_constraints_rejected_at_options_construction() -> None:
    with pytest.raises(Exception, match="at least one constraint"):
        _options(constraints=())


# ── xcontrol $fix compiler ──────────────────────────────────────────────


def test_fix_block_shapes() -> None:
    assert xcontrol_fix_block(None) == ""
    assert xcontrol_fix_block(()) == ""
    assert xcontrol_fix_block((3,)) == "$fix\n  atoms: 4\n$end\n"
    assert xcontrol_fix_block((0, 1, 2, 5)) == "$fix\n  atoms: 1-3, 6\n$end\n"
    assert xcontrol_fix_block((5, 0, 2, 1)) == "$fix\n  atoms: 1-3, 6\n$end\n"


def test_parse_opt_log_scc_failure() -> None:
    evidence = parse_opt_log(
        "WARNING: SCC is not converged after 500 iterations!\n"
        "abnormal termination of xtb\n"
    )
    assert evidence["scc_failure_detected"] is True
    assert evidence["converged_geometry"] is False
    assert evidence["normal_termination"] is False
