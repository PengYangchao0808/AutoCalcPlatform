#!/usr/bin/env python3
"""ACP 2.0 C-track experience acceptance harness.

This is the C consumer of B's single receipt contract
(``acp2.acceptance.v1``).  It never carries a look-alike receipt writer or
validator: every result file is produced by
``scripts/acceptance/acp2_runtime.py`` from ``$ACP2_PROJECT_ROOT`` and then
validated by the same module.  C only adds the namespaced
``experience.*`` extension values and the C-owned candidate revisions.

Three mutually exclusive modes:

1. scenario mode
   ``--scenario ID --out DIR --timeout SEC [--manifest ABS] [--phase PHASE]
   [--mode fixture|live] [--subcase ID] [--dry-run]``
2. receipt verification
   ``--verify-receipt PATH``
3. documentation placeholder check
   ``--check-doc-placeholders PATH``

Exit codes are exactly ``0=PASS`` (or a successful tool check),
``1=FAIL``, ``2=NOT_VERIFIED`` / not executed, ``3=usage error``.

``--help``, ``--dry-run`` and the two tool-check modes are never a scenario
PASS.  ``--dry-run`` only validates inputs, writes a ``NOT_VERIFIED`` receipt
and exits ``0``.  A known scenario without an implementation exits ``2`` when
it is not a dry run.  An unknown scenario exits ``3``.

``evidence_kind=live`` is not derived from the phase alone (M0-03/R10).  Every
C live scenario carries a frozen present/absent observation contract owned by
B's validator (``EXPERIENCE_REQUIRED_OBSERVATIONS``); the harness refuses to run
a live row whose scenario has no declared contract and publishes the frozen
declaration plus its own ``experience.observations`` payload.  A live PASS with
missing, mismatched or undeclared observations is rejected by the validator, so
an offline fixture can never enter a live row.

The module is stdlib-only and does not import the product, a model client, DSH
or a platform controller at import time.  ``--help`` therefore has no
connection, model or DSH side effect.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
SCRIPT_DIR = SCRIPT_PATH.parent

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3

ACCEPTANCE_SCHEMA = "acp2.acceptance.v1"
CONTRACT_MAJOR = "1"
MASK = "***"

# ---------------------------------------------------------------------------
# Frozen scenario registry (plan C section 3.3; 36 IDs, verbatim)
# ---------------------------------------------------------------------------

SCENARIO_SECTIONS: dict[str, tuple[str, ...]] = {
    "P0": (
        "baseline",
        "contracts",
        "workspace",
        "harness-selftest",
        "node-dsh-install",
        "browser-install",
        "bridge-contract",
        "mcp-live",
        "dsh-discovery-live",
        "model-live",
    ),
    "P1": (
        "client-fixtures",
        "manual-browser-fixtures",
        "dsh-local-singlepoint-live",
        "dsh-lsf-singlepoint-live",
        "dsh-optfreq-live",
        "same-task-browser-live",
        "client-recovery-live",
    ),
    "P2": (
        "question-reaction-fixtures",
        "research-record-fixtures",
        "approval-budget-live",
        "research-loop-live",
        "evidence-browser-live",
        "research-resume-live",
        "fault-boundaries-live",
        "da-research-live",
        "p2-closeout",
    ),
    "P3": (
        "plugin-discovery-live",
        "schema-form-live",
        "plugin-upgrade-live",
    ),
    "P4": (
        "cross-domain-live",
        "multi-project-live",
        "experience-upgrade-live",
    ),
    "P5": (
        "client-release",
        "clean-install-live",
        "operator-runbook-live",
        "experience-release-closeout",
    ),
}

SCENARIO_IDS: tuple[str, ...] = tuple(
    scenario for section in SCENARIO_SECTIONS.values() for scenario in section
)

LIVE_SCENARIOS: tuple[str, ...] = (
    "mcp-live",
    "dsh-discovery-live",
    "model-live",
    "dsh-local-singlepoint-live",
    "dsh-lsf-singlepoint-live",
    "dsh-optfreq-live",
    "same-task-browser-live",
    "client-recovery-live",
    "approval-budget-live",
    "research-loop-live",
    "research-resume-live",
    "fault-boundaries-live",
    "da-research-live",
    "plugin-discovery-live",
    "schema-form-live",
    "plugin-upgrade-live",
    "cross-domain-live",
    "multi-project-live",
    "experience-upgrade-live",
    "clean-install-live",
    "operator-runbook-live",
)

# evidence-browser-live is deliberately fixture-by-default; live is explicit.
EXPLICIT_LIVE_SCENARIOS: tuple[str, ...] = ("evidence-browser-live",)
LIVE_ALLOWED = frozenset(LIVE_SCENARIOS + EXPLICIT_LIVE_SCENARIOS)
LIVE_DEFAULT = frozenset(LIVE_SCENARIOS)

# Scenarios whose default phase is the common ``main`` phase, not a mode phase.
MAIN_PHASE_SCENARIOS = frozenset(("p2-closeout", "client-release", "experience-release-closeout"))

SUBCASE_WHITELIST: dict[str, tuple[str, ...]] = {
    "cross-domain-live": ("nmr", "thermo", "selectivity"),
    "research-loop-live": ("control", "search"),
}

# Common (un-namespaced) phases published by the B01 registry.
COMMON_PHASES = (
    "main",
    "approval",
    "budget",
    "da-preflight",
    "da-submit",
    "da-evidence",
    "aggregate",
)

# Scenarios whose current evidence content is fixture-only.  Such a scenario
# may accept the common phase vocabulary on the command line, but it must
# never publish ``evidence_kind=live``: no Controller/DSH/model/QC/LSF/browser
# process is started for those rows.  The C workbench owns this classification
# (B does not know which C content is fixture), so it lives here next to the
# phase normalizer.
# M4-02 removed ``approval-budget-live`` from this set: the row now runs a real
# Controller with two independent browser identities, real approval decisions
# and a real budget refusal, so a live run is exactly what it must be.
FIXTURE_ONLY_SCENARIOS: frozenset[str] = frozenset()

# Scenario-level phase aliases.  The CLI keeps accepting B01's common phase
# (``--phase approval`` is T-27's required invocation) while the receipt is
# written with the honest public phase.  The requested phase stays available to
# the scenario body through ``ResolvedIdentity.requested_phase``.
PHASE_ALIASES: dict[str, dict[str, str]] = {}

RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
SECRET_KEY_RE = re.compile(r"(?i)(secret|token|password|api[_-]?key|authorization|credential)")
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
PLACEHOLDER_RE = re.compile(r"<[^>\n]+>")
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")


# ---------------------------------------------------------------------------
# Dynamic imports: B's single validator and C's scenario registry
# ---------------------------------------------------------------------------


_RUNTIME: ModuleType | None = None
_RUNTIME_ERROR: str | None = None
_SCENARIOS: dict[str, Callable[[Any], int]] | None = None


def _runtime_candidates() -> Iterable[Path]:
    explicit = os.environ.get("ACP2_RUNTIME_PATH")
    if explicit:
        yield Path(explicit)
    project = os.environ.get("ACP2_PROJECT_ROOT")
    if project:
        yield Path(project) / "scripts" / "acceptance" / "acp2_runtime.py"
    # Last resort: walk up from this script (works when the C harness is run
    # from a checkout that also carries the B1 runtime).
    for parent in SCRIPT_PATH.parents:
        yield parent / "scripts" / "acceptance" / "acp2_runtime.py"


def load_runtime() -> ModuleType | None:
    """Import B's single receipt validator/writer, or return ``None``."""

    global _RUNTIME, _RUNTIME_ERROR
    if _RUNTIME is not None:
        return _RUNTIME
    if _RUNTIME_ERROR is not None:
        return None
    seen: set[Path] = set()
    for candidate in _runtime_candidates():
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            continue
        spec = importlib.util.spec_from_file_location("acp2_runtime_b01", resolved)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError) as exc:  # pragma: no cover - defensive
            _RUNTIME_ERROR = f"cannot import B validator {resolved}: {exc}"
            print(f"NOT_VERIFIED: {_RUNTIME_ERROR}", file=sys.stderr)
            return None
        schema_path = getattr(module, "SCHEMA_PATH", None)
        if (
            getattr(module, "ACCEPTANCE_SCHEMA", None) != ACCEPTANCE_SCHEMA
            or not isinstance(schema_path, Path)
            or not schema_path.is_file()
        ):
            _RUNTIME_ERROR = f"B validator contract/schema mismatch at {resolved}"
            print(f"NOT_VERIFIED: {_RUNTIME_ERROR}", file=sys.stderr)
            return None
        _RUNTIME = module
        return module
    _RUNTIME_ERROR = "B validator scripts/acceptance/acp2_runtime.py not found"
    print(f"NOT_VERIFIED: {_RUNTIME_ERROR}", file=sys.stderr)
    return None


