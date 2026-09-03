"""Chinese display labels for workflow stage names."""

from __future__ import annotations

# Every stage name found across active + retired stage plan providers.
# Active: Confsearch, PESsearch, BatchOptimize, simple, NMR
# Retired: ensemble, energy, xtbmd_censo_energy
STAGE_LABELS_ZH: dict[str, str] = {
    # Confsearch
    "prepare": "准备",
    "sampling": "构象采样",
    "energy": "能量计算",
    "dedup": "去重",
    "refinement": "精修",
    "finalize": "完成",
    # PESsearch
    "materialize_input": "输入结构",
    "validate_coordinate": "坐标验证",
    "run_relaxed_scan": "松弛扫描",
    "extract_frames": "提取帧",
    "run_single_points": "单点计算",
    "build_profile": "构建能量剖面",
    "select_candidates": "筛选候选结构",
    # BatchOptimize / simple
    "optimize": "结构优化",
    "frequency": "频率计算",
    "single_point": "单点能",
    "thermochemistry": "热化学修正",
    "scan": "坐标扫描",
    "irc": "IRC 验证",
    "xtb_optimize": "xTB 优化",
    "preparing": "准备",
    "irc_forward": "IRC 正向",
    "irc_backward": "IRC 反向",
    "validating": "端点验证",
    # NMR
    "embed_smiles": "SMILES 嵌入",
    "crest_search": "CREST 搜索",
    "censo_prescreening": "CENSO 预筛",
    "censo_screening": "CENSO 筛选",
    "ensemble_export": "构象导出",
    "giao_nmr": "GIAO NMR 计算",
    "boltzmann_average": "Boltzmann 平均",
    "dp4_dp5_probability": "DP4/DP5 概率分析",
    "nmr_report": "NMR 报告",
    # Retired: ensemble provider stages
    "censo_optimization": "CENSO 优化",
    # Retired: energy provider stages
    "dft_optimize": "DFT 优化",
    "censo_refinement": "CENSO 精修",
    "final_format": "结果格式化",
    "shermo_thermo": "Shermo 热力学",
    # Retired: xtbmd_censo_energy provider stages
    "embed": "SMILES 嵌入",
    "xtbmd": "xTB-MD 采样",
    "batch_opt": "批量优化",
    "isostat": "构象聚类",
    "energy_filter": "能量筛选",
    "censo": "CENSO 排序",
    "dft_handoff": "DFT 交接",
    "conformer_energy": "构象能量",
    # Fake workflow (testing)
    "init": "初始化",
    "compute": "计算中",
}


def stage_label(name: str) -> str:
    """Return a Chinese display label for *name*, with a humanized fallback."""
    known = STAGE_LABELS_ZH.get(name)
    if known is not None:
        return known
    return name.replace("_", " ").title()


__all__ = ["STAGE_LABELS_ZH", "stage_label"]
