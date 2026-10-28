# M4-04 — Limited Diels–Alder science vertical (ADR-008)

- generated: `2026-10-27T22:21:34Z`
- git: `34b71aa8230ca7ee411456918aea90b3c84a022c`
- scenario_result: **PASS**
- claim_status: **refuted**
- H4: **open**

## Executed level

- declared ADR-008 level: `wB97X-D4/def2-TZVP`
- executed level: `r2SCAN-3c/def2-mTZVPP` — Amendment A R6 reference-level variant (deviation recorded, no equivalence claimed)
- ORCA: `{'name': 'orca', 'executable': '/home/xieningke/orca611/orca', 'version': 'Program Version 6.1.1  -  RELEASE   -', 'source': 'env:CONFSEARCH_ORCA_PATH', 'is_expected': True}`
- xTB: `{'name': 'xtb', 'executable': '/home/xieningke/xtb-dist/bin/xtb', 'version': "* xtb version 6.7.1 (edcfbbe) compiled by 'albert@albert-system' on 2024-07-22", 'source': 'env:CONFSEARCH_XTB_PATH', 'is_expected': True}`

## TS-guess route (ADR-008 Amendment A)

- route: `route.diels_alder.ts_guess.pes_relaxed_scan_frame_batch_optimize_ts.v1`
- reference case: PESsearch `20261014_165743_003_PESsearch` → BatchOptimize `20261014_170039_004_BatchOptimize`
- frame rule: first converged, constraint-satisfied frame with reaction_progress >= 0.5
- selected frame: `5` (reaction_progress `0.6329311284384961`, 21 frames)
- stages: pes_search=completed (281.07 s), batch_optimize_ts=completed (521.33 s)
- deviation: R6 reference-level variant: the whole chain (steps 1-9) executes at r2SCAN-3c/def2-mTZVPP because the frozen-level TS optimization did not converge within the declared budget. All energy participants share this level; no equivalence to wB97X-D4/def2-TZVP is claimed. The frozen level attempt is preserved separately and its numbers are never merged into this claim.

## ADR-008 steps

| step | entry | status | seconds | task ref |
| --- | --- | --- | --- | --- |
| endpoint_prep | legacy_task_core | completed | 367.31 | 20261028_940_endpoint_prep_butadiene + 20261028_940_endpoint_prep_ethene + 20261028_940_endpoint_prep_cyclohexene |
| candidate_path | platform_workflow_route | completed | 802.4 | 20261028_945_candidate_path |
| candidate_frequency | bounded_recipe_envelope | completed | 94.2 | 20261028_941_candidate_frequency |
| ts_optimize | route_batch_optimize_ts | completed | 521.33 | 20261028_945_candidate_path_ts |
| ts_frequency | bounded_recipe_envelope | completed | 94.51 | 20261028_941_ts_frequency |
| irc | legacy_task_core | completed | 2454.71 | 20261028_940_irc |
| endpoint_reactants | bounded_recipe_envelope | completed | 8.16 | 20261028_941_endpoint_reactants |
| endpoint_product | bounded_recipe_envelope | completed | 8.17 | 20261028_941_endpoint_product |
| energy_thermochemistry | deterministic_analysis | completed | 0.0 | 20261028_942_energy_thermochemistry |

## Criteria (scenario_result vs claim_status)

| criterion | scenario_result | claim_status | applied rules |
| --- | --- | --- | --- |
| DA-C01-ts-single-imaginary-mode | PASS | supported |  |
| DA-C02-ts-mode-mapping | PASS | supported |  |
| DA-C03-frequency-evidence | PASS | supported |  |
| DA-C04-irc-bidirectional | PASS | supported |  |
| DA-C05-endpoint-association | PASS | refuted |  |
| DA-C06-energy-comparability | PASS | supported |  |
| DA-C07-reactant-stoichiometry | PASS | supported |  |

## Questions

- `question.diels_alder.path_connectivity.v1` → **refuted**
- `question.diels_alder.activation_energy.v1` → **supported**

## Public bounded-recipe entry

- compiled: `False`
- error: `{'code': 'unsupported_step_kind', 'detail': 'unsupported_step_kind: endpoint_prep: pure-analysis/non-dispatchable steps are not dispatchable bounded DAG steps', 'type': 'StepDagError'}`
- open steps: `['endpoint_prep', 'candidate_path', 'energy_thermochemistry']`

## Evidence chain

- report digest: `sha256:252b1c552953b3ca7d55fa9676b62f5a00489e7b8f21112508680b5b2095c147`
- platform attribution verified: `True`
- platform decision: `revise`
- RPH decision: `revise`
- decisions agree: `True`
- workbench available: `True`

## Degradation scenarios

- `single-irc-direction` → passed: `True`
- `missing-frequency` → passed: `True`
- `endpoint-mismatch` → passed: `True`
- `forged-supported-record` → passed: `True`

## Projection attempt (single-task worker)

- entry: `single_task_worker_envelope`
- status: `completed`
- task ref: `20261028_943_projection_attempt`
- receipt: `completed`
- native digests: `native-digests.json`

## H4

- state: **open**
  - the Local DA row is open: the row is open: missing required receipt(s) walltime_process_termination, terminal_usage_receipt
  - the LSF DA row is NOT_VERIFIED (M4-03 dependency)
  - the budget timing basis is unmeasured (ADR-008 §6)

## Limitations

- one declared candidate path, one conformer per endpoint: a declared boundary
- a known candidate carried through the chain is never a search result
- no experimental rate, no global minimum, no general method accuracy claim
- the bounded-recipe DAG cannot host the frozen nine-step recipe today: the nine steps ran as nine attempts sharing one release, so the evidence closure is per step and not yet one-TaskRef/nine-step
- the bounded-DAG attempt writes per-step evidence, not a task-level task_result_digest: the platform projection is therefore bound to the single-task worker attempt (recorded separately)
- Shermo is not installed on this host: the Gibbs activation barrier is not claimed (the electronic barrier is)
