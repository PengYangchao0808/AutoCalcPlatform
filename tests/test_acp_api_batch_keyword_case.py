"""Regression tests: BatchOptimize keyword canonicalization at job creation.

The Workbench submits a BatchOptimize ``method`` carrying both a flat mirror
(``method[field]``) and a nested ``method["levels"]["batch"]`` copy. Job
creation must rewrite enumerated keyword values in BOTH places to the exact
catalog spellings declared by ``FIELD_DEFINITIONS``, leaving free-form values
(method names, unknown spellings) untouched.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.api.v1_routes import _canonicalize_batch_keywords
from acp.catalog import FIELD_DEFINITIONS
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with make_client(tmp_path, monkeypatch) as test_client:
        yield test_client


@pytest.fixture()
def qc_capable_local(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a server with all QC binaries installed (hermetic API tests)."""
    import acp.scheduler.capabilities as capabilities_module
    import acp.scheduler.manager as manager_module

    monkeypatch.setattr(capabilities_module, "local_satisfies", lambda required: True)
    monkeypatch.setattr(manager_module, "local_satisfies", lambda required: True)


def _catalog_spelling(field: str, folded: str) -> str:
    """Return the exact ``FIELD_DEFINITIONS`` spelling matching *folded*."""
    options = FIELD_DEFINITIONS[field]["options"]
    for option in options:
        if option.lower() == folded.lower():
            return option
    raise AssertionError(f"{folded!r} not among FIELD_DEFINITIONS[{field!r}] options {options}")


# ---------------------------------------------------------------------------
# Unit tests — helper directly
# ---------------------------------------------------------------------------


def test_canonicalize_batch_keywords_flat_and_nested_to_catalog_spellings() -> None:
    """Given mixed-case flat + nested values, When canonicalized, Then both
    mirrors carry the exact catalog spellings and free-form fields survive."""
    method: dict[str, object] = {
        "opt_convergence": "tight",
        "scf_convergence": "verytight",
        "optimization_method": "wB97X-D4",
        "levels": {
            "batch": {
                "opt_convergence": "Tight",
                "scf_strategy": "SOSCF",
            }
        },
    }

    _canonicalize_batch_keywords(method)  # type: ignore[arg-type]

    assert method["opt_convergence"] == _catalog_spelling("opt_convergence", "tight")
    assert method["scf_convergence"] == _catalog_spelling("scf_convergence", "verytight")
    assert method["optimization_method"] == "wB97X-D4"
    batch = method["levels"]["batch"]  # type: ignore[index]
    assert batch["opt_convergence"] == _catalog_spelling("opt_convergence", "tight")
    assert batch["scf_strategy"] == _catalog_spelling("scf_strategy", "soscf")


def test_canonicalize_batch_keywords_leaves_none_empty_and_unknown_untouched() -> None:
    """Given None/empty/unknown spellings, When canonicalized, Then they pass
    through verbatim."""
    method: dict[str, object] = {
        "opt_convergence": None,
        "scf_convergence": "",
        "opt_rescue_policy": "SOMEUnknownPolicy",
        "levels": {
            "batch": {
                "opt_initial_hessian": None,
                "minimum_opt_initial_hessian": "",
                "scf_strategy": "not-a-strategy",
            }
        },
    }

    _canonicalize_batch_keywords(method)  # type: ignore[arg-type]

    assert method["opt_convergence"] is None
    assert method["scf_convergence"] == ""
    assert method["opt_rescue_policy"] == "SOMEUnknownPolicy"
    batch = method["levels"]["batch"]  # type: ignore[index]
    assert batch["opt_initial_hessian"] is None
    assert batch["minimum_opt_initial_hessian"] == ""
    assert batch["scf_strategy"] == "not-a-strategy"


def test_canonicalize_batch_keywords_canonical_values_are_stable() -> None:
    """Given already-canonical values, When canonicalized, Then they are
    byte-stable (idempotent round trip)."""
    canonical = {
        field: FIELD_DEFINITIONS[field]["options"][0]
        for field in (
            "opt_convergence",
            "scf_convergence",
            "opt_initial_hessian",
            "minimum_opt_initial_hessian",
            "transition_state_opt_initial_hessian",
            "opt_rescue_policy",
            "scf_strategy",
        )
    }
    method: dict[str, object] = {**canonical, "levels": {"batch": dict(canonical)}}

    _canonicalize_batch_keywords(method)  # type: ignore[arg-type]

    for field, value in canonical.items():
        assert method[field] == value
        assert method["levels"]["batch"][field] == value  # type: ignore[index]


