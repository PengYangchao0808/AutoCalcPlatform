"""T45 — plugin SDK contract suite (seven boundary classes).

Each boundary class is exercised with a real observation, not a simulated
success:

1. version mismatch — typed failure + rollback guidance, no silent downgrade;
2. missing dependency — real ``importlib`` absence, checked before the loader;
3. duplicate capability — discovery rejects collisions with built-ins/plugins;
4. partial output — a real truncated XYZ keeps the valid prefix;
5. units / atom mapping — explicit canonical units + input atom order;
6. cancel / timeout — cooperative checks raise typed control outcomes;
7. fallback / rollback — failures never substitute a built-in result silently.

The example plugin (:mod:`cccp.plugin.example_xyz`) is a real plain-XYZ parser
and is loaded through :class:`~cccp.plugin.host.PluginHost`.
"""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.errors import TaskCancelledError
from cccp.calculation.requests import (
    StructureInput,
    TaskKind,
    TaskRequest,
)
from cccp.calculation.results import ErrorKind, TaskResult
from cccp.plugin import (
    EXAMPLE_CANDIDATE,
    EXAMPLE_CAPABILITY_NAME,
    EXAMPLE_OUTPUT_SPEC,
    EXAMPLE_PLUGIN_NAME,
    PILOT_CANDIDATE,
    ApiCompatibility,
    ErrorSemantics,
    PluginCandidate,
    PluginCapability,
    PluginCapabilityConflictError,
    PluginContractViolationError,
    PluginDependencyUnavailableError,
    PluginFailureReport,
    PluginHost,
    PluginIdentityScope,
    PluginLoadError,
    PluginMetadata,
    PluginOutputSpec,
    PluginRollbackAction,
    PluginRollbackGuidance,
    PluginTimeoutError,
    PluginUnavailableError,
    PluginVersionMismatchError,
    ResourceRequirements,
    check_cancelled,
    check_deadline,
    discover_plugins,
    failure_report,
    invoke_capability,
    lock_plugin,
    validate_result_contract,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
COMPLETE_XYZ = FIXTURES / "xyz_readonly_example.xyz"
TRUNCATED_XYZ = FIXTURES / "xyz_readonly_truncated.xyz"


def _metadata(name: str, **overrides: object) -> PluginMetadata:
    values: dict[str, object] = {
        "name": name,
        "distribution": "example-dist",
        "version": "1.0.0",
        "api": ApiCompatibility(),
        "capabilities": (PluginCapability(name="readonly_structure", schema="structure_product"),),
        "science_software": (),
        "resources": ResourceRequirements(),
        "license": "MIT",
        "dependencies": (),
        "error_semantics": ErrorSemantics(),
        "default_enabled": False,
    }
    values.update(overrides)
    return PluginMetadata(**values)  # type: ignore[arg-type]


def _request(path: Path) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.SINGLEPOINT,
        structure=StructureInput(path=path),
    )


def _example_host() -> PluginHost:
    host = PluginHost(candidates=(EXAMPLE_CANDIDATE,))
    host.enable(EXAMPLE_PLUGIN_NAME)
    return host


# ── version / digest locking ────────────────────────────────────────────


def test_digest_is_deterministic_and_version_sensitive() -> None:
    metadata = EXAMPLE_CANDIDATE.metadata
    first = lock_plugin(metadata)
    second = lock_plugin(metadata)

    assert first.digest == second.digest
    assert first.to_dict() == second.to_dict()
    assert len(first.digest) == 64
    assert first.identity_fields(scope="release")["plugin_digest"] == first.digest

    version_changed = replace(metadata, version="9.9.9")
    assert lock_plugin(version_changed).digest != first.digest

    schema_changed = replace(
        metadata,
        capabilities=(
            PluginCapability(
                name=EXAMPLE_CAPABILITY_NAME,
                schema="xyz_structure",
                schema_version=2,
            ),
        ),
    )
    assert lock_plugin(schema_changed).digest != first.digest

    range_changed = replace(metadata, api=ApiCompatibility(compatible_range=">=1,<3"))
    assert lock_plugin(range_changed).digest != first.digest


