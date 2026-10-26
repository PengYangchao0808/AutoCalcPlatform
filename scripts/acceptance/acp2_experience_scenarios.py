"""C experience scenario registry and the five P0 environment bodies.

The harness imports this module from its own directory.  ``SCENARIO_IMPLS``
maps a frozen scenario ID to a callable ``handler(context) -> int``:

* ``0`` = PASS, ``1`` = FAIL, ``2`` = NOT_VERIFIED;
* the handler appends at least one assertion to ``context.assertions`` before a
  PASS is accepted (zero assertions can never PASS);
* artifacts are appended as ``{"path": ...}``; the harness recomputes
  ``size_bytes``/``sha256`` and rejects a missing file.

T-07 registers exactly the five P0 environment/fixture bodies.  Every other
frozen scenario stays unregistered here: a non-dry-run call returns
``NOT_VERIFIED`` (exit 2) and a dry run still returns 0.

The five bodies never start a model, DSH, QC, LSF or Chromium process.  They
only run local environment commands (git, python/pytest, node/pnpm) and read
already-committed contracts.  A missing prerequisite is reported as exit 2
(``NOT_VERIFIED``); a command that ran and produced a wrong result is exit 1
(``FAIL``).  The two states are never interchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

# scenario ID -> handler(context) -> exit code
SCENARIO_IMPLS: dict[str, Callable[[Any], int]] = {}

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3

SCHEMA_DIALECT = "json-schema-2020-12"
SCHEMA_DIALECT_URL = "https://json-schema.org/draft/2020-12/schema"
SECRET_PROPERTY_RE = re.compile(r"(?i)(secret|token|password|api[_-]?key)$")
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
TREE_RE = re.compile(r"^[0-9a-f]{40}$")
NODE_SUITE_RE = re.compile(r"(\d+)\s+passed,\s+(\d+)\s+failed")
PYTEST_COUNT_RE = re.compile(r"(\d+)\s+passed")

P1_TOOLS = frozenset(
    {
        "acp_capabilities",
        "acp_preflight",
        "acp_submit",
        "acp_status",
        "acp_cancel",
        "acp_prepare_artifact",
        "acp_read_artifact",
        "acp_analyze",
    }
)

# The C tree is the owner of the P0 baseline (T-01).  These two commits are the
# frozen pair whose trees must still agree.
C_BASELINE_COMMIT = "75a8f08"
B_MATERIALIZED_COMMIT = "a0185b15"


def register(scenario: str, handler: Callable[[Any], int]) -> Callable[[Any], int]:
    """Register one scenario body; rejects duplicate or empty registrations."""

    if not isinstance(scenario, str) or not scenario:
        raise ValueError("scenario must be a non-empty string")
    if not callable(handler):
        raise TypeError(f"handler for {scenario!r} is not callable")
    if scenario in SCENARIO_IMPLS:
        raise ValueError(f"scenario already registered: {scenario}")
    SCENARIO_IMPLS[scenario] = handler
    return handler


# ---------------------------------------------------------------------------
# Context and command helpers
# ---------------------------------------------------------------------------


def _out_dir(context: Any) -> Path:
    out = Path(context.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _assertion(context: Any, name: str, ok: bool, detail: str = "") -> bool:
    context.assertions.append(
        {
            "name": name,
            "status": "PASS" if ok else "FAIL",
            "detail": (detail[:2000] if detail else None),
        }
    )
    return ok


def _not_verified(context: Any, reason: str) -> int:
    context.reason = reason
    # NOT_VERIFIED is a prerequisite gap, not an executed failure: never write
    # a FAIL assertion here or the receipt would claim an execution verdict.
    context.assertions.append(
        {
            "name": f"prerequisite:{reason.split(':', 1)[0]}",
            "status": "NOT_VERIFIED",
            "detail": reason[:2000],
        }
    )
    return EXIT_NOT_VERIFIED


def _finish(context: Any, reason: str) -> int:
    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = context.reason or reason
        return EXIT_FAIL
    return EXIT_PASS


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def _run(
    argv: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: float = 60.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one local command; execution errors become a normal non-zero result."""

    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(
            argv,
            returncode=127,
            stdout="",
            stderr=f"{type(exc).__name__}: {exc}",
        )


def _write_text(out: Path, name: str, text: str) -> str:
    path = out / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return name


def _write_json(out: Path, name: str, document: Any) -> str:
    return _write_text(out, name, json.dumps(document, indent=2, sort_keys=True) + "\n")


def _add_artifact(context: Any, name: str) -> None:
    context.artifacts.append({"path": name})


OBSERVATIONS_SCHEMA = "acp2.experience.observations.v1"


