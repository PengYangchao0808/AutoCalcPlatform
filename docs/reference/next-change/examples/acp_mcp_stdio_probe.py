"""Read-only, real-process MCP diagnostic accompanying the ACP 2.0 runbook."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

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


async def probe(args: argparse.Namespace) -> None:
    launch = json.loads(args.launch.read_text(encoding="utf-8"))
    command = launch["command"]
    argv = launch.get("args", [])
    env = launch.get("env", {})
    if not isinstance(command, str) or not Path(command).is_absolute():
        raise ValueError("command must be an absolute executable path")
    if not isinstance(argv, list) or not all(isinstance(v, str) for v in argv):
        raise ValueError("args must be a JSON list of strings")
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise ValueError("env must map strings to strings")
    cwd = Path(launch["cwd"])
    if not cwd.is_absolute() or not cwd.is_dir():
        raise ValueError("cwd must be an existing absolute directory")
    args.out.mkdir(parents=True, exist_ok=False)
    with (
        (args.out / "stderr.log").open("w", encoding="utf-8") as stderr,
        (args.out / "wire.jsonl").open("w", encoding="utf-8") as wire,
    ):
        process = await asyncio.create_subprocess_exec(
            command,
            *argv,
            cwd=cwd,
            env={**os.environ, **env},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            limit=4 * 1024 * 1024,
        )
        assert process.stdin is not None and process.stdout is not None

        def record(direction: str, message: object) -> None:
            wire.write(
                json.dumps({"direction": direction, "message": message}, ensure_ascii=False) + "\n"
            )
            wire.flush()

        async def send(message: dict) -> None:
            record("send", message)
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def request(number: int, method: str, params: dict) -> dict:
            await send({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
            # Whole request deadline, including any intervening notifications.
            deadline = asyncio.get_running_loop().time() + args.timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                line = await asyncio.wait_for(process.stdout.readline(), remaining)
                if not line:
                    raise RuntimeError(f"server closed stdout during {method}")
                record("receive_raw", line.decode("utf-8", errors="replace"))
                message = json.loads(line)
                record("receive", message)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("not a JSON-RPC 2.0 object")
                if "id" not in message and "method" in message:
                    continue
                if message.get("id") != number or "method" in message:
                    raise ValueError("unexpected response or unsupported server request")
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message["result"]

        try:
            initialized = await request(
                1,
                "initialize",
                {
                    "protocolVersion": args.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "acp2-readonly-probe", "version": "1"},
                },
            )
            if initialized.get("protocolVersion") != args.protocol_version:
                raise ValueError("probe supports only its explicitly requested protocol")
            await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            listed = await request(2, "tools/list", {})
            tools = listed["tools"]
            if (
                len(tools) != len(EXPECTED_TOOLS)
                or {tool["name"] for tool in tools} != EXPECTED_TOOLS
            ):
                raise ValueError("tool allowlist differs from this frozen probe")
            if any(tool.get("inputSchema", {}).get("type") != "object" for tool in tools):
                raise ValueError("tool inputSchema must be an object schema")
            capabilities = await request(
                3, "tools/call", {"name": "acp_capabilities", "arguments": {}}
            )
            if capabilities.get("isError"):
                raise ValueError("acp_capabilities returned a tool error")
            structured = capabilities["structuredContent"]
            texts = [item["text"] for item in capabilities["content"] if item.get("type") == "text"]
            if len(texts) != 1 or json.loads(texts[0]) != structured:
                raise ValueError("text and structuredContent disagree")
            if structured.get("status") != "ok":
                raise ValueError("capabilities did not return status=ok")
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            (args.out / "process.json").write_text(
                json.dumps({"pid": process.pid, "exit_code": process.returncode}) + "\n",
                encoding="utf-8",
            )
        if process.returncode != 0:
            raise RuntimeError(f"server shutdown exit code: {process.returncode}")
    print("PASS: read-only stdio diagnostic; full MCP/DSH acceptance remains separate")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--protocol-version", default="2025-06-18")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    asyncio.run(probe(args))


if __name__ == "__main__":
    main()
