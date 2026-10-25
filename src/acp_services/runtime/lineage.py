"""Read-only research evidence projection (lineage edges + versioned revisions).

B08-5 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 46).  This module
owns the **platform-side** research-provenance projection: a small, pure data
model of the edges that connect a task to the things it **uses**, **produces**,
**derives from**, **revises** (human selection/revision) and **reuses**, plus an
append-only, versioned revision ledger.

Design rules (frozen by the todo's Must NOT clauses):

* **No second task state machine.**  An edge names a relation between two
  identities; it carries no lifecycle state and this module never transitions,
  queues or executes anything.  Task status stays in the scheduler authority.
* **Read-only projection.**  :class:`EvidenceProjection` is immutable; its query
  methods only read prepared data.  :class:`TaskEvidenceBuilder` accumulates
  edges *out of band* and freezes them once — it is not a service and never
  writes a file or a database.
* **Human revisions never overwrite the original scientific result.**  The
  original result is registered once (write-once) and returned as an unchanged
  deep copy; revisions are appended with a monotonically increasing ``revision``
  number and reference — but never mutate — the original.
* **No invented science.**  Scientific fields supplied by lane C (C02) are
  carried **verbatim** under ``evidence`` and mapped through the documented
  :data:`C02_FIELD_MAP`.  Native indices are preserved as-is; this module never
  reclassifies a ``source_kind`` into a scientific meaning and never renumbers a
  frame / mode / atom index.

C02 channel
-----------
The scientific derived fields this projection consumes are delivered by lane C
(C02); the field mapping and its diff against this platform map are recorded in

* ``tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md`` §2.4
  (``derived`` → B-T46 研究溯源边投影 + candidate metadata), and
* ``tests/baseline/services-evidence/lineage_field_map.md`` (this side's diff).

The provider side is ``delivered`` (HN-02 state A「契约可消费」); the **cross-lane
end-to-end** state (HN-02 §9.3 state B) is ``NOT_VERIFIED`` until B/A integrate —
:data:`C02_FIELD_MAP_STATUS` says so explicitly rather than assuming it.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package.  This module is stdlib only; the delegation adapter that reads a
task's ``spec.input`` / ``result_manifest.json`` lives on the legacy side
(``acp.api.v2_routes`` lineage projection endpoint).

Author: ACP Services (B lane, todo 46).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from acp_services.runtime.helpers import utc_now_iso as _utc_now_iso

logger = logging.getLogger(__name__)

__all__ = [
    "C02_FIELD_MAP",
    "C02_FIELD_MAP_STATUS",
    "EVIDENCE_PROJECTION_VERSION",
    "EdgeType",
    "EvidenceProjection",
    "FieldMapEntry",
    "IdentityMapping",
    "LineageEdge",
    "LineageNode",
    "MappingKind",
    "OriginalResultOverwriteError",
    "RevisionEntry",
    "RevisionLedger",
    "TaskEvidenceBuilder",
    "original_digest",
]

#: Wire version of :meth:`EvidenceProjection.to_dict` (additive evolution only).
EVIDENCE_PROJECTION_VERSION = 1


def _int_or_none(value: object) -> int | None:
    """Return *value* when it is a real ``int`` (bool is not an int here)."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class EdgeType(str, Enum):
    """The five frozen provenance edge types (todo 46)."""

    #: 使用 — the subject consumes an input / upstream artifact.
    USES = "uses"
    #: 产生 — the subject produces a result product.
    PRODUCES = "produces"
    #: 派生 — the subject derives a product from other products (C02 fields).
    DERIVES = "derives"
    #: 人工选择或修订 — a human selection or revision (versioned, non-authoritative).
    REVISION = "revision"
    #: 复用 — the subject reuses a previously produced result (resume/rerun).
    REUSES = "reuses"


class MappingKind(str, Enum):
    """Identity spaces a provenance edge may preserve across a projection."""

    ATOM = "atom"
    FRAME = "frame"
    MODE = "mode"


