# M5-03 独立操作者验收 — 执行环境说明

- **日期**: 2026-10-28（11:39–12:30 Asia/Shanghai）
- **操作者**: 昔涟（Cyrene, DSH agent）— 非实现者，独立操作者角色
- **执行主机**: ARM64 干净机 `10.15.92.10`（hostname `ACPServer`，Raspberry Pi, `Linux 6.18.34+rpt-rpi-v8 aarch64`, Debian）
- **登录用户**: `ACPtest`（无 sudo）
- **工具链**: Python 3.11.18（`~/acp2-envs/py311`）/ Node v22.19.0（`~/acp2-tools/node-v22.19.0-linux-arm64`）/ pnpm 11.28.5（corepack，需 `source ~/acp2-env.sh`）
- **制品库**: 由实现侧 `/ACPServer/acp2-artifacts/remediation/m5-02/`（252MB）经 rsync 整体复制到 `~/acp2-m503-store/`（253MB / 1577 文件），验收全程只消费该副本
- **隔离边界**: 所有安装/服务/协议验证均在新近创建的 venv（`venv-root` / `venv-roles` / `venv-mix`）与 `~/rph-op` 内进行，pip 对四个发行包一律 `--no-index --find-links <制品库>`

## 环境偏差（如实记录）
1. **干净机不干净**：端口 8765 被一个自 2026-10-17 起运行的 `uvicorn acp.api.server:app`（`/ACPServer/AutoCalcPlatform/.venv`，属主 xieningke）占用；主机上存在 `/ACPServer/AutoCalcPlatform` checkout。ACPtest 无权停止该进程，验收服务改用 **8766** 端口。
2. **Workbench 浏览器检查**：Pi 无 GUI 浏览器；经 SSH 隧道（`-L 18899:127.0.0.1:8766`）由工作站 Chromium（Playwright）访问打包服务的 localhost 端点，控制台错误与截图为真实浏览器采集。工作站仅充当浏览器客户端。
3. **声明一致性回执核对**：覆盖账本 PASS 行的 `receipt` 指向 `reports/...` 仓库路径，回执文件未随制品库交付；digest 核对使用了实现仓库副本（`/ACPServer/acp2-remediation-integration`）作交叉核对 — **该项为非独立验证**，已在结果中标注。

## 明确不在验收范围（与声明一致，未执行）
真实 QC/LSF/DA 科学计算、真实模型调用、生产凭据、生产部署/systemd。
