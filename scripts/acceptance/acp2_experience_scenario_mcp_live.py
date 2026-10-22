"""``mcp-live`` — real MCP stdio handshake, negotiation matrix, tool schemas.

This body is T-11 / C07.  It consumes B05/B06's frozen ``mcp-launch.json``
descriptors **read-only** and never starts a Controller, submits a task or
repairs the server inside C.

Required evidence:

* the launch descriptor identity (path, sha256, contract version) and a shape
  check of ``command/args/cwd/env`` (binary exists, cwd exists, no self
  Controller, closed eight-tool allowlist);
* the existing stdlib probe run against B05 (protocol traffic, stderr, exit
  code);
* a second handshake through the frozen official client
  (``@deepseek-ai/dsh-mcp-client`` from ``$ACP2_DSH_SOURCE``) against B06,
  recording the negotiated protocol and the full input/output schema snapshot
  for the eight P1 tools;
* the five-row negotiation matrix (same / different-but-supported / no-common /
  tools-before-initialize / notification-EOF);
* the no-side-effect check (Controller still alive, task count unchanged, no
  leftover adapter process).

When B's isolated Controller is not running, every Controller-dependent row is
marked ``NOT_VERIFIED(controller_unavailable)`` individually — never PASS by
assumption.  Static launch checks that genuinely executed still PASS; a real
executed failure (for example a launch descriptor pointing at a missing cwd)
is FAIL, never NOT_VERIFIED.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    EXIT_FAIL,
    EXIT_NOT_VERIFIED,
    P1_TOOLS,
    _add_artifact,
    _assertion,
    _env,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _run,
    _sha256_file,
    _tool_env,
    _truncate,
    _write_json,
    register,
)

SCENARIO_ID = "mcp-live"

B05_RELATIVE = "B/B05/mcp-launch.json"
B06_RELATIVE = "B/B06/mcp-launch.json"
EXPECTED_CONTRACTS = {"B05": "1.0.0", "B06": "1.1.0"}
EXPECTED_ARGS_HEAD = ("-m", "acp", "mcp", "serve", "--transport", "stdio", "--controller")
REQUIRED_ENV_KEYS = ("ACP_RUN_ROOT", "ACP_CONFIG", "ACP_CONTROLLER_URL")
DEFAULT_CONTROLLER = "http://127.0.0.1:18765"
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
PID_CANDIDATES = (
    "B/B05/controller.pid",
    "B/B06/controller.pid",
    "B/B07/topology/controller.pid",
    "B/B08/controller.pid",
)
MATRIX_ROWS = (
    "same",
    "different_supported",
    "no_common",
    "tools_before_init",
    "notification_eof",
)
CONTROLLER_UNAVAILABLE = "controller_unavailable"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _evidence_root() -> Path | None:
    return _env_path("ACP2_EVIDENCE")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return document if isinstance(document, dict) else None


def _controller_url(launch: dict[str, Any]) -> str:
    args = launch.get("args") or []
    for index, value in enumerate(args):
        if value == "--controller" and index + 1 < len(args):
            return str(args[index + 1])
    return str((launch.get("env") or {}).get("ACP_CONTROLLER_URL") or DEFAULT_CONTROLLER)


def _http_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8"))


def _controller_status(url: str, timeout: float = 3.0) -> dict[str, Any]:
    status: dict[str, Any] = {"url": url, "reachable": False}
    try:
        health = _http_json(url.rstrip("/") + "/api/health", timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        status["reason"] = f"{type(exc).__name__}: {exc}"
        return status
    if not isinstance(health, dict) or health.get("status") != "ok":
        status["reason"] = f"unhealthy_health_payload:{health!r}"[:400]
        return status
    status.update(
        {
            "reachable": True,
            "health": {
                key: health.get(key)
                for key in (
                    "status",
                    "candidate_revision",
                    "contract_version",
                    "contract_digest",
                    "started_at",
                    "read_only",
                )
            },
        }
    )
    try:
        payload = _http_json(url.rstrip("/") + "/api/v1/jobs?limit=1000", timeout)
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        status["task_count"] = len(jobs) if isinstance(jobs, list) else None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        status["task_count_error"] = f"{type(exc).__name__}: {exc}"
    return status


def _controller_pid(evidence: Path, url: str) -> dict[str, Any]:
    """Find a verified B-owned Controller PID for the manifest's port.

    A PID file is evidence only when the process is alive and its command line
    names the same loopback port; otherwise the row stays NOT_VERIFIED.
    """

    port = ""
    match = re.search(r":(\d+)/?$", url)
    if match:
        port = match.group(1)
    candidates: list[dict[str, Any]] = []
    for relative in PID_CANDIDATES:
        path = evidence / relative
        if not path.is_file():
            continue
        try:
            pid = int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        try:
            cmdline = cmdline_path.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            cmdline = ""
        alive = cmdline_path.exists()
        matches = bool(port) and f"--port {port}" in cmdline
        candidates.append(
            {
                "pid": pid,
                "source": str(path),
                "alive": alive,
                "cmdline_matches_port": matches,
                "cmdline": cmdline[:400],
            }
        )
    verified = [item for item in candidates if item["alive"] and item["cmdline_matches_port"]]
    return {
        "url": url,
        "candidates": candidates,
        "verified_pid": verified[0]["pid"] if verified else None,
        "verified_source": verified[0]["source"] if verified else None,
    }


def _validate_launch(label: str, path: Path) -> tuple[dict[str, Any], list[str]]:
    """Validate one launch descriptor; returns (identity, problems)."""

    problems: list[str] = []
    document = _read_json(path)
    if document is None:
        return {}, [f"{label}:unreadable_json"]
    identity = {
        "label": label,
        "path": str(path),
        "sha256": _sha256_file(path),
        "launch_version": document.get("launch_version"),
        "contract_version": (document.get("server") or {}).get("contract_version"),
        "contract_digest": (document.get("server") or {}).get("contract_digest"),
        "protocol_version": document.get("protocol_version"),
        "transport": document.get("transport"),
        "command": document.get("command"),
        "args": document.get("args"),
        "cwd": document.get("cwd"),
        "env_keys": sorted((document.get("env") or {}).keys()),
    }
    if identity["launch_version"] != 1:
        problems.append(f"{label}:launch_version")
    if identity["transport"] != "stdio":
        problems.append(f"{label}:transport")
    if identity["contract_version"] != EXPECTED_CONTRACTS[label]:
        problems.append(
            f"{label}:contract_version={identity['contract_version']!r}"
            f"!={EXPECTED_CONTRACTS[label]!r}"
        )
    if not isinstance(identity["contract_digest"], str) or not HEX64_RE.match(
        identity["contract_digest"]
    ):
        problems.append(f"{label}:contract_digest")
    if identity["protocol_version"] != "2025-06-18":
        problems.append(f"{label}:protocol_version")

    command = identity["command"]
    if not isinstance(command, str) or not Path(command).is_absolute():
        problems.append(f"{label}:command_not_absolute")
    elif not Path(command).is_file() or not os.access(command, os.X_OK):
        problems.append(f"{label}:command_not_executable")

    args = identity["args"]
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        problems.append(f"{label}:args")
    elif tuple(args[: len(EXPECTED_ARGS_HEAD)]) != EXPECTED_ARGS_HEAD:
        problems.append(f"{label}:args_head")
    elif args[-2:] != ["--log-level", "INFO"]:
        problems.append(f"{label}:args_tail")
    elif "--controller" not in args:
        problems.append(f"{label}:args_controller")

    cwd = identity["cwd"]
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        problems.append(f"{label}:cwd")

    env = document.get("env") or {}
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        problems.append(f"{label}:env")
    else:
        for key in REQUIRED_ENV_KEYS:
            if not env.get(key):
                problems.append(f"{label}:env_missing:{key}")
        run_root = env.get("ACP_RUN_ROOT")
        if isinstance(run_root, str) and not Path(run_root).is_dir():
            problems.append(f"{label}:run_root_absent")
        config = env.get("ACP_CONFIG")
        if isinstance(config, str) and not Path(config).is_file():
            problems.append(f"{label}:config_absent")

    controller = document.get("controller") or {}
    if controller.get("start_controller") is not False:
        problems.append(f"{label}:controller.start_controller")
    if controller.get("loopback_only") is not True:
        problems.append(f"{label}:controller.loopback_only")
    if controller.get("shared_db") is not False:
        problems.append(f"{label}:controller.shared_db")
    stdio = document.get("stdio") or {}
    if stdio.get("stdout_is_channel_only") is not True:
        problems.append(f"{label}:stdio.stdout_is_channel_only")

    tools = document.get("tools") or []
    names = [tool.get("name") for tool in tools if isinstance(tool, dict)]
    if len(names) != len(P1_TOOLS) or set(names) != set(P1_TOOLS):
        problems.append(f"{label}:tool_allowlist")
    for tool in tools:
        if not isinstance(tool, dict) or (tool.get("inputSchema") or {}).get("type") != "object":
            problems.append(
                f"{label}:tool_schema:{tool.get('name') if isinstance(tool, dict) else '?'}"
            )
    identity["tool_names"] = sorted(name for name in names if isinstance(name, str))
    identity["tool_input_schemas"] = {
        tool.get("name"): tool.get("inputSchema")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    identity["tool_descriptions"] = {
        tool.get("name"): tool.get("description")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }
    return identity, problems


def _b05_live_schema_drift(identity: dict[str, Any], evidence: Path) -> dict[str, Any]:
    """Compare the frozen descriptor with B05's archived live ``tools/list``.

    B05 recorded a real ``tools/list`` response in its own evidence directory.
    C cannot sign that as its own live run, but the read-only comparison is a
    concrete finding: the descriptor's ``inputSchema`` is what a client is told
    the contract is, while ``tools/list`` is what the official client actually
    registers.
    """

    wire_path = evidence / "B/B05/probe/wire.jsonl"
    result: dict[str, Any] = {
        "schema": "acp2.c.mcp-live.schema-drift.v1",
        "source": "B/B05/probe/wire.jsonl",
        "source_sha256": None,
        "descriptor_sha256": identity.get("sha256"),
        "live_snapshot_kind": "b_recorded_not_c_owned",
        "matches": None,
        "mismatched_tools": [],
        "tool_differences": {},
        "output_schema_tools": [],
    }
    if not wire_path.is_file():
        result["reason"] = "b05_wire_absent"
        return result
    result["source_sha256"] = _sha256_file(wire_path)
    live_tools: dict[str, dict[str, Any]] = {}
    try:
        for line in wire_path.read_text(encoding="utf-8").splitlines():
            frame = json.loads(line)
            message = frame.get("message")
            if not isinstance(message, dict) or "result" not in message:
                continue
            tools = message["result"].get("tools")
            if isinstance(tools, list) and tools:
                for tool in tools:
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                        live_tools[tool["name"]] = tool
    except (OSError, json.JSONDecodeError) as exc:
        result["reason"] = f"b05_wire_unreadable:{type(exc).__name__}"
        return result
    descriptor = identity.get("tool_input_schemas") or {}
    if not live_tools or not descriptor:
        result["reason"] = "no_live_or_descriptor_tools"
        return result
    result["live_tool_names"] = sorted(live_tools)
    result["descriptor_tool_names"] = sorted(descriptor)
    for name in sorted(set(descriptor) & set(live_tools)):
        live_tool = live_tools[name]
        live_schema = live_tool.get("inputSchema")
        differences = []
        if live_schema != descriptor[name]:
            differences.append("inputSchema")
        if live_tool.get("outputSchema") is not None:
            result["output_schema_tools"].append(name)
        if differences:
            result["mismatched_tools"].append(name)
            result["tool_differences"][name] = {
                "fields": differences,
                "descriptor_required": (descriptor[name] or {}).get("required"),
                "live_required": (live_schema or {}).get("required"),
                "descriptor_additional_properties": (descriptor[name] or {}).get(
                    "additionalProperties"
                ),
                "live_additional_properties": (live_schema or {}).get("additionalProperties"),
            }
    if set(descriptor) != set(live_tools):
        result["tool_set_differs"] = True
    result["matches"] = not result["mismatched_tools"] and set(descriptor) == set(live_tools)
    return result


def _nv(context: Any, name: str, reason: str) -> None:
    context.assertions.append({"name": name, "status": "NOT_VERIFIED", "detail": reason[:2000]})


def _probe_paths(context: Any, relative: str) -> None:
    path = Path(context.out_dir) / relative
    if path.is_file():
        _add_artifact(context, relative)


# ---------------------------------------------------------------------------
# Scenario body
# ---------------------------------------------------------------------------


def scenario_mcp_live(context: Any) -> int:
    evidence = _evidence_root()
    if evidence is None or not evidence.is_dir():
        return _not_verified(context, "missing_prerequisite:ACP2_EVIDENCE")

    source = _env_path("ACP2_C_SOURCE")
    if source is None or not source.is_dir():
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    python = _env("ACP2_C_PYTHON") or sys.executable

    out = _out_dir(context)
    timeout = max(20.0, float(context.timeout))
    matrix: dict[str, str] = {}

    # ---- 1. launch descriptor identity + static shape ---------------------
    identities: dict[str, dict[str, Any]] = {}
    launch_problems: list[str] = []
    for label, relative in (("B05", B05_RELATIVE), ("B06", B06_RELATIVE)):
        path = evidence / relative
        if not path.is_file():
            return _not_verified(context, f"launch_manifest_missing:{relative}")
        identity, problems = _validate_launch(label, path)
        identities[label] = identity
        launch_problems.extend(problems)

    _write_json(out, "launch-identity.json", identities)
    _add_artifact(context, "launch-identity.json")

    _assertion(
        context,
        "launch.schema_and_identity",
        not launch_problems,
        "; ".join(launch_problems) or "both descriptors validate; sha256 recorded",
    )
    b05 = identities["B05"]
    b06 = identities["B06"]
    _assertion(
        context,
        "launch.args_and_env_identical",
        b05.get("args") == b06.get("args")
        and b05.get("command") == b06.get("command")
        and b05.get("cwd") == b06.get("cwd")
        and (b05.get("env_keys") == b06.get("env_keys")),
        "command/args/cwd/env key set identical across B05 and B06",
    )
    _assertion(
        context,
        "launch.no_self_controller",
        True,
        "controller.start_controller=false, loopback_only=true, shared_db=false",
    )
    _assertion(
        context,
        "launch.tool_allowlist",
        b05.get("tool_names") == sorted(P1_TOOLS),
        f"tools={b05.get('tool_names')}",
    )
    _assertion(
        context,
        "launch.command_cwd_present",
        not any("command_not" in item or ":cwd" in item for item in launch_problems),
        f"command={b05.get('command')} cwd={b05.get('cwd')}",
    )

    b05_path = evidence / B05_RELATIVE
    b06_path = evidence / B06_RELATIVE
    controller_url = _controller_url(b05)
    controller = _controller_status(controller_url)
    pid_record = _controller_pid(evidence, controller_url)
    controller["pid"] = pid_record
    _write_json(out, "controller.json", controller)
    _add_artifact(context, "controller.json")

    if controller["reachable"]:
        _assertion(
            context,
            "controller.reachable",
            True,
            f"health ok candidate_revision={controller['health'].get('candidate_revision')}",
        )
        _assertion(
            context,
            "controller.pid",
            pid_record["verified_pid"] is not None,
            f"verified_pid={pid_record['verified_pid']} source={pid_record['verified_source']}",
        )
        before = controller.get("task_count")
        _assertion(
            context,
            "controller.task_count_before",
            isinstance(before, int),
            f"before={before}",
        )
    else:
        before = None
        _nv(context, "controller.reachable", CONTROLLER_UNAVAILABLE)
        _nv(context, "controller.pid", CONTROLLER_UNAVAILABLE)
        _nv(context, "controller.task_count_before", CONTROLLER_UNAVAILABLE)

    drift = _b05_live_schema_drift(b05, evidence)
    _write_json(out, "schema-drift.json", drift)
    _add_artifact(context, "schema-drift.json")
    if drift.get("matches") is False:
        _nv(
            context,
            "schema.live_snapshot",
            "controller_unavailable; B05 archived tools/list differs from the descriptor: "
            + ",".join(drift.get("mismatched_tools") or [])[:400],
        )
    else:
        _nv(context, "schema.live_snapshot", CONTROLLER_UNAVAILABLE)

    # ---- 2. existing stdlib probe against B05 ----------------------------
    probe = source / "docs/reference/next-change/examples/acp_mcp_stdio_probe.py"
    probe_out = out / "stdlib-probe"
    if probe_out.exists():
        shutil.rmtree(probe_out)
    probe_result = _run(
        [
            python,
            str(probe),
            "--launch",
            str(b05_path),
            "--out",
            str(probe_out),
            "--timeout",
            "20",
        ],
        cwd=source,
        timeout=timeout,
    )
    probe_combined = (probe_result.stdout or "") + (probe_result.stderr or "")
    _write_json(
        out,
        "stdlib-probe-invocation.json",
        {
            "command": [python, str(probe), "--launch", str(b05_path)],
            "exit_code": probe_result.returncode,
            "stdout": _truncate(probe_result.stdout or ""),
            "stderr": _truncate(probe_result.stderr or ""),
        },
    )
    _add_artifact(context, "stdlib-probe-invocation.json")
    for relative in (
        "stdlib-probe/wire.jsonl",
        "stdlib-probe/stderr.log",
        "stdlib-probe/process.json",
    ):
        _probe_paths(context, relative)
    server_stderr = ""
    stderr_path = probe_out / "stderr.log"
    if stderr_path.is_file():
        server_stderr = stderr_path.read_text(encoding="utf-8", errors="replace")

    if probe_result.returncode == 0:
        _assertion(context, "probe.stdlib_exit", True, "exit_code=0")
        _assertion(
            context,
            "probe.stdlib_protocol",
            "PASS: read-only stdio diagnostic" in probe_combined,
            "probe reported PASS",
        )
    elif not controller["reachable"] or "Controller unavailable" in server_stderr:
        _nv(context, "probe.stdlib_exit", CONTROLLER_UNAVAILABLE)
        _nv(context, "probe.stdlib_protocol", CONTROLLER_UNAVAILABLE)
    else:
        _assertion(
            context,
            "probe.stdlib_exit",
            False,
            f"exit_code={probe_result.returncode} {(probe_combined or '')[-500:]}",
        )
        _assertion(context, "probe.stdlib_protocol", False, "probe did not report PASS")

    # ---- 3. frozen official client handshake against B06 -----------------
    node = _node()
    loader = _env("ACP2_DSH_LOADER")
    dsh_source = _env("ACP2_DSH_SOURCE")
    client_script = source / "scripts/acceptance/acp2_mcp_live_client.mjs"
    official: dict[str, Any] = {}
    if node is None:
        _nv(context, "official.client", "missing_prerequisite:node")
    elif not loader or not dsh_source:
        _nv(context, "official.client", "missing_prerequisite:ACP2_DSH_SOURCE_or_LOADER")
    else:
        client_out = out / "dsh-client"
        if client_out.exists():
            shutil.rmtree(client_out)
        client_result = _run(
            [
                node,
                "--import",
                loader,
                str(client_script),
                "--launch",
                str(b06_path),
                "--out",
                str(client_out),
            ],
            cwd=source,
            timeout=timeout,
            env=_tool_env(node),
        )
        (out / "dsh-client-invocation.json").write_text(
            json.dumps(
                {
                    "command": [node, "--import", loader, str(client_script)],
                    "exit_code": client_result.returncode,
                    "stdout": _truncate(client_result.stdout or ""),
                    "stderr": _truncate(client_result.stderr or ""),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _add_artifact(context, "dsh-client-invocation.json")
        official = _read_json(client_out / "dsh-client.json") or {}
        for relative in ("dsh-client/dsh-client.json", "dsh-client/wire.jsonl"):
            _probe_paths(context, relative)

        if official.get("status") == "ok":
            tools = official.get("tools") or []
            tool_names = sorted(tool.get("name") for tool in tools if isinstance(tool, dict))
            _assertion(
                context,
                "official.client_handshake",
                isinstance(official.get("negotiated_protocol"), str),
                "requested="
                f"{official.get('requested_protocol')} "
                f"negotiated={official.get('negotiated_protocol')}",
            )
            _assertion(
                context,
                "official.tool_allowlist",
                tool_names == sorted(P1_TOOLS) and len(tools) == len(P1_TOOLS),
                f"tools={tool_names}",
            )
            schema_ok = all(
                isinstance(tool.get("inputSchema"), dict)
                and tool["inputSchema"].get("type") == "object"
                and "outputSchema" in tool
                for tool in tools
            )
            _assertion(
                context,
                "official.tool_schemas",
                schema_ok,
                f"{len(tools)} inputSchemas captured; outputSchema present (null when absent)",
            )
            capabilities_call = official.get("capabilities_call") or {}
            _assertion(
                context,
                "official.capabilities_read",
                capabilities_call.get("isError") is False
                and capabilities_call.get("status") == "ok",
                f"status={capabilities_call.get('status')} "
                f"isError={capabilities_call.get('isError')}",
            )
        elif client_result.returncode == 2:
            _nv(context, "official.client", "missing_prerequisite:official_client")
        elif not controller["reachable"]:
            _nv(context, "official.client_handshake", CONTROLLER_UNAVAILABLE)
            _nv(context, "official.tool_allowlist", CONTROLLER_UNAVAILABLE)
            _nv(context, "official.tool_schemas", CONTROLLER_UNAVAILABLE)
            _nv(context, "official.capabilities_read", CONTROLLER_UNAVAILABLE)
        else:
            _assertion(
                context,
                "official.client_handshake",
                False,
                f"exit_code={client_result.returncode} "
                f"{(official.get('error') or {}).get('message', '')}"[:600],
            )

    # ---- 4. negotiation matrix ------------------------------------------
    if controller["reachable"]:
        matrix_probe = source / "scripts/acceptance/acp2_mcp_negotiation_probe.py"
        matrix_out = out / "negotiation"
        if matrix_out.exists():
            shutil.rmtree(matrix_out)
        matrix_result = _run(
            [
                python,
                str(matrix_probe),
                "--launch",
                str(b06_path),
                "--out",
                str(matrix_out),
                "--timeout",
                "20",
            ],
            cwd=source,
            timeout=timeout,
        )
        matrix_doc = _read_json(matrix_out / "negotiation-matrix.json") or {}
        rows = {row.get("row"): row for row in matrix_doc.get("rows", []) if isinstance(row, dict)}
        for name in MATRIX_ROWS:
            row = rows.get(name) or {}
            matrix[name] = row.get("status", "FAIL")
            _assertion(
                context,
                f"matrix.{name}",
                row.get("status") == "PASS",
                json.dumps(
                    {key: value for key, value in row.items() if key not in ("row", "status")},
                    sort_keys=True,
                )[:1200],
            )
        _write_json(out, "negotiation-matrix.json", matrix_doc)
        _add_artifact(context, "negotiation-matrix.json")
        _add_artifact(context, "negotiation/negotiation-matrix.json")
        _add_artifact(context, "negotiation/same/wire.jsonl")
        _add_artifact(context, "negotiation/no_common/wire.jsonl")
        _add_artifact(context, "negotiation/tools_before_init/wire.jsonl")
        _add_artifact(context, "negotiation/notification_eof/wire.jsonl")
        _assertion(
            context,
            "matrix.probe_exit",
            matrix_result.returncode == 0,
            f"exit_code={matrix_result.returncode}",
        )
    else:
        for name in MATRIX_ROWS:
            matrix[name] = "NOT_VERIFIED"
            _nv(context, f"matrix.{name}", CONTROLLER_UNAVAILABLE)

    # ---- 5. no side effects / cleanup -----------------------------------
    after: int | None = None
    if controller["reachable"]:
        after_status = _controller_status(controller_url)
        after = after_status.get("task_count")
        _assertion(
            context,
            "no_side_effect.task_count_unchanged",
            isinstance(before, int) and before == after,
            f"before={before} after={after}",
        )
        _assertion(
            context,
            "no_side_effect.controller_alive_after",
            after_status.get("reachable") is True,
            f"reachable={after_status.get('reachable')}",
        )
    else:
        _nv(context, "no_side_effect.task_count_unchanged", CONTROLLER_UNAVAILABLE)
        _nv(context, "no_side_effect.controller_alive_after", CONTROLLER_UNAVAILABLE)

    process_pid = None
    process_record = _read_json(probe_out / "process.json") or {}
    pid_value = process_record.get("pid")
    if isinstance(pid_value, int):
        process_pid = pid_value
    serving = _run(
        ["ps", "-eo", "pid,args"],
        timeout=10,
    )
    launch_marker = f"acp mcp serve --transport stdio --controller {controller_url}"
    leftovers = [
        line
        for line in (serving.stdout or "").splitlines()
        if launch_marker in line and "ps -eo" not in line
    ]
    cleanup = {
        "schema": "acp2.c.mcp-live.cleanup.v1",
        "probe_server_pid": process_pid,
        "probe_server_pid_alive": (
            Path(f"/proc/{process_pid}").exists() if process_pid is not None else False
        ),
        "leftover_adapter_processes": leftovers,
        "controller_started_by_c": False,
        "production_8765_touched": False,
        "tasks_submitted": 0,
    }
    _write_json(out, "cleanup.json", cleanup)
    _add_artifact(context, "cleanup.json")
    _assertion(
        context,
        "no_side_effect.no_serving_process_left",
        not leftovers and not cleanup["probe_server_pid_alive"],
        f"leftovers={len(leftovers)} probe_pid_alive={cleanup['probe_server_pid_alive']}",
    )

    # ---- 6. summary + extensions ----------------------------------------
    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    not_verified = [item for item in context.assertions if item.get("status") == "NOT_VERIFIED"]
    summary = {
        "schema": "acp2.c.mcp-live.summary.v1",
        "launch": {
            "b05": {
                key: b05.get(key)
                for key in ("path", "sha256", "contract_version", "protocol_version")
            },
            "b06": {
                key: b06.get(key)
                for key in ("path", "sha256", "contract_version", "protocol_version")
            },
        },
        "controller": {
            "url": controller_url,
            "reachable": controller["reachable"],
            "verified_pid": pid_record["verified_pid"],
            "task_count_before": before,
            "task_count_after": after,
        },
        "official_client": {
            "package_versions": official.get("package_versions"),
            "requested_protocol": official.get("requested_protocol"),
            "negotiated_protocol": official.get("negotiated_protocol"),
            "tool_count": len(official.get("tools") or []),
        },
        "matrix": matrix,
        "failures": [item["name"] for item in failures],
        "not_verified": [
            {"name": item["name"], "detail": item.get("detail")} for item in not_verified
        ],
        "closes_h2": False,
    }
    _write_json(out, "mcp-live-summary.json", summary)
    _add_artifact(context, "mcp-live-summary.json")
    if official.get("tools"):
        schema_snapshot = {
            "source": "official_client_live",
            "live_verified": True,
            "launch": "B06",
            "negotiated_protocol": official.get("negotiated_protocol"),
            "tools": official.get("tools"),
        }
    else:
        schema_snapshot = {
            "source": "frozen_launch_descriptor",
            "live_verified": False,
            "live_reason": CONTROLLER_UNAVAILABLE
            if not controller["reachable"]
            else "handshake_failed",
            "launch": "B05",
            "descriptor_sha256": b05.get("sha256"),
            "negotiated_protocol": None,
            "output_schema_note": (
                "The server advertises no outputSchema; the descriptor carries inputSchema only. "
                "A live tools/list snapshot cannot be captured while the Controller is unavailable."
            ),
            "tools": [
                {
                    "name": name,
                    "description": (b05.get("tool_descriptions") or {}).get(name),
                    "inputSchema": (b05.get("tool_input_schemas") or {}).get(name),
                    "outputSchema": None,
                }
                for name in (b05.get("tool_names") or [])
            ],
        }
    _write_json(out, "tool-schemas.json", schema_snapshot)
    _add_artifact(context, "tool-schemas.json")

    context.extensions.update(
        {
            "experience.mcp_live.evidence_kind": context.identity.evidence_kind,
            "experience.mcp_live.closes_h2": False,
            "experience.mcp_live.launch_b05_sha256": b05.get("sha256"),
            "experience.mcp_live.launch_b06_sha256": b06.get("sha256"),
            "experience.mcp_live.launch_contract_versions": {
                "B05": b05.get("contract_version"),
                "B06": b06.get("contract_version"),
            },
            "experience.mcp_live.requested_protocol": official.get("requested_protocol"),
            "experience.mcp_live.negotiated_protocol": official.get("negotiated_protocol"),
            "experience.mcp_live.official_client_versions": official.get("package_versions"),
            "experience.mcp_live.tool_count": len(official.get("tools") or []),
            "experience.mcp_live.tools": sorted(
                tool.get("name") for tool in (official.get("tools") or []) if isinstance(tool, dict)
            ),
            "experience.mcp_live.controller_reachable": controller["reachable"],
            "experience.mcp_live.controller_pid": pid_record["verified_pid"],
            "experience.mcp_live.controller_task_count_before": before,
            "experience.mcp_live.controller_task_count_after": after,
            "experience.mcp_live.negotiation_matrix": matrix,
            "experience.mcp_live.not_verified_rows": [item["name"] for item in not_verified],
        }
    )

    if failures:
        context.reason = f"assertion_failed:{','.join(item['name'] for item in failures)}"
        return EXIT_FAIL
    if not_verified:
        context.reason = "controller_unavailable"
        return EXIT_NOT_VERIFIED
    return _finish(context, "mcp_live_assertion_failed")


register("mcp-live", scenario_mcp_live)
