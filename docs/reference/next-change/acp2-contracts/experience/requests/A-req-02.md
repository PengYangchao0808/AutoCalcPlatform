# A-req-02 — CDXML owner and stage scope (structure/reaction semantics)

- **Status:** open
- **Raised by:** C (T-23 / C17a), 2026-10-23
- **Requested of:** A (owner of the Recipe / capability science contract)
- **Severity:** blocking for CDXML/ChemDraw input only; the P2 frozen input
  range (MOL / SDF / SMILES + structure upload) is unaffected

## Consumer step

T-23 (`QuestionSpec/ReactionSpec binding`), T-24 (ambiguity confirmation),
T-34 (DA / selectivity) and every later round that would accept a ChemDraw
starting point. C freezes the P2 input range to MOL / SDF / SMILES + the
existing structure upload and classifies CDXML as `DEFERRED`; C must not
claim ChemDraw input is delivered.

## Owner

A — structure/reaction semantics authority for the CDXML format. A owns the
format semantics (species, atom mapping, charge/spin, stereo, reaction
components); B (see `B-req-09`) owns the authorized upload + parse entry. C
consumes both and never implements CDXML parsing in the browser.

## Current schema revision

No CDXML structure/reaction semantics are visible from the C worktree. The
Workbench classifies `cdxml` input as:

```json
{
  "format": "cdxml",
  "status": "DEFERRED",
  "supported": false,
  "claim": "not_supported_until_A-req-02_and_B-req-09"
}
```

A deferred input is recorded by reference only and is never parsed into an
(in particular, never an empty) structure.

## Expected schema revision

`acp2.science.cdxml-semantics` **v1.0.0** (or A's equivalent versioned ID)
plus a published digest, covering at least:

1. species identity and atom identity preserved from the CDXML document
   (atom labels / references are carried, never silently renumbered);
2. atom-atom mapping identity for reaction components (mapping numbers are
   part of the scientific identity; no silent rewrite);
3. `charge` and `spin_multiplicity` per species with the electron-parity rule
   (missing values stay missing — no silent fill);
4. stereo descriptors and their preservation across conversion;
5. reaction components: explicit reactants / products / candidate elementary
   steps, and the net-reaction vs elementary-step distinction;
6. declared input format + byte digest for the input document
   (changing bytes changes `plan_definition_digest`).

## Minimal input

```json
{
  "question_ref": "question-cdxml-001",
  "reaction_ref": "reaction-cdxml-001",
  "science": {
    "recipe_digest": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "method": "r2SCAN-3c",
    "mapping": "mapping-cdxml-v1",
    "charge": 0,
    "multiplicity": 1,
    "initial_inputs": [
      {
        "input_id": "input-cdxml-1",
        "sha256": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "format": "cdxml",
        "role": "reactant"
      }
    ]
  }
}
```

## Expected success output

A versioned CDXML semantics contract whose validator converts the document
into the frozen ReactionSpec projection (atom labels / mapping / charge /
spin / stereo preserved, reactants / products / candidate elementary steps
explicit) and returns its digest:

```json
{
  "result": "accepted",
  "schema": "acp2.science.cdxml-semantics",
  "schema_version": "1.0.0",
  "mapping": "mapping-cdxml-v1",
  "reaction_kind": "net_reaction",
  "candidate_step_count": 2
}
```

## Failure output

A validator that silently renumbers atoms, rewrites mapping/stereo, or fills a
missing charge/spin with a default is rejected; C's Workbench keeps the input
`DEFERRED` and returns `input_invalid`:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "silently_rewritten_identity",
    "path": "science.mapping"
  },
  "input": {
    "science": {
      "mapping": "auto-generated-without-authority",
      "charge": 0
    }
  }
}
```

Also rejected:

```json
{"science": {"initial_inputs": [{"input_id": "x", "sha256": "sha256:aaaa...64hex", "format": "cdxml"}], "charge": "0"}}
```

(a string charge, or a CDXML input claimed as parsed without A's delivered
semantics, is a contract violation — C must keep `status: "DEFERRED"` rather
than accept it.)

## Compatibility requirement

- Additive within v1: new optional fields are allowed; renaming or retyping
  the mapping/identity shapes requires a major version.
- Delivering CDXML semantics alone does NOT enable CDXML input: B must also
  ship the authorized upload + parse entry (`B-req-09`). Only then may C
  change `cdxml` from `DEFERRED` to accepted.
- Until both land, every C surface keeps claiming `DEFERRED`, never
  "ChemDraw input supported".

## Evidence path

`$ACP2_EVIDENCE/C/question-reaction-fixtures/<run_id>/` — the T-23 run whose
fixtures pin the `DEFERRED` classification and the never-parsed-empty-structure
behavior; later CDXML enablement must cite this request plus `B-req-09`.
