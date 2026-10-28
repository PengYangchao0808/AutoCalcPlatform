# C10-s4 — 最终证据与交接汇总（c10-final-evidence-index.md · tracked）

- **owner:** C 路科学负责人（渠道 `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md`）
- **plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 51（C10-s4）
- **base snapshot:** HEAD `355d96f66fcdaca2fc53d17c5e3eeca0319358ca`（T54 候选提交；本汇总在 T54 之后执行，评审 R4 §3.1）
- **tracked_path:** `tests/baseline/cccp-evidence/c10-final-evidence-index.md`（C直写，`write_registry.md` §4.9）
- **evidence root:** `.omo/evidence/acp-parallel-plan-3-cccp/`（gitignored）+ `tests/baseline/cccp-evidence/`（tracked）
- **规则**: 仅汇总已可用证据；仍等待跨路应用/集成的项目**显式保持未关闭**；本汇总不构成 B 发布的额外等待项（发布前终验消费 T54；生产部署/发布后核对归 B-T76/F5）。

---

## 1. 证据索引（逐 todo 一行：todo → 证据路径 → 三态判定 → 范围状态）

> 判定三态：`PASS` / `FAIL` / `NOT_VERIFIED`（真实科学程序/未运行面）。范围状态：`IN_SCOPE` / `DEFERRED` / `NOT_APPLICABLE`。
> 路径前缀 `.omo/evidence/acp-parallel-plan-3-cccp/` = **E**；`tests/baseline/cccp-evidence/` = **T**。脚本交叉核对见 §7。

