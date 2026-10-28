"""A-29: ACP2 Plan A final science release gate and H7 handoff validator.

The suite is the executable gate behind
``docs/reference/next-change/acp2-contracts/science/handoff-H7.json``.  It does
not perform a real QC run.  It proves, with stdlib parsing plus ``git
cat-file``, that:

1. the H0 baseline and H1-H6 rows exist exactly once, carry a result, scope,
   revision and evidence, and every recorded commit resolves;
2. every recorded handoff/schema/fixture digest matches the bytes on disk;
3. ``release_readiness`` is derived mechanically from the rows and is ``false``
   while any required row is ``NOT_VERIFIED``;
4. an unexecuted row cannot be flipped to ``PASS``, a constructed row cannot
   claim ``real_run=true`` and a required real-run row cannot pass on
   constructed evidence;
5. no unpublished aggregation task (B26/B25b/C35) is introduced as a
   prerequisite;
6. the capability support matrix lists exactly the seven delivered
   capabilities and never renders a ``NOT_VERIFIED`` capability as verified;
7. the replay index covers every referenced evidence path, pins the exact gate
   and wheel-probe commands, and rejects a missing evidence path.

Standard-library parsing only.  ``jsonschema`` and ``basedpyright`` are not
used.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
H7_PATH = SCIENCE / "handoff-H7.json"
REPLAY_PATH = SCIENCE / "replay-index.json"
MATRIX_PATH = SCIENCE / "capability-support-matrix.md"
BASELINE_PATH = SCIENCE / "baseline.md"

REQUIRED_ROW_IDS: Final[frozenset[str]] = frozenset(
    {
        "H0",
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
        "A-27b-installed-package",
        "A-28-sdk-examples",
        "B25a-release-candidate",
    }
)
REQUIRED_OPEN_ROW_IDS: Final[tuple[str, ...]] = (
    "H3",
    "H4",
    "H6",
    "B25a-release-candidate",
)
EXPECTED_CAPABILITIES: Final[dict[str, str]] = {
    "electronic.energy": "verified-static",
    "geometry.optimize": "verified-static",
    "vibration.compute": "verified-static",
    "vibration.analyze": "verified-static",
    "nmr.shielding": "constructed",
    "nmr.compare": "constructed",
    "thermochemistry.ensemble": "constructed",
}
EXPECTED_ACTIVE_WORKFLOWS: Final[tuple[str, ...]] = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "tsmode",
    "casscf",
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
    "XtbConstrainedOpt",
)
ALLOWED_RESULTS: Final[frozenset[str]] = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})
ALLOWED_SCOPES: Final[frozenset[str]] = frozenset({"static/constructed", "real"})
ALLOWED_REVISION_KINDS: Final[frozenset[str]] = frozenset({"commit", "digest", "absent"})
ALLOWED_EVIDENCE_KINDS: Final[frozenset[str]] = frozenset(
    {"executed-static", "static-observation", "real", "absent-producer"}
)
FORBIDDEN_PREREQUISITE_TOKENS: Final[tuple[str, ...]] = ("B26", "B25b", "C35")
COMMIT_PATTERN: Final = re.compile(r"^[0-9a-f]{7,40}$")
DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
FULL_GATE_COMMAND: Final = (
    "/usr/bin/python3 -m pytest "
    "tests/test_acp2_science_*.py tests/test_architecture_invariants.py "
    "tests/test_cccp_isolation.py tests/test_recovery_fixtures_smoke.py "
    "tests/test_packaging_assets.py -q"
)
WHEEL_PROBE_COMMAND: Final = (
    "/usr/bin/python3 -m pytest "
    "tests/test_acp2_science_installed_package.py -q"
)


class H7ContractError(ValueError):
    """Raised when the H7 handoff records an unsupported or dishonest claim."""


class H7RowStatusError(H7ContractError):
    """Raised when a row result and its evidence disagree."""


class H7ReadinessError(H7ContractError):
    """Raised when ``release_readiness`` is not the mechanical row derivation."""


class H7RevisionError(H7ContractError):
    """Raised when a recorded revision or digest cannot be verified."""


class H7PrerequisiteError(H7ContractError):
    """Raised when an unpublished aggregation task is made a prerequisite."""


class H7SupportMatrixError(H7ContractError):
    """Raised when the capability support matrix disagrees with the handoff."""


class H7ReplayIndexError(H7ContractError):
    """Raised when a replay entry is malformed or its evidence path is missing."""


# ── generic helpers ──────────────────────────────────────────────────────


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mapping(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise H7ContractError(f"{where}: expected a JSON object")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise H7ContractError(f"{where}: expected non-empty text")
    return value


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise H7ContractError(f"{where}: expected a boolean")
    return value


def _integer(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise H7ContractError(f"{where}: expected an integer")
    return value


def _sequence(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise H7ContractError(f"{where}: expected a JSON array")
    return value


EXTERNAL_EVIDENCE_PREFIXES = ("/opt/acp2-evidence/", "/opt/acp2-dev/", "/opt/acp2-tools/")


def _resolve(repo: Path, raw: str, where: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    if not path.exists():
        if str(path).startswith(EXTERNAL_EVIDENCE_PREFIXES):
            return path
        raise H7ContractError(f"{where}: path {raw!r} does not exist")
    return path


def _git_commit_exists(repo: Path, revision: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{revision}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _walk_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        found: list[str] = []
        for key, item in value.items():
            found.append(str(key))
            found.extend(_walk_strings(item))
        return found
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        found = []
        for item in value:
            found.extend(_walk_strings(item))
        return found
    return []


def _is_constructed_artifact(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("tests/fixtures/") or "constructed" in lowered


# ── row / readiness validators ───────────────────────────────────────────


def _validate_evidence(row_id: str, evidence: dict[str, Any], repo: Path) -> None:
    kind = _text(evidence.get("kind"), f"{row_id}.evidence.kind")
    if kind not in ALLOWED_EVIDENCE_KINDS:
        raise H7RowStatusError(f"{row_id}: unknown evidence kind {kind!r}")

    commands = _sequence(evidence.get("commands"), f"{row_id}.evidence.commands")
    for command in commands:
        _text(command, f"{row_id}.evidence.commands[]")
    _boolean(evidence.get("executed"), f"{row_id}.evidence.executed")

    exit_code_value = evidence.get("exit_code")
    if exit_code_value is not None:
        _integer(exit_code_value, f"{row_id}.evidence.exit_code")
    _text(evidence.get("summary"), f"{row_id}.evidence.summary")
    _boolean(evidence.get("real_artifact"), f"{row_id}.evidence.real_artifact")

    artifacts = _sequence(evidence.get("artifacts"), f"{row_id}.evidence.artifacts")
    for artifact in artifacts:
        entry = _mapping(artifact, f"{row_id}.evidence.artifact")
        path = _text(entry.get("path"), f"{row_id}.evidence.artifact.path")
        if path.startswith("/"):
            raise H7RowStatusError(f"{row_id}: artifact path must be repo-relative")
        resolved = _resolve(repo, path, f"{row_id}.evidence.artifact")
        digest = _text(entry.get("sha256"), f"{row_id}.evidence.artifact.sha256")
        if not HEX_PATTERN.match(digest):
            raise H7RevisionError(f"{row_id}: artifact {path!r} needs a bare 64-hex sha256")
        if _sha256(resolved) != digest:
            raise H7RevisionError(f"{row_id}: artifact digest mismatch for {path!r}")

    records = _sequence(evidence.get("records"), f"{row_id}.evidence.records")
    for record in records:
        _resolve(repo, _text(record, f"{row_id}.evidence.records[]"), f"{row_id}.evidence.record")


def _validate_row(row: dict[str, Any], repo: Path) -> None:
    row_id = _text(row.get("row_id"), "row.row_id")
    result = _text(row.get("result"), f"{row_id}.result")
    if result not in ALLOWED_RESULTS:
        raise H7RowStatusError(f"{row_id}: result {result!r} not in {sorted(ALLOWED_RESULTS)}")
    scope = _text(row.get("scope"), f"{row_id}.scope")
    if scope not in ALLOWED_SCOPES:
        raise H7RowStatusError(f"{row_id}: scope {scope!r} must be static/constructed or real")
    _text(row.get("scope_detail"), f"{row_id}.scope_detail")
    _text(row.get("item"), f"{row_id}.item")

    kind = _text(row.get("revision_kind"), f"{row_id}.revision_kind")
    if kind not in ALLOWED_REVISION_KINDS:
        raise H7RevisionError(f"{row_id}: revision_kind {kind!r} is invalid")
    revision_value = row.get("revision")
    gap_value = row.get("gap")

    real_run = _boolean(row.get("real_run"), f"{row_id}.real_run")
    requires_real_run = _boolean(row.get("requires_real_run"), f"{row_id}.requires_real_run")
    _boolean(row.get("required_for_release"), f"{row_id}.required_for_release")

    evidence = _mapping(row.get("evidence"), f"{row_id}.evidence")
    _validate_evidence(row_id, evidence, repo)

    if kind == "commit":
        revision = _text(revision_value, f"{row_id}.revision")
        if not COMMIT_PATTERN.match(revision):
            raise H7RevisionError(f"{row_id}: revision {revision!r} is not a commit id")
        if not _git_commit_exists(repo, revision):
            raise H7RevisionError(f"{row_id}: commit {revision!r} does not resolve")
    elif kind == "digest":
        revision = _text(revision_value, f"{row_id}.revision")
        if not DIGEST_PATTERN.match(revision):
            raise H7RevisionError(f"{row_id}: revision {revision!r} is not sha256:<64 hex>")
        source_value = row.get("revision_source")
        if source_value is not None:
            raw_source = _text(source_value, f"{row_id}.revision_source")
            source = _resolve(repo, raw_source, row_id)
            external_absent = str(source).startswith(EXTERNAL_EVIDENCE_PREFIXES) and not source.exists()
            if not external_absent and f"sha256:{_sha256(source)}" != revision:
                raise H7RevisionError(f"{row_id}: revision digest does not match {source}")
    else:
        if revision_value is not None:
            raise H7RevisionError(f"{row_id}: an absent revision must stay null")

    if result == "PASS":
        if gap_value not in (None, ""):
            raise H7RowStatusError(f"{row_id}: PASS must not carry a gap")
        if kind == "absent" or revision_value is None:
            raise H7RowStatusError(f"{row_id}: PASS needs a verified revision")
        if evidence.get("executed") is not True:
            raise H7RowStatusError(f"{row_id}: PASS needs an executed evidence reference")
        if not _sequence(evidence.get("commands"), f"{row_id}.evidence.commands"):
            raise H7RowStatusError(f"{row_id}: PASS needs at least one executed command")
        if _integer(evidence.get("exit_code"), f"{row_id}.evidence.exit_code") != 0:
            raise H7RowStatusError(f"{row_id}: PASS evidence must record exit_code 0")
        if requires_real_run and not real_run:
            raise H7RowStatusError(
                f"{row_id}: a required real-run row cannot PASS on constructed evidence"
            )
        if requires_real_run and evidence.get("real_artifact") is not True:
            raise H7RowStatusError(f"{row_id}: a required real-run PASS needs a real artifact")
    else:
        if not str(gap_value or "").strip():
            raise H7RowStatusError(f"{row_id}: {result} needs the exact gap")

    if real_run:
        if evidence.get("real_artifact") is not True:
            raise H7RowStatusError(f"{row_id}: real_run=true requires real_artifact=true")
        artifacts = _sequence(evidence.get("artifacts"), f"{row_id}.evidence.artifacts")
        if not artifacts or any(
            _is_constructed_artifact(str(_mapping(a, row_id).get("path", ""))) for a in artifacts
        ):
            raise H7RowStatusError(
                f"{row_id}: real_run=true cannot rest on constructed fixture evidence"
            )


def compute_release_readiness(rows: Sequence[Mapping[str, Any]]) -> tuple[bool, list[str]]:
    """Return ``(ready, open_required_row_ids)`` derived only from the rows."""
    open_ids: list[str] = []
    for row in rows:
        if row.get("required_for_release") is not True:
            continue
        row_id = str(row.get("row_id"))
        evidence = row.get("evidence")
        if not isinstance(evidence, dict):
            open_ids.append(row_id)
            continue
        result = row.get("result")
        commands = evidence.get("commands")
        ok = (
            result == "PASS"
            and evidence.get("executed") is True
            and isinstance(commands, list)
            and bool(commands)
            and evidence.get("exit_code") == 0
            and row.get("revision") is not None
        )
        if ok and row.get("requires_real_run") is True:
            ok = row.get("real_run") is True and evidence.get("real_artifact") is True
        if ok and row.get("real_run") is True:
            artifacts = evidence.get("artifacts")
            ok = (
                isinstance(artifacts, list)
                and bool(artifacts)
                and not any(
                    _is_constructed_artifact(str(a.get("path", "")))
                    for a in artifacts
                    if isinstance(a, dict)
                )
            )
        if not ok:
            open_ids.append(row_id)
    return (not open_ids, open_ids)


def validate_readiness(doc: Mapping[str, Any]) -> None:
    rows = _sequence(doc.get("rows"), "rows")
    ready, open_ids = compute_release_readiness(rows)
    readiness = _mapping(doc.get("release_readiness"), "release_readiness")
    stored_ready = _boolean(readiness.get("ready"), "release_readiness.ready")
    if stored_ready != ready:
        raise H7ReadinessError(
            f"release_readiness.ready={stored_ready} contradicts the rows ({ready}); "
            f"open required rows: {open_ids}"
        )
    stored_open = _sequence(
        readiness.get("open_required_row_ids"), "release_readiness.open_required_row_ids"
    )
    if stored_open != open_ids:
        raise H7ReadinessError(
            f"open_required_row_ids {stored_open} is not the mechanical list {open_ids}"
        )
    required = _sequence(readiness.get("required_row_ids"), "release_readiness.required_row_ids")
    expected_required = [
        str(row.get("row_id")) for row in rows if row.get("required_for_release") is True
    ]
    if required != expected_required:
        raise H7ReadinessError("required_row_ids does not match the required_for_release rows")


# ── handoff validator ────────────────────────────────────────────────────


def validate_handoff(doc: Mapping[str, Any], repo: Path = REPO) -> None:
    """Validate the full H7 handoff against the files on disk."""
    if doc.get("handoff_id") != "H7":
        raise H7ContractError("handoff_id must be H7")
    if doc.get("generated_by") != "A-29":
        raise H7ContractError("generated_by must be A-29")
    if doc.get("contract_set") != "acp2.science":
        raise H7ContractError("contract_set must be acp2.science")
    if doc.get("contract_set_version") != 1 or doc.get("contract_set_frozen") is not True:
        raise H7ContractError("the science contract set must be v1 and frozen")

    source = _mapping(doc.get("source"), "source")
    pre_delivery = _text(source.get("pre_delivery_commit"), "source.pre_delivery_commit")
    if not COMMIT_PATTERN.match(pre_delivery):
        raise H7RevisionError("source.pre_delivery_commit is not a commit id")
    if not _git_commit_exists(repo, pre_delivery):
        raise H7RevisionError("source.pre_delivery_commit does not resolve")
    if source.get("delivery_commit") is not None:
        raise H7ContractError("the delivery commit belongs in the evidence record, not the handoff")

    handoffs = _mapping(doc.get("handoff_digests"), "handoff_digests")
    expected_handoffs = {f"H{i}" for i in range(1, 7)}
    if set(handoffs) != expected_handoffs:
        raise H7RevisionError("handoff_digests must cover exactly H1..H6")
    for key, raw in handoffs.items():
        entry = _mapping(raw, f"handoff_digests.{key}")
        path = _text(entry.get("path"), f"handoff_digests.{key}.path")
        digest = _text(entry.get("sha256"), f"handoff_digests.{key}.sha256")
        resolved = _resolve(repo, path, f"handoff_digests.{key}")
        if not HEX_PATTERN.match(digest) or _sha256(resolved) != digest:
            raise H7RevisionError(f"handoff_digests.{key}: stale or malformed digest")
        recorded = entry.get("source_pre_delivery_commit")
        if recorded is None:
            if key != "H1":
                raise H7RevisionError(f"handoff_digests.{key}: only H1 may omit the source commit")
            if (
                _text(entry.get("revision_check"), f"handoff_digests.{key}.revision_check")[:16]
                != "NO_SOURCE_COMMIT"
            ):
                raise H7RevisionError("H1 must record that it predates the source block")
        elif not _git_commit_exists(repo, str(recorded)):
            raise H7RevisionError(f"handoff_digests.{key}: recorded commit {recorded!r} is absent")

    contract_digests = _mapping(doc.get("contract_digests"), "contract_digests")
    contract_files = _mapping(
        contract_digests.get("contract_files"), "contract_digests.contract_files"
    )
    if len(contract_files) != 14:
        raise H7RevisionError("contract_digests.contract_files must list all 14 contract artifacts")
    for path, digest in contract_files.items():
        resolved = _resolve(repo, str(path), "contract_digests.contract_files")
        if not isinstance(digest, str) or _sha256(resolved) != digest:
            raise H7RevisionError(f"contract digest mismatch: {path}")

    fixture_digests = _mapping(
        contract_digests.get("fixture_digests"), "contract_digests.fixture_digests"
    )
    fixture_paths = _mapping(
        contract_digests.get("fixture_paths"), "contract_digests.fixture_paths"
    )
    if len(fixture_digests) != 8 or set(fixture_digests) != set(fixture_paths):
        raise H7RevisionError("fixture digest/path tables must cover the same eight fixtures")
    for key, digest in fixture_digests.items():
        resolved = _resolve(repo, str(fixture_paths[key]), f"fixture_digests.{key}")
        if _sha256(resolved) != digest:
            raise H7RevisionError(f"fixture digest mismatch: {key}")

    rows = _sequence(doc.get("rows"), "rows")
    row_ids: list[str] = [str(row.get("row_id")) for row in rows if isinstance(row, dict)]
    if len(row_ids) != len(set(row_ids)):
        raise H7RowStatusError("duplicate row_id in handoff rows")
    if set(row_ids) != REQUIRED_ROW_IDS:
        raise H7RowStatusError(
            f"rows must be exactly {sorted(REQUIRED_ROW_IDS)}, got {sorted(set(row_ids))}"
        )
    for raw in rows:
        _validate_row(_mapping(raw, "row"), repo)

    validate_readiness(doc)

    signature = _mapping(doc.get("h7_signature"), "h7_signature")
    if signature.get("signed_by") != "A":
        raise H7ContractError("H7 must be signed by A")
    if signature.get("candidate_b25a") != "NOT_VERIFIED":
        raise H7ContractError("B25a is absent; candidate_b25a must stay NOT_VERIFIED")
    if signature.get("candidate_b25a_revision") is not None:
        raise H7ContractError("no B25a candidate revision may be invented")
    signed_from = _mapping(signature.get("signed_from"), "h7_signature.signed_from")
    if signed_from.get("b25a_candidate") != "NOT_VERIFIED":
        raise H7ContractError("signed_from.b25a_candidate must stay NOT_VERIFIED")
    if signed_from.get("a27b_verification") != "PASS":
        raise H7ContractError("signed_from.a27b_verification must be PASS")
    if signed_from.get("a28_verification") != "PASS":
        raise H7ContractError("signed_from.a28_verification must be PASS")

    prerequisites = _sequence(doc.get("prerequisites"), "prerequisites")
    known = REQUIRED_ROW_IDS - {"B25a-release-candidate"} | {"A-27b", "A-28"}
    for raw in prerequisites:
        entry = _mapping(raw, "prerequisites[]")
        artifact_id = _text(entry.get("artifact_id"), "prerequisites[].artifact_id")
        if artifact_id not in known:
            raise H7PrerequisiteError(
                f"prerequisite {artifact_id!r} is not an existing A handoff item"
            )
    for value in _walk_strings(doc):
        for token in FORBIDDEN_PREREQUISITE_TOKENS:
            if re.search(rf"\b{re.escape(token)}\b", value):
                raise H7PrerequisiteError(
                    f"forbidden aggregation token {token!r} appears in the H7 handoff"
                )

    checks = _mapping(doc.get("release_checks"), "release_checks")
    for check_name in (
        "reverse_import_scan",
        "one_execution_station",
        "active_workflows",
        "compatibility_surfaces",
    ):
        check = _mapping(checks.get(check_name), f"release_checks.{check_name}")
        if check.get("result") != "PASS":
            raise H7ContractError(f"release_checks.{check_name} must be PASS")
        if _integer(check.get("exit_code"), f"release_checks.{check_name}.exit_code") != 0:
            raise H7ContractError(f"release_checks.{check_name} must record exit_code 0")
    workflows = _mapping(checks.get("active_workflows"), "release_checks.active_workflows")
    ids = _sequence(workflows.get("ids"), "release_checks.active_workflows.ids")
    if tuple(ids) != EXPECTED_ACTIVE_WORKFLOWS:
        raise H7ContractError("the 15 active workflow ids are not the exact ordered tuple")
    if workflows.get("count") != 15:
        raise H7ContractError("active workflow count must be 15")

    matrix = _mapping(doc.get("support_matrix"), "support_matrix")
    states = _mapping(matrix.get("capabilities"), "support_matrix.capabilities")
    if states != EXPECTED_CAPABILITIES:
        raise H7SupportMatrixError("support_matrix.capabilities drifted from the delivered set")
    if matrix.get("task_kind_count") != 15:
        raise H7SupportMatrixError("support_matrix.task_kind_count must be 15")
    matrix_path = _resolve(repo, _text(matrix.get("path"), "support_matrix.path"), "support_matrix")
    validate_support_matrix(matrix_path.read_text(encoding="utf-8"), states)


# ── support matrix validator ─────────────────────────────────────────────


def parse_support_matrix(text: str) -> dict[str, str]:
    """Return ``{capability_id: contract_state}`` from the markdown table."""
    states: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|") or "`" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6:
            continue
        capability = cells[1]
        if not capability.startswith("`") or not capability.endswith("`"):
            continue
        capability_id = capability.strip("`")
        if capability_id not in EXPECTED_CAPABILITIES:
            continue
        state = cells[5].strip("`")
        if capability_id in states:
            raise H7SupportMatrixError(f"duplicate matrix row for {capability_id}")
        states[capability_id] = state
    return states


def validate_support_matrix(text: str, expected: Mapping[str, str]) -> None:
    """Require exactly the seven capabilities and states equal to the handoff."""
    states = parse_support_matrix(text)
    if set(states) != set(EXPECTED_CAPABILITIES):
        raise H7SupportMatrixError(
            f"matrix must list exactly {sorted(EXPECTED_CAPABILITIES)}, got {sorted(states)}"
        )
    allowed_states = {"verified-static", "constructed", "NOT_VERIFIED", "not_applicable"}
    for capability_id, state in states.items():
        if state not in allowed_states:
            raise H7SupportMatrixError(f"{capability_id}: unknown state {state!r}")
        if expected.get(capability_id) == "NOT_VERIFIED" and "verified" in state.lower():
            raise H7SupportMatrixError(
                f"{capability_id}: a NOT_VERIFIED capability cannot be rendered as verified"
            )
        if state != expected.get(capability_id):
            raise H7SupportMatrixError(
                f"{capability_id}: matrix state {state!r} != handoff state "
                f"{expected.get(capability_id)!r}"
            )


# ── replay index validator ───────────────────────────────────────────────

COVERAGE_PATH_KEYS: Final[tuple[str, ...]] = ()


def h7_referenced_paths(doc: Mapping[str, Any]) -> set[str]:
    """Return every repo-relative evidence path the H7 handoff references."""
    paths: set[str] = {"docs/reference/next-change/acp2-contracts/science/baseline.md"}
    handoffs = _mapping(doc.get("handoff_digests"), "handoff_digests")
    for entry in handoffs.values():
        paths.add(str(_mapping(entry, "handoff").get("path")))
    contract = _mapping(doc.get("contract_digests"), "contract_digests")
    paths.update(str(path) for path in _mapping(contract.get("contract_files"), "contract_files"))
    for raw_row in _sequence(doc.get("rows"), "rows"):
        row = _mapping(raw_row, "row")
        evidence = _mapping(row.get("evidence"), "row.evidence")
        for artifact in _sequence(evidence.get("artifacts"), "row.evidence.artifacts"):
            paths.add(str(_mapping(artifact, "artifact").get("path")))
    return paths


def validate_replay_index(index: Mapping[str, Any], repo: Path = REPO) -> None:
    """Validate every replay entry and require full H7 evidence coverage."""
    if index.get("schema") != "acp2.science.replay-index.v1":
        raise H7ReplayIndexError("unexpected replay-index schema")
    expected_exit = _integer(index.get("expected_exit_code", 0), "expected_exit_code")
    del expected_exit
    entries = _sequence(index.get("entries"), "entries")
    if not entries:
        raise H7ReplayIndexError("replay index must have entries")
    covered: set[str] = set()
    commands: set[str] = set()
    for raw in entries:
        entry = _mapping(raw, "replay.entry")
        entry_id = _text(entry.get("entry_id"), "replay.entry.entry_id")
        command = _text(entry.get("command"), f"{entry_id}.command")
        commands.add(command)
        first = command.split(" ", 1)[0]
        if not Path(first).is_absolute() or not Path(first).is_file():
            raise H7ReplayIndexError(f"{entry_id}: command interpreter {first!r} is not absolute")
        cwd = _text(entry.get("cwd"), f"{entry_id}.cwd")
        if cwd != "." and not Path(cwd).is_absolute():
            raise H7ReplayIndexError(f"{entry_id}: cwd {cwd!r} must be absolute or '.'")
        interpreter = _text(entry.get("interpreter"), f"{entry_id}.interpreter")
        if not Path(interpreter).is_absolute():
            raise H7ReplayIndexError(f"{entry_id}: interpreter must be absolute")
        if _integer(entry.get("expected_exit_code"), f"{entry_id}.expected_exit_code") != 0:
            raise H7ReplayIndexError(f"{entry_id}: expected_exit_code must be 0")
        if entry.get("expected_result") != "PASS":
            raise H7ReplayIndexError(f"{entry_id}: expected_result must be PASS")
        records = _sequence(entry.get("evidence_records"), f"{entry_id}.evidence_records")
        for record in records:
            raw_record = _text(record, f"{entry_id}.evidence_records[]")
            try:
                _resolve(repo, raw_record, f"{entry_id}.evidence_record")
            except H7ContractError as error:
                raise H7ReplayIndexError(str(error)) from error
            if not Path(raw_record).is_absolute():
                covered.add(raw_record)

    if FULL_GATE_COMMAND not in commands:
        raise H7ReplayIndexError("the replay index must contain the full science gate command")
    if WHEEL_PROBE_COMMAND not in commands:
        raise H7ReplayIndexError("the replay index must contain the A-27b wheel probe command")

    doc = _load(repo / "docs/reference/next-change/acp2-contracts/science/handoff-H7.json")
    missing = sorted(h7_referenced_paths(doc) - covered)
    if missing:
        raise H7ReplayIndexError(f"replay index does not cover H7 evidence paths: {missing}")


# ── tests ────────────────────────────────────────────────────────────────


def _h7() -> dict[str, Any]:
    return _load(H7_PATH)


def test_full_handoff_and_replay_index_validate() -> None:
    doc = _h7()
    validate_handoff(doc)
    validate_replay_index(_load(REPLAY_PATH))


def test_h0_and_h1_to_h6_rows_exist_exactly_once() -> None:
    rows = [row["row_id"] for row in _h7()["rows"]]
    assert len(rows) == len(set(rows)) == 10
    assert set(rows) == REQUIRED_ROW_IDS


def test_every_row_has_result_scope_revision_and_evidence() -> None:
    for row in _h7()["rows"]:
        assert row["result"] in ALLOWED_RESULTS
        assert row["scope"] in ALLOWED_SCOPES
        assert row["revision_kind"] in ALLOWED_REVISION_KINDS
        assert "revision" in row
        evidence = row["evidence"]
        assert evidence["summary"].strip()
        assert isinstance(evidence["commands"], list)
        assert isinstance(evidence["artifacts"], list)


def test_commit_revisions_exist() -> None:
    for row in _h7()["rows"]:
        if row["revision_kind"] != "commit":
            continue
        assert _git_commit_exists(REPO, row["revision"]), row["row_id"]


def test_stale_handoff_digest_is_rejected() -> None:
    doc = copy.deepcopy(_h7())
    doc["handoff_digests"]["H1"]["sha256"] = "0" * 64
    with pytest.raises(H7RevisionError):
        validate_handoff(doc)


def test_stale_contract_digest_is_rejected() -> None:
    doc = copy.deepcopy(_h7())
    first = next(iter(doc["contract_digests"]["contract_files"]))
    doc["contract_digests"]["contract_files"][first] = "0" * 64
    with pytest.raises(H7RevisionError):
        validate_handoff(doc)


def test_handoff_digests_match_files_on_disk() -> None:
    for key, entry in _h7()["handoff_digests"].items():
        path = REPO / entry["path"]
        assert _sha256(path) == entry["sha256"], key


def test_h1_has_no_source_commit_and_uses_its_recorded_digest() -> None:
    entry = _h7()["handoff_digests"]["H1"]
    assert entry["source_pre_delivery_commit"] is None
    assert entry["revision_check"].startswith("NO_SOURCE_COMMIT")
    h1_row = next(row for row in _h7()["rows"] if row["row_id"] == "H1")
    assert h1_row["revision"] == f"sha256:{entry['sha256']}"


def test_release_readiness_is_derived_from_rows() -> None:
    doc = _h7()
    ready, open_ids = compute_release_readiness(doc["rows"])
    assert doc["release_readiness"]["ready"] is ready
    assert doc["release_readiness"]["open_required_row_ids"] == open_ids


def test_release_readiness_is_false_with_expected_open_rows() -> None:
    readiness = _h7()["release_readiness"]
    assert readiness["ready"] is False
    assert tuple(readiness["open_required_row_ids"]) == REQUIRED_OPEN_ROW_IDS


def test_flipped_unexecuted_b25a_pass_is_rejected() -> None:
    doc = copy.deepcopy(_h7())
    row = next(row for row in doc["rows"] if row["row_id"] == "B25a-release-candidate")
    row["result"] = "PASS"
    row["gap"] = None
    row["revision_kind"] = "commit"
    row["revision"] = "4f123256dfd2e8d1a2486d422261405bc95efce0"
    row["evidence"]["executed"] = False
    row["evidence"]["commands"] = []
    with pytest.raises(H7RowStatusError) as error:
        validate_handoff(doc)
    assert "executed" in str(error.value)


def test_flipped_da_or_selectivity_pass_with_constructed_evidence_is_rejected() -> None:
    for row_id in ("H4", "H6"):
        doc = copy.deepcopy(_h7())
        row = next(row for row in doc["rows"] if row["row_id"] == row_id)
        row["result"] = "PASS"
        row["real_run"] = False
        row["evidence"]["real_artifact"] = False
        with pytest.raises(H7RowStatusError):
            validate_handoff(doc)


def test_ready_true_with_open_row_is_rejected() -> None:
    doc = copy.deepcopy(_h7())
    doc["release_readiness"]["ready"] = True
    with pytest.raises(H7ReadinessError):
        validate_handoff(doc)


def test_real_run_true_with_constructed_evidence_is_rejected() -> None:
    doc = copy.deepcopy(_h7())
    row = next(row for row in doc["rows"] if row["row_id"] == "H4")
    row["real_run"] = True
    row["evidence"]["real_artifact"] = False
    with pytest.raises(H7RowStatusError):
        validate_handoff(doc)


def test_b26_b25b_c35_tokens_are_absent() -> None:
    for value in _walk_strings(_h7()):
        for token in FORBIDDEN_PREREQUISITE_TOKENS:
            assert not re.search(rf"\b{re.escape(token)}\b", value), (token, value)
    doc = copy.deepcopy(_h7())
    doc["prerequisites"].append(
        {"artifact_id": "A-28", "kind": "verification", "availability": "present", "state": "B26"}
    )
    with pytest.raises(H7PrerequisiteError):
        validate_handoff(doc)


def test_support_matrix_lists_exactly_seven_capabilities() -> None:
    states = parse_support_matrix(MATRIX_PATH.read_text(encoding="utf-8"))
    assert set(states) == set(EXPECTED_CAPABILITIES)
    assert len(states) == 7


def test_support_matrix_states_agree_with_handoff() -> None:
    doc = _h7()
    validate_support_matrix(
        MATRIX_PATH.read_text(encoding="utf-8"), doc["support_matrix"]["capabilities"]
    )


def test_support_matrix_cannot_promote_not_verified_to_verified() -> None:
    text = MATRIX_PATH.read_text(encoding="utf-8")
    mutated = text.replace(
        "| `nmr.shielding` | compute | `nmr_shielding` → `cccp.calculation.tasks.nmr_shielding` "
        "| capability.schema v1 + domain extension v1 | `constructed` |",
        "| `nmr.shielding` | compute | `nmr_shielding` → `cccp.calculation.tasks.nmr_shielding` "
        "| capability.schema v1 + domain extension v1 | `verified-static` |",
    )
    assert mutated != text
    expected = dict(EXPECTED_CAPABILITIES)
    expected["nmr.shielding"] = "NOT_VERIFIED"
    with pytest.raises(H7SupportMatrixError):
        validate_support_matrix(mutated, expected)


@pytest.mark.skipif(
    not Path("/opt/acp2-evidence").is_dir(),
    reason="A-lane live evidence is external to the repository (absent in a clean checkout)",
)
def test_replay_index_covers_every_h7_evidence_path() -> None:
    validate_replay_index(_load(REPLAY_PATH))


def test_replay_index_pins_full_gate_and_wheel_probe() -> None:
    commands = {entry["command"] for entry in _load(REPLAY_PATH)["entries"]}
    assert FULL_GATE_COMMAND in commands
    assert WHEEL_PROBE_COMMAND in commands


def test_replay_index_missing_evidence_path_is_rejected() -> None:
    index = copy.deepcopy(_load(REPLAY_PATH))
    index["entries"][0]["evidence_records"].append(
        "docs/reference/next-change/absent-evidence.json"
    )
    with pytest.raises(H7ReplayIndexError):
        validate_replay_index(index)


def test_scope_distinguishes_static_constructed_from_real() -> None:
    for row in _h7()["rows"]:
        assert row["scope"] in ALLOWED_SCOPES
        if row["real_run"]:
            assert row["scope"] == "real"
        if row["requires_real_run"] and row["result"] == "PASS":
            assert row["real_run"] is True


def test_real_run_requires_a_real_artifact() -> None:
    for row in _h7()["rows"]:
        if not row["real_run"]:
            continue
        assert row["evidence"]["real_artifact"] is True
        artifacts = row["evidence"]["artifacts"]
        assert all(not _is_constructed_artifact(artifact["path"]) for artifact in artifacts)


def test_handoff_records_the_science_gate_and_wheel_evidence() -> None:
    doc = _h7()
    a27b = next(row for row in doc["rows"] if row["row_id"] == "A-27b-installed-package")
    a28 = next(row for row in doc["rows"] if row["row_id"] == "A-28-sdk-examples")
    assert a27b["result"] == "PASS"
    assert a28["result"] == "PASS"
    assert any("installed_package" in command for command in a27b["evidence"]["commands"])
    assert doc["contract_digests"]["schema_versions"]["acceptance_result_schema"] is None
