"""``question-reaction-fixtures`` — bind QuestionSpec/ReactionSpec into the Workbench.

This body is the T-23 / C17a acceptance row extended by T-24 / C17b
(chemistry ambiguity confirmation and negative inputs).  It never starts a
model, DSH, QC, LSF or browser process; it runs the zero-dependency Node
module harness on the Workbench reaction/question spec binding and reads the
committed feature modules.

Assertions:
* ``frontend/js/module_tests/run_all.js acp2_question_reaction`` exits 0 with
  at least eighteen passing tests and zero failures;
* the three negative rejection fixtures are present and passing: duplicate
  atom map, missing product, charge non-conservation;
* the net-reaction-as-single-TS clarification fixture and the CDXML ``DEFERRED``
  fixture are present and passing;
* the full clarification matrix is present and passing: ``missing_stereo``,
  ``missing_conditions``, ``mapping_ambiguity``, ``unknown_multiplicity`` and
  ``reaction.candidate_steps`` (stable view-model field names);
* at least five chemistry-ambiguity negatives are present and passing
  (unconfirmed clarification, hidden/omitted approximation, CDXML parse
  error, mapping ambiguity, unknown multiplicity, net multi-step bypass);
* the negative evidence bundle is written under ``negative/`` in the run
  directory (and mirrored to ``$ACP2_EVIDENCE/C/question-reaction-fixtures/
  negative/`` when ``ACP2_EVIDENCE`` is set).

A missing prerequisite (Node, source tree, module or test file) is
``NOT_VERIFIED`` (exit 2); a command that ran and produced a wrong result is
``FAIL`` (exit 1).  The harness normalizes this fixture-default scenario to
``experience:fixture`` with ``evidence_kind=fixture``.
"""

from __future__ import annotations

import json
import os
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
MIN_TESTS = 18
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

CLARIFICATION_MATRIX = (
    "missing_stereo",
    "missing_conditions",
    "mapping_ambiguity",
    "unknown_multiplicity",
    "reaction.candidate_steps",
)

AMBIGUITY_NEGATIVE_FRAGMENTS = (
    "unconfirmed clarification blocks submit",
    "approximation hidden/omitted blocks submit",
    "CDXML parse error shows error and no submit",
    "mapping ambiguity",
    "unknown multiplicity requires explicit input",
    "net multi-step prompt cannot be bypassed",
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

    missing_matrix = [
        name for name in CLARIFICATION_MATRIX if not any(name in line for line in ok_lines)
    ]
    _assertion(
        context,
        "question_reaction.clarification_matrix",
        not missing_matrix,
        "stable view-model clarification field names present and passing: "
        + ", ".join(CLARIFICATION_MATRIX)
        + ("; missing: " + ", ".join(missing_matrix) if missing_matrix else ""),
    )

    negative_cases = []
    for fragment in AMBIGUITY_NEGATIVE_FRAGMENTS:
        match = next((line.strip()[len("ok - "):] for line in ok_lines if fragment in line), None)
        negative_cases.append(
            {
                "fragment": fragment,
                "status": "PASS" if match else "MISSING",
                "test": match,
            }
        )
    matched_negatives = [item for item in negative_cases if item["status"] == "PASS"]
    total_negatives = len(AMBIGUITY_NEGATIVE_FRAGMENTS)
    _assertion(
        context,
        "question_reaction.ambiguity_negatives",
        len(matched_negatives) >= 5 and len(matched_negatives) == total_negatives,
        f"chemistry-ambiguity negatives passing: {len(matched_negatives)}/{total_negatives} "
        "(require >=5, all declared)",
    )

    negative_doc = {
        "scenario": SCENARIO_ID,
        "filter": FILTER,
        "evidence_kind": "fixture",
        "command": [node, str(runner), FILTER],
        "cwd": str(source),
        "exit_code": result.returncode,
        "passed": passed,
        "failed": failed,
        "clarification_matrix": list(CLARIFICATION_MATRIX),
        "required_negatives": list(AMBIGUITY_NEGATIVE_FRAGMENTS),
        "cases": negative_cases,
        "passing_tests": [line.strip()[len("ok - "):] for line in ok_lines],
    }
    negative_json_name = "negative/negative-cases.json"
    _write_json(out, negative_json_name, negative_doc)
    _add_artifact(context, negative_json_name)
    negative_log_name = "negative/negative-cases.log"
    case_lines = "\n".join(
        f"{item['status']} - {item['test'] or item['fragment']}" for item in negative_cases
    )
    _write_text(out, negative_log_name, _truncate(case_lines + "\n", 8000))
    _add_artifact(context, negative_log_name)

    stable_negative = None
    evidence_root = _env_path("ACP2_EVIDENCE")
    if evidence_root is not None:
        stable_dir = evidence_root / "C" / SCENARIO_ID / "negative"
        stable_dir.mkdir(parents=True, exist_ok=True)
        stable_doc = dict(negative_doc)
        stable_doc["authoritative_run"] = str(out)
        stable_doc["run_artifact"] = negative_json_name
        stable_path = stable_dir / "negative-cases.json"
        tmp_path = stable_dir / ".negative-cases.json.tmp"
        payload = json.dumps(stable_doc, indent=2, sort_keys=True) + "\n"
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(str(tmp_path), str(stable_path))
        stable_negative = str(stable_path)

    stable_detail = (
        f" and stable copy {stable_negative}"
        if stable_negative
        else " (stable copy skipped: no ACP2_EVIDENCE)"
    )
    stable_ok = evidence_root is None or stable_negative is not None
    _assertion(
        context,
        "question_reaction.negative_evidence",
        (out / negative_json_name).is_file() and stable_ok,
        "negative bundle at " + negative_json_name + stable_detail,
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
        "clarification_matrix": list(CLARIFICATION_MATRIX),
        "ambiguity_negative_fragments": list(AMBIGUITY_NEGATIVE_FRAGMENTS),
        "ambiguity_negatives_passed": len(matched_negatives),
        "negative_evidence": negative_json_name,
        "negative_evidence_stable": stable_negative,
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
            "experience.question_reaction_clarification_matrix": not missing_matrix,
            "experience.question_reaction_ambiguity_negatives": len(matched_negatives),
            "experience.question_reaction_negative_evidence": negative_json_name,
        }
    )

    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = "question_reaction_assertion_failed"
        return EXIT_FAIL
    return _finish(context, "question_reaction_assertion_failed")


register(SCENARIO_ID, scenario_question_reaction)
