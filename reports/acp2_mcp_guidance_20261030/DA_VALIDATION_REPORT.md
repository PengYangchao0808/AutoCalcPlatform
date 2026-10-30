# DA 反应 TS 搜索 — MCP 真实链路验证报告

日期：2026-10-30（Asia/Shanghai）。执行者：验证子代理（session 内，parent=session-dbce86c9-29de-4e83-845c-5db7fb555157）。
任务书：`/tmp/da_validation/TASK.md`。反应体系：环戊二烯 + 乙烯 Diels–Alder。

## 结论速览

- **TS 驻点判定：supported**。BatchOptimize（`20261030_101415_006_BatchOptimize`，attempt 1）OptTS 收敛，**恰一个显著虚频 −424.44 cm⁻¹**（量级与协同 DA TS 的常见计算值一致；该区间仅作直觉参考，非通用验收阈值——codex 验收修正），虚模位移集中于 idx1/idx4/idx11/idx12 且两两相对反向，对应两条 C–C 键协同形成；平台自身 `ts_validation: "passed"`（注意：该检查只核对负频数量恰为一，不验证模式或端点连接，不构成独立证明——codex 验收指出）。
- **端点验证：缺项（inconclusive）**。未执行 IRC，TS 与反应物/产物两端的连接性未验证；INT 侧未优化（本任务仅选 TS）。
- 势垒（B97-3c 电子能量，无 ZPE/热校正）：TS − 产物端 ≈ **+36.96 kcal/mol**；TS − 远离端（3.3 Å 分离帧）≈ +13.39 kcal/mol（约束帧参考，非自由优化端点，数值偏低估）。
- 全链真实 MCP 面：共享桥 + stdio 双通道均使用；新工具 `acp_get_guidance` 验证通过。
- 关键流程缺陷（用户在会话中指正并复现）：**inline items 提交 BatchOptimize 不会按 OptTS 运行**，必须先经 `POST /jobs/{id}/pes/review` 确认锁定选点，再以 `from_artifact` 进入 Batch。

## 1. 会话与通道事实

| 项 | 值 |
|---|---|
| 共享桥工具（capabilities 报告） | `acp_analyze, acp_cancel, acp_capabilities, acp_preflight, acp_prepare_artifact, acp_read_artifact, acp_status, acp_submit`（8） |
| stdio 通道工具（tools/list 实测） | 上述 8 + **`acp_get_guidance`**（9） |
| MCP 协议版本 | 2025-06-18（initialize 应答） |
| Controller | http://127.0.0.1:8765，健康（candidate_revision=bb309134c7b2bb21d8a8bfcb5589516b5b0cf798，contract_version=1.7.0） |
| 身份 | principal_local / local-single，admission=local-single-enforced（quota-only） |
| DA 主链通道 | 共享桥 `mcp__acp__*`；`acp_capabilities(workflow=…)` 契约经 stdio 通道读取（共享桥 schema 未暴露 workflow 参数——记录为易用性差异） |
| 只读兜底 | `GET /api/v1/jobs/<id>/files[/path]`（在共享桥 read 被 redacted 检查拦截时使用，见 §6-E） |

### 新工具 acp_get_guidance 验证（stdio 通道）

- `tools/list` 含 `acp_get_guidance`（9 基础）✓
- `acp_get_guidance("workflow.PESsearch")`：format `acp.guidance.v1`，version **1.0.0**，digest **sha256:dace9278d995387acac22741bd53d65949891db9be9fed9cc585e8f370c83c01**，coverage=first_batch，topics=3，sections.purpose/steps 中文正文完整可读（含"推荐与最终发布分开，选点仅为初猜"等关键约束）。
- `acp_get_guidance("shared.execution")`：version **1.0.0**，digest **sha256:aeb7390d2e3db5813644015298b5acb1c528040e070f1f1202d0a67bb9e85bba**，sections.body 12 条通用执行约束完整可读。

## 2. PESsearch 键长扫描

### 2.1 提交前纠错（preflight 阶段）

