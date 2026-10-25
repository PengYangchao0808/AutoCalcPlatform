# ACP2 Plan A — ADR-008 Input: Limited Diels-Alder Science Scope (A-14b)

Status: **input for the platform owner's shared ADR-008.** A (science track) owns
this scientific text; B aggregates ADR-008 and owns its platform decisions. This
document does not execute anything, does not issue a run key and does not claim a
result. Its facts come from the frozen fixture
`tests/fixtures/acp2/science/diels_alder/v1/` (commit `ef207f5`) and the frozen
reaction/question contract v1.

Reference rows: `Plan A R3-11`, `Plan A R5-04`, `Plan A §A-14/A-17`,
`docs/reference/next-change/ACP_2.0_MCP_DSH_Server_Acceptance_Runbook_20261020.md`
(queue/stop/export rules). The machine companion is
`../cross-track-dependencies.json` rows `A-14b:search-contract` and
`B20a-search:search-run-evidence`.

## 1. Scientific question and candidate

The first and only active candidate in this slice is the net reaction

```text
buta-1,3-diene (C4H6) + ethene (C2H4) -> cyclohexene (C6H10)
```

at 16 declared atoms, with the exact one-to-one atom mapping in
`reaction.json`. The intended candidate is one elementary (concerted) step, but
the frozen `reaction_scope` is **`net_reaction`** and the elementary-step claim is
**not asserted**: reaction contract v1 carries no TS/IRC evidence field. The
intent is recorded only as `scope.intended_candidate_kind =
"single_elementary_concerted_step"`.

Only two questions are admitted by `question.json`:

| Question id | Type | What it can say |
|---|---|---|
| `question.diels_alder.path_connectivity.v1` | `path_connectivity` | whether the declared candidate path connects the specified endpoints, with TS/frequency/bidirectional-IRC/endpoint evidence |
| `question.diels_alder.activation_energy.v1` | `activation_energy` | the activation energy under the frozen method/conditions and compatible reference state |

No other question type, and no named-reaction-specific execution engine, is
authorized by this input.

## 2. Frozen scientific parameters (pre-registered)

These values are frozen **before** any real run; a later ADR or run-spec change
requires a new version and a new approval, not an inline edit.

| Parameter | Frozen value |
|---|---|
| Method | `wB97X-D4` |
| Basis | `def2-TZVP` |
| Phase / solvent | gas phase, no implicit solvent (`solvent: null`) |
| Electronic state | neutral closed-shell singlet, charge `0`, multiplicity `1`, `state_id = state.singlet.s0` |
| Temperature | `298.15 K` |
| Pressure | `100000 Pa` |
| Standard state | `1_bar_ideal_gas` |
| Energy unit | `hartree` for raw energies; comparisons in `kcal_per_mol` |
| Reference state | separated butadiene + ethene in the gas phase at infinite separation, and cyclohexene, all at the frozen conditions |
| Activation-energy definition | `ΔE‡ = E(TS) - [E(butadiene) + E(ethene)]`; `ΔG‡` additionally uses the frozen frequency thermochemistry at 298.15 K / 1 bar; no tunneling correction; no experimental-rate claim |
| Candidate cap | `max_candidate_paths = 1`, `max_ts_candidates = 1`, `max_conformers_per_endpoint = 1` |
| Sampling | one conformation per endpoint; no conformer search, MD or Monte-Carlo sampling; one declared candidate path |
| Backend | `orca` preferred; `TaskRequest.backend` stays `null` so cccp capability selection remains deterministic |
| Tolerances | atom count `0`; coordinate `1e-3 Å`; imaginary-mode threshold `0.0 cm-1`; low-frequency threshold `50.0 cm-1`; activation-energy comparison `0.5 kcal/mol` (constructed, not measured) |

The fixture is **constructed** data: no optimized TS geometry, no measured energy
and no experimental reference value exists. Every number that is a ceiling or a
tolerance is labelled as constructed where applicable.

## 3. Resource budget and evidence-time rules

