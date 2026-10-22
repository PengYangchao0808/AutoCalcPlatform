# pyright: reportUnknownVariableType=false, reportUnknownMemberType=false
"""ACP2 science inventory validator (Plan A todo 2 / A-00b).

Validates the three A-00b deliverables with **standard-library JSON parsing
only** — no third-party schema engine (`jsonschema` is deliberately not used
and must not be imported):

* ``docs/reference/next-change/acp2-contracts/science/cross-track-dependencies.json``
  — required row fields, readiness/phase/evidence enums, explicit AND/OR
  precondition shape, producer/consumer id resolution, precondition target
  resolution and a DFS cycle check.
* ``docs/reference/next-change/acp2-contracts/science/packaging-preview.md``
  — the fenced machine-readable summary block (``packages[]`` +
  ``wheel_smoke_argv[]``), on-disk existence of every named package/asset
  path and the absolute-or-repo-relative token rule.
* ``docs/reference/next-change/acp2-contracts/science/baseline.md``
  — exactly fifteen active workflows and fifteen task kinds, cross-checked
  against the live registry/enum (code is the count authority).

Two negative cases are first-class unit tests: a nonexistent B product id and
a missing package-data path must be rejected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SCIENCE = REPO / "docs" / "reference" / "next-change" / "acp2-contracts" / "science"
LEDGER_PATH = SCIENCE / "cross-track-dependencies.json"
PREVIEW_PATH = SCIENCE / "packaging-preview.md"
BASELINE_PATH = SCIENCE / "baseline.md"

REQUIRED_ROW_FIELDS = frozenset(
    {
        "task_id",
        "artifact_id",
        "product",
        "producer",
        "consumer",
        "phase",
        "version",
        "digest",
        "readiness",
        "preconditions",
        "evidence_state",
        "gap",
    }
)
ALLOWED_READINESS = frozenset(
    {"contract_ready", "implementation_ready", "integration_ready", "verified"}
)
ALLOWED_PHASES = frozenset(
    {"main", "da-evidence", "experience:nmr", "experience:thermo", "experience:selectivity"}
)
ALLOWED_EVIDENCE = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})

#: Edges the plan's acceptance criteria require to be present (artifact-level).
REQUIRED_EXTERNAL_IDS = (
    "B00",
    "B01",
    "B05",
    "B07",
    "B08",
    "B20a-exec",
    "B20a-search",
    "B23b",
    "C T-40a",
    "C T-40b",
    "B25a",
)

#: Top-level package/asset paths the plan names for the packaging preview.
REQUIRED_PLAN_PATHS = (
    "src/cccp",
    "src/acp",
    "src/acp_services",
    "frontend",
    "packaging",
    "scripts",
    "tests",
    "docs",
    "src/cccp/analysis/nmr/model_assets",
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


# ── ledger ──────────────────────────────────────────────────────────────


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    """Parse the cross-track ledger with stdlib JSON only."""
    with path.open(encoding="utf-8") as handle:
        doc: dict[str, Any] = json.load(handle)
    assert isinstance(doc, dict), "ledger root must be a JSON object"
    return doc


def validate_ledger(doc: dict[str, Any]) -> None:
    """Reject any structural, enum, id-resolution or precondition defect."""
    for key in (
        "schema",
        "owner",
        "task_id",
        "rows",
        "allowed_phases",
        "allowed_readiness",
        "allowed_evidence_state",
        "declared_external_task_ids",
        "declared_external_artifacts",
        "phase_registry_gap",
        "readiness_semantics",
        "ledger_scope",
    ):
        assert key in doc, f"ledger missing top-level field {key!r}"

    assert doc["owner"] == "A", "this ledger holds A's external edges only"
    assert frozenset(doc["allowed_phases"]) <= ALLOWED_PHASES, (
        f"undeclared phase values: {set(doc['allowed_phases']) - ALLOWED_PHASES}"
    )
    assert frozenset(doc["allowed_readiness"]) == ALLOWED_READINESS, "readiness enum drift"
    assert frozenset(doc["allowed_evidence_state"]) == ALLOWED_EVIDENCE, "evidence enum drift"
    assert "B01" in str(doc["phase_registry_gap"]), (
        "phase_registry_gap must name B01 as the unpublished registry owner"
    )

    rows = doc["rows"]
    assert isinstance(rows, list) and rows, "ledger must contain rows"
    artifact_ids = [row.get("artifact_id") for row in rows]
    assert all(isinstance(a, str) and a for a in artifact_ids), "every row needs an artifact_id"
    assert len(set(artifact_ids)) == len(artifact_ids), "duplicate artifact_id in ledger rows"

    external_tasks = set(doc["declared_external_task_ids"])
    external_artifacts = set(doc["declared_external_artifacts"])
    task_ids = {row.get("task_id") for row in rows}
    known_endpoints = task_ids | external_tasks  # endpoints are task ids, never artifact ids

    for row in rows:
        missing = REQUIRED_ROW_FIELDS - set(row)
        assert not missing, f"row {row.get('artifact_id')!r} missing fields {sorted(missing)}"
        where = row["artifact_id"]

        assert row["readiness"] in ALLOWED_READINESS, (
            f"{where}: readiness {row['readiness']!r} not in {sorted(ALLOWED_READINESS)}"
        )
        assert row["phase"] in ALLOWED_PHASES, (
            f"{where}: phase {row['phase']!r} not in {sorted(ALLOWED_PHASES)}"
        )
        assert row["evidence_state"] in ALLOWED_EVIDENCE, (
            f"{where}: evidence_state {row['evidence_state']!r} invalid"
        )

        for endpoint in (row["producer"], row["consumer"]):
            assert isinstance(endpoint, str) and endpoint, f"{where}: empty producer/consumer"
            assert endpoint in known_endpoints, (
                f"{where}: producer/consumer {endpoint!r} is neither a row task_id "
                f"nor a declared external id"
            )

        pre = row["preconditions"]
        assert isinstance(pre, dict) and set(pre) == {"and", "or"}, (
            f"{where}: preconditions must be exactly {{'and': [...], 'or': [...]}}, got "
            f"{sorted(pre) if isinstance(pre, dict) else type(pre).__name__}"
        )
        for op in ("and", "or"):
            values = pre[op]
            assert isinstance(values, list), f"{where}: preconditions.{op} must be an array"
            for value in values:
                assert isinstance(value, str) and value.strip(), (
                    f"{where}: preconditions.{op} entries must be non-empty strings"
                )
                assert value in artifact_ids or value in external_artifacts, (
                    f"{where}: preconditions.{op} references unknown artifact {value!r}"
                )
        # empty AND/OR lists are legal (no precondition)

        digest = row["digest"]
        if digest is None:
            assert row["gap"], f"{where}: null digest requires an explicit gap"
        else:
            assert isinstance(digest, str) and _SHA256_RE.match(digest), (
                f"{where}: digest must be null or sha256:<64 hex>, got {digest!r}"
            )
        assert row["version"] is None or isinstance(row["version"], str), (
            f"{where}: version must be null or a string"
        )
        assert isinstance(row["gap"], str) and row["gap"].strip(), f"{where}: gap must be stated"

        public_key = row.get("public_key")
        if public_key is not None:
            assert set(public_key) == {"owner", "scenario", "phase", "run_id"}, (
                f"{where}: public_key must be the plan's four-field receipt key"
            )
            assert public_key["run_id"] is None, (
                f"{where}: run_id is unknown and must stay null (never invented)"
            )

    # Explicit AND/OR must be present on rows the plan calls "artifact-level edges".
    for row in rows:
        assert isinstance(row["preconditions"]["and"], list)
        assert isinstance(row["preconditions"]["or"], list)


def find_cycles(doc: dict[str, Any]) -> list[list[str]]:
    """DFS cycle check over precondition edges; returns cycle paths (none expected)."""
    graph: dict[str, list[str]] = {}
    artifact_ids = {row["artifact_id"] for row in doc["rows"]}
    for row in doc["rows"]:
        targets = [*row["preconditions"]["and"], *row["preconditions"]["or"]]
        graph[row["artifact_id"]] = [t for t in targets if t in artifact_ids]

    state: dict[str, int] = {}  # 0 = unvisited, 1 = on stack, 2 = done
    cycles: list[list[str]] = []

    def walk(node: str, stack: list[str]) -> None:
        state[node] = 1
        for child in graph.get(node, []):
            status = state.get(child, 0)
            if status == 1:
                cycles.append([*stack, child])
            elif status == 0:
                walk(child, [*stack, child])
        state[node] = 2

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            walk(node, [node])
    return cycles


def test_ledger_parses_and_is_deterministic() -> None:
    doc = load_ledger()
    raw = LEDGER_PATH.read_text(encoding="utf-8")
    regenerated = json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    assert regenerated == raw, "ledger must be byte-identical when re-serialized with sorted keys"


def test_ledger_required_fields_readiness_and_precondition_shape() -> None:
    doc = load_ledger()
    validate_ledger(doc)  # raises AssertionError with the exact defect
    readineses = {row["readiness"] for row in doc["rows"]}
    assert readineses <= ALLOWED_READINESS
    for row in doc["rows"]:
        assert set(row["preconditions"]) == {"and", "or"}


def test_ledger_producer_and_consumer_ids_resolve() -> None:
    doc = load_ledger()
    known = {row["task_id"] for row in doc["rows"]} | set(doc["declared_external_task_ids"])
    for row in doc["rows"]:
        for endpoint in (row["producer"], row["consumer"]):
            assert endpoint in known, (
                f"{row['artifact_id']}: unresolved id {endpoint!r} "
                f"(row task_ids + declared external ids only)"
            )


def test_ledger_precondition_targets_resolve() -> None:
    doc = load_ledger()
    artifacts = {row["artifact_id"] for row in doc["rows"]}
    external = set(doc["declared_external_artifacts"])
    for row in doc["rows"]:
        for op in ("and", "or"):
            for target in row["preconditions"][op]:
                assert target in artifacts or target in external, (
                    f"{row['artifact_id']}: dangling precondition {target!r}"
                )


def test_ledger_has_no_cycle() -> None:
    cycles = find_cycles(load_ledger())
    assert cycles == [], f"cross-track ledger must be acyclic, found: {cycles}"
    print("cycle check: none")


def test_ledger_enumerates_required_external_edges() -> None:
    doc = load_ledger()
    endpoints = set()
    for row in doc["rows"]:
        endpoints.add(row["producer"])
        endpoints.add(row["consumer"])
    for required in REQUIRED_EXTERNAL_IDS:
        assert required in endpoints, f"required external edge {required!r} not enumerated"
    # Each required id must sit on its own artifact-level row (not one readiness bit
    # for a whole B/C task).
    by_endpoint: dict[str, int] = {}
    for row in doc["rows"]:
        for endpoint in (row["producer"], row["consumer"]):
            if endpoint in REQUIRED_EXTERNAL_IDS:
                by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
    for required in REQUIRED_EXTERNAL_IDS:
        assert by_endpoint[required] >= 1, f"{required!r} must sit on its own artifact-level row"


def test_ledger_phases_restricted_and_registry_gap_recorded() -> None:
    doc = load_ledger()
    assert set(doc["allowed_phases"]) == ALLOWED_PHASES
    for row in doc["rows"]:
        assert row["phase"] in ALLOWED_PHASES
        assert row["phase"] not in {"fixture", "live"}, "bare fixture/live phases are forbidden"
    gap = doc["phase_registry_gap"]
    assert "B01" in gap and "registry" in gap and "run_id" in gap


def test_ledger_evidence_states_are_honest() -> None:
    doc = load_ledger()
    for row in doc["rows"]:
        if row["evidence_state"] == "PASS":
            assert row["digest"] is not None, (
                f"{row['artifact_id']}: PASS evidence must carry a verified digest"
            )
            assert row["readiness"] == "verified", (
                f"{row['artifact_id']}: PASS/verified must travel together"
            )
        if row["evidence_state"] == "NOT_VERIFIED":
            assert row["gap"], f"{row['artifact_id']}: NOT_VERIFIED needs the exact gap"
            assert row["readiness"] != "verified", (
                f"{row['artifact_id']}: NOT_VERIFIED cannot be readiness=verified"
            )


# ── packaging preview ───────────────────────────────────────────────────


def extract_json_blocks(markdown: str) -> list[dict[str, Any]]:
    """Return every fenced ```json block body parsed with stdlib json."""
    blocks: list[dict[str, Any]] = []
    for match in re.finditer(r"```json\n(.*?)\n```", markdown, flags=re.DOTALL):
        parsed = json.loads(match.group(1))
        assert isinstance(parsed, dict), "summary block must be a JSON object"
        blocks.append(parsed)
    return blocks


def load_packaging_summary(path: Path = PREVIEW_PATH) -> dict[str, Any]:
    blocks = extract_json_blocks(path.read_text(encoding="utf-8"))
    for block in blocks:
        if "packages" in block and "wheel_smoke_argv" in block:
            return block
    raise AssertionError(
        "packaging-preview.md must contain a fenced json summary block "
        "with packages[] and wheel_smoke_argv[]"
    )


def validate_packaging_summary(summary: dict[str, Any], repo: Path = REPO) -> None:
    """Reject unknown package paths, missing plan paths and bad smoke tokens."""
    packages = summary.get("packages")
    assert isinstance(packages, list) and packages, "packages[] must be a non-empty array"
    seen: set[str] = set()
    for entry in packages:
        assert isinstance(entry, dict), "each packages[] entry must be an object"
        for field in ("path", "kind", "owner", "ships_in", "evidence"):
            assert field in entry, f"packages[] entry missing {field!r}: {entry}"
        package_path = entry["path"]
        assert package_path not in seen, f"duplicate packages[] path {package_path!r}"
        seen.add(package_path)
        resolved = repo / package_path
        assert resolved.exists(), (
            f"package-data path {package_path!r} does not exist on disk at {resolved}"
        )
        assert entry["evidence"] in {"static-fact", "needs-real-evidence"}

    for required in REQUIRED_PLAN_PATHS:
        assert required in seen, f"plan-named top-level path {required!r} missing from packages[]"

    argv = summary.get("wheel_smoke_argv")
    assert isinstance(argv, list) and argv, "wheel_smoke_argv[] must be a non-empty array"
    for token in argv:
        assert isinstance(token, str) and token, "argv tokens must be non-empty strings"
        if token.startswith("/"):
            continue  # absolute
        if token.startswith("-") and "/" not in token:
            continue  # flag word, not a path
        if "/" not in token:
            continue  # bare value word (e.g. role name), not a path
        assert (repo / token).exists(), (
            f"argv token {token!r} is neither absolute nor an existing repo-relative path"
        )


def test_packaging_summary_block_parses() -> None:
    summary = load_packaging_summary()
    assert "packages" in summary and "wheel_smoke_argv" in summary


def test_packaging_paths_exist_on_disk() -> None:
    validate_packaging_summary(load_packaging_summary())


def test_wheel_smoke_argv_tokens_are_absolute_or_repo_relative() -> None:
    argv = load_packaging_summary()["wheel_smoke_argv"]
    assert argv[0].startswith("/"), "interpreter token must be absolute"
    for token in argv:
        if "/" in token and not token.startswith("/"):
            assert (REPO / token).exists(), f"repo-relative argv token missing: {token}"


def test_plan_named_top_level_paths_all_present() -> None:
    summary = load_packaging_summary()
    paths = {entry["path"] for entry in summary["packages"]}
    for required in REQUIRED_PLAN_PATHS:
        assert required in paths, f"plan-named path {required!r} not inventoried"
        assert (REPO / required).exists(), f"plan-named path {required!r} absent on disk"


# ── baseline inventory tables ───────────────────────────────────────────


def _table_rows(marker: str) -> list[list[str]]:
    """Return the data rows of the first markdown table whose header starts *marker*."""
    lines = BASELINE_PATH.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith(marker):
            rows: list[list[str]] = []
            for body in lines[index + 2 :]:  # skip header + separator
                if not body.startswith("|"):
                    break
                cells = [cell.strip().strip("`") for cell in body.strip("|").split("|")]
                rows.append(cells)
            return rows
    raise AssertionError(f"baseline.md has no table headed {marker!r}")


