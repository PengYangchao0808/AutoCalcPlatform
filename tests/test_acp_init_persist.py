"""Tests for acp.init_wizard.persist — merge-safe config persistence (plan T3).

Covers D1/D5/D7: raw load-modify-save round-trip of the target YAML only,
timestamped backups, atomic tmp+os.replace writes, and writability pre-flight.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from acp.init_wizard import persist
from acp.init_wizard.persist import (
    InitAbort,
    load_target,
    save_target,
    set_cluster_type,
    set_executable_path,
    set_execution_mode_remote,
    upsert_node,
)

requires_non_root = pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root bypasses file permissions",
)


def test_round_trip_preserves_unrelated_keys(tmp_path: Path) -> None:
    target = tmp_path / ".cccp.yaml"
    target.write_text(
        yaml.dump({"nproc": 8, "executables": {"orca": {"path": "/usr/bin/orca"}}}),
        encoding="utf-8",
    )
    data = load_target(target)
    set_executable_path(data, "xtb", Path("/opt/xtb/bin/xtb"))
    save_target(target, data)

    reloaded = load_target(target)
    assert reloaded["nproc"] == 8
    assert reloaded["executables"]["orca"] == {"path": "/usr/bin/orca"}
    assert reloaded["executables"]["xtb"] == {"path": "/opt/xtb/bin/xtb"}


def test_backup_captures_pre_save_state(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    original = {"a": 1, "executables": {"orca": {"path": "/x/orca"}}}
    target.write_text(yaml.dump(original), encoding="utf-8")

    data = load_target(target)
    set_execution_mode_remote(data)
    save_target(target, data)

    backups = list(tmp_path.glob("config.yaml.bak-*"))
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text(encoding="utf-8")) == original


def test_parse_error_aborts_and_leaves_file_untouched(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    bad = "key: [unclosed\n"
    target.write_text(bad, encoding="utf-8")

    with pytest.raises(InitAbort):
        load_target(target)

    assert target.read_text(encoding="utf-8") == bad
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_non_dict_root_aborts(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(InitAbort):
        load_target(target)


def test_empty_file_loads_as_empty_dict(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_bytes(b"")
    assert load_target(target) == {}


def test_missing_file_loads_as_empty_dict(tmp_path: Path) -> None:
    assert load_target(tmp_path / "does_not_exist.yaml") == {}


@requires_non_root
def test_read_only_target_aborts_before_any_write(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n", encoding="utf-8")
    target.chmod(0o400)

    with pytest.raises(InitAbort):
        save_target(target, {"a": 2})

    assert target.read_text(encoding="utf-8") == "a: 1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]


def test_existing_restrictive_mode_never_widened(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n", encoding="utf-8")
    target.chmod(0o600)

    save_target(target, {"a": 2})

    assert (target.stat().st_mode & 0o777) == 0o600
    assert load_target(target) == {"a": 2}


def test_explicit_mode_applied_to_fresh_file(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    save_target(target, {"a": 1}, mode=0o600)

    assert (target.stat().st_mode & 0o777) == 0o600


def test_backup_same_second_collision_gets_numeric_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n", encoding="utf-8")
    monkeypatch.setattr(persist, "_backup_timestamp", lambda: "20260911-120000")
    first = tmp_path / "config.yaml.bak-20260911-120000"
    first.write_text("old: 1\n", encoding="utf-8")

    save_target(target, {"a": 2})

    collided = tmp_path / "config.yaml.bak-20260911-120000-1"
    assert collided.exists()
    assert yaml.safe_load(collided.read_text(encoding="utf-8")) == {"a": 1}
    assert first.read_text(encoding="utf-8") == "old: 1\n"


def test_replace_failure_removes_tmp_and_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("a: 1\n", encoding="utf-8")

    def boom(src: Any, dst: Any) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(InitAbort) as excinfo:
        save_target(target, {"a": 2})

    assert "13" in str(excinfo.value)
    assert str(target) in str(excinfo.value)
    assert not list(tmp_path.glob("*.tmp-*"))
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == {"a": 1}


def test_set_executable_path_preserves_sibling_keys() -> None:
    data: dict[str, Any] = {"executables": {"xtb": {"path": "/old", "version": "7.1"}}}
    set_executable_path(data, "xtb", Path("/new/xtb"))
    assert data["executables"]["xtb"] == {"path": "/new/xtb", "version": "7.1"}


def test_set_executable_path_creates_missing_sections() -> None:
    data: dict[str, Any] = {}
    set_executable_path(data, "xtb", Path("/x"))
    assert data == {"executables": {"xtb": {"path": "/x"}}}


def test_set_cluster_type_only_when_absent_or_local() -> None:
    data: dict[str, Any] = {}
    set_cluster_type(data, "openlava")
    assert data["cluster"]["type"] == "openlava"

    data["cluster"]["type"] = "local"
    set_cluster_type(data, "openlava")
    assert data["cluster"]["type"] == "openlava"

    data["cluster"]["type"] = "lsf"
    set_cluster_type(data, "openlava")
    assert data["cluster"]["type"] == "lsf"


def test_set_execution_mode_remote_creates_cluster_dict() -> None:
    data: dict[str, Any] = {}
    set_execution_mode_remote(data)
    assert data["cluster"]["execution_mode"] == "remote"


def test_upsert_node_replaces_by_name_and_appends() -> None:
    data: dict[str, Any] = {"cluster": {"nodes": [{"name": "n1", "host": "h1"}]}}
    upsert_node(data, {"name": "n1", "host": "h1b"})
    assert data["cluster"]["nodes"] == [{"name": "n1", "host": "h1b"}]

    upsert_node(data, {"name": "n2", "host": "h2"})
    assert len(data["cluster"]["nodes"]) == 2

    fresh: dict[str, Any] = {}
    upsert_node(fresh, {"name": "n9", "host": "h9"})
    assert fresh["cluster"]["nodes"] == [{"name": "n9", "host": "h9"}]
