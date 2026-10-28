"""B05 process-level acceptance for the real MCP stdio adapter.

The tests start a real ``python -m acp mcp serve --transport stdio`` child
process (current worktree ``src`` on ``sys.path`` via ``cwd``) against a
loopback fake Controller.  They cover framing, sequential independent
requests, malformed/oversized/batch frames, unknown methods, protocol-version
negotiation, lifecycle gating, long single-line results, stdout/stderr
separation, client disconnect, EOF/signal cleanup and the no-task-creation
invariant.  The live Controller probe is recorded separately under
``reports/acp2_implementation/bb45a46/B/B05``.
"""

from __future__ import annotations

import http.server
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

PROTOCOL_VERSION = "2025-06-18"
EXPECTED_TOOLS = {
    "acp_capabilities",
    "acp_preflight",
    "acp_submit",
    "acp_status",
    "acp_cancel",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_analyze",
}
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"


class _ControllerState:
    def __init__(self) -> None:
        self.health_reads = 0
        self.capabilities_reads = 0
        self.submit_posts = 0
        self.long_description = "capability descriptor " * 500


class _ControllerHandler(http.server.BaseHTTPRequestHandler):
    state: _ControllerState

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/api/health":
            self.state.health_reads += 1
            self._send_json(
                200,
                {
                    "schema_version": 1,
                    "status": "ok",
                    "candidate_revision": "test-revision",
                    "contract_version": "1.0.0",
                    "contract_digest": "0" * 64,
                    "contract_digest_scope": "test",
                    "read_only": True,
                    "started_at": "2026-10-22T00:00:00Z",
                },
            )
            return
        if self.path == "/api/capabilities":
            self.state.capabilities_reads += 1
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
                    "protocol_version": PROTOCOL_VERSION,
                    "description": self.state.long_description,
                },
            )
            return
        if self.path.startswith("/api/v1/jobs/"):
            task_id = self.path.rsplit("/", 1)[-1]
            if task_id == "known-job":
                self._send_json(
                    200,
                    {
                        "id": "known-job",
                        "status": "completed",
                        "attempt": 1,
                        "revision": 2,
                    },
                )
            else:
                self._send_json(404, {"detail": "job not found"})
            return
        self._send_json(404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)
        if self.path == "/api/v1/jobs":
            self.state.submit_posts += 1
            self._send_json(201, {"job_id": "must-not-be-created"})
            return
        self._send_json(404, {"detail": "not found"})

    def log_message(self, *args: Any) -> None:
        del args


def _handler_type(state: _ControllerState) -> type[_ControllerHandler]:
    return type("_BoundControllerHandler", (_ControllerHandler,), {"state": state})


