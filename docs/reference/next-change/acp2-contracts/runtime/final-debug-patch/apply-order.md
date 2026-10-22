# Apply order — final_debug_candidate `sha256:bb45a46c…c3e12`

Base commit: `096cdffe9a3817d8ee443d824830d87ec6e07bc2` (historical debug delivery revision, **not** the start baseline).
Candidate label: `bb45a46` (evidence root `reports/acp2_implementation/bb45a46/`).

Apply this bundle only in a **clean, dedicated worktree**. The original checkout is dirty (90 porcelain paths at B00 capture: 39 modified tracked + 51 untracked); a dirty tree is never the baseline.

## Stage 0 — Preflight

```bash
export WORKTREE=/absolute/native/path/acp2-final-debug
git -C /ACPServer/AutoCalcPlatform worktree add --detach "$WORKTREE" 096cdffe9a3817d8ee443d824830d87ec6e07bc2
test "$(git -C "$WORKTREE" rev-parse HEAD)" = "096cdffe9a3817d8ee443d824830d87ec6e07bc2"
test -z "$(git -C "$WORKTREE" status --porcelain)"
sha256sum tracked-changes.patch   # expect 4578405dc1b99d9a884c651a8ea064a6eed311128b9564966200b33e95aa9242
```

Do not copy or install from `/ACPServer/AutoCalcPlatform` directly.

## Stage 1 — Tracked patch (28 files)

Rationale: the unified diff targets tracked paths that must exist at the base commit. Applying it before any new file copy avoids accidental patch-context collisions.

```bash
git -C "$WORKTREE" apply --whitespace=nowarn /absolute/path/to/final-debug-patch/tracked-changes.patch
```

## Stage 2 — New files (144 files)

Rationale: the overlay's `new-files/` set contains only paths absent from `096cdff`, so it cannot overwrite a tracked base file. Copy in a deterministic lexicographic order (the shell `cp -a source/. dest/` and the verification script both walk sorted paths); the order is recorded so a failed partial copy can be resumed by re-running the same command.

```bash
cp -a /absolute/path/to/final-debug-patch/new-files/. "$WORKTREE/"
```

## Stage 3 — Verify every candidate file

```bash
python3.11 /absolute/path/to/verify_final_debug_patch.py \
  --bundle /absolute/path/to/final-debug-patch \
  --worktree "$WORKTREE"
```

Expected: `1906/1906` files match `manifest.json` SHA-256 and size; 0 mismatches; 0 missing. `tests/baseline/refactor-evidence/e2e-task-root.txt` is excluded and must not be restored from this bundle.

## Stage 4 — Commit and register the resulting SHA (B02)

```bash
git -C "$WORKTREE" add -A
git -C "$WORKTREE" commit -m "fix(debug): apply frozen 2026-10-22 final debug candidate"
FINAL_SHA="$(git -C "$WORKTREE" rev-parse HEAD)"
```

B02 writes `FINAL_SHA` and the empty `git status --porcelain` check into `runtime/baseline-lock.json` (`final_debug_candidate_commit`). B00 deliberately does **not** invent or pre-record that commit.

## Rollback

In the dedicated worktree only:

```bash
git -C "$WORKTREE" reset --hard 096cdffe9a3817d8ee443d824830d87ec6e07bc2
git -C "$WORKTREE" clean -fdx
```

Never run this in `/ACPServer/AutoCalcPlatform`; that checkout contains unrelated user work and must not be reset or cleaned. The source evidence under `reports/architecture_repair_20261022/**` is read-only and is not modified by this bundle.
