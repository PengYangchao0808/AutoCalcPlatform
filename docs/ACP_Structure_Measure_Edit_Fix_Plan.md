# 结构查看器测量与轻量几何编辑修复方案

**版本：** 1.0  
**日期：** 2026-09-12  
**范围：** 结构查看器中的距离/键长、键角、二面角测量与修改，不扩展为完整分子建模器。

## 1. 结论

截图中的“测量：距离 · 已选 0/2”证明工具按钮和模式切换已经生效，但原子点击没有稳定进入选择状态。当前异常的关键不在距离、角度和二面角公式，而在真实画布拾取与编辑状态的集成。

当前实现存在三套相互耦合但没有统一所有权的状态：

- 页面全局 `molDoc`：当前帧、选择、测量列表和测量模式；
- `ACPStructureViewer.state`：当前任务条目、解析后的 symbols/coords、dirty 状态；
- `ACPStructureEditor.editorState`：邻接图、原始坐标、事务、撤销/重做。

它们依靠 `_svLoadXyzToViewer()` 和若干全局函数桥接。选择原子时调用 `renderMolDoc()`，应用修改后又通过 XYZ 桥接重载模型；重载过程会清空 `molDoc.selection`、`molDoc.measures` 和 `measureBuffer`。因此，拾取、测量记录、编辑结果和撤销状态很容易不同步。

现有相关测试主要验证纯几何函数和源码中是否存在调用字符串。它们没有创建真实 3Dmol 模型、执行原子点击并验证画布重建后的状态，所以即使测试通过，也不能证明界面可用。

## 2. 关键问题定位

### P0-1：原子拾取依赖临时自定义属性

当前流程先从 3Dmol 模型取得 atom 对象，再给对象写入 `atom.acpId`，点击回调只接受 `typeof atom.acpId === "number"` 的原子。

风险：

- 3Dmol 在 selectable/clickable 内部重新取得 atom 对象时，不保证保留后写入的自定义属性；
- 一旦 `acpId` 缺失，回调直接静默返回，界面就会一直停在 `0/2`；
- 没有任何错误提示或诊断事件，用户只看到“点不动”。

修复：以 3Dmol 原生稳定索引 `atom.index` / `serial` 为拾取输入，通过当前 geometry 的显式映射表解析为 ACP atom id；禁止以运行时注入的 `acpId` 作为唯一身份来源。

### P0-2：每次拾取都会销毁并重建模型

`handleMeasureClick()` 将原子加入缓冲区后立即调用 `renderMolDoc()`；后者执行：

```text
removeAllModels → addModel → setStyle → setClickable → render
```

这相当于在 3Dmol 点击回调尚在执行时销毁触发该回调的模型。即使部分浏览器中偶尔可用，也属于高风险的重入式生命周期。

修复：

- 原子选择只更新选中样式和编号标记，不重建模型；
- 只有切换任务、条目或轨迹帧时才重建模型；
- 提供 `updateAtomSelectionStyle()`，使用 `setStyle/addStyle + render` 做增量更新；
- clickable 回调在模型装载后绑定一次，直到 geometry revision 改变。

### P0-3：编辑提交会清空刚创建的测量

`ACPStructureEditor._commitEdit()` 更新 `displayedCoords` 后调用 `_pushModelToViewer()`；该函数通过 `_svLoadXyzToViewer()` 重载 XYZ。桥接函数会清空测量和选择，然后抽屉代码再用“best-effort”方式重新插入一条 `_applied` 测量。

这种“先清空、再猜测恢复”的流程会导致：

- 应用后测量记录丢失或重复；
- 测量列表中的索引与实际对象不再稳定；
- 重算值可能读取旧的 `molDoc` 坐标；
- undo/redo 后抽屉和画布显示不一致。

修复：编辑事务直接调用统一的 `geometryStore.updateCoordinates()`，再增量更新 3Dmol 模型坐标；测量对象保留稳定 `measurement_id`，并根据新坐标重新计算，不经过清空和重新播种。

### P0-4：测量和可编辑内部坐标没有分层

任意两个、三个或四个原子都可以测量距离/角度/二面角，但内部坐标修改要求：

- 键长：A-B 必须存在化学键；
- 键角：A-B-C 必须是连续成键路径；
- 二面角：A-B-C-D 必须是连续成键路径；
- 中心旋转键不能是无法切开的环键。

