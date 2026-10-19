"""Single execution-chain dispatcher + frozen workflow/release capability table.

B06-3 (plan todo 32). The local subprocess entry (``acp.scheduler.runner``) and
the remote LSF entry (``acp.scheduler.remote.script_gen``) must not each decide
*how* a job runs: exactly one dispatcher consults the frozen
workflow/release capability table, freezes the chosen execution protocol on the
attempt, and returns the entry command. This module **is** that dispatcher and
the table.

Decisions frozen here
---------------------

* **One dispatcher, one entry per attempt.** :func:`resolve_attempt_choice`
  reads the attempt's recorded choice and reuses it verbatim when the attempt
  number is unchanged (resume / reconcile never re-select the entry); a *new*
  attempt re-selects. A recorded choice is the single authority for which entry
  a given attempt ran, so the same attempt can never dispatch twice.
* **Frozen capability table.** :data:`FROZEN_WORKFLOW_CAPABILITY_TABLE` is the
  C01 (HN-01) closure snapshot: the eight workflows whose exhaustive
  per-workflow closure was delivered are ``closure="integrated"`` and are the
  *enabled* scope; the remaining seven stay ``closure="legacy"`` and keep the
  legacy path (never claimed independent).
* **Envelope-ready gate.** A workflow whose closure is integrated still only
  takes the envelope entry when a frozen envelope/plan pair is present for the
  attempt (:func:`frozen_artifacts_present`). Until the controller freezes one,
  the dispatcher deterministically selects the legacy entry — the chain is
  switched, but a workflow is never half-switched (the whole closure uses one
  entry, recorded).
* **Single worker argv.** :func:`worker_argv` is the only place the
  ``python -m acp_services.worker_bootstrap`` string entry is built; the remote
  entry (:func:`acp.scheduler.remote.script_gen.build_remote_worker_command`)
  delegates to it.

This module imports stdlib only (no ``acp``) and never references the legacy CLI
entry as a command (AST + string gate in
``tests/test_services_chain_switch.py``).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

__all__ = [
    "ATTEMPT_PROTOCOL_FILENAME",
    "BINDING_FILENAME",
    "CURRENT_SWITCH_SCHEMA",
    "ENVELOPE_FILENAME",
    "EXECUTION_ENTRY_ENVELOPE",
    "EXECUTION_ENTRY_LEGACY",
    "EXECUTION_PROTOCOL_LOCAL",
    "EXECUTION_PROTOCOL_LSF",
    "FROZEN_WORKFLOW_CAPABILITY_TABLE",
    "PLAN_FILENAME",
    "RUNTIME_PROTOCOL_DIR",
    "WORKER_MODULE",
    "AttemptExecutionChoice",
    "ExecutionLaunch",
    "ExecutionEntrySelection",
    "UnsupportedExecutionWorkflowError",
    "WorkflowExecutionCapability",
    "attempt_choice_from_result",
    "capability_for",
    "frozen_artifacts_present",
    "local_launch",
    "read_attempt_choice",
    "resolve_attempt_choice",
    "select_execution_entry",
    "with_attempt_choice",
    "worker_argv",
    "write_attempt_choice",
]

#: Execution entry vocabulary (frozen on the attempt record).
EXECUTION_ENTRY_ENVELOPE: Final = "envelope"
EXECUTION_ENTRY_LEGACY: Final = "legacy"

#: Execution protocol vocabulary (matches ``envelope.SUPPORTED_EXECUTION_PROTOCOLS``).
EXECUTION_PROTOCOL_LOCAL: Final = "local"
EXECUTION_PROTOCOL_LSF: Final = "lsf"

#: Wire version of the attempt protocol record.
CURRENT_SWITCH_SCHEMA: Final = 1

#: Runtime dir (relative to the task work dir) holding per-attempt runtime files.
RUNTIME_PROTOCOL_DIR: Final = ("WORK", "00_RUNTIME")
ATTEMPT_PROTOCOL_FILENAME: Final = "execution_protocol.json"
ENVELOPE_FILENAME: Final = "envelope.json"
PLAN_FILENAME: Final = "plan.json"
BINDING_FILENAME: Final = "binding.json"

#: Services worker module entry (never the legacy CLI).
WORKER_MODULE: Final = "acp_services.worker_bootstrap"


class UnsupportedExecutionWorkflowError(Exception):
    """No execution capability is registered for the workflow."""

    def __init__(self, workflow: str) -> None:
        super().__init__(f"no execution capability registered for workflow: {workflow!r}")
        self.workflow = workflow


# ── frozen capability table ─────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkflowExecutionCapability:
    """Frozen execution capability of one active workflow.

    ``closure`` is the C01 (HN-01) per-workflow closure verdict — ``integrated``
    when the exhaustive closure was delivered *and* applied by the consumer,
    ``legacy`` otherwise. ``remote_supported`` mirrors the remote allow-list
    (``script_gen._ALLOWED_REMOTE_WORKFLOWS``); a workflow that cannot run on a
    node keeps the legacy local-only assignment even when it is integrated.
    """

    workflow: str
    capabilities: tuple[str, ...]
    closure: str
    remote_supported: bool
    release_required: bool = True


#: C01 (HN-01) delivered-and-applied closure: the eight workflows whose
#: per-workflow closure record is `integrated` (todo 32 applies HN-01).
_C01_INTEGRATED: Final[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
    ("singlepoint", ("singlepoint",), True),
    ("optimize", ("optimize",), True),
    ("frequency", ("frequency",), True),
    ("scan", ("scan",), True),
    ("irc", ("irc",), True),
    ("casscf", ("casscf",), False),
    ("xtb_optimize", ("optimize",), True),
    ("OrcaGradient", ("orca_gradient",), False),
)

#: Not yet closed by C01: keep the legacy path; never claimed independent.
_LEGACY_SCOPE: Final[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
    ("Confsearch", ("conformer_search", "md_sampling", "clustering", "censo_refine"), True),
    ("PESsearch", ("scan", "singlepoint"), True),
    ("BatchOptimize", ("optimize", "frequency", "singlepoint", "thermochemistry"), True),
    ("XtbPathSearch", ("xtb_path_search",), False),
    ("XtbConstrainedOpt", ("xtb_constrained_opt",), False),
    ("tsmode", ("frequency", "optimize"), True),
    ("nmr", ("nmr_shielding", "conformer_search", "censo_refine"), True),
)


def _build_table() -> dict[str, WorkflowExecutionCapability]:
    table: dict[str, WorkflowExecutionCapability] = {}
    for workflow, capabilities, remote_supported in _C01_INTEGRATED:
        table[workflow] = WorkflowExecutionCapability(
            workflow=workflow,
            capabilities=capabilities,
            closure="integrated",
            remote_supported=remote_supported,
        )
    for workflow, capabilities, remote_supported in _LEGACY_SCOPE:
        table[workflow] = WorkflowExecutionCapability(
            workflow=workflow,
            capabilities=capabilities,
            closure="legacy",
            remote_supported=remote_supported,
        )
    return table


#: The frozen workflow/release capability table (single authority).
FROZEN_WORKFLOW_CAPABILITY_TABLE: Final[dict[str, WorkflowExecutionCapability]] = _build_table()

#: The enabled scope (C01 integrated) — recorded for the enablement matrix.
ENABLED_WORKFLOWS: Final[frozenset[str]] = frozenset(
    name for name, cap in FROZEN_WORKFLOW_CAPABILITY_TABLE.items() if cap.closure == "integrated"
)


def capability_for(workflow: str) -> WorkflowExecutionCapability:
    """Return the frozen capability of *workflow* (raises when unknown)."""
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None:
        raise UnsupportedExecutionWorkflowError(workflow)
    return capability


# ── dispatcher ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionEntrySelection:
    """The dispatcher's entry decision for one attempt."""

    entry: str
    protocol: str
    closure: str


