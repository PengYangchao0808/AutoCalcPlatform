# B02 / todo 3 — ACP 2.0 Plan B runtime environment

- Candidate: `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12`
- Base: `096cdffe9a3817d8ee443d824830d87ec6e07bc2`
- Materialized commit: `a0185b15c546fbd07919326d581fab13e05000fa`
- Worktree: `/ACPServer/acp2-development/B` (clean, single candidate-materialization commit)
- Verification: 1906/1906 files matched manifest SHA-256 and size; 0 mismatches / 0 missing.
- Evidence root: `reports/acp2_implementation/bb45a46/B/B02/`

## Executed

1. ENV-01 probe (tools, Python 3.11.13, x86_64, 40 cores, 62 GiB RAM, xfs native data root, 28 GiB free, 35.9M free inodes, ports 18765/18766 free). Production 8765 untouched.
2. Clean worktree at `096cdff`; applied `tracked-changes.patch` then `new-files/`; verified 1906/1906; committed.
3. `python3.11 -m venv --system-site-packages venvs/b`; `pip install -e 'B[dev,api,remote]'`; `wheel 0.48.0` added to the B venv (build prerequisite missing at first attempt).
4. `unset PYTHONPATH` imports: acp/cccp/acp_services all resolve under `/ACPServer/acp2-development/B/src`.
5. `acp init --config .../cccp.yaml --answers .../answers.json` (non-interactive), then explicit `cluster.execution_mode: local`, `cluster.nodes: []`, isolated run root, and explicit local executable pins. `acp doctor` 10 PASS / 0 FAIL; `acp run serve --help` exit 0.
6. `$ACP2_DATA_ROOT/acceptance-env.sh` (0600) with all required variables, PATH order, deferred fixtures, `ACP2_DA_TIMEOUT` intentionally unset, and env-reference-only credential convention; verified in a clean shell.
7. Role interpreter matrix with import-source assertions and dependency digests for A/B/C/integration; browser env records playwright 1.63.0 without launching Chromium.
8. B02b P0 package smoke outside the repo: three wheels/sdists built; ownership/assets/static verified; worker and web role probes PASS; clean-venv install/upgrade/uninstall PASS; cccp import PASS with empty executable PATH.
9. Cleanup of temporary venvs/build dirs (2.6 GiB → 90 MiB artifact set kept for evidence); no lingering process or port; B worktree kept.

## Named gaps

- LSF: NOT_VERIFIED (no bsub/bjobs).
- Chromium launch: NOT_VERIFIED (C T-08 H2).
- Functional science: NOT_VERIFIED (doctor checks binary presence/sanity only).
- Full B25 packaging: NOT_VERIFIED (P5).
- C convergence: NOT_VERIFIED (C worktree advanced during B02; snapshot recorded).

## Key findings

- `/usr/bin/orca` is the GNOME screen reader; doctor rejected it. The real ELF QC ORCA is `/home/xieningke/orca611/orca`.
- `~/.local/.../__editable__.acp-0.2.0.pth` points at the dirty main checkout. The B venv's own site-packages wins, and every role assertion checks `__file__`; clean venvs were used wherever user-site leakage would invalidate a result.
- `ss` is installed at `/usr/sbin/ss` but absent from the default PATH; the handoff prepends `/usr/sbin`.
