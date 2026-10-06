#!/usr/bin/env python3
"""Run the ACP NMR benchmark harness (todo 50 / gap §10.3).

Loads a dataset manifest (typed provenance/hash validation), evaluates the
layered metric suite — reusing the todo-48 spectra benchmark as the
spectra-processing layer — compares against the pre-registered threshold file
(content-hash verified) and writes one canonical metrics JSON with full
provenance (dataset hash, threshold hash, git revision, timestamp).

No QC binaries run here: the dataset manifest supplies candidate predictions.
With ``--now`` pinned and ``--no-resources`` the output is byte-identical
across processes (deterministic replay).

Usage:
    python3 scripts/nmr_benchmark.py
    python3 scripts/nmr_benchmark.py --dataset-manifest dataset.json --output metrics.json
    python3 scripts/nmr_benchmark.py --no-spectra --no-resources --now 2026-10-06T00:00:00+00:00

Exit status: 0 on success (``not_verified`` is not a failure); 1 when a
threshold comparison reports ``fail`` (override with
``--allow-threshold-failures``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.benchmark.nmr.bootstrap import (  # noqa: E402
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_RESAMPLES,
)
from tests.benchmark.nmr.harness import (  # noqa: E402
    DEFAULT_DATASET_MANIFEST,
    DEFAULT_THRESHOLDS_PATH,
    run_harness,
)
from tests.nmr_spectra_benchmark import canonical_json_bytes, write_metrics  # noqa: E402


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": payload["schema"],
        "datasets": [block["dataset_id"] for block in payload["datasets"]],
        "summary": payload["summary"],
        "comparisons": [
            {
                "dataset_id": block["dataset_id"],
                "metric": comparison["metric"],
                "op": comparison["op"],
                "threshold": comparison["threshold"],
                "observed": comparison["observed"],
                "status": comparison["status"],
                "reasons": comparison["reasons"],
            }
            for block in payload["datasets"]
            for comparison in block["thresholds"]["comparisons"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-manifest",
        default=str(DEFAULT_DATASET_MANIFEST),
        help="validated dataset manifest JSON (default: the synthetic self-test fixture)",
    )
    parser.add_argument(
        "--thresholds",
        default=str(DEFAULT_THRESHOLDS_PATH),
        help="pre-registered threshold file (content-hash verified)",
    )
    parser.add_argument(
        "--output",
        help="write the canonical metrics JSON here (stdout when omitted)",
    )
    parser.add_argument(
        "--no-spectra",
        action="store_true",
        help="skip the todo-48 spectra layer (records a typed not_requested block)",
    )
    parser.add_argument(
        "--no-resources",
        action="store_true",
        help="disable wall/CPU/RSS accounting (use for deterministic replay)",
    )
    parser.add_argument(
        "--now",
        help="pin the recorded timestamp (ISO 8601) for deterministic replay",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument("--resamples", type=int, default=DEFAULT_N_RESAMPLES)
    parser.add_argument(
        "--allow-threshold-failures",
        action="store_true",
        help="exit 0 even when a threshold comparison reports fail",
    )
    args = parser.parse_args(argv)

    payload = run_harness(
        args.dataset_manifest,
        args.thresholds,
        include_spectra=not args.no_spectra,
        now=args.now,
        measure_resources=not args.no_resources,
        n_resamples=args.resamples,
        seed=args.seed,
    )
    failures = sum(
        1
        for block in payload["datasets"]
        for comparison in block["thresholds"]["comparisons"]
        if comparison["status"] == "fail"
    )
    if args.output:
        target = write_metrics(payload, args.output)
        summary = _summary(payload)
        summary["output"] = str(target.resolve())
        summary["threshold_failures"] = failures
        json.dump(summary, sys.stdout, indent=2, ensure_ascii=True)
        sys.stdout.write("\n")
    else:
        sys.stdout.buffer.write(canonical_json_bytes(payload))
    if failures and not args.allow_threshold_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
