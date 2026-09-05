# S2 双键同步扫描修复报告

**报告日期**：2026-09-05
**前置文档**：`ACP_S2_Double_Bond_Scan_Incident_Report.md`（2026-09-05，问题报告）
**重算任务**：`20260905_012942_001_INT_P_PESsearch_Concernted`（completed, exit 0）
**问题级别**：P0 → 已修复并重算验证 ✅

---

## 1. 执行摘要

事故的直接根因不是约束语法（F1，已于 9-4 修复但不足），而是一个更深层的
**原子索引 base 错误**：`orca_constraint_block` 将 0-based 原子序 +1 转成
"1-based"写入 ORCA 约束，而 **ORCA `%geom` 约束索引是 0-based**
（官方手册示例 `{ B 0 1 1.25 C }`）。结果：双键同步扫描的 21 帧约束
**被完美执行在了错误的原子对上**——本应拉伸 C6–C5 与 C4–C8，实际拉断的是
C6–C7 与 C5–C9，而应用层没有任何门禁去核对 `target vs actual` 残差，
曲线按漂移的 actual 直接连线，最终呈现回折、负值的异常能量图。

本轮修复（索引修正 + 约束残差硬门禁 + 横轴语义 + 双坐标推荐）后重算：
**21/21 帧两键残差 ≤ 7.5×10⁻⁷ Å**，能量曲线为单调解离型
（0 → +112.5 kcal/mol 后进入平台），全部质量门禁通过。

---

## 2. 最终根因：ORCA 约束原子索引 off-by-one

### 2.1 机制

| 层 | 事实 | 证据 |
|---|---|---|
| 写入 | `constraints.py::orca_constraint_block` 写 `{ B i+1 j+1 value C }` | 事故任务 frame inp：`{ B 7 6 2.5582 C }`（源 atoms=(6,5) 0-based） |
| 引擎 | ORCA `%geom` 约束索引 **0-based** | 手册 §4.1.3.3 示例 `{ B 0 1 1.25 C }`；实测 `B 7 6` 约束精确作用于 0-based 对 {6,7} |
| 后果 | 约束落在邻位原子对 {6,7}/{5,9} 并被**精确执行** | 事故 21 帧几何实测 d(6,7)/d(5,9) 逐帧 == 目标日程至小数点后 4 位 |
| 放行 | 无 target-actual 残差门禁（事故报告 F2） | 事故 profile 21 帧 `optimization_converged=True`，而真实键残差最大 −1.65 Å |
| 显示 | 横轴取第一条 actual + 帧序连线（F3） | actual 漂移 → 回折曲线 |

### 2.2 关键判别实验（ORCA 6.1.1，真实事故几何）

| 探针 | 输入 | 结果 |
|---|---|---|
| v1 `{ B 7 6 2.5582 C }` (xTB) | 误指 {6,7} | d(6,7)=2.5582 精确命中；d(6,5)=1.51 自由弛豫 |
| v5 同上 (B97-3c DFT) | 误指 {6,7} | d(6,7)=2.5582 精确命中 — **DFT 同样如此** |
| 修复后 frame-1 机制验证 | 正确 {6,5}/{4,8} | d(6,5)=1.6582 / d(4,8)=1.5958，残差 ±0.0000 |
| Simul_Scan 原生探针 | `B 7 6 = ...` (误指) | relaxscanact.dat actual==target；.004.xyz d(6,7)=1.9582 |

**方法论修正记录**：9-4 晚间我曾依据 v1/v5 判定"ORCA value-C 约束不执行目标值"，
该结论是**误诊**——探针测量用了原始 0-based 索引，而约束实际作用于 +1 后的
原子对，量错了对象。教训已固化进集成测试：断言必须直接测**约束声明的原子对**
的距离（见 `tests/test_pes_orca_simulscan_integration.py`）。另：ORCA 手册
"建议 value 不要离初始结构太远"的告警在实测中并不构成限制（Δ≈1.07 Å 亦精确执行）。

### 2.3 为什么历史单键扫描一直是对的

`_orca_scan_line`（原生 Scan 行）从来就写 0-based 原始值 —— 9-3 的单键任务
actual==target 达 10 位小数。同一文件内两条写入路径 base 约定不一致，
只有走 `constrained_optimize`（IRC/TS/多坐标同步扫描）的路径踩雷。

---

## 3. 修复明细

| 项 | 文件 | 内容 |
|---|---|---|
| P0-α 索引修复 | `cccp/qc/interfaces/constraints.py` | `orca_constraint_block` 去除 +1，写 0-based；docstring 记录 ORCA=0-based / xTB=1-based 的约定分野 |
| P0-β 残差门禁 | `acp/calculations/pes/contracts.py` | `ScanFrame` 新增 `constraint_residuals` / `constraint_residual_ok` / `max_constraint_residual` / `invalid_reasons`；`ScanQuality` 新增 `constraints_satisfied` / `constraint_tolerance` / `max_constraint_residual` |
| P0-β 残差门禁 | `acp/calculations/pes/scan.py` | `_extract_frames` 逐帧计算 \|actual−target\|（默认容差 distance 0.01 Å / angle 0.5° / dihedral 1.0°，`pes_scan.constraint_residual_tolerance_angstrom` 可覆盖）；`run_pes_scan` 汇总门禁，任一帧超限 → `quality.status="invalid"`、TS/INT 候选**全部抑制**、ERROR 日志 |
| P1-α 横轴语义 | `acp/results/energy_graph.py` | x 轴改为 target（λ 日程，构造上单调）优先、actual 兜底；actual 保留在 metadata 作 corrector 质量信号；metadata 暴露 `constraints_satisfied` / `max_constraint_residual` / `constraint_tolerance` |
| P1-β 双坐标推荐 | `acp/calculations/pes/scan.py` | `_select_distance_seeds` 的 `forming_bonds` 收齐**全部** distance 坐标的原子对（双键同步扫描不再退化为第一条键） |
| P2 测试 | `tests/test_pes_constraint_gate.py`（新） | 容差解析、离轨帧标记、门禁抑制、ScanQuality 序列化、横轴 target 优先 |
| P2 测试 | `tests/test_pes_orca_simulscan_integration.py`（新，`--run-slow`+ORCA 门控） | 真实 ORCA 双键同步扫描逐帧断言两键残差 ≤0.01 Å（**直接测约束声明的原子对**） |
| P2 测试 | `tests/test_cccp_orca_ts_extensions.py` / `test_pes_search.py` | 约束渲染断言改 0-based；mock 几何改为跟踪目标日程（否则过不了新门禁） |

