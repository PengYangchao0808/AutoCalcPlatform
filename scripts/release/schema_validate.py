"""Minimal JSON-Schema validator for the frozen ACP 2.0 release contracts.

The repository does not ship ``jsonschema`` and the release BOM must be checked
on any clean host, so this module implements exactly the keyword subset the
``acp2.release.bom.v1`` schema uses (``$ref``/``$defs``, ``type``, ``const``,
``enum``, ``pattern``, ``minimum``, ``minItems``, ``minLength``, ``required``,
``properties``, ``additionalProperties: false`` and ``items``). Unsupported
keywords raise instead of being ignored, so a schema that grows a new keyword
cannot silently stop being enforced.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SUPPORTED = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "$comment",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "pattern",
        "minimum",
        "minItems",
        "minLength",
        "required",
        "properties",
        "additionalProperties",
        "items",
    }
)
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _check_unsupported(schema: Any, path: str) -> None:
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key not in _SUPPORTED:
                raise ValueError(f"unsupported JSON-Schema keyword {key!r} at {path}")
            if key in {"$defs", "properties"}:
                for name, child in value.items():
                    _check_unsupported(child, f"{path}/{key}/{name}")
            elif key == "items":
                _check_unsupported(value, f"{path}/items")


def _resolve(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"only local $ref is supported: {reference}")
    node: Any = root
    for part in reference.removeprefix("#/").split("/"):
        node = node[part]
    return node


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    return isinstance(instance, _TYPES[expected])


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Return every violation of *schema* by *instance* (empty list = valid)."""
    _check_unsupported(schema, "#")
    errors: list[str] = []

    def walk(value: Any, node: dict[str, Any], path: str) -> None:
        if "$ref" in node:
            walk(value, _resolve(schema, node["$ref"]), path)
            return
        if "const" in node and value != node["const"]:
            errors.append(f"{path}: expected const {node['const']!r}, got {value!r}")
        if "enum" in node and value not in node["enum"]:
            errors.append(f"{path}: {value!r} not in {node['enum']!r}")
        if "type" in node:
            types = node["type"] if isinstance(node["type"], list) else [node["type"]]
            if not any(_type_matches(value, item) for item in types):
                errors.append(f"{path}: {type(value).__name__} is not {types}")
                return
        if isinstance(value, str):
            if "minLength" in node and len(value) < node["minLength"]:
                errors.append(f"{path}: shorter than {node['minLength']}")
            if "pattern" in node and re.search(node["pattern"], value) is None:
                errors.append(f"{path}: {value!r} does not match {node['pattern']!r}")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in node and value < node["minimum"]:
                errors.append(f"{path}: {value} < minimum {node['minimum']}")
        if isinstance(value, list):
            if "minItems" in node and len(value) < node["minItems"]:
                errors.append(f"{path}: fewer than {node['minItems']} items")
            if "items" in node:
                for index, item in enumerate(value):
                    walk(item, node["items"], f"{path}[{index}]")
        if isinstance(value, dict):
            for name in node.get("required", ()):
                if name not in value:
                    errors.append(f"{path}: missing required property {name!r}")
            properties = node.get("properties", {})
            for name, child in properties.items():
                if name in value:
                    walk(value[name], child, f"{path}.{name}")
            if node.get("additionalProperties") is False:
                for name in value:
                    if name not in properties:
                        errors.append(f"{path}: undeclared property {name!r}")

    walk(instance, schema, "$")
    return errors


def load(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_file(document: Path, schema_path: Path) -> list[str]:
    return validate(load(document), load(schema_path))


__all__ = ["load", "validate", "validate_file"]
