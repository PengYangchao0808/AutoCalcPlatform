# 前端一次性生成器归档说明 — generate_energy_renderer.js

日期：2026-10-16。

`generate_energy_renderer.js`（原位于 `frontend/js/module_tests/generate_energy_renderer.js`，现已归档至本目录）是 H2 外置时期的一次性图表生成器：从 compat 内联源逐字抽取纯 energy 渲染函数族，生成 `frontend/js/visualization/energy_chart_renderer.js` 并输出符号/依赖/外部调用点报告。归档原因：其输入 `frontend/js/workbench/compat/workbench-inline.js` 已在 H2 外置后的拆分（compat/api.js、features/*.js 等）中不复存在，脚本运行即 ENOENT，**不可再运行**；它也不在默认页面脚本列表，`module_tests/run_all.js` 仅收集 `.test.js` 文件、从不执行它。其历史产物现用渲染器 `frontend/js/visualization/energy_chart_renderer.js` 已存在且由 `ACP_Workbench_v2.html` 正常加载——切勿试图重新运行本生成器覆盖该现用文件。抽取时的符号/依赖全表证据见 `.omo/evidence/frontend-arch-20261016/energy-renderer-symbols.json`。
