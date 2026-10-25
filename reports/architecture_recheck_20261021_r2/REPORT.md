# ACP 架构整改第二轮独立复核（修复前快照）

日期：2026-10-21。**结论：FAIL，确认 4 项功能缺陷和 1 项验收证据缺口，共 1 项 P1、4 项 P2。** 本报告保存此次检查的结果；后续 debug 不回写这些原始结果。

审查对象为 `3670790363c7a1959ad1dbe4b4297adec518952a` 加本次尚未提交的源码/测试修改，不能仅以 HEAD 代表候选。[baseline.json](evidence/baseline.json) 记录 18 个相关文件摘要；[candidate.patch](evidence/candidate.patch) 保存相关 tracked diff，SHA256 为 `44079de7ab200dfe92461a8ecc3b01e4fddb718183cf0ba79af09e4155aefe7c`。新增测试文件另列于 baseline 的摘要表，未包含在 tracked diff 中。检查结束时这些文件没有漂移。

本阶段未修改用户实现、测试、计划及旧报告，没有重启生产服务。未打包 `tests/baseline/refactor-evidence/e2e-task-root.txt` 内容。

## 1. 剩余问题

| 编号 | 级别 | 问题 | 驻点 |
|---|---|---|---|
| R2-01 | P1 | 父 worker 已死、旧计算子进程仍活时，新 worker 仍可取得执行权 | `src/acp_services/runtime/attempt_freeze.py:336,551` |
| R2-02 | P2 | 全局成功 operation 超过 1,000 条后，驱逐缓存可能无法重新准备 | `src/acp/scheduler/manager.py:5006` |
| R2-03 | P2 | 频率辅助文件首次瞬断后，重试提前 `already_ready`，缺失文件不再下载 | `src/acp/services_data_bootstrap.py:822,850` |
| R2-04 | P2 | 多分辨率 block 身份遗漏选点策略，可跨策略误用缓存/接受拼接 | `src/cccp/analysis/bounded/multiresolution.py:150`；`identity.py:384` |
| R2-05 | P2 | 四组合仍未覆盖七类 legacy 普通提交及前端 JS 行为，不能证明完整兼容性 | `scripts/acceptance/four_combinations.py:263,421,631,720` |

### R2-01：恢复执行权没有证明旧计算已停止（R03 剩余边界）

[_holder_alive](../../src/acp_services/runtime/attempt_freeze.py:336) 只检查 claim 内父 PID；[acquire](../../src/acp_services/runtime/attempt_freeze.py:551) 发现父 PID 死亡后立即换锁。claim 没有计算子进程/独占进程组身份，也没有停止确认端口。父进程被 SIGKILL 等方式终止不会自动证明 QC 子进程已停止；ORCA 路径使用普通 `subprocess.Popen`，没有父进程死亡联动保证。

真实进程反例：worker 取得锁，启动受控长驻子进程后 `os._exit()`；确认父进程退出而子进程存活，新领取返回 **HELD**。这允许同一工作目录中的旧计算与新计算并行。此反例使用受控子进程，没有执行真实 QC。[原始结果](evidence/orphan-claim.json)、[复现脚本](reproductions/orphan_claim.py)。

建议：持久记录可验证的执行进程组/子进程身份，或注入 controller 的停止确认；只有旧执行及子进程全部停止才允许接管。无法确认时拒绝自动重算。补测父进程突然死亡、子进程继续存活及随后停止的恢复矩阵。

### R2-02：全局分页截断使缓存重建失效（R08 剩余边界）

[manager.py:5006](../../src/acp/scheduler/manager.py:5006) 先 `operations.list(states=["succeeded"])` 再按 job/attempt 过滤，而 [operations.py:818](../../src/acp_services/data/operations.py:818) 默认只返回全局最早 1,000 条。

成对反例：没有其他成功记录时，正文缺失会过期旧 operation，创建新 epoch、入队 1 条；先放入 1,000 条其他成功记录后，目标记录仍为原 **SUCCEEDED**，`pending_count=0`，没有新 lease。读侧已正确返回 ABSENT，但后台无法重建。[原始结果](evidence/data.json)、[复现脚本](reproductions/data.py)。

