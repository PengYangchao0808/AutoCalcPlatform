"""
Pipeline
========

Pipeline execution for conformer search stages.

Author: QCcalc Team (adapted from RPH)
"""

from pathlib import Path
from typing import Any, Dict, Optional

from cccp.core.protocols import ProtocolSpec


class PipelineExecutor:
    """
    Executes pipeline stages for conformer search.
    """

    def __init__(self, protocol_spec: ProtocolSpec):
        """
        Initialize pipeline executor.

        Args:
            protocol_spec: Protocol specification
        """
        self.protocol_spec = protocol_spec

    def execute_final_opt_sp(
        self, engine: Any, candidate_paths: list[Path], output_dir: Path
    ) -> Dict[str, Any]:
        """
        Execute final OPT-SP stage.

        Args:
            engine: (deprecated — engine removed in wave-8)
            candidate_paths: List of candidate XYZ paths
            output_dir: Output directory

        Returns:
            Dictionary with results
        """
        return engine._run_shared_dft_handoff(candidate_paths)

    def execute_handoff(self, engine: Any, candidate_set: Any, mode: str) -> Any:
        """
        Execute handoff strategy.

        Args:
            engine: (deprecated — engine removed in wave-8)
            candidate_set: Input candidate set
            mode: Handoff mode

        Returns:
            Modified candidate set
        """
        if mode == "optimize_rank1":
            return candidate_set.select_top_n(1)
        elif mode == "optimize_all_candidates":
            return candidate_set
        elif mode == "optimize_top2_if_gap_small":
            top2 = candidate_set.select_top_n(2)
            if len(top2) >= 2:
                gap = top2[1].energy - top2[0].energy
                if gap > self.protocol_spec.handoff_policy.small_gap_kcal:
                    return top2.select_top_n(1)
            return top2
        else:
            return candidate_set.select_top_n(1)
