"""Resource-plan tests for the PES single-point stage (2026-09-05 incident).

The SP batch helper must split the task-level nproc across concurrent frame
workers: worker count from the budget remainder, per-job cores written into
BOTH ``resources.nproc`` and ``executables.orca.nproc`` (the latter wins in
``ORCAInterface`` input generation).
"""

# pyright: reportMissingImports=false, reportPrivateUsage=false

from __future__ import annotations

from acp.calculations.pes.scan import _sp_resource_plan


def test_sixteen_cores_split_four_ways() -> None:
    cfg = {
        "resources": {"nproc": 16, "mem": "32GB"},
        "executables": {"orca": {"path": "/opt/orca_6_1_1/orca", "nproc": 16}},
    }
    workers, sp_cfg = _sp_resource_plan(cfg)
    assert workers == 4
    assert sp_cfg["resources"]["nproc"] == 4
    assert sp_cfg["executables"]["orca"]["nproc"] == 4
    assert cfg["resources"]["nproc"] == 16  # original cfg untouched


def test_small_budget_runs_sequential_full_core() -> None:
    workers, sp_cfg = _sp_resource_plan({"resources": {"nproc": 4}})
    assert workers == 1
    assert sp_cfg["resources"]["nproc"] == 4
    assert sp_cfg["executables"]["orca"]["nproc"] == 4


def test_missing_resources_defaults_to_single_worker() -> None:
    workers, sp_cfg = _sp_resource_plan({})
    assert workers == 1
    assert sp_cfg["resources"]["nproc"] == 1


def test_worker_cap_eight() -> None:
    workers, _ = _sp_resource_plan({"resources": {"nproc": 64}})
    assert workers == 8