| todo | 证据路径 | verdict | scope |
|---|---|---|---|
| 1 | E/c00-baseline.md | PASS | IN_SCOPE |
| 2 | E/c00-closure-ledger.md | PASS | IN_SCOPE |
| 3 | E/c00-first-slice-decision.md；E/c00-closure-classification.md | PASS | IN_SCOPE |
| 4 | E/c00-numeric-baseline.md | PASS | IN_SCOPE |
| 5 | T/write_registry.md；T/handoff_registry.md；E/task-5-*.log；E/task-5-verify.sh | PASS | IN_SCOPE |
| 6 | E/c00-retired-disposition.md | PASS | IN_SCOPE |
| 7 | E/c01-first-slice-spec.md；E/t7-isolation-evidence.txt；E/t7-selection-rerun.txt；E/t7-first-slice-decision-program.py | PASS | IN_SCOPE |
| 8 | E/c01-first-slice-spec.md §7；E/t7-isolation-evidence.txt（T7 verdict B 证据；未执行迁移） | PASS（NOT_APPLICABLE 判定本身已记录） | **NOT_APPLICABLE**（本次首批范围；见 §5） |
| 9 | E/c01-first-slice-spec.md §7；E/t7-isolation-evidence.txt（同 T8） | PASS（NOT_APPLICABLE 判定本身已记录） | **NOT_APPLICABLE**（本次首批范围；见 §5） |
| 10 | T/handoffs/HN-01-b06-closure.md；E/task-10-*.md | PASS | IN_SCOPE |
| 11 | E/task-11-*.md | PASS | IN_SCOPE |
| 12 | E/task-12-*.md | PASS | IN_SCOPE |
| 13 | T/handoffs/HN-02-frames-mapping.md；E/t13-qa-frames-mapping.txt；E/t13-zero-acp-imports.txt；E/t13-qa-real-frame.txt | PASS（状态 A 契约可消费；状态 B 跨路端到端 = blocked-by-B，见 §3） | IN_SCOPE |
| 14 | T/handoffs/HN-02a-display-constraints.md；E/t14-sample-derivation-output.txt | PASS | IN_SCOPE |
| 15 | T/patches/t15-models-hartree-to-kcal.patch；T/patches/t15-energy-graph-hartree-to-kcal.patch | PASS（补丁已交付；集成 pending-B，见 §3） | IN_SCOPE |
| 16 | E/t16-dedup-and-scope-records.md；E/t16-parse-equivalence-comparison.txt；E/t16-{pre,post}-parse-equivalence.json | PASS | IN_SCOPE |
| 17 | E/task-17-*.md；E/t17-equivalence-result.txt；E/t17-isolation.log；T/patches/t17-io-structures.patch | PASS | IN_SCOPE |
| 18 | E/task-18-*.md；E/t18-equivalence-result.txt；E/t18-isolation-live.log；T/patches/t18-acp-results-{orca-parser,frequencies}.patch；T/patches/t18-acp-frequency-modes-test.patch | PASS | IN_SCOPE |
| 19 | E/t19-live-acceptance.log；E/t19-same-object.txt；T/patches/t19-acp-results-frequencies.patch | PASS | IN_SCOPE |
| 20 | E/t20-contract-ruling.md；E/t20-serialization-diff.txt；E/t20-same-object.txt | PASS | IN_SCOPE |
| 21 | E/t21-cumulative-unification.md；E/t21-equivalence-diff.txt；T/patches/t21-confsearch-{contracts,result-helpers}.patch | PASS | IN_SCOPE |
| 22 | E/t22-acceptance-pytest.log；E/t22-isolation.log；E/t22-grep-gates.log | PASS | IN_SCOPE |
| 23 | E/task-23-*.md；E/t23-equivalence.py；T/patches/t23-tsmode-contracts-reexport.patch | PASS | IN_SCOPE |
| 24 | E/task-24-*.md；E/t24-post-verification.txt；T/patches/t24-acp-workflows-{ensemble,energy-shared,ensemble-thermo,xtbmd-md,xtbmd-censo-energy}.patch | PASS | IN_SCOPE |
| 25 | E/task-25-*.md；E/t25-qa-identity.txt；E/t25-inventory.md | PASS | IN_SCOPE |
| 26 | E/task-26-*.md；E/t26-capture-post.json | PASS | IN_SCOPE |
| 27 | E/task-27-*.md；E/t27-probes.txt | PASS | IN_SCOPE |
| 28 | E/task-28-*.md；E/t28-coldstart-probe.txt | PASS | IN_SCOPE |
| 29 | E/t29-report-trio.txt；E/t29-split-symbols.md；E/t29-payload-boundary.txt | PASS | IN_SCOPE |
| 30 | T/asset_packaging_closure_audit.md；T/nmr_asset_manifest.md；E/t30-audit-raw.txt；E/t30-probe.json | PASS | IN_SCOPE |
| 31 | E/c06-bounded-baseline.md（同文 tracked 副本 T/c06-bounded-baseline.md）；E/c06-measure.py；E/c06-raw/ | PASS | IN_SCOPE |
| 32 | E/c06-sample-baseline.md；E/c06-generate-samples.py；E/c06-samples/；E/c06b-measure.py；E/c06b-raw/ | PASS | IN_SCOPE |
| 33 | T/handoffs/HN-03-bounded-products.md；T/handoffs/walks/raw/；tests/fixtures/science_products/bounded/ | PASS | IN_SCOPE |
| 34 | E/c07-precision-proposal.md；E/c07-precision-ledger.md（**仅决策提案；未改任何值**） | PASS（提案交付） | **DEFERRED**（不同精度常量统一待批准；见 §5） |
| 35 | E/t35-core-type-consolidation.md；E/t35-t52-identity.txt；E/t35-t52-serialization-equivalence.txt | PASS | IN_SCOPE |
| 36 | T/config_domain_boundary.md；E/t36-config-snapshot-{before,after}.json；E/t36-guards.log；E/t36-tests.log | PASS | IN_SCOPE |
| 37 | E/task-37-*.md | PASS | IN_SCOPE |
| 38 | E/task-38-*.md；E/task-38-behavior-{before,after}.txt | PASS | IN_SCOPE |
| 39 | T/release_manifest.md；T/patches/t39-01-pyproject-ownership.patch；T/patches/t39-02-cccp-pyproject.patch；T/patches/t39-build-proof.txt | PASS（交付）；**B 集成 = pending**（t39-01 对候选已漂移，见 §3/§6） | IN_SCOPE |
| 40 | E/task-40a-*.md；E/task-40b-*.md；E/t40a-raw-commands.txt；E/t40b-raw-commands.txt；E/t40b-raw/ | PASS（出树预验；合并复验 = T54） | IN_SCOPE |
| 41 | T/license_availability_statement.md | PASS | IN_SCOPE |
| 42 | T/node_dependency_sync_request.md；E/task-42-import-scan.md；E/task-42-node-surface-sweep.md | PASS（请求交付）；节点实机 `pip install -r` = NOT_VERIFIED（B 回执面，T54 venv pipcheck PASS 为替代证据） | IN_SCOPE |
| 43 | T/python_api_support_matrix.md；E/task-43-examples.txt；E/task-43-inventory.txt；T/examples/gen_public_api_inventory.py | PASS；py3.10/3.12 行 = NOT_VERIFIED（宿主仅 3.9/3.11） | IN_SCOPE |
| 44 | T/plugin_pilot_spec.md | PASS | IN_SCOPE |
| 45 | T/plugin_sdk.md；T/examples/example_c_plugin_xyz.py | PASS | IN_SCOPE |
| 46 | T/handoffs/HN-06-version-digest.md；T/handoffs/samples/version_digest_samples.json | PASS | IN_SCOPE |
| 47 | E/c09-reuse-assessment.md；E/c09-candidate-compare.txt | PASS（评估完成：cclib/QCEngine 不接管，QCElemental 未决） | IN_SCOPE |
| 48 | T/slice_guard.md；T/workflow_task_map.md；E/task-48-*.log | PASS | IN_SCOPE |
| 49 | E/c10-old-new-comparison.md；E/c10-compare-tristate.txt；E/c10-compare-{identity,nmr,pes,simple}.txt；E/c10-raw/ | PASS（NMR×4/PES×1/simple×2/CLI×1 = PASS，失败 0）；真实 QC/LSF/Web/MCP = **NOT_VERIFIED**（如实标注） | IN_SCOPE |
| 50 | E/task-50-*.md；E/t50-equivalence-pre.txt；E/task-50-identity-probe.txt | PASS | IN_SCOPE |
| 51 | 本文件（T/ c10-final-evidence-index.md）+ E/c10-final-index-crosscheck.py（§7） | PASS（本文件即交付物） | IN_SCOPE |
| 52 | E/task-52-*.md；E/t52-serialization-{pre,post}.json；T/patches/t52-core-models-structure.patch | PASS | IN_SCOPE |
| 53 | E/task-53-*.md；E/task-53-fingerprint-{pre,post}.json | PASS | IN_SCOPE |
| 54 | E/task-54-*.md；E/task-54-raw/（首次运行 superseded-e021ad3 不作数） | PASS（候选 `355d96f` 声明范围内全断言通过）；**真实 QC 程序/生产部署/发布后核对 = NOT_VERIFIED** | IN_SCOPE |
| 55 | E/t55-verification-summary.md；E/t55-digest-table.md；E/t55-{pre,post}-probe.json；T/patches/t55-c39a-packaging-test.patch | PASS | IN_SCOPE |
| 56 | T/build_spec_first_batch.md；T/patches/c39a-pyproject-first-batch.patch；T/patches/c39a-build-proof.txt | PASS（交付）；c39a 已被 B 应用 @`8b9e0e6`（见 §3） | IN_SCOPE |
| F1 | 消费 T/ c10-final-evidence-index.md + E/task-54-*.md（入场范围） | **NOT_VERIFIED**（F 波未执行；本索引为其输入） | IN_SCOPE（F 波） |
| F2 | 消费本文件 | **NOT_VERIFIED**（F 波未执行） | IN_SCOPE（F 波） |
| F3 | 消费本文件 | **NOT_VERIFIED**（F 波未执行） | IN_SCOPE（F 波） |
| F4 | 消费 E/task-54-*.md（必须消费 T54 结果）；当前 `tests/test_f4_scope_audit.py` 1 RED = 他道未登记站点（§3 未决项 4，非 C 缺陷） | **NOT_VERIFIED**（F 波未执行） | IN_SCOPE（F 波） |

