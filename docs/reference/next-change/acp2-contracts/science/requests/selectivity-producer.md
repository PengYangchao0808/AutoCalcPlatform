# ACP2 Plan A — Selectivity Producer Request (A-25a)

Status: **still open after A-25b (2026-10-23).** C T-40a has not submitted, B23b
has not executed or exported, and no public `run_id` exists. A has frozen the
two-path selectivity fixture and its endpoint definitions, and A-25b has
published the bounded absent-producer report
(`science/selectivity-evidence-report.json`: `platform_evidence_state =
NOT_VERIFIED`, `real_run = false`, both path statuses `not_evaluated`, H6 open).
The published report does not create producer evidence and does not upgrade
this request. A (science track) requests the one registered research submission
and the authorized artifacts; this document is **not** a submission by A, it
does **not** issue a run key and it does **not** claim a result. A does not start
a scheduler, a Controller, a QC process, an SSH/LSF session or a production
database write from this request.

Handoff chain (must keep this order):

```text
A-25a (this fixture) -> C T-40a (single research submission)
                      -> B23b (execute both paths and export authorized artifacts)
                      -> A-25b (scientific audit and bounded EvidenceReport)
                      -> C T-40b (presentation and signature)
```

The B23b artifact edge closes at readiness `integration_ready` (artifacts
exported), not at B23 whole-task `verified`. C T-40b signs only after A-25b.

## 1. Single registered reference

A uses exactly the single registered reference
`owner=C, scenario=experience:cross-domain-live, subcase=selectivity` in the
fixture, the request and the audit. `subcase=selectivity` is display metadata;
it is never part of the public key.

| Field | Value |
|---|---|
| `owner` | `C` |
| `scenario` | `experience:cross-domain-live` |
| `subcase` | `selectivity` (display metadata only) |
| `phase` | `experience:selectivity` |
| `run_id` | B/C-issued; unknown to A; never invented |
| Public key | `(owner, scenario, phase, run_id)` = `(C, experience:cross-domain-live, experience:selectivity, <B/C-issued>)` |
| Forbidden alias | no separate `selectivity-live` scenario is registered or accepted |

If B01 has not published the phase registry, or if two subcases resolve to the
same public key, the row is `NOT_VERIFIED`; A does not merge subcases or invent
a `run_id`.

## 2. Frozen case and inputs

The fixture root is `tests/fixtures/acp2/science/selectivity/v1/`. It is
**constructed**: the structures are RDKit ETKDG/MMFF geometries, no QC job ran,
and no energy, TS geometry or experimental value exists.

| Item | Frozen value |
|---|---|
| Case id | `selectivity.cp_methyl_acrylate.endo_exo.v1` |
| Fixture id | `selectivity-cyclopentadiene-methyl-acrylate-endo-exo-wb97xd4-def2svp` |
| Diene | cyclopentadiene, `C5H6` |
| Dienophile | methyl acrylate, `C4H6O2`, frozen s-cis conformer (C=C-C=O ≈ 0°) |
| Declared regioisomer | 2-substituted methyl bicyclo[2.2.1]hept-5-ene-2-carboxylate |
| Products | endo and exo, both `C9H12O2`, 23 atoms each |
| Method / basis | `wB97X-D4` / `def2-SVP`, gas phase |
| Electronic state | charge `0`, multiplicity `1`, `state_id = state.singlet.s0` |
| Conditions | `298.15 K`, `100000 Pa`, `1_bar_ideal_gas`, no solvent |
| Raw energy unit | `hartree`; comparison unit `kcal_per_mol` |
| Reference state | separated cyclopentadiene + methyl acrylate at infinite separation, and each product at the same conditions |
| Preference tolerance | `0.5 kcal/mol` (constructed) |
| Source label | `frozen_known_candidate` for every frozen structure; no `generated_candidate` is claimed |

The two paths share the left side, the atom-mapping topology and the bond
changes; only the product stereochemistry differs.

## 3. The two path inputs

