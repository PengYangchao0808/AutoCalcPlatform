"""``client-fixtures`` — prove the science/research client query layer offline.

This body is the T-15 / C10a acceptance row.  It never starts a model, DSH, QC,
LSF or browser process; it runs the zero-dependency Node module harness and
reads the two committed client modules plus their contract validator.

Assertions:
* ``frontend/js/module_tests/run_all.js acp2_client`` exits 0 with at least ten
  passing tests and zero failures;
* the same run includes the two cross-project key-isolation tests and the two
  late-response ``StaleDiscard`` tests;
* ``science-query.js`` and ``research-query.js`` contain zero ``fetch(`` calls
  (all reads go through the injected adapters and ``ACPClient.store.query``).

A missing prerequisite (Node, source tree, module or contract file) is
``NOT_VERIFIED`` (exit 2); a command that ran and produced a wrong result is
``FAIL`` (exit 1).  The harness normalizes this fixture-default scenario to
``experience:fixture`` with ``evidence_kind=fixture``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    EXIT_FAIL,
    _add_artifact,
    _assertion,
    _env_path,
    _finish,
    _node,
    _not_verified,
    _out_dir,
    _run,
    _tool_env,
    _truncate,
    _write_json,
    _write_text,
    register,
)

SCENARIO_ID = "client-fixtures"
FILTER = "acp2_client"
MIN_TESTS = 10
NODE_SUITE_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")

MODULE_PATHS = (
    "frontend/js/client/science-query.js",
    "frontend/js/client/research-query.js",
)
REQUIRED_PATHS = MODULE_PATHS + (
    "frontend/js/workbench/contracts/experience_contract.js",
    "frontend/js/module_tests/acp2_client.test.js",
    "frontend/js/module_tests/run_all.js",
)

# The suite names are the machine-checkable cross-project / stale evidence.
REQUIRED_TEST_FRAGMENTS = (
    "cross-project same resource yields distinct keys",
    "cross-project same record id yields distinct keys",
)


def _fetch_hits(path: Path) -> int:
    """Count literal ``fetch(`` occurrences in one client module."""

    return path.read_text(encoding="utf-8").count("fetch(")


def scenario_client_fixtures(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    missing = [rel for rel in REQUIRED_PATHS if not (source / rel).is_file()]
    if missing:
        return _not_verified(
            context, "missing_prerequisite:client_module_absent:" + ",".join(missing)
        )

    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node)
    runner = source / "frontend/js/module_tests/run_all.js"

    result = _run([node, str(runner), FILTER], cwd=source, timeout=timeout, env=env)
    combined = (result.stdout or "") + (result.stderr or "")
    match = NODE_SUITE_RE.search(combined)
    passed = int(match.group(1)) if match else 0
    failed = int(match.group(2)) if match else 0

    _assertion(
        context,
        "client.node_suite",
        result.returncode == 0 and failed == 0 and passed >= MIN_TESTS,
        f"rc={result.returncode} passed={passed} failed={failed} min={MIN_TESTS}",
    )

    _assertion(
        context,
        "client.cross_project_isolation",
        all(fragment in combined for fragment in REQUIRED_TEST_FRAGMENTS),
        "; ".join(
            f"{fragment}={'present' if fragment in combined else 'absent'}"
            for fragment in REQUIRED_TEST_FRAGMENTS
        ),
    )

    _assertion(
        context,
        "client.stale_discard",
        combined.count("StaleDiscard") >= 2,
        f"occurrences={combined.count('StaleDiscard')}",
    )

    hits: dict[str, int] = {}
    for rel in MODULE_PATHS:
        count = _fetch_hits(source / rel)
        if count:
            hits[rel] = count
    _assertion(
        context,
        "client.no_fetch",
        not hits,
        "zero fetch( expected; "
        + (
            ", ".join(f"{path}={count}" for path, count in hits.items())
            if hits
            else "observed 0 in both modules"
        ),
    )

    log_name = "client-fixtures.log"
    _write_text(out, log_name, _truncate(combined, 16000))
    _add_artifact(context, log_name)

    summary = {
        "scenario": SCENARIO_ID,
        "filter": FILTER,
        "command": [node, str(runner), FILTER],
        "cwd": str(source),
        "exit_code": result.returncode,
        "passed": passed,
        "failed": failed,
        "min_tests": MIN_TESTS,
        "cross_project_tests": list(REQUIRED_TEST_FRAGMENTS),
        "stale_discard_occurrences": combined.count("StaleDiscard"),
        "modules": {rel: {"fetch_hits": _fetch_hits(source / rel)} for rel in MODULE_PATHS},
    }
    summary_name = "client-fixtures.json"
    _write_json(out, summary_name, summary)
    _add_artifact(context, summary_name)

    context.extensions.update(
        {
            "experience.client_suite_passed": passed,
            "experience.client_suite_failed": failed,
            "experience.client_min_tests": MIN_TESTS,
            "experience.client_fetch_hits": sum(hits.values()),
        }
    )

    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = "client_fixtures_assertion_failed"
        return EXIT_FAIL
    return _finish(context, "client_fixtures_assertion_failed")


register(SCENARIO_ID, scenario_client_fixtures)
