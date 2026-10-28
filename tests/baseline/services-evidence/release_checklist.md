# Release checklist — todo 76 执行清单（todo 71 交付，F1—F4 全 APPROVE 后才执行）

- **Owner:** B 路集成写者（todo 76 executor）
- **本文件:** `tests/baseline/services-evidence/release_checklist.md`（todo 71 交付的 tracked 发布清单）
- **Created:** 2026-10-19 · Plan: `.omo/plans/acp-parallel-plan-2-services.md` todo 71（B13-6）/ 执行归 todo 76（B13-8）
- **权威背景文档:** `docs/reference/current/ACP_Services_Release_Prep_20261019.md`（兼容矩阵 §2 / schema 回退 §3 / unit 收口 §4 / 演练 §5）
- **性质:** 执行前逐项勾选；执行后本文件各 `[ ]` 改 `[x]` 并在 todo 76 evidence 中留回执。**F1—F4 未全部 APPROVE 前不得勾选任何执行项（G0 之后的项全部保持未勾）。**

## G0 — 硬门（全部 APPROVE 才继续）

- [ ] F1 APPROVE 记录在案（引用路径：____________）
- [ ] F2 APPROVE 记录在案（引用路径：____________）
- [ ] F3 APPROVE 记录在案（引用路径：____________）
- [ ] F4 APPROVE 记录在案（引用路径：____________）
- [ ] 发布产物与 C-T54 所验候选产物可核对（评审 R4；产物校验和记录：____________）
- [ ] 兼容矩阵（Release_Prep §2 M1—M6）「依据」列测试在**发布候选提交**上全部绿或逐行注明 NOT_VERIFIED

## G1 — 版本 lockstep（4 文件同一版本）

```bash
cd /ACPServer/AutoCalcPlatform
grep -n '__version__' src/acp/__init__.py src/cccp/__init__.py src/cccp/version.py
grep -n '^version' pyproject.toml
# 期望：四处均为同一版本号（当前 0.2.0；发布新版本时先同步 4 处再执行）
```

- [ ] 4 处版本一致：____________

## G2 — 扩展前快照（schema 回退点；Release_Prep §3.5）

```bash
# 生产 run_root 以 systemctl cat acp / 启动日志中的 "ACP run root" 为准（模板为 /var/lib/acp/runs）
RUN_ROOT=/var/lib/acp/runs
COMMIT=$(git rev-parse HEAD)
sqlite3 "$RUN_ROOT/acp_jobs.db" ".backup $RUN_ROOT/pre_release_${COMMIT}.db"
# 任务树关键目录打包（WORK/00_RUNTIME、RESULT、调度 markers）
tar -czf "$RUN_ROOT/pre_release_${COMMIT}_tree.tgz" -C "$RUN_ROOT" \
  --exclude='*/WORK/00_CACHE' '*/WORK/00_RUNTIME' '*/RESULT' '*/job.json' '*/task.json' 2>/dev/null || true
ls -la "$RUN_ROOT"/pre_release_${COMMIT}*
```

- [ ] DB `.backup` 快照存在且可读（路径：____________）
- [ ] 任务树快照存在（路径：____________；或逐任务缺失原因：____________）

## G3 — 候选提交守卫套件（精确命令，全绿才发布）

```bash
cd /ACPServer/AutoCalcPlatform
python3.11 -m pytest tests/test_services_*.py -q
# 基线参考：1348 passed / 1 failed（F-3 runtime sqlite3 守卫，closure §6；发布时 F-3 状态：____）
python3.11 -m pytest tests/test_acp_services_isolation.py tests/test_architecture_invariants.py -q
python3.11 -m pytest tests/test_f4_scope_audit.py -q
python3.11 -m pytest tests/test_packaging_assets.py tests/test_services_packaging_roles.py -q
python3.11 -m pytest tests/test_scheduler_markers_audit.py tests/test_result_publication.py -q
python3.11 -m pytest tests/test_execution_integrity_e2e.py tests/test_checkpoint_resume_semantics.py \
  tests/test_recovery_fixtures_smoke.py tests/test_recovery_cross_version.py -q
python3.11 -m pytest tests/test_services_release_prep.py -q
python3.11 scripts/check_grep_gates.py --suite architecture-remediation
python3.11 -m pytest tests/test_acp_api_v1.py -q   # 旧 GET 兼容面（M1 行依据）
```

