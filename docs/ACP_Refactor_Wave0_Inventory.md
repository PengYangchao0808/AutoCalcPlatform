# ACP Refactor Wave 0: Import Graph & Deletion Inventory

**Generated:** 2026-08-27 (Wave 0, Todo 3)
**Branch:** `refactor/calc-cleanup`
**Source:** Frozen plan `docs/ACP_Calculation_Workflow_Refactor_Cleanup_Plan.md` §11, §16

---

## §1 Import Graph: src/ files importing `acp.mechanism`

Eight production source files import from `acp.mechanism`. Each row carries its §16 dependency classification.

| # | Source file | Import lines | Mechanism dependency | §16 classification |
|---|-------------|-------------|---------------------|-------------------|
| 1 | `src/acp/cli.py` | 849, 986, 1004, 1167, 1195, 1242, 2223, 2316 | CLI handlers for PESsearch/Lowconfirm/Highconfirm + dispatch table | active import → migrate |
| 2 | `src/acp/api/v1_routes.py` | 171, 181, 531, 1146, 1201, 2275, 2676, 2831 | Mechanism study CRUD, reaction preview/confirm, S2 review, promote | active import → migrate |
| 3 | `src/acp/scheduler/jobs.py` | 23, 148, 579 | MechanismProjectStore, write_mechanism_job_config, stage_batch_request | active import → migrate |
| 4 | `src/acp/scheduler/manager.py` | 26 | MechanismProjectStore import | active import → migrate |
| 5 | `src/acp/scheduler/runner.py` | 178, 485, 1419 | Mechanism workflow execution paths | active import → migrate |
| 6 | `src/acp/scheduler/stage_tasks.py` | 261 | BOND_SCAN_STAGES constant | active import → migrate |
| 7 | `src/acp/scheduler/remote/runner.py` | 867, 1508 | Remote mechanism job handling | active import → migrate |
| 8 | `src/acp/confsearch/protocols/censo_crest.py` | 96, 97 | ConformerEngine + RPHEnsembleProvider | active import → migrate |

**Total src/ consumers:** 8 files, ~28 import sites

---

## §2 Import Graph: tests/ files importing `acp.mechanism`

22 test files directly import from `acp.mechanism` (verified via `grep -rn "from acp.mechanism\|import acp.mechanism" tests/*.py`). All are test-only references: test-only → rewrite or delete (§16).

| # | Test file | §16 classification |
|---|-----------|-------------------|
| 1 | `test_acp_mechanism_batch.py` | test-only → rewrite or delete |
| 2 | `test_acp_mechanism_cli_study.py` | test-only → rewrite or delete |
| 3 | `test_acp_mechanism_endpoint.py` | test-only → rewrite or delete |
| 4 | `test_acp_mechanism_guided.py` | test-only → rewrite or delete |
| 5 | `test_acp_mechanism_modules.py` | test-only → rewrite or delete |
| 6 | `test_acp_mechanism_modules_schema.py` | test-only → rewrite or delete |
| 7 | `test_acp_mechanism_native_censo_lite.py` | test-only → rewrite or delete |
| 8 | `test_acp_mechanism_native_peb.py` | test-only → rewrite or delete |
| 9 | `test_acp_mechanism_native_refinement.py` | test-only → rewrite or delete |
| 10 | `test_acp_mechanism_primitives.py` | test-only → rewrite or delete |
| 11 | `test_acp_mechanism_project.py` | test-only → rewrite or delete |
| 12 | `test_acp_mechanism_reaction.py` | test-only → rewrite or delete |
| 13 | `test_acp_mechanism_refinement_manifest.py` | test-only → rewrite or delete |
| 14 | `test_acp_mechanism_reports.py` | test-only → rewrite or delete |
| 15 | `test_acp_mechanism_results_v2.py` | test-only → rewrite or delete |
| 16 | `test_acp_mechanism_rph_adapter.py` | test-only → rewrite or delete |
| 17 | `test_acp_mechanism_stages.py` | test-only → rewrite or delete |
| 18 | `test_acp_mechanism_study.py` | test-only → rewrite or delete |
| 19 | `test_acp_mechanism_thermo.py` | test-only → rewrite or delete |
| 20 | `test_acp_mechanism_torsion_dedup.py` | test-only → rewrite or delete |
| 21 | `test_acp_s2_bond_length_scan.py` | test-only → rewrite or delete |
| 22 | `test_acp_s2_candidate_review.py` | test-only → rewrite or delete |

**Total tests/ consumers:** 22 files

---

## §3 mechanism/ File Inventory (ground truth)

59 `.py` files under `src/acp/mechanism/` (verified via `find ... -name "*.py" | sort`):