def select_execution_entry(
    workflow: str,
    *,
    remote: bool,
    envelope_available: bool,
) -> ExecutionEntrySelection:
    """Select the execution entry for *workflow* using the frozen table.

    Envelope only when the closure is integrated, the target protocol supports
    the workflow, and a frozen envelope/plan pair is available for the attempt;
    otherwise the legacy entry. The remote protocol is ``lsf``, the local one
    ``local``. A workflow outside the active table (retired / synthetic) is
    total-mapped to the legacy entry so existing paths never break.
    """
    protocol = EXECUTION_PROTOCOL_LSF if remote else EXECUTION_PROTOCOL_LOCAL
    capability = FROZEN_WORKFLOW_CAPABILITY_TABLE.get(workflow)
    if capability is None:
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, "legacy")
    if capability.closure != "integrated":
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, capability.closure)
    if remote and not capability.remote_supported:
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, capability.closure)
    if not envelope_available:
        return ExecutionEntrySelection(EXECUTION_ENTRY_LEGACY, protocol, capability.closure)
    return ExecutionEntrySelection(EXECUTION_ENTRY_ENVELOPE, protocol, capability.closure)


# ── per-attempt protocol record ─────────────────────────────────────────────


@dataclass(frozen=True)
class AttemptExecutionChoice:
    """The entry/protocol frozen on one attempt (the unique-dispatch record)."""

    attempt: int
    workflow: str
    entry: str
    protocol: str
    closure: str = ""
    release_id: str = ""
    schema: int = CURRENT_SWITCH_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "attempt": self.attempt,
            "workflow": self.workflow,
            "entry": self.entry,
            "protocol": self.protocol,
            "closure": self.closure,
            "release_id": self.release_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttemptExecutionChoice:
        if not isinstance(payload, Mapping):
            raise ValueError("attempt execution record must be a mapping")
        raw_attempt = payload.get("attempt", 1)
        attempt = int(raw_attempt) if isinstance(raw_attempt, (int, str)) else 1
        return cls(
            attempt=attempt,
            workflow=str(payload.get("workflow") or ""),
            entry=str(payload.get("entry") or EXECUTION_ENTRY_LEGACY),
            protocol=str(payload.get("protocol") or ""),
            closure=str(payload.get("closure") or ""),
            release_id=str(payload.get("release_id") or ""),
            schema=int(payload.get("schema", CURRENT_SWITCH_SCHEMA)),
        )


