# ACP Services Freshness / Availability Contract — B03-2 (todo 20)

**Status:** additive response contract; append-only to the first-slice contract §2.3.
**Created:** 2026-10-19.
**Owner:** B 路（`src/acp_services/**`）. Consumers: A（前端 prepare/refresh/display）、C（科学）.
**Module:** `src/acp_services/runtime/freshness.py`（stdlib-only；**不得** import `acp`）+
`src/acp_services/runtime/projections.py`.
**Checker:** `tests/test_services_freshness.py`.
**Authority:** `docs/reference/next-change/ACP_Services_First_Slice_Contracts.md` §2.2/§2.3.

This document freezes the three orthogonal semantics the task-row projection now
carries on the v2 task-view response. All existing fields are unchanged
(additive-only); this is the "先加后迁" step.

---

## 1. Response fields (v2 task-view row)

| 字段 | 类型 | 语义 | 不表示 |
|---|---|---|---|
| `availability` | enum `ready` / `preparing` / `unavailable` | 资源本体是否已准备可读 | 不表示投影新旧 |
| `freshness` | enum `fresh` / `stale` / `unknown` | 投影是否跟上权威状态 | 不表示资源不可读 |
| `basis_revision` | `map[str,int]` | 投影依据的**各 owner 维度** revision | 不表示时间新旧 |
| `observed_at` | str(ISO-8601) | 诊断时间 | **不作为跨进程/跨节点严格新旧判定** |
| `stale_dimensions` | `list[str]` | 落后的 owner 维度（诊断） | 不表示资源不可读 |

`availability=absent` 属 Data `prepare` 面词表（§2.2），任务行投影分类器不产生该值。

## 2. 组合语义（权威判定表）

| availability | freshness | 判定 | 消费者动作 |
|---|---|---|---|
| `ready` | `fresh` | 完整可用 | 正常显示 |
| `ready` | `stale` | 资源可读但投影旧 | 显示基础事实 + 标 stale，**不得隐藏** |
| `unavailable` | `unknown` | 无投影但 jobs 基础事实可见 | 显示 jobs 权威基础数据，不伪造 |
| `preparing` | `unknown` | 投影构建中 | 显示基础事实，可稍后重试 |

- **无投影（missing）**：`availability=unavailable`、`freshness=unknown`、`basis_revision={}`；
  行仍携带全部 jobs 权威基础字段（`status`/`molecule_name`/`attempt`/`revision`/…）。
- **旧 attempt 投影迟到**：`freshness=stale`（`basis_revision` 落后）；迟到投影在 store 层被
  丢弃（`incoming_is_late`），**不覆盖**更新的事实。

## 3. Owner revision 维度（独立比较，不互相替代）

| 维度 | 当前值来源（`task_views.owner_revisions_for_row`） | 投影 basis 来源 |
|---|---|---|
| `job` | `row["revision"]`（jobs CAS revision） | `record.revision` |
| `attempt` | `row["attempt"]` | `record.attempt` |
| `name` | `row["name_revision"]` | task index `name_revision` |
| `catalog` | `CATALOG_REVISION` | `CATALOG_REVISION` |
| `organization` | `row["organization_revision"]`（暂无独立列 → 0） | 同左 |

规则：
- 只在**两侧都出现**的维度上比较；某维度缺失即无声明，不算 mismatch（partial basis）。
- 任一已比较维度不符 → `stale`，并列入 `stale_dimensions`。
- **不同 owner revision 不相互替代**：job 相符不能掩盖 name 落后。
- **不改现有 revision 语义**：`row["revision"]` 仍是 jobs CAS revision，`name_revision` 仍是命名修订。

## 4. observed_at 规则

- `observed_at` 仅诊断，**永不**决定 `freshness`；时钟偏移但 basis revision 正确 → `fresh`。
- 不变量（诊断守卫，`validate_observation_monotonicity`）：投影不可早于其 basis revision 被观测；
  `observed_at < basis_updated_at` → `ProjectionObservationError`（伪造/时钟倒退拒绝）。

## 5. A 路消费 fixture（prepare / refresh / display 决策）

A 路用同一组 fixture 覆盖三态。字段形状（`basis_revision` 为 owner 维度映射）：

```jsonc
// ready + fresh
{"availability":"ready","freshness":"fresh",
 "basis_revision":{"job":5,"attempt":1,"name":2,"catalog":1,"organization":0},
 "observed_at":"2026-10-19T10:00:05+00:00","stale_dimensions":[]}

// ready + stale（资源可读但投影旧）
{"availability":"ready","freshness":"stale",
 "basis_revision":{"job":4,"attempt":1,"name":2,"catalog":1,"organization":0},
 "observed_at":"2026-10-19T10:00:05+00:00","stale_dimensions":["job"]}

// missing（无投影 → jobs 基础事实）
{"availability":"unavailable","freshness":"unknown",
 "basis_revision":{},"observed_at":"","stale_dimensions":[]}

// late old-attempt projection（丢弃，不覆盖）
{"availability":"ready","freshness":"stale",
 "basis_revision":{"job":5,"attempt":1,"name":2,"catalog":1,"organization":0},
 "observed_at":"2026-10-19T09:59:00+00:00","stale_dimensions":["attempt"]}
```

判定：`ready+fresh`/`ready+stale`/`missing` 三态可区分；`observed_at` 改变不影响判定；
仅 revision 决定 `freshness`。

## 6. 变更记录

| 日期 | 变更 | 提交 |
|---|---|---|
| 2026-10-19 | 首版（B03-2）：availability/freshness/basis_revision/observed_at + 独立 owner revision | 待提交 |