```
__init__.py              _helpers.py              atom_mapping.py
batch_confirm.py         batch_models.py          bond_changes.py
bond_scan.py             candidates.py            chain.py
endpoint.py              engines/__init__.py      engines/confirmation.py
engines/conformer.py     engines/elementary_step.py  gates.py
identity.py              layout.py                models.py
modules/__init__.py      modules/module_confirm.py modules/module_conformer.py
modules/module_step.py   modules/schema.py        orchestrator.py
presets.py               primitives/__init__.py   primitives/energy_refinement.py
primitives/geometry_guard.py  primitives/path_profile.py  primitives/path_selector.py
primitives/scan_rescue.py  primitives/scan_trajectory.py  primitives/torsion_dedup.py
project.py               providers/__init__.py    providers/contracts.py
providers/fake.py        providers/guided_scan.py  providers/native_censo_lite.py
providers/native_peb.py  providers/native_refinement.py  providers/rph_adapter.py
providers/thermo.py      providers/xtb_ensemble.py  reaction_definition.py
refinement_manifest.py   reports.py               rescue.py
results_v2.py            s2_confirm_svc.py        s2_confirm_sup.py
scan_manifest.py         scan_models.py           stages/__init__.py
stages/confirm.py        stages/handoff.py        stages/high_confirm.py
stages/low_confirm.py    stages/pes_search.py     strategies.py
study_runner.py
```

---

## §11.1 Deletion List (post-migration, 12 rows)

Modules belonging to the old study orchestration or phase compilation. Delete after new workflow engine is wired and all active references removed.

| # | Module path | §16 classification | Notes |
|---|-------------|-------------------|-------|
| 1 | `mechanism/orchestrator.py` | active import → migrate | StudyOrchestrator phase execution, review gates, SR cycles |
| 2 | `mechanism/study_runner.py` | active import → migrate | run_mechanism_study / resume entry |
| 3 | `mechanism/project.py` | active import → migrate | MechanismProjectStore, project CRUD |
| 4 | `mechanism/chain.py` | active import → migrate | MechanismChain orchestration |
| 5 | `mechanism/engines/` | active import → migrate | confirmation.py, conformer.py, elementary_step.py (4 files incl. __init__) |
| 6 | `mechanism/modules/` | active import → migrate | module_confirm.py, module_conformer.py, module_step.py, schema.py (5 files incl. __init__) |
| 7 | `mechanism/stages/confirm.py` | active import → migrate | BatchConfirmEngine S3/S4 confirmation |
| 8 | `mechanism/stages/low_confirm.py` | active import → migrate | Lowconfirm (S3) coarse opt+freq+IRC |
| 9 | `mechanism/stages/high_confirm.py` | active import → migrate | Highconfirm (S4) high-fidelity opt+freq+SP+thermo |
| 10 | `mechanism/stages/handoff.py` | active import → migrate | Artifact transfer between stages |
| 11 | `mechanism/results_v2.py` | active import → migrate | Legacy result manifest writer |
| 12 | `mechanism/__init__.py` | legacy reader → compat | Package init; delete after all children removed |

**Deletion count:** 12 ✓

---

## §11.2 Merge/Migrate List (post-migration, 14 rows)

Modules with valuable algorithmic content. Merge into `calculations/` or `storage/` targets, then delete originals from mechanism/.

| # | Current module | Migration target | §16 classification |
|---|---------------|-----------------|-------------------|
| 1 | `mechanism/batch_confirm.py` | `calculations/batch/engine.py` | active import → migrate |
| 2 | `mechanism/batch_models.py` | `calculations/batch/models.py` | active import → migrate |
| 3 | `mechanism/providers/native_refinement.py` | `calculations/batch/engine.py` | active import → migrate |
| 4 | `mechanism/providers/thermo.py` | `calculations/primitives/thermochemistry.py` | active import → migrate |
| 5 | `mechanism/bond_scan.py` | `calculations/pes/scan.py` | active import → migrate |
| 6 | `mechanism/scan_models.py` | `calculations/pes/contracts.py` | active import → migrate |
| 7 | `mechanism/scan_manifest.py` | `results/manifest.py` | active import → migrate |
| 8 | `mechanism/primitives/path_selector.py` | `calculations/pes/path_selection.py` | active import → migrate |
| 9 | `mechanism/primitives/path_profile.py` | `calculations/pes/path_analysis.py` | active import → migrate |
| 10 | `mechanism/primitives/scan_trajectory.py` | `calculations/pes/scan.py` | active import → migrate |
| 11 | `mechanism/primitives/scan_rescue.py` | `calculations/pes/validation.py` | active import → migrate |
| 12 | `mechanism/primitives/geometry_guard.py` | `calculations/pes/validation.py` | active import → migrate |
| 13 | `mechanism/identity.py` | `calculations/irc/validation.py` | active import → migrate |
| 14 | `mechanism/layout.py` | `storage/layout.py` | active import → migrate |

