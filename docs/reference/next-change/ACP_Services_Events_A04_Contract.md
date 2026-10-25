# ACP Services Event Stream — A04 Consumption Contract

日期：2026-10-18 · 基线 HEAD：`650c782` · 所有者：B lane（todo 24 / B04-3）
实现驻点：`src/acp_services/runtime/events.py`（授权复用流 + 覆盖矩阵）；HTTP 表面 `src/acp/api/routes.py::stream_scope_events`（`GET /api/events/stream`）；守护测试 `tests/test_services_events_authz.py`。

本文件是给 **A04 刷新策略**（并行计划一消费方）的冻结消费契约：它声明事件覆盖范围、样例序列、快照重置语义，并记录与 A 侧现行取样的对齐/差异。**是否触发轮询降级由 A 侧决定**；本契约不命令消费方停轮询。

## 1. 单个受权流：`(principal, scope)` 复用

- 一个流恰好对应 `(principal_id, scope)`，scope ∈ `task | project | data`（冻结 wire 枚举）。
- 同 principal + 同 scope 的多次订阅**复用同一流对象**；追加任务只是 `subscribe`，绝不新建“每任务一条流”。`AuthorizedStreamRegistry.open()` 对相同 key 返回同一实例。
- 一个连接可携带同 scope 内任意多个 owner 的事件；SSE `id` 为复合形式 `<owner_id>:<seq>`（owner 事件 seq 仍为各自的持久 cursor）。
- 跨 scope 复用（cursor 的 principal/scope 与流不符）**拒绝**：服务端抛 `StreamScopeMismatchError`，不静默重解释。
- 新连接在订阅授权失败时被拒绝（`StreamAuthorizationError`），且不留下已注册流。

端点：

```
GET /api/events/stream?scope=task&job_ids=scope-a,scope-b
    [&after=scope-a:3,scope-b:0]   # 复合 cursor（可选，默认从头取保留窗口）
    [&epoch=<上次服务世代>]         # 可选；用于服务重启检测
```

## 2. 覆盖矩阵（4 类，非 wire 值）

分类由 `event_type` 派生（`classify_event`），不改变冻结 envelope 的 12 字段。未知类型保守归为 `task_summary`，绝不自动视为实时进度。

| 类别 | wire scope | 代表 event_type | 通道 | 传输 | 重置覆盖 | 轮询降级 |
|---|---|---|---|---|---|---|
| `log` | task | `log`, `log_line`, `stdout`, `stderr` | `logs_api`, `event_stream` | poll_tail | cursor_expired / permission_changed / service_restart | **A 侧决定** |
| `task_progress` | task | `progress`, `stage.started`, `stage.completed`, `process.starting` | `event_stream`, `job_detail_poll` | sse | 全部 4 类重置 | **A 侧决定** |
| `task_summary` | task | `job.created`, `job.completed`, `job.failed`, `job.cancelled` | `event_stream`, `job_detail_poll`, `queue_poll` | sse | 全部 4 类重置 | **A 侧决定** |
| `data_availability` | data | `availability`, `artifact_available`, `cache_index`, `pending_fetch` | `availability_api`, `data_event_stream` | poll_diff | cursor_expired / permission_changed / service_restart | **A 侧决定** |

样例事件类型序列（矩阵自带 `sample_sequence`，测试守护）：

- `log` → `log_line, log_line, log_tail`
- `task_progress` → `job.started, stage.started, progress, stage.completed`
- `task_summary` → `job.created, job.completed`
- `data_availability` → `availability, artifact_available, cache_index`

## 3. 样例序列（真实端点输出，截断 data）

### 3.1 复用：一个连接、两个任务（终态）

```
retry: 3000

id: scope-a:1
event: job.started
data: {"event_id":"job:scope-a:1","seq":1,"owner_id":"scope-a","event_type":"job.started","cursor":"1","coverage":"task",...}

id: scope-a:2
event: progress
data: {...,"owner_id":"scope-a","event_type":"progress","payload":{"fraction":0.5},...}

id: scope-a:3
event: job.completed
data: {...,"owner_id":"scope-a","event_type":"job.completed",...}

id: scope-b:1
event: job.started
data: {...,"owner_id":"scope-b",...}
id: scope-b:2 ... id: scope-b:3 event: job.completed ...

event: done
data: {"job_id":"scope-a","status":"completed","progress":1.0,...}
event: done
data: {"job_id":"scope-b","status":"completed","progress":1.0,...}
```