**FAIL 项：0。** 所有 IN_SCOPE 且已执行的 C 侧 todo（1–7、10–33、35–56）verdict 均 PASS；NOT_VERIFIED 项逐项如实标注（真实 QC、Web/MCP、LSF 实机、py3.10/3.12、F 波、生产面）。

---

## 2. 可合入切片清单（C 车道提交系列，`dfad6b30..355d96f`）

> 单作者仓库（lanes 共享 committer），按提交信息 scope 区分 C 车道。纯搬运与行为变化已分提交。

| 波次 | 提交 | 内容 |
|---|---|---|
| W0 | `60960d2` | T5 写权/交接 registry（tracked） |
| W1 | `ffcf1b8`,`5aaae5d` | T10 B06 逐工作流闭包 handoff（tracked） |
| W2a | `e89af04` | T53 identity fingerprint 下沉 |
| W2a | `5147e7e` | T11 版本化产品契约骨架 |
| W2a | `91b63a6` | T16 结构摄入归并 |
| W2a | `457f28e` | T20 采样核心迁移 |
| W2a | `7f11fa4` | T48 逐片依赖/唯一性 harness |
| W2a | `16b308b` | T55 NMR 资产统一入口同批切换 |
| W2a | `fd950c7` | T52 Structure 族前移 |
| W2a | `4d2c6f8`,`d644d61` | T13 frames 映射 + 实装闭包（tracked） |
| W2a | `627947e` | T14 显示约束/二面角决策（tracked） |
| W2b | `fd005b0`,`ac60ab7`,`48b3d2f`,`39d076f`,`b864140`,`7fd5a90` | T25–T28 NMR 批 1–4 + T17 io patch 登记 |
| W2b | `6805dc3`,`fd00c57` | T18 ORCA 解析归并 + patch 登记 |
| W2b | `6e1c667` | T19 振动产品归并 |
| W2b | `6b5ebec` | T21 Boltzmann/选择迁移 |
| W2b | `9a64228` | T22 PES 科学迁移 |
| W2b | `dfc192e` | T23 tsmode 科学迁移（contracts re-export patch 已入树） |
| W2b | `29b2e8f` | T24 retired 引擎闭包实施 |
| W2b | `72f7b44` | T29 报告链 + revision/method-config 拆分 |
| W3 | `260d032` | T32 有界产品实现 |
| W3 | `e447783`,`fe20037`,`9152f94` | T33 有界交付 handoff（tracked） |
| W3 | `e8aeea5` | T38 qc/cluster 审计 + 异常窄化 |
| W3 | `29ed796` | T36 config 域分离 |
| W3 | `4915a4c` | T37 legacy executable discovery 撤除 |
| W3 | `8bf635b` | T30 NMR 资产/打包闭包终审（tracked） |
| W4 | `040db3b` | T56 首批构建规格 + package-data 请求（tracked） |
| W4 | `29a185f` | T39 完整发行清单/变更请求（tracked） |
| W4 | `5072375` | T41 许可/可获得性声明（tracked） |
| W4 | `b9283e5` | T45 插件 SDK + 示例 + 契约测试 |
| W4 | `637d8d3` | T44 插件样板（只读 parser 试点） |
| W4 | `31273af`,`f68274c` | T46 版本摘要字段 handoff（tracked） |
| W4 | `f0f1b58` | T43 Python API 示例/支持矩阵（tracked） |
| W4 | `87ab288`,`60811f0` | T42 节点依赖同步请求（tracked） |
| W5 | `355d96f` | T50 撤旧实现 + 验证过的转发 shim |
| 跨路（B 应用） | `8b9e0e6`,`e021ad3` | C08 joint：c39a package-data + t55 打包测试改指 + HN-05 typing-extensions（B 记回执 `packaging_rollout_receipts.md`；**非 C 提交**，列入为集成事实） |

