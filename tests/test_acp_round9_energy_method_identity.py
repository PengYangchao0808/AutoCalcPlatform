"""Round-9 M12: every energy product must identify the method that ran.

The executor's ``RESULT/energy/*.json`` product now resolves the level from the
frozen step/job spec (``method`` → ``functional`` → nested ``levels``) before
``plan.profile``, and records ``method_unknown_reason`` instead of the false
fact ``""``.  ``provenance_requirements.method_identity`` is ``required`` for
both ``electronic.energy`` and ``geometry.optimize``, so a blank method is a
contract violation, not a cosmetic one.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import acp.calculations.executor as executor_module
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationRequest,
    CalculationResult,
    CalculationStep,
    StepKind,
    StructureArtifact,
)
from acp.calculations.executor import (
    CalculationPlanExecutor,
    _extract_method_from_resources,
    _resolve_energy_method,
)
from acp.results.energy_product import (
    ENERGY_METHOD_UNKNOWN_REASON,
    build_energy_product_payload,
)
from acp.storage.manifest import ProductKind, ResultManifest
from acp.workflows.simple import _step_spec
from cccp.calculation.capabilities.builtin import BUILTIN_DESCRIPTORS

_ENERGY_SCHEMA_KEYS = set(
    build_energy_product_payload(
        step_id="step_0_singlepoint",
        step_kind="singlepoint",
        energy_hartree=-1.0,
        method="wB97M-V",
    )
)


def _input_xyz(tmp_path: Path) -> Path:
    path = tmp_path / "input.xyz"
    path.write_text("1\ninput\nC 0.0 0.0 0.0\n", encoding="utf-8")
    return path


def _plan(
    tmp_path: Path,
    kind: StepKind,
    *,
    spec: dict[str, object] | None = None,
    profile: str = "default",
) -> CalculationPlan:
    step = CalculationStep(kind=kind, spec=spec) if spec is not None else CalculationStep(kind=kind)
    return CalculationPlan(
        workflow=kind.value,
        profile=profile,
        items=[StructureArtifact(path=_input_xyz(tmp_path), elements=["C"], source="test")],
        steps=[step],
    )


def _execute(plan: CalculationPlan, task_root: Path, *, energy: float = -76.42) -> None:
    def dispatch(request: object) -> CalculationResult:
        return CalculationResult(status="completed", energy=energy)

    first_step = plan.steps[0]
    kind = first_step.kind if isinstance(first_step, CalculationStep) else StepKind.SINGLEPOINT
    with patch.dict(executor_module._PRIMITIVE_DISPATCH, {kind: dispatch}):
        result = CalculationPlanExecutor().execute(plan, task_root=task_root)
    assert result.is_completed


def _energy_product(task_root: Path) -> tuple[object, dict[str, object]]:
    manifest = ResultManifest.read(task_root / "RESULT")
    products = [
        product for product in manifest.products if product.kind is ProductKind.ENERGY_REPORT
    ]
    assert len(products) == 1
    product = products[0]
    payload = json.loads((task_root / "RESULT" / product.path).read_text(encoding="utf-8"))
    return product, payload


def _assert_method_identity(metadata: object, *, method: str | None) -> None:
    assert isinstance(metadata, dict)
    if method is None:
        assert metadata.get("method_unknown_reason") == ENERGY_METHOD_UNKNOWN_REASON
    else:
        assert metadata.get("method_source") in {
            "job_spec",
            "job_spec_levels",
            "plan_profile",
        }
        assert "method_unknown_reason" not in metadata


def test_singlepoint_energy_product_names_the_requested_method(tmp_path: Path) -> None:
    # Given: a singlepoint job whose spec names the wB97M-V single-point level.
    plan = _plan(
        tmp_path,
        StepKind.SINGLEPOINT,
        spec={"method": "wB97M-V", "basis": "def2-TZVP"},
    )

    # When: the step executes and publishes its energy.
    _execute(plan, tmp_path)

    # Then: the product carries the real method and basis, never a blank string.
    product, payload = _energy_product(tmp_path)
    assert payload["method"] == "wB97M-V"
    assert payload["method"] != ""
    assert payload["schema_version"] == "acp_energy_product_v1"
    assert product.metadata.get("method_source") == "job_spec"
    assert product.metadata.get("basis") == "def2-TZVP"
    _assert_method_identity(product.metadata, method="wB97M-V")


def test_optimize_energy_product_keeps_the_round8_default(tmp_path: Path) -> None:
    # Given: an optimize job on the round-8 corrected default (r2SCAN-3c).
    plan = _plan(tmp_path, StepKind.OPTIMIZE, spec={"method": "r2SCAN-3c"})

    # When: the optimization completes and publishes its energy.
    _execute(plan, tmp_path)

    # Then: the product reports r2SCAN-3c — the correction is not silently
    # reverted to wB97M-V by the reporting path.
    product, payload = _energy_product(tmp_path)
    assert payload["method"] == "r2SCAN-3c"
    assert product.metadata.get("method_source") == "job_spec"


def test_nested_levels_fall_back_to_the_job_level(tmp_path: Path) -> None:
    # Given: a job spec that carries the level only inside ``levels``.
    plan = _plan(
        tmp_path,
        StepKind.SINGLEPOINT,
        spec={"levels": {"single_point": {"functional": "wB97M-V", "basis": "def2-TZVP"}}},
    )

    # When: the step publishes its energy.
    _execute(plan, tmp_path)

    # Then: the nested level is used, with its provenance named.
    product, payload = _energy_product(tmp_path)
    assert payload["method"] == "wB97M-V"
    assert product.metadata.get("method_source") == "job_spec_levels"
    assert product.metadata.get("basis") == "def2-TZVP"


def test_plan_profile_is_only_a_last_resort(tmp_path: Path) -> None:
    # Given: a profile-based plan (no step-level method) such as BatchOptimize.
    plan = _plan(tmp_path, StepKind.OPTIMIZE, profile="r2SCAN-3c")

    # When: the step publishes its energy.
    _execute(plan, tmp_path)

    # Then: the profile is reported as the resolution source.
    product, payload = _energy_product(tmp_path)
    assert payload["method"] == "r2SCAN-3c"
    assert product.metadata.get("method_source") == "plan_profile"


def test_unresolved_method_is_null_with_a_reason_never_blank(tmp_path: Path) -> None:
    # Given: a plan whose spec and profile name no level ("default" is a
    # container, not a method).
    plan = _plan(tmp_path, StepKind.SINGLEPOINT, spec={}, profile="default")

    # When: the energy product is published.
    _execute(plan, tmp_path)

    # Then: the method is explicitly unknown — not the false fact "".
    product, payload = _energy_product(tmp_path)
    assert payload["method"] is None
    assert payload["method"] != ""
    assert product.metadata.get("method_unknown_reason") == ENERGY_METHOD_UNKNOWN_REASON
    assert "method_source" not in product.metadata


@pytest.mark.parametrize("kind", [StepKind.SINGLEPOINT, StepKind.OPTIMIZE])
def test_energy_product_schema_is_shared(kind: StepKind, tmp_path: Path) -> None:
    # Given: two different workflows that both publish an energy.
    spec = {"method": "wB97M-V"} if kind is StepKind.SINGLEPOINT else {"method": "r2SCAN-3c"}
    plan = _plan(tmp_path, kind, spec=spec)

    # When: both products are written.
    _execute(plan, tmp_path)

    # Then: one frozen key set is used, so consumers never branch per workflow.
    _, payload = _energy_product(tmp_path)
    assert set(payload) == _ENERGY_SCHEMA_KEYS
    assert payload["step_kind"] == kind.value


def test_method_identity_is_declared_required_for_both_capabilities() -> None:
    # Given: the capability contracts behind singlepoint and optimize.
    descriptors = {descriptor.capability_id: descriptor for descriptor in BUILTIN_DESCRIPTORS}

    # Then: method identity is a required provenance field for both — the
    # energy product cannot legitimately be blank.
    for capability_id in ("electronic.energy", "geometry.optimize"):
        descriptor = descriptors[capability_id]
        assert descriptor.provenance_requirements.method_identity == "required"


def test_extract_method_from_resources_prefers_the_job_level() -> None:
    # Given/When/Then: explicit method wins, then functional, then levels,
    # and only a fully unresolvable spec falls back to the caller's default.
    assert _extract_method_from_resources({"method": "PBE0"}, "fallback") == "PBE0"
    assert _extract_method_from_resources({"method": "  "}, "fallback") == "fallback"
    assert _extract_method_from_resources({"functional": "wB97M-V"}, "fallback") == "wB97M-V"
    assert (
        _extract_method_from_resources(
            {"levels": {"single_point": {"functional": "wB97M-V"}}}, "fallback"
        )
        == "wB97M-V"
    )
    assert _extract_method_from_resources({}, "fallback") == "fallback"


def test_resolver_reports_basis_and_source_for_the_documented_singlepoint_default(
    tmp_path: Path,
) -> None:
    # Given: the documented single-point default level as a job spec.
    plan = _plan(
        tmp_path,
        StepKind.SINGLEPOINT,
        spec={"method": "wB97M-V", "basis": "def2-TZVP"},
    )

    # When: the energy-product resolver runs for step 0.
    method, basis, source = _resolve_energy_method(plan, 0)

    # Then: the effective method and its provenance are named.
    assert (method, basis, source) == ("wB97M-V", "def2-TZVP", "job_spec")


def test_simple_step_spec_omits_a_blank_method() -> None:
    # Given: a step request with a real level and one with none (xTB).
    artifact = StructureArtifact(path=Path("input.xyz"), elements=["C"], source="test")
    with_method = CalculationRequest(
        input_artifact=artifact,
        method="wB97M-V",
        resources={"basis": "def2-TZVP"},
        workflow="singlepoint",
        profile="default",
    )
    without_method = CalculationRequest(
        input_artifact=artifact,
        method="",
        resources={"basis": ""},
        workflow="xtb_optimize",
        profile="default",
    )

    # When: their job-spec views are built.
    named = _step_spec(with_method)
    unnamed = _step_spec(without_method)

    # Then: a level is named exactly, and blank identity is omitted — never "".
    assert named["method"] == "wB97M-V"
    assert named["basis"] == "def2-TZVP"
    assert "method" not in unnamed
    assert "basis" not in unnamed
