# ACP Services 四组合联合验收记录（F0/S1 × 前端轴线）

日期：2026-10-19。计划：`.omo/plans/acp-parallel-plan-2-services.md` todo 75（B13-7，评审修订 X3）。
范围：在集成分支 `feat/frontend-plan1-closure` 上**组织并执行**四组合（F0＋S0 / F1＋S0 / F0＋S1 / F1＋S1），逐组合声明预期能力、绑定实际提交/契约版本/配置/fixture 摘要，诚实登记 `NOT_VERIFIED` 与 A/C 接收延期。
本文件由 `tests/test_services_four_combination_records.py` 解析守护；机器可读记录见 §8。

> **T75 只负责组合执行与产出**；最终收口记录（三分层）由 T69 消费本文件生成。本文件不等待 T69。

---

## 1. 轴定义与执行基线绑定

| 轴 | 取值 | 本记录中的对应物 |
|---|---|---|
| F0 | 冻结的原始前端（本切片前静态文件集） | 旧客户端流程 = 既有 GET 端点原样调用；仓内以直接调用旧 GET 的测试替身（无真实旧 bundle 可执行） |
| F1 | 后续前端版本（lane A §42 拆分，仓内已落地） | `frontend/js/**` 模块 + 前端 pytest 选择清单（`tests/frontend_test_selection.txt`） |
| S0 | 冻结的服务/CCCP/配置行为基线（未改动服务） | 既有旧 GET 路径（在 S1 中**逐字保留**，S1 改动为加性） |
| S1 | 后续服务版本（Services 切片） | `src/acp_services/**` + 加性 HTTP 面（cache-only / availability / events / prepare） |

**执行基线（execution base HEAD）：`fded1160c4edb0974a6e49b528ee263975fd0075`**（分支 `feat/frontend-plan1-closure`）。
Python `3.11.13`（`/usr/bin/python3.11`）；Node `v20.19.0`。配置：每个套件使用 `mktemp -d` 隔离的 `ACP_RUN_ROOT`；**未重启服务、未改配置、未改 DB**；默认配置来源 `cccp.config._get_default_config()`（built-in 权威，ANTI #3）。

**移动目标披露（并发车道）**：本记录全部套件在 `fded116` 上执行；记录撰写期间 HEAD 因并发 worker 前进到 `16799d18f4a823942a4fd6a9a76e6e109f3e5873`（todo 56 `feat(services-metering): decouple usage collection from science completion/result access`），另有 todo 55 的 `src/acp_services/runtime/quota.py` 未跟踪在途。该并发提交是加性的用量采集路径，不触及本文件四组合的 S0/S1 表面（旧 GET / cache-only / events / frames）。**S0 旧记录不得用作 S1 证据；本记录不拼接不同提交的通过记录。**

| 绑定项 | 值 |
|---|---|
| execution commit | `fded1160c4edb0974a6e49b528ee263975fd0075` |
| observed HEAD（撰写时） | `16799d18f4a823942a4fd6a9a76e6e109f3e5873` |
| First Slice 契约版本 | `contract_version = 1`（`docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`，sha256[0:16]=`e4e7dc4a0f020e38`） |
| A04 事件契约 | sha256[0:16]=`140148c325dd7f42`；**状态 DEFERRED（未接入，见 §6）** |
| cache-only 切换矩阵 | `tests/baseline/services-evidence/cache_only_switch_matrix.md`，sha256[0:16]=`6f97e465929bb7c1` |
| fixture 摘要 contract_samples | 31 文件，sha256[0:16]=`367815d7cb7291bc` |
| fixture 摘要 frames_mapping | 12 文件，sha256[0:16]=`79d229e24101a430` |

---

## 2. 四组合矩阵

