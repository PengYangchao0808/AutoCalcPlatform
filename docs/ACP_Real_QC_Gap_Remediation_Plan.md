# ACP / CCCP 真实 QC 缺口修复计划

日期：2026-10-06。状态：**计划已制定；生产代码修复尚未开始**。

## 1. 基线、证据与目标

原始测试基线为 `a90d00b`，报告位于 `/tmp/acp_mt/FULL_TEST_REPORT.md`。本次复核基线为 `32d474890495e2d0f92940f5cfd074d84988337e`；两者之间仅增加 `tests/test_acp_nmr_dp5q_isolation.py` 的模块状态恢复，D1–D8 涉及的生产代码未变。

本次追加证据见 [复核回执](reports/ACP_Real_QC_Gap_Audit_20261006.json)。原始日志和探针脚本位于 `/tmp/acp_gap_review/`。回执保存文件摘要；实施前须将必要的原始 QC 工件固化为仓库 fixture，不能长期依赖 `/tmp`。

目标：恢复 scan、tsmode、带溶剂 NMR 的真实端到端运行；使 CASSCF 的科学完成状态可信；修复 gradient timeout 和能量发布；让真实计算测试、手册与运行契约一致。

本轮按计划交付，尚未修改生产实现、提交 API 作业或重启 systemd 服务。追加计算均使用隔离的 `/tmp/acp_gap_review` 目录。

### 本次已完成的复核

| 项目 | 证据 | 结论 |
| --- | --- | --- |
| D1 | 原始水分子及弯曲 HCN `.hess` 均被现有解析器拒绝；独立按索引解码后矩阵严格对称 | 分块列头/行号被当数值；原始数据未损坏 |
| D1 科学交叉检查 | 独立解码矩阵交给现有模态计算，水分子频率与 ORCA 差异小于 `0.0002 cm⁻¹`，弯曲 HCN 小于 `0.00014 cm⁻¹` | 非线性样例中的主要阻断位于读入格式 |
| D1 附加边界 | 受控线性三原子几何的外部子空间返回 6 列，应为 5；现有 QR 后按列范数筛选不能识别秩亏 | 读入修好后，还须防止线性分子错误剔除一个振动自由度 |
| D2 | 相同水分子、r2SCAN-3c、单核、O–H 1.05→1.25 Å、3 点：默认 ScanTS 失败；`use_scants=False` 完成 3/3 帧 | 键长最大误差 `5.22×10⁻⁷ Å`；关闭 ScanTS 已有真实正向对照 |
| D2 输入 | 当前 CLI 输入 `CCO` 退出 1，报 `Unsupported input format ''` | 文件校验位于 `_handle_scan`，须同时修正输入物化 |
| D3 | `nmr --input CCO --spectrum … --solvent chloroform --preset censo-zero` 退出 1 | 即使跳过 CENSO，也在 CREST/xTB 初始化时因 `smd` 失败 |
| D4 | 原冻结 gradient 请求包含 `timeout_seconds=600`，当前 CLI 退出 1 | `optimization_control.timeout=None` 写入时报 TypeError |
| D5 | 原始 CASSCF 日志有 ENERGY/GRADIENT 收敛标记及 `N(occ)=1.99733 0.00267`；解析器返回 false、空占据数/根列表 | 不止收敛标记，实际输出区段也需兼容 |
| D5 附加反例 | 只有普通 SCF 收敛和最终能量的日志被解析为 `converged=True` | 前置 SCF 成功不能证明 CASSCF 成功；提升为 P1 |
| D6 | singlepoint、optimize、frequency、xtb_optimize、casscf 五类真实 manifest 均有空 energy path | 缺口在共享 executor 发布逻辑，范围大于 simple/casscf 两个名称 |
| 定向单测 | tsmode source、task scan、OrcaGradient、CASSCF parser，共 38 passed | 现有合成输入和 mock 未覆盖已复现缺陷 |

