**ACP runtime contract remediation — 第三次复核与缓存隔离补充**

复核日期：2026-10-08。当前主计划 305 行，SHA-256 为 `c7dc8e15cf9627dcd893343c7800d41cb94bfff4c7218ca4340358f9a818a042`；代码 HEAD 仍为 `ec3bbb149389ed59834d04f8ffe8e2591b35c0ed`。

**结论：计划结构已合理，上轮六项意见已纳入。本轮发现一个重要遗漏：远程文件缓存需要按 attempt 隔离。将它补入现有任务 6/11 即可，无需增加任务或再次重拆计划。**

**上轮问题的关闭情况。**

本次重新解析了当前依赖矩阵：无环，`Blocks` 与 `Depends on` 的反向关系完全一致，原来的 `2 → 5 → 7 → 2` 已不存在。R0 已改为绿色基线；TS Mode 科学完成/发布状态、期望模式集合、真实 API 执行版本、v2 命名空间与 60 秒预算、无需额外人工质量确认，也均已进入任务正文及验收。代码尚未实施，因此这里确认的是计划要求已补齐。

**P1：当前任务 6 的“缓存存在则返回”不能跨 attempt 沿用。**

原地重跑复用 `job_id`、远端目录和产物相对路径。现有 [RemoteStructureCache.fetch](/ACPServer/AutoCalcPlatform/src/acp/results/remote_structure_cache.py:198) 只以 `job_id + rel_path` 定位缓存，文件存在就直接返回，没有检查 `record.attempt`。

[原地重跑](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:940) 先读取旧缓存保留历史结构，再清理任务目录并递增 attempt；没有随之切换远程缓存版本。现有 `purge_job()` 调用发生在[级联删除任务](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:1705)，不在重跑路径。[后台预取](/ACPServer/AutoCalcPlatform/src/acp/scheduler/manager.py:4478) 也会因旧缓存的 catalog 已存在而跳过新结果抓取。

任务 6 目前只要求缓存缺失时下载，任务 11 只验证冷缓存。这会让实现通过这些验收，却在“重跑前已经看过结果”的常见场景中返回旧科学结果。任务 14 的详情响应版本守卫无法修复文件缓存里的旧字节。

本次使用真实缓存类和 fake fetcher 复现：

| 场景 | 操作 | 实际结果 |
| --- | --- | --- |
| 热缓存跨重跑 | attempt 1 缓存 manifest/结构；同 job 的 attempt 2 远端同路径内容改变，再读 catalog/结构 | 返回 attempt 1 的 manifest 和结构；这两个路径没有重新向远端读取 |
| 保留磁盘缓存后重建实例 | 重新创建 RemoteStructureCache，再请求 attempt 2 | 仍返回 attempt 1 的结构 |
| 旧下载在清缓存后完成 | 阻塞 attempt 1 下载 → `purge_job()` → 释放旧下载 → attempt 2 请求同路径 | 旧下载重新写入共享缓存，attempt 2 再次命中旧结构 |

第三个反例说明：仅在重跑时调用一次 `purge_job()`，不能完成隔离。

**补入任务 6 的要求。**

1. 冻结缓存的执行身份：至少绑定 `job_id + attempt`，执行节点/远端目录改变也必须失效。缓存命中和 catalog readiness 都必须证明属于当前执行身份；旧缓存不能因为路径相同而被接纳。
2. 按 attempt 区分缓存空间，或采用等价的写入隔离。旧 SFTP 请求延迟完成时，只能影响其所属旧 attempt，不能写回新 attempt 的当前缓存。单次清理或一次无锁身份检查均不充分。
3. 将此规则统一用于 `fetch`、`fetch_catalog`、缓存读取根和后台 prefetch。新 attempt 尚未发布结果时，按既有未就绪/缺失契约返回，不得回退到旧 manifest/结构。
4. 旧 attempt 的 `previous_outputs`/历史结构保留先完成，再切换当前缓存身份；旧缓存按既有保留策略留存或清理。CAS 失败时，不应破坏仍有效的当前 attempt 读取。不能把新 identity 标签贴到旧文件上完成“迁移”。
5. 对磁盘上没有可验证 attempt 归属的历史缓存保守处理；重新实例化后仍需验证身份。保持 manager cache 单例、任务目录布局和 DB schema，不新增 cccp 实现或缓存框架。

**补入任务 11 的验收矩阵。**

- attempt 1 的 manifest、结构和频率文件均已缓存；attempt 2 在同路径写入不同内容。经真实 v2 路由读取必须得到 attempt 2，重复请求仍命中当前缓存。
- attempt 2 尚无新 manifest/结构时，返回声明的未就绪/缺失结果，不能展示 attempt 1 的产物。
- 用事件屏障控制旧下载晚于缓存切换完成，断言旧字节不能成为新 attempt 的结果；覆盖后台预取与前端按需下载的交错。
- 保留旧磁盘缓存并重建 cache 实例后，仍拒绝旧身份缓存；重跑前的历史结构保留不受影响。

这些测试仍全部使用 fake fetcher，无需真实 SSH、LSF 或 QC。IS-5 的反例栏增加“热缓存跨 attempt、旧下载晚到、缓存重建后身份验证”即可；任务 2/8 只需协调现有重跑/保留顺序。

**可随 R0 一并清理的文案与顺序，不再作为新的架构问题。**

- 主计划第 138 行将旧版本 `34b057a7…6aff` 写为实际执行摘要，但本次已是 `c7dc8e15…a042`。应写“R0 执行时计算并记录当前主计划 SHA-256”，不要在主计划里硬编码其自身摘要；旧摘要仅作历史。
- 第 111 行已指定 v2 源码 `6 → 9` 编辑顺序，矩阵任务 9 的前置仍仅为 7。将 6 补入任务 9 的编辑前置，反推更新任务 6 的 `Blocks`，避免按矩阵调度时漏掉文件串行约束。当前矩阵本身已经无环。
- 契约由五项扩为八项、任务 4 验收由七项扩为九项；将旧计数改成“上述契约/全部列举场景”，减少后续修订再次失配。

**执行建议。**

R0/R1 可以沿当前结构推进；进入 R6 前必须冻结上述缓存身份与旧请求隔离规则，并将热缓存反例纳入任务 11。其余已关闭问题不再反复扩大范围。是否实现成功，以各单元行为验收为准。

本轮只新增本地评审和隔离探针，没有修改主计划、业务代码或用户已有改动，没有启动 API/scheduler、连接真实远端或执行真实 QC。未运行全套业务 pytest，不能据此声称系统问题已修复。

复现脚本：[probe.py](/ACPServer/AutoCalcPlatform/.omo/evidence/acp-runtime-contract-remediation/followup-review/probe.py)；观察结果：[probe-output.json](/ACPServer/AutoCalcPlatform/.omo/evidence/acp-runtime-contract-remediation/followup-review/probe-output.json)。运行：`PYTHONDONTWRITEBYTECODE=1 /opt/acp/venv/bin/python .omo/evidence/acp-runtime-contract-remediation/followup-review/probe.py`。全部文件保持 `.omo/` 本地未跟踪状态。
