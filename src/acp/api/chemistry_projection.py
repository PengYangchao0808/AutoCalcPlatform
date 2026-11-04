"""Pure, read-only projection for the project chemistry network (B1 identity).

Contract: ``docs/reference/next-change/ACP_Chemistry_Network_Contract_20261103.md``
(§2 nodes = structure versions keyed by ``content_checksum``; §3 edges =
persisted ``lineage`` only; §4 node identity = ``molecule_identity_key`` when
persisted, metadata from ``structure_identities``; §7 name/SMILES/InChIKey
merging is forbidden).

This module projects **in-memory rows only**: it never opens a database, never
reads a file, never computes a checksum or an identity key and never mutates its
inputs.  The route (``acp.api.v2_routes``) owns every SELECT; these helpers only
reshape the rows it already fetched.  Keeping the module import-free of DB/server
modules is the ``src/acp/api/AGENTS.md`` pure-projection rule.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "EDGE_RELATIONS",
    "IDENTITY_KIND_MIXED",
    "MOLECULE_IDENTITY_KIND",
    "NODE_IDENTITY_KIND",
    "SKIP_IDENTITY_METADATA_MISSING",
    "SKIP_MISSING_STRUCTURE_HASH",
    "SKIP_ORPHAN_ENDPOINT",
    "build_network",
]

#: Identity layer of a structure *version* node (B0 behaviour, still the fallback).
NODE_IDENTITY_KIND = "structure_content_v1"
#: Identity layer of a node carrying a persisted molecule identity.
MOLECULE_IDENTITY_KIND = "molecule_v1"
#: Whole-graph layer when only some nodes are identified (§4: never silent).
IDENTITY_KIND_MIXED = "mixed"

#: Closed enum (§3).  A relation outside this tuple is never emitted.
EDGE_RELATIONS: tuple[str, ...] = ("lineage",)

#: ``skipped`` reasons (§2 / §4).
SKIP_MISSING_STRUCTURE_HASH = "missing_structure_hash"
SKIP_IDENTITY_METADATA_MISSING = "identity_metadata_missing"
SKIP_ORPHAN_ENDPOINT = "orphan_endpoint"

#: Upper bound on edges relative to the node budget (§5).
_EDGE_BUDGET_FACTOR = 4


def _text(value: Any) -> str:
    """Return a stripped string for *value* (``""`` for ``None``)."""
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: Any) -> int | None:
    """Return *value* as ``int`` when it carries a usable number, else ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _text(value)
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _display_names(tasks: list[dict]) -> dict[str, str]:
    """Map ``task_id`` → display molecule name (display only, never identity)."""
    names: dict[str, str] = {}
    for row in tasks:
        task_id = _text(row.get("task_id"))
        name = _text(row.get("molecule_name"))
        if task_id and name:
            names[task_id] = name
    return names


def _identity_registry(identities: list[dict] | None) -> dict[str, dict]:
    """Index registry rows (``structure_identities``) by ``identity_key``."""
    registry: dict[str, dict] = {}
    for row in identities or []:
        key = _text(row.get("identity_key"))
        if key and key not in registry:
            registry[key] = row
    return registry


def _new_group(identified: bool, identity_key: str) -> dict[str, Any]:
    """Create the accumulator for one node (identified or checksum-keyed)."""
    return {
        "identified": identified,
        "identity_key": identity_key,
        "task_ids": set(),
        "molecule_names": [],
        "formula": "",
        "charge": None,
        "atom_count": None,
    }


def _group_nodes(
    sources: list[dict],
    display_names: dict[str, str],
    skipped: list[dict[str, str]],
) -> dict[tuple[str, bool], dict[str, Any]]:
    """Group sources by persisted identity, else by ``content_checksum`` (§4).

    A source row is *identified* only when ``molecule_identity_key`` is a
    non-empty string; it is then grouped by that key alone.  An unidentified row
    falls back to its ``content_checksum``.  The two spaces never merge: the group
    key carries the ``identified`` flag, so an identified row can never land in a
    checksum group (and vice versa) even if the two strings were equal.  A row
    with neither is skipped — never identified by name, path or timestamp (§7).
    """
    groups: dict[tuple[str, bool], dict[str, Any]] = {}
    for source in sources:
        task_id = _text(source.get("job_id"))
        identity_key = _text(source.get("molecule_identity_key"))
        checksum = _text(source.get("content_checksum"))
        if identity_key:
            group_id, identified = identity_key, True
        elif checksum:
            group_id, identified = checksum, False
        else:
            # §2/§7.3: never fall back to a file name or display name.
            skipped.append({"reason": SKIP_MISSING_STRUCTURE_HASH, "task_id": task_id})
            continue
        group_key = (group_id, identified)
        group = groups.get(group_key)
        if group is None:
            group = _new_group(identified, identity_key)
            groups[group_key] = group
        if task_id:
            group["task_ids"].add(task_id)
        name = display_names.get(task_id) or _text(source.get("molecule_name"))
        if name and name not in group["molecule_names"]:
            group["molecule_names"].append(name)
        # Descriptive facts: first non-empty value in input order wins.  Never
        # invented, never averaged across the version's files.
        if not group["formula"]:
            group["formula"] = _text(source.get("formula"))
        if group["charge"] is None:
            group["charge"] = _as_int(source.get("charge"))
        if group["atom_count"] is None:
            group["atom_count"] = _as_int(source.get("atom_count"))
    return groups


