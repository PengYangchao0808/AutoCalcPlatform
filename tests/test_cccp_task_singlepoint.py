"""cccp singlepoint task core — plan todo 17 (first minimal complete path).

Pure-cccp tests (never import ``acp``): request validation, two-step
selection, translation entry (``resolve_spec`` + ``render_backend_input``),
typed ``SinglePointPayload``, the ``stability_check`` option, and failure
classification.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from cccp.calculation._common import render_backend_input, resolve_spec
from cccp.calculation.context import TaskContext
from cccp.calculation.contracts import ElectronicStateSpec, electronic_state_spec_from_dict
from cccp.calculation.errors import (
    BackendUnavailableError,
    TaskInputError,
    UnsupportedCapabilityError,
)
from cccp.calculation.requests import (
    MethodSpec,
    SinglePointOptions,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
)
from cccp.calculation.results import ErrorKind, SinglePointPayload
from cccp.calculation.selection import precheck_runtime, select_semantic
from cccp.calculation.tasks.singlepoint import run_singlepoint


class _RecordingBackend:
    """Minimal single-point capability fake recording call kwargs."""

    name = "orca"

    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    def single_point(
        self,
        coordinates: Any,
        symbols: list[str],
        charge: int = 0,
        multiplicity: int = 1,
        output_dir: Path | None = None,
        **kwargs: Any,
    ) -> Any:
        from cccp.qc.interfaces.base import QCResult

        self.calls.append(
            {
                "coordinates": np.asarray(coordinates, dtype=float),
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
        return QCResult(success=True, energy=-41.5, symbols=list(symbols), converged=True)


def _request(
    *,
    backend: str | None = None,
    level: MethodSpec | None = None,
    options: SinglePointOptions | None = None,
    electronic_state: ElectronicStateSpec | None = None,
    n_atoms: int = 1,
) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(
            coordinates=tuple((float(i), 0.0, 0.0) for i in range(n_atoms)),
            symbols=tuple("C" for _ in range(n_atoms)),
        ),
        charge=0,
        multiplicity=1,
        level=level or MethodSpec(method="wB97X-D4", basis="def2-SVP"),
        backend=backend,
        electronic_state=electronic_state,
        options=options,
    )


def _flipspin_state() -> ElectronicStateSpec:
    return electronic_state_spec_from_dict(
        {
            "state_id": "t1",
            "target_multiplicity": 3,
            "spin_mode": "unrestricted",
            "spatial_symmetry": "disable",
            "guess": {
                "strategy": "flipspin",
                "reference_multiplicity": 5,
                "final_ms": 1.0,
                "flip_atoms": [1],
                "atom_index_base": 1,
            },
        }
    )


# ── happy path: independent cccp call ───────────────────────────────────


def test_independent_cccp_call_returns_typed_payload() -> None:
    backend = _RecordingBackend()
    result = run_singlepoint(_request(), context=TaskContext(backend=backend))

    assert result.task is TaskKind.SINGLEPOINT
    assert result.status == "completed"
    assert result.energy_hartree == -41.5
    assert result.error_kind is None
    assert isinstance(result.payload, SinglePointPayload)
    assert result.provenance is not None and result.provenance.backend == "orca"
    call = backend.calls[0]
    assert call["multiplicity"] == 1
    assert call["kwargs"]["method"] == "wB97X-D4"


# ── request validation ──────────────────────────────────────────────────


def test_request_validation_rejects_wrong_task() -> None:
    request = TaskRequest(
        task=TaskKind.FREQUENCY,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
    )
    with pytest.raises(TaskInputError):
        run_singlepoint(request, context=TaskContext(backend=_RecordingBackend()))


def test_request_validation_rejects_auto_backend_and_missing_structure() -> None:
    with pytest.raises(TaskInputError, match="auto"):
        run_singlepoint(
            _request(backend="auto"), context=TaskContext(backend=_RecordingBackend())
        )
    bare = TaskRequest(task=TaskKind.SINGLEPOINT)
    with pytest.raises(TaskInputError, match="structure"):
        run_singlepoint(bare, context=TaskContext(backend=_RecordingBackend()))


def test_request_validation_rejects_mismatched_options_type() -> None:
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
        options=SinglePointOptions(stability_check=True),
    )
    object.__setattr__(request, "options", object())
    with pytest.raises(TaskInputError):
        run_singlepoint(request, context=TaskContext(backend=_RecordingBackend()))


# ── two-step selection ──────────────────────────────────────────────────


def test_explicit_backend_is_honored_in_selection() -> None:
    request = _request(backend="xtb")
    selection = select_semantic(request)
    assert selection.backend == "xtb"
    assert selection.explicit_backend is True
    assert selection.capability == "single_point"


def test_runtime_precheck_uses_context_programs_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable", lambda name, configured_path=None: None
    )
    selection = select_semantic(_request())
    assert selection.backend == "orca"
    with pytest.raises(BackendUnavailableError):
        precheck_runtime(selection)
    with pytest.raises(BackendUnavailableError):
        run_singlepoint(_request())


def test_backend_override_skips_runtime_precheck(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(name: str, configured_path: object = None) -> str:
        raise AssertionError("precheck must be skipped when a backend instance is supplied")

    monkeypatch.setattr("cccp.calculation.selection.resolve_executable", _boom)
    backend = _RecordingBackend()
    result = run_singlepoint(_request(), context=TaskContext(backend=backend))
    assert result.status == "completed"
    assert backend.calls


# ── translation: resolve_spec + render_backend_input ────────────────────


def test_translation_passes_explicit_level_values_verbatim() -> None:
    backend = _RecordingBackend()
    level = MethodSpec(method="r2SCAN-3c", basis="def2-TZVP")
    context = TaskContext(
        backend=backend,
        capability_extras={"scf_maxiter": 200, "output_name": "sp_0001"},
    )
    result = run_singlepoint(_request(level=level), context=context)

    assert result.status == "completed"
    kwargs = backend.calls[0]["kwargs"]
    assert kwargs["method"] == "r2SCAN-3c"
    assert kwargs["basis"] == "def2-TZVP", "explicit basis must never be clamped at execution"
    assert kwargs["scf_maxiter"] == 200
    assert kwargs["output_name"] == "sp_0001"


def test_render_backend_input_passes_only_explicit_values() -> None:
    spec = resolve_spec("wB97X-D4", explicit={"basis": "def2-SVP"})
    rendered = render_backend_input(spec, method="wB97X-D4")
    assert rendered == {"method": "wB97X-D4", "basis": "def2-SVP"}
    assert spec["aux_j_basis"].effective == "def2/J", (
        "resolution fills method defaults for identity/provenance, but the "
        "renderer is the single place that materialises them"
    )


def test_render_backend_input_leaves_method_owned_layers_unrendered() -> None:
    spec = resolve_spec("DLPNO-CCSD(T)", explicit={"basis": "def2-TZVPP"})
    rendered = render_backend_input(spec, method="DLPNO-CCSD(T)")
    assert rendered == {"method": "DLPNO-CCSD(T)", "basis": "def2-TZVPP"}


def test_render_backend_input_merges_state_scf_options() -> None:
    spec = resolve_spec("wB97X-D4", explicit={"basis": "def2-SVP"})
    rendered = render_backend_input(
        spec,
        method="wB97X-D4",
        state_scf_options={"hf_typ": "UHF"},
        extras={"scf_options": {"maxiter": 200}},
    )
    assert rendered["scf_options"] == {"maxiter": 200, "hf_typ": "UHF"}


# ── typed payload + stability_check option ──────────────────────────────


def test_typed_payload_carries_electronic_state_metadata() -> None:
    backend = _RecordingBackend()
    result = run_singlepoint(
        _request(electronic_state=_flipspin_state(), n_atoms=2),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    assert isinstance(result.payload, SinglePointPayload)
    state = result.payload.electronic_state
    assert state is not None
    assert state["state_id"] == "t1"
    assert state["state_status"] in {"accepted", "warning"}
    call = backend.calls[0]
    assert call["multiplicity"] == 5, "flipspin inputs use the high-spin reference"
    assert call["kwargs"]["scf_options"]["hf_typ"] == "UHF"
    assert call["kwargs"]["scf_options"]["flip_spin_atoms"] == [0]


def test_stability_check_option_sets_stab_flags() -> None:
    backend = _RecordingBackend()
    result = run_singlepoint(
        _request(options=SinglePointOptions(stability_check=True)),
        context=TaskContext(backend=backend),
    )

    assert result.status == "completed"
    scf_options = backend.calls[0]["kwargs"]["scf_options"]
    assert scf_options["stab_perform"] is True
    assert scf_options["stab_restart"] is True


def test_state_validation_rejects_parity_mismatch() -> None:
    state = electronic_state_spec_from_dict(
        {"state_id": "d1", "target_multiplicity": 2, "spin_mode": "unrestricted"}
    )
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
        electronic_state=state,
    )
    with pytest.raises(TaskInputError, match="electronic_state"):
        run_singlepoint(request, context=TaskContext(backend=_RecordingBackend()))


# ── failure classification ──────────────────────────────────────────────


def test_qc_failure_is_classified() -> None:
    from cccp.qc.interfaces.base import QCResult

    backend = _RecordingBackend(
        responses=[QCResult(success=False, error_message="SCF did not converge")]
    )
    result = run_singlepoint(_request(), context=TaskContext(backend=backend))
    assert result.status == "failed"
    assert result.error_kind is ErrorKind.NOT_CONVERGED
    assert result.errors == ("SCF did not converge",)


def test_backend_exception_is_classified() -> None:
    backend = _RecordingBackend(responses=[RuntimeError("orca crashed")])
    result = run_singlepoint(_request(), context=TaskContext(backend=backend))
    assert result.status == "failed"
    assert result.error_kind is ErrorKind.BACKEND_FAILURE
    assert result.errors == ("orca crashed",)


def test_missing_energy_is_classified_as_parse_failure() -> None:
    from cccp.qc.interfaces.base import QCResult

    backend = _RecordingBackend(responses=[QCResult(success=True, energy=None)])
    result = run_singlepoint(_request(), context=TaskContext(backend=backend))
    assert result.status == "failed"
    assert result.error_kind is ErrorKind.PARSE_FAILURE


def test_missing_program_raises_before_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable", lambda name, configured_path=None: None
    )
    with pytest.raises(BackendUnavailableError):
        run_singlepoint(_request())


def test_unsupported_capability_never_escapes_as_attribute_error() -> None:
    class _NoCapability:
        name = "orca"

    with pytest.raises(UnsupportedCapabilityError):
        run_singlepoint(_request(), context=TaskContext(backend=_NoCapability()))


# ── resources envelope ──────────────────────────────────────────────────


def test_resource_quota_violation_rejected() -> None:
    request = TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("C",)),
        resources=TaskResources(nproc=4, maxcore=2000, mem="2GB"),
    )
    with pytest.raises(TaskInputError, match="cannot cover"):
        run_singlepoint(request, context=TaskContext(backend=_RecordingBackend()))
