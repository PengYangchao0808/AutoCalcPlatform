# ACP 2.0 Plan B — H7 release candidate changelog

**Candidate:** `acp2.plan-b.h7.release-fix4` · version `0.3.0`
**Release source:** `abec2bc4f1d302827248d36171973df5bc1bc4dd` (tree `c9844959c4c39814234a0a46c71d6f70a42456a4`)
**Landed HEAD at index time:** `3871ccbdc02c7dae69fb2103bc4921ae13ca58ee`
**Release directory:** `reports/acp2_implementation/bb45a46/B/B25/release-fix4/`
**Manifest:** `release-manifest.json` (sha256 `1f47a5e094dabc41f12654b627783df4bdf4ac29995fd5fbc149068bc06a7ef0`)

This is a reviewable release candidate, **not a production deployment**. `acp.service`
was not restarted and `/var/lib/acp/runs` was not touched.

## Artifacts and digests

| Artifact | SHA-256 |
|---|---|
| `cccp-0.3.0-py3-none-any.whl` | `088604cdbe52bbc98c879edb6cb3ccca486f9ed68d8ecd4471e296b9c5110445` |
| `cccp-0.3.0.tar.gz` | `b8c712f3ebf2afd9eee547f80797b74b70534bed3005506059df0256c7e8e96f` |
| `acp_worker-0.3.0-py3-none-any.whl` | `f30e72ca56e620520987f1408eccdd69b181a81c353a8cec461df268bcb38471` |
| `acp-worker-0.3.0.tar.gz` | `badb68d3ea69ab5fe95711049ed26db4d9304f8969ede6443a29da0963c8e17b` |
| `acp_web-0.3.0-py3-none-any.whl` | `68077f7d9734276b46a1a68fc207f5cde0582788f0218ebffed851763fdf41b1` |
| `acp-web-0.3.0.tar.gz` | `5d7c5a3f735ee5a10b553c1e4d095a0d32cd25a377573d01858e73b91019d67f` |
| `root-build/acp-0.3.0-py3-none-any.whl` | `423a9cd8f533429d3f2a78284ccf0d5f058c5e590c169790be6e663c982fed63` |
| `root-build/acp-0.3.0.tar.gz` | `01fbe86f4231703a180a92e11ae735e41b4fc0a8c81b59fef677372fed4d0f2f` |

All eight digests were recomputed from disk by the H7 aggregation; see
`integration/release-candidate.json`.

## Changes since the frozen B25a/B25b candidate

### B25b — role package correction

- Rebuilt the 0.3.0 four-role release from clean source; original B25a artifacts are
  retained under `release-b25a-original/` and `root-build-b25a-original/`.
- Fixed the B25a `acp_web` startup defect: `create_app()` passed
  `OriginPolicy(public_origin=...)` before `boundary.py` accepted the field. The
  installed Lab now starts and serves `/api/health`.
- Added the `package-install` acceptance scenario and evidence-identity checks.

### B26a — external reproduction defects (both fixed in B-fix-4)

- **B26A-D1 (high, scheduler execution).** `local_release_identity()` required
  `<root>/src/{acp,acp_services,cccp}`. An installed `acp-web`/`acp-worker` wheel places
  packages under `site-packages`, so Lab/Workbench submissions and `acp task execute`
  failed before dispatch while direct `acp run` worked.
  **Fix:** `src/acp/calculations/attempt_freeze.py::_release_sources` now derives the
  execution tree from installed package roots (`importlib.util.find_spec`) with stable
  `acp/...`, `acp_services/...`, `cccp/...` keys; `build_local_release` copies from the
  resolved source map. `tests/test_acp_attempt_freeze.py` covers both layouts, and the
  package-install scenario now asserts one real scheduler task.
  **Verified:** installed Lab HTTP submit `201` → `completed`;
  `acp task execute --follow` exit 0 → `completed`; release identity equals installed
  identity.
- **B26A-D2 (medium, revocation SSE).** After key/account revocation the next read was
  `401`, but an already-open `/api/events/stream` closed via `done` before emitting
  `permission_changed`. Root cause was fixture state, not installed code: reused PAUSED
  fixture jobs had been frozen to `FAILED` at Controller restart.
  **Fix:** the B15 harness resets reused fixture jobs to their declared `PAUSED` state
  through the `JobStore.transition` CAS. The twelve strict assertions were not weakened.
  **Verified:** both `revocation.key.read_and_stream` and
  `revocation.account.read_and_stream` emitted `permission_changed`; reads after revoke
  returned `401`.

### B-fix-4 — release rebuild

- Built from a detached clean commit `abec2bc` = B25b `a84c426` + only the D1 fix.
- Regenerated all role wheels/sdists and the root build; `release-fix4/` is the frozen
  candidate consumed by this index.
- Re-ran `package-install` (PASS incl. scheduler task), B15 authorization (PASS) and the
  affected regression tests.

## Known gaps (not closed by this changelog)

- H4 and H6 remain OPEN; A-29 and C35 signatures are absent, so H7 is OPEN.
- C33/C34 install evidence and C's static-asset manifest are absent.
- The B26a reproduction is same-agent self-reproduction, not an independent operator.
- LSF live execution, C DSH/browser handshake, and running-task disconnect recovery
  remain `NOT_VERIFIED`.
- ADR-005 licensing/open-source release remains a user decision; no license changed.