---

## 3. 交接状态（HN-01..HN-06：完成 / blocked-by-B / blocked-by-A；补丁：已交付 vs 已集成）

> 权威表 = B 路 `tests/baseline/services-evidence/handoff_registry.md`；C 表 `tests/baseline/cccp-evidence/handoff_registry.md` 引用同一版本。**集成判定机器法**：T54 `patch -p1 -R --dry-run` 反向可应用 = 已入候选树（E/task-54-raw/patch-application-check.txt）。

| HN | C 交付 | provider 提交 | B 集成证据 | 状态（T51 收口） |
|---|---|---|---|---|
| HN-01 B06 逐工作流闭包 | 完成 | `ffcf1b8` | 无集成回执（B06 worker/接线未应用） | **delivered · blocked-by-B**（跨路接线/注入移除未做） |
| HN-02 frames 映射 + 五态 fixture | 完成（状态 A 契约可消费） | `4d2c6f8` | 无 B adapter 集成证据 | **delivered · blocked-by-B**（状态 B 跨路端到端保持 open） |
| HN-03 有界产品 + 块索引 + walks | 完成 | `e447783` | 无 B Data 面集成证据 | **delivered · blocked-by-B** |
| HN-04 最终发行打包请求（t39-01/02） | 完成 | `29a185f` | **无**；且 t39-01 对候选 `355d96f` 不再干净应用（E/task-54-raw/t39-01-apply-reject.log：2 hunks FAILED——HN-05/C39a 锚点漂移） | **pending · blocked-by-B**（B40b gate OPEN；**补丁需重锚**，见 §6） |
| HN-05 节点依赖同步请求 | 完成 | `87ab288` | **已集成**：typing-extensions 入 `pyproject.toml:38` + `requirements-node.txt:46` @`e021ad3`（B 回执 §1.5；T54 3×venv `pip check` PASS） | **integrated**（applied_commit `e021ad3`；注：8b9e0e6 为同批 joint 前奏）。**acp_services 代码同步缺口不在本 HN 内闭环 → B06，保持 open** |
| HN-06 版本摘要字段 | 完成 | `f68274c` | 无 B 身份冻结集成证据 | **delivered · blocked-by-B** |

