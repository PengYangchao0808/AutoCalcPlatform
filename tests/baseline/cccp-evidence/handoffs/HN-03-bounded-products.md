# HN-03 — 有界大数据产品与块索引交接（供 B Data 字节/元数据查询 + A 有限产品消费）

| 字段 | 值 |
|---|---|
| HN 编号 | `HN-03`（C 侧 `tests/baseline/cccp-evidence/handoff_registry.md` §2 索引行） |
| owner / provider / consumer | C 路科学负责人 / C / **B（Data 字节/元数据查询，B02/B03 面）+ A（有限产品消费）** |
| tracked 位置 | `tests/baseline/cccp-evidence/handoffs/HN-03-bounded-products.md`（本文件）+ `tests/fixtures/science_products/bounded/`（6 fixture 组，32 文件）+ `tests/baseline/cccp-evidence/handoffs/walks/`（生成器、walk 脚本、raw 执行输出） |
| 计划来源 | `.omo/plans/acp-parallel-plan-3-cccp.md` todo 33（C06-s3）；closes GAP-7、GAP-12 |
| 形状来源（已冻结） | T32 `src/cccp/analysis/bounded/*`（commit `260d032`）：`BOUNDED_SCHEMA_VERSION=1`（`identity.py:43`）、`METHOD_CHUNK/STRIDE/MULTIRESOLUTION`、`GENERATOR_*`；T11/T12 C02 产品契约 `PRODUCT_SCHEMA_VERSION=1`（`products/contracts.py:109`） |
| 性能/样本前置 | T31 `c06-sample-baseline.md`（Commit N，evidence 在 `.omo/evidence/acp-parallel-plan-3-cccp/`）+ T32 `c06-bounded-baseline.md`（生成/读取两阶段实测） |
| provider 基线快照 | `6a67635`（fresh 快照 2026-10-19；dirty worktree 中 `src/acp/scheduler/*`、`src/acp_services/*`、`docs/**` 在途改动均属 Plan-2 Services / joint 评审，无一条属本任务；本任务**零 `src/**` 改动**） |
| status | `delivered`（provider 侧；B/A 消费契约测试通过前不得记 `integrated` ——「样例是合法 JSON」不算完成，评审 X2） |
| applied_commit（provider 交付提交） | 见 `handoff_registry.md` HN-03 行回填（交付提交 + 登记提交，双提交同消息惯例同 HN-06） |
| 本件不做 | 不改任何产品/任务代码；不让 Data 做科学判据；不承诺未测的分块尺寸调优；不把块索引当科学结果 |

---

## 0. 范围与冻结声明（B/A 必读）

1. **交付 = 块索引 schema + 版本化交付字段 + 4 态 fixture（normal/empty/partial/corrupt）+ 逐 fixture 已执行消费 walk + 数据预算/有限消费说明**。
   产物由 T32 冻结实现生成（真实生产入口 `chunk_curve_product`/`chunk_trajectory_product` → 每块经 C02 生成函数），本任务**未触碰 `src/**`**（`git status` 见 §9）。
2. **块索引是字节/元数据投影，不是科学结果**（计划 Must-NOT）。索引里只有：字节数/sha256、块范围与计数、契约状态（state/complete）、身份摘要（digest）、算法参数（method/resolution/range）。
   **索引中没有任何科学数值可被解释为判据**；科学内容只在块产品载荷里，且在生成时已由 C 完成（抽样关键点保留、缺失标注等）。
3. **不承诺未测的分块尺寸调优**：本交付出现的 `chunk_size` 只有 T32 实测过的值（energy 4096 / traj 500；fixture 内为小样本 16/5/4 以保证交付体积有界）。
   其它分块尺寸的性能**未测**，见 §3.3 与 §8。
4. **消费契约 ≠ 合法 JSON（评审 X2）**：每个 fixture 都有「读 index → 经块索引读块 → C02 reader 解码 → 重构/映射/类型化拒绝」的完整 walk，且结果与 `expected.json` + `index.json.expected_consumption`（版本化期望消费结果）逐字段比对。6/6 fixture walk 已执行，raw 输出入 `walks/raw/` 并在 §6/§7 全文记录。
5. **状态语义**：`delivered` ≠ `integrated`。C 单方提交只能记 `delivered`；B/A 跑通同一 fixture 组的消费契约测试后由消费方回填 `integrated` 与 consumer 提交（权威表 `tests/baseline/services-evidence/handoff_registry.md` 语义）。

---

## 1. 版本化交付字段（version / providing commit / sample digests / expected consumption results）

### 1.1 版本轴（version）

| 字段 | 值 | 定义处 |
|---|---|---|
| `delivery_version` | `hn-03-bounded-delivery-v1` | 本 HN；fixture 布局 + 索引 schema 的交付版本 |
| `index_schema` / `index_schema_version` | `hn03_block_index` / `1` | 本 HN §2；`index.json` 顶层 |
| `bounded_schema_version` | `1`（`BOUNDED_SCHEMA_VERSION`） | `src/cccp/analysis/bounded/identity.py:43`；入派生身份 `params_digest` |
| `product_schema_version` | `1`（`PRODUCT_SCHEMA_VERSION`） | `src/cccp/calculation/products/contracts.py:109`；manifest/块信封 |
| 算法版本 `algorithm_version` | `"1"`（chunk/stride/multiresolution 同） | `bounded/identity.py`（`BoundedSpec` 默认）+ `chunking.py:45` |
| 规范 JSON 摘要约定 | `sha256`（hex）over canonical JSON（`sort_keys=True, separators=(",",":")`） | `compute_input_digest`（C02）与本交付 `expected_consumption.result_digest` 同约定 |

### 1.2 提供提交（providing commit）

| 内容 | 提供提交 | 说明 |
|---|---|---|
| 有界产品实现（生成/读取/身份/索引形状来源） | `260d032`（`feat(cccp): bounded chunked/subsampled products with provenance identity`，T32） | 消费方按此提交锁定代码形状；**字段形状变更必须先递增 schema 版本并由 C 更新本 HN** |
| 样本与全量基线（T31） | Commit N（evidence-only，`.omo/evidence/acp-parallel-plan-3-cccp/c06-sample-baseline.md`，`.omo/` 被 gitignore） | 样本摘要见 §1.3，可独立复核 |
| 本交付（fixture + walks + 本文件） | provider 交付提交 = `handoff_registry.md` HN-03 行 `applied_commit` 回填 | 双提交同消息（`docs(plan3): c06 bounded-product handoff (tracked)`），同 HN-06 惯例 |

