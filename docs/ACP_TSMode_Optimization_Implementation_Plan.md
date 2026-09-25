# ACP TS Mode 定向过渡态优化：完整修改方案

- 文档日期：2026-09-21（2026-09-22 实施状态同步）
- 状态：**代码骨架已实施（P1/P2/P3 + 前端与 API 首版）**；P0（真实 ORCA 模式映射验证）**未完成** — 详见文末"实施状态"一节。
- 功能名称：TS Mode 优化（基于已有虚频结果的定向 TS 再优化）。
- 工作流标识：`tsmode`，归属"简单计算"。
- 首期后端：ORCA；具体受支持版本由真实引擎验证确定，不预先宣称跨版本通用。
- 本文中的新增目录、API、JSON 字段与 CLI 参数均为拟议契约。实施时沿用项目既有命名规范，并同步修订本文。

## 1. 已确认需求与范围

用户通过已有频率计算的三维振动动画选择目标虚频，创建一个独立的定向过渡态优化任务。该任务读取与频率结果匹配的 Hessian，执行定向 OptTS，最后重新计算最终结构的频率并报告验证结果。

已确认的产品决策：

1. 在“简单计算”中设置独立入口，不仅作为普通优化中的高级开关。
2. 先有可用的频率结果和匹配 Hessian，再允许选择目标并提交。
3. 复用已有振动查看器、TS 优化 primitive、频率 primitive、任务调度及结果查看机制。
4. 单个任务只选一个虚频；探索多个目标时分别创建任务。
5. 请求目标必须在重试、重算、远程执行及结果中可追溯。
6. 最终频率验证是首期固定步骤；IRC 作为结果页显式发起的后续独立任务。

科学语义边界：ORCA 的 `TS_Mode` 本身并不要求预先完成频率计算，也支持计算初始 Hessian 或按内部坐标选模。本功能主动采用更严格的前置条件，以实现“看到某个虚频动画，再沿该模式优化”的明确操作语义。它不能保证从任意结构发现目标 TS，也不能代替 PES/NEB 等初猜生成过程。

首期不包括：任意结构编辑后沿用原模式、跨计算级别 Hessian、Gaussian Hessian 转换、多模式叠加、自动切换目标、自动启动 IRC、完整全局反应路径搜索。

## 2. 当前代码基础与已知缺口

以下为本次代码核对所得，实施时应以实际分支再次核验，不能依赖历史行号。

| 模块 | 可复用能力 | 缺口/风险 |
|---|---|---|
| `frontend/js/vibration_viewer.js` | 原生模式编号、当前选择、位移箭头、动画 | 增加明确的目标确认和工作流跳转 |
| `src/acp/results/frequencies.py` | `normal_modes_v1` 的频率与位移向量 | 缺少可执行来源包及 Hessian 绑定 |
| `src/acp/calculations/plans.py` | 按结构角色分派 TS 优化 | 新增来源准备、目标解析与最终验证编排 |
| `src/acp/calculations/primitives/optimize.py` | TS capability 分派、救援策略 | 救援 `ts_mode=True` 会选择 M 0，必须防止覆盖显式目标 |
| `src/cccp/qc/interfaces/orca_ts.py` | `TS_Mode {M n} end` 输入生成 | 参数为 bool/int；注释将 n 视为频率编号，需纠正并验证 |
| `src/cccp/qc/interfaces/orca.py` | TS 执行及相关结果解析 | TS Hessian 读取/文件落地、模式核对需要完整打通 |
| scheduler、job editor、remote | 创建、编辑重算、执行与文件传输 | 新工作流注册、输入资产、指纹与续算能力 |

当前工作区存在 PES DFT 扩展等未提交修改。本文只新增设计文件；实施时与这些改动协调，尤其复用计算级别规范化模型，避免建立重复模型。

## 3. 核心架构

```text
简单计算入口 / 频率查看器快捷入口
                  ↓
        频率来源包选择与校验
                  ↓
        虚频预览与显式目标确认
                  ↓
        不可变输入快照与目标解析
                  ↓
       TSMode 工作流编排器
         ├─ ORCA 模式映射与核对
         ├─ 现有 TS 优化 primitive
         ├─ 现有频率 primitive
         └─ TS 验证报告
                  ↓
     结果查看 / 修改目标后重算 / 创建 IRC
```

