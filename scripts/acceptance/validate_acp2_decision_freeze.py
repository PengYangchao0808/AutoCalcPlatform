#!/usr/bin/env python3.11
"""M0-11/M0-12: mechanical validator for the ACP 2.0 decision freezes.

Two decision records live under
``docs/reference/next-change/acp2-contracts/decision-freeze/``:

* ``identity.v1.json`` (M0-11, ``acp2.decision-freeze.identity.v1``) — identity,
  field policy and migration decisions consumed by M1-02/M1-04;
* ``integration.v1.json`` (M0-12, ``acp2.decision-freeze.integration.v1``) —
  extension negotiation, contract mutation, CI scope and registration decisions
  consumed by M1-01.

A freeze document is a decision record, not prose: every claim in it must be
checkable.  The validator therefore enforces, for both documents, the shared
structure (schema, contract version, owners, status vocabulary, per-decision
required fields, status honesty, vector resolution, impact existence) and the
document-specific checks:

identity
    client-inventory freshness against a live run of
    :mod:`scripts.acceptance.check_v1_request_field_consumers`, the recorded
    field policy against the live policy table, and the P2 status enum.

integration
    every ``wire_refs`` entry resolves against a live file — JSON Pointer
    (RFC 6901) fields must exist **and** equal/contain the frozen value, text
    files must contain/omit the frozen marker; the ADR-006 status line must say
    ``Accepted``; ``consumer_contract`` must point M1-01 at the integration file
    and M1-02/M1-04 at the identity file without pinning identity statistics;
    the negotiation/branch/CI/hygiene/registration blocks must agree with the
    decision ids they describe.

Exit codes
----------
``0``  VALID for every validated document.
``1``  INVALID (each failure is printed to stderr; ``--json`` lists all).
``2``  the validator could not run (missing file, unreadable checkout).

Usage
-----
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py --json
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py --file integration.v1.json
    python3.11 scripts/acceptance/validate_acp2_decision_freeze.py --file identity.v1.json

``--file`` is repeatable and a bare file name resolves inside the
decision-freeze directory; without it both freezes are validated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_FREEZE = "docs/reference/next-change/acp2-contracts/decision-freeze/identity.v1.json"
INTEGRATION_FREEZE = "docs/reference/next-change/acp2-contracts/decision-freeze/integration.v1.json"
SCHEMA = "acp2.decision-freeze.identity.v1"
INTEGRATION_SCHEMA = "acp2.decision-freeze.integration.v1"
P2_CONTRACT = "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json"
LINT_BASELINE = "docs/reference/next-change/acp2-contracts/runtime/lint-baseline.json"
MANIFEST = "docs/reference/next-change/acp2-contracts/manifest.json"
ADR_006 = "docs/reference/next-change/adr/ADR-006-mcp-negotiation.md"
CONTRACT_VALIDATOR = "scripts/acceptance/validate_acp2_contracts.py"

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_ERROR = 2

STATUS_FROZEN = "frozen"
STATUS_PENDING_IMPLEMENTATION = "pending_implementation"
STATUS_PENDING_CONFLICT = "pending_conflict"
STATUSES = frozenset({STATUS_FROZEN, STATUS_PENDING_IMPLEMENTATION, STATUS_PENDING_CONFLICT})
IMPLEMENTATION_STATUSES = frozenset({"planned", "delivered", "verified"})

DECISION_ID = re.compile(r"^D-IDENT-\d{2}$")
INTEGRATION_DECISION_ID = re.compile(r"^D-INT-\d{2}$")
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
INTEGRATION_TOP_LEVEL_REQUIRED = (
    "schema",
    "contract_version",
    "frozen_at",
    "owner",
    "task",
    "title",
    "closes",
    "status_vocabulary",
    "pending_rule",
    "approval_protocol",
    "consumer_contract",
    "decisions",
    "pending_summary",
    "observations",
    "validator",
    "validator_tests",
    "scope_notes",
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
INTEGRATION_DECISION_REQUIRED = DECISION_REQUIRED + ("wire_refs",)
PENDING_REQUIRED = ("pending_reason", "consumer_task", "frozen_forward_convention", "blocking")
WIRE_ASSERTIONS = ("equals", "contains", "absent", "exists")


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


# ---------------------------------------------------------------------------
# wire reference resolution (integration freeze)
# ---------------------------------------------------------------------------


def _resolve_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer.

    Array segments are either an integer index or a ``field=value`` selector that
    picks the single element whose ``field`` stringifies to ``value`` (so a
    decision does not break when an unrelated entry is inserted before it).
    """
    if not pointer.startswith("/"):
        raise KeyError(f"pointer must start with '/', got {pointer!r}")
    node = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            if token.isdigit():
                index = int(token)
                if index >= len(node):
                    raise KeyError(f"index {index} is out of range")
                node = node[index]
                continue
            field, separator, value = token.partition("=")
            if not separator:
                raise KeyError(f"array segment {token!r} is neither an index nor field=value")
            matches = [
                item for item in node if isinstance(item, Mapping) and str(item.get(field)) == value
            ]
            if len(matches) != 1:
                raise KeyError(f"selector {token!r} matched {len(matches)} elements")
            node = matches[0]
        elif isinstance(node, Mapping):
            if token not in node:
                raise KeyError(f"missing key {token!r}")
            node = node[token]
        else:
            raise KeyError(f"cannot descend into {type(node).__name__} with {token!r}")
    return node


