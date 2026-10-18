"""Migration equivalence + isolation suite for B05-7 (todo 77).

Guards the in-package migration of the store/parameter-conversion closures:
the migrated ``acp_services`` implementations are behaviourally equivalent to
the pre-migration legacy behaviour, the old ``acp`` paths are same-object
forwarders, and ``src/acp_services`` stays free of ``acp`` imports.

Expected values here are **captured literals** (from the documented CAS /
persistence semantics and the legacy flag-emission behaviour), not re-derived
from the new code. All database work uses an isolated ``tmp_path`` DB — the
"no jobs DB" constraint applies only to the worker-independence probe, not to
the store/controller equivalence checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_services_injection_contracts import scan_for_acp_imports

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SERVICES_ROOT = _REPO_ROOT / "src" / "acp_services"


# ---------------------------------------------------------------------------
# (a) module isolation: migrated Services modules import no ``acp``
# ---------------------------------------------------------------------------


def _migrated_modules() -> list[Path]:
    store_pkg = _SERVICES_ROOT / "store"
    files = sorted(p for p in store_pkg.rglob("*.py") if "__pycache__" not in p.parts)
    runtime = _SERVICES_ROOT / "runtime"
    files.append(runtime / "method_flags.py")
    files.append(runtime / "plan_contracts.py")
    files.append(runtime / "step_result.py")
    files.append(runtime / "step_requirements.py")
    return files


def test_migrated_modules_are_acp_free() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _migrated_modules():
        violations = scan_for_acp_imports(path.read_text(encoding="utf-8"))
        if violations:
            offenders[str(path.relative_to(_REPO_ROOT))] = violations
    assert not offenders, f"migrated Services modules reference acp: {offenders}"


def test_migrated_modules_exist() -> None:
    for path in _migrated_modules():
        assert path.is_file(), path


# ---------------------------------------------------------------------------
# (b) old-path identity: ``acp.X is acp_services.Y``
# ---------------------------------------------------------------------------


def test_store_symbols_are_same_objects() -> None:
    import acp.scheduler.migrations as legacy_migrations
    import acp.scheduler.molecule_groups as legacy_groups
    import acp.scheduler.naming as legacy_naming
    import acp.scheduler.store as legacy_store
    import acp.storage.layout as legacy_layout
    import acp.storage.record as legacy_record
    import acp_services.store.layout as moved_layout
    import acp_services.store.migrations as moved_migrations
    import acp_services.store.molecule_groups as moved_groups
    import acp_services.store.naming as moved_naming
    import acp_services.store.record as moved_record
    import acp_services.store.store as moved_store

    assert legacy_store.JobStore is moved_store.JobStore
    assert legacy_store.JobStateConflictError is moved_store.JobStateConflictError
    assert legacy_migrations.migrate is moved_migrations.migrate
    assert legacy_layout.TaskStorage is moved_layout.TaskStorage
    assert legacy_layout.runtime_file is moved_layout.runtime_file
    assert legacy_record.TaskRecord is moved_record.TaskRecord
    assert legacy_naming.molecule_group_key is moved_naming.molecule_group_key
    assert legacy_groups.resolve_molecule_key is moved_groups.resolve_molecule_key


def test_record_types_are_same_objects() -> None:
    import acp.scheduler.jobs as legacy_jobs
    import acp.scheduler.nodes as legacy_nodes
    import acp_services.store.records as moved

    assert legacy_jobs.JobStatus is moved.JobStatus
    assert legacy_jobs.JobSpec is moved.JobSpec
    assert legacy_jobs.JobRecord is moved.JobRecord
    assert legacy_nodes.ExecutionMode == moved.ExecutionMode


def test_method_levels_converter_is_same_object() -> None:
    import acp.catalog as legacy_catalog
    import acp_services.runtime.method_flags as moved

    assert legacy_catalog.method_levels_to_cli_flags is moved.method_levels_to_cli_flags


def test_legacy_method_normalizer_is_same_object() -> None:
    import acp.catalog as legacy_catalog
    import acp_services.store.legacy_method as moved

    assert legacy_catalog.normalize_legacy_method is moved.normalize_legacy_method


# ---------------------------------------------------------------------------
# (c) parameter-conversion parity (fixed inputs -> captured expected argv)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("levels", "workflow", "expected"),
    [
        (
            {"opt": {"functional": "wB97M-V", "basis": "def2-TZVPP"}},
            None,
            ["--method", "wB97M-V", "--basis", "def2-TZVPP"],
        ),
        (
            {"opt": {"functional": "wb97m-v", "solvent_model": "Water"}},
            "optimize",
            ["--method", "wb97m-v", "--solvent-model", "water"],
        ),
        (
            {"opt": {"recalc_hess": "auto"}},
            "optimize",
            ["--calc-hess", "auto"],
        ),
        (
            {"opt": {"recalc_hess": 0}},
            "optimize",
            ["--no-calc-hess"],
        ),
        (
            {"opt": {"_disabled": True, "functional": "x"}},
            None,
            [],
        ),
    ],
)
def test_method_levels_to_cli_flags_parity(levels, workflow, expected) -> None:
    from acp_services.runtime.method_flags import method_levels_to_cli_flags

    assert method_levels_to_cli_flags(levels, workflow=workflow) == expected


def test_method_levels_rejects_unsupported_simple_field() -> None:
    from acp_services.runtime.method_flags import method_levels_to_cli_flags

    with pytest.raises(ValueError, match="does not support method field"):
        method_levels_to_cli_flags(
            {"opt": {"transition_state_scf_max_iter": 5}}, workflow="optimize"
        )


# ---------------------------------------------------------------------------
# (d) store persistence / CAS equivalence on an isolated DB
# ---------------------------------------------------------------------------


def _new_store(tmp_path: Path):
    from acp_services.store.store import JobStore

    return JobStore(tmp_path / "acp_jobs.db")


def _make_record(job_id: str):
    from acp_services.store.records import JobRecord, JobSpec

    return JobRecord(id=job_id, spec=JobSpec(workflow="singlepoint", name="m1"))


def test_store_persistence_round_trip_isolated_db(tmp_path: Path) -> None:
    store = _new_store(tmp_path)
    store.create(_make_record("j1"))

    loaded = store.get("j1")
    assert loaded is not None
    assert loaded.id == "j1"
    assert loaded.spec.workflow == "singlepoint"
    # Captured defaults from the documented record model.
    assert loaded.status.value == "queued"
    assert loaded.revision == 0
    assert loaded.attempt == 1
    assert loaded.work_dir == ""
    assert store.get("absent") is None


def test_store_cas_transition_equivalence_isolated_db(tmp_path: Path) -> None:
    from acp_services.store.records import JobStatus

    store = _new_store(tmp_path)
    store.create(_make_record("j2"))

    advanced = store.transition(
        "j2",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        current_stage="02_SEARCH",
        status=JobStatus.RUNNING,
    )
    # Captured CAS outcome: revision +1, status advanced, attempt untouched.
    assert advanced.status is JobStatus.RUNNING
    assert advanced.revision == 1
    assert advanced.attempt == 1
    assert advanced.current_stage == "02_SEARCH"


def test_store_cas_rejects_stale_writer_and_preserves_state(tmp_path: Path) -> None:
    from acp_services.store.records import JobStatus
    from acp_services.store.store import JobStateConflictError

    store = _new_store(tmp_path)
    store.create(_make_record("j3"))
    store.transition(
        "j3",
        expected_status=JobStatus.QUEUED,
        expected_revision=0,
        status=JobStatus.RUNNING,
    )

    with pytest.raises(JobStateConflictError):
        store.transition(
            "j3",
            expected_status=JobStatus.QUEUED,
            expected_revision=0,
            status=JobStatus.COMPLETED,
        )

    unchanged = store.get("j3")
    assert unchanged is not None
    assert unchanged.status is JobStatus.RUNNING
    assert unchanged.revision == 1


def test_store_schema_is_created_by_native_migrator(tmp_path: Path) -> None:
    import sqlite3

    from acp_services.store.migrations import get_schema_version, migrate

    db_path = tmp_path / "acp_jobs.db"
    applied = migrate(db_path)
    assert applied >= 0
    assert db_path.is_file()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        assert get_schema_version(conn) > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (e) legacy-method normalization equivalence (captured expected values)
# ---------------------------------------------------------------------------


def test_normalize_legacy_method_scan_optimizer_engine(tmp_path: Path) -> None:
    from acp_services.store.legacy_method import normalize_legacy_method

    normalized = normalize_legacy_method(
        {"levels": {"scan_optimizer": {"engine": "xtb"}, "opt": {}}}
    )
    assert normalized["levels"]["scan_optimizer"]["engine"] == "orca"


# ---------------------------------------------------------------------------
# (f) plan/step-requirements closure: identity + parity (fixed inputs)
# ---------------------------------------------------------------------------


def test_plan_closure_symbols_are_same_objects() -> None:
    import acp.calculations.contracts as legacy_contracts
    import acp.calculations.step_requirements as legacy_requirements
    import acp.calculations.step_result as legacy_step_result
    import acp_services.runtime.plan_contracts as moved_contracts
    import acp_services.runtime.step_requirements as moved_requirements
    import acp_services.runtime.step_result as moved_step_result

    for name in ("StepKind", "CalculationResult", "CalculationPlan", "Provenance", "validate_plan"):
        assert getattr(legacy_contracts, name) is getattr(moved_contracts, name), name
    assert legacy_contracts.JsonValue is moved_contracts.JsonValue
    assert legacy_requirements.evaluate_prerequisite is moved_requirements.evaluate_prerequisite
    assert legacy_step_result.write_step_result is moved_step_result.write_step_result
    assert legacy_step_result.read_step_result is moved_step_result.read_step_result


def _prior(kind, status, result=None):
    from acp_services.runtime.step_requirements import PriorStep

    return PriorStep(kind=kind, status=status, result=result)


def test_evaluate_prerequisite_parity() -> None:
    from acp_services.runtime.plan_contracts import CalculationResult, StepKind
    from acp_services.runtime.step_requirements import (
        MISSING_REQUIREMENT,
        UPSTREAM_FAILED,
        evaluate_prerequisite,
    )

    # No upstream coord-producing step -> geometry prerequisite satisfied.
    assert evaluate_prerequisite([], StepKind.SINGLEPOINT, ["O", "H", "H"]).satisfied is True
    # Upstream optimize failed -> upstream_failed.
    failed = evaluate_prerequisite([_prior(StepKind.OPTIMIZE, "failed")], StepKind.SINGLEPOINT, [])
    assert failed.satisfied is False
    assert failed.reason == UPSTREAM_FAILED
    # Upstream optimize completed without coords -> missing_requirement.
    no_coords = evaluate_prerequisite(
        [_prior(StepKind.OPTIMIZE, "completed", CalculationResult())], StepKind.SINGLEPOINT, []
    )
    assert no_coords.satisfied is False
    assert no_coords.reason == MISSING_REQUIREMENT
    # Coord count mismatch against item symbols -> missing_requirement.
    mismatch = evaluate_prerequisite(
        [_prior(StepKind.OPTIMIZE, "completed", CalculationResult(coords=[[0.0, 0.0, 0.0]]))],
        StepKind.SINGLEPOINT,
        ["O", "H"],
    )
    assert mismatch.satisfied is False
    assert mismatch.reason == MISSING_REQUIREMENT
    # Matching coord count -> satisfied.
    matched = evaluate_prerequisite(
        [_prior(StepKind.OPTIMIZE, "completed", CalculationResult(coords=[[0.0, 0.0, 0.0]]))],
        StepKind.SINGLEPOINT,
        ["O"],
    )
    assert matched.satisfied is True
    # THERMOCHEMISTRY requires preceding frequency + singlepoint.
    thermo = evaluate_prerequisite([], StepKind.THERMOCHEMISTRY, [])
    assert thermo.satisfied is False
    assert thermo.reason == MISSING_REQUIREMENT
    # A diagnostics-only upstream result never satisfies a normal prerequisite.
    diagnostic = CalculationResult(
        coords=[[0.0, 0.0, 0.0]], metadata={"diagnostic_only": True}
    )
    diag_out = evaluate_prerequisite(
        [_prior(StepKind.OPTIMIZE, "completed", diagnostic)], StepKind.SINGLEPOINT, ["O"]
    )
    assert diag_out.satisfied is False
    assert diag_out.reason == MISSING_REQUIREMENT


def test_requirement_outcome_shape_is_stable() -> None:
    from acp_services.runtime.step_requirements import SATISFIED, RequirementOutcome

    assert SATISFIED.satisfied is True
    assert SATISFIED.reason == ""
    outcome = RequirementOutcome(satisfied=False, reason="missing_requirement")
    assert outcome.satisfied is False
    assert outcome.reason == "missing_requirement"


def test_validate_plan_parity() -> None:
    from acp_services.runtime.plan_contracts import (
        CalculationPlan,
        CalculationStep,
        StepKind,
        validate_plan,
    )

    valid = CalculationPlan(
        workflow="optimize",
        items=[{"path": "input.xyz"}],
        steps=[
            CalculationStep(kind=StepKind.SINGLEPOINT),
            CalculationStep(kind=StepKind.FREQUENCY),
        ],
    )
    assert validate_plan(valid) == []

    empty = CalculationPlan(workflow="optimize", items=[], steps=[])
    errors = validate_plan(empty)
    assert errors and "exactly one input item" in errors[0]

    thermo_only = CalculationPlan(
        workflow="optimize",
        items=[{"path": "input.xyz"}],
        steps=[CalculationStep(kind=StepKind.THERMOCHEMISTRY)],
    )
    thermo_errors = validate_plan(thermo_only)
    assert any("thermochemistry requires" in message for message in thermo_errors)


def test_calculation_result_coords_shape_is_stable() -> None:
    from acp_services.runtime.plan_contracts import CalculationResult

    result = CalculationResult(coords=[(0.0, 0.0, 0.0)], frequencies=(1.0, 2.0))
    assert result.coords == [(0.0, 0.0, 0.0)]
    assert result.frequencies == [1.0, 2.0]
    assert result.artifacts == []
    assert result.status == "completed"