def _workflow_ids() -> list[str]:
    rows = _table_rows("| # | CLI name")
    return [row[1] for row in rows]


def _task_kind_ids() -> list[str]:
    rows = _table_rows("| # | Task kind id")
    return [row[1] for row in rows]


def test_baseline_inventory_has_exactly_fifteen_workflows_and_fifteen_task_kinds() -> None:
    workflows = _workflow_ids()
    task_kinds = _task_kind_ids()
    assert len(workflows) == 15, f"expected 15 workflow rows, got {len(workflows)}: {workflows}"
    assert len(task_kinds) == 15, f"expected 15 task rows, got {len(task_kinds)}: {task_kinds}"
    print(f"inventory counts: workflows={len(workflows)} task_kinds={len(task_kinds)}")


def test_baseline_workflow_rows_match_live_registry() -> None:
    from acp.scheduler.jobs import PUBLIC_WORKFLOWS

    assert tuple(_workflow_ids()) == tuple(PUBLIC_WORKFLOWS), (
        "baseline workflow table must equal the live PUBLIC_WORKFLOWS order/ids"
    )


def test_baseline_task_rows_match_live_task_kind_enum() -> None:
    from cccp.calculation.requests import TaskKind

    assert set(_task_kind_ids()) == {member.value for member in TaskKind}, (
        "baseline task table must equal the live TaskKind enum"
    )
    from cccp.calculation.tasks import __all__ as task_exports

    assert len(task_exports) == 15, "cccp.calculation.tasks must export exactly 15 run_*"


