"""Round-8 三.7 / K4: local-single MCP responses may reveal declared fields.

``redact()`` is unconditional: it rewrites host paths and opaque-looking tokens
everywhere, so a ``local-single`` agent received ``<redacted-path>`` for its own
``work_dir`` and ``<redacted>`` for its ``idempotency_key`` — and went back to
``ls``/``cat`` on the run root.  The relaxation is deliberately narrow:

* **only** the MCP response exit requests it (never ``redact()`` itself, never
  an HTTP route);
* **only** for ``local-single`` with no bound credential (API-key/multi-user
  sessions stay redacted);
* **only** the declared :data:`LOCAL_SINGLE_REVEAL_FIELDS` allowlist;
* a credential-shaped field name is refused even when listed, and error
  messages stay redacted.
"""

from __future__ import annotations

from typing import Any

from acp_services.contracts.access import DeploymentTier, Principal
from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints import mcp

_LONG_NAME = "mol_Confsearch_20261031_120000_001_abcdefghijklmnopqrstuvwxyz"
_WORK_DIR = "/ACPServer/runs/uncategorized/" + _LONG_NAME
_IDEMPOTENCY_KEY = "idem_20261031_120000_001_abcdefghijklmnopqrstuvwxyz012345"


def _data() -> dict[str, Any]:
    return {
        "task_id": "20261031_120000_001_Confsearch",
        "work_dir": _WORK_DIR,
        "directory_name": _LONG_NAME,
        "name": _LONG_NAME,
        "task_name": _LONG_NAME,
        "molecule_name": "CCO",
        "idempotency_key": _IDEMPOTENCY_KEY,
        "spec_ref": {"artifact_id": "spec_20261031_120000_001_abcdefghijklmnopqrstuvwxyz"},
        "source_ref": _LONG_NAME,
        # never revealed, even when a caller lists it
        "api_key": "fake-api-key-value-not-a-credential-0001",
        # not on the allowlist
        "some_other_path": _WORK_DIR,
    }


class _FakeCapabilities:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def capabilities(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return dict(self._payload)


class _Unused:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"must not reach the {name} port")


def _server(
    payload: dict[str, Any] | None = None,
    *,
    tier: str = DeploymentTier.LOCAL_SINGLE,
    identity: Any = None,
) -> Any:
    unused = _Unused()
    return mcp.McpServer(
        backends=mcp.McpBackends(
            capabilities=_FakeCapabilities(payload or _data()),
            preflight=unused,
            submit=unused,
            status=unused,
            cancel=unused,
            artifacts=unused,
            analysis=unused,
        ),
        identity=identity,
        configured_tier=tier,
    )


def _capabilities(server: Any) -> dict[str, Any]:
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": mcp.McpTool.CAPABILITIES, "arguments": {}},
        }
    )
    assert response is not None and "result" in response, response
    return response["result"]


# --------------------------------------------------------------------------- #
# default redaction is unchanged (byte-identical contract)
# --------------------------------------------------------------------------- #


def test_redact_default_is_unchanged() -> None:
    sample = _data()
    expected = {
        "task_id": "20261031_120000_001_Confsearch",
        # secrets run before the path substitution: the long basename is first
        # collapsed by the opaque-token net, so a path keeps both placeholders
        "work_dir": "<redacted-path><redacted>",
        "directory_name": "<redacted>",
        "name": "<redacted>",
        "task_name": "<redacted>",
        "molecule_name": "CCO",
        "idempotency_key": "<redacted>",
        "spec_ref": {"artifact_id": "<redacted>"},
        "source_ref": "<redacted>",
        "api_key": "<redacted>",
        "some_other_path": "<redacted-path><redacted>",
    }
    assert mo.redact_value(sample) == expected
    # the explicit defaults reproduce the historical call exactly
    assert mo.redact_value(sample, reveal_paths=False, reveal_fields=frozenset()) == expected


def test_machine_result_default_serialisation_is_unchanged() -> None:
    result = mo.MachineResult(operation="acp_status", status="ok", data=_data())
    default = result.to_dict()
    explicit = result.to_dict(reveal_paths=False, reveal_fields=frozenset())
    assert default == explicit
    assert default["data"]["work_dir"] == "<redacted-path><redacted>"
    assert default["data"]["idempotency_key"] == "<redacted>"
    assert "<redacted-path>" in result.to_json()


