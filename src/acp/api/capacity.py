"""Admission-view capacity projection for agent resource selection.

Agents must choose ``nproc``/``mem`` without guessing.  This module is a *pure
projection over the existing resource authorities* — it never re-derives a
resource rule of its own:

* the strategy ceilings come from
  :class:`acp_services.runtime.local_admission.AdmissionLimits` (the same
  ``ACP_ADMISSION_*`` definitions admission enforces);
* the callable pool comes from the admission controller's ``Capacity`` view
  (``controller.capacity()`` — cores/memory/concurrency/source/available);
* per-job reservations are normalised by
  :func:`acp.scheduler.resources.resolve_job_resources` and
  :func:`cccp.utils.resource_utils.parse_memory_mb` (the single memory parser);
* concurrency slots use the manager's own ``ACP_MAX_RUNNING``.

It reports the **admission view** — what the Controller can admit right now —
not an OS-idle snapshot: other processes share the host and the admission
ceilings are the binding numbers.  A missing authority becomes an explicit
``unknown`` entry, never a fabricated zero.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from acp.scheduler.resources import resolve_job_resources
from cccp.utils.resource_utils import parse_memory_mb

logger = logging.getLogger(__name__)

__all__ = ["build_capacity_projection"]

_SCHEMA = "acp2.capacity.view.v1"
_ACTIVE_STATES = ("queued", "running", "paused", "waiting_review")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _admission_view(admission: Any) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Return (limits, pool, unknown) from the admission authority."""
    unknown: list[str] = []
    limits_obj = getattr(admission, "limits", None)
    limits: dict[str, Any] = {}
    if limits_obj is not None:
        limits = {
            "max_cores": getattr(limits_obj, "max_cores", None),
            "max_memory_mb": getattr(limits_obj, "max_memory_mb", None),
            "max_tasks": getattr(limits_obj, "max_tasks", None),
            "max_walltime_s": getattr(limits_obj, "max_walltime_s", None),
            "max_storage_bytes": getattr(limits_obj, "max_storage_bytes", None),
        }
    else:
        unknown.append("limits")

    pool: dict[str, Any] = {}
    controller = getattr(admission, "controller", None)
    capacity_obj = None
    if controller is not None:
        try:
            capacity_obj = controller.capacity()
        except Exception:  # noqa: BLE001 - projection must survive a probe failure
            logger.warning("capacity projection: admission pool read failed", exc_info=True)
    if capacity_obj is not None:
        pool = {
            "cores": getattr(capacity_obj, "cores", None),
            "memory_mb": getattr(capacity_obj, "memory_mb", None),
            "concurrency": getattr(capacity_obj, "concurrency", None),
            "storage_bytes": getattr(capacity_obj, "storage_bytes", None),
            "source": getattr(capacity_obj, "source", None),
            "available": getattr(capacity_obj, "available", None),
        }
    else:
        # No pool authority: fall back to the strategy ceilings, labelled.
        unknown.append("pool")
        pool = {
            "cores": limits.get("max_cores"),
            "memory_mb": limits.get("max_memory_mb"),
            "concurrency": limits.get("max_tasks"),
            "storage_bytes": limits.get("max_storage_bytes"),
            "source": "admission_limits_fallback" if limits else None,
            "available": bool(limits),
        }
    return limits, pool, unknown


def _active_reservations(manager: Any) -> tuple[int, int, dict[str, int], list[str]]:
    """Sum the reservations of active jobs through the shared resolver."""
    unknown: list[str] = []
    store = getattr(manager, "store", None)
    counts: dict[str, int] = {}
    if store is None:
        return 0, 0, counts, ["in_use"]
    try:
        counts = dict(store.counts())
    except Exception:  # noqa: BLE001
        logger.warning("capacity projection: status scan failed", exc_info=True)
        return 0, 0, counts, ["in_use"]
    cores = 0
    memory_mb = 0
    for state in _ACTIVE_STATES:
        if not counts.get(state):
            continue
        try:
            records = store.list(status=state, limit=1000)
        except Exception:  # noqa: BLE001
            logger.warning("capacity projection: %s scan failed", state, exc_info=True)
            unknown.append("in_use")
            continue
        for record in records:
            spec = getattr(record, "spec", None)
            if spec is None:
                continue
            try:
                resolved = resolve_job_resources(spec)
            except (ValueError, AttributeError):
                continue
            nproc = resolved.get("nproc")
            if isinstance(nproc, (int, float)) and not isinstance(nproc, bool):
                cores += int(nproc)
            mem = resolved.get("mem")
            if mem is not None:
                try:
                    memory_mb += int(round(parse_memory_mb(mem)))
                except (TypeError, ValueError):
                    continue
    return cores, memory_mb, counts, unknown


