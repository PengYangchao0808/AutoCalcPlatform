"""ACP-side publication entry routed through the typed Data port (todo 28).

This is the composition seam between the Runtime layer and the todo-11
``DataResourcePort`` (``src/acp_services/data``).  Runtime publication call
sites — the task ``RESULT/result_manifest.json``, the authoritative
``RESULT/frame_candidates.json``, the scheduler markers (``job.json`` /
``task.json``) and the B28b frame projections — reach the Data layer through
:class:`PublicationDataPort` instead of touching the underlying writers/cache
directly.  ``acp_services`` stays free of any ``acp`` import (the isolation
scanner); the adapter lives here, on the legacy side of the seam.

Why not ``DataResourcePort.upload_paths`` for markers: that frozen method is the
**cache → remote** write-back (``RemoteStructureCache.push_paths``).  Scheduler
markers are task-root files uploaded *before* a cache entry ever exists, so
:meth:`PublicationDataPort.upload_markers` exposes that local→remote transfer on
the same publication entry, delegating the mechanism to the injected
``FileStager``.  The frozen port is never bypassed with a private table/cache
write.

The manifest/candidates methods delegate to the *real* todo-11 adapter
(``build_data_resource_port``), so behaviour, the authoritative candidate-file
format and the v2 manifest schema are unchanged.  The manifest publisher maps a
``RESULT`` directory to the Data port's ``task_root`` shape; a non-``RESULT``
manifest (the per-step publication manifest under ``WORK/...``) cannot be
expressed by the frozen port and falls back to the named
``result_publication`` writer — never a direct ``manifest.write`` from a
workflow module.
"""

from __future__ import annotations

import json
import logging
import posixpath
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from acp.results.frame_candidate_store import atomic_write_text
from acp.services_data_bootstrap import build_data_resource_port
from acp.storage.manifest import ProductKind, ResultManifest
from acp_services.data import contracts as dc

logger = logging.getLogger(__name__)

__all__ = [
    "PublicationDataPort",
    "build_publication_data_port",
    "default_manifest_publisher",
    "default_publication_data_port",
    "manifest_publisher",
]

_FRAME_PROJECTION_DIR = "frames"
_MARKER_RELATIVE_PATHS = ("job.json", "task.json")


class _UnusedDataBackend:
    """Placeholder backing object for a publication-only Data port.

    ``publish_manifest`` / ``read_manifest`` / candidates never touch the
    source store or the remote cache, so the default publication port is built
    without opening the jobs DB or the cache directory (no side effects).
    """


def default_publication_data_port() -> dc.DataResourcePort:
    """Build the manifest/candidate-only Data port (no DB/cache side effects)."""
    unused = _UnusedDataBackend()
    return build_data_resource_port(
        ".",
        source_store=unused,  # type: ignore[arg-type]
        cache=unused,  # type: ignore[arg-type]
    )