采用组合调用，不从一个工作流递归启动另一个完整工作流，不复制 subprocess 层。`acp` 层负责契约、编排和结果；`cccp.qc.interfaces` 负责 ORCA 输入、执行与原生解析；backend 保持薄适配器。

## 4. 输入：FrequencySourceBundle

### 4.1 最小完整性

| 数据 | 必需性 | 用途 |
|---|---|---|
| 对应频率计算的几何 | 必需 | 初始优化结构与模式展示 |
| 元素、原子顺序、原子质量约定 | 必需 | Hessian/位移一致性检查 |
| 频率及完整位移向量 | 必需 | 用户选择及目标身份 |
| 完整且匹配的 Hessian | 必需 | 初始曲率与模式解析 |
| 电荷、多重度 | 必需 | 体系定义 |
| 方法、基组、色散、溶剂等计算级别 | 必需，可补录 | 首期与来源一致 |
| ORCA 版本和文件来源 | 尽可能解析；缺失时明确提示 | 兼容矩阵与审计 |
| GBW | 可选 | 轨道初猜，不代替 Hessian |

优先从 `.hess` 获取其绑定几何并与输出校验。不能默认使用同目录最新的 XYZ 或输出文件最后一个不相关结构。

### 4.2 来源方式

- ACP 已有任务：按结构条目和具体频率产物选择，不能仅指定 job_id 后猜测文件。
- 本地导入：首期推荐 `.out + .hess`；可补充匹配 XYZ。
- 单独 `.hess`：仅在解析器能提取完整几何、Hessian、频率和模式时支持；缺失计算级别、电荷、多重度必须补齐。
- 只有 `.out`：允许查看模式，不允许提交；提供显式补充 Hessian 或创建频率任务的操作，不自动追加计算。
- 远程结果：按需获取所选来源文件。状态为待获取、获取中、可用或失败，未完成前不可提交。

即使来源父任务失败，只要选定的频率产物完整有效也可使用。反之，父任务 COMPLETED 不等于该产物合格。

### 4.3 校验

必须验证原子数、元素及顺序、Hessian 维度 3N×3N、数值有限性、几何单位、模式向量维度，以及来源频率段与文件匹配关系。几何比较需考虑允许的刚体变换，但首期不自动重排原子；存在无法消除的原子对应歧义时阻止提交。

记录文件 SHA-256、几何哈希、所用频率段标识和解析版本。缺少关键证据、输出截断、模式缺失、Hessian 不对应时返回可定位的错误。

Hessian 与模式可能体积很大，应设置上传大小和原子数资源边界；解析避免无界分配。内部文件引用使用服务端资产 ID，不接收任意服务器路径。归档导入如后续支持，必须防路径逃逸。

## 5. UI 完整流程

### 5.1 双入口

1. 简单计算 → TS Mode 优化 → 选择已有频率结果或导入文件。
2. 结果 → 振动查看器 → “以当前虚频创建 TS Mode 任务”。

两个入口复用同一个向导。快捷入口携带来源 ID、修订号和原生 mode_index，服务端仍重新校验。

### 5.2 页面 A：来源

展示任务名、结构条目、频率产物、计算级别、原子数、虚频数量和文件检查状态。多结构批量任务必须明确选择 item_id/entry_id。提供更换来源和远程文件获取。

### 5.3 页面 B：模式选择

```text
来源：任务 A / 结构 B       结构匹配 ✓  Hessian 可用 ✓

模式列表                         三维振动预览
○ #6  -520.4 cm⁻¹                播放 / 暂停 / 箭头
● #7  -180.2 cm⁻¹                振幅 / 速度 / 原子编号
○ #8   -32.1 cm⁻¹

正在预览：#7  -180.2 cm⁻¹
[设为优化目标]
已确认目标：#7  -180.2 cm⁻¹
```

规则：

- 默认显示全部负频率，并区分显著虚频与接近零的弱虚频；阈值用于解释，不删除原始数据。
- 允许切换查看全部模式，但首期提交目标必须是有完整位移的虚频。
- 列表点击只预览；明确按钮确认目标。没有确认不得自动使用默认高亮模式提交。
- 改变来源使确认失效；预览其他模式时显示“当前预览不同于已确认目标”。
- 原生编号保留，禁止因列表排序/筛选/分页而变化。
- 动画振幅、速度、正反播放均仅影响可视化；不保存为几何位移。
- 零虚频时解释本入口不可用，提供普通 TS 优化或频率计算入口。
- 向量损坏时模式不可选；不能用零向量填充。
- 支持键盘选择、按钮可访问标签、焦点管理和加载错误重试。
- 快速切换来源使用请求令牌/取消机制，避免旧响应覆盖新选择。

