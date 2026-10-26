#!/usr/bin/env python3.11
"""M0-11: mechanical validator for the identity decision freeze.

``docs/reference/next-change/acp2-contracts/decision-freeze/identity.v1.json``
is a decision record, not prose: every claim in it must be checkable.  This
validator enforces exactly that:

* structure — schema, contract version, owners, status vocabulary;
* per-decision required fields — id/title/status/owner/decision/impact/
  wire-or-migration version/rollback/vectors;
* **status honesty** — a ``pending_*`` decision must carry
  ``pending_reason``/``consumer_task``/``frozen_forward_convention`` and may
  never look frozen; a ``frozen`` decision must not carry pending fields;
* **vector resolution** — every ``vectors[].test`` must name a real file and a
  real test in it (``tests/x.py::test_y`` or
  ``rph/test/x.test.ts::<exact node:test title>``);
* **impact existence** — every ``impact.files[]`` path exists;
* **client-inventory freshness** — the embedded inventory digest must equal a
  live run of :mod:`scripts.acceptance.check_v1_request_field_consumers`, and
  every consumer a decision claims as compatible/breaking must exist in that
  live scan with the same disposition;
* **field policy** — the recorded accepted/server-path field sets and the empty
  legacy allowlist must match the live policy table;
* **P2 status enum** — every ``implementation_status`` in
  ``p2-research-operations.v1.json`` is ``planned|delivered|verified`` and the
  recorded current values match the file.

Exit codes
----------
``0``  VALID.
``1``  INVALID (the first failure is printed to stderr; ``--json`` lists all).
``2``  the validator could not run (missing file, unreadable checkout).

Usage
-----
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py --json
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py --freeze path/to/identity.v1.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FREEZE = "docs/reference/next-change/acp2-contracts/decision-freeze/identity.v1.json"
SCHEMA = "acp2.decision-freeze.identity.v1"
P2_CONTRACT = "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json"

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2

STATUS_FROZEN = "frozen"
STATUS_PENDING_IMPLEMENTATION = "pending_implementation"
STATUS_PENDING_CONFLICT = "pending_conflict"
STATUSES = frozenset({STATUS_FROZEN, STATUS_PENDING_IMPLEMENTATION, STATUS_PENDING_CONFLICT})
IMPLEMENTATION_STATUSES = frozenset({"planned", "delivered", "verified"})

DECISION_ID = re.compile(r"^D-IDENT-\d{2}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TOP_LEVEL_REQUIRED = (
    "schema",
    "contract_version",
    "frozen_at",
    "owner",
    "task",
    "closes",
    "status_vocabulary",
    "pending_rule",
    "consumers_by_task",
    "client_inventory",
    "decisions",
    "pending_summary",
    "observations",
    "validator",
    "validator_tests",
)
DECISION_REQUIRED = (
    "id",
    "title",
    "status",
    "owner",
    "signoff",
    "decision",
    "rationale",
    "impact",
    "wire_version",
    "migration_version",
    "rollback",
    "compatible_clients",
    "vectors",
)
PENDING_REQUIRED = ("pending_reason", "consumer_task", "frozen_forward_convention", "blocking")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _test_names(path: Path, *, kind: str) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if kind == "pytest":
        return set(
            re.findall(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", text, re.MULTILINE)
        )
    return set(re.findall(r"\b(?:test|it)\(\s*[\"']([^\"']+)[\"']", text))


def _check_vector(vector: Mapping[str, Any], repo_root: Path, decisions_id: str) -> list[str]:
    """Return the failures for one ``vectors[]`` entry."""
    errors: list[str] = []
    reference = vector.get("test")
    if not isinstance(reference, str) or "::" not in reference:
        return [
            f"{decisions_id}: vector reference must be '<path>::<test name>', got {reference!r}"
        ]
    assert isinstance(reference, str)
    relative, _, name = reference.partition("::")
    if not isinstance(vector.get("asserts"), str) or not vector["asserts"].strip():
        errors.append(f"{decisions_id}: vector {reference!r} must say what it asserts")
    candidate = (repo_root / relative).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return [f"{decisions_id}: vector path escapes the repository: {relative}"]
    if not candidate.is_file():
        return [f"{decisions_id}: vector file does not exist: {relative}"]
    kind = "pytest" if candidate.suffix == ".py" else "node"
    if kind == "node" and candidate.suffix not in {".ts", ".mjs", ".js"}:
        return [f"{decisions_id}: unsupported vector file type: {relative}"]
    if name not in _test_names(candidate, kind=kind):
        return [f"{decisions_id}: test {name!r} not found in {relative}"]
    return errors


def _live_inventory(repo_root: Path) -> Mapping[str, Any] | None:
    """Run the consumer scanner against the live checkout."""
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from scripts.acceptance.check_v1_request_field_consumers import build_report
    except ImportError:  # pragma: no cover - checkout failure
        return None
    return build_report(repo_root)


def _check_client_inventory(
    freeze: Mapping[str, Any], live: Mapping[str, Any], repo_root: Path
) -> list[str]:
    """Return the failures of the embedded client inventory."""
    errors: list[str] = []
    inventory = freeze.get("client_inventory")
    if not isinstance(inventory, Mapping):
        return ["client_inventory must be an object"]
    if inventory.get("digest") != live.get("digest"):
        errors.append(
            "client_inventory.digest is stale "
            f"({inventory.get('digest')!r} != {live.get('digest')!r}); "
            "regenerate with scripts/acceptance/check_v1_request_field_consumers.py"
        )
    live_consumers = {
        (entry["consumer"], entry["path"]): entry["disposition"] for entry in live["consumers"]
    }
    embedded = inventory.get("consumers")
    if not isinstance(embedded, Sequence) or isinstance(embedded, (str, bytes)):
        return errors + ["client_inventory.consumers must be a list"]
    missing = [
        f"{entry.get('consumer')} @ {entry.get('path')}"
        for entry in embedded
        if (entry.get("consumer"), entry.get("path")) not in live_consumers
    ]
    if missing:
        errors.append(f"client_inventory names consumers the scanner no longer finds: {missing}")
    if len(embedded) != len(live["consumers"]):
        errors.append(
            "client_inventory.consumers length differs from the live scan "
            f"({len(embedded)} != {len(live['consumers'])})"
        )
    summary = inventory.get("summary")
    if not isinstance(summary, Mapping) or summary != live["summary"]:
        errors.append("client_inventory.summary differs from the live scan summary")
    del repo_root
    return errors


def _check_decisions(
    decisions: Sequence[Mapping[str, Any]],
    freeze: Mapping[str, Any],
    live: Mapping[str, Any] | None,
    repo_root: Path,
) -> list[str]:
    """Return the failures of the decision list."""
    errors: list[str] = []
    seen: set[str] = set()
    live_consumers = (
        {}
        if live is None
        else {
            (entry["consumer"], entry["path"]): entry["disposition"] for entry in live["consumers"]
        }
    )
    for decision in decisions:
        if not isinstance(decision, Mapping):
            errors.append(f"decision entry is not an object: {decision!r}")
            continue
        identifier = decision.get("id")
        if not isinstance(identifier, str) or DECISION_ID.match(identifier) is None:
            errors.append(f"decision id is malformed: {identifier!r}")
            continue
        if identifier in seen:
            errors.append(f"{identifier}: duplicate decision id")
        seen.add(identifier)
        for field in DECISION_REQUIRED:
            if field not in decision:
                errors.append(f"{identifier}: missing required field {field!r}")
        status = decision.get("status")
        if status not in STATUSES:
            errors.append(f"{identifier}: status {status!r} is not one of {sorted(STATUSES)}")
            continue
        if status == STATUS_FROZEN:
            leaked = [field for field in PENDING_REQUIRED if field in decision]
            if leaked:
                errors.append(
                    f"{identifier}: a frozen decision must not carry pending fields {leaked}"
                )
            vectors = decision.get("vectors")
            if (
                not isinstance(vectors, Sequence)
                or isinstance(vectors, (str, bytes))
                or not vectors
            ):
                errors.append(f"{identifier}: a frozen decision needs at least one vector")
        else:
            for field in PENDING_REQUIRED:
                if field not in decision:
                    errors.append(f"{identifier}: a {status} decision must carry {field!r}")
            if (
                decision.get("frozen_forward_convention")
                and status != STATUS_PENDING_IMPLEMENTATION
            ):
                errors.append(
                    f"{identifier}: frozen_forward_convention is only legal with "
                    f"{STATUS_PENDING_IMPLEMENTATION}"
                )
            if not str(decision.get("pending_reason") or "").strip():
                errors.append(f"{identifier}: pending_reason must be a non-empty explanation")
            if decision.get("status") == STATUS_FROZEN:  # pragma: no cover - defensive
                errors.append(f"{identifier}: an undecided item may not be frozen")
        vectors = decision.get("vectors") or []
        if not isinstance(vectors, Sequence) or isinstance(vectors, (str, bytes)):
            errors.append(f"{identifier}: vectors must be a list")
        else:
            for vector in vectors:
                if isinstance(vector, Mapping):
                    errors.extend(_check_vector(vector, repo_root, identifier))
                else:
                    errors.append(f"{identifier}: vector entry is not an object")
        impact = decision.get("impact")
        if not isinstance(impact, Mapping):
            errors.append(f"{identifier}: impact must be an object")
        else:
            files = impact.get("files")
            if not isinstance(files, Sequence) or isinstance(files, (str, bytes)) or not files:
                errors.append(f"{identifier}: impact.files must list at least one file")
            else:
                for relative in files:
                    if not isinstance(relative, str):
                        errors.append(f"{identifier}: impact.files entry is not a string")
                    elif not (repo_root / relative).exists():
                        errors.append(f"{identifier}: impact file does not exist: {relative}")
            entries = impact.get("entries")
            if (
                not isinstance(entries, Sequence)
                or isinstance(entries, (str, bytes))
                or not entries
            ):
                errors.append(f"{identifier}: impact.entries must list at least one entry")
        if not isinstance(decision.get("signoff"), Sequence) or isinstance(
            decision.get("signoff"), (str, bytes)
        ):
            errors.append(f"{identifier}: signoff must be a list")
        for field in ("wire_version", "migration_version"):
            if field not in decision:
                errors.append(f"{identifier}: {field} must be present (null when not applicable)")
        rollback = decision.get("rollback")
        if not isinstance(rollback, str) or len(rollback.strip()) < 20:
            errors.append(f"{identifier}: rollback must be a real statement")
        clients = decision.get("compatible_clients")
        if not isinstance(clients, Sequence) or isinstance(clients, (str, bytes)):
            errors.append(f"{identifier}: compatible_clients must be a list")
        else:
            for client in clients:
                if not isinstance(client, Mapping):
                    errors.append(f"{identifier}: compatible_clients entry is not an object")
                    continue
                key = (client.get("consumer"), client.get("path"))
                if live_consumers and key not in live_consumers:
                    errors.append(f"{identifier}: compatible client {key} is not in the live scan")
                elif live_consumers and client.get("disposition") != live_consumers[key]:
                    errors.append(
                        f"{identifier}: compatible client {key[0]} disposition "
                        f"{client.get('disposition')!r} != live {live_consumers[key]!r}"
                    )
    errors.extend(_check_pending_summary(decisions, freeze))
    return errors


def _check_pending_summary(
    decisions: Sequence[Mapping[str, Any]], freeze: Mapping[str, Any]
) -> list[str]:
    """Return the failures of ``pending_summary`` (it must mirror the decisions)."""
    errors: list[str] = []
    summary = freeze.get("pending_summary")
    if not isinstance(summary, Sequence) or isinstance(summary, (str, bytes)):
        return ["pending_summary must be a list"]
    expected = {
        decision["id"]: decision
        for decision in decisions
        if isinstance(decision, Mapping) and decision.get("status") in STATUSES - {STATUS_FROZEN}
    }
    listed = {entry.get("id"): entry for entry in summary if isinstance(entry, Mapping)}
    if set(listed) != set(expected):
        errors.append(
            "pending_summary ids must equal the non-frozen decisions "
            f"({sorted(listed)} != {sorted(expected)})"
        )
    for identifier, decision in expected.items():
        entry = listed.get(identifier)
        if entry is None:
            continue
        if entry.get("status") != decision.get("status"):
            errors.append(f"{identifier}: pending_summary status differs from the decision")
        if entry.get("consumer_task") != decision.get("consumer_task"):
            errors.append(f"{identifier}: pending_summary consumer_task differs from the decision")
    return errors


def _check_field_decision(
    decisions: Sequence[Mapping[str, Any]], live: Mapping[str, Any] | None
) -> list[str]:
    """Return the failures of the v1 field decision against the live policy."""
    errors: list[str] = []
    decision = next(
        (
            item
            for item in decisions
            if isinstance(item, Mapping) and item.get("id") == "D-IDENT-02"
        ),
        None,
    )
    if decision is None:
        return ["D-IDENT-02 (v1 request fields) is missing"]
    field_decision = decision.get("field_decision")
    if not isinstance(field_decision, Mapping):
        return ["D-IDENT-02 requires a field_decision object"]
    if field_decision.get("additional_properties") is not False:
        errors.append("D-IDENT-02: additional_properties must be false (extra=forbid)")
    if field_decision.get("http_status") != 422:
        errors.append("D-IDENT-02: unknown_field/unsupported_server_path must answer 422")
    if sorted(field_decision.get("rules") or []) != ["unknown_field", "unsupported_server_path"]:
        errors.append("D-IDENT-02: the frozen rule vocabulary changed")
    allowlist = field_decision.get("legacy_allowlist")
    if not isinstance(allowlist, Mapping):
        errors.append("D-IDENT-02: legacy_allowlist must be an object")
    elif allowlist.get("rows") != []:
        errors.append("D-IDENT-02: the v1 legacy allowlist must be empty")
    if live is None:
        return errors
    snapshot = live["authority_snapshot"]
    submit = set(snapshot["declared_fields"].get("submit") or ())
    server_paths = sorted(name for name in submit if name in set(snapshot["server_path_rejected"]))
    accepted = sorted(submit - set(server_paths))
    if sorted(field_decision.get("accepted_submit_fields") or []) != accepted:
        errors.append("D-IDENT-02: accepted_submit_fields differs from the live policy")
    if sorted(field_decision.get("server_path_fields") or []) != server_paths:
        errors.append("D-IDENT-02: server_path_fields differs from the live policy")
    if snapshot["legacy_allowlist"] != []:
        errors.append("the live legacy allowlist is no longer empty; regenerate the freeze")
    return errors


def _check_p2_statuses(decisions: Sequence[Mapping[str, Any]], repo_root: Path) -> list[str]:
    """Return the failures of the P2 implementation_status decision."""
    errors: list[str] = []
    decision = next(
        (
            item
            for item in decisions
            if isinstance(item, Mapping) and item.get("id") == "D-IDENT-03"
        ),
        None,
    )
    if decision is None:
        return ["D-IDENT-03 (P2 implementation_status) is missing"]
    current = decision.get("current_values")
    if not isinstance(current, Mapping):
        return ["D-IDENT-03 requires current_values"]
    rows = current.get("rows")
    if not isinstance(rows, Sequence):
        return ["D-IDENT-03: current_values.rows must be a list"]
    for row in rows:
        if not isinstance(row, Mapping):
            errors.append("D-IDENT-03: current_values row is not an object")
            continue
        if row.get("implementation_status") not in IMPLEMENTATION_STATUSES:
            errors.append(
                f"D-IDENT-03: row {row.get('id')} status {row.get('implementation_status')!r} "
                "is outside planned|delivered|verified"
            )
    contract_path = repo_root / P2_CONTRACT
    if not contract_path.is_file():
        return errors + [f"missing P2 contract: {P2_CONTRACT}"]
    contract = _load_json(contract_path)
    observed = {
        row.get("id"): row.get("implementation_status")
        for row in contract["rows"]
        if isinstance(row, Mapping)
    }
    for identifier, status in observed.items():
        if status not in IMPLEMENTATION_STATUSES:
            errors.append(
                f"{P2_CONTRACT}: row {identifier} status {status!r} is outside "
                "planned|delivered|verified"
            )
    recorded = {row.get("id"): row.get("implementation_status") for row in rows}
    if recorded != observed:
        errors.append(
            "D-IDENT-03: recorded row statuses differ from the P2 contract "
            f"({recorded} != {observed})"
        )
    return errors


def validate(freeze_path: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Return every failure of one freeze document (empty list = VALID)."""
    if not freeze_path.is_file():
        return [f"freeze document not found: {freeze_path}"]
    try:
        freeze = _load_json(freeze_path)
    except json.JSONDecodeError as exc:
        return [f"freeze document is not valid JSON: {exc}"]
    if not isinstance(freeze, Mapping):
        return ["freeze document must be a JSON object"]
    errors: list[str] = []
    for field in TOP_LEVEL_REQUIRED:
        if field not in freeze:
            errors.append(f"missing top-level field {field!r}")
    if freeze.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}, got {freeze.get('schema')!r}")
    frozen_at = freeze.get("frozen_at")
    if not isinstance(frozen_at, str) or DATE.match(frozen_at) is None:
        errors.append("frozen_at must be an ISO date")
    else:
        try:
            dt.date.fromisoformat(frozen_at)
        except ValueError:
            errors.append("frozen_at must be a real calendar date")
    for field in (
        "contract_version",
        "owner",
        "task",
        "pending_rule",
        "validator",
        "validator_tests",
    ):
        if not isinstance(freeze.get(field), str) or not str(freeze.get(field)).strip():
            errors.append(f"{field} must be a non-empty string")
    if not isinstance(freeze.get("closes"), Sequence) or not freeze.get("closes"):
        errors.append("closes must list the gap ids this freeze closes")
    if not isinstance(freeze.get("status_vocabulary"), Mapping):
        errors.append(
            "status_vocabulary must define frozen/pending_implementation/pending_conflict"
        )
    else:
        for status in STATUSES:
            if status not in freeze["status_vocabulary"]:
                errors.append(f"status_vocabulary is missing {status}")
    for prerequisite in freeze.get("prerequisites") or []:
        if not isinstance(prerequisite, Mapping):
            errors.append("prerequisites entries must be objects")
            continue
        if not isinstance(prerequisite.get("task"), str):
            errors.append("prerequisite without a task id")
        if (
            not isinstance(prerequisite.get("commit"), str)
            or COMMIT.match(str(prerequisite.get("commit"))) is None
        ):
            errors.append(f"prerequisite {prerequisite.get('task')} needs a commit id")
    decisions = freeze.get("decisions")
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)) or not decisions:
        return errors + ["decisions must be a non-empty list"]

    live = _live_inventory(repo_root)
    if live is None:  # pragma: no cover - environment failure
        errors.append(
            "cannot run the client scanner (scripts/acceptance/check_v1_request_field_consumers.py)"
        )
    else:
        errors.extend(_check_client_inventory(freeze, live, repo_root))
    errors.extend(_check_decisions(decisions, freeze, live, repo_root))
    errors.extend(_check_field_decision(decisions, live))
    errors.extend(_check_p2_statuses(decisions, repo_root))
    consumers = freeze.get("consumers_by_task")
    if not isinstance(consumers, Mapping) or not consumers:
        errors.append("consumers_by_task must map the M1 tasks to decision ids")
    else:
        known = {decision.get("id") for decision in decisions if isinstance(decision, Mapping)}
        for task, ids in consumers.items():
            if not isinstance(ids, Sequence) or isinstance(ids, (str, bytes)):
                errors.append(f"consumers_by_task[{task}] must be a list")
                continue
            for identifier in ids:
                if identifier not in known:
                    errors.append(f"consumers_by_task[{task}] names unknown decision {identifier}")
    return errors