# ---------------------------------------------------------------------------
# Identity mapping (atom / frame / mode preservation)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityMapping:
    """One preserved identity mapping carried by a provenance edge.

    ``native_index`` is the index in the **source** numbering space (native frame
    / mode / atom index) and is never renumbered.  ``kind`` is ``None`` when the
    mapping is not classified as an atom/frame/mode (the platform must not invent
    a scientific meaning for an opaque C02 ``source_kind``); ``source_kind`` keeps
    the raw token verbatim so a consumer can decide for itself.
    """

    native_index: int | None = None
    kind: MappingKind | None = None
    stable_id: str = ""
    artifact: str = ""
    source_kind: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe mapping (``None`` kind stays ``None``)."""
        return {
            "native_index": self.native_index,
            "kind": self.kind.value if isinstance(self.kind, MappingKind) else None,
            "stable_id": self.stable_id,
            "artifact": self.artifact,
            "source_kind": self.source_kind,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> IdentityMapping:
        """Rebuild from :meth:`to_dict` output (defensive: unknown kind → ``None``)."""
        raw_kind = payload.get("kind")
        kind: MappingKind | None = None
        if isinstance(raw_kind, str):
            try:
                kind = MappingKind(raw_kind)
            except ValueError:
                kind = None
        return cls(
            native_index=_int_or_none(payload.get("native_index")),
            kind=kind,
            stable_id=str(payload.get("stable_id") or ""),
            artifact=str(payload.get("artifact") or ""),
            source_kind=str(payload.get("source_kind") or ""),
        )


# ---------------------------------------------------------------------------
# Edges and nodes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineageEdge:
    """One read-only provenance edge ``subject --edge_type--> object_ref``.

    ``evidence`` carries provenance extras verbatim (never interpreted).  For a
    :data:`EdgeType.DERIVES` edge produced from a C02 derived product it holds the
    ``sources`` / ``source_versions`` / ``parameters`` / ``raw_io`` /
    ``reference_frame`` / ``identity`` payload fields unchanged.
    """

    edge_type: EdgeType
    subject: str
    object_ref: str
    role: str = ""
    revision: int = 0
    mappings: tuple[IdentityMapping, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "edge_type": self.edge_type.value,
            "subject": self.subject,
            "object_ref": self.object_ref,
            "role": self.role,
            "revision": self.revision,
            "mappings": [m.to_dict() for m in self.mappings],
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> LineageEdge:
        """Rebuild from :meth:`to_dict` output."""
        try:
            edge_type = EdgeType(str(payload.get("edge_type")))
        except ValueError:
            logger.warning("unknown edge type %r; defaulting to uses", payload.get("edge_type"))
            edge_type = EdgeType.USES
        raw_mappings = payload.get("mappings")
        mappings = (
            tuple(IdentityMapping.from_dict(m) for m in raw_mappings if isinstance(m, Mapping))
            if isinstance(raw_mappings, Sequence) and not isinstance(raw_mappings, (str, bytes))
            else ()
        )
        raw_evidence = payload.get("evidence")
        evidence = dict(raw_evidence) if isinstance(raw_evidence, Mapping) else {}
        return cls(
            edge_type=edge_type,
            subject=str(payload.get("subject") or ""),
            object_ref=str(payload.get("object_ref") or ""),
            role=str(payload.get("role") or ""),
            revision=_int_or_none(payload.get("revision")) or 0,
            mappings=mappings,
            evidence=evidence,
        )


@dataclass(frozen=True)
class LineageNode:
    """A lightweight identity a projection may label (task / product / revision).

    ``kind`` is a node *category* (task/product/candidate/revision/input), not a
    task lifecycle state — this module owns no state machine.
    """

    node_id: str
    kind: str = "task"
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {"node_id": self.node_id, "kind": self.kind, "label": self.label}


# ---------------------------------------------------------------------------
# Versioned revisions (human selection / revision; never overwrite the original)
# ---------------------------------------------------------------------------


def original_digest(result: Mapping[str, Any]) -> str:
    """Return a stable sha256 of a scientific result mapping (order-independent)."""
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class OriginalResultOverwriteError(RuntimeError):
    """Raised when a human revision path would overwrite the original result.

    The original scientific result is write-once by contract; a second
    :meth:`RevisionLedger.register_original` for the same target (or a revision
    that tries to replace it) is rejected rather than silently applied.
    """


@dataclass(frozen=True)
class RevisionEntry:
    """One versioned human revision, appended to (never merged into) the original.

    ``payload`` is the human-provided change (selection / edit).  ``revision`` is
    a monotonically increasing per-target counter starting at ``1``;
    ``supersedes`` is the previous revision number (``0`` for the first).
    ``original_digest`` pins which scientific result this revision was made
    against so a stale revision is detectable without touching the result.
    """

    revision_id: str
    target: str
    revision: int
    revision_kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    author: str = ""
    reason: str = ""
    created_at: str = ""
    supersedes: int = 0
    original_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "revision_id": self.revision_id,
            "target": self.target,
            "revision": self.revision,
            "revision_kind": self.revision_kind,
            "payload": dict(self.payload),
            "author": self.author,
            "reason": self.reason,
            "created_at": self.created_at,
            "supersedes": self.supersedes,
            "original_digest": self.original_digest,
        }


class RevisionLedger:
    """Append-only, versioned revision ledger over write-once originals.

    The ledger is deliberately in-memory and side-effect free: recording a human
    revision never writes back to the authoritative result, never bumps a job
    revision and never mutates a previously registered original.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._originals: dict[str, dict[str, Any]] = {}
        self._original_digests: dict[str, str] = {}
        self._revisions: dict[str, list[RevisionEntry]] = {}
        self._order: list[RevisionEntry] = []

    # -- originals (write-once) -----------------------------------------

    def register_original(self, target: str, result: Mapping[str, Any]) -> str:
        """Register the original scientific result for *target*; returns its digest.

        Raises:
            OriginalResultOverwriteError: a result is already registered for
                *target* (the original is never overwritten).
        """
        if not target:
            raise ValueError("target must be non-empty")
        if not isinstance(result, Mapping):
            raise TypeError("result must be a mapping")
        with self._lock:
            if target in self._originals:
                raise OriginalResultOverwriteError(
                    f"original result already registered for target {target!r}"
                )
            snapshot = json.loads(json.dumps(dict(result), default=str))
            self._originals[target] = snapshot
            digest = original_digest(snapshot)
            self._original_digests[target] = digest
            return digest

    def original(self, target: str) -> Mapping[str, Any] | None:
        """Return an unchanged deep copy of the original result (``None`` if none)."""
        with self._lock:
            stored = self._originals.get(target)
            if stored is None:
                return None
            return json.loads(json.dumps(stored, default=str))

    def original_digest(self, target: str) -> str:
        """Return the pinned digest of the original result (``""`` if none)."""
        with self._lock:
            return self._original_digests.get(target, "")

    # -- revisions (append-only) ----------------------------------------

    def record_revision(
        self,
        target: str,
        *,
        revision_kind: str,
        payload: Mapping[str, Any] | None = None,
        author: str = "",
        reason: str = "",
        created_at: str = "",
    ) -> RevisionEntry:
        """Append a versioned human revision for *target* and return it.

        The original result (if registered) is never modified; the new entry only
        records the human change plus the original digest it was made against.

        Raises:
            OriginalResultOverwriteError: *payload* contains a truthy
                ``overwrite_original`` key — a human revision may never claim to
                replace the authoritative scientific result.
        """
        if not target:
            raise ValueError("target must be non-empty")
        if not revision_kind:
            raise ValueError("revision_kind must be non-empty")
        change = dict(payload) if isinstance(payload, Mapping) else {}
        if change.get("overwrite_original"):
            raise OriginalResultOverwriteError(
                f"human revision for {target!r} may not overwrite the original result"
            )
        with self._lock:
            history = self._revisions.setdefault(target, [])
            revision = len(history) + 1
            supersedes = history[-1].revision if history else 0
            entry = RevisionEntry(
                revision_id=f"rev:{target}:{revision}",
                target=target,
                revision=revision,
                revision_kind=str(revision_kind),
                payload=change,
                author=str(author),
                reason=str(reason),
                created_at=created_at or _utc_now_iso(),
                supersedes=supersedes,
                original_digest=self._original_digests.get(target, ""),
            )
            history.append(entry)
            self._order.append(entry)
            return entry

    def versions(self, target: str) -> tuple[RevisionEntry, ...]:
        """Return the revision history of *target*, oldest first."""
        with self._lock:
            return tuple(self._revisions.get(target, ()))

    def latest(self, target: str) -> RevisionEntry | None:
        """Return the newest revision of *target* (``None`` when never revised)."""
        with self._lock:
            history = self._revisions.get(target, ())
            return history[-1] if history else None

    def revisions(self) -> tuple[RevisionEntry, ...]:
        """Return every revision across targets, in record order."""
        with self._lock:
            return tuple(self._order)


