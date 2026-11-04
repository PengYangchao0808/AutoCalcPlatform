# molecule-viewer — ACP 2.0 实验入口结构查看模块（UI-02a）

- 文件：`frontend/js/modules/molecule-viewer/module.js`（classic script，无构建、无 ESM）
- 样式：`frontend/css/modules/molecule-viewer.css`（仅消费 `frontend/css/workbench/base.css` 变量）
- 测试：`frontend/js/module_tests/acp2_module_viewer.test.js`（`node frontend/js/module_tests/run_all.js acp2_module_viewer`）
- 注册名：`window.ACPNextModules["molecule-viewer"]`（唯一顶层名，符合 UI-00 契约草案 §注册表）

## 1. 模块契约（Shell 对接面）

```js
window.ACPNextModules["molecule-viewer"] = {
  descriptor: {
    id: "molecule-viewer",
    interfaceVersion: 1,
    dependencies: [],
    capabilities: { requires: [], provides: ["structure.render"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",       // P0 单活动实例（Shell 装配时强制）
    configSchemaVersion: 0,
    error: { titleKey: "next.viewer.errorTitle", messageKey: "next.viewer.errorMessage" }
  },
  factory: function (deps) { /* → ModuleInstance */ }
};
```

ModuleInstance：`mount(host, initial) → Promise<void>`、`updateContext(next)`、`resize()`、
`suspend()`、`resume()`、`dispose()`。

- `deps = { context: { getSnapshot, subscribe }, commands: { openTask, openEvidence }, fetch?, request? }`
  — 全部可注入可 mock；`request(path, opts)` 仅用于 A-47 资产保存
  （缺省回落 `deps.client.transport.request` → `window.ACPClient.transport.request`）。
  **P0 只读**：structureRef/taskRef 变化时更新自身，绝不回写 context。
- `mount` 返回 ≠ 数据就绪（3D 库可能仍在异步加载）；模块以自身 `data-acp-module-state` 表达
  `empty | loading | ready | error`。
- `mount` 失败（宿主非法 → reject TypeError；内部失败 → 清理自身资源后在 host 内渲染
  局部错误框 + 重试按钮，**不外泄**异常）。
- `dispose` 幂等且彻底：取消 context 订阅、销毁 framing（ResizeObserver/定时器）、
  `scene.dispose()`、移除自建 DOM、reset **本模块拥有**的 geometry store 状态，
  丢弃 A-46 本地编辑事务，并作废 A-47 进行中的保存结果（不写 store/回执）。
- `suspend()`：断开 framing 观察器 + `scene.setHidden(true)`；`resume()` 恢复并重新
  schedule framing（仅 ready 态）。模块无常驻渲染循环（静态结构，单次 render）。
- 迟到异步结果带 generation 守卫：dispose / A→B→A 切换后的迟到 fetch/解析结果
  整体作废，不写 DOM、不写 store。

## 2. structureRef 冻结形状（v0）

```js
// 本地任务文件（旧工作台 loadStructureFile 同源端点）
{ kind: "job-file", jobId: "20260823_001_Confsearch", path: "WORK/02_SEARCH/crest_best.xyz", frameIndex?: 0 }

// LSF 远程任务文件（旧工作台 loadRemoteStructureFile 同源端点）
{ kind: "job-remote-file", jobId: "…", path: "…", frameIndex?: 0 }

// 结构目录条目（A-33；geometry 端点，store 身份 = job:entry:<id>）
{ kind: "structure-entry", jobId: "…", entryId: "…", frameIndex?: 0 }

// 内联 XYZ（测试/向导预填等无网络路径）
{ kind: "inline-xyz", text: "2\n\nC 0 0 0\nO 1.2 0 0\n", frameIndex?: 0 }
```

- `frameIndex` 可选，默认 0，越界钳制到最后一帧；多帧 XYZ 按帧解析。
- **字符串桥接**：`structureRef` 若为字符串，模块尝试 `JSON.parse` 后按上表解释
  （兼容 UI-00 草案的 `structureRef?: string` 形状）；解析失败 → error 态并给出原因。
