"""Doc-consistency checker for the ACP Services Standalone Recipe (todo 41 / B41a).

Guards the structural promises of
``docs/reference/next-change/ACP_Services_Standalone_Recipe.md``:

* required sections present (recipe, capability report, two exit gates,
  B41a/B41b split, Hosted declaration, Must-NOT list);
* capability report keeps the four states (installed / configured /
  enabled / verified) in separate columns;
* every ``NOT_VERIFIED`` table row carries a reason;
* no Hosted row claims production readiness;
* the two exit gates are separate sections (first-scope vs full-release);
* the B41b deferral (after todo 74) is stated explicitly.

This is a documentation contract test — it intentionally does not touch
product code, the live service, or the real run_root.
"""

from __future__ import annotations

import re
from pathlib import Path

DOC_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "reference"
    / "next-change"
    / "ACP_Services_Standalone_Recipe.md"
)

REQUIRED_HEADINGS = (
    "## 1. 范围与依据",
    "## 2. 干净环境复现步骤（install → init → doctor → 首个本地科研闭环）",
    "## 3. Capability 报告（installed / configured / enabled / verified 四态分列）",
    "## 4. 退出门一（首批范围退出门 / First-scope exit gate）",
    "## 5. 退出门二（完整发行退出门 / Full-release exit gate）",
    "## 6. B41a / B41b 顺序拆分",
    "## 7. Hosted 与 Lab Server 未验证声明",
    "## 8. 禁止性声明（Must NOT）",
    "## 9. 复现命令速查",
)

CAPABILITY_HEADER = "| 组件 | installed | configured | enabled | verified | 证据 |"
STEPS_HEADER = "| 步骤 | 命令 | Expected | Actual | 结果 |"

_VERDICTS = ("PASS", "FAIL", "NOT_VERIFIED")


def _text() -> str:
    assert DOC_PATH.exists(), f"recipe doc missing: {DOC_PATH}"
    return DOC_PATH.read_text(encoding="utf-8")


def _is_separator_row(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s\-:|]+\|", line))


def _table_rows(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("|") and not _is_separator_row(line)]


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    rest = text[start + len(heading) :]
    match = re.search(r"\n## ", rest)
    return rest[: match.start()] if match else rest


def test_required_sections_present() -> None:
    text = _text()
    missing = [h for h in REQUIRED_HEADINGS if h not in text]
    assert not missing, f"missing sections: {missing}"


def test_capability_report_four_state_columns() -> None:
    lines = _text().splitlines()
    assert CAPABILITY_HEADER in lines, "capability table header with four state columns missing"
    start = lines.index(CAPABILITY_HEADER)
    rows: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        if _is_separator_row(line):
            continue
        rows.append(line)
    assert len(rows) >= 10, f"expected >=10 capability rows, got {len(rows)}"
    for row in rows:
        cells = _cells(row)
        assert len(cells) == 6, f"capability row must have 6 cells: {row}"
        for idx, state in ((1, "installed"), (2, "configured"), (3, "enabled"), (4, "verified")):
            assert cells[idx], f"{state} cell empty in row: {row}"
            assert "  " not in cells[idx], f"{state} cell looks conflated (double state): {row}"


def _is_not_verified_row(row: str) -> bool:
    """True when a cell itself *is* a NOT_VERIFIED verdict (not a mere mention)."""
    return any(cell.startswith("NOT_VERIFIED") for cell in _cells(row))


def test_every_not_verified_table_row_has_reason() -> None:
    rows = _table_rows(_text().splitlines())
    not_verified_rows = [row for row in rows if _is_not_verified_row(row)]
    offenders = [row for row in not_verified_rows if "原因" not in row and "reason" not in row]
    assert not offenders, f"NOT_VERIFIED rows without a reason: {offenders}"
    assert len(not_verified_rows) >= 5, (
        f"expected several explicit NOT_VERIFIED records, got {len(not_verified_rows)}"
    )