def _haystack(target: Path, value: Any, pointer: str | None) -> str:
    if pointer is None:
        return target.read_text(encoding="utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _check_wire_ref(reference: Mapping[str, Any], repo_root: Path, decision_id: str) -> list[str]:
    """Return the failures for one ``wire_refs[]`` entry."""
    if not isinstance(reference, Mapping):
        return [f"{decision_id}: wire_refs entry is not an object: {reference!r}"]
    relative = reference.get("path")
    if not isinstance(relative, str) or not relative.strip():
        return [f"{decision_id}: wire_refs entry needs a non-empty path"]
    prefix = f"{decision_id}: wire_ref {relative}"
    target = (repo_root / relative).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError:
        return [f"{prefix} escapes the repository"]
    if not target.exists():
        return [f"{prefix} does not exist"]
    errors: list[str] = []
    if not str(reference.get("why") or "").strip():
        errors.append(f"{prefix} must say why it is referenced")
    present = [name for name in WIRE_ASSERTIONS if name in reference]
    if len(present) != 1:
        errors.append(f"{prefix} must carry exactly one of {list(WIRE_ASSERTIONS)}, got {present}")
        return errors
    kind = present[0]
    if kind == "exists":
        if reference["exists"] is not True:
            errors.append(f"{prefix} exists must be true")
        return errors
    if not target.is_file():
        return errors + [f"{prefix} is not a file"]
    pointer = reference.get("pointer")
    value: Any = None
    if pointer is not None:
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            return errors + [f"{prefix} pointer must be an RFC 6901 string"]
        try:
            document = _load_json(target)
        except json.JSONDecodeError as exc:
            return errors + [f"{prefix} is not valid JSON: {exc}"]
        try:
            value = _resolve_pointer(document, pointer)
        except KeyError as exc:
            return errors + [f"{prefix}{pointer} does not resolve: {exc}"]
    haystack = _haystack(target, value, pointer)
    if kind == "equals":
        if pointer is None:
            errors.append(f"{prefix} equals requires a pointer")
        elif value != reference["equals"]:
            errors.append(
                f"{prefix}{pointer} is {value!r}, the frozen value is {reference['equals']!r}"
            )
    elif kind == "contains":
        needle = reference["contains"]
        if not isinstance(needle, str) or not needle:
            errors.append(f"{prefix} contains must be a non-empty string")
        elif needle not in haystack:
            errors.append(f"{prefix}{pointer or ''} does not contain {needle!r}")
    elif kind == "absent":
        needle = reference["absent"]
        if not isinstance(needle, str) or not needle:
            errors.append(f"{prefix} absent must be a non-empty string")
        elif needle in haystack:
            errors.append(f"{prefix}{pointer or ''} still contains {needle!r}")
    return errors


# ---------------------------------------------------------------------------
# shared decision checks
# ---------------------------------------------------------------------------


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
    *,
    id_pattern: re.Pattern[str] = DECISION_ID,
    required: Sequence[str] = DECISION_REQUIRED,
    require_wire_refs: bool = False,
    live_clients: bool = True,
) -> list[str]:
    """Return the failures of the decision list (shared by both freezes)."""
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
        if not isinstance(identifier, str) or id_pattern.match(identifier) is None:
            errors.append(f"decision id is malformed: {identifier!r}")
            continue
        if identifier in seen:
            errors.append(f"{identifier}: duplicate decision id")
        seen.add(identifier)
        for field in required:
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
        if require_wire_refs:
            wire_refs = decision.get("wire_refs")
            if (
                not isinstance(wire_refs, Sequence)
                or isinstance(wire_refs, (str, bytes))
                or not wire_refs
            ):
                errors.append(f"{identifier}: wire_refs must list at least one resolved reference")
            else:
                for reference in wire_refs:
                    errors.extend(_check_wire_ref(reference, repo_root, identifier))
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
        elif live_clients:
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