- 冻结依据（复用旧页面真实取数端点，不发明新端点）：
  - `job-file` → `GET {API_BASE}/jobs/{jobId}/files/{path}`（`workbench/features/molecule-view.js::loadStructureFile`，
    `API_BASE` 默认 `/api/v1`，与 `workbench/compat/shared-state.js` 一致；路径段逐段 `encodeURIComponent`）
  - `job-remote-file` → `GET {API_BASE}/jobs/{jobId}/remote-files/{path}/preview?mode=structure`
    （`molecule-view.js::loadRemoteStructureFile`；响应信封 `{content: string}`，也兼容直接文本/预解析 body）
  - `structure-entry` → `GET {API_BASE}/jobs/{jobId}/structure-viewer/entries/{entryId}/geometry`
    （目录条目的官方几何端点；不按 `geometry_ref` 猜路径）
- fetch 解析：注入 `deps.fetch` 优先，回落 `window.fetch`；接受 fetch `Response`
  （`ok/status/text()/json()`）与 `api()` 风格预解析 body（`{content}` 或裸文本）。

### 2.1 A-00 数据路径（2026-10-31）

模块**不再直接 fetch**：`loadRef` 经 `ACPView.source.create(...)` 取数，收到
`acp.view.v1` 归一化信封（`view_model.js`），再按 `objects[geometryRef].text`
解析 XYZ。`view_source.js` 保留上表端点的精确 URL 与响应形状；`?view=fixture`
（或 `localStorage["acp.view.source"]="fixture"`）在无选中任务时用确定性的
`structure` fixture 驱动 viewer，并渲染 `data-acp-fixture="true"` 徽标。
`warnings[]` 在模块底部以 `data-acp-warning-count` 显式展示；`source` 仍受
generation 守卫约束（A→B→A / dispose 的迟到结果不写 DOM / store）。

### 2.2 A-32 / A-33 结构目录与条目几何（2026-10-31）

绑定 `taskRef` 后，viewer 顶部出现“结构目录”开关；目录**懒加载**
`GET /api/v1/jobs/{jobId}/structure-viewer`，经适配层归一化为 `catalog`
（`groups` / `entries`），按分组渲染只读条目列表。点击条目：
1. 用 `{kind:"structure-entry", jobId, entryId}` 加载
   `GET …/structure-viewer/entries/{entryId}/geometry`；
2. 复用唯一 3D 栈渲染，store 身份为 `job:entry:<entryId>`（不按 `geometry_ref`
   猜路径）；3. 若 Shell 注入 `commands.openStructure`，同步 context 焦点。
`pending_fetch` 显示为 empty + warning，`unavailable` 显示为 error；目录本身
不写任务状态，不提交任何命令。fixture 模式同样提供 catalog 与条目几何。
外部 `structureRef`（例如 `energy-profile` 点击曲线发来的 `job-file`）会按
`geometryRef`/`entryId` 匹配并高亮对应目录条目，完成“点曲线定位条目”的联动。

### 2.3 A-34 样式与视图状态（2026-10-31）

- 样式预设与旧页 `STYLE_PRESETS` 一致：`ball-stick / stick / spacefill /
  wireframe / publication`；风格只改变当前 model token，不重建场景。
- 相机经 `scene.getView()/setView()` 保存/恢复；mouseup / wheel 后节流写入，
  dispose / suspend 兜底写入。
- 存储键 `acp-next-viewer-view-v1`，按 entry identity 保存 `{style, view}`；
  view 必须是 8 个有限数，最多保留 24 条；不写入任何 task/credential。
- “重置视角”删除该 entry 的 view 并重新走共享 framing；样式保留。

### 2.4 A-35 叠加与 RMSD（2026-10-31）

- 目录每个条目有“叠加”按钮；基准 A 必须是当前选中的目录条目。
- 数据来自 `GET /api/v1/jobs/{id}/structure-viewer/overlay?entry_a=&entry_b=`
  （映射/Kabsch 仅服务端计算）与 B 的 `structure-entry` 几何端点。