### 1.3 样例摘要（sample digests）

**基线样本（T31 实测规模，逐字节同 T32 测量）：**

| 样本 | 路径（evidence） | 规模 | sha256 |
|---|---|---|---|
| energy S4 | `.omo/evidence/acp-parallel-plan-3-cccp/c06-samples/energy_synth_200000p.json` | 200,000 点 | `a5cf2883f08a925e5f857a7d44e4970cf16089cd126bd57b0db192726d6d0479` |
| traj S3 | `.omo/evidence/acp-parallel-plan-3-cccp/c06-samples/traj_synth_10000f_30a.xyz` | 10,000 帧×30 原子 | `747026d5074fd4d0babede0a2b04ab72a77d820a56e6a899ba5cc0afe9b4564f` |

**本交付 fixture 摘要**（32 文件、125,513 B 全清单 = `walks/raw/generate.out`；重生成逐字节全等 = `walks/raw/regen_determinism.out`）：

| fixture | manifest.json sha256 | index.json sha256 | expected.json sha256 | 块字节 |
|---|---|---|---|---|
| `curve_scatter_normal` | `cc98c56f84ae4a7eb12d15c7906260b292fe23abe056bcadcb8fc18d3dbf33b5` | `6b186ba7aa81b99849d0fcf4f721ff4637629e62750ec1ed2680bf1bf435ed2f` | `c649be5d5d296807a685d901e3c0796112e02b710c7b4bbfb8cddbda0235a8eb` | 11,943 |
| `curve_scatter_empty` | `4156460ef48db6991a846477c3f669da1aab2904dfee390a7e85d70f0ef443b2` | `a2e7fbd111fe5fbf00649bce85be51b19152a58530811f28c762c1324e3f6b15` | `8ec6a3e37607a8993542dfeb3af561529c67f34387dac53a4626d1191efce864` | 0 |
| `curve_scatter_partial` | `33f8f0cc9ead973209b1a84d7e565bd001558a9804b5ab0d0407734cc65a2c40` | `55947ac277d30dbf37fa0cabc7413a4810ed8290f8deff9f8da93fcfd33d3296` | `0752ace08a21850a031d886d1340f37310fd688e005495cb7da5d362bbb4086e` | 5,427 |
| `curve_scatter_corrupt` | `0967206836c5310b0b7d53ea172ff984c458921257a4f126aa515c1980c14914` | `a88c0a95ad403729ddf601996a8d9755639c1833738321578339186da7b2c5b2` | `c36ec6dc08bb644a88fa92565d2e24bcfe474fd06434bb99b68b7882b095174c` | 4,885 |
| `structure_trajectory_normal` | `6b88c78525ecd3c8817c9db9f3637c96a95b0a13c15da44b9d7f9c0d84865f4d` | `d91cce18de869c2dc710a192a4f20192742f356ace7e86e30ff7dec67a508958` | `e56ce620c4753bcfb02a9e5b1518d34da46ddf90b89294c2fb65a80cd5910ab4` | 9,372 |
| `structure_trajectory_partial` | `3a75ad665731415582146d7e4b2851f62194e009b5cb5238f15a1b9f70885582` | `84904cfa429d2bb0d0beff8b91a1c47a55edbe5bc4f0f07366a6fb1b66de80d6` | `1ee9ea1041b5152cb8f324419320194afb8e38dec6d4fea0b654ec8c46ac3277` | 8,878 |

### 1.4 期望消费结果（expected consumption results）

每 fixture 的期望消费结果**一式两处**且必须一致（walk STEP7 断言）：`expected.json`（详细 checks + digest）与 `index.json.expected_consumption`（B 侧只见索引时的最小期望）。digest = 对重构/报告结果的 canonical-JSON sha256。

| fixture | operation | 期望结果 | result_digest |
|---|---|---|---|
| `curve_scatter_normal` | `reconstruct` | 40 点逐点重构（id/x/y/缺失）；3 块 `[0,16) [16,32) [32,40)`；全 complete | `sha256:d23ae446ad2221d94b2f888223f453cc07c1a1b0634378dc4bcb7b3936dbb8c8` |
| `curve_scatter_empty` | `report_empty` | `block_count=0, total_items=0, covered_range=[0,0]`；**不**调用重构（§8 边界） | `sha256:6a0f78d5b3480af0e25e363bbb663652d782badd09d676bdbf615484589a4857` |
| `curve_scatter_partial` | `reconstruct` | 12 点重构；native 3 = `missing_point_values`（x/y 缺席，不补零）；块 0 `complete=false` | `sha256:91dccc3ff99022ce267931f244fe1ac9ecb65d86e29529888b3dfd95a4a6885f` |
| `curve_scatter_corrupt` | `reject` | 3 处类型化拒绝（见 §6.4），**零部分数据被收养** | —（拒绝型消费无结果摘要） |
| `structure_trajectory_normal` | `reconstruct` | 10 帧重构（frame_id/frame_index/坐标）；3 块 `[0,4) [4,8) [8,10)` | `sha256:f1c9eca962b2c30c8503bba202b6884eb8398d1e8372bda9c0e588ee65e46cc7` |
| `structure_trajectory_partial` | `reconstruct` | 10 帧重构；frame 4 = `missing_coordinates`（atoms 空，原位保留）；块 1 `complete=false` | `sha256:76a44344f49073967a665795543cac2652eaa4c4fde830ff536e6e2afd4ca3cf` |

---

## 2. 块索引 schema — `hn03_block_index` v1

### 2.1 布局与角色

每个 fixture 目录（也是 B 侧同构交付的建议布局）：

