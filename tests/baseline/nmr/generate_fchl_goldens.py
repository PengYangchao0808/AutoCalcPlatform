#!/usr/bin/env python3.11
"""Layered FCHL golden generator (plan todo 36; gap §10.2/§10.3 trial A).

Regenerates ``fchl_golden.json`` from the current implementation + the
NOTICE-pinned assets.  The layer computation lives in
``tests/test_acp_nmr_fchl_golden.py::compute_layer_values`` and is imported
here so the committed golden and the assertions can never drift into two
implementations.

Determinism contract: sorted-key JSON, no timestamps, no machine paths; two
generations at the same commit MUST be byte-identical (verified by running
this script twice and comparing bytes).  Floats are stored at full ``repr``
precision because the layered assertions compare with frozen tolerances
instead of pre-rounding the golden (rounding would hide sub-1e-10 drift).

Measurement mode (default) computes every layer twice in-process and reports
the per-layer max absolute repeat deviation on stdout — this is the raw
repeatability measurement the tolerances are frozen from.  The measurement
block in ``fchl_golden_tolerances.json`` records the procedure and results.

Usage (from the repo root):

    PYTHONPATH=src python3.11 tests/baseline/nmr/generate_fchl_goldens.py
    PYTHONPATH=src python3.11 tests/baseline/nmr/generate_fchl_goldens.py --out-dir /tmp/g1
    PYTHONPATH=src python3.11 tests/baseline/nmr/generate_fchl_goldens.py --check

Never ``git add`` a regenerated golden without reading the diff and updating
``fchl_golden_tolerances.json`` (and the task evidence) deliberately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[3]
for entry in (str(REPO_ROOT), str(REPO_ROOT / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tests.test_acp_nmr_fchl_golden import (  # noqa: E402
    ASSET_SHA256,
    DP5_REVISION,
    compute_layer_values,
    golden_inputs_metadata,
)

GOLDENS_DIR = Path(__file__).resolve().parent
GOLDEN_NAME = "fchl_golden.json"
SCHEMA = "acp_nmr_fchl_layers_golden_v1"


def _max_abs_delta(a: Any, b: Any) -> float:
    """Recursive max |a - b| over nested dict/list/numeric structures."""
    if isinstance(a, dict) and isinstance(b, dict):
        return max((_max_abs_delta(a[k], b[k]) for k in a), default=0.0)
    if isinstance(a, list) and isinstance(b, list):
        return max((_max_abs_delta(x, y) for x, y in zip(a, b)), default=0.0)
    return float(np.max(np.abs(np.asarray(a, dtype=float) - np.asarray(b, dtype=float))))


def _payload(layer_values: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "dp5_revision": DP5_REVISION,
        "asset_sha256": dict(ASSET_SHA256),
        "runtime_note": (
            "probability_per_conformer_fchl_reduced32 uses the first 32 training "
            "atoms (and first 64 folded residuals) of the NOTICE-pinned assets: "
            "the pure-numpy kernel costs ~25 min per query atom on the full "
            "53208-atom set. Layer 2 anchors the real kernel on real training "
            "data; layer 3 anchors the full-residual KDE."
        ),
        "inputs": golden_inputs_metadata(),
        "golden": layer_values,
    }


def _dump(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=GOLDENS_DIR)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate and compare byte-for-byte with the committed golden",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    target = args.out_dir / GOLDEN_NAME

    with tempfile.TemporaryDirectory(prefix="fchl-golden-") as tmp:
        workdir = Path(tmp)
        if args.check:
            regenerated = _payload(compute_layer_values(workdir))
            text = json.dumps(regenerated, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            if not target.exists():
                print(f"MISSING golden: {target}", file=sys.stderr)
                return 1
            committed = target.read_text(encoding="utf-8")
            if text != committed:
                print(
                    "CHECK FAILED: regenerated golden differs from committed bytes", file=sys.stderr
                )
                return 1
            print(f"CHECK OK: {target} sha256={hashlib.sha256(text.encode()).hexdigest()}")
            return 0

        # Measurement: two independent in-process computations.
        first = _payload(compute_layer_values(workdir))
        second = _payload(compute_layer_values(workdir))
        deltas = {
            key: _max_abs_delta(first["golden"][key], second["golden"][key])
            for key in first["golden"]
        }
        _dump(target, first)

    text = target.read_text(encoding="utf-8")
    report = {
        "schema": "acp_nmr_fchl_golden_measurement_v1",
        "golden": str(target),
        "golden_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "command": ("PYTHONPATH=src python3.11 tests/baseline/nmr/generate_fchl_goldens.py"),
        "repeats_in_process": 2,
        "max_abs_dev_per_layer": deltas,
        "observed_in_process_max_abs_dev": max(deltas.values()),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
