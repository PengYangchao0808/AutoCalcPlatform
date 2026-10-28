# C06-s2 (T32) 有界产品实现与生成/读取性能基线 — `c06-bounded-baseline.md`

**计划**: `.omo/plans/acp-parallel-plan-3-cccp.md` todo 32（C06-s2，Wave 3，blocks T33）
**参考**: 主方案 C06；评审 C5（生成 + 读取两阶段、随规模曲线）；T31 `c06-sample-baseline.md` §5
**范围**: `src/cccp/analysis/bounded/*`（新契约，registry §4.6）+ 定向测试 + 本测量文档。零 `acp` 依赖；复用 C02 产品骨架（每块为真实 `ScienceProduct`）。
**日期**: 2026-10-19
**机器/解释器**: 同 T31 §1（Xeon E5-2673 v4；`/usr/bin/python3.11` 3.11.13；numpy 2.4.6）

---

## 1. 实现概要（真实生产入口，同 T13 实装闭包表）

| 操作 | 入口（真实生成函数） | 结果 |
|---|---|---|
| 无损分块（曲线） | `cccp.analysis.bounded.chunk_curve_product` → 每块经 `cccp.calculation.products.curve.build_curve_scatter_product` | 块内有界；`reconstruct_curve_series` 逐点精确重构全序列 |
| 无损分块（轨迹） | `cccp.analysis.bounded.chunk_trajectory_product` → 每块经 `...products.structure.build_structure_trajectory_product` | 帧序/`frame_index`/`frame_id` 保持 |
| 抽样（显示） | `cccp.analysis.bounded.subsample_curve_product` | 选中点↔原始点映射（`point_id`+native index）；关键点保留或显式标注；显式标注"显示抽样 ≠ 全量科学结果" |
| 多分辨率 | `cccp.analysis.bounded.build_multiresolution_product` | 每层为独立真实产品（distinct series_id），逐层映射回原始索引 |
| 读取 | `cccp.calculation.products.serialize.product_from_dict`（唯一 C02 reader） | 块按原始索引重建 |

- 派生身份 = `ProductIdentity(input_digest=基产品摘要, algorithm/version=有界算法, params_digest=范围+分辨率+参数+bounded schema)`；**不含 job/account**。
- 无第二算法链：仅按原始索引切片/步进；不重算聚类/降维（ADR/ACV 不参与）。

---

## 2. 样本复用（与 T31 逐字节一致）

| 样本 | 路径 | sha256（本任务实测） | T31 记录 |
|---|---|---|---|
| S4 能量（200,000 点） | `.omo/evidence/acp-parallel-plan-3-cccp/c06-samples/energy_synth_200000p.json` | `a5cf2883f08a925e5f857a7d44e4970cf16089cd126bd57b0db192726d6d0479` | 同（`c06-raw/sample-sha256.txt`） |
| S3 轨迹（10,000 帧×30 原子） | `.omo/evidence/acp-parallel-plan-3-cccp/c06-samples/traj_synth_10000f_30a.xyz` | `747026d5074fd4d0babede0a2b04ab72a77d820a56e6a899ba5cc0afe9b4564f` | 同 |

- 小规模轨迹点（2,000/5,000 帧）由 T32 harness 用 **T31 `_write_traj` 同款确定性算术**（无 RNG）重生成后临时读取并清理（无残留）。
- 能量小规模点 = S4 前 N 点（同一固定数值输入）。

---

## 3. 测量方法（生成/读取分列，每 case 独立进程）

- harness（.omo，随原始日志）：`.omo/evidence/acp-parallel-plan-3-cccp/c06b-measure.py`。
- 每 case 独立进程，`/usr/bin/time -v` 归因干净：`generate` 阶段 = 真实入口建基产品 + 有界化 + 写块 JSON；`read` 阶段 = 只经 C02 reader 读回块（不含生成）。
- 精确命令（repo 根，`PYTHONPATH=src`；`<phase> ∈ {generate,read}`，`<kind> ∈ {energy,traj}`）：

```bash
/usr/bin/time -v /usr/bin/python3.11 .omo/evidence/acp-parallel-plan-3-cccp/c06b-measure.py <phase> <kind> <scale>
# energy scales: 10000 50000 200000（chunk_size=4096）
# traj   scales:  2000  5000  10000（chunk_size=500）
```
- 每 case run1/run2；raw `/usr/bin/time -v` 全文：`c06b-raw/<phase>_<kind>_<scale>.run{1,2}.{out,err}`。

---

## 4. 生成阶段（真实入口 → 有界产品）

### 4.1 能量曲线（chunk_size=4096）

| 规模（点） | 块数 | 最大块 | wall run1/run2 (s) | 峰值 RSS run1/run2 (kB) | 说明 |
|---|---|---|---|---|---|
| 10,000 | 3 | 4096 | 0.26 / 0.25 | 40,208 / 40,568 | 基产品建 + 分块 |
| 50,000 | 13 | 4096 | 0.64 / 0.64 | 48,860 / 48,860 | |
| 200,000 | 49 | 4096 | 2.12 / 2.09 | 114,876 / 114,556 | 随规模近线性 |

### 4.2 轨迹（chunk_size=500）

