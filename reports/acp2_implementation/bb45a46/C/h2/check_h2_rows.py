#!/usr/bin/env python3
"""Script-diff H2.json rows against their source result files.

Fails (exit 1) when any per-scenario row in ``experience.rows`` differs from
the value in the source receipt, when a P0 key is missing, when the aggregate
verdict or gap list misrepresents the sources, or when a row's recorded
artifacts cannot be re-verified (size + sha256) on disk.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

H2 = Path(
    "/ACPServer/acp2-development/C/docs/reference/next-change/"
    "acp2-contracts/experience/handoffs/H2.json"
)

P0 = [
    "baseline",
    "contracts",
    "workspace",
    "harness-selftest",
    "node-dsh-install",
    "browser-install",
    "bridge-contract",
    "mcp-live",
    "dsh-discovery-live",
    "model-live",
]

ROW_FIELDS = [
    "result",
    "scope",
    "reason",
    "command",
    "cwd",
    "exit_code",
    "candidate",
    "started_at",
    "finished_at",
    "artifacts",
    "usage",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    problems: list[str] = []
    document = json.loads(H2.read_text(encoding="utf-8"))
    rows = document["extensions"]["experience.rows"]

    missing = [key for key in P0 if key not in rows]
    if missing:
        problems.append(f"missing P0 keys: {missing}")
    extra = [key for key in rows if key not in P0]
    if extra:
        problems.append(f"unexpected extra row keys: {extra}")

    gaps = []
    for key in P0:
        if key not in rows:
            continue
        row = rows[key]
        source = json.loads(Path(row["receipt_path"]).read_text(encoding="utf-8"))
        extensions = source.get("extensions") or {}

        for field in ROW_FIELDS:
            if row.get(field) != source.get(field):
                problems.append(f"{key}.{field}: H2={row.get(field)!r} SRC={source.get(field)!r}")
        for field in ("interpreter", "product_import", "harness_path", "test_revision"):
            ext_key = f"experience.{field}"
            if row.get(field) is None:
                problems.append(f"{key}.{field}: missing in H2 row")
            elif row[field] != extensions.get(ext_key):
                problems.append(
                    f"{key}.{field}: H2={row.get(field)!r} SRC={extensions.get(ext_key)!r}"
                )
        for sub in document["sub_results"]:
            if sub.get("run_id") == source.get("run_id") and sub.get("scenario") == key:
                pass
        if row["result"] != "PASS":
            gaps.append(key)

        # Row artifacts must still verify on disk (paths are receipt-relative).
        receipt_dir = Path(row["receipt_path"]).parent
        for artifact in row["artifacts"]:
            path = Path(artifact["path"])
            resolved = path if path.is_absolute() else receipt_dir / path
            if not resolved.is_file():
                problems.append(f"{key}: artifact missing {artifact['path']}")
                continue
            if resolved.stat().st_size != artifact["size_bytes"]:
                problems.append(f"{key}: artifact size mismatch {artifact['path']}")
            if sha256_file(resolved) != artifact["sha256"]:
                problems.append(f"{key}: artifact sha256 mismatch {artifact['path']}")

    # sub_results must point at exactly the row receipts with matching results.
    if len(document["sub_results"]) != len(P0):
        problems.append(f"sub_results count {len(document['sub_results'])} != {len(P0)}")
    row_by_path = {rows[key]["receipt_path"]: rows[key] for key in rows}
    for sub in document["sub_results"]:
        row = row_by_path.get(sub["receipt"])
        if row is None:
            problems.append(f"sub_results receipt not a row: {sub['receipt']}")
        elif sub["result"] != row["result"]:
            problems.append(f"sub_results result mismatch for {sub['receipt']}")

    expected_gaps = ["mcp-live", "dsh-discovery-live", "model-live"]
    if gaps != expected_gaps:
        problems.append(f"gap list {gaps} != expected {expected_gaps}")

    if document["result"] == "PASS":
        problems.append("aggregate claims PASS while gaps exist" if gaps else "aggregate PASS without full check")
    elif gaps and document["result"] != "NOT_VERIFIED":
        problems.append(f"aggregate result {document['result']} with gaps {gaps}")

    reason = document.get("reason") or ""
    for key in expected_gaps:
        if key not in reason:
            problems.append(f"reason does not name gap {key}")
    if "stays open" not in reason:
        problems.append("reason does not state H2 C side stays open")

    if problems:
        for problem in problems:
            print(f"DIFF: {problem}")
        return 1
    print(f"OK: all {len(P0)} rows match their source result files; gaps={gaps}")
    print(f"OK: aggregate result={document['result']} names all gaps and keeps C side open")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
