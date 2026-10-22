# A-req-04 — Freeze candidate-search input classes and acceptance criteria

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** A (candidate generation/search science authority)
- **Severity:** blocking for T-30b (`experience:research-loop-live/search`)

## Consumer step

T-30b (`candidate search, this period's scope`). C saves the search trace and
presents it; A approves the science; B proves actual execution. C must not
declare search capability until this contract and B-req-15 are
`integration_ready`.

## Owner

A — initial-input classes, candidate sources, generation steps, rejection
reasons, validators and success criteria.

## Current schema revision

No committed A search contract is visible; T-30b is
`NOT_VERIFIED(search_capability)`.

## Expected schema revision

`acp2.science.candidate-search` **v1.0.0**, covering:

- accepted initial-input **classes** (default excludes TS seeds);
- candidate **sources** and permitted generation steps;
- a hard candidate-count cap;
- explicit rejection reasons (not free text);
- the validator identity/version and the success criterion;
- a trace schema that distinguishes *generated* from *executed* candidates.

## Minimal input

```json
{
  "search_id": "search-da-001",
  "input_classes": ["reactant_complex"],
  "candidate_sources": ["systematic_rotation", "conformer_ensemble"],
  "max_candidates": 8,
  "generation_steps": ["embed", "prune_by_rmsd"],
  "reject_reasons": ["steric_clash", "duplicate_geometry"],
  "validator": {"id": "A-search-validator", "version": "1.0.0"},
  "success_criteria": ["at_least_one_valid_ts_candidate", "all_candidates_traceable"]
}
```

## Expected success output

Each generated candidate is traceable to the declared input class/source and
each rejection carries a declared reason; the validator is named by version:

```json
{
  "search_id": "search-da-001",
  "generated": [
    {"candidate_id": "cand-001", "source": "systematic_rotation", "disposition": "accepted", "trace_ref": "trace-001#0"},
    {"candidate_id": "cand-002", "source": "conformer_ensemble", "disposition": "rejected", "reject_reason": "duplicate_geometry"}
  ],
  "validator": {"id": "A-search-validator", "version": "1.0.0"},
  "cap_ok": true
}
```

## Failure output

An undeclared input class (a TS seed), an undeclared reject reason, or a
success criterion that is merely "the model agrees" is rejected:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "undeclared_search_input_class",
    "value": "transition_state_seed"
  },
  "input": {
    "input_classes": ["transition_state_seed"],
    "success_criteria": ["model_agrees"]
  }
}
```

## Compatibility requirement

- Additive only; the accepted-class list and rejection vocabulary are frozen
  before T-30b starts.
- A model summary or multiple LLM roles agreeing is never a success criterion;
  only the named validator can produce the verdict.
- If A has not delivered this contract, C records
  `NOT_VERIFIED(search_capability)` and T-34 must not claim search capability.

## Evidence path

`$ACP2_EVIDENCE/C/research-loop-live/<run_id>/experience/search/` with the
search trace referenced by digest.
