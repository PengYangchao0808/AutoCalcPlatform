"""``question-reaction-fixtures`` — bind QuestionSpec/ReactionSpec into the Workbench.

This body is the T-23 / C17a acceptance row.  It never starts a model, DSH,
QC, LSF or browser process; it runs the zero-dependency Node module harness on
the Workbench reaction/question spec binding and reads the committed feature
modules.

Assertions:
* ``frontend/js/module_tests/run_all.js acp2_question_reaction`` exits 0 with
  at least ten passing tests and zero failures;
* the three negative rejection fixtures are present and passing: duplicate
  atom map, missing product, charge non-conservation;
* the net-reaction-as-single-TS clarification fixture and the CDXML ``DEFERRED``
  fixture are present and passing.

A missing prerequisite (Node, source tree, module or test file) is
``NOT_VERIFIED`` (exit 2); a command that ran and produced a wrong result is
``FAIL`` (exit 1).  The harness normalizes this fixture-default scenario to
``experience:fixture`` with ``evidence_kind=fixture``.
"""

from __future__ import annotations

import re
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

SCENARIO_ID = "question-reaction-fixtures"
FILTER = "acp2_question_reaction"
MIN_TESTS = 10
NODE_SUITE_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")

REQUIRED_PATHS = (
    "frontend/js/workbench/features/reaction-builder.js",
    "frontend/js/workbench/features/science-question.js",
    "frontend/js/module_tests/acp2_question_reaction.test.js",
    "frontend/js/module_tests/run_all.js",
    "docs/reference/next-change/acp2-contracts/experience/requests/A-req-02.md",
    "docs/reference/next-change/acp2-contracts/experience/requests/B-req-09.md",
)

NEGATIVE_TEST_FRAGMENTS = (
    "duplicate atom map",
    "missing product",
    "charge non-conservation",
)

CLARIFICATION_FRAGMENT = "clarification item"
DEFERRED_FRAGMENT = "DEFERRED"


def _passing_test_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.strip().startswith("ok - ")]


def scenario_question_reaction(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    missing = [rel for rel in REQUIRED_PATHS if not (source / rel).is_file()]
    if missing:
        return _not_verified(
            context, "missing_prerequisite:question_reaction_module_absent:" + ",".join(missing)
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
        "question_reaction.node_suite",
        result.returncode == 0 and failed == 0 and passed >= MIN_TESTS,
        f"rc={result.returncode} passed={passed} failed={failed} min={MIN_TESTS}",
    )

    ok_lines = _passing_test_lines(combined)
    missing_negatives = [
        fragment
        for fragment in NEGATIVE_TEST_FRAGMENTS
        if not any(fragment in line for line in ok_lines)
    ]
    _assertion(
        context,
        "question_reaction.negative_rejections",
        not missing_negatives,
        "passing negative fixtures for: "
        + ", ".join(NEGATIVE_TEST_FRAGMENTS)
        + ("; missing: " + ", ".join(missing_negatives) if missing_negatives else ""),
    )

    _assertion(
        context,
        "question_reaction.clarification_not_submit",
        any(CLARIFICATION_FRAGMENT in line for line in ok_lines),
        "net-reaction-as-single-TS must yield a clarification item, not a submit; "
        f"fragment={CLARIFICATION_FRAGMENT!r} "
        f"present={any(CLARIFICATION_FRAGMENT in line for line in ok_lines)}",
    )

    _assertion(
        context,
        "question_reaction.cdxml_deferred",
        any(DEFERRED_FRAGMENT in line for line in ok_lines),
        "CDXML input must stay DEFERRED and never parse to an empty structure; "
        f"fragment={DEFERRED_FRAGMENT!r} "
        f"present={any(DEFERRED_FRAGMENT in line for line in ok_lines)}",
    )

    log_name = "question-reaction-fixtures.log"
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
        "negative_fragments": list(NEGATIVE_TEST_FRAGMENTS),
        "clarification_fragment": CLARIFICATION_FRAGMENT,
        "deferred_fragment": DEFERRED_FRAGMENT,
        "passing_tests": [line.strip()[len("ok - "):] for line in ok_lines],
    }
    summary_name = "question-reaction-fixtures.json"
    _write_json(out, summary_name, summary)
    _add_artifact(context, summary_name)

    context.extensions.update(
        {
            "experience.question_reaction_suite_passed": passed,
            "experience.question_reaction_suite_failed": failed,
            "experience.question_reaction_min_tests": MIN_TESTS,
            "experience.question_reaction_negative_rejections": not missing_negatives,
        }
    )

    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = "question_reaction_assertion_failed"
        return EXIT_FAIL
    return _finish(context, "question_reaction_assertion_failed")


register(SCENARIO_ID, scenario_question_reaction)