def test_reveal_only_touches_declared_fields() -> None:
    revealed = mo.redact_value(
        _data(),
        reveal_paths=True,
        reveal_fields=mo.LOCAL_SINGLE_REVEAL_FIELDS,
    )
    assert revealed["work_dir"] == _WORK_DIR
    assert revealed["directory_name"] == _LONG_NAME
    assert revealed["name"] == _LONG_NAME
    assert revealed["task_name"] == _LONG_NAME
    assert revealed["idempotency_key"] == _IDEMPOTENCY_KEY
    assert revealed["source_ref"] == _LONG_NAME
    assert revealed["spec_ref"]["artifact_id"] == "<redacted>"  # nested value, not top-level
    assert revealed["some_other_path"] == "<redacted-path><redacted>"
    assert revealed["api_key"] == "<redacted>"


def test_a_secret_shaped_field_is_never_revealed() -> None:
    revealed = mo.redact_value(
        {"api_key": "fake-api-key-value-not-a-credential-0001", "work_dir": _WORK_DIR},
        reveal_paths=True,
        reveal_fields=frozenset({"api_key", "work_dir"}),
    )
    assert revealed["api_key"] == "<redacted>"
    assert revealed["work_dir"] == _WORK_DIR
    assert mo.is_secret_field_name("api_key") is True
    assert mo.is_secret_field_name("work_dir") is False


def test_secret_assignments_inside_a_revealed_field_stay_redacted() -> None:
    revealed = mo.redact_value(
        {"work_dir": "/srv/run/token=supersecretvalue"},
        reveal_paths=True,
        reveal_fields=frozenset({"work_dir"}),
    )
    assert "supersecretvalue" not in revealed["work_dir"]
    assert "<redacted>" in revealed["work_dir"]


def test_error_messages_are_not_revealed() -> None:
    result = mo.MachineResult(
        operation="acp_status",
        status="error",
        exit_code=mo.ExitCode.USAGE,
        error=mo.error_object(
            mo.ErrorCode.NOT_FOUND,
            f"unknown reference {_WORK_DIR}",
            params={"reference": _WORK_DIR},
        ),
    )
    payload = result.to_dict(reveal_paths=True, reveal_fields=mo.LOCAL_SINGLE_REVEAL_FIELDS)
    assert payload["error"]["params"]["reference"] == "<redacted-path><redacted>"
    assert "<redacted-path>" in payload["error"]["message"]


# --------------------------------------------------------------------------- #
# the MCP exit: opt-in only where it is safe
# --------------------------------------------------------------------------- #


def test_local_single_mcp_exit_reveals_the_declared_fields() -> None:
    result = _capabilities(_server())
    assert result["isError"] is False
    data = result["structuredContent"]["data"]
    assert data["work_dir"] == _WORK_DIR
    assert data["name"] == _LONG_NAME
    assert data["idempotency_key"] == _IDEMPOTENCY_KEY
    assert data["api_key"] == "<redacted>"
    # the text frame and the structured payload carry the same policy
    import json

    text_frame = json.loads(result["content"][0]["text"])
    assert text_frame["data"]["work_dir"] == _WORK_DIR


def test_multi_user_tiers_stay_redacted() -> None:
    """``API_KEY`` is an identity mode, not a tier: every non-local tier hides."""
    for tier in (DeploymentTier.LAB, DeploymentTier.HOSTED):
        server = _server(tier=tier)
        assert server._reveal_policy() is False
        payload = server._result_payload(
            mo.MachineResult(operation="acp_status", status="ok", data=_data())
        )
        data = payload["structuredContent"]["data"]
        assert data["work_dir"] == "<redacted-path><redacted>"
        assert data["idempotency_key"] == "<redacted>"


def test_a_bound_credential_disables_the_reveal_even_on_local_single() -> None:
    identity = mcp.IdentityMapper(
        configured_tier=DeploymentTier.LOCAL_SINGLE,
        bound_principal=Principal(
            principal_id="principal_remote",
            kind="user",
            tenant_id="tenant_remote",
        ),
        bound_actions=tuple(mcp.STDIO_PERMISSION_CEILING),
    )
    server = _server(identity=identity)
    assert server._reveal_policy() is False
    payload = server._result_payload(
        mo.MachineResult(operation="acp_status", status="ok", data=_data())
    )
    assert payload["structuredContent"]["data"]["work_dir"] == "<redacted-path><redacted>"