**补丁状态分列（delivered ≠ integrated；T54 §2 回执盘点）：**

| 类别 | 补丁 | 状态 |
|---|---|---|
| 已被 B 应用（反向 dry-run APPLIED） | c39a `c39a-pyproject-first-batch.patch` → `pyproject.toml` | **integrated** @`8b9e0e6`（live `pyproject.toml:97`） |
| 已被 B 应用 | `t55-c39a-packaging-test.patch` → `tests/test_packaging_assets.py` | **integrated** @`8b9e0e6`（T51 复核 `pytest tests/test_packaging_assets.py` = **7 passed**） |
| 已被 B 应用 | HN-05（无 patch 文件，文档请求） | **integrated** @`e021ad3` |
| 已入候选树（C 迁移提交直接改写，非 B 集成回执） | `t23-tsmode-contracts-reexport.patch` → `src/acp/calculations/tsmode/contracts.py` | **in-tree** @`dfc192e`（T54 反向 dry-run APPLIED；由 C 的 tsmode 迁移提交带入，不计 B 验收） |
| **delivered · pending-B**（反向 dry-run 全 NOT_APPLIED） | t15（×2）、t17、t18（×3）、t19、t21（×2）、t24（×5）、t52 | **未集成，保持 open**（acp 侧/测试 shim；T54 §2 #8） |
| **delivered · pending-B 且需重锚** | `t39-01-pyproject-ownership.patch`、`t39-02-cccp-pyproject.patch`（HN-04） | **未集成**；t39-01 相对候选陈旧（§3.3），B 应用前须重锚；t39-02 目标 `packaging/` 不存在于候选 |

### 跨路未决清单（显式保持 OPEN — 不提前关闭）

1. **t39-01/02 重锚**（HN-04）：补丁锚点被 HN-05（`e021ad3`）与 C39a（`8b9e0e6`）漂移；重锚文本 C 可产，但目标 `pyproject.toml`/`packaging/` 为 B-owned → **交 B/T51-F 波**，C 不直改（E/task-54-raw/t39-01-apply-reject.log + reconstruct_m2_pyproject.py 为确定性重建参照）。
2. **acp 侧 shim 补丁 t15/t17/t18/t19/t21/t24/t52 delivered-pending**：候选 `355d96f` 反向 dry-run 全部 NOT_APPLIED；`src/acp/results/{orca_parser,frequencies}.py` 等仍是活旧实现（T49 独立对比因此成立）→ **blocked-by-B**。
3. **`acp_services` 构建同步缺口 → B06**：`build_sync_file_list` 缺 `src/acp_services/**`，远端 sync 后双入口 import 断；`sync.py` 为 B补丁混合文件（write_registry §4.1 禁 C 直改）→ **blocked-by-B**（E/task-42-node-surface-sweep.md；T42 issues）。
4. **F4 审计未登记站点（他道）**：`tests/test_f4_scope_audit.py::test_migration_scope_audit_reaches_migration_files` 当前 1 RED（22 个站点 "changed outside registered migration stations"）——均为**他道已提交文件**（如 `src/acp/chem/embedding.py`），该测试为 B请求共享门，C 不静默放宽 → **owned by B，保持 open**（T51 复核运行记录 §7 附注）。
5. **HN-01/HN-02/HN-03/HN-06 集成**：无 B 应用回执 → blocked-by-B。
6. **真实 QC 程序运行 / macOS+Windows / 生产部署与发布后核对（B-T76/F5）**：NOT_VERIFIED（§5）。

