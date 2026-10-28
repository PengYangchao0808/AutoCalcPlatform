# ACP2 Plan A — DA Research Candidate Search Contract (A-14b)

Status: **frozen input contract for `B20a-search`.** This contract is A's science
input to the platform/runtime owner; B owns search execution, the run key and the
search evidence row. A defines what a search must report and when it may be
called a success. Writing this contract executes nothing, proves nothing and
leaves H4 open.

Consumers and edges:

- producer of this contract: `A-14b` (todo 28);
- consumer: `B20a-search` (`../cross-track-dependencies.json` row
  `B20a-search:search-run-evidence`);
- adjudicator: `A-20` (todo 37);
- companion scientific scope: `ADR-008-input.md`.

The v1 slice remains the limited Diels-Alder candidate
`butadiene + ethene -> cyclohexene`; the only admissible questions are
`path_connectivity` and `activation_energy` under the frozen parameters in
`ADR-008-input.md` §2. No endo/exo selectivity, complete mechanism, experimental
rate, global minimum or general-accuracy claim is produced by search.

## 1. Input categories

Search accepts only a user-declared chemical request in the frozen reaction
contract vocabulary. Anything outside this table is rejected explicitly; no
best-effort parser and no empty structure.

| Input category | Accepted values / fields | Search meaning |
|---|---|---|
| Reaction endpoints | `mol`, `sdf`, `smiles`, `structure_upload`; explicit stable atom ids on both sides | the user declares the target transformation and its atom identity |
| Question | `path_connectivity` and/or `activation_energy`; comparison conditions from question contract v1 | the user declares what evidence is sought, not how to find it |
| Starting-structure hint (optional) | one user-supplied starting geometry or TS seed in a supported format, labelled `user_input` | a seed to be evaluated or a reference; never a search result |
| Frozen execution parameters | the §2 parameters in `ADR-008-input.md` | method/basis/state/conditions are input, not search choices |

CDXML is `DEFERRED` and is not an input category. A request with unidentified
atoms, duplicate atom ids, an unmapped atom, an empty product side, a charge or
element mismatch, or an endo/exo selectivity question is rejected at the contract
boundary (§4).

## 2. Candidate source and count

Every candidate must carry exactly one source label and a stable identity:

| Source label | Producer | Allowed in the v1 search row |
|---|---|---|
| `user_input` | the user; parser/normalizer may only normalize declared fields | yes, as the declared seed/endpoints |
| `frozen_known_candidate` | a digested structure committed before the run | yes only as a comparison/control structure; **it is not a search success** |
| `generated_candidate` | the bounded proposal step in §3 | yes; this is the only source that can support a genuine search claim |

Counts and caps:

- the frozen v1 cap is `max_candidate_paths = 1`, `max_ts_candidates = 1` and
  `max_conformers_per_endpoint = 1` (`fixture-manifest.json::candidate_cap`);
- a search row must record the requested, proposed, accepted and rejected counts
  per source label; the proposed count may never exceed the declared cap;
- exceeding the cap stops the search and is recorded as a rejected proposal with
  reason `candidate_cap_exceeded`; the over-cap structures are not scored;
- a `generated_candidate` count of zero with a known candidate used to pass the
  control chain is not search evidence.

Any deferral of search is allowed only with an owner-ratified
`search_scope = DEFERRED` synchronized across A/B/C and the main plan; a deferral
does not permit a control-loop PASS to stand in for search.

## 3. Generation steps

The proposal step is bounded and deterministic in its record-keeping, even when a
generator itself is stochastic:

1. **Parse and normalize** the user input under the reaction contract; assign or
   preserve stable atom ids and record the declared atom order.
2. **Validate the request** for supported input category, complete endpoints,
   one-to-one mapping, element/charge conservation and declared unknowns.
3. **Enumerate within the cap**: at most one candidate path, one TS candidate per
   path and one input conformation per endpoint for the v1 slice. No unbounded
   conformer scan, MD sampling or Monte-Carlo sampling.
4. **Propose** each candidate with source label, generator/seed identity, count
   and the parameters that must match the frozen conditions.
5. **Screen** each proposal against §4 before spending a QC invocation; record
   the exact rejection reason and retain the rejected candidate identity.
6. **Validate** accepted candidates with the §5 deterministic validators and
   record the four-valued status plus every missing check.
7. **Stop or revise** within the frozen budget. Stopping because the cap, budget
   or deadline is reached is a recorded outcome, not a success; no new step starts
   after a stop is accepted (see `ADR-008-input.md` §3).

Generation must not silently convert an unmappable or ambiguous request into an
unbounded search. If the mapping or candidate identity is indeterminate, search
stops and emits the gap.

## 4. Screening and rejection reasons

A proposal is rejected before QC dispatch for exactly one recorded primary
reason (secondary observations may be attached, but the primary reason is
stable):