建议：提供按 job/attempt/resource 定向查询和过期的服务 API，避免全局扫描后过滤；补测目标排在第 1,001 条及以后。不应仅把固定上限调大。

### R2-03：cache hit 绕过辅助产物重试（R09 剩余边界）

[822 行附近](../../src/acp/services_data_bootstrap.py:822) 只要主 catalog 就绪即返回；[850 行](../../src/acp/services_data_bootstrap.py:850) 的频率模式发现/下载循环随后才执行。

成对反例：冷缓存正常准备能下载 `RESULT/frequencies/normal_modes.json`；首次该文件因瞬断下载失败时，manifest 已经落盘。下一 lease 返回 **prepared=true / already_ready**，辅助文件仍缺失，总下载尝试仍为 1 次。已有主缓存缺少辅助文件的升级场景同样受影响。[原始结果](evidence/data.json)。

建议：主 catalog 命中只跳过主文件传输，仍检查并补齐同一 manifest 引用的辅助文件；明确 required/optional 的完成规则，保留既定 404 兼容语义。增加首次失败后真正再次下载并成功的测试。

### R2-04：同源、不同选点策略产生相同 block identity（R10 同域新增）

[生成器](../../src/cccp/analysis/bounded/multiresolution.py:150) 的 block 参数没有 `key_points`/`retain_key_points`；[新 reader 推导](../../src/cccp/analysis/bounded/identity.py:384) 也省略这些字段。

同一曲线、`resolutions=[3]` 下，仅切换 `retain_key_points=False/True`：父身份不同，块实际分别有 **3/5 个点**，但完整 block identity 相同。将 False 的 3 点块放入 True 的 manifest 后，`BoundedProduct.from_dict()` 接受，`is_cache_adoptable()` 仍为 **true**，保留关键点的契约被破坏。[原始结果](evidence/bounded-params.log)、[复现脚本](reproductions/bounded_params.py)。

建议：生成器和 reader 共享包含全部选点参数的块身份推导；兼顾旧身份的保守处理。补测 retain/key-point 参数改变时身份不同，以及跨策略拼接拒绝。原 R10 的跨源拼接反例已修复，此处是同源参数完整性问题。

### R2-05：验收 harness 仍缺少计划规定的行为覆盖（R12 部分修复）

新增 origin proxy、静态资产加载和普通 API fake 提交有实际价值，但 [_probe_frontend_load](../../scripts/acceptance/four_combinations.py:421) 只 GET HTML 和至多 6 个资产，不执行 JS；API 提交由 Python 硬编码产生。受控 HTTP 边界返回语法错误的 JS 时，该 checker 仍返回 **ok=true**。[检查结果](evidence/harness-inspection.json)、[复现脚本](reproductions/harness_inspection.py)。这不是实际四组合运行。

七类 legacy 仍仅检查缺参数 CLI；新增 [_probe_legacy_real](../../scripts/acceptance/four_combinations.py:263) 实际执行 `acp task execute --workflow fake`，不覆盖计划 todo 41 指定的 `Confsearch/PESsearch/BatchOptimize/XtbPathSearch/XtbConstrainedOpt/tsmode/nmr` 普通提交及 legacy 选择路径。单格虽标注 browser 未验证，整体仍可 `result=PASS / all_pass=true`。

建议：为七类 workflow 分别准备合法输入，通过普通入口及受控 backend 验证原选择/行为；前端组合执行实际 JS 与请求路径。已通过的静态/API 子检查保留 PASS；尚未覆盖的兼容性单独记 NOT_VERIFIED，不能据此宣告完整四组合 PASS。本轮没有重跑四个 live worktree：当前修改未提交，直接 `--f1-rev HEAD` 将测到旧提交。

## 2. 上轮问题关闭情况

