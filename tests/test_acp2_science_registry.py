"""Generic behaviour tests for the ACP2 read-only capability registry.

The suite is deliberately catalog-free: no built-in capability is seeded
here.  A-05b owns the four built-in descriptors.  Everything below is
constructed from local test descriptors and an injected executable probe.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cccp.calculation.capabilities import (
    CapabilityDescriptor,
    CapabilityProjections,
    CapabilityRegistry,
    CapabilityState,
    DuplicateCapabilityError,
    DuplicateImplementationError,
    ExecutionSemantics,
    IncompatibleStateError,
    Precondition,
    Projection,
    SchemaReference,
    UnknownCapabilityError,
    UnknownImplementationError,
)

_INPUT_SCHEMA = SchemaReference(
    "https://acp.local/acp2/science/task-binding.schema.json"
)
_OUTPUT_SCHEMA = SchemaReference("cccp.calculation.results#TaskResult")


def _compute_descriptor(
    capability_id: str,
    implementation_id: str,
    *,
    software: tuple[str, ...] = ("orca",),
    verified: bool = False,
) -> CapabilityDescriptor:
    preconditions = tuple(
        Precondition(
            id=name,
            kind="software",
            description=f"{name} executable",
            required=True,
            error_code="unavailable_binary",
        )
        for name in software
    )
    projections = (
        CapabilityProjections(
            verified=Projection(
                CapabilityState.VERIFIED, evidence_ref="evidence:test/reference"
            )
        )
        if verified
        else CapabilityProjections()
    )
    return CapabilityDescriptor(
        capability_id=capability_id,
        implementation_id=implementation_id,
        input_schema=_INPUT_SCHEMA,
        output_schema=_OUTPUT_SCHEMA,
        execution_semantics=ExecutionSemantics(
            kind="compute",
            starts_subprocess=True,
            reads_existing_artifacts_only=False,
            delegates_to_task_kind="singlepoint",
            delegation_count=1,
            side_effects="artifacts_only",
            network_access=False,
            writes_artifacts=True,
        ),
        preconditions=preconditions,
        projections=projections,
    )


def _analysis_descriptor(
    capability_id: str,
    implementation_id: str,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        implementation_id=implementation_id,
        input_schema=_INPUT_SCHEMA,
        output_schema=_OUTPUT_SCHEMA,
        execution_semantics=ExecutionSemantics(
            kind="pure_analysis",
            starts_subprocess=False,
            reads_existing_artifacts_only=True,
            delegates_to_task_kind=None,
        ),
    )


def _resolver(*available: str):
    present = frozenset(available)

    def resolve(name: str) -> Path | None:
        return Path("/opt/fake") / name if name in present else None

    return resolve


def _three_descriptors() -> tuple[CapabilityDescriptor, ...]:
    return (
        _compute_descriptor("test.zeta.energy", "test.impl.zeta"),
        _compute_descriptor("test.alpha.energy", "test.impl.alpha"),
        _compute_descriptor("test.middle.energy", "test.impl.middle"),
    )


def test_catalog_order_is_deterministic() -> None:
    registry = CapabilityRegistry(_three_descriptors(), resolver=_resolver("orca"))

    assert registry.capability_ids == (
        "test.alpha.energy",
        "test.middle.energy",
        "test.zeta.energy",
    )
    assert tuple(item.capability_id for item in registry.catalog().capabilities) == (
        registry.capability_ids
    )
    assert tuple(item.capability_id for item in registry.descriptors) == (
        registry.capability_ids
    )


def test_catalog_serialization_is_byte_identical_across_registries() -> None:
    forward = CapabilityRegistry(_three_descriptors(), resolver=_resolver("orca"))
    reverse = CapabilityRegistry(
        tuple(reversed(_three_descriptors())), resolver=_resolver("orca")
    )

    first = forward.catalog_json()
    assert first == forward.catalog_json()
    assert first == reverse.catalog_json()
    assert [entry["capability_id"] for entry in json.loads(first)["capabilities"]] == list(
        forward.capability_ids
    )


def test_unknown_capability_returns_typed_unsupported_result() -> None:
    registry = CapabilityRegistry(_three_descriptors(), resolver=_resolver("orca"))

    result = registry.query("test.missing.capability")

    assert result.supported is False
    assert result.found is False
    assert result.record is None
    assert result.error is not None
    assert result.error.code == "unknown_implementation"
    assert result.error.layer == "scientific"
    assert result.error.retryable is False
    assert result.error.detail["capability_id"] == "test.missing.capability"
    assert registry.query("test.missing.capability").to_dict()["supported"] is False

    with pytest.raises(UnknownCapabilityError):
        registry.require("test.missing.capability")


def test_unknown_implementation_returns_typed_unsupported_result() -> None:
    registry = CapabilityRegistry(_three_descriptors(), resolver=_resolver("orca"))

    result = registry.query_implementation("test.impl.missing")

    assert result.supported is False
    assert result.record is None
    assert result.error is not None
    assert result.error.code == "unknown_implementation"
    assert result.error.detail["implementation_id"] == "test.impl.missing"

    with pytest.raises(UnknownImplementationError):
        registry.require_implementation("test.impl.missing")


def test_duplicate_capability_id_raises_typed_conflict() -> None:
    duplicate = _compute_descriptor("test.alpha.energy", "test.impl.other")

    with pytest.raises(DuplicateCapabilityError) as excinfo:
        CapabilityRegistry((_three_descriptors()[1], duplicate), resolver=_resolver("orca"))

    assert isinstance(excinfo.value, ValueError)


def test_duplicate_implementation_id_raises_typed_conflict() -> None:
    duplicate = _compute_descriptor("test.other.energy", "test.impl.alpha")

    with pytest.raises(DuplicateImplementationError) as excinfo:
        CapabilityRegistry((_three_descriptors()[1], duplicate), resolver=_resolver("orca"))

    assert isinstance(excinfo.value, ValueError)


def test_incompatible_execution_state_raises_typed_error() -> None:
    with pytest.raises(IncompatibleStateError):
        ExecutionSemantics(
            kind="compute",
            starts_subprocess=False,
            reads_existing_artifacts_only=False,
            delegates_to_task_kind="singlepoint",
        )

    with pytest.raises(IncompatibleStateError):
        ExecutionSemantics(
            kind="pure_analysis",
            starts_subprocess=True,
            reads_existing_artifacts_only=True,
            delegates_to_task_kind=None,
        )


def test_incompatible_projection_state_raises_typed_error() -> None:
    with pytest.raises(IncompatibleStateError):
        CapabilityProjections(described=Projection(CapabilityState.ABSENT))

    with pytest.raises(IncompatibleStateError):
        CapabilityProjections(installed=Projection(CapabilityState.VERIFIED))


def test_verified_projection_requires_evidence() -> None:
    with pytest.raises(IncompatibleStateError):
        CapabilityProjections(verified=Projection(CapabilityState.VERIFIED))


def test_projections_are_independent() -> None:
    unverified = _compute_descriptor("test.alpha.energy", "test.impl.alpha")
    registry = CapabilityRegistry((unverified,), resolver=_resolver("orca"))
    projections = registry.require("test.alpha.energy").projections

    assert projections.described.state is CapabilityState.PRESENT
    assert projections.installed.state is CapabilityState.PRESENT
    assert projections.available.state is CapabilityState.PRESENT
    assert projections.verified.state is CapabilityState.NOT_VERIFIED

    verified = _compute_descriptor(
        "test.beta.energy", "test.impl.beta", verified=True
    )
    registry = CapabilityRegistry((verified,), resolver=_resolver())
    projections = registry.require("test.beta.energy").projections

    assert projections.installed.state is CapabilityState.ABSENT
    assert projections.verified.state is CapabilityState.VERIFIED
    assert projections.verified.evidence_ref == "evidence:test/reference"


def test_injected_resolver_drives_installed_projection() -> None:
    descriptor = _compute_descriptor(
        "test.alpha.energy", "test.impl.alpha", software=("orca", "xtb")
    )

    absent = CapabilityRegistry((descriptor,), resolver=_resolver("orca"))
    projections = absent.require("test.alpha.energy").projections
    assert projections.installed.state is CapabilityState.ABSENT
    assert projections.installed.reason is not None
    assert "xtb" in projections.installed.reason
    assert projections.available.state is CapabilityState.ABSENT

    present = CapabilityRegistry((descriptor,), resolver=_resolver("orca", "xtb"))
    projections = present.require("test.alpha.energy").projections
    assert projections.installed.state is CapabilityState.PRESENT
    assert projections.available.state is CapabilityState.PRESENT


def test_pure_analysis_requires_no_executable() -> None:
    def forbidden(name: str) -> Path | None:
        raise AssertionError(f"pure analysis must not probe an executable: {name}")

    registry = CapabilityRegistry(
        (_analysis_descriptor("test.vibration.analyze", "test.impl.analysis"),),
        resolver=forbidden,
    )
    projections = registry.require("test.vibration.analyze").projections

    assert projections.installed.state is CapabilityState.NOT_VERIFIED
    assert projections.available.state is CapabilityState.PRESENT
    assert projections.verified.state is CapabilityState.NOT_VERIFIED


def test_register_returns_a_new_immutable_registry() -> None:
    first = _three_descriptors()[0]
    second = _three_descriptors()[1]
    base = CapabilityRegistry((first,), resolver=_resolver("orca"))
    extended = base.register(second)

    assert base.capability_ids == ("test.zeta.energy",)
    assert extended.capability_ids == ("test.alpha.energy", "test.zeta.energy")

    with pytest.raises(DuplicateCapabilityError):
        extended.register(first)


def test_empty_registry_seeds_no_builtin_capabilities() -> None:
    registry = CapabilityRegistry((), resolver=_resolver())

    assert registry.capability_ids == ()
    assert registry.catalog().capabilities == ()


def test_discovery_imports_no_task_module() -> None:
    script = """
import sys

from cccp.calculation.capabilities import CapabilityRegistry

registry = CapabilityRegistry((), resolver=lambda name: None)
assert registry.catalog_json()
task_modules = sorted(
    name
    for name in sys.modules
    if name == "cccp.calculation.tasks" or name.startswith("cccp.calculation.tasks.")
)
assert task_modules == [], task_modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
