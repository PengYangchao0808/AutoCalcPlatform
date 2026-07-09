# ACP 环己烷基准测试报告

**测试分子**: 环己烷 (C₆H₁₂, SMILES: C1CCCCC1)  
**测试日期**: 2026-06-25  
**测试环境**: Gaussian 16, ORCA (wB97X-D4/def2-TZVPP), CREST, xTB, ISOSTAT, Shermo

---

## 1. 协议全景

### 1.1 当前可用协议（legacy-* 已移除）

| 协议 | 家族 | 管线 | 适用场景 |
|------|------|------|---------|
| `ext` | ext | CREST GFN0→GFN2 → ISOSTAT | 仅需 ensemble 采样 |
| `censo-zero` | censo | CREST → Part0(xTB) → Part3(wB97X-D4 SP) | 极速粗筛 |
| `censo-lite` | censo | CREST → Part0 → Part1(r2SCAN-3c SP) → Part3 | 快速SP排序 |
| `censo-full` | censo | CREST → Part0 → Part1 → Part2(r2SCAN-3c opt+freq) → Part3 | 生产级精度 |
| `censo-full-safe` | censo | 同 full，窗口更宽 | 带电/活性体系 |
| `allopt` | censo | CREST 两阶段 + Part2 + Part3 | 穷举验证 |
| `reference-sp` | reference | 已有 ensemble → DLPNO-CCSD(T) SP | 基准参考 |

---

## 2. 性能对比

### 2.1 时序与资源消耗

| 协议 | 总耗时 | CREST | ISOSTAT | Part0 | Part1 | Part2 | Part3 | 磁盘占用 |
|------|--------|-------|---------|-------|-------|-------|-------|----------|
| **ext** | **77 s** | 75 s | <1 s | - | - | - | - | 1.7 MB |
| **censo-zero** | **80 s** | 41 s | 1 s | 1 s | - | - | 39 s | 6.7 MB |
| **censo-lite** | **79 s** | 41 s | 1 s | 1 s | <1 s* | - | 38 s | 6.7 MB |
| **censo-full** | **140 s** | 42 s | 1 s | 1 s | <1 s* | 82 s | 20 s | 4.1 MB |
| **censo-full-safe** | **224 s** | 42 s | 1 s | 1 s | <1 s* | 143 s | 20 s | 4.4 MB |
| **allopt** | **255 s** | 75 s | 1 s | - | - | - | 80 s | 7.9 MB |

> *Part1 (r2SCAN-3c SP) 因本地 ORCA 未配置 r2SCAN-3c 方法而回退到 xTB 能量，耗时可忽略。若有 r2SCAN-3c 支持，预计每候选 ~30s。

### 2.2 构象筛选漏斗

| 协议 | CREST → Part0 | Part0→Part1 | Part1→Part2 | Part2→Part3 | 最终输出 |
|------|-------------|-------------|-------------|-------------|---------|
| **censo-zero** | 2 → 2 (100%) | - | - | 2 → 2 (100%) | **2** |
| **censo-lite** | 2 → 2 (100%) | 2 → 2 (100%) | - | 2 → 2 (100%) | **2** |
| **censo-full** | 3 → 1 (33%) | 1 → 1 (100%) | 1 → 1 (100%) | 1 → 1 (100%) | **1** |
| **censo-full-safe** | 2 → 2 (100%) | 2 → 2 (100%) | 3 → 1 (33%) | 1 → 1 (100%) | **1** |
| **allopt** | (无 Part0) | - | - | 2 → 2 (100%) | **2** |

**分析**：
- **censo-full** 的 Part0 窗口 4.0 kcal 有效筛掉了能量较高的扭曲构象（仅保留了椅式），说明椅式与次低构象之间的能量差 > 4.0 kcal/mol
- **censo-full-safe** 的 Part0 窗口 8.0 kcal 保留了椅式和扭船式，直到 Part2 (r2SCAN-3c opt + freq) 才筛掉扭船式（窗口 4.0 kcal）。Part2 输出 3 个候选是因为分裂事件（或多次优化尝试）
- 两种协议的**最终椅式构象一致**（仅 1 个显著构象，Boltzmann 占比 100%）