**环境差异必须留在验收记录中。** 本次 `/opt/acp/venv/bin/python` 为 Python 3.11.13，原报告写的是 3.13.9；本次 PATH 已能找到 CREST/xTB，不能声称当前还复现了原报告的 CREST/xTB skip。D8 的代码缺口仍成立：marker 只看 `shutil.which`，与生产配置解析不同；Shermo/ISOSTAT 当前仍仅由配置解析到。探测到的二进制还可能与测试实际启动的二进制不同。

## 2. 优先级与实施顺序

| 优先级 | 缺陷 | 完成标准 |
| --- | --- | --- |
| P1 | D1 | 原始 ORCA `.hess` 可解析、几何/频率/模态对应正确，原文件可被 OptTS 直接读取 |
| P1 | D2 | SMILES/文件输入可用；普通 scan 默认只扫描，显式 ScanTS 才搜索 TS |
| P1 | D3 | CREST/xTB、CENSO、GIAO 的有效溶剂模型各自合法且可追溯 |
| P1（由 P2 提升） | D5 | CASSCF 正向真实输出被正确识别；普通 SCF、未收敛、截断输出不能伪装 completed |
| P2 | D4 | 缺省/null timeout 可用，payload 超时确实传到执行层 |
| P3 | D6 | 每个新 energy product 对应可读取的 RESULT 文件，发布失败可独立重试 |
| P3 | D7 | 手册示例和 CLI 实际语义一致 |
| P3，作为实施前置 | D8 | 配置路径足以打开真实测试门，探测与实际执行使用同一二进制 |

实施顺序：**测试路径与真实 fixture → D4 快速解阻 → D1/D2/D3/D5 → D6 → D7 → 集成验收**。每个行为修改与对应回归测试同批提交。T00 已完成，其余勾选项均是待实施工作。

## 3. 贯穿修复的架构约束

- QC 格式解析与外部进程只在 `cccp/qc`；能力适配在 `cccp/backends`；计算任务执行只在 `cccp/calculation/tasks`；ACP 负责输入、编排、恢复、发布。
- ACP primitives/backends 保持兼容转发。不得在 ACP 再写一个 scan/CASSCF/gradient 计算实现，不新增 retired 工作流。
- 参数进入已有 typed request/options 和适配入口；有效科学参数进入身份。ScanTS 和溶剂模型变化不能继续复用旧参数的结果。
- 决定科学完成状态的门控在任务层及结果复用检查中生效；发布问题在发布层重试，不能触发无必要的 QC 重算。
- 修改 QC 接口时更新 `test_f4_scope_audit.py` 的具体 amendment 声明和断言；保留 baseline、唯一实现检查及四个 grep pin，不放宽已有守护。
- 若添加配置默认值，同步 Python built-in 与 `config/defaults.yaml`；不要新增科学库依赖或复制已有溶剂/单位映射。

## 4. 可执行任务清单

### 阶段 A：建立可靠的回归入口

- [x] **T00 — 当前 HEAD 复核及最小真实对照计算。** 产物为本计划及复核 JSON；D2 正负向各执行一个真实 ORCA task。未重跑完整 7469 项测试，原报告结果作为历史证据保留。

- [ ] **T01 / D8 — 统一真实测试的二进制解析和执行配置。**
  - 位置：`tests/conftest.py`、真实 QC 测试的配置 fixture/构造处；复用 `cccp.config.load_config` 与 `cccp.software.resolve_executable`。
  - 为真实计算建立独立的配置 fixture，marker 和 fixture 使用同一批已解析绝对路径。支持显式配置、环境覆盖、用户配置与生产 resolver 的合法回退。
  - 处理 autouse `_clean_env_vars` 清理环境后的执行路径；只修改 marker 不足以修复 D8。普通单测继续用隔离的 `sample_config`，不能因为用户 YAML 存在而改变 mock 测试。
  - collection 阶段不执行 QC，不引入长时间版本探测；版本证据在真实任务启动时记录。
  - 验收：受控 PATH 不含二进制、临时用户配置含有效绝对路径时，真实 marker 打开且实际启动该路径；无二进制时仍明确 `NOT_VERIFIED`；无配置污染；真实 CREST/xTB 用例不因 YAML-only 布局 skip。