def test_canonicalize_batch_keywords_without_nested_batch() -> None:
    """Given a missing / malformed ``levels.batch``, When canonicalized, Then
    only the flat mirror is rewritten and no structure is created."""
    flat_only: dict[str, object] = {"opt_convergence": "VERYTIGHT"}
    _canonicalize_batch_keywords(flat_only)  # type: ignore[arg-type]
    assert flat_only["opt_convergence"] == _catalog_spelling("opt_convergence", "verytight")
    assert "levels" not in flat_only

    levels_none: dict[str, object] = {"scf_strategy": "SlowConv", "levels": None}
    _canonicalize_batch_keywords(levels_none)  # type: ignore[arg-type]
    assert levels_none["scf_strategy"] == _catalog_spelling("scf_strategy", "slowconv")

    batch_missing: dict[str, object] = {"scf_strategy": "SOSCF", "levels": {"sp": {}}}
    _canonicalize_batch_keywords(batch_missing)  # type: ignore[arg-type]
    assert batch_missing["scf_strategy"] == _catalog_spelling("scf_strategy", "soscf")


def test_canonicalize_batch_keywords_touches_only_flat_and_batch_levels() -> None:
    """Given sibling levels beside ``batch``, When canonicalized, Then those
    levels are left untouched (scope is flat mirror + levels.batch)."""
    method: dict[str, object] = {
        "opt_convergence": "loose",
        "levels": {
            "batch": {"opt_convergence": "LOOSE"},
            "sp": {"opt_convergence": "VERYTIGHT"},
        },
    }

    _canonicalize_batch_keywords(method)  # type: ignore[arg-type]

    assert method["opt_convergence"] == _catalog_spelling("opt_convergence", "loose")
    batch = method["levels"]["batch"]  # type: ignore[index]
    assert batch["opt_convergence"] == _catalog_spelling("opt_convergence", "loose")
    sp = method["levels"]["sp"]  # type: ignore[index]
    assert sp["opt_convergence"] == "VERYTIGHT"


# ---------------------------------------------------------------------------
# T24 — /validate-method surfaces migration/canonicalization warnings
# ---------------------------------------------------------------------------