| 组合 | 预期能力（本组合由此声明） | 仓内可执行证据 | 状态 |
|---|---|---|---|
| **F0＋S0** 基线 | 旧客户端 + 旧服务基线：旧 GET 任务 CRUD/读取流程可用；**不代表**支持新 prepare 或账户 UI | `tests/test_acp_api_v1.py` + `tests/test_acp_api_structure_viewer.py` = **219 passed** | PASS_IN_REPO（S0 表面；浏览器 NOT_VERIFIED） |
| **F1＋S0** 前端兼容 | 新前端在**服务不改**（S0 默认路径）下工作；**不代表** A04 prepare/事件/账户 UI 已接入 | Node 模块 **306 passed** + 前端选择 **525 passed** + 旧 GET **219 passed** | PASS_IN_REPO（前端与 S0 表面；浏览器 NOT_VERIFIED） |
| **F0＋S1** 旧客户端兼容 | 旧 URL/字段/错误/远端读取行为保留；按资源启用 cache-only 新路径并保留旧 GET 回退；**不代表** F1＋S1，也不证明科学等价 | `tests/test_services_cache_only_switch.py` **32 passed** + 旧 GET **219 passed** | PASS_IN_REPO（浏览器 NOT_VERIFIED；科学等价 NOT_VERIFIED） |
| **F1＋S1** 集成行为 | 新前端 + 新 Services 服务在**已交付接口**上集成：cache-only / availability / events / CLI 持久控制；**A04 未启用** → 交付的是前端兼容重构而非新刷新协议；不代表科学等价或 Lab 账户 UI | Services 集成 **89 passed** + CLI offline **25 passed** + Node **306 passed** + 前端选择 **525 passed** | PASS_IN_REPO（浏览器与科学等价 NOT_VERIFIED） |

**逐项预期能力声明（评审 X3 硬性条款）**：
- **F0＋S0 基线通过不代表支持新 prepare 或账户 UI。** 旧 GET 通过只证明基线读取流程未回归，不声明任何 S1 新能力。
- **Lab 功能启用的组合需等 todo 73 身份交付。** todo 73（identity lifecycle）**已交付**（提交 `014234c1a403eb885d8d02435a9013096b112cd1`），但其**组件未接入 HTTP/UI**（该任务自述假设）。因此 Lab 账户 UI 组合 = `NOT_VERIFIED`（channel: B，理由见 §5）。
- **首批本地组合不被未选 Lab 能力阻塞。** 本地四组合的仓内证据不依赖未选用的 Lab/OIDC 能力；OIDC 恒为 `NOT_VERIFIED`（todo 73 `OIDC_SUPPORTED = False`）。

---

## 3. 逐组合执行记录（命令 + 结果 + 证据绑定）

所有命令在 `fded116` 上执行；每个套件以隔离 `ACP_RUN_ROOT` 运行，未重启服务。

### 3.1 F0＋S0（基线）
```
$ python3.11 -m pytest tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py -q
219 passed, 2 warnings in 216.93s
```
旧 GET 路径在 S1 中为**加性保留**（`src/acp/api/v1_routes.py` 的 S1 改动为 +120/0，见 cache-only 切换矩阵 §2），故本套件执行的是 S0 服务表面。F0 客户端以直接调用旧 GET 的测试替身代表。
**历史基线引用（非本提交通过记录）**：`.omo/evidence/acp-parallel-plan-2-services/b00-baseline.md`（todo 1，冻结于 `24ffca5a4eda31a3dcdf417fb7c69f11fb0dbc33`）——仅作基线对照引用，**不拼接为本提交的通过证据**。

### 3.2 F1＋S0（前端兼容）
```
$ node frontend/js/module_tests/run_all.js
306 passed, 0 failed

$ python3.11 -m pytest $(cat tests/frontend_test_selection.txt | tr '\n' ' ') -q
525 passed, 2 warnings in 17.95s

$ python3.11 -m pytest tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py -q
219 passed, 2 warnings in 216.93s
```
新前端模块在 S0 默认路径语义下的兼容性证据；A04 相关刷新/事件/账户路径未接入（见 §6）。