# ---------------------------------------------------------------------------
# C02 scientific-field map (derived product → lineage edge)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldMapEntry:
    """One documented C02 ``source_field`` → platform ``target_field`` mapping."""

    source_field: str
    target_field: str
    rule: str
    verbatim: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "source_field": self.source_field,
            "target_field": self.target_field,
            "rule": self.rule,
            "verbatim": self.verbatim,
        }


#: The platform-side field map for C02 derived products (``DerivedPayload``, T12).
#: Every scientific field is preserved verbatim: no conversion, no renumbering and
#: no invented meaning.  Diff against the C02 side: HN-02 §2.4 maps ``derived`` to
#: B-T46 provenance edges + candidate metadata; the concrete field names below come
#: from the T12 contract (``src/cccp/calculation/products/contracts.py``).
C02_FIELD_MAP: tuple[FieldMapEntry, ...] = (
    FieldMapEntry(
        "ScienceProduct.identity",
        "LineageEdge(derives).evidence.identity",
        "carry the product identity verbatim; used to label the derived product",
    ),
    FieldMapEntry(
        "DerivedPayload.sources[]",
        "LineageEdge(derives).evidence.sources + IdentityMapping"
        "{source_kind,artifact,native_index}",
        "one mapping per source; native_index is preserved un-renumbered; source_kind is "
        "kept verbatim and is NOT classified into an atom/frame/mode meaning",
    ),
    FieldMapEntry(
        "DerivedPayload.source_versions[]",
        "LineageEdge(derives).evidence.source_versions",
        "verbatim, one-to-one with sources (reproducibility)",
    ),
    FieldMapEntry(
        "DerivedPayload.parameters[]",
        "LineageEdge(derives).evidence.parameters",
        "verbatim typed parameters (name/value/unit/source)",
    ),
    FieldMapEntry(
        "DerivedPayload.raw_io[]",
        "LineageEdge(derives).evidence.raw_io",
        "verbatim raw input/output data (name/role/value/source)",
    ),
    FieldMapEntry(
        "DerivedPayload.reference_frame",
        "LineageEdge(derives).evidence.reference_frame",
        "verbatim axes/origin/energy_zero",
    ),
    FieldMapEntry(
        "ProductKind.DERIVED",
        "LineageEdge.edge_type = derives",
        "the only scientific product kind that becomes a derives edge",
    ),
)

