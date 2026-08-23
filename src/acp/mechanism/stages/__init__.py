"""Standalone mechanism stage runners (PESsearch / Lowconfirm / Highconfirm).

The one-shot ``mechanism`` study (S0→S4) is retired for new runs; the four
stages are independent jobs that hand over through standard manifests
(docs/ACP_Confsearch_Manual_Mechanism_Modification_Plan.md §6, §8):

* S1 ``Confsearch``  → ``confsearch_manifest.json``   (acp.confsearch)
* S2 ``PESsearch``   → ``s2_path_manifest.json``
* S3 ``Lowconfirm``  → ``s3_lowconfirm_manifest.json``
* S4 ``Highconfirm`` → ``s4_highconfirm_manifest.json`` + mechanism_profile.json
"""

from __future__ import annotations

from .confirm import ConfirmEngine, ConfirmProfile, HighConfirmProfile, LowConfirmProfile
from .handoff import (
    HANDOFF_PAYLOAD_DIRS,
    ArtifactRefError,
    copy_handoff_payload,
    expected_source_kind,
    resolve_source_job_work_dir,
    validate_stage_artifact,
)
from .high_confirm import run_high_confirm
from .low_confirm import run_low_confirm
from .pes_search import run_pes_search

__all__ = [
    "ArtifactRefError",
    "ConfirmEngine",
    "ConfirmProfile",
    "HANDOFF_PAYLOAD_DIRS",
    "HighConfirmProfile",
    "LowConfirmProfile",
    "copy_handoff_payload",
    "expected_source_kind",
    "resolve_source_job_work_dir",
    "run_high_confirm",
    "run_low_confirm",
    "run_pes_search",
    "validate_stage_artifact",
]
