"""B06-1 (todo 30) — ExecutionEnvelope/Receipt contract tests.

Consumes the frozen execution samples
(``tests/baseline/services-evidence/contract_samples/execution_*.json``) with the
real ``acp_services.contracts.envelope`` contracts and asserts:

* roundtrip serialization + frozen-dataclass equality;
* v1 legacy (``envelope_version=0``) migrates, v2 explicit (``envelope_version=1``)
  is accepted;
* TaskContext/runtime state is never serialized (negative);
* attempt protocol re-selection on resume is rejected (negative);
* unsupported entrypoints/capabilities fail explicitly;
* the module imports stdlib only (the ``acp_services → acp`` ban).

The samples are version-1 tracked deliverables — this test reads them, never
rewrites them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from acp_services.contracts.envelope import (
    CURRENT_ENVELOPE_VERSION,
    CURRENT_RECEIPT_VERSION,
    DEFAULT_ENTRYPOINT,
    ENTRYPOINT_WHITELIST,
    LEGACY_IMPLICIT_ENVELOPE_VERSION,
    RECEIPT_STATES,
    SUPPORTED_CAPABILITIES,
    SUPPORTED_EXECUTION_PROTOCOLS,
    AttemptProtocolError,
    EnvelopeSerializationError,
    EnvelopeVersionError,
    ExecutionEnvelope,
    ExecutionError,
    ExecutionReceipt,
    ResourceBudget,
    ScientificClosure,
    StorageBinding,
    TaskContextSerializationError,
    UnknownFieldError,
    UnsupportedCapabilityError,
    assert_resume_does_not_reselect,
    ensure_no_runtime_state,
    expected_submission_id,
    migrate_legacy_envelope,
)

_THIS = Path(__file__).resolve().parent
_ROOT = _THIS.parent
_SAMPLES = _THIS / "baseline" / "services-evidence" / "contract_samples"
_MODULE = _ROOT / "src" / "acp_services" / "contracts" / "envelope.py"

# Frozen §5.1 envelope fields + B06-1 additive fields.
_ENVELOPE_FIELDS = {
    "envelope_version",
    "job_id",
    "attempt",
    "submission_id",
    "release_id",
    "release_digest",
    "owner",
    "fencing_token",
    "plan_digest",
    "plan_identity",
    "step_identities",
    "protocol",
    "entrypoint",
    "required_capabilities",
    "data_bindings",
    "resource_budget",
    "scientific_closure",
}
# Frozen §5.2 receipt fields + B06-1 additive observation fields.
_RECEIPT_FIELDS = {
    "receipt_version",
    "job_id",
    "attempt",
    "submission_id",
    "state",
    "result_digest",
    "errors",
    "written_at",
    "release_id",
    "release_digest",
    "owner",
    "fencing_token",
    "plan_identity",
    "step_identities",
    "data_bindings",
    "evidence_digests",
}


def _sample(name: str) -> dict:
    return json.loads((_SAMPLES / f"execution_{name}.json").read_text(encoding="utf-8"))["body"]


def _make_envelope(**overrides: object) -> ExecutionEnvelope:
    kwargs: dict[str, object] = {
        "envelope_version": CURRENT_ENVELOPE_VERSION,
        "job_id": "job-1",
        "attempt": 1,
        "submission_id": expected_submission_id("job-1", 1),
        "release_id": "rel-1",
        "owner": "worker-1",
        "fencing_token": "fence-1",
        "plan_digest": "v2:plan",
        "plan_identity": "v2:plan",
        "step_identities": ("v2:step0",),
        "required_capabilities": ("optimize",),
        "resource_budget": ResourceBudget(
            nproc=4, mem="2G", mem_mb=2048, queue="normal", walltime_s=3600
        ),
        "scientific_closure": ScientificClosure("optimize", algorithm_version="v1"),
    }
    kwargs.update(overrides)
    return ExecutionEnvelope(**kwargs)  # type: ignore[arg-type]


def _make_receipt(**overrides: object) -> ExecutionReceipt:
    kwargs: dict[str, object] = {
        "receipt_version": CURRENT_RECEIPT_VERSION,
        "job_id": "job-1",
        "attempt": 1,
        "submission_id": expected_submission_id("job-1", 1),
        "state": "completed",
        "result_digest": "sha256:result",
        "errors": (),
        "written_at": "2026-10-18T00:00:00Z",
        "release_id": "rel-1",
        "owner": "worker-1",
        "fencing_token": "fence-1",
        "plan_identity": "v2:plan",
        "step_identities": ("v2:step0",),
        "evidence_digests": {"RESULT/result_manifest.json": "abc"},
    }
    kwargs.update(overrides)
    return ExecutionReceipt(**kwargs)  # type: ignore[arg-type]


# ── happy path: frozen + roundtrip ──────────────────────────────────────────


def test_envelope_roundtrip_and_frozen_equality() -> None:
    envelope = _make_envelope()
    payload = envelope.to_dict()
    assert set(payload) == _ENVELOPE_FIELDS
    decoded = ExecutionEnvelope.from_dict(payload)
    assert decoded == envelope
    assert decoded is not envelope
    # frozen dataclass: mutation raises
    with pytest.raises(Exception):
        envelope.job_id = "other"  # type: ignore[misc]


def test_receipt_roundtrip_and_frozen_equality() -> None:
    receipt = _make_receipt()
    payload = receipt.to_dict()
    assert set(payload) == _RECEIPT_FIELDS
    decoded = ExecutionReceipt.from_dict(payload)
    assert decoded == receipt


def test_schema_field_tables_match_contract_doc_shape() -> None:
    """Field-by-field assertion against the frozen §5.1/§5.2 whitelist."""
    envelope = _make_envelope()
    receipt = _make_receipt()
    assert set(envelope.to_dict()) == _ENVELOPE_FIELDS
    assert set(receipt.to_dict()) == _RECEIPT_FIELDS
    # Frozen §5.2 required fields are present in the receipt.
    assert {
        "receipt_version",
        "job_id",
        "attempt",
        "submission_id",
        "state",
        "result_digest",
        "errors",
        "written_at",
    } <= _RECEIPT_FIELDS
    assert envelope.schema_version == envelope.envelope_version
    assert receipt.schema_version == receipt.receipt_version


def test_storage_binding_and_budget_carry_digests() -> None:
    binding = StorageBinding(
        binding_id="sb_1",
        tenant_id="t1",
        project_id="p1",
        endpoint="lsf",
        root="/scratch/acp",
        relative_path="proj/task",
        mapping_version=2,
        artifact_digests={"input.xyz": "deadbeef"},
    )
    envelope = _make_envelope(data_bindings=(binding,))
    decoded = ExecutionEnvelope.from_dict(envelope.to_dict())
    assert decoded == envelope
    assert decoded.data_bindings[0].artifact_digests == {"input.xyz": "deadbeef"}
    assert decoded.resource_budget.nproc == 4
    assert decoded.resource_budget.walltime_s == 3600


# ── version compatibility: v1 legacy implicit / v2 explicit ─────────────────


def test_v2_explicit_sample_accepted() -> None:
    body = _sample("normal")
    envelope = ExecutionEnvelope.from_dict(body["envelope"])
    receipt = ExecutionReceipt.from_dict(body["receipt"])
    assert envelope.envelope_version == CURRENT_ENVELOPE_VERSION
    assert envelope.migrated_from_legacy is False
    assert envelope.attempt == 1
    assert envelope.submission_id == "sub_abc"
    assert envelope.resource_budget.nproc == 4
    # additive fields default cleanly when the frozen sample omits them
    assert envelope.protocol is None
    assert envelope.entrypoint == DEFAULT_ENTRYPOINT
    assert envelope.step_identities == ()
    assert envelope.release_digest == ""
    assert receipt.receipt_version == CURRENT_RECEIPT_VERSION
    assert receipt.state in RECEIPT_STATES
    assert receipt.errors == ()

    envelope_old = _sample("old-version")["envelope"]
    assert envelope_old["envelope_version"] == LEGACY_IMPLICIT_ENVELOPE_VERSION


def test_v1_legacy_sample_migrated() -> None:
    body = _sample("old-version")
    envelope = ExecutionEnvelope.from_dict(body["envelope"])
    assert envelope.migrated_from_legacy is True
    assert envelope.envelope_version == CURRENT_ENVELOPE_VERSION
    assert envelope.job_id == "j1"
    assert envelope.attempt == 1
    # receipt in the legacy sample is already explicit (version 1)
    receipt = ExecutionReceipt.from_dict(body["receipt"])
    assert receipt.receipt_version == CURRENT_RECEIPT_VERSION


def test_legacy_camelcase_aliases_migrate() -> None:
    legacy = {
        "envelopeVersion": 0,
        "jobId": "j2",
        "attempt": 2,
        "submissionId": "sub_x",
        "releaseId": "rel_x",
        "owner": "w",
        "fencingToken": "fk",
        "planDigest": "pd",
        "planIdentity": "pi",
        "dataBindings": [],
        "resourceBudget": {"cores": 8, "memory_gb": 4},
        "scientificClosure": {"task_kind": "frequency"},
    }
    migrated = migrate_legacy_envelope(legacy)
    assert migrated["envelope_version"] == CURRENT_ENVELOPE_VERSION
    assert migrated["submission_id"] == "sub_x"
    assert migrated["resource_budget"]["cores"] == 8

    envelope = ExecutionEnvelope.from_dict(legacy)
    assert envelope.migrated_from_legacy is True
    assert envelope.job_id == "j2"
    assert envelope.attempt == 2
    # cores/memory_gb alias folding
    assert envelope.resource_budget.nproc == 8
    assert envelope.resource_budget.mem_mb == 4096


def test_unsupported_future_version_rejected() -> None:
    body = _sample("normal")
    bad = dict(body["envelope"])
    bad["envelope_version"] = CURRENT_ENVELOPE_VERSION + 1
    with pytest.raises(EnvelopeVersionError):
        ExecutionEnvelope.from_dict(bad)


def test_frozen_empty_and_partial_samples() -> None:
    empty = _sample("empty")
    receipt = ExecutionReceipt.from_dict(empty["receipt"])
    assert receipt.result_digest == ""
    assert receipt.errors == ()

    partial = _sample("partial")
    envelope = ExecutionEnvelope.from_dict(partial["envelope"])
    assert envelope.resource_budget.partial is True


def test_corrupt_sample_rejected() -> None:
    body = _sample("corrupt")
    with pytest.raises(TaskContextSerializationError):
        ExecutionEnvelope.from_dict(body["envelope"])


# ── negatives: runtime state / unknown fields ───────────────────────────────


def test_task_context_is_never_serializable() -> None:
    from cccp.calculation.context import TaskContext

    ctx = TaskContext(cancelled=lambda: False)
    with pytest.raises(TaskContextSerializationError):
        ensure_no_runtime_state(ctx)
    with pytest.raises(TaskContextSerializationError):
        ensure_no_runtime_state({"safe": 1, "ctx": ctx})


def test_callback_field_on_envelope_is_rejected() -> None:
    body = _sample("normal")["envelope"]
    injected = dict(body)
    injected["progress_callback"] = lambda: None
    with pytest.raises(TaskContextSerializationError):
        ExecutionEnvelope.from_dict(injected)


def test_callable_in_known_field_is_rejected() -> None:
    body = _sample("normal")["envelope"]
    injected = dict(body)
    injected["job_id"] = lambda: None  # a callable where a str is required
    with pytest.raises(TaskContextSerializationError):
        ExecutionEnvelope.from_dict(injected)


def test_to_dict_rejects_runtime_state_injected_past_validation() -> None:
    envelope = _make_envelope()
    object.__setattr__(envelope, "owner", lambda: None)
    with pytest.raises(TaskContextSerializationError):
        envelope.to_dict()


def test_unknown_envelope_field_rejected() -> None:
    body = _sample("normal")["envelope"]
    injected = dict(body)
    injected["browser_tab_id"] = "tab-7"  # UI identity is not allowed either
    with pytest.raises(UnknownFieldError):
        ExecutionEnvelope.from_dict(injected)


def test_unknown_receipt_field_rejected() -> None:
    body = _sample("normal")["receipt"]
    injected = dict(body)
    injected["task_display_name"] = "nice name"
    with pytest.raises(UnknownFieldError):
        ExecutionReceipt.from_dict(injected)


def test_no_ui_identity_fields_in_contract() -> None:
    forbidden_markers = ("custom_name", "organization", "display_name", "browser", "tab_id")
    assert not any(marker in name for name in _ENVELOPE_FIELDS for marker in forbidden_markers)
    assert not any(marker in name for name in _RECEIPT_FIELDS for marker in forbidden_markers)


# ── negatives: attempt protocol freeze ──────────────────────────────────────


def test_protocol_frozen_per_attempt_and_reselection_rejected() -> None:
    envelope = _make_envelope(protocol=None)
    frozen = envelope.select_protocol("lsf", release_id="rel-9", release_digest="digest-9")
    assert frozen.protocol == "lsf"
    assert frozen.release_id == "rel-9"
    assert frozen.release_digest == "digest-9"

    # re-selection on the SAME attempt (resume) is rejected
    with pytest.raises(AttemptProtocolError):
        frozen.select_protocol("local", release_id="rel-10")
    with pytest.raises(AttemptProtocolError):
        assert_resume_does_not_reselect(frozen, attempt=1, protocol="local")

    # a new attempt may select anew
    advanced = frozen.advance_attempt(2, submission_id=expected_submission_id("job-1", 2))
    assert advanced.attempt == 2
    assert advanced.protocol is None
    re_frozen = advanced.select_protocol("local", release_id="rel-11")
    assert re_frozen.protocol == "local"


def test_attempt_must_advance_exactly_one_step() -> None:
    envelope = _make_envelope(attempt=3)
    with pytest.raises(AttemptProtocolError):
        envelope.advance_attempt(5)
    with pytest.raises(AttemptProtocolError):
        envelope.advance_attempt(3)


def test_attempt_below_one_rejected() -> None:
    with pytest.raises(EnvelopeSerializationError):
        _make_envelope(attempt=0)


# ── negatives: entrypoint whitelist / unsupported capabilities ──────────────


def test_unsupported_entrypoint_rejected() -> None:
    assert "worker.execute" in ENTRYPOINT_WHITELIST
    with pytest.raises(UnsupportedCapabilityError):
        _make_envelope(entrypoint="arbitrary.shell")


def test_unsupported_capability_rejected() -> None:
    assert "optimize" in SUPPORTED_CAPABILITIES
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        _make_envelope(required_capabilities=("optimize", "quantum-teleport"))
    assert "quantum-teleport" in excinfo.value.unsupported


def test_unsupported_protocol_rejected() -> None:
    assert "lsf" in SUPPORTED_EXECUTION_PROTOCOLS
    with pytest.raises(UnsupportedCapabilityError):
        _make_envelope().select_protocol("teleport", release_id="r")


# ── receipt errors: typed + legacy string ───────────────────────────────────


def test_receipt_typed_errors_roundtrip() -> None:
    receipt = _make_receipt(
        state="failed",
        result_digest="",
        errors=(
            ExecutionError(code="qc_failed", message="ORCA vanished", retryable=True),
            "legacy-string-error",
        ),
    )
    payload = receipt.to_dict()
    assert payload["errors"][0] == {
        "code": "qc_failed",
        "message": "ORCA vanished",
        "retryable": True,
    }
    assert payload["errors"][1] == "legacy-string-error"
    decoded = ExecutionReceipt.from_dict(payload)
    assert decoded == receipt
    assert decoded.errors[0].retryable is True
    assert decoded.errors[1] == "legacy-string-error"


def test_receipt_invalid_state_rejected() -> None:
    with pytest.raises(EnvelopeSerializationError):
        _make_receipt(state="finished")


def test_receipt_forbidden_field_rejected() -> None:
    body = _sample("normal")["receipt"]
    injected = dict(body)
    injected["task_context"] = {"workdir": "/tmp"}
    with pytest.raises(TaskContextSerializationError):
        ExecutionReceipt.from_dict(injected)


# ── dependency-direction guard ──────────────────────────────────────────────


def test_module_does_not_import_acp() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    pattern = re.compile(r"^\s*(?:from|import)\s+acp(?:\.|\s|$)", re.MULTILINE)
    assert not pattern.search(source), "src/acp_services must never import acp"
    # only stdlib imports
    imports = re.findall(r"^\s*(?:from|import)\s+([A-Za-z0-9_]+)", source, re.MULTILINE)
    assert set(imports) <= {
        "__future__",
        "collections",
        "dataclasses",
        "hashlib",
        "json",
        "typing",
    }, f"unexpected imports in envelope.py: {sorted(set(imports))}"


def test_submission_id_matches_frozen_derivation() -> None:
    # sub_ + sha256(f"{job_id}:{attempt}")[:16]  (remote/submission.py:56-64)
    assert expected_submission_id("j1", 1) == "sub_3054d9def37091fd"
    assert expected_submission_id("j1", None) == expected_submission_id("j1", 1)
    assert expected_submission_id("job-1", 2).startswith("sub_")
    assert len(expected_submission_id("job-1", 2)) == len("sub_") + 16
