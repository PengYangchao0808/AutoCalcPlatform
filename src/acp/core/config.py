# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false
"""
ACP Configuration
=================

ACP-native configuration facade for the Auto-Calc Platform.

Phase 1 keeps the authoritative configuration implementation in
``cccp.config``. This module provides a stable ACP import path with
typed helper functions that delegate to the legacy configuration layer.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

ConfigDict = dict[str, object]
DEFAULT_CONFIG_NAME: str
USER_CONFIG_NAME: str

# Mechanism manual-only bond editing (default ON): API reaction preview/confirm
# skip automatic atom mapping and bond-change determination; bond changes must
# come from user-provided ``manual_bond_changes``. Mirrors
# ``mechanism.manual_bond_editing`` in config/defaults.yaml — keep in sync.
MECHANISM_MANUAL_BOND_EDITING_DEFAULT: bool = True

__all__ = [
    "DEFAULT_CONFIG_NAME",
    "MECHANISM_MANUAL_BOND_EDITING_DEFAULT",
    "USER_CONFIG_NAME",
    "apply_env_overrides",
    "get_default_config",
    "load_config",
    "merge_configs",
    "resolve_manual_bond_editing",
    "save_config",
    "validate_config",
]


def resolve_manual_bond_editing(override: bool | None = None) -> bool:
    """Resolve whether mechanism manual-only bond editing is active.

    Args:
        override: Explicit per-request value; ``None`` falls back to the
            built-in default ``MECHANISM_MANUAL_BOND_EDITING_DEFAULT``.

    Returns:
        True when automatic bond-change determination is disabled and bond
        changes must be supplied manually.
    """
    if override is not None:
        return bool(override)
    return MECHANISM_MANUAL_BOND_EDITING_DEFAULT


def _normalize_path(path: Path | str | None) -> Path | None:
    """Normalize a path-like value to ``Path`` when provided."""
    if path is None:
        return None
    return Path(path)


def __getattr__(name: str) -> str:
    """Lazily expose selected legacy configuration constants."""
    if name == "DEFAULT_CONFIG_NAME":
        from cccp.config import DEFAULT_CONFIG_NAME as legacy_default_config_name

        return legacy_default_config_name
    if name == "USER_CONFIG_NAME":
        from cccp.config import USER_CONFIG_NAME as legacy_user_config_name

        return legacy_user_config_name
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def load_config(
    config_path: Path | str | None = None,
    overrides: ConfigDict | None = None,
    defaults_path: Path | str | None = None,
) -> ConfigDict:
    """Load ACP configuration through the legacy configuration layer.

    Args:
        config_path: Optional explicit configuration file path.
        overrides: Optional in-memory overrides applied last.
        defaults_path: Optional explicit defaults file path.

    Returns:
        Fully merged and validated configuration dictionary.
    """
    logger.debug("Loading ACP configuration via cccp.config")
    from cccp.config import load_config as legacy_load_config

    return legacy_load_config(
        config_path=_normalize_path(config_path),
        overrides=overrides,
        defaults_path=_normalize_path(defaults_path),
    )


def save_config(config: ConfigDict, output_path: Path | str) -> None:
    """Save configuration through the legacy configuration layer.

    Args:
        config: Configuration dictionary to serialize.
        output_path: Destination YAML file path.
    """
    logger.debug("Saving ACP configuration via cccp.config")
    from cccp.config import save_config as legacy_save_config

    legacy_save_config(config, Path(output_path))


def get_default_config() -> ConfigDict:
    """Return the authoritative default ACP configuration.

    Returns:
        Built-in default configuration dictionary.
    """
    from cccp.config import _get_default_config

    return _get_default_config()


def merge_configs(base: ConfigDict, override: ConfigDict) -> ConfigDict:
    """Recursively merge configuration dictionaries.

    Args:
        base: Base configuration dictionary.
        override: Override configuration dictionary.

    Returns:
        Merged configuration dictionary.
    """
    from cccp.config import _merge_configs

    return _merge_configs(base, override)


def validate_config(config: ConfigDict) -> ConfigDict:
    """Validate configuration through the legacy configuration layer.

    Args:
        config: Configuration dictionary to validate.

    Returns:
        Validated configuration dictionary.
    """
    from cccp.config import _validate_config

    return _validate_config(config)


def apply_env_overrides(config: ConfigDict) -> ConfigDict:
    """Apply supported environment variable overrides to a configuration.

    Args:
        config: Configuration dictionary to update.

    Returns:
        Configuration dictionary with environment overrides applied.
    """
    from cccp.config import _apply_env_overrides

    return _apply_env_overrides(config)
