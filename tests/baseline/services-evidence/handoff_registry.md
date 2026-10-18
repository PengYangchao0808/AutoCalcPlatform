# Handoff registry — 跨路交接登记表（B00-3 / todo 3，与 C-T5 共同维护）

**Created:** 2026-10-18，基线 HEAD `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9`。
**Owner of this file:** B 路集成写者（见 `single_writer_map.md` 第 4/7 行）；A/C 引用**同一版本**的本表，不各自复制。
**含义:** 每次跨路交接（文件/契约/符号从一路交给另一路）在此登记。每片交接须能回溯：谁提供、谁消费、基于哪个基线、前置、验收方式、补丁是否已交付、是否已在消费者分支集成。

## 列定义

| 列 | 含义 |
|---|---|
| owner | 本片交接的负责路（A/B/C） |
| 可修改范围 | 交接涉及的文件/符号范围（glob 或符号名） |
| provider | 提供方（哪一路产出该工件/接口） |
| consumer | 消费方（哪一路接收并集成/消费） |
| base_commit | 该交接的基线 revision（SHA） |
| prerequisites | 前置条件（依赖的任务号/工件/契约版本） |
| acceptance | 验收方式（消费者侧如何判定可用） |
| applied_commit | 补丁「已交付」的提交；若已集成，记录消费者分支的集成提交 |
| 状态 | handoff 状态枚举：pending / delivered / integrated / rejected |

## 状态语义（补丁「已交付」与「已集成」分开）

- `pending`：交接已登记，尚无交付提交；消费者不得据此开发真实功能。
- `delivered`：提供方已提交该工件/契约补丁**到共享分支**，但消费者尚未应用并通过测试。
- `integrated`：消费者分支已应用该补丁且通过验收测试（`applied_commit` 记集成提交）。
- `rejected`：该交接被驳回或撤销，须在 prerequisites/acceptance 记录理由。

**规则：** 只检查「样例是合法 JSON」不算 integrated；必须由消费者侧通过契约/行为测试。提供方单方提交只能记 `delivered`。

## 交付物与机制版本（与 X2 对齐）

本表登记**交接这一动作**；交付物的字段语义冻结在 `docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`（版本随该文件头部冻结）。每份 X2 交付物至少一行：Access 与配置边界、Data prepare/read、事件流、科学产品与帧 adapter、执行协议与科学闭包、发行与资产。

## 登记表

| owner | 可修改范围 | provider | consumer | base_commit | prerequisites | acceptance | applied_commit | 状态 |
|---|---|---|---|---|---|---|---|---|
| A | `frontend/**`、Node 与前端专属测试 | A | B/C 提供接口与 fixture | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | A00 前端基线地图（`docs/reference/current/ACP_Frontend_Baseline_Map_20261018.md`） | B 以黑盒浏览器场景执行；C 判定科学等价，A 源码写权不转移 | n/a | pending |
| B | `src/acp_services/**`、scheduler/api、平台 planning/recovery、部署/config/CLI/init | B | A 消费；C 提供科学入口 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | 首片契约冻结（本表 + `ACP_Services_First_Slice_Contracts.md`） | B 保持现有 `src/acp` 兼容路径并通过定向测试 | n/a | pending |
| C | `src/cccp/**`、登记移交的旧 acp 科学专属文件 | C | B/C 先固定逐文件或逐符号边界 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | C00 闭包地图（渠道 `docs/reference/next-change/ACP_Parallel_Plan_3_CCCP_20261018.md` C00/C01） | C 提取唯一实现与旧 shim 后由 B 应用接线 | n/a | pending |
| B | 混合的 workflows/catalog/executor/results 等旧文件 | B 为接入写者 | C 提交符号迁移补丁与调用替换 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | C 提交具体符号迁移补丁；登记移交后才转移文件写权 | 禁止双方整文件搬迁；逐符号边界由 `migration_ledger.md` 守护 | n/a | pending |
| C | 科学 schema、黄金 fixture、数值/单位/模式身份 | C | B/A 审阅可执行性与可显示性 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | cccp 任务契约（`cccp/calculation/requests.py`+`results.py`） | 平台 manifest/frames adapter 由 B 写并消费 C 的同一组 fixture | n/a | pending |
| B | 平台 API/事件/prepare/envelope schema | B | A/C 共同审阅 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | 先固定正常/空/部分/错误/旧版本样例（见 `contract_samples/`） | 消费者对提供方发布的同一组 fixture 做契约测试 | n/a | pending |
| B | `pyproject.toml`、`requirements-node.txt`、根 README/AGENTS、主方案、全局架构门、共享集成测试 | B 为集成写者 | A/C 提交精确修改需求 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | 科学/QC 门修订由 C 提供证据 | 各专属清单由所属路维护；B 只集成不越权改科学语义 | n/a | pending |
| B | 组件 manifest（`docs/reference/next-change/ACP_Services_Component_Manifest.md`） | B | A/C 引用 | `7074692` | 主方案 §27 模块归属 | manifest 状态枚举与 §27.1 对齐；候选不进 pyproject | `7074692` | delivered |
| B | 首片契约 + 逐符号台账 + 单写者表（本目录） | B | A/C 消费同一版本 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | B00-3 验收；X2 六交付物冻结 | checker `tests/test_services_first_slice_contracts.py` PASS | n/a | pending |
| B | 现有 `acp.backends` re-export shim → `cccp.backends` 移交（前序计划样板） | B（历史） | 全 acp 消费者 | `2a23b93` | 前序迁移计划收口 | `test_architecture_invariants.py` 依赖方向门 | `66222f7` | integrated |
| C | C00 闭包地图与 C01 首个阻塞 helper | C | B 应用接线 | n/a | 计划三 C00/C01（尚未开工） | C00/C01 产出后由 B 记录 applied_commit | n/a | pending |
| A | A00 前端基线接管地图 | A | B/C 引用 | `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9` | 无 | 地图被 `ACP_Frontend_Baseline_Map_20261018.md` 引用 | `f410ac9d`（地图基线） | delivered |

## 维护约定

- 本表**与 C-T5 共同维护**：C 路新增科学闭包/移交行后，B 在整合时更新 `applied_commit` 与状态。
- A 路引用本表时使用**同一版本**（同一提交）；禁止在 A/C 清单内复制一份状态不同的表。
- 行内不得出现 `|`；glob 中的 `/**` 允许。