| # | 事件 | 结果 |
|---|---|---|
| A | preflight（start/end 用 JSON float） | typed error `input_invalid / non_canonical_number`（field=plan，"JSON float at input.coordinate.end; use a decimal string"）→ 改十进制字符串后通过 |
| B | preflight（修正后） | feasible=true，plan_digest `sha256:1b9fd808…` |

### 2.2 提交与失败记录（三次失败均为执行层裸错误，非 typed error）

| TaskRef | 幂等键 | 方案 | 结果 |
|---|---|---|---|
| `20261030_094753_001_PESsearch` (attempt 1) | da-validate-pes-e3f15bdc62d5 | 反应物侧压缩；**本方笔误**：coordinates 仅 1 条且 `unit:"angstrom2"`（非法枚举） | 提交**被接受**（无枚举校验），~2 s 后 `workflow exited with code 1`；cancel 到达时已终态。易用性证据：非法 unit 未在提交校验层拦截 |
| `20261030_094840_002_PESsearch` (attempt 1) | da-validate-pes-48dac9373d38 | 反应物侧压缩 3.3→1.6 双坐标（正确 body） | ~14 s 失败：`the first double-scan group must be an adjacent pair (pair 1, 11 is not a bond)`——两条坐标折叠为 `double_bond_scan`，要求两组均为感知键；3.3 Å 形成键不满足 |
| `20261030_095322_003_PESsearch` (attempt 1) | da-validate-pes-54076646bae6 | 加 `selection.path_plan={fixed_endpoints:true}` + 逐点 values 尝试走显式路径分支 | ~3 s 失败：`fixed boundaries require reference geometries` |
| **`20261030_095632_004_PESsearch` (attempt 1)** | da-validate-pes-aef5f71da025 | **产物侧拉伸 1.6→3.3 Å 双坐标 15 点**（方案经用户纠错后修正） | **completed**（09:56:32→09:58:25，约 1 分 53 秒） |

用户纠错内容（关键）：应输入 DA 产物、拉伸两条**已成键** C–C，而非从反应物侧压缩非键对。产物（降冰片烯，17 原子，idx 映射保持：idx1/idx4 二烯末端、idx11/idx12 乙烯 C）由 RDKit ETKDG+MMFF94 构造：d(1,11)=d(4,12)=1.543 Å、d(2,3)=1.342 Å（产物双键）、d(11,12)=1.554 Å（乙烯键变单键）。

### 2.3 成功任务详情（TaskRef `20261030_095632_004_PESsearch`）

- 协议：`orca_relaxed_scan_xtb_gfn2_sp_b973c_v1`，execution_mode=**pointwise**（多坐标自动切逐点约束优化 + retry_previous）；scan_optimizer GFN2-xTB（max_iterations 250）；逐帧单点 ORCA B97-3c TightSCF（resume）；resources nproc=8 mem=16GB。
- 覆盖：**15/15 帧**约束优化收敛，`constraints_satisfied=true`，max_constraint_residual 1.16e-6 Å；`sp_incomplete=false`；quality `ready_for_review`（`manual_only: save exact frames explicitly`）。
- 产物：`pes_profile`（prepare 后 ready：30265 B，digest `sha256:174d5717b9e7fb0d2e49b33f720f3776403fd4223f166354118a8394cdcd7ab9`，generation 1）；`pes_recommendations`（audit only，ts/int 均空——算法推荐未产出，人工选点）。
- 曲线（B97-3c 单点，参考=frame 0 产物端）：

| frame | d(1,11)≈d(4,12) / Å | ΔE / kcal·mol⁻¹ | 备注 |
|---|---|---|---|
| 0 | 1.600 | 0.000 | 产物端（参考） |
| 3 | 1.964 | 29.074 | |
| 4 | 2.086 | 35.478 | |
| **5** | **2.207** | **+37.789** | **曲线极大** |
| 6 | 2.329 | 36.951 | |
| 10 | 2.814 | 27.308 | |
| 14 | 3.300 | 23.572 | 接近分离 |

原始能量（Ha）：frame0 −272.58897963；frame5 −272.52875869；frame14 −272.55141556。

## 3. 初猜选择

