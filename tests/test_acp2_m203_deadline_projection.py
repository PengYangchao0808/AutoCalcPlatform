"""M2-03c — approval deadline, budget mapping and the honest capability projection.

Exit condition (EXECUTION_PLAN M2-03c): the approval deadline reaches **all
three** termination surfaces through one resolver (local process group, CCCP
subprocess ``timeout_s``, LSF ``-W``), the admission-bound and process-termination
walltime claims are split, ``UsageUnitValidationError`` never escapes as a 500 or
as "empty limits", and the ``application_params`` / ``research_budget`` /
RPH ``PlanBudget`` vocabularies map through one function — proven over real HTTP
and MCP requests, not by feeding a parser a synthetic dict.

The *capability* rows are pinned as **not** upgraded: wiring a surface is not
evidence that it terminates anything (M4-03), and recipe/qc/model_request only
upgrade after M2-04/M3-03.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.budget_projection import (
    PLAN_BUDGET_FIELDS,
    BudgetRequestError,
    budget_request_from_approval,
    budget_request_from_plan,
    plan_budget_to_execution_shape,
    plan_budget_to_platform_budget,
    plan_budget_to_unit_limits,
    unwrap_budget_error,
)
from acp_services.runtime.deadline_wiring import (
    DEADLINE_SURFACES,
    apply_to_task_resources,
    deadline_wiring,
    deadline_wiring_statement,
    lsf_walltime_string,
)
from acp_services.runtime.enforcement_projection import (
    blocked_upgrades,
    enforcement_projection,
)
from acp_services.runtime.execution_limits import (
    EXECUTION_LIMITS,
    bind_launch_budget_shape,
    execution_limits_statement,
    limit_row,
    resolve_resource_limits,
    resource_limits_from_projection,
)
from acp_services.runtime.local_admission import build_local_admission
from acp_services.runtime.research_budget import (
    UNIT_CAPABILITIES,
    BudgetCapability,
    parse_budget_policy,
)
from acp_services.runtime.usage_units import UsageUnitValidationError
from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixtures
    _runtime,
    _tool,
)
from tests.test_acp2_runtime_mcp_backends import (
    live_controller as live_controller,  # noqa: PLC0414 - re-exported fixture
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RPH_MODELS = REPO_ROOT / "rph" / "packages" / "dsh-bundle" / "skills" / "models.ts"

_PLAN_BUDGET = {
    "model_requests": 2,
    "input_tokens": 1000,
    "output_tokens": 500,
    "task_refs": 1,
    "recipe_steps": 2,
    "qc_invocations": 2,
    "wall_seconds": 600,
    "queue_seconds": 120,
}


# ---------------------------------------------------------------------------
# one resolver -> three surfaces
# ---------------------------------------------------------------------------


def test_wall_seconds_reaches_all_three_termination_surfaces() -> None:
    wiring = deadline_wiring({"wall_seconds": 600, "queue_seconds": 120})
    assert wiring.local_terminate_after_s == 600
    assert wiring.ccp_resources_timeout_s == 600.0
    assert wiring.lsf_walltime_minutes == 10
    assert wiring.lsf_walltime == "00:10"
    assert wiring.queue_deadline_s == 120
    assert wiring.bounded is True


def test_cluster_walltime_only_narrows_and_empty_is_not_a_limit() -> None:
    # A deployment walltime wider than the approval never widens the deadline.
    narrower = deadline_wiring({"wall_seconds": 600}, cluster_walltime_s=120)
    assert narrower.local_terminate_after_s == 120
    assert narrower.lsf_walltime == "00:02"
    # An empty cluster walltime is "not limited", not a zero limit.
    empty = deadline_wiring({"wall_seconds": 600}, cluster_walltime_s=None)
    assert empty.local_terminate_after_s == 600
    assert lsf_walltime_string(None) is None
    assert lsf_walltime_string(0) is None
    assert lsf_walltime_string(90) == "01:30"


def test_cccp_task_resources_take_the_deadline_without_widening() -> None:
    from cccp.calculation.requests import TaskResources

    wiring = deadline_wiring({"wall_seconds": 600})
    applied = TaskResources.from_dict(apply_to_task_resources({}, wiring))
    assert applied.timeout_s == 600.0
    # An explicit shorter subprocess timeout wins; a longer one is narrowed.
    assert TaskResources.from_dict(
        apply_to_task_resources({"timeout_s": 30}, wiring)
    ).timeout_s == pytest.approx(30.0)
    assert TaskResources.from_dict(
        apply_to_task_resources({"timeout_s": 900}, wiring)
    ).timeout_s == pytest.approx(600.0)
    # Unbounded wiring adds no invented default.
    assert apply_to_task_resources({}, deadline_wiring({})) == {}


def test_lsf_walltime_is_the_tighter_of_cluster_and_deadline() -> None:
    from acp.scheduler.remote.script_gen import derive_lsf_resources

    spec = type(
        "Spec",
        (),
        {
            "resources": {"nproc": 1, "mem": "1GB"},
            "method": {},
            "workflow": "fake",
            "input": {},
        },
    )()
    _, _, _, walltime, _ = derive_lsf_resources(spec, walltime="01:00", walltime_minutes=10)
    assert walltime == "00:10"
    _, _, _, shorterm, _ = derive_lsf_resources(spec, walltime="00:05", walltime_minutes=10)
    assert shorterm == "00:05"
    _, _, _, default, _ = derive_lsf_resources(spec, walltime="", walltime_minutes=10)
    assert default == "00:10"
    _, _, _, unbounded, _ = derive_lsf_resources(spec, walltime="", walltime_minutes=None)
    assert unbounded == ""


def test_local_admission_clamps_the_duration_to_the_approval_deadline() -> None:
    import types

    admission = build_local_admission("/tmp/opencode/m203-clamp/admission.db")
    spec = types.SimpleNamespace(
        workflow="fake",
        project_id="project-clamp",
        resources={"nproc": 1, "mem": "1GB", "walltime_s": 86400},
        target_node="local",
    )
    claim = {
        "budget_id": "rb_clamp",
        "budget": {"wall_seconds": 300},
        "approved_budget": {"wall_seconds": 300},
        "project_id": "project-clamp",
    }
    request = admission.request_for_spec("job-clamp", spec, budget=claim)
    # The spec asked for 24 h; the approved deadline is 5 min (admission-bound).
    assert request.duration_s == 300
    # Without a budget the spec value is untouched (legacy behaviour).
    plain = admission.request_for_spec("job-clamp", spec)
    assert plain.duration_s == 86400


def test_admission_bound_and_process_termination_are_split_claims() -> None:
    statement = execution_limits_statement()
    admission_row = limit_row("walltime_admission_bound")
    termination_row = limit_row("walltime_process_termination")
    assert admission_row.capability == BudgetCapability.ENFORCED
    # M4-03 closed the termination claim with real receipts; the narrower
    # subprocess-level surface keeps its own honest NOT_VERIFIED.
    assert termination_row.capability == BudgetCapability.ENFORCED
    wiring_statement = deadline_wiring_statement()
    assert "local_process_group" in wiring_statement["wired"]
    assert "remote_lsf" in wiring_statement["wired"]
    assert {row["surface"] for row in wiring_statement["not_verified"]} >= {
        "cccp_subprocess",
    }
    enforced_surfaces = set(wiring_statement["enforced"])
    assert {"local_process_group", "remote_lsf", "queue_clock"} <= enforced_surfaces
    assert statement["enforced"] == [
        "walltime_admission_bound",
        "walltime_process_termination",
        "queue_deadline",
    ]
    by_surface = {row.surface: row for row in DEADLINE_SURFACES}
    assert by_surface["local_admission"].capability == BudgetCapability.ENFORCED
    for surface in ("local_process_group", "remote_lsf", "queue_clock"):
        assert by_surface[surface].capability == BudgetCapability.ENFORCED
        # Wired and certified by M4-03 real receipts.
        assert by_surface[surface].wired is True
    assert by_surface["cccp_subprocess"].capability == BudgetCapability.NOT_VERIFIED
    assert by_surface["cccp_subprocess"].wired is True


# ---------------------------------------------------------------------------
# capabilities that must NOT be upgraded yet
# ---------------------------------------------------------------------------


def test_recipe_qc_model_and_bsub_rows_stay_unenforced() -> None:
    assert UNIT_CAPABILITIES["recipe_step"] == BudgetCapability.RECORDED_ONLY
    assert UNIT_CAPABILITIES["qc_invocation"] == BudgetCapability.RECORDED_ONLY
    assert UNIT_CAPABILITIES["model_request"] == BudgetCapability.RECORDED_ONLY
    assert UNIT_CAPABILITIES["bsub"] == BudgetCapability.RECORDED_ONLY
    capabilities = {row.limit: row.capability for row in EXECUTION_LIMITS}
    # M4-03 receipts closed the two clock rows; the quota rows stay gated.
    assert capabilities["walltime_process_termination"] == BudgetCapability.ENFORCED
    assert capabilities["queue_deadline"] == BudgetCapability.ENFORCED

    blocked = blocked_upgrades()
    assert blocked["recipe_step"].startswith("M2-04")
    assert blocked["qc_invocation"].startswith("M2-04")
    assert blocked["model_request"].startswith("M3-03")
    assert "walltime_process_termination" not in blocked
    projection = enforcement_projection()
    assert projection["enforced"] == sorted(
        [
            "attempt",
            "task_ref",
            "walltime_admission_bound",
            "walltime_process_termination",
            "queue_deadline",
        ]
    )
    assert "recipe_step" not in projection["enforced"]


# ---------------------------------------------------------------------------
# one budget mapping (application_params / research_budget / RPH PlanBudget)
# ---------------------------------------------------------------------------


def test_plan_budget_maps_to_units_shape_and_platform_wire() -> None:
    limits = plan_budget_to_unit_limits(_PLAN_BUDGET)
    assert limits == {
        "model_request": 2,
        "task_ref": 1,
        "recipe_step": 2,
        "qc_invocation": 2,
    }
    shape = plan_budget_to_execution_shape(_PLAN_BUDGET)
    assert shape == {
        "wall_seconds": 600,
        "queue_seconds": 120,
        "input_tokens": 1000,
        "output_tokens": 500,
    }
    platform = plan_budget_to_platform_budget(_PLAN_BUDGET)
    # The v1 launch field only accepts unit/count/timeout_s.
    assert platform == {"unit": "task_count", "count": 1, "timeout_s": 600}


def test_rph_plan_budget_producer_contract_matches_the_mapping() -> None:
    """The mapping's field set is the RPH producer's, read from its source."""
    source = RPH_MODELS.read_text(encoding="utf-8")
    block = re.search(r"const BUDGET_SHAPE_FIELDS = new Set\(\[(.*?)\]\);", source, re.S)
    assert block is not None, "RPH PlanBudget field set moved; update the mapping"
    fields = tuple(re.findall(r'"([a-z_]+)"', block.group(1)))
    assert fields == PLAN_BUDGET_FIELDS
    # And the projection helper RPH uses is mirrored by the Python function.
    assert "export function toPlatformBudget" in source
    assert "export function toResourceLimits" in source
    assert plan_budget_to_platform_budget(_PLAN_BUDGET) == {
        "unit": "task_count",
        "count": 1,
        "timeout_s": 600,
    }


def test_plan_budget_binds_as_an_approval_budget_end_to_end(tmp_path: Path) -> None:
    """C's proposal budget (an RPH PlanBudget) is bindable and enforced as-is."""
    import types

    admission = build_local_admission(str(tmp_path / "admission.db"))
    spec = types.SimpleNamespace(
        workflow="fake",
        project_id="project-plan-budget",
        resources={"nproc": 1, "mem": "1GB"},
        target_node="local",
    )
    claim = {
        "budget_id": "rb_plan_budget",
        "budget": dict(_PLAN_BUDGET),
        "approved_budget": dict(_PLAN_BUDGET),
        "project_id": "project-plan-budget",
    }
    first = admission.admit_job("job-plan-1", spec, project_id="project-plan-budget", budget=claim)
    assert first.allowed is True
    projection = admission.ledger.research_budget_projection("rb_plan_budget")
    assert projection is not None
    # The PlanBudget unit names are the usage units; the wall bound is shape.
    assert projection.policy.limits["model_request"] == 2
    assert projection.policy.limits["qc_invocation"] == 2
    assert projection.policy.shape["wall_seconds"] == 600
    assert projection.consumed["task_ref"] == 1
    # The deadline survives a process restart (read back from the policy row).
    restarted = build_local_admission(str(tmp_path / "admission.db"))
    restored = restarted.ledger.research_budget_projection("rb_plan_budget")
    assert restored is not None
    assert resource_limits_from_projection(restored.policy.shape).lsf_walltime == "00:10"
    # task_refs=1 is the cap, so the second task is refused.
    second = admission.admit_job("job-plan-2", spec, project_id="project-plan-budget", budget=claim)
    assert second.admitted is False


