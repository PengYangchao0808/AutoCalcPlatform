"""P3: bounded artifact descriptors from the result manifest."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from acp.api import artifact_descriptors as descriptors


def _manifest(*products: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(products=list(products))


def _product(pid: str, path: str, label: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=pid,
        path=path,
        kind=SimpleNamespace(value="file"),
        label=label or pid,
    )


def _touch_manifest(tmp_path: Any) -> Any:
    result = tmp_path / "RESULT"
    result.mkdir(parents=True, exist_ok=True)
    (result / "result_manifest.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_descriptors_publish_result_relative_paths(monkeypatch: Any, tmp_path: Any) -> None:
    manifest = _manifest(
        _product("final_report", "confsearch/final_report.json", "Final report"),
        _product("traversal", "../secret.txt"),
        _product("absolute", "/etc/passwd"),
        _product("windows", "..\\secret.txt"),
    )
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: manifest)
    items, truncated = descriptors.describe_job_artifacts(_touch_manifest(tmp_path))
    assert [item["resource_id"] for item in items] == ["final_report"]
    assert items[0]["relative_path"] == "RESULT/confsearch/final_report.json"
    assert items[0]["prepared"] is False
    assert items[0]["size_bytes"] is None
    assert truncated is False


def test_descriptors_are_bounded(monkeypatch: Any, tmp_path: Any) -> None:
    manifest = _manifest(*[_product(f"p{i}", f"f{i}.json") for i in range(5)])
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: manifest)
    items, truncated = descriptors.describe_job_artifacts(_touch_manifest(tmp_path), limit=2)
    assert len(items) == 2
    assert truncated is True


def test_missing_manifest_is_empty(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: None)
    assert descriptors.describe_job_artifacts(tmp_path) == ([], False)


def test_unsafe_resource_ids_are_not_published(monkeypatch: Any, tmp_path: Any) -> None:
    manifest = _manifest(
        _product("a b", "f1.json"),
        _product("../x", "f2.json"),
        _product("safe_id", "f3.json"),
    )
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: manifest)
    items, _ = descriptors.describe_job_artifacts(_touch_manifest(tmp_path))
    assert [item["resource_id"] for item in items] == ["safe_id"]
