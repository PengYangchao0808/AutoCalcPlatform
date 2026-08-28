# F1 — DoD §19 Ten-Item Sign-Off Table

**Audit date**: 2026-08-28
**Branch**: refactor/calc-cleanup
**Source**: `docs/ACP_Calculation_Workflow_Refactor_Cleanup_Plan.md` §19 (lines 1141-1154)

## Sign-Off Table

| # | DoD Item | Corresponding Todo(s) | Test(s) | Evidence | Status |
|---|----------|----------------------|---------|----------|--------|
| 1 | UI 中没有"阶段工作流"、Lowconfirm、Highconfirm 和 S1-S4 | 36, 37, 43 | `test_acp_api.py::test_workflow_catalog_excludes_retired`, `test_architecture_invariants.py::test_batch_engine_no_stage_symbols`, `frontend_removed_tokens` gate | `36-happy.txt`, `37-happy.txt`, `43-happy.txt`, gate exit 0 | ✅ PASS |
| 2 | `optfreq`、`optfreqsp` 不再作为活动任务 | 36, 37, 39 | `test_acp_workflow_retirement.py::test_manager_rejects_retired_workflow`, `wave6_zero` gate, `final_optfreq_terms` gate | `36-happy.txt`, `37-happy.txt`, `39-happy.txt`, `catalog-retired-ids-final.txt` | ✅ PASS |
| 3 | BatchOptimize 可以覆盖优化、频率、单点和热化学的所有组合 | 18, 19, 21 | `test_batch_optimize.py::test_mixed_ts_int_opt_freq_sp_thermo`, `test_batch_optimize.py::test_models`, `test_batch_optimize.py::test_entry` | `18-happy.txt`, `19-happy.txt`, `21-happy.txt` | ✅ PASS |
| 4 | IRC 可以从任意已完成 TS Artifact 独立提交 | 25, 26, 28 | `test_irc.py::test_irc_both_directions`, `test_irc.py::test_workflow`, `test_acp_api_v1.py::test_run_irc_endpoint` | `25-happy.txt`, `26-happy.txt`, `28-happy.txt` | ✅ PASS |
| 5 | BatchOptimize 与 IRC 没有代码级依赖 | 19, 23, 27 | `test_architecture_invariants.py::test_batch_schema_rejects_irc`, `test_architecture_invariants.py::test_batch_no_irc`, `test_architecture_invariants.py::test_no_irc_calculation_step`, `wave4_batch_no_irc` gate, `wave4_endpointprovider` gate | `19-happy.txt`, `23-happy.txt`, `27-happy.txt`, gates exit 0 | ✅ PASS |
| 6 | PESsearch、BatchOptimize 和 IRC 都能独立运行、暂停、继续和查看结果 | 15, 21, 26, 33 | `test_checkpoint_continue.py::test_simple_workflow_resume_skips_done`, `test_batch_optimize.py::test_entry`, `test_irc.py::test_workflow`, `test_pes_search.py::test_entry_from_artifact` | `15-happy.txt`, `21-happy.txt`, `26-happy.txt`, `33-happy.txt` | ✅ PASS |
| 7 | 新任务只写统一结果协议 | 13, 14, 20 | `test_calculation_executor.py::test_three_step_plan`, `test_acp_workflows_simple.py::test_run_optimize_manifest_only`, `wave2_no_result_summary` gate, `wave3_no_batchmanifest` gate | `13-happy.txt`, `14-happy.txt`, `20-happy.txt`, gates exit 0 | ✅ PASS |
| 8 | 历史任务仍可只读查看 | 9, 34, 41, 42 | `test_legacy_read_only.py`, `test_legacy_layout_compat.py`, `test_acp_api_mechanism_studies.py::test_list_readonly`, `test_acp_api_mechanism_studies.py::test_create_returns_410` | `09-happy.txt`, `34-happy.txt`, `41-happy.txt`, `42-happy.txt` | ✅ PASS |
| 9 | 旧机制代码不再被活动模块 import | 46, 47 | `test_architecture_invariants.py::test_cccp_engine_gone`, `final_mechanism_imports` gate, `wave8_confsearch_decoupled` gate, `pre_delete_mechanism_external` gate | `46-happy.txt`, `47-happy.txt`, `52-happy.txt`, gates exit 0 | ✅ PASS |
| 10 | 完整测试、静态检查和远程/本地执行一致性验证通过 | 52 | `52-happy.txt` (full sweep: pytest gate + slow tests + ruff + final gates + remote parity), `test_remote_phase6.py` parity tests | `52-happy.txt` | ✅ PASS |

## Summary

All 10 DoD items: **PASS** ✅

Each item has:
- Corresponding todo(s) from the plan
- Concrete test function name(s) that verify the item
- Evidence file(s) or gate output confirming pass

No "未覆盖" items.