def load_scenario_impls() -> dict[str, Callable[[Any], int]]:
    """Import the C scenario registry (empty until T-07 registers bodies)."""

    global _SCENARIOS
    if _SCENARIOS is not None:
        return _SCENARIOS
    path = SCRIPT_DIR / "acp2_experience_scenarios.py"
    if not path.is_file():
        _SCENARIOS = {}
        return _SCENARIOS
    spec = importlib.util.spec_from_file_location("acp2_experience_scenarios", path)
    if spec is None or spec.loader is None:
        _SCENARIOS = {}
        return _SCENARIOS
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    registry = getattr(module, "SCENARIO_IMPLS", {})
    _SCENARIOS = dict(registry) if isinstance(registry, dict) else {}
    return _SCENARIOS


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write_json(path: Path, document: Any) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _git_revision(source: str | None) -> str | None:
    if not source:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", source, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and REVISION_RE.match(revision) else None


def _first_revision(*values: str | None) -> str | None:
    for value in values:
        if isinstance(value, str) and REVISION_RE.match(value):
            return value
    return None


# ---------------------------------------------------------------------------
# Redaction and placeholder checks
# ---------------------------------------------------------------------------


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a copy with secret-looking keys and Bearer tokens masked."""

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for child_key, child_value in value.items():
            if isinstance(child_key, str) and SECRET_KEY_RE.search(child_key):
                # A numeric count or boolean under a token-shaped key
                # (``usage.total_tokens``) is a measurement, not a credential.
                # Masking it would erase the budget evidence the receipt
                # contract requires.
                if isinstance(child_value, (int, float, bool)):
                    cleaned[child_key] = child_value
                else:
                    cleaned[child_key] = MASK
            else:
                cleaned[child_key] = redact(child_value, key=child_key)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return BEARER_RE.sub(MASK, value)
    return value


def _strip_html_comments(line: str, in_comment: bool) -> tuple[str, bool]:
    pieces: list[str] = []
    index = 0
    while index < len(line):
        if in_comment:
            end = line.find("-->", index)
            if end == -1:
                return "".join(pieces), True
            index = end + 3
            in_comment = False
            continue
        start = line.find("<!--", index)
        if start == -1:
            pieces.append(line[index:])
            break
        pieces.append(line[index:start])
        index = start + 4
        in_comment = True
    return "".join(pieces), in_comment


def check_doc_placeholders(path: Path) -> int:
    """Exit 1 when a body placeholder is found outside fences/comments."""

    if not path.is_file():
        print(f"usage error: documentation file not found: {path}", file=sys.stderr)
        return EXIT_USAGE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"usage error: cannot read {path}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    in_fence = False
    in_comment = False
    violations: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        cleaned, in_comment = _strip_html_comments(line, in_comment)
        for match in PLACEHOLDER_RE.finditer(cleaned):
            violations.append((line_number, match.group(0)))
    if in_comment:
        print(f"PLACEHOLDER: {path}:unterminated HTML comment", file=sys.stderr)
        return EXIT_FAIL
    if violations:
        for line_number, token in violations:
            print(f"PLACEHOLDER: {path}:{line_number}: {token}", file=sys.stderr)
        return EXIT_FAIL
    print(f"CLEAN: {path}")
    return EXIT_PASS


# ---------------------------------------------------------------------------
# Receipt verification
# ---------------------------------------------------------------------------


def _resolve_artifact_path(raw_path: str, document: dict[str, Any], root: Path) -> list[Path]:
    candidates: list[Path] = []
    if Path(raw_path).is_absolute():
        candidates.append(Path(raw_path))
    cwd = document.get("cwd")
    if isinstance(cwd, str) and Path(cwd).is_absolute():
        candidates.append(Path(cwd) / raw_path)
    candidates.append(root / raw_path)
    return candidates


def _self_reference_errors(
    document: dict[str, Any],
    receipt_path: Path,
    runtime: ModuleType,
    *extra_paths: Path,
) -> list[tuple[int, str]]:
    """Detect an artifact that is this receipt/summary or its own digest."""

    targets = {receipt_path.resolve()}
    targets.update(path.resolve() for path in extra_paths)
    try:
        receipt_digest = runtime.sha256_file(receipt_path)
    except OSError:
        receipt_digest = None
    errors: list[tuple[int, str]] = []
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        return errors
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        for candidate in _resolve_artifact_path(raw_path, document, receipt_path.parent):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in targets:
                errors.append((index, f"artifact_self_reference:{raw_path}"))
                break
        else:
            digest = artifact.get("sha256")
            if receipt_digest is not None and digest == receipt_digest:
                errors.append((index, f"artifact_self_digest:{raw_path}"))
    return errors


def verify_receipt(path: Path) -> int:
    runtime = load_runtime()
    if runtime is None:
        return EXIT_NOT_VERIFIED
    if not path.is_file():
        print(f"REJECT: receipt not found: {path}", file=sys.stderr)
        return EXIT_FAIL
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"REJECT: json_parse:{path.name}:{exc.msg}", file=sys.stderr)
        return EXIT_FAIL
    errors = list(
        runtime.validate_receipt(
            document,
            verify_artifacts=True,
            artifact_root=path.parent,
        )
    )
    if isinstance(document, dict):
        errors.extend(reason for _, reason in _self_reference_errors(document, path, runtime))
    if errors:
        for reason in errors:
            print(f"REJECT: {reason}", file=sys.stderr)
        return EXIT_FAIL
    print(f"VALID: {path}")
    return EXIT_PASS


# ---------------------------------------------------------------------------
# Scenario metadata and phase normalization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedIdentity:
    scenario: str
    scenario_key: str
    phase: str
    evidence_kind: str
    mode: str | None
    subcase: str | None
    requested_phase: str | None = None


def validate_registry_environment() -> str | None:
    """Fail closed when the acceptance env's frozen lists diverge from ours."""

    declared_all = os.environ.get("ACP2_ALL_SCENARIOS")
    if declared_all is not None:
        declared = declared_all.split()
        if declared != list(SCENARIO_IDS):
            missing = [item for item in SCENARIO_IDS if item not in declared]
            extra = [item for item in declared if item not in SCENARIO_IDS]
            return f"scenario_enum_divergence:missing={missing};extra={extra}"
    declared_live = os.environ.get("ACP2_LIVE_SCENARIOS")
    if declared_live is not None:
        declared = declared_live.split()
        if declared != list(LIVE_SCENARIOS):
            missing = [item for item in LIVE_SCENARIOS if item not in declared]
            extra = [item for item in declared if item not in LIVE_SCENARIOS]
            return f"live_enum_divergence:missing={missing};extra={extra}"
    return None