| 文件 | 角色 | 谁读 |
|---|---|---|
| `index.json` | **块索引** = 字节/元数据投影（schema 见 §2.2/§2.3）。Data 的唯一入口：不打开块载荷即可回答大小/计数/范围/身份/状态问题 | **B（Data）**、A（选块） |
| `manifest.json` | 规范全量文档 = `BoundedProduct.to_dict()`（含内嵌 blocks + key_points + spec + 双身份） | A / 科学消费方（经 `BoundedProduct.from_dict`） |
| `block_XXXXXX.json` | 每块一个**真实 C02 产品**（`product_to_dict` 输出）= **有界读取单元** | A（按块有限消费）、B（字节校验） |
| `expected.json` | 详细期望消费结果（operation/checks/digest 或 expected_rejection） | 消费契约测试（B/A 跑 walk 时比对） |

`manifest.json` 与 `block_*.json` 内容同源（同一 `product_to_dict`），**以 manifest 为准**；index 中每块的 `bytes/sha256` 绑定 block 文件，`manifest.bytes/sha256` 绑定 manifest 文件 —— 任何一层不一致由 walk/QA 拦截（§7）。

### 2.2 顶层字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `index_schema` / `index_schema_version` | str / int | `"hn03_block_index"` / `1` |
| `delivery_version` | str | `"hn-03-bounded-delivery-v1"` |
| `bounded_schema_version` / `product_schema_version` | int | `1` / `1`（= 被索引文档的契约版本） |
| `delivery_state` | `"ok" \| "corrupt"` | `corrupt` = 本组文件按设计不可消费，唯一合法消费 = 类型化拒绝（§6.4） |
| `product_kind` | str | `curve_scatter` / `structure_trajectory` |
| `manifest` | `{file, bytes, sha256}` | 规范文档的字节身份 |
| `bounded_identity` / `base_identity` | ProductIdentity dict | 派生身份（= 输入摘要 + 算法/版本 + 参数摘要）/ 基产品身份；**均不含 job/account**（T32 断言） |
| `spec` | BoundedSpec dict | `method`（chunk/stride/multiresolution）、`algorithm_version`、`resolution`、半开原生范围 `[start,end)`、`params`（series_id/product_kind/block_index 等） |
| `display_subsampled` / `note` | bool / str | 显示抽样标记；抽样/多分辨率产品必带 `DISPLAY_SAMPLING_NOTE`（§4.4） |
| `block_count` / `max_block_size` / `total_items` / `covered_range` | int/int/int/[int,int] | 计数与覆盖（**元数据，非科学量**） |
| `blocks[]` | list | 块条目（§2.3），`block_index` 连续 `0..n-1` |
| `expected_consumption` | `{operation, result_digest?}` 或 `{operation:"reject"}` | §1.4 的索引侧最小期望 |
| `expected_rejection` | `{file: 异常类型路径}`（仅 corrupt） | 逐文件期望拒绝类型 |

### 2.3 块条目字段

| 字段 | 类型 | 语义 |
|---|---|---|
| `block_index` | int | 连续 `0..n-1`（`BoundedProduct.__post_init__` 强制） |
| `start` / `end` | int | 半开**原生**索引范围 `[start,end)`（`0 <= start <= end` 强制） |
| `resolution` | int | 块大小（chunk）/ 步长（stride），`>=1`；与 `spec.resolution` 一致 |
| `item_count` | int | 块产品实际携带的点/帧数；**chunk 模式不变量 `end - start == item_count`**（walk STEP5 断言；stride/多分辨率下不适用，二者由 T32 映射测试守护） |
| `file` | str | 块文件名 `block_%06d.json` |
| `bytes` / `sha256` | int / str | 块文件字节身份（B 下载/缓存校验直接用，B02-4） |
| `product_schema_version` | int | 块信封契约版本（=1） |
| `state` / `complete` | str / bool | 契约状态：`available` + `complete=false` 表示该块含逐项缺失（partial 是**逐块/逐项**状态，不是全局状态） |
| `identity` | dict | 块产品身份子集（generator/algorithm/algorithm_version/params_digest/input_digest）；`generator` = 真实 C02 生成函数，`input_digest` 继承基产品 |

### 2.4 示例（真实样例，`curve_scatter_normal/index.json` 摘录）

```json
{
  "index_schema": "hn03_block_index",
  "index_schema_version": 1,
  "delivery_version": "hn-03-bounded-delivery-v1",
  "bounded_schema_version": 1,
  "product_schema_version": 1,
  "delivery_state": "ok",
  "product_kind": "curve_scatter",
  "manifest": {
    "file": "manifest.json",
    "bytes": 14854,
    "sha256": "sha256:cc98c56f84ae4a7eb12d15c7906260b292fe23abe056bcadcb8fc18d3dbf33b5"
  },
  "display_subsampled": false,
  "note": "",
  "block_count": 3,
  "max_block_size": 16,
  "total_items": 40,
  "covered_range": [0, 40],
  "bounded_identity": {
    "product_kind": "curve_scatter",
    "schema_version": 1,
    "generator": "cccp.analysis.bounded.chunking.chunk_curve_product",
    "input_digest": "sha256:b194fe9da09127ece15108bf96f505ac831e5c347697bfdb1f694daa7d29408c",
    "algorithm": "chunk",
    "algorithm_version": "1",
    "params_digest": "sha256:b6ae6f1805a1f716bee7d7b017839b8bac91cb29fb991f169cbc8225bb5d2262"
  },
  "spec": {
    "method": "chunk",
    "algorithm_version": "1",
    "resolution": 16,
    "start": 0,
    "end": 40,
    "params": [
      {"name": "series_id", "value": "curve_scatter:series:scan_energy"},
      {"name": "product_kind", "value": "curve_scatter"}
    ]
  },
  "expected_consumption": {
    "operation": "reconstruct",
    "result_digest": "sha256:d23ae446ad2221d94b2f888223f453cc07c1a1b0634378dc4bcb7b3936dbb8c8"
  },
  "blocks": [
    {
      "block_index": 0, "start": 0, "end": 16, "resolution": 16, "item_count": 16,
      "file": "block_000000.json", "bytes": 4583,
      "sha256": "sha256:921f14746f5d5c3696312d27d13a9e3b0ee2be791b2327b49b827c9446a67e65",
      "product_schema_version": 1, "state": "available", "complete": true,
      "identity": {
        "generator": "cccp.calculation.products.curve.build_curve_scatter_product",
        "algorithm": "chunk", "algorithm_version": "1",
        "params_digest": "sha256:3314261c1825591884fb92d8371d2cef5d06fb7819fedd07634539d61898192a",
        "input_digest": "sha256:b194fe9da09127ece15108bf96f505ac831e5c347697bfdb1f694daa7d29408c"
      }
    },
    { "block_index": 2, "start": 32, "end": 40, "item_count": 8, "...": "…" }
  ]
}
```

