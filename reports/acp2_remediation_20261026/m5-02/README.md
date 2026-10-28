# M5-02 — single BOM, retrievable artifacts and evidence coverage matrix

Generated from the clean candidate `f6b6153` and the receipts in
`reports/acp2_remediation_20261026/`. Every number below is read back from
`summary.json` / `walk-bom.json`, not hand-written.

- task result: **PASS** (the M5-02 delivery is complete)
- full ACP2 release: **NOT_VERIFIED** (H4/H7 and 15 required rows remain open)
- candidate: `f6b6153070bae9fee175b0c98b840ee7e5f1515a`, tree
  `2b8fb094209ffbad5c1ef7d49504250517e2ec24`, `clean=true`
- persistent ref: `acp2/integration-remediation`; proposed tag
  `acp2-remediation-m5-02-candidate` (not created — the orchestrator tags)
- BOM: `docs/reference/next-change/acp2-contracts/release/bom.current.json`
  sha256 `538f573b523a6511057be0954fb80ef58e96cf86bcabf71f5317d0f9a7a8cf60`
- artifact store: `/ACPServer/acp2-artifacts/remediation/m5-02`
- coverage ledger sha256 `fa04b53dca214ebbc40bf1d85edd455b6d32e57dd49e928d9a4b2633b4a97018`
- manifest contract `1.7.0`, contract digest
  `5701101bb521213484e5ab3551814357c9342b7c1849ace944897fe712bf879b`
- prior BOM (M5-01) is referenced through `evidence.supersedes`; its bytes
  were not rewritten.

## BOM components

| kind | count |
| --- | --- |
| `python-wheel` | 4 |
| `python-sdist` | 4 |
| `rph-package` | 12 (4 tarballs + 8 install-surface files) |
| `plugin-wheel` | 3 |
| `static-asset-bundle` | 1 |
| `contract-bundle` | 1 |
| `science-asset` | 1 |
| `document` | 1 |
| **total** | **27** (237,674,375 bytes) |

The Python wheels/sdists were built from a clean worktree at the candidate
commit; the only worktree differences are the 17 M5-02 release-ledger overlay
paths recorded in `source.release_overlay` (coverage/schema/manifest/BOM,
release scripts, the sign-off tests and this report). No business source changed.

## Coverage: 39 tasks / 32 checkpoints / 26 gaps / 8 H rows / 3 DEFERRED rows

| segment | total | PASS/CLOSED | OPEN/NOT_VERIFIED |
| --- | --- | --- | --- |
| tasks | 39 | 36 (plus M0-13 NOT_APPLICABLE) | 2 (`M3-03`, `M5-03`) |
| checkpoints | 32 | 31 | 1 (`M0-02c`) |
| gaps | 26 | 16 | 10 (`G01,G05,G06,G10,G13,G15,G16,G17,G18,G19`) |
| H rows | 8 | 5 (`H0,H1,H2,H5,H6`) | 3 (`H3,H4,H7`) |
| DEFERRED | 3 | 0 | 3 (worker-hosted COMPUTE, CDXML, cumulative core-hours/token) |

Every row cites an existing receipt + sha256, the candidate, `evidence_kind`,
`result`, `scope`, `impact_globs`, `reuse` and `required_observations`. 108
receipt digests are verified by the walker.

### Reuse audit

- `exact` 27 rows: the evidence path is byte-identical at the candidate
  (including the M0-01 ledger, M5-02 BOM/walker and the M4 rows committed from
  the same working tree).
- `reused` 62 rows: later additive diffs do not touch the row's evidenced
  behaviour and the final full suite at `6cbf725` is green apart from the two
  documented location-dependent `h4_closeout` tests.
- `rerun_required` 19 rows (`M0-02c`, `M3-03`, `M5-03`, the 10 open gaps, H3/H4/H7
  and the 3 DEFERRED rows): no current-candidate run; they remain
  `NOT_VERIFIED`.

## Key judgements