def test_identity_fields_support_release_step_and_product_scopes() -> None:
    lock = lock_plugin(EXAMPLE_CANDIDATE.metadata)

    release = lock.identity_fields(scope=PluginIdentityScope.RELEASE)
    assert release["plugin_identity_scope"] == "release"
    assert release["plugin_version"] == EXAMPLE_CANDIDATE.metadata.version
    assert "plugin_capability" not in release

    step = lock.identity_fields(scope="step", capability=EXAMPLE_CAPABILITY_NAME)
    assert step["plugin_identity_scope"] == "step"
    assert step["plugin_capability"] == EXAMPLE_CAPABILITY_NAME

    product = lock.identity_fields(
        scope=PluginIdentityScope.PRODUCT, capability=EXAMPLE_CAPABILITY_NAME
    )
    assert product["plugin_capability_schema"] == "xyz_structure"
    assert product["plugin_capability_schema_version"] == 1
    assert product["plugin_digest_schema_version"] == 1

    with pytest.raises(PluginContractViolationError):
        lock.identity_fields(scope=PluginIdentityScope.PRODUCT)
    with pytest.raises(PluginContractViolationError):
        lock.identity_fields(scope="product", capability="not_declared")


def test_digest_locks_resolved_dependency_versions() -> None:
    # pytest is a real distribution in every test environment.
    metadata = _metadata("vendor_with_dependency", dependencies=("pytest",))
    lock = lock_plugin(metadata)

    resolved = {dependency.name: dependency for dependency in lock.resolved_dependencies}
    assert "pytest" in resolved
    assert resolved["pytest"].version != "unversioned"
    assert resolved["pytest"].source == "distribution"
    assert lock.to_dict()["resolved_dependencies"]


# ── boundary 1: version mismatch ────────────────────────────────────────


class TestVersionMismatchBoundary:
    def test_incompatible_api_is_typed_and_carries_rollback_guidance(self) -> None:
        metadata = _metadata(
            "vendor_future_plugin",
            api=ApiCompatibility(compatible_range=">=2,<3"),
        )

        with pytest.raises(PluginVersionMismatchError) as excinfo:
            lock_plugin(metadata)
        error = excinfo.value
        assert error.name == "vendor_future_plugin"
        assert error.declared == ">=2,<3"

        report = failure_report(error)
        assert isinstance(report, PluginFailureReport)
        assert report.guidance.action is PluginRollbackAction.REPIN_VERSION
        assert report.guidance.builtin_capability_preserved is True
        assert report.guidance.silent_substitution_allowed is False
        assert report.guidance.requires_operator is True
        assert report.guidance.retryable is False
        assert "re-pin" in report.guidance.reason
        json.dumps(report.to_dict())  # no exception object / non-JSON value

    def test_incompatible_plugin_cannot_be_discovered_or_invoked(self) -> None:
        metadata = _metadata(
            "vendor_future_plugin",
            api=ApiCompatibility(compatible_range=">=2,<3"),
        )
        with pytest.raises(PluginVersionMismatchError):
            PluginHost(candidates=(PluginCandidate(metadata=metadata),))


# ── boundary 2: missing dependency ──────────────────────────────────────


class TestMissingDependencyBoundary:
    def test_missing_dependency_is_typed_before_loader_runs(self) -> None:
        missing = "cccp_plugin_contract_missing_dependency_8f4d"
        assert importlib.util.find_spec(missing) is None  # genuinely absent
        calls: list[str] = []

        def loader() -> object:
            calls.append("loaded")
            return object()

        metadata = _metadata(
            "vendor_missing_dependency",
            capabilities=(PluginCapability(name="readonly_structure", schema="structure_product"),),
            dependencies=(missing,),
        )
        host = PluginHost(candidates=(PluginCandidate(metadata=metadata, loader=loader),))
        host.enable("vendor_missing_dependency")

        with pytest.raises(PluginDependencyUnavailableError) as excinfo:
            invoke_capability(
                host,
                "vendor_missing_dependency",
                capability="readonly_structure",
                request=_request(COMPLETE_XYZ),
            )

        assert excinfo.value.missing == (missing,)
        assert calls == []  # dependency check happens before load, no import/install
        report = failure_report(excinfo.value)
        assert report.guidance.action is PluginRollbackAction.FIX_DEPENDENCY
        assert report.guidance.silent_substitution_allowed is False

    def test_declared_dependency_absent_from_lock_map_is_typed(self) -> None:
        metadata = _metadata(
            "vendor_dependency_lock",
            dependencies=("pytest", "another_real_dependency"),
        )
        with pytest.raises(PluginDependencyUnavailableError) as excinfo:
            lock_plugin(metadata, resolved_dependencies={"pytest": "9.1.1"})
        assert excinfo.value.missing == ("another_real_dependency",)


