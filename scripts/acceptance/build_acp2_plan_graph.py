#!/usr/bin/env python3.11
"""Build the ACP 2.0 remediation plan graph from ``EXECUTION_PLAN.md``.

The execution plan (``reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md``)
is the single authority for the 39 remediation tasks (``### Mx-yy title`` headings),
their ``**规模**`` sizes, their ``**前置**`` prerequisites (including ``A～B``
ranges and ``Mx 全部`` aggregates) and their ``a/b/c/d`` sub-checkpoints.  This
module parses that text instead of restating it, then derives the two graphs the
plan requires (M0-01, section 3):

``start``
    Task-start dependencies.  It answers "what may be worked on in parallel
    now": close-only augmentations and the conditional ``M0-13`` external-change
    task are excluded, because section 3 freezes the start path at
    ``M0-01→04→05→06→11→12→M1-01→M1-06→M2-01→M2-03→M3-03→M3-04→M4-01→M5-01→M5-02→M5-03``
    (16 tasks / 3 XL).

``close``
    Whole-item sign-off dependencies.  It adds the close-only edges that only
    matter once the deliverable is signed off: ``M5-01``/``M5-02`` "最终关闭需
    M4 全部" and the ``M4-06b`` cross-domain row (``前置 M4-06a、M4-03、M4-05``).
    Section 3 freezes it at 19 tasks / 6 XL.

``close_external_change``
    The ``external_change_required`` branch of ``close``: ``M0-13`` becomes real
    and gates ``M1-01``.  Section 3 freezes it at 20 tasks / 6 XL.

Everything else (unknown references, dependency cycles, orphan sub-checkpoints)
fails closed, because a silently dropped edge would make the critical path and
the capacity model unreviewable.

Usage::

    python3.11 scripts/acceptance/build_acp2_plan_graph.py            # write JSON
    python3.11 scripts/acceptance/build_acp2_plan_graph.py --check    # verify JSON
    python3.11 scripts/acceptance/build_acp2_plan_graph.py --stdout   # dump JSON

Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_RELATIVE_PATH = "reports/acp2_delivery_gap_audit_20261026/EXECUTION_PLAN.md"
GRAPH_RELATIVE_PATH = "reports/acp2_delivery_gap_audit_20261026/plan-graph.json"
DEFAULT_PLAN = REPO_ROOT / PLAN_RELATIVE_PATH
DEFAULT_OUT = REPO_ROOT / GRAPH_RELATIVE_PATH
GENERATOR_RELATIVE_PATH = "scripts/acceptance/build_acp2_plan_graph.py"

GRAPH_SCHEMA = "acp2.execution-plan-graph.v3"
TASK_COUNT = 39
CLOSE_MODES = ("close", "close_external_change")

TASK_HEADING = re.compile(r"^### (M\d+-\d\d) (.+)$")
SECTION_END = re.compile(r"^#{2,3} ")
SIZE_PATTERN = re.compile(r"\*\*规模\*\*：(XL|L|M|S)")
TASK_ID = re.compile(r"^M\d+-\d\d$")
RANGE_PATTERN = re.compile(r"^(M\d+-\d\d)～(M\d+-\d\d)$")
AGGREGATE_PATTERN = re.compile(r"^(?:最终关闭需 )?(M[0-5]) 全部$")
CHECKPOINT_ID = re.compile(r"(?<![A-Za-z0-9-])(M\d+-\d\d[a-z])(?![A-Za-z0-9-])")
PARENT_TASK_OF_CHECKPOINT = re.compile(r"^(M\d+-\d\d)[a-z]$")
PREREQ_MARKER = "**前置**："
SENTENCE_END = "。"
CLAUSE_SEPARATOR = "；"
ITEM_SEPARATOR = "、"
NOT_APPLICABLE = "无"

#: ``M0-09`` may end in ``external_change_required``; only then does the
#: conditional task become a hard prerequisite of the task named in the plan.
CONDITIONAL_EDGE_PATTERN = re.compile(r"external_change_required，还必须先完成 (M\d+-\d\d)")
CONDITIONAL_HEADING_MARKER = "条件任务"

#: ``**独立检查点**：M0-02a …`` / ``**子检查点**：M3-01a …`` declare every
#: identifier up to the next sentence end.  A bare "检查点" mention (for example
#: M0-01's "从任务/子检查点与前置字段") deliberately does not match.
CHECKPOINT_DECLARATION_MARKER = "检查点**："

#: Close-only prerequisites that live in prose rather than in the ``**前置**``
#: field.  Each rule records the exact plan sentence it was read from; the
#: generator fails when that sentence is gone, so a rewritten plan cannot keep
#: a stale edge.
CLOSE_ONLY_TEXT_RULES: tuple[dict[str, Any], ...] = (
    {
        "dependent": "M4-06",
        "anchor": (
            "M4-06b — 跨领域/多租户 required 行**：由 A+B+C+INT 执行，前置 M4-06a、M4-03、M4-05。"
        ),
        "dependencies": ("M4-03", "M4-05"),
        "reason": "M4-06b（跨领域/多租户 required 行）在前置行之外还要求 M4-03 与 M4-05",
    },
)

GAP_ROW = re.compile(r"^\| (G\d\d) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$")
SCOPE_ROW = re.compile(r"^\| (IN_SCOPE|DEFERRED|OUT_OF_SCOPE) \| ([^|]+) \|$")
SCOPE_CLAUSE_SKIP_PREFIXES = ("不计作已实现",)
H_RANGE = re.compile(r"^(H\d)[～–](H\d)$")


class PlanParseError(Exception):
    """Raised when the plan text no longer matches the parsed model."""


@dataclass(frozen=True)
class TaskSpec:
    """One ``### Mx-yy`` task of the execution plan."""

    id: str
    title: str
    owner: str
    size: str
    start_dependencies: tuple[str, ...]
    close_only_dependencies: tuple[str, ...]
    conditional: bool
    checkpoints: tuple[str, ...]
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GapRow:
    """One ``G01–G26`` row of plan section 10."""

    id: str
    h_rows: tuple[str, ...]
    tasks: tuple[str, ...]
    conditional_tasks: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class PlanModel:
    """Everything the graph and coverage validators read from the plan."""

    plan_sha256: str
    tasks: tuple[TaskSpec, ...]
    checkpoints: dict[str, tuple[str, ...]]
    checkpoint_owners: dict[str, str]
    unknown_refs: tuple[str, ...]
    gaps: tuple[GapRow, ...]
    scope_items: dict[str, tuple[str, ...]]
    conditional_edges: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.id for task in self.tasks)

    @property
    def checkpoint_ids(self) -> tuple[str, ...]:
        return tuple(checkpoint for task in self.tasks for checkpoint in task.checkpoints)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sections(text: str) -> list[tuple[str, str, str]]:
    """Split the plan into ``(task_id, title, body)`` for every ``### Mx-yy``."""

    lines = text.splitlines()
    headings: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = TASK_HEADING.match(line)
        if match:
            headings.append((index, match.group(1), match.group(2).strip()))
    sections: list[tuple[str, str, str]] = []
    for position, (line_no, task_id, title) in enumerate(headings):
        stop = len(lines)
        for candidate in range(line_no + 1, len(lines)):
            if SECTION_END.match(lines[candidate]):
                stop = candidate
                break
        if position + 1 < len(headings):
            stop = min(stop, headings[position + 1][0])
        sections.append((task_id, title, "\n".join(lines[line_no + 1 : stop])))
    return sections


