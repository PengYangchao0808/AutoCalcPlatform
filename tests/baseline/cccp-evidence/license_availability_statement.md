# C08-s3 (T41): CCCP 科学软件许可与可获得性声明 (tracked)

- **Owner:** C 路科学负责人 · **Plan:** `.omo/plans/acp-parallel-plan-3-cccp.md` todo 41（C08-s3）
- **Base:** 共享脏工作树 fresh snapshot（2026-10-19）；资产实际位置 `src/cccp/analysis/nmr/model_assets/`（T55 已迁移，8 文件 / 65,513,350 B）
- **Closes:** GAP-9、GAP-12 · **依据:** 主方案 §18.1 / §28.3（`docs/ACP_Architecture_Adjustment_Plan_20261016.md`）
- **范围:** 本声明覆盖 T40a 声明的**本次 IN_SCOPE 首批闭包**（`cccp.analysis.*` + `cccp.calculation.products`）内随包分发的全部内容，以及平台调用但不分发的全部外部 QC 软件。T40b 完整发行层的许可收口由 T39 负责，不在本文件冒领。

---

## 1. 自有代码：CCCP（MIT）

| 项 | 声明 |
|---|---|
| 许可证 | **MIT License** |
| 许可文本位置 | 仓库根 `LICENSE`（21 行完整 MIT 文本，`Copyright (c) 2026 彭扬超`）；`pyproject.toml:11` `license = {text = "MIT"}`，classifier `License :: OSI Approved :: MIT License` |
| 来源 | 本仓库自有实现：`src/cccp/**`（QC 底座 ≈86 .py：config/software/version + core/io/pipeline/qc/utils/backends/calculation/analysis） |
| 分发方式 | 随本仓库与独立 `cccp` wheel 分发（T40a 证据：wheel 零 `acp*` 条目，178 entries 全部 `cccp/`） |

已核验（本任务实测，非转述）：根 `LICENSE` 文件存在且为标准 MIT 文本（见上引用行）；`pyproject.toml` 声明一致。MIT 许可文本**原样保留**于根目录，本声明不改变、不替代它。

## 2. 模型资产：DP5/FCHL NMR error-model assets（MIT，随包分发）

**单一资产位置**：`src/cccp/analysis/nmr/model_assets/`（wheel 内 `cccp/analysis/nmr/model_assets/`，T56 规则 `"cccp.analysis.nmr" = ["model_assets/*"]`）。

**许可与来源**：全部 8 个文件再分发自 **Goodman-lab/DP5** 仓库（https://github.com/Goodman-lab/DP5），commit `b6cf559007a5d13fe79654f37daf945ee1661a23`（2023-07-15）。许可为 **MIT**（`LICENSE-DP5`：PyDP4 integrated workflow v0.4, `Copyright (c) 2015 Kristaps Ermanis, Jonathan M. Goodman`）。`LICENSE-DP5` 与 `NOTICE.md` 两个许可/notice 文件**随资产同目录随包分发**，不得拆离。

8 文件清单（字节与 sha256 录自 T56 §2 资产摘要表，本任务逐项核对现场文件在位）：

| # | File | Bytes | License / notice |
|---|---|---|---|
| 1 | `atomic_reps.gz` | 22,171,041 | DP5 repo (MIT)，`LICENSE-DP5` + `NOTICE.md` 随包 |
| 2 | `frag_reps.gz` | 18,044,581 | DP5 repo (MIT)，同上 |
| 3 | `i_w_kde_mean_s_0.025.p` | 24,353,668 | DP5 repo (MIT)，同上 |
| 4 | `folded_scaled_errors.p` | 851,489 | DP5 repo (MIT)，同上 |
| 5 | `c_w_kde_mean_s_0.025.p` | 79,518 | DP5 repo (MIT)，同上 |
| 6 | `tms_references.txt` | 4,578 | DP5 repo (MIT)，同上 |
| 7 | `LICENSE-DP5` | 1,175 | MIT 文本本体（PyDP4 v0.4, © 2015 Ermanis & Goodman） |
| 8 | `NOTICE.md` | 3,204 | 再分发 notice + 来源 revision + 学术引用（Smith & Goodman, *JACS* 2010, 132, 12946, DP4；Howarth, Ermanis & Goodman, *Chem. Sci.* 2021, DP5） |

补充事实（引自 `NOTICE.md`，准确性已核）：

- **FCHL 运行时依赖**：FCHL 加权 DP5 路径（`dp5_mode=fchl`）需要 `qml` 包（QMLKit, von Lilienfeld group），它**不是**本包构建依赖；不可用时 DP5 静默降级为无加权全局 KDE 回退（`dp5_mode=fallback`）。`qml` 当前仅对 `numpy<2` 可构建。**本平台不分发 qml**，其许可/可得性由其上游声明。
- **pickle 兼容**：`.p` 文件为旧版 `scipy.stats.gaussian_kde` pickle，加载时从 `dataset`/`weights`/`factor` 属性重建（`_rebuild_kde`），对 scipy 内部 API 变更稳健。
- **已知 gap（非缺陷）**：`dp5q_stub_params.json` 依 T2/T4/T55 登记为缺失（基线即缺），不在 8 文件集内，无许可问题。