def test_unknown_plan_budget_field_is_a_typed_error() -> None:
    with pytest.raises(BudgetRequestError) as excinfo:
        budget_request_from_plan({**_PLAN_BUDGET, "gpu_hours": 4})
    assert excinfo.value.rule == "unknown_budget_field"
    assert excinfo.value.http_status == 422
    assert excinfo.value.params["fields"] == ["gpu_hours"]


def test_launch_shape_units_are_not_usage_units() -> None:
    # ``core_hours``/``walltime_s`` are registered launch units; they are shape.
    limits, shape = bind_launch_budget_shape({"unit": "core_hours", "count": 5, "timeout_s": 60})
    assert limits == {}
    assert shape.core_hours == 5
    assert shape.wall_seconds == 60
    limits, shape = bind_launch_budget_shape({"unit": "walltime_s", "count": 120, "timeout_s": 600})
    assert limits == {}
    assert shape.wall_seconds == 120
    limits, shape = bind_launch_budget_shape({"unit": "task_count", "count": 1})
    assert limits == {"task_ref": 1}
    with pytest.raises(Exception) as excinfo:
        bind_launch_budget_shape({"unit": "bogus", "count": 1})
    assert getattr(excinfo.value, "rule", "") == "unknown_budget_unit"


def test_usage_unit_error_is_wrapped_as_one_typed_budget_error() -> None:
    with pytest.raises(BudgetRequestError) as excinfo:
        budget_request_from_approval({"units": {"gpu_hours": 2}})
    assert excinfo.value.rule == "unknown_budget_unit"
    assert unwrap_budget_error(UsageUnitValidationError("unknown usage unit 'gpu'")).rule == (
        "unknown_budget_unit"
    )
    # A policy parse of the same shape raises the typed budget error, never the
    # bare unit-counter ValueError and never "empty limits".
    with pytest.raises(Exception) as parsed:
        parse_budget_policy({"units": {"gpu_hours": 2}}, budget_id="rb_bad")
    assert not isinstance(parsed.value, UsageUnitValidationError)
    # A recognized launch shape unit parses without raising.
    policy = parse_budget_policy(
        {"unit": "core_hours", "count": 5, "timeout_s": 60}, budget_id="rb_core_hours"
    )
    assert policy.limits == {}
    assert policy.shape["core_hours"] == 5


