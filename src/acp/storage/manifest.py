"""v2 result manifest — ``RESULT/result_manifest.json`` schema (design doc §8)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MANIFEST_FILENAME", "Product", "ProductKind", "ResultManifest"]

MANIFEST_FILENAME = "result_manifest.json"


class ProductKind(str, Enum):
    """Kind of a manifest product (design doc §8 ``kind`` field)."""

    STRUCTURE = "structure"
    FREQUENCY_MODES = "frequency_modes"
    ENERGY_REPORT = "energy_report"
    ENSEMBLE = "ensemble"
    TRAJECTORY = "trajectory"
    REPORT = "report"
    FILE = "file"


@dataclass
class Product:
    """One result product entry: ``{id, label, path, kind}`` with path relative to ``RESULT/``."""

    id: str
    label: str
    path: str
    kind: ProductKind = ProductKind.FILE

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "id": self.id,
            "label": self.label,
            "path": self.path,
            "kind": self.kind.value,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Product:
        """Deserialise; unknown ``kind`` values fall back to ``file``."""
        try:
            kind = ProductKind(payload.get("kind", ProductKind.FILE.value))
        except ValueError:
            logger.warning("unknown product kind %r; falling back to 'file'", payload.get("kind"))
            kind = ProductKind.FILE
        return cls(
            id=str(payload.get("id", "")),
            label=str(payload.get("label", "")),
            path=str(payload.get("path", "")),
            kind=kind,
        )


@dataclass
class ResultManifest:
    """``RESULT/result_manifest.json`` content — frontend result-display entry (design doc §8)."""

    task_id: str = ""
    workflow: str = ""
    status: str = "pending"
    version: int = 2
    products: list[Product] = field(default_factory=list)

    def add_product(
        self,
        id: str,
        label: str,
        path: str,
        kind: ProductKind | str = ProductKind.FILE,
    ) -> Product:
        """Append or replace (by ``id``) a product entry; returns the stored product."""
        product = Product(id=id, label=label, path=path, kind=ProductKind(kind))
        self.products = [p for p in self.products if p.id != id]
        self.products.append(product)
        return product

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the §8 JSON schema."""
        return {
            "version": self.version,
            "task_id": self.task_id,
            "workflow": self.workflow,
            "status": self.status,
            "products": [p.to_dict() for p in self.products],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ResultManifest:
        """Deserialise from a parsed ``result_manifest.json`` payload."""
        return cls(
            task_id=str(payload.get("task_id", "")),
            workflow=str(payload.get("workflow", "")),
            status=str(payload.get("status", "pending")),
            version=int(payload.get("version", 2)),
            products=[Product.from_dict(p) for p in payload.get("products", [])],
        )

    def write(self, result_dir: Path | str) -> Path:
        """Atomically write ``result_manifest.json`` under *result_dir*."""
        target_dir = Path(result_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / MANIFEST_FILENAME
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        return path

    @classmethod
    def read(cls, result_dir: Path | str) -> ResultManifest:
        """Read and parse ``result_manifest.json`` from *result_dir*.

        Raises:
            FileNotFoundError: If the manifest file does not exist.
        """
        path = Path(result_dir) / MANIFEST_FILENAME
        if not path.is_file():
            raise FileNotFoundError(f"result manifest not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(payload)