- **帧号 frame 5**；几何来源 `WORK/07_PATH/pes_scan_001/frame_005/orca_relaxed_scan_try1.xyz`（该点 GFN2-xTB 约束优化收敛几何，B97-3c 单点 −272.52875869 Ha）。
- 选择理由（非"模型置信"）：①曲线唯一极大点（+37.79，两侧单调下降 35.48/36.95）；②两条形成键**同步**（actual 2.2071/2.2072 Å，约束残差 <1e-6）；③对称协同几何符合 DA 单一 TS 预期；④曲线远端能量回落（产物端 0 参考、远端 +23.6）。
- 几何摘要：d(1,11)=2.207143 Å，d(4,12)=2.207142 Å（四位小数均为 2.2071；codex 验收修正）。
- 发布：用户在会话中确认选点与角色后，`POST /jobs/20261030_095632_004_PESsearch/pes/review`（frame_index=5, role=TS）→ status=confirmed，**candidate_id `pes_ts_frame_005`**，revision 1，frame_source=final_profile，selection_source=manual（用户确认、agent 代为录入，未伪装来源）。

## 4. BatchOptimize

### 4.1 第一次提交（inline items）——流程缺陷复现（用户指正）

- preflight：typed error `input_invalid / shadowed_field`（`method.transition_state_method would be ignored while method.batch_roles is set`）→ 移除平铺字段后 feasible（plan_digest `sha256:7057b26d…`）。
- `20261030_100432_005_BatchOptimize`（attempt 1，幂等键 da-validate-batch-7a23128ffb33，inline items name="TS_guess_1" + batch_roles ts/B97-3c + profile opt_freq）**被接受**（无来源确认门拦截），但 ORCA 实际执行 `! B3LYP D3 ma-def2-SVP def2/J Opt`——普通极小化优化且 batch_roles 方法未生效（inline item 无 role tag，角色匹配失败且无警告）。该任务在把 TS 初猜向极小点推进，属错误语义计算，已于 ~8 min 后取消（终态 cancelled）。
- **Issue（经 codex 验收修正后的表述）**：本例 inline item 仅带 `name="TS_guess_1"`、未带 `role`/`tag` 字段——inline 路径本身支持 role/tag 指定 TS（`loaders.py:235`），未标角色时默认 INT（`_xyz.py:75`），`batch_roles.ts` 不会把未标 item 自动变成 TS（`options.py:334`）；`_raise_unconfirmed_pes` 确实只位于 manifest/from_artifact 加载路径（`loaders.py:82`），inline 无 PES 来源确认属来源追踪覆盖缺口而非越权漏洞。**真正的高优先级缺陷是：item 的实际角色/方法与操作者 TS 意图不匹配时，preflight 与执行均不给出任何 typed 提示**，本例已造成错误方法（B3LYP-D3 普通 Opt）计算与资源浪费。正确链路（review → from_artifact + selected_ids，job 006 已验证）或 inline 显式 `role:"ts"` 均可触发 OptTS。建议：预检对"携带 batch_roles.ts 配置但 item 未标 ts 角色"给出 typed warning；来源绑定缺口在 P3 同批整改。

### 4.2 第二次提交（review 链路，成功）

- `20261030_101415_006_BatchOptimize`（attempt 1，幂等键 da-validate-batch-6e0ef9444d16）：input `from_artifact=/home/xieningke/ACP_runs/uncategorized/mol_PESsearch__04/RESULT/result_manifest.json` + `selected_ids=["pes_ts_frame_005"]`；method `profile=opt_freq` + `batch_roles={"ts":{"role":"ts","method":"B97-3c"}}`；resources 8C/16GB。preflight feasible（plan_digest `sha256:6a6c3873…`）。
- 实际执行（CLI + ORCA 输入核对）：`! B97-3c OptTS TightOpt TightSCF`，`Calc_Hess true`、`Recalc_Hess 5`、`Trust 0.3`、MaxIter 200；provenance role=ts、method=B97-3c、opt_convergence=tight。
- 时间：10:14:15→10:16:26 completed（约 2 分 11 秒）。

