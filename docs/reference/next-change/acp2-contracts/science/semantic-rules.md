# ACP2 Reaction and Question Semantic Rules

Status: frozen contract text for `reaction.schema.json` and `question.schema.json`
(major version 1). Authored by A-02a; the executable contract test and the fixture
files are owned by A-02b (`tests/test_acp2_science_reaction_contract.py`,
`tests/fixtures/acp2/science/contracts/reaction/`). This document names each rule,
its structural or semantic enforcement, and the rejecting fixture reserved for it.

## 1. What each layer can and cannot decide

JSON Schema (`reaction.schema.json`, `question.schema.json`) enforces **structure**:
types, required fields, enums, bounds, `uniqueItems`, object-key uniqueness and
`additionalProperties`. It cannot balance elements, compare parallel arrays, check
that both mapping directions agree, or decide whether two results are scientifically
comparable. Those are **scientific-semantics rules** below; they are implemented by
the future contract test with standard-library Python only (no `jsonschema`, no
second schema engine).

The split mirrors the existing science stack:

| Authority | Responsibility | Citation |
|---|---|---|
| `AtomIdentityMap` | stable per-structure atom identity and JSON round-trip (`to_dict`/`from_dict`) | `src/cccp/analysis/pes/atom_mapping.py:26-53` |
| `AtomMapCandidate` | one reactant→product mapping with confidence, method and `mapping_source` provenance | `src/cccp/analysis/pes/atom_mapping.py:64-72` |
| `MappingResult` | mapping outcomes `unique` / `candidates` / `count_mismatch` / `failed`; unmatched atom lists | `src/cccp/analysis/pes/atom_mapping.py:76-84` |
| `map_reactant_to_product(...)` | mapping entry point; count/composition mismatch is an explicit status, never silently repaired | `src/cccp/analysis/pes/atom_mapping.py:114` |
| `BondChange` | one mapped bond change with 0-based side indices, bond orders, distances and `break`/`form`/`order_up`/`order_down` classification | `src/cccp/analysis/pes/bond_changes.py:34-58` |
| `_bond_changes_from_graphs(...)` | evidence-first classification: bonded-on-both-sides is only an order/aromaticity change, never a break/form | `src/cccp/analysis/pes/bond_changes.py:100` |
| `_validated_manual_entry(...)`, `_validated_manual_atom_pair(...)` | manual entries require at least one side, reject self-pairs and out-of-range indices | `src/cccp/analysis/pes/bond_changes.py:418`, `:452` |
| `ElectronicStateSpec` | `state_id` and physical `target_multiplicity`; no implicit electronic state | `src/cccp/calculation/contracts.py:283-313` |
| pure science seam | scientific types are separated from platform identity and execution | `src/cccp/calculation/contracts.py:36-40` |
| structure parsing | the formats the stack can interpret today | `src/cccp/analysis/structure/parsers.py:275-288` |

The JSON contract uses **canonical atom addresses** of the form
`<structure_id>:<atom_id>` (see `$defs.atom_address`). Both parts are serialized
fields; an address is never derived from an array position. This is the JSON-level
equivalent of `AtomIdentityMap.uid_to_structure_index` plus
`AtomIdentityMap.mapping` (`atom_mapping.py:26-53`).

Bond-change classification is translated, not re-invented:
`form -> formed`, `break -> broken`, `order_up|order_down -> changed`, with
`bond_order_left`/`bond_order_right` carrying the direction. The existing
`BondChange.change_type` vocabulary and 0-based side indices remain authoritative at
the Python layer.

## 2. Invariant rules

Fixture paths are relative to `tests/fixtures/acp2/science/contracts/`. The directory
is created by A-02b; this table names the fixture that must exist and that must be
rejected (or, where stated, preserved) for each rule.

