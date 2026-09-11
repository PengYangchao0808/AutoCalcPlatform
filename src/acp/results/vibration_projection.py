"""Shared vibration-source discovery for catalog + endpoint.

Provides a single 3-tier resolution path used by both the structure-viewer
catalog (``probe_vibration_projection``) and the vibrations API endpoint
(``find_vibration_source``) so that availability decisions never diverge.

Resolution tiers (identical to the former endpoint-only logic):
  1. Per-item product ``RESULT/frequencies/{item_id}__normal_modes.json``
  2. Global product ``RESULT/frequencies/normal_modes.json``
  3. Historical ORCA outputs under ``WORK/04_FREQ/`` (+ batch subpaths)

Performance: the catalog is a hot path (rebuilt per poll).  Historical ORCA
files are parsed at most once per ``(path, mtime_ns, size)`` identity and
the result is reused for BOTH the mode-presence decision and the imaginary
count.  An ``OrderedDict``-based LRU with ``_CACHE_MAX`` entries prevents
unbounded memory growth.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "VibrationSource",
    "VibrationProjection",
    "find_vibration_source",
    "probe_vibration_projection",
]


# ---------------------------------------------------------------------------
# Discriminated source result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VibrationSource:
    """Resolved location of vibration data.

    Attributes:
        kind: ``"product"`` (JSON file) or ``"historical"`` (ORCA output).
        path: Absolute filesystem path to the source file.
    """

    kind: str  # "product" | "historical"
    path: Path


# ---------------------------------------------------------------------------
# Catalog probe result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VibrationProjection:
    """Lightweight vibration availability probe for the catalog.

    Attributes:
        available: Whether vibration data exists.
        source: ``"product"`` or ``"historical_projection"`` or ``None``.
        imaginary_count: Number of imaginary modes, or ``None`` when
            unavailable or when parsing failed.
    """

    available: bool
    source: str | None = None
    imaginary_count: int | None = None


# ---------------------------------------------------------------------------
# Product-JSON validation
# ---------------------------------------------------------------------------


def _is_valid_product(p: Path) -> bool:
    """Return True if *p* is a readable ``normal_modes_v1`` JSON file."""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("schema_version") != "normal_modes_v1":
        return False
    return isinstance(data.get("modes"), list)


def _count_imaginary_from_product_json(p: Path) -> int | None:
    """Read a validated ``normal_modes_v1`` JSON and count imaginary modes.

    Caller must ensure *p* passes :func:`_is_valid_product` first.
    """
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    modes = data.get("modes")
    if not isinstance(modes, list):
        return None
    count = 0
    for m in modes:
        if isinstance(m, dict) and m.get("imaginary"):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Unified ORCA parse cache (LRU, keyed by file identity)
# ---------------------------------------------------------------------------
# Value: (has_mode_vectors: bool, imaginary_count: int | None)
#   - has_mode_vectors is True when the parse produced non-empty mode_vectors
#   - imaginary_count counts only modes present in mode_vectors (matching
#     the endpoint basis) with freq < 0; None on parse failure
_CACHE_MAX = 256
_orca_cache: OrderedDict[tuple[str, int, int], tuple[bool, int | None]] = OrderedDict()


def _cache_key(p: Path) -> tuple[str, int, int]:
    try:
        st = p.stat()
        return (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(p), 0, 0)


def _probe_orca_file(p: Path) -> tuple[bool, int | None]:
    """Parse an ORCA output at most once per file identity.

    Returns ``(has_mode_vectors, imaginary_count)``.  On parse failure
    returns ``(False, None)``.  The result is cached in an LRU keyed by
    ``(path, mtime_ns, size)``.
    """
    key = _cache_key(p)
    if key in _orca_cache:
        _orca_cache.move_to_end(key)
        return _orca_cache[key]

    from acp.results.orca_parser import OrcaOutputParser

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        calc = OrcaOutputParser().parse_text(text)
    except (OSError, ValueError) as exc:
        logger.debug("Failed to parse ORCA output %s: %s", p, exc)
        result: tuple[bool, int | None] = (False, None)
    else:
        has_modes = bool(calc.mode_vectors)
        if has_modes:
            imag = sum(
                1
                for idx, freq in calc.mode_frequencies.items()
                if idx in calc.mode_vectors and freq < 0
            )
        else:
            imag = None
        result = (has_modes, imag)

    _orca_cache[key] = result
    if len(_orca_cache) > _CACHE_MAX:
        _orca_cache.popitem(last=False)

    return result


# ---------------------------------------------------------------------------
# Shared 3-tier source discovery
# ---------------------------------------------------------------------------


def find_vibration_source(
    task_root: Path,
    *,
    item_id: str | None = None,
    is_batch: bool = False,
) -> VibrationSource | None:
    """Discover the first available vibration source for an entry.

    Resolution order mirrors the endpoint exactly:
      1. Per-item product (when ``is_batch`` and ``item_id`` provided).
      2. Global product ``RESULT/frequencies/normal_modes.json``.
      3. Historical ORCA output under ``WORK/04_FREQ/`` (+ batch subpaths).

    Product files are schema-validated before acceptance so catalog and
    endpoint never diverge on malformed JSON.  Invalid files are silently
    skipped and resolution continues to the next tier.

    Returns ``None`` when no source is found.
    """
    freq_dir = task_root / "RESULT" / "frequencies"

    # Tier 1: per-item product (validated)
    if is_batch and item_id:
        item_path = freq_dir / f"{item_id}__normal_modes.json"
        if item_path.is_file() and _is_valid_product(item_path):
            return VibrationSource(kind="product", path=item_path)

    # Tier 2: global product (validated)
    global_path = freq_dir / "normal_modes.json"
    if global_path.is_file() and _is_valid_product(global_path):
        return VibrationSource(kind="product", path=global_path)

    # Tier 3: historical ORCA outputs
    work = task_root / "WORK"
    if not work.is_dir():
        return None

    candidates: list[Path] = []

    freq_dir_work = work / "04_FREQ"
    if freq_dir_work.is_dir():
        candidates.extend(sorted(freq_dir_work.glob("*.out")))
        candidates.extend(sorted(freq_dir_work.glob("*.log")))

    if is_batch and item_id:
        for batch_freq in [
            work / item_id / "frequency",
            work / "04_FREQ" / "batch" / item_id / "frequency",
        ]:
            if batch_freq.is_dir():
                candidates.extend(sorted(batch_freq.glob("*.out")))
                candidates.extend(sorted(batch_freq.glob("*.log")))

    for candidate in candidates:
        has_modes, _imag = _probe_orca_file(candidate)
        if has_modes:
            return VibrationSource(kind="historical", path=candidate)

    return None


# ---------------------------------------------------------------------------
# Catalog probe
# ---------------------------------------------------------------------------


def probe_vibration_projection(
    task_root: Path,
    *,
    item_id: str | None = None,
    is_batch: bool = False,
) -> VibrationProjection:
    """Probe vibration availability for the catalog without endpoint-level parsing.

    Uses :func:`find_vibration_source` for discovery, then counts imaginary
    modes from the source file.  For historical ORCA outputs the parse result
    is reused from the LRU cache populated during discovery.
    """
    source = find_vibration_source(task_root, item_id=item_id, is_batch=is_batch)
    if source is None:
        return VibrationProjection(available=False)

    if source.kind == "product":
        img = _count_imaginary_from_product_json(source.path)
        return VibrationProjection(
            available=True,
            source="product",
            imaginary_count=img,
        )

    # historical — reuse cached probe result (single parse per file identity)
    _has_modes, imag = _probe_orca_file(source.path)
    return VibrationProjection(
        available=True,
        source="historical_projection",
        imaginary_count=imag,
    )
