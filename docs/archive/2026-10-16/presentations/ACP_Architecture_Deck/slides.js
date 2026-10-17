/*
 * ACP ARCHITECTURE DECK — editable slide content
 * Each object is one slide. Modify title, subtitle, html or notes directly.
 * No build step, framework, CDN, or external dependency is required.
 * status: "vision" = 目标愿景; "design" = 已确认设计 / 未实施; "baseline" = 文档中的代码清点; "candidate" = PoC 候选.
 */
window.ACP_SLIDES = [
  {
    id:'opening', section:'01 · 产品愿景', title:'重新定义计算化学工作台', subtitle:'', theme:'dark', kind:'cover', status:'vision',
    html:String.raw`
      <div class="cover-mark">ACP / MOLECULAR RESEARCH WORKBENCH</div>
      <h1 class="cover-title">从化学直觉<br>到<span>科学证据</span></h1>
      <p class="cover-sub">让反应机理研究不再从配置输入文件开始。<br>从绘制结构，到计算、诊断、分析与验证，构建完整科研闭环。</p>
      <div class="hero-bullets"><span>LOCAL FIRST</span><span>SCIENTIFICALLY GROUNDED</span><span>AGENT READY</span></div>
      <div class="cover-key"><i></i><span>产品战略 · 三层架构 · 迁移路线 · 实施蓝图</span></div>
      <div class="cover-version">DESIGN BLUEPRINT V1.5　 /　16 OCTOBER 2026</div>
      <svg class="cover-diagram" viewBox="0 0 610 610" aria-label="象征科学计算与科研流程的分子轨道图">
        <circle class="cover-orbit" cx="320" cy="300" r="239"/><circle class="cover-orbit" cx="320" cy="300" r="169"/><circle class="cover-orbit" cx="320" cy="300" r="111"/>
        <ellipse class="cover-orbit-thick" cx="320" cy="300" rx="275" ry="118" transform="rotate(-36 320 300)"/><ellipse class="cover-orbit" cx="320" cy="300" rx="130" ry="265" transform="rotate(38 320 300)"/>
        <g stroke="rgba(91,232,208,.4)" stroke-width="3" fill="none"><path d="M164 260L242 212L321 260L322 352L242 395L164 350Z"/><path d="M322 352L410 398L487 353"/><path d="M321 260L403 213L481 260L487 353L410 398L322 352"/></g>
        <g fill="none" stroke="#59cbb8" stroke-width="2"><path d="M181 272L243 233L304 269"/><path d="M405 234L469 270L473 341"/><path d="M243 375L179 339"/></g>
        <circle class="cover-node" cx="164" cy="260" r="10"/><circle class="cover-node-a" cx="242" cy="212" r="14"/><circle class="cover-node" cx="321" cy="260" r="11"/><circle class="cover-node-b" cx="322" cy="352" r="15"/><circle class="cover-node" cx="242" cy="395" r="9"/><circle class="cover-node-a" cx="403" cy="213" r="10"/><circle class="cover-node" cx="481" cy="260" r="11"/><circle class="cover-node-b" cx="487" cy="353" r="10"/><circle class="cover-node" cx="410" cy="398" r="11"/>
        <circle class="cover-node" cx="108" cy="172" r="7"/><circle class="cover-node-b" cx="525" cy="173" r="7"/><circle class="cover-node-a" cx="323" cy="538" r="8"/>
        <rect x="252" y="279" width="140" height="52" rx="15" class="cover-center"/><text x="322" y="303" text-anchor="middle" class="cover-small">ACP</text><text x="322" y="318" text-anchor="middle" class="cover-small-sub">SCIENTIFIC CORE</text>
      </svg>`,
    notes:'这一页是产品愿景，而不是已完成能力声明。演示基于 2026-10-16 六份 ACP 架构与产品设计文档，重点突出有机合成/机理研究者可用的图形化科研闭环。', source:'产品战略 · 完整方案 §30–35'
  },
  {
    id:'problem',section:'01 · 产品愿景',title:'最大的障碍，不是计算能力',subtitle:'真正阻碍非计算化学研究者的，是碎片化、不可解释的操作链路。',theme:'light',status:'vision',
    html:String.raw`<div class="comparison" style="position:relative;height:410px">
      <div class="panel" style="background:#fff5f0;border-color:#f0d8cd"><div class="eyebrow" style="color:#bb7064">TODAY / 传统操作链</div><h3 class="problem" style="font-size:29px;margin:22px 0">软件之间反复切换</h3>
        <div class="vstack" style="gap:11px"><div class="soft" style="background:#fff">手工搭建输入文件 <span style="float:right;color:#bb7166">01</span></div><div class="soft" style="background:#fff">泛函 / 基组 / 资源配置 <span style="float:right;color:#bb7166">02</span></div><div class="soft" style="background:#fff">终端 / 集群 / 作业排查 <span style="float:right;color:#bb7166">03</span></div><div class="soft" style="background:#fff">解析输出 / 绘图 / 比较 <span style="float:right;color:#bb7166">04</span></div></div>
        <p style="margin-top:auto;padding-top:17px;color:#a47772">复杂流程的每个环节都可能让新手止步。</p></div>
      <div class="panel" style="background:#e6faf3;border-color:#b3e7d9"><div class="eyebrow">TOMORROW / ACP 目标</div><h3 class="solution" style="font-size:29px;margin:22px 0">研究问题驱动的闭环</h3>
        <div class="vstack" style="gap:11px"><div class="soft" style="background:#fff">绘制结构与候选机理 <span style="float:right;color:#009784">01</span></div><div class="soft" style="background:#fff">由系统预检、解释参数 <span style="float:right;color:#009784">02</span></div><div class="soft" style="background:#fff">受控执行、失败诊断与恢复 <span style="float:right;color:#009784">03</span></div><div class="soft" style="background:#fff">证据比较与下一步修订 <span style="float:right;color:#009784">04</span></div></div>
        <p style="margin-top:auto;padding-top:17px;color:#147d70">把复杂性留在系统内部，把决策权交还研究者。</p></div>
      <div class="versus">VS</div></div>`,
    notes:'设计目的不是宣称所有科学参数都无须判断，而是把默认配置、兼容性预检、失败可解释性和科研证据整合为高质量用户流程；保留高级参数与原始文件入口。', source:'产品战略 §2/5 · 完整方案 §30/34'
  },
  {
    id:'promise',section:'01 · 产品愿景',title:'一句话定位，一套完整承诺',subtitle:'本地优先、面向分子科研的计算化学工作台。',theme:'dark',status:'vision',
    html:String.raw`<div class="grid3" style="margin-top:29px">
      <div class="panel" style="min-height:310px"><div class="icon-box teal">⌘</div><h3 style="font-size:29px;margin-top:30px">科学易用性</h3><p>从结构与问题开始；默认流程、预检与可解释错误降低使用门槛。</p><div class="tags"><span class="tag good">GUI · CLI</span><span class="tag">有高级模式</span></div></div>
      <div class="panel"><div class="icon-box blue">◎</div><h3 style="font-size:29px;margin-top:30px">证据连续性</h3><p>输入、计划、attempt、产物、人工选择与结论有迹可循。</p><div class="tags"><span class="tag good">可复核</span><span class="tag">可恢复</span></div></div>
      <div class="panel"><div class="icon-box amber">↗</div><h3 style="font-size:29px;margin-top:30px">开放可延展</h3><p>CCCP 独立开源，科研项目可迁移，可信插件和外部 Agent 可接入。</p><div class="tags"><span class="tag good">Local-first</span><span class="tag">Open science</span></div></div>
    </div><div class="callout" style="margin-top:23px;text-align:center"><strong>竞争力不来自“又一个提交按钮”，而来自贯通计算、失败、比较、修订和复核的科研工作流。</strong></div>`,
    notes:'差异化是组合性能力，尚未经过同场景竞品基准或访谈验证；产品不以所有软件数量、可视化菜单多少或调度器数量作为核心价值指标。', source:'产品战略 §2–4'
  },
  {
    id:'journey',section:'01 · 产品愿景',title:'面向科研，而不是面向输入文件',subtitle:'理想用户旅程：从一次化学判断开始，到一条可以被复核的证据链结束。',theme:'light',status:'vision',
    html:String.raw`<div class="swimlane" style="margin-top:49px">
      <div class="lane-node" style="min-height:160px"><span class="icon-box teal">✎</span><strong>1 / 绘制</strong><small>结构 · 反应 · 候选</small></div><div class="lane-arrow">→</div>
      <div class="lane-node"><span class="icon-box blue">◇</span><strong>2 / 提问</strong><small>路径 · 相对能量 · NMR</small></div><div class="lane-arrow">→</div>
      <div class="lane-node"><span class="icon-box teal">✓</span><strong>3 / 预检</strong><small>方法 · 软件 · 资源</small></div><div class="lane-arrow">→</div>
      <div class="lane-node"><span class="icon-box blue">⚙</span><strong>4 / 运行</strong><small>任务 · 监控 · 恢复</small></div><div class="lane-arrow">→</div>
      <div class="lane-node"><span class="icon-box amber">◉</span><strong>5 / 理解</strong><small>图形 · 比较 · 证据</small></div>
    </div><div class="hstack" style="margin-top:34px"><div class="panel grow tint-teal"><div class="eyebrow">SCIENTIFIC LOOP</div><h3>研究者选择 / 修订 / 再计算</h3><p>人工候选选择保留版本与来源；下一次计算需要显式提交，不由绘图组件隐式触发。</p></div><div style="width:110px;display:flex;align-items:center;justify-content:center;font-size:70px;color:#0a9e8b">⟲</div><div class="panel grow"><div class="eyebrow">EVIDENCE</div><h3>原始数据 → 科学产品 → 可复核报告</h3><p>完整保留软件版本、科学参数、失败原因和可比性限制。</p></div></div>`,
    notes:'这是一条目标用户旅程。科学是否“适用”、能量是否“可比”、结果是否“证实机理”都不能由动画或 Agent 模糊替代；必须有科学约束和证据。', source:'产品战略 §2/5 · 完整方案 §32/34'
  },
  {
    id:'early-flows',section:'01 · 产品愿景',title:'先打透两条高频科研闭环',subtitle:'不先做万能工作流编辑器，也不一次性承诺覆盖所有化学领域。',theme:'light',status:'design',
    html:String.raw`<div class="grid2" style="margin-top:23px"><div class="panel" style="border-top:5px solid #17bfa7;min-height:312px"><div class="stage-chip">SCENARIO 01 · 首要交付</div><h3 style="font-size:28px">结构 → 计算 → 解释</h3>
      <div class="swimlane" style="margin:31px 0 24px"><div class="lane-node compact"><strong>Structure</strong><small>绘制 / 输入</small></div><div class="lane-arrow">›</div><div class="lane-node compact"><strong>Opt / Freq</strong><small>运行 / 恢复</small></div><div class="lane-arrow">›</div><div class="lane-node compact accent"><strong>Evidence</strong><small>报告 / 比较</small></div></div><p>交付标准：新用户能在支持环境中，不编脚本完成一次有效任务。</p></div>
      <div class="panel" style="border-top:5px solid #7186ef"><div class="stage-chip">SCENARIO 02 · 紧接着</div><h3 style="font-size:28px">构象搜索 → 选择 → 批处理</h3><div class="swimlane" style="margin:31px 0 24px"><div class="lane-node compact"><strong>Confsearch</strong><small>结构集合</small></div><div class="lane-arrow">›</div><div class="lane-node compact"><strong>Selection</strong><small>人工候选</small></div><div class="lane-arrow">›</div><div class="lane-node compact accent"><strong>Batch Opt</strong><small>热化学比较</small></div></div><p>交付标准：候选来源可追溯，能量比较有单位、方法和参考态检查。</p></div></div>
    <div class="flow-footer" style="margin-top:20px">随后加深：<strong>PES / TS / IRC</strong> 的路径证据，以及 <strong>NMR</strong> 研究闭环。均在科学可验证和用户价值通过后逐步推进。</div>`,
    notes:'M0 固定场景、软硬件条件和成功判据，M1 首先提供第一个完整流程，M2 第二场景与迁移、MCP 等能力；PES/TS/IRC 和 NMR 是后续纵深。', source:'产品战略 §2/7 · 完整方案 §34–35'
  },
  {
    id:'chapter-architecture',section:'02 · 架构总览',title:'一种产品，三层职责',subtitle:'从页面交互到平台控制，再到科学计算：能力集中、边界清晰。',theme:'dark',kind:'chapter',status:'design',
    html:String.raw`<div class="chapter-accent"></div><div class="chapter-cover"><div class="chapter-num">CHAPTER TWO / SYSTEM DESIGN</div><h1>三层架构<br>九个主要模块</h1><p>每层三个模块，每个领域一个唯一权威写入者。<br>模块是职责边界，而不是九个微服务。</p><div class="chapter-underline"></div></div>`,
    notes:'目标架构按三层各三个高内聚模块计数，支撑代码、接口、查询和后台角色是内部组件，不另成新的业务模块。设计未实施迁移。', source:'完整方案 §0–3 · 高内聚模块设计 §1'
  },
  {
    id:'three-tier',section:'02 · 架构总览',title:'ACP / ACR / CCCP 三层九模块',subtitle:'点击任意模块，可查看职责与禁止跨越的边界。',theme:'light',status:'design',
    html:String.raw`<div style="position:relative;margin-top:-2px">
      <div class="arch-row r-acp"><div class="arch-label"><div class="lab">LAYER 01</div><strong>ACP</strong><span>前端科研工作台</span></div><div class="arch-units">
        <div class="arch-unit clickable" tabindex="0" role="button" data-inspect="workbench"><strong>Workbench</strong><small>页面 · 功能用例 · 用户意图</small></div><div class="arch-unit clickable" tabindex="0" role="button" data-inspect="client"><strong>Client</strong><small>HTTP/SSE · 查询 · 缓存 · 刷新</small></div><div class="arch-unit clickable" tabindex="0" role="button" data-inspect="acv"><strong>ACV</strong><small>2D · 3Dmol · 动画 · 导出</small></div>
      </div></div>
      <div style="text-align:center;font-size:19px;color:#609f9a;height:24px">↓ <span style="font-size:11px;letter-spacing:.15em">VERSIONED API / COMMAND / EVENTS</span> ↓</div>
      <div class="arch-row r-acr"><div class="arch-label"><div class="lab">LAYER 02</div><strong>ACR</strong><span>控制与资源运行时</span></div><div class="arch-units">
        <div class="arch-unit clickable" tabindex="0" role="button" data-inspect="workspace"><strong>Workspace</strong><small>项目 · 身份 · 授权 · Credit</small></div><div class="arch-unit clickable" tabindex="0" role="button" data-inspect="runtime"><strong>Runtime</strong><small>任务 · 编排 · 派发 · 恢复</small></div><div class="arch-unit clickable" tabindex="0" role="button" data-inspect="data"><strong>Data</strong><small>工件 · 传输 · 缓存 · 发布</small></div>
      </div></div>
      <div style="text-align:center;font-size:19px;color:#609f9a;height:24px">↓ <span style="font-size:11px;letter-spacing:.15em">PUBLIC SCIENCE API / TASK REQUEST</span> ↓</div>
      <div class="arch-row r-cccp"><div class="arch-label"><div class="lab">LAYER 03</div><strong>CCCP</strong><span>科学计算与数据处理</span></div><div class="arch-units">
        <div class="arch-unit clickable" tabindex="0" role="button" data-inspect="calculation"><strong>Calculation</strong><small>唯一科学任务 · 证据</small></div><div class="arch-unit clickable" tabindex="0" role="button" data-inspect="analysis"><strong>Analysis</strong><small>数值分析 · 规范化产品</small></div><div class="arch-unit clickable" tabindex="0" role="button" data-inspect="software"><strong>Software</strong><small>引擎 · 原生 I/O · 解析适配</small></div>
      </div></div>
      <div class="flow-footer">核心约束：<strong>Workbench → Client / ACV；Runtime → Data / Workspace / CCCP；Data → Workspace；CCCP 不反向依赖平台。</strong></div>
    </div>`,
    notes:'这张是目标依赖图的空间化表达：Client 不依赖 ACV，ACV 不访问网络；Runtime 通过公开入口调用 Data/Workspace/CCCP。点击模块可查看详细职责。', source:'完整方案 §0/3 · 高内聚模块设计 §2–5'
  },
  {
    id:'layers-value',section:'02 · 架构总览',title:'把复杂性放在正确的地方',subtitle:'每层负责一类不同的问题，避免重复实现、反向调用和跨层“越权”。',theme:'dark',status:'design',
    html:String.raw`<div class="grid3" style="margin-top:32px"><div class="panel" style="border-top:5px solid #50dcc6;min-height:322px"><div class="eyebrow">01 / EXPERIENCE</div><h3 style="font-size:31px;margin:23px 0 17px">ACP</h3><p>用户看到什么、选择什么、如何操作和查看图形。</p><div class="rule"></div><div class="tag good">不决定科学计算结果</div><div class="tags"><span class="tag">不直接解析 QC</span><span class="tag">不保存权威任务</span></div></div>
      <div class="panel" style="border-top:5px solid #859dff"><div class="eyebrow">02 / ORCHESTRATION</div><h3 style="font-size:31px;margin:23px 0 17px">ACR</h3><p>谁可以运行、如何持久化任务、怎样传输与恢复。</p><div class="rule"></div><div class="tag good">唯一平台事实与资源边界</div><div class="tags"><span class="tag">不重做数值分析</span><span class="tag">不自行解析科学输出</span></div></div>
      <div class="panel" style="border-top:5px solid #ffc681"><div class="eyebrow">03 / SCIENCE</div><h3 style="font-size:31px;margin:23px 0 17px">CCCP</h3><p>科学任务如何计算、程序原生格式如何转为规范化科学产物。</p><div class="rule"></div><div class="tag good">唯一科学执行核心</div><div class="tags"><span class="tag">无身份与账务依赖</span><span class="tag">无 Web 依赖</span></div></div></div>
      <div class="callout" style="margin-top:26px;text-align:center;font-size:19px">三层是<strong>职责</strong>，不是三个进程、三台服务器或三个强制独立部署包。</div>`,
    notes:'清晰层级是为了减少多重责任，保留 CCCP 独立可用。分层之后仍可同仓同进程运行，实际是否分布式由部署需求决定。', source:'三层主设计 §1/3 · 完整方案 §3'
  },
  {
  "id": "architecture-bridge",
  "section": "02 · 架构总览",
  "title": "现有执行分层与目标产品分层",
  "subtitle": "四层执行纪律继续保留，三层九模块补充产品所有权与公开接口",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>比较维度</th><th>当前代码事实</th><th>本轮目标设计</th></tr></thead><tbody><tr><td>产品边界</td><td>acp 同时承载调度、科学分析和发布</td><td>ACP 前端 / ACR 控制与数据 / CCCP 科学</td></tr><tr><td>执行依赖</td><td>acp 规划发布调用 cccp.calculation，经 backends 到 qc</td><td>Runtime 科学 adapter 接入原有唯一任务根</td></tr><tr><td>科学职责</td><td>部分 NMR、PES、结构摄入仍在 acp</td><td>逐项移交 CCCP Analysis / IO</td></tr><tr><td>完成条件</td><td>任务核心迁移已在 2026-10-05 收口</td><td>ACR 新边界与独立 worker 逐切片验收</td></tr></tbody></table><div class=\"flow-footer\">阅读顺序：确定数据 owner，展开公开调用，再用关键文件和验收场景证明边界。</div></div>",
  "notes": "已收口的是任务核心迁移，2026-10-16 的产品拆分仍待实施。两者不是同一次迁移。三层产品设计保留 CCCP 内部执行分层。",
  "source": "主方案 §3/6/12 · 当前任务核心与 shim"
},
  {
    id:'frontend',section:'02 · 架构总览',title:'前端：让用户操作和网络、绘图解耦',subtitle:'三个模块，三种职责；数据缓存由 Client 独占，三维句柄由 ACV 独占。',theme:'light',status:'design',
    html:String.raw`<div class="grid3" style="margin-top:23px"><div class="panel" style="min-height:317px"><span class="stage-chip">WORKBENCH</span><div class="icon-box teal">▦</div><h3>交互入口</h3><p>导航、表单、选择联动、错误呈现和完整用户动作。</p><div class="tags"><span class="tag">用户意图</span><span class="tag">页面状态</span></div></div>
      <div class="panel"><span class="stage-chip">CLIENT</span><div class="icon-box blue">⇄</div><h3>统一数据客户端</h3><p>类型化查询/命令、ResourceStore、共享请求与统一事件订阅。</p><div class="tags"><span class="tag">HTTP / SSE</span><span class="tag">缓存 / 版本</span></div></div>
      <div class="panel"><span class="stage-chip">ACV</span><div class="icon-box amber">◈</div><h3>可视化库</h3><p>二维图、3Dmol 适配、动画与本地导出；实例化、更新与销毁。</p><div class="tags"><span class="tag">setData</span><span class="tag">destroy</span></div></div></div>
      <div class="flow-band" style="margin-top:19px"><div class="lab">STATIC DEPENDENCIES</div><div style="display:flex;align-items:center;justify-content:center;gap:30px;margin:13px 0"><strong style="font-size:23px">Workbench</strong><span class="big-arrow">→</span><span class="tag" style="font-size:18px">Client</span><span class="big-arrow">＋</span><span class="tag" style="font-size:18px">ACV</span></div><p style="text-align:center">Client 与 ACV 彼此独立 · 选点回调先回 Workbench · 不在图形中提交科学计算</p></div>`,
    notes:'页面/feature 可以直接调用 client 和 acv 的公开 API。前端 RequestStore/刷新应统一，减少 API 轮询压力与面板重复请求；ACV 只负责显示不持有权威研究数据。', source:'高内聚模块设计 §2 · 模块接口 §2'
  },
  {
  "id": "frontend-contract",
  "section": "02 · 架构总览",
  "title": "一次选帧交互的完整调用关系",
  "subtitle": "ACV 回调 frame_id，Workbench 协调用例，Client 取数，再由 feature 调 ACV.setData",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>顺序 / 动作</th><th>调用与返回</th><th>实现目标</th></tr></thead><tbody><tr><td>01 选点 / 选帧</td><td>ACV 回调 point_id / frame_id 给 feature</td><td>不读取全局 selectedJob，不直接发请求</td></tr><tr><td>02 获取结构</td><td>feature 调 Client.query，Data 返回授权数据块</td><td>Client 去重、比较版本并写共享缓存</td></tr><tr><td>03 数据未准备</td><td>feature 调 Client.prepare，返回 OperationReceipt</td><td>后台持久传输，查询返回可用性</td></tr><tr><td>04 保存候选</td><td>feature 发候选命令，携带来源与 revision</td><td>保存回执后刷新共享资源与视图</td></tr><tr><td>05 切换 / 关闭</td><td>取消旧订阅，调用 ACV.destroy</td><td>旧响应不覆盖新任务，释放动画与句柄</td></tr></tbody></table><div class=\"flow-footer\">数据返回由 feature 接续到 ACV。Client 与 ACV 无静态依赖，保存候选后的新计算经独立提交用例发起。</div></div>",
  "notes": "接口名表示目标接口族，不代表现行已发布的 JS 签名。交互回调是控制返回，不是反向 import。",
  "source": "模块接口 §2/6/7.4"
},
  {
    id:'acv',section:'02 · 架构总览',title:'ACV 是“画图的人”，不是另一套应用',subtitle:'3Dmol 是 ACV 内部 adapter；科学全量数据由 CCCP 处理后再交付前端。',theme:'light',status:'design',
    html:String.raw`<div class="two-tier" style="margin-top:21px"><div class="panel" style="padding:28px 31px"><div class="eyebrow">RENDERER PIPELINE</div><div class="swimlane" style="margin:24px 0 24px"><div class="lane-node accent"><strong>Scientific Product</strong><small>单位 / ID / 映射</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>Display Model</strong><small>限量显示 / 选择</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>Renderer</strong><small>2D / 3Dmol</small></div></div>
          <div class="grid2"><div class="soft"><b>二维产品</b><p>曲线、散点、路径、谱图、交互标记</p></div><div class="soft"><b>三维产品</b><p>分子结构、轨迹、振动、相机与动画</p></div></div><div class="note-box" style="margin-top:20px">公开实例 API：create · setData · setSelection · resize · export · destroy</div></div>
        <div class="vstack"><div class="panel tint-teal"><div class="eyebrow">YES</div><h3>只能做显示工作</h3><p>主题、图例、相机、选帧、临时交互、图片导出。</p></div><div class="panel" style="background:#fff3ea;border-color:#f4dec9"><div class="eyebrow" style="color:#b17c50">NO</div><h3>不能隐式触发科研任务</h3><p>不 fetch、不解析原始 QC、不更改候选权威文件、不改任务状态。</p></div></div></div>`,
    notes:'现有 SVG/3Dmol 是可复用基线；目标是封装而不是先更换渲染引擎。现有无浏览器自动报告 JSON/XLSX/PNG 必须继续兼容，不因 ACV 前端化而删除。', source:'三层主设计 §4/6 · 完整方案 §4/7'
  },
  {
    id:'acr',section:'02 · 架构总览',title:'ACR：三位领域所有者，各有写权',subtitle:'一个模块对一个完整流程负责，后台适配器不再成为第四个“超级管理器”。',theme:'dark',status:'design',
    html:String.raw`<div class="grid3" style="margin-top:22px"><div class="panel" style="min-height:344px"><span class="eyebrow">01 · ACCESS & ORGANIZATION</span><h3 style="font-size:29px;margin-top:19px">Workspace</h3><p>项目、成员、身份/角色、策略、个人 API Key、Credit 账户。</p><div class="rule"></div><div class="lab">唯一写权</div><div class="tags"><span class="tag">项目组织</span><span class="tag">授权</span><span class="tag">账本</span></div></div>
      <div class="panel"><span class="eyebrow">02 · LIFECYCLE & EXECUTION</span><h3 style="font-size:29px;margin-top:19px">Runtime</h3><p>计划冻结、作业/attempt、派发、控制、恢复、资源预算与用量。</p><div class="rule"></div><div class="lab">唯一写权</div><div class="tags"><span class="tag">任务生命周期</span><span class="tag">操作</span><span class="tag">回执</span></div></div>
      <div class="panel"><span class="eyebrow">03 · ARTIFACT & AVAILABILITY</span><h3 style="font-size:29px;margin-top:19px">Data</h3><p>输入资产、工件/manifest、传输、缓存、来源与候选。</p><div class="rule"></div><div class="lab">唯一写权</div><div class="tags"><span class="tag">工件登记</span><span class="tag">缓存发布</span><span class="tag">候选</span></div></div></div>
      <div class="callout" style="margin-top:25px;text-align:center;font-size:19px">允许的调用方向：<strong>Runtime → Data → Workspace</strong>，Runtime 也可直接通过公开接口查询 Workspace。</div>`,
    notes:'Workspace 不查询 Runtime 私有表；Data 不反调 Runtime 发起科学任务；Runtime 不直接写 Data 私有 manifest。三者依赖清晰，跨域流程由拥有该用例的模块协调。', source:'高内聚模块设计 §3 · 完整方案 §5'
  },
  {
    id:'runtime',section:'02 · 架构总览',title:'Runtime：两种运行角色，一套任务真相',subtitle:'controller 持有任务生命周期；plan_runner 执行冻结计划，返回可验证事实。',theme:'light',status:'design',
    html:String.raw`<div class="grid2" style="margin-top:13px"><div class="panel" style="border-top:5px solid #6d8ce9;min-height:241px"><div class="eyebrow">CONTROL PLANE</div><h3 style="font-size:27px">Controller</h3><p>资源准入、提交身份、lease / CAS、外部观测对账；确认停止并守护用户控制意图。</p><div class="tags"><span class="tag">唯一生命周期 owner</span></div></div>
    <div class="panel" style="border-top:5px solid #22b9a7"><div class="eyebrow">EXECUTION PLANE</div><h3 style="font-size:27px">Plan Runner</h3><p>按冻结计划处理依赖、步骤恢复与科学 API 调用；只返回步骤/执行回执。</p><div class="tags"><span class="tag">本地 / 集群 worker</span></div></div></div>
    <div class="flow-band" style="margin-top:17px"><div class="swimlane"><div class="lane-node compact accent"><strong>Controller</strong><small>冻结 plan + 派发</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>ExecutionEnvelope</strong><small>attempt / release / 身份</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>Plan Runner</strong><small>调用 CCCP Calculation</small></div><div class="lane-arrow">→</div><div class="lane-node compact accent"><strong>ExecutionReceipt</strong><small>事实 / 证据 / 状态</small></div></div></div>
    <div class="note-box" style="margin-top:16px">worker 不直接连接平台 jobs 数据库。旧 attempt、旧 release、旧 submission 的回执不得覆盖当前事实。</div>`,
    notes:'本机与 LSF/远端是 ExecutionBackend adapter，不能绕 controller 操纵 JobStatus。ExecutionEnvelope 不序列化本机 TaskContext 回调，科学请求也不携带平台身份。', source:'模块接口 §3.1/5 · 完整方案 §5/7/8'
  },
  {
  "id": "execution-contract",
  "section": "02 · 架构总览",
  "title": "平台执行协议与科学任务协议",
  "subtitle": "保留 TaskRequest / TaskResult，外层派发协议单独承载平台身份",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>边界与文件</th><th>核心内容</th><th>校验与返回</th></tr></thead><tbody><tr><td>目标 Runtime contracts<br>ExecutionEnvelope</td><td>schema、job / attempt / submission / release、fencing、冻结计划、输入摘要、预算</td><td>worker 校验协议、输入与执行版本</td></tr><tr><td>现有 calculation/requests.py<br>TaskRequest</td><td>科学输入、任务类型、方法、类型化 options、资源</td><td>workflow / candidate 身份留在平台外层</td></tr><tr><td>现有 calculation/context.py<br>TaskContext</td><td>workdir、config、progress、timeout_s、cancelled</td><td>本机注入回调，不序列化跨网</td></tr><tr><td>现有 calculation/results.py<br>TaskResult</td><td>科学结果、complete、错误、工件、provenance</td><td>worker 汇总回执，controller 显式映射 JobStatus</td></tr></tbody></table><div class=\"flow-footer\">回执接纳：attempt、submission、release 与当前派发一致，且必需步骤和工件完成事实有效。</div></div>",
  "notes": "requests/results/context 位于 src/cccp/calculation。ExecutionEnvelope/Receipt 为目标协议，字段与兼容版本需 P4-04 冻结。complete=False 的部分科学输出不等同平台 COMPLETED。",
  "source": "主方案 §7.2 · requests/results/context.py"
},
  {
    id:'data',section:'02 · 架构总览',title:'Data：数据可用性必须是一条持久流程',subtitle:'“结果已算出”≠“已经传回”≠“用户可以查看”。',theme:'light',status:'design',
    html:String.raw`<div class="swimlane" style="margin-top:55px"><div class="lane-node"><div class="flow-num">01</div><strong>Register</strong><small>工件登记 / 来源</small></div><div class="lane-arrow">→</div><div class="lane-node"><div class="flow-num">02</div><strong>Prepare</strong><small>显式准备 / 入队</small></div><div class="lane-arrow">→</div><div class="lane-node"><div class="flow-num">03</div><strong>Transfer</strong><small>SSH / SFTP / 断点恢复</small></div><div class="lane-arrow">→</div><div class="lane-node"><div class="flow-num">04</div><strong>Verify</strong><small>摘要 / generation</small></div><div class="lane-arrow">→</div><div class="lane-node accent"><div class="flow-num">05</div><strong>Publish</strong><small>原子可见 / availability</small></div></div>
      <div class="grid3" style="margin-top:37px"><div class="soft"><b>查询不引发传输</b><p>GET 只读取已授权、已就绪的快照或明确 unavailable。</p></div><div class="soft"><b>队列能够恢复</b><p>持久操作、租约、退避、取消、重启重放与去重。</p></div><div class="soft"><b>旧结果不能污染新结果</b><p>attempt / generation / 摘要校验后才能发布到当前缓存。</p></div></div>`,
    notes:'把 cache-only GET 与 prepare/持久传输/状态恢复联动交付，不可单独禁止远端 fetch 造成老任务不可见。科学派生产品由 Runtime 显式发起 CCCP 任务，Data 不能在 getter 里自动计算。', source:'完整方案 §5/8/14 · 高内聚模块设计 §3.3'
  },
  {
  "id": "data-view-contract",
  "section": "02 · 架构总览",
  "title": "结果从磁盘到视图的文件接力",
  "subtitle": "科学工件经发布登记、结果读取和帧投影，再交 Client 与 ACV",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>接力环节 / 关键文件</th><th>当前内容</th><th>迁移目标</th></tr></thead><tbody><tr><td>01 CCCP calculation/tasks/</td><td>科学计算、完成证据、原始与结构化工件</td><td>Analysis 增量提供标准科学产品</td></tr><tr><td>02 acp/storage/manifest.py</td><td>写入 result_manifest v2</td><td>Data 独占平台发布与可用性</td></tr><tr><td>03 acp/results/manifest.py</td><td>读取已发布结果与引用</td><td>只读查询配套 prepare / 持久传输</td></tr><tr><td>04 acp/results/frames.py</td><td>VIEW_REGISTRY、to_node() / to_annotation()</td><td>保留投影兼容，科学语义归 CCCP</td></tr><tr><td>05 frame_candidates.json</td><td>人工候选的权威文件</td><td>Data 保留来源、revision 与选择事实</td></tr></tbody></table><div class=\"flow-footer\">远端缓存当前驻点为 results/remote_structure_cache.py；目标增加 generation 校验，未准备数据通过命令处理。</div></div>",
  "notes": "源码路径相对 src/。当前查询并非全部 cache-only，prepare 与后台传输应配套切换。CCCP 不导入 acp.results.frames。",
  "source": "主方案 §7.3/8.3 · manifest/frames 契约"
},
  {
    id:'science',section:'02 · 架构总览',title:'CCCP：核心科学能力必须独立完整',subtitle:'保留现有 TaskRequest / TaskResult / TaskContext 与唯一任务根，插件化不等于把科学内核掏空。',theme:'dark',status:'design',
    html:String.raw`<div class="grid3" style="margin-top:28px"><div class="panel" style="min-height:332px;border-top:5px solid #ffc484"><div class="icon-box amber">⚗</div><h3 style="font-size:28px">Calculation</h3><p>稳定科学入口、原子级/步骤级任务、完成证据、取消和超时。</p><div class="tags"><span class="tag good">唯一 Tasks Root</span></div></div>
      <div class="panel" style="border-top:5px solid #7fe6d5"><div class="icon-box teal">∑</div><h3 style="font-size:28px">Analysis</h3><p>科学模型、PES / NMR / 采样算法、规范化与大数据抽样产物。</p><div class="tags"><span class="tag good">有单位 / 来源</span></div></div>
      <div class="panel" style="border-top:5px solid #9daeff"><div class="icon-box blue">⌥</div><h3 style="font-size:28px">Software</h3><p>程序发现、原生输入输出、程序能力、受控 parser 与执行 adapter。</p><div class="tags"><span class="tag good">软件差异收敛</span></div></div></div>
      <div class="callout" style="margin-top:23px;text-align:center;font-size:18px"><strong>Calculation → Analysis / Software</strong>，Analysis 可使用 Software 的只读 parser；CCCP 不依赖账号、商业 Credit 或浏览器。</div>`,
    notes:'QCEngine、QCElemental、cclib 可以作为受控候选 adapter，不是对现有科学实现的全量替代。单个软件的额外特性必须通过 typed capability 处理，不可静默丢失。', source:'完整方案 §6/31 · 开源复用评估 §2'
  },
  {
    id:'scientific-products',section:'02 · 架构总览',title:'统一“科学产品”，而非统一所有原始文件',subtitle:'科学身份、单位、来源与不确定性必须随数据穿过 CCCP → ACR → ACP。',theme:'light',status:'design',
    html:String.raw`<div class="grid4" style="margin-top:23px"><div class="panel tight"><div class="eyebrow">SERIES</div><h3>曲线 / 散点</h3><svg viewBox="0 0 220 95" width="100%" height="95" aria-hidden="true"><path d="M8 8V83H213" stroke="#bad6d5" stroke-width="2" fill="none"/><path d="M12 67Q55 70 85 49T140 30T212 10" stroke="#13ac9c" fill="none" stroke-width="3"/><g fill="#16b4a0"><circle cx="12" cy="67" r="5"/><circle cx="85" cy="49" r="5"/><circle cx="140" cy="30" r="5"/><circle cx="212" cy="10" r="5"/></g></svg><p class="small">point_id · x/y 单位 · 参考态</p></div>
      <div class="panel tight"><div class="eyebrow">STRUCTURE</div><h3>结构 / 轨迹</h3><svg viewBox="0 0 220 95" width="100%" height="95" aria-hidden="true"><g stroke="#8aa6aa" stroke-width="7"><line x1="32" y1="60" x2="80" y2="23"/><line x1="80" y1="23" x2="130" y2="61"/><line x1="130" y1="61" x2="175" y2="18"/></g><g fill="#13ad97"><circle cx="32" cy="60" r="16"/><circle cx="130" cy="61" r="17"/></g><g fill="#8b9bff"><circle cx="80" cy="23" r="14"/><circle cx="175" cy="18" r="18"/></g></svg><p class="small">atom_uid · frame_id · 拓扑来源</p></div>
      <div class="panel tight"><div class="eyebrow">VIBRATION</div><h3>振动模式</h3><svg viewBox="0 0 220 95" width="100%" height="95" aria-hidden="true"><g stroke="#9fabae" stroke-width="6"><path d="M35 49L94 49L157 49"/></g><g fill="#658bef"><circle cx="35" cy="49" r="16"/><circle cx="157" cy="49" r="17"/></g><circle cx="94" cy="49" r="12" fill="#1aa997"/><g stroke="#f3ac5d" stroke-width="4" fill="none"><path d="M25 20L47 20M41 13L48 20L41 27M147 77L168 77M163 70L170 77L163 84"/></g></svg><p class="small">原生模式号 · 向量 · 顺序映射</p></div>
      <div class="panel tight"><div class="eyebrow">LARGE DATA</div><h3>分块 / 抽样</h3><svg viewBox="0 0 220 95" width="100%" height="95" aria-hidden="true"><g fill="#c1d8d5">${Array.from({length:36},(_,i)=>`<circle cx="${16+i%12*17}" cy="${16+Math.floor(i/12)*30+(i%4)*3}" r="2.8"/>`).join('')}</g><g fill="#0cafa0"><circle cx="16" cy="16" r="5"/><circle cx="84" cy="27" r="5"/><circle cx="169" cy="49" r="5"/><circle cx="203" cy="88" r="5"/></g></svg><p class="small">算法/范围/分辨率 · 原点映射</p></div></div>
      <div class="flow-footer" style="margin-top:24px">Software 保留 ORCA / xTB / CREST 等程序特有格式；CCCP 输出标准化科学语义；<strong>ACV 不得把抽样后的显示数据当成完整科学结果。</strong></div>`,
    notes:'本页产品是目标契约，不代表每个程序已覆盖所有字段。缺失、未准备、解析失败、科学不适用必须区分；不能统一填 0。', source:'完整方案 §7.3 · 模块接口 §6'
  },
  {
    id:'entrypoints',section:'03 · 流程与 Agent',title:'一个 Runtime，多种可信入口',subtitle:'GUI、CLI、MCP/Agent 进入同一套授权、预算、任务状态与科学结果。',theme:'dark',status:'design',
    html:String.raw`<div class="grid3" style="margin-top:8px"><div class="panel tight"><div class="icon-box teal">▣</div><h3>Web GUI</h3><p>研究者可视操作<br>Workbench + Client + ACV</p></div><div class="panel tight"><div class="icon-box blue">⌘</div><h3>CLI</h3><p>脚本 / 批量 / 终端<br>不依赖 Web 与浏览器</p></div><div class="panel tight"><div class="icon-box amber">◇</div><h3>MCP / Agent</h3><p>外部智能体的薄工具接口<br>能力发现 / 调用 / 结果检索</p></div></div>
      <div style="text-align:center;font-size:24px;color:#67e2d0;margin:0">↘　　　　　　　　 ↓　　　　　　　　 ↙</div>
      <div class="panel accent-panel" style="text-align:center;padding:17px 24px"><div class="eyebrow" style="color:#80eedf">SINGLE SOURCE OF TRUTH</div><h3 style="font-size:28px;margin:10px">ACR · Workspace / Runtime / Data</h3><p>身份授权 · 显式计划 · 资源预算 · 幂等命令 · 恢复 · 工件证据</p></div>
      <div style="text-align:center;font-size:23px;color:#65e2d0;height:24px">↓</div>
      <div class="panel" style="text-align:center;padding:13px"><strong style="font-size:22px;color:#ffcb90">CCCP · Calculation / Analysis / Software</strong><p class="small" style="margin-top:5px">独立科学计算核心：不区分 GUI、CLI 和 Agent 的算法分支</p></div>`,
    notes:'MCP 是将 ACR 现有/目标 use case 暴露给外部 Agent 的可控入口，而不是 ACP 自己托管 LLM 或替代科学控制器。tools/list 和 tools/call 是协议目标方法，具体兼容矩阵要实测。', source:'完整方案 §18–19/33'
  },
  {
    id:'agent-loop',section:'03 · 流程与 Agent',title:'未来 Agent：推动研究，不绕过科学规则',subtitle:'Agent 负责建议与调用；科学计算、权限预算和完成判据仍由确定性内核负责。',theme:'light',status:'vision',
    html:String.raw`<div class="two-tier" style="margin-top:16px"><div class="panel"><div class="eyebrow">EXTERNAL RESEARCH AGENT / FUTURE DIRECTION</div><div class="swimlane" style="margin:22px 0"><div class="lane-node compact"><strong>科学问题</strong><small>结构 / 假说</small></div><div class="lane-arrow">›</div><div class="lane-node compact"><strong>方案建议</strong><small>路径 / 方法</small></div><div class="lane-arrow">›</div><div class="lane-node compact accent"><strong>受控调用</strong><small>预检 / dry-run</small></div></div><div class="swimlane"><div class="lane-node compact"><strong>证据回看</strong><small>误差 / 来源</small></div><div class="lane-arrow">←</div><div class="lane-node compact"><strong>运行与恢复</strong><small>Runtime / CCCP</small></div><div class="lane-arrow">←</div><div class="lane-node compact"><strong>人工授权</strong><small>预算 / 风险</small></div></div><div class="note-box" style="margin-top:21px">下一轮研究由证据、失败原因与用户决策驱动，不由 LLM 直接篡改任务或科学结论。</div></div>
       <div class="vstack"><div class="panel tint-teal"><div class="eyebrow">AGENT CAN</div><h3>发现、解释、提出方案</h3><p>查询能力、预检输入、建议候选计算与读取结构化证据。</p></div><div class="panel" style="background:#fff1e8;border-color:#f0d2bc"><div class="eyebrow" style="color:#bc7752">AGENT CANNOT</div><h3>绕开后端规则</h3><p>不能任意执行 shell、无限重试、无预算派发、伪造 completed。</p></div></div></div>`,
    notes:'本页是未来产品愿景（与近期 PES/TS/IRC 纵深互补），并非自主研究 Agent 已经开发完成；主方案明确不在第一阶段承诺无限自主 Agent 或托管 LLM。', source:'完整方案 §19.2/33–34 · 产品战略 §5'
  },
  {
    id:'command-query',section:'03 · 流程与 Agent',title:'查询、命令、事件：三条分离的通道',subtitle:'高并发和可靠性改善的关键，是让“看一眼状态”不等于“启动一次操作”。',theme:'light',status:'design',
    html:String.raw`<div class="vstack" style="gap:16px;margin-top:21px"><div class="flow-band" style="background:#e4f7f1"><div class="grid3" style="align-items:center;grid-template-columns:195px 1fr 255px"><div><div class="eyebrow">QUERY / GET</div><h3 style="margin:6px 0">读取事实</h3></div><div class="swimlane"><div class="lane-node compact"><strong>Client.query</strong><small>请求合并</small></div><div class="lane-arrow">→</div><div class="lane-node compact accent"><strong>只读快照</strong><small>revision / freshness</small></div></div><div class="soft" style="background:#fff"><b>不触发 SSH / SFTP / QC</b><p>没就绪就返回 availability</p></div></div></div>
      <div class="flow-band" style="background:#edf0ff"><div class="grid3" style="align-items:center;grid-template-columns:195px 1fr 255px"><div><div class="eyebrow">COMMAND / POST</div><h3 style="margin:6px 0">显式动作</h3></div><div class="swimlane"><div class="lane-node compact"><strong>Client.command</strong><small>幂等 / 预算</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>Runtime / Data</strong><small>受理 / 持久操作</small></div></div><div class="soft" style="background:#fff"><b>202 ≠ 已完成</b><p>用 operation_id 追踪结果</p></div></div></div>
      <div class="flow-band" style="background:#fff3e2"><div class="grid3" style="align-items:center;grid-template-columns:195px 1fr 255px"><div><div class="eyebrow">EVENT / SSE</div><h3 style="margin:6px 0">事实通知</h3></div><div class="swimlane"><div class="lane-node compact"><strong>事件 cursor</strong><small>去重 / 重连</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>Client Store</strong><small>局部刷新</small></div></div><div class="soft" style="background:#fff"><b>单一订阅</b><p>活跃 SSE 关闭对应冗余轮询</p></div></div></div></div>`,
    notes:'来自前端轮询卡顿和 API 稳定性问题的直接治理方向。当前并非全部 API 已实现 cache-only；迁移时 prepare/后台传输必须配套，否则远端结果可能突然不可见。', source:'完整方案 §7.4/8/13 · 模块接口 §2/7'
  },
  {
    id:'execution-flow',section:'03 · 流程与 Agent',title:'提交到完成：一条能够恢复的科研流水线',subtitle:'所有环节可观测、可对账；科学完成、工件发布、缓存可用是三种不同事实。',theme:'dark',status:'design',
    html:String.raw`<div class="swimlane" style="margin-top:31px"><div class="lane-node compact"><strong>01 · Intent</strong><small>Web / CLI / Agent</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>02 · Validate</strong><small>授权 · 科学 · 预算</small></div><div class="lane-arrow">→</div><div class="lane-node compact accent"><strong>03 · Plan</strong><small>冻结参数 · 身份</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>04 · Dispatch</strong><small>lease · release</small></div></div>
      <div style="display:flex;justify-content:flex-end;padding:4px 86px 4px 0;color:#80e5d5;font-size:35px">↓</div>
      <div class="swimlane"><div class="lane-node compact accent"><strong>08 · Publish</strong><small>工件 + 可用性</small></div><div class="lane-arrow">←</div><div class="lane-node compact"><strong>07 · Verify</strong><small>回执 · CAS · 恢复</small></div><div class="lane-arrow">←</div><div class="lane-node compact"><strong>06 · Science</strong><small>CCCP · 原生软件</small></div><div class="lane-arrow">←</div><div class="lane-node compact"><strong>05 · Worker</strong><small>执行信封 / 步骤</small></div></div>
      <div class="grid3" style="margin-top:31px"><div class="panel tight"><div class="eyebrow">SAFETY</div><h3>旧回执无权复活</h3><p class="small">attempt / submission / release 一致性校验</p></div><div class="panel tight"><div class="eyebrow">RESUME</div><h3>科学身份可复用</h3><p class="small">有科学证据才复用；旧式不明身份保守重算</p></div><div class="panel tight"><div class="eyebrow">RETRY</div><h3>发布失败不重算</h3><p class="small">只重试对应发布阶段，不重跑有效科学计算</p></div></div>`,
    notes:'示意顺序不能替代现有 result_publication 与 completed 的时间语义，迁移必须与当前代码和现行四项完整性不变量等价；状态 CAS 在 Runtime，文件工件在 Data。', source:'完整方案 §8/14 · 三层主设计 §11'
  },
  {
  "id": "current-call-chain",
  "section": "03 · 流程与 Agent",
  "title": "当前实现：一次优化如何落到软件执行",
  "subtitle": "调度路径经 CLI 子进程与工作流计划，执行器再通过兼容 shim 接入 CCCP",
  "theme": "light",
  "status": "baseline",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>调用顺序 / 关键文件</th><th>关键内容与输出</th></tr></thead><tbody><tr><td>01 scheduler/manager.py → runner.py</td><td>JobManager 接收 JobSpec 并持久化；JobRunner 启动 python -m acp.cli run</td></tr><tr><td>02 workflows → calculations/executor.py</td><td>CalculationPlanExecutor.execute() 校验计划、身份、依赖和 checkpoint；OPTIMIZE 进入 dispatch</td></tr><tr><td>03 calculations/primitives/optimize.py</td><td>CalculationRequest 转 TaskRequest，注入 TaskContext，转调 cccp.calculation.run_optimize</td></tr><tr><td>04 cccp/calculation/tasks/optimize.py</td><td>唯一优化实现：完成检查、失败分类、内部 rescue 与科学结果</td></tr><tr><td>05 cccp/backends → qc/interfaces</td><td>能力适配调用软件封装，再执行子进程；保留原生输入与输出</td></tr></tbody></table><div class=\"flow-footer\">返回路径：软件结果交任务核心，shim 映射旧结果，执行器汇总步骤；平台按完成规则处理状态与发布。</div></div>",
  "notes": "未带 cccp 前缀的路径位于 src/acp。直接 CLI 不经过 JobManager，不同工作流可能采用专用编排。本页为 optimize 步骤调用骨架，不表示所有工作流都使用同一 executor。",
  "source": "executor.py · primitives/optimize.py · tasks/optimize.py"
},
  {
    id:'integrity',section:'03 · 流程与 Agent',title:'四条不可突破的执行不变量',subtitle:'架构可迁移，科学身份、生命周期和恢复规则不能在重构中丢失。',theme:'light',status:'design',
    html:String.raw`<div class="grid2" style="gap:19px;margin-top:20px"><div class="panel"><div class="card-top"><div class="circle-flag">01</div><h3>身份先于派发</h3></div><p>每个 attempt 先确认存储位置、release 与持久 submission 身份。</p></div>
      <div class="panel"><div class="card-top"><div class="circle-flag">02</div><h3>控制意图不能被旧状态覆盖</h3></div><p>取消须确认停止；终态不复活，进度不能覆写状态或有效参数。</p></div>
      <div class="panel"><div class="card-top"><div class="circle-flag">03</div><h3>只复用有身份和证据的结果</h3></div><p>旧 v1 不确定结果保守重算；发布失败只重试发布，科学与平台身份分开。</p></div>
      <div class="panel"><div class="card-top"><div class="circle-flag">04</div><h3>不满足依赖，不假装 completed</h3></div><p>上游失败/缺失阻断下游；诊断继续必须显式且恢复时重判。</p></div></div>
      <div class="note-box" style="margin-top:20px">这四项是现有架构调整的强制验收门，不因插件化、分布式部署或新 UI 而放宽。</div>`,
    notes:'此处忠实概括完整方案 §14.1 四项执行完整性不变量，应在每个切片持续回归，而非仅在最终重构完毕后测试。', source:'完整方案 §14.1'
  },
  {
  "id": "recovery-decisions",
  "section": "03 · 流程与 Agent",
  "title": "失败之后，系统具体复用什么",
  "subtitle": "恢复同时检查科学身份、步骤完成事实与平台 attempt",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>场景</th><th>关键驻点</th><th>确定行为</th></tr></thead><tbody><tr><td>checkpoint 身份不匹配</td><td>calculations/executor.py + checkpoint / identity</td><td>保守重算，不能凭输出文件存在跳过</td></tr><tr><td>必需上游失败或缺失</td><td>step_requirements.py + batch/engine.py</td><td>依赖步骤 blocked，整体不能 completed</td></tr><tr><td>科学成功但发布失败</td><td>现有发布契约，目标 Runtime / Data 边界</td><td>保留科学事实，只重试幂等发布</td></tr><tr><td>旧 worker 回执迟到</td><td>scheduler/store.py CAS + 目标回执校验</td><td>拒绝旧 attempt / submission / release 覆盖</td></tr><tr><td>用户原地重跑</td><td>manager.py + store.requeue_with_spec</td><td>同目录 attempt+1，旧回执归档并隔离</td></tr></tbody></table><div class=\"flow-footer\">终态副作用在终态 CAS 持久化之后执行，并保持幂等可重试；迁移沿用现有 result_publication 时间语义。</div></div>",
  "notes": "当前文件位于 src/acp。Runtime/Data 为目标所有权。不得以新组件名重新安排发布与 completed 的顺序。",
  "source": "执行完整性四不变量 · 主方案 §14"
},
  {
    id:'provenance',section:'03 · 流程与 Agent',title:'可复核的科研证据链，是 ACP 的长期资产',subtitle:'科学结论必须能够追溯到输入、方法、计算执行、人工选择与版本。',theme:'light',status:'design',
    html:String.raw`<div class="swimlane" style="margin-top:40px"><div class="lane-node"><strong>输入结构</strong><small>source / atom_uid</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>冻结计划</strong><small>method / basis / options</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>执行证据</strong><small>job / attempt / release</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>科学产物</strong><small>result / parser / 单位</small></div><div class="lane-arrow">→</div><div class="lane-node accent"><strong>研究结论</strong><small>选择 / 修订 / 报告</small></div></div>
      <div class="grid2" style="margin-top:42px"><div class="panel tint-teal"><div class="eyebrow">HUMAN IN THE LOOP</div><h3>人工候选不覆盖原始证据</h3><p>每次选择、重命名、修订和进一步计算都有版本与来源记录。</p></div><div class="panel"><div class="eyebrow">SCIENTIFIC COMPARABILITY</div><h3>不同能量不能简单拼成一条曲线</h3><p>先验证方法、单位、参考态和结构/帧映射，再进行比较。</p></div></div>`,
    notes:'Runtime 负责计划/任务/操作事实，Data 负责工件和来源边，CCCP 负责科学派生与版本证据；投影是只读的可重建研究溯源，不另造第二个状态机。', source:'完整方案 §32.2 · 产品战略 §4'
  },
  {
    id:'plugin',section:'04 · 开放与部署',title:'科学插件：小核心，受控扩展',subtitle:'先支持 Engine / Parser / Analysis adapter；不将所有业务拆成插件。',theme:'dark',status:'design',
    html:String.raw`<div class="grid2" style="margin-top:16px"><div class="panel"><div class="eyebrow">EXTENSION LIFECYCLE</div><div class="swimlane" style="margin:24px 0"><div class="lane-node compact"><strong>Discover</strong><small>entry_points</small></div><div class="lane-arrow">→</div><div class="lane-node compact"><strong>Approve</strong><small>可信来源 / 管理员</small></div><div class="lane-arrow">→</div><div class="lane-node compact accent"><strong>Load</strong><small>版本 / 能力匹配</small></div></div><div class="note-box">仅发现不自动执行；插件是可执行代码，不是沙箱。重复能力名称须明确拒绝。</div></div>
      <div class="panel"><div class="eyebrow">PLUGIN CONTRACT</div><div class="checkrow"><div class="checkicon">✓</div><div><b>版本与来源锁定</b><p>发行 / API / schema / 原生软件 / 许可证</p></div></div><div class="checkrow"><div class="checkicon">✓</div><div><b>科学契约测试</b><p>单位、atom/frame 映射、取消、失败、部分输出</p></div></div><div class="checkrow"><div class="checkicon">✓</div><div><b>单一任务入口</b><p>不绕开 Calculation 的任务根</p></div></div></div></div>
      <div class="flow-footer" style="margin-top:22px">推荐先试点 <strong>xTB adapter 或只读 parser</strong>，并交付插件 SDK / 最小示例 / 合同测试；这仍是目标方案。</div>`,
    notes:'Everything is a Plugin 被调整为“内置完整科学核心 + 受控 adapter 扩展”。一律避免启动时自动加载/下载安装任意插件，也不要把 ACP/ACR 财务和任务生命周期变成第三方脚本可任意覆盖的插件。', source:'完整方案 §31 · 产品战略 §1/4'
  },
  {
    id:'archive',section:'04 · 开放与部署',title:'科研项目应该可以离开 ACP',subtitle:'项目可迁移性：科学身份可保留，平台身份、权限和执行接管必须重新建立。',theme:'light',status:'design',
    html:String.raw`<div class="swimlane" style="margin-top:32px"><div class="lane-node accent"><strong>Lab Standalone</strong><small>冻结一致快照</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>Portable Archive</strong><small>manifest · checksum · products</small></div><div class="lane-arrow">→</div><div class="lane-node accent"><strong>Lab Server</strong><small>授权 / 存储重绑定</small></div><div class="lane-arrow">→</div><div class="lane-node"><strong>再回个人端</strong><small>离线证据 / 可复核</small></div></div>
      <div class="grid3" style="margin-top:37px"><div class="panel tight"><div class="eyebrow">INCLUDED</div><h3>科学与来源信息</h3><p class="small">输入 / 输出 / 原始文件 / 规范化产品 / 参数 / schema / 摘要</p></div><div class="panel tight"><div class="eyebrow">REBOUND</div><h3>身份与资源重新绑定</h3><p class="small">user / project / storage binding / 本地 job identity</p></div><div class="panel tight"><div class="eyebrow">EXCLUDED</div><h3>秘密与执行所有权</h3><p class="small">密码 / token / SSH 密钥 / 活跃 lease / 支付凭据</p></div></div>
      <div class="note-box" style="margin-top:20px">导入默认只得到可信历史结果，不自动执行或接管远端计算；“可读取”不等于“可从 checkpoint 继续”。</div>`,
    notes:'项目包必须完整性、体积/路径/归档炸弹/权限检查。科学 content identity 和 source_namespace/source_id 保留，平台 user ID 和 storage locator 重新生成；不可把相同文件 hash 当作访问授权。', source:'完整方案 §32.1'
  },
  {
    id:'credits',section:'04 · 开放与部署',title:'免费科学能力，与托管核时计量解耦',subtitle:'图形、查询、CLI/MCP、下载与科学算法不依收费渠道改变；付费托管必须先通过结算安全门。',theme:'light',status:'design',
    html:String.raw`<div class="two-tier" style="margin-top:17px"><div class="panel"><div class="eyebrow">HOSTED COMPUTE CREDIT FLOW</div><div class="swimlane" style="margin:25px 0"><div class="lane-node compact"><strong>Runtime</strong><small>预检 / 资源估算</small></div><div class="lane-arrow">›</div><div class="lane-node compact"><strong>Workspace</strong><small>原子预留</small></div><div class="lane-arrow">›</div><div class="lane-node compact accent"><strong>Runtime</strong><small>执行 / 核时对账</small></div></div><div class="swimlane"><div class="lane-node compact"><strong>Data</strong><small>用量证据</small></div><div class="lane-arrow">‹</div><div class="lane-node compact"><strong>Workspace</strong><small>幂等结算 / 释放</small></div><div class="lane-arrow">‹</div><div class="lane-node compact"><strong>Runtime</strong><small>usage record</small></div></div><div class="note-box" style="margin-top:16px">排队、传输、网页轮询、结果发布不计计算核时；计费口径、费率和失败政策尚待正式冻结。</div></div>
      <div class="vstack"><div class="panel tint-teal"><div class="eyebrow">FREE</div><h3>科学功能与自有算力</h3><p>本地使用、实验室自有计算资源和完整科学能力保持免费。</p></div><div class="panel"><div class="eyebrow">ONLY HOSTED RESOURCE</div><h3>托管核时 → Credit</h3><p>余额/预留/账本归 Workspace；可信用量归 Runtime；CCCP 无 Credit 概念。</p></div></div></div>`,
    notes:'文档没有冻结 credits 每核时价格，也没有决定最终采用分配核时还是实际 CPU 时间。科学 completed 与账务待对账是独立维度，余额不足不得阻止合法读取已有结果。', source:'完整方案 §20 · 高内聚模块设计 §3'
  },
  {
    id:'deployment',section:'04 · 开放与部署',title:'三种部署配置，共用一个科学内核',subtitle:'由个人本地到实验室共享，再到按需托管；部署复杂度与安全要求随场景提升。',theme:'dark',status:'design',
    html:String.raw`<div class="grid3" style="margin-top:11px"><div class="panel" style="min-height:373px;border-top:5px solid #6be8d2"><div class="stage-chip">01 · PRIORITY</div><h3 style="font-size:27px">Lab Standalone</h3><p style="min-height:66px">本地单人，尽量不依赖额外常驻服务。</p><div class="rule"></div><div class="mini-meta">身份 / 数据库</div><h4 style="margin-top:8px">本地主体 · SQLite</h4><div class="mini-meta" style="margin-top:11px">计算资源</div><h4 style="margin-top:8px">受控 Local</h4><div class="tags"><span class="tag good">先交付</span></div></div>
      <div class="panel" style="border-top:5px solid #91a4ff"><div class="stage-chip">02 · NEXT</div><h3 style="font-size:27px">Lab Server</h3><p style="min-height:66px">多人共享实验室资源与现有集群。</p><div class="rule"></div><div class="mini-meta">身份 / 数据库</div><h4 style="margin-top:8px">多用户授权 · 服务 DB</h4><div class="mini-meta" style="margin-top:11px">计算资源</div><h4 style="margin-top:8px">已有 Local / LSF</h4><div class="tags"><span class="tag">配额 / 隔离</span></div></div>
      <div class="panel" style="border-top:5px solid #ffd093"><div class="stage-chip">03 · CONDITIONAL</div><h3 style="font-size:27px">ACP Hosted</h3><p style="min-height:66px">在线多租户，满足隔离、计量与运维门之后按需开放。</p><div class="rule"></div><div class="mini-meta">身份 / 数据库</div><h4 style="margin-top:8px">OIDC · 可审计账本</h4><div class="mini-meta" style="margin-top:11px">计算资源</div><h4 style="margin-top:8px">可信隔离 · Credit</h4><div class="tags"><span class="tag warn">后置验证</span></div></div></div>
      <div class="callout" style="margin-top:13px;text-align:center;font-size:17px">不是 3 套代码，也不是 3 套科学算法；配置、认证、调度、存储和隔离策略按需替换。</div>`,
    notes:'Lab Standalone 关闭登录交互也不能绕过本地身份/访问边界；Lab Server 不能沿用单用户安全假设；Hosted 需先过可靠计量、权限隔离、备份及运维验收。', source:'完整方案 §26/30'
  },
  {
    id:'open-source',section:'04 · 开放与部署',title:'充分复用开源，不外包 ACP 的独特性',subtitle:'优先复用成熟“基础能力”，把开发力量集中在科研流程、证据与生命周期。',theme:'light',status:'candidate',
    html:String.raw`<div class="grid3" style="margin-top:21px"><div class="panel tight"><div class="eyebrow">EXISTING · 现有复用</div><h3>优先封装已有能力</h3><div class="tags"><span class="tag good">FastAPI</span><span class="tag good">Paramiko</span><span class="tag good">3Dmol</span><span class="tag good">RDKit</span><span class="tag good">NumPy / SciPy</span></div><p class="small" style="margin-top:16px">不为了改架构就重选技术栈。</p></div>
      <div class="panel tight"><div class="eyebrow">CANDIDATES · 待 PoC</div><h3>渐进引入基础设施</h3><div class="tags"><span class="tag warn">Keycloak / Authlib</span><span class="tag warn">PostgreSQL</span><span class="tag warn">Slurm</span><span class="tag warn">SeaweedFS</span><span class="tag warn">ECharts</span></div><p class="small" style="margin-top:16px">按部署档位、负担与兼容性决定。</p></div>
      <div class="panel tight"><div class="eyebrow">SCIENCE ADAPTER · 待验证</div><h3>按程序 / 能力试点</h3><div class="tags"><span class="tag warn">QCElemental</span><span class="tag warn">QCEngine</span><span class="tag warn">cclib</span><span class="tag warn">cgroup / Podman</span></div><p class="small" style="margin-top:16px">逐版本科学 fixture，对照原生输出与失败语义。</p></div></div>
      <div class="panel accent-panel" style="margin-top:24px;display:flex;align-items:center;gap:25px"><span style="font-size:46px;color:#91efdb">≠</span><div><h3 style="margin:0 0 6px">开源组件 ≠ 完整科学工作台</h3><p>登录服务不等于项目授权；调度器不等于科学恢复；通用计费不等于可信核时准入；解析库不等于所有字段覆盖。</p></div></div>`,
    notes:'文档列出的候选不是已集成/锁版本清单，必须做许可证、依赖、科学功能与恢复 PoC 再决定采用。已有 LSF 不强制换成 Slurm，轻量 Lab 不要求同时安装全部候选。', source:'开源复用评估 §1–6 · 完整方案 §27'
  },
  {
    id:'competition',section:'04 · 开放与部署',title:'向成熟生态学习，靠“完整闭环”建立差异',subtitle:'不宣称 AiiDA 没有 GUI，也不宣称 ACP 已全面领先其他产品。',theme:'light',status:'candidate',
    html:String.raw`<div class="grid2" style="gap:17px;margin-top:18px"><div class="panel tight"><div class="eyebrow">AiiDA / AiiDAlab</div><h3>学习过程溯源与插件体系</h3><p class="small">不默认整体引入其运行引擎 / ORM；避免双 controller。</p></div><div class="panel tight"><div class="eyebrow">Avogadro / ORCA</div><h3>对标日常建模与结果可视化</h3><p class="small">ACP 必须先达到日常结构操作的可用性基线。</p></div><div class="panel tight"><div class="eyebrow">Chemcraft</div><h3>学习专业结果解释与图形输出</h3><p class="small">不能声称当前已完全替代专业轨道/振动/谱图工具。</p></div><div class="panel tight"><div class="eyebrow">AMS / PLAMS</div><h3>学习 GUI + 脚本 + 科研流程集成</h3><p class="small">ACP 的目标差异是本地优先、开放科学底座和跨平台证据可迁移。</p></div></div>
      <div class="callout" style="margin-top:19px;text-align:center"><strong>同场景、同环境、同科学能力</strong> 实测安装耗时、人工步骤、恢复和证据质量，才构成有效差异化结论。</div>`,
    notes:'竞品信息来自公开资料整理而非用户实机对照，不能以没有看到某个功能页面推断竞品不支持。未来如采用 AiiDA 数据互操作，优先从只读来源映射试点。', source:'产品战略 §3–4 · 完整方案 §30.2'
  },
  {
    id:'chapter-implementation',section:'05 · 迁移实施',title:'不是推倒重来',subtitle:'以能交付用户价值的垂直切片推进架构收敛，保证老任务与科学数据可用。',theme:'dark',kind:'chapter',status:'design',
    html:String.raw`<div class="chapter-accent"></div><div class="chapter-cover"><div class="chapter-num">CHAPTER FIVE / DELIVERY PLAN</div><h1>从现有代码<br>稳步演进</h1><p>先解决请求、刷新和数据准备的实际问题；<br>再逐步形成清晰的模块边界与独立交付能力。</p><div class="chapter-underline"></div></div>`,
    notes:'主方案强调原地高内聚、垂直切片、持续回归与明确回滚，而不是一次性大规模机械搬迁。', source:'完整方案 §12–17/35'
  },
  {
    id:'baseline',section:'05 · 迁移实施',title:'当前代码：有成熟能力，也有职责混驻',subtitle:'以下为 2026-10-16 文档中的静态文件清点，不是已完成迁移进度。',theme:'light',status:'baseline',
    html:String.raw`<div class="grid4" style="margin-top:23px"><div class="kpi"><div class="kpi-label">现有 src/acp/</div><div class="kpi-value">268</div><div class="kpi-hint">Python 文件<br>平台 + 部分科学逻辑混合</div></div><div class="kpi"><div class="kpi-label">现有 src/cccp/</div><div class="kpi-value">87</div><div class="kpi-hint">Python 文件<br>科学核心已具基础契约</div></div><div class="kpi"><div class="kpi-label">现有 frontend/</div><div class="kpi-value">20</div><div class="kpi-hint">文件<br>3Dmol、SVG 与页面混驻</div></div><div class="kpi"><div class="kpi-label">tests 中 test_*.py</div><div class="kpi-value">314</div><div class="kpi-hint">命名清点<br>不等于真实测试通过数</div></div></div>
      <div class="grid2" style="margin-top:22px"><div class="panel tight"><div class="eyebrow">COMPLEXITY TO REDUCE</div><h3>依赖与入口混杂</h3><p class="small">本地 / 远端 worker 依赖 acp.cli；主 HTML 集中业务逻辑；部分 GET 可能隐式传输。</p></div><div class="panel tight"><div class="eyebrow">FOUNDATION TO PRESERVE</div><h3>不重做已有科学能力</h3><p class="small">CCCP requests/results/context/progress、现有 active 工作流、科学 fixtures、输出与历史目录协议保持。</p></div></div>`,
    notes:'清点不是全部依赖与行为的动态审计，实施 P0 需重新记录 revision/dirty 状态与测试环境。文档还提到 scripts/ 12 文件和一个共用 pyproject。', source:'三层主设计 §2 · 完整方案 §2'
  },
  {
  "id": "file-ownership",
  "section": "05 · 迁移实施",
  "title": "关键文件的迁移归属与保留内容",
  "subtitle": "按职责拆分文件内容，保持唯一实现与旧导入兼容",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>当前文件或目录（src/ 下）</th><th>保留的关键内容</th><th>目标 owner</th></tr></thead><tbody><tr><td>acp/scheduler/manager.py + store.py</td><td>控制意图、attempt、状态 CAS、恢复与查询</td><td>ACR Runtime</td></tr><tr><td>acp/calculations/executor.py + batch/</td><td>步骤依赖、身份、checkpoint、科学调用</td><td>Runtime plan_runner</td></tr><tr><td>acp/storage/ + results/manifest.py</td><td>发布登记、工件引用、读取与可用性</td><td>ACR Data</td></tr><tr><td>acp/results/ + nmr/ + chem/ + intake/</td><td>拆出数值处理、科学解析、结构和分析产品</td><td>CCCP Analysis / IO</td></tr><tr><td>cccp/calculation/tasks/ + backends/ + qc/</td><td>唯一任务核心、能力适配、软件接口</td><td>CCCP 原驻点保留</td></tr></tbody></table><div class=\"flow-footer\">结构来源组织、权限与平台关联留在 ACR，科学算法迁入 CCCP。results/ 与 nmr/ 都需要按内容拆分。</div></div>",
  "notes": "源文档 §9 有完整映射，本页精选影响独立 worker 与数据边界的驻点。旧 primitives/backends 继续纯 shim。src/acp_runtime 为目标包。",
  "source": "主方案 §9–10 · 当前源码职责"
},
  {
  "id": "target-file-contents",
  "section": "05 · 迁移实施",
  "title": "目标关键文件要承载什么",
  "subtitle": "以下为建议落点与内容清单，具体文件粒度与签名在切片实施时冻结",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>建议落点</th><th>必须包含</th><th>边界限制</th></tr></thead><tbody><tr><td>frontend/client/</td><td>transport、ResourceStore、查询去重、命令回执、刷新</td><td>无 DOM / 3Dmol / 科学分析</td></tr><tr><td>acp_runtime/runtime/public.py</td><td>类型化提交、控制、查询入口与公开符号</td><td>不含 SQL / SSH / 原生科学输入生成</td></tr><tr><td>acp_runtime/runtime/worker/</td><td>envelope 校验、计划执行、科学 adapter、回执</td><td>不写 jobs DB，不自增 attempt</td></tr><tr><td>acp_runtime/data/public.py</td><td>inputs / publish / prepare / read 类型化端口</td><td>不调度任务，不隐式科学重跑</td></tr><tr><td>cccp/analysis/</td><td>科学算法、单位、身份映射、规范化产品</td><td>不含项目权限与图形实例</td></tr></tbody></table><div class=\"flow-footer\">每个边界同步交付：公开符号、输入输出 fixture、依赖白名单、唯一写入者与兼容路径。</div></div>",
  "notes": "Python 落点相对 src/。主方案规定目录族，但不强制 public.py 等文件名。本页为建议，不代表已存在或已冻结 API。避免预建空目录。",
  "source": "主方案 §10 · 模块接口 §9 · 建议落点"
},
  {
    id:'migration',section:'05 · 迁移实施',title:'P0–P7：按依赖切片，不按目录整体搬家',subtitle:'每次只迁移一条可验证的流程：唯一 owner、保留 shim、完成测试与回滚。',theme:'light',status:'design',
    html:String.raw`<div class="grid4" style="gap:13px;margin-top:16px"><div class="phase"><div class="phase-id">P0 · BASELINE</div><h3>基线与护栏</h3><p>清点依赖 / 性能 / 兼容 / 科学 fixtures</p></div><div class="phase"><div class="phase-id">P1 · REQUESTS</div><h3>Client 与事件</h3><p>请求合并、统一 store、投影、SSE</p></div><div class="phase"><div class="phase-id">P2 · DATA</div><h3>传输可恢复</h3><p>prepare、generation、cache-only</p></div><div class="phase"><div class="phase-id">P3 · VISUALS</div><h3>ACV 模块化</h3><p>2D/3Dmol、选点、资源释放</p></div><div class="phase"><div class="phase-id">P4 · RUNTIME</div><h3>运行边界</h3><p>controller/worker、身份、独立入口</p></div><div class="phase"><div class="phase-id">P5 · SCIENCE</div><h3>科学归 CCCP</h3><p>统一产品、NMR/PES/抽样迁移</p></div><div class="phase"><div class="phase-id">P6 · SCALE</div><h3>按需多节点</h3><p>DB / lease / 集群 / 分布式存储</p></div><div class="phase"><div class="phase-id">P7 · CLEANUP</div><h3>删除旧实现</h3><p>回滚窗口和兼容通过后才清理</p></div></div>
      <div class="note-box" style="margin-top:22px">P0–P7 是工作分解，不要求整阶段完成才交付。可交错推进的垂直切片由 M0–M4 组织。</div>`,
    notes:'尤其 P4-03 是 worker 入口迁移的科学依赖前置，不能只移动 scheduler；前端先治理刷新和只读数据，确保用户立即体验收益。', source:'完整方案 §13 · §35'
  },
  {
  "id": "worker-dependencies",
  "section": "05 · 迁移实施",
  "title": "独立 worker 的依赖前置与切换顺序",
  "subtitle": "P4-03 先迁科学闭包，P4-04 冻结协议，P4-05 切换独立入口",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>顺序</th><th>交付动作</th><th>验证目标</th></tr></thead><tbody><tr><td>01 盘点导入闭包</td><td>定位 executor 与 helper 中的科学 / 平台职责</td><td>明确旧类型、算法及隐性 acp 依赖</td></tr><tr><td>02 迁移科学部分</td><td>CCCP 唯一实现，旧路径同类 re-export</td><td>类型和数值兼容，CCCP 无 acp 反向导入</td></tr><tr><td>03 冻结执行协议</td><td>Envelope / Receipt 版本与身份映射</td><td>拒绝旧派发回执，TaskContext 不跨网</td></tr><tr><td>04 切换 worker</td><td>直接调用科学公开 API，移除旧 CLI 依赖</td><td>不通过字符串 subprocess 调旧 acp.cli 绕行</td></tr><tr><td>05 最小环境验收</td><td>实际 wheel 安装并执行本地 / 远端场景</td><td>ACR + CCCP 无浏览器前端也可用</td></tr></tbody></table><div class=\"flow-footer\">当前 JobRunner 使用旧 CLI 是基线事实。新禁止边随切片切换启用，不将目标约束倒写为当前已完成状态。</div></div>",
  "notes": "导入通过不能证明科学运行与 LSF 恢复成功。真实软件和远程未验证时标 NOT_VERIFIED。保持旧磁盘格式、跨版本恢复 fixture 与架构门。",
  "source": "主方案 §10/12.2/13 P4-03–05"
},
  {
    id:'milestones',section:'05 · 迁移实施',title:'M0–M4：每个里程碑都有用户可感知成果',subtitle:'从科研新手第一次跑通，到实验室协作和可选的托管验证。',theme:'dark',status:'design',
    html:String.raw`<div class="timeline" style="margin-top:45px"><div class="phase"><div class="phase-id">M0 / EVIDENCE</div><h3>冻结基线</h3><p>2 个科研场景<br>安装 / 测试 / 失败样本</p><small>记录真实成本，不填未测收益</small></div>
      <div class="phase" style="border-color:#4cae9f;background:#123b3d"><div class="phase-id">M1 / FIRST VALUE</div><h3>本地闭环</h3><p>结构→计算→ACV→报告<br>GUI 与 CLI 可用</p><small>第一条可信插件样板</small></div>
      <div class="phase"><div class="phase-id">M2 / PORTABLE</div><h3>迁移 / Agent</h3><p>第二条科研链<br>Archive / MCP / 诊断</p><small>跨入口科学结果与错误一致</small></div>
      <div class="phase"><div class="phase-id">M3 / COLLABORATE</div><h3>Lab Server</h3><p>多用户隔离 / 配额<br>已有 Local / LSF</p><small>完成本地↔实验室项目往返</small></div>
      <div class="phase"><div class="phase-id">M4 / WHEN NEEDED</div><h3>按需扩展</h3><p>更多插件与科研链<br>Hosted / AiiDA 数据桥</p><small>收费、安全和运维门通过才开放</small></div></div>
      <div class="callout" style="margin-top:34px;text-align:center;font-size:18px"><strong>M1 不等待“大重构”：</strong>使用现有组件完成一条真正可用的图形科研闭环。</div>`,
    notes:'M0–M4 是设计优先序，不是实施进度或预估周数。每个切片必须持续回归受影响的 15 个 active 工作流，不能将“所有回归”推迟到 M4。', source:'完整方案 §35 · 产品战略 §7'
  },
  {
  "id": "slice-acceptance",
  "section": "05 · 迁移实施",
  "title": "首批切片的具体交付与验收",
  "subtitle": "把模块目标落实为用户动作、文件边界和失败场景",
  "theme": "light",
  "status": "design",
  "html": "<div class=\"implementation-detail\"><table class=\"detail-table\"><thead><tr><th>切片 / 对应阶段</th><th>涉及驻点</th><th>完成判据</th></tr></thead><tbody><tr><td>统一取数与绘图样板<br>P1 / M1</td><td>Client + 一个 feature + ACV</td><td>两面板共享请求，旧响应无覆盖，关闭后无动画残留</td></tr><tr><td>远程结果可用性<br>P2 / M1–M2</td><td>Data query / prepare / transfer / cache</td><td>未缓存可准备，重启可续传，旧 generation 不发布</td></tr><tr><td>首个独立执行闭环<br>P4 / M1–M2</td><td>Runtime worker + optimize / frequency</td><td>有效输入完成计算，依赖失败阻断，重复回执不重复发布</td></tr><tr><td>旧任务兼容与恢复<br>逐切片持续验收</td><td>shim / checkpoint / manifest / CAS</td><td>旧任务可读，重跑隔离 attempt，发布失败不重复计算</td></tr></tbody></table><div class=\"flow-footer\">每个切片保留输入 fixture、输出对照与回滚入口。数值容差先定义，真实软件与远程验证逐项记录。</div></div>",
  "notes": "这是主方案验收要求的实施化建议，非通过声明。M 是用户交付批次，P 是依赖工作包。性能与数值目标在 P0 固定场景后确定，演示稿不虚构阈值。",
  "source": "主方案 §14/17/35 · 切片验收建议"
},
  {
    id:'quality',section:'05 · 迁移实施',title:'验收不看“模块搬完没有”，看事实是否可靠',subtitle:'稳定性、科学等价、数据边界和性能都需要基线与可验证结果。',theme:'light',status:'design',
    html:String.raw`<div class="grid3" style="gap:17px;margin-top:20px"><div class="panel tight"><div class="icon-box teal">◇</div><h3>科学正确性</h3><p class="small">数值 / 单位 / 原子与模式映射 / 历史 fixture / 15 active workflows</p></div><div class="panel tight"><div class="icon-box blue">↻</div><h3>恢复与兼容</h3><p class="small">attempt / CAS / 取消确认 / 旧任务 / 目录 / 重启 / 发布对账</p></div><div class="panel tight"><div class="icon-box amber">▥</div><h3>唯一数据权威</h3><p class="small">Runtime 不写 Data，Data 不写 job，GET 不启动传输或科学计算</p></div><div class="panel tight"><div class="icon-box teal">▤</div><h3>前端性能</h3><p class="small">请求数、P50/P95、去重、资源缓存、浏览器内存与长时切换泄漏</p></div><div class="panel tight"><div class="icon-box blue">⌘</div><h3>独立交付</h3><p class="small">CCCP 无 Web/账务依赖；CLI 无浏览器依赖；ACV 能使用 fixture 渲染</p></div><div class="panel tight"><div class="icon-box amber">✓</div><h3>运维可靠性</h3><p class="small">一致备份 / 回滚 / 跨租户测试 / 组件许可与实际安装可复现</p></div></div>
      <div class="note-box" style="margin-top:23px">性能百分比、稳定性提升幅度、科学软件覆盖范围在 M0 / PoC 量测前均不得写成已验证成果。</div>`,
    notes:'不能把 pytest 跳过的真实软件/LSF 验证记为通过；明确 PASS / FAIL / NOT_VERIFIED。性能数据固定场景、硬件和采样方法，前后对照。', source:'完整方案 §14/17/35'
  },
  {
    id:'risks',section:'05 · 迁移实施',title:'明确四类高风险，不让“架构优化”变成回归',subtitle:'每个风险都有可以直接落到测试与切片的约束。',theme:'light',status:'design',
    html:String.raw`<div class="grid2" style="gap:20px;margin-top:21px"><div class="panel"><div class="card-top"><div class="circle-flag">01</div><h3>新的超级 Manager</h3></div><p>将跨域能力收敛进稳定公开接口，而不是增加一个可访问所有表与进程的全局服务。</p></div>
      <div class="panel"><div class="card-top"><div class="circle-flag">02</div><h3>过早切换 cache-only</h3></div><p>只有 prepare/持久传输/availability 成套交付后，才能删除同步 fetch 回退。</p></div>
      <div class="panel"><div class="card-top"><div class="circle-flag">03</div><h3>科学结果或身份漂移</h3></div><p>不新增第二 tasks root，保留程序特有参数、原始证据及旧任务格式。</p></div>
      <div class="panel"><div class="card-top"><div class="circle-flag">04</div><h3>双重 owner / 双重扣费</h3></div><p>旧 worker 回执必须受 fencing；Credit 预留与结算幂等；不能同时运行两套权威。</p></div></div>
      <div class="callout" style="margin-top:23px;text-align:center"><strong>切片回滚原则：</strong>旧发布和数据可读，不重提未知作业，不双写、不覆盖历史科学证据。</div>`,
    notes:'文档同时警示独立 ACR 对旧 acp.cli 的隐性依赖、科学格式覆盖不足与过多服务引入。重构后的包名并不证明 worker 依赖真的消失。', source:'完整方案 §15–16/29'
  },
  {
    id:'closing',section:'05 · 迁移实施',title:'一个清晰的下一步',subtitle:'以“让一位有机合成研究者独立完成一次可靠计算”为产品交付准绳。',theme:'dark',status:'design',
    html:String.raw`<div class="two-tier" style="margin-top:13px"><div class="panel accent-panel" style="padding:33px 34px"><div class="eyebrow" style="color:#8cecd8">IMMEDIATE FOCUS</div><div class="big-statement" style="font-size:43px;color:#edfffc">先交付一条<br>真正可用的科研闭环。</div><p style="font-size:17px">让结构、计算、结果、证据与可恢复性作为一个整体出现，而不是多个功能菜单的集合。</p><div class="tags"><span class="tag good">Local-first</span><span class="tag good">One scientific truth</span></div></div>
      <div class="vstack"><div class="panel"><div class="eyebrow">NOW</div><h3>M0 · 固定科学场景与真实基线</h3><p class="small">锁输入、软件版本、可复核结果、错误样本、安装与性能指标。</p></div><div class="panel"><div class="eyebrow">NEXT</div><h3>M1 · 端到端 GUI / CLI 科研演示</h3><p class="small">请求治理 + Data prepare + ACV 渲染 + 科学证据 + 可靠恢复。</p></div></div></div>
      <div class="callout" style="margin-top:25px;text-align:center;font-size:19px">ACP 的目标：<strong>让计算化学从少数专家的工具链，成为更多研究者可信赖的科研能力。</strong></div>`,
    notes:'这是 v1.5 方案的综合结论。所有目录、契约和候选适配器仍须逐切片实施验证；本套网站是演示与后续 Vibe Coding 编辑起点，不是已完成的软件迁移。', source:'完整方案 §0/34–35 · 产品战略 §2/7'
  }
];

