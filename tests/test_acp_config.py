"""Tests for acp.core.config — ACP configuration facade."""

from __future__ import annotations

from pathlib import Path

import pytest


class TestAcpConfigFacade:
    """ACP config module delegates to conformer_search.config correctly."""

    def test_load_config_returns_dict(self, tmp_path, monkeypatch):
        """load_config() returns a dict with expected keys."""
        monkeypatch.chdir(tmp_path)
        from acp.core.config import load_config

        config = load_config()

        assert isinstance(config, dict)
        for key in ("executables", "resources", "theory", "protocols"):
            assert key in config

    def test_get_default_config_returns_dict(self):
        """get_default_config() returns the built-in defaults."""
        from acp.core.config import get_default_config

        defaults = get_default_config()

        assert isinstance(defaults, dict)
        assert defaults["protocols"]["default"] == "ext"
        assert defaults["theory"]["single_point"]["method"] == "wB97X-D4"

    def test_merge_configs_deep_merges(self):
        """merge_configs() deeply merges nested dicts."""
        from acp.core.config import merge_configs

        result = merge_configs({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}})
        assert result == {"a": {"x": 1, "y": 3}}

    def test_validate_config_fixes_nproc(self):
        """validate_config() corrects invalid nproc."""
        from acp.core.config import get_default_config, validate_config

        config = get_default_config()
        config["resources"]["nproc"] = -1
        result = validate_config(config)
        assert result["resources"]["nproc"] > 0

    def test_save_config_writes_yaml(self, tmp_path):
        """save_config() writes a valid YAML file."""
        from acp.core.config import get_default_config, save_config

        config = get_default_config()
        out_path = tmp_path / "test_config.yaml"
        save_config(config, out_path)

        assert out_path.exists()
        import yaml

        with open(out_path) as f:
            loaded = yaml.safe_load(f)
        assert loaded["protocols"]["default"] == "ext"

    def test_apply_env_overrides_sets_nproc(self, monkeypatch):
        """apply_env_overrides() applies CONFSEARCH_NPROC."""
        from acp.core.config import apply_env_overrides, get_default_config

        monkeypatch.setenv("CONFSEARCH_NPROC", "8")
        config = get_default_config()
        result = apply_env_overrides(config)
        assert result["resources"]["nproc"] == 8

    def test_constants_exported(self):
        """Module exports DEFAULT_CONFIG_NAME and USER_CONFIG_NAME."""
        from acp.core.config import DEFAULT_CONFIG_NAME, USER_CONFIG_NAME

        assert isinstance(DEFAULT_CONFIG_NAME, str)
        assert isinstance(USER_CONFIG_NAME, str)
