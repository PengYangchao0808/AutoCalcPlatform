# C 侧交接登记（HN 索引）— handoff_registry.md

- **owner:** C 路科学负责人（渠道 `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md`）
- **created:** 2026-10-18
- **base_commit:** `dfad6b30e4aa01fa3e0249ac3eb00e313263f10d`
- **计划来源:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 5（C00-s5）
- **本文件定位:** **HN 序号索引 + C 侧明细**。跨路交接的**权威表**在 B 路，C **引用同一版本、不复制一份状态不同的表**。

## 0. 权威跨路交接表（canonical，B-owned，co-maintained）

| 项 | 值 |
|---|---|
| 路径 | `tests/baseline/services-evidence/handoff_registry.md` |
| 归属 | B 路集成写者（见 `tests/baseline/services-evidence/single_writer_map.md` 第 4/7 行） |
| C 引用提交 | `c50004304577ec88f36fb650014605d2b9b95c0f`（`git log -1 -- tests/baseline/services-evidence/`；两表首入库提交 `8a9f1b8`） |
| 状态词表（引用） | `pending` / `delivered` / `integrated` / `rejected`（语义见权威表「状态语义」节） |
| C 侧关系 | C 新增科学闭包/HN 交付行后，B 在整合时于权威表回填 `applied_commit` 与状态；禁止在 C 清单内复制一份状态不同的权威表 |

> 规则（照抄权威表）：`delivered`（提供方提交到共享分支）≠ `integrated`（消费者分支已应用且通过验收测试）。**「样例是合法 JSON」不算 integrated。** 提供方单方提交只能记 `delivered`。

## 1. HN 编号约定与字段

- **编号:** `HN-01` … `HN-06`（本计划预登记；新增交接继续递增，不复用已关闭号）。
- **字段（与权威表列一致）:** `owner / 可修改范围 / provider / consumer / base_commit / prerequisites / acceptance / applied_commit / status`。
- `applied_commit` 记：**交付提交（provider 侧）**；集成后另记 **consumer 侧集成提交**（由 B 回填）。
- `status` 初始 `pending`（未产出）；C 产出并提交后置 `delivered`；B 应用并通过验收后置 `integrated`。

## 2. HN 索引表

| HN | owner | 可修改范围 | provider | consumer | base_commit | prerequisites | acceptance | applied_commit | status |
|---|---|---|---|---|---|---|---|---|---|
| HN-01 | C | `docs`/tracked `handoffs/HN-01-b06-closure.md` + 逐工作流 B06 闭包补丁（混合文件补丁，见 `write_registry.md` §4.1） | C | B（B06 独立 worker / 接线） | 产出时冻结（`provider_base_commit`） | T3 分类 + T5 写权登记 + T48 隔离能力；T2 台账六栏 | 逐工作流四状态表 + 十要素字段完整；补丁 `git apply --check` 通过或隔离 worktree 应用后定向测试通过（记录后撤销）；「已集成」仅 B 应用并通过后记录；blocked-by-B 显式 | `ffcf1b821c89100ac0a5166a8cbc3cbe3b751a66`（provider 交付提交） | delivered |
| HN-02 | C | 科学产品 → frames/VIEW_REGISTRY 字段映射表 + 五态 fixture 集（normal/empty/partial/corrupt/old）+ 实装闭包表（T13） | C | B（B-T28a/B-T28b adapter）/ A（显示） | 产出时冻结 | T11 契约骨架 + T12 曲线/振动契约（完整交付）；ANTI #21；`src/acp/results/frames.py` 只读 | 映射表逐字段（源→目标→单位/身份规则）+ fixture 五态齐全（各附期望映射输出）+ cccp 侧零 `acp.results` import；状态 A「契约可消费」即本 HN 完成；状态 B「跨路端到端」单独记录 | `4d2c6f8d10370faf84138678e9e10eba26c5f13a`（provider 交付提交） | delivered |
| HN-03 | C | 有界大数据产品 + 块索引（schema/fixture/walk 交付）：`tests/baseline/cccp-evidence/handoffs/HN-03-bounded-products.md` + `tests/fixtures/science_products/bounded/`（normal/empty/partial/corrupt + 轨迹 normal/partial 共 6 组）+ `handoffs/walks/`（生成器/消费 walk/raw 执行输出）（T33） | C | B（Data 字节/元数据查询，B02/B03 面）/ A（有限产品消费） | 产出时冻结（shape=T32 `260d032`；`BOUNDED_SCHEMA_VERSION=1`/`PRODUCT_SCHEMA_VERSION=1`/`hn03_block_index@1`；provider 快照 `6a67635`） | T11/T12 产品契约；T31/T32 分块实现 + 性能实测（已满足） | 块索引 schema/示例/数据预算（Data 仅字节/元数据、无科学判据）齐；4 态 fixture 齐；逐 fixture 消费 walk（reconstruct/map/report_empty/reject + result_digest 比对）6/6 PASS 且 raw 入库；A 说明含单位/选择/缺帧 + 显示抽样≠全量；块↔索引 QA 重推 MATCH；「合法 JSON」不算完成（X2）；重生成 sha256 全等 | `e44778324200c13872f0dfd25ef9ae59d4b16d95`（provider 交付提交） | delivered |
| HN-04 | C | CCCP 最小科学包/依赖/资产/许可清单 + 精确打包改动请求（`pyproject.toml`/发行输入）+ wheel 构建与无 acp 安装证据（T39） | C | B（共享打包集成） | 产出时冻结 | T30/T33/T38 资产与清单；单 `pyproject.toml` 现状基线 | 精确变更请求可应用；无 acp/Services 环境安装 wheel 并运行科学 fixture/产品读取/模型报告样例；许可/可获得性声明 | pending | pending |
| HN-05 | C | 远端节点运行时依赖同步请求（`requirements-node.txt`）+ 新增运行时 import 声明（T42） | C | B（节点清单集成） | 产出时冻结 | HN-04 科学依赖集；`acp run` 新增 import 清单 | 新增运行时依赖逐条登记（HERE + `pyproject.toml` 同步请求）；节点环境 `pip install -r` PASS；无漏项 | pending | pending |
| HN-06 | C | 插件/parser 版本摘要字段规范 + 真实示例值（`handoffs/HN-06-version-digest.md` + `handoffs/samples/version_digest_samples.json`，T46） | C | B（release/step/product identity 冻结与发布，B06/B07） | 产出时冻结（shape=T45 `b9283e5`；`PLUGIN_DIGEST_SCHEMA_VERSION=1`/`PLUGIN_IDENTITY_SCOPE_VERSION=1`） | T44 样板 + T45 SDK digest/identity_fields（已满足） | 字段名/types/来源齐备 B 可直接照接；样例值真实（SDK 生成，记录命令+原始输出）；字段冻结时点早于 B 执行身份发布；PRODUCT 缺 capability → typed 错误 | `f68274c4f0b15d46e23398aaecdd2d4542e575d8`（provider 交付提交） | delivered |

## 3. 与 write_registry 的关系

- `write_registry.md` 回答「**谁可以写**」+ **补丁程序**（`git apply --check` → 隔离 worktree → 定向测试 → 记录 provider 基线/consumer 提交 → `git apply -R`）。
- 本文件回答「**C 交给谁、交付/集成到哪一步**」（HN 序号索引）。
- 两者与 B 的权威表 `tests/baseline/services-evidence/{single_writer_map.md,handoff_registry.md}` 配套；C 对混合文件的改动只以补丁形式产出，B 应用后由 B 在权威表回填状态。
