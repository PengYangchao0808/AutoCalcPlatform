"""Separate usage-unit counters shared by usage, budgets and admission (B17).

This module has no runtime dependencies so it can be imported by
:mod:`acp_services.runtime.usage` and :mod:`acp_services.runtime.research_budget`
without the metering-policy/admission import cycle.  The counts are execution
facts only; they carry no price, rate, balance or account vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "USAGE_UNIT_FIELDS",
    "USAGE_UNIT_ALIASES",
    "USAGE_NOT_UNIFIED_BILLING_NOTE",
    "UsageUnitCounts",
    "UsageUnitValidationError",
    "normalize_usage_unit",
]


class UsageUnitValidationError(ValueError):
    """A unit name or count is invalid."""


USAGE_UNIT_FIELDS: tuple[str, ...] = (
    "task_ref",
    "recipe_step",
    "qc_invocation",
    "attempt",
    "bsub",
    "model_request",
)

USAGE_UNIT_ALIASES: dict[str, str] = {
    "task": "task_ref",
    "task_ref_count": "task_ref",
    "task_count": "task_ref",
    "recipe_step_count": "recipe_step",
    "step": "recipe_step",
    "qc": "qc_invocation",
    "qc_call": "qc_invocation",
    "qc_invocation_count": "qc_invocation",
    "attempt_count": "attempt",
    "bsub_call": "bsub",
    "bsub_count": "bsub",
    "model_call": "model_request",
    "model_request_count": "model_request",
    "token_request": "model_request",
}

USAGE_NOT_UNIFIED_BILLING_NOTE = (
    "B records trusted usage only. Model-token enforcement and any model-call "
    "proxy are C-owned (acp2_experience_budget_proxy.py, C T-13); B never "
    "proxies tokens and never invents unified billing. External LLM cost managed "
    "by DSH is recorded from a trusted source only."
)


def normalize_usage_unit(value: Any) -> str:
    """Return the canonical unit name or raise :class:`UsageUnitValidationError`."""
    text = str(value or "").strip().lower().replace("-", "_")
    text = USAGE_UNIT_ALIASES.get(text, text)
    if text not in USAGE_UNIT_FIELDS:
        raise UsageUnitValidationError(f"unknown usage unit {value!r}")
    return text


@dataclass(frozen=True)
class UsageUnitCounts:
    """Separate unit counters for one trusted receipt or budget reservation."""

    task_ref: int = 0
    recipe_step: int = 0
    qc_invocation: int = 0
    attempt: int = 0
    bsub: int = 0
    model_request: int = 0

    def __post_init__(self) -> None:
        for name in USAGE_UNIT_FIELDS:
            if int(getattr(self, name)) < 0:
                raise UsageUnitValidationError(f"{name} must be non-negative")

    def get(self, unit: str) -> int:
        return int(getattr(self, normalize_usage_unit(unit)))

    @property
    def total(self) -> int:
        return sum(self.get(unit) for unit in USAGE_UNIT_FIELDS)

    def to_dict(self) -> dict[str, int]:
        return {unit: self.get(unit) for unit in USAGE_UNIT_FIELDS}

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> UsageUnitCounts:
        """Normalize a mapping of canonical or aliased unit names."""
        values = {unit: 0 for unit in USAGE_UNIT_FIELDS}
        for raw_name, raw_value in (payload or {}).items():
            unit = normalize_usage_unit(raw_name)
            try:
                amount = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise UsageUnitValidationError(f"{unit} must be an integer") from exc
            if amount < 0:
                raise UsageUnitValidationError(f"{unit} must be non-negative")
            values[unit] += amount
        return cls(**values)
