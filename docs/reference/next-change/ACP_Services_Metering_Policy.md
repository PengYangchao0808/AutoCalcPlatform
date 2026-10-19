# ACP Services — 计量政策（冻结契约 B10-1 / todo 53）

**状态：** 契约冻结（可审计口径与不变量已固定）；**具体政策取值仍待确认**（见「未决项」）。
**Owner：** ACP Services（Runtime 计量组件）。
**代码驻点：** `src/acp_services/runtime/metering_policy.py`。
**验收测试：** `tests/test_services_metering_policy.py`。
**依据：** 主方案 §20.3 / PR-05a；并行计划二 B10-1；`docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`。
**作者：** ACP Services（B 路，todo 53）。

> 本文是**政策文档 + 代码化配置**。冻结的是「口径如何被选择与计算」的规则；**不冻结**「选哪个口径、
> 每核时多少 Credit」这类需要产品/运营决策的取值。任何未确认取值保持 `unresolved`，代码**不内置默认值**。

---

## 0. 一句话结论

计量政策把「测什么、怎么测、边界在哪」全部显式化；未配置时**拒绝计量并报结构化错误**，
绝不替用户猜一个口径。核心不变量：**口径二选一且不混用、未决即待对账/拒绝、排队/传输/发布不计、
执行前冻结可执行上限、无法给出上限则拒绝 quota-only**。

---

## 1. 冻结规则（已固定，代码强制）

### 1.1 `metering_basis` 显式选择，默认 `unresolved`

两种口径**互斥**，必须显式选择其一：

| 口径 | 取值 | 公式 |
|---|---|---|
| 分配核时 | `allocated-core-hours` | `core_hours = Σ(eligible_interval_seconds × allocated_cores) / 3600` |
| 实际 CPU 秒 | `actual-cpu-seconds` | `core_hours = Σ(confirmed_cpu_seconds) / 3600` |

* 新建 `MeteringPolicy` 的 `basis` 默认 `unresolved`；**没有内置默认口径**。
* 未解析时调用 `measure()` 抛 `MeteringRefusedError(reason_code="metering_basis_unresolved")`；不产生任何用量记录。
* **禁止混用**：`measure()` 只按 policy 选定的一种口径推导全部数字；另一种度量
  （分配口径下的 `cpu_seconds`、实际口径下的 `allocated_cores`）只作证据元数据，**永不相加**。
  `MeteringResult.mixed_bases` 恒为 `False`（可执行守卫）。

### 1.2 舍入规则显式（`RoundingSpec`）

* `rule ∈ {half-up, half-even, floor, ceil}`；`decimal_places` 为显式非负整数。
* `rule=unresolved` 时 `decimal_places` 必须为 `None`；解析后必须给出 `decimal_places`。
* 未解析舍入 → `measure()` 抛 `MeteringRefusedError("rounding_unresolved")`。
* 定点化：`quantize_core_hours(raw, spec)`；`MeteringResult` 同时保留 `raw_core_hours`（未舍入）与
  `core_hours`（已舍入）供审计。

### 1.3 周期边界 + 时区显式（`BillingPeriod`）

* `unit ∈ {hour, day, month}` + IANA `timezone`（如 `UTC`、`Asia/Shanghai`），两者都必须配置。
* `period_bounds(moment, period)`：返回含 `moment` 的半开区间 `[start, end)`，边界在 policy 时区内计算。
* `segment_by_period(start, end, period)`：把跨周期区间**精确切分**，拼接即原区间（供 todo 55 跨周期分段）。
* naive datetime 一律拒绝（`MeteringValidationError`）；未知时区拒绝。
* 未解析周期 → `measure()` 抛 `MeteringRefusedError("period_unresolved")`。

### 1.4 暂停 / 失败 / 重试（`IntervalBilling`，默认全 `unresolved`）

| 旋钮 | 默认 | `unresolved` 行为 | 显式取值 |
|---|---|---|---|
| `pause` | `unresolved` | 暂停区间 → **待对账**（不计、不排除） | `included`（计费）/`excluded`（排除） |
| `failure` | `unresolved` | 失败区间 → **待对账** | `included`/`excluded` |
| `retry` | `unresolved` | 重试区间 → **待对账** | `included`/`excluded` |

> 「未决 = 待对账」不是「排除」：未确认政策**不得**悄悄缩小或放大账单。待对账区间
> 计入 `MeteringResult.pending`，`core_hours` 只由 `metered` 区间产生。

