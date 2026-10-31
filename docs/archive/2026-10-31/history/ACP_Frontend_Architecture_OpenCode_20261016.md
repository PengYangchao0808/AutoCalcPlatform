# ACP 前端架构并行开发任务书 — OpenCode

日期：2026-10-16。状态：首批模块已对接并验证，后续部分按本文逐片实施。实际完成范围见[前端对接验收记录](../../../reference/current/ACP_Frontend_Integration_20261016.md)。用途：OpenCode 与 Antigravity 的并行职责及后续交接参考。

上位方案：[完整架构调整方案 v1.12 第 40 节](../../../ACP_Architecture_Adjustment_Plan_20261016.md#40-antigravity-与-opencode-并行开发) 为当前并行写权和逐片接入权威；38.6—38.10 的独立运行/兼容门和第 39 节 HTML 拆分目标继续有效。配对任务：[Antigravity HTML 拆分](ACP_HTML_Decomposition_Antigravity_20261016.md)。本文只展开前端工作，不授权迁移 Services／CCCP，不构成另一套总计划。

## 1. 交付目标

按现有技术栈把前端收敛为 Workbench／Client／Visualization：用户流程有明确 owner，网络与缓存统一，图形实例独立。**本轮保持现有布局、主题、文案、功能入口和操作流程，只调整内部代码结构与接线。** 新服务能力按对应功能切片接入，不借架构调整提前重设计界面。

**你现在即可开始 Client、Visualization、controller 与专属测试，不必等待 Antigravity 拆完 HTML。** Antigravity 同时维护主 HTML/CSS/旧页面脚本，并按你交付的接口/接线清单逐片接入模块。并行开发不等于同时编辑共享入口。

你拥有新模块正确性、S0 兼容和接入说明；Antigravity 拥有页面拆分与入口接线正确性；真实页面/旧服务联调由双方完成。主 HTML、bootstrap 和共享大测试在整个并行期由 Antigravity 单写，不因某片完成自动转交给你。已存在/已迁移的实现直接复用，不搬回旧结构。

**硬性完成条件：仅替换本轮前端文件，其他部分保持已冻结的现有版本，原有支持功能仍正常运行。** 不得将新增 prepare、持久事件、新科学产品、Services 拆包或 CCCP 迁移列为本轮完成前置。独立运行要以实施后的真实旧服务验证证明，当前方案不代表代码已经通过。

## 2. 基线与目标驻点

先读根及 `tests/AGENTS.md`、主方案第 4／7／8／12—14／29／37／38 节；实际涉及服务代码时另读相应嵌套规范并交服务负责人处理。

本次只读核对的起点为 HEAD `779a29a` 加大量未提交工作。主 HTML 约 3.5 万行，存在 `api/apiRequest/apiRemote/apiV2/apiV2Remote`、直接 fetch、多路刷新、EventSource 和多处 `$3Dmol.createViewer`；来源选择器也有自己的通信路径。以上是抽取入口，不是已完成的迁移清单；实施时重新清点全部调用。

以上为 H 之前的盘点；并行期间代码可能仍在 HTML，也可能已到 `js/workbench/{compat,features}`。从冻结的符号/摘要地图取实现，不按旧行号写文件。已发现 `frontend/js/client/transport.js`，公开 `ACPClient.transport.init/request/remote`；先审计接续及补验证，不覆盖用户工作或新建第二 transport。本次只读观察不表示该模块已经生产接入或行为验证通过。

```text
frontend/ACP_Workbench_v2.html       Antigravity 独写，你提供接线清单
frontend/js/workbench/
  views/ compat/ features/          Antigravity 独写，你默认只读
  controllers/                     OpenCode：用户用例与跨面板流程
  contracts/                       OpenCode：纯显示输入/动作/接口与样例
  [字典/bootstrap]                  Antigravity：资源装载与主页面装配
frontend/js/client/                OpenCode：transport/store/query/command/change/refresh/adapters
frontend/js/visualization/         OpenCode：contracts/models/renderers/adapter/lifecycle/export
frontend/css/workbench/            Antigravity 独写，保持原呈现
frontend/js/                       未迁移实现和必要兼容转发，按片清理
frontend/css/                      原样式按需抽取，保持呈现，不全量覆盖
frontend/js/vendor/                保留本地 3Dmol 及许可，不修改第三方实现
```

目录是目标而非要求的空骨架。**本轮实际路径必须位于旧服务已公开的 `/js` 或 `/css` 下。** 代码核对发现 `src/acp/api/server.py` 只挂载这两个静态根；主方案 37.1 的顶层 `frontend/{workbench,client,visualization}` 是长期目标，本轮不得直接引用对应未挂载 URL。无需修改服务挂载、反向代理或安装依赖即可加载的路径优先，不建两份同实现源码或用符号链接弥补。

不引入 React/Vue、全局状态框架或全新构建系统作为前置。继续使用现有脚本装载方式或最小兼容方案，验证加载顺序、相对资源引用、初始化次数、缓存版本与 3Dmol 异步就绪；目录存在不等于浏览器能通过原服务加载。

## 3. AR-0：冻结最小交接（D0／P0／CQ-01）

1. 记录代码 revision、dirty/untracked 文件归属、默认入口、现有浏览器与接口基线；不重置、不丢弃、不顺手提交用户修改。
2. 列出网络入口／查询键／定时器／事件流、状态写者、图形创建点、DOM hooks 和动态模板，按 Workbench／Client／Visualization 归属。
3. 首片固定“任务列表／详情＋一种现有二维结果及关联结构预览”。优先复用已有节点/注解与结构 fixture；科学预期由服务/科学负责人确认，不在前端重算。
4. 提供第 38.3 节交接材料，至少含只读显示输入、动作回调、DOM hooks、可写文件、真实 API 支持和缺口、正常／空／部分／失败／旧版本样例。
5. 记录实施环境可用的浏览器／JS 测试机制，不先为重构安装一套新测试框架。
6. 冻结 F0/S0：F0 为当前完整前端资源集，S0 为实际现有服务/CCCP/配置/依赖与接口行为。纳入已确认的工作区改动并记录补丁/摘要，不只记录裸 HEAD。创建独立测试数据，记录原故障，禁止通过删功能消除基线失败。

记录同一原页面/后端基线，并将拟接管的 api*/刷新资源/renderer/全局依赖登记给 Antigravity，随即在专属目录开发。不要等 H5、重复 H 的纯搬运或改模板；涉及服务字段语义仍由服务负责人审阅。

### 3.1 OpenCode 并行工作清单与优先级

| 包 | 可以立即实现的内容 | 主要产物 | 接入时交给 Antigravity |
|---|---|---|---|
| OC-0 最小契约与接管清单 | 任务列表/详情＋单结果片的输入、回调、生命周期与符号 owner；冻结正常/空/失败样例 | 纯契约/JSDoc、样例、旧符号→新 API 清单 | 脚本顺序、接口版本、初始化参数、DOM 容器需求 |
| OC-1 transport | 接续现有 transport，兼容错误/超时/取消/JSON/文本/FormData，提供 S0 adapter | `js/client/**` 与专属行为测试 | 旧 apiRequest 的同签名委托；保留 api 的编辑拦截与写后失效 |
| OC-2 任务资源协调 | 首先只统一任务列表/详情的 store、同 key 合并、版本/选择世代、可见性/SSE/轮询协调 | Client 资源模块、失效/订阅/取消测试 | 旧 jobs/summary 刷新和缓存写者的撤除清单；不动其他资源 owner |
| OC-3 单结果可视化 | 一种二维图＋关联分子预览，实例 API、选择回调、framing/导出/销毁 | `js/visualization/**`、实例/映射测试 | 旧 renderer 替换点、容器和显式数据/resize/可见性事件 |
| OC-4 最小 controller | 任务选择→详情→结果；注入 Client/Visualization/view callbacks，处理迟到响应和 dispose | `workbench/controllers/**`、controller 状态/动作测试 | view 显示模型/动作绑定清单；未迁移用例仍可走窄 adapter |
| OC-5 逐片验证/清理 | 模块契约和 S0 验证；接入后排查重复 owner、旧引用与回退 | 每片测试/限制/接线和回退说明 | 由 Antigravity 应用旧代码删除/窄委托并联合验收 |

OC-1 与 OC-3 可在各自最小契约确定后并行推进；OC-2 不要求所有网络资源先迁完，OC-4 可注入可控 adapter 测试。先交 transport，再交任务资源和单结果/控制器，不把全部十五工作流或新服务能力纳入首片。

### 3.2 并行期文件写权

你默认可写 `frontend/js/client/**`、`frontend/js/visualization/**`、`frontend/js/workbench/{controllers,contracts}/**`。专属测试保持 tests 平铺，可用 `test_frontend_client_*.py`、`test_frontend_visualization_*.py`、`test_frontend_controller_*.py` 等独立文件名；实际目录/文件按需要创建，不搭空骨架。纯显示契约样例由你维护，不重算科学黄金值。

你默认只读主 HTML、`frontend/css/**`、旧 `frontend/js/*.js`、`workbench/{compat,features,views}`、字典/bootstrap、`test_frontend_sync.py` 与其他已分配给 Antigravity 的页面回归测试。可运行现有测试，但不要在自己分支顺手改共享入口。对这些文件的必要变更，交接具体符号/补丁文本和验证要求，由 Antigravity 应用。

共享纯契约由你单写、Antigravity 审阅并消费；两份任务书各维护自己的交接记录，主方案统一由协调者维护。不要用整文件格式化或 ours/theirs 覆盖他人工作。独立 checkout/分支与隔离测试数据必需；本任务仅定义要求，不替用户移动当前未提交代码。

## 4. AR-1：Client 唯一通信与副本（D1／P1／CQ-03）

- transport 统一处理现有 v1/v2 URL、状态码、解析、超时、取消及结构化错误。保留现有本地默认 8 秒、适用远端请求 60 秒的场景差异，不能在重构中全部归为一个短超时。
- 保留旧 `api/apiRequest/apiRemote/apiV2/apiV2Remote` 的实际调用契约，经窄 shim 委托新实现；v1/v2 前缀不混淆，文本/JSON、FormData、调用方 signal、取消静默处理和写后元数据失效不变。尤其保留 `api(POST /jobs)` 的 `ACPJobEditor.interceptJobCreate` 编辑重算拦截，不能抽 transport 后绕过它重复创建任务。
- 公开显式 query／command／subscribe／prepare 能力，资源方法按首片实际用例命名；不暴露任意 `call(name, dict)` 超级入口。
- ResourceStore 是共享服务资源缓存的唯一写入口；面板可持有订阅和只读快照，不再各自维护可写 jobs 权威副本。表单、草稿和选中态归 Workbench。
- 查询键包括授权/项目范围、资源身份、过滤/分页及适用 attempt/generation/数据范围；不只按 job_id 缓存所有内容。身份/范围切换清除不适用副本与订阅。
- 同 key 的在途查询合并，订阅者取消与底层请求取消分离；一个面板销毁不能中断另一个面板仍需要的请求。
- job/name/catalog/组织 revision 分别比较；校验选择令牌和请求所属身份，迟到结果不能覆盖新任务／attempt。旧接口缺 revision 时标记能力限制，使用请求世代等局部护栏，不伪造服务版本保证。
- RefreshPolicy 统一可见性、活跃任务与重连策略；只在 SSE 确实覆盖某资源更新时替代对应轮询。日志、其他未覆盖资源和动画不是同一类定时器。
- cursor/去重/快照重置按实际服务能力实现；服务不支持持久事件时保留明确的轮询降级并记录缺口，不假装浏览器去重等于服务持久重放。
- 命令携带现有接口支持的预期版本与幂等信息；不要向不支持的旧接口盲加新字段。动作受理、执行中、最终完成分开。
- Client 无 DOM、toast、科学判定或私有 renderer；错误经 controller 映射为 UI 状态，不能以空列表掩盖失败。

首片接通后删除该片被替代的请求/轮询，其他片保留可枚举的临时兼容入口。不能一边新增 Client 一边保持两套后台刷新。

先单独交付 transport，再逐资源交付共享 store/刷新，避免要求一次迁完所有面板。统一 Client 可以适配当前按任务建立的 SSE；“单一受权全局事件流”是服务后续能力，不能为实现它阻塞本轮或假定现有 SSE 覆盖任务列表/所有资源。

## 5. AR-2：Workbench 控制器与界面接线（D1—D3）

- 将任务选择、详情、结果视图、候选保存、输入草稿、参数编辑和重算各自收敛到相应用例；避免一个新 controller 重新承载整页所有逻辑。
- views 接收只读显示模型、文案和回调，不读 `selectedJobId`、jobsCache、ResourceStore 私有对象或底层 viewer。
- controller 协调 Client 查询/命令和 Visualization 实例；处理旧选择请求、表单脏状态、预检/提交/恢复与错误呈现。视图不互改私有状态。
- 使用 catalog 驱动现有工作流/参数，不重新定义校验和默认 retired 工作流；保持 15 active 工作流和本地专用限制。
- 名称/来源组织/参数编辑继续携带各自预期 revision，冲突显示当前投影并由用户已有流程处理。
- energy viewer 的动作只保存候选；后续任务由原新任务用例提交，不能把 viewer 的选点直接接到提交命令。
- 原样保留或抽取既有模板/样式，保持 DOM hooks 与视觉效果，必要的内部变更同时迁移调用与测试。标签页、面板隐藏/显示传递显式 resize/可见性通知。

你提供 controller 的显示输入/动作回调，不直接操作 Antigravity 私有 DOM；Antigravity 完成模板与回调的接线。主 HTML、动态模板与原样式的写权不因 controller 就绪转移。若新状态无法用现有提示表达，先列最小增量，不扩展为整页设计。

## 6. AR-3：Visualization 唯一图形边界（D2／P3／OS-08）

- 从现有 `energyGraphBuildSvg` 等抽一个二维样板：纯显示模型、renderer、交互分离。保留节点/注解语义，首片沿用 renderer；ECharts 仅在另有明确需求及对比证据后考虑。
- 抽出分子渲染核心与 3Dmol adapter；只有 adapter 引用 `$3Dmol`，业务页面不持有其 viewer 句柄。vendor 加载脚本与许可保留。
- 提供主方案第 38.3 节实例 API 与回调；数据经 controller 注入，缺帧需求交回 controller，不在 Visualization 内联网或读全局任务。
- 结构、轨迹、振动逐片共用三维核心；保留 `scheduleViewerFraming/frame3DViewer` 行为，对隐藏容器、显示后适配、用户相机及多个实例隔离验证。
- 稳定 frame/node/atom/mode 身份遵守现行 frames/VIEW_REGISTRY。原生模式编号不是数组位置；display index 与原生身份映射显式保存。
- 允许坐标轴映射、格式化和有限显示处理，不移入原始 QC 解析、聚类、降维、权威抽样、相对能参考系或科学完成判据。
- 相机、hover、播放、临时测量归实例；跨面板选择与几何编辑草稿归 Workbench，正式保存走既有验证与登记流程。
- 隐藏暂停动画；销毁幂等，停止计时器/动画帧、断开观察器/监听器并释放可释放的图形资源；重复挂载不增加后台活动。
- 按 renderer 声明导出能力，保留现有 PNG；二维 SVG 仅在真实支持时启用。浏览器交互抽取不得删除无浏览器的 NMR JSON/XLSX/PNG 报告路径或其 Python 依赖。

每迁移一种视图，同片移除旧 renderer 与直接调用；临时兼容层只能转发同一实现，不能复制完整绘图代码。

## 7. AR-4：后续能力接入（有条件执行，不是本轮独立交付门）

前端只负责消费和呈现。cache-only 查询、持久 prepare/传输/重试、后台预取、新科学产品与分块、新授权范围、持久事件等由 Services／CCCP 负责人交付。**本轮先用 S0 adapter 完成架构抽取，AR-4 未执行可以独立验收并关闭本轮；后续对应功能片就绪时再接入。**

- 按现有 URL/字段实现兼容 adapter，清楚区分“现有行为”和“新契约待后端实现”；逻辑 query/command 分类不等于擅自修改 API URL。
- 以稳定内部方法屏蔽线协议差异，默认启用现有 adapter；新 adapter 只在声明的服务版本/能力或本部署明确配置满足时启用，不要求 S0 提供新 capability API。显式选择可用于隔离验证；不引入运行时依赖的额外配置文件或后端改动。
- 新 prepare 到位后接操作回执和 availability；未到位不能通过删除现有远端获取路径，使现有远端结果失去入口。迁移 GET 读取行为须由服务负责人配套完成并联调。
- 终态不再轮询科学进度，但结果 prepare/可用性仍须按 Client 策略完成；不能把 COMPLETED 当成字节已在浏览器可读。
- 大数据按范围/帧块与数据预算消费，记录适用上限；禁止默认下载全轨迹后用浏览器重做科学产品。
- 后端 `_job_read_root`、SFTP/cache、CAS、任务目录和实际生命周期不在本前端任务中实现。缺后端能力列出接口需求和阻塞范围，继续完成不依赖它的前端片段。

未知可选字段可忽略，未知必需 schema/缺必填仅使相关资源明确报错；不填零、不假定权限和科学身份。401/403/409/422/5xx/断线不触发换协议或重复命令；资源不存在的 404 也不等于能力不支持。当前 GET 可能触发远端获取，不能借“GET”字样擅改其参数、频率或重试策略。

## 8. AR-5：验证、清理与分片交付

| 范围 | 必须有的证据 |
|---|---|
| Client | 同 key 合并；共享取消；乱序与旧选择；各版本/身份；SSE 与轮询覆盖关系；错误和超时；可见性切换 |
| Workbench | 任务选择/详情/分页；表单与编辑冲突；动作受理到结果；失败与旧数据；原 DOM hooks、布局、文案和操作流程保持 |
| Visualization | 原子/帧/模式/单位映射；多实例与 framing；隐藏和反复挂载/销毁；选择回调；能力匹配的导出 |
| 完整流程 | S0 真实 API 下列表→详情→结果；D3 用隔离数据验证原输入→提交→结果→报告及失败/取消/恢复，后端不迁移 |
| 清理 | 本片旧网络/刷新/renderer 调用移除；未迁移路径和兼容删除条件有台账；默认入口与历史 reader 保留 |

优先复用现有测试，按切片选择运行；以下是相关文件，不表示本次已经运行或全部适用于每一片：

```text
tests/test_frontend_sync.py
tests/test_frontend_startup_browser.py
tests/test_frontend_interaction.py
tests/test_frontend_structure_picker.py
tests/test_frontend_candidate_workspace.py
tests/test_frontend_viewer_framing_browser.py
tests/test_frontend_multiframe_playback_browser.py
tests/test_frontend_measure_edit_browser.py
tests/test_acp_energy_graph.py
tests/test_acp_api_structure_viewer.py
tests/test_acp_job_edit.py
```

JS 行为验证使用现有测试设施和故障样例，不为样式写镜像实现的测试。现有文本断言若固定旧位置，先补等效或更强行为检查，再迁移断言范围；不把测试改成只检查新目录存在。拆大测试时保留原断言、fixtures 和收集覆盖。

收尾按主方案运行适用架构护栏及非慢回归；Python/打包代码若由对应负责人改动，补适用 compileall/安装验证。浏览器或软件不可用的部分标 NOT_VERIFIED，不能当作 PASS。运行服务的重启遵循所在环境及 AGENTS，不因整理前端文件就自动操作生产任务。

每片提交：范围与写权、接口/样例版本、实现和删除清单、测试结果、真实 API/浏览器证据、后端缺口和回滚点。独立 checkout/分支，不共享可写任务目录或测试库；契约先行、实现随后、真实联调后合入，再清理替代路径。

## 9. 各片可单独合入的实施顺序

这里将原 AR 工作包细分为可运行提交，不增加另一套功能路线。除内部边界先明确外，Visualization 与 Client/Workbench 的完成进度互不阻塞。

| 交付单元 | 单独修改内容 | 其余不改时如何运行 | 每片退出门 |
|---|---|---|---|
| 装载与边界 | 提供独立模块/显式初始化和接线清单，Antigravity 修改入口 | 原用例和原请求/renderer 仍工作 | 原服务加载全部资源，首页/旧入口正常，未启用代码无副作用 |
| transport | HTTP/错误/超时/取消与 S0 协议 adapter | 旧 `api*` 接口委托同一 transport；不要求共享 store 先完成 | 请求契约/编辑拦截/上传/文本响应保持，调用方取消不冒充超时 |
| 某资源协调 | 该资源 store、查询合并、刷新 | 旧 feature 使用公开订阅/读取或窄适配，不访问 store 私有结构 | 迁移瞬间撤旧 timer/写者；其他资源刷新不受影响 |
| 某二维/三维视图 | 抽 renderer/实例/生命周期 | 旧 feature 获取原载荷并注入，不依赖新产品或完整 Client | 原选择/单位/模式/framing/导出等价，多实例无资源残留 |
| 某 Workbench 用例 | 原界面后接 controller | Client 或图形尚未迁移时注入其窄接口 | 原输入/详情/候选/编辑/任务动作完整；未迁移面板无回归 |

为尚未迁移的代码保留“旧名称/签名→同一新实现”的 shim。纯载荷 adapter 可以并存不同协议，但不能各自执行业务、复制科学派生或各建共享缓存。兼容旧 UI/旧服务的代码是迁移期明确职责，不因“旧”字直接删除。

切换某资源时，先停止/解除旧 owner，再启用新 owner；不容许新旧轮询、监听器或 store 写者同时处理同一范围。未迁移资源仍由原 owner 处理。动画定时器与网络刷新分别登记。

迁移路径可提供显式测试选择，生产默认每个资源只有一个实现。若最小依赖无法隔离，将依赖与本片一起组成可运行提交，记录原因；不得合入要求后续 PR 修好才能打开的默认页面。

每片记录改动清单和回退资源集；合入一个片不要求其他所有片同时合入。跨模块只共享纯契约/fixtures，修改契约先兼容旧消费方，再迁调用者，最后按明确条件删除转发。

## 10. 前后端互相兼容的验证矩阵

F0/S0 含义见第 3 节；F1 为本轮前端，S1 为后续服务版本。

| 组合 | 谁负责、何时验证 | 必须满足 |
|---|---|---|
| F0＋S0 | 开始前冻结 | 原功能与已知缺陷可复现 |
| 每个已合入前端片＋S0 | 该片提交者 | 原默认页面可用，其他前端片可保持未迁移 |
| F1＋S0 | 本轮前端发布必过 | 不改服务源码/配置/DB/依赖，原功能无新增回归 |
| F0＋S1 | 对应服务片负责人 | 旧前端照常使用原端点/字段/状态/文件/远端获取行为 |
| F1＋S1 | S1 实际可用时联合验证 | 相同旧功能保持；新能力仅在契约满足时启用 |

S1 尚未存在时该组合标 NOT_VERIFIED，可发布已经通过 F1＋S0 的兼容重构，不能宣称已兼容所有未来后端。服务片保留兼容窗口，新增 endpoint/字段优先；旧 Client 未退役前不删除它依赖的协议。原后端应答与接口验收由服务 owner 提供，前端保留相同可显示性/请求 fixtures。

针对 S0 必查：

1. 首页、所有新增静态资源、原 `/legacy/`；冷加载、普通刷新、暖缓存、3Dmol 缺失/延迟时页面壳仍可用。
2. 任务列表/筛选/分页/详情、catalog/表单、原历史结果、结构/轨迹/振动与图形交互，覆盖该片实际触及范围。
3. 上传的 multipart 与文本响应；编辑重算不额外 POST 创建；候选保存及名称/组织/编辑的预期版本；任务 pause/unpause/continue/rerun/cancel 等原语义，用隔离任务验证。
4. 本地与适用远端请求超时、现有 fetch/获取行为、终态结果可用性、SSE 中断后的已覆盖/未覆盖资源刷新。
5. 迟到响应、共享取消、断线、401/403、409/422、服务错误、缺失字段和未知可选字段，验证不重复命令、不显空成功。
6. 回退一个前端片/完整前端资源集后原功能恢复；不需要恢复数据库或移动任务目录。

命令的网络失败不能证明未执行。没有服务幂等支持就禁止自动重发；先按已有任务/操作查询对账，无法确认时明确显示状态未知，不切 adapter 再发一次。

真实 API 与浏览器必须覆盖 F1＋S0 核心路径；mock 用于故障注入和契约边界，不能替代该门。真实科学软件/LSF 按适用环境分别记录，不为了验证模板抽取而重新跑全部科学算法。基线已有失败单列，不伪装为本片通过，也不顺手扩大成后端改造。

## 11. 静态发布、缓存、旧页面与回退

HTML、脚本、CSS 与启动装配组成一个前端交付资源集，记录引用和摘要。当前 `/js`、`/css` 的缓存校验机制继续沿用，但 `no-cache` 不会自动替换已运行页面，也不保证多文件更新原子性。

实施时在现有挂载范围内选择版本化资源引用或部署流程已有的一致发布方式：先备齐新资源再切入口；保留旧资源供已打开页面/回退使用。不新增必须的 manifest 后端 API、service worker、构建系统、Python 包依赖或静态挂载。没有可靠热切方式时，把受控维护窗口/完整资源替换和验证写清楚，不声称任意复制几份文件即可安全上线。

保留旧 `api*`/viewer 公开名称所需的窄转发，不能要求用户清空所有偏好/草稿才能启动。localStorage 的语言、布局、任务偏好等保留兼容读取；新局部缓存可失效，不能清空已存用户数据来掩盖不兼容。表单脏状态和已发命令不得因重连/重载自动再提交。

发布检查涵盖旧标签页仍打开、页面刷新、新标签页、资源延迟/加载失败、版本不匹配提示及恢复。静态加载失败不得静默降级到功能缩水的 minimal 页面。

回退恢复整个匹配资源集或本片验证过的匹配集合，不只退 HTML；任务、DB、科学结果、attempt 与后台运行保持。移除新 owner/引用后恢复原路径，仍保证一个资源只有一个活跃 owner。若 S1 同时已发布，其负责人必须保持 F0＋S1 可用。

每片兼容 shim 的删除条件同时满足：旧调用清单归零、动态/外部入口审计完成、旧前端/服务支持窗口结束、真实兼容矩阵通过、回退资源保留有替代。未满足时保留窄转发，不能保留第二完整实现。

最终交付分列：本轮独立重构的验收事实、后续能力待办、仍需保留的兼容面、未验证环境。不得把 AR-4 未交付误记为本轮前端未完成，也不得把未交付功能写成已支持。

### 11.1 并行实现与逐片接入

模块注册/导入不自动 fetch、建立 SSE、轮询、挂载 viewer 或写 DOM。请求由显式调用触发，刷新/实例由显式 start/mount 启用，停止和 dispose/destroy 可验证。这样新模块可先独立合入，原页面继续旧实现，不发生同资源双执行。

接管同一符号时：Antigravity 可以先 H2 原样外置完整旧脚本，但不再把已预留给你的 api*/renderer 等另拆成长期 feature 实现。你从冻结源提取到自己的目录，旧源仅在未切换期间作为当前运行实现；新模块隔离验证后，由 Antigravity 在接入片中删除旧实现或改为窄委托。未激活的新旧源暂存不能变成生产双缓存、双轮询或两套绘图。

每片按以下顺序交接，不等整个 H5 或 AR 全部完成：

1. 你提交接口/符号清单：冻结源摘要、目标模块、方法/错误/回调、依赖、初始化/停止、旧调用替换点。
2. 你在专属文件实现和测试，向 Antigravity 提供可核对的脚本加载顺序、初始化参数、接入补丁说明、适用 S0 验证与限制。
3. Antigravity 在可运行的页面片中引用新模块，保留 api 编辑重算拦截等上层行为；撤旧 owner 并删除或委托原实现，维护共享文本/浏览器测试的必要适配。
4. 双方验证同一 S0 下的真实默认页、请求/刷新唯一性、原行为/图形身份/错误、旧资源兼容与回退；不让 mock 通过替代实际接入。
5. 记录“模块已验证／原页面已接入／功能片已验收”三项状态。只完成第一项可以独立合入待启用模块，不能宣称迁移完成。
6. 你继续下一模块，Antigravity 同时拆其他未接管页面代码；本片失败只阻止本片激活，不强制两条开发线全部停下。

交接至少包含可运行模块、纯契约/样例版本、公开符号/脚本顺序、旧实现/定时器/缓存写者移除清单、测试/未测项与匹配资源回退步骤。接口改变保持旧消费方兼容或先双边更新契约，不能让 Antigravity 通过读取私有变量猜接线。

## 12. 可直接派发的提示词

```text
请与 Antigravity 同时开发 ACP 前端架构部分，不等待 HTML 全部拆分完成。

先读 AGENTS.md、tests/AGENTS.md，
docs/ACP_Architecture_Adjustment_Plan_20261016.md 第 38.6—38.10、40 节，及
docs/archive/2026-10-31/history/ACP_Frontend_Architecture_OpenCode_20261016.md。
第 40 节是 v1.12 并行写权与逐片接入的权威，覆盖此前默认先 H 后 AR 的安排。

Antigravity 独写主 HTML/CSS、旧页面 JS、workbench/compat/features/views、
字典/bootstrap 与 test_frontend_sync.py 等共享页面测试，并负责生产入口接线。
你默认只写 frontend/js/client/**、frontend/js/visualization/**、
frontend/js/workbench/{controllers,contracts}/** 以及新模块专属测试/纯契约样例。
旧文件只读；需要改主页面时提供逐符号接入清单/补丁文本，由 Antigravity 应用，
不要自己也修改入口然后留下合并冲突。后端/配置/DB/依赖/科学 fixtures 不改。

先保留当前未提交工作，从实际共同基线盘点符号、依赖、原行为与 S0 接口。
已有 frontend/js/client/transport.js，先审计并接续，不覆盖或新建第二套。
立即按 OC-0—OC-5 推进：
1. 最小纯契约/fixture 和符号接管清单；
2. S0 transport/协议 adapter，兼容超时/取消/错误/JSON/文本/FormData；
3. 任务列表/详情资源的 store、请求合并、选择/版本、订阅与刷新协调；
4. 一种二维图＋分子预览的实例 API、选择/framing/导出/销毁；
5. 注入 Client/Visualization/view callbacks 的任务/结果 controller；
6. 独立行为测试、真实 S0 验证、逐片接线/旧 owner 删除/回退交接包。

模块导入只注册公开能力，不自动联网/轮询/挂 viewer；副作用显式启用。
Client 不操作 DOM，Visualization 不联网或读全局任务，controller 不读页面私有 DOM。
原 api*、编辑重算 POST /jobs 拦截、写后失效、8s/60s 场景预算、
科学帧/原子/原生模式身份、energy viewer 仅保存候选及 framing 保持兼容。
不要求新 prepare/全局事件/科学产品先实现，不换框架/构建器，资源沿用 /js、/css。

每一小片模块就绪即交给 Antigravity 接入，不等全套完成。
Antigravity 接入时撤旧 timer/监听器/缓存写者和旧实现，仅留必要同实现窄委托；
你提供正确的新模块与接线要求，双方共同验证原页＋未修改 S0＋回退。
分别记录模块已验证、页面已接入、功能片已验收，不能以单测冒充原页面完成。
旧资源/命令断线/并发取消不重复提交或双刷新；F1+S0 是激活发布必过门。

交付专属文件、契约/样例、公开 API/加载顺序、旧符号替换与清理清单、
行为/接口/浏览器证据及回退步骤，实际未验证部分标 NOT_VERIFIED。
```