---

## 3. 数据预算（Data 只做字节/元数据查询 —— 无科学判据）

### 3.1 B（Data）允许的查询（全部是字节/元数据）

1. 目录/文件枚举：文件名、`bytes`、`sha256`（对账 `index.manifest` 与 `blocks[].bytes/sha256`；B02-4 下载→摘要/大小/身份校验直接消费这两个字段）。
2. 读取 `index.json` 并回答：块数、最大块项数、总项数、覆盖范围、每块原生范围 `[start,end)`、`resolution`、`state`/`complete` 标志、身份摘要（`input_digest`/`params_digest`/`generator`）、`spec` 参数（method/algorithm_version/range）、`delivery_state`/`schema` 版本、`display_subsampled`/`note` 标记本身。
3. 按 `blocks[].file` 做**单块字节读取/Range 请求**（有界单元，B02-5 只读大文件有界要求）与 sha256 校验。
4. `key_points[]` 的保留台账元数据（`kind`/`native_index`/`retained`/`reason`/`resolution`）——**只作存在性/覆盖元数据**（哪个原生索引被标注为关键点）；其中 `x`/`y` 数值**不得解释**（判据已在生成时由 C 完成并记录）。

### 3.2 B（Data）禁止的行为（科学判据一律不做）

- **禁止**比较/解释科学数值：不按能量大小挑极值/阈值筛选、不判断 TS/鞍点、不解读频率正负、不做单位换算或参考态平移。
- **禁止**任何科学加工：聚类、降维、插值、填补缺失、重排序、把多分辨率层当新数据集重算。
- **禁止**把 `display_subsampled=true` 的产品当全量科学结果消费（§4.4）。
- **禁止**把块索引当科学结果（计数/范围/摘要 = 元数据；`item_count` 不是观测数的科学推断，只是携带项数）。
- 科学结论只存在于：C 生成时的产品载荷 + A 面按契约的有限显示消费；Data 不产生也不裁剪科学内容。

### 3.3 实测预算（只列已测数字）

**本交付（fixture 组，实测）：** 6 目录 / 32 文件 / 125,513 B；单目录 3,150–31,019 B；单块文件 ≤ 4,611 B（normal 块）。

**生产规模（T32 `c06-bounded-baseline.md` §4–§6 同样本实测，勿外推）：**

| 数据 | 分块参数（实测值） | 块数 / 块目录字节 | 读取峰值 RSS（run1/run2） | 生成峰值 RSS |
|---|---|---|---|---|
| energy 10,000 点 | `chunk_size=4096` | 3 / — | 26,260 / 26,260 kB | 40,208 / 40,568 kB |
| energy 50,000 点 | 4096 | 13 / — | 27,092 / 27,092 kB | 48,860 / 48,860 kB |
| energy 200,000 点 | 4096 | 49 / 34 MiB | 27,964 / 27,960 kB | 114,876 / 114,556 kB |
| traj 2,000 帧 | `chunk_size=500` | 4 / — | 36,964 / 37,116 kB | 59,348 / 59,337 kB |
| traj 5,000 帧 | 500 | 10 / — | 37,128 / 37,124 kB | 83,956 / 84,228 kB |
| traj 10,000 帧 | 500 | 20 / 26 MiB | 37,752 / 36,976 kB | 132,800 / 132,480 kB |

- **明确未测（不承诺）**：`chunk_size` ≠ 4096/500 的任何调优收益；>10⁶ 规模；磁盘 I/O 单独分解；抽样/多分辨率逐规模曲线（T32 §8）。
- 已测对照（T32 §6，仅记录不外推）：energy 200k 有界读取 vs T31 全量读取 = wall ×10.6、峰值 RSS −35%；traj 10k = wall ×3.7–5.2、峰值 RSS −70%。块 JSON 自带逐点契约包装（200k 块目录 34 MiB vs 源 4.05 MiB）是「同一科学契约可被 B/A 消费」的显式成本。

### 3.4 与 B02/B03 消费面的对应

| B 侧需求（plan-2） | 本交付提供 |
|---|---|
| B02-4 下载→摘要/大小/身份校验→缓存发布 | `index.manifest` + `blocks[].bytes/sha256` = 期望值；corrupt fixture 演示拒绝语义（类型化错误、不收养部分文件） |
| B02-5 统一读取根 / 只读 / 大文件 Range 有界 | `index.json` 先答元数据；`block_XXXXXX.json` = 有界读取单元（fixture 单块 ≤4.6 KB；生产由 chunk_size 界定） |
| B02-6 兼容对拍 | 本交付不动任何旧读取面；索引是**新增**只读投影 |
| B03 请求预算 / 投影快照 | `block_count`/`max_block_size`/`total_items`/`bytes` 可直接做预算；`delivery_state` 作采纳门（corrupt 不进科学快照） |

---

## 4. A 面有限产品消费说明

### 4.1 单位（units —— 契约字面值，A 原样渲染，不做换算）

| 产品 | 字段 | 本交付 fixture 值 | 契约位置 |
|---|---|---|---|
| curve_scatter | `SeriesRef.x_unit` | `angstrom-or-degree`（扫描坐标） | C02 `SeriesRef`；HN-02a §1 单位表 |
| curve_scatter | `SeriesRef.y_unit` | `hartree`（能量） | 同上；相对能须随 `reference_frame` |
| structure_trajectory | `coordinate_units`（产品级 + 逐帧） | `angstrom`（合法集 `angstrom/bohr/nanometer`） | `CoordinateUnits` |

换算（如 hartree→kcal/mol）**不在 A/B 做**；换算派生产品属于未来的 C 交付（自带单位与身份）。

### 4.2 选择（selection —— 只带稳定 ID + 原生索引）

