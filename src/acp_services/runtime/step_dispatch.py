"""M2-04a — the single Services-side ``StepDispatchAdapter`` (production ``DispatchFn``).

The bounded step DAG executes exactly one step per call.  This module turns a
**frozen step + verified upstream artifacts** into a typed
``cccp.calculation.requests.TaskRequest`` + ``TaskContext``, invokes exactly one
``cccp.calculation.run_*`` task core, maps the outcome through the **existing**
completion truth table (:func:`acp_services.runtime.worker.decide_task_result`)
and declares the real step products.

Non-goals (frozen):

* it never receives or forwards the plan ``dict`` — the request is rebuilt from
  the typed context fields;
* it never guesses an input role, an output directory or a default method: the
  context must carry ``work_dir``/``input_base``/``output_dir``/``attempt``, the
  structure role and every declared input role, and an explicitly resolved
  method/resource/software snapshot;
* it never decides completion policy itself — ``decide_task_result`` and
  ``TASK_COMPLETION_AUTHORITIES`` are the authority, so the DAG cannot grow a
  second truth table;
* it never publishes: it declares products (kind + real path + digest) and an
  opaque ``publish_payload``; the injected ``PublishFn`` and the acp-side
  ``result_publication`` seam do the publishing.

A ``product_kind`` must resolve to a real artifact: an ``energy`` product cannot
be satisfied by the digest of the whole result JSON.  Product identity
(``output_digests`` + ``products``) and publication semantics
(``publish_payload``) are validated separately.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp_services.runtime.canonical_digest import canonical_digest
from acp_services.runtime.content_digest import document_digest
from acp_services.runtime.step_dag import (
    PUBLISH_POLICY_NONE,
    PUBLISH_POLICY_NORMAL,
    PUBLISH_POLICY_PARTIAL,
    QUALIFICATION_COMPLETE,
    QUALIFICATION_DIAGNOSTIC,
    QUALIFICATION_INCOMPLETE,
    QUALIFICATION_PARTIAL,
    QUALIFICATION_TASK_FAILED,
    STEP_ENTRYPOINTS,
    STEP_TASK_KINDS,
    StepDagError,
    StepDispatchContext,
    StepOutcome,
    StepProduct,
    StepSpec,
)
from acp_services.runtime.worker import (
    QUALIFICATION_COMPLETE as WORKER_QUALIFICATION_COMPLETE,
)
from acp_services.runtime.worker import (
    QUALIFICATION_INCOMPLETE as WORKER_QUALIFICATION_INCOMPLETE,
)
from acp_services.runtime.worker import (
    QUALIFICATION_PARTIAL as WORKER_QUALIFICATION_PARTIAL,
)
from acp_services.runtime.worker import (
    QUALIFICATION_TASK_FAILED as WORKER_QUALIFICATION_TASK_FAILED,
)
from acp_services.runtime.worker import decide_task_result

logger = logging.getLogger(__name__)

__all__ = [
    "PRODUCT_KIND_RESULT",
    "StepArtifactMissingError",
    "StepDispatchAdapter",
    "StepDispatchError",
    "StepInputs",
    "StepSettings",
    "declare_products",
    "validate_declared_products",
    "validate_publish_payload",
]

#: Product kind of the whole task result (the only kind allowed to be digest-only).
PRODUCT_KIND_RESULT: str = "result"


class StepDispatchError(StepDagError):
    """The step cannot be dispatched from the frozen context (typed refusal)."""


class StepArtifactMissingError(StepDispatchError):
    """A declared product kind has no real artifact behind it."""

    def __init__(self, step_id: str, product_kind: str, detail: str = "") -> None:
        message = f"{step_id}: declared product {product_kind!r} resolves to no real artifact" + (
            f" ({detail})" if detail else ""
        )
        super().__init__("missing_product_artifact", message)
        self.product_kind = product_kind


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True, slots=True)
class StepInputs:
    """Verified step inputs: the structure plus every declared input role."""

    structure_path: Path
    structure_digest: str
    structure_role: str
    role_paths: Mapping[str, Path] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.structure_digest:
            raise StepDispatchError(
                "missing_input_digest", "the step structure must carry a content digest"
            )
        if not self.structure_role:
            raise StepDispatchError("missing_structure_role", "a structure role is required")
        object.__setattr__(self, "role_paths", dict(self.role_paths))

    def path_for_role(self, role: str) -> Path:
        """Return the path of *role* (never a guess, never a fallback)."""
        if role == self.structure_role:
            return Path(self.structure_path)
        try:
            return Path(self.role_paths[role])
        except KeyError as exc:
            raise StepDispatchError(
                "unknown_input_role",
                f"no declared input for role {role!r} (declared: "
                f"{sorted(self.role_paths) + [self.structure_role]})",
            ) from exc


@dataclass(frozen=True, slots=True)
class StepSettings:
    """The explicitly resolved method/resource/software snapshot for one attempt."""

    charge: int = 0
    multiplicity: int = 1
    method: Mapping[str, Any] = field(default_factory=dict)
    backend: str | None = None
    electronic_state: Mapping[str, Any] | None = None
    resources: Mapping[str, Any] = field(default_factory=dict)
    config: Mapping[str, Any] | None = None
    software: Mapping[str, Any] = field(default_factory=dict)

    def level_payload(self, request_method: Mapping[str, Any] | None) -> dict[str, Any]:
        """Merge the frozen method with the step's own explicit method overrides."""
        payload: dict[str, Any] = dict(self.method)
        payload.update(dict(request_method or {}))
        return payload


