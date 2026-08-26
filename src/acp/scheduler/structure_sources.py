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

from acp.confsearch.manifest import (
    find_confsearch_manifest,
    read_manifest,
    resolve_manifest_geometry,
)
from acp.intake import parse_xyz_text
from acp.scheduler.artifacts import compute_checksum
from acp.scheduler.files import resolve_safe
from acp.scheduler.jobs import JobRecord, JobStatus
from acp.scheduler.naming import canonical_molecule_name, molecule_name_from_input

if TYPE_CHECKING:
    from acp.scheduler.remote.fetcher import RemoteResultFetcher

logger = logging.getLogger(__name__)

__all__ = ["StructureSourceService"]

_RESULT_SUMMARY_FILENAME = "result_summary.json"
_RESULT_MANIFEST_FILENAME = "result_manifest.json"
_CONFSEARCH_MANIFEST_FILENAME = "confsearch_manifest.json"
#: Product kinds that carry a reusable 3D structure (batch plan §6): the
#: unified v2 manifest writes ``structure``; legacy summaries write ``xyz``.
_STRUCTURE_KINDS = frozenset({"xyz", "structure"})
_ROLE_FINAL_STRUCTURE = "final_stable_structure"
# Workflows with an explicit structure-source contract.  Retired names remain
# here so historical jobs continue to resolve through the same policy.
_CONFORMER_SEARCH_WORKFLOWS = frozenset(
    {"Confsearch", "energy", "ensemble", "xtbmd_censo_energy", "conformer"}
)
_PESSEARCH_WORKFLOWS = frozenset({"PESsearch"})
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
#: ``TAG: TS | candidate_id=ts_guess_001 | ...`` comment-line contract
#: (batch plan §4) — parsed for the result-list badges.
_TAG_RE = re.compile(r"\bTAG\s*[:=]\s*(TS|INT)\b", re.IGNORECASE)
_TAG_ID_RE = re.compile(r"\bcandidate_id\s*=\s*([^\s|]+)", re.IGNORECASE)
_CANDIDATE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])((?:ts|int)_(?:guess|candidate)_[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_S2_CANDIDATE_ID_RE = re.compile(r"s2_candidate[_-]([A-Za-z0-9_-]+)", re.IGNORECASE)

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


def _candidate_id_from_path(rel_path: str) -> str:
    """Recover an S2 candidate id when an old XYZ comment lacks metadata."""
    match = _CANDIDATE_PATH_RE.search(rel_path.replace("\\", "/"))
    return match.group(1) if match else ""


def _candidate_id_from_item(item: dict[str, Any]) -> str:
    """Recover an S2 candidate id from manifest metadata or its path."""
    explicit = str(item.get("candidate_id") or "").strip()
    if explicit:
        return explicit
    for value in (item.get("id"), item.get("label"), item.get("path")):
        text = str(value or "")
        match = _CANDIDATE_PATH_RE.search(text)
        if match:
            return match.group(1)
        match = _S2_CANDIDATE_ID_RE.search(text)
        if match:
            return match.group(1)
    return ""


def _tag_from_item(item: dict[str, Any]) -> str:
    """Recover only the exceptional TS marker from a product descriptor."""
    for value in (item.get("tag"), item.get("id"), item.get("label"), item.get("path")):
        text = str(value or "")
        if re.search(r"\bTS\b", text, re.IGNORECASE):
            return "TS"
    candidate_id = _candidate_id_from_item(item)
    return "TS" if candidate_id.lower().startswith("ts_") else ""


def _conformer_sort_key(entry: dict[str, Any]) -> tuple[int, float, str]:
    """Sort a Confsearch conformer with rank first, energy as a fallback."""
    try:
        rank = int(entry.get("rank") or 0)
    except (TypeError, ValueError):
        rank = 0
    try:
        energy = float(
            entry.get("free_energy_hartree")
            if entry.get("free_energy_hartree") is not None
            else entry.get("energy_hartree")
        )
    except (TypeError, ValueError):
        energy = float("inf")
    return (rank if rank > 0 else 10**9, energy, str(entry.get("conf_id") or ""))


def _select_rank1_conformer(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the lowest-energy/rank-1 conformer from a Confsearch payload."""
    conformers = [item for item in payload.get("conformers") or [] if isinstance(item, dict)]
    if not conformers:
        return None
    return min(conformers, key=_conformer_sort_key)


def _is_pes_candidate_item(item: dict[str, Any]) -> bool:
    """Return whether a product is an exported PES stationary-point candidate."""
    if not _product_is_xyz(item):
        return False
    values = [str(item.get(key) or "") for key in ("id", "label", "path")]
    joined = " ".join(values).lower()
    return (
        "s2_candidate" in joined
        or "s2 candidate" in joined
        or "/s2_candidates/" in joined
        or _candidate_id_from_item(item) != ""
    )


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
        return kind in _STRUCTURE_KINDS
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
    if candidates:
        return candidates
    # Historical energy summaries sometimes predate the explicit role and
    # publish both ``all_conformers.xyz`` and ``*_global_min.xyz``.  The
    # source picker represents one reusable stationary structure, so prefer
    # the named global minimum when it is available.  Keep the broader legacy
    # fallback for old jobs that do not expose a global minimum at all.
    global_min = [
        item
        for item in legacy
        if str(item.get("path") or "").rsplit("/", 1)[-1].endswith("_global_min.xyz")
    ]
    return global_min or legacy


def _select_manifest_structure_products(products: list[Any]) -> list[dict[str, Any]]:
    """Select reusable structure products from a unified result manifest.

    Explicit S2/S3/S4 candidates remain plural, while legacy energy manifests
    that expose both an ensemble and ``global_min`` contribute only the latter.
    """
    structures = [
        item
        for item in products
        if isinstance(item, dict) and item.get("path") and _product_is_xyz(item)
    ]
    if not structures:
        return []
    # The energy workflow's v2 manifest predates ``role`` and registers both
    # its conformer ensemble and its global minimum as ``kind=structure``.
    # Only the latter is a single reusable stationary structure.  S2/S3/S4
    # candidate products have neither this id nor label and remain plural.
    preferred = [
        item
        for item in structures
        if str(item.get("id") or "").lower() in {"global_min", "global_minimum"}
        or "global minimum" in str(item.get("label") or "").lower()
        or item.get("role") == _ROLE_FINAL_STRUCTURE
    ]
    return preferred or structures


def _select_minimum_manifest_products(products: list[Any]) -> list[dict[str, Any]]:
    """Select exactly one final minimum from a legacy v2 structure manifest."""
    structures = _select_manifest_structure_products(products)
    if not structures:
        return []
    if len(structures) == 1:
        return structures
    ranked = [
        item
        for item in structures
        if re.search(r"(?:rank\s*1|lowest|minimum|global_min)",
                     " ".join(str(item.get(key) or "") for key in ("id", "label", "path")),
                     re.IGNORECASE)
    ]
    return ranked[:1] if ranked else structures[:1]


def _select_minimum_legacy_products(products: list[Any]) -> list[dict[str, Any]]:
    """Select one final minimum from a legacy result summary."""
    structures = _select_structure_products(products)
    if not structures:
        return []
    if len(structures) == 1:
        return structures
    optimized = [
        item
        for item in structures
        if str(item.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
        == "optimized.xyz"
    ]
    return optimized[:1] or structures[:1]


def _select_pes_products(products: list[Any]) -> list[dict[str, Any]]:
    """Select all exported PES stationary-point products, and only those."""
    return [
        item
        for item in products
        if isinstance(item, dict) and item.get("path") and _is_pes_candidate_item(item)
    ]


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
        return self._deduplicate_entries(entries)[:limit]

    @staticmethod
    def _deduplicate_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply a final semantic de-duplication guard before API exposure."""
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            workflow = str(entry.get("workflow") or "")
            job_id = str(entry.get("job_id") or "")
            if workflow in _CONFORMER_SEARCH_WORKFLOWS:
                identity = "minimum"
            elif workflow in _PESSEARCH_WORKFLOWS:
                identity = str(entry.get("candidate_id") or entry.get("path") or "")
            else:
                identity = str(entry.get("path") or "")
            key = (job_id, workflow, identity)
            if key in seen:
                continue
            seen.add(key)
            unique.append(entry)
        return unique

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
        """Apply the workflow-specific structure-source policy."""
        workflow = record.spec.workflow
        if workflow == "Confsearch":
            return self._discover_confsearch_job(record)
        if workflow in _CONFORMER_SEARCH_WORKFLOWS:
            return self._discover_conformer_legacy_job(record)
        if workflow in _PESSEARCH_WORKFLOWS:
            return self._discover_pessearch_job(record)
        return self._discover_generic_job(record)

    def _discover_confsearch_job(self, record: JobRecord) -> list[dict[str, Any]]:
        """Return only rank-1 from the active Confsearch manifest."""
        root = Path(record.work_dir)
        manifest_path = find_confsearch_manifest(root)
        if manifest_path is None:
            return self._discover_conformer_legacy_job(record)
        try:
            payload = read_manifest(manifest_path)
            conformer = _select_rank1_conformer(payload)
            if conformer is None:
                return []
            geometry = resolve_manifest_geometry(
                manifest_path, str(conformer.get("geometry") or "")
            )
            rel_posix = geometry.resolve().relative_to(root.resolve()).as_posix()
            resolved = resolve_safe(root, rel_posix)
            if resolved is None:
                return []
            text = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError, KeyError):
            logger.debug("Confsearch manifest unreadable for job %s", record.id, exc_info=True)
            return []
        meta = self._parse_structure_meta(record, text, rel_posix)
        if meta is None:
            return []
        conf_id = str(conformer.get("conf_id") or "rank_1")
        item = {
            "id": f"confsearch_{conf_id}",
            "label": f"Lowest-energy conformer ({conf_id})",
            "path": rel_posix,
            "kind": "structure",
        }
        return [self._entry(record, item, rel_posix, meta, remote=False, needs_fetch=False)]

    def _discover_conformer_legacy_job(self, record: JobRecord) -> list[dict[str, Any]]:
        """Return one final minimum structure for legacy conformer workflows."""
        return self._discover_product_listings(
            record,
            selectors=(
                (_RESULT_MANIFEST_FILENAME, _select_minimum_manifest_products),
                (_RESULT_SUMMARY_FILENAME, _select_minimum_legacy_products),
            ),
            max_entries=1,
        )

    def _discover_pessearch_job(self, record: JobRecord) -> list[dict[str, Any]]:
        """Return every exported TS/INT stationary-point candidate."""
        return self._discover_product_listings(
            record,
            selectors=(
                (_RESULT_MANIFEST_FILENAME, _select_pes_products),
                (_RESULT_SUMMARY_FILENAME, _select_pes_products),
            ),
            candidate_hints=True,
        )

    def _discover_generic_job(self, record: JobRecord) -> list[dict[str, Any]]:
        """Discover structure sources in a local job work directory.

        Discovery order (batch plan §6): unified ``result_manifest.json``
        products (``kind: "structure"`` — S2 candidates, batch S3/S4
        outputs) first, then the legacy ``result_summary.json`` pointers,
        then nothing else — files not referenced by either manifest are
        invisible by design. Broken pointers are dropped silently.
        """
        root = Path(record.work_dir)
        entries: list[dict[str, Any]] = []
        if not root.is_dir():
            return entries
        seen: set[str] = set()
        seen_candidates: set[str] = set()
        for filename, selector in (
            (_RESULT_MANIFEST_FILENAME, _select_manifest_structure_products),
            (_RESULT_SUMMARY_FILENAME, _select_structure_products),
        ):
            try:
                candidates = list(root.rglob(filename))
            except OSError:
                continue
            for listing_path in sorted(candidates):
                try:
                    payload = json.loads(listing_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                base = listing_path.parent
                for item in selector(payload.get("products") or []):
                    abs_path = base / str(item["path"])
                    try:
                        rel = abs_path.relative_to(root)
                    except ValueError:
                        continue
                    rel_posix = rel.as_posix()
                    if rel_posix in seen:
                        continue
                    resolved = resolve_safe(root, rel_posix)
                    if resolved is None:
                        continue
                    try:
                        text = resolved.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
                    meta = self._parse_structure_meta(record, text, rel_posix)
                    if meta is None:
                        continue
                    seen.add(rel_posix)
                    candidate_id = str(meta.get("candidate_id") or "")
                    if candidate_id and candidate_id in seen_candidates:
                        continue
                    if candidate_id:
                        seen_candidates.add(candidate_id)
                    entries.append(
                        self._entry(record, item, rel_posix, meta, remote=False, needs_fetch=False)
                    )
        return entries

    def _discover_product_listings(
        self,
        record: JobRecord,
        *,
        selectors: tuple[tuple[str, Any], ...],
        candidate_hints: bool = False,
        max_entries: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read the first usable authoritative listing for a workflow."""
        root = Path(record.work_dir)
        if not root.is_dir():
            return []
        for filename, selector in selectors:
            try:
                listing_paths = sorted(root.rglob(filename))
            except OSError:
                continue
            for listing_path in listing_paths:
                try:
                    payload = json.loads(listing_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                entries: list[dict[str, Any]] = []
                seen_paths: set[str] = set()
                seen_candidates: set[str] = set()
                for item in selector(payload.get("products") or []):
                    rel_posix = self._resolve_local_product_path(root, listing_path, item)
                    if rel_posix is None or rel_posix in seen_paths:
                        continue
                    resolved = resolve_safe(root, rel_posix)
                    if resolved is None:
                        continue
                    try:
                        text = resolved.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue
                    candidate_hint = _candidate_id_from_item(item) if candidate_hints else ""
                    tag_hint = _tag_from_item(item) if candidate_hints else ""
                    meta = self._parse_structure_meta(
                        record,
                        text,
                        rel_posix,
                        candidate_hint=candidate_hint,
                        tag_hint=tag_hint,
                    )
                    if meta is None:
                        continue
                    candidate_id = str(meta.get("candidate_id") or "")
                    if candidate_id and candidate_id in seen_candidates:
                        continue
                    seen_paths.add(rel_posix)
                    if candidate_id:
                        seen_candidates.add(candidate_id)
                    entries.append(
                        self._entry(record, item, rel_posix, meta, remote=False, needs_fetch=False)
                    )
                    if max_entries is not None and len(entries) >= max_entries:
                        break
                if entries:
                    return entries
        return []

    @staticmethod
    def _resolve_local_product_path(
        root: Path,
        listing_path: Path,
        item: dict[str, Any],
    ) -> str | None:
        """Resolve a manifest-relative product to a root-relative POSIX path."""
        try:
            return (listing_path.parent / str(item["path"])).resolve().relative_to(root.resolve()).as_posix()
        except (KeyError, OSError, ValueError):
            return None

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
            "tag": "",
            "candidate_id": "",
            "remote": True,
            "needs_fetch": True,
        }

    def _probe_remote(self, record: JobRecord) -> list[dict[str, Any]] | None:
        """Apply the workflow-specific remote structure-source policy."""
        cached = self._probe_cache.get(record.id)
        if cached is not None and time.monotonic() - cached[0] < _PROBE_TTL_SECONDS:
            return cached[1]
        if record.spec.workflow == "Confsearch":
            entries = self._probe_remote_confsearch(record)
        elif record.spec.workflow in _CONFORMER_SEARCH_WORKFLOWS:
            entries = self._probe_remote_conformer_legacy(record)
        elif record.spec.workflow in _PESSEARCH_WORKFLOWS:
            entries = self._probe_remote_pessearch(record)
        else:
            entries = self._probe_remote_generic(record)
        self._probe_cache[record.id] = (time.monotonic(), entries)
        return entries

    def _probe_remote_confsearch(self, record: JobRecord) -> list[dict[str, Any]] | None:
        """Return only rank-1 from a remote Confsearch manifest."""
        if self._fetcher is None:
            return None
        try:
            listings = [
                rel
                for rel, _info in self._fetcher.walk_remote_files(
                    record, include=[f"*{_CONFSEARCH_MANIFEST_FILENAME}"]
                )
                if rel.endswith(_CONFSEARCH_MANIFEST_FILENAME)
            ]
            for listing_rel in sorted(listings):
                payload = json.loads(
                    self._fetcher.read_file(record, listing_rel).decode("utf-8", errors="replace")
                )
                if not isinstance(payload, dict):
                    continue
                conformer = _select_rank1_conformer(payload)
                if conformer is None:
                    continue
                geometry_ref = str(conformer.get("geometry") or "")
                rel_posix = self._remote_join(posixpath.dirname(listing_rel), geometry_ref)
                if rel_posix is None or not self._fetcher.file_exists(record, rel_posix):
                    continue
                data = self._fetcher.read_file(record, rel_posix)
                text = data.decode("utf-8", errors="replace")
                meta = self._parse_structure_meta(record, text, rel_posix)
                if meta is None:
                    continue
                conf_id = str(conformer.get("conf_id") or "rank_1")
                item = {
                    "id": f"confsearch_{conf_id}",
                    "label": f"Lowest-energy conformer ({conf_id})",
                    "path": rel_posix,
                    "kind": "structure",
                }
                return [self._entry(record, item, rel_posix, meta, remote=True, needs_fetch=False)]
        except _REMOTE_ERRORS as exc:
            logger.debug("Remote Confsearch probe failed for job %s: %s", record.id, exc)
            return None
        return self._probe_remote_conformer_legacy(record)

    def _probe_remote_conformer_legacy(self, record: JobRecord) -> list[dict[str, Any]] | None:
        """Return one minimum from legacy conformer result listings."""
        return self._probe_remote_product_listings(
            record,
            selectors=(
                (_RESULT_MANIFEST_FILENAME, _select_minimum_manifest_products),
                (_RESULT_SUMMARY_FILENAME, _select_minimum_legacy_products),
            ),
            max_entries=1,
        )

    def _probe_remote_pessearch(self, record: JobRecord) -> list[dict[str, Any]] | None:
        """Return all remote PES stationary-point candidates."""
        return self._probe_remote_product_listings(
            record,
            selectors=(
                (_RESULT_MANIFEST_FILENAME, _select_pes_products),
                (_RESULT_SUMMARY_FILENAME, _select_pes_products),
            ),
            candidate_hints=True,
        )

    def _probe_remote_product_listings(
        self,
        record: JobRecord,
        *,
        selectors: tuple[tuple[str, Any], ...],
        candidate_hints: bool = False,
        max_entries: int | None = None,
    ) -> list[dict[str, Any]] | None:
        """Read the first usable authoritative result listing over SFTP."""
        if self._fetcher is None:
            return None
        try:
            listings = {
                rel
                for rel, _info in self._fetcher.walk_remote_files(
                    record,
                    include=[f"*{filename}" for filename, _selector in selectors],
                )
            }
            for filename, selector in selectors:
                for listing_rel in sorted(
                    rel for rel in listings if rel.endswith(filename)
                ):
                    payload = json.loads(
                        self._fetcher.read_file(record, listing_rel).decode(
                            "utf-8", errors="replace"
                        )
                    )
                    if not isinstance(payload, dict):
                        continue
                    found: list[dict[str, Any]] = []
                    seen_paths: set[str] = set()
                    seen_candidates: set[str] = set()
                    base = posixpath.dirname(listing_rel)
                    for item in selector(payload.get("products") or []):
                        if not isinstance(item, dict):
                            continue
                        rel_posix = self._remote_join(base, str(item.get("path") or ""))
                        if rel_posix is None or rel_posix in seen_paths:
                            continue
                        if not self._fetcher.file_exists(record, rel_posix):
                            continue
                        data = self._fetcher.read_file(record, rel_posix)
                        meta = self._parse_structure_meta(
                            record,
                            data.decode("utf-8", errors="replace"),
                            rel_posix,
                            candidate_hint=(
                                _candidate_id_from_item(item) if candidate_hints else ""
                            ),
                            tag_hint=_tag_from_item(item) if candidate_hints else "",
                        )
                        if meta is None:
                            continue
                        candidate_id = str(meta.get("candidate_id") or "")
                        if candidate_id and candidate_id in seen_candidates:
                            continue
                        seen_paths.add(rel_posix)
                        if candidate_id:
                            seen_candidates.add(candidate_id)
                        found.append(
                            self._entry(record, item, rel_posix, meta, remote=True, needs_fetch=False)
                        )
                        if max_entries is not None and len(found) >= max_entries:
                            break
                    if found:
                        return found
        except _REMOTE_ERRORS as exc:
            logger.debug("Remote structure probe failed for job %s: %s", record.id, exc)
            return None
        return []

    def _probe_remote_generic(self, record: JobRecord) -> list[dict[str, Any]] | None:
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
            listings = [
                rel
                for rel, _info in self._fetcher.walk_remote_files(
                    record,
                    include=[f"*{_RESULT_SUMMARY_FILENAME}", f"*{_RESULT_MANIFEST_FILENAME}"],
                )
            ]
            listing_rels = sorted(listings)[:_PROBE_MAX_SUMMARIES]
            found: list[dict[str, Any]] = []
            seen_paths: set[str] = set()
            seen_candidates: set[str] = set()
            for listing_rel in listing_rels:
                payload = json.loads(
                    self._fetcher.read_file(record, listing_rel).decode("utf-8", errors="replace")
                )
                if not isinstance(payload, dict):
                    continue
                selector = (
                    _select_manifest_structure_products
                    if listing_rel.endswith(_RESULT_MANIFEST_FILENAME)
                    else _select_structure_products
                )
                base = posixpath.dirname(listing_rel)
                for item in selector(payload.get("products") or []):
                    if len(found) >= _PROBE_MAX_PRODUCTS:
                        break
                    rel_posix = self._remote_join(base, str(item["path"]))
                    if rel_posix is None:
                        continue
                    if rel_posix in seen_paths:
                        continue
                    if not self._fetcher.file_exists(record, rel_posix):
                        continue
                    data = self._fetcher.read_file(record, rel_posix)
                    meta = self._parse_structure_meta(
                        record, data.decode("utf-8", errors="replace"), rel_posix
                    )
                    if meta is None:
                        continue
                    seen_paths.add(rel_posix)
                    candidate_id = str(meta.get("candidate_id") or "")
                    if candidate_id and candidate_id in seen_candidates:
                        continue
                    if candidate_id:
                        seen_candidates.add(candidate_id)
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

    def _parse_structure_meta(
        self,
        record: JobRecord,
        text: str,
        rel_path: str = "",
        *,
        candidate_hint: str = "",
        tag_hint: str = "",
    ) -> dict[str, Any] | None:
        """Parse the first XYZ frame into listing metadata (None on failure)."""
        try:
            result = parse_xyz_text(text)
        except (ValueError, IndexError):
            return None
        if not result.structures:
            return None
        first = result.structures[0]
        comment = _first_frame_comment(text)
        charge, mult = self._resolve_charge_mult(record, comment)
        tag_match = _TAG_RE.search(comment)
        tag_id_match = _TAG_ID_RE.search(comment)
        candidate_id = (
            tag_id_match.group(1)
            if tag_id_match
            else (_candidate_id_from_path(rel_path) or candidate_hint)
        )
        inferred_tag = (
            "TS"
            if candidate_id.lower().startswith("ts_")
            else ("INT" if candidate_id.lower().startswith("int_") else "")
        )
        # INT is the default stationary-point interpretation.  Persist only
        # the exceptional TS marker so callers cannot accidentally render or
        # re-apply a redundant INT annotation.
        tag = tag_match.group(1).upper() if tag_match else (tag_hint or inferred_tag)
        return {
            "formula": first.formula,
            "atom_count": first.atom_count,
            "charge": charge,
            "multiplicity": mult,
            "has_3d": first.has_3d,
            "tag": "TS" if tag == "TS" else "",
            "candidate_id": candidate_id,
        }

    @staticmethod
    def _inherited_structure_name(
        record: JobRecord,
        rel_path: str,
        *,
        candidate_id: str = "",
    ) -> str:
        """Return a task identity that remains unique for S2 candidates.

        A completed Job has one base molecule name, but a PESsearch result can
        contain several independently selectable stationary-point guesses.
        Preserve the base name and append the source candidate id only when
        it is present.  The candidate id is kept as metadata as well, so the
        suffix is human-readable without making the Job ID part of a molecule
        name.
        """
        base = StructureSourceService._record_molecule_name(record, rel_path)
        candidate = canonical_molecule_name(candidate_id)
        if candidate and candidate.lower() != base.lower():
            return canonical_molecule_name(f"{base}__{candidate}", fallback=base)
        return base

    def _build_asset(self, record: JobRecord, rel_path: str, text: str) -> dict[str, Any] | None:
        """Build a ``StructureAssetModel``-shaped dict from XYZ text."""
        try:
            result = parse_xyz_text(text)
        except (ValueError, IndexError):
            return None
        if not result.structures:
            return None
        first = result.structures[0]
        meta = self._parse_structure_meta(record, text, rel_path)
        if meta is None:
            return None
        candidate_id = str(meta.get("candidate_id") or "")
        return {
            "asset_id": first.asset_id,
            "name": rel_path.rsplit("/", 1)[-1],
            "molecule_name": self._inherited_structure_name(
                record, rel_path, candidate_id=candidate_id
            ),
            "source_type": "job_artifact",
            "original_format": "xyz",
            "xyz": first.xyz,
            "molfile": None,
            "has_3d": bool(meta["has_3d"]),
            "charge": int(meta["charge"]),
            "multiplicity": int(meta["multiplicity"]),
            "atom_count": int(meta["atom_count"]),
            "formula": str(meta["formula"]),
            "smiles": None,
            "normalized_path": None,
            "tag": str(meta.get("tag") or ""),
            "candidate_id": candidate_id,
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
    def _record_molecule_name(record: JobRecord, rel_path: str) -> str:
        """Return only the molecule identity that may be inherited.

        ``spec.name`` is intentionally excluded: it is the physical task
        directory label and may contain workflow, remark, or legacy Job-ID
        text.  Older records fall back to their input source, then the
        artifact stem as a deterministic last resort.
        """
        spec = record.spec
        name = canonical_molecule_name(getattr(spec, "molecule_name", ""))
        if name:
            return name
        name = molecule_name_from_input(getattr(spec, "input", {}))
        if name:
            return name
        return canonical_molecule_name(rel_path, fallback="mol")

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
            "molecule_name": StructureSourceService._inherited_structure_name(
                record, rel_posix, candidate_id=str(meta.get("candidate_id") or "")
            ),
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
            "tag": meta.get("tag") or "",
            "candidate_id": meta.get("candidate_id") or "",
            "remote": remote,
            "needs_fetch": needs_fetch,
        }