- B 作为第二个 model 以 legacy cyan/透明样式渲染；`max_displacement.i`
  在 A 上加黄色高亮；状态条显示 RMSD、mapping 来源与 mapped 原子数。
  client 不对 B 做二次刚体变换（与 legacy 行为一致），RMSD/Kabsch 仅由服务端计算。
- `ok:false`（`unproven`/失败）时根节点写
  `data-acp-overlay-measurements="blocked"`，并显示“映射未证实 · 测量已禁用”；
  后续测量 UI 必须读取该状态，禁止跨结构测量。
- “清除叠合”移除 B model、恢复 A 样式；切换结构/目录条目也会清除叠合。

### 2.5 A-36 IRC 帧播放（2026-10-31）

- viewer 提供“正向 / 反向 / 停止”控制；目录按需加载（无需先手动打开抽屉）。
- 帧集合来自 `catalog` 的 `irc_forward` / `irc_reverse` 分组，按
  `source.frame_index` 排序；每帧经 `structure-entry` 端点取 XYZ。
- 播放期间保留相机 `getView/setView`，逐帧替换当前 model（不新建场景）；
  默认 250ms/帧，测试可注入 `ircFrameMs`。
- 任务切换 / 目录重置 / suspend / dispose / 新结构加载都会停止播放并清 timer。
- 大轨迹抽样、slider/单步尚不在 A-36 范围内（后续 A-14/A-36 细化）。

### 2.6 A-37 窄屏单列（2026-10-31）

- `data-acp-viewer-narrow` / `data-acp-viewer-layout="single"` 在宽度 ≤720px 时设置；
  `resize()` 与 window resize 同步，宽屏恢复 `split`。
- 窄屏下目录变成底部 sheet（`left/right/bottom` 全宽、max-height 62%），
  样式条 / IRC 条 / 叠合条改为全宽单列；warning 上移避免和 overlay 条重叠。
- CSS 同时提供 `@media (max-width:720px)` fallback；单列只改布局，不改数据/命令。

### 2.7 A-42 振动频率（2026-10-31）

- 样式条“振动”打开抽屉；目录选中的 entry 作为振动数据身份。
- `GET /api/v1/jobs/{id}/structure-viewer/entries/{entryId}/vibrations` 经
  `ACPView.source.loadVibrations` 归一化为 `modes` / `vibration`。
- 列表排序：虚频（`imaginary` 或负频）最负优先，其余按频率升序；默认选择
  最负虚频（无虚频则第一个模式）；行显示原生 `mode_index`、频率、虚频 chip、
  IR 强度。
- `available:false` 或空 modes 只显示 reason（如“无振动模式数据”），不渲染
  任何假 mode 行；切换 entry/task 会清空抽屉。
- 箭头/动画属于 A-43，尚未接入。

### 2.8 A-43 振动箭头 / 动画（2026-10-31）

- `computeArrowEndpoints` / `computeDisplacedCoords` 纯函数逐原子生成箭头与位移；
  `scene.addArrow` 绘制、`removeAllShapes` 清理；虚频红色、其余蓝色。
- 箭头按钮是显示开关；`播放` 以 `requestAnimationFrame` 重建位移 model，
  保留相机，`停止` 恢复 equilibrium 模型并重画静态箭头。
- stop / task switch / entry switch / suspend / dispose 都取消 rAF 并清理
  shapes；测试断言停止后与切任务后没有迟到帧。
- 当前没有速度/振幅滑块；播放中不叠加箭头（只显示 displaced model）。

### 2.9 A-44 热化学摘要（2026-10-31）

- `catalog` 的 `ensemble_thermo` 经适配层映射为 `thermo`：总 Gibbs
  (kcal/mol / Hartree)、rank-1 weight、T·S_mix、population coverage、温度。
- 振动抽屉底部显示摘要；缺字段只跳过该行；整个 `thermo` 缺失时显示
  “无热化学数据”，根节点 `data-acp-thermo-state="empty"`，不补 0。