用户选择依据是预期成断键/迁移运动，不是自动选择最负频率。模式整体反号表示同一方向子空间，不是新的优化目标。

### 5.4 页面 C：优化设置

继承来源计算级别、电荷、多重度及初始几何，首期不允许在此修改体系。复用几何最大迭代、几何收敛、SCF、资源和失败重试设置。

初始 Hessian 固定“读取来源 Hessian”，展示文件来源。启动时不自动重新计算 Hessian。重算周期可以配置，但必须服从模式保持及恢复策略。

信赖半径区分初始自适应半径与固定半径，明确单位和 ORCA 正负号编码；前端使用结构化选项，不能要求用户猜负号含义。现有 TS 默认值需经实际引擎验证后复用。

最终频率为固定步骤。页面解释预计包含优化和最终频率两个主要计算阶段，Hessian 重算另有成本；不显示未经测量的固定耗时承诺。

### 5.5 页面 D：预览并创建

展示来源、选定模式、频率、计算级别、资源、恢复策略、最终验证及模式映射状态。未映射状态只能创建显式的验证准备作业（若实现该机制），不能声称可直接定向优化。默认首期要求提交前已有受支持且可验证的映射路径。

## 6. 模式映射：上线硬门槛

### 6.1 三种 ID 必须区分

| 标识 | 定义 |
|---|---|
| source_mode_index | 频率输出的原生模式编号，用于 UI 定位 |
| target_mode_id | 几何、Hessian 与模式向量绑定后的稳定目标 ID |
| optimizer_mode_index | 实际传给 ORCA TS_Mode M 的模式选择值 |

不得直接使用 `source_mode_index`，不得以“减 6”“减 5”或“虚频排序序号”作为未经验证的通用映射。

ORCA 文档将 M 0 定义为最低本征值模式；频率模式与优化器模式可能存在质量加权、坐标基、投影和排序差异。现有 `ts_geom_block` 中将 M 序号解释为频率序号的注释必须修订。

### 6.2 验证方案

1. 为拟支持的 ORCA 版本准备单虚频、多虚频、近简并/低频竞争、线性与非线性分子样例。
2. 从同一来源 Hessian、几何及向量建立候选对应关系。
3. 核对实际优化器坐标表示和初始选模输出，确定能否读取可比较的模式证据。
4. 能比较向量时，统一原子顺序、旋转、质量加权、平移转动投影与归一化，再计算绝对重叠；符号相反视为同一模式。
5. 记录最佳匹配、次佳匹配、间隔和证据来源。阈值由样例标定，不在设计阶段编造固定可信阈值。
6. 将最终解析的 optimizer_mode_index、版本和验证方法写入解析产物。
7. 优化启动后检查实际跟随证据；不一致时终止后续推进并保留诊断文件。

如果需要运行 ORCA 才能取得映射证据，必须使用受调度管理、资源有界的准备阶段；不能在 API 请求中同步启动无界计算。具体准备输入须通过版本验证，不能假定存在未确认的 dry-run 能力。

### 6.3 状态与降级规则

映射状态：`pending / resolved / ambiguous / unsupported / mismatch`。

仅 `resolved` 可进入正式定向优化。近简并模式可能只能确定子空间，不能保证唯一向量，此时标记 ambiguous。无法证明对应关系时禁止静默改为 M 0 或改为按键选择；可由用户退出本流程并创建普通 TS 任务。

真实 ORCA 验证不能由字符串快照测试替代。无法通过本节门槛时允许交付来源解析/预览，但不得将“选虚频后精确定向优化”标记为完成。

## 7. 数据契约

新增强类型、优先 frozen dataclass 的模型，避免把全部语义塞进 bool/int kwargs。

### 7.1 请求示例

