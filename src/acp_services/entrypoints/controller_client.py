"""Loopback Controller client — the only transport to the single Controller.

B04a (plan ``.omo/plans/acp2-parallel-plan-b-runtime.md`` todo 5).  Every
machine entry (CLI ``acp task --controller``, the B05 stdio adapter, later MCP
backends) talks to **one** already-running ACP Controller over loopback HTTP.
This module is the client half of that rule:

* it imports **stdlib only** (``urllib.request``/``urllib.error``/``json``);
* it never imports :mod:`acp`, never builds a ``JobManager``, never opens the
  jobs database and never spawns a thread;
* it holds no connection state and no cached task facts — each method performs
  exactly one request against one application command and returns the
  Controller's answer.

The P1 command surface (method names and request/response field names) is
frozen in ``docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json``
(schema ``acp2.runtime.application-api.v1``).  B04a implements the read-only
discovery trio (``capabilities``/``health``/``status``) live and freezes the
remaining submit/control/data signatures; B06 binds their business routes.  A
command whose Controller route is not bound yet surfaces as
:class:`UnsupportedError` (``unsupported_capability``), never as a fabricated
success.

Author: ACP Services (B lane, B04a).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MCP_TOOLS",
    "TaskRef",
    "ArtifactRef",
    "Capabilities",
    "Health",
    "StatusSnapshot",
    "ControllerClient",
    "ControllerClientError",
    "RetryableError",
    "AuthorizationError",
    "InvalidParameterError",
    "ConflictError",
    "NotPreparedError",
    "UnsupportedError",
]

#: Default per-request timeout.  A Controller command must answer promptly; a
#: long scientific run is represented by a task reference, never a long HTTP
#: request.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: The eight frozen P1 MCP tool names, reported by ``capabilities`` so a stdio
#: probe can assert the allowlist without connecting to the adapter.
MCP_TOOLS: tuple[str, ...] = (
    "acp_capabilities",
    "acp_preflight",
    "acp_submit",
    "acp_status",
    "acp_cancel",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_analyze",
)

#: Frozen control intents (``control`` command).
CONTROL_INTENTS: frozenset[str] = frozenset({"pause", "unpause", "continue", "rerun", "edit"})


# ---------------------------------------------------------------------------
# Typed request/response records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskRef:
    """Verifiable task/attempt reference (``references.task_ref``).

    ``release_id``/``release_digest`` are optional on reads of legacy rows that
    predate the release binding; a submit response always carries them.
    """

    task_id: str
    attempt: int = 1
    release_id: str | None = None
    release_digest: str | None = None
    job_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"task_id": self.task_id, "attempt": self.attempt}
        if self.release_id is not None:
            payload["release"] = {
                "release_id": self.release_id,
                "release_digest": self.release_digest,
            }
        if self.job_id is not None:
            payload["job_id"] = self.job_id
        return payload


@dataclass(frozen=True)
class ArtifactRef:
    """Verifiable artifact-generation reference (``references.artifact_ref``)."""

    task_id: str
    attempt: int
    artifact_id: str
    generation: int
    digest: str
    size_bytes: int | None = None
    media_type: str | None = None
    relative_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": self.task_id,
            "attempt": self.attempt,
            "artifact_id": self.artifact_id,
            "generation": self.generation,
            "digest": self.digest,
        }
        if self.size_bytes is not None:
            payload["size_bytes"] = self.size_bytes
        if self.media_type is not None:
            payload["media_type"] = self.media_type
        if self.relative_path is not None:
            payload["relative_path"] = self.relative_path
        return payload


@dataclass(frozen=True)
class Capabilities:
    """Read-only discovery snapshot (``capabilities`` command)."""

    schema_version: int
    task_kinds: tuple[str, ...]
    workflows: tuple[str, ...]
    backends: tuple[Mapping[str, Any], ...]
    limits: Mapping[str, Any]
    tools: tuple[str, ...] | None
    protocol_version: str | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Capabilities:
        tools = payload.get("tools")
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            task_kinds=tuple(str(item) for item in payload.get("task_kinds") or ()),
            workflows=tuple(str(item) for item in payload.get("workflows") or ()),
            backends=tuple(
                item for item in payload.get("backends") or () if isinstance(item, Mapping)
            ),
            limits=dict(payload.get("limits") or {}),
            tools=tuple(str(item) for item in tools) if isinstance(tools, list) else None,
            protocol_version=(
                str(payload["protocol_version"])
                if payload.get("protocol_version") is not None
                else None
            ),
            raw=dict(payload),
        )


@dataclass(frozen=True)
class Health:
    """Entry-equivalence probe (``health`` command)."""

    schema_version: int
    status: str
    candidate_revision: str
    contract_version: str
    contract_digest: str
    contract_digest_scope: str | None
    read_only: bool
    started_at: str | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> Health:
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            status=str(payload.get("status", "unknown")),
            candidate_revision=str(payload.get("candidate_revision", "unknown")),
            contract_version=str(payload.get("contract_version", "")),
            contract_digest=str(payload.get("contract_digest", "")),
            contract_digest_scope=(
                str(payload["contract_digest_scope"])
                if payload.get("contract_digest_scope") is not None
                else None
            ),
            read_only=bool(payload.get("read_only", True)),
            started_at=(
                str(payload["started_at"]) if payload.get("started_at") is not None else None
            ),
            raw=dict(payload),
        )


@dataclass(frozen=True)
class StatusSnapshot:
    """One task snapshot (``status`` command).

    The Controller's legacy ``GET /api/v1/jobs/{id}`` row is normalised into the
    frozen vocabulary while the untouched row stays available as :attr:`raw`.
    """

    schema_version: int
    task_id: str
    state: str
    attempt: int | None
    revision: int | None
    updated_at: str | None
    task_ref: Mapping[str, Any] | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_legacy(
        cls, payload: Mapping[str, Any], *, fallback_task_id: str = ""
    ) -> StatusSnapshot:
        task_id = str(payload.get("task_id") or payload.get("id") or fallback_task_id)
        attempt_raw = payload.get("attempt")
        attempt = int(attempt_raw) if isinstance(attempt_raw, int) else None
        revision_raw = payload.get("revision")
        revision = int(revision_raw) if isinstance(revision_raw, int) else None
        task_ref: Mapping[str, Any] | None = None
        if task_id:
            task_ref = {"task_id": task_id, "attempt": attempt if attempt is not None else 1}
            if payload.get("release_id") is not None:
                task_ref["release"] = {
                    "release_id": payload["release_id"],
                    "release_digest": payload.get("release_digest"),
                }
            task_ref["job_id"] = task_id
        return cls(
            schema_version=int(payload.get("schema_version", 1)),
            task_id=task_id,
            state=str(payload.get("state") or payload.get("status") or "unknown"),
            attempt=attempt,
            revision=revision,
            updated_at=(
                str(payload["updated_at"]) if payload.get("updated_at") is not None else None
            ),
            task_ref=task_ref,
            raw=dict(payload),
        )


# ---------------------------------------------------------------------------
# Typed errors (frozen request-outcome classes)
# ---------------------------------------------------------------------------


class ControllerClientError(Exception):
    """Base transport/application failure carrying a frozen error code."""

    code = "unknown_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        params: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.params = dict(params) if params else {}
        if retryable is not None:
            self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "params": dict(self.params),
            "retryable": self.retryable,
        }


class RetryableError(ControllerClientError):
    """Transient prerequisite missing; retry with the same idempotency key."""

    code = "backend_unavailable"
    retryable = True


class AuthorizationError(ControllerClientError):
    """The resolved principal/scope may not perform the command."""

    code = "access_denied"


class InvalidParameterError(ControllerClientError):
    """The request is malformed or violates the frozen field rules."""

    code = "input_invalid"


class ConflictError(ControllerClientError):
    """Revision/attempt/idempotency conflict with authoritative state."""

    code = "revision_conflict"
    retryable = True


class NotPreparedError(ControllerClientError):
    """The referenced artifact is not prepared; prepare is explicit."""

    code = "resource_not_ready"
    retryable = True


class UnsupportedError(ControllerClientError):
    """The command/workflow/version is outside the frozen contract."""

    code = "unsupported_capability"


#: HTTP status -> (default code, error class).  A structured server ``detail``
#: may override the code, never the class family.
_HTTP_ERRORS: dict[int, tuple[str, type[ControllerClientError]]] = {
    400: ("input_invalid", InvalidParameterError),
    403: ("access_denied", AuthorizationError),
    404: ("resource_not_ready", NotPreparedError),
    409: ("revision_conflict", ConflictError),
    422: ("unsupported_capability", UnsupportedError),
    425: ("resource_not_ready", NotPreparedError),
    429: ("admission_denied", ControllerClientError),
    503: ("backend_unavailable", RetryableError),
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _task_id_of(task_ref: TaskRef | Mapping[str, Any] | str) -> str:
    if isinstance(task_ref, TaskRef):
        return task_ref.task_id
    if isinstance(task_ref, str):
        return task_ref
    if isinstance(task_ref, Mapping):
        nested = task_ref.get("task_ref")
        if isinstance(nested, Mapping):
            task_ref = nested
        value = task_ref.get("task_id") or task_ref.get("job_id") or task_ref.get("id")
        if isinstance(value, str) and value:
            return value
    raise InvalidParameterError(
        "task_ref must carry a non-empty task_id", params={"field": "task_ref"}
    )


class ControllerClient:
    """Stateless loopback HTTP client for the single ACP Controller.

    Args:
        base_url: Controller origin, e.g. ``http://127.0.0.1:18765``.  A missing
            scheme defaults to ``http://``.
        timeout: Per-request timeout in seconds.
    """

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        raw = str(base_url or "").strip()
        if not raw:
            raise InvalidParameterError(
                "controller URL is required", params={"field": "controller"}
            )
        if "://" not in raw:
            raw = "http://" + raw
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InvalidParameterError(
                f"unsupported controller URL: {base_url!r}",
                params={"field": "controller"},
            )
        self.base_url = raw.rstrip("/")
        self.timeout = float(timeout)

    # -- transport ---------------------------------------------------------
    def _request_json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        body = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _error_from_http(exc) from None
        except urllib.error.URLError as exc:
            raise RetryableError(
                f"Controller unreachable at {self.base_url}: {exc.reason}",
                code="backend_unavailable",
            ) from None
        except TimeoutError:
            raise RetryableError(
                f"Controller request timed out after {timeout or self.timeout}s",
                code="resource_unavailable",
            ) from None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ControllerClientError(
                f"Controller returned non-JSON response: {exc}", code="unknown_error"
            ) from None

    # -- read-only discovery ----------------------------------------------
    def capabilities(self, *, extensions: Mapping[str, Any] | None = None) -> Capabilities:
        """Return what the Controller currently offers (no side effects)."""
        del extensions  # discovery takes no request body; extensions are metadata only
        data = self._request_json("GET", "/api/capabilities")
        if not isinstance(data, Mapping):
            raise ControllerClientError("capabilities response is not an object")
        return Capabilities.from_mapping(data)

    def health(self, *, extensions: Mapping[str, Any] | None = None) -> Health:
        """Return the entry-equivalence facts (no side effects)."""
        del extensions  # liveness probe takes no request body
        data = self._request_json("GET", "/api/health")
        if not isinstance(data, Mapping):
            raise ControllerClientError("health response is not an object")
        return Health.from_mapping(data)

    def status(
        self,
        task_ref: TaskRef | Mapping[str, Any] | str,
        *,
        extensions: Mapping[str, Any] | None = None,
    ) -> StatusSnapshot:
        """Return one authoritative task snapshot (read-only)."""
        task_id = _task_id_of(task_ref)
        del extensions  # reserved for B06's status request envelope
        quoted = urllib.parse.quote(task_id, safe="")
        data = self._request_json("GET", f"/api/v1/jobs/{quoted}")
        if not isinstance(data, Mapping):
            raise ControllerClientError("status response is not an object")
        return StatusSnapshot.from_legacy(data, fallback_task_id=task_id)

    def list_tasks(
        self,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> Mapping[str, Any]:
        """Bounded task list (read-only convenience for ``acp task query``)."""
        query: dict[str, Any] = {"limit": max(1, min(int(limit), 1000))}
        if status:
            query["status"] = status
        data = self._request_json("GET", "/api/v1/jobs?" + urllib.parse.urlencode(query))
        if not isinstance(data, Mapping):
            raise ControllerClientError("list response is not an object")
        return data

    # -- application commands (signatures frozen; B06 binds the routes) ----
    def preflight(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Zero-side-effect feasibility/estimate for a would-be submit."""
        data = self._request_json("POST", "/api/v1/preflight", dict(request))
        return _as_mapping(data, "preflight")

    def submit(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Create exactly one task attempt for an authorized frozen plan."""
        data = self._request_json("POST", "/api/v1/jobs", dict(request))
        return _as_mapping(data, "submit")

    def cancel(
        self,
        task_ref: TaskRef | Mapping[str, Any] | str,
        *,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        """Request cancellation; the execution owner confirms the terminal state."""
        task_id = _task_id_of(task_ref)
        body: dict[str, Any] = {}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        quoted = urllib.parse.quote(task_id, safe="")
        data = self._request_json("POST", f"/api/v1/jobs/{quoted}/cancel", body or None)
        return _as_mapping(data, "cancel")

    def control(
        self,
        intent: str,
        task_ref: TaskRef | Mapping[str, Any] | str,
        *,
        expected_revision: int | None = None,
        target_node: str | None = None,
        project_id: str | None = None,
        edit: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        """Apply one non-cancel control intent (pause/unpause/continue/rerun/edit)."""
        if intent not in CONTROL_INTENTS:
            raise InvalidParameterError(
                f"unsupported control intent: {intent!r}", params={"field": "intent"}
            )
        task_id = _task_id_of(task_ref)
        body: dict[str, Any] = {}
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        if target_node is not None:
            body["target_node"] = target_node
        if project_id is not None:
            body["project_id"] = project_id
        if edit is not None:
            body["edit"] = dict(edit)
        if idempotency_key is not None:
            body["idempotency_key"] = idempotency_key
        quoted = urllib.parse.quote(task_id, safe="")
        data = self._request_json("POST", f"/api/v1/jobs/{quoted}/{intent}", body or None)
        return _as_mapping(data, "control")

    def operation(
        self,
        operation_ref: str | Mapping[str, Any],
        *,
        extensions: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Read one persistent prepare-operation projection (query-only)."""
        del extensions  # no request body on a read
        if isinstance(operation_ref, Mapping):
            operation_ref = (
                operation_ref.get("operation_id")
                or operation_ref.get("id")
                or operation_ref.get("operation_ref")
                or ""
            )
        operation_id = str(operation_ref or "").strip()
        if not operation_id:
            raise InvalidParameterError(
                "operation_ref must carry a non-empty operation_id",
                params={"field": "operation_ref"},
            )
        quoted = urllib.parse.quote(operation_id, safe="")
        data = self._request_json("GET", f"/api/v1/operations/{quoted}")
        return _as_mapping(data, "operation")

    def prepare(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Explicitly prepare one artifact (never implicit, never a science submit)."""
        task_ref = request.get("task_ref")
        task_id = _task_id_of(task_ref) if task_ref is not None else ""
        if not task_id:
            fallback = request.get("job_id")
            if isinstance(fallback, str) and fallback:
                task_id = fallback
        if not task_id:
            raise InvalidParameterError(
                "prepare requires task_ref.task_id or job_id",
                params={"field": "task_ref"},
            )
        quoted = urllib.parse.quote(task_id, safe="")
        data = self._request_json("POST", f"/api/v1/jobs/{quoted}/prepare", dict(request))
        return _as_mapping(data, "prepare")

    def read(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Bounded read of an already-prepared artifact (no fetch on miss).

        ``artifact_ref`` is the frozen reference.  ``resource`` (or the
        ``artifact_id`` itself for the five switch resources) selects the
        prepared cache resource; a miss maps to :class:`NotPreparedError`.
        """
        artifact_ref = request.get("artifact_ref")
        if not isinstance(artifact_ref, Mapping):
            raise InvalidParameterError(
                "read requires artifact_ref", params={"field": "artifact_ref"}
            )
        task_id = _task_id_of(artifact_ref)
        artifact_id = str(artifact_ref.get("artifact_id") or "")
        resource = str(request.get("resource") or artifact_id or "")
        if not resource:
            raise InvalidParameterError(
                "read requires artifact_ref.artifact_id", params={"field": "artifact_id"}
            )
        query: dict[str, Any] = {}
        for key in ("max_bytes", "encoding", "relative_path", "offset", "limit"):
            if request.get(key) is not None:
                query[key] = request[key]
        attempt = request.get("attempt", artifact_ref.get("attempt"))
        if attempt is not None:
            query["attempt"] = attempt
        generation = artifact_ref.get("generation")
        if generation is not None:
            query.setdefault("expected_revision", generation)
        quoted_task = urllib.parse.quote(task_id, safe="")
        quoted_resource = urllib.parse.quote(resource, safe="")
        path = f"/api/v1/jobs/{quoted_task}/prepared/{quoted_resource}"
        if query:
            path += "?" + urllib.parse.urlencode(query)
        data = self._request_json("GET", path)
        return _as_mapping(data, "read")

    def analyze(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Explicit analysis request (``explicit=true``; never implicit)."""
        if request.get("explicit") is not True:
            raise InvalidParameterError(
                "analyze requires explicit=true", params={"field": "explicit"}
            )
        input_refs = request.get("input_refs")
        first: Mapping[str, Any] | None = None
        if isinstance(input_refs, list) and input_refs and isinstance(input_refs[0], Mapping):
            first = input_refs[0]
        task_id = _task_id_of(first) if first is not None else ""
        if not task_id:
            fallback = request.get("job_id")
            if isinstance(fallback, str) and fallback:
                task_id = fallback
        if not task_id:
            raise InvalidParameterError(
                "analyze requires input_refs[0].task_id or job_id",
                params={"field": "input_refs"},
            )
        quoted = urllib.parse.quote(task_id, safe="")
        data = self._request_json("POST", f"/api/v1/jobs/{quoted}/analyze", dict(request))
        return _as_mapping(data, "analyze")

    def recover(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Reuse identity-valid results for an existing plan (single recovery owner)."""
        data = self._request_json("POST", "/api/v1/recover", dict(request))
        return _as_mapping(data, "recover")


def _as_mapping(data: Any, command: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ControllerClientError(f"{command} response is not an object")
    return data


def _error_from_http(exc: urllib.error.HTTPError) -> ControllerClientError:
    default_code, error_cls = _HTTP_ERRORS.get(
        int(exc.code), ("unknown_error", ControllerClientError)
    )
    code = default_code
    params: dict[str, Any] = {}
    message = f"Controller returned HTTP {exc.code} for {exc.url}"
    retryable: bool | None = None
    try:
        raw = exc.read()
    except OSError:  # a broken error body must not mask the status code
        raw = b""
    if raw:
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        detail = document.get("detail") if isinstance(document, Mapping) else None
        if isinstance(detail, list):
            # FastAPI request-model validation: an invalid-parameter outcome,
            # not an unsupported capability.
            error_cls = InvalidParameterError
            code = "input_invalid"
            message = "request validation failed"
            params = {"errors": detail[:5]}
        elif isinstance(detail, str) and detail:
            message = detail
        elif isinstance(detail, Mapping):
            if isinstance(detail.get("code"), str):
                code = detail["code"]
            if isinstance(detail.get("message"), str) and detail["message"]:
                message = detail["message"]
            if isinstance(detail.get("params"), Mapping):
                params = dict(detail["params"])
            if isinstance(detail.get("retryable"), bool):
                retryable = detail["retryable"]
    # ``not_prepared`` is a distinct request-outcome class: the artifact/cache
    # exists in principle but its body is not prepared.  A 404 for an unknown
    # operation is the same class (never an empty success).
    lowered = f"{code} {message}".lower()
    if "not_prepared" in lowered or code == "resource_not_ready":
        error_cls = NotPreparedError
        code = "resource_not_ready"
    return error_cls(message, code=code, params=params, retryable=retryable)