def publish_observations(
    context: Any,
    observed: dict[str, str],
    *,
    model_calls: int,
    model_evidence: str,
    denial_evidence: str | None = None,
    denial_kind: str | None = None,
    offline_fixture_content: bool = False,
) -> dict[str, Any]:
    """Publish the row's real observations under ``experience.observations``.

    The present/absent *declaration* per scenario is frozen in B's validator
    (``EXPERIENCE_REQUIRED_OBSERVATIONS``); this helper only records what the
    row actually saw.  It never promotes fixture content to live: a live PASS
    whose observations are missing or disagree with the declaration is rejected
    by ``validate_receipt``.
    """

    payload: dict[str, Any] = {
        "schema": OBSERVATIONS_SCHEMA,
        "observed": dict(observed),
        "model_calls": int(model_calls),
        "model_evidence": model_evidence,
        "offline_fixture_content": bool(offline_fixture_content),
    }
    if denial_evidence is not None:
        payload["denial_evidence"] = denial_evidence
    if denial_kind is not None:
        payload["denial_kind"] = denial_kind
    context.extensions["experience.observations"] = payload
    return payload


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def _env_path(name: str) -> Path | None:
    value = _env(name)
    return Path(value) if value else None


def _python() -> str:
    return _env("ACP2_C_PYTHON") or sys.executable


def _node() -> str | None:
    return _env("ACP2_NODE_BIN") or shutil.which("node")


def _pnpm() -> str | None:
    return _env("ACP2_PNPM_BIN") or shutil.which("pnpm")


def _tool_env(*extra: str | None) -> dict[str, str]:
    """Environment with the frozen toolchain directories first on PATH."""

    env = dict(os.environ)
    prefixes = [str(Path(item).parent) for item in extra if item]
    node = _env("ACP2_NODE_BIN")
    if node:
        prefixes.insert(0, str(Path(node).parent))
    pnpm = _env("ACP2_PNPM_BIN")
    if pnpm:
        prefixes.append(str(Path(pnpm).parent))
    env["PATH"] = os.pathsep.join([item for item in prefixes if item] + [env.get("PATH", "")])
    return env


def _contracts_dir() -> Path | None:
    """Return the C-owned ``experience/`` contract directory.

    ``$ACP2_CONTRACTS`` is the acp2-contracts *root* (it also carries the
    B-owned ``runtime/`` subtree, which C never writes).  C's contracts live in
    its ``experience/`` child.  Tests may point the variable directly at the
    experience directory.
    """

    explicit = _env_path("ACP2_CONTRACTS")
    if explicit is not None:
        if (explicit / "experience").is_dir():
            return explicit / "experience"
        return explicit
    source = _env_path("ACP2_C_SOURCE")
    if source is not None:
        return source / "docs/reference/next-change/acp2-contracts/experience"
    return None


def _project_root() -> Path | None:
    explicit = _env_path("ACP2_PROJECT_ROOT")
    if explicit is not None:
        return explicit
    # Walk up from this file looking for the B runtime checkout.
    for parent in Path(__file__).resolve().parents:
        if (parent / "scripts/acceptance/acp2_runtime.py").is_file():
            return parent
    return None


# ---------------------------------------------------------------------------
# Live-evidence guard (shared by every live scenario body added later)
# ---------------------------------------------------------------------------


_FAKE_ENV_KEYS = (
    "ACP2_FAKE_TRANSPORT",
    "ACP2_FAKE_BACKEND",
    "ACP2_FAKE_MODEL",
    "ACP2_MOCK_TRANSPORT",
    "ACP2_MOCK_BACKEND",
    "ACP2_MOCK_MODEL",
)
# Match a test-double TOKEN PAIR, not loose substrings: ``fake_transport`` and
# ``transport.fake`` hit, while ``httpx._transports.mock`` (a real httpx module
# left behind in ``sys.modules`` by a full-suite run), ``unittest.mock``,
# ``acp.nmr.dp5q_stub`` and ``my.transport_module`` do not.
_FAKE_MODULE_RE = re.compile(
    r"(?:^|[._])(?:fake|mock|stub)[._](?:backend|transport|model|client)(?:[._]|$)"
    r"|(?:^|[._])(?:backend|transport|model|client)[._](?:fake|mock|stub)(?:[._]|$)",
    re.IGNORECASE,
)

# Real modules whose names coincidentally contain a token pair.  The allowlist
# wins over the regex (checked first) so a legitimate import can never fail a
# clean live run; it is deliberately tiny and name-exact.
_FAKE_MODULE_ALLOWLIST: frozenset[str] = frozenset({"httpx._transports.mock"})


def _is_fake_module(module_name: str) -> bool:
    """Return True when ``module_name`` is a test double we must reject."""

    if module_name in _FAKE_MODULE_ALLOWLIST:
        return False
    return _FAKE_MODULE_RE.search(module_name) is not None


def assert_live_environment(context: Any) -> bool:
    """Reject a live-evidence run carrying a mock transport/backend/model.

    Fixture rows may use fakes freely.  A row written with
    ``evidence_kind == "live"`` is a signed product statement and must not be
    produced by a fake transport, a ``FakeBackend`` or a fake model client.
    The check is intentionally name/environment based: the harness cannot prove
    absence, but it does fail closed on the usual test doubles.
    """

    if getattr(context.identity, "evidence_kind", None) != "live":
        return True
    problems: list[str] = []
    for key in _FAKE_ENV_KEYS:
        if os.environ.get(key):
            problems.append(f"env:{key}")
    for module_name in list(sys.modules):
        if _is_fake_module(module_name):
            problems.append(f"module:{module_name}")
    return _assertion(
        context,
        "live.no_fake_transport",
        not problems,
        ",".join(problems) or "no fake transport/backend/model detected",
    )


