# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""Round-9 M1: an artifact body carrying host paths is READABLE (masked).

The external agent's P0: every PES profile / frequency / energy JSON was refused
outright because it contained a ``/ACPServer/...`` path, so the official TS chain
could read nothing.  Round-9 replaces the whole-body *refusal* with an explicit
**masking** presentation:

* the integrity check still runs on the **stored** bytes (`digest` keeps covering
  the artifact; masking never skips or weakens it);
* the whole body is masked **before** ``max_bytes`` is applied, then the masked
  text is sliced;
* the response declares the masking — ``body_masked``, ``masked_digest`` (the
  returned slice), ``redaction="paths+secrets"`` and ``digest_scope="artifact"``;
* only a body with no verifiable presentation (non-UTF-8/binary) is still
  refused, and the one masking implementation is the shared ``redact`` policy.

The happy path runs the real Controller (uvicorn + ``JobManager`` + prepare
service + artifact store) and the real MCP adapter, so "the body is readable"
is proven over the wire the agent actually uses.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _client,
    _LiveController,
    _runtime,
    _tool,
    live_controller,
)

PROJECT = "uncategorized"
JOB_ID = "20261102_120000_000_frequency"
ATTEMPT = 1
ARTIFACT_ID = "normal_modes"
RELATIVE_PATH = "RESULT/frequencies/normal_modes.json"
BINARY_ARTIFACT_ID = "raw_binary"
BINARY_RELATIVE_PATH = "RESULT/frequencies/raw.bin"

_HOST_PATH = "/ACPServer/AutoCalcPlatform/RESULT/freq.out"
_HOME_PATH = "/home/xieningke/runs/20261102_120000_000_frequency-1"
_BEARER = "Bearer sk-round9bearer0123456789abcdef"

#: A realistic frequency product: numeric science plus the paths that used to
#: make the whole read fail.  Long enough for the ``max_bytes`` slice to bite.
_PAYLOAD: dict[str, Any] = {
    "schema": "acp2.frequency-modes.v1",
    "source": {"path": _HOST_PATH, "work_dir": _HOME_PATH},
    "frequencies_cm1": [round(3000 + index * 0.37, 3) for index in range(120)],
    "zpve_hartree": 0.021345,
    "imaginary_count": 0,
    "modes": [[0.1, -0.2, 0.3]],
    "log_ref": f"{_HOST_PATH}.log",
    "authorization": _BEARER,
}
BODY = (json.dumps(_PAYLOAD, indent=2) + "\n").encode("utf-8")
DIGEST = "sha256:" + hashlib.sha256(BODY).hexdigest()
BINARY_BODY = b"\xff\xfe\x00\x01binary"


def _sha256(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _seed_job(live: _LiveController) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    work_dir = live.manager.run_root / PROJECT / f"{JOB_ID}-{ATTEMPT}"
    (work_dir / "RESULT" / "frequencies").mkdir(parents=True, exist_ok=True)
    (work_dir / RELATIVE_PATH).write_bytes(BODY)
    (work_dir / BINARY_RELATIVE_PATH).write_bytes(BINARY_BODY)
    live.manager.store.create(
        JobRecord(
            id=JOB_ID,
            spec=JobSpec(workflow="frequency", name=JOB_ID, project_id=PROJECT),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            attempt=ATTEMPT,
            project_id=PROJECT,
        )
    )


def _prepare(live: _LiveController, *, artifact_id: str, relative_path: str) -> dict[str, Any]:
    import time

    prepared = dict(
        _client(live).prepare(
            {
                "schema_version": 1,
                "purpose": "artifact",
                "job_id": JOB_ID,
                "artifact_id": artifact_id,
                "attempt": ATTEMPT,
                "generation": 1,
                "relative_path": relative_path,
                "media_type": "application/json",
            }
        )
    )
    operation_id = str(prepared["operation_id"])
    deadline = time.monotonic() + 10.0
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = dict(_client(live).operation(operation_id))
        if body.get("state") in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.01)
    assert body.get("state") == "ready", body
    return {
        "task_id": JOB_ID,
        "attempt": ATTEMPT,
        "artifact_id": artifact_id,
        "generation": 1,
        "digest": str(body["artifact_ref"]["digest"]),
    }


def _read(live: _LiveController, ref: dict[str, Any], **extra: Any) -> dict[str, Any]:
    runtime = _runtime(live)
    arguments: dict[str, Any] = {"artifact_ref": ref}
    arguments.update(extra)
    result = _tool(runtime, "acp_read_artifact", arguments)
    assert result["isError"] is False, result
    return result["structuredContent"]["data"]


# ---------------------------------------------------------------------------
# 1. The P0: a path-bearing JSON body is readable, masked and verifiable
# ---------------------------------------------------------------------------


