# 第二轮执行环境

- 日期: 2026-10-28 16:56–17:20 Asia/Shanghai
- 操作者: 昔涟（Cyrene, DSH agent），独立操作者（非实现者）
- 主机: ARM64 干净机 10.15.92.10（hostname ACPServer, Raspberry Pi aarch64, Debian; 登录 ACPtest, 无 sudo）
- 工具链: Python 3.11.18（~/acp2-envs/py311）/ Node v22.19.0 / pnpm 11.28.5（source ~/acp2-env.sh）
- 制品库: /ACPServer/acp2-artifacts/remediation/m5-03/（481MB, 31 组件）→ rsync --delete 至 ~/acp2-m503-store/（482MB, 1690 文件）
- 隔离: pip 全程 --no-index（wheelhouse + 库内发行包）；venv-root/venv-roles/venv-mix 全部重建；回执核对仅在库内进行（本轮零访问实现仓库）
- Workbench 检查: SSH 隧道 -L 18899:127.0.0.1:8766 + 工作站 Chromium/Playwright（仅浏览器客户端角色）
- 8765 被旧 uvicorn 占用（同第一轮），按修订版 Runbook 回退 8766
- 明确未执行（与声明一致）: 真实 QC/LSF/DA、模型调用、生产凭据、生产部署