def _derive_path_context(out_dir: Path, scenario: str) -> tuple[str | None, list[str]]:
    parts = out_dir.resolve().parts
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] != scenario or index + 1 >= len(parts):
            continue
        run_id = parts[index + 1]
        if RUN_ID_RE.match(run_id):
            return run_id, list(parts[index + 2 :])
    return None, []


def _infer_from_suffix(scenario: str, suffix: list[str]) -> dict[str, str | None] | None:
    if not suffix:
        return {"phase": None, "mode": None, "subcase": None}
    inferred: dict[str, str | None] = {
        "phase": suffix[0],
        "mode": None,
        "subcase": None,
    }
    rest = suffix[1:]
    if rest and rest[0] in ("fixture", "live"):
        inferred["mode"] = rest[0]
        rest = rest[1:]
    if rest and rest[0] in SUBCASE_WHITELIST.get(scenario, ()):
        inferred["subcase"] = rest[0]
        rest = rest[1:]
    if rest:
        return None
    return inferred


def _merge(cli_value: str | None, inferred: str | None, label: str) -> str | None:
    if cli_value and inferred and cli_value != inferred:
        raise ValueError(f"path identity conflict for {label}: {cli_value} != {inferred}")
    return cli_value or inferred


def required_observations_for(runtime: ModuleType, scenario_key: str) -> dict[str, str] | None:
    """Return B's frozen present/absent contract for one C live scenario."""

    resolver = getattr(runtime, "required_observations", None)
    if callable(resolver):
        declared = resolver(scenario_key)
        if isinstance(declared, dict):
            return declared
    registry = getattr(runtime, "EXPERIENCE_REQUIRED_OBSERVATIONS", None)
    if isinstance(registry, dict):
        declared = registry.get(scenario_key)
        if isinstance(declared, dict):
            return dict(declared)
    return None


