# 20261021 独立复核诊断入口

从仓库根目录执行；脚本只在临时目录创建任务、缓存、数据库及本地浏览器测试数据，不提交任务到生产或真实 QC/LSF。

这些是复现剩余缺陷的诊断脚本，不是通过型验收套件。退出 0 不代表产品正确；必须阅读输出中 `reproduced`、原始科学数据是否保留、执行权及字节/画布事实。问题编号和判断依据见上级 `REPORT.md`。

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3.11 reports/architecture_recheck_20261021/reproductions/controller.py
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3.11 reports/architecture_recheck_20261021/reproductions/worker_claim_and_bootstrap.py
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3.11 reports/architecture_recheck_20261021/reproductions/worker_lost_write.py
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3.11 reports/architecture_recheck_20261021/reproductions/data.py
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3.11 reports/architecture_recheck_20261021/reproductions/frontend_science.py
```

最后一项需要已安装 Playwright/Chromium，并允许启动本地浏览器；它使用仓库真实 3Dmol/viewer 模块和受控网络边界。若只复跑 bounded 身份反例：

```bash
PYTHONPATH=src:. PYTHONDONTWRITEBYTECODE=1 python3.11 -c 'import json,runpy; m=runpy.run_path("reports/architecture_recheck_20261021/reproductions/frontend_science.py"); print(json.dumps(m["probe_bounded"](), indent=2))'
```

`run_browser_explicit.py` 只对真实 Chromium 添加显式 `--no-sandbox` 启动参数，运行原有四项浏览器断言；不修改产品、断言、DOM 或 3Dmol。其结果与默认启动配置的失败分别保留，避免混淆环境表现和 R11 的生命周期反例。
