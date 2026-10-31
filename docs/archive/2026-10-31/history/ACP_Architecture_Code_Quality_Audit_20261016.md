# ACP 架构调整代码质量清理台账

日期：2026-10-16。主方案 v1.4 第 29 节的证据附件，不另设实施顺序。审计基线：HEAD `779a29a` 加当前未提交工作区；本次只读扫描 src/frontend/tests 与相关配置，不声明全部死代码已证明，不修改运行代码。

## 1. 方法与局限

使用源码引用检索、公开导出/路由/恢复 fixture 检查，以及 Python AST 函数体比较（函数跨度至少 8 行，忽略源码位置）识别候选。AST 比较不包含签名/装饰器，也不能证明外部全局依赖相同，因此以下“相同函数体”均须再做语义对照。未运行应用、科学软件或全套测试；尚无性能收益实测。行号为本次快照导航，实施时按符号重新定位。

## 2. 有具体证据的合并候选

| ID / 优先级 | 源码证据 | 建议与必要验证 |
|---|---|---|
| C01 / 优先 | `cli.py:3734::_is_wsl_environment` 与 `api/routes.py:62::_is_wsl` 函数体相同 | 归入入口环境探测支撑；核对平台探测/异常和测试 patch 路径，避免 Web 依赖进入 CLI |
| C02 / 优先 | `scheduler/manager.py:137::_parse_iso_ts` 与 `scheduler/remote/submission.py:86::_parse_iso` 函数体相同 | 归 Runtime 纯时间解析；验证无时区/非法日期/UTC 及租约过期语义，不改变提交权判断 |
| C03 / 优先 | `workflows/energy_shared.py:1462::resolve_crest_ewin` 与 `confsearch/shared/helpers.py:49::resolve_crest_ewin` 函数体相同 | 核对 globals/defaults 后由单一科学配置归一函数实现，保留旧导出；验证四协议参数优先级 |
| C04 / 随迁移 | `workflows/orca_gradient.py:326::_write_text_atomic`、`xtb_constrained_opt.py:392::_write_text_atomic`、`calculations/pes/review.py:395::_atomic_write_text` 函数体相同 | 先检查已有原子写机制；按文件 owner 复用底层机制，不合并科学发布事务；验证临时文件清理、失败与覆盖行为 |
| C05 / 随迁移 | `workflows/xtb_constrained_opt.py:414/425` 与 `xtb_path.py:318/329` 的 `_sha256_file`、`_executable_provenance` 函数体分别相同 | 科学软件来源/摘要归科学 owner；核对版本探测是否启动程序、缓存键和失败语义，不能让 Data 隐式执行 |
| C06 / 随迁移 | primitives frequency/optimize/singlepoint 的 `_legacy_result`/`legacy_result` 函数体相同 | 仅归并兼容结果转换，先核对类型/字段及 globals；不在 ACP 增加科学实现 |
| C07 / 审慎 | `cccp/qc/interfaces/xtb.py:103` 与 `xtb_path.py:105` 的 `_solvent_args` 函数体相同 | 核对两接口支持能力/异常；走 F4 amendment 和输入 fixture，不能把整个 XTBInterface 回并 |
| C08 / 审慎 | ACP/CCCP `calculation(s)/contracts.py` 中部分 `__post_init__` 函数体相同 | 仅列审计候选；平台/科学契约职责不同，相同校验不足以合并类型，优先保留边界 |

上述路径除明确 cccp 外均相对 `src/acp/`。函数名相似但语义不同不列为可直接合并：例如 energy_graph 的 `_relative_hartree` 与 `_relative_from_first` 必须分别确认参考能量，不能凭表达式相似统一。

## 3. 常量与旧能力的实质重复

- `HARTREE_TO_KCAL` 不止知识库所述两处：源码定义见 `cccp/utils/constants.py:6`、`acp/core/models.py:19`、`acp/confsearch/shared/boltzmann.py:8`、`acp/results/energy_graph.py:24`、`acp/calculations/pes/path_analysis.py:20`。前四处同为 627.5094740631，PES 为 627.509。前四处可在保留导出下归唯一常量；PES 精度变更必须单列黄金值/阈值评估，不能夹带进搬迁。
- 气体常数在 `cccp/core/candidates.py`、`acp/confsearch/shared/boltzmann.py`、`acp/workflows/nmr.py` 存在不同精度或单位换算，权威已有 `GAS_CONSTANT_KCAL_PER_MOL_K`。须记录历史数值、单位、权重/筛选边界后逐一迁移，不宣称无数值变化。
- `cccp/utils/resource_utils.py::find_executable/resolve_executable_config` 仍有实现和 `utils/__init__.py` 导出；在本次 src/tests 检索中未发现其他业务调用，现有权威为 `cccp.software.resolve_executable`。这是合并兼容 adapter 的候选，而不是可直接删的私有死代码：旧函数 fallback_paths、返回元组和配置形状须先兼容。