def _expand_range(start: str, end: str) -> tuple[str, ...]:
    start_group, start_number = start.split("-")
    end_group, end_number = end.split("-")
    if start_group != end_group:
        raise PlanParseError(f"range crosses milestones: {start}～{end}")
    low, high = int(start_number), int(end_number)
    if low > high:
        raise PlanParseError(f"reversed range: {start}～{end}")
    return tuple(f"{start_group}-{number:02d}" for number in range(low, high + 1))


def _prereq_span(body: str, task_id: str) -> str:
    for line in body.splitlines():
        if PREREQ_MARKER in line:
            span = line.split(PREREQ_MARKER, 1)[1]
            return span.split(SENTENCE_END, 1)[0].strip()
    raise PlanParseError(f"{task_id} has no '{PREREQ_MARKER}' field")


def _expand_token(token: str, task_ids: set[str], task_id: str) -> tuple[tuple[str, ...], bool]:
    """Return ``(dependencies, close_only)`` for one prerequisite token."""

    token = token.strip()
    if not token:
        raise PlanParseError(f"{task_id} has an empty prerequisite token")
    if token == NOT_APPLICABLE:
        return (), False
    aggregate = AGGREGATE_PATTERN.match(token)
    if aggregate:
        prefix = aggregate.group(1)
        members = tuple(
            sorted(candidate for candidate in task_ids if candidate.startswith(f"{prefix}-"))
        )
        if not members:
            raise PlanParseError(f"{task_id} references unknown aggregate '{token}'")
        return members, True
    range_match = RANGE_PATTERN.match(token)
    if range_match:
        expanded = _expand_range(range_match.group(1), range_match.group(2))
        for dependency in expanded:
            if dependency not in task_ids:
                raise PlanParseError(f"{task_id} references unknown task {dependency}")
        return expanded, False
    if TASK_ID.match(token):
        if token not in task_ids:
            raise PlanParseError(f"{task_id} references unknown task {token}")
        return (token,), False
    raise PlanParseError(f"{task_id} has an unparsable prerequisite token '{token}'")