def selfcheck_live_environment() -> int:
    """Run ONLY the clean live environment assertion (fresh-process entry).

    A full-suite pytest run can leave legitimate modules such as
    ``httpx._transports.mock`` in ``sys.modules``.  Checking in the same
    process therefore inherits whatever an earlier test imported; this entry
    point exists so the check can run in a pristine interpreter.
    """

    context = SimpleNamespace(
        identity=SimpleNamespace(evidence_kind="live"),
        assertions=[],
        reason=None,
    )
    ok = assert_live_environment(context)
    failed = [item for item in context.assertions if item.get("status") == "FAIL"]
    detail = ",".join(str(item.get("detail") or "") for item in failed) or "clean"
    stream = sys.stdout if ok else sys.stderr
    print(f"live.no_fake_transport: {'PASS' if ok else 'FAIL'}: {detail}", file=stream)
    return EXIT_PASS if ok else EXIT_FAIL


# ---------------------------------------------------------------------------
# baseline — rerun the T-01 baseline commands
# ---------------------------------------------------------------------------


def scenario_baseline(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    if not source.is_dir():
        return _not_verified(context, f"missing_prerequisite:C_source_absent:{source}")
    python = _python()
    out = _out_dir(context)
    timeout = max(5.0, float(context.timeout))

    head = _run(["git", "-C", str(source), "rev-parse", "HEAD"], timeout=timeout)
    head_value = head.stdout.strip()
    _assertion(
        context,
        "baseline.git_head",
        head.returncode == 0 and bool(HEAD_RE.match(head_value)),
        head_value or head.stderr.strip(),
    )

    status = _run(["git", "-C", str(source), "status", "--porcelain"], timeout=timeout)
    porcelain = status.stdout.strip()
    _assertion(
        context,
        "baseline.worktree_clean",
        status.returncode == 0 and porcelain == "",
        porcelain or "clean",
    )

    probe = (
        "import json, acp, cccp, acp_services\n"
        "print(json.dumps({'acp': acp.__file__, 'cccp': cccp.__file__, "
        "'acp_services': acp_services.__file__}))\n"
    )
    imported = _run([python, "-c", probe], timeout=timeout)
    import_paths: dict[str, str] = {}
    if imported.returncode == 0:
        try:
            import_paths = json.loads(imported.stdout)
        except json.JSONDecodeError:
            import_paths = {}
    resolved_source = source.resolve()
    inside = bool(import_paths) and all(
        Path(value).resolve().is_relative_to(resolved_source)
        for value in import_paths.values()
        if isinstance(value, str)
    )
    _assertion(
        context,
        "baseline.product_import_source",
        inside,
        json.dumps(import_paths, sort_keys=True) if import_paths else imported.stderr.strip(),
    )

    c_tree = _run(
        ["git", "-C", str(source), "rev-parse", f"{C_BASELINE_COMMIT}^{{tree}}"], timeout=timeout
    )
    project = _project_root()
    b_tree = (
        _run(
            ["git", "-C", str(project), "rev-parse", f"{B_MATERIALIZED_COMMIT}^{{tree}}"],
            timeout=timeout,
        )
        if project is not None
        else subprocess.CompletedProcess([], 127, "", "ACP2_PROJECT_ROOT unavailable")
    )
    c_tree_value = c_tree.stdout.strip()
    b_tree_value = b_tree.stdout.strip()
    _assertion(
        context,
        "baseline.tree_matches_b_materialized",
        c_tree.returncode == 0
        and b_tree.returncode == 0
        and bool(TREE_RE.match(c_tree_value))
        and bool(TREE_RE.match(b_tree_value))
        and c_tree_value == b_tree_value,
        f"C={c_tree_value or c_tree.stderr.strip()} B={b_tree_value or b_tree.stderr.strip()}",
    )

    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")
    suite = _run(
        [node, "frontend/js/module_tests/run_all.js"],
        cwd=source,
        timeout=timeout,
        env=_tool_env(node),
    )
    match = NODE_SUITE_RE.search(suite.stdout)
    passed = int(match.group(1)) if match else 0
    failed = int(match.group(2)) if match else -1
    _assertion(
        context,
        "baseline.node_zero_dependency_suite",
        suite.returncode == 0 and failed == 0 and passed > 0,
        f"rc={suite.returncode} passed={passed} failed={failed}",
    )

    tail = "\n".join(suite.stdout.strip().splitlines()[-5:])
    _write_text(out, "node-suite.txt", tail + "\n")
    _add_artifact(context, "node-suite.txt")
    report = {
        "source": str(source),
        "head": head_value,
        "status_porcelain": porcelain,
        "imports": import_paths,
        "c_tree": c_tree_value,
        "b_tree": b_tree_value,
        "node_suite": {"returncode": suite.returncode, "passed": passed, "failed": failed},
    }
    _write_json(out, "baseline.json", report)
    _add_artifact(context, "baseline.json")
    return _finish(context, "baseline_assertion_failed")


# ---------------------------------------------------------------------------
# contracts — parse/ref/dialect/required-set and T-02/T-03 negative fixtures
# ---------------------------------------------------------------------------


_ENGINE: ModuleType | None = None


def _contract_engine() -> ModuleType | None:
    """Import B's single runtime module to reuse its schema engine."""

    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    candidates: list[Path] = []
    explicit = _env_path("ACP2_RUNTIME_PATH")
    if explicit is not None:
        candidates.append(explicit)
    project = _project_root()
    if project is not None:
        candidates.append(project / "scripts/acceptance/acp2_runtime.py")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("acp2_runtime_contract_engine", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError):
            continue
        if getattr(module, "ACCEPTANCE_SCHEMA", None) != "acp2.acceptance.v1":
            continue
        _ENGINE = module
        return module
    return None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _walk_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_refs(item)


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return document
    if not pointer.startswith("/"):
        raise KeyError(pointer)
    current = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(token)
    return current


def _resolve_ref(doc_path: Path, ref: str) -> None:
    file_part, _, pointer = ref.partition("#")
    if file_part:
        target = (doc_path.parent / file_part).resolve()
        if not target.is_file():
            raise FileNotFoundError(f"{ref} -> {target}")
        document = _load_json(target)
    else:
        document = _load_json(doc_path)
    _resolve_pointer(document, pointer)


def _secret_property_violations(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                for prop_name in value:
                    if isinstance(prop_name, str) and SECRET_PROPERTY_RE.search(prop_name):
                        found.append(f"{path}/properties/{prop_name}")
            found.extend(_secret_property_violations(value, f"{path}/{key}"))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_secret_property_violations(item, f"{path}/{index}"))
    return found


def _validate(
    engine: ModuleType, schema: dict[str, Any], schema_path: Path, document: Any
) -> list[str]:
    try:
        return list(engine._validate_schema(document, schema, "$", schema, schema_path.parent))
    except Exception as exc:  # noqa: BLE001 - engine boundary, reported as a failure
        return [f"engine_error:{type(exc).__name__}:{exc}"]


def _allowed_tools() -> set[str] | None:
    project = _project_root()
    if project is None:
        return None
    path = (
        project / "docs/reference/next-change/acp2-contracts/runtime/p2-research-operations.v1.json"
    )
    if not path.is_file():
        return None
    document = _load_json(path)
    names = set(P1_TOOLS)
    for row in document.get("rows", []):
        for tool in row.get("tools", []):
            name = tool.get("name")
            if isinstance(name, str):
                names.add(name)
    return names


def _copy(document: Any) -> Any:
    return json.loads(json.dumps(document))


def scenario_contracts(context: Any) -> int:
    contracts = _contracts_dir()
    if contracts is None or not contracts.is_dir():
        return _not_verified(context, f"missing_prerequisite:contracts_absent:{contracts}")
    engine = _contract_engine()
    if engine is None:
        return _not_verified(context, "missing_prerequisite:b_contract_engine")
    allowed_tools = _allowed_tools()
    if allowed_tools is None:
        return _not_verified(context, "missing_prerequisite:b_p2_research_operations")

    out = _out_dir(context)
    documents = sorted(path for path in contracts.rglob("*.json") if path.is_file())
    parsed: dict[Path, Any] = {}
    parse_errors: list[str] = []
    for path in documents:
        try:
            parsed[path] = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            parse_errors.append(f"{path.relative_to(contracts)}:{exc}")
    _assertion(
        context,
        "contracts.json_parse",
        not parse_errors and bool(documents),
        f"{len(documents)} document(s), errors={parse_errors}",
    )

    refs_checked = 0
    ref_errors: list[str] = []
    for path, document in parsed.items():
        for ref in _walk_refs(document):
            refs_checked += 1
            try:
                _resolve_ref(path, ref)
            except (
                KeyError,
                IndexError,
                ValueError,
                FileNotFoundError,
                OSError,
                json.JSONDecodeError,
            ) as exc:
                ref_errors.append(f"{path.relative_to(contracts)}:{ref}:{exc}")
    _assertion(
        context,
        "contracts.refs_resolve",
        not ref_errors and refs_checked > 0,
        f"{refs_checked} ref(s), errors={ref_errors}",
    )

    schema_documents: dict[str, dict[str, Any]] = {}
    dialect_errors: list[str] = []
    schema_parse_errors: list[str] = []
    secret_violations: list[str] = []
    for path in sorted(contracts.glob("*.schema.json")):
        try:
            schema = _load_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            schema_parse_errors.append(f"{path.name}:{exc}")
            continue
        schema_documents[path.name] = schema
        if schema.get("schema_dialect") != SCHEMA_DIALECT:
            dialect_errors.append(f"{path.name}:schema_dialect={schema.get('schema_dialect')!r}")
        if schema.get("$schema") != SCHEMA_DIALECT_URL:
            dialect_errors.append(f"{path.name}:$schema={schema.get('$schema')!r}")
        secret_violations.extend(
            f"{path.name}:{item}" for item in _secret_property_violations(schema)
        )
    _assertion(
        context,
        "contracts.schema_dialect",
        not schema_parse_errors and not dialect_errors and bool(schema_documents),
        f"{len(schema_documents)} schema(s), errors={schema_parse_errors + dialect_errors}",
    )
    _assertion(
        context,
        "contracts.no_secret_property_names",
        not secret_violations,
        f"violations={secret_violations}",
    )

    required_sets = {
        "manifest-extension.schema.json": {
            "candidate_id",
            "candidate",
            "evidence_root",
            "registry_ref",
            "contracts",
            "paths",
            "test_controller",
            "mcp_launch",
            "dsh",
            "browser",
            "model",
            "credential_sources",
            "fixtures",
            "budget",
            "extensions",
        },
        "proposal.schema.json": {
            "proposal_id",
            "project_id",
            "recipe_digest",
            "plan_definition_digest",
            "budget",
            "approval_ref",
            "round",
            "decision",
        },
        "research-record.schema.json": {
            "research_id",
            "project_id",
            "round",
            "decision",
            "plan_definition_digest",
            "step_identity_refs",
            "task_refs",
            "evidence_refs",
        },
        "view-model.schema.json": {
            "snapshot_revision",
            "project_id",
            "task_ref",
            "manifest_generation",
            "artifacts",
            "claims",
            "clarifications",
            "provenance",
            "read_only",
            "not_authoritative_for_science",
        },
    }
    required_errors: list[str] = []
    for name, expected in required_sets.items():
        schema = schema_documents.get(name)
        if not isinstance(schema, dict):
            required_errors.append(f"{name}:missing")
            continue
        declared = set(schema.get("required") or [])
        missing = sorted(expected - declared)
        if missing:
            required_errors.append(f"{name}:missing_required={missing}")
    _assertion(
        context,
        "contracts.t02_t03_required_fields",
        not required_errors,
        f"errors={required_errors}",
    )

    negative: dict[str, Any] = {}
    positive: dict[str, Any] = {}

    def _fixture_failed(errors: list[str]) -> bool:
        # A missing/broken fixture is a harness failure, never a rejection.
        return any(item.startswith("fixture_error:") for item in errors)

    def reject(name: str, errors: list[str]) -> None:
        rejected = bool(errors) and not _fixture_failed(errors)
        negative[name] = {"rejected": rejected, "errors": errors[:8]}
        _assertion(context, f"contracts.negative.{name}", rejected, f"errors={errors[:8]}")

    def accept(name: str, errors: list[str]) -> None:
        accepted = not errors
        positive[name] = {"accepted": accepted, "errors": errors[:8]}
        _assertion(context, f"contracts.positive.{name}", accepted, f"errors={errors[:8]}")

    manifest_schema = schema_documents.get("manifest-extension.schema.json", {})
    proposal_schema = schema_documents.get("proposal.schema.json", {})
    record_schema = schema_documents.get("research-record.schema.json", {})
    view_schema = schema_documents.get("view-model.schema.json", {})
    manifest_path = contracts / "manifest-extension.schema.json"
    proposal_path = contracts / "proposal.schema.json"
    record_path = contracts / "research-record.schema.json"
    view_path = contracts / "view-model.schema.json"

    # 1. missing candidate_id (T-02 required field)
    manifest_required = set(manifest_schema.get("required") or [])
    probe_errors = (
        _validate(engine, manifest_schema, manifest_path, {})
        if manifest_schema
        else ["schema_missing"]
    )
    candidate_rejections = [
        item for item in probe_errors if "candidate_id" in item or item == "schema_missing"
    ]
    if "candidate_id" not in manifest_required:
        candidate_rejections.append("required_set_missing_candidate_id")
    reject(
        "missing_candidate_id",
        candidate_rejections or ["accepted_missing_candidate_id"],
    )

    # 2. api_key property name (T-02 secret rule)
    api_key_errors = [item for item in _secret_property_violations({"properties": {"api_key": {}}})]
    reject("api_key_property", api_key_errors)

    # 3. unknown availability (view-model enum)
    try:
        wrong_view = _copy(_load_json(contracts / "fixtures/wrong-project.valid.json"))[
            "view_model"
        ]
        wrong_view["artifacts"][0]["availability"] = "unknown"
        availability_errors = _validate(engine, view_schema, view_path, wrong_view)
    except (OSError, json.JSONDecodeError, KeyError, IndexError) as exc:
        availability_errors = [f"fixture_error:{exc}"]
    reject("unknown_availability", availability_errors)

    # 4. round string (proposal integer)
    try:
        proposal_round = _copy(_load_json(contracts / "fixtures/proposal.valid.json"))
        proposal_round["round"] = "1"
        round_errors = _validate(engine, proposal_schema, proposal_path, proposal_round)
    except (OSError, json.JSONDecodeError) as exc:
        round_errors = [f"fixture_error:{exc}"]
    reject("round_string", round_errors)

    # 5. bare task_refs (research-record object list)
    try:
        record_bare = _copy(_load_json(contracts / "fixtures/research-record.valid.json"))
        record_bare["task_refs"] = ["20261022_001_PESsearch"]
        bare_errors = _validate(engine, record_schema, record_path, record_bare)
    except (OSError, json.JSONDecodeError) as exc:
        bare_errors = [f"fixture_error:{exc}"]
    reject("bare_task_refs", bare_errors)

    # 6. wrong project (view-model vs task_refs, plus schema)
    try:
        wrong_project = _load_json(contracts / "fixtures/wrong-project.json")
        view_model = wrong_project["view_model"]
        expected_project = view_model.get("project_id")
        cross_errors = [
            f"task_refs[{index}].project_id={ref.get('project_id')!r} != {expected_project!r}"
            for index, ref in enumerate(wrong_project.get("task_refs", []))
            if ref.get("project_id") != expected_project
        ]
    except (OSError, json.JSONDecodeError, KeyError, AttributeError) as exc:
        cross_errors = [f"fixture_error:{exc}"]
    reject("wrong_project", cross_errors)

    # 7. unknown tool (P2 research allowlist + frozen P1 eight)
    try:
        unknown_tool = _load_json(contracts / "fixtures/unknown-tool.json")
        tool_errors = [] if unknown_tool.get("tool") in allowed_tools else ["tool_not_in_allowlist"]
    except (OSError, json.JSONDecodeError) as exc:
        tool_errors = [f"fixture_error:{exc}"]
    reject("unknown_tool", tool_errors)

    # 8. illegal approver (payload identity on approval decide)
    try:
        illegal = _load_json(contracts / "fixtures/illegal-request.json")
        arguments = illegal.get("arguments") or {}
        approver_errors = [
            f"payload_identity:{key}" for key in ("approver", "role", "tenant") if key in arguments
        ]
    except (OSError, json.JSONDecodeError) as exc:
        approver_errors = [f"fixture_error:{exc}"]
    reject("illegal_approver", approver_errors)

    # 9. stale digest (record evaluated an older plan definition)
    try:
        stale = _load_json(contracts / "fixtures/stale-reference.json")
        proposal_digest = (stale.get("proposal") or {}).get("plan_definition_digest")
        record_digest = (stale.get("record") or {}).get("plan_definition_digest")
        stale_errors = (
            []
            if proposal_digest and proposal_digest == record_digest
            else [f"plan_definition_digest_mismatch:{proposal_digest} != {record_digest}"]
        )
    except (OSError, json.JSONDecodeError) as exc:
        stale_errors = [f"fixture_error:{exc}"]
    reject("stale_digest", stale_errors)

    # Positive counterparts: every valid fixture must still be accepted.
    for name, schema_name, schema_path, fixture in (
        ("proposal", "proposal.schema.json", proposal_path, "fixtures/proposal.valid.json"),
        (
            "research_record",
            "research-record.schema.json",
            record_path,
            "fixtures/research-record.valid.json",
        ),
    ):
        try:
            document = _load_json(contracts / fixture)
            errors = _validate(engine, schema_documents[schema_name], schema_path, document)
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            errors = [f"fixture_error:{exc}"]
        accept(name, errors)

    try:
        view_document = _load_json(contracts / "fixtures/wrong-project.valid.json")["view_model"]
        view_errors = _validate(engine, view_schema, view_path, view_document)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        view_errors = [f"fixture_error:{exc}"]
    accept("view_model", view_errors)

    try:
        valid_tool = _load_json(contracts / "fixtures/unknown-tool.valid.json")
        tool_ok = [] if valid_tool.get("tool") in allowed_tools else ["tool_not_in_allowlist"]
    except (OSError, json.JSONDecodeError) as exc:
        tool_ok = [f"fixture_error:{exc}"]
    accept("unknown_tool_positive", tool_ok)

    try:
        valid_request = _load_json(contracts / "fixtures/illegal-request.valid.json")
        request_args = valid_request.get("arguments") or {}
        request_ok = [
            f"payload_identity:{key}"
            for key in ("approver", "role", "tenant")
            if key in request_args
        ]
    except (OSError, json.JSONDecodeError) as exc:
        request_ok = [f"fixture_error:{exc}"]
    accept("illegal_request_positive", request_ok)

    try:
        stale_ok_doc = _load_json(contracts / "fixtures/stale-reference.valid.json")
        stale_ok_proposal = (stale_ok_doc.get("proposal") or {}).get("plan_definition_digest")
        stale_ok_record = (stale_ok_doc.get("record") or {}).get("plan_definition_digest")
        stale_ok = (
            []
            if stale_ok_proposal and stale_ok_proposal == stale_ok_record
            else ["plan_definition_digest_mismatch"]
        )
    except (OSError, json.JSONDecodeError) as exc:
        stale_ok = [f"fixture_error:{exc}"]
    accept("stale_reference_positive", stale_ok)

    report = {
        "contracts_dir": str(contracts),
        "documents": [str(path.relative_to(contracts)) for path in documents],
        "schemas": sorted(schema_documents),
        "refs_checked": refs_checked,
        "ref_errors": ref_errors,
        "parse_errors": parse_errors,
        "dialect_errors": dialect_errors,
        "secret_property_violations": secret_violations,
        "required_errors": required_errors,
        "negative": negative,
        "positive": positive,
    }
    _write_json(out, "contracts-report.json", report)
    _add_artifact(context, "contracts-report.json")
    return _finish(context, "contracts_assertion_failed")


# ---------------------------------------------------------------------------
# workspace — pnpm install/build/test/check + boundary gate
# ---------------------------------------------------------------------------


def scenario_workspace(context: Any) -> int:
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    workspace = source / "rph"
    if not (workspace / "package.json").is_file():
        return _not_verified(context, f"missing_prerequisite:rph_workspace_absent:{workspace}")
    pnpm = _pnpm()
    if pnpm is None:
        return _not_verified(context, "missing_prerequisite:pnpm")
    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")

    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node, pnpm)
    commands = [
        ("install", [pnpm, "--dir", str(workspace), "install", "--frozen-lockfile"]),
        ("build", [pnpm, "--dir", str(workspace), "build"]),
        ("test", [pnpm, "--dir", str(workspace), "test"]),
        ("check", [pnpm, "--dir", str(workspace), "check"]),
        (
            "boundaries",
            [node, str(workspace / "scripts/check-boundaries.mjs")],
        ),
    ]
    log: list[str] = []
    for name, argv in commands:
        result = _run(argv, cwd=source, timeout=timeout, env=env)
        log.append(f"$ {' '.join(argv)}\nrc={result.returncode}\n{result.stdout}{result.stderr}")
        _assertion(
            context,
            f"workspace.{name}",
            result.returncode == 0,
            f"rc={result.returncode} {(result.stderr or result.stdout).strip()[-400:]}",
        )

    root_package = source / "package.json"
    _assertion(
        context,
        "workspace.no_root_package_json",
        not root_package.exists(),
        str(root_package) if root_package.exists() else "absent",
    )

    _write_text(out, "workspace.log", _truncate("\n\n".join(log), 12000))
    _add_artifact(context, "workspace.log")
    return _finish(context, "workspace_assertion_failed")


