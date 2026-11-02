"""Parameter disposition registry for the first audited workflows (round-5 PR-03).

The registry answers, for every contract-declared field, what the platform
actually does with it (consumed / normalized alias / metadata / rejected /
legacy noop / conditionally not applicable) and which behaviour test proves it.
It never becomes a second allowlist: :func:`unregistered_contract_fields`
enumerates the existing ``WORKFLOW_CONTRACTS`` and reports fields missing from
the registry, so a new contract field cannot silently ship un-audited.

Audited in this batch: PESsearch, scan, irc, tsmode (the plan's first batch).
Other contracted workflows stay explicitly partial (their fields are reported
as ``declared_unverified`` by :func:`coverage`), never as audited.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "AUDITED_WORKFLOWS",
    "RegisteredEffect",
    "coverage",
    "registry",
    "unregistered_contract_fields",
]

#: Workflows whose contract fields must all appear in the registry.
AUDITED_WORKFLOWS = ("PESsearch", "scan", "irc", "tsmode")

#: Behaviour test proving each disposition (module file + test function name).
_PES_TESTS = "tests/test_acp_parameter_effects.py"
_PARITY_TESTS = "tests/test_acp_round5_preflight_parity.py"
_PR01_TESTS = "tests/test_acp_round5_coordinate_contract.py"
_PR02_TESTS = "tests/test_cccp_irc_boundary_contract.py"


@dataclass(frozen=True)
class RegisteredEffect:
    workflow: str
    field: str
    disposition: str
    detail: str = ""
    behavior_test: str = ""


#: Contract-declared fields whose consumer audit is deferred (explicit list, not
#: automatic): a new contract key is NOT covered here and fails the guard test.
_DECLARED_ONLY: dict[str, tuple[str, ...]] = {
    "PESsearch": (
        "method.mode",
        "method.select",
        "method.coordinate_plan",
        "method.scan_request",
        "method.profile_id",
        "method.schema_id",
        "input.source",
        "input.input",
        "input.smiles",
        "input.source_type",
        "input.charge",
        "input.multiplicity",
        "input.source_ref",
        "input.asset_id",
        "input.input_artifact",
        "input.selection",
        "input.protocol",
        "input.source_job_id",
        "input.from_artifact",
        "input.from",
        "input.coordinate_plan",
        "input.product",
        "input.ts_guess",
    ),
    "scan": (
        "method.levels",
        "method.profile_id",
        "method.schema_id",
        "method.scan_use_scants",
        "method.use_scants",
        "input.source",
        "input.input",
        "input.smiles",
        "input.source_type",
        "input.charge",
        "input.multiplicity",
        "input.source_ref",
        "input.asset_id",
        "input.input_artifact",
        "input.xyz_text",
    ),
    "irc": (
        "method.basis",
        "method.functional",
        "method.levels",
        "method.method",
        "method.schema_id",
        "input.charge",
        "input.multiplicity",
        "input.directions",
        "input.input",
        "input.input_artifact",
        "input.input_role",
        "input.smiles",
        "input.source",
        "input.source_id",
        "input.source_job_id",
        "input.source_product_id",
        "input.source_ref",
        "input.source_type",
        "input.ts_provenance",
        "input.ts_source",
    ),
    "tsmode": (
        "input.allow_unverified_mapping",
        "input.bundle",
        "input.final_frequency",
        "input.input",
        "input.level_overrides",
        "input.max_iterations",
        "input.recalc_hess",
        "input.retry_limit",
        "input.source_type",
        "input.trust_radius",
    ),
}


_EFFECTS: tuple[RegisteredEffect, ...] = (
    # ---- PESsearch ---------------------------------------------------------
    RegisteredEffect(
        "PESsearch",
        "input.coordinate",
        "consumed",
        "primary coordinate; must equal coordinates[0] when both are given",
        f"{_PR01_TESTS}::test_primary_listed_conflict_is_refused",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.coordinates",
        "consumed",
        "synchronized list; coordinates-only allowed (primary = first)",
        f"{_PR01_TESTS}::test_coordinates_only_preflights",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.protocol.scan_driver.retry_count",
        "normalized_alias",
        "alias of protocol.scan_optimizer.retry_count; conflicts refused",
        f"{_PES_TESTS}::test_scan_driver_retry_alias_and_conflict",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.protocol.scan_driver.max_iterations",
        "normalized_alias",
        "alias of protocol.scan_optimizer.max_iterations; conflicts refused",
        f"{_PES_TESTS}::test_scan_driver_retry_alias_and_conflict",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.protocol.scan_driver.retry_strategy",
        "rejected",
        "native scan cannot honour per-point retry strategy",
        f"{_PES_TESTS}::test_native_scan_rejects_pointwise_only_driver_fields",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.protocol.scan_driver.failure_policy",
        "rejected",
        "native scan cannot honour per-point failure policy",
        f"{_PES_TESTS}::test_native_scan_rejects_pointwise_only_driver_fields",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.protocol.scan_driver.reuse_previous_geometry",
        "rejected",
        "native scan cannot honour per-point geometry reuse",
        f"{_PES_TESTS}::test_native_scan_rejects_pointwise_only_driver_fields",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.protocol.execution_mode",
        "rejected",
        "server output field; clients must not set it",
        f"{_PES_TESTS}::test_protocol_execution_mode_is_rejected",
    ),
    RegisteredEffect(
        "PESsearch",
        "input.scan_request",
        "consumed",
        "prepared carrier compared against the expanded fields",
        f"{_PES_TESTS}::test_scan_request_carrier_must_agree",
    ),
    RegisteredEffect(
        "PESsearch",
        "method.strategy=guided_scan",
        "legacy_noop",
        "accepted name; does not switch the algorithm",
        f"{_PES_TESTS}::test_guided_scan_is_legacy_noop",
    ),
    RegisteredEffect(
        "PESsearch",
        "method.strategy=reverse_peb/direct_ts",
        "rejected",
        "unimplemented strategies refused for new executions",
        f"{_PES_TESTS}::test_unimplemented_strategy_is_rejected",
    ),
    # ---- scan --------------------------------------------------------------
    RegisteredEffect(
        "scan",
        "input.scan_coordinates",
        "consumed",
        "one normalized list shared by flags and validation",
        f"{_PR01_TESTS}::test_scan_alias_conflict_is_refused",
    ),
    RegisteredEffect(
        "scan",
        "input.coordinate",
        "consumed",
        "alias of scan_coordinates; disagreement refused",
        f"{_PR01_TESTS}::test_scan_alias_conflict_is_refused",
    ),
    RegisteredEffect(
        "scan",
        "method.scan_coordinates",
        "consumed",
        "method-side alias; must normalize to the same list",
        f"{_PR01_TESTS}::test_scan_alias_conflict_is_refused",
    ),
    RegisteredEffect(
        "scan",
        "method.coordinate",
        "consumed",
        "method-side alias; must normalize to the same list",
        f"{_PR01_TESTS}::test_scan_alias_conflict_is_refused",
    ),
    RegisteredEffect(
        "scan",
        "method.scan_points",
        "consumed",
        "one effective point count; conflicting aliases refused",
        f"{_PR01_TESTS}::test_scan_point_alias_conflict_is_refused",
    ),
    # ---- irc ---------------------------------------------------------------
    RegisteredEffect(
        "irc",
        "method.step",
        "rejected",
        "no validated ORCA IRC step mapping; explicit values refused",
        f"{_PR02_TESTS}::test_contract_refuses_explicit_step_at_top_level",
    ),
    RegisteredEffect(
        "irc",
        "method.levels.irc.step",
        "rejected",
        "same refusal inside the level block",
        f"{_PR02_TESTS}::test_contract_refuses_step_inside_the_irc_level",
    ),
    RegisteredEffect(
        "irc",
        "method.initial_hessian",
        "rejected",
        "ORCA IRC does not consume it; the Hessian handoff is supported",
        f"{_PR02_TESTS}::test_typed_options_refuse_step_and_initial_hessian",
    ),
    RegisteredEffect(
        "irc",
        "method.maxpoints",
        "consumed",
        "iteration cap; mirrors must agree",
        f"{_PARITY_TESTS}::test_irc_maxpoints_mirror_conflict_is_refused_in_analysis",
    ),
    # ---- tsmode ------------------------------------------------------------
    RegisteredEffect(
        "tsmode",
        "input.source_job_id",
        "consumed",
        "frequency source task (required)",
        f"{_PR01_TESTS}::test_scan_alias_conflict_is_refused",
    ),
    RegisteredEffect(
        "tsmode",
        "input.entry_id",
        "consumed",
        "frequency entry selection",
        f"{_PARITY_TESTS}::test_preflight_and_submit_agree_on_static_errors",
    ),
    RegisteredEffect(
        "tsmode",
        "input.source_mode_index",
        "consumed",
        "raw mode index inside the source bundle",
        f"{_PARITY_TESTS}::test_preflight_and_submit_agree_on_static_errors",
    ),
)


def registry() -> dict[str, dict[str, RegisteredEffect]]:
    """workflow -> field prefix -> disposition."""
    result: dict[str, dict[str, RegisteredEffect]] = {}
    for effect in _EFFECTS:
        result.setdefault(effect.workflow, {})[effect.field] = effect
    return result


def _declared_fields(workflow: str) -> set[str]:
    """Fields the shared contract declares for *workflow* (existing authority)."""
    from acp.api.submission_contract import WORKFLOW_CONTRACTS

    contract = WORKFLOW_CONTRACTS.get(workflow)
    if contract is None:
        return set()
    fields = {f"method.{key}" for key in contract.method_keys}
    fields |= {f"input.{key}" for key in contract.input_keys}
    # the disposition registry also names value-scoped entries
    # (``method.strategy=guided_scan``); compare on the base path
    return fields


def _registered_roots(workflow: str) -> set[str]:
    return {field.split("=", 1)[0] for field in registry().get(workflow, {})}


def unregistered_contract_fields(workflow: str) -> list[str]:
    """Contract-declared fields with no disposition entry (coverage guard)."""
    declared = _declared_fields(workflow)
    roots = _registered_roots(workflow)
    declared_only = {field.split("=", 1)[0] for field in _DECLARED_ONLY.get(workflow, ())}
    missing: list[str] = []
    for field in sorted(declared):
        base = field.split("=", 1)[0]
        if base in declared_only or base in roots:
            continue
        if field in roots:
            continue
        missing.append(field)
    return missing


def coverage() -> dict[str, dict[str, int | str]]:
    """Per-workflow audit coverage without claiming unaudited workflows."""
    result: dict[str, dict[str, int | str]] = {}
    for workflow in AUDITED_WORKFLOWS:
        missing = unregistered_contract_fields(workflow)
        result[workflow] = {
            "status": "audited" if not missing else "partial",
            "unregistered_fields": len(missing),
            "declared_only_fields": len(_DECLARED_ONLY.get(workflow, ())),
        }
    return result