def build_capacity_projection(manager: Any, *, admission: Any = None) -> dict[str, Any]:
    """Project the admission view onto a capacity block.

    Args:
        manager: The scheduler ``JobManager`` (``store`` + ``max_running``).
        admission: The Controller's ``LocalAdmission``; without it the strategy
            ceilings are reported as ``unknown`` (never invented).
    """
    limits, pool, unknown = _admission_view(admission)
    in_use_cores, in_use_mem_mb, counts, scan_unknown = _active_reservations(manager)
    unknown.extend(scan_unknown)

    total_cores = pool.get("cores")
    total_mem_mb = pool.get("memory_mb")
    available_cores = (
        None if total_cores is None else max(0, int(total_cores) - in_use_cores)
    )
    available_mem_mb = (
        None if total_mem_mb is None else max(0, int(total_mem_mb) - in_use_mem_mb)
    )

    running = int(counts.get("running", 0))
    concurrency: dict[str, Any] = {
        "running": running,
        "queued": int(counts.get("queued", 0)),
        "paused": int(counts.get("paused", 0)),
        "waiting_review": int(counts.get("waiting_review", 0)),
    }
    max_tasks = pool.get("concurrency")
    if isinstance(max_tasks, int) and max_tasks > 0:
        concurrency["admission_max_tasks"] = max_tasks
    else:
        unknown.append("concurrency.admission_max_tasks")
    # Round-6: slots must reflect the limit admission actually enforces
    # (registry.local.max_jobs), not the legacy max_running knob that no longer
    # caps local concurrency (round-5 PR-08 published it as legacy_noop).
    registry = getattr(manager, "registry", None)
    local = getattr(registry, "local", None)
    effective_slots = getattr(local, "max_jobs", None)
    if isinstance(effective_slots, int) and effective_slots > 0:
        concurrency["max_running"] = effective_slots
        concurrency["max_running_source"] = "registry.local.max_jobs"
        concurrency["slots_free"] = max(0, effective_slots - running)
    else:
        legacy_slots = getattr(manager, "max_running", None)
        if isinstance(legacy_slots, int) and legacy_slots > 0:
            concurrency["max_running"] = legacy_slots
            concurrency["max_running_source"] = "manager.max_running"
            concurrency["slots_free"] = max(0, legacy_slots - running)
        else:
            unknown.append("concurrency.max_running")

    return {
        "schema": _SCHEMA,
        "semantics": (
            "admission-view: what the Controller can admit right now "
            "(AdmissionLimits/Capacity minus active reservations) — not an "
            "OS-idle snapshot"
        ),
        "source": (
            "acp_services.runtime.local_admission (AdmissionLimits + "
            "controller.capacity()) + scheduler resources.resolve_job_resources "
            "+ manager.max_running"
        ),
        "observed_at": _iso_now(),
        "limits": limits,
        "pool": pool,
        "in_use": {
            "cores": in_use_cores,
            "memory_mb": in_use_mem_mb,
            "active_jobs": sum(int(counts.get(state, 0)) for state in _ACTIVE_STATES),
        },
        "available": {"cores": available_cores, "memory_mb": available_mem_mb},
        "concurrency": concurrency,
        "guidance": (
            "choose nproc/mem <= available.*, never above limits.*; "
            "over-quota requests queue or are refused"
        ),
        "unknown": sorted(set(unknown)),
    }
