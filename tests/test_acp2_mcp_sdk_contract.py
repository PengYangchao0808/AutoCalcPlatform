"""Real MCP SDK/transport handshake contract (M0-08 / ADR-006, G01).

Cross-stack contract lane (``-m contract``): these tests need Node ≥ 22.18 and
the external ``$ACP2_TOOLS/deepseek-harness`` tree that pins
``@deepseek-ai/dsh-mcp-client`` → ``@modelcontextprotocol/client`` ``2.2.0``.
The ordinary Python matrix excludes them; a missing prerequisite is a
``NOT_VERIFIED`` skip with the exact reason, never a fabricated pass.

What is proven here (and what is not):

* the **unmodified** frozen client, with the plugin's production constructor
  options (``versionNegotiation: {mode: 'auto'}``), completes ``initialize``
  against a real ``acp mcp serve`` stdio process and lists the P1 tool allowlist
* the client offers ``2025-11-25``, the server answers ``2025-06-18`` (standard
  MCP negotiation, ADR-006), and the client accepts it because it supports it;
* the ``auto`` era probe runs on a **second, disposable adapter process**
  (``server/discover`` → ordinary ``-32601``): two adapter startups are
  observable at the Controller stub and neither creates a task;
* a genuinely incompatible client (its own ``supportedProtocolVersions`` excludes
  the server version) refuses *on its own* — the server never turns an
  unsupported version into an error — and still creates no task;
* "SDK handshake passed" is **not** "full DSH Agent passed": model, skills and
  the research extension handshake stay ``NOT_VERIFIED``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests import _acp2_mcp_live_harness as harness

pytestmark = pytest.mark.contract

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _prerequisites() -> harness.Prerequisites:
    facts = harness.prerequisites()
    if not facts.available:
        pytest.skip(f"NOT_VERIFIED: {facts.problem}")
    return facts


@pytest.fixture(scope="module")
def live_default(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One real default handshake, archived for the assertions below."""
    facts = _prerequisites()
    out = tmp_path_factory.mktemp("mcp-live-default")
    run_root = out / "run-root"
    run_root.mkdir()
    with harness.FakeController() as controller:
        launch = harness.write_launch_descriptor(
            out / "mcp-launch.json", harness.launch_descriptor(controller.url, run_root)
        )
        document = harness.run_live_client(
            launch, out / "client", node=facts.node, dsh_source=facts.dsh_source
        )
        counters = controller.counters.to_dict()
    return {
        "facts": facts.details,
        "document": document,
        "counters": counters,
        "out": out,
        "run_root": run_root,
        "run_root_entries": sorted(str(p.relative_to(run_root)) for p in run_root.rglob("*")),
    }


def test_real_dsh_client_default_auto_handshake_succeeds(live_default: dict[str, Any]) -> None:
    document = live_default["document"]
    assert document.get("status") == "ok", document.get("error") or document.get("stderr_tail")
    assert document.get("exit_code") == 0
    assert document.get("scenario") == "default"

    # The package actually used is the frozen one the matrix pins.
    assert document["package_versions"]["@deepseek-ai/dsh-mcp-client"]["version"] == "0.2.1-rc.2"
    assert document["package_versions"]["@modelcontextprotocol/client"]["version"] == "2.2.0"
    assert document["client_options"]["client_info"] == {
        "name": "dsh-mcp-client",
        "version": "0.0.1",
    }
    assert document["client_options"]["versionNegotiation"] == {"mode": "auto"}


def test_server_answers_its_own_version_and_client_accepts_it(
    live_default: dict[str, Any],
) -> None:
    document = live_default["document"]
    assert document["requested_protocol"] == "2025-11-25"
    assert document["negotiated_protocol"] == harness.SERVER_VERSION
    initialize = document["initialize_response"]
    assert initialize["protocolVersion"] == harness.SERVER_VERSION
    assert initialize["supportedProtocolVersions"] == [harness.SERVER_VERSION]
    # M0-08 result shape: no top-level extensions, no negotiated claim.
    assert "extensions" not in initialize
    assert '"negotiated"' not in json.dumps(initialize.get("capabilities") or {})
    guidance = initialize["capabilities"]["extensions"]["acp.negotiation.v1"]
    assert guidance["requested_version"] == "2025-11-25"
    assert guidance["supported_versions"] == [harness.SERVER_VERSION]
    assert document["server"]["server_info"]["name"] == "acp-services-mcp"


def test_tools_list_is_the_p1_tool_allowlist(live_default: dict[str, Any]) -> None:
    tools = live_default["document"].get("tools") or []
    assert {tool["name"] for tool in tools} == set(harness.EXPECTED_TOOLS)
    assert all(tool["inputSchema"]["type"] == "object" for tool in tools)
    # The live tools/list is what the client registers: that is the authority.
    assert live_default["document"]["capabilities_call"]["status"] == "ok"


def test_discover_probe_and_initialize_are_both_side_effect_free(
    live_default: dict[str, Any],
) -> None:
    """Two adapter startups (era probe + session), zero task/storage effects."""
    counters = live_default["counters"]
    assert counters["submit_posts"] == 0
    assert counters["other_requests"] == []
    # `auto` on the SDK's own stdio transport probes a disposable sibling
    # process; each adapter process calls /api/health exactly once at startup.
    assert counters["health_reads"] >= 2
    assert counters["capabilities_reads"] >= 1
    assert live_default["run_root_entries"] == []

    # The session wire only carries the legacy handshake: the probe went to the
    # sibling process, not to the connection the caller owns.
    methods = harness.wire_methods(live_default["document"])
    assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]
    assert "server/discover" not in methods


