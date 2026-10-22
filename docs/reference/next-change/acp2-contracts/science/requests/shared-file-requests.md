# ACP2 Plan A — Shared-File Requests (A-08)

Status: open, low risk. These files are **B-owned**. A has not edited them and
must not edit them. This document is the exact change request.

## 1. Documentation drift: "14 workflows / 14 task cores"

The live code has **15** active workflows and **15** `TaskKind` members. The
following B-owned prose still says 14. The count authority is the code, not the
prose (`baseline.md` §8.1/§8.8).

### `README.md`

| Line | Current text (quoted) | Requested change |
|---|---|---|
| 118 | `兼容提示：\`GET /api/workflows\` 现恰好返回 14 个 active 工作流（合成 \`fake\` id 不再公开列出，内部提交受理不变）；外部脚本请直接消费响应内容，勿硬编码旧的 15 项固定列表` | `14 个` → `15 个`; keep the rest of the compatibility note. |
| 198 | `│   ├── workflows/                # 工作流编排（14 active）` | `14 active` → `15 active`. |
| 232 | `│   ├── tasks/                # 14 个任务核心（singlepoint/optimize/frequency/scan/irc/casscf/` | `14 个` → `15 个` and add `xtb_constrained_opt` to the enumerated list. |
| 245 | `... 任务层（**唯一执行驻点**，14 个任务核心）...` | `14 个` → `15 个`. |
| 249 | `... 14 个工作流的执行全部经 \`cccp.calculation\` 任务核心` | `14 个` → `15 个`. |
| 517 | `│  cccp/calculation — 任务层（唯一执行驻点，tasks/ 14 任务核心）        │` | `14 任务核心` → `15 任务核心`; widen the box if needed. |

Exact edit for line 232's enumerated tail: append
`xtb_constrained_opt` to the list that already contains
`singlepoint/optimize/frequency/scan/irc/casscf/thermochemistry/conformer_search/md_sampling/clustering/censo_refine/nmr_shielding/xtb_path_search/orca_gradient`.

### `docs/reference/current/ACP_CCCP_Task_API_DevDoc.md`

| Line | Current text (quoted) | Requested change |
|---|---|---|
| 8 | `layer landed (todo 25), all fourteen \`run_*\` task cores landed (todos 17–22,` | `fourteen` → `fifteen`. |
| 90 | `| \`cccp.calculation.tasks/*\` | the fourteen \`run_*\` task cores ...` | `fourteen` → `fifteen`. |
| 147 | `All fourteen entry points share one signature shape:` | `fourteen` → `fifteen`. |
| 677 | `All fourteen task cores are executable and are the **single execution core**` | `fourteen` → `fifteen`. |
| 862 | `... all 14 workflows callable with no execution bypass` | `14` → `15`. |

Rationale: `xtb_constrained_opt` landed after the 14-entry list was written. The
runtime evidence is `baseline.md` §8.1 and the A-06b matrix
(`../task-compatibility-matrix.md`).

## 2. AGENTS.md note

**No AGENTS.md edit is required.** The root `AGENTS.md` in this worktree already
says `tasks/ 15 核心` and `可运行工作流（15，registry 驱动）`; the nested
`src/acp/workflows/AGENTS.md` already says **15 active workflows**. There is no
drift to report at those paths.

If B wants a cross-reference, the exact optional sentence is:

```text
> ACP2 science contract freeze: see `docs/reference/next-change/acp2-contracts/science/handoff-H2.json` (contract set `acp2.science` v1, frozen 2026-10-22).
```

A does not request it and does not add it.

## 3. Other shared files checked, no request

- `config/defaults.yaml` — no ACP2 science default added; Python built-in
  defaults remain authoritative.
- `pyproject.toml`, `requirements*.txt`, CI configs — untouched.
- `src/acp/**`, `src/acp_services/**`, `frontend/**` — untouched.
- Version constants — untouched.

## 4. Application rule

Apply the README/DevDoc corrections as one B-owned documentation commit after
the ACP2 science handoff is consumed. A will re-check the quoted lines and the
15 count in the next handoff; until then this remains a request, not a claim
that the drift is fixed.