```json
{
  "workflow": "tsmode",
  "schema_version": "tsmode_request_v1",
  "source": {
    "bundle_id": "fb_example",
    "revision": "source_revision",
    "job_id": "source_job",
    "item_id": "source_item",
    "entry_id": "source_entry"
  },
  "target": {
    "source_mode_index": 7,
    "target_mode_id": "tm_example"
  },
  "optimization": {
    "initial_hessian": "read_source",
    "max_iterations": 250,
    "convergence": "tight",
    "recalc_hess": 5,
    "retry_limit": 2
  },
  "validation": {"final_frequency": true},
  "resources": {"nproc": 8},
  "request_id": "client_generated_id"
}
```

示例数值不是所有体系的推荐参数。用户请求不包含可信的 optimizer_mode_index；服务端从快照解析。method/charge/multiplicity 从经校验来源获得，缺失时在准备阶段补齐。

### 7.2 服务端规范化快照

包含：来源全部资产及哈希、结构及质量信息、计算级别、原生模式编号、频率、原始向量与约定、target_mode_id、映射版本/状态/证据、实际后端参数、资源、来源 lineage。

目标身份须绑定源文件及向量，不能只用频率数值。不要直接对未经规范化的浮点文本生成跨平台稳定身份；保存来源字节哈希与规范化版本。

### 7.3 错误码

| 错误码 | 含义 |
|---|---|
| frequency_source_incomplete | 缺少频率/向量/几何 |
| hessian_missing | 缺少可读取 Hessian |
| source_geometry_mismatch | 几何或原子顺序不匹配 |
| target_mode_invalid | 非虚频、编号不存在或向量无效 |
| source_revision_conflict | 来源发生变化，需要重新选择 |
| mode_mapping_ambiguous | 不能唯一确定优化目标 |
| mode_mapping_unsupported | 当前后端版本或表示不支持 |
| target_mode_mismatch | 实际跟随模式与请求不符 |
| source_fetch_pending | 远程输入尚未获取 |

建议语义：422 表示输入不合法，409 表示来源修订冲突或资源状态尚未就绪，404 表示来源不存在；沿用项目统一错误响应格式。

## 8. API 与 CLI

建议新增来源服务，复用现有任务创建和编辑重算 API，不新造第二套提交系统。

拟议 API：

- `GET /api/v1/jobs/{id}/frequency-sources`：返回具体 item/entry 频率包及完整性。
- `POST /api/v1/frequency-sources/import`：导入文件，返回 bundle_id；具体上传传输沿用现有 intake 机制。
- `GET /api/v1/frequency-sources/{bundle_id}`：来源元数据、模式及修订。
- `POST /api/v1/frequency-sources/{bundle_id}/prepare`：验证/按需获取，必要时返回受调度准备任务状态。
- `POST /api/v1/tsmode/preview`：规范化请求、校验目标、返回摘要及指纹。
- 既有 `POST /api/v1/jobs`：提交 workflow=tsmode。
- 既有 edit-draft / edit-recalculate：读取、预览和提交重算。

静态与动态路由顺序遵循现有规范。来源过滤不能只看作业状态，应看产物完整性。GET 不触发昂贵计算。

拟议 CLI：

```text
acp run tsmode --source-bundle bundle.json --source-mode-index 7 --output ./tsmode_out
```

`bundle.json` 描述经校验的本地来源文件引用及计算级别。GUI 和 CLI 共用同一解析器，不允许 CLI 绕过映射校验。调度器把管理资产落地为工作目录中的 bundle，远程节点不需要直接访问 Web API。

## 9. 执行状态与计划

阶段顺序：

1. prepare_source：获取、验证并快照来源。
2. resolve_target：目标及优化器模式映射。
3. optimize_ts：读取 Hessian，定向 OptTS。
4. frequency_final：对最终优化结构执行同级别频率。
5. validate_ts：生成鞍点及反应运动评估。
6. publish_results：写入 manifest、结构条目及报告。

可复用现有 CalculationPlan/Executor，但不能改变 build_simple_plan 对其他简单计算的默认单步行为。准备及验证阶段由专用编排器组合；若需扩展公共 StepKind，必须说明复用需求，首期优先避免无必要扩展。

ORCA 输入语义示例，n 为已验证的优化器序号：

```text
! <resolved method keywords> OptTS
%geom
  InHess Read
  InHessName "source.hess"
  TS_Mode {M n} end
end
```