## 3. 外部软件：ORCA / xTB / CREST / CENSO / ISOSTAT / Shermo（外部提供，不分发）

**统一口径**：以下软件**均不由本平台再分发**（wheel/仓库不含其任何二进制或源码），平台只经配置解析既定安装路径并生成输入、调用子进程、解析输出。解析唯一驻点 `src/cccp/software.py::resolve_executable`（优先级：显式 config `executables.<name>.path` → `CONFSEARCH_*_PATH` 环境变量 → `PATH` 上 `shutil.which` → 有界常规安装布局的唯一命中 → None）。使用者在运行前须自行从提供方取得并安装（`acp doctor` 可诊断可见性）。

| 软件 | 提供方 / 获得渠道 | 许可与可得性口径（以提供方现行声明为准） | 平台配置键 |
|---|---|---|---|
| **ORCA** | FACCTS GmbH（https://www.faccts.de/orca/） | **外部提供**。对学术用户免费但须注册接受其 EULA；闭源，不允许再分发。 | `executables.orca.path` |
| **xTB** | Grimme group（https://github.com/grimme-lab/xtb） | **外部提供**。GitHub 开源（LGPL 系），含 conda-forge 渠道。 | `executables.xtb.path` |
| **CREST** | Grimme group（https://github.com/grimme-lab/crest） | **外部提供**。GitHub 开源（LGPL 系）。 | `executables.crest.path` |
| **CENSO** | Grimme group（https://github.com/grimme-lab/censo） | **外部提供**。GitHub 开源（LGPL 系）。 | `executables.censo.path` |
| **ISOSTAT** | 随 Molclus 程序包分发（Grimme/Bannwarth，见 Grimme group 软件页 https://www.chemie.uni-bonn.de/grimme/de/software） | **外部提供**。按 Molclus 分发条款免费取得；随上游分发包更新。 | `executables.isostat.path` |
| **Shermo** | Tian Lu（https://sobereva.com/soft/shermo） | **外部提供**。免费使用，闭源 freeware，不允许再分发。 | `executables.shermo.path` |
| Molclus（附带） | 同 ISOSTAT 行 | **外部提供**。平台 resolver 表含 `molclus`（`cccp/qc/interfaces/molclus.py` 消费），同样只解析路径不分发。 | `executables.molclus.path` |

> 上表"许可口径"为撰写时的公开常识性描述，**权威以各提供方现行条款为准**（主方案 §28.3 冻结规则：外部组件发行后功能/许可变化需重新核验）。本声明不伪称任何一项已随包分发，也不改变任何既有许可。真实 QC 程序运行验证状态 = **NOT_VERIFIED**（T40a 口径，本次范围样例无需真实 QC 程序）。

---

## 4. QA：声明 vs wheel namelist 交叉核对（acceptance：无漏项）

**核对基准**：T40a 证据 `.omo/evidence/acp-parallel-plan-3-cccp/task-40a-acp-parallel-plan-3-cccp.md` §5（wheel = 冻结提交 `5518aa6` 出树构建 `cccp-0.2.0-py3-none-any.whl`，sha256 `bf0a44ce…31c3ca0a2`）：

```
missing model_assets: []
digest OK cccp/analysis/nmr/model_assets/{atomic_reps.gz, frag_reps.gz,
    i_w_kde_mean_s_0.025.p, folded_scaled_errors.p, c_w_kde_mean_s_0.025.p,
    tms_references.txt, LICENSE-DP5, NOTICE.md}   # 逐项 digest OK
OK: 8/8 assets present + digests match; no acp
```

**交叉核对结果**（本节即核对记录）：

| 核对项 | 结果 |
|---|---|
| 声明 §2 的 8 文件集 vs wheel namelist 8 条 `model_assets` 条目 | **一一对应，零差集**（双侧均为上表 8 个文件名） |
| 每个随包资产是否有许可+来源 | **是**，8/8 均由 `LICENSE-DP5`（MIT）+ `NOTICE.md`（来源 revision/引用）覆盖，且两文件本身在 wheel 内随包 |
| 自有代码许可 vs wheel 内容 | wheel 178 entries 全部 `cccp/`（零 `acp*`），全部落在根 `LICENSE` MIT 覆盖范围内 |
| 外部软件 vs wheel 内容 | wheel 不含 ORCA/xTB/CREST/CENSO/ISOSTAT/Shermo/Molclus 任何二进制，与 §3 "外部提供、不分发"声明一致 |

**failure 模式核验**：未发现无许可资产 → **无 blocked 项**，无需向 T39 manifest 修正清单追加任何条目。唯一登记缺口 `dp5q_stub_params.json`（不存在，T2/T4 已登记为 gap）不构成许可缺失。

## 5. 结论

三节齐全（自有代码 MIT / 模型资产 MIT 随包含 notice / 外部软件六项+附带 molclus 全部"外部提供"）；每项含许可证与来源或显式"外部提供"标注；与 T40a wheel namelist 交叉核对无漏项。本声明为记录性文档，不修改任何许可文本或打包配置。
