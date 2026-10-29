"""Paramiko-free, stdlib-only node configuration validation (B07-3).

Single source of truth for the *pure* checks that decide whether a raw
``cluster.nodes[]`` mapping is a valid remote node.  Two consumers import
this module so the interactive and non-interactive paths can never disagree:

* ``acp.init_wizard.flows`` — the ``acp init`` resource menu, which decorates
  broken entries ``name (配置无效: <原因>)`` and refuses to select them.
* ``acp.scheduler.remote.config.RemoteNode.from_config_dict`` — the
  authoritative parser.

Why a standalone pure module: :class:`RemoteNode` lives behind the
``acp.scheduler.remote`` package, whose ``__init__`` eagerly imports
``paramiko`` (the ``[remote]`` extra).  The ``acp init`` menu must run on a
core-only install, so it cannot import the parser just to ask "is this entry
valid?".  This module has **no** ``paramiko``, ``acp`` or third-party imports
and is safe on that path.

Schema (the union of the previously duplicated checks): a mapping root, the
five required keys, a space-free ``remote_work_dir``, and ``int``-coercible
``port`` / ``max_concurrent_jobs``.  Unknown keys are ignored — never
rejected, never rewritten — and the env-var password override stays the
parser's concern (``RemoteNode.resolved_password``); both semantics are
unchanged by extracting the checks.

Each consumer renders the shared facts in the message shape it has always
used, so no existing error entry changes visibility:

* :func:`node_entry_error` — the wizard menu's human-readable reason.
* :func:`require_valid_node_entry` — raises :class:`ValueError` with the
  ``RemoteNode``-shaped message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "NODE_REQUIRED_KEYS",
    "NodeConfigViolation",
    "node_entry_error",
    "require_valid_node_entry",
    "validate_node_entry",
]

#: Required node keys — the parser's hard requirement set.
NODE_REQUIRED_KEYS: tuple[str, ...] = (
    "name",
    "host",
    "username",
    "remote_work_dir",
    "remote_code_dir",
)

#: Present keys that must be ``int``-coercible when supplied.
_INT_KEYS: tuple[str, ...] = ("port", "max_concurrent_jobs")


@dataclass(frozen=True)
class NodeConfigViolation:
    """Presentation-free description of the first invalid node-entry fact.

    Attributes:
        kind: One of ``"not_mapping"``, ``"missing_key"``,
            ``"work_dir_space"`` or ``"not_int"``.
        key: Offending config key (``None`` for ``not_mapping``).
        value: Offending value — the stringified ``remote_work_dir`` for
            ``work_dir_space``, the raw value for ``not_int``.
    """

    kind: str
    key: str | None = None
    value: Any = None


def validate_node_entry(entry: Any) -> NodeConfigViolation | None:
    """Return the first violation of *entry*, or ``None`` when it is valid.

    Pure and side-effect-free: no I/O, no prompts, no imports beyond this
    module.  Unknown keys are ignored.
    """
    if not isinstance(entry, dict):
        return NodeConfigViolation(kind="not_mapping")
    for key in NODE_REQUIRED_KEYS:
        if key not in entry:
            return NodeConfigViolation(kind="missing_key", key=key)
    work_dir = str(entry["remote_work_dir"])
    if " " in work_dir:
        return NodeConfigViolation(kind="work_dir_space", key="remote_work_dir", value=work_dir)
    for key in _INT_KEYS:
        if key in entry:
            try:
                int(entry[key])
            except (TypeError, ValueError):
                return NodeConfigViolation(kind="not_int", key=key, value=entry[key])
    return None


def _menu_reason(violation: NodeConfigViolation) -> str:
    """Render *violation* in the wizard menu's established message shape."""
    if violation.kind == "not_mapping":
        return "节点条目必须是映射"
    if violation.kind == "missing_key":
        return f"缺少必需字段 {violation.key!r}"
    if violation.kind == "work_dir_space":
        return f"remote_work_dir 不能包含空格（{violation.value!r}）"
    return f"{violation.key} 不是有效整数（{violation.value!r}）"


def _remote_message(entry: Any, violation: NodeConfigViolation) -> str:
    """Render *violation* in the ``RemoteNode.from_config_dict`` shape."""
    if violation.kind == "not_mapping":
        return f"RemoteNode config must be a mapping, got {type(entry).__name__}"
    name = entry.get("name") if isinstance(entry, dict) else None
    if violation.kind == "missing_key":
        return f"RemoteNode config missing required key: {violation.key!r}"
    if violation.kind == "work_dir_space":
        return (
            f"RemoteNode {name!r}: remote_work_dir must not contain spaces "
            f"(BSUB -o/-e directives break on unquoted paths): {violation.value!r}"
        )
    return f"RemoteNode {name!r}: {violation.key} must be an integer, got {violation.value!r}"


def node_entry_error(entry: Any) -> str | None:
    """Menu-facing reason *entry* is invalid, or ``None`` when it is valid."""
    violation = validate_node_entry(entry)
    return None if violation is None else _menu_reason(violation)


def require_valid_node_entry(entry: Any) -> None:
    """Raise :class:`ValueError` (parser-shaped message) when *entry* is invalid.

    No-op for a valid entry; never mutates *entry*.
    """
    violation = validate_node_entry(entry)
    if violation is not None:
        raise ValueError(_remote_message(entry, violation))
