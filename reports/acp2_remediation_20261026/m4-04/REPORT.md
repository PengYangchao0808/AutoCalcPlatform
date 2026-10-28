# M4-04 — Limited Diels–Alder science vertical (ADR-008)

- generated: `2026-10-27T15:31:36Z`
- git: `02cb71e97eee477d638741bd4c43494d42f9c2db`
- scenario_result: **FAIL**
- claim_status: **inconclusive**
- H4: **open**

## Executed level

- frozen ADR-008 level: `wB97X-D4/def2-TZVP` — used unchanged
- ORCA: `{'name': 'orca', 'executable': '/home/xieningke/orca611/orca', 'version': 'Program Version 6.1.1  -  RELEASE   -', 'source': 'scan', 'is_expected': True}`
- xTB: `{'name': 'xtb', 'executable': '/home/xieningke/xtb-dist/bin/xtb', 'version': "* xtb version 6.7.1 (edcfbbe) compiled by 'albert@albert-system' on 2024-07-22", 'source': 'env:CONFSEARCH_XTB_PATH', 'is_expected': True}`

## ADR-008 steps

| step | entry | status | seconds | task ref |
| --- | --- | --- | --- | --- |
| endpoint_prep | legacy_task_core | completed | 2543.09 | 20261027_940_endpoint_prep_butadiene + 20261027_940_endpoint_prep_ethene + 20261027_940_endpoint_prep_cyclohexene |
| candidate_path | legacy_task_core | failed | 9.64 | 20261027_940_candidate_path |
| candidate_frequency | None | skipped | None | None |
| ts_optimize | None | skipped | None | None |
| ts_frequency | None | skipped | None | None |
| irc | None | skipped | None | None |
| endpoint_reactants | None | skipped | None | None |
| endpoint_product | None | skipped | None | None |
| energy_thermochemistry | deterministic_analysis | partial | 0.0 | 20261027_942_energy_thermochemistry |

## Criteria (scenario_result vs claim_status)

| criterion | scenario_result | claim_status | applied rules |
| --- | --- | --- | --- |
| DA-C01-ts-single-imaginary-mode | FAIL | not_evaluated |  |
| DA-C02-ts-mode-mapping | FAIL | not_evaluated |  |
| DA-C03-frequency-evidence | FAIL | not_evaluated |  |
| DA-C04-irc-bidirectional | FAIL | not_evaluated |  |
| DA-C05-endpoint-association | FAIL | inconclusive |  |
| DA-C06-energy-comparability | FAIL | inconclusive |  |
| DA-C07-reactant-stoichiometry | FAIL | supported |  |

## Questions

- `question.diels_alder.path_connectivity.v1` → **inconclusive**
- `question.diels_alder.activation_energy.v1` → **inconclusive**

## Public bounded-recipe entry

- compiled: `False`
- error: `{'code': 'unsupported_step_kind', 'detail': 'unsupported_step_kind: endpoint_prep: pure-analysis/non-dispatchable steps are not dispatchable bounded DAG steps', 'type': 'StepDagError'}`
- open steps: `['endpoint_prep', 'candidate_path', 'energy_thermochemistry']`

## Evidence chain

- report digest: `sha256:82864b88da2722bf5f4b44f3aa6f683d4ebaa34416e178923366be1f531eed25`
- platform attribution verified: `True`
- platform decision: `revise`
- RPH decision: `escalate`
- decisions agree: `False`
- workbench available: `True`

## Degradation scenarios

- `single-irc-direction` → passed: `True`
- `missing-frequency` → passed: `True`
- `endpoint-mismatch` → passed: `True`
- `forged-supported-record` → passed: `True`

## Projection attempt (single-task worker)

- entry: `single_task_worker_envelope`
- status: `completed`
- task ref: `20261027_943_projection_attempt`
- receipt: `completed`
- native digests: `native-digests.json`

## H4

- state: **open**
  - the Local DA row is open: the row is open: incomplete steps ['candidate_path', 'candidate_frequency', 'ts_optimize', 'ts_frequency', 'irc', 'endpoint_reactants', 'endpoint_product', 'energy_thermochemistry']
  - the LSF DA row is NOT_VERIFIED (M4-03 dependency)
  - the budget timing basis is unmeasured (ADR-008 §6)

## Limitations

- one declared candidate path, one conformer per endpoint: a declared boundary
- a known candidate carried through the chain is never a search result
- no experimental rate, no global minimum, no general method accuracy claim
- the bounded-recipe DAG cannot host the frozen nine-step recipe today: the nine steps ran as nine attempts sharing one release, so the evidence closure is per step and not yet one-TaskRef/nine-step
- the bounded-DAG attempt writes per-step evidence, not a task-level task_result_digest: the platform projection is therefore bound to the single-task worker attempt (recorded separately)
- Shermo is not installed on this host: the Gibbs activation barrier is not claimed (the electronic barrier is)