| 规模（帧） | 块数 | 最大块 | wall run1/run2 (s) | 峰值 RSS run1/run2 (kB) | 说明 |
|---|---|---|---|---|---|
| 2,000 | 4 | 500 | 0.82 / 0.83 | 59,348 / 59,336 | 含 XYZ 解析 |
| 5,000 | 10 | 500 | 1.83 / 1.86 | 83,956 / 84,228 | |
| 10,000 | 20 | 500 | 3.10 / 3.38 | 132,800 / 132,480 | S3 实文件（`read_xyz_multiframe` 解析占大头） |

- **块有界断言**：所有规模 `max_block_size <= chunk_size`（raw `RESULT ... max_block_size=...`）。生成阶段峰值 RSS 随规模增长（基产品在内存中完整持有一次），这是"一次全量科学分析"的既有成本（T31 §5.3 生成阶段输入侧）。

---

## 5. 读取阶段（只读有界块，经唯一 C02 reader）

### 5.1 能量（块 JSON 读回）

| 规模（点） | 块数 | wall run1/run2 (s) | 峰值 RSS run1/run2 (kB) | 块目录字节 |
|---|---|---|---|---|
| 10,000 | 3 | 0.19 / 0.23 | 26,260 / 26,260 | — |
| 50,000 | 13 | 0.51 / 0.50 | 27,092 / 27,092 | — |
| 200,000 | 49 | 1.70 / 1.68 | 27,964 / 27,960 | 34 MiB |

### 5.2 轨迹

| 规模（帧） | 块数 | wall run1/run2 (s) | 峰值 RSS run1/run2 (kB) | 块目录字节 |
|---|---|---|---|---|
| 2,000 | 4 | 0.60 / 0.58 | 36,964 / 37,116 | — |
| 5,000 | 10 | 1.27 / 1.50 | 37,128 / 37,124 | — |
| 10,000 | 20 | 2.74 / 2.75 | 37,752 / 36,976 | 26 MiB |

- **读取峰值 RSS 近恒定**（能量 ≈28 MB、轨迹 ≈37 MB），不随规模增长——这正是有界读取的目标（C5）。墙钟随规模线性（逐块 JSON 解析）。

---

## 6. 与 T31 全量读取基线对照（分列，不写未测收益比）

| 数据 | T31 全量读取（`c06-measure.py`） | T32 有界读取（本表） | 差异（实测） |
|---|---|---|---|
| 能量 200,000 点 | 0.16 s / 42,836 kB（`json.load` 单 docs） | 1.70 s / 27,964 kB（49 块） | wall ×10.6；峰值 RSS −35% |
| 轨迹 10,000 帧 | 0.75 s / 125,120 kB（全序列位移处理；纯解析 0.53 s） | 2.74 s / 37,752 kB（20 块） | wall ×3.7–5.2；峰值 RSS −70% |

- 结论（仅记录，不推论未测收益）：有界读取以**更低的峰值内存**换取逐块解析的墙钟开销；块 JSON 自带逐点/逐帧契约包装（能量 200k 块目录 34 MiB vs 源 4.05 MiB），这是"同一科学契约可被 B/A 消费"的显式成本。

---

## 7. 正确性（定向测试，`python3.11 -m pytest tests/test_cccp_bounded*.py -q`）

| 断言 | 方法 | 结果 |
|---|---|---|
| 块有界 | chunk 后 `max_block_size() <= chunk_size`（矩阵 1/7/1000/128） | PASS |
| 无损重构 | `reconstruct_curve_series` 逐点 `(point_id,x,y,missing_reason)` 精确相等 | PASS |
| 缺失点不补零 | partial 系列：缺 `point_id` 保留 `missing_reason`，`complete=False` | PASS |
| 抽样仅证映射 | 每个选中点 `point_id/x/y == 原始[native_index]`；索引严格递增 | PASS |
| 关键点保留 | min/max/端点/局部极小均保留（`retained=True`） | PASS |
| 关键点显式标注 | `retain_key_points=False`：未落 stride 的 min/max 记 `retained=False` + reason | PASS |
| 多分辨率 | 每层 distinct `series_id`；逐层映射回原索引；关键点按层记录 | PASS |
| 身份稳定/可区分 | 同输入同参 → 相等；异 chunk_size/range/method/resolution 集 → 相异 | PASS |
| 轨迹无损 | `reconstruct_structure_frames` 与原始帧**精确相等**（含 `frame_id`） | PASS |
| 序列化 | 清单/块经 JSON 往返相等；块经唯一 C02 reader 往返相等 | PASS |
| 真实入口 | 基/块产品 `identity.generator` = 注册的真实生成函数；`input_digest` 继承 | PASS |

- 结果：**29 passed**（`tests/test_cccp_bounded_products.py`）。
- slice guard：`python3.11 scripts/verify_cccp_slice_guard.py` → `RESULT: PASS`（5/5；含新 `cccp/analysis/bounded/**` 的静态反依赖扫描与无 `acp` 导入）。

---

## 8. 未测 / 边界（显式）

| 项 | 原因 |
|---|---|
| 真实 >10^6 帧轨迹 / >10^6 点 / 真实仪器 FID 分块 | 按 Must-NOT 不以超大真实数据集作性能证据 |
| 抽样/多分辨率逐规模内存曲线 | 抽样输出远小于输入、单块；上表仅记录生成/读取的有界分块曲线 |
| 磁盘 I/O 单独分解（page-cache 命中率） | 未纳入本口径（major faults 在各 raw log 中） |
| A/B 消费端实测 | 跨路（T33 handoff 交 B/A），本任务只交产品与基线 |