---

## 4. 验收摘要（tracked）

### 4.1 唯一性 / 数值 / 隔离 / 资产证据指针

| 面 | 证据 |
|---|---|
| 源码唯一性（每符号一实现体） | T/slice_guard.md（逐片 uniqueness/dependency harness）+ T/workflow_task_map.md + `tests/test_architecture_invariants.py::test_unique_primitive_definitions`（CI 常绿）；E/task-48-*.log |
| 数值基线/等价 | E/c00-numeric-baseline.md（冻结）→ E/c10-compare-{nmr,pes,simple,identity}.txt（旧树 replay vs 新实现，PASS×8，失败 0）+ E/c10-old-new-comparison.md；黄金值零改动（E/task-22-baseline-rev.txt） |
| 隔离（cccp 不依赖 acp） | `python3.11 scripts/verify_cccp_isolation.py` RESULT PASS（E/t7-isolation-evidence.txt、E/t22-isolation.log、E/t18-isolation-live.log、E/task-54-raw/isolation-*.txt 三次独立记录）+ E/t13-zero-acp-imports.txt |
| 资产（8 NMR 模型文件） | E/t55-digest-table.md + E/t55-{pre,post}-probe.json（统一入口，8/8 摘要一致非空）+ T/nmr_asset_manifest.md + E/task-54-raw/wheel-audit.json（M2/M1 wheel 内 8/8 == master） |

### 4.2 旧调用替换记录（撤旧/shim）

- 撤旧清单与判定：E/task-50-*.md（T50）；身份探针 E/task-50-identity-probe.txt、E/c10-compare-identity.txt（公开旧契约解析到唯一新实现，`same-object` 逐符号）。
- 转发形态台账：T/write_registry.md §4 逐行（RT-SAME / RT-CONV / D-KEEP / D-NOSHIM）；B 应用后删除条件（D-NOSHIM）未满足 → 旧路径 shim 保留，**无第二份 ACP 实现**（`test_unique_primitive_definitions` 守护）。
- 活旧实现（补丁未应用）诚实标注：`src/acp/results/{orca_parser,frequencies}.py`（T49 据此做独立数值对比）；E/t18-mirror-deletion-proof.txt（本地 fallback mirror 已删）。

### 4.3 向 B 交付完成清单（闭包/补丁/打包/字段）

| 交付 | 落点 | 状态 |
|---|---|---|
| B06 逐工作流闭包（四状态表 + 十要素 + 注入点/装配需求） | T/handoffs/HN-01-b06-closure.md | 交付完成；集成 blocked-by-B |
| 混合文件补丁集（t15/17/18/19/21/23/24/52） | T/patches/ | 全部 delivered；t23 语义已入树 @dfc192e（C 迁移提交带入，非 B 回执）；其余 pending-B |
| 打包：首批（c39a）| T/patches/c39a-pyproject-first-batch.patch + T/build_spec_first_batch.md | **integrated** @8b9e0e6 |
| 打包：完整发行（t39-01/02）| T/patches/t39-{01,02}-*.patch + T/release_manifest.md | delivered；**pending-B（t39-01 需重锚）** |
| 打包测试改指（t55）| T/patches/t55-c39a-packaging-test.patch | **integrated** @8b9e0e6（7 passed 复核） |
| 节点依赖（HN-05）| T/node_dependency_sync_request.md | **integrated** @e021ad3 |
| 字段冻结（HN-06 版本摘要）| T/handoffs/HN-06-version-digest.md + samples | delivered；冻结时点早于 B 执行身份发布；集成 blocked-by-B |
| 全局门/护栏（不放宽）| T/slice_guard.md；E/task-48-*.log | C 证据齐；F4 未登记站点修复 = B（§3 未决 4） |

### 4.4 向 A 交付完成清单（产品样例/映射/限制）