def resolve_attempt_choice(
    *,
    workflow: str,
    attempt: int,
    remote: bool,
    envelope_available: bool,
    previous: AttemptExecutionChoice | None = None,
    release_id: str = "",
) -> AttemptExecutionChoice:
    """Return the attempt's frozen choice, reusing it for an unchanged attempt.

    Resume/reconcile call with the already-recorded *previous* choice and the
    same attempt number: the choice (entry + protocol + release snapshot) is
    returned verbatim — the entry is never re-selected. A new attempt number
    re-selects from the frozen table.
    """
    if previous is not None and previous.attempt == attempt and previous.workflow == workflow:
        return previous
    selection = select_execution_entry(
        workflow, remote=remote, envelope_available=envelope_available
    )
    return AttemptExecutionChoice(
        attempt=attempt,
        workflow=workflow,
        entry=selection.entry,
        protocol=selection.protocol,
        closure=selection.closure,
        release_id=release_id,
    )


def _protocol_path(work_dir: Path | str) -> Path:
    return Path(work_dir).joinpath(*RUNTIME_PROTOCOL_DIR, ATTEMPT_PROTOCOL_FILENAME)


def read_attempt_choice(work_dir: Path | str) -> AttemptExecutionChoice | None:
    """Read the attempt protocol record from *work_dir* (``None`` when absent)."""
    target = _protocol_path(work_dir)
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    try:
        return AttemptExecutionChoice.from_dict(payload)
    except (TypeError, ValueError):
        return None


