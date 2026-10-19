"""Doc-consistency checker for the ACP Services closeout (plan todo 70 / B13-5).

Consumer-only: it reads the docs/ledgers that todo 70 syncs and asserts they
stay mutually consistent with the implementation and with the layered closure
record. No product code is modified.

Checks:

1. every synced ``ACP_Services_*`` doc carries the todo-70 status block
   (``delivered`` / ``verified`` / ``NOT_VERIFIED`` / ``limits``) and points at
   the closure record; the standalone recipe and the closure record are **not**
   injected with the block;
2. path-like references inside the updated docs resolve to real files;
3. symbols named by the docs exist, and the old ``acp`` import paths are the
   **same objects** as their ``acp_services`` roots (identity forwarders);
4. the migration ledger keeps exactly six columns, every data row carries a
   status token in the exit-condition column, and the summary counts match;
5. closure-record verdicts are not contradicted: ``F-3`` stays FAIL, the
   frontend/science/LSF/production rows stay ``NOT_VERIFIED``, and no doc claims
   real science / production release / full envelope coverage;
6. the public shim inventory lists the retained surfaces with callers and every
   referenced path exists;
7. the recipe and the closure record contain no todo-70 markers.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

STATUS_MARKER = "<!-- SERVICES_STATUS_SYNC:todo70 -->"
SHIM_MARKER = "<!-- SERVICES_SHIM_INVENTORY:todo70 -->"

CLOSURE = REPO_ROOT / "docs" / "reference" / "current" / "ACP_Services_Closure_20261019.md"
RECIPE = (
    REPO_ROOT / "docs" / "reference" / "next-change" / "ACP_Services_Standalone_Recipe.md"
)
MASTER_PLAN = REPO_ROOT / "docs" / "ACP_Architecture_Adjustment_Plan_20261016.md"
DEPLOY_MANUAL = REPO_ROOT / "docs" / "reference" / "current" / "ACP_Function_Test_Manual.md"
LEDGER = REPO_ROOT / "tests" / "baseline" / "services-evidence" / "migration_ledger.md"
SHIM_DOC = (
    REPO_ROOT / "tests" / "baseline" / "services-evidence" / "public_shim_inventory.md"
)
SERVICE_AGENTS = REPO_ROOT / "src" / "acp_services" / "AGENTS.md"
ROOT_AGENTS = REPO_ROOT / "AGENTS.md"
ACP_AGENTS = REPO_ROOT / "src" / "acp" / "AGENTS.md"
SCHEDULER_AGENTS = REPO_ROOT / "src" / "acp" / "scheduler" / "AGENTS.md"
API_AGENTS = REPO_ROOT / "src" / "acp" / "api" / "AGENTS.md"

_STATUS_DOCS = [
    "ACP_Services_Component_Manifest.md",
    "ACP_Services_Diagnostics_Contract.md",
    "ACP_Services_Freshness_Contract.md",
    "ACP_Services_Identity_Support_Scope.md",
    "ACP_Services_MCP_Client_Matrix.md",
    "ACP_Services_Metering_Policy.md",
]
SKIPPED_PINNED_DOCS = [
    "ACP_Services_First_Slice_Contracts.md",  # digest-pinned by closure record
    "ACP_Services_Events_A04_Contract.md",  # digest-pinned by closure record
]
STATUS_DOCS = [REPO_ROOT / "docs" / "reference" / "next-change" / name for name in _STATUS_DOCS]
PINNED_DOCS = [
    REPO_ROOT / "docs" / "reference" / "next-change" / name for name in SKIPPED_PINNED_DOCS
]

_PATH_RE = re.compile(r"((?:src|tests|docs|scripts)/[A-Za-z0-9_./-]+\.(?:py|md|json|sh))")
_STATUS_RE = re.compile(r"^\[(CLOSED|RETAINED|OPEN|DEFERRED)\]")
_SRC_RE = re.compile(r"src/[A-Za-z0-9_./-]+\.py")
_FORBIDDEN_CLAIMS = (
    "真实科学 PASS",
    "生产已发布",
    "所有服务验证通过",
    "全部验证通过",
    "15/15 envelope 全部启用",
)


# ── generic markdown table parsing ──────────────────────────────────────────


def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def _is_separator(cells: tuple[str, ...]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def _find_table(text: str, first_header: str) -> list[tuple[str, ...]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        header = _cells(stripped)
        if not header or header[0] != first_header:
            continue
        if index + 1 >= len(lines) or not lines[index + 1].strip().startswith("|"):
            continue
        if not _is_separator(_cells(lines[index + 1])):
            continue
        rows: list[tuple[str, ...]] = []
        for follow in lines[index + 2 :]:
            if not follow.strip().startswith("|"):
                break
            row = _cells(follow)
            if _is_separator(row):
                continue
            rows.append(row)
        return rows
    return []


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_paths_exist(label: str, text: str) -> None:
    missing = []
    for token in sorted(set(_PATH_RE.findall(text))):
        if not (REPO_ROOT / token).exists():
            missing.append(token)
    assert not missing, f"{label}: referenced paths missing: {missing}"


def _assert_services_line_paths_exist(label: str, text: str, keywords: tuple[str, ...]) -> None:
    """Check paths only on lines that are part of the todo-70 services sync.

    Older sections in the same file may reference files renamed by unrelated
    work; those are out of this checker's scope.
    """
    missing = []
    for line in text.splitlines():
        if not any(keyword in line for keyword in keywords):
            continue
        for token in _PATH_RE.findall(line):
            if not (REPO_ROOT / token).exists():
                missing.append(token)
    assert not missing, f"{label}: services-line paths missing: {sorted(set(missing))}"


# ── 1. status blocks ────────────────────────────────────────────────────────


def test_status_sync_blocks_present_and_tokenized() -> None:
    for path in STATUS_DOCS:
        text = _text(path)
        assert text.count(STATUS_MARKER) == 1, f"{path.name}: expected exactly one status marker"
        for token in ("delivered", "verified", "NOT_VERIFIED", "limits"):
            assert token in text, f"{path.name}: status block missing {token!r}"
        assert "ACP_Services_Closure_20261019.md" in text, (
            f"{path.name}: status block must point at the layered closure record"
        )


def test_recipe_and_closure_have_no_status_marker() -> None:
    for path in (RECIPE, CLOSURE, *PINNED_DOCS):
        text = _text(path)
        assert STATUS_MARKER not in text, f"{path.name}: must not be injected by todo 70"
        assert SHIM_MARKER not in text, f"{path.name}: must not carry the shim marker"
    assert RECIPE.exists() and CLOSURE.exists()


# ── 2. referenced paths ─────────────────────────────────────────────────────


def test_updated_docs_reference_existing_paths() -> None:
    _assert_paths_exist("src/acp_services/AGENTS.md", _text(SERVICE_AGENTS))
    _assert_paths_exist("public_shim_inventory.md", _text(SHIM_DOC))
    for path in STATUS_DOCS:
        _assert_paths_exist(path.name, _text(path))
    for path in (ACP_AGENTS, SCHEDULER_AGENTS, API_AGENTS):
        _assert_paths_exist(path.name, _text(path))
    _assert_services_line_paths_exist(
        "root AGENTS.md",
        _text(ROOT_AGENTS),
        (
            "acp_services",
            "Services",
            "services-evidence",
            "worker_bootstrap",
            "public_shim_inventory",
        ),
    )
    deploy = _text(DEPLOY_MANUAL)
    start = deploy.find("#### 1.5.1")
    assert start != -1, "deployment manual must carry the §1.5.1 services sync"
    rest = deploy[start:]
    next_heading = re.search(r"\n#{2,4} ", rest[10:])
    deploy_section = rest[: 10 + next_heading.start()] if next_heading else rest
    _assert_paths_exist("ACP_Function_Test_Manual.md §1.5.1", deploy_section)
    # Master plan: only the todo-70 record is under this checker's scope
    # (older sections legitimately mention files renamed before this plan).
    plan = _text(MASTER_PLAN)
    assert "## 45. ACP Services 实施记录（2026-10-19）" in plan
    section_45 = plan.split("## 45. ACP Services 实施记录（2026-10-19）", 1)[1]
    _assert_paths_exist("master plan §45", section_45)


# ── 3. symbols exist and forwarders are identity ────────────────────────────


_SERVICE_SYMBOLS = {
    "acp_services.bootstrap": ("ServicesPorts",),
    "acp_services.worker_bootstrap": ("main",),
    "acp_services.store.store": ("JobStore", "JobStateConflictError"),
    "acp_services.store.migrations": ("migrate", "get_schema_version"),
    "acp_services.data.operations": ("PrepareOperationService",),
    "acp_services.data.namespaces": ("NamespaceStore", "EnsureNamespaceRequest"),
    "acp_services.data.transfer": ("TransferPipeline",),
    "acp_services.runtime.provisioning": ("ProvisioningService",),
    "acp_services.runtime.admission": ("AdmissionController",),
    "acp_services.runtime.commands": ("RuntimeCommands",),
    "acp_services.runtime.execution_chain": ("select_execution_entry",),
    "acp_services.runtime.plan_contracts": ("CalculationPlan",),
    "acp_services.runtime.step_requirements": ("evaluate_prerequisite",),
    "acp_services.runtime.usage": ("UsageRecord",),
    "acp_services.runtime.quota": ("QuotaService",),
    "acp_services.workspace.admin": ("AdminService", "AdminAuthorizer"),
}

_IDENTITY_FORWARDERS = (
    ("acp.scheduler.store", "JobStore", "acp_services.store.store", "JobStore"),
    ("acp.scheduler.migrations", "migrate", "acp_services.store.migrations", "migrate"),
    (
        "acp.calculations.contracts",
        "CalculationPlan",
        "acp_services.runtime.plan_contracts",
        "CalculationPlan",
    ),
    (
        "acp.calculations.step_result",
        "portable_path",
        "acp_services.runtime.step_result",
        "portable_path",
    ),
    (
        "acp.calculations.step_requirements",
        "evaluate_prerequisite",
        "acp_services.runtime.step_requirements",
        "evaluate_prerequisite",
    ),
    (
        "acp.catalog",
        "method_levels_to_cli_flags",
        "acp_services.runtime.method_flags",
        "method_levels_to_cli_flags",
    ),
)


def test_documented_services_symbols_exist() -> None:
    for module_name, symbols in _SERVICE_SYMBOLS.items():
        module = importlib.import_module(module_name)
        for symbol in symbols:
            assert hasattr(module, symbol), f"{module_name}.{symbol} missing"


def test_old_paths_are_identity_forwarders() -> None:
    for old_mod, old_sym, new_mod, new_sym in _IDENTITY_FORWARDERS:
        old = getattr(importlib.import_module(old_mod), old_sym)
        new = getattr(importlib.import_module(new_mod), new_sym)
        assert old is new, f"{old_mod}.{old_sym} must be {new_mod}.{new_sym} (identity)"


# ── 4. migration ledger ─────────────────────────────────────────────────────


def test_migration_ledger_six_columns_and_status_tokens() -> None:
    rows = _find_table(_text(LEDGER), "能力")
    assert len(rows) >= 50, f"ledger has {len(rows)} rows — expected >= 50"
    counts = {"CLOSED": 0, "RETAINED": 0, "OPEN": 0, "DEFERRED": 0}
    for row in rows:
        assert len(row) == 6, f"ledger row has {len(row)} columns: {row}"
        assert _SRC_RE.search(row[1]), f"ledger row missing src/...py station: {row}"
        match = _STATUS_RE.match(row[4])
        assert match, f"ledger exit-condition cell missing status token: {row[4][:40]!r}"
        counts[match.group(1)] += 1
    assert counts["CLOSED"] >= 30, f"too few CLOSED rows: {counts}"
    assert counts["RETAINED"] >= 4, f"too few RETAINED rows: {counts}"
    assert counts["OPEN"] >= 5, f"too few OPEN rows: {counts}"

    text = _text(LEDGER)
    for token, value in counts.items():
        match = re.search(rf"\[{token}\][`]*\s*(\d+)", text)
        assert match, f"ledger summary missing count for {token}"
        assert int(match.group(1)) == value, (
            f"ledger summary {token}={match.group(1)} != parsed {value}"
        )


# ── 5. closure-record consistency ───────────────────────────────────────────


def _closure_record() -> dict:
    text = _text(CLOSURE)
    begin = "<!-- SERVICES_CLOSURE_RECORD_BEGIN -->"
    end = "<!-- SERVICES_CLOSURE_RECORD_END -->"
    assert begin in text and end in text, "closure machine block markers missing"
    body = text.split(begin, 1)[1].split(end, 1)[0]
    match = re.search(r"```json\s*(.*?)```", body, re.DOTALL)
    assert match, "closure json block missing"
    return json.loads(match.group(1))


def test_closure_statuses_not_contradicted() -> None:
    record = _closure_record()
    statuses: dict[str, str] = {}
    for layer in record["layers"].values():
        for row in layer["rows"]:
            statuses[row["id"]] = row["status"]

    assert statuses["L1-03"] == "FAIL", "F-3 row must stay FAIL"
    for row_id in ("L2-11", "L2-12", "L2-14", "L2-15", "L3-05", "L3-06", "L3-07"):
        assert statuses.get(row_id) == "NOT_VERIFIED", f"{row_id} must stay NOT_VERIFIED"
    for gate, info in record["environment_gates"].items():
        assert info["status"] == "NOT_VERIFIED", f"gate {gate} must stay NOT_VERIFIED"

    # Every synced doc that maps to a NOT_VERIFIED area says so explicitly.
    for path in STATUS_DOCS:
        assert "NOT_VERIFIED" in _text(path), f"{path.name} must state NOT_VERIFIED limits"
    a04_doc = REPO_ROOT / "docs" / "reference" / "next-change" / "ACP_Services_Events_A04_Contract.md"
    assert "NOT_VERIFIED" in _text(a04_doc), "A04 doc must state NOT_VERIFIED"
    assert record["binding"]["contract_versions"]["a04_status"] == "DEFERRED"

    # The implementation record mirrors the F-3 failure honestly.
    section_45 = _text(MASTER_PLAN).split("## 45. ACP Services 实施记录（2026-10-19）", 1)[1]
    assert "F-3" in section_45 and "fail" in section_45.lower()
    assert "未放宽" in section_45 or "未删除护栏" in section_45

    # No doc may claim the deferred gates were reached.
    for path in (MASTER_PLAN, ROOT_AGENTS, *STATUS_DOCS, SERVICE_AGENTS):
        text = _text(path)
        for phrase in _FORBIDDEN_CLAIMS:
            assert phrase not in text, f"{path.name}: forbidden claim {phrase!r}"


# ── 6. public shim inventory ────────────────────────────────────────────────


def test_public_shim_inventory_lists_retained_surfaces() -> None:
    text = _text(SHIM_DOC)
    assert SHIM_MARKER in text
    rows = _find_table(text, "公开面 (shim)")
    assert len(rows) >= 8, f"shim inventory has {len(rows)} rows — expected >= 8"
    for row in rows:
        assert len(row) == 5, f"shim row has {len(row)} columns: {row}"
        assert "[RETAINED]" in row[3], f"shim row must be RETAINED: {row[0]}"
        assert row[2].strip(), f"shim row must list callers: {row[0]}"
    for needle in (
        "acp.calculations.primitives",
        "acp.backends",
        "acp.scheduler.store",
        "acp.scheduler.migrations",
        "acp.compat.legacy",
        "recovery_fixtures",
        "model_assets",
        "NOTICE.md",
        "frontend/js/vendor",
    ):
        assert needle in text, f"shim inventory missing retained surface: {needle}"
    assert "未删除" in text or "not delete" in text.lower() or "不授权删除" in text


# ── 7. AGENTS / master plan structure ───────────────────────────────────────


def test_agents_and_master_plan_document_services() -> None:
    root = _text(ROOT_AGENTS)
    for token in (
        "src/acp_services/",
        "worker_bootstrap.py",
        "acp_services → acp",
        "identity forwarder",
        "唯一实现根",
        "public_shim_inventory.md",
        "test_services_doc_sync.py",
    ):
        assert token in root, f"root AGENTS.md missing services token: {token}"
    assert "NOT_VERIFIED" in root

    assert SERVICE_AGENTS.exists()
    service = _text(SERVICE_AGENTS)
    assert "acp_services → acp" in service or "never import `acp`" in service
    assert "worker_bootstrap" in service

    acp = _text(ACP_AGENTS)
    assert "identity forwarder" in acp
    assert "acp_services.store.store" in acp
    sched = _text(SCHEDULER_AGENTS)
    assert "acp_services.store.store" in sched and "acp_services.store.migrations" in sched
    api = _text(API_AGENTS)
    assert "availability" in api and "prepared/{resource}" in api

    plan = _text(MASTER_PLAN)
    section_45 = plan.split("## 45. ACP Services 实施记录（2026-10-19）", 1)[1]
    for token in ("B40b", "T34b", "W12", "W13", "F0/F1×S0/S1", "NOT_VERIFIED"):
        assert token in section_45, f"master plan §45 missing {token}"
