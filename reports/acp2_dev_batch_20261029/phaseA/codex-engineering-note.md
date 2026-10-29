# 前置批次：基线修复工程交接

状态：**未达到整批完成标准**。F01/F03/F02/F04 已有可审查修改；F09 尚未定位或修复；F08 已完成本机候选定位及摘要核对，但原 URI 可取回性仍 FAIL。浏览器交互与插件服务集成受当前执行沙箱限制，不能标记 PASS。

工作区 `/ACPServer/AutoCalcPlatform`；基线 HEAD `fd2087c71df9695edbc798b74e595ee487b198f8`。已阅读根 AGENTS.md、tests、acp、api、plugin、acp_services 的相关嵌套约束，核对审查报告 §4 F01–F10、原 browser.xml、Origin 隔离复验、overlap JSON/probe、nonslow 插件断言、BOM walker/integrity 与历史数值编辑记录。F05/F06/F07/F10 不在本批功能开发范围。

## 每项结论

| 编号 | 根因/定位结论 | 本次工程处理 | 验证状态 |
|---|---|---|---|
| F01 | 原队列 `flex:1 1 auto; min-height:0` 可被自然高度约 1630px 的科研面板挤到零高度；科研面板又允许 visible overflow，内容命中项目/队列控件；shell grid 也缺少明确的剩余空间约束。代码与历史 hit-test 支持此解释，本次动态复现被环境阻断。 | 固定桌面 shell/grid 约束，队列禁止收缩；科研面板最大 `min(640px,100vh-64px)`、自身滚动和裁剪；移动端恢复自然高度；补三个视口下项目、分页、队列折叠/展开回归。 | Node 布局回归 PASS；真实 hit-test、shell 缺查看器、分页/控制浏览器复验 NOT_VERIFIED。 |
| F03 | DOM input.value 是字符串；原 change 直接传给 setValue，整数 schema 拒绝字符串；渲染模型丢失 integer/number 区别。 | 仅 DOM 边界按 schema 转换有限数字；整数小数不截断；step=1/any；空字符串保留为空；badInput 单独变为会被严格校验拒绝的非法值，不能退回默认值或沿用旧值；新增编辑仍记录溯源。 | Node 整数/数值/非法/空值回归 PASS；真实 Chromium change 测试已新增但 NOT_VERIFIED。后端与共享严格 validator 未放宽；程序化字符串仍被拒绝。可选空值是否回退默认值继续由原 schema 规则决定。 |
| F09 | 原日志仅证明 canvas 或 container 至少有一个缺失，无法区分是哪一个。检查了 fixture、async 本地 vendor、initViewer/renderMolDoc、scene adapter、framing 和容器布局，未找到可确认的根因。 | 强化原失败断言的诊断字段并要求 canvas 连接且仅一个；保留尺寸/中心断言。**没有把推测当作修复，没有归因为渲染引擎缺陷。** | NOT_VERIFIED / 未完成。独立复现首先在临时 TCP socket 创建处失败；直接 Chromium 启动也在 sandbox_host_linux.cc 的 shutdown 权限检查崩溃，未进入真实渲染。 |
| F02 | 回归误把 pip 专用 --no-index 要求施加到 python -m build；后者正确使用 --no-isolation。 | 两条 argv 分别检查；真实执行两种构建器，不卸载 build；fixture backend 本地、requires=[]、dependencies=[]；子进程 PIP_NO_INDEX=1，无 Collecting/Downloading；保留 metadata-only 原断言。 | 两个真实构建器用例 PASS；全文件 17 PASS / 8 ERROR（临时 uvicorn 无法绑定端口），全文件集成 NOT_VERIFIED。 |
| F04 | 临时端口夹具继承进程环境或 init YAML 的 deployment public_origin，与浏览器实际 Origin 不同，被产品边界正确拒绝。 | 五个本机临时服务显式使用隔离夹具，不是全局 autouse；环境值为空覆盖 YAML，teardown 恢复。新增同源允许/evil 拒绝的纯边界回归，保留独立攻击测试与产品代码。 | 夹具/OriginPolicy 回归及四种 HTTP 写方法攻击拒绝 PASS；原十个合法浏览器请求本次 NOT_VERIFIED。 |
| F08 | 原冻结路径 `/ACPServer/acp2-artifacts/remediation/m5-03` 本机不存在。 | 只读检索既有制品及按大小检索改名候选，逐候选核对 SHA-256；没有构建发行包、修改 BOM 或回填 URI。 | 原 URI 0/31 存在（FAIL）；16/31 找到历史摘要匹配副本；15/31 在本次可读范围内未找到匹配副本。 |

## 修改文件（仅本次工程改动）

