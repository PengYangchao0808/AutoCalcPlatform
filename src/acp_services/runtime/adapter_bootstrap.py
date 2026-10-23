"""Composition-root seam for the infrastructure adapter registry (B21).

The Controller remains the single runtime authority: this module only builds an
:class:`~acp_services.runtime.adapter_registry.AdapterRegistry` from explicit
configuration/environment.  It starts no Controller, no scheduler and no
subprocess.  The acceptance harness and the application bootstrap can therefore
inject the same registry type without creating a second state authority.

Environment variables (all optional; absent means no external plugin root /
no trusted plugin / no persistent adapter state):

* ``ACP_ADAPTER_PLUGIN_ROOT`` — directory of externally installed adapters.
* ``ACP_ADAPTER_STATE_ROOT`` — persistent installed/active/task-freeze records.
* ``ACP_ADAPTER_TRUSTED_IDS`` — comma-separated explicit trust allow-list.
* ``ACP_ADAPTER_AUTHORIZED_PRINCIPALS`` — comma-separated authorization issuers
  (defaults to ``controller``).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from acp_services.runtime.adapter_registry import AdapterRegistry

__all__ = [
    "ADAPTER_PLUGIN_ROOT_ENV",
    "ADAPTER_STATE_ROOT_ENV",
    "ADAPTER_TRUSTED_IDS_ENV",
    "ADAPTER_AUTHORIZED_PRINCIPALS_ENV",
    "build_adapter_registry",
]


ADAPTER_PLUGIN_ROOT_ENV = "ACP_ADAPTER_PLUGIN_ROOT"
ADAPTER_STATE_ROOT_ENV = "ACP_ADAPTER_STATE_ROOT"
ADAPTER_TRUSTED_IDS_ENV = "ACP_ADAPTER_TRUSTED_IDS"
ADAPTER_AUTHORIZED_PRINCIPALS_ENV = "ACP_ADAPTER_AUTHORIZED_PRINCIPALS"


def build_adapter_registry(
    *,
    plugin_root: str | Path | None = None,
    state_root: str | Path | None = None,
    trusted_adapter_ids: Sequence[str] | None = None,
    authorized_principals: Sequence[str] | None = None,
    environment_probe: Callable[[str], bool] | None = None,
    now: Callable[[], str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> AdapterRegistry:
    """Build the single registry from explicit arguments or the environment.

    Explicit arguments win over environment values.  This function performs no
    discovery (discovery stays an explicit read-only call) and never imports an
    adapter entrypoint.
    """

    source = environ if environ is not None else os.environ
    resolved_plugin_root = (
        plugin_root if plugin_root is not None else source.get(ADAPTER_PLUGIN_ROOT_ENV) or None
    )
    resolved_state_root = (
        state_root if state_root is not None else source.get(ADAPTER_STATE_ROOT_ENV) or None
    )
    if trusted_adapter_ids is None:
        raw_trusted = source.get(ADAPTER_TRUSTED_IDS_ENV, "")
        resolved_trusted = tuple(item.strip() for item in raw_trusted.split(",") if item.strip())
    else:
        resolved_trusted = tuple(trusted_adapter_ids)
    if authorized_principals is None:
        raw_principals = source.get(ADAPTER_AUTHORIZED_PRINCIPALS_ENV, "")
        resolved_principals = tuple(
            item.strip() for item in raw_principals.split(",") if item.strip()
        ) or ("controller",)
    else:
        resolved_principals = tuple(authorized_principals)
    return AdapterRegistry(
        plugin_root=resolved_plugin_root,
        state_root=resolved_state_root,
        trusted_adapter_ids=resolved_trusted,
        authorized_principals=resolved_principals,
        environment_probe=environment_probe,
        now=now,
    )
