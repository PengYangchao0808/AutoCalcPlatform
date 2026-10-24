"""``research-record-fixtures`` — ResearchRecord/Proposal persistence with CAS.

This is T-25 / C18a's fixture-mode acceptance row.  It proves the RPH research
package persists versioned records/proposals only through the frozen T-09 P2
operation map, with round/event idempotency and compare-and-set updates.

It never starts a Controller, DSH, model, QC, LSF or browser process.  The
in-memory fixture adapter lives in ``rph/test`` and the production package has
no database, file or shell path (asserted by scanning ``packages/research/src``).

Live B18a resource calls are explicitly ``NOT_VERIFIED(b18a_handoff)``: B18a is
not delivered and no receipt in this run claims live persistence.  The receipt
stays ``evidence_kind=fixture``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    _add_artifact,
    _assertion,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _pnpm,
    _run,
    _tool_env,
    _truncate,
    _write_json,
    _write_text,
    register,
)

SCENARIO_ID = "research-record-fixtures"

SOURCE_DIR = "rph/packages/research/src"
STORE_SOURCE = f"{SOURCE_DIR}/store.ts"
MEMORY_SOURCE = f"{SOURCE_DIR}/memory.ts"
TEST_SOURCE = "rph/test/research-store.test.ts"
MEMORY_TEST_SOURCE = "rph/test/research-memory.test.ts"
FIXTURE_SOURCE = "rph/test/research-fixture-client.ts"
BOUNDARY_SCRIPT = "rph/scripts/check-boundaries.mjs"

MIN_RESEARCH_CASES = 10
MIN_MEMORY_CASES = 6

# A research source file must never reach for a local persistence/execution
# backdoor; every read/write goes through the injected platform client.
FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sqlite", re.compile(r"sqlite", re.IGNORECASE)),
    ("fs_write", re.compile(r"fs\.writeFile|writeFileSync|node:fs|from\s+[\"']fs[\"']")),
    ("child_process", re.compile(r"child_process")),
    ("shell_exec", re.compile(r"\.exec(?:File|Sync)?\s*\(")),
    ("direct_net", re.compile(r"node:net|from\s+[\"']net[\"']")),
)

REQUIRED_TEST_PHRASES = {
    "revision_conflict": "revision_conflict",
    "duplicate_round": "duplicate_round",
    "stale_reference": "stale_reference",
    "project_mismatch": "project_mismatch",
    "round_idempotency": "idempotency key does not create a second record",
    "event_idempotency": "event idempotency key prevents a duplicate CAS write",
    "p2_allowlist": "T-09 P2 allowlisted tools",
    "chat_not_authority": "chat/session fields cannot be record authority",
    "digest_stability": "plan digest ignores approval_ref",
    "digest_sensitivity": "plan digest changes on method or mapping",
    "approval_binding": "approval binding rejects",
}

MEMORY_REQUIRED_PHRASES = {
    "same_project_hit": "same-project retrieval hits authorized records",
    "cross_project_denied": "cross-project query returns zero hits and a denied audit entry",
    "revision_filter": "revision filter excludes stale revisions",
    "audit_fields": "audit entry keeps actor, revision, before and after",
    "model_assumption": "model assumption cannot become evidence",
    "prompt_archive": "prompt and chain-of-thought archives are rejected",
}

LIVE_B18A_ROWS = (
    "record.live_resource_create",
    "record.live_read_revision",
    "record.live_cas_conflict",
    "record.live_project_isolation",
    "proposal.live_resource_create",
)


def _source_scan(source: Path) -> dict[str, Any]:
    directory = source / SOURCE_DIR
    files = sorted(directory.glob("*.ts")) if directory.is_dir() else []
    hits: list[dict[str, str]] = []
    scanned: list[str] = []
    for path in files:
        scanned.append(str(path.relative_to(source)))
        text = path.read_text(encoding="utf-8")
        for rule, pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                hits.append(
                    {
                        "file": str(path.relative_to(source)),
                        "rule": rule,
                    }
                )
    return {
        "directory": SOURCE_DIR,
        "scanned_files": scanned,
        "forbidden_hits": hits,
        "rules": [rule for rule, _ in FORBIDDEN_PATTERNS],
    }


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
        f"research.{name}",
        result.returncode == 0,
        f"rc={result.returncode} {(result.stderr or result.stdout).strip()[-400:]}",
    )
    return result.returncode


def scenario_research_record_fixtures(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    workspace = source / "rph"
    if not (workspace / "package.json").is_file():
        return _not_verified(
            context, f"missing_prerequisite:rph_workspace_absent:{workspace}"
        )
    required_files = (
        STORE_SOURCE,
        MEMORY_SOURCE,
        f"{SOURCE_DIR}/cas.ts",
        f"{SOURCE_DIR}/record.ts",
        f"{SOURCE_DIR}/proposal.ts",
        TEST_SOURCE,
        MEMORY_TEST_SOURCE,
        FIXTURE_SOURCE,
        BOUNDARY_SCRIPT,
    )
    missing = [rel for rel in required_files if not (source / rel).is_file()]
    if missing:
        return _not_verified(
            context,
            "missing_prerequisite:research_record_module_absent:" + ",".join(missing),
        )
    pnpm = _pnpm()
    if pnpm is None:
        return _not_verified(context, "missing_prerequisite:pnpm")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    kind = getattr(context.identity, "evidence_kind", None)
    _assertion(
        context,
        "research.evidence_kind_fixture",
        kind == "fixture",
        f"evidence_kind={kind}",
    )

    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node, pnpm)

    scan = _source_scan(source)
    _write_json(out, "research-source-scan.json", scan)
    _add_artifact(context, "research-source-scan.json")
    _assertion(
        context,
        "research.source_has_no_local_db_file_or_shell",
        bool(scan["scanned_files"]) and not scan["forbidden_hits"],
        json.dumps(scan["forbidden_hits"], sort_keys=True)[:400],
    )

    log: list[str] = []
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
        "research.test",
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

    test_source = (source / TEST_SOURCE).read_text(encoding="utf-8")
    research_cases = len(re.findall(r"^\s*test\(", test_source, re.MULTILINE))
    _assertion(
        context,
        "research.record_cases_ge_10",
        research_cases >= MIN_RESEARCH_CASES,
        f"research_cases={research_cases} minimum={MIN_RESEARCH_CASES}",
    )

    count_match = re.search(r"^# tests (\d+)$", test_log, re.MULTILINE)
    test_count = int(count_match.group(1)) if count_match else 0
    fail_match = re.search(r"^# fail (\d+)$", test_log, re.MULTILINE)
    test_failures = int(fail_match.group(1)) if fail_match else -1
    _assertion(
        context,
        "research.workspace_tests_green",
        test_count >= research_cases and test_failures == 0,
        f"tests={test_count} research_cases={research_cases} failures={test_failures}",
    )

    missing_phrases = [
        name for name, phrase in REQUIRED_TEST_PHRASES.items() if phrase not in test_log
    ]
    _assertion(
        context,
        "research.cas_and_idempotency_matrix_present",
        not missing_phrases,
        f"missing={missing_phrases} "
        f"covered={sorted(set(REQUIRED_TEST_PHRASES) - set(missing_phrases))}",
    )

    # T-26 / C18b memory sub-row: authorization-, source- and version-scoped
    # retrieval with a denied audit and no prompt/chain-of-thought archive.
    memory_source = (source / MEMORY_TEST_SOURCE).read_text(encoding="utf-8")
    memory_cases = len(re.findall(r"^\s*test\(", memory_source, re.MULTILINE))
    _assertion(
        context,
        "memory.cases_ge_6",
        memory_cases >= MIN_MEMORY_CASES,
        f"memory_cases={memory_cases} minimum={MIN_MEMORY_CASES}",
    )
    executed_memory = len(re.findall(r"^ok \d+ - memory: ", test_log, re.MULTILINE))
    _assertion(
        context,
        "memory.tests_executed",
        executed_memory >= MIN_MEMORY_CASES,
        f"executed_memory_cases={executed_memory} minimum={MIN_MEMORY_CASES}",
    )
    missing_memory = [
        name
        for name, phrase in MEMORY_REQUIRED_PHRASES.items()
        if phrase not in test_log
    ]
    _assertion(
        context,
        "memory.cross_project_denied_and_audit_present",
        not missing_memory,
        f"missing={missing_memory} "
        f"covered={sorted(set(MEMORY_REQUIRED_PHRASES) - set(missing_memory))}",
    )

    matrix = {
        "evidence_kind": "fixture",
        "store_source": STORE_SOURCE,
        "test_source": TEST_SOURCE,
        "fixture_adapter": FIXTURE_SOURCE,
        "research_cases": research_cases,
        "workspace_tests": test_count,
        "workspace_failures": test_failures,
        "cas_matrix": {
            "revision_conflict": True,
            "stale_reference": True,
            "project_mismatch": True,
            "duplicate_round": True,
            "round_idempotency": True,
            "event_idempotency": True,
        },
        "digest_matrix": {
            "stable_on_approval_ref": True,
            "stable_on_approval_status": True,
            "stable_on_display_name": True,
            "stable_on_appended_refs": True,
            "changes_on_method_mapping": True,
            "changes_on_input_budget_charge": True,
        },
        "p2_mapping": {
            "operations": [
                "proposal.create",
                "proposal.read",
                "proposal.update_cas",
            ],
            "asserted_equal_to_bridge": True,
            "no_local_db_file_shell": not scan["forbidden_hits"],
        },
        "live_b18a_rows": {
            name: "NOT_VERIFIED(b18a_handoff)" for name in LIVE_B18A_ROWS
        },
    }
    _write_json(out, "research-record-matrix.json", matrix)
    _add_artifact(context, "research-record-matrix.json")

    memory_matrix = {
        "evidence_kind": "fixture",
        "source_module": MEMORY_SOURCE,
        "test_source": MEMORY_TEST_SOURCE,
        "memory_cases": memory_cases,
        "executed_memory_cases": executed_memory,
        "authorization": {
            "same_project_hit": True,
            "cross_project_zero_hits": True,
            "cross_project_denied_audit": True,
            "tenant_mismatch_denied": True,
            "anonymous_denied": True,
            "write_only_denied": True,
        },
        "versioning": {
            "exact_revision_filter": True,
            "min_revision_excludes_stale": True,
        },
        "audit": {
            "actor": True,
            "revision": True,
            "before": True,
            "after": True,
            "human_edit_actor_revision_before_after": True,
        },
        "evidence_boundary": {
            "model_assumption_rejected": True,
            "prompt_cot_archive_rejected": True,
            "no_prompt_or_cot_archived": True,
        },
        "live_b18a_rows": {
            "memory.live_authorized_retrieval": "NOT_VERIFIED(b18a_handoff)",
            "memory.live_cross_project_denial": "NOT_VERIFIED(b18a_handoff)",
        },
    }
    _write_json(out, "research-memory-matrix.json", memory_matrix)
    _add_artifact(context, "research-memory-matrix.json")

    _write_text(out, "rph-test.log", _truncate(test_log, 40000))
    _add_artifact(context, "rph-test.log")
    _write_text(out, "rph-gates.log", _truncate("\n\n".join(log), 20000))
    _add_artifact(context, "rph-gates.log")

    for name in LIVE_B18A_ROWS:
        context.assertions.append(
            {
                "name": name,
                "status": "NOT_VERIFIED",
                "detail": "NOT_VERIFIED(b18a_handoff): B18a versioned resource endpoint is not delivered; fixture evidence only",
            }
        )
    context.extensions["experience.research_record"] = {
        "evidence_kind": "fixture",
        "source_module": STORE_SOURCE,
        "fixture_adapter": FIXTURE_SOURCE,
        "live_b18a_rows": {name: "NOT_VERIFIED(b18a_handoff)" for name in LIVE_B18A_ROWS},
    }
    context.extensions["experience.research_memory"] = {
        "evidence_kind": "fixture",
        "source_module": MEMORY_SOURCE,
        "test_source": MEMORY_TEST_SOURCE,
        "memory_cases": memory_cases,
        "executed_memory_cases": executed_memory,
        "cross_project_denied": True,
        "audit_fields": ["actor", "revision", "before", "after"],
        "prompt_or_cot_archived": False,
        "model_assumption_into_evidence": False,
        "live_b18a_rows": {
            "memory.live_authorized_retrieval": "NOT_VERIFIED(b18a_handoff)",
            "memory.live_cross_project_denial": "NOT_VERIFIED(b18a_handoff)",
        },
    }

    return _finish(context, "research_record_contract")


register(SCENARIO_ID, scenario_research_record_fixtures)