# ---------------------------------------------------------------------------
# identity-only checks
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# integration-only checks
# ---------------------------------------------------------------------------


def _integration_decision(
    decisions: Sequence[Mapping[str, Any]], identifier: str
) -> tuple[Mapping[str, Any] | None, list[str]]:
    decision = next(
        (item for item in decisions if isinstance(item, Mapping) and item.get("id") == identifier),
        None,
    )
    if decision is None:
        return None, [f"{identifier} is missing from the integration freeze"]
    return decision, []


def _check_consumer_contract(freeze: Mapping[str, Any], repo_root: Path) -> list[str]:
    """M1-01 reads this file; M1-02/M1-04 read the identity file — without pinning counts."""
    errors: list[str] = []
    contract = freeze.get("consumer_contract")
    if not isinstance(contract, Mapping) or not contract:
        return ["consumer_contract must map the M1 tasks to their decision source"]
    decisions = [item for item in freeze.get("decisions") or [] if isinstance(item, Mapping)]
    by_id = {item.get("id"): item for item in decisions}
    identity_path = repo_root / DEFAULT_FREEZE
    if not identity_path.is_file():
        return [f"missing identity freeze: {DEFAULT_FREEZE}"]
    identity_doc = _load_json(identity_path)
    identity_by_id = {
        item.get("id"): item
        for item in identity_doc.get("decisions") or []
        if isinstance(item, Mapping)
    }
    for task, spec in contract.items():
        if not isinstance(task, str) or not re.match(r"^M1-\d\d", task):
            errors.append(f"consumer_contract[{task!r}] is not an M1 task id")
            continue
        if not isinstance(spec, Mapping):
            errors.append(f"consumer_contract[{task}] must be an object")
            continue
        expected_source = INTEGRATION_FREEZE if task == "M1-01" else DEFAULT_FREEZE
        if spec.get("source") != expected_source:
            errors.append(
                f"consumer_contract[{task}].source must be {expected_source!r}, "
                f"got {spec.get('source')!r}"
            )
        identifiers = spec.get("decision_ids")
        fields = spec.get("machine_readable_fields")
        if (
            not isinstance(identifiers, Sequence)
            or isinstance(identifiers, (str, bytes))
            or not identifiers
        ):
            errors.append(f"consumer_contract[{task}].decision_ids must be a non-empty list")
            continue
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)) or not fields:
            errors.append(f"consumer_contract[{task}].machine_readable_fields must be non-empty")
        pool = by_id if task == "M1-01" else identity_by_id
        for identifier in identifiers:
            decision = pool.get(identifier)
            if decision is None:
                errors.append(
                    f"consumer_contract[{task}] names {identifier}, which is not in "
                    f"{expected_source}"
                )
                continue
            if task == "M1-01" and decision.get("status") != STATUS_FROZEN:
                if decision.get("frozen_forward_convention") is not True:
                    errors.append(
                        f"consumer_contract[M1-01] names pending {identifier} without a "
                        "frozen forward convention (no open choice may enter M1-01)"
                    )
            for field in fields or ():
                if field not in decision:
                    errors.append(
                        f"consumer_contract[{task}] declares machine field {field!r}, "
                        f"which {identifier} does not carry"
                    )
    if "M1-01" not in contract:
        errors.append("consumer_contract must declare M1-01 explicitly")
    return errors


