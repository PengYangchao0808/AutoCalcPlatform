"""Benchmark suite — multi-protocol comparison runner."""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conformer_search.core.specs import (
    BenchmarkSuite, ConformerWorkflowSpec, PROTOCOL_REGISTRY,
)

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result from running one protocol in a benchmark."""

    protocol: str
    n_conformers: int
    global_min_id: str | None
    global_min_energy: float | None
    walltime_s: float = 0.0
    n_opt: int = 0
    n_freq: int = 0
    n_sp: int = 0
    success: bool = True
    error: str | None = None


@dataclass
class BenchmarkRun:
    """Aggregated benchmark results."""

    level: str
    results: list[BenchmarkResult] = field(default_factory=list)
    reports_dir: Path | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "n_protocols": len(self.results),
            "successful": sum(1 for r in self.results if r.success),
            "failed": sum(1 for r in self.results if not r.success),
        }

    def write_report(self, output_dir: Path) -> None:
        out = output_dir / "benchmark"
        out.mkdir(parents=True, exist_ok=True)
        # Summary CSV
        with (out / "benchmark_summary.csv").open("w", newline="",
                                                   encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "protocol", "n_conformers", "global_min_id",
                "global_min_energy", "n_opt", "n_freq", "n_sp",
                "walltime_s", "success", "error",
            ])
            w.writeheader()
            for r in self.results:
                w.writerow({
                    "protocol": r.protocol,
                    "n_conformers": r.n_conformers,
                    "global_min_id": r.global_min_id or "",
                    "global_min_energy": r.global_min_energy or "",
                    "n_opt": r.n_opt,
                    "n_freq": r.n_freq,
                    "n_sp": r.n_sp,
                    "walltime_s": f"{r.walltime_s:.1f}",
                    "success": r.success,
                    "error": r.error or "",
                })
        # JSON summary
        (out / "benchmark_report.json").write_text(
            json.dumps({
                "summary": self.summary(),
                "results": [
                    {k: v for k, v in r.__dict__.items()}
                    for r in self.results
                ],
            }, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Benchmark report written to %s", out)


# Standard benchmark level definitions
BENCHMARK_LEVELS = {
    "quick": BenchmarkSuite(
        level="quick",
        protocols=["censo-zero", "censo-lite"],
        reference_protocol="censo-lite",
    ),
    "standard": BenchmarkSuite(
        level="standard",
        protocols=["censo-zero", "censo-lite", "censo-full-safe", "allopt"],
        reference_protocol="censo-full-safe",
    ),
    "strict": BenchmarkSuite(
        level="strict",
        protocols=["ext", "censo-zero", "censo-lite", "censo-full",
                   "censo-full-safe", "allopt", "reference-sp"],
        reference_protocol="censo-full",
    ),
}


def resolve_benchmark(
    level: str,
    custom_protocols: list[str] | None = None,
) -> BenchmarkSuite:
    if level in BENCHMARK_LEVELS:
        return BENCHMARK_LEVELS[level]
    if custom_protocols:
        for p in custom_protocols:
            if p not in PROTOCOL_REGISTRY:
                raise KeyError(f"Unknown protocol in benchmark: {p}")
        return BenchmarkSuite(level="custom", protocols=custom_protocols)
    raise ValueError(f"Unknown benchmark level: {level}")


__all__ = [
    "BenchmarkResult", "BenchmarkRun",
    "BENCHMARK_LEVELS", "resolve_benchmark",
]
