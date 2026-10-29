"""Deterministic evidence-report production for completed envelope tasks.

The A line (``cccp.analysis.research.evidence``) owns the report vocabulary and
the honest downgrade rules; this module is the B-side use case that assembles
one report from a *completed* attempt's native artifacts, verifies the claim
sources against the attempt's read root, and persists the canonical document
inside the work dir so the record evidence projection can cite it.

No model narrative participates: the validator slot is the software identity
that produced the quantity, and every claim source is a digest-bound artifact
of the same attempt.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from acp_services.data.helpers import atomic_write_text
from acp_services.runtime import worker
from cccp.analysis.research.evidence import (
    ArtifactSourceRef,
    ClaimDraft,
    ClaimScope,
    Digest,
    EvidenceValidator,
    ExecutionFacts,
    SourceVerification,
    Subject,
    assemble_evidence_report,
    verify_claim_sources,
)

__all__ = [
    "EvidenceProductionError",
    "REPORT_RELATIVE_PATH",
    "produce_attempt_evidence_report",
]

logger = logging.getLogger(__name__)

#: Where the canonical report document lands inside the attempt's work dir.
REPORT_RELATIVE_PATH = "RESULT/evidence_report.json"

#: The software identity that produced the energy quantity (parser + product).
_ENERGY_VALIDATOR = EvidenceValidator(
    validator_id="software.acp.singlepoint-energy-product",
    validator_version="1.0.0",
    kind="software",
    description="ACP result-manifest energy product for a completed singlepoint attempt",
)

_SCIENCE_IDENTITY_PATTERN_LEN = 32


class EvidenceProductionError(ValueError):
    """A typed refusal; the report is never assembled from insufficient facts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _energy_product(work_dir: Path) -> tuple[Path, dict[str, Any]]:
    """Locate the energy_report product of the attempt via its result manifest."""
    manifest_path = work_dir / "RESULT" / "result_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceProductionError(
            "manifest_unreadable", f"the result manifest is not readable: {exc}"
        ) from exc
    for product in manifest.get("products") or []:
        if not isinstance(product, dict) or product.get("kind") != "energy_report":
            continue
        relative = str(product.get("path") or "")
        if not relative:
            continue
        path = (work_dir / "RESULT" / relative).resolve()
        root = (work_dir / "RESULT").resolve()
        if root not in path.parents:
            raise EvidenceProductionError(
                "product_path_refused", "the energy product escapes the RESULT root"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvidenceProductionError(
                "product_unreadable", f"the energy product is not readable: {exc}"
            ) from exc
        return path, payload
    raise EvidenceProductionError(
        "energy_product_missing", "the completed attempt has no energy_report product"
    )


def produce_attempt_evidence_report(
    *,
    work_dir: Path,
    job_id: str,
    attempt: int,
) -> dict[str, Any]:
    """Assemble, verify and persist the evidence report of one completed attempt.

    Returns the canonical document plus its digest and work-dir-relative path.
    The function is deterministic for one attempt state: repeated calls write
    byte-identical bytes and return the same digest.
    """
    work_dir = Path(work_dir)
    try:
        receipt = worker.read_receipt(work_dir / "execution_receipt.json")
    except (OSError, ValueError, worker.ReceiptConflictError) as exc:
        raise EvidenceProductionError(
            "receipt_unreadable", f"the execution receipt is not readable: {exc}"
        ) from exc
    task_result = worker.read_task_result(work_dir)
    if receipt is None or task_result is None:
        raise EvidenceProductionError(
            "attempt_not_ready", "the attempt has no receipt/task_result yet"
        )
    receipt_state = str(getattr(receipt, "state", "") or "")
    if receipt_state != "completed":
        raise EvidenceProductionError(
            "attempt_not_completed",
            f"evidence reports are produced for completed attempts only, saw {receipt_state!r}",
        )

    product_path, product = _energy_product(work_dir)
    energy = product.get("energy_hartree")
    if not isinstance(energy, (int, float)) or isinstance(energy, bool):
        raise EvidenceProductionError(
            "energy_missing", "the energy product carries no numeric energy_hartree"
        )
    product_bytes = product_path.read_bytes()
    product_digest = hashlib.sha256(product_bytes).hexdigest()
    product_relative = str(product_path.relative_to(work_dir))

    receipt_document = receipt.to_dict()
    plan_identity = str(receipt_document.get("plan_identity") or "")
    science_identity: str | None = None
    if plan_identity.startswith("v2:") and len(plan_identity) == 3 + _SCIENCE_IDENTITY_PATTERN_LEN:
        science_identity = plan_identity

    execution = ExecutionFacts(
        terminal_state=receipt_state,
        argv=(f"job:{job_id}", f"attempt:{attempt}", "workflow:singlepoint"),
        exit_code=int(task_result.get("exit_code") or 0),
    )
    statement = (
        f"The total electronic energy of the submitted structure at the confirmed "
        f"singlepoint level is {energy!r} hartree, as written by the attempt's "
        f"energy_report product."
    )
    draft = ClaimDraft(
        claim_id="total-electronic-energy",
        statement=statement,
        status="supported",
        scope=ClaimScope(
            subject=f"job:{job_id}",
            applicability="system_specific",
            conditions="confirmed geometry, charge and multiplicity; single electronic state",
            not_in_scope=("geometry optimality", "thermochemistry", "spectroscopic quantities"),
        ),
        source_refs=(
            ArtifactSourceRef(
                artifact_path=product_relative,
                digest=Digest(product_digest),
                role="computed_quantity",
                units="hartree",
                quantity="total_electronic_energy",
            ),
        ),
        validator=_ENERGY_VALIDATOR,
        limitations=(),
    )
    report = assemble_evidence_report(
        report_id=f"evidence:{job_id}:a{attempt}",
        subject=Subject(
            subject_id=f"job:{job_id}",
            subject_kind="calculation_result",
            description="One completed singlepoint attempt of the ACP execution chain",
            input_digests=(),
            science_identity=science_identity,
        ),
        execution=execution,
        drafts=(draft,),
    )
    verifications: tuple[SourceVerification, ...] = verify_claim_sources(
        report, read_root=work_dir
    )
    for verification in verifications:
        if verification.declared_digest != verification.actual_digest:
            raise EvidenceProductionError(
                "source_verification_failed",
                f"claim source {verification.artifact_path!r} did not verify",
            )

    document = report.to_dict()
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    target = work_dir / REPORT_RELATIVE_PATH
    atomic_write_text(target, canonical + "\n")
    digest = hashlib.sha256((canonical + "\n").encode("utf-8")).hexdigest()
    logger.info("evidence report produced for %s attempt %s", job_id, attempt)
    return {
        "report": document,
        "sha256": f"sha256:{digest}",
        "relative_path": REPORT_RELATIVE_PATH,
        "claim_statuses": report.claim_statuses,
    }
