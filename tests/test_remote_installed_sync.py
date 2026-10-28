"""Installed-wheel remote sync/release identity (F3 LSF normal defect).

A source checkout syncs ``<root>/src/{acp,acp_services,cccp}`` under
``src/...`` keys.  An installed wheel has no ``<root>/src``; the installed
package roots are authoritative and use package-relative keys, which must be
exactly the keys ``local_release_identity`` hashes.  These tests pin that
fallback without requiring a real venv.
"""

from __future__ import annotations

import json
from pathlib import Path

from acp.scheduler.remote import release as release_module
from acp.scheduler.remote import sync as sync_module


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _installed_tree(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    site = tmp_path / "site-packages"
    roots: dict[str, Path] = {}
    for name in ("acp", "acp_services", "cccp"):
        root = site / name
        _write(root / "__init__.py", f'"""installed {name}"""\n')
        roots[name] = root
    _write(roots["acp"] / "workflows" / "simple.py", "VALUE = 1\n")
    _write(roots["acp"] / "scheduler" / "remote" / "sync.py", "VALUE = 2\n")
    _write(roots["acp"] / "api" / "routes.py", "VALUE = 3\n")
    _write(roots["acp_services"] / "runtime" / "worker.py", "VALUE = 4\n")
    _write(roots["cccp"] / "calculation" / "tasks" / "singlepoint.py", "VALUE = 5\n")
    _write(site / "unrelated" / "ignored.py", "VALUE = 6\n")
    return site, roots


def test_installed_project_root_uses_package_roots(tmp_path: Path, monkeypatch) -> None:
    site, roots = _installed_tree(tmp_path)

    def fake_root(name: str) -> Path | None:
        return roots.get(name)

    monkeypatch.setattr(sync_module, "_installed_package_root", fake_root)
    module_file = site / "acp" / "scheduler" / "remote" / "sync.py"
    assert sync_module._project_root_from(module_file) == site


def test_installed_sync_manifest_is_non_empty_and_matches_installed_keys(
    tmp_path: Path, monkeypatch
) -> None:
    site, roots = _installed_tree(tmp_path)

    monkeypatch.setattr(sync_module, "_installed_package_root", lambda name: roots.get(name))

    entries = sync_module._sync_entries(site)
    keys = {relative for relative, _path in entries}
    assert "acp/__init__.py" in keys
    assert "acp/workflows/simple.py" in keys
    assert "acp_services/runtime/worker.py" in keys
    assert "cccp/calculation/tasks/singlepoint.py" in keys
    # api/ is excluded from the execution tree; unrelated packages never enter.
    assert "acp/api/routes.py" not in keys
    assert not any(key.startswith("unrelated/") for key in keys)
    assert not any(key.startswith("src/") for key in keys)
    assert all(path.is_file() for _relative, path in entries)

    manifest = release_module.build_release_manifest(site)
    assert manifest.files
    assert set(manifest.files) == keys
    assert manifest.release_id
    assert manifest.requirements_sha256 == ""
    assert manifest.defaults_sha256 == ""
    # The manifest is stable across builds over the same installed tree.
    again = release_module.build_release_manifest(site)
    assert again.release_id == manifest.release_id

    source = sync_module.resolve_sync_source(site, "cccp/calculation/tasks/singlepoint.py")
    assert source == roots["cccp"] / "calculation" / "tasks" / "singlepoint.py"
    assert json.loads(json.dumps(manifest.files))  # JSON-safe manifest payload