# ---------------------------------------------------------------------------
# harness-selftest — run the T-07 pytest suite once, without recursion
# ---------------------------------------------------------------------------


def scenario_harness_selftest(context: Any) -> int:
    if os.environ.get("ACP2_EXPERIENCE_SELFTEST") == "1":
        return _not_verified(context, "prerequisite:selftest_recursion_guard")
    source = _env_path("ACP2_C_SOURCE")
    if source is None:
        return _not_verified(context, "missing_prerequisite:ACP2_C_SOURCE")
    test_file = source / "tests/test_acp2_experience_harness.py"
    if not test_file.is_file():
        return _not_verified(context, f"missing_prerequisite:selftest_absent:{test_file}")
    python = _python()
    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))

    env = dict(os.environ)
    env["ACP2_EXPERIENCE_SELFTEST"] = "1"
    result = _run(
        [python, "-m", "pytest", str(test_file), "-q"],
        cwd=source,
        timeout=timeout,
        env=env,
    )
    combined = result.stdout + result.stderr
    failed_match = re.search(r"(\d+)\s+failed", combined)
    failed = int(failed_match.group(1)) if failed_match else 0
    passed_match = PYTEST_COUNT_RE.search(combined)
    passed = int(passed_match.group(1)) if passed_match else 0
    _assertion(
        context,
        "harness_selftest.pytest",
        result.returncode == 0 and failed == 0 and passed >= 16,
        f"rc={result.returncode} passed={passed} failed={failed}",
    )

    _write_text(out, "pytest.log", _truncate(combined, 12000))
    _add_artifact(context, "pytest.log")
    return _finish(context, "harness_selftest_assertion_failed")


