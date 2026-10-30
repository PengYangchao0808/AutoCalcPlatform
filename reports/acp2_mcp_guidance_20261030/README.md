# MCP 指南首批切片 — DA 真实链路验证回执（2026-10-30）

范围：计划《ACP_MCP_Agent_Guidance_PES_Batch_Plan_20261030》P0+P1a/P1b 首批切片的科研门验证——
环戊二烯 + 乙烯 Diels–Alder，经真实 MCP 面（共享桥 + stdio 双通道）完成
PESsearch 键长扫描 → 选点确认 → BatchOptimize OptTS + 频率验证。

## 文件

| 文件 | 内容 |
|---|---|
| `DA_VALIDATION_REPORT.md` | 验证报告（按 codex 验收 notes 修订后）。TS 驻点 supported（−424.44 cm⁻¹ 单虚频）；路径连接 inconclusive（缺 IRC） |
| `codex_result_acceptance.md` | 计算结果独立验收（gpt-6-astra，**PASS-with-notes**，109k tokens：逐帧能量重算、频率结构、虚模键轴余弦 −0.9978、磁盘 SHA 核对） |
| `codex_code_review.md` | 分支代码评审结论（gpt-6-astra，1×P1 + 4×P2，全部在本仓 `a588132d` 修复并带行为测试） |

## 登记的缺陷（后续批次）

1. **[高优先级·易用性] BatchOptimize inline item 未标 role/tag 时静默按 INT 语义 + 平台默认方法执行**，
   TS 意图落空无任何 typed 提示（负例 `20261030_100432_005_BatchOptimize`，已取消）。
   inline 显式 `role:"ts"` 或 review→from_artifact 链路均可正确触发 OptTS。归 P1a/P3 整改。
2. PESsearch `coordinate.unit` 非法枚举值（`angstrom2`）未在提交校验层拦截，执行层裸 exit 1（负例 job 001）。归 P1a。
3. `pes_profile.frames[].geometry_path` 与实际落盘路径不一致；共享桥 capabilities 未暴露 workflow 参数；prepare 异步需重试——易用性观察，归 P1a/P2。

验证轮 TaskRef：PES `20261030_095632_004_PESsearch`（completed，15/15 帧）；
Batch `20261030_101415_006_BatchOptimize`（completed，OptTS 收敛 + 频率）。
失败负例 TaskRef 见报告 §7。

> 三态说明：TS 驻点判定基于 B97-3c 单一方法级别；端点连接（IRC）、INT 侧、ZPE/热校正
> 未验证（inconclusive/无数据），不以本回执关闭任何 G/H 发布门。
