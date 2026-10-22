# B03 evidence — application API, MCP launch and science binding (H1)

Candidate label `bb45a46`. Branch `feat/frontend-plan1-closure`, B00 baseline commit `ac43c16`.

## Deliverables (contract root `docs/reference/next-change/acp2-contracts/`)

| Contract | Path | Manifest id |
|---|---|---|
| Application API + entry call table + digest/error/unknown-field rules | `runtime/application-api.v1.json` | `application-api` |
| Rendered entry-to-use-case table | `runtime/entry-call-table.v1.md` | `entry-call-table` |
| P2 research-operation table (R3-02) | `runtime/p2-research-operations.v1.json` | `p2-research-operations` |
| MCP launch descriptor schema (8 frozen tools, protocol 2025-06-18) | `runtime/mcp-launch.schema.json` | `mcp-launch-schema` |
| Placeholder-only launch template | `runtime/mcp-launch.template.json` | `mcp-launch-template` |
| Canonical digest spec + byte-exact vectors (B16 input) | `runtime/canonical-digest-vectors.v1.json` | `canonical-digest-vectors` |
| Science binding (singlepoint → TaskRequest) + reserved A ids | `runtime/science-binding.v1.json` | `science-binding` |
| P1 control/artifact/idempotency/budget fields | `runtime/control-and-data.v1.json` | `control-and-data` |
| P1 admission + P2 additive approval/budget | `runtime/admission-approval.v1.json` | `admission-approval` |
| CDXML deferral + A/B/C scope change | `runtime/fallback-deferral.json` | `fallback-deferral` |
| Positive/negative samples + hashed index | `runtime/samples/**` | `samples` |
| Refreshed dependency ledger | `runtime/dependencies.json` | `dependencies` |

Test: `tests/test_acp2_runtime_contracts.py` (28 tests).
Validator extension: `scripts/acceptance/validate_acp2_contracts.py` (stage-aware delivery, contract_digest
and self-digest enforcement, consumer-edge and not_verified checks).

## Files in this directory

| File | Content |
|---|---|
| `call-table-audit.md` | audit notes: submit/control chains, the four gaps, why `control`/`recover` were added |
| `contract-digests.txt` | sha256 of every B03 deliverable plus manifest/dependencies |
| `manifest-flip.json` | delivered/planned contract rows, `contract_digest`, self digest |
| `validator-happy.txt` | `validate_acp2_contracts.py` exit 0 |
| `validator-negative.txt` | three tampered inputs rejected with their exact reasons (sha mismatch, missing `delivered_by`, dangling dependency) |
| `pytest.log` | full `tests/test_acp2_runtime_contracts.py -v` run |
| `sample-validator.log` | per-sample accept/reject matrix + the sample/dependency tests |
| `dependencies-validation.log` | ledger counts, C source digest match, A `NOT_VERIFIED / blocked_fixture` row |
| `json-tool.log` | `python3.11 -m json.tool` over all 108 contract JSON files |
| `ruff.log` | ruff check + format --check on the test and validator |

## Honest limitations

- Entry-call-table verdict is **PARTIAL**: GAP-B03-01..04 are implementation gaps owned by
  B04a/B05/B06/B10a/B12. No PASS is claimed for entry equivalence.
- The A descriptor → B discovery → C form → B validation → CCCP round trip is **scheduled for B18**,
  not performed here.
- A's `science/cross-track-dependencies.json` does not exist; the edge is `NOT_VERIFIED / blocked_fixture`
  with the exact expected path. No A row was fabricated.
- B01's acceptance corpus under `runtime/samples/acceptance/**` is excluded from the B03 sample index;
  B01 owns and indexes it. At the time of writing, B01's `samples/acceptance/**` files were not yet
  present, so `tests/test_acp2_runtime_acceptance_harness.py` was still failing for that reason only
  (failure messages name the missing B01 files, not a B03 contract).
- `contract_version` stays `1.0.0`; the delivered-set `contract_digest` moved because planned contracts
  became delivered. B00's inherited `contract_digest` was not reproducible from the documented rule
  (drift `D-B03-01`), so it is recomputed and validator-enforced.