### 3.3 F0＋S1（旧客户端兼容）
```
$ python3.11 -m pytest tests/test_services_cache_only_switch.py -q
32 passed, 2 warnings in 6.54s

$ python3.11 -m pytest tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py -q
219 passed, 2 warnings in 216.93s
```
`test_services_cache_only_switch.py` 含 F0 对拍（冷缓存下真实 entries/nodes/候选/modes/XYZ，非仅状态码）、逐资源回退演练与"未对拍直接切换"负例（证明对拍有意义）。

### 3.4 F1＋S1（集成行为）
```
$ python3.11 -m pytest tests/test_services_read_root.py tests/test_services_events_authz.py \
    tests/test_services_data_port.py tests/test_services_first_slice_contracts.py \
    tests/test_services_cli_full_loop.py -q
89 passed, 2 warnings in 29.53s
   （read_root 17 + events_authz 19 + data_port 11 + first_slice_contracts 14 + cli_full_loop 28）

$ python3.11 -m pytest tests/test_services_cli_offline_loop.py -q
25 passed in 5.83s

$ node frontend/js/module_tests/run_all.js
306 passed, 0 failed

$ python3.11 -m pytest $(cat tests/frontend_test_selection.txt | tr '\n' ' ') -q
525 passed, 2 warnings in 17.95s
```
集成面证据为模块/API 级（TestClient + 真实 CLI 入口 + 真实 store/checkpoint/manifest 写入）。**浏览器联合联调与真实科学执行 NOT_VERIFIED**（channel A05 / C10，见 §5）。

---

## 4. 证据绑定规则（强制）

1. **每份证据绑定实际提交/契约版本/配置/fixture 摘要**：本文件 §1 与 §8 的 `binding` 块给出 execution commit、`contract_version=1`、配置（隔离 run_root）、fixture 摘要（contract_samples / frames_mapping / cache-only 矩阵）。
2. **S0 旧记录不能证明 S1**：`references` 中的历史基线（todo 1，`24ffca5c`）仅作对照；S1 组合（F0＋S1 / F1＋S1）各自必须在 execution commit 上拥有 `executed` 记录（由 checker 守护）。
3. **不拼接不同提交的通过记录**：所有 `executed` 记录的 `commit` 必须恰为 execution commit；历史提交只能出现在 `references` 且必须带非空 `note` 说明其"仅引用"性质。
4. **不以单组合代替四组合**：四组合必须同时在场且相互独立（无 `covered_by`）。
5. **mock 通过只记模块完成**：本记录的仓内 PASS 一律标注为 PASS_IN_REPO（模块/API 级），不得外推为浏览器/真实科学/生产通过。

---

## 5. A/C 接收任务与延期范围登记

| 路 | 任务 | 交付物 | 延期范围 | 状态 |
|---|---|---|---|---|
| A | **A05**（当前前端联合验收与静态发布包；lane A 已交付物完成，退出门 OPEN） | 在**同一集成候选**上执行浏览器场景：列表→详情→文件/日志→结果、分页/筛选、A→B→A、rerun、编辑不新建、候选冲突、缺库、多实例、导出、隐藏/重挂载、中英、断网恢复；并提供黑盒测试供 B 执行 | 仅浏览器真机；B **不取得 A 源码写权**，只执行 A 提供的黑盒测试；A05 现有证据绑定其自身提交，**不得借为当前 S1 提交的通过记录** | REGISTERED（本组合浏览器腿 `NOT_VERIFIED`） |
| C | **C10**（科学/架构验收、清理与证据交付） | 在**同一集成候选**上以固定输入/参数/软件/算法版本判定旧/新科学输出等价（黄金容差 + 恢复 fixtures），并交付源码唯一性/数值/隔离证据 | 科学等价与真实软件（ORCA/xTB/CREST/LSF）执行；接口模拟/纯输入生成不得冒充真实运行；ch&#8203;annel `C10` | REGISTERED（科学等价 `NOT_VERIFIED`；HN-02 状态 B pending） |
| B | T69 | 消费本文件的四组合结果生成三分层收口记录 | 组合执行与产出归 T75；T69 不再重复执行四组合 | 下游 |