- 摘要与振动可用性独立：vibrations `available:false` 不影响已有热化学摘要。

### 2.10 A-45 测量：距离 / 角度 / 二面角（2026-10-31，只读）

- 只读测量抽屉（样式条“测量”开关）：类型按钮 `distance` / `angle` /
  `dihedral`，点类型即进入选取；`开始选取/取消选取`、`清除全部`、逐条
  `删除`；结果单位为 `Å` / `°`，**不提交任务、不写 geometry store、
  不修改任何坐标**。
- 点击原子走唯一 3D 栈的受限 model token：
  `modelToken.setClickable({}, true, cb)`（scene_instance.js 将回调包装为
  plain-data atom snapshot），不新建第二个 viewer，也无厂商库直呼。
- 纯函数（`_internals` 暴露，供测试/诊断）：`measurementDistance`、
  `measurementAngle`、`measurementDihedral`、`measurementValue`、
  `normalizeDihedral`、`formatMeasurementValue`。二面角采用
  geometry_store / FE-10 golden 固化的 IUPAC 投影约定
  `atan2((b1u × v)·w, v·w)`，输出归一化到 `(-180, 180]`（-180 → +180）；
  镜像构型符号相反，cis = 0°，trans = ±180°。共线/重合中心键、缺原子、
  参数个数不符一律返回 `null`。
- 几何来源是 viewer 已持有的 `currentFrame.atoms`（原始帧从不被修改）；A-46
  存在本地编辑时，测量与 overlay 改用“屏幕上显示的编辑几何”，保证数值与
  画面一致；measurement item 只保存 `{id,type,atoms,value}`。每次点击都按
  0-based `atom.index`（缺失时回退 `serial-1`）映射，越界点击报
  “无法定位点击的原子”，不会静默产生错误值。
- 选取进度与结果：面板状态行显示 `已选 n/arity：… · 继续点击原子 k`；
  已选原子用 `scene.addSphere` 高亮；结果行显示
  `距离 · C1—C2 · 1.000 Å` 等。线条/标签经 `scene.addCylinder` /
  `scene.addLabel` 绘制并登记 token，清除/删除/切换结构时逐个移除。
- 四个本地面板状态（根节点 `data-acp-measurement-state`）：
  `empty`（无结构或暂无测量）、`picking`、`ready`、`blocked`；附带
  `data-acp-measurement-count` / `data-acp-measurement-type`。
  显式本地空态/错误文案包括“暂无结构，先加载结构再测量”“暂无测量”
  “当前结构仅 N 个原子，无法测量…（需要 k 个原子）”“几何退化或坐标缺失…”
  “映射未证实 · 测量已禁用”（读取 A-35 的
  `data-acp-overlay-measurements="blocked"`）。
- 清除时机：结构加载/切换、任务切换、目录条目切换、IRC 播放开始、
  dispose 都清空测量与 overlay；IRC 帧渲染期间根节点
  `data-acp-measurement-state="blocked"`（显示帧不是 `currentFrame`
  几何），振动动画播放期间同理暂停选取。
- A-46（内坐标编辑/目标值应用/撤销）见 §2.11；A-47（`structure-assets` 保存）
  见 §2.12，只允许保存已提交编辑，绝不自动保存。

### 2.11 A-46 内坐标编辑与本地事务（2026-10-31）

- 测量行内新增“目标”输入（`data-measure-target`）与“应用”按钮（`data-measure-apply`），
  仅对 distance / angle / dihedral 生效；目标范围沿用冻结编辑器契约：
  距离 `0.4–5.0 Å`、角度 `1–179°`、二面角 `-180–180°`。NaN/Infinity/空值/越界、
  退化几何（零键长、三点共线、中心键投影为零）一律显式拒绝并显示本地文案，
  不产生预览或事务；目标值与当前值相同也显式提示、不建事务。
