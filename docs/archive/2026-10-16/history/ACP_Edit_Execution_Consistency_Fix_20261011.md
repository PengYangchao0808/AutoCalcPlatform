# 编辑参数与执行输入一致性修复（2026-10-11）

用户报告的统一根因已确认：编辑器修改表单或 `method.levels`，执行器优先读取另一份输入或扁平参数；旧值未同步，且回填容易用目录默认值覆盖实际值。`optimize` 默认档生成的 `--scf-convergence Tight` 确实不被原简单工作流 CLI 接受。

本次修改不迁移生产任务、不自动重算历史任务，也不改动 QC 接口或计算任务核心的驻点。

## 工作流处理结果

| 工作流 | 修复或明确的边界 |
| --- | --- |
| singlepoint / optimize / frequency | 简单 CLI 接受 SCF 收敛与 grid，并传入计算层；默认 optimize 命令可解析。被动编辑保存电荷、自旋。已知但无 CLI 消费者的参数在入队前报错。 |
| casscf / xtb_optimize | 保留原有 levels 执行链，修复被动编辑的电荷、自旋传递。 |
| PESsearch（bond） | 从实际 coordinate/protocol 回填；修改范围、点数、驱动与方法层级后更新执行参数并重建 `scan_request`。电荷、自旋同步到结构源及单点协议；原子选择保存到坐标与 selection。 |
| scan | 从实际坐标和顶层点数回填；修改后更新 `input.scan_coordinates`、顶层点数和 ScanTS 开关。普通 scan 面板移除没有消费者的 PES 专属驱动参数，改用可执行的优化步数；新增的无效驱动编辑明确报错。多坐标任务显示首个真实坐标并提示联动关系，若修改单坐标镜像会报错，须通过完整 `input.scan_coordinates` 编辑。 |
| irc | 顶层 maxpoints/step 回填并同步编辑值，再经过上游 TS 验证。方法、电子态、结构证据和 config 继续由已验证 TS 来源决定；不匹配的覆盖明确报错。 |
| nmr | GIAO 字段同步到 resolver 优先读取的扁平别名。候选增删、名称/结构/电荷/自旋修改与枚举参数保存；候选使用独立输入文件并携带各自名称、电子态元数据，同名候选分配不同执行 ID，避免共用计算目录。远程上传全部候选和实验资产。候选集合或几何改变须创建新任务。 |
| BatchOptimize | 回填保留 stored levels；全局电子态进入差异与提交投影；编辑时对角色关键字使用创建入口同一个大小写规范化函数。条目与 profile/角色编辑链保留。 |
| Confsearch | ewin 可显式清空，溶剂相关修改同步到顶层消费者。 |
| tsmode | 通用编辑明确禁用；提示在 TS Mode 专用向导选择模式、验证频率证据并创建新任务。 |
| XtbPathSearch / OrcaGradient | 通用编辑明确禁用；提示在 PES2TS 修改冻结请求并创建新任务。 |

三个专用/冻结输入工作流仍是 active，也仍登记在 edit coverage registry；禁用的是无法表达其执行契约的通用编辑入口。直接重跑的原有契约不受影响。

## 实现与防线

- `scheduler/edit_parameters.py` 集中维护执行参数的显示投影和编辑映射。原始 `editable_spec` 保留用于无修改提交、差异与指纹；目录回填值不会因只修改另一个字段而全部写入执行配置。实际改变的字段才参与桥接。
- 编辑器提交 `method_projection_changed` 标识实际表单改动。即使目标值恰好等于陈旧的 stored levels，也会按实际执行值比较并更新输入；不带标识的原样 spec 重发继续保持无修改语义。API 调用方遇到这种歧义时可显式设为 `true`。该标识只属于请求，不进入科学 spec。
- 编辑请求省略 method/input/resources/tags/node_tags/remark 时保留原值；对象局部更新合并，显式空对象/列表/字符串表示清空。结构更换会清除过时来源描述，避免把新几何与旧 source_ref 混用；清空仍须满足工作流自身的校验。
- 编辑与新建共享电子态扩展、BatchOptimize 关键字规范化及 PES 请求准备。通用 TS Mode 编辑不进入执行路径，因此不会绕过专用入口的证据准备。
- 编辑步骤同步共享向导的步骤状态，保证 PES/scan 原子点选在参数步骤可用。预览、草稿和提交使用远程端点的超时策略。
- 服务端用请求本身的补丁负载计算幂等 hash，完成后的重放无需再与已改变的源 spec 合并；前端同一次确认后的网络重试复用 request_id。保留旧操作记录的 hash 兼容路径。
- 远程简单工作流在提交前上传 `electronic_state.json`，命令携带 `--spin-config`。continue 同时上传本次 `resume_source.json`；缺失回执会阻断提交。
- 远程 rerun 将旧根配置及 WORK 科学产物移入旧 attempt 归档；continue 保留科学结果供身份验证后恢复。目录读取或归档失败阻断提交，不删除旧目录。配置侧文件补入 `_SCHEDULER_MARKERS`，调度身份二级防线保留。
- `.structure_history` 的已登记不可变来源保留，这是历史结果复用契约。活跃 attempt 的“当前结果路径”不能自动指向旧快照；快照必须匹配登记的几何校验和，其电荷等信息从旧 attempt 输入/元数据恢复。
- 远程编辑草稿通过 `_job_read_root` 获取结果目录与 effective-config 参考快照。上次 effective-config 仍是参考信息，不替代本次提交 spec；可编辑值优先投影本次实际执行参数。

