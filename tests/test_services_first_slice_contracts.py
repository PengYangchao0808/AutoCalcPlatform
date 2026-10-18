"""Checker for B00-3 (todo 3): Services migration ledger, handoff registry,
single-writer map, public allowlist and first-slice contract samples.

This test is a *consumer*: it parses the deliverables under
``tests/baseline/services-evidence/`` and the parallel index §5, and it
consumes the science samples with the real readers
(``acp.storage.manifest.ResultManifest`` and ``acp.results.frames``).

It does NOT import ``acp_services`` (that package does not exist in this todo).
Contract samples are classified by *contract semantics* (see
``docs/reference/next-change/ACP_Services_First_Slice_Contracts.md``), not by
JSON validity — a corrupt sample is valid JSON but must classify as rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_EVIDENCE = _THIS / "baseline" / "services-evidence"
_LEDGER = _EVIDENCE / "migration_ledger.md"
_HANDOFF = _EVIDENCE / "handoff_registry.md"
_SINGLE_WRITER = _EVIDENCE / "single_writer_map.md"
_ALLOWLIST = _EVIDENCE / "public_allowlist.md"
_SAMPLES = _EVIDENCE / "contract_samples"
_INDEX = (
    _THIS.parent
    / "docs"
    / "reference"
    / "next-change"
    / "ACP_Architecture_Progress_and_Parallel_Index_20261018.md"
)
_CONTRACT_DOC = (
    _THIS.parent / "docs" / "reference" / "next-change" / "ACP_Services_First_Slice_Contracts.md"
)

_LEDGER_COLUMNS = ("能力", "当前实现驻点", "兼容入口", "生产消费者", "退出条件", "验收测试")
_HANDOFF_COLUMNS = (
    "owner",
    "可修改范围",
    "provider",
    "consumer",
    "base_commit",
    "prerequisites",
    "acceptance",
    "applied_commit",
    "状态",
)
_HANDOFF_STATUS = {"pending", "delivered", "integrated", "rejected"}
_SRC_RE = __import__("re").compile(r"src/[A-Za-z0-9_./-]+\.py")
_HEX = set("0123456789abcdefABCDEF")


# ── generic markdown table parsing ──────────────────────────────────────────


def _table_cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def _is_separator(cells: tuple[str, ...]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def _find_table(text: str, first_header: str) -> list[tuple[str, ...]]:
    """Return data rows of the first *header* table whose first header cell matches.

    A header is identified by the immediately following separator row, so a
    column-definition table whose data rows start with the same token is not
    mistaken for the target table.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _table_cells(stripped)
        if not cells or cells[0] != first_header:
            continue
        if index + 1 >= len(lines) or not lines[index + 1].strip().startswith("|"):
            continue
        if not _is_separator(_table_cells(lines[index + 1])):
            continue
        rows: list[tuple[str, ...]] = []
        for follow in lines[index + 2 :]:
            if not follow.strip().startswith("|"):
                break
            follow_cells = _table_cells(follow)
            if _is_separator(follow_cells):
                continue
            rows.append(follow_cells)
        return rows
    return []