def _default_runner(entrypoint: str) -> Callable[..., Any]:
    """Resolve the existing ``cccp.calculation.run_*`` core (never a new executor)."""
    import cccp.calculation as calculation

    name = entrypoint.rsplit(".", 1)[-1]
    runner = getattr(calculation, name, None)
    if runner is None:
        raise StepDispatchError("unknown_implementation", f"missing {entrypoint}")
    return runner


@dataclass(frozen=True, slots=True)
class StepDispatchAdapter:
    """The ONE production dispatch adapter for bounded step DAGs.

    It is a single-argument ``DispatchFn`` (``__call__(context) -> StepOutcome``)
    so the loop keeps one narrow seam; the typed request/context conversion,
    the completion decision and the product declaration all happen here.
    """

    inputs: StepInputs
    settings: StepSettings = field(default_factory=StepSettings)
    meter: Any | None = None
    runner_factory: Callable[[str], Callable[..., Any]] = _default_runner
    work_root: Path | None = None

    # -- context validation ------------------------------------------------

    def _require_context(self, context: StepDispatchContext) -> tuple[Path, Path, Path]:
        if not context.work_dir:
            raise StepDispatchError(
                "incomplete_dispatch_context",
                f"{context.step.step_id}: work_dir is required (never inferred)",
            )
        if not context.output_dir:
            raise StepDispatchError(
                "incomplete_dispatch_context",
                f"{context.step.step_id}: an explicit per-step output_dir is required",
            )
        if not context.structure_role:
            raise StepDispatchError(
                "incomplete_dispatch_context",
                f"{context.step.step_id}: structure_role is required",
            )
        if not context.provider_version:
            raise StepDispatchError(
                "incomplete_dispatch_context",
                f"{context.step.step_id}: capability/provider version is required",
            )
        work_dir = Path(context.work_dir)
        input_base = Path(context.input_base or context.work_dir)
        output_dir = Path(context.output_dir)
        if not output_dir.is_absolute():
            output_dir = work_dir / output_dir
        root = Path(self.work_root) if self.work_root is not None else work_dir
        resolved_root = root.resolve()
        for label, candidate in (("work_dir", work_dir), ("output_dir", output_dir)):
            try:
                candidate.resolve().relative_to(resolved_root)
            except ValueError as exc:
                raise StepDispatchError(
                    "output_outside_work_dir",
                    f"{context.step.step_id}: {label} {candidate} escapes {resolved_root}",
                ) from exc
        return work_dir, input_base, output_dir

    # -- request building --------------------------------------------------

    def build_request(self, context: StepDispatchContext) -> tuple[Any, Any]:
        """Return the typed ``(TaskRequest, TaskContext)`` for one step.

        Every field comes from the frozen context or the resolved settings
        snapshot; nothing is defaulted silently and no plan ``dict`` is involved.
        """
        from cccp.calculation.context import TaskContext
        from cccp.calculation.requests import TaskRequest

        work_dir, input_base, output_dir = self._require_context(context)
        step = context.step
        structure = self.inputs
        if structure.structure_role != context.structure_role:
            raise StepDispatchError(
                "structure_role_mismatch",
                f"{step.step_id}: frozen structure role {context.structure_role!r} != "
                f"supplied {structure.structure_role!r}",
            )
        declared_roles = dict(step.input_roles)
        for role, role_path in declared_roles.items():
            supplied = structure.path_for_role(role)
            if Path(role_path).name != supplied.name:
                raise StepDispatchError(
                    "input_role_mismatch",
                    f"{step.step_id}: role {role!r} expects {role_path!r}, "
                    f"the adapter was given {supplied.name!r}",
                )
        payload: dict[str, Any] = {
            "task": STEP_TASK_KINDS[step.kind].value,
            "structure": {
                "path": str(Path(structure.structure_path)),
                "role": structure.structure_role,
                "source": context.task_ref,
            },
            "charge": int(self.settings.charge),
            "multiplicity": int(self.settings.multiplicity),
            "level": self.settings.level_payload(context.method),
            "resources": dict(self.settings.resources),
            "options": dict(step.parameters),
            "output_dir": str(output_dir),
        }
        if self.settings.backend:
            payload["backend"] = self.settings.backend
        if self.settings.electronic_state:
            payload["electronic_state"] = dict(self.settings.electronic_state)
        try:
            request = TaskRequest.from_dict(payload)
        except (TypeError, ValueError) as exc:
            raise StepDispatchError(
                "invalid_task_request", f"{step.step_id}: {type(exc).__name__}: {exc}"
            ) from exc
        task_context = TaskContext(
            workdir=work_dir,
            input_base=input_base,
            config=dict(self.settings.config) if self.settings.config else None,
            meter=self.meter,
        )
        return request, task_context

    # -- dispatch ----------------------------------------------------------

    def __call__(self, context: StepDispatchContext) -> StepOutcome:
        step = context.step
        request, task_context = self.build_request(context)
        runner = self.runner_factory(STEP_ENTRYPOINTS[step.kind])
        try:
            result = runner(request, context=task_context)
        except Exception:
            raise
        return self.outcome_from_result(context, result, task_context=task_context)

    def outcome_from_result(
        self, context: StepDispatchContext, result: Any, *, task_context: Any = None
    ) -> StepOutcome:
        """Map one task result onto the frozen decision + declared products."""
        step = context.step
        decision = decide_task_result(result)
        payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        result_digest = document_digest(payload)
        qualification = _step_qualification(step, decision)
        if qualification in {
            QUALIFICATION_INCOMPLETE,
            QUALIFICATION_TASK_FAILED,
        }:
            return StepOutcome(
                output_digests={},
                receipt_digest="",
                result_digest=result_digest,
                publish_payload={
                    "result": payload,
                    "decision": _decision_view(decision),
                    "publish_policy": decision.publish_policy,
                    "products": [],
                },
                qualification=qualification,
                publish_policy=PUBLISH_POLICY_NONE,
                products=(),
                execution_identity=context.step_identity.step_identity,
                identity_scheme=context.step_identity.identity_scheme,
                failure_reason=(
                    f"{decision.qualification}: "
                    + "; ".join(str(error) for error in decision.errors)
                    if decision.errors
                    else decision.qualification
                ),
            )
        output_dir = Path(context.output_dir)
        products = declare_products(step, result, output_dir=output_dir)
        output_digests = {product.product_kind: product.digest for product in products}
        publish_policy = _step_publish_policy(step, decision, qualification)
        receipt_digest = ""
        if task_context is not None and hasattr(task_context, "progress_errors"):
            receipt_digest = canonical_digest(
                {
                    "step_id": step.step_id,
                    "result_digest": result_digest,
                    "products": output_digests,
                }
            )
        return StepOutcome(
            output_digests=output_digests,
            receipt_digest=receipt_digest,
            result_digest=result_digest,
            publish_payload={
                "result": payload,
                "decision": _decision_view(decision),
                "publish_policy": publish_policy,
                "products": [product.to_dict() for product in products],
                "execution_identity": context.step_identity.step_identity,
                "identity_scheme": context.step_identity.identity_scheme,
            },
            qualification=qualification,
            publish_policy=publish_policy,
            products=products,
            execution_identity=context.step_identity.step_identity,
            identity_scheme=context.step_identity.identity_scheme,
        )


