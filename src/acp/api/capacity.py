"""Admission-view capacity projection for agent resource selection.

Agents must choose ``nproc``/``mem`` without guessing.  What the platform can
honestly report is *its own admission view*: the node's total capacity, the
resources already reserved by active jobs, the free quota left, and the
concurrency slots.  It is deliberately **not** an OS-idle snapshot — other
processes share the machine and the deployment ceilings (admission policy,
cgroup) are the binding numbers.

Every field either carries a measured value with a source or an explicit
``unknown`` entry; a probe failure never degrades into a zero/None that a
client could mistake for "nothing free" (U04 resource semantics).
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["build_capacity_projection"]

_SCHEMA = "acp2.capacity.view.v1"
_ACTIVE_STATES = ("queued", "running", "paused", "waiting_review")
_CGROUP_V2_ROOT = Path("/sys/fs/cgroup")
_MEM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGT]i?B?|B)?\s*$", re.IGNORECASE)
_MEM_UNIT_MB = {
    None: 1,
    "B": 1.0 / (1024 * 1024),
    "K": 1.0 / 1024,
    "KB": 1.0 / 1024,
    "KIB": 1.0 / 1024,
    "M": 1.0,
    "MB": 1.0,
    "MIB": 1.0,
    "G": 1024.0,
    "GB": 1024.0,
    "GIB": 1024.0,
    "T": 1024.0 * 1024,
    "TB": 1024.0 * 1024,
    "TIB": 1024.0 * 1024,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _parse_mem_mb(value: Any) -> int | None:
    """Parse a resource memory value (``8GB``, ``4096``, ``4GiB``) into MiB."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    match = _MEM_RE.match(value)
    if match is None:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").upper() or None
    factor = _MEM_UNIT_MB.get(unit)
    if factor is None:
        return None
    return int(amount * factor)


def _proc_meminfo_total_mb() -> int | None:
    text = _read_text(Path("/proc/meminfo"))
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            match = re.search(r"(\d+)\s*kB", line)
            if match:
                return int(int(match.group(1)) / 1024)
    return None


def _cgroup_v2_limits() -> dict[str, int | None]:
    """Read the delegated cgroup v2 ceilings (``None`` = unavailable/unlimited)."""
    out: dict[str, int | None] = {"cpu_cores": None, "memory_mb": None}
    cpu_max = _read_text(_CGROUP_V2_ROOT / "cpu.max")
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) == 2 and parts[0] != "max" and parts[1].isdigit() and int(parts[1]) > 0:
            try:
                out["cpu_cores"] = max(1, int(int(parts[0]) / int(parts[1])))
            except (ValueError, ZeroDivisionError):
                pass
    mem_max = _read_text(_CGROUP_V2_ROOT / "memory.max")
    if mem_max and mem_max != "max" and mem_max.isdigit():
        out["memory_mb"] = int(int(mem_max) / (1024 * 1024))
    return out


def _spec_cores(resources: Mapping[str, Any] | None) -> int:
    data = dict(resources or {})
    for key in ("nproc", "ncores", "cores"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return 0


def _spec_mem_mb(resources: Mapping[str, Any] | None) -> int:
    data = dict(resources or {})
    for key in ("mem", "memory"):
        parsed = _parse_mem_mb(data.get(key))
        if parsed is not None:
            return parsed
    for key in ("memory_gb", "mem_gb"):
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(float(value) * 1024)
    return 0


def build_capacity_projection(manager: Any) -> dict[str, Any]:
    """Return the Controller's admission-view capacity (read-only, cheap).

    Args:
        manager: The scheduler ``JobManager`` (uses ``store`` only; no I/O
            beyond /proc + cgroup reads and one SQLite status scan).
    """
    unknown: list[str] = []
    physical_cores = os.cpu_count() or None
    if physical_cores is None:
        unknown.append("total.cores")
    cg = _cgroup_v2_limits()
    total_cores = physical_cores
    ceilings: list[int] = [value for value in (physical_cores, cg.get("cpu_cores")) if value]
    if ceilings:
        total_cores = min(ceilings)
    phys_mem_mb = _proc_meminfo_total_mb()
    total_mem_mb = phys_mem_mb
    mem_ceilings = [value for value in (phys_mem_mb, cg.get("memory_mb")) if value]
    if mem_ceilings:
        total_mem_mb = min(mem_ceilings)
    if total_mem_mb is None:
        unknown.append("total.memory_mb")

    store = getattr(manager, "store", None)
    active: list[Any] = []
    counts: dict[str, int] = {}
    if store is not None:
        try:
            counts = dict(store.counts())
            for state in _ACTIVE_STATES:
                if counts.get(state):
                    active.extend(store.list(status=state, limit=1000))
        except Exception:  # noqa: BLE001 - capacity is advisory; never break discovery
            logger.warning("capacity projection: active-job scan failed", exc_info=True)
            unknown.append("in_use")
    else:
        unknown.append("in_use")

    in_use_cores = 0
    in_use_mem_mb = 0
    for record in active:
        resources = getattr(getattr(record, "spec", None), "resources", None)
        in_use_cores += _spec_cores(resources)
        in_use_mem_mb += _spec_mem_mb(resources)

    max_running = getattr(manager, "max_running", None)
    running = int(counts.get("running", 0))
    concurrency: dict[str, Any] = {
        "running": running,
        "queued": int(counts.get("queued", 0)),
        "paused": int(counts.get("paused", 0)),
        "waiting_review": int(counts.get("waiting_review", 0)),
    }
    if isinstance(max_running, int) and max_running > 0:
        concurrency["max_running"] = max_running
        concurrency["slots_free"] = max(0, max_running - running)
    else:
        unknown.append("concurrency.max_running")

    available_cores = None if total_cores is None else max(0, total_cores - in_use_cores)
    available_mem_mb = None if total_mem_mb is None else max(0, total_mem_mb - in_use_mem_mb)

    return {
        "schema": _SCHEMA,
        "semantics": (
            "admission-view available quota the Controller can admit now — "
            "not an OS-idle snapshot; other processes share the host and the "
            "cgroup/admission ceilings are the binding numbers"
        ),
        "source": (
            "controller: os.cpu_count + /proc/meminfo + cgroup v2 ceilings + "
            "active job reservations"
        ),
        "observed_at": _iso_now(),
        "total": {"cores": total_cores, "memory_mb": total_mem_mb},
        "in_use": {
            "cores": in_use_cores,
            "memory_mb": in_use_mem_mb,
            "active_jobs": len(active),
        },
        "available": {"cores": available_cores, "memory_mb": available_mem_mb},
        "concurrency": concurrency,
        "guidance": (
            "choose nproc/mem at or below available.* and never above "
            "admission.limits; a request above the free quota queues or is "
            "refused by admission"
        ),
        "unknown": sorted(set(unknown)),
    }