#: Provenance and verification status of the C02 field map.  Written explicitly so
#: a consumer can never mistake "provider delivered" for "cross-lane verified".
C02_FIELD_MAP_STATUS: Mapping[str, Any] = {
    "provider": "lane-C (C02)",
    "channel": "tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md",
    "contract": "src/cccp/calculation/products/contracts.py::DerivedPayload (T12)",
    "diff_record": "tests/baseline/services-evidence/lineage_field_map.md",
    "provider_status": "delivered",
    "cross_lane_e2e": "NOT_VERIFIED",
    "note": (
        "HN-02 state A (contract consumable) is delivered; state B (cross-lane "
        "end-to-end acceptance) is pending B/A integration and is NOT claimed here."
    ),
}


def _mapping_from_source(source: Mapping[str, Any]) -> IdentityMapping:
    """Build an :class:`IdentityMapping` from one verbatim C02 ``SourceRef`` dict."""
    return IdentityMapping(
        native_index=_int_or_none(source.get("native_index")),
        kind=None,  # never invent an atom/frame/mode meaning for an opaque source_kind
        artifact=str(source.get("artifact") or ""),
        source_kind=str(source.get("source_kind") or ""),
    )


def _derived_edge_evidence(product: Mapping[str, Any]) -> dict[str, Any]:
    """Carry every C02 derived field verbatim under one evidence mapping."""
    payload = product.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else {}
    evidence: dict[str, Any] = {
        "product_kind": product.get("product_kind") or "derived",
        "state": product.get("state", ""),
        "complete": product.get("complete", False),
    }
    identity = product.get("identity")
    if isinstance(identity, Mapping):
        evidence["identity"] = dict(identity)
    for key in ("sources", "source_versions", "parameters", "raw_io", "reference_frame"):
        if key in payload_map:
            evidence[key] = payload_map[key]
    errors = product.get("errors")
    if errors:
        evidence["errors"] = list(errors)
    missing_reason = product.get("missing_reason")
    if missing_reason:
        evidence["missing_reason"] = missing_reason
    return evidence