当前界面直到点击“应用修改”后才以 `no_bond`、`ring_bond` 等内部 reason 失败，表现为“新增功能不能用”。

修复：测量创建时立即运行 `validateEditableCoordinate()`：测量始终允许；只有满足拓扑条件时才显示“修改”输入框，否则显示人类可读说明，例如“该距离不是键长，只能测量”“中心键位于环中，轻量编辑器不支持旋转”。

### P1-1：成键推断可能产生错误拓扑

XYZ 没有键级，当前使用共价半径乘以 1.3 推断。对过渡态、弱键、拥挤构象和金属体系，可能漏键或多推断键，进而错误触发 `no_bond` / `ring_bond`。

修复优先级：

1. SDF/MOL 或结果 manifest 有显式 connectivity 时优先使用；
2. 计算输出能提供 Wiberg/Mayer 或后端 connectivity 时使用明确来源；
3. 只有 XYZ 时才使用半径推断；
4. 在编辑面板显示“键连接来源：文件 / 计算结果 / 半径推断”；
5. 半径推断不可靠的体系只允许测量，编辑需明确解锁，不静默猜测。

### P1-2：修改哪一侧不透明

键长会移动一个连通片段，角度和二面角会旋转一侧片段，但当前界面没有在应用前显示将移动哪些原子。

修复：选定内部坐标后预高亮固定侧和移动侧，并提供简化切换按钮：

```text
移动侧：自动（较小片段） | A 侧 | D/C 侧
将移动 7 个原子
```

环键仍不支持切侧编辑，保持轻量化边界。

### P1-3：振动、叠合与编辑模式冲突

振动动画会锁定编辑器，但测量按钮和应用按钮仍可能保持可操作外观；叠合模式下测量也可能对应不明确的模型。

修复：建立单一交互状态机：

```text
VIEW → PICKING → MEASURED → EDIT_PREVIEW → DIRTY
  ↑        ↘ Cancel ────────────────┘

VIBRATING / OVERLAY 进入时暂停或禁用 EDIT_PREVIEW
```

不可操作时按钮必须 disabled，并说明“请先暂停振动动画”或“叠合模式下不能修改结构”。

## 3. 推荐的新交互

### 3.1 拾取反馈

- 进入工具后光标变为十字或原子拾取指针；
- 已选原子显示 `1 / 2 / 3 / 4` 顺序徽标，而不是只有黄色球；
- 工具栏状态显示元素和编号，例如 `距离 · C12 → O18 · 1/2`；
- 重复点击最后一个原子表示撤销该步；`Esc` 清空本次选择；
- 禁止同一原子在同一个测量中重复出现，并给出轻提示。

### 3.2 测量完成后的底部编辑条

完成选择后在画布底部显示紧凑条，而不是依赖用户再寻找列表：

```text
C12—O18   1.428 Å     目标 [ 1.400 ] Å   [预览] [应用] [取消]
```

键角和二面角使用同一布局。点击“预览”只更新临时坐标；点击“应用”才创建事务。关闭底栏不会修改结构。

### 3.3 错误信息

内部 reason 必须映射为中文：

| reason | 用户提示 |
|---|---|
| `no_bond` | 所选原子不是连续成键路径，只能测量，不能这样修改 |
| `ring_bond` | 中心键位于环中，轻量编辑器暂不支持旋转该片段 |
| `degenerate` | 当前几何共线或重合，无法定义稳定旋转轴 |
| `out_of_range` | 目标值超出允许范围 |
| `locked` | 请先暂停振动动画或退出冲突模式 |
| `entry_mismatch` | 当前结果已切换，请重新选择原子 |

不应把 `no_bond` 等程序字符串直接展示给用户。

## 4. 状态和接口重构

### 4.1 单一几何所有者

由 `ACPStructureViewer.geometryStore` 统一持有：

```text
entryId
revision
symbols
coordinates
bonds
bondProvenance
selection
measurements
editPreview
```

`molDoc` 只作为兼容视图适配器逐步退出；`ACPStructureEditor` 接收 store snapshot 并返回 transaction，不再直接跨模块修改多个全局对象。

### 4.2 稳定身份