### 4.3 TS item 结果（item_001 = pes_ts_frame_005）

| 项 | 值 |
|---|---|
| 优化 | `optimization_status: converged`（ORCA `THE OPTIMIZATION HAS CONVERGED`） |
| E(B97-3c) | **−272.530082011 Ha** |
| 频率 | 51 模式：6 零频（平/转）→ **唯一虚频 −424.44 cm⁻¹**（mode 6）→ 首个实频 196.07 → … → 3196.07 |
| 平台判定 | `ts_validation: "passed"`，frequency_status completed |
| 产物 | `RESULT/structures/item_001__TAG_TS__optimized.xyz`；`RESULT/frequencies/item_001__normal_modes.json`（76443 B，digest `sha256:13059080e14e6bc251aade6c047691d8d774c7f5933f06e8ba6661f80b14a3f6`，经 prepare→read MCP 全文读取成功） |

优化后 TS 几何：d(1,11)=**2.2243** Å、d(4,12)=**2.2232** Å（两键对称将成键）；d(2,3)=1.3960 Å（二烯中心键部分双键化，产物 1.34/反应物 ~1.47 之间）；d(11,12)=1.3824 Å（乙烯键自产物 1.554 部分断裂、趋向反应物 ~1.33）——协同中间态特征完整。

虚模方向证据（mode 6 位移矢量）：主要分量位于 idx1 (0.304,−0.197,−0.229)、idx4 (0.248,−0.252,−0.240)、idx11 (−0.366,0.159,0.254)、idx12 (−0.217,0.306,0.285)——两对成键原子位移沿键轴相对（idx1↔idx11、idx4↔idx12 近似反向），即两条 C–C 键同步形成/断裂的反应坐标模式；H 原子位移显著更小。

势垒（B97-3c 电子能量差）：ΔE(TS−产物端)=−272.530082−(−272.588980)=+0.058898 Ha=**+36.96 kcal/mol**；ΔE(TS−3.3Å 分离帧)=+0.021334 Ha=+13.39 kcal/mol。注意：这是相对**约束扫描帧**的 B97-3c 电子能量差——端点为 GFN2-xTB 约束几何上的单点（frame 0/14），TS 为 B97-3c 优化几何，不是完整同级优化端点势垒，也不是活化自由能；未含 ZPE/热校正。净偏差方向（有限距离相互作用 vs 几何松弛）由现有证据不能确定，不做与文献的数值一致性声明（codex 验收修正）。

INT 侧：本任务未选 INT 候选（曲线无极小平台），无 INT 频率结果——按证据范围留空，未做任何补算。

## 5. 新工具验证结论

`acp_get_guidance` 在 stdio 通道可见（9/9）且两个 guide 读取成功，返回结构化 version/digest/sections，正文与《统一实施计划》首批切片口径一致（"选点仅为初猜""Batch completed 不等于反应路径验证完成"等）。该工具未在共享桥 schema 暴露（共享桥 8 工具），跨通道不一致记录为易用性观察。

## 6. typed errors 与纠错清单（U02/U03 证据）

| # | code / rule | 场景 | 纠错 |
|---|---|---|---|
| A | `input_invalid / non_canonical_number`（field=plan，input.coordinate.end） | PES preflight 浮点 | start/end 改十进制字符串 |
| B | （非 typed）`unit:"angstrom2"` 通过提交校验，执行层 exit 1 | job 001 | 修正 body；**缺陷：枚举未拦截** |
| C | （非 typed）`first double-scan group must be an adjacent pair` | job 002 | 方案错误：需产物侧拉伸已成键（用户纠错） |
| D | （非 typed）`fixed boundaries require reference geometries` | job 003 | 放弃 path_plan 分支，改产物侧方案 |
| E | `input_invalid / artifact_body_redacted`（field=body） | 共享桥 read pes_profile | 只读 HTTP `GET /jobs/{id}/files/...` 兜底成功（不算绕过） |
| F | `input_invalid / shadowed_field`（method.transition_state_method） | Batch preflight | 移除平铺镜像字段 |
| G | （流程缺陷，经 codex 修正表述）inline item 未带 role/tag → 默认 INT 语义 + 平台默认方法（B3LYP-D3 普通 Opt），TS 意图无任何 typed 提示地落空 | job 005 | 用户指正后取消；改 review→from_artifact 链路（job 006 OptTS 正确）；inline 显式 role 亦可 |

