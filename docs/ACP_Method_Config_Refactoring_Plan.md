# ACP 计算方法配置重构方案 (rev.2)

**日期:** 2026-06-28
**状态:** 设计方案，待实施
**影响范围:** src/acp/catalog.py, src/acp/api/v1_schemas.py, src/acp/api/v1_routes.py,
  frontend/ACP_Workbench_v2.html, src/acp/scheduler/jobs.py

---

## 0. 编码规范

- 所有源码文件 (.py, .html) 必须为 UTF-8 编码。
- catalog.py 中的 label_zh、summary 等中文内容须与前端 i18n 字典使用相同编码。
- 提交前做轻量检查: python3 -c "open('src/acp/catalog.py').read().encode('utf-8')"

---

## 1. 核心架构

```
工作流配置 (Workflow Config)       方法配置 (Method Config)
决定"做什么"                       决定"怎么算"
- Single Point Energy              - allowed_engines (允许哪些 backend)
- Geometry Optimization            - per-level 字段 (functional/basis/dispersion/solvent...)
- Frequency / Opt+Freq             - engine 联动: 选 Gaussian 换 functional/basis 列表
- Confsearch + NMR + Benchmark     - 跨 level 一致性检查
- Mechanism / Custom Sequence      - 缺省值补齐
```

---

## 2. 数据结构

### 2.1 命名规范

| 上下文 | 字段名 | 含义 |
|--------|--------|------|
| schema | allowed_engines | 该 level 允许的 engine 列表 |
| profile / payload | engine | 用户选中的 engine |
| frontend state | selected_engine | 同 payload |

### 2.2 Schema 示例

