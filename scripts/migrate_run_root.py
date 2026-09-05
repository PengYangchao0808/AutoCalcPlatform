#!/usr/bin/env python
"""One-time migration: relocate the ACP data run root (two-tier layout).

Copies the entire run tree (SQLite index + per-project task directories) to
a new root on a native filesystem, then rewrites every absolute-path
reference from the old root to the new one:

- all TEXT columns in the copied SQLite database (prefix match only)
- ``job.json`` / ``task.json`` files inside the copied tree

The old tree is NEVER modified or deleted — it stays as a read-only archive.
A timestamped backup of the database is kept in the new root.

Usage:
    python scripts/migrate_run_root.py /old/ACP_runs /var/lib/acp/runs [--dry-run]

After migration, point ACP_RUN_ROOT (or ``acp run serve --run-root``) at the
new root before restarting the server.
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_NAME = "acp_jobs.db"
JSON_FILE_NAMES = {"job.json", "task.json"}


def _fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def _validate(old: Path, new: Path) -> None:
    if not (old / DB_NAME).is_file():
        raise ValueError(f"{old} does not look like a run root (missing {DB_NAME})")
    if new == old or new in old.parents or old in new.parents:
        raise ValueError(f"old and new roots must be disjoint: {old} vs {new}")


def _count_jobs(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


def _rewrite_db_prefixes(db_path: Path, old: Path, new: Path) -> tuple[int, int]:
    """Rewrite `old`-prefixed TEXT values row-by-row; return (rows, columns) touched."""
    conn = sqlite3.connect(str(db_path))
    rows_touched = 0
    columns_touched = 0
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        old_text = str(old)
        new_text = str(new)
        for table in tables:
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info([{table}])")]
            for column in columns:
                try:
                    rows = conn.execute(
                        f"SELECT rowid, [{column}] FROM [{table}] WHERE typeof([{column}]) = 'text'"
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue  # rowid-less or expression-indexed table; skip
                updates = [
                    (new_text + value[len(old_text) :], rowid)
                    for rowid, value in rows
                    if isinstance(value, str) and value.startswith(old_text)
                ]
                if not updates:
                    continue
                conn.executemany(
                    f"UPDATE [{table}] SET [{column}] = ? WHERE rowid = ?",
                    updates,
                )
                rows_touched += len(updates)
                columns_touched += 1
        conn.commit()
    finally:
        conn.close()
    return rows_touched, columns_touched


def _rewrite_json_files(tree: Path, old: Path, new: Path) -> int:
    old_text = str(old)
    new_text = str(new)
    rewritten = 0
    for name in JSON_FILE_NAMES:
        for json_file in tree.rglob(name):
            text = json_file.read_text(encoding="utf-8")
            if old_text in text:
                json_file.write_text(text.replace(old_text, new_text), encoding="utf-8")
                rewritten += 1
    return rewritten


def _verify(new: Path) -> list[str]:
    problems: list[str] = []
    conn = sqlite3.connect(str(new / DB_NAME))
    try:
        rows = conn.execute("SELECT id, work_dir FROM jobs").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    for job_id, work_dir in rows:
        if not Path(str(work_dir)).is_dir():
            problems.append(f"job {job_id}: work_dir missing: {work_dir}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old_root", type=Path, help="Current run root (stays untouched)")
    parser.add_argument("new_root", type=Path, help="New run root (must not exist inside old)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and report only; copy nothing"
    )
    args = parser.parse_args(argv)

    old = args.old_root.expanduser().resolve()
    new = args.new_root.expanduser().resolve()
    try:
        _validate(old, new)
    except ValueError as exc:
        return _fail(str(exc))

    print(f"Old run root : {old}  ({_count_jobs(old / DB_NAME)} jobs, never modified)")
    print(f"New run root : {new}")
    if args.dry_run:
        print("Dry run: no changes made.")
        return 0

    if new.exists() and any(new.iterdir()):
        return _fail(f"new root already exists and is not empty: {new}")

    print("Copying tree ...")
    shutil.copytree(old, new, dirs_exist_ok=True, symlinks=True)

    db_path = new / DB_NAME
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = new / f"{DB_NAME}.bak-{stamp}"
    shutil.copy2(db_path, backup)
    print(f"DB backup    : {backup}")

    rows, columns = _rewrite_db_prefixes(db_path, old, new)
    print(f"DB rewrite   : {rows} value(s) across {columns} column(s)")

    json_count = _rewrite_json_files(new, old, new)
    print(f"JSON rewrite : {json_count} job.json/task.json file(s)")

    problems = _verify(new)
    if problems:
        for problem in problems:
            print(f"VERIFY FAIL  : {problem}", file=sys.stderr)
        print(
            f"DB backup at {backup}; fix or restore before switching ACP_RUN_ROOT.",
            file=sys.stderr,
        )
        return 1

    print("Verify       : all job work_dirs present under the new root")
    print(
        "\nDone. Restart the server with the new root, e.g.:\n"
        f"  ACP_RUN_ROOT={new} python -m acp.cli run serve\n"
        "or via scripts/start_acp.sh with ACP_RUN_ROOT exported.\n"
        f"The old tree {old} is kept as a read-only archive."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