# ── boundary 3: duplicate capability ────────────────────────────────────


class TestDuplicateCapabilityBoundary:
    def test_duplicate_capability_across_plugins_is_rejected(self) -> None:
        first = _metadata(
            "vendor_one",
            capabilities=(PluginCapability(name="shared_structure", schema="s1"),),
        )
        second = _metadata(
            "vendor_two",
            capabilities=(PluginCapability(name="shared_structure", schema="s1"),),
        )

        with pytest.raises(PluginCapabilityConflictError) as excinfo:
            discover_plugins((PluginCandidate(metadata=first), PluginCandidate(metadata=second)))
        assert excinfo.value.name == "vendor_two"
        assert excinfo.value.capability == "shared_structure"
        assert "vendor_one" in excinfo.value.reason

    def test_capability_reserved_for_builtin_task_is_rejected(self) -> None:
        metadata = _metadata(
            "vendor_builtin_capability",
            capabilities=(PluginCapability(name=TaskKind.FREQUENCY.value, schema="fake"),),
        )
        with pytest.raises(PluginCapabilityConflictError) as excinfo:
            discover_plugins((PluginCandidate(metadata=metadata),))
        assert excinfo.value.capability == TaskKind.FREQUENCY.value
        assert "built-in" in excinfo.value.reason

    def test_invoke_rejects_undeclared_capability(self) -> None:
        host = _example_host()
        with pytest.raises(PluginUnavailableError):
            invoke_capability(
                host,
                EXAMPLE_PLUGIN_NAME,
                capability="not_declared",
                request=_request(COMPLETE_XYZ),
            )

    def test_pilot_and_example_discover_with_distinct_capabilities(self) -> None:
        host = PluginHost(candidates=(PILOT_CANDIDATE, EXAMPLE_CANDIDATE))
        assert set(host.catalog.names()) == {
            PILOT_CANDIDATE.metadata.name,
            EXAMPLE_PLUGIN_NAME,
        }
        capabilities = {
            capability.name
            for metadata in host.catalog.plugins
            for capability in metadata.capabilities
        }
        assert len(capabilities) == 2


# ── example plugin happy path ───────────────────────────────────────────


def test_example_plugin_loads_through_host_and_parses_complete_xyz() -> None:
    host = _example_host()
    request = _request(COMPLETE_XYZ)
    result = invoke_capability(
        host,
        EXAMPLE_PLUGIN_NAME,
        capability=EXAMPLE_CAPABILITY_NAME,
        request=request,
    )

    assert isinstance(result, TaskResult)
    assert result.task is request.task
    assert result.status == "completed"
    assert result.complete is True
    assert result.symbols == ("O", "H", "H")
    assert result.coordinates is not None and len(result.coordinates) == 3
    assert result.coordinates[0] == (0.0, 0.0, 0.1173)
    assert result.metadata["capability"] == EXAMPLE_CAPABILITY_NAME
    assert result.metadata["units"] == {"coordinates": "angstrom"}
    assert result.metadata["atom_order"] == ["O", "H", "H"]
    assert result.provenance is not None
    assert len(result.provenance.input_signature) == 64


# ── boundary 4: partial output ──────────────────────────────────────────


