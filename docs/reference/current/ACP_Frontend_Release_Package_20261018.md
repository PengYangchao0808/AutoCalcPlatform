# ACP 前端静态发布包与回退步骤（2026-10-18）

- 生成：并行计划一路 A（Frontend 收口）todo T33（`.omo/plans/acp-parallel-plan-1-frontend.md`）；依据主方案 §38.6 路径约定、§38.9 发布/旧页面/回退。
- 发布集修订：branch `feat/frontend-plan1-closure` @ `899c7c028e4ec378550b1bef235043878cc359df`（本文件记录时的现盘实测；frontend/** 当时干净、与 HEAD 一致）。
- 原始证据（清单、原始 curl/TestClient 输出、演练日志）：`.omo/evidence/acp-parallel-plan-1-frontend/task-33-acp-parallel-plan-1-frontend.md` + `t33/`（含机器可查清单 `resource_listing.txt`）。本文件是面向部署/运维的**交付摘要**；`.omo` 为本地工件不入提交。
- 三态纪律：PASS / FAIL / NOT_VERIFIED；生产发布与本机 systemd 重启不在本计划范围（交接见 §6）。

---

## 1. 发布资源集（静态文件集）

| 项 | 值 |
|---|---|
| 默认入口 | `frontend/ACP_Workbench_v2.html`（`GET /`），sha256 `b3dd9c0351d9f7175c06032b11f35cbf8e2ee4cce04df414649b734dc2efc27c`，82,478 bytes |
| 默认页被引用资源 | **58**（48 `<script src>` + 10 `<link rel="stylesheet">`），合计 2,976,817 bytes；零缺失、零重复、全在盘（逐条 sha256 见证据清单） |
| 回退单元（交付集） | `frontend/**` 全部 **96** 个文件（含默认页未引用的 `js/module_tests/**` 测试件与 legacy `ACP_Workbench.html`） |
| 静态挂载 | `js/**` → `/js`（route `frontend-js`）、`css/**` → `/css`（route `frontend-css`），均为 `_RevalidatingStaticFiles`（`Cache-Control: no-cache` + ETag）；HTML 由 `GET /` 路由读取 |
| 发布集聚合摘要 | `f15381ea7e19d103b2895f773e3d3e0015a53dbc1a1a25afa553ddf1e86a5136`（命令见 §1.2，**必须带 `LC_ALL=C`**：主机为 zh_CN.UTF-8 时 `sort` 的 locale 排序会改变聚合值） |

### 1.1 相对冻结基线 F0 的变化（`f410ac9d` → 本修订）

- 引用面：51 → 58（**+7 新模块**）：`js/client/system-endpoints.js`、`js/visualization/scene_instance.js`、`js/workbench/controllers/system_resources_controller.js`、`js/workbench/features/wizard/{upload-parse,preview-validate,draft-form,submit-edit}.js`；14 个既有文件摘要更新（含 `wizard.js` 拆分后瘦身、`energy.js`/`reaction-builder.js`/`structure_viewer.js` 等）；0 个被引用文件从 HTML 移除。
- 目录面：77 → 96 文件（新增 19 个中 13 个为 `js/module_tests/*` 测试件）。
- 兼容窗口规则：**不删除旧资源**；旧页面可能仍加载的文件保持在线直至明确兼容窗口结束（§38.9）。

### 1.2 可复现校验命令

```bash
cd repo-root
# 资源清单重生成（HTML 现盘解析；输出 58 行 + 头）
/home/redacted-user/.venvs/acp-frontend/bin/python \
    .omo/evidence/acp-parallel-plan-1-frontend/t33/collect_manifest.py

# 发布集聚合摘要（期望 f15381ea...）
find frontend -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum

# 冷/暖缓存与字节一致（起本地隔离服务于 8898，结束自动杀进程并给出端口空闲回执）
/home/redacted-user/.venvs/acp-frontend/bin/python \
    .omo/evidence/acp-parallel-plan-1-frontend/t33/cache_check.py
```

## 2. 装载顺序与缓存语义（T33 实测 PASS）

- 装载顺序（文档序）：`<head>` 1 个 **async** vendor 3Dmol（`js/vendor/3Dmol-2.5.5.min.js`，`id="viewer-library"`，行 7；经 `load`/`error` 监听，缺库时 3D 视图原样降级——molecule-view.js:113-122）；`<head>` 10 个 CSS（行 8–17）；body 尾部 47 个经典同步脚本（行 1211–1257，依赖顺序执行，`tests/test_frontend_integration.py` 守护）。无 inline script、无 ESM module、无 favicon/preload。
- 缓存语义（实测）：58/58 资源冷加载 200 + 字节一致 + `Cache-Control: no-cache` + ETag；同上下文重载（`If-None-Match`）58/58 → **304**；普通硬刷新 58/58 → 200 字节一致；负控制 `/js/__does_not_exist__.js` → 404。
- 旧标签页与原子性（F-T4-1 延续）：已打开的旧标签页继续运行内存中已执行的脚本；`no-cache` 仅保证**下次导航逐文件 revalidate**（未变 304 / 已变 200），**不保证多文件原子替换**——发布过程中正在刷新的页面可能混到新/旧文件。HTML 文档面当前仍无 `Cache-Control`/ETag（LOW，登记 F-T4-1，非发布阻塞项；每次导航全量拉取 HTML 本身即最新引用面）。
- 服务重启后：未变文件 304（省流），已变文件 200 全量——用户刷新即可获得新资源，无需清缓存操作。

## 3. 回退步骤（恢复资源集；具体路径）

前提：目标修订可引用（`5518aa6f` 为本次发布集；更深回退到计划前基线用 `f410ac9d`）。**只恢复 `frontend/**`；不碰 DB/attempt/工件；不重发任何任务命令**（§4）。

```bash
# 0) （可选）先停服，避免半还原状态被访问；允许短暂半态可跳过
sudo systemctl stop acp

# 1) 从发布修订恢复前端资源树（回退唯一被改动的目录）
cd repo-root
git checkout 899c7c028e4ec378550b1bef235043878cc359df -- frontend/

# 2) 校验：清单重生成 + 聚合摘要（期望 5518aa6f 对应值 f15381ea...）
/home/redacted-user/.venvs/acp-frontend/bin/python \
    .omo/evidence/acp-parallel-plan-1-frontend/t33/collect_manifest.py
find frontend -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum

# 3) 恢复服务
sudo systemctl restart acp

# 备选（无 git 环境）：用发布包内 frontend tarball 覆盖
#   tar -xzf <release>/frontend-5518aa6f.tar.gz -C repo-root
#   然后仍执行步骤 2–3（聚合校验不可省略）
# 更深回退到计划前基线：同上把修订换成 f410ac9d（聚合期望 fe9da69f...）
```

**路径 checkout 的残留文件语义（本地演练实证）**：`git checkout <旧修订> -- frontend/` 只还原旧修订中存在的文件，**不会删除**后来新增的文件。演练确认：覆盖后 77 个 F0 文件全部逐字节一致，19 个 F0 后新增文件（6 个生产模块 + 13 个 module_tests 测试件）残留但被恢复的 F0 HTML 零引用、行为惰性（清单见 T33 证据 §3）。若需逐字节还原旧树，另按 `git diff --diff-filter=A <旧修订>..<新修订> --name-only -- frontend/` 列出的文件显式删除。`git restore --source=<rev> frontend/` 语义相同。

## 4. 回退不触碰清单（rollback non-touch list）

- **DB**：`acp_jobs.db` 以及 `jobs`/`tasks`/`stage_tasks`/`artifacts` 等表不读不写不改。
- **attempt**：`WORK/00_RUNTIME/attempts/**` 不动。
- **工件/结果**：`WORK/**`、`RESULT/**`、远端结构缓存与远程工作目录不动。
- **不重发命令**：不触发 pause/unpause/continue/rerun/purge，不写命令队列，不做 bsub/SSH/SFTP 远程提交或结果拉取；刷新后按现有任务查询恢复显示（§38.9）。
- 回退范围 = 仅 `frontend/**` 文件替换；S1（服务）如已上线仍须满足 F0＋S1 兼容门。

## 5. 本地演练记录（PASS）

`t33/rollback_drill.sh` 在 `/tmp/t33-rollback` 只读副本上执行，共享树零改动（演练后 `git status --porcelain -- frontend/` 为空、wizard.js 摘要保持 HEAD 值）：

- 代表文件恢复（`frontend/js/workbench/features/wizard.js`）：scratch 摘要 `111e51c2...`（HEAD）→ `9e70922d...`（F0，与 `git show f410ac9d:…` 及 T4 清单三方一致）。
- 全目录恢复模拟：F0 归档解出 77 文件 → 聚合 `fe9da69f...` 与 F0 记录一致。
- 覆盖语义模拟（HEAD 树上覆 F0）：77/77 逐字节一致；19 个残留文件清单见 T33 证据 §3。

## 6. 交接与范围声明

- **接收方 = 部署/运维 owner**：本发布包（资源集 + 校验命令 + 回退步骤）交付部署/运维，用于实际发布与回退操作。
- **B-T71（集成发布）验证项（交接）**：资源清单交 B-T71 验证「**打开的旧页面 + 资源缓存 + 新服务**」三者组合的行为（本计划只做发布准备与本地演练；实际部署由集成发布任务在终验通过后执行）。
- **明确不在本计划范围**：生产发布、systemd 重启/切换、发布包分发介质制作；如上步骤为交付给运维的操作描述，本计划未对其在生产环境执行。
- 本计范围外若需版本化 URL/新路由：§38.6 规定本轮不新建静态路由、不加 manifest API、不加构建步骤。

---

*证据索引：验收清单与原始输出 → `.omo/evidence/acp-parallel-plan-1-frontend/task-33-acp-parallel-plan-1-frontend.md`；机器可查清单 → 同目录 `t33/resource_listing.txt`（58 行：order/kind/path/sha256/bytes/served_via/html_line）。*