| 交付 | 落点 | 状态 |
|---|---|---|
| 产品样例（已安装产物实跑） | E/task-54-raw/samples-*.txt：cccp-only 16/16 ×2 + co-install 7/7（产品读/有界读/报告三件套/TMS/插件/ORCA 解析） | 完成（候选 355d96f） |
| 显示映射（frames→VIEW_REGISTRY） | T/handoffs/HN-02-frames-mapping.md + samples/display_*.json | 状态 A 完成；端到端 blocked-by-B |
| 显示限制/二面角决策 | T/handoffs/HN-02a-display-constraints.md | 完成 |
| 有限产品消费说明（单位/抽样≠全量） | T/handoffs/HN-03-bounded-products.md §A | 完成 |
| API/兼容政策/支持矩阵 | T/python_api_support_matrix.md（含两态诚实：same-object shim vs delivered-patch 活旧实现） | 完成 |

### 4.5 Registry 状态机闭合（待核验→交付→集成→关闭）

| registry | 收口状态 |
|---|---|
| C 侧 handoff_registry.md（HN-01..06） | HN-05 → **integrated**（applied `e021ad3`，测试记录 = T54 pipcheck + T51 packaging 复核）；HN-01/02/03/06 = delivered（blocked-by-B）；HN-04 = pending（blocked-by-B + 重锚）。**其余不关闭**——集成事实仅在有 applied-commit + 测试记录时登记（本次唯一新增 = HN-05） |
| write_registry.md（补丁行） | t55 打包测试行 → integrated @8b9e0e6；c39a（§4.8 pyproject 行）→ 记 "c39a 部分 integrated @8b9e0e6；t39-01/02 pending"；其余 delivered/pending 不动 |
| B 权威表 | 由 B 回填；C 不代记（§0 规则） |

**未能闭合的状态机节点及原因**：所有 pending-B 行（§3 未决清单 1–5）——`delivered ≠ integrated`，无 B 应用提交 + 测试记录即不得写 integrated；F1–F4 未执行。

---

## 5. 范围状态声明

- **NOT_APPLICABLE**：T8（首片迁移实施）、T9（次片迁移实施）——T7 裁定 verdict B（xtb-crest 闭包符号唯一归属 T20/T21/T24；simple 系 8/10 已独立），证据 E/c01-first-slice-spec.md §7 + E/t7-isolation-evidence.txt。**非"完成"、非"删除后续迁移"**；全路线后续科学迁移保留。
- **DEFERRED**：不同精度常量统一（`627.509` 截断、R 不同精度副本等）——T34 提案已交付（E/c07-precision-proposal.md）**待批准；本计划未改任何值**（numeric-baseline 约束全程生效）。必需上游不能用 DEFERRED 绕过（评审 R5）——本计划无 IN_SCOPE 任务依赖该统一。
- **NOT_VERIFIED**（如实标注，不计为通过）：
  - 真实 QC 程序运行（ORCA/xTB/CREST/CENSO/LSF 实机）——T49/T54 仅文本 fixture 只读解析；
  - macOS / Windows 平台；
  - py3.10 / py3.12 宿主矩阵（T43；宿主仅 3.9/3.11）；
  - Web/MCP 科学输出路径（T49 范围外）；
  - 节点实机 `pip install -r`（B 回执面；本地离线 venv pipcheck 为替代证据）；
  - F1–F4 波（未执行，本文件为其输入）；
  - 生产部署 / 发布后核对（B-T76/F5 跨路）。

---

## 6. B 发布物可核对性（消费 T54；本汇总非额外等待项）

| 项 | 值 |
|---|---|
| 候选提交 | `355d96f66fcdaca2fc53d17c5e3eeca0319358ca` |
| 构建配置 sha256 | M2 cccp `8ee89c0c…`；M2 acp `e1bd2faf…`；M1 live `4d40cf84…`（= candidate blob） |
| 产物 sha256 | M2 cccp `c66cc7c7…`；M2 acp `b1bc512f…`；M1 `bd2acb50…`（仅绑定本候选株，禁拼接） |
| 运行环境 | /usr/bin/python3.11 · build 1.6.1 · setuptools 65.5.1 · 3 venv 离线 `--no-index` |
| 样例退出码 | cccp 16/16 ×2 rc=0；acp 7/7 rc=0（E/task-54-raw/samples-*.txt） |
| 失效规则 | 候选后再有触碰 `src/cccp|src/acp|src/acp_services` 的 commit → 复验失效须重跑（当前无） |

