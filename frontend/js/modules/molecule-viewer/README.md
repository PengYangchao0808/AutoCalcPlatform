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

- `deps = { context: { getSnapshot, subscribe }, commands: { openTask, openEvidence }, fetch? }`
  — 全部可注入可 mock；**P0 只读**：structureRef/taskRef 变化时更新自身，绝不回写 context。
- `mount` 返回 ≠ 数据就绪（3D 库可能仍在异步加载）；模块以自身 `data-acp-module-state` 表达
  `empty | loading | ready | error`。
- `mount` 失败（宿主非法 → reject TypeError；内部失败 → 清理自身资源后在 host 内渲染
  局部错误框 + 重试按钮，**不外泄**异常）。
- `dispose` 幂等且彻底：取消 context 订阅、销毁 framing（ResizeObserver/定时器）、
  `scene.dispose()`、移除自建 DOM、reset **本模块拥有**的 geometry store 状态。
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
- fetch 解析：注入 `deps.fetch` 优先，回落 `window.fetch`；接受 fetch `Response`
  （`ok/status/text()/json()`）与 `api()` 风格预解析 body（`{content}` 或裸文本）。

## 3. molecule-view.js 解析函数白名单结论

**结论：不以 library 方式加载 molecule-view.js，模块内自写最小多帧 XYZ 解析**（理由如下）。

逐函数核查（`workbench/features/molecule-view.js`）：

| 函数 | 纯度 | 结论 |
|---|---|---|
| `parseMultiFrameXYZ(text)` | ✅ 纯（无 shared-state 全局、无 DOM） | 语义已在模块内复刻（`_internals.parseMultiFrameXyz`） |
| `parseXYZBlock(text)` | ✅ 纯 | 白名单成员，但 P0 未用（多帧覆盖单帧） |
| `atomsToXYZ(atoms)` / `framesToXYZ` / `countFormula` / `parseEnergyFromComment` | ✅ 纯 | 白名单成员；模块复刻了 `atomsToXYZ`/`parseEnergyFromComment` |
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
normalizeRef / buildUrl`）供测试与诊断使用；**不暴露实例内部状态**。

## 4. 渲染与几何栈

- 渲染只经 `ACPVisualization.scene.create({container, options})`（受限实例）+ `scene.addModel(...)`
  返回的模型 token；framing 一律走 `ACPVisualization.framing.scheduleViewerFraming /
  disposeViewerFraming`（root ANTI #22）。新文件中**不存在**厂商库直呼字面量（`$3Dmol`）；
  受限 token 方法仅用守卫白名单内的 `setStyle`（`tests/test_frontend_sync_viewer_framing.py`
  的 `test_add_model_result_restricted_methods_pin` 对 `scene.addModel` 直呼天然放行）。
- 默认样式 = 旧页 `STYLE_PRESETS["ball-stick"]`（`stick.radius 0.15` + `sphere.scale 0.28`）。
- 几何状态经 `window.ACPGeometryStore`（`js/geometry_store.js`）：成功渲染后
  `store.load({entryId, symbols, coordinates})`；`entryId = "<jobId>:<path>"` 或
  `"inline-xyz"`。**多实例前需工厂化 store** —— 当前为页面级单例 + P0
  `supportedInstancing: "single"` 语义；dispose 时仅当本模块拥有已加载条目（
  `storeOwned` 标记）才 `store.reset()`，避免误清他人状态。

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
| `next.viewer.errorMessage` | descriptor.error 使用的兜底 message key |

## 6. 已知缺口（P0）

- 仅 XYZ（多帧）；MOL/SDF 解析未做（旧页 `parseMolTextToFrame` 不纯，不可复用）。
- 单帧显示，无帧播放控制（`frameIndex` 只选帧）。
- 无原子点选/测量交互（geometry store 已就位，交互留给后续车道）。
- 3D 库异步未就绪时展示 error+重试（不做自动轮询等待）。
- geometry store 为页面级单例，多实例化前必须先工厂化。