- [ ] **T02 — 固化原始格式 fixture 与缺陷回归。**
  - 位置：`tests/fixtures/`、`tests/test_acp_tsmode_source.py`、`tests/test_acp_electronic_state.py` 及各缺陷所属测试。
  - 保存未经改写的 ORCA 6.1.1 `.hess`：水分子、已有真实 TS；保存对应 `.out/.xyz` 或所需原始区段，记录方法、几何、版本、来源、摘要和单位。
  - CASSCF fixture 保留实际收敛区段、末次 `N(occ)`、`CASSCF RESULTS`、各 ROOT 与正常终止。旧合成 fixture 用于畸形输入和兼容测试；真实文件成为格式契约基准。
  - 独立参考值使用 ORCA 输出的数值、原子坐标与频率，不以被测解析器自己的输出产生期望值。
  - 验收：改实现前，新增针对缺陷的回归确实失败；无二进制的 CI 也能运行真实文件解析测试；fixture 自带来源说明。

### 阶段 B：解除运行阻断并修正科学完成状态

- [ ] **T03 / D4 — 修复 nullable timeout 和共享配置副作用。**
  - 位置：`acp/workflows/orca_gradient.py::run_orca_gradient`；测试 `test_acp_workflows_orca_gradient.py`、`test_acp_legacy_adapters.py`。
  - 缺省或 null 的 `optimization_control`/`timeout` 规范化为可写映射；已有 timeout 映射保留其他字段；不合法的非映射值给出可识别的输入/配置错误。
  - 复制将被修改的配置层，防止浅拷贝写坏调用者的 `executables.orca`、resources 或 timeout；冻结 `pes2ts_orca_gradient_request_v1` 格式保持不变。
  - `timeout_seconds` 在 typed `TaskResources.timeout_s` 和 ORCA 实际执行配置中一致。测试捕获执行参数，不能仅断言没有 TypeError。
  - 验收：missing/null/mapping 三组配置及 caller 不被修改；带原请求执行真实水分子梯度，能量及 `3N` 梯度均存在；受控超时返回失败且不发布 completed。

- [ ] **T04 / D1 — 按真实 ORCA 格式解析 Hessian、几何和频率。**
  - 位置：`cccp/qc/interfaces/hess_file.py`；保留纯解析器及公共返回契约。
  - 以列头和行号填充矩阵，处理末尾不足一个块的列；校验索引范围、重复/缺失项、矩阵维数、有限性和对称性。禁止读够 `dimension²` 个 token 即忽略余下数据。
  - 从真实 `$atoms` 行读取 `symbol mass x y z`，坐标为 Bohr；可继续支持明确的旧 `$coords` 合成格式，但缺少所有几何来源必须失败，不能生成全零坐标。若两个几何节同时出现，交叉校验一致性。
  - `$vibrational_frequencies` 按 count 与 index/value 解码，不把 count、行号加入频率列表；保留六个零模式，不用“过滤零值”破坏模式编号。
  - 验收：原始水分子及 TS 文件解析成功，坐标/质量/Hessian 数值与独立证据一致；D 指数、不同块宽、缺行、重复索引、越界、截断、非有限值均有明确结果；原始文件内容与摘要保持不变。

- [ ] **T05 / D1 — 打通 bundle→原始 Hessian→OptTS，并修正线性边界。**
  - 位置：`acp/calculations/tsmode/source.py`、既有 engine/任务适配链；必要时仅补 `cccp/qc/interfaces/orca.py::transition_state_opt` 的文件交接。
  - 保留源 `.hess` 全部原始字节并通过现有 staging 交给 ORCA；不能将供 Python 分析的矩阵重新平铺写成 ORCA 输入，不能丢 `$act_energy` 等未解析节。
  - 复核 Bohr→Å、原子顺序、几何 RMSD、选定频率索引与模态对应、源文件摘要。保留几何不匹配与无 Hessian 的拒绝行为。
  - 外部子空间用秩判定保留独立平移/转动方向；明确非线性 `3N−6`、线性 `3N−5`。该附加边界是本次受控探针确认的问题，与原报告的弯曲 HCN 实例区分记录。
  - 验收：真实 ORCA TS `.out/.hess/.xyz` 原封不动进入 tsmode，OptTS 正常读取 InHess 并完成，report/manifest 齐全且目标模态验收通过；线性/非线性计数正确，真实水频与源 TS 虚频在预先约定容差内；负例不会 completed。

