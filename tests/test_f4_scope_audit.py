"""F4 scope-fidelity audit — deterministic audit test.

Verifies that changes from ``refactor-baseline`` to the working tree in the
interfaces/orca + backends/orca scope are limited to declared target regions
(opt_freq deletion, calc_type_map cleanup, NumFreq branch simplification),
with no algorithm-body additions or interface-scope overflows.

Groups:
    ① AST function-scope audit (deletion-only)
    ② Scheduler DB mechanism tables (fixture via migrations)
    ③ .omo/ directory — no new files
    ④ Catalog retired-ID final-state audit
    ⑤ Must-NOT-Have (grep gates + BatchOptimize no-IRC)
"""
from __future__ import annotations

import ast
import inspect
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASELINE = "refactor-baseline"
ALLOWED_PY = frozenset({
    "src/cccp/qc/interfaces/orca.py",
    "src/cccp/qc/interfaces/orca_ts.py",
    "src/acp/backends/orca.py",
})


# ── helpers ──────────────────────────────────────────────────────────────────


def _git(*args: str) -> str:
    """Run a git command and return stdout."""
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, cwd=ROOT,
    ).stdout


def _changed_files() -> set[str]:
    """Files changed between *BASELINE* and worktree in the audited scope."""
    out = _git(
        "diff", "--name-only", BASELINE, "--",
        "src/cccp/qc/interfaces/", "src/acp/backends/orca.py",
    )
    return set(out.strip().splitlines()) if out.strip() else set()