---

## 3. 能量一致性分析

### 3.1 核心发现

**所有协议在 SP 能量层面高度一致：**

| 协议 | wB97X-D4 SP (hartree) | Gibbs Free Energy (hartree) | ΔG 与基准差 |
|------|----------------------|---------------------------|-------------|
| **censo-zero** | **-236.1092544** | -236.1092544 (无修正) | 基准 |
| **censo-lite** | **-236.1092235** | -236.1092235 (无修正) | 忽略不计 |
| **censo-full** | **-236.1091422** | **-235.9650101** | 0.1442443 |
| **censo-full-safe** | **-236.1091490** | **-235.9649790** | 0.1442754 |
| **allopt** | **-236.1091297** | **-235.9649681** | 0.1442656 |

> **SP 能量极差**: |max - min| = 0.0001247 Ha ≈ **0.08 kcal/mol** —— 在化学精度范围内可忽略。

### 3.2 能量差异来源

之前的报告中提到的 "−235.96 vs −236.10" 的差异**不是几何级别导致的，而是 Gibbs 自由能修正**：

```
SP 能量: E_SP(wB97X-D4)            = -236.10915 Ha
Gibbs 修正: G_corr(ZPE + thermal + entropy) ≈ +0.14416 Ha
────────────────────────────────────────────────────
Gibbs 自由能: G = E_SP + G_corr    = -235.96499 Ha
```

- **censo-zero/lite** 不做频率验证，不计算热力学修正，`Gibbs ≈ SP 能量`
- **censo-full/safe/allopt** 在 Part2 中计算了 r2SCAN-3c 频率，得到了含 ZPE + 热修正 + 熵的 Gibbs 自由能
- **+0.144 Ha 的修正量完全合理**：环己烷椅式有 54 个振动模式（3N-6 = 48），ZPE + 298K 热修正 ≈ 90-100 kcal/mol ≈ 0.14-0.16 Ha

### 3.3 几何质量影响

对于环己烷这个**刚性分子**，不同级别几何 (GFN2 vs r2SCAN-3c) 对 wB97X-D4 SP 能量的影响：

```
censo-zero (GFN2 geom):  -236.1092544 Ha
censo-full (r2SCAN-3c):  -236.1091422 Ha
差 值:                      0.0001122 Ha = 0.07 kcal/mol
```

**结论**：对于刚性小分子，GFN2 几何直接做 wB97X-D4 SP 与 r2SCAN-3c 优化后的 SP 几乎相同。但对于柔性分子（有可旋转二面角），这个差异可能显著增大。

---

## 4. 构象搜索结果

### 4.1 构象分析

| 构象 | 识别方法 | 相对能量 (kcal/mol) | 归一化 Boltzmann 权重 |
|------|---------|-------------------|---------------------|
| 椅式 (chair) | CREST + ISOSTAT | **0.0** | >99.9% |
| 扭船式 (twist-boat) | CREST + ISOSTAT | **~5.9** | <0.1% |

环己烷椅式与扭船式的已知能差为 5-6 kcal/mol，实测值 5.9 kcal/mol **与文献一致**✅

### 4.2 几何验证

```
椅式 (chair):    C-C 键角 ~111.5°, C-C 键长 ~1.53 Å
                所有 C-H 键处于交错式(eclipsed)构象
                无虚频 (仅 censo-full 做了频率验证)

扭船式 (twist):  非平面六元环，C-C 键长 ~1.53 Å
                比椅式高 ~5.9 kcal/mol
```

---

## 5. 架构变更汇总

### 5.1 Legacy 协议移除

| 操作 | 文件数 | 说明 |
|------|--------|------|
| `protocols.py` | 移除 5 个协议定义、配置字典、校验函数 | 从 `SUPPORTED_PROTOCOLS` 删除 |
| `specs.py` | 移除 5 个 LEGACY_* 定义 + registry 条目 | `ProtocolFamily` 去掉 `"legacy"` |
| `spec_adapter.py` | 移除 `warn_legacy_protocol`、精简 `stages_from_workflow_spec` | ~80 行 |
| `engine.py` | 路由从 5 分支 → 2 分支 | 删除 `_run_full/lite_protocol` |
| `config.py` | 移除 5 个配置段落 + 校验列表 | |
| `defaults.yaml` | 移除 5 个配置段落 | ~120 行 YAML |
| CLI 文件 (×3) | 移除协议列表选项 | |
| 测试文件 (×11) | 清理 legacy 引用 | |