```python
{
  "schema_id": "confsearch",
  "method_levels": [
    {
      "level_id": "preopt",
      "label": "xTB Pre-optimization",
      "required": True,
      "allowed_engines": ["xtb"],
      "fields": {
        "gfn": {
          "type": "select",
          "per_backend": {"xtb": ["GFN0-xTB", "GFN1-xTB", "GFN2-xTB"]},
          "default": {"xtb": "GFN2-xTB"}
        },
        "solvent_model": {
          "type": "select",
          "per_backend": {"xtb": ["none", "ALPB", "GBSA"]},
          "default": {"xtb": "none"}
        }
      }
    },
    {
      "level_id": "dft_opt",
      "label": "DFT Optimization",
      "required": True,
      "allowed_engines": ["gaussian", "orca"],
      "fields": {
        "functional": {
          "type": "select",
          "per_backend": {
            "gaussian": ["B3LYP", "PBE0", "M06-2X", "wB97X-D", "wB97X-D4", "r2SCAN-3c"],
            "orca": ["B3LYP", "PBE0", "wB97X-D4", "r2SCAN-3c"]
          },
          "default": {"gaussian": "wB97X-D", "orca": "B3LYP"}
        },
        "basis": {
          "type": "select",
          "per_backend": {
            "gaussian": ["def2-SVP", "def2-TZVP", "def2-TZVPP", "def2-TZVPPD"],
            "orca": ["def2-SVP", "def2-TZVP", "def2-TZVPP", "def2-TZVPPD"]
          },
          "default": {"gaussian": "def2-SVP", "orca": "def2-SVP"}
        },
        "dispersion": {
          "type": "select",
          "options": ["none", "D3", "D3BJ", "D4"],
          "default": {"*": "D3BJ"}
        },
        "solvent_model": {
          "type": "select",
          "per_backend": {
            "gaussian": ["none", "CPCM", "SMD"],
            "orca": ["none", "CPCM", "COSMO"]
          },
          "default": {"*": "none"}
        },
        "solvent": {
          "type": "select",
          "options": ["none", "water", "methanol", "ethanol", "chloroform", "THF", "DMSO", "toluene"],
          "default": {"*": "none"},
          "depends_on": {"field": "solvent_model", "not_values": ["none"]}
        },
        "grid": {
          "type": "select",
          "options": ["SG1", "Fine", "UltraFine", "SuperFine"],
          "default": {"*": "UltraFine"}
        },
        "scf_convergence": {
          "type": "select",
          "options": ["Normal", "Tight", "VeryTight"],
          "default": {"*": "Tight"}
        }
      }
    },
    {
      "level_id": "single_point",
      "label": "Single Point Energy",
      "required": True,
      "allowed_engines": ["gaussian", "orca"],
      "fields": {
        "functional": {
          "type": "select",
          "per_backend": {
            "gaussian": ["B3LYP", "PBE0", "M06-2X", "wB97X-D", "wB97X-D4", "r2SCAN-3c"],
            "orca": ["B3LYP", "PBE0", "wB97X-D4", "r2SCAN-3c", "DLPNO-CCSD(T)"]
          },
          "default": {"gaussian": "wB97X-D4", "orca": "DLPNO-CCSD(T)"}
        },
        "basis": {
          "type": "select",
          "per_backend": {
            "gaussian": ["def2-SVP", "def2-TZVP", "def2-TZVPP", "def2-TZVPPD"],
            "orca": ["def2-SVP", "def2-TZVP", "def2-TZVPP", "def2-TZVPPD"]
          },
          "default": {"gaussian": "def2-TZVPPD", "orca": "def2-TZVPP"}
        },
        "aux_basis": {
          "type": "select",
          "per_backend": {
            "gaussian": ["", "def2-TZVPP/C", "def2-QZVPP/C"],
            "orca": ["", "def2-TZVPP/C", "cc-pVTZ/C"]
          },
          "default": {"*": ""}
        },
        "dispersion": {"type": "select", "options": ["none", "D3", "D3BJ", "D4"], "default": {"*": "D4"}},
        "ri_approximation": {
          "type": "select",
          "per_backend": {
            "gaussian": ["none", "RIJCOSX"],
            "orca": ["none", "RI", "RIJCOSX", "RIJK"]
          },
          "default": {"*": "none"}
        },
        "solvent_model": {
          "type": "select",
          "per_backend": {"gaussian": ["none", "CPCM", "SMD"], "orca": ["none", "CPCM", "COSMO"]},
          "default": {"*": "none"}
        },
        "solvent": {
          "type": "select",
          "options": ["none", "water", "methanol", "ethanol", "chloroform", "THF", "DMSO", "toluene"],
          "default": {"*": "none"},
          "depends_on": {"field": "solvent_model", "not_values": ["none"]}
        }
      }
    },
    {
      "level_id": "thermo",
      "label": "Thermochemistry",
      "required": False,
      "allowed_engines": ["shermo"],
      "fields": {
        "temperature": {"type": "float", "min": 0, "max": 10000, "default": {"*": 298.15}, "unit": "K"},
        "pressure": {"type": "float", "min": 0, "max": 100000, "default": {"*": 1.0}, "unit": "atm"},
        "scale_factor": {"type": "float", "min": 0, "max": 1.0, "default": {"*": 1.0}}
      }
    }
  ],
  "profiles": [
    {
      "profile_id": "censo-lite",
      "label": "censo-lite",
      "summary": "CREST GFN2 | r2SCAN-3c opt | wB97X-D SP",
      "levels": {
        "preopt": {"engine": "xtb", "gfn": "GFN2-xTB", "solvent_model": "none"},
        "crest": {"engine": "crest", "gfn": "GFN2-xTB", "ewin": 6.0, "rthr": 0.125},
        "dft_opt": {"engine": "gaussian", "functional": "r2SCAN-3c", "basis": "def2-mTZVPP", "dispersion": "none"},
        "single_point": {"engine": "gaussian", "functional": "wB97X-D", "basis": "def2-TZVP", "dispersion": "D3BJ"}
      }
    }
  ]
}
```

---

## 3. 标准化 + 验证函数

```python
def normalize_and_validate_method_config(method: dict, schema: dict) -> tuple[dict, list[str]]:
    """
    返回 (normalized_levels, errors)

    1. schema_id 存在校验
    2. 必需 level 缺失检查
    3. engine 在 allowed_engines 内
    4. 字段在 schema 定义内
    5. select 值在可选列表内
    6. int/float min/max
    7. solvent_model=none => solvent 自动置空
    8. 跨 level engine 一致性警告
    9. 所有缺省值补齐（按 per_backend > * > FIELD_DEFINITIONS 顺序）
    """
```

**缺省值补齐优先级:**
- schema.field.default.engine (如 `{"gaussian": "wB97X-D"}`)
- schema.field.default.* (如 `{"*": "none"}`)
- FIELD_DEFINITIONS[key].default.*
- 若无 -> error

**solvent_model=none 规则:** 当 solvent_model 为 "none" 时，solvent 无论 payload 传什么值，标准化后强制为 ""。

---

## 4. Method.levels 落地驱动计算

```python
def method_levels_to_workflow_config(levels: dict, schema_id: str, workflow: str) -> dict:
    """
    将标准化后的 levels 转换为 work_dir/method_config.json 内容。
    对 confsearch:
      dft_opt -> optimize stage
      single_point -> sp stage
      crest -> crest stage
      preopt -> xtb_preopt stage
      thermo -> thermo stage
    对 simple workflow (P1):
      singlepoint -> 直接生成 Gaussian/ORCA 输入文件参数

    confsearch 示例输出:
    {
      "crest": {"gfn": "gfn2", "ewin": 6.0, "rthr": 0.125},
      "optimize": {"backend": "gaussian", "functional": "wB97X-D", "basis": "def2-SVP",
                   "dispersion": "D3BJ", "solvent": "", "grid": "UltraFine"},
      "sp": {"backend": "gaussian", "functional": "wB97X-D4", "basis": "def2-TZVPPD",
             "dispersion": "D4"},
      "thermo": {"temperature": 298.15, "pressure": 1.0}
    }
    """
```

