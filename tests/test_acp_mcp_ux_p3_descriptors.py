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


def test_descriptors_publish_result_relative_paths(monkeypatch: Any) -> None:
    manifest = _manifest(
        _product("final_report", "confsearch/final_report.json", "Final report"),
        _product("traversal", "../secret.txt"),
        _product("absolute", "/etc/passwd"),
    )
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: manifest)
    items, truncated = descriptors.describe_job_artifacts("/tmp/task")
    assert [item["resource_id"] for item in items] == ["final_report"]
    assert items[0]["relative_path"] == "RESULT/confsearch/final_report.json"
    assert items[0]["prepared"] is False
    assert items[0]["size_bytes"] is None
    assert truncated is False


def test_descriptors_are_bounded(monkeypatch: Any) -> None:
    manifest = _manifest(*[_product(f"p{i}", f"f{i}.json") for i in range(5)])
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: manifest)
    items, truncated = descriptors.describe_job_artifacts("/tmp/task", limit=2)
    assert len(items) == 2
    assert truncated is True


def test_missing_manifest_is_empty(monkeypatch: Any) -> None:
    monkeypatch.setattr(descriptors, "load_result_manifest", lambda task: None)
    assert descriptors.describe_job_artifacts("/tmp/task") == ([], False)