| Item | Frozen value | Status |
|---|---|---|
| `science_deadline_seconds` | `21600` | constructed |
| `export_budget_seconds` | `600` | constructed |
| `aggregate_budget_seconds` | `22200` (`= science + export`) | constructed |
| Shared `started_at` | `2026-10-23T00:00:00Z` (equals `frozen_at`; not a run timestamp) | constructed |
| `max_qc_invocations` | `10` | constructed |
| `max_task_refs` | `10` | constructed |
| `max_attempts_per_step` | `2` | constructed |
| `max_bsub` | `1` | constructed |
| Budget timing basis | `unmeasured`; `measured_step_timings: null`; `timing_evidence: []` | honest gap |

**Queue-wait accounting.** Queue wait counts toward total wall time. The clock
starts at the shared `started_at`; waiting for a scheduler slot consumes both
`science_deadline_seconds` and `aggregate_budget_seconds`. The reserved export
window is excluded from queue accounting.

**In-flight stop rule.** After a stop/cancel request is accepted, no new step or
retry may be dispatched. The in-flight step is stopped with confirmation, and its
partial artifacts are not accepted as scientific evidence.

**Reserved evidence-export window.** A `600 s` window starts after the last step
reaches a terminal state (or after stop confirmation). It is reserved for
evidence export only: it never extends the science deadline and never dispatches
new science.

**H4 consequence.** Because the timing basis is `unmeasured`, the ceilings above
are constructed, not derived from measured representative step timings. H4
therefore remains **OPEN** until representative step timings are recorded and the
full-chain budget is derived from them before a real run.

## 4. Execution-chain validation versus research candidate search

ADR-008 must keep two capabilities as separate evidence rows; a PASS in one never
implies the other.

### 4.1 Execution-chain validation (control)

Goal: prove that a known, correctly described starting point is carried through
the existing execution stack - task submission, TS optimization, frequency,
bidirectional IRC, endpoint mapping and energy/thermochemistry comparison - and
that failures are classified honestly.

- Starting geometry/candidate is **known and frozen before dispatch**.
- The method, basis, electronic state, solvent, temperature, standard state and
  tolerances in §2 are fixed; they are not chosen per candidate.
- Pre-registered criteria in §5 decide whether the chain is supported,
  refuted or inconclusive.
- A control-loop PASS proves **execution/validation capability only**. It does not
  prove search capability, and it does not close H4 by itself.

### 4.2 Research candidate search

Goal: start from a user-declared chemical input and, within a bounded budget,
propose candidate paths/TS structures, consume validator evidence and stop or
revise. The executable contract is `search-contract.md`.

- Input is a user request (reaction endpoints and question) in a supported input
  category (`mol`, `sdf`, `smiles`, `structure_upload`).
- Proposals are bounded: at most the frozen candidate cap in §2 for the v1
  slice; every proposal has a source, a count and a recorded rejection reason.
- A search row must satisfy the success criteria in `search-contract.md` before
  any search claim is made.
- **A known TS is never a search success.** Presenting a pre-frozen TS (or a
  control-loop PASS) as evidence of research search is a failed claim, even when
  the known TS passes every validator.
- Search is a **separate evidence row**; it is not proven by a control-loop PASS
  and does not inherit a control PASS.

## 5. Starting-structure provenance (mandatory label)

Every starting structure presented to either capability must carry exactly one
label. B/C may add display metadata, but may not drop the label.

| Label | Meaning | In this fixture |
|---|---|---|
| `user_input` | structure supplied by the user through a supported input category, unmodified except for declared parsing/normalization | none yet; required for a search row |
| `frozen_known_candidate` | a structure committed and digested before the run, whose provenance is known and which is not claimed to be discovered by search | `reactants.xyz` and `product.xyz` are constructed frozen candidates; any pre-frozen TS candidate for control is also `frozen_known_candidate` (constructed, not experimental) |
| `generated_candidate` | a structure produced by the bounded proposal step from user input during this run, with generator identity and count recorded | required for a genuine search row; none exists in the static fixture |

The static fixture contains **no** user input and **no** generated candidate. Its
sampling boundary says so explicitly: one constructed path, one conformation per
endpoint, one TS candidate. A real A-17/A-18/A-19 audit must state which label
applies per structure; a known candidate for the execution chain must not be
reported as a generated search candidate.

