"""
Structure Source Discovery
==========================

Discover reusable final structures from COMPLETED scheduler jobs so the
frontend "job results" tab can offer them as input for new jobs.

Read-only by design: this module never writes ``result_summary.json`` and
never participates in resume/checkpoint decisions (job file layout spec
§6.2).  Local paths are always validated through
:func:`acp.scheduler.files.resolve_safe`; remote paths are POSIX-normalised
and fetched on demand through a ``RemoteResultFetcher``-like object.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from acp.intake import parse_xyz_text
from acp.scheduler.artifacts import compute_checksum
from acp.scheduler.files import resolve_safe
from acp.scheduler.jobs import JobRecord, JobStatus

if TYPE_CHECKING:
    from acp.scheduler.remote.fetcher import RemoteResultFetcher

logger = logging.getLogger(__name__)

__all__ = ["StructureSourceService"]

_RESULT_SUMMARY_FILENAME = "result_summary.json"
_ROLE_FINAL_STRUCTURE = "final_stable_structure"
#: Workflows whose final products are not reusable 3D structures.
_EXCLUDED_WORKFLOWS = frozenset({"singlepoint", "frequency"})
#: Bound on remote listing probes: beyond this many remote jobs the listing
#: falls back to ``needs_fetch`` placeholders instead of SFTP round-trips.
_REMOTE_PROBE_THRESHOLD = 10
#: TTL (seconds) for per-job remote probe results (aligned with the
#: NodeManager 30 s cache convention).
_PROBE_TTL_SECONDS = 30.0
#: Bounds for a single remote probe (summaries / structure products).
_PROBE_MAX_SUMMARIES = 20
_PROBE_MAX_PRODUCTS = 50

_CHARGE_RE = re.compile(r"charge\s*=\s*(-?\d+)", re.IGNORECASE)
_MULT_RE = re.compile(r"mult(?:i(?:plicity)?)?\s*=\s*(\d+)", re.IGNORECASE)

#: Exceptions a remote probe/fetch may raise: RemoteFileError is a
#: RuntimeError subclass; SFTP/socket failures surface as OSError; JSON and
#: payload problems as ValueError/KeyError.
_REMOTE_ERRORS = (OSError, RuntimeError, ValueError, KeyError)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_frame_comment(text: str) -> str:
    """Return the comment line of the first XYZ frame ("" when absent)."""
    lines = text.strip().splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return ""
    try:
        int(lines[i].strip())
    except ValueError:
        return ""
    return lines[i + 1] if i + 1 < len(lines) else ""


def _legacy_filename_matches(rel_path: str) -> bool:
    """Legacy (role-less) structure-product filename conventions."""
    posix = rel_path.replace("\\", "/")
    name = posix.rsplit("/", 1)[-1]
    if name == "optimized.xyz" or name == "ensemble.xyz":
        return True
    if name.endswith("_global_min.xyz"):
        return True
    return posix.endswith("finalDFT/all_conformers.xyz")


def _product_is_xyz(item: dict[str, Any]) -> bool:
    kind = str(item.get("kind") or "")
    if kind:
        return kind == "xyz"
    return str(item.get("path") or "").endswith(".xyz")


def _select_structure_products(products: list[Any]) -> list[dict[str, Any]]:
    """Select structure products: role-marked first, legacy filename fallback."""
    candidates: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    for item in products:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        if not _product_is_xyz(item):
            continue
        if item.get("role") == _ROLE_FINAL_STRUCTURE:
            candidates.append(item)
        elif _legacy_filename_matches(str(item["path"])):
            legacy.append(item)
    return candidates or legacy


class StructureSourceService:
    """Discover and load reusable final structures from completed jobs.

    Args:
        store: Scheduler :class:`JobStore` used for job queries.
        run_root: Scheduler run root (jobs live under it).
        fetcher: Optional ``RemoteResultFetcher``-like object used for
            remote jobs.  Only ``walk_remote_files``/``read_file``/
            ``file_exists`` (record, rel_path) are required.
    """

    def __init__(
        self,
        store: Any,
        run_root: Path,
        fetcher: RemoteResultFetcher | None = None,
    ) -> None:
        self._store = store
        self._run_root = Path(run_root)
        self._fetcher = fetcher
        self._probe_cache: dict[str, tuple[float, list[dict[str, Any]] | None]] = {}

    # ------------------------------------------------------------------ #
    # Listing
    # ------------------------------------------------------------------ #

    def list_recent(
        self,
        *,
        limit: int = 20,
        project_id: str | None = None,
        workflow: str | None = None,
        include_remote: bool = True,
    ) -> list[dict[str, Any]]:
        """Return one entry per reusable structure from recent COMPLETED jobs."""
        if workflow in _EXCLUDED_WORKFLOWS:
            return []
        records = self._store.list_recent_completed(
            limit=max(limit * 3, 20),
            project_id=project_id,
            workflow=workflow,
        )
        records = [r for r in records if r.status == JobStatus.COMPLETED]
        remote_count = sum(
            1 for r in records if r.spec.workflow not in _EXCLUDED_WORKFLOWS and self._is_remote(r)
        )
        probe_remote = include_remote and remote_count <= _REMOTE_PROBE_THRESHOLD

        entries: list[dict[str, Any]] = []
        for record in records:
            if len(entries) >= limit:
                break
            if record.spec.workflow in _EXCLUDED_WORKFLOWS:
                continue
            if self._is_remote(record):
                if not include_remote:
                    continue
                probed = self._probe_remote(record) if probe_remote else None
                if probed:
                    entries.extend(probed)
                else:
                    entries.append(self._remote_placeholder(record))
                continue
            entries.extend(self._discover_job(record))
        return entries[:limit]

    # ------------------------------------------------------------------ #
    # Single source
    # ------------------------------------------------------------------ #

    def get(self, source_id: str) -> tuple[dict[str, Any], str]:
        """Load the structure for *source_id*.

        Returns:
            ``(structure_asset_dict, checksum)`` where the asset dict uses
            ``StructureAssetModel`` field names and the checksum follows the
            ``"sha256:<hex>"`` convention of
            :func:`acp.scheduler.artifacts.compute_checksum`.

        Raises:
            ValueError: Malformed id, unknown/incomplete job, excluded
                workflow, missing file, or unreachable remote node.
        """
        job_id, rel_path = self.parse_source_id(source_id)
        record = self._store.get(job_id)
        if record is None:
            raise ValueError(f"Job not found: {job_id}")
        if record.status != JobStatus.COMPLETED:
            raise ValueError(f"Job {job_id} is not completed (status={record.status.value})")
        if record.spec.workflow in _EXCLUDED_WORKFLOWS:
            raise ValueError(
                f"Workflow {record.spec.workflow!r} does not provide reusable structures"
            )

        if self._is_remote(record):
            data = self._fetch_remote_xyz(record, rel_path)
            checksum = "sha256:" + hashlib.sha256(data).hexdigest()
            text = data.decode("utf-8", errors="replace")
        else:
            resolved = resolve_safe(record.work_dir, rel_path)
            if resolved is None:
                raise ValueError(f"Source file not found: {rel_path}")
            checksum = compute_checksum(resolved)
            try:
                text = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ValueError(f"Source file unreadable: {rel_path} ({exc})") from exc

        asset = self._build_asset(record, rel_path, text)
        if asset is None:
            raise ValueError(f"Source file is not a parseable XYZ structure: {rel_path}")
        return asset, checksum

    @staticmethod
    def parse_source_id(source_id: str) -> tuple[str, str]:
        """Split ``job_<job_id>:<rel_path>`` into ``(job_id, rel_path)``.

        Raises:
            ValueError: If the id does not follow the contract.
        """
        prefix, sep, rel_path = source_id.partition(":")
        if not sep or not prefix.startswith("job_") or not prefix[4:] or not rel_path:
            raise ValueError(f"Invalid source_id: {source_id}")
        return prefix[4:], rel_path

    # ------------------------------------------------------------------ #
    # Local discovery
    # ------------------------------------------------------------------ #

    def _discover_job(self, record: JobRecord) -> list[dict[str, Any]]:
        """Discover structure sources in a local job work directory.

        Mirrors :func:`acp.scheduler.files._collect_pinned`: rglob every
        ``result_summary.json`` under the work dir, select structure
        products, validate through ``resolve_safe`` and drop broken
        pointers silently.
        """
        root = Path(record.work_dir)
        entries: list[dict[str, Any]] = []
        if not root.is_dir():
            return entries
        try:
            candidates = list(root.rglob(_RESULT_SUMMARY_FILENAME))
        except OSError:
            return entries
        for summary_path in sorted(candidates):
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            base = summary_path.parent
            for item in _select_structure_products(payload.get("products") or []):
                abs_path = base / str(item["path"])
                try:
                    rel = abs_path.relative_to(root)
                except ValueError:
                    continue
                rel_posix = rel.as_posix()
                resolved = resolve_safe(root, rel_posix)
                if resolved is None:
                    continue
                try:
                    text = resolved.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                meta = self._parse_structure_meta(record, text)
                if meta is None:
                    continue
                entries.append(
                    self._entry(record, item, rel_posix, meta, remote=False, needs_fetch=False)
                )
        return entries

    # ------------------------------------------------------------------ #
    # Remote handling
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_remote(record: JobRecord) -> bool:
        """True when the job ran (or is running) on a remote node."""
        result = record.result or {}
        return bool(
            record.remote_job_id
            or result.get("lsf_job_id")
            or result.get("execution_kind") == "remote"
        )

    def _remote_placeholder(self, record: JobRecord) -> dict[str, Any]:
        """Coarse listing entry for a remote job whose files were not probed."""
        return {
            "source_id": f"job_{record.id}:",
            "job_id": record.id,
            "job_name": record.spec.name,
            "workflow": record.spec.workflow,
            "project_id": record.project_id or record.spec.project_id,
            "completed_at": record.completed_at or "",
            "label": record.spec.name or record.id,
            "path": "",
            "formula": "",
            "atom_count": 0,
            "charge": 0,
            "multiplicity": 1,
            "has_3d": True,
            "remote": True,
            "needs_fetch": True,
        }

    def _probe_remote(self, record: JobRecord) -> list[dict[str, Any]] | None:
        """Probe a remote job's result summaries over SFTP (30 s TTL cache).

        Returns discovered entries, or ``None`` when the probe is
        impossible (no fetcher, node unreachable, unreadable payloads) so
        the caller degrades to a ``needs_fetch`` placeholder.
        """
        if self._fetcher is None:
            return None
        cached = self._probe_cache.get(record.id)
        if cached is not None and time.monotonic() - cached[0] < _PROBE_TTL_SECONDS:
            return cached[1]

        entries: list[dict[str, Any]] | None = None
        try:
            summaries = [
                rel
                for rel, _info in self._fetcher.walk_remote_files(
                    record, include=[f"*{_RESULT_SUMMARY_FILENAME}"]
                )
            ]
            summaries = sorted(summaries)[:_PROBE_MAX_SUMMARIES]
            found: list[dict[str, Any]] = []
            for summary_rel in summaries:
                payload = json.loads(
                    self._fetcher.read_file(record, summary_rel).decode("utf-8", errors="replace")
                )
                if not isinstance(payload, dict):
                    continue
                base = posixpath.dirname(summary_rel)
                for item in _select_structure_products(payload.get("products") or []):
                    if len(found) >= _PROBE_MAX_PRODUCTS:
                        break
                    rel_posix = self._remote_join(base, str(item["path"]))
                    if rel_posix is None:
                        continue
                    if not self._fetcher.file_exists(record, rel_posix):
                        continue
                    data = self._fetcher.read_file(record, rel_posix)
                    meta = self._parse_structure_meta(
                        record, data.decode("utf-8", errors="replace")
                    )
                    if meta is None:
                        continue
                    found.append(
                        self._entry(record, item, rel_posix, meta, remote=True, needs_fetch=False)
                    )
            entries = found
        except _REMOTE_ERRORS as exc:
            logger.debug("Remote structure probe failed for job %s: %s", record.id, exc)
            entries = None
        self._probe_cache[record.id] = (time.monotonic(), entries)
        return entries

    @staticmethod
    def _remote_join(base: str, rel_path: str) -> str | None:
        """POSIX-join a product path onto its summary dir (None on traversal)."""
        joined = (
            posixpath.normpath(posixpath.join(base, rel_path))
            if base
            else (posixpath.normpath(rel_path))
        )
        if joined.startswith("..") or posixpath.isabs(joined):
            return None
        return joined

    def _fetch_remote_xyz(self, record: JobRecord, rel_path: str) -> bytes:
        """Read a structure file from the remote node (never resolved locally)."""
        rel_posix = self._remote_join("", rel_path)
        if rel_posix is None:
            raise ValueError(f"Invalid remote path: {rel_path}")
        if self._fetcher is None:
            raise ValueError(
                f"Remote source {rel_path} requires a configured remote fetcher "
                "(remote execution is not configured on this server)"
            )
        try:
            return self._fetcher.read_file(record, rel_posix)
        except FileNotFoundError as exc:
            raise ValueError(f"Source file missing on remote node: {rel_path}") from exc
        except _REMOTE_ERRORS as exc:
            raise ValueError(f"Remote node unavailable for job {record.id}: {exc}") from exc

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    def _parse_structure_meta(self, record: JobRecord, text: str) -> dict[str, Any] | None:
        """Parse the first XYZ frame into listing metadata (None on failure)."""
        try:
            result = parse_xyz_text(text)
        except (ValueError, IndexError):
            return None
        if not result.structures:
            return None
        first = result.structures[0]
        charge, mult = self._resolve_charge_mult(record, _first_frame_comment(text))
        return {
            "formula": first.formula,
            "atom_count": first.atom_count,
            "charge": charge,
            "multiplicity": mult,
            "has_3d": first.has_3d,
        }

    def _build_asset(self, record: JobRecord, rel_path: str, text: str) -> dict[str, Any] | None:
        """Build a ``StructureAssetModel``-shaped dict from XYZ text."""
        try:
            result = parse_xyz_text(text)
        except (ValueError, IndexError):
            return None
        if not result.structures:
            return None
        first = result.structures[0]
        charge, mult = self._resolve_charge_mult(record, _first_frame_comment(text))
        return {
            "asset_id": first.asset_id,
            "name": rel_path.rsplit("/", 1)[-1],
            "source_type": "job_artifact",
            "original_format": "xyz",
            "xyz": first.xyz,
            "molfile": None,
            "has_3d": first.has_3d,
            "charge": charge,
            "multiplicity": mult,
            "atom_count": first.atom_count,
            "formula": first.formula,
            "smiles": None,
            "normalized_path": None,
            "warnings": [],
            "errors": [],
        }

    @staticmethod
    def _resolve_charge_mult(record: JobRecord, comment: str) -> tuple[int, int]:
        """Charge/multiplicity precedence: XYZ comment → spec.input → (0, 1)."""
        charge: int | None = None
        mult: int | None = None
        match = _CHARGE_RE.search(comment)
        if match:
            charge = int(match.group(1))
        match = _MULT_RE.search(comment)
        if match:
            mult = int(match.group(1))
        spec_input = record.spec.input if record.spec else {}
        if charge is None:
            charge = _coerce_int(spec_input.get("charge"), 0)
        if mult is None:
            mult = _coerce_int(spec_input.get("multiplicity"), 1)
        return charge, mult

    @staticmethod
    def _entry(
        record: JobRecord,
        item: dict[str, Any],
        rel_posix: str,
        meta: dict[str, Any],
        *,
        remote: bool,
        needs_fetch: bool,
    ) -> dict[str, Any]:
        return {
            "source_id": f"job_{record.id}:{rel_posix}",
            "job_id": record.id,
            "job_name": record.spec.name,
            "workflow": record.spec.workflow,
            "project_id": record.project_id or record.spec.project_id,
            "completed_at": record.completed_at or "",
            "label": str(item.get("label") or item.get("path") or ""),
            "path": rel_posix,
            "formula": meta["formula"],
            "atom_count": meta["atom_count"],
            "charge": meta["charge"],
            "multiplicity": meta["multiplicity"],
            "has_3d": meta["has_3d"],
            "remote": remote,
            "needs_fetch": needs_fetch,
        }