def write_attempt_choice(work_dir: Path | str, choice: AttemptExecutionChoice) -> Path:
    """Atomically write the attempt protocol record into *work_dir*."""
    target = _protocol_path(work_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(choice.to_dict(), sort_keys=True, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def attempt_choice_from_result(result: Mapping[str, Any] | None) -> AttemptExecutionChoice | None:
    """Read the choice recorded in a job ``result`` payload (``execution`` key)."""
    if not isinstance(result, Mapping):
        return None
    raw = result.get("execution")
    if not isinstance(raw, Mapping):
        return None
    try:
        return AttemptExecutionChoice.from_dict(raw)
    except (TypeError, ValueError):
        return None


def with_attempt_choice(
    result: Mapping[str, Any] | None, choice: AttemptExecutionChoice
) -> dict[str, Any]:
    """Return a copy of *result* with the attempt choice recorded under ``execution``."""
    updated: dict[str, Any] = dict(result or {})
    updated["execution"] = choice.to_dict()
    return updated


# ── frozen envelope artifacts + worker argv ─────────────────────────────────


@dataclass(frozen=True)
class ExecutionLaunch:
    """Everything the worker entry needs for one attempt."""

    entry: str
    protocol: str
    closure: str
    work_dir: str
    envelope_path: str
    plan_path: str
    binding_path: str | None = None


def frozen_artifacts_present(work_dir: Path | str) -> bool:
    """True when the frozen envelope + plan pair exists under *work_dir*."""
    runtime = Path(work_dir).joinpath(*RUNTIME_PROTOCOL_DIR)
    return (runtime / ENVELOPE_FILENAME).is_file() and (runtime / PLAN_FILENAME).is_file()


def local_launch(work_dir: Path | str, choice: AttemptExecutionChoice) -> ExecutionLaunch:
    """Build the local (absolute-path) worker launch for *choice*.

    The caller must only call this when ``choice.entry == envelope`` and the
    frozen artifacts are present (:func:`frozen_artifacts_present`); a missing
    pair is an explicit failure, never a silent legacy hop.
    """
    if choice.entry != EXECUTION_ENTRY_ENVELOPE:
        raise ValueError(f"attempt {choice.attempt} did not select the envelope entry")
    runtime = Path(work_dir).joinpath(*RUNTIME_PROTOCOL_DIR)
    envelope = runtime / ENVELOPE_FILENAME
    plan = runtime / PLAN_FILENAME
    if not envelope.is_file() or not plan.is_file():
        raise FileNotFoundError(
            f"frozen envelope/plan missing under {runtime} for attempt {choice.attempt}"
        )
    binding = runtime / BINDING_FILENAME
    return ExecutionLaunch(
        entry=choice.entry,
        protocol=choice.protocol,
        closure=choice.closure,
        work_dir=str(work_dir),
        envelope_path=str(envelope),
        plan_path=str(plan),
        binding_path=str(binding) if binding.is_file() else None,
    )


def worker_argv(
    python_executable: str,
    launch: ExecutionLaunch,
    *,
    work_dir: str | None = None,
    receipt_path: str | None = None,
    probe_executor: str | None = None,
) -> list[str]:
    """Build the ``python -m acp_services.worker_bootstrap`` argv (single root)."""
    py = python_executable or "python"
    argv: list[str] = [
        py,
        "-m",
        WORKER_MODULE,
        "--envelope",
        str(launch.envelope_path),
        "--plan",
        str(launch.plan_path),
        "--work-dir",
        str(work_dir if work_dir is not None else launch.work_dir),
    ]
    if receipt_path:
        argv += ["--receipt", str(receipt_path)]
    if launch.binding_path:
        argv += ["--binding", str(launch.binding_path)]
    if probe_executor:
        argv += ["--probe-executor", str(probe_executor)]
    return argv


def launch_with_remote_paths(
    launch: ExecutionLaunch,
    *,
    work_dir: str,
    envelope_path: str,
    plan_path: str,
    binding_path: str | None,
) -> ExecutionLaunch:
    """Re-point a launch at the node-side absolute paths (remote upload)."""
    return replace(
        launch,
        work_dir=work_dir,
        envelope_path=envelope_path,
        plan_path=plan_path,
        binding_path=binding_path,
    )


def iter_enabled_workflows() -> Iterable[str]:
    """Iterate the C01-integrated (enabled) workflow ids in table order."""
    return iter(ENABLED_WORKFLOWS)
