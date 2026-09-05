# §14.2 Capability Evidence Table

Each of the ten scientific capabilities is mapped to a live, collectable test function.

| # | 能力 | 测试文件 | 测试函数 (node id) |
|---|------|----------|-------------------|
| 1 | PES坐标/路径选择 | tests/test_pes_search.py | tests/test_pes_search.py::TestPesContracts::test_scan_coordinate_roundtrip |
| 2 | 扫描profile | tests/test_pes_search.py | tests/test_pes_search.py::test_scan_five_frames_profile |
| 3 | TS虚频 | tests/test_batch_optimize.py | tests/test_batch_optimize.py::test_ts_imaginary_judgment_valid |
| 4 | 最低点虚频失败 | tests/test_batch_optimize.py | tests/test_batch_optimize.py::test_ts_frequency_failure_aborts_item |
| 5 | 混合TS/INT | tests/test_batch_optimize.py | tests/test_batch_optimize.py::test_mixed_ts_int_opt_freq_sp_thermo |
| 6 | 单item失败隔离 | tests/test_batch_optimize.py | tests/test_batch_optimize.py::test_item_failure_isolated |
| 7 | checkpoint+cache key | tests/test_batch_optimize.py | tests/test_batch_optimize.py::test_resume_skips_completed |
| 8 | IRC正反向+端点 | tests/test_irc.py | tests/test_irc.py::TestIrcBothDirections::test_irc_both_directions |
| 9 | 热化学输入完整性 | tests/test_thermochemistry.py | tests/test_thermochemistry.py::test_compute_full_inputs |
| 10 | 结果Artifact注册 | tests/test_scan_workflow.py | tests/test_scan_workflow.py::test_run_scan_writes_frames_and_trajectory_product |
