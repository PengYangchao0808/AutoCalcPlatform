"""Guard tests for the B05-5 (CQ-02) owner-scoped helper merge.

This file is both the **acceptance test** and the **merge inventory** for the
"merge equivalent pure helpers per-owner (no universal utils)" change:

* the Runtime owner's UTC-ISO helper (one identical ``datetime.now(timezone.utc)
  .isoformat()`` copy-pasted across the scheduler and the Runtime package) now
  lives once in :mod:`acp_services.runtime.helpers`; every former copy imports
  it under its historical private alias, so equivalence is *by construction*
  (same function object);
* the Data owner's atomic text writer now lives once in
  :mod:`acp_services.data.helpers`; ``acp.results.frame_candidate_store``
  delegates to it and keeps its public API;
* numeric constants (``HARTREE_TO_KCAL`` / gas constant ``R``) with *differing*
  precisions are **deliberately not merged** (C07 decision) — equivalence could
  not be proven, so the merge is refused;
* no module may reach across owners into another owner's private SQLite tables
  (Runtime touching ``data_*`` tables or Data touching Runtime tables).

The negative controls at the bottom prove both scanners actually fire.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"

_RUNTIME_HELPER_MODULE = "acp_services.runtime.helpers"
_DATA_HELPER_MODULE = "acp_services.data.helpers"

# ---------------------------------------------------------------------------
# Merge inventory
# ---------------------------------------------------------------------------
#: ``(old private symbol, old module, new canonical location, callers)``.
#: ``callers`` is informational (the private name is still importable from the
#: old module via the thin alias).
TIME_HELPER_INVENTORY: tuple[tuple[str, str, str, str], ...] = (
    (
        "_utc_now_iso",
        "src/acp/scheduler/artifacts.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "capture_stage_artifacts",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/migrations.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "migrate",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/events.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "JobEventLog.append",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/molecule_groups.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "MoleculeGroupStore",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/registration.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "register_completed_cli_job",
    ),
    (
        "_utc_now_iso (+ _utc_now removed)",
        "src/acp/scheduler/jobs.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "JobRecord default_factory",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/stage_tasks.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "StageTaskStore / StageTaskObserver",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/store.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "JobStore.transition/append_event",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/runner.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "JobRunner",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/tasks.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "TaskIndex.update_custom_name + acp.api.v2_routes",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/structure_source_store.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "StructureSourceStore",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/job_edit.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "JobEditOperationStore",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/manager.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "JobManager",
    ),
    (
        "_utc_now_iso",
        "src/acp/scheduler/remote/runner.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "RemoteJobRunner",
    ),
    (
        "_utc_now_iso",
        "src/acp_services/runtime/worker.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "write_receipt_atomic",
    ),
    (
        "_utc_now_iso",
        "src/acp_services/runtime/events.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "EventLogPort.append",
    ),
    (
        "_utc_now_iso",
        "src/acp_services/runtime/outbox.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "OutboxDeliveryService",
    ),
    (
        "_utc_now_iso",
        "src/acp_services/runtime/projections.py",
        "src/acp_services/runtime/helpers.py::utc_now_iso",
        "TaskProjectionStore._clock",
    ),
)

#: The Runtime modules that must now import the single helper (minor: the path
#: is the alias-holder; ``local_cleanup`` had a *dead* copy that was deleted, so
#: it holds neither a definition nor an alias).
RUNTIME_TIME_ALIAS_MODULES: tuple[str, ...] = tuple(
    old_module for _sym, old_module, _new, _callers in TIME_HELPER_INVENTORY
)
RUNTIME_TIME_DEAD_COPY_MODULES: tuple[str, ...] = ("src/acp/scheduler/local_cleanup.py",)

#: Atomic-write inventory (Data owner).
ATOMIC_WRITE_INVENTORY: tuple[tuple[str, str, str, str], ...] = (
    (
        "atomic_write_text",
        "src/acp/results/frame_candidate_store.py",
        "src/acp_services/data/helpers.py::atomic_write_text",
        "frame_candidates/structure_snapshots/structure_migration/nmr/"
        "calculations.executor/services_publication_bootstrap",
    ),
)

#: Copies that were inventoried but **refused** because equivalence could not be
#: proven (different on-disk format) or because they belong to another owner.
NOT_MERGED_COPIES: tuple[tuple[str, str], ...] = (
    ("_utc_now_iso (time.strftime + 'Z', no microseconds)", "src/acp_services/data/operations.py"),
    ("_utc_now_iso (time.strftime + 'Z', no microseconds)", "src/acp_services/data/transfer.py"),
    ("_utc_now_iso (Data owner — cross-owner merge forbidden)", "src/acp_services/data/read_root.py"),
    ("_utc_now_iso (Workspace owner — out of scope)", "src/acp_services/workspace/projects.py"),
    ("_utc_now_iso (API owner — out of scope)", "src/acp/api/v1_routes.py"),
    ("_utc_now_iso (acp.calculations — sibling lane)", "src/acp/calculations/batch/engine.py"),
)

#: Numeric constants explicitly out of scope (C07): same name, different precision.
NUMERIC_CONSTANTS_NOT_MERGED: tuple[str, ...] = ("HARTREE_TO_KCAL", "R")


# ---------------------------------------------------------------------------
# (a) invariants: single definition + identity equivalence
# ---------------------------------------------------------------------------


def test_runtime_helper_module_exists_and_has_no_acp_import() -> None:
    path = _SRC / "acp_services" / "runtime" / "helpers.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    assert "acp" not in imported, "Runtime helper must not import acp"


def test_no_local_time_helper_definition_in_merged_modules() -> None:
    """Every merged module must *import* the helper, never re-define it."""
    offenders: dict[str, list[str]] = {}
    for rel in (*RUNTIME_TIME_ALIAS_MODULES, *RUNTIME_TIME_DEAD_COPY_MODULES):
        path = _REPO_ROOT / rel
        violations = find_local_helper_definitions(rel, path.read_text(encoding="utf-8"))
        if violations:
            offenders[rel] = violations
    assert not offenders, f"local helper definitions survived the merge: {offenders}"


def test_time_helper_is_one_object_across_all_runtime_modules() -> None:
    """Equivalence proof: the alias resolves to the *same* function object."""
    canonical = importlib.import_module(_RUNTIME_HELPER_MODULE).utc_now_iso
    mismatched: list[str] = []
    for rel in RUNTIME_TIME_ALIAS_MODULES:
        module = importlib.import_module(_module_name_for(rel))
        alias = getattr(module, "_utc_now_iso", None)
        if alias is not canonical:
            mismatched.append(rel)
    assert not mismatched, f"aliases not identical to the canonical helper: {mismatched}"


def test_dead_local_cleanup_copy_is_gone_without_alias() -> None:
    module = importlib.import_module(_module_name_for("src/acp/scheduler/local_cleanup.py"))
    assert not hasattr(module, "_utc_now_iso")
    # the epoch variant is a *different* function and must remain.
    assert hasattr(module, "_utc_now_iso_from")


_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00$")


def test_utc_now_iso_format_and_round_trip() -> None:
    canonical = importlib.import_module(_RUNTIME_HELPER_MODULE).utc_now_iso
    for _ in range(5):
        value = canonical()
        assert _ISO_RE.match(value), f"unexpected format: {value!r}"
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)
        # round-trip: re-serialising the parsed value is byte-identical
        assert parsed.isoformat() == value


def test_utc_now_iso_matches_reference_producer_format() -> None:
    """Microsecond precision + UTC offset, exactly like the merged copies."""
    canonical = importlib.import_module(_RUNTIME_HELPER_MODULE).utc_now_iso
    reference = datetime.now(timezone.utc).isoformat()
    assert len(canonical()) == len(reference)
    assert canonical().endswith("+00:00")


# ---------------------------------------------------------------------------
# (b) atomic-write equivalence
# ---------------------------------------------------------------------------


def test_atomic_writer_is_data_owner_canonical() -> None:
    helpers = importlib.import_module(_DATA_HELPER_MODULE)
    assert callable(helpers.atomic_write_text)


def test_frame_candidate_store_delegates_to_data_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    fcs = importlib.import_module("acp.results.frame_candidate_store")
    calls: list[tuple[Path, str]] = []

    def _record(path: Path, text: str) -> None:
        calls.append((path, text))

    monkeypatch.setattr(fcs, "_atomic_write_text", _record)
    target = Path("/tmp/never-written-by-recorder.json")
    fcs.atomic_write_text(target, "payload")
    assert calls == [(target, "payload")]


def test_atomic_write_text_writes_content_and_creates_parents(tmp_path: Path) -> None:
    helpers = importlib.import_module(_DATA_HELPER_MODULE)
    target = tmp_path / "deep" / "nested" / "file.txt"
    helpers.atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_leaves_no_temp_and_overwrites(tmp_path: Path) -> None:
    helpers = importlib.import_module(_DATA_HELPER_MODULE)
    target = tmp_path / "file.txt"
    helpers.atomic_write_text(target, "one")
    helpers.atomic_write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_atomic_write_text_is_owner_only(tmp_path: Path) -> None:
    helpers = importlib.import_module(_DATA_HELPER_MODULE)
    target = tmp_path / "perm.txt"
    helpers.atomic_write_text(target, "x")
    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode & 0o077 == 0, f"file must not be group/other accessible: {oct(mode)}"


def test_atomic_write_text_cleans_temp_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helpers = importlib.import_module(_DATA_HELPER_MODULE)
    captured: dict[str, Path] = {}

    def _boom(src: str, dst: os.PathLike[str] | str) -> None:
        captured["src"] = Path(src)
        raise RuntimeError("replace failed")

    monkeypatch.setattr(os, "replace", _boom)
    target = tmp_path / "file.txt"
    with pytest.raises(RuntimeError):
        helpers.atomic_write_text(target, "payload")
    # temp lives in the same directory (atomic replace precondition) ...
    assert captured["src"].parent == tmp_path
    # ... and is cleaned up on failure; the target was never created.
    assert not target.exists()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


# ---------------------------------------------------------------------------
# (c) inventory coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("old_module", [row[1] for row in TIME_HELPER_INVENTORY])
def test_inventory_modules_exist(old_module: str) -> None:
    assert (_REPO_ROOT / old_module).is_file()


@pytest.mark.parametrize("old_module", [row[1] for row in ATOMIC_WRITE_INVENTORY])
def test_atomic_inventory_modules_exist(old_module: str) -> None:
    assert (_REPO_ROOT / old_module).is_file()


def test_inventory_covers_every_definition_under_owner_roots() -> None:
    """A *new* local definition anywhere in the Runtime/Data roots is a merge hole."""
    holes: list[str] = []
    runtime_root = _SRC / "acp_services" / "runtime"
    scheduler_root = _SRC / "acp" / "scheduler"
    for root in (runtime_root, scheduler_root):
        for path in sorted(root.rglob("*.py")):
            if path.name == "helpers.py" or "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            violations = find_local_helper_definitions(str(path.relative_to(_REPO_ROOT)), source)
            if violations:
                holes.append(f"{path.relative_to(_REPO_ROOT)}: {violations}")
    assert not holes, f"uninventoried local time-helper definitions: {holes}"


def test_numeric_constants_are_not_merged() -> None:
    for module_name in (_RUNTIME_HELPER_MODULE, _DATA_HELPER_MODULE):
        module = importlib.import_module(module_name)
        for const in NUMERIC_CONSTANTS_NOT_MERGED:
            assert not hasattr(module, const), f"{const} must not be merged into {module_name}"


# ---------------------------------------------------------------------------
# (d) cross-owner private-table access (AST assertion, zero hits)
# ---------------------------------------------------------------------------

_OWNER_ROOTS: dict[str, tuple[str, ...]] = {
    "runtime": ("src/acp_services/runtime", "src/acp/scheduler"),
    "data": ("src/acp_services/data",),
}

#: Tables each owner owns.  Access from another owner is a violation.
_OWNER_PRIVATE_TABLES: dict[str, frozenset[str]] = {
    "runtime": frozenset(
        {
            "jobs",
            "job_outbox",
            "stage_tasks",
            "artifacts",
            "tasks",
            "_schema_migrations",
            "job_edit_operations",
        }
    ),
    "data": frozenset({"data_operations", "data_transfer_publications"}),
}

_SQL_TABLE_RE = re.compile(
    r"\b(?:FROM|INTO|UPDATE|JOIN|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
_SQL_KEYWORD_RE = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE)\b", re.IGNORECASE)


def _static_string(node: ast.AST) -> str | None:
    """Best-effort static value of a str Constant / JoinedStr / `+` fold."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append(" ")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def find_cross_owner_table_access(owner: str, source: str, label: str) -> list[str]:
    """Return violations where *owner* source references another owner's table."""
    foreign: set[str] = set()
    for other, tables in _OWNER_PRIVATE_TABLES.items():
        if other != owner:
            foreign |= tables
    violations: list[str] = []
    seen: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
            continue
        if isinstance(node, ast.Constant) and not isinstance(node.value, str):
            continue
        text = _static_string(node)
        if not text or not _SQL_KEYWORD_RE.search(text):
            continue
        for match in _SQL_TABLE_RE.finditer(text):
            table = match.group(1)
            if table in foreign and (label, table) not in seen:
                seen.add((label, table))
                violations.append(f"{label}: cross-owner table {table!r}")
    return violations


