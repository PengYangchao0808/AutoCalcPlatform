# ACP 架构缺陷整改后独立复核

日期：2026-10-21。审查提交：`3670790363c7a1959ad1dbe4b4297adec518952a`。整改报告声明的代码候选为 `bb309134c7b2bb21d8a8bfcb5589516a5b0cf798`；两者之间业务源码未变，验收脚本 `scripts/acceptance/four_combinations.py` 有修改。

**结论：FAIL，仍存在需要修复的问题。确认 11 项产品/执行缺陷及 1 项验收证据缺口，共 7 项 P1、5 项 P2。现有测试通过不足以支持“本批缺陷已全部关闭”。**

本次对照已通过评审的实施计划、原 D01—D13 报告、整改报告及实际提交；分别审查 worker/执行权、scheduler/发布、Data、前端和 bounded 身份，并执行定向回归和独立反例。没有修改业务代码、原计划或原报告，也没有重启生产服务。原有文档及 `e2e-task-root.txt` 的 dirty 状态保留；没有打包该文件内容。

## 1. 问题总表

| 编号 | 级别 | 问题及用户影响 | 主要驻点 |
|---|---|---|---|
| R01 | P1 | `task_result.json` 未保存科学结果正文，已完成任务没有可恢复/正常展示的能量等数据 | `runtime/worker.py:983`；`calculations/attempt_publication.py:184` |
| R02 | P1 | 默认 worker 丢失任务指定的配置、核数和内存，实际执行与提交意图不一致 | `calculations/attempt_freeze.py:785`；`runtime/worker.py:310` |
| R03 | P1 | 死锁持有者恢复存在删除新锁的竞争，同 attempt 可出现两个执行权持有者 | `runtime/attempt_freeze.py:497` |
| R04 | P1 | 冻结组/信封错误路径没有取得执行权也能覆盖权威回执 | `worker_bootstrap.py:945`、`:963` |
| R05 | P1 | 失去执行权的 worker 仍先写 `task_result.json`，提交守卫只保护 receipt | `runtime/worker.py:1142`、`:1320` |
| R06 | P1 | 新生产 prepare adapter 传给真实 fetcher 的记录缺少远端定位，冷缓存准备失败 | `services_data_bootstrap.py:1166` |
| R07 | P1 | cache attempt 校验与 open 分离，请求 attempt 1 可读到 attempt 2 正文 | `services_data_bootstrap.py:1710`、`:1738` |
| R08 | P2 | 缓存驱逐后仍显示 READY，同身份重新入队仍返回旧 SUCCEEDED，无法重建 | `data/read_root.py:494`；`data/operations.py:988` |
| R09 | P2 | 新 prepare 漏掉 manifest 引用的频率模式辅助产物，报告 prepared 但模式未缓存 | `services_data_bootstrap.py:841` |
| R10 | P2 | bounded reader 接受拼接的旧源 block，并认为可收养 | `analysis/bounded/manifest.py:389`、`:403` |
| R11 | P2 | NMR 旧请求迟到仍可删除新画布并留下未释放实例 | `features/nmr.js:31`、`:59` |
| R12 | P2 | 四组合 harness 未运行前端组合或真实 legacy 提交，不能证明四格兼容性 PASS | `scripts/acceptance/four_combinations.py:219`、`:316` |

完整路径见各项说明。R01—R11 均有实际反例；R12 由 harness 执行路径和其已发布回执直接核验。

## 2. 逐项证据与修复建议

### R01：科学正文只剩摘要，发布器无法恢复原始结果

位置：[worker.py:983](../../src/acp_services/runtime/worker.py:983)、[attempt_publication.py:184](../../src/acp/calculations/attempt_publication.py:184)、[attempt_publication.py:221](../../src/acp/calculations/attempt_publication.py:221)。

`write_task_result(..., result)` 仅写 qualification、状态和 `_result_digest(result)`，没有写 `result.to_dict()`。能量、坐标、频率、payload 和工件引用均不在这个持久文件中。发布器随后只复制此资格文件，并将其作为一个 FILE 产品；`ScientificResultRecord.summary` 也没有科学数值。

独立反例输入真实形状的 singlepoint 结果，能量为 **-76.0 Hartree**。发布后：`task_result.json` 没有能量正文；scientific summary 没有 `energy_hartree`；manifest 仅含 FILE `task_result.json`；真实 structure-viewer 投影的能量为 **null**。几何显示可回退到 `input.xyz`，不能据此认定科学结果发布完整。

证据：[controller.log](evidence/controller.log)，诊断入口：[controller.py](reproductions/controller.py)。

建议：用单一 codec 保存完整原始 TaskResult 及其 job/attempt/submission/plan 绑定，把资格与正文一起纳入 receipt 摘要；发布时复用原有科学产品契约生成 energy_report、结构及其他适用产品。测试必须检查实际数值、工件及重启后的可读性，不能只断言 manifest 文件存在或 publication complete。

