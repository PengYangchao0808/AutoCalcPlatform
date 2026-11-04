# ACP 占用浮层而非 DSH 原生右侧工作区：诊断

日期：2026-11-04。依据用户新截图、当前工作树及本机已安装 DSH `0.2.1-rc.5` 的发布包，只读检查。没有修改运行中 DSH、desktop profile 或插件源码，没有重启、模型调用或计算提交。

## 结论

当前真实 ACP 内容挂在 `shell.overlay`，不是 DSH 原生右侧 docking surface。已有右侧 tab 注册只渲染占位说明。面板自己绝对定位在屏幕右边，并通过宿主 DOM 选择器修改中间列 padding，因此既没有原生停靠语义，也不能自然获得右侧工作区的标签、分屏、尺寸与焦点管理。截图中右侧覆盖式面板及未利用的主区域，与该实现一致。

用户所称“右侧窗口”在 DSH 已安装版本中由原生 right docking surface 承载。它的 API 名称仍含 `sidebarRight`；名称不表示必须把整个科学工作台画成窄工具栏。应改用这一真实工作区，而不是另外再造一个贴右边的浮层。

## 源码证据

| 文件 / 行 | 已核对事实 | 影响 |
|---|---|---|
| `frontend/dsh-plugin/client/client.js:38`、`:727` | `SLOT_OVERLAY="shell.overlay"`，注册 `AcpRail` | 真实面板由浮层绘制 |
| 同文件 `:56`、`:58`、`:59` | 默认 620px，上限 900px，中间最少保留 360px | 宽屏也受插件自有尺寸限制；与宿主实际轨道无关 |
| 同文件 `:705` | 原生 tab body 注册为 `AcpTabPlaceholder` | 已有 native tab 不能承载工作台 |
| 同文件 `:340` 与 `client/host.css:118` | 写根属性/变量，并选择 `div:has(+ [data-rightbar-col])` 加右内边距 | 对宿主内部 DOM 耦合，不能形成真正停靠；原生右栏与 ACP 浮层可叠加 |
| `client/host.css:37`、`:124` | 绝对定位，窄屏改为整幅覆盖 | “右边出现”不等于“右侧工作区” |
| `frontend/css/shell/next-layout.css:139–166` | 容器 <849px 收起 D 任务栏为抽屉，<640px 再收起部分品牌/导入/命令行入口 | 620px 的 rail 会触发紧凑模式；应先修宿主可用空间，不仅取消内部折叠断点 |
| `client.js:46`、`:500` | 所有挂载固定 `sessionId="acp-workbench"` | 返回原生会话级工作区时须恢复实际会话绑定，不把保持 UI 等同于共用会话身份 |
| `test/client.test.mjs:708`、`:760`、`:793` | 测试要求浮层注册且不调用原生打开资源 | 通过这些断言只证明旧实现；新修复需按目标行为修改，不能照搬旧 PASS |

## 已安装宿主的接口核查

实际只读来源：`C:/Users/彭扬超/AppData/Local/Programs/DeepSeek Harness/resources/app.asar`。通过 ASAR 文件目录读取下面两个包的 README，没有运行应用或执行包代码。下述行号为包内 README，不是仓库文档行号。

| 包 / 行 | 发布包声明 | 修复含义 |
|---|---|---|
| `@deepseek-ai/dsh-client-ui-layout/README.md:30` | 原生右栏初次约 45% viewport，保存用户宽度偏好，上限 70%；为中间区保留约 400px，空间不足先缩再关闭 | 宽度由宿主管理，支持用户拖宽；不能声称存在已核实的任意设宽 API |
| 同文件 `:32`、`:59` | `main` keyed slot，`ctx.layout.selectPanel`；选全局 main 会改变当前面板，右栏 owner 只在 Conversation 显示时渲染 | 直接注册 `main` 会替换对话，不是聊天与 ACP 右侧并排的等价修复 |
| 同文件 `:34` | `shell.overlay` 跨整个 frame；`shell.bottom` 位于全局列下方 | overlay 是覆盖层；ACP 的 G 应先留在 ACP 容器内，不再抢占宿主全局区域 |
| `@deepseek-ai/dsh-client-ui-sidebar-right/README.md:39–50` | normal push 由对话让位，fullscreen 复用同一内容树而不重建 tab；窄屏可自动 fullscreen | 使用原生 push/全屏，不自画第二套尺寸/全屏/拖动管理；全屏会覆盖对话，必须明确 |
| 同文件 `:61`、`:74` | 按真实 Session 组织右侧布局，支持恢复；背景保留与前景展示分开 | 跨会话保持工作需采用状态恢复和单实例所有权，不伪造固定 Session |
| 同文件 `:83–84` | `sidebarRightTabs.register` 类型，`sidebar.right.pane.tab` keyed body；支持 `keepMounted` 与框架 `useTabInfo()` | 可以直接注册真实 ACP body；visibility、target、session 需从真实 hook 读取并验证 |
| 同文件 `:93–99` | `ctx.sidebarRight.openResource/openTab` 打开与去重、展开，支持分屏、焦点和全屏操作 | ACP-RPH 按钮应导航原生 tab，而不是切自有浮层开关 |
| 同文件 `:144` | 无 Session 时没有右侧 surface | 开始页要提示进入/新建会话，或另设明确的全局模式；不能默默重建浮层作为默认 |

## 建议与证据边界

修复方案统一追加在[插件计划 §13.14](../docs/reference/next-change/ACP_Frontend_DSH_Pluginization_Plan_20261102.md#native-docking-fix-20261104)。本记录只提供诊断证据，不是第二份实施总方案。

静态根因与原生能力核对已完成。尚未实施 native tab body 替换、原生打开入口、实例迁移、资源构建和真实桌面验收。上一轮独立原型 100 项检查只覆盖 ACP 内部布局，未覆盖真实宿主的 dock/overlay 归属，不能证明此问题已修复。