def test_steps_table_verdicts_are_honest() -> None:
    lines = _text().splitlines()
    assert STEPS_HEADER in lines, "steps table header missing"
    start = lines.index(STEPS_HEADER)
    rows: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        if _is_separator_row(line):
            continue
        rows.append(line)
    assert len(rows) >= 8, f"expected >=8 recipe steps, got {len(rows)}"
    verdicts: list[str] = []
    for row in rows:
        cells = _cells(row)
        assert len(cells) == 5, f"steps row must have 5 cells: {row}"
        verdict = cells[4]
        token = next((v for v in _VERDICTS if verdict.startswith(v)), None)
        assert token is not None, f"unknown verdict {verdict!r} in row: {row}"
        verdicts.append(token)
        if token in ("FAIL", "NOT_VERIFIED"):
            assert "原因" in row, f"{token} row must state a reason: {row}"
    assert "PASS" in verdicts and "FAIL" in verdicts and "NOT_VERIFIED" in verdicts, (
        f"recipe must record real outcomes incl. failures, got: {verdicts}"
    )


def test_hosted_row_never_claims_production_ready() -> None:
    text = _text()
    hosted_rows = [
        line for line in _table_rows(text.splitlines()) if line.startswith("| Hosted 档位")
    ]
    assert hosted_rows, "Hosted capability row missing"
    for row in hosted_rows:
        cells = _cells(row)
        assert len(cells) == 6, f"Hosted row must have 6 cells: {row}"
        for idx, state in ((1, "installed"), (2, "configured"), (3, "enabled")):
            assert cells[idx].startswith("NO"), f"Hosted {state} must be NO: {row}"
        assert "NOT_VERIFIED" in cells[4], f"Hosted verified must be NOT_VERIFIED: {row}"
        assert "PASS" not in cells[4], f"Hosted verified must not PASS: {row}"
    for line in text.splitlines():
        if "Hosted" in line and re.search(r"可生产启用|production-ready", line):
            assert ("不得" in line) or ("未" in line), (
                f"unqualified production-ready claim for Hosted: {line}"
            )


def test_two_exit_gates_are_separate() -> None:
    text = _text()
    gate1 = _section(text, REQUIRED_HEADINGS[3])
    gate2 = _section(text, REQUIRED_HEADINGS[4])
    assert "第一主场景" in gate1, "first-scope gate must define the first main scenario"
    assert "OPEN" in gate1, "first-scope gate must carry an explicit status"
    assert "15" in gate2, "full-release gate must reference the 15-workflow coverage"
    assert "OPEN" in gate2 and "保持开放" in gate2, "full-release gate must stay open until reached"
    assert "不阻塞已满足的独立交付" in gate2, (
        "full-release gate must state it does not block independent deliveries"
    )
    assert text.index(REQUIRED_HEADINGS[3]) < text.index(REQUIRED_HEADINGS[4]), (
        "gate ordering: first-scope before full-release"
    )
    assert "代表完整发行" in text, "doc must forbid first-scope-as-full-release"


def test_b41b_deferral_stated() -> None:
    text = _text()
    assert "B41a" in text and "B41b" in text, "B41a/B41b split must be named"
    assert "todo 74" in text, "B41b must be tied to todo 74"
    assert "T74 不等待本任务" in text, "T74 must not wait on this task"
    assert "todo 74 交付后" in text, "B41b execution must be deferred until after todo 74"


def test_s4_s4c_original_fail_records_are_retained() -> None:
    """The todo-79 addendum must not launder the original FAIL records."""
    lines = _text().splitlines()
    start = lines.index(STEPS_HEADER)
    rows: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            break
        if _is_separator_row(line):
            continue
        rows.append(line)

    def verdict_for(label: str) -> str:
        for row in rows:
            cells = _cells(row)
            if cells[0].startswith(f"{label} "):
                return cells[4]
        raise AssertionError(f"steps row {label} missing")

    for label in ("S4", "S4c"):
        assert verdict_for(label).startswith("FAIL"), (
            f"{label} original FAIL record must be retained (audit trail); "
            "todo-79 after-values belong in the §2.2 addendum"
        )
    assert "## 2.2 Todo 79 缺陷修复后值" in _text(), "todo-79 addendum missing"


def test_wizard_completion_is_not_acceptance() -> None:
    text = _text()
    assert re.search(r"不把[“\"]?向导结束[”\"]?当验收", text), (
        "doc must state that wizard completion is not acceptance"
    )
    assert "S3a" in text, "interactive wizard step must be tracked (NOT_VERIFIED)"


def test_clean_venv_reproduction_recorded() -> None:
    text = _text()
    assert "/tmp/acp_recipe_venv" in text, "clean venv path must be recorded"
    assert "pip install -e '.[api,remote]'" in text, "install command must be reproducible"
    assert "load_result_manifest" in text, "closed-loop judgement must cite the manifest reader"