这是语义示例，非可直接运行的完整输入。实际拼写、Hessian 文件格式和行为由版本测试锁定。读取来源时不能同时无意写入重新计算初始 Hessian 的选项。

## 10. 重试、恢复与修改重算

### 10.1 重试原则

- SCF 失败：只采用用户允许的 SCF 恢复策略，保留目标与计算级别。
- 几何不收敛：可减小步长、重新计算 Hessian或从最后有效结构启动，必须重新核对目标语义。
- 重启结构与来源几何不同：不得无校验地将旧来源模式编号继续用于新 Hessian。
- 不能确定目标时停止并报告，不自动回到最低模式。
- 不自动更换泛函、基组、溶剂、电荷或多重度。
- 所有 attempt 独立保存输入、输出、目标解析及恢复动作。

现有 TS_MODE_DIRECTED 的布尔默认救援只允许用于未指定目标的旧流程。新请求携带显式目标时，由模式保持策略控制。

### 10.2 检查点

指纹包含来源几何/Hessian/向量、目标身份、计算级别、解析器与映射版本、关键优化参数。目标或体系改变，优化及后续频率缓存失效。

最终频率失败而优化已成功时，允许从频率阶段恢复，前提是最终结构哈希与计算级别一致。模式映射失败不得伪装成可以从优化阶段续算。

### 10.3 任务操作

- 编辑目标：返回同一来源模式选择页面，重新确认并生成预览指纹。
- 更换来源：清空旧目标、Hessian 绑定和检查点。
- 复制任务：复制不可变输入和来源 lineage，创建独立任务。
- 原地重算：遵守现有清理失败阻止排队规则及 request_id 幂等。
- 续算按钮：由实际恢复矩阵控制，不能仅因为 primitive 有 checkpoint 就宣布支持。

## 11. 文件布局、远程与清理

沿用项目 Job File Layout Spec，以下为拟议相对布局，实施时确认与现有目录约定一致：

```text
INPUT/tsmode/
  source.xyz
  source.hess
  source_modes.json
  source_bundle.json
  source.gbw                 # 可选
WORK/tsmode/
  mapping/
  optimize/attempt_001/
  frequency/
RESULT/tsmode/
  target_resolution.json
  tsmode_report.json
  optimized.xyz
  normal_modes.json
RESULT/result_manifest.json
```

INPUT 是当前任务拥有的快照；不要引用浏览器上传临时路径或来源任务可被清理的目录。实际是否使用 INPUT 顶层，应以现有权威布局约定为准，不能为了本功能破坏目录契约。

远程提交同步所有必要资产，并校验哈希；Hessian 路径为任务内受控路径。保存远程实际 ORCA 版本。结果抓取包括最终结构、频率产物、模式映射证据及报告。

取消/暂停沿用现有 lifecycle；不能把准备子进程留在调度器管理之外。清理策略不得删掉仍被当前任务检查点引用的资产。来源任务删除不影响新任务复现。

如增加运行时依赖，必须同时更新 pyproject.toml 与 requirements-node.txt；首期优先复用 numpy 和现有解析能力。

## 12. 结果契约与 UI

报告建议采用 `tsmode_report_v1`，至少包含：source、target、mapping、resolved_level、attempts、optimization_status、frequency_status、imaginary_modes、validation、artifacts、warnings。

将计算执行状态与化学验证状态分开：

| 场景 | 执行/验证语义 |
|---|---|
| 优化失败 | 执行失败，保留最后有效结构 |
| 优化完成、最终频率失败 | 验证未完成，可恢复频率阶段 |
| 无显著虚频 | 计算完成，未验证为一阶鞍点 |
| 一个显著虚频 | 一阶鞍点候选；目标运动仍需检查 |
| 多个显著虚频 | 高阶鞍点或需要进一步诊断 |
| 模式对应无法判断 | 显示待人工确认，不给伪精确成功结论 |

保留原始负频率与使用的阈值。目标一致性可展示内部坐标变化和可比较的向量证据；初末结构变化较大时不以简单向量重叠直接判断化学反应相同。

结果页提供来源/最终振动对照、目标摘要、实际输入、重试历史、修改目标后重算和创建 IRC。最终结构以 TS 候选语义进入结构来源列表，标签不应被误读为已通过 IRC 验证。

## 13. 模块修改清单