| Path | Reaction | Product structure | Stereochemical signature | Face |
|---|---|---|---|---|
| `path.endo` | `reaction-endo.json` | `product_endo` (`product-endo.mol`) | `pd_c5 = S`; signed volume of (`pd_c4`, `pd_c6`, `pd_c8`) about `pd_c5` is negative | endo |
| `path.exo` | `reaction-exo.json` | `product_exo` (`product-exo.mol`) | `pd_c5 = R`; signed volume is positive | exo |

Common mapping topology digest:
`f131899dba9c7b02f8d841546362eca1a2b3ef9d5e160a71f3acc7744fc3ed16`.
Each path's full mapping digest and endpoint digest are frozen in
`fixture-manifest.json`; B23b must return the same values it actually consumed.

The endo/exo definition is recorded in the manifest:

- reference plane: `pd_c4`, `pd_c5`, `pd_c6`;
- face reference atom: `pd_c7` (the C7 methylene bridge);
- substituent reference atom: `pd_c8` (the ester carbonyl carbon);
- **endo**: `pd_c7` and `pd_c8` are on the same side of the reference plane;
- **exo**: they are on opposite sides.

The endpoint test asserts a stereochemical difference (CIP configuration and
signed-volume sign) while the atom-id order and element sequence are identical.
An atom-order permutation must not change the signature.

## 4. Required coverage and acceptance criteria

A path is **qualified** only when all of the following are present and
comparable for that path:

1. A transition-state candidate with a stationary-point optimization result.
2. Exactly one significant imaginary frequency and a mapped imaginary mode.
3. Bidirectional IRC evidence reaching both frozen endpoints with an exact
   one-to-one endpoint atom mapping and endpoint geometry inside the frozen
   coordinate tolerance.
4. A single-point electronic energy at the frozen method/basis/state, plus
   thermochemistry at the frozen conditions when a `ΔG‡` claim is made.
5. The same method, electronic state, temperature, pressure, standard state,
   reference state and energy unit as the other path.
6. A path identity that resolves to the frozen reaction/product digests; a
   stale attempt, a different generation or a different product is rejected.

Both paths are required. One path alone, two single-point minima, one imaginary
frequency or one IRC direction never constitutes a selectivity prediction.

B23b must return, at minimum:

- the B/C-issued `run_id` and the public-key row;
- the single `TaskRef`, attempt, generation, release/environment identity and
  storage location;
- both path records with step receipts and the authorized artifacts;
- the actual per-subrow TaskRef/step/QC counts and the cumulative budget,
  including reused artifacts;
- the terminal outcome of each step and any typed failure reason;
- the executed recipe and the consumed input digests;
- an explicit statement of whether a real QC binary ran.

A does not accept a receipt, scenario id, run key, artifact digest or budget row
that B/C did not publish.

## 5. Run-key request

B01 has not published `acceptance-result.schema.json` / `acp2.acceptance.v1` or
the `(owner, scenario, phase, run_id)` registry. A therefore requests:

| Component | Requested value / rule |
|---|---|
| `owner` | `C` |
| `scenario` | `experience:cross-domain-live` |
| `phase` | `experience:selectivity` |
| `subcase` | `selectivity` (display metadata only) |
| `run_id` | B/C-issued; not known to A |
| `fixture_id` | `selectivity-cyclopentadiene-methyl-acrylate-endo-exo-wb97xd4-def2svp` |
| `case_id` | `selectivity.cp_methyl_acrylate.endo_exo.v1` |
| `mapping_topology_digest` | `f131899dba9c7b02f8d841546362eca1a2b3ef9d5e160a71f3acc7744fc3ed16` |
| `path_ids` | `path.endo`, `path.exo` |
| `backend` | as frozen by B23b; no silent substitution |
| `scheduler` | `local` or `lsf`/`remote`; one scheduler per run key |
| `budget_ref` | B/C-issued reservation id |
| `evidence_owner` | A consumes; B/C own the raw execution record |