- `frontend/css/workbench/layout.css`：F01：限定桌面 shell 高度与可收缩 grid 行，保留工作区宽度约束。
- `frontend/css/workbench/queue-detail.css`：F01：队列不再被后续面板挤成零高度；科研面板限定视口高度并可滚动。
- `frontend/css/workbench/features.css`：F01：科研面板自身裁剪/滚动，不向队列区域溢出；移动布局保持自然高度。
- `frontend/js/workbench/contracts/ui_subset.js`：F03：渲染模型保留 schema_type，区分 integer 与 number；冻结契约和 digest 均未修改。
- `frontend/js/workbench/features/capability-form.js`：F03：真实 change 入口将合法数字转为 number，整数不截断小数；空字符串与 badInput/非法值分开处理。
- `frontend/js/module_tests/acp2_layout.test.js`：F01：检查队列不收缩与面板有界滚动；替换原先要求无限溢出的旧样式断言。
- `frontend/js/module_tests/capability_form.test.js`：F03：通过 DOM listener 测试整数、负数、小数、空值、非法值、badInput、编辑溯源及程序化调用严格校验。
- `tests/_acp2_external_plugin_harness.py`：F02：构建子进程显式 PIP_NO_INDEX=1；保留原 build/pip 选择与参数。
- `tests/test_acp2_science_plugin_production.py`：F02：按实际工具链断言；参数化真实执行 build、pip 两种命令，并检查无构建/运行依赖与下载日志。
- `tests/conftest.py`：F04：新增非 autouse 的 session 夹具，显式置空 ACP_PUBLIC_ORIGIN，并在 teardown 恢复。
- `tests/test_frontend_startup_browser.py`：F01/F03/F04：临时 Origin 隔离；新增 1024×600、1280×800、1920×1080 队列操作回归与真实浏览器 change 回归。
- `tests/test_frontend_interaction.py`：F04：临时服务依赖隔离夹具；保留外部 URL 分支与全部原 marker/攻击保护。
- `tests/test_frontend_measure_edit_browser.py`：F04：临时服务依赖隔离夹具。
- `tests/test_frontend_multiframe_playback_browser.py`：F04：临时服务依赖隔离夹具。
- `tests/test_frontend_viewer_framing_browser.py`：F04/F09：临时 Origin 隔离；失败时输出容器/canvas 存在性、连接数、库资源、sceneError 与 framing 状态，不降低断言。
- `tests/test_services_local_boundary.py`：F04：模拟部署 YAML，验证夹具覆盖配置后临时同源允许、evil Origin 仍为 403。

共 16 个仓库文件。没有改动用户已有文档、AGENTS.md、历史审查证据或正式门/BOM。受保护快照核对 **699 文件、0 内容变化**，见 [protected-check.json](protected-check.json)。无 git add/commit、生产重启、真实模型/QC/LSF 提交；临时 uvicorn 均未能完成监听。测试构建仅为必需的纯分析插件 fixture，不用这些本次新构建文件作为 F08 的历史恢复证据。

## 实际验证命令与结果

以下 ERROR/中断不计为 PASS；产品交互在环境错误前尚未执行的部分标 NOT_VERIFIED。完整终端输出见链接，必要失败输出在本文附录原样收录。

| 命令 | 实际结果 | 原始结果 |
|---|---|---|
| `python3.11 -m pytest tests/test_acp2_science_plugin_production.py -q --junitxml=/tmp/phaseA/plugin-final.xml` | 17 PASS / 8 ERROR；两个 build/pip 参数化用例都 PASS；uvicorn `could not bind on any address out of [('127.0.0.1', 0)]` | [完整日志](plugin-final.log)、[JUnit](plugin-final.xml) |
| `ACP_PUBLIC_ORIGIN= python3.11 -m pytest tests/test_frontend_viewer_framing_browser.py::TestMainViewerFraming::test_main_viewer_first_load_centers_model --run-slow -q --tb=long --junitxml=/tmp/phaseA/viewer-before.xml` | 独立修前复现：1 ERROR，socket PermissionError | [完整日志](viewer-before.log)、[JUnit](viewer-before.xml) |
| 定向剩余 8 个原失败项，精确 selector 见下面代码块；`ACP_PUBLIC_ORIGIN= ACP_T25_BASE_URL=` | 8 ERROR，无 skip；临时 socket PermissionError，未进入页面 | [完整日志](browser-targeted.log)、[JUnit](browser-targeted.xml) |
| `ACP_PUBLIC_ORIGIN= ACP_T25_BASE_URL= python3.11 -m pytest tests/test_frontend_startup_browser.py::test_research_panels_do_not_cover_queue_controls tests/test_frontend_startup_browser.py::test_capability_numeric_fields_use_real_change_events tests/test_frontend_viewer_framing_browser.py::TestMainViewerFraming::test_main_viewer_first_load_centers_model --run-slow -q --junitxml=/tmp/phaseA/browser-affected.xml` | 5 ERROR（三个视口、数值编辑、首载）；NOT_VERIFIED | [完整日志](browser-affected.log)、[JUnit](browser-affected.xml) |
| `python3.11 -m pytest tests/test_services_local_boundary.py::test_temporary_browser_origin_isolated_from_deployment tests/test_services_local_boundary.py::test_forged_cross_site_origin_write_denied tests/test_services_local_boundary.py::test_public_origin_remains_scheme_and_port_aware tests/test_frontend_sync_viewer_framing.py -q --junitxml=/tmp/phaseA/origin-and-viewer-unit.xml` | **16 PASS**，包含 6 项 Origin 回归、10 项查看器静态契约；并非浏览器证据 | [日志](origin-and-viewer-unit.log)、[JUnit](origin-and-viewer-unit.xml) |
| `python3.11 -m pytest tests/test_acp2_ui_subset.py -k 'not route' -q --junitxml=/tmp/phaseA/ui-schema.xml` | **17 PASS / 2 deselected**；明示纯契约范围，未计入未跑的 HTTP route 测试 | [日志](ui-schema.log)、[JUnit](ui-schema.xml) |
| `node frontend/js/module_tests/run_all.js` | **481 PASS / 0 FAIL** | [完整日志](frontend-modules-final.log) |
| 8 个修改 Python 文件 `python3.11 -m ruff check ...`（清单见 ruff-final.log 对应说明） | PASS；interaction 文件另有基线已存在的三处 E501，原样登记，未批量格式化 | [增量检查](ruff-final.log)、[存量输出](ruff-browser-initial.log)、[HEAD 对照](ruff-interaction-baseline.log) |
| 修改 Python 文件 `python3.11 -m compileall -q ...` | PASS | [日志](compile.log) |
| `git diff --check` | PASS | [日志](diff-check.log) |
| `python3.11 /tmp/phaseA/locate_bom.py` | 完成只读候选扫描与摘要核对，详见下节 | [日志](bom-location.log)、[所有候选及摘要](bom-location.json) |

