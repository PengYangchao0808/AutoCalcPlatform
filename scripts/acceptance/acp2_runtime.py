#!/usr/bin/env python3.11
"""ACP 2.0 runtime acceptance harness (B01 skeleton).

The receipt protocol is frozen in
``docs/reference/next-change/acp2-contracts/runtime/acceptance-result.schema.json``
(root schema ``acp2.acceptance.v1``) and described in
``acceptance-harness.v1.md``.  This module owns the single validator and the
executable scenario skeleton; scenario bodies are implemented by the owner named
in the registry (B04a, B05, B07, ... B25b).

Exit codes are exactly 0/1/2/3: PASS (or tool check worked) / FAIL /
NOT_VERIFIED / usage error.  ``--help``, ``--dry-run`` and ``--check-only`` are
tool checks and are never a scenario PASS.

Stdlib only.  The schema engine is a deliberate JSON-Schema-subset validator
(``type``/``required``/``enum``/``const``/``minimum``/``maximum``/``pattern``/
``items``/``properties``/``additionalProperties``/``propertyNames``/``$ref``/
``allOf``/``anyOf``/``oneOf``/``not``/``if``-``then``-``else``) with explicit
reference resolution; it is not a general-purpose JSON Schema implementation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "docs/reference/next-change/acp2-contracts"
SCHEMA_PATH = CONTRACT_ROOT / "runtime" / "acceptance-result.schema.json"
SAMPLES_ROOT = CONTRACT_ROOT / "runtime" / "samples" / "acceptance"
SCRIPT_PATH = Path(__file__).resolve()

ACCEPTANCE_SCHEMA = "acp2.acceptance.v1"
SUMMARY_SCHEMA = "acp2.acceptance.summary.v1"
SAMPLES_SCHEMA = "acp2.acceptance.samples.v1"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SUPPORTED_CONTRACT_MAJOR = "1"
CONTRACT_VERSION = "1.1.0"

BASELINE_CANDIDATE_REVISION = "096cdffe9a3817d8ee443d824830d87ec6e07bc2"
SERVER_CANDIDATE_REVISION = (
    "sha256:bb45a46c93d241d03ffae2952a78a2736a637a3f2c8d69b72d2bd0c4fafc3e12"
)

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3

RESULT_VALUES = ("PASS", "FAIL", "NOT_VERIFIED")
SCOPE_VALUES = ("IN_SCOPE", "DEFERRED", "NOT_APPLICABLE")
EVIDENCE_KINDS = ("fixture", "live", "aggregate")
MCP_TOOLS = (
    "acp_capabilities",
    "acp_preflight",
    "acp_submit",
    "acp_status",
    "acp_cancel",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_analyze",
)
OWNER_VALUES = ("B", "A", "C", "integration")
OPERATION_CLASSES = (
    "submit",
    "read",
    "prepare",
    "discover",
    "standalone",
    "control",
    "aggregate",
)
PLATFORM_REF_KEYS = (
    "job_id",
    "operation_id",
    "attempt",
    "release_id",
    "submission_id",
    "manifest_generation",
    "negotiated_protocol",
)
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$"
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


# ---------------------------------------------------------------------------
# Scenario registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseSpec:
    """One signable (scenario, phase) registry row."""

    owner: str
    operation_class: str
    target: str
    required_args: tuple[str, ...]
    controller_required: bool
    prerequisites: tuple[str, ...]
    side_effects: tuple[str, ...]
    timeout: int
    manifest_generation_required: bool = False


@dataclass(frozen=True)
class ScenarioSpec:
    """One of the 15 immutable B scenario IDs."""

    scenario: str
    default_owner: str
    phases: dict[str, PhaseSpec]
    missing_capability: str


def _phase(
    owner: str,
    operation_class: str,
    target: str,
    timeout: int,
    *,
    args: tuple[str, ...] = ("out",),
    controller: bool = False,
    prerequisites: tuple[str, ...] = (),
    side_effects: tuple[str, ...] = ("none",),
    manifest_generation_required: bool = False,
) -> PhaseSpec:
    prereqs: list[str] = []
    for item in (("controller",) if controller else ()) + prerequisites:
        if item not in prereqs:
            prereqs.append(item)
    return PhaseSpec(
        owner=owner,
        operation_class=operation_class,
        target=target,
        required_args=args,
        controller_required=controller,
        prerequisites=prereqs,
        side_effects=side_effects,
        timeout=timeout,
        manifest_generation_required=manifest_generation_required,
    )


def _standard_phases(
    owner: str,
    operation_class: str,
    target: str,
    timeout: int,
    *,
    args: tuple[str, ...] = ("out",),
    controller: bool = False,
    prerequisites: tuple[str, ...] = (),
    side_effects: tuple[str, ...] = ("none",),
    manifest_generation_required: bool = False,
) -> dict[str, PhaseSpec]:
    return {
        "main": _phase(
            owner,
            operation_class,
            target,
            timeout,
            args=args,
            controller=controller,
            prerequisites=prerequisites,
            side_effects=side_effects,
            manifest_generation_required=manifest_generation_required,
        )
    }


B_SCENARIOS: dict[str, ScenarioSpec] = {
    "env-runtime": ScenarioSpec(
        "env-runtime",
        "B07",
        _standard_phases("B07", "discover", "local", 120),
        "B07 environment/runtime probe is not implemented in the B01 skeleton",
    ),
    "entry-readonly": ScenarioSpec(
        "entry-readonly",
        "B04a",
        {
            "main": _phase("B04a", "discover", "local", 120, controller=True),
            "live": _phase(
                "B04a",
                "discover",
                "local",
                180,
                controller=True,
                side_effects=("no task submit", "no page render", "no science"),
            ),
        },
        "B04a read-only entry equivalence probe is not implemented in the B01 skeleton",
    ),
    "mcp-stdio": ScenarioSpec(
        "mcp-stdio",
        "B05",
        _standard_phases(
            "B05",
            "discover",
            "local",
            180,
            args=("launch", "out"),
            controller=True,
            prerequisites=("launch", "controller"),
            side_effects=("adapter process start", "no task/page/science side effect"),
        ),
        "B05 MCP stdio launch/handshake probe is not implemented in the B01 skeleton",
    ),
    "submit-replay": ScenarioSpec(
        "submit-replay",
        "B08",
        _standard_phases(
            "B08",
            "submit",
            "local",
            600,
            args=("fixture", "out"),
            controller=True,
            prerequisites=("fixture", "controller"),
            side_effects=("one task create", "idempotent replay"),
        ),
        "B08 idempotent submit/replay is not implemented in the B01 skeleton",
    ),
    "local-singlepoint": ScenarioSpec(
        "local-singlepoint",
        "B10",
        {
            "main": _phase(
                "B10",
                "submit",
                "local",
                1800,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("one local QC task",),
            ),
            "live": _phase(
                "B10",
                "submit",
                "local",
                1800,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("one local QC task",),
            ),
        },
        "B10 local singlepoint execution is not implemented in the B01 skeleton",
    ),
    "lsf-singlepoint": ScenarioSpec(
        "lsf-singlepoint",
        "B11",
        {
            "main": _phase(
                "B11",
                "submit",
                "remote",
                3600,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("one LSF submission",),
            ),
            "live": _phase(
                "B11",
                "submit",
                "remote",
                3600,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("one LSF submission",),
            ),
        },
        "B11 LSF singlepoint execution is not implemented in the B01 skeleton",
    ),
    "three-entry": ScenarioSpec(
        "three-entry",
        "B10",
        _standard_phases(
            "B10",
            "submit",
            "local",
            900,
            args=("fixture", "out"),
            controller=True,
            prerequisites=("fixture", "controller"),
            side_effects=("CLI/HTTP/MCP submit the same request",),
        ),
        "B10 three-entry submit equivalence is not implemented in the B01 skeleton",
    ),
    "artifact": ScenarioSpec(
        "artifact",
        "B12",
        _standard_phases(
            "B12",
            "read",
            "local",
            900,
            args=("fixture", "out"),
            controller=True,
            prerequisites=("fixture", "controller"),
            side_effects=("explicit prepare", "cache-only read"),
            manifest_generation_required=True,
        ),
        "B12 artifact prepare/read is not implemented in the B01 skeleton",
    ),
    "control-recovery": ScenarioSpec(
        "control-recovery",
        "B13",
        _standard_phases(
            "B13",
            "control",
            "local",
            1800,
            args=("fixture", "out"),
            controller=True,
            prerequisites=("fixture", "controller"),
            side_effects=("pause", "unpause", "cancel", "controlled restart"),
        ),
        "B13 control/recovery matrix is not implemented in the B01 skeleton",
    ),
    "approval-budget": ScenarioSpec(
        "approval-budget",
        "B16",
        {
            "approval": _phase(
                "B16",
                "control",
                "local",
                600,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("approval request/decision only",),
            ),
            "budget": _phase(
                "B17",
                "control",
                "local",
                600,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("budget reservation/settlement only",),
            ),
        },
        "B16/B17 approval and budget acceptance is not implemented in the B01 skeleton",
    ),
    "da-runtime": ScenarioSpec(
        "da-runtime",
        "B18",
        {
            "da-preflight": _phase(
                "B18",
                "discover",
                "local",
                300,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
            ),
            "da-submit": _phase(
                "B20a",
                "submit",
                "local",
                900,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("one task submit",),
            ),
            "da-evidence": _phase(
                "B20b",
                "read",
                "local",
                300,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("cache-only read",),
                manifest_generation_required=True,
            ),
            "aggregate": _phase(
                "B20c",
                "aggregate",
                "local",
                120,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
            ),
        },
        "B18/B20a/B20b/B20c DA runtime acceptance is not implemented in the B01 skeleton",
    ),
    "infra-plugin": ScenarioSpec(
        "infra-plugin",
        "B21",
        {
            "infra:install": _phase(
                "B21",
                "control",
                "local",
                900,
                args=("fixture", "out"),
                prerequisites=("fixture",),
                side_effects=("plugin/adapter install",),
            ),
            "infra:upgrade": _phase(
                "B22",
                "control",
                "local",
                900,
                args=("fixture", "out"),
                prerequisites=("fixture",),
                side_effects=("plugin/adapter upgrade",),
            ),
        },
        "B21/B22 infrastructure plugin install/upgrade is not implemented in the B01 skeleton",
    ),
    "multitenant": ScenarioSpec(
        "multitenant",
        "B15",
        {
            "multitenant:authz": _phase(
                "B15",
                "control",
                "local",
                600,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("denied requests only",),
            ),
            "multitenant:two-project": _phase(
                "B23",
                "control",
                "local",
                900,
                args=("fixture", "out"),
                controller=True,
                prerequisites=("fixture", "controller"),
                side_effects=("isolated project tasks",),
            ),
        },
        "B15/B23 multitenant authorization and two-project isolation is not implemented",
    ),
    "migration": ScenarioSpec(
        "migration",
        "B24a",
        _standard_phases(
            "B24a",
            "control",
            "local",
            1800,
            args=("fixture", "out"),
            prerequisites=("fixture",),
            side_effects=("export", "import", "backup restore"),
        ),
        "B24a migration/backup restore acceptance is not implemented in the B01 skeleton",
    ),
    "package-install": ScenarioSpec(
        "package-install",
        "B25b",
        _standard_phases(
            "B25b",
            "control",
            "local",
            1800,
            side_effects=("clean-environment install",),
        ),
        "B25b clean-environment package install is not implemented in the B01 skeleton",
    ),
}

EXPECTED_B_SCENARIOS = (
    "env-runtime",
    "entry-readonly",
    "mcp-stdio",
    "submit-replay",
    "local-singlepoint",
    "lsf-singlepoint",
    "three-entry",
    "artifact",
    "control-recovery",
    "approval-budget",
    "da-runtime",
    "infra-plugin",
    "multitenant",
    "migration",
    "package-install",
)


def all_phase_rows() -> list[tuple[str, str, PhaseSpec]]:
    rows: list[tuple[str, str, PhaseSpec]] = []
    for scenario in EXPECTED_B_SCENARIOS:
        spec = B_SCENARIOS[scenario]
        for phase_name, phase in spec.phases.items():
            rows.append((scenario, phase_name, phase))
    return rows


# ---------------------------------------------------------------------------
# JSON Schema subset engine
# ---------------------------------------------------------------------------


class _RefError(Exception):
    pass


_SCHEMA_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_REF_CACHE: dict[str, list[str]] = {}


def load_schema(path: Path = SCHEMA_PATH) -> dict[str, Any]:
    key = str(path)
    mtime = path.stat().st_mtime
    cached = _SCHEMA_CACHE.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"schema is not an object: {path}")
    _SCHEMA_CACHE[key] = (mtime, document)
    return document


def _pointer(document: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return document
    current = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise _RefError(f"missing pointer segment {token!r}")
            current = current[token]
        elif isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise _RefError(f"bad array pointer {token!r}") from exc
        else:
            raise _RefError(f"cannot descend into {type(current).__name__}")
    return current


def _resolve_ref(
    ref: str, root_schema: dict[str, Any], base_dir: Path
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    if ref.startswith("#"):
        return _pointer(root_schema, ref[1:]), root_schema, base_dir
    file_part, _, fragment = ref.partition("#")
    target_path = (base_dir / file_part).resolve()
    if not target_path.is_file():
        raise _RefError(f"external schema missing: {file_part}")
    document = json.loads(target_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise _RefError(f"external schema is not an object: {file_part}")
    resolved = _pointer(document, fragment) if fragment else document
    return resolved, document, target_path.parent


def validate_schema_refs(schema: dict[str, Any], schema_path: Path = SCHEMA_PATH) -> list[str]:
    key = f"{schema_path}:{schema_path.stat().st_mtime}"
    if key in _REF_CACHE:
        return _REF_CACHE[key]
    errors: list[str] = []

    def walk(node: Any, base_dir: Path, depth: int) -> None:
        if depth > 128:
            errors.append("unresolved_ref:depth_limit")
            return
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                try:
                    resolved, resolved_root, resolved_base = _resolve_ref(ref, schema, base_dir)
                except (_RefError, json.JSONDecodeError, OSError) as exc:
                    errors.append(f"unresolved_ref:{ref}:{exc}")
                else:
                    walk(resolved, resolved_base, depth + 1)
                    if resolved_root is not schema:
                        walk(resolved_root, resolved_base, depth + 1)
            for child in node.values():
                walk(child, base_dir, depth + 1)
        elif isinstance(node, list):
            for child in node:
                walk(child, base_dir, depth + 1)

    walk(schema, schema_path.parent, 0)
    _REF_CACHE[key] = errors
    return errors


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _validate_schema(
    instance: Any,
    schema: Any,
    path: str,
    root_schema: dict[str, Any],
    base_dir: Path,
    depth: int = 0,
) -> list[str]:
    if depth > 128:
        return [f"schema:depth_limit:{path}"]
    if isinstance(schema, bool):
        return [] if schema else [f"schema:false:{path}"]
    if not isinstance(schema, dict):
        return []
    errors: list[str] = []

    ref = schema.get("$ref")
    if isinstance(ref, str):
        try:
            resolved, resolved_root, resolved_base = _resolve_ref(ref, root_schema, base_dir)
        except (_RefError, json.JSONDecodeError, OSError) as exc:
            return [f"unresolved_ref:{ref}:{exc}"]
        return _validate_schema(instance, resolved, path, resolved_root, resolved_base, depth + 1)

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(instance, item) for item in allowed):
            rendered = "|".join(str(item) for item in allowed)
            return [f"schema:type:{path}:expected {rendered}"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"schema:const:{path}")
    if isinstance(schema.get("enum"), list) and instance not in schema["enum"]:
        errors.append(f"schema:enum:{path}")

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(instance) < min_length:
            errors.append(f"schema:minLength:{path}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(instance) > max_length:
            errors.append(f"schema:maxLength:{path}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            errors.append(f"schema:pattern:{path}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and instance < minimum:
            errors.append(f"schema:minimum:{path}")
        maximum = schema.get("maximum")
        if isinstance(maximum, (int, float)) and instance > maximum:
            errors.append(f"schema:maximum:{path}")

    if isinstance(instance, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in instance:
                    errors.append(f"schema:missing_required:{path}:{key}")
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in instance:
                    errors.extend(
                        _validate_schema(
                            instance[key],
                            child_schema,
                            f"{path}.{key}",
                            root_schema,
                            base_dir,
                            depth + 1,
                        )
                    )
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in instance:
                errors.extend(
                    _validate_schema(
                        key,
                        property_names,
                        f"{path}.<key:{key}>",
                        root_schema,
                        base_dir,
                        depth + 1,
                    )
                )
        additional = schema.get("additionalProperties", True)
        known = set(properties) if isinstance(properties, dict) else set()
        for key, value in instance.items():
            if key in known:
                continue
            if additional is False:
                errors.append(f"schema:unexpected_field:{path}:{key}")
            elif isinstance(additional, dict):
                errors.extend(
                    _validate_schema(
                        value,
                        additional,
                        f"{path}.{key}",
                        root_schema,
                        base_dir,
                        depth + 1,
                    )
                )

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(instance) < min_items:
            errors.append(f"schema:minItems:{path}")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(instance) > max_items:
            errors.append(f"schema:maxItems:{path}")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                errors.extend(
                    _validate_schema(
                        value,
                        items,
                        f"{path}[{index}]",
                        root_schema,
                        base_dir,
                        depth + 1,
                    )
                )

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for child in all_of:
            errors.extend(_validate_schema(instance, child, path, root_schema, base_dir, depth + 1))
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        if not any(
            not _validate_schema(instance, child, path, root_schema, base_dir, depth + 1)
            for child in any_of
        ):
            errors.append(f"schema:anyOf:{path}")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = sum(
            1
            for child in one_of
            if not _validate_schema(instance, child, path, root_schema, base_dir, depth + 1)
        )
        if matches != 1:
            errors.append(f"schema:oneOf:{path}")
    not_schema = schema.get("not")
    if isinstance(not_schema, dict):
        if not _validate_schema(instance, not_schema, path, root_schema, base_dir, depth + 1):
            errors.append(f"schema:not:{path}")
    if_schema = schema.get("if")
    if isinstance(if_schema, dict):
        branch = (
            "then"
            if not _validate_schema(instance, if_schema, path, root_schema, base_dir, depth + 1)
            else "else"
        )
        child = schema.get(branch)
        if isinstance(child, dict):
            errors.extend(_validate_schema(instance, child, path, root_schema, base_dir, depth + 1))
    return errors


# ---------------------------------------------------------------------------
# Alias normalization and semantic validation
# ---------------------------------------------------------------------------


def _extension_str(document: dict[str, Any], key: str) -> str | None:
    extensions = document.get("extensions")
    if not isinstance(extensions, dict):
        return None
    value = extensions.get(key)
    return value if isinstance(value, str) else None


def _extension_list(document: dict[str, Any], key: str) -> list[str]:
    extensions = document.get("extensions")
    if not isinstance(extensions, dict):
        return []
    value = extensions.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def normalize_receipt(document: Any) -> tuple[Any, list[str]]:
    """Apply the ingest-only aliases; return the normalized document and errors."""

    if not isinstance(document, dict):
        return document, ["not_an_object"]
    normalized = copy.deepcopy(document)
    errors: list[str] = []

    if "scenario_id" in normalized:
        alias = normalized["scenario_id"]
        if "scenario" in normalized and normalized["scenario"] != alias:
            errors.append("scenario_alias_conflict")
        elif "scenario" not in normalized:
            normalized["scenario"] = alias
        normalized.pop("scenario_id", None)

    owner = normalized.get("owner")
    scenario = normalized.get("scenario")
    if owner == "C" and isinstance(scenario, str) and scenario.startswith("runtime:"):
        normalized["scenario"] = "experience:" + scenario[len("runtime:") :]

    if normalized.get("phase") == "preflight":
        normalized["phase"] = "da-preflight"
    if normalized.get("readiness") == "deployed":
        normalized["readiness"] = "integration_ready"

    if "evidence" in normalized:
        alias = normalized["evidence"]
        if "artifacts" in normalized and normalized["artifacts"] != alias:
            errors.append("evidence_alias_conflict")
        elif "artifacts" not in normalized:
            normalized["artifacts"] = alias
        normalized.pop("evidence", None)

    platform = normalized.get("platform")
    if isinstance(platform, dict):
        for key in PLATFORM_REF_KEYS:
            if key not in platform:
                continue
            value = platform[key]
            if key in normalized and normalized[key] != value:
                errors.append(f"platform_alias_conflict:{key}")
            else:
                normalized[key] = value
        normalized.pop("platform", None)
    for key in PLATFORM_REF_KEYS:
        normalized.setdefault(key, None)
    return normalized, errors


def _platform_matrix_errors(
    document: dict[str, Any],
    operation_class: str | None,
    target: str | None,
    phase_spec: PhaseSpec | None,
) -> list[str]:
    errors: list[str] = []
    evidence_kind = document.get("evidence_kind")
    refs = {key: document.get(key) for key in PLATFORM_REF_KEYS}

    if evidence_kind == "fixture":
        for key in PLATFORM_REF_KEYS:
            if refs[key] is not None:
                errors.append(f"fixture_platform_refs_not_null:{key}")
        return errors
    if evidence_kind != "live":
        return errors

    if operation_class in (None, "", "discover", "standalone", "prepare"):
        allowed = {"negotiated_protocol"}
        if operation_class == "prepare":
            allowed.add("operation_id")
        label = operation_class or "standalone"
        for key in PLATFORM_REF_KEYS:
            if key in allowed:
                continue
            if refs[key] is not None:
                errors.append(f"live_{label}_platform_ref:{key}")
        return errors

    if operation_class == "submit":
        if refs["job_id"] is None:
            errors.append("live_submit_missing_job_id")
        if refs["attempt"] is None:
            errors.append("live_submit_missing_attempt")
        if target == "remote":
            if refs["release_id"] is None:
                errors.append("live_submit_missing_release_id")
            if refs["submission_id"] is None:
                errors.append("live_submit_missing_submission_id")
        else:
            if refs["submission_id"] is not None:
                errors.append("live_submit_submission_id_not_null_local")
        if refs["manifest_generation"] is not None:
            errors.append("live_submit_manifest_generation_not_null")
        return errors

    if operation_class == "read":
        if refs["job_id"] is None:
            errors.append("live_read_missing_job_id")
        if refs["attempt"] is None:
            errors.append("live_read_missing_attempt")
        if refs["release_id"] is None:
            errors.append("live_read_missing_release_id")
        if target == "remote":
            if refs["submission_id"] is None:
                errors.append("live_read_missing_submission_id")
        else:
            if refs["submission_id"] is not None:
                errors.append("live_local_submission_id_not_null")
        if (
            phase_spec is not None
            and phase_spec.manifest_generation_required
            and refs["manifest_generation"] is None
        ):
            errors.append("live_read_missing_manifest_generation")
        return errors

    return errors


def _aggregate_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    evidence_kind = document.get("evidence_kind")
    phase = document.get("phase")
    if evidence_kind != "aggregate" and phase != "aggregate":
        return errors

    sub_results = document.get("sub_results")
    children: list[dict[str, Any]] = []
    if isinstance(sub_results, list):
        children = [
            child
            for child in sub_results
            if isinstance(child, dict)
            and isinstance(child.get("receipt"), str)
            and child["receipt"]
        ]
    if not children:
        errors.append("aggregate_missing_child_refs")
        return errors

    required = _extension_list(document, "da.required_child_phases")
    if not required:
        required = _extension_list(document, "acceptance.required_child_phases")
    by_phase = {child.get("phase"): child for child in children}
    for phase_name in required:
        if phase_name not in by_phase:
            errors.append(f"aggregate_missing_required_child_phase:{phase_name}")
    if document.get("result") == "PASS":
        for phase_name in required:
            child = by_phase.get(phase_name)
            if child is not None and child.get("result") != "PASS":
                errors.append(f"aggregate_child_not_pass:{phase_name}")
    return errors


def _semantic_errors(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    version = document.get("contract_version")
    if isinstance(version, str) and SEMVER.match(version):
        if version.split(".")[0] != SUPPORTED_CONTRACT_MAJOR:
            errors.append(f"unsupported_contract_version_major:{version}")

    owner = document.get("owner")
    scenario = document.get("scenario")
    phase = document.get("phase")

    spec: ScenarioSpec | None = None
    if owner == "B":
        if isinstance(scenario, str) and scenario not in B_SCENARIOS:
            errors.append(f"scenario_unknown:{scenario}")
        elif isinstance(scenario, str):
            spec = B_SCENARIOS[scenario]
    elif owner == "A":
        if isinstance(scenario, str) and not scenario.startswith("science:"):
            errors.append("owner_scenario_prefix_mismatch:A")
    elif owner == "C":
        if isinstance(scenario, str) and not scenario.startswith("experience:"):
            errors.append("owner_scenario_prefix_mismatch:C")

    phase_spec: PhaseSpec | None = None
    if spec is not None and isinstance(phase, str):
        phase_spec = spec.phases.get(phase)
        if phase_spec is None:
            errors.append(f"phase_undeclared:{scenario}/{phase}")

    evidence_kind = document.get("evidence_kind")
    if isinstance(phase, str) and evidence_kind in ("fixture", "live"):
        phase_is_fixture = phase == "fixture" or phase.endswith(":fixture")
        phase_is_live = phase == "live" or phase.endswith(":live")
        if phase_is_fixture and evidence_kind != "fixture":
            errors.append(f"phase_evidence_kind_mismatch:{phase}!={evidence_kind}")
        if phase_is_live and evidence_kind != "live":
            errors.append(f"phase_evidence_kind_mismatch:{phase}!={evidence_kind}")
    if phase == "aggregate" and evidence_kind != "aggregate":
        errors.append(f"phase_evidence_kind_mismatch:aggregate!={evidence_kind}")

    operation_class: str | None = None
    target: str | None = None
    if phase_spec is not None:
        operation_class = phase_spec.operation_class
        target = phase_spec.target
    else:
        operation_class = _extension_str(document, "acceptance.operation_class")
        target = _extension_str(document, "acceptance.target")
    if operation_class is not None and operation_class not in OPERATION_CLASSES:
        errors.append(f"operation_class_invalid:{operation_class}")

    errors.extend(_platform_matrix_errors(document, operation_class, target, phase_spec))
    errors.extend(_aggregate_errors(document))
    return errors


# ---------------------------------------------------------------------------
# Artifacts and public validation API
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_errors(document: dict[str, Any], artifact_root: Path | None) -> list[str]:
    errors: list[str] = []
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        return errors
    cwd = document.get("cwd")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        relative = Path(raw_path)
        candidates: list[Path] = []
        if relative.is_absolute():
            candidates.append(relative)
        else:
            if isinstance(cwd, str) and Path(cwd).is_absolute():
                candidates.append(Path(cwd) / relative)
            if artifact_root is not None:
                candidates.append(Path(artifact_root) / relative)
            if isinstance(cwd, str):
                candidates.append(Path(cwd) / relative)
        found = next((candidate for candidate in candidates if candidate.is_file()), None)
        if found is None:
            errors.append(f"artifact_missing:{raw_path}")
            continue
        size_bytes = artifact.get("size_bytes")
        if isinstance(size_bytes, int) and size_bytes != found.stat().st_size:
            errors.append(f"artifact_size_bytes_mismatch:{raw_path}")
        digest = artifact.get("sha256")
        if isinstance(digest, str) and HEX64.match(digest):
            if sha256_file(found) != digest:
                errors.append(f"artifact_sha256_mismatch:{raw_path}")
    return errors


def validate_receipt(
    document: Any,
    *,
    schema: dict[str, Any] | None = None,
    verify_artifacts: bool = False,
    artifact_root: Path | None = None,
) -> list[str]:
    """Validate one receipt.  Returns an empty list when accepted.

    Order is deliberate so cross-field/registry reasons win over generic schema
    noise: normalize aliases -> semantic registry/platform matrix -> schema.
    """

    schema_document = schema if schema is not None else load_schema()
    ref_errors = validate_schema_refs(schema_document, SCHEMA_PATH)
    if ref_errors:
        return ref_errors

    normalized, alias_errors = normalize_receipt(document)
    if alias_errors:
        return alias_errors
    if not isinstance(normalized, dict):
        return ["not_an_object"]

    errors = _semantic_errors(normalized)
    errors.extend(
        _validate_schema(
            normalized,
            schema_document,
            "$",
            schema_document,
            SCHEMA_PATH.parent,
        )
    )
    if verify_artifacts:
        errors.extend(_artifact_errors(normalized, artifact_root))
    return errors


def validate_summary(
    summary_path: Path,
    *,
    verify_artifacts: bool = True,
) -> list[str]:
    """Validate a summary.json or a single receipt, resolving every child path."""

    summary_path = Path(summary_path)
    try:
        document = json.loads(summary_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"summary_missing_run:{summary_path}"]
    except json.JSONDecodeError as exc:
        return [f"json_parse:{summary_path.name}:{exc.msg}"]
    if not isinstance(document, dict):
        return ["not_an_object"]

    schema_name = document.get("schema")
    if schema_name == ACCEPTANCE_SCHEMA:
        return validate_receipt(
            document,
            verify_artifacts=verify_artifacts,
            artifact_root=summary_path.parent,
        )
    if schema_name != SUMMARY_SCHEMA:
        return [f"unknown_summary_schema:{schema_name}"]

    errors: list[str] = []
    runs = document.get("runs")
    if not isinstance(runs, list) or not runs:
        return ["summary_missing_run:<none>"]
    expected_revision = document.get("candidate_revision")
    for index, run in enumerate(runs):
        if not isinstance(run, str) or not run:
            errors.append(f"summary_missing_run:[{index}]")
            continue
        target = (summary_path.parent / run).resolve()
        if not target.is_file():
            errors.append(f"summary_missing_run:{run}")
            continue
        try:
            child = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"json_parse:{run}:{exc.msg}")
            continue
        for reason in validate_receipt(
            child,
            verify_artifacts=verify_artifacts,
            artifact_root=target.parent,
        ):
            errors.append(f"run:{run}:{reason}")
        if (
            isinstance(expected_revision, str)
            and isinstance(child, dict)
            and child.get("candidate_revision") != expected_revision
        ):
            errors.append(f"summary_candidate_revision_mismatch:{run}")

    aggregate = document.get("aggregate")
    if isinstance(aggregate, dict):
        for reason in validate_receipt(
            aggregate,
            verify_artifacts=verify_artifacts,
            artifact_root=summary_path.parent,
        ):
            errors.append(f"aggregate:{reason}")
    return errors


# ---------------------------------------------------------------------------
# Cleanup / timeout accounting
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class CleanupLedger:
    """Tracks everything the harness creates; never pretends cleanup happened."""

    pids: list[int] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    submissions: list[str] = field(default_factory=list)
    started_controller: bool = False
    logs: list[str] = field(default_factory=list)
    timed_out: bool = False

    def track_pid(self, pid: int) -> None:
        if pid not in self.pids:
            self.pids.append(pid)

    def track_task(
        self,
        *,
        job_id: str,
        attempt: int | None = None,
        release_id: str | None = None,
        submission_id: str | None = None,
        entry: str | None = None,
    ) -> None:
        self.tasks.append(
            {
                "job_id": job_id,
                "attempt": attempt,
                "release_id": release_id,
                "submission_id": submission_id,
                "entry": entry,
            }
        )

    def track_submission(self, submission_id: str) -> None:
        if submission_id not in self.submissions:
            self.submissions.append(submission_id)

    def record_log(self, path: str) -> None:
        if path not in self.logs:
            self.logs.append(path)

    def as_record(
        self,
        *,
        scenario: str | None = None,
        phase: str | None = None,
        run_id: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": "acp2.acceptance.cleanup.v1",
            "scenario": scenario,
            "phase": phase,
            "run_id": run_id,
            "timeout_seconds": timeout_seconds,
            "timed_out": self.timed_out,
            "tracked_pids": list(self.pids),
            "tracked_tasks": list(self.tasks),
            "tracked_submissions": list(self.submissions),
            "started_controller": self.started_controller,
            "logs": list(self.logs),
            "cleanup_order": [
                "save logs",
                "cancel only harness-created active tasks through the platform",
                "confirm stop/reconciliation",
                "export native files/manifest/digests",
                "stop adapters",
                "stop the test Controller only if the harness started it",
            ],
        }


def run_handler_with_timeout(
    handler: Any,
    timeout: float,
    ledger: CleanupLedger,
    out_dir: Path,
    *,
    scenario: str | None = None,
    phase: str | None = None,
    run_id: str | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """Run a scenario handler; on timeout write ``timeout-cleanup.json``.

    Real handlers must be subprocess-based so the listed cleanup actions can be
    executed after the daemon thread is abandoned.  This skeleton only needs the
    accounting contract.
    """

    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = handler(ledger)
        except BaseException as exc:  # noqa: BLE001 - scenario boundary reports failures
            box["error"] = exc

    thread = threading.Thread(target=target, name="acp2-scenario", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        ledger.timed_out = True
        record = ledger.as_record(
            scenario=scenario,
            phase=phase,
            run_id=run_id,
            timeout_seconds=timeout,
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "timeout-cleanup.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return EXIT_NOT_VERIFIED, record
    if "error" in box:
        raise box["error"]
    value = box.get("value", EXIT_NOT_VERIFIED)
    return int(value), None


# ---------------------------------------------------------------------------
# Result writing and execution skeleton
# ---------------------------------------------------------------------------


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def harness_revision() -> str:
    return sha256_file(SCRIPT_PATH)


def _evidence_kind_for_phase(phase: str) -> str:
    if phase == "fixture" or phase.endswith(":fixture"):
        return "fixture"
    if phase == "aggregate":
        return "aggregate"
    return "live"


def write_result(
    out_dir: Path,
    *,
    scenario: str,
    phase: str,
    run_id: str,
    started_at: str,
    finished_at: str,
    result: str,
    reason: str | None,
    exit_code: int | None,
    command: list[str],
    evidence_kind: str,
    owner: str = "B",
    assertions: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    refs: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    extensions: dict[str, Any] | None = None,
    candidate_revision: str | None = None,
    server_revision: str | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    reference_values = {key: None for key in PLATFORM_REF_KEYS}
    if refs:
        reference_values.update(refs)
    if candidate_revision is None:
        candidate_revision = os.environ.get("ACP_CANDIDATE_REVISION", BASELINE_CANDIDATE_REVISION)
    if server_revision is None:
        server_revision = SERVER_CANDIDATE_REVISION
    document: dict[str, Any] = {
        "schema": ACCEPTANCE_SCHEMA,
        "schema_dialect": SCHEMA_DIALECT,
        "contract_version": CONTRACT_VERSION,
        "owner": owner,
        "scenario": scenario,
        "phase": phase,
        "run_id": run_id,
        "candidate_revision": candidate_revision,
        "candidate": {
            "server_revision": server_revision,
            "client_revision": None,
            "harness_revision": harness_revision(),
        },
        "result": result,
        "scope": "IN_SCOPE",
        "reason": reason,
        "command": command,
        "cwd": str(Path.cwd()),
        "exit_code": exit_code,
        "started_at": started_at,
        "finished_at": finished_at,
        "assertions": assertions or [],
        "artifacts": artifacts or [],
        "job_id": reference_values["job_id"],
        "operation_id": reference_values["operation_id"],
        "attempt": reference_values["attempt"],
        "release_id": reference_values["release_id"],
        "submission_id": reference_values["submission_id"],
        "manifest_generation": reference_values["manifest_generation"],
        "negotiated_protocol": reference_values["negotiated_protocol"],
        "usage": usage,
        "evidence_kind": evidence_kind,
        "extensions": extensions or {},
    }
    run_name = f"{_safe_name(scenario)}-{_safe_name(phase)}-{run_id}.json"
    run_path = out_dir / run_name
    run_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary_path = out_dir / "summary.json"
    runs: list[str] = []
    if summary_path.is_file():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        raw_runs = existing.get("runs") if isinstance(existing, dict) else None
        if isinstance(raw_runs, list):
            runs = [item for item in raw_runs if isinstance(item, str)]
    if run_name not in runs:
        runs.append(run_name)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "schema_dialect": SCHEMA_DIALECT,
        "contract_version": CONTRACT_VERSION,
        "candidate_revision": document["candidate_revision"],
        "generated_at": finished_at,
        "runs": runs,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return run_path, summary_path


def _b01_stub_handler(spec: ScenarioSpec) -> Any:
    def handler(_ledger: CleanupLedger) -> int:
        return EXIT_NOT_VERIFIED

    return handler


def controller_reachable(controller: str) -> bool:
    parsed = urlparse(controller)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _http_json(url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def _http_json_post(url: str, payload: dict[str, Any], timeout: float) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def _http_error_body(url: str, timeout: float) -> tuple[int, Any]:
    try:
        _http_json(url, timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = raw.decode("utf-8", errors="replace")
        return int(exc.code), body
    return 200, None


def _controller_task_count(controller: str, timeout: float) -> int:
    payload = _http_json(controller.rstrip("/") + "/api/v1/jobs?limit=1000", timeout)
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    return len(jobs) if isinstance(jobs, list) else 0


def resolve_candidate_identity(
    evidence_kind: str, controller: str | None, timeout: float
) -> tuple[str, str, str | None]:
    """Bind live receipt identity to the running Controller's health revision.

    ``entry-readonly`` and every other live scenario take both the top-level
    ``candidate_revision`` and ``candidate.server_revision`` from
    ``GET /api/health``.  Fixture/aggregate evidence keeps the frozen baseline
    defaults.  If ``ACP_CANDIDATE_REVISION`` is exported and disagrees with the
    Controller, the caller must FAIL the run with the returned named reason
    ``candidate_revision_mismatch:<env_value>!=<health_value>``.
    """

    env_revision = os.environ.get("ACP_CANDIDATE_REVISION")
    if evidence_kind != "live":
        return (
            env_revision or BASELINE_CANDIDATE_REVISION,
            SERVER_CANDIDATE_REVISION,
            None,
        )
    health_revision: str | None = None
    if controller and controller_reachable(controller):
        try:
            health = _http_json(controller.rstrip("/") + "/api/health", timeout)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            health = None
        if isinstance(health, dict):
            value = health.get("candidate_revision")
            if isinstance(value, str) and value:
                health_revision = value
    if health_revision is None:
        # A live prerequisite that cannot reach the Controller (for example
        # ``controller_unreachable``) has no health identity to bind to; keeps the
        # declared fallback so the NOT_VERIFIED receipt still validates.
        return (
            env_revision or BASELINE_CANDIDATE_REVISION,
            SERVER_CANDIDATE_REVISION,
            None,
        )
    if env_revision is not None and env_revision != health_revision:
        return (
            health_revision,
            health_revision,
            f"candidate_revision_mismatch:{env_revision}!={health_revision}",
        )
    return health_revision, health_revision, None


B07_FIXTURE_FIELDS = (
    "fixture_id",
    "input_digest",
    "backend",
    "request_owner",
    "budget",
    "max_queue_wait_seconds",
    "max_run_seconds",
    "max_prepare_seconds",
    "cleanup_seconds",
    "margin_seconds",
    "scenario_timeout_seconds",
    "task",
    "attempt",
    "release",
    "status",
    "evidence_owner",
    "reusable_by",
)
B07_P0_ROWS = (
    "h2_candidate_record",
    "local_orca_binary_and_license",
    "local_scientific_software_inventory",
    "acp_doctor",
    "local_run_root",
    "fixture_registry",
    "controller_health_contract",
    "local_controller_two_adapter_topology",
    "lsf_client_queue_ssh",
    "c_dsh_real_handshake",
    "c_chromium_launch",
    "c_route_json_diagnostic",
    "c_env_03_node_dsh",
    "c_env_05_dsh_profile_model",
    "c_env_08_network",
)


def _b07_bases(out_dir: Path) -> list[Path]:
    """Candidate evidence bases for the B07 environment records."""
    bases: list[Path] = [out_dir.resolve().parent]
    evidence_root = os.environ.get("ACP2_EVIDENCE")
    if evidence_root:
        bases.append(Path(evidence_root) / "B")
    bases.append(REPO_ROOT / "reports" / "acp2_implementation" / "bb45a46" / "B")
    unique: list[Path] = []
    for base in bases:
        if base not in unique:
            unique.append(base)
    return unique


def _b07_pick(bases: list[Path], relative: str) -> Path | None:
    for base in bases:
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return None


def _b07_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable_json:{path}:{exc}"
    if not isinstance(document, dict):
        return None, f"not_a_json_object:{path}"
    return document, None


def _b07_repo_path(relative: str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else REPO_ROOT / path


def _run_env_runtime(
    out_dir: Path, controller: str | None, timeout: float
) -> tuple[int, list[dict[str, Any]], dict[str, Any], str | None]:
    """Verify the real B07 environment record; never promote a missing fact.

    This is a discovery scenario.  It returns PASS only when the H2 candidate
    record, the Local ORCA execution probe, the doctor raw log, the LSF
    NOT_VERIFIED prerequisite and the fixture registry all exist and agree.
    C-owned DSH/browser rows are never signed here.
    """
    bases = _b07_bases(out_dir)
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "discover",
        "acceptance.target": "local",
        "b07.evidence_bases": [str(base) for base in bases],
    }

    def add(name: str, ok: bool, detail: str, *, not_verified: bool = False) -> None:
        assertions.append(
            {
                "name": name,
                "status": "PASS" if ok else ("NOT_VERIFIED" if not_verified else "FAIL"),
                "detail": detail,
            }
        )

    env_path = _b07_pick(bases, "environment/b07-environment.json")
    h2_path = _b07_pick(bases, "environment/h2-candidate.json")
    fixture_path = _b07_pick(bases, "fixture-runs.json")
    p0_path = _b07_pick(bases, "B07/p0-matrix.json") or _b07_pick(bases, "p0-matrix.json")
    missing = [
        name
        for name, path in (
            ("environment/b07-environment.json", env_path),
            ("environment/h2-candidate.json", h2_path),
            ("fixture-runs.json", fixture_path),
            ("B07/p0-matrix.json", p0_path),
        )
        if path is None
    ]
    if missing:
        extensions["b07.missing_records"] = missing
        add("b07_records_present", False, "missing:" + ",".join(missing), not_verified=True)
        reason = "environment_record_missing:" + ",".join(missing)
        return EXIT_NOT_VERIFIED, assertions, extensions, reason

    environment, env_error = _b07_json(env_path)
    candidate, candidate_error = _b07_json(h2_path)
    fixture_registry, fixture_error = _b07_json(fixture_path)
    p0_matrix, p0_error = _b07_json(p0_path)
    load_errors = [
        error
        for error in (env_error, candidate_error, fixture_error, p0_error)
        if error is not None
    ]
    if load_errors:
        add("b07_records_parse", False, "; ".join(load_errors))
        reason = "invalid_environment_record:" + "; ".join(load_errors)
        return EXIT_FAIL, assertions, extensions, reason
    assert environment is not None and candidate is not None
    assert fixture_registry is not None and p0_matrix is not None

    add(
        "environment_record_schema",
        environment.get("schema") == "acp2.b07.environment.v1",
        f"schema={environment.get('schema')}",
    )
    software = environment.get("software") if isinstance(environment.get("software"), dict) else {}
    orca = software.get("orca") if isinstance(software.get("orca"), dict) else {}
    probe = orca.get("probe") if isinstance(orca.get("probe"), dict) else {}
    expected_orca = "/home/<user>/orca611/orca"
    orca_path = str(orca.get("path") or "")
    orca_ok = (
        orca_path == expected_orca
        and orca_path != "/usr/bin/orca"
        and orca.get("elf_magic_verified") is True
        and bool(HEX64.match(str(orca.get("sha256") or "")))
        and Path(orca_path).is_file()
        and os.access(orca_path, os.X_OK)
    )
    add("local_orca_real_binary", orca_ok, f"path={orca_path} sha256={orca.get('sha256')}")
    add(
        "forbidden_usr_bin_orca",
        bool(environment.get("forbidden_binary"))
        and environment["forbidden_binary"].get("use_as_qc_orca") is False
        and orca_path != "/usr/bin/orca",
        "record states /usr/bin/orca is the GNOME screen reader and is never QC ORCA",
    )

    probe_stdout = _b07_repo_path(str(probe.get("stdout") or ""))
    probe_input = _b07_repo_path(str(probe.get("input") or ""))
    probe_ok = (
        probe.get("status") == "PASS"
        and probe.get("exit_code") == 0
        and "ORCA TERMINATED NORMALLY" in str(probe.get("termination_line") or "")
        and isinstance(probe.get("final_single_point_energy_hartree"), (int, float))
        and isinstance(probe.get("wall_clock_s"), (int, float))
        and float(probe.get("wall_clock_s")) < 10.0
        and probe_stdout.is_file()
        and sha256_file(probe_stdout) == probe.get("stdout_sha256")
        and probe_input.is_file()
        and sha256_file(probe_input) == probe.get("input_sha256")
    )
    add(
        "local_orca_minimal_probe",
        probe_ok,
        f"status={probe.get('status')} termination={probe.get('termination_line')} "
        f"energy={probe.get('final_single_point_energy_hartree')} "
        f"wall_clock_s={probe.get('wall_clock_s')}",
    )

    software_ok = all(
        name in software
        and isinstance(software[name], dict)
        and bool(software[name].get("version"))
        and bool(HEX64.match(str(software[name].get("sha256") or "")))
        and Path(str(software[name].get("path") or "")).is_file()
        for name in ("orca", "xtb", "crest", "censo", "shermo")
    )
    add(
        "local_scientific_software_inventory",
        software_ok,
        "orca/xtb/crest/censo/shermo each have a version, sha256 and real path",
    )

    host = environment.get("host") if isinstance(environment.get("host"), dict) else {}
    run_root = str(host.get("run_root") or "")
    host_ok = (
        isinstance(host.get("cores"), int)
        and int(host["cores"]) > 0
        and isinstance(host.get("memory_total_bytes"), int)
        and int(host["memory_total_bytes"]) > 0
        and host.get("run_root_fstype") == "xfs"
        and run_root == "<acp2-dev>/data/b/run-root"
        and host.get("production_run_root_touched") is False
    )
    add(
        "local_run_root",
        host_ok,
        f"run_root={run_root} fstype={host.get('run_root_fstype')} "
        f"cores={host.get('cores')} memory={host.get('memory_total_bytes')}",
    )

    doctor = environment.get("doctor") if isinstance(environment.get("doctor"), dict) else {}
    doctor_log = _b07_repo_path(str(doctor.get("raw_log") or ""))
    doctor_text = (
        doctor_log.read_text(encoding="utf-8", errors="replace") if doctor_log.is_file() else ""
    )
    doctor_ok = (
        doctor.get("exit_code") == 0
        and doctor_log.is_file()
        and "Local doctor finished: all checks passed" in doctor_text
        and isinstance(doctor.get("gaps"), list)
        and bool(doctor["gaps"])
    )
    add(
        "acp_doctor",
        doctor_ok,
        f"exit={doctor.get('exit_code')} summary={doctor.get('summary')} "
        f"gaps={len(doctor.get('gaps') or [])}",
    )

    lsf = environment.get("lsf") if isinstance(environment.get("lsf"), dict) else {}
    lsf_log = _b07_repo_path(str(lsf.get("raw_log") or ""))
    prerequisite = str(lsf.get("prerequisite") or "")
    lsf_ok = (
        lsf.get("status") == "NOT_VERIFIED"
        and lsf.get("remote_job_attempted") is False
        and "LSF_ENVDIR" in prerequisite
        and "bsub" in prerequisite
        and lsf_log.is_file()
    )
    add(
        "lsf_gap_honestly_recorded",
        lsf_ok,
        f"status={lsf.get('status')} remote_job_attempted={lsf.get('remote_job_attempted')} "
        f"prerequisite={prerequisite[:160]}",
    )

    fixtures = fixture_registry.get("fixtures")
    fixture_rows = fixtures if isinstance(fixtures, list) else []
    fixture_ids = [row.get("fixture_id") for row in fixture_rows if isinstance(row, dict)]
    fields_ok = bool(fixture_rows) and all(
        isinstance(row, dict) and all(field in row for field in B07_FIXTURE_FIELDS)
        for row in fixture_rows
    )
    arithmetic_ok = True
    for row in fixture_rows:
        if not isinstance(row, dict):
            arithmetic_ok = False
            continue
        maxima = [
            row.get("max_queue_wait_seconds"),
            row.get("max_run_seconds"),
            row.get("max_prepare_seconds"),
            row.get("cleanup_seconds"),
            row.get("margin_seconds"),
        ]
        if row.get("status") == "planned":
            arithmetic_ok = arithmetic_ok and all(value is None for value in maxima)
            arithmetic_ok = arithmetic_ok and row.get("scenario_timeout_seconds") is None
            continue
        if not all(isinstance(value, int) and value >= 0 for value in maxima):
            arithmetic_ok = False
            continue
        arithmetic_ok = arithmetic_ok and row.get("scenario_timeout_seconds") == sum(maxima)
    expensive = [
        (row.get("backend"), row.get("input_digest"))
        for row in fixture_rows
        if isinstance(row, dict)
        and row.get("status") != "planned"
        and row.get("input_digest") is not None
    ]
    fixture_ok = (
        fixture_registry.get("schema") == "acp2.fixture-runs.v1"
        and fields_ok
        and len(fixture_ids) == len(set(fixture_ids))
        and arithmetic_ok
        and len(expensive) == len(set(expensive))
        and fixture_registry.get("lsf_timeout_seconds")
        == next(
            (
                row.get("scenario_timeout_seconds")
                for row in fixture_rows
                if isinstance(row, dict) and row.get("fixture_id") == "fx-lsf-singlepoint-v1"
            ),
            None,
        )
    )
    add(
        "fixture_registry",
        fixture_ok,
        f"rows={len(fixture_rows)} unique={len(set(fixture_ids))} arithmetic_ok={arithmetic_ok} "
        f"lsf_timeout_seconds={fixture_registry.get('lsf_timeout_seconds')}",
    )

    h2_ok = (
        bool(re.fullmatch(r"[0-9a-f]{40}", str(candidate.get("server_revision") or "")))
        and (
            bool(re.fullmatch(r"[0-9a-f]{40}", str(candidate.get("client_revision") or "")))
            or candidate.get("client_revision") == "NOT_VERIFIED"
        )
        and bool(HEX64.match(str(candidate.get("harness_revision") or "")))
        and bool(HEX64.match(str(candidate.get("contract_digest") or "")))
        and bool(HEX64.match(str(candidate.get("env_digest") or "")))
        and isinstance(candidate.get("tested_matrix"), dict)
    )
    add(
        "h2_candidate_record",
        h2_ok,
        f"server={candidate.get('server_revision')} client={candidate.get('client_revision')} "
        f"harness={candidate.get('harness_revision')} contract={candidate.get('contract_digest')} "
        f"env={candidate.get('env_digest')}",
    )

    raw_candidate_record = environment.get("candidate")
    candidate_record = raw_candidate_record if isinstance(raw_candidate_record, dict) else {}
    env_digest_path = (
        Path(os.environ.get("ACP2_DATA_ROOT", "<acp2-dev>/data/b"))
        / "acceptance-env.sh"
    )
    env_digest_ok = (
        candidate_record.get("server_revision") == candidate.get("server_revision")
        and env_digest_path.is_file()
        and sha256_file(env_digest_path) == candidate.get("env_digest")
    )
    add(
        "h2_candidate_environment_binding",
        env_digest_ok,
        f"env_server={candidate_record.get('server_revision')} "
        f"env_digest={candidate.get('env_digest')} file={env_digest_path}",
    )

    p0_rows = p0_matrix.get("rows")
    p0_row_list = p0_rows if isinstance(p0_rows, list) else []
    p0_ids = {row.get("row_id") for row in p0_row_list if isinstance(row, dict)}
    p0_results_ok = all(
        isinstance(row, dict)
        and row.get("result") in RESULT_VALUES
        and isinstance(row.get("reason"), str)
        and bool(row.get("raw_log"))
        for row in p0_row_list
    )
    p0_ok = (
        p0_matrix.get("schema") == "acp2.b07.p0-matrix.v1"
        and p0_matrix.get("overall_status") in {"OPEN", "CLOSED"}
        and p0_results_ok
        and set(B07_P0_ROWS).issubset(p0_ids)
    )
    add(
        "p0_evidence_matrix",
        p0_ok,
        f"overall={p0_matrix.get('overall_status')} rows={len(p0_row_list)} "
        f"missing={sorted(set(B07_P0_ROWS) - p0_ids)}",
    )

    controller_binding = None
    if controller:
        if not controller_reachable(controller):
            add(
                "controller_health_binding",
                False,
                f"controller_unreachable:{controller}",
                not_verified=True,
            )
            controller_binding = "unreachable"
        else:
            try:
                before = _controller_task_count(controller, timeout)
                health = _http_json(controller.rstrip("/") + "/api/health", timeout)
                after = _controller_task_count(controller, timeout)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
                add("controller_health_binding", False, f"request_failed:{exc}")
                controller_binding = "failed"
            else:
                health_ok = (
                    isinstance(health, dict)
                    and health.get("candidate_revision") == candidate.get("server_revision")
                    and health.get("contract_digest") == candidate.get("contract_digest")
                    and before == after
                )
                add(
                    "controller_health_binding",
                    health_ok,
                    f"health_candidate="
                    f"{health.get('candidate_revision') if isinstance(health, dict) else None} "
                    f"health_contract="
                    f"{health.get('contract_digest') if isinstance(health, dict) else None} "
                    f"tasks_before={before} tasks_after={after}",
                )
                controller_binding = {
                    "health": health,
                    "task_count_before": before,
                    "task_count_after": after,
                }

    failed = [item["name"] for item in assertions if item["status"] == "FAIL"]
    not_verified = [item["name"] for item in assertions if item["status"] == "NOT_VERIFIED"]
    extensions.update(
        {
            "b07.environment_record": str(env_path),
            "b07.h2_candidate_record": str(h2_path),
            "b07.fixture_runs": str(fixture_path),
            "b07.p0_matrix": str(p0_path),
            "b07.orca_probe": str(probe_stdout),
            "b07.doctor_log": str(doctor_log),
            "b07.lsf_log": str(lsf_log),
            "b07.fixture_rows": len(fixture_rows),
            "b07.lsf_timeout_seconds": fixture_registry.get("lsf_timeout_seconds"),
            "b07.controller_binding": controller_binding,
        }
    )
    if failed:
        return EXIT_FAIL, assertions, extensions, "assertion_failed:" + ",".join(failed)
    if not_verified:
        return (
            EXIT_NOT_VERIFIED,
            assertions,
            extensions,
            "environment_not_verified:" + ",".join(not_verified),
        )
    return EXIT_PASS, assertions, extensions, None


def _run_entry_readonly(
    controller: str, timeout: float
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """Live read-only probe: capabilities + health, task count unchanged."""
    base = controller.rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "discover",
        "acceptance.target": "local",
        "b04a.controller": base,
    }
    preflight_request = {
        "schema_version": 1,
        "workflow": "singlepoint",
        "input": {"xyz_text": "2\nH2\nH 0 0 0\nH 0 0 0.74\n"},
        "method": {"method": "r2SCAN-3c", "charge": 0, "multiplicity": 1},
    }
    try:
        before = _controller_task_count(base, timeout)
        health = _http_json(base + "/api/health", timeout)
        capabilities = _http_json(base + "/api/capabilities", timeout)
        preflight = _http_json_post(base + "/api/v1/preflight", preflight_request, timeout)
        missing_status, missing_body = _http_error_body(
            base + "/api/v1/operations/op_b06_missing", timeout
        )
        after = _controller_task_count(base, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        extensions["b04a.entry_readonly"] = f"request_failed: {exc}"
        assertions.append({"name": "controller_commands", "status": "FAIL", "detail": str(exc)})
        return EXIT_FAIL, assertions, extensions

    tools = capabilities.get("tools") if isinstance(capabilities, dict) else None
    health_ok = (
        isinstance(health, dict)
        and health.get("schema_version") == 1
        and health.get("status") == "ok"
        and isinstance(health.get("candidate_revision"), str)
        and bool(health.get("candidate_revision"))
        and isinstance(health.get("contract_version"), str)
        and bool(health.get("contract_version"))
        and isinstance(health.get("contract_digest"), str)
        and bool(HEX64.match(health.get("contract_digest", "")))
    )
    capabilities_ok = (
        isinstance(capabilities, dict)
        and capabilities.get("schema_version") == 1
        and isinstance(capabilities.get("workflows"), list)
        and bool(capabilities.get("workflows"))
        and isinstance(capabilities.get("backends"), list)
        and isinstance(capabilities.get("limits"), dict)
        and isinstance(tools, list)
        and set(tools) == set(MCP_TOOLS)
    )
    preflight_ok = (
        isinstance(preflight, dict)
        and preflight.get("schema_version") == 1
        and preflight.get("would_create_task") is False
        and preflight.get("would_reserve") is False
        and (
            preflight.get("plan_digest") is None
            or str(preflight.get("plan_digest")).startswith("sha256:")
        )
    )
    missing_detail = missing_body.get("detail") if isinstance(missing_body, dict) else None
    missing_ok = (
        missing_status == 404
        and isinstance(missing_detail, dict)
        and missing_detail.get("code") == "resource_not_ready"
        and "not_prepared" in str(missing_detail.get("message", ""))
    )
    assertions.extend(
        [
            {
                "name": "health_entry_equivalence_facts",
                "status": "PASS" if health_ok else "FAIL",
                "detail": "candidate_revision/contract_version/contract_digest present",
            },
            {
                "name": "capabilities_workflows_backends_limits_tools",
                "status": "PASS" if capabilities_ok else "FAIL",
                "detail": "workflows/backends/limits present and exactly the eight P1 tools",
            },
            {
                "name": "preflight_zero_side_effects",
                "status": "PASS" if preflight_ok else "FAIL",
                "detail": "would_create_task=false would_reserve=false; plan_digest is a sha256",
            },
            {
                "name": "missing_operation_is_not_prepared",
                "status": "PASS" if missing_ok else "FAIL",
                "detail": (
                    f"status={missing_status} code={missing_detail and missing_detail.get('code')}"
                ),
            },
            {
                "name": "task_count_unchanged",
                "status": "PASS" if before == after else "FAIL",
                "detail": f"before={before} after={after}",
            },
        ]
    )
    extensions.update(
        {
            "b04a.task_count_before": before,
            "b04a.task_count_after": after,
            "b04a.workflows": len(capabilities.get("workflows") or []),
            "b04a.tools": len(tools) if isinstance(tools, list) else 0,
            "b04a.candidate_revision": health.get("candidate_revision"),
            "b04a.contract_version": health.get("contract_version"),
            "b04a.contract_digest": health.get("contract_digest"),
            "b06.preflight_digest": preflight.get("plan_digest"),
            "b06.missing_operation_status": missing_status,
        }
    )
    passed = health_ok and capabilities_ok and preflight_ok and missing_ok and before == after
    return (EXIT_PASS if passed else EXIT_FAIL), assertions, extensions


class _McpStdioProbe:
    """Synchronous newline-delimited JSON-RPC probe for one real adapter."""

    def __init__(self, launch: dict[str, Any], timeout: float) -> None:
        self._timeout = timeout
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._stderr: list[str] = []
        command = launch.get("command")
        argv = launch.get("args")
        cwd = launch.get("cwd")
        env = launch.get("env") or {}
        if not isinstance(command, str) or not Path(command).is_absolute():
            raise ValueError("command must be an absolute executable path")
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            raise ValueError("args must be a JSON list of strings")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise ValueError("cwd must be an absolute directory")
        if not isinstance(env, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in env.items()
        ):
            raise ValueError("env must map strings to strings")
        merged_env = {**os.environ, **env}
        self.proc = subprocess.Popen(
            [command, *argv],
            cwd=cwd,
            env=merged_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._err_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._err_reader.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                self._queue.put(None)
                return
            self._queue.put(line)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr.append(line.decode("utf-8", errors="replace"))

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self._timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no response for {method}")
            try:
                raw = self._queue.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"no response for {method}") from None
            if raw is None:
                raise RuntimeError(f"adapter closed stdout during {method}")
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise ValueError("not a JSON-RPC 2.0 object")
            if "id" not in message and "method" in message:
                continue
            if message.get("id") != request_id:
                raise ValueError("unexpected response id")
            if "error" in message:
                raise RuntimeError(f"{method}: {message['error']}")
            return message["result"]

    def close(self) -> int:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                return self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=5)

    def stderr_text(self) -> str:
        return "".join(self._stderr)


def _run_mcp_stdio(
    controller: str, launch_path: str, timeout: float
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """Launch the real stdio adapter and prove every call reached the Controller."""
    base = controller.rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "discover",
        "acceptance.target": "local",
        "b06.mcp_launch": str(launch_path),
    }
    probe: _McpStdioProbe | None = None
    try:
        launch = json.loads(Path(launch_path).read_text(encoding="utf-8"))
        if not isinstance(launch, dict):
            raise ValueError("launch descriptor must be a JSON object")
        before = _controller_task_count(base, timeout)
        probe = _McpStdioProbe(launch, timeout)
        initialized = probe.request(
            1,
            "initialize",
            {
                "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                "capabilities": {},
                "clientInfo": {"name": "acp2-b06-probe", "version": "1"},
            },
        )
        probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = probe.request(2, "tools/list", {})
        tools = listed.get("tools") if isinstance(listed, dict) else None
        tool_names = {tool.get("name") for tool in tools or [] if isinstance(tool, dict)}
        capabilities = probe.request(
            3,
            "tools/call",
            {"name": "acp_capabilities", "arguments": {}},
        )
        preflight = probe.request(
            4,
            "tools/call",
            {
                "name": "acp_preflight",
                "arguments": {
                    "workflow": "singlepoint",
                    "input": {"xyz_text": "2\nH2\nH 0 0 0\nH 0 0 0.74\n"},
                    "method": {"method": "r2SCAN-3c", "charge": 0, "multiplicity": 1},
                },
            },
        )
        status_missing = probe.request(
            5,
            "tools/call",
            {"name": "acp_status", "arguments": {"reference": "b06-missing-job"}},
        )
        read_missing = probe.request(
            6,
            "tools/call",
            {
                "name": "acp_read_artifact",
                "arguments": {
                    "artifact_ref": {
                        "task_id": "b06-missing-job",
                        "attempt": 1,
                        "artifact_id": "catalog",
                        "generation": 1,
                        "digest": "sha256:" + "0" * 64,
                    }
                },
            },
        )
        analyze_implicit = probe.request(
            7,
            "tools/call",
            {
                "name": "acp_analyze",
                "arguments": {
                    "analysis_id": "vibration.analyze",
                    "input_refs": [{"task_id": "b06-missing-job"}],
                    "explicit": False,
                },
            },
        )
        analyze_unsupported = probe.request(
            8,
            "tools/call",
            {
                "name": "acp_analyze",
                "arguments": {
                    "analysis_id": "vibration.analyze",
                    "input_refs": [{"task_id": "b06-missing-job"}],
                    "explicit": True,
                },
            },
        )
        after = _controller_task_count(base, timeout)
        exit_code = probe.close()
        probe = None
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
    ) as exc:
        extensions["b06.mcp_stdio"] = f"probe_failed: {exc}"
        assertions.append({"name": "mcp_stdio_probe", "status": "FAIL", "detail": str(exc)})
        if probe is not None:
            probe.close()
        return EXIT_FAIL, assertions, extensions

    extension = (initialized.get("extensions") or {}).get("acp.research.v1") or {}
    initialized_ok = (
        initialized.get("protocolVersion") == launch.get("protocol_version", "2025-06-18")
        and extension.get("negotiated") is True
        and extension.get("version") == "1.1.0"
    )
    tools_ok = tool_names == set(MCP_TOOLS)
    capabilities_data = capabilities.get("structuredContent", {})
    capabilities_ok = (
        capabilities.get("isError") is False
        and capabilities_data.get("status") == "ok"
        and set(capabilities_data.get("data", {}).get("tools") or []) == set(MCP_TOOLS)
    )
    preflight_data = preflight.get("structuredContent", {})
    preflight_ok = (
        preflight.get("isError") is False
        and preflight_data.get("dry_run") is True
        and preflight_data.get("data", {}).get("executed") is False
        and preflight_data.get("data", {}).get("estimate", {}).get("would_create_task") is False
    )
    status_ok = (
        status_missing.get("isError") is True
        and status_missing.get("structuredContent", {}).get("error", {}).get("code")
        == "resource_not_ready"
    )
    read_ok = (
        read_missing.get("isError") is True
        and read_missing.get("structuredContent", {}).get("error", {}).get("code")
        == "resource_not_ready"
    )
    analyze_implicit_ok = (
        analyze_implicit.get("isError") is True
        and analyze_implicit.get("structuredContent", {}).get("error", {}).get("code")
        == "input_invalid"
    )
    analyze_unsupported_ok = analyze_unsupported.get("isError") is True and analyze_unsupported.get(
        "structuredContent", {}
    ).get("error", {}).get("code") in {"unsupported_capability", "resource_not_ready"}
    assertions.extend(
        [
            {
                "name": "initialize_research_extension",
                "status": "PASS" if initialized_ok else "FAIL",
                "detail": "acp.research.v1 negotiated with version 1.1.0",
            },
            {
                "name": "tools_allowlist",
                "status": "PASS" if tools_ok else "FAIL",
                "detail": f"tools={sorted(tool_names)}",
            },
            {
                "name": "capabilities_via_controller",
                "status": "PASS" if capabilities_ok else "FAIL",
                "detail": "acp_capabilities returned status=ok with the eight tools",
            },
            {
                "name": "preflight_zero_side_effects",
                "status": "PASS" if preflight_ok else "FAIL",
                "detail": "dry_run=true executed=false would_create_task=false",
            },
            {
                "name": "status_missing_is_not_prepared",
                "status": "PASS" if status_ok else "FAIL",
                "detail": "acp_status returned resource_not_ready",
            },
            {
                "name": "read_before_prepare_is_not_prepared",
                "status": "PASS" if read_ok else "FAIL",
                "detail": "acp_read_artifact returned resource_not_ready",
            },
            {
                "name": "analyze_requires_explicit",
                "status": "PASS" if analyze_implicit_ok else "FAIL",
                "detail": "implicit analyze returned input_invalid",
            },
            {
                "name": "analyze_registry_gated",
                "status": "PASS" if analyze_unsupported_ok else "FAIL",
                "detail": "unregistered analysis did not run science",
            },
            {
                "name": "task_count_unchanged",
                "status": "PASS" if before == after else "FAIL",
                "detail": f"before={before} after={after}",
            },
        ]
    )
    extensions.update(
        {
            "b06.task_count_before": before,
            "b06.task_count_after": after,
            "b06.adapter_exit_code": exit_code,
            "b06.tools": sorted(tool_names),
            "b06.protocol_version": initialized.get("protocolVersion"),
        }
    )
    passed = (
        initialized_ok
        and tools_ok
        and capabilities_ok
        and preflight_ok
        and status_ok
        and read_ok
        and analyze_implicit_ok
        and analyze_unsupported_ok
        and before == after
        and exit_code == 0
    )
    return (EXIT_PASS if passed else EXIT_FAIL), assertions, extensions


def _dry_run_out(args: argparse.Namespace, scenario: str, phase: str) -> str:
    if args.out:
        return str(Path(args.out).resolve())
    return f"/tmp/acp2-{_safe_name(scenario)}-{_safe_name(phase)}"


def dry_run_commands(
    spec: ScenarioSpec,
    phase_name: str,
    phase: PhaseSpec,
    args: argparse.Namespace,
) -> list[str]:
    """Return exact, runnable-as-written commands (no literal ellipsis)."""

    out = _dry_run_out(args, spec.scenario, phase_name)
    controller = args.controller or "http://127.0.0.1:8765"
    fixture = args.fixture or f"/tmp/acp2-fixtures/{_safe_name(spec.scenario)}.json"
    launch = args.launch or "/tmp/acp2-launch/mcp-launch.json"

    commands: list[str] = []
    if spec.scenario == "env-runtime":
        commands.append(f"{shlex.quote(sys.executable)} -m acp doctor")
    elif spec.scenario == "entry-readonly":
        commands.append(f"curl -fsS {shlex.quote(controller + '/api/health')}")
        commands.append(f"curl -fsS {shlex.quote(controller + '/api/capabilities')}")
        commands.append(f"curl -fsS {shlex.quote(controller + '/api/v1/jobs?limit=1000')}")
    elif spec.scenario == "mcp-stdio":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp mcp serve --transport stdio "
            f"--controller {shlex.quote(controller)}"
        )
        commands.append(f"{shlex.quote(sys.executable)} -m json.tool {shlex.quote(launch)}")
    elif spec.scenario == "submit-replay":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task --controller "
            f"{shlex.quote(controller)} ingest {shlex.quote(fixture)}"
        )
    elif spec.scenario in ("local-singlepoint", "lsf-singlepoint", "three-entry"):
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task --controller "
            f"{shlex.quote(controller)} plan {shlex.quote(fixture)}"
        )
    elif spec.scenario == "artifact":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task --controller "
            f"{shlex.quote(controller)} export {shlex.quote(fixture)}"
        )
    elif spec.scenario == "control-recovery":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task --controller "
            f"{shlex.quote(controller)} query {shlex.quote(fixture)}"
        )
    elif spec.scenario == "approval-budget":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task --controller "
            f"{shlex.quote(controller)} plan {shlex.quote(fixture)}"
        )
    elif spec.scenario == "da-runtime":
        commands.append(
            f"{shlex.quote(sys.executable)} scripts/acceptance/acp2_runtime.py "
            f"--scenario da-runtime --phase {phase_name} --controller "
            f"{shlex.quote(controller)} --fixture {shlex.quote(fixture)} --out {shlex.quote(out)}"
        )
    elif spec.scenario == "infra-plugin":
        commands.append(
            f"{shlex.quote(sys.executable)} scripts/build_release.py --check {shlex.quote(fixture)}"
        )
    elif spec.scenario == "multitenant":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task --controller "
            f"{shlex.quote(controller)} query {shlex.quote(fixture)}"
        )
    elif spec.scenario == "migration":
        commands.append(f"{shlex.quote(sys.executable)} -m acp task export {shlex.quote(fixture)}")
    elif spec.scenario == "package-install":
        commands.append(f"{shlex.quote(sys.executable)} -m pip install -e .")

    harness_command = [
        sys.executable,
        str(SCRIPT_PATH),
        "--scenario",
        spec.scenario,
        "--phase",
        phase_name,
        "--out",
        out,
    ]
    if phase.controller_required:
        harness_command += ["--controller", controller]
    if "fixture" in phase.required_args:
        harness_command += ["--fixture", fixture]
    if "launch" in phase.required_args:
        harness_command += ["--launch", launch]
    commands.append(" ".join(shlex.quote(part) for part in harness_command))
    return commands


def _write_prerequisite_result(
    spec: ScenarioSpec,
    phase_name: str,
    phase: PhaseSpec,
    args: argparse.Namespace,
    reason: str,
) -> int:
    started_at = _utcnow()
    finished_at = _utcnow()
    run_id = _new_run_id()
    timeout = args.timeout if args.timeout is not None else phase.timeout
    candidate_revision, server_revision, identity_error = resolve_candidate_identity(
        _evidence_kind_for_phase(phase_name), args.controller, timeout
    )
    result = "NOT_VERIFIED"
    exit_code = EXIT_NOT_VERIFIED
    reason_value = reason
    if identity_error is not None:
        result = "FAIL"
        exit_code = EXIT_FAIL
        reason_value = identity_error
    run_path, _ = write_result(
        Path(args.out),
        scenario=spec.scenario,
        phase=phase_name,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        reason=reason_value,
        exit_code=exit_code,
        command=dry_run_commands(spec, phase_name, phase, args),
        evidence_kind=_evidence_kind_for_phase(phase_name),
        assertions=[{"name": "prerequisite", "status": "NOT_VERIFIED", "detail": reason}],
        extensions={"b01.skeleton": True, "b01.prerequisite_failure": reason},
        candidate_revision=candidate_revision,
        server_revision=server_revision,
    )
    errors = validate_receipt(
        json.loads(run_path.read_text(encoding="utf-8")), verify_artifacts=False
    )
    if errors:
        print(
            "INTERNAL: prerequisite receipt is invalid: " + "; ".join(errors),
            file=sys.stderr,
        )
        return EXIT_FAIL
    if result == "FAIL":
        print(f"FAIL: {reason_value}", file=sys.stderr)
        return EXIT_FAIL
    print(f"NOT_VERIFIED: {reason}", file=sys.stderr)
    return EXIT_NOT_VERIFIED


def execute_scenario(
    spec: ScenarioSpec,
    phase_name: str,
    phase: PhaseSpec,
    args: argparse.Namespace,
) -> int:
    out_dir = Path(args.out)
    started_at = _utcnow()
    run_id = _new_run_id()
    timeout = args.timeout if args.timeout is not None else phase.timeout
    ledger = CleanupLedger()
    command = [sys.executable, str(SCRIPT_PATH), *sys.argv[1:]]
    outcome: dict[str, Any] = {}
    evidence_kind = _evidence_kind_for_phase(phase_name)
    candidate_revision, server_revision, identity_error = resolve_candidate_identity(
        evidence_kind, args.controller, timeout
    )

    if spec.scenario == "entry-readonly":

        def entry_readonly(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_entry_readonly(args.controller, timeout)
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            return code_value

        handler = entry_readonly
    elif spec.scenario == "env-runtime":

        def env_runtime(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions, reason = _run_env_runtime(
                out_dir, args.controller, timeout
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["reason"] = reason
            return code_value

        handler = env_runtime
    elif spec.scenario == "mcp-stdio":

        def mcp_stdio(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_mcp_stdio(
                args.controller, str(args.launch), timeout
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            return code_value

        handler = mcp_stdio
    else:
        handler = _b01_stub_handler(spec)

    code, timeout_record = run_handler_with_timeout(
        handler,
        timeout,
        ledger,
        out_dir,
        scenario=spec.scenario,
        phase=phase_name,
        run_id=run_id,
    )
    finished_at = _utcnow()
    if timeout_record is not None:
        reason = f"timeout after {timeout}s; cleanup record saved"
        result = "NOT_VERIFIED"
        code = EXIT_NOT_VERIFIED
    elif spec.scenario in {"entry-readonly", "mcp-stdio", "env-runtime"}:
        result = (
            "PASS"
            if code == EXIT_PASS
            else ("NOT_VERIFIED" if code == EXIT_NOT_VERIFIED else "FAIL")
        )
        failed = [
            item["name"] for item in outcome.get("assertions", []) if item.get("status") != "PASS"
        ]
        reason = (
            None
            if result == "PASS"
            else outcome.get("reason") or "assertion_failed:" + ",".join(failed)
        )
    else:
        reason = f"missing_capability:{spec.missing_capability}"
        result = (
            "NOT_VERIFIED"
            if code == EXIT_NOT_VERIFIED
            else ("PASS" if code == EXIT_PASS else "FAIL")
        )
    if spec.scenario in {"entry-readonly", "mcp-stdio", "env-runtime"}:
        extensions: dict[str, Any] = dict(outcome.get("extensions", {}))
        assertions = list(outcome.get("assertions", []))
    else:
        extensions = {
            "b01.skeleton": True,
            "b01.missing_capability": spec.missing_capability,
        }
        assertions = [
            {
                "name": "scenario_implemented",
                "status": "NOT_VERIFIED",
                "detail": spec.missing_capability,
            }
        ]
    if timeout_record is not None:
        extensions["b01.timeout_cleanup"] = "timeout-cleanup.json"
    if identity_error is not None:
        result = "FAIL"
        reason = identity_error
        code = EXIT_FAIL
    run_path, _ = write_result(
        out_dir,
        scenario=spec.scenario,
        phase=phase_name,
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        result=result,
        reason=reason,
        exit_code=code,
        command=command,
        evidence_kind=evidence_kind,
        assertions=assertions,
        extensions=extensions,
        candidate_revision=candidate_revision,
        server_revision=server_revision,
    )
    errors = validate_receipt(
        json.loads(run_path.read_text(encoding="utf-8")), verify_artifacts=False
    )
    if errors:
        print(
            "INTERNAL: written receipt is invalid: " + "; ".join(errors),
            file=sys.stderr,
        )
        return EXIT_FAIL
    if result == "PASS":
        print(f"PASS: {spec.scenario}/{phase_name}", file=sys.stderr)
    elif result == "FAIL":
        print(f"FAIL: {reason}", file=sys.stderr)
    else:
        print(f"NOT_VERIFIED: {reason}", file=sys.stderr)
    return code


# ---------------------------------------------------------------------------
# Corpus / check-only modes
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run_validate_samples(samples_root: Path = SAMPLES_ROOT) -> int:
    cases_path = samples_root / "cases.json"
    try:
        manifest = _load_json(cases_path)
    except FileNotFoundError:
        print(f"INVALID: samples manifest missing: {cases_path}", file=sys.stderr)
        return EXIT_FAIL
    except json.JSONDecodeError as exc:
        print(f"INVALID: samples manifest is not JSON: {exc}", file=sys.stderr)
        return EXIT_FAIL
    cases = manifest.get("samples") if isinstance(manifest, dict) else None
    if not isinstance(cases, list) or not cases:
        print("INVALID: samples manifest has no samples", file=sys.stderr)
        return EXIT_FAIL

    failures = 0
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("path"), str):
            failures += 1
            print("REJECT  <malformed case>  invalid_case")
            continue
        relative = case["path"]
        path = samples_root / relative
        expected = case.get("expect")
        expected_reason = case.get("expect_reason")
        try:
            document = _load_json(path)
        except FileNotFoundError:
            failures += 1
            print(f"REJECT  {relative}  sample_missing")
            continue
        except json.JSONDecodeError as exc:
            failures += 1
            print(f"REJECT  {relative}  json_parse:{exc.msg}")
            continue
        reasons = validate_receipt(
            document,
            verify_artifacts=True,
            artifact_root=path.parent,
        )
        accepted = not reasons
        verdict = "ACCEPT" if accepted else "REJECT"
        reason = "ok" if accepted else reasons[0]
        print(f"{verdict}  {relative}  {reason}")
        if expected == "accept":
            if not accepted:
                failures += 1
                print(f"  MISMATCH: expected accept, got {reason}")
        elif expected == "reject":
            if accepted:
                failures += 1
                print("  MISMATCH: expected reject, got accept")
            elif isinstance(expected_reason, str) and expected_reason not in reason:
                failures += 1
                print(f"  MISMATCH: expected reason containing {expected_reason!r}, got {reason!r}")
        else:
            failures += 1
            print(f"  MISMATCH: unknown expectation {expected!r}")
    print(f"SUMMARY: {len(cases) - failures}/{len(cases)} sample expectations matched")
    return EXIT_PASS if failures == 0 else EXIT_FAIL


def run_check_only(path: Path) -> int:
    errors = validate_summary(path)
    if errors:
        for reason in errors:
            print(f"INVALID: {reason}", file=sys.stderr)
        return EXIT_FAIL
    print(f"VALID: {path} (tool check only; this is not a scenario PASS)")
    return EXIT_PASS


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _help_epilog() -> str:
    lines = [
        "Registered B scenarios (15 immutable IDs):",
    ]
    for scenario in EXPECTED_B_SCENARIOS:
        spec = B_SCENARIOS[scenario]
        phase_names = ", ".join(spec.phases)
        required = sorted(
            {arg for phase in spec.phases.values() for arg in phase.required_args if arg != "out"}
        )
        required_text = ", ".join(f"--{name}" for name in required) or "(none)"
        controller = "yes" if any(p.controller_required for p in spec.phases.values()) else "no"
        lines.append(
            f"  {scenario:<18} owner={spec.default_owner:<5} phases={phase_names}"
            f"  required={required_text}  controller={controller}"
        )
    lines.append("")
    lines.append("Phase registry (scenario/phase -> owner, prerequisites, timeout):")
    for scenario, phase_name, phase in all_phase_rows():
        prereqs = ", ".join(phase.prerequisites) or "none"
        effects = ", ".join(phase.side_effects) or "none"
        lines.append(
            f"  {scenario}/{phase_name:<16} owner={phase.owner:<5}"
            f" op={phase.operation_class:<9} target={phase.target:<6}"
            f" timeout={phase.timeout}s  prereq={prereqs}  effects={effects}"
        )
    lines.extend(
        [
            "",
            "A/C namespaced IDs: science:* (A) / experience:* (C).",
            "dsh-local-singlepoint is C-owned (experience:dsh-local-singlepoint),"
            " not a 16th B scenario.",
            "Exit codes: 0 PASS/tool check, 1 FAIL, 2 NOT_VERIFIED, 3 usage error.",
            "--help/--dry-run/--check-only are tool checks, never a scenario PASS.",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acp2_runtime.py",
        description=(
            "ACP 2.0 runtime acceptance harness skeleton (acp2.acceptance.v1). "
            "Validate receipts/samples, print exact dry-run commands, or execute "
            "a registered scenario. Scenario bodies are owned by B04a..B25b."
        ),
        epilog=_help_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scenario", help="registered B scenario ID")
    parser.add_argument(
        "--controller",
        help="existing Controller URL to connect to; the harness never starts one",
    )
    parser.add_argument("--launch", help="MCP stdio launch manifest path")
    parser.add_argument("--fixture", help="scenario request/fixture JSON path")
    parser.add_argument("--out", help="output directory for run receipt + summary")
    parser.add_argument(
        "--timeout", type=float, default=None, help="override the registry timeout (seconds)"
    )
    parser.add_argument(
        "--check-only",
        type=Path,
        metavar="PATH",
        help="validate an existing summary.json or receipt (tool check, not PASS)",
    )
    parser.add_argument("--phase", help="registered phase for the scenario")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print exact commands and create no DB/process/job",
    )
    parser.add_argument(
        "--validate-samples",
        action="store_true",
        help="run the deterministic acceptance sample corpus",
    )
    parser.add_argument(
        "--samples-root",
        type=Path,
        default=SAMPLES_ROOT,
        help="sample corpus root (default: the frozen runtime samples)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.validate_samples:
        return run_validate_samples(args.samples_root)
    if args.check_only is not None:
        return run_check_only(args.check_only)

    if not args.scenario:
        parser.print_usage(sys.stderr)
        print("usage error: --scenario is required", file=sys.stderr)
        return EXIT_USAGE
    spec = B_SCENARIOS.get(args.scenario)
    if spec is None:
        print(f"usage error: unknown scenario {args.scenario!r}", file=sys.stderr)
        return EXIT_USAGE

    phase_name = args.phase or "main"
    if phase_name == "preflight":
        phase_name = "da-preflight"
    phase = spec.phases.get(phase_name)
    if phase is None:
        print(
            f"usage error: undeclared scenario/phase combination {spec.scenario}/{phase_name}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.dry_run:
        print("DRY-RUN (not a scenario result; no DB/process/job is created)")
        for command in dry_run_commands(spec, phase_name, phase, args):
            print(command)
        return EXIT_PASS

    if not args.out:
        print("usage error: --out is required for execution", file=sys.stderr)
        return EXIT_USAGE
    missing_args = [
        name for name in phase.required_args if name != "out" and getattr(args, name) is None
    ]
    if missing_args:
        rendered = ", ".join(f"--{name}" for name in missing_args)
        print(f"usage error: missing required argument(s): {rendered}", file=sys.stderr)
        return EXIT_USAGE

    if phase.controller_required:
        if not args.controller:
            return _write_prerequisite_result(spec, phase_name, phase, args, "missing_controller")
        if not controller_reachable(args.controller):
            return _write_prerequisite_result(
                spec, phase_name, phase, args, "controller_unreachable"
            )
    if "launch" in phase.required_args and not Path(args.launch).is_file():
        return _write_prerequisite_result(spec, phase_name, phase, args, "launch_missing")
    if "fixture" in phase.required_args and not Path(args.fixture).is_file():
        return _write_prerequisite_result(spec, phase_name, phase, args, "fixture_missing")
    return execute_scenario(spec, phase_name, phase, args)


if __name__ == "__main__":
    raise SystemExit(main())
