"""Display-only QC runtime metrics extraction from job stdout logs.

The scheduler writes job stdout to ``<work_dir>/stdout.log`` but never
parses it. This module incrementally tails that file, matches a small
registry of engine output patterns, and persists a throttled
``metrics.json`` sidecar so the Workbench info panel can show live
runtime indicators (current energy / optimization cycle / convergence).

The extractor is intentionally display-only: it never gates control flow
(resume/purge/cleanup must not depend on it), it never raises, and
unmatched output simply yields absent metrics.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_METRICS_FILENAME = "metrics.json"
_WRITE_THROTTLE_SECONDS = 5.0

# Pattern registry as data (not per-engine code). Each entry maps a metric
# key to a compiled regex; the first match group is used when present.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("orca_energy", re.compile(r"FINAL SINGLE POINT ENERGY\s+(-?[\d.]+)")),
    ("orca_opt_done", re.compile(r"OPTIMIZATION RUN DONE")),
    ("xtb_energy", re.compile(r"TOTAL ENERGY\s+(-?[\d.]+)")),
]


class MetricsExtractor:
    """Incremental stdout.log tail reader producing a metrics sidecar.

    Tracks the last-read byte offset per job so repeated calls only scan
    newly appended output. Writes are throttled and atomic (tmp + replace).
    """

    def __init__(self, throttle_seconds: float = _WRITE_THROTTLE_SECONDS):
        self.throttle_seconds = throttle_seconds
        self._offsets: dict[str, int] = {}
        self._last_writes: dict[str, float] = {}

    def extract(self, job_id: str, work_dir: Path) -> dict[str, Any] | None:
        """Tail ``stdout.log`` and refresh ``metrics.json`` if changed.

        Returns the merged metrics dict when a write happened, else ``None``.
        Never raises: any I/O or parse error is logged and swallowed.
        """
        stdout_path = Path(work_dir) / "stdout.log"
        try:
            if not stdout_path.exists():
                return None
            offset = self._offsets.get(job_id, 0)
            size = stdout_path.stat().st_size
            if size < offset:
                offset = 0  # file truncated (e.g. rerun-in-place)
            with stdout_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                self._offsets[job_id] = handle.tell()
        except OSError as exc:
            logger.debug("metrics extract skipped for job %s: %s", job_id, exc)
            return None

        metrics_path = Path(work_dir) / _METRICS_FILENAME
        current: dict[str, Any] = {}
        try:
            if metrics_path.exists():
                current = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}

        changed = self._apply_chunk(current, chunk)
        now = time.time()
        last = self._last_writes.get(job_id, 0.0)
        if not changed or now - last < self.throttle_seconds:
            return None
        current["updated_at"] = _iso_now()
        try:
            tmp = metrics_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
            os.replace(tmp, metrics_path)
            self._last_writes[job_id] = now
            return current
        except OSError as exc:
            logger.debug("metrics write failed for job %s: %s", job_id, exc)
            return None

    def _apply_chunk(self, metrics: dict[str, Any], chunk: str) -> bool:
        """Apply pattern matches from ``chunk`` into ``metrics``; True if changed."""
        changed = False
        for key, pattern in _PATTERNS:
            for match in pattern.finditer(chunk):
                if key == "orca_opt_done":
                    metrics["opt_converged"] = True
                elif key == "orca_energy":
                    metrics["last_energy_hartree"] = float(match.group(1))
                    metrics["engine"] = "orca"
                elif key == "xtb_energy":
                    metrics["last_energy_hartree"] = float(match.group(1))
                    metrics["engine"] = "xtb"
                changed = True
        return changed


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


__all__ = ["MetricsExtractor"]