定向 8 项的完整命令：

```bash
ACP_PUBLIC_ORIGIN= ACP_T25_BASE_URL= python3.11 -m pytest \
  tests/test_frontend_startup_browser.py::test_stalled_catalogs_and_missing_viewer_do_not_block_shell \
  tests/test_frontend_startup_browser.py::test_failed_task_list_has_retry_and_recovers \
  tests/test_frontend_startup_browser.py::test_paging_keeps_counts_and_reuses_unchanged_rows \
  tests/test_frontend_viewer_framing_browser.py::TestMainViewerFraming::test_main_viewer_first_load_centers_model \
  tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser::test_completed_row_drops_stale_active_actions_and_offers_rerun_menu \
  tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser::test_unselected_row_pause_then_resume_reflects_fresh_state \
  tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser::test_batch_ops_invalidate_all_selected_row_caches \
  --run-slow -q --junitxml=/tmp/phaseA/browser-targeted.xml
```

首项参数化为两项，因此共 8 项。首次误选择整个 QueueRowFreshnessBrowser 类的 12 ERROR 也保留在 [初次选择日志](browser-initial-selection.log)/[JUnit](browser-initial-selection.xml)，不冒充精确 8 项结果。

**整组浏览器：NOT_VERIFIED，未执行。** 用户要求在定向 8 项通过后再跑整组，当前前提未成立，故没有生成虚构的 `/tmp/phaseA/browser-rerun.xml`。已准备 [rerun_browser.py](rerun_browser.py)：在允许临时监听及 Chromium 正常启动的同一工作区中运行 `python3.11 /tmp/phaseA/rerun_browser.py`，它先重跑精确 8 项，通过才运行审查同款五模块（含新增回归），将整组 JUnit 写到要求的 `/tmp/phaseA/browser-rerun.xml`。当前 CI 环境变量未设置，也未修改 marker 或跳过任何本次用例制造通过。

另有 Origin 全文件初试在 36 个已完成纯边界用例后于原 TestClient 用例挂起，人工中断；新夹具 TestClient 初试也挂起并由 45 秒 timeout 中断。这两次均不算全文件 PASS，日志完整保留为 [origin.log](origin.log)、[origin-regression.log](origin-regression.log)。之后采用独立的纯 resolver/OriginPolicy 回归（见 16 PASS），没有以此代替 HTTP 复验。

本次 ruff PASS 的八文件为 conftest.py、test_frontend_startup_browser.py、test_acp2_science_plugin_production.py、test_services_local_boundary.py、_acp2_external_plugin_harness.py、test_frontend_viewer_framing_browser.py、test_frontend_measure_edit_browser.py、test_frontend_multiframe_playback_browser.py。format 初检中 conftest 与 local_boundary 既有格式债未整文件改写；startup 已按 ruff format 格式化；原始检查均保留。

## F08：候选保存位置与摘要结论

冻结 BOM SHA-256：`15839febdc3f3a333528e6d7401b9bc0b400c9464be68ba4922c2e6705766530`。检索根：`/ACPServer`、`/tmp`、`/opt`、`/home`、`/mnt`、`/media`、`/var/tmp`、`/var/cache`、`/srv`、`/root`；检查 1,302,830 个文件入口。同名候选及大小相同的改名候选均核对摘要；通用 README/package/lock/checksum 名称只考虑大小匹配者。排除 `.git/.ssh/.aws/.gnupg`，有 142 条不可读/失效链接等错误，详见 JSON，因此不宣称全机绝对不存在。未挂载或不可访问的外部保存位置为 NOT_VERIFIED。

