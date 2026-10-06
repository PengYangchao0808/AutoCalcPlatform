#!/usr/bin/env python3.11
"""Verify the ACP NMR Goodman gap-coverage ledger (todo 59).

The ledger ``.omo/evidence/acp-nmr-goodman-gap-remediation/GAP_COVERAGE.md``
maps every gap id G01-G17 from
``.omo/evidence/acp-cccp-remediation/discovered/ACP_NMR_Goodman_Gap_Investigation_2026-10-05.md``
to the plan tasks (T<number>), binding tests, evidence files and a status from
the closed set {fixed, validated-harness, deferred-out-of-scope}.

Checks performed (any failure exits non-zero):

1. Exactly one table row per gap id G01..G17, and no unexpected gap rows.
2. Every gap row maps to at least one distinct task ``T<number>``.
3. Every gap row status is in the closed vocabulary.
4. Every ``tests/...py`` file cited in a row exists in the repository.
5. Every ``task-...`` evidence file cited in a row exists under the evidence
   directory (exact name, or shell-glob when a ``*`` is used).
6. Every task number cited in a row has at least one ``task-<N>-*`` artifact
   in the evidence directory.
7. No orphan evidence: every artifact that already existed when the ledger was
   written (top-level ``task-*`` entries plus files nested inside ``task-*``
   directories) is listed in the ledger index. Artifacts whose mtime is newer
   than the ledger are reported as post-snapshot and exempt until the index is
   regenerated (concurrent workers may keep adding evidence files).

Usage (stdlib only, runs from the repository root):

    python3.11 scripts/check_nmr_gap_coverage.py
    python3.11 scripts/check_nmr_gap_coverage.py --ledger <path> --evidence-dir <dir>

Author: ACP NMR gap-remediation plan, todo 59.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

GAP_IDS = tuple(f"G{index:02d}" for index in range(1, 18))
VALID_STATUSES = ("fixed", "validated-harness", "deferred-out-of-scope")

DEFAULT_LEDGER = Path(".omo/evidence/acp-nmr-goodman-gap-remediation/GAP_COVERAGE.md")
DEFAULT_EVIDENCE_DIR = Path(".omo/evidence/acp-nmr-goodman-gap-remediation")

ROW_RE = re.compile(r"^\|\s*(G\d{2})\s*\|")
TASK_RE = re.compile(r"\bT(\d+)\b")
EVIDENCE_RE = re.compile(r"`(task-[^`]+)`")
TEST_RE = re.compile(r"`(tests/[^`]+\.py)`")

# Column order of the G-coverage table (header: Gap | Title | Tasks | Tests |
# Evidence | Status | Basis).
TASKS_COLUMN = 2
TESTS_COLUMN = 3
EVIDENCE_COLUMN = 4
STATUS_COLUMN = 5
MIN_CELLS = 7


class LedgerCheckError(Exception):
    """A ledger contract violation."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_EVIDENCE_DIR)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    return parser.parse_args(argv)


def parse_rows(text: str) -> dict[str, list[str]]:
    """Return {gap_id: cells} for every ``| G.. |`` table row."""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = ROW_RE.match(line)
        if match is None:
            continue
        gap_id = match.group(1)
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if gap_id in rows:
            raise LedgerCheckError(f"duplicate table row for {gap_id}")
        if gap_id not in GAP_IDS:
            raise LedgerCheckError(f"unexpected gap row {gap_id} (expected G01-G17)")
        rows[gap_id] = cells
    return rows


def resolve_evidence_token(token: str, evidence_dir: Path) -> bool:
    """True when the token names an existing artifact (glob-aware)."""
    candidate = evidence_dir / token
    if candidate.exists():
        return True
    if "*" in token or "?" in token or "[" in token:
        return any(True for _ in evidence_dir.glob(token))
    return False


def collect_artifacts(evidence_dir: Path, ledger_mtime: float) -> tuple[list[str], list[str]]:
    """Return (snapshot artifacts, post-snapshot artifacts).

    Snapshot artifacts existed when the ledger was written and must be
    mentioned in the index. Post-snapshot artifacts (mtime newer than the
    ledger) are exempt until the index is regenerated.
    """
    snapshot: list[str] = []
    post_snapshot: list[str] = []
    for entry in sorted(evidence_dir.iterdir()):
        if not entry.name.startswith("task-"):
            continue
        if entry.stat().st_mtime > ledger_mtime:
            post_snapshot.append(entry.name)
        else:
            snapshot.append(entry.name)
        if entry.is_dir():
            for nested in sorted(entry.rglob("*")):
                if not nested.is_file():
                    continue
                relative = f"{entry.name}/{nested.relative_to(entry).as_posix()}"
                if nested.stat().st_mtime > ledger_mtime:
                    post_snapshot.append(relative)
                else:
                    snapshot.append(relative)
    return snapshot, post_snapshot


