"""M3-03: the Controller-side model gateway (planning budget + trusted usage).

The gateway is the production seam M1-04c left for it: before the first
``understand``/``hypothesize``/``critique``/``plan`` call it reserves a planning
``model_request`` on the one admission ledger, reuses the M1-02 claim/receipt
identity (never a job/attempt), and records a trusted receipt after the call.

These tests prove the accounting semantics with a controlled fake transport:

* an empty research (no proposal, no TaskRef) may spend inside its allowance;
* an exhausted allowance refuses **before** the provider is called;
* a retry replays the stored receipt and never double-charges;
* an unknown/interrupted/streamed call keeps the charge and does not reset it;
* a restart reads the durable ledger instead of process memory;
* concurrent calls cannot overspend the same planning book;
* the provider credential never leaves the gateway (no receipt/dict/log field).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from acp_services.runtime.admission import ReservationLedger
from acp_services.runtime.model_gateway import (
    MODEL_GATEWAY_SCHEMA,
    ModelBudgetDeniedError,
    ModelCallUnknownError,
    ModelCredentialUnavailableError,
    ModelGateway,
    ModelGatewayConflictError,
    ModelGatewayTransportFailureError,
    ModelGatewayValidationError,
    ModelPlanningRequest,
    ModelTransportError,
    ModelTransportResponse,
    resolve_model_credential,
)
from acp_services.runtime.research_session import ResearchIdentity
from acp_services.runtime.usage_units import UsageUnitCounts
from acp_services.workspace.command_identity import CommandIdentity, SubjectKind

_ENV = {"ACP2_MODEL_API_KEY": "unit-test-secret-key", "ACP2_MODEL_ID": "approved-model"}


class RecordingTransport:
    """A controlled provider: records every call, returns a fixed response."""

    def __init__(self, response: ModelTransportResponse | None = None) -> None:
        self.calls: list[Any] = []
        self.response = response or ModelTransportResponse(
            text='{"status": "candidate"}',
            usage={"prompt_tokens": 11, "completion_tokens": 7},
        )

    def call(self, request: Any) -> ModelTransportResponse:
        self.calls.append(request)
        return self.response


class FailingTransport:
    def __init__(self, error: ModelTransportError) -> None:
        self.calls: list[Any] = []
        self.error = error

    def call(self, request: Any) -> ModelTransportResponse:
        self.calls.append(request)
        raise self.error


def _identity(seed: str = "m3-03") -> ResearchIdentity:
    return ResearchIdentity.allocate(
        CommandIdentity(principal_id="principal_m3_03", subject_kind=SubjectKind.USER),
        session_seed=seed,
    )


def _ledger(tmp_path: Path) -> ReservationLedger:
    return ReservationLedger(str(tmp_path / "admission.db"))


def _gateway(
    tmp_path: Path,
    transport: Any,
    *,
    limit: int = 1,
    ledger: ReservationLedger | None = None,
    environ: dict[str, str] | None = None,
) -> ModelGateway:
    return ModelGateway(
        ledger or _ledger(tmp_path),
        transport=transport,
        receipt_root=tmp_path / "model-receipts",
        model_request_limit=limit,
        environ=_ENV if environ is None else environ,
        clock=lambda: "2026-10-27T00:00:00+00:00",
    )


def _request(
    *, round_no: int = 0, call_seq: int = 1, phase: str = "plan", **overrides: Any
) -> ModelPlanningRequest:
    payload: dict[str, Any] = {
        "phase": phase,
        "round": round_no,
        "call_seq": call_seq,
        "messages": ({"role": "user", "content": "materialize the plan"},),
    }
    payload.update(overrides)
    return ModelPlanningRequest(**payload)


# ---------------------------------------------------------------------------
# reserve before the first call, no fabricated execution fact
# ---------------------------------------------------------------------------


def test_first_call_reserves_before_the_provider_without_a_task_ref(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport)
    identity = _identity()

    result = gateway.call(identity, _request())

    assert len(transport.calls) == 1
    assert result.replayed is False
    assert result.receipt.trusted_source == "controller-terminal-receipt"
    assert result.receipt.receipt_id.startswith("modelrcpt|")
    assert result.receipt.claim_id.startswith("plan|")
    # A planning call is never a job/attempt.
    assert result.receipt.job_id == ""
    assert result.receipt.attempt == 0
    assert result.reservation.job_id == ""
    assert result.reservation.attempt == 0
    assert result.reservation.budget_id.startswith("rb_")
    # The usage is the provider's reported one, not a process counter.
    assert result.receipt.usage.to_dict()["input_tokens"] == 11
    assert result.receipt.usage.to_dict()["output_tokens"] == 7
    assert result.receipt.usage.complete is True

    usage = gateway.planning_usage(identity)
    assert usage["model_requests"] == 1
    assert usage["settled"] == 1
    assert usage["reserved"] == 0
    assert usage["remaining"] == 0
    assert usage["receipts"] == 1


def test_empty_research_has_no_proposal_and_no_task_ref(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, RecordingTransport(), limit=2)
    identity = _identity("empty-research")

    result = gateway.call(identity, _request(phase="understand"))
    payload = result.to_dict()

    assert payload["reservation"]["is_execution_fact"] is False
    assert payload["receipt"]["is_execution_fact"] is False
    assert "proposal" not in json.dumps(payload).lower()
    # The planning budget is research/session scoped, not proposal scoped.
    assert "planning:" in result.reservation.budget_id or result.reservation.budget_id.startswith(
        "rb_"
    )


def test_every_planning_phase_reserves_before_the_provider(tmp_path: Path) -> None:
    from acp_services.runtime.model_gateway import PLANNING_MODEL_PHASES

    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, limit=len(PLANNING_MODEL_PHASES))
    identity = _identity("all-phases")

    for sequence, phase in enumerate(PLANNING_MODEL_PHASES, start=1):
        result = gateway.call(identity, _request(phase=phase, call_seq=sequence))
        assert result.receipt.phase == phase
        assert result.receipt.call_seq == sequence

    assert len(transport.calls) == len(PLANNING_MODEL_PHASES)
    assert gateway.planning_usage(identity)["model_requests"] == len(PLANNING_MODEL_PHASES)


# ---------------------------------------------------------------------------
# quota: refusal happens before any provider call
# ---------------------------------------------------------------------------


def test_exhausted_allowance_makes_zero_provider_calls(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, limit=1)
    identity = _identity("denied")

    gateway.call(identity, _request(call_seq=1))
    assert len(transport.calls) == 1

    with pytest.raises(ModelBudgetDeniedError) as excinfo:
        gateway.call(identity, _request(call_seq=2))

    assert excinfo.value.rule == "planning_model_request_exhausted"
    assert len(transport.calls) == 1  # zero calls for the refused request
    assert gateway.planning_usage(identity)["model_requests"] == 1


def test_missing_credential_is_refused_before_reservation_and_calls(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, environ={})
    identity = _identity("no-credential")

    with pytest.raises(ModelCredentialUnavailableError) as excinfo:
        gateway.call(identity, _request())

    assert excinfo.value.rule == "no_model_credential"
    assert len(transport.calls) == 0
    assert gateway.planning_usage(identity)["model_requests"] == 0
    assert list((tmp_path / "model-receipts").glob("claim-*.json")) == []


def test_unapproved_model_is_refused_before_reservation(tmp_path: Path) -> None:
    gateway = _gateway(tmp_path, RecordingTransport())
    identity = _identity("wrong-model")

    with pytest.raises(ModelGatewayValidationError) as excinfo:
        gateway.call(identity, _request(model="another-model"))

    assert excinfo.value.rule == "model_not_approved"
    assert gateway.planning_usage(identity)["model_requests"] == 0


# ---------------------------------------------------------------------------
# retry / unknown / interruption
# ---------------------------------------------------------------------------


def test_retry_replays_the_receipt_without_a_second_provider_call(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport)
    identity = _identity("retry")

    first = gateway.call(identity, _request())
    replay = gateway.call(identity, _request())

    assert replay.replayed is True
    assert replay.receipt.receipt_id == first.receipt.receipt_id
    assert len(transport.calls) == 1
    assert gateway.planning_usage(identity)["model_requests"] == 1


def test_unknown_outcome_stays_charged_and_never_resets(tmp_path: Path) -> None:
    transport = FailingTransport(ModelTransportError("upstream_error", "connection refused"))
    gateway = _gateway(tmp_path, transport, limit=1)
    identity = _identity("unknown")

    with pytest.raises(ModelGatewayTransportFailureError) as excinfo:
        gateway.call(identity, _request())

    failure = excinfo.value
    assert failure.result.receipt.outcome == "upstream_error"
    assert failure.result.receipt.usage.complete is False
    assert gateway.planning_usage(identity)["model_requests"] == 1

    # Same claim: the stored unknown receipt is replayed, never re-called.
    replay = gateway.call(identity, _request())
    assert replay.replayed is True
    assert replay.receipt.outcome == "upstream_error"
    assert len(transport.calls) == 1

    # A fresh call needs a new call_seq and a fresh allowance; the old charge
    # is never released to make room.
    with pytest.raises(ModelBudgetDeniedError):
        gateway.call(identity, _request(call_seq=2))
    assert len(transport.calls) == 1


def test_lost_receipt_keeps_the_claim_unknown_and_charged(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, limit=1)
    identity = _identity("lost-receipt")
    result = gateway.call(identity, _request())

    # Simulate a crash after settlement but before the receipt reached disk.
    for path in (tmp_path / "model-receipts").glob("receipt-*.json"):
        path.unlink()

    with pytest.raises(ModelCallUnknownError) as excinfo:
        gateway.call(identity, _request())
    assert excinfo.value.rule == "receipt_missing"
    assert len(transport.calls) == 1
    assert gateway.planning_usage(identity)["model_requests"] == 1
    assert result.receipt.receipt_id.startswith("modelrcpt|")


def test_interrupted_stream_records_partial_usage_and_charges_once(tmp_path: Path) -> None:
    transport = FailingTransport(
        ModelTransportError(
            "upstream_interrupted",
            "stream ended before [DONE]",
            usage={"prompt_tokens": 40, "completion_tokens": 9},
        )
    )
    gateway = _gateway(tmp_path, transport, limit=1)
    identity = _identity("interrupted")

    with pytest.raises(ModelGatewayTransportFailureError) as excinfo:
        gateway.call(identity, _request())

    receipt = excinfo.value.result.receipt
    assert receipt.outcome == "upstream_interrupted"
    assert receipt.usage.input_tokens == 40
    assert receipt.usage.output_tokens == 9
    assert gateway.planning_usage(identity)["model_requests"] == 1
    assert len(transport.calls) == 1


def test_claim_reused_with_a_different_request_is_a_typed_conflict(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, limit=2)
    identity = _identity("changed-request")

    gateway.call(identity, _request())
    changed = ModelPlanningRequest(
        phase="plan",
        round=0,
        call_seq=1,
        messages=({"role": "user", "content": "a different question"},),
    )
    with pytest.raises(ModelGatewayConflictError) as excinfo:
        gateway.call(identity, changed)
    assert excinfo.value.rule == "claim_request_changed"
    assert len(transport.calls) == 1


# ---------------------------------------------------------------------------
# restart / concurrency
# ---------------------------------------------------------------------------


def test_restart_reads_the_durable_allowance_not_process_memory(tmp_path: Path) -> None:
    identity = _identity("restart")
    first_transport = RecordingTransport()
    first = _gateway(tmp_path, first_transport, limit=1)
    first.call(identity, _request())

    # A new process builds a new gateway (and a new transport) on the same
    # ledger; the allowance is not reset.
    restarted_transport = RecordingTransport()
    restarted = _gateway(
        tmp_path,
        restarted_transport,
        limit=1,
        ledger=ReservationLedger(str(tmp_path / "admission.db")),
    )
    with pytest.raises(ModelBudgetDeniedError):
        restarted.call(identity, _request(call_seq=2))
    assert len(restarted_transport.calls) == 0
    assert restarted.planning_usage(identity)["model_requests"] == 1


def test_concurrent_distinct_claims_cannot_overspend(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, limit=1)
    identity = _identity("concurrent")
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(call_seq: int) -> None:
        try:
            gateway.call(identity, _request(call_seq=call_seq))
            with lock:
                outcomes.append("ok")
        except ModelBudgetDeniedError:
            with lock:
                outcomes.append("denied")

    threads = [threading.Thread(target=attempt, args=(seq,)) for seq in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["denied", "ok"]
    assert len(transport.calls) == 1
    assert gateway.planning_usage(identity)["model_requests"] == 1


# ---------------------------------------------------------------------------
# secret handling / receipt projection
# ---------------------------------------------------------------------------


def test_credential_never_leaves_the_gateway(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport)
    identity = _identity("secret")

    result = gateway.call(identity, _request())
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    gateway_dict = json.dumps(gateway.to_dict(), sort_keys=True)
    claim_files = [path.read_text() for path in (tmp_path / "model-receipts").glob("*.json")]

    assert _ENV["ACP2_MODEL_API_KEY"] not in serialized
    assert _ENV["ACP2_MODEL_API_KEY"] not in gateway_dict
    for content in claim_files:
        assert _ENV["ACP2_MODEL_API_KEY"] not in content
    # The transport receives the token, but its repr is secret-free.
    assert _ENV["ACP2_MODEL_API_KEY"] not in repr(transport.calls[0])
    assert transport.calls[0].auth_token == _ENV["ACP2_MODEL_API_KEY"]
    assert gateway.credential_state()["configured"] is True


def test_receipt_is_idempotent_and_content_addressed(tmp_path: Path) -> None:
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport)
    identity = _identity("receipts")

    result = gateway.call(identity, _request())
    stored = gateway.receipt(result.receipt.receipt_id)
    assert stored is not None
    assert stored["trusted_source"] == "controller-terminal-receipt"
    assert stored["job_id"] == ""
    assert stored["attempt"] == 0
    assert stored["request_digest"] == result.receipt.request_digest
    assert stored["usage"]["source"] == "provider-reported"
    assert stored["usage"]["complete"] is True
    # Re-delivery is a no-op (same bytes), never a second token charge.
    assert gateway.receipt(result.receipt.receipt_id) == stored


def test_gateway_statement_is_recorded_only_and_never_a_job(tmp_path: Path) -> None:
    from acp_services.runtime.model_gateway import model_gateway_statement

    statement = model_gateway_statement()
    assert statement["schema"] == MODEL_GATEWAY_SCHEMA
    assert statement["capability"] == "recorded-only"
    assert statement["reserves_before_call"] is True
    assert statement["job_or_attempt_fabricated"] is False
    assert statement["direct_client_credentials_allowed"] is False


def test_missing_usage_is_recorded_as_unknown_not_fabricated(tmp_path: Path) -> None:
    transport = RecordingTransport(ModelTransportResponse(text="answer", usage=None))
    gateway = _gateway(tmp_path, transport)
    identity = _identity("unknown-usage")

    result = gateway.call(identity, _request())

    assert result.receipt.usage.source == "estimated"
    assert result.receipt.usage.complete is False
    assert result.receipt.usage.output_tokens is None
    assert result.receipt.usage.input_tokens is not None
    # The budget unit still settles exactly once.
    assert gateway.planning_usage(identity)["model_requests"] == 1


def test_resolve_credential_reads_names_not_values(tmp_path: Path) -> None:
    credential = resolve_model_credential(
        {"DEEPSEEK_API_KEY": "s3cret", "ACP2_MODEL_ID": "approved-model"}
    )
    assert credential.environment == "DEEPSEEK_API_KEY"
    assert credential.model == "approved-model"
    assert "s3cret" not in json.dumps(credential.to_dict())
    with pytest.raises(ModelCredentialUnavailableError) as excinfo:
        resolve_model_credential({"ACP2_MODEL_ID": "approved-model"})
    assert excinfo.value.rule == "no_model_credential"


def test_usage_counter_units_are_not_overwritten(tmp_path: Path) -> None:
    """The planning book counts one model_request per call (never a token unit)."""
    transport = RecordingTransport()
    gateway = _gateway(tmp_path, transport, limit=2)
    identity = _identity("counters")
    result = gateway.call(identity, _request())

    usage = gateway.planning_usage(identity)
    assert usage["model_requests"] == 1
    # Token counts live in the trusted receipt, not in the unit ledger.
    projection = _ledger(tmp_path).research_budget_projection(result.reservation.budget_id)
    assert projection is not None
    assert projection.consumed["model_request"] >= 1
    assert set(UsageUnitCounts(model_request=1).to_dict()) == {
        "task_ref",
        "recipe_step",
        "qc_invocation",
        "attempt",
        "bsub",
        "model_request",
    }