| 可选对象 | 稳定 ID | 原生索引 | 索引辅助字段 |
|---|---|---|---|
| 曲线点 | `point_id = <series_id>:point:<i>`（`series_id = curve_scatter:series:scan_energy`） | `PointRecord.source.native_index`（chunk 必带；`native_index_of()` 兜底解析 ID 后缀） | 块级：`blocks[].block_index/start/end` |
| 结构帧 | `frame_id = structure_trajectory:frame:<i>` | `frame_index`（分块不重编号） | 同上 |
| 块 | — | `blocks[].start..end`（半开原生范围） | `resolution`/`item_count` |
| 多分辨率层 | 每层 **distinct** `series_id = <base>:res:<n>` | 该层内 `native_index` | `blocks[].resolution`（T32 文档化行为） |

规则：选择载荷只含稳定 ID + 原生索引（HN-02a §2 同款，防陈旧快照）；`native_index` 永远指向**源**编号空间，窗口/分块不重投影重编号。

### 4.3 缺失语义（missing-frame / missing-point；不伪造、不补零）

| 情形 | 契约表达 | 显示/消费行为 |
|---|---|---|
| 缺点 | `PointRecord.missing_reason = missing_point_values`，x/y 缺席 | 图上原位留 gap：不断档前移、不插值、不补 0（fixture `curve_scatter_partial` 实证，§6.3） |
| 缺帧 | `FrameRecord.missing_reason = missing_coordinates`，`atoms` 空 | 帧位保留、`frame_index` 连续，后续帧不前移（fixture `structure_trajectory_partial` 实证，§6.6） |
| 块内 partial | 块产品 `state=available, complete=false` | 按块读取时照常解码；`complete` 是**元数据状态**，A 逐项渲染 reason |
| 全局缺失 | `state=unavailable`（`payload=null`）+ `missing_reason` | 显示「不可用 + reason」占位，不显示空图/零值（HN-02a §3；bounded 层拒绝对其分块，T32 `test_chunk_rejects_bad_arguments_and_products`） |
| 结构损坏 | `ProductSchemaError` | 拒绝整读，**绝不部分渲染**（corrupt fixture 实证，§6.4） |

### 4.4 显示抽样 ≠ 全量科学结果 + 不重算

- `display_subsampled=true` 的产品（stride 抽样、多分辨率）必须携带 `DISPLAY_SAMPLING_NOTE`（双语：`display sampling is not the full scientific result (显示抽样 ≠ 全量科学结果)`）；A 必须原样展示该标记。
- 对此类 manifest，`reconstruct_curve_series`/`reconstruct_structure_frames` **显式拒绝**（`BoundedSchemaError`）——只有 `selected_point_mapping` 的「选中点↔原点映射」语义合法（T32 `test_reconstruct_rejects_subsampled_manifest`）。
- **A 不重算聚类/降维/科学判据**（C06 退出门）：A 只消费有限产品做显示；关键点保留与缺失标注已在生成侧完成（`key_points[]` 台账：`retained=false` 必带 `reason`，无静默丢点）。
- 多分辨率：逐层经**块产品**访问（层内单一 series）；整 manifest 的 `reconstruct_*` 因跨层多 series 而拒绝——这是契约行为，不是数据损坏（T32 `_curve_points` 文档化）。

---

## 5. Fixture 组登记（tests/fixtures/science_products/bounded/）

| fixture | kind | 参数 | 文件（manifest/index/expected + 块） | 目录字节 | 消费 operation |
|---|---|---|---|---|---|
| `curve_scatter_normal` | curve_scatter | 40 点，`chunk_size=16` → 3 块 | 3 + 3 块 | 31,019 | `reconstruct` |
| `curve_scatter_empty` | curve_scatter | 0 点，`chunk_size=16` → 0 块 | 3 + 0 块 | 3,150 | `report_empty` |
| `curve_scatter_partial` | curve_scatter | 12 点（缺 native 3），`chunk_size=5` → 3 块 | 3 + 3 块 | 17,263 | `reconstruct`（含缺失映射） |
| `curve_scatter_corrupt` | curve_scatter | 自 normal 布局改坏：manifest=`schema_version:99`（**合法 JSON、契约非法**）；`block_000000`=合法 JSON、`state:"bogus_state"`；`block_000001`=截断字节（非 JSON） | 3 + 2 块 | 23,232 | `reject`（逐文件期望拒绝类型） |
| `structure_trajectory_normal` | structure_trajectory | 10 帧×4 原子，`chunk_size=4` → 3 块 | 3 + 3 块 | 25,913 | `reconstruct` |
| `structure_trajectory_partial` | structure_trajectory | 10 帧（frame 4 缺），`chunk_size=4` → 3 块 | 3 + 3 块 | 24,936 | `reconstruct`（含缺帧映射） |

- 四态齐全（normal/empty/partial/corrupt）+ 轨迹族 normal/partial（为 A 的单位/选择/缺帧提供实测载体）。
- 数值生成：T31/T32 确定性公式（无 RNG），重生成 sha256 全等（`walks/raw/regen_determinism.out`）。
- 生成命令（B/A 可复跑）：`python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_gen_fixtures.py`（raw：`walks/raw/generate.out`，32 文件逐文件摘要）。
- 抽样/多分辨率 manifest 未做成 fixture：其消费语义（只证映射 + 关键点）由 T32 定向测试守护（`tests/test_cccp_bounded_products.py`，29 passed），列 §8。

---

## 6. 消费契约 walk（read block → decode product → reconstruct or map）

**命令（B/A 均可跑；退出码 0=PASS）：**

```bash
python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_walk.py <fixture>   # 单 fixture 全 walk
python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_walk.py --all       # 6 个全跑
python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_walk.py qa [fixture] # 仅块索引一致性（§7）
```

**walk 步骤（每 fixture 相同骨架）**：STEP1-2 读 index（字节/sha + 头字段）→ STEP3 manifest 字节绑定 → STEP4 manifest 解码（`BoundedProduct.from_dict`，corrupt = 类型化拒绝）→ **STEP5 经块索引逐块读文件 → 字节校验 → `product_from_dict` 解码 → 与 index 条目 + manifest 内嵌块三方比对** → STEP6 独立重推索引数据字段并 diff → STEP7 消费（reconstruct/report_empty/reject）与 `expected.json` + `index.expected_consumption` 比对（含 result_digest）。

