"""M1-04a: D06 digest domains, approval dual binding and typed science inputs.

Three independent checkpoints are covered here:

1. the digest-domain registry is complete and only marks a domain ``frozen``
   when a validator is actually registered (``recipe_definition_digest`` is the
   honest ``not-frozen`` row);
2. ``experience/plan-identity.md`` carries one contract row per registered
   domain with the same owner/version/validator (documentation cannot drift);
3. an approval binds the scientific digest **and** the execution digest, and a
   missing critical scientific value is a clarification, never ``0``/``1``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from acp_services.runtime.approvals import (
    ApprovalConflictError,
    ApprovalService,
)
from acp_services.runtime.canonical_digest import (
    frozen_plan_definition,
    plan_digest,
    proposal_plan_definition_digest,
)
from acp_services.runtime.digest_domains import (
    DIGEST_DOMAINS,
    domain_row,
    registry_rows,
    validate_digest_domain_registry,
)
from acp_services.runtime.science_inputs import (
    SCIENCE_INPUT_FIELDS,
    ScienceInputError,
    normalize_science_inputs,
    require_complete_science_inputs,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLAN_IDENTITY = (
    _REPO_ROOT / "docs/reference/next-change/acp2-contracts/experience/plan-identity.md"
)


def _proposal() -> dict:
    return {
        "proposal_id": "prop-m1-04a",
        "project_id": "project_alpha",
        "recipe_digest": "sha256:" + "1" * 64,
        "science": {
            "capability_steps": [
                {"step_index": 0, "capability": "geometry_optimization", "version": "1.0.0"}
            ],
            "initial_inputs": [
                {"input_id": "in-1", "sha256": "sha256:" + "2" * 64, "format": "xyz"}
            ],
            "method": "r2SCAN-3c",
            "mapping": "identity",
            "charge": -1,
            "multiplicity": 2,
            "allowed_substitutions": [],
        },
        "budget": {
            "model_requests": 1,
            "input_tokens": 1000,
            "output_tokens": 500,
            "task_refs": 1,
            "recipe_steps": 1,
            "qc_invocations": 1,
            "wall_seconds": 600,
            "queue_seconds": 60,
        },
    }


# ---------------------------------------------------------------------------
# 1. registry
# ---------------------------------------------------------------------------


def test_registry_is_valid_and_complete() -> None:
    assert validate_digest_domain_registry(_REPO_ROOT) == []
    names = [row.domain for row in DIGEST_DOMAINS]
    assert len(names) == len(set(names)) >= 9
    # D06 § 1-8: every identity that was historically called "the digest".
    for required in (
        "plan_definition_digest",
        "execution_plan_digest",
        "proposal_digest",
        "submit_request_digest",
        "frozen_plan_body_digest",
        "recipe_definition_digest",
        "step_execution_identity",
    ):
        assert required in names, required


def test_unknown_validator_domain_is_not_frozen() -> None:
    recipe = domain_row("recipe_definition_digest")
    assert recipe.frozen is False
    assert recipe.validator_path == ""
    assert recipe.notes, "a not-frozen domain must document its gap"


def test_frozen_domains_have_an_ordered_field_list_and_validator() -> None:
    for row in DIGEST_DOMAINS:
        if not row.frozen:
            continue
        assert row.canonical_inputs, row.domain
        assert row.version.strip(), row.domain
        assert row.validator_path and row.validator_symbol, row.domain
        assert (_REPO_ROOT / row.validator_path).is_file(), row.domain
        assert row.validator_side in {"platform", "cccp", "rph"}, row.domain


def test_legacy_wire_fields_map_to_distinct_domains() -> None:
    science = domain_row("plan_definition_digest")
    execution = domain_row("execution_plan_digest")
    # The pre-D06 wire field is documented on both rows, with different targets:
    # one name may never silently mean two digests at the same time.
    assert science.legacy_targets()["plan_digest"] == "plan_definition_digest"
    assert execution.legacy_targets()["plan_digest"] == "execution_plan_digest"
    assert science.domain != execution.domain


def test_registry_rejects_a_frozen_domain_without_validator() -> None:
    from dataclasses import replace

    import acp_services.runtime.digest_domains as module

    broken = replace(domain_row("execution_plan_digest"), validator_path="", validator_symbol="")
    patched = tuple(
        broken if row.domain == "execution_plan_digest" else row for row in module.DIGEST_DOMAINS
    )
    original = module.DIGEST_DOMAINS
    module.DIGEST_DOMAINS = patched
    try:
        violations = module.validate_digest_domain_registry(_REPO_ROOT)
    finally:
        module.DIGEST_DOMAINS = original
    assert any("frozen domain needs a validator_path" in item for item in violations), violations


def test_registry_rows_are_json_ready() -> None:
    rows = registry_rows()
    assert [row["domain"] for row in rows] == [row.domain for row in DIGEST_DOMAINS]
    for row in rows:
        assert isinstance(row["canonical_inputs"], list)
        assert row["field_order"].startswith("canonical_inputs")


# ---------------------------------------------------------------------------
# 2. the document and the registry agree
# ---------------------------------------------------------------------------


def _document_rows() -> dict[str, dict[str, str]]:
    text = _PLAN_IDENTITY.read_text(encoding="utf-8")
    section = text.split("## 6.", 1)
    assert len(section) == 2, "plan-identity.md must carry the D06 registry section"
    body = section[1]
    rows: dict[str, dict[str, str]] = {}
    for line in body.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        name = cells[0].strip("`")
        rows[name] = {
            "owner": cells[1],
            "version": cells[2],
            "validator": cells[3].strip("`"),
            "side": cells[4],
            "status": cells[5],
        }
    return rows


def test_plan_identity_documents_every_registered_domain() -> None:
    documented = _document_rows()
    assert documented, "no D06 registry rows parsed from plan-identity.md"
    for row in DIGEST_DOMAINS:
        assert row.domain in documented, row.domain
        entry = documented[row.domain]
        assert row.validator_side in entry["side"], (row.domain, entry)
        assert row.version in entry["version"], (row.domain, entry)
        if row.frozen:
            assert entry["status"].startswith("frozen"), (row.domain, entry)
            assert row.validator_path in entry["validator"], (row.domain, entry)
        else:
            assert "not-frozen" in entry["status"], (row.domain, entry)
    assert set(documented) == {row.domain for row in DIGEST_DOMAINS}


# ---------------------------------------------------------------------------
# 3. approval dual binding
# ---------------------------------------------------------------------------


def test_approval_binds_science_and_execution_digest(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    proposal = _proposal()
    science_digest = proposal_plan_definition_digest(proposal).digest
    execution_digest = plan_digest(
        frozen_plan_definition(
            workflow="optimize",
            input={"source": "CCO"},
            method={"method": "r2SCAN-3c"},
        )
    ).digest
    approval, replayed = service.request(
        proposal_ref=proposal["proposal_id"],
        proposal_digest="sha256:" + "a" * 64,
        plan_digest=execution_digest,
        project_id="project_alpha",
        requester_principal="principal_alpha",
        science_digest=science_digest,
    )
    assert replayed is False
    assert approval.science_digest == science_digest
    assert approval.binding_status == "full"
    binding = approval.binding()
    assert binding["science_digest"] == science_digest
    assert binding["plan_digest"] == execution_digest
    assert binding["binding_status"] == "full"
    # Both digests survive a restart (persisted, not in-memory).
    reloaded = service.get(approval.approval_id)
    assert reloaded is not None
    assert reloaded.science_digest == science_digest
    assert reloaded.binding()["binding_status"] == "full"


def test_approval_without_proposal_is_execution_only(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    approval, _ = service.request(
        proposal_ref="prop-no-body",
        proposal_digest="sha256:" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        project_id="project_alpha",
        requester_principal="principal_alpha",
    )
    assert approval.science_digest == ""
    assert approval.binding_status == "execution-only"
    assert approval.to_dict()["binding_status"] == "execution-only"


def test_changed_science_digest_conflicts_with_the_live_approval(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    service.request(
        proposal_ref="prop-science-change",
        proposal_digest="sha256:" + "a" * 64,
        plan_digest="sha256:" + "b" * 64,
        project_id="project_alpha",
        requester_principal="principal_alpha",
        science_digest="sha256:" + "c" * 64,
    )
    with pytest.raises(ApprovalConflictError) as excinfo:
        service.request(
            proposal_ref="prop-science-change",
            proposal_digest="sha256:" + "a" * 64,
            plan_digest="sha256:" + "b" * 64,
            project_id="project_alpha",
            requester_principal="principal_alpha",
            science_digest="sha256:" + "d" * 64,
        )
    assert excinfo.value.rule == "approval_binding_mismatch"


def test_science_digest_must_be_a_canonical_digest(tmp_path: Path) -> None:
    service = ApprovalService(tmp_path / "jobs.db")
    with pytest.raises(Exception) as excinfo:
        service.request(
            proposal_ref="prop-bad-digest",
            proposal_digest="sha256:" + "a" * 64,
            plan_digest="sha256:" + "b" * 64,
            project_id="project_alpha",
            requester_principal="principal_alpha",
            science_digest="not-a-digest",
        )
    assert "science_digest" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 4. typed science inputs
# ---------------------------------------------------------------------------


def test_missing_charge_and_multiplicity_is_a_clarification_not_a_default() -> None:
    verdict = normalize_science_inputs({"method": "r2SCAN-3c"})
    assert verdict.complete is False
    assert verdict.clarification_required is True
    missing = [item.field_name for item in verdict.clarifications]
    assert missing == ["charge", "multiplicity"]
    assert verdict.values is None
    with pytest.raises(ScienceInputError) as excinfo:
        require_complete_science_inputs({"method": "r2SCAN-3c"})
    assert excinfo.value.rule == "clarification_required"


def test_explicit_null_is_a_clarification_not_zero() -> None:
    verdict = normalize_science_inputs({"charge": None, "multiplicity": 1})
    assert verdict.values is None
    assert [item.field_name for item in verdict.clarifications] == ["charge"]


def test_charged_and_open_shell_inputs_are_preserved() -> None:
    verdict = normalize_science_inputs(
        {
            "method": "r2SCAN-3c",
            "mapping": "identity",
            "charge": -1,
            "multiplicity": 2,
            "electronic_state": {"state_label": "doublet", "spin_polarization": 1},
            "structure_provenance": {
                "input_id": "in-anion",
                "sha256": "sha256:" + "3" * 64,
                "format": "xyz",
                "role": "minimum",
            },
        }
    )
    assert verdict.complete is True
    values = verdict.values
    assert values is not None
    assert values.charge == -1
    assert values.multiplicity == 2
    assert values.electronic_state is not None
    assert values.electronic_state.open_shell is True
    assert values.to_dict()["charge"] == -1
    assert values.to_dict()["multiplicity"] == 2


def test_electron_parity_rejects_an_impossible_state() -> None:
    # 10 electrons are an even count: an odd multiplicity is legal (triplet),
    # an even one is not (a doublet with 10 electrons cannot exist closed-form).
    triplet = normalize_science_inputs({"charge": 0, "multiplicity": 3, "electron_count": 10})
    assert triplet.complete is True
    assert triplet.electron_parity_checked is True
    doublet = normalize_science_inputs({"charge": 0, "multiplicity": 2, "electron_count": 10})
    assert doublet.complete is False
    assert [issue.rule for issue in doublet.issues] == ["spin_multiplicity_mismatch"]
    # 10 electrons minus charge +1 = 9 electrons: an even multiplicity is legal.
    cation = normalize_science_inputs({"charge": 1, "multiplicity": 2, "electron_count": 10})
    assert cation.complete is True


def test_structure_without_provenance_is_a_clarification() -> None:
    verdict = normalize_science_inputs(
        {"charge": 0, "multiplicity": 1, "structure": {"path": "inputs/a.xyz"}}
    )
    assert verdict.complete is False
    assert [item.field_name for item in verdict.clarifications] == ["structure_provenance"]


def test_constraints_are_typed_and_forwarded_verbatim() -> None:
    verdict = normalize_science_inputs(
        {
            "charge": 0,
            "multiplicity": 1,
            "constraints": [
                {"kind": "bond", "atoms": [0, 1], "target": "1.4", "unit": "angstrom"},
                {"kind": "dihedral", "atoms": [0, 1, 2, 3], "target": "180"},
            ],
        }
    )
    assert verdict.complete is True
    assert verdict.values is not None
    forwarded = verdict.values.to_dict()["constraints"]
    assert forwarded[0] == {
        "kind": "bond",
        "atoms": [0, 1],
        "target": "1.4",
        "unit": "angstrom",
    }
    assert forwarded[1]["atoms"] == [0, 1, 2, 3]


def test_unknown_science_field_is_rejected() -> None:
    with pytest.raises(ScienceInputError) as excinfo:
        normalize_science_inputs({"charge": 0, "multiplicity": 1, "spin": 1})
    assert excinfo.value.rule == "unknown_science_field"


def test_canonical_field_order_is_frozen() -> None:
    assert SCIENCE_INPUT_FIELDS == (
        "method",
        "mapping",
        "charge",
        "multiplicity",
        "electronic_state",
        "structure_provenance",
        "constraints",
    )
    values = require_complete_science_inputs({"charge": 2, "multiplicity": 1})
    assert list(values.to_dict()) == ["charge", "multiplicity"]
