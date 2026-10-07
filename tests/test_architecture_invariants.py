from __future__ import annotations

import ast
import inspect
import re
import textwrap
from pathlib import Path

import pytest

from acp.calculations.batch import engine as batch_engine
from acp.calculations.batch.models import BatchStructureItem, JsonObject, load_batch_request
from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
from acp.scheduler.jobs import ALL_WORKFLOWS, PUBLIC_WORKFLOWS, SUPPORTED_WORKFLOWS
from acp.storage.manifest import ProductKind, ResultManifest

CURRENT_ACTIVE_IDS = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "tsmode",
    "casscf",
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "XtbPathSearch",
    "OrcaGradient",
)
TARGET_ACTIVE_IDS = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "tsmode",
    "casscf",
    "xtb_optimize",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "nmr",
    "XtbPathSearch",
    "OrcaGradient",
)

TARGET_STATE_ENABLED = True


def _cli_dispatch_ids() -> set[str]:
    from acp import cli

    tree = ast.parse(textwrap.dedent(inspect.getsource(cli.main)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        if not isinstance(node.target, ast.Name) or node.target.id != "dispatch":
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        annotated_keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                break
            annotated_keys.append(key.value)
        else:
            return set(annotated_keys)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = node.targets
        if not any(isinstance(target, ast.Name) and target.id == "dispatch" for target in targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        assignment_keys: list[str] = []
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                break
            assignment_keys.append(key.value)
        else:
            return set(assignment_keys)
    raise AssertionError("main() must define a string-keyed workflow dispatch dictionary")


def test_current_active_workflow_ids_are_exact_and_ordered() -> None:
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")

    assert active_ids == CURRENT_ACTIVE_IDS
    assert len(active_ids) == 14
    assert set(active_ids) == {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "tsmode",
        "casscf",
        "xtb_optimize",
        "nmr",
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
        "XtbPathSearch",
        "OrcaGradient",
    }


def test_scheduler_workflows_follow_catalog_order_with_fake_hook() -> None:
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")

    assert PUBLIC_WORKFLOWS == active_ids
    assert ALL_WORKFLOWS == active_ids + ("fake",)
    assert SUPPORTED_WORKFLOWS == active_ids + ("fake",)


def test_each_active_workflow_has_dispatch_and_method_schema() -> None:
    dispatch_ids = _cli_dispatch_ids()

    for entry in WORKFLOW_CATALOG:
        if entry.get("status") != "active":
            continue
        workflow_id = entry.get("id")
        assert isinstance(workflow_id, str)
        assert workflow_id in dispatch_ids
        assert entry["method_schema_id"] in METHOD_SCHEMAS


def test_batch_schema_rejects_irc() -> None:
    payload: JsonObject = {
        "schema_version": "batch_structures_v1",
        "irc": {"directions": ["forward"]},
        "items": [
            {
                "id": "int_001",
                "xyz": "2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n",
            }
        ],
    }

    with pytest.raises(ValueError, match="IRC"):
        _ = load_batch_request(payload)


@pytest.mark.usefixtures("fake_backend")
def test_batch_manifest_no_irc_product(tmp_path: Path) -> None:
    item = BatchStructureItem(
        item_id="int_001",
        name="INT candidate",
        tag="INT",
        xyz="2\nTAG: INT | candidate_id=int_001\nH 0 0 0\nH 0 0 0.7\n",
        candidate_id="int_001",
    )
    result_root = tmp_path / "task" / "RESULT"

    outcome = batch_engine.BatchOptimizeEngine(
        work_root=tmp_path / "task" / "WORK",
        result_root=result_root,
    ).run([item], profile="opt_only")

    assert outcome.items[0].status == "completed"
    manifest = ResultManifest.read(result_root)
    assert manifest.products
    assert all(product.kind is ProductKind.STRUCTURE for product in manifest.products)
    assert all(product.kind is not ProductKind.IRC_ENDPOINT for product in manifest.products)
    assert all(
        "irc" not in f"{product.id} {product.label} {product.path}".casefold()
        for product in manifest.products
    )


def test_batch_engine_no_endpoint_provider_import() -> None:
    source = inspect.getsource(batch_engine)

    assert "EndpointProvider" not in source
    assert "MechanismProject" not in source


def test_batch_no_irc() -> None:
    source = inspect.getsource(batch_engine).casefold()

    assert "irc" not in source


def test_batch_engine_no_stage_symbols() -> None:
    source = inspect.getsource(batch_engine).casefold()

    for stage_symbol in ("s3", "s4", "lowconfirm", "highconfirm"):
        assert stage_symbol not in source


@pytest.mark.skipif(not TARGET_STATE_ENABLED, reason="Target-state gate activates at Todo 36")
def test_target_active_workflow_ids_are_exact() -> None:
    active_ids = tuple(w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active")

    assert len(active_ids) == 14
    assert set(active_ids) == {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "tsmode",
        "casscf",
        "xtb_optimize",
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
        "nmr",
        "XtbPathSearch",
        "OrcaGradient",
    }


def test_no_irc_calculation_step() -> None:
    """IRC must be a standalone IrcRequest, never a CalculationStep."""
    from acp.calculations.contracts import StepKind

    irc_kinds = [k for k in StepKind if k.value == "irc"]
    assert irc_kinds == [], f"StepKind should not contain irc, got: {irc_kinds}"


# ---------------------------------------------------------------------------
# Final-state invariants (todo 50)
# ---------------------------------------------------------------------------


def test_retired_workflows_not_in_active_catalog() -> None:
    """optfreq, optfreqsp, Lowconfirm, Highconfirm must not appear as active IDs."""
    active_ids = {w["id"] for w in WORKFLOW_CATALOG if w.get("status") == "active"}
    for retired in ("optfreq", "optfreqsp", "Lowconfirm", "Highconfirm"):
        assert retired not in active_ids, f"{retired} should not be in active catalog"


def test_new_code_never_writes_s3_s4_manifest() -> None:
    """Batch engine and PES engine must not write s3/s4 lowconfirm/highconfirm manifests."""
    import acp.calculations.batch.engine as be
    import acp.calculations.pes.engine as pe

    for module in (be, pe):
        source = inspect.getsource(module).casefold()
        for pattern in ("s3_lowconfirm_manifest", "s4_highconfirm_manifest"):
            assert pattern not in source, f"{module.__name__} still writes {pattern}"


def test_legacy_api_mechanism_returns_410(tmp_path, monkeypatch) -> None:
    """Mechanism mutation endpoints must return 410 Gone (read-only)."""
    # Env must precede import: server.py's module-level create_app() resolves
    # run_root at import time and would collide with a live server's lock.
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    client = TestClient(create_app(run_root=tmp_path))
    for method, url in [
        ("POST", "/api/v1/mechanism-studies/study-x/promote"),
        ("POST", "/api/v1/mechanism-studies/study-x/resume"),
    ]:
        resp = client.request(method, url, json={})
        assert resp.status_code == 410, f"{method} {url} returned {resp.status_code}, expected 410"


def test_all_new_results_register_result_manifest() -> None:
    """All product registration routes through the NAMED ACP manifest-write
    module ``acp.calculations.result_publication`` (todo 16).

    The named module is the single ACP persistence entry for
    ``RESULT/result_manifest.json``; the executor / scan / IRC registration
    sites must call its seam (``register_result_manifest``) and must not write
    the manifest themselves.
    """
    import acp.calculations.executor as executor_mod
    import acp.calculations.primitives.irc as irc_mod
    import acp.calculations.primitives.scan as scan_mod
    import acp.calculations.result_publication as publication_mod

    publication_source = inspect.getsource(publication_mod)
    assert "ResultManifest" in publication_source, (
        "acp.calculations.result_publication no longer persists ResultManifest"
    )
    assert "def register_result_manifest" in publication_source, (
        "named module lost its registration seam register_result_manifest()"
    )

    for module in (executor_mod, scan_mod, irc_mod):
        source = inspect.getsource(module)
        assert "register_result_manifest" in source, (
            f"{module.__name__} does not register via acp.calculations.result_publication"
        )
        assert "manifest.write(" not in source, (
            f"{module.__name__} writes the manifest directly; route through "
            "acp.calculations.result_publication.register_result_manifest"
        )


# ---------------------------------------------------------------------------
# Capability evidence audit (todo 50)
# ---------------------------------------------------------------------------

_CAPABILITY_EVIDENCE_PATH = (
    Path(__file__).parent / "baseline" / "refactor-evidence" / "capability-evidence.md"
)


def test_capability_evidence_table() -> None:
    """Parse capability-evidence.md and cross-check each row's test function
    against pytest --collect-only output.  Missing row → FAIL."""
    import subprocess
    import sys

    assert _CAPABILITY_EVIDENCE_PATH.is_file(), (
        f"Capability evidence file not found: {_CAPABILITY_EVIDENCE_PATH}"
    )

    text = _CAPABILITY_EVIDENCE_PATH.read_text(encoding="utf-8")

    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| #") or line.startswith("|---"):
            continue
        cols = [c.strip() for c in line.split("|")]
        cols = [c for c in cols if c]
        if len(cols) >= 4:
            capability = cols[0]
            node_id = cols[3]
            if capability == "#":
                continue
            rows.append((capability, node_id))

    assert len(rows) == 10, f"Expected 10 capability rows, got {len(rows)}"

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    collected_lines = result.stdout.strip().splitlines()
    collected_ids = set()
    for line in collected_lines:
        line = line.strip()
        if "::" in line and line.startswith("tests/"):
            collected_ids.add(line)

    for capability, node_id in rows:
        short_name = node_id.split("::")[0].split("/")[-1].replace("test_", "").replace(".py", "")
        assert node_id in collected_ids, f"capability row missing: {short_name}"


# ---------------------------------------------------------------------------
# Unique primitive definitions (todo 52 §e gate test; single-root station
# hard-switched by todo 23)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Single root (todo 23 hard switch) — the ONLY implementation station:
# ``src/cccp/calculation/tasks``.  ``src/acp/calculations/primitives`` is a
# pure compat-shim station (forwarders / re-export aliases); any
# non-shim body of a primitive name there is a second implementation and
# fails.  The dual-root tolerance of todo 16 is gone.
_PRIMITIVES_DIR: Path = _REPO_ROOT / "src" / "cccp" / "calculation" / "tasks"
_CCCP_CALCULATION_DIR: Path = _PRIMITIVES_DIR.parent

_PRIMITIVE_DEFS: dict[str, str] = {
    "run_singlepoint": "singlepoint.py",
    "run_optimize": "optimize.py",
    "run_frequency": "frequency.py",
    "run_scan": "scan.py",
    "run_irc": "irc.py",
    "run_casscf": "casscf.py",
    "run_thermochemistry": "thermochemistry.py",
    # todo 26 (nmr-gap): P2 execution landed (migration todo 43) — register the
    # task core so the unique-impl + ledger cross-checks cover it.
    "run_nmr_shielding": "nmr_shielding.py",
}


def _is_compat_shim(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    """Return ``True`` when *node* is a pure delegation/placeholder body.

    Compat-shim shapes (migration-period acp side after the body moves to
    ``cccp.calculation``):

    * function: docstring + a single ``return <call | attribute | subscript>``
      (pure forwarder), or an empty body;
    * class: docstring + ``pass`` / ``...`` / empty body (alias shell).

    Such bodies carry no implementation logic and must not count as the
    unique implementation body.
    """
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if not body:
        return True
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(node, ast.ClassDef):
        return isinstance(stmt, ast.Pass) or (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is ...
        )
    if isinstance(stmt, ast.Return):
        return isinstance(stmt.value, (ast.Call, ast.Attribute, ast.Subscript))
    return False


def _scan_def_sites(root: Path, names: set[str]) -> dict[str, tuple[list[str], list[str]]]:
    """Map each *name* to ``(implementation_sites, shim_sites)`` under *root*.

    Sites are repo-relative when possible; a missing root counts as empty.
    """
    found: dict[str, tuple[list[str], list[str]]] = {name: ([], []) for name in names}
    if not root.is_dir():
        return found
    for py_file in sorted(root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name not in names:
                continue
            try:
                site = str(py_file.relative_to(_REPO_ROOT))
            except ValueError:
                site = str(py_file)
            impl_sites, shim_sites = found[node.name]
            (shim_sites if _is_compat_shim(node) else impl_sites).append(site)
    return found


def _unique_impl_sites() -> dict[str, str]:
    """Return ``{name: site}`` — the single implementation site per primitive.

    Raises AssertionError unless exactly one implementation body exists per
    primitive definition within the single root ``src/cccp/calculation/tasks``
    (pure compat shims excluded).
    """
    names = set(_PRIMITIVE_DEFS)
    found = _scan_def_sites(_PRIMITIVES_DIR, names)
    unique: dict[str, str] = {}
    for name, (impl_sites, shim_sites) in found.items():
        assert len(impl_sites) == 1, (
            f"{name} has {len(impl_sites)} implementation bodies {impl_sites} "
            f"(shims: {shim_sites}); expected exactly 1 in "
            f"{_PRIMITIVES_DIR.relative_to(_REPO_ROOT)}"
        )
        unique[name] = impl_sites[0]
    return unique


def test_unique_primitive_definitions() -> None:
    """Each calculation primitive has exactly ONE implementation body in the
    single root ``src/cccp/calculation/tasks`` (todo 23 hard switch).

    Pure compat shims never count as implementation bodies; import re-exports
    and module-level alias assignments are not definitions at all.
    """
    unique = _unique_impl_sites()
    for name, expected_file in _PRIMITIVE_DEFS.items():
        expected = Path("src/cccp/calculation/tasks") / expected_file
        assert unique[name] == str(expected), (
            f"{name} implemented in {unique[name]}; expected {expected}"
        )
    # cccp-side stray bodies: nothing outside the pinned station root.
    station_rel = str(_PRIMITIVES_DIR.relative_to(_REPO_ROOT))
    for name, (impl_sites, _shim_sites) in _scan_def_sites(
        _CCCP_CALCULATION_DIR, set(_PRIMITIVE_DEFS)
    ).items():
        for site in impl_sites:
            assert site == station_rel or site.startswith(station_rel + "/"), (
                f"{name}: implementation body at {site} outside the pinned "
                f"station root {station_rel}"
            )


# ---------------------------------------------------------------------------
# No second implementation body anywhere else in ACP (todo 16 — prevents
# compat shims from making the uniqueness guard vacuous)
# ---------------------------------------------------------------------------

# Workflow-layer entry wrappers (plan: wrappers are entry points, NOT
# primitive implementations — the same narrow exclusion the ``unique_run_scan``
# / ``unique_run_irc`` gates use).  Anywhere else in ``src/acp`` a non-shim
# body under a primitive name is a second implementation and must fail.
_ACP_ENTRY_WRAPPER_SITES: dict[str, frozenset[str]] = {
    "run_singlepoint": frozenset({"src/acp/workflows/simple.py"}),
    "run_optimize": frozenset({"src/acp/workflows/simple.py"}),
    "run_frequency": frozenset({"src/acp/workflows/simple.py"}),
    "run_scan": frozenset({"src/acp/workflows/simple.py"}),
    "run_casscf": frozenset({"src/acp/workflows/simple.py"}),
}


def _acp_second_implementation_issues(
    acp_root: Path, unique_impl: dict[str, str | None]
) -> list[str]:
    """Violations of "no second primitive implementation body in any ACP module".

    A non-shim definition of a primitive name may only sit at the unique
    implementation site (single root) or at a sanctioned workflow entry wrapper.
    """
    issues: list[str] = []
    for name, (impl_sites, _shim_sites) in sorted(
        _scan_def_sites(acp_root, set(_PRIMITIVE_DEFS)).items()
    ):
        allowed = set(_ACP_ENTRY_WRAPPER_SITES.get(name, frozenset()))
        unique_site = unique_impl.get(name)
        if unique_site is not None:
            allowed.add(unique_site)
        for site in impl_sites:
            if site not in allowed:
                issues.append(
                    f"{name}: second implementation body at {site} (unique station: {unique_site})"
                )
    return issues


def test_no_second_primitive_implementation_in_acp() -> None:
    """No ACP module outside the single-root station may carry a second
    ``run_*`` implementation body (todo 16 guard, todo 23 single root)."""
    unique = _unique_impl_sites()
    issues = _acp_second_implementation_issues(_REPO_ROOT / "src" / "acp", unique)
    assert not issues, "ACP side carries second implementation bodies:\n" + "\n".join(issues)
    wrapper_src = (_REPO_ROOT / "src" / "acp" / "workflows" / "simple.py").read_text(
        encoding="utf-8"
    )
    assert "get_backend(" not in wrapper_src and "require_backend(" not in wrapper_src, (
        "workflow entry wrappers must not execute QC themselves"
    )
    for node in ast.walk(ast.parse(wrapper_src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name in _ACP_ENTRY_WRAPPER_SITES
        ):
            assert any(isinstance(child, ast.Call) for child in ast.walk(node)), (
                f"entry wrapper {node.name} does not delegate"
            )


def test_acp_second_implementation_guard_has_teeth(tmp_path: Path) -> None:
    """Negative injection: a disguised second body must be reported while a
    pure forwarder shim is not counted (proves the guard cannot go vacuous)."""
    (tmp_path / "sneaky.py").write_text(
        "def run_scan(request):\n    prepared = _prepare(request)\n    return _execute(prepared)\n",
        encoding="utf-8",
    )
    (tmp_path / "compat.py").write_text(
        '"""Compat forwarder."""\n\n\ndef run_scan(request):\n    return _delegate(request)\n',
        encoding="utf-8",
    )
    issues = _acp_second_implementation_issues(
        tmp_path, {"run_scan": "src/acp/calculations/primitives/scan.py"}
    )
    assert any("sneaky.py" in issue for issue in issues), (
        "guard failed to detect a disguised second implementation body"
    )
    assert not any("compat.py" in issue for issue in issues), (
        "pure forwarder shim must not count as an implementation body"
    )
    disguised = ast.parse("def run_scan(x):\n    return _f(x)\n    y = 1\n").body[0]
    assert not _is_compat_shim(disguised), "shim detection must reject bodies carrying extra logic"


# ---------------------------------------------------------------------------
# Current-station ledger cross-check (todo 16): dual-station wording is
# accepted per capability against
# tests/baseline/refactor-evidence/migration_ledger.md
# ---------------------------------------------------------------------------

_LEDGER_PATH = Path(__file__).parent / "baseline" / "refactor-evidence" / "migration_ledger.md"
_LEDGER_COLUMNS = ("能力", "当前实现驻点", "兼容入口", "生产消费者", "退出条件", "验收测试")
_LEDGER_PATH_RE = re.compile(r"src/[A-Za-z0-9_./-]+\.py")


def _ledger_rows() -> list[tuple[str, ...]]:
    """Parse the six-column current-station ledger table."""
    rows: list[tuple[str, ...]] = []
    in_table = False
    for line in _LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if in_table:
                break
            continue
        cells = tuple(cell.strip() for cell in stripped.strip("|").split("|"))
        if not in_table:
            if len(cells) >= 2 and cells[0] == _LEDGER_COLUMNS[0]:
                in_table = True
            continue
        if all(cell and set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def test_migration_ledger_rows_have_six_columns() -> None:
    """Every current-station ledger row carries exactly the six columns
    能力/当前实现驻点/兼容入口/生产消费者/退出条件/验收测试 (todo 16)."""
    rows = _ledger_rows()
    assert rows, f"no ledger rows parsed from {_LEDGER_PATH}"
    for cells in rows:
        assert len(cells) == len(_LEDGER_COLUMNS), (
            f"ledger row has {len(cells)} columns (expected 6): {cells}"
        )


def test_current_station_ledger_matches_implementation_sites() -> None:
    """Per-capability ledger acceptance (todo 16/23): the ledger's
    当前实现驻点 column must equal the unique implementation body discovered
    by the single-root scan — update the ledger row when a body moves."""
    unique = _unique_impl_sites()
    stations: dict[str, str] = {}
    for cells in _ledger_rows():
        match = _LEDGER_PATH_RE.search(cells[1])
        if match:
            stations[cells[0].strip().strip("`")] = match.group(0)
    for name, site in unique.items():
        assert name in stations, f"{name} missing from the current-station ledger"
        assert stations[name] == site, (
            f"{name}: ledger station {stations[name]} != implementation site {site}"
        )