**科学等价判定引用**：C 侧映射契约 `tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md`（provider commit `06ce0452a714ff1e389e7ed1aedc022e8640cef9`，provider 侧 `delivered`）。其 **状态 B「跨路端到端验收完成」当前为 `pending`**（HN-02 §0/§9.3），故本文件**科学等价 = `NOT_VERIFIED`（channel C10）**，不以跨版本摘要拼作当前证据。

---

## 6. 未验证项登记（NOT_VERIFIED 逐项原因）

| 项 | 影响组合 | 状态 | 原因 | 渠道 |
|---|---|---|---|---|
| 真实 F0 前端 bundle 在浏览器中执行 | F0＋S0 / F0＋S1 | NOT_VERIFIED | 本环境无 Playwright，且无可执行的旧版 F0 bundle；仅能以直接调用旧 GET 的测试替身代表旧客户端流程 | A05 |
| 真实 F1 前端 + 真实 S0 服务浏览器联调 | F1＋S0 | NOT_VERIFIED | 浏览器环境不可用；S0 服务表面以旧 GET 套件代表 | A05 |
| F1＋S1 浏览器联合联调 | F1＋S1 | NOT_VERIFIED | 无浏览器；模块/API 级集成已 PASS，浏览器级未执行 | A05 |
| 科学等价（旧/新同输入输出对照） | F0＋S1 / F1＋S1 | NOT_VERIFIED | C10 判定记录未交付；HN-02 状态 B pending；真实科学软件未运行 | C10 |
| 真实科学执行（ORCA/xTB/CREST/CENSO） | 全部 | NOT_VERIFIED | 二进制不可当场真实运行（config 源可解析⚠但非运行证据） | C10/B |
| 远端 LSF/SSH/SFTP 路径 | 全部 | NOT_VERIFIED | 无网络；未涉及远端执行 | B |
| Lab 账户/会话 UI | F1＋S1 | NOT_VERIFIED | todo 73 identity 组件已交付（`014234c`），但**未接入 HTTP/UI**；OIDC 恒不可用 | B |
| A04 刷新协议接入（cursor/重置/停轮询） | F1＋S1 | NOT_VERIFIED | A04 为 **DEFERRED / 未接入**（plan-1 §3 A04 六条全未勾选）；故本次交付是**前端兼容重构**，非新刷新协议 | A04-lane |
| 生产发布/静态缓存/旧标签页 | F1＋S0 / F1＋S1 | NOT_VERIFIED | 未做生产发布演练 | B |

**A04 交付定性（硬性条款）**：A04 未启用，因此本次交付的是**前端兼容重构**（F1＋S0 保持默认可运行），**不是**新的 prepare/事件刷新协议；相关能力保持开放、未启用。

---

## 7. 结论

- 四组合记录**齐全且可追溯**（提交/契约版本/配置/fixture 摘要逐项绑定）。
- 仓内可执行腿全部 **PASS_IN_REPO**；浏览器、科学等价、真实科学/LSF、生产、Lab UI、A04 接入逐项 **NOT_VERIFIED 并附原因与渠道**。
- 科学等价**引用 C 判定位置并置于 NOT_VERIFIED**（C10 判定记录未交付）。
- A/C 接收任务与延期范围已登记；未验证项不被外推。
- T69 可直接消费本文件（含 §8 机器可读记录）生成收口记录。

---

## 8. 机器可读记录（由 checker 解析守护）

