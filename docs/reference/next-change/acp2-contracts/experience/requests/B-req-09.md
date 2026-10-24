# B-req-09 — Authorized CDXML upload + parse entry

- **Request ID:** `B-req-09`
- **Owner / dispatcher:** B02b (upload/parse surface) with B00 (path/allowlist registration)
- **Producer:** B (runtime upload + parse entry), gated on A's CDXML semantics (`A-req-02`)
- **Consumer step:** C/T-23 (Workbench input binding), C/T-24 (parse-failure
  reporting), and every later C todo that would enable a ChemDraw starting point
- **Consumer track:** C (`acp2-plan-c-rph-workbench`)

## Consumer step (detail)

C freezes the P2 input range to MOL / SDF / SMILES + the existing structure
upload and classifies `cdxml` as `DEFERRED`. C only changes that classification
to accepted when BOTH (a) A delivers CDXML structure/reaction semantics
(`A-req-02`) and (b) B ships the authorized upload + parse entry requested
here. Until then no C surface may claim ChemDraw input is supported.

## Owner

B — the platform runtime owns the upload endpoint, authorization and parser
invocation. C never implements CDXML parsing in the browser; the Workbench
records the input by reference and displays parse errors from this entry.

## Current schema revision

No authorized CDXML upload/parse entry is visible from the C worktree. C's
Workbench currently records CDXML input as:

```json
{
  "format": "cdxml",
  "status": "DEFERRED",
  "supported": false,
  "claim": "not_supported_until_A-req-02_and_B-req-09"
}
```

and never sends it to a parser. Status: `NOT_VERIFIED(b_cdxml_entry)`.

## Expected schema revision

B-published `acp2.runtime.upload-parse.v1` (or a later major, versioned and
digested) adding one authorized entry with at least:

1. an upload input for `cdxml` documents bound to the resolved project scope
   (same authorization rules as the existing structure upload);
2. a parse step invoking A's CDXML semantics (`A-req-02`) server-side — never
   a browser-side parse;
3. explicit failure reporting: a parse failure is an error, NEVER an empty
   structure accepted for submit;
4. the parsed projection returning preserved atom labels / mapping / stereo,
   explicit reactants / products / candidate elementary steps, charge/spin
   (missing stays missing), and the input document digest;
5. an idempotent upload identity (same bytes = same input_id) so replays do
   not create second inputs.

## Minimal input

```json
{
  "tool": "acp_upload_parse",
  "arguments": {
    "project_id": "proj-da-001",
    "format": "cdxml",
    "filename": "butadiene-ethene.cdxml",
    "content_base64": "PD94bWwgdmVyc2lvbj0iMS4wIj8+...",
    "role": "reactant"
  }
}
```

## Expected success output

```json
{
  "result": "parsed",
  "input_id": "input-cdxml-1",
  "sha256": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "schema": "acp2.science.cdxml-semantics",
  "reaction": {
    "reaction_kind": "net_reaction",
    "reactants": [{"ref": "r1", "charge": 0, "multiplicity": 1}],
    "products": [{"ref": "p1", "charge": 0, "multiplicity": 1}],
    "candidate_steps": [{"step_id": "step-1", "role": "elementary"}],
    "atom_mapping": {"status": "mapped"}
  }
}
```

## Failure output

A document A's semantics cannot parse must fail closed — no task, no empty
structure, and the error is surfaced to the Workbench:

```json
{
  "class": "invalid-parameter",
  "code": "input_invalid",
  "params": {
    "rule": "cdxml_parse_failed",
    "path": "arguments.content_base64"
  },
  "input": {
    "tool": "acp_upload_parse",
    "arguments": {"format": "cdxml", "filename": "broken.cdxml"}
  }
}
```

Also rejected:

- uploading `cdxml` while the entry is undelivered or unauthorized →
  `unsupported_capability` / `access_denied`, and C keeps `status: "DEFERRED"`;
- a parser that returns `{"structures": []}` for a broken document is a
  contract violation: the correct output is `input_invalid` above. C's
  fixtures (`question-reaction-fixtures`) pin that a deferred/broken CDXML
  input is never treated as an empty structure.

## Compatibility requirement

- Additive within `acp2.runtime.upload-parse.v1`; the existing structure
  upload keeps its name and input schema.
- The eight frozen P1 tools and the frozen P2 allowlist are not widened by
  this request; a new upload/parse entry is a new versioned resource, not a
  silent change to `acp_submit`.
- Enabling this entry without `A-req-02` remains `NOT_VERIFIED`: C must not
  flip `cdxml` off `DEFERRED` on B delivery alone.

## Evidence path

`$ACP2_EVIDENCE/C/question-reaction-fixtures/<run_id>/` (the DEFERRED pin)
plus the B runtime receipt that delivers this entry; the enablement change
must cite `A-req-02` + `B-req-09` together.
