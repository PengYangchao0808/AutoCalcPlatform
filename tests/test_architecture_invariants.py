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
    "xtb_optimize",
    "nmr",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
)
TARGET_ACTIVE_IDS = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "xtb_optimize",
    "Confsearch",
    "PESsearch",
    "BatchOptimize",
    "nmr",
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
    assert len(active_ids) == 10
    assert set(active_ids) == {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "xtb_optimize",
        "nmr",
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
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

    assert len(active_ids) == 10
    assert set(active_ids) == {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "xtb_optimize",
        "Confsearch",
        "PESsearch",
        "BatchOptimize",
        "nmr",
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


def test_legacy_api_mechanism_returns_410() -> None:
    """Mechanism mutation endpoints must return 410 Gone (read-only)."""
    from fastapi.testclient import TestClient

    from acp.api.server import create_app

    client = TestClient(create_app())
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
        assert node_id in collected_ids, (
            f"capability row missing: {node_id.split('::')[0].split('/')[-1].replace('test_', '').replace('.py', '')}"
        )


# ---------------------------------------------------------------------------
# Unique primitive definitions (todo 52 §e gate test)
# ---------------------------------------------------------------------------

_PRIMITIVES_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "acp" / "calculations" / "primitives"
)

_PRIMITIVE_DEFS: dict[str, str] = {
    "run_singlepoint": "singlepoint.py",
    "run_optimize": "optimize.py",
    "run_frequency": "frequency.py",
    "run_scan": "scan.py",
    "run_irc": "irc.py",
    "ThermochemistryCalculator": "thermochemistry.py",
}


def test_unique_primitive_definitions() -> None:
    """Each calculation primitive must be defined in exactly one module under
    ``calculations/primitives/``.  Import aliases in ``workflows/simple.py``
    or elsewhere must NOT count as definitions."""
    for name, expected_file in _PRIMITIVE_DEFS.items():
        definition_sites: list[str] = []
        for py_file in _PRIMITIVES_DIR.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == name:
                        definition_sites.append(py_file.name)
        assert len(definition_sites) == 1, (
            f"{name} defined in {definition_sites}; expected exactly 1 in {expected_file}"
        )
        assert definition_sites[0] == expected_file, (
            f"{name} defined in {definition_sites[0]}; expected {expected_file}"
        )