def derived_mappings(product: Mapping[str, Any]) -> tuple[IdentityMapping, ...]:
    """Preserve the C02 derived product's source mappings (native indices kept)."""
    payload = product.get("payload")
    payload_map = payload if isinstance(payload, Mapping) else {}
    raw_sources = payload_map.get("sources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
        return ()
    return tuple(_mapping_from_source(s) for s in raw_sources if isinstance(s, Mapping))


def derived_object_ref(product: Mapping[str, Any]) -> str:
    """Best-effort stable reference for a derived product (identity digest)."""
    identity = product.get("identity")
    if isinstance(identity, Mapping):
        digest = identity.get("input_digest")
        if isinstance(digest, str) and digest:
            return digest
    return ""


# ---------------------------------------------------------------------------
# Frozen projection + builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceProjection:
    """Immutable, read-only research evidence projection for one task.

    Query methods only read the prepared edges; nothing here writes state,
    advances a job or mutates a scientific result.
    """

    task_id: str
    version: int = EVIDENCE_PROJECTION_VERSION
    nodes: Mapping[str, LineageNode] = field(default_factory=dict)
    edges: tuple[LineageEdge, ...] = ()
    revisions: tuple[RevisionEntry, ...] = ()
    field_map: tuple[FieldMapEntry, ...] = C02_FIELD_MAP
    field_map_status: Mapping[str, Any] = field(default_factory=lambda: dict(C02_FIELD_MAP_STATUS))

    # -- read-only queries ----------------------------------------------

    def node(self, node_id: str) -> LineageNode | None:
        """Return the labelled node, if any."""
        return self.nodes.get(node_id)

    def edges_from(self, subject: str) -> tuple[LineageEdge, ...]:
        """Return every edge whose subject is *subject*."""
        return tuple(edge for edge in self.edges if edge.subject == subject)

    def edges_to(self, object_ref: str) -> tuple[LineageEdge, ...]:
        """Return every edge whose object is *object_ref*."""
        return tuple(edge for edge in self.edges if edge.object_ref == object_ref)

    def edges_of_type(self, edge_type: EdgeType) -> tuple[LineageEdge, ...]:
        """Return every edge of one of the five frozen types."""
        return tuple(edge for edge in self.edges if edge.edge_type is edge_type)

    def revisions_of(self, target: str) -> tuple[RevisionEntry, ...]:
        """Return the versioned revisions recorded for *target*, oldest first."""
        return tuple(rev for rev in self.revisions if rev.target == target)

    def chain(self, start: str, *, max_depth: int = 10) -> tuple[LineageEdge, ...]:
        """Breadth-first edge chain reachable from *start* (cycle-guarded, bounded).

        Purely a read of the prepared edges: this is not a scheduler walk and it
        never advances any state.
        """
        if max_depth <= 0:
            return ()
        ordered: list[LineageEdge] = []
        seen_nodes: set[str] = {start}
        queue: list[tuple[str, int]] = [(start, 0)]
        index: dict[str, list[LineageEdge]] = {}
        for edge in self.edges:
            index.setdefault(edge.subject, []).append(edge)
        while queue:
            node_id, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for edge in index.get(node_id, ()):
                ordered.append(edge)
                if edge.object_ref and edge.object_ref not in seen_nodes:
                    seen_nodes.add(edge.object_ref)
                    queue.append((edge.object_ref, depth + 1))
        return tuple(ordered)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole projection to a JSON-safe dict."""
        return {
            "task_id": self.task_id,
            "version": int(self.version),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "revisions": [rev.to_dict() for rev in self.revisions],
            "field_map": [entry.to_dict() for entry in self.field_map],
            "field_map_status": dict(self.field_map_status),
        }


class TaskEvidenceBuilder:
    """Accumulates read-only provenance edges, then freezes one projection.

    The builder is a plain accumulator used *out of band* by the HTTP adapter;
    it performs no I/O and owns no state machine.  Calling :meth:`build` does not
    consume the builder, so a caller can inspect intermediate counts, but the
    returned :class:`EvidenceProjection` is an immutable snapshot.
    """

    def __init__(self, task_id: str, *, ledger: RevisionLedger | None = None) -> None:
        if not task_id:
            raise ValueError("task_id must be non-empty")
        self._task_id = task_id
        self._ledger = ledger if ledger is not None else RevisionLedger()
        self._nodes: dict[str, LineageNode] = {}
        self._edges: list[LineageEdge] = []

    @property
    def ledger(self) -> RevisionLedger:
        """The revision ledger backing this builder."""
        return self._ledger

    # -- nodes -----------------------------------------------------------

    def add_node(self, node_id: str, *, kind: str = "task", label: str = "") -> LineageNode:
        """Label a node; re-labelling replaces the previous label (still read-only)."""
        if not node_id:
            raise ValueError("node_id must be non-empty")
        node = LineageNode(node_id=node_id, kind=str(kind), label=str(label))
        self._nodes[node_id] = node
        return node

    # -- edges -----------------------------------------------------------

    def _add(self, edge: LineageEdge) -> LineageEdge:
        self._edges.append(edge)
        return edge

    def add_use(
        self,
        subject: str,
        object_ref: str,
        *,
        role: str = "input",
        relation: str = "",
        evidence: Mapping[str, Any] | None = None,
    ) -> LineageEdge:
        """Record ``subject`` 使用 ``object_ref`` (an input / upstream artifact)."""
        merged = dict(evidence or {})
        if relation:
            merged.setdefault("relation", relation)
        return self._add(
            LineageEdge(
                edge_type=EdgeType.USES,
                subject=subject,
                object_ref=object_ref,
                role=role,
                evidence=merged,
            )
        )

    def add_produce(
        self,
        subject: str,
        object_ref: str,
        *,
        role: str = "product",
        mappings: Iterable[IdentityMapping] = (),
        evidence: Mapping[str, Any] | None = None,
    ) -> LineageEdge:
        """Record ``subject`` 产生 ``object_ref`` (a result product)."""
        return self._add(
            LineageEdge(
                edge_type=EdgeType.PRODUCES,
                subject=subject,
                object_ref=object_ref,
                role=role,
                mappings=tuple(mappings),
                evidence=dict(evidence or {}),
            )
        )

    def add_derive(
        self,
        subject: str,
        object_ref: str,
        *,
        role: str = "derived",
        mappings: Iterable[IdentityMapping] = (),
        evidence: Mapping[str, Any] | None = None,
    ) -> LineageEdge:
        """Record ``subject`` 派生 ``object_ref`` from other products."""
        return self._add(
            LineageEdge(
                edge_type=EdgeType.DERIVES,
                subject=subject,
                object_ref=object_ref,
                role=role,
                mappings=tuple(mappings),
                evidence=dict(evidence or {}),
            )
        )

    def add_c02_derived(self, subject: str, product: Mapping[str, Any]) -> LineageEdge:
        """Record a 派生 edge from a serialised C02 derived product (verbatim)."""
        return self.add_derive(
            subject,
            derived_object_ref(product),
            role=str(product.get("product_kind") or "derived"),
            mappings=derived_mappings(product),
            evidence=_derived_edge_evidence(product),
        )

    def add_revision(
        self,
        target: str,
        *,
        revision_kind: str,
        payload: Mapping[str, Any] | None = None,
        author: str = "",
        reason: str = "",
        created_at: str = "",
    ) -> RevisionEntry:
        """Record a versioned human selection/revision and its REVISION edge.

        The original scientific result (registered on the ledger) is never
        modified; the entry only records the human change.
        """
        entry = self._ledger.record_revision(
            target,
            revision_kind=revision_kind,
            payload=payload,
            author=author,
            reason=reason,
            created_at=created_at,
        )
        self.add_node(entry.revision_id, kind="revision", label=str(revision_kind))
        self._add(
            LineageEdge(
                edge_type=EdgeType.REVISION,
                subject=target,
                object_ref=entry.revision_id,
                role=str(revision_kind),
                revision=entry.revision,
                evidence={
                    "supersedes": entry.supersedes,
                    "original_digest": entry.original_digest,
                },
            )
        )
        return entry

    def add_reuse(
        self,
        subject: str,
        object_ref: str,
        *,
        role: str = "reused",
        attempt: int | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> LineageEdge:
        """Record ``subject`` 复用 a previously produced result (resume/rerun)."""
        merged = dict(evidence or {})
        if attempt is not None:
            merged.setdefault("attempt", attempt)
        return self._add(
            LineageEdge(
                edge_type=EdgeType.REUSES,
                subject=subject,
                object_ref=object_ref,
                role=role,
                evidence=merged,
            )
        )

    # -- freeze ----------------------------------------------------------

    def build(self) -> EvidenceProjection:
        """Freeze the accumulated nodes/edges/revisions into a read-only projection."""
        return EvidenceProjection(
            task_id=self._task_id,
            version=EVIDENCE_PROJECTION_VERSION,
            nodes=dict(self._nodes),
            edges=tuple(self._edges),
            revisions=self._ledger.revisions(),
        )
