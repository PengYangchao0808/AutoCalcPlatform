# M5-03 独立操作者验收 Runbook

> 你是独立操作者。本手册假设你**不访问实现仓库**、只用下面的制品库与命令操作。
> 任何时候遇到与手册不符的行为：原样记录（命令、输出、截图/拍照），不要尝试修复——那正是验收要发现的东西。

## 0. 你需要的东西

| 物品 | 位置 |
|---|---|
| 制品库（约 481MB，含四个发行包、第三方依赖 wheelhouse 与覆盖回执） | `/ACPServer/acp2-artifacts/remediation/m5-03/`（先整体复制到干净机，如 `~/acp2-m503-store/`） |
| BOM（组件清单+sha256） | 制品库内 `bom/bom.current.json` |
| 覆盖账本（本次声明） | 制品库内 `contracts/release/evidence-coverage.current.json` |
| 覆盖回执（随库交付） | 制品库内 `evidence/receipts/` + `evidence/coverage-receipts-manifest.json` |
| 干净机 | ARM64 主机（10.15.92.10，`ACPtest@`）或任何无本仓库 checkout 的隔离环境 |
| Python 3.11 / Node 22.19 / pnpm 11.28.5 | ARM64 机已备（`~/acp2-env.sh`） |

**边界声明（重要）**：若在 ARM64 机执行，它没有 LSF/ORCA/xTB/模型凭据——这些行在声明中本就是 `NOT_VERIFIED`，你只需核对声明如实，不需要执行科学计算。你验收的是**安装、服务、协议、隔离与声明一致性**。

## 1. 制品完整性（5 分钟）

```bash
cd ~/acp2-m503-store
python3 - <<'PY'
import json, hashlib, pathlib, os
bom = json.load(open("bom/bom.current.json"))
# BOM URIs are build-host absolute paths; the store is copied as a whole, so
# resolve every component relative to the URIs' common store root.
root = pathlib.Path(os.path.commonpath([c["uri"] for c in bom["artifacts"]]))
bad = []
for c in bom["artifacts"]:
    p = pathlib.Path(".") / pathlib.Path(c["uri"]).relative_to(root)
    if not p.is_file():
        bad.append((c["name"], "MISSING")); continue
    d = hashlib.sha256(p.read_bytes()).hexdigest()
    if d != c["sha256"]:
        bad.append((c["name"], "DIGEST"))
print("artifacts:", len(bom["artifacts"]), "store root:", root, "bad:", bad)
assert not bad
PY
```
**预期**：`bad: []`。记录输出。

## 2. 四发行安装 + 互斥规则（10 分钟）

制品库自带第三方依赖 wheelhouse（x86_64 与 ARM64 两个平台），下面的安装全程
`--no-index`，不允许联网：

```bash
cd ~ && python3.11 -m venv venv-root && ./venv-root/bin/pip install --no-index \
  --find-links ~/acp2-m503-store/python/wheelhouse \
  --find-links ~/acp2-m503-store/python/root acp
./venv-root/bin/acp --version          # 预期 `acp 0.3.0`
python3.11 -m venv venv-roles && ./venv-roles/bin/pip install --no-index \
  --find-links ~/acp2-m503-store/python/wheelhouse \
  --find-links ~/acp2-m503-store/python/roles cccp acp-worker acp-web
# 互斥负例：同一 venv 混装后，产品自身必须在三个入口拒绝
python3.11 -m venv venv-mix && ./venv-mix/bin/pip install --no-index \
  --find-links ~/acp2-m503-store/python/wheelhouse \
  --find-links ~/acp2-m503-store/python/root acp >/dev/null 2>&1
./venv-mix/bin/pip install --no-index \
  --find-links ~/acp2-m503-store/python/wheelhouse \
  --find-links ~/acp2-m503-store/python/roles acp-web 2>&1 | tail -2
./venv-mix/bin/acp --version; echo "exit=$?"                  # 预期 78 + mutually_exclusive_distributions
echo 'from acp.api.server import create_app; create_app()' | ./venv-mix/bin/python -  # 预期 TypedError
./venv-mix/bin/acp mcp serve --transport stdio; echo "exit=$?"  # 预期 78，不是缺少 --controller
```
**预期**：root 装完后 `acp --version` 输出 `acp 0.3.0`；混装后 pip 可能"成功"，但
**产品**在 CLI、FastAPI `create_app`、`mcp serve` 三处都以 typed 错误
`mutually_exclusive_distributions`（退出码 78）拒绝启动。若 pip 自身就冲突报错，
原样记录 pip 输出。另：root venv 未装 `[remote]` 时 `./venv-root/bin/acp doctor`
应输出一行 `NOT_VERIFIED`（paramiko 未安装）并正常结束，**不得**抛
`ModuleNotFoundError`；把它一并记录。

