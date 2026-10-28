#!/usr/bin/env python3.11
"""M0-11: mechanical inventory of every v1 request-field consumer.

The M0-06 field policy (``acp_services.entrypoints.application_params``) freezes
``extra="forbid"`` for the v1 submit/preflight DTOs and the registered contract
``runtime/application-api.v1.json`` at ``additional_properties=false``.  That is
a hard cut for *clients*: a field a shipped client sends but the policy does not
declare is now a 422 ``input_invalid`` / ``params.rule=unknown_field``.  The
legacy allowlist is empty, so the blast radius must be *measured*, not
enumerated by hand (EXECUTION_PLAN M0-11: "不能只靠人工列举").

This script is that measurement.  It walks the registered consumer surfaces and
extracts the request keys each consumer actually puts on the wire:

``frontend/js/**/*.js``
    every ``api("/jobs", {method: "POST", body: JSON.stringify(X)})`` call site;
    ``X`` resolves to its object literal (inline, a local variable, or the
    returned literal of the builder function it calls), plus the top-level
    ``X.key = …`` mutations in the same function and the one-hop helpers that
    mutate a passed body (``applyNodeSelectionToBody``).  Body builders named
    ``build*Body`` are also reported, because a builder can be injected into a
    submit path without appearing next to a ``JSON.stringify``.
``rph/packages/**/src/**/*.ts``
    every ``put("key", …)`` inside ``buildToolArguments``, grouped by the
    ``case "<operation>":`` run it belongs to.
``src/acp/cli.py``
    every request object handed to ``canonical_submit_request`` (the CLI's one
    canonical request) or to a ``POST /api/v1/jobs`` helper.
``scripts/acceptance/**/*.py``
    the same call-site trace (acceptance fixtures POST real bodies).
``docs/**/*.json``
    contract samples: a top-level ``payload`` next to an ``operation``.

Excluded on purpose: ``frontend/js/module_tests`` (test doubles and browser
harnesses are not shipped consumers; their assertions live in the frontend
suite) and ``rph/**/dist`` (build output).  Both exclusions are recorded in the
report so nothing is hidden.

Comparison authorities (both, never one):

1. the live policy table (``SUBMIT_/PREFLIGHT_/PREPARE_FIELD_POLICY``);
2. the registered contract operation field list (``operations.<kind>.request``).

Output is a deterministic JSON report (``consumer/path/keys/extra_keys/
disposition``), sorted, with no clock values, so it can be snapshotted.

Dispositions
------------
``compliant``
    Every key is declared by the policy table *and* registered in the contract.
``breaking``
    A key is not in the policy table, or a ``server_path_rejected`` field is
    sent at an entry that refuses it: the server rejects the request **today**.
``contract_divergence``
    The policy accepts every key but the registered contract does not declare
    one of them: the request succeeds and the contract is behind the code.
``expected_rejection``
    A negative contract sample that *asserts* the rejection (documentation of
    the rule, not a client).
``other_request_kind``
    A non-submit/prepare vocabulary (status/read/analyze tool arguments): the
    v1 field policy does not govern it and this report does not judge it.

Exit codes
----------
``0``  scan completed and (with ``--check``) no ``breaking`` consumer exists.
``1``  ``--check`` was requested and at least one ``breaking`` consumer exists.
``2``  the scan could not run (missing authority, unparsable source, bad flag).

Usage
-----
    python3.11 scripts/acceptance/check_v1_request_field_consumers.py
    python3.11 scripts/acceptance/check_v1_request_field_consumers.py --check
    python3.11 scripts/acceptance/check_v1_request_field_consumers.py --out /tmp/c.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEMA = "acp2.identity.v1.request-field-consumers"
SCANNER_VERSION = "1"

EXIT_OK = 0
EXIT_BREAKING = 1
EXIT_ERROR = 2

#: Registered consumer surfaces (relative to the repository root).
FRONTEND_ROOT = "frontend/js"
RPH_ROOT = "rph/packages"
CLI_PATH = "src/acp/cli.py"
ACCEPTANCE_ROOT = "scripts/acceptance"
DOCS_ROOT = "docs"

#: Directories inside a scanned root that are not shipped consumers.
EXCLUDED_FRONTEND_DIRS: tuple[str, ...] = ("module_tests",)
EXCLUDED_RPH_DIRS: tuple[str, ...] = ("dist", "node_modules")

CONTRACT_PATH = "docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json"
POLICY_AUTHORITY = "src/acp_services/entrypoints/application_params.py"

DISPOSITION_COMPLIANT = "compliant"
DISPOSITION_BREAKING = "breaking"
DISPOSITION_CONTRACT_DIVERGENCE = "contract_divergence"
DISPOSITION_EXPECTED_REJECTION = "expected_rejection"
DISPOSITION_OTHER_KIND = "other_request_kind"
DISPOSITIONS: frozenset[str] = frozenset(
    {
        DISPOSITION_COMPLIANT,
        DISPOSITION_BREAKING,
        DISPOSITION_CONTRACT_DIVERGENCE,
        DISPOSITION_EXPECTED_REJECTION,
        DISPOSITION_OTHER_KIND,
    }
)

KIND_FRONTEND = "frontend-js"
KIND_RPH = "rph-typescript"
KIND_CLI = "acp-cli"
KIND_ACCEPTANCE = "acceptance-python"
KIND_DOCS = "docs-json"

_SERVER_PATH_DISPOSITION = "server_path_rejected"

#: The v1 job-create endpoint every Python request builder is traced through.
_JOB_CREATE_PATH = "/api/v1/jobs"
#: Callee names that consume the canonical v1 submit request.
_SUBMIT_NORMALISERS = frozenset({"canonical_submit_request"})
#: Keyword names that carry a request body into an HTTP helper.
_BODY_KEYWORDS = ("body", "json", "payload", "data")


@dataclass(frozen=True)
class FieldUse:
    """One request body a consumer actually builds."""

    consumer: str
    kind: str
    path: str
    line: int
    request_kind: str
    entry: str
    keys: tuple[str, ...]
    note: str = ""
    expected_rejection: bool = False


@dataclass(frozen=True)
class Authorities:
    """The two frozen comparison authorities plus the policy vocabulary."""

    policies: Mapping[str, Mapping[str, Any]]
    contract_fields: Mapping[str, frozenset[str]]
    legacy_allowlist: tuple[str, ...]


# ---------------------------------------------------------------------------
# Authorities
# ---------------------------------------------------------------------------


def load_authorities(repo_root: Path = REPO_ROOT) -> Authorities:
    """Load the live field policy and the registered contract operation table.

    Raises:
        RuntimeError: the policy module or the contract cannot be read (the scan
            is meaningless without both).
    """
    src = str(repo_root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from acp_services.entrypoints.application_params import (  # noqa: PLC0415
            FIELD_POLICY,
            LEGACY_REQUEST_FIELD_ALLOWLIST,
        )
    except ImportError as exc:  # pragma: no cover - environment failure
        raise RuntimeError(f"cannot import the field policy from {src}: {exc}") from exc

    contract_file = repo_root / CONTRACT_PATH
    try:
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
        operations = contract["operations"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(f"cannot read the registered contract {CONTRACT_PATH}: {exc}") from exc

    contract_fields: dict[str, frozenset[str]] = {}
    for kind, operation in operations.items():
        request = operation.get("request") if isinstance(operation, Mapping) else None
        if not isinstance(request, Mapping):
            continue
        declared = request.get("fields")
        if isinstance(declared, Mapping):
            contract_fields[str(kind)] = frozenset(str(name) for name in declared)
        elif isinstance(declared, Sequence) and not isinstance(declared, (str, bytes)):
            contract_fields[str(kind)] = frozenset(str(name) for name in declared)

    return Authorities(
        policies=FIELD_POLICY,
        contract_fields=contract_fields,
        legacy_allowlist=tuple(sorted(LEGACY_REQUEST_FIELD_ALLOWLIST)),
    )


# ---------------------------------------------------------------------------
# String/brace helpers (JS/TS have no stdlib parser here)
# ---------------------------------------------------------------------------


def _skip_string(text: str, start: int) -> int:
    """Return the index just past the string literal that starts at *start*."""
    quote = text[start]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return len(text)


def _match_brace(text: str, start: int) -> int:
    """Return the index of the ``}`` closing the ``{`` at *start* (or -1)."""
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char in "\"'`":
            index = _skip_string(text, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _top_level_keys(body: str) -> tuple[str, ...]:
    """Return the depth-0 ``key:`` names of one object-literal body."""
    keys: list[str] = []
    depth = 0
    index = 0
    while index < len(body):
        char = body[index]
        if char in "\"'`":
            index = _skip_string(body, index)
            continue
        if char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif depth == 0:
            match = re.match(r"([A-Za-z_$][\w$]*)\s*:", body[index:])
            if match and (index == 0 or body[index - 1] in "{,\n \t\r"):
                keys.append(match.group(1))
                index += match.end()
                continue
        index += 1
    return tuple(keys)


def _function_body(text: str, name: str) -> tuple[int, int] | None:
    """Return ``(open_brace, close_brace)`` of ``function name(...) { … }``."""
    match = re.search(rf"(?:async\s+)?function\s+{re.escape(name)}\s*\(", text)
    if match is None:
        return None
    open_brace = text.find("{", match.end())
    if open_brace < 0:
        return None
    close_brace = _match_brace(text, open_brace)
    if close_brace < 0:
        return None
    return open_brace, close_brace


def _declared_literal(text: str, name: str, before: int) -> tuple[int, tuple[str, ...]] | None:
    """Return the last ``<name> = { … }`` literal declared before *before*."""
    pattern = re.compile(rf"(?:var|let|const)?\s*\b{re.escape(name)}\s*=\s*\{{")
    found: tuple[int, tuple[str, ...]] | None = None
    for match in pattern.finditer(text, 0, before):
        open_brace = match.end() - 1
        close_brace = _match_brace(text, open_brace)
        if close_brace < 0:
            continue
        found = (match.start(), _top_level_keys(text[open_brace + 1 : close_brace]))
    return found


def _mutated_keys(text: str, start: int, end: int, variable: str) -> set[str]:
    """Return top-level ``<variable>.<key> =`` keys assigned inside a span."""
    keys: set[str] = set()
    for match in re.finditer(
        rf"\b{re.escape(variable)}\.([A-Za-z_$][\w$]*)\s*=[^=]", text[start:end]
    ):
        keys.add(match.group(1))
    for match in re.finditer(
        rf"\b{re.escape(variable)}\[\s*[\"']([A-Za-z_$][\w$]*)[\"']\s*\]\s*=[^=]", text[start:end]
    ):
        keys.add(match.group(1))
    return keys


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


# ---------------------------------------------------------------------------
# Extractor: frontend/js — POST /jobs call sites and body builders
# ---------------------------------------------------------------------------

_POST_JOBS = re.compile(
    r"\b(?:api|apiRemote|_apiV1|apiV2|apiV2Remote)\(\s*[\"']/jobs[\"']\s*,\s*\{"
)
_STRINGIFIED = re.compile(r"body\s*:\s*JSON\.stringify\(\s*([^)]*?)\s*\)")
_POST_METHOD = re.compile(r"method\s*:\s*[\"']POST[\"']")
#: A JS function declaration line (the frontend declares one per top-level line).
_FUNC_DECL = re.compile(
    r"^[ \t]*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)",
    re.MULTILINE,
)
#: A body builder: ``function build<Something>Body(spec) { … return body; }``.
_BUILDER_NAME = re.compile(
    r"^[ \t]*(?:async\s+)?function\s+(build[A-Za-z0-9_]*Body)\s*\(",
    re.MULTILINE,
)


@dataclass(frozen=True)
class JsFunction:
    """One resolved JS function definition (cross-file resolution helper)."""

    path: str
    text: str
    start: int
    end: int
    params: tuple[str, ...]

    def keys(self) -> tuple[str, ...]:
        """Return the wire keys of the object the function returns.

        A builder either returns an object literal or a local variable that
        carries the literal plus the optional-field ``name.key = …`` adds, so
        both shapes resolve to the same key set.
        """
        body = self.text[self.start : self.end]
        named = re.search(r"\breturn\s+([A-Za-z_$][\w$]*)\s*;", body)
        if named is not None:
            declared = _declared_literal(body, named.group(1), named.start())
            if declared is not None:
                declared_at, keys = declared
                return tuple(
                    sorted(set(keys) | _mutated_keys(body, declared_at, len(body), named.group(1)))
                )
        match = re.search(r"\breturn\s*\{", body)
        if match is None:
            return ()
        literal_start = match.end() - 1
        literal_end = _match_brace(body, literal_start)
        if literal_end <= literal_start:
            return ()
        return _top_level_keys(body[literal_start + 1 : literal_end])


def _frontend_files(repo_root: Path) -> list[Path]:
    root = repo_root / FRONTEND_ROOT
    if not root.exists():
        return []
    paths: list[Path] = []
    for path in sorted(root.rglob("*.js")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_FRONTEND_DIRS for part in relative.parts):
            continue
        paths.append(path)
    return paths


def _param_names(raw: str) -> tuple[str, ...]:
    """Return the parameter names of one ``function f(<raw>)`` declaration."""
    names: list[str] = []
    for part in raw.split(","):
        candidate = part.strip().split("=")[0].strip().lstrip(".")
        if re.fullmatch(r"[A-Za-z_$][\w$]*", candidate):
            names.append(candidate)
    return tuple(names)


def _declarations(text: str, path: str) -> list[JsFunction]:
    """Return every named function declaration, spanned to the next one.

    The frontend declares its functions at the top level and does not nest named
    declarations, so "from this declaration to the next" is the function's
    scope.  Brace matching is deliberately not used here: a regex literal such
    as ``/[/\\:*?"<>|]/g`` defeats a lexer that only knows quotes.
    """
    matches = list(_FUNC_DECL.finditer(text))
    declarations: list[JsFunction] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        declarations.append(
            JsFunction(
                path=path,
                text=text,
                start=match.start(),
                end=end,
                params=_param_names(match.group(2)),
            )
        )
    return declarations


def _js_function_index(repo_root: Path, files: Sequence[Path]) -> dict[str, JsFunction]:
    """Index every named ``function`` in the shipped frontend sources."""
    index: dict[str, JsFunction] = {}
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(repo_root).as_posix()
        for declaration in _declarations(text, relative):
            name = _decl_name(declaration)
            if name:
                index.setdefault(name, declaration)
    return index


def _enclosing_declaration(declarations: Sequence[JsFunction], position: int) -> JsFunction | None:
    """Return the last function declaration that starts before *position*."""
    found: JsFunction | None = None
    for declaration in declarations:
        if declaration.start <= position:
            found = declaration
        else:
            break
    return found


def _decl_name(declaration: JsFunction) -> str:
    """Return a declaration's function name."""
    match = _FUNC_DECL.match(declaration.text, declaration.start)
    return "" if match is None else match.group(1)


def _resolve_js_body(
    *,
    text: str,
    expr: str,
    call_position: int,
    frame: JsFunction | None,
    index: Mapping[str, JsFunction],
) -> tuple[str, tuple[str, ...], int] | None:
    """Resolve one ``JSON.stringify(X)`` argument to its wire key set."""
    expr = expr.strip()
    if expr.startswith("{"):
        close = _match_brace(expr, 0)
        if close < 0:
            return None
        return "<inline>", _top_level_keys(expr[1:close]), call_position
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):
        if frame is not None and expr in frame.params:
            # A pass-through wrapper (`function (body) { … JSON.stringify(body) }`):
            # the keys belong to whoever built the body, not to this frame.
            return None
        window_start = 0 if frame is None else frame.start
        window = text[window_start:call_position]
        declared = _declared_literal(window, expr, len(window))
        if declared is None:
            return None
        declared_at, keys = declared
        merged = set(keys) | _mutated_keys(window, declared_at, len(window), expr)
        for helper in re.finditer(
            rf"\b([A-Za-z_$][\w$]*)\(\s*{re.escape(expr)}\s*[,)]", window[declared_at:]
        ):
            merged |= _helper_mutation_keys(index.get(helper.group(1)), expr)
        return expr, tuple(sorted(merged)), window_start + declared_at
    call = re.fullmatch(r"([A-Za-z_$][\w$]*)\([^)]*\)", expr)
    if call is not None:
        builder = index.get(call.group(1))
        if builder is None:
            return None
        return call.group(1), builder.keys(), call_position
    return None


def _helper_mutation_keys(helper: JsFunction | None, argument: str) -> set[str]:
    """Keys a one-hop helper assigns on the parameter that receives *argument*."""
    if helper is None or not helper.params:
        return set()
    del argument
    return _mutated_keys(helper.text, helper.start, helper.end, helper.params[0])


def scan_frontend(repo_root: Path) -> list[FieldUse]:
    """Scan every POST ``/jobs`` body site and ``build*Body`` builder."""
    files = _frontend_files(repo_root)
    if not files:
        return []
    index = _js_function_index(repo_root, files)
    uses: list[FieldUse] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(repo_root).as_posix()
        declarations = _declarations(text, relative)
        for match in _POST_JOBS.finditer(text):
            options_start = match.end() - 1
            options_end = _match_brace(text, options_start)
            if options_end < 0:
                continue
            options = text[options_start:options_end]
            if _POST_METHOD.search(options) is None:
                continue
            stringified = _STRINGIFIED.search(options)
            if stringified is None:
                continue
            frame = _enclosing_declaration(declarations, match.start())
            resolved = _resolve_js_body(
                text=text,
                expr=stringified.group(1),
                call_position=match.start(),
                frame=frame,
                index=index,
            )
            if resolved is None:
                continue
            builder, keys, declared_at = resolved
            if not keys:
                continue
            consumer = "<module>" if frame is None else _decl_name(frame) or "<callback>"
            uses.append(
                FieldUse(
                    consumer=f"frontend:{consumer}",
                    kind=KIND_FRONTEND,
                    path=relative,
                    line=_line_of(text, declared_at),
                    request_kind="submit",
                    entry="http",
                    keys=tuple(sorted(set(keys))),
                    note=f"POST /api/v1/jobs body {builder}",
                )
            )
        builders = {_decl_name(item): item for item in declarations}
        for match in _BUILDER_NAME.finditer(text):
            name = match.group(1)
            builder = builders.get(name)
            if builder is None:
                continue
            keys = builder.keys()
            if not keys:
                continue
            uses.append(
                FieldUse(
                    consumer=f"frontend:{name}",
                    kind=KIND_FRONTEND,
                    path=relative,
                    line=_line_of(text, builder.start),
                    request_kind="submit",
                    entry="http",
                    keys=tuple(sorted(keys)),
                    note="body builder (returned literal; injected into a submit path)",
                )
            )
    return uses


# ---------------------------------------------------------------------------
# Extractor: rph/packages — buildToolArguments switch
# ---------------------------------------------------------------------------

_PUT_CALL = re.compile(r"\bput\(\s*\"([A-Za-z0-9_.]+)\"")
_CASE_LABEL = re.compile(r"case\s+\"([a-z_]+)\"\s*:")

#: Bridge operation → v1 request kind the platform then applies.
_BRIDGE_OPERATION_KIND: Mapping[str, str] = {
    "discover": "capabilities",
    "validate": "preflight",
    "submit": "submit",
    "evidence": "analyze",
    "status": "status",
    "prepare": "prepare",
    "read": "read",
}


def _case_runs(body: str) -> list[tuple[int, tuple[str, ...]]]:
    """Return ``(end_position, labels)`` for each consecutive ``case`` group.

    Adjacent labels with nothing but whitespace between them share one block
    (``case "validate": case "submit": case "evidence":``), so every label of
    the run owns the statements that follow it.
    """
    labels = [(m.start(), m.end(), m.group(1)) for m in _CASE_LABEL.finditer(body)]
    runs: list[tuple[int, tuple[str, ...]]] = []
    current: list[str] = []
    previous_end = -1
    for start, end, name in labels:
        if current and body[previous_end:start].strip():
            runs.append((previous_end, tuple(current)))
            current = []
        current.append(name)
        previous_end = end
    if current:
        runs.append((previous_end, tuple(current)))
    return runs


def _operation_guards(body: str) -> list[tuple[int, int, str]]:
    """Return ``(span_start, span_end, operation)`` for ``if (request.operation === "x")``.

    A shared ``case`` block may narrow one ``put`` to a single operation
    (``if (request.operation === "evidence") { put("explicit", …) }``); that
    ``put`` belongs to the guarded operation, not to the whole run.
    """
    guards: list[tuple[int, int, str]] = []
    pattern = re.compile(r"if\s*\(\s*\w+\.operation\s*===\s*[\"']([a-z_]+)[\"']\s*\)\s*\{")
    for match in pattern.finditer(body):
        open_brace = match.end() - 1
        close_brace = _match_brace(body, open_brace)
        if close_brace > open_brace:
            guards.append((open_brace, close_brace, match.group(1)))
    return guards


def scan_rph(repo_root: Path) -> list[FieldUse]:
    """Scan ``buildToolArguments`` in every RPH TypeScript source tree."""
    uses: list[FieldUse] = []
    root = repo_root / RPH_ROOT
    if not root.exists():
        return uses
    for path in sorted(root.rglob("src/**/*.ts")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_RPH_DIRS for part in relative.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        span = _function_body(text, "buildToolArguments")
        if span is None:
            continue
        open_brace, close_brace = span
        body = text[open_brace:close_brace]
        runs = _case_runs(body)
        if not runs:
            runs = [(0, ("<default>",))]
        guards = _operation_guards(body)
        per_operation: dict[str, set[str]] = {name: set() for _, labels in runs for name in labels}
        for match in _PUT_CALL.finditer(body):
            owner = runs[0][1]
            for end, labels in runs:
                if end <= match.start():
                    owner = labels
            narrowed = [
                operation
                for guard_start, guard_end, operation in guards
                if guard_start < match.start() < guard_end
            ]
            for name in narrowed or owner:
                per_operation[name].add(match.group(1))
        for operation, keys in sorted(per_operation.items()):
            if not keys:
                continue
            uses.append(
                FieldUse(
                    consumer=f"rph:buildToolArguments:{operation}",
                    kind=KIND_RPH,
                    path=path.relative_to(repo_root).as_posix(),
                    line=_line_of(text, open_brace),
                    request_kind=_BRIDGE_OPERATION_KIND.get(operation, "unknown"),
                    entry="mcp",
                    keys=tuple(sorted(keys)),
                    note=f"bridge operation {operation}",
                )
            )
    return uses


# ---------------------------------------------------------------------------
# Extractor: Python request builders (CLI + acceptance fixtures)
# ---------------------------------------------------------------------------


def _python_scopes(tree: ast.Module) -> list[tuple[ast.AST, str, tuple[ast.AST, ...]]]:
    """Return ``(scope node, qualname, nodes owned by the scope)``.

    A scope owns every node reachable from it without descending into a nested
    function or class body, so a module-level pass never re-reports (or merges
    keys with) a function-local request dict.
    """
    scopes: list[tuple[ast.AST, str, tuple[ast.AST, ...]]] = [
        (tree, "<module>", tuple(_owned_nodes(tree)))
    ]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scopes.append((node, node.name, tuple(_owned_nodes(node))))
    return scopes


def _owned_nodes(scope: ast.AST) -> Iterable[ast.AST]:
    """Yield the nodes of *scope* without entering a nested function/class body."""
    for child in ast.iter_child_nodes(scope):
        yield child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield from _owned_nodes(child)


def _walk_owned(node: ast.AST) -> Iterable[ast.AST]:
    """Yield *node* and its descendants, stopping at a nested scope boundary."""
    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    for child in ast.iter_child_nodes(node):
        yield from _walk_owned(child)


def _subscript_key(target: ast.expr) -> tuple[str, str] | None:
    """Return ``(variable, key)`` for ``variable["key"] = …`` targets."""
    if not isinstance(target, ast.Subscript):
        return None
    if not isinstance(target.value, ast.Name):
        return None
    slice_node = target.slice
    if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
        return target.value.id, slice_node.value
    return None


def _dict_string_keys(node: ast.Dict) -> set[str]:
    """Return the string keys of one dict literal (``**spread`` ignored)."""
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_path_expr(expr: ast.expr) -> bool:
    """Whether *expr* is a string expression naming the v1 job-create endpoint."""
    if isinstance(expr, ast.Constant):
        return isinstance(expr.value, str) and _JOB_CREATE_PATH in expr.value
    if isinstance(expr, ast.JoinedStr):
        return any(_is_path_expr(value) for value in expr.values)
    if isinstance(expr, ast.FormattedValue):
        return False
    if isinstance(expr, ast.BinOp):
        return _is_path_expr(expr.left) or _is_path_expr(expr.right)
    return False


@dataclass(frozen=True)
class _PyScope:
    """One resolvable Python scope (module or function)."""

    name: str
    values: Mapping[str, tuple[tuple[int, ast.expr], ...]]
    subscripts: Mapping[str, tuple[tuple[int, str], ...]]
    returns: tuple[ast.expr, ...]
    bound: frozenset[str]


def _build_scope(name: str, nodes: Sequence[ast.AST], params: Iterable[str] = ()) -> _PyScope:
    """Resolve one scope in a single bounded traversal.

    Values, subscript adds, returns and the names the scope *binds* without
    assigning (parameters, loop/comprehension targets, ``with … as``,
    ``except … as``) are collected together, so a request name that is only a
    parameter is never resolved to an unrelated local literal.
    """
    values: dict[str, list[tuple[int, ast.expr]]] = {}
    subscripts: dict[str, list[tuple[int, str]]] = {}
    returns: list[ast.expr] = []
    bound: set[str] = set(params)
    for node in nodes:
        for inner in _walk_owned(node):
            position = getattr(inner, "lineno", 0)
            if isinstance(inner, ast.Assign):
                targets = inner.targets
            elif isinstance(inner, ast.AnnAssign) and inner.value is not None:
                targets = [inner.target]
            else:
                targets = []
            for target in targets:
                if isinstance(target, ast.Name):
                    values.setdefault(target.id, []).append((position, inner.value))
                found = _subscript_key(target)
                if found is not None:
                    subscripts.setdefault(found[0], []).append((position, found[1]))
            if isinstance(inner, ast.Return) and inner.value is not None:
                returns.append(inner.value)
            elif isinstance(inner, (ast.For, ast.AsyncFor)):
                bound |= _target_names(inner.target)
            elif isinstance(inner, ast.comprehension):
                bound |= _target_names(inner.target)
            elif isinstance(inner, ast.withitem) and inner.optional_vars is not None:
                bound |= _target_names(inner.optional_vars)
            elif isinstance(inner, ast.ExceptHandler) and inner.name:
                bound.add(inner.name)
    return _PyScope(
        name=name,
        values={key: tuple(items) for key, items in values.items()},
        subscripts={key: tuple(items) for key, items in subscripts.items()},
        returns=tuple(returns),
        bound=frozenset(bound),
    )


def _parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return every parameter name a function binds."""
    args = node.args
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _target_names(target: ast.expr) -> set[str]:
    """Return the names bound by one assignment/iteration target."""
    names: set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _latest_value(scope: _PyScope, variable: str, before: int) -> ast.expr | None:
    """Return the last assignment to *variable* at or before line *before*."""
    candidates = [item for item in scope.values.get(variable, ()) if item[0] <= before]
    if not candidates:
        return None
    return candidates[-1][1]


def _subscript_keys_before(scope: _PyScope, variable: str, before: int) -> set[str]:
    return {key for position, key in scope.subscripts.get(variable, ()) if position <= before}


def _resolve_python_expr(
    expr: ast.expr,
    *,
    scope: _PyScope,
    lookup: Callable[[str], _PyScope | None],
    before: int,
    depth: int = 0,
) -> set[str] | None:
    """Resolve one request expression to its key set (``None`` = not a request).

    Resolution is scope-local and position-aware: the assignment that is live at
    the call site wins, and a name the scope only *binds* (a parameter or a loop
    variable) is not guessed.  A call resolves to what the callee returns (the
    acceptance fixtures build their body in a helper and hand it to the HTTP
    helper), never to "any dict in that function".
    """
    if isinstance(expr, ast.Dict):
        return _dict_string_keys(expr)
    if depth > 2:
        return None
    if isinstance(expr, ast.Name):
        if expr.id in scope.bound:
            return None
        value = _latest_value(scope, expr.id, before)
        if value is None:
            return None
        resolved = _resolve_python_expr(
            value, scope=scope, lookup=lookup, before=before, depth=depth + 1
        )
        if resolved is None:
            return None
        return resolved | _subscript_keys_before(scope, expr.id, before)
    if isinstance(expr, ast.Call):
        callee = lookup(_call_name(expr))
        if callee is None:
            return None
        keys: set[str] = set()
        for returned in callee.returns:
            if isinstance(returned, ast.Name):
                value = _latest_value(callee, returned.id, 10**9)
                if value is not None:
                    resolved = _resolve_python_expr(
                        value, scope=callee, lookup=lookup, before=10**9, depth=depth + 1
                    )
                    if resolved is not None:
                        keys |= resolved
                keys |= _subscript_keys_before(callee, returned.id, 10**9)
            elif isinstance(returned, ast.Dict):
                keys |= _dict_string_keys(returned)
        return keys or None
    return None


def _request_kind_for(keys: Iterable[str], call: ast.Call, path_lines: Mapping[int, str]) -> str:
    """Pick the v1 request kind a body belongs to (submit/prepare/analyze/usage)."""
    key_set = set(keys)
    start = getattr(call, "lineno", 0)
    end = getattr(call, "end_lineno", start) or start
    window = "".join(path_lines.get(line, "") for line in range(start, end + 1))
    # Route-aware kinds: the analyze and usage endpoints live under the same
    # /api/v1/jobs prefix, but their bodies belong to their own contracts.
    if "/analyze" in window:
        return "analyze"
    if "/usage" in window:
        return "usage"
    if "/prepare" in window:
        return "prepare"
    if "purpose" in key_set and key_set & {"artifact_id", "job_id", "task_ref"}:
        return "prepare"
    return "submit"


def _python_request_dicts(text: str) -> list[tuple[str, str, int, tuple[str, ...]]]:
    """Return ``(request_kind, scope, line, keys)`` for real request builders.

    Only expressions handed to a ``POST /api/v1/jobs`` helper (the endpoint is a
    direct string argument of the call) or to ``canonical_submit_request`` are
    request bodies.  A plan/spec fixture that happens to carry
    ``workflow``/``input`` (an approval plan, a ``TaskRequest``) is not one and
    is deliberately not reported.
    """
    tree = ast.parse(text)
    path_lines = {
        index: line
        for index, line in enumerate(text.splitlines(), start=1)
        if _JOB_CREATE_PATH in line or "/prepare" in line or "/analyze" in line or "/usage" in line
    }
    scope_nodes = _python_scopes(tree)
    owned_by_name = {name: nodes for _node, name, nodes in scope_nodes}
    params_by_name = {
        name: _parameters(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else ()
        for node, name, _nodes in scope_nodes
    }
    built: dict[str, _PyScope] = {}

    def lookup(name: str) -> _PyScope | None:
        nodes = owned_by_name.get(name)
        if nodes is None:
            return None
        if name not in built:
            built[name] = _build_scope(name, nodes, params_by_name.get(name, ()))
        return built[name]

    results: list[tuple[str, str, int, tuple[str, ...]]] = []
    for _node, scope_name, nodes in scope_nodes:
        request_calls = [call for call in _owned_calls(nodes) if _is_request_call(call)]
        if not request_calls:
            continue
        scope = lookup(scope_name)
        if scope is None:  # pragma: no cover - defensive
            continue
        for call in request_calls:
            before = getattr(call, "lineno", 0)
            candidates = [
                keyword.value for keyword in call.keywords if keyword.arg in _BODY_KEYWORDS
            ]
            candidates.extend(call.args)
            for candidate in candidates:
                keys = _resolve_python_expr(candidate, scope=scope, lookup=lookup, before=before)
                if not keys:
                    continue
                results.append(
                    (
                        _request_kind_for(keys, call, path_lines),
                        scope_name,
                        getattr(candidate, "lineno", before),
                        tuple(sorted(keys)),
                    )
                )
    return results


def _is_request_call(call: ast.Call) -> bool:
    """Whether one call reaches the v1 job-create endpoint or the normaliser."""
    if _call_name(call) in _SUBMIT_NORMALISERS:
        return True
    direct = list(call.args) + [keyword.value for keyword in call.keywords]
    return any(_is_path_expr(argument) for argument in direct)


def _owned_calls(nodes: Sequence[ast.AST]) -> list[ast.Call]:
    """Return the calls owned by one scope (no nested function bodies)."""
    calls: list[ast.Call] = []
    for node in nodes:
        for inner in _walk_owned(node):
            if isinstance(inner, ast.Call):
                calls.append(inner)
    return calls


def scan_python(repo_root: Path, relative: str, kind: str, prefix: str) -> list[FieldUse]:
    """Scan one Python file (or a directory of them) for request builders."""
    uses: list[FieldUse] = []
    target = repo_root / relative
    paths = sorted(target.rglob("*.py")) if target.is_dir() else [target]
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            found = _python_request_dicts(text)
        except SyntaxError:
            continue
        for request_kind, scope_name, line, keys in found:
            uses.append(
                FieldUse(
                    consumer=f"{prefix}:{scope_name}",
                    kind=kind,
                    path=path.relative_to(repo_root).as_posix(),
                    line=line,
                    request_kind=request_kind,
                    entry="cli" if kind == KIND_CLI else "http",
                    keys=keys,
                )
            )
    return uses


# Extractor: docs contract samples
# ---------------------------------------------------------------------------


def scan_docs(repo_root: Path) -> list[FieldUse]:
    """Scan contract sample JSON under ``docs`` for a request payload."""
    uses: list[FieldUse] = []
    root = repo_root / DOCS_ROOT
    if not root.exists():
        return uses
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, Mapping):
            continue
        payload = data.get("payload")
        operation = data.get("operation")
        if not isinstance(payload, Mapping) or not isinstance(operation, str):
            continue
        if operation not in {"submit", "preflight", "prepare"}:
            continue
        expected = data.get("expected")
        negative = (
            str(data.get("kind") or "") == "negative"
            and isinstance(expected, Mapping)
            and expected.get("valid") is False
        )
        sample_id = str(data.get("sample_id") or path.stem)
        uses.append(
            FieldUse(
                consumer=f"docs:{sample_id}",
                kind=KIND_DOCS,
                path=path.relative_to(repo_root).as_posix(),
                line=1,
                request_kind=operation,
                entry="http",
                keys=tuple(sorted(str(name) for name in payload)),
                note="contract sample payload",
                expected_rejection=negative,
            )
        )
    return uses


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify(use: FieldUse, authorities: Authorities) -> dict[str, Any]:
    """Attach ``extra_keys``/``rejected_keys``/``disposition`` to one field use."""
    policy = authorities.policies.get(use.request_kind)
    contract = authorities.contract_fields.get(use.request_kind, frozenset())
    entry: dict[str, Any] = dict(
        consumer=use.consumer,
        kind=use.kind,
        path=use.path,
        line=use.line,
        request_kind=use.request_kind,
        entry=use.entry,
        keys=list(use.keys),
        note=use.note,
    )
    if policy is None:
        entry.update(
            extra_keys=[],
            rejected_keys=[],
            contract_extra_keys=sorted(set(use.keys) - contract),
            disposition=DISPOSITION_OTHER_KIND,
        )
        return entry
    extra = sorted(name for name in use.keys if name not in policy)
    rejected = sorted(
        name
        for name in use.keys
        if name in policy
        and policy[name].disposition == _SERVER_PATH_DISPOSITION
        and use.entry not in policy[name].entries
    )
    contract_extra = sorted(name for name in use.keys if contract and name not in contract)
    if use.expected_rejection:
        disposition = DISPOSITION_EXPECTED_REJECTION
    elif extra or rejected:
        disposition = DISPOSITION_BREAKING
    elif contract_extra:
        disposition = DISPOSITION_CONTRACT_DIVERGENCE
    else:
        disposition = DISPOSITION_COMPLIANT
    entry.update(
        extra_keys=extra,
        rejected_keys=rejected,
        contract_extra_keys=contract_extra,
        disposition=disposition,
    )
    return entry


def _dedupe(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical ``(consumer, path, keys)`` records."""
    seen: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for entry in entries:
        key = (str(entry["consumer"]), str(entry["path"]), tuple(entry["keys"]))
        current = seen.get(key)
        if current is None or int(entry["line"]) < int(current["line"]):
            seen[key] = entry
    return sorted(
        seen.values(),
        key=lambda item: (item["kind"], item["consumer"], item["path"], item["line"]),
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def build_report(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Scan every registered consumer surface and return the frozen report."""
    authorities = load_authorities(repo_root)
    raw: list[FieldUse] = []
    raw.extend(scan_frontend(repo_root))
    raw.extend(scan_rph(repo_root))
    raw.extend(scan_python(repo_root, CLI_PATH, KIND_CLI, "cli"))
    raw.extend(scan_python(repo_root, ACCEPTANCE_ROOT, KIND_ACCEPTANCE, "acceptance"))
    raw.extend(scan_docs(repo_root))

    entries = _dedupe([classify(use, authorities) for use in raw])
    counts: dict[str, int] = {name: 0 for name in sorted(DISPOSITIONS)}
    for entry in entries:
        counts[str(entry["disposition"])] += 1
    breaking = [entry for entry in entries if entry["disposition"] == DISPOSITION_BREAKING]
    affected = sorted({str(entry["consumer"]) for entry in breaking})

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "scanner_version": SCANNER_VERSION,
        "policy_authority": POLICY_AUTHORITY,
        "contract": CONTRACT_PATH,
        "scanned_roots": [
            FRONTEND_ROOT,
            RPH_ROOT,
            CLI_PATH,
            ACCEPTANCE_ROOT,
            DOCS_ROOT,
        ],
        "excluded": [
            f"{FRONTEND_ROOT}/**/{name}/** (test doubles, not shipped consumers)"
            for name in EXCLUDED_FRONTEND_DIRS
        ]
        + [f"{RPH_ROOT}/**/{name}/** (build output)" for name in EXCLUDED_RPH_DIRS],
        "authority_snapshot": {
            "declared_fields": {
                kind: sorted(policy) for kind, policy in authorities.policies.items()
            },
            "server_path_rejected": sorted(
                {
                    name
                    for policy in authorities.policies.values()
                    for name, row in policy.items()
                    if row.disposition == _SERVER_PATH_DISPOSITION
                }
            ),
            "contract_fields": {
                kind: sorted(fields) for kind, fields in sorted(authorities.contract_fields.items())
            },
            "legacy_allowlist": list(authorities.legacy_allowlist),
        },
        "consumers": entries,
        "summary": {
            "consumers": len(entries),
            "counts": counts,
            "breaking_consumers": affected,
        },
    }
    report["digest"] = report_digest(report)
    return report


def report_digest(report: Mapping[str, Any]) -> str:
    """Return ``sha256:<hex>`` over the stable part of a consumer report.

    Line numbers are excluded (they move on any edit and are not part of the
    decision), so the digest pins *which consumer sends which keys and how it is
    disposed*, not where the code happens to sit.
    """
    material = {
        "consumers": [
            {
                "consumer": entry["consumer"],
                "kind": entry["kind"],
                "path": entry["path"],
                "request_kind": entry["request_kind"],
                "keys": entry["keys"],
                "extra_keys": entry["extra_keys"],
                "rejected_keys": entry["rejected_keys"],
                "contract_extra_keys": entry["contract_extra_keys"],
                "disposition": entry["disposition"],
            }
            for entry in report["consumers"]
        ],
        "summary": report["summary"],
        "authority_snapshot": report["authority_snapshot"],
    }
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _summary_line(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    counts = summary["counts"]
    return (
        f"consumers={summary['consumers']} "
        f"compliant={counts[DISPOSITION_COMPLIANT]} "
        f"breaking={counts[DISPOSITION_BREAKING]} "
        f"contract_divergence={counts[DISPOSITION_CONTRACT_DIVERGENCE]} "
        f"expected_rejection={counts[DISPOSITION_EXPECTED_REJECTION]} "
        f"other_request_kind={counts[DISPOSITION_OTHER_KIND]} "
        f"digest={report['digest']}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the scan; see the module docstring for the exit-code contract."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root to scan (default: this checkout)",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the JSON report here")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 when at least one consumer is rejected by the live policy",
    )
    args = parser.parse_args(argv)

    try:
        report = build_report(args.repo_root.resolve())
    except RuntimeError as exc:
        print(f"check_v1_request_field_consumers: {exc}", file=sys.stderr)
        return EXIT_ERROR

    payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")

    if args.check:
        print(_summary_line(report))
        breaking = report["summary"]["breaking_consumers"]
        for name in breaking:
            print(f"BREAKING {name}", file=sys.stderr)
        return EXIT_BREAKING if breaking else EXIT_OK

    sys.stdout.write(payload)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - script entry
    raise SystemExit(main())