If B01 cannot issue a run key, the execution stays `NOT_VERIFIED` with this
exact gap. A never fills `run_id` with a placeholder.

## 6. Budget request

The following is A's proposed envelope for the limited selectivity subcase. It
is **constructed** and subject to B07/B23b: B may tighten it, and the B/C
cross-domain budget table is authoritative. A does not invent a B/C budget row.

| Cap | Requested value |
|---|---|
| New Local `TaskRef`s | `1` |
| `max_task_refs` | `1` |
| Declared paths | `2` (`path.endo`, `path.exo`) |
| TS candidates per path | `1` |
| Conformations per endpoint | `1` |
| Declared regioisomers | `1` (2-substituted) |
| `max_qc_invocations` | `14` (constructed proposal; B23b records actual counts) |
| `max_attempts_per_step` | `2` |
| `max_bsub` | `1` |
| `science_deadline_seconds` | `3000` |
| `export_budget_seconds` | `600` |
| `aggregate_budget_seconds` | `3600` |
| Budget timing basis | `unmeasured`; `measured_step_timings = null` |

The selectivity subrow shares the B/C cumulative budget with `nmr` and
`thermo`; A never resets that cumulative total by changing `run_id`. The B23a
`1800 s` multitenant timeout covers only B23a isolation and does not cover this
scientific execution.

## 7. Missing-path and absent-artifact policy

- If either declared path lacks a qualified TS, frequency, bidirectional IRC,
  endpoint mapping, comparable energy or thermochemistry, the scientific result
  is `inconclusive` and no relative preference is stated.
- If the B23b authorized artifacts or the public reference key are absent, the
  platform evidence state is `NOT_VERIFIED`, no preference is claimed and H6
  remains open.
- If the frozen budget is exhausted before both paths are qualified, record the
  gap and report `inconclusive`; do not start a new hypothesis and do not reset
  the budget by changing `run_id`.
- A single-point minimum, a single imaginary frequency or one IRC direction is
  never a substitute for a qualified path.
- No experimental quantitative claim, complete-mechanism claim or global-
  minimum claim is made from this fixture.

## 8. Out of scope

The 3-substituted regioisomer, other methyl acrylate conformers, solvent
effects, tunneling, experimental rates, alternative paths, the complete
mechanism, the global minimum and general method accuracy are out of scope.
Their absence is a limitation, not evidence for either path.

## 9. A-25b outcome — this request is still open

A-25b (todo 46) consumed the registered-reference / B23b / budget contract and
published the bounded report `science/selectivity-evidence-report.json`. The
report was produced from the **absent-producer** state because none of the
required producer artifacts exists yet:

| Required input | State on 2026-10-23 | Consequence |
|---|---|---|
| Registered `owner=C, scenario=experience:cross-domain-live, subcase=selectivity, phase=experience:selectivity` record | absent | public key incomplete; `run_id` never invented |
| C T-40a single research submission | not submitted | chain stops at the first handoff |
| B23b `integration_ready` authorized artifacts (declared edge `B23b:selectivity-authorized-artifacts`) | not exported; machine id unpublished | both path statuses `not_evaluated` |
| B/C cross-domain budget table covering `nmr`, `thermo`, `selectivity` | absent | no per-subrow TaskRef/step/QC counts or cumulative total can be audited |

The published report therefore records `platform_evidence_state =
NOT_VERIFIED`, `real_run = false`, `constructed = true`, `preference = null`,
both path statuses `not_evaluated`, every limitation and `h6_open = true`. It is
a bounded contract consumer, not evidence that C T-40a or B23b ran.

A-25b is complete as a consumer. It becomes a real selectivity row only when
C T-40a submits, B23b exports both paths at `integration_ready` under a unique
B/C-issued `run_id`, and the B/C budget table records the `nmr`, `thermo` and
`selectivity` subrows without resetting the cumulative total. C T-40b signs
only after that real A-25b audit; this request stays open until then.
