"""Canonical molecule naming helpers shared by API and source discovery."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any

_MOLECULAR_SUFFIXES = frozenset(
    {".xyz", ".gjf", ".sdf", ".mol", ".com", ".inp", ".log", ".out", ".json"}
)
_JOB_ID_RE = re.compile(r"^\d{8}_\d{6}_\d{3}_.+$")


def canonical_molecule_name(value: Any, *, fallback: str = "") -> str:
    """Return a scalar molecule-name component without task/Job-ID metadata."""
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if not text:
        return fallback
    # Accept Windows paths even when the server is running on POSIX.
    basename = PurePosixPath(text.replace("\\", "/")).name
    suffix = PurePosixPath(basename).suffix.lower()
    if suffix in _MOLECULAR_SUFFIXES:
        basename = PurePosixPath(basename).stem
    if not basename or _JOB_ID_RE.fullmatch(basename):
        return fallback
    return basename.strip()


def molecule_name_from_input(inp: Mapping[str, Any] | None) -> str:
    """Resolve a molecule name from input without ever stringifying mappings."""
    if not isinstance(inp, Mapping):
        return ""

    value = canonical_molecule_name(inp.get("molecule_name"))
    if value:
        return value

    source = inp.get("source")
    if isinstance(source, str):
        value = canonical_molecule_name(source)
        if value:
            return value
    elif isinstance(source, Mapping):
        for key in ("molecule_name", "name", "source_name"):
            value = canonical_molecule_name(source.get(key))
            if value:
                return value
        for key in ("artifact_path", "path", "filename"):
            value = canonical_molecule_name(source.get(key))
            if value:
                return value

    for key in ("input", "smiles", "filename", "path"):
        value = canonical_molecule_name(inp.get(key))
        if value:
            return value
    return ""


__all__ = ["canonical_molecule_name", "molecule_name_from_input"]