def _check_adr_status(repo_root: Path) -> list[str]:
    """ADR-006 must be Accepted, keep §D7, and reference this freeze."""
    path = repo_root / ADR_006
    if not path.is_file():
        return [f"missing ADR: {ADR_006}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    match = re.search(r"^\s*-\s*\*\*状态\*\*\s*[：:]\s*(.+)$", text, re.MULTILINE)
    if match is None:
        return [f"{ADR_006} has no '- **状态**' status line"]
    status_line = match.group(1).strip()
    if "Accepted" not in status_line:
        errors.append(f"{ADR_006} status line must say Accepted, got {status_line[:80]!r}")
    for terminal in ("Superseded", "Withdrawn", "Rejected", "Proposed"):
        if terminal in status_line:
            errors.append(f"{ADR_006} status line may not be {terminal}")
    for marker, label in (
        ("§D7", "the D7 decision section"),
        ("tools/call", "the tools/call carrier"),
        (INTEGRATION_FREEZE, "the M0-12 integration freeze"),
    ):
        if marker not in text:
            errors.append(f"{ADR_006} must reference {label}")
    return errors


def _check_negotiation_freeze(decisions: Sequence[Mapping[str, Any]]) -> list[str]:
    decision, errors = _integration_decision(decisions, "D-INT-02")
    if decision is None:
        return errors
    block = decision.get("negotiation_freeze")
    if not isinstance(block, Mapping):
        return errors + ["D-INT-02 requires a negotiation_freeze object"]
    if block.get("carrier") != "tools/call:acp_capabilities":
        errors.append("D-INT-02: the frozen carrier must be tools/call:acp_capabilities")
    if block.get("request_field") != "research_extension":
        errors.append("D-INT-02: the frozen request field must be research_extension")
    if block.get("initialize_claim") != "none":
        errors.append("D-INT-02: initialize_claim must be none")
    if block.get("initialize_may_only_point_at_the_carrier") is not True:
        errors.append("D-INT-02: initialize may only point at the carrier")
    if block.get("negotiated_requires") != ["implemented"]:
        errors.append("D-INT-02: negotiated=true must require implemented=true and nothing else")
    if "extensions[].negotiated" not in str(block.get("negotiated_surface") or ""):
        errors.append(
            "D-INT-02: negotiated_surface must be the acp_capabilities extensions[] field"
        )
    forbidden = block.get("forbidden")
    if not isinstance(forbidden, Sequence) or isinstance(forbidden, (str, bytes)) or not forbidden:
        errors.append("D-INT-02: forbidden must list at least one forbidden shortcut")
    return errors


def _check_branch_decision(decisions: Sequence[Mapping[str, Any]]) -> list[str]:
    decision, errors = _integration_decision(decisions, "D-INT-03")
    if decision is None:
        return errors
    block = decision.get("branch_decision")
    if not isinstance(block, Mapping):
        return errors + ["D-INT-03 requires a branch_decision object"]
    if block.get("selected") != "external_change_not_required":
        errors.append("D-INT-03: the selected branch is external_change_not_required")
    if block.get("m0_09_outcome") != "path_selected":
        errors.append("D-INT-03: the M0-09 outcome must be path_selected")
    if block.get("m0_13_status") != "NOT_APPLICABLE":
        errors.append("D-INT-03: M0-13 must be recorded NOT_APPLICABLE")
    if block.get("dsh_source_modified") is not False:
        errors.append("D-INT-03: the external DSH source may not be modified on this branch")
    if block.get("bom_entry_added") is not False:
        errors.append("D-INT-03: no BOM entry may be added on this branch")
    conditions = block.get("reopen_conditions")
    if (
        not isinstance(conditions, Sequence)
        or isinstance(conditions, (str, bytes))
        or len(conditions) < 2
    ):
        errors.append("D-INT-03: reopen_conditions must record both reopen branches")
    else:
        for condition in conditions:
            if not isinstance(condition, Mapping):
                errors.append("D-INT-03: reopen condition is not an object")
                continue
            for field in ("id", "trigger", "required"):
                if not str(condition.get(field) or "").strip():
                    errors.append(
                        f"D-INT-03: reopen condition {condition.get('id')!r} needs {field}"
                    )
    return errors


def _check_ci_scope(decisions: Sequence[Mapping[str, Any]]) -> list[str]:
    decision, errors = _integration_decision(decisions, "D-INT-05")
    if decision is None:
        return errors
    block = decision.get("ci_scope")
    if not isinstance(block, Mapping):
        return errors + ["D-INT-05 requires a ci_scope object"]
    if block.get("contract_marker_owner") != "mcp-conformance":
        errors.append("D-INT-05: mcp-conformance must own the contract marker")
    if block.get("contract_marker_owner_count") != 1:
        errors.append("D-INT-05: exactly one lane may own the contract marker")
    excluded = block.get("matrix_excludes_markers")
    if not isinstance(excluded, Sequence) or "contract" not in excluded:
        errors.append("D-INT-05: the ordinary matrix must exclude the contract marker")
    dsh = block.get("dsh_source")
    if not isinstance(dsh, Mapping):
        errors.append("D-INT-05: dsh_source must be an object")
    else:
        if dsh.get("env_var") != "ACP2_DSH_SOURCE":
            errors.append("D-INT-05: the DSH tree is declared through ACP2_DSH_SOURCE")
        if dsh.get("vendored") is not False:
            errors.append("D-INT-05: the frozen DSH tree is never vendored")
        if dsh.get("undeclared_result") != "NOT_VERIFIED":
            errors.append("D-INT-05: an undeclared tree must report NOT_VERIFIED")
    frontend = block.get("frontend_gate")
    if not isinstance(frontend, Mapping) or frontend.get("non_empty_guard") is not True:
        errors.append("D-INT-05: the frontend module job must keep its non-empty guard")
    rph = block.get("rph_gate")
    if not isinstance(rph, Mapping) or rph.get("packages_before_upload") is not True:
        errors.append("D-INT-05: the RPH job must package before uploading")
    wall = block.get("wall_clock")
    if not isinstance(wall, Mapping) or wall.get("recorded") is not True:
        errors.append("D-INT-05: every working job must record wall-clock")
    elif wall.get("max_minutes_before_split") != 20:
        errors.append("D-INT-05: a leg over 20 minutes must be split, never skipped")
    elif not isinstance(wall.get("jobs_with_wall_clock"), Sequence) or isinstance(
        wall.get("jobs_with_wall_clock"), (str, bytes)
    ):
        errors.append("D-INT-05: wall_clock.jobs_with_wall_clock must name the timed jobs")
    elif not isinstance(wall.get("jobs_without"), Sequence) or isinstance(
        wall.get("jobs_without"), (str, bytes)
    ):
        errors.append("D-INT-05: wall_clock.jobs_without must name every untimed job")
    failures = block.get("conformance_failure_modes")
    if not isinstance(failures, Sequence):
        errors.append("D-INT-05: conformance_failure_modes must be a list")
    else:
        for code in (
            "EMPTY_CONTRACT_SELECTION",
            "PARTIAL_CONTRACT_SKIP",
            "DSH_DECLARED_BUT_UNRESOLVED",
        ):
            if code not in failures:
                errors.append(f"D-INT-05: conformance_failure_modes must include {code}")
    if block.get("lint_gates") != "commented_out_until_hygiene_stream":
        errors.append("D-INT-05: the lint gates stay out of the required chain")
    return errors


def _check_hygiene(decisions: Sequence[Mapping[str, Any]], repo_root: Path) -> list[str]:
    errors: list[str] = []
    decision, decision_errors = _integration_decision(decisions, "D-INT-06")
    errors.extend(decision_errors)
    baseline_path = repo_root / LINT_BASELINE
    baseline_version = None
    if baseline_path.is_file():
        baseline_version = _load_json(baseline_path).get("ruff_version")
    if decision is not None:
        block = decision.get("hygiene_decision")
        if not isinstance(block, Mapping):
            errors.append("D-INT-06 requires a hygiene_decision object")
        else:
            if block.get("tool") != "ruff":
                errors.append("D-INT-06: the hygiene tool is ruff")
            if baseline_version and block.get("pinned_version") != baseline_version:
                errors.append(
                    "D-INT-06: pinned_version differs from the lint baseline "
                    f"({block.get('pinned_version')!r} != {baseline_version!r})"
                )
            if block.get("excluded_from_required_chain") is not True:
                errors.append("D-INT-06: the ratchet must be excluded from the required chain")
            if block.get("required_chain") != "G01-G26":
                errors.append("D-INT-06: the required chain under consideration is G01-G26")
            if block.get("never_a_closing_condition") is not True:
                errors.append("D-INT-06: ruff may never be a closing condition for a gap")
            if block.get("baseline_may_only_shrink") is not True:
                errors.append("D-INT-06: the baseline may only shrink")

    pending, pending_errors = _integration_decision(decisions, "D-INT-07")
    errors.extend(pending_errors)
    if pending is not None:
        if pending.get("status") != STATUS_PENDING_IMPLEMENTATION:
            errors.append(
                "D-INT-07 must stay pending_implementation while its artifacts are missing"
            )
        block = pending.get("hygiene_implementation")
        if not isinstance(block, Mapping):
            errors.append("D-INT-07 requires a hygiene_implementation object")
        else:
            missing = block.get("missing_artifacts")
            if (
                not isinstance(missing, Sequence)
                or isinstance(missing, (str, bytes))
                or not missing
            ):
                errors.append("D-INT-07: missing_artifacts must list the absent evidence")
            else:
                for relative in missing:
                    if (repo_root / str(relative)).exists():
                        errors.append(
                            f"D-INT-07 is stale: {relative} now exists — regenerate the entry "
                            "and flip the decision instead of claiming it is missing"
                        )
            if block.get("enters_required_chain_after_landing") is not False:
                errors.append("D-INT-07: landing the ratchet must not add it to the required chain")
        pins = (pending.get("hygiene_implementation") or {}).get("observed_pins") or {}
        if isinstance(pins, Mapping) and baseline_version:
            if pins.get("lint_baseline") != baseline_version:
                errors.append(
                    "D-INT-07: observed_pins.lint_baseline differs from the lint baseline"
                )
    return errors


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_registration(decisions: Sequence[Mapping[str, Any]], repo_root: Path) -> list[str]:
    """D-INT-08 must describe an executable registration and match the live manifest."""
    decision, errors = _integration_decision(decisions, "D-INT-08")
    if decision is None:
        return errors
    block = decision.get("registration")
    if not isinstance(block, Mapping):
        return errors + ["D-INT-08 requires a registration object"]
    if block.get("target") != MANIFEST:
        errors.append(f"D-INT-08: registration.target must be {MANIFEST}")
    if not str(block.get("executor") or "").strip():
        errors.append("D-INT-08: registration.executor must name who writes the manifest")
    executed = block.get("executed")
    if not isinstance(executed, bool):
        errors.append("D-INT-08: registration.executed must be a boolean")
        executed = False
    entries = block.get("entries")
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)) or len(entries) < 2:
        return errors + ["D-INT-08: registration.entries must register both freezes"]
    ids: set[str] = set()
    paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            errors.append("D-INT-08: registration entry is not an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append("D-INT-08: registration entry needs an id")
            continue
        if identifier in ids:
            errors.append(f"D-INT-08: duplicate registration id {identifier}")
        ids.add(identifier)
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative:
            errors.append(f"D-INT-08: {identifier} needs a path")
            continue
        if relative in paths:
            errors.append(f"D-INT-08: duplicate registration path {relative}")
        paths.add(relative)
        if not (repo_root / relative).is_file():
            errors.append(f"D-INT-08: {identifier} path does not exist: {relative}")
        if entry.get("status") != "delivered":
            errors.append(f"D-INT-08: {identifier} must register as a delivered contract")
        if entry.get("owner") != "INT":
            errors.append(f"D-INT-08: {identifier} must register with owner INT")
        if entry.get("delivered_by") != "M0-12":
            errors.append(f"D-INT-08: {identifier} must register with delivered_by M0-12")
        if not str(entry.get("digest_rule") or "").strip():
            errors.append(f"D-INT-08: {identifier} must record its digest_rule")
        elif "sha256" not in str(entry["digest_rule"]):
            errors.append(f"D-INT-08: {identifier} digest_rule must say sha256")
        if "sha256" in entry:
            errors.append(
                f"D-INT-08: {identifier} must not embed its own digest (self-reference hazard)"
            )
    if len(paths) != 2 or DEFAULT_FREEZE not in paths or INTEGRATION_FREEZE not in paths:
        errors.append(
            "D-INT-08: registration must name exactly the identity and integration freezes"
        )
    for freeze_path in (DEFAULT_FREEZE, INTEGRATION_FREEZE):
        if not (repo_root / freeze_path).is_file():
            errors.append(f"D-INT-08: freeze file is missing: {freeze_path}")
    deltas = block.get("validator_deltas")
    if not isinstance(deltas, Sequence) or isinstance(deltas, (str, bytes)) or not deltas:
        errors.append("D-INT-08: validator_deltas must be a non-empty list")
    else:
        joined = "\n".join(str(item) for item in deltas)
        for marker in ("DELIVERED_IDS_BY_STAGE", "owner enum", CONTRACT_VALIDATOR):
            if marker not in joined:
                errors.append(f"D-INT-08: validator_deltas must name {marker}")
    changes = block.get("manifest_changes")
    if not isinstance(changes, Sequence) or "contract_digest" not in "\n".join(map(str, changes)):
        errors.append("D-INT-08: manifest_changes must include the contract_digest recompute")
    must_not = block.get("must_not")
    if not isinstance(must_not, Sequence) or isinstance(must_not, (str, bytes)) or not must_not:
        errors.append("D-INT-08: must_not must record the forbidden shortcuts")

    manifest_path = repo_root / MANIFEST
    if not manifest_path.is_file():
        return errors + [f"missing contract manifest: {MANIFEST}"]
    manifest = _load_json(manifest_path)
    registered = {
        item.get("id"): item
        for item in manifest.get("contracts") or []
        if isinstance(item, Mapping)
    }
    present = {identifier for identifier in ids if identifier in registered}
    if executed and present != ids:
        errors.append(
            "D-INT-08: registration.executed is true but the manifest is missing "
            f"{sorted(ids - present)}"
        )
    if not executed and present:
        errors.append(
            "D-INT-08: registration.executed is false but the manifest already contains "
            f"{sorted(present)} — flip executed with the registration"
        )
    if executed:
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            identifier = entry.get("id")
            manifest_entry = registered.get(identifier)
            if manifest_entry is None:
                continue
            for field in ("path", "status", "owner", "delivered_by"):
                if manifest_entry.get(field) != entry.get(field):
                    errors.append(
                        f"D-INT-08: manifest {identifier}.{field} is "
                        f"{manifest_entry.get(field)!r}, the freeze says {entry.get(field)!r}"
                    )
            relative = entry.get("path")
            if isinstance(relative, str) and (repo_root / relative).is_file():
                actual = _sha256_file(repo_root / relative)
                if manifest_entry.get("sha256") != actual:
                    errors.append(
                        f"D-INT-08: manifest {identifier}.sha256 is stale "
                        f"({manifest_entry.get('sha256')!r} != {actual!r})"
                    )
    return errors


# ---------------------------------------------------------------------------
# document validation
# ---------------------------------------------------------------------------


def _check_common_top_level(freeze: Mapping[str, Any], required: Sequence[str]) -> list[str]:
    errors: list[str] = []
    for field in required:
        if field not in freeze:
            errors.append(f"missing top-level field {field!r}")
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
    return errors


def _validate_identity(freeze: Mapping[str, Any], repo_root: Path) -> list[str]:
    errors = _check_common_top_level(freeze, TOP_LEVEL_REQUIRED)
    if freeze.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA!r}, got {freeze.get('schema')!r}")
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