raw 输出同时存于 `walks/raw/<fixture>.walk.out`；以下为**逐 fixture 实际执行输出全文**（2026-10-19，`/usr/bin/python3.11`）：

### 6.1 `curve_scatter_normal`（reconstruct）

```
WALK fixture=curve_scatter_normal
  expected.json bytes=524 sha256=sha256:c649be5d5d296807a685d901e3c0796112e02b710c7b4bbfb8cddbda0235a8eb
  STEP1 index.json bytes=3698 sha256=sha256:6b186ba7aa81b99849d0fcf4f721ff4637629e62750ec1ed2680bf1bf435ed2f
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=curve_scatter
  STEP3 manifest bytes=14854 sha256=sha256:cc98c56f84ae4a7eb12d15c7906260b292fe23abe056bcadcb8fc18d3dbf33b5 matches_index=True
  STEP4 manifest decode ok: blocks=3 display_subsampled=False spec.method=chunk resolution=16 range=[0,40]
  STEP5 block[0] file=block_000000.json bytes=4583 sha256=sha256:921f14746f5d5c36... decode=curve_scatter state=available complete=True items=16 range=[0,16) metadata_match=True
  STEP5 block[1] file=block_000001.json bytes=4611 sha256=sha256:6b716c15a2c0a075... decode=curve_scatter state=available complete=True items=16 range=[16,32) metadata_match=True
  STEP5 block[2] file=block_000002.json bytes=2749 sha256=sha256:a986a14e9012a7c2... decode=curve_scatter state=available complete=True items=8 range=[32,40) metadata_match=True
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
  STEP7 consumption op=reconstruct
  STEP7a reconstruct from block files: n_points=40 first=curve_scatter:series:scan_energy:point:0 last=curve_scatter:series:scan_energy:point:39
  STEP7b reconstruct_curve_series(manifest) digest agrees: sha256:d23ae446ad2221d9...
  STEP7c result_digest=sha256:d23ae446ad2221d94b2f888223f453cc07c1a1b0634378dc4bcb7b3936dbb8c8 matches expected.json AND index.expected_consumption
WALK RESULT: PASS fixture=curve_scatter_normal op=reconstruct blocks=3 total_items=40 digest=sha256:d23ae446ad2221d9...
SUMMARY fixtures=1 failures=0
```

### 6.2 `curve_scatter_empty`（report_empty）

```
WALK fixture=curve_scatter_empty
  expected.json bytes=346 sha256=sha256:8ec6a3e37607a8993542dfeb3af561529c67f34387dac53a4626d1191efce864
  STEP1 index.json bytes=1697 sha256=sha256:a2e7fbd111fe5fbf00649bce85be51b19152a58530811f28c762c1324e3f6b15
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=curve_scatter
  STEP3 manifest bytes=1107 sha256=sha256:4156460ef48db6991a846477c3f669da1aab2904dfee390a7e85d70f0ef443b2 matches_index=True
  STEP4 manifest decode ok: blocks=0 display_subsampled=False spec.method=chunk resolution=16 range=[0,0]
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
  STEP7 consumption op=report_empty
  STEP7c result_digest=sha256:6a0f78d5b3480af0e25e363bbb663652d782badd09d676bdbf615484589a4857 matches expected.json AND index.expected_consumption
WALK RESULT: PASS fixture=curve_scatter_empty op=report_empty blocks=0 total_items=0 digest=sha256:6a0f78d5b3480af0...
SUMMARY fixtures=1 failures=0
```

（空组合法消费 = 报告 0 块/0 项后终止；**不得**调用 `reconstruct_*` —— 见 §8 已知边界。）

### 6.3 `curve_scatter_partial`（reconstruct + 缺失映射）

```
WALK fixture=curve_scatter_partial
  expected.json bytes=658 sha256=sha256:0752ace08a21850a031d886d1340f37310fd688e005495cb7da5d362bbb4086e
  STEP1 index.json bytes=3689 sha256=sha256:55947ac277d30dbf37fa0cabc7413a4810ed8290f8deff9f8da93fcfd33d3296
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=curve_scatter
  STEP3 manifest bytes=7489 sha256=sha256:33f8f0cc9ead973209b1a84d7e565bd001558a9804b5ab0d0407734cc65a2c40 matches_index=True
  STEP4 manifest decode ok: blocks=3 display_subsampled=False spec.method=chunk resolution=5 range=[0,12]
  STEP5 block[0] file=block_000000.json bytes=2038 sha256=sha256:a22d556866876c62... decode=curve_scatter state=available complete=False items=5 range=[0,5) metadata_match=True
  STEP5 block[1] file=block_000001.json bytes=2039 sha256=sha256:2249d2f8eab4da89... decode=curve_scatter state=available complete=True items=5 range=[5,10) metadata_match=True
  STEP5 block[2] file=block_000002.json bytes=1350 sha256=sha256:0d6d13da3d35e836... decode=curve_scatter state=available complete=True items=2 range=[10,12) metadata_match=True
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
  STEP7 consumption op=reconstruct
  STEP7a reconstruct from block files: n_points=12 first=curve_scatter:series:scan_energy:point:0 last=curve_scatter:series:scan_energy:point:11
  STEP7b reconstruct_curve_series(manifest) digest agrees: sha256:91dccc3ff99022ce...
  STEP7c result_digest=sha256:91dccc3ff99022ce267931f244fe1ac9ecb65d86e29529888b3dfd95a4a6885f matches expected.json AND index.expected_consumption
  STEP7d missing items preserved (never zero-filled): [{'native_index': 3, 'point_id': 'curve_scatter:series:scan_energy:point:3', 'reason': 'missing_point_values'}]
WALK RESULT: PASS fixture=curve_scatter_partial op=reconstruct blocks=3 total_items=12 digest=sha256:91dccc3ff99022ce...
SUMMARY fixtures=1 failures=0
```

### 6.4 `curve_scatter_corrupt`（reject ——「合法 JSON 不算完成」的反向面）