## 4. 高收益结构治理

| 热点（实测行数） | 清理目标 | 交付条件 |
|---|---|---|
| `frontend/ACP_Workbench_v2.html` 35076 | 抽出 Workbench/Client/ACV；统一 api/apiRequest/apiRemote/apiV2 与订阅生命周期 | 保持本地/远程超时差异、共享取消和结构化错误；删对应旧入口；framePlayTimer 是动画，不作为重复网络轮询删除 |
| `src/acp/api/v1_routes.py` 7394 | 路由只适配协议，用例和写权归 Runtime/Data/Workspace；审计 rglob 与宽泛异常 | 持久 prepare/后台预取准备好后才切 cache-only，不删除回退而让旧结果永久不可读 |
| `src/acp/scheduler/manager.py` 5758 | 生命周期、资源准入、发布/传输调用按 owner 收敛 | CAS、单控制 owner、停止确认与恢复保留；禁止拆成多个独立状态机 |
| `src/acp/catalog.py` 5424 | 科学定义、平台 profile、显示文本分离并保留单一来源 | CLI/前端/远端参数归一三个 choke points 和 active/edit coverage 仍一致 |
| `tests/test_frontend_sync.py` 14317 | 按 Client/ACV/向导/兼容责任拆分；迁移时逐步补行为证据 | 不丢原断言/收集项；文本护栏不能只因脆弱而删除，需先有等效或更强检查 |

宽泛异常见 v1_routes 和 remote_structure_cache 多处；先区分顶层隔离、可预期不可用与缺陷掩盖，再按切片收敛。不能全局删除 except 或把所有错误变成 500。此次未给出“可减少百分之多少代码”的未经验证估计。

## 5. 不能误删的代码与资产

| 对象 | 已发现的保留证据 | 后续处置 |
|---|---|---|
| `workflows/ensemble.py`、`xtbmd_censo_energy.py`、`energy_shared.py` | Confsearch 的 xtb_crest/censo_crest/xtbmd_censo/xtb_md 与 NMR 仍有调用或复用 | retired CLI 不等于实现无用；科学闭包提取后保留转发，不能整文件删除 |
| `acp/backends/batch.py` | test_acp_backends_batch、recovery_cross_version、recovery_fixtures_smoke 及基线生成器引用旧缓存接口 | 隔离兼容面，保留旧缓存读取/格式证据；新执行禁止回用，确认外部兼容窗口后才能裁剪旧执行体 |
| `frontend/ACP_Workbench.html` | `api/server.py::legacy_index` 仍通过 `/legacy/` 提供 | 需明确旧入口退役和历史显示替代后再删，不能称为未引用 |
| `ACP_Workbench_minimal.html` | 当前 server 不路由，test_frontend_sync 明确禁止其作为回退 | 仅为候选；还需查直接静态访问、发布脚本/文档和外部用户依赖，未满足删除门 |
| primitives/backends shim、compat/legacy | 公开旧导入、历史 manifest、类身份与恢复兼容 | 零业务逻辑是目标，文件留存不是质量失败 |
| vendor、NMR 模型/报告库、baseline、artifacts、运行目录 | 第三方许可、科学复现、历史审计或用户数据 | 本轮不按大小、旧日期或名字清理；依赖移除须最小安装及报告/模型加载验证 |

## 6. 执行验收记录

每项候选追加：owner、引用与导出审计、唯一目标实现、处理决策、改动前后行为、受影响测试、回退与证据。当前全部为“待语义核验/待实施”，没有已删除代码。优先推进 C01/C02/C03 与同精度常量；科学精度变更、旧缓存和多租户路径修改单独切片。质量门以主方案第 29 节为准。


## v1.6 初始化向导追加审计

`src/acp/init_wizard/flows.py::_node_entry_error` 的注释明确说明其镜像 RemoteNode.from_config_dict 校验。IN-02/CQ-02 应提取无 Paramiko 依赖的纯配置验证供双方复用，保持 core-only 导入、无效节点可见不可选、旧错误语义和配置兼容。当前为待实施候选，不得直接删除验证或强制加载远端依赖。新向导继续复用 defaults 与各 owner 验证，不复制配置权威。
