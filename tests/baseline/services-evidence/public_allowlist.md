# acp_services public allowlist — 公开符号与依赖方向（B00-3 / todo 3）

**Created:** 2026-10-18，基线 HEAD `26ee295f911ae2c0dff0d3d7b01dd4779e5c87f9`。
**Owner:** B 路（`src/acp_services/**` 单写者，见 `single_writer_map.md` 第 2 行）。
**Scope:** `src/acp_services/`（尚未创建；本表是**冻结目标面**，不是实现）。首片只允许下列公开面。

## 依赖方向规则（hard rules）

1. `acp → acp_services` **允许**：`src/acp/**` 可导入 `acp_services` 的公开符号。
2. `acp_services → acp` **禁止**：`src/acp_services/**` 不得导入 `src/acp/**`，含
   - 顶层 `import acp...`；
   - 函数内**懒加载** `import acp...`；
   - `if TYPE_CHECKING:` 下的 `import acp...`（类型面同样禁止）；
   - 字符串反射（`importlib.import_module("acp...")`、动态 `__import__`、`getattr` 字符串寻址）。
3. `acp_services` 只依赖：stdlib、批准第三方、以及兼容的 `cccp` 科学面（`cccp.calculation` 契约）；不得依赖 `acp` CLI/API/scheduler/workflows。
4. 方向守卫由本表驱动：删除任一允许符号须在下方「变更记录」写明理由（append-only 规则见末节）。

> 与既有四层架构门（`cccp_imports_acp`）同级：本规则是 `acp_services → acp` 的新增方向门，落地在 todo 72/todo 77 的依赖闭包审计。

## 首片允许公开符号（first-slice allowlist）

首片按 X2 交付物冻结；字段命名由 owner（B）在 `docs/reference/next-change/ACP_Services_First_Slice_Contracts.md` 定义。此处的模块/符号名是**目标公开面**。

| 模块前缀 | 允许公开符号 | 用途（首片） | 首个消费者 | 状态 |
|---|---|---|---|---|
| `acp_services.contracts` | `Principal`, `AccessScope`, `ResourceGrant`, `AccessDecision` | Access 与配置边界（X2 交付物 1） | `acp` 授权面（todo 6 起） | 冻结 |
| `acp_services.contracts` | `DeploymentTier`, `ExecutionMode` | 部署档位与执行模式区分 | `acp` 配置/入口 | 冻结 |
| `acp_services.workspace.projects` | `ProjectOwnerPort`, `ProjectRecord` | 项目/成员/分组 owner | `acp.scheduler.projects` 兼容转发 | 冻结 |
| `acp_services.data.contracts` | `DataResourcePort`, `PrepareOperation`, `ResourceRevision`, `Availability`, `Freshness`, `BasisRevision` | Data prepare/read 与 availability/freshness/basis_revision 语义（X2 交付物 2） | `acp.api` 读取面 | 冻结 |
| `acp_services.events` | `EventEnvelope`, `EventCursor`, `EventScope` | 事件流 owner/scope/cursor/去重/过期重置（X2 交付物 3） | `acp.api` SSE/轮询 | 冻结 |
| `acp_services.runtime` | `RuntimeControlPort`, `SubmitIntent`, `ControlOutcome` | Runtime 用例（submit/pause/unpause/cancel/continue/rerun） | `acp.scheduler.manager` 兼容转发 | 冻结 |
| `acp_services.contracts.envelope` | `ExecutionEnvelope`, `ExecutionReceipt`, `StorageBinding` | 执行协议与科学闭包（X2 交付物 5） | 独立 worker（todo 30/31/78） | 冻结 |
| `acp_services.release` | `ReleaseManifest`, `AssetManifest`, `LegacyImportWindow` | 发行与资产（X2 交付物 6） | `acp.scheduler.remote.release` 兼容转发 | 冻结 |
| `acp_services.science.adapter` | `ScienceProductPort`, `FrameNode`, `FrameAnnotation` | 科学产品与帧 adapter（X2 交付物 4）；消费 `cccp.calculation.requests/results` 与 `acp.results.frames` 形状 | `acp.results` 发布/读取 | 冻结 |
| `acp_services.data.namespaces` | `NamespaceStore`, `EnsureNamespaceRequest`, `NamespaceRecord`, `ensure_namespace` | Data 命名空间原子绑定（开通）；重复/并发收敛单空间、冲突非静默 | `acp_services.runtime.provisioning` | 冻结 |
| `acp_services.runtime.provisioning` | `ProvisioningService`, `ProvisioningIntent`, `ProvisioningResult`, `EnvironmentStatePort` | Runtime 开通用例：消费 Workspace 持久意图 → Data 命名空间 → 提交环境状态并审计（失败可对账） | Workspace 环境状态命令面（todo 52 接线） | 冻结 |

## 明确不在首片允许面

- 具体存储后端（SQLite/PostgreSQL schema、迁移脚本）：属 todo 62/77，实现驻 `acp_services` 内部，不公开。
- 配额/计量/账本/价格符号：属 todo 53 至 todo 61，M3 前不冻结公开面。
- CLI 解析器、MCP 工具、OIDC/账户符号：属 todo 8/9/35/43/73。
- 任何 `acp.*` 类型作为 `acp_services` 公开签名的参数/返回类型（须使用 `acp_services.contracts` 自有类型）。

## 变更记录（append-only）

| 日期 | 符号 | 动作（add/remove） | 理由 | 提交 |
|---|---|---|---|---|
| 2026-10-18 | 上表全部（首片） | add | B00-3 首片冻结 | n/a |
| 2026-10-19 | `acp_services.data.namespaces`、`acp_services.runtime.provisioning` | add | todo 48（B09-1）开通幂等：Data `ensure_namespace` + Runtime 消费 Workspace 意图 | n/a |

## 追加规则（append-only semantics）

- 本 allowlist **只增不删**：新增允许符号直接追加行。
- **删除任一允许符号必须说明理由**并登记在「变更记录」：原因、替代符号、受影响消费者与迁移路径。
- 删除后须由消费者测试确认无 `acp_services` 公开面回退；未说明理由的删除视为违反本表。