def test_mcp_read_returns_a_masked_but_readable_json_body(
    live_controller: _LiveController,
) -> None:
    _seed_job(live_controller)
    ref = _prepare(live_controller, artifact_id=ARTIFACT_ID, relative_path=RELATIVE_PATH)
    assert ref["digest"] == DIGEST  # the digest is the stored artifact's

    data = _read(live_controller, ref)

    assert data["body_masked"] is True
    assert data["redaction"] == "paths+secrets"
    assert data["digest_scope"] == "artifact"
    assert data["truncated"] is False
    # digest == the STORED artifact; masked_digest == the RETURNED text.
    assert data["digest"] == DIGEST
    assert data["masked_digest"] == _sha256(data["body"].encode("utf-8"))
    assert data["digest"] != data["masked_digest"]
    # The masked text is exactly the one shared policy's output (no second one).
    from acp_services.entrypoints.machine_output import mask_body_text

    assert data["body"] == mask_body_text(BODY.decode("utf-8"))

    # Readable: still valid JSON with the science values unchanged.
    parsed = json.loads(data["body"])
    assert parsed["frequencies_cm1"] == _PAYLOAD["frequencies_cm1"]
    assert parsed["zpve_hartree"] == _PAYLOAD["zpve_hartree"]
    assert parsed["imaginary_count"] == 0
    assert parsed["modes"] == _PAYLOAD["modes"]

    # No host path and no secret value survived the mask.
    for withheld in ("/ACPServer", "/home/xieningke", _HOST_PATH, _HOME_PATH):
        assert withheld not in data["body"]
    assert "sk-round9bearer0123456789abcdef" not in data["body"]
    assert "Bearer <redacted>" in data["body"]


def test_the_same_read_is_byte_identical_twice(
    live_controller: _LiveController,
) -> None:
    """Masking must be deterministic: a retried read replays the same bytes."""
    _seed_job(live_controller)
    ref = _prepare(live_controller, artifact_id=ARTIFACT_ID, relative_path=RELATIVE_PATH)
    first = _read(live_controller, ref)
    second = _read(live_controller, ref)
    assert first["body"] == second["body"]
    assert first["masked_digest"] == second["masked_digest"]


def test_base64_transport_carries_the_same_masked_bytes(
    live_controller: _LiveController,
) -> None:
    """``encoding=base64`` is a transport choice, not a second masking policy."""
    import base64

    _seed_job(live_controller)
    ref = _prepare(live_controller, artifact_id=ARTIFACT_ID, relative_path=RELATIVE_PATH)
    plain = _read(live_controller, ref)
    encoded = _read(live_controller, ref, encoding="base64")

    assert encoded["body_masked"] is True
    assert encoded["encoding"] == "base64"
    decoded = base64.b64decode(encoded["body"])
    assert decoded == plain["body"].encode("utf-8")
    assert encoded["masked_digest"] == _sha256(decoded)
    assert encoded["digest"] == DIGEST


def test_max_bytes_slices_the_masked_text_not_the_stored_body(
    live_controller: _LiveController,
) -> None:
    """The policy runs on the whole body; ``max_bytes`` only bounds the return."""
    _seed_job(live_controller)
    ref = _prepare(live_controller, artifact_id=ARTIFACT_ID, relative_path=RELATIVE_PATH)
    full = _read(live_controller, ref)
    sliced = _read(live_controller, ref, max_bytes=1200)

    assert sliced["truncated"] is True
    assert sliced["digest_scope"] == "artifact"
    assert sliced["digest"] == DIGEST
    # Bound applies to the masked text (which is shorter than the stored body).
    assert len(sliced["body"].encode("utf-8")) <= 1200
    assert len(full["body"]) > len(sliced["body"])
    assert sliced["body"] == full["body"][: len(sliced["body"])]
    # masked_digest covers the returned slice; digest never changes.
    assert sliced["masked_digest"] == _sha256(sliced["body"].encode("utf-8"))
    assert sliced["masked_digest"] != full["masked_digest"]
    assert sliced["size_bytes"] == len(BODY)


# ---------------------------------------------------------------------------
# 2. The remaining hard refusal: a body with no verifiable presentation
# ---------------------------------------------------------------------------


def test_binary_body_is_still_refused_with_a_projection_hint(
    live_controller: _LiveController,
) -> None:
    _seed_job(live_controller)
    ref = _prepare(
        live_controller, artifact_id=BINARY_ARTIFACT_ID, relative_path=BINARY_RELATIVE_PATH
    )
    result = _tool(_runtime(live_controller), "acp_read_artifact", {"artifact_ref": ref})
    assert result["isError"] is True, result
    error = result["structuredContent"]["error"]
    assert error["code"] == "unsupported_capability"
    assert error["retryable"] is False
    assert error["params"]["rule"] == "artifact_body_unverifiable"
    assert error["params"]["allowed_formats"] == ["utf-8 text"]
    assert BINARY_BODY.decode("latin-1")[:4] not in json.dumps(result)


