"""M0-12 (G02/G04/G20) — the integration decision freeze is machine-checked.

``docs/reference/next-change/acp2-contracts/decision-freeze/integration.v1.json``
freezes the extension negotiation, the contract mutation path, the CI required
scope, the Ruff hygiene exclusion and the manifest registration rule.  This file
pins, independently of the validator's own helpers:

1. **Structure and honesty** — required fields, the three statuses, and the rule
   that a ``pending_*`` decision can never be read as ``frozen``.
2. **Wire references are real** — every ``wire_refs`` entry resolves against a
   live file, and ``equals`` entries match the value the contract actually has
   today (``request_carrier == tools/call``, not a remembered copy).
3. **Vectors resolve** — every ``vectors[].test`` names a real test in a real file.
4. **The branch decision is anchored** — M0-13 is ``NOT_APPLICABLE`` in the
   M0-09 summary and in the release scope, with explicit reopen conditions.
5. **Ruff stays out of the required chain** — the hygiene decision, the release
   scope and the workflow all agree that the ratchet is not a closing condition.
6. **Registration is mechanically executable** — the expected manifest entries
   are complete, never embed a self-referential digest, and match the live
   manifest in both directions.

Nothing here executes science, a Controller, Node or a browser: it is decision
and contract evidence only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from acp import mcp_extension  # noqa: E402
from acp_services.entrypoints import mcp as mcp_entrypoints  # noqa: E402
from scripts.acceptance import validate_acp2_contracts as contract_validator  # noqa: E402
from scripts.acceptance import validate_acp2_decision_freeze as validator  # noqa: E402

CONTRACTS = REPO_ROOT / "docs/reference/next-change/acp2-contracts"
FREEZE_PATH = CONTRACTS / "decision-freeze/integration.v1.json"
IDENTITY_FREEZE = CONTRACTS / "decision-freeze/identity.v1.json"
P2_CONTRACT = CONTRACTS / "runtime/p2-research-operations.v1.json"
APPLICATION_API = CONTRACTS / "runtime/application-api.v1.json"
LAUNCH_SCHEMA = CONTRACTS / "runtime/mcp-launch.schema.json"
LAUNCH_TEMPLATE = CONTRACTS / "runtime/mcp-launch.template.json"
LINT_BASELINE = CONTRACTS / "runtime/lint-baseline.json"
SCOPE = CONTRACTS / "release/scope.json"
MANIFEST = CONTRACTS / "manifest.json"
ADR_006 = REPO_ROOT / "docs/reference/next-change/adr/ADR-006-mcp-negotiation.md"
CI_PATH = REPO_ROOT / ".github/workflows/ci.yml"
CONTRACT_VALIDATOR = REPO_ROOT / "scripts/acceptance/validate_acp2_contracts.py"
M009_SUMMARY = REPO_ROOT / "reports/acp2_remediation_20261026/m0-09/summary.json"
BRIDGE = REPO_ROOT / "rph/packages/bridge/src/index.ts"

FROZEN = "frozen"
PENDING_IMPLEMENTATION = "pending_implementation"
PENDING_CONFLICT = "pending_conflict"


# ---------------------------------------------------------------------------
# helpers (independent of the validator's own resolution helpers)
# ---------------------------------------------------------------------------


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def freeze() -> dict[str, Any]:
    """The parsed integration freeze (read-only: copy before mutating)."""
    return _load(FREEZE_PATH)


@pytest.fixture(scope="module")
def decisions(freeze: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in freeze["decisions"] if isinstance(item, dict)]


def _decision(freeze: dict[str, Any], identifier: str) -> dict[str, Any]:
    for item in freeze["decisions"]:
        if isinstance(item, dict) and item["id"] == identifier:
            return item
    raise AssertionError(f"decision {identifier} is missing from the freeze")


def _pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 pointer, with ``field=value`` selectors for arrays."""
    node = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if token.isdigit():
                node = node[int(token)]
                continue
            field, _, value = token.partition("=")
            matches = [item for item in node if str(item.get(field)) == value]
            assert len(matches) == 1, (pointer, token, len(matches))
            node = matches[0]
        else:
            node = node[token]
    return node