class TestPartialOutputBoundary:
    def test_truncated_xyz_keeps_valid_prefix_and_marks_partial(self) -> None:
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=_request(TRUNCATED_XYZ),
        )

        assert result.complete is False
        assert result.status == "failed"
        assert result.error_kind is ErrorKind.PARSE_FAILURE
        assert result.symbols == ("O", "H")  # real valid prefix, never fabricated
        assert result.coordinates is not None and len(result.coordinates) == 2
        assert result.errors
        assert "truncated" in result.errors[0]

    def test_partial_result_without_errors_is_rejected(self) -> None:
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=_request(TRUNCATED_XYZ),
        )
        without_errors = replace(result, errors=())

        with pytest.raises(PluginContractViolationError) as excinfo:
            validate_result_contract(
                without_errors,
                metadata=EXAMPLE_CANDIDATE.metadata,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=_request(TRUNCATED_XYZ),
                output_spec=EXAMPLE_OUTPUT_SPEC,
            )
        assert "at least one error" in excinfo.value.detail

    def test_capability_that_disallows_partial_is_rejected(self) -> None:
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=_request(TRUNCATED_XYZ),
        )
        with pytest.raises(PluginContractViolationError) as excinfo:
            validate_result_contract(
                result,
                metadata=EXAMPLE_CANDIDATE.metadata,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=_request(TRUNCATED_XYZ),
                output_spec=PluginOutputSpec(
                    units=(("coordinates", "angstrom"),),
                    allows_partial=False,
                ),
            )
        assert "does not allow partial" in excinfo.value.detail


# ── boundary 5: units / atom mapping ────────────────────────────────────


class TestUnitsMappingBoundary:
    def test_declared_units_and_input_atom_mapping_are_accepted(self) -> None:
        request = _request(COMPLETE_XYZ)
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=request,
        )
        validate_result_contract(
            result,
            metadata=EXAMPLE_CANDIDATE.metadata,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=request,
            output_spec=EXAMPLE_OUTPUT_SPEC,
        )
        assert result.metadata["atom_order"] == list(result.symbols or ())

    def test_wrong_atom_order_is_rejected(self) -> None:
        request = _request(COMPLETE_XYZ)
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=request,
        )
        wrong_mapping = replace(
            result,
            metadata={**result.metadata, "atom_order": ["H", "H", "O"]},
        )
        with pytest.raises(PluginContractViolationError) as excinfo:
            validate_result_contract(
                wrong_mapping,
                metadata=EXAMPLE_CANDIDATE.metadata,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=request,
                output_spec=EXAMPLE_OUTPUT_SPEC,
            )
        assert "atom mapping" in excinfo.value.detail

    def test_non_canonical_unit_is_rejected(self) -> None:
        request = _request(COMPLETE_XYZ)
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=request,
        )
        wrong_units = replace(
            result,
            metadata={
                **result.metadata,
                "units": {"coordinates": "angstrom", "energy": "furlong"},
            },
        )
        with pytest.raises(PluginContractViolationError) as excinfo:
            validate_result_contract(
                wrong_units,
                metadata=EXAMPLE_CANDIDATE.metadata,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=request,
                output_spec=EXAMPLE_OUTPUT_SPEC,
            )
        assert "not canonical" in excinfo.value.detail

    def test_symbol_coordinate_count_mismatch_is_rejected(self) -> None:
        request = _request(COMPLETE_XYZ)
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=request,
        )
        mismatch = replace(result, symbols=("O", "H"))
        with pytest.raises(PluginContractViolationError) as excinfo:
            validate_result_contract(
                mismatch,
                metadata=EXAMPLE_CANDIDATE.metadata,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=request,
                output_spec=EXAMPLE_OUTPUT_SPEC,
            )
        assert "symbol count" in excinfo.value.detail

    def test_result_task_must_match_request_task(self) -> None:
        request = _request(COMPLETE_XYZ)
        result = invoke_capability(
            _example_host(),
            EXAMPLE_PLUGIN_NAME,
            capability=EXAMPLE_CAPABILITY_NAME,
            request=request,
        )
        other_task = replace(result, task=TaskKind.OPTIMIZE)
        with pytest.raises(PluginContractViolationError) as excinfo:
            validate_result_contract(
                other_task,
                metadata=EXAMPLE_CANDIDATE.metadata,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=request,
                output_spec=EXAMPLE_OUTPUT_SPEC,
            )
        assert "second task chain" in excinfo.value.detail


# ── boundary 6: cancel / timeout ────────────────────────────────────────


