"""M0-01 coverage gate: scope, G01–G26, tasks, checkpoints and H rows.

Frozen vectors are independent expectations copied from
``reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md`` §0.2/§10; they are
never recomputed from the artifacts under test.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = REPO_ROOT / "scripts/acceptance/validate_acp2_gap_coverage.py"
BUILDER = REPO_ROOT / "scripts/acceptance/build_acp2_plan_graph.py"
PLAN = REPO_ROOT / "reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md"
RELEASE = REPO_ROOT / "docs/reference/next-change/acp2-contracts/release"
COVERAGE = RELEASE / "evidence-coverage.current.json"
SCOPE = RELEASE / "scope.json"
MANIFEST = REPO_ROOT / "docs/reference/next-change/acp2-contracts/manifest.json"

EXPECTED_SCOPE_ITEMS = {
    "IN_SCOPE": (
        "G01–G26 整改",
        "P0–P2 研究/科学闭环",
        "P3 纯分析插件与 Schema form",
        "H6 多用户/跨领域/迁移",
        "独立安装与证据移交",
    ),
    "DEFERRED": (
        "worker-hosted 第三方 COMPUTE",
        "CDXML",
        "累计 core-hours/token 硬强制",
    ),
    "OUT_OF_SCOPE": (
        "插件商城/GUI",
        "通用 JSON Schema 求值器",
        "2PC",
        "第二 QC 引擎",
        "公共 npm 发布",
        "生产部署/重启",
        "默认全库 lint 清理",
    ),
}

EXPECTED_GAP_VECTORS = {
    "G01": (("H2", "H7"), ("M0-02", "M0-08", "M5-03")),
    "G05": (("H2", "H4", "H7"), ("M0-09", "M0-13", "M3-01", "M3-02", "M3-03", "M3-04", "M4-02")),
    "G08": (("H4",), ("M1-03", "M3-04", "M4-01", "M4-04")),
    "G15": (("H4",), ("M1-05", "M2-04", "M4-01", "M4-04")),
    "G18": (
        ("H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7"),
        (
            "M0-03",
            "M0-10",
            "M1-05",
            "M4-01",
            "M4-02",
            "M4-03",
            "M4-04",
            "M4-05",
            "M4-06",
            "M5-02",
            "M5-03",
        ),
    ),
    "G20": (
        ("H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7"),
        (
            "M0-02",
            "M0-11",
            "M0-12",
            "M1-01",
            "M1-02",
            "M1-03",
            "M1-04",
            "M1-05",
            "M1-06",
            "M4-01",
        ),
    ),
}

EXPECTED_CHECKPOINT_OWNERS = {
    "M0-01a": ("M0-01", "INT"),
    "M0-01b": ("M0-01", "B"),
    "M0-02a": ("M0-02", "INT"),
    "M0-02b": ("M0-02", "INT + B + C-RPH"),
    "M0-02c": ("M0-02", "INT"),
    "M0-03a": ("M0-03", "B"),
    "M0-03b": ("M0-03", "C-RPH"),
    "M0-03c": ("M0-03", "INT/REL"),
    "M1-01a": ("M1-01", "B"),
    "M1-01b": ("M1-01", "B"),
    "M1-01c": ("M1-01", "B"),
    "M1-04a": ("M1-04", "B + A"),
    "M1-04b": ("M1-04", "B + A"),
    "M1-04c": ("M1-04", "B + A"),
    "M2-01a": ("M2-01", "B"),
    "M2-01b": ("M2-01", "B"),
    "M2-01c": ("M2-01", "B"),
    "M2-03a": ("M2-03", "B"),
    "M2-03b": ("M2-03", "B"),
    "M2-03c": ("M2-03", "B"),
    "M2-04a": ("M2-04", "B"),
    "M2-04b": ("M2-04", "B + A"),
    "M2-04c": ("M2-04", "A/B3"),
    "M2-04d": ("M2-04", "B + A"),
    "M3-01a": ("M3-01", "C-RPH"),
    "M3-01b": ("M3-01", "C-RPH"),
    "M3-02a": ("M3-02", "C-RPH"),
    "M3-02b": ("M3-02", "C-RPH"),
    "M3-05a": ("M3-05", "C-Web"),
    "M3-05b": ("M3-05", "C-Web + B"),
    "M4-06a": ("M4-06", "B + INT"),
    "M4-06b": ("M4-06", "A + B + C + INT"),
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def planner():
    return _load("acp2_plan_graph_for_coverage_tests", BUILDER)


@pytest.fixture(scope="module")
def validator():
    return _load("acp2_gap_coverage_validator_tests", VALIDATOR)


@pytest.fixture(scope="module")
def model(planner):
    return planner.load_model(PLAN)


@pytest.fixture()
def coverage() -> dict:
    return json.loads(COVERAGE.read_text(encoding="utf-8"))


@pytest.fixture()
def scope() -> dict:
    return json.loads(SCOPE.read_text(encoding="utf-8"))


def test_full_ledger_validates(validator) -> None:
    summary = validator.run(REPO_ROOT)
    assert summary.startswith("VALID:")
    assert "39 tasks / 32 checkpoints / 26 gaps / 8 H rows" in summary
    assert "full release NOT_VERIFIED" in summary


def test_validator_cli_exits_zero() -> None:
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("VALID:")


def test_nothing_is_closed_and_nothing_claims_pass(coverage) -> None:
    for row in [*coverage["tasks"], *coverage["checkpoints"], *coverage["gaps"]]:
        assert row["status"] == "OPEN", row["id"]
        assert row["result"] == "NOT_VERIFIED", row["id"]
        assert row["receipt"] is None, row["id"]
    for row in coverage["h_rows"]:
        assert row["result"] == "NOT_VERIFIED", row["id"]
    assert coverage["summary"]["full_release_status"] == "NOT_VERIFIED"
    assert coverage["candidate_full"]["allowed"] is False
    assert coverage["candidate_full"]["blocking_rows"] == [
        f"G{index:02d}" for index in range(1, 27)
    ]


def test_every_gap_row_matches_the_frozen_section_10_vectors(coverage) -> None:
    gaps = {row["id"]: row for row in coverage["gaps"]}
    assert tuple(sorted(gaps)) == tuple(f"G{index:02d}" for index in range(1, 27))
    for gap_id, (h_rows, tasks) in EXPECTED_GAP_VECTORS.items():
        assert tuple(gaps[gap_id]["h_rows"]) == h_rows, gap_id
        assert tuple(gaps[gap_id]["tasks"]) == tasks, gap_id
        assert gaps[gap_id]["scope"] == "IN_SCOPE"
        assert gaps[gap_id]["closing_evidence"]
    assert gaps["G02"]["conditional_tasks"] == ["M0-13"]
    assert gaps["G26"]["h_rows"] == ["H4", "H6"]


def test_tasks_and_checkpoints_cover_every_plan_item(coverage) -> None:
    tasks = {row["id"]: row for row in coverage["tasks"]}
    assert len(tasks) == 39
    assert tasks["M0-13"]["scope"] == "NOT_APPLICABLE"
    assert all(row["scope"] == "IN_SCOPE" for key, row in tasks.items() if key != "M0-13")
    assert tasks["M2-04"]["size"] == "XL"
    assert tasks["M0-01"]["owner"].startswith("INT")
    checkpoints = {row["id"]: row for row in coverage["checkpoints"]}
    assert set(checkpoints) == set(EXPECTED_CHECKPOINT_OWNERS)
    for checkpoint_id, (parent, owner) in EXPECTED_CHECKPOINT_OWNERS.items():
        assert checkpoints[checkpoint_id]["parent"] == parent
        assert checkpoints[checkpoint_id]["owner"] == owner


def test_h_rows_mirror_the_h7_handoff_without_upgrading_it(coverage) -> None:
    h_rows = {row["id"]: row for row in coverage["h_rows"]}
    assert tuple(sorted(h_rows)) == tuple(f"H{index}" for index in range(8))
    assert {key: row["science_state"] for key, row in h_rows.items()} == {
        "H0": "PASS",
        "H1": "PASS",
        "H2": "PASS",
        "H3": "NOT_VERIFIED",
        "H4": "NOT_VERIFIED",
        "H5": "PASS",
        "H6": "NOT_VERIFIED",
        "H7": "NOT_VERIFIED",
    }
    assert h_rows["H3"]["result"] == "NOT_VERIFIED"
    assert coverage["h_index"]["handoff_h7"]["release_ready"] is False
    assert coverage["h_index"]["handoff_h7"]["handoff_id"] == "H7"
    assert coverage["source"]["h7_handoff"].endswith("science/handoff-H7.json")


def test_scope_capabilities_match_the_section_0_2_table(scope) -> None:
    assert scope["schema"] == "acp2.release.scope.v1"
    assert scope["task_id"] == "M0-01"
    assert scope["owner"] == "B"
    by_class: dict[str, list[str]] = {}
    for capability in scope["capabilities"]:
        by_class.setdefault(capability["class"], []).append(capability["statement"])
    assert tuple(by_class["IN_SCOPE"]) == EXPECTED_SCOPE_ITEMS["IN_SCOPE"]
    assert tuple(by_class["DEFERRED"]) == EXPECTED_SCOPE_ITEMS["DEFERRED"]
    assert tuple(by_class["OUT_OF_SCOPE"]) == EXPECTED_SCOPE_ITEMS["OUT_OF_SCOPE"]
    for capability in scope["capabilities"]:
        if capability["class"] == "IN_SCOPE":
            assert capability["required"] is True
        else:
            assert capability["required"] is False
            assert capability["entry_condition"]
            assert capability["release_statement"]
    gate = scope["candidate_full_gate"]
    assert gate["allowed"] is False
    assert gate["deferred_counts_as_delivered"] is False
    assert len(gate["required_rows"]) == 5
    assert len(gate["deferred_rows"]) == 3
    assert len(gate["out_of_scope_rows"]) == 7


def test_scope_gap_index_covers_every_gap_as_required(scope) -> None:
    index = scope["gap_index"]
    assert tuple(sorted(index)) == tuple(f"G{index_:02d}" for index_ in range(1, 27))
    for gap_id, row in index.items():
        assert row["scope"] == "IN_SCOPE", gap_id
        assert row["required"] is True, gap_id
        assert row["capabilities"], gap_id
    assert scope["conditional_tasks"][0]["id"] == "M0-13"
    assert scope["conditional_tasks"][0]["scope"] == "NOT_APPLICABLE"


def test_release_contracts_are_registered_as_planned_and_owned_by_b() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    contracts = {entry["id"]: entry for entry in manifest["contracts"]}
    for contract_id in (
        "release-scope",
        "release-evidence-coverage-schema",
        "release-bom-schema",
    ):
        entry = contracts[contract_id]
        assert entry["status"] == "planned"
        assert entry["owner"] == "B"
        assert "sha256" not in entry
        assert (REPO_ROOT / entry["path"]).is_file()
    # M0-12 (2026-10-27): the decision-freeze registration bumps the manifest
    # to 1.5.0 and appends the M0-12 history entry; the release namespace above
    # stays planned/owner-B until the M5-02 release stage.
    # M1-01/M1-02: contract unification + research-session -> 1.6.0 / M1-02.
    assert manifest["contract_version"] == "1.6.0"
    assert manifest["contract_version_history"][-1]["stage"] == "M1-02"
    release_files = {path.name for path in RELEASE.iterdir() if path.is_file()}
    # M5-01 adds the generated BOM instance (schema registered as
    # release-bom-schema; the instance itself is M5-02's registration).
    assert release_files == {
        "scope.json",
        "evidence-coverage.schema.json",
        "evidence-coverage.current.json",
        "bom.schema.json",
        "bom.current.json",
    }


# ---------------------------------------------------------------------------
# negative cases: the gate must fail closed
# ---------------------------------------------------------------------------


def test_defective_coverage_rows_are_rejected(validator, coverage, scope, model) -> None:
    def reject(mutator, match: str) -> None:
        mutated = copy.deepcopy(coverage)
        mutator(mutated)
        with pytest.raises(validator.CoverageError, match=match):
            validator.validate_coverage_against_plan(REPO_ROOT, mutated, model)

    reject(lambda data: data["tasks"].pop(), "must cover exactly the plan tasks")
    reject(
        lambda data: data["tasks"][0].__setitem__("owner", "REL"),
        "owner drifts from the plan",
    )
    reject(
        lambda data: data["tasks"][0].__setitem__("size", "XL"),
        "size drifts from the plan",
    )
    reject(lambda data: data["checkpoints"].pop(), "cover every declared sub-checkpoint")
    reject(
        lambda data: data["gaps"][0].__setitem__("tasks", ["M5-03"]),
        "closing tasks",
    )

    def deferred_pass(data: dict) -> None:
        row = data["gaps"][0]
        row["scope"] = "DEFERRED"
        row["result"] = "PASS"
        row["status"] = "CLOSED"

    reject(deferred_pass, "DEFERRED and may never claim PASS")

    def close_task(data: dict) -> None:
        data["tasks"][0]["status"] = "CLOSED"
        data["tasks"][0]["result"] = "PASS"

    reject(close_task, "is CLOSED while gap")

    def h_mapping(data: dict) -> None:
        data["h_rows"][0]["gaps"] = ["G01"]

    reject(h_mapping, "section 10 G→H mapping")

    drifted = copy.deepcopy(coverage)
    drifted["gaps"][0]["scope"] = "DEFERRED"
    with pytest.raises(validator.CoverageError, match="disagrees with scope.gap_index"):
        validator.validate_scope_coverage_alignment(drifted, scope)


def test_scope_defects_are_rejected(validator, scope, model) -> None:
    def reject(mutator, match: str) -> None:
        mutated = copy.deepcopy(scope)
        mutator(mutated)
        with pytest.raises(validator.CoverageError, match=match):
            validator.validate_scope(REPO_ROOT, mutated, model)

    reject(lambda data: data["capabilities"].pop(0), "not covered by scope.json")

    def deferred_claims_pass(data: dict) -> None:
        capability = next(row for row in data["capabilities"] if row["class"] == "DEFERRED")
        capability["release_statement"] = "PASS"

    reject(deferred_claims_pass, "may not claim PASS")

    def required_in_scope_dropped(data: dict) -> None:
        capability = next(row for row in data["capabilities"] if row["class"] == "IN_SCOPE")
        capability["required"] = False

    reject(required_in_scope_dropped, "IN_SCOPE but not required")
    reject(lambda data: data["gap_index"].pop("G26"), "exactly G01–G26")
    reject(
        lambda data: data["candidate_full_gate"]["required_rows"].pop(),
        "required_rows",
    )
    reject(
        lambda data: data["candidate_full_gate"].__setitem__("deferred_counts_as_delivered", True),
        "must stay false",
    )


def test_coverage_self_digest_and_source_links_are_enforced(validator, coverage, model) -> None:
    def reject(mutator, match: str) -> None:
        mutated = copy.deepcopy(coverage)
        mutator(mutated)
        with pytest.raises(validator.CoverageError, match=match):
            validator.validate_source(REPO_ROOT, mutated, model)

    reject(lambda data: data["tasks"][0].__setitem__("title", "edited"), "coverage_sha256")
    reject(lambda data: data["source"].__setitem__("plan_sha256", "0" * 64), "plan_sha256")
    assert validator.canonical_digest(coverage) == coverage["coverage_sha256"]


def test_release_registration_is_checked(validator) -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    validator.validate_release_registration(REPO_ROOT, manifest, RELEASE)

    delivered = copy.deepcopy(manifest)
    next(row for row in delivered["contracts"] if row["id"] == "release-scope")["status"] = (
        "delivered"
    )
    with pytest.raises(validator.CoverageError, match="must stay status=planned"):
        validator.validate_release_registration(REPO_ROOT, delivered, RELEASE)

    missing = copy.deepcopy(manifest)
    missing["contracts"] = [
        row for row in missing["contracts"] if row["id"] != "release-bom-schema"
    ]
    with pytest.raises(validator.CoverageError, match="not registered"):
        validator.validate_release_registration(REPO_ROOT, missing, RELEASE)


def test_bom_schema_keeps_the_m5_02_anchor_fields(validator) -> None:
    schema = json.loads((RELEASE / "bom.schema.json").read_text(encoding="utf-8"))
    validator.validate_bom_schema(schema)
    stripped = copy.deepcopy(schema)
    stripped["required"] = [name for name in stripped["required"] if name != "artifacts"]
    with pytest.raises(validator.CoverageError, match="M5-02 required fields"):
        validator.validate_bom_schema(stripped)


def test_coverage_schema_rejects_an_unknown_field(validator, coverage) -> None:
    schema = json.loads((RELEASE / "evidence-coverage.schema.json").read_text(encoding="utf-8"))
    assert validator.validate_schema(coverage, schema, schema, "") == []
    mutated = copy.deepcopy(coverage)
    mutated["tasks"][0]["invented"] = True
    errors = validator.validate_schema(mutated, schema, schema, "")
    assert errors and "unexpected property 'invented'" in errors[0]
