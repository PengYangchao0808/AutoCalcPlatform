"""Compatibility aggregate for historical batch execution records."""
# pyright: basic, reportReturnType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportAny=false

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from acp.confsearch.shared.artifacts import write_json_atomic

from ._items import BatchCalculationItem, JsonObject

logger = logging.getLogger(__name__)
BATCH_CALCULATION_SCHEMA_VERSION = "batch_calculation_v1"
TERMINAL_ITEM_STATUSES = frozenset({"completed", "failed", "skipped", "cancelled"})


@dataclass(slots=True)
class BatchCalculationManifest:
    """Persisted aggregate retained for historical task compatibility."""

    profile: str = ""
    items: list[BatchCalculationItem] = field(default_factory=list)
    schema_version: str = BATCH_CALCULATION_SCHEMA_VERSION
    workflow: str = "BatchOptimize"
    created_at: str = ""
    updated_at: str = ""

    @property
    def counts(self) -> dict[str, int]:
        """Count records by lifecycle status."""
        return {
            "total": len(self.items),
            **{
                status: sum(item.status == status for item in self.items)
                for status in ("completed", "failed", "skipped", "cancelled", "pending", "running")
            },
        }

    def by_cache_key(self) -> dict[str, BatchCalculationItem]:
        """Index records by non-empty cache key."""
        return {item.cache_key: item for item in self.items if item.cache_key}

    def to_dict(self) -> JsonObject:
        """Serialize this compatibility aggregate."""
        return {
            "kind": "batch_calculation_manifest",
            "schema_version": self.schema_version,
            "workflow": self.workflow,
            "profile": self.profile,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "counts": self.counts,
            "items": [item.to_dict() for item in self.items],
        }

    def write(self, path: Path | str) -> Path:
        """Write the aggregate atomically."""
        return write_json_atomic(Path(path), self.to_dict())

    @classmethod
    def read(cls, path: Path | str) -> BatchCalculationManifest | None:
        """Read an aggregate, returning ``None`` when unavailable."""
        manifest_path = Path(path)
        if not manifest_path.is_file():
            return None
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Unreadable batch calculation manifest %s: %s", manifest_path, exc)
            return None
        if not isinstance(raw, dict):
            return None
        rows = raw.get("items")
        items = (
            [BatchCalculationItem.from_dict(row) for row in rows if isinstance(row, dict)]
            if isinstance(rows, list)
            else []
        )
        return cls(
            profile=str(raw.get("profile") or raw.get("profile_level") or ""),
            items=items,
            schema_version=str(raw.get("schema_version") or BATCH_CALCULATION_SCHEMA_VERSION),
            workflow=str(raw.get("workflow") or "BatchOptimize"),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
        )


__all__ = ["BATCH_CALCULATION_SCHEMA_VERSION", "BatchCalculationManifest", "TERMINAL_ITEM_STATUSES"]
