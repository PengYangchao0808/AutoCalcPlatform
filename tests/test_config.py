"""
Tests for Configuration Loading and Validation
==============================================
"""

import yaml
import copy
from pathlib import Path

from conformer_search.config import (
    _merge_configs,
    _apply_env_overrides,
    _validate_config,
    _get_default_config,
    load_config,
)

_default_config = _get_default_config()

_ALL_CONFSEARCH_ENV_VARS = [
    "CONFSEARCH_NPROC",
    "CONFSEARCH_MEM",
    "CONFSEARCH_ORCA_PATH",
    "CONFSEARCH_XTB_PATH",
    "CONFSEARCH_CREST_PATH",
    "CONFSEARCH_ISOSTAT_PATH",
    "CONFSEARCH_SHERMO_PATH",
]


def _clear_env_vars(monkeypatch):
    """Delete all CONFSEARCH_* env vars for a clean test environment."""
    for var in _ALL_CONFSEARCH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class TestConfig:
    """Test configuration loading, merging, env overrides, and validation."""

    def test_load_config_defaults(self, monkeypatch, tmp_path):
        """load_config() returns dict with expected top-level keys."""
        _clear_env_vars(monkeypatch)
        monkeypatch.chdir(tmp_path)

        config = load_config()

        expected_keys = {
            "executables",
            "resources",
            "theory",
            "thermo",
            "nmr",
            "protocols",
            "cluster",
            "optimization_control",
        }
        for key in expected_keys:
            assert key in config, f"Missing top-level key: {key}"

    def test_merge_configs_deep(self):
        """_merge_configs() deeply merges nested dicts (not shallow replace)."""
        result = _merge_configs({"a": {"x": 1, "y": 2}}, {"a": {"y": 3}})
        assert result == {"a": {"x": 1, "y": 3}}

    def test_merge_configs_override(self):
        """_merge_configs() replaces non-dict values with override."""
        result = _merge_configs({"a": 1}, {"a": 2})
        assert result == {"a": 2}

    def test_apply_env_overrides_nproc(self, monkeypatch):
        """CONFSEARCH_NPROC=8 sets config['resources']['nproc'] to 8."""
        monkeypatch.setenv("CONFSEARCH_NPROC", "8")
        config = copy.deepcopy(_default_config)
        _apply_env_overrides(config)
        assert config["resources"]["nproc"] == 8

    def test_apply_env_overrides_orca_path(self, monkeypatch):
        """CONFSEARCH_ORCA_PATH sets orca executable path."""
        monkeypatch.setenv("CONFSEARCH_ORCA_PATH", "/tmp/test_orca")
        config = copy.deepcopy(_default_config)
        _apply_env_overrides(config)
        assert config["executables"]["orca"]["path"] == "/tmp/test_orca"

    def test_apply_env_overrides_all_seven(self, monkeypatch):
        """All 7 CONFSEARCH_* env vars applied correctly without TypeError."""
        env_values = {
            "CONFSEARCH_NPROC": "8",
            "CONFSEARCH_MEM": "64GB",
            "CONFSEARCH_ORCA_PATH": "/tmp/orca",
            "CONFSEARCH_XTB_PATH": "/tmp/xtb",
            "CONFSEARCH_CREST_PATH": "/tmp/crest",
            "CONFSEARCH_ISOSTAT_PATH": "/tmp/isostat",
            "CONFSEARCH_SHERMO_PATH": "/tmp/shermo",
        }
        for var, val in env_values.items():
            monkeypatch.setenv(var, val)

        config = copy.deepcopy(_default_config)
        _apply_env_overrides(config)

        assert config["resources"]["nproc"] == 8
        assert config["resources"]["mem"] == "64GB"
        assert config["executables"]["orca"]["path"] == "/tmp/orca"
        assert config["executables"]["xtb"]["path"] == "/tmp/xtb"
        assert config["executables"]["crest"]["path"] == "/tmp/crest"
        assert config["executables"]["isostat"]["path"] == "/tmp/isostat"
        assert config["executables"]["shermo"]["path"] == "/tmp/shermo"

    def test_validate_config_nproc_negative(self):
        """_validate_config() fixes negative nproc to cpu_count()."""
        config = copy.deepcopy(_default_config)
        config["resources"]["nproc"] = -1
        _validate_config(config)
        assert config["resources"]["nproc"] > 0

    def test_validate_config_unknown_protocol(self):
        """_validate_config() defaults unknown protocol to 'ext'."""
        config = copy.deepcopy(_default_config)
        config["protocols"]["default"] = "unknown"
        _validate_config(config)
        assert config["protocols"]["default"] == "ext"

    def test_get_default_config_has_nmr_defaults(self):
        """_get_default_config() includes NMR theory and runtime defaults."""
        config = _get_default_config()

        assert config["theory"]["nmr"]["engine"] == "orca"
        assert config["theory"]["nmr"]["method"] == "B3LYP"
        assert config["theory"]["nmr"]["basis"] == "def2-TZVPP"
        assert config["theory"]["nmr"]["solvent"] is None
        assert config["theory"]["nmr"]["solvent_model"] == "smd"

        assert config["nmr"]["temperature_k"] == 298.15
        assert config["nmr"]["energy_window_kcal"] == 3.0
        assert config["nmr"]["max_conformers"] == 10
        assert config["nmr"]["references"]["1H"] == 31.88
        assert config["nmr"]["references"]["13C"] == 186.10
        assert config["nmr"]["references"]["15N"] is None

    def test_validate_config_invalid_nmr_values(self):
        """_validate_config() normalizes invalid NMR values to defaults."""
        config = copy.deepcopy(_default_config)
        config["theory"]["nmr"]["engine"] = "xtb"
        config["nmr"]["temperature_k"] = 0
        config["nmr"]["energy_window_kcal"] = -1.0
        config["nmr"]["max_conformers"] = 0
        config["nmr"]["references"]["1H"] = "bad"
        config["nmr"]["references"]["13C"] = 180

        _validate_config(config)

        assert config["theory"]["nmr"]["engine"] == "orca"
        assert config["nmr"]["temperature_k"] == 298.15
        assert config["nmr"]["energy_window_kcal"] == 3.0
        assert config["nmr"]["max_conformers"] == 10
        assert config["nmr"]["references"]["1H"] == 31.88
        assert config["nmr"]["references"]["13C"] == 180.0

    def test_yaml_vs_python_builtin_parity(self):
        """Key values match between _get_default_config() and config/defaults.yaml."""
        defaults_yaml_path = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"
        with open(defaults_yaml_path, "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)

        python_config = _get_default_config()

        assert python_config["resources"]["nproc"] == yaml_config["resources"]["nproc"]
        assert python_config["resources"]["mem"] == yaml_config["resources"]["mem"]

        py_opt = python_config["theory"]["optimization"]
        yml_opt = yaml_config["theory"]["optimization"]
        assert py_opt["method"] == yml_opt["method"]
        assert py_opt["basis"] == yml_opt["basis"]
        assert py_opt.get("dispersion") == yml_opt.get("dispersion")
        assert py_opt["solvent"] == yml_opt["solvent"]
        assert py_opt["solvent_model"] == yml_opt["solvent_model"]

        py_sp = python_config["theory"]["single_point"]
        yml_sp = yaml_config["theory"]["single_point"]
        # SP method/basis now match Python built-in defaults
        assert py_sp["method"] == yml_sp["method"]
        assert py_sp["basis"] == yml_sp["basis"]
        assert py_sp["solvent"] == yml_sp["solvent"]
        assert py_sp["solvent_model"] == yml_sp["solvent_model"]

        assert python_config["protocols"]["default"] == yaml_config["protocols"]["default"]

        py_thermo = python_config["thermo"]
        yml_thermo = yaml_config["thermo"]
        assert py_thermo["temperature_k"] == yml_thermo["temperature_k"]
        assert py_thermo["pressure_atm"] == yml_thermo["pressure_atm"]
        assert py_thermo["scl_zpe"] == yml_thermo["scl_zpe"]
        assert py_thermo["shermo_ilowfreq"] == yml_thermo["shermo_ilowfreq"]
        assert py_thermo["shermo_imagreal"] == yml_thermo["shermo_imagreal"]
        assert py_thermo["shermo_conc"] == yml_thermo["shermo_conc"]

        # Preoptimization parity
        py_preopt = python_config["theory"]["preoptimization"]
        yml_preopt = yaml_config["theory"]["preoptimization"]
        assert py_preopt["gfn_level"] == yml_preopt["gfn_level"]
        assert py_preopt["solvent"] == yml_preopt["solvent"]

        py_nmr_theory = python_config["theory"]["nmr"]
        yml_nmr_theory = yaml_config["theory"]["nmr"]
        assert py_nmr_theory["engine"] == yml_nmr_theory["engine"]
        assert py_nmr_theory["method"] == yml_nmr_theory["method"]
        assert py_nmr_theory["basis"] == yml_nmr_theory["basis"]
        assert py_nmr_theory["solvent"] == yml_nmr_theory["solvent"]
        assert py_nmr_theory["solvent_model"] == yml_nmr_theory["solvent_model"]

        py_nmr = python_config["nmr"]
        yml_nmr = yaml_config["nmr"]
        assert py_nmr["temperature_k"] == yml_nmr["temperature_k"]
        assert py_nmr["energy_window_kcal"] == yml_nmr["energy_window_kcal"]
        assert py_nmr["max_conformers"] == yml_nmr["max_conformers"]
        assert py_nmr["references"] == yml_nmr["references"]

        # Protocols key exists in both
        assert "protocols" in python_config
        assert "protocols" in yaml_config

        # ext protocol final_opt_sp matches between Python and YAML
        py_ext = python_config["protocols"]["ext"]["final_opt_sp"]
        yml_ext = yaml_config["protocols"]["ext"]["final_opt_sp"]
        assert py_ext["final_sp_method"] == yml_ext["final_sp_method"]
        assert py_ext["final_sp_basis"] == yml_ext["final_sp_basis"]
        assert py_ext["final_sp_method"] == "wB97X-D4"
        assert py_ext["final_sp_basis"] == "def2-TZVPP"
