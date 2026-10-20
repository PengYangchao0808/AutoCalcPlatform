"""Default-chain publication adapter (07-B / task 28).

The Worker writes only frozen execution facts:
``WORK/00_RUNTIME/execution_receipt.json`` and
``WORK/00_RUNTIME/task_result.json``.  This ACP-side adapter turns those facts
into the existing durable publication path
(:mod:`acp.calculations.result_publication`):

1. read the receipt and the digest-bound ``task_result.json`` through the one
   worker codec (``read_receipt`` / ``read_task_result`` /
   ``read_task_result_qualification``);
2. bind job / attempt / submission / release / owner / fencing / plan identity
   and the receipt's recorded ``task_result_digest``;
3. save the durable ``ScientificResultRecord`` and publish the display
   ``ResultManifest`` through :func:`publish_result` (never a second writer);
4. mark ``publication_state.json`` complete.

Retry semantics are inherited from ``publish_result``: an existing scientific
record is reused, only the manifest publication is retried, and QC is never
re-executed.  A missing/tampered task_result is a typed publication failure,
never a successful product.  ``partial_result`` is published only as a
``partial`` manifest/product and is never offered as a normal complete result.

The adapter is parameterized by workflow (``singlepoint`` first).  When
``workflow`` is omitted the task-result ``task`` field is the workflow; when it
is supplied the task must match.  Task 29 may reuse this adapter per workflow or
document a workflow-specific equivalent.

No ACP publication code is imported by the worker; this module is composed by
the ACP controller/manager side only.  It is never called from a GET.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.calculations.result_publication import (
    ArtifactReference,
    ManifestPublisher,
    ScientificResultRecord,
    load_publication_state,
    publish_result,
)
from acp.storage.manifest import ProductKind, ResultManifest
from acp_services.runtime.attempt_completion import (
    AttemptSnapshot,
    AttemptSnapshotReader,
)
from acp_services.runtime.worker import (
    TASK_RESULT_SCHEMA,
    TaskResultIntegrityError,
    read_receipt,
    read_task_result,
    read_task_result_qualification,
    task_result_path,
)

__all__ = [
    "PUBLICATION_TASK_RESULT_FILENAME",
    "AttemptPublicationError",
    "AttemptResultPublication",
    "PublicationIntegrityError",
    "PublicationReport",
    "PublicationSupersededError",
]

#: Product copy under ``RESULT/`` (relative).  The original durable worker
#: document stays at ``WORK/00_RUNTIME/task_result.json``.
PUBLICATION_TASK_RESULT_FILENAME: str = "task_result.json"


class AttemptPublicationError(RuntimeError):
    """A publication attempt failed; the job remains publication-pending."""

    code: str = "publication_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)


class PublicationIntegrityError(AttemptPublicationError):
    """The receipt/task_result binding or content digest is invalid."""

    code = "publication_integrity_error"


class PublicationSupersededError(AttemptPublicationError):
    """The attempt/owner changed before the side-effect publication boundary."""

    code = "publication_superseded"


@dataclass(frozen=True)
class PublicationReport:
    """Successful publication fact returned to the Runtime completion seam."""

    result_id: str
    workflow: str
    result_dir: Path
    record: ScientificResultRecord
    manifest: ResultManifest
    partial: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "workflow": self.workflow,
            "result_dir": str(self.result_dir),
            "manifest_status": self.manifest.status,
            "partial": self.partial,
            "products": [product.to_dict() for product in self.manifest.products],
        }


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class AttemptResultPublication:
    """Publish one worker attempt's ``task_result.json`` through the named path.

    Args:
        workflow: Expected task workflow; ``None`` infers it from the
            ``task_result.json`` ``task`` field.
        publisher: Optional injected ``(result_dir, manifest) -> path`` publisher
            (Data publication port in production).  ``None`` uses the named
            local ``register_result_manifest`` writer.
        snapshot_reader: Optional current-attempt reader; when set, publication
            re-reads the attempt/owner at the side-effect boundary and refuses
            to write a superseded attempt's result.
    """

    workflow: str | None = None
    publisher: ManifestPublisher | None = None
    snapshot_reader: AttemptSnapshotReader | None = None

    # -- AttemptPublicationPort ------------------------------------------- #

    def publish(self, snapshot: AttemptSnapshot) -> PublicationReport:
        """Publish the bound task_result; raise a typed error on any failure."""
        self._assert_current_attempt(snapshot)
        receipt, document, qualification, workflow = self._read_bound_facts(snapshot)
        result_id = self.result_id(snapshot)
        result_dir = self.result_dir(snapshot)
        partial = bool(qualification.partial_result)
        publish_normal = bool(qualification.publish) and not partial
        status = self._manifest_status(qualification)
        copy_product = publish_normal or partial

        artifacts: tuple[ArtifactReference, ...] = ()
        metadata: dict[str, Any] = {
            "attempt": snapshot.attempt,
            "submission_id": snapshot.submission_id,
            "job_id": snapshot.job_id,
            "plan_identity": (
                snapshot.envelope.plan_identity if snapshot.envelope is not None else ""
            ),
            "task_result_digest": qualification.content_digest,
            "partial_result": partial,
            "normal_reuse": bool(qualification.normal_reuse),
            "qualification": qualification.qualification,
        }
        if copy_product:
            artifacts = (
                ArtifactReference(
                    path=PUBLICATION_TASK_RESULT_FILENAME,
                    type="task_result",
                ),
            )
            metadata["product_role"] = "partial" if partial else "normal"

        record = ScientificResultRecord(
            result_id=result_id,
            kind=workflow,
            artifacts=artifacts,
            summary={
                "status": qualification.status,
                "task": qualification.task,
                "qualification": qualification.qualification,
                "partial_result": partial,
                "normal_reuse": bool(qualification.normal_reuse),
                "publish": bool(qualification.publish),
                "publish_policy": qualification.publish_policy,
                "receipt_state": qualification.receipt_state,
                "exit_code": qualification.exit_code,
                "result_digest": str(document.get("result_digest") or ""),
                "task_result_digest": qualification.content_digest,
                "job_id": snapshot.job_id,
                "attempt": snapshot.attempt,
                "submission_id": snapshot.submission_id,
                "plan_identity": (
                    snapshot.envelope.plan_identity if snapshot.envelope is not None else ""
                ),
                "release_id": snapshot.release_id,
            },
        )

        manifest = ResultManifest(
            task_id=snapshot.job_id,
            workflow=workflow,
            status=status,
        )
        if copy_product:
            manifest.add_product(
                PUBLICATION_TASK_RESULT_FILENAME,
                PUBLICATION_TASK_RESULT_FILENAME,
                PUBLICATION_TASK_RESULT_FILENAME,
                ProductKind.FILE,
                metadata=metadata,
            )

        def _copy_products() -> None:
            if copy_product:
                _atomic_copy(
                    task_result_path(Path(snapshot.work_dir)),
                    result_dir / PUBLICATION_TASK_RESULT_FILENAME,
                )

        published = publish_result(
            result_dir,
            record=record,
            manifest=manifest,
            copy_products=_copy_products,
            publisher=self.publisher,
        )
        return PublicationReport(
            result_id=result_id,
            workflow=workflow,
            result_dir=result_dir,
            record=record,
            manifest=published,
            partial=partial,
        )

    def is_published(self, snapshot: AttemptSnapshot) -> bool:
        """True only when the publication marker, manifest and result id exist."""
        result_dir = self.result_dir(snapshot)
        state = load_publication_state(result_dir)
        if state is None or not state.complete or state.result_id != self.result_id(snapshot):
            return False
        try:
            manifest = ResultManifest.read(result_dir)
        except (OSError, ValueError):
            return False
        return manifest.task_id == snapshot.job_id

    # -- binding helpers --------------------------------------------------- #

    @staticmethod
    def result_id(snapshot: AttemptSnapshot) -> str:
        """Stable publication identity of one frozen attempt."""
        return f"{snapshot.job_id}:attempt{snapshot.attempt}:{snapshot.submission_id}"

    @staticmethod
    def result_dir(snapshot: AttemptSnapshot) -> Path:
        return Path(snapshot.work_dir) / "RESULT"

    def _assert_current_attempt(self, snapshot: AttemptSnapshot) -> None:
        if self.snapshot_reader is None:
            return
        current = self.snapshot_reader.read(snapshot.job_id)
        if current is None or not snapshot.same_identity(current):
            raise PublicationSupersededError(
                "attempt/owner changed before publication; refusing to write the old attempt"
            )

    def _read_bound_facts(
        self,
        snapshot: AttemptSnapshot,
    ) -> tuple[Any, dict[str, Any], Any, str]:
        """Read and cross-check the receipt + digest-bound task_result."""
        work_dir = Path(snapshot.work_dir)
        if not str(work_dir):
            raise PublicationIntegrityError("publication requires the task work dir")
        try:
            receipt = read_receipt(work_dir / "execution_receipt.json")
        except Exception as exc:  # noqa: BLE001 - typed publication failure
            raise PublicationIntegrityError(f"receipt is unreadable: {exc}") from exc
        if receipt is None:
            raise PublicationIntegrityError("receipt is missing")
        self._check_receipt_identity(snapshot, receipt)

        try:
            document = read_task_result(work_dir)
        except TaskResultIntegrityError as exc:
            raise PublicationIntegrityError(f"task_result rejected: {exc}") from exc
        if document is None:
            raise PublicationIntegrityError("task_result is missing")
        qualification = read_task_result_qualification(work_dir)
        if qualification is None:
            raise PublicationIntegrityError("task_result qualification is missing")
        if document.get("schema") != TASK_RESULT_SCHEMA:
            raise PublicationIntegrityError("task_result schema is not the worker codec schema")

        workflow = str(document.get("task") or "")
        if self.workflow is not None:
            if workflow != self.workflow:
                raise PublicationIntegrityError(
                    f"task_result workflow {workflow!r} != expected {self.workflow!r}"
                )
        if not workflow:
            raise PublicationIntegrityError("task_result has no workflow/task identity")

        expected_digest = receipt.evidence_digests.get("task_result_digest", "")
        if not expected_digest:
            raise PublicationIntegrityError(
                "receipt does not record task_result_digest; refusing an unbound publication"
            )
        if document.get("content_digest") != expected_digest:
            raise PublicationIntegrityError(
                "task_result content_digest does not match the receipt-recorded digest"
            )
        if document.get("receipt_state") != receipt.state:
            raise PublicationIntegrityError("task_result receipt_state does not match the receipt")
        if document.get("result_digest") != receipt.result_digest:
            raise PublicationIntegrityError("task_result result_digest does not match the receipt")
        return receipt, document, qualification, workflow

    def _check_receipt_identity(self, snapshot: AttemptSnapshot, receipt: Any) -> None:
        fields = {
            "job_id": snapshot.job_id,
            "attempt": snapshot.attempt,
            "submission_id": snapshot.submission_id,
            "release_id": snapshot.release_id,
            "owner": snapshot.owner,
            "fencing_token": snapshot.fencing_token,
        }
        for name, expected in fields.items():
            if getattr(receipt, name) != expected:
                raise PublicationIntegrityError(
                    f"receipt {name} {getattr(receipt, name)!r} != current snapshot {expected!r}"
                )
        envelope = snapshot.envelope
        if envelope is None:
            return
        if receipt.plan_identity != envelope.plan_identity:
            raise PublicationIntegrityError("receipt plan_identity does not match the envelope")
        if tuple(receipt.step_identities) != tuple(envelope.step_identities):
            raise PublicationIntegrityError("receipt step identities do not match the envelope")
        if receipt.release_digest != envelope.release_digest:
            raise PublicationIntegrityError("receipt release_digest does not match the envelope")

    @staticmethod
    def _manifest_status(qualification: Any) -> str:
        if qualification.partial_result:
            return "partial"
        if qualification.publish and qualification.qualification == "complete":
            return "completed"
        if qualification.receipt_state == "failed":
            return "failed"
        return "incomplete"
