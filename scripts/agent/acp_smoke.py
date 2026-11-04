#!/usr/bin/env python3
"""P0 smoke test: local DSH ACP profile + the ACP-MCP server, no model calls.

This is the executable form of the 2026-11-02 probe recorded in
``docs/reference/next-change/ACP_Web_Agent_DSH_Bridge_Plan_20261102.md`` §2.6.

What it proves (and what it never does):

* ``initialize`` answers ACP v1 with ``deepseek-harness-acp``;
* ``session/new`` accepts a **stdio MCP declaration** pointing at
  ``acp mcp serve`` and publishes the session (DSH rolls the session back when
  the MCP connection or its tool discovery fails, so a published session is
  evidence that discovery succeeded);
* the MCP child logs ``MCP session initialized (protocolVersion=…)``;
* ``session/list`` and ``session/close`` answer.

It sends **no prompt**, so no model request is made and no job is created.
Exit codes follow the repository three-state rule:

* ``0`` PASS — every assertion above held;
* ``1`` FAIL — the prerequisite existed (DSH + Controller) and something failed;
* ``2`` NOT_VERIFIED — DSH or the Controller is unavailable, so the row could
  not run. Never reported as PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PASS, FAIL, NOT_VERIFIED = 0, 1, 2

DEFAULT_CONTROLLER = "http://127.0.0.1:8765"
DEFAULT_MCP_LAUNCH = (
    "cd {repo} && export PYTHONPATH=$PWD/src && exec {python} -m acp mcp serve "
    "--transport stdio --controller {controller} --log-level INFO"
)
DEFAULT_WSL_PYTHON = "/root/miniforge3/bin/python"


def default_dsh() -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    return (
        Path(local)
        / "Programs"
        / "DeepSeek Harness"
        / "resources"
        / "runtime"
        / "cli"
        / "bin"
        / "dsh.cmd"
    )


def default_repo() -> Path:
    return Path(__file__).resolve().parents[2]


class AcpProcess:
    """Minimal bidirectional newline JSON-RPC client over the ``dsh`` stdio server."""

    def __init__(self, dsh: Path, profile: str, cwd: Path) -> None:
        self._stdout: queue.Queue[str] = queue.Queue()
        self.stderr: list[str] = []
        self.proc = subprocess.Popen(
            ["cmd.exe", "/c", str(dsh), "--profile", profile],
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._stdout.put(line.rstrip("\n"))

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip("\n"))

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def wait(self, request_id: int, timeout: float) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._stdout.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    return None
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message
        return None

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def controller_health(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/health", timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"controller unreachable: {exc}"
    return True, str(body.get("contract_version", "?"))


def run(args: argparse.Namespace) -> int:
    dsh = Path(args.dsh).expanduser()
    repo = Path(args.cwd).expanduser().resolve()

    if os.name != "nt":
        print(f"NOT_VERIFIED(not_windows): this launcher drives the Windows DSH CLI; got {os.name}")
        return NOT_VERIFIED
    if not dsh.is_file():
        print(f"NOT_VERIFIED(dsh_missing): {dsh}")
        return NOT_VERIFIED
    if not repo.is_dir():
        print(f"FAIL(repo_missing): {repo}")
        return FAIL

    healthy, detail = controller_health(args.controller)
    if not healthy:
        print(f"NOT_VERIFIED({detail})")
        return NOT_VERIFIED
    print(f"controller: {detail}")

    wsl_repo = args.repo_in_wsl or f"/mnt/{repo.drive[0].lower()}{repo.as_posix()[2:]}"
    launch = args.mcp_launch.format(
        repo=Path(wsl_repo).as_posix(),
        python=args.wsl_python,
        controller=args.controller,
    )
    mcp_entry = {
        "type": "stdio",
        "name": "acp",
        "command": args.wsl,
        "args": ["-d", args.distro, "-e", "bash", "-lc", launch],
        "env": [],
    }

    process = AcpProcess(dsh, args.profile, repo)
    failures: list[str] = []
    try:
        process.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": 1, "clientCapabilities": {}},
            }
        )
        init = process.wait(1, args.timeout)
        if not init or "result" not in init:
            print(f"FAIL(initialize): {json.dumps(init)[:400] if init else 'no response'}")
            return FAIL
        info = init["result"].get("agentInfo", {})
        caps = init["result"].get("agentCapabilities", {})
        proto = init["result"].get("protocolVersion")
        print(f"initialize: {info.get('name')} {info.get('version')} proto={proto}")
        if not caps.get("sessionCapabilities"):
            failures.append("initialize: sessionCapabilities missing")

        process.send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"cwd": str(repo), "mcpServers": [mcp_entry]},
            }
        )
        created = process.wait(2, args.timeout)
        if not created or "result" not in created:
            print(f"FAIL(session_new): {json.dumps(created)[:600] if created else 'no response'}")
            print("stderr_tail:\n" + "\n".join(process.stderr[-8:]))
            return FAIL
        session_id = created["result"].get("sessionId")
        options = [item.get("id") for item in created["result"].get("configOptions", [])]
        print(f"session/new: {session_id} options={options}")

        process.send({"jsonrpc": "2.0", "id": 3, "method": "session/list", "params": {}})
        listed = process.wait(3, args.timeout)
        if not listed or "result" not in listed:
            detail = json.dumps(listed)[:300] if listed else "no response"
            failures.append(f"session/list failed: {detail}")
        else:
            sessions = listed["result"].get("sessions", [])
            print(f"session/list: {len(sessions)} session(s)")

        mcp_ready = [line for line in process.stderr if "MCP session initialized" in line]
        if not mcp_ready:
            failures.append("MCP child never logged 'MCP session initialized'")
        else:
            print("mcp: " + mcp_ready[-1].strip()[-120:])

        process.send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "session/close",
                "params": {"sessionId": session_id},
            }
        )
        if process.wait(4, args.timeout) is None:
            failures.append("session/close timed out")
        else:
            print("session/close: ok")
    finally:
        process.close()

    if failures:
        print("FAIL:")
        for item in failures:
            print(f"  - {item}")
        print("stderr_tail:\n" + "\n".join(process.stderr[-6:]))
        return FAIL
    print("PASS: DSH ACP + ACP-MCP session published (no model call, no job created)")
    return PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--dsh", default=str(default_dsh()), help="dsh.cmd path")
    parser.add_argument("--profile", default="acp", help="dsh profile (default: acp)")
    parser.add_argument(
        "--cwd", default=str(default_repo()), help="session working directory (absolute)"
    )
    parser.add_argument(
        "--repo-in-wsl", default="", help="repo path as seen from WSL (auto-detected)"
    )
    parser.add_argument("--controller", default=DEFAULT_CONTROLLER)
    parser.add_argument("--wsl", default=r"C:\Windows\System32\wsl.exe")
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--wsl-python", default=DEFAULT_WSL_PYTHON)
    parser.add_argument("--mcp-launch", default=DEFAULT_MCP_LAUNCH)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        return FAIL


if __name__ == "__main__":
    sys.exit(main())