def _diff_hunks(
    path: str,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Parse unified diff for *path*.

    Returns ``(added, deleted)`` where each entry is
    ``(line_number_in_respective_version, content)``.
    """
    out = _git("diff", BASELINE, "--", path)
    added: list[tuple[int, str]] = []
    deleted: list[tuple[int, str]] = []
    old = new = 0
    for raw in out.splitlines():
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old, new = int(m[1]), int(m[2])
        elif raw.startswith("-") and not raw.startswith("---"):
            deleted.append((old, raw[1:]))
            old += 1
        elif raw.startswith("+") and not raw.startswith("+++"):
            added.append((new, raw[1:]))
            new += 1
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            old += 1
            new += 1
    return added, deleted


def _baseline_content(path: str) -> str:
    """Return file content at the baseline ref, or skip if absent."""
    r = subprocess.run(
        ["git", "show", f"{BASELINE}:{path}"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if r.returncode != 0:
        pytest.skip(f"Not in baseline: {path}")
    return r.stdout


def _worktree_content(path: str) -> str:
    """Return file content from the working tree."""
    return (ROOT / path).read_text()


def _func_ranges(src: str) -> dict[str, tuple[int, int]]:
    """Parse AST and return ``{name: (start_line, end_line)}``."""
    out: dict[str, tuple[int, int]] = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = (node.lineno, getattr(node, "end_lineno", node.lineno))
    return out


def _target_orca(src: str) -> set[int]:
    """Target-region line numbers for baseline ``orca.py``."""
    lines = src.splitlines()
    fr = _func_ranges(src)
    tgt: set[int] = set()

    # 1. ORCAInterface.opt_freq method + trailing blank-line buffer
    if "opt_freq" in fr:
        s, e = fr["opt_freq"]
        tgt.update(range(s, min(e + 4, len(lines) + 1)))

    # 2. _build_input_blocks — lines referencing optfreq / Opt Freq + context
    if "_build_input_blocks" in fr:
        bs, be = fr["_build_input_blocks"]
        for i in range(bs, be + 1):
            txt = lines[i - 1]
            if "optfreq" in txt or "Opt Freq" in txt:
                for j in range(max(bs, i - 5), min(be + 1, i + 10)):
                    tgt.add(j)

    # 3. Module-level optfreq constants
    for i, ln in enumerate(lines, 1):
        if not ln[:1].strip():
            low = ln.lower()
            if ("optfreq" in low or "opt_freq" in low) and not ln.lstrip().startswith("#"):
                tgt.add(i)

    return tgt


def _target_backend(src: str) -> set[int]:
    """Target-region line numbers for baseline ``backends/orca.py``."""
    lines = src.splitlines()
    fr = _func_ranges(src)
    tgt: set[int] = set()
    if "opt_freq" in fr:
        s, e = fr["opt_freq"]
        tgt.update(range(s, min(e + 4, len(lines) + 1)))
    return tgt


# ── ① AST function-scope audit ──────────────────────────────────────────────


def test_diff_only_allowed_py_files() -> None:
    """① Only ``.py`` files in the allowed set appear in the diff."""
    py = {f for f in _changed_files() if f.endswith(".py")}
    assert not (py - ALLOWED_PY), f"Unexpected .py files changed: {py - ALLOWED_PY}"


def test_algorithm_body_untouched() -> None:
    """① Every added line must be pure comment/blank — no algorithm-body changes.

    Per plan: ``新增行仅允许为纯注释/空行（^\\s*#|^\\s*$），
    任何非注释新增/修改行 = FAIL``.
    """
    violations: list[str] = []
    for fp in ALLOWED_PY:
        added, _ = _diff_hunks(fp)
        for ln, txt in added:
            stripped = txt.strip()
            if stripped and not stripped.startswith("#"):
                violations.append(f"  {fp}:{ln}: {txt!r}")
    assert not violations, "Non-comment added lines detected:\n" + "\n".join(violations)


def test_orca_ts_no_changes() -> None:
    """① ``orca_ts.py`` must have zero changes (empty target set)."""
    assert "src/cccp/qc/interfaces/orca_ts.py" not in _changed_files(), (
        "orca_ts.py has changes but target-region set is empty"
    )


def test_deleted_lines_in_target_regions() -> None:
    """① Every deleted line falls inside a declared target region."""
    checks = [
        ("src/cccp/qc/interfaces/orca.py", _target_orca),
        ("src/acp/backends/orca.py", _target_backend),
    ]
    for fp, builder in checks:
        tgt = builder(_baseline_content(fp))
        _, deleted = _diff_hunks(fp)
        bad = [f"  {fp}:{ln}: {t!r}" for ln, t in deleted if ln not in tgt]
        assert not bad, "Deleted lines outside target regions:\n" + "\n".join(bad)


def test_worktree_opt_freq_absent() -> None:
    """① Target regions must not reappear in the working tree."""
    # Class methods
    for cls, fp in [
        ("ORCAInterface", "src/cccp/qc/interfaces/orca.py"),
        ("ORCABackend", "src/acp/backends/orca.py"),
    ]:
        tree = ast.parse(_worktree_content(fp))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == cls:
                names = {
                    d.name
                    for d in node.body
                    if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                assert "opt_freq" not in names, f"{cls}.opt_freq still present"

    # Module-level optfreq constants
    for fp in ("src/cccp/qc/interfaces/orca.py", "src/acp/backends/orca.py"):
        for i, ln in enumerate(_worktree_content(fp).splitlines(), 1):
            if not ln[:1].strip():
                low = ln.lower()
                if ("optfreq" in low or "opt_freq" in low) and not ln.lstrip().startswith("#"):
                    pytest.fail(f"{fp}:{i}: module-level optfreq reference: {ln!r}")


# ── ② Scheduler DB mechanism tables ─────────────────────────────────────────


def test_scheduler_db_mechanism_tables() -> None:
    """② ``mechanism_studies`` / ``decision_points`` / ``mechanism_projects`` exist.

    Built in-test via ``acp.scheduler.migrations.migrate``; fixture rows
    inserted and verified.
    """
    from acp.scheduler.migrations import migrate

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = Path(f.name)
    try:
        migrate(db)
        conn = sqlite3.connect(str(db))
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for t in ("mechanism_studies", "decision_points", "mechanism_projects"):
            assert t in tables, f"{t} table missing"

        # Fixture rows
        conn.execute(
            "INSERT INTO mechanism_studies "
            "(id, job_id, status, created_at, updated_at) "
            "VALUES ('s1', 'j1', 'active', '2026-01-01', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO decision_points (id, study_id, status, created_at) "
            "VALUES ('dp1', 's1', 'pending', '2026-01-01')"
        )
        conn.execute(
            "INSERT INTO mechanism_projects "
            "(project_id, name, created_at, updated_at) "
            "VALUES ('p1', 'test', '2026-01-01', '2026-01-01')"
        )
        conn.commit()

        assert conn.execute("SELECT count(*) FROM mechanism_studies").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM decision_points").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM mechanism_projects").fetchone()[0] == 1
        conn.close()
    finally:
        db.unlink(missing_ok=True)


# ── ③ .omo/ no new files ────────────────────────────────────────────────────


def test_omo_no_new_files() -> None:
    """③ ``git log --all --diff-filter=A -- .omo/`` must be empty."""
    assert not _git("log", "--all", "--diff-filter=A", "--", ".omo/").strip()


# ── ④ Catalog retired-ID final-state audit ──────────────────────────────────


def test_catalog_retired_ids() -> None:
    """④ ``final == baseline ∪ {optfreq, optfreqsp, Lowconfirm, Highconfirm}``.

    Also asserts ``baseline ⊆ final`` (zero drift on original ten).
    """
    base_path = ROOT / "tests/baseline/refactor-evidence/catalog-retired-ids.txt"
    final_path = ROOT / "tests/baseline/refactor-evidence/catalog-retired-ids-final.txt"
    base = set(base_path.read_text().splitlines())
    final = set(final_path.read_text().splitlines())
    expected = base | {"optfreq", "optfreqsp", "Lowconfirm", "Highconfirm"}
    assert final == expected, (
        f"Retired-ID mismatch: missing={expected - final}, extra={final - expected}"
    )
    assert base <= final, "Baseline IDs not subset of final"


# ── ⑤ Must-NOT-Have ────────────────────────────────────────────────────────


def test_grep_gate_final_forbidden_symbols() -> None:
    """⑤ ``check_grep_gates --gate final_forbidden_symbols src/acp`` exit 0."""
    r = subprocess.run(
        ["python", "scripts/check_grep_gates.py", "--gate", "final_forbidden_symbols", "src/acp"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert r.returncode == 0, f"Gate failed:\n{r.stdout}\n{r.stderr}"


def test_batch_no_irc_invariants() -> None:
    """⑤ BatchOptimize engine source must not reference IRC."""
    from acp.calculations.batch import engine as batch_engine

    src = inspect.getsource(batch_engine).casefold()
    assert "irc" not in src, "BatchOptimize engine contains IRC references"
