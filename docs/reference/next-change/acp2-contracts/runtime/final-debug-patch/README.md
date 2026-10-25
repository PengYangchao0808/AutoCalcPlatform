# final-debug-patch — frozen 2026-10-22 debug candidate

This directory is a **copy** of the authoritative source evidence in
`reports/architecture_repair_20261022/` (that source directory is read-only during
B00 and is not modified). It exists so A/C consumers and B02 can apply and verify
one self-contained bundle from the contract root.

## Identity and provenance

| Field | Value |
|---|---|
| Base commit | `3670790363c7a1959ad1dbe4b4297adec518952a` |
| Historical code candidate | `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798` |
| `candidate_id` | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` |
| Candidate kind | source-overlay SHA-256, **not** a Git commit |
| Source report | `reports/architecture_repair_20261022/REPORT.md` (`sha256:1a970c8d…15de`) |
| Source overlay | `candidate-overlay.tar.gz` (`sha256:3fb835b5…7734`) |
| Tracked patch | `tracked-changes.patch` (`sha256:4578405d…9242`, 28 file diffs) |
| Snapshot | `candidate-snapshot.json` (`sha256:33861de7…872c`, 1,906 files) |
| Excluded path | `tests/baseline/refactor-evidence/e2e-task-root.txt` (pre-existing local test-run marker; never part of the candidate) |

## Digest verification decision (no fabrication)

The report defines no reconstructable `candidate_id` derivation rule. B00 tested 87
plausible canonicalization variants over `candidate-snapshot.json` (sorted path/hash
concatenations, JSON encodings, hash-only line formats); none reproduced
`bb45a46c…c3e12`. Therefore the digest is recorded as **inherited from the report**
with `verification: partial`, and the exact reason is stored in `manifest.json` and
the B00 patch-verification log.

The per-file evidence was verified independently: applying `tracked-changes.patch`
to a clean `096cdff` worktree and copying `new-files/` reproduced
**1,906/1,906** logged files with matching SHA-256 (0 mismatches, 0 missing), which
matches `reconstruction.json` (`result: PASS`). The current dirty checkout differs
from the frozen snapshot in 16 files due to later uncommitted edits; those paths are
recorded as drift and are **not** part of the candidate.

## Contents

- `tracked-changes.patch` — unified diff for 28 tracked files.
- `new-files/` — 144 files absent from the base commit.
- `candidate-snapshot.json`, `reconstruction.json`, `verification-summary.json` — copied source evidence.
- `candidate-overlay.tar.gz` — copied original overlay archive for provenance.
- `manifest.json` — per-file `path` / `sha256` / `size_bytes` for all 1,906 candidate files plus bundle-file hashes.
- `apply-order.md` — ordered apply stages and rollback.

## Use

Follow `apply-order.md`. Apply only to a clean worktree at `096cdff`; never to the
dirty development checkout. B02 records the resulting full commit SHA in
`runtime/baseline-lock.json`.