- [ ] **T06 / D2 — 支持 scan 的 SMILES 输入物化。**
  - 位置：`acp/cli.py::_handle_scan`、现有 StructureReader/输入适配；不要直接删除 `_check_input` 后继续将 `CCO` 当作文件 Path。
  - 复用已有 SMILES→结构入口，先物化可追溯的 XYZ，再交给既有 CalculationRequest/TaskRequest；保留有效文件输入、charge/multiplicity 和 0-based 原子编号。
  - 明确不存在的文件与非法 SMILES 的报错；物化输入及原子序与任务参数进入来源/身份记录，防止恢复时使用不同嵌入几何。
  - 验收：CLI 的 `--input CCO --coordinate 0,1,…` 可进入任务执行；真实小范围扫描完成；XYZ/GJF/COM/INP 原有读取不回归；原子越界、非法输入在启动 QC 前失败；scheduler 已物化输入走相同语义。

- [ ] **T07 / D2 — 普通扫描默认关闭 ScanTS，提供显式开关。**
  - 位置：`cccp/calculation/requests.py::ScanOptions`、`tasks/scan.py`、`acp/calculations/legacy_adapters.py`、CLI/catalog/本地及远程 argv 和 edit coverage 的参数投影。
  - 推荐契约：typed `ScanOptions.use_scants: bool=False`，CLI `--scants` 显式开启。任务层总是把有效布尔值传到 backend/interface；底层原有直接接口的兼容默认不靠本轮静默改变。
  - 保持 `ScanMode.RELAXED` 表示松弛扫描；ScanTS 是额外行为，不混用为 rigid/relaxed 模式。对同步多坐标/显式网格等现有执行分支中不能兑现的 ScanTS 请求提前给出明确错误，不静默忽略。
  - 有效默认值必须进入序列化、科学身份、恢复判断及编辑重算；旧缓存缺少该字段时不能默认当作新语义的普通 scan 已完成结果。
  - 验收：默认单调 3/3 点及多坐标扫描正常；默认 ORCA 输入无 ScanTS；显式开关有 ScanTS，最高能量在端点的负例返回失败；失败帧保留索引，下游依赖被阻断；模式变化使身份变化且不收养旧结果。已获得真实正负向对照见复核回执。

- [ ] **T08 / D3 — 分阶段解析溶剂模型，并覆盖共享协议引擎。**
  - 位置：`acp/workflows/nmr.py::_run_conformer_generation/_run_conformer_tasks`、复用的 `energy_shared.py`/`ensemble.py` 溶剂传参；科学名称映射复用 `cccp/utils/solvent_map.py`。
  - 将溶剂名称和各阶段模型分开解析：CREST/xTB 只接收 `alpb/gbsa/none`；CENSO DFT 与 ORCA GIAO 使用各自有效模型。CLI `--solvent-model` 按当前 help 只控制 GIAO，不直接写入 CREST。
  - 无 sampling 模型配置且指定溶剂时，推荐使用明确记录的 sampling 默认 ALPB；显式 sampling 配置优先。DFT SMD 与 sampling ALPB 是不同模型，不把两者描述为等价转换。无溶剂/显式 gas-phase 的各阶段行为单独测试。
  - 同步配置默认与有效参数回执；采样、几何/布居能量、GIAO 的模型进入相应 protocol fingerprint/缓存身份，确保改模型后重新计算必要科学阶段。不能继续沿用旧协议的校准声明。
  - `ensemble.py` 仍有“为 CENSO 和 CREST/xTB 同时默认 SMD”的相同模式，须覆盖其 Confsearch 消费路径；既有气相 4 协议通过不足以证明带溶剂也正确。
  - 验收：真实乙醇 `censo-zero` + chloroform 从 CREST 运行到 GIAO/report；`censo-light` 再覆盖 CENSO 中间阶段；带溶剂 Confsearch 的共享路径通过；参数化 mock 验证 alpb/gbsa/none、GIAO smd/cpcm/none 和配置优先级；未知模型保持严格失败。NMR 验收只证明链路和产物，不宣称 DP4/DP5 校准准确性。