<!-- FOUR_COMBINATION_RECORDS_BEGIN -->
```json
{
  "schema": "acp.services.four_combination_records",
  "schema_version": 1,
  "plan_todo": 75,
  "generated_at": "2026-10-19T06:08:00Z",
  "binding": {
    "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
    "branch": "feat/frontend-plan1-closure",
    "observed_head_after": "16799d18f4a823942a4fd6a9a76e6e109f3e5873",
    "interpreter": "python3.11 (3.11.13)",
    "node": "v20.19.0",
    "contract_versions": {
      "first_slice_contract_version": 1,
      "first_slice_contract_doc_sha256_16": "e4e7dc4a0f020e38",
      "a04_events_contract_sha256_16": "140148c325dd7f42",
      "a04_status": "DEFERRED"
    },
    "config": "isolated ACP_RUN_ROOT per suite (mktemp); no service restart; no config mutation; default _get_default_config()",
    "fixture_digests": {
      "contract_samples": {
        "files": 31,
        "sha256_16": "367815d7cb7291bc"
      },
      "frames_mapping": {
        "files": 12,
        "sha256_16": "79d229e24101a430"
      },
      "cache_only_switch_matrix": {
        "files": 1,
        "sha256_16": "6f97e465929bb7c1"
      }
    }
  },
  "combinations": [
    {
      "id": "F0+S0",
      "label": "old frontend + old server baseline",
      "status": "PASS_IN_REPO",
      "expected_capabilities": [
        "old-client task CRUD/read flows via preserved old GET endpoints",
        "baseline passing does NOT imply new prepare or account UI support"
      ],
      "executed": [
        {
          "command": "python3.11 -m pytest tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py -q",
          "result": "219 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "S0 old-GET surface, byte-preserved additive in S1"
        }
      ],
      "references": [
        {
          "path": ".omo/evidence/acp-parallel-plan-2-services/b00-baseline.md",
          "commit": "24ffca5a4eda31a3dcdf417fb7c69f11fb0dbc33",
          "note": "frozen todo-1 baseline; referenced for comparison only, not spliced into this commit's pass"
        }
      ],
      "not_verified": [
        {
          "item": "real F0 bundle executed in a browser",
          "reason": "no Playwright and no executable old F0 bundle; old-client flow represented by direct old-GET callers",
          "channel": "A05"
        }
      ]
    },
    {
      "id": "F1+S0",
      "label": "new frontend + old server frontend-compatibility",
      "status": "PASS_IN_REPO",
      "expected_capabilities": [
        "F1 frontend modules work against S0 default paths",
        "does NOT imply A04 prepare/events/account UI are wired in"
      ],
      "executed": [
        {
          "command": "node frontend/js/module_tests/run_all.js",
          "result": "306 passed, 0 failed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "F1 frontend module behaviour"
        },
        {
          "command": "python3.11 -m pytest $(cat tests/frontend_test_selection.txt | tr '\\n' ' ') -q",
          "result": "525 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "F1 frontend static/sync selection"
        },
        {
          "command": "python3.11 -m pytest tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py -q",
          "result": "219 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "S0 old-GET surface"
        }
      ],
      "references": [],
      "not_verified": [
        {
          "item": "real F1 frontend + real S0 server browser joint run",
          "reason": "browser environment unavailable; S0 surface represented by old-GET suite",
          "channel": "A05"
        },
        {
          "item": "production static release / old-tab window",
          "reason": "no production release drill performed",
          "channel": "B"
        }
      ]
    },
    {
      "id": "F0+S1",
      "label": "old client + new Services server legacy-client compatibility",
      "status": "PASS_IN_REPO",
      "expected_capabilities": [
        "old URLs/fields/errors/remote-read behaviour preserved",
        "per-resource cache-only new path enabled with old GET fallback retained",
        "does NOT prove F1+S1 and does NOT prove scientific equivalence"
      ],
      "executed": [
        {
          "command": "python3.11 -m pytest tests/test_services_cache_only_switch.py -q",
          "result": "32 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "F0 对拍 + per-resource fallback drill + negative control"
        },
        {
          "command": "python3.11 -m pytest tests/test_acp_api_v1.py tests/test_acp_api_structure_viewer.py -q",
          "result": "219 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "old GET fallback surface"
        }
      ],
      "references": [],
      "not_verified": [
        {
          "item": "scientific equivalence of new products",
          "reason": "C10 judgement not delivered; HN-02 state B pending; real science not run",
          "channel": "C10"
        },
        {
          "item": "browser-level F0 client",
          "reason": "no browser / no F0 bundle",
          "channel": "A05"
        }
      ]
    },
    {
      "id": "F1+S1",
      "label": "new frontend + new Services server integrated behaviour",
      "status": "PASS_IN_REPO",
      "expected_capabilities": [
        "F1 frontend + S1 Services integrate on delivered interfaces (cache-only/availability/events/CLI)",
        "A04 not enabled: delivery is the frontend-compatibility refactor, not a new refresh protocol",
        "does NOT imply scientific equivalence or Lab account UI"
      ],
      "executed": [
        {
          "command": "python3.11 -m pytest tests/test_services_read_root.py tests/test_services_events_authz.py tests/test_services_data_port.py tests/test_services_first_slice_contracts.py tests/test_services_cli_full_loop.py -q",
          "result": "89 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "S1 services module/API integration"
        },
        {
          "command": "python3.11 -m pytest tests/test_services_cli_offline_loop.py -q",
          "result": "25 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "persistent controller/worker CLI loop"
        },
        {
          "command": "node frontend/js/module_tests/run_all.js",
          "result": "306 passed, 0 failed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "F1 frontend module behaviour"
        },
        {
          "command": "python3.11 -m pytest $(cat tests/frontend_test_selection.txt | tr '\\n' ' ') -q",
          "result": "525 passed",
          "commit": "fded1160c4edb0974a6e49b528ee263975fd0075",
          "scope": "F1 frontend static/sync selection"
        }
      ],
      "references": [],
      "not_verified": [
        {
          "item": "F1+S1 browser joint integration",
          "reason": "no browser; module/API integration passed, browser level not executed",
          "channel": "A05"
        },
        {
          "item": "scientific equivalence",
          "reason": "C10 judgement not delivered; HN-02 state B pending",
          "channel": "C10"
        },
        {
          "item": "Lab account/session UI",
          "reason": "todo 73 identity delivered (014234c) but not wired into HTTP/UI; OIDC unavailable",
          "channel": "B"
        },
        {
          "item": "A04 refresh protocol consumption",
          "reason": "A04 DEFERRED/not integrated; delivery is frontend-compatibility refactor",
          "channel": "A04-lane"
        }
      ]
    }
  ],
  "scientific_equivalence": {
    "status": "NOT_VERIFIED",
    "channel": "C10",
    "reference": "tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md",
    "reference_commit": "06ce0452a714ff1e389e7ed1aedc022e8640cef9",
    "reason": "C10 judgement record not delivered; HN-02 cross-lane state B pending; real science not executed on this candidate"
  },
  "lane_receipts": [
    {
      "lane": "A",
      "task": "A05",
      "deliverable": "browser scenarios on the same integrated candidate + black-box tests for B to execute",
      "deferral_scope": "browser-only; B never gains A source write access; existing A05 evidence stays bound to its own commit",
      "status": "REGISTERED"
    },
    {
      "lane": "C",
      "task": "C10",
      "deliverable": "scientific equivalence judgement on the same integrated candidate (fixed input + golden evidence)",
      "deferral_scope": "scientific equivalence and real software execution; mock/fixture-only not accepted",
      "status": "REGISTERED"
    }
  ],
  "deferrals": [
    {
      "item": "A04 refresh protocol",
      "status": "DEFERRED",
      "note": "not enabled; the delivered artifact is the frontend-compatibility refactor"
    },
    {
      "item": "todo 73 Lab identity HTTP/UI wiring",
      "status": "NOT_VERIFIED",
      "note": "component delivered 014234c; not wired into HTTP/UI and OIDC unavailable"
    }
  ]
}
```
<!-- FOUR_COMBINATION_RECORDS_END -->