- 编辑约定（纯函数 `displaceGeometry` 即契约，便于审查与测试）：
  - 距离：**只平移第二个原子**，沿 A→B 单位轴移动 `目标 − 当前键长`，另一侧
    所有原子精确不动；
  - 角度：**只旋转第三个原子**，绕顶点（第二个原子）垂直于 A-B-C 平面的轴
    旋转 `目标 − 当前角度`；两条键长与整个 A-B 侧几何严格保持；
  - 二面角：**只旋转第四个原子**，绕 B→C 中心键轴按最短归一化增量
    `normalizeDihedral(目标 − 当前)` 旋转；中心键与末端键长保持；
  - 未移动原子逐字节保留原坐标；`currentFrame` **从不被修改**，因此撤销/
    重做/放弃后按事务或原帧重建出的屏幕几何与原始几何逐字节一致
    （测试直接比较 `scene.addModel` 的 XYZ 文本）。
- 事务生命周期：`应用`（预览，`data-acp-edit-state="preview"`）→ `确认应用`
  （写入本地事务，`applied`）→ `撤销`/`重做` → `放弃全部`（回到 `currentFrame`）。
  同一时刻只允许一个预览/一次提交；`busy` 期间根节点 `data-acp-edit-busy="true"`、
  按钮禁用，重入的提交调用被忽略（不乱序、不重复入栈）。
- 四个本地状态（根节点 `data-acp-edit-state`）：`idle` / `preview` / `applied` /
  `blocked`；`data-acp-edit-count` = 已应用事务步数，`data-acp-edit-busy` =
  是否正在处理编辑。
- 阻塞条件与显式本地文案：overlay 映射未证实 → “映射未证实 · 编辑已禁用”；
  叠合模式 → “叠合模式 · 请先清除叠合再编辑”；IRC 播放 →
  “IRC 播放中 · 编辑已暂停”；振动动画 → “振动动画播放中 · 编辑已暂停”；
  屏幕模型不是当前帧 → “当前 3D 模型不是编辑几何，请重新加载结构”；
  无结构 → “暂无结构，先加载结构再编辑”。阻塞时输入与 apply 均禁用。
- 状态清理：结构切换/任务切换/目录条目切换/IRC 播放开始/振动动画开始/
  清除全部测量/`dispose` 都清空事务；任务切换但 structureRef 不变时仍用
  原始 `currentFrame` 重建屏幕几何，避免“已编辑模型 + 空事务”的残留。
  振动动画开始会显式放弃本地编辑（动画显示的不是事务几何）。
- 无持久化：`ACPGeometryStore` 始终保存原始 `currentFrame` 坐标；编辑不写
  store（不 `updateCoordinates`/`setEditPreview`/`markApplied`）、不发请求、
  不提交任务、不新增第二个 viewer，只重建唯一受限场景的 model。
  A-47 的显式资产保存是唯一后端写入（见 §2.12）；A-46 本身仍不持久化，
  保存失败不丢编辑。
- 已知边界：模块不做键连通性/成环/碰撞检测（编辑约定是“只移动一个原子”的
  可逆局部位移，不重排拓扑）；测量滚动列表不虚拟化；没有“编辑历史列表”。

### 2.12 A-47 结构资产保存（2026-10-31）

- 入口在测量抽屉的“资产保存”块。**只有已提交（`applied`，`data-acp-edit-count > 0`）
  的 A-46 编辑几何可保存**；预览（`preview`）、撤销到 0 步、无结构、叠合/IRC/振动
  阻塞、屏幕几何不是编辑几何，或请求通道未注入时，保存禁用并显示本地原因。
  **不提供“保存原始结构”**：没有已提交编辑时不会产生任何后端写入。
