"""Release-identity tests for source checkouts and installed distributions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from acp.calculations import attempt_freeze as freeze

_PACKAGES = ("acp", "acp_services", "cccp")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _populate_source_tree(project: Path) -> None:
    _write(project / "src/acp/__init__.py", '"""acp"""\n')
    _write(project / "src/acp/a.py", "A = 1\n")
    _write(project / "src/acp/api/routes.py", "API = 1\n")
    _write(project / "src/acp/scheduler/runner.py", "SCHED = 1\n")
    _write(project / "src/acp/__pycache__/a.cpython-311.pyc", "junk")
    _write(project / "src/acp_services/__init__.py", '"""services"""\n')
    _write(project / "src/acp_services/svc.py", "S = 1\n")
    _write(project / "src/cccp/__init__.py", '"""cccp"""\n')
    _write(project / "src/cccp/c.py", "C = 1\n")
    _write(project / "requirements-node.txt", "numpy\n")
    _write(project / "config/defaults.yaml", "x: 1\n")


def _populate_site_packages(parent: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for name in _PACKAGES:
        package_root = parent / name
        _write(package_root / "__init__.py", f'"""{name}"""\n')
        _write(package_root / "mod.py", f"{name.upper()} = 1\n")
        roots[name] = package_root
    _write(parent / "acp/api/routes.py", "API = 1\n")
    _write(parent / "acp/scheduler/runner.py", "SCHED = 1\n")
    _write(parent / "acp/__pycache__/mod.cpython-311.pyc", "junk")
    return roots


def _fake_finder(roots: dict[str, Path]):
    def find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        package_root = roots.get(name)
        if package_root is None:
            return None
        return SimpleNamespace(
            submodule_search_locations=[str(package_root)],
            origin=str(package_root / "__init__.py"),
        )

    return find_spec


def _installed_root(parent: Path) -> Path:
    root = parent / "lib" / "python3.11"
    root.mkdir(parents=True)
    return root


def test_source_tree_identity_stays_repo_relative(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _populate_source_tree(project)

    identity = freeze.local_release_identity(project)
    again = freeze.local_release_identity(project)

    assert identity.project_root == project.resolve()
    assert identity.release_digest == again.release_digest
    assert identity.release_id == again.release_id
    assert len(identity.release_id) == 16
    assert len(identity.release_digest) == 64
    assert {
        "src/acp/__init__.py",
        "src/acp/a.py",
        "src/acp_services/svc.py",
        "src/cccp/c.py",
        "requirements-node.txt",
        "config/defaults.yaml",
    } <= set(identity.files)
    assert "src/acp/api/routes.py" not in identity.files
    assert "src/acp/scheduler/runner.py" not in identity.files
    assert "src/acp/__pycache__/a.cpython-311.pyc" not in identity.files
    assert not any(rel.startswith("/") for rel in identity.files)


def test_installed_layout_fallback_hashes_package_relative_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_packages = tmp_path / "site-packages"
    roots = _populate_site_packages(site_packages)
    monkeypatch.setattr(freeze.importlib.util, "find_spec", _fake_finder(roots))
    project = _installed_root(tmp_path)

    identity = freeze.local_release_identity(project)
    again = freeze.local_release_identity(project)

    assert set(identity.files) == {
        "acp/__init__.py",
        "acp/mod.py",
        "acp_services/__init__.py",
        "acp_services/mod.py",
        "cccp/__init__.py",
        "cccp/mod.py",
    }
    assert {key.split("/", 1)[0] for key in identity.files} == set(_PACKAGES)
    assert identity.release_digest == again.release_digest
    assert len(identity.release_id) == 16
    assert str(site_packages) not in identity.release_digest

    other_site_packages = tmp_path / "other-site-packages"
    other_roots = _populate_site_packages(other_site_packages)
    monkeypatch.setattr(freeze.importlib.util, "find_spec", _fake_finder(other_roots))
    other = freeze.local_release_identity(_installed_root(tmp_path / "other"))
    assert other.release_digest == identity.release_digest
    assert other.release_id == identity.release_id


def test_installed_layout_release_copies_packages_to_release_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_packages = tmp_path / "site-packages"
    roots = _populate_site_packages(site_packages)
    monkeypatch.setattr(freeze.importlib.util, "find_spec", _fake_finder(roots))
    project = _installed_root(tmp_path)

    release = freeze.build_local_release(project, tmp_path / "runs")

    assert not release.unversioned
    assert release.worker_pythonpath == release.root
    for key in ("acp/__init__.py", "acp_services/mod.py", "cccp/mod.py"):
        assert (release.root / key).is_file()
    assert not (release.root / "src").exists()
    release.assert_current()


def test_installed_layout_missing_package_is_named_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site_packages = tmp_path / "site-packages"
    roots = _populate_site_packages(site_packages)
    roots.pop("cccp")
    monkeypatch.setattr(freeze.importlib.util, "find_spec", _fake_finder(roots))

    with pytest.raises(freeze.ReleaseIdentityError, match="cccp"):
        freeze.local_release_identity(_installed_root(tmp_path))

    with pytest.raises(freeze.ReleaseIdentityError, match="cccp"):
        freeze.build_local_release(_installed_root(tmp_path / "build"), tmp_path / "runs")