### 1.5 未知用量处理

* `kind=unknown` 或**缺证据来源/版本** → 待对账（`unknown_usage` / `evidence_missing`）。
* 已分类为 `metered` 但所选口径所需度量缺失：
  * 分配口径缺 `allocated_cores` → 待对账（`allocated_cores_missing`）；
  * 实际口径缺 `cpu_seconds` → 待对账（`actual_cpu_seconds_missing`）。
* **缺失值绝不当 0**（对齐 §20.4「对无法确认的远端用量保持待对账」）。

### 1.6 排队 / 传输 / 发布不计

`IntervalKind.NON_METERED = {queued, transfer, publication}` 由构造无条件**排除**，
即使 pause/failure/retry 全设 `included` 也不例外（对齐 §20.3「排队等待、数据传输、网页轮询和结果发布不计计算核时」）。

### 1.7 执行前冻结可执行上限（`ExecutableBound`）

`freeze_executable_bound(policy, cores, walltime_s, hard_limit_capability, queues?, nodes?)` 冻结：

* 分配核数 `cores`；
* 墙钟上限 `walltime_s`；
* 硬限制能力 `hard_limit_capability ∈ {enforced, unavailable}`；
* 政策版本 `policy_version`（自 policy 快照）；
* **PAUSED 计量** `pause_metering`（自 policy 快照）。

`bound.executable` 为真当且仅当：policy 版本非空 ∧ 核数有限 ∧ walltime 有限 ∧ 能力为 `enforced`。

### 1.8 无法给出上限 → 拒绝 quota-only（`admit_metering_mode`）

`admit_metering_mode(bound, mode)`：

| 条件 | 结果 |
|---|---|
| `mode=usage-only` | 准入（仅记录；`enforced=False`），管理员**显式**选择 |
| quota-only ∧ 无政策版本 | 拒绝 `no_policy_version` |
| quota-only ∧ 核数或 walltime 缺失 | 拒绝 `unbounded_request`（列出缺失维度） |
| quota-only ∧ 硬限制能力非 `enforced` | 拒绝 `hard_limit_unavailable` |
| 其余 quota-only | 准入 `admitted`（`enforced=True`） |

**唯一绕过途径 = 显式 `usage-only`**，不允许从失败 quota-only 静默降级。

### 1.9 版本化与快照固定

* `MeteringPolicy` 为 `frozen=True` dataclass；`policy_version` 必填（空 → 拒绝）。
* `snapshot()` 为规范字典；`snapshot_digest()` 为 `sha256(canonical json)`，用于审计/漂移检测。
* `MeteringResult.policy_version` 记录**测量当时**的版本；后续改政策不能重标已记录测量。
* `ExecutableBound` 同样快照 `policy_version` 与 `pause_metering`。

### 1.10 不属于科学身份

`METERING_NOT_SCIENTIFIC_IDENTITY_NOTE`（代码常量）逐字声明：

> The metering policy is a platform execution/usage fact and is **NOT part of scientific identity**:
> it must never enter TaskRequest, scientific options, CCCP content identity, or the ACV data model.

计量政策只决定「如何计量算力」，不决定「跑什么科学、结果如何比较」。

### 1.11 无隐藏价格 / 无隐藏默认

* 本模块**不含任何价格/费率/ Credit 词汇或数值常量**（由测试 AST 守卫）。
* `MeteringPolicy` 无 `price`/`rate`/`credit` 字段。
* 价格政策属 **PR-05b**，另行审批；本政策与价格/Credit **解耦**，可独立交付（MU-10a）。

---

## 2. 未决项（显式待确认，**不得**在代码中补默认值）

以下取值**尚未确定**，当前为 `unresolved`；在获得产品/运营明确裁决前，任何执行不得按隐藏默认计费：

| 未决项 | 现状 | 待确认内容 |
|---|---|---|
| 采用哪种核时口径 | `basis=unresolved` | 分配核时 vs 实际 CPU 秒 |
| 周期粒度 | `period.unit=unresolved` | hour / day / month |
| 计费周期时区 | `period.timezone=""` | IANA 时区 |
| 舍入规则与位数 | `rounding.rule=unresolved` | half-up/half-even/floor/ceil + decimal_places |
| 暂停是否计费 | `pause=unresolved` | included / excluded（未决=待对账） |
| 失败是否计费 | `failure=unresolved` | included / excluded |
| 重试是否计费 | `retry=unresolved` | included / excluded |
| 每核时价格 / Credit | 不在本模块 | PR-05b（价格政策审批前不开工） |