def test_baseline_rows_carry_evidence_markers() -> None:
    text = BASELINE_PATH.read_text(encoding="utf-8")
    assert "`static-fact`" in text
    assert "`needs-real-evidence`" in text
    for row in _table_rows("| # | CLI name"):
        assert row[-1] in {
            "`static-fact`",
            "static-fact",
            "`needs-real-evidence`",
            "needs-real-evidence",
        }, f"workflow row missing evidence marker: {row}"
    for row in _table_rows("| # | Task kind id"):
        assert "static-fact" in row[-1] or "needs-real-evidence" in row[-1], (
            f"task row missing evidence marker: {row}"
        )


# ── negative cases (must reject) ────────────────────────────────────────


def test_negative_nonexistent_b_product_id_is_rejected() -> None:
    import copy

    import pytest

    # (a) a B product id that nobody publishes: dangling precondition target.
    doc = copy.deepcopy(load_ledger())
    doc["rows"][0]["preconditions"]["and"].append("B99:nonexistent-product")
    with pytest.raises(AssertionError, match=re.escape("B99:nonexistent-product")):
        validate_ledger(doc)

    # (b) the same invented id used as a producer endpoint.
    doc = copy.deepcopy(load_ledger())
    doc["rows"][0]["producer"] = "B99"
    with pytest.raises(AssertionError, match=re.escape("B99")):
        validate_ledger(doc)


def test_negative_missing_package_data_path_is_rejected() -> None:
    import copy

    import pytest

    summary = copy.deepcopy(load_packaging_summary())
    summary["packages"].append(
        {
            "path": "src/cccp/analysis/nmr/model_assets/missing-package-data.bin",
            "kind": "package-data",
            "owner": "injected",
            "ships_in": "cccp",
            "evidence": "static-fact",
        }
    )
    with pytest.raises(AssertionError, match="missing-package-data.bin"):
        validate_packaging_summary(summary)