def check_ledger(
    text: str, evidence_dir: Path, repo_root: Path, ledger_mtime: float
) -> tuple[dict[str, list[str]], int, int]:
    """Run all contract checks; return (rows, indexed_count, exempt_count)."""
    rows = parse_rows(text)
    missing_ids = [gap_id for gap_id in GAP_IDS if gap_id not in rows]
    if missing_ids:
        raise LedgerCheckError(f"missing gap rows: {', '.join(missing_ids)}")

    task_prefixes = tuple(
        entry.name for entry in evidence_dir.iterdir() if entry.name.startswith("task-")
    )
    # task-<N> artifacts are named task-<N>-...; the plain prefix also matches
    # the directory entries, so a directory-style existence test is fine.
    known_tasks: set[int] = set()
    for name in task_prefixes:
        match = re.match(r"task-(\d+)", name)
        if match is not None:
            known_tasks.add(int(match.group(1)))

    for gap_id in GAP_IDS:
        cells = rows[gap_id]
        if len(cells) < MIN_CELLS:
            raise LedgerCheckError(
                f"{gap_id}: row has {len(cells)} cells, expected at least {MIN_CELLS}"
            )
        tasks = [int(number) for number in TASK_RE.findall(cells[TASKS_COLUMN])]
        if not tasks:
            raise LedgerCheckError(f"{gap_id}: maps to zero tasks")
        if len(set(tasks)) != len(tasks):
            raise LedgerCheckError(f"{gap_id}: duplicate task numbers in row")
        for number in tasks:
            if number not in known_tasks:
                raise LedgerCheckError(
                    f"{gap_id}: task T{number} has no task-{number}-* evidence artifact"
                )
        status = cells[STATUS_COLUMN]
        if status not in VALID_STATUSES:
            raise LedgerCheckError(f"{gap_id}: status {status!r} not in {list(VALID_STATUSES)}")
        for test_token in TEST_RE.findall(cells[TESTS_COLUMN]):
            if not (repo_root / test_token).is_file():
                raise LedgerCheckError(f"{gap_id}: cited test missing: {test_token}")
        for evidence_token in EVIDENCE_RE.findall(cells[EVIDENCE_COLUMN]):
            if not resolve_evidence_token(evidence_token, evidence_dir):
                raise LedgerCheckError(f"{gap_id}: cited evidence missing: {evidence_token}")

    snapshot, post_snapshot = collect_artifacts(evidence_dir, ledger_mtime)
    orphaned = [name for name in snapshot if name not in text]
    if orphaned:
        preview = ", ".join(orphaned[:5])
        raise LedgerCheckError(
            f"{len(orphaned)} evidence artifact(s) not indexed in the ledger: {preview}"
        )
    return rows, len(snapshot), len(post_snapshot)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        text = args.ledger.read_text(encoding="utf-8")
        ledger_mtime = args.ledger.stat().st_mtime
    except OSError as exc:
        print(f"GAP_COVERAGE_CHECK: FAIL — cannot read ledger: {exc}")
        return 1
    try:
        rows, artifact_count, exempt_count = check_ledger(
            text, args.evidence_dir, args.repo_root, ledger_mtime
        )
    except LedgerCheckError as exc:
        print(f"GAP_COVERAGE_CHECK: FAIL — {exc}")
        return 1

    statuses = {cells[STATUS_COLUMN] for cells in rows.values()}
    for status in VALID_STATUSES:
        count = sum(1 for cells in rows.values() if cells[STATUS_COLUMN] == status)
        print(f"status[{status}] = {count}")
    print(f"gaps = {len(rows)}/17; every gap maps to >= 1 task")
    print(f"evidence_artifacts_indexed = {artifact_count}")
    if exempt_count:
        print(
            f"post_snapshot_artifacts_exempt = {exempt_count} "
            "(newer than the ledger; regenerate the index to fold them in)"
        )
    print(f"active_statuses = {sorted(statuses)}")
    print("GAP_COVERAGE_CHECK: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
