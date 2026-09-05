"""Tests for stage_labels — Chinese display labels for workflow stage names."""

from __future__ import annotations

from acp.core.stage_labels import STAGE_LABELS_ZH, stage_label

# ── Test 1: All 9 PES stage keys return their Chinese labels ─────────────

PES_STAGE_EXPECTED: dict[str, str] = {
    "prepare": "准备",
    "materialize_input": "输入结构",
    "validate_coordinate": "坐标验证",
    "run_relaxed_scan": "松弛扫描",
    "extract_frames": "提取帧",
    "run_single_points": "单点计算",
    "build_profile": "构建能量剖面",
    "select_candidates": "筛选候选结构",
    "finalize": "完成",
}


class TestPESStageLabels:
    def test_prepare(self) -> None:
        assert stage_label("prepare") == "准备"

    def test_materialize_input(self) -> None:
        assert stage_label("materialize_input") == "输入结构"

    def test_validate_coordinate(self) -> None:
        assert stage_label("validate_coordinate") == "坐标验证"

    def test_run_relaxed_scan(self) -> None:
        assert stage_label("run_relaxed_scan") == "松弛扫描"

    def test_extract_frames(self) -> None:
        assert stage_label("extract_frames") == "提取帧"

    def test_run_single_points(self) -> None:
        assert stage_label("run_single_points") == "单点计算"

    def test_build_profile(self) -> None:
        assert stage_label("build_profile") == "构建能量剖面"

    def test_select_candidates(self) -> None:
        assert stage_label("select_candidates") == "筛选候选结构"

    def test_finalize(self) -> None:
        assert stage_label("finalize") == "完成"

    def test_all_pes_keys_present_in_dict(self) -> None:
        for key in PES_STAGE_EXPECTED:
            assert key in STAGE_LABELS_ZH, f"PES key {key!r} missing from STAGE_LABELS_ZH"

    def test_all_pes_keys_match_expected_values(self) -> None:
        for key, expected_zh in PES_STAGE_EXPECTED.items():
            assert STAGE_LABELS_ZH[key] == expected_zh


# ── Test 2: Unknown key falls back to title-cased replacement ────────────


class TestFallbackBehavior:
    def test_single_underscore_fallback(self) -> None:
        assert stage_label("totally_unknown_stage") == "Totally Unknown Stage"

    def test_multi_underscore_fallback(self) -> None:
        assert stage_label("a_b_c_d") == "A B C D"

    def test_no_underscore_single_word_fallback(self) -> None:
        assert stage_label("mystage") == "Mystage"

    def test_mixed_known_unknown(self) -> None:
        assert stage_label("some_new_feature") == "Some New Feature"

    def test_lowercase_only_fallback(self) -> None:
        assert stage_label("new_step") == "New Step"


# ── Test 3: Retired keys have labels ────────────────────────────────────

RETIRED_STAGE_EXPECTED: dict[str, str] = {
    "censo_optimization": "CENSO 优化",
    "dft_optimize": "DFT 优化",
    "censo_refinement": "CENSO 精修",
    "final_format": "结果格式化",
    "shermo_thermo": "Shermo 热力学",
    "embed": "SMILES 嵌入",
    "xtbmd": "xTB-MD 采样",
    "batch_opt": "批量优化",
    "isostat": "构象聚类",
    "energy_filter": "能量筛选",
    "censo": "CENSO 排序",
    "dft_handoff": "DFT 交接",
    "conformer_energy": "构象能量",
}


class TestRetiredStageLabels:
    def test_censo_optimization(self) -> None:
        assert stage_label("censo_optimization") == "CENSO 优化"

    def test_dft_optimize(self) -> None:
        assert stage_label("dft_optimize") == "DFT 优化"

    def test_censo_refinement(self) -> None:
        assert stage_label("censo_refinement") == "CENSO 精修"

    def test_final_format(self) -> None:
        assert stage_label("final_format") == "结果格式化"

    def test_shermo_thermo(self) -> None:
        assert stage_label("shermo_thermo") == "Shermo 热力学"

    def test_xtbmd(self) -> None:
        assert stage_label("xtbmd") == "xTB-MD 采样"

    def test_censo(self) -> None:
        assert stage_label("censo") == "CENSO 排序"

    def test_dft_handoff(self) -> None:
        assert stage_label("dft_handoff") == "DFT 交接"

    def test_all_retired_keys_present(self) -> None:
        for key in RETIRED_STAGE_EXPECTED:
            assert key in STAGE_LABELS_ZH, f"Retired key {key!r} missing from STAGE_LABELS_ZH"

    def test_all_retired_keys_match_expected(self) -> None:
        for key, expected_zh in RETIRED_STAGE_EXPECTED.items():
            assert STAGE_LABELS_ZH[key] == expected_zh


# ── Test 4: Edge cases — arbitrary / empty / unicode strings ─────────────


class TestEdgeCases:
    def test_empty_string_returns_empty(self) -> None:
        assert stage_label("") == ""

    def test_single_word_no_underscore(self) -> None:
        assert stage_label("hello") == "Hello"

    def test_underscore_only(self) -> None:
        assert stage_label("_") == " "

    def test_leading_underscore(self) -> None:
        assert stage_label("_start") == " Start"

    def test_trailing_underscore(self) -> None:
        assert stage_label("end_") == "End "

    def test_multiple_consecutive_underscores(self) -> None:
        assert stage_label("a__b") == "A  B"

    def test_unicode_label_does_not_raise(self) -> None:
        result = stage_label("émoji_🎯_stage")
        assert isinstance(result, str)

    def test_whitespace_key_does_not_raise(self) -> None:
        result = stage_label("my stage")
        assert isinstance(result, str)
        assert result == "My Stage"