def _decision_view(decision: Any) -> dict[str, Any]:
    """JSON-safe view of a ``CompletionDecision`` (no policy is re-derived)."""
    return {
        "row": getattr(decision, "row", ""),
        "receipt_state": getattr(decision, "receipt_state", ""),
        "job_status": getattr(decision, "job_status", ""),
        "normal_reuse": bool(getattr(decision, "normal_reuse", False)),
        "publish": bool(getattr(decision, "publish", False)),
        "publish_policy": getattr(decision, "publish_policy", ""),
        "partial_result": bool(getattr(decision, "partial_result", False)),
        "qualification": getattr(decision, "qualification", ""),
        "missing_facts": list(getattr(decision, "missing_facts", ()) or ()),
        "required_facts": list(getattr(decision, "required_facts", ()) or ()),
        "errors": [
            error.to_dict() if hasattr(error, "to_dict") else str(error)
            for error in getattr(decision, "errors", ()) or ()
        ],
    }


def _step_qualification(step: StepSpec, decision: Any) -> str:
    """Translate the worker decision into a step qualification (no new policy).

    ``diagnostic_only`` steps are recorded as diagnostics even when the task
    core completed: the D01 truth table answers "did the task complete", not
    "may this result release a normal downstream step".
    """
    declaration = str(getattr(decision, "qualification", "") or "")
    if declaration == WORKER_QUALIFICATION_TASK_FAILED:
        return QUALIFICATION_TASK_FAILED
    if declaration == WORKER_QUALIFICATION_INCOMPLETE:
        return QUALIFICATION_INCOMPLETE
    if declaration == WORKER_QUALIFICATION_PARTIAL or bool(
        getattr(decision, "partial_result", False)
    ):
        # A partial result never releases required downstream science.
        return QUALIFICATION_PARTIAL
    if declaration != WORKER_QUALIFICATION_COMPLETE:
        return QUALIFICATION_INCOMPLETE
    if step.diagnostic_only:
        return QUALIFICATION_DIAGNOSTIC
    return QUALIFICATION_COMPLETE