```
WALK fixture=curve_scatter_corrupt
  expected.json bytes=328 sha256=sha256:c36ec6dc08bb644a88fa92565d2e24bcfe474fd06434bb99b68b7882b095174c
  STEP1 index.json bytes=3164 sha256=sha256:a88c0a95ad403729ddf601996a8d9755639c1833738321578339186da7b2c5b2
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=corrupt kind=curve_scatter
  STEP3 manifest bytes=14855 sha256=sha256:0967206836c5310b0b7d53ea172ff984c458921257a4f126aa515c1980c14914 matches_index=True
  STEP4 corrupt delivery: consumption = typed rejection only (no partial data)
  REJECT manifest.json -> cccp.analysis.bounded.errors.BoundedSchemaError: unsupported bounded schema_version 99; current is 1
  REJECT block_000000.json -> cccp.calculation.products.contracts.ProductSchemaError: product.state must be one of: available, unavailable, failed, not_prepared; got 'bogus_state'
  REJECT block_000001.json -> json.JSONDecodeError: Expecting ',' delimiter: line 8 column 92 (char 300)
  STEP6 index re-derivation: SKIPPED (manifest is contract-invalid by design)
WALK RESULT: PASS fixture=curve_scatter_corrupt op=reject rejections=3
SUMMARY fixtures=1 failures=0
```

要点（评审 X2）：`manifest.json` 与 `block_000000.json` 都是**合法 JSON**仍被契约层拒绝；截断块被 `json` 层拒绝。三类拒绝都必须是**类型化**异常（`BoundedSchemaError` / `ProductSchemaError` / `JSONDecodeError`），且期望拒绝类型逐文件登记在 `index.expected_rejection` 与 `expected.json`，walk 断言实际异常类型与登记一致 —— 消费方不得收养任何部分数据。

### 6.5 `structure_trajectory_normal`（reconstruct）

```
WALK fixture=structure_trajectory_normal
  expected.json bytes=505 sha256=sha256:e56ce620c4753bcfb02a9e5b1518d34da46ddf90b89294c2fb65a80cd5910ab4
  STEP1 index.json bytes=3687 sha256=sha256:d91cce18de869c2dc710a192a4f20192742f356ace7e86e30ff7dec67a508958
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=structure_trajectory
  STEP3 manifest bytes=12349 sha256=sha256:6b88c78525ecd3c8817c9db9f3637c96a95b0a13c15da44b9d7f9c0d84865f4d matches_index=True
  STEP4 manifest decode ok: blocks=3 display_subsampled=False spec.method=chunk resolution=4 range=[0,10]
  STEP5 block[0] file=block_000000.json bytes=3594 sha256=sha256:0c09b7a3d60b7300... decode=structure_trajectory state=available complete=True items=4 range=[0,4) metadata_match=True
  STEP5 block[1] file=block_000001.json bytes=3594 sha256=sha256:9c114e9e385de2ef... decode=structure_trajectory state=available complete=True items=4 range=[4,8) metadata_match=True
  STEP5 block[2] file=block_000002.json bytes=2184 sha256=sha256:947a6b8a2735f1e9... decode=structure_trajectory state=available complete=True items=2 range=[8,10) metadata_match=True
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
  STEP7 consumption op=reconstruct
  STEP7a reconstruct from block files: n_frames=10 first=structure_trajectory:frame:0 last=structure_trajectory:frame:9
  STEP7b reconstruct_structure_frames(manifest) digest agrees: sha256:f1c9eca962b2c30c...
  STEP7c result_digest=sha256:f1c9eca962b2c30c8503bba202b6884eb8398d1e8372bda9c0e588ee65e46cc7 matches expected.json AND index.expected_consumption
WALK RESULT: PASS fixture=structure_trajectory_normal op=reconstruct blocks=3 total_items=10 digest=sha256:f1c9eca962b2c30c...
SUMMARY fixtures=1 failures=0
```

### 6.6 `structure_trajectory_partial`（reconstruct + 缺帧映射）

```
WALK fixture=structure_trajectory_partial
  expected.json bytes=626 sha256=sha256:1ee9ea1041b5152cb8f324419320194afb8e38dec6d4fea0b654ec8c46ac3277
  STEP1 index.json bytes=3688 sha256=sha256:84904cfa429d2bb0d0beff8b91a1c47a55edbe5bc4f0f07366a6fb1b66de80d6
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=structure_trajectory
  STEP3 manifest bytes=11744 sha256=sha256:3a75ad665731415582146d7e4b2851f62194e009b5cb5238f15a1b9f70885582 matches_index=True
  STEP4 manifest decode ok: blocks=3 display_subsampled=False spec.method=chunk resolution=4 range=[0,10]
  STEP5 block[0] file=block_000000.json bytes=3594 sha256=sha256:d0e47a70bddce2b5... decode=structure_trajectory state=available complete=True items=4 range=[0,4) metadata_match=True
  STEP5 block[1] file=block_000001.json bytes=3100 sha256=sha256:377d62d17aef6a07... decode=structure_trajectory state=available complete=False items=4 range=[4,8) metadata_match=True
  STEP5 block[2] file=block_000002.json bytes=2184 sha256=sha256:a0a21bcc69a225fd... decode=structure_trajectory state=available complete=True items=2 range=[8,10) metadata_match=True
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
  STEP7 consumption op=reconstruct
  STEP7a reconstruct from block files: n_frames=10 first=structure_trajectory:frame:0 last=structure_trajectory:frame:9
  STEP7b reconstruct_structure_frames(manifest) digest agrees: sha256:76a44344f4907396...
  STEP7c result_digest=sha256:76a44344f49073967a665795543cac2652eaa4c4fde830ff536e6e2afd4ca3cf matches expected.json AND index.expected_consumption
  STEP7d missing items preserved (never zero-filled): [{'frame_index': 4, 'frame_id': 'structure_trajectory:frame:4', 'reason': 'missing_coordinates'}]
WALK RESULT: PASS fixture=structure_trajectory_partial op=reconstruct blocks=3 total_items=10 digest=sha256:76a44344f4907396...
SUMMARY fixtures=1 failures=0
```

**汇总：6/6 fixture walk PASS（`SUMMARY fixtures=6 failures=0`，`walks/raw/` 各文件 + `--all` 复跑一致）。**

---

## 7. QA — 块索引 ↔ 块一致性（计划 QA 分支）

**方法**：不经生成侧，walk STEP5/6 从**块索引出发**读回块（字节/sha256/解码/三方元数据比对），再**独立重推**索引数据字段（manifest + 块文件 → 重建 12+1 个字段）与落盘 `index.json` diff；不一致 = 索引生成错误 → 修生成器并重测（本交付首轮即 MATCH，未触发 failure 分支）。