def _owner_of(path: Path) -> str | None:
    rel = str(path.relative_to(_REPO_ROOT))
    for owner, roots in _OWNER_ROOTS.items():
        if any(rel.startswith(root + "/") for root in roots):
            return owner
    return None


def test_no_cross_owner_private_table_access() -> None:
    offenders: list[str] = []
    for owner, roots in _OWNER_ROOTS.items():
        for root in roots:
            for path in sorted((_REPO_ROOT / root).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                assert _owner_of(path) == owner
                offenders += find_cross_owner_table_access(
                    owner, path.read_text(encoding="utf-8"), str(path.relative_to(_REPO_ROOT))
                )
    assert not offenders, f"cross-owner private-table access detected: {offenders}"


# ---------------------------------------------------------------------------
# (e) negative controls — the scanners must fire on injected duplicates
# ---------------------------------------------------------------------------


def test_negative_injected_duplicate_is_detected(tmp_path: Path) -> None:
    injected = tmp_path / "injected_dup.py"
    injected.write_text(
        "from __future__ import annotations\n"
        "from datetime import datetime, timezone\n"
        "\n"
        "\n"
        "def _utc_now_iso() -> str:\n"
        "    return datetime.now(timezone.utc).isoformat()\n",
        encoding="utf-8",
    )
    violations = find_local_helper_definitions(
        "injected/injected_dup.py", injected.read_text(encoding="utf-8")
    )
    assert violations, "duplicated helper definition was NOT detected"


def test_negative_cross_owner_table_access_is_detected() -> None:
    injected = (
        "SQL = 'SELECT * FROM data_operations WHERE state = ?'\n"
    )
    violations = find_cross_owner_table_access("runtime", injected, "injected/runtime_mod.py")
    assert violations, "cross-owner private-table access was NOT detected"
    # and the Data owner reading its own table is *not* a violation
    assert not find_cross_owner_table_access("data", injected, "injected/data_mod.py")


def test_negative_data_owner_reading_runtime_table_is_detected() -> None:
    injected = "SQL = 'UPDATE job_outbox SET delivered_at = ?'\n"
    violations = find_cross_owner_table_access("data", injected, "injected/data_mod.py")
    assert violations


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_TIME_HELPER_NAMES = frozenset({"utc_now_iso", "_utc_now_iso"})


def find_local_helper_definitions(label: str, source: str) -> list[str]:
    """Return violations for local ``utc_now_iso``/``_utc_now_iso`` definitions."""
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _TIME_HELPER_NAMES:
            violations.append(f"{label}:{node.lineno}: local def {node.name}")
    return violations


def _module_name_for(rel_path: str) -> str:
    """``src/acp/scheduler/jobs.py`` -> ``acp.scheduler.jobs``."""
    rel = rel_path[len("src/") :]
    assert rel.endswith(".py")
    return rel[: -len(".py")].replace("/", ".")