class PublicationDataPort:
    """ACP publication entry over a :class:`DataResourcePort` (+ stager).

    Only manifest/candidate operations are used by the executor; the remote
    runner additionally uses :meth:`upload_markers`.  The class is a thin
    delegate — it holds no state and re-implements no writer.
    """

    def __init__(self, data: dc.DataResourcePort, *, stager: Any | None = None) -> None:
        self._data = data
        self._stager = stager

    # ── artifacts / manifest ────────────────────────────────────────────

    def publish_manifest(self, task_root: Path | str, manifest: ResultManifest) -> Path:
        """Publish the v2 manifest under ``<task_root>/RESULT`` through Data."""
        publication = self._data.publish_manifest(
            dc.ManifestPublish(task_root=str(task_root), manifest=manifest.to_dict())
        )
        return Path(publication.path)

    def read_manifest(self, task_root: Path | str) -> Mapping[str, Any] | None:
        """Read the manifest through Data; ``None`` when absent/unreadable."""
        snapshot = self._data.read_manifest(dc.ManifestRead(task_root=str(task_root)))
        if not snapshot.found:
            return None
        return dict(snapshot.manifest)

    # ── candidates ──────────────────────────────────────────────────────

    def save_candidates(
        self,
        task_root: Path | str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int | None = None,
    ) -> dc.CandidateSnapshot:
        """Write the authoritative ``RESULT/frame_candidates.json`` through Data."""
        return self._data.save_candidates(
            dc.CandidateWrite(
                task_root=str(task_root),
                payload=dict(payload),
                expected_revision=expected_revision,
            )
        )

    def read_candidates(self, task_root: Path | str) -> dc.CandidateSnapshot:
        """Read the authoritative ``RESULT/frame_candidates.json`` through Data."""
        return self._data.read_candidates(str(task_root))

    # ── transfers (scheduler markers) ───────────────────────────────────

    def upload_markers(
        self,
        *,
        job_id: str,
        work_dir: Path | str,
        node: Any,
        remote_job_dir: str,
        relative_paths: Sequence[str] = _MARKER_RELATIVE_PATHS,
    ) -> tuple[str, ...]:
        """Upload task-root scheduler markers into the remote job directory.

        The files are uploaded one-by-one preserving the relative layout; an
        upload failure propagates unchanged so the submission fails visibly
        (never a silently nested-layout task).  ``job_id`` is accepted for
        audit symmetry; the transfer itself is path-based.
        """
        if self._stager is None:
            raise RuntimeError("PublicationDataPort.upload_markers requires a stager")
        for name in relative_paths:
            self._stager.upload_file(
                node, Path(work_dir) / name, posixpath.join(remote_job_dir, name)
            )
        logger.debug("publication markers uploaded for job %s: %s", job_id, list(relative_paths))
        return tuple(relative_paths)

    # ── frames projection (B28b) ────────────────────────────────────────

    def save_frame_projection(
        self,
        task_root: Path | str,
        view_type: str,
        projection: Mapping[str, Any],
    ) -> Path:
        """Publish a frame projection under ``RESULT/frames/`` and register it.

        The projection body is written as a RESULT artifact and registered as a
        manifest product through the Data port; the existing manifest is read
        back through Data and preserved.
        """
        root = Path(task_root)
        result_dir = root / "RESULT"
        relative = f"{_FRAME_PROJECTION_DIR}/{view_type}.json"
        target = result_dir / relative
        atomic_write_text(
            target,
            json.dumps(projection, indent=2, sort_keys=True, default=str) + "\n",
        )
        existing = self.read_manifest(root)
        manifest = (
            ResultManifest.from_dict(dict(existing))
            if existing is not None
            else ResultManifest(task_id="", workflow="frames", status="completed")
        )
        manifest.add_product(
            id=f"frames_{view_type}",
            label=f"Frames projection ({view_type})",
            path=relative,
            kind=ProductKind.REPORT,
            metadata={"view_type": view_type, "product_kind": "frames_projection"},
        )
        self.publish_manifest(root, manifest)
        return target

    def read_frame_projection(
        self, task_root: Path | str, view_type: str
    ) -> Mapping[str, Any] | None:
        """Read a published frame projection through the Data manifest entry."""
        root = Path(task_root)
        manifest = self.read_manifest(root)
        if manifest is None:
            return None
        products = manifest.get("products")
        if not isinstance(products, list):
            return None
        relative = next(
            (
                str(product.get("path") or "")
                for product in products
                if isinstance(product, Mapping) and product.get("id") == f"frames_{view_type}"
            ),
            "",
        )
        if not relative:
            return None
        target = root / "RESULT" / relative
        if not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, Mapping) else None


def manifest_publisher(
    publication: PublicationDataPort | None,
) -> Callable[[Path, ResultManifest], Path]:
    """Return the ``(result_dir, manifest) -> Path`` publisher for the seam.

    With no publication port the publisher is the direct named writer (behaviour
    preserved).  With one, a ``RESULT`` directory is routed through the Data
    port; any other ``result_dir`` (per-step manifest under ``WORK/``) falls back
    to the named writer because the frozen port cannot express it.
    """
    if publication is None:
        return lambda result_dir, manifest: manifest.write(result_dir)

    def _publish(result_dir: Path, manifest: ResultManifest) -> Path:
        resolved = Path(result_dir)
        if resolved.name == "RESULT":
            return publication.publish_manifest(resolved.parent, manifest)
        return manifest.write(resolved)

    return _publish


def default_manifest_publisher() -> Callable[[Path, ResultManifest], Path]:
    """Production publisher bound to the default (publication-only) Data port."""
    return manifest_publisher(PublicationDataPort(default_publication_data_port()))


def build_publication_data_port(
    *,
    data: dc.DataResourcePort | None = None,
    stager: Any | None = None,
) -> PublicationDataPort:
    """Build the ACP publication entry (Data port built when omitted)."""
    return PublicationDataPort(data or default_publication_data_port(), stager=stager)