def test_validate_method_migrated_grid_config_returns_non_empty_warnings(
    client: TestClient,
) -> None:
    """Given a legacy Gaussian-era grid alias (UltraFine), When validated,
    Then the response is valid AND carries a non-empty warnings list naming
    the canonical token (DefGrid3) — the field previously existed but was
    never populated."""
    response = client.post(
        "/api/v1/validate-method",
        json={
            "schema_id": "dft_optimize",
            "levels": {
                "optimize": {
                    "engine": "orca",
                    "functional": "wB97X-D4",
                    "basis": "def2-TZVP",
                    "grid": "UltraFine",
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert body["errors"] == []
    assert body["warnings"], "migrated legacy config must produce warnings"
    assert any("UltraFine" in w and "DefGrid3" in w for w in body["warnings"])
    # The migrated value lands in normalized_levels so the wizard can adopt it.
    assert body["normalized_levels"]["optimize"]["grid"] == "DefGrid3"


def test_validate_method_clean_config_returns_empty_warnings(client: TestClient) -> None:
    """Given an already-canonical config, When validated, Then warnings is
    exactly [] (no false-positive migration noise)."""
    response = client.post(
        "/api/v1/validate-method",
        json={
            "schema_id": "dft_optimize",
            "levels": {
                "optimize": {
                    "engine": "orca",
                    "functional": "wB97X-D4",
                    "basis": "def2-TZVP",
                    "grid": "DefGrid3",
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert body["warnings"] == []


def test_validate_method_method_alias_and_case_canonicalization_warn(
    client: TestClient,
) -> None:
    """Given a method alias (b973c) on an alias-flagged field and a
    case-folded enum value (tight), When validated, Then each
    canonicalization is surfaced as its own warning."""
    response = client.post(
        "/api/v1/validate-method",
        json={
            "schema_id": "pes_scan",
            "levels": {
                "scan_optimizer": {
                    "engine": "orca",
                    "scan_optimizer_method": "b973c",
                    "scan_optimizer_basis": "def2-mTZVP",
                    "scan_optimizer_convergence": "Tight",
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert any(
        "b973c" in w and "B97-3c" in w for w in body["warnings"]
    ), f"method alias warning missing: {body['warnings']}"
    assert any(
        "canonicalized to 'tight'" in w and "Tight" in w for w in body["warnings"]
    ), f"case canonicalization warning missing: {body['warnings']}"


def test_validate_method_unknown_schema_still_has_empty_warnings(
    client: TestClient,
) -> None:
    """Given an unknown schema_id, When validated, Then the response stays
    valid=False with empty warnings (no migration ran)."""
    response = client.post(
        "/api/v1/validate-method",
        json={"schema_id": "nope", "levels": {}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is False
    assert body["warnings"] == []


# ---------------------------------------------------------------------------
# Wiring tests — create_job applies the helper for BatchOptimize only
# ---------------------------------------------------------------------------


def _seed_completed_pes_job(client: TestClient, tmp_path: Path) -> None:
    manager = client.app.state.job_manager
    source_dir = tmp_path / "batch-kw-pes-source"
    source_dir.mkdir(parents=True, exist_ok=True)
    manager.store.create(
        JobRecord(
            id="batch-kw-pes-src",
            spec=JobSpec(
                workflow="PESsearch",
                name="batch-kw-pes-src",
                project_id=manager.default_project_id,
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(source_dir),
            project_id=manager.default_project_id,
        )
    )


def test_create_job_canonicalizes_batch_optimize_keywords(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qc_capable_local: None
) -> None:
    """Given a BatchOptimize submission with mixed-case keywords, When the job
    is created, Then the stored spec carries catalog spellings in BOTH the
    flat mirror and the nested levels.batch copy."""
    _seed_completed_pes_job(client, tmp_path)
    manager = client.app.state.job_manager
    captured: dict[str, object] = {}
    original = manager.submit

    def _submit(spec, group_id=None):
        captured["spec"] = spec
        work_dir = tmp_path / "captured-batch-kw"
        work_dir.mkdir(parents=True, exist_ok=True)
        return JobRecord(
            id="captured-batch-kw-1",
            spec=spec,
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
            project_id=spec.project_id,
        )

    manager.submit = _submit  # type: ignore[method-assign]
    try:
        response = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "BatchOptimize",
                "name": "batch-kw-case",
                "input": {
                    "source_type": "stage_artifact",
                    "source_job_id": "batch-kw-pes-src",
                    "from_artifact": "RESULT/result_manifest.json",
                },
                "method": {
                    "opt_convergence": "tight",
                    "scf_strategy": "SOSCF",
                    "levels": {
                        "batch": {
                            "opt_convergence": "TIGHT",
                            "scf_strategy": "soscf",
                        }
                    },
                },
            },
        )
    finally:
        manager.submit = original  # type: ignore[method-assign]

    assert response.status_code == 201, response.text
    spec = captured["spec"]
    assert spec is not None
    assert spec.method["opt_convergence"] == _catalog_spelling("opt_convergence", "tight")
    assert spec.method["scf_strategy"] == _catalog_spelling("scf_strategy", "soscf")
    nested = spec.method["levels"]["batch"]
    assert nested["opt_convergence"] == _catalog_spelling("opt_convergence", "tight")
    assert nested["scf_strategy"] == _catalog_spelling("scf_strategy", "soscf")


def test_create_job_leaves_non_batchoptimize_keywords_untouched(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a non-BatchOptimize workflow with the same keys, When the job is
    created, Then method values pass through verbatim (scope is BatchOptimize
    only)."""
    manager = client.app.state.job_manager
    captured: dict[str, object] = {}
    original = manager.submit

    def _submit(spec, group_id=None):
        captured["spec"] = spec
        work_dir = tmp_path / "captured-fake-kw"
        work_dir.mkdir(parents=True, exist_ok=True)
        return JobRecord(
            id="captured-fake-kw-1",
            spec=spec,
            status=JobStatus.QUEUED,
            work_dir=str(work_dir),
            project_id=spec.project_id,
        )

    manager.submit = _submit  # type: ignore[method-assign]
    try:
        response = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "fake",
                "name": "fake-kw-case",
                "input": {"source": "CCO"},
                "method": {
                    "opt_convergence": "tight",
                    "scf_strategy": "SOSCF",
                    "levels": {"batch": {"opt_convergence": "TIGHT"}},
                },
            },
        )
    finally:
        manager.submit = original  # type: ignore[method-assign]

    assert response.status_code == 201, response.text
    spec = captured["spec"]
    assert spec is not None
    assert spec.method["opt_convergence"] == "tight"
    assert spec.method["scf_strategy"] == "SOSCF"
    assert spec.method["levels"]["batch"]["opt_convergence"] == "TIGHT"
