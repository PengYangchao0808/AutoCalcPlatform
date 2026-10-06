"""Benchmark harness orchestration (todo 50 / gap §10.3).

``run_harness`` loads a dataset manifest (typed validation), reuses the todo-48
spectra benchmark as the spectra-processing layer, evaluates every dataset with
the metric suite, compares against the pre-registered thresholds and emits one
canonical metrics JSON with full provenance:

* dataset manifest path + sha256, per-dataset declared hashes;
* threshold file sha256 + content hash + pre-registration timestamp;
* code provenance (``git rev-parse HEAD`` captured at run time + package
  version);
* run timestamp (injectable via ``now`` for deterministic replay);
* bootstrap settings (seeded, molecule-clustered).

Determinism contract: with ``now`` pinned and ``measure_resources=False`` the
whole payload is byte-identical across processes (canonical JSON, sorted keys,
no wall-clock reads).  With resource accounting enabled only the ``resources``
blocks and the timestamp are volatile.
"""

from __future__ import annotations

import hashlib
import resource
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.benchmark.nmr.bootstrap import DEFAULT_BOOTSTRAP_SEED, DEFAULT_N_RESAMPLES
from tests.benchmark.nmr.metrics import evaluate_dataset
from tests.benchmark.nmr.schema import load_dataset_manifest
from tests.benchmark.nmr.thresholds import compare_dataset_metrics, load_thresholds
from tests.nmr_spectra_benchmark import (
    NOT_VERIFIED,
    canonical_json_bytes,
    run_benchmark,
    write_metrics,
)

HARNESS_SCHEMA = "acp-nmr-benchmark-harness-metrics-v1"

_PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET_MANIFEST = _PACKAGE_DIR / "fixtures" / "synthetic_dataset.json"
DEFAULT_THRESHOLDS_PATH = _PACKAGE_DIR / "thresholds.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def _git_head() -> tuple[str | None, str]:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git_unavailable: {exc}"
    if completed.returncode != 0:
        return None, f"git_exit_{completed.returncode}"
    head = completed.stdout.strip()
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        return None, "unexpected_rev_parse_output"
    return head, "git rev-parse HEAD"


def _code_provenance() -> dict[str, Any]:
    head, source = _git_head()
    try:
        import acp  # noqa: PLC0415

        version = getattr(acp, "__version__", None)
    except ImportError:  # pragma: no cover - acp is importable in this repo
        version = None
    return {"git_head": head, "git_head_source": source, "package_version": version}


def _resource_snapshot(start_wall: float, start_cpu: tuple[float, float]) -> dict[str, Any]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "measured": True,
        "wall_seconds": round(time.perf_counter() - start_wall, 6),
        "cpu_user_seconds": round(usage.ru_utime - start_cpu[0], 6),
        "cpu_system_seconds": round(usage.ru_stime - start_cpu[1], 6),
        "peak_rss_bytes": int(usage.ru_maxrss) * 1024,
    }


def _resources_disabled() -> dict[str, Any]:
    return {"measured": False}


def _spectra_not_requested() -> dict[str, Any]:
    return {
        "status": "not_requested",
        "schema": None,
        "manifest": None,
        "nmrglue": None,
        "summary": None,
        "fixtures": [],
        "payload_sha256": None,
        "reasons": [],
    }


def _spectra_block() -> dict[str, Any]:
    """Compact, deterministic projection of the todo-48 spectra benchmark."""
    try:
        payload = run_benchmark()
    except (OSError, ValueError) as exc:
        return {
            "status": "not_verified",
            "schema": None,
            "manifest": None,
            "nmrglue": None,
            "summary": None,
            "fixtures": [],
            "payload_sha256": None,
            "reasons": [f"{NOT_VERIFIED}: spectra benchmark unavailable: {exc}"],
        }
    fixtures = [
        {
            "id": entry["id"],
            "status": entry["status"],
            "verification_status": entry["verification"]["status"],
            "gating_outcome": entry["gating"]["outcome"],
            "sha256": entry["sha256"],
        }
        for entry in payload["fixtures"]
    ]
    return {
        "status": "measured",
        "schema": payload["schema"],
        "manifest": payload["manifest"],
        "nmrglue": payload["nmrglue"],
        "summary": payload["summary"],
        "fixtures": fixtures,
        "payload_sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
        "reasons": [],
    }