- 端点（唯一写入面，已核对 `src/acp/api/v1_routes.py`）：
  `POST {API_BASE}/structure-assets`（201）。不存在 `/jobs/{id}/structure-assets`
  写路由；job/entry 只作为 body 元数据。body 字段：
  - `name`：净化后的 1–80 字符名称（路径分隔符/控制字符折叠为空格）；
  - `xyz_text`：当前显示几何（含编辑 provenance 注释与原始 frame 注释）；
  - `parent_job_id` / `parent_entry_id`：可得时传入；`project_id`：context 提供时传入；
  - `edit_operations`：与 legacy editor 同形的完整事务列表，`type` 为
    `bond_length` / `bond_angle` / `dihedral`，含 `atom_ids`、`before`、`after`、
    `moved_atom_ids`、`collision_warnings: []`（A-46 不做碰撞检测）；
  - `provenance`：`comment`、`created_at`、`op_count`、`source: "molecule-viewer"`，
    可选 `note`（界面“备注”）。
  - 不发送 `charge` / `multiplicity`：A-46 不跟踪电荷/多重度，保持后端解析默认值。
- 请求通道：注入 `deps.request(path, opts)` → `deps.client.transport.request` →
  `window.ACPClient.transport.request`（页面已加载 `js/client/transport.js`）；
  **不读取 `window.fetch`，不用 XHR/sendBeacon**。返回已解析 body（`transport.request`
  契约），不自行解析 HTTP 响应。
- 两步确认：第一次点“保存为资产”只进入确认（根节点/按钮
  `data-acp-asset-confirm="true"`，状态行显示名称），第二次点“确认保存”才发请求；
  “取消”回到未确认状态。
- 四个本地状态（根节点 `data-acp-asset-state`）：`idle`（无已提交编辑/被阻塞）/
  `ready`（可保存）/ `saving`（请求中）/ `saved`（当前事务已保存且几何未变）。
  另有 `data-acp-asset-busy`、`data-acp-asset-error`、`data-acp-asset-id`、
  `data-acp-asset-path`；回执行显示 `asset_id`、`asset_path`、名称与原子数。
  同一事务保存成功后按钮禁用（防重复资产）；撤销/重做/新增编辑会改变事务指纹并
  允许另存新资产；结构/任务切换清空回执。
- 成功回执只记录 `asset_id` / `asset_path` / 名称 / 原子数 / 公式 / 事务指纹，
  **不保存第二份几何**；geometry store、`currentFrame` 与本地事务均不变。
- 失败（HTTP 非 2xx、响应缺 `asset_id`、`ok:false`、网络异常）只写本地错误状态：
  A-46 事务、显示几何与测量都不丢，按钮回到可重试；再次保存重新走确认。
- 守卫：保存期间复用 `editState.busy`（编辑/撤销/重做/放弃/点选/测量删除/抽屉
  清除被冻结），重复点击被忽略（单请求）；`assetSeq` + `generation` 丢弃
  dispose / 结构切换 / 任务切换后的迟到成功或失败，不写 DOM、不记录回执。
- 保存不触发任务提交、不写任务目录、不修改原始 XYZ 文件，也不新增第二个 viewer。

## 3. molecule-view.js 解析函数白名单结论

**结论：不以 library 方式加载 molecule-view.js，XYZ 解析统一走 `view_model.js::parseXyzFrames`**（理由如下）。

逐函数核查（`workbench/features/molecule-view.js`）：

| 函数 | 纯度 | 结论 |
|---|---|---|
| `parseMultiFrameXYZ(text)` | ✅ 纯（无 shared-state 全局、无 DOM） | 语义由 `view_model.js::parseXyzFrames` 统一实现（source + viewer 共用） |
| `parseXYZBlock(text)` | ✅ 纯 | 白名单成员，但 P0 未用（多帧覆盖单帧） |
| `atomsToXYZ(atoms)` / `framesToXYZ` / `countFormula` / `parseEnergyFromComment` | ✅ 纯 | 白名单成员；`atomsToXyz` / `parseEnergyFromComment` 由 `view_model.js` 提供 |
| `parseMolTextToFrame(text, format)` | ❌ 不纯 | 依赖页面 `viewer` 全局 + `initViewer()`（DOM + 厂商实例）。**绝不复用**；P0 只支持 XYZ |

不加载原文件的决定性原因（即使上述纯函数可复用）：

