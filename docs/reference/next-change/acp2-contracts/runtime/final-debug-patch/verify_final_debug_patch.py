#!/usr/bin/env python3.11
"""Verify a worktree against the frozen final-debug-patch manifest (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--worktree", required=True, type=Path)
    args = parser.parse_args()

    manifest = json.loads((args.bundle / "manifest.json").read_text(encoding="utf-8"))
    mismatches: list[dict[str, object]] = []
    missing: list[str] = []
    checked = 0
    for entry in manifest["files"]:
        rel = entry["path"]
        target = args.worktree / rel
        if not target.is_file():
            missing.append(rel)
            continue
        checked += 1
        actual_sha = sha256(target)
        actual_size = target.stat().st_size
        if actual_sha != entry["sha256"] or actual_size != entry["size_bytes"]:
            mismatches.append(
                {
                    "path": rel,
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": actual_sha,
                    "expected_size": entry["size_bytes"],
                    "actual_size": actual_size,
                }
            )

    result = {
        "candidate_id": manifest["candidate_id"],
        "total": len(manifest["files"]),
        "checked": checked,
        "missing": missing,
        "mismatches": mismatches,
        "result": "PASS" if not missing and not mismatches else "FAIL",
    }
    print(json.dumps(result, indent=2))
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
