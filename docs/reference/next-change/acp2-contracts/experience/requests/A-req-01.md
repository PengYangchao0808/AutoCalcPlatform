# A-req-01 — Close the science-field / unit / charge-spin semantic gaps

- **Status:** open
- **Raised by:** C (T-03 / C01b), 2026-10-22
- **Requested of:** A (owner of the Recipe / capability science contract)
- **Severity:** blocking for T-15 (science binding) and the Reaction Builder

## Consumer step

T-15 (`Science binding / Reaction Builder`) and, downstream, T-30 (`research
loop`) and T-40 (`DA / selectivity`). C builds the Proposal `science` block
(`proposal.schema.json#/$defs/science`) by referencing A's frozen Recipe; it
must not invent defaults.

## Owner

A — Recipe/capability science authority. C does not edit A's files; this
request records the gap and the expected revision.

## Current schema revision

No committed A science schema is visible from the C worktree. The T-02/T-03
baseline records `NOT_VERIFIED(a_science_handoff)`; C references the recipe by
digest only (`recipe_digest`).

## Expected schema revision

`acp2.science.recipe` **v1.0.0** (or A's equivalent versioned ID) plus a
published digest, covering at least:

1. every quantity with an explicit `{value, unit}` pair (no bare numbers, no
   implicit hartree/kcal ambiguity);
2. `charge` as an integer total charge and `spin_multiplicity` as an integer,
   with the electron-parity rule stated;
3. the atom/geometry **mapping identity** (C must not infer endo/exo or atom
   correspondence from a molecule name);
4. the initial-input class/role (e.g. `transition_state_guess`,
   `reactant_complex`) and its byte digest requirement;
5. explicit defaults: a field omitted by the model must not be silently
   defaulted by A, C or the backend.

## Minimal input

```json
{
  "question_ref": "question-da-selectivity-001",
  "reaction_ref": "reaction-butadiene-ethene-001",
  "science": {
    "recipe_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "method": "r2SCAN-3c",
    "mapping": "mapping-butadiene-ethene-endo-v1",
    "charge": 0,
    "spin_multiplicity": 1,
    "initial_inputs": [
      {
        "input_id": "input-ts-guess-endo",
        "sha256": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "role": "transition_state_guess"
      }
    ]
  }
}
```

## Expected success output

A versioned Recipe contract whose validator accepts the minimal input and
returns the frozen Recipe digest plus the canonicalized science projection:

```json
{
  "result": "accepted",
  "recipe_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
  "normalized_science": {
    "charge": 0,
    "spin_multiplicity": 1,
    "temperature": {"value": 298, "unit": "kelvin"}
  }
}
```

## Failure output

A validator that accepts a string charge or a bare float quantity must be
rejected; C's Proposal validation returns `input_invalid`:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "non_canonical_science_value",
    "path": "science.charge"
  },
  "input": {"science": {"charge": "0", "spin_multiplicity": 1}}
}
```

Also rejected:

```json
{"science": {"barrier": {"value": 1.5, "unit": "hartree"}}}
```

(`1.5` is a JSON float and `barrier` is not a declared unit-bearing field; both
are contract violations, not values to round or rename.)

## Compatibility requirement

- Additive only within v1: new optional fields are allowed; renaming or
  retyping `charge`/`spin_multiplicity`/quantity shapes requires a major
  version.
- A's validator must reject rather than coerce a string/number ambiguity.
- C's `recipe_digest` reference must remain stable for an unchanged Recipe.

## Evidence path

`$ACP2_EVIDENCE/C/contracts/<run_id>/` — the run that records
`A-req-01` consumption and the `NOT_VERIFIED(a_science_handoff)` state until A
publishes the revision.
