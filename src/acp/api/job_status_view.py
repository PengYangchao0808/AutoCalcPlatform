# pyright: reportAny=false, reportExplicitAny=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnnecessaryIsInstance=false, reportUnreachable=false
"""Pure projections for live job status and display methods."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Final

from acp.api.v1_schemas import JobLiveMetric, JobLiveStatus
from acp.core.stage_labels import stage_label

#: A live observation older than this many seconds is reported as stale.
#: A state file always carries ``observed_at``/``updated_at``; when the clock
#: cannot be read the projection reports ``None`` instead of guessing.
LIVE_STATUS_STALE_SECONDS: Final[float] = 90.0

_ALLOWED_METRIC_KINDS: Final[frozenset[str]] = frozenset(
    {"count", "iteration", "status", "text", "progress"}
)
_PES_SCAN_STAGES: Final[frozenset[str]] = frozenset(
    {"run_relaxed_scan", "prepare", "materialize_input", "validate_coordinate"}
)
_SIMPLE_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"singlepoint", "optimize", "frequency", "scan"}
)
_HTML_TAG_PATTERN: Final[re.Pattern[str]] = re.compile(r"<[^>]*>")


def _safe_string(value: Any) -> str | None:
    try:
        return str(value)
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        return None


def _clean_metric_value(value: Any) -> str | None:
    text = _safe_string(value)
    if text is None:
        return None
    text = _HTML_TAG_PATTERN.sub("", unescape(text))
    return text.replace("<", "").replace(">", "")[:48]


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _priority(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def normalize_live_metrics(raw: object) -> list[dict[str, Any]]:  # noqa: ANN401  # noqa: OBJECT_OK
    """Normalize untrusted live metrics into at most three plain dictionaries.

    Args:
        raw: Raw ``live_metrics`` content read from a state payload.

    Returns:
        Valid metrics with safe values, deterministic ordering, and the API field shape.
    """
    if not isinstance(raw, list):
        return []

    by_key: dict[str, dict[str, Any]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue

        key = entry.get("key")
        kind = entry.get("kind")
        if not isinstance(key, str) or not isinstance(kind, str):
            continue
        if kind not in _ALLOWED_METRIC_KINDS or "value" not in entry:
            continue

        value = _clean_metric_value(entry["value"])
        if value is None:
            continue

        by_key[key] = {
            "key": key,
            "label_key": _optional_text(entry.get("label_key")),
            "label": _optional_text(entry.get("label")),
            "value": value,
            "kind": kind,
            "priority": _priority(entry.get("priority", 0)),
            "detail": _optional_text(entry.get("detail")),
        }

    return sorted(by_key.values(), key=lambda metric: (-metric["priority"], metric["key"]))[:3]


def resolve_stage_label(stage_key: str | None) -> str | None:
    """Resolve a stage key to its localized display label.

    Args:
        stage_key: Internal workflow stage key, if one is available.

    Returns:
        The localized or humanized stage label, or ``None`` for no stage.
    """
    if not isinstance(stage_key, str) or not stage_key:
        return None
    return stage_label(stage_key)


def _stage_number(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an observation timestamp into an aware UTC datetime.

    Accepts ISO-8601 strings (``Z`` included), ``datetime`` values and epoch
    seconds.  Anything else is ``None``: freshness is never guessed.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _utc_now(now: datetime | None) -> datetime:
    if isinstance(now, datetime):
        return now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    return datetime.now(timezone.utc)


def build_live_status(
    state_data: Mapping[str, Any],
    *,
    now: datetime | None = None,
    source_mtime: str | datetime | float | None = None,
) -> JobLiveStatus | None:
    """Project a parsed state payload into the typed live-status response model.

    Freshness is computed here, on every call, from the payload's own
    ``observed_at``/``updated_at`` — a projection is never cached as fresh.

    Args:
        state_data: Parsed ``state.json`` mapping.
        now: Projection clock (UTC; naive values are read as UTC).  Defaults
            to the current time.
        source_mtime: The state file's mtime when the caller can stat it.  A
            file rewritten later than the moment its payload claims marks the
            observation stale: a newer write is not evidence of a newer
            observation, and the recorded one is never presented as current.

    Returns:
        A ``JobLiveStatus`` when a stage or metric is present; otherwise ``None``.
    """
    if not isinstance(state_data, Mapping):
        return None

    raw_stage = state_data.get("current_stage")
    stage_key = raw_stage if isinstance(raw_stage, str) else None
    stage_display = resolve_stage_label(stage_key)
    raw_metrics = state_data.get("live_metrics")
    metrics = [JobLiveMetric(**metric) for metric in normalize_live_metrics(raw_metrics)]
    if stage_display is None and not metrics:
        return None

    observed_at = _first_text(state_data, ("updated_at", "last_observed_at", "observed_at"))
    observed_moment = _parse_timestamp(observed_at)
    mtime_moment = _parse_timestamp(source_mtime) if source_mtime is not None else None

    observed_age_seconds: float | None = None
    stale: bool | None = None
    if observed_moment is not None:
        projection_now = _utc_now(now)
        observed_age_seconds = round(
            max(0.0, (projection_now - observed_moment).total_seconds()), 3
        )
        stale = observed_age_seconds > LIVE_STATUS_STALE_SECONDS
        if (
            mtime_moment is not None
            and (mtime_moment - observed_moment).total_seconds() > LIVE_STATUS_STALE_SECONDS
        ):
            # Defensive re-read: the file was rewritten later than the clock
            # reading it claims; do not reuse that reading as live.
            stale = True

    source = _first_text(state_data, ("live_source", "source"))
    return JobLiveStatus(
        stage_label=stage_display,
        stage_index=_stage_number(state_data.get("stage_index")),
        stage_total=_stage_number(state_data.get("stage_total")),
        metrics=metrics,
        observed_at=observed_at,
        source=source,
        unknown_reason=(
            "stage_unknown"
            if stage_display is None and metrics
            else "metrics_unavailable"
            if not metrics
            else None
        ),
        observed_age_seconds=observed_age_seconds,
        stale=stale,
        source_mtime=(
            mtime_moment.astimezone(timezone.utc).isoformat() if mtime_moment is not None else None
        ),
    )


def read_live_status(
    state_path: str | Path,
    *,
    now: datetime | None = None,
) -> JobLiveStatus | None:
    """Stat and re-read one ``state.json``, then project it with freshness.

    This is the defensive read path: the file is parsed from its current bytes
    on every call (a previously parsed payload is never reused), and its mtime
    travels with the projection as ``source_mtime``.  A cached projection can
    therefore never be served as live.

    Args:
        state_path: The workflow ``state.json`` to observe.
        now: Projection clock, forwarded to :func:`build_live_status`.

    Returns:
        The live projection, or ``None`` when the file is absent, unreadable
        or carries no stage/metric.
    """
    path = Path(state_path)
    try:
        stat = path.stat()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping):
        return None
    return build_live_status(raw, now=now, source_mtime=stat.st_mtime)


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _first_text(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        text = _text(mapping.get(key))
        if text is not None:
            return text
    return None


def _format_method(method: str | None, basis: str | None) -> str | None:
    if method is None:
        return None
    if basis is None:
        return method
    return f"{method} / {basis}"


def _pair(mapping: Mapping[str, Any], method_key: str, basis_key: str) -> str | None:
    return _format_method(_text(mapping.get(method_key)), _text(mapping.get(basis_key)))


def _pes_protocol(
    spec_method: Mapping[str, Any], spec_input: Mapping[str, Any]
) -> Mapping[str, Any]:
    for spec in (spec_input, spec_method):
        scan_request = _mapping(spec.get("scan_request"))
        if scan_request is None:
            continue
        protocol = _mapping(scan_request.get("protocol"))
        if protocol is not None:
            return protocol
    return {}


def _pes_method(
    spec_method: Mapping[str, Any], spec_input: Mapping[str, Any], current_stage: str | None
) -> str | None:
    protocol = _pes_protocol(spec_method, spec_input)
    single_point = _mapping(protocol.get("single_point")) or {}
    scan_optimizer = _mapping(protocol.get("scan_optimizer")) or {}

    if isinstance(current_stage, str) and current_stage in _PES_SCAN_STAGES:
        scan_method = _text(scan_optimizer.get("method"))
        if scan_method is not None:
            return _format_method(scan_method, _text(scan_optimizer.get("basis")))
    return _pair(single_point, "method", "basis")


def _batch_method(spec_method: Mapping[str, Any], current_stage: str | None) -> str | None:
    if current_stage == "single_point":
        method = _first_text(spec_method, ("single_point_method", "optimization_method"))
        basis = _first_text(spec_method, ("single_point_basis", "optimization_basis"))
        return _format_method(method, basis)
    return _pair(spec_method, "optimization_method", "optimization_basis")


def _simple_method(spec_method: Mapping[str, Any], workflow: str) -> str | None:
    levels = _mapping(spec_method.get("levels"))
    if levels is None:
        return None

    selected: Mapping[str, Any] | None = None
    for level_key in (workflow, "optfreq"):
        candidate = _mapping(levels.get(level_key))
        if candidate is not None:
            selected = candidate
            break
    if selected is None:
        for candidate in levels.values():
            selected = _mapping(candidate)
            if selected is not None:
                break
    if selected is None:
        return None
    return _pair(selected, "functional", "basis")


def _irc_method(spec_method: Mapping[str, Any]) -> str | None:
    levels = _mapping(spec_method.get("levels"))
    irc_level = _mapping(levels.get("irc")) if levels is not None else None
    method = _first_text(spec_method, ("method", "functional"))
    if method is None and irc_level is not None:
        method = _text(irc_level.get("method"))

    basis = _text(spec_method.get("basis"))
    if basis is None and irc_level is not None:
        basis = _text(irc_level.get("basis"))
    return _format_method(method, basis)


def resolve_display_method(
    spec_method: Mapping[str, Any],
    spec_input: Mapping[str, Any],
    workflow: str,
    current_stage: str | None,
) -> str | None:
    """Resolve the method pair shown for the current workflow stage.

    Args:
        spec_method: Method section from the scheduler job specification.
        spec_input: Input section from the scheduler job specification.
        workflow: Workflow identifier.
        current_stage: Current internal stage identifier, if known.

    Returns:
        A ``method / basis`` string, a method-only string, or ``None``.
    """
    if not isinstance(workflow, str):
        return None

    method_spec = _mapping(spec_method) or {}
    input_spec = _mapping(spec_input) or {}

    if workflow == "PESsearch":
        return _pes_method(method_spec, input_spec, current_stage)
    if workflow == "BatchOptimize":
        return _batch_method(method_spec, current_stage)
    if workflow in _SIMPLE_WORKFLOWS:
        return _simple_method(method_spec, workflow)
    if workflow == "irc":
        return _irc_method(method_spec)
    return _pair(method_spec, "method", "basis")


__all__ = [
    "LIVE_STATUS_STALE_SECONDS",
    "build_live_status",
    "normalize_live_metrics",
    "read_live_status",
    "resolve_display_method",
    "resolve_stage_label",
]