def _node_metadata(
    group: dict[str, Any], registry: dict[str, dict]
) -> tuple[str, str, str, int | None, int | None]:
    """Return ``(scheme, canonical_smiles, inchikey, charge, multiplicity)``.

    Unidentified nodes carry no molecule metadata at all.  An identified node
    takes its metadata from the ``structure_identities`` row (§4.3 — never
    recomputed here); a missing row leaves the metadata empty rather than
    guessing it from the name, the checksum or another molecule's row.
    """
    if not group["identified"]:
        return "", "", "", None, None
    row = registry.get(_text(group.get("identity_key")))
    if row is None:
        return "", "", "", None, None
    return (
        _text(row.get("scheme")),
        _text(row.get("canonical_smiles")),
        _text(row.get("inchikey")),
        _as_int(row.get("charge")),
        _as_int(row.get("multiplicity")),
    )


def _edge_depth(raw: dict) -> int:
    depth = _as_int(raw.get("depth"))
    return 1 if depth is None else depth


def _build_edges(
    edges_raw: list[dict],
    known_tasks: set[str],
    max_edges: int,
    skipped: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], bool]:
    """Dedupe/validate the supplied lineage edges against the emitted nodes."""
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    truncated = False
    for raw in edges_raw:
        relation = _text(raw.get("relation")) or EDGE_RELATIONS[0]
        from_task = _text(raw.get("from_task"))
        to_task = _text(raw.get("to_task"))
        # §3: closed enum, direct edges only, no self-edges.
        if relation not in EDGE_RELATIONS:
            continue
        if not from_task or not to_task or from_task == to_task:
            continue
        if _edge_depth(raw) > 1:
            continue
        key = (from_task, to_task)
        if key in seen:
            continue
        if from_task not in known_tasks or to_task not in known_tasks:
            # An edge to a task with no emitted node would fabricate structure.
            skipped.append({"reason": SKIP_ORPHAN_ENDPOINT, "task_id": to_task})
            continue
        if len(edges) >= max_edges:
            truncated = True
            break
        seen.add(key)
        edges.append({"relation": relation, "from_task": from_task, "to_task": to_task, "depth": 1})
    return edges, truncated


def _graph_identity_kind(identified_nodes: int, unidentified_nodes: int) -> str:
    """Whole-graph identity layer from the two node counts (§4 / §5)."""
    if identified_nodes and not unidentified_nodes:
        return MOLECULE_IDENTITY_KIND
    if not identified_nodes:
        return NODE_IDENTITY_KIND
    return IDENTITY_KIND_MIXED


def build_network(
    project_id: str,
    *,
    tasks: list[dict],
    sources: list[dict],
    edges_raw: list[dict],
    limit: int,
    truncated: bool = False,
    next_cursor: str | None = None,
    identities: list[dict] | None = None,
) -> dict:
    """Project the chemistry network from already-fetched rows.

    ``tasks`` are ``acp.scheduler.tasks.TaskIndex.list_by_project`` rows,
    ``sources`` are ``StructureSourceStore.query_sources`` items (already
    paginated by the caller; ``molecule_identity_key`` is ``i.*`` so it is part
    of each row), ``edges_raw`` are the resolved direct lineage edges
    (``{"from_task", "to_task", "depth": 1}``) and ``identities`` are
    ``structure_identities`` registry rows for the project (``None``/empty
    degrades to the B0 ``structure_content_v1`` shape).  The returned mapping has
    exactly the ``V2ChemistryNetworkResponse`` field set.
    """
    node_limit = max(int(limit), 1)
    skipped: list[dict[str, str]] = []
    registry = _identity_registry(identities)
    groups = _group_nodes(sources, _display_names(tasks), skipped)

    # §4: identified nodes without a registry row keep their node; the missing
    # metadata is accounted for instead of being invented.
    for node_key in sorted(groups, key=lambda key: (key[0], key[1])):
        group = groups[node_key]
        if not group["identified"]:
            continue
        if _text(group.get("identity_key")) in registry:
            continue
        for task_id in sorted(group["task_ids"]):
            skipped.append({"reason": SKIP_IDENTITY_METADATA_MISSING, "task_id": task_id})

    # Deterministic order by identity, never by name.
    node_keys = sorted(groups, key=lambda key: (key[0], key[1]))
    truncated_flag = bool(truncated)
    if len(node_keys) > node_limit:
        node_keys = node_keys[:node_limit]
        truncated_flag = True

    nodes: list[dict[str, Any]] = []
    known_tasks: set[str] = set()
    identified_nodes = 0
    unidentified_nodes = 0
    for node_key in node_keys:
        node_id, identified = node_key
        group = groups[node_key]
        scheme, canonical_smiles, inchikey, charge, multiplicity = _node_metadata(group, registry)
        task_ids = sorted(group["task_ids"])
        known_tasks.update(task_ids)
        if identified:
            identified_nodes += 1
        else:
            unidentified_nodes += 1
        nodes.append(
            {
                "node_id": node_id,
                "identity_kind": MOLECULE_IDENTITY_KIND if identified else NODE_IDENTITY_KIND,
                "scheme": scheme,
                "task_ids": task_ids,
                "molecule_names": list(group["molecule_names"]),
                "canonical_smiles": canonical_smiles,
                "inchikey": inchikey,
                "formula": group["formula"],
                "charge": charge if identified else group["charge"],
                "multiplicity": multiplicity,
                "atom_count": group["atom_count"],
            }
        )

    edges, edges_truncated = _build_edges(
        edges_raw, known_tasks, node_limit * _EDGE_BUDGET_FACTOR, skipped
    )

    return {
        "project_id": str(project_id),
        "nodes": nodes,
        "edges": edges,
        "skipped": skipped,
        "truncated": truncated_flag or edges_truncated,
        "next_cursor": next_cursor,
        "identity_kind": _graph_identity_kind(identified_nodes, unidentified_nodes),
        "identified_nodes": identified_nodes,
        "unidentified_nodes": unidentified_nodes,
    }
