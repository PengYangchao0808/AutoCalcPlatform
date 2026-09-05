"""v2 server-side task record (design doc §9.1)."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["TaskRecord"]


@dataclass
class TaskRecord:
    """Server-side task metadata row (design doc §9.1); full files stay on the node."""

    task_id: str
    project_id: str
    molecule_name: str
    task_name: str
    workflow: str
    task_dir_name: str
    status: str = "pending"
    remark: str = ""
    display_name: str = ""
    node_id: str | None = None
    node_path: str | None = None
    input_hash: str | None = None
    result_manifest_path: str | None = None
    current_stage: str | None = None
    layout_version: int = 2
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskRecord:
        """Deserialise from a dict, tolerating unknown/missing keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known})