```bash
python3.11 tests/baseline/cccp-evidence/handoffs/walks/hn03_walk.py qa   # 默认样本 curve_scatter_normal + structure_trajectory_partial
```

raw 输出（`walks/raw/qa_index_consistency.out` 全文）：

```
WALK fixture=curve_scatter_normal
  expected.json bytes=524 sha256=sha256:c649be5d5d296807a685d901e3c0796112e02b710c7b4bbfb8cddbda0235a8eb
  STEP1 index.json bytes=3698 sha256=sha256:6b186ba7aa81b99849d0fcf4f721ff4637629e62750ec1ed2680bf1bf435ed2f
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=curve_scatter
  STEP3 manifest bytes=14854 sha256=sha256:cc98c56f84ae4a7eb12d15c7906260b292fe23abe056bcadcb8fc18d3dbf33b5 matches_index=True
  STEP4 manifest decode ok: blocks=3 display_subsampled=False spec.method=chunk resolution=16 range=[0,40]
  STEP5 block[0] file=block_000000.json bytes=4583 sha256=sha256:921f14746f5d5c36... decode=curve_scatter state=available complete=True items=16 range=[0,16) metadata_match=True
  STEP5 block[1] file=block_000001.json bytes=4611 sha256=sha256:6b716c15a2c0a075... decode=curve_scatter state=available complete=True items=16 range=[16,32) metadata_match=True
  STEP5 block[2] file=block_000002.json bytes=2749 sha256=sha256:a986a14e9012a7c2... decode=curve_scatter state=available complete=True items=8 range=[32,40) metadata_match=True
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
WALK RESULT: PASS fixture=curve_scatter_normal qa=consistency blocks=3
WALK fixture=structure_trajectory_partial
  expected.json bytes=626 sha256=sha256:1ee9ea1041b5152cb8f324419320194afb8e38dec6d4fea0b654ec8c46ac3277
  STEP1 index.json bytes=3688 sha256=sha256:84904cfa429d2bb0d0beff8b91a1c47a55edbe5bc4f0f07366a6fb1b66de80d6
  STEP2 header ok: hn03_block_index v1 delivery=hn-03-bounded-delivery-v1 bounded_schema=1 product_schema=1 state=ok kind=structure_trajectory
  STEP3 manifest bytes=11744 sha256=sha256:3a75ad665731415582146d7e4b2851f62194e009b5cb5238f15a1b9f70885582 matches_index=True
  STEP4 manifest decode ok: blocks=3 display_subsampled=False spec.method=chunk resolution=4 range=[0,10]
  STEP5 block[0] file=block_000000.json bytes=3594 sha256=sha256:d0e47a70bddce2b5... decode=structure_trajectory state=available complete=True items=4 range=[0,4) metadata_match=True
  STEP5 block[1] file=block_000001.json bytes=3100 sha256=sha256:377d62d17aef6a07... decode=structure_trajectory state=available complete=False items=4 range=[4,8) metadata_match=True
  STEP5 block[2] file=block_000002.json bytes=2184 sha256=sha256:a0a21bcc69a225fd... decode=structure_trajectory state=available complete=True items=2 range=[8,10) metadata_match=True
  STEP6 index re-derivation: MATCH fields=12 (manifest+blocks -> index)
WALK RESULT: PASS fixture=structure_trajectory_partial qa=consistency blocks=3
SUMMARY fixtures=2 failures=0
```

**结论：块↔索引一致（样本 2/2 PASS，12 个数据字段 + 头字段全等）。** 另：重生成确定性检查 `walks/raw/regen_determinism.out`（32 文件 sha256 全等）。

---

## 8. 已知边界与未测（显式）

| 项 | 状态/处置 |
|---|---|
| **空组（0 块）上调用 `reconstruct_*`** | 当前实现抛 `IndexError`（`blocks[0]` 空取），**不是** `BoundedSchemaError`。消费方契约：先看 `index.block_count`（operation=`report_empty`）再决定是否重构 —— walk 即按此实现。本任务按 Must-NOT **不改产品代码**；如需收窄为 typed 错误，须另立 C 侧产品代码切片（已记 `.omo/notepads/acp-parallel-plan-3-cccp/issues.md`） |
| 抽样（stride）/多分辨率 fixture | 未随本交付（四态为验收要求）；其消费语义（仅证映射 + 关键点保留/标注、`reconstruct_*` 拒绝、层内 distinct series_id）由 T32 定向测试守护：`tests/test_cccp_bounded_products.py` 29 passed（`c06-bounded-baseline.md` §7） |
| 缺帧在**轨迹分块**下的行为 | T32 测试只覆盖曲线缺测点；轨迹缺帧分块由本交付 fixture + walk（§6.6）首次给出消费侧实证 |
| 未测 | `chunk_size` ≠ 4096/500 调优、>10⁶ 规模、磁盘 I/O 分解、抽样/多分辨率逐规模曲线（T32 §8）；**A/B 端到端消费**（本 HN 只交 fixture + walk，集成验收由 B/A 消费方记录后回填 `integrated`） |

---

## 9. 交付状态与集成约定

- 本 HN 状态：`delivered`（provider 单方）。**B（Data）/ A 的集成验收** = 消费方在同一 fixture 组上跑通 §6 命令（至少每 fixture 一次 walk）+ 其自身消费面接入后，在**权威表** `tests/baseline/services-evidence/handoff_registry.md` 回填 `integrated` + consumer 提交；C 侧 `handoff_registry.md` HN-03 行同步引用（不复制状态）。
- 「样例是合法 JSON」**不构成**验收（X2）：验收必须覆盖 reconstruct/map/report_empty/reject 四类 operation 与 result_digest 比对。
- 本任务改动面（`git status`，fresh 快照 `6a67635`）：仅 `tests/fixtures/science_products/bounded/**`（新增）、`tests/baseline/cccp-evidence/handoffs/{HN-03-bounded-products.md, walks/**}`（新增）、`tests/baseline/cccp-evidence/handoff_registry.md`（HN-03 行回填）—— **零 `src/**` 改动，零其它 lane 文件**。
