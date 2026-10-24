"""B1-7/B1-1 — research resource keys and the attempt-scoped prepare read."""

from __future__ import annotations

import json
import string
from pathlib import Path
from typing import Any

import pytest

from acp_services.data.contracts import ResourceKind
from acp_services.data.operations import (
    PrepareOperationRecord,
    PrepareOperationService,
    PrepareRequest,
)
from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceRequest,
    ResearchResourceStore,
    ResearchResourceValidationError,
    research_resource_key,
    resource_id_for,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "acp2" / "experience" / "b18"


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def test_key_is_deterministic_and_shaped() -> None:
    first = research_resource_key("proj-a", "proposal", "prop-1")
    second = research_resource_key("proj-a", "proposal", "prop-1")
    assert first == second
    assert first.startswith("rrk_")
    assert len(first) == 52
    assert all(char in string.hexdigits for char in first[4:])


def test_colon_collision_triple_produces_distinct_keys() -> None:
    left = research_resource_key("a", "proposal", "prop-x:proposal:prop-y")
    right = research_resource_key("a:proposal:prop-x", "proposal", "prop-y")
    assert left != right
    assert len(left) == len(right) == 52
    assert left.startswith("rrk_") and right.startswith("rrk_")


def test_kind_is_part_of_the_identity() -> None:
    assert research_resource_key("proj-a", "proposal", "doc-1") != research_resource_key(
        "proj-a", "record", "doc-1"
    )


def test_project_is_part_of_the_identity() -> None:
    assert research_resource_key("proj-a", "proposal", "doc-1") != research_resource_key(
        "proj-b", "proposal", "doc-1"
    )


def test_long_and_punctuated_ids_are_stable() -> None:
    long_id = "d" * 300
    punctuated = "id.with:colons-and.dots"
    for document_id in (long_id, punctuated):
        first = research_resource_key("proj-a", "record", document_id)
        assert first == research_resource_key("proj-a", "record", document_id)
        assert len(first) == 52
        assert first.startswith("rrk_")


@pytest.mark.parametrize(
    ("project_id", "kind", "document_id"),
    [
        ("", "proposal", "doc-1"),
        ("   ", "proposal", "doc-1"),
        ("proj-a", "proposal", ""),
        ("proj-a", "proposal", "   "),
        ("proj-a", "dataset", "doc-1"),
        ("proj-a", "Proposal", "doc-1"),
    ],
)
def test_key_rejects_unknown_kind_and_blank_dimensions(
    project_id: str, kind: str, document_id: str
) -> None:
    with pytest.raises(ResearchResourceValidationError):
        research_resource_key(project_id, kind, document_id)


def test_create_derives_proposal_key_from_document_id(tmp_path: Path) -> None:
    proposal = _load("proposal.valid.json")
    project = str(proposal["project_id"])
    expected = research_resource_key(project, "proposal", str(proposal["proposal_id"]))
    store = ResearchResourceStore(tmp_path / "research.db")
    try:
        created = store.create(
            ResearchResourceRequest(
                resource_id="",
                schema=PROPOSAL_SCHEMA,
                document=proposal,
                principal_id="principal-1",
                project_id=project,
            )
        )
        assert created.resource_id == expected
        read = store.read(expected, principal_id="principal-1", project_id=project)
        assert read.document == proposal
        assert read.revision == 1
    finally:
        store.close()


def test_create_derives_record_key_from_document_id(tmp_path: Path) -> None:
    record = _load("research-record.valid.json")
    project = str(record["project_id"])
    expected = research_resource_key(project, "record", str(record["research_id"]))
    store = ResearchResourceStore(tmp_path / "research.db")
    try:
        created = store.create(
            ResearchResourceRequest(
                resource_id="",
                schema=RESEARCH_RECORD_SCHEMA,
                document=record,
                principal_id="principal-1",
                project_id=project,
            )
        )
        assert created.resource_id == expected
        read = store.read(expected, principal_id="principal-1", project_id=project)
        assert read.document == record
    finally:
        store.close()


