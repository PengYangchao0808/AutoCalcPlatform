# FE-10 几何辅助核验：两套二面角函数的语义差异

日期：2026-10-16。对《ACP 前端冗余与维护性审计》FE-10 的单独核验记录。**本轮未修改任何运行代码**；结论是两套实现**不能合并**，任何统一须单列变更并附黄金样例。

## 1. 核验对象

| 实现 | 位置 | 输入/输出约定 |
|---|---|---|
| `s2scanDihedral(a,b,c,d)` | `frontend/js/workbench/features/scan-s2.js:478` | 原子对象 `{x,y,z}`；`atan2` 原始输出（−180, 180]，无显式归一化；退化坐标返回 `NaN` |
| `computeMeasurementValue("dihedral", …)` | `frontend/js/geometry_store.js:326`（dihedral 分支 :350-359） | atom-id 索引坐标数组；`normalizeDihedral` 归一化到 (−180, 180]（−180 → +180）；退化/缺坐标返回 `null` |

消费方：`s2scanDihedral` 经 `wizard.js:1905` 用于 scan/PESsearch 向导选点预填；`computeMeasurementValue` 用于结构查看器测量显示与编辑。

## 2. 实测方法与结果

两个函数体逐字提取至 Node 隔离求值（脚本：`.omo/evidence/frontend-cleanup-20261016/fe10_dihedral_check.js`，坐标见下表）。测试坐标均为 B–C 轴 = +y、A/D 取不同方位的构造样例：

| 样例 | 坐标 (a,b,c,d) | S2 | GeometryStore | 差值 |
|---|---|---:|---:|---:|
| 审计样例 | (−1,0,0)(0,0,0)(0,1,0)(0,1,1) | −90° | +90° | −180° |
| 翻转 A | (+1,0,0)(0,0,0)(0,1,0)(0,1,1) | +90° | −90° | −180° |
| 平面 anti（A、D 异侧） | (−1,0,0)(0,0,0)(0,1,0)(+1,1,0) | 0° | 180° | −180° |
| 平面 syn（A、D 同侧） | (−1,0,0)(0,0,0)(0,1,0)(−1,1,0) | 180° | 0° | −180° |
| 一般方位 | (−1,0,0)(0,0,0)(0,1,0)(0.7071,1,0.7071) | −45° | 135° | −180° |

## 3. 数学结论

- S2 取 **A→B** 向量在 B–C 轴法平面上的投影 v_S2；GeometryStore 取 **B→A** 的投影 v_GS = −v_S2（对应经典 praxeolitic/IUPAC 公式）。x=v·w 与 y=(u×v)·w 同时变号，`atan2(−y,−x)` 恒等于 `atan2(y,x)±180°`。
- 因此两者是**恒定 180° 平移**关系（S2 = GS − 180 (mod 360)），**不是镜像/符号翻转**：仅在 ±90° 处看起来像"只差正负号"，anti/syn 两个平面样例证明语义整体错位——S2 的 0° 对应 IUPAC 反式（anti-periplanar），S2 的 180° 对应顺式（syn-periplanar）。
- 范围与退化处理也不同（见 §1 表）。

## 4. 用户可见影响（现状）

同一组 4 个原子：结构查看器测量标签显示 GS 值，scan/PESsearch 向导选点预填 S2 值，两者可相差 180°。这是现存的不一致，**不属于本轮"代码精简"可顺手修复**——修正哪一边取决于扫描负载（`--coordinate` 起止角）在后端的科学约定。

## 5. 后续统一前置条件（与审计建议一致）

1. 先核定科学权威：后端 scan 坐标（含 ORCA/xtb 二面角方向约定）采用哪种定义；建议以 IUPAC 扭角（GS 现用）为基准评估。
2. 建立黄金样例集（至少本表 5 例 + 多帧/坏行/共线退化用例），先固化为 Node 测试。
3. 契约应同时定义：原子 id、首帧/多帧、无效坐标返回值（null vs NaN）、范围归一化、二面角方向与单位。
4. 之后再让 `s2scanDihedral` 适配契约；若数值语义变化须单列变更说明，不得宣称"等价重构"。

## 6. 后端约定勘察结论（2026-10-17）

基于仓库证据链的后端 scan 坐标约定勘察（**仅固化证据，本轮未修改任何运行时行为**）。证据逐条核实于 2026-10-17：

| # | 环节 | 证据驻点 | 结论 |
|---|---|---|---|
| 1 | CLI 入参 | `src/acp/cli.py:447-458`、`legacy_adapters.py:302-328` | `--coordinate` 给 4 个原子即 dihedral；start/end 为**用户度数**，原样解析 |
| 2 | 规划层 | `src/cccp/calculation/tasks/scan.py:245-309`、`src/cccp/qc/interfaces/constraints.py:101-140` | 纯透传，无任何符号/基准变换；`CoordinateSpec` 契约文档明示 "angles/dihedrals in degrees" |
| 3 | 后端写入 | `src/cccp/qc/interfaces/orca.py:932-944`、`:2339`；`src/cccp/qc/interfaces/xtb_scan.py:31-69` | ORCA 逐字写 `D i j k l = start, end, n`；xtb 逐字写 `dihedral: i,j,k,l,value` —— 用户度数直达引擎关键字，途中无符号调整 |
| 4 | 平台内唯一实测实现 | `src/cccp/utils/geometry_tools.py:73-102`（`GeometryUtils.calculate_dihedral`） | praxeolitic/IUPAC 公式，**≡ GeometryStore 约定**；被 PES `actual_coordinate` 采用（`src/acp/calculations/pes/scan.py:1088-1096`、`scan_snapshot.py:63-84`） |
| 5 | 前端图轴 | 前端 scan 能量图 x 轴 | 优先消费 `actual_coordinate`（即第 4 条的 IUPAC 值） |

结论：

1. **引擎符号约定未在代码中声明** —— ORCA/xtb 对 `D i j k l` / `dihedral` 的方向（右手性）定义属引擎手册层知识，仓库内无断言，最终确证需对引擎跑实验黄金样例；但**仓库内全部证据一致指向 IUPAC/GS 为平台自洽基准**（唯一实测实现与图轴消费均为 IUPAC）。
2. 现状分歧定位收窄：S2 预填（`s2scanDihedral`）与测量标签/图轴（GS）对同一组 4 原子**恒差 180°**（§2 表 5 例 + `s2 + 180 ≡ gs (mod 360)` 恒等式）。用户若以测量标签读数为参照填向导 start/end，会扫到关于目标构象镜像的路径。
3. **统一建议**：若对齐 IUPAC/GS，只需改 `frontend/js/workbench/features/scan-s2.js:126-140` 一处（消费点 `wizard.js:1903-1905` 原样透传，无需改动）；必须按 §5 前置条件**单列变更**（先引擎实验黄金样例确证 → 改实现 → 同步黄金样例期望值），不得作为等价重构顺手修改。
4. 黄金样例已固化：`frontend/js/module_tests/fe10_dihedral_golden.test.js`（BEHAVIOR-FROZEN）—— §2 表 5 例双侧 pin（容差 1e-6）+ 恒等式断言 + 退化语义（中轴退化 GS 返 null / S2 返 NaN；**共线中轴时 S2 静默返 0 而非 NaN**，`atan2(0,0)=0`，已一并 pin）+ angle/distance 双侧一致 pin。任一侧数值变化都会使该测试变红并触发 FE-10 契约评审；**不得为通过测试而单侧改数**。
