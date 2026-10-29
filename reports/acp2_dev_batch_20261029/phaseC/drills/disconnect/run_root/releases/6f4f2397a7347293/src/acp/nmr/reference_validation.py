"""Compatibility shim — NMR ``reference_validation`` science now lives in
``cccp.analysis.nmr.reference_validation`` (plan C05-s4 / T28).

Re-exports the same objects so existing ``acp.nmr.reference_validation`` imports and
``isinstance``/identity checks keep working. Isolated optional-research
seams remain outside this path."""

from __future__ import annotations

from cccp.analysis.nmr.reference_validation import (
    ASSET_FILES,
    PINNED_GOODMAN_UPSTREAM,
    REFERENCE_UNAVAILABLE_REASONS,
    ComparisonRow,
    PinnedSetting,
    PinnedUpstreamSettings,
    ReferenceAvailability,
    ReferenceComparison,
    ReferenceDataset,
    ReferenceRecord,
    apply_reference_validation,
    assess_reference_dataset,
    asset_hashes,
    attach_reference_segment,
    compare_reference_vs_migration,
    pinned_goodman_upstream,
)

__all__ = [
    "ASSET_FILES",
    "ComparisonRow",
    "PINNED_GOODMAN_UPSTREAM",
    "PinnedSetting",
    "PinnedUpstreamSettings",
    "REFERENCE_UNAVAILABLE_REASONS",
    "ReferenceAvailability",
    "ReferenceComparison",
    "ReferenceDataset",
    "ReferenceRecord",
    "apply_reference_validation",
    "assess_reference_dataset",
    "asset_hashes",
    "attach_reference_segment",
    "compare_reference_vs_migration",
    "pinned_goodman_upstream",
]