def _find_header(text: str, first_header: str) -> tuple[str, ...]:
    """Return the header cells of the first table whose first header matches."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _table_cells(stripped)
        if not cells or cells[0] != first_header:
            continue
        if index + 1 < len(lines) and _is_separator(_table_cells(lines[index + 1])):
            return cells
    return ()


def _assert_six_column_ledger(text: str) -> list[tuple[str, ...]]:
    """Validate the six-column ledger shape; raise AssertionError otherwise."""
    rows = _find_table(text, _LEDGER_COLUMNS[0])
    assert rows, "no ledger rows parsed"
    for cells in rows:
        assert len(cells) == len(_LEDGER_COLUMNS), (
            f"ledger row has {len(cells)} columns (expected 6): {cells}"
        )
        assert _SRC_RE.search(cells[1]), (
            f"ledger row has no real src/...py station path: {cells[1]}"
        )
    return rows


# ── (a1) migration ledger ───────────────────────────────────────────────────


def test_migration_ledger_six_columns_and_twenty_rows() -> None:
    text = _LEDGER.read_text(encoding="utf-8")
    header = _find_header(text, _LEDGER_COLUMNS[0])
    assert header == _LEDGER_COLUMNS, f"ledger header mismatch: {header}"
    rows = _assert_six_column_ledger(text)
    assert len(rows) >= 20, f"ledger has {len(rows)} rows; expected >= 20"


def test_migration_ledger_covers_required_capabilities() -> None:
    rows = _assert_six_column_ledger(_LEDGER.read_text(encoding="utf-8"))
    blob = "\n".join(cells[0] + " " + cells[1] for cells in rows)
    for needle in (
        "projects.py",
        "structure_source_store.py",
        "task_views.py",
        "remote/paths.py",
        "remote/release.py",
        "remote/submission.py",
        "result_publication.py",
        "v1_routes.py",
        "init_wizard",
        "catalog.py",
        "storage/manifest.py",
        "frame_candidates.py",
        "remote_structure_cache.py",
        "store.py",
        "manager.py",
        "executor.py",
        "step_requirements.py",
        "script_gen.py",
        "cli.py",
        "events.py",
    ):
        assert needle in blob, f"ledger missing required capability marker: {needle}"


def test_negative_five_column_ledger_row_is_rejected() -> None:
    """A 5-column row must fail the same validation (built in-memory)."""
    bad = (
        "| 能力 | 当前实现驻点 | 兼容入口 | 生产消费者 | 退出条件 | 验收测试 |\n"
        "|---|---|---|---|---|---|\n"
        "| broken | `src/acp/scheduler/store.py:106` | entry | consumer | exit |\n"
    )
    try:
        _assert_six_column_ledger(bad)
    except AssertionError as exc:
        assert "columns (expected 6)" in str(exc)
    else:  # pragma: no cover - would mean the guard is vacuous
        raise AssertionError("five-column ledger row was accepted")


# ── (a2) handoff registry ───────────────────────────────────────────────────


def test_handoff_registry_headers_and_rows() -> None:
    text = _HANDOFF.read_text(encoding="utf-8")
    header = _find_header(text, _HANDOFF_COLUMNS[0])
    assert header == _HANDOFF_COLUMNS, f"handoff header mismatch: {header}"
    rows = _find_table(text, _HANDOFF_COLUMNS[0])
    assert rows, "no handoff registry rows parsed"
    for cells in rows:
        assert len(cells) == len(_HANDOFF_COLUMNS), (
            f"handoff row has {len(cells)} columns (expected 9): {cells}"
        )
        assert cells[-1] in _HANDOFF_STATUS, (
            f"handoff status not in {sorted(_HANDOFF_STATUS)}: {cells[-1]}"
        )


def test_handoff_registry_delivered_and_integrated_are_distinct() -> None:
    rows = _find_table(_HANDOFF.read_text(encoding="utf-8"), _HANDOFF_COLUMNS[0])
    statuses = {cells[-1] for cells in rows}
    assert "delivered" in statuses, "handoff registry must record a delivered patch"
    assert "integrated" in statuses, "handoff registry must record an integrated patch"


# ── (d) single-writer map vs parallel index §5 ──────────────────────────────


def test_single_writer_map_matches_parallel_index_section_five() -> None:
    index_rows = _find_table(_INDEX.read_text(encoding="utf-8"), "文件/契约")
    map_rows = _find_table(_SINGLE_WRITER.read_text(encoding="utf-8"), "文件/契约")
    assert index_rows, "parallel index §5 table not found"
    assert map_rows, "single-writer map table not found"

    index_pairs = [(cells[0], cells[1]) for cells in index_rows]
    map_pairs = [(cells[0], cells[1]) for cells in map_rows]
    assert map_pairs == index_pairs, (
        f"single-writer map diverges from parallel index §5:\nindex={index_pairs}\nmap={map_pairs}"
    )


# ── (b) public allowlist ────────────────────────────────────────────────────


def test_public_allowlist_declares_dependency_direction_and_first_slice() -> None:
    text = _ALLOWLIST.read_text(encoding="utf-8")
    assert "acp → acp_services" in text or "acp -> acp_services" in text
    assert "acp_services → acp" in text or "acp_services -> acp" in text
    assert "TYPE_CHECKING" in text
    assert "只增不删" in text
    for module in (
        "acp_services.contracts",
        "acp_services.workspace.projects",
        "acp_services.data.contracts",
        "acp_services.runtime",
    ):
        assert module in text, f"allowlist missing first-slice module {module}"


def test_contract_doc_has_four_categories_and_x2_matrix() -> None:
    text = _CONTRACT_DOC.read_text(encoding="utf-8")
    for heading in (
        "## 1. Access",
        "## 2. Data prepare/read",
        "## 3. 事件流",
        "## 4. 科学输入输出",
        "## 5. 执行身份",
        "## 6. 发行与资产",
        "X2 六项交付物覆盖矩阵",
    ):
        assert heading in text, f"contract doc missing section: {heading}"
    semantic_tokens = (
        "availability",
        "freshness",
        "basis_revision",
        "deployment_tier",
        "execution_mode",
    )
    for token in semantic_tokens:
        assert token in text, f"contract doc missing semantic token: {token}"


def test_single_writer_map_header_matches_section_five() -> None:
    header = _find_header(_SINGLE_WRITER.read_text(encoding="utf-8"), "文件/契约")
    assert header == ("文件/契约", "唯一写者", "其他两路的参与方式"), header


# ── (c) contract samples: classification by semantics ───────────────────────

_KINDS = ("normal", "empty", "partial", "corrupt", "old-version")
_CONTRACTS = ("access", "data", "events", "science", "execution", "release")


def _hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def classify_access(body: dict) -> str:
    if body.get("legacy"):
        return "accepted_with_migration"
    decision = body.get("decision", {})
    if decision.get("effect") not in ("allow", "deny"):
        return "rejected"
    if "principal_id" not in decision:
        return "rejected"
    for grant in body.get("grants", []):
        if "principal_id" not in grant:
            return "rejected"
        if grant.get("basis_revision", 0) < 0:
            return "rejected"
    if decision.get("effect") == "allow" and "available_actions" not in decision:
        return "accepted_partial"
    return "accepted"


def classify_data(body: dict) -> str:
    if body.get("legacy"):
        return "accepted_with_migration"
    op = body.get("operation", {})
    if op.get("state") not in {"pending", "running", "ready", "failed", "cancelled"}:
        return "rejected"
    if op.get("availability") not in {"absent", "preparing", "ready", "unavailable"}:
        return "rejected"
    if op.get("freshness") not in {"current", "stale", "unknown"}:
        return "rejected"
    if op.get("sha256") is not None and not _hex64(op.get("sha256")):
        return "rejected"
    if op.get("basis_revision", 0) < 0 or op.get("expected_revision", 0) < 0:
        return "rejected"
    if op.get("state") == "pending" and op.get("availability") == "absent":
        return "accepted"
    if op.get("availability") == "ready" and op.get("freshness") == "stale":
        return "accepted_partial"
    return "accepted"


def classify_events(body: dict) -> str:
    if body.get("legacy"):
        return "accepted_with_migration"
    events = body.get("events")
    if not isinstance(events, list):
        return "rejected"
    last = None
    for event in events:
        if "owner_id" not in event:
            return "rejected"
        if event.get("scope") not in {"task", "project", "data"}:
            return "rejected"
        seq = event.get("seq")
        if not isinstance(seq, int):
            return "rejected"
        if last is not None and seq <= last:
            return "rejected"
        last = seq
    if body.get("partial"):
        return "accepted_partial"
    return "accepted"


def classify_science(body: dict) -> str:
    manifest = body.get("manifest")
    if not isinstance(manifest, dict):
        return "rejected"
    version = manifest.get("version")
    if not isinstance(version, int):
        return "rejected"
    if not isinstance(manifest.get("products"), list):
        return "rejected"
    if version < 2:
        return "accepted_with_migration"
    if manifest.get("status") == "partial" or manifest.get("complete") is False:
        return "accepted_partial"
    return "accepted"


def classify_execution(body: dict) -> str:
    envelope = body.get("envelope")
    if not isinstance(envelope, dict):
        return "rejected"
    if "task_context" in envelope:
        return "rejected"
    attempt = envelope.get("attempt")
    if not isinstance(attempt, int) or attempt < 1:
        return "rejected"
    version = envelope.get("envelope_version", 1)
    if not isinstance(version, int):
        return "rejected"
    if version < 1:
        return "accepted_with_migration"
    if envelope.get("resource_budget", {}).get("partial") is True:
        return "accepted_partial"
    return "accepted"


def classify_release(body: dict) -> str:
    release = body.get("release")
    if not isinstance(release, dict):
        return "rejected"
    version = release.get("manifest_version")
    if not isinstance(version, int):
        return "rejected"
    files = release.get("files")
    if not isinstance(files, list):
        return "rejected"
    for entry in files:
        if not _hex64(entry.get("sha256")):
            return "rejected"
    if version < 1:
        return "accepted_with_migration"
    if release.get("wheel_sha256") is None:
        return "accepted_partial"
    return "accepted"


_CLASSIFIERS = {
    "access": classify_access,
    "data": classify_data,
    "events": classify_events,
    "science": classify_science,
    "execution": classify_execution,
    "release": classify_release,
}


def _load_samples() -> list[dict]:
    samples = []
    for path in sorted(_SAMPLES.glob("*.json")):
        if path.name == "samples_manifest.json":
            continue
        # Every sample MUST be valid JSON (corrupt samples are valid JSON too).
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_path"] = path
        samples.append(payload)
    return samples


def test_contract_samples_cover_all_groups_and_kinds() -> None:
    manifest = json.loads((_SAMPLES / "samples_manifest.json").read_text(encoding="utf-8"))
    assert manifest["sample_version"] == 1
    assert set(manifest["groups"]) == set(_CONTRACTS)
    for contract, kinds in manifest["groups"].items():
        assert tuple(kinds) == _KINDS, f"{contract} kinds diverge: {kinds}"
    present = {(s["contract"], s["sample_kind"]) for s in _load_samples()}
    for contract in _CONTRACTS:
        for kind in _KINDS:
            assert (contract, kind) in present, f"missing sample {contract}/{kind}"


def test_contract_samples_classified_by_contract_semantics() -> None:
    for sample in _load_samples():
        contract = sample["contract"]
        classifier = _CLASSIFIERS[contract]
        actual = classifier(sample["body"])
        assert actual == sample["expected_classification"], (
            f"{sample['_path'].name}: classified {actual!r}, "
            f"expected {sample['expected_classification']!r}"
        )


def test_corrupt_samples_are_valid_json_but_rejected() -> None:
    seen = 0
    for sample in _load_samples():
        if sample["sample_kind"] != "corrupt":
            continue
        seen += 1
        # reaching here proves the file parsed as JSON
        assert _CLASSIFIERS[sample["contract"]](sample["body"]) == "rejected"
    assert seen == len(_CONTRACTS), f"expected {len(_CONTRACTS)} corrupt samples, got {seen}"


def test_old_version_samples_accepted_with_migration() -> None:
    for sample in _load_samples():
        if sample["sample_kind"] == "old-version":
            assert _CLASSIFIERS[sample["contract"]](sample["body"]) == ("accepted_with_migration")


def test_science_samples_consumed_by_real_reader() -> None:
    """The science group is exercised by real consumers (manifest + frames)."""
    from acp.results.frames import VIEW_REGISTRY, TrajectoryAnnotation, TrajectoryFrame
    from acp.storage.manifest import ProductKind, ResultManifest

    assert "optimization" in VIEW_REGISTRY

    normal = json.loads((_SAMPLES / "science_normal.json").read_text(encoding="utf-8"))["body"]
    manifest = ResultManifest.from_dict(normal["manifest"])
    assert manifest.version == 2
    assert manifest.status == "completed"
    assert len(manifest.products) == 1
    assert manifest.products[0].kind is ProductKind.STRUCTURE

    frame = TrajectoryFrame(**normal["frame"])
    node = frame.to_node("frame")
    assert node["id"] == "f0"
    assert node["metadata"]["step"] == 0

    annotation = TrajectoryAnnotation(**normal["annotation"])
    assert annotation.to_annotation()["id"] == "a1"

    old = json.loads((_SAMPLES / "science_old-version.json").read_text(encoding="utf-8"))["body"]
    migrated = ResultManifest.from_dict(old["manifest"])
    assert migrated.version == 1
    assert migrated.products[0].path == "structures/final.xyz"
