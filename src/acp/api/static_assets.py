"""Digest manifest for the packaged Workbench asset tree.

The ``acp-web`` wheel ships ``acp_web_assets/`` (built from ``frontend/`` at
release time). A browser bundle that silently differs from the one the release
was verified against is exactly the 2026-09-28 stale-bundle class of incident,
so the manifest is generated whenever the wheel is staged (``scripts/
build_release.py``) and re-verified before the packaged assets are served.

One implementation owns the rule: ``build_manifest`` produces it, the release
builder embeds it, and :func:`verify_manifest` (called from ``create_app``)
recomputes every digest at startup. The committed copy at
``docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json``
must be byte-identical to the embedded one; the M5-01 acceptance compares the
two from the *installed* wheel, never only from the checkout.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
from importlib.util import find_spec
from pathlib import Path
from typing import Any

MANIFEST_NAME = "static-assets.manifest.json"
SCHEMA = "acp2.experience.static-assets.v1"
SCHEMA_VERSION = "1.0.0"
DIGEST_RULE = 'sha256 over sorted lines "<path> <sha256>\\n"'
_SKIP_DIRS = frozenset({"__pycache__", "node_modules", ".git"})
#: Packaging scaffolding added by ``build_release.py`` — not a frontend asset.
_SKIP_ROOT_FILES = frozenset({"__init__.py"})
_MEDIA_OVERRIDES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
}


class StaticAssetDigestError(RuntimeError):
    """The packaged asset tree does not match its digest manifest."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _media_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in _MEDIA_OVERRIDES:
        return _MEDIA_OVERRIDES[suffix]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def iter_asset_files(directory: Path) -> list[Path]:
    """Every shipped asset below *directory*, sorted, manifest excluded."""
    files = [
        path
        for path in directory.rglob("*")
        if path.is_file()
        and path.name != MANIFEST_NAME
        and not _SKIP_DIRS.intersection(path.relative_to(directory).parts)
        and not (path.parent == directory and path.name in _SKIP_ROOT_FILES)
    ]
    return sorted(files, key=lambda path: path.relative_to(directory).as_posix())


def manifest_digest(entries: list[dict[str, str]]) -> str:
    """Digest rule shared with the contract document (sorted path/digest lines)."""
    payload = "".join(f"{entry['path']} {entry['sha256']}\n" for entry in entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_manifest(
    directory: Path,
    *,
    release_id: str,
    package_version: str,
    entry_points: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the manifest for *directory* (deterministic; no timestamp)."""
    directory = Path(directory)
    if not directory.is_dir():
        raise StaticAssetDigestError(f"asset directory does not exist: {directory}")
    entries = [
        {
            "path": path.relative_to(directory).as_posix(),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "media_type": _media_type(path.relative_to(directory).as_posix()),
        }
        for path in iter_asset_files(directory)
    ]
    if not entries:
        raise StaticAssetDigestError(f"asset directory is empty: {directory}")
    counts = {"files": len(entries), "bytes": sum(entry["size_bytes"] for entry in entries)}
    for suffix in (".js", ".css", ".html"):
        counts[suffix.removeprefix(".")] = sum(
            1 for entry in entries if entry["path"].endswith(suffix)
        )
    declared = entry_points or {
        "index": "ACP_Workbench_v2.html",
        "legacy": "ACP_Workbench.html",
        "js_root": "js",
    }
    missing = [
        name
        for name in declared.values()
        if not any(
            entry["path"] == name or entry["path"].startswith(f"{name}/") for entry in entries
        )
    ]
    if missing:
        raise StaticAssetDigestError(f"declared entry point(s) missing: {missing}")
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "package_version": package_version,
        "digest_rule": DIGEST_RULE,
        "digest": manifest_digest(entries),
        "counts": counts,
        "entry_points": declared,
        "files": entries,
    }


def manifest_text(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, sort_keys=True) + "\n"


def write_manifest(directory: Path, manifest: dict[str, Any]) -> Path:
    """Write the manifest atomically inside *directory*."""
    target = Path(directory) / MANIFEST_NAME
    text = manifest_text(manifest)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=target.parent, prefix=".static-assets-", delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def verify_manifest(
    directory: Path, *, manifest_path: Path | None = None, strict: bool = True
) -> dict[str, Any]:
    """Recompute every digest in *directory* and fail closed on any drift.

    ``strict`` additionally rejects files that are on disk but not listed (an
    unlisted file would be served without ever being verified).
    """
    directory = Path(directory)
    path = Path(manifest_path) if manifest_path is not None else directory / MANIFEST_NAME
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StaticAssetDigestError(f"missing digest manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StaticAssetDigestError(f"malformed digest manifest: {path}: {exc}") from exc
    if manifest.get("schema") != SCHEMA:
        raise StaticAssetDigestError(f"unexpected manifest schema: {manifest.get('schema')!r}")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise StaticAssetDigestError(f"manifest lists no files: {path}")
    listed: set[str] = set()
    for entry in entries:
        name = entry["path"]
        listed.add(name)
        asset = directory / name
        if not asset.is_file():
            raise StaticAssetDigestError(f"manifest lists a missing asset: {name}")
        actual = _sha256(asset)
        if actual != entry["sha256"]:
            raise StaticAssetDigestError(
                f"sha256 mismatch for {name}: manifest {entry['sha256']}, file {actual}"
            )
    if strict:
        extra = {
            candidate.relative_to(directory).as_posix() for candidate in iter_asset_files(directory)
        } - listed
        if extra:
            raise StaticAssetDigestError(f"unlisted asset(s) would be served: {sorted(extra)}")
    recomputed = manifest_digest(
        [{"path": entry["path"], "sha256": entry["sha256"]} for entry in entries]
    )
    if recomputed != manifest.get("digest"):
        raise StaticAssetDigestError(
            f"manifest digest is not reproducible: {manifest.get('digest')} != {recomputed}"
        )
    return manifest


def find_packaged_assets() -> Path | None:
    """Directory of the packaged ``acp_web_assets`` module, when installed."""
    spec = find_spec("acp_web_assets")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


__all__ = [
    "DIGEST_RULE",
    "MANIFEST_NAME",
    "SCHEMA",
    "SCHEMA_VERSION",
    "StaticAssetDigestError",
    "build_manifest",
    "manifest_digest",
    "find_packaged_assets",
    "iter_asset_files",
    "manifest_text",
    "verify_manifest",
    "write_manifest",
]