def _observation_contract_error(identity: ResolvedIdentity, runtime: ModuleType) -> str | None:
    """Fail closed when a live row has no declared required observations.

    ``evidence_kind=live`` is no longer derived from the phase alone: the row
    must be a live-registered scenario *and* carry a frozen present/absent
    observation contract.  An undeclared live scenario is a contract error, not
    a default accept.
    """

    if identity.evidence_kind != "live":
        return None
    registry = getattr(runtime, "EXPERIENCE_REQUIRED_OBSERVATIONS", None)
    if not isinstance(registry, dict) or not registry:
        return "experience_observations_registry_missing"
    if required_observations_for(runtime, identity.scenario_key) is None:
        return f"experience_observations_undeclared:{identity.scenario_key}"
    return None


def _resolve_identity(
    args: argparse.Namespace,
) -> tuple[ResolvedIdentity | None, str | None]:
    scenario = args.scenario
    if scenario not in SCENARIO_IDS:
        return None, f"unknown scenario {scenario!r}"

    derived_run_id, suffix = _derive_path_context(args.out, scenario)
    inferred = _infer_from_suffix(scenario, suffix)
    if inferred is None:
        return None, f"unexpected --out suffix after run_id: {'/'.join(suffix)}"

    try:
        phase = _merge(args.phase, inferred["phase"], "phase")
        mode = _merge(args.mode, inferred["mode"], "mode")
        subcase = _merge(args.subcase, inferred["subcase"], "subcase")
    except ValueError as exc:
        return None, str(exc)

    if phase is not None and phase not in COMMON_PHASES:
        return None, f"unknown namespaced/common phase {phase!r}"

    requested_phase = phase
    phase_alias = PHASE_ALIASES.get(scenario, {})

    # ``c_accept`` uses ``main`` as its default leaf segment for every scenario
    # (route_select never forwards --phase).  For fixture/live-default
    # scenarios that segment is a directory default, not a requested public
    # phase: normalize it to the scenario default.  ``main`` remains meaningful
    # for the closeout/release scenarios.
    if phase == "main" and scenario not in MAIN_PHASE_SCENARIOS:
        phase = None

    if mode is not None and mode not in ("fixture", "live"):
        return None, f"unknown mode {mode!r}"
    if mode == "live" and scenario not in LIVE_ALLOWED:
        return None, f"--mode live is not registered for scenario {scenario!r}"
    if mode == "live" and scenario in FIXTURE_ONLY_SCENARIOS:
        return None, f"--mode live is unavailable for fixture-only scenario {scenario!r}"

    if subcase is not None:
        allowed = SUBCASE_WHITELIST.get(scenario, ())
        if subcase not in allowed:
            return None, f"subcase {subcase!r} is not registered for scenario {scenario!r}"

    if phase == "aggregate":
        if scenario == "evidence-browser-live":
            return None, "evidence-browser-live uses a terminal live result; aggregate is forbidden"
        if subcase is not None:
            return None, "--phase aggregate cannot be combined with --subcase"
        resolved_phase = "aggregate"
    elif phase is not None:
        resolved_phase = phase_alias.get(phase, phase)
    elif subcase is not None:
        resolved_phase = f"experience:{subcase}"
    elif mode is not None:
        resolved_phase = f"experience:{mode}"
    elif scenario in FIXTURE_ONLY_SCENARIOS:
        resolved_phase = "experience:fixture"
    elif scenario in MAIN_PHASE_SCENARIOS:
        resolved_phase = "main"
    elif scenario in LIVE_DEFAULT:
        resolved_phase = "experience:live"
    else:
        resolved_phase = "experience:fixture"

    if resolved_phase == "aggregate":
        evidence_kind = "aggregate"
    elif resolved_phase == "fixture" or resolved_phase.endswith(":fixture"):
        evidence_kind = "fixture"
    elif scenario in FIXTURE_ONLY_SCENARIOS:
        evidence_kind = "fixture"
    else:
        evidence_kind = "live"

    effective_mode = mode
    if effective_mode is None:
        if scenario in FIXTURE_ONLY_SCENARIOS:
            effective_mode = "fixture"
        elif scenario in LIVE_DEFAULT or resolved_phase in ("main", "experience:live"):
            effective_mode = "live"
        elif resolved_phase.endswith(":live"):
            effective_mode = "live"
        else:
            effective_mode = "fixture"

    return (
        ResolvedIdentity(
            scenario=f"experience:{scenario}",
            scenario_key=scenario,
            phase=resolved_phase,
            evidence_kind=evidence_kind,
            mode=effective_mode,
            subcase=subcase,
            requested_phase=requested_phase,
        ),
        None,
    )


def resolve_run_id(args: argparse.Namespace, scenario: str) -> tuple[str | None, str | None]:
    configured = os.environ.get("ACP2_RUN_ID")
    derived, _ = _derive_path_context(args.out, scenario)
    if configured:
        if not RUN_ID_RE.match(configured):
            return None, f"ACP2_RUN_ID is not a valid run id: {configured!r}"
        if derived and derived != configured:
            return None, f"ACP2_RUN_ID ({configured}) != path run id ({derived})"
        return configured, None
    if derived:
        return derived, None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    generated = f"{scenario}-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    return generated, None


# ---------------------------------------------------------------------------
# Manifest validation (C experience extension, not the B receipt schema)
# ---------------------------------------------------------------------------