def _vector_names(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        pattern = r"^\s*(?:async\s+)?def\s+([A-Za-z0-9_]+)\s*\("
    else:
        pattern = r"\b(?:test|it)\(\s*[\"']([^\"']+)[\"']"
    return set(re.findall(pattern, text, re.MULTILINE))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and isinstance(data.get("jobs"), dict)
    return data


def _runs(job: dict[str, Any]) -> list[str]:
    steps = [step for step in job.get("steps") or [] if isinstance(step, dict)]
    return [" ".join(str(step.get("run", "")).split()) for step in steps]


# ---------------------------------------------------------------------------
# 1. Structure, honesty and vector resolution
# ---------------------------------------------------------------------------


def test_freeze_document_is_valid() -> None:
    """The shipped freeze passes the mechanical validator (schema + wire + blocks)."""
    errors = validator.validate(FREEZE_PATH, REPO_ROOT)
    assert errors == [], errors


def test_every_decision_carries_the_required_fields(
    freeze: dict[str, Any], decisions: list[dict[str, Any]]
) -> None:
    """decision/owner/impact/versions/rollback/vectors/wire_refs, per EXECUTION_PLAN."""
    assert freeze["schema"] == "acp2.decision-freeze.integration.v1"
    assert freeze["task"] == "M0-12"
    assert freeze["owner"] == "INT"
    assert freeze["closes"] == ["G02", "G04", "G20"]
    assert len(decisions) >= 8
    identifiers = [item["id"] for item in decisions]
    assert len(identifiers) == len(set(identifiers))
    for decision in decisions:
        assert re.fullmatch(r"D-INT-\d{2}", decision["id"]), decision["id"]
        assert decision["status"] in {FROZEN, PENDING_IMPLEMENTATION, PENDING_CONFLICT}
        assert decision["owner"] in {"B", "A", "C-RPH", "C-Web", "INT"}, decision["id"]
        assert decision["signoff"], decision["id"]
        for field in ("decision", "rationale", "rollback"):
            assert isinstance(decision[field], str) and decision[field].strip(), (
                decision["id"],
                field,
            )
        for field in ("wire_version", "migration_version"):
            assert field in decision, (decision["id"], field)
            assert decision[field] is None or isinstance(decision[field], str)
        assert decision["impact"]["files"], decision["id"]
        assert decision["impact"]["entries"], decision["id"]
        for relative in decision["impact"]["files"]:
            assert (REPO_ROOT / relative).exists(), (decision["id"], relative)
        assert decision["vectors"], decision["id"]
        assert decision["wire_refs"], decision["id"]


def test_every_vector_names_an_existing_test(decisions: list[dict[str, Any]]) -> None:
    """A frozen claim must point at a test that exists (no phantom evidence)."""
    for decision in decisions:
        for vector in decision["vectors"]:
            relative, _, name = vector["test"].partition("::")
            path = REPO_ROOT / relative
            assert path.is_file(), (decision["id"], relative)
            assert name in _vector_names(path), (decision["id"], relative, name)
            assert vector["asserts"].strip(), (decision["id"], relative)


def test_pending_decisions_are_never_frozen(
    freeze: dict[str, Any], decisions: list[dict[str, Any]]
) -> None:
    """未决项不得写 frozen: every pending decision says why and who closes it."""
    pending_ids = set()
    for decision in decisions:
        if decision["status"] == FROZEN:
            assert "pending_reason" not in decision, decision["id"]
            assert "frozen_forward_convention" not in decision, decision["id"]
            continue
        pending_ids.add(decision["id"])
        assert decision["pending_reason"].strip(), decision["id"]
        assert decision["consumer_task"].strip(), decision["id"]
        assert decision["frozen_forward_convention"] is True, decision["id"]
        assert isinstance(decision["blocking"], bool), decision["id"]
    listed = {entry["id"]: entry["status"] for entry in freeze["pending_summary"]}
    assert set(listed) == pending_ids
    for decision in decisions:
        if decision["id"] in listed:
            assert listed[decision["id"]] == decision["status"]
    # M0-12 leaves exactly two forward conventions and no open conflict.
    assert {decision["id"] for decision in decisions if decision["status"] != FROZEN} == {
        "D-INT-07",
        "D-INT-08",
    }


def test_decision_statistics_and_machine_readable_fields(
    decisions: list[dict[str, Any]],
) -> None:
    """The M1-01-consumable fields are typed, not prose (status/wire/rollback/vectors)."""
    counts = {FROZEN: 0, PENDING_IMPLEMENTATION: 0, PENDING_CONFLICT: 0}
    for decision in decisions:
        counts[decision["status"]] += 1
        assert decision["rollback"].strip() and len(decision["rollback"]) >= 20
        for vector in decision["vectors"]:
            assert isinstance(vector["test"], str) and "::" in vector["test"]
    assert counts == {FROZEN: 6, PENDING_IMPLEMENTATION: 2, PENDING_CONFLICT: 0}


# ---------------------------------------------------------------------------
# 2. Wire references resolve to the live contracts
# ---------------------------------------------------------------------------


def test_every_wire_reference_resolves_independently(
    decisions: list[dict[str, Any]],
) -> None:
    """Every reference resolves without trusting the validator's resolver."""
    resolved = 0
    for decision in decisions:
        for reference in decision["wire_refs"]:
            path = REPO_ROOT / reference["path"]
            assert path.exists(), (decision["id"], reference["path"])
            assert reference["why"].strip(), (decision["id"], reference["path"])
            assertions = {"equals", "contains", "absent", "exists"} & set(reference)
            assert len(assertions) == 1, (decision["id"], reference)
            kind = assertions.pop()
            if kind == "exists":
                assert reference["exists"] is True
                resolved += 1
                continue
            pointer = reference.get("pointer")
            if pointer is None:
                haystack = path.read_text(encoding="utf-8", errors="replace")
                value = None
            else:
                value = _pointer(_load(path), pointer)
                haystack = value if isinstance(value, str) else json.dumps(value)
            if kind == "equals":
                assert pointer is not None, (decision["id"], reference)
                assert value == reference["equals"], (
                    decision["id"],
                    reference["path"],
                    pointer,
                    value,
                    reference["equals"],
                )
            elif kind == "contains":
                assert reference["contains"] in haystack, (decision["id"], reference)
            else:
                assert reference["absent"] not in haystack, (decision["id"], reference)
            resolved += 1
    # A freeze with fewer than 20 resolved references is not a freeze.
    assert resolved >= 20


def test_wire_references_pin_the_negotiation_carrier(
    freeze: dict[str, Any], decisions: list[dict[str, Any]]
) -> None:
    """The carrier is pinned to its live value, never to a remembered copy."""
    decision = _decision(freeze, "D-INT-02")
    p2 = _load(P2_CONTRACT)["client_path"]["negotiation"]
    by_pointer = {
        reference.get("pointer"): reference.get("equals")
        for reference in decision["wire_refs"]
        if reference.get("path", "").endswith("p2-research-operations.v1.json")
    }
    assert by_pointer["/client_path/negotiation/request_carrier"] == "tools/call"
    assert by_pointer["/client_path/negotiation/request_tool"] == "acp_capabilities"
    assert by_pointer["/client_path/negotiation/request_field"] == "research_extension"
    assert by_pointer["/client_path/negotiation/initialize_claim"] == "none"
    assert p2["request_carrier"] == "tools/call"
    assert p2["initialize_claim"] == "none"
    assert "initialize" not in json.dumps(p2["request_carrier"])


def test_extension_version_authority_is_single() -> None:
    """Contract -> projection -> acp constant -> RPH constant carry one id/version."""
    p2 = _load(P2_CONTRACT)["client_path"]["negotiation"]
    projection = _load(APPLICATION_API)["mcp_extensions"]["acp.research.v1"]
    bridge = BRIDGE.read_text(encoding="utf-8")
    services = (REPO_ROOT / "src/acp_services/entrypoints/mcp.py").read_text(encoding="utf-8")
    assert p2["extension_id"] == mcp_extension.RESEARCH_EXTENSION_ID == "acp.research.v1"
    assert p2["extension_version"] == "1.0.0"
    assert projection["version"] == p2["extension_version"]
    assert mcp_extension.RESEARCH_EXTENSION_VERSION == p2["extension_version"]
    assert 'BRIDGE_EXTENSION_ID = "acp.research.v1"' in bridge
    assert 'BRIDGE_EXTENSION_VERSION = "1.0.0"' in bridge
    # acp_services receives the spec; it must not own the pair.
    assert "RESEARCH_EXTENSION_ID =" not in services
    assert "RESEARCH_EXTENSION_VERSION =" not in services


def test_negotiation_carrier_is_tools_call_and_initialize_claims_nothing(
    freeze: dict[str, Any],
) -> None:
    """initialize stays claim-free; the only negotiated surface is extensions[]."""
    decision = _decision(freeze, "D-INT-02")
    block = decision["negotiation_freeze"]
    assert mcp_entrypoints.EXTENSION_HANDSHAKE_CARRIER == "tools/call:acp_capabilities"
    assert block["carrier"] == mcp_entrypoints.EXTENSION_HANDSHAKE_CARRIER
    assert block["request_field"] == "research_extension"
    assert block["initialize_claim"] == "none"
    assert block["initialize_may_only_point_at_the_carrier"] is True
    assert block["negotiated_requires"] == ["implemented"]
    assert "extensions[].negotiated" in block["negotiated_surface"]
    assert block["forbidden"]
    # The launch descriptor is metadata only: negotiated is const false.
    schema = _load(LAUNCH_SCHEMA)["properties"]["initialize"]["properties"]["extensions"][
        "properties"
    ]["acp.research.v1"]["properties"]["negotiated"]
    assert schema["const"] is False
    template = _load(LAUNCH_TEMPLATE)["instance"]["initialize"]["extensions"]["acp.research.v1"]
    assert template["negotiated"] is False


# ---------------------------------------------------------------------------
# 3. The M0-09 branch decision is anchored
# ---------------------------------------------------------------------------


def test_m0_13_not_applicable_is_recorded_with_reopen_conditions(freeze: dict[str, Any]) -> None:
    """NOT_APPLICABLE is recorded upstream and carries what would reopen it."""
    summary = _load(M009_SUMMARY)["decision"]
    scope = _load(SCOPE)
    conditional = next(entry for entry in scope["conditional_tasks"] if entry["id"] == "M0-13")
    assert summary["outcome"] == "path_selected"
    assert summary["m0_13"]["status"] == "NOT_APPLICABLE"
    assert summary["m0_13"]["reason"] == "external_change_not_required"
    assert conditional["scope"] == "NOT_APPLICABLE"
    decision = _decision(freeze, "D-INT-03")
    block = decision["branch_decision"]
    assert decision["status"] == FROZEN
    assert block["selected"] == summary["m0_13"]["reason"]
    assert block["m0_13_status"] == summary["m0_13"]["status"]
    assert block["dsh_source_modified"] is False
    assert block["bom_entry_added"] is False
    conditions = {entry["id"]: entry for entry in block["reopen_conditions"]}
    assert set(conditions) == {"REOPEN-UPSTREAM-CAPABILITY", "REOPEN-RESEARCH-ABANDONED"}
    for condition in conditions.values():
        assert condition["trigger"].strip() and condition["required"].strip()
    # A reopen always needs a new ADR/scope decision, never an in-place edit.
    assert "scope_change" in conditions["REOPEN-UPSTREAM-CAPABILITY"]["required"]
    assert "scope_change" in conditions["REOPEN-RESEARCH-ABANDONED"]["required"]


def test_branch_decision_is_consistent_with_the_m0_09_provenance(freeze: dict[str, Any]) -> None:
    """The branch derives from the recorded M0-09 path/carrier, not from prose."""
    summary = _load(M009_SUMMARY)["decision"]
    branch = _decision(freeze, "D-INT-03")["branch_decision"]
    negotiation = _decision(freeze, "D-INT-02")["negotiation_freeze"]
    assert branch["m0_09_outcome"] == summary["outcome"]
    assert branch["m0_09_path"] == summary["path"]
    assert negotiation["carrier"] == summary["carrier"]
    assert negotiation["request_field"] == summary["request_field"]
    assert negotiation["initialize_claim"] == summary["initialize_claim"]


# ---------------------------------------------------------------------------
# 4. Contract mutation ownership
# ---------------------------------------------------------------------------


def test_contract_mutation_ownership_names_every_surface(freeze: dict[str, Any]) -> None:
    """Authority -> projection -> descriptor, each with its ADR/sample/digest duty."""
    decision = _decision(freeze, "D-INT-04")
    block = decision["contract_mutation"]
    assert block["authority"].endswith("p2-research-operations.v1.json::client_path.negotiation")
    assert block["projection"].endswith("application-api.v1.json::mcp_extensions[acp.research.v1]")
    assert {Path(path).name for path in block["descriptor"]} == {
        "mcp-launch.schema.json",
        "mcp-launch.template.json",
    }
    steps = " ".join(block["required_steps"])
    for marker in ("ADR-006", "samples", "contract_digest", "P1"):
        assert marker in steps, marker
    assert block["forbidden"], "the mutation must say what is forbidden"
    assert (REPO_ROOT / block["machine_gate"]).is_file()
    # The gate still knows about the stage allowlist it must be extended with.
    assert "DELIVERED_IDS_BY_STAGE" in CONTRACT_VALIDATOR.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. CI required scope and the Ruff hygiene exclusion
# ---------------------------------------------------------------------------


def test_ci_required_scope_matches_the_workflow(freeze: dict[str, Any]) -> None:
    """Every frozen CI claim resolves against the parsed workflow."""
    workflow = _workflow()
    jobs = workflow["jobs"]
    decision = _decision(freeze, "D-INT-05")
    block = decision["ci_scope"]
    matrix = jobs["test"]["strategy"]["matrix"]["python-version"]
    assert matrix == block["matrix"]
    assert block["matrix_excludes_markers"] == ["slow", "contract"]
    test_runs = " ".join(_runs(jobs["test"]))
    assert "not slow and not contract" in test_runs
    # Exactly one lane selects the contract marker.
    selecting = {
        name
        for name, job in jobs.items()
        if any("-m contract" in run or '-m "contract"' in run for run in _runs(job))
    }
    assert selecting == {block["contract_marker_owner"]}, selecting
    assert block["contract_marker_owner_count"] == 1
    conformance_runs = " ".join(_runs(jobs["mcp-conformance"]))
    for marker in (
        "EMPTY_CONTRACT_SELECTION",
        "PARTIAL_CONTRACT_SKIP",
        "DSH_AVAILABLE_BUT_SKIPPED",
        "DSH_DECLARED_BUT_UNRESOLVED",
    ):
        assert marker in conformance_runs, marker
    conformance_steps = json.dumps(jobs["mcp-conformance"], ensure_ascii=False)
    assert "vars.ACP2_DSH_SOURCE" in conformance_steps
    assert block["dsh_source"]["env_var"] == "ACP2_DSH_SOURCE"
    assert block["dsh_source"]["vendored"] is False
    frontend = jobs["frontend-module-tests"]
    assert any("contains no *.test.js files" in run for run in _runs(frontend))
    assert any("run_all.js" in run for run in _runs(frontend))
    assert block["frontend_gate"]["non_empty_guard"] is True
    rph = jobs["rph"]
    rph_runs = _runs(rph)
    assert any("pnpm package" in run for run in rph_runs)
    upload = next(
        step for step in rph["steps"] if str(step.get("uses", "")).startswith("actions/upload")
    )
    assert upload["with"]["path"] == "rph/dist-packages/*"
    assert upload["with"]["if-no-files-found"] == "error"
    assert block["rph_gate"]["packages_before_upload"] is True
    wall = block["wall_clock"]
    assert wall["recorded"] is True
    assert wall["max_minutes_before_split"] == 20
    for name in wall["jobs_with_wall_clock"]:
        assert any("ACP2_JOB_WALL_CLOCK_SECONDS" in run for run in _runs(jobs[name])), name
    # The only untimed job is the boolean detector, and the freeze says so.
    untimed = {
        name
        for name, job in jobs.items()
        if not any("ACP2_JOB_WALL_CLOCK_SECONDS" in run for run in _runs(job))
    }
    assert untimed == {entry["job"] for entry in wall["jobs_without"]}, untimed


def test_ruff_hygiene_is_excluded_from_the_required_chain(freeze: dict[str, Any]) -> None:
    """Ruff 0.15.22 is hygiene only: no G item may close (or block) on it."""
    decision = _decision(freeze, "D-INT-06")
    block = decision["hygiene_decision"]
    baseline = _load(LINT_BASELINE)
    assert decision["status"] == FROZEN
    assert block["pinned_version"] == baseline["ruff_version"] == "0.15.22"
    assert block["excluded_from_required_chain"] is True
    assert block["required_chain"] == "G01-G26"
    assert block["never_a_closing_condition"] is True
    assert block["baseline_may_only_shrink"] is True
    # The release scope says the same thing, with required=false.
    scope_rows = {entry["id"]: entry for entry in _load(SCOPE)["capabilities"] if "id" in entry}
    assert scope_rows["SCOPE-OUT-07"]["required"] is False
    assert "不进入 G01–G26" in scope_rows["SCOPE-OUT-07"]["reason"]
    # And the workflow still does not gate on it.
    raw = CI_PATH.read_text(encoding="utf-8")
    assert "# - name: Lint with ruff" in raw
    assert "\n      - name: Lint with ruff" not in raw
    # No decision may name ruff as part of a required chain.
    for item in freeze["decisions"]:
        assert "required_chain" not in item or item["id"] == "D-INT-06"


def test_ruff_implementation_is_recorded_as_pending_with_an_honest_absence(
    freeze: dict[str, Any],
) -> None:
    """The pin/baseline/ratchet are pending; the claim is checked against the disk."""
    decision = _decision(freeze, "D-INT-07")
    block = decision["hygiene_implementation"]
    missing = block["missing_artifacts"]
    assert missing
    for relative in missing:
        assert not (REPO_ROOT / relative).exists(), (
            f"{relative} exists, so D-INT-07 must be regenerated and flipped"
        )
    assert decision["status"] == PENDING_IMPLEMENTATION
    assert decision["frozen_forward_convention"] is True
    assert decision["blocking"] is False
    assert decision["consumer_task"] == "M0-02c"
    assert block["enters_required_chain_after_landing"] is False
    pins = block["observed_pins"]
    assert "ruff>=0.4.0" in (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert pins["pyproject_dev_extra"] == "ruff>=0.4.0"
    assert "rev: v0.4.0" in (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert pins["pre_commit_rev"] == "v0.4.0"
    assert pins["lint_baseline"] == _load(LINT_BASELINE)["ruff_version"]
    # If M0-02c ever lands, this test forces the flip rather than passing silently.
    if (REPO_ROOT / missing[0]).exists() or (REPO_ROOT / missing[1]).exists():
        assert decision["status"] == FROZEN


# ---------------------------------------------------------------------------
# 6. Registration is mechanically executable
# ---------------------------------------------------------------------------


def test_registration_decision_is_mechanically_executable(freeze: dict[str, Any]) -> None:
    """D-INT-08 gives the orchestrator an exact, self-consistent manifest edit."""
    decision = _decision(freeze, "D-INT-08")
    block = decision["registration"]
    assert decision["status"] == PENDING_IMPLEMENTATION
    assert block["target"] == "docs/reference/next-change/acp2-contracts/manifest.json"
    assert block["executor"].strip()
    entries = block["entries"]
    assert len(entries) == 2
    expected_ids = {"decision-freeze-identity", "decision-freeze-integration"}
    assert {entry["id"] for entry in entries} == expected_ids
    for entry in entries:
        # The manifest contract entry shape the registrant must write.
        assert set(entry) == {
            "id",
            "path",
            "status",
            "owner",
            "delivered_by",
            "digest_rule",
        }, entry
        assert entry["status"] == "delivered"
        assert entry["owner"] == "INT"
        assert entry["delivered_by"] == "M0-12"
        assert "sha256" in entry["digest_rule"]
        assert "sha256" not in entry, "a freeze may not embed its own digest"
        assert (REPO_ROOT / entry["path"]).is_file()
    assert {entry["path"] for entry in entries} == {
        validator.DEFAULT_FREEZE,
        validator.INTEGRATION_FREEZE,
    }
    assert "sha256" in block["digest_rule"]
    deltas = "\n".join(block["validator_deltas"])
    assert "DELIVERED_IDS_BY_STAGE" in deltas
    assert "owner enum" in deltas
    assert "contract_digest" in "\n".join(block["manifest_changes"])
    assert block["must_not"]

    manifest = _load(MANIFEST)
    registered = {entry["id"]: entry for entry in manifest["contracts"]}
    if block["executed"] is False:
        # The pending state must not lie: nothing is registered yet, and the two
        # validator deltas are still needed.
        assert expected_ids.isdisjoint(registered)
        assert "M0-12" not in contract_validator.DELIVERED_IDS_BY_STAGE
        for entry in entries:
            digest = _sha256(REPO_ROOT / entry["path"])
            assert re.fullmatch(r"[0-9a-f]{64}", digest)
    else:
        assert expected_ids <= set(registered)
        for entry in entries:
            live = registered[entry["id"]]
            assert live["path"] == entry["path"]
            assert live["status"] == entry["status"]
            assert live["owner"] == entry["owner"]
            assert live["delivered_by"] == entry["delivered_by"]
            assert live["sha256"] == _sha256(REPO_ROOT / entry["path"])


# ---------------------------------------------------------------------------
# 7. M1-01 consumes this list only; M1-02/M1-04 consume the identity list
# ---------------------------------------------------------------------------


def test_m1_01_consumes_only_the_integration_list(
    freeze: dict[str, Any], decisions: list[dict[str, Any]]
) -> None:
    """No two-way choice: the two freezes are disjoint by decision id."""
    contract = freeze["consumer_contract"]
    assert contract["M1-01"]["source"] == validator.INTEGRATION_FREEZE
    integration_ids = {decision["id"] for decision in decisions}
    consumed = set(contract["M1-01"]["decision_ids"])
    assert consumed <= integration_ids
    assert consumed == {"D-INT-01", "D-INT-02", "D-INT-03", "D-INT-04"}
    identity = _load(IDENTITY_FREEZE)
    identity_ids = {item["id"] for item in identity["decisions"]}
    assert consumed.isdisjoint(identity_ids)
    for task in ("M1-02", "M1-04"):
        spec = contract[task]
        assert spec["source"] == validator.DEFAULT_FREEZE
        # The identity list is read dynamically: no count/stat is pinned here.
        assert set(spec["decision_ids"]) <= identity_ids
    # Every machine-readable field really exists on the consumed decisions.
    by_id = {decision["id"]: decision for decision in decisions}
    for identifier in consumed:
        for field in contract["M1-01"]["machine_readable_fields"]:
            assert field in by_id[identifier], (identifier, field)


# ---------------------------------------------------------------------------
# 8. The validator itself: default coverage and negative cases
# ---------------------------------------------------------------------------


def test_default_validation_covers_both_freezes(capsys: pytest.CaptureFixture[str]) -> None:
    """No argument validates identity and integration; --file selects one."""
    # The identity document is owned by another lane; this test pins coverage,
    # not the identity verdict (tests/test_acp2_decision_freeze_identity.py owns it).
    assert validator.main([]) in {validator.EXIT_OK, validator.EXIT_INVALID}
    captured = capsys.readouterr()
    assert "identity.v1.json" in captured.out + captured.err
    assert "integration.v1.json" in captured.out + captured.err
    assert validator.main(["--file", str(FREEZE_PATH)]) == validator.EXIT_OK
    assert "integration.v1.json" in capsys.readouterr().out
    # A bare name resolves inside the decision-freeze directory.
    assert validator.main(["--file", "integration.v1.json"]) == validator.EXIT_OK
    assert "integration.v1.json" in capsys.readouterr().out
    assert validator.main(["--freeze", str(IDENTITY_FREEZE)]) != validator.EXIT_ERROR


def test_validator_rejects_a_phantom_wire_reference(tmp_path: Path) -> None:
    """A wire value that the contract does not have is INVALID."""
    doctored = _load(FREEZE_PATH)
    reference = next(
        item
        for decision in doctored["decisions"]
        if decision["id"] == "D-INT-02"
        for item in decision["wire_refs"]
        if item.get("pointer") == "/client_path/negotiation/request_carrier"
    )
    reference["equals"] = "initialize"
    path = tmp_path / "integration.v1.json"
    path.write_text(json.dumps(doctored), encoding="utf-8")
    errors = validator.validate(path, REPO_ROOT)
    assert any("frozen value" in error for error in errors), errors


def test_validator_rejects_an_unaccepted_adr_status(tmp_path: Path) -> None:
    """A non-Accepted ADR status line is INVALID (the freeze cites it)."""
    adr = tmp_path / validator.ADR_006
    adr.parent.mkdir(parents=True)
    original = ADR_006.read_text(encoding="utf-8")
    adr.write_text(
        original.replace("- **状态**：Accepted", "- **状态**：Superseded", 1),
        encoding="utf-8",
    )
    errors = validator._check_adr_status(tmp_path)
    assert any("must say Accepted" in error for error in errors), errors


def test_validator_rejects_a_pending_decision_marked_frozen(tmp_path: Path) -> None:
    """A pending decision relabelled frozen is INVALID (no silent promotion)."""
    doctored = _load(FREEZE_PATH)
    for decision in doctored["decisions"]:
        if decision["id"] == "D-INT-08":
            decision["status"] = FROZEN
    path = tmp_path / "integration.v1.json"
    path.write_text(json.dumps(doctored), encoding="utf-8")
    errors = validator.validate(path, REPO_ROOT)
    assert any("must not carry pending fields" in error for error in errors), errors
