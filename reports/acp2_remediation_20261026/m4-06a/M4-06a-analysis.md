# M4-06a 分析说明（升级 / 回滚 / 失败注入）

本文件是人工分析说明，与自动生成的 `README.md` / `summary.json` / `migration-matrix.json`
共存；重跑 harness 只覆盖 JSON 与 `README.md`，不覆盖本文件。

## 升级对

* **previous**：git tag `acp2-baseline-20261026` = `c46370639fae48f8dab286b4f3baa63affc26af2`，
  由临时 worktree 构建（不污染主树），`release-manifest.json` 源状态 `clean=true`。
* **candidate**：`be6c5e82916d6b14382cebd524a3c10133e0c78a`（本车道开工时 HEAD；
  M4-02/03/05 并行车道已在其上提交）+ **本任务发现的迁移修复**
  （`src/acp_services/runtime/admission.py`，未提交）。`dirty_paths` 如实记录该文件；
  `release-manifest.json` 的 `artifacts` 覆盖全部 6 个发行件的 SHA-256，
  源文件哈希覆盖发行内容，可复核。
* 两个制品各自安装进独立 venv（`pip install` 本地 wheel，依赖来自离线可用的
  index/cache），装后探针确认 `acp`/`acp_services` 解析到 venv `site-packages`，
  版本 0.3.0，且 previous 侧没有 `science_plugins` / `verify_usage_history` 面。

## 演练数据（真实写入）

* HTTP keyed / keyless submit、真实 `acp mcp serve` submit（不带 key）由 previous
  Controller 产生；备份前全部取消并等到终态（`previous.legacy_tasks_quiesced_before_backup`），
  因此备份是时间点一致的副本（868 个文件，逐字节比对）。
* 其余 legacy 状态由 `acp2_upgrade_pair_helper.py seed` 在 previous venv 内用旧
  Services writer 写入：approval、frozen `auto_` 行、pending 行、不可归属行、
  prepare-v1 operation、Proposal/ResearchRecord、历史 usage receipt、capability 冻结快照。
* 真实旧 MCP 行的账本键是 `op_…`（`auto_` 派生键被旧 MCP 折叠进 `op_` 作用域键）；
  该观测写入 `previous-state.json::pre_m0_05_mcp_row`。M0-05 冻结的 `auto_` 查找
  规则另由旧代码的 `derive_idempotency_key` + 旧 `JobStore` 写出的 `auto_` 行验证。

## 本次发现的真实缺陷（已修复，属 M4-06a 产出）

previous → candidate 升级时候选 Controller 启动失败：`no such column: job_id`。
`idx_admission_usage_job` / `idx_research_budget_receipts_job` 引用了 M0-07 的追加列，
却写在 `_CREATE_TABLE_SQL`：升级时旧表已存在（`CREATE TABLE IF NOT EXISTS` 空操作），
索引先于 `migrate_admission_schema` 的 `ALTER TABLE` 创建。

修复：新增 `_ADDITIVE_INDEXES`，由 `migrate_admission_schema` 在补齐列后创建；
新库与升级库共用同一路径。守护测试（快速层）：
`test_legacy_admission_db_upgrades_without_column_error`、
`test_historical_usage_is_reported_unverified_not_fabricated`、
`test_fresh_admission_db_still_gets_the_same_indexes`。

## M0 冻结语义复核（逐项）

| 项 | 旧形态 | 新语义 / 证据 |
|---|---|---|
| 旧 local scope | `identity_version=1`，无归属列 | 迁移 024 → `legacy-local` + `legacy-verified`；committed 行按原 scope+digest 重放回原 TaskRef（`legacy.classified_committed_verified`、`legacy.replay_verified_row_returns_original_task`） |
| pending | 过期 lease 的 claimed 行 | `legacy-pending`，可重试；不当作 replay（`legacy.classified_pending_not_replayable`） |
| 不可归属 | 无 principal/project 分隔符的 scope | `legacy-unknown`+`legacy-unverified`，一律 typed conflict，绝不盲重放（`legacy.classified_unattributable_unverified`、`legacy.unattributable_row_is_conflict_not_adopted`） |
| 跨 scope 同 key | 单一折叠 scope | 另一 verified scope 用同 key → conflict（`legacy.other_scope_is_conflict_not_replay`） |
| 旧 request digest | v1 字段表 | 行保持 v1；同字节=同请求，tampered payload=conflict（`legacy.request_digest_v1_verifies_and_rejects_tamper`） |
| 旧 MCP `auto_` 键 | `auto_`（旧 MCP 账本键 `op_`） | 公式字节冻结、只读查找；keyless submit → `idempotency_key_required` 零副作用（`legacy.auto_derivation_is_byte_frozen`、`upgrade.keyless_mcp_submit_refused_without_side_effect`） |
| 旧 prepare v1 | `op_`、无归属 | v1 唯一索引保留；local-single 同内容 merge，其他 verified principal → conflict；新行 v2 `prep_`（`prepare.*`） |
| 历史 usage | 无 receipt identity 的旧行 | 原样保留；`verify_usage_history` 报 `binding_missing`/`verified=false`，不重标 trusted、不伪造（`usage.historical_rows_preserved_not_fabricated`） |

## 回滚与失败注入

* 回滚：停候选 → 删除 run root → 备份逐字节恢复 → previous Controller 重新启动；
  schema 回到 23/1/0、`integrity_check=ok`、旧任务/研究记录可读、候选新增行随备份消失。
* 失败注入：在 024 前 SIGKILL（exit -9）→ 23 条 migration、5 个 job、`integrity_check=ok`；
  随后同一文件迁移到 26 且 job 数不变（无裸删、无半记录迁移）。

## 边界与遗留（如实标注）

* 不使用生产 run root、真实 QC/LSF/模型/DSH/浏览器；旧任务在备份前取消，因此本证据
  不声明任何科学结果（H4 仍需 M4-02/03/05/06b）。
* previous 基线没有 `science-plugins/` enablement ledger / `acp science-plugin`；因此
  “升级前插件 enablement 快照”不存在（NOT_APPLICABLE）。只读快照项以 previous 真实写入的
  **capability 冻结快照**字节验证；真实插件安装/启用/卸载归 M4-05。
* 迁移失败注入覆盖“进程在迁移中途被杀”；未覆盖磁盘满/并发迁移进程（M4-03/M5 范畴）。
* candidate 制品含未提交的迁移修复；提交后从 HEAD 直接重建即可得到 clean manifest，
  `--reuse-artifacts` 可复用当前制品。

## 制品与源码快照位置

* 升级对制品、venv 与 pinned 源码快照均在隔离 artifact store：
  `/ACPServer/acp2-artifacts/remediation/m4-06a/{previous,candidate,venvs,candidate-src}`。
  `candidate-src` 是 detached worktree（HEAD + 迁移修复），harness 默认优先使用它，
  因此重跑无需主工作树干净；`release-manifest.json` 记录 revision/tree/dirty_paths。
* 未触碰 `/ACPServer/acp2-runs/**`（生产 run root）与生产 systemd 服务。
