from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from acp.calculations.batch import engine as batch_engine
from acp.calculations.batch.models import BatchStructureItem, JsonObject, load_batch_request
from acp.catalog import METHOD_SCHEMAS, WORKFLOW_CATALOG
from acp.scheduler.jobs import SUPPORTED_WORKFLOWS
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
    """CalculationPlanExecutor, BatchOptimizeEngine, and scan/IRC primitives all
    write result_manifest.json via ResultManifest."""
    import acp.calculations.executor as executor_mod
    import acp.calculations.primitives.irc as irc_mod
    import acp.calculations.primitives.scan as scan_mod

    for module in (executor_mod, scan_mod, irc_mod):
        source = inspect.getsource(module)
        assert "ResultManifest" in source, f"{module.__name__} does not use ResultManifest"


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
# Unique primitive definitions (todo 52 §e gate test; migration-period
# dual-station semantics for the acp→cccp architecture remediation)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Current station (Wave 0): real implementation bodies live here; after the
# migration these modules become pure compat shims delegating to cccp.
_PRIMITIVES_DIR = _REPO_ROOT / "src" / "acp" / "calculations" / "primitives"

# New station: cccp task-layer implementation root.  Tolerated absent at
# Wave 0 (non-existent root counts as empty).  Do NOT hard-point at a
# specific module under it (e.g. ``tasks``) until todos 16/23 pin the
# station — doing so would falsely red the guard before the migration.
_CCCP_CALCULATION_DIR = _REPO_ROOT / "src" / "cccp" / "calculation"

_PRIMITIVE_DEFS: dict[str, str] = {
    "run_singlepoint": "singlepoint.py",
    "run_optimize": "optimize.py",
    "run_frequency": "frequency.py",
    "run_scan": "scan.py",
    "run_irc": "irc.py",
    "ThermochemistryCalculator": "thermochemistry.py",
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


def test_unique_primitive_definitions() -> None:
    """Each calculation primitive must have exactly ONE implementation body
    across BOTH migration-period stations (dual-station semantics):

    * ``src/acp/calculations/primitives`` — current station (Wave 0: the
      real bodies; after migration: compat shims that delegate);
    * ``src/cccp/calculation`` — new station (may not exist yet at Wave 0;
      a non-existent root counts as empty).

    Definitions whose body is a pure compat shim (see :func:`_is_compat_shim`)
    do not count as implementation bodies; import re-exports are not
    definitions at all (aliases in ``workflows/simple.py`` or elsewhere must
    NOT count).  While the single implementation body still lives in the acp
    root it must stay in its expected module; once it moves under
    ``src/cccp/calculation`` any module there is accepted (the exact module
    is pinned by todos 16/23, not here).
    """
    roots = [path for path in (_PRIMITIVES_DIR, _CCCP_CALCULATION_DIR) if path.is_dir()]
    for name, expected_file in _PRIMITIVE_DEFS.items():
        implementation_sites: list[str] = []
        shim_sites: list[str] = []
        for root in roots:
            for py_file in sorted(root.rglob("*.py")):
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(py_file))
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue
                    if node.name != name:
                        continue
                    site = str(py_file.relative_to(_REPO_ROOT))
                    if _is_compat_shim(node):
                        shim_sites.append(site)
                    else:
                        implementation_sites.append(site)
        assert len(implementation_sites) == 1, (
            f"{name} has {len(implementation_sites)} implementation bodies "
            f"{implementation_sites} (shims: {shim_sites}); expected exactly 1 across "
            f"{[str(r.relative_to(_REPO_ROOT)) for r in roots]}"
        )
        impl_path = Path(implementation_sites[0])
        if impl_path.parent.name == "primitives":
            assert impl_path.name == expected_file, (
                f"{name} implemented in {implementation_sites[0]}; expected {expected_file}"
            )
        else:
            assert impl_path.parts[:3] == ("src", "cccp", "calculation"), (
                f"{name} implemented outside both stations: {implementation_sites[0]}"
            )