### R02：配置与资源在 freeze → worker 间丢失

位置：[attempt_freeze.py:785](../../src/acp/calculations/attempt_freeze.py:785)、[legacy_adapters.py:371](../../src/acp/calculations/legacy_adapters.py:371)、[worker.py:310](../../src/acp_services/runtime/worker.py:310)。

freeze builder 经 `to_task_request()` 转换后丢弃 `_legacy_binding`，而 config 正是保存在这个 binding 中。`_build_simple_request` 没有把已解析配置中的资源填入 TaskResources；worker 创建 `TaskContext` 时又没有 config。

反例：JobSpec 指定 `nproc=3`、`mem=2GB` 和自定义 ORCA 路径。builder 的配置解析正确，但真实 `CccpTaskExecutor` 接收到的 `TaskResources` 为 `{}`，传给科学 runner 的 `TaskContext.config` 为 **None**。旧 CLI 路径会传递 `--config/--nproc/--mem`。这会改变可执行文件选择、资源使用及未显式写入请求的科学配置默认值。

证据：[controller.log](evidence/controller.log)。该反例实际经过 CccpTaskExecutor，科学 runner 替身只用于捕获入参并返回 TaskResult，没有调用外部 QC。

建议：冻结可执行的科学配置快照或等效的完整解析结果，通过独立 worker 的装配端口还原 TaskContext；把资源显式写入 TaskResources，保留内存单位契约。增加自定义配置和非默认资源的默认提交集成测试。

### R03：死持有者回收不具原子性

位置：[attempt_freeze.py:497](../../src/acp_services/runtime/attempt_freeze.py:497)。

两个恢复进程都读取旧的死 claim 后，A 删除旧文件并取得新 claim；B 随后仍依据旧观测 unlink 当前路径，删除的已经是 A 的新 claim，再取得自己的 claim。确定性双进程 barrier 反例中，两者均返回 **HELD**、两进程同时存活，最终锁只记录 B。worker 在科学执行之后才复核写权限，因此不能阻止重复 QC。

另一个静态风险在 [attempt_freeze.py:326](../../src/acp_services/runtime/attempt_freeze.py:326)：claim 没有主机身份，liveness 仅查本机 PID；共享 LSF 目录不能据此证明其他节点上的持有者已死。本轮未将该跨节点场景记为真实 LSF 验收结果。

证据：[worker_claim_and_bootstrap.log](evidence/worker_claim_and_bootstrap.log)，入口：[worker_claim_and_bootstrap.py](reproductions/worker_claim_and_bootstrap.py)。

建议：在稳定的互斥恢复边界中检查旧 claim 并替换，所有领取/回收操作遵守同一协议；不可凭旧读结果 unlink 当前锁。失联恢复还须区分主机并确认原进程及子进程停止。新增两个恢复者同时抢同一旧 claim 的测试。

### R04：入口装配错误可以破坏已有成功回执

位置：[worker_bootstrap.py:945](../../src/acp_services/worker_bootstrap.py:945)、[worker_bootstrap.py:963](../../src/acp_services/worker_bootstrap.py:963)。

`main` 在验证冻结组失败或无法解码 envelope 时，尚未获取执行权就调用 `_write_unbound_failure(receipt_path, ...)`。该路径直接写 canonical receipt，没有保留旧文件。

反例先写入合法 completed ExecutionReceipt，再用 NOT_HELD 端口及缺失 manifest 调入口：**exit=2，执行权端口未调用，原 receipt 字节已改变，内容被 worker_assembly_error 替换。**

证据同 [worker_claim_and_bootstrap.log](evidence/worker_claim_and_bootstrap.log)。建议将无法绑定身份的错误写入独立诊断或标准错误输出；不允许触碰权威 receipt。增加组损坏、信封损坏且已有成功回执的负例。

### R05：失权提交守卫只覆盖 receipt，没有覆盖 task_result

位置：[worker.py:1142](../../src/acp_services/runtime/worker.py:1142)、[worker.py:1320](../../src/acp_services/runtime/worker.py:1320)。

`CccpTaskExecutor.execute` 内的 `outcome_from_task_result` 先写权威 task_result，返回后 FrozenWorker 才调用 `before_write()` 复核执行权。反例实际经过 CccpTaskExecutor、有效单点请求和 TaskResult，端口 acquire=HELD、verify=LOST；结果返回 `WorkerRefusal(execution_right_lost)`，receipt 不存在，但权威 **task_result.json 已被写入**。失权者因此仍可覆盖合法持有者的完成事实，并造成 receipt 摘要失配。

证据：[worker_lost_write.log](evidence/worker_lost_write.log)，入口：[worker_lost_write.py](reproductions/worker_lost_write.py)。

