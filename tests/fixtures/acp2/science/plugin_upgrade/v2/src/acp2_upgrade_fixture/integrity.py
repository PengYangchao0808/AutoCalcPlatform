"""Deterministic integrity helpers for the constructed ACP2 upgrade fixture.

Both digests are reproducible by the installed distribution itself, so the
platform can compare the manifest claim with the bytes that were actually
installed:

* :func:`distribution_digest` hashes the fixture's Python sources
  (``<relative path>\\0<bytes>\\0`` in sorted order, ``__pycache__`` excluded);
* :func:`fixture_evidence_digest` hashes the fixed native input and expected
  output by file name and content.

The build backend imports this module from the source tree and writes both
values into ``cccp_science_plugin.json`` inside the wheel's ``.dist-info``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PACKAGE_NAME = "acp2_upgrade_fixture"

_SEPARATOR = b"\x00"


def distribution_digest(package_dir: Path | None = None) -> str:
    """Return ``sha256:<hex>`` over the fixture package's Python sources."""
    root = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
    digest = hashlib.sha256()
    sources = sorted(
        path for path in root.rglob("*.py") if "__pycache__" not in path.parts and path.is_file()
    )
    for path in sources:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(_SEPARATOR)
        digest.update(path.read_bytes())
        digest.update(_SEPARATOR)
    return f"sha256:{digest.hexdigest()}"


def fixture_evidence_digest(fixtures_dir: Path) -> str:
    """Return ``sha256:<hex>`` over the fixed fixture files by name/content."""
    entries = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(fixtures_dir).iterdir())
        if path.is_file()
    }
    canonical = json.dumps(entries, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


__all__ = ["PACKAGE_NAME", "distribution_digest", "fixture_evidence_digest"]
