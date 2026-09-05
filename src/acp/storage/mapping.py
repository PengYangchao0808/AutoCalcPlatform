"""v2 server↔node path mapping record (design doc §9.3)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["NodePathMapping"]


@dataclass(frozen=True)
class NodePathMapping:
    """Immutable mapping of a task to its storage node/path (design doc §9.3)."""

    task_id: str
    storage_node: str
    storage_path: str
    storage_mode: str = "local"
    result_manifest_path: str = "RESULT/result_manifest.json"
    last_seen: str | None = None
    input_hash: str | None = None
    result_manifest_mtime: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> NodePathMapping:
        """Deserialise from a dict, tolerating unknown/missing keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known})