## 验证证据与限制

新增测试位于 `tests/test_acp_edit_parameter_execution.py` 和 `tests/test_remote_edit_parameter_execution.py`，另扩展了 edit coverage、marker 与历史快照回归。

- 完整非慢测试：8037 项通过、11 项跳过、92 项慢测试被排除；命令为 `pytest -m 'not slow' -q --disable-warnings`。该轮包含架构、grep gates、API、NMR、远程和前端静态护栏。
- 随后完成最后的 NMR 同名隔离、联动 scan 回填及同值编辑意图边界：NMR/编辑定向回归 158 项通过；最后相关回归 614 项通过，追加的前端实际序列化案例 1 项通过。最后两项边界修改在完整测试结束后进行，并由这些相关回归覆盖。
- 其中 42 项直接覆盖本次问题，包括简单 CLI 参数抵达模拟 QC 后端、部分字段编辑、PES 序列化、NMR 候选及同名隔离、原子点选门控、远程上传顺序、缺失恢复回执拒绝和历史快照保护。API 部分编辑重放案例验证旧 revision/fingerprint 可重试、attempt 不递增，并拒绝同一 request_id 的不同修改。
- Python 3.11 全源代码 compileall、JavaScript 语法检查和 `git diff --check` 通过；新增 Python 模块/测试通过 Ruff 检查。

这些验证使用临时任务目录、模拟 QC 后端及模拟 SSH/SFTP/LSF，未提交真实计算或远程作业。真实 ORCA/xTB/CENSO 与生产节点的科学计算结果不属于本次实测证据。

## 跟进：PESsearch 切换为独立 scan 的预检失败

已确认用户指的是更换工作流，而非修改 PESsearch 内的扫描参数。原地重算锁定工作流是既有契约（`docs/archive/2026-10-16/history/ACP_Edit_And_Recalculate_Plan.md` §4.2）；切换为独立 scan 必须选择「创建新任务」。

本次发现两项额外缺陷：工作流选择器在原地模式允许更换工作流，但编辑序列化始终使用源 PESsearch 适配器；创建新任务模式下更换工作流也继续沿用源工作流及冻结输入。另外，提交构造器的表单校验只弹窗后返回，编辑收集器再把具体错误替换成「未能收集到可提交的参数」。

- 原地模式的工作流选择器锁定其他工作流并说明如何切换；前端预检和后端参数构造都提前拦截陈旧/程序修改的工作流值。
- 创建新任务模式更换工作流后，收集目标工作流的完整创建参数，替换源工作流 method/input，使用目标任务名默认值，并重置为目标工作流的协议默认档。原结构、电荷、自旋保留，扫描原子和范围须重新确认。
- 编辑收集过程中将具体校验错误交回预检提示；创建向导继续使用原弹窗行为。收集期间跳过 PES 创建入口的重复确认，并阻断创建成功后的关窗、刷新、草稿清除等副作用。
- 相关非慢回归 529 项通过；新增真实浏览器回归 3 项通过，覆盖工作流锁定、陈旧选择拦截、scan 校验原因及最终预检载荷；已有 scan 创建/点选浏览器回归 13 项通过。API 用例验证新建 scan 的参数正确，原 PESsearch 的状态和 revision 不变。

浏览器测试使用临时 FastAPI 服务，截获创建及预检请求，不执行真实计算。初次运行遇到 Chromium GPU/GL 驱动卡顿与动画帧等待超时；关闭测试浏览器的 GL 与加速并复用仓库已有的 evaluate 轮询后，三个新交互用例实际执行通过，未将环境失败计为通过或跳过。

## 服务加载状态

项目 AGENTS.md 要求代码变更后执行 `sudo systemctl restart acp`。本次已尝试重启，但当前终端的 sudo 返回 `a password is required`，直接调用 systemctl 返回 `Interactive authentication required`。服务重启尚未完成，需要在服务器终端通过系统管理员认证执行该命令，再验证 `/api/v1/status` 与 `/openapi.json` 的新编辑请求字段。

只读检查中 systemd 报告服务为 active；当前工具对本机 8765 健康接口的连接未成功，因此尚未确认新后端已加载。