- [ ] **T09 / D5 — 修复 CASSCF 的版本兼容解析。**
  - 位置：`cccp/qc/interfaces/orca.py::parse_casscf_output` 及其 occupation/root helpers。
  - 支持 ORCA 6.1.1 ENERGY/GRADIENT 收敛措辞、可变空白；保留既有旧标记。普通 `THE SCF HAS CONVERGED` 不能单独证明 CAS 收敛。只有 energy 标记、没有最终 CAS 结果/完整收敛事实的日志不应被接受。
  - 读取末次有效 `N(occ)` 或明确的 active natural occupation 区段，并绑定 active space；不能把所有 inactive/virtual orbital 的 OCC 全部当作 active occupations。兼容真实 `CASSCF RESULTS` 与 ROOT 区段。
  - 分开验证 CAS 能量、NEVPT2 修正与相关能量，不把最终其他方法能量自动当 CAS 能量。测试覆盖多个作业块、截断日志和只有前置 SCF 的反例。
  - 验收：原始水 CAS(2e,2o) 返回 converged=true、两个占据数约 `[1.99733,0.00267]`、正确 ROOT 0 能量；正常终止与最终区段一致；旧格式回归通过；只有 SCF/未收敛/截断负例不返回已收敛 CAS。

- [ ] **T10 / D5 — 在任务完成与恢复复用时落实 CASSCF 科学门控。**
  - 位置：`cccp/calculation/tasks/casscf.py` 及既有 result reuse/前置条件验证处。
  - 不以 `success=True` 加能量存在就判 completed；显式 CAS 收敛事实与所请求的必要产物满足后才完成。失败结果保留诊断日志、能量等证据，但标记 complete=false，依赖步骤被阻断。
  - 不将旧 `converged=false` 的 completed 回执直接收养。若要修复旧误判元数据，仅在原始日志完整、摘要/身份有效时按新解析规则重新判定；缺证据则保守重算。读取过程中不得直接改写历史任务。
  - 验收：真 CAS 收敛 completed；backend success 但 CAS 未收敛时 failed；恢复同样重判；旧版本 fixture 不发生失败结果复活；单纯发布重试仍不重复执行 QC。

### 阶段 C：修复正式产物与使用契约

- [ ] **T11 / D6 — 给共享 executor 的能量产物提供真实文件。**
  - 位置：`acp/calculations/executor.py::_write_result_manifest` 与发布/恢复测试。
  - 推荐在 `RESULT/energy/<step_id>.json` 原子写入 energy、单位 `hartree`、方法/步骤和来源；product.path 使用 `energy/<step_id>.json`，相对 RESULT，稳定 product id 保持不变。
  - 从已有科学结果发布，不仅在 metadata 中补一个数值，也不直接把 `WORK/...` 当成 RESULT 相对路径。保留 `scientific_result.json` 作为恢复证据。
  - 覆盖 singlepoint/optimize/frequency/xtb_optimize/casscf。诊断产物带实际状态；blocked 注记本来就不是能量文件，不借本次修复把所有历史空 path 全局禁止。
  - 旧 manifest 的读取兼容保留；API 文件读取、viewer 和 RemoteStructureCache 使用已有路径契约验证。混合 task-relative FILE 产物的历史路径另作说明，本轮不全局改写存量 manifest。
  - 验收：五类新 energy product 均可 resolve/read，值和科学回执一致；发布中断后 resume 只补发布，QC 调用计数不增加；新远程产物能由 catalog/cache 读取；历史读取 fixture 不回归。

