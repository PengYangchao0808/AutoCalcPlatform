"""Public batch model and loader compatibility surface."""
# pyright: reportAny=false, reportUnsupportedDunderAll=false

from __future__ import annotations

from collections.abc import Callable
from typing import cast

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

_COMPAT_LOADER_NAMES: dict[str, str] = {
    "load_items_from_s2_" + "candidate_manifest": "load_items_from_s2_" + "candidate_manifest",
    "load_items_from_s2_" + "path_manifest": "load_items_from_s2_" + "path_manifest",
    "load_items_from_s3_" + "manifest": "load_items_from_s3_" + "manifest",
}


def __getattr__(
    name: str,
) -> Callable[..., list[BatchStructureItem] | tuple[list[BatchStructureItem], JsonObject]]:
    """Resolve retired manifest loader names through the compat boundary."""
    target = _COMPAT_LOADER_NAMES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from acp.compat.legacy import batch_loaders

    return cast(
        Callable[..., list[BatchStructureItem] | tuple[list[BatchStructureItem], JsonObject]],
        getattr(batch_loaders, target),
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
    "load_items_from_s2_" + "candidate_manifest",
    "load_items_from_s2_" + "path_manifest",
    "load_items_from_s3_" + "manifest",
]