| ID | Invariant | Enforcement | Rejecting / preserving fixture |
|---|---|---|---|
| **R-01** | **Stable atom ids on both sides.** Every species declares `atom_ids` (non-empty, unique) and an `atom_id_scheme`; ids are serialized identity and are not renumbered on round-trip. | Schema: `required`, `minItems`, `uniqueItems`, `atom_id_scheme` enum. Semantics: every mapping address resolves to a declared `structure_id:atom_id` on its own side; left and right sides are checked independently. | `reaction/atom_id_missing.json` (missing `atom_ids`), `reaction/atom_id_duplicate.json` (duplicate id inside one structure) |
| **R-02** | **One-to-one atom maps.** Each mapped atom appears exactly once per side. No left atom maps twice and no right atom is claimed twice. | Schema: `left_to_right` and `right_to_left` are JSON objects, so duplicate keys are structurally impossible in one direction; `unmapped_*` are `uniqueItems`. Semantics: the two directions are exact inverses and an atom is either mapped once or listed once as unmapped — never both, never omitted. | `reaction/duplicate_map.json` (same left atom mapped to two right atoms / same right atom claimed twice) |
| **R-03** | **Element and stoichiometry conservation.** The multiset of elements is identical on both sides; the reaction is balanced. Net charge is conserved; each species' `charge` is explicit. | Semantics only (JSON Schema cannot balance elements): sum `elements`/`formula` per side, compare element counts and total charge; a missing whole side is incomplete. This is implemented by the A-02b contract test, not by a schema engine. | `reaction/missing_product.json` (right side removed), `reaction/element_mismatch.json` (unbalanced element counts), `reaction/charge_mismatch.json` (net charge differs) |
| **R-04** | **Explicit unknown stereo and conditions are preserved, never invented.** `stereochemistry.status` may be `unknown`; individual centers/double bonds may be `unknown`; conditions may be `null` only when the path is declared in `input_uncertainty.unknown_fields`. | Schema: `unknown` enum members, nullable condition types, required `input_uncertainty`. Semantics: a null/invalid value is either declared or rejected; a declared-unknown stereo fact may not be replaced by an `R`/`S`/`E`/`Z` assignment; missing temperature/pressure is never replaced by 298.15 K / 1 bar. | Preserve: `reaction/ambiguous_stereo.json` (stays `unknown` after round-trip). Reject: `reaction/invented_stereo.json` (contract claims an assignment that the input marked unknown) |
| **R-05** | **"Cannot interpret the input" is not "representable but not applicable".** An uninterpretable structure/format is rejected at the contract boundary with an interpretation error. A valid, interpretable reaction that a given question or recipe cannot use is accepted by the schema but rejected for that use with a `not_applicable` reason. | Separate error classes, never merged. The contract test asserts distinct reasons and that neither is silently coerced into the other. | `reaction/uninterpretable_input.json` (cannot parse/identify atoms), `reaction/representable_not_applicable.json` (valid reaction, question/recipe applicability fails) |
| **R-06** | **A net reaction never claims a single elementary step.** `reaction_scope` distinguishes `net_reaction` from `elementary_step`. A `net_reaction` payload may list net bond changes but must not assert that one elementary step explains the transformation. | Schema: required enum. Semantics: `elementary_step` requires explicit transition-state/IRC evidence outside this schema; a net reaction cannot be relabelled as one. | `reaction/net_as_elementary.json` (multi-step net reaction asserted as one elementary step) |
| **R-07** | **Unsupported input is rejected explicitly, never silently dropped.** Initial supported `input_format` values are `mol`, `sdf`, `smiles` and `structure_upload`. **CDXML is `DEFERRED` outside the initial A/B/C scope.** | Schema: closed `input_format` enum (CDXML absent). Semantics: an unsupported format produces an explicit rejection; no best-effort parser and no empty structure. | `reaction/unsupported_cdxml.json` (CDXML input rejected as out of scope) |
| **R-08** | **The question carries no LLM/model role, cost/price, budget or platform state.** Forbidden names: `role`, `model_role`, `cost`, `price`, `job_status`, `queue`, `account`. | Schema: `additionalProperties: false` everywhere plus an explicit top-level/nested `propertyNames.not.enum` containing exactly that list. | `question/job_status.json`, `question/role.json`, `question/cost.json`, `question/price.json` |
| **R-09** | **Claim statuses are the frozen four values.** `supported`, `refuted`, `inconclusive`, `not_evaluated`. Execution terminal state (completed/failed/cancelled) is not a claim status and never maps to `supported`. | Schema: `claim_status_vocabulary` is a `const` array of exactly the four values; the evidence contract references the same vocabulary. | `question/unknown_claim_status.json` (fifth/unknown value) |
| **R-10** | **Comparison conditions are explicit and compatible.** Method, electronic state (charge/multiplicity), temperature, pressure, standard state, reference state and energy unit are declared; `compatibility_requirements` names what must match. A missing/unknown condition cannot be treated as a match. | Schema: required fields, closed enums, explicit `energy_unit`, non-empty requirement list. Semantics: the validator reports the first unsatisfied requirement instead of widening the comparison. | `question/missing_standard_state.json` (condition omitted/undeclared), `question/incompatible_method.json` (requirement not met) |