- [ ] **T12 / D7 — 按修复后的行为更新手册和示例。**
  - 位置：`docs/ACP_Function_Test_Manual.md`、README、Simple/TSMode/相关设计文档和 CLI help；必要时同步 AGENTS 中直接给出的命令与测试约定。
  - provenance 路径示例用 `--ts-provenance prov.json`；内联内容才用 `--ts-provenance-json`。IRC 命令补 `--input-role transition_state` 并以实际 provenance 和 geometry_sha256 校验。
  - 手册不再以被 ORCA 忽略的 `--step` 作为有效控制；保留既有兼容 warning，并写明未生效，移除 runnable 示例中的该选项。若以后支持 IRC step 映射，另行验证单位和语义。
  - Confsearch 描述按协议与 refinement-policy 说明实际执行阶段，`xtb-crest screen` 不标为 DFT 精修；默认 scan 描述与显式 `--scants` 对齐。
  - 写明 RESULT 相对科学产物与历史 FILE 路径口径；修正真实测试的检测方式、执行 Python/二进制版本，并清理“所有链路均无自动化覆盖”等已与 `test_acp_nmr_qc_smoke.py` 不符的绝对表述。
  - 验收：关键示例使用真实 fixture 或隔离测试目录执行；参数 help 与示例一致；不存在 provenance/role 缺失导致的假失败；以 PASS/FAIL/NOT_VERIFIED 明确记录覆盖情况。

### 阶段 D：集成验收和交付

- [ ] **T13 — 完成修复后的科学、恢复、发布和架构验收。**
  - 依赖 T01–T12。先运行受影响的定向测试和下表真实矩阵；结果稳定后运行一次 CI 要求的全量 `-m "not slow"`、对应 compileall 和工具链检查。
  - 真实计算使用隔离数据根，限制并发及 nproc，保留原始输入、输出、退出码、版本和工件摘要；所有新进程应有超时及结束回执。缺少二进制只能记 NOT_VERIFIED。
  - CLI 修复通过后，重启 `acp.service` 使服务加载修复版本；按 root AGENTS 的 systemd 规则执行，并记录服务实际版本。仅对本轮新建 API 测试任务验证 scan/NMR/CASSCF/产物读取；既有 failed 作业保留原状。
  - 最终记录新的 14 工作流矩阵和对应验收级别。CASSCF、tsmode、scan、NMR 必须满足科学/产物门槛，不能只看 exit 0。明确哪些未变路径沿用原始报告、哪些是在修复 HEAD 重跑；不得将历史证据标成当前版本全链路实测。
  - 保存修复总结、未验证项和精确版本；任务可逐项关闭，计划总体只有在所有缺陷验收完成后才关闭。

## 5. 修复后的真实计算验收矩阵

| 用例 | 输入与规模 | 必须验证 |
| --- | --- | --- |
| 普通 scan | 水 O–H 1.05→1.25 Å，3 点，r2SCAN-3c | 3/3、真实几何距离、能量、轨迹与 manifest；无 ScanTS |
| SMILES scan | `CCO` 的 C–C 小范围扫描，3 点 | 输入物化、原子映射、charge/multiplicity、帧产物可读取 |
| 显式 ScanTS | 同一单调水扫描作为负例；适合 TS 的独立例作正例 | 负例不 completed；正例确实额外运行 TS 搜索，不能只有扫描帧 |
| tsmode | 已有真实 TS `.out/.hess/.xyz`，优先 `/tmp/acp_mt/ts_opt_m0/` 的真实 TS | 源摘要、InHess 字节、选模态、收敛及虚频/目标重叠、报告与 manifest |
| NMR | 乙醇，chloroform，censo-zero；再 censo-light | sampling/GIAO/DFT 各段有效模型、shielding 数目及原子绑定、报告状态 |
| Confsearch 溶剂回归 | 共享 CREST→CENSO 路径的小分子 | CREST 不接收 SMD；CENSO 的指定 DFT 模型保留 |
| OrcaGradient | 原水请求，timeout_seconds=600，配置 timeout=null | 能量、3N 梯度及单位、timeout 传递、正式产物 |
| CASSCF | 原水 CAS(2e,2o)；受控缺少 CAS 收敛事实的负例 | 真实 convergence、active occupations、roots；负例失败及恢复拒绝 |
| energy 发布 | 上述及 simple opt/freq/xTB 的小分子 | 每个 energy path 存在，值一致；publish-only retry 无 QC 重算 |
| API | 修复版服务的新任务 | scan/NMR/CAS 提交、detail/files/viewer 产物读取、错误状态准确 |