@pytest.mark.parametrize("document_id", ["", "   ", None])
def test_create_rejects_blank_or_missing_document_id(
    tmp_path: Path, document_id: str | None
) -> None:
    proposal = _load("proposal.valid.json")
    proposal["proposal_id"] = document_id
    if document_id is None:
        proposal.pop("proposal_id")
    store = ResearchResourceStore(tmp_path / "research.db")
    try:
        with pytest.raises(ResearchResourceValidationError):
            store.create(
                ResearchResourceRequest(
                    resource_id="",
                    schema=PROPOSAL_SCHEMA,
                    document=proposal,
                    principal_id="principal-1",
                    project_id=str(proposal["project_id"]),
                )
            )
    finally:
        store.close()


def test_explicit_resource_id_is_preserved_and_legacy_helper_stays_stable(
    tmp_path: Path,
) -> None:
    proposal = _load("proposal.valid.json")
    project = str(proposal["project_id"])
    store = ResearchResourceStore(tmp_path / "research.db")
    try:
        created = store.create(
            ResearchResourceRequest(
                resource_id="rr-explicit",
                schema=PROPOSAL_SCHEMA,
                document=proposal,
                principal_id="principal-1",
                project_id=project,
            )
        )
        assert created.resource_id == "rr-explicit"
        read = store.read("rr-explicit", principal_id="principal-1", project_id=project)
        assert read.resource_id == "rr-explicit"

        legacy = resource_id_for(
            project_id=project, schema=PROPOSAL_SCHEMA, document=proposal
        )
        assert legacy.startswith("rr_")
        assert len(legacy) == 27
        assert all(char in string.hexdigits for char in legacy[3:])
    finally:
        store.close()


def _enqueue(
    service: PrepareOperationService,
    *,
    job_id: str,
    attempt: int,
    resource_id: str,
    resource_kind: ResourceKind,
) -> PrepareOperationRecord:
    return service.enqueue(
        PrepareRequest(
            job_id=job_id,
            attempt=attempt,
            resource_id=resource_id,
            resource_kind=resource_kind,
        )
    )


def test_list_for_job_filters_attempt_and_resource_kind(tmp_path: Path) -> None:
    service = PrepareOperationService(tmp_path / "operations.db")
    try:
        artifact_attempt_1 = _enqueue(
            service,
            job_id="job-1",
            attempt=1,
            resource_id="step_0_optimize",
            resource_kind=ResourceKind.ARTIFACT,
        )
        artifact_attempt_2 = _enqueue(
            service,
            job_id="job-1",
            attempt=2,
            resource_id="step_0_optimize",
            resource_kind=ResourceKind.ARTIFACT,
        )
        structure_input = _enqueue(
            service,
            job_id="job-1",
            attempt=1,
            resource_id="structure-input:foo/bar",
            resource_kind=ResourceKind.INPUT,
        )
        other_job = _enqueue(
            service,
            job_id="job-2",
            attempt=1,
            resource_id="step_0_optimize",
            resource_kind=ResourceKind.ARTIFACT,
        )

        rows = service.list_for_job(job_id="job-1", attempt=1)
        assert [row.operation_id for row in rows] == [artifact_attempt_1.operation_id]
        assert all(row.resource_kind == ResourceKind.ARTIFACT.value for row in rows)
        assert structure_input.operation_id not in {row.operation_id for row in rows}

        inputs = service.list_for_job(
            job_id="job-1", attempt=1, resource_kind=ResourceKind.INPUT
        )
        assert [row.operation_id for row in inputs] == [structure_input.operation_id]

        string_kind = service.list_for_job(job_id="job-1", attempt=2, resource_kind="artifact")
        assert [row.operation_id for row in string_kind] == [artifact_attempt_2.operation_id]
        assert other_job.operation_id not in {row.operation_id for row in string_kind}
    finally:
        service.close()


@pytest.mark.parametrize("attempt", [0, -1, "0"])
def test_list_for_job_rejects_attempt_below_one(tmp_path: Path, attempt: int | str) -> None:
    service = PrepareOperationService(tmp_path / "operations.db")
    try:
        with pytest.raises(ValueError):
            service.list_for_job(job_id="job-1", attempt=attempt)  # type: ignore[arg-type]
    finally:
        service.close()


def test_list_for_job_rejects_unknown_resource_kind(tmp_path: Path) -> None:
    service = PrepareOperationService(tmp_path / "operations.db")
    try:
        with pytest.raises(ValueError):
            service.list_for_job(job_id="job-1", attempt=1, resource_kind="bogus")
    finally:
        service.close()