1. **加载期副作用绑定页面全局**：文件顶层注册 `window._sv*` 桥（`_svLoadXyzToViewer` /
   `_svClearViewerGeometry` / `_svSyncGeometryToCanvas` 等）、`window.__acpPickDiag`、
   `window.addEventListener("acp:interaction-state", …)`，并在两个顶层 IIFE 中读取
   `document.getElementById("sv-measure-editbar-*")` 与 `window.ACPGeometryStore` ——
   在 acp-web 新入口会污染全局命名空间并与本模块的 store 写入竞争（store 订阅 IIFE
   会在 `load` 变更时触发旧页 canvas 同步逻辑）。
2. `:113` 读 `#viewer-library` 的加载期守卫**已存在**（`if (viewerLibraryScript) {…}`），
   元素缺失时静默跳过 —— 本次未对 `molecule-view.js` 做任何修改（旧页面行为不变）。
3. 自写解析器 ≈40 行、行为对齐 `parseMultiFrameXYZ`（含能量注释行提取），并有独立测试。

模块另经 `_internals` 暴露纯函数（`parseMultiFrameXyz / atomsToXyz / refKey /
normalizeRef / buildUrl`、A-45 测量数学、A-46 的 `EDIT_RANGES /
validateEditTarget / displaceGeometry / formatEditInputValue`）供测试与诊断
使用；**不暴露实例内部状态**。

## 4. 渲染与几何栈

- 渲染只经 `ACPVisualization.scene.create({container, options})`（受限实例）+ `scene.addModel(...)`
  返回的模型 token；framing 一律走 `ACPVisualization.framing.scheduleViewerFraming /
  disposeViewerFraming`（root ANTI #22）。新文件中**不存在**厂商库直呼字面量（`$3Dmol`）；
  受限 token 方法仅用守卫白名单内的 `setStyle`（`tests/test_frontend_sync_viewer_framing.py`
  的 `test_add_model_result_restricted_methods_pin` 对 `scene.addModel` 直呼天然放行）。
- 默认样式 = 旧页 `STYLE_PRESETS["ball-stick"]`（`stick.radius 0.15` + `sphere.scale 0.28`）。
- 几何状态经 `window.ACPGeometryStore`（`js/geometry_store.js`）：成功渲染后
  `store.load({entryId, symbols, coordinates})`；`entryId = "<jobId>:<path>"`、
  `"<jobId>:entry:<entryId>"` 或 `"inline-xyz"`。**多实例前需工厂化 store** —— 当前为页面级单例 + P0
  `supportedInstancing: "single"` 语义；dispose 时仅当本模块拥有已加载条目（
  `storeOwned` 标记）才 `store.reset()`，避免误清他人状态。
  **A-46 编辑只重建 `scene` 的 model，不调用 `store.updateCoordinates`/
  `setEditPreview`/`markApplied`**：store 始终持有原始 `currentFrame` 坐标，
  本地事务（预览/撤销/重做/放弃）只在模块内存在，切换或 dispose 即消失。
  **A-47 保存只读显示几何并 POST 资产**，同样不写 store、不改 `currentFrame`，
  只在内存记录返回的 `asset_id` / `asset_path` 回执。

## 5. 文案 / i18n

zh 字面量 + `data-i18n` 属性（key 前缀 `next.viewer.*`），不硬依赖 i18n 模块；
Shell 接入 i18n 后可统一替换：