class TestCancellationTimeoutBoundary:
    def test_cooperative_cancellation_stops_mid_parse(self) -> None:
        calls: list[int] = []

        def cancelled() -> bool:
            calls.append(1)
            return len(calls) >= 4

        context = TaskContext(cancelled=cancelled)
        with pytest.raises(TaskCancelledError):
            invoke_capability(
                _example_host(),
                EXAMPLE_PLUGIN_NAME,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=_request(COMPLETE_XYZ),
                context=context,
            )
        # Two pre-checks (SDK + handler) then one atom line; the second atom
        # line observes the cancel, so parsing genuinely stopped mid-file.
        assert len(calls) == 4

        report = failure_report(TaskCancelledError("cancelled"), plugin_name=EXAMPLE_PLUGIN_NAME)
        assert report.guidance.action is PluginRollbackAction.RETRY
        assert report.guidance.retryable is True
        assert report.guidance.requires_operator is False

    def test_cooperative_timeout_is_typed(self) -> None:
        context = TaskContext(timeout_s=0.0)
        with pytest.raises(PluginTimeoutError) as excinfo:
            invoke_capability(
                _example_host(),
                EXAMPLE_PLUGIN_NAME,
                capability=EXAMPLE_CAPABILITY_NAME,
                request=_request(COMPLETE_XYZ),
                context=context,
            )
        assert excinfo.value.timeout_s == 0.0

        report = failure_report(excinfo.value)
        assert report.guidance.action is PluginRollbackAction.RETRY
        assert report.guidance.retryable is True

    def test_deadline_helper_uses_elapsed_time_and_non_positive_budget(self) -> None:
        context = TaskContext(timeout_s=5.0)
        ticks = iter([0.0, 1.0, 10.0])
        clock = lambda: next(ticks)  # noqa: E731 - simple local clock

        check_deadline("vendor_timer", 0.0, context, clock=clock)
        check_deadline("vendor_timer", 0.0, context, clock=clock)
        with pytest.raises(PluginTimeoutError):
            check_deadline("vendor_timer", 0.0, context, clock=clock)

        with pytest.raises(PluginTimeoutError):
            check_deadline("vendor_timer", 0.0, TaskContext(timeout_s=-1.0))
        check_cancelled(TaskContext())  # no callback / not cancelled


# ── boundary 7: fallback / rollback ─────────────────────────────────────


class TestFallbackRollbackBoundary:
    def test_loader_failure_is_typed_and_never_substitutes_a_builtin(self) -> None:
        def broken_loader() -> object:
            raise RuntimeError("broken dependency wiring")

        metadata = _metadata(
            "vendor_broken_loader",
            capabilities=(PluginCapability(name="readonly_structure", schema="structure_product"),),
        )
        host = PluginHost(candidates=(PluginCandidate(metadata=metadata, loader=broken_loader),))
        host.enable("vendor_broken_loader")

        with pytest.raises(PluginLoadError) as excinfo:
            invoke_capability(
                host,
                "vendor_broken_loader",
                capability="readonly_structure",
                request=_request(COMPLETE_XYZ),
            )
        assert isinstance(excinfo.value.cause, RuntimeError)

        report = failure_report(excinfo.value)
        assert report.guidance.action is PluginRollbackAction.DISABLE_PLUGIN
        assert report.guidance.builtin_capability_preserved is True
        assert report.guidance.silent_substitution_allowed is False
        assert report.guidance.requires_operator is True

        # The host still knows the plugin; it was not silently disabled, and no
        # fallback TaskResult was produced under the plugin identity.
        assert host.catalog.get("vendor_broken_loader") is not None
        assert host.enabled_names() == ("vendor_broken_loader",)

    def test_contract_violation_guidance_preserves_partial_evidence(self) -> None:
        error = PluginContractViolationError(EXAMPLE_PLUGIN_NAME, detail="atom_order mismatch")
        report = failure_report(error)
        assert report.guidance.action is PluginRollbackAction.DISABLE_PLUGIN
        assert report.guidance.preserve_valid_partial_output is True
        assert report.guidance.silent_substitution_allowed is False
        assert report.guidance.builtin_capability_preserved is True
        json.dumps(report.to_dict())

    def test_rollback_guidance_structurally_forbids_silent_substitution(self) -> None:
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.DISABLE_PLUGIN,
            plugin="vendor_example",
            reason="contract violation",
        )
        assert guidance.silent_substitution_allowed is False
        assert guidance.to_dict()["silent_substitution_allowed"] is False
        assert guidance.to_dict()["builtin_capability_preserved"] is True
