"""Per-conformer GIAO shielding checkpoints: resume, invalidation, budget (todo 28 / gap G15).

Contract under test (``acp.workflows.nmr``):

* every conformer's GIAO result is fingerprinted (geometry hash +
  method/basis/solvent/solvent_model/nuclei/charge/multiplicity +
  atom mapping + theory run-config) and stored in ``checkpoint.json``
  inside the per-candidate GIAO dir;
* a re-run with matching fingerprints skips the computed conformers and
  computes only the missing ones (interrupt or deleted entry);
* ANY method-level change invalidates the whole file; a single
  conformer's geometry change invalidates only that entry;
* the ``候选×构象×nproc`` resource budget is explicit, capped, verified,
  and recorded in the analysis result — execution stays sequential.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.nmr.models import NmrConfig
from tests.test_acp_workflows_nmr import _shielding_result

_SYMBOLS = ["C", "H", "H", "H", "H"]


def _conf(x_offset: float, *, charge: int = 0, multiplicity: int = 1) -> Structure:
    """One conformer geometry identified by its first-atom x coordinate."""
    coords = [(x_offset + i * 0.4, 0.1 * i, -0.05 * i) for i in range(len(_SYMBOLS))]
    return Structure(
        id="cand",
        charge=charge,
        multiplicity=multiplicity,
        symbols=list(_SYMBOLS),
        coordinates=np.array(coords, dtype=float),
    )


def _fake_shielding(calls: list[float], interrupt_at: float | None = None) -> Any:
    """Deterministic task core: isotropic keyed to the conformer geometry."""

    def fake(request: Any, *, context: Any = None) -> Any:
        x = float(request.structure.coordinates[0][0])
        calls.append(x)
        if interrupt_at is not None and abs(x - interrupt_at) < 1e-9:
            raise KeyboardInterrupt("simulated interruption before conformer completes")
        value = 100.0 + x
        return _shielding_result(
            {0: {"symbol": "C", "isotropic": value}, 1: {"symbol": "H", "isotropic": value - 70.0}}
        )

    return fake


def _run(
    conformers: list[tuple[Structure, float, float]],
    nmr_config: NmrConfig,
    giao_dir: Path,
    cfg: dict[str, Any] | None = None,
) -> list[Any]:
    from acp.workflows.nmr import _run_giao_for_conformers

    return _run_giao_for_conformers(conformers, nmr_config, giao_dir, cfg or {}, None)


# ---------------------------------------------------------------------------
# (a) resume: interrupt / delete → only the missing conformer is computed
# ---------------------------------------------------------------------------


def test_resume_after_interrupt_computes_only_missing(tmp_path: Path) -> None:
    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.4, 0.1) for x in (0.0, 1.0, 2.0, 3.0)]

    calls1: list[float] = []
    with patch(
        "acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls1, interrupt_at=2.0)
    ):
        with pytest.raises(KeyboardInterrupt):
            _run(conformers, NmrConfig(), giao_dir)

    # interrupted at conf_002: conf_000/conf_001 were computed+checkpointed;
    # conf_002 was attempted but never completed → never cached
    assert calls1 == [0.0, 1.0, 2.0]
    checkpoint_path = giao_dir / "checkpoint.json"
    assert checkpoint_path.is_file(), "no CHECKPOINT written inside the task work dir"
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert stored["identity_schema"] == 2
    assert sorted(stored["items_state"]) == ["conf_000", "conf_001"]
    assert stored["plan_fingerprint"].startswith("v2:")
    entry = stored["items_state"]["conf_000"]
    assert entry["fingerprint"].startswith("v2:")
    assert entry["shieldings"]["0"]["symbol"] == "C"

    # resume: only conf_002 + conf_003 are computed; 000/001 come from cache
    calls2: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls2)):
        results = _run(conformers, NmrConfig(), giao_dir)

    assert calls2 == [2.0, 3.0], f"resume recomputed too much: {calls2}"
    assert [item.conformer_id for item in results] == [
        "conf_000",
        "conf_001",
        "conf_002",
        "conf_003",
    ]
    assert results[0].shieldings[0]["isotropic"] == pytest.approx(100.0)  # reused
    assert results[1].shieldings[0]["isotropic"] == pytest.approx(101.0)  # reused
    assert results[2].shieldings[0]["isotropic"] == pytest.approx(102.0)  # computed
    assert results[3].shieldings[0]["isotropic"] == pytest.approx(103.0)  # computed

    # completed run leaves every conformer checkpointed
    stored2 = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert sorted(stored2["items_state"]) == ["conf_000", "conf_001", "conf_002", "conf_003"]
    assert stored2["resume_count"] >= 1


def test_resume_after_deleted_entry_recomputes_only_that_conformer(tmp_path: Path) -> None:
    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.0, 2.0)]

    calls1: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls1)):
        first = _run(conformers, NmrConfig(), giao_dir)
    assert calls1 == [0.0, 1.0, 2.0]
    assert len(first) == 3

    # simulate a lost conformer result: drop conf_001 from the checkpoint
    checkpoint_path = giao_dir / "checkpoint.json"
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    del stored["items_state"]["conf_001"]
    checkpoint_path.write_text(json.dumps(stored), encoding="utf-8")

    calls2: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls2)):
        second = _run(conformers, NmrConfig(), giao_dir)

    assert calls2 == [1.0], f"expected only conf_001 recomputed, got {calls2}"
    assert [item.conformer_id for item in second] == ["conf_000", "conf_001", "conf_002"]
    assert second[0].shieldings[0]["isotropic"] == pytest.approx(100.0)  # cache
    assert second[1].shieldings[0]["isotropic"] == pytest.approx(101.0)  # recompute
    assert second[2].shieldings[0]["isotropic"] == pytest.approx(102.0)  # cache


def test_failed_conformer_is_not_cached_and_is_retried(tmp_path: Path) -> None:
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import TaskResult

    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.0)]
    failed = TaskResult(
        task=TaskKind.NMR_SHIELDING, status="failed", complete=False, errors=("no convergence",)
    )

    calls1: list[float] = []
    ok = _fake_shielding(calls1)

    def flaky(request: Any, *, context: Any = None) -> Any:
        if float(request.structure.coordinates[0][0]) == 0.0:
            return failed
        return ok(request, context=context)

    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=flaky):
        first = _run(conformers, NmrConfig(), giao_dir)
    assert [item.conformer_id for item in first] == ["conf_001"]  # failure skipped

    stored = json.loads((giao_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert sorted(stored["items_state"]) == ["conf_001"]  # failure never cached

    calls2: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls2)):
        second = _run(conformers, NmrConfig(), giao_dir)
    assert calls2 == [0.0], "failed conformer must be retried; success must be reused"
    assert [item.conformer_id for item in second] == ["conf_000", "conf_001"]


# ---------------------------------------------------------------------------
# (b) invalidation: method-level change → ALL recomputed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("nmr_method", "B3LYP"),
        ("nmr_basis", "def2-TZVP"),
        ("solvent", "water"),
        ("solvent_model", "smd"),
        ("nuclei", ("13C",)),
    ],
)
def test_method_change_invalidates_all_conformers(tmp_path: Path, field: str, value: Any) -> None:
    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.0, 2.0)]

    calls1: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls1)):
        _run(conformers, NmrConfig(), giao_dir)
    assert calls1 == [0.0, 1.0, 2.0]
    plan_before = json.loads((giao_dir / "checkpoint.json").read_text())["plan_fingerprint"]

    changed = NmrConfig(**{field: value})
    calls2: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls2)):
        results = _run(conformers, changed, giao_dir)
    assert calls2 == [0.0, 1.0, 2.0], f"{field}={value!r} did not invalidate the cache"
    assert len(results) == 3
    plan_after = json.loads((giao_dir / "checkpoint.json").read_text())["plan_fingerprint"]
    assert plan_after != plan_before, "plan fingerprint must change with the method"


def test_charge_change_invalidates_all_conformers(tmp_path: Path) -> None:
    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.0)]

    calls1: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls1)):
        _run(conformers, NmrConfig(), giao_dir)
    assert calls1 == [0.0, 1.0]

    charged = [(_conf(x, charge=1, multiplicity=2), 0.5, 0.1) for x in (0.0, 1.0)]
    calls2: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls2)):
        results = _run(charged, NmrConfig(), giao_dir)
    assert calls2 == [0.0, 1.0], "charge/multiplicity change must invalidate every conformer"
    assert len(results) == 2


def test_theory_config_change_invalidates_all_conformers(tmp_path: Path) -> None:
    """theory.* run-config reaches resolve_spec → science identity, so a
    changed grid/scf/aux-basis default must never reuse stale shieldings."""
    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.0)]

    calls1: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls1)):
        _run(conformers, NmrConfig(), giao_dir, cfg={"theory": {"nmr": {"grid": "def2"}}})
    assert calls1 == [0.0, 1.0]

    calls2: list[float] = []
    with patch(
        "acp.workflows.nmr.run_nmr_shielding",
        side_effect=_fake_shielding(calls2),
    ):
        _run(
            conformers,
            NmrConfig(),
            giao_dir,
            cfg={"theory": {"nmr": {"grid": "grid5"}}},
        )
    assert calls2 == [0.0, 1.0], "theory run-config change must invalidate the cache"


# ---------------------------------------------------------------------------
# (c) geometry change → single-conformer invalidation
# ---------------------------------------------------------------------------


def test_geometry_change_invalidates_single_conformer(tmp_path: Path) -> None:
    giao_dir = tmp_path / "giao"
    conformers = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.0, 2.0)]

    calls1: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls1)):
        _run(conformers, NmrConfig(), giao_dir)
    assert calls1 == [0.0, 1.0, 2.0]

    # conf_001 geometry perturbed (1.0 → 1.5); conf_000/conf_002 untouched
    perturbed = [(_conf(x), 0.5, 0.1) for x in (0.0, 1.5, 2.0)]
    calls2: list[float] = []
    with patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls2)):
        results = _run(perturbed, NmrConfig(), giao_dir)

    assert calls2 == [1.5], f"only the changed conformer must recompute, got {calls2}"
    assert results[0].shieldings[0]["isotropic"] == pytest.approx(100.0)  # cache
    assert results[1].shieldings[0]["isotropic"] == pytest.approx(101.5)  # new geometry
    assert results[2].shieldings[0]["isotropic"] == pytest.approx(102.0)  # cache


# ---------------------------------------------------------------------------
# (d) bounded concurrency / explicit resource budget
# ---------------------------------------------------------------------------


def test_giao_resource_budget_caps_workers() -> None:
    from acp.workflows.nmr import _giao_resource_budget, _verify_giao_budget

    cfg = {"resources": {"nproc": 64, "mem": "30GB"}, "executables": {"orca": {"nproc": 10}}}
    budget = _giao_resource_budget(cfg, n_candidates=2, n_conformers=5)
    assert budget["nproc"] == 64
    assert budget["mem"] == "30GB"
    assert budget["nproc_per_giao"] == 10
    assert budget["giao_jobs"] == 10  # 候选 × 构象 demand envelope
    assert budget["max_parallel_giao_jobs"] == min(64 // 10, 10)  # min(nproc//per, jobs)
    assert budget["nproc_per_giao"] * budget["max_parallel_giao_jobs"] <= budget["nproc"]
    assert budget["execution"] == "sequential"
    _verify_giao_budget(budget)  # bound check passes

    # demand clamp: fewer jobs than cores allow
    small = _giao_resource_budget(cfg, n_candidates=1, n_conformers=3)
    assert small["max_parallel_giao_jobs"] == 3


def test_giao_resource_budget_clamps_threads_and_flags_oversubscription() -> None:
    from acp.workflows.nmr import _giao_resource_budget, _verify_giao_budget

    cfg = {"resources": {"nproc": 4}, "executables": {"orca": {"nproc": 32}}}
    budget = _giao_resource_budget(cfg, n_candidates=1, n_conformers=3)
    assert budget["nproc_per_giao"] == 4  # clamped to the job spec
    assert budget["oversubscribed"] is True  # raw 32 > job 4 — recorded, never silent
    assert budget["max_parallel_giao_jobs"] == 1
    _verify_giao_budget(budget)

    # defaults when the job spec carries no resources at all
    bare = _giao_resource_budget({}, n_candidates=1, n_conformers=2)
    assert bare["nproc"] >= 1 and bare["nproc_per_giao"] >= 1
    assert bare["max_parallel_giao_jobs"] == 1
    _verify_giao_budget(bare)


def test_giao_budget_bound_check_rejects_oversubscription() -> None:
    from acp.workflows.nmr import _verify_giao_budget

    with pytest.raises(RuntimeError, match="oversubscri"):
        _verify_giao_budget(
            {"nproc": 4, "nproc_per_giao": 4, "max_parallel_giao_jobs": 3, "giao_jobs": 3}
        )
        with pytest.raises(RuntimeError, match="giao_jobs"):
            _verify_giao_budget(
                {"nproc": 8, "nproc_per_giao": 2, "max_parallel_giao_jobs": 9, "giao_jobs": 5}
            )


def test_budget_recorded_in_analysis_result(tmp_path: Path) -> None:
    """The chosen budget lands in the analysis result (nmr_summary.json +
    WorkflowResult.metadata) — never a new JobStatus."""
    structure = _conf(0.0)
    ensemble = StructureEnsemble(
        records=[
            StructureRecord(
                structure=structure, energy_hartree=-1.0, free_energy_hartree=-1.0, weight=1.0
            )
        ]
    )
    spectrum = "C: 40.0(C1)\nH: 4.0(H1), 3.0(H2), 1.0(H3), 0.0(H4)"
    calls: list[float] = []

    with (
        patch("acp.workflows.nmr.StructureReader") as reader_cls,
        patch("acp.workflows.nmr.run_nmr_shielding", side_effect=_fake_shielding(calls)),
    ):
        reader = MagicMock()
        reader.read.return_value = structure
        reader_cls.return_value = reader

        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=["CCO"],
            spectrum=spectrum,
            output_dir=str(tmp_path),
            skip_conformers=False,
            prebuilt_ensembles=[ensemble],
            error_model="placeholder-student-t",
        )

    assert result.status == "completed", result.error
    assert calls == [0.0], "prebuilt ensemble still runs GIAO when skip_conformers=False"
    budget = result.metadata.get("giao_resource_budget")
    assert budget is not None, "budget missing from WorkflowResult.metadata"
    assert budget["execution"] == "sequential"
    assert budget["n_candidates"] == 1 and budget["n_conformers"] == 1
    assert budget["giao_jobs"] == 1
    assert budget["max_parallel_giao_jobs"] <= budget["giao_jobs"]
    assert budget["nproc_per_giao"] * budget["max_parallel_giao_jobs"] <= budget["nproc"]

    summary_path = Path(result.metadata["report_json"]).parent / "nmr_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["giao_resource_budget"] == budget
