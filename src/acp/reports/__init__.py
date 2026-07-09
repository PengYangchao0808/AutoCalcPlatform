# pyright: reportMissingTypeStubs=false
"""Report writers."""

from __future__ import annotations

from acp.reports.nmr_report import write_json_report, write_xlsx_report

__all__ = ["write_json_report", "write_xlsx_report"]