| BOM id | 结论 | 可用历史候选位置（逐项 SHA-256 已与 BOM 相等） |
|---|---|---|
| `python-roles-acp_web-0.3.0-py3-none-any.whl` | 未找到匹配副本 | 同名/同大小候选 9 个；摘要均不符 |
| `python-roles-acp_worker-0.3.0-py3-none-any.whl` | 未找到匹配副本 | 同名/同大小候选 8 个；摘要均不符 |
| `python-roles-cccp-0.3.0-py3-none-any.whl` | 未找到匹配副本 | 同名/同大小候选 10 个；摘要均不符 |
| `python-roles-acp-web-0.3.0.tar.gz` | 未找到匹配副本 | 同名/同大小候选 5 个；摘要均不符 |
| `python-roles-acp-worker-0.3.0.tar.gz` | 未找到匹配副本 | 同名/同大小候选 4 个；摘要均不符 |
| `python-roles-cccp-0.3.0.tar.gz` | 未找到匹配副本 | 同名/同大小候选 4 个；摘要均不符 |
| `python-root-acp-0.3.0-py3-none-any.whl` | 未找到匹配副本 | 同名/同大小候选 8 个；摘要均不符 |
| `python-root-acp-0.3.0.tar.gz` | 未找到匹配副本 | 同名/同大小候选 5 个；摘要均不符 |
| `rph-research` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/rph-research-0.0.0.tgz` |
| `rph-bridge` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/rph-bridge-0.0.0.tgz` |
| `rph-dsh-bundle` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/rph-dsh-bundle-0.0.0.tgz` |
| `rph-cli` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/rph-cli-0.0.0.tgz` |
| `rph-surface-rph-install-package-json` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/package.json` |
| `rph-surface-package-json` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/package.json` |
| `rph-surface-pnpm-workspace-yaml` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/pnpm-workspace.yaml` |
| `rph-surface-pnpm-lock-yaml` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/node_modules/.pnpm/lock.yaml` |
| `rph-surface-rph-packages-json` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/rph-packages.json` |
| `rph-surface-SHA256SUMS` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/SHA256SUMS` |
| `rph-surface-README-md` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/README.md` |
| `rph-surface-verify-install-mjs` | MATCH | `/ACPServer/acp2-runs/remediation/m5-01-install/rph-install/verify-install.mjs` |
| `plugin-external` | MATCH | `/ACPServer/acp2-runs/remediation/m4-05/environment/wheel/acp2_external_science_validator-1.0.0-py3-none-any.whl` |
| `plugin-upgrade-v1` | MATCH | `/ACPServer/acp2-runs/remediation/m4-05/environment/wheel/acp2_upgrade_science_validator-1.0.0-py3-none-any.whl` |
| `plugin-upgrade-v2` | MATCH | `/ACPServer/acp2-runs/remediation/m4-05/environment/wheel/acp2_upgrade_science_validator-2.0.0-py3-none-any.whl` |
| `static-assets-workbench` | MATCH | `/ACPServer/AutoCalcPlatform/docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json` |
| `contracts-acp2` | 未找到匹配副本 | 同名/同大小候选 0 个；本次未发现候选 |
| `science-nmr-model-assets` | 未找到匹配副本 | 同名/同大小候选 0 个；本次未发现候选 |
| `docs-release` | 未找到匹配副本 | 同名/同大小候选 0 个；本次未发现候选 |
| `python-wheelhouse` | 未找到匹配副本 | 同名/同大小候选 9 个；摘要均不符 |
| `python-wheelhouse-checksums` | 未找到匹配副本 | 同名/同大小候选 16 个；摘要均不符 |
| `evidence-receipts` | 未找到匹配副本 | 同名/同大小候选 0 个；本次未发现候选 |
| `evidence-receipts-manifest` | 未找到匹配副本 | 同名/同大小候选 14 个；摘要均不符 |

16 项匹配副本不意味着 31 项原 URI 已恢复。RPH 既有 dist-packages 中部分 tgz/lock/checksum 与冻结 BOM 不符，故优先列出历史 install 目录中的匹配副本。8 个 Python wheel/sdist 的同名副本均不能凭版本名替代旧摘要；contracts/NMR 资产/docs/receipt 归档和 wheelhouse 两个 manifest 也未找到匹配副本。未复制到 `/ACPServer/acp2-artifacts`，没有改写任何原 BOM URI、digest 或 retrievable 字段；本批没有重建 Python/RPH 发行物。插件测试新生成的确定性 fixture wheel 即使摘要相同，也不作为上述历史候选结论的依据。

## 剩余风险与未覆盖项

- F09 仍未定位，必须在能启动本机临时服务和 Chromium 的环境读取新增诊断并完成真实修复；不能宣称本批已完成。
- F01 的不同视口实际 hit-test、顶部/队列/分页、缺失与停滞查看器下 shell、科研面板滚动及移动端没有本次浏览器 PASS；CSS 修改尚需真实布局复核。
- F03 的整数/小数/空/非法输入 Node 回归已通过，真实 Chromium change 尚未验证；冻结绑定表仍以整数参数为主，Node 数值渲染用受支持的 scalar type array 覆盖小数，不提交小数电荷。后端代码与严格校验未修改，亦未把纯模块测试当作新的后端 HTTP 拒绝证据。
- F02 服务 discovery/analysis/disable/uninstall 8 项被夹具前置 uvicorn 启动失败阻断，metadata-only 原断言和服务链仍需在可监听环境全文件重跑。
- F04 原 10 项合法写请求须复验；纯边界攻击拒绝 PASS 不替代这 10 项。
- F08 15 个制品没有匹配副本，原 31 个 URI 均缺失；需要制品保存方确认外部 store/备份/挂载，不能靠重建关闭旧 digest 可取回性问题。
- 未执行整组浏览器、生产发布、真实模型/QC/LSF、四发行重建/独立安装。正式 G/H 门、candidate_full.allowed 与历史审查 FAIL 均未提升。

## 失败/环境阻断的完整输出

以下内容是原始日志逐字收录。其他重复初试日志也全部保留在本目录；[junit-summary.json](junit-summary.json) 给出每份 JUnit 的机器计数，不将重复运行累加为独立覆盖。

### plugin-final.log

```text
.................EEEEEEEE                                                [100%]
==================================== ERRORS ====================================
_ ERROR at setup of test_clean_environment_install_is_offline_and_discovery_is_metadata_only _

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
________ ERROR at setup of test_clean_environment_fail_closed_negatives ________

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
_______ ERROR at setup of test_clean_environment_enablement_is_persisted _______

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
_ ERROR at setup of test_real_service_discovers_the_enabled_plugin_on_both_surfaces _

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
_ ERROR at setup of test_plugin_analysis_runs_over_the_prepared_artifact_with_full_provenance _

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
____________ ERROR at setup of test_analysis_refusals_start_nothing ____________

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
_ ERROR at setup of test_disable_is_persisted_and_stops_new_process_discovery __

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
_ ERROR at setup of test_uninstall_keeps_the_frozen_descriptor_and_the_product_readable _