建议：科学 executor 先返回内存结果或独立 staging；task_result 与 receipt 的权威提交统一受持权边界保护。测试断言两个权威路径均不被竞争失败/失权者创建或覆盖。

### R06：真实 RemoteResultFetcher 接线失败

位置：[services_data_bootstrap.py:1166](../../src/acp/services_data_bootstrap.py:1166)、[fetcher.py:130](../../src/acp/scheduler/remote/fetcher.py:130)。

source adapter 仅构造 `SimpleNamespace(id, attempt)`，然后传给真实 fetcher。真实 `RemoteResultFetcher.resolve()` 必须读取 `record.result` 中的 node/remote_dir，所以尚未到 SSH 即报属性错误。本批将该 adapter 接入生产 prepare，fake fetcher 没有读取 result，未暴露问题。

反例：**prepared=False，detail=publish_rejected:source_unavailable，底层 TransferSourceError: SimpleNamespace has no attribute result。**

证据：[data.json](evidence/data.json)，入口：[data.py](reproductions/data.py)。建议从已经核验身份的记录/绑定提供完整远端定位，并用真实 RemoteResultFetcher 配合受控 transport 做契约测试。

### R07：校验过 attempt 的路径不等于校验过打开的正文

位置：[services_data_bootstrap.py:1710](../../src/acp/services_data_bootstrap.py:1710)、[services_data_bootstrap.py:1738](../../src/acp/services_data_bootstrap.py:1738)。

`get_cached(..., attempt=1)` 先校验 sidecar，随后才 `_open_within_root()`。两者之间若发布者原子替换正文和 sidecar，descriptor 打开的是 attempt 2。后续 `os.stat(approved)` 仍对相同路径取当前 inode，无法辨认早先批准的 attempt 1。

确定性反例：**请求 attempt=1，当前 attempt=2，实际返回 ATTEMPT_2 字节。** descriptor pin 保护了打开之后的文件，未保护批准快照到打开之间的身份窗口。

证据：[data.json](evidence/data.json)。建议将 sidecar/attempt 校验和 descriptor 获取置于与发布共享的互斥边界，或使用不可变的缓存版本定位；检查打开文件与被批准身份的一致性，不能只比较两个已经更新的同路径观察。

### R08：缓存淘汰未使成功 operation 过期

位置：[read_root.py:494](../../src/acp_services/data/read_root.py:494)、[operations.py:988](../../src/acp_services/data/operations.py:988)。

真实 `cache.purge_job` 后 operation 仍是 SUCCEEDED。reader 无正文时直接转发其 READY；同身份 enqueue 合并旧成功，只有已手动 EXPIRED 的行才创建新 epoch。

反例：**availability=ready、prepared_root=null；再次 enqueue 仍为原 succeeded；pending_count=0、lease_next=None。** 现有集成测试手动调用 expire，未证明生产驱逐/启动恢复已经连接这个动作。

证据：[data.json](evidence/data.json)。建议由驱逐命令/后台恢复使旧 operation 过期并允许新 epoch；availability 无可用正文时不得报告 READY，同时保留 GET 只读。

### R09：prepare 遗漏频率模式辅助产物

位置：[services_data_bootstrap.py:841](../../src/acp/services_data_bootstrap.py:841)，对照 [remote_structure_cache.py:700](../../src/acp/results/remote_structure_cache.py:700)。

新 prepare 用静态 catalog 路径循环代替旧 `fetch_catalog`，没有迁移 `_fetch_catalog_products`。manifest 引用 `frequencies/normal_modes.json` 时，新 prepare 返回 **prepared=True**，却从未请求该文件；旧 helper 在相同受控数据下会缓存它。

证据：[data.json](evidence/data.json)。建议在同一完整 lease/publish guard 下恢复 manifest 辅助产品发现与准备，并定义 required/optional 产品对 ready 的影响。

### R10：bounded 外壳有效不能证明块的派生身份有效

位置：[manifest.py:389](../../src/cccp/analysis/bounded/manifest.py:389)、[manifest.py:403](../../src/cccp/analysis/bounded/manifest.py:403)。

当前 reader 比较 block 外壳的 source_identity_digest，以及 product kind/input digest，没有验证 block.product.identity.params_digest 确由当前完整源身份和块参数推导。

反例创建 raw input 相同、源 algorithm_version 不同的两个 chunk manifest，将新 manifest 首块的 product 替换成旧块、保留新外壳。`BoundedProduct.from_dict()` 接受，**is_cache_adoptable=True**，块身份却是旧源的身份。生成入口的 D10 修复有效，读取/收养门仍有缺口。

证据：[frontend_science.json](evidence/frontend_science.json)，入口：[frontend_science.py](reproductions/frontend_science.py) 的 `probe_bounded`。

