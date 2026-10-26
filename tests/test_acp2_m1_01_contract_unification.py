"""M1-01 contract-unification guards (a: negotiation axis, b: typed operations).

These tests exist so the three M1-01 checkpoints keep their machine gates even
when a later edit touches one surface only:

* **a** — the P2 implementation-status vocabulary (``planned|delivered|verified``,
  additive) and the advertised/implemented/negotiated relation: only a registered
  set that covers the whole ten-name promise set may report ``negotiated=true``;
* **b** — the operation/artifact schema rules that change client behaviour
  (required submit key, ``artifact_ref`` read, explicit analyze);
* **c** — the single wire-id station is covered by
  ``tests/test_acp_services_isolation.py`` (governance + bypass negatives).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "docs/reference/next-change/acp2-contracts"
P2_RELATIVE = Path(
    "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json"
)

sys.path.insert(0, str(REPO_ROOT / "scripts" / "acceptance"))


def _validator() -> Any:
    import validate_acp2_contracts

    return validate_acp2_contracts


def _mutated_root(tmp_path: Path, mutate) -> Path:
    """Copy the one P2 contract into a temp tree and mutate it."""
    target = tmp_path / P2_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / P2_RELATIVE, target)
    contract = json.loads(target.read_text(encoding="utf-8"))
    mutate(contract)
    target.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
    return tmp_path


def _assert_invalid(root: Path, needle: str) -> None:
    validator = _validator()
    with pytest.raises(validator.InvalidContractError) as excinfo:
        validator.validate_p2_implementation_status(root)
    assert needle in str(excinfo.value), str(excinfo.value)


def test_implementation_status_validator_accepts_the_live_contract() -> None:
    _validator().validate_p2_implementation_status(REPO_ROOT)


def test_implementation_status_validator_rejects_a_delivered_row_without_owner(
    tmp_path: Path,
) -> None:
    def mutate(contract: dict[str, Any]) -> None:
        contract["rows"][0]["implementation_status"] = "delivered"

    _assert_invalid(_mutated_root(tmp_path, mutate), "names no delivered_by")


def test_implementation_status_validator_rejects_a_verified_row_without_evidence(
    tmp_path: Path,
) -> None:
    def mutate(contract: dict[str, Any]) -> None:
        row = contract["rows"][1]
        row["implementation_status"] = "verified"
        row.pop("verified_by", None)

    _assert_invalid(_mutated_root(tmp_path, mutate), "names no verified_by")


def test_implementation_status_validator_rejects_a_second_status_field(
    tmp_path: Path,
) -> None:
    def mutate(contract: dict[str, Any]) -> None:
        contract["rows"][0]["implemented"] = True

    _assert_invalid(_mutated_root(tmp_path, mutate), "second 'implemented' status field")


def test_implementation_status_validator_rejects_an_unknown_value(tmp_path: Path) -> None:
    def mutate(contract: dict[str, Any]) -> None:
        contract["rows"][0]["implementation_status"] = "implemented"

    _assert_invalid(_mutated_root(tmp_path, mutate), "implementation_status is invalid")


def test_implementation_status_validator_rejects_a_promise_outside_the_set(
    tmp_path: Path,
) -> None:
    def mutate(contract: dict[str, Any]) -> None:
        contract["rows"][0]["tools"][0]["name"] = "acp_research_unlisted_v1"

    _assert_invalid(_mutated_root(tmp_path, mutate), "outside p2_promised")


def test_only_a_covering_implementation_set_negotiates() -> None:
    """Executable proof of the D-INT-02 rule (advertising is not delivery)."""
    from acp_services.entrypoints import mcp

    contract = json.loads((CONTRACTS / "runtime" / "p2-research-operations.v1.json").read_text())
    negotiation = contract["client_path"]["negotiation"]
    spec = mcp.ExtensionSpec(
        extension_id=negotiation["extension_id"],
        version=negotiation["extension_version"],
        promised_tools=tuple(contract["client_path"]["tool_sets"]["p2_promised"]),
    )
    requested = {"id": negotiation["extension_id"], "version": negotiation["extension_version"]}

    # Advertising only: the registered set is the eight P1 tools.
    advertised = mcp.negotiate_research_extension(spec, None)
    entry = advertised.entries[0]
    assert (entry["implemented"], entry["negotiated"]) == (False, False)

    # A partial registration never negotiates and is refused by name.
    partial = frozenset(set(mcp.TOOL_ALLOWLIST) | set(spec.promised_tools[:-1]))
    refused = mcp.negotiate_research_extension(spec, requested, registered=partial)
    assert refused.negotiated is False
    assert refused.refusal is not None
    assert refused.refusal["params"]["rule"] == "extension_not_implemented"
    assert refused.entries[0]["implemented"] is False

    # A covering implementation with the matching version is the only way.
    covering = frozenset(set(mcp.TOOL_ALLOWLIST) | set(spec.promised_tools))
    negotiated = mcp.negotiate_research_extension(spec, requested, registered=covering)
    assert negotiated.negotiated is True
    assert negotiated.refusal is None
    assert negotiated.entries[0]["implemented"] is True

    # A covering set with a different requested version is still refused.
    mismatch = mcp.negotiate_research_extension(
        spec,
        {"id": negotiation["extension_id"], "version": "9.9.9"},
        registered=covering,
    )
    assert mismatch.negotiated is False
    assert mismatch.refusal["params"]["rule"] == "extension_version_mismatch"


def test_operation_schemas_freeze_the_m1_01b_client_contract() -> None:
    from acp_services.entrypoints import mcp

    api = json.loads((CONTRACTS / "runtime" / "application-api.v1.json").read_text())
    control = json.loads((CONTRACTS / "runtime" / "control-and-data.v1.json").read_text())

    assert "idempotency_key" in api["operations"]["submit"]["request"]["required"]
    assert api["submit_field_policy"]["submit_key_policy"]["mcp"].startswith("REQUIRED")
    key_policy = control["idempotency"]["key_policy"]
    assert key_policy["mcp_submit"]["missing_key_result"]["params"]["rule"] == (
        "idempotency_key_required"
    )
    assert key_policy["http_manual_legacy"]["allowed_without_key"] is True
    assert "NON-IDEMPOTENT" in key_policy["http_manual_legacy"]["contract_statement"]
    assert key_policy["mcp_submit"]["short_key_cross_tenant_result"]["params"]["rule"] == (
        "idempotency_scope_conflict"
    )

    assert set(control["artifacts"]["artifact_ref"]["semantics"]) == {
        "task",
        "job",
        "attempt",
        "resource",
        "artifact",
        "generation",
        "digest",
    }
    assert "no_implicit_compute" in control["artifacts"]["prepare"]
    assert "no_implicit_compute" in control["artifacts"]["read"]
    assert control["analyze"]["capability_states"]["values"] == [
        "registered",
        "executable",
        "unavailable",
    ]

    runtime_submit = next(tool for tool in mcp._tool_specs() if tool["name"] == mcp.McpTool.SUBMIT)[
        "inputSchema"
    ]
    assert runtime_submit["required"] == ["workflow", "input", "idempotency_key"]
    assert runtime_submit["additionalProperties"] is False