def _validate_integration(freeze: Mapping[str, Any], repo_root: Path) -> list[str]:
    errors = _check_common_top_level(freeze, INTEGRATION_TOP_LEVEL_REQUIRED)
    if freeze.get("schema") != INTEGRATION_SCHEMA:
        errors.append(f"schema must be {INTEGRATION_SCHEMA!r}, got {freeze.get('schema')!r}")
    if not isinstance(freeze.get("title"), str) or not freeze["title"].strip():
        errors.append("title must be a non-empty string")
    if not isinstance(freeze.get("approval_protocol"), Mapping):
        errors.append("approval_protocol must record aggregation and pending signoffs")
    if (
        not isinstance(freeze.get("scope_notes"), Sequence)
        or isinstance(freeze.get("scope_notes"), (str, bytes))
        or not freeze["scope_notes"]
    ):
        errors.append("scope_notes must list what this freeze does not decide")
    decisions = freeze.get("decisions")
    if not isinstance(decisions, Sequence) or isinstance(decisions, (str, bytes)) or not decisions:
        return errors + ["decisions must be a non-empty list"]

    errors.extend(
        _check_decisions(
            decisions,
            freeze,
            None,
            repo_root,
            id_pattern=INTEGRATION_DECISION_ID,
            required=INTEGRATION_DECISION_REQUIRED,
            require_wire_refs=True,
            live_clients=False,
        )
    )
    errors.extend(_check_consumer_contract(freeze, repo_root))
    errors.extend(_check_adr_status(repo_root))
    errors.extend(_check_negotiation_freeze(decisions))
    errors.extend(_check_branch_decision(decisions))
    errors.extend(_check_ci_scope(decisions))
    errors.extend(_check_hygiene(decisions, repo_root))
    errors.extend(_check_registration(decisions, repo_root))
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
    if freeze.get("schema") == INTEGRATION_SCHEMA:
        return _validate_integration(freeze, repo_root)
    return _validate_identity(freeze, repo_root)


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
    """Validate the freezes; see the module docstring for the exit codes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--file",
        type=Path,
        action="append",
        default=None,
        help="freeze document to validate (repeatable); default: identity + integration",
    )
    parser.add_argument(
        "--freeze",
        type=Path,
        default=None,
        help="alias for --file (kept for the M0-11 command line)",
    )
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="repository root the freeze describes"
    )
    parser.add_argument("--json", action="store_true", help="print a machine-readable result")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    requested = list(args.file or [])
    if args.freeze is not None:
        requested.append(args.freeze)
    if not requested:
        requested = [
            repo_root / DEFAULT_FREEZE,
            repo_root / INTEGRATION_FREEZE,
        ]
    resolved: list[Path] = []
    freeze_dir = (repo_root / DEFAULT_FREEZE).parent
    for item in requested:
        if item.is_absolute():
            path = item
        else:
            path = repo_root / item
            # `--file integration.v1.json` means the decision-freeze directory.
            if not path.exists() and len(item.parts) == 1:
                path = freeze_dir / item
        resolved.append(path.resolve())

    documents: list[dict[str, Any]] = []
    try:
        for freeze_path in resolved:
            errors = validate(freeze_path, repo_root)
            documents.append(
                {
                    "freeze": str(freeze_path),
                    "valid": not errors,
                    "errors": errors,
                    "summary": _summary(freeze_path) if freeze_path.is_file() else None,
                }
            )
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"validate_acp2_decision_freeze: {exc}", file=sys.stderr)
        return EXIT_ERROR

    valid = all(document["valid"] for document in documents)
    if args.json:
        print(
            json.dumps(
                {"valid": valid, "documents": documents},
                indent=2,
                ensure_ascii=False,
            )
        )
    elif not valid:
        for document in documents:
            if document["valid"]:
                print(f"VALID {Path(document['freeze']).name} ({document['summary']})")
                continue
            print("INVALID " + document["freeze"], file=sys.stderr)
            for error in document["errors"]:
                print(f"  - {error}", file=sys.stderr)
    else:
        for document in documents:
            print(f"VALID {Path(document['freeze']).name} ({document['summary']})")
    return EXIT_OK if valid else EXIT_INVALID


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