# ---------------------------------------------------------------------------
# node-dsh-install — re-check the frozen Node/pnpm/DSH toolchain
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scenario_node_dsh_install(context: Any) -> int:
    contracts = _contracts_dir()
    if contracts is None or not (contracts / "dsh-compatibility.json").is_file():
        return _not_verified(context, "missing_prerequisite:dsh_compatibility_absent")
    compatibility = _load_json(contracts / "dsh-compatibility.json")

    node = _node()
    if node is None:
        return _not_verified(context, "missing_prerequisite:node")
    pnpm = _pnpm()
    if pnpm is None:
        return _not_verified(context, "missing_prerequisite:pnpm")
    dsh_source = _env_path("ACP2_DSH_SOURCE")
    if dsh_source is None or not dsh_source.is_dir():
        return _not_verified(context, f"missing_prerequisite:dsh_source_absent:{dsh_source}")

    expected_node = _env("ACP2_NODE_VERSION") or compatibility.get("node", {}).get("version")
    expected_pnpm = _env("ACP2_PNPM_VERSION") or compatibility.get("pnpm", {}).get("version")
    expected_rev = _env("ACP2_DSH_REV") or compatibility.get("dsh", {}).get("revision")

    out = _out_dir(context)
    timeout = max(10.0, float(context.timeout))
    env = _tool_env(node, pnpm)
    observed: dict[str, Any] = {}

    node_version = _run([node, "--version"], timeout=timeout, env=env)
    observed["node"] = node_version.stdout.strip()
    _assertion(
        context,
        "node_dsh.node_version",
        node_version.returncode == 0 and observed["node"] == f"v{expected_node}",
        f"{observed['node']} expected=v{expected_node}",
    )

    pnpm_version = _run([pnpm, "--version"], timeout=timeout, env=env)
    observed["pnpm"] = pnpm_version.stdout.strip()
    _assertion(
        context,
        "node_dsh.pnpm_version",
        pnpm_version.returncode == 0 and observed["pnpm"] == expected_pnpm,
        f"{observed['pnpm']} expected={expected_pnpm}",
    )

    revision = _run(["git", "-C", str(dsh_source), "rev-parse", "HEAD"], timeout=timeout, env=env)
    observed["dsh_revision"] = revision.stdout.strip()
    _assertion(
        context,
        "node_dsh.dsh_revision",
        revision.returncode == 0 and observed["dsh_revision"] == expected_rev,
        f"{observed['dsh_revision']} expected={expected_rev}",
    )

    dsh_version = _run([pnpm, "dsh", "--version"], cwd=dsh_source, timeout=timeout, env=env)
    observed["dsh_version_rc"] = dsh_version.returncode
    observed["dsh_version_stdout"] = dsh_version.stdout.strip()
    _assertion(
        context,
        "node_dsh.dsh_version_probe",
        dsh_version.returncode == 0,
        f"rc={dsh_version.returncode} {dsh_version.stdout.strip()[-200:]}",
    )

    archive_path = Path(compatibility["node"]["archive_path"])
    expected_archive = compatibility["node"]["archive_sha256"]
    if not archive_path.is_file():
        return _not_verified(context, f"missing_prerequisite:node_archive_absent:{archive_path}")
    actual_archive = _sha256_file(archive_path)
    observed["archive_sha256"] = actual_archive
    _assertion(
        context,
        "node_dsh.archive_sha256",
        actual_archive == expected_archive and actual_archive.startswith("c0649af"),
        f"{actual_archive} expected={expected_archive}",
    )

    _write_json(out, "node-dsh-install.json", observed)
    _add_artifact(context, "node-dsh-install.json")
    return _finish(context, "node_dsh_assertion_failed")


