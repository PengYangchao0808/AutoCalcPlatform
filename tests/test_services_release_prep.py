"""Todo 71 (B13-6) release-prep guards.

Doc/checklist consistency + systemd unit provenance + rollback-drill reference
for `docs/reference/current/ACP_Services_Release_Prep_20261019.md`.
Product code is never imported; every path is read-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "reference" / "current" / "ACP_Services_Release_Prep_20261019.md"
CHECKLIST = REPO / "tests" / "baseline" / "services-evidence" / "release_checklist.md"
UNIT_SCRIPT = REPO / "scripts" / "install_systemd.sh"
START_SCRIPT = REPO / "scripts" / "start_acp.sh"
LIVE_UNIT = Path("/etc/systemd/system/acp.service")

# Doc tokens that prove this todo prepared (not performed) the release.
PREPARATION_ONLY_TOKENS = ("未发布", "未重启生产", "未修改 systemd unit", "todo 76")
# Sentences that would illegally claim publish/restart happened in this todo.
FORBIDDEN_PUBLISH_CLAIMS = (
    "本 todo 已发布",
    "本 todo 已重启",
    "已完成生产发布",
    "已重启服务并",
    "已实际发布",
    "本文件撰写过程中已发布",
)

REQUIRED_DOC_SECTIONS = (
    "兼容矩阵",
    "旧 reader 能读哪些新记录",
    "何时不可回退",
    "代码回退（code rollback）",
    "Schema 回退（schema rollback）",
    "数据恢复（data restore）",
    "服务重启流程与 unit 生成来源核对",
    "回退演练记录",
    "发布清单",
)

# Compatibility-matrix row labels (Release_Prep §2 M1—M6).
MATRIX_ROW_TOKENS = ("旧页面", "旧 worker", "旧 release", "在途任务")


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def _checklist_text() -> str:
    return CHECKLIST.read_text(encoding="utf-8")


def _assert_no_publish_claim(text: str, *, where: str) -> None:
    """Fail when `text` claims this todo published/restarted, or lacks the
    preparation-only statements.  Shared by the positive guard and the
    negative control."""
    violations = [c for c in FORBIDDEN_PUBLISH_CLAIMS if c in text]
    missing = [t for t in PREPARATION_ONLY_TOKENS if t not in text]
    assert not violations, f"{where}: illegal publish/restart claim(s): {violations}"
    assert not missing, f"{where}: missing preparation-only statement(s): {missing}"


def test_doc_exists_with_required_sections() -> None:
    text = _doc_text()
    for section in REQUIRED_DOC_SECTIONS:
        assert section in text, f"doc missing required section token: {section!r}"


def test_doc_preparation_only_no_publish_claim() -> None:
    _assert_no_publish_claim(_doc_text(), where="ACP_Services_Release_Prep doc")


def test_negative_fake_doc_claiming_publish_would_fail() -> None:
    fake = "本 todo 已发布 0.2.0 并已重启服务并完成冒烟；未发布"  # claim + token mix
    with pytest.raises(AssertionError, match="illegal publish/restart claim"):
        _assert_no_publish_claim(fake, where="negative control")


def test_compatibility_matrix_rows_complete() -> None:
    text = _doc_text()
    for token in MATRIX_ROW_TOKENS:
        assert token in text, f"compatibility matrix missing row: {token!r}"
    # Four-combination records must be consumed, not re-executed.
    assert "ACP_Services_Four_Combination_Acceptance.md" in text
    assert "PASS_IN_REPO" in text


def test_rollback_steps_written_separately_for_code_schema_data() -> None:
    text = _doc_text()
    for token in ("### 3.3", "### 3.4", "### 3.5"):
        assert token in text, f"missing rollback subsection heading: {token}"
    # Each path must carry its own operational statement.
    assert "git checkout" in text, "code rollback must state the concrete operation"
    assert "无 downgrade API" in text or "不存在降级 API" in text, (
        "schema rollback must state no-downgrade-API"
    )
    assert ".backup" in text, "data restore must state the snapshot operation"
    # Forward-only ledger constraint (fault-matrix F-2) must be cited.
    assert "F-2" in text


def test_drill_record_referenced_and_scoped_to_tmp() -> None:
    text = _doc_text()
    assert "t71_rollback_drill.py" in text, "doc must reference the drill script"
    assert "/tmp/opencode/t71-drill" in text, "doc must reference the drill record path"
    assert "RESULT=PASS" in text, "doc must record the drill outcome"
    assert "未触生产" in text or "未读写生产" in text, (
        "doc must state the drill never touched production"
    )
    # The drill must cover old-reader + restore legs required by the matrix.
    for leg in ("旧 reader", "数据恢复", "schema version"):
        assert leg in text, f"drill record missing leg: {leg!r}"


def test_checklist_references_existing_files_and_scripts() -> None:
    text = _checklist_text()
    # Repo paths appear both inline and inside fenced command blocks; scan the
    # raw text.  Globs/placeholders (…_*.py, ${COMMIT}) are intentionally
    # unverifiable and skipped.
    candidates = re.findall(r"(?:tests|src|scripts|docs)/[A-Za-z0-9_.*${}/-]+", text)
    checked: set[str] = set()
    for token in candidates:
        if any(ch in token for ch in "*${}"):
            continue
        token = token.rstrip(".,;:)")
        path = REPO / token
        assert path.exists(), f"checklist references missing path: {token}"
        checked.add(token)
    assert len(checked) >= 10, f"expected many concrete path references, found {sorted(checked)}"
    # The checklist itself and the prep doc must both be referenced.
    assert "docs/reference/current/ACP_Services_Release_Prep_20261019.md" in checked
    assert "tests/baseline/services-evidence/release_checklist.md" in checked


def test_checklist_gates_and_commands_present() -> None:
    text = _checklist_text()
    for gate in ("F1 APPROVE", "F2 APPROVE", "F3 APPROVE", "F4 APPROVE"):
        assert gate in text, f"checklist missing gate: {gate}"
    # Version lockstep across the 4 files.
    for lock in (
        "src/acp/__init__.py",
        "src/cccp/__init__.py",
        "src/cccp/version.py",
        "pyproject.toml",
    ):
        assert lock in text, f"checklist missing lockstep file: {lock}"
    # Guard suites + grep gates + smoke.
    for cmd in (
        "tests/test_services_",
        "test_acp_services_isolation",
        "check_grep_gates",
        "acp doctor",
        "systemctl restart acp",
    ):
        assert cmd in text, f"checklist missing command/gate token: {cmd}"
    assert "install_systemd.sh" in text
    assert "手改" in text, "checklist must forbid hand-editing the unit"
    # In-flight compatibility check (matrix rows) must be an execution step.
    assert "在途任务" in text


def test_unit_script_contains_generation_template() -> None:
    script = UNIT_SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "[Unit]",
        "Description=ACP",
        "Environment=ACP_RUN_ROOT=",
        "ExecStart=$PROJECT_ROOT/scripts/start_acp.sh",
        "WantedBy=multi-user.target",
        "sudo tee",
        "daemon-reload",
        "/etc/systemd/system/acp.service",
    ):
        assert marker in script, f"install_systemd.sh missing template marker: {marker!r}"
    assert "Environment=ACP_RUN_ROOT=/var/lib/acp/runs" not in script
    start = START_SCRIPT.read_text(encoding="utf-8")
    assert "uvicorn acp.api.server:app" in start


def test_doc_warns_against_hand_editing_unit() -> None:
    text = _doc_text()
    assert "勿手改 `/etc/systemd/system/acp.service`" in text
    assert "scripts/install_systemd.sh" in text


def test_unit_provenance_comparison_recorded() -> None:
    """The doc's provenance verdict must match a live re-comparison of the
    unit's key lines against the generator template (read-only).  Skips the
    live half when the unit is not readable on this host."""
    text = _doc_text()
    script = UNIT_SCRIPT.read_text(encoding="utf-8")
    # Extract the heredoc template's key lines.
    template: dict[str, str] = {}
    for key in ("Description=", "After=", "ExecStart=", "Environment=ACP_RUN_ROOT="):
        m = re.search(rf"^{re.escape(key)}.*$", script, flags=re.MULTILINE)
        assert m, f"template line not found in install_systemd.sh: {key}"
        template[key.rstrip("=").split("=")[0]] = m.group(0).strip()

    if not LIVE_UNIT.exists():
        pytest.skip("live acp.service not readable on this host")
    live = LIVE_UNIT.read_text(encoding="utf-8")
    live_lines = {
        ln.split("=", 1)[0]: ln.strip()
        for ln in live.splitlines()
        if "=" in ln and not ln.startswith(("#", "["))
    }
    drifted = any(live_lines.get(k) != v for k, v in template.items())
    if drifted:
        assert "核对结果：DRIFT" in text, (
            "live unit differs from the generator template but the doc does "
            "not record the DRIFT verdict"
        )
    else:
        assert "核对结果：一致" in text, (
            "live unit matches the generator template but the doc does not record the match verdict"
        )
