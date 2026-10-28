"""Deterministic integrity helpers for the external ACP2 science fixture.

Both digests are reproducible by the installed distribution itself, so the
platform can compare the manifest claim with the bytes that were actually
installed:

* :func:`distribution_digest` hashes the **whole installed payload** — every
  payload file keyed by its installed relative path
  (``<relative path>\\0<bytes>\\0`` in sorted order, ``__pycache__``/byte-code
  caches excluded).  That is exactly the platform rule
  ``acp2.plugin.distribution-digest.v1``: the wheel's payload paths are the
  installed paths, so a wheel built here and a wheel installed by pip hash to
  the same value.  ``data_dir`` lets the source tree (where the fixture inputs
  live beside ``src/``) feed the same ``<package>/data/<name>`` entries the
  build backend copies into the wheel.
* :func:`fixture_evidence_digest` hashes the fixed native input and expected
  output by file name and content.

The build backend imports this module from the source tree and writes both
values into ``cccp_science_plugin.json`` inside the wheel's ``.dist-info``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

PACKAGE_NAME = "acp2_fixture_validator"

_SEPARATOR = b"\x00"


def _payload_entries(root: Path, data_dir: Path | None = None) -> dict[str, bytes]:
    """Return ``installed relative path -> bytes`` for the fixture payload."""
    prefix = root.name
    entries: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        entries[f"{prefix}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
    if data_dir is not None:
        for path in sorted(Path(data_dir).iterdir()):
            if path.is_file():
                entries[f"{prefix}/data/{path.name}"] = path.read_bytes()
    return entries


def distribution_digest(
    package_dir: Path | None = None, *, data_dir: Path | None = None
) -> str:
    """Return ``sha256:<hex>`` over the fixture payload as installed."""
    root = Path(package_dir) if package_dir is not None else Path(__file__).resolve().parent
    entries = _payload_entries(root, data_dir)
    digest = hashlib.sha256()
    for relative in sorted(entries):
        digest.update(relative.encode("utf-8"))
        digest.update(_SEPARATOR)
        digest.update(entries[relative])
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
