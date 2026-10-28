# Single-writer map — 共享文件单写者（B00-3 / todo 3，镜像并行索引 §5）

**Created:** 2026-10-18，基线 HEAD `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9`。
**Authority:** 本表是 `docs/reference/next-change/ACP_Architecture_Progress_and_Parallel_Index_20261018.md` §5「单写者与交接规则」的**逐行镜像**。
**Rule:** 本表行与 §5 行**必须 diff 一致**（同一行、同一写者）；由 `tests/test_services_first_slice_contracts.py` 比对守护。修改 §5 必须同步本表；修改本表必须同步 §5（且 §5 属 B 集成写者范围）。

## 单写者表

| 文件/契约 | 唯一写者 | 其他两路的参与方式 |
|---|---|---|
| `frontend/**`、Node 和前端专属测试 | A | B/C 提供接口与 fixture；若仍按 Antigravity/OpenCode 分工，由入口单写者接入，沿用主方案 §40 |
| `src/acp_services/**`、scheduler/api、平台 planning/recovery、部署/config/CLI/init | B | A 消费；C 提供科学入口。B 保持现有 `src/acp` 兼容路径 |
| `src/cccp/**`；登记移交的旧 acp 科学专属文件 | C | B/C 先固定逐文件或逐符号边界；C 提取唯一实现和旧 shim |
| 混合的 workflows/catalog/executor/results 等旧文件 | B 为接入写者 | C 提交具体符号迁移补丁及调用替换；登记移交后才转移文件写权，禁止双方整文件搬迁 |
| 科学 schema、黄金 fixture、数值/单位/模式身份 | C | B/A 审阅可执行性与可显示性；平台 manifest/frames adapter 由 B 写 |
| 平台 API/事件/prepare/envelope schema | B | A/C 共同审阅，先固定正常/空/部分/错误/旧版本样例 |
| `pyproject.toml`、`requirements-node.txt`、根 README/AGENTS、主方案、全局架构门、共享集成测试 | B 为集成写者 | A/C 提交精确修改需求；科学/QC 门修订由 C 提供证据。各专属清单由所属路维护 |

## 交接要求（随每片交接）

每片交接含：基线 revision、调用方/旧 writer、真实签名/schema、版本/身份、错误/取消/超时、依赖装载、fixture、切换/撤旧清单、证据和回退。
接口冻结后可依 fixture 开发；真实接口缺失时只记模块验证，功能关闭必须真实联调。共享契约逐片冻结，不以「全部设计完」为开工前提。

## 与交接登记表的关系

- 本表回答「**谁可以写**」；`handoff_registry.md` 回答「**谁交给谁、交付/集成到哪一步**」。
- 两表同属 B 路集成写者范围，随同一提交入仓。