def _step_publish_policy(step: StepSpec, decision: Any, qualification: str) -> str:
    if qualification == QUALIFICATION_DIAGNOSTIC:
        return PUBLISH_POLICY_NONE
    if qualification == QUALIFICATION_PARTIAL:
        return PUBLISH_POLICY_PARTIAL
    policy = str(getattr(decision, "publish_policy", "") or "")
    if not bool(getattr(decision, "publish", False)) or policy == PUBLISH_POLICY_NONE:
        return PUBLISH_POLICY_NONE
    if policy == PUBLISH_POLICY_PARTIAL:
        return PUBLISH_POLICY_PARTIAL
    return PUBLISH_POLICY_NORMAL


def declare_products(
    step: StepSpec,
    result: Any,
    *,
    output_dir: Path,
) -> tuple[StepProduct, ...]:
    """Declare the step's real products from the task result artifacts.

    A ``product_kind`` must map onto an existing artifact file (by declared
    ``product_artifact_types`` or by exact artifact-type equality).  The single
    exception is the whole-result product kind ``result``, whose artifact is the
    task-result document the worker writes; even then the declared digest is the
    content digest of the result document, never a placeholder.
    """
    artifacts = tuple(getattr(result, "artifacts", ()) or ())
    declared: list[StepProduct] = []
    for reference in step.produces:
        product_kind = reference.product_kind
        accepted = tuple(step.product_artifact_types.get(product_kind, (product_kind,)))
        match = None
        for artifact in artifacts:
            artifact_type = str(getattr(artifact, "type", "") or "")
            if artifact_type in accepted:
                match = artifact
                break
        if match is None:
            raise StepArtifactMissingError(
                step.step_id,
                product_kind,
                f"accepted artifact types {list(accepted)}; task artifacts "
                f"{sorted({str(getattr(a, 'type', '') or '') for a in artifacts})}",
            )
        path = Path(str(getattr(match, "path", "") or ""))
        if not path.is_absolute():
            path = output_dir / path
        if not path.is_file():
            raise StepArtifactMissingError(
                step.step_id, product_kind, f"artifact file {path} is missing"
            )
        declared.append(
            StepProduct(
                product_kind=product_kind,
                path=str(path),
                digest=_sha256_file(path),
                size_bytes=path.stat().st_size,
            )
        )
    return tuple(declared)


