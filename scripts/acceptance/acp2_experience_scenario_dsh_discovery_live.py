"""``dsh-discovery-live`` — wire the official DSH MCP plugin and prove discovery.

This body is T-12 / C08.  It boots the frozen DSH ``web`` and ``headless``
profiles with the C-owned Cordis overlays under
``rph/packages/dsh-bundle/profiles/`` and reads the LIVE tool registry through
``scripts/acceptance/acp2_dsh_tool_snapshot.mjs`` (``real_registration``, never
a source scan).

Required evidence:

* the overlay launch fields are byte-equal to B05's ``mcp-launch.json``;
* ``dsh_acceptance --profile <web|headless> --patch <overlay> --dump-config``
  exits 0 and its archived copy is redacted (no credential values);
* the web profile binds loopback only and never production ``:8765``;
* web and headless model-visible snapshots (name, source plugin, permission
  class, ``mcp__acp__*`` flag) contain no shell/exec/SSH/arbitrary-command
  tool and no second QC-capable MCP namespace;
* when the official plugin registers, the eight P1 ``mcp__acp__*`` tools are
  present and match the B05 allowlist;
* the negative fixtures fail explicitly: a bad ``command`` (ENOENT) and an MCP
  server that rejects the client's protocol version.

When B's isolated Controller is down the ACP MCP server exits before
``initialize``; the plugin then fails with ``failOnStartupError``.  That row is
``NOT_VERIFIED(controller_unavailable)`` with the exact stderr — never PASS and
never FAIL.  An executed negative that does not fail explicitly is FAIL.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml
from acp2_experience_scenarios import (
    EXIT_FAIL,
    EXIT_NOT_VERIFIED,
    EXIT_PASS,
    P1_TOOLS,
    _add_artifact,
    _assertion,
    _env,
    _env_path,
    _not_verified,
    _out_dir,
    _run,
    _sha256_file,
    _truncate,
    _write_json,
    _write_text,
    register,
)

SCENARIO_ID = "dsh-discovery-live"

B05_RELATIVE = "B/B05/mcp-launch.json"
OVERLAY_WEB_RELATIVE = "rph/packages/dsh-bundle/profiles/web.cordis.yml"
OVERLAY_HEADLESS_RELATIVE = "rph/packages/dsh-bundle/profiles/headless.cordis.yml"
SNAPSHOT_SCRIPT_RELATIVE = "scripts/acceptance/acp2_dsh_tool_snapshot.mjs"
ENTRY_ID = "acp-mcp-acceptance"
MCP_PLUGIN = "@deepseek-ai/dsh-mcp-client"
MODEL_PREFIX = "mcp__acp__"
CONTROLLER_UNAVAILABLE = "controller_unavailable"
# Names that would be a generic execution surface. `job_*` are background
# command controls and are treated the same as a shell.
EXEC_NAME_RE = re.compile(
    r"(?i)^(?:bash|pwsh|powershell|shell|sh|zsh|terminal|exec|spawn|ssh|scp|sftp|"
    r"run_command|run_shell|job_(?:list|output|kill)|jobs?)$"
)
SECRET_KEY_RE = re.compile(r"(?i)(secret|token|password|api[_-]?key|authorization|credential)s?$")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
TOKEN_QUERY_RE = re.compile(r"(?i)([?&]token=)[^&\s]+")
MASK = "REDACTED"


def _nv(context: Any, name: str, reason: str) -> None:
    context.assertions.append({"name": name, "status": "NOT_VERIFIED", "detail": reason[:2000]})


# ---------------------------------------------------------------------------
# YAML / snapshot helpers
# ---------------------------------------------------------------------------


class _DshLoader(yaml.SafeLoader):
    """SafeLoader that keeps the Loader's ``!!js`` expressions as text."""


_DshLoader.add_constructor(
    "tag:yaml.org,2002:js",
    lambda loader, node: loader.construct_scalar(node),
)


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=_DshLoader)


def _composed_entry(document: Any, entry_id: str) -> dict[str, Any] | None:
    if not isinstance(document, list):
        return None
    for row in document:
        if isinstance(row, dict) and row.get("id") == entry_id:
            return row
    return None


def _inserted_entry(document: Any, entry_id: str) -> dict[str, Any] | None:
    """Find an entry declared inside an overlay's ``insert`` list."""

    if not isinstance(document, list):
        return None
    for patch in document:
        if not isinstance(patch, dict):
            continue
        for row in patch.get("insert") or []:
            if isinstance(row, dict) and row.get("id") == entry_id:
                return row
    return None


def _disabled_ids(document: Any) -> set[str]:
    found: set[str] = set()
    if not isinstance(document, list):
        return found
    for patch in document:
        if (
            isinstance(patch, dict)
            and patch.get("disabled") is True
            and isinstance(patch.get("id"), str)
        ):
            found.add(patch["id"])
    return found


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _local_non_loopback_ipv4() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            value = sock.getsockname()[0]
    except OSError:
        return None
    return value if value and not value.startswith("127.") else None