The minimum required set from the plan is covered as R-01 (stable ids both sides),
R-02 (one-to-one maps), R-03 (element/stoichiometry conservation), R-04 (explicit
unknown stereo/conditions preserved) and R-05 (cannot-interpret vs
representable-but-not-applicable). R-06 to R-10 are additional frozen rules from the
same task statement.

## 3. Supported input scope and the CDXML decision

| Input | Status | Meaning |
|---|---|---|
| MOL | **SUPPORTED** | existing structure parser (`src/cccp/analysis/structure/parsers.py:280`) |
| SDF | **SUPPORTED** | existing structure parser (`parsers.py:278`) |
| SMILES | **SUPPORTED** | existing structure parser (`parsers.py:286`) |
| existing structure upload | **SUPPORTED** | the current upload/intake path feeding the same parsers |
| CDXML | **DEFERRED** | outside the initial A/B/C scope (R3-12); no owner, no parser, no input enum value |

Rules that follow from this table:

1. An unsupported format is **rejected explicitly** (`unsupported_input_format`);
   it is never parsed best-effort and never becomes an empty structure.
2. **B and C must not claim a complete ChemDraw/CDXML chain** until an owner and a
   parser exist and this table is versioned to SUPPORTED. A `DEFERRED` marker in an
   A document does not authorize a B/C product claim.
3. The schema enum is the contract. Adding CDXML later requires a new
   `schema_version`, a parser owner and a fixture — not an unversioned enum edit.

The parser module can interpret additional formats (`log`, `xyz`, `gjf`, `inp`), but
the ACP2 **reaction contract** is deliberately narrower: the initial supported list
above is the one B/C may promise.

## 4. Unknown values versus defaults

- `null` in `conditions`/`comparison_conditions` means **not supplied**. The matching
  dotted path must appear in `unknown_fields` (`input_uncertainty.unknown_fields` for
  reactions, `comparison_conditions.unknown_fields` for questions).
- `stereochemistry.status`, `stereo_center.configuration` and
  `stereo_double_bond.configuration` use the explicit string `unknown`. It survives
  serialization and is never replaced by an inferred assignment.
- `AtomMapCandidate` provenance (`method`, `confidence`, `mapping_source`) and
  `MappingResult.status` are preserved by the mapping layer; a `candidates` or
  `count_mismatch` result must not be upgraded to a unique authoritative map without
  human confirmation (`atom_mapping.py:76-84`).
- No field in either schema has a schema-level `default`. A default would turn a
  missing scientific fact into an invented one.

## 5. Net reaction versus elementary step

`reaction_scope: "net_reaction"` describes the overall transformation. Its bond
changes are net changes. It never asserts that a single elementary step or a single
transition state explains the transformation. `reaction_scope: "elementary_step"` is
allowed only when the payload is accompanied by the supporting transition-state /
IRC / frequency evidence in the evidence contract; this schema alone never upgrades a
net reaction to an elementary step.

## 6. Determinism and round-trip

- Schema documents are UTF-8 JSON with a fixed key order, a `$schema`, a stable `$id`
  (`https://acp.local/acp2/science/<stem>.schema.json`) and `schema_version` 1.
- Reaction instances round-trip without changing atom ids, mapping direction,
  charge/multiplicity, unknown markers or condition nulls.
- Atom identity is carried by `atom_ids`, never by array position, so reordering
  species or pretty-printing does not change the mapping.
- Unknown fields are **rejected** for new executions (`additionalProperties: false`).
  Historical reads are a separate compatibility concern and gain no execution or
  recovery eligibility from tolerating unknown fields.

## 7. Question-specific prohibitions

The question schema intentionally has no field for:

- LLM/model roles or prompts (`role`, `model_role`);
- monetary or budget data (`cost`, `price`);
- platform scheduling state (`job_status`, `queue`, `account`).

These names are rejected both by `additionalProperties: false` and by the explicit
`propertyNames.not.enum` list, so adding one is a contract violation rather than an
ignored extension. Scientific comparability lives only in
`comparison_conditions`; execution status and cost live in the platform contract,
not here.