其他易用性观察：①`pes_profile.frames[].geometry_path`（`scan_frames/frame_005.xyz`）与实际落盘路径（`WORK/07_PATH/pes_scan_001/frame_005/orca_relaxed_scan_try1.xyz`）不一致；②`acp_capabilities` 的 `workflow` 参数仅 stdio schema 暴露；③prepare 为异步（pending→ready 需重试一次）；④pes_recommendations 为空（算法推荐未产出）时 selection_mode=manual_only，人工选点是当前唯一路径——与"全自动是首版目标"的差距证据。

## 7. 结论与缺项（诚实分级）

1. **TS 驻点：supported**——OptTS 收敛 + 恰一个显著虚频 −424.44 cm⁻¹ + 虚模对应两键协同形成（与键轴余弦 ≈ −0.9978，codex 定量核实）+ 几何中间态特征。准确表述：B97-3c 级别下的一阶鞍点，模式与 DA 成键坐标相符。
2. **反应路径连接：inconclusive（缺 IRC）**——未运行 irc 工作流验证 TS 分别连向 CPD+乙烯与降冰片烯；"TS 属于本 DA 反应"的完整判定缺端点证据。
3. **INT 侧：无数据**（曲线无平台期，未选 INT，未补算）。
4. **势垒数值：partial**——电子能量差 + 约束帧端点，未含 ZPE/热校正，未做端点自由优化。
5. 原始产物均留在平台 run_root（`/home/xieningke/ACP_runs/uncategorized/`，由平台管理，未改动），本报告仅引用 TaskRef：`20261030_094753_001_PESsearch`（failed，笔误负例）、`20261030_094840_002_PESsearch`（failed，adjacent-pair）、`20261030_095322_003_PESsearch`（failed，fixed-boundaries）、`20261030_095632_004_PESsearch`（completed，PES）、`20261030_100432_005_BatchOptimize`（cancelled，inline 负例）、`20261030_101415_006_BatchOptimize`（completed，TS opt+freq）。

## 附：时间线（Asia/Shanghai）

09:46:17 stdio 9 工具 + guidance 验证 → 09:47:53/09:48:40/09:53:22 三次 PES 失败 → 09:56:32 产物侧 PES 提交 → 09:58:25 completed → 10:02 prepare/read pes_profile（redacted→HTTP）→ 10:04:32 inline Batch 提交 → ~10:10 用户指正流程 → 10:12:34 job 005 取消 → 10:13:04 pes/review 确认 pes_ts_frame_005 → 10:14:15 Batch 006 提交 → 10:16:26 completed → 10:19-10:25 证据读取与本报告。

## 附二：独立验收记录（codex / gpt-6-astra，2026-10-30）

- 评审方：codex-cli 0.163.0（gpt-6-astra，effort=high，只读沙箱），session 01a18a9e-59a1-76c0-b70c-98cb39c4e1b2，约 109k tokens。
- **总体判定：PASS-with-notes**。数值自洽 PASS（逐帧独立重算吻合、频率 6+1+44 结构核实、两对虚模位移与键轴余弦 ≈ −0.9978、产物 SHA 一致）；磁盘事实抽查 PASS（job.json 时间线、pes_review 回执）；Controller 实时 GET 因评审沙箱禁网 NOT_VERIFIED（以同一权威的磁盘 job.json 代替核实）。
- 已按 notes 修正本报告：frame5 几何精度、ts_validation 的定位（非独立证明）、势垒措辞收窄（约束帧电子能量差，不做文献一致性声明）、inline 缺陷归因改述（角色未标注默认 INT + 无 typed 提示，而非"绕过确认门"）。
- 原始评审全文：本仓 `reports/acp2_mcp_guidance_20261030/codex_result_acceptance.md`。