def test_persisted_shape_projection_reproduces_the_three_surfaces() -> None:
    policy = parse_budget_policy(
        {"unit": "task_count", "count": 1, "timeout_s": 600}, budget_id="rb_persisted"
    )
    restored = resource_limits_from_projection(policy.shape)
    direct = resolve_resource_limits({"unit": "task_count", "count": 1, "timeout_s": 600})
    assert restored.local_terminate_after_s == direct.local_terminate_after_s == 600
    assert restored.ccp_resources_timeout_s == direct.ccp_resources_timeout_s == 600.0
    assert restored.lsf_walltime == direct.lsf_walltime == "00:10"


# ---------------------------------------------------------------------------
# real HTTP / MCP requests (the parse is the application's, not a synthetic dict)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from acp.api.server import create_app
    from acp_services.runtime.submit_policy import SUBMIT_POLICY_ENV

    monkeypatch.delenv(SUBMIT_POLICY_ENV, raising=False)
    app = create_app(run_root=tmp_path / "run-root", max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _submit_budget(client: Any, budget: Any, name: str):
    return client.post(
        "/api/v1/jobs",
        json={"workflow": "fake", "name": name, "input": {"source": "C"}, "budget": budget},
    )


def test_http_rph_plan_budget_is_refused_as_an_undeclared_launch_field(client) -> None:
    """A raw PlanBudget is not the v1 launch field: one typed 422, never a 500."""
    response = _submit_budget(client, dict(_PLAN_BUDGET), "raw-plan-budget")
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"]["rule"] == "unknown_field"
    assert "budget.model_requests" in detail["params"]["fields"]


def test_http_mapped_platform_budget_passes_the_field_policy(client) -> None:
    """The mapped shape is what the v1 wire accepts (no unknown field)."""
    response = _submit_budget(
        client, plan_budget_to_platform_budget(_PLAN_BUDGET), "mapped-plan-budget"
    )
    # A budget is research-signalling without an approval: the *policy* refuses
    # it (403 approval_required), never the parser (422/500).
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "access_denied"
    assert detail["params"]["rule"] == "approval_required"


def test_http_core_hours_launch_unit_does_not_500(client) -> None:
    response = _submit_budget(
        client, {"unit": "core_hours", "count": 5, "timeout_s": 60}, "core-hours-budget"
    )
    # The shape unit is interpreted, so the request reaches the approval gate.
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["params"]["rule"] == "approval_required"


def test_mcp_submit_refuses_the_same_budget_field(live_controller):

    runtime = _runtime(live_controller)
    result = _tool(
        runtime,
        "acp_submit",
        {
            "workflow": "fake",
            "input": {"source": "C"},
            "idempotency_key": "m2-03c-budget-0001",
            "budget": dict(_PLAN_BUDGET),
        },
    )
    assert result["isError"] is True
    error = result["structuredContent"]["error"]
    assert error["code"] == "input_invalid"
    assert error["params"]["rule"] == "unknown_field"
    assert set(error["params"]["fields"]) == {
        "budget.model_requests",
        "budget.task_refs",
        "budget.recipe_steps",
        "budget.qc_invocations",
        "budget.input_tokens",
        "budget.output_tokens",
        "budget.wall_seconds",
        "budget.queue_seconds",
    }

    mapped = _tool(
        runtime,
        "acp_submit",
        {
            "workflow": "fake",
            "input": {"source": "C"},
            "idempotency_key": "m2-03c-budget-0002",
            "budget": plan_budget_to_platform_budget(_PLAN_BUDGET),
        },
    )
    assert mapped["isError"] is True
    mapped_error = mapped["structuredContent"]["error"]
    assert mapped_error["params"]["rule"] == "approval_required"
