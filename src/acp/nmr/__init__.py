# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false
"""NMR + DP4/DP5 stereochemistry-assignment workflow.

Stage-based implementation of the Goodman DP4/DP5 method on top of the
ACP conformer-search + ORCA GIAO pipeline (DevDoc
``docs/ACP_NMR_DP4_DevDoc.md``). Reuses ``acp run ensemble`` (censo-light)
for conformer generation and adds GIAO NMR + Bayesian probability on top.
"""

from __future__ import annotations

import logging

from acp.nmr.assignment import (
    collect_residual_inputs,
    match_assigned,
    match_unassigned,
)
from acp.nmr.averaging import boltzmann_average_shieldings
from acp.nmr.enumerate import (
    EnumeratedCandidate,
    EnumerateOptions,
    enumerate_candidates,
    enumerate_to_smiles,
)
from acp.nmr.equivalence import (
    build_all_labels,
    build_label_for_atom,
    detect_equivalence_groups,
    merge_explicit_and_detected,
)
from acp.nmr.error_model import (
    ErrorModel,
    GoodmanDP5Model,
    GoodmanErrorModel,
    PlaceholderStudentTErrorModel,
    dp5_fchl_available,
    dp5_model_available,
    load_dp5_model,
    load_error_model,
    validate_error_model_binding,
)
from acp.nmr.fchl import (
    build_atom_representations,
    fchl_assets_available,
    fchl_kernel_active,
    generate_fchl_representation,
    get_atomic_kernels_numpy,
    kernel_backend,
    load_atomic_reps,
    qml_kernel_available,
)
from acp.nmr.io import parse_experimental_nmr
from acp.nmr.models import (
    Assignment,
    AtomShift,
    CandidateResult,
    ConformerShielding,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    NmrReport,
    RegressionResult,
    lookup_tms_shieldings,
)
from acp.nmr.probability import (
    compute_dp4,
    compute_dp5,
    compute_dp5_goodman,
    dp5_log_to_probability,
    normalize_dp4,
)
from acp.nmr.scaling import build_assignments, fit_regression, fit_scaling_goodman
from acp.nmr.spectra import (
    BrukerProcessResult,
    ProcessedSpectrum,
    bruker_result_to_text,
    find_bruker_experiments,
    process_bruker_experiment,
    process_bruker_tree,
)

logger = logging.getLogger(__name__)

__all__ = [
    # models
    "Assignment",
    "AtomShift",
    "CandidateResult",
    "ConformerShielding",
    "ExperimentalNmr",
    "ExperimentalPeak",
    "NmrConfig",
    "NmrReport",
    "RegressionResult",
    "lookup_tms_shieldings",
    # io
    "parse_experimental_nmr",
    # equivalence
    "build_all_labels",
    "build_label_for_atom",
    "detect_equivalence_groups",
    "merge_explicit_and_detected",
    # averaging
    "boltzmann_average_shieldings",
    # enumerate (P2)
    "EnumerateOptions",
    "EnumeratedCandidate",
    "enumerate_candidates",
    "enumerate_to_smiles",
    # assignment
    "match_assigned",
    "match_unassigned",
    "collect_residual_inputs",
    # scaling
    "fit_regression",
    "fit_scaling_goodman",
    "build_assignments",
    # probability
    "compute_dp4",
    "normalize_dp4",
    "compute_dp5",
    "compute_dp5_goodman",
    "dp5_log_to_probability",
    # error model
    "ErrorModel",
    "GoodmanErrorModel",
    "GoodmanDP5Model",
    "PlaceholderStudentTErrorModel",
    "load_error_model",
    "load_dp5_model",
    "dp5_model_available",
    "dp5_fchl_available",
    "validate_error_model_binding",
    # FCHL (P4, DevDoc appendix D)
    "build_atom_representations",
    "fchl_assets_available",
    "fchl_kernel_active",
    "generate_fchl_representation",
    "get_atomic_kernels_numpy",
    "kernel_backend",
    "load_atomic_reps",
    "qml_kernel_available",
    # spectra (P3)
    "BrukerProcessResult",
    "ProcessedSpectrum",
    "bruker_result_to_text",
    "find_bruker_experiments",
    "process_bruker_experiment",
    "process_bruker_tree",
]