# ---------------------------------------------------------------------------
# 3. The two policies share one implementation
# ---------------------------------------------------------------------------


def test_the_strict_policy_still_refuses_a_path_body() -> None:
    from acp_services.data.artifacts import (
        BODY_POLICY_MASK,
        BODY_POLICY_REFUSE,
        ArtifactBodyRefusedError,
        _present_body_text,
    )

    body = b'{"path": "/ACPServer/AutoCalcPlatform/RESULT/x.json", "energy": -76.1}'
    presented, masked = _present_body_text(body, policy=BODY_POLICY_MASK)
    assert masked is True
    assert b"/ACPServer" not in presented
    assert b"-76.1" in presented

    with pytest.raises(ArtifactBodyRefusedError) as refused:
        _present_body_text(body, policy=BODY_POLICY_REFUSE)
    assert refused.value.params["rule"] == "artifact_body_redacted"


def test_an_unknown_body_policy_is_a_typed_refusal() -> None:
    from acp_services.data.artifacts import ArtifactError, resolve_artifact_read

    with pytest.raises(ArtifactError) as excinfo:
        resolve_artifact_read(  # type: ignore[arg-type]
            None,
            None,
            job_id="j",
            attempt=1,
            resource_id="r",
            generation=1,
            digest="sha256:" + "a" * 64,
            body_policy="bogus",
        )
    assert excinfo.value.params["rule"] == "invalid_enum"
    assert excinfo.value.params["allowed"] == ["mask", "refuse"]


def test_binary_body_has_no_presentation_under_either_policy() -> None:
    from acp_services.data.artifacts import (
        BODY_POLICY_MASK,
        BODY_POLICY_REFUSE,
        ArtifactBodyRefusedError,
        _present_body_text,
    )

    for policy in (BODY_POLICY_MASK, BODY_POLICY_REFUSE):
        with pytest.raises(ArtifactBodyRefusedError) as excinfo:
            _present_body_text(BINARY_BODY, policy=policy)
        assert excinfo.value.params["rule"] == "artifact_body_unverifiable"


# ---------------------------------------------------------------------------
# 4. The MCP verifier trusts no mask: it verifies the declared masked digest
# ---------------------------------------------------------------------------


def _masked_payload(**overrides: Any) -> dict[str, Any]:
    from acp_services.entrypoints.machine_output import mask_body_text, masked_body_digest

    text = mask_body_text(BODY.decode("utf-8"))
    payload: dict[str, Any] = {
        "body": text,
        "encoding": "identity",
        "digest": DIGEST,
        "body_masked": True,
        "masked_digest": masked_body_digest(text.encode("utf-8")),
        "truncated": False,
    }
    payload.update(overrides)
    return payload


def test_mcp_verifier_accepts_a_consistent_masked_body() -> None:
    from acp_services.entrypoints.mcp import McpServer

    payload = _masked_payload()
    McpServer._verify_artifact_body(payload)
    assert payload["digest_scope"] == "artifact"


def test_mcp_verifier_refuses_a_masked_digest_mismatch() -> None:
    from acp_services.entrypoints.mcp import McpServer

    with pytest.raises(Exception) as excinfo:
        McpServer._verify_artifact_body(_masked_payload(masked_digest="sha256:" + "b" * 64))
    assert excinfo.value.params["rule"] == "artifact_masked_digest_mismatch"  # type: ignore[attr-defined]


def test_mcp_verifier_refuses_a_mask_that_did_not_remove_the_path() -> None:
    from acp_services.entrypoints.machine_output import masked_body_digest
    from acp_services.entrypoints.mcp import McpServer

    unmasked = BODY.decode("utf-8")
    with pytest.raises(Exception) as excinfo:
        McpServer._verify_artifact_body(
            _masked_payload(body=unmasked, masked_digest=masked_body_digest(BODY))
        )
    assert excinfo.value.params["rule"] == "artifact_body_redacted"  # type: ignore[attr-defined]


def test_mcp_verifier_requires_masked_digest_on_a_masked_body() -> None:
    from acp_services.entrypoints.mcp import McpServer

    payload = _masked_payload()
    payload.pop("masked_digest")
    with pytest.raises(Exception) as excinfo:
        McpServer._verify_artifact_body(payload)
    assert excinfo.value.params["rule"] == "artifact_masked_digest_missing"  # type: ignore[attr-defined]
