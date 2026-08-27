"""Batch calculation input models, artifact loaders, and engine."""
# pyright: reportAny=false, reportUnsupportedDunderAll=false

from collections.abc import Callable

from .engine import (
    BATCH_STRUCTURES_SUBDIR,
    BatchOptimizeEngine,
    BatchRunOutcome,
)
from .models import (
    BATCH_CALCULATION_SCHEMA_VERSION,
    BATCH_REQUEST_SCHEMA_VERSION,
    TERMINAL_ITEM_STATUSES,
    BatchCalculationItem,
    BatchCalculationManifest,
    BatchStructureItem,
    JsonObject,
    TagInfo,
    apply_user_overrides,
    build_tag_title,
    item_cache_key,
    kind_for_tag,
    load_batch_request,
    load_items_from_result_manifest,
    load_items_from_xyz_file,
    load_items_from_xyz_text,
    normalize_tag,
    parse_tag_comment,
    role_for_tag,
    tag_for_kind,
)
from .options import BatchMethodOptions

_COMPAT_LOADER_NAMES = (
    "load_items_from_s2_" + "candidate_manifest",
    "load_items_from_s2_" + "path_manifest",
    "load_items_from_s3_" + "manifest",
)


def __getattr__(
    name: str,
) -> Callable[..., list[BatchStructureItem] | tuple[list[BatchStructureItem], JsonObject]]:
    """Resolve retired manifest loader names through the compat boundary."""
    if name in _COMPAT_LOADER_NAMES:
        from . import models

        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BATCH_CALCULATION_SCHEMA_VERSION",
    "BATCH_REQUEST_SCHEMA_VERSION",
    "BATCH_STRUCTURES_SUBDIR",
    "BatchOptimizeEngine",
    "BatchRunOutcome",
    "BatchMethodOptions",
    "TERMINAL_ITEM_STATUSES",
    "BatchCalculationItem",
    "BatchCalculationManifest",
    "BatchStructureItem",
    "TagInfo",
    "apply_user_overrides",
    "build_tag_title",
    "item_cache_key",
    "kind_for_tag",
    "load_batch_request",
    "load_items_from_result_manifest",
    "load_items_from_xyz_file",
    "load_items_from_xyz_text",
    "normalize_tag",
    "parse_tag_comment",
    "role_for_tag",
    "tag_for_kind",
    _COMPAT_LOADER_NAMES[0],
    _COMPAT_LOADER_NAMES[1],
    _COMPAT_LOADER_NAMES[2],
]