| 文件/目录 | 修改内容 |
|---|---|
| `src/acp/catalog.py` | 注册 tsmode、字段 schema、后端能力、参数映射与 CLI 转换 |
| `src/acp/cli.py` | 独立入口、来源包与目标参数，调用共享验证 |
| `src/acp/workflows/registry.py` | 工作流 builder 注册 |
| `src/acp/workflows/tsmode.py`（新增） | 薄工作流适配器 |
| `src/acp/calculations/tsmode/`（新增） | contracts/source/mode_mapping/engine/validation，模块按职责拆分 |
| `src/acp/calculations/plans.py` | 必要的计划组合辅助，保持旧行为 |
| `src/acp/calculations/primitives/optimize.py` | 显式目标透传、重试保护、恢复证据 |
| `src/acp/calculations/primitives/_common.py` | 参数白名单及能力传递核查 |
| `src/acp/backends/orca.py` | 薄适配参数映射 |
| `src/cccp/qc/interfaces/orca_ts.py` | 区分源编号/优化器编号、Hessian 输入生成、修订注释 |
| `src/cccp/qc/interfaces/orca.py` | 来源 Hessian 落地、TS 输入与实际目标证据解析 |
| `src/acp/results/frequencies.py` | 来源绑定元数据；兼容 normal_modes_v1 |
| `src/acp/results/structure_viewer.py` | TSMode 最终结构和频率解析 |
| `src/acp/storage/manifest.py` | 新产物登记，尽量沿用现有 schema 扩展字段 |
| `src/acp/api/` | 来源 API、预览、提交校验与能力返回 |
| `src/acp/scheduler/job_edit.py` | 编辑覆盖注册、修订指纹和 draft |
| scheduler runner/manager/remote | 工作流执行映射、输入同步、恢复能力及清理核查 |
| `frontend/ACP_Workbench_v2.html` | 简单计算入口和向导挂载 |
| `frontend/js/tsmode_editor.js`（新增） | 向导状态与来源/目标/预览交互 |
| `frontend/js/vibration_viewer.js` | 当前模式显式确认与快捷创建事件 |
| `frontend/js/job_editor.js` | 编辑回显及重算集成 |
| 相关 docs/AGENTS | 上线后同步工作流、接口、文件布局及测试说明 |

新增目录的 `__init__.py` 只做导出，不放实现。修改前阅读对应目录 AGENTS.md。不要通过 frontend 和 backend 分别维护独立默认值导致漂移。

## 14. 兼容迁移

- 普通 optimize、frequency、BatchOptimize 的默认行为不变。
- 旧 ts_mode bool/int 在兼容层解释，不能作为新 API 的完整契约；显式拒绝 bool 被当成整数序号。
- 既有 normal_modes_v1 的 mode_index 语义不变，新增映射存放独立产物，不重编号旧模式。
- 新增 workflow 必须进入编辑覆盖审计、能力目录、调度执行白名单、远程 CLI 和结果解析。
- 旧任务缺 Hessian 时可读可看，不伪造可执行能力。
- 优先利用现有 JSON spec/manifest 存储，不预设必须新增数据库表；只有来源资产生命周期确实需要时才新增迁移。
- 已有 PES DFT 计算级别规范化工具若可复用则共用，不复制一套泛函/基组/溶剂规则。

## 15. 测试与验收矩阵

### 15.1 单元与契约测试

- 来源完整/不完整、不同 item/entry、截断输出、多频率段选择。
- 原子顺序错误、几何不符、非有限 Hessian、维度错误和缺失向量。
- 虚频过滤不改变 mode_index；mode 0、布尔值、负值及越界校验。
- 同一目标向量反号不改变物理匹配结果；近简并产生 ambiguous。
- 输入生成包含读取来源 Hessian，且不意外覆盖为 Calc_Hess。
- SCF 与几何重试保留目标；禁止默认 M 0 覆盖。
- 修改目标/来源使指纹失效；仅频率失败可恢复已完成优化。
- API/CLI 规范化一致，来源修订冲突、幂等重放、编辑覆盖审计。
- 源任务清理不破坏新任务快照；远程资产缺失/哈希不符拒绝执行。

### 15.2 UI 集成测试