- 标准化后的 config 写入 `work_dir/method_config.json`
- runner 在 `_run_fake` / `_run_subprocess` 读取此文件
- 对 conformer 工作流，method_config.json 内容映射为 stages
- 对 fake 工作流，仅写入文件做 provenance，不执行计算

**P0 约束:**
- 只实现 confsearch schema 的落地
- simple 工作流保持 planned (UI 展示但不可提交)
- P1 再做 simple workflow backend adapter

---

## 5. 提交 Payload

```json
{
  "workflow": "conformer",
  "input": {"source_type": "smiles", "source": "CCO"},
  "method": {
    "schema_id": "confsearch",
    "profile_id": "censo-lite",
    "levels": {
      "preopt": {"engine": "xtb", "gfn": "GFN2-xTB", "solvent_model": "none"},
      "crest": {"engine": "crest", "gfn": "GFN2-xTB", "ewin": 6.0, "rthr": 0.125},
      "dft_opt": {"engine": "gaussian", "functional": "r2SCAN-3c", "basis": "def2-mTZVPP",
                   "dispersion": "none", "solvent_model": "SMD", "solvent": "chloroform",
                   "grid": "UltraFine", "scf_convergence": "Tight"},
      "single_point": {"engine": "gaussian", "functional": "wB97X-D4", "basis": "def2-TZVPPD",
                        "aux_basis": "def2-TZVPP/C", "dispersion": "D4"}
    }
  }
}
```

Backend 处理流程:
1. `normalize_and_validate_method_config()` -> errors 或 normalized levels
2. `method_levels_to_workflow_config()` -> `work_dir/method_config.json`
3. `record.method` 保存完整 method dict (含 levels)
4. `method.protocol` 向下兼容

---

## 6. 实现优先级

### Phase 1 - P0

| 任务 | 文件 | 产出 |
|------|------|------|
| UTF-8 编码检查 | 所有文件 | 无 mojibake |
| 重构 catalog.py: allowed_engines + per_backend defaults + FIELD_DEFINITIONS | catalog.py | catalog v2 |
| 迁移 confsearch profiles 到新格式 + thermo level | catalog.py | 完整 confsearch schema |
| 新增 normalize_and_validate_method_config() | catalog.py | 验证函数 + 测试 |
| 新增 method_levels_to_workflow_config() | catalog.py | 转换函数 + 测试 |
| 更新 POST /api/v1/jobs 集成验证 | v1_routes.py | job 创建时验证 |
| 新增 POST /api/v1/validate-method | v1_routes.py | 前端实时验证 |
| 更新 submitJobModal() 发送 method.levels | frontend | 完整 payload |
| fake runner 写入 method_config.json | runner.py | 落地文件 |

### Phase 2 - P0

| 任务 | 文件 |
|------|------|
| 方法弹窗改为 level cards + live summary | frontend |
| 模板选择器 | frontend |
| Engine 联动 (per_backend 驱动) | frontend |

### Phase 3 - P1

| 任务 |
|------|
| Simple workflow backend adapter |
| simple 工作流标记 active |
| 模板分类搜索 |
| 用户自定义模板 |

---

## 7. 验收标准 (P0)

| 验收项 | 方法 |
|--------|------|
| catalog.py 无 mojibake | python3 check |
| confsearch schema 含 5 levels (preopt/crest/dft_opt/single_point/thermo) | GET /method-catalog |
| 每个 field 有 per_backend / options / default | catalog schema |
| validate_method_config 正确返回 errors | 单元测试 |
| normalize_method_config 补齐缺省值 | 单元测试 |
| solvent_model=none 时 solvent 自动 = "" | 单元测试 |
| 提交 job 时 method.levels 完整保存 | GET /api/v1/jobs/{id} |
| simple 工作流在 UI 标记 planned | 前端 |

---

## 8. 风险

| 风险 | 缓解 |
|------|------|
| 旧 job 无 method.levels | 回退读 method.protocol |
| Engine 联动复杂 | per_backend 驱动，不写死 switch |
| simple 工作流 P0 不可提交 | 用 status=planned 禁用 |
| 字段膨胀 | 默认显示核心字段，折叠 advanced |