- atom id：以结构条目内 0-based canonical index 为权威；界面显示 1-based 编号；
- rendered atom map：`modelAtom.index → canonical atom id`；
- measurement id：UUID/entry revision scoped id，不能使用数组下标；
- 每个测量保存 `entry_id + geometry_revision + atom_ids`；切换 entry 后明确失效，而不是误用于新结构。

### 4.3 增量画布更新

新增三个明确的适配器：

- `bindPickHandlers(model, geometryRevision)`：一次绑定；
- `renderSelectionOverlay(selection)`：只更新高亮和顺序标签；
- `updateModelCoordinates(coords)`：编辑、undo、redo 时更新坐标和测量覆盖物，不重新创建 viewer/model。

如果 3Dmol 当前版本不能安全原位更新坐标，可以重建 model，但必须由 geometry store 在重建前后保留 selection、measurements 和 editor session，且禁止在 click callback 同步重建，改到下一帧统一刷新。

## 5. CLI 与 GUI 边界

截图对应的是 GUI 结构查看器，不是命令行 CLI。当前故障应优先修复前端拾取和状态同步。

若后续确实需要无界面的批量几何修改，可另行设计：

```text
acp structure edit input.xyz --bond 12 18 1.40 -o edited.xyz
acp structure edit input.xyz --angle 4 8 13 109.5 -o edited.xyz
acp structure edit input.xyz --dihedral 1 5 8 12 180 -o edited.xyz
```

该 CLI 应复用同一套经过验证的几何核心和拓扑校验，但不应作为修复当前 GUI 点击异常的替代方案。

## 6. 实施顺序

### P0：恢复基本可用性

1. 用 3Dmol 原生 index/serial 建立显式 atom map，去除 `acpId` 静默失败路径。
2. 原子点击只增量更新选择样式，不在回调中执行 `removeAllModels()`。
3. 统一 measurement store，编辑后不再清空再播种。
4. 创建测量时先验证是否可编辑，并翻译错误原因。
5. 修复应用、撤销、重做后坐标和测量值同步。

### P1：交互优化

1. 增加拾取顺序标记和底部编辑条。
2. 增加移动侧高亮及自动/A侧/C-D侧选择。
3. 振动、叠合、轨迹播放与编辑接入统一状态机。
4. 显示成键来源和推断可靠性。

### P2：测试和可诊断性

1. 增加真实浏览器 + 3Dmol 集成测试，不再只检查源码字符串。
2. 增加拾取事件诊断：mode、rendered index、canonical id、revision。
3. 对真实 OPT、TS、Confsearch 和金属/无显式键样例做回归。

## 7. 必须增加的端到端测试

1. 加载一份真实 XYZ，点击距离工具，再点击两个原子，状态依次为 `0/2 → 1/2 → 0/2`，并生成一条测量。
2. 完成测量后 3D 画布、底部编辑条和测量列表显示相同原子及数值。
3. 修改键长后目标值生效，其他片段内部距离保持不变。
4. 修改键角和二面角后达到目标值，原子顺序没有变化。
5. 应用后 undo、redo、reset 均同步更新模型和测量值。
6. 连续创建两条测量不会因模型重建丢失第一条。
7. 非成键距离可以测量，但修改按钮禁用并解释原因。
8. 环键编辑被明确阻止，普通非环键可选择移动侧。
9. 切换构象/轨迹帧后旧 measurement 不会错误应用到新 revision。
10. 振动动画播放时编辑按钮禁用；暂停后恢复。
11. 在 3Dmol 回调 atom 缺少自定义属性时，仍可通过原生 index 正确拾取。
12. 浏览器缩放、画布 resize 和侧栏开合后仍能准确拾取原子。

## 8. 验收标准

- 用户点击工具后第一次点击原子必然产生可见的 `1/N` 反馈；
- 不允许发生静默点击失败；任何拾取失败必须给出可诊断状态；
- 距离、键角、二面角均可测量，满足拓扑条件时均可修改；
- 编辑过程不销毁任务结果条目，不改变元素、原子数和键拓扑；
- 应用、撤销、重做、重置、切换结果和刷新之间状态一致；
- 所有错误使用用户可理解的中文，不显示内部 reason；
- 实际浏览器点击测试纳入 CI，纯函数测试不能代替集成验收。

