"""Behaviour tests for the ACP2 capability/task binding (A-06a).

The binding is the seam between the read-only capability registry and the
existing fifteen task cores.  These tests prove the four invariants that the
contract cannot express in prose alone:

* each seeded compute capability reaches exactly one ``run_*`` implementation;
* ``vibration.analyze`` never calls a task core;
* preflight is side-effect free (no files, no subprocess, no task call);
* an unknown capability/task kind raises a typed error instead of degrading
  to a different task.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import cccp.calculation as calculation_package
from cccp.calculation.capabilities import BUILTIN_CAPABILITY_IDS
from cccp.calculation.capabilities.contracts import (
    TASK_KINDS,
    CapabilityDescriptor,
    ExecutionSemantics,
    SchemaReference,
    UnknownCapabilityError,
)
from cccp.calculation.capabilities.registry import CapabilityRegistry
from cccp.calculation.capabilities.task_binding import (
    BindingSelectionMode,
    PreflightOutcome,
    ScientificPreconditionError,
    TaskKindReference,
    UnknownTaskKindError,
    UnsupportedBindingError,
    bind_capability,
    bind_task_kind,
    build_task_request,
)
from cccp.calculation.requests import (
    OptimizeOptions,
    TaskKind,
    TaskRequest,
    XtbConstrainedOptOptions,
)
from cccp.calculation.results import XtbConstrainedOptPayload

_RUNNERS: dict[str, tuple[str, str]] = {
    "singlepoint": ("cccp.calculation.tasks.singlepoint", "run_singlepoint"),
    "optimize": ("cccp.calculation.tasks.optimize", "run_optimize"),
    "frequency": ("cccp.calculation.tasks.frequency", "run_frequency"),
}

_COMPUTE_CAPABILITIES = (
    ("electronic.energy", "singlepoint"),
    ("geometry.optimize", "optimize"),
    ("vibration.compute", "frequency"),
)


def _request_payload(task: str) -> dict[str, object]:
    """Return a minimal valid water request mapping for *task*."""
    return {
        "schema_version": 1,
        "task": task,
        "charge": 0,
        "multiplicity": 1,
        "level": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
        "structure": {
            "coordinates": [[0.0, 0.0, 0.0], [0.0, 0.0, 1.09], [1.03, 0.0, -0.28]],
            "symbols": ["O", "H", "H"],
            "role": "minimum",
            "source": "test",
        },
        "resources": {"nproc": 1},
    }


def _install_task_spies(monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, str]]) -> None:
    """Patch all three runners; every invocation is recorded once."""

    def _make_spy(kind: str):
        def spy(request: TaskRequest, *, context: object | None = None) -> dict[str, str]:
            calls.append((kind, request.task.value))
            return {"task": kind}

        return spy

    for kind, (module_name, function_name) in _RUNNERS.items():
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, function_name, _make_spy(kind))


def test_builtin_catalog_is_unchanged() -> None:
    assert BUILTIN_CAPABILITY_IDS == (
        "electronic.energy",
        "geometry.optimize",
        "vibration.analyze",
        "vibration.compute",
    )


@pytest.mark.parametrize(("capability_id", "task_kind"), _COMPUTE_CAPABILITIES)
def test_compute_capability_reaches_exactly_one_task(
    capability_id: str,
    task_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _install_task_spies(monkeypatch, calls)

    binding = bind_capability(capability_id, _request_payload(task_kind))
    assert binding.task_kind == task_kind
    assert binding.preflight().ready is True

    result = binding.execute()

    assert result == {"task": task_kind}
    assert calls == [(task_kind, task_kind)]
    assert binding.to_dict()["delegation"]["max_invocations_per_binding"] == 1


def test_vibration_analyze_never_calls_a_run_star(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _install_task_spies(monkeypatch, calls)
    analysis = SimpleNamespace(
        frequencies=[1595.0, 3657.0, 3756.0],
        imaginary_modes=[],
        ir_intensities=[70.0, 5.0, 30.0],
        mode_vectors={},
    )

    binding = bind_capability("vibration.analyze", analysis_input=analysis)

    assert binding.task_kind is None
    assert binding.is_analysis is True
    assert binding.delegation_target == "cccp.analysis.frequency_products.build_frequency_report"
    assert binding.preflight().ready is True

    report = binding.execute()

    assert report["frequencies"] == [1595.0, 3657.0, 3756.0]
    assert calls == []


def test_vibration_analyze_accepts_the_analysis_payload_positionally() -> None:
    analysis = SimpleNamespace(
        frequencies=[100.0],
        imaginary_modes=[],
        ir_intensities=None,
        mode_vectors={},
    )

    binding = bind_capability("vibration.analyze", analysis)  # type: ignore[arg-type]

    assert binding.analysis_input is analysis
    assert binding.preflight().ready is True


def test_vibration_analyze_without_products_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _install_task_spies(monkeypatch, calls)

    binding = bind_capability("vibration.analyze")

    assert binding.preflight().outcome is PreflightOutcome.NOT_READY
    with pytest.raises(ScientificPreconditionError):
        binding.execute()
    assert calls == []


def test_preflight_is_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[str, str]] = []
    _install_task_spies(monkeypatch, calls)

    def _no_subprocess(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight must not start a subprocess")

    monkeypatch.setattr(subprocess, "Popen", _no_subprocess)
    monkeypatch.setattr(subprocess, "run", _no_subprocess)

    binding = bind_capability("electronic.energy", _request_payload("singlepoint"))
    before = sorted(path.name for path in tmp_path.rglob("*"))

    result = binding.preflight()

    assert result.ready is True
    assert sorted(path.name for path in tmp_path.rglob("*")) == before == []
    assert calls == []


def test_unknown_capability_raises_typed_error() -> None:
    with pytest.raises(UnknownCapabilityError):
        bind_capability("nmr.shielding", _request_payload("nmr_shielding"))

    with pytest.raises(UnknownCapabilityError):
        bind_capability("not.a.capability")


def test_registered_capability_without_entrypoint_raises_typed_error() -> None:
    descriptor = CapabilityDescriptor(
        capability_id="test.scan.capability",
        implementation_id="test.scan.implementation",
        input_schema=SchemaReference("cccp.calculation.requests#TaskRequest"),
        output_schema=SchemaReference("cccp.calculation.results#TaskResult"),
        execution_semantics=ExecutionSemantics(
            kind="compute",
            starts_subprocess=True,
            reads_existing_artifacts_only=False,
            delegates_to_task_kind="scan",
            delegation_count=1,
            side_effects="artifacts_only",
            writes_artifacts=True,
        ),
    )
    registry = CapabilityRegistry([descriptor], resolver=lambda name: None)

    with pytest.raises(UnsupportedBindingError):
        bind_capability("test.scan.capability", registry=registry)


@pytest.mark.parametrize("task_kind", TASK_KINDS)
def test_direct_task_kind_reference_covers_the_fifteen_kinds(task_kind: str) -> None:
    binding = bind_task_kind(task_kind)

    assert binding.selection.mode is BindingSelectionMode.EXISTING_TASK_KIND
    assert binding.task_kind == task_kind
    document = binding.to_dict()
    assert document["selection"]["task_kind"] == task_kind
    assert document["binding_output"]["binding_metadata"]["resolved_task_kind"] == task_kind


def test_direct_unknown_task_kind_is_rejected() -> None:
    with pytest.raises(UnknownTaskKindError):
        bind_task_kind("not_a_task_kind")

    with pytest.raises(UnknownTaskKindError):
        TaskKindReference("not_a_task_kind")


def test_direct_unimplemented_task_kind_raises_on_execution() -> None:
    binding = bind_task_kind("scan")

    assert binding.preflight().outcome is PreflightOutcome.UNSUPPORTED_CAPABILITY
    with pytest.raises(UnsupportedBindingError):
        binding.execute()


def test_direct_task_kind_converts_inputs_to_task_request() -> None:
    binding = bind_task_kind("singlepoint", _request_payload("singlepoint"))

    assert isinstance(binding.request, TaskRequest)
    assert binding.request.task is TaskKind.SINGLEPOINT

    request = build_task_request(TaskKind.SINGLEPOINT, _request_payload("singlepoint"))
    assert request is not None
    assert request.schema_version == 1


def test_request_schema_and_payload_types_are_preserved() -> None:
    payload = _request_payload("optimize")
    payload["options"] = {"mode": "unconstrained"}

    binding = bind_capability("geometry.optimize", payload)

    assert binding.request is not None
    assert binding.request.schema_version == 1
    assert isinstance(binding.request.options, OptimizeOptions)
    document = binding.to_dict()
    assert document["binding_output"]["request"]["schema_version"] == 1
    assert document["binding_output"]["binding_metadata"]["delegation_target"] == (
        "cccp.calculation.run_optimize"
    )
    assert document["compatibility"]["existing_contract_versions"] == {
        "task_request": 1,
        "task_result": 1,
    }


def test_inconsistent_charge_multiplicity_fails_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    _install_task_spies(monkeypatch, calls)
    payload = _request_payload("singlepoint")
    payload["multiplicity"] = 2

    binding = bind_capability("electronic.energy", payload)
    result = binding.preflight()

    assert result.outcome is PreflightOutcome.NOT_READY
    assert any(check.id == "charge_multiplicity_consistent" for check in result.failures())
    with pytest.raises(ScientificPreconditionError) as excinfo:
        binding.execute()
    assert excinfo.value.code == "invalid_scientific_input"
    assert calls == []


def test_optimization_mode_role_mismatch_fails_preflight() -> None:
    payload = _request_payload("optimize")
    payload["options"] = {"mode": "transition_state"}

    binding = bind_capability("geometry.optimize", payload)

    assert binding.preflight().outcome is PreflightOutcome.NOT_READY
    assert any(
        check.id == "optimization_mode_applicable" for check in binding.preflight().failures()
    )


def test_request_for_another_task_is_rejected() -> None:
    with pytest.raises(ScientificPreconditionError):
        bind_capability("electronic.energy", _request_payload("frequency"))


def test_package_root_exposes_all_fifteen_task_cores() -> None:
    package_exports = calculation_package.__all__

    for task_kind in TASK_KINDS:
        export_name = f"run_{task_kind}"
        assert export_name in package_exports, (
            f"{export_name} missing from cccp.calculation.__all__"
        )
        runner = getattr(calculation_package, export_name)
        assert callable(runner), f"{export_name} is not callable"
        module = importlib.import_module(f"cccp.calculation.tasks.{task_kind}")
        assert runner is getattr(module, export_name)


def test_xtb_constrained_opt_package_exports_are_the_defined_objects() -> None:
    assert callable(calculation_package.run_xtb_constrained_opt)
    assert isinstance(calculation_package.XtbConstrainedOptOptions, type)
    assert isinstance(calculation_package.XtbConstrainedOptPayload, type)

    assert calculation_package.XtbConstrainedOptOptions is XtbConstrainedOptOptions
    assert calculation_package.XtbConstrainedOptPayload is XtbConstrainedOptPayload

    tasks_module = importlib.import_module("cccp.calculation.tasks.xtb_constrained_opt")
    assert calculation_package.run_xtb_constrained_opt is tasks_module.run_xtb_constrained_opt


def test_lazy_export_table_covers_every_task_kind() -> None:
    from cccp.calculation import _LAZY_EXPORTS

    for task_kind in TASK_KINDS:
        assert _LAZY_EXPORTS[f"run_{task_kind}"] == f"tasks.{task_kind}"

    assert _LAZY_EXPORTS["run_xtb_constrained_opt"] == "tasks.xtb_constrained_opt"
    assert _LAZY_EXPORTS["XtbConstrainedOptOptions"] == "requests"
    assert _LAZY_EXPORTS["XtbConstrainedOptPayload"] == "results"

    targets = {_LAZY_EXPORTS[f"run_{task_kind}"] for task_kind in TASK_KINDS}
    assert len(targets) == len(TASK_KINDS)
