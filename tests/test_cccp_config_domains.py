"""C07-s4 config domain separation — science vs Services platform (T36).

Locks the three layers frozen by T36:

* **CCCP science-config parsing** — :func:`cccp.config.get_science_defaults`,
  :func:`cccp.config.load_science_config`.
* **Services platform-config parsing** — the ``cluster`` section is opaque
  pass-through here (:data:`cccp.config.PLATFORM_CONFIG_SECTIONS`,
  :func:`cccp.config.load_platform_section`); authority is the upper layer.
* **Legacy full-config compatibility assembly** — :func:`cccp.config.load_config`
  keeps returning science ∪ platform with an unchanged six-source merge.

Equivalence is proven by ``load_config`` output being identical before/after
the split (see the T36 snapshot diff in ``.omo/evidence/...``); these tests
lock the resulting invariants in-process.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import yaml

from cccp import config as ccfg

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_SRC = _REPO_ROOT / "src" / "cccp" / "config.py"


def _redirect_home(monkeypatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    return home


class TestDomainDefaults:
    def test_science_and_platform_defaults_are_disjoint(self) -> None:
        science = ccfg.get_science_defaults()
        platform = ccfg.get_platform_defaults()

        assert set(science).isdisjoint(ccfg.PLATFORM_CONFIG_SECTIONS)
        assert set(platform) == set(ccfg.PLATFORM_CONFIG_SECTIONS)
        assert "cluster" in platform
        assert "cluster" not in science
        assert {"executables", "resources", "theory", "protocols"} <= set(science)

    def test_full_defaults_equal_domain_union(self) -> None:
        full = ccfg._get_default_config()
        union = ccfg._merge_configs(ccfg.get_science_defaults(), ccfg.get_platform_defaults())
        assert full == union
        assert set(full) == set(ccfg.get_science_defaults()) | set(ccfg.get_platform_defaults())

    def test_platform_defaults_carry_remote_keys(self) -> None:
        cluster = ccfg.get_platform_defaults()["cluster"]
        for key in (
            "execution_mode",
            "type",
            "queue",
            "walltime",
            "extra_flags",
            "nodes",
            "local",
            "local_retention",
        ):
            assert key in cluster


class TestSplitConfigDomains:
    def test_split_partitions_every_top_level_key(self) -> None:
        science, platform = ccfg.split_config_domains(ccfg._get_default_config())
        assert set(science) | set(platform) == set(ccfg._get_default_config())
        assert set(science) & set(platform) == set()

    def test_unknown_keys_stay_in_science(self) -> None:
        science, platform = ccfg.split_config_domains(
            {"future_science_section": {"x": 1}, "cluster": {"execution_mode": "local"}}
        )
        assert science == {"future_science_section": {"x": 1}}
        assert platform == {"cluster": {"execution_mode": "local"}}

    def test_platform_section_is_verbatim_passthrough(self) -> None:
        raw_cluster = {
            "execution_mode": "remote",
            "nodes": [
                {
                    "name": "compute-01",
                    "host": "10.0.0.1",
                    "username": "acp",
                    "remote_work_dir": "/scratch/acp/acp_jobs",
                    "remote_code_dir": "/home/acp/acp_code",
                    "unknown_node_key": {"kept": True},
                }
            ],
            "unknown_platform_key": "kept",
        }
        _, platform = ccfg.split_config_domains({"cluster": raw_cluster})
        assert platform["cluster"] == raw_cluster
        assert platform["cluster"]["nodes"][0]["unknown_node_key"] == {"kept": True}


class TestPublicEntryEquivalence:
    def test_load_science_config_is_full_minus_platform(self, monkeypatch, tmp_path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        full = ccfg.load_config()
        science = ccfg.load_science_config()

        assert "cluster" not in science
        expected_science = {k: v for k, v in full.items() if k != "cluster"}
        assert science == expected_science

    def test_load_platform_section_is_full_cluster(self, monkeypatch, tmp_path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        full = ccfg.load_config()
        platform = ccfg.load_platform_section()

        assert platform == {"cluster": full["cluster"]}

    def test_legacy_load_config_still_returns_both_domains(self, monkeypatch, tmp_path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        full = ccfg.load_config()

        assert "cluster" in full
        assert "resources" in full
        assert "theory" in full

    def test_platform_passthrough_survives_load_config(self, monkeypatch, tmp_path) -> None:
        home = _redirect_home(monkeypatch, tmp_path)
        (home / ".cccp.yaml").write_text(
            yaml.safe_dump(
                {
                    "cluster": {
                        "execution_mode": "remote",
                        "nodes": [{"name": "n1", "unknown_node_key": 7}],
                    }
                }
            ),
            encoding="utf-8",
        )
        platform = ccfg.load_platform_section()
        assert platform["cluster"]["execution_mode"] == "remote"
        assert platform["cluster"]["nodes"] == [{"name": "n1", "unknown_node_key": 7}]


class TestSixSourcePriority:
    def test_priority_order_later_source_wins(self, monkeypatch, tmp_path) -> None:
        home = _redirect_home(monkeypatch, tmp_path)
        (home / ".cccp.yaml").write_text(
            yaml.safe_dump({"resources": {"nproc": 21}}), encoding="utf-8"
        )
        (tmp_path / "cccp.yaml").write_text(
            yaml.safe_dump({"resources": {"nproc": 3}}), encoding="utf-8"
        )
        explicit = tmp_path / "explicit.yaml"
        explicit.write_text(yaml.safe_dump({"resources": {"nproc": 9}}), encoding="utf-8")
        monkeypatch.setenv("CONFSEARCH_NPROC", "12")

        overrides = {"resources": {"nproc": 5}}
        full = ccfg.load_config(config_path=explicit, overrides=overrides)
        science = ccfg.load_science_config(config_path=explicit, overrides=overrides)

        assert full["resources"]["nproc"] == 5
        assert science["resources"]["nproc"] == 5

    def test_env_overrides_apply_in_science_entry(self, monkeypatch, tmp_path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        monkeypatch.setenv("CONFSEARCH_MEM", "48GB")
        science = ccfg.load_science_config()
        assert science["resources"]["mem"] == "48GB"

    def test_unknown_fields_pass_through(self, monkeypatch, tmp_path) -> None:
        _redirect_home(monkeypatch, tmp_path)
        (tmp_path / "cccp.yaml").write_text(
            yaml.safe_dump({"brand_new_section": {"a": [1, 2]}}), encoding="utf-8"
        )
        science = ccfg.load_science_config()
        assert science["brand_new_section"] == {"a": [1, 2]}


class TestScienceMinimalServicesFree:
    def test_config_module_has_no_services_import(self) -> None:
        tree = ast.parse(_CONFIG_SRC.read_text(encoding="utf-8"))
        blocked_roots = {"acp", "acp_services", "paramiko"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in blocked_roots, alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in blocked_roots, node.module

    def test_science_entry_imports_without_services(self) -> None:
        child = textwrap_dedent(
            """
            import sys, importlib.abc

            _BLOCKED = ("acp", "acp_services", "paramiko")

            class _Block(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split(".")[0] in _BLOCKED:
                        raise ModuleNotFoundError("blocked for isolation test: " + fullname)
                    return None

            for name in list(sys.modules):
                if name.split(".")[0] in _BLOCKED:
                    del sys.modules[name]
            sys.meta_path.insert(0, _Block())

            import cccp.config as c
            science = c.load_science_config()
            assert "cluster" not in science, science.keys()
            assert "resources" in science
            platform = c.load_platform_section()
            assert "cluster" in platform
            leaked = sorted(m for m in sys.modules if m.split(".")[0] in _BLOCKED)
            assert not leaked, leaked
            print("ISOLATION_OK")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", child],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert "ISOLATION_OK" in result.stdout


def textwrap_dedent(source: str) -> str:
    import textwrap

    return textwrap.dedent(source)