# ---------------------------------------------------------------------------
# Registration (T-07 registers exactly the five P0 environment/fixture rows)
# ---------------------------------------------------------------------------

P0_SCENARIOS: tuple[str, ...] = (
    "baseline",
    "contracts",
    "workspace",
    "harness-selftest",
    "node-dsh-install",
)

register("baseline", scenario_baseline)
register("contracts", scenario_contracts)
register("workspace", scenario_workspace)
register("harness-selftest", scenario_harness_selftest)
register("node-dsh-install", scenario_node_dsh_install)


# ---------------------------------------------------------------------------
# Sibling scenario-module discovery (T-08 contract)
# ---------------------------------------------------------------------------
# Every later todo adds ONE ``acp2_experience_scenario_<name>.py`` file in this
# directory and never edits this registry.  A sibling registers its body through
# the shared ``register`` function (duplicates raise) or through an import-time
# call of that same helper.  Discovery is sorted so the registry is
# deterministic across runs and platforms.
#
# A sibling module MUST be import-safe without optional third-party packages:
# import Playwright/DSH-only libraries inside the handler, so the core five P0
# bodies keep working in the C interpreter.
#
# ``SCENARIO_IMPLS`` stays the only map the harness reads.  ``DISCOVERY_MODULES``
# is the diagnostic record of which sibling modules were imported.

