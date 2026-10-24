# confsearch-weight-provenance-final-report - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 构象搜索任务的玻尔兹曼权重从此标注真实来源（CENSO / xTB / DFT）与温度：rank1（1only）显示 CENSO 表里的 p₁（不再是 100%），99% cutoff 与 all 显示 DFT 精修权重；每个任务还会多出一份"最终报告"（精修后的结构 + 热力学校正能量 + 玻尔兹曼权重），检查器与任务详情都能看到。

**Why this approach:** 权重目前在管线里被统一重算、来源信息一路丢失；我们不猜来源，而是让每个协议在结束时把自己的权威权重表（CENSO / xTB / DFT）与真实温度显式写进清单，并据此生成报告与展示。

**What it will NOT do:** 不回填或改写历史任务；不改动采样与构象选择的科学逻辑；不做超出权重来源标注的界面改版。

**Effort:** Large
**Risk:** Medium - 横跨引擎/协议/查看器/前端，且清单与结果清单是下游（PESsearch、结构来源面板）的读取契约。

**Decisions to sanity-check:** rank1 仅显示精修条目 + CENSO p₁；独立报告文件（final_report.json + final_conformers.xyz）并合并注册进 result_manifest；显式设置 --temperature 时转发给委托工作流；检查器新增"来源 + 温度"标注（zh/en）。

Your next move: 审阅计划后选择：直接开始执行（`$start-work`），或先做一轮高精度双审（momus + Oracle）。

---

> TL;DR (machine): Large effort, Medium risk - per-policy weight provenance (rank1 CENSO/xTB p₁, cumulative-99/all DFT, screen/pure-xTB table source) + weighted-temperature propagation, consolidated final report (final_report.json + final_conformers.xyz) merged into result_manifest, viewer/API/frontend provenance display, docs + regression tests; 12 todos in 3 waves + F1-F4.