window.ACP_DETAILS = {
  workbench:{title:'Workbench / 工作台',copy:'组织页面、导航、表单、任务与结果交互、错误呈现和跨面板协作。拥有 UI 状态。只通过 Client 和 ACV 的公开接口工作，不直接 fetch，不持有任务或科学权威。',tags:['ACP 前端','用户意图','不直接访问 3Dmol']},
  client:{title:'Client / 统一数据客户端',copy:'HTTP/SSE、QueryCoordinator、CommandClient、ResourceStore、RefreshPolicy 与 ChangeCoordinator。共享缓存有唯一写入口；对外提供 query / command / subscribe / prepare。',tags:['ACP 前端','共享请求去重','统一错误与刷新']},
  acv:{title:'ACV / 可视化',copy:'静态前端独立渲染库：二维图、3Dmol adapter、相机、动画、选择回调、图片导出和资源销毁。不发请求、不解析原始科学文件，也不提交计算。',tags:['ACP 前端','2D / 3Dmol','create / setData / destroy']},
  workspace:{title:'Workspace / 工作空间',copy:'项目组织、成员、账户、身份、个人 API Key、角色/权限、配额授权和 Credit 账户/预留/账本的唯一业务所有者。不反向读取 Runtime 私有表。',tags:['ACR 中段','项目/授权','Credit 账本']},
  runtime:{title:'Runtime / 任务运行',copy:'任务提交、科学预检、冻结计划、controller/worker、attempt 与 CAS、取消/暂停/恢复，以及资源隔离和核时证据。通过公开端口调用 Data/Workspace/CCCP。',tags:['ACR 中段','唯一任务状态 owner','ExecutionEnvelope / Receipt']},
  data:{title:'Data / 数据资源',copy:'输入、科学工件、平台 manifest、传输/缓存/发布、generation 校验、候选与来源的权威所有者。GET 只读可用数据；不隐式启动科学计算。',tags:['ACR 中段','ArtifactStore','prepare / publish / read']},
  calculation:{title:'Calculation / 科学任务',copy:'CCCP 唯一任务实现驻点，稳定 TaskRequest / TaskResult / TaskContext，负责单科学步骤与完成证据；科学引擎保持可独立开源和调用。',tags:['CCCP 科学后端','唯一 tasks root','取消/超时']},
  analysis:{title:'Analysis / 科学数据处理',copy:'科学结构、数值分析、NMR / PES / 采样、规范化产品以及大数据分块/抽样。输出具有单位、来源和映射的科学数据，不把显示抽样冒充全量结果。',tags:['CCCP 科学后端','全量科学处理','标准数据产品']},
  software:{title:'Software / 软件适配',copy:'软件发现、原生输入输出、程序兼容、执行与只读解析 adapter。保留版本与特有参数；可通过受控插件增强，不绕开 Calculation 的唯一任务入口。',tags:['CCCP 科学后端','ORCA / xTB 等','可信 Engine / Parser 插件']}
};
