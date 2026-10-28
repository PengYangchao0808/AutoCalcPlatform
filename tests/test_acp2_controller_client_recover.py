"""m1: ``ControllerClient.recover`` refuses explicitly and never hits the transport.

The public method is retained (SDK compatibility, plan R6) but no Controller
route is registered for HTTP recovery.  These tests pin three facts:

* ``recover`` raises :class:`UnsupportedError` (``unsupported_capability``)
  before any request object or session is created, with a human-readable
  pointer to the local recovery entry (``acp task recover``);
* the unregistered ``/api/v1/recover`` URL is gone from the client source;
* both delivered contracts mark the operation ``not_delivered`` instead of
  advertising a bound route, and the manifest digests stay in sync.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from acp_services.entrypoints.controller_client import ControllerClient, UnsupportedError

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / "docs/reference/next-change/acp2-contracts"
RUNTIME = CONTRACTS / "runtime"
CLIENT_PATH = REPO_ROOT / "src/acp_services/entrypoints/controller_client.py"

#: A plan identity that would be valid for the frozen recover request schema;
#: the command is refused regardless of payload, so this only proves the
#: refusal is not a payload-validation artefact.
_VALID_RECOVER_REQUEST: dict[str, Any] = {"plan_digest": "sha256:" + "0" * 64}


def _client() -> ControllerClient:
    # Port 9 (discard) is intentionally dead: an accidental request fails.
    return ControllerClient("http://127.0.0.1:9")


def test_recover_raises_unsupported_with_no_transport_call(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    def _forbid(label: str):
        def _fail(*args: Any, **kwargs: Any) -> Any:
            attempts.append(label)
            raise AssertionError(f"recover must not touch the transport ({label})")

        return _fail

    monkeypatch.setattr(urllib.request, "urlopen", _forbid("urlopen"))
    monkeypatch.setattr(urllib.request, "Request", _forbid("Request"))
    monkeypatch.setattr(ControllerClient, "_request_json", _forbid("_request_json"))

    with pytest.raises(UnsupportedError) as excinfo:
        _client().recover(_VALID_RECOVER_REQUEST)

    error = excinfo.value
    assert attempts == []
    assert error.code == "unsupported_capability"
    assert error.retryable is False
    assert "not delivered over HTTP" in error.message
    assert "acp task recover" in error.message
    assert error.params["command"] == "recover"
    assert error.params["delivery_status"] == "not_delivered"
    assert error.params["transport_binding"] == "none"
    assert error.params["local_entry"] == "acp task recover"
    assert error.to_dict()["code"] == "unsupported_capability"


def test_recover_public_signature_is_retained() -> None:
    signature = inspect.signature(ControllerClient.recover)
    assert list(signature.parameters) == ["self", "request"]
    assert signature.return_annotation is not inspect.Signature.empty
    # The class attribute is the frozen public surface; it is never deleted.
    assert callable(getattr(ControllerClient, "recover"))


def test_client_source_no_longer_references_the_unregistered_route() -> None:
    source = CLIENT_PATH.read_text(encoding="utf-8")
    assert "/api/v1/recover" not in source


def test_application_api_marks_recover_not_delivered_over_http() -> None:
    api = json.loads((RUNTIME / "application-api.v1.json").read_text(encoding="utf-8"))
    recover = api["operations"]["recover"]
    assert recover["id"] == "recover"
    assert recover["status"] == "not_delivered"
    delivery = recover["http_delivery"]
    assert delivery["status"] == "not_delivered"
    assert delivery["transport_binding"] == "none"
    assert delivery["error_class"] == "unsupported"
    assert delivery["error_code"] == "unsupported_capability"
    assert delivery["client_method"].startswith("ControllerClient.recover")
    assert delivery["local_use_case"].startswith("cli:acp task recover")
    # Only the recover operation changed: no other command claims a delivery
    # status or an HTTP-delivery block.
    others = {op_id: op for op_id, op in api["operations"].items() if op_id != "recover"}
    assert others
    assert all("status" not in op and "http_delivery" not in op for op in others.values())


def test_controller_topology_has_no_recover_transport_binding() -> None:
    topology = json.loads((RUNTIME / "controller-topology.v1.json").read_text(encoding="utf-8"))
    rows = [row for row in topology["endpoints"] if row["command"] == "recover"]
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "not_delivered"
    assert row["transport_binding"] == "none"
    assert row["method"] is None
    assert row["path"] is None
    assert row["client_method"] == "ControllerClient.recover"
    assert row["local_entry"].startswith("cli:acp task recover")
    assert all(endpoint.get("path") != "/api/v1/recover" for endpoint in topology["endpoints"])
    # The other commands keep their transport bindings untouched.
    assert (
        sum(1 for endpoint in topology["endpoints"] if endpoint.get("method"))
        == len(topology["endpoints"]) - 1
    )


def test_controller_client_methods_pin_still_holds() -> None:
    # Mirror of the P2 pin
    # (tests/test_acp2_runtime_contracts.py::test_p2_research_operations_are_complete_and_owned)
    # for the part that touches ControllerClient methods.  That pin never
    # enumerated ``recover``; what it requires is a non-empty client-method
    # list per P2 row, and the recover method itself stays on the client.
    p2 = json.loads((RUNTIME / "p2-research-operations.v1.json").read_text(encoding="utf-8"))
    assert p2["rows"]
    for row in p2["rows"]:
        assert row["controller_client_methods"]
    assert "ControllerClient.recover" not in {
        method for row in p2["rows"] for method in row["controller_client_methods"]
    }
    assert hasattr(ControllerClient, "recover")


def test_manifest_digests_match_the_updated_contracts() -> None:
    manifest = json.loads((CONTRACTS / "manifest.json").read_text(encoding="utf-8"))
    entries = {entry["id"]: entry for entry in manifest["contracts"]}
    for contract_id in ("application-api", "controller-topology"):
        entry = entries[contract_id]
        path = REPO_ROOT / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], contract_id