def _parse_prerequisites(
    body: str, task_id: str, task_ids: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    span = _prereq_span(body, task_id)
    segments = [segment.strip() for segment in span.split(CLAUSE_SEPARATOR)]
    start: list[str] = []
    close_only: list[str] = []
    notes: list[str] = []
    for index, segment in enumerate(segments):
        tokens = [token for token in segment.split(ITEM_SEPARATOR) if token.strip()]
        unparsed = False
        for token in tokens:
            try:
                dependencies, is_close_only = _expand_token(token, task_ids, task_id)
            except PlanParseError:
                if index == 0:
                    raise
                unparsed = True
                continue
            (close_only if is_close_only else start).extend(dependencies)
        if index > 0 and unparsed:
            notes.append(segment)
    return (
        tuple(dict.fromkeys(start)),
        tuple(dict.fromkeys(close_only)),
        tuple(notes),
    )


def _conditional_dependency(body: str, task_id: str) -> str | None:
    match = CONDITIONAL_EDGE_PATTERN.search(body)
    return match.group(1) if match else None


def _declared_checkpoints(body: str) -> tuple[str, ...]:
    """Return sub-checkpoints *declared* by a task section.

    A bare mention is a cross-task reference (for example M0-01 quotes
    ``M4-06a`` and M1-06 quotes ``M2-01a``); only a declaration counts:

    * bold label -- ``**M2-04a — request/result adapter**``;
    * the ``**Owner**`` line -- ``INT（M0-01a 基线/coverage）``;
    * a parenthetical definition -- ``M0-03a（B runtime evidence）``.
    """

    declared: list[str] = []
    for line in body.splitlines():
        if "**Owner**" in line:
            declared.extend(CHECKPOINT_ID.findall(line))
        if CHECKPOINT_DECLARATION_MARKER in line:
            tail = line.split(CHECKPOINT_DECLARATION_MARKER, 1)[1]
            declared.extend(CHECKPOINT_ID.findall(tail.split(SENTENCE_END, 1)[0]))
        for checkpoint in CHECKPOINT_ID.findall(line):
            if f"**{checkpoint}" in line or f"{checkpoint}（" in line:
                declared.append(checkpoint)
    return tuple(sorted(dict.fromkeys(declared)))


def _parse_owner(body: str, task_id: str) -> str:
    for line in body.splitlines():
        if "**Owner**：" in line:
            owner = line.split("**Owner**：", 1)[1]
            owner = owner.split(CLAUSE_SEPARATOR, 1)[0].strip()
            return owner
    raise PlanParseError(f"{task_id} has no '**Owner**：' field")


def _parse_size(body: str, task_id: str) -> str:
    match = SIZE_PATTERN.search(body)
    if not match:
        raise PlanParseError(f"{task_id} has no '**规模**' field")
    return match.group(1)


def _close_only_from_prose(task_id: str, body: str) -> tuple[str, ...]:
    """Close-only prerequisites declared in prose instead of the ``前置`` field."""

    additions: list[str] = []
    for rule in CLOSE_ONLY_TEXT_RULES:
        if rule["dependent"] != task_id:
            continue
        if rule["anchor"] not in body:
            raise PlanParseError(
                f"close-only rule for {task_id} lost its plan anchor: {rule['anchor']!r}"
            )
        additions.extend(rule["dependencies"])
    return tuple(dict.fromkeys(additions))


def parse_tasks(text: str) -> tuple[tuple[TaskSpec, ...], dict[str, tuple[str, ...]]]:
    """Parse tasks, their prerequisites and their declared sub-checkpoints."""

    raw_sections = _sections(text)
    task_ids = {task_id for task_id, _, _ in raw_sections}
    if len(task_ids) != len(raw_sections):
        raise PlanParseError("duplicate task heading in the execution plan")
    if len(raw_sections) != TASK_COUNT:
        raise PlanParseError(f"expected {TASK_COUNT} '### Mx-yy' tasks, found {len(raw_sections)}")

    checkpoints: dict[str, tuple[str, ...]] = {}
    tasks: list[TaskSpec] = []
    for task_id, title, body in raw_sections:
        start, close_only, notes = _parse_prerequisites(body, task_id, task_ids)
        close_only = tuple(dict.fromkeys((*close_only, *_close_only_from_prose(task_id, body))))
        declared = _declared_checkpoints(body)
        checkpoints[task_id] = declared
        conditional = CONDITIONAL_HEADING_MARKER in title
        task = TaskSpec(
            id=task_id,
            title=title,
            owner=_parse_owner(body, task_id),
            size=_parse_size(body, task_id),
            start_dependencies=start,
            close_only_dependencies=close_only,
            conditional=conditional,
            checkpoints=declared,
            notes=notes,
        )
        tasks.append(task)
    return tuple(tasks), checkpoints


def find_unknown_checkpoint_refs(text: str, tasks: tuple[TaskSpec, ...]) -> tuple[str, ...]:
    """Return sub-checkpoint references that no task declares.

    A reference is accepted only when the ``Mx-yy`` parent task exists *and* the
    identifier appears inside that task's own section.  This is the mechanical
    reading of M0-01's "校验所有被引用子检查点已定义".
    """

    known = {task.id: task.checkpoints for task in tasks}
    unknown: list[str] = []
    for checkpoint in CHECKPOINT_ID.findall(text):
        parent_match = PARENT_TASK_OF_CHECKPOINT.match(checkpoint)
        if parent_match is None:
            unknown.append(checkpoint)
            continue
        parent = parent_match.group(1)
        if parent not in known or checkpoint not in known[parent]:
            unknown.append(checkpoint)
    return tuple(sorted(set(unknown)))


def parse_gap_table(text: str, task_ids: set[str]) -> tuple[GapRow, ...]:
    rows: list[GapRow] = []
    for line in text.splitlines():
        match = GAP_ROW.match(line)
        if not match:
            continue
        gap_id, h_cell, task_cell, _evidence = match.groups()
        h_rows = _expand_h_cell(h_cell)
        tasks, conditional, notes = _expand_task_cell(task_cell, task_ids)
        rows.append(
            GapRow(
                id=gap_id,
                h_rows=h_rows,
                tasks=tasks,
                conditional_tasks=conditional,
                notes=notes,
            )
        )
    if len(rows) != 26:
        raise PlanParseError(f"expected 26 gap rows (G01–G26), found {len(rows)}")
    return tuple(rows)


def _expand_h_cell(cell: str) -> tuple[str, ...]:
    rows: list[str] = []
    for token in cell.strip().split("/"):
        token = token.strip()
        range_match = H_RANGE.match(token)
        if range_match:
            low, high = int(range_match.group(1)[1:]), int(range_match.group(2)[1:])
            rows.extend(f"H{number}" for number in range(low, high + 1))
        elif token.startswith("H") and token[1:].isdigit():
            rows.append(token)
        else:
            raise PlanParseError(f"unparsable H mapping '{token}'")
    return tuple(dict.fromkeys(rows))


def _expand_task_cell(
    cell: str, task_ids: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    tasks: list[str] = []
    conditional: list[str] = []
    notes: list[str] = []
    for chunk in cell.replace("/", ITEM_SEPARATOR).split(ITEM_SEPARATOR):
        token = chunk.strip()
        if not token:
            continue
        is_conditional = "（条件）" in token
        token = token.split("（", 1)[0].strip()
        range_match = RANGE_PATTERN.match(token)
        if range_match:
            expanded = _expand_range(range_match.group(1), range_match.group(2))
            for dependency in expanded:
                if dependency not in task_ids:
                    raise PlanParseError(f"gap table references unknown task {dependency}")
            tasks.extend(expanded)
            continue
        aggregate = AGGREGATE_PATTERN.match(token)
        if aggregate:
            prefix = aggregate.group(1)
            members = sorted(item for item in task_ids if item.startswith(f"{prefix}-"))
            if not members:
                raise PlanParseError(f"gap table references unknown aggregate '{token}'")
            tasks.extend(members)
            continue
        if TASK_ID.match(token):
            if token not in task_ids:
                raise PlanParseError(f"gap table references unknown task {token}")
            tasks.append(token)
            if is_conditional:
                conditional.append(token)
            continue
        notes.append(token)
    return tuple(dict.fromkeys(tasks)), tuple(dict.fromkeys(conditional)), tuple(notes)


def parse_scope_table(text: str) -> dict[str, tuple[str, ...]]:
    """Parse the section 0.2 initial scope table into per-class capability items."""

    items: dict[str, list[str]] = {"IN_SCOPE": [], "DEFERRED": [], "OUT_OF_SCOPE": []}
    in_section = False
    for line in text.splitlines():
        if line.startswith("### 0.2"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        match = SCOPE_ROW.match(line)
        if not match:
            continue
        scope_class, definition = match.groups()
        for clause in definition.split(CLAUSE_SEPARATOR):
            clause = clause.strip()
            if not clause or clause.startswith(SCOPE_CLAUSE_SKIP_PREFIXES):
                continue
            for item in clause.split(ITEM_SEPARATOR):
                item = item.strip()
                if item:
                    items[scope_class].append(item)
    expected = {"IN_SCOPE": 5, "DEFERRED": 3, "OUT_OF_SCOPE": 7}
    counts = {key: len(value) for key, value in items.items()}
    if counts != expected:
        raise PlanParseError(f"section 0.2 scope table changed shape: {counts} != {expected}")
    return {key: tuple(value) for key, value in items.items()}


def load_model(plan_path: Path = DEFAULT_PLAN) -> PlanModel:
    """Parse the execution plan into the model both acceptance gates consume."""

    text = plan_path.read_text(encoding="utf-8")
    tasks, checkpoints = parse_tasks(text)
    task_ids = {task.id for task in tasks}
    unknown = find_unknown_checkpoint_refs(text, tasks)
    if unknown:
        raise PlanParseError(f"undefined sub-checkpoint reference(s): {', '.join(unknown)}")
    gaps = parse_gap_table(text, task_ids)
    scope_items = parse_scope_table(text)
    conditional_edges: dict[str, tuple[str, ...]] = {}
    for task_id, _title, body in _sections(text):
        dependency = _conditional_dependency(body, task_id)
        if dependency is None:
            continue
        if dependency not in task_ids:
            raise PlanParseError(f"{task_id} has a conditional edge to unknown {dependency}")
        conditional_edges[task_id] = (dependency,)
    declared_conditional = {task.id for task in tasks if task.conditional}
    if not declared_conditional:
        raise PlanParseError("the plan declares no conditional task")
    if set(conditional_edges.values()) and not declared_conditional <= task_ids:
        raise PlanParseError("conditional task is missing from the parsed tasks")
    checkpoint_owners = {checkpoint: task.id for task in tasks for checkpoint in task.checkpoints}
    for checkpoint in checkpoint_owners:
        parent = PARENT_TASK_OF_CHECKPOINT.match(checkpoint)
        if parent is None or parent.group(1) not in task_ids:
            raise PlanParseError(f"checkpoint {checkpoint} has no parent task")
    return PlanModel(
        plan_sha256=sha256_text(text),
        tasks=tasks,
        checkpoints=checkpoints,
        checkpoint_owners=checkpoint_owners,
        unknown_refs=(),
        gaps=gaps,
        scope_items=scope_items,
        conditional_edges=conditional_edges,
    )


def _graph_dependencies(model: PlanModel, mode: str) -> dict[str, tuple[str, ...]]:
    dependencies: dict[str, tuple[str, ...]] = {}
    for task in model.tasks:
        deps = set(task.start_dependencies)
        if mode in CLOSE_MODES:
            deps |= set(task.close_only_dependencies)
        if mode == "close_external_change":
            deps |= set(model.conditional_edges.get(task.id, ()))
        if mode != "close_external_change" and task.conditional:
            # The conditional task is NOT_APPLICABLE on the default path; it
            # stays a node so the task set is stable, but it has no edges.
            deps = set()
        dependencies[task.id] = tuple(sorted(deps))
    return dependencies


def _topological_order(dependencies: dict[str, tuple[str, ...]], mode: str) -> list[str]:
    resolved: list[str] = []
    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        current = state.get(node, 0)
        if current == 1:
            cycle = " -> ".join([*stack[stack.index(node) :], node])
            raise PlanParseError(f"{mode} graph has a dependency cycle: {cycle}")
        if current == 2:
            return
        state[node] = 1
        stack.append(node)
        for dependency in dependencies[node]:
            if dependency not in dependencies:
                raise PlanParseError(f"{mode} graph: {node} depends on unknown task {dependency}")
            visit(dependency, stack)
        stack.pop()
        state[node] = 2
        resolved.append(node)

    for node in sorted(dependencies):
        visit(node, [])
    return resolved


def _longest_path(dependencies: dict[str, tuple[str, ...]], order: list[str]) -> list[str]:
    depth: dict[str, int] = {}
    previous: dict[str, str | None] = {}
    for node in order:
        best_depth = 0
        best_parent: str | None = None
        for dependency in sorted(dependencies[node]):
            candidate = depth[dependency]
            if candidate > best_depth or (
                candidate == best_depth and best_parent is not None and dependency < best_parent
            ):
                best_depth = candidate
                best_parent = dependency
        depth[node] = best_depth + 1
        previous[node] = best_parent
    leaf = min(node for node in depth if depth[node] == max(depth.values()))
    path: list[str] = []
    cursor: str | None = leaf
    while cursor is not None:
        path.append(cursor)
        cursor = previous[cursor]
    path.reverse()
    if len(path) != depth[leaf]:
        raise PlanParseError("critical path reconstruction disagreed with the depth table")
    return path


def build_graph(model: PlanModel, mode: str) -> dict[str, Any]:
    dependencies = _graph_dependencies(model, mode)
    order = _topological_order(dependencies, mode)
    sizes = {task.id: task.size for task in model.tasks}
    tasks_by_id = {task.id: task for task in model.tasks}
    nodes = []
    for task_id in sorted(dependencies):
        task = tasks_by_id[task_id]
        declared = set(task.start_dependencies) | set(task.close_only_dependencies)
        declared |= set(model.conditional_edges.get(task_id, ()))
        nodes.append(
            {
                "id": task_id,
                "title": task.title,
                "size": task.size,
                "owner": task.owner,
                "conditional": task.conditional,
                "applies": mode == "close_external_change" or not task.conditional,
                "dependencies": list(dependencies[task_id]),
                "declared_dependencies": sorted(declared),
                "prereq_notes": list(task.notes),
            }
        )
    edges = [
        {"from": dependency, "to": task_id}
        for task_id in sorted(dependencies)
        for dependency in dependencies[task_id]
    ]
    critical_path = _longest_path(dependencies, order)
    xl_on_path = [task_id for task_id in critical_path if sizes[task_id] == "XL"]
    return {
        "mode": mode,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "xl_count": len(xl_on_path),
        "xl_total": sum(1 for size in sizes.values() if size == "XL"),
        "xl_on_critical_path": xl_on_path,
        "critical_path": critical_path,
        "critical_path_length": len(critical_path),
    }


def build_document(model: PlanModel) -> dict[str, Any]:
    graphs = {
        mode: build_graph(model, mode) for mode in ("start", "close", "close_external_change")
    }
    generator_path = REPO_ROOT / GENERATOR_RELATIVE_PATH
    return {
        "schema": GRAPH_SCHEMA,
        "task_id": "M0-01",
        "generator": GENERATOR_RELATIVE_PATH,
        "generator_sha256": sha256_file(generator_path) if generator_path.is_file() else None,
        "source_plan": PLAN_RELATIVE_PATH,
        "source_plan_sha256": model.plan_sha256,
        "task_count": len(model.tasks),
        "checkpoint_count": len(model.checkpoint_ids),
        "checkpoints": {task.id: list(task.checkpoints) for task in model.tasks},
        "unknown_refs": list(model.unknown_refs),
        "cycles": [],
        "gap_count": len(model.gaps),
        "scope_item_counts": {key: len(value) for key, value in model.scope_items.items()},
        "conditional_edges": [
            {"from": source, "to": target, "condition": "external_change_required"}
            for source, targets in sorted(model.conditional_edges.items())
            for target in targets
        ],
        "close_only_text_rules": [
            {
                "dependent": rule["dependent"],
                "dependencies": list(rule["dependencies"]),
                "reason": rule["reason"],
            }
            for rule in CLOSE_ONLY_TEXT_RULES
        ],
        "dependency_model": {
            "start": (
                "task-start dependencies from the '**前置**' field; close-only "
                "augmentations and the conditional M0-13 external-change task are excluded"
            ),
            "close": (
                "whole-item sign-off: start graph plus close-only edges (M5-01/M5-02 "
                "'M4 全部', M4-06b M4-03/M4-05)"
            ),
            "close_external_change": (
                "close graph plus M0-13 and its M1-01 gate when M0-09 selects "
                "external_change_required"
            ),
            "tie_break": "longest path by node count; lexicographically smallest predecessor",
        },
        "graphs": graphs,
        "default_start": graphs["start"],
        "default_close": graphs["close"],
        "external_change_required_close": graphs["close_external_change"],
    }


def _serialize(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed graph equals a fresh build instead of writing it",
    )
    parser.add_argument("--stdout", action="store_true", help="print the graph to stdout")
    parser.add_argument("--quiet", action="store_true", help="suppress the summary line")
    args = parser.parse_args(argv)

    try:
        model = load_model(args.plan)
        document = build_document(model)
    except PlanParseError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    payload = _serialize(document)
    if args.check:
        if not args.out.is_file():
            print(f"INVALID: graph missing: {args.out}", file=sys.stderr)
            return 1
        recorded = args.out.read_text(encoding="utf-8")
        if recorded != payload:
            print(
                f"INVALID: {args.out} is not the current generator output; rerun "
                "scripts/acceptance/build_acp2_plan_graph.py",
                file=sys.stderr,
            )
            return 1
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    if args.stdout:
        print(payload, end="")
    if not args.quiet:
        start = document["graphs"]["start"]
        close = document["graphs"]["close"]
        external = document["graphs"]["close_external_change"]
        print(
            "VALID: {task} tasks / {checkpoint} checkpoints | start {s_len} ({s_xl} XL, "
            "{s_edges} edges) | close {c_len} ({c_xl} XL, {c_edges} edges) | "
            "external close {e_len} ({e_xl} XL, {e_edges} edges)".format(
                task=document["task_count"],
                checkpoint=document["checkpoint_count"],
                s_len=start["critical_path_length"],
                s_xl=start["xl_count"],
                s_edges=start["edge_count"],
                c_len=close["critical_path_length"],
                c_xl=close["xl_count"],
                c_edges=close["edge_count"],
                e_len=external["critical_path_length"],
                e_xl=external["xl_count"],
                e_edges=external["edge_count"],
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