def _controller_status(url: str, timeout: float = 3.0) -> dict[str, Any]:
    status: dict[str, Any] = {"url": url, "reachable": False}
    try:
        request = urllib.request.Request(
            url.rstrip("/") + "/api/health", headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        status["reason"] = f"{type(exc).__name__}: {exc}"
        return status
    status["reachable"] = isinstance(payload, dict) and payload.get("status") == "ok"
    status["health"] = payload if isinstance(payload, dict) else None
    return status


def _snapshot_env() -> dict[str, str] | None:
    dsh_source = _env("ACP2_DSH_SOURCE")
    dsh_home = _env("ACP2_DSH_HOME")
    loader = _env("ACP2_DSH_LOADER")
    node = _env("ACP2_NODE_BIN")
    node_home = _env("ACP2_NODE_HOME")
    workspace = _env("ACP2_DSH_WORKSPACE")
    if not all((dsh_source, dsh_home, loader, node, node_home, workspace)):
        return None
    environment = dict(os.environ)
    environment.update(
        {
            "ACP2_DSH_SOURCE": dsh_source,
            "ACP2_DSH_HOME": dsh_home,
            "ACP2_DSH_LOADER": loader,
            "ACP2_DSH_WORKSPACE": workspace,
            "DSH_HOME": dsh_home,
            "TSX_TSCONFIG_PATH": str(Path(dsh_source) / "tsconfig.json"),
        }
    )
    environment["PATH"] = os.pathsep.join(
        [str(Path(node).parent), str(Path(node_home) / "bin"), environment.get("PATH", "")]
    )
    return environment


def _run_snapshot(
    *,
    profile: str,
    patches: list[Path],
    out: Path,
    launch: Path,
    workspace: Path,
    environment: dict[str, str],
    timeout: float,
    script: Path,
    web_port: int | None = None,
    probe_external: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    node = Path(environment["ACP2_NODE_BIN"])
    loader = environment["ACP2_DSH_LOADER"]
    argv = [
        str(node),
        "--import",
        loader,
        str(script),
        "--profile",
        profile,
        "--out",
        str(out),
        "--launch",
        str(launch),
    ]
    for patch in patches:
        argv.extend(["--patch", str(patch)])
    if web_port is not None:
        argv.extend(["--web-port", str(web_port)])
    if probe_external is not None:
        argv.extend(["--probe-external", probe_external])
    invocation = {
        "argv": argv,
        "cwd": str(workspace),
        "env_keys": sorted(key for key in environment if key.startswith(("ACP2_", "DSH_", "TSX_"))),
    }
    result = _run(argv, cwd=workspace, timeout=timeout, env=environment)
    document = None
    try:
        document = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        document = None
    invocation.update(
        {
            "exit_code": result.returncode,
            "stderr": _truncate(result.stderr, 6000),
            "stdout": _truncate(result.stdout, 2000),
        }
    )
    return document, invocation


def _redact(text: str, secret_values: list[str]) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(\s*)([A-Za-z0-9_.-]+)(\s*:\s*)(.*)$", line)
        if match is not None and SECRET_KEY_RE.search(match.group(2)):
            line = f'{match.group(1)}{match.group(2)}{match.group(3)}"{MASK}"'
        line = BEARER_RE.sub(f"Bearer {MASK}", line)
        line = TOKEN_QUERY_RE.sub(rf"\1{MASK}", line)
        for value in secret_values:
            if value and len(value) >= 4:
                line = line.replace(value, MASK)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _secret_values() -> list[str]:
    values: list[str] = []
    for key, value in os.environ.items():
        if SECRET_KEY_RE.search(key) and value and len(value) >= 4:
            values.append(value)
    return values


def _tool_names(document: dict[str, Any] | None) -> set[str]:
    if not isinstance(document, dict):
        return set()
    return {
        row.get("name")
        for row in document.get("model_visible_tools") or []
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def _tool_rows(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        return []
    return [row for row in document.get("model_visible_tools") or [] if isinstance(row, dict)]


def _mcp_tools(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [row for row in _tool_rows(document) if str(row.get("name", "")).startswith("mcp__")]


def _banned_tools(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    banned: list[dict[str, Any]] = []
    for row in _tool_rows(document):
        name = str(row.get("name", ""))
        source = str(row.get("source_plugin", ""))
        if EXEC_NAME_RE.match(name) or EXEC_NAME_RE.match(source):
            banned.append(row)
    return banned


def _stderr_text(document: dict[str, Any] | None) -> str:
    if not isinstance(document, dict):
        return ""
    return "\n".join(str(line) for line in document.get("stderr_lines") or [])


def _entry_failed(document: dict[str, Any] | None) -> bool:
    entry = (document or {}).get("acp_entry")
    return isinstance(entry, dict) and entry.get("failed") is True


def _entry_error_text(document: dict[str, Any] | None) -> str:
    error = (document or {}).get("acp_entry_error") or {}
    if not isinstance(error, dict):
        return ""
    return "\n".join(
        [str(error.get("name") or ""), str(error.get("message") or "")]
        + [str(line) for line in error.get("stack") or []]
    )


# ---------------------------------------------------------------------------
# Overlay fixtures
# ---------------------------------------------------------------------------


def _overlay_document(
    *,
    command: str,
    args: list[str],
    cwd: str,
    env: dict[str, str],
    disabled: list[str] | None = None,
) -> list[dict[str, Any]]:
    document: list[dict[str, Any]] = [
        {
            "insert": [
                {
                    "id": ENTRY_ID,
                    "name": MCP_PLUGIN,
                    "config": {
                        "serverName": "acp",
                        "transport": "stdio",
                        "command": command,
                        "args": args,
                        "cwd": cwd,
                        "env": env,
                        "toolCallTimeoutMs": 120000,
                        "failOnStartupError": True,
                        "reconnect": {
                            "enabled": True,
                            "initialDelayMs": 500,
                            "maxDelayMs": 30000,
                            "maxAttempts": 10,
                        },
                    },
                }
            ]
        }
    ]
    for entry_id in disabled or []:
        document.append({"id": entry_id, "disabled": True})
    return document


INCOMPATIBLE_STUB = '''#!/usr/bin/env python3
"""MCP stdio fixture: reject every protocol version this client asks for."""
import json
import os
import sys

wire = os.environ.get("STUB_WIRE")
log = open(wire, "a", encoding="utf-8") if wire else None


def record(kind, payload):
    if log:
        log.write(json.dumps({"kind": kind, "payload": payload}) + "\\n")
        log.flush()


def emit(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    record("recv", message)
    request_id = message.get("id")
    if message.get("method") == "initialize":
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32602,
                "message": "unsupported protocol version",
                "data": {
                    "code": "unsupported_protocol_version",
                    "supported": ["2025-06-18"],
                },
            },
        }
        emit(response)
        record("send", response)
    elif request_id is not None:
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
        emit(response)
        record("send", response)
if log:
    log.close()
'''


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def scenario_dsh_discovery_live(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    evidence = _env_path("ACP2_EVIDENCE")
    environment = _snapshot_env()
    workspace = _env_path("ACP2_DSH_WORKSPACE")
    out = _out_dir(context)
    timeout = max(30.0, float(context.timeout))

    if source is None or not source.is_dir():
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    if evidence is None or not evidence.is_dir():
        return _not_verified(context, "missing_prerequisite:ACP2_EVIDENCE")
    if environment is None or workspace is None:
        return _not_verified(context, "missing_prerequisite:dsh_env")

    launch_path = evidence / B05_RELATIVE
    if not launch_path.is_file():
        return _not_verified(context, f"missing_prerequisite:{B05_RELATIVE}")
    try:
        launch = json.loads(launch_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _not_verified(context, f"missing_prerequisite:launch_unreadable:{exc}")

    controller_url = str((launch.get("env") or {}).get("ACP_CONTROLLER_URL") or "")
    for index, value in enumerate(launch.get("args") or []):
        if value == "--controller" and index + 1 < len(launch.get("args") or []):
            controller_url = str(launch["args"][index + 1])
            break
    controller_before = (
        _controller_status(controller_url) if controller_url else {"reachable": False}
    )
    _write_json(out, "controller.json", controller_before)
    _add_artifact(context, "controller.json")

    overlay_web = source / OVERLAY_WEB_RELATIVE
    overlay_headless = source / OVERLAY_HEADLESS_RELATIVE
    for path in (overlay_web, overlay_headless):
        if not path.is_file():
            return _not_verified(context, f"missing_prerequisite:{path.relative_to(source)}")

    # ---- 1. overlay identity: command/args/cwd/env == B05 -----------------
    expected = {
        "command": launch.get("command"),
        "args": list(launch.get("args") or []),
        "cwd": launch.get("cwd"),
        "env": dict(launch.get("env") or {}),
    }
    overlay_identity: dict[str, Any] = {
        "schema": "acp2.c.dsh-discovery.overlay-identity.v1",
        "b05_path": str(launch_path),
        "b05_sha256": _sha256_file(launch_path),
        "profiles": {},
    }
    for label, path in (("web", overlay_web), ("headless", overlay_headless)):
        try:
            document = _load_yaml(path)
        except (OSError, yaml.YAMLError) as exc:
            _assertion(context, f"overlay.{label}.parses", False, _truncate(str(exc)))
            continue
        entry = _inserted_entry(document, ENTRY_ID)
        actual = {
            "command": (entry or {}).get("config", {}).get("command"),
            "args": list((entry or {}).get("config", {}).get("args") or []),
            "cwd": (entry or {}).get("config", {}).get("cwd"),
            "env": dict((entry or {}).get("config", {}).get("env") or {}),
        }
        matches = actual == expected
        overlay_identity["profiles"][label] = {
            "path": str(path),
            "sha256": _sha256_file(path),
            "insert_id": (entry or {}).get("id"),
            "plugin": (entry or {}).get("name"),
            "launch_matches_b05": matches,
            "actual": actual,
        }
        _assertion(
            context,
            f"overlay.{label}.insert_semantics",
            isinstance(document, list)
            and any(isinstance(row, dict) and row.get("insert") for row in document),
            "top-level array with an insert list",
        )
        _assertion(
            context,
            f"overlay.{label}.plugin_name",
            (entry or {}).get("name") == MCP_PLUGIN,
            str((entry or {}).get("name")),
        )
        _assertion(
            context,
            f"overlay.{label}.launch_identical_to_b05",
            matches,
            "command/args/cwd/env equal"
            if matches
            else _truncate(json.dumps({"expected": expected, "actual": actual}, sort_keys=True)),
        )
        if label == "web":
            disabled = _disabled_ids(document)
            _assertion(
                context,
                "overlay.web.exec_presets_disabled",
                {"preset-standard", "preset-ptc", "preset-minimal", "preset-cordis"} <= disabled,
                f"disabled={sorted(disabled)}",
            )
        else:
            disabled = _disabled_ids(document)
            _assertion(
                context,
                "overlay.headless.exec_rows_disabled",
                {"tool-bash", "tool-pwsh", "tool-jobs", "bash-sandbox", "pwsh-sandbox"} <= disabled,
                f"disabled={sorted(disabled)}",
            )
    _write_json(out, "overlay-identity.json", overlay_identity)
    _add_artifact(context, "overlay-identity.json")

    # ---- 2. dump-config: apply the patch, archive a redacted copy ---------
    secret_values = _secret_values()
    dump_documents: dict[str, Any] = {}
    for label, path in (("web", overlay_web), ("headless", overlay_headless)):
        command = [
            "bash",
            "-lc",
            (
                'source "$ACP2_DATA_ROOT/acceptance-env-c.sh"; '
                f'cd "$ACP2_DSH_WORKSPACE"; dsh_acceptance {label} '
                f"--patch {json.dumps(str(path))} --dump-config"
            ),
        ]
        result = _run(command, timeout=timeout, env=environment)
        dump_path = out / f"dsh-effective-config.{label}.redacted.yml"
        redacted = _redact(result.stdout, secret_values)
        _write_text(out, dump_path.name, redacted)
        _add_artifact(context, dump_path.name)
        dump = {
            "exit_code": result.returncode,
            "stderr": _truncate(result.stderr, 4000),
            "redacted_path": dump_path.name,
            "redacted_sha256": _sha256_file(dump_path),
            "redacted_bytes": dump_path.stat().st_size,
            "contains_secret_value": any(value in redacted for value in secret_values),
            "contains_token_query": bool(re.search(r"(?i)[?&]token=(?!\*\*\*)", redacted)),
        }
        _assertion(
            context,
            f"dump.{label}.exit_zero",
            result.returncode == 0,
            f"exit_code={result.returncode} stderr={_truncate(result.stderr, 500)}",
        )
        _assertion(
            context,
            f"dump.{label}.redacted",
            not dump["contains_secret_value"] and not dump["contains_token_query"],
            (
                f"secret_value={dump['contains_secret_value']} "
                f"token_query={dump['contains_token_query']}"
            ),
        )
        try:
            document = _load_yaml(dump_path)
        except (OSError, yaml.YAMLError):
            document = None
        entry = _composed_entry(document, ENTRY_ID)
        if entry is not None:
            dump_documents[label] = entry
        _assertion(
            context,
            f"dump.{label}.inserted_entry_applied",
            entry is not None
            and entry.get("name") == MCP_PLUGIN
            and isinstance(entry.get("config"), dict)
            and (entry.get("config") or {}).get("failOnStartupError") is True,
            "composed entry present with failOnStartupError"
            if entry is not None
            else "entry absent from composed tree",
        )
        if entry is not None:
            composed = entry.get("config") or {}
            composed_identity = {
                "command": composed.get("command"),
                "args": list(composed.get("args") or []),
                "cwd": composed.get("cwd"),
                "env": dict(composed.get("env") or {}),
            }
            _assertion(
                context,
                f"dump.{label}.entry_launch_identical_to_b05",
                composed_identity == expected,
                _truncate(json.dumps(composed_identity, sort_keys=True)),
            )
        _write_json(out, f"dump.{label}.json", dump)
        _add_artifact(context, f"dump.{label}.json")

    # ---- 3. live registration snapshots ---------------------------------
    no_runner = out / "headless-runner-disabled.cordis.yml"
    _write_text(
        out,
        no_runner.name,
        "# Snapshot-only overlay: the headless one-shot runner would create an\n"
        "# agent (and contact a model). T-12 reads registration, not a model run.\n"
        "- id: headless-runner\n  disabled: true\n",
    )
    _add_artifact(context, no_runner.name)

    external_ip = _local_non_loopback_ipv4()
    web_port = _free_port()
    snapshots: dict[str, Any] = {}
    invocations: dict[str, Any] = {}
    for label, overlay, extra, port, external in (
        ("web", overlay_web, [], web_port, external_ip),
        ("headless", overlay_headless, [no_runner], None, None),
    ):
        snapshot_path = out / f"tool-snapshot.{label}.json"
        document, invocation = _run_snapshot(
            profile=label,
            patches=[overlay, *extra],
            out=snapshot_path,
            launch=launch_path,
            workspace=workspace,
            environment=environment,
            timeout=timeout,
            script=source / SNAPSHOT_SCRIPT_RELATIVE,
            web_port=port,
            probe_external=external,
        )
        snapshots[label] = document
        invocations[label] = invocation
        _write_json(out, f"tool-snapshot.{label}.invocation.json", invocation)
        _add_artifact(context, f"tool-snapshot.{label}.invocation.json")
        if document is not None:
            _add_artifact(context, f"tool-snapshot.{label}.json")
        _assertion(
            context,
            f"snapshot.{label}.executed",
            document is not None and document.get("status") == "ok",
            _truncate(
                json.dumps(
                    {
                        "exit_code": invocation.get("exit_code"),
                        "error": (document or {}).get("error"),
                        "stderr": invocation.get("stderr"),
                    },
                    sort_keys=True,
                )
            ),
        )
        if document is None:
            continue
        _assertion(
            context,
            f"snapshot.{label}.real_registration",
            document.get("source") == "real_registration",
            f"source={document.get('source')} booted_via={document.get('booted_via')}",
        )
        banned = _banned_tools(document)
        _assertion(
            context,
            f"snapshot.{label}.no_exec_tool",
            not banned,
            f"banned={[row.get('name') for row in banned]}",
        )
        rows = _mcp_tools(document)
        namespaces = {
            str(row.get("name", "")).split("__")[1]
            for row in rows
            if str(row.get("name", "")).count("__") >= 2
        }
        _assertion(
            context,
            f"snapshot.{label}.single_mcp_namespace",
            namespaces <= {"acp"},
            f"namespaces={sorted(namespaces)}",
        )
        _assertion(
            context,
            f"snapshot.{label}.tool_rows_have_source",
            all(
                isinstance(row.get("source_plugin"), str) and row.get("source_plugin") != "unknown"
                for row in _tool_rows(document)
            ),
            f"tools={[row.get('name') for row in _tool_rows(document)]}",
        )
        if label == "web":
            bound = document.get("bound_preset") or {}
            presets = [
                preset.get("id")
                for preset in document.get("presets") or []
                if isinstance(preset, dict)
            ]
            _assertion(
                context,
                "snapshot.web.research_preset",
                bound.get("id") == "rph-research" and presets == ["rph-research"],
                f"bound={bound.get('id')} presets={presets}",
            )
            web_url = document.get("web_url") or {}
            loopback = web_url.get("loopback") or {}
            external_probe = web_url.get("external") or {}
            _assertion(
                context,
                "snapshot.web.loopback_only",
                web_url.get("loopback_only_requested") is True
                and web_url.get("production_8765") is False
                and loopback.get("reachable") is True
                and (
                    external_probe.get("probed") is not True
                    or external_probe.get("reachable") is False
                ),
                _truncate(
                    json.dumps(
                        {
                            "port": web_url.get("port"),
                            "loopback": loopback,
                            "external": external_probe,
                        },
                        sort_keys=True,
                    )
                ),
            )
        else:
            _assertion(
                context,
                "snapshot.headless.global_is_model_view",
                document.get("model_visible_scope") == "global"
                and _tool_names(document)
                == {row.get("name") for row in document.get("global_tools") or []},
                f"scope={document.get('model_visible_scope')}",
            )

    # ---- 4. MCP registration / allowlist ---------------------------------
    registration: dict[str, Any] = {
        "schema": "acp2.c.dsh-discovery.registration.v1",
        "entry_id": ENTRY_ID,
        "plugin": MCP_PLUGIN,
        "controller_reachable": controller_before.get("reachable"),
        "entries": {},
        "allowlist": sorted(P1_TOOLS),
        "model_prefix": MODEL_PREFIX,
        "p2_extensions": [],
        "p2_status": "NOT_VERIFIED(b03_handoff)",
    }
    accepted_any = False
    controller_related = False
    exact_stderr: dict[str, list[str]] = {}
    for label in ("web", "headless"):
        document = snapshots.get(label)
        entry = (document or {}).get("acp_entry")
        registration["entries"][label] = entry
        rows = _mcp_tools(document)
        registration[f"{label}_mcp_tools"] = sorted(row.get("name") for row in rows)
        invocation_stderr = str((invocations.get(label) or {}).get("stderr") or "")
        exact_stderr[label] = [
            line
            for line in invocation_stderr.splitlines()
            if "Controller unavailable" in line
            or "Connection refused" in line
            or "SdkError: Connection closed" in line
        ]
        if not _entry_failed(document):
            # A failed entry is the only expected state while the Controller is
            # down; a non-failed entry must actually expose the eight tools.
            accepted = {str(row.get("name")) for row in rows if row.get("mcp_namespaced") is True}
            expected_names = {MODEL_PREFIX + name for name in P1_TOOLS}
            _assertion(
                context,
                f"registration.{label}.eight_p1_tools",
                accepted == expected_names,
                f"present={sorted(accepted)} expected={sorted(expected_names)}",
            )
            _assertion(
                context,
                f"registration.{label}.source_plugin",
                all(row.get("source_plugin") == MCP_PLUGIN for row in rows),
                f"sources={[row.get('source_plugin') for row in rows]}",
            )
            accepted_any = True
        else:
            stderr = _stderr_text(document) + "\n" + _entry_error_text(document)
            controller_related = controller_related or bool(
                re.search(r"Controller unavailable|Connection refused|Connection closed", stderr)
            )
    registration["exact_stderr"] = exact_stderr
    _write_json(out, "registration.json", registration)
    _add_artifact(context, "registration.json")

    if accepted_any:
        _assertion(context, "registration.at_least_one_profile", True, "official plugin registered")
    elif _entry_failed(snapshots.get("web")) or _entry_failed(snapshots.get("headless")):
        if not controller_before.get("reachable") and controller_related:
            detail = CONTROLLER_UNAVAILABLE
            first = next(
                (
                    line
                    for lines in exact_stderr.values()
                    for line in lines
                    if "Controller unavailable" in line
                ),
                None,
            )
            if first is not None:
                detail = f"{CONTROLLER_UNAVAILABLE}: {first}"
            _nv(context, "registration.official_plugin", detail)
            _nv(context, "registration.eight_p1_tools", detail)
            _nv(context, "registration.source_plugin", detail)
        else:
            _assertion(
                context,
                "registration.official_plugin",
                False,
                "plugin failed while the Controller was reachable or for a non-Controller reason",
            )
    else:
        _assertion(context, "registration.official_plugin", False, "plugin entry missing")

    # ---- 5. negative fixtures -------------------------------------------
    negative_root = out / "negative"
    negative_root.mkdir(parents=True, exist_ok=True)

    bad_command_path = negative_root / "bad-command.cordis.yml"
    bad_entry = _load_yaml(overlay_headless)[0]["insert"][0]
    bad_entry["config"] = dict(bad_entry["config"])
    bad_entry["config"]["command"] = "/nonexistent/acp2-missing-python"
    _write_text(
        out,
        str(bad_command_path.relative_to(out)),
        yaml.safe_dump(
            [
                {"insert": [bad_entry]},
                *[
                    {"id": entry_id, "disabled": True}
                    for entry_id in (
                        "tool-bash",
                        "tool-pwsh",
                        "tool-jobs",
                        "bash-sandbox",
                        "pwsh-sandbox",
                    )
                ],
            ],
            sort_keys=False,
        ),
    )
    _add_artifact(context, str(bad_command_path.relative_to(out)))
    bad_document, bad_invocation = _run_snapshot(
        profile="headless",
        patches=[bad_command_path, no_runner],
        out=negative_root / "tool-snapshot.bad-command.json",
        launch=launch_path,
        workspace=workspace,
        environment=environment,
        timeout=timeout,
        script=source / SNAPSHOT_SCRIPT_RELATIVE,
    )
    _write_json(out, "negative/bad-command.invocation.json", bad_invocation)
    _add_artifact(context, "negative/bad-command.invocation.json")
    if bad_document is None:
        _assertion(
            context,
            "negative.bad_command.explicit_failure",
            False,
            _truncate(str(bad_invocation.get("stderr"))),
        )
    else:
        _add_artifact(context, "negative/tool-snapshot.bad-command.json")
        bad_stderr = _stderr_text(bad_document) + "\n" + _entry_error_text(bad_document)
        _assertion(
            context,
            "negative.bad_command.entry_failed",
            _entry_failed(bad_document),
            json.dumps(bad_document.get("acp_entry"), sort_keys=True),
        )
        _assertion(
            context,
            "negative.bad_command.explicit_failure",
            bool(re.search(r"ENOENT|spawn .*acp2-missing-python", bad_stderr)),
            _truncate(bad_stderr),
        )
        _assertion(
            context,
            "negative.bad_command.no_mcp_tools",
            not _mcp_tools(bad_document),
            f"mcp={[row.get('name') for row in _mcp_tools(bad_document)]}",
        )

    stub_path = negative_root / "incompatible_protocol_stub.py"
    _write_text(out, str(stub_path.relative_to(out)), INCOMPATIBLE_STUB)
    os.chmod(stub_path, 0o755)
    _add_artifact(context, str(stub_path.relative_to(out)))
    wire_path = negative_root / "incompatible-protocol.wire.jsonl"
    incompatible_path = negative_root / "incompatible-protocol.cordis.yml"
    incompatible = _overlay_document(
        command=_env("ACP2_C_PYTHON") or sys.executable,
        args=[str(stub_path)],
        cwd=str(negative_root),
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "STUB_WIRE": str(wire_path)},
        disabled=["tool-bash", "tool-pwsh", "tool-jobs", "bash-sandbox", "pwsh-sandbox"],
    )
    _write_text(
        out,
        str(incompatible_path.relative_to(out)),
        yaml.safe_dump(incompatible, sort_keys=False),
    )
    _add_artifact(context, str(incompatible_path.relative_to(out)))
    if wire_path.exists():
        wire_path.unlink()
    incompatible_document, incompatible_invocation = _run_snapshot(
        profile="headless",
        patches=[incompatible_path, no_runner],
        out=negative_root / "tool-snapshot.incompatible-protocol.json",
        launch=launch_path,
        workspace=workspace,
        environment=environment,
        timeout=timeout,
        script=source / SNAPSHOT_SCRIPT_RELATIVE,
    )
    _write_json(out, "negative/incompatible-protocol.invocation.json", incompatible_invocation)
    _add_artifact(context, "negative/incompatible-protocol.invocation.json")
    wire_messages: list[dict[str, Any]] = []
    if wire_path.is_file():
        for line in wire_path.read_text(encoding="utf-8").splitlines():
            try:
                wire_messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        _add_artifact(context, "negative/incompatible-protocol.wire.jsonl")
    if incompatible_document is None:
        _assertion(
            context,
            "negative.incompatible_protocol.explicit_failure",
            False,
            _truncate(str(incompatible_invocation.get("stderr"))),
        )
    else:
        _add_artifact(context, "negative/tool-snapshot.incompatible-protocol.json")
        incompatible_stderr = (
            _stderr_text(incompatible_document) + "\n" + _entry_error_text(incompatible_document)
        )
        initialize_versions = [
            (message.get("payload") or {}).get("params", {}).get("protocolVersion")
            for message in wire_messages
            if isinstance(message.get("payload"), dict)
            and message["payload"].get("method") == "initialize"
        ]
        protocol_rejections = [
            message
            for message in wire_messages
            if (message.get("payload") or {}).get("error", {}).get("data", {}).get("code")
            == "unsupported_protocol_version"
        ]
        _assertion(
            context,
            "negative.incompatible_protocol.entry_failed",
            _entry_failed(incompatible_document),
            json.dumps(incompatible_document.get("acp_entry"), sort_keys=True),
        )
        _assertion(
            context,
            "negative.incompatible_protocol.explicit_failure",
            bool(re.search(r"unsupported protocol version|ProtocolError", incompatible_stderr))
            or bool(protocol_rejections),
            _truncate(incompatible_stderr),
        )
        _assertion(
            context,
            "negative.incompatible_protocol.wire",
            bool(initialize_versions) and bool(protocol_rejections),
            f"versions={initialize_versions} rejections={len(protocol_rejections)}",
        )
        _assertion(
            context,
            "negative.incompatible_protocol.no_mcp_tools",
            not _mcp_tools(incompatible_document),
            f"mcp={[row.get('name') for row in _mcp_tools(incompatible_document)]}",
        )

    # ---- 6. F1 (descriptor vs live schema) ------------------------------
    f1: dict[str, Any] = {
        "schema": "acp2.c.dsh-discovery.f1.v1",
        "finding": "F1 descriptor tools[].inputSchema differs from a live tools/list shape",
        "owner": "B",
        "must_reconcile_before": "H2",
        "c_live_confirmation": "NOT_VERIFIED(controller_unavailable)"
        if not controller_before.get("reachable")
        else "observed_this_run",
    }
    drift_paths = sorted(
        (evidence / "C/mcp-live").glob("*/main/schema-drift.json"),
        key=lambda path: path.stat().st_mtime,
    )
    if drift_paths:
        drift_path = drift_paths[-1]
        try:
            drift = json.loads(drift_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            drift = {}
        f1.update(
            {
                "t11_source": str(drift_path),
                "t11_source_sha256": _sha256_file(drift_path),
                "descriptor_sha256": drift.get("descriptor_sha256"),
                "live_snapshot_kind": drift.get("live_snapshot_kind"),
                "t11_matches": drift.get("matches"),
                "mismatched_tools": drift.get("mismatched_tools") or [],
                "status": "OPEN" if drift.get("matches") is False else "CLOSED",
            }
        )
        _assertion(
            context,
            "f1.recorded",
            drift.get("matches") is False and len(drift.get("mismatched_tools") or []) == 8,
            f"source={drift_path} matches={drift.get('matches')}",
        )
    else:
        _nv(context, "f1.recorded", "missing_prerequisite:T11_schema_drift_evidence")
    _write_json(out, "f1-schema-mismatch.json", f1)
    _add_artifact(context, "f1-schema-mismatch.json")

    # ---- 7. no side effects / cleanup -----------------------------------
    controller_after = (
        _controller_status(controller_url) if controller_url else {"reachable": False}
    )
    ps = _run(["ps", "-eo", "pid,args"], timeout=min(10.0, timeout))
    process_lines = (ps.stdout or "").splitlines()
    leftovers = [
        line
        for line in process_lines
        if ("acp mcp serve --transport stdio --controller" in line and "ps -eo" not in line)
        or str(stub_path) in line
    ]
    cleanup = {
        "schema": "acp2.c.dsh-discovery.cleanup.v1",
        "controller_url": controller_url,
        "controller_reachable_before": controller_before.get("reachable"),
        "controller_reachable_after": controller_after.get("reachable"),
        "controller_started_by_c": False,
        "production_8765_touched": False,
        "tasks_submitted": 0,
        "leftover_processes": leftovers,
        "web_port_used": web_port,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write_json(out, "cleanup.json", cleanup)
    _add_artifact(context, "cleanup.json")
    _assertion(
        context,
        "no_side_effect.no_leftover_process",
        not leftovers,
        _truncate("\n".join(leftovers)),
    )
    _assertion(
        context,
        "no_side_effect.production_8765_untouched",
        web_port != 8765,
        f"port={web_port}",
    )
    if controller_before.get("reachable"):
        _assertion(
            context,
            "no_side_effect.controller_alive_after",
            controller_after.get("reachable") is True,
            f"after={controller_after.get('reachable')}",
        )

    # ---- 8. summary ------------------------------------------------------
    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    not_verified = [item for item in context.assertions if item.get("status") == "NOT_VERIFIED"]
    summary = {
        "schema": "acp2.c.dsh-discovery.summary.v1",
        "profiles": ["web", "headless"],
        "overlay_identity": overlay_identity,
        "controller": {
            "url": controller_url,
            "reachable": controller_before.get("reachable"),
        },
        "registration": registration,
        "snapshots": {
            label: {
                "model_visible_scope": (document or {}).get("model_visible_scope"),
                "model_visible_tools": sorted(_tool_names(document)),
                "mcp_tools": sorted(row.get("name") for row in _mcp_tools(document)),
                "acp_entry": (document or {}).get("acp_entry"),
                "tool_count": len(_tool_rows(document)),
                "real_registration": (document or {}).get("source") == "real_registration",
            }
            for label, document in snapshots.items()
        },
        "negatives": {
            "bad_command": {
                "entry_failed": _entry_failed(bad_document),
                "mcp_tools": [row.get("name") for row in _mcp_tools(bad_document)],
            },
            "incompatible_protocol": {
                "entry_failed": _entry_failed(incompatible_document),
                "mcp_tools": [row.get("name") for row in _mcp_tools(incompatible_document)],
                "initialize_versions": [
                    (message.get("payload") or {}).get("params", {}).get("protocolVersion")
                    for message in wire_messages
                    if isinstance(message.get("payload"), dict)
                    and message["payload"].get("method") == "initialize"
                ],
            },
        },
        "f1": f1,
        "failures": [item["name"] for item in failures],
        "not_verified": [
            {"name": item["name"], "detail": item.get("detail")} for item in not_verified
        ],
        "closes_h2": False,
    }
    _write_json(out, "dsh-discovery-summary.json", summary)
    _add_artifact(context, "dsh-discovery-summary.json")

    context.extensions.update(
        {
            "experience.dsh_discovery.evidence_kind": context.identity.evidence_kind,
            "experience.dsh_discovery.closes_h2": False,
            "experience.dsh_discovery.overlay_web_sha256": overlay_identity["profiles"]
            .get("web", {})
            .get("sha256"),
            "experience.dsh_discovery.overlay_headless_sha256": overlay_identity["profiles"]
            .get("headless", {})
            .get("sha256"),
            "experience.dsh_discovery.b05_sha256": overlay_identity.get("b05_sha256"),
            "experience.dsh_discovery.controller_reachable": controller_before.get("reachable"),
            "experience.dsh_discovery.registration_ok": accepted_any,
            "experience.dsh_discovery.web_model_visible_tools": sorted(
                _tool_names(snapshots.get("web"))
            ),
            "experience.dsh_discovery.headless_model_visible_tools": sorted(
                _tool_names(snapshots.get("headless"))
            ),
            "experience.dsh_discovery.negatives_ok": all(
                item.get("status") == "PASS"
                for item in context.assertions
                if item.get("name", "").startswith("negative.")
            ),
            "experience.dsh_discovery.f1_status": f1.get("status"),
            "experience.dsh_discovery.not_verified_rows": [item["name"] for item in not_verified],
        }
    )

    if failures:
        context.reason = f"assertion_failed:{','.join(item['name'] for item in failures)}"
        return EXIT_FAIL
    if not_verified:
        context.reason = CONTROLLER_UNAVAILABLE
        return EXIT_NOT_VERIFIED
    return EXIT_PASS


register("dsh-discovery-live", scenario_dsh_discovery_live)
