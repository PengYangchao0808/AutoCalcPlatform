"""Live real-SDK MCP handshake harness (M0-08 / ADR-006).

Un-prefixed on purpose: this module is imported by contract tests and by the
acceptance driver, never collected as a test.

It provides the smallest honest fixture set for the *real* client handshake:

* :func:`fake_controller` — a loopback Controller stub.  It serves the two
  read-only endpoints the adapter needs (``/api/health``, ``/api/capabilities``)
  and **counts** every request so a test can prove that neither the
  ``server/discover`` probe process nor the ``initialize`` process created a
  task (``POST /api/v1/jobs`` is recorded, never served).
* :func:`write_launch_descriptor` — the launch JSON the frozen official client
  script (``scripts/acceptance/acp2_mcp_live_client.mjs``) and the negotiation
  probe consume.
* :func:`prerequisites` — the external environment check (Node ≥ 22.18 for
  TypeScript type-stripping, the real ``$ACP2_TOOLS/deepseek-harness`` tree with
  ``@deepseek-ai/dsh-mcp-client`` and its pinned
  ``@modelcontextprotocol/client`` 2.2.0).  A missing prerequisite is
  ``NOT_VERIFIED`` — never a fabricated pass.
* :func:`run_live_client` — spawn the frozen official client script and return
  its archived JSON document plus the raw wire frames.

No product code is imported here beyond the CLI entry point the launch
descriptor names; no QC, model or LSF access happens.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
LIVE_CLIENT_SCRIPT = REPO_ROOT / "scripts" / "acceptance" / "acp2_mcp_live_client.mjs"
ADAPTER_MODULE = "acp"

#: Node needs unflagged TypeScript type-stripping for the DSH plugin's
#: ``src/transport.ts`` (Node 22.18+/23.6+).  The SDK itself only needs 20+.
MIN_NODE_VERSION = (22, 18)
DEFAULT_DSH_SOURCE = Path("/ACPServer/acp2-tools/deepseek-harness")
DEFAULT_NODE_CANDIDATES = (
    Path("/ACPServer/acp2-tools/node-v22.19.0-linux-x64/bin/node"),
    Path("/ACPServer/acp2-tools/pnpm/node_modules/.bin/node"),
)
EXPECTED_TOOLS = frozenset(
    {
        "acp_capabilities",
        "acp_preflight",
        "acp_submit",
        "acp_status",
        "acp_cancel",
        "acp_prepare_artifact",
        "acp_read_artifact",
        "acp_get_guidance",
        "acp_list_jobs",
        "acp_analyze",
    }
)
SERVER_VERSION = "2025-06-18"


# --------------------------------------------------------------------------- #
# External prerequisites (three-state: present / missing = NOT_VERIFIED)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Prerequisites:
    """The resolved live-client environment."""

    node: Path | None
    dsh_source: Path | None
    problem: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.problem is None and self.node is not None and self.dsh_source is not None


def _node_version(node: Path) -> tuple[int, int] | None:
    try:
        raw = subprocess.run(
            [str(node), "--version"], capture_output=True, text=True, timeout=20, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not raw.startswith("v"):
        return None
    parts = raw[1:].split(".")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None


def _resolve_node() -> tuple[Path | None, str | None]:
    candidates: list[Path] = []
    override = os.environ.get("ACP2_NODE")
    if override:
        candidates.append(Path(override))
    candidates.extend(DEFAULT_NODE_CANDIDATES)
    discovered = shutil.which("node")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        version = _node_version(candidate)
        if version is None:
            continue
        if version >= MIN_NODE_VERSION:
            return candidate, None
    return None, f"no Node >= {MIN_NODE_VERSION[0]}.{MIN_NODE_VERSION[1]} found (set ACP2_NODE)"


def _dsh_facts(source: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read the plugin/SDK facts the client matrix pins (no import, no execution)."""
    plugin_package = source / "node_modules" / "@deepseek-ai" / "dsh-mcp-client" / "package.json"
    transport = source / "packages" / "mcp" / "mcp-client" / "src" / "transport.ts"
    if not plugin_package.is_file():
        return None, f"missing {plugin_package}"
    if not transport.is_file():
        return None, f"missing {transport}"
    plugin = json.loads(plugin_package.read_text(encoding="utf-8"))
    sdk_root = source / "node_modules" / ".pnpm"
    matches = sorted(
        sdk_root.glob(
            "@modelcontextprotocol+client@*/node_modules/@modelcontextprotocol/client/package.json"
        )
    )
    if not matches:
        return None, f"missing @modelcontextprotocol/client under {sdk_root}"
    sdk = json.loads(matches[-1].read_text(encoding="utf-8"))
    declared = (plugin.get("dependencies") or {}).get("@modelcontextprotocol/client")
    return (
        {
            "plugin_package": str(plugin_package),
            "plugin_name": plugin.get("name"),
            "plugin_version": plugin.get("version"),
            "declared_sdk_range": declared,
            "sdk_package": str(matches[-1]),
            "sdk_version": sdk.get("version"),
            "transport_module": str(transport),
        },
        None,
    )