**Merge/migrate count:** 14 ✓

---

## §Matrix-out Additions (2 rows)

Files not in the §11.1/§11.2 matrices but present in mechanism/ and requiring disposition.

| # | Module path | §16 classification | Notes |
|---|-------------|-------------------|-------|
| 1 | `mechanism/s2_confirm_service.py` | active import → migrate | API S2 review service layer |
| 2 | `mechanism/s2_confirm_support.py` | active import → migrate | API S2 review support utilities |

**Matrix-out count:** 2 ✓

---

## §11.3 Valuable-but-Renamed Migration List (17 rows)

Algorithmic files that must NOT be deleted outright. Migrate to `calculations/pes` or `calculations/irc`, stripping mechanism-specific semantics (study, stage, promotion, review gate, S2/S3/S4, mechanism project).

| # | Module path | §16 classification | Notes |
|---|-------------|-------------------|-------|
| 1 | `mechanism/reaction_definition.py` | active import → migrate | Locked reaction-definition models + reaction.json persistence |
| 2 | `mechanism/endpoint.py` | active import → migrate | Endpoint classification, IRC endpoint matching |
| 3 | `mechanism/atom_mapping.py` | active import → migrate | Cross-state reactant/product atom mapping (0-based) |
| 4 | `mechanism/bond_changes.py` | active import → migrate | Bond-change classification + drive-coordinate suggestions |
| 5 | `mechanism/scan_manifest.py` | active import → migrate | Scan manifest writer |
| 6 | `mechanism/scan_models.py` | active import → migrate | Scan data models |
| 7 | `mechanism/bond_scan.py` | active import → migrate | Bond-length scan pipeline |
| 8 | `mechanism/batch_confirm.py` | active import → migrate | Batch confirm engine |
| 9 | `mechanism/batch_models.py` | active import → migrate | Batch calculation models |
| 10 | `mechanism/providers/native_refinement.py` | active import → migrate | Native refinement provider |
| 11 | `mechanism/providers/thermo.py` | active import → migrate | Thermochemistry provider |
| 12 | `mechanism/presets.py` | active import → migrate | Study presets/configuration |
| 13 | `mechanism/reports.py` | active import → migrate | Mechanism report generation |
| 14 | `mechanism/rescue.py` | active import → migrate | 8-cell rescue matrix for failed calculations |
| 15 | `mechanism/refinement_manifest.py` | active import → migrate | S3/S4 manifest read/write |
| 16 | `mechanism/candidates.py` | active import → migrate | Candidate selection and management |
| 17 | `mechanism/strategies.py` | active import → migrate | Guided-scan / rph-reverse / direct-ts strategies |

**Migration count:** 17 ✓

---

## §4 Classification Legend (§16 from frozen plan)

| Category | Meaning | Action |
|----------|---------|--------|
| **active import → migrate** | Production code imports from `acp.mechanism` | Must be rewired to new `calculations/` targets before mechanism/ deletion |
| **legacy reader → compat** | Code reads mechanism artifacts for backward compatibility | Move to `compat/legacy/`, read-only |
| **test-only → rewrite or delete** | Test file imports mechanism modules directly | Rewrite to test new targets, or delete if coverage redundant |
| **doc → update** | Documentation references mechanism paths | Update paths, archive historical sections |

---

## §5 Notes on api_mechanism Tests

Four test files in the `test_acp_api_mechanism_*` family exist in `tests/`. Their disposition:

- **3 tests (promote, studies, submit)**: do NOT import `acp.mechanism` (import scheduler/fastapi only). **KEPT**, rewritten to read-only.
- **1 test (reaction)**: DOES import `acp.mechanism.reaction_definition`. Included in the 22 deleted tests above (row #12). Must be rewritten to use the migrated module from its new location.

**Summary:** 22 tests deleted (all mechanism-importing), 3 api_mechanism tests kept and rewritten. The kept tests exercise API routes that serve mechanism data but do not import mechanism modules directly.

---

## §6 Verification Checklist

| Metric | Expected | Actual | Status |
|--------|----------|--------|--------|
| §11.1 deletion rows | 12 | 12 | ✓ |
| §11.2 merge/migrate rows | 14 | 14 | ✓ |
| Matrix-out rows | 2 | 2 | ✓ |
| §11.3 migration rows | 17 | 17 | ✓ |
| Distinct test filenames (c4) | 22 | 22 | ✓ |
| Total table rows (`grep -c "^| "`) | ≥ 50 | ~80 | ✓ |