## Scope
### Must have
- **C1 — Weight provenance per protocol × refinement policy.** Confsearch manifests and `boltzmann.json` declare where each weight came from and at which temperature; `rank1` keeps ONE refined entry whose weight is the CENSO/xTB screening-table `p₁` (not 1.0); `cumulative-99` / `all` weights are DFT-table values with `population_coverage`; `screen` and pure-xTB protocols carry CENSO/xTB-table provenance. Adds: top-level `temperature_k`, per-conformer `weight_source` + `refined`, a `weight_table` summary block, a policy-aware G1 `boltzmann_weights_valid` gate, and one canonical Boltzmann helper for the viewer fallback.
- **Screening-key + temperature decisions (Metis B1/M2).** Manifest `conf_id` stays `conf_NNNN` (no format change); a new additive `source_conf_id` carries the original screening-table key used to join `boltzmann_table.json`, propagated through `protocols/_common.records_from_ensemble_result` (which must also carry `Structure.metadata["source"]`). When `ConfsearchRequest.temperature` is explicitly set it is forwarded to the delegated workflows via a per-protocol `levels.thermo.temperature` copy (explicit `levels` wins; `request` never mutated); the resolved temperature is what manifests/report/viewer display. If a rank1 table is missing/corrupt the job degrades: `weight_source="computed"`, `weight_method="computed"`, gate checks only `0<w<=1`, warning recorded.
- **C2 — Final report artifact (user requirement).** New `RESULT/confsearch/final_report.json` (schema `confsearch_final_report_v1`) + `RESULT/confsearch/final_conformers.xyz` containing, per conformer: final (refined where applicable) geometry reference, thermodynamically corrected energy with level label, Boltzmann weight + source, plus temperature/coverage/method summary; merge-registered into `RESULT/result_manifest.json` (read → `add_product` → `write`, idempotent, header fields preserved) so job detail and the structure viewer can see it. Screen-policy jobs gain a registered `result_manifest.json` (previously absent) — a conscious, tested change including the `structure_sources` projection.
- **C3 — Inspector display correctness.** Structure-viewer resolver reads top-level temperature and `weight_source`; API schema and frontend show the weight with its source and temperature (D4, rendered from the field — never a hardcoded literal); energy-graph conformer inspector field-key mismatch fixed so "Boltzmann 权重" renders (D11); no cross-level recomputation mixing.
- **C4 — Other-workflow semantics documented.** A weight-source matrix covering all four Confsearch protocols/policies (incl. preset branches such as rank1 cheap → xTB table) and the non-Confsearch workflows (NMR internal CENSO/xTB weights; BatchOptimize no weights but Gibbs; PES/scan/IRC/sampling no weights), documented in the dev docs (no science changes).
- **Tests (D3 — tests-after, red-first for the pinning tests).** pytest regressions for rank1/cumulative-99/all/screen provenance, report schema + registration merge, viewer/API fields, energy-graph keys, and legacy read-compat. Critical provenance/join-key tests (T2, T5) must first fail on pre-change code (failing log captured). Each todo lists the existing tests it updates; pre-named: `tests/test_acp_confsearch.py:130-133` (boltzmann.json exact shape) and the fill-only-missing viewer test (`tests/test_acp_structure_viewer.py:2453+`).

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No backfill/rewrite of historical job artifacts or manifests; legacy manifests keep the current fill-only-missing read path.
- No change to rank1 manifest row count (stays one refined entry — user decision D1) and no change to the actual selection science of screen/cumulative-99/all.
- No new workflows, CLI subcommands, or protocol values; no changes to PESsearch/BatchOptimize/NMR scientific pipelines.
- No `RESULT/confsearch/refinement/` directory writer (documented but never written — do not invent it).
- No change to `write_v2_manifest` overwrite semantics; report registration must merge (read → add_product → write).
- No change to manifest `conf_id` format, geometry file naming, `ensemble.csv` columns, or delegated-workflow output file formats (`conformer_thermo.csv`, `ensemble_thermo.json`, `all_conformers.xyz`) beyond additive metadata keys.
- No change to `energy_graph.py` series ids (e.g. the `boltzmann_weight` series) or to `TrajectoryFrame.to_node()` usage (AGENTS #28).
- Canonical-helper scope is exactly: confsearch engine + viewer fallback + report; delegated workflows (`energy_shared`) and `cccp` keep their own constants (values agree within FP tolerance). Do not rewrite `energy_shared.py`'s k_B math.
- No frontend redesign beyond the weight provenance disclosure; no new frontend dependencies; no emojis.
- No `.omo/` or test artifacts committed; no unrelated dirty-worktree files touched.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after** (user D3) + pytest; run targeted suites per todo and `pytest -m "not slow"` for CI parity at the end. For the critical provenance/join-key paths (T2, T5), the new regression must be demonstrated **red-first** (fails on pre-change code; failing log kept as evidence) before the fix.
- Evidence: `.omo/evidence/confsearch-weight-provenance-final-report/task-<N>-<name>.log` (outside ulw-loop use `.omo/evidence/`).
- Every todo carries agent-executable acceptance criteria and happy + failure QA scenarios with exact commands, plus an "Existing tests updated" line.
- Verified consumer sweep (Metis m6, do not re-litigate): additive fields are safe for `read_manifest` (`manifest.py:215-229`), PESsearch (`calculations/pes/engine.py:77-147`), energy-graph normalizer (`energy_graph.py:1023-1058`), structure_sources (kind-based), and Pydantic models (ignore extras). The only semantic surface change is the registered result_manifest products from T6.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1 — core data path (T1–T4):** canonical Boltzmann helper; contracts/result_helpers/gate; workflow metadata emission; manifest writer.
- **Wave 2 — integration + read surfaces (T5, T6, T8, T10):** protocol table application; final-report writer + merge registration; structure-viewer resolver/schema; energy-graph keys.
- **Wave 3 — engine wiring + frontend + docs + compat (T7, T9, T11, T12):** engine finalization; frontend inspector; documentation; legacy-compat/regression pass.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 | — | T5 (helper use), T8 | T2, T3, T4, T10 |
| T2 | — | T3, T4, T5, T6, T7, T8 | T1, T3, T10 |
| T3 | — | T5, T7 | T1, T2, T4, T10 |
| T4 | T2 | T5, T6, T7, T8 | T1, T3, T10 |
| T5 | T2, T3, T4 | T7 | T6, T8, T10 |
| T6 | T2, T4 | T7 | T5, T8, T10 |
| T7 | T5, T6 | T12 | T9, T11 |
| T8 | T4 | T9 | T5, T6, T10 |
| T9 | T8 | T12 | T7, T11 |
| T10 | — | T12 | T1, T2, T3, T5, T6, T8 |
| T11 | T2–T8 (schema frozen) | T12 | T7, T9 |
| T12 | T7, T9, T10, T11 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. shared/boltzmann.py: add missing-policy to the canonical Boltzmann helper
  What to do / Must NOT do: In `src/acp/confsearch/shared/boltzmann.py` extend `boltzmann_weights(energies, temperature_k=298.15, *, missing: Literal["zero","none"]="zero")`; `"zero"` keeps today's behavior exactly, `"none"` maps None/non-finite entries to `None` and excludes them from the normalization denominator (mirrors `structure_viewer._compute_boltzmann_weights` semantics). Re-export via `shared/__init__.py` if that file lists symbols. Must NOT change the default call signature behavior (existing callers `result_helpers.py:40` unaffected), the R constants (line 9), or add numpy.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 5, 8
  References: `src/acp/confsearch/shared/boltzmann.py:1-49`; `src/acp/results/structure_viewer.py:587-619` (target semantics); `src/acp/confsearch/result_helpers.py:14,37-41`; `src/acp/confsearch/shared/__init__.py`.
  Existing tests updated: none; new tests added.
  Acceptance criteria (agent-executable): `pytest tests/test_acp_confsearch.py tests/test_acp_structure_viewer.py -q` green; a new unit test proves `missing="none"` returns `None` per missing entry while finite entries normalize to 1.0, and `missing="zero"` output equals the current implementation for mixed input.
  QA scenarios: happy — `pytest tests/test_acp_confsearch.py -q` plus new unit test passes; failure — assert the `"none"` policy on an all-missing list returns all `None` (no division), Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-1-boltzmann.log`
  Commit: Y | feat(confsearch): add missing-policy to canonical Boltzmann helper

- [x] 2. contracts.py + result_helpers.py: propagate weight provenance and screening join key
  What to do / Must NOT do: (a) `contracts.py`: `ConformerEntry` gains additive fields `source_conf_id: str|None`, `weight_source: str|None`, `refined: bool=False` (serialized in `to_dict`, existing keys unchanged); `ProtocolOutcome` gains `weight_table: dict[str,float]|None`, `weight_source: str|None`, `weight_method: str|None`, `population_coverage: float|None`, `energy_kind: str|None`. (b) `protocols/_common.py::records_from_ensemble_result`: carry `source_conf_id` = `structure.metadata.get("source")` or `structure.metadata.get("conf_id")` (screen path) into each record; keep `conf_id` as record basis. (c) `result_helpers.build_entries`: manifest `conf_id` REMAINS `conf_NNNN`; set `source_conf_id`; when `outcome.weight_table` is present, weight = table lookup by `source_conf_id` — if ANY lookup fails, fall back to recompute for ALL entries and set `weight_source="computed"`; `refined` = `source_conf_id in outcome.refined_conf_ids` (when non-empty). (d) `quality_gates`: make `boltzmann_weights_valid` policy-aware via a new `policy`/`weight_source` input — rank1 (table mode): all weights finite in (0,1] and match table (tol 1e-9); rank1 (degraded `computed`): only `0<w<=1`; all other policies: existing `abs(sum-1)<1e-3`. Add `weight_source` key to the gates dict. Must NOT change `conf_id` format, `sorted_records` ordering, `relative_energies_kcal` behavior, or start consuming `record["weight"]`.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3,4,5,6,7,8
  References: `src/acp/confsearch/contracts.py:80-133`; `src/acp/confsearch/result_helpers.py:17-112`; `src/acp/confsearch/protocols/_common.py:22-62`; `src/acp/workflows/energy.py:762-766` (source-space refined ids); `src/acp/workflows/energy_shared.py:1005-1033` (metadata source key); draft `.omo/drafts/confsearch-weight-provenance-final-report.md` addenda.
  Existing tests updated: `tests/test_acp_confsearch.py:123-172,296-303` (entry fields, gates), any assertions constructing `ProtocolOutcome`/`ConformerEntry` positionally.
  Acceptance criteria: fabricated-outcome unit tests: table present → entry weight == table value + `weight_source`; table absent → recompute + `computed`; rank1 single entry with p₁=0.8442 → `G1=="PASS"`; multi-entry sum gate unchanged (red-first: the rank1 test must fail before the change); `pytest tests/test_acp_confsearch.py -q`.
  QA scenarios: happy — engine-level stub protocol test via `ConfsearchEngine.run` with monkeypatched `PROTOCOL_RUNNERS`; failure — table lookup partial miss → all-computed fallback + warning, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-2-provenance.log`
  Commit: Y | feat(confsearch): propagate weight provenance and screening join key

- [x] 3. Delegated workflows: emit resolved temperature and weight-table provenance in metadata
  What to do / Must NOT do: `energy_shared.write_final_outputs` return dict (`outputs`) gains additive keys `temperature_k`, `population_coverage`, `weight_source` (`"dft"` for workflow-1 / `external_table_source` for workflow-2), `weight_method` (= `summary.method`); spread flows to metadata via existing `**outputs` in `energy.py:758` and `xtbmd_censo_energy.py`. `energy_shared.build_result_ensemble` adds `"conf_id": c.get("source") or conf_struct.id` to each `Structure.metadata` (join key). `workflows/ensemble.py` metadata gains `temperature_k` (from the CENSO result; verify field path), `weight_source` (`"xtb"` iff preset `censo-zero` else `"censo"`), `weight_method` (`xtb_table`/`censo_table`), `population_coverage` 1.0. Must NOT change delegated output file formats (`conformer_thermo.csv`, `ensemble_thermo.json`, `all_conformers.xyz`, `boltzmann_table.json`, `ensemble.json/csv/xyz`) or any weight math.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 5, 7
  References: `src/acp/workflows/energy_shared.py:585-687,765-1002,1005-1033`; `src/acp/workflows/energy.py:718-766`; `src/acp/workflows/xtbmd_censo_energy.py:2032-2085`; `src/acp/workflows/ensemble.py:81-107,330-398`; `src/acp/workflows/energy_shared.py:315` (resolved T).
  Existing tests updated: `tests/test_acp_workflows_energy.py:441,812-826,873-886`; `tests/test_acp_workflows_xtbmd_censo_energy.py`; `tests/test_acp_workflows_ensemble.py` (assert additions only).
  Acceptance criteria: new assertions that metadata contains the four keys with expected values for: censo rank1 (source censo), cheap rank1 (source xtb), cumulative-99 (source dft + coverage <1), ensemble screen (source censo/xtb, coverage 1.0); `pytest tests/test_acp_workflows_energy.py tests/test_acp_workflows_xtbmd_censo_energy.py tests/test_acp_workflows_ensemble.py -q`.
  QA scenarios: happy — targeted suites; failure — rank1 cheap path returns `weight_source="xtb"` assertion, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-3-metadata.log`
  Commit: Y | feat(workflows): emit weight provenance and resolved temperature metadata

- [x] 4. confsearch/manifest.py: top-level temperature, weight_table block, report refs, boltzmann.json provenance
  What to do / Must NOT do: `build_manifest_payload` gains params `temperature_k: float`, `weight_table: dict|None`, `report: dict|None`; payload adds top-level `temperature_k`, `weight_table` (`{source, method, temperature_k, population_coverage, reference}`), `report` (`{json, xyz}`). `write_ensemble_table` boltzmann.json adds `temperature_k`, `source`, `method`, `population_coverage`, `reference` while keeping `weights` + `weight_sum` exactly. Update the module docstring tree (`manifest.py:1-15`) to include `final_report.json`/`final_conformers.xyz` and correct the stale `refinement/` mention. Must NOT bump `schema_version` ("confsearch_v1"), rename existing keys, or change `ensemble.csv` header/geometry/id schemes.
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 5,6,7,8
  References: `src/acp/confsearch/manifest.py:1-15,92-212`; `src/acp/confsearch/engine.py:188-206`.
  Existing tests updated: `tests/test_acp_confsearch.py:123-172` (boltzmann.json exact-shape assertion at 130-133 must be updated to the additive shape).
  Acceptance criteria: tests assert new keys present with correct values and all old keys unchanged; `pytest tests/test_acp_confsearch.py -q`.
  QA scenarios: happy — roundtrip test reads manifest/boltzmann.json and checks `weight_table.reference` points at `RESULT/ensembles/boltzmann_table.json` when applicable; failure — malformed `weight_table` input (non-dict) is rejected/omitted without crashing, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-4-manifest.log`
  Commit: Y | feat(confsearch): add weight provenance to manifest and boltzmann outputs

- [x] 5. Protocols: apply rank1 table, set provenance per policy, forward temperature
  What to do / Must NOT do: In `protocols/_common.py` add `apply_screening_weight_table(outcome, policy, warnings)`: for `policy=="rank1"` read `outcome.workflow_metadata["boltzmann_table_json"]` (JSON: `temperature_k`, `source`, `weights`) → set `outcome.weight_table/weight_source/weight_method=f"{source}_table_rank1"/population_coverage=1.0/temperature_k` (prefer file value); missing/unreadable → warning + `weight_source="computed"`, `weight_method="computed"`. Wire per protocol: `censo_crest.py` rank1 → helper; cumulative-99/all → `weight_source="dft"`, `weight_method` from metadata, coverage from metadata; screen → from ensemble metadata; `xtbmd_censo.py` same; `xtb_crest.py`/`xtb_md.py` → `weight_source="xtb"`, `weight_method="xtb_table"`, coverage 1.0. Temperature forwarding (M2): when `request.temperature` is set and no explicit `levels.thermo.temperature`, pass a COPY of levels with `thermo.temperature` merged into the delegated call for censo_crest/xtbmd_censo/xtb_crest (never mutate `request`; explicit levels win). Must NOT read the table for non-rank1 policies, fail the job on missing table (degrade + warning), change threshold/selection logic, or mutate `request.levels`.
  Parallelization: Wave 2 | Blocked by: 2,3,4 | Blocks: 7
  References: `src/acp/confsearch/protocols/_common.py:65-98`; `src/acp/confsearch/protocols/censo_crest.py:28-84`; `xtbmd_censo.py:21-83`; `xtb_crest.py:19-45`; `xtb_md.py:30-179`; `src/acp/workflows/energy_shared.py:898-913` (table shape); `src/acp/confsearch/selection.py:54-64`; `src/acp/confsearch/contracts.py:73` (request.temperature).
  Existing tests updated: `tests/test_acp_confsearch.py` protocol-runner tests; any test asserting hardcoded `temperature_k=298.15`.
  Acceptance criteria: mocked delegated results prove: rank1 table applied (weight_source censo/xtb, p₁ from table); missing table → computed + warning; cumulative-99 → dft + coverage; screen → censo/xTB; temperature forwarded when set and explicit levels untouched (red-first for the rank1 table application); `pytest tests/test_acp_confsearch*.py -q`.
  QA scenarios: happy — mocked `run_conformer_energy` returning metadata with `boltzmann_table_json` pointing at a fixture file; failure — fixture missing → outcome degraded with warning and job completes, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-5-protocols.log`
  Commit: Y | feat(confsearch): apply screening weights per policy and forward temperature

- [x] 6. confsearch/report.py: final report writer + merge registration
  What to do / Must NOT do: New module `src/acp/confsearch/report.py` exporting `write_final_report(confsearch_dir, *, request, entries, outcome) -> tuple[Path, Path]` and `register_final_report(mol_dir, report_path, xyz_path) -> None`. `final_report.json` schema `confsearch_final_report_v1`: top-level `workflow/protocol/profile/refinement_policy/temperature_k/weight_table{source,method,population_coverage,reference}/total_gibbs_hartree/total_gibbs_kcal_mol` (from `outcome.workflow_metadata` when present) + `conformers[]` each `{conf_id, source_conf_id, rank, refined, geometry (confsearch-relative, same as manifest), energy_hartree, free_energy_hartree, energy_kind (refined→"dft" else outcome.energy_kind or protocol default censo|xtb), relative_energy_kcal, boltzmann_weight, weight_source}`. Write JSON via `shared.artifacts.write_json_atomic`. `final_conformers.xyz`: one frame per entry in rank order, read from `confsearch_dir/entry.geometry`, title `{conf_id} G=... w=... src=... [refined]`. Registration: `ResultManifest.read(mol_dir/"RESULT")` (FileNotFoundError → fresh `ResultManifest(task_id="", workflow="Confsearch", status="completed")`), preserve existing header fields, `add_product("confsearch_final_report", ..., "confsearch/final_report.json", ProductKind.REPORT)` + `add_product("confsearch_final_conformers", ..., "confsearch/final_conformers.xyz", ProductKind.STRUCTURE)`, `manifest.write(mol_dir/"RESULT")`. Report write/registration failure RAISES (required deliverable). Must NOT hand-write result_manifest JSON, drop existing products/header, add ProductKind values, or register when files are missing.
  Parallelization: Wave 2 | Blocked by: 2,4 | Blocks: 7
  References: `src/acp/storage/manifest.py:21-163`; `src/acp/confsearch/shared/artifacts.py:41`; `src/acp/nmr/report.py:30-35,156-165`; `src/acp/workflows/nmr.py:1087-1160`; `src/acp/workflows/energy_shared.py:88-104`; draft addenda.
  Existing tests updated: none (new test module), plus `tests/test_acp_confsearch.py` gains report assertions.
  Acceptance criteria: new tests: schema keys + per-conformer field set; xyz frame count == entries; merge test seeds a fake `result_manifest.json` with pre-existing product ids and asserts they survive alongside the two new ids with kinds report/structure; re-run is idempotent (no duplicates); `FileNotFoundError` → fresh manifest; `pytest tests/test_acp_confsearch.py -q`.
  QA scenarios: happy — registration merge test with seeded manifest; failure — `confsearch_dir` read-only / missing geometry → raises with a clear error, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-6-report.log`
  Commit: Y | feat(confsearch): write and register the final report artifact

- [x] 7. engine.py: wire finalization (temperature, weight_table, report, gate) — F2 initial REJECT fixed in 84ebca1; F2/F1 re-review VERDICT: APPROVE
  What to do / Must NOT do: In `ConfsearchEngine._finalize`: build `weight_table` meta from `outcome` (source/method/coverage/reference=`outcome.workflow_metadata.get("boltzmann_table_json")`), pass `temperature_k=outcome.temperature_k` + `weight_table` + `report={json,xyz}` to `build_manifest_payload`; call `write_final_report` + `register_final_report` (mol_dir = `confsearch_dir.parent.parent`); keep sampling-history best-effort; ensure manifest `report` keys are set before `write_manifest`. Must NOT swallow report errors, change stage/progress names, or reorder existing key semantics.
  Parallelization: Wave 3 | Blocked by: 5,6 | Blocks: 12
  References: `src/acp/confsearch/engine.py:139-279`; `src/acp/confsearch/report.py` (todo 6); `src/acp/confsearch/manifest.py` (todo 4).
  Existing tests updated: `tests/test_acp_confsearch_progress.py`; `tests/test_acp_confsearch.py:296-303` (tree/G1 assertions).
  Acceptance criteria: stub-protocol engine test asserts manifest has `temperature_k`/`weight_table`/`report`, both report files exist, `RESULT/result_manifest.json` contains the two products, and the engine returns `completed`; `pytest tests/test_acp_confsearch.py tests/test_acp_confsearch_progress.py -q`.
  QA scenarios: happy — end-to-end engine test on a temp dir; failure — report writer raises → engine returns `status="failed"` with error surfaced (assert), Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-7-engine.log`
  Commit: Y | feat(confsearch): finalize manifests with provenance and final report

- [x] 8. structure_viewer.py + v1_schemas.py: weight_source, temperature, canonical fallback
  What to do / Must NOT do: `StructureViewerEntry` gains `weight_source: str|None=None` (serialize in `to_dict`); `_resolve_confsearch` reads temperature from top-level `temperature_k` → fallback `provenance.temperature_k` → default; sets `weight_source` from the conformer; the fill-only-missing fallback delegates to the canonical helper (`shared.boltzmann.boltzmann_weights(..., missing="none")`), deleting the local duplicate R constants. `api/v1_schemas.StructureViewerEntryModel` gains `weight_source: str|None=None`. Keep the existing warning text and fill-only-missing semantics. Must NOT rename entry fields, alter other resolvers' behavior, or change `_compute_revision`.
  Parallelization: Wave 2 | Blocked by: 4 | Blocks: 9
  References: `src/acp/results/structure_viewer.py:266-297,587-619,622-784`; `src/acp/api/v1_schemas.py:1194-1268`; `src/acp/api/v1_routes.py` structure-viewer endpoint; `tests/test_acp_structure_viewer.py:564-597,2453+`.
  Existing tests updated: `tests/test_acp_structure_viewer.py` (temp fallback + warning pins), `tests/test_acp_api_structure_viewer.py` fixtures.
  Acceptance criteria: new tests: top-level temperature honored (provenance fallback covered), `weight_source` round-trips through the API model, legacy manifest without new fields still fills weights and warns; `pytest tests/test_acp_structure_viewer.py tests/test_acp_api_structure_viewer.py -q`.
  QA scenarios: happy — resolver unit tests on fixture manifests; failure — manifest with only provenance temperature still shows the right T and weights, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-8-viewer.log`
  Commit: Y | feat(results): expose weight source and true temperature in the viewer

- [x] 9. frontend structure viewer: render weight source + temperature (zh/en)
  What to do / Must NOT do: In `frontend/js/structure_viewer.js` inspector weight row (~3280-3282) render `xx.x%（<SOURCE>, <T> K）` using `entry.weight_source` mapping {censo: CENSO, xtb: xTB, dft: DFT, computed: computed}; add source/temperature to the Boltzmann bar tooltips (~2103-2108, 2592-2595, 2742-2748); add i18n keys for the source labels in BOTH zh and en dictionaries (same file or the Workbench i18n dict where `structure.weight` lives — locate first); degrade to today's bare percent when `weight_source`/temperature absent. Must NOT add dependencies, change payload field names, or touch unrelated UI.
  Parallelization: Wave 3 | Blocked by: 8 | Blocks: 12
  References: `frontend/js/structure_viewer.js:2103-2108,2592-2595,2742-2748,3270-3295`; `tests/test_frontend_sync.py:27-30,448-466,574-613`; i18n location of `structure.weight`.
  Existing tests updated: `tests/test_frontend_sync.py` (new key parity + structure_viewer.js contract).
  Acceptance criteria: `pytest tests/test_frontend_sync.py -q` green; new assertions: zh+en both contain the new keys and `structure_viewer.js` references them; legacy entries without source still render the bare percent.
  QA scenarios: happy — frontend sync suite; failure — remove a key from one locale in a scratch copy → parity test fails (demonstrate test efficacy), Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-9-frontend.log`
  Commit: Y | feat(frontend): show Boltzmann weight source and temperature in the inspector

- [x] 10. energy_graph.py + frontend: conformer inspector key alignment
  What to do / Must NOT do: In `_build_conformer_graph` emit node metadata via `TrajectoryFrame.to_node()` with keys matching the frontend conformer specs: `boltzmann_weight`, `gibbs_energy`, `relative_energy_kcal`, keep `rank` (drop `weight`/`gibbs_hartree` from node metadata once no consumer needs them; keep `energy_hartree` only if it is still displayed intentionally); update `_INSPECTOR_COMMON_KEYS` handling so no raw duplicate rows remain. Do NOT change series ids (the `boltzmann_weight` series at `energy_graph.py:1159` stays) or wire shapes elsewhere. Must NOT bypass `TrajectoryFrame.to_node()` (AGENTS #28).
  Parallelization: Wave 2 | Blocked by: none | Blocks: 12
  References: `src/acp/results/energy_graph.py:1023-1058,1136-1192,1425-1442`; `frontend/ACP_Workbench_v2.html:7217-7239,7440-7470`; `src/acp/results/frames.py:64-87`; `tests/test_acp_energy_graph.py:754,797-842`.
  Existing tests updated: `tests/test_acp_energy_graph.py` (node metadata assertions), frontend sync contract if it pins inspector fields.
  Acceptance criteria: new assertions that conformer nodes carry `boltzmann_weight`/`gibbs_energy`/`relative_energy_kcal`; `pytest tests/test_acp_energy_graph.py -q` + frontend sync green; series id unchanged.
  QA scenarios: happy — energy-graph unit tests; failure — a node missing relative energy still omits only that row without raw duplicates, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-10-energygraph.log`
  Commit: Y | fix(results): align conformer inspector metadata keys

- [x] 11. docs: weight-source matrix, final report contract, viewer fields
  What to do / Must NOT do: Update `docs/ACP_Structure_Viewer_DevDoc.md` (entry `weight_source`/temperature semantics, fallback rules, test map) and `docs/ACP_Confsearch_Manual_Mechanism_Modification_Plan.md` §5 output tree (add `final_report.json`/`final_conformers.xyz`; document `weight_table` + boltzmann.json fields). Add a weight-source matrix: 4 protocols × 4 policies with preset branches (rank1 → censo|xtb cheap path; cumulative-99/all → dft + coverage; screen → censo|xtb; pure xTB → xtb), plus a short statement for NMR/BatchOptimize/PES/scan/IRC/sampling (no display weights / not in scope). Must NOT change code or add new docs beyond these.
  Parallelization: Wave 3 | Blocked by: 2-8 (schema frozen) | Blocks: 12
  References: `docs/ACP_Structure_Viewer_DevDoc.md:129,241`; `docs/ACP_Confsearch_Manual_Mechanism_Modification_Plan.md:274-338`; draft addenda (matrix evidence).
  Existing tests updated: none.
  Acceptance criteria: docs name every new field and the matrix covers all protocol×policy cells; reviewer can trace each cell to code refs in the draft.
  QA scenarios: happy — grep the matrix cells and field names in the two docs; failure — a missing cell (e.g. rank1 cheap xtb) is detectable by the matrix checklist, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-11-docs.log`
  Commit: Y | docs(confsearch): document weight provenance and final report contract

- [x] 12. Legacy compat + focused regression sweep
  What to do / Must NOT do: Run `pytest tests/test_acp_confsearch.py tests/test_acp_structure_viewer.py tests/test_acp_api_structure_viewer.py tests/test_acp_energy_graph.py tests/test_acp_workflows_energy.py tests/test_acp_workflows_xtbmd_censo_energy.py tests/test_acp_workflows_ensemble.py tests/test_acp_ensemble_thermo.py tests/test_storage_manifest_kinds.py tests/test_frontend_sync.py -q` then `pytest -m "not slow"`; fix only regressions caused by this work; run `ruff check src tests` + `ruff format --check src tests` on touched files. Verify by unit test that a screen-policy tree now exposes a registered multi-frame structure product through `scheduler/structure_sources` projection. Must NOT refactor unrelated code or chase pre-existing failures (document them instead).
  Parallelization: Wave 3 | Blocked by: 7,9,10,11 | Blocks: none
  References: `src/acp/scheduler/structure_sources.py:47-55,266-290,611-638`; `tests/test_frontend_sync.py`; `.github/workflows/ci.yml` (install line `.[dev,api,remote]`).
  Existing tests updated: as needed by regressions.
  Acceptance criteria: `pytest -m "not slow"` exit 0; ruff clean on touched files; compat test proves old manifests render unchanged.
  QA scenarios: happy — the full command set with logs; failure — intentionally load a legacy manifest fixture and assert fill-only-missing behavior still applies, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-12-regression.log`
  Commit: N | (verification only; commit fixes if regressions require)

- [x] 13. report.py: eliminate the orphan final_report.json on XYZ-write failure (discovered by T6/T7 verification)
  What to do / Must NOT do: In `src/acp/confsearch/report.py::write_final_report`, write `final_conformers.xyz` BEFORE `final_report.json` (or unlink the JSON on XYZ failure) so a failed write leaves no orphan artifact; keep raise semantics and byte-identical success output. Add a regression test asserting no `final_report.json` remains when all geometries are missing. Must NOT change schema/contract, engine behavior, or success-path bytes.
  Parallelization: Wave 3 (discovered work) | Blocked by: 6,7 | Blocks: 12
  References: `src/acp/confsearch/report.py:70-101`; `.omo/evidence/confsearch-weight-provenance-final-report/verify-t5-t6.md` (low issue); `verify-t7.md` (low issue).
  Existing tests updated: `tests/test_acp_confsearch_report.py` (add one).
  Acceptance criteria: new test fails pre-fix (orphan JSON present) and passes post-fix; `pytest tests/test_acp_confsearch_report.py tests/test_acp_confsearch_engine_provenance.py -q` green; success-path outputs byte-identical.
  QA scenarios: happy — success outputs unchanged; failure — all-geometry-missing leaves no `final_report.json`, Evidence `.omo/evidence/confsearch-weight-provenance-final-report/task-13-atomicity.log`
  Commit: Y | fix(confsearch): avoid orphan final report on XYZ write failure

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — APPROVE (f1-plan-compliance.md; post-fix re-audit f1-rereview.md)
- [x] F2. Code quality review — APPROVE after fix 84ebca1 (f2-code-quality.md initial REJECT; f2-rereview.md VERDICT: APPROVE)
- [x] F3. Real manual QA — APPROVE (f3-manual-qa.md; 100/100 assertions, 409 focused tests)
- [x] F4. Scope fidelity — APPROVE (f4-scope-fidelity.md; 9/9 must-not checks, zero out-of-scope files)

## Commit strategy
- Atomic conventional commits per todo (`fix(confsearch):`, `feat(confsearch):`, `test(confsearch):`, `docs(...)`) — each todo's Commit line says Y/N.
- Never commit `.omo/` evidence, `*.log`, or generated job data; stage only source + tests + docs.
- Run `ruff check src tests` + `ruff format --check src tests` on touched files before each commit (project gate; format files when needed).

## Success criteria
- A completed Confsearch job's `confsearch_manifest.json`/`boltzmann.json`/`final_report.json` declare the correct weight source per policy: rank1 → CENSO/xTB `p₁` (single refined entry, weight ≠ 1.0 when p₁<1); cumulative-99/all → DFT; screen/pure-xTB → CENSO/xTB; temperature equals the workflow's resolved temperature.
- `final_report.json` + `final_conformers.xyz` exist for all four protocols, carry refined geometry refs + thermo-corrected energy + weights, and are registered in `RESULT/result_manifest.json` without dropping the delegated workflow's existing products.
- The structure viewer inspector shows the correct weight together with its source and temperature; the energy-graph conformer inspector shows "Boltzmann 权重" (no raw `weight` duplicate); legacy manifests still render via the fill-only-missing path.
- `pytest -m "not slow"` passes; the new regression tests fail on the pre-change behavior (verified by the QA scenario of T2/T5).