建议共享并验证完整块身份推导，覆盖旧块拼接及跨范围/分辨率负例；保留历史 v1 可读、不可作为新结果收养的政策。

### R11：NMR 迟到响应仍会清空新画布

位置：[nmr.js:31](../../frontend/js/workbench/features/nmr.js:31)、[nmr.js:59](../../frontend/js/workbench/features/nmr.js:59)。

本批将 dispose 移到 fetch 前，但 await 后没有检查 token/当前任务身份。请求 A 等待、B 完成真实画布加载后，A 的迟到 null 或异常响应仍写 report body；B 的实例没有同步释放。

真实 Chromium + 仓库 3Dmol 反例：覆盖前 **canvasConnected=true、atoms=3、disposed=false**；A 迟到后 **canvasConnected=false、disposed=false、registryContainsDetachedViewer=true**，body 变成 nmr.no_report。

证据：[frontend_science.json](evidence/frontend_science.json)。建议成功/空报告/异常分支在触碰 DOM 前统一验证 token 和任务身份；增加 A/B 逆序返回、切换任务及失败响应竞争测试。

### R12：四组合全 PASS 的证据范围不足

位置：[four_combinations.py:219](../../scripts/acceptance/four_combinations.py:219)、[four_combinations.py:316](../../scripts/acceptance/four_combinations.py:316)。

脚本的 frontend 维度仅用于计算文件树 hash，没有加载 F0/F1 页面、运行 JS 或把其请求连接到指定 S0/S1。相同 service 的两格复用相同后端结果。七个 legacy “提交”实际上是缺必要参数的 CLI 调用，以 argparse exit=2 且错误文本不含 worker_bootstrap 为成功条件，没有创建或执行任务。

因此现有四个 PASS 只能支持服务基础接口和 CLI 缺参错误仍可响应，不能证明四种前后端组合、旧字段/副作用以及 legacy 正常提交兼容。另有 harness 代码在报告声明的 code-candidate SHA 之后修改，见 [baseline.json](evidence/baseline.json)。

证据为现有 [four-combination 回执](../architecture_remediation_20261020/four_combinations/summary.json) 和脚本执行路径。

建议收窄当前证据结论；对四格实际加载对应静态包并连接服务，以受控科学后端完成普通提交、详情/结果/错误/副作用断言。没有执行的兼容性单元格保持未验证，不能凭文件 hash 或 exit=2 关闭。

## 3. 测试结果与边界

本次重新执行了现有定向测试；独立反例与正式回归分别统计。反例脚本打印的是已复现缺陷，退出 0 表示诊断运行完成，**不是产品 PASS**。

- worker/contracts、bounded、vibration pins、D12/D13：309 passed，见 `evidence/contracts-tests.log` 和对应 JUnit。
- chain/submission/enablement、架构隔离、Data、D03 HTTP、现有浏览器套件：324 passed、4 failed；四个失败均来自浏览器 viewer 创建阶段，见 `evidence/integration-tests.log`。该运行在沙箱外完成，没有将实际失败改写为 NOT_VERIFIED。
- Node 全套：327 passed、0 failed，见 [node.log](evidence/node.log)。
- NMR 请求乱序反例已经以显式 `--no-sandbox` 的真实 Chromium 独立确认；其余浏览器启动差异另记在 `evidence/browser-explicit.log`，不与 NMR 竞争缺陷混算。
- 沙箱内先前的 TestClient 阻塞运行已中止，保留日志，不计为产品 PASS 或 FAIL。未重新运行历史约半小时的全量非慢套件；原报告的 10,005/2,124 计数仅作为历史证据。
- 未调用真实 QC 或 LSF，相关环境结论继续 NOT_VERIFIED；上述冷缓存 fetcher 契约反例在 SSH 之前即失败，不依赖集群凭据。

## 4. 对整改结论的影响与建议顺序

D03 字段映射、D09 冷启动逻辑、D12/D13 守护迁移，以及 bounded 生成入口的新身份区分均看到实际修复。它们不抵消默认执行链、Data 接线和恢复边界上的反例。

建议先修 R01/R02，保证真正保存计算结果并按任务指定配置执行；同期收紧 R03—R05 的执行权与所有权威写入边界。随后修 R06/R07 的生产远端接线和跨 attempt 读取，再收口缓存驱逐、辅助产品、bounded 收养及 NMR 生命周期，最后补足四组合验收。

当前应重新打开受影响的 D04/D05/D06/D07、D08 生命周期及 D10 收养门，并撤回 A/G1 与默认链整体验收 PASS。已经证明的子场景可以保留局部通过，真实环境及独立打包的原 OPEN/NOT_VERIFIED 继续如实记录。

证据和诊断入口见 [reproductions/README.md](reproductions/README.md)；文件摘要见 `evidence_manifest.json`。原 20261019 与本批整改报告均未改写。
