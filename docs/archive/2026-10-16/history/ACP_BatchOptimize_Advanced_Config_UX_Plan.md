# BatchOptimize 高级优化配置改造方案

**版本：** 1.0  
**日期：** 2026-09-12  
**范围：** BatchOptimize 的 ORCA 优化、SCF、Hessian 与热化学配置；包括前端交互、参数合同、调度传递、ORCA 输入和结果追溯。

## 1. 结论

当前问题并不只是“高级入口不明显”，而是配置链路没有闭合：

1. 前端已经定义了“优化控制”和“SCF 收敛控制”分组，也会把 `opt_*`、`scf_*` 字段写入任务 payload；
2. `batch_optimize` 方法 schema 没有发布这些字段，因此两个空分组被渲染器直接跳过；
3. 多个 `opt_*` / `scf_*` 字段没有对应的 catalog 字段定义；
4. 调度器只会把方法、基组、温压和缩放因子转换为 CLI 参数，高级值被丢弃；
5. BatchOptimize CLI 没有声明这些高级参数，`BatchMethodOptions` 构造时也没有接收它们；
6. Batch 引擎的优化请求仍使用硬编码值：普通驻点 `MaxIter 200`，过渡态 `MaxIter 200 + Trust 0.3 + Calc_Hess + Recalc_Hess 5`，没有读取 `BatchMethodOptions` 中已有的高级字段；
7. 截图中的“电子态与自旋”被渲染为 `[object Object]`，是结构化 `module` 字段被通用文本输入框错误处理的独立前端缺陷。

因此不应只补几个输入框；必须一次性完成 catalog → 前端状态 → API/JobSpec → scheduler/CLI → Batch 引擎 → ORCA 输入 → 结果记录的端到端修复。

## 2. 交互目标

- 常用用户只需选择稳定性预设，不必理解所有 ORCA 关键字。
- 高级用户可以明确控制 SCF、几何优化步长和 Hessian 策略。
- 任何预设都必须显示其实际参数，不能成为不可解释的“黑盒按钮”。
- 参数名称采用计算化学语义，而不是直接堆砌 ORCA 缩写。
- 页面展示“最终生效值”，并标明其来源：默认、预设、用户覆盖或结构角色覆盖。
- 不暴露当前后端没有真正实现的选项。

## 3. 新的高级区信息架构

保留“高级”折叠入口，但展开后改为四个紧凑分组，取代目前面积过大的四张预设卡。

```text
高级设置                                      已自定义 3 项   恢复默认

稳定性方案  [标准] [困难几何] [困难 SCF] [严格收敛] [自定义]
            MaxIter 400 · Trust 0.10 · Hessian 每 5 步重算

┌ 优化器 ─────────────────────┬ SCF ──────────────────────┐
│ 最大优化循环       400       │ 最大 SCF 循环       500   │
│ 收敛标准           严格      │ 收敛标准            严格  │
│ 初始信赖半径       0.10      │ 收敛策略            SlowConv│
└─────────────────────────────┴────────────────────────────┘

┌ Hessian ────────────────────┬ 频率与热化学 ─────────────┐
│ 初始 Hessian   自动/模型/计算│ 温度 / 压力 / 缩放因子     │
│ 重算策略       自动/关闭/每N步│                              │
└─────────────────────────────┴────────────────────────────┘

结构角色覆盖  [继承通用设置]  INT ...  TS ...
有效参数预览  TightOpt · TightSCF · MaxIter 400 · Trust 0.10
```

### 3.1 预设改造

四个大卡片改为单行分段按钮或紧凑 chips。选择后在下一行展示该预设修改了哪些值；用户手动改任意值后自动进入“自定义”，但保留未被修改的预设值。

建议更名：

- `标准优化` → `标准`
- `困难几何` 保留
- `困难 SCF` 保留
- `高精度确认` → `严格收敛`

“VeryTight”改变的是数值收敛阈值，不等同于提高理论方法精度，因此“高精度”容易误导。

### 3.2 优化器分组

| 界面字段 | 参数合同 | ORCA 映射 | 建议控件 |
|---|---|---|---|
| 最大优化循环 | `opt_max_iter` | `%geom MaxIter` | 整数输入；支持“自动” |
| 优化收敛标准 | `opt_convergence` | `LooseOpt / Opt / TightOpt / VeryTightOpt` | 下拉选择 |
| 初始信赖半径（步长控制） | `opt_trust_radius` | `%geom Trust` | 数字输入；支持“自动” |
| 失败恢复策略 | `opt_rescue_policy` | ACP 重试策略 | `关闭 / 自适应`，放入专家项 |
| 最大恢复次数 | `opt_max_rescue` | ACP 重试次数 | 整数输入，仅自适应时显示 |

