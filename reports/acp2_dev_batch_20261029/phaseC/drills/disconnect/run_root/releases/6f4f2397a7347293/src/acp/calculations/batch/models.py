"""Public batch model and loader compatibility surface."""
# pyright: reportAny=false, reportUnsupportedDunderAll=false

from __future__ import annotations

from ._items import (
    BatchCalculationItem,
    BatchStructureItem,
    JsonObject,
    JsonValue,
    apply_user_overrides,
    item_cache_key,
)
from ._manifest import (
    BATCH_CALCULATION_SCHEMA_VERSION,
    TERMINAL_ITEM_STATUSES,
    BatchCalculationManifest,
)
from ._tag import (
    TagInfo,
    build_tag_title,
    kind_for_tag,
    normalize_tag,
    parse_tag_comment,
    role_for_tag,
    tag_for_kind,
)
from .loaders import (
    BATCH_REQUEST_SCHEMA_VERSION,
    load_batch_request,
    load_items_from_result_manifest,
    load_items_from_xyz_file,
    load_items_from_xyz_text,
)

__all__ = [
    "BATCH_CALCULATION_SCHEMA_VERSION",
    "BATCH_REQUEST_SCHEMA_VERSION",
    "BatchCalculationItem",
    "BatchCalculationManifest",
    "BatchStructureItem",
    "JsonObject",
    "JsonValue",
    "TagInfo",
    "TERMINAL_ITEM_STATUSES",
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
]