tmp_path_factory = TempPathFactory(_given_basetemp=None, _trace=<pluggy._tracing.TagTracerSub object at 0x14970c967390>, _basetemp=PosixPath('/tmp/pytest-of-xieningke/pytest-7073'), _retention_count=3, _retention_policy='all')

    @pytest.fixture(scope="module")
    def m205_environment(
        tmp_path_factory: pytest.TempPathFactory,
    ) -> Generator[dict[str, Any], None, None]:
        override = os.environ.get("ACP2_M205_WORK_ROOT")
        if override:
            root = Path(override) / f"pytest-{uuid.uuid4().hex[:8]}"
        else:
            root = tmp_path_factory.mktemp("m205-plugin")
        root.mkdir(parents=True, exist_ok=True)
        wheel_v1, build_argv_v1, build_log_v1 = build_fixture_wheel(root / "wheel")
        python, venv_argv = create_isolated_venv(root)
        install_argv, install_result = install_wheel(python, wheel_v1)
        run_root = root / "run-root"
        payload: dict[str, Any] = {
            "root": root,
            "wheel": wheel_v1,
            "python": python,
            "run_root": run_root,
            "build_argv": build_argv_v1,
            "build_log": build_log_v1,
            "venv_argv": venv_argv,
            "install_argv": install_argv,
            "install_log": install_result.stdout + install_result.stderr,
        }
        payload["enabled"] = run_python_script(python, PROBE, "enable", run_root)
        product = root / "v1-product.json"
>       payload["served"] = run_python_script(python, PROBE, "serve", run_root, FIXED_INPUT, product)
                            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_acp2_science_plugin_production.py:658: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

python = PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/venv/bin/python')
script = PosixPath('/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py')
args = ('serve', PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/run-root'), PosixPath('/ACPServer/AutoCalcPlatf...in/fixtures/native_singlepoint.acp2n'), PosixPath('/tmp/pytest-of-xieningke/pytest-7073/m205-plugin0/v1-product.json'))

    def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
        """Run *script* in the isolated venv and return its JSON payload."""
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
        argv = [str(python), str(script), *(str(arg) for arg in args)]
        result = _run(argv, env=env)
        if result.returncode != 0:
>           raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
E           AssertionError: probe failed (1):
E           ERROR:    could not bind on any address out of [('127.0.0.1', 0)]
E           Traceback (most recent call last):
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 509, in <module>
E               sys.exit(main(sys.argv))
E                        ^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 495, in main
E               payload = probe_serve(run_root, Path(argv[3]), Path(argv[4]))
E                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 181, in probe_serve
E               with _served(run_root) as served:
E             File "/ACPServer/AutoCalcPlatform/tests/_acp2_m205_plugin_probe.py", line 388, in __enter__
E               raise AssertionError("uvicorn thread exited before startup")
E           AssertionError: uvicorn thread exited before startup

tests/_acp2_external_plugin_harness.py:163: AssertionError
=========================== short test summary info ============================
ERROR tests/test_acp2_science_plugin_production.py::test_clean_environment_install_is_offline_and_discovery_is_metadata_only
ERROR tests/test_acp2_science_plugin_production.py::test_clean_environment_fail_closed_negatives
ERROR tests/test_acp2_science_plugin_production.py::test_clean_environment_enablement_is_persisted
ERROR tests/test_acp2_science_plugin_production.py::test_real_service_discovers_the_enabled_plugin_on_both_surfaces
ERROR tests/test_acp2_science_plugin_production.py::test_plugin_analysis_runs_over_the_prepared_artifact_with_full_provenance
ERROR tests/test_acp2_science_plugin_production.py::test_analysis_refusals_start_nothing
ERROR tests/test_acp2_science_plugin_production.py::test_disable_is_persisted_and_stops_new_process_discovery
ERROR tests/test_acp2_science_plugin_production.py::test_uninstall_keeps_the_frozen_descriptor_and_the_product_readable
17 passed, 8 errors in 9.00s

```

### browser-targeted.log

```text
EEEEEEEE                                                                 [100%]
==================================== ERRORS ====================================
_ ERROR at setup of test_stalled_catalogs_and_missing_viewer_do_not_block_shell[False] _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of test_stalled_catalogs_and_missing_viewer_do_not_block_shell[True] _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
________ ERROR at setup of test_failed_task_list_has_retry_and_recovers ________

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_____ ERROR at setup of test_paging_keeps_counts_and_reuses_unchanged_rows _____

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of TestMainViewerFraming.test_main_viewer_first_load_centers_model _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        The server runs in a daemon thread; ``create_app`` is called with a
        temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        # Import the app factory — this is the canonical entry point.
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_viewer_framing_browser.py:118: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_viewer_framing_browser.py:75: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of TestQueueRowFreshnessBrowser.test_completed_row_drops_stale_active_actions_and_offers_rerun_menu _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Yield the workbench base URL.
    
        When ``ACP_T25_BASE_URL`` is set (e.g. an ``acp run serve`` instance),
        drive that server; otherwise boot the real FastAPI app in-process on an
        ephemeral port with a throwaway ``run_root``.
        """
        external = os.environ.get("ACP_T25_BASE_URL", "").strip()
        if external:
            yield external.rstrip("/")
            return
    
        import tempfile
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_t25_server_"))
        # Set BEFORE importing the server module (module-level app reads it).
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_interaction.py:168: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_interaction.py:104: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of TestQueueRowFreshnessBrowser.test_unselected_row_pause_then_resume_reflects_fresh_state _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Yield the workbench base URL.
    
        When ``ACP_T25_BASE_URL`` is set (e.g. an ``acp run serve`` instance),
        drive that server; otherwise boot the real FastAPI app in-process on an
        ephemeral port with a throwaway ``run_root``.
        """
        external = os.environ.get("ACP_T25_BASE_URL", "").strip()
        if external:
            yield external.rstrip("/")
            return
    
        import tempfile
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_t25_server_"))
        # Set BEFORE importing the server module (module-level app reads it).
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_interaction.py:168: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_interaction.py:104: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of TestQueueRowFreshnessBrowser.test_batch_ops_invalidate_all_selected_row_caches _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Yield the workbench base URL.
    
        When ``ACP_T25_BASE_URL`` is set (e.g. an ``acp run serve`` instance),
        drive that server; otherwise boot the real FastAPI app in-process on an
        ephemeral port with a throwaway ``run_root``.
        """
        external = os.environ.get("ACP_T25_BASE_URL", "").strip()
        if external:
            yield external.rstrip("/")
            return
    
        import tempfile
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_t25_server_"))
        # Set BEFORE importing the server module (module-level app reads it).
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_interaction.py:168: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_interaction.py:104: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
=========================== short test summary info ============================
ERROR tests/test_frontend_startup_browser.py::test_stalled_catalogs_and_missing_viewer_do_not_block_shell[False]
ERROR tests/test_frontend_startup_browser.py::test_stalled_catalogs_and_missing_viewer_do_not_block_shell[True]
ERROR tests/test_frontend_startup_browser.py::test_failed_task_list_has_retry_and_recovers
ERROR tests/test_frontend_startup_browser.py::test_paging_keeps_counts_and_reuses_unchanged_rows
ERROR tests/test_frontend_viewer_framing_browser.py::TestMainViewerFraming::test_main_viewer_first_load_centers_model
ERROR tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser::test_completed_row_drops_stale_active_actions_and_offers_rerun_menu
ERROR tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser::test_unselected_row_pause_then_resume_reflects_fresh_state
ERROR tests/test_frontend_interaction.py::TestQueueRowFreshnessBrowser::test_batch_ops_invalidate_all_selected_row_caches
8 errors in 3.23s

```

### browser-affected.log

```text
EEEEE                                                                    [100%]
==================================== ERRORS ====================================
_ ERROR at setup of test_research_panels_do_not_cover_queue_controls[viewport0] _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of test_research_panels_do_not_cover_queue_controls[viewport1] _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of test_research_panels_do_not_cover_queue_controls[viewport2] _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
___ ERROR at setup of test_capability_numeric_fields_use_real_change_events ____

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        Mirrors the sibling browser suites: the server runs in a daemon thread
        against a temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_startup_browser.py:49: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_startup_browser.py:22: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
_ ERROR at setup of TestMainViewerFraming.test_main_viewer_first_load_centers_model _

isolated_browser_deployment = None

    @pytest.fixture(scope="session")
    def server_url(isolated_browser_deployment: None) -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        The server runs in a daemon thread; ``create_app`` is called with a
        temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        # Import the app factory — this is the canonical entry point.
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_viewer_framing_browser.py:118: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
tests/test_frontend_viewer_framing_browser.py:75: in _find_free_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
=========================== short test summary info ============================
ERROR tests/test_frontend_startup_browser.py::test_research_panels_do_not_cover_queue_controls[viewport0]
ERROR tests/test_frontend_startup_browser.py::test_research_panels_do_not_cover_queue_controls[viewport1]
ERROR tests/test_frontend_startup_browser.py::test_research_panels_do_not_cover_queue_controls[viewport2]
ERROR tests/test_frontend_startup_browser.py::test_capability_numeric_fields_use_real_change_events
ERROR tests/test_frontend_viewer_framing_browser.py::TestMainViewerFraming::test_main_viewer_first_load_centers_model
5 errors in 2.61s

```

### viewer-before.log

```text
E                                                                        [100%]
==================================== ERRORS ====================================
_ ERROR at setup of TestMainViewerFraming.test_main_viewer_first_load_centers_model _

    @pytest.fixture(scope="session")
    def server_url() -> Generator[str, None, None]:
        """Boot the real FastAPI app on an ephemeral port for the test session.
    
        The server runs in a daemon thread; ``create_app`` is called with a
        temporary ``run_root`` so no persistent state is written.
        """
        import os
        import tempfile
        from pathlib import Path
    
        import uvicorn
    
        tmp_root = Path(tempfile.mkdtemp(prefix="acp_browser_test_"))
    
        # Set ACP_RUN_ROOT BEFORE importing the server module, because server.py
        # has a module-level ``app = create_app()`` that reads this env var.
        os.environ["ACP_RUN_ROOT"] = str(tmp_root)
    
        # Import the app factory — this is the canonical entry point.
        from acp.api.server import create_app
    
        app = create_app(run_root=tmp_root)
>       port = _find_free_port()
               ^^^^^^^^^^^^^^^^^

tests/test_frontend_viewer_framing_browser.py:118: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

    def _find_free_port() -> int:
        """Return an OS-assigned ephemeral port."""
>       with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

tests/test_frontend_viewer_framing_browser.py:75: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 

self = <socket.socket fd=-1, family=0, type=0, proto=0>
family = <AddressFamily.AF_INET: 2>, type = <SocketKind.SOCK_STREAM: 1>
proto = 0, fileno = None

    def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
        # For user code address family and type values are IntEnum members, but
        # for the underlying _socket.socket they're just integers. The
        # constructor of _socket.socket converts the given argument to an
        # integer automatically.
        if fileno is None:
            if family == -1:
                family = AF_INET
            if type == -1:
                type = SOCK_STREAM
            if proto == -1:
                proto = 0
>       _socket.socket.__init__(self, family, type, proto, fileno)
E       PermissionError: [Errno 1] Operation not permitted

/usr/lib64/python3.11/socket.py:232: PermissionError
=========================== short test summary info ============================
ERROR tests/test_frontend_viewer_framing_browser.py::TestMainViewerFraming::test_main_viewer_first_load_centers_model
1 error in 2.34s

```

### browser-launch-probe.log

```text
Traceback (most recent call last):
  File "<stdin>", line 3, in <module>
  File "/home/xieningke/.local/lib/python3.11/site-packages/playwright/sync_api/_generated.py", line 16814, in launch
    self._sync(
  File "/home/xieningke/.local/lib/python3.11/site-packages/playwright/_impl/_sync_base.py", line 115, in _sync
    return task.result()
           ^^^^^^^^^^^^^
  File "/home/xieningke/.local/lib/python3.11/site-packages/playwright/_impl/_browser_type.py", line 101, in launch
    await self._channel.send(
  File "/home/xieningke/.local/lib/python3.11/site-packages/playwright/_impl/_connection.py", line 76, in send
    return await self._connection.wrap_api_call(
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/xieningke/.local/lib/python3.11/site-packages/playwright/_impl/_connection.py", line 632, in wrap_api_call
    raise rewrite_error(error, f"{parsed_st['apiName']}: {error}") from None
playwright._impl._errors.TargetClosedError: BrowserType.launch: Target page, context or browser has been closed
Browser logs:

<launching> /home/xieningke/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell --disable-field-trial-config --disable-background-networking --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-back-forward-cache --disable-breakpad --disable-client-side-phishing-detection --disable-component-extensions-with-background-pages --disable-component-update --no-default-browser-check --disable-default-apps --disable-dev-shm-usage --disable-edgeupdater --disable-extensions --disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,OptimizationHints,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion --enable-features=CDPScreenshotNewSurface --allow-pre-commit-input --disable-hang-monitor --disable-ipc-flooding-protection --disable-popup-blocking --disable-prompt-on-repost --disable-renderer-backgrounding --disable-updater-scheduler --force-color-profile=srgb --metrics-recording-only --no-first-run --password-store=basic --use-mock-keychain --no-service-autorun --export-tagged-pdf --disable-search-engine-choice-screen --unsafely-disable-devtools-self-xss-warnings --edge-skip-compat-layer-relaunch --disable-infobars --disable-search-engine-choice-screen --disable-sync --enable-unsafe-swiftshader --headless --hide-scrollbars --mute-audio --blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4 --no-sandbox --user-data-dir=/tmp/playwright_chromiumdev_profile-XqdSTY --remote-debugging-pipe --no-startup-window
<launched> pid=498
[pid=498][err] [1029/010439.170599:FATAL:content/browser/sandbox_host_linux.cc:41] Check failed: . shutdown: 不允许的操作 (1)
Call log:
  - <launching> /home/xieningke/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell --disable-field-trial-config --disable-background-networking --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-back-forward-cache --disable-breakpad --disable-client-side-phishing-detection --disable-component-extensions-with-background-pages --disable-component-update --no-default-browser-check --disable-default-apps --disable-dev-shm-usage --disable-edgeupdater --disable-extensions --disable-features=AvoidUnnecessaryBeforeUnloadCheckSync,DestroyProfileOnBrowserClose,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,MediaRouter,PaintHolding,ThirdPartyStoragePartitioning,BlockOriginHeaderModificationOnRedirect,Translate,AutoDeElevate,OptimizationHints,msForceBrowserSignIn,msEdgeUpdateLaunchServicesPreferredVersion --enable-features=CDPScreenshotNewSurface --allow-pre-commit-input --disable-hang-monitor --disable-ipc-flooding-protection --disable-popup-blocking --disable-prompt-on-repost --disable-renderer-backgrounding --disable-updater-scheduler --force-color-profile=srgb --metrics-recording-only --no-first-run --password-store=basic --use-mock-keychain --no-service-autorun --export-tagged-pdf --disable-search-engine-choice-screen --unsafely-disable-devtools-self-xss-warnings --edge-skip-compat-layer-relaunch --disable-infobars --disable-search-engine-choice-screen --disable-sync --enable-unsafe-swiftshader --headless --hide-scrollbars --mute-audio --blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4 --no-sandbox --user-data-dir=/tmp/playwright_chromiumdev_profile-XqdSTY --remote-debugging-pipe --no-startup-window
  - <launched> pid=498
  - [pid=498][err] [1029/010439.170599:FATAL:content/browser/sandbox_host_linux.cc:41] Check failed: . shutdown: 不允许的操作 (1)
  - [pid=498] <gracefully close start>
  - [pid=498] <kill>
  - [pid=498] <will force kill>
  - [pid=498] <process did exit: exitCode=null, signal=SIGTRAP>
  - [pid=498] starting temporary directories cleanup
  - [pid=498] finished temporary directories cleanup
  - [pid=498] <gracefully close end>


```

### origin.log

```text
....................................
```

### origin-regression.log

```text
============================= test session starts ==============================
platform linux -- Python 3.11.13, pytest-9.1.1, pluggy-1.6.0 -- /usr/bin/python3.11
cachedir: .pytest_cache
rootdir: /ACPServer/AutoCalcPlatform
configfile: pyproject.toml
plugins: anyio-4.15.1, cov-7.1.0, mock-3.15.1
collecting ... collected 1 item

tests/test_services_local_boundary.py::test_temporary_browser_origin_isolated_from_deployment Timeout (0:00:15)!
Thread 0x00001464c52ff640 (most recent call first):
  File "/usr/lib64/python3.11/selectors.py", line 468 in select
  File "/usr/lib64/python3.11/asyncio/base_events.py", line 1898 in _run_once
  File "/usr/lib64/python3.11/asyncio/base_events.py", line 608 in run_forever
  File "/usr/lib64/python3.11/asyncio/base_events.py", line 641 in run_until_complete
  File "/usr/lib64/python3.11/asyncio/runners.py", line 118 in run
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/_backends/_asyncio.py", line 2548 in run
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/_core/_eventloop.py", line 83 in run
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/from_thread.py", line 538 in run_blocking_portal
  File "/usr/lib64/python3.11/threading.py", line 982 in run
  File "/usr/lib64/python3.11/threading.py", line 1045 in _bootstrap_inner
  File "/usr/lib64/python3.11/threading.py", line 1002 in _bootstrap

Thread 0x00001464f91e1200 (most recent call first):
  File "/usr/lib64/python3.11/threading.py", line 327 in wait
  File "/usr/lib64/python3.11/concurrent/futures/_base.py", line 451 in result
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/_backends/_asyncio.py", line 2780 in run_sync_from_thread
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/from_thread.py", line 119 in run_sync
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/from_thread.py", line 303 in _spawn_task_from_thread
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/from_thread.py", line 384 in start_task_soon
  File "/home/xieningke/.local/lib/python3.11/site-packages/anyio/from_thread.py", line 340 in call
  File "/home/xieningke/.local/lib/python3.11/site-packages/starlette/testclient.py", line 350 in handle_request
  File "/home/xieningke/.local/lib/python3.11/site-packages/httpx/_client.py", line 1014 in _send_single_request
  File "/home/xieningke/.local/lib/python3.11/site-packages/httpx/_client.py", line 979 in _send_handling_redirects
  File "/home/xieningke/.local/lib/python3.11/site-packages/httpx/_client.py", line 942 in _send_handling_auth
  File "/home/xieningke/.local/lib/python3.11/site-packages/httpx/_client.py", line 914 in send
  File "/home/xieningke/.local/lib/python3.11/site-packages/httpx/_client.py", line 825 in request
  File "/home/xieningke/.local/lib/python3.11/site-packages/starlette/testclient.py", line 455 in request
  File "/home/xieningke/.local/lib/python3.11/site-packages/httpx/_client.py", line 1144 in post
  File "/home/xieningke/.local/lib/python3.11/site-packages/starlette/testclient.py", line 556 in post
  File "/ACPServer/AutoCalcPlatform/tests/test_services_local_boundary.py", line 273 in test_temporary_browser_origin_isolated_from_deployment
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/python.py", line 167 in pytest_pyfunc_call
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_callers.py", line 121 in _multicall
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_manager.py", line 120 in _hookexec
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_hooks.py", line 512 in __call__
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/python.py", line 1707 in runtest
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/runner.py", line 184 in pytest_runtest_call
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_callers.py", line 121 in _multicall
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_manager.py", line 120 in _hookexec
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_hooks.py", line 512 in __call__
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/runner.py", line 250 in <lambda>
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/runner.py", line 361 in from_call
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/runner.py", line 249 in call_and_report
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/runner.py", line 139 in runtestprotocol
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/runner.py", line 118 in pytest_runtest_protocol
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_callers.py", line 121 in _multicall
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_manager.py", line 120 in _hookexec
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_hooks.py", line 512 in __call__
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/main.py", line 408 in pytest_runtestloop
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_callers.py", line 121 in _multicall
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_manager.py", line 120 in _hookexec
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_hooks.py", line 512 in __call__
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/main.py", line 384 in _main
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/main.py", line 330 in wrap_session
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/main.py", line 377 in pytest_cmdline_main
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_callers.py", line 121 in _multicall
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_manager.py", line 120 in _hookexec
  File "/home/xieningke/.local/lib/python3.11/site-packages/pluggy/_hooks.py", line 512 in __call__
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/config/__init__.py", line 229 in _main
  File "/home/xieningke/.local/lib/python3.11/site-packages/_pytest/config/__init__.py", line 253 in _console_main
  File "/home/xieningke/.local/lib/python3.11/site-packages/pytest/__main__.py", line 9 in <module>
  File "<frozen runpy>", line 88 in _run_code
  File "<frozen runpy>", line 198 in _run_module_as_main

```