---

## 7. QA：脚本交叉核对（路径存在性）+ 原始输出

脚本：`.omo/evidence/acp-parallel-plan-3-cccp/c10-final-index-crosscheck.py`（对本文件 §1 索引的全部证据路径做存在性断言；逐 todo 一行；任何缺失 → FAIL 并回链该 todo，**不补记"完成"**）。

原始输出（2026-10-19，`/usr/bin/python3.11`，exit=0；副本 `E/c10-final-index-crosscheck.out`）：

```
ROOT=/ACPServer/AutoCalcPlatform
rows=60 (todos 1-56 + F1-F4)
todo   1: OK (1 paths)
todo   2: OK (1 paths)
todo   3: OK (2 paths)
todo   4: OK (1 paths)
todo   5: OK (4 paths)
todo   6: OK (1 paths)
todo   7: OK (4 paths)
todo   8: OK (2 paths)
todo   9: OK (2 paths)
todo  10: OK (2 paths)
todo  11: OK (1 paths)
todo  12: OK (1 paths)
todo  13: OK (4 paths)
todo  14: OK (2 paths)
todo  15: OK (2 paths)
todo  16: OK (4 paths)
todo  17: OK (4 paths)
todo  18: OK (6 paths)
todo  19: OK (3 paths)
todo  20: OK (3 paths)
todo  21: OK (4 paths)
todo  22: OK (3 paths)
todo  23: OK (3 paths)
todo  24: OK (4 paths)
todo  25: OK (3 paths)
todo  26: OK (2 paths)
todo  27: OK (2 paths)
todo  28: OK (2 paths)
todo  29: OK (3 paths)
todo  30: OK (4 paths)
todo  31: OK (3 paths)
todo  32: OK (5 paths)
todo  33: OK (3 paths)
todo  34: OK (2 paths)
todo  35: OK (3 paths)
todo  36: OK (5 paths)
todo  37: OK (1 paths)
todo  38: OK (3 paths)
todo  39: OK (4 paths)
todo  40: OK (5 paths)
todo  41: OK (1 paths)
todo  42: OK (3 paths)
todo  43: OK (4 paths)
todo  44: OK (1 paths)
todo  45: OK (2 paths)
todo  46: OK (2 paths)
todo  47: OK (2 paths)
todo  48: OK (3 paths)
todo  49: OK (7 paths)
todo  50: OK (3 paths)
todo  51: OK (1 paths)
todo  52: OK (4 paths)
todo  53: OK (3 paths)
todo  54: OK (2 paths)
todo  55: OK (5 paths)
todo  56: OK (3 paths)
todo  F1: OK (2 paths)
todo  F2: OK (1 paths)
todo  F3: OK (1 paths)
todo  F4: OK (1 paths)
TOTAL_MISSING=0
CROSSCHECK_RESULT: PASS
```

附注（T51 复核运行，非本任务产出但为 §1 F4 行依据）：`python3.11 -m pytest tests/test_f4_scope_audit.py -q` = **1 failed, 28 passed**（`test_migration_scope_audit_reaches_migration_files`：22 个站点 "changed outside registered migration stations"——均为他道已提交文件；B请求 共享门，C 不放宽，见 §3 未决 4）；`pytest tests/test_packaging_assets.py -q` = **7 passed**（t55/c39a 集成后复核）。

---

## 8. 本汇总自身的 Must-NOT 自检

- [x] 未将任何 blocked-by-B 项标为完成（§3/§4.5 全部保持 open 或 delivered）。
- [x] 未替 B/A 记录其验收（B 集成事实仅引用 B 回执 + T54 机器判定；A 侧仅列 C 交付物）。
- [x] 未删除未决清单（§3 六项 + §5 NOT_VERIFIED 全列）。
- [x] 无捏造证据路径（§7 脚本断言全部存在）。
- [x] 范围状态与三态判定分列（§1 两列）；补丁已交付/已集成分列（§3 补丁表）。
- [x] T54 之后汇总（base = 355d96f = T54 候选）；本汇总不构成 B 发布额外等待项（§6）。