## 3. 服务与协议（10 分钟）

```bash
./venv-roles/bin/acp run serve --port 8765 &     # acp-web 角色的服务入口
# 若 8765 已被占用（Address already in use），换一个空闲端口并全程使用它，例如：
#   ./venv-roles/bin/acp run serve --port 8766 &
#   curl -s localhost:8766/api/capabilities
sleep 3; curl -s localhost:8765/api/capabilities | head -c 300   # 预期能力 JSON
# MCP 握手（stdio，必须指向一个已启动的 Controller）：
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"m503-op","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | \
  ./venv-roles/bin/acp mcp serve --transport stdio --controller http://127.0.0.1:8765 | head -4
```
**预期**：initialize 回 `2025-06-18`；tools/list 恰好 **8** 个 `acp_*` 工具。记录 JSON 响应。
`acp mcp serve` 必须带 `--controller`（或环境变量 `ACP_CONTROLLER_URL`），否则按 usage 错误退出（exit 2）。

## 4. Workbench 静态资产（5 分钟）

浏览器打开服务根路径：页面加载、无意外控制台错误（只允许与本次判定无关的浏览器扩展/
缓存提示；`GET /api/v2/science/capabilities` 不应在未选择项目时自动发出）、
选择项目后 `/api/v2/science/capabilities?project_id=<所选项目>` 返回 200；
尝试 `GET /js/../etc/passwd` 应 404。记录截图与浏览器 Console 截图。

## 5. RPH 离线安装 + doctor（10 分钟）

```bash
mkdir ~/rph-op && cp ~/acp2-m503-store/rph/install-surface/* ~/rph-op/ && cd ~/rph-op
pnpm install --offline --frozen-lockfile && node verify-install.mjs   # 预期 RPH INSTALL VERIFY PASS
node <cli bin path> doctor                                             # 预期 9 项静态检查 ok
```

## 6. 声明一致性核对（10 分钟）

打开 `contracts/release/evidence-coverage.current.json`：
- 抽 3 行 `result: "PASS"` —— 打开其 `receipt` 指向的文件：**回执已随制品库交付**，
  位于 `evidence/receipts/<receipt 相对路径>`；用 `evidence/coverage-receipts-manifest.json`
  里的 `sha256` 核对（无需实现仓库）：
  ```bash
  cd ~/acp2-m503-store
  python3 - <<'PY'
  import json, hashlib, pathlib
  root = pathlib.Path(".")
  manifest = json.load(open(root / "evidence/coverage-receipts-manifest.json"))
  bad = []
  for item in manifest["receipts"]:
      path = root / "evidence/receipts" / item["path"]
      if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
          bad.append(item["path"])
  print("receipts:", len(manifest["receipts"]), "bad:", bad)
  assert not bad
  PY
  ```
- 确认这些行**如实为 NOT_VERIFIED**：DA 科学命题（H4）、LSF DA、生产模型凭据（M3-03）、worker-hosted COMPUTE（DEFERRED）；
- 确认 `scope.json` 的 `candidate_full_gate.allowed == false`（你未签收前不放行）。

## 7. 签收回执

把你每步的命令+输出+截图整理为一个目录 `m503-operator-receipt/`（含：执行环境说明——ARM64 干净机/隔离边界、日期、操作者名），交回给实现侧归档。**任何一步失败都原样记录并停止该步**——失败本身是有效验收结果。

## 明确不在你范围（声明已如实 NOT_VERIFIED，无需执行）
真实 QC/LSF/DA 科学计算、真实模型调用、生产凭据、生产部署/systemd。