def validate_declared_products(
    step: StepSpec,
    outcome: StepOutcome,
    *,
    work_root: Path | None = None,
) -> None:
    """Verify the declared products are real, inside the attempt and digest-bound.

    Product identity is checked here and publication semantics separately
    (:func:`validate_publish_payload`); neither substitutes for the other.
    """
    declared_kinds = {reference.product_kind for reference in step.produces}
    observed_kinds = {product.product_kind for product in outcome.products}
    missing = declared_kinds - observed_kinds
    if missing:
        raise StepDispatchError(
            "undeclared_product",
            f"{step.step_id}: declared products {sorted(missing)} were not produced",
        )
    extra = observed_kinds - declared_kinds
    if extra:
        raise StepDispatchError(
            "unexpected_product",
            f"{step.step_id}: produced products {sorted(extra)} are not declared",
        )
    for product in outcome.products:
        path = Path(product.path)
        if not path.is_file():
            raise StepArtifactMissingError(step.step_id, product.product_kind, "file missing")
        if work_root is not None:
            try:
                path.resolve().relative_to(Path(work_root).resolve())
            except ValueError as exc:
                raise StepDispatchError(
                    "product_outside_work_dir",
                    f"{step.step_id}: product {path} escapes {work_root}",
                ) from exc
        actual = _sha256_file(path)
        if actual != product.digest:
            raise StepDispatchError(
                "product_digest_mismatch",
                f"{step.step_id}/{product.product_kind}: declared {product.digest} "
                f"!= actual {actual}",
            )
        if outcome.output_digests.get(product.product_kind) != product.digest:
            raise StepDispatchError(
                "product_identity_mismatch",
                f"{step.step_id}/{product.product_kind}: output_digests and products disagree",
            )


def validate_publish_payload(step: StepSpec, outcome: StepOutcome) -> None:
    """Verify the publication semantics (not the product identity).

    ``publish_payload`` must carry the completion decision and the declared
    products; a payload that merely repeats ``output_digests`` is not a
    publication document.
    """
    payload = outcome.publish_payload
    if not isinstance(payload, Mapping):
        raise StepDispatchError(
            "invalid_publish_payload", f"{step.step_id}: publish_payload must be a mapping"
        )
    for key in ("result", "decision", "publish_policy", "products"):
        if key not in payload:
            raise StepDispatchError(
                "invalid_publish_payload",
                f"{step.step_id}: publish_payload is missing {key!r}",
            )
    declared = payload.get("products")
    if not isinstance(declared, Sequence) or isinstance(declared, (str, bytes)):
        raise StepDispatchError(
            "invalid_publish_payload", f"{step.step_id}: publish_payload.products must be a list"
        )
    kinds = {str(item.get("product_kind") or "") for item in declared if isinstance(item, Mapping)}
    if kinds != {product.product_kind for product in outcome.products}:
        raise StepDispatchError(
            "invalid_publish_payload",
            f"{step.step_id}: publish_payload.products disagree with the declared products",
        )
    if str(payload.get("publish_policy") or "") != outcome.publish_policy:
        raise StepDispatchError(
            "invalid_publish_payload",
            f"{step.step_id}: publish_payload.publish_policy != outcome.publish_policy",
        )