| 上轮项 | 本轮结论 |
|---|---|
| R01 科学正文/发布 | 已修复；原始 TaskResult 正文持久化，singlepoint 能量 -76.0 到达真实 viewer 投影 |
| R02 配置/资源 | 已修复；冻结配置快照及显式资源，worker 收到 3 核/2GB 和自定义配置 |
| R03 领取竞争 | 原 read/unlink 竞争及明确跨主机 claim 已修复；子进程停止确认仍见 R2-01 |
| R04 未持权覆盖回执 | 已修复；错误写独立 `.unbound.json`，原回执字节保持 |
| R05 失权写 task_result | 默认 CCCP/Probe 路径已修复；pending document 在持权复核后提交 |
| R06 fetcher 记录不完整 | 已修复；真实 RemoteResultFetcher + 受控 SFTP 冷准备成功 |
| R07 attempt 校验/open 竞争 | 已修复；真实 publisher 被共享 mutex 阻塞，attempt 1 请求返回 attempt 1 正文 |
| R08 驱逐后假 READY | 读侧及小规模重新入队已修复；批量记录边界见 R2-02 |
| R09 频率模式缺失 | 正常冷准备已修复；瞬断重试见 R2-03 |
| R10 跨源 block 拼接 | 原反例已修复；同源参数遗漏另见 R2-04 |
| R11 NMR 迟到响应 | 已修复；Node 和真实 Chromium 均验证旧 null 响应不再删除新 canvas |
| R12 四组合证据 | 已补静态 origin/API 子检查；完整 legacy/frontend 行为证据仍见 R2-05 |

## 3. 验证结果与范围

| 检查 | 结果 | 原始记录 |
|---|---|---|
| 新增回归、chain、worker、harness、Services 隔离和科学架构护栏 | **272 passed** | [日志](evidence/root-tests.log)、[JUnit](evidence/root-tests.xml) |
| Data 定向回归（含与上行重复的新测试） | **117 passed, 19 deselected** | [日志](evidence/data-tests.log) |
| bounded 定向回归（含与首行重复的新测试） | **78 passed, 16 deselected** | [日志](evidence/bounded-tests.log) |
| 全部 Node 模块测试 | **329 passed, 0 failed** | [日志](evidence/node-all-outside.log) |
| 真实 Chromium NMR 迟到响应 | **PASS：新 canvas connected、3 atoms、原实例仍注册** | [结果](evidence/nmr-browser.json)、[脚本](reproductions/nmr_late_response.py) |
| 正式 Chromium NMR/vibration 套件 | **4 failed** | [日志](evidence/browser-tests-outside.log)、[JUnit](evidence/browser-tests.xml) |

正式浏览器在 Chromium `153.0.8010.12` 上实际启动，失败集中于 viewer 创建。补充观察记录显示图形上下文/着色器初始化不稳定；失败不能改写为 PASS 或 NOT_VERIFIED，也不将四个失败直接算作四项新增产品缺陷。独立 R11 脚本验证 DOM/实例生命周期，不证明像素渲染通过，两者不矛盾。

上述 Python 批次有重叠，未相加当作去重总数。没有重复全量 non-slow、真实 QC、真实 LSF 或生产发布；这些未由本轮补证。三个功能反例脚本的 exit 0 仅代表诊断执行成功，输出中的错误事实不是产品 PASS。

在仓库根目录复现：

```bash
PYTHONPATH=src:. /opt/acp2/venv/bin/python reports/architecture_recheck_20261021_r2/reproductions/orphan_claim.py
PYTHONPATH=src:. /opt/acp2/venv/bin/python reports/architecture_recheck_20261021_r2/reproductions/data.py
PYTHONPATH=src:. /opt/acp2/venv/bin/python reports/architecture_recheck_20261021_r2/reproductions/bounded_params.py
PYTHONPATH=src:. /opt/acp2/venv/bin/python reports/architecture_recheck_20261021_r2/reproductions/nmr_late_response.py
PYTHONPATH=src:. /opt/acp2/venv/bin/python reports/architecture_recheck_20261021_r2/reproductions/harness_inspection.py
```

所有归档文件的 SHA256 见 [SHA256SUMS](SHA256SUMS)。