- 两入口进入同一向导；快捷入口准确绑定当前结构和模式。
- 默认高亮不等于确认；预览与已确认目标明确区分。
- 切换来源后清空目标，旧异步响应不能恢复旧选择。
- 缺 Hessian、无虚频、远程待获取、映射不支持均有明确阻止原因。
- 动画振幅不改变几何；列表排序不改变提交编号。
- 编辑重算完整回显；普通优化不携带 TSMode 残留参数。

### 15.3 真实 ORCA 验证（上线必需）

| 样例 | 验证目标 |
|---|---|
| 已知单虚频 TS | 来源读取、选择映射、优化和最终频率闭环 |
| 含至少两个虚频的初猜 | 选择非默认模式后，实际跟随确实不同 |
| 低频扭转与反应模式竞争 | 不因最负频率/列表位置误选 |
| 近简并模式 | 能识别歧义并阻止伪确定性 |
| 线性/非线性体系 | 无固定减 5/减 6 的编号假设 |
| Hessian 重算或中断恢复 | 目标保持或明确报告无法保持 |
| 本地和远程同源输入 | 参数、资产和目标解析一致 |

记录 ORCA 精确版本、样例来源、输入/输出、模式证据和验证结论。测试文件授权与体积应满足仓库要求。无法获得真实引擎时明确标记此项未验收，不用 mock 替代完成声明。

建议新增测试文件：test_acp_tsmode_source.py、test_acp_tsmode_mapping.py、test_acp_tsmode_engine.py、test_acp_api_tsmode.py，并扩展现有 ORCA TS、frontend_sync、job_edit 和 remote 测试。实际命名以仓库测试结构为准。

## 16. 实施里程碑

### P0：模式映射技术验证

交付支持版本矩阵、真实多虚频样例、编号/向量对应规则和不支持条件。没有通过 P0，不宣称完整功能可用。

### P1：来源模型与只读预览

交付结果包解析、快照、哈希、远程获取、来源 API 和虚频选择 UI。完成输入异常与来源切换测试。

### P2：独立工作流执行

交付 catalog/CLI/API/registry、TS 优化复用、最终频率及结果报告。打通本地真实闭环。

### P3：生命周期与远程

交付编辑重算、幂等、续算矩阵、目标保持、远程文件同步与清理隔离。通过远程样例。

### P4：发布验收

完成真实引擎测试矩阵、适用性说明和文档同步；按配置/能力矩阵控制入口可用性。未支持版本可浏览来源，但不得执行未经验证的选模路径。

后续增强：Hybrid Hessian、跨计算级别来源、模式重叠持续监测、多坐标协同模式辅助选择。它们不应阻塞首期清晰限定的功能，但任何影响模式准确性的事项不能推迟为可选优化。

## 17. 完成定义

- [ ] 用户能从频率结果动画明确选择并确认一个虚频。
- [ ] 来源几何、Hessian 和模式一致且形成独立输入快照。
- [ ] 频率编号与优化器编号经过真实版本验证，不使用猜测转换。
- [ ] 实际 ORCA 目标与用户目标有可核对证据。
- [ ] 重试、重启及编辑重算不静默丢失目标。
- [ ] 最终频率针对最终结构重新计算。
- [ ] 执行成功与化学验证状态分开显示。
- [ ] 本地、远程、API、CLI 和编辑回显一致。
- [ ] 普通优化及历史任务不受行为破坏。
- [ ] 所有未完成的真实引擎验证明确列出，不冒充已通过。

## 18. 参考资料