def prerequisites() -> Prerequisites:
    """Resolve node + the external DSH tree; a gap is a reason, not an exception."""
    node, node_problem = _resolve_node()
    override = os.environ.get("ACP2_DSH_SOURCE")
    source = Path(override) if override else DEFAULT_DSH_SOURCE
    if not source.is_dir():
        return Prerequisites(
            node=node,
            dsh_source=None,
            problem=f"external DSH tree not found at {source} (set ACP2_DSH_SOURCE)",
        )
    facts, dsh_problem = _dsh_facts(source)
    if facts is None:
        return Prerequisites(node=node, dsh_source=source, problem=dsh_problem)
    problem = node_problem
    if problem is None and facts["sdk_version"] != "2.2.0":
        problem = (
            "plugin-resolved @modelcontextprotocol/client is "
            f"{facts['sdk_version']!r}, the matrix pins 2.2.0"
        )
    return Prerequisites(
        node=node,
        dsh_source=source,
        problem=problem,
        details={**facts, "node": str(node) if node else None},
    )


def node_runtime() -> Path:
    """Return the Node binary or raise with a ``NOT_VERIFIED`` reason."""
    node, problem = _resolve_node()
    if node is None:
        raise RuntimeError(f"NOT_VERIFIED: {problem}")
    return node


# --------------------------------------------------------------------------- #
# Loopback Controller stub (counts every request; creates nothing)
# --------------------------------------------------------------------------- #


@dataclass
class ControllerCounters:
    """Request accounting for the zero-side-effect proof."""

    health_reads: int = 0
    capabilities_reads: int = 0
    submit_posts: int = 0
    other_requests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "health_reads": self.health_reads,
            "capabilities_reads": self.capabilities_reads,
            "submit_posts": self.submit_posts,
            "other_requests": list(self.other_requests),
        }


class _ControllerHandler(http.server.BaseHTTPRequestHandler):
    counters: ControllerCounters = ControllerCounters()

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/api/health":
            self.counters.health_reads += 1
            self._send_json(
                200,
                {
                    "schema_version": 1,
                    "status": "ok",
                    "candidate_revision": "m0-08-live-harness",
                    "contract_version": "1.0.0",
                    "contract_digest": "0" * 64,
                    "contract_digest_scope": "test",
                    "read_only": True,
                    "started_at": "2026-10-26T00:00:00Z",
                },
            )
            return
        if self.path == "/api/capabilities":
            self.counters.capabilities_reads += 1
            self._send_json(
                200,
                {
                    "schema_version": 1,
                    "task_kinds": ["singlepoint"],
                    "workflows": ["singlepoint", "optimize"],
                    "backends": [{"name": "orca", "available": True}],
                    "capability_descriptors": None,
                    "limits": {"max_cores": 64},
                    "tools": sorted(EXPECTED_TOOLS),
                    "protocol_version": SERVER_VERSION,
                    "description": "m0-08 live handshake stub",
                },
            )
            return
        self.counters.other_requests.append(f"GET {self.path}")
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        if self.path == "/api/capabilities":
            self.counters.capabilities_reads += 1
            body = json.loads(raw or b"{}")
            payload = {
                "schema_version": 1,
                "task_kinds": ["singlepoint"],
                "workflows": ["singlepoint", "optimize"],
                "backends": [{"name": "orca", "available": True}],
                "capability_descriptors": None,
                "limits": {"max_cores": 64},
                "tools": sorted(EXPECTED_TOOLS),
                "protocol_version": SERVER_VERSION,
                "description": "m0-08 live handshake stub",
            }
            if body.get("workflow"):
                payload["workflow_contract"] = {
                    "workflow": body["workflow"],
                    "coverage": "partial",
                    "gaps": ["live harness fixture does not publish value-level gaps"],
                    "status_vocabulary": {"envelope": {"ok": "ok", "error": "error"}},
                }
                payload["research_tools"] = {"visible": True}
            self._send_json(200, payload)
            return
        if self.path == "/api/v1/jobs":
            # Counted, never served: a handshake must not create a task.
            self.counters.submit_posts += 1
            self._send_json(201, {"job_id": "must-not-be-created"})
            return
        self.counters.other_requests.append(f"POST {self.path}")
        self._send_json(404, {"detail": "not found"})

    def log_message(self, *args: Any) -> None:
        del args