def test_real_sdk_capabilities_call_returns_the_research_handshake(
    live_default: dict[str, Any],
) -> None:
    """M0-09: the real frozen client reads `extensions[]` from `tools/call`.

    This is the application-level handshake proof the execution plan asks for:
    the unmodified `@deepseek-ai/dsh-mcp-client` → `@modelcontextprotocol/client`
    2.2.0 client sends `research_extension{id,version}` to `acp_capabilities`
    (because `initialize` carries no extension claim and the client sends
    `capabilities: {}`) and reads back the server's advertisement.
    """
    document = live_default["document"]
    handshake = document.get("research_handshake") or {}
    assert handshake.get("tool") == "acp_capabilities"
    assert handshake.get("error") is None, handshake
    # M2-01c registers the ten promised tools, so the exact-version request is a
    # successful negotiation — and only a covering implementation may say so.
    assert handshake["isError"] is False
    assert handshake["status"] == "ok"
    assert handshake["error_code"] is None
    assert handshake["error_params"] is None
    entries = handshake["extensions"]
    assert isinstance(entries, list) and len(entries) == 1, entries
    entry = entries[0]
    assert entry["id"] == "acp.research.v1"
    assert entry["version"] == "1.0.0"
    assert entry["supported"] is True
    assert entry["requested"] == {"id": "acp.research.v1", "version": "1.0.0"}
    assert entry["negotiated_by"] == "tools/call:acp_capabilities"
    assert entry["implemented"] is True
    assert entry["negotiated"] is True
    assert entry["refusal"] is None

    # The machine envelope (including extensions[]) survives as JSON text, which
    # is what the DSH plugin turns into a thrown tool failure: the typed refusal
    # is therefore still recoverable through the adapter.
    assert handshake["content_text_is_json"] is True

    # The plain discovery call advertises the same extension without a request.
    plain = document["capabilities_call"]
    assert plain["isError"] is False
    # A plain discovery read makes no negotiated claim: implemented describes the
    # registered set, negotiated describes *this* session.
    assert plain["extensions"][0]["implemented"] is True
    assert plain["extensions"][0]["negotiated"] is False
    assert plain["extensions"][0]["requested"] is None

    # Two `tools/call` frames, still no task and no storage effect.
    counters = live_default["counters"]
    assert counters["submit_posts"] == 0
    assert counters["other_requests"] == []
    methods = harness.wire_methods(document)
    assert methods.count("tools/call") == 3


def test_research_handshake_identity_matches_the_contract_and_the_bridge() -> None:
    """The wire id/version has one authority; the live client repeats it."""
    from acp import mcp_extension

    contract = json.loads(
        (
            _REPO_ROOT
            / "docs"
            / "reference"
            / "next-change"
            / "acp2-contracts"
            / "runtime"
            / "p2-research-operations.v1.json"
        ).read_text(encoding="utf-8")
    )
    negotiation = contract["client_path"]["negotiation"]
    extension_id = negotiation["extension_id"]
    extension_version = negotiation["extension_version"]
    script = harness.LIVE_CLIENT_SCRIPT.read_text(encoding="utf-8")
    bridge = (_REPO_ROOT / "rph" / "packages" / "bridge" / "src" / "index.ts").read_text(
        encoding="utf-8"
    )
    assert f'RESEARCH_EXTENSION_ID = "{extension_id}"' in script
    assert f'RESEARCH_EXTENSION_VERSION = "{extension_version}"' in script
    assert f'BRIDGE_EXTENSION_ID = "{extension_id}"' in bridge
    assert f'BRIDGE_EXTENSION_VERSION = "{extension_version}"' in bridge
    assert mcp_extension.RESEARCH_EXTENSION_ID == extension_id
    assert mcp_extension.RESEARCH_EXTENSION_VERSION == extension_version


def test_incompatible_client_refuses_on_its_own_without_tasks(tmp_path: Path) -> None:
    facts = _prerequisites()
    run_root = tmp_path / "run-root"
    run_root.mkdir()
    with harness.FakeController() as controller:
        launch = harness.write_launch_descriptor(
            tmp_path / "mcp-launch.json", harness.launch_descriptor(controller.url, run_root)
        )
        document = harness.run_live_client(
            launch,
            tmp_path / "client",
            node=facts.node,
            dsh_source=facts.dsh_source,
            scenario="incompatible-client",
        )
        counters = controller.counters.to_dict()

    assert document.get("status") == "refused", document.get("error")
    assert document.get("exit_code") == 0
    # The server did answer (its own version); refusing it was the client's call.
    assert document.get("negotiated_protocol") == harness.SERVER_VERSION
    assert document.get("requested_protocol") == "2025-11-25"
    error_message = str((document.get("error") or {}).get("message") or "")
    assert "not supported" in error_message
    assert document.get("tools") is None
    assert counters["submit_posts"] == 0
    assert sorted(p.name for p in run_root.iterdir()) == []


def test_real_sdk_workflow_selector_keeps_the_negotiated_state(
    live_default: dict[str, Any],
) -> None:
    """P2: the real client reads a workflow contract and keeps its negotiate state."""
    document = live_default["document"]
    selector = document.get("workflow_selector") or {}
    assert selector.get("isError") is False, selector
    assert selector["workflow"] == "Confsearch"
    assert selector["coverage"] == "partial"
    assert selector["status_vocabulary"] is True
    assert selector["research_tools_visible"] is True
    listed = set(selector["listed_after_selector"])
    assert "acp_research_proposal_create_v1" in listed