- [ ] 服务套件全绿（回执计数：____________；F-3 状态：____________）
- [ ] 隔离/架构守护绿
- [ ] F4 scope 门绿
- [ ] 打包资产/角色矩阵绿
- [ ] markers/发布门绿
- [ ] 执行完整性/恢复门绿
- [ ] release-prep 文档守护绿
- [ ] grep gates exit 0
- [ ] 旧 GET 兼容面绿

## G4 — unit 收口（Release_Prep §4.2；禁手改 `/etc/systemd/system/acp.service`）

```bash
# 核对当前 drift（只读）与决策路径
systemctl cat acp
# 路径 A：定制行并入 scripts/install_systemd.sh 模板后重新生成
# 路径 B：接受模板现状（核对 start_acp.sh 能否解析生产 venv /opt/acp/venv）
sudo bash scripts/install_systemd.sh --user xieningke
systemctl cat acp > /ACPServer/AutoCalcPlatform/.omo/evidence/acp-parallel-plan-2-services/task-76-unit-snapshot.txt
```

- [ ] 路径选择：A / B（圈一）；模板变更 commit：____________（A 时）
- [ ] 重新生成后 `systemctl cat acp` 快照已存 evidence
- [ ] `systemctl is-active acp` → `active`

## G5 — 重启与冒烟

```bash
sudo systemctl restart acp            # unit 由 G4 重新生成；非手改
sudo journalctl -u acp -n 50 --no-pager
acp doctor
curl -sf http://127.0.0.1:8765/ -o /dev/null && echo PAGE_OK
curl -sf http://127.0.0.1:8765/api/status
# 一个闭环（本地，控制面级）：
python3.11 -m pytest tests/test_services_cli_offline_loop.py -q
```

- [ ] restart 后 active；journal 无 fatal
- [ ] `acp doctor` exit 0
- [ ] 默认页 200 + `/api/status` 正常
- [ ] 离线闭环套件绿

## G6 — 在途任务兼容核对（矩阵 M2/M4 行）

- [ ] 重启前在途任务清单已记录（job_id × 状态：____________）
- [ ] 重启后各在途任务状态未被破坏（RUNNING 收养/PAUSED 保持/QUEUED 派发；0 重复 bsub 证据：____________）
- [ ] 旧页面窗口：旧 GET 冒烟（浏览器或 curl 旧端点）通过（回执：____________）
- [ ] 旧 worker/旧 release：远端在途 attempt 未被新 release 覆盖（回执：____________ 或 NOT_VERIFIED + 原因）

## G7 — 发布记录与收尾

- [ ] 发布记录写入 `.omo/evidence/acp-parallel-plan-2-services/task-76-acp-parallel-plan-2-services.md`（提交/构建/重启/冒烟/unit 快照/在途核对）
- [ ] 本清单勾选完成并随发布记录引用
- [ ] F5（结果核对）已知会；未回写 F1—F4 结论

## 禁止项（任何一步违反即中止发布）

1. F1—F4 未全 APPROVE 前执行 G2 及之后任何项。
2. 手改 `/etc/systemd/system/acp.service`（一切 unit 变更经 `scripts/install_systemd.sh`）。
3. 无兼容矩阵（Release_Prep §2）或无 G2 快照即重启。
4. 破坏在途任务（重启前不 pause/cancel 用户任务；purge 只在用户显式请求时）。
5. 发布产物与 C-T54 候选不可核对（评审 R4）。