- [ORCA 6.1 Transition State Searches](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations_TS.html)：OptTS、TS_Mode、Hessian 读取、模式跟随及最终频率检查。
- [ORCA 6.0 Geometry Optimization](https://www.faccts.de/docs/orca/6.0/manual/contents/detailed/geomopt.html)：用于版本差异核查，不能替代目标部署版本测试。
- 项目文档：`docs/ACP_Job_File_Layout_Spec.md`、`docs/ACP_Structure_Viewer_DevDoc.md`。
- 项目实现：`frontend/js/vibration_viewer.js`、`src/acp/results/frequencies.py`、`src/acp/calculations/primitives/optimize.py`、`src/cccp/qc/interfaces/orca_ts.py`。

本文没有将 ORCA 原生能力与 ACP 当前已实现能力混为一谈；所有新增能力均按上述里程碑实施并验收。

## 19. 实施状态（2026-09-22）

### 已交付

| 模块 | 状态 |
|---|---|
| `src/cccp/qc/interfaces/hess_file.py` | ✅ `.hess` 解析器（$atoms/$coords/$hessian，非有限值/维度/对称性校验） |
| `src/cccp/qc/interfaces/orca_ts.py` | ✅ `ts_geom_block` 支持 `InHess Read`/`InHessName`；`hess_file_name`+`calculate` 组合显式拒绝；M 序号语义注释修订（M 0 = 最低本征值，非频率输出编号） |
| `src/cccp/qc/interfaces/orca.py` | ✅ `transition_state_opt` 接受 `hess_file`（暂存为 `<name>.hess`）；`TsOptResult` 增加 `energy`/`frequencies` 只读属性供归一化 |
| `src/acp/calculations/tsmode/` | ✅ contracts（三 ID 模型、§7.3 错误码、tsmode_request_v1 / tsmode_report_v1 / target_resolution_v1）/ source（来源加载校验 + 质量加权投影简正分析 + Kabsch 几何校验 + 单位自识别 + 频率交叉核对）/ mode_mapping（向量重叠映射 + ambiguous 简并判定 + enforce_launch_gate）/ validation（执行/验证状态分离）/ engine（六阶段编排 + 指纹检查点 + 仅频率阶段恢复） |
| `src/acp/calculations/primitives/optimize.py` | ✅ 显式 `ts_mode` int 目标保护：几何类失败 → terminal（停止并报告），仅 SCF 救援保留目标（§10.1） |
| 注册面 | ✅ catalog（active + METHOD_SCHEMAS tsmode）/ CLI `acp run tsmode` / workflows/tsmode.py + registry / scheduler jobs（自动派生）/ stage_tasks / job_edit（EDIT_ACTIVE_WORKFLOWS + 覆盖审计通过）/ runner（白名单 + `_materialize_tsmode_bundle` 物化 + 命令分支）/ remote script_gen（白名单 + `build_remote_tsmode_tail`） |
| API | ✅ `GET /api/v1/jobs/{id}/frequency-sources` + `POST /api/v1/jobs` tsmode 分支（来源解析→bundle 加载→目标解析→launch gate→`source_type: tsmode_bundle` 重写 + sha256） |
| 结果 | ✅ structure_viewer `_resolve_tsmode`（optimized + source 快照条目 + 振动）/ manifest 产物（structure/frequency_modes/report/file） |
| 前端 | ✅ `js/tsmode_editor.js`（ACPTsmodeEditor 三面板向导：来源→模式确认→设置；预览≠确认；request-token 防旧响应）+ vibration_viewer 0.8.0 快捷创建 + Workbench 提交分支 + i18n（zh/en） |
| 测试 | ✅ tests/test_acp_tsmode_{source,mapping,engine,orca_inputs}.py + tests/test_acp_api_tsmode.py + tests/test_acp_structure_viewer_tsmode.py + tsmode_synthetic.py 合成夹具 + frontend_sync 10 项新契约 |

### 未完成（不冒充已通过）

1. **P0 真实 ORCA 模式映射验证（上线硬门槛，§6.2/§6.3）**：`TS_MODE_MAPPING_VERIFIED_VERSIONS` 为空集。默认提交被 `enforce_launch_gate` 以 `mode_mapping_unsupported` 拒绝（`require_verified_mapping=True`）；完成 §6.2 样例矩阵后把验证过的版本串填入该 frozenset，或在请求中显式 `allow_unverified_mapping`（CLI `--allow-unverified-mapping`）。当前实现采用的是"本征值升序排名"映射假设（evidence 中记录 assumption 字段），**该假设未经真实引擎核对，不构成完成声明**。
2. §15.3 真实引擎测试矩阵全部未执行（需算力与授权样例）。
3. 远程 tsmode 提交仅实现了命令生成与资产同步路径（script_gen + runner 物化），未做远程端到端样例验证（§16 P3 远程样例）。
4. 本地导入入口（`POST /api/v1/frequency-sources/import`）未实现；首版仅支持从已有任务的频率产物选择来源（§4.2 其余来源方式后续补充）。
5. 编辑重算（edit-recalculate）对 tsmode 已登记覆盖审计；目标编辑回显 UI（返回模式选择页）为后续项（§10.3）。