## 6. Pre-registered success, failure and status criteria

The three records stay separate: scenario `result` (did validation complete with
valid artifacts), scientific claim `status` (four-valued), and H4 closure.

| Criterion id | Requirement | Pre-registered status before a real run |
|---|---|---|
| `DA-01-parse` | all six fixture documents parse and satisfy contract v1 | `met_by_static_fixture` |
| `DA-02-unique-mapping` | mapping unique, one-to-one, exact inverse with full coverage | `met_by_static_fixture` |
| `DA-03-conservation` | elements and net charge conserved | `met_by_static_fixture` |
| `DA-04-no-selectivity-request` | no endo/exo request or claim anywhere | `met_by_static_fixture` |
| `DA-05-frozen-scope` | method/basis/solvent/temperature/standard state/candidate cap/budget/tolerances frozen before a real run | `met_by_static_fixture` |
| `DA-06-run-spec` | deadlines, shared `started_at`, queue accounting, in-flight stop and export window recorded | `met_by_static_fixture` |
| `DA-07-atom-order` | XYZ atom order is byte-stable against declared `atom_ids` order | `met_by_static_fixture` |
| `DA-08-real-ts` | a real TS candidate has exactly one imaginary frequency with a mapped mode | `not_evaluated` |
| `DA-09-real-irc` | bidirectional IRC reaches both specified endpoints with stable atom order | `not_evaluated` |
| `DA-10-real-energy` | activation energy is comparable under the frozen conditions with explicit units | `not_evaluated` |

Claim status vocabulary is exactly `supported`, `refuted`, `inconclusive`,
`not_evaluated`. Execution terminal state (`completed`/`failed`/`cancelled`) is
not a claim status and never maps to `supported`. Missing evidence is
`inconclusive`/`not_evaluated`, not `refuted`; a nonconverged optimization refutes
"this optimization converged", not the stronger proposition that no stationary
point exists in the region. One imaginary frequency proves only a local
first-order saddle candidate, never connectivity; one IRC direction never proves a
bidirectional path.

For the search row, `supported` additionally requires the success criteria in
`search-contract.md`; a search that returns no qualified candidate is
`inconclusive` or `refuted` with the recorded rejection reasons, never
`supported` by substituting a known example.

## 7. Explicit limitations and out-of-scope questions

This fixture and any ADR-008 slice derived from it **cannot** support:

| Out-of-scope question | Reason |
|---|---|
| **Endo/exo selectivity** | the symmetric butadiene + ethene example has no endo/exo distinction; no endo/exo question, field, recipe option or claim is part of this fixture |
| **Complete mechanism** | only one candidate path is declared; no complete mechanism is claimed |
| **Exclusion of alternative paths** | other paths are neither enumerated nor excluded; their absence is not evidence |
| **Experimental rates** | no experimental rate, kinetics or benchmark comparison is predicted |
| **Global minimum** | no global minimum or exhaustive conformer search is established |
| **General method accuracy** | no general accuracy, transferability or benchmark claim is made |

Additional limitations: no real energy, TS geometry or reference value exists; the
one-conformation/one-path/one-TS boundary is a declared scope, not a discovery
claim; the timing basis is unmeasured; and the intended elementary-step character
is intent only. Any of these limitations must remain visible in the evidence
report and in the UI.

## 8. What the platform owner must freeze in ADR-008

ADR-008 is complete for this slice only when it records:

1. the two admissible questions and the exact candidate above;
2. every parameter in §2, including method, basis, solvent, temperature, pressure,
   standard state, electronic state, reference state, units, candidate cap and
   tolerances;
3. the resource budget, its `unmeasured` timing basis and the H4-open consequence;
4. the queue-wait accounting, in-flight stop rule and reserved export window;
5. the execution-chain/search split, the starting-structure provenance labels and
   the rule that a known TS is never a search success;
6. the pre-registered criteria and the four-valued claim vocabulary;
7. every out-of-scope question in §7.

If B cannot freeze these, or if a run key/authorized environment is absent, the
real rows stay `NOT_VERIFIED` with the exact gap. A does not fill the gap with a
placeholder, a mock or a known candidate presented as a search result.
