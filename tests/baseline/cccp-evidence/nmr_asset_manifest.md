# NMR/DP5 model asset manifest (consolidated, for T39)

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 30 (C05-s6)
- **Produced by:** T30 final audit — `.omo/evidence/acp-parallel-plan-3-cccp/` (`t30-probe.json`,
  `t30-audit-raw.txt`; gitignored) · sibling audit doc `asset_packaging_closure_audit.md`
- **Single asset location:** `src/cccp/analysis/nmr/model_assets/` (data dir, **no** `__init__.py`)
- **Fresh snapshot:** 2026-10-19 (shared dirty worktree; only T30 files staged)
- **Cross-references:** T56 `build_spec_first_batch.md` §2 (master digest table) · T41
  `license_availability_statement.md` §2/§4 (license + wheel cross-check) · T55
  `t55-digest-table.md` (pre/post move).

Consumers: **T39** (full-release manifest / final change request) — this table is the asset
input list. The license provenance column is the T39 license/notice input; the wheel-column
values are the T40a/T54 cross-check values.

## 1. Single location

| Item | Value |
|---|---|
| Directory | `src/cccp/analysis/nmr/model_assets/` |
| `__init__.py` present | **No** (`asset_dir_has_init=false`) — package *data*, not a sub-package |
| Files | **8** (regular files, `find -maxdepth 1 -type f` = 8) |
| `du -sb` | **65,513,350 B** (== T56/T55 recorded value) |
| `du -sh` | 63M |
| Sum of file bytes | 65,504,254 (du − 4096 directory entry) |
| Resolved by | `cccp.analysis.nmr.assets.asset_dir()` / `resolve_asset(name)` — the only path deriver |

## 2. Digest table (file · bytes · sha256)

Fresh `sha256sum` (T30, 2026-10-19) printed below; every hash equals **T55 pre-migration**,
**T55 post-migration**, and the **T56 §2 master**.

| # | File | Bytes | sha256 (T30 fresh) | == pre-migration | == T56 master |
|---|---|---|---|---|---|
| 1 | `atomic_reps.gz` | 22,171,041 | `bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65` | YES | YES |
| 2 | `frag_reps.gz` | 18,044,581 | `e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64` | YES | YES |
| 3 | `i_w_kde_mean_s_0.025.p` | 24,353,668 | `2d523808d3859e8a48c695f5dbf8f9b9fe1fd3f02ee2dc70c8016414cf8f383e` | YES | YES |
| 4 | `folded_scaled_errors.p` | 851,489 | `c59bd8d3218b28db0e5dde3cf70911688ce530351c45f7930a4b470bd9d200dd` | YES | YES |
| 5 | `c_w_kde_mean_s_0.025.p` | 79,518 | `246025abffd97e32cc405d8956deaf3fd03e7426ff688e7052340135e79dc309` | YES | YES |
| 6 | `tms_references.txt` | 4,578 | `5ff2429aa73ade13aa6a98b3cc6433cc8eedd9d2a687c6b9fa907be03be7dcf7` | YES | YES |
| 7 | `LICENSE-DP5` | 1,175 | `55bc399b93914549e797365ac07b46527775f9aa28275ccb9fe43c4ebaf08146` | YES | YES |
| 8 | `NOTICE.md` | 3,204 | `f99522ae9c481ceacba628e994cdd8a958b6febaa7ae66136020849cc6925ad0` | YES | YES |

**8/8 identical to pre-migration values and to the T56 master table.**

`dp5q_stub_params.json` is **absent** (registered T2/T4 baseline gap — not part of the 8-file
set, not a regression; see audit §3 reader 5).

## 3. License / notice / provenance

| # | File | License | Provenance |
|---|---|---|---|
| 1–3, 4–6 | `.gz` / `.p` / `.txt` assets | **MIT** (via `LICENSE-DP5`) | Redistributed from `Goodman-lab/DP5` @ `b6cf559007a5d13fe79654f37daf945ee1661a23` (2023-07-15) |
| 7 | `LICENSE-DP5` | MIT text body (PyDP4 integrated workflow v0.4, © 2015 Kristaps Ermanis, Jonathan M. Goodman) | Ships in-wheel with the assets |
| 8 | `NOTICE.md` | NOTICE / attribution | DP4: Smith & Goodman, *JACS* 2010, 132, 12946; DP5: Howarth, Ermanis & Goodman, *Chem. Sci.* 2021; source revision recorded |

Both license/notice files travel **in the same directory** and ship in the wheel — **do not
separate them** when T39 finalizes the release layer. Full license text: T41
`license_availability_statement.md` §2.

## 4. Cross-reference — T56 / T41 / T55

| Source | What it contributes | Agreement |
|---|---|---|
| T56 `build_spec_first_batch.md` §2 | Master digest table (bytes + sha256 + license column) | T30 fresh `sha256sum` == T56 master 8/8 |
| T41 `license_availability_statement.md` §2 | License/notice statement + 8-file list | Same 8 files, same bytes; T30 confirms on-disk presence |
| T41 §4 | Declaration vs T40a wheel-namelist cross-check | 1:1, zero diff — wheel ships exactly these 8 at `cccp/analysis/nmr/model_assets/` |
| T55 `t55-digest-table.md` | pre→post move equivalence | T30 == pre == post 8/8 |

## 5. Single-copy proof

- `find . -name <basename> -not -path ./.git/*` → each basename appears **once** under
  `src/` at the unified location; the only extra hit is the **gitignored stale build artifact**
  `build/lib/acp/nmr/models/<name>` (`.gitignore:7 build/`, untracked, dated 2026-09-06 — a
  setuptools `build/` output predating the move, **not** a source second copy).
- Legacy directory `src/acp/nmr/models/` is **empty and untracked**
  (`git ls-files src/acp/nmr/models/` = ∅; 0 files on disk). The surviving
  `src/acp/nmr/models.py` is the compatibility **shim module** re-exporting
  `cccp.analysis.nmr.models`, not a data directory.
- No reader derives a path from `__file__` except `assets.py` itself (`grep __file__` =
  `assets.py:41` only).

## 6. Source vs wheel evidence

| Environment | Status | Evidence |
|---|---|---|
| **Source tree (current HEAD)** | **VERIFIED (T30 fresh)** | `sha256sum` 8/8; `du -sb`; reader probes; isolation; slice guard |
| **Wheel (frozen commit `5518aa6`)** | **VERIFIED (cited, T40a — not re-run by T30)** | `cccp-0.2.0-py3-none-any.whl` sha256 `bf0a44ce…31c3ca0a2`; namelist 8/8 at unified path with in-wheel digests == this table; 0 `acp*` entries |
| **Wheel re-run at current HEAD** | **NOT_VERIFIED** | T54's candidate re-verification (post-B40a) — out of T30 scope |
| **Real QC programs (ORCA/…) exercising assets** | **NOT_VERIFIED** | Not needed for asset/read-path audit |