用户所说的“步长”在当前 ORCA 优化器中应准确呈现为“初始信赖半径（步长控制）”。底层必须生成 `Trust`，不得生成无效的 `TrustRadius`。首版不开放负值所代表的固定信赖半径模式，以避免过度复杂化。

### 3.3 SCF 分组

| 界面字段 | 参数合同 | ORCA 映射 | 建议控件 |
|---|---|---|---|
| 最大 SCF 循环 | `scf_max_iter` | `%scf MaxIter` | 整数输入 |
| SCF 收敛标准 | `scf_convergence` | `NormalSCF / TightSCF / VeryTightSCF` | 下拉选择 |
| SCF 收敛策略 | `scf_strategy` | 默认 / `SlowConv` / `SOSCF` | 下拉选择 |
| 轨道继承 | `scf_orbital_inherit` | 优化后 `.gbw` 传递 | 开关，专家项 |

阻尼、level shift 等救援参数已有部分后端能力，但首版不宜直接常驻展示。可在“SCF 救援”二级折叠区中提供，并只在选择专家模式时出现。

### 3.4 Hessian 分组

必须把“初始 Hessian”和“后续重算”拆开：

- `opt_initial_hessian`：`自动 / 模型 Hessian / 启动时计算精确 Hessian`。
- `opt_recalc_hess`：`自动 / 不重算 / 每 N 个优化循环重算`。

`Recalc_Hess N` 表示重算间隔，不是“总共计算 N 次”。界面不得使用“Hessian 计算次数”作为字段名；可附带估算说明，例如“最大 200 个循环时，理论上最多重算约 40 次”。

首版不要提供“读取 Hessian”选项。当前接口虽然接受 `read` 文本，但没有完整的 Hessian 文件选择、上传、暂存与路径校验链路，展示该选项会形成假功能。

### 3.5 结构角色覆盖

BatchOptimize 同时处理普通驻点和过渡态，建议从当前单纯的“角色级方法覆盖”升级为：

- `通用设置`：所有结构继承；
- `INT 覆盖`：仅显示与通用值不同的字段；
- `TS 覆盖`：初始 Hessian、重算间隔和 Trust 通常需要不同默认值。

交互上使用 `通用 / INT / TS` 三个页签，每个字段旁显示“继承”状态。首阶段至少保留方法/基组覆盖，并把当前引擎硬编码的 TS 差异展示成可见的“角色默认”，不能继续隐藏在代码中。

## 4. 推荐默认策略

默认值需要集中在一个服务端权威表中，前端、CLI 和引擎均读取同一来源，避免当前 `Trust 0.15`、`Trust 0.3` 等多套默认并存。

| 场景 | OPT | Trust | 初始 Hessian | 重算 Hessian | SCF |
|---|---|---:|---|---|---|
| INT 标准 | Tight | 自动 | 模型/自动 | 自动 | Tight，300，默认策略 |
| TS 标准 | Tight | 服务端统一默认 | 启动时计算 | 每 5 步 | Tight，300，默认策略 |
| 困难几何 | Tight | 0.10 | 计算 | 每 5 步 | Tight，300 |
| 困难 SCF | Tight | 自动 | 角色默认 | 角色默认 | Tight，500，SlowConv |
| 严格收敛 | VeryTight | 自动 | 角色默认 | 角色默认 | VeryTight，500 |

上表是产品层建议；合并实现前应由计算负责人确认 TS 的统一 Trust 默认值。关键要求是只保留一个权威值，并在任务详情中可追溯。

## 5. 参数合同与传递链

### 5.1 Catalog

在 `FIELD_DEFINITIONS` 中补齐以下字段定义，并将其加入 `METHOD_SCHEMAS["batch_optimize"].method_levels[0].fields`：

```text
opt_max_iter
opt_convergence
opt_trust_radius
opt_initial_hessian
opt_recalc_hess
opt_rescue_policy
opt_max_rescue
scf_max_iter
scf_convergence
scf_strategy
scf_orbital_inherit
```

`opt_recalc_hess` 应复用现有 `hessian_interval` 控件和校验语义，而不是在前端再造一套硬编码规则。

### 5.2 前端

- 对 `type: module, renderer: electronic_state` 做专用分发，禁止落入字符串输入框，从根本上修复 `[object Object]`。
- 高级字段全部由 catalog 驱动；分组只负责布局，不负责发明默认值。
- 表单顶部增加“生效参数摘要”；每个值显示来源标记。
- 提交前展示只读预览，不允许前端自行拼 ORCA 语法；预览由服务端规范化接口返回。
- 中英文标签、说明、单位统一使用相同字体层级，清除截图中的英文描述和全大写标签混用。

### 5.3 Scheduler 与 CLI