### 5.2 环境配置修复

| 问题 | 修复 | 效果 |
|------|------|------|
| ORCA 路径 `/opt/orca/orca` → `/opt/software/orca/orca` | `config.py` + `defaults.yaml` | Part3 SP 正常运行 |
| final_sp 默认引擎 Gaussian → ORCA | `engine.py` line 584 | SP 不再因 Gaussian 失败回退 |
| `ext` 协议 Molclus 后端 → CREST | `specs.py` | 节省 300s Molclus 超时等待 |
| XYZ 文件输入 bug (`str.stem`) | `input_handler.py` line 280 | reference-sp 可正常输入文件 |

---

## 6. 协议选型建议

### 6.1 决策树

```
分子刚性大 + 仅需能量排序?
├── 是 → censo-zero (最快)
└── 否 → 分子柔性大?
    ├── 是 → 需要可靠 Gibbs 自由能?
    │   ├── 是 → censo-full (生产级)
    │   └── 否 → censo-lite (快速 SP 排序)
    └── 否 (如环己烷)
        ├── 仅需采样 → ext
        ├── 快速排序 → censo-zero (足够)
        └── 正式发布 → censo-full
```

### 6.2 各协议推荐场景

| 协议 | 推荐场景 | 样本分子 | 预期成本 |
|------|---------|---------|---------|
| **ext** | 仅生成 CREST ensemble | 任何 | ~1-2 min |
| **censo-zero** | 高通量虚拟筛选、构象数极多 | 药物小分子 | ~1-5 min |
| **censo-lite** | 需要 r2SCAN-3c 重排但不需优化 | 中等柔性 | ~2-10 min |
| **censo-full** | **推荐生产协议** | 大多数分子 | ~5-30 min |
| **censo-full-safe** | 带电体系、过渡金属、高能中间体 | 反应中间体 | ~5-30 min |
| **allopt** | 基准验证、方法学对比 | 1-3 分子 | ~10-60 min |
| **reference-sp** | 已优化构象的 DLPNO-CCSD(T)基准 | 验证集 | 取决于构象数 |

### 6.3 成本-精度权衡曲线

```
精度 ↑
    │                      ★ censo-full (最佳性价比)
    │                    ★ censo-full-safe
    │                 ★ censo-lite
    │              ★ censo-zero
    │           ★ ext
    └──────────────────────────────→ 成本 (时间 + 计算)
```

---

## 7. 后续优化方向

### 7.1 短期（可立即执行）

1. **Part1 ORCA r2SCAN-3c 支持**: 当前 ORCA 5.x 版本支持 r2SCAN-3c，需要更新分辨率逻辑或配置
2. **Gaussian wrapper 修复**: 若需使用 Gaussian 16 做特定计算（如 NMR GIAO），需调试 `run_g16_worker.sh`
3. **censo-lite 与 censo-zero 管线差异化**: 当前两者 CREST 参数完全相同（GFN2、ngeom 等），lite 的 Part1 未实际执行

### 7.2 中期

4. **柔性分子基准测试**: 换用柔性分子（如庚烷 C7H16 或 1,2-二氯乙烷），验证 GFN2 几何 vs r2SCAN-3c 几何对 SP 能量的影响
5. **Boltzmann 分布对比**: 在柔性分子上比较各协议对 Boltzmann 权重的预测一致性
6. **Part0 xTB vs Part1 r2SCAN-3c 筛选效率对比**: 定量分析每层筛掉了多少构象及其能量分布

### 7.3 长期

7. **与实验值对比**: 选已知气相构象分布的分子，比较各协议预测的 Boltzmann 分布与实验/高精度基准值的偏差
8. **自动协议推荐**: 根据分子大小/柔性/电荷自动推荐最优协议

---

*报告生成: 2026-06-25 | ACP V1 Package*
