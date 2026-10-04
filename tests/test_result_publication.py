"""Publication contract tests for ``acp.calculations.result_publication`` (todo 16).

Contract under test (persistence order after the calculation completes):
① scientific result record + artifact references → ② manifest / view
products → ③ publication-complete marker.  Publication is idempotent keyed by
the stable ``result_id``; recovery checks for an existing valid scientific
result BEFORE deciding whether to run the calculation and, when one exists,
retries publication only (fault-injection scenarios assert the QC call count
does not increase).  All QC executables are fakes — no real binaries.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

import acp.calculations.result_publication as publication
from acp.calculations.result_publication import (
    ArtifactReference,
    PublicationState,
    ScientificResultRecord,
    load_publication_state,
    load_scientific_result,
    mark_publication_complete,
    publish_result,
    recover_publication,
    save_scientific_result,
)
from acp.storage.manifest import MANIFEST_FILENAME, ProductKind, ResultManifest

_STDLIB_ROOTS = frozenset(
    {"__future__", "collections", "dataclasses", "json", "logging", "os", "pathlib", "typing"}
)


def _record(result_id: str = "task-1::item-1") -> ScientificResultRecord:
    return ScientificResultRecord(
        result_id=result_id,
        kind="optimize",
        artifacts=(ArtifactReference(path="structures/final.xyz", type="structure"),),
        summary={"energy_hartree": -40.2, "converged": True},
    )


def _manifest() -> ResultManifest:
    manifest = ResultManifest(task_id="t", workflow="optimize", status="completed")
    manifest.add_product("final", "Final geometry", "structures/final.xyz", ProductKind.STRUCTURE)
    return manifest


def _execute_qc_factory(record: ScientificResultRecord, counter: dict[str, int]):
    def execute_qc() -> ScientificResultRecord:
        counter["qc"] += 1
        return record

    return execute_qc


def _build_manifest(record: ScientificResultRecord) -> ResultManifest:
    manifest = ResultManifest(task_id="t", workflow=record.kind, status="completed")
    for artifact in record.artifacts:
        manifest.add_product("artifact", artifact.path, artifact.path, ProductKind.FILE)
    return manifest


# ── persistence order + idempotency ─────────────────────────────────────────


def test_publish_result_persists_in_contract_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_save, real_register, real_mark = (
        publication.save_scientific_result,
        publication.register_result_manifest,
        publication.mark_publication_complete,
    )

    def spy_save(result_dir, record):
        events.append("scientific_result")
        return real_save(result_dir, record)

    def spy_register(result_dir, manifest):
        events.append("display_manifest")
        return real_register(result_dir, manifest)

    def spy_mark(result_dir, result_id):
        events.append("complete_marker")
        return real_mark(result_dir, result_id)

    monkeypatch.setattr(publication, "save_scientific_result", spy_save)
    monkeypatch.setattr(publication, "register_result_manifest", spy_register)
    monkeypatch.setattr(publication, "mark_publication_complete", spy_mark)

    record = _record()
    publish_result(
        tmp_path,
        record=record,
        manifest=_manifest(),
        copy_products=lambda: events.append("products"),
    )
    assert events == [
        "scientific_result",
        "products",
        "display_manifest",
        "complete_marker",
    ], "publication must persist scientific results first and mark complete last"


def test_publish_result_is_idempotent_by_stable_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record()
    register_calls: list[str] = []
    real_register = publication.register_result_manifest

    def spy_register(result_dir, manifest):
        register_calls.append(record.result_id)
        return real_register(result_dir, manifest)

    monkeypatch.setattr(publication, "register_result_manifest", spy_register)

    publish_result(tmp_path, record=record, manifest=_manifest())
    again = publish_result(tmp_path, record=record, manifest=_manifest())
    assert len(register_calls) == 1, "re-publishing the same result_id must be a no-op"
    assert again.to_dict() == _manifest().to_dict()
    assert load_publication_state(tmp_path) == PublicationState(
        result_id=record.result_id, complete=True
    )


def test_scientific_record_and_display_manifest_are_separate(tmp_path: Path) -> None:
    record = _record()
    publish_result(tmp_path, record=record, manifest=_manifest())
    record_payload = json.loads(
        (tmp_path / publication.SCIENTIFIC_RESULT_FILENAME).read_text(encoding="utf-8")
    )
    manifest_payload = json.loads((tmp_path / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert set(record_payload) == {"result_id", "kind", "artifacts", "summary"}
    assert "products" not in record_payload
    assert "summary" not in manifest_payload and "artifacts" not in manifest_payload


def test_result_publication_module_only_persists() -> None:
    tree = ast.parse(inspect.getsource(publication))
    external: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in _STDLIB_ROOTS:
                    external.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root not in _STDLIB_ROOTS:
                external.append(node.module)
    assert external == ["acp.storage.manifest"], (
        f"publication module must only persist (stdlib + storage.manifest), found {external}"
    )


# ── recovery: check scientific result first ────────────────────────────────


def test_recovery_checks_existing_scientific_result_before_qc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record()
    save_scientific_result(tmp_path, record)
    events: list[str] = []
    real_load = publication.load_scientific_result

    def spy_load(result_dir):
        events.append("check_scientific_result")
        return real_load(result_dir)

    monkeypatch.setattr(publication, "load_scientific_result", spy_load)

    counter = {"qc": 0}
    outcome = recover_publication(
        tmp_path,
        result_id=record.result_id,
        execute_qc=_execute_qc_factory(record, counter),
        build_manifest=_build_manifest,
    )
    assert events[0] == "check_scientific_result"
    assert counter["qc"] == 0, "existing valid scientific result must skip QC"
    assert outcome.recovered is True and outcome.qc_executed is False


def test_recovery_without_scientific_result_allows_recompute(tmp_path: Path) -> None:
    counter = {"qc": 0}
    outcome = recover_publication(
        tmp_path,
        result_id="task-1::item-1",
        execute_qc=_execute_qc_factory(_record(), counter),
        build_manifest=_build_manifest,
    )
    assert counter["qc"] == 1, "no stored result → recompute is explicitly allowed (once)"
    assert outcome.qc_executed is True and outcome.recovered is False
    assert load_publication_state(tmp_path).complete is True


# ── fault injection: recovery retries publication only ─────────────────────


def test_fault_scientific_stored_manifest_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record()
    counter = {"qc": 0}
    execute_qc = _execute_qc_factory(record, counter)
    real_register = publication.register_result_manifest
    state = {"failures": 1}

    def flaky_register(result_dir, manifest):
        if state["failures"]:
            state["failures"] -= 1
            raise OSError("injected manifest write failure")
        return real_register(result_dir, manifest)

    monkeypatch.setattr(publication, "register_result_manifest", flaky_register)

    with pytest.raises(OSError):
        recover_publication(
            tmp_path,
            result_id=record.result_id,
            execute_qc=execute_qc,
            build_manifest=_build_manifest,
        )
    assert load_scientific_result(tmp_path) is not None, "scientific result must survive"
    assert counter["qc"] == 1

    outcome = recover_publication(
        tmp_path,
        result_id=record.result_id,
        execute_qc=execute_qc,
        build_manifest=_build_manifest,
    )
    assert outcome.recovered is True
    assert counter["qc"] == 1, "recovery must not re-execute QC"
    assert load_publication_state(tmp_path).complete is True


def test_fault_manifest_updated_complete_marker_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record()
    counter = {"qc": 0}
    execute_qc = _execute_qc_factory(record, counter)
    real_mark = mark_publication_complete
    state = {"failures": 1}

    def flaky_mark(result_dir, result_id):
        if state["failures"]:
            state["failures"] -= 1
            raise OSError("injected complete-marker failure")
        return real_mark(result_dir, result_id)

    monkeypatch.setattr(publication, "mark_publication_complete", flaky_mark)

    with pytest.raises(OSError):
        recover_publication(
            tmp_path,
            result_id=record.result_id,
            execute_qc=execute_qc,
            build_manifest=_build_manifest,
        )
    assert (tmp_path / "result_manifest.json").is_file(), "manifest must be published"
    assert load_publication_state(tmp_path) is None

    outcome = recover_publication(
        tmp_path,
        result_id=record.result_id,
        execute_qc=execute_qc,
        build_manifest=_build_manifest,
    )
    assert outcome.recovered is True
    assert counter["qc"] == 1, "recovery must not re-execute QC"
    assert load_publication_state(tmp_path).complete is True


def test_fault_partial_product_copy_then_exit(tmp_path: Path) -> None:
    record = _record()
    counter = {"qc": 0}
    execute_qc = _execute_qc_factory(record, counter)
    products_dir = tmp_path / "structures"
    copies = {"n": 0}

    def copy_products() -> None:
        copies["n"] += 1
        products_dir.mkdir(parents=True, exist_ok=True)
        (products_dir / "a.xyz").write_text("a", encoding="utf-8")
        if copies["n"] == 1:
            raise OSError("process exits after partial product copy")

    with pytest.raises(OSError):
        recover_publication(
            tmp_path,
            result_id=record.result_id,
            execute_qc=execute_qc,
            build_manifest=_build_manifest,
            copy_products=copy_products,
        )
    assert (products_dir / "a.xyz").is_file() and not (products_dir / "b.xyz").is_file()
    assert load_scientific_result(tmp_path) is not None

    def copy_products_retry() -> None:
        copies["n"] += 1
        products_dir.mkdir(parents=True, exist_ok=True)
        (products_dir / "a.xyz").write_text("a", encoding="utf-8")
        (products_dir / "b.xyz").write_text("b", encoding="utf-8")

    outcome = recover_publication(
        tmp_path,
        result_id=record.result_id,
        execute_qc=execute_qc,
        build_manifest=_build_manifest,
        copy_products=copy_products_retry,
    )
    assert outcome.recovered is True
    assert counter["qc"] == 1, "recovery must not re-execute QC"
    assert (products_dir / "a.xyz").is_file() and (products_dir / "b.xyz").is_file()
    assert load_publication_state(tmp_path).complete is True


def test_fault_remote_results_exist_local_publication_interrupted(tmp_path: Path) -> None:
    record = _record()
    save_scientific_result(tmp_path, record)
    counter = {"qc": 0}

    outcome = recover_publication(
        tmp_path,
        result_id=record.result_id,
        execute_qc=_execute_qc_factory(record, counter),
        build_manifest=_build_manifest,
    )
    assert counter["qc"] == 0, "remote results already persisted → publication retry only"
    assert outcome.recovered is True
    assert load_publication_state(tmp_path).complete is True


# ── ACP-internal pending state ─────────────────────────────────────────────


def test_publication_state_is_acp_internal_not_job_status() -> None:
    from acp.scheduler.jobs import JobStatus

    expected = {
        "queued",
        "starting",
        "pending",
        "running",
        "paused",
        "cancelling",
        "waiting_review",
        "cancelled",
        "completed",
        "failed",
    }
    assert {status.value for status in JobStatus} == expected, (
        "publication-pending state is ACP-internal; never add a global JobStatus"
    )
    assert not isinstance(PublicationState(result_id="x"), JobStatus)