def run_harness(
    dataset_manifest: str | Path = DEFAULT_DATASET_MANIFEST,
    thresholds_path: str | Path | None = DEFAULT_THRESHOLDS_PATH,
    *,
    include_spectra: bool = True,
    now: str | None = None,
    measure_resources: bool = True,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Run the full benchmark harness and return the canonical metrics JSON.

    Args:
        dataset_manifest: Validated dataset manifest path.
        thresholds_path: Pre-registered threshold file; ``None`` skips the
            comparison block explicitly (never silently).
        include_spectra: Run the todo-48 spectra benchmark and validate
            ``spectra_fixture_id`` links; ``False`` records a typed
            ``not_requested`` block and skips link resolution.
        now: ISO timestamp to record; ``None`` uses the current UTC time.
            Pinning it (with ``measure_resources=False``) makes the output
            byte-identical across processes.
        measure_resources: Account wall/CPU/peak-RSS; disable for
            deterministic replay.
        n_resamples: Cluster-bootstrap resample count.
        seed: Cluster-bootstrap seed (molecule-level resampling).
    """
    wall_start = time.perf_counter()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_start = (usage.ru_utime, usage.ru_stime)
    timestamp = now if now is not None else datetime.now(timezone.utc).isoformat(timespec="seconds")

    spectra = _spectra_block() if include_spectra else _spectra_not_requested()
    spectra_fixture_ids = (
        {str(entry["id"]) for entry in spectra["fixtures"]}
        if spectra["status"] == "measured"
        else None
    )
    manifest = load_dataset_manifest(dataset_manifest, spectra_fixture_ids=spectra_fixture_ids)
    manifest_path = Path(dataset_manifest)
    manifest_sha256 = _sha256_file(manifest_path)
    thresholds: dict[str, Any] | None = None
    thresholds_file_sha256: str | None = None
    if thresholds_path is not None:
        thresholds = load_thresholds(thresholds_path)
        thresholds_file_sha256 = _sha256_file(Path(thresholds_path))
    code = _code_provenance()

    datasets_out: list[dict[str, Any]] = []
    for dataset in manifest["datasets"]:
        dataset_wall = time.perf_counter()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        dataset_cpu = (usage.ru_utime, usage.ru_stime)
        block = evaluate_dataset(dataset, seed=seed, n_resamples=n_resamples)
        comparisons = compare_dataset_metrics(block, thresholds) if thresholds is not None else []
        block["thresholds"] = {
            "status": "measured" if thresholds is not None else "not_requested",
            "comparisons": comparisons,
        }
        block["provenance"] = {
            "dataset_hash": block["hash"],
            "dataset_manifest_sha256": manifest_sha256,
            "threshold_file_sha256": thresholds_file_sha256,
            "threshold_content_sha256": thresholds["content_sha256"] if thresholds else None,
            "git_head": code["git_head"],
            "timestamp": timestamp,
        }
        block["resources"] = (
            _resource_snapshot(dataset_wall, dataset_cpu)
            if measure_resources
            else _resources_disabled()
        )
        datasets_out.append(block)

    threshold_statuses = Counter(
        comparison["status"]
        for block in datasets_out
        for comparison in block["thresholds"]["comparisons"]
    )
    provenance: dict[str, Any] = {
        "dataset_manifest": {
            "path": _display_path(manifest_path),
            "sha256": manifest_sha256,
            "schema": manifest["schema"],
        },
        "datasets": {
            str(dataset["dataset_id"]): {
                "declared_hash": str(dataset["hash"]),
                "version": str(dataset["version"]),
                "source": str(dataset["source"]),
                "license": str(dataset["license"]),
            }
            for dataset in manifest["datasets"]
        },
        "thresholds": (
            {
                "path": _display_path(Path(thresholds_path)),
                "file_sha256": thresholds_file_sha256,
                "content_sha256": thresholds["content_sha256"],
                "preregistered_at": thresholds["preregistered_at"],
            }
            if thresholds is not None and thresholds_path is not None
            else None
        ),
        "code": code,
        "timestamp": timestamp,
        "bootstrap": {"seed": seed, "n_resamples": n_resamples, "unit": "molecule"},
    }
    summary = {
        "n_datasets": len(datasets_out),
        "n_items": sum(block["counts"]["n_items"] for block in datasets_out),
        "n_molecules": sum(block["counts"]["n_molecules"] for block in datasets_out),
        "n_candidates": sum(block["counts"]["n_candidates"] for block in datasets_out),
        "layers": sorted({block["layer"] for block in datasets_out}),
        "threshold_statuses": dict(sorted(threshold_statuses.items())),
    }
    return {
        "schema": HARNESS_SCHEMA,
        "provenance": provenance,
        "spectra": spectra,
        "datasets": datasets_out,
        "summary": summary,
        "resources": (
            _resource_snapshot(wall_start, cpu_start)
            if measure_resources
            else _resources_disabled()
        ),
    }


__all__ = [
    "DEFAULT_DATASET_MANIFEST",
    "DEFAULT_THRESHOLDS_PATH",
    "HARNESS_SCHEMA",
    "NOT_VERIFIED",
    "REPO_ROOT",
    "run_harness",
    "write_metrics",
]