测试结果：`test_pes_constraint_gate` + `test_pes_search` + `test_cccp_orca_ts_extensions`
+ `test_acp_energy_graph` + `test_pes_atom_selection` + `test_cli_pessearch_finalize`
+ `test_scan_workflow` + `test_acp_backends` = **125 passed**；`test_acp_api_v1` **78 passed**；
真实 ORCA 集成测试 **passed**（5.8 s）；ruff check / format 全过。

---

## 4. 重算验证（三张审计表）

任务 `20260905_012942_001`，输入与事故任务完全相同
（INT_P 全局极小构型，q1=C6–C5: 1.5582→3.5582 Å，q2=C4–C8: 1.4958→3.4958 Å，
21 点，驱动 GFN2-xTB/ORCA，单点 B97-3c/mTZVP）。运行 12 分钟完成。

### 4.1 约束表（摘要）

- 21/21 帧 `optimization_converged=True`，`constraint_residual_ok=True`
- 两键残差全部 ≤ **7.49×10⁻⁷ Å**（容差 0.01 Å）
- `quality`: `constraints_satisfied=true`, `status=ready_for_review`,
  `max_constraint_residual=7.5e-07`

### 4.2 能量表（摘要）

- `energy_source=single_point`，21/21 帧单点完成，`reference_index=0`
- 相对能量：0 → 单调爬升 → +109.7（帧 15）→ **+112.5 kcal/mol 平台**（帧 16–20）
- 曲线形态为化学上合理的**协同解离型势能曲线**（笼架双键协同断裂 ≈110 kcal/mol 量级，随后解离平台），无回折、无负值异常（事故版曾现 −146.9 kcal/mol）

### 4.3 结构/单调性表（摘要）

- target q1、actual q1、actual q2 全部严格单调 ✓
- energy_graph 投影：x = 1.558→3.558 单调，`metadata.constraints_satisfied=true`

### 4.4 候选（待人工审核，audit-only）

- `ts_guess_016`（帧 15，+109.7 kcal/mol，medium）—— knee-shift 策略
- `int_guess_017`（帧 16，解离平台侧，medium）
- 按 PES 人工确认流程（`POST /jobs/{id}/pes/review`）确认后方可进入 BatchOptimize

### 4.5 与事故版对比

| 指标 | 事故版（9-4） | 重算版（9-5） |
|---|---|---|
| 约束实际作用原子对 | {6,7}/{5,9}（错位） | {6,5}/{4,8}（正确） |
| 最大残差 | −1.65 Å | **7.5×10⁻⁷ Å** |
| 曲线形态 | 回折、跨段长连线 | 单调解离平台型 |
| 相对能量范围 | −146.9 ~ +15.7 kcal/mol | 0 ~ +112.5 kcal/mol |
| 候选可信度 | 无效（TS-001/INT-015 作废） | ready_for_review |

---

## 5. 残留事项与后续建议

1. **数据作废声明（维持事故报告 §9）**：`20260904_115655_001_PESsearch`
   及其所有 TS/INT 候选、能量数值全部作废；重算版为唯一有效数据。
2. **历史受影响面**：所有走 `constrained_optimize` 带目标值的任务（多坐标
   同步扫描、部分 IRC/TS 约束优化）在 9-4 00:18（commit 0ae1897 引入同步
   扫描）至本修复之间产生的结果需按 §4 同样的残差审计复核。
3. **ORCA 原生 Simul_Scan（后续优化，非本轮）**：ORCA 6.1 支持最多 3 坐标
   `Simul_Scan true` 同步扫描，可将逐帧 Python 循环折叠为单个 ORCA 作业
   （`relaxscanact.dat` 自带 actual 台账）；本轮为最小风险修复未启用，
   已验证可行（v7 探针），建议作为后续性能优化项。
4. **xtb 可执行文件预检警告**：与本次问题无关，但 PESsearch 预检要求 xtb
   仍会在无 xtb 环境告警；若未来切换 xtb 驱动需先配置
   `executables.xtb.path` 或 `CONFSEARCH_XTB_PATH`。
5. **前端标签（可选）**："相对能量"建议改为"相对能量（首帧参考）"；工作区
   有用户未提交的前端改动，未在本轮触碰，留待用户合并时处理。
6. **约束残差容差校准**：当前默认 0.01 Å；不同方法/优化级别下的校准值
   可经 `pes_scan.constraint_residual_tolerance_angstrom` 调整。

---

## 6. 验证门（全部通过）

```
单元/回归测试        125 passed（pes/ts/backends/graph/cli/scan_workflow）
API 套件             78 passed
真实 ORCA 集成测试   passed（双键逐帧残差断言）
frame-1 机制验证     残差 ±0.0000 Å
重算任务             completed, exit 0, 12 min
三张审计表           全绿（残差 ≤7.5e-07 Å、能量单调合理、结构单调）
energy_graph 投影    x 单调 + 质量元数据暴露
ruff check/format    通过
```