> 对齐 §20.3：「具体采用哪种核时、每核时多少 Credit、暂停期间是否属于可计区间、节点类别价格、
> 失败与重试政策均未在本轮指定，**不能在代码中自行补成隐藏价格**」。

---

## 3. 与准入 / 用量记录的关系

* **准入（B09-2 `runtime/admission.py`）** 决定"能不能派发"；本政策决定"派发后怎么计量"。
  quota-only 的可执行上限语义与本政策 `admit_metering_mode` 一致，共用 `AdmissionMode` 与
  `HARD_LIMIT_ENFORCED/UNAVAILABLE` 词汇（常量单点定义）。
* **用量记录（B10-2 todo 54）** 将产出 `UsageRecord`，其区间填充本模块的 `UsageInterval`；
  父子资源（LSF 作业核时 XOR 内部 ORCA 进程核时）只选一层，不得相加（对齐 §20.3）。
* **quota-only 生命周期（B10-3 todo 55）** 消费 `segment_by_period` 做跨周期分段。
* **解耦审计（B10-4 todo 56）**：用量写入不依赖发布/完成路径；`progress`/浏览器计时/监控采样不得入账。

---

## 4. 符号索引（`acp_services.runtime.metering_policy`）

词汇：`MeteringBasis`、`RoundingRule`、`PeriodUnit`、`IntervalKind`、`IntervalBilling`、
`ChargeDecision`、`MeteringReason`。

值对象：`RoundingSpec`、`BillingPeriod`、`MeteringPolicy`、`UsageInterval`、`IntervalVerdict`、
`MeteringResult`、`ExecutableBound`、`MeteringAdmission`。

函数：`period_bounds`、`segment_by_period`、`quantize_core_hours`、`classify_interval`、`measure`、
`freeze_executable_bound`、`admit_metering_mode`。

错误：`MeteringError`、`MeteringValidationError`、`MeteringRefusedError`。

---

## 5. 验收映射

| 验收要求 | 测试 |
|---|---|
| 口径二选一未配置时拒绝计量（显式错误） | `test_unresolved_basis_refuses_metering_with_explicit_error`、`test_unresolved_rounding_refuses_metering`、`test_unresolved_period_refuses_metering` |
| 两口径按文档公式 | `test_allocated_core_hours_formula`、`test_actual_cpu_seconds_formula` |
| 不混用口径 | `test_allocated_basis_ignores_measured_cpu_seconds`、`test_actual_basis_ignores_allocated_cores` |
| 舍入边界 | `test_rounding_at_half_boundary`、`test_rounding_decimal_places` |
| 周期/时区边界 | `test_month_period_bounds_utc`、`test_period_timezone_changes_the_boundary`、`test_segment_by_period_splits_cross_period_interval` |
| 暂停边界 | `test_pause_*`（unresolved/excluded/included） |
| 排队/传输/发布不计 | `test_non_metered_categories_are_always_excluded` |
| 未知用量待对账 | `test_unknown_kind_is_pending`、`test_missing_evidence_is_pending`、`test_actual_basis_without_cpu_seconds_is_pending_not_zero` |
| 政策版本快照固定 | `test_policy_dataclass_is_frozen`、`test_snapshot_digest_is_stable_and_version_sensitive`、`test_measure_result_keeps_the_policy_version_it_measured_under` |
| 可执行上限冻结 + 无上限拒绝 quota-only | `test_freeze_executable_bound_validation`、`test_quota_only_refused_when_*`、`test_usage_only_is_the_explicit_escape_hatch_even_unbounded` |
| 无隐藏价格/默认、不属于科学身份、隔离 | `test_policy_carries_no_price_or_rate_field`、`test_module_has_no_numeric_price_constants`、`test_module_is_isolated_from_acp_cccp_and_networks` |

---

## 6. 变更记录（append-only）

| 日期 | 动作 | 说明 | 提交 |
|---|---|---|---|
| 2026-10-19 | 初版冻结 | todo 53（B10-1）落地：口径/舍入/周期/暂停失败重试/未知用量/排除项/可执行上限/quota-only 拒绝 | 见 todo 53 提交 |