DISCOVERY_PATTERN = "acp2_experience_scenario_*.py"
DISCOVERY_MODULES: tuple[str, ...] = ()


def _discover_sibling_scenarios() -> tuple[str, ...]:
    global DISCOVERY_MODULES

    script_dir = Path(__file__).resolve().parent
    # The harness (and the self-tests) load this file under a synthetic module
    # name.  Sibling modules import the shared helpers by the canonical name,
    # so publish the currently-executing registry under that name as well.
    sys.modules["acp2_experience_scenarios"] = sys.modules[__name__]
    script_dir_text = str(script_dir)
    if script_dir_text not in sys.path:
        sys.path.insert(0, script_dir_text)

    discovered: list[str] = []
    for path in sorted(script_dir.glob(DISCOVERY_PATTERN)):
        if path.name == Path(__file__).name or not path.is_file():
            continue
        module_name = path.stem
        before = set(SCENARIO_IMPLS)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load scenario module {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        added = set(SCENARIO_IMPLS) - before
        if not added:
            raise ValueError(f"scenario module {path.name} registered no scenarios")
        discovered.append(module_name)
    DISCOVERY_MODULES = tuple(discovered)
    return DISCOVERY_MODULES


# Sibling discovery is import-time behaviour for the harness (which loads this
# file under a synthetic module name).  A direct ``--selfcheck-live-env`` run
# must stay import-light, so it is skipped only for that entry point.
if __name__ != "__main__":
    _discover_sibling_scenarios()


if __name__ == "__main__":
    if sys.argv[1:] == ["--selfcheck-live-env"]:
        raise SystemExit(selfcheck_live_environment())
    raise SystemExit(EXIT_USAGE)