@pytest.fixture()
def controller() -> Any:
    state = _ControllerState()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _handler_type(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield state, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _McpProcess:
    def __init__(
        self,
        controller_url: str,
        run_root: Path,
        *,
        max_frame_bytes: int | None = None,
    ) -> None:
        args = [
            sys.executable,
            "-m",
            "acp",
            "mcp",
            "serve",
            "--transport",
            "stdio",
            "--controller",
            controller_url,
            "--log-level",
            "DEBUG",
        ]
        if max_frame_bytes is not None:
            args.extend(["--max-frame-bytes", str(max_frame_bytes)])
        env = dict(os.environ)
        env.update({"ACP_RUN_ROOT": str(run_root), "PYTHONUNBUFFERED": "1"})
        env.pop("ACP2_MCP_LOG_LEVEL", None)
        self.proc = subprocess.Popen(
            args,
            cwd=str(_SRC),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        self._buffer = b""
        self._stderr: bytes | None = None
        self.lines: list[str] = []
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None

    def __enter__(self) -> _McpProcess:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def send(self, payload: Any) -> None:
        self.send_raw((json.dumps(payload) + "\n").encode("utf-8"))

    def send_raw(self, data: bytes) -> None:
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def read_line(self, timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                raw, self._buffer = self._buffer[:index], self._buffer[index + 1 :]
                line = raw.decode("utf-8")
                self.lines.append(line)
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out reading MCP stdout")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                raise TimeoutError("timed out reading MCP stdout")
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise EOFError("MCP stdout closed")
            self._buffer += chunk

    def request(
        self,
        request_id: int,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self.send(message)
        return json.loads(self.read_line(timeout=timeout))

    def handshake(
        self,
        *,
        version: str = PROTOCOL_VERSION,
        declared: list[str] | None = None,
        request_id: int = 1,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "protocolVersion": version,
            "clientInfo": {"name": "acp2-process-test", "version": "1"},
        }
        if declared is not None:
            params["protocolVersions"] = declared
        return self.request(request_id, "initialize", params)

    def notify_initialized(self) -> None:
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def wait(self, timeout: float = 30.0) -> int:
        return self.proc.wait(timeout=timeout)

    def stderr_text(self) -> str:
        if self._stderr is None:
            self._stderr = self.proc.stderr.read() if self.proc.stderr else b""
        return self._stderr.decode("utf-8", errors="replace")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.close_stdin()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.stderr_text()


def _assert_stdout_is_protocol_only(process: _McpProcess) -> None:
    assert process.lines
    for line in process.lines:
        frame = json.loads(line)
        assert frame["jsonrpc"] == "2.0"


def _assert_stderr_has_no_protocol(process: _McpProcess) -> None:
    stderr = process.stderr_text()
    assert stderr.strip()
    for line in stderr.splitlines():
        assert not line.startswith('{"jsonrpc"')


def test_handshake_tools_list_capabilities_and_separation(controller: Any, tmp_path: Path) -> None:
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        initialized = process.handshake()
        assert "error" not in initialized, initialized
        result = initialized["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        assert result["supportedProtocolVersions"] == [PROTOCOL_VERSION]
        assert result["serverInfo"]["name"] == "acp-services-mcp"
        process.notify_initialized()

        listed = process.request(2, "tools/list", {})
        names = {tool["name"] for tool in listed["result"]["tools"]}
        assert names == EXPECTED_TOOLS
        assert all(tool["inputSchema"]["type"] == "object" for tool in listed["result"]["tools"])

        called = process.request(3, "tools/call", {"name": "acp_capabilities", "arguments": {}})
        payload = called["result"]
        assert payload["isError"] is False
        structured = payload["structuredContent"]
        text = json.loads(payload["content"][0]["text"])
        assert text == structured
        assert structured["status"] == "ok"
        assert structured["data"]["protocol_version"] == PROTOCOL_VERSION
        assert structured["data"]["description"] == state.long_description

        process.close_stdin()
        assert process.wait() == 0
        stderr = process.stderr_text()

    _assert_stdout_is_protocol_only(process)
    _assert_stderr_has_no_protocol(process)
    assert "mcp stdio transport ready" in stderr
    assert "MCP session initialized" in stderr
    assert state.submit_posts == 0
    assert state.capabilities_reads >= 1
    assert state.health_reads >= 1


def test_sequential_requests_are_independent(controller: Any, tmp_path: Path) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        first = process.request(10, "ping", {}, timeout=10)
        second = process.request(11, "ping", {}, timeout=10)
        assert first == {"jsonrpc": "2.0", "id": 10, "result": {}}
        assert second == {"jsonrpc": "2.0", "id": 11, "result": {}}
        process.close_stdin()
        assert process.wait() == 0


def test_malformed_frame_is_parse_error_and_stream_survives(
    controller: Any, tmp_path: Path
) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.send_raw(b"{not json\n")
        malformed = json.loads(process.read_line())
        assert malformed["error"]["code"] == -32700
        assert malformed["id"] is None
        recovered = process.request(2, "ping", {})
        assert recovered["id"] == 2
        assert recovered["result"] == {}
        process.close_stdin()
        assert process.wait() == 0
    assert "rejected malformed JSON frame" in process.stderr_text()


def test_oversized_frame_is_rejected_without_desync(controller: Any, tmp_path: Path) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root", max_frame_bytes=2048) as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.send_raw(b"{" + b"x" * 4096 + b"}\n")
        oversized = json.loads(process.read_line())
        assert oversized["error"]["code"] == -32600
        assert oversized["error"]["data"]["code"] == "frame_too_large"
        recovered = process.request(2, "ping", {})
        assert recovered["result"] == {}
        process.close_stdin()
        assert process.wait() == 0


def test_batch_frame_is_explicitly_rejected(controller: Any, tmp_path: Path) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.send(
            [
                {"jsonrpc": "2.0", "id": 9, "method": "ping"},
            ]
        )
        batch = json.loads(process.read_line())
        assert batch["error"]["code"] == -32600
        assert batch["error"]["data"]["code"] == "batch_not_supported"
        recovered = process.request(2, "ping", {})
        assert recovered["result"] == {}
        process.close_stdin()
        assert process.wait() == 0


def test_unknown_method_returns_jsonrpc_error(controller: Any, tmp_path: Path) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        unknown = process.request(2, "does/not/exist", {})
        assert unknown["error"]["code"] == -32601
        process.close_stdin()
        assert process.wait() == 0


def test_same_version_negotiation(controller: Any, tmp_path: Path) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        response = process.handshake(version=PROTOCOL_VERSION)
        assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
        process.close_stdin()
        assert process.wait() == 0


def test_unsupported_preferred_version_returns_server_version(
    controller: Any, tmp_path: Path
) -> None:
    """M0-08: the client's offered version (2025-11-25) is not a server error."""
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        response = process.handshake(version="2025-11-25")
        assert "error" not in response, response
        assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert response["result"]["supportedProtocolVersions"] == [PROTOCOL_VERSION]
        process.notify_initialized()
        listed = process.request(2, "tools/list", {})
        assert {tool["name"] for tool in listed["result"]["tools"]} == EXPECTED_TOOLS
        process.close_stdin()
        assert process.wait() == 0


def test_unknown_version_with_declared_hint_still_initializes(
    controller: Any, tmp_path: Path
) -> None:
    """No ``protocolVersions`` gate: an unknown declared set is recorded, not refused."""
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        response = process.handshake(version="1999-01-01", declared=["1999-01-01"])
        assert "error" not in response, response
        result = response["result"]
        assert result["protocolVersion"] == PROTOCOL_VERSION
        guidance = result["capabilities"]["extensions"]["acp.negotiation.v1"]
        assert guidance["requested_version"] == "1999-01-01"
        assert guidance["client_declared_versions"] == ["1999-01-01"]
        assert "extensions" not in result
        assert "negotiated" not in json.dumps(result["capabilities"])
        process.notify_initialized()
        listed = process.request(2, "tools/list", {})
        assert {tool["name"] for tool in listed["result"]["tools"]} == EXPECTED_TOOLS
        process.close_stdin()
        assert process.wait() == 0
    assert state.submit_posts == 0


def test_malformed_protocol_version_is_invalid_params_and_blocks_tools(
    controller: Any, tmp_path: Path
) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        process.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 20250618,
                    "clientInfo": {"name": "x", "version": "1"},
                },
            }
        )
        malformed = json.loads(process.read_line())
        assert malformed["error"]["code"] == -32602
        assert malformed["error"]["data"]["code"] == "invalid_protocol_version"
        blocked = process.request(2, "tools/list", {})
        assert blocked["error"]["data"]["code"] == "not_initialized"
        assert "result" not in blocked
        recovered = process.handshake(request_id=3)
        assert "error" not in recovered
        process.notify_initialized()
        listed = process.request(4, "tools/list", {})
        assert {tool["name"] for tool in listed["result"]["tools"]} == EXPECTED_TOOLS
        process.close_stdin()
        assert process.wait() == 0


def test_server_discover_probe_is_plain_unknown_method(controller: Any, tmp_path: Path) -> None:
    """An era-probing client must get -32601 before initialize (not not_initialized)."""
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        probe = process.request(
            1,
            "server/discover",
            {"protocolVersions": [PROTOCOL_VERSION], "clientInfo": {"name": "x", "version": "1"}},
        )
        assert probe["error"]["code"] == -32601
        assert (probe["error"].get("data") or {}).get("code") != "not_initialized"
        assert "result" not in probe
        assert "error" not in process.handshake(request_id=2)
        process.notify_initialized()
        listed = process.request(3, "tools/list", {})
        assert {tool["name"] for tool in listed["result"]["tools"]} == EXPECTED_TOOLS
        process.close_stdin()
        assert process.wait() == 0
    assert state.submit_posts == 0


def test_tools_before_initialize_are_rejected(controller: Any, tmp_path: Path) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        early = process.request(1, "tools/list", {})
        assert early["error"]["data"]["code"] == "not_initialized"
        assert "result" not in early
        assert "error" not in process.handshake(request_id=2)
        process.notify_initialized()
        listed = process.request(3, "tools/list", {})
        assert {tool["name"] for tool in listed["result"]["tools"]} == EXPECTED_TOOLS
        process.close_stdin()
        assert process.wait() == 0


def test_long_result_is_one_valid_json_line(controller: Any, tmp_path: Path) -> None:
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        called = process.request(2, "tools/call", {"name": "acp_capabilities", "arguments": {}})
        raw = process.lines[-1]
        assert len(raw.encode("utf-8")) > 8192
        assert "\n" not in raw
        assert called["result"]["structuredContent"]["data"]["description"] == (
            state.long_description
        )
        process.close_stdin()
        assert process.wait() == 0


def test_status_is_a_real_read_only_controller_call(controller: Any, tmp_path: Path) -> None:
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        known = process.request(
            2,
            "tools/call",
            {"name": "acp_status", "arguments": {"reference": "known-job"}},
        )
        assert known["result"]["isError"] is False
        assert known["result"]["structuredContent"]["data"]["state"] == "completed"

        missing = process.request(
            3,
            "tools/call",
            {"name": "acp_status", "arguments": {"reference": "missing-job"}},
        )
        assert missing["result"]["isError"] is True
        assert missing["result"]["structuredContent"]["error"]["code"] == "resource_not_ready"
        process.close_stdin()
        assert process.wait() == 0
    assert state.submit_posts == 0


def test_eof_cleanup_does_not_submit(controller: Any, tmp_path: Path) -> None:
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.close_stdin()
        assert process.wait() == 0
        stderr = process.stderr_text()
    assert "stdin EOF" in stderr
    assert state.submit_posts == 0


def test_client_disconnect_does_not_submit(controller: Any, tmp_path: Path) -> None:
    state, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.send({"jsonrpc": "2.0", "id": 99, "method": "ping"})
        try:
            process.proc.stdout.close()
        except OSError:
            pass
        process.close_stdin()
        assert process.wait() == 0
    assert state.submit_posts == 0


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_shutdown_is_clean(controller: Any, tmp_path: Path, signum: int) -> None:
    _, url = controller
    with _McpProcess(url, tmp_path / "run-root") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.proc.send_signal(signum)
        assert process.wait() == 0
        assert "shutdown signal" in process.stderr_text()


def test_protocol_negotiation_matrix(controller: Any, tmp_path: Path) -> None:
    state, url = controller
    cases: list[tuple[str, dict[str, Any]]] = []

    with _McpProcess(url, tmp_path / "matrix-same") as process:
        response = process.handshake()
        cases.append(
            (
                "same",
                {
                    "requested": PROTOCOL_VERSION,
                    "negotiated": response["result"]["protocolVersion"],
                    "error": None,
                },
            )
        )
        process.close_stdin()
        assert process.wait() == 0

    with _McpProcess(url, tmp_path / "matrix-unsupported") as process:
        response = process.handshake(version="2025-11-25", declared=["2025-11-25"])
        guidance = response["result"]["capabilities"]["extensions"]["acp.negotiation.v1"]
        cases.append(
            (
                "unsupported-version",
                {
                    "requested": "2025-11-25",
                    "negotiated": response["result"]["protocolVersion"],
                    "error": None,
                    "client_declared_versions": guidance["client_declared_versions"],
                },
            )
        )
        process.close_stdin()
        assert process.wait() == 0

    with _McpProcess(url, tmp_path / "matrix-malformed") as process:
        process.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": ["2025-06-18"]},
            }
        )
        response = json.loads(process.read_line())
        cases.append(
            (
                "malformed-version",
                {
                    "requested": None,
                    "negotiated": None,
                    "error": response["error"]["data"]["code"],
                    "error_code": response["error"]["code"],
                },
            )
        )
        process.close_stdin()
        assert process.wait() == 0

    with _McpProcess(url, tmp_path / "matrix-tools") as process:
        response = process.request(1, "tools/list", {})
        cases.append(
            (
                "tools-before-init",
                {
                    "requested": None,
                    "negotiated": None,
                    "error": response["error"]["data"]["code"],
                },
            )
        )
        process.close_stdin()
        assert process.wait() == 0

    with _McpProcess(url, tmp_path / "matrix-eof") as process:
        assert "error" not in process.handshake()
        process.notify_initialized()
        process.close_stdin()
        exit_code = process.wait()
        cases.append(
            (
                "notification-eof",
                {
                    "requested": PROTOCOL_VERSION,
                    "negotiated": PROTOCOL_VERSION,
                    "error": None,
                    "exit_code": exit_code,
                },
            )
        )

    for name, outcome in cases:
        print(f"MATRIX case={name} outcome={json.dumps(outcome, sort_keys=True)}")

    assert cases[0][1]["negotiated"] == PROTOCOL_VERSION
    assert cases[1][1]["negotiated"] == PROTOCOL_VERSION
    assert cases[1][1]["client_declared_versions"] == ["2025-11-25"]
    assert cases[2][1]["error"] == "invalid_protocol_version"
    assert cases[2][1]["error_code"] == -32602
    assert cases[3][1]["error"] == "not_initialized"
    assert cases[4][1]["exit_code"] == 0
    # Zero task side effects across the whole matrix (no process called a tool).
    assert state.submit_posts == 0
