# task-wizard — ACP Next 新建计算向导模块（A-01 … A-09）

- 文件：`frontend/js/modules/task-wizard/module.js`（classic script，无构建、无 ESM）
- 样式：`frontend/css/modules/task-wizard.css`（仅 `.acp-wizard*` 作用域，复用 `base.css` / `shell.css` 变量）
- 测试：`frontend/js/module_tests/acp2_task_wizard.test.js`
  （`node frontend/js/module_tests/run_all.js acp2_task_wizard`）
- 注册名：`window.ACPNextModules["task-wizard"]`（`descriptor.version = "0.1.0"`，与并行批次其它新模块一致，未随功能升版）
- 范围：**A-01 向导骨架/步骤导航**、**A-02 分子输入与来源**、**A-03 电荷/自旋/多重度奇偶校验**、
  **A-04 资源（内存/核数/队列）**、**A-05 方法校验 + 预检指纹**、**A-06 提交/回执**、
  **A-07 其余 11 个 active 工作流的配置面板**、**A-08 提交检查弹窗**、
  **A-09 任务草稿（自动保存 / 恢复横幅 / 丢弃 / 409 冲突 / 提交后删除）**。
  本模块只有一条任务创建路径（`POST /api/v1/jobs`，经注入 client），
  不复制 `task-config` 的提交实现；草稿只走既有 `GET/POST/PUT/DELETE /api/v1/drafts`，
  没有 `/uploads`、没有第二个缓存、没有 `localStorage`。

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["task-wizard"] = {
  descriptor: {
    id: "task-wizard", interfaceVersion: 1, version: "0.1.0",
    provides: ["task.wizard"], binds: [],
    capabilities: { requires: [], provides: ["task.wizard"] },
    hosts: ["acp-web"], supportedInstancing: "single",
    configSchemaVersion: 1,
    error: { titleKey: "next.wizard.errorTitle", messageKey: "next.wizard.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ },
  _internals: { /* 纯函数测试缝，见 §15 */ }
};
```

`ModuleInstance`：`mount(hostEl) → Promise`、`updateContext(snapshot)`、`suspend()`、
`resume()`、`dispose()`。

- **槽位无关**：`mount()` 只依赖传入的宿主元素（或 `api.panel.bodyEl`），不假设
  工作区/槽位/recipe；`binds: []`，不声明必需上下文。
- **注入 client**：`deps.client.request`（首选）/ `deps.client` 为函数 /
  `deps.client.transport.request` / `deps.request` / `window.ACPClient.transport.request`。
  模块源码没有 `fetch` / `XMLHttpRequest`：解析、预检与提交全部经这一条缝。
- **注入 commands（导航）**：Shell 的 `hostApi.commands` = `window.ACPShell.commands`。
  A-06 成功后**只**通过 `commands.openTask({jobId, attempt, source})` 定位任务；
  `commands` 缺失只隐藏回执里的“在任务面板打开”按钮，不影响提交本身。
- **单状态**：只有一个模块 `state` 对象；没有 `localStorage`、没有第二份目录/结构
  缓存、没有第二个 3D viewer、没有第二套预检实现。A-09 草稿是唯一被持久化的
  展示/输入状态，从服务端读回的 snapshot 只保留到“应用或丢弃”为止（§9）。
- **生命周期守卫**：所有异步结果带挂载 `generation` + 目录/输入/资源/队列/预检/提交/
  草稿 `token`；`dispose()` 幂等并移除 DOM/监听/上下文订阅，取消草稿防抖定时器，
  迟到结果一律丢弃（迟到回执也不会 `openTask`）；`suspend()` 期间不重绘，
  `resume()` 重新渲染已应用状态。
- client/doc 缺失：`doc` 缺失以 `TypeError` 拒绝 `mount`；client 缺失以
  `task-wizard: structure client missing (...)` 拒绝 `mount`（不静默降级）。
  因而“missing client hides submit”是防御分支：`canSubmit()` 与 `renderSubmit()`
  都要求 `state.client`，正常装配下 mount 失败时根本没有 submit 表面。

## 2. 端点清单（全部经注入 client，均为 `src/acp/api/` 既有路由）

| 用途 | 方法与路径 | 读取字段 |
|---|---|---|
| 工作流目录（A-01） | `GET /api/v1/workflow-catalog` | `workflows[]`（`status` / `visible` / `method_schema_id` / `default_backend`） |
| 方法目录（A-01/A-05/A-07） | `GET /api/v1/method-catalog` | `method_schemas` / `field_definitions` / `basis_catalog` / `method_meta` |
| 工作流输入契约（A-07） | `GET /api/capabilities?workflow=<id>` | `workflow_contract`（`coverage` / `input.required_any` / `input.properties` / `method.properties` / `method.enums`） |
| SMILES 解析（A-02） | `POST /api/v1/molecule/resolve` | `{smiles}` → `valid` / `smiles` / `charge` / `multiplicity` / `formula` |
| 3D 坐标（A-02） | `POST /api/v1/molecule/embed` | `{smiles}` 或 `{molfile}` → `xyz` / `num_atoms` / `formula` |
| 结构解析（A-02） | `POST /api/v1/structures/parse` | `{content, format:"auto", filename}` → `structures[0]` |
| 资源权威（A-04） | `GET /api/capabilities?detail=summary` | `capacity.limits` / `capacity.pool` / `capacity.available` / `capacity.unknown` |
| 队列目录（A-04） | `GET /api/v1/nodes` | `nodes[].name` / `nodes[].status` / `nodes[].queue` |
| 预检（A-05） | `POST /api/v1/preflight` | 请求体见 §6.1；响应 `feasible` / `reasons` / `advisories` / `plan_digest` |
| 提交（A-06） | `POST /api/v1/jobs` | 请求体 = §6.1 冻结计划 + `preflight_fingerprint`；响应 `job_id` / `status` / `attempt` / `plan_digest` / `idempotent_replay` |
| 草稿列表（A-09） | `GET /api/v1/drafts` | `items[]`（`draft_id` / `name` / `project_id` / `workflow` / `structure_count` / `revision` / `created_at` / `updated_at`，按 `updated_at` 倒序） |
| 草稿详情（A-09） | `GET /api/v1/drafts/{draft_id}` | 摘要 + `snapshot`（本模块只读最新一条；`kind` / `version` 判定归属） |
| 新建草稿（A-09） | `POST /api/v1/drafts` | `{name, project_id, workflow, snapshot}` → 201 摘要（`revision=1`） |
| 更新草稿（A-09） | `PUT /api/v1/drafts/{draft_id}` | `{name, project_id, workflow, expected_revision, snapshot}`；旧 `expected_revision` → `409 revision_conflict`，行不存在 → `404 draft_not_found`，缺 `expected_revision` → `422` |
| 删除草稿（A-09） | `DELETE /api/v1/drafts/{draft_id}` | → `{deleted:true}`；不存在 → `404 draft_not_found` |

写端点只有：`/preflight`（零副作用 dry-run，`_preflight_projection`：不创建任务/attempt/prepare/预留）、
`/jobs`（`create_job`，唯一建任务路径）与 A-09 的 `/drafts` CRUD（`src/acp/api/drafts.py`，
仅用户显式丢弃/提交成功后删除；自动保存不触碰任务）。**没有** `/uploads` 调用；
模块源码 pin 断言 `fetch(`、`XMLHttpRequest`、`localStorage`、`jobCommands`、`/uploads`
不出现，且 `/drafts` 只出现在 `ENDPOINTS.drafts` 与其路由调用里（全部经注入 client）。
`workflow=<id>` 的契约读取只对 extended 工作流、且只对当前选中项发一次；简单四个工作流保持
mount 时只有两个目录请求，不额外读契约。

## 3. A-01 / A-02（既有能力，行为未回退）

### 3.1 工作流目录投影

- 唯一来源：`GET /api/v1/workflow-catalog`。
- 只保留 `status === "active"` 且 `visible !== false` 的条目，**保持目录顺序**；退役项
  （`energy` / `optfreq` / `conformer` / `benchmark` 等）与 `visible:false` 项不会进入 `<select>`。
- 默认值：**第一条 active 且可见的目录条目**（`pickDefaultWorkflow(entries) = entries[0]`），
  没有硬编码工作流 id，也不回退退役项；目录为空时进入 `empty` 态并给出可见原因。
- 目录读取失败：`error` 态 + 可见错误 + `重试`；重试会重新请求两个目录端点。

### 3.2 方法目录投影（A-01 最小投影 + A-07 全量投影）

- 选中工作流的 `method_schema_id` → 该 schema 的 `method_levels[].fields`。投影模式是
  **字段集合策略**，不是 workflow id 白名单：schema 的每个字段都属于既有 generic
  single-structure 字段集合（`functional` / `basis` / `gfn` / `dispersion` / `ri_approximation` /
  `aux_*_basis` / `solvent*` / `grid` / `scf_convergence` / `max_steps` / `opt_convergence` /
  `recalc_hess` / `opt_level` / `electronic_state`）时，保留 A-01 的最小投影（`functional` /
  `basis` / `gfn`）与原有 wire body；schema 引入任何其他字段（scan / PES / IRC / TS-mode /
  CAS / NMR / Confsearch / Batch）或没有任何字段（冻结 payload）时进入 A-07 全量投影。
  根 `data-acp-method-projection` = `minimal` / `extended`，`data-acp-method-support` =
  `ok` / `schema_missing` / `schema_empty`。
- 取值顺序：schema `profiles` 的默认 profile（`profile_id === "default"`，否则第一条）→
  `field_definitions.<field>.default`。有 profile 时只渲染 profile 命名的 levels（例如
  Confsearch 的 `xtb-crest` 不渲染 `refinement_sp`）；profile 切换会重新播种。
- 全量投影按 `field_definitions` 渲染控件：`select` + `options` / `per_backend`（含
  `option_labels_zh`）、`bool` → checkbox、`int` / `float` → 文本数值控件、`multi` →
  目录 option 复选框、`advanced` 字段加弱化样式；`type:"module"`（`electronic_state`）
  不渲染为通用字段。无 definition 的 schema 字段渲染为文本控件，不伪造 select / 默认值。
- 服务端 profile 值不在 option 列表时（例如 NMR 的 `6-311G(d)` GIAO basis）作为该 select
  的当前 option 保留；大小写差异按服务端语义折叠（`cpcm` → `CPCM`）。只有“用户改成了
  目录外值”才在前端报 `method_invalid_option`。`int` / `float` 的 min/max、整数性、bool
  原样保留；`float` 在预检体中输出为十进制字符串（canonical digest 拒绝 JSON float）。
- `basis` 优先用顶层 `basis_catalog`；`functional`/`gfn` 用 `field_definitions.<field>.per_backend[engine]`，
  再退 `options`（`functional` 最后可退 `method_meta` 键）。
- 多个 profile 时显示 `方法预设`；切换会用该 profile 的 levels 重新播种。单 profile 隐藏选择器。
- A-05 的服务端方法校验**不在前端重算**：前端只做目录投影、类型/选项本地前置校验与字段级错误映射；
  最终权威仍是 `/preflight`。

### 3.3 步骤与导航

固定 3 步：`workflow → input → review`（索引 0/1/2）。

| 状态 | 载体 | 值 |
|---|---|---|
| 当前步骤 | 根 `data-acp-step` / `data-acp-step-index` | `workflow` / `input` / `review` |
| 已走到的最远步骤 | 根 `data-acp-max-step` | `0..2` |
| 当前步骤是否有效 | 根 `data-acp-step-valid` | `true` / `false` |
| 下一步是否可用 | 根 `data-acp-can-next` | `true` / `false` |
| 单步状态 | 步骤按钮 `data-step-state` | `current` / `complete` / `available` / `locked` / `invalid` |

- `input` 步骤的有效性按 A-07 adapter 分支：`structure` / `scan` / `batch` 仍要求结构 `ready`、
  A-03 电子状态有效、资源结束加载且资源字段有效；`source` / `payload` 不要求结构，改为
  源任务 / 冻结 JSON 有效，电荷/多重度可留空。任一无效：`下一步` 禁用，`data-role="nav-hint"`
  显示第一条可见原因。
- `review` 步骤：`下一步` 始终禁用（提交是 review 内的独立动作）。`data-acp-step-valid` 只有在预检
  **未失败/未过期** 时为 `true`；`idle`/`loading`/`ready` 均视为未阻塞该步骤（提交检查另行要求预检通过）。
- `上一步`/已访问步骤可回退；未到达步骤 `locked` + `disabled`。编辑结构输入会把
  `step`/`maxReached` 收回到 `input`。
- 进入 `input` 步骤（首次 or 重试）才请求 A-04 资源；A-07 contract 在 extended 工作流被选中时请求。
  当前目录的第一条 active 是 minimal 条目，所以默认 mount 仍然只有两个目录请求。

### 3.4 分子输入三个来源

| 来源 | UI | 端点序列 | 请求体 |
|---|---|---|---|
| SMILES | `data-role="smiles"` + `解析 SMILES` | `resolve` → `embed` | `{smiles}` → `{smiles}` |
| 粘贴文本 | `data-role="structure-text"` + `解析文本` | `structures/parse` | `{content, format:"auto", filename:""}` |
| 文件 | `input[type=file][data-role="structure-file"]` | 浏览器内读取 → 同一个 parse 端点 | `{content, format:"auto", filename:<文件名>}` |

- 三个来源共用一个 `runInput()` 管道；每次开始递增输入 token，旧请求迟到结果不写 DOM；
  同一来源按钮请求中禁用；切换来源可发起新请求，新 token 淘汰旧请求。
- `parse` 优先用 `structures[0].xyz`；无 3D 坐标时回退 `embed`（有 `molfile` 发 `{molfile}`，
  否则有 `smiles` 发 `{smiles}`），两者都没有则报“没有可用于坐标生成的输入”。
- 文件只本地读取（`file.text()` / `FileReader`，可注入 `deps.readFile`），没有上传端点。
- 任何失败（`resolve.valid !== true`、`embed` 错误/空 `xyz`、`parse.ok !== true`、
  传输 reject、文件读取失败、空输入）→ 输入 `error` 态 + 可见文案，`下一步` 禁用且点击不前进；
  成功不自动跳步。

### 3.5 A-07 工作流输入契约与 adapter

- **唯一来源**：`GET /api/capabilities?workflow=<id>` 的 `workflow_contract`
  （`src/acp/api/workflow_contracts.py::describe_workflow`），在 extended 工作流被选中时
  读取一次；generation + token 守卫，切换工作流 / dispose 后旧回包丢弃。契约失败或
  loading 是显式阻塞（可见 `data-role="contract-status"` + 重试），不会静默退回通用表单。
- `coverage:"not_provided"`（casscf / nmr）仍然有方法 schema：显式显示
  “服务端尚未发布输入契约”，输入走 A-02 结构包络；这不是静默 generic 表单。
- 五类 adapter（`data-acp-input-adapter`）：
  | adapter | 触发（契约/ schema） | 输入 | method |
  |---|---|---|---|
  | `structure` | 默认；`input` 有 chemistry envelope | A-02 结构 XYZ + charge/multiplicity | `{schema_id, levels}` |
  | `batch` | `input.properties.items` 且 `required_any` 含 `items` | `{source_type:"batch_structures", items:[one], charge, multiplicity}`（单结构批次） | `{schema_id, levels}` |
  | `scan` | schema 含 `scan_coordinate_*` 字段 | A-02 结构 + 操作者输入的 0 基扫描原子；简单 scan 组 `input.scan_coordinates`，PES 组 `input.coordinate` + `input.protocol` | 简单 scan：`{schema_id, levels}`；PES：`{mode:"bond_length_scan", schema_id, profile_id}`（**不发 levels**） |
  | `source` | `required_any` 含 `source_job_id` | `input.source_job_id` + 目录声明的 `source_product_id` / `source_mode_index`；TS-mode 把 schema `max_steps` / `recalc_hess` 映射到 `input.max_iterations` / `input.recalc_hess` | levels 若契约允许 `levels`；TS-mode 契约禁止 method，故省略 |
  | `payload` | `required_any` 含 `*_request` | `input.<path_request|gradient_request|constrained_opt_request>` = 操作者粘贴的 JSON 对象，原文提交 | 省略（上游冻结 payload 自己拥有科学参数） |
- **扫描原子不自动选择**：`data-role="scan-atoms"` 由操作者输入 2/3/4 个互不相同、在
  `structure.atom_count` 内的 0 基原子；空/数量不符/越界/重复都是显式字段错误。
- **PES 协议映射**：schema `scan_coordinate` → `input.coordinate`（`kind` / `atoms` /
  `unit` / `start` / `end` / `n_points` / `bond_type`）；`scan_driver` / `scan_optimizer` /
  `single_point` → `input.protocol`（`acp.calculations.pes.contracts` 的
  `ScanDriver` / `ScanOptimizer` / `SinglePointSpec` 键）。start/end 以十进制字符串提交；
  `use_scants` / bool 保持 boolean，int 保持 integer。
- **冻结 payload**：`data-role="workflow-payload"` textarea；空/非法 JSON/非对象都在本地
  显式阻塞，不发 `/preflight`、不发 `/jobs`。本模块不补上游默认值。
- **TS-mode / 源任务**：`data-role="source-job"` / `source-product` / `source-mode-index`；
  源任务必须是已存在任务（服务端 `/preflight` 的 source facts 会判定），本模块不伪造 Job ID。
- **列表只来自目录**：工作流、schema、level、field、profile、option、widget 类型和默认值
  全部来自两个目录 + 契约；模块源码没有 active/retired workflow id 字面量，也没有
  `r2SCAN-3c` / `def2-TZVPP` / `6-311G(d)` 之类科学值字面量。
- **构建顺序**：先 `validateMethodSchema`（类型 / min-max / 选项），再按 adapter 组
  `input` / `method`，最后加 `resources` / `project_id`；任一错误返回 `{request:null, errors[]}`，
  绝不返回半成品 generic body。A-05 只发这同一个 body，A-06 只冻结 body + fingerprint。

## 4. A-03 电荷 / 自旋多重度与奇偶校验

### 4.1 输入与播种

- `data-role="charge"`（电荷）与 `data-role="multiplicity"`（自旋多重度）都是**文本输入**，
  模块从不把非法值改写成合法值（“no silent normalization”）。
- 结构载入成功后，用**服务端返回的结构事实** `structure.charge` / `structure.multiplicity`
  播种（`resolve`/`parse` 返回的 charge/multiplicity）；服务端没给就保持空值，
  空值显示“电荷/自旋多重度必须是整数”的显式错误并阻塞 `下一步`，**不补 0/1**。
- 结构编辑（输入框改动）会把 `charge`/`multiplicity` 一起清空，等待新结构重新播种。

### 4.2 校验规则（ΣZ − charge）

纯函数 `validateElectronic(chargeRaw, multiplicityRaw, structure)`，按顺序：

1. 电荷必须是整数（`^[+-]?\d+$`，允许负电荷），否则 `next.config.error.charge_integer`；
2. 多重度必须是 ≥ 1 的整数，否则 `next.config.error.multiplicity_integer`；
3. 由结构 `formula`（Hill 式，如 `C2H6O`）解析元素计数并计算
   `electrons = ΣZ − charge`（`electronCount`）：
   - `electrons < 0` → 显式“电荷过高：没有剩余电子。”（电荷侧错误）；
   - `multiplicity > electrons + 1` → 显式“自旋多重度 {multiplicity} 超过当前电子数允许的上限 {limit}。”；
   - 奇偶：**偶数电子必须配奇数多重度，奇数电子必须配偶数多重度**，否则显式
     “电子数为 {electrons}，自旋多重度奇偶不匹配。”（`next.config.error.multiplicity_parity`）。
4. 公式缺失/不可解析/元素不在内置周期表（符号 1..103）→ 奇偶**不可校验**：
   显示 `next.s2scan.parity_unverifiable` 提示，不报假错、不阻塞，**也不进入 A-08 阻塞项**。

### 4.3 状态与可见性

| 载体 | 值 |
|---|---|
| 根 `data-acp-electronic-state` | `empty`（无结构）/ `ready` / `error` |
| 输入面板电子状态块 `data-acp-electronic-state` | 同上 |
| `data-role="parity-hint"` + `data-acp-parity` | `checked` / `unverifiable` / `invalid` |
| 字段错误 `data-role="field-error" data-field="charge"` / `"multiplicity"` | 隐藏或显式文案 |

无效时 `data-acp-step-valid=false`、`下一步` 禁用、`nav-hint` 显示该错误，点击 `下一步`
不会离开 `input` 步骤。结构就绪后摘要同时显示 `ΣZ − charge = {electrons}`（review 面板）。

## 5. A-04 资源（内存 / 核数 / 队列）

### 5.1 默认值权威（无硬编码）

- **唯一来源**：`GET /api/capabilities?detail=summary` 的 `capacity` 块
  （`src/acp/api/capacity.py::build_capacity_projection`，同一个 `AdmissionLimits` + 准入池）。
- 解析顺序（全部是服务端数字）：`capacity.available.cores` → `capacity.pool.cores`
  → `capacity.limits.max_cores`；内存同理 `available.memory_mb` → `pool.memory_mb`
  → `limits.max_memory_mb`。内存以服务端单位 **MB** 显示（如 `24576MB`）。
- `capacity.limits.max_cores` / `max_memory_mb` 作为输入上限一并下发；`capacity.unknown`
  原样保留在 `state.resources.defaults.unknown`（不补造）。
- **服务端没有可用数字**（如没有 `capacity` 块，或调度器未装配）：资源 `empty` 态 +
  可见说明“服务端没有发布资源默认值；留空表示使用服务端默认值。”，输入保持空，
  **不发明 4 核/2000MB 之类默认值**，也不阻塞（资源是可选项，服务端会用自己的默认值）。
- 读取失败：资源 `error` 态 + 可见原因 + `重试`；此时 `input` 步骤无效（有明确原因）。

### 5.2 内存格式（`memory_input` 单位/格式校验）

- 语法镜像服务端唯一解析器 `cccp/utils/resource_utils.py`：去空白、十进制/科学计数、
  单位 `TB|GB|MB|T|G|M`（大小写不敏感）、正有限数。
- **本模块要求显式单位**（比服务端更严格）：`16` 被拒绝并提示“内存需要带单位：
  例如 8GB、512MB、2T。”，而不是静默按 GB 解释；`16gb` → `16GB`、`2T` → `2TB`、
  `1.5 GB` → `1.5GB`。
- 超过 `max_memory_mb` → 显式“内存 {memory} 超过服务端上限 {max}。”。
- 核数：整数 ≥ 1；超过 `max_cores` → 显式“核数 {cores} 超过服务端上限 {max}。”。
- 空值 = 未设置（合法，服务端默认），不是错误。`0GB`/`-1GB`/`abc`/`16XB` 均拒绝。

### 5.3 队列（只读投影）

- 队列名来自 `GET /api/v1/nodes` 的 `nodes[].queue`（服务端节点配置，LSF 队列），
  按节点顺序去重显示为 chips（`data-queue-name` / `data-queue-node`）。
- **只读**：V1 提交字段策略里没有客户端可选 `queue` 字段（`validate_resource_request`
  只消费 `nproc/ncores/cores`，执行侧 `queue` 由节点/准入授予决定），因此本模块
  **不把队列放进请求体**，避免“静默吞字段”。队列加载失败只影响该投影
  （`data-acp-queue-state=error`），不阻塞向导与提交检查；无节点时显示服务端“未配置远程节点”语义。
- 该投影单独 4 态：`data-acp-queue-state` = `empty` / `loading` / `ready` / `error`，
  与资源默认值（`data-acp-resource-state`）分开，互不覆盖。

### 5.4 review 摘要

review 面板显示 `核数 {n} · 内存 {mem} · 队列 {queues}`；未设置时显示 `—`，
不补数字。

## 6. A-05 方法校验 + 预检指纹

### 6.1 请求体（冻结的提交同形体）

`POST /api/v1/preflight`（`V1PreflightRequest`，`extra="forbid"`）：

```json
{
  "schema_version": 1,
  "workflow": "<选中工作流 id>",
  "input": {
    "source_type": "xyz_text",
    "source": "<结构 XYZ 原文>",
    "charge": 0,
    "multiplicity": 1
  },
  "method": {
    "schema_id": "<工作流 method_schema_id>",
    "levels": { "<level_id>": { "engine": "orca", "functional": "wB97M-V", "basis": "def2-TZVPP" } }
  },
  "resources": { "nproc": 6, "mem": "24576MB" }
}
```

- `input` 只放冻结契约允许的键（`source_type`/`source`/`charge`/`multiplicity`），
  不放 `name`/`formula`/`atom_count` 等展示字段（简单工作流的 `input_keys` 不含它们）。
- `method.levels` 来自 §3.2 的目录投影（`engine` + 投影字段），空行被丢弃。
- `resources` 只在用户/服务端给了值时才出现；两者都空时**整个块省略**（服务端默认生效）。
- `project_id` 仅在上下文快照提供时附加；它不进入计划身份（服务端 `plan_digest`
  只覆盖 workflow/input/method/resources）。
- 服务端 `_preflight_projection` 依次执行与提交相同的 `validate_request_fields` →
  `normalize_preflight` → `_validate_simple_method_cli` → `validate_workflow_submission`
  → `validate_resource_request` → 目标/可行性检查 → `plan_digest`；本模块没有第二套
  方法校验实现。
- **throwaway 契约验证（本机 Python + `src` 导入）**：A-07 后对 15 个 active 工作流的
  module 生成请求体逐条执行上述真实校验（`validate_request_fields` / `normalize_preflight` /
  `method_levels_to_cli_flags` / `validate_workflow_submission` / `validate_scan_submission` /
  `frozen_plan_definition` + `plan_digest`）：**15/15 PASS**，包括 `scan` / `irc` / `tsmode` /
  `casscf` / `nmr` / `Confsearch` / `PESsearch` / `BatchOptimize` / 三个冻结 payload。
  之前 `tsmode` 的 `method.levels unknown_field` 已随 A-07 的 method-omission 修复；本验证
  只证明请求体通过真实契约层，不代表已真实提交任务。

### 6.2 指纹展示

- 成功（`feasible === true` 且有 `plan_digest`）：`data-acp-preflight-state="ready"`、
  `data-acp-preflight-valid="true"`、`data-acp-preflight-stale="false"`；
  `data-acp-plan-digest` 与 `data-acp-preflight-fingerprint` 都是完整 digest；
  `data-role="preflight-fingerprint"` 显示完整值，状态行显示截断值。
- digest 绑定 **计划身份** `planIdentity({workflow,input,method,resources})`
  （稳定排序 JSON）：任何影响计划的编辑（结构/电荷/多重度/工作流/方法/资源）
  都会让 `data-acp-preflight-stale="true"`、`data-acp-preflight-valid="false"`、
  `data-acp-preflight-state="error"`、`data-acp-step-valid="false"`，并显示
  “配置已变更，请重新预检。”；digest 仍显示（让用户看到过期的是哪个指纹）。
- 预检成功时**冻结请求体副本**（`state.preflight.request`）：A-06 提交的就是这份 frozen
  拷贝 + 其 `preflight_fingerprint`，不会从后续（可能已编辑的）状态重新推导请求体。
- 重新预检会发第二次 `/preflight`，携带编辑后的请求体；成功后重新 `ready`。

### 6.3 错误映射（400 / 422 / 不可行）

- 应用错误信封 `{detail:{code,message,params:{field,rule,allowed}}}`：按
  `params.field` 映射到 `workflow` / `input.*` / `input.charge` / `input.multiplicity` /
  `method*` / `resources.nproc|ncores|cores` / `resources.mem|memory_gb` / `resources`，
  对应控件下出现 `data-role="field-error" data-field="<group>"` 可见文案；
  原始 `field`/`rule`/`code` 也写入 `data-role="preflight-reason"` 的
  `data-field`/`data-code` 与 `data-acp-preflight-error-code`。
- FastAPI 422 列表 `{detail:[{loc,msg,type}]}`：`loc` 去掉首段 `body` 后作为字段路径。
- 纯文本 `detail`/传输错误：显示在预检状态与 reasons 中（`next.s2scan.preflight_error`）。
- 不可行（HTTP 200 但 `feasible !== true`）：`data-acp-preflight-state="error"`，
  `data-acp-preflight-status="infeasible"`，`reasons[]` 逐条显示（带字段前缀），
  `advisories[]` 单独显示；不产生 digest。
- 所有失败/过期都会让 `data-acp-step-valid="false"`，即“invalid/stale preflight blocks”。

### 6.4 预检本身不提交

- 预检的唯一请求是 `/preflight`；测试与源码 pin 均断言：预检请求头只有 `Content-Type`
  （无 `Idempotency-Key`），预检调用不产生 `/jobs` 请求。
- 提交是 review 面板内的独立动作（先打开 A-08 检查弹窗，确认后才发 `/jobs`）。

## 7. A-06 提交与回执

### 7.1 提交请求体（冻结计划 + 指纹）

`POST /api/v1/jobs` 的 body = §6.1 的冻结预检请求体 + `preflight_fingerprint`：

```json
{
  "schema_version": 1,
  "workflow": "optimize",
  "input": { "source_type": "xyz_text", "source": "<XYZ>", "charge": 0, "multiplicity": 1 },
  "method": { "schema_id": "dft_optimize", "levels": { "optimize": { "engine": "orca", "functional": "wB97M-V", "basis": "def2-TZVPP" } } },
  "resources": { "nproc": 6, "mem": "24576MB" },
  "preflight_fingerprint": "sha256:<A-05 digest>"
}
```

- `preflight_fingerprint` 是 `V1JobCreateRequest` 已声明的控制字段；服务端 `create_job`
  会用它和 `_plan_digest(normalized_submit)` 比较，不一致 → `400 preflight_stale`。
  `_plan_digest` 只覆盖 workflow/input/method/resources，所以“预检 body + fingerprint”
  与预检摘要一致。
- body 只来自冻结副本：`buildSubmitBody(state.preflight.request, digest)` 先深拷贝再附加
  fingerprint，从不修改缓存的请求体；没有 `plan_digest` / `idempotency_key` / `name`
  等额外字段（`plan_digest` 是 research 字段，legacy simple 提交不带）。
- **没有直接 fetch**：`call(ENDPOINTS.jobs, …)` 只经 `state.client.request`。

### 7.2 Idempotency-Key（一次计划一把钥匙）

- 头：`Idempotency-Key: next-wizard-<uuid 或 timestamp+random>`（服务端 pattern
  `^[A-Za-z0-9._:-]{8,128}$`）。
- 生成时机：第一次为某个计划确认提交时；钥匙绑定 **计划身份 `planIdentity`**
  （`state.submit.keyPlan`），不是绑定 digest 字符串。同一计划重试（400/409/422/5xx/
  传输失败）**复用同一把钥匙**，因此“回包丢失后的重试”变成服务端幂等重放，而不是第二个任务。
- 计划改变（结构/电荷/多重度/工作流/方法/资源）→ `planIdentity` 变化 → 下一次确认生成新钥匙。
- 已拿到回执的同一计划：`submit.receiptKey === planKey`，提交按钮禁用；即使强行触发确认，
  `confirmSubmit()` 直接返回既有回执，**不再发请求**（“重复提交不产生第二任务”）。
- 双击/连点：`state.submit.status === "loading"` 时第二次确认直接返回；真实浏览器里
  confirm 按钮同时 `disabled`。

### 7.3 回执

成功（HTTP 201）后消费：

| 响应字段 | 用途 |
|---|---|
| `job_id` | 回执主键；`data-acp-receipt-job` 与回执行文本；`commands.openTask` 的 `jobId` |
| `status` | 回执行 `任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试`（`next.config.receipt_body`） |
| `attempt` | 同上；默认 1（服务端 `V1JobCreatedResponse.attempt` 默认值） |
| `plan_digest` | 回执摘要行（`next.config.receipt_digest`）；服务端未返回时隐藏，不补造 |
| `idempotent_replay` | 显示 `next.s2scan.receipt_replay`（“幂等重放：返回的是同一任务的既有回执。”） |

- 回执块：`data-role="receipt"`、`data-acp-receipt-state="idle|submitted|error"`；
  成功时 `data-acp-submit-state="submitted"`、`data-acp-receipt-job=<job_id>`。
- **成功导航只走 `commands.openTask`**：`applySubmitReceipt()` 渲染后立即调用一次
  `openReceiptTask()`；回执里的 `data-action="open-task"` 按钮走同一个函数。
  `commands` 缺失时按钮隐藏、不自动导航，回执仍保留。
- 没有回执（`job_id` 缺失）的 2xx 视为 **unconfirmed**：`data-acp-submit-state="error"`，
  文案 `next.capabilityForm.submit.unconfirmed`，绝不显示成功。

### 7.4 错误映射（400 / 409 / 422 / 5xx / 传输）

`mapSubmitError(error)` 读取 `err.status` + `err.body.detail`（应用信封
`{code,message,params:{field,rule}}`、FastAPI 列表或纯文本），产出可读文案与稳定分支：

| 条件 | 分支键 | 机器属性 |
|---|---|---|
| 无 HTTP 状态（传输失败） | `next.capabilityForm.submit.unconfirmed`（结果未知，可重试） | `data-acp-submit-error-status=""` |
| `400` + `code=preflight_stale` | `next.config.plan_stale`；同时把预检置为 `stale`（清 digest/frozen body） | `…error-code=preflight_stale` |
| `400` 其他 | `next.config.submit_failed` | 同上 |
| `401/403` | `next.config.forbidden` | 状态码原样 |
| `409` + `code=idempotency_in_progress` | `next.capabilityForm.code.submit_busy`（可重试） | `…error-code=idempotency_in_progress` |
| `409` 其他（`idempotency_conflict` 等） | `next.capabilityForm.submit.conflict` | 同上 |
| `422` | `next.capabilityForm.code.validation_failed` + 服务端 message | `…error-field` / `…error-rule` |
| `>=500` | `next.config.submit_failed`（可重试） | 状态码原样 |

- 根属性：`data-acp-submit-state="error"`、`data-acp-submit-error-status`、
  `data-acp-submit-error-code`、`data-acp-submit-error-field`、`data-acp-submit-error-rule`。
- 回执块 `data-acp-receipt-state="error"`，`data-role="receipt-body"` 是可读文案，
  `data-role="receipt-digest"` 显示 `HTTP <status> · <code> · <field> · <rule>`（可得的片段）。
- **失败不显示成功回执、不导航、不自动重试**；钥匙保留给用户重试。
- 409 `idempotency_conflict` 不换钥匙（换钥匙可能造出第二个任务）；需重新预检/核对后再操作。

## 8. A-08 提交检查弹窗（submit check）

### 8.1 交互

- review 面板的 `data-action="submit"`（`next.config.submit`）只**打开**检查弹窗，
  不直接发请求。
- 弹窗 `data-role="submit-check"`（`role="dialog"`、`aria-modal="true"`、`hidden` 关闭）：
  摘要（工作流/方法/结构/电子状态/资源/预检指纹）+ 阻塞项 + 警告 + 取消/确认提交。
- `确认提交`（`data-action="submit-check-confirm"`）调用 `confirmSubmit()`；`取消`
  （`data-action="submit-check-cancel"`）只关闭弹窗，**零请求**。
- 有阻塞项时 confirm `disabled`，且 `confirmSubmit()` 自身重新计算检查（即使强行触发
  click 也不发请求）——**blocked submit has no request**。

### 8.2 阻塞项 vs 警告（no false positives）

`submitCheck(input)` 只把真正会阻止正确提交的条件列为阻塞项：

| code | 触发 |
|---|---|
| `workflow_required` / `schema_missing` | 无选中工作流 / 方法目录缺 schema |
| `structure_required` / `structure_loading` / `structure_error` | 结构未就绪（按状态给原因） |
| `charge_invalid` / `multiplicity_invalid` | A-03 `validateElectronic` 的错误 |
| `resources_loading` / `resources_error` | 资源权威仍在载入 / 读取失败 |
| `resources_invalid` | A-04 `validateResources` 的错误（非法格式/超上限） |
| `preflight_required` | 尚未预检（`idle`/空） |
| `preflight_busy` | 预检仍在 loading |
| `preflight_blocked` | 本地校验失败导致预检未发送 |
| `preflight_infeasible` | 服务端 `feasible:false`，每个 `reason` 一条（带 `field`） |
| `preflight_error` | 预检请求失败（400/422/传输） |
| `preflight_stale` | 预检成功但计划已变 / fingerprint 已作废 |

- **警告**只来自预检 `advisories`（`normalizeAdvisories`）：显示在
  `data-role="submit-check-warning"`，永不参与 `ok` 判定。
- 不算阻塞（显式不误报）：空资源（`""` = 服务端默认）、服务端未发布资源默认值、
  队列投影 `empty/error`（只读、不提交队列字段）、奇偶不可校验（未知元素/不可解析公式）、
  预检 advisory、`project_id` 缺失。
- 弹窗载体：`data-acp-check-state="ok|blocked"`；阻塞行 `data-role="submit-check-blocker"`
  + `data-code`/`data-field`；根 `data-acp-submit-check="ok|blocked"`、
  `data-acp-can-submit="true|false"`。

## 9. A-09 草稿（自动保存 / 恢复横幅 / 丢弃 / 409 / 提交后删除）

### 9.1 范围与持久化文档

- 唯一持久化通道：`src/acp/api/drafts.py` 的 `/api/v1/drafts`（端点形状见 §2）；模块不新增
  路由、不写文件、不使用 `localStorage`。
- 自动保存的 snapshot 只包含**展示/输入状态**：

  | 键 | 内容 |
  |---|---|
  | `kind` / `version` | `"task-wizard"` / `1`（归属判定；其它 kind/version 一律拒绝） |
  | `step` | 当前步骤（0..2；恢复时截到输入步，见 §9.4） |
  | `workflow_id` | 目录工作流 id（目录键，不是 task ref） |
  | `method` | `{profile_id, levels:{<levelId>:{<field>: value}}}`（目录投影后的字段值） |
  | `input` | `{mode, smiles, text, file_name, charge, multiplicity}`（原始文本与原始电荷/多重度） |
  | `resources` | `{cores, memory}`（原始输入串） |
  | `scan_atoms` | 操作者输入的 0 基扫描原子串 |
  | `payload` | 冻结请求 textarea 原文 |

- **绝不进入 snapshot**（写死在纯函数 `draftSnapshot` 与测试里）：
  - task ref：`source_job_id` / `source_product_id` / `source_mode_index`、回执 `job_id`；
  - 控制/幂等：`Idempotency-Key`、`preflight_fingerprint`、`plan_digest`；
  - 计划状态：冻结的 `/preflight` 请求体、reasons/advisories、`submit` 回执；
  - 服务端派生数据：已解析的 `structure`（XYZ/formula/atom_count）与任何凭据字段。
    因此恢复草稿后结构停在 `empty`，必须重新解析（§9.4）。
- **不扫描旧草稿**：挂载时只读 `GET /drafts` 的第一条（服务端按 `updated_at` 倒序）再读
  `GET /drafts/{id}`；`snapshot.kind !== "task-wizard"` 时保持“无草稿”，**不改写也不删除**
  经典向导的旧草稿。没有第二份列表缓存。
- `name` = `task-wizard · <目录工作流标签>`；`project_id` 取上下文快照（缺失为空串）。

### 9.2 自动保存（防抖，非每次击键）

- 触发点：用户实际编辑（SMILES/粘贴文本输入、文件选择、输入模式切换、电荷/多重度、
  核数/内存、方法字段、方法 profile、工作流切换、扫描原子、冻结 payload）。
  程序性状态变化（目录装载、结构解析回填、恢复）**不**触发保存。
- `scheduleDraftSave()` 取消旧定时器再挂一个 `draftDebounceMs`（默认 1200 ms，可由注入
  `deps.draftDebounceMs` 覆盖）定时器；防抖期内多次击键只产生**一个**写请求。
- 第一次 POST `/drafts`（无 `expected_revision`）；拿到 `revision` 后同一行 **PUT**
  带 `expected_revision`。响应 `draft_id` / `revision` / `updated_at` 写回
  `state.draft.meta`。
- 同一 snapshot 与 `lastSaved` 相同则跳过请求（只把状态置为 `saved`）；保存中再次编辑
  置 `queued`，当前写成功后再补一次（单飞行写，不并发 PUT）。
- 保存失败：`error` 状态 + `next.config.draft_error` 文案，下一次编辑可重试；
  不自动重试循环，不清空本地输入。
- 忙面：草稿请求在飞时 `data-acp-busy="draft"`、`data-acp-draft-busy="true"`，
  四个动作按钮 `disabled`。

### 9.3 恢复横幅与显式恢复

- 挂载时 `data-acp-draft-state=loading`（横幅隐藏）→ 读到本模块草稿后 `ready` +
  `data-acp-draft-status=found`，横幅显示 `next.config.draft_found`（`{when}` =
  服务端 `updated_at` 截成 `YYYY-MM-DD HH:MM`，不用本地时钟编造）+ `恢复` / `丢弃`。
- `恢复` 需要目录就绪：目录还在 loading / error 时点击只置 `restoreQueued`，
  目录就绪后自动应用一次（不重复请求、不半应用）；目录失败保持 `found`。
- 恢复内容（§9.4）来自 `normalizeDraftSnapshot` 的白名单清洗；恢复本身**零请求**。
- 恢复成功后横幅 `data-acp-draft-status=restored`（`next.wizard.local.draft_restored`），
  `lastSaved` 同步为恢复的 snapshot，避免下一次渲染立刻回写。

### 9.4 恢复的字段与不恢复的字段

| 恢复 | 不恢复（显式） |
|---|---|
| 工作流（`findWorkflow`；目录中不存在则报 `next.tasks.action_failed` 的“恢复失败”，不改当前配置） | 已解析结构（`structure`）→ 保持 `empty`，需重新解析 |
| 方法 profile + 各 level 字段值（按目录 `type` 还原 bool / multi / 文本） | 预检 digest / 冻结计划 / 提交回执 |
| 输入模式、SMILES / 粘贴文本 / 文件名、原始电荷与多重度 | 源任务绑定（`source_job_id` / product / mode index） |
| 资源原始输入（`touched=true`，不被服务端默认值覆盖） | `Idempotency-Key` |
| 扫描原子、冻结 payload 原文 | 步骤 review：`step` 截到 `≤1`（review 需要新预检） |
| 步骤（0 或输入步） | 目录/队列缓存（仍由 A-01/A-04 各自从服务端读取） |

- 恢复的 select 值若已不在目录选项中：不静默改写，按 A-07 本地校验显示
  `method_invalid_option` 类字段错误（恢复 ≠ 合法化）。

### 9.5 显式丢弃与提交后删除

- `丢弃`（`data-action="draft-discard"`）：DELETE 当前行；成功 `status=none`、横幅隐藏；
  404 视为已删除（同一结果）；其它失败保留行元数据并显示
  `next.tasks.action_failed`（`{action}=丢弃`）+ `data-acp-draft-notice="delete"`，
  按钮保留用于重试。
- 提交成功（A-06 回执渲染 + `openTask` 之后）自动删除该草稿：
  - 先取消在飞的防抖；
  - 若草稿写请求仍在飞：排队，待写结束后删除（防止提交后遗留孤儿草稿）；
  - DELETE 成功/404：`status=none`、横幅隐藏；
  - **删除失败非致命**：回执、`data-acp-submit-state=submitted`、`data-acp-receipt-job`
    与 `commands.openTask` 全部保持不变，只显示可重试的删除失败提示；
  - 若草稿处于 409 `conflict`：**不删除**（可能是另一窗口的新版本），只显示冲突提示。

### 9.6 409 `revision_conflict`

- PUT 旧 `expected_revision` → 服务端 `409 {detail:"revision_conflict"}`；
  模块置 `data-acp-draft-state=conflict`、`data-acp-draft-status=conflict`，
  显示 `next.config.draft_conflict`，并**停止自动保存**（后续编辑不再排定时器/发 PUT），
  直到用户显式解决：
  - `载入最新`（`data-action="draft-reload"`）：GET `/drafts/{id}` 取最新 revision + snapshot，
    套用同一恢复路径（`status=restored`，`lastSaved` 更新），之后编辑以新 revision 为 CAS 基线；
  - `另存为新草稿`（`data-action="draft-fork"`）：POST 一条新草稿（不带 `expected_revision`），
    采用新行元数据并清除冲突；**不删除**另一窗口的草稿；
  - `丢弃`：显式删除服务端行（危险操作，由用户自行确认语义）。
- 409 之外：404（行消失）→ `error` + 清空 meta（下一次编辑重新 POST，不静默重建）；
  5xx/传输失败 → `error` + 可重试；422（缺 `expected_revision` / snapshot 非对象）也是显式错误。

### 9.7 四态、守卫与 dispose

| 载体 | 值 |
|---|---|
| 根/横幅 `data-acp-draft-state` | `empty` / `loading` / `ready` / `error` / `conflict` |
| 根/横幅 `data-acp-draft-status` | `idle` / `loading` / `none` / `found` / `saving` / `deleting` / `saved` / `restored` / `conflict` / `error` |
| 根/横幅 `data-acp-draft-busy` | `true` / `false` |
| 横幅 `data-acp-draft-notice` | `""` / `lookup` / `restore` / `delete` |
| 根 `data-acp-busy` | 草稿请求在飞时为 `draft`（submit/resources/queue/preflight 优先） |

- 每个异步结果带 `generation` + `draft.token`（lookup / delete / reload）或
  `draft.saveToken`（save / fork）；dispose、remount 或更新的请求后到达一律丢弃。
- `dispose()` 取消防抖定时器、作废 token；`suspend()` 期间保存完成只写状态不重绘，
  `resume()` 补画横幅。
- 草稿查询失败（`/drafts` 不可达、503 等）是**非阻塞**错误：`data-acp-draft-state=error` +
  `next.nodes.section_error`（`{section}=草稿`）可见提示；目录/结构/预检/提交照常可用。

## 10. 四态与异步守卫

| 面 | 状态载体 | 值 |
|---|---|---|
| 模块整体（目录生命周期） | 根 `data-acp-module-state` | `loading` / `ready` / `error` / `empty` |
| 分子输入 | `data-step-panel="input"` 的 `data-acp-structure-state` | `empty` / `loading` / `ready` / `error` |
| 电子状态（A-03） | 根与电子状态块的 `data-acp-electronic-state` | `empty` / `ready` / `error` |
| 资源默认值（A-04） | 根与资源块的 `data-acp-resource-state` | `empty` / `loading` / `ready` / `error` |
| 队列投影（A-04） | 根与队列块的 `data-acp-queue-state` | `empty` / `loading` / `ready` / `error` |
| 工作流输入契约（A-07） | 根、输入面板与 workflow-input 块的 `data-acp-contract-state` | `idle` / `loading` / `ready` / `error` |
| 输入 adapter（A-07） | 根与输入面板 `data-acp-input-adapter` | `structure` / `scan` / `batch` / `source` / `payload` / `loading` / `error` |
| 方法投影模式（A-07） | 根与方法容器 `data-acp-method-projection` | `minimal` / `extended` |
| 方法 schema 支持 | 根/工作流错误/方法容器 `data-acp-method-support` | `ok` / `schema_missing` / `schema_empty` |
| 预检（A-05） | 根与预检块的 `data-acp-preflight-state` | `idle` / `loading` / `ready` / `error` |
| 预检有效/过期 | 根 `data-acp-preflight-valid` / `data-acp-preflight-stale` | `true` / `false` |
| 提交（A-06） | 根 `data-acp-submit-state` | `idle` / `loading` / `submitted` / `error` |
| 提交检查（A-08） | 根 `data-acp-submit-check`；弹窗 `data-acp-check-state` | `ok` / `blocked` |
| 回执 | `data-role="receipt"` 的 `data-acp-receipt-state` | `idle` / `submitted` / `error` |
| 草稿（A-09） | 根与横幅 `data-acp-draft-state` / `data-acp-draft-status` | §9.7：`empty`/`loading`/`ready`/`error`/`conflict` + 细分状态 |
| 忙碌面 | 根 `data-acp-busy` | `none` / `structure` / `submit` / `resources` / `queue` / `preflight` / `draft` |

- 迟到结果：对应 `token` 或 `generation` 变化后到达的结果一律丢弃，不写 DOM、不发后续请求；
  迟到的提交回执也不会调用 `openTask`；迟到的 workflow contract 也不会覆盖当前工作流；
  迟到的草稿 lookup / save / delete / reload 也不会改写横幅或清理草稿。
- `dispose()` 作废全部 token（目录/输入/资源/队列/契约/预检/提交/草稿）并清空 DOM，
  关闭弹窗、取消草稿防抖定时器；`suspend()` 冻结重绘，`resume()` 补一次 `renderAll()`；
  重新 `mount()` 重新加载目录并重新做一次草稿 lookup，
  清空结构/资源/契约/extras/预检/提交/草稿状态。

## 11. i18n 键策略（重点）

本批次**没有新增 `next.wizard.*` 私有键**：`frontend/js/shell/i18n.js`（A-61 全键批次）
不在本任务的 4 个文件边界内，而 `acp2_i18n_new_modules.test.js` 会扫描模块源码/`var ZH`
并要求每个键同时存在于 zh-CN 与 en-US 两个 shell 字典。因此 A-03…A-09 的文案
**复用已落库的共享键**（运行时 `t()` 先查 shell 字典，测试里回落到模块 `ZH` 副本）：

| 用途 | 复用的既有键 |
|---|---|
| 电荷 / 自旋多重度 | `next.config.charge` · `next.config.multiplicity` |
| 电荷/多重度整数与奇偶错误 | `next.config.error.charge_integer` · `next.config.error.multiplicity_integer` · `next.config.error.multiplicity_parity` |
| 奇偶通过 / 不可校验 | `next.s2scan.parity_ok` · `next.s2scan.parity_unverifiable` |
| 资源区块标题 / 核数 / 内存 | `next.approval.plan.resources` · `next.config.nproc` · `next.config.mem` |
| 内存格式错误 | `next.capabilityForm.code.ui_value_format` |
| 队列标题 / 空节点 | `next.nodes.meta_queue` · `next.nodes.not_configured` |
| 资源/队列读取失败 | `next.nodes.section_error` |
| 重试 | `next.config.retry` |
| 预检按钮/状态 | `next.config.validate` · `next.config.preflighting` · `next.config.preflight_retry` · `next.config.preflight_failed` · `next.s2scan.preflight_idle` · `next.s2scan.preflight_ok` · `next.s2scan.preflight_error` · `next.s2scan.preflight_blocked` · `next.s2scan.preflight_edited` · `next.s2scan.preflight_failed` · `next.capabilityForm.preflight.stale` · `next.capabilityForm.preflight_fingerprint` · `next.s2scan.advisories` |
| 计划摘要 | `next.config.receipt_digest`（`{digest}`） |
| A-08 弹窗标题/确认/取消 | `next.config.review` · `next.config.submit` · `next.tasks.dialog.cancel` · `next.config.submit_blocked` · `next.config.preflight_ok` |
| A-06 提交/回执 | `next.config.submitting` · `next.config.receipt` · `next.config.receipt_body` · `next.config.open_task` · `next.s2scan.receipt_replay` |
| A-06 错误映射 | `next.config.submit_failed` · `next.config.plan_stale` · `next.config.forbidden` · `next.capabilityForm.submit.unconfirmed` · `next.capabilityForm.submit.conflict` · `next.capabilityForm.code.validation_failed` · `next.capabilityForm.code.submit_busy` |
| A-07 字段标签 / 方法空态 | 字段标签来自 `field_definitions.label(_zh)`（服务端目录）；`next.wizard.method_empty` · `next.wizard.level_engine` · `next.wizard.review_missing` · `next.wizard.err.schema_missing` |
| A-09 草稿动作 / 状态 | `next.config.draft_found`（`{when}`）· `next.config.draft_restore` · `next.config.draft_discard` · `next.config.draft_saving` · `next.config.draft_saved` · `next.config.draft_conflict` · `next.config.draft_reload` · `next.config.draft_fork` · `next.config.draft_error`（`{message}`） |
| A-09 草稿错误 | `next.tasks.action_failed`（`{action}`+`{message}`，丢弃/恢复失败）· `next.nodes.section_error`（`{section}`=草稿）· `next.approval.status.draft` |

**LOCAL 中的 zh-only 文案**（`next.wizard.local.*` 已在 shell 预留 zh/en 两语；
运行时仍走 `lt()`，A-61 批次可继续迁移为 `next.wizard.*` 键）：

- `electronic_title`：电子状态（电荷 / 自旋多重度）
- `charge_too_high`：电荷过高：没有剩余电子。
- `multiplicity_too_high`：自旋多重度 {multiplicity} 超过当前电子数允许的上限 {limit}。
- `resources_loading`：正在载入资源默认值…
- `resources_empty`：服务端没有发布资源默认值；留空表示使用服务端默认值。
- `cores_too_many` / `memory_too_large`：超过服务端上限（带实际上限值）。
- `memory_format`：内存需要带单位：例如 8GB、512MB、2T。
- `queue_hint` / `queue_empty`：队列只读说明 / 无队列。
- **A-07 新增 LOCAL**：`workflow_input_title` / `workflow_input_hint`、`contract_loading` /
  `contract_error` / `contract_not_provided` / `contract_coverage` / `contract_required` /
  `contract_retry`、`payload_label` / `payload_hint` / `payload_placeholder` /
  `payload_required` / `payload_invalid` / `payload_summary`、`source_job` /
  `source_job_placeholder` / `source_product` / `source_product_placeholder` /
  `source_mode_index` / `source_mode_hint` / `source_required` / `source_mode_invalid` /
  `source_summary`、`scan_atoms` / `scan_atoms_placeholder` / `scan_atoms_hint` /
  `scan_atoms_required` / `scan_atoms_invalid` / `scan_atoms_out_of_range`、`batch_note`、
  `method_schema_empty`、`method_invalid_number` / `method_invalid_integer` /
  `method_out_of_range` / `method_invalid_option`、`field_not_consumed`。
  这些键**不进入** shell 字典（文件边界外），由 `lt()` 回落；A-61 若迁移可整批替换。
- **A-09 新增 LOCAL**：`draft_restored`（草稿已恢复）、`draft_deleting`（正在删除草稿…）。
  草稿其余文案全部复用上表的共享 zh/en 键（含 `next.tasks.action_failed` 与
  `next.nodes.section_error`），因此英文下只有这两条仍是中文。

（A-05 的 `scope_note` / `preflight_digest` 已随 A-06 落地删除，shell 中预留键保留。）

## 12. 测试与证据

命令：

```
"C:\Users\彭扬超\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe" \
  frontend/js/module_tests/run_all.js acp2_task_wizard
```

**结果：66 passed, 0 failed（exit 0）**。A-01…A-08 的 55 项全部保留通过；
A-09 新增 11 项：

45. `A-07`：15 个 active 目录条目全部出现、retired/hidden 永不出现；每个条目的 projection
    mode / adapter / contract state 跟随目录与 schema；简单四个仍只发两个目录请求；
    contract 只为 extended 选中项读取，且从不读取 retired；
46. `A-07`：schema 驱动控件（scan 的 select/bool/int、NMR 的 multi checkboxes、Confsearch 的
    profile level 子集、Batch 的 server-side module 跳过、冻结 payload 无 method 字段、
    语言切换重绘）；
47. `A-07`：`scan` 预检保留 boolean `false` / integer，`float` 输出十进制字符串；
    `input.scan_coordinates` 来自显式原子与 schema 起止；提交回执真实；
48. `A-07`：PES 生成 `input.coordinate` + `input.protocol`（driver/optimizer/single_point），
    `method.levels` 被禁止；提交回执；
49. `A-07`：`irc` 绑定 source job/product 且 method levels 由契约允许；`tsmode` 省略 method，
    schema `max_steps` / `recalc_hess` 映射到 input consumer；两者均可提交；
50. `A-07`：冻结 payload 空/非法 JSON 本地阻塞（零 `/preflight`），合法 JSON 原文进入
    `input.path_request` 且无 method；提交回执；
51. `A-07`：contract loading/error 是显式 `data-acp-contract-state` 阻塞；可见错误 + 重试恢复；
52. `A-07`：切换工作流 / dispose 后迟到 contract 不覆盖当前状态（token/generation 守卫）；
53. `A-07`：**15/15** active 工作流逐条走完 A-05 预检 + A-06 提交，body workflow 与
    `preflight_fingerprint` 对应，回执 job id 可见；
54. `A-07`：缺失 / 空 method schema 是显式本地阻塞（可见原因、无 method 控件、无
    `/preflight` / `/jobs` / contract 请求），绝不渲染 silent generic form；
55. `A-07` internals：15 schema 的 projection/support/默认值、五类 adapter 判别、
    `parseScanAtoms` 的 required/count/integer/distinct/range、min/option 本地校验、
    tsmode request 构建与 missing source 返回 `request:null`。

A-09（56–66）：

56. `A-09`：挂载只做一次草稿 lookup（`GET /drafts`，无草稿时无第二条 GET）；无草稿 →
    `data-acp-draft-state=empty` + 横幅隐藏 + 零写请求；lookup 失败是**非阻塞**可见错误；
57. `A-09`：自动保存防抖——一串击键只挂一个定时器、定时器到点只有一个 POST；snapshot 形状
    （kind/version/step/workflow_id/method/input/resources/scan_atoms/payload）与排除项；
    第二次编辑变成 PUT 且带 `expected_revision`；
58. `A-09` internals：`draftSnapshot` 的排除项（task ref / 幂等 / preflight / structure /
    token）、`normalizeDraftSnapshot` 的 kind+version 拒绝与白名单清洗、`draftBody` 的
    CAS 字段、`draftPath` 编码、`draftWhen`、`mapDraftError` 的 409/404/5xx 分支；
59. `A-09`：有草稿时挂载显示恢复横幅（服务端 `updated_at`、恢复/丢弃可见、reload/fork 隐藏）；
    点恢复后工作流/profile/方法字段/SMILES/电荷多重度/资源/步骤被还原，结构保持 `empty`、
    预检保持 `idle`，恢复零请求零写；
60. `A-09`：目录未就绪时点恢复只排队（不半应用），目录就绪后自动应用一次；
61. `A-09`：显式丢弃 DELETE 一次并隐藏横幅；DELETE 500 时保留行元数据、显示
    `data-acp-draft-notice="delete"` 的“丢弃失败”并保留可重试按钮，重试成功后清空；
62. `A-09`：PUT 409 `revision_conflict` → conflict 状态、停止自动保存（不再排定时器/发 PUT），
    `载入最新` 采用服务端 revision 后继续 CAS，`另存为新草稿` POST 新行且不删除旧行；
63. `A-09`：提交成功后 DELETE 草稿（且发生在 `/jobs` 之后），回执与 `openTask` 不变；
    若提交时写请求仍在飞，删除排队到写结束之后（不遗留孤儿草稿）；
64. `A-09`：提交后草稿删除失败**非致命**——`data-acp-submit-state=submitted`、回执 job id
    与 `submit-error-*` 不变，只有可重试提示；重试成功后回执保持；
65. `A-09`：dispose 取消防抖（定时器数归零、之后无写请求）、在飞 save 的迟到响应、
    迟到 lookup 都不能重建/改写 DOM；
66. `A-09`：草稿 chrome 的 `data-i18n` 键全部存在于 zh-CN 与 en-US 两个 shell 字典，
    `{when}` 已被格式化且文案为“发现保存的草稿（2026-10-31 09:15）”。

另跑 **`acp2_i18n_new_modules`：5 passed / 0 failed**（task-wizard 扫描键 148 条全部在
zh/en 字典中存在；新增 11 条 A-09 键），确认没有新增私有 `next.*` 键缺口。
**全量 `run_all.js`：1103 passed / 0 failed**（1 个既有 skip：`wizard_submit_payload`
需要 acp-importable Python；exit 0）。`acp2_theme_narrow`：6 passed / 0 failed。

**真实目录交叉验证（throwaway，未提交）**：用本机 Python 生成
`WORKFLOW_CATALOG` + `METHOD_CATALOG` + `workflow_contracts.describe_workflow` 的 15 条真实
数据，在 Node `vm` 中运行模块的 `projectMethod` / `projectInputAdapter` /
`buildWorkflowRequest`，再把请求体交给真实
`validate_request_fields` → `normalize_preflight` → `method_levels_to_cli_flags` →
`validate_workflow_submission` → `validate_scan_submission` →
`frozen_plan_definition` + `plan_digest`：**15/15 PASS**。验证中发现并修复了两个真实契约
问题：NMR profile basis `6-311G(d)` 不在 `basis_catalog`（profile 值作为当前 option 保留），
以及 canonical digest 拒绝 JSON float（float 字段改为十进制字符串）。

**未执行**：真实浏览器、真实 HTTP 服务、真实 `/api/capabilities`/`/nodes` 探测
（`/nodes` 冷缓存会触发 SSH 探测），真实 `POST /jobs` 提交，以及真实
`/api/v1/drafts` CRUD 与跨刷新恢复（本轮用 mock 草稿后端 + 注入定时器覆盖契约）。
因此“真实页面可点击走通 + 真实服务响应 + 真实任务回执 + 真实草稿跨刷新”仍为
**NOT_VERIFIED**，不计 PASS。

## 13. 接入步骤（本次文件边界外，未改动）

1. `frontend/next/index.html`：已由集成方加入本模块 CSS/script 两行（非本任务改动）。
2. `frontend/js/shell/recipes.js`：V7 装配时为 Execution 工作区选择槽位/默认可见性；
   本批未接默认 recipe，默认布局不会自动显示该面板，可由布局编辑器手动选用。
3. `frontend/js/shell/layout-manager.js`：`factory(hostApi)` 已把 `commands`
   （`window.ACPShell.commands`）注入模块 deps；A-06 的 `openTask` 直接可用。
   模块的 client 仍按 §1 的回退链从 `window.ACPClient.transport.request` 取。
4. `frontend/js/shell/i18n.js`：本批未改；§11 的 `LOCAL` 条目若需 en 化，须由 A-61 批次补键。
5. 浏览器用例：`acp2_next_entry.browser.js` 尚无 task-wizard 提交/草稿场景；本任务未执行浏览器套件。
6. A-09 草稿**无需额外装配**：`loadDraft`/`scheduleDraftSave` 都走 §1 的同一条注入 client；
   真实页面用 `window.ACPClient.transport.request` 即可，没有第二套初始化。
   草稿 API 需要 ACP 后端已挂载 `src/acp/api/drafts.py`（`/api/v1/drafts`，`create_task_drafts`
   表），否则挂载时只显示非阻塞的“草稿加载失败”。

## 14. 已知缺口 / 边界

- **A-07 已实现（15 个 active 全部可配/可预检/可提交）**：唯一 dynamic panel +
  workflow contract projection；无 workflow-id 白名单，retired 条目永不出现。真实目录的
  15/15 请求体已过服务端契约层（§12）。但 A-07 **不补齐科学输入本身**：
  - `casscf` / `nmr` 的 `workflow_contract` 目前是 `not_provided`；本模块用方法 schema +
    A-02 结构包络，NMR 没有谱图文件字段、CAS 的 `electronic_state` module 不渲染；
    预检/提交可达，但“完整 NMR/CASSCF 科学输入”仍属后续后端/表单契约。
  - `scan` / `PESsearch` 的原子序号由 `data-role="scan-atoms"` 手工输入（0 基），
    尚未接 3D 结构点选；模块不自动选点。
  - `BatchOptimize` 按调度器 `single_flat` 语义**一个任务一个结构**；不提供多 item 批次。
  - `XtbPathSearch` / `OrcaGradient` / `XtbConstrainedOpt` 只透传上游冻结 JSON，
    内层 schema 不在本模块投影，也不补默认值（契约文档明确由上游拥有）。
  - `irc` / `tsmode` 需要已存在的源任务；本模块只提供 Job ID/产物/模式序号文本输入，
    不做源任务选择器；服务端 source facts 仍会判定不可行。`irc.directions` 与
    `tsmode.entry_id` 等契约可选字段未单独渲染（服务端默认/解析路径继续生效）。
  - 预检体仍不含 `execution_mode` / `target_node` / `node_tags` / `walltime` / budget
    等目标与预算字段（沿用 A-01…A-06 范围），因此“目标节点不可行”类原因不来自本模块。
- **A-10 未实现**：“编辑草稿加载”属于 `job-edit` 模块；本模块只做新建任务草稿（A-09）。
- **A-09 草稿的已知边界**：
  - **结构不持久化**：snapshot 只存输入文本，不含已解析 `structure`；恢复后必须重新
    “解析 SMILES / 解析文本 / 选择文件”。文件模式只恢复文件名与读取到的文本，
    浏览器 `File` 对象本身无法持久化。
  - **只读最新一条**：列表摘要没有模块/kind 字段，挂载时只检查 `updated_at` 最新的
    一条；它是别的模块/经典向导写的草稿时，本模块不显示横幅、不改写不删除。
    更旧的本模块草稿不会被自动发现（没有草稿箱 UI；经典“草稿箱”仍由老页面提供）。
  - **源任务引用不跨刷新**：`source_job_id` / product / mode index 是 task ref，
    永不入草稿；`irc` / `tsmode` 恢复后需要重新填源任务。
  - **无跨窗口实时同步**：两个窗口同时编辑同一草稿时由 CAS 兜底（409），
    此时自动保存停止，需要用户显式“载入最新 / 另存为新草稿 / 丢弃”。
  - **删除失败只在选择器内提示**：提交后删除失败会保留横幅与可重试按钮，
    但不阻塞任务页导航；草稿会留在服务端直到重试成功或人工清理。
  - **大输入**：服务端 snapshot 上限 8 MiB；超大粘贴文本/文件/冻结 payload 会得到
    `413 draft_too_large`（显示为草稿保存失败），不影响预检/提交。
  - **i18n**：`draft_restored` / `draft_deleting` 是 zh-only `LOCAL`（shell 字典在
    文件边界外）；其余草稿文案已是共享 zh/en 键。
- **A-09 草稿与预检/提交无耦合**：草稿写失败不会阻塞预检或提交；预检/提交也不会写草稿。
- **`plan_digest` 回显**：`V1JobCreatedResponse.plan_digest` 来自 `req.plan_digest`
  （research 字段）；legacy simple 提交不发送该字段，因此真实回执常见 `plan_digest:null`，
  回执摘要行隐藏（不补造），预检指纹仍显示在预检块。
- **A-06 的 409 不换钥匙**：`idempotency_conflict` 意味着同钥匙 + 不同 payload；
  本模块按计划身份绑定钥匙，正常不会发生。为免“换钥匙造出第二任务”，失败后保留钥匙，
  用户需重新预检/核对（README §7.4）。
- **A-06 钥匙生命周期 = 模块实例**：Idempotency-Key 只活在当前挂载实例内（内存状态，
  **不入** A-09 草稿——它是控制字段，不是展示状态）。页面刷新后重新提交同一计划会生成
  新钥匙，服务端无法识别为同一意图，因此“刷新后防重复”**未实现**（需要后端提交幂等
  意图持久化；A-09 只恢复配置）。
- **`submission_id` 未消费**：`POST /jobs` 当前响应没有 `submission_id` 字段；回执消费
  `job_id/status/attempt/plan_digest`，若后续契约新增该字段需同批更新本模块。
- **预检体不含目标/预算字段**：A-01/A-02/A-03/A-04 的字段 + A-07 的 method/input 已在
  §6.1/§3.5 覆盖，但 `execution_mode`/`target_node`/`node_tags`/`walltime`/budget
  等目标与预算字段未接入，因此服务端“目标节点不可行”类原因不会来自本模块；
  `feasible:false` 仍可能来自 input/method/资源检查。
- **队列只读**：V1 资源策略没有客户端 `queue` 字段；本模块显示节点目录队列但不发送，
  也不把队列加载失败当成提交阻塞。
- **内存比服务端更严格**：服务端接受无单位数字（按 GB），本模块要求显式单位；
  这是有意的 UI 决策，已在 §5.2 记录。
- **资源默认值非“推荐值”**：顺序是 available → pool → limits，只保证“来自服务端”，
  不是调度器推荐配置；超过 available 的请求由服务端准入排队/拒绝（预检会体现）。
- **无真实环境验证**：真实浏览器挂载、真实 `/api/capabilities`/`/nodes` 探测、
  真实 RDKit resolve/embed/parse 响应、真实 `POST /jobs` 回执均 **NOT_VERIFIED**。
- **未改共享文件**：`frontend/next/index.html`、`frontend/js/shell/*`、
  `frontend/js/shell/recipes.js` 与其它模块均未被本任务修改；本任务只改
  `module.js` / `acp2_task_wizard.test.js` / `task-wizard.css` / 本 README。没有 git commit。

## 15. 纯函数测试缝（`_internals`）

`MODULE_ID`、`ENDPOINTS`、`STEP_IDS`、`INPUT_MODES`、`PROJECTED_FIELDS`、
`MINIMAL_SCHEMA_FIELDS`、`ADAPTERS`、`METHOD_EMIT`、`LOCAL`、
`filterWorkflows(catalogBody)`、`findWorkflow(entries, id)`、`pickDefaultWorkflow(entries)`、
`schemaFor(methodCatalog, schemaId)`、`fieldOptions(methodCatalog, field, engine)`、
`fieldControl(...)`、`isExtendedSchema(schema)`、`methodSupport(method)`、
`validateMethodSchema(method)`、`parseScanAtoms(raw, expected, atomCount)`、
`projectInputAdapter(entry, method, contractState)`、
`projectMethod(workflow, methodCatalog, options)`、`buildWorkflowRequest(input)`、
`buildPesProtocol(method, charge, multiplicity)`、`jsonObject(raw)`、
`structureFromAsset(asset, source, filename)`、
`structureFromResolve(resolveBody, embedBody)`、`structureSummaryText(structure)`、
`describeError(error)`、`parseFormula(formula)`、`electronCount(formula, charge)`、
`validateElectronic(chargeRaw, multiplicityRaw, structure)`、
`normalizeMemory(value)`、`validateResources(coresRaw, memoryRaw, limits)`、
`projectResourceDefaults(capabilitiesBody)`、`projectQueues(nodesBody)`、
`buildPreflightRequest(input)`（旧 seam：无 adapter 时保持 A-01 body；有 adapter 时委托
`buildWorkflowRequest`）、`preflightLevels(method)`、`planIdentity(request)`、
`parsePreflightError(error)`、`buildSubmitBody(request, fingerprint)`、
`normalizeReceipt(responseBody)`、`mapSubmitError(error)`、`submitCheck(input)`、
`newIdempotencyKey()`、`stableStringify(value)`。
A-09：`DRAFT_KIND`、`DRAFT_VERSION`、`DRAFT_DEBOUNCE_MS`、`draftPath(draftId)`、
`draftWhen(updatedAt)`、`draftSnapshot(input)`、`normalizeDraftSnapshot(raw)`、
`draftBody(snapshot, options)`、`mapDraftError(error)`。

测试用这些函数钉住目录过滤/默认值/投影/结构映射、A-07 的五类 adapter 与请求体、
schema 类型/选项/min-max、scan 原子解析、电子计数与奇偶矩阵、内存语法、
资源投影与预检体/身份/错误映射、提交体冻结、回执规整、提交错误分支与提交检查矩阵；
UI 测试再钉住事件、步骤状态、请求次数、四态属性、Idempotency-Key 复用与迟到响应/契约守卫。
A-09 的纯函数测试再钉住 snapshot 的展示/输入白名单与排除项、归属 kind/version 判定、
CAS body 形状与 `revision_conflict`/`draft_not_found`/5xx 分支；UI 测试钉住防抖、
恢复横幅/队列、丢弃/提交后删除、删除失败非致命与 dispose/迟到守卫。
