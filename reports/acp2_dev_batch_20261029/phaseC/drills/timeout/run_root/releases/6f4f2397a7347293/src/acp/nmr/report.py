"""Compatibility shim — NMR report/export now lives in
``cccp.analysis.nmr.report`` (plan C05-s5 / T29).

Re-exports the same objects so the stage-8 ``write_all_reports`` call chain
(``acp.workflows.nmr``) and direct imports keep operating on the single live
implementation. JSON/XLSX/PNG export behaviour (including the optional
openpyxl/matplotlib degradation path) is unchanged."""

from __future__ import annotations

from cccp.analysis.nmr.report import (
    GOODMAN_RESIDUAL_LABEL,
    LEGACY_REPORT_NOTE,
    PROCESSING_QUALITY_KEY,
    R2_DEFINITIONS,
    ReportPaths,
    processing_quality_records,
    report_validation_note,
    write_all_reports,
    write_json_report,
    write_plots,
    write_xlsx_report,
)
from cccp.analysis.nmr.report import (
    _augment_split_r2 as _augment_split_r2,
)
from cccp.analysis.nmr.report import (
    _nucleus_of_element as _nucleus_of_element,
)
from cccp.analysis.nmr.report import (
    _prediction_r2_by_nucleus as _prediction_r2_by_nucleus,
)

#: Names and declaration order mirror the pre-move ``acp.nmr.report`` ``__all__``.
__all__ = [
    "write_json_report",
    "write_xlsx_report",
    "write_plots",
    "write_all_reports",
    "report_validation_note",
    "GOODMAN_RESIDUAL_LABEL",
    "LEGACY_REPORT_NOTE",
    "PROCESSING_QUALITY_KEY",
    "R2_DEFINITIONS",
    "ReportPaths",
    "processing_quality_records",
]
