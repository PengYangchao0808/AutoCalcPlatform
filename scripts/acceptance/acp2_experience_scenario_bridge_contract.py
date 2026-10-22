"""``bridge-contract`` — research semantics mapped onto the official MCP tools.

This body is C06a's offline contract proof:

* the bridge compiles, tests and passes its boundary gate;
* every P2 operation in ``experience/p2-operation-map.md`` names a tool that
  exists in B03's committed ``p2-research-operations.v1.json`` (the eight P1
  tools plus ten P2 tools = 18 names);
* the doc table and the compiled bridge expose exactly the same operations
  (no missing row, no invented row);
* the extension negotiation rule and the six boundary cases are covered by the
  executed test names.

It is ``evidence_kind=fixture``: it never starts a Controller, a DSH session, a
model or a QC process, and it does not close H2.  Missing prerequisites are
``NOT_VERIFIED`` (exit 2); a command that ran with a wrong result is ``FAIL``
(exit 1).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    EXIT_FAIL,
    P1_TOOLS,
    _add_artifact,
    _assertion,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _pnpm,
    _run,
    _sha256_file,
    _tool_env,
    _truncate,
    _write_json,
    _write_text,
    register,
)

SCENARIO_ID = "bridge-contract"

B03_RELATIVE = "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json"
MAP_RELATIVE = "docs/reference/next-change/acp2-contracts/experience/p2-operation-map.md"

EXTENSION_ID = "acp.research.v1"
EXTENSION_VERSION = "1.0.0"

DOC_SHA_RE = re.compile(r"sha256:\s*([0-9a-f]{64})")
TOOL_CELL_RE = re.compile(r"^`([^`]+)@([0-9]+\.[0-9]+\.[0-9]+)`$")
WIRING_TASKS = frozenset({"T-25", "T-27", "T-28", "T-32"})

REQUIRED_COLUMNS = (
    "operation",
    "row",
    "tool/version",
    "controller method",
    "application use case",
    "permission/principal",
    "error codes",
    "wiring task",
)

REQUIRED_TEST_PHRASES = {
    "extension_negotiation": "not negotiated",
    "iserror_never_success": "isError is never promoted",
    "negative_iserror_mutant": "reporting ok for isError=true",
    "raw_prefix": "mcp__acp__",
    "read_without_prepare": "read without prepare",
    "long_task_reference_only": "long task returns only",
    "artifact_reference_digest": "artifact result exposes",
    "mutated_method": "mutated method",
}

SIX_BOUNDARY_CASES = (
    "iserror_never_success",
    "raw_prefix",
    "read_without_prepare",
    "long_task_reference_only",
    "artifact_reference_digest",
    "mutated_method",
)


def _parse_p2_table(text: str) -> list[dict[str, str]]:
    """Parse the doc table whose header declares ``tool/version``."""

    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if header is None:
            if stripped.startswith("|") and "tool/version" in stripped:
                header = [cell.strip() for cell in stripped.strip("|").split("|")]
            continue
        if not stripped.startswith("|"):
            break
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        if len(cells) != len(header):
            continue
        rows.append(dict(zip(header, cells, strict=True)))
    return rows


def _split_codes(cell: str) -> set[str]:
    return {item.strip().strip("`") for item in cell.split(",") if item.strip()}


def _gate(
    context: Any,
    name: str,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    log: list[str],
) -> int:
    result = _run(argv, cwd=cwd, timeout=timeout, env=env)
    combined = (result.stdout or "") + (result.stderr or "")
    log.append(f"$ {' '.join(argv)}\nrc={result.returncode}\n{combined}")
    _assertion(
        context,
        f"bridge.{name}",
        result.returncode == 0,
        f"rc={result.returncode} {(result.stderr or result.stdout).strip()[-400:]}",
    )
    return result.returncode


def scenario_bridge_contract(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    workspace = source / "rph"
    if not (workspace / "package.json").is_file():
        return _not_verified(context, f"missing_prerequisite:rph_workspace_absent:{workspace}")
    project = _env_path("ACP2_PROJECT_ROOT")
    if project is None:
        return _not_verified(context, "missing_prerequisite:ACP2_PROJECT_ROOT")
    map_path = source / MAP_RELATIVE
    if not map_path.is_file():
        return _not_verified(context, f"missing_prerequisite:map_absent:{map_path}")
    pnpm = _pnpm()
    if pnpm is None:
        return _not_verified(context, "missing_prerequisite:pnpm")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    kind = getattr(context.identity, "evidence_kind", None)
    _assertion(
        context,
        "bridge.evidence_kind_fixture",
        kind == "fixture",
        f"evidence_kind={kind}",
    )

    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node, pnpm)

    log: list[str] = []
    _gate(
        context,
        "install",
        [pnpm, "--dir", str(workspace), "install", "--frozen-lockfile"],
        cwd=source,
        env=env,
        timeout=timeout,
        log=log,
    )
    _gate(
        context,
        "build",
        [pnpm, "--dir", str(workspace), "build"],
        cwd=source,
        env=env,
        timeout=timeout,
        log=log,
    )
    test_run = _run(
        [pnpm, "--dir", str(workspace), "test"],
        cwd=source,
        timeout=timeout,
        env=env,
    )
    test_log = (test_run.stdout or "") + (test_run.stderr or "")
    log.append(f"$ {pnpm} --dir {workspace} test\nrc={test_run.returncode}\n{test_log}")
    _assertion(
        context,
        "bridge.test",
        test_run.returncode == 0,
        f"rc={test_run.returncode} {test_log.strip()[-400:]}",
    )
    _gate(
        context,
        "check",
        [pnpm, "--dir", str(workspace), "check"],
        cwd=source,
        env=env,
        timeout=timeout,
        log=log,
    )
    _gate(
        context,
        "boundaries",
        [node, str(workspace / "scripts/check-boundaries.mjs")],
        cwd=source,
        env=env,
        timeout=timeout,
        log=log,
    )

    count_match = re.search(r"^# tests (\d+)$", test_log, re.MULTILINE)
    test_count = int(count_match.group(1)) if count_match else 0
    fail_match = re.search(r"^# fail (\d+)$", test_log, re.MULTILINE)
    test_failures = int(fail_match.group(1)) if fail_match else -1
    _assertion(
        context,
        "bridge.test_count",
        test_count >= 10 and test_failures == 0,
        f"tests={test_count} failures={test_failures}",
    )
    missing_phrases = [
        name for name, phrase in REQUIRED_TEST_PHRASES.items() if phrase not in test_log
    ]
    _assertion(
        context,
        "bridge.boundary_cases_in_test_names",
        not missing_phrases,
        f"missing={missing_phrases} "
        f"covered={sorted(set(REQUIRED_TEST_PHRASES) - set(missing_phrases))}",
    )

    show = _run(
        ["git", "-C", str(project), "show", f"HEAD:{B03_RELATIVE}"],
        timeout=timeout,
        env=env,
    )
    if show.returncode != 0:
        return _not_verified(
            context,
            f"missing_prerequisite:b03_table_absent:{show.stderr.strip()[-300:]}",
        )
    blob_text = show.stdout
    blob_digest = hashlib.sha256(blob_text.encode("utf-8")).hexdigest()
    try:
        b03 = json.loads(blob_text)
    except json.JSONDecodeError as exc:
        _assertion(context, "bridge.b03_parse", False, f"json_parse:{exc}")
        return EXIT_FAIL

    _write_text(out, "p2-research-operations.v1.json", blob_text)
    _add_artifact(context, "p2-research-operations.v1.json")
    written = out / "p2-research-operations.v1.json"
    _assertion(
        context,
        "bridge.b03_snapshot_sha256",
        _sha256_file(written) == blob_digest,
        blob_digest,
    )

    doc_text = map_path.read_text(encoding="utf-8")
    doc_digests = DOC_SHA_RE.findall(doc_text)
    _assertion(
        context,
        "bridge.doc_declares_b03_sha256",
        doc_digests == [blob_digest],
        f"declared={doc_digests} committed={blob_digest}",
    )
    _assertion(
        context,
        "bridge.doc_extension_identity",
        EXTENSION_ID in doc_text
        and EXTENSION_VERSION in doc_text
        and "unsupported_capability" in doc_text
        and "extension_not_negotiated" in doc_text,
        f"{EXTENSION_ID}@{EXTENSION_VERSION}",
    )
    negotiation = (b03.get("client_path") or {}).get("negotiation") or {}
    refusal = negotiation.get("refusal") or {}
    _assertion(
        context,
        "bridge.b03_negotiation_rule",
        b03.get("schema") == "acp2.runtime.p2-research-operations.v1"
        and negotiation.get("initialize_param") == "capabilities.extensions"
        and negotiation.get("extension_id") == EXTENSION_ID
        and negotiation.get("extension_version") == EXTENSION_VERSION
        and refusal.get("code") == "unsupported_capability"
        and (refusal.get("params") or {}).get("rule") == "extension_not_negotiated",
        json.dumps(negotiation, sort_keys=True)[:400],
    )

    declared_p1 = set((b03.get("p1_allowlist_unchanged") or {}).get("tools") or [])
    _assertion(
        context,
        "bridge.p1_allowlist_is_eight",
        declared_p1 == set(P1_TOOLS) and len(declared_p1) == 8,
        f"declared={sorted(declared_p1)}",
    )

    b03_tools: dict[str, dict[str, Any]] = {}
    b03_rows: dict[str, dict[str, Any]] = {}
    for row in b03.get("rows") or []:
        row_id = row.get("id")
        if isinstance(row_id, str):
            b03_rows[row_id] = row
        for tool in row.get("tools") or []:
            name = tool.get("name")
            if isinstance(name, str):
                b03_tools[name] = {
                    "version": tool.get("tool_version"),
                    "namespaced_id": tool.get("namespaced_id"),
                    "row": row_id,
                    "error_classes": set(row.get("error_classes") or []),
                }
    combined = declared_p1 | set(b03_tools)
    _assertion(
        context,
        "bridge.allowlist_size_18",
        len(b03_tools) == 10 and len(combined) == 18,
        f"p1={len(declared_p1)} p2={len(b03_tools)} combined={len(combined)}",
    )

    dump = _run(
        [node, str(workspace / "scripts/dump-bridge-contract.mjs")],
        cwd=workspace,
        timeout=timeout,
        env=env,
    )
    _assertion(
        context,
        "bridge.dump_exit",
        dump.returncode == 0,
        f"rc={dump.returncode} {(dump.stderr or dump.stdout).strip()[-300:]}",
    )
    try:
        bridge_map = json.loads(dump.stdout)
    except json.JSONDecodeError as exc:
        _assertion(context, "bridge.dump_parse", False, f"json_parse:{exc}")
        return EXIT_FAIL
    _write_json(out, "bridge-map.json", bridge_map)
    _add_artifact(context, "bridge-map.json")

    extension = bridge_map.get("extension") or {}
    _assertion(
        context,
        "bridge.compiled_extension_identity",
        extension.get("id") == EXTENSION_ID
        and extension.get("version") == EXTENSION_VERSION
        and set(bridge_map.get("p1") or []) == set(P1_TOOLS),
        json.dumps(extension, sort_keys=True),
    )

    doc_rows = _parse_p2_table(doc_text)
    _assertion(
        context,
        "bridge.doc_table_columns",
        bool(doc_rows) and list(doc_rows[0].keys()) == list(REQUIRED_COLUMNS),
        f"columns={list(doc_rows[0].keys()) if doc_rows else []}",
    )

    doc_operations = [row.get("operation", "").strip("`") for row in doc_rows]
    compiled_rows = {item.get("operation"): item for item in bridge_map.get("p2") or []}
    _assertion(
        context,
        "bridge.map_has_no_missing_row",
        len(doc_operations) == len(set(doc_operations))
        and set(doc_operations) == set(compiled_rows)
        and len(compiled_rows) == 12,
        f"doc={len(doc_operations)} compiled={len(compiled_rows)} "
        f"diff={sorted(set(doc_operations) ^ set(compiled_rows))}",
    )

    coverage: list[str] = []
    mismatches: list[str] = []
    for row in doc_rows:
        operation = row.get("operation", "").strip("`")
        cell = row.get("tool/version", "")
        match = TOOL_CELL_RE.match(cell)
        if match is None:
            mismatches.append(f"{operation}:tool_cell_unparsable:{cell}")
            continue
        tool_name, tool_version = match.group(1), match.group(2)
        tool = b03_tools.get(tool_name)
        if tool is None:
            mismatches.append(f"{operation}:tool_not_in_b03:{tool_name}")
            continue
        coverage.append(tool_name)
        if tool["version"] != tool_version:
            mismatches.append(f"{operation}:version:{tool_version}!={tool['version']}")
        row_id = row.get("row", "").strip("`")
        if row_id != tool["row"]:
            mismatches.append(f"{operation}:row:{row_id}!={tool['row']}")
        compiled = compiled_rows.get(operation)
        if compiled is None:
            mismatches.append(f"{operation}:missing_in_bridge")
            continue
        if compiled.get("tool") != tool_name:
            mismatches.append(f"{operation}:bridge_tool:{compiled.get('tool')}!={tool_name}")
        if compiled.get("toolVersion") != tool_version:
            mismatches.append(f"{operation}:bridge_version_mismatch")
        if compiled.get("namespacedId") != tool["namespaced_id"]:
            mismatches.append(f"{operation}:namespaced_id_mismatch")
        method = row.get("controller method", "").strip("`")
        if method != compiled.get("controllerClientMethod"):
            mismatches.append(
                f"{operation}:method:{method}!={compiled.get('controllerClientMethod')}"
            )
        if row.get("wiring task", "").strip("`") != compiled.get("wiringTask"):
            mismatches.append(f"{operation}:wiring_task_mismatch")
        if not row.get("application use case"):
            mismatches.append(f"{operation}:missing_use_case")
        if not row.get("permission/principal"):
            mismatches.append(f"{operation}:missing_permission")
        codes = _split_codes(row.get("error codes", ""))
        if codes != tool["error_classes"]:
            mismatches.append(
                f"{operation}:error_codes:{sorted(codes)}!={sorted(tool['error_classes'])}"
            )
        if compiled.get("wiringTask") not in WIRING_TASKS:
            mismatches.append(f"{operation}:unknown_wiring_task")
    _assertion(
        context,
        "bridge.p2_rows_reference_committed_tools",
        not mismatches,
        f"mismatches={mismatches}",
    )
    _assertion(
        context,
        "bridge.every_b03_tool_has_a_row",
        set(coverage) == set(b03_tools),
        f"covered={len(set(coverage))} of {len(b03_tools)}",
    )

    allowlist = {
        "p1": sorted(declared_p1),
        "p2": sorted(b03_tools),
        "combined": sorted(combined),
    }
    allowlist_digest = hashlib.sha256(
        json.dumps(allowlist, sort_keys=True).encode("utf-8")
    ).hexdigest()
    _write_json(
        out,
        "tool-allowlist.json",
        {**allowlist, "sha256": allowlist_digest, "b03_sha256": blob_digest},
    )
    _add_artifact(context, "tool-allowlist.json")

    _write_text(out, "rph-test.log", _truncate(test_log, 40000))
    _add_artifact(context, "rph-test.log")
    _write_text(out, "rph-gates.log", _truncate("\n\n".join(log), 20000))
    _add_artifact(context, "rph-gates.log")

    report = {
        "b03": {
            "path": B03_RELATIVE,
            "sha256": blob_digest,
            "schema": b03.get("schema"),
        },
        "extension": extension,
        "allowlist": {**allowlist, "sha256": allowlist_digest},
        "p2_operations": doc_operations,
        "p2_tool_count": len(b03_tools),
        "tests": {"count": test_count, "failures": test_failures},
        "boundary_cases": list(SIX_BOUNDARY_CASES),
        "evidence_kind": kind,
        "closes_h2": False,
    }
    _write_json(out, "bridge-contract-report.json", report)
    _add_artifact(context, "bridge-contract-report.json")

    context.extensions.update(
        {
            "experience.bridge_contract.evidence_kind": kind,
            "experience.bridge_contract.closes_h2": False,
            "experience.bridge_contract.b03_sha256": blob_digest,
            "experience.bridge_contract.allowlist_size": len(combined),
            "experience.bridge_contract.p2_operations": len(doc_operations),
            "experience.bridge_contract.tests": test_count,
            "experience.bridge_contract.tool_allowlist_sha256": allowlist_digest,
            "experience.bridge_contract.extension": f"{EXTENSION_ID}@{EXTENSION_VERSION}",
        }
    )

    return _finish(context, "bridge_contract_assertion_failed")


register(SCENARIO_ID, scenario_bridge_contract)
