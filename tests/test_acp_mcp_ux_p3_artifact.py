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