无需为了 D7 的文字修改重复昂贵的无关 QC；全工作流回归按修改影响和最终发布要求选择，明确未重测项。远程提交不作为这 8 项的默认修复步骤；新增 scan 参数的远程 argv 投影、路径缓存和既有 remote tests 必须验证。

## 6. 自动化门与关闭标准

定向测试按任务使用以下已有文件，新增真实格式/配置/状态反例随同修改加入；具体 test node 名以实施 HEAD 为准：

- D1：`test_acp_tsmode_source.py`、`test_acp_tsmode_mapping.py`、`test_acp_tsmode_engine.py`、`test_acp_tsmode_orca_inputs.py`、`test_acp_api_tsmode.py`。
- D2：`test_cccp_task_scan.py`、`test_scan_workflow.py`、`test_acp_workflows_simple.py`、`test_acp_legacy_adapters.py`、`test_recovery_identity.py`、`test_acp_job_edit.py`、已有 ORCA 同步扫描 integration。
- D3：`test_acp_workflows_nmr.py`、`test_acp_nmr_method_config.py`、`test_acp_nmr_protocol_spec.py`、既有 Confsearch/ensemble 溶剂测试、`test_acp_nmr_qc_smoke.py` 中真实链路及新增 CLI 带溶剂例。
- D4：`test_acp_workflows_orca_gradient.py`、`test_acp_legacy_adapters.py`、对应 task gradient 测试。
- D5：`test_acp_electronic_state.py`、`test_cccp_task_casscf.py`、`test_calculation_executor.py`、`test_recovery_fixtures_smoke.py`、`test_recovery_cross_version.py`。
- D6：`test_calculation_executor.py`、`test_storage_manifest_kinds.py`、发布中断 recovery fixtures、viewer/remote cache 对应测试。
- D8：`test_cccp_software.py` 与新增 conftest gate/config 隔离测试；真实 CREST/xTB 测试由同一配置 fixture 实际运行。
- 架构门：`test_architecture_invariants.py`、`test_f4_scope_audit.py`、`test_grep_gates_script.py`，及 `python scripts/check_grep_gates.py`。

每项关闭必须有四类事实：缺陷回归通过、相关真实格式/计算通过、失败/恢复语义正确、相邻契约无回归。完整单测通过不能替代真实计算验收；原始 11/14 结果与本次 38/38 单测证明了这种区别。

## 7. 官方接口语义参考

ScanTS 会把扫描中最高能量结构作为 TS 搜索起点，并需要相邻点用于曲率估计；因此普通单调扫描应使用独立的松弛扫描行为。见 [ORCA 6.1 Transition State Searches](https://www.faccts.de/docs/orca/6.1/manual/contents/structurereactivity/optimizations_TS.html)。

原始 Hessian 可以由 `%geom InHess Read` / `InHessName` 读取，计划采用原文件交接。见 [ORCA 6.1 TS Optimization tutorial](https://www.faccts.de/docs/orca/6.1/tutorials/react/tsopt.html)。

官方示例包含 CAS-SCF GRADIENT 收敛标记、`N(occ)` 和 `CASSCF RESULTS`，支持 D5 对实际区段的修复方向。见 [ORCA 6.1 CASSCF manual](https://www.faccts.de/docs/orca/6.1/manual/contents/modelchemistries/CASSCF.html)。精确的本机格式与数值仍以附带版本/摘要的原始输出 fixture 为验收依据。
