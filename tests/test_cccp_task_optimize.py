"""cccp optimize task core — plan todo 18 (cleaned typed options contract).

Pure-cccp tests (never import ``acp``): request validation (``ts.mode_index``
requires ``enabled``), ``level`` single-source theory priority, rescue
``failure_type`` explicit/derived states, ``mode``/``StructureRole``
consistency, normal-vs-TS capability dispatch
(``optimize``/``transition_state_opt``), internal rescue chain diagnostics,
and scientific-only progress events.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation._common import (
    level_explicit_fields,
    render_backend_input,
    resolve_spec,
    theory_run_config,
)
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import OptimizationMode, StructureRole
from cccp.calculation.errors import TaskInputError
from cccp.calculation.progress import ProgressEvent, ProgressEventKind
from cccp.calculation.requests import (
    MethodSpec,
    OptimizeOptions,
    RescueSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
    TsSpec,
)
from cccp.calculation.results import OptimizePayload
from cccp.calculation.tasks.optimize import derive_failure_type, run_optimize


class _RecordingBackend:
    """Optimize-capability fake recording every capability call."""

    name = "orca"

    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def _record(self, method: str, symbols: list[str], output_dir: Path | None, kwargs: Any) -> Any:
        self.calls.append({"method": method, "output_dir": output_dir, "kwargs": dict(kwargs)})
        if self._responses:
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            if isinstance(response, list):
                callback = kwargs.get("output_callback")
                if callable(callback):
                    for line in response:
                        callback(line)
                return _ok(symbols)
            return response
        return _ok(symbols)

    def optimize(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        return self._record("optimize", symbols, output_dir, kwargs)

    def transition_state_opt(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        return self._record("transition_state_opt", symbols, output_dir, kwargs)


def _ok(symbols: list[str]) -> Any:
    from cccp.qc.interfaces.base import QCResult

    return QCResult(
        success=True,
        energy=-40.0,
        coordinates=np.asarray([[0.0, 0.0, 0.0]] * len(symbols), dtype=float),
        symbols=list(symbols),
        converged=True,
    )


def _request(
    *,
    role: StructureRole = StructureRole.MINIMUM,
    level: MethodSpec | None = None,
    options: OptimizeOptions | None = None,
    output_dir: Path | None = None,
) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.OPTIMIZE,
        structure=StructureInput(
            coordinates=((0.0, 0.0, 0.0),),
            symbols=("C",),
            role=role,
        ),
        level=level if level is not None else MethodSpec(method="r2SCAN-3c", basis=""),
        options=options,
        output_dir=output_dir,
    )


# ── happy path: independent cccp call ───────────────────────────────────


def test_independent_cccp_call_returns_typed_payload() -> None:
    backend = _RecordingBackend()
    result = run_optimize(_request(), context=TaskContext(backend=backend))

    assert result.status == "completed"
    assert result.complete is True
    assert result.energy_hartree == -40.0
    assert result.coordinates == ((0.0, 0.0, 0.0),)
    assert isinstance(result.payload, OptimizePayload)
    assert result.payload.optimization_status == "converged"
    assert result.payload.rescue_failure_type is None
    assert result.payload.rescue_attempts is None
    assert result.provenance is not None and result.provenance.backend == "orca"
    assert backend.calls[0]["method"] == "optimize"


# ── TsSpec: mode_index only legal with enabled ──────────────────────────


def test_ts_mode_index_without_enabled_rejected() -> None:
    with pytest.raises(TaskInputError, match="ts.mode_index requires ts.enabled"):
        TsSpec(enabled=False, mode_index=2)
    with pytest.raises(TaskInputError, match="ts.mode_index requires ts.enabled"):
        OptimizeOptions.from_dict({"ts": {"mode_index": 3}})
    TsSpec(enabled=True, mode_index=2)
    TsSpec(enabled=True)


# ── level: single theory carrier + unique resolution priority ───────────


def test_options_carry_no_duplicate_theory_or_platform_identity() -> None:
    names = {field.name for field in fields(OptimizeOptions)}
    assert "trajectory_item_id" not in names
    assert "candidate_id" not in names
    assert "profile" not in names
    assert "structure_kind" not in names
    assert "ts_mode" not in names
    for theory_field in (
        "solvent",
        "solvent_model",
        "grid",
        "integration_grid",
        "scf",
        "basis",
        "dispersion",
        "ri_approximation",
        "auxiliary_basis_j",
        "auxiliary_basis_c",
        "method",
    ):
        assert theory_field not in names, theory_field
    assert {"mode", "level", "constraints", "ts", "rescue", "geom_maxiter"} <= names


def test_level_is_single_theory_source_options_level_wins() -> None:
    backend = _RecordingBackend()
    request = _request(
        level=MethodSpec(method="r2SCAN-3c", solvent="toluene"),
        options=OptimizeOptions(level=MethodSpec(method="r2SCAN-3c", solvent="water")),
    )
    run_optimize(request, context=TaskContext(backend=backend))
    assert backend.calls[0]["kwargs"]["solvent"] == "water"


def test_level_falls_back_to_request_level() -> None:
    backend = _RecordingBackend()
    request = _request(level=MethodSpec(method="r2SCAN-3c", solvent="toluene"))
    run_optimize(request, context=TaskContext(backend=backend))
    assert backend.calls[0]["kwargs"]["solvent"] == "toluene"


def test_level_absent_resolves_by_unique_priority() -> None:
    level = MethodSpec(method="wB97X-D4", dispersion="D4")
    config = {"theory": {"dft": {"dispersion": "D3BJ", "solvent": "water"}}}
    explicit = level_explicit_fields(level)
    spec = resolve_spec(
        level.method or None,
        explicit=explicit,
        run_config=theory_run_config(config),
    )
    assert spec.get("dispersion") is not None
    assert spec.get("dispersion").source == "explicit"
    assert spec.get("dispersion").requested == "D4"
    assert spec.get("solvent") is not None
    assert spec.get("solvent").source == "run_config"
    rendered = render_backend_input(spec, method=level.method or None)
    assert rendered.get("dispersion") == "D4"
    assert "solvent" not in rendered


# ── rescue.failure_type: explicit override vs task-derived ──────────────


def test_rescue_failure_type_explicit_and_derived() -> None:
    derived_backend = _RecordingBackend([RuntimeError("optimization failed [scf_failure]")])
    derived = run_optimize(
        _request(options=OptimizeOptions(rescue=RescueSpec(policy="off"))),
        context=TaskContext(backend=derived_backend),
    )
    assert derived.status == "failed"
    assert isinstance(derived.payload, OptimizePayload)
    assert derived.payload.rescue_failure_type == "scf_failure"

    override_backend = _RecordingBackend([RuntimeError("optimization failed [scf_failure]")])
    overridden = run_optimize(
        _request(
            options=OptimizeOptions(rescue=RescueSpec(policy="off", failure_type="memory_failure"))
        ),
        context=TaskContext(backend=override_backend),
    )
    assert isinstance(overridden.payload, OptimizePayload)
    assert overridden.payload.rescue_failure_type == "memory_failure"

    assert derive_failure_type("generic error") == "geometry_not_converged"
    assert derive_failure_type("generic error", override="crash_timeout") == "crash_timeout"
    assert derive_failure_type("[scf_failure]", override="not-a-token") == "scf_failure"


# ── mode / StructureRole consistency ────────────────────────────────────


def test_mode_and_structure_role_must_agree() -> None:
    with pytest.raises(TaskInputError, match="disagree"):
        run_optimize(
            _request(
                role=StructureRole.MINIMUM,
                options=OptimizeOptions(mode=OptimizationMode.TRANSITION_STATE),
            ),
            context=TaskContext(backend=_RecordingBackend()),
        )
    with pytest.raises(TaskInputError, match="disagree"):
        run_optimize(
            _request(role=StructureRole.TRANSITION_STATE),
            context=TaskContext(backend=_RecordingBackend()),
        )
    with pytest.raises(TaskInputError, match="disagree"):
        run_optimize(
            _request(options=OptimizeOptions(ts=TsSpec(enabled=True))),
            context=TaskContext(backend=_RecordingBackend()),
        )


def test_consistent_ts_request_accepted() -> None:
    backend = _RecordingBackend()
    result = run_optimize(
        _request(
            role=StructureRole.TRANSITION_STATE,
            options=OptimizeOptions(
                mode=OptimizationMode.TRANSITION_STATE, ts=TsSpec(enabled=True)
            ),
        ),
        context=TaskContext(backend=backend),
    )
    assert result.status == "completed"
    assert backend.calls[0]["method"] == "transition_state_opt"
    assert backend.calls[0]["kwargs"]["ts_mode"] is True


# ── normal vs TS capability dispatch (mode/TsSpec) ──────────────────────


def test_capability_dispatch_normal_vs_ts() -> None:
    normal = _RecordingBackend()
    run_optimize(_request(), context=TaskContext(backend=normal))
    assert [call["method"] for call in normal.calls] == ["optimize"]

    by_mode = _RecordingBackend()
    run_optimize(
        _request(
            role=StructureRole.TRANSITION_STATE,
            options=OptimizeOptions(mode=OptimizationMode.TRANSITION_STATE),
        ),
        context=TaskContext(backend=by_mode),
    )
    assert [call["method"] for call in by_mode.calls] == ["transition_state_opt"]

    by_ts_spec = _RecordingBackend()
    run_optimize(
        _request(
            role=StructureRole.TRANSITION_STATE,
            options=OptimizeOptions(ts=TsSpec(enabled=True, mode_index=1)),
        ),
        context=TaskContext(backend=by_ts_spec),
    )
    assert [call["method"] for call in by_ts_spec.calls] == ["transition_state_opt"]
    assert by_ts_spec.calls[0]["kwargs"]["ts_mode"] == 1


# ── internal rescue chain + derived diagnostics ─────────────────────────


def test_rescue_chain_is_internal_and_diagnostics_typed() -> None:
    backend = _RecordingBackend(
        [
            RuntimeError("optimization failed [scf_failure]"),
            RuntimeError("optimization failed [scf_failure]"),
            _ok(["C"]),
        ]
    )
    result = run_optimize(
        _request(
            options=OptimizeOptions(rescue=RescueSpec(policy="adaptive", max_rescue=2)),
        ),
        context=TaskContext(backend=backend),
    )
    assert result.status == "completed"
    assert len(backend.calls) == 3
    assert backend.calls[1]["kwargs"]["scf_maxiter"] == 500
    assert backend.calls[2]["kwargs"]["scf_strategy"] == "slowconv"
    payload = result.payload
    assert isinstance(payload, OptimizePayload)
    assert payload.rescue_failure_type == "scf_failure"
    assert payload.rescue_structure_kind == "minimum"
    assert payload.rescue_attempts == 2
    assert payload.rescue_terminal is False
    assert payload.rescue_actions[:2] == ("scf_increase_maxiter", "scf_slowconv")
    assert result.errors[0].startswith("optimize: ")
    assert "scf_increase_maxiter" in result.errors[1]
    assert all("trajectory_item_id" not in call["kwargs"] for call in backend.calls)


# ── progress events are scientific (no UI fields) ───────────────────────


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)


def test_progress_events_are_scientific_not_ui(tmp_path: Path) -> None:
    backend = _RecordingBackend(
        [
            [
                "GEOMETRY OPTIMIZATION CYCLE 1",
                "FINAL SINGLE POINT ENERGY     -10.000000",
                "GEOMETRY OPTIMIZATION CONVERGED",
            ]
        ]
    )
    sink = _CollectingSink()
    result = run_optimize(
        _request(output_dir=tmp_path), context=TaskContext(backend=backend, progress=sink)
    )
    assert result.status == "completed"
    assert sink.events, "the optimize task must publish progress events"
    metric_names = {field.name for field in fields(ProgressEvent)}
    assert metric_names == {"kind", "stage", "metric", "value", "unit", "message", "index"}
    for event in sink.events:
        assert event.kind in ProgressEventKind
        assert event.metric in (None, "cycle")
        assert not hasattr(event, "label_key")
        assert not hasattr(event, "priority")
    cycles = [event for event in sink.events if event.metric == "cycle"]
    assert cycles and cycles[0].value == 1.0