| key | zh 默认 |
|---|---|
| `next.viewer.empty` | 等待结构加载 |
| `next.viewer.loading` | 结构加载中… |
| `next.viewer.ready` | （ready 态隐藏 overlay） |
| `next.viewer.errorTitle` | 结构加载失败 |
| `next.viewer.retry` | 重试 |
| `next.viewer.denied` | 无权限访问该结构 |
| `next.viewer.catalog_toggle` | 结构目录 |
| `next.viewer.catalog_loading` | 结构目录加载中… |
| `next.viewer.catalog_empty` | 暂无结构条目 |
| `next.viewer.catalog_error` | 结构目录加载失败 |
| `next.viewer.catalog_other` | 其他 |
| `next.viewer.style` | 样式 |
| `next.viewer.style_ball_stick` | 球棍 |
| `next.viewer.style_stick` | 棍状 |
| `next.viewer.style_spacefill` | 空间填充 |
| `next.viewer.style_wireframe` | 线框 |
| `next.viewer.style_publication` | 出版 |
| `next.viewer.reset_view` | 重置视角 |
| `next.viewer.overlay` | 叠加 |
| `next.viewer.overlay_clear` | 清除叠合 |
| `next.viewer.overlay_loading` | 叠合计算中… |
| `next.viewer.overlay_blocked` | 映射未证实 · 测量已禁用 |
| `next.viewer.irc_forward` | 正向 |
| `next.viewer.irc_reverse` | 反向 |
| `next.viewer.irc_stop` | 停止 |
| `next.viewer.vibration` | 振动 |
| `next.viewer.measure` | 测量 |
| `next.viewer.measure_distance` | 距离 |
| `next.viewer.measure_angle` | 角度 |
| `next.viewer.measure_dihedral` | 二面角 |
| `next.viewer.measure_pick` | 开始选取 |
| `next.viewer.measure_cancel` | 取消选取 |
| `next.viewer.measure_clear` | 清除全部 |
| `next.viewer.measure_delete` | 删除 |
| `next.viewer.edit_title` | 内坐标编辑 |
| `next.viewer.edit_badge` | 本地事务 |
| `next.viewer.edit_apply` | 应用 |
| `next.viewer.edit_commit` | 确认应用 |
| `next.viewer.edit_cancel` | 取消预览 |
| `next.viewer.edit_undo` | 撤销 |
| `next.viewer.edit_redo` | 重做 |
| `next.viewer.edit_abandon` | 放弃全部 |
| `next.viewer.asset_title` | 资产保存 |
| `next.viewer.asset_save` | 保存为资产 |
| `next.viewer.asset_confirm` | 确认保存 |
| `next.viewer.asset_cancel` | 取消 |
| `next.view.fixture` | 演示数据（fixture） |
| `next.view.warning` | 数据不全：{message} |
| `next.view.warning_more` | 另有 {n} 条提示 |
| `next.viewer.errorMessage` | descriptor.error 使用的兜底 message key |

A-47 的 4 个 `next.viewer.asset_*` 键在 `frontend/js/shell/i18n.js` 的 zh-CN / en-US
词典中均有对应条目（A-61 的完整性与配对扫描通过）。

## 6. 已知缺口（P0）

- 仅 XYZ（多帧）；MOL/SDF 解析未做（旧页 `parseMolTextToFrame` 不纯，不可复用）。
- A-45 只读测量、A-46 本地内坐标编辑事务、A-47 显式资产保存已接入。
  A-47 只保存已提交编辑（不提供保存原始结构）；服务端 `structure-assets`
  没有幂等键，若请求已落库但回包丢失（或客户端在请求中 dispose/切换），
  重试会生成第二个资产，客户端只能丢弃迟到回执，不能回滚已落库的资产
  （UI 只能在收到回执后阻止同一事务的重复保存）。
- `view_source.js` 的 live 适配器当前覆盖 `structure` / `structure-entry` / `energy` / `catalog`；其余视图族显式返回 `E_FAMILY_NOT_IMPLEMENTED`（后续 A-xx 逐族接入）。
- 单帧显示，无帧播放控制（`frameIndex` 只选帧）。
- 测量与编辑事务本身不持久化：切换结构/任务会清空；A-47 可显式把已提交编辑
  几何另存为结构资产，但不保存测量行，也没有跨帧历史。
- A-46 编辑是“只移动一个原子”的简单可逆约定，不做键连通性/成环/碰撞检查，
  也不把编辑几何同步给 geometry store 或其它面板（store 始终保存原始
  `currentFrame`）；A-50 路径视图如需复用需先确认该约定。
- 3D 库异步未就绪时展示 error+重试（不做自动轮询等待）。
- geometry store 为页面级单例，多实例化前必须先工厂化。
