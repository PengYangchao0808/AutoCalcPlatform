# B14a — P1 integration candidate merge log

Candidate: `fbac5abb50ba7f86603b786fc98d857df52f5f2a`
Tree: `0a9436f24a2a62232c2186a8ba75d0b05f3397a8`
Branch: `acp2/integration-p1` (worktree `/opt/acp2-dev/integration`)
Created: 2026-10-23 (Asia/Shanghai)

## 1. Base and changesets

| Element | SHA | Note |
|---|---|---|
| B candidate base | `a72e8db08416c882d52e1cfefbdf8b1f1b3d74d4` | B00–B13 plus B fixes |
| Historical delivery / debug base | `3670790363c7a1959ad1dbe4b4297adec518952a` | common merge base of A/B/C |
| Final debug candidate (source overlay) | `sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12` | B00 `final-debug-patch/manifest.json` |
| A changeset | `9977a106445177708e929557f9460e86914787ad` | `acp2/a-science` |
| B changeset | `a72e8db08416c882d52e1cfefbdf8b1f1b3d74d4` | `feat/frontend-plan1-closure` |
| C changeset | `35614fd83f616005a0546af35631c477151e7dde` | `acp2/c-experience` |

The B worktree P1 candidate (`10eb71e`) was not used as the base because it stops at
B00–B09. The main B branch `2a57d6b` carries B00–B13, but it never committed 144
overlay files and still matched `096cdff` on 24 tracked overlay files. Both facts
were measured against the B00 manifest, so the base was corrected before A/C were
merged.

## 2. Commit sequence (all in the integration worktree)

1. `a44e39a` — **restore the frozen B00 debug overlay onto the B candidate** (B-owned).
   * 144 files absent from `2a57d6b` added.
   * 24 files where `2a57d6b` still equalled `096cdff` replaced with the overlay bytes.
   * 169 files changed, +43610/−209.
   * Every restored file was verified `sha256(file) == manifest.files[].sha256`
     (168/168 restore targets matched; the source commit was A's branch only because
     it carries a byte-identical copy of the B00 overlay — not an A domain change).
   * `src/acp/api/server.py` keeps the B13 admission wiring and gains the overlay's
     `acp_web_assets` fallback block (5 added lines, disjoint from the B13 hunk).
2. `b00bafc` — **merge A by domain ownership** (parents `a44e39a` + `243d2b7`).
   * 178 files, +68301, all A-owned.
   * `src/cccp/**`, `acp2-contracts/science/**`, `scripts/acceptance/acp2_science.py`,
     `tests/test_acp2_science_*.py`, `tests/fixtures/acp2/science/**`.
   * A's uncommitted worktree edit (`scripts/acceptance/acp2_science.py`, +383 lines)
     and untracked `science/handoff-H4.json` are **not** part of A's delivered SHA and
     were not included.
3. `fbac5ab` — **merge C by domain ownership** (parents `b00bafc` + `e03a9dd`).
   * 137 files, +42859/−11, all C-owned.
   * `frontend/**`, `rph/**`, `acp2-contracts/experience/**`,
     `scripts/acceptance/acp2_experience*`, `acp2_mcp_live_client.mjs`,
     `acp2_mcp_negotiation_probe.py`, `acp2_route_probe.sh`,
     `acp2_dsh_tool_snapshot.mjs`, `tests/test_acp2_experience_*.py`.
   * C's branch predates B00–B13 and therefore *lacks* B-owned runtime/acceptance
     files. Those absences were not treated as deletions.

Candidate delta over the B base (all three commits): 484 files, +154770/−220.

## 3. Overlay preservation check at the candidate

Manifest total 1906 files. At the candidate:

* 1886 files match the B00 overlay byte-for-byte.
* 20 files deliberately differ because B00–B13 evolved after the overlay
  (`docs/.../ACP_Services_MCP_Client_Matrix.md`, `scripts/acceptance/real_environment.py`,
  `src/acp/api/{routes,server,v1_routes,v1_schemas}.py`, `src/acp/cli.py`,
  `src/acp/scheduler/manager.py`, `src/acp/services_{bootstrap,data_bootstrap}.py`,
  `src/acp_services/...` 10 files, `tests/test_services_runtime_usecases.py`).
* For each of those 20, every textual line the overlay added relative to `096cdff`
  is present in the candidate (checked line-by-line; 0 missing), including the
  manually merged `server.py` fallback block.

No file was lost from the B00 manifest.

## 4. Cleanliness

`git status --porcelain` is empty in the integration worktree after every commit and
at the frozen candidate. No code was edited after the candidate commit while the
guards/suite were running.