def _validate_manifest(
    path: Path, evidence_root: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_absolute():
        return None, f"--manifest must be an absolute path: {path}"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, f"manifest_missing:{path}"
    except json.JSONDecodeError as exc:
        return None, f"manifest_json_invalid:{exc.msg}"
    except OSError as exc:
        return None, f"manifest_unreadable:{exc}"
    if not isinstance(document, dict):
        return None, "manifest_not_object"
    candidate_id = document.get("candidate_id")
    if not isinstance(candidate_id, str) or not REVISION_RE.match(candidate_id):
        return None, "manifest_missing:candidate_id"
    schema = document.get("schema")
    if schema is not None and not (
        isinstance(schema, str) and schema.startswith("acp2.experience.manifest-extension.v")
    ):
        return None, f"manifest_schema_unsupported:{schema}"
    version = document.get("schema_version")
    if version is not None and not (
        isinstance(version, str) and version.startswith(f"{CONTRACT_MAJOR}.")
    ):
        return None, f"manifest_schema_version_unsupported:{version}"
    declared_root = document.get("evidence_root")
    if declared_root is not None and evidence_root:
        if not isinstance(declared_root, str) or not declared_root:
            return None, "manifest_evidence_root_invalid"
        raw = Path(declared_root)
        if not raw.is_absolute():
            project = os.environ.get("ACP2_PROJECT_ROOT")
            if not project:
                return None, "manifest_evidence_root_unresolvable"
            raw = Path(project) / raw
        if raw.resolve() != Path(evidence_root).resolve():
            return None, "manifest_evidence_root_mismatch"
    return document, None


# ---------------------------------------------------------------------------
# C-owned extension values
# ---------------------------------------------------------------------------


def _product_import() -> str:
    spec = importlib.util.find_spec("acp")
    if spec is not None and spec.origin:
        return str(Path(spec.origin).resolve())
    for variable in ("ACP2_PRODUCT_SOURCE", "ACP2_C_SOURCE"):
        value = os.environ.get(variable)
        if value:
            return str(Path(value).resolve())
    return "not_importable"


def _candidate_values(runtime: ModuleType) -> dict[str, str]:
    c_source = os.environ.get("ACP2_C_SOURCE")
    product_source = os.environ.get("ACP2_PRODUCT_SOURCE")
    candidate = _first_revision(
        os.environ.get("ACP_CANDIDATE_REVISION"),
        os.environ.get("ACP2_CANDIDATE_REVISION"),
        _git_revision(c_source),
        _git_revision(product_source),
        getattr(runtime, "BASELINE_CANDIDATE_REVISION", None),
    )
    client = _first_revision(
        os.environ.get("ACP2_CLIENT_REVISION"),
        _git_revision(c_source),
        candidate,
    )
    test = _first_revision(os.environ.get("ACP2_TEST_REVISION"), client, candidate)
    if candidate is None or client is None or test is None:  # pragma: no cover
        candidate = candidate or "sha256:" + "0" * 64
        client = client or candidate
        test = test or candidate
    return {
        "candidate_revision": candidate,
        "client_revision": client,
        "test_revision": test,
    }


def _experience_extensions(
    identity: ResolvedIdentity,
    run_id: str,
    runtime: ModuleType,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    revisions = _candidate_values(runtime)
    extensions: dict[str, Any] = {
        "experience.mode": identity.mode,
        "experience.subcase": identity.subcase,
        "experience.scenario_key": identity.scenario_key,
        "experience.interpreter": sys.executable,
        "experience.product_import": _product_import(),
        "experience.harness_path": str(SCRIPT_PATH),
        "experience.test_revision": revisions["test_revision"],
        "experience.harness_contract_revision": runtime.sha256_file(runtime.SCRIPT_PATH),
        "experience.run_id": run_id,
    }
    if manifest is not None:
        extensions["experience.manifest"] = redact(manifest)
    if identity.evidence_kind == "live":
        declared = required_observations_for(runtime, identity.scenario_key)
        if declared is not None:
            extensions["experience.declared_observations"] = declared
    return extensions


# ---------------------------------------------------------------------------
# Scenario execution scaffolding
# ---------------------------------------------------------------------------


@dataclass
class ScenarioContext:
    identity: ResolvedIdentity
    run_id: str
    out_dir: Path
    timeout: float
    manifest: dict[str, Any] | None
    assertions: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    extensions: dict[str, Any] = field(default_factory=dict)
    refs: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] | None = None
    reason: str | None = None


def _artifact_record(path: Path, out_dir: Path, runtime: ModuleType) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(out_dir.resolve())
        recorded = str(relative)
    except ValueError:
        recorded = str(resolved)
    return {
        "path": recorded,
        "size_bytes": resolved.stat().st_size,
        "sha256": runtime.sha256_file(resolved),
    }


def _normalize_artifacts(
    context: ScenarioContext,
    artifacts: list[dict[str, Any]],
    runtime: ModuleType,
) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("artifact_not_object")
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append("artifact_missing_path")
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = context.out_dir / path
        if not path.is_file():
            errors.append(f"artifact_missing:{raw_path}")
            continue
        normalized.append(_artifact_record(path, context.out_dir, runtime))
    return normalized, errors


def _dry_run_actions(
    identity: ResolvedIdentity,
    run_id: str,
    out_dir: Path,
    manifest_path: Path | None,
    has_impl: bool,
) -> list[str]:
    actions = [
        f"scenario={identity.scenario} scenario_key={identity.scenario_key}",
        f"phase={identity.phase} evidence_kind={identity.evidence_kind} "
        f"mode={identity.mode} subcase={identity.subcase}",
        f"run_id={run_id} out={out_dir}",
        f"manifest={manifest_path if manifest_path is not None else 'none'}",
        "validate argv, frozen scenario enum, phase/mode/subcase whitelist and append-only out",
        "fail closed when a live scenario has no declared required observations",
        "publish experience.declared_observations and experience.observations for live rows",
        "no Controller is started, no model call is made, no DSH/QC process is launched",
        f"scenario body registered={has_impl} (T-07 adds the P0 bodies)",
        "write one acp2.acceptance.v1 receipt with result=NOT_VERIFIED through B's write_result",
        "validate the receipt with B's validate_receipt and exit 0 (tool check, never PASS)",
    ]
    return actions


def _write_receipt(
    runtime: ModuleType,
    *,
    identity: ResolvedIdentity,
    run_id: str,
    out_dir: Path,
    started_at: str,
    finished_at: str,
    result: str,
    reason: str | None,
    exit_code: int,
    command: list[str],
    assertions: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    refs: dict[str, Any],
    usage: dict[str, Any] | None,
    extensions: dict[str, Any],
    sub_results: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, int]:
    run_path, summary_path = runtime.write_result(
        out_dir,
        scenario=identity.scenario,
        phase=identity.phase,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        reason=reason,
        exit_code=exit_code,
        command=redact(command),
        evidence_kind=identity.evidence_kind,
        owner="C",
        assertions=redact(assertions),
        artifacts=artifacts,
        refs=refs,
        usage=usage,
        extensions=extensions,
    )

    document = json.loads(run_path.read_text(encoding="utf-8"))
    revisions = _candidate_values(runtime)
    document["candidate_revision"] = revisions["candidate_revision"]
    document.setdefault("candidate", {})
    document["candidate"]["client_revision"] = revisions["client_revision"]
    document["candidate"]["harness_revision"] = runtime.harness_revision()
    if sub_results is not None:
        document["sub_results"] = redact(sub_results)
    document.setdefault("extensions", {})
    document["extensions"].update(extensions)
    document = redact(document)

    self_reference = _self_reference_errors(document, run_path, runtime, summary_path)
    if self_reference:
        offending = {index for index, _ in self_reference}
        document["artifacts"] = [
            artifact
            for index, artifact in enumerate(document.get("artifacts", []))
            if index not in offending
        ]
        document["result"] = "FAIL"
        document["reason"] = "artifact_self_reference"
        document["exit_code"] = EXIT_FAIL
        exit_code = EXIT_FAIL
        for _, reason_text in self_reference:
            print(f"FAIL: {reason_text}", file=sys.stderr)

    _atomic_write_json(run_path, document)

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["candidate_revision"] = document["candidate_revision"]
    _atomic_write_json(summary_path, summary)

    errors = runtime.validate_receipt(document, verify_artifacts=True, artifact_root=out_dir)
    if errors:
        run_path.unlink(missing_ok=True)
        runs = [
            item
            for item in summary.get("runs", [])
            if isinstance(item, str) and item != run_path.name
        ]
        if runs:
            summary["runs"] = runs
            _atomic_write_json(summary_path, summary)
        else:
            summary_path.unlink(missing_ok=True)
        for reason_text in errors:
            print(f"INVALID: {reason_text}", file=sys.stderr)
        return None, EXIT_FAIL
    return document, exit_code


def aggregate_decision(
    child_results: list[dict[str, Any]],
    expected_phases: list[str],
) -> tuple[str, list[str]]:
    """Frozen aggregate rule shared by the harness and its unit tests.

    * any executed child FAIL -> FAIL;
    * no child -> NOT_VERIFIED;
    * a missing/NOT_VERIFIED/non-PASS child -> NOT_VERIFIED with the gaps;
    * every required child PASS -> PASS.
    """

    by_phase = {document.get("phase"): document for document in child_results}
    gaps = [
        phase
        for phase in expected_phases
        if phase not in by_phase or by_phase[phase].get("result") != "PASS"
    ]
    if any(document.get("result") == "FAIL" for document in child_results):
        return "FAIL", gaps
    if not child_results:
        return "NOT_VERIFIED", gaps or ["no_child_results"]
    if gaps or any(document.get("result") != "PASS" for document in child_results):
        return "NOT_VERIFIED", gaps
    return "PASS", gaps


def _run_aggregate(
    identity: ResolvedIdentity,
    run_id: str,
    out_dir: Path,
    timeout: float,
    started_at: str,
    command: list[str],
) -> int:
    run_root = out_dir
    while run_root.name != run_id and run_root.parent != run_root:
        run_root = run_root.parent
    if run_root.name != run_id:
        run_root = out_dir.parent

    children: list[tuple[Path, dict[str, Any]]] = []
    for candidate in sorted(run_root.rglob("*.json")):
        try:
            candidate.resolve().relative_to(out_dir.resolve())
            continue
        except ValueError:
            pass
        try:
            document = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        if document.get("schema") != ACCEPTANCE_SCHEMA:
            continue
        if document.get("owner") != "C":
            continue
        if document.get("scenario") != identity.scenario:
            continue
        if document.get("run_id") != run_id:
            continue
        if document.get("phase") in (None, "aggregate"):
            continue
        children.append((candidate, document))

    expected = [f"experience:{item}" for item in SUBCASE_WHITELIST.get(identity.scenario_key, ())]
    result, gaps = aggregate_decision(
        [document for _, document in children],
        expected,
    )
    exit_code = {
        "PASS": EXIT_PASS,
        "FAIL": EXIT_FAIL,
        "NOT_VERIFIED": EXIT_NOT_VERIFIED,
    }[result]

    if result == "NOT_VERIFIED" and not children:
        print(
            "NOT_VERIFIED: aggregate has no child result under "
            f"{run_root}; gaps={gaps or ['no_child_results']}",
            file=sys.stderr,
        )
        return EXIT_NOT_VERIFIED

    sub_results: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    for path, document in children:
        try:
            reference = str(path.resolve().relative_to(out_dir.resolve()))
        except ValueError:
            reference = str(path.resolve())
        sub_results.append(
            {
                "scenario": document.get("scenario"),
                "owner": document.get("owner"),
                "phase": document.get("phase"),
                "receipt": reference,
                "result": document.get("result"),
                "run_id": document.get("run_id"),
            }
        )
        assertions.append(
            {
                "name": f"child:{document.get('phase')}",
                "status": document.get("result"),
                "detail": reference,
            }
        )

    required = (
        expected
        if expected and not gaps
        else sorted(str(document.get("phase")) for _, document in children)
    )
    extensions: dict[str, Any] = {"acceptance.required_child_phases": required}
    if gaps:
        extensions["experience.aggregate_gaps"] = gaps
        assertions.append(
            {
                "name": "aggregate_gaps",
                "status": "NOT_VERIFIED",
                "detail": ",".join(gaps),
            }
        )

    runtime = load_runtime()
    if runtime is None:
        return EXIT_NOT_VERIFIED
    revisions = _candidate_values(runtime)
    finished_at = _utcnow()
    reason = None
    if result == "NOT_VERIFIED":
        reason = "aggregate_incomplete:" + ",".join(gaps or ["child_not_pass"])
    elif result == "FAIL":
        reason = "aggregate_child_failed"

    extensions.update(
        {
            "experience.mode": identity.mode,
            "experience.subcase": None,
            "experience.scenario_key": identity.scenario_key,
            "experience.interpreter": sys.executable,
            "experience.product_import": _product_import(),
            "experience.harness_path": str(SCRIPT_PATH),
            "experience.test_revision": revisions["test_revision"],
            "experience.harness_contract_revision": runtime.sha256_file(runtime.SCRIPT_PATH),
            "experience.run_id": run_id,
        }
    )

    document, written_code = _write_receipt(
        runtime,
        identity=identity,
        run_id=run_id,
        out_dir=out_dir,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        reason=reason,
        exit_code=exit_code,
        command=command,
        assertions=assertions,
        artifacts=[],
        refs={},
        usage=None,
        extensions=extensions,
        sub_results=sub_results,
    )
    if document is None:
        return written_code
    print(f"{result}: {out_dir} ({len(children)} child receipt(s))")
    return exit_code


# ---------------------------------------------------------------------------
# Scenario mode
# ---------------------------------------------------------------------------


def run_scenario(args: argparse.Namespace) -> int:
    if args.scenario not in SCENARIO_IDS:
        print(f"usage error: unknown scenario {args.scenario!r}", file=sys.stderr)
        return EXIT_USAGE
    if args.timeout is None or not args.timeout > 0:
        print("usage error: --timeout must be a positive number", file=sys.stderr)
        return EXIT_USAGE
    if args.out is None:
        print("usage error: --out is required in scenario mode", file=sys.stderr)
        return EXIT_USAGE
    if args.manifest is not None and not args.manifest.is_absolute():
        print("usage error: --manifest must be an absolute path", file=sys.stderr)
        return EXIT_USAGE

    divergence = validate_registry_environment()
    if divergence is not None:
        print(f"NOT_VERIFIED: {divergence}", file=sys.stderr)
        return EXIT_NOT_VERIFIED

    identity, error = _resolve_identity(args)
    if identity is None:
        print(f"usage error: {error}", file=sys.stderr)
        return EXIT_USAGE

    run_id, error = resolve_run_id(args, args.scenario)
    if run_id is None:
        print(f"usage error: {error}", file=sys.stderr)
        return EXIT_USAGE

    out_dir = args.out
    if out_dir.exists():
        if not out_dir.is_dir():
            print(f"usage error: --out is not a directory: {out_dir}", file=sys.stderr)
            return EXIT_USAGE
        if any(out_dir.iterdir()):
            if os.environ.get("ACP2_RUN_ID"):
                print(
                    f"usage error: fixed ACP2_RUN_ID={os.environ['ACP2_RUN_ID']} "
                    f"with non-empty leaf {out_dir}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"usage error: refusing non-empty evidence directory: {out_dir}",
                    file=sys.stderr,
                )
            return EXIT_USAGE

    runtime = load_runtime()
    if runtime is None:
        return EXIT_NOT_VERIFIED

    observation_error = _observation_contract_error(identity, runtime)
    if observation_error is not None:
        print(f"NOT_VERIFIED: {observation_error}", file=sys.stderr)
        return EXIT_NOT_VERIFIED

    manifest: dict[str, Any] | None = None
    if args.manifest is not None:
        manifest, error = _validate_manifest(args.manifest, os.environ.get("ACP2_EVIDENCE"))
        if error is not None:
            print(f"NOT_VERIFIED: {error}", file=sys.stderr)
            return EXIT_NOT_VERIFIED

    started_at = _utcnow()
    command = [sys.executable, str(SCRIPT_PATH), *sys.argv[1:]]
    impls = load_scenario_impls()
    has_impl = args.scenario in impls

    if identity.phase == "aggregate":
        return _run_aggregate(identity, run_id, out_dir, args.timeout, started_at, command)

    if args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        actions = _dry_run_actions(identity, run_id, out_dir, args.manifest, has_impl)
        dry_run_path = out_dir / "dry-run.txt"
        dry_run_path.write_text("\n".join(actions) + "\n", encoding="utf-8")
        artifacts = [_artifact_record(dry_run_path, out_dir, runtime)]
        if manifest is not None:
            manifest_path = out_dir / "manifest.redacted.json"
            _atomic_write_json(manifest_path, redact(manifest))
            artifacts.append(_artifact_record(manifest_path, out_dir, runtime))
        extensions = _experience_extensions(identity, run_id, runtime, manifest)
        document, exit_code = _write_receipt(
            runtime,
            identity=identity,
            run_id=run_id,
            out_dir=out_dir,
            started_at=started_at,
            finished_at=_utcnow(),
            result="NOT_VERIFIED",
            reason="dry_run",
            exit_code=EXIT_PASS,
            command=command,
            assertions=[
                {
                    "name": "dry_run",
                    "status": "NOT_VERIFIED",
                    "detail": "input validation only; no scenario execution",
                }
            ],
            artifacts=artifacts,
            refs={},
            usage=None,
            extensions=extensions,
        )
        if document is None:
            return exit_code
        print(f"NOT_VERIFIED (dry-run): {out_dir}")
        return EXIT_PASS

    if not has_impl:
        out_dir.mkdir(parents=True, exist_ok=True)
        extensions = _experience_extensions(identity, run_id, runtime, manifest)
        document, exit_code = _write_receipt(
            runtime,
            identity=identity,
            run_id=run_id,
            out_dir=out_dir,
            started_at=started_at,
            finished_at=_utcnow(),
            result="NOT_VERIFIED",
            reason=f"scenario_not_implemented:{args.scenario}",
            exit_code=EXIT_NOT_VERIFIED,
            command=command,
            assertions=[
                {
                    "name": "scenario_impl",
                    "status": "NOT_VERIFIED",
                    "detail": "no registered implementation (T-07 adds the P0 bodies)",
                }
            ],
            artifacts=[],
            refs={},
            usage=None,
            extensions=extensions,
        )
        if document is None:
            return exit_code
        print(
            f"NOT_VERIFIED: scenario {args.scenario!r} has no registered implementation",
            file=sys.stderr,
        )
        return EXIT_NOT_VERIFIED

    context = ScenarioContext(
        identity=identity,
        run_id=run_id,
        out_dir=out_dir,
        timeout=args.timeout,
        manifest=manifest,
    )
    ledger = runtime.CleanupLedger()
    try:
        exit_code, timeout_record = runtime.run_handler_with_timeout(
            lambda active_ledger: impls[args.scenario](context),
            args.timeout,
            ledger,
            out_dir,
            scenario=identity.scenario,
            phase=identity.phase,
            run_id=run_id,
        )
    except BaseException as exc:  # noqa: BLE001 - scenario boundary reports failures
        exit_code = EXIT_FAIL
        context.reason = f"scenario_exception:{type(exc).__name__}:{exc}"
        timeout_record = None

    if timeout_record is not None:
        cleanup_path = out_dir / "timeout-cleanup.json"
        if cleanup_path.is_file():
            context.artifacts.append(_artifact_record(cleanup_path, out_dir, runtime))

    normalized, artifact_errors = _normalize_artifacts(context, context.artifacts, runtime)
    context.artifacts = normalized
    if artifact_errors:
        context.assertions.append(
            {
                "name": "artifacts",
                "status": "FAIL",
                "detail": ",".join(artifact_errors),
            }
        )
        exit_code = EXIT_FAIL

    if exit_code == EXIT_PASS and not context.assertions:
        context.assertions.append(
            {
                "name": "assertion_count",
                "status": "FAIL",
                "detail": "zero assertions may not PASS",
            }
        )
        exit_code = EXIT_FAIL
        context.reason = context.reason or "zero_assertions"

    if exit_code == EXIT_PASS:
        result = "PASS"
    elif exit_code == EXIT_NOT_VERIFIED:
        result = "NOT_VERIFIED"
    else:
        result = "FAIL"
        exit_code = EXIT_FAIL

    extensions = _experience_extensions(identity, run_id, runtime, manifest)
    extensions.update(context.extensions)
    document, written_code = _write_receipt(
        runtime,
        identity=identity,
        run_id=run_id,
        out_dir=out_dir,
        started_at=started_at,
        finished_at=_utcnow(),
        result=result,
        reason=context.reason,
        exit_code=exit_code,
        command=command,
        assertions=context.assertions,
        artifacts=context.artifacts,
        refs=context.refs,
        usage=context.usage,
        extensions=extensions,
    )
    if document is None:
        return written_code
    print(f"{result}: {out_dir}")
    return exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class _UsageParser(argparse.ArgumentParser):
    """argparse parser whose usage failures exit 3, never 2."""

    def error(self, message: str) -> None:  # pragma: no cover - argparse path
        self.print_usage(sys.stderr)
        print(f"usage error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = _UsageParser(
        prog="acp2_experience.py",
        description=(
            "ACP 2.0 C-track experience acceptance harness. Receipts are written "
            "and validated by B's single acp2_runtime.py implementation."
        ),
        epilog=(
            "Exit codes: 0 PASS/tool check, 1 FAIL, 2 NOT_VERIFIED/not executed, "
            "3 usage error. --help/--dry-run/--verify-receipt/"
            "--check-doc-placeholders are tool checks, never a scenario PASS."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scenario", help="one of the 36 frozen experience scenario IDs")
    mode.add_argument(
        "--verify-receipt",
        type=Path,
        metavar="PATH",
        help="validate one receipt with B's validate_receipt (tool check, not PASS)",
    )
    mode.add_argument(
        "--check-doc-placeholders",
        type=Path,
        metavar="PATH",
        help="fail on angle-bracket placeholders outside fences/HTML comments",
    )
    parser.add_argument("--out", type=Path, help="evidence leaf directory")
    parser.add_argument(
        "--timeout", type=float, default=None, help="total scenario timeout in seconds"
    )
    parser.add_argument(
        "--manifest", type=Path, help="absolute C experience manifest extension path"
    )
    parser.add_argument("--phase", help="common B01 phase (main/approval/budget/...)")
    parser.add_argument(
        "--mode", choices=("fixture", "live"), help="fixture (default) or live lanes"
    )
    parser.add_argument(
        "--subcase",
        help="sub-line: cross-domain-live:{nmr,thermo,selectivity}, "
        "research-loop-live:{control,search}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate inputs, write NOT_VERIFIED, exit 0; never executes",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check_doc_placeholders is not None:
        return check_doc_placeholders(args.check_doc_placeholders)
    if args.verify_receipt is not None:
        return verify_receipt(args.verify_receipt)
    if args.scenario is None:
        parser.print_usage(sys.stderr)
        print(
            "usage error: choose one of --scenario, --verify-receipt, --check-doc-placeholders",
            file=sys.stderr,
        )
        return EXIT_USAGE
    return run_scenario(args)


if __name__ == "__main__":
    raise SystemExit(main())
