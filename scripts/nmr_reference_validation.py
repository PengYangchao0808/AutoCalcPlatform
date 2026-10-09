#!/usr/bin/env python3
"""Reproducible reference-validation check (todo 31 / gap G04 §10.3 试验 A).

Compares a pinned reference dataset — by default the fixed synthetic methanol
fixture embedded below — against optional migrated ACP results, and prints the
side-by-side table, per-item differences and aggregate statistics as JSON.

The default fixture values are SYNTHETIC and clearly labelled as such: they
exercise the comparison math for manual QA; they are NOT literature values and
must never be quoted as such.

Usage:
    python3 scripts/nmr_reference_validation.py
    python3 scripts/nmr_reference_validation.py --reference ref.json
    python3 scripts/nmr_reference_validation.py --migrated migrated.json

``--reference`` expects a ``ReferenceDataset`` JSON (``as_dict`` shape);
``--migrated`` expects a flat ``{"<item_id>": <value>}`` JSON mapping.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from acp.nmr.reference_validation import (  # noqa: E402
    ReferenceDataset,
    ReferenceRecord,
    assess_reference_dataset,
    asset_hashes,
    compare_reference_vs_migration,
    pinned_goodman_upstream,
)

DEFAULT_REFERENCE = ReferenceDataset(
    dataset_id="methanol-synthetic-pin-v1",
    source="synthetic fixture for manual QA (NOT a literature reference)",
    structure_identity="methanol (CH3OH) — fixed synthetic atom order C1/H1-H4",
    units="ppm",
    value_kind="shift",
    records=(
        ReferenceRecord(item_id="C1", nucleus="13C", value=49.0, source="synthetic"),
        ReferenceRecord(item_id="H1", nucleus="1H", value=3.31, source="synthetic"),
        ReferenceRecord(item_id="H2", nucleus="1H", value=3.31, source="synthetic"),
        ReferenceRecord(item_id="H3", nucleus="1H", value=3.31, source="synthetic"),
        ReferenceRecord(item_id="H4", nucleus="1H", value=3.70, source="synthetic"),
    ),
)
DEFAULT_MIGRATED = {"C1": 48.5, "H1": 3.35, "H2": 3.33, "H4": 3.62}


def _load_reference(path: str | None) -> ReferenceDataset:
    if path is None:
        return DEFAULT_REFERENCE
    payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    return ReferenceDataset.from_dict(payload)


def _load_migrated(path: str | None) -> dict[str, float]:
    if path is None:
        return dict(DEFAULT_MIGRATED)
    payload: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("--migrated must be a JSON object {item_id: value}")
    return {str(key): float(value) for key, value in payload.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", help="ReferenceDataset JSON (as_dict shape)")
    parser.add_argument("--migrated", help="flat migrated-values JSON {item_id: value}")
    args = parser.parse_args(argv)

    reference = _load_reference(args.reference)
    availability = assess_reference_dataset(reference)
    comparison = compare_reference_vs_migration(reference, _load_migrated(args.migrated))
    payload = {
        "notice": "synthetic fixture values are NOT literature references",
        "pinned_upstream": pinned_goodman_upstream().as_dict(),
        "reference": reference.as_dict(),
        "availability": availability.as_dict(),
        "comparison": comparison.as_dict(),
        "asset_hashes": asset_hashes(),
    }
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
