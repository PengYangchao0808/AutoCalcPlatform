"""Static consistency checker for the ACP Services work plan (评审 P1-5).

Parses ``.omo/plans/acp-parallel-plan-2-services.md`` (located relative to the
repository root) and asserts the plan's own structural invariants:

1. top-level todo numbers are unique and gap-free (the plan's ``{t}``
   placeholder headers are assigned the missing numbers in file order);
   conditionally-gated todos use the ``[~]`` checkbox and still carry an
   explicit number;
2. every numeric / ``F``-task reference in a ``Blocked by:`` field resolves
   to an existing todo id (cross-lane tokens such as ``C01``/``A05``/``MU-04``
   /``T69``/``B40a`` are lane labels, not this plan's todo ids, and are
   ignored); a todo without a ``Parallelization:`` line declares no
   dependency edges (the appended F-wave remediation rows 80—83 do this);
3. the dependency graph is acyclic;
4. named producer-before-consumer pairs hold over the transitive dependency
   closure (contracts frozen before ports, worker before chain switch,
   release prep before release execution, release gated on F1—F4);
5. final-wave tasks (F1—F5) stay out of the pre-release dependency set —
   only todo 76 (release execution, excluded from the pre-release wave by
   the plan's own scoping note) may depend on F1—F4, never on F5.

``.omo/`` is gitignored: in a clean checkout the plan file is absent and the
whole module SKIPS with a clear message so the repository suite stays green.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = REPO_ROOT / ".omo" / "plans" / "acp-parallel-plan-2-services.md"

if not PLAN_PATH.is_file():
    pytest.skip(
        f"plan file {PLAN_PATH.relative_to(REPO_ROOT)} not present "
        "(.omo/ is gitignored) — static plan checks run only in a full worktree",
        allow_module_level=True,
    )

# ── parsing ─────────────────────────────────────────────────────────────────

# `[ ]` pending, `[x]`/`[X]` done, `[~]` explicitly conditional/gated.
_HEADER_RE = re.compile(r"^- \[[ xX~]\] (\S+?)(?:\.|\s)")
_BLOCKED_BY_RE = re.compile(r"^\s*Parallelization:.*?\|\s*Blocked by:\s*(.*?)\s*\|\s*Blocks:")
_BLOCKED_BY_EOI_RE = re.compile(r"^\s*Parallelization:.*?\|\s*Blocked by:\s*(.+?)\s*$")

# Flat full-width parentheticals carry gate notes / negations ("不再等待 41"),
# never hard dependencies — dropped before reference extraction.
_PAREN_RE = re.compile(r"（[^（）]*）")
# Cross-lane token: C01 / C39a / C-T13 / A05 / MU-04 / OS-03 / B40a / T69 / M3.
# F-tasks of THIS plan are preserved (negative lookahead).
_CROSS_LANE_RE = re.compile(r"(?!F\d)[A-Z](?:-[A-Z]{1,2})?-?\d+[a-z]?")
_RANGE_RE = re.compile(r"(?<![A-Za-z0-9-])(\d+)\s*[—–-]\s*(\d+)(?![A-Za-z0-9])")
_SINGLE_RE = re.compile(r"(?<![A-Za-z0-9-])(\d+)(?![A-Za-z0-9])")
_F_RANGE_RE = re.compile(r"F(\d+)\s*[—–-]\s*F(\d+)")
_F_SINGLE_RE = re.compile(r"F(\d+)")


@dataclass(frozen=True)
class Todo:
    """One top-level plan todo."""

    todo_id: str  # canonical id: "12" or "F3"
    header_raw: str
    blocked_by_text: str | None
    numeric_deps: frozenset[int] = field(default_factory=frozenset)
    f_deps: frozenset[str] = field(default_factory=frozenset)


def _clean_blocked_by(text: str) -> str:
    """Drop parentheticals and cross-lane tokens from a Blocked-by field."""
    text = _PAREN_RE.sub(" ", text)
    text = _CROSS_LANE_RE.sub(" ", text)
    return text


def _extract_numeric_refs(text: str) -> frozenset[int]:
    cleaned = _clean_blocked_by(text)
    refs: set[int] = set()
    for match in _RANGE_RE.finditer(cleaned):
        lo, hi = int(match.group(1)), int(match.group(2))
        refs.update(range(min(lo, hi), max(lo, hi) + 1))
    without_ranges = _RANGE_RE.sub(" ", cleaned)
    for match in _SINGLE_RE.finditer(without_ranges):
        refs.add(int(match.group(1)))
    return frozenset(refs)


def _extract_f_refs(text: str) -> frozenset[str]:
    cleaned = _PAREN_RE.sub(" ", text)
    refs: set[str] = set()
    for match in _F_RANGE_RE.finditer(cleaned):
        lo, hi = int(match.group(1)), int(match.group(2))
        refs.update(f"F{i}" for i in range(min(lo, hi), max(lo, hi) + 1))
    without_ranges = _F_RANGE_RE.sub(" ", cleaned)
    for match in _F_SINGLE_RE.finditer(without_ranges):
        refs.add(f"F{int(match.group(1))}")
    return frozenset(refs)


@dataclass(frozen=True)
class PlanModel:
    """Parsed plan: canonical todos, placeholder assignment, parse problems."""

    todos: dict[str, Todo]
    explicit_numeric_ids: frozenset[int]
    f_ids: frozenset[str]
    placeholder_count: int
    problems: tuple[str, ...]


def _parse_plan(path: Path) -> PlanModel:
    lines = path.read_text(encoding="utf-8").splitlines()

    # Pass 1: collect headers in file order.
    headers: list[tuple[int, str]] = []  # (line_index, raw_id_token)
    for idx, line in enumerate(lines):
        match = _HEADER_RE.match(line)
        if match:
            headers.append((idx, match.group(1)))

    explicit_numeric: list[int] = []
    f_ints: list[int] = []
    placeholder_slots: list[int] = []  # header positions of {t} entries
    problems: list[str] = []
    for pos, (idx, token) in enumerate(headers):
        if token == "{t}":
            placeholder_slots.append(pos)
        elif token.isdigit():
            explicit_numeric.append(int(token))
        elif re.fullmatch(r"F\d+", token):
            f_ints.append(int(token[1:]))
        else:
            problems.append(f"line {idx + 1}: unrecognised todo id token {token!r}")

    # Uniqueness of explicit numeric ids.
    seen: set[int] = set()
    for value in explicit_numeric:
        if value in seen:
            problems.append(f"duplicate todo number {value}")
        seen.add(value)

    # Assign {t} placeholders the missing numbers in 1..max, in file order.
    numeric_set = set(explicit_numeric)
    max_numeric = max(numeric_set) if numeric_set else 0
    gaps = [n for n in range(1, max_numeric + 1) if n not in numeric_set]
    if len(gaps) != len(placeholder_slots):
        problems.append(
            f"{{t}} placeholder count ({len(placeholder_slots)}) does not match "
            f"numbering gaps in 1..{max_numeric} ({gaps})"
        )
    assigned: dict[int, str] = {}
    for gap, pos in zip(gaps, placeholder_slots):
        assigned[pos] = str(gap)

    # F-id contiguity: F1..Fmax with no gaps.
    sorted_f = sorted(f_ints)
    expected_f = list(range(1, (max(sorted_f) if sorted_f else 0) + 1))
    if sorted_f != expected_f:
        problems.append(
            f"final-wave ids are not contiguous F1..F{max(sorted_f, default=0)}: {sorted_f}"
        )

    # Pass 2: build Todo records with their Blocked-by fields.
    todos: dict[str, Todo] = {}
    for pos, (idx, token) in enumerate(headers):
        todo_id = assigned.get(pos, token if token != "{t}" else None)
        if todo_id is None:
            continue  # unassigned placeholder already reported
        blocked_text: str | None = None
        end = headers[pos + 1][0] if pos + 1 < len(headers) else len(lines)
        for line in lines[idx + 1 : end]:
            if _BLOCKED_BY_RE.match(line):
                blocked_text = _BLOCKED_BY_RE.match(line).group(1)
                break
            early = _BLOCKED_BY_EOI_RE.match(line)
            if early:
                blocked_text = early.group(1)
                break
        numeric_deps: frozenset[int] = frozenset()
        f_deps: frozenset[str] = frozenset()
        if blocked_text is not None:
            numeric_deps = _extract_numeric_refs(blocked_text)
            f_deps = _extract_f_refs(blocked_text)
        todos[todo_id] = Todo(
            todo_id=todo_id,
            header_raw=lines[idx],
            blocked_by_text=blocked_text,
            numeric_deps=numeric_deps,
            f_deps=f_deps,
        )

    return PlanModel(
        todos=todos,
        explicit_numeric_ids=frozenset(explicit_numeric),
        f_ids=frozenset(f"F{i}" for i in f_ints),
        placeholder_count=len(placeholder_slots),
        problems=tuple(problems),
    )


@lru_cache(maxsize=1)
def _plan() -> PlanModel:
    return _parse_plan(PLAN_PATH)


def _known_ids(model: PlanModel) -> set[str]:
    return set(model.todos)


def _deps_of(model: PlanModel, todo_id: str) -> set[str]:
    todo = model.todos[todo_id]
    known = _known_ids(model)
    deps: set[str] = set()
    for value in todo.numeric_deps:
        deps.add(str(value))
    deps.update(todo.f_deps)
    return {d for d in deps if d in known or d[0].isdigit()}


def _dependency_closure(model: PlanModel, todo_id: str) -> set[str]:
    """Transitive Blocked-by closure of ``todo_id`` (excluding itself)."""
    seen: set[str] = set()
    stack = list(_deps_of(model, todo_id))
    while stack:
        current = stack.pop()
        if current in seen or current not in model.todos:
            continue
        seen.add(current)
        stack.extend(_deps_of(model, current))
    return seen


def _dangling_refs(model: PlanModel) -> list[str]:
    known = _known_ids(model)
    dangling: list[str] = []
    for todo_id, todo in sorted(model.todos.items()):
        for value in sorted(todo.numeric_deps):
            if str(value) not in known:
                dangling.append(f"todo {todo_id} -> numeric {value}")
        for fid in sorted(todo.f_deps):
            if fid not in known:
                dangling.append(f"todo {todo_id} -> {fid}")
    return dangling


def _cycle_path(model: PlanModel) -> list[str] | None:
    white, grey, black = 0, 1, 2
    color = {tid: white for tid in model.todos}
    cycle_path: list[str] = []

    def dfs(node: str) -> bool:
        color[node] = grey
        for dep in sorted(_deps_of(model, node)):
            if dep not in color:
                continue
            if color[dep] == grey:
                cycle_path.extend([dep, node])
                return True
            if color[dep] == white and dfs(dep):
                cycle_path.append(node)
                return True
        color[node] = black
        return False

    for todo_id in sorted(
        model.todos, key=lambda t: (not t.isdigit(), int(t) if t.isdigit() else t)
    ):
        if color[todo_id] == white and dfs(todo_id):
            return cycle_path
    return None


# ── tests ───────────────────────────────────────────────────────────────────


def test_plan_parses_without_problems() -> None:
    model = _plan()
    assert not model.problems, "plan parse problems: " + "; ".join(model.problems)
    assert len(model.todos) >= 70, f"only {len(model.todos)} todos parsed — parser regression"
    assert model.placeholder_count >= 0


def test_todo_numbering_unique_and_gap_free() -> None:
    model = _plan()
    explicit = sorted(model.explicit_numeric_ids)
    assert len(explicit) == len(set(explicit)), "duplicate explicit todo numbers"
    max_id = explicit[-1]
    missing = sorted(set(range(1, max_id + 1)) - set(explicit))
    assert len(missing) == model.placeholder_count, (
        f"numbering gaps {missing} not covered by {{t}} placeholders "
        f"(count={model.placeholder_count})"
    )
    # After placeholder assignment every id 1..max must exist exactly once.
    numeric_ids = [t for t in model.todos if t.isdigit()]
    assert sorted(int(t) for t in numeric_ids) == list(range(1, max_id + 1))
    # Final-wave ids are unique and contiguous.
    f_ints = sorted(int(t[1:]) for t in model.todos if t.startswith("F"))
    assert f_ints == list(range(1, len(f_ints) + 1)), f"non-contiguous F ids: {f_ints}"


def test_blocked_by_references_resolve_to_existing_todos() -> None:
    dangling = _dangling_refs(_plan())
    assert not dangling, "dangling Blocked-by references: " + ", ".join(dangling)


def test_dependency_graph_is_acyclic() -> None:
    cycle_path = _cycle_path(_plan())
    assert cycle_path is None, "dependency cycle detected: " + " <- ".join(cycle_path or [])


def test_artifact_before_consumer_named_pairs() -> None:
    """Named producers must be (transitive) dependencies of their consumers."""
    model = _plan()
    pairs = [
        ("3", "6", "first-slice contracts frozen before workspace access ports"),
        ("3", "11", "first-slice contracts frozen before DataResourcePort"),
        ("3", "12", "first-slice contracts frozen before StorageBinding authority"),
        ("3", "30", "first-slice contracts frozen before ExecutionEnvelope schema"),
        ("3", "31", "first-slice contracts frozen before worker rules"),
        ("31", "32", "worker (frozen plan + receipt) before whole-chain switch"),
        ("71", "76", "release prep before release execution"),
    ]
    for producer, consumer, rationale in pairs:
        closure = _dependency_closure(model, consumer)
        assert producer in closure, (
            f"{rationale}: todo {producer} is not a (transitive) Blocked-by "
            f"dependency of todo {consumer} (closure: {sorted(closure)})"
        )
    # Release execution is gated on the final verification wave F1—F4.
    release_deps = _deps_of(model, "76")
    for gate in ("F1", "F4"):
        assert gate in release_deps, (
            f"release execution (todo 76) must depend on {gate} approval, "
            f"deps={sorted(release_deps)}"
        )


def test_final_wave_tasks_excluded_from_pre_release_dependencies() -> None:
    """Only todo 76 (release execution) may depend on F-tasks — and only on F1—F4.

    The plan's Final verification wave note runs F1—F4 in parallel after the
    pre-release implementation todos (todo 76 excluded by design); F5 verifies
    release *results* after 76 and is never a dependency of any implementation
    todo.
    """
    model = _plan()
    violations: list[str] = []
    for todo_id, todo in sorted(model.todos.items()):
        if todo_id.startswith("F") or todo_id == "76":
            continue
        if todo.f_deps:
            violations.append(f"todo {todo_id} depends on {sorted(todo.f_deps)}")
    assert not violations, "final-wave tasks leaked into pre-release dependency set: " + "; ".join(
        violations
    )
    release_f_deps = model.todos["76"].f_deps
    assert release_f_deps == {"F1", "F2", "F3", "F4"}, (
        f"todo 76 must depend on exactly F1—F4 approval, got {sorted(release_f_deps)}"
    )


# ── negative self-tests (synthetic plans; the real plan is never mutated) ──


def _synthetic_todo(
    todo_id: str,
    numeric: tuple[int, ...] = (),
    f_ids: tuple[str, ...] = (),
) -> Todo:
    return Todo(
        todo_id=todo_id,
        header_raw=f"- [ ] {todo_id}.",
        blocked_by_text=None,
        numeric_deps=frozenset(numeric),
        f_deps=frozenset(f_ids),
    )


def _synthetic_model(todos: dict[str, Todo]) -> PlanModel:
    return PlanModel(
        todos=todos,
        explicit_numeric_ids=frozenset(int(t) for t in todos if t.isdigit()),
        f_ids=frozenset(t for t in todos if t.startswith("F")),
        placeholder_count=0,
        problems=(),
    )


def test_conditional_checkbox_row_is_parsed(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(
        "- [~] 18. conditional gate\n"
        "  Parallelization: Wave 4 | Blocked by: 12 | Blocks: 62\n",
        encoding="utf-8",
    )
    model = _parse_plan(plan)
    assert "18" in model.todos
    assert model.todos["18"].numeric_deps == frozenset({12})


def test_negative_duplicate_todo_number_is_detected(tmp_path: Path) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text(
        "- [x] 1. first\n"
        "  Parallelization: Wave 1 | Blocked by: none | Blocks: 2\n"
        "- [x] 1. duplicate\n"
        "  Parallelization: Wave 1 | Blocked by: none | Blocks: 2\n",
        encoding="utf-8",
    )
    model = _parse_plan(plan)
    assert any("duplicate todo number 1" in problem for problem in model.problems)


def test_negative_invalid_reference_is_detected() -> None:
    model = _synthetic_model({"1": _synthetic_todo("1", numeric=(99,))})
    assert _dangling_refs(model) == ["todo 1 -> numeric 99"]


def test_negative_dependency_cycle_is_detected() -> None:
    model = _synthetic_model(
        {
            "1": _synthetic_todo("1", numeric=(2,)),
            "2": _synthetic_todo("2", numeric=(1,)),
        }
    )
    assert _cycle_path(model) is not None