- `_BATCHOPTIMIZE_SCALAR_FLAGS` 增加全部已支持高级参数。
- `_add_batch_optimize_parser` 声明对应 CLI 参数、类型、choices 和边界。
- `_handle_batch_optimize` 将参数完整传给 `BatchMethodOptions`。
- 本地 runner 与远程 `script_gen` 必须共享同一 flag emitter，保证执行一致。
- `auto / off / N` 采用统一序列化规则，避免 `null`、空字符串和字符串数字在不同层含义不一。

### 5.4 Batch 引擎与 ORCA

重写 `_optimization_kwargs()` 的优先级：

```text
用户角色覆盖 > 用户通用值 > 稳定性预设 > INT/TS 角色默认 > ORCA 默认
```

必须实际读取 `BatchMethodOptions`：

- `opt_max_iter` → `max_cycles` / `geom_maxiter`
- `opt_convergence` → `opt_level`
- `opt_trust_radius` → `trust_radius`
- `opt_initial_hessian` → `initial_hessian`
- `opt_recalc_hess` → `recalc_hess`
- `scf_max_iter` → `scf_maxiter`
- `scf_convergence` → `scf_convergence`
- `scf_strategy` → `scf_strategy`

SCF 参数还应传给频率和单点步骤；是否继承同一严格度必须在有效参数预览中明确。频率与优化继续使用同一方法/基组，避免 Hessian 与驻点能量面不一致。

### 5.5 结果追溯

任务创建后把规范化的最终配置写入 `job.json` / `task.json` 和结果 manifest 的 provenance；至少记录：

- 用户选择的预设；
- 最终生效的 INT/TS 参数；
- 自动 Hessian 策略解析后的实际间隔；
- 生成的 ORCA `%geom` / `%scf` 关键设置摘要；
- 失败恢复是否触发及每次覆盖了哪些参数。

## 6. 实施顺序

### P0：先修“可配置且确实生效”

1. 补齐 catalog 字段和 Batch schema。
2. 补齐 scheduler flags、CLI 参数和 `BatchMethodOptions` 构造。
3. 删除 Batch 引擎中的硬编码覆盖，接通 ORCA 请求。
4. 修复 `electronic_state` 专用渲染，消除 `[object Object]`。
5. 增加端到端测试，证明每个字段进入最终 `.inp`。

### P1：改造高级区布局

1. 大卡片改为紧凑预设按钮。
2. 增加优化器、SCF、Hessian、频率/热化学四分组。
3. 增加生效参数摘要、来源标记和恢复默认。
4. 统一中文标签、字号、行高、控件高度和间距。

### P2：角色覆盖与可观测性

1. 增加 `通用 / INT / TS` 继承式覆盖。
2. 增加服务端规范化参数预览。
3. 在任务详情和结果 manifest 展示实际运行配置与救援历史。

## 7. 验收标准

1. BatchOptimize 展开“高级”后始终能看到优化器、SCF 和 Hessian 入口。
2. 修改任意高级值后，任务 payload、调度命令、`BatchMethodOptions`、计算请求和 ORCA `.inp` 五处一致。
3. `opt_max_iter=400` 最终生成 `%geom MaxIter 400`。
4. `opt_trust_radius=0.10` 最终生成 `%geom Trust 0.1`，且不出现 `TrustRadius`。
5. `opt_initial_hessian=calculate` 生成 `Calc_Hess true`。
6. `opt_recalc_hess=5` 生成 `Recalc_Hess 5`；关闭时不生成该行。
7. `scf_max_iter=500` 生成 `%scf MaxIter 500`；SlowConv/SOSCF 与收敛阈值只出现一次。
8. INT 与 TS 的角色默认和用户覆盖可分别验证，且不会互相污染。
9. 选择预设后可看到所有实际改动；手动修改后显示“自定义”。
10. 保存、刷新或重开配置时值不丢失，任务详情能回显最终生效值。
11. “电子态与自旋”不再出现 `[object Object]`，而是专用模块或简洁摘要。
12. 未支持的后端能力不显示，或明确禁用并解释原因，不允许提交后静默忽略。

## 8. 建议测试范围

- Catalog：字段存在、类型/默认值/范围正确、Batch schema 包含完整字段。
- 前端合同：分组存在、module 专用渲染、预设转自定义、单位和中文标签。
- Scheduler：本地与远程命令参数完全一致。
- CLI：合法值解析，非法区间、未知枚举和 Hessian 非法格式被拒绝。
- Batch engine：INT/TS 默认、通用覆盖、角色覆盖和救援覆盖优先级。
- ORCA interface：精确断言 `%geom`、`%scf` 和 route line。
- 回归：现有 opt-only、opt+freq、opt+freq+SP、完整热化学链均正常。
- E2E：从 UI 创建一个 INT 和一个 TS，检查两份真实输入文件及任务详情回显。