class FakeController:
    """Context manager around a thread-served loopback Controller stub."""

    def __init__(self) -> None:
        self.counters = ControllerCounters()
        handler = type(
            "_BoundM008ControllerHandler",
            (_ControllerHandler,),
            {"counters": self.counters},
        )
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> FakeController:
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Launch descriptor + live client runner
# --------------------------------------------------------------------------- #


def launch_descriptor(
    controller_url: str,
    run_root: Path,
    *,
    protocol_version: str = SERVER_VERSION,
    log_level: str = "INFO",
) -> dict[str, Any]:
    """Build the stdio launch descriptor the acceptance scripts consume."""
    env = {
        "ACP_RUN_ROOT": str(run_root),
        "ACP_CONTROLLER_URL": controller_url,
        "ACP2_MCP_LOG_LEVEL": log_level,
        "PYTHONPATH": str(SRC),
    }
    return {
        "launch_version": 1,
        "server": {"name": "acp", "entrypoint": "acp_services.entrypoints.mcp"},
        "transport": "stdio",
        "protocol_version": protocol_version,
        "command": sys.executable,
        "args": [
            "-m",
            ADAPTER_MODULE,
            "mcp",
            "serve",
            "--transport",
            "stdio",
            "--controller",
            controller_url,
            "--log-level",
            log_level,
        ],
        "cwd": str(SRC),
        "env": env,
        "controller": {
            "transport": "loopback-http",
            "loopback_only": True,
            "start_controller": False,
            "shared_db": False,
        },
    }


def write_launch_descriptor(path: Path, descriptor: dict[str, Any]) -> Path:
    """Write *descriptor* as JSON and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8")
    return path


def run_live_client(
    launch_path: Path,
    out_dir: Path,
    *,
    node: Path | None = None,
    dsh_source: Path | None = None,
    scenario: str = "default",
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Run the frozen official client script and return its archived document."""
    if node is None:
        node = node_runtime()
    env = dict(os.environ)
    env["ACP2_DSH_SOURCE"] = str(dsh_source or DEFAULT_DSH_SOURCE)
    out_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            str(node),
            str(LIVE_CLIENT_SCRIPT),
            "--launch",
            str(launch_path),
            "--out",
            str(out_dir),
            "--scenario",
            scenario,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )
    document_path = out_dir / "dsh-client.json"
    document: dict[str, Any] = {}
    if document_path.is_file():
        document = json.loads(document_path.read_text(encoding="utf-8"))
    document["exit_code"] = completed.returncode
    document["stdout_tail"] = completed.stdout[-4000:]
    document["stderr_tail"] = completed.stderr[-4000:]
    return document


def wire_methods(document: dict[str, Any]) -> list[str]:
    """Return the session-process send methods recorded by the live client."""
    return [
        str(frame.get("message", {}).get("method"))
        for frame in document.get("wire", [])
        if frame.get("direction") == "send" and frame.get("message", {}).get("method")
    ]
