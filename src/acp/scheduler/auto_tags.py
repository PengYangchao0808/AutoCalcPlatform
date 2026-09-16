"""User-defined auto-tag rules for project tasks.

Rules live in ``projects.settings.auto_tag_rules`` as a JSON list and
are applied at submission time (v1/v2 submit hooks) or on manual
backfill.  ``apply_auto_tag_rules`` is a pure helper with no DB
dependency — the caller is responsible for merging returned tags into
``TaskIndex.update_display_fields``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AutoTagRule", "apply_auto_tag_rules"]

_VALID_FIELDS = frozenset({"remark", "molecule_name", "workflow"})
_VALID_OPS = frozenset({"contains", "equals"})


@dataclass(frozen=True)
class AutoTagRule:
    """One auto-tag rule stored in project settings."""

    id: str
    field: str
    op: str
    value: str
    tag: str
    enabled: bool = True

    def matches(self, task_row: dict[str, Any]) -> bool:
        """Return *True* when this rule applies to *task_row*."""
        if not self.enabled:
            return False
        if not self.tag or not self.tag.strip():
            return False
        if self.field not in _VALID_FIELDS or self.op not in _VALID_OPS:
            return False
        field_val = task_row.get(self.field) or ""
        if not isinstance(field_val, str):
            field_val = str(field_val)
        if not self.value:
            return False
        if self.op == "contains":
            return self.value.lower() in field_val.lower()
        elif self.op == "equals":
            return field_val == self.value
        return False


def apply_auto_tag_rules(
    rules: list[dict[str, Any]], task_row: dict[str, Any]
) -> list[str]:
    """Evaluate enabled rules against *task_row* and return matched tags.

    Parameters
    ----------
    rules:
        List of dicts from ``projects.settings.auto_tag_rules``.
    task_row:
        A dict with at least ``molecule_name``, ``remark``, ``workflow``
        keys (from the tasks index).

    Returns
    -------
    list[str]
        De-duplicated tags that matched.  Empty list when no rule matches.
        Invalid/disabled rules are silently skipped.
    """
    matched: list[str] = []
    seen: set[str] = set()
    for raw in rules:
        try:
            rule = AutoTagRule(
                id=str(raw.get("id", "")),
                field=str(raw.get("field", "")),
                op=str(raw.get("op", "")),
                value=str(raw.get("value", "")),
                tag=str(raw.get("tag", "")),
                enabled=bool(raw.get("enabled", True)),
            )
            if rule.matches(task_row):
                tag = rule.tag.strip()
                if tag and tag not in seen:
                    matched.append(tag)
                    seen.add(tag)
        except Exception:  # noqa: BLE001 — one bad rule must not break submit
            logger.warning("auto_tag rule skipped (bad data): %s", raw, exc_info=True)
    return matched