要点：两个任务的事件在同一条连接上；`id` 前缀区分 owner；全部终态后每 owner 一帧 `done` 并结束。

### 3.2 快照重置（过期 cursor / 权限变化 / 服务重启）

重置帧统一形状，其后跟随 `snapshot()` 全量事件（草稿示例）：

```
event: reset
data: {"reason":"cursor_expired","scope":"task","principal_id":"local-single","earliest_seq":{"scope-a":98},"latest_seq":{"scope-a":120}}

id: scope-a:98
event: progress
data: {...}
...
```

```
event: reset
data: {"reason":"permission_changed","scope":"task","principal_id":"local-single"}
# 服务端随后关闭连接；不再发事件
```

```
event: reset
data: {"reason":"service_restart","scope":"task","principal_id":"local-single"}
# 消费方应以新 epoch 重连；旧 epoch cursor 不可复活
```

## 4. 重置语义

| reason | 触发 | 消费方动作 |
|---|---|---|
| `cursor_expired` | after_seq 落在保留窗口之前（`PersistentEventService.replay` 判定） | 丢弃 cursor；`snapshot()` 全量重读后按新 cursor 续 |
| `permission_changed` | 订阅授权检查拒绝，或显式 `reset()` | 丢弃 cursor；停止使用该连接；授权恢复后以新连接重订阅 |
| `subscription_changed` | 流内 owner 集合变化（`unsubscribe`） | 丢弃 cursor；全量重读 |
| `service_restart` | cursor/流的 service epoch 不一致 | 丢弃 cursor；以新 epoch 重连 |

不变量：重置永远显式（`reset=true` + `reason`），绝不表现为“空且成功”；授权拒绝在**下一次**授权检查即时生效（活动连接被关闭；新连接被拒绝）。

## 5. 与 A 侧现行取样的对齐 / 差异（diff 记录）

A 侧现行证据（只读）：`docs/reference/current/ACP_Frontend_Closure_20261017.md` §R2 — `client/event-stream.js`（连接世代、`?after_seq` 替代 `?history=200`、A→B→A 旧重连不可复活）、`client/polling-strategy.js`（四通道 4/15/60s、2.5/30s、5s、30s + 世代守卫）。

对齐：

- “世代守卫 / 旧重连不可复活”与本契约的 `service_restart` epoch 语义同向；本契约把 epoch 提升为显式重置原因。
- A 侧 `after_seq` 单任务 cursor 与每 owner 的 `cursor` 语义一致；本契约仅把多个 owner 组合为 `owner:seq` 复合 id。

差异（需 A04 决策，本契约不替代）：

| # | A 侧现状 | Services 事件流 | A04 需决策 |
|---|---|---|---|
| D1 | 每任务一条 SSE（`EventSource` per job） | 同 principal+scope 单流复用多任务 | 是否将同屏多任务收敛到一条 scope 流 |
| D2 | 轮询四通道固定间隔 | 声明 4 类覆盖，不给间隔 | 是否用事件覆盖降级某类轮询 |
| D3 | `after_seq` 纯整数 | 复合 `owner:seq` + `epoch` | cursor 迁移/持久化格式 |
| D4 | 无服务重启显式信号 | `service_restart` 重置帧 | 是否据此整页刷新 |
| D5 | 日志走 logs 轮询 | `log` 类别仅声明覆盖 | 是否改为事件驱动日志可用性 |

A 侧产物缺席声明：**NOT_VERIFIED** — A04（并行计划一 Gated conditional tasks，GT-2）尚未实现消费方；上表差异未在 A 侧代码验证，通道：`docs/reference/current/ACP_Frontend_Closure_20261017.md` + Plan_1 A04 入场门。旧轮询路径（`/api/v1/jobs/{id}/events`、`/api/jobs/{id}/events`、`?after_seq`、`?history=200`）**保持不变**，A04 不迁移亦可继续运行。

## 6. 旧轮询兼容（硬约束）

- `GET /api/jobs/{job_id}/events` 与 `GET /api/v1/jobs/{job_id}/events` 行为不变（`after_seq` / `Last-Event-ID` / `history` 有界重放 / 终态 `done` / 心跳）。
- 新的 scope 流是**加性**端点；旧消费方零改动。
- 回归守护：`tests/test_api_sse_backlog.py`、`tests/test_services_events_authz.py::test_old_polling_endpoints_still_work`。
