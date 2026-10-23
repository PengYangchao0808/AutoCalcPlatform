# H3 receipt — B14b P1 capability × backend matrix

**Candidate:** `77ac1de1af6a5db6b57f4f904a859305ff73042c` (`bb45a46`)

**H3 status: OPEN**

| Capability/backend | Result | TaskRef | Attempt | Release | Chain |
|---|---|---|---|---|---|
| `singlepoint/local` | **PASS** | `20261023_111633_001_singlepoint` | 1 | `28b9a5b5e038090a` | envelope-worker |
| `singlepoint/lsf` | **NOT_VERIFIED** | `None` | None | `None` | envelope-worker |
| `optimize/local` | **PASS** | `20261023_111652_002_optimize` | 1 | `None` | legacy-cli |
| `optimize/lsf` | **NOT_VERIFIED** | `None` | None | `None` | envelope-worker |
| `frequency/local` | **PASS** | `20261023_111946_003_frequency` | 1 | `None` | legacy-cli |
| `frequency/lsf` | **NOT_VERIFIED** | `None` | None | `None` | envelope-worker |

## Open rows
- `singlepoint/lsf` — **NOT_VERIFIED** — owner B; reason: `lsf_prerequisite_missing:bsub,bjobs,bhosts,bqueues,LSF_ENVDIR,ACP_LSF_NODE`
- `optimize/lsf` — **NOT_VERIFIED** — owner B; reason: `lsf_prerequisite_missing:bsub,bjobs,bhosts,bqueues,LSF_ENVDIR,ACP_LSF_NODE`
- `frequency/lsf` — **NOT_VERIFIED** — owner B; reason: `lsf_prerequisite_missing:bsub,bjobs,bhosts,bqueues,LSF_ENVDIR,ACP_LSF_NODE`
- `B10b/three-entry` — **NOT_VERIFIED** — owner B10; reason: `three_entry_workbench_not_verified:c_driver_absent`
- `B11/lsf-normal-completion` — **NOT_VERIFIED** — owner B11; reason: `lsf_prerequisite_missing:ACP_LSF_NODE,LSF_ENVDIR,bsub,bjobs,bhosts,bqueues,ssh_credentials`
- `B13/control-recovery` — **NOT_VERIFIED** — owner B13; reason: `local fault rows PASS; LSF control NOT_VERIFIED; DSH disconnect NOT_VERIFIED`
- `A/scientific-audit` — **NOT_VERIFIED** — owner A; reason: `a_not_available`
- `C/dsh-browser` — **NOT_VERIFIED** — owner C; reason: `c_dsh_browser_absent`
- `T3` — **NOT_VERIFIED** — owner C; reason: `c_dsh_browser_absent`
- `T4/lsf` — **NOT_VERIFIED** — owner B; reason: `lsf_prerequisite_missing`
- `T5/lsf` — **NOT_VERIFIED** — owner B; reason: `lsf_prerequisite_missing`
- `T5/dsh` — **NOT_VERIFIED** — owner C; reason: `c_dsh_browser_absent`

## What was actually proven
- Local singlepoint: real ORCA 6.1.1 H2/STO-3G HF; ORCA terminated normally; energy `-1.116759307204 Ha` matches task_result; no modes inferred.
- Local optimize: real ORCA optimization; `THE OPTIMIZATION HAS CONVERGED`; geometry `H 0.000000 0.000000 0.013863` / `H 0.000000 0.000000 0.726137`; energy `-1.11751709882 Ha`; exactly one ORCA process.
- Local frequency: real ORCA frequency; one mode `5038.6 cm-1`; `n_imaginary=0`; geometry and energy match the ORCA output; exactly one ORCA process.
- The three capabilities are three distinct TaskRefs, not one extrapolated run.

## Caveats
- optimize/local and frequency/local run on the legacy CLI chain; no execution_receipt/release_id is emitted. Their science is real (ORCA output, task_result-equivalent step_result, manifest) but release identity is NOT_EMITTED_ON_LEGACY_CHAIN.
- B10b Workbench, B11 LSF normal completion, B13 LSF control and DSH rows remain NOT_VERIFIED and are listed separately, not collapsed into the capability rows.

## T0–T5
- `T0` environment/version: **PASS** — Rocky Linux 9.8, Python 3.11.13, real ORCA 6.1.1 at /home/<user>/orca611/orca (sha256 d65fe8fe...), contract 1.1.0; LSF absent by design.
- `T1` MCP process: **PASS** — Real stdio MCP adapter (8 tools, protocol 2025-06-18) performed the B14b acp_submit calls on 77ac1de; B05 protocol matrix and B06 entry mapping PASS.
- `T2` platform use case: **PASS** — MCP submit -> Controller -> real Workspace/Runtime/Data Local chains produced three distinct TaskRefs; B06 entry parity and B12 artifact lifecycle PASS.
- `T3` DSH: **NOT_VERIFIED** — C DSH/Workbench P1 is not present on this host; no real official-client handshake is claimed.
- `T4` science/cluster: **NOT_VERIFIED** — Local singlepoint/optimize/frequency PASS with real ORCA; LSF normal completion is NOT_VERIFIED (no bsub/queue/node).
- `T5` fault/boundary: **NOT_VERIFIED** — B13 Local fault/race rows PASS; LSF control and DSH disconnect NOT_VERIFIED.

## Compatibility guard
- 4 passed in 0.62s on candidate 77ac1de — evidence `B/B14b/guard/pytest-15-active-workflows.txt`

## A/C scope
- A scientific audit: **NOT_VERIFIED** (`a_not_available`) — B does not sign A science.
- C DSH/browser: **NOT_VERIFIED** (`c_dsh_browser_absent`) — B does not sign C scope.

## Verdict
P1 is frozen for the Local capabilities, but H3 is **OPEN**: LSF singlepoint/optimize/frequency, C DSH/Workbench, A scientific audit and the historical B10b/B11/B13 open rows are not verified. A Local result is not used as LSF evidence and a singlepoint is not used as optimize/frequency evidence.