def _summary(freeze_path: Path) -> str:
    freeze = _load_json(freeze_path)
    decisions = [item for item in freeze.get("decisions") or [] if isinstance(item, Mapping)]
    counts = {status: 0 for status in sorted(STATUSES)}
    for decision in decisions:
        status = decision.get("status")
        if status in counts:
            counts[status] += 1
    return (
        f"{len(decisions)} decisions: frozen={counts[STATUS_FROZEN]} "
        f"pending_implementation={counts[STATUS_PENDING_IMPLEMENTATION]} "
        f"pending_conflict={counts[STATUS_PENDING_CONFLICT]}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the freeze; see the module docstring for the exit codes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--freeze", type=Path, default=None, help="freeze document to validate")
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="repository root the freeze describes"
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable result")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    freeze_path = args.freeze or (repo_root / DEFAULT_FREEZE)
    if not freeze_path.is_absolute():
        freeze_path = (repo_root / freeze_path).resolve()
    try:
        errors = validate(freeze_path, repo_root)
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"validate_acp2_decision_freeze: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        payload: dict[str, Any] = {
            "valid": not errors,
            "errors": errors,
            "freeze": str(freeze_path),
        }
        if not errors:
            payload["summary"] = _summary(freeze_path)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif errors:
        print("INVALID " + str(freeze_path), file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
    else:
        print(f"VALID {freeze_path.name} ({_summary(freeze_path)})")
    return EXIT_OK if not errors else EXIT_INVALID


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