| row | verdict | reason |
| --- | --- | --- |
| H4 | **OPEN / NOT_VERIFIED** | DA-ENV-LSF is required and NOT_VERIFIED; the Local DA row lacks `walltime_process_termination` and `terminal_usage_receipt`; the bounded recipe public entry is unsupported; the timing basis is unmeasured. |
| H6 | **CLOSED / PASS** | M4-06a upgrade/rollback/recovery + M4-06b NMR/thermochemistry reuse, multi-project/multi-user isolation and three-face revocation. `science_state` still mirrors the older handoff-H7 NOT_VERIFIED. |
| H3 | OPEN / NOT_VERIFIED | LSF submission/control landed, but the required DA-ENV-LSF row and the shipped execution_limits rows stay NOT_VERIFIED. |
| H7 | OPEN / NOT_VERIFIED | BOM + walker are ready; the independent operator/clean-host install is M5-03. |
| M0-13 | CLOSED / NOT_VERIFIED (scope NOT_APPLICABLE) | M0-09 selected the application-level handshake; no external DSH change required. |
| M0-02c | OPEN / NOT_VERIFIED | Ruff 0.15.22 ratchet pending; D-INT-06 freezes it out of the G01–G26 required chain and it is not a candidate_full blocker. |
| M3-03 | OPEN / NOT_VERIFIED | The model smoke had no provider credential; M4-02 ran a live round, but rotation/authorization of the production gateway credential is pending. |
| M4-04 | CLOSED / PASS (execution chain) | Route-a ran the whole ADR-008 chain on real ORCA; `claim_status=refuted` on DA-C05 endpoint identity (path connectivity refuted, activation energy supported). This is an execution-chain PASS, not a supported scientific claim. |
| M5-03 | OPEN / NOT_VERIFIED | Independent operator acceptance has not run. |
| G15 | OPEN / NOT_VERIFIED | `bounded_recipe_public_entry=absent` (endpoint_prep is not dispatchable; nine attempts share one release, not one TaskRef) and `DA-ENV-LSF=NOT_VERIFIED`. |

The two `tests/test_acp2_runtime_h4_closeout.py` failures are pre-existing
location-dependent path-relocation failures. They are **not** used as evidence
for or against H4 or any other row.

## B26 manifest registration

`release/scope.json`, `release/evidence-coverage.schema.json` and
`release/bom.schema.json` flipped from `planned` to `delivered`; the generated
`release/bom.current.json` and `release/evidence-coverage.current.json`
registered as new delivered contracts. All five name `delivered_by: B26`
(a stage already present in `DELIVERED_IDS_BY_STAGE`), owner B. The manifest
moved to `contract_version 1.7.0` and appended the B26 history entry.
`release-bom-current` is excluded from `contract_digest` because the BOM embeds
that aggregate digest; both release schemas and the coverage instance stay
inside it. This is an additive exception to the two existing self-referential
files (manifest, baseline-lock).

## Walker

`python3.11 scripts/release/walk_bom.py --verify` (receipt:
`walk-bom.json`) starts from the BOM and:

- verifies the source revision/tree against git and `clean=true`;
- re-fetches all 27 artifact URIs, recomputes sha256/size, opens the contract
  bundle and rejects a stale embedded BOM;
- re-hashes all 108 coverage-row receipts and checks candidate/scope/
  evidence_kind/reuse/required_observations;
- recomputes the manifest contract digest/self digest and the five B26
  registrations;
- prints the 20 open rows (15 required after excluding the N/A/hygiene/deferred
  rows). Exit 0 means every referenced byte is retrievable; it does not mean the
  full release is signed off.

Result: **PASS**, 27/27 artifacts byte-verified, 108/108 receipts verified.

## Open rows handed to M5-03

`G01,G05,G06,G10,G13,G15,G16,G17,G18,G19`, `H3,H4,H7`, `M3-03`, `M5-03`.
`M0-02c` and the three DEFERRED rows stay open but are outside the required
Candidate-Full chain.

## Validation commands

```bash
set -a; . /ACPServer/acp2-runs/remediation/env.sh; set +a
CONFSEARCH_SHERMO_PATH=/home/xieningke/Shermo_2.6.1/Shermo \
  python3.11 scripts/release/build_release_bom.py \
  --store /ACPServer/acp2-artifacts/remediation/m5-02/ \
  --python-source /tmp/opencode/m5-02-clean
python3.11 scripts/release/walk_bom.py --verify
python3.11 scripts/acceptance/validate_acp2_contracts.py
python3.11 scripts/acceptance/validate_acp2_gap_coverage.py
PYTHONPATH=src python3.11 -m pytest \
  tests/test_acp2_m502_coverage_signoff.py tests/test_acp2_gap_coverage.py \
  tests/test_acp2_runtime_contracts.py tests/test_acp2_plan_graph.py -q
```

## Limitations

- M5-02 records terminal judgments; it does not rerun every scientific/model/
  LSF/browser scenario. `reused` rows rely on the 6cbf725 full suite and the
  absence of impact-scope changes; `rerun_required` rows stay NOT_VERIFIED.
- The production model-gateway credential must be rotated and authorized before
  M3-03/G05/G10/G13 can PASS.
- The contract bundle is a build-time snapshot and excludes `bom.current.json`;
  it predates the final BOM sha registration. The live manifest + BOM are the
  authoritative pair.
- `persistent_tag` is a proposal only: no tag, commit or push was performed.