| Reason id | Meaning |
|---|---|
| `unsupported_input_category` | format outside `mol`/`sdf`/`smiles`/`structure_upload` (including CDXML) |
| `uninterpretable_input` | parser cannot identify atoms/elements; not the same as a valid-but-not-applicable reaction |
| `missing_product` | empty or absent product side |
| `missing_reactant` | empty or absent reactant side |
| `duplicate_atom_id` | duplicate id inside one structure |
| `duplicate_map` | one atom claimed more than once by a mapping direction |
| `map_not_inverse` | `left_to_right` and `right_to_left` are not exact inverses |
| `mapping_coverage_gap` | an atom is neither mapped nor declared unmapped |
| `element_mismatch` | element/stoichiometry counts differ across sides |
| `charge_mismatch` | net charge differs across sides |
| `ambiguous_stereo` | a claimed R/S/E/Z assignment contradicts a declared-unknown stereo fact (an explicit `unknown` is preserved, not rejected) |
| `unknown_condition_undeclared` | a null condition is not declared in `unknown_fields` |
| `method_or_state_mismatch` | proposal cannot be evaluated under the frozen method/electronic state |
| `candidate_cap_exceeded` | proposed candidates exceed the §2 cap |
| `budget_exhausted` | no QC invocation/attempt/task-ref/queue budget remains |
| `generator_failed` | generator returned no usable candidate or an internal error |

`uninterpretable_input` and `representable_not_applicable` stay distinct: the
former is a contract boundary rejection, the latter a valid reaction the current
question/recipe cannot use. Neither is coerced into the other.

A rejection is evidence: the search row must keep the count, the reason id and
the candidate identity so a later reviewer can reproduce the decision. A rejected
proposal is never re-labelled as accepted because a later step succeeded.

## 5. Validators

Search consumes the same deterministic validators as the control chain; it does
not define a second scientific checker. Every validator returns exactly one of
`supported`, `refuted`, `inconclusive`, `not_evaluated`, with missing check ids
and a reason.

| Validator | Required evidence | Claim it can support |
|---|---|---|
| Request/mapping contract | parsed reaction, stable atom ids, one-to-one exact-inverse mapping, complete coverage | the proposal is a well-formed candidate for the declared endpoints |
| Element/charge conservation | side elements, formulas and net charge | the proposal is stoichiometrically admissible |
| Stationary-point / convergence | optimize result and convergence declaration | this optimization converged (not "no stationary point exists") |
| TS frequency | frequency result with exactly one significant imaginary mode and a mapped mode | a local first-order saddle **candidate** |
| Bidirectional IRC | forward and reverse IRC with endpoint geometries and bond-topology mapping | connectivity of the declared candidate path (one direction alone is insufficient) |
| Energy comparability | method, basis, electronic state, temperature, pressure, standard state, reference state and energy unit all match | the activation energy is comparable under the frozen conditions |
| Thermochemistry | frequency-derived thermochemistry under compatible conditions | `ΔG‡` under the frozen conditions |

Numeric evidence must come from a real authorized execution. Constructed,
illustrative or copied values are labelled constructed and cannot support a
claim. A frequency or IRC step cannot be marked optional while connectivity is
claimed.

## 6. Success criteria (search evidence row)

A search row is `PASS` only when **all** of the following hold:

1. The input category and all input identities are recorded, and the input is
   within the supported categories in §1.
2. Every starting structure has exactly one source label (`user_input`,
   `frozen_known_candidate`, `generated_candidate`), and at least one accepted
   candidate is `generated_candidate`.
3. Requested/proposed/accepted/rejected counts per source are recorded and do not
   exceed the §2 cap; every rejection has a §4 reason id and candidate identity.
4. The generation steps in §3 are recorded with generator/seed identity and the
   frozen parameters.
5. Every accepted candidate passed the §5 validators; the claim statuses and
   missing checks are recorded separately from execution terminal state.
6. The search outcome is one of: `supported` with a qualified generated
   candidate, `refuted` with recorded contradictory evidence, or `inconclusive`
   with the exact missing evidence; `not_evaluated` is not a success.
7. The row is machine-distinguishable from the control/execution-chain row and
   carries its own run identity, artifacts and status.
8. A known TS or frozen candidate used to exercise the control chain is **not**
   counted as a generated candidate and **never** turns the search row into a
   `PASS`.

Search is a separate evidence row; it is not proven by a control-loop `PASS`.
A search `PASS` does not by itself close H4: the other H4 rows (real TS,
bidirectional IRC, comparable energy, honest negatives) still apply, and a
missing environment keeps the row `NOT_VERIFIED` with the exact gap.

## 7. Failure and evidence rules

- The search row and the control row are separate records; one cannot overwrite or
  stand in for the other.
- A search that finds no qualified candidate is published honestly as
  `inconclusive` (or `refuted` when evidence contradicts the candidate) together
  with every rejection reason; an example structure is never substituted.
- Wall-clock accounting follows `ADR-008-input.md` §3: queue wait counts toward
  the science and aggregate budgets; after an accepted stop no new step/retry is
  dispatched; the reserved export window is export-only.
- No search claim is derived from two single points, a single imaginary
  frequency, one IRC direction or an unmeasured budget.
- The v1 slice makes no endo/exo, complete-mechanism, alternative-path,
  experimental-rate, global-minimum or general-accuracy claim, regardless of the
  search outcome.
