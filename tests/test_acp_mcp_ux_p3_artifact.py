"""P3: artifact body encoding label and digest-scoped verification."""

from __future__ import annotations

import hashlib

import pytest

from acp_services.data.artifacts import ArtifactRead, ArtifactRef
from acp_services.entrypoints import mcp
from acp_services.entrypoints.mcp import McpServer


def _ref(body: bytes) -> ArtifactRef:
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    return ArtifactRef(
        job_id="job_1",
        attempt=1,
        resource_id="report",
        generation=1,
        digest=digest,
        size_bytes=len(body),
    )


def test_non_utf8_body_reports_base64() -> None:
    body = b"\xff\xfe\x00"
    payload = ArtifactRead(ref=_ref(body), body=body).to_dict()
    assert payload["encoding"] == "base64"
    assert payload["body"] != ""


def test_valid_utf8_body_stays_identity() -> None:
    body = b"hello"
    payload = ArtifactRead(ref=_ref(body), body=body).to_dict()
    assert payload["encoding"] == "identity"
    assert payload["body"] == "hello"


def _payload(data: dict[str, object]) -> dict[str, object]:
    data.setdefault("truncated", False)
    return data


def test_matching_body_passes() -> None:
    body = b"hello"
    McpServer._verify_artifact_body(
        _payload({**_ref(body).to_dict(), "body": "hello", "encoding": "identity", "digest": _ref(body).digest})
    )


def test_redacted_body_is_refused() -> None:
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            _payload(
                {
                    "body": "path=/home/xieningke/secret.txt",
                    "encoding": "identity",
                    "digest": "sha256:" + "a" * 64,
                }
            )
        )
    assert excinfo.value.params["rule"] == "artifact_body_redacted"


def test_digest_mismatch_is_refused() -> None:
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            _payload(
                {
                    "body": "hello",
                    "encoding": "identity",
                    "digest": "sha256:" + "a" * 64,
                }
            )
        )
    assert excinfo.value.params["rule"] == "artifact_digest_mismatch"


def test_truncated_slice_is_exempt() -> None:
    McpServer._verify_artifact_body(
        {
            "body": "hel",
            "encoding": "identity",
            "digest": "sha256:" + "a" * 64,
            "truncated": True,
        }
    )


def test_base64_body_roundtrips_and_is_not_redacted_as_a_path() -> None:
    import base64

    raw = b"hello"
    payload = ArtifactRead(ref=_ref(raw), body=raw).to_dict()
    assert payload["encoding"] == "identity"
    # A UTF-8 text body encoded as base64 round-trips and passes the verifier.
    encoded = base64.b64encode(raw).decode("ascii")
    McpServer._verify_artifact_body(
        _payload({"body": encoded, "encoding": "base64", "digest": _ref(raw).digest})
    )


def test_truncated_multibyte_slice_decodes_to_the_same_bytes() -> None:
    import base64

    full = "é".encode("utf-8")
    sliced = full[:1]
    payload = ArtifactRead(ref=_ref(full), body=sliced, truncated=True).to_dict()
    assert payload["encoding"] == "base64"
    assert base64.b64decode(payload["body"]) == sliced
    assert payload["truncated"] is True


def test_truncated_body_with_a_redacted_pattern_is_refused() -> None:
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            {
                "body": "path=/home/xieningke/secret.txt",
                "encoding": "identity",
                "digest": "sha256:" + "a" * 64,
                "truncated": True,
            }
        )
    assert excinfo.value.params["rule"] == "artifact_body_redacted"


def test_base64_body_survives_machine_serialization() -> None:
    from acp_services.entrypoints.machine_output import MachineResult

    result = MachineResult(
        operation="read_artifact",
        status="ok",
        data={"body": "/w==", "encoding": "base64", "truncated": False},
        passthrough_body=True,
    )
    assert result.to_dict()["data"]["body"] == "/w=="


def test_identity_body_is_still_redacted() -> None:
    from acp_services.entrypoints.machine_output import MachineResult

    result = MachineResult(
        operation="read_artifact",
        status="ok",
        data={"body": "path=/home/xieningke/secret.txt", "encoding": "identity"},
    )
    assert result.to_dict()["data"]["body"] != "path=/home/xieningke/secret.txt"


def test_identity_body_that_looks_like_base64_is_still_redacted() -> None:
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            {
                "body": "/home/secret",
                "encoding": "identity",
                "digest": "sha256:" + "a" * 64,
            }
        )
    assert excinfo.value.params["rule"] == "artifact_body_redacted"


def test_base64_cannot_smuggle_a_redacted_path() -> None:
    import base64

    encoded = base64.b64encode(b"/home/xieningke/secret.txt").decode("ascii")
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            {
                "body": encoded,
                "encoding": "base64",
                "digest": "sha256:" + "a" * 64,
            }
        )
    assert excinfo.value.params["rule"] == "artifact_body_redacted"


def test_invalid_base64_is_refused() -> None:
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            {"body": "!!!!", "encoding": "base64", "digest": "sha256:" + "a" * 64}
        )
    assert excinfo.value.params["rule"] == "artifact_encoding_invalid"


def test_truncated_base64_is_still_content_checked() -> None:
    import base64

    encoded = base64.b64encode(b"prefix /home/secret").decode("ascii")
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            {
                "body": encoded,
                "encoding": "base64",
                "digest": "sha256:" + "a" * 64,
                "truncated": True,
            }
        )
    assert excinfo.value.params["rule"] == "artifact_body_redacted"


def test_non_utf8_body_is_unverifiable() -> None:
    import base64

    raw = b"\xff"
    encoded = base64.b64encode(raw).decode("ascii")
    with pytest.raises(mcp.McpToolError) as excinfo:
        McpServer._verify_artifact_body(
            {"body": encoded, "encoding": "base64", "digest": _ref(raw).digest}
        )
    assert excinfo.value.params["rule"] == "artifact_body_unverifiable"
