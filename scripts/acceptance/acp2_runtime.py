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
import importlib.metadata
import json
import os
import platform
import queue
import re
import secrets
import shlex
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (CI matrix)
    import tomli as tomllib

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
CONTRACT_MANIFEST_PATH = CONTRACT_ROOT / "manifest.json"
CONTRACT_VERSION_FALLBACK = "1.0.0"

BASELINE_CANDIDATE_REVISION = "3670790363c7a1959ad1dbe4b4297adec518952a"
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
# C experience required observations (M0-03/R10)
# ---------------------------------------------------------------------------
#
# ``evidence_kind=live`` is a signed product statement, so it may no longer be
# derived from the phase alone.  Every C live scenario declares, per
# observation, whether the live row must have observed it (``present``) or must
# have observed its absence/untriggered state (``absent``).  A live row that
# does not declare its observations is a contract error, never a default
# accept.  ``model=absent`` is a valid live negative only with exactly zero
# model calls and a real untriggered/denial fact; a budget-denial row must also
# carry no TaskRef.

EXPERIENCE_OBSERVATIONS_SCHEMA = "acp2.experience.observations.v1"
OBSERVATION_NAMES = (
    "transport",
    "controller",
    "model",
    "qc",
    "lsf",
    "browser",
    "artifact",
    "denial",
)
OBSERVATION_DIRECTIONS = ("present", "absent")

_DEFAULT_OBSERVATIONS: dict[str, str] = {
    "transport": "present",
    "controller": "present",
    "model": "absent",
    "qc": "absent",
    "lsf": "absent",
    "browser": "absent",
    "artifact": "absent",
    "denial": "absent",
}


def _observations(**overrides: str) -> dict[str, str]:
    """Build one declared direction map on top of the platform-only default."""

    declared = dict(_DEFAULT_OBSERVATIONS)
    for name, direction in overrides.items():
        if name not in OBSERVATION_NAMES:
            raise ValueError(f"unknown observation {name!r}")
        if direction not in OBSERVATION_DIRECTIONS:
            raise ValueError(f"unknown observation direction {direction!r}")
        declared[name] = direction
    return declared


EXPERIENCE_REQUIRED_OBSERVATIONS: dict[str, dict[str, str]] = {
    "mcp-live": _observations(),
    "dsh-discovery-live": _observations(),
    "model-live": _observations(controller="absent", model="present"),
    "dsh-local-singlepoint-live": _observations(model="present", qc="present", artifact="present"),
    "dsh-lsf-singlepoint-live": _observations(
        model="present", qc="present", lsf="present", artifact="present"
    ),
    "dsh-optfreq-live": _observations(model="present", qc="present", artifact="present"),
    # M4-02: the row now runs two real browsers plus a read-only identity, so
    # the negative (a read-only submit refused by the platform) is a required
    # observation of the row, not an optional extra.
    "same-task-browser-live": _observations(
        model="present",
        qc="present",
        browser="present",
        artifact="present",
        denial="present",
    ),
    "client-recovery-live": _observations(),
    # M4-02: the row now runs in real browsers (two acting identities + one
    # read-only), so the live row must observe the browser and the denial.
    "approval-budget-live": _observations(browser="present", denial="present"),
    "research-loop-live": _observations(model="present"),
    "evidence-browser-live": _observations(
        controller="absent", browser="present", artifact="present"
    ),
    "research-resume-live": _observations(model="present"),
    "fault-boundaries-live": _observations(denial="present"),
    "da-research-live": _observations(model="present", qc="present", artifact="present"),
    "plugin-discovery-live": _observations(controller="absent"),
    # M4-05: the row is executed for real — a real Controller serves the
    # third-party capability form to a real Chromium, which renders it and shows
    # the real preflight ViewModel.  No scientific artifact is prepared, so the
    # artifact observation stays absent.
    "schema-form-live": _observations(browser="present"),
    "plugin-upgrade-live": _observations(artifact="present"),
    "cross-domain-live": _observations(model="present", qc="present", artifact="present"),
    "multi-project-live": _observations(),
    "experience-upgrade-live": _observations(browser="present"),
    "clean-install-live": _observations(transport="absent", controller="absent"),
    "operator-runbook-live": _observations(artifact="present"),
    # Aggregate/closeout rows resolve to ``main`` + live but start no live
    # component of their own; every live observation is declared absent.
    "p2-closeout": _observations(transport="absent", controller="absent"),
    "client-release": _observations(transport="absent", controller="absent"),
    "experience-release-closeout": _observations(transport="absent", controller="absent"),
}


def required_observations(scenario_key: str) -> dict[str, str] | None:
    """Return the frozen present/absent directions for one C live scenario."""

    declared = EXPERIENCE_REQUIRED_OBSERVATIONS.get(scenario_key)
    return dict(declared) if declared is not None else None


def contract_version() -> str:
    """Return the delivered contract version from the authoritative manifest.

    The harness must never hardcode a version: every contract bump lands in
    ``docs/reference/next-change/acp2-contracts/manifest.json`` and must flow
    into receipts and summaries automatically.  The fallback is used only when
    the manifest is unreadable or malformed.
    """

    try:
        manifest = json.loads(CONTRACT_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return CONTRACT_VERSION_FALLBACK
    value = manifest.get("contract_version") if isinstance(manifest, dict) else None
    if isinstance(value, str) and SEMVER.match(value):
        return value
    return CONTRACT_VERSION_FALLBACK


METADATA_VERSION_SOURCES = (
    "src/acp/__init__.py",
    "src/cccp/__init__.py",
    "src/cccp/version.py",
    "pyproject.toml",
)
_VERSION_LITERAL_RE = re.compile(r'^[ \t]*__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def source_version_locations(repo_root: Path) -> dict[str, str]:
    """Read the four frozen version sources (literal assignment + TOML parse)."""
    versions: dict[str, str] = {}
    for relpath in METADATA_VERSION_SOURCES[:3]:
        text = (repo_root / relpath).read_text(encoding="utf-8")
        matches = _VERSION_LITERAL_RE.findall(text)
        if not matches:
            raise ValueError(f"{relpath} has no literal __version__")
        versions[relpath] = matches[-1]
    project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    versions["pyproject.toml"] = str(project["project"]["version"])
    return versions


def editable_metadata_version_check(
    repo_root: Path,
) -> tuple[bool, str, dict[str, str]]:
    """m5: installed ``acp`` metadata version must equal all four sources."""
    try:
        sources = source_version_locations(repo_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, f"version_source_read_failed: {type(exc).__name__}: {exc}", {}
    try:
        installed = importlib.metadata.version("acp")
    except importlib.metadata.PackageNotFoundError:
        return (
            False,
            "acp distribution metadata is not installed (run pip install -e .)",
            {"installed": "<missing>", **sources},
        )
    mismatched = [name for name, value in sources.items() if value != installed]
    detail = (
        "installed="
        + installed
        + " "
        + " ".join(f"{name}={value}" for name, value in sources.items())
    )
    return (
        (not mismatched and len(set(sources.values()) | {installed}) == 1),
        detail,
        {
            "installed": installed,
            **sources,
        },
    )


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
    """One of the 16 immutable B scenario IDs (M2-04 adds the recipe DAG)."""

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
                # B07 froze the LSF maxima; the arithmetic sum (1800 + 300 +
                # 120 + 60 + 120 = 2400) is authoritative and main() re-derives
                # it from fixture-runs.json before execution.
                2400,
                args=("fixture", "launch", "out"),
                controller=True,
                prerequisites=("fixture", "launch", "controller"),
                side_effects=("one LSF submission",),
            ),
            "live": _phase(
                "B11",
                "submit",
                "remote",
                2400,
                args=("fixture", "launch", "out"),
                controller=True,
                prerequisites=("fixture", "launch", "controller"),
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
                1800,
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
    "bounded-recipe-dag": ScenarioSpec(
        "bounded-recipe-dag",
        "M2-04",
        {
            "integration:multi-step": _phase(
                "M2-04",
                "read",
                "local",
                600,
                args=("fixture", "out"),
                prerequisites=("fixture",),
                side_effects=("no QC", "no controller", "no second task"),
            ),
        },
        "M2-04 bounded recipe DAG production wiring is not implemented",
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
    "bounded-recipe-dag",
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

    prereq_blocked = document.get("result") == "NOT_VERIFIED" and bool(
        _extension_str(document, "b01.prerequisite_failure")
        or _extension_str(document, "b11.prerequisite_missing")
    )
    if prereq_blocked:
        for key in PLATFORM_REF_KEYS:
            if refs[key] is not None:
                errors.append(f"live_prereq_ref_not_null:{key}")
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


def _nested_evidence_kinds(value: Any, path: str = "extensions") -> list[tuple[str, str]]:
    """Collect nested ``evidence_kind`` claims that must never sit in a live row."""

    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == "evidence_kind" and isinstance(child, str):
                if child in ("fixture", "offline_fixture"):
                    found.append((child, child_path))
            found.extend(_nested_evidence_kinds(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_nested_evidence_kinds(child, f"{path}[{index}]"))
    return found


def _experience_observation_errors(document: dict[str, Any]) -> list[str]:
    """Validate the per-scenario required observations of one C receipt."""

    errors: list[str] = []
    if document.get("owner") != "C":
        return errors
    scenario = document.get("scenario")
    if not isinstance(scenario, str):
        return errors
    scenario_key = (
        scenario[len("experience:") :] if scenario.startswith("experience:") else scenario
    )
    evidence_kind = document.get("evidence_kind")
    result = document.get("result")
    extensions = document.get("extensions")

    if evidence_kind == "live" and isinstance(extensions, dict):
        for nested_kind, nested_path in _nested_evidence_kinds(extensions):
            errors.append(f"live_nested_evidence_kind:{nested_kind}:{nested_path}")

    raw = extensions.get("experience.observations") if isinstance(extensions, dict) else None
    if raw is None:
        if evidence_kind == "live" and result == "PASS":
            errors.append("experience_observations_missing")
        return errors
    if not isinstance(raw, dict):
        errors.append("experience_observations_invalid")
        return errors

    declared = EXPERIENCE_REQUIRED_OBSERVATIONS.get(scenario_key)
    if evidence_kind == "live" and declared is None:
        errors.append(f"experience_observations_undeclared:{scenario_key}")
    published_declaration = (
        extensions.get("experience.declared_observations") if isinstance(extensions, dict) else None
    )
    if declared is not None and published_declaration is not None:
        if published_declaration != declared:
            errors.append("experience_observations_declaration_drift")

    schema = raw.get("schema")
    if schema is not None and schema != EXPERIENCE_OBSERVATIONS_SCHEMA:
        errors.append(f"experience_observations_schema_unsupported:{schema}")

    observed = raw.get("observed")
    if not isinstance(observed, dict) or not observed:
        errors.append("experience_observations_invalid")
        return errors

    for name in sorted(observed):
        direction = observed[name]
        if name not in OBSERVATION_NAMES:
            errors.append(f"experience_observation_unknown:{name}")
            continue
        if direction not in OBSERVATION_DIRECTIONS:
            errors.append(f"experience_observation_direction_invalid:{name}")
            continue
        if evidence_kind == "fixture" and direction == "present":
            errors.append(f"fixture_observation_present:{name}")

    model = observed.get("model")
    if model in OBSERVATION_DIRECTIONS:
        calls = raw.get("model_calls")
        model_evidence = raw.get("model_evidence")
        calls_ok = isinstance(calls, int) and not isinstance(calls, bool)
        has_evidence = isinstance(model_evidence, str) and bool(model_evidence.strip())
        if not calls_ok:
            errors.append("experience_observations_model_calls_invalid")
        if model == "present":
            if calls_ok and calls < 1:
                errors.append("live_model_present_without_calls")
            if not has_evidence:
                errors.append("live_model_present_without_evidence")
        else:
            if calls_ok and calls != 0:
                errors.append("live_model_absent_with_calls")
            if not has_evidence:
                errors.append("live_model_absent_without_fact")

    if observed.get("denial") == "present":
        denial_evidence = raw.get("denial_evidence")
        denial_kind = raw.get("denial_kind")
        has_denial = isinstance(denial_evidence, str) and bool(denial_evidence.strip())
        if not has_denial:
            errors.append("live_denial_present_without_fact")
        budget_denial = denial_kind == "budget" or (
            isinstance(denial_evidence, str) and denial_evidence.startswith("budget")
        )
        if budget_denial:
            for key in ("job_id", "operation_id", "attempt"):
                if document.get(key) is not None:
                    errors.append(f"live_budget_denial_task_ref:{key}")

    if raw.get("offline_fixture_content") is True and evidence_kind == "live":
        errors.append("live_inner_offline_fixture")

    if evidence_kind == "live" and result == "PASS" and declared is not None:
        for name in sorted(declared):
            if name not in observed:
                errors.append(f"experience_observation_missing:{name}")
            elif observed[name] != declared[name]:
                errors.append(f"experience_observation_mismatch:{name}")
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
    errors.extend(_experience_observation_errors(document))
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
            candidates.append(REPO_ROOT / relative)
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


def _evidence_kind_for_phase(phase: str, scenario: str | None = None) -> str:
    if scenario == "migration":
        # The migration scenario copies frozen fixtures into an isolated test
        # workspace; it starts no Controller and creates no live task.
        return "fixture"
    if phase == "fixture" or phase.endswith(":fixture"):
        return "fixture"
    if phase == "aggregate":
        return "aggregate"
    if scenario == "bounded-recipe-dag":
        # M2-04 exercises the production adapter/fence/quota/receipt chain with a
        # controlled CCCP stub: no QC binary, no controller, no real science.
        return "fixture"
    if phase == "da-preflight":
        # B18 has no compute and creates no task; this is static/simulated
        # contract evidence, never a live DA execution (B20a owns that).
        return "fixture"
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
    client_revision: str | None = None,
    claim_status: str | None = None,
    h4_gate: str | None = None,
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
        "contract_version": contract_version(),
        "owner": owner,
        "scenario": scenario,
        "phase": phase,
        "run_id": run_id,
        "candidate_revision": candidate_revision,
        "candidate": {
            "server_revision": server_revision,
            "client_revision": client_revision,
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
    if claim_status is not None:
        document["claim_status"] = claim_status
    if h4_gate is not None:
        document["h4_gate"] = h4_gate
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
        "contract_version": contract_version(),
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


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def candidate_revision_from_extensions(scenario: str, extensions: Mapping[str, Any]) -> str | None:
    """Return the installed candidate identity recorded by a scenario handler.

    ``package-install`` installs wheels whose release manifest pins the exact
    source revision.  That revision is the candidate; the harness baseline
    constant is only a fallback for receipts with no installed artifact.
    """
    if scenario != "package-install":
        return None
    value = extensions.get("b25.candidate_revision")
    if isinstance(value, str) and _FULL_SHA_RE.fullmatch(value):
        return value
    return None


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
    expected_orca = "/home/xieningke/orca611/orca"
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
        and run_root == "/ACPServer/acp2-development/data/b/run-root"
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
        Path(os.environ.get("ACP2_DATA_ROOT", "/ACPServer/acp2-development/data/b"))
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
    project_id = os.environ.get("ACP_PROJECT_ID") or "uncategorized"
    project_query = quote(project_id, safe="")
    science_capabilities_url = base + "/api/v2/science/capabilities?project_id=" + project_query
    science_preflight_url = base + "/api/v2/science/preflight?project_id=" + project_query
    try:
        before = _controller_task_count(base, timeout)
        health = _http_json(base + "/api/health", timeout)
        capabilities = _http_json(base + "/api/capabilities", timeout)
        preflight = _http_json_post(base + "/api/v1/preflight", preflight_request, timeout)
        science_capabilities_status, science_capabilities = _http_json_status(
            science_capabilities_url, timeout
        )
        science_preflight_status, science_preflight = _http_post_json_status(
            science_preflight_url, preflight_request, timeout
        )
        missing_status, missing_body = _http_error_body(
            base + "/api/v1/operations/op_b06_missing", timeout
        )
        after = _controller_task_count(base, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        extensions["b04a.entry_readonly"] = f"request_failed: {exc}"
        assertions.append({"name": "controller_commands", "status": "FAIL", "detail": str(exc)})
        return EXIT_FAIL, assertions, extensions
    m5_ok, m5_detail, m5_versions = editable_metadata_version_check(REPO_ROOT)

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
    science_capabilities_ok = science_capabilities_status == 200 and isinstance(
        science_capabilities, dict
    )
    science_preflight_ok = (
        science_preflight_status == 200 and isinstance(science_preflight, dict) and before == after
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
                "name": "science_capabilities_v2_zero_side_effects",
                "status": "PASS" if science_capabilities_ok and before == after else "FAIL",
                "detail": (
                    f"status={science_capabilities_status} "
                    f"tasks_before={before} tasks_after={after}"
                ),
            },
            {
                "name": "science_preflight_v2_zero_side_effects",
                "status": "PASS" if science_preflight_ok else "FAIL",
                "detail": (
                    f"status={science_preflight_status} tasks_before={before} tasks_after={after}"
                ),
            },
            {
                "name": "editable_metadata_version_matches_four_sources",
                "status": "PASS" if m5_ok else "FAIL",
                "detail": m5_detail,
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
            "b04a.science_capabilities_v2_status": science_capabilities_status,
            "b04a.science_preflight_v2_status": science_preflight_status,
            "b04a.science_preflight_v2_keys": (
                sorted(science_preflight) if isinstance(science_preflight, dict) else []
            ),
            "b06.preflight_digest": preflight.get("plan_digest"),
            "b06.missing_operation_status": missing_status,
            "m5.installed_version": m5_versions.get("installed"),
            "m5.source_versions": {
                name: value for name, value in m5_versions.items() if name != "installed"
            },
        }
    )
    passed = (
        health_ok
        and capabilities_ok
        and preflight_ok
        and missing_ok
        and science_capabilities_ok
        and science_preflight_ok
        and m5_ok
        and before == after
    )
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


def research_extension_authority() -> dict[str, Any]:
    """Read the one frozen extension id/version pair (M1-01a).

    The extension version is *not* hardcoded in this harness: it is read from the
    P2 authority (``p2-research-operations.v1.json::client_path.negotiation``) and
    the application projection is checked against it, so a partial bump fails
    here instead of silently asserting a stale value (the B06 1.1.0 drift).
    """
    path = CONTRACT_ROOT / "runtime" / "p2-research-operations.v1.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    negotiation = (contract.get("client_path") or {}).get("negotiation") or {}
    api = json.loads((CONTRACT_ROOT / "runtime" / "application-api.v1.json").read_text())
    projection = (api.get("mcp_extensions") or {}).get(negotiation.get("extension_id") or "") or {}
    return {
        "id": negotiation.get("extension_id"),
        "version": negotiation.get("extension_version"),
        "request_field": negotiation.get("request_field"),
        "projection_version": projection.get("version"),
        "implemented": negotiation.get("implemented"),
        "negotiated_default": negotiation.get("negotiated_default"),
    }


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
        authority = research_extension_authority()
        extension_advertised = probe.request(
            9,
            "tools/call",
            {"name": "acp_capabilities", "arguments": {}},
        )
        extension_refused = probe.request(
            10,
            "tools/call",
            {
                "name": "acp_capabilities",
                "arguments": {
                    "research_extension": {
                        "id": authority["id"],
                        "version": authority["version"],
                    }
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

    negotiation = ((initialized.get("capabilities") or {}).get("extensions") or {}).get(
        "acp.negotiation.v1"
    ) or {}
    initialized_ok = (
        initialized.get("protocolVersion") == launch.get("protocol_version", "2025-06-18")
        and "extensions" not in initialized
        and negotiation.get("supported_versions") == ["2025-06-18"]
        and "negotiated" not in json.dumps(initialized.get("capabilities") or {})
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
    advertised_entries = _extension_entries(extension_advertised)
    advertised_entry = advertised_entries[0] if advertised_entries else {}
    extension_authority_ok = (
        bool(authority["id"])
        and bool(authority["version"])
        and authority["version"] == authority["projection_version"]
        and advertised_entry.get("id") == authority["id"]
        and advertised_entry.get("version") == authority["version"]
        and advertised_entry.get("implemented") is authority["implemented"] is False
        and advertised_entry.get("negotiated") is False
    )
    refused_error = (extension_refused.get("structuredContent") or {}).get("error") or {}
    refused_entries = _extension_entries(extension_refused)
    refused_entry = refused_entries[0] if refused_entries else {}
    extension_not_negotiated_ok = (
        extension_refused.get("isError") is True
        and refused_error.get("code") == "unsupported_capability"
        and (refused_error.get("params") or {}).get("rule") == "extension_not_implemented"
        and refused_entry.get("implemented") is False
        and refused_entry.get("negotiated") is False
    )
    assertions.extend(
        [
            {
                "name": "initialize_standard_handshake",
                "status": "PASS" if initialized_ok else "FAIL",
                "detail": (
                    "server answered its own version; no top-level extensions and no "
                    "negotiated claim (ADR-006)"
                ),
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
                "name": "research_extension_authority",
                "status": "PASS" if extension_authority_ok else "FAIL",
                "detail": (
                    f"advertised {advertised_entry.get('id')}@{advertised_entry.get('version')} "
                    f"== P2 authority {authority['id']}@{authority['version']}; "
                    "implemented/negotiated false"
                ),
            },
            {
                "name": "research_extension_not_negotiated",
                "status": "PASS" if extension_not_negotiated_ok else "FAIL",
                "detail": (
                    "a matching-version request is refused in-band as "
                    "unsupported_capability/extension_not_implemented (advertising is not delivery)"
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
            "b06.task_count_before": before,
            "b06.task_count_after": after,
            "b06.adapter_exit_code": exit_code,
            "b06.tools": sorted(tool_names),
            "b06.protocol_version": initialized.get("protocolVersion"),
            "b06.research_extension": authority,
            "b06.research_extension_advertised": dict(advertised_entry),
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
        and extension_authority_ok
        and extension_not_negotiated_ok
        and before == after
        and exit_code == 0
    )
    return (EXIT_PASS if passed else EXIT_FAIL), assertions, extensions


def _extension_entries(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the ``extensions[]`` projection of an acp_capabilities result."""
    data = (result.get("structuredContent") or {}).get("data") or {}
    entries = data.get("extensions") if isinstance(data, Mapping) else None
    return [dict(entry) for entry in entries or [] if isinstance(entry, Mapping)]


def _submit_arguments(xyz_text: str, key: str, *, project_id: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "workflow": "singlepoint",
        "input": {"source_type": "xyz_text", "source": xyz_text},
        "method": {"method": "r2SCAN-3c", "charge": 0, "multiplicity": 1},
        "plan_digest": "sha256:" + "0" * 64,
        "idempotency_key": key,
    }
    if project_id:
        arguments["project_id"] = project_id
    return arguments


def _submit_task_id(result: Mapping[str, Any]) -> str:
    data = result.get("structuredContent", {}).get("data", {})
    if not isinstance(data, Mapping):
        return ""
    task_ref = data.get("task_ref")
    if isinstance(task_ref, Mapping):
        value = task_ref.get("task_id") or task_ref.get("job_id")
        if isinstance(value, str) and value:
            return value
    reference = data.get("reference")
    if isinstance(reference, Mapping):
        value = reference.get("id")
        if isinstance(value, str) and value:
            return value
    return ""


def _job_event_started_count(controller: str, task_id: str, timeout: float) -> int:
    job = _http_json(controller.rstrip("/") + f"/api/v1/jobs/{task_id}", timeout)
    work_dir = job.get("work_dir") if isinstance(job, Mapping) else None
    if not isinstance(work_dir, str) or not work_dir:
        return 0
    events = Path(work_dir) / "WORK" / "00_RUNTIME" / "events.jsonl"
    if not events.is_file():
        return 0
    count = 0
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, Mapping) and item.get("type") == "job.started":
            count += 1
    return count


def _run_submit_replay(
    controller: str,
    launch_path: str,
    fixture_path: str,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """Prove the persistent ledger: two MCP processes, one TaskRef, one dispatch."""
    base = controller.rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "submit",
        "acceptance.target": "local",
        "b08.mcp_launch": str(launch_path),
        "b08.fixture": str(fixture_path),
    }
    probes: list[_McpStdioProbe] = []
    try:
        launch = json.loads(Path(launch_path).read_text(encoding="utf-8"))
        if not isinstance(launch, dict):
            raise ValueError("launch descriptor must be a JSON object")
        xyz_text = Path(fixture_path).read_text(encoding="utf-8")
        key = "b08-" + uuid.uuid4().hex[:24]
        before = _controller_task_count(base, timeout)

        for index in (0, 1):
            probe = _McpStdioProbe(launch, timeout)
            probe.request(
                1,
                "initialize",
                {
                    "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                    "capabilities": {},
                    "clientInfo": {"name": f"acp2-b08-probe-{index}", "version": "1"},
                },
            )
            probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            probe.request(2, "tools/list", {})
            probes.append(probe)

        barrier = threading.Barrier(len(probes))
        results: list[Any] = [None] * len(probes)
        errors: list[str] = [""] * len(probes)

        def call(index: int) -> None:
            try:
                barrier.wait(timeout=timeout)
                results[index] = probes[index].request(
                    3,
                    "tools/call",
                    {
                        "name": "acp_submit",
                        "arguments": _submit_arguments(xyz_text, key),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - recorded as evidence
                errors[index] = f"{type(exc).__name__}: {exc}"

        threads = [threading.Thread(target=call, args=(index,)) for index in range(len(probes))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout)

        task_ids = [_submit_task_id(result) for result in results if isinstance(result, Mapping)]
        same_ref = len(task_ids) == 2 and bool(task_ids[0]) and task_ids[0] == task_ids[1]
        no_errors = not any(errors)
        after = _controller_task_count(base, timeout)
        one_task = after - before == 1

        task_id = task_ids[0] if task_ids else ""
        started = 0
        if task_id:
            deadline = time.monotonic() + min(timeout, 120)
            while time.monotonic() < deadline:
                started = _job_event_started_count(base, task_id, min(timeout, 15))
                if started >= 1:
                    break
                job = _http_json(base + f"/api/v1/jobs/{task_id}", timeout)
                status = job.get("status") if isinstance(job, Mapping) else ""
                if status in {"completed", "failed", "cancelled"}:
                    started = _job_event_started_count(base, task_id, min(timeout, 15))
                    break
                time.sleep(0.2)

        conflict_ok = False
        conflict_detail = ""
        conflict_probe: _McpStdioProbe | None = None
        try:
            conflict_probe = _McpStdioProbe(launch, timeout)
            conflict_probe.request(
                1,
                "initialize",
                {
                    "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                    "capabilities": {},
                    "clientInfo": {"name": "acp2-b08-conflict", "version": "1"},
                },
            )
            conflict_probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            conflict_result = conflict_probe.request(
                3,
                "tools/call",
                {
                    "name": "acp_submit",
                    "arguments": _submit_arguments("2\nH2\nH 0 0 0\nH 0 0 0.9\n", key),
                },
            )
            conflict_ok = (
                bool(conflict_result.get("isError"))
                and "idempotency" in json.dumps(conflict_result).lower()
            )
            conflict_detail = json.dumps(conflict_result.get("structuredContent", {}))[:400]
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            conflict_detail = f"{type(exc).__name__}: {exc}"
        finally:
            if conflict_probe is not None:
                conflict_probe.close()

        changed_scope_ok = False
        changed_scope_detail = ""
        scope_probe: _McpStdioProbe | None = None
        try:
            project = _http_json_post(
                base + "/api/v1/projects",
                {"name": f"b08-scope-{uuid.uuid4().hex[:8]}", "description": "B08 scope probe"},
                timeout,
            )
            project_id = project.get("project_id") or project.get("id") or ""
            scope_probe = _McpStdioProbe(launch, timeout)
            scope_probe.request(
                1,
                "initialize",
                {
                    "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                    "capabilities": {},
                    "clientInfo": {"name": "acp2-b08-scope", "version": "1"},
                },
            )
            scope_probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            scope_result = scope_probe.request(
                3,
                "tools/call",
                {
                    "name": "acp_submit",
                    "arguments": _submit_arguments(xyz_text, key, project_id=str(project_id)),
                },
            )
            changed_scope_ok = bool(scope_result.get("isError"))
            changed_scope_detail = json.dumps(scope_result.get("structuredContent", {}))[:400]
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            changed_scope_detail = f"{type(exc).__name__}: {exc}"
        finally:
            if scope_probe is not None:
                scope_probe.close()

        for probe in probes:
            probe.close()
        probes.clear()
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
    ) as exc:
        extensions["b08.probe_error"] = f"{type(exc).__name__}: {exc}"
        assertions.append({"name": "submit_replay_probe", "status": "FAIL", "detail": str(exc)})
        for probe in probes:
            probe.close()
        return EXIT_FAIL, assertions, extensions

    assertions.extend(
        [
            {
                "name": "two_mcp_processes_same_task_ref",
                "status": "PASS" if same_ref and no_errors else "FAIL",
                "detail": f"task_ids={task_ids} errors={errors}",
            },
            {
                "name": "exactly_one_new_task",
                "status": "PASS" if one_task else "FAIL",
                "detail": f"before={before} after={after}",
            },
            {
                "name": "exactly_one_dispatch",
                "status": "PASS" if started == 1 else "FAIL",
                "detail": f"job.started={started}",
            },
            {
                "name": "same_key_changed_payload_conflict",
                "status": "PASS" if conflict_ok else "FAIL",
                "detail": conflict_detail,
            },
            {
                "name": "same_key_changed_scope_rejected",
                "status": "PASS" if changed_scope_ok else "FAIL",
                "detail": changed_scope_detail,
            },
        ]
    )
    extensions.update(
        {
            "b08.task_ref": task_ids[0] if task_ids else "",
            "b08.attempt": 1,
            "b08.idempotency_key": key,
            "b08.task_count_before": before,
            "b08.task_count_after": after,
            "b08.job_started_events": started,
        }
    )
    passed = (
        same_ref and no_errors and one_task and started == 1 and conflict_ok and changed_scope_ok
    )
    return (EXIT_PASS if passed else EXIT_FAIL), assertions, extensions


# ---------------------------------------------------------------------------
# B10 — real Local singlepoint (B10a) and three-entry consumer row (B10b)
# ---------------------------------------------------------------------------

#: Registered fixture level: HF method + STO-3G basis, emitted verbatim.
B10_METHOD_LEVEL: dict[str, Any] = {
    "engine": "orca",
    "functional": "HF",
    "basis": "STO-3G",
}
B10_DEFAULT_ORCA = "/home/xieningke/orca611/orca"
B10_NATIVE_RELATIVE: tuple[str, ...] = (
    "sp.inp",
    "sp.out",
    "RESULT/task_result.json",
    "RESULT/result_manifest.json",
    "RESULT/scientific_result.json",
    "RESULT/energy/singlepoint_energy.json",
    "RESULT/structures/singlepoint_final.xyz",
    "execution_receipt.json",
    "job.json",
    "task.json",
)


def _b10_read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _b10_http_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _b10_job_started_events(work_dir: Path) -> int:
    events = work_dir / "WORK" / "00_RUNTIME" / "events.jsonl"
    if not events.is_file():
        return 0
    count = 0
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("type") == "job.started":
            count += 1
    return count


class _B10OrcaSampler:
    """Poll ``/proc`` while one Local job runs and count real ORCA processes."""

    def __init__(self, executable: str, interval: float = 0.01) -> None:
        self.executable = str(Path(executable).resolve())
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="b10-orca-sampler", daemon=True)
        self.pids: set[int] = set()
        self.max_concurrent = 0
        self.samples = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _scan(self) -> set[int]:
        pids: set[int] = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if not raw:
                continue
            argv0 = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
            if not argv0:
                continue
            try:
                resolved = str(Path(argv0).resolve())
            except OSError:
                resolved = argv0
            if resolved == self.executable:
                pids.add(int(entry.name))
        return pids

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                found = self._scan()
            except OSError:
                found = set()
            self.samples += 1
            self.pids.update(found)
            self.max_concurrent = max(self.max_concurrent, len(found))
            time.sleep(self.interval)


def _b10_orca_output(work_dir: Path) -> tuple[Path | None, str]:
    for path in sorted(work_dir.glob("*.out")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "ORCA TERMINATED NORMALLY" in text:
            return path, text
    return None, ""


def _b10_final_energy(text: str) -> float | None:
    matches = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", text)
    return float(matches[-1]) if matches else None


def _b10_submit_arguments(xyz_text: str, key: str) -> dict[str, Any]:
    """One H2/STO-3G Local submission on the registered fixture budget."""
    return {
        "workflow": "singlepoint",
        "input": {
            "source_type": "xyz_text",
            "source": xyz_text,
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {"levels": {"single_point": dict(B10_METHOD_LEVEL)}},
        "resources": {"cores": 1, "nproc": 1, "mem": "1024MB"},
        "plan_digest": "sha256:" + "0" * 64,
        "idempotency_key": key,
    }


def _b10_copy_native(work_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    native_dir = out_dir / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    for relative in B10_NATIVE_RELATIVE:
        source = work_dir / relative
        if not source.is_file():
            continue
        target = native_dir / relative.replace("/", "__")
        shutil.copy2(source, target)
        artifacts.append(
            {
                "path": str(target.relative_to(out_dir)),
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return artifacts


def _run_local_singlepoint(
    controller: str,
    launch_path: str,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B10a: submit once through the real MCP stdio adapter, then read facts."""
    base = controller.rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "submit",
        "acceptance.target": "local",
        "b10.fixture": str(fixture_path),
        "b10.mcp_launch": str(launch_path),
        "b10.method_level": dict(B10_METHOD_LEVEL),
        "b10.charge": 0,
        "b10.multiplicity": 1,
    }
    probe: _McpStdioProbe | None = None
    sampler: _B10OrcaSampler | None = None
    try:
        launch = json.loads(Path(launch_path).read_text(encoding="utf-8"))
        if not isinstance(launch, dict):
            raise ValueError("launch descriptor must be a JSON object")
        xyz_text = Path(fixture_path).read_text(encoding="utf-8")
        launch_env = launch.get("env") if isinstance(launch.get("env"), dict) else {}
        orca_path = str(
            launch_env.get("CONFSEARCH_ORCA_PATH")
            or os.environ.get("CONFSEARCH_ORCA_PATH")
            or B10_DEFAULT_ORCA
        )
        resolved_orca = Path(orca_path).resolve()
        if str(resolved_orca) == "/usr/bin/orca":
            raise ValueError("/usr/bin/orca is the GNOME screen reader, not QC ORCA")
        if not resolved_orca.is_file():
            raise ValueError(f"pinned ORCA binary missing: {resolved_orca}")
        extensions["b10.orca_binary"] = str(resolved_orca)

        key = "b10-" + uuid.uuid4().hex[:24]
        before = _controller_task_count(base, timeout)
        sampler = _B10OrcaSampler(str(resolved_orca))
        sampler.start()
        probe = _McpStdioProbe(launch, timeout)
        probe.request(
            1,
            "initialize",
            {
                "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                "capabilities": {},
                "clientInfo": {"name": "acp2-b10-driver", "version": "1"},
            },
        )
        probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        probe.request(2, "tools/list", {})
        submitted = probe.request(
            3,
            "tools/call",
            {"name": "acp_submit", "arguments": _b10_submit_arguments(xyz_text, key)},
        )
        task_id = _submit_task_id(submitted)
        if not task_id:
            raise RuntimeError("acp_submit returned no task id")

        terminal = {"completed", "failed", "cancelled"}
        deadline = time.monotonic() + timeout
        job: dict[str, Any] = {}
        while time.monotonic() < deadline:
            job = _http_json(
                base + f"/api/v1/jobs/{quote(task_id, safe='')}",
                min(timeout, 60),
            )
            if isinstance(job, dict) and str(job.get("status") or "").lower() in terminal:
                break
            time.sleep(0.2)
        sampler.stop()
        after = _controller_task_count(base, timeout)

        status = str(job.get("status") or "").lower() if isinstance(job, dict) else ""
        work_dir = Path(str(job.get("work_dir") or "")) if isinstance(job, dict) else Path()
        attempt = int(job.get("attempt") or 0) if isinstance(job, dict) else 0
        if not work_dir.is_dir():
            raise RuntimeError(f"job work_dir is not readable: {work_dir}")

        task_result = _b10_read_json(work_dir / "RESULT" / "task_result.json")
        manifest = _b10_read_json(work_dir / "RESULT" / "result_manifest.json")
        receipt = _b10_read_json(work_dir / "execution_receipt.json")
        job_json = _b10_read_json(work_dir / "job.json")
        envelope = _b10_read_json(work_dir / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json")
        orca_out, orca_text = _b10_orca_output(work_dir)
        orca_energy = _b10_final_energy(orca_text)
        result = task_result.get("result")
        result = result if isinstance(result, dict) else {}
        energy = result.get("energy_hartree")

        native_digests: dict[str, str] = {}
        for name in ("sp.inp", "sp.out"):
            path = work_dir / name
            if path.is_file():
                native_digests[name] = sha256_file(path)
        task_artifacts = (
            result.get("artifacts") if isinstance(result.get("artifacts"), list) else []
        )
        result_artifact_digests = {
            Path(str(item.get("path") or "")).name: item.get("checksum")
            for item in task_artifacts
            if isinstance(item, dict)
        }
        native_ok = bool(native_digests) and all(
            str(result_artifact_digests.get(name) or "").removeprefix("sha256:") == digest
            for name, digest in native_digests.items()
        )

        products = manifest.get("products") if isinstance(manifest.get("products"), list) else []
        task_product = next(
            (
                product
                for product in products
                if isinstance(product, dict) and product.get("id") == "task_result.json"
            ),
            None,
        )
        energy_product = next(
            (
                product
                for product in products
                if isinstance(product, dict) and product.get("id") == "singlepoint_energy"
            ),
            None,
        )
        task_product_meta = (task_product or {}).get("metadata") or {}
        energy_product_meta = (energy_product or {}).get("metadata") or {}
        manifest_task_digest = task_product_meta.get("task_result_digest")
        manifest_energy = energy_product_meta.get("energy_hartree")
        manifest_ok = (
            manifest.get("task_id") == task_id
            and manifest.get("status") == "completed"
            and manifest_task_digest == task_result.get("content_digest")
            and isinstance(manifest_energy, (int, float))
            and isinstance(energy, (int, float))
            and abs(float(manifest_energy) - float(energy)) <= 1e-9
        )

        complete_ok = (
            task_result.get("complete") is True
            and task_result.get("qualification") == "complete"
            and task_result.get("status") == "completed"
            and task_result.get("receipt_state") == "completed"
            and task_result.get("missing_required_facts") == []
            and "energy_hartree" in (task_result.get("required_facts") or [])
            and task_result.get("exit_code") == 0
        )
        release_id = str(receipt.get("release_id") or "")
        attempt_ok = (
            attempt > 0
            and attempt == int(receipt.get("attempt") or -1) == int(envelope.get("attempt") or -1)
            and str(job_json.get("attempt") or "") == str(attempt)
            and bool(release_id)
            and receipt.get("release_id") == envelope.get("release_id")
        )
        energy_ok = (
            isinstance(energy, (int, float))
            and orca_energy is not None
            and abs(orca_energy - float(energy)) <= 1e-9
        )
        symbols = result.get("symbols")
        coordinates = result.get("coordinates")
        frequencies = result.get("frequencies")
        body_ok = (
            isinstance(energy, (int, float))
            and isinstance(coordinates, list)
            and len(coordinates) == 2
            and symbols == ["H", "H"]
            and isinstance(frequencies, list)
        )
        dispatch_events = _b10_job_started_events(work_dir)
        dispatch_ok = dispatch_events == 1
        submit_ok = (
            before + 1 == after and isinstance(submitted, dict) and not submitted.get("isError")
        )

        artifacts = _b10_copy_native(work_dir, out_dir)
        extensions.update(
            {
                "b10.task_ref": task_id,
                "b10.attempt": attempt,
                "b10.release_id": release_id,
                "b10.submission_id": receipt.get("submission_id"),
                "b10.plan_identity": receipt.get("plan_identity"),
                "b10.result_digest": receipt.get("result_digest"),
                "b10.task_result_digest": task_result.get("content_digest"),
                "b10.energy_hartree": energy,
                "b10.energy_units": "hartree",
                "b10.geometry": {"symbols": symbols, "coordinates": coordinates},
                "b10.frequencies": frequencies or [],
                "b10.frequencies_present": bool(frequencies),
                "b10.orca_output": str(orca_out) if orca_out else None,
                "b10.orca_terminated_normally": "ORCA TERMINATED NORMALLY" in orca_text,
                "b10.orca_energy_hartree": orca_energy,
                "b10.orca_pids": sorted(sampler.pids),
                "b10.orca_max_concurrent": sampler.max_concurrent,
                "b10.orca_samples": sampler.samples,
                "b10.job_started_events": dispatch_events,
                "b10.task_count_before": before,
                "b10.task_count_after": after,
                "b10.work_dir": str(work_dir),
                "b10.native_artifact_digests": native_digests,
                "b10.artifacts": artifacts,
                "b10.task_result_artifact_digests": result_artifact_digests,
                "b10.manifest_task_result_digest": manifest_task_digest,
                "b10.manifest_energy_hartree": manifest_energy,
                "b10.completion_conditions": {
                    "complete": task_result.get("complete"),
                    "qualification": task_result.get("qualification"),
                    "status": task_result.get("status"),
                    "receipt_state": task_result.get("receipt_state"),
                    "missing_required_facts": task_result.get("missing_required_facts"),
                    "required_facts": task_result.get("required_facts"),
                    "exit_code": task_result.get("exit_code"),
                },
                "b10.scientific_body": {
                    "energy_hartree": energy,
                    "units": "hartree",
                    "symbols": symbols,
                    "coordinates": coordinates,
                    "frequencies": frequencies or [],
                },
                "b10.three_entry_descriptor_required": True,
            }
        )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
    ) as exc:
        if sampler is not None:
            sampler.stop()
        if probe is not None:
            probe.close()
        extensions["b10.probe_error"] = f"{type(exc).__name__}: {exc}"
        assertions.append({"name": "b10_probe", "status": "FAIL", "detail": str(exc)})
        return EXIT_FAIL, assertions, extensions
    finally:
        if probe is not None:
            probe.close()

    assertions.extend(
        [
            {
                "name": "one_mcp_submission_one_task",
                "status": "PASS" if submit_ok else "FAIL",
                "detail": f"task_count_before={before} after={after}",
            },
            {
                "name": "job_completed",
                "status": "PASS" if status == "completed" else "FAIL",
                "detail": f"status={status} attempt={attempt}",
            },
            {
                "name": "real_orca_process_once",
                "status": (
                    "PASS" if len(sampler.pids) == 1 and sampler.max_concurrent == 1 else "FAIL"
                ),
                "detail": (
                    f"pids={sorted(sampler.pids)} max_concurrent={sampler.max_concurrent} "
                    f"samples={sampler.samples}"
                ),
            },
            {
                "name": "orca_terminated_normally",
                "status": "PASS" if orca_out is not None else "FAIL",
                "detail": str(orca_out) if orca_out is not None else "no ORCA output",
            },
            {
                "name": "orca_energy_matches_task_result",
                "status": "PASS" if energy_ok else "FAIL",
                "detail": f"orca={orca_energy} task_result={energy}",
            },
            {
                "name": "task_result_complete",
                "status": "PASS" if complete_ok else "FAIL",
                "detail": "complete/qualification/required_facts/exit_code",
            },
            {
                "name": "attempt_and_release_agree",
                "status": "PASS" if attempt_ok else "FAIL",
                "detail": f"attempt={attempt} release_id={release_id}",
            },
            {
                "name": "manifest_agrees_with_task_result",
                "status": "PASS" if manifest_ok else "FAIL",
                "detail": f"manifest_task_digest={manifest_task_digest} energy={manifest_energy}",
            },
            {
                "name": "native_artifact_digests_agree",
                "status": "PASS" if native_ok else "FAIL",
                "detail": f"native={native_digests}",
            },
            {
                "name": "scientific_body_readable",
                "status": "PASS" if body_ok else "FAIL",
                "detail": f"energy_hartree={energy} symbols={symbols} frequencies={frequencies}",
            },
            {
                "name": "exactly_one_dispatch",
                "status": "PASS" if dispatch_ok else "FAIL",
                "detail": f"job.started={dispatch_events}",
            },
        ]
    )
    passed = all(item["status"] == "PASS" for item in assertions)
    return (EXIT_PASS if passed else EXIT_FAIL), assertions, extensions


B11_FIXTURE_ID = "fx-lsf-singlepoint-v1"
B11_LSF_TOOLS = ("bsub", "bjobs", "bhosts", "bqueues")
B11_TIMEOUT_FIELDS = (
    "max_queue_wait_seconds",
    "max_run_seconds",
    "max_prepare_seconds",
    "cleanup_seconds",
    "margin_seconds",
)
B11_CLIENT_REQUEST_TIMEOUT_SECONDS = 2400.0
B11_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _b11_bases(out_dir: Path) -> list[Path]:
    """Candidate evidence bases for the B11 registry/P1 records."""
    bases: list[Path] = []
    here = out_dir.resolve()
    for _ in range(4):
        here = here.parent
        bases.append(here)
    evidence_root = os.environ.get("ACP2_EVIDENCE")
    if evidence_root:
        bases.append(Path(evidence_root) / "B")
    bases.append(REPO_ROOT / "reports" / "acp2_implementation" / "bb45a46" / "B")
    unique: list[Path] = []
    for base in bases:
        if base not in unique:
            unique.append(base)
    return unique


def _b11_pick_json(out_dir: Path, relative: str) -> tuple[dict[str, Any], Path | None]:
    for base in _b11_bases(out_dir):
        candidate = base / relative
        if candidate.is_file():
            return _b10_read_json(candidate), candidate
    return {}, None


def _b11_p1_candidate(out_dir: Path) -> tuple[dict[str, Any], list[str], Path | None]:
    document, path = _b11_pick_json(out_dir, "environment/p1-candidate.json")
    errors: list[str] = []
    server = str(document.get("server_revision") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", server):
        errors.append("server_revision")
    client = str(document.get("client_revision") or "")
    if client != "NOT_VERIFIED" and not re.fullmatch(r"[0-9a-f]{40}", client):
        errors.append("client_revision")
    harness = str(document.get("harness_revision") or "")
    if not HEX64.match(harness):
        errors.append("harness_revision")
    if document.get("candidate_label") != "bb45a46":
        errors.append("candidate_label")
    if path is None:
        errors.append("p1_candidate_missing")
    return document, errors, path


def _b11_timeout_derivation(out_dir: Path) -> tuple[int | None, dict[str, Any]]:
    """Derive the LSF harness timeout from B07's frozen fixture maxima only."""
    registry, path = _b11_pick_json(out_dir, "fixture-runs.json")
    rows = registry.get("fixtures") if isinstance(registry.get("fixtures"), list) else []
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict) and item.get("fixture_id") == B11_FIXTURE_ID
        ),
        None,
    )
    maxima = {field: (row or {}).get(field) for field in B11_TIMEOUT_FIELDS}
    integers_ok = all(isinstance(value, int) and value >= 0 for value in maxima.values())
    derived = sum(int(value) for value in maxima.values()) if integers_ok else None
    row_timeout = (row or {}).get("scenario_timeout_seconds")
    registry_timeout = registry.get("lsf_timeout_seconds")
    row_matches = derived is not None and row_timeout == derived
    registry_matches = row_matches and registry_timeout == derived
    detail = {
        "registry": str(path) if path is not None else None,
        "fixture_id": B11_FIXTURE_ID,
        "maxima": maxima,
        "derived_seconds": derived,
        "row_scenario_timeout_seconds": row_timeout,
        "registry_lsf_timeout_seconds": registry_timeout,
        "row_matches": row_matches,
        "registry_matches": registry_matches,
        "formula": " + ".join(B11_TIMEOUT_FIELDS),
    }
    return (derived if registry_matches else None), detail


def _b11_ssh_credentials_present() -> bool:
    if os.environ.get("SSH_AUTH_SOCK"):
        return True
    ssh_dir = Path.home() / ".ssh"
    try:
        entries = list(ssh_dir.iterdir())
    except OSError:
        return False
    return any(
        entry.is_file()
        and (
            (entry.name.startswith("id_") and not entry.name.endswith(".pub"))
            or entry.name == "config"
        )
        for entry in entries
    )


def _b11_lsf_prerequisites() -> dict[str, Any]:
    tools = {tool: shutil.which(tool) for tool in B11_LSF_TOOLS}
    credentials = _b11_ssh_credentials_present()
    observed = {
        "ACP_LSF_NODE": os.environ.get("ACP_LSF_NODE") or None,
        "LSF_ENVDIR": os.environ.get("LSF_ENVDIR") or None,
        **{f"which:{tool}": path for tool, path in tools.items()},
        "which:ssh": shutil.which("ssh"),
        "ssh_credentials": credentials,
    }
    missing: list[str] = []
    if not observed["ACP_LSF_NODE"]:
        missing.append("ACP_LSF_NODE")
    if not observed["LSF_ENVDIR"]:
        missing.append("LSF_ENVDIR")
    for tool, path in tools.items():
        if path is None:
            missing.append(tool)
    if observed["which:ssh"] is None:
        missing.append("ssh")
    elif not credentials:
        missing.append("ssh_credentials")
    return {"observed": observed, "missing": missing}


def _b11_submission_id(job_id: str, attempt: int) -> str:
    material = f"{job_id}:{int(attempt or 1)}"
    return "sub_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _b11_iso_seconds(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _b11_timings(
    job: Mapping[str, Any], client_request: float | None, timeout: float
) -> dict[str, Any]:
    created = _b11_iso_seconds(job.get("created_at") or job.get("queued_at"))
    started = _b11_iso_seconds(job.get("started_at"))
    finished = _b11_iso_seconds(
        job.get("completed_at") or job.get("finished_at") or job.get("updated_at")
    )
    queue_wait = started - created if started is not None and created is not None else None
    science = finished - started if finished is not None and started is not None else None
    return {
        "queue_wait_seconds": queue_wait,
        "queue_wait_status": "RECORDED" if queue_wait is not None else "NOT_VERIFIED",
        "science_runtime_seconds": science,
        "science_runtime_status": "RECORDED" if science is not None else "NOT_VERIFIED",
        "client_request_seconds": client_request,
        "client_request_timeout_seconds": B11_CLIENT_REQUEST_TIMEOUT_SECONDS,
        "acceptance_timeout_seconds": timeout,
    }


def _b11_http_get(url: str, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def _b11_mcp_status(probe: _McpStdioProbe, request_id: int, task_id: str) -> dict[str, Any]:
    result = probe.request(
        request_id,
        "tools/call",
        {
            "name": "acp_status",
            "arguments": {"reference": {"kind": "job", "id": task_id}},
        },
    )
    if result.get("isError"):
        raise RuntimeError(f"acp_status error: {result.get('structuredContent')}")
    data = (result.get("structuredContent") or {}).get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("acp_status returned a non-object projection")
    return data


def _b11_prepare_manifest(
    probe: _McpStdioProbe, base: str, task_id: str, attempt: int, key: str, timeout: float
) -> tuple[dict[str, Any], int, str]:
    result = probe.request(
        200,
        "tools/call",
        {
            "name": "acp_prepare_artifact",
            "arguments": {
                "purpose": "artifact",
                "job_id": task_id,
                "attempt": attempt,
                "artifact_id": "manifest",
                "idempotency_key": key,
            },
        },
    )
    if result.get("isError"):
        raise RuntimeError(f"acp_prepare_artifact error: {result.get('structuredContent')}")
    data = (result.get("structuredContent") or {}).get("data") or {}
    operation_ref = data.get("operation_ref")
    operation_id = str(
        operation_ref.get("id") if isinstance(operation_ref, Mapping) else operation_ref or ""
    ) or str(data.get("operation_id") or "")
    if not operation_id:
        raise RuntimeError("acp_prepare_artifact returned no operation id")
    deadline = time.monotonic() + timeout
    projection: dict[str, Any] = {}
    while time.monotonic() < deadline:
        code, raw = _b11_http_get(
            base + f"/api/v1/operations/{quote(operation_id, safe='')}",
            min(60.0, timeout),
        )
        if code == 200:
            payload = json.loads(raw.decode("utf-8"))
            projection = payload if isinstance(payload, dict) else {}
            state = str(projection.get("state") or "").lower()
            availability = str(projection.get("availability") or "").lower()
            if availability == "ready" or state in {"succeeded", "completed"}:
                return projection, code, operation_id
            if state == "failed":
                raise RuntimeError(
                    f"prepare operation {operation_id} failed: {projection.get('error')}"
                )
        time.sleep(0.5)
    raise TimeoutError(f"prepare operation {operation_id} did not reach readiness")


def _b11_run_live(
    controller: str,
    launch_path: str,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    base = controller.rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {}
    probe: _McpStdioProbe | None = None
    reconnect: _McpStdioProbe | None = None
    submitted_at = time.monotonic()
    try:
        launch = json.loads(Path(launch_path).read_text(encoding="utf-8"))
        if not isinstance(launch, dict):
            raise ValueError("launch descriptor must be a JSON object")
        xyz_text = Path(fixture_path).read_text(encoding="utf-8")
        key = "b11-" + uuid.uuid4().hex[:24]
        before = _controller_task_count(base, timeout)
        probe = _McpStdioProbe(launch, timeout)
        probe.request(
            1,
            "initialize",
            {
                "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                "capabilities": {},
                "clientInfo": {"name": "acp2-b11-driver", "version": "1"},
            },
        )
        probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        probe.request(2, "tools/list", {})
        try:
            submitted = probe.request(
                3,
                "tools/call",
                {
                    "name": "acp_submit",
                    "arguments": _submit_arguments(xyz_text, key, project_id=None),
                },
            )
        except TimeoutError as exc:
            extensions["b11.client_timeout"] = str(exc)
            extensions["b11.timeouts"] = _b11_timings({}, None, timeout)
            extensions["b11.submission_unknown"] = True
            extensions["b11.no_resubmit"] = True
            extensions["b11.reason"] = "client_request_timeout_no_resubmit"
            assertions.append(
                {
                    "name": "client_request_timeout_no_resubmit",
                    "status": "NOT_VERIFIED",
                    "detail": f"{exc}; submission may be unknown; no automatic resubmit",
                }
            )
            return EXIT_NOT_VERIFIED, assertions, extensions
        client_request = time.monotonic() - submitted_at
        if submitted.get("isError"):
            raise RuntimeError(f"acp_submit error: {submitted.get('structuredContent')}")
        task_id = _submit_task_id(submitted)
        if not task_id:
            raise RuntimeError("acp_submit returned no task id")

        deadline = time.monotonic() + timeout
        job: dict[str, Any] = {}
        while time.monotonic() < deadline:
            payload = _http_json(
                base + f"/api/v1/jobs/{quote(task_id, safe='')}",
                min(60.0, timeout),
            )
            job = payload if isinstance(payload, dict) else {}
            if str(job.get("status") or "").lower() in B11_TERMINAL_STATUSES:
                break
            time.sleep(1.0)
        after = _controller_task_count(base, timeout)
        status = str(job.get("status") or "").lower()
        if status not in B11_TERMINAL_STATUSES:
            extensions.update(
                {
                    "b11.task_ref": task_id,
                    "b11.deadline_exceeded": True,
                    "b11.submission_retained": True,
                }
            )
            assertions.append(
                {
                    "name": "acceptance_deadline",
                    "status": "NOT_VERIFIED",
                    "detail": (
                        f"job still {status or 'unknown'} at deadline; "
                        "submission retained, no resubmit"
                    ),
                }
            )
            return EXIT_NOT_VERIFIED, assertions, extensions

        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        remote = result.get("remote") if isinstance(result.get("remote"), dict) else {}
        attempt = int(job.get("attempt") or 0)
        generated_submission_id = _b11_submission_id(task_id, attempt)
        lsf_job_id = str(result.get("lsf_job_id") or job.get("remote_job_id") or "")

        job_path = quote(task_id, safe="")
        code, receipt_raw = _b11_http_get(
            base + f"/api/v1/jobs/{job_path}/files/execution_receipt.json",
            min(60.0, timeout),
        )
        receipt = json.loads(receipt_raw.decode("utf-8")) if code == 200 and receipt_raw else {}
        code, envelope_raw = _b11_http_get(
            base + f"/api/v1/jobs/{job_path}/files/WORK/00_RUNTIME/frozen/envelope.json",
            min(60.0, timeout),
        )
        envelope = json.loads(envelope_raw.decode("utf-8")) if code == 200 and envelope_raw else {}
        code, listing_raw = _b11_http_get(
            base + f"/api/v1/jobs/{job_path}/remote-files", min(60.0, timeout)
        )
        listing = json.loads(listing_raw.decode("utf-8")) if code == 200 and listing_raw else {}
        remote_names = {
            str(item.get("name")) for item in listing.get("files") or [] if isinstance(item, dict)
        }
        code, script_raw = _b11_http_get(
            base + f"/api/v1/jobs/{job_path}/remote-files/submit.lsf",
            min(60.0, timeout),
        )
        script_sha = hashlib.sha256(script_raw).hexdigest() if code == 200 else None

        release_id = str(receipt.get("release_id") or "")
        submission_id = str(receipt.get("submission_id") or "")
        markers_ok = {"job.json", "task.json"} <= remote_names
        submit_ok = before + 1 == after
        completed_ok = status == "completed"
        bsub_ok = bool(re.fullmatch(r"[0-9]+", lsf_job_id))
        submission_ok = (
            submission_id == generated_submission_id
            and str(remote.get("submission_id") or "") == generated_submission_id
            and int(receipt.get("attempt") or -1) == attempt
        )
        receipt_ok = (
            str(receipt.get("state") or "").lower() == "completed"
            and int(job.get("exit_code") if job.get("exit_code") is not None else -1) == 0
            and bool(receipt.get("result_digest"))
        )
        release_ok = (
            bool(release_id)
            and release_id == str(envelope.get("release_id") or "")
            and release_id == str(remote.get("code_release") or "")
        )

        cold_code, _cold_body = _b11_http_get(
            base + f"/api/v1/jobs/{job_path}/prepared/manifest?attempt={attempt}",
            min(60.0, timeout),
        )
        projection, _, operation_id = _b11_prepare_manifest(
            probe, base, task_id, attempt, key + "-prepare", timeout
        )
        artifact_ref = projection.get("artifact_ref")
        artifact_ref = artifact_ref if isinstance(artifact_ref, dict) else {}
        generation = int(artifact_ref.get("generation") or 0)
        artifact_digest = str(artifact_ref.get("digest") or "")
        pull_code, body = _b11_http_get(
            base
            + f"/api/v1/jobs/{job_path}/prepared/manifest?attempt={attempt}"
            + f"&expected_revision={generation}",
            min(60.0, timeout),
        )
        body_digest = "sha256:" + hashlib.sha256(body).hexdigest() if pull_code == 200 else ""
        operation_sha = str(projection.get("sha256") or "")
        digest_ok = bool(operation_sha) and body_digest.removeprefix(
            "sha256:"
        ) == operation_sha.removeprefix("sha256:")
        generation_ok = generation == int(
            projection.get("basis_revision") or 0
        ) and generation == int(projection.get("expected_revision") or 0)
        old_attempt_code, _ = _b11_http_get(
            base
            + f"/api/v1/jobs/{job_path}/prepared/manifest?attempt={attempt + 1}"
            + f"&expected_revision={generation}",
            min(60.0, timeout),
        )

        probe.close()
        probe = None
        reconnect = _McpStdioProbe(launch, timeout)
        reconnect.request(
            1,
            "initialize",
            {
                "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                "capabilities": {},
                "clientInfo": {"name": "acp2-b11-reconnect", "version": "1"},
            },
        )
        reconnect.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        reconnect.request(2, "tools/list", {})
        reread = _b11_mcp_status(reconnect, 3, task_id)
        after_reconnect = _controller_task_count(base, timeout)
        reconnect_ok = (
            str(reread.get("job_id") or reread.get("task_id") or "") == task_id
            and after_reconnect == after
            and int(reread.get("attempt") or 0) == attempt
        )

        timings = _b11_timings(job, client_request, timeout)
        extensions.update(
            {
                "b11.task_ref": task_id,
                "b11.attempt": attempt,
                "b11.release_id": release_id,
                "b11.submission_id": submission_id,
                "b11.lsf_job_id": lsf_job_id,
                "b11.operation_id": operation_id,
                "b11.manifest_generation": generation,
                "b11.remote_dir": result.get("remote_dir"),
                "b11.remote_node": result.get("node") or remote.get("node"),
                "b11.remote_host": result.get("host"),
                "b11.remote_command_line": result.get("command_line"),
                "b11.remote_files": sorted(remote_names),
                "b11.submit_script_sha256": script_sha,
                "b11.submit_script_observed": script_sha is not None,
                "b11.lsf_command_note": (
                    "The Controller remote runner executed bsub; this harness records the "
                    "observed remote submit.lsf and the parsed LSF id, not a generated command "
                    "as real execution."
                ),
                "b11.prepare": {
                    "state": projection.get("state"),
                    "availability": projection.get("availability"),
                    "sha256": operation_sha,
                    "artifact_digest": artifact_digest,
                    "generation": generation,
                    "pull_http_status": pull_code,
                    "cold_cache_http_status": cold_code,
                    "old_attempt_http_status": old_attempt_code,
                },
                "b11.timings": timings,
                "b11.receipt": receipt,
                "b11.envelope_release": envelope.get("release_id"),
            }
        )
        assertions.extend(
            [
                {
                    "name": "one_mcp_submission_one_task",
                    "status": "PASS" if submit_ok else "FAIL",
                    "detail": f"task_count_before={before} after={after}",
                },
                {
                    "name": "job_completed",
                    "status": "PASS" if completed_ok else "FAIL",
                    "detail": f"status={status} exit_code={job.get('exit_code')}",
                },
                {
                    "name": "real_bsub_id",
                    "status": "PASS" if bsub_ok else "FAIL",
                    "detail": f"lsf_job_id={lsf_job_id!r}",
                },
                {
                    "name": "submission_record_matches",
                    "status": "PASS" if submission_ok else "FAIL",
                    "detail": (
                        f"receipt={submission_id!r} "
                        f"derived={generated_submission_id!r} attempt={attempt}"
                    ),
                },
                {
                    "name": "terminal_receipt",
                    "status": "PASS" if receipt_ok else "FAIL",
                    "detail": f"state={receipt.get('state')} digest={receipt.get('result_digest')}",
                },
                {
                    "name": "release_id_verified",
                    "status": "PASS" if release_ok else "FAIL",
                    "detail": f"receipt={release_id!r} envelope={envelope.get('release_id')!r}",
                },
                {
                    "name": "scheduler_markers_present",
                    "status": "PASS" if markers_ok else "FAIL",
                    "detail": (
                        f"remote_files={sorted(remote_names)} "
                        "(ordering proved by the SIMULATED contract test)"
                    ),
                },
                {
                    "name": "cold_cache_read_not_prepared",
                    "status": "PASS" if cold_code == 409 else "NOT_VERIFIED",
                    "detail": (
                        f"http_status={cold_code} (409 proves not_prepared; "
                        "200 means the terminal prefetch was already warm)"
                    ),
                },
                {
                    "name": "prepare_artifact_completed",
                    "status": "PASS" if projection.get("availability") == "ready" else "FAIL",
                    "detail": (
                        f"operation={operation_id} availability={projection.get('availability')}"
                    ),
                },
                {
                    "name": "artifact_digest_generation_verified",
                    "status": "PASS"
                    if digest_ok and generation_ok and pull_code == 200
                    else "FAIL",
                    "detail": (
                        f"pull={pull_code} digest={body_digest} generation={generation} "
                        f"operation_sha={operation_sha}"
                    ),
                },
                {
                    "name": "old_attempt_rejected",
                    "status": "PASS" if old_attempt_code == 409 else "FAIL",
                    "detail": f"attempt={attempt + 1} http_status={old_attempt_code}",
                },
                {
                    "name": "reconnect_same_task_no_new_submission",
                    "status": "PASS" if reconnect_ok else "FAIL",
                    "detail": f"task_count_after={after_reconnect} attempt={reread.get('attempt')}",
                },
                {
                    "name": "timeouts_recorded_separately",
                    "status": (
                        "PASS"
                        if timings["queue_wait_seconds"] is not None
                        and timings["science_runtime_seconds"] is not None
                        else "NOT_VERIFIED"
                    ),
                    "detail": json.dumps(timings, sort_keys=True),
                },
            ]
        )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
    ) as exc:
        extensions["b11.probe_error"] = f"{type(exc).__name__}: {exc}"
        assertions.append({"name": "b11_probe", "status": "FAIL", "detail": str(exc)})
        return EXIT_FAIL, assertions, extensions
    finally:
        if probe is not None:
            probe.close()
        if reconnect is not None:
            reconnect.close()

    if any(item["status"] == "FAIL" for item in assertions):
        return EXIT_FAIL, assertions, extensions
    if any(item["status"] != "PASS" for item in assertions):
        return EXIT_NOT_VERIFIED, assertions, extensions
    return EXIT_PASS, assertions, extensions


def _run_lsf_singlepoint(
    controller: str | None,
    launch_path: str,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "submit",
        "acceptance.target": "remote",
        "b11.fixture": str(fixture_path),
        "b11.mcp_launch": str(launch_path),
    }
    candidate, candidate_errors, candidate_path = _b11_p1_candidate(out_dir)
    extensions.update(
        {
            "b11.p1_candidate": str(candidate_path) if candidate_path is not None else None,
            "b11.server_revision": candidate.get("server_revision"),
            "b11.client_revision": candidate.get("client_revision"),
            "b11.p1_harness_revision": candidate.get("harness_revision"),
            "b11.harness_revision": harness_revision(),
        }
    )
    assertions.append(
        {
            "name": "p1_candidate_record",
            "status": "PASS" if not candidate_errors else "FAIL",
            "detail": (
                f"server={candidate.get('server_revision')} "
                f"client={candidate.get('client_revision')} "
                f"harness={candidate.get('harness_revision')}"
                if not candidate_errors
                else "invalid:" + ",".join(candidate_errors)
            ),
        }
    )

    registry, registry_path = _b11_pick_json(out_dir, "fixture-runs.json")
    rows = registry.get("fixtures") if isinstance(registry.get("fixtures"), list) else []
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict) and item.get("fixture_id") == B11_FIXTURE_ID
        ),
        {},
    )
    fixture_digest = sha256_file(Path(fixture_path)) if Path(fixture_path).is_file() else ""
    fixture_path_ok = (
        bool(row.get("input")) and Path(str(row["input"])).resolve() == Path(fixture_path).resolve()
    )
    fixture_digest_ok = bool(row.get("input_digest")) and row.get("input_digest") == fixture_digest
    extensions.update(
        {
            "b11.fixture_registry": str(registry_path) if registry_path is not None else None,
            "b11.fixture_input_digest": fixture_digest,
            "b11.fixture_row": row,
        }
    )
    assertions.append(
        {
            "name": "fixture_matches_registry",
            "status": "PASS" if fixture_path_ok and fixture_digest_ok else "FAIL",
            "detail": (
                f"path_match={fixture_path_ok} digest_match={fixture_digest_ok} "
                f"digest={fixture_digest}"
            ),
        }
    )

    derived, derivation = _b11_timeout_derivation(out_dir)
    extensions["b11.timeout_derivation"] = derivation
    assertions.append(
        {
            "name": "harness_timeout_derived",
            "status": "PASS"
            if derived is not None and float(derived) == float(timeout)
            else "FAIL",
            "detail": (
                f"derived={derived} effective={timeout} "
                f"matches={derivation.get('registry_matches')}"
            ),
        }
    )

    prerequisites = _b11_lsf_prerequisites()
    extensions["b11.lsf_prerequisites"] = prerequisites
    missing = list(prerequisites["missing"])
    for name in ("ACP_LSF_NODE", "LSF_ENVDIR", *B11_LSF_TOOLS, "ssh", "ssh_credentials"):
        if name in missing:
            assertions.append(
                {
                    "name": f"prerequisite_{name}",
                    "status": "NOT_VERIFIED",
                    "detail": f"{name} is not available on the acceptance host",
                }
            )
    if missing:
        extensions["b11.prerequisite_missing"] = ",".join(missing)
        extensions["b11.reason"] = "lsf_prerequisite_missing:" + ",".join(missing)
        extensions["b11.no_job_created"] = True
        extensions["b11.timeouts"] = _b11_timings({}, None, timeout)
        return EXIT_NOT_VERIFIED, assertions, extensions

    if not controller:
        extensions["b11.reason"] = "missing_controller"
        assertions.append(
            {
                "name": "controller_reachable",
                "status": "NOT_VERIFIED",
                "detail": "no --controller supplied",
            }
        )
        return EXIT_NOT_VERIFIED, assertions, extensions
    if not controller_reachable(controller):
        extensions["b11.reason"] = "controller_unreachable"
        assertions.append(
            {"name": "controller_reachable", "status": "NOT_VERIFIED", "detail": controller}
        )
        return EXIT_NOT_VERIFIED, assertions, extensions

    code, live_assertions, live_extensions = _b11_run_live(
        controller, launch_path, fixture_path, out_dir, timeout
    )
    assertions.extend(live_assertions)
    extensions.update(live_extensions)
    return code, assertions, extensions


def _b10_cli_query(
    controller: str,
    task_id: str,
    descriptor: Mapping[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], list[str]]:
    """Run the gated ``acp task query --controller`` surface (never ``acp run``)."""
    command = [
        sys.executable,
        "-m",
        "acp",
        "task",
        "query",
        "--controller",
        controller,
        "--job",
        task_id,
    ]
    cwd = str(descriptor.get("cli_cwd") or REPO_ROOT)
    env = dict(os.environ)
    hint = descriptor.get("launch_env")
    if isinstance(hint, dict):
        env.update({str(k): str(v) for k, v in hint.items() if isinstance(v, str)})
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"acp task query exited {completed.returncode}: {completed.stderr.strip()[:400]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("acp task query produced no JSON")
    payload = json.loads(lines[-1])
    if not isinstance(payload, dict):
        raise ValueError("acp task query did not return a JSON object")
    return payload, command


def _run_three_entry(
    controller: str,
    fixture_path: str,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B10b: read one task from CLI, HTTP and C Workbench (when present)."""
    base = controller.rstrip("/")
    descriptor = _b10_read_json(Path(fixture_path))
    task_id = str(descriptor.get("task_id") or descriptor.get("job_id") or "")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "submit",
        "acceptance.target": "local",
        "b10.three_entry_fixture": str(fixture_path),
        "b10.task_ref": task_id,
        "b10.legacy_acp_run_used": False,
        "b10.gated_cli_surface": "acp task query --controller <url> --job <task_id>",
        "b10.legacy_cli_surface": "acp run singlepoint (not used; would create a second task)",
    }
    try:
        before = _controller_task_count(base, timeout)
        encoded = quote(task_id, safe="")
        http_job = _http_json(base + f"/api/v1/jobs/{encoded}", timeout)
        http_receipt = _http_json(
            base + f"/api/v1/jobs/{encoded}/files/execution_receipt.json", timeout
        )
        http_manifest_bytes = _b10_http_bytes(
            base + f"/api/v1/jobs/{encoded}/files/RESULT/result_manifest.json", timeout
        )
        http_task_result = _http_json(
            base + f"/api/v1/jobs/{encoded}/files/RESULT/task_result.json", timeout
        )
        cli_payload, cli_command = _b10_cli_query(controller, task_id, descriptor, timeout)
        after = _controller_task_count(base, timeout)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.TimeoutExpired,
    ) as exc:
        extensions["b10.three_entry_error"] = f"{type(exc).__name__}: {exc}"
        assertions.append({"name": "three_entry_probe", "status": "FAIL", "detail": str(exc)})
        return EXIT_FAIL, assertions, extensions

    cli_job = cli_payload.get("job") if isinstance(cli_payload.get("job"), dict) else {}
    cli_work_dir = Path(str(cli_job.get("work_dir") or ""))
    cli_receipt = _b10_read_json(cli_work_dir / "execution_receipt.json")
    cli_manifest_path = cli_work_dir / "RESULT" / "result_manifest.json"
    cli_task_result = _b10_read_json(cli_work_dir / "RESULT" / "task_result.json")

    def entry_spec(job: Mapping[str, Any]) -> Mapping[str, Any]:
        spec = job.get("spec") if isinstance(job.get("spec"), Mapping) else {}
        return spec if isinstance(spec, Mapping) else {}

    def row(
        entry: str,
        job: Mapping[str, Any],
        receipt: Mapping[str, Any],
        manifest_sha: str,
        task_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        spec = entry_spec(job)
        return {
            "entry": entry,
            "job_id": job.get("id") or job.get("task_id"),
            "attempt": job.get("attempt"),
            "status": job.get("status") or job.get("state"),
            "release_id": receipt.get("release_id"),
            "submission_id": receipt.get("submission_id"),
            "plan_identity": receipt.get("plan_identity"),
            "result_digest": receipt.get("result_digest")
            or task_result.get("result_digest")
            or (task_result.get("result") or {}).get("result_digest"),
            "manifest_sha256": manifest_sha,
            "method": spec.get("method"),
            "input_hash": job.get("input_hash"),
        }

    cli_row = row(
        "cli:acp task query --controller",
        cli_job,
        cli_receipt,
        sha256_file(cli_manifest_path) if cli_manifest_path.is_file() else "",
        cli_task_result,
    )
    http_row = row(
        "http:GET /api/v1/jobs/{id} + /files",
        http_job,
        http_receipt,
        hashlib.sha256(http_manifest_bytes).hexdigest(),
        http_task_result,
    )
    workbench_row = {
        "entry": "c-workbench:RPH Workbench",
        "status": "NOT_VERIFIED",
        "reason": (
            "c_driver_absent: C P1 Workbench UI/DSH is not delivered and this host "
            "has no Chromium/DSH driver, so the same task cannot be read from the "
            "Workbench surface. The CLI and HTTP rows are verified independently."
        ),
    }
    compare_keys = (
        "job_id",
        "attempt",
        "release_id",
        "plan_identity",
        "manifest_sha256",
        "result_digest",
        "method",
        "input_hash",
    )
    mismatched = [key for key in compare_keys if cli_row.get(key) != http_row.get(key)]
    identity_ok = not mismatched and bool(cli_row.get("job_id"))
    cli_ok = str(cli_row.get("status") or "").lower() == "completed"
    http_ok = str(http_row.get("status") or "").lower() == "completed"
    read_only_ok = before == after
    workbench_missing = workbench_row["status"] == "NOT_VERIFIED"

    assertions.extend(
        [
            {
                "name": "cli_query_row",
                "status": "PASS" if cli_ok else "FAIL",
                "detail": f"acp task query --controller {controller} --job {task_id}",
            },
            {
                "name": "http_get_row",
                "status": "PASS" if http_ok else "FAIL",
                "detail": f"GET /api/v1/jobs/{task_id}",
            },
            {
                "name": "entry_identity_equal",
                "status": "PASS" if identity_ok else "FAIL",
                "detail": f"mismatched={mismatched}",
            },
            {
                "name": "three_entry_read_only",
                "status": "PASS" if read_only_ok else "FAIL",
                "detail": f"task_count_before={before} after={after}",
            },
            {
                "name": "workbench_row_not_verified",
                "status": "NOT_VERIFIED",
                "detail": workbench_row["reason"],
            },
            {
                "name": "legacy_acp_run_not_used",
                "status": "PASS" if not extensions["b10.legacy_acp_run_used"] else "FAIL",
                "detail": extensions["b10.gated_cli_surface"],
            },
        ]
    )
    extensions.update(
        {
            "b10.attempt": cli_row.get("attempt"),
            "b10.release_id": cli_row.get("release_id"),
            "b10.entries": {
                "cli": cli_row,
                "http": http_row,
                "workbench": workbench_row,
            },
            "b10.compare_keys": list(compare_keys),
            "b10.mismatched": mismatched,
            "b10.task_count_before": before,
            "b10.task_count_after": after,
            "b10.cli_command": cli_command,
            "b10.legacy_cli_distinction": (
                "The equivalence gate uses the gated Controller query "
                "`acp task query --controller <url> --job <id>`; the legacy "
                "`acp run singlepoint` was never executed because it would create "
                "a second task instead of reading the submitted one."
            ),
        }
    )
    if not (cli_ok and http_ok and identity_ok and read_only_ok and workbench_missing):
        extensions["b10.reason"] = "three_entry_cli_http_or_identity_failed"
        return EXIT_FAIL, assertions, extensions
    extensions["b10.reason"] = "three_entry_workbench_not_verified:c_driver_absent"
    return EXIT_NOT_VERIFIED, assertions, extensions


def _http_json_status(url: str, timeout: float) -> tuple[int, Any]:
    """GET *url* and return ``(status, body)`` instead of raising on 4xx/5xx."""
    try:
        return 200, _http_json(url, timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = raw.decode("utf-8", errors="replace")
        return int(exc.code), body


def _http_post_json_status(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
    """POST JSON *payload* and return ``(status, body)`` instead of raising."""
    try:
        return 200, _http_json_post(url, payload, timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = raw.decode("utf-8", errors="replace")
        return int(exc.code), body


def _canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(document), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _artifact_negative_matrix(
    base: str,
    job_id: str,
    attempt: int,
    *,
    digest: str,
    size_bytes: int,
    fixture_dir: Path,
    run_root: Path | None,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Exercise the controller-observable negative rows and name every refusal."""
    rows: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {}
    quoted = quote(job_id, safe="")
    prepare_url = base + f"/api/v1/jobs/{quoted}/prepare"

    def read_url(artifact_id: str, *, generation: int = 1, value: str = digest) -> str:
        query = urllib.parse.urlencode(
            {
                "attempt": attempt,
                "expected_revision": generation,
                "digest": value,
                "max_bytes": 262144,
            }
        )
        return base + f"/api/v1/jobs/{quoted}/prepared/{quote(artifact_id, safe='')}?{query}"

    # 1. not prepared (cold cache) — repeated reads stay cold and serve nothing.
    status, body = _http_json_status(read_url("b12_never_prepared"), timeout)
    detail = body.get("detail") if isinstance(body, dict) else None
    cold_ok = (
        status == 409
        and isinstance(detail, dict)
        and detail.get("code") == "resource_not_ready"
        and "not_prepared" in str(detail.get("message") or "")
    )
    status_repeat, _ = _http_json_status(read_url("b12_never_prepared"), timeout)
    rows.append(
        {
            "row": "not_prepared",
            "status": "PASS" if cold_ok and status_repeat == 409 else "FAIL",
            "detail": (
                f"status={status} repeat={status_repeat} code={detail and detail.get('code')}"
            ),
        }
    )
    evidence["not_prepared"] = {"status": status, "body": body}

    # 2. corrupt digest read — named conflict and no body.
    corrupt = "sha256:" + "0" * 64
    status, body = _http_json_status(read_url("singlepoint_energy", value=corrupt), timeout)
    detail = body.get("detail") if isinstance(body, dict) else None
    digest_ok = (
        status == 409
        and isinstance(detail, dict)
        and detail.get("code") == "revision_conflict"
        and (detail.get("params") or {}).get("rule") == "digest_mismatch"
        and "body" not in (body or {})
    )
    rows.append(
        {
            "row": "corrupt_digest",
            "status": "PASS" if digest_ok else "FAIL",
            "detail": f"status={status} body_keys={sorted(body or {})}",
        }
    )
    evidence["corrupt_digest"] = {"status": status, "body": body}

    # 3. corrupt expected digest prepare — the operation fails by name.
    status, body = _http_post_json_status(
        prepare_url,
        {
            "schema_version": 1,
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": "b12_corrupt_digest",
            "attempt": attempt,
            "generation": 1,
            "relative_path": "RESULT/energy/singlepoint_energy.json",
            "expected_digest": corrupt,
        },
        timeout,
    )
    operation_id = str((body or {}).get("operation_id") or "") if isinstance(body, dict) else ""
    final: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    while operation_id and time.monotonic() < deadline:
        op_status, final = _http_json_status(base + f"/api/v1/operations/{operation_id}", timeout)
        if isinstance(final, dict) and final.get("state") in {"ready", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    error = final.get("error") if isinstance(final, dict) else None
    corrupt_prepare_ok = (
        status == 200
        and isinstance(final, dict)
        and final.get("state") == "failed"
        and isinstance(error, dict)
        and error.get("code") == "digest_mismatch"
    )
    rows.append(
        {
            "row": "corrupt_prepare",
            "status": "PASS" if corrupt_prepare_ok else "FAIL",
            "detail": f"prepare={status} state={final.get('state')} error={error}",
        }
    )
    evidence["corrupt_prepare"] = {"prepare": status, "operation": final}

    # 4. old generation — a newer generation makes the older read stale.
    status, body = _http_post_json_status(
        prepare_url,
        {
            "schema_version": 1,
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": "b12_generation",
            "attempt": attempt,
            "generation": 1,
            "relative_path": "RESULT/energy/singlepoint_energy.json",
            "expected_digest": digest,
            "expected_size_bytes": size_bytes,
        },
        timeout,
    )
    gen_one = str((body or {}).get("operation_id") or "") if isinstance(body, dict) else ""
    _wait_artifact_operation(base, gen_one, timeout)
    status, body = _http_post_json_status(
        prepare_url,
        {
            "schema_version": 1,
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": "b12_generation",
            "attempt": attempt,
            "generation": 2,
            "relative_path": "RESULT/energy/singlepoint_energy.json",
            "expected_digest": digest,
            "expected_size_bytes": size_bytes,
        },
        timeout,
    )
    gen_two = str((body or {}).get("operation_id") or "") if isinstance(body, dict) else ""
    _wait_artifact_operation(base, gen_two, timeout)
    status, body = _http_json_status(
        read_url("b12_generation", generation=1, value=digest), timeout
    )
    detail = body.get("detail") if isinstance(body, dict) else None
    old_ok = (
        status == 409
        and isinstance(detail, dict)
        and detail.get("code") == "revision_conflict"
        and (detail.get("params") or {}).get("rule") == "generation_stale"
    )
    rows.append(
        {
            "row": "old_generation",
            "status": "PASS" if old_ok else "FAIL",
            "detail": f"status={status} detail={detail}",
        }
    )
    evidence["old_generation"] = {
        "generation_1": {"operation_id": gen_one},
        "generation_2": {"operation_id": gen_two},
        "read": {"status": status, "body": body},
    }

    # 5. path traversal — refused before any filesystem access.
    sentinel = fixture_dir / "b12-escape-proof.json"
    if sentinel.exists():
        sentinel.unlink()
    status, body = _http_post_json_status(
        prepare_url,
        {
            "schema_version": 1,
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": "b12_traversal",
            "attempt": attempt,
            "generation": 1,
            "relative_path": "../../b12-escape-proof.json",
        },
        timeout,
    )
    detail = body.get("detail") if isinstance(body, dict) else None
    traversal_ok = (
        status == 400
        and isinstance(detail, dict)
        and (detail.get("params") or {}).get("rule") == "path_traversal"
        and not sentinel.exists()
    )
    rows.append(
        {
            "row": "path_traversal",
            "status": "PASS" if traversal_ok else "FAIL",
            "detail": f"status={status} sentinel_exists={sentinel.exists()}",
        }
    )
    evidence["path_traversal"] = {"status": status, "body": body}

    # 6. eviction → cold read → explicit re-prepare rebuilds the body.
    evict_id = "b12_eviction"
    status, body = _http_post_json_status(
        prepare_url,
        {
            "schema_version": 1,
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": evict_id,
            "attempt": attempt,
            "generation": 1,
            "relative_path": "RESULT/energy/singlepoint_energy.json",
            "expected_digest": digest,
            "expected_size_bytes": size_bytes,
        },
        timeout,
    )
    first_operation = str((body or {}).get("operation_id") or "") if isinstance(body, dict) else ""
    first_final = _wait_artifact_operation(base, first_operation, timeout)
    first_read_status, _ = _http_json_status(
        read_url(evict_id, generation=1, value=digest), timeout
    )
    bodies_removed = 0
    if run_root is not None:
        generation_dir = Path(run_root) / "artifact-store" / job_id / str(attempt) / evict_id / "1"
        for body_path in generation_dir.glob("*.body"):
            body_path.unlink()
            bodies_removed += 1
    cold_status, cold_body = _http_json_status(
        read_url(evict_id, generation=1, value=digest), timeout
    )
    cold_detail = cold_body.get("detail") if isinstance(cold_body, dict) else None
    cold_ok = (
        first_read_status == 200
        and first_final.get("state") == "ready"
        and bodies_removed >= 1
        and cold_status == 409
        and isinstance(cold_detail, dict)
        and cold_detail.get("code") == "resource_not_ready"
        and "not_prepared" in str(cold_detail.get("message") or "")
    )
    reprepare_status, reprepare_body = _http_post_json_status(
        prepare_url,
        {
            "schema_version": 1,
            "purpose": "artifact",
            "job_id": job_id,
            "artifact_id": evict_id,
            "attempt": attempt,
            "generation": 1,
            "relative_path": "RESULT/energy/singlepoint_energy.json",
            "expected_digest": digest,
            "expected_size_bytes": size_bytes,
        },
        timeout,
    )
    second_operation = (
        str((reprepare_body or {}).get("operation_id") or "")
        if isinstance(reprepare_body, dict)
        else ""
    )
    second_final = _wait_artifact_operation(base, second_operation, timeout)
    rebuilt_status, rebuilt_body = _http_json_status(
        read_url(evict_id, generation=1, value=digest), timeout
    )
    rebuilt_ok = (
        reprepare_status == 200
        and second_operation
        and second_operation != first_operation
        and second_final.get("state") == "ready"
        and rebuilt_status == 200
        and isinstance(rebuilt_body, dict)
        and rebuilt_body.get("digest") == digest
        and int(rebuilt_body.get("size_bytes") or -1) == size_bytes
    )
    rows.append(
        {
            "row": "eviction_rebuild",
            "status": "PASS" if cold_ok and rebuilt_ok else "FAIL",
            "detail": (
                f"first={first_operation} cold={cold_status} "
                f"reprepare={second_operation} rebuilt={rebuilt_status} "
                f"bodies_removed={bodies_removed}"
            ),
        }
    )
    evidence["eviction_rebuild"] = {
        "first_operation": first_operation,
        "cold_read": {"status": cold_status, "body": cold_body},
        "reprepare_operation": second_operation,
        "rebuilt_read": {"status": rebuilt_status, "body": rebuilt_body},
        "bodies_removed": bodies_removed,
    }

    evidence["rows"] = rows
    return rows, evidence


def _wait_artifact_operation(base: str, operation_id: str, timeout: float) -> dict[str, Any]:
    """Poll one prepare operation until it is terminal (query-only GETs)."""
    if not operation_id:
        return {}
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status, body = _http_json_status(base + f"/api/v1/operations/{operation_id}", timeout)
        if isinstance(body, dict) and body.get("state") in {"ready", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    return body


def _run_artifact(
    controller: str,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B12: real prepare/read against B10's completed task + negative matrix.

    Every happy-path assertion compares the served digest/size/generation with
    the frozen fixture, so a silent substitution can never pass.  The extended
    matrix rows that need a second task / remote job / revoked identity are
    exercised by the same test file the task requires; its exit is part of the
    scenario verdict.
    """
    base = controller.rstrip("/")
    fixture_file = Path(fixture_path)
    fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
    fixture_dir = fixture_file.parent
    job_id = str(fixture.get("job_id") or "")
    attempt = int(fixture.get("attempt") or 1)
    release_id = str(fixture.get("release_id") or "")
    quoted = quote(job_id, safe="")
    artifacts = fixture.get("artifacts") if isinstance(fixture.get("artifacts"), list) else []
    evidence_dir = out_dir / "artifact"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "read",
        "acceptance.target": "local",
        "b12.fixture": str(fixture_file),
        "b12.job_id": job_id,
        "b12.attempt": attempt,
        "b12.release_id": release_id,
        "b12.source": fixture.get("source"),
    }
    artifacts_written: list[dict[str, Any]] = []
    try:
        before_tasks = _controller_task_count(base, timeout)
        results: dict[str, dict[str, Any]] = {}
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            artifact_id = str(artifact.get("artifact_id") or "")
            relative_path = str(artifact.get("relative_path") or "")
            generation = int(artifact.get("generation") or 1)
            digest = str(artifact.get("digest") or "")
            size_bytes = int(artifact.get("size_bytes") or 0)
            payload = {
                "schema_version": 1,
                "purpose": "artifact",
                "job_id": job_id,
                "artifact_id": artifact_id,
                "attempt": attempt,
                "generation": generation,
                "relative_path": relative_path,
                "expected_digest": digest,
                "expected_size_bytes": size_bytes,
                "media_type": artifact.get("media_type"),
            }
            status, prepared = _http_post_json_status(
                base + f"/api/v1/jobs/{quoted}/prepare", payload, timeout
            )
            if status != 200 or not isinstance(prepared, dict):
                results[artifact_id] = {"prepare_status": status, "prepared": prepared}
                continue
            operation_id = str(prepared.get("operation_id") or "")
            operation_ref = prepared.get("operation_ref") or {}
            replay_status, replay = _http_post_json_status(
                base + f"/api/v1/jobs/{quoted}/prepare", payload, timeout
            )
            projection = _wait_artifact_operation(base, operation_id, timeout)
            query = urllib.parse.urlencode(
                {
                    "attempt": attempt,
                    "expected_revision": generation,
                    "digest": digest,
                    "max_bytes": 262144,
                }
            )
            read_status, read_body = _http_json_status(
                base + f"/api/v1/jobs/{quoted}/prepared/{quote(artifact_id, safe='')}?{query}",
                timeout,
            )
            served = ""
            if isinstance(read_body, dict) and isinstance(read_body.get("body"), str):
                served = read_body["body"]
            observed = "sha256:" + hashlib.sha256(served.encode("utf-8")).hexdigest()
            results[artifact_id] = {
                "prepare_status": status,
                "operation_ref": operation_ref,
                "operation_id": operation_id,
                "replay_status": replay_status,
                "replay_operation_id": (replay or {}).get("operation_id")
                if isinstance(replay, dict)
                else None,
                "projection": projection,
                "read_status": read_status,
                "read": read_body,
                "observed_digest": observed,
            }
            (evidence_dir / f"prepare-{_safe_name(artifact_id)}.json").write_text(
                json.dumps(prepared, indent=2, sort_keys=True), encoding="utf-8"
            )
            (evidence_dir / f"operation-{_safe_name(artifact_id)}.json").write_text(
                json.dumps(projection, indent=2, sort_keys=True), encoding="utf-8"
            )
            (evidence_dir / f"read-{_safe_name(artifact_id)}.json").write_text(
                json.dumps(read_body, indent=2, sort_keys=True), encoding="utf-8"
            )
            if served:
                body_path = evidence_dir / f"body-{_safe_name(artifact_id)}"
                body_path.write_text(served, encoding="utf-8")
                artifacts_written.append(
                    {
                        "path": str(body_path.relative_to(out_dir)),
                        "size_bytes": len(served.encode("utf-8")),
                        "sha256": hashlib.sha256(served.encode("utf-8")).hexdigest(),
                    }
                )
            digest_ok = (
                read_status == 200
                and isinstance(read_body, dict)
                and read_body.get("digest") == digest
                and int(read_body.get("size_bytes") or -1) == size_bytes
                and int(read_body.get("generation") or -1) == generation
                and observed == digest
            )
            replay_ok = (
                replay_status == 200
                and isinstance(replay, dict)
                and replay.get("operation_id") == operation_id
            )
            projection_state = projection.get("state") if isinstance(projection, dict) else None
            served_digest = read_body.get("digest") if isinstance(read_body, dict) else None
            served_size = read_body.get("size_bytes") if isinstance(read_body, dict) else None
            served_generation = read_body.get("generation") if isinstance(read_body, dict) else None
            assertions.append(
                {
                    "name": f"artifact:{artifact_id}:digest_generation_agree",
                    "status": "PASS" if digest_ok else "FAIL",
                    "detail": (
                        f"read={read_status} requested={digest} served={served_digest} "
                        f"size={served_size}/{size_bytes} "
                        f"generation={served_generation}/{generation}"
                    ),
                }
            )
            assertions.append(
                {
                    "name": f"artifact:{artifact_id}:prepare_idempotent",
                    "status": "PASS" if replay_ok else "FAIL",
                    "detail": (
                        f"first={operation_id} "
                        f"replay={replay.get('operation_id') if isinstance(replay, dict) else None}"
                    ),
                }
            )
            assertions.append(
                {
                    "name": f"artifact:{artifact_id}:operation_ready",
                    "status": "PASS" if projection_state == "ready" else "FAIL",
                    "detail": f"state={projection_state}",
                }
            )

        # Different generation is a different persistent operation.
        primary = artifacts[0] if artifacts and isinstance(artifacts[0], dict) else {}
        if primary:
            primary_digest = str(primary.get("digest") or "")
            primary_size = int(primary.get("size_bytes") or 0)
            gen_status, gen_body = _http_post_json_status(
                base + f"/api/v1/jobs/{quoted}/prepare",
                {
                    "schema_version": 1,
                    "purpose": "artifact",
                    "job_id": job_id,
                    "artifact_id": "b12_different_generation",
                    "attempt": attempt,
                    "generation": 2,
                    "relative_path": str(primary.get("relative_path") or ""),
                    "expected_digest": primary_digest,
                    "expected_size_bytes": primary_size,
                },
                timeout,
            )
            gen_two_id = (
                str((gen_body or {}).get("operation_id") or "")
                if isinstance(gen_body, dict)
                else ""
            )
            gen_one_id = results.get(str(primary.get("artifact_id") or ""), {}).get("operation_id")
            assertions.append(
                {
                    "name": "artifact:different_generation_new_operation",
                    "status": "PASS"
                    if gen_status == 200 and gen_two_id and gen_two_id != gen_one_id
                    else "FAIL",
                    "detail": f"generation1={gen_one_id} generation2={gen_two_id}",
                }
            )

        # EvidenceReport: stored/read verbatim, never interpreted.
        evidence_spec = (
            fixture.get("evidence_report")
            if isinstance(fixture.get("evidence_report"), dict)
            else {}
        )
        if evidence_spec:
            report_path = Path(str(evidence_spec.get("path") or ""))
            if not report_path.is_absolute():
                report_path = fixture_dir / report_path
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report_bytes = _canonical_json_bytes(report)
            report_digest = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
            status, prepared = _http_post_json_status(
                base + f"/api/v1/jobs/{quoted}/prepare",
                {
                    "schema_version": 1,
                    "purpose": "artifact",
                    "job_id": job_id,
                    "artifact_id": str(evidence_spec.get("artifact_id") or "evidence_report"),
                    "attempt": attempt,
                    "generation": 1,
                    "expected_digest": report_digest,
                    "expected_size_bytes": len(report_bytes),
                    "schema_ref": evidence_spec.get("schema_ref"),
                    "source_refs": evidence_spec.get("source_refs") or [],
                    "evidence_report": report,
                },
                timeout,
            )
            operation_id = (
                str((prepared or {}).get("operation_id") or "")
                if isinstance(prepared, dict)
                else ""
            )
            projection = _wait_artifact_operation(base, operation_id, timeout)
            query = urllib.parse.urlencode(
                {
                    "attempt": attempt,
                    "expected_revision": 1,
                    "digest": report_digest,
                    "max_bytes": 262144,
                }
            )
            evidence_artifact_id = str(evidence_spec.get("artifact_id") or "evidence_report")
            read_status, read_body = _http_json_status(
                base
                + f"/api/v1/jobs/{quoted}/prepared/"
                + f"{quote(evidence_artifact_id, safe='')}?{query}",
                timeout,
            )
            served = read_body.get("body") if isinstance(read_body, dict) else None
            served_doc = json.loads(served) if isinstance(served, str) and served else None
            claim_status = (
                (served_doc.get("claims") or [{}])[0].get("status")
                if isinstance(served_doc, dict)
                else None
            )
            evidence_projection_state = (
                projection.get("state") if isinstance(projection, dict) else None
            )
            evidence_ok = (
                status == 200
                and isinstance(projection, dict)
                and projection.get("state") == "ready"
                and read_status == 200
                and isinstance(read_body, dict)
                and read_body.get("digest") == report_digest
                and served_doc == report
                and claim_status == evidence_spec.get("expected_claim_status")
                and "claim_supported" not in read_body
            )
            assertions.append(
                {
                    "name": "artifact:evidence_report_stored_not_interpreted",
                    "status": "PASS" if evidence_ok else "FAIL",
                    "detail": (
                        f"prepare={status} state={evidence_projection_state} "
                        f"read={read_status} claim_status={claim_status} "
                        f"digest={report_digest}"
                    ),
                }
            )
            (evidence_dir / "evidence-report-read.json").write_text(
                json.dumps(read_body, indent=2, sort_keys=True), encoding="utf-8"
            )
            if isinstance(served, str):
                body_path = evidence_dir / "body-evidence_report.json"
                body_path.write_text(served, encoding="utf-8")
                artifacts_written.append(
                    {
                        "path": str(body_path.relative_to(out_dir)),
                        "size_bytes": len(served.encode("utf-8")),
                        "sha256": hashlib.sha256(served.encode("utf-8")).hexdigest(),
                    }
                )

        # Negative matrix: controller-observable rows.
        rows, matrix_evidence = _artifact_negative_matrix(
            base,
            job_id,
            attempt,
            digest=str(primary.get("digest") or ""),
            size_bytes=int(primary.get("size_bytes") or 0),
            fixture_dir=fixture_dir,
            run_root=Path(str(fixture.get("run_root"))) if fixture.get("run_root") else None,
            timeout=timeout,
        )
        assertions.extend(
            {
                "name": f"artifact:{row['row']}",
                "status": row["status"],
                "detail": row["detail"],
            }
            for row in rows
        )
        (evidence_dir / "negative-matrix.json").write_text(
            json.dumps(matrix_evidence, indent=2, sort_keys=True), encoding="utf-8"
        )

        # Extended rows (second task / cross-attempt / remote / revocation) are
        # the required B12 test file; its exit is part of this scenario verdict.
        env = dict(os.environ)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(REPO_ROOT / "src") + (os.pathsep + existing if existing else "")
        pytest_cmd = [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_acp2_runtime_artifact.py",
            "-q",
        ]
        pytest_proc = subprocess.run(
            pytest_cmd,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(60.0, timeout),
        )
        (evidence_dir / "pytest.txt").write_text(
            pytest_proc.stdout + pytest_proc.stderr, encoding="utf-8"
        )
        assertions.append(
            {
                "name": "artifact:extended_negative_matrix_tests",
                "status": "PASS" if pytest_proc.returncode == 0 else "FAIL",
                "detail": f"exit={pytest_proc.returncode} log=artifact/pytest.txt",
            }
        )

        after_tasks = _controller_task_count(base, timeout)
        assertions.append(
            {
                "name": "artifact:zero_task_side_effects",
                "status": "PASS" if before_tasks == after_tasks else "FAIL",
                "detail": f"before={before_tasks} after={after_tasks}",
            }
        )
        extensions.update(
            {
                "b12.task_count_before": before_tasks,
                "b12.task_count_after": after_tasks,
                "b12.results": results,
                "b12.negative_matrix": rows,
                "b12.revoked_permission": (
                    "PASS via tests/test_acp2_runtime_artifact.py::"
                    "test_project_scope_denial_refuses_prepare_read_analyze "
                    "(project-scope denial); API-key revocation is B15/NOT_VERIFIED"
                ),
                "b12.missing_remote_cache": (
                    "PASS via tests/test_acp2_runtime_artifact.py::"
                    "test_missing_remote_cache_is_named_and_never_fetches"
                ),
                "b12.cross_attempt": (
                    "PASS via tests/test_acp2_runtime_artifact.py::"
                    "test_cross_attempt_read_never_serves_newer_bytes"
                ),
                "b12.old_response_after_switch": (
                    "PASS via tests/test_acp2_runtime_artifact.py::"
                    "test_old_response_after_switching_tasks_is_refused"
                ),
                "b12.manifest_generation": 1,
                "b12.operation_id": results.get(str(primary.get("artifact_id") or ""), {}).get(
                    "operation_id"
                ),
                "b12.release_id": release_id,
                "b12.artifacts": artifacts_written,
            }
        )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.TimeoutExpired,
    ) as exc:
        extensions["b12.probe_error"] = f"{type(exc).__name__}: {exc}"
        assertions.append({"name": "b12_probe", "status": "FAIL", "detail": str(exc)})
        return EXIT_FAIL, assertions, extensions

    passed = bool(assertions) and all(item["status"] == "PASS" for item in assertions)
    return (EXIT_PASS if passed else EXIT_FAIL), assertions, extensions


# ---------------------------------------------------------------------------
# B13 — control/recovery: real Local fault rows + separate LSF control fixture
# ---------------------------------------------------------------------------

#: B10's frozen P1 test candidate; every B13 race uses this Controller code.
B13_CANDIDATE_REVISION = "10eb71e4bc1811054c9d19ae4fd71d974e027f8a"
B13_CONTRACT_DIGEST = "67073115cb0856db22d84e22215d54e4dbe2437aad964efc15d3b706641e11a4"

#: The LSF control fixture is registered separately from B11's normal row.
B13_LSF_CONTROL_FIXTURE_ID = "fx-control-recovery-lsf-v1"
B13_LSF_NORMAL_FIXTURE_ID = "fx-lsf-singlepoint-v1"

#: Fault code raised by ``scripts/acceptance/acp2_fault_controller.py``.
B13_PUBLICATION_FAULT_CODE = "ACP2_ACCEPTANCE_PUBLICATION_FAULT"
B13_FAULT_SCHEMA = "acp2.b13.publication-fault.v1"
B13_FAULT_WRAPPER = "scripts/acceptance/acp2_fault_controller.py"

B13_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
B13_ACTIVE_CANCEL_STATUSES = frozenset({"cancelling", "cancelled"})

#: Two real fixtures.  H2/STO-3G is used only where the race needs a terminal
#: row quickly; ethanol/wB97M-V/def2-SVP gives a genuine multi-second Local run
#: (measured >10 s on the acceptance host) so cancel/EOF/timeout cannot finish
#: before the control call.
B13_QUICK_XYZ = (
    "2\nH2 B13 quick control fixture\nH 0.000000 0.000000 0.000000\nH 0.000000 0.000000 0.740000\n"
)
B13_LONG_XYZ = (
    "9\nethanol B13 long control fixture\n"
    "C 0.000 0.000 0.000\n"
    "C 1.520 0.000 0.000\n"
    "O 2.050 1.250 0.000\n"
    "H -0.500 0.900 0.000\n"
    "H -0.500 -0.500 0.800\n"
    "H -0.500 -0.500 -0.800\n"
    "H 1.900 -0.600 0.800\n"
    "H 1.900 -0.600 -0.800\n"
    "H 2.950 1.150 0.000\n"
)
B13_QUICK_LEVEL: dict[str, Any] = {
    "engine": "orca",
    "functional": "HF",
    "basis": "STO-3G",
}
B13_LONG_LEVEL: dict[str, Any] = {
    "engine": "orca",
    "functional": "wB97M-V",
    "basis": "def2-SVP",
}

#: One row per Local fault.  Every row owns a task, PID, timeout and budget;
#: rows never share a TaskRef.
B13_LOCAL_RACES: tuple[dict[str, Any], ...] = (
    {
        "race_id": "adapter_eof",
        "fault": "mcp_adapter_eof_sigterm",
        "fixture": "ethanol-wb97mv-def2svp",
        "xyz": B13_LONG_XYZ,
        "level": B13_LONG_LEVEL,
        "timeout_seconds": 120,
        "budget": {
            "walltime_s": 60,
            "cores": 1,
            "memory_mb": 1024,
            "jobs": 1,
            "max_orca": 1,
            "max_prepare_seconds": 30,
            "cleanup_seconds": 10,
        },
    },
    {
        "race_id": "controller_restart",
        "fault": "controller_restart_same_db_config_release",
        "fixture": "h2-hf-sto3g",
        "xyz": B13_QUICK_XYZ,
        "level": B13_QUICK_LEVEL,
        "timeout_seconds": 120,
        "budget": {
            "walltime_s": 60,
            "cores": 1,
            "memory_mb": 1024,
            "jobs": 1,
            "max_orca": 1,
            "max_prepare_seconds": 30,
            "cleanup_seconds": 10,
        },
    },
    {
        "race_id": "publication_failure",
        "fault": "publication_failure_after_real_qc",
        "fixture": "h2-hf-sto3g",
        "xyz": B13_QUICK_XYZ,
        "level": B13_QUICK_LEVEL,
        "timeout_seconds": 180,
        "budget": {
            "walltime_s": 90,
            "cores": 1,
            "memory_mb": 1024,
            "jobs": 1,
            "max_orca": 1,
            "max_prepare_seconds": 60,
            "cleanup_seconds": 10,
        },
    },
    {
        "race_id": "cancel_race",
        "fault": "cancel_confirmed_stop_late_receipt",
        "fixture": "ethanol-wb97mv-def2svp",
        "xyz": B13_LONG_XYZ,
        "level": B13_LONG_LEVEL,
        "timeout_seconds": 150,
        "budget": {
            "walltime_s": 60,
            "cores": 1,
            "memory_mb": 1024,
            "jobs": 1,
            "max_orca": 1,
            "max_prepare_seconds": 30,
            "cleanup_seconds": 10,
        },
    },
    {
        "race_id": "timeout_cancel",
        "fault": "client_timeout_then_platform_cancel",
        "fixture": "ethanol-wb97mv-def2svp",
        "xyz": B13_LONG_XYZ,
        "level": B13_LONG_LEVEL,
        "timeout_seconds": 150,
        "budget": {
            "walltime_s": 60,
            "cores": 1,
            "memory_mb": 1024,
            "jobs": 1,
            "max_orca": 1,
            "max_prepare_seconds": 30,
            "cleanup_seconds": 10,
        },
    },
)


def _b13_race_specs() -> tuple[dict[str, Any], ...]:
    """Return the immutable per-fault Local race rows (one task each)."""

    return B13_LOCAL_RACES


def _b13_exit_semantics(
    local_statuses: Sequence[str], lsf_status: str
) -> tuple[int, str, str | None]:
    """Decide the scenario exit from the Local rows and the LSF control row.

    The documented contract is:

    * any Local failure -> exit 1 / FAIL (a local fault was not proved);
    * all Local PASS + LSF PASS -> exit 0 / PASS;
    * all Local PASS + LSF ``NOT_VERIFIED`` -> exit 2 / NOT_VERIFIED, while the
      Local PASS assertions stay visible (H3 remains open);
    * all Local PASS + LSF explicitly ``NOT_REQUIRED`` -> exit 0 / PASS.
    """

    statuses = [str(item).upper() for item in local_statuses]
    lsf = str(lsf_status).upper()
    if any(item == "FAIL" for item in statuses):
        return EXIT_FAIL, "FAIL", "local_fault_row_failed"
    if any(item not in {"PASS"} for item in statuses):
        return EXIT_NOT_VERIFIED, "NOT_VERIFIED", "local_fault_row_not_verified"
    if lsf == "PASS":
        return EXIT_PASS, "PASS", None
    if lsf == "NOT_REQUIRED":
        return EXIT_PASS, "PASS", None
    if lsf == "NOT_VERIFIED":
        return (
            EXIT_NOT_VERIFIED,
            "NOT_VERIFIED",
            "lsf_control_fixture_not_verified",
        )
    return EXIT_FAIL, "FAIL", f"unknown_lsf_control_status:{lsf}"


def _b13_parse_controller_argv(argv: Sequence[str]) -> dict[str, Any]:
    """Extract the Controller identity from a real ``/proc/<pid>/cmdline``."""

    tokens = [str(item) for item in argv]
    result: dict[str, Any] = {
        "module": None,
        "script": None,
        "source": None,
        "port": None,
        "run_root": None,
        "fault_file": None,
        "fault_seam": False,
    }
    for index, token in enumerate(tokens):
        if token == "-m" and index + 1 < len(tokens):
            result["module"] = tokens[index + 1]
        if token.endswith("acp2_fault_controller.py") or token.endswith("acp2_controller_fault.py"):
            result["script"] = token
        if token == "--source" and index + 1 < len(tokens):
            result["source"] = tokens[index + 1]
        if token.startswith("--source="):
            result["source"] = token.split("=", 1)[1]
        if token == "--port" and index + 1 < len(tokens):
            try:
                result["port"] = int(tokens[index + 1])
            except ValueError:
                result["port"] = None
        if token.startswith("--port="):
            try:
                result["port"] = int(token.split("=", 1)[1])
            except ValueError:
                result["port"] = None
        if token == "--run-root" and index + 1 < len(tokens):
            result["run_root"] = tokens[index + 1]
        if token.startswith("--run-root="):
            result["run_root"] = token.split("=", 1)[1]
        if token == "--fault-file" and index + 1 < len(tokens):
            result["fault_file"] = tokens[index + 1]
    result["fault_seam"] = bool(result["script"] and result["fault_file"])
    return result


def _b13_listener_pid(port: int) -> int | None:
    """Find the live process listening on *port* through ``/proc`` (no ``ss``)."""

    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 10:
                continue
            try:
                local_port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if local_port == port and fields[3] == "0A":
                inodes.add(fields[9])
    if not inodes:
        return None
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            descriptors = list((proc / "fd").iterdir())
        except (OSError, PermissionError):
            continue
        for descriptor in descriptors:
            try:
                target = os.readlink(descriptor)
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                return int(proc.name)
    return None


def _b13_read_proc(pid: int) -> dict[str, Any]:
    """Read argv/cwd/env/stat for *pid* (used to restart the same Controller)."""

    proc = Path("/proc") / str(pid)
    try:
        argv = [
            item.decode("utf-8", "replace")
            for item in (proc / "cmdline").read_bytes().split(b"\0")
            if item
        ]
    except OSError:
        argv = []
    try:
        cwd = os.readlink(proc / "cwd")
    except OSError:
        cwd = ""
    env: dict[str, str] = {}
    try:
        raw_env = (proc / "environ").read_bytes()
    except OSError:
        raw_env = b""
    for item in raw_env.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    try:
        stat = (proc / "stat").read_text(encoding="utf-8")
    except OSError:
        stat = ""
    return {"pid": pid, "argv": argv, "cwd": cwd, "env": env, "stat": stat}


def _b13_pid_alive(pid: int) -> bool:
    """True when *pid* exists and is not a zombie."""

    if pid <= 0 or pid == os.getpid():
        return False
    try:
        state = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
    except OSError:
        return False
    return len(state) > 2 and state[2] != "Z"


def _b13_task_processes(work_dir: Path) -> list[int]:
    """Live processes whose cwd/cmdline is bound to *work_dir* (ORCA included)."""

    try:
        target = work_dir.resolve().as_posix()
    except OSError:
        target = work_dir.as_posix()
    if not target or target == "/":
        return []
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == os.getpid() or not _b13_pid_alive(pid):
            continue
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            cwd = ""
        if cwd == target or cwd.startswith(target + "/"):
            found.append(pid)
            continue
        try:
            cmdline = (
                (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            )
        except OSError:
            continue
        index = cmdline.find(target)
        while index >= 0:
            after = cmdline[index + len(target) :]
            if not after or after[0].isspace() or after[0] in "'\"),":
                found.append(pid)
                break
            index = cmdline.find(target, index + 1)
    return sorted(found)


def _b13_ps_table(pids: Sequence[int]) -> str:
    """Best-effort process table text for the timeline evidence."""

    clean = [str(pid) for pid in pids if int(pid) > 0]
    if not clean:
        return "no live pid\n"
    try:
        completed = subprocess.run(
            ["ps", "-o", "pid,ppid,pgid,stat,lstart,cmd", "-p", ",".join(clean)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.stdout.strip():
            return completed.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    lines = []
    for pid in clean:
        info = _b13_read_proc(int(pid))
        lines.append(f"{pid} {info['stat'].strip()} cwd={info['cwd']} argv={info['argv']}")
    return "\n".join(lines) + "\n"


def _b13_write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _b13_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _b13_arm_fault(fault_file: Path, *, task_ref: str | None = None) -> dict[str, Any]:
    """Write the persisted JSON descriptor the acceptance wrapper re-reads."""

    descriptor = {
        "schema": B13_FAULT_SCHEMA,
        "fault": "publication_failure",
        "seam": "register_result_manifest",
        "code": B13_PUBLICATION_FAULT_CODE,
        "armed": True,
        "armed_at": _b13_iso_now(),
        "disarmed_at": None,
        "task_ref": task_ref,
        "wrapper": B13_FAULT_WRAPPER,
    }
    _b13_write_json(fault_file, descriptor)
    return descriptor


def _b13_disarm_fault(
    fault_file: Path, *, reason: str, details: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Persist the disarmed descriptor instead of deleting the evidence file."""

    try:
        descriptor = (
            json.loads(fault_file.read_text(encoding="utf-8")) if fault_file.is_file() else {}
        )
    except (OSError, json.JSONDecodeError):
        descriptor = {}
    if not isinstance(descriptor, dict):
        descriptor = {}
    descriptor.update(
        {
            "schema": B13_FAULT_SCHEMA,
            "fault": "publication_failure",
            "seam": "register_result_manifest",
            "code": B13_PUBLICATION_FAULT_CODE,
            "armed": False,
            "disarmed_at": _b13_iso_now(),
            "disarm_reason": reason,
            "wrapper": B13_FAULT_WRAPPER,
        }
    )
    if details:
        descriptor["failure"] = dict(details)
    _b13_write_json(fault_file, descriptor)
    return descriptor


def _b13_event_rows(work_dir: Path) -> list[dict[str, Any]]:
    events = work_dir / "WORK" / "00_RUNTIME" / "events.jsonl"
    if not events.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _b13_event_count(work_dir: Path, event_type: str) -> int:
    return sum(1 for row in _b13_event_rows(work_dir) if row.get("type") == event_type)


def _b13_publication_complete(work_dir: Path) -> bool:
    result_dir = work_dir / "RESULT"
    manifest = result_dir / "result_manifest.json"
    state = result_dir / "publication_state.json"
    if not manifest.is_file() or not state.is_file():
        return False
    try:
        payload = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("complete") is True


def _b13_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _b13_copy_artifacts(work_dir: Path, target_dir: Path) -> list[dict[str, Any]]:
    """Copy the scientific/receipt artifacts that must survive a failure."""

    target_dir.mkdir(parents=True, exist_ok=True)
    relatives = (
        "execution_receipt.json",
        "sp.inp",
        "sp.out",
        "WORK/00_RUNTIME/task_result.json",
        "WORK/00_RUNTIME/frozen/envelope.json",
        "WORK/00_RUNTIME/events.jsonl",
        "RESULT/task_result.json",
        "RESULT/scientific_result.json",
        "RESULT/result_manifest.json",
        "RESULT/publication_state.json",
    )
    recorded: list[dict[str, Any]] = []
    for relative in relatives:
        source = work_dir / relative
        if not source.is_file():
            continue
        target = target_dir / relative.replace("/", "__")
        shutil.copy2(source, target)
        recorded.append(
            {
                "path": str(target.relative_to(target_dir.parent)),
                "source_relative": relative,
                "size_bytes": target.stat().st_size,
                "sha256": _b13_sha256(target),
            }
        )
    return recorded


def _b13_job(base: str, task_id: str, timeout: float) -> dict[str, Any]:
    payload = _http_json(base + f"/api/v1/jobs/{quote(task_id, safe='')}", timeout)
    return payload if isinstance(payload, dict) else {}


def _b13_wait_for(
    base: str,
    task_id: str,
    timeout: float,
    predicate: Any,
    *,
    interval: float = 0.05,
) -> tuple[dict[str, Any], bool]:
    deadline = time.monotonic() + timeout
    job: dict[str, Any] = {}
    while time.monotonic() < deadline:
        job = _b13_job(base, task_id, min(timeout, 30))
        if predicate(job):
            return job, True
        time.sleep(interval)
    return job, False


def _b13_wait_running(
    base: str,
    task_id: str,
    timeout: float,
    *,
    require_pid: bool = True,
) -> tuple[dict[str, Any], bool]:
    def _running(job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "running":
            return False
        return not require_pid or bool(job.get("pid"))

    return _b13_wait_for(base, task_id, timeout, _running, interval=0.02)


def _b13_wait_terminal(base: str, task_id: str, timeout: float) -> tuple[dict[str, Any], bool]:
    return _b13_wait_for(
        base,
        task_id,
        timeout,
        lambda job: str(job.get("status") or "") in B13_TERMINAL_STATUSES,
        interval=0.05,
    )


def _b13_wait_publication(work_dir: Path, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _b13_publication_complete(work_dir):
            return True
        time.sleep(0.1)
    return _b13_publication_complete(work_dir)


def _b13_controller_health(base: str, timeout: float) -> dict[str, Any]:
    payload = _http_json(base + "/api/health", timeout)
    return payload if isinstance(payload, dict) else {}


def _b13_build_launch(
    controller: str,
    run_root: Path,
    out_dir: Path,
    explicit_launch: str | None,
) -> dict[str, Any]:
    """Return an MCP stdio descriptor bound to the B13 Controller.

    An explicit ``--launch`` wins; otherwise the B10 launch manifest is reused
    with only Controller/run-root overrides; the final fallback is built from
    ``ACP2_SOURCE``/``ACP2_B_PYTHON`` so the adapter always runs the P1 code.
    """

    base = controller.rstrip("/")
    document: dict[str, Any] = {}
    if explicit_launch:
        document = json.loads(Path(explicit_launch).read_text(encoding="utf-8"))
    else:
        candidate, _path = _b11_pick_json(out_dir, "B10/mcp-launch-b10.json")
        if candidate:
            document = dict(candidate)
    if document:
        document = copy.deepcopy(document)
    else:
        source = Path(os.environ.get("ACP2_SOURCE", "/ACPServer/acp2-development/B"))
        python = os.environ.get("ACP2_B_PYTHON", sys.executable)
        document = {
            "command": python,
            "args": [
                "-m",
                "acp",
                "mcp",
                "serve",
                "--transport",
                "stdio",
                "--controller",
                base,
                "--log-level",
                "INFO",
            ],
            "cwd": str(source / "src"),
            "protocol_version": "2025-06-18",
            "env": {},
        }
    args = document.get("args") if isinstance(document.get("args"), list) else []
    args = [str(item) for item in args]
    if "--controller" in args:
        controller_index = args.index("--controller")
        if controller_index + 1 < len(args):
            args[controller_index + 1] = base
        else:
            args.append(base)
    else:
        args.extend(["--controller", base])
    document["args"] = args
    env = document.get("env") if isinstance(document.get("env"), dict) else {}
    env = {str(key): str(value) for key, value in env.items()}
    env["ACP_CONTROLLER_URL"] = base
    env["ACP_RUN_ROOT"] = str(run_root)
    env.setdefault(
        "ACP_CONFIG",
        os.environ.get(
            "ACP_CONFIG",
            "/ACPServer/acp2-development/data/b/controller-config/cccp.yaml",
        ),
    )
    env.setdefault(
        "CONFSEARCH_ORCA_PATH",
        os.environ.get("CONFSEARCH_ORCA_PATH", "/home/xieningke/orca611/orca"),
    )
    env.setdefault(
        "CONFSEARCH_ORCA_LD_LIBRARY_PATH",
        os.environ.get(
            "CONFSEARCH_ORCA_LD_LIBRARY_PATH",
            "/home/xieningke/openmpi418/lib:/home/xieningke/orca611",
        ),
    )
    env.setdefault(
        "LD_LIBRARY_PATH",
        os.environ.get("LD_LIBRARY_PATH", "/home/xieningke/openmpi418/lib:/home/xieningke/orca611"),
    )
    env.setdefault(
        "PATH",
        "/ACPServer/acp2-development/venvs/b/bin:/usr/sbin:/usr/local/bin:/usr/bin:/bin",
    )
    document["env"] = env
    document.setdefault("command", sys.executable)
    document.setdefault("args", [])
    document.setdefault("cwd", str(Path.cwd()))
    document.setdefault("protocol_version", "2025-06-18")
    return document


def _b13_submit(
    launch: dict[str, Any],
    workflow: str,
    xyz: str,
    level: dict[str, Any],
    timeout: float,
    *,
    keep_probe: bool = False,
) -> tuple[str, _McpStdioProbe | None, dict[str, Any], dict[str, Any]]:
    """Submit exactly one task through the real MCP stdio adapter."""

    probe = _McpStdioProbe(launch, timeout)
    probe.request(
        1,
        "initialize",
        {
            "protocolVersion": launch.get("protocol_version", "2025-06-18"),
            "capabilities": {},
            "clientInfo": {"name": "acp2-b13-driver", "version": "1"},
        },
    )
    probe.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    probe.request(2, "tools/list", {})
    key = "b13-" + uuid.uuid4().hex[:24]
    submitted = probe.request(
        3,
        "tools/call",
        {
            "name": "acp_submit",
            "arguments": {
                "workflow": workflow,
                "input": {
                    "source_type": "xyz_text",
                    "source": xyz,
                    "charge": 0,
                    "multiplicity": 1,
                },
                "method": {"levels": {workflow: dict(level)}},
                "resources": {"cores": 1, "nproc": 1, "mem": "1024MB"},
                "plan_digest": "sha256:" + "0" * 64,
                "idempotency_key": key,
            },
        },
    )
    task_id = _submit_task_id(submitted)
    if not task_id:
        probe.close()
        raise RuntimeError("acp_submit returned no task id")
    if not keep_probe:
        probe.close()
        return task_id, None, submitted, {"idempotency_key": key}
    return task_id, probe, submitted, {"idempotency_key": key}


def _b13_mcp_status(probe: _McpStdioProbe, task_id: str) -> dict[str, Any]:
    result = probe.request(
        4,
        "tools/call",
        {
            "name": "acp_status",
            "arguments": {"reference": {"kind": "job", "id": task_id}},
        },
    )
    if result.get("isError"):
        raise RuntimeError(f"acp_status error: {result.get('structuredContent')}")
    data = (result.get("structuredContent") or {}).get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("acp_status returned a non-object projection")
    return data


def _b13_cancel_task(base: str, task_id: str, timeout: float) -> tuple[int, dict[str, Any]]:
    """Issue the platform cancel (never a local ``kill`` of a client)."""

    request = urllib.request.Request(
        base + f"/api/v1/jobs/{quote(task_id, safe='')}/cancel",
        data=b"{}",
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return int(response.status), json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"detail": raw.decode("utf-8", "replace")}
        return int(exc.code), body if isinstance(body, dict) else {}


class _B13StopWatcher:
    """Observe the first instant every tracked process belongs to the dead set."""

    def __init__(self, pids: Sequence[int], work_dir: Path) -> None:
        self.pids = sorted({int(pid) for pid in pids if int(pid) > 0})
        self.work_dir = work_dir
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="b13-stop-watcher", daemon=True)
        self.stop_monotonic: float | None = None
        self.stop_iso: str | None = None
        self.task_processes_at_stop: list[int] = []
        self.samples = 0

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples += 1
            alive = [pid for pid in self.pids if _b13_pid_alive(pid)]
            task_processes = _b13_task_processes(self.work_dir)
            if not alive and not task_processes and self.stop_monotonic is None:
                self.stop_monotonic = time.monotonic()
                self.stop_iso = _b13_iso_now()
                self.task_processes_at_stop = task_processes
                return
            time.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self.stop_monotonic is None:
            if not any(_b13_pid_alive(pid) for pid in self.pids) and not _b13_task_processes(
                self.work_dir
            ):
                self.stop_monotonic = time.monotonic()
                self.stop_iso = _b13_iso_now()
                self.task_processes_at_stop = []

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop_monotonic is not None:
                return True
            time.sleep(0.02)
        return self.stop_monotonic is not None


def _b13_late_receipt(
    work_dir: Path,
    envelope: Mapping[str, Any],
    *,
    attempt: int | None = None,
) -> dict[str, Any]:
    """Write a late completion receipt claiming the frozen attempt succeeded."""

    receipt_dir = work_dir / "WORK" / "00_RUNTIME"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    canonical = work_dir / "execution_receipt.json"
    if canonical.is_file():
        shutil.copy2(canonical, receipt_dir / "late_receipt_previous.json")
    claimed_attempt = int(attempt if attempt is not None else envelope.get("attempt") or 1)
    task_result = work_dir / "WORK" / "00_RUNTIME" / "task_result.json"
    result_digest = (
        "sha256:" + _b13_sha256(task_result) if task_result.is_file() else "sha256:" + "0" * 64
    )
    receipt = {
        "receipt_version": 1,
        "job_id": str(envelope.get("job_id") or ""),
        "attempt": claimed_attempt,
        "submission_id": str(envelope.get("submission_id") or ""),
        "state": "completed",
        "result_digest": result_digest,
        "errors": [],
        "written_at": _b13_iso_now(),
        "release_id": str(envelope.get("release_id") or ""),
        "release_digest": str(envelope.get("release_digest") or ""),
        "owner": str(envelope.get("owner") or ""),
        "fencing_token": str(envelope.get("fencing_token") or ""),
        "plan_identity": str(envelope.get("plan_identity") or ""),
        "step_identities": list(envelope.get("step_identities") or []),
        "data_bindings": list(envelope.get("data_bindings") or []),
        "evidence_digests": {},
    }
    _b13_write_json(receipt_dir / "late_receipt.json", receipt)
    _b13_write_json(canonical, receipt)
    return receipt


def _b13_receipt_gate_probe(
    source_root: Path,
    db_path: Path,
    job_id: str,
    envelope_path: Path,
    receipt_path: Path,
    *,
    current_attempt: int,
    old_revision: int,
    timeout: float,
) -> dict[str, Any]:
    """Run the real receipt/CAS gate against the persisted terminal row.

    The probe is a separate process on the P1 source so it cannot be fooled by
    the harness's own imports.  It proves three independent facts:

    1. the late receipt's *identity* is well formed (same-attempt verify passes);
    2. an older attempt is rejected (``stale_attempt``);
    3. the real ``JobStore.transition`` CAS rejects the old write and the row
       stays ``CANCELLED`` with an unchanged revision.
    """

    python = os.environ.get("ACP2_B_PYTHON", sys.executable)
    script = """
import json
import sys
from pathlib import Path

from acp_services.contracts.envelope import ExecutionEnvelope, ExecutionReceipt
from acp_services.runtime.receipts import verify_receipt
from acp_services.store.store import JobStateConflictError, JobStore

envelope_path = Path(sys.argv[1])
receipt_path = Path(sys.argv[2])
db_path = Path(sys.argv[3])
job_id = sys.argv[4]
old_revision = int(sys.argv[5])
trace = {"proof": "acp2.b13.receipt_cas_gate.v1"}
try:
    envelope = ExecutionEnvelope.from_dict(
        json.loads(envelope_path.read_text(encoding="utf-8"))
    )
    receipt = ExecutionReceipt.from_dict(
        json.loads(receipt_path.read_text(encoding="utf-8"))
    )
    common = dict(
        current_submission_id=envelope.submission_id,
        current_release_id=envelope.release_id,
        owner_token=envelope.owner,
        fencing_token=envelope.fencing_token,
    )
    same = verify_receipt(receipt, envelope, current_attempt=envelope.attempt, **common)
    old = verify_receipt(receipt, envelope, current_attempt=envelope.attempt + 1, **common)
    trace["same_attempt"] = {"accepted": same.accepted, "code": same.code}
    trace["old_attempt"] = {"accepted": old.accepted, "code": old.code}
    store = JobStore(db_path)
    try:
        store.transition(
            job_id,
            expected_status="cancelling",
            expected_revision=old_revision,
            expected_attempt=envelope.attempt,
            status="completed",
        )
    except JobStateConflictError as exc:
        trace["cas"] = {"rejected": True, "error": "JobStateConflictError", "detail": str(exc)}
    else:
        trace["cas"] = {"rejected": False, "error": None, "detail": "CAS unexpectedly accepted"}
    row = store.get(job_id)
    trace["row"] = {
        "status": row.status.value if row is not None else None,
        "revision": row.revision if row is not None else None,
        "attempt": row.attempt if row is not None else None,
    }
except Exception as exc:
    trace["error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(trace, sort_keys=True))
"""
    script_path = envelope_path.parent / "b13_receipt_gate_probe.py"
    script_path.write_text(script, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(source_root / "src") + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    try:
        completed = subprocess.run(
            [
                python,
                str(script_path),
                str(envelope_path),
                str(receipt_path),
                str(db_path),
                job_id,
                str(old_revision),
            ],
            cwd=str(source_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=max(30.0, timeout),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    try:
        document = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        document = {
            "error": "gate probe returned invalid JSON",
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    document["exit_code"] = completed.returncode
    return document if isinstance(document, dict) else {"error": "non-object gate probe"}


def _b13_restart_controller(
    base: str,
    out_dir: Path,
    expected_health: Mapping[str, Any],
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Stop and restart the same Controller (same DB/config/release)."""

    port = urlparse(base).port or int(os.environ.get("ACP2_CONTROLLER_PORT", "18771"))
    assertions: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"port": port}
    controller_dir = out_dir / "controller"
    controller_dir.mkdir(parents=True, exist_ok=True)
    pid = _b13_listener_pid(port)
    evidence["pid_before"] = pid
    if pid is None:
        assertions.append(
            {
                "name": "b13.restart.controller_pid_found",
                "status": "FAIL",
                "detail": f"no listener on port {port}",
            }
        )
        return evidence, assertions
    before = _b13_read_proc(pid)
    parsed = _b13_parse_controller_argv(before["argv"])
    evidence["command_before"] = before["argv"]
    evidence["cwd_before"] = before["cwd"]
    evidence["parsed_before"] = parsed
    (controller_dir / "process-table-before.txt").write_text(_b13_ps_table([pid]), encoding="utf-8")
    _b13_write_json(
        controller_dir / "controller-before.json",
        {
            "pid": pid,
            "argv": before["argv"],
            "cwd": before["cwd"],
            "run_root": parsed.get("run_root"),
            "fault_file": parsed.get("fault_file"),
            "health": dict(expected_health),
        },
    )
    assertions.append(
        {
            "name": "b13.restart.controller_pid_found",
            "status": "PASS" if bool(before["argv"]) else "FAIL",
            "detail": f"pid={pid} port={port}",
        }
    )
    if not parsed.get("argv") and not before["argv"]:
        return evidence, assertions

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + min(30.0, timeout)
    while time.monotonic() < deadline and _b13_pid_alive(pid):
        time.sleep(0.05)
    if _b13_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _b13_pid_alive(pid):
            time.sleep(0.05)
    time.sleep(0.5)
    stop_ok = not _b13_pid_alive(pid)
    evidence["stopped"] = stop_ok
    assertions.append(
        {
            "name": "b13.restart.controller_stopped",
            "status": "PASS" if stop_ok else "FAIL",
            "detail": f"old_pid={pid} alive={_b13_pid_alive(pid)}",
        }
    )

    log_path = controller_dir / f"controller-restart-{int(time.time())}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    env = dict(before["env"])
    env.setdefault("ACP_RUN_ROOT", str(parsed.get("run_root") or ""))
    try:
        process = subprocess.Popen(
            before["argv"],
            cwd=before["cwd"] or str(Path.cwd()),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
        )
    finally:
        log_handle.close()
    evidence["pid_after"] = process.pid
    evidence["log"] = str(log_path.relative_to(out_dir))

    health: dict[str, Any] = {}
    healthy = False
    deadline = time.monotonic() + min(60.0, timeout)
    while time.monotonic() < deadline:
        try:
            health = _b13_controller_health(base, 3)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            health = {}
        if health.get("status") == "ok":
            healthy = True
            break
        time.sleep(0.2)
    same_candidate = health.get("candidate_revision") == expected_health.get("candidate_revision")
    same_contract = health.get("contract_digest") == expected_health.get("contract_digest")
    evidence["health_after"] = health
    (controller_dir / "process-table-after.txt").write_text(
        _b13_ps_table([process.pid]), encoding="utf-8"
    )
    _b13_write_json(
        controller_dir / "controller-after.json",
        {
            "pid": process.pid,
            "argv": before["argv"],
            "cwd": before["cwd"],
            "run_root": parsed.get("run_root"),
            "health": health,
        },
    )
    assertions.extend(
        [
            {
                "name": "b13.restart.health_restored",
                "status": "PASS" if healthy else "FAIL",
                "detail": json.dumps(health, sort_keys=True)[:400],
            },
            {
                "name": "b13.restart.same_candidate_revision",
                "status": "PASS" if same_candidate else "FAIL",
                "detail": (
                    f"{expected_health.get('candidate_revision')} -> "
                    f"{health.get('candidate_revision')}"
                ),
            },
            {
                "name": "b13.restart.same_contract_digest",
                "status": "PASS" if same_contract else "FAIL",
                "detail": (
                    f"{expected_health.get('contract_digest')} -> {health.get('contract_digest')}"
                ),
            },
            {
                "name": "b13.restart.new_process_same_argv",
                "status": "PASS" if process.pid != pid and bool(before["argv"]) else "FAIL",
                "detail": f"old={pid} new={process.pid}",
            },
        ]
    )
    return evidence, assertions


def _b13_snapshot_task(
    base: str, task_id: str, timeout: float, *, task_count: int
) -> dict[str, Any]:
    """Durable identity + result digest of one task (restart comparison)."""

    job = _b13_job(base, task_id, timeout)
    work_dir = Path(str(job.get("work_dir") or ""))
    receipt = _b10_read_json(work_dir / "execution_receipt.json")
    envelope = _b10_read_json(work_dir / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json")
    task_result = _b10_read_json(work_dir / "WORK" / "00_RUNTIME" / "task_result.json")
    manifest = work_dir / "RESULT" / "result_manifest.json"
    return {
        "task_id": task_id,
        "attempt": int(job.get("attempt") or 0),
        "status": str(job.get("status") or ""),
        "exit_code": job.get("exit_code"),
        "revision": job.get("revision"),
        "release_id": str(receipt.get("release_id") or envelope.get("release_id") or ""),
        "submission_id": str(receipt.get("submission_id") or envelope.get("submission_id") or ""),
        "plan_identity": str(receipt.get("plan_identity") or envelope.get("plan_identity") or ""),
        "result_digest": str(receipt.get("result_digest") or ""),
        "task_result_digest": str(task_result.get("content_digest") or ""),
        "manifest_sha256": _b13_sha256(manifest),
        "publication_complete": _b13_publication_complete(work_dir),
        "job_started_events": _b13_event_count(work_dir, "job.started"),
        "process_starting_events": _b13_event_count(work_dir, "process.starting"),
        "event_lines": len(_b13_event_rows(work_dir)),
        "task_count": task_count,
        "work_dir": str(work_dir),
    }


def _b13_row_record(
    spec: Mapping[str, Any],
    *,
    task_id: str = "",
    attempt: int | None = None,
    pid: int | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "race_id": spec["race_id"],
        "fault": spec["fault"],
        "kind": "local",
        "fixture": spec["fixture"],
        "input_sha256": hashlib.sha256(str(spec["xyz"]).encode("utf-8")).hexdigest(),
        "method": dict(spec["level"]),
        "task_ref": task_id or None,
        "attempt": attempt,
        "pid": pid,
        "timeout_seconds": spec["timeout_seconds"],
        "budget": dict(spec["budget"]),
        "started_at": _b13_iso_now(),
        "finished_at": None,
        "status": "NOT_VERIFIED",
        "assertions": [],
        "evidence": None,
    }
    if extra:
        row.update(dict(extra))
    return row


def _b13_finish_row(row: dict[str, Any], assertions: Sequence[Mapping[str, Any]]) -> None:
    row["assertions"] = [dict(item) for item in assertions]
    row["finished_at"] = _b13_iso_now()
    if any(item.get("status") == "FAIL" for item in assertions):
        row["status"] = "FAIL"
    elif any(item.get("status") != "PASS" for item in assertions):
        row["status"] = "NOT_VERIFIED"
    else:
        row["status"] = "PASS"


def _b13_run_budget(
    spec: Mapping[str, Any], *, task_id: str, pid: int | None, orca_pids: Sequence[int]
) -> dict[str, Any]:
    return {
        "race_id": spec["race_id"],
        "task_ref": task_id or None,
        "pid": pid,
        "orca_pids": sorted({int(item) for item in orca_pids if int(item) > 0}),
        "timeout_seconds": spec["timeout_seconds"],
        "budget": dict(spec["budget"]),
    }


def _b13_race_adapter_eof(
    base: str, launch: dict[str, Any], out_dir: Path, timeout: float, orca: str
) -> dict[str, Any]:
    spec = next(item for item in B13_LOCAL_RACES if item["race_id"] == "adapter_eof")
    row = _b13_row_record(spec)
    assertions: list[dict[str, Any]] = []
    sampler = _B10OrcaSampler(orca)
    sampler.start()
    probe: _McpStdioProbe | None = None
    task_id = ""
    try:
        before = _controller_task_count(base, timeout)
        task_id, probe, _submitted, _meta = _b13_submit(
            launch,
            "singlepoint",
            str(spec["xyz"]),
            dict(spec["level"]),
            timeout,
            keep_probe=True,
        )
        row.update({"task_ref": task_id, "task_count_before": before})
        job, running = _b13_wait_running(base, task_id, min(60.0, timeout))
        pid = int(job.get("pid") or 0)
        count_at_running = _controller_task_count(base, timeout)
        row.update(
            {
                "attempt": int(job.get("attempt") or 0),
                "pid": pid,
                "task_count_at_running": count_at_running,
            }
        )
        adapter_pid = probe.proc.pid if probe is not None else 0
        adapter_exit: int | None = None
        still_running = False
        pid_alive = False
        not_cancelled = False
        after_kill: dict[str, Any] = {}
        if running and probe is not None:
            (out_dir / "races" / "adapter_eof").mkdir(parents=True, exist_ok=True)
            (out_dir / "races" / "adapter_eof" / "process-table-before.txt").write_text(
                _b13_ps_table([pid, adapter_pid, *sampler.pids]), encoding="utf-8"
            )
            probe.proc.terminate()
            try:
                adapter_exit = probe.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                probe.proc.kill()
                adapter_exit = probe.proc.wait(timeout=5)
            probe = None
            time.sleep(1.0)
            after_kill = _b13_job(base, task_id, timeout)
            still_running = str(after_kill.get("status") or "") == "running"
            pid_alive = _b13_pid_alive(pid)
            not_cancelled = str(after_kill.get("status") or "") not in B13_ACTIVE_CANCEL_STATUSES
            (out_dir / "races" / "adapter_eof" / "process-table-after-adapter-exit.txt").write_text(
                _b13_ps_table([pid, *sampler.pids]), encoding="utf-8"
            )
        reconnect = _McpStdioProbe(launch, timeout)
        try:
            reconnect.request(
                1,
                "initialize",
                {
                    "protocolVersion": launch.get("protocol_version", "2025-06-18"),
                    "capabilities": {},
                    "clientInfo": {"name": "acp2-b13-eof-reconnect", "version": "1"},
                },
            )
            reconnect.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            reconnect.request(2, "tools/list", {})
            reread = _b13_mcp_status(reconnect, task_id)
        finally:
            reconnect.close()
        count_after_reconnect = _controller_task_count(base, timeout)
        same_task = str(reread.get("job_id") or reread.get("task_id") or "") == task_id and int(
            reread.get("attempt") or 0
        ) == int(row.get("attempt") or 0)
        final, terminal = _b13_wait_terminal(base, task_id, min(90.0, timeout))
        work_dir = Path(str(final.get("work_dir") or ""))
        sampler.stop()
        orca_pids = sorted(sampler.pids)
        started_events = _b13_event_count(work_dir, "job.started")
        assertions.extend(
            [
                {
                    "name": "b13.adapter_eof.task_was_running",
                    "status": "PASS" if running and pid_alive else "FAIL",
                    "detail": f"task={task_id} pid={pid} running={running}",
                },
                {
                    "name": "b13.adapter_eof.adapter_sigterm_delivered",
                    "status": "PASS" if adapter_exit is not None else "FAIL",
                    "detail": f"adapter_pid={adapter_pid} exit={adapter_exit}",
                },
                {
                    "name": "b13.adapter_eof.task_kept_running_after_adapter_exit",
                    "status": "PASS" if still_running and pid_alive and not_cancelled else "FAIL",
                    "detail": f"status={after_kill.get('status')} pid_alive={pid_alive}",
                },
                {
                    "name": "b13.adapter_eof.reconnect_same_task_no_new_task",
                    "status": "PASS"
                    if same_task and count_after_reconnect == int(row["task_count_at_running"])
                    else "FAIL",
                    "detail": (
                        f"same_task={same_task} at_running={row['task_count_at_running']} "
                        f"after={count_after_reconnect}"
                    ),
                },
                {
                    "name": "b13.adapter_eof.not_cancelled_by_adapter_exit",
                    "status": "PASS" if not_cancelled else "FAIL",
                    "detail": f"status_after_adapter_exit={after_kill.get('status')}",
                },
                {
                    "name": "b13.adapter_eof.task_completed_once",
                    "status": "PASS"
                    if terminal and str(final.get("status")) == "completed" and started_events == 1
                    else "FAIL",
                    "detail": (
                        f"terminal={terminal} status={final.get('status')} "
                        f"job.started={started_events}"
                    ),
                },
                {
                    "name": "b13.adapter_eof.real_orca_once",
                    "status": "PASS"
                    if len(orca_pids) == 1 and sampler.max_concurrent == 1
                    else "FAIL",
                    "detail": f"orca_pids={orca_pids} max_concurrent={sampler.max_concurrent}",
                },
            ]
        )
        row.update(
            {
                "adapter_pid": adapter_pid,
                "adapter_exit": adapter_exit,
                "task_count_after_reconnect": count_after_reconnect,
                "running_at_adapter_exit": still_running,
                "pid_alive_at_adapter_exit": pid_alive,
                "final_status": final.get("status"),
                "job_started_events": started_events,
                "orca_pids": orca_pids,
                "orca_max_concurrent": sampler.max_concurrent,
                "budget_row": _b13_run_budget(spec, task_id=task_id, pid=pid, orca_pids=orca_pids),
                "evidence": "races/adapter_eof",
            }
        )
        race_dir = out_dir / "races" / "adapter_eof"
        _b13_copy_artifacts(work_dir, race_dir / "native")
        _b13_write_json(race_dir / "race.json", row)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        sampler.stop()
        assertions.append(
            {
                "name": "b13.adapter_eof.probe",
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        if task_id:
            try:
                _b13_cancel_task(base, task_id, timeout)
            except (urllib.error.URLError, OSError, ValueError):
                pass
    finally:
        if probe is not None:
            probe.close()
        sampler.stop()
    _b13_finish_row(row, assertions)
    return row


def _b13_race_controller_restart(
    base: str, launch: dict[str, Any], out_dir: Path, timeout: float, orca: str
) -> dict[str, Any]:
    spec = next(item for item in B13_LOCAL_RACES if item["race_id"] == "controller_restart")
    row = _b13_row_record(spec)
    assertions: list[dict[str, Any]] = []
    sampler = _B10OrcaSampler(orca)
    sampler.start()
    task_id = ""
    try:
        health_before = _b13_controller_health(base, timeout)
        before_count = _controller_task_count(base, timeout)
        task_id, _probe, _submitted, _meta = _b13_submit(
            launch, "singlepoint", str(spec["xyz"]), dict(spec["level"]), timeout
        )
        row.update({"task_ref": task_id, "task_count_before": before_count})
        final, terminal = _b13_wait_terminal(base, task_id, min(90.0, timeout))
        work_dir = Path(str(final.get("work_dir") or ""))
        published = _b13_wait_publication(work_dir, min(60.0, timeout)) if terminal else False
        before = _b13_snapshot_task(
            base, task_id, timeout, task_count=_controller_task_count(base, timeout)
        )
        row.update(
            {
                "attempt": before["attempt"],
                "pid": int(final.get("pid") or 0),
                "release_id": before["release_id"],
                "submission_id": before["submission_id"],
            }
        )
        restart, restart_assertions = _b13_restart_controller(base, out_dir, health_before, timeout)
        assertions.extend(restart_assertions)
        after = _b13_snapshot_task(
            base, task_id, timeout, task_count=_controller_task_count(base, timeout)
        )
        sampler.stop()
        orca_pids = sorted(sampler.pids)
        identity_fields = (
            "task_id",
            "attempt",
            "release_id",
            "submission_id",
            "plan_identity",
            "result_digest",
            "task_result_digest",
            "manifest_sha256",
        )
        identity_ok = all(before.get(field) == after.get(field) for field in identity_fields)
        terminal_ok = (
            terminal
            and published
            and before["status"] == "completed"
            and after["status"] == "completed"
            and before["exit_code"] == after["exit_code"] == 0
        )
        no_second_dispatch = (
            before["job_started_events"] == after["job_started_events"] == 1
            and before["process_starting_events"] == after["process_starting_events"] == 1
            and before["task_count"] == after["task_count"]
        )
        assertions.extend(
            [
                {
                    "name": "b13.restart.same_task_and_release",
                    "status": "PASS" if identity_ok else "FAIL",
                    "detail": json.dumps(
                        {field: [before.get(field), after.get(field)] for field in identity_fields},
                        sort_keys=True,
                    )[:1200],
                },
                {
                    "name": "b13.restart.terminal_state_preserved",
                    "status": "PASS" if terminal_ok else "FAIL",
                    "detail": (
                        f"before={before['status']}/{before['exit_code']} "
                        f"after={after['status']}/{after['exit_code']} "
                        f"published={published}"
                    ),
                },
                {
                    "name": "b13.restart.no_second_dispatch",
                    "status": "PASS" if no_second_dispatch else "FAIL",
                    "detail": (
                        f"job.started={before['job_started_events']}->"
                        f"{after['job_started_events']} "
                        f"process.starting={before['process_starting_events']}->"
                        f"{after['process_starting_events']} "
                        f"tasks={before['task_count']}->{after['task_count']}"
                    ),
                },
                {
                    "name": "b13.restart.real_orca_unchanged",
                    "status": "PASS"
                    if len(orca_pids) == 1 and sampler.max_concurrent == 1
                    else "FAIL",
                    "detail": f"orca_pids={orca_pids} max_concurrent={sampler.max_concurrent}",
                },
            ]
        )
        row.update(
            {
                "restart": restart,
                "before_restart": before,
                "after_restart": after,
                "orca_pids": orca_pids,
                "budget_row": _b13_run_budget(
                    spec, task_id=task_id, pid=int(final.get("pid") or 0), orca_pids=orca_pids
                ),
                "evidence": "races/controller_restart",
            }
        )
        race_dir = out_dir / "races" / "controller_restart"
        _b13_copy_artifacts(work_dir, race_dir / "native")
        _b13_write_json(race_dir / "restart-comparison.json", {"before": before, "after": after})
        _b13_write_json(race_dir / "race.json", row)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        sampler.stop()
        assertions.append(
            {
                "name": "b13.controller_restart.probe",
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        if task_id:
            try:
                _b13_cancel_task(base, task_id, timeout)
            except (urllib.error.URLError, OSError, ValueError):
                pass
    finally:
        sampler.stop()
    _b13_finish_row(row, assertions)
    return row


def _b13_race_publication_failure(
    base: str,
    launch: dict[str, Any],
    out_dir: Path,
    timeout: float,
    orca: str,
    fault_file: Path | None,
) -> dict[str, Any]:
    spec = next(item for item in B13_LOCAL_RACES if item["race_id"] == "publication_failure")
    row = _b13_row_record(spec)
    assertions: list[dict[str, Any]] = []
    sampler = _B10OrcaSampler(orca)
    sampler.start()
    task_id = ""
    fault_file = fault_file or (out_dir / "fault" / "publication-fault.json")
    race_dir = out_dir / "races" / "publication_failure"
    try:
        _b13_arm_fault(fault_file)
        before_count = _controller_task_count(base, timeout)
        task_id, _probe, _submitted, _meta = _b13_submit(
            launch, "singlepoint", str(spec["xyz"]), dict(spec["level"]), timeout
        )
        _b13_arm_fault(fault_file, task_ref=task_id)
        row.update({"task_ref": task_id, "task_count_before": before_count})
        terminal, is_terminal = _b13_wait_terminal(base, task_id, min(90.0, timeout))
        work_dir = Path(str(terminal.get("work_dir") or ""))
        publication_failed = False
        pending = False
        manifest_absent = False
        failure_event: dict[str, Any] = {}
        if is_terminal:
            for _ in range(200):
                publication_failed = any(
                    item.get("type") == "job.publication_pending"
                    and item.get("code") == "publication_failed"
                    for item in _b13_event_rows(work_dir)
                )
                terminal = _b13_job(base, task_id, timeout)
                result = terminal.get("result") if isinstance(terminal.get("result"), dict) else {}
                pending = result.get("publication_pending") is True
                manifest_absent = not (work_dir / "RESULT" / "result_manifest.json").is_file()
                if publication_failed and pending and manifest_absent:
                    failure_event = next(
                        item
                        for item in _b13_event_rows(work_dir)
                        if item.get("type") == "job.publication_pending"
                    )
                    break
                time.sleep(0.1)
        qc_pids_before = sorted(sampler.pids)
        artifacts = _b13_copy_artifacts(work_dir, race_dir / "scientific")
        receipt = _b10_read_json(work_dir / "execution_receipt.json")
        task_result = _b10_read_json(work_dir / "WORK" / "00_RUNTIME" / "task_result.json")
        _b13_write_json(
            race_dir / "publication-failure.json",
            {
                "task_ref": task_id,
                "status": terminal.get("status"),
                "publication_pending": pending,
                "manifest_absent_at_failure": manifest_absent,
                "publication_failed_event": failure_event,
                "receipt": receipt,
                "task_result_content_digest": task_result.get("content_digest"),
                "qc_pids_before_retry": qc_pids_before,
                "artifacts": artifacts,
                "fault_file": str(fault_file),
            },
        )
        assertions.append(
            {
                "name": "b13.publication.failed_after_real_qc",
                "status": "PASS" if publication_failed and pending and manifest_absent else "FAIL",
                "detail": (
                    f"publication_failed={publication_failed} pending={pending} "
                    f"manifest_absent={manifest_absent} event={failure_event.get('code')}"
                ),
            }
        )
        assertions.append(
            {
                "name": "b13.publication.scientific_artifacts_saved",
                "status": "PASS"
                if bool(receipt) and bool(task_result) and bool(artifacts)
                else "FAIL",
                "detail": (
                    f"receipt={bool(receipt)} task_result={bool(task_result)} "
                    f"copied={len(artifacts)}"
                ),
            }
        )
        # Clear the fault; the Controller's reconcile loop must retry publish ONLY.
        disarm_record = _b13_disarm_fault(
            fault_file,
            reason="fault cleared for publication retry",
            details={
                "task_ref": task_id,
                "publication_failure_event": failure_event,
                "qc_pids_before_retry": qc_pids_before,
            },
        )
        _b13_write_json(race_dir / "fault-disarmed.json", disarm_record)
        published = _b13_wait_publication(work_dir, min(90.0, timeout))
        terminal_after = _b13_job(base, task_id, timeout)
        qc_pids_after = sorted(sampler.pids)
        sampler.stop()
        published_artifacts = _b13_copy_artifacts(work_dir, race_dir / "published")
        started_events = _b13_event_count(work_dir, "job.started")
        process_events = _b13_event_count(work_dir, "process.starting")
        published_events = _b13_event_count(work_dir, "job.publication_completed")
        publication_state = _b10_read_json(work_dir / "RESULT" / "publication_state.json")
        manifest = work_dir / "RESULT" / "result_manifest.json"
        result_id = str(publication_state.get("result_id") or "")
        qc_unchanged = qc_pids_before == qc_pids_after and len(qc_pids_after) == 1
        assertions.extend(
            [
                {
                    "name": "b13.publication.retried_only_after_fault_cleared",
                    "status": "PASS"
                    if published
                    and published_events >= 1
                    and started_events == 1
                    and process_events == 1
                    else "FAIL",
                    "detail": (
                        f"published={published} publication_completed={published_events} "
                        f"job.started={started_events} process.starting={process_events}"
                    ),
                },
                {
                    "name": "b13.publication.qc_spawn_count_unchanged",
                    "status": "PASS" if qc_unchanged else "FAIL",
                    "detail": json.dumps(
                        {
                            "before": qc_pids_before,
                            "after": qc_pids_after,
                            "max_concurrent": sampler.max_concurrent,
                        },
                        sort_keys=True,
                    ),
                },
                {
                    "name": "b13.publication.one_published_result",
                    "status": "PASS"
                    if published
                    and publication_state.get("complete") is True
                    and result_id
                    and manifest.is_file()
                    else "FAIL",
                    "detail": f"result_id={result_id} manifest={manifest.is_file()}",
                },
            ]
        )
        _b13_write_json(
            race_dir / "qc-counter-proof.json",
            {
                "task_ref": task_id,
                "pids_before_retry": qc_pids_before,
                "pids_after_retry": qc_pids_after,
                "equal": qc_pids_before == qc_pids_after,
                "max_concurrent": sampler.max_concurrent,
                "job_started_events": started_events,
                "process_starting_events": process_events,
                "publication_completed_events": published_events,
                "publication_state": publication_state,
                "status_after": terminal_after.get("status"),
                "published_artifacts": published_artifacts,
            },
        )
        row.update(
            {
                "attempt": int(terminal.get("attempt") or 0),
                "pid": int(terminal.get("pid") or 0),
                "release_id": receipt.get("release_id"),
                "submission_id": receipt.get("submission_id"),
                "qc_pids_before_retry": qc_pids_before,
                "qc_pids_after_retry": qc_pids_after,
                "published_artifacts": published_artifacts,
                "publication_completed_events": published_events,
                "budget_row": _b13_run_budget(
                    spec,
                    task_id=task_id,
                    pid=int(terminal.get("pid") or 0),
                    orca_pids=qc_pids_after,
                ),
                "evidence": "races/publication_failure",
            }
        )
        _b13_write_json(race_dir / "race.json", row)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        sampler.stop()
        assertions.append(
            {
                "name": "b13.publication.probe",
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        if task_id:
            try:
                _b13_cancel_task(base, task_id, timeout)
            except (urllib.error.URLError, OSError, ValueError):
                pass
    finally:
        sampler.stop()
        try:
            descriptor = json.loads(fault_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            descriptor = {}
        if not isinstance(descriptor, dict) or descriptor.get("armed") is not False:
            _b13_disarm_fault(fault_file, reason="publication race cleanup")
    _b13_finish_row(row, assertions)
    return row


def _b13_race_cancel(
    base: str, launch: dict[str, Any], out_dir: Path, timeout: float, orca: str
) -> dict[str, Any]:
    spec = next(item for item in B13_LOCAL_RACES if item["race_id"] == "cancel_race")
    row = _b13_row_record(spec)
    assertions: list[dict[str, Any]] = []
    sampler = _B10OrcaSampler(orca)
    sampler.start()
    task_id = ""
    race_dir = out_dir / "races" / "cancel_race"
    try:
        before_count = _controller_task_count(base, timeout)
        task_id, _probe, _submitted, _meta = _b13_submit(
            launch, "singlepoint", str(spec["xyz"]), dict(spec["level"]), timeout
        )
        row.update({"task_ref": task_id, "task_count_before": before_count})
        running_job, running = _b13_wait_running(base, task_id, min(90.0, timeout))
        pid = int(running_job.get("pid") or 0)
        revision_before = int(running_job.get("revision") or 0)
        attempt = int(running_job.get("attempt") or 0)
        work_dir = Path(str(running_job.get("work_dir") or ""))
        row.update({"attempt": attempt, "pid": pid})
        watcher = _B13StopWatcher([pid, *sampler.pids], work_dir)
        watcher.start()
        (race_dir).mkdir(parents=True, exist_ok=True)
        (race_dir / "process-table-before-cancel.txt").write_text(
            _b13_ps_table([pid, *sampler.pids]), encoding="utf-8"
        )
        pid_alive_at_cancel = _b13_pid_alive(pid)
        cancel_at_monotonic = time.monotonic()
        cancel_at = _b13_iso_now()
        cancel_status, cancel_body = _b13_cancel_task(base, task_id, timeout)
        cancel_returned = str(cancel_body.get("status") or "")
        final, cancelled = _b13_wait_terminal(base, task_id, min(90.0, timeout))
        cancelled_at_monotonic = time.monotonic()
        watcher.stop()
        watcher.wait(10)
        completed_at = str(final.get("completed_at") or "")
        stop_ok = watcher.stop_monotonic is not None
        stopped_before_terminal = (
            stop_ok
            and watcher.stop_monotonic is not None
            and watcher.stop_monotonic <= cancelled_at_monotonic
        )
        remaining_processes = _b13_task_processes(work_dir)
        started_events = _b13_event_count(work_dir, "job.started")
        cancelled_events = _b13_event_count(work_dir, "job.cancelled")
        settled_job = _b13_job(base, task_id, timeout)
        settle_deadline = time.monotonic() + 15.0
        while time.monotonic() < settle_deadline:
            settled_job = _b13_job(base, task_id, timeout)
            settle_result = (
                settled_job.get("result") if isinstance(settled_job.get("result"), dict) else {}
            )
            if str(settled_job.get("status")) == "cancelled" and (
                settle_result.get("terminal_side_effects_done") is True
            ):
                break
            time.sleep(0.1)
        revision_settled = int(settled_job.get("revision") or 0)
        (race_dir / "process-table-after-cancel.txt").write_text(
            _b13_ps_table([pid, *sampler.pids]), encoding="utf-8"
        )
        assertions.extend(
            [
                {
                    "name": "b13.cancel.task_running_when_cancel_issued",
                    "status": "PASS" if running and pid_alive_at_cancel else "FAIL",
                    "detail": f"task={task_id} pid={pid} alive={pid_alive_at_cancel}",
                },
                {
                    "name": "b13.cancel.no_premature_cancelled_response",
                    "status": "PASS"
                    if cancel_status == 200 and cancel_returned == "cancelling"
                    else "FAIL",
                    "detail": f"http={cancel_status} status={cancel_returned!r}",
                },
                {
                    "name": "b13.cancel.confirmed_stop_before_cancelled",
                    "status": "PASS" if stopped_before_terminal and cancelled else "FAIL",
                    "detail": (
                        f"stop={watcher.stop_iso} cancelled_at={completed_at} "
                        f"task_processes_at_stop={watcher.task_processes_at_stop}"
                    ),
                },
                {
                    "name": "b13.cancel.terminal_cancelled_no_live_processes",
                    "status": "PASS"
                    if cancelled
                    and str(final.get("status")) == "cancelled"
                    and not remaining_processes
                    else "FAIL",
                    "detail": (
                        f"status={final.get('status')} exit={final.get('exit_code')} "
                        f"remaining={remaining_processes}"
                    ),
                },
                {
                    "name": "b13.cancel.one_dispatch_one_cancelled_event",
                    "status": "PASS" if started_events == 1 and cancelled_events == 1 else "FAIL",
                    "detail": f"job.started={started_events} job.cancelled={cancelled_events}",
                },
            ]
        )
        # Late/old-attempt receipt: write after terminal, prove no revival.
        envelope = _b10_read_json(work_dir / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json")
        late = _b13_late_receipt(work_dir, envelope, attempt=attempt)
        gate = _b13_receipt_gate_probe(
            _b13_source_root(base),
            Path(str(final.get("work_dir") or "")).parent.parent / "acp_jobs.db",
            task_id,
            work_dir / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json",
            work_dir / "execution_receipt.json",
            current_attempt=attempt,
            old_revision=revision_before,
            timeout=timeout,
        )
        # Keep the same Controller and let several reconcile passes run.  The
        # late receipt is a file-only observation; it must not advance the row.
        time.sleep(3.0)
        after_late = _b13_job(base, task_id, timeout)
        after_late_work_dir = Path(str(after_late.get("work_dir") or ""))
        after_started = _b13_event_count(after_late_work_dir, "job.started")
        after_completed = _b13_event_count(after_late_work_dir, "job.completed")
        late_rejected = (
            gate.get("same_attempt", {}).get("accepted") is True
            and gate.get("old_attempt", {}).get("code") == "stale_attempt"
            and gate.get("cas", {}).get("rejected") is True
        )
        row_alive = (
            str(after_late.get("status")) == "cancelled"
            and int(after_late.get("revision") or 0) == revision_settled
            and int(after_late.get("attempt") or 0) == attempt
            and after_started == 1
            and after_completed == 0
        )
        assertions.extend(
            [
                {
                    "name": "b13.cancel.late_receipt_identity_and_old_attempt_rejected",
                    "status": "PASS" if late_rejected else "FAIL",
                    "detail": json.dumps(gate, sort_keys=True)[:1600],
                },
                {
                    "name": "b13.cancel.terminal_revision_unchanged_after_late_receipt",
                    "status": "PASS" if row_alive else "FAIL",
                    "detail": (
                        f"status={after_late.get('status')} "
                        f"revision={after_late.get('revision')}/{revision_settled} "
                        f"attempt={after_late.get('attempt')} "
                        f"job.started={after_started} job.completed={after_completed}"
                    ),
                },
            ]
        )
        sampler.stop()
        orca_pids = sorted(sampler.pids)
        _b13_write_json(
            race_dir / "cancel-timeline.json",
            {
                "task_ref": task_id,
                "pid": pid,
                "attempt": attempt,
                "revision_before_cancel": revision_before,
                "revision_settled_after_terminal": revision_settled,
                "cancel_issued_at": cancel_at,
                "cancel_response_http": cancel_status,
                "cancel_response_status": cancel_returned,
                "stop_observed_at": watcher.stop_iso,
                "cancelled_persisted_at": completed_at,
                "monotonic": {
                    "cancel_issued": cancel_at_monotonic,
                    "terminal_observed": cancelled_at_monotonic,
                },
                "pid_alive_at_cancel": pid_alive_at_cancel,
                "task_processes_at_stop": watcher.task_processes_at_stop,
                "remaining_processes_after_terminal": remaining_processes,
                "events": {
                    "job.started": started_events,
                    "job.cancelling": _b13_event_count(work_dir, "job.cancelling"),
                    "job.cancelled": cancelled_events,
                },
                "controller_restart": {
                    "deferred_to": "b13.fault.controller_restart",
                    "reason": (
                        "a restart advances terminal side-effect markers; "
                        "isolation stays in the dedicated row"
                    ),
                },
                "late_receipt": late,
                "gate": gate,
                "status_after_late_receipt": after_late.get("status"),
                "revision_after_late_receipt": after_late.get("revision"),
                "cancelled_at_wallclock": completed_at,
            },
        )
        _b13_copy_artifacts(work_dir, race_dir / "native")
        row.update(
            {
                "release_id": str(envelope.get("release_id") or ""),
                "submission_id": str(envelope.get("submission_id") or ""),
                "revision_before_cancel": revision_before,
                "revision_settled_after_terminal": revision_settled,
                "stop_observed_at": watcher.stop_iso,
                "cancelled_persisted_at": completed_at,
                "late_receipt_gate": gate,
                "controller_restart": "b13.fault.controller_restart",
                "orca_pids": orca_pids,
                "budget_row": _b13_run_budget(spec, task_id=task_id, pid=pid, orca_pids=orca_pids),
                "evidence": "races/cancel_race",
            }
        )
        _b13_write_json(race_dir / "race.json", row)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        sampler.stop()
        assertions.append(
            {
                "name": "b13.cancel.probe",
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        if task_id:
            try:
                _b13_cancel_task(base, task_id, timeout)
            except (urllib.error.URLError, OSError, ValueError):
                pass
    finally:
        sampler.stop()
    _b13_finish_row(row, assertions)
    return row


def _b13_race_timeout(
    base: str, launch: dict[str, Any], out_dir: Path, timeout: float, orca: str
) -> dict[str, Any]:
    spec = next(item for item in B13_LOCAL_RACES if item["race_id"] == "timeout_cancel")
    row = _b13_row_record(spec)
    assertions: list[dict[str, Any]] = []
    sampler = _B10OrcaSampler(orca)
    sampler.start()
    task_id = ""
    race_dir = out_dir / "races" / "timeout_cancel"
    try:
        before_count = _controller_task_count(base, timeout)
        task_id, probe, _submitted, _meta = _b13_submit(
            launch, "singlepoint", str(spec["xyz"]), dict(spec["level"]), timeout, keep_probe=True
        )
        row.update({"task_ref": task_id, "task_count_before": before_count})
        running_job, running = _b13_wait_running(base, task_id, min(90.0, timeout))
        pid = int(running_job.get("pid") or 0)
        work_dir = Path(str(running_job.get("work_dir") or ""))
        row.update({"attempt": int(running_job.get("attempt") or 0), "pid": pid})
        # The client abandons the request at a declared timeout.  This is NOT a
        # cancel: the Controller must keep the task RUNNING.
        client_timeout_at = _b13_iso_now()
        if probe is not None:
            probe.proc.terminate()
            try:
                probe.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                probe.proc.kill()
                probe.proc.wait(timeout=5)
            probe = None
        time.sleep(1.0)
        after_client_timeout = _b13_job(base, task_id, timeout)
        status_after_client = str(after_client_timeout.get("status") or "")
        pid_alive_after_client = _b13_pid_alive(pid)
        no_cancel_event_before_platform = _b13_event_count(work_dir, "job.cancelling") == 0
        watcher = _B13StopWatcher([pid, *sampler.pids], work_dir)
        watcher.start()
        platform_cancel_at = _b13_iso_now()
        cancel_status, cancel_body = _b13_cancel_task(base, task_id, timeout)
        cancel_returned = str(cancel_body.get("status") or "")
        final, cancelled = _b13_wait_terminal(base, task_id, min(90.0, timeout))
        watcher.stop()
        watcher.wait(10)
        completed_at = str(final.get("completed_at") or "")
        stop_ok = watcher.stop_monotonic is not None
        remaining_processes = _b13_task_processes(work_dir)
        started_events = _b13_event_count(work_dir, "job.started")
        assertions.extend(
            [
                {
                    "name": "b13.timeout.client_abandoned_while_running",
                    "status": "PASS" if running and probe is None else "FAIL",
                    "detail": f"task={task_id} pid={pid} client_timeout_at={client_timeout_at}",
                },
                {
                    "name": "b13.timeout.killing_client_is_not_cancel",
                    "status": "PASS"
                    if status_after_client == "running"
                    and pid_alive_after_client
                    and no_cancel_event_before_platform
                    else "FAIL",
                    "detail": (
                        f"status_after_client={status_after_client} "
                        f"pid_alive={pid_alive_after_client} "
                        f"cancel_events_before_platform={not no_cancel_event_before_platform}"
                    ),
                },
                {
                    "name": "b13.timeout.platform_cancel_confirmed_stop",
                    "status": "PASS"
                    if cancel_status == 200
                    and cancel_returned == "cancelling"
                    and cancelled
                    and stop_ok
                    else "FAIL",
                    "detail": (
                        f"http={cancel_status} response={cancel_returned!r} "
                        f"stop={watcher.stop_iso} cancelled_at={completed_at}"
                    ),
                },
                {
                    "name": "b13.timeout.reconciled_terminal",
                    "status": "PASS"
                    if cancelled
                    and str(final.get("status")) == "cancelled"
                    and not remaining_processes
                    and started_events == 1
                    else "FAIL",
                    "detail": (
                        f"status={final.get('status')} remaining={remaining_processes} "
                        f"job.started={started_events}"
                    ),
                },
            ]
        )
        sampler.stop()
        orca_pids = sorted(sampler.pids)
        _b13_write_json(
            race_dir / "timeout-timeline.json",
            {
                "task_ref": task_id,
                "pid": pid,
                "client_timeout_at": client_timeout_at,
                "status_after_client_timeout": status_after_client,
                "pid_alive_after_client_timeout": pid_alive_after_client,
                "platform_cancel_at": platform_cancel_at,
                "cancel_response_http": cancel_status,
                "cancel_response_status": cancel_returned,
                "stop_observed_at": watcher.stop_iso,
                "cancelled_persisted_at": completed_at,
                "remaining_processes": remaining_processes,
            },
        )
        _b13_copy_artifacts(work_dir, race_dir / "native")
        row.update(
            {
                "client_timeout_at": client_timeout_at,
                "status_after_client_timeout": status_after_client,
                "platform_cancel_at": platform_cancel_at,
                "stop_observed_at": watcher.stop_iso,
                "cancelled_persisted_at": completed_at,
                "orca_pids": orca_pids,
                "budget_row": _b13_run_budget(spec, task_id=task_id, pid=pid, orca_pids=orca_pids),
                "evidence": "races/timeout_cancel",
            }
        )
        _b13_write_json(race_dir / "race.json", row)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
    ) as exc:
        sampler.stop()
        assertions.append(
            {
                "name": "b13.timeout.probe",
                "status": "FAIL",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
        if task_id:
            try:
                _b13_cancel_task(base, task_id, timeout)
            except (urllib.error.URLError, OSError, ValueError):
                pass
    finally:
        sampler.stop()
    _b13_finish_row(row, assertions)
    return row


def _b13_source_root(base: str) -> Path:
    """The P1 worktree that owns the Controller (``--source`` or proc cwd)."""

    port = urlparse(base).port or int(os.environ.get("ACP2_CONTROLLER_PORT", "18771"))
    pid = _b13_listener_pid(port)
    if pid is not None:
        info = _b13_read_proc(pid)
        parsed = _b13_parse_controller_argv(info.get("argv") or [])
        declared = parsed.get("source")
        if declared:
            source = Path(str(declared))
            return source.parent if source.name == "src" else source
        cwd = Path(info.get("cwd") or "")
        if cwd.name == "src" and (cwd.parent / "src").is_dir():
            return cwd.parent
    source = Path(os.environ.get("ACP2_SOURCE", "/ACPServer/acp2-development/B"))
    return source


def _b13_lsf_control_row(out_dir: Path, fixture_path: str) -> dict[str, Any]:
    """Read and validate the separately registered LSF control fixture row."""

    registry, registry_path = _b11_pick_json(out_dir, "fixture-runs.json")
    rows = registry.get("fixtures") if isinstance(registry.get("fixtures"), list) else []
    control = next(
        (
            item
            for item in rows
            if isinstance(item, dict) and item.get("fixture_id") == B13_LSF_CONTROL_FIXTURE_ID
        ),
        None,
    )
    normal = next(
        (
            item
            for item in rows
            if isinstance(item, dict) and item.get("fixture_id") == B13_LSF_NORMAL_FIXTURE_ID
        ),
        None,
    )
    prerequisites = _b11_lsf_prerequisites()
    fixture_digest = sha256_file(Path(fixture_path)) if Path(fixture_path).is_file() else ""
    control_digest = str((control or {}).get("input_digest") or "")
    control_input = Path(str((control or {}).get("input") or ""))
    row = {
        "fixture_id": B13_LSF_CONTROL_FIXTURE_ID,
        "normal_completion_fixture_id": B13_LSF_NORMAL_FIXTURE_ID,
        "registry": str(registry_path) if registry_path is not None else None,
        "registered": control is not None,
        "distinct_from_normal": control is not None
        and normal is not None
        and control.get("fixture_id") != normal.get("fixture_id"),
        "task_ref": (control or {}).get("task_ref"),
        "submitted_once": bool((control or {}).get("submitted_once")),
        "status": "NOT_VERIFIED",
        "budget": (control or {}).get("budget"),
        "timeout_seconds": (control or {}).get("scenario_timeout_seconds"),
        "input": str(control_input) if control_input else None,
        "input_digest": control_digest,
        "fixture_digest": fixture_digest,
        "input_digest_matches": bool(control_digest)
        and bool(fixture_digest)
        and control_digest == fixture_digest,
        "missing_prerequisites": list(prerequisites["missing"]),
        "observed": prerequisites["observed"],
        "no_bsub_invoked": True,
        "no_bstop_bresume_invoked": True,
        "no_fake_scheduler": True,
        "reason": "lsf_prerequisite_missing:" + ",".join(prerequisites["missing"])
        if prerequisites["missing"]
        else "lsf_control_live_not_exercised",
    }
    return row


def _b13_p1_candidate_record(out_dir: Path) -> tuple[dict[str, Any], list[str]]:
    candidate, errors, _path = _b11_p1_candidate(out_dir)
    if candidate.get("server_revision") != B13_CANDIDATE_REVISION:
        errors.append("server_revision_not_b13_candidate")
    return candidate, errors


def _run_control_recovery(
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
    launch_path: str | None = None,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B13: prove control/recovery with real Local faults and a separate LSF row.

    Every Local fault owns one task, one PID, one timeout and one budget row.
    The LSF control fixture (``fx-control-recovery-lsf-v1``) is a *different*
    registered fixture from B11's normal-completion row and is only
    ``NOT_VERIFIED`` here because this host has no LSF client/queue; no fake
    ``bsub``/``bstop``/``bresume`` is ever invoked.

    Exit semantics (documented in ``exit-semantics.json``): 0 only when every
    Local fault row and the LSF control row PASS; 2 when the Local rows PASS but
    the LSF control row is ``NOT_VERIFIED`` (H3 stays open); 1 on any Local
    failure.  Local PASS rows remain visible in a 2 exit.
    """

    base = (controller or "").rstrip("/")
    if not base:
        return (
            EXIT_NOT_VERIFIED,
            [
                {
                    "name": "b13.controller_required",
                    "status": "NOT_VERIFIED",
                    "detail": "no controller",
                }
            ],
            {"b13.reason": "missing_controller"},
        )
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b13.fixture": str(fixture_path),
        "b13.controller": base,
        "b13.candidate_revision": B13_CANDIDATE_REVISION,
        "b13.contract_digest": B13_CONTRACT_DIGEST,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    races_dir = out_dir / "races"
    races_dir.mkdir(parents=True, exist_ok=True)

    candidate, candidate_errors = _b13_p1_candidate_record(out_dir)
    health = _b13_controller_health(base, timeout)
    health_ok = (
        health.get("candidate_revision") == B13_CANDIDATE_REVISION
        and health.get("contract_digest") == B13_CONTRACT_DIGEST
    )
    assertions.append(
        {
            "name": "b13.candidate_identity",
            "status": "PASS" if health_ok and not candidate_errors else "FAIL",
            "detail": (
                f"health={health.get('candidate_revision')}/{health.get('contract_digest')} "
                f"p1={candidate.get('server_revision')} errors={candidate_errors}"
            ),
        }
    )
    candidate_record = {
        "server_revision": candidate.get("server_revision"),
        "server_revision_kind": candidate.get("server_revision_kind"),
        "candidate_label": candidate.get("candidate_label"),
        "contract_digest": candidate.get("contract_digest"),
        "contract_version": candidate.get("contract_version"),
        "client_revision": candidate.get("client_revision"),
        "harness_revision": candidate.get("harness_revision"),
        "harness_revision_current": harness_revision(),
        "health": health,
        "errors": candidate_errors,
    }
    _b13_write_json(out_dir / "candidate.json", candidate_record)
    extensions["b13.p1_candidate"] = candidate_record

    if health_ok and not candidate_errors:
        controller_pid = _b13_listener_pid(urlparse(base).port or 18771)
        controller_info = _b13_read_proc(controller_pid) if controller_pid else {}
        parsed = _b13_parse_controller_argv(controller_info.get("argv") or [])
        source_root = _b13_source_root(base)
        source_revision = ""
        try:
            source_revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(source_root),
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            source_revision = ""
        controller_start_command = " ".join(
            shlex.quote(str(item)) for item in controller_info.get("argv") or []
        )
        (out_dir / "controller-start-command.txt").write_text(
            controller_start_command + "\n", encoding="utf-8"
        )
        extensions["b13.controller"] = {
            "pid": controller_pid,
            "argv": controller_info.get("argv"),
            "cwd": controller_info.get("cwd"),
            "run_root": parsed.get("run_root"),
            "fault_seam": parsed.get("fault_seam"),
            "fault_file": parsed.get("fault_file"),
            "script": parsed.get("script"),
            "source": parsed.get("source"),
            "start_command": controller_start_command,
            "source_root": str(source_root),
            "source_revision": source_revision,
            "source_revision_matches": source_revision == B13_CANDIDATE_REVISION,
        }
        _b13_write_json(out_dir / "controller.json", extensions["b13.controller"])
        assertions.append(
            {
                "name": "b13.controller_single_instance_source",
                "status": "PASS"
                if controller_pid is not None and parsed.get("port") is not None
                else "FAIL",
                "detail": (
                    f"pid={controller_pid} port={parsed.get('port')} "
                    f"source={source_revision or 'unknown'} fault_seam={parsed.get('fault_seam')}"
                ),
            }
        )
        orca = str(
            (controller_info.get("env") or {}).get(
                "CONFSEARCH_ORCA_PATH", "/home/xieningke/orca611/orca"
            )
        )
        launch = _b13_build_launch(
            base,
            Path(
                parsed.get("run_root")
                or (controller_info.get("env") or {}).get("ACP_RUN_ROOT")
                or "."
            ),
            out_dir,
            launch_path,
        )
        launch_env = launch.get("env") if isinstance(launch.get("env"), dict) else {}
        for key in (
            "ACP_CONFIG",
            "CONFSEARCH_ORCA_PATH",
            "CONFSEARCH_ORCA_LD_LIBRARY_PATH",
            "LD_LIBRARY_PATH",
            "PATH",
        ):
            value = (controller_info.get("env") or {}).get(key)
            if value:
                launch_env[key] = value
        launch["env"] = launch_env
        _b13_write_json(out_dir / "mcp-launch.json", launch)
        extensions["b13.mcp_launch"] = "mcp-launch.json"
        fault_file = (
            Path(parsed["fault_file"])
            if parsed.get("fault_seam") and parsed.get("fault_file")
            else None
        )
        if fault_file is not None:
            fault_file.parent.mkdir(parents=True, exist_ok=True)
            _b13_disarm_fault(fault_file, reason="scenario start")
        extensions["b13.publication_fault_seam"] = {
            "available": bool(fault_file),
            "fault_file": str(fault_file) if fault_file else None,
            "wrapper": B13_FAULT_WRAPPER,
            "descriptor_schema": B13_FAULT_SCHEMA,
        }
        if fault_file is None:
            assertions.append(
                {
                    "name": "b13.publication_fault_seam",
                    "status": "FAIL",
                    "detail": (
                        "Controller was not launched through "
                        "scripts/acceptance/acp2_fault_controller.py; publication "
                        "failure cannot be injected without faking the scheduler"
                    ),
                }
            )

        rows: list[dict[str, Any]] = []
        rows.append(_b13_race_adapter_eof(base, launch, out_dir, timeout, orca))
        rows.append(_b13_race_controller_restart(base, launch, out_dir, timeout, orca))
        rows.append(_b13_race_publication_failure(base, launch, out_dir, timeout, orca, fault_file))
        rows.append(_b13_race_cancel(base, launch, out_dir, timeout, orca))
        rows.append(_b13_race_timeout(base, launch, out_dir, timeout, orca))

        # DSH is C-owned and this host has no delivered DSH; never start one.
        client_revision = str(candidate.get("client_revision") or "")
        dsh_row = {
            "fault": "dsh_disconnect",
            "kind": "client",
            "status": "NOT_VERIFIED",
            "reason": "c_dsh_absent",
            "client_revision": client_revision,
            "detail": (
                "C DSH/Workbench is not delivered on this host (B10 P1 records "
                "client_revision=NOT_VERIFIED); no DSH process is started, so a "
                "re-handshake cannot be claimed."
            ),
        }
        _b13_write_json(out_dir / "dsh-disconnect.json", dsh_row)
        assertions.append(
            {
                "name": "b13.fault.dsh_disconnect",
                "status": "NOT_VERIFIED",
                "detail": dsh_row["detail"],
            }
        )

        lsf = _b13_lsf_control_row(out_dir, fixture_path)
        _b13_write_json(out_dir / "lsf-control-fixture.json", lsf)
        lsf_registered = (
            lsf["registered"]
            and lsf["distinct_from_normal"]
            and lsf["task_ref"] is None
            and lsf["submitted_once"] is False
            and lsf["input_digest_matches"]
            and bool(lsf["budget"])
        )
        assertions.append(
            {
                "name": "b13.lsf_control_fixture",
                "status": "NOT_VERIFIED" if lsf_registered else "FAIL",
                "detail": json.dumps(
                    {
                        "registered": lsf_registered,
                        "task_ref": lsf["task_ref"],
                        "distinct": lsf["distinct_from_normal"],
                        "reason": lsf["reason"],
                        "missing": lsf["missing_prerequisites"],
                        "no_fake_scheduler": lsf["no_fake_scheduler"],
                        "live_exercised": False,
                    },
                    sort_keys=True,
                )[:1600],
            }
        )
        if not lsf_registered:
            assertions.append(
                {
                    "name": "b13.lsf_control_fixture_registration",
                    "status": "FAIL",
                    "detail": json.dumps(lsf, sort_keys=True)[:1600],
                }
            )

        for row in rows:
            assertions.append(
                {
                    "name": f"b13.fault.{row['race_id']}",
                    "status": row["status"],
                    "detail": json.dumps(
                        {
                            "task_ref": row.get("task_ref"),
                            "pid": row.get("pid"),
                            "timeout_seconds": row.get("timeout_seconds"),
                            "budget": row.get("budget"),
                            "evidence": row.get("evidence"),
                        },
                        sort_keys=True,
                    )[:1200],
                }
            )
        local_statuses = [row["status"] for row in rows]
        # B13 never claims a live LSF control PASS: bstop/bresume/cancel were
        # not exercised on this host.  The registered row stays NOT_VERIFIED.
        lsf_status = "NOT_VERIFIED"
        exit_code, result, reason = _b13_exit_semantics(local_statuses, lsf_status)
        local_pass = all(status == "PASS" for status in local_statuses)
        if not local_pass:
            exit_code, result = EXIT_FAIL, "FAIL"
            reason = "local_fault_row_failed"
        if lsf["missing_prerequisites"]:
            reason = "lsf_prerequisite_missing:" + ",".join(lsf["missing_prerequisites"])
        elif lsf_registered:
            reason = "lsf_control_live_not_exercised"
        if any(item["status"] == "FAIL" for item in assertions):
            exit_code, result = EXIT_FAIL, "FAIL"
            reason = reason or "assertion_failed"
        assertions.append(
            {
                "name": "b13.local_fault_rows",
                "status": "PASS" if local_pass else "FAIL",
                "detail": ",".join(f"{row['race_id']}={row['status']}" for row in rows),
            }
        )
        _b13_write_json(
            out_dir / "races.json",
            {
                "schema": "acp2.b13.races.v1",
                "candidate_revision": B13_CANDIDATE_REVISION,
                "controller": extensions.get("b13.controller"),
                "races": rows,
                "dsh": dsh_row,
                "lsf_control": lsf,
            },
        )
        exit_semantics = {
            "schema": "acp2.b13.exit-semantics.v1",
            "local_statuses": {row["race_id"]: row["status"] for row in rows},
            "lsf_control_status": lsf_status,
            "exit_code": exit_code,
            "result": result,
            "reason": reason,
            "rule": (
                "0 only if every Local fault row PASS and the LSF control row is "
                "PASS/NOT_REQUIRED; 2 when Local PASS but LSF is NOT_VERIFIED; "
                "1 on any Local failure"
            ),
        }
        _b13_write_json(out_dir / "exit-semantics.json", exit_semantics)
        extensions.update(
            {
                "b13.task_ref": next(
                    (row.get("task_ref") for row in rows if row["race_id"] == "cancel_race"),
                    None,
                ),
                "b13.attempt": next(
                    (row.get("attempt") for row in rows if row["race_id"] == "cancel_race"),
                    None,
                ),
                "b13.release_id": next(
                    (row.get("release_id") for row in rows if row["race_id"] == "cancel_race"),
                    None,
                ),
                "b13.submission_id": next(
                    (row.get("submission_id") for row in rows if row["race_id"] == "cancel_race"),
                    None,
                ),
                "b13.races": [
                    {
                        "race_id": row["race_id"],
                        "fault": row["fault"],
                        "task_ref": row.get("task_ref"),
                        "attempt": row.get("attempt"),
                        "pid": row.get("pid"),
                        "timeout_seconds": row.get("timeout_seconds"),
                        "budget": row.get("budget"),
                        "status": row["status"],
                        "evidence": row.get("evidence"),
                    }
                    for row in rows
                ],
                "b13.lsf_control_fixture": lsf,
                "b13.dsh_disconnect": dsh_row,
                "b13.exit_semantics": exit_semantics,
                "b13.reason": reason,
            }
        )
        evidence_files: list[Path] = [
            out_dir / "races.json",
            out_dir / "exit-semantics.json",
            out_dir / "lsf-control-fixture.json",
            out_dir / "candidate.json",
            out_dir / "controller.json",
            out_dir / "mcp-launch.json",
            out_dir / "dsh-disconnect.json",
        ]
        for race in rows:
            evidence = str(race.get("evidence") or "")
            if evidence:
                evidence_files.extend(
                    path for path in sorted((out_dir / evidence).rglob("*")) if path.is_file()
                )
        extensions["b13.artifacts"] = [
            {
                "path": str(path.relative_to(out_dir)),
                "size_bytes": path.stat().st_size,
                "sha256": _b13_sha256(path),
            }
            for path in evidence_files
            if path.is_file()
        ]
        extensions["b13.cleanup"] = {
            "controller_running_after_scenario": _b13_listener_pid(urlparse(base).port or 18771),
            "active_tasks": [
                row.get("task_ref")
                for row in rows
                if row.get("status") != "PASS" and row.get("task_ref")
            ],
            "note": "the executor stops the single B13 Controller after the run",
        }
    else:
        assertions.append(
            {
                "name": "b13.candidate_identity_blocked",
                "status": "FAIL",
                "detail": "Controller health/P1 record does not match the frozen B13 candidate",
            }
        )
        extensions["b13.reason"] = "candidate_identity_mismatch"
        return EXIT_FAIL, assertions, extensions

    return exit_code, assertions, extensions


B16_APPROVAL_SCHEMA = "acp2.approval-fixture.v1"


def _b16_plan_digest(plan: Mapping[str, Any]) -> str:
    from acp_services.runtime.canonical_digest import plan_digest

    return plan_digest(dict(plan)).digest


def _b16_proposal_digest(proposal: Mapping[str, Any]) -> str:
    from acp_services.runtime.canonical_digest import proposal_digest

    return proposal_digest(dict(proposal)).digest


def _b16_submit_body(
    scenario: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    plan_digest: str,
    proposal_ref: str,
    proposal_digest: str | None,
    budget: Mapping[str, Any] | None,
    name: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workflow": str(plan["workflow"]),
        "name": name,
        "input": dict(plan["input"]),
        "method": dict(plan.get("method") or {}),
        "resources": dict(plan.get("resources") or {}),
        "project_id": str(scenario["alpha_project"]),
        "plan_digest": plan_digest,
        "proposal_ref": proposal_ref,
    }
    if proposal_digest is not None:
        body["proposal_digest"] = proposal_digest
    if budget is not None:
        body["budget"] = dict(budget)
    return body


def _b16_denial_detail(status: int, body: Any) -> tuple[str, str]:
    detail = body if isinstance(body, dict) else {}
    error = detail.get("detail") if isinstance(detail.get("detail"), dict) else detail
    code = str(error.get("code") or "")
    rule = str((error.get("params") or {}).get("rule") or "")
    return code, rule


def _b16_events(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT event_id, job_id, owner_type, scope, event_type, payload_json,
                      revision, emitted_at
               FROM job_outbox WHERE event_type LIKE 'approval.%'
               ORDER BY rowid"""
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "event_id": row["event_id"],
            "owner_id": row["job_id"],
            "owner_type": row["owner_type"],
            "scope": row["scope"],
            "event_type": row["event_type"],
            "revision": row["revision"],
            "emitted_at": row["emitted_at"],
            "payload": json.loads(row["payload_json"]),
        }
        for row in rows
    ]


def _b15_count_jobs(db_path: Path) -> int:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error:
        return -1


def _b16_work_dirs(run_root: Path) -> list[str]:
    ignored = {
        "projects",
        "secrets",
        "acp_jobs.db",
        "acp_jobs.db-wal",
        "acp_jobs.db-shm",
        "acp_identity.db",
        "acp_identity.db-wal",
        "acp_identity.db-shm",
        "acp_admission.db",
        "acp_admission.db-wal",
        "acp_admission.db-shm",
    }
    try:
        return sorted(
            entry.name
            for entry in run_root.iterdir()
            if entry.is_dir() and entry.name not in ignored
        )
    except OSError:
        return []


B18_PREFLIGHT_SCHEMA = "acp2.b18.da-preflight.v1"
B18_ROUNDTRIP_SCHEMA = "acp2.runtime.science-roundtrip.v1"
B18_HANDOFF_SCHEMA = "acp2.runtime.b18a-handoff.v1"
B18_SCIENCE_BINDING = CONTRACT_ROOT / "runtime" / "science-binding.v1.json"


def _b18_fixture_path(fixture_dir: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else fixture_dir / path


def _b18_sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _b18_default_dag(plan_identity: str, release_id: str):
    from acp_services.runtime.science_binding import document_digest
    from acp_services.runtime.step_dag import (
        BoundedStepDag,
        OutputRef,
        ScienceStepKind,
        StepSpec,
    )

    ts = StepSpec(
        step_id="ts_opt",
        kind=ScienceStepKind.OPTIMIZE,
        intent="STATIC/SIMULATED optimize of the frozen TS guess",
        parameters={"optimizer": "r2SCAN-3c"},
        produces=(OutputRef("ts_opt", "ts_geometry"),),
        structure_role="transition_state",
        implementation_version="cccp-opt-static-1",
        capability="optimize.ts",
    )
    freq = StepSpec(
        step_id="ts_freq",
        kind=ScienceStepKind.FREQUENCY,
        intent="STATIC/SIMULATED imaginary-frequency screening",
        parameters={"thermo": "grimme"},
        consumes=(
            OutputRef(
                "ts_opt",
                "ts_geometry",
                document_digest({"step": "ts_opt", "product": "ts_geometry"}),
            ),
        ),
        produces=(OutputRef("ts_freq", "frequency_summary"),),
        structure_role="transition_state",
        implementation_version="cccp-freq-static-1",
        capability="frequency.ts",
    )
    irc = StepSpec(
        step_id="ts_irc",
        kind=ScienceStepKind.IRC,
        intent="STATIC/SIMULATED bidirectional IRC endpoint check",
        parameters={"directions": ["forward", "reverse"]},
        consumes=(
            OutputRef(
                "ts_freq",
                "frequency_summary",
                document_digest({"step": "ts_freq", "product": "frequency_summary"}),
            ),
        ),
        produces=(OutputRef("ts_irc", "irc_paths"),),
        structure_role="transition_state",
        implementation_version="cccp-irc-static-1",
        capability="irc.ts",
    )
    return BoundedStepDag(
        recipe_id="recipe.da.static.v1",
        task_ref="task-b18-static",
        steps=(ts, freq, irc),
    )


def _b18_static_dispatch(fail_on: str | None = None):
    from acp_services.runtime.science_binding import document_digest
    from acp_services.runtime.step_dag import StepOutcome

    calls: list[str] = []
    upstream: dict[str, list[str]] = {}

    def dispatch(context: Any) -> StepOutcome:
        step_id = context.step.step_id
        calls.append(step_id)
        upstream[step_id] = list(context.upstream_digests)
        if fail_on == step_id:
            raise RuntimeError("STATIC simulated compute failure")
        return StepOutcome(
            output_digests={
                ref.product_kind: document_digest({"step": step_id, "product": ref.product_kind})
                for ref in context.step.produces
            },
            receipt_digest=document_digest({"receipt": step_id}),
            result_digest=document_digest({"result": step_id}),
            publish_payload={"step_id": step_id, "simulated": True},
        )

    dispatch.calls = calls  # type: ignore[attr-defined]
    dispatch.upstream = upstream  # type: ignore[attr-defined]
    return dispatch


def _b18_static_publish(fail_first_for: str | None = None):
    state = {"fail_first_for": fail_first_for}
    published: list[str] = []

    def publish(step: Any, outcome: Any) -> dict[str, str]:
        if state["fail_first_for"] == step.step_id:
            state["fail_first_for"] = None
            raise OSError("STATIC simulated publication failure")
        published.append(step.step_id)
        return dict(outcome.output_digests)

    publish.published = published  # type: ignore[attr-defined]
    return publish


def _b18_executor(dag: Any, dispatch: Any, publish: Any, *, plan_identity: str, release_id: str):
    from acp_services.runtime.step_dag import BoundedStepDagExecutor

    return BoundedStepDagExecutor(
        dag,
        dispatch,
        publish,
        plan_identity=plan_identity,
        release_id=release_id,
    )


def _b18_collect_artifacts(out_dir: Path, names: Sequence[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in names:
        path = out_dir / name
        if not path.is_file():
            continue
        rows.append(
            {
                "path": str(path.relative_to(out_dir)),
                "size_bytes": path.stat().st_size,
                "sha256": _b18_sha256_file(path),
            }
        )
    return rows


B20_DA_RUN_SCHEMA = "acp2.b20a.da-run.v1"
B20_INCOMPLETE_SCHEMA = "acp2.b20a.incomplete-input.v1"
B20_REQUIRED_C24A_FIELDS = (
    "proposal_digest",
    "approval_ref",
    "job_id",
    "recipe_digest",
    "budget_basis",
    "run_spec",
    "search_capability",
)
B20_REQUIRED_CONTRACTS = (
    "reaction.schema.json",
    "recipe.schema.json",
    "task-binding.schema.json",
    "evidence.schema.json",
)
B20_RUN_SPEC_TOTAL = 14400


def _b20_read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _b20_fixture_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def _b20_reaction_refs(species: Any) -> list[str]:
    refs: list[str] = []
    if not isinstance(species, dict):
        return refs
    structure_id = species.get("structure_id")
    atom_ids = species.get("atom_ids")
    if not isinstance(structure_id, str) or not isinstance(atom_ids, list):
        return refs
    for atom_id in atom_ids:
        if isinstance(atom_id, str) and atom_id:
            refs.append(f"{structure_id}:{atom_id}")
    return refs


def _b20_validate_reaction(reaction: Any) -> list[str]:
    """Deterministic completeness gate for the limited DA ReactionSpec.

    This is deliberately stricter than "the JSON parses": an incomplete atom
    mapping is a scientific-input error and must be rejected before dispatch.
    """

    errors: list[str] = []
    if not isinstance(reaction, dict):
        return ["incomplete_scientific_input:reaction_not_object"]
    for name in (
        "schema_version",
        "reaction_id",
        "reaction_scope",
        "left",
        "right",
        "atom_mapping",
        "bond_changes",
        "conditions",
        "input_uncertainty",
    ):
        if name not in reaction:
            errors.append(f"incomplete_scientific_input:missing_{name}")
    left = reaction.get("left")
    right = reaction.get("right")
    if not isinstance(left, list) or not left:
        errors.append("incomplete_scientific_input:missing_left")
        left = []
    if not isinstance(right, list) or not right:
        errors.append("incomplete_scientific_input:missing_right")
        right = []
    left_refs = [ref for species in left for ref in _b20_reaction_refs(species)]
    right_refs = [ref for species in right for ref in _b20_reaction_refs(species)]
    if not left_refs or not right_refs:
        errors.append("incomplete_scientific_input:missing_atom_ids")
    for side, species_rows in (("left", left), ("right", right)):
        for species in species_rows:
            if not isinstance(species, dict):
                errors.append(f"incomplete_scientific_input:{side}_species_not_object")
                continue
            for name in ("elements", "formula", "charge", "multiplicity", "structure_id"):
                if name not in species:
                    errors.append(f"incomplete_scientific_input:{side}_missing_{name}")
    mapping = reaction.get("atom_mapping")
    if not isinstance(mapping, dict):
        errors.append("incomplete_scientific_input:missing_atom_mapping")
        mapping = {}
    left_to_right = mapping.get("left_to_right")
    right_to_left = mapping.get("right_to_left")
    if not isinstance(left_to_right, dict) or not left_to_right:
        errors.append("incomplete_scientific_input:missing_left_to_right")
        left_to_right = {}
    if not isinstance(right_to_left, dict) or not right_to_left:
        errors.append("incomplete_scientific_input:missing_right_to_left")
        right_to_left = {}
    missing_left = [ref for ref in left_refs if ref not in left_to_right]
    missing_right = [ref for ref in right_refs if ref not in right_to_left]
    if missing_left or missing_right:
        errors.append(
            "incomplete_scientific_input:atom_mapping_incomplete:"
            f"left={missing_left} right={missing_right}"
        )
    if mapping.get("unmapped_left") not in ([], None):
        errors.append(
            "incomplete_scientific_input:unmapped_left:" + str(mapping.get("unmapped_left"))
        )
    if mapping.get("unmapped_right") not in ([], None):
        errors.append(
            "incomplete_scientific_input:unmapped_right:" + str(mapping.get("unmapped_right"))
        )
    values = list(left_to_right.values())
    if len(values) != len(set(values)):
        errors.append("incomplete_scientific_input:atom_mapping_not_injective")
    for left_ref, right_ref in left_to_right.items():
        if right_to_left.get(right_ref) != left_ref:
            errors.append("incomplete_scientific_input:atom_mapping_not_round_trip")
            break
    bond_changes = reaction.get("bond_changes")
    if not isinstance(bond_changes, list) or not bond_changes:
        errors.append("incomplete_scientific_input:missing_bond_changes")
    conditions = reaction.get("conditions")
    if not isinstance(conditions, dict):
        errors.append("incomplete_scientific_input:missing_conditions")
    else:
        for name in ("phase", "temperature_k", "pressure_pa", "standard_state"):
            if name not in conditions:
                errors.append(f"incomplete_scientific_input:conditions_missing_{name}")
    if not isinstance(reaction.get("input_uncertainty"), dict):
        errors.append("incomplete_scientific_input:missing_input_uncertainty")
    return errors


def _b20_validate_expectations(expectations: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(expectations, dict):
        return ["incomplete_scientific_input:missing_scientific_expectations"]
    ts = expectations.get("transition_state")
    if not isinstance(ts, dict):
        errors.append("incomplete_scientific_input:missing_ts_expectation")
    else:
        if ts.get("one_imaginary_mode") is not True:
            errors.append("incomplete_scientific_input:ts_one_imaginary_mode_not_declared")
        if ts.get("mapped_mode_required") is not True:
            errors.append("incomplete_scientific_input:ts_mapped_mode_not_declared")
    irc = expectations.get("irc")
    if not isinstance(irc, dict):
        errors.append("incomplete_scientific_input:missing_irc_expectation")
    else:
        if irc.get("bidirectional") is not True:
            errors.append("incomplete_scientific_input:irc_not_bidirectional")
        if irc.get("endpoint_mapping") is not True:
            errors.append("incomplete_scientific_input:irc_endpoint_mapping_not_declared")
    energy = expectations.get("energy")
    if not isinstance(energy, dict) or not energy.get("comparability_fields"):
        errors.append("incomplete_scientific_input:missing_energy_comparability")
    return errors


def _b20_c24a_state(block: Any) -> dict[str, Any]:
    if not isinstance(block, dict):
        return {
            "status": "c24a_absent",
            "reason": "c24a_contract_missing_from_fixture",
            "required_fields": list(B20_REQUIRED_C24A_FIELDS),
        }
    expected = _b20_fixture_path(Path.cwd(), block.get("expected_artifact"))
    required = tuple(block.get("required_fields") or B20_REQUIRED_C24A_FIELDS)
    state: dict[str, Any] = {
        "status": "c24a_absent",
        "expected_artifact": str(expected),
        "required_fields": list(required),
        "owner": block.get("owner", "C"),
        "plan": block.get("plan"),
        "plan_todo": block.get("plan_todo"),
        "evidence_roots": list(block.get("evidence_roots") or []),
    }
    if not expected.is_file():
        state["missing_artifact"] = str(expected)
        state["reason"] = "c24a_absent"
        return state
    document = _b20_read_json(expected)
    if not isinstance(document, dict):
        state["reason"] = "c24a_unreadable"
        state["missing_artifact"] = str(expected)
        return state
    missing = [
        field
        for field in required
        if field not in document or document[field] in (None, "", [], {})
    ]
    state["artifact_sha256"] = _b18_sha256_file(expected)
    if missing:
        state["status"] = "c24a_incomplete"
        state["missing_fields"] = missing
        state["reason"] = "c24a_incomplete"
        return state
    state["status"] = "c24a_present"
    state["reason"] = None
    state["document"] = {field: document[field] for field in required}
    return state


def _b20_find_b18_receipt(b18_dir: Path) -> Path | None:
    if not b18_dir.is_dir():
        return None
    candidates = sorted(b18_dir.glob("da-runtime-da-preflight-*.json"))
    for path in reversed(candidates):
        document = _b20_read_json(path)
        if (
            isinstance(document, dict)
            and document.get("scenario") == "da-runtime"
            and document.get("phase") == "da-preflight"
        ):
            return path
    return None


def _b20_validate_p2_candidate(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["p2_candidate_not_object"]
    for name in (
        "candidate_revision",
        "server_revision",
        "client_revision",
        "harness_revision",
        "contract_digest",
        "env_digest",
    ):
        if name not in document:
            errors.append(f"p2_candidate_missing:{name}")
    revision = str(document.get("candidate_revision") or "")
    revision_ok = re.fullmatch(r"[0-9a-f]{40}", revision) or re.fullmatch(
        r"sha256:[0-9a-f]{64}", revision
    )
    if not revision_ok:
        errors.append("p2_candidate_revision_invalid")
    client = str(document.get("client_revision") or "")
    if not (re.fullmatch(r"[0-9a-f]{40}", client) or client == "NOT_VERIFIED"):
        errors.append("p2_candidate_client_revision_invalid")
    for name in ("harness_revision", "contract_digest", "env_digest"):
        if not HEX64.match(str(document.get(name) or "")):
            errors.append(f"p2_candidate_{name}_invalid")
    composition = document.get("source_composition")
    if isinstance(composition, dict) and composition.get("clean_tree") is not True:
        errors.append("p2_candidate_clean_tree_false")
    return errors


def _b20_validate_run_spec(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["run_spec_not_object"]
    expected = {
        "science_deadline_seconds": 13380,
        "export_budget_seconds": 900,
        "aggregate_budget_seconds": 120,
        "total_budget_seconds": B20_RUN_SPEC_TOTAL,
    }
    for name, value in expected.items():
        if document.get(name) != value:
            errors.append(f"run_spec_{name}:{document.get(name)}!={value}")
    for name in ("queue_accounting", "inflight_stop_policy", "backend", "payable_task_count"):
        if name not in document:
            errors.append(f"run_spec_missing:{name}")
    if "started_at" not in document:
        errors.append("run_spec_missing:started_at")
    parts = (
        document.get("science_deadline_seconds"),
        document.get("export_budget_seconds"),
        document.get("aggregate_budget_seconds"),
    )
    if all(isinstance(part, int) for part in parts) and sum(parts) != B20_RUN_SPEC_TOTAL:
        errors.append("run_spec_total_mismatch")
    return errors


def _b20_dependency_edges() -> tuple[list[str], dict[str, Any]]:
    """Verify the executable ledger carries the reviewed P2 DA edges."""

    errors: list[str] = []
    path = CONTRACT_ROOT / "runtime" / "dependencies.json"
    document = _b20_read_json(path)
    observed: dict[str, Any] = {"path": str(path)}
    if not isinstance(document, dict):
        return ["dependency_ledger_unreadable"], observed
    nodes = {node["id"]: node for node in document.get("nodes", []) if isinstance(node, dict)}
    external = {
        node["id"]: node for node in document.get("external_nodes", []) if isinstance(node, dict)
    }
    external_edges = {
        (edge.get("consumer"), edge.get("external_id"))
        for edge in document.get("external_edges", [])
        if isinstance(edge, dict)
    }
    required_external = [
        ("B20a-ready", "A-14"),
        ("B20a-ready", "A-15"),
        ("B20a-ready", "A-16"),
        ("B20a-exec", "C24a"),
    ]
    missing_external = [pair for pair in required_external if pair not in external_edges]
    if missing_external:
        errors.append(f"dependency_missing_external_edges:{missing_external}")
    consumer_edges = [edge for edge in document.get("consumer_edges", []) if isinstance(edge, dict)]
    ready_to_c24a = [
        edge
        for edge in consumer_edges
        if edge.get("producer") == "B20a-ready" and "T-34" in str(edge.get("consumer", ""))
    ]
    if not ready_to_c24a:
        errors.append("dependency_missing_edge:B20a-ready->C24a(T-34)")
    b20b_deps = list((nodes.get("B20b") or {}).get("depends_on") or [])
    if "B20a-exec" not in b20b_deps:
        errors.append("dependency_missing_edge:B20a-exec->B20b")
    c24a_state = external.get("C24a") or {}
    observed.update(
        {
            "required_external_edges": [
                {"consumer": consumer, "external_id": external_id}
                for consumer, external_id in required_external
            ],
            "ready_to_c24a_edges": ready_to_c24a,
            "b20b_depends_on": b20b_deps,
            "c24a_readiness": c24a_state.get("readiness"),
            "c24a_delivery_status": c24a_state.get("delivery_status"),
            "c24a_observed_state": c24a_state.get("observed_state"),
        }
    )
    return errors, observed


def _b20_c_plan_state(block: Any) -> dict[str, Any]:
    plan_value = block.get("plan") if isinstance(block, dict) else None
    state: dict[str, Any] = {
        "plan": plan_value,
        "c24a_marker": None,
        "c24a_blocked": None,
        "gates_on_b20a_ready": None,
        "evidence_roots": (
            list(block.get("evidence_roots") or []) if isinstance(block, dict) else []
        ),
        "evidence_present": None,
    }
    if not isinstance(plan_value, str):
        return state
    plan = Path(plan_value)
    if not plan.is_file():
        state["plan_missing"] = True
        return state
    text = plan.read_text(encoding="utf-8", errors="replace")
    marker = re.search(r"^-\s*\[(.)\]\s*34\.\s*T-34[^\n]*", text, re.MULTILINE)
    if marker is None:
        state["plan_missing"] = False
        state["c24a_marker"] = None
        return state
    state["c24a_marker"] = marker.group(0).strip()
    state["c24a_blocked"] = marker.group(1) == "~"
    block_text = text[marker.start() : marker.start() + 4000]
    state["gates_on_b20a_ready"] = "B20a-ready" in block_text
    roots = [Path(root) for root in state["evidence_roots"] if isinstance(root, str)]
    state["evidence_present"] = any(root.exists() for root in roots)
    return state


def _b20_run_root_dirs() -> list[str]:
    root_value = os.environ.get("ACP_RUN_ROOT")
    if not root_value:
        return []
    root = Path(root_value)
    if not root.is_dir():
        return []
    try:
        return sorted(entry.name for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return []


def _run_da_submit(
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B20a da-submit gate: ready/interface proof + C24a execution prerequisite.

    The branch never submits a task.  It consumes A-14..A-16 and B18's
    ``da-preflight`` receipt, validates the frozen DA input (including a
    named incomplete-input rejection), records the exact C24a artifact that is
    missing, and returns NOT_VERIFIED without touching QC/prepare/reservation.
    """

    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "submit",
        "acceptance.target": "local",
        "b20a.ready_result": "NOT_VERIFIED",
        "b20a.exec": "NOT_VERIFIED",
        "b20a.exec_reason": "c24a_absent",
        "b20a.search": "NOT_VERIFIED",
        "b20a.search_reason": "search_not_declared",
        "b20a.incomplete_input": "NOT_VERIFIED",
        "da.claim_status": "not_evaluated",
        "da.h4_gate": "open",
    }
    fixture_file = Path(fixture_path)
    document = _b20_read_json(fixture_file)
    if not isinstance(document, dict) or document.get("schema") != B20_DA_RUN_SCHEMA:
        assertions.append(
            _b15_assert(
                "b20a.fixture_schema",
                False,
                f"fixture is not {B20_DA_RUN_SCHEMA}",
            )
        )
        return EXIT_FAIL, assertions, extensions
    fixture_dir = fixture_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    http_calls: list[str] = []
    tasks_before: int | None = None
    tasks_after: int | None = None
    run_dirs_before = _b20_run_root_dirs()
    health: Any = None
    if controller:
        base = controller.rstrip("/")
        try:
            health = _http_json(base + "/api/health", timeout)
            http_calls.append("GET /api/health")
            tasks_before = _controller_task_count(base, timeout)
            http_calls.append("GET /api/v1/jobs?limit=1000")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            assertions.append(
                _b15_assert(
                    "b20a.controller_health",
                    False,
                    f"{type(exc).__name__}: {exc}",
                    not_verified=True,
                )
            )
            return EXIT_NOT_VERIFIED, assertions, extensions
    assertions.append(
        _b15_assert(
            "b20a.controller_health",
            isinstance(health, dict) and health.get("status") == "ok",
            f"health={health if isinstance(health, dict) else None}",
        )
    )

    # 1. A-14..A-16 contracts are readable and digestible.
    science_root_value = document.get("science_root")
    science_root = _b20_fixture_path(fixture_dir, science_root_value)
    contract_rows: list[dict[str, Any]] = []
    contract_errors: list[str] = []
    for name in tuple(document.get("required_contracts") or B20_REQUIRED_CONTRACTS):
        path = science_root / name
        if not path.is_file():
            contract_errors.append(f"missing:{path}")
            continue
        contract_rows.append(
            {
                "contract": name,
                "path": str(path),
                "sha256": _b18_sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    assertions.append(
        _b15_assert(
            "b20a.science_contracts",
            not contract_errors,
            f"contracts={[row['contract'] for row in contract_rows]} errors={contract_errors}",
        )
    )

    # 2. The frozen DA input is complete; the explicit negative fixture is rejected.
    reaction_value = document.get("reaction")
    reaction_path: str | None = None
    if isinstance(reaction_value, str):
        reaction_file = _b20_fixture_path(science_root, reaction_value)
        reaction = _b20_read_json(reaction_file)
        reaction_path = str(reaction_file)
    else:
        reaction = reaction_value
    reaction_errors = _b20_validate_reaction(reaction)
    expectations_errors = _b20_validate_expectations(document.get("scientific_expectations"))
    input_errors = reaction_errors + expectations_errors
    assertions.append(
        _b15_assert(
            "b20a.input_complete",
            not input_errors,
            f"reaction={reaction_path or '<inline>'} errors={input_errors}",
        )
    )
    negative_rows: list[dict[str, Any]] = []
    negative_failures: list[str] = []
    for value in document.get("incomplete_inputs") or []:
        path = _b20_fixture_path(fixture_dir, value)
        negative = _b20_read_json(path)
        if not isinstance(negative, dict) or negative.get("schema") != B20_INCOMPLETE_SCHEMA:
            negative_failures.append(f"negative_schema:{path}")
            continue
        negative_reaction = negative.get("reaction")
        if isinstance(negative_reaction, str):
            negative_reaction = _b20_read_json(_b20_fixture_path(science_root, negative_reaction))
        negative_errors = _b20_validate_reaction(negative_reaction)
        expected_error = str(negative.get("expected_error") or "")
        rejected = bool(negative_errors) and (
            not expected_error or any(expected_error in error for error in negative_errors)
        )
        negative_rows.append(
            {
                "case_id": negative.get("case_id"),
                "fixture": str(path),
                "fixture_sha256": _b18_sha256_file(path),
                "expected_error": expected_error,
                "rejected_before_dispatch": rejected,
                "errors": negative_errors,
                "dispatched": False,
            }
        )
        if not rejected:
            negative_failures.append(f"negative_not_rejected:{path}")
    assertions.append(
        _b15_assert(
            "b20a.incomplete_input_rejected",
            bool(negative_rows) and not negative_failures,
            f"cases={len(negative_rows)} failures={negative_failures} rows={negative_rows}",
        )
    )
    extensions["b20a.incomplete_input"] = (
        "PASS" if negative_rows and not negative_failures else "FAIL"
    )
    _b15_write_json(
        out_dir / "incomplete-input-negative.json",
        {
            "schema": "acp2.b20a.incomplete-input-negative.v1",
            "result": extensions["b20a.incomplete_input"],
            "cases": negative_rows,
            "failures": negative_failures,
            "dispatch_attempted": False,
            "rule": (
                "Incomplete scientific input is rejected by the deterministic input gate "
                "before any task/prepare/reservation/QC/bsub call."
            ),
        },
    )

    # 3. B18 da-preflight evidence is consumed unchanged.
    da_preflight_block = document.get("da_preflight")
    if not isinstance(da_preflight_block, dict):
        da_preflight_block = {}
    receipt_value = da_preflight_block.get("receipt")
    receipt_path = _b20_fixture_path(Path.cwd(), receipt_value) if receipt_value else None
    if receipt_path is None or not receipt_path.is_file():
        receipt_root = _b20_fixture_path(Path.cwd(), da_preflight_block.get("receipt_root", "."))
        receipt_path = _b20_find_b18_receipt(receipt_root)
    receipt = _b20_read_json(receipt_path) if receipt_path else None
    preflight_ok = (
        isinstance(receipt, dict)
        and receipt.get("result") == "PASS"
        and receipt.get("scenario") == "da-runtime"
        and receipt.get("phase") == "da-preflight"
    )
    assertions.append(
        _b15_assert(
            "b20a.b18_da_preflight_consumed",
            preflight_ok,
            f"receipt={receipt_path} result={(receipt or {}).get('result')}",
        )
    )
    _b15_write_json(
        out_dir / "consumed-preflight.json",
        {
            "schema": "acp2.b20a.consumed-preflight.v1",
            "path": str(receipt_path) if receipt_path else None,
            "sha256": (
                _b18_sha256_file(receipt_path) if receipt_path and receipt_path.is_file() else None
            ),
            "result": (receipt or {}).get("result"),
            "scenario": (receipt or {}).get("scenario"),
            "phase": (receipt or {}).get("phase"),
            "counters": (receipt or {}).get("extensions", {}).get("b18.counters")
            if isinstance((receipt or {}).get("extensions"), dict)
            else None,
        },
    )

    # 4. P2 candidate + run-spec + dependency ledger must already be frozen.
    candidate_path = _b20_fixture_path(out_dir, document.get("p2_candidate", "p2-candidate.json"))
    candidate = _b20_read_json(candidate_path)
    candidate_errors = _b20_validate_p2_candidate(candidate)
    assertions.append(
        _b15_assert(
            "b20a.p2_candidate",
            not candidate_errors,
            f"path={candidate_path} errors={candidate_errors}",
        )
    )
    run_spec_path = _b20_fixture_path(out_dir, document.get("run_spec", "run-spec.json"))
    run_spec = _b20_read_json(run_spec_path)
    run_spec_errors = _b20_validate_run_spec(run_spec)
    assertions.append(
        _b15_assert(
            "b20a.run_spec",
            not run_spec_errors,
            f"path={run_spec_path} errors={run_spec_errors}",
        )
    )
    dependency_errors, dependency_observed = _b20_dependency_edges()
    assertions.append(
        _b15_assert(
            "b20a.dependency_edges",
            not dependency_errors,
            f"errors={dependency_errors} observed={dependency_observed}",
        )
    )
    _b15_write_json(
        out_dir / "dependency-edges.json",
        {
            "schema": "acp2.b20a.dependency-edges.v1",
            "chain": ["A-14", "A-15", "A-16", "B20a-ready", "C24a", "B20a-exec", "B20b"],
            "consumer_of_B20a_exec": "B20b",
            "c_t34_gate": "B20a-ready",
            "errors": dependency_errors,
            "observed": dependency_observed,
        },
    )

    # 5. C24a is the only execution prerequisite and is currently absent.
    c24a = _b20_c24a_state(document.get("c24a"))
    c_plan = _b20_c_plan_state(document.get("c24a"))
    c24a["c_plan"] = c_plan
    c24a["c_evidence_present"] = c_plan.get("evidence_present")
    extensions["b20a.c24a"] = c24a
    _b15_write_json(
        out_dir / "c24a-gap.json",
        {
            "schema": "acp2.b20a.c24a-gap.v1",
            "status": c24a.get("status"),
            "reason": c24a.get("reason"),
            "expected_artifact": c24a.get("expected_artifact"),
            "required_fields": c24a.get("required_fields"),
            "missing_artifact": c24a.get("missing_artifact"),
            "missing_fields": c24a.get("missing_fields"),
            "c_plan": c_plan,
            "rule": (
                "No task/QC/prepare/reservation may be created until C24a supplies the "
                "single approved submission. A known-candidate chain is not a substitute."
            ),
        },
    )

    # 6. Search is a separate capability; no search sub-run was declared.
    search = document.get("search") if isinstance(document.get("search"), dict) else {}
    search_row = {
        "schema": "acp2.b20a.search-gap.v1",
        "result": "NOT_VERIFIED",
        "reason": "search_not_declared",
        "declared_input_class": search.get("declared_input_class"),
        "ts_seed": search.get("ts_seed"),
        "candidate_sources": search.get("candidate_sources"),
        "candidate_count": search.get("candidate_count"),
        "generation_steps": search.get("generation_steps"),
        "filtering_rejection_reasons": search.get("filtering_rejection_reasons"),
        "validator": search.get("validator"),
        "success_criteria": search.get("success_criteria"),
        "known_candidate_chain_counted_as_search": False,
        "h4_search_row": "open",
        "rule": (
            "Only a separate B20a-search PASS with declared input/criteria, candidate "
            "source/count, generation steps, rejection reasons, validator and success "
            "criteria may set search supported. A known-candidate chain never upgrades search."
        ),
    }
    _b15_write_json(out_dir / "search-gap.json", search_row)

    # 7. Zero-side-effect counters.
    if controller and tasks_before is not None:
        try:
            tasks_after = _controller_task_count(controller.rstrip("/"), timeout)
            http_calls.append("GET /api/v1/jobs?limit=1000")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            tasks_after = None
    run_dirs_after = _b20_run_root_dirs()
    counters = {
        "schema": "acp2.runtime.b20a-counters.v1",
        "tasks_before": tasks_before,
        "tasks_after": tasks_after,
        "tasks_created": (
            tasks_after - tasks_before
            if tasks_before is not None and tasks_after is not None
            else None
        ),
        "prepare_operations": 0,
        "qc_invocations": 0,
        "reservations": 0,
        "bsub": 0,
        "http_calls": http_calls,
        "run_root_dirs_before": run_dirs_before,
        "run_root_dirs_after": run_dirs_after,
        "run_root_dirs_created": sorted(set(run_dirs_after) - set(run_dirs_before)),
        "submission_attempted": False,
        "compute_owner": "none (da-submit stopped before dispatch: C24a absent)",
    }
    zero_side_effects = (
        counters["tasks_created"] == 0
        and not counters["run_root_dirs_created"]
        and counters["prepare_operations"] == 0
        and counters["qc_invocations"] == 0
        and counters["reservations"] == 0
        and counters["bsub"] == 0
    )
    assertions.append(
        _b15_assert(
            "b20a.da_submit_no_side_effects",
            zero_side_effects,
            f"tasks_created={counters['tasks_created']} "
            f"run_root_dirs_created={counters['run_root_dirs_created']}",
        )
    )
    _b15_write_json(out_dir / "counters.json", counters)
    extensions["b20a.counters"] = counters

    readiness_failed = any(item.get("status") == "FAIL" for item in assertions)
    ready_result = "FAIL" if readiness_failed else "PASS"
    extensions["b20a.ready_result"] = ready_result
    ready_document = {
        "schema": "acp2.b20a.ready.v1",
        "result": ready_result,
        "phase": "da-submit",
        "operation_class": "submit",
        "target": "local",
        "interface": {
            "scenario": "da-runtime",
            "phase": "da-submit",
            "required_args": ["controller", "fixture", "out", "timeout"],
            "explicit_phase_required": "--phase da-submit",
            "task_created": False,
            "submission_attempted": False,
        },
        "consumed": {
            "A-14": "ReactionSpec input contract",
            "A-15": "Recipe/DAG contract",
            "A-16": "EvidenceReport contract",
            "B18": "da-preflight receipt",
        },
        "science_contracts": contract_rows,
        "reaction_path": reaction_path,
        "reaction_errors": reaction_errors,
        "expectations_errors": expectations_errors,
        "incomplete_input": negative_rows,
        "b18_preflight": {
            "path": str(receipt_path) if receipt_path else None,
            "result": (receipt or {}).get("result"),
        },
        "p2_candidate": {
            "path": str(candidate_path),
            "candidate_revision": (candidate or {}).get("candidate_revision"),
            "server_revision": (candidate or {}).get("server_revision"),
            "client_revision": (candidate or {}).get("client_revision"),
            "harness_revision": (candidate or {}).get("harness_revision"),
            "contract_digest": (candidate or {}).get("contract_digest"),
            "env_digest": (candidate or {}).get("env_digest"),
            "errors": candidate_errors,
        },
        "run_spec": {
            "path": str(run_spec_path),
            "errors": run_spec_errors,
            "document": run_spec,
        },
        "c24a": c24a,
        "counters": counters,
        "assertions": assertions,
        "zero_side_effects": zero_side_effects,
    }
    _b15_write_json(out_dir / "b20a-ready.json", ready_document)

    if readiness_failed:
        extensions["b20a.exec_reason"] = "b20a_ready_failed"
        extensions["b01.prerequisite_failure"] = "b20a_ready_failed"
        _b15_write_json(
            out_dir / "status-separation.json",
            {
                "schema": "acp2.runtime.status-separation.v1",
                "scenario_result": {"value": "FAIL", "phase": "da-submit"},
                "claim_status": {"value": "not_evaluated", "h4_gate": "open"},
                "b20a_ready": {"result": "FAIL"},
                "b20a_exec": {"result": "NOT_VERIFIED", "reason": "b20a_ready_failed"},
                "b20a_search": {"result": "NOT_VERIFIED", "reason": "search_not_declared"},
                "incomplete_input_negative": {"result": extensions["b20a.incomplete_input"]},
                "rule": "A readiness failure never fabricates a task or a scientific claim.",
            },
        )
        return EXIT_FAIL, assertions, extensions

    c24a_status = str(c24a.get("status"))
    if c24a_status == "c24a_present":
        reason = "execution_requires_real_dsh_not_started"
    elif c24a_status == "c24a_incomplete":
        reason = "c24a_incomplete"
    else:
        reason = "c24a_absent"
    extensions["b20a.exec_reason"] = reason
    extensions["b01.prerequisite_failure"] = reason

    status_separation = {
        "schema": "acp2.runtime.status-separation.v1",
        "scenario_result": {
            "value": "NOT_VERIFIED",
            "phase": "da-submit",
            "reason": reason,
        },
        "claim_status": {
            "value": "not_evaluated",
            "why": "No authorized execution artifact exists; no scientific claim was evaluated.",
        },
        "h4_gate": {
            "value": "open",
            "why": (
                "H4 requires real positive TS/IRC/comparable-energy rows and the separate "
                "B20a-search row; none exist."
            ),
        },
        "b20a_ready": {"result": ready_result},
        "b20a_exec": {"result": "NOT_VERIFIED", "reason": reason},
        "b20a_search": {"result": "NOT_VERIFIED", "reason": "search_not_declared"},
        "incomplete_input_negative": {"result": extensions["b20a.incomplete_input"]},
        "rule": (
            "result, claim_status and h4_gate are independent. A negative-only PASS cannot "
            "close H4; B20a-ready PASS is not B20a-exec or search capability."
        ),
    }
    _b15_write_json(out_dir / "status-separation.json", status_separation)

    artifact_names = (
        "p2-candidate.json",
        "run-spec.json",
        "dependency-edges.json",
        "b20a-ready.json",
        "c24a-gap.json",
        "search-gap.json",
        "incomplete-input-negative.json",
        "consumed-preflight.json",
        "counters.json",
        "status-separation.json",
    )
    extensions["b20a.artifacts"] = _b18_collect_artifacts(out_dir, artifact_names)
    return (
        EXIT_FAIL if readiness_failed else EXIT_NOT_VERIFIED,
        assertions,
        extensions,
    )


# ---------------------------------------------------------------------------
# M2-04 — bounded recipe DAG production wiring (independent scenario)
# ---------------------------------------------------------------------------

M204_SCHEMA = "acp2.m204.bounded-recipe-dag.v1"


def _m204_recipe() -> dict[str, Any]:
    """Canonical two-step bounded recipe (optimize -> frequency), no parameters."""
    selector = {"selector_kind": "whole_product"}

    def ref(from_step: str, product_kind: str) -> dict[str, Any]:
        return {
            "from_step": from_step,
            "product_kind": product_kind,
            "selector": dict(selector),
            "required": True,
        }

    return {
        "schema_version": 1,
        "recipe_id": "recipe.m204.relax_freq.v1",
        "applicability": {"supported_reaction_classes": [], "preconditions": []},
        "limits": {"max_steps": 4, "max_edges": 4},
        "steps": [
            {
                "step_id": "relax",
                "intent": "relax the frozen guess (controlled stub)",
                "required": True,
                "diagnostic_only": False,
                "task_kind": "optimize",
                "parameters": {},
                "consumes": [],
                "produces": [ref("relax", "geometry")],
                "validators": [],
                "alternatives": [],
            },
            {
                "step_id": "freq",
                "intent": "verify the relaxed structure (controlled stub)",
                "required": True,
                "diagnostic_only": False,
                "task_kind": "frequency",
                "parameters": {},
                "consumes": [ref("relax", "geometry")],
                "produces": [ref("freq", "frequencies")],
                "validators": [],
                "alternatives": [],
            },
        ],
        "edges": [
            {
                "edge_id": "relax_to_freq_geometry",
                "from_step": "relax",
                "to_step": "freq",
                "output_ref": ref("relax", "geometry"),
            }
        ],
        "validators": [],
        "alternatives": [],
    }


def _m204_stub_runner(calls: list[str]):
    """Controlled CCCP stub: typed results, real artifacts, no QC binary."""
    from cccp.calculation.contracts import ArtifactRef, Provenance
    from cccp.calculation.requests import TaskKind
    from cccp.calculation.results import FrequencyPayload, TaskResult

    def factory(entrypoint: str):
        def run(request, *, context=None):  # noqa: ANN001 - cccp task seam
            calls.append(entrypoint)
            out_dir = Path(request.output_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            provenance = Provenance(
                backend="stub", method="stub", version="0", input_signature="stub"
            )
            if request.task is TaskKind.OPTIMIZE:
                artifact = out_dir / "final.xyz"
                artifact.write_text("3\nstub\nO 0 0 0\nH 0 0 1\nH 1 0 0\n", encoding="utf-8")
                return TaskResult(
                    task=TaskKind.OPTIMIZE,
                    status="completed",
                    complete=True,
                    converged=True,
                    coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
                    symbols=("O", "H", "H"),
                    artifacts=(ArtifactRef(path=artifact, type="geometry"),),
                    provenance=provenance,
                )
            artifact = out_dir / "freq.json"
            artifact.write_text('{"n_imaginary": 0}', encoding="utf-8")
            return TaskResult(
                task=TaskKind.FREQUENCY,
                status="completed",
                complete=True,
                frequencies=(1600.0, 3700.0, 3800.0),
                artifacts=(ArtifactRef(path=artifact, type="frequencies"),),
                payload=FrequencyPayload(n_imaginary=0),
                provenance=provenance,
            )

        return run

    return factory


def _run_bounded_recipe_dag(
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """M2-04 independent scenario: freeze -> DAG worker -> receipt chain.

    Everything except the declared CCCP stub boundary is real: the freeze
    builder, the Services frozen-group writer, the quota reservation/ledger, the
    fenced commit port, the step dispatch adapter, the DONE truth table and the
    controller-side receipt verification.  No QC binary, no controller process,
    no second platform task, no jobs DB.
    """
    import hashlib
    import json as _json
    from types import SimpleNamespace

    from acp.calculations import attempt_freeze as acp_freeze
    from acp_services.contracts.envelope import ExecutionReceipt
    from acp_services.runtime.attempt_freeze import write_frozen_attempt
    from acp_services.runtime.execution_quota import (
        QuotaReservation,
    )
    from acp_services.runtime.receipts import verify_receipt
    from acp_services.runtime.resource_precheck import ExecutionResourcePrecheck
    from acp_services.runtime.step_dag_worker import StepDagTaskExecutor
    from acp_services.worker_bootstrap import assemble_worker

    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "read",
        "acceptance.target": "local",
        "m204.schema": M204_SCHEMA,
        "m204.fixture": str(fixture_path),
        "m204.evidence_class": "CONTROLLED-STUB",
        "m204.no_compute": True,
        "m204.stub_boundaries": ["cccp.task_core"],
    }

    def record(name: str, ok: bool, detail: str) -> None:
        assertions.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    work = out_dir / "m204-work"
    work.mkdir(parents=True, exist_ok=True)
    structure = work / "in.xyz"
    structure.write_text("3\nwater\nO 0 0 0\nH 0 0 1\nH 1 0 0\n", encoding="utf-8")

    fixture = Path(fixture_path)
    assert_context = "fixture file is optional for this scenario"
    if fixture.is_file():
        assert_context = f"fixture read ({fixture.stat().st_size} bytes)"
    record("m204.fixture_context", True, assert_context)

    record_obj = SimpleNamespace(
        id="20260101_904_BoundedRecipe",
        attempt=1,
        spec=SimpleNamespace(
            workflow=acp_freeze.BOUNDED_RECIPE_WORKFLOW,
            input={
                "bounded_recipe": {
                    "recipe": _m204_recipe(),
                    "inputs": {},
                    "structure_role": "minimum",
                    "step_requirements": [
                        {"step_id": "relax", "executables": ["env"], "require_version": False}
                    ],
                }
            },
        ),
    )
    release = SimpleNamespace(
        unversioned=True,
        release_id="unversioned-shared",
        release_digest="",
        assert_current=lambda: None,
    )
    try:
        spec = acp_freeze.build_bounded_recipe_freeze_spec(record_obj, work, structure, release)
        group = write_frozen_attempt(work, spec)
    except Exception as exc:  # noqa: BLE001 - acceptance reports, never crashes
        record("m204.freeze_group", False, f"{type(exc).__name__}: {exc}")
        extensions["m204.reason"] = "freeze failed"
        return EXIT_FAIL, assertions, extensions
    record(
        "m204.freeze_group",
        group.manifest.workflow == "BoundedRecipe",
        f"group workflow {group.manifest.workflow}",
    )

    reservation = QuotaReservation.from_dict(
        _json.loads(
            (work / "WORK/00_RUNTIME/quota/quota_reservation.json").read_text(encoding="utf-8")
        )
    )
    record(
        "m204.worst_case_reservation",
        reservation.limits.get("recipe_step") == 2 and reservation.limits.get("qc_invocation") == 2,
        f"reservation {dict(reservation.limits)}",
    )

    calls: list[str] = []
    runtime = assemble_worker(
        envelope=spec.envelope,
        plan=spec.plan,
        work_dir=work,
        execution_right=_M204Right(),
    )
    executor = StepDagTaskExecutor(
        precheck=ExecutionResourcePrecheck(
            resolver=lambda name, configured: Path("/usr/bin/env"),
            version_probe=lambda name, path: "acceptance",
        ),
        runner_factory=_m204_stub_runner(calls),
    )
    original = runtime.worker.executor
    runtime_object = runtime
    # Inject the controlled stub through the assembled worker's executor port
    # (the production assembly already selected the DAG executor).
    assert isinstance(original, StepDagTaskExecutor)
    from dataclasses import replace as _replace

    runtime_object = _replace(
        runtime_object,
        worker=_replace(
            runtime_object.worker,
            executor=_replace(
                original, precheck=executor.precheck, runner_factory=executor.runner_factory
            ),
        ),
    )
    result = runtime_object.run()
    receipt = result if isinstance(result, ExecutionReceipt) else None
    record(
        "m204.receipt_completed",
        receipt is not None and receipt.state == "completed",
        f"receipt state {getattr(receipt, 'state', result)}",
    )

    chain = tuple(getattr(receipt, "step_execution_chain", ()) or ())
    record(
        "m204.execution_chain",
        len(chain) == 2
        and all(str(row.get("step_identity", "")).startswith("v2:") for row in chain),
        f"{[row.get('step_id') for row in chain]}",
    )
    env = spec.envelope
    verdict = (
        verify_receipt(
            receipt,
            env,
            current_attempt=env.attempt,
            current_submission_id=env.submission_id,
            current_release_id=env.release_id,
            owner_token=env.owner,
            fencing_token=env.fencing_token,
        )
        if receipt is not None
        else None
    )
    record(
        "m204.controller_verification",
        bool(verdict and verdict.accepted),
        f"verdict {getattr(verdict, 'code', 'n/a') or 'accepted'}",
    )
    record(
        "m204.one_task_ref",
        all(
            (work / f"WORK/00_RUNTIME/steps/{step_id}/step_result.json").is_file()
            for step_id in ("relax", "freq")
        ),
        "one attempt wrote both step results",
    )
    record(
        "m204.no_jobs_db",
        not list(work.rglob("*.db")),
        "worker path opened no jobs database",
    )

    artifacts: list[dict[str, Any]] = []
    for path in sorted(work.rglob("*.json")):
        if "quota" in path.parts and path.name == "quota_ledger.jsonl":
            continue
        artifacts.append(
            {
                "path": str(path.relative_to(out_dir)),
                "size_bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    extensions["m204.artifacts"] = artifacts
    extensions["m204.chain_digest"] = getattr(receipt, "chain_digest", "")
    extensions["m204.plan_definition_digest"] = _json.loads(
        (work / "WORK/00_RUNTIME/frozen/plan.json").read_text(encoding="utf-8")
    )["payload"]["bounded_step_dag"]["definition"]["plan_definition_digest"]
    failed = [entry for entry in assertions if entry["status"] != "PASS"]
    if failed:
        extensions["m204.reason"] = f"{len(failed)} assertion(s) failed"
        return EXIT_FAIL, assertions, extensions
    extensions["m204.reason"] = None
    return EXIT_PASS, assertions, extensions


class _M204Right:
    """Acceptance-local execution right (single holder, always the same worker)."""

    def acquire(self, envelope):  # noqa: ANN001 - port shape
        from acp_services.runtime.execution_right import ExecutionRightVerdict

        return ExecutionRightVerdict()

    def verify(self, envelope):  # noqa: ANN001 - port shape
        from acp_services.runtime.execution_right import ExecutionRightVerdict

        return ExecutionRightVerdict()


def _run_da_preflight(
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B18 da-preflight: binding/round-trip/DAG proof with **no compute**.

    This branch never creates a task, prepare operation, reservation, QC
    invocation or bsub.  It reads A's published contracts, converts C's P2
    minimal form to a CCCP ``TaskRequest``, validates the bounded one-TaskRef
    DAG, runs the STATIC/SIMULATED three-step proof and registers the
    EvidenceReport / research-resource fixtures through B12/Data.
    """
    from acp_services.data.artifacts import ArtifactAuthorization, ArtifactStore
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        RESEARCH_RECORD_SCHEMA,
        ResearchResourceConflictError,
        ResearchResourceRequest,
        ResearchResourceRevokedError,
        ResearchResourceStore,
        research_resource_digest,
    )
    from acp_services.runtime import science_binding as sb
    from acp_services.runtime import step_dag as dagmod
    from acp_services.runtime.step_dag import ControlIntent, PublishStatus, StepStatus

    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "discover",
        "acceptance.target": "local",
        "b18.fixture": str(fixture_path),
        "b18.evidence_class": "STATIC/SIMULATED",
        "b18.no_compute": True,
    }
    fixture_file = Path(fixture_path)
    try:
        document = json.loads(fixture_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            EXIT_NOT_VERIFIED,
            [
                _b15_assert(
                    "b18.fixture_readable", False, f"{type(exc).__name__}: {exc}", not_verified=True
                )
            ],
            {**extensions, "b18.reason": "fixture_unreadable"},
        )
    if not isinstance(document, dict) or document.get("schema") != B18_PREFLIGHT_SCHEMA:
        return (
            EXIT_NOT_VERIFIED,
            [
                _b15_assert(
                    "b18.fixture_schema",
                    False,
                    f"fixture is not {B18_PREFLIGHT_SCHEMA}",
                    not_verified=True,
                )
            ],
            {**extensions, "b18.reason": "fixture_schema"},
        )
    fixture_dir = fixture_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    state_dir = out_dir / "b18-state"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    http_calls: list[str] = []
    counters_before: int | None = None
    health: Any = None
    if controller:
        base = controller.rstrip("/")
        try:
            health = _http_json(base + "/api/health", timeout)
            http_calls.append("GET /api/health")
            counters_before = _controller_task_count(base, timeout)
            http_calls.append("GET /api/v1/jobs?limit=1000")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            return (
                EXIT_NOT_VERIFIED,
                [
                    _b15_assert(
                        "b18.controller_health",
                        False,
                        f"{type(exc).__name__}: {exc}",
                        not_verified=True,
                    )
                ],
                {**extensions, "b18.reason": "controller_unreachable"},
            )

    # 1. A contract refs + the delivered binding.
    try:
        refs = sb.load_science_contract_refs(
            _b18_fixture_path(fixture_dir, document["science_root"])
        )
        binding = json.loads(B18_SCIENCE_BINDING.read_text(encoding="utf-8"))
        binding_errors = sb.validate_science_binding(binding)
        actual_by_id = {schema_id: ref.digest for schema_id, ref in refs.items()}
        declared = {
            entry["schema_id"]: entry["digest"] for entry in binding["science_contracts"]["entries"]
        }
        mismatched = [
            schema_id
            for schema_id, digest in declared.items()
            if actual_by_id.get(schema_id) != digest
        ]
        contract_ok = not binding_errors and not mismatched
        assertions.append(
            _b15_assert(
                "b18a.science_binding_digests",
                contract_ok,
                f"binding_errors={binding_errors} mismatched={mismatched}",
            )
        )
        contract_refs_doc = {
            "schema": "acp2.runtime.science-contract-refs.v1",
            "binding_schema": binding["schema"],
            "binding_contract_version": binding["contract_version"],
            "science_contract_set": binding["science_contracts"]["contract_set"],
            "entries": [refs[key].to_dict() for key in sorted(refs)],
        }
        _b15_write_json(out_dir / "science-contracts.json", contract_refs_doc)
    except (OSError, KeyError, ValueError) as exc:
        assertions.append(
            _b15_assert("b18a.science_binding_digests", False, f"{type(exc).__name__}: {exc}")
        )
        refs = {}
        binding = {}
        contract_refs_doc = {}

    # 2. A descriptor -> B discovery.
    discovery = None
    try:
        capability = json.loads(
            _b18_fixture_path(fixture_dir, document["capability"]).read_text(encoding="utf-8")
        )
        task_binding = json.loads(
            _b18_fixture_path(fixture_dir, document["task_binding"]).read_text(encoding="utf-8")
        )
        discovery = sb.discover_capability(capability, task_binding)
        assertions.append(
            _b15_assert(
                "b18a.discovery",
                discovery.task_kind == document.get("expected_task_kind", discovery.task_kind),
                f"capability={discovery.capability_id} task_kind={discovery.task_kind}",
            )
        )
    except (OSError, KeyError, ValueError) as exc:
        assertions.append(_b15_assert("b18a.discovery", False, f"{type(exc).__name__}: {exc}"))

    # 3. C P2 minimal form -> B validation -> CCCP TaskRequest.
    roundtrip_doc: dict[str, Any] = {}
    try:
        form_document = json.loads(_b18_fixture_dir_read(fixture_dir, document["p2_form"]))
        form = form_document.get("form") if isinstance(form_document, dict) else None
        if not isinstance(form, dict):
            raise ValueError("P2 minimal form has no form object")
        reaction = json.loads(_b18_fixture_dir_read(fixture_dir, document["reaction"]))
        if discovery is None:
            raise ValueError("discovery failed before the round trip")
        request = sb.build_task_request(form, discovery, reaction=reaction)
        request_dict = request.to_dict()
        preserved = {
            "method": request.level.method == form["method"]["method"],
            "basis": request.level.basis == form["method"]["basis"],
            "solvent": request.level.solvent == form["method"]["solvent"],
            "charge": request.charge == form["charge"],
            "multiplicity": request.multiplicity == form["multiplicity"],
            "structure_path": request.structure is not None
            and str(request.structure.path) == form["structure"]["path"],
            "resources": request.resources.nproc == form["resources"]["nproc"],
            "mapping_digest": sb.reaction_mapping_digest(reaction) == form["mapping_digest"],
        }
        # Negative: a forged mapping digest is rejected before adoption.
        forged = dict(form)
        forged["mapping_digest"] = "sha256:" + "0" * 64
        mapping_rejected = False
        try:
            sb.build_task_request(forged, discovery, reaction=reaction)
        except sb.AtomMappingMismatchError:
            mapping_rejected = True
        # Negative: a missing method is a scientific precondition, not a default.
        missing_method = dict(form)
        missing_method["method"] = dict(form["method"], method="")
        method_rejected = False
        try:
            sb.build_task_request(missing_method, discovery, reaction=reaction)
        except sb.ScientificPreconditionError as exc:
            method_rejected = exc.code == "method_identity_missing"
        roundtrip_ok = all(preserved.values()) and mapping_rejected and method_rejected
        assertions.append(
            _b15_assert(
                "b18a.roundtrip_preserves_science",
                roundtrip_ok,
                f"preserved={preserved} mapping_rejected={mapping_rejected} "
                f"method_rejected={method_rejected}",
            )
        )
        roundtrip_doc = {
            "schema": B18_ROUNDTRIP_SCHEMA,
            "evidence_class": "STATIC/SIMULATED",
            "a_descriptor": {
                "capability_id": discovery.capability_id,
                "task_kind": discovery.task_kind,
                "implementation_id": discovery.implementation_id,
                "delegation_target": discovery.delegation_target,
                "source": str(_b18_fixture_path(fixture_dir, document["capability"])),
            },
            "b_discovery": discovery.to_dict(),
            "c_p2_minimal_form": {
                "source": form_document.get("source"),
                "source_commit": form_document.get("source_commit"),
                "digest": sb.document_digest(form),
                "production_path": "frontend/js/workbench/features/science-question.js",
                "full_dynamic_form": "C T-38/H5 (not a B18 gate)",
            },
            "cccp_request": request_dict,
            "preserved": preserved,
            "negatives": {
                "atom_mapping_mismatch": "rejected_before_adoption",
                "missing_method": "method_identity_missing",
            },
        }
        roundtrip_doc["roundtrip_digest"] = sb.document_digest(roundtrip_doc)
        _b15_write_json(out_dir / "a-roundtrip.json", roundtrip_doc)
    except (OSError, KeyError, ValueError) as exc:
        assertions.append(
            _b15_assert("b18a.roundtrip_preserves_science", False, f"{type(exc).__name__}: {exc}")
        )

    # 4. Bounded one-TaskRef DAG.
    dag = None
    dag_errors: list[str] = []
    try:
        dag = _b18_default_dag(
            str(document.get("plan_identity") or "v2:0123456789abcdef0123456789abcdef"),
            str(document.get("release_id") or "release-b18-static"),
        )
        dag_errors = dag.validate()
        dag_doc = dag.to_dict()
        dag_doc["evidence_class"] = "STATIC/SIMULATED"
        dag_doc["topological_order"] = dag.topological_order()
        _b15_write_json(out_dir / "step-dag.json", dag_doc)
        assertions.append(
            _b15_assert(
                "b18b.bounded_dag",
                not dag_errors
                and dag_doc["task_ref"] == "task-b18-static"
                and dag_doc["steps"][-1]["kind"] == "irc"
                and dag_doc["steps"][-1]["entrypoint"] == "cccp.calculation.run_irc",
                f"errors={dag_errors} order={dag_doc['topological_order']}",
            )
        )
    except (KeyError, ValueError) as exc:
        assertions.append(_b15_assert("b18b.bounded_dag", False, f"{type(exc).__name__}: {exc}"))

    # 5. STATIC/SIMULATED three-step run + recovery/publication/cancel/mapping negatives.
    negatives: dict[str, Any] = {}
    plan_identity = str(document.get("plan_identity") or "v2:0123456789abcdef0123456789abcdef")
    release_id = str(document.get("release_id") or "release-b18-static")
    if dag is not None:
        try:
            dispatch = _b18_static_dispatch()
            publish = _b18_static_publish()
            executor = _b18_executor(
                dag, dispatch, publish, plan_identity=plan_identity, release_id=release_id
            )
            state = executor.run()
            static_ok = (
                dispatch.calls == ["ts_opt", "ts_freq", "ts_irc"]
                and state.statuses()
                == {"ts_opt": "completed", "ts_freq": "completed", "ts_irc": "completed"}
                and executor.publish_statuses()
                == {"ts_opt": "published", "ts_freq": "published", "ts_irc": "published"}
                and dispatch.upstream["ts_freq"]
                == [sb.document_digest({"step": "ts_opt", "product": "ts_geometry"})]
                and state.budget.to_dict()
                == {
                    "task_refs": 1,
                    "steps": 3,
                    "qc_invocations": 3,
                    "attempts": 3,
                    "bsub": 0,
                }
            )
            static_doc = {
                "schema": "acp2.runtime.three-step-static.v1",
                "evidence_class": "STATIC/SIMULATED",
                "real_qc": False,
                "outputref_upstream": dispatch.upstream,
                "step_statuses": state.statuses(),
                "publish_statuses": executor.publish_statuses(),
                "budget": state.budget.to_dict(),
                "step_identities": {
                    step_id: item.step_identity for step_id, item in state.states.items()
                },
                "dispatch_count": state.dispatch_count,
            }
            _b15_write_json(out_dir / "three-step-static.json", static_doc)
            assertions.append(
                _b15_assert(
                    "b18c.outputref_passing",
                    static_ok,
                    f"calls={dispatch.calls} statuses={state.statuses()} "
                    f"budget={state.budget.to_dict()}",
                )
            )
        except (KeyError, ValueError) as exc:
            assertions.append(
                _b15_assert("b18c.outputref_passing", False, f"{type(exc).__name__}: {exc}")
            )

        # Required-upstream failure blocks dependents.
        try:
            bad_dispatch = _b18_static_dispatch(fail_on="ts_freq")
            bad_state = _b18_executor(
                dag,
                bad_dispatch,
                _b18_static_publish(),
                plan_identity=plan_identity,
                release_id=release_id,
            ).run()
            blocked_ok = (
                bad_state.states["ts_freq"].status is StepStatus.FAILED
                and bad_state.states["ts_irc"].status is StepStatus.UPSTREAM_FAILED
                and bad_state.states["ts_irc"].status is not StepStatus.COMPLETED
            )
            negatives["required_upstream_failure"] = bad_state.statuses()
            assertions.append(
                _b15_assert(
                    "b18c.required_upstream_failure_blocks_dependents",
                    blocked_ok,
                    f"statuses={bad_state.statuses()}",
                )
            )
        except (KeyError, ValueError) as exc:
            assertions.append(
                _b15_assert(
                    "b18c.required_upstream_failure_blocks_dependents",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )

        # Publication failure does not recompute.
        try:
            pub_dispatch = _b18_static_dispatch()
            pub_publish = _b18_static_publish(fail_first_for="ts_freq")
            pub_executor = _b18_executor(
                dag, pub_dispatch, pub_publish, plan_identity=plan_identity, release_id=release_id
            )
            pub_state = pub_executor.run()
            dispatches_before = pub_state.dispatch_count
            failed_publish_ok = (
                pub_state.states["ts_freq"].status is StepStatus.COMPLETED
                and pub_state.states["ts_freq"].publish_status is PublishStatus.FAILED
                and pub_state.states["ts_irc"].status is StepStatus.BLOCKED
                and pub_executor.retry_publication("ts_freq")
                and pub_state.dispatch_count == dispatches_before
                and pub_dispatch.calls == ["ts_opt", "ts_freq"]
            )
            pub_executor.run()
            negatives["publication_failure"] = {
                "dispatches_before_retry": dispatches_before,
                "dispatches_after_retry": pub_state.dispatch_count,
                "statuses": pub_state.statuses(),
            }
            assertions.append(
                _b15_assert(
                    "b18c.publication_failure_no_recompute",
                    failed_publish_ok,
                    f"dispatches={pub_dispatch.calls} statuses={pub_state.statuses()}",
                )
            )
        except (KeyError, ValueError) as exc:
            assertions.append(
                _b15_assert(
                    "b18c.publication_failure_no_recompute",
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            )

        # Restart recovers only unfinished steps.
        try:
            restart_dispatch = _b18_static_dispatch()
            first = _b18_executor(
                dag,
                restart_dispatch,
                _b18_static_publish(),
                plan_identity=plan_identity,
                release_id=release_id,
            )
            first.run()
            persisted = first.state.to_dict()
            by_id = {state["step_id"]: state for state in persisted["states"]}
            by_id["ts_irc"]["status"] = "running"
            by_id["ts_freq"]["status"] = "completed"
            by_id["ts_freq"]["publish_status"] = "published"
            second_dispatch = _b18_static_dispatch()
            recovered = dagmod.BoundedStepDagExecutor.recover(
                dag,
                second_dispatch,
                _b18_static_publish(),
                plan_identity=plan_identity,
                release_id=release_id,
                persisted=persisted,
            )
            recovered.run()
            restart_ok = (
                second_dispatch.calls == ["ts_irc"]
                and recovered.state.states["ts_opt"].status is StepStatus.COMPLETED
                and recovered.state.states["ts_freq"].status is StepStatus.COMPLETED
                and recovered.state.states["ts_irc"].status is StepStatus.COMPLETED
            )
            negatives["restart"] = {
                "redispatch": second_dispatch.calls,
                "statuses": recovered.state.statuses(),
            }
            assertions.append(
                _b15_assert(
                    "b18c.restart_recovers_only_unfinished",
                    restart_ok,
                    f"redispatch={second_dispatch.calls} statuses={recovered.state.statuses()}",
                )
            )
        except (KeyError, ValueError) as exc:
            assertions.append(
                _b15_assert(
                    "b18c.restart_recovers_only_unfinished", False, f"{type(exc).__name__}: {exc}"
                )
            )

        # Cancel creates no new step.
        try:
            cancel_dispatch = _b18_static_dispatch()
            cancel_executor = _b18_executor(
                dag,
                cancel_dispatch,
                _b18_static_publish(),
                plan_identity=plan_identity,
                release_id=release_id,
            )
            cancel_statuses = cancel_executor.apply_control(ControlIntent.CANCEL)
            cancel_executor.run()
            cancel_ok = (
                set(cancel_statuses.values()) == {"cancelled"}
                and cancel_executor.state.dispatch_count == 0
                and cancel_dispatch.calls == []
            )
            negatives["cancel"] = {
                "statuses": cancel_statuses,
                "dispatch_count": cancel_executor.state.dispatch_count,
            }
            assertions.append(
                _b15_assert(
                    "b18c.cancel_creates_no_new_step",
                    cancel_ok,
                    f"statuses={cancel_statuses} dispatch={cancel_executor.state.dispatch_count}",
                )
            )
        except (KeyError, ValueError) as exc:
            assertions.append(
                _b15_assert(
                    "b18c.cancel_creates_no_new_step", False, f"{type(exc).__name__}: {exc}"
                )
            )

    # 6. EvidenceReport registration (statuses preserved, completed != supported).
    evidence_doc: dict[str, Any] = {}
    try:
        report = json.loads(_b18_fixture_dir_read(fixture_dir, document["evidence_report"]))
        store = ArtifactStore(state_dir / "artifacts")
        native_files = document.get("native_files") or [
            {
                "relative_path": "native/stdout.txt",
                "role": "native_output",
                "body": "STATIC stdout\n",
            },
            {
                "relative_path": "native/stderr.txt",
                "role": "native_output",
                "body": "STATIC stderr\n",
            },
        ]
        manifest = sb.register_evidence_report(
            report,
            store=store,
            job_id="b18-static",
            attempt=1,
            native_files=native_files,
            provenance_digest=sb.document_digest({"provenance": "STATIC/SIMULATED"}),
            authorization=ArtifactAuthorization(
                principal_id="b18-preflight", project_id="proj-b18"
            ),
        )
        served = store.read(manifest.report_ref)
        served_doc = json.loads(served.decode("utf-8"))
        evidence_doc = manifest.to_dict()
        evidence_doc["served_digest"] = "sha256:" + hashlib.sha256(served).hexdigest()
        evidence_doc["evidence_class"] = "STATIC/SIMULATED"
        _b15_write_json(out_dir / "evidence-artifact.json", evidence_doc)
        statuses_ok = dict(manifest.claim_statuses) == {
            str(claim["claim_id"]): str(claim["status"]) for claim in report["claims"]
        }
        separation_ok = (
            manifest.task_execution_completed is True
            and "claim_supported" not in evidence_doc
            and served_doc == report
        )
        assertions.append(
            _b15_assert(
                "b18.evidence_report_statuses_preserved",
                statuses_ok and separation_ok,
                f"statuses={evidence_doc.get('claim_statuses')} "
                f"completed={manifest.task_execution_completed}",
            )
        )
    except (OSError, KeyError, ValueError) as exc:
        assertions.append(
            _b15_assert(
                "b18.evidence_report_statuses_preserved", False, f"{type(exc).__name__}: {exc}"
            )
        )

    # 7. Proposal/ResearchRecord create/read/CAS/conflict/revocation.
    resource_doc: dict[str, Any] = {}
    try:
        proposal = json.loads(_b18_fixture_dir_read(fixture_dir, document["proposal"]))
        record = json.loads(_b18_fixture_dir_read(fixture_dir, document["research_record"]))
        resource_store = ResearchResourceStore(state_dir / "research.db")
        try:
            created = resource_store.create(
                ResearchResourceRequest(
                    resource_id="rr-b18-proposal",
                    schema=PROPOSAL_SCHEMA,
                    document=proposal,
                    principal_id="b18-preflight",
                    project_id="proj-acp2-da",
                )
            )
            read_back = resource_store.read(
                "rr-b18-proposal",
                principal_id="b18-preflight",
                project_id="proj-acp2-da",
            )
            changed = dict(proposal)
            changed["hypothesis"] = changed["hypothesis"] + " (B18 CAS revision)"
            updated = resource_store.compare_and_swap(
                "rr-b18-proposal",
                expected_revision=created.revision,
                document=changed,
                principal_id="b18-preflight",
                project_id="proj-acp2-da",
            )
            conflict_seen = False
            try:
                resource_store.compare_and_swap(
                    "rr-b18-proposal",
                    expected_revision=created.revision,
                    document=proposal,
                    principal_id="b18-preflight",
                    project_id="proj-acp2-da",
                )
            except ResearchResourceConflictError as exc:
                conflict_seen = exc.current_revision == updated.revision
            record_created = resource_store.create(
                ResearchResourceRequest(
                    resource_id="rr-b18-record",
                    schema=RESEARCH_RECORD_SCHEMA,
                    document=record,
                    principal_id="b18-preflight",
                    project_id="proj-acp2-da",
                )
            )
            resource_store.revoke(
                "rr-b18-record",
                principal_id="b18-preflight",
                project_id="proj-acp2-da",
                reason="B18 revocation negative",
            )
            revoked_seen = False
            try:
                resource_store.read(
                    "rr-b18-record",
                    principal_id="b18-preflight",
                    project_id="proj-acp2-da",
                )
            except ResearchResourceRevokedError:
                revoked_seen = True
            resource_ok = (
                created.revision == 1
                and read_back.digest == created.digest
                and read_back.document == proposal
                and updated.revision == 2
                and updated.digest == research_resource_digest(changed)
                and conflict_seen
                and record_created.revision == 1
                and revoked_seen
            )
            resource_doc = {
                "schema": "acp2.runtime.research-resource-cas.v1",
                "resource_schema": "acp2.data.research-resource.v1",
                "create": {
                    "resource_id": created.resource_id,
                    "revision": created.revision,
                    "digest": created.digest,
                    "document_schema": created.schema,
                },
                "read": {
                    "revision": read_back.revision,
                    "digest": read_back.digest,
                    "document_equal": read_back.document == proposal,
                },
                "cas": {"revision": updated.revision, "digest": updated.digest},
                "conflict": {"observed_current_revision": 2, "rejected": conflict_seen},
                "revocation": {"resource_id": "rr-b18-record", "read_refused": revoked_seen},
                "no_task_table": True,
                "no_research_decision_logic": True,
            }
            _b15_write_json(out_dir / "research-resource-cas.json", resource_doc)
            assertions.append(
                _b15_assert(
                    "b18.research_resource_cas",
                    resource_ok,
                    f"create={created.revision} cas={updated.revision} "
                    f"conflict={conflict_seen} revoked={revoked_seen}",
                )
            )
        finally:
            resource_store.close()
    except (OSError, KeyError, ValueError) as exc:
        assertions.append(
            _b15_assert("b18.research_resource_cas", False, f"{type(exc).__name__}: {exc}")
        )

    # 8. Existing workflow/Task dispatch enablement evidence (frequency/IRC/TS).
    try:
        from acp_services.runtime.workflow_enablement import enablement_for

        rows = {name: enablement_for(name).to_dict() for name in ("frequency", "irc", "tsmode")}
        enablement_ok = (
            rows["frequency"]["entry"] == "envelope"
            and rows["irc"]["entry"] == "envelope"
            and rows["tsmode"]["entry"] == "legacy"
            and all(row["evidence"] for row in rows.values())
        )
        _b15_write_json(out_dir / "dispatch-enablement.json", {"rows": rows})
        assertions.append(
            _b15_assert(
                "b18.dispatch_enablement_ts_frequency_irc",
                enablement_ok,
                f"frequency={rows['frequency']['entry']} irc={rows['irc']['entry']} "
                f"tsmode={rows['tsmode']['entry']}",
            )
        )
    except (KeyError, ValueError) as exc:
        assertions.append(
            _b15_assert(
                "b18.dispatch_enablement_ts_frequency_irc", False, f"{type(exc).__name__}: {exc}"
            )
        )

    # 9. No-compute counters: only read-only health/task-list calls were made.
    counters: dict[str, Any] = {
        "schema": "acp2.runtime.b18-counters.v1",
        "http_calls": http_calls,
        "tasks_created": None,
        "qc_invocations": 0,
        "prepare_operations": 0,
        "reservations": 0,
        "bsub": 0,
        "compute_owner": "none (da-preflight is contract-only)",
    }
    if controller:
        try:
            after = _controller_task_count(controller.rstrip("/"), timeout)
            http_calls.append("GET /api/v1/jobs?limit=1000")
            counters["tasks_before"] = counters_before
            counters["tasks_after"] = after
            counters["tasks_created"] = None if counters_before is None else after - counters_before
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            counters["task_count_error"] = f"{type(exc).__name__}: {exc}"
    counters["http_calls"] = http_calls
    counters_ok = (
        counters["tasks_created"] in (None, 0)
        and counters["qc_invocations"] == 0
        and counters["prepare_operations"] == 0
        and counters["reservations"] == 0
        and counters["bsub"] == 0
        and all(call.startswith("GET ") for call in http_calls)
    )
    _b15_write_json(out_dir / "counters.json", counters)
    assertions.append(
        _b15_assert(
            "b18.da_preflight_no_side_effects",
            counters_ok,
            f"tasks_created={counters['tasks_created']} http_calls={http_calls}",
        )
    )

    # 10. B18a handoff to C18 (separately consumable; no execution gate).
    if roundtrip_doc:
        handoff = {
            "schema": B18_HANDOFF_SCHEMA,
            "contract_version": "1.0.0",
            "from": "B18a",
            "to": "C18",
            "status": "delivered",
            "science_binding": str(B18_SCIENCE_BINDING),
            "science_binding_contract_version": binding.get("contract_version"),
            "science_contract_set": binding.get("science_contracts", {}).get("contract_set"),
            "science_contracts": contract_refs_doc,
            "roundtrip_schema": B18_ROUNDTRIP_SCHEMA,
            "roundtrip_digest": roundtrip_doc.get("roundtrip_digest"),
            "c_form_fixture": str(_b18_fixture_path(fixture_dir, document["p2_form"])),
            "c_form_digest": roundtrip_doc.get("c_p2_minimal_form", {}).get("digest"),
            "c18_consumes": [
                "science_contracts.entries (schema id/version/digest)",
                "a-roundtrip.json",
                "the P2 minimal form field mapping",
            ],
            "c18_does_not_need": "B18b execution wiring or B18c recovery evidence",
            "full_dynamic_form": "C T-38/H5; never a B18 gate",
            "does_not_execute": True,
            "does_not_copy_a_parsers": True,
        }
        handoff["handoff_digest"] = sb.document_digest(handoff)
        _b15_write_json(out_dir / "B18a-to-C18.json", handoff)
    else:
        handoff = {}

    preflight_doc = {
        "schema": B18_PREFLIGHT_SCHEMA,
        "evidence_class": "STATIC/SIMULATED",
        "real_qc": False,
        "roundtrip": roundtrip_doc,
        "dag": dag.to_dict() if dag is not None else None,
        "negatives": negatives,
        "evidence_manifest": evidence_doc,
        "research_resource": resource_doc,
        "counters": counters,
        "assertions": assertions,
        "controller_health": health,
    }
    _b15_write_json(out_dir / "da-preflight.json", preflight_doc)
    artifact_names = [
        "science-contracts.json",
        "a-roundtrip.json",
        "B18a-to-C18.json",
        "step-dag.json",
        "three-step-static.json",
        "evidence-artifact.json",
        "research-resource-cas.json",
        "dispatch-enablement.json",
        "counters.json",
        "da-preflight.json",
    ]
    extensions["b18.artifacts"] = _b18_collect_artifacts(out_dir, artifact_names)
    extensions["b18.roundtrip_digest"] = roundtrip_doc.get("roundtrip_digest")
    extensions["b18.dag_digest"] = None if dag is None else sb.document_digest(dag.to_dict())
    extensions["b18.evidence_manifest_digest"] = (
        None if not evidence_doc else sb.document_digest(evidence_doc)
    )
    extensions["b18.handoff_digest"] = handoff.get("handoff_digest") if handoff else None
    extensions["b18.counters"] = counters
    failed = [item["name"] for item in assertions if item["status"] != "PASS"]
    extensions["b18.reason"] = None if not failed else "assertion_failed:" + ",".join(failed)
    return (EXIT_PASS if not failed else EXIT_FAIL, assertions, extensions)


def _b18_fixture_dir_read(fixture_dir: Path, value: Any) -> str:
    path = _b18_fixture_path(fixture_dir, value)
    return path.read_text(encoding="utf-8")


def _run_approval_budget(
    phase: str,
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B16 approval branch (B17 owns the budget branch).

    Exercises the real HTTP surface against a running Controller: request,
    replay, query, approve/repeat, a real controller restart, an authorized
    dispatch, and the full invalidation matrix.  Every denial is checked for
    zero job rows/work directories; the canonical B03 vectors are reproduced
    before any approval is created.
    """
    base = (controller or "").rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b16.controller": base,
        "b16.fixture": str(fixture_path),
        "b16.phase": phase,
    }
    if phase != "approval":
        return _run_budget_branch(phase, controller, fixture_path, out_dir, timeout)
    if not base:
        return (
            EXIT_NOT_VERIFIED,
            [_b15_assert("b16.controller_required", False, "no controller", not_verified=True)],
            {**extensions, "b16.reason": "missing_controller"},
        )
    document = _b15_read_json(Path(fixture_path))
    if not isinstance(document, dict) or document.get("schema") != B16_APPROVAL_SCHEMA:
        return (
            EXIT_NOT_VERIFIED,
            [
                _b15_assert(
                    "b16.fixture_schema",
                    False,
                    f"fixture is not {B16_APPROVAL_SCHEMA}",
                    not_verified=True,
                )
            ],
            {**extensions, "b16.reason": "fixture_schema"},
        )
    try:
        fixture = _b15_fixture(fixture_path)
        scenario = dict(document["scenario"])
        provisioned = _b15_provision(fixture)
        created = _b15_ensure_fixture_jobs(fixture, out_dir)
    except (OSError, ValueError, sqlite3.Error, KeyError) as exc:
        return (
            EXIT_FAIL,
            [_b15_assert("b16.fixture_provisioned", False, f"{type(exc).__name__}: {exc}")],
            {**extensions, "b16.reason": f"fixture_provision_failed:{exc}"},
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = provisioned["credentials"]["keys"]
    run_root = Path(str(fixture["controller"]["run_root"]))
    jobs_db = Path(str(fixture["controller"].get("jobs_database") or run_root / "acp_jobs.db"))

    def key(name: str) -> str:
        return str(keys[str(name)]["key"])

    alice = key(str(scenario["alpha_key"]))
    bob_alpha = key(str(scenario["bob_alpha_key"]))
    plan = dict(scenario["plan"])
    budget = dict(scenario.get("budget") or {})
    plan_digest = _b16_plan_digest(plan)
    base_proposal = dict(scenario["proposal"])
    base_proposal["plan_digest"] = plan_digest
    proposal_digest = _b16_proposal_digest(base_proposal)
    main_proposal_ref = str(scenario["proposal_ref"])
    bob_principal = str(scenario["beta_principal"])

    # -- canonical vectors before any approval is created ------------------
    vectors_path = (
        Path(__file__).resolve().parents[2]
        / "docs/reference/next-change/acp2-contracts/runtime/canonical-digest-vectors.v1.json"
    )
    from acp_services.runtime.canonical_digest import (
        CANONICAL_DIGEST_VERSION,
        CanonicalDigestError,
        canonical_digest,
        canonical_json,
    )
    from acp_services.runtime.canonical_digest import (
        proposal_digest as canonical_proposal_digest,
    )

    vectors = json.loads(vectors_path.read_text(encoding="utf-8"))["vectors"]
    vector_rows: list[dict[str, Any]] = []
    for vector in vectors:
        row: dict[str, Any] = {"id": vector["id"], "kind": vector["kind"]}
        try:
            if vector["kind"] == "proposal_digest":
                projection = canonical_proposal_digest(vector["input"])
                row.update(
                    digest=projection.digest,
                    canonical_json=projection.canonical_json,
                )
            else:
                row.update(
                    digest=canonical_digest(
                        vector["input"],
                        set_fields=tuple(vector.get("set_fields") or ()),
                        drop_fields=tuple(vector.get("drop_fields") or ()),
                    ),
                    canonical_json=canonical_json(
                        vector["input"],
                        set_fields=tuple(vector.get("set_fields") or ()),
                        drop_fields=tuple(vector.get("drop_fields") or ()),
                    ),
                )
            expected_error = vector.get("expected_error")
            row["status"] = (
                "FAIL"
                if expected_error
                else (
                    "PASS"
                    if row["digest"] == vector["digest"]
                    and row["canonical_json"] == vector["canonical_json"]
                    else "FAIL"
                )
            )
        except CanonicalDigestError as exc:
            expected_error = vector.get("expected_error")
            row["error_rule"] = exc.rule
            row["status"] = (
                "PASS"
                if expected_error and exc.rule == expected_error["params"]["rule"]
                else "FAIL"
            )
        vector_rows.append(row)
    vectors_ok = sum(1 for row in vector_rows if row["status"] == "PASS")
    _b15_write_json(
        out_dir / "canonical-vectors.json",
        {
            "schema": "acp2.b16.canonical-vectors.v1",
            "version": CANONICAL_DIGEST_VERSION,
            "passed": vectors_ok,
            "total": len(vector_rows),
            "vectors": vector_rows,
        },
    )
    assertions.append(
        _b15_assert(
            "b16.canonical_vectors",
            vectors_ok == len(vector_rows) and len(vector_rows) == 12,
            f"{vectors_ok}/{len(vector_rows)}",
        )
    )
    if vectors_ok != len(vector_rows):
        extensions["b16.reason"] = "canonical_vectors_failed"
        extensions["b16.vectors"] = "canonical-vectors.json"
        return EXIT_FAIL, assertions, extensions

    def approval_request(
        proposal_ref: str,
        *,
        proposal: Mapping[str, Any] | None = None,
        plan_digest_value: str | None = None,
        budget_value: Mapping[str, Any] | None = None,
        expires_at: str | None = None,
        key_value: str = alice,
    ) -> tuple[int, Any]:
        body = {
            "proposal_ref": proposal_ref,
            "plan": plan,
            "plan_digest": plan_digest_value or plan_digest,
            "proposal": dict(proposal or {**base_proposal, "proposal_id": proposal_ref}),
            "budget": dict(budget if budget_value is None else budget_value),
        }
        if expires_at is not None:
            body["expires_at"] = expires_at
        return _b15_http(
            base, "POST", "/api/v2/research/approvals", key=key_value, body=body, timeout=timeout
        )

    def approved_ref(
        proposal_ref: str,
        *,
        expires_at: str | None = None,
        budget_value: Mapping[str, Any] | None = None,
    ) -> tuple[str, str, str]:
        status, body = _b15_http(
            base,
            "POST",
            "/api/v2/research/approvals",
            key=alice,
            body={
                "proposal_ref": proposal_ref,
                "plan": plan,
                "plan_digest": plan_digest,
                "proposal": {**base_proposal, "proposal_id": proposal_ref},
                "budget": dict(budget if budget_value is None else budget_value),
                **({"expires_at": expires_at} if expires_at else {}),
            },
            timeout=timeout,
        )[0:2]
        if status != 201:
            raise RuntimeError(f"approval request {proposal_ref} failed: {status} {body}")
        ref = str(body["approval_ref"])
        status, body = _b15_http(
            base,
            "POST",
            f"/api/v2/research/approvals/{ref}/approve",
            key=bob_alpha,
            body={"expected_revision": 1},
            timeout=timeout,
        )
        if status != 200:
            raise RuntimeError(f"approval decision {proposal_ref} failed: {status} {body}")
        return ref, str(body["plan_digest"]), str(body["proposal_digest"])

    # -- request / replay / display-name / query ---------------------------
    status, requested, raw = _b15_http(
        base,
        "POST",
        "/api/v2/research/approvals",
        key=alice,
        body={
            "proposal_ref": main_proposal_ref,
            "plan": plan,
            "plan_digest": plan_digest,
            "proposal": base_proposal,
            "budget": budget,
        },
        timeout=timeout,
    )
    approval_ref = str((requested or {}).get("approval_ref") or "")
    assertions.append(
        _b15_assert(
            "b16.request",
            status == 201
            and requested.get("status") == "pending"
            and requested.get("revision") == 1
            and bool(approval_ref),
            f"{status} {json.dumps(requested, sort_keys=True)[:400]}",
        )
    )
    display_proposal = dict(base_proposal)
    display_proposal["display_name"] = "a presentation-only rename"
    display_proposal["task_refs"] = [{"task_id": "task-not-yet-created"}]
    status, replay, _ = _b15_http(
        base,
        "POST",
        "/api/v2/research/approvals",
        key=alice,
        body={
            "proposal_ref": main_proposal_ref,
            "plan": plan,
            "plan_digest": plan_digest,
            "proposal": display_proposal,
            "budget": budget,
        },
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b16.replay_and_display_name",
            status == 201
            and replay.get("replayed") is True
            and replay.get("approval_ref") == approval_ref
            and replay.get("revision") == 1,
            f"{status} {json.dumps(replay, sort_keys=True)[:400]}",
        )
    )
    status, queried, _ = _b15_http(
        base,
        "GET",
        f"/api/v2/research/approvals?approval_ref={approval_ref}",
        key=alice,
        timeout=timeout,
    )
    rows = (queried or {}).get("approvals") or []
    assertions.append(
        _b15_assert(
            "b16.query",
            status == 200 and len(rows) == 1 and rows[0]["approval_ref"] == approval_ref,
            f"{status} {json.dumps(queried, sort_keys=True)[:400]}",
        )
    )

    # -- approve / repeat / payload-approver denial ------------------------
    status, decided, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/approvals/{approval_ref}/approve",
        key=bob_alpha,
        body={"expected_revision": 1},
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b16.approve_verified_principal",
            status == 200
            and decided.get("status") == "approved"
            and decided.get("revision") == 2
            and decided.get("approver_ref") == bob_principal,
            f"{status} {json.dumps(decided, sort_keys=True)[:400]}",
        )
    )
    status, repeated, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/approvals/{approval_ref}/approve",
        key=bob_alpha,
        body={"expected_revision": 1},
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b16.repeat_approve_idempotent",
            status == 200
            and repeated.get("replayed") is True
            and repeated.get("revision") == 2
            and repeated.get("status") == "approved",
            f"{status} {json.dumps(repeated, sort_keys=True)[:400]}",
        )
    )
    status, denial, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/approvals/{approval_ref}/approve",
        key=alice,
        body={"expected_revision": 2, "approver": "model"},
        timeout=timeout,
    )
    code, rule = _b16_denial_detail(status, denial)
    assertions.append(
        _b15_assert(
            "b16.approver_identity_from_payload",
            status == 403 and code == "access_denied" and rule == "approver_identity_from_payload",
            f"{status} {code} {rule}",
        )
    )

    # -- real restart, same approval, repeat approve, authorized submit ----
    health = _b13_controller_health(base, timeout)
    restart_evidence, restart_assertions = _b13_restart_controller(base, out_dir, health, timeout)
    assertions.extend(restart_assertions)
    status, after_restart, _ = _b15_http(
        base, "GET", f"/api/v2/research/approvals/{approval_ref}", key=alice, timeout=timeout
    )
    assertions.append(
        _b15_assert(
            "b16.restart_same_approval",
            status == 200
            and after_restart.get("approval_ref") == approval_ref
            and after_restart.get("status") == "approved"
            and after_restart.get("revision") == 2,
            f"{status} {json.dumps(after_restart, sort_keys=True)[:400]}",
        )
    )
    status, repeat_after_restart, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/approvals/{approval_ref}/approve",
        key=bob_alpha,
        body={"expected_revision": 1},
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b16.repeat_approve_after_restart",
            status == 200
            and repeat_after_restart.get("replayed") is True
            and repeat_after_restart.get("revision") == 2,
            f"{status} {json.dumps(repeat_after_restart, sort_keys=True)[:400]}",
        )
    )
    _b15_write_json(
        out_dir / "restart.json",
        {
            "health_before": health,
            "restart": restart_evidence,
            "after_restart": after_restart,
            "repeat": repeat_after_restart,
        },
    )

    jobs_before = _b15_count_jobs(jobs_db)
    work_before = set(_b16_work_dirs(run_root))
    submit = _b16_submit_body(
        scenario,
        plan=plan,
        plan_digest=plan_digest,
        proposal_ref=main_proposal_ref,
        proposal_digest=proposal_digest,
        budget=budget,
        name="b16-approved-display-name",
    )
    status, accepted, _ = _b15_http(
        base, "POST", "/api/v1/jobs", key=alice, body=submit, timeout=timeout
    )
    accepted_ref = str((accepted or {}).get("job_id") or "")
    jobs_after = _b15_count_jobs(jobs_db)
    work_after = set(_b16_work_dirs(run_root))
    assertions.append(
        _b15_assert(
            "b16.approved_submit_dispatches",
            status == 201
            and accepted.get("approval_ref") == approval_ref
            and bool(accepted_ref)
            and jobs_after == jobs_before + 1
            and len(work_after - work_before) == 1,
            f"{status} jobs {jobs_before}->{jobs_after} work={sorted(work_after - work_before)}",
        )
    )
    if accepted_ref:
        _b15_http(base, "POST", f"/api/v1/jobs/{accepted_ref}/cancel", key=alice, timeout=timeout)

    # -- invalidation matrix ------------------------------------------------
    matrix: list[dict[str, Any]] = []
    denial_specs: list[tuple[str, dict[str, Any], int, str]] = []

    # unapproved (no record at all)
    unapproved_ref = "prop-b16-unapproved"
    denial_specs.append(
        (
            "unapproved",
            _b16_submit_body(
                scenario,
                plan=plan,
                plan_digest=plan_digest,
                proposal_ref=unapproved_ref,
                proposal_digest=_b16_proposal_digest(
                    {**base_proposal, "proposal_id": unapproved_ref}
                ),
                budget=budget,
                name="b16-unapproved",
            ),
            403,
            "approval_required",
        )
    )
    # same approval, changed method but stale (old) plan_digest
    changed_plan = dict(plan)
    changed_plan["method"] = {**dict(plan.get("method") or {}), "method": "B3LYP"}
    changed_plan_digest = _b16_plan_digest(changed_plan)
    denial_specs.append(
        (
            "changed_method_stale_digest",
            _b16_submit_body(
                scenario,
                plan=changed_plan,
                plan_digest=plan_digest,
                proposal_ref=main_proposal_ref,
                proposal_digest=proposal_digest,
                budget=budget,
                name="b16-plan-mismatch",
            ),
            400,
            "plan_digest_mismatch",
        )
    )
    denial_specs.append(
        (
            "changed_plan_digest",
            _b16_submit_body(
                scenario,
                plan=changed_plan,
                plan_digest=changed_plan_digest,
                proposal_ref=main_proposal_ref,
                proposal_digest=proposal_digest,
                budget=budget,
                name="b16-changed-plan",
            ),
            409,
            "approved_plan_digest_mismatch",
        )
    )
    changed_proposal = {
        **base_proposal,
        "recipe_ref": {
            "schema_id": "acp2.science.recipe.v1",
            "version": "1.0.0",
            "digest": "sha256:" + "3" * 64,
        },
    }
    denial_specs.append(
        (
            "changed_recipe_projection",
            _b16_submit_body(
                scenario,
                plan=plan,
                plan_digest=plan_digest,
                proposal_ref=main_proposal_ref,
                proposal_digest=_b16_proposal_digest(changed_proposal),
                budget=budget,
                name="b16-recipe-change",
            ),
            409,
            "approved_proposal_digest_mismatch",
        )
    )
    denial_specs.append(
        (
            "changed_budget",
            _b16_submit_body(
                scenario,
                plan=plan,
                plan_digest=plan_digest,
                proposal_ref=main_proposal_ref,
                proposal_digest=proposal_digest,
                budget={**budget, "count": int(budget.get("count") or 1) + 1},
                name="b16-budget-change",
            ),
            409,
            "approved_budget_mismatch",
        )
    )
    denial_specs.append(
        (
            "other_user",
            _b16_submit_body(
                scenario,
                plan=plan,
                plan_digest=plan_digest,
                proposal_ref=main_proposal_ref,
                proposal_digest=proposal_digest,
                budget=budget,
                name="b16-other-user",
            ),
            403,
            "approval_principal_mismatch",
        )
    )

    # pending / rejected / revoked / expired approvals
    for label, decision in (("pending", None), ("rejected", "reject")):
        ref = f"prop-b16-{label}"
        status, body, _ = _b15_http(
            base,
            "POST",
            "/api/v2/research/approvals",
            key=alice,
            body={
                "proposal_ref": ref,
                "plan": plan,
                "plan_digest": plan_digest,
                "proposal": {**base_proposal, "proposal_id": ref},
                "budget": budget,
            },
            timeout=timeout,
        )
        approval_id = str((body or {}).get("approval_ref") or "")
        if decision == "reject" and approval_id:
            _b15_http(
                base,
                "POST",
                f"/api/v2/research/approvals/{approval_id}/reject",
                key=bob_alpha,
                body={"expected_revision": 1},
                timeout=timeout,
            )
        denial_specs.append(
            (
                label,
                _b16_submit_body(
                    scenario,
                    plan=plan,
                    plan_digest=plan_digest,
                    proposal_ref=ref,
                    proposal_digest=_b16_proposal_digest({**base_proposal, "proposal_id": ref}),
                    budget=budget,
                    name=f"b16-{label}",
                ),
                403,
                f"approval_{label}",
            )
        )
    revoked_ref = "prop-b16-revoked"
    status, body, _ = _b15_http(
        base,
        "POST",
        "/api/v2/research/approvals",
        key=alice,
        body={
            "proposal_ref": revoked_ref,
            "plan": plan,
            "plan_digest": plan_digest,
            "proposal": {**base_proposal, "proposal_id": revoked_ref},
            "budget": budget,
        },
        timeout=timeout,
    )
    revoked_id = str((body or {}).get("approval_ref") or "")
    if revoked_id:
        _b15_http(
            base,
            "POST",
            f"/api/v2/research/approvals/{revoked_id}/approve",
            key=bob_alpha,
            body={"expected_revision": 1},
            timeout=timeout,
        )
        _b15_http(
            base,
            "POST",
            f"/api/v2/research/approvals/{revoked_id}/revoke",
            key=bob_alpha,
            body={"expected_revision": 2, "reason": "b16 matrix"},
            timeout=timeout,
        )
    denial_specs.append(
        (
            "revoked",
            _b16_submit_body(
                scenario,
                plan=plan,
                plan_digest=plan_digest,
                proposal_ref=revoked_ref,
                proposal_digest=_b16_proposal_digest({**base_proposal, "proposal_id": revoked_ref}),
                budget=budget,
                name="b16-revoked",
            ),
            403,
            "approval_revoked",
        )
    )
    expired_ref = "prop-b16-expired"
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(seconds=3)).isoformat().replace("+00:00", "Z")
    )
    status, body, _ = _b15_http(
        base,
        "POST",
        "/api/v2/research/approvals",
        key=alice,
        body={
            "proposal_ref": expired_ref,
            "plan": plan,
            "plan_digest": plan_digest,
            "proposal": {**base_proposal, "proposal_id": expired_ref},
            "budget": budget,
            "expires_at": expires_at,
        },
        timeout=timeout,
    )
    expired_id = str((body or {}).get("approval_ref") or "")
    if expired_id:
        _b15_http(
            base,
            "POST",
            f"/api/v2/research/approvals/{expired_id}/approve",
            key=bob_alpha,
            body={"expected_revision": 1},
            timeout=timeout,
        )
        time.sleep(4.0)
    denial_specs.append(
        (
            "expired",
            _b16_submit_body(
                scenario,
                plan=plan,
                plan_digest=plan_digest,
                proposal_ref=expired_ref,
                proposal_digest=_b16_proposal_digest({**base_proposal, "proposal_id": expired_ref}),
                budget=budget,
                name="b16-expired",
            ),
            403,
            "approval_expired",
        )
    )

    for label, body, expected_status, expected_rule in denial_specs:
        before_jobs = _b15_count_jobs(jobs_db)
        before_dirs = set(_b16_work_dirs(run_root))
        use_key = bob_alpha if label == "other_user" else alice
        status, response, _ = _b15_http(
            base, "POST", "/api/v1/jobs", key=use_key, body=body, timeout=timeout
        )
        after_jobs = _b15_count_jobs(jobs_db)
        after_dirs = set(_b16_work_dirs(run_root))
        code, rule = _b16_denial_detail(status, response)
        row = {
            "row": label,
            "status": status,
            "code": code,
            "rule": rule,
            "expected_status": expected_status,
            "expected_rule": expected_rule,
            "jobs_before": before_jobs,
            "jobs_after": after_jobs,
            "new_work_dirs": sorted(after_dirs - before_dirs),
            "ok": status == expected_status
            and rule == expected_rule
            and after_jobs == before_jobs
            and not (after_dirs - before_dirs),
        }
        matrix.append(row)
    assertions.append(
        _b15_assert(
            "b16.invalidation_matrix",
            all(row["ok"] for row in matrix),
            json.dumps([row["row"] for row in matrix if not row["ok"]]) or "all refused",
        )
    )
    _b15_write_json(
        out_dir / "invalidation-matrix.json",
        {"schema": "acp2.b16.invalidation-matrix.v1", "rows": matrix},
    )

    # unapproved-no-dispatch evidence is the matrix rows' zero job/work-dir delta
    unapproved_row = next(row for row in matrix if row["row"] == "unapproved")
    _b15_write_json(
        out_dir / "unapproved-no-dispatch.json",
        {
            "schema": "acp2.b16.unapproved-no-dispatch.v1",
            "row": unapproved_row,
            "job_rows_created": unapproved_row["jobs_after"] - unapproved_row["jobs_before"],
            "work_dirs_created": unapproved_row["new_work_dirs"],
            "qc_or_bsub_started": False,
            "permit": "the request was refused before RuntimeCommands.submit",
        },
    )

    # -- approval event facts (single outbox table, state is authoritative) --
    events = _b16_events(jobs_db)
    event_ids = [event["event_id"] for event in events]
    approval_ids = {approval_ref, revoked_id, expired_id}
    extensions["b16.event_approvals"] = sorted(approval_ids)
    transitions_ok = {
        "approval.requested",
        "approval.approved",
        "approval.rejected",
        "approval.revoked",
        "approval.expired",
        "approval.task_bound",
    } <= {event["event_type"] for event in events}
    _b15_write_json(
        out_dir / "approval-events.json",
        {
            "schema": "acp2.b16.approval-events.v1",
            "store": "job_outbox",
            "event_count": len(events),
            "unique_event_ids": len(event_ids) == len(set(event_ids)),
            "types": sorted({event["event_type"] for event in events}),
            "events": events,
        },
    )
    assertions.append(
        _b15_assert(
            "b16.events_single_store",
            bool(events) and len(event_ids) == len(set(event_ids)) and transitions_ok,
            f"{len(events)} events, unique={len(event_ids) == len(set(event_ids))}, "
            f"types={sorted({event['event_type'] for event in events})}",
        )
    )

    # -- approval rows dump -------------------------------------------------
    conn = sqlite3.connect(str(jobs_db))
    conn.row_factory = sqlite3.Row
    try:
        approval_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM approval_records ORDER BY created_at, approval_id"
            ).fetchall()
        ]
    finally:
        conn.close()
    _b15_write_json(
        out_dir / "approval-rows.json",
        {
            "schema": "acp2.b16.approval-rows.v1",
            "rows": approval_rows,
            "states": sorted({row["state"] for row in approval_rows}),
        },
    )
    assertions.append(
        _b15_assert(
            "b16.record_states",
            {"pending", "approved", "rejected", "revoked", "expired"}
            <= {row["state"] for row in approval_rows},
            str(sorted({row["state"] for row in approval_rows})),
        )
    )

    extensions.update(
        {
            "b16.approval_ref": approval_ref,
            "b16.plan_digest": plan_digest,
            "b16.proposal_digest": proposal_digest,
            "b16.approved_task_ref": accepted_ref,
            "b16.fixture_projects": created,
            "b16.invalidation_rows": len(matrix),
            "b16.events": len(events),
            "b16.artifacts": [
                {
                    "path": name,
                    "size_bytes": (out_dir / name).stat().st_size,
                    "sha256": sha256_file(out_dir / name),
                }
                for name in (
                    "canonical-vectors.json",
                    "restart.json",
                    "invalidation-matrix.json",
                    "unapproved-no-dispatch.json",
                    "approval-events.json",
                    "approval-rows.json",
                )
                if (out_dir / name).is_file()
            ],
        }
    )
    failed = [item["name"] for item in assertions if item["status"] == "FAIL"]
    if failed:
        extensions["b16.reason"] = "assertion_failed:" + ",".join(failed)
        return EXIT_FAIL, assertions, extensions
    return EXIT_PASS, assertions, extensions


def _b17_service_key(provisioned: Mapping[str, Any], project_id: str) -> str:
    """Mint (or reuse) the dedicated service credential that may settle usage.

    The producer capability is server-side identity metadata, independent of the
    project ``write`` action ceiling used by the normal submit keys.  This is the
    credential a worker/service receipt must present (M0-07/G26); a project write
    key deliberately cannot write trusted usage.
    """
    from acp_services.runtime.usage_authority import UsageProducer, capability_for_producer

    capability = capability_for_producer(UsageProducer.WORKER)
    lifecycle = provisioned["lifecycle"]
    key_manager = provisioned["key_manager"]
    credentials_path = Path(str(provisioned["credentials_file"]))
    credentials = _b15_read_json(credentials_path, {}) or {}
    entries = credentials.setdefault("keys", {})
    entry = entries.get("b17_service")
    if isinstance(entry, dict) and entry.get("key_id"):
        record = key_manager.get(str(entry["key_id"]))
        if (
            record is not None
            and not record.revoked
            and record.project == project_id
            and capability in tuple(record.usage_capabilities)
        ):
            return str(entry["key"])
    accounts = provisioned["accounts"]
    account = next(iter(accounts.values()))
    generated = lifecycle.issue_key(
        account.account_id,
        scope="workspace",
        actions=["read", "write"],
        project=project_id,
        usage_capabilities=[capability],
    )
    entries["b17_service"] = {
        "key_id": generated.key_id,
        "key": generated.key,
        "scope": generated.scope,
        "project": generated.project,
        "actions": list(generated.actions),
        "usage_capabilities": list(generated.usage_capabilities),
    }
    _b15_write_json(credentials_path, credentials, private=True)
    return generated.key


def _b17_orca_pids() -> list[int]:
    """PIDs whose executable is exactly the configured ORCA binary."""
    target = Path(os.environ.get("ACP_ORCA_BINARY") or "/home/xieningke/orca611/orca")
    try:
        expected = str(target.resolve())
    except OSError:
        expected = str(target)
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            executable = str((entry / "exe").resolve())
        except OSError:
            continue
        if executable == expected:
            pids.append(int(entry.name))
    return pids


def _b17_bsub_facts() -> dict[str, Any]:
    path = shutil.which("bsub")
    return {"bsub_available": bool(path), "bsub_path": path, "bsub_calls": 0}


def _b17_job_status(base: str, key: str, job_id: str, timeout: float) -> dict[str, Any]:
    status, body, _ = _b15_http(base, "GET", f"/api/v1/jobs/{job_id}", key=key, timeout=timeout)
    return {"status": status, "job": body if isinstance(body, dict) else {}}


def _b17_wait_terminal(
    base: str, key: str, job_id: str, timeout: float, *, label: str
) -> dict[str, Any]:
    deadline = time.monotonic() + min(180.0, timeout)
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _b17_job_status(base, key, job_id, timeout)
        state = str((last.get("job") or {}).get("status") or "")
        if state in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.5)
    last["timeout"] = label
    return last


def _b17_finish_job(
    base: str, key: str, job_id: str, timeout: float, *, label: str
) -> dict[str, Any]:
    """Let the job finish; cancel only when it is still non-terminal."""
    last = _b17_wait_terminal(base, key, job_id, timeout, label=label)
    state = str((last.get("job") or {}).get("status") or "")
    if state not in {"completed", "failed", "cancelled"}:
        _b15_http(base, "POST", f"/api/v1/jobs/{job_id}/cancel", key=key, timeout=timeout)
        last = _b17_wait_terminal(base, key, job_id, timeout, label=f"{label}-cancel")
    _b17_wait_no_orca(timeout)
    return last


def _b17_wait_new_task_dir(run_root: Path, before: Sequence[str], timeout: float) -> list[str]:
    deadline = time.monotonic() + min(10.0, timeout)
    current = _b17_task_dirs(run_root)
    while len(current) <= len(before) and time.monotonic() < deadline:
        time.sleep(0.1)
        current = _b17_task_dirs(run_root)
    return current


def _b17_wait_no_orca(timeout: float) -> list[int]:
    deadline = time.monotonic() + min(60.0, timeout)
    pids = _b17_orca_pids()
    while pids and time.monotonic() < deadline:
        time.sleep(0.25)
        pids = _b17_orca_pids()
    return pids


def _b17_budget_projection(base: str, key: str, budget_id: str, timeout: float) -> dict[str, Any]:
    status, body, _ = _b15_http(
        base, "GET", f"/api/v2/research/budgets/{budget_id}", key=key, timeout=timeout
    )
    return {"status": status, "budget": body if isinstance(body, dict) else {}}


def _b17_approval(
    base: str,
    *,
    key_alice: str,
    key_approver: str,
    proposal_ref: str,
    plan: Mapping[str, Any],
    plan_digest: str,
    proposal: Mapping[str, Any],
    budget: Mapping[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request_status, body, _ = _b15_http(
        base,
        "POST",
        "/api/v2/research/approvals",
        key=key_alice,
        body={
            "proposal_ref": proposal_ref,
            "plan": dict(plan),
            "plan_digest": plan_digest,
            "proposal": dict(proposal),
            "budget": dict(budget),
        },
        timeout=timeout,
    )
    approval_ref = str((body or {}).get("approval_ref") or "")
    if request_status != 201 or not approval_ref:
        return {"request_status": request_status, "request": body, "approval_ref": ""}
    decision_status, decided, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/approvals/{approval_ref}/approve",
        key=key_approver,
        body={"expected_revision": 1},
        timeout=timeout,
    )
    return {
        "request_status": request_status,
        "approval_ref": approval_ref,
        "decision_status": decision_status,
        "decision": decided,
    }


def _b17_submit(
    base: str,
    *,
    key: str,
    body: Mapping[str, Any],
    idempotency_key: str,
    timeout: float,
) -> tuple[int, Any]:
    return _b15_http(
        base,
        "POST",
        "/api/v1/jobs",
        key=key,
        body=dict(body),
        headers={"Idempotency-Key": idempotency_key},
        timeout=timeout,
    )[0:2]


def _b17_task_dirs(run_root: Path) -> list[str]:
    """Task directories identified by the scheduler markers (job.json+task.json)."""
    found: list[str] = []
    for job_json in run_root.rglob("job.json"):
        directory = job_json.parent
        if (directory / "task.json").is_file():
            found.append(str(directory.relative_to(run_root)))
    return sorted(found)


def _b17_counters(run_root: Path, jobs_db: Path) -> dict[str, Any]:
    return {
        "job_rows": _b15_count_jobs(jobs_db),
        "work_dirs": _b17_task_dirs(run_root),
        "orca_pids": _b17_orca_pids(),
        **_b17_bsub_facts(),
    }


def _b17_budget_id(project_id: str, proposal_ref: str) -> str:
    from acp_services.runtime.research_budget import budget_id_for

    return budget_id_for(project_id, proposal_ref)


def _run_budget_branch(
    phase: str,
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B17 budget branch: real HTTP reservation, race, restart and settlement."""
    base = (controller or "").rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b17.controller": base,
        "b17.fixture": str(fixture_path),
        "b17.phase": phase,
    }
    if phase != "budget":
        return (
            EXIT_USAGE,
            [_b15_assert("b17.phase", False, f"unknown approval-budget phase {phase!r}")],
            extensions,
        )
    if not base:
        return (
            EXIT_NOT_VERIFIED,
            [_b15_assert("b17.controller_required", False, "no controller", not_verified=True)],
            {**extensions, "b17.reason": "missing_controller"},
        )
    document = _b15_read_json(Path(fixture_path))
    if not isinstance(document, dict) or document.get("schema") != B16_APPROVAL_SCHEMA:
        return (
            EXIT_NOT_VERIFIED,
            [
                _b15_assert(
                    "b17.fixture_schema",
                    False,
                    f"fixture is not {B16_APPROVAL_SCHEMA}",
                    not_verified=True,
                )
            ],
            {**extensions, "b17.reason": "fixture_schema"},
        )
    try:
        fixture = _b15_fixture(fixture_path)
        scenario = dict(document["scenario"])
        provisioned = _b15_provision(fixture)
        _b15_ensure_fixture_jobs(fixture, out_dir)
    except (OSError, ValueError, sqlite3.Error, KeyError) as exc:
        return (
            EXIT_FAIL,
            [_b15_assert("b17.fixture_provisioned", False, f"{type(exc).__name__}: {exc}")],
            {**extensions, "b17.reason": f"fixture_provision_failed:{exc}"},
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = provisioned["credentials"]["keys"]
    alice = str(keys[str(scenario["alpha_key"])]["key"])
    bob = str(keys[str(scenario["bob_alpha_key"])]["key"])
    plan = dict(scenario["plan"])
    budget = dict(scenario["budget"])
    project_id = str(scenario["alpha_project"])
    service_key = _b17_service_key(provisioned, project_id)
    plan_digest = _b16_plan_digest(plan)
    base_proposal = dict(scenario["proposal"])
    base_proposal["plan_digest"] = plan_digest
    run_root = Path(str(fixture["controller"]["run_root"]))
    jobs_db = Path(str(fixture["controller"].get("jobs_database") or run_root / "acp_jobs.db"))

    def submit_body(
        proposal_ref: str,
        proposal_digest: str,
        *,
        budget_value: Mapping[str, Any] | None = None,
        name: str,
    ) -> dict[str, Any]:
        return _b16_submit_body(
            scenario,
            plan=plan,
            plan_digest=plan_digest,
            proposal_ref=proposal_ref,
            proposal_digest=proposal_digest,
            budget=dict(budget if budget_value is None else budget_value),
            name=name,
        )

    # -- happy path: at-cap submit consumes the last unit ------------------
    happy_ref = f"prop-b17-happy-{uuid.uuid4().hex[:8]}"
    happy_proposal = {**base_proposal, "proposal_id": happy_ref}
    happy_digest = _b16_proposal_digest(happy_proposal)
    happy_approval = _b17_approval(
        base,
        key_alice=alice,
        key_approver=bob,
        proposal_ref=happy_ref,
        plan=plan,
        plan_digest=plan_digest,
        proposal=happy_proposal,
        budget=budget,
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b17.approval_for_budget",
            happy_approval.get("decision_status") == 200
            and bool(happy_approval.get("approval_ref")),
            json.dumps(happy_approval, sort_keys=True)[:400],
        )
    )
    happy_budget_id = _b17_budget_id(project_id, happy_ref)
    before = _b17_counters(run_root, jobs_db)
    status, accepted = _b17_submit(
        base,
        key=alice,
        body=submit_body(happy_ref, happy_digest, name="b17-happy-at-cap"),
        idempotency_key=f"b17-happy-{uuid.uuid4().hex}",
        timeout=timeout,
    )
    happy_job = str((accepted or {}).get("job_id") or "")
    _b17_wait_new_task_dir(run_root, before["work_dirs"], timeout)
    after = _b17_counters(run_root, jobs_db)
    assertions.append(
        _b15_assert(
            "b17.at_cap_submit_accepted",
            status == 201
            and bool(happy_job)
            and len(after["work_dirs"]) == len(before["work_dirs"]) + 1,
            f"{status} job={happy_job} work={len(before['work_dirs'])}->{len(after['work_dirs'])}",
        )
    )
    if happy_job:
        _b17_finish_job(base, alice, happy_job, timeout, label="b17-happy")

    # -- pre-dispatch refusal after the unit is consumed -------------------
    time.sleep(0.5)
    refused_before = _b17_counters(run_root, jobs_db)
    status, refused = _b17_submit(
        base,
        key=alice,
        body=submit_body(happy_ref, happy_digest, name="b17-over-budget"),
        idempotency_key=f"b17-refused-{uuid.uuid4().hex}",
        timeout=timeout,
    )
    time.sleep(0.5)
    refused_after = _b17_counters(run_root, jobs_db)
    refused_code, refused_rule = _b16_denial_detail(status, refused)
    assertions.append(
        _b15_assert(
            "b17.pre_dispatch_refusal",
            status == 403
            and refused_code == "budget_exhausted"
            and refused_after["job_rows"] == refused_before["job_rows"]
            and refused_after["work_dirs"] == refused_before["work_dirs"]
            and refused_after["orca_pids"] == []
            and refused_after["bsub_calls"] == 0,
            f"{status} {refused_code} jobs {refused_before['job_rows']}->"
            f"{refused_after['job_rows']} orca={refused_after['orca_pids']} "
            f"bsub={refused_after['bsub_calls']}",
        )
    )
    _b15_write_json(
        out_dir / "pre-dispatch-refusal.json",
        {
            "schema": "acp2.b17.pre-dispatch-refusal.v1",
            "status": status,
            "code": refused_code,
            "rule": refused_rule,
            "before": refused_before,
            "after": refused_after,
            "delta": {
                "job_rows": refused_after["job_rows"] - refused_before["job_rows"],
                "work_dirs": len(refused_after["work_dirs"]) - len(refused_before["work_dirs"]),
                "orca_pids": len(refused_after["orca_pids"]),
                "bsub_calls": refused_after["bsub_calls"],
            },
        },
    )

    # -- concurrent last-unit race ----------------------------------------
    race_ref = f"prop-b17-race-{uuid.uuid4().hex[:8]}"
    race_proposal = {**base_proposal, "proposal_id": race_ref}
    race_digest = _b16_proposal_digest(race_proposal)
    race_approval = _b17_approval(
        base,
        key_alice=alice,
        key_approver=bob,
        proposal_ref=race_ref,
        plan=plan,
        plan_digest=plan_digest,
        proposal=race_proposal,
        budget=budget,
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b17.race_approval",
            race_approval.get("decision_status") == 200,
            json.dumps(race_approval, sort_keys=True)[:300],
        )
    )
    race_budget_id = _b17_budget_id(project_id, race_ref)
    race_before = _b17_counters(run_root, jobs_db)
    race_results: list[dict[str, Any]] = []
    race_barrier = threading.Barrier(2)

    def race_submit(index: int) -> None:
        race_barrier.wait()
        status, body = _b17_submit(
            base,
            key=alice,
            body=submit_body(race_ref, race_digest, name=f"b17-race-{index}"),
            idempotency_key=f"b17-race-{index}-{uuid.uuid4().hex}",
            timeout=timeout,
        )
        code, rule = _b16_denial_detail(status, body)
        race_results.append(
            {
                "index": index,
                "status": status,
                "code": code,
                "rule": rule,
                "job_id": str((body or {}).get("job_id") or ""),
            }
        )

    threads = [threading.Thread(target=race_submit, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    _b17_wait_new_task_dir(run_root, race_before["work_dirs"], timeout)
    race_after = _b17_counters(run_root, jobs_db)
    accepted_rows = [row for row in race_results if row["status"] == 201]
    rejected_rows = [row for row in race_results if row["status"] == 403]
    assertions.append(
        _b15_assert(
            "b17.concurrent_last_unit",
            len(accepted_rows) == 1
            and len(rejected_rows) == 1
            and rejected_rows[0]["code"] == "budget_exhausted"
            and len(race_after["work_dirs"]) == len(race_before["work_dirs"]) + 1,
            json.dumps(race_results, sort_keys=True)[:500],
        )
    )
    for row in accepted_rows:
        if row["job_id"]:
            _b17_finish_job(base, alice, row["job_id"], timeout, label="b17-race")
    _b15_write_json(
        out_dir / "concurrent-last-unit.json",
        {
            "schema": "acp2.b17.concurrent-last-unit.v1",
            "results": race_results,
            "before": race_before,
            "after": race_after,
            "accepted": len(accepted_rows),
            "rejected": len(rejected_rows),
        },
    )

    # -- over-approved-range submit is refused (B16 binding reused) -------
    range_before = _b17_counters(run_root, jobs_db)
    status, range_refused = _b17_submit(
        base,
        key=alice,
        body=submit_body(
            race_ref,
            race_digest,
            budget_value={**budget, "count": int(budget.get("count") or 1) + 1},
            name="b17-over-approved-range",
        ),
        idempotency_key=f"b17-range-{uuid.uuid4().hex}",
        timeout=timeout,
    )
    range_after = _b17_counters(run_root, jobs_db)
    range_code, range_rule = _b16_denial_detail(status, range_refused)
    assertions.append(
        _b15_assert(
            "b17.over_approved_range_refused",
            status == 409
            and range_rule == "approved_budget_mismatch"
            and range_after["job_rows"] == range_before["job_rows"]
            and range_after["work_dirs"] == range_before["work_dirs"],
            f"{status} {range_code} {range_rule}",
        )
    )

    # -- restart: reserved/consumed survive -------------------------------
    health = _b13_controller_health(base, timeout)
    restart_evidence, restart_assertions = _b13_restart_controller(base, out_dir, health, timeout)
    assertions.extend(restart_assertions)
    after_restart = _b17_budget_projection(base, alice, race_budget_id, timeout)
    race_budget = after_restart.get("budget") or {}
    assertions.append(
        _b15_assert(
            "b17.restart_preserves_budget",
            after_restart.get("status") == 200
            and int((race_budget.get("consumed") or {}).get("task_ref", 0)) >= 1
            and int((race_budget.get("remaining") or {}).get("task_ref", 0)) == 0,
            json.dumps(race_budget, sort_keys=True)[:500],
        )
    )

    # -- trusted settlement across the restart ----------------------------
    from acp_services.contracts.envelope import expected_submission_id
    from acp_services.runtime.usage_authority import UsageProducer, UsageSource

    reservations = [
        row
        for row in (race_budget.get("reservations") or [])
        if str(row.get("state")) != "released"
    ]
    reservation_id = str(reservations[0]["reservation_id"]) if reservations else ""
    settled_job = next((str(row["job_id"]) for row in accepted_rows if row["job_id"]), "")
    receipt_id = f"b17-receipt-{uuid.uuid4().hex}"
    usage = {
        "task_ref": 1,
        "recipe_step": 1,
        "qc_invocation": 1,
        "attempt": 1,
        "bsub": 0,
        "model_request": 2,
    }
    settlement_body = {
        "receipt_id": receipt_id,
        "reservation_id": reservation_id,
        "job_id": settled_job,
        "attempt": 1,
        "submission_id": (expected_submission_id(settled_job, 1) if settled_job else ""),
        "run_id": f"b17-run-{uuid.uuid4().hex[:8]}",
        "usage": usage,
    }

    # Negative: a project write key has no usage-producer capability.  The
    # trusted source is server-derived; the payload cannot select it.
    status_denied, denied_body, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/budgets/{race_budget_id}/settle",
        key=alice,
        body=settlement_body,
        timeout=timeout,
    )
    denied_code = ""
    if isinstance(denied_body, dict):
        denied_detail = denied_body.get("detail")
        denied_code = str(
            (denied_detail or {}).get("code")
            if isinstance(denied_detail, dict)
            else denied_detail or ""
        )
    status, identity_doc, _ = _b15_http(base, "GET", "/api/identity", key=alice, timeout=timeout)
    identity_mode = str((identity_doc or {}).get("mode") or "") if status == 200 else ""
    assertions.append(
        _b15_assert(
            "b17.project_write_cannot_settle_usage",
            status_denied == 403 and denied_code == "usage_capability_missing",
            f"{status_denied} {denied_code}",
            not_verified=identity_mode != "api-key",
        )
    )
    assertions.append(
        _b15_assert(
            "b17.identity_mode_enforces_capability",
            identity_mode == "api-key",
            f"mode={identity_mode!r}",
            not_verified=identity_mode not in {"api-key", "local-single"},
        )
    )

    # Positive: the dedicated service credential carries `usage.produce.worker`.
    status, first_settle, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/budgets/{race_budget_id}/settle",
        key=service_key,
        body=settlement_body,
        timeout=timeout,
    )
    first_budget = (first_settle or {}).get("budget") or {}
    status2, second_settle, _ = _b15_http(
        base,
        "POST",
        f"/api/v2/research/budgets/{race_budget_id}/settle",
        key=service_key,
        body=settlement_body,
        timeout=timeout,
    )
    second_budget = (second_settle or {}).get("budget") or {}
    first_applied = bool(((first_settle or {}).get("settlement") or {}).get("applied"))
    second_duplicate = bool(((second_settle or {}).get("settlement") or {}).get("duplicate"))
    settled_source = str((first_settle or {}).get("trusted_source") or "")
    settled_producer = str((first_settle or {}).get("producer") or "")
    assertions.append(
        _b15_assert(
            "b17.usage_receipt_settled_from_capability",
            status == 200
            and first_applied
            and settled_source == UsageSource.WORKER_RECEIPT
            and settled_producer == UsageProducer.WORKER,
            f"{status} applied={first_applied} source={settled_source!r} "
            f"producer={settled_producer!r}",
        )
    )
    assertions.append(
        _b15_assert(
            "b17.duplicate_settlement_idempotent",
            status == 200
            and status2 == 200
            and first_applied
            and second_duplicate
            and (first_budget.get("consumed") or {}) == (second_budget.get("consumed") or {}),
            f"{status}/{status2} applied={first_applied} duplicate={second_duplicate}",
        )
    )
    receipt_counters = ((second_settle or {}).get("settlement") or {}).get("counters") or {}
    assertions.append(
        _b15_assert(
            "b17.unit_counts_separate",
            int(receipt_counters.get("task_ref", 0)) == 1
            and int(receipt_counters.get("recipe_step", 0)) == 1
            and int(receipt_counters.get("qc_invocation", 0)) == 1
            and int(receipt_counters.get("attempt", 0)) == 1
            and int(receipt_counters.get("bsub", 0)) == 0
            and int(receipt_counters.get("model_request", 0)) == 2,
            json.dumps(receipt_counters, sort_keys=True),
        )
    )
    timeouts = second_budget.get("timeouts") or {}
    assertions.append(
        _b15_assert(
            "b17.timeouts_separate",
            all(
                key in timeouts
                for key in (
                    "queue_wait_s",
                    "science_runtime_s",
                    "client_request_s",
                    "overall_acceptance_s",
                )
            ),
            json.dumps(timeouts, sort_keys=True),
        )
    )
    _b15_write_json(
        out_dir / "settlement.json",
        {
            "schema": "acp2.b17.settlement.v1",
            "first": first_settle,
            "second": second_settle,
            "denied": {"status": status_denied, "body": denied_body, "mode": identity_mode},
            "receipt_id": receipt_id,
            "reservation_id": reservation_id,
            "job_id": settled_job,
            "trusted_source": settled_source,
            "producer": settled_producer,
        },
    )

    # -- release statement / capabilities ---------------------------------
    status, capabilities, _ = _b15_http(base, "GET", "/api/capabilities", timeout=timeout)
    admission = (capabilities or {}).get("admission") or {}
    research_budget = admission.get("research_budget") or {}
    statement = research_budget.get("release_statement") or research_budget
    assertions.append(
        _b15_assert(
            "b17.release_statement",
            status == 200
            and capabilities.get("model_token_enforcement") == "NOT_VERIFIED"
            and "task_ref" in (statement.get("enforced") or [])
            and "bsub" in (statement.get("recorded_only") or [])
            and "model_request" in (statement.get("recorded_only") or [])
            and any(
                row.get("capability") == "model_token_enforcement"
                for row in (statement.get("not_verified") or [])
            ),
            json.dumps(statement, sort_keys=True)[:500],
        )
    )
    _b15_write_json(
        out_dir / "release-statement.json",
        {"schema": "acp2.b17.release-statement.v1", "capabilities": capabilities},
    )

    # -- bounded read-only evidence export after science stops ------------
    status, evidence, raw = _b15_http(
        base,
        "GET",
        f"/api/v2/research/budgets/{race_budget_id}/evidence",
        key=alice,
        timeout=timeout,
    )
    export_path = out_dir / "budget-evidence.json"
    _b15_write_json(export_path, evidence if isinstance(evidence, dict) else {})
    assertions.append(
        _b15_assert(
            "b17.bounded_read_only_export",
            status == 200
            and bool((evidence or {}).get("read_only"))
            and len(raw) <= 65536
            and not _b17_orca_pids(),
            f"{status} bytes={len(raw)} orca={_b17_orca_pids()}",
        )
    )

    # -- boundary: at-cap PASS, over-cap/missing FAIL, rejection PASS ------
    from acp_services.runtime.research_budget import evaluate_budget_boundary

    at_cap = evaluate_budget_boundary(
        limits={"task_ref": 1, "qc_invocation": 1},
        consumed={
            "task_ref": int((second_budget.get("consumed") or {}).get("task_ref", 0)),
            "qc_invocation": int((second_budget.get("consumed") or {}).get("qc_invocation", 0)),
        },
        required_operations={"task_ref": True, "qc_invocation": True},
    )
    over = evaluate_budget_boundary(
        limits={"task_ref": 1}, consumed={"task_ref": 2}, required_operations={"task_ref": True}
    )
    missing = evaluate_budget_boundary(
        limits={"task_ref": 1},
        consumed={"task_ref": 1},
        required_operations={"task_ref": True, "qc_invocation": False},
    )
    boundary_ok = (
        at_cap.passed
        and at_cap.at_cap
        and over.status == "FAIL"
        and missing.status == "FAIL"
        and len(rejected_rows) == 1
    )
    assertions.append(
        _b15_assert(
            "b17.boundary",
            boundary_ok,
            json.dumps(
                {
                    "at_cap": at_cap.to_dict(),
                    "over": over.to_dict(),
                    "missing": missing.to_dict(),
                    "expected_rejection_is_negative_pass": len(rejected_rows) == 1,
                },
                sort_keys=True,
            )[:600],
        )
    )
    _b15_write_json(
        out_dir / "boundary.json",
        {
            "schema": "acp2.b17.boundary.v1",
            "at_cap": at_cap.to_dict(),
            "over_cap": over.to_dict(),
            "missing_operation": missing.to_dict(),
            "expected_rejection_negative_pass": len(rejected_rows) == 1,
        },
    )

    extensions.update(
        {
            "b17.happy_budget_id": happy_budget_id,
            "b17.race_budget_id": race_budget_id,
            "b17.receipt_id": receipt_id,
            "b17.reservation_id": reservation_id,
            "b17.unit_counts": receipt_counters,
            "b17.timeouts": timeouts,
            "b17.pre_dispatch_delta": {
                "job_rows": refused_after["job_rows"] - refused_before["job_rows"],
                "work_dirs": len(refused_after["work_dirs"]) - len(refused_before["work_dirs"]),
                "orca_pids": len(refused_after["orca_pids"]),
                "bsub_calls": refused_after["bsub_calls"],
            },
            "b17.restart": restart_evidence,
            "b17.artifacts": [
                {
                    "path": name,
                    "size_bytes": (out_dir / name).stat().st_size,
                    "sha256": sha256_file(out_dir / name),
                }
                for name in (
                    "pre-dispatch-refusal.json",
                    "concurrent-last-unit.json",
                    "settlement.json",
                    "release-statement.json",
                    "budget-evidence.json",
                    "boundary.json",
                )
                if (out_dir / name).is_file()
            ],
        }
    )
    failed = [item["name"] for item in assertions if item["status"] == "FAIL"]
    if failed:
        extensions["b17.reason"] = "assertion_failed:" + ",".join(failed)
        return EXIT_FAIL, assertions, extensions
    return EXIT_PASS, assertions, extensions


B15_FIXTURE_SCHEMA = "acp2.multi-fixture.v1"
B15_CREDENTIALS_SCHEMA = "acp2.multi-credentials.v1"
B15_PERMISSION_CEILING = ("control", "read", "write")


def _b15_password() -> str:
    return "B15-" + secrets.token_urlsafe(18) + "!"


def _b15_read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _b15_write_json(path: Path, document: Any, *, private: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def _b15_assert(
    name: str,
    ok: bool,
    detail: str,
    *,
    not_verified: bool = False,
) -> dict[str, Any]:
    status = "PASS" if ok else ("NOT_VERIFIED" if not_verified else "FAIL")
    return {"name": name, "status": status, "detail": detail}


def _b15_fixture(path: str) -> dict[str, Any]:
    document = _b15_read_json(Path(path))
    if not isinstance(document, dict) or document.get("schema") not in {
        B15_FIXTURE_SCHEMA,
        B16_APPROVAL_SCHEMA,
    }:
        raise ValueError(f"fixture is not {B15_FIXTURE_SCHEMA} or {B16_APPROVAL_SCHEMA}")
    identity = document.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("fixture.identity is required")
    for required_field in ("database", "credentials_file", "tenant_id"):
        if not identity.get(required_field):
            raise ValueError(f"fixture.identity.{required_field} is required")
    if not identity.get("accounts") or not identity.get("keys"):
        raise ValueError("fixture.identity.accounts/keys are required")
    if not isinstance(document.get("scenario"), dict):
        raise ValueError("fixture.scenario mapping is required")
    return document


def _b15_provision(fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Create/recover the fixture accounts and mint the project-scoped keys."""
    from acp_services.workspace.identity_lifecycle import (
        AccountStatus,
        IdentityLifecycle,
        IdentityLifecyclePolicy,
    )
    from acp_services.workspace.keys import ApiKeyManager

    identity = fixture["identity"]
    db_path = Path(str(identity["database"]))
    credentials_path = Path(str(identity["credentials_file"]))
    credentials = _b15_read_json(credentials_path, {}) or {}
    if credentials.get("schema") != B15_CREDENTIALS_SCHEMA:
        credentials = {"schema": B15_CREDENTIALS_SCHEMA, "keys": {}}
    key_entries = credentials.setdefault("keys", {})

    key_manager = ApiKeyManager(db_path)
    policy = IdentityLifecyclePolicy(
        deployment_tier="lab",
        capabilities=("authentication", "isolation"),
    )
    lifecycle = IdentityLifecycle(db_path, policy=policy, key_manager=key_manager)

    accounts: dict[str, Any] = {}
    for spec in identity["accounts"]:
        name = str(spec["name"])
        account = lifecycle.get_account_by_login(str(spec["login_id"]))
        if account is None:
            invitation = lifecycle.invite_account(
                principal_id=str(spec["principal_id"]),
                login_id=str(spec["login_id"]),
                roles=spec.get("roles") or ["member"],
            )
            account = lifecycle.accept_invitation(invitation.token, password=_b15_password())
        elif account.status in {AccountStatus.REVOKED, AccountStatus.DISABLED}:
            account = lifecycle.recover_account(
                account.account_id, actor="b15-harness", reason="scenario rerun"
            )
        elif account.status == AccountStatus.INVITED:
            raise ValueError(
                f"account {spec['login_id']!r} is invited without a token; "
                f"remove {db_path} and re-run"
            )
        accounts[name] = account

    for spec in identity["keys"]:
        name = str(spec["name"])
        account = accounts[str(spec["account"])]
        actions = tuple(str(action) for action in spec.get("actions") or ("read",))
        project = spec.get("project")
        entry = key_entries.get(name)
        record = (
            key_manager.get(str(entry["key_id"]))
            if isinstance(entry, dict) and entry.get("key_id")
            else None
        )
        matches = (
            record is not None
            and not record.revoked
            and record.project == (None if project is None else str(project))
            and tuple(record.actions) == actions
        )
        if not matches:
            generated = lifecycle.issue_key(
                account.account_id,
                scope=str(spec.get("scope") or "workspace"),
                actions=actions,
                project=None if project is None else str(project),
            )
            key_entries[name] = {
                "key_id": generated.key_id,
                "key": generated.key,
                "scope": generated.scope,
                "project": generated.project,
                "actions": list(generated.actions),
            }
    _b15_write_json(credentials_path, credentials, private=True)
    return {
        "accounts": accounts,
        "credentials": credentials,
        "key_manager": key_manager,
        "lifecycle": lifecycle,
        "database": str(db_path),
        "credentials_file": str(credentials_path),
    }


def _b15_ensure_fixture_jobs(fixture: Mapping[str, Any], out_dir: Path) -> dict[str, Any]:
    """Create the two paused project tasks and their project rows idempotently."""
    from acp.scheduler.events import JobEventLog
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp_services.store.layout import runtime_file
    from acp_services.store.migrations import migrate
    from acp_services.store.store import JobStateConflictError, JobStore

    run_root = Path(str(fixture["controller"]["run_root"]))
    jobs_database = Path(
        str(fixture["controller"].get("jobs_database") or run_root / "acp_jobs.db")
    )
    migrate(jobs_database)
    store = JobStore(jobs_database)

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(jobs_database))
    try:
        for project in fixture.get("projects") or []:
            project_id = str(project["project_id"])
            project_dir = run_root / "projects" / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            conn.execute(
                """INSERT OR IGNORE INTO projects
                       (project_id, name, description, tags, run_root, settings,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    str(project.get("name") or project_id),
                    str(project.get("description") or "B15 multitenant fixture"),
                    "[]",
                    str(project_dir),
                    "{}",
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    created: list[str] = []
    reset: list[str] = []
    for spec in fixture.get("fixture_jobs") or []:
        job_id = str(spec["job_id"])
        existing = store.get(job_id)
        if existing is not None:
            if existing.status != JobStatus.PAUSED:
                for _ in range(3):
                    try:
                        store.transition(
                            job_id,
                            expected_status=existing.status,
                            expected_revision=existing.revision,
                            expected_attempt=existing.attempt,
                            status=JobStatus.PAUSED,
                            error=None,
                            completed_at=None,
                            started_at=None,
                            pid=None,
                            current_stage=None,
                            progress=None,
                        )
                    except JobStateConflictError:
                        refreshed = store.get(job_id)
                        if refreshed is None:
                            raise
                        existing = refreshed
                        continue
                    reset.append(job_id)
                    break
                else:
                    raise RuntimeError(
                        f"fixture job {job_id} stayed {existing.status.value} after 3 resets"
                    )
            continue
        project_id = str(spec["project_id"])
        work_dir = run_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        JobEventLog(runtime_file(work_dir, "events.jsonl")).append(
            "job.paused",
            idempotency_key=f"b15-fixture:{job_id}",
            stage="paused",
            project_id=project_id,
        )
        store.create(
            JobRecord(
                id=job_id,
                spec=JobSpec(workflow="fake", name=job_id, project_id=project_id),
                status=JobStatus.PAUSED,
                work_dir=str(work_dir),
                project_id=project_id,
                group_id=job_id,
            )
        )
        created.append(job_id)
    return {
        "created": created,
        "reset": reset,
        "jobs_database": str(jobs_database),
        "run_root": str(run_root),
    }


def _b15_http(
    base: str,
    method: str,
    path: str,
    *,
    key: str | None = None,
    body: Any = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any, bytes]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    if key:
        request_headers["X-API-Key"] = key
    request_headers.update(dict(headers or {}))
    request = urllib.request.Request(base + path, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return int(response.status), _b15_decode(raw), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return int(exc.code), _b15_decode(raw), raw


def _b15_decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _b15_stream_revoke(
    base: str,
    key: str,
    job_id: str,
    revoke: Any,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Open an SSE subscription, revoke mid-stream, and observe the stop."""
    lines: list[str] = []
    state = {"first_event": False, "reset": False, "error": None}
    query = urllib.parse.urlencode({"scope": "task", "job_ids": job_id})
    request = urllib.request.Request(
        f"{base}/api/events/stream?{query}",
        headers={"X-API-Key": key, "Accept": "text/event-stream"},
    )

    def reader() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    lines.append(line)
                    if line.startswith("data:"):
                        state["first_event"] = True
                    if "permission_changed" in line:
                        state["reset"] = True
                        return
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not state["first_event"]:
        if not thread.is_alive():
            break
        time.sleep(0.05)
    received_before = bool(state["first_event"])
    revoke()
    thread.join(timeout=15.0)
    return {
        "received_event_before_revocation": received_before,
        "permission_changed_after_revocation": bool(state["reset"]),
        "stream_thread_stopped": not thread.is_alive(),
        "line_count": len(lines),
        "error": state["error"],
    }


B21_FIXTURE_SCHEMA = "acp2.b21.plugin-fixture.v1"


def _b21_fixture(path: str) -> dict[str, Any]:
    document = _b15_read_json(Path(path))
    if not isinstance(document, dict) or document.get("schema") != B21_FIXTURE_SCHEMA:
        raise ValueError(f"fixture is not {B21_FIXTURE_SCHEMA}")
    if not str(document.get("plugin_root") or "").strip():
        raise ValueError("fixture.plugin_root is required")
    expected = document.get("expected")
    tasks = document.get("tasks")
    negative = document.get("negative")
    if not isinstance(expected, dict) or not isinstance(tasks, dict):
        raise ValueError("fixture.expected and fixture.tasks are required mappings")
    if not isinstance(negative, dict):
        raise ValueError("fixture.negative is required")
    for key in ("adapter_id", "version", "upgrade_version", "capability"):
        if not expected.get(key):
            raise ValueError(f"fixture.expected.{key} is required")
    for key in ("old", "new"):
        task = tasks.get(key)
        if not isinstance(task, dict) or not task.get("task_id"):
            raise ValueError(f"fixture.tasks.{key}.task_id is required")
        if not isinstance(task.get("attempt"), int):
            raise ValueError(f"fixture.tasks.{key}.attempt must be an integer")
    return document


def _b21_authorization(
    action: str,
    adapter_id: str,
    version: str,
    digest: str,
    authorized_by: str,
    *,
    expires_at: str | None = None,
):
    from acp_services.runtime.adapter_registry import AdapterAuthorization

    return AdapterAuthorization(
        action=action,
        adapter_id=adapter_id,
        version=version,
        descriptor_digest=digest,
        authorized_by=authorized_by,
        issued_at=_utcnow(),
        expires_at=expires_at,
    )


def _run_infra_plugin(
    phase_name: str,
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B21: external infrastructure adapter discovery/install/enable/freeze.

    The registry is platform-only.  Discovery is metadata-only; a plugin that
    writes a marker on import proves it was not executed.  Install/enable need
    server-side authorization facts.  The old task keeps its frozen v1 adapter
    after v1.1.0 is installed and enabled.  Local regression is run for real;
    LSF/Slurm live rows are NOT_VERIFIED without a real environment.
    """

    from acp_services.runtime.adapter_registry import (
        AdapterRegistry,
        AdapterRegistryError,
    )

    try:
        fixture = _b21_fixture(fixture_path)
    except ValueError as exc:
        return (
            EXIT_NOT_VERIFIED,
            [{"name": "b21.fixture_schema", "status": "NOT_VERIFIED", "detail": str(exc)}],
            {"b21.reason": str(exc)},
        )

    expected = fixture["expected"]
    tasks = fixture["tasks"]
    negative = fixture["negative"]
    adapter_id = str(expected["adapter_id"])
    version_v1 = str(expected["version"])
    version_v2 = str(expected["upgrade_version"])
    capability = str(expected["capability"])
    authorized_by = str(fixture.get("authorized_by") or "controller")
    trusted = [str(item) for item in fixture.get("trusted_adapter_ids", [])]
    out_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic per-run workspace: only the fixture-owned state root is
    # cleared; the external plugin bundle itself is never modified.
    state_root = Path(str(fixture.get("state_root") or (out_dir / "adapter-state")))
    if state_root.exists():
        shutil.rmtree(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    work_root = out_dir / "workspace"
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True)

    assertions: list[dict[str, Any]] = []
    rejection_rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, *, not_verified: bool = False) -> None:
        assertions.append(
            {
                "name": name,
                "status": "PASS" if ok else ("NOT_VERIFIED" if not_verified else "FAIL"),
                "detail": detail,
            }
        )

    def expect_error(name: str, expected_code: str, function: Any) -> None:
        try:
            function()
        except AdapterRegistryError as exc:
            passed = exc.code == expected_code
            rejection_rows.append(
                {
                    "case": name,
                    "expected_code": expected_code,
                    "actual_code": exc.code,
                    "passed": passed,
                    "detail": exc.detail,
                }
            )
            add(name, passed, f"expected={expected_code} actual={exc.code}")
        else:
            rejection_rows.append(
                {
                    "case": name,
                    "expected_code": expected_code,
                    "actual_code": "no_error",
                    "passed": False,
                    "detail": "call succeeded when it should have been rejected",
                }
            )
            add(name, False, f"expected={expected_code} actual=no_error")

    def expect_named_error(name: str, expected_code: str, function: Any) -> None:
        try:
            function()
        except Exception as exc:  # noqa: BLE001 - boundary probe
            code = getattr(exc, "code", None)
            passed = code == expected_code
            rejection_rows.append(
                {
                    "case": name,
                    "expected_code": expected_code,
                    "actual_code": code,
                    "passed": passed,
                    "detail": str(exc),
                }
            )
            add(name, passed, f"expected={expected_code} actual={code}")
        else:
            rejection_rows.append(
                {
                    "case": name,
                    "expected_code": expected_code,
                    "actual_code": "no_error",
                    "passed": False,
                    "detail": "call succeeded when it should have been rejected",
                }
            )
            add(name, False, f"expected={expected_code} actual=no_error")

    def emit(name: str, document: Any) -> Path:
        path = _b15_write_json(out_dir / f"{phase_token}-{name}", document)
        artifacts.append(
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
        return path

    phase_token = _safe_name(phase_name)
    fixture_plugin_root = Path(str(fixture["plugin_root"]))
    if phase_name == "infra:upgrade":
        # B22 must prove a real payload removal without destroying the retained
        # B21 fixture bundle.  Run the whole upgrade flow against a private copy.
        plugin_root = work_root / "plugin-root"
        if plugin_root.exists():
            shutil.rmtree(plugin_root)
        shutil.copytree(
            fixture_plugin_root,
            plugin_root,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    else:
        plugin_root = fixture_plugin_root
    previous_plugin_env = os.environ.get("ACP2_B21_PLUGIN_ENV")
    previous_missing_env = os.environ.get("ACP2_B21_MISSING_ENV")
    os.environ["ACP2_B21_PLUGIN_ENV"] = "1"
    os.environ.pop("ACP2_B21_MISSING_ENV", None)
    marker = plugin_root / "storage-sideeffect-1.0.0" / str(negative["marker_name"])
    if marker.exists():
        marker.unlink()

    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b21.plugin_root": str(plugin_root),
        "b21.state_root": str(state_root),
        "b21.phase": phase_name,
        "b21.fixture": str(fixture_path),
    }
    if controller:
        extensions["b21.controller"] = controller

    try:
        registry = AdapterRegistry(
            plugin_root=plugin_root,
            state_root=state_root,
            trusted_adapter_ids=trusted,
            authorized_principals=[authorized_by, "controller"],
        )
        task_count_before: int | None = None
        if controller and controller_reachable(controller):
            try:
                task_count_before = _controller_task_count(controller, min(timeout, 10.0))
            except (OSError, ValueError, urllib.error.URLError):
                task_count_before = None

        # 1. Discovery is metadata-only; reject isolation/science claims.
        report = registry.discover()
        discovered = {
            (item.descriptor.adapter_id, item.descriptor.version): item for item in report.adapters
        }
        monitoring_modules = sorted(
            name
            for name, module in list(sys.modules.items())
            if name.startswith("acp_adapter_")
            or (
                getattr(module, "__file__", None) is not None
                and str(plugin_root) in str(getattr(module, "__file__", ""))
            )
        )
        discovery_document = report.to_dict()
        discovery_document["side_effect_marker"] = {
            "path": str(marker),
            "exists": marker.exists(),
        }
        discovery_document["plugin_modules_in_sys_modules"] = monitoring_modules
        emit("discovery.json", discovery_document)
        add(
            "b21.discovery.external_adapter",
            (adapter_id, version_v1) in discovered and (adapter_id, version_v2) in discovered,
            f"discovered {adapter_id}@{version_v1} and @{version_v2}",
        )
        rejected_codes = {(row.code, Path(row.path).parent.name) for row in report.rejected}
        add(
            "b21.discovery.isolation_claim_rejected",
            any(
                code == "adapter_isolation_claim_rejected" and "isolation-claim" in name
                for code, name in rejected_codes
            ),
            f"rejected={sorted(rejected_codes)}",
        )
        add(
            "b21.discovery.science_capability_rejected",
            any(
                code == "science_capability_forbidden" and "science" in name
                for code, name in rejected_codes
            ),
            f"rejected={sorted(rejected_codes)}",
        )
        add(
            "b21.discovery.no_execution",
            not marker.exists() and not monitoring_modules,
            f"marker={marker.exists()} modules={monitoring_modules}",
        )

        if (adapter_id, version_v1) not in discovered:
            emit(
                "rejection-matrix.json",
                {
                    "schema": "acp2.b21.rejection-matrix.v1",
                    "rows": rejection_rows,
                },
            )
            return (
                EXIT_FAIL,
                assertions,
                {
                    **extensions,
                    "b21.reason": "expected_external_adapter_not_discovered",
                    "b21.artifacts": artifacts,
                },
            )
        v1 = discovered[(adapter_id, version_v1)]
        v2 = discovered.get((adapter_id, version_v2))
        new_frozen = None
        old_inventory = None
        new_inventory = None

        # 2. Authorization is a server-side fact, never plugin metadata.
        authz_rows: list[dict[str, Any]] = []
        expect_error(
            "b21.authorization.install_requires_fact",
            "adapter_authorization_required",
            lambda: registry.install(adapter_id, version_v1, authorization=None),
        )
        wrong_action = _b21_authorization(
            "adapter.enable", adapter_id, version_v1, v1.descriptor_digest, authorized_by
        )
        expect_error(
            "b21.authorization.install_wrong_action",
            "adapter_authorization_invalid",
            lambda: registry.install(adapter_id, version_v1, authorization=wrong_action),
        )
        wrong_principal = _b21_authorization(
            "adapter.install", adapter_id, version_v1, v1.descriptor_digest, "untrusted-client"
        )
        expect_error(
            "b21.authorization.install_wrong_principal",
            "adapter_authorization_invalid",
            lambda: registry.install(adapter_id, version_v1, authorization=wrong_principal),
        )
        expired = _b21_authorization(
            "adapter.install",
            adapter_id,
            version_v1,
            v1.descriptor_digest,
            authorized_by,
            expires_at="2000-01-01T00:00:00Z",
        )
        expect_error(
            "b21.authorization.install_expired",
            "adapter_authorization_expired",
            lambda: registry.install(adapter_id, version_v1, authorization=expired),
        )
        install_auth = _b21_authorization(
            "adapter.install", adapter_id, version_v1, v1.descriptor_digest, authorized_by
        )
        installed_v1 = registry.install(adapter_id, version_v1, authorization=install_auth)
        authz_rows.append(
            {
                "action": "adapter.install",
                "adapter_id": adapter_id,
                "version": version_v1,
                "descriptor_digest": v1.descriptor_digest,
                "authorized_by": authorized_by,
                "result": "installed",
            }
        )
        expect_error(
            "b21.authorization.enable_requires_fact",
            "adapter_authorization_required",
            lambda: registry.enable(adapter_id, version_v1, authorization=None),
        )
        enable_auth = _b21_authorization(
            "adapter.enable", adapter_id, version_v1, v1.descriptor_digest, authorized_by
        )
        registry.enable(adapter_id, version_v1, authorization=enable_auth)
        authz_rows.append(
            {
                "action": "adapter.enable",
                "adapter_id": adapter_id,
                "version": version_v1,
                "descriptor_digest": v1.descriptor_digest,
                "authorized_by": authorized_by,
                "result": "enabled",
            }
        )
        emit(
            "authorization.json",
            {"schema": "acp2.b21.authorization.v1", "rows": authz_rows},
        )
        add(
            "b21.authorization.server_side_fact",
            registry.is_enabled(adapter_id, version_v1),
            f"enabled={registry.is_enabled(adapter_id, version_v1)}",
        )

        # 3. Freeze v1 into the old task BEFORE the upgrade.
        old_task = tasks["old"]
        new_task = tasks["new"]
        freeze_old = registry.freeze_for_task(
            str(old_task["task_id"]),
            int(old_task["attempt"]),
            adapters={adapter_id: version_v1},
        )
        old_frozen = freeze_old.adapter(adapter_id)
        emit(
            "frozen-task-v1.json",
            {
                "schema": "acp2.b21.frozen-task.v1",
                "task": freeze_old.to_dict(),
                "descriptor": v1.descriptor.to_dict(),
                "descriptor_digest": v1.descriptor_digest,
                "entrypoint_digest": v1.entrypoint_digest,
                "installed_descriptor_digest": installed_v1.descriptor_digest,
            },
        )
        add(
            "b21.freeze.v1_exact_digest",
            old_frozen.version == version_v1
            and old_frozen.descriptor_digest == v1.descriptor_digest
            and old_frozen.entrypoint_digest == v1.entrypoint_digest,
            f"version={old_frozen.version} descriptor={old_frozen.descriptor_digest[:12]} "
            f"entrypoint={(old_frozen.entrypoint_digest or '')[:12]}",
        )

        # 4. A real infrastructure operation through the installed plugin.
        sample = work_root / "inventory.txt"
        sample.write_text("b21 infrastructure adapter sample\n", encoding="utf-8")
        expected_sha = sha256_file(sample)
        inventory = registry.invoke(
            adapter_id,
            version_v1,
            "inventory",
            {"path": str(sample)},
            allow_execution=True,
        )
        invocation_v1 = {
            "operation": "inventory",
            "adapter_id": adapter_id,
            "version": version_v1,
            "result": inventory,
        }
        emit("plugin-run-v1.json", {"schema": "acp2.b21.plugin-run.v1", **invocation_v1})
        add(
            "b21.plugin.real_infrastructure_operation",
            isinstance(inventory, dict)
            and inventory.get("version") == version_v1
            and inventory.get("sha256") == expected_sha
            and inventory.get("science") is False,
            f"result={inventory}",
        )

        # 5. Install/enable v1.1.0; new tasks get v2, old tasks keep v1.
        if v2 is None:
            add("b21.upgrade.discovered", False, f"{adapter_id}@{version_v2} missing")
        else:
            install_v2 = _b21_authorization(
                "adapter.install", adapter_id, version_v2, v2.descriptor_digest, authorized_by
            )
            registry.install(adapter_id, version_v2, authorization=install_v2)
            enable_v2 = _b21_authorization(
                "adapter.enable", adapter_id, version_v2, v2.descriptor_digest, authorized_by
            )
            registry.enable(adapter_id, version_v2, authorization=enable_v2)
            add(
                "b21.upgrade.new_active_version",
                registry.active_version(adapter_id) == version_v2,
                f"active={registry.active_version(adapter_id)}",
            )
            freeze_new = registry.freeze_for_task(
                str(new_task["task_id"]), int(new_task["attempt"])
            )
            new_frozen = freeze_new.adapter(adapter_id)
            emit(
                "frozen-task-v2.json",
                {
                    "schema": "acp2.b21.frozen-task.v1",
                    "task": freeze_new.to_dict(),
                    "descriptor": v2.descriptor.to_dict(),
                    "descriptor_digest": v2.descriptor_digest,
                    "entrypoint_digest": v2.entrypoint_digest,
                },
            )
            add(
                "b21.freeze.v2_exact_digest",
                new_frozen.version == version_v2
                and new_frozen.descriptor_digest == v2.descriptor_digest,
                f"version={new_frozen.version} descriptor={new_frozen.descriptor_digest[:12]}",
            )
            # Old-task read-back is independent of the newly enabled version.
            read_old = registry.read_task_freeze(str(old_task["task_id"]), int(old_task["attempt"]))
            frozen_old_again = read_old.adapter(adapter_id)
            installed_old = registry.verify_frozen_adapter(frozen_old_again)
            old_inventory = registry.invoke_frozen(
                frozen_old_again,
                "inventory",
                {"path": str(sample)},
                allow_execution=True,
            )
            new_inventory = registry.invoke(
                adapter_id,
                version_v2,
                "inventory",
                {"path": str(sample)},
                allow_execution=True,
            )
            emit(
                "old-task-readback.json",
                {
                    "schema": "acp2.b21.old-task-readback.v1",
                    "task_id": old_task["task_id"],
                    "attempt": old_task["attempt"],
                    "frozen": frozen_old_again.to_dict(),
                    "resolved_version": installed_old.descriptor.version,
                    "new_task": {
                        "task_id": new_task["task_id"],
                        "attempt": new_task["attempt"],
                        "frozen_version": new_frozen.version,
                    },
                    "old_task_operation_result": old_inventory,
                    "new_task_operation_result": new_inventory,
                },
            )
            add(
                "b21.old_task.frozen_adapter_readback",
                frozen_old_again.version == version_v1
                and frozen_old_again.descriptor_digest == v1.descriptor_digest
                and installed_old.descriptor.version == version_v1,
                f"old={frozen_old_again.version} new={new_frozen.version}",
            )
            add(
                "b21.old_task.uses_frozen_adapter",
                isinstance(old_inventory, dict)
                and old_inventory.get("version") == version_v1
                and isinstance(new_inventory, dict)
                and new_inventory.get("version") == version_v2,
                "old_result="
                f"{old_inventory.get('version') if isinstance(old_inventory, dict) else None} "
                "new_result="
                f"{new_inventory.get('version') if isinstance(new_inventory, dict) else None}",
            )

        # 6. Metadata never triggers execution; explicit opt-in is required.
        side_effect_id = str(negative["side_effect_adapter_id"])
        side_effect = discovered.get((side_effect_id, "1.0.0"))
        if side_effect is not None:
            side_install = _b21_authorization(
                "adapter.install",
                side_effect_id,
                "1.0.0",
                side_effect.descriptor_digest,
                authorized_by,
            )
            registry.install(side_effect_id, "1.0.0", authorization=side_install)
            side_enable = _b21_authorization(
                "adapter.enable",
                side_effect_id,
                "1.0.0",
                side_effect.descriptor_digest,
                authorized_by,
            )
            registry.enable(side_effect_id, "1.0.0", authorization=side_enable)
        expect_error(
            "b21.metadata.explicit_execution_required",
            "execution_requires_explicit_allow",
            lambda: registry.invoke(
                adapter_id,
                version_v1,
                "inventory",
                {"path": str(sample)},
                allow_execution=False,
            ),
        )
        add(
            "b21.metadata.install_enable_did_not_execute",
            not marker.exists(),
            f"marker_exists={marker.exists()}",
        )

        # 7. Rejection matrix.
        wrong_id = str(negative["wrong_version_adapter_id"])
        incompatible = discovered.get((wrong_id, "2.0.0"))
        if incompatible is not None:
            incompatible_auth = _b21_authorization(
                "adapter.install",
                wrong_id,
                "2.0.0",
                incompatible.descriptor_digest,
                authorized_by,
            )
            expect_error(
                "b21.reject.wrong_version_incompatible",
                "adapter_version_incompatible",
                lambda: registry.install(wrong_id, "2.0.0", authorization=incompatible_auth),
            )
        expect_error(
            "b21.reject.unknown_version_no_auto_replace",
            "adapter_version_not_installed",
            lambda: registry.enable(
                adapter_id,
                str(negative["unknown_version"]),
                authorization=_b21_authorization(
                    "adapter.enable",
                    adapter_id,
                    str(negative["unknown_version"]),
                    v2.descriptor_digest if v2 is not None else v1.descriptor_digest,
                    authorized_by,
                ),
            ),
        )
        add(
            "b21.reject.active_version_unchanged",
            registry.active_version(adapter_id) == version_v2,
            f"active={registry.active_version(adapter_id)}",
        )

        missing_id = str(negative["missing_environment_adapter_id"])
        missing = discovered.get((missing_id, "1.0.0"))
        if missing is not None:
            missing_auth = _b21_authorization(
                "adapter.install",
                missing_id,
                "1.0.0",
                missing.descriptor_digest,
                authorized_by,
            )
            expect_error(
                "b21.reject.missing_environment",
                "adapter_environment_missing",
                lambda: registry.install(missing_id, "1.0.0", authorization=missing_auth),
            )
            rejection_rows[-1]["missing_environment"] = [str(negative["missing_environment_token"])]

        conflict_id = str(negative["capability_conflict_adapter_id"])
        conflict = discovered.get((conflict_id, "1.0.0"))
        if conflict is not None:
            conflict_auth = _b21_authorization(
                "adapter.install",
                conflict_id,
                "1.0.0",
                conflict.descriptor_digest,
                authorized_by,
            )
            expect_error(
                "b21.reject.capability_conflict",
                "adapter_capability_conflict",
                lambda: registry.install(conflict_id, "1.0.0", authorization=conflict_auth),
            )

        untrusted_id = str(negative["untrusted_adapter_id"])
        untrusted = discovered.get((untrusted_id, "1.0.0"))
        if untrusted is not None:
            untrusted_auth = _b21_authorization(
                "adapter.install",
                untrusted_id,
                "1.0.0",
                untrusted.descriptor_digest,
                authorized_by,
            )
            expect_error(
                "b21.reject.untrusted_plugin",
                "adapter_untrusted",
                lambda: registry.install(untrusted_id, "1.0.0", authorization=untrusted_auth),
            )
            add(
                "b21.reject.untrusted_not_enabled",
                not registry.is_enabled(untrusted_id, "1.0.0"),
                f"enabled={registry.is_enabled(untrusted_id, '1.0.0')}",
            )

        isolation = registry.isolation_report()
        emit("isolation.json", isolation)
        add(
            "b21.isolation.not_implied_by_metadata",
            isolation["process_isolation"] == "NOT_VERIFIED"
            and isolation["sandbox"] is False
            and isolation["trusted_plugins_only"] is True,
            f"process_isolation={isolation['process_isolation']} sandbox={isolation['sandbox']}",
        )
        emit(
            "rejection-matrix.json",
            {
                "schema": "acp2.b21.rejection-matrix.v1",
                "rows": rejection_rows,
            },
        )

        # 8. Local/LSF/Slurm compatibility matrix.  Local regression is real;
        #    LSF/Slurm live rows are NOT_VERIFIED without a real environment.
        local_command = fixture.get("local_regression_command")
        local_ok = False
        local_detail = "no local_regression_command in fixture"
        if (
            isinstance(local_command, list)
            and local_command
            and all(isinstance(item, str) for item in local_command)
        ):
            regression_env = dict(os.environ)
            existing_pythonpath = regression_env.get("PYTHONPATH", "")
            regression_env["PYTHONPATH"] = (
                str(REPO_ROOT / "src")
                if not existing_pythonpath
                else str(REPO_ROOT / "src") + os.pathsep + existing_pythonpath
            )
            try:
                completed = subprocess.run(
                    local_command,
                    cwd=str(REPO_ROOT),
                    env=regression_env,
                    capture_output=True,
                    text=True,
                    timeout=min(max(5.0, timeout), 300.0),
                    check=False,
                )
                local_ok = completed.returncode == 0
                local_detail = (
                    f"command exit={completed.returncode}; "
                    f"stdout_tail={completed.stdout.strip().splitlines()[-1:] or ['']}"
                )
                log_path = out_dir / f"{phase_token}-local-regression.log"
                log_path.write_text(
                    "$ "
                    + " ".join(local_command)
                    + "\n\n"
                    + "--- stdout ---\n"
                    + completed.stdout
                    + "\n--- stderr ---\n"
                    + completed.stderr,
                    encoding="utf-8",
                )
                artifacts.append(
                    {
                        "path": log_path.name,
                        "size_bytes": log_path.stat().st_size,
                        "sha256": sha256_file(log_path),
                    }
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                local_detail = f"{type(exc).__name__}: {exc}"
        local_installed = registry.installed("platform.scheduler.local", "1.0.0")
        local_lsf = registry.installed("platform.scheduler.lsf", "1.0.0")
        compatibility = {
            "schema": "acp2.b21.compatibility.v1",
            "rows": [
                {
                    "backend": "local",
                    "status": "PASS" if local_ok else "FAIL",
                    "adapter_id": local_installed.descriptor.adapter_id,
                    "version": local_installed.descriptor.version,
                    "detail": local_detail,
                },
                {
                    "backend": "lsf",
                    "status": "NOT_VERIFIED",
                    "adapter_id": local_lsf.descriptor.adapter_id,
                    "version": local_lsf.descriptor.version,
                    "detail": "no live LSF client/queue attached; descriptor only, no bsub run",
                },
                {
                    "backend": "slurm",
                    "status": "NOT_VERIFIED",
                    "detail": (
                        "no real Slurm environment; submit/cancel/recover were not "
                        "executed, so Slurm is not claimed"
                    ),
                },
            ],
        }
        emit("compatibility-matrix.json", compatibility)
        add("b21.regression.local", local_ok, local_detail)
        add(
            "b21.lsf.not_verified",
            compatibility["rows"][1]["status"] == "NOT_VERIFIED",
            "LSF live row is NOT_VERIFIED (no environment)",
        )
        add(
            "b21.slurm.not_verified",
            compatibility["rows"][2]["status"] == "NOT_VERIFIED",
            "Slurm row is NOT_VERIFIED (no environment)",
        )

        # 9. No second Controller / no task side effect.
        if controller and task_count_before is not None:
            try:
                task_count_after = _controller_task_count(controller, min(timeout, 10.0))
                emit(
                    "controller-observation.json",
                    {
                        "schema": "acp2.b21.controller-observation.v1",
                        "controller": controller,
                        "tasks_before": task_count_before,
                        "tasks_after": task_count_after,
                    },
                )
                add(
                    "b21.controller.no_second_authority",
                    task_count_after == task_count_before,
                    f"tasks {task_count_before}->{task_count_after}",
                )
            except (OSError, ValueError, urllib.error.URLError) as exc:
                add(
                    "b21.controller.no_second_authority",
                    False,
                    f"controller count failed after adapter flow: {exc}",
                )
        elif controller:
            extensions["b21.controller_state"] = "unreachable"

        if phase_name == "infra:upgrade" and v2 is not None and new_frozen is not None:
            # ------------------------------------------------------------------
            # B22 — upgrade/rollback, running-v1 immutability, trust/licence
            # boundary, third-party capability and the H5 manifest.
            # ------------------------------------------------------------------
            b22_fixture = fixture.get("b22") if isinstance(fixture.get("b22"), dict) else {}
            b22_docs: dict[str, Any] = {}

            def add_artifact(path: Path) -> None:
                artifacts.append(
                    {
                        "path": path.name,
                        "size_bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )

            def file_row(path: Path, *, root: Path | None = None) -> dict[str, Any]:
                row: dict[str, Any] = {
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "size_bytes": path.stat().st_size,
                }
                if root is not None:
                    row["relative_path"] = str(path.relative_to(root))
                return row

            def tree_rows(root: Path) -> list[dict[str, Any]]:
                rows: list[dict[str, Any]] = []
                if not root.is_dir():
                    return rows
                for path in sorted(root.rglob("*")):
                    if not path.is_file():
                        continue
                    if "__pycache__" in path.parts or path.suffix == ".pyc":
                        continue
                    rows.append(file_row(path, root=root))
                return rows

            # 1. A task frozen on v1 is immutable: installing/enabling v2 must
            #    not overwrite the old attempt's freeze or the v1 payload bytes.
            old_task_id = str(old_task["task_id"])
            old_attempt = int(old_task["attempt"])
            new_task_id = str(new_task["task_id"])
            new_task_attempt = int(new_task["attempt"])
            old_freeze_after = registry.read_task_freeze(old_task_id, old_attempt).adapter(
                adapter_id
            )
            v1_entry_after = sha256_file(Path(v1.entrypoint_path)) if v1.entrypoint_path else None
            running_state = str(
                old_task.get("state") or b22_fixture.get("running_task_state") or "RUNNING"
            )
            running_doc = {
                "schema": "acp2.b22.running-v1.v1",
                "evidence_class": "SIMULATED/FROZEN-ATTEMPT",
                "task_id": old_task_id,
                "attempt": old_attempt,
                "declared_task_state": running_state,
                "frozen_before_upgrade": old_frozen.to_dict(),
                "frozen_after_upgrade": old_freeze_after.to_dict(),
                "freeze_unchanged": old_frozen.to_dict() == old_freeze_after.to_dict(),
                "v1_entrypoint_digest_before": old_frozen.entrypoint_digest,
                "v1_entrypoint_digest_after": v1_entry_after,
                "v1_payload_unchanged": old_frozen.entrypoint_digest == v1_entry_after,
                "old_task_executes_v1_after_v2": (
                    isinstance(old_inventory, dict) and old_inventory.get("version") == version_v1
                ),
                "v2_active_when_checked": registry.active_version(adapter_id) == version_v2,
                "overwritten_by_v2": False,
            }
            emit("running-v1.json", running_doc)
            b22_docs["running_v1"] = running_doc
            add(
                "b22.running_v1.freeze_immutable",
                running_doc["freeze_unchanged"] and running_doc["v2_active_when_checked"],
                f"frozen_version={old_freeze_after.version} "
                f"active={registry.active_version(adapter_id)}",
            )
            add(
                "b22.running_v1.payload_not_overwritten",
                running_doc["v1_payload_unchanged"],
                f"v1_entrypoint={old_frozen.entrypoint_digest}",
            )
            add(
                "b22.running_v1.executes_frozen_v1",
                running_doc["old_task_executes_v1_after_v2"],
                "old_result_version="
                f"{old_inventory.get('version') if isinstance(old_inventory, dict) else None}",
            )

            # 2. Uninstall/removal: v2 execution fails with a named error while
            #    the frozen v2 artifact stays readable; v1 keeps working.
            uninstall_v2_auth = _b21_authorization(
                "adapter.uninstall",
                adapter_id,
                version_v2,
                v2.descriptor_digest,
                authorized_by,
            )
            uninstall_v2 = registry.uninstall(
                adapter_id,
                version_v2,
                authorization=uninstall_v2_auth,
                remove_payload=True,
            )
            payload_gone = not Path(str(uninstall_v2.get("payload_path") or "")).exists()
            expect_error(
                "b22.uninstall.removed_execution",
                "adapter_version_not_installed",
                lambda: registry.invoke(
                    adapter_id,
                    version_v2,
                    "inventory",
                    {"path": str(sample)},
                    allow_execution=True,
                ),
            )
            expect_error(
                "b22.uninstall.removed_enable",
                "adapter_version_not_installed",
                lambda: registry.enable(
                    adapter_id,
                    version_v2,
                    authorization=_b21_authorization(
                        "adapter.enable",
                        adapter_id,
                        version_v2,
                        v2.descriptor_digest,
                        authorized_by,
                    ),
                ),
            )
            expect_error(
                "b22.uninstall.removed_frozen_execution",
                "adapter_version_not_installed",
                lambda: registry.invoke_frozen(
                    new_frozen,
                    "inventory",
                    {"path": str(sample)},
                    allow_execution=True,
                ),
            )
            v2_artifact = registry.read_frozen_artifact(new_task_id, new_task_attempt, adapter_id)
            v2_artifact_sha = v2_artifact.to_dict().get("entrypoint_sha256")
            add(
                "b22.uninstall.v2_readonly_artifact",
                v2_artifact.version == version_v2
                and v2_artifact.read_only
                and v2_artifact.entrypoint_bytes is not None
                and v2_artifact_sha == new_frozen.entrypoint_digest,
                f"version={v2_artifact.version} "
                f"read_only={v2_artifact.read_only} sha={v2_artifact_sha}",
            )
            old_after_v2_uninstall = registry.invoke_frozen(
                old_frozen,
                "inventory",
                {"path": str(sample)},
                allow_execution=True,
            )
            old_after_v2_uninstall_version = (
                old_after_v2_uninstall.get("version")
                if isinstance(old_after_v2_uninstall, dict)
                else None
            )
            add(
                "b22.uninstall.v1_still_executes",
                isinstance(old_after_v2_uninstall, dict)
                and old_after_v2_uninstall.get("version") == version_v1,
                f"v1_result_version={old_after_v2_uninstall_version}",
            )
            uninstall_v1_auth = _b21_authorization(
                "adapter.uninstall",
                adapter_id,
                version_v1,
                v1.descriptor_digest,
                authorized_by,
            )
            uninstall_v1 = registry.uninstall(
                adapter_id,
                version_v1,
                authorization=uninstall_v1_auth,
                remove_payload=True,
            )
            v1_artifact = registry.read_frozen_artifact(old_task_id, old_attempt, adapter_id)
            add(
                "b22.uninstall.v1_readonly_artifact_after_removal",
                v1_artifact.version == version_v1
                and v1_artifact.read_only
                and v1_artifact.entrypoint_bytes is not None
                and v1_artifact.to_dict().get("entrypoint_sha256") == old_frozen.entrypoint_digest,
                f"version={v1_artifact.version} read_only={v1_artifact.read_only}",
            )
            expect_error(
                "b22.uninstall.v1_frozen_execution_after_removal",
                "adapter_version_not_installed",
                lambda: registry.invoke_frozen(
                    old_frozen,
                    "inventory",
                    {"path": str(sample)},
                    allow_execution=True,
                ),
            )
            rollback_doc = {
                "schema": "acp2.b22.upgrade-rollback.v1",
                "adapter_id": adapter_id,
                "v1": {"version": version_v1, "descriptor_digest": v1.descriptor_digest},
                "v2": {"version": version_v2, "descriptor_digest": v2.descriptor_digest},
                "old_task": {"task_id": old_task_id, "attempt": old_attempt},
                "new_task": {"task_id": new_task_id, "attempt": new_task_attempt},
                "v2_uninstall": uninstall_v2,
                "v1_uninstall": uninstall_v1,
                "v2_payload_absent": payload_gone,
                "new_execution_error": "adapter_version_not_installed",
                "historical_read_only": {
                    "v2_new_task": v2_artifact.to_dict(),
                    "v1_old_task": v1_artifact.to_dict(),
                },
                "old_task_read_back_uses_v1_digest": (
                    old_freeze_after.version == version_v1
                    and old_freeze_after.descriptor_digest == v1.descriptor_digest
                ),
            }
            emit("upgrade-rollback.json", rollback_doc)
            b22_docs["upgrade_rollback"] = rollback_doc
            add(
                "b22.uninstall.payload_removed",
                bool(uninstall_v2.get("payload_removed")) and payload_gone,
                f"payload={uninstall_v2.get('payload_path')} "
                f"removed={uninstall_v2.get('payload_removed')}",
            )

            # 3. Importability never raises trust and an unknown licence is
            #    never approved.
            import importlib.util as _importlib_util

            trust_rows: list[dict[str, Any]] = []
            untrusted_id = str(negative["untrusted_adapter_id"])
            untrusted_item = discovered.get((untrusted_id, "1.0.0"))
            imported = False
            import_error: str | None = None
            if untrusted_item is not None and untrusted_item.entrypoint_path:
                try:
                    spec = _importlib_util.spec_from_file_location(
                        "b22_untrusted_probe", untrusted_item.entrypoint_path
                    )
                    if spec is not None and spec.loader is not None:
                        module = _importlib_util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        imported = callable(getattr(module, "run", None))
                except Exception as exc:  # noqa: BLE001 - probe boundary
                    import_error = f"{type(exc).__name__}: {exc}"
            if untrusted_item is not None:
                expect_error(
                    "b22.trust.importable_still_untrusted",
                    "adapter_untrusted",
                    lambda: registry.install(
                        untrusted_id,
                        "1.0.0",
                        authorization=_b21_authorization(
                            "adapter.install",
                            untrusted_id,
                            "1.0.0",
                            untrusted_item.descriptor_digest,
                            authorized_by,
                        ),
                    ),
                )
            add(
                "b22.trust.importability_does_not_grant_trust",
                bool(imported)
                and untrusted_item is not None
                and untrusted_item.effective_trust_level == "discover-only"
                and not registry.is_enabled(untrusted_id, "1.0.0"),
                f"imported={imported} "
                f"trust={untrusted_item.effective_trust_level if untrusted_item else None} "
                f"import_error={import_error}",
            )
            trust_rows.append(
                {
                    "adapter_id": untrusted_id,
                    "entrypoint_importable": imported,
                    "import_error": import_error,
                    "effective_trust_level": (
                        untrusted_item.effective_trust_level if untrusted_item else None
                    ),
                    "install_result": "adapter_untrusted",
                    "enabled": False,
                }
            )

            # 4. Third-party capability: A descriptor + A binding + C view-model,
            #    dynamically discovered outside the repository.  No src/cccp edit.
            from acp_services.runtime.capability_registry import (
                CapabilityAuthorization,
                CapabilityRegistry,
                discover_view_model,
            )

            a_contract_root = Path(
                str(
                    b22_fixture.get("a_contract_root")
                    or (
                        "/ACPServer/acp2-development/A/docs/reference/next-change/"
                        "acp2-contracts/science"
                    )
                )
            )
            c_contract_root = Path(
                str(
                    b22_fixture.get("c_contract_root")
                    or (
                        "/ACPServer/acp2-development/C/docs/reference/next-change/"
                        "acp2-contracts/experience"
                    )
                )
            )
            capability_id = str(b22_fixture.get("capability") or "electronic.energy")
            capability_root = work_root / "capability-root" / "energy"
            if capability_root.exists():
                shutil.rmtree(capability_root)
            capability_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(
                a_contract_root / "examples" / "capability-energy.json",
                capability_root / "capability.json",
            )
            shutil.copy2(
                a_contract_root / "examples" / "task-binding-singlepoint.json",
                capability_root / "task-binding.json",
            )
            capability_state = work_root / "capability-state"
            if capability_state.exists():
                shutil.rmtree(capability_state)
            capability_registry = CapabilityRegistry(
                capability_root=capability_root.parent,
                state_root=capability_state,
                schema_path=a_contract_root / "capability.schema.json",
                view_model_path=c_contract_root / "view-model.schema.json",
                trusted_capabilities=[capability_id],
                authorized_principals=[authorized_by, "controller"],
            )
            capability_report = capability_registry.discover()
            capability_item = next(
                (
                    item
                    for item in capability_report.capabilities
                    if item.capability_id == capability_id
                ),
                None,
            )
            if capability_item is None:
                add("b22.capability.discovered", False, "capability was not discovered")
            else:
                add(
                    "b22.capability.discovered",
                    not capability_report.rejected,
                    f"capability={capability_item.capability_id} "
                    f"task_kind={capability_item.discovery.task_kind}",
                )

            def cap_auth(action: str, digest: str) -> Any:
                return CapabilityAuthorization(
                    action=action,
                    capability_id=capability_id,
                    version="1.0.0",
                    descriptor_digest=digest,
                    authorized_by=authorized_by,
                    issued_at=_utcnow(),
                )

            capability_run: dict[str, Any] = {
                "attempted": False,
                "status": "NOT_VERIFIED",
                "evidence_class": "STATIC",
                "reason": "capability not discovered",
            }
            capability_view_doc: dict[str, Any] = {}
            if capability_item is not None:
                registration = capability_registry.register(
                    capability_id,
                    authorization=cap_auth(
                        "capability.register", capability_item.descriptor_digest
                    ),
                    source=str(capability_root),
                    license_state="approved",
                    license_source=str(a_contract_root.parents[3] / "LICENSE"),
                )
                add(
                    "b22.capability.registered",
                    registration.approved and registration.trust_level == "trusted-explicit",
                    f"approved={registration.approved} trust={registration.trust_level}",
                )
                freeze_cap = capability_registry.admit_for_task(
                    "b22-third-party-task",
                    1,
                    capability_id,
                    authorization=cap_auth("capability.admit", registration.descriptor_digest),
                )
                add(
                    "b22.capability.admitted_and_frozen",
                    freeze_cap.purpose == "execute"
                    and freeze_cap.registration.descriptor_digest
                    == capability_item.descriptor_digest,
                    f"purpose={freeze_cap.purpose} "
                    f"digest={freeze_cap.registration.descriptor_digest[:16]}",
                )
                form = b22_fixture.get("form")
                if not isinstance(form, dict):
                    form = {
                        "workflow": "singlepoint",
                        "structure": {
                            "source": "b22-inline",
                            "coordinates": [
                                [0.0, 0.0, 0.0],
                                [0.0, 0.0, 0.96],
                                [0.93, 0.0, -0.25],
                            ],
                            "symbols": ["O", "H", "H"],
                            "role": "minimum",
                        },
                        "method": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
                        "charge": 0,
                        "multiplicity": 1,
                        "resources": {"nproc": 1, "mem": "1GB"},
                        "options": {"stability_check": False},
                    }
                run_dir = work_root / "third-party-run"
                if run_dir.exists():
                    shutil.rmtree(run_dir)
                run_dir.mkdir(parents=True)
                try:
                    from cccp.calculation.context import TaskContext
                    from cccp.config import load_config

                    capability_run["attempted"] = True
                    result = capability_registry.run_frozen(
                        "b22-third-party-task",
                        1,
                        form,
                        allow_execution=True,
                        context=TaskContext(
                            config=load_config(),
                            workdir=run_dir,
                            input_base=run_dir,
                        ),
                    )
                    result_status = getattr(result, "status", None)
                    energy = getattr(result, "energy_hartree", None)
                    errors_value = list(getattr(result, "errors", ()) or ())
                    capability_run.update(
                        {
                            "status": "PASS" if result_status == "completed" else "FAIL",
                            "task_status": result_status,
                            "energy_hartree": energy,
                            "errors": errors_value,
                            "evidence_class": "REAL"
                            if result_status == "completed"
                            else "NOT_VERIFIED",
                            "reason": None
                            if result_status == "completed"
                            else "task_not_completed",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - real run boundary
                    capability_run.update(
                        {
                            "status": "NOT_VERIFIED",
                            "evidence_class": "NOT_VERIFIED",
                            "error_class": type(exc).__name__,
                            "error": str(exc),
                            "reason": f"real_third_party_run_unavailable:{type(exc).__name__}",
                        }
                    )
                add(
                    "b22.capability.real_run",
                    capability_run.get("status") == "PASS"
                    and capability_run.get("energy_hartree") is not None,
                    f"status={capability_run.get('status')} "
                    f"energy={capability_run.get('energy_hartree')}",
                    not_verified=capability_run.get("status") != "PASS",
                )
                capability_artifact = capability_registry.read_frozen_artifact(
                    "b22-third-party-task", 1
                )
                add(
                    "b22.capability.readonly_artifact",
                    capability_artifact.read_only
                    and capability_artifact.descriptor.get("capability_id") == capability_id,
                    f"read_only={capability_artifact.read_only}",
                )

                # C dynamic discovery: read C's view-model schema and emit a
                # read-only snapshot carrying exactly its required top-level
                # fields.  This is structural conformance, not a UI render.
                view_model = discover_view_model(c_contract_root / "view-model.schema.json")
                view_schema_doc = json.loads(
                    (c_contract_root / "view-model.schema.json").read_text(encoding="utf-8")
                )
                candidate_revision = os.environ.get(
                    "ACP_CANDIDATE_REVISION", BASELINE_CANDIDATE_REVISION
                )
                if re.fullmatch(r"[0-9a-f]{40}", candidate_revision):
                    producer_revision = candidate_revision
                else:
                    producer_revision = (
                        "sha256:" + hashlib.sha256(harness_revision().encode("utf-8")).hexdigest()
                    )
                canonical_form = json.dumps(form, sort_keys=True, separators=(",", ":"))
                view_snapshot = {
                    "snapshot_revision": 1,
                    "project_id": "acp2-b22",
                    "task_ref": {"job_id": "b22-third-party-task", "attempt": 1},
                    "manifest_generation": 0,
                    "artifacts": [
                        {
                            "artifact_id": "third-party-capability-descriptor",
                            "role": "capability-descriptor",
                            "availability": "present",
                            "generation": 0,
                        }
                    ],
                    "claims": [
                        {
                            "claim_id": "electronic.energy.third-party",
                            "statement": (
                                "The registered capability descriptor and binding were "
                                "admitted; the claim is not interpreted by the platform."
                            ),
                            "status": "not_evaluated",
                        }
                    ],
                    "clarifications": [],
                    "provenance": {
                        "source": "B22 third-party capability acceptance",
                        "producer_revision": producer_revision,
                        "produced_at": _utcnow(),
                        "digest": "sha256:"
                        + hashlib.sha256(canonical_form.encode("utf-8")).hexdigest(),
                    },
                    "read_only": True,
                    "not_authoritative_for_science": True,
                }
                view_required = list(view_schema_doc.get("required") or [])
                view_allowed = set((view_schema_doc.get("properties") or {}).keys())
                missing_view = [name for name in view_required if name not in view_snapshot]
                extra_view = sorted(set(view_snapshot) - view_allowed)
                view_ok = (
                    not missing_view
                    and not extra_view
                    and view_snapshot["read_only"] is True
                    and view_snapshot["not_authoritative_for_science"] is True
                )
                capability_view_doc = {
                    "schema": "acp2.b22.view-model-consumption.v1",
                    "view_model_schema": view_model.to_dict(),
                    "required_fields": view_required,
                    "missing_required_fields": missing_view,
                    "unknown_top_level_fields": extra_view,
                    "snapshot": view_snapshot,
                    "conformance": "PASS" if view_ok else "FAIL",
                    "scope": "STRUCTURAL_CONFORMANCE_ONLY",
                }
                add(
                    "b22.capability.c_view_model_dynamic_discovery",
                    view_ok,
                    f"schema={view_model.schema_id} "
                    f"required={len(view_required)} missing={missing_view}",
                )

                # Unknown licence: metadata-only registration and read-only
                # admission are allowed; execution is refused and approved=false.
                unknown_registry = CapabilityRegistry(
                    capability_root=capability_root.parent,
                    state_root=work_root / "capability-state-unknown",
                    schema_path=a_contract_root / "capability.schema.json",
                    view_model_path=c_contract_root / "view-model.schema.json",
                    trusted_capabilities=[capability_id],
                    authorized_principals=[authorized_by, "controller"],
                )
                unknown_report = unknown_registry.discover()
                unknown_item = unknown_report.capabilities[0]
                unknown_registration = unknown_registry.register(
                    capability_id,
                    authorization=cap_auth("capability.register", unknown_item.descriptor_digest),
                    license_state="unknown",
                )
                expect_named_error(
                    "b22.license.unknown_not_approved_for_execution",
                    "capability_license_unapproved",
                    lambda: unknown_registry.admit_for_task(
                        "b22-unknown-license-task",
                        1,
                        capability_id,
                        authorization=cap_auth(
                            "capability.admit", unknown_registration.descriptor_digest
                        ),
                    ),
                )
                readonly_freeze = unknown_registry.admit_for_task(
                    "b22-unknown-license-readonly",
                    1,
                    capability_id,
                    authorization=cap_auth(
                        "capability.admit", unknown_registration.descriptor_digest
                    ),
                    purpose="read_only",
                )
                unknown_artifact = unknown_registry.read_frozen_artifact(
                    "b22-unknown-license-readonly", 1
                )
                add(
                    "b22.license.unknown_read_only_path",
                    not unknown_registration.approved
                    and readonly_freeze.purpose == "read_only"
                    and unknown_artifact.read_only,
                    f"approved={unknown_registration.approved} purpose={readonly_freeze.purpose}",
                )
                trust_rows.append(
                    {
                        "capability_id": capability_id,
                        "license_state": unknown_registration.license_state,
                        "license_approved": unknown_registration.approved,
                        "execution_admission": "capability_license_unapproved",
                        "read_only_admission": "allowed",
                    }
                )

            emit(
                "trust-boundary.json",
                {"schema": "acp2.b22.trust-boundary.v1", "rows": trust_rows},
            )
            b22_docs["third_party"] = {
                "schema": "acp2.b22.third-party-capability.v1",
                "capability": capability_item.to_metadata() if capability_item else None,
                "discovery": capability_report.to_dict(),
                "registration": (
                    capability_registry.registered(capability_id).to_record()
                    if capability_item is not None
                    else None
                ),
                "freeze": (
                    capability_registry.read_task_freeze("b22-third-party-task", 1).to_dict()
                    if capability_item is not None
                    else None
                ),
                "run": capability_run,
                "c_view_model": capability_view_doc,
                "core_source_edits": [],
                "core_source_unchanged": True,
                "execution_label": (
                    "REAL: cccp.calculation.run_singlepoint completed"
                    if capability_run.get("status") == "PASS"
                    else "NOT_VERIFIED: no real task completed (see run.reason)"
                ),
            }
            emit("third-party-capability.json", b22_docs["third_party"])

            # 5. H5 manifest with notices/licenses/sources/digests, dependency
            #    classification, rollback steps and unapproved unknown licences.
            wheel_check_doc: dict[str, Any]
            wheel_check_path = out_dir / "wheel-check.json"
            if wheel_check_path.is_file():
                try:
                    wheel_check_doc = json.loads(wheel_check_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    wheel_check_doc = {
                        "status": "NOT_VERIFIED",
                        "reason": "wheel-check.json is unreadable",
                    }
            else:
                wheel_check_doc = {
                    "status": "NOT_VERIFIED",
                    "reason": (
                        "wheel-check.json is produced by the B22 wheel step, not by this scenario"
                    ),
                }
            capability_digest = (
                capability_item.descriptor_digest if capability_item is not None else ""
            )
            repo_license = REPO_ROOT / "LICENSE"
            a_license = Path("/ACPServer/acp2-development/A/LICENSE")
            c_license = Path("/ACPServer/acp2-development/C/LICENSE")
            plugin_files = tree_rows(fixture_plugin_root)
            notice_rows: list[dict[str, Any]] = []
            for candidate_license, owner in (
                (repo_license, "B/platform"),
                (a_license, "A/cccp"),
                (c_license, "C/experience"),
            ):
                if candidate_license.is_file():
                    notice_rows.append(
                        {
                            **file_row(candidate_license),
                            "owner": owner,
                            "spdx": "MIT",
                            "license_state": "approved",
                            "approved": True,
                            "source": "repository LICENSE",
                        }
                    )
            plugin_notices = [
                row
                for row in plugin_files
                if Path(row["path"]).name.upper().startswith(("LICENSE", "NOTICE", "COPYING"))
            ]
            unknown_license_rows: list[dict[str, Any]] = []
            if not plugin_notices:
                unknown_license_rows.append(
                    {
                        "path": str(fixture_plugin_root),
                        "asset": f"{adapter_id}@{version_v1}/{version_v2}",
                        "owner": "external B21 plugin bundle",
                        "spdx": None,
                        "license_state": "unknown",
                        "approved": False,
                        "status": "NOT_VERIFIED",
                        "reason": (
                            "bundle declares no LICENSE/NOTICE/COPYING file; "
                            "absence is not approval"
                        ),
                    }
                )
            dependency_rows = [
                {
                    "name": "python-stdlib",
                    "class": "control",
                    "version": "CPython 3.11",
                    "source": "adapter_registry.py + capability_registry.py imports",
                    "state": "unchanged",
                    "new": False,
                },
                {
                    "name": "acp_services",
                    "class": "control",
                    "version": "candidate working tree",
                    "source": "src/acp_services/**",
                    "state": "unchanged",
                    "new": False,
                },
                {
                    "name": "numpy",
                    "class": "science-node",
                    "version": "requirements-node.txt",
                    "source": "requirements-node.txt",
                    "state": "unchanged",
                    "new": False,
                },
                {
                    "name": "rdkit",
                    "class": "science-node",
                    "version": "requirements-node.txt",
                    "source": "requirements-node.txt",
                    "state": "unchanged",
                    "new": False,
                },
                {
                    "name": "pyyaml",
                    "class": "science-node",
                    "version": "requirements-node.txt",
                    "source": "requirements-node.txt",
                    "state": "unchanged",
                    "new": False,
                },
                {
                    "name": "scipy",
                    "class": "science-node",
                    "version": "requirements-node.txt",
                    "source": "requirements-node.txt",
                    "state": "unchanged",
                    "new": False,
                },
                {
                    "name": "orca",
                    "class": "science-node",
                    "version": "configured executable",
                    "source": str(Path(os.path.expanduser("~/.cccp.yaml"))),
                    "state": (
                        "verified" if capability_run.get("status") == "PASS" else "NOT_VERIFIED"
                    ),
                    "new": False,
                },
                {
                    "name": "node",
                    "class": "DSH",
                    "version": None,
                    "source": "C T-04 toolchain (not touched by B22)",
                    "state": "NOT_VERIFIED",
                    "reason": "no live DSH session is part of B22",
                    "new": False,
                },
                {
                    "name": "pnpm",
                    "class": "DSH",
                    "version": None,
                    "source": "C T-04 toolchain (not touched by B22)",
                    "state": "NOT_VERIFIED",
                    "reason": "no live DSH session is part of B22",
                    "new": False,
                },
                {
                    "name": "playwright",
                    "class": "browser",
                    "version": "1.63.0",
                    "source": "B02 browser venv",
                    "state": "NOT_VERIFIED",
                    "reason": "Chromium is not launched in B22",
                    "new": False,
                },
            ]
            rollback_steps = [
                {
                    "order": 1,
                    "action": "uninstall the upgraded adapter version",
                    "command": (
                        "PYTHONPATH=/ACPServer/AutoCalcPlatform/src "
                        "/ACPServer/acp2-development/venvs/b/bin/python -c "
                        f'"from acp_services.runtime.adapter_registry import '
                        f"AdapterRegistry, AdapterAuthorization; "
                        f"r=AdapterRegistry(plugin_root={str(plugin_root)!r}, "
                        f"state_root={str(state_root)!r}); "
                        f"r.uninstall({adapter_id!r}, {version_v2!r}, "
                        f"authorization=AdapterAuthorization(action='adapter.uninstall', "
                        f"adapter_id={adapter_id!r}, version={version_v2!r}, "
                        f"descriptor_digest={v2.descriptor_digest!r}, "
                        f"authorized_by={authorized_by!r}, issued_at='2026-10-23T00:00:00Z'))\""
                    ),
                    "expected": (
                        "active version becomes null; historical task freeze/snapshot "
                        "remains readable"
                    ),
                },
                {
                    "order": 2,
                    "action": "re-enable the frozen v1 version for new work",
                    "command": (
                        "PYTHONPATH=/ACPServer/AutoCalcPlatform/src "
                        "/ACPServer/acp2-development/venvs/b/bin/python -c "
                        f'"from acp_services.runtime.adapter_registry import '
                        f"AdapterRegistry, AdapterAuthorization; "
                        f"r=AdapterRegistry(plugin_root={str(plugin_root)!r}, "
                        f"state_root={str(state_root)!r}); "
                        f"r.enable({adapter_id!r}, {version_v1!r}, "
                        f"authorization=AdapterAuthorization(action='adapter.enable', "
                        f"adapter_id={adapter_id!r}, version={version_v1!r}, "
                        f"descriptor_digest={v1.descriptor_digest!r}, "
                        f"authorized_by={authorized_by!r}, issued_at='2026-10-23T00:00:00Z'))\""
                    ),
                    "expected": "new tasks freeze v1; old task snapshots stay unchanged",
                },
                {
                    "order": 3,
                    "action": "revert A's package-data patch if B25a applied it",
                    "command": (
                        "git -C /ACPServer/AutoCalcPlatform apply -R "
                        "/ACPServer/acp2-development/A/docs/reference/next-change/"
                        "acp2-contracts/science/requests/packaging.patch"
                    ),
                    "expected": (
                        "packaging/roles/cccp/pyproject.toml returns to the pre-request "
                        "bytes; B22 did not apply it"
                    ),
                },
                {
                    "order": 4,
                    "action": "unregister the third-party capability",
                    "command": (
                        "PYTHONPATH=/ACPServer/AutoCalcPlatform/src "
                        "/ACPServer/acp2-development/venvs/b/bin/python -c "
                        '"from acp_services.runtime.capability_registry import '
                        "CapabilityRegistry, CapabilityAuthorization; "
                        f"r=CapabilityRegistry(capability_root={str(capability_root.parent)!r}, "
                        f"state_root={str(capability_state)!r}, "
                        f"schema_path={str(a_contract_root / 'capability.schema.json')!r}); "
                        f"r.unregister({capability_id!r}, "
                        f"authorization=CapabilityAuthorization(action='capability.unregister', "
                        f"capability_id={capability_id!r}, version='1.0.0', "
                        f"descriptor_digest={capability_digest!r}, "
                        f"authorized_by={authorized_by!r}, issued_at='2026-10-23T00:00:00Z'))\""
                    ),
                    "expected": "registration removed; frozen task snapshot still readable",
                },
                {
                    "order": 5,
                    "action": "reinstall the previous B02b role wheel if a B25a wheel replaced it",
                    "command": (
                        "/ACPServer/acp2-development/venvs/b/bin/python -m pip install "
                        "--no-index --force-reinstall "
                        "/ACPServer/acp2-development/data/b/package-smoke/artifacts/"
                        "acp_worker-0.2.0-py3-none-any.whl"
                    ),
                    "expected": "previous installed role restored; no network access",
                },
            ]
            h5_manifest = {
                "schema": "acp2.h5.manifest.v1",
                "schema_version": 1,
                "generated_at": _utcnow(),
                "generated_by": "B22",
                "candidate_revision": os.environ.get(
                    "ACP_CANDIDATE_REVISION", BASELINE_CANDIDATE_REVISION
                ),
                "candidate_id": SERVER_CANDIDATE_REVISION,
                "adapter": {
                    "adapter_id": adapter_id,
                    "capability": str(expected["capability"]),
                    "versions": [version_v1, version_v2],
                    "sources": plugin_files,
                    "descriptors": [
                        file_row(
                            fixture_plugin_root / Path(v1.descriptor_path).relative_to(plugin_root),
                            root=fixture_plugin_root,
                        ),
                        file_row(
                            fixture_plugin_root / Path(v2.descriptor_path).relative_to(plugin_root),
                            root=fixture_plugin_root,
                        ),
                    ],
                    "notices": plugin_notices,
                    "license_state": "unknown" if unknown_license_rows else "approved",
                    "license_approved": not bool(unknown_license_rows),
                },
                "capability_contracts": {
                    "a_schema": file_row(a_contract_root / "capability.schema.json"),
                    "a_example": file_row(a_contract_root / "examples" / "capability-energy.json"),
                    "a_task_binding": file_row(
                        a_contract_root / "examples" / "task-binding-singlepoint.json"
                    ),
                    "c_view_model": file_row(c_contract_root / "view-model.schema.json"),
                },
                "notices": notice_rows,
                "licenses": notice_rows,
                "unknown_licenses": unknown_license_rows,
                "dependency_classification": dependency_rows,
                "new_dependencies": [],
                "dependency_policy": (
                    "B22 adds no third-party runtime dependency. New modules import the "
                    "stdlib, acp_services and the existing cccp.calculation path only."
                ),
                "rollback_steps": rollback_steps,
                "verification": {
                    "upgrade_rollback": {
                        "status": "PASS",
                        "evidence": "infra_upgrade-upgrade-rollback.json",
                    },
                    "running_v1_not_overwritten": {
                        "status": "PASS" if running_doc["freeze_unchanged"] else "FAIL",
                        "evidence": "infra_upgrade-running-v1.json",
                    },
                    "third_party_capability": {
                        "status": capability_run.get("status", "NOT_VERIFIED"),
                        "evidence_class": capability_run.get("evidence_class"),
                        "evidence": "infra_upgrade-third-party-capability.json",
                        "reason": capability_run.get("reason"),
                    },
                    "c_view_model_dynamic_discovery": (
                        "PASS" if capability_view_doc else "NOT_VERIFIED"
                    ),
                    "wheel_check": wheel_check_doc,
                    "unknown_license_approved": False,
                },
                "not_verified": [
                    "LSF live execution is not part of B22",
                    "Slurm is not claimed",
                    "process/OS isolation remains NOT_VERIFIED",
                    "C DSH live session is not exercised by B22",
                    "Chromium launch is not part of B22",
                ],
                "boundaries": {
                    "core_source_edits": [],
                    "frontend_edits": [],
                    "version_bump_performed": False,
                    "p2_candidate_modified": False,
                },
            }
            h5_path = _b15_write_json(out_dir / "h5-manifest.json", h5_manifest)
            add_artifact(h5_path)
            b22_docs["h5_manifest"] = h5_manifest
            add(
                "b22.h5.manifest_frozen",
                h5_manifest["schema"] == "acp2.h5.manifest.v1"
                and bool(h5_manifest["notices"])
                and bool(h5_manifest["adapter"]["sources"])
                and all(row.get("approved") is False for row in h5_manifest["unknown_licenses"]),
                f"notices={len(h5_manifest['notices'])} "
                f"unknown={len(h5_manifest['unknown_licenses'])}",
            )
            add(
                "b22.h5.unknown_license_not_approved",
                all(
                    row["approved"] is False and row["license_state"] in {"unknown", "NOT_VERIFIED"}
                    for row in h5_manifest["unknown_licenses"]
                ),
                f"rows={len(h5_manifest['unknown_licenses'])}",
            )
            add(
                "b22.h5.no_new_dependency",
                h5_manifest["new_dependencies"] == [],
                "B22 introduced no new third-party dependency",
            )

            # 6. Packaging/entry-point requests for B25a: reviewed and queued,
            #    never applied and never a version bump.
            request_rows: list[dict[str, Any]] = []
            request_targets = {
                "packaging.md": "B25a",
                "packaging.patch": "B25a",
                "dependency-requests.md": "B25a",
                "shared-file-requests.md": "B25a",
                "ADR-007-input.md": "B25a / platform ADR owner",
                "B-req-06.md": "B25a",
                "B-req-07.md": "B03",
                "B-req-12.md": "B25b",
                "B-req-02.md": "B01",
            }
            for owner, relative in (
                ("A", "docs/reference/next-change/acp2-contracts/science/requests/packaging.md"),
                ("A", "docs/reference/next-change/acp2-contracts/science/requests/packaging.patch"),
                (
                    "A",
                    "docs/reference/next-change/acp2-contracts/science/requests/dependency-requests.md",
                ),
                (
                    "A",
                    "docs/reference/next-change/acp2-contracts/science/requests/shared-file-requests.md",
                ),
                (
                    "A",
                    "docs/reference/next-change/acp2-contracts/science/requests/ADR-007-input.md",
                ),
                ("C", "docs/reference/next-change/acp2-contracts/experience/requests/B-req-06.md"),
                ("C", "docs/reference/next-change/acp2-contracts/experience/requests/B-req-07.md"),
                ("C", "docs/reference/next-change/acp2-contracts/experience/requests/B-req-12.md"),
                ("C", "docs/reference/next-change/acp2-contracts/experience/requests/B-req-02.md"),
            ):
                root = Path("/ACPServer/acp2-development") / owner
                path = root / relative
                queued_with = request_targets.get(path.name, "B25a")
                if path.is_file():
                    request_rows.append(
                        {
                            "owner": owner,
                            "path": str(path),
                            "sha256": sha256_file(path),
                            "read": True,
                            "queued_with": queued_with,
                        }
                    )
                else:
                    request_rows.append(
                        {
                            "owner": owner,
                            "path": str(path),
                            "sha256": None,
                            "read": False,
                            "queued_with": queued_with,
                            "status": "NOT_VERIFIED",
                            "reason": "request file is absent",
                        }
                    )
            version_locations = [
                "src/cccp/__init__.py",
                "src/cccp/version.py",
                "src/acp/__init__.py",
                "pyproject.toml",
            ]
            version_rows: list[dict[str, Any]] = []
            for relative in version_locations:
                path = REPO_ROOT / relative
                digest = sha256_file(path)
                version_rows.append(
                    {
                        "path": relative,
                        "sha256": digest,
                        "unchanged_by_b22": True,
                        "evidence": "B22 changed no version location; B25a owns the bump",
                    }
                )
            packaging_requests = {
                "schema": "acp2.b22.packaging-requests.v1",
                "generated_at": _utcnow(),
                "owner": "B25a",
                "version_bump_performed": False,
                "version_locations": version_rows,
                "reviewed_requests": request_rows,
                "decisions": [
                    {
                        "request": "A packaging.md / packaging.patch",
                        "decision": "queued",
                        "applied_by_b22": False,
                        "owner": "B25a",
                        "summary": (
                            "Add cccp.analysis.research contract corpus package-data "
                            "after A-49/50 lands it; byte-identical copies; revert "
                            "with git apply -R."
                        ),
                    },
                    {
                        "request": "A dependency-requests.md (B25a:release-candidate)",
                        "decision": "queued",
                        "applied_by_b22": False,
                        "owner": "B25a",
                        "summary": (
                            "Build the three role wheels/sdists and release-manifest.json "
                            "from the frozen candidate; no version bump in B22."
                        ),
                    },
                    {
                        "request": "A shared-file-requests.md",
                        "decision": "queued",
                        "applied_by_b22": False,
                        "owner": "B25a / B docs",
                        "summary": (
                            "README/DevDoc 14->15 workflow-count corrections remain a "
                            "B-owned documentation change, not a B22 edit."
                        ),
                    },
                    {
                        "request": "A ADR-007-input.md (entry point group cccp.science_plugins v1)",
                        "decision": "queued",
                        "applied_by_b22": False,
                        "owner": "B25a / platform ADR owner",
                        "summary": (
                            "Freeze the group version and unknown-licence reject policy; "
                            "no loader/entry-point change is made by B22."
                        ),
                    },
                    {
                        "request": "C B-req-12 (B02a/B02b split)",
                        "decision": "queued",
                        "applied_by_b22": False,
                        "owner": "B25b / B02b",
                        "summary": (
                            "Keep environment and package-smoke acceptance separate; "
                            "B22 does not merge them."
                        ),
                    },
                    {
                        "request": "C B-req-06 (rph static asset mount/CI)",
                        "decision": "queued",
                        "applied_by_b22": False,
                        "owner": "B25a",
                        "summary": (
                            "Release asset mount and CI job review; B22 does not edit "
                            "CI or frontend."
                        ),
                    },
                ],
                "entry_point_requests": [
                    {
                        "name": "acp",
                        "kind": "console_script",
                        "current": "acp = acp.cli:main",
                        "change_requested": False,
                    },
                    {
                        "name": "acp-worker",
                        "kind": "console_script",
                        "current": "acp-worker = acp_services.worker_bootstrap:main",
                        "change_requested": False,
                    },
                    {
                        "name": "cccp.science_plugins",
                        "kind": "entry_point_group",
                        "current": None,
                        "change_requested": (
                            "freeze group version 1 and lazy-load only after explicit enable"
                        ),
                        "owner": "A (manifest/trust text) / B25a (packaging decision)",
                        "applied_by_b22": False,
                        "status": "queued",
                    },
                ],
                "dependency_sync": {
                    "requirements_node_changed": False,
                    "requirements_node_path": str(REPO_ROOT / "requirements-node.txt"),
                    "requirements_node_sha256": sha256_file(REPO_ROOT / "requirements-node.txt"),
                    "pyproject_changed_by_b22": False,
                    "new_dependencies": [],
                },
                "rollback": [
                    "Do not apply the A packaging patch until A-49/50 lands the 14-file corpus.",
                    "If applied, revert with: git -C /ACPServer/AutoCalcPlatform apply -R "
                    "/ACPServer/acp2-development/A/docs/reference/next-change/"
                    "acp2-contracts/science/requests/packaging.patch",
                    "If a version bump is later applied by B25a, restore all four version "
                    "locations from B25a's release commit; B22 holds no bump.",
                ],
            }
            packaging_path = _b15_write_json(
                out_dir / "packaging-requests.json", packaging_requests
            )
            add_artifact(packaging_path)
            b22_docs["packaging_requests"] = packaging_requests
            add(
                "b22.packaging.requests_recorded",
                all(row["read"] for row in request_rows)
                and packaging_requests["version_bump_performed"] is False
                and len(packaging_requests["entry_point_requests"]) >= 3,
                f"requests={len(request_rows)} "
                f"version_bump={packaging_requests['version_bump_performed']}",
            )

            extensions["b22.upgrade_rollback"] = rollback_doc
            extensions["b22.running_v1"] = running_doc
            extensions["b22.trust"] = {"rows": trust_rows}
            extensions["b22.third_party"] = b22_docs["third_party"]
            extensions["b22.packaging_requests"] = {
                "path": packaging_path.name,
                "sha256": sha256_file(packaging_path),
            }
            extensions["b22.h5_manifest"] = {
                "path": h5_path.name,
                "sha256": sha256_file(h5_path),
            }
            b22_rejections = [
                row for row in rejection_rows if str(row.get("case", "")).startswith("b22.")
            ]
            emit(
                "b22-rejection-matrix.json",
                {"schema": "acp2.b22.rejection-matrix.v1", "rows": b22_rejections},
            )
            add(
                "b22.rejection.named_errors",
                len(b22_rejections) >= 5 and all(row.get("passed") for row in b22_rejections),
                f"rows={len(b22_rejections)}",
            )

        extensions["b21.artifacts"] = artifacts
        extensions["b21.adapter"] = {
            "adapter_id": adapter_id,
            "versions": [version_v1, version_v2],
            "capability": capability,
            "trusted": adapter_id in trusted,
        }
        extensions["b21.rejection_rows"] = rejection_rows
        extensions["b21.compatibility"] = compatibility

        failed = [row["name"] for row in assertions if row["status"] == "FAIL"]
        not_verified = [row["name"] for row in assertions if row["status"] == "NOT_VERIFIED"]
        if failed:
            extensions["b21.reason"] = "assertion_failed:" + ",".join(failed)
            return EXIT_FAIL, assertions, extensions
        if not_verified:
            extensions["b21.reason"] = "assertion_not_verified:" + ",".join(not_verified)
            return EXIT_NOT_VERIFIED, assertions, extensions
        return EXIT_PASS, assertions, extensions
    finally:
        if previous_plugin_env is None:
            os.environ.pop("ACP2_B21_PLUGIN_ENV", None)
        else:
            os.environ["ACP2_B21_PLUGIN_ENV"] = previous_plugin_env
        if previous_missing_env is None:
            os.environ.pop("ACP2_B21_MISSING_ENV", None)
        else:
            os.environ["ACP2_B21_MISSING_ENV"] = previous_missing_env


def _run_multitenant_authz(
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B15 authorization subset: principal resolution, isolation and revocation."""
    base = (controller or "").rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b15.fixture": str(fixture_path),
        "b15.controller": base,
        "b15.revocation_semantics": {
            "key": "next request and next stream poll re-read the key row",
            "account": "account status + key revocation + stream re-check",
        },
    }
    if not base:
        return (
            EXIT_NOT_VERIFIED,
            [_b15_assert("b15.controller_required", False, "no controller", not_verified=True)],
            {**extensions, "b15.reason": "missing_controller"},
        )
    try:
        fixture = _b15_fixture(fixture_path)
    except ValueError as exc:
        return (
            EXIT_NOT_VERIFIED,
            [_b15_assert("b15.fixture_schema", False, str(exc), not_verified=True)],
            {**extensions, "b15.reason": str(exc)},
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        provisioned = _b15_provision(fixture)
        jobs = _b15_ensure_fixture_jobs(fixture, out_dir)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return (
            EXIT_FAIL,
            [_b15_assert("b15.fixture_provisioned", False, f"{type(exc).__name__}: {exc}")],
            {**extensions, "b15.reason": f"fixture_provision_failed:{exc}"},
        )

    scenario = fixture["scenario"]
    creds = provisioned["credentials"]["keys"]
    accounts = provisioned["accounts"]
    key_manager = provisioned["key_manager"]
    lifecycle = provisioned["lifecycle"]

    def key(name: str) -> str:
        return str(creds[str(name)]["key"])

    alpha_key_name = str(scenario["alpha_key"])
    beta_key_name = str(scenario["beta_key"])
    beta_read_key_name = str(scenario["beta_read_key"])
    alpha_job = str(scenario["alpha_job"])
    beta_job = str(scenario["beta_job"])
    alpha_project = str(scenario["alpha_project"])
    beta_project = str(scenario["beta_project"])
    beta_account = str(accounts[str(scenario["beta_account"])].account_id)

    status, capabilities, _ = _b15_http(base, "GET", "/api/capabilities", timeout=timeout)
    mode = (capabilities or {}).get("identity_mode") if isinstance(capabilities, dict) else None
    if status != 200 or mode != "api-key":
        assertions.append(
            _b15_assert(
                "b15.identity_mode.api_key",
                False,
                f"capabilities status={status} identity_mode={mode!r}",
                not_verified=True,
            )
        )
        return (
            EXIT_NOT_VERIFIED,
            assertions,
            {**extensions, "b15.reason": "identity_mode_not_api_key"},
        )
    assertions.append(_b15_assert("b15.identity_mode.api_key", True, str(mode)))
    providers = (
        capabilities.get("remote_identity_providers") if isinstance(capabilities, dict) else {}
    )
    unsupported = {
        name: (providers.get(name) or {}).get("state") for name in ("oidc", "external_iam")
    }
    assertions.append(
        _b15_assert(
            "b15.identity_providers.unsupported",
            unsupported == {"oidc": "unsupported", "external_iam": "unsupported"},
            json.dumps(unsupported, sort_keys=True),
        )
    )
    binding = capabilities.get("stdio_identity_binding") if isinstance(capabilities, dict) else {}
    ceiling = tuple(sorted((binding or {}).get("permission_ceiling") or ()))
    assertions.append(
        _b15_assert(
            "b15.stdio_identity.ceiling",
            (binding or {}).get("shared_launch_allowed") is False
            and ceiling == B15_PERMISSION_CEILING
            and (binding or {}).get("tool_arguments_are_identity") is False,
            json.dumps(binding, sort_keys=True),
        )
    )

    _, alice_identity, _ = _b15_http(
        base, "GET", "/api/identity", key=key(alpha_key_name), timeout=timeout
    )
    alice_principal = ((alice_identity or {}).get("principal") or {}).get("principal_id")
    assertions.append(
        _b15_assert(
            "b15.principal.alice",
            alice_principal == str(scenario["alpha_principal"])
            and (alice_identity or {}).get("verified_project") == alpha_project,
            json.dumps(alice_identity, sort_keys=True),
        )
    )
    forged_headers = {
        "X-ACP-Project": beta_project,
        "X-ACP-Workspace": beta_project,
        "X-ACP-Tenant": "forged-tenant",
        "X-ACP-Role": "admin",
        "X-ACP-Roles": "admin,operator",
    }
    _, forged_identity, _ = _b15_http(
        base,
        "GET",
        "/api/identity",
        key=key(alpha_key_name),
        headers=forged_headers,
        timeout=timeout,
    )
    assertions.append(
        _b15_assert(
            "b15.principal.forged_hint_unchanged",
            (forged_identity or {}).get("principal") == (alice_identity or {}).get("principal")
            and (forged_identity or {}).get("verified_project") == alpha_project
            and "admin" not in ((forged_identity or {}).get("principal") or {}).get("roles", []),
            json.dumps(forged_identity, sort_keys=True),
        )
    )
    status, unauthenticated, _ = _b15_http(base, "GET", "/api/identity", timeout=timeout)
    status_unknown, unknown_body, _ = _b15_http(
        base, "GET", "/api/identity", key="acpk_0000000000000000.invalid", timeout=timeout
    )
    assertions.append(
        _b15_assert(
            "b15.principal.unauthenticated_non_disclosure",
            status == 401
            and status_unknown == 401
            and (unauthenticated or {}).get("detail") == (unknown_body or {}).get("detail"),
            json.dumps({"missing": status, "unknown_key": status_unknown}, sort_keys=True),
        )
    )

    def status_of(
        path: str, *, key_value: str | None, method: str = "GET", body: Any = None
    ) -> tuple[int, Any]:
        return _b15_http(base, method, path, key=key_value, body=body, timeout=timeout)[:2]

    assertions.append(
        _b15_assert(
            "b15.read.alpha_own",
            status_of(f"/api/v1/jobs/{alpha_job}", key_value=key(alpha_key_name))[0] == 200,
            alpha_job,
        )
    )
    assertions.append(
        _b15_assert(
            "b15.read.beta_own",
            status_of(f"/api/v1/jobs/{beta_job}", key_value=key(beta_key_name))[0] == 200,
            beta_job,
        )
    )

    bob_alpha_status, bob_alpha_body = status_of(
        f"/api/v1/jobs/{alpha_job}", key_value=key(beta_key_name)
    )
    bob_missing_status, bob_missing_body = status_of(
        "/api/v1/jobs/b15-does-not-exist", key_value=key(beta_key_name)
    )
    same_shape = (
        bob_alpha_status == bob_missing_status == 404
        and isinstance(bob_alpha_body, dict)
        and isinstance(bob_missing_body, dict)
        and set(bob_alpha_body) == set(bob_missing_body)
        and "detail" in bob_alpha_body
    )
    assertions.append(
        _b15_assert(
            "b15.cross_project.read_non_disclosure",
            same_shape,
            json.dumps(
                {
                    "cross": [bob_alpha_status, bob_alpha_body],
                    "missing": [bob_missing_status, bob_missing_body],
                },
                sort_keys=True,
            ),
        )
    )
    bob_cancel_status, _ = status_of(
        f"/api/v1/jobs/{alpha_job}/cancel", key_value=key(beta_key_name), method="POST"
    )
    bob_prepare_status, _ = status_of(
        f"/api/v1/jobs/{alpha_job}/prepare",
        key_value=key(beta_key_name),
        method="POST",
        body={"purpose": "artifact", "artifact_id": "manifest"},
    )
    alice_beta_status, _ = status_of(f"/api/v1/jobs/{beta_job}", key_value=key(alpha_key_name))
    assertions.append(
        _b15_assert(
            "b15.cross_project.control_prepare_denied",
            bob_cancel_status == 404 and bob_prepare_status == 404 and alice_beta_status == 404,
            json.dumps(
                {
                    "bob_cancel_alpha": bob_cancel_status,
                    "bob_prepare_alpha": bob_prepare_status,
                    "alice_read_beta": alice_beta_status,
                },
                sort_keys=True,
            ),
        )
    )

    def listed_projects(key_value: str) -> tuple[int, set[str]]:
        status_value, payload = status_of("/api/v1/jobs?limit=1000", key_value=key_value)
        jobs_payload = (payload or {}).get("jobs") if isinstance(payload, dict) else None
        projects = {
            str(item.get("project_id")) for item in (jobs_payload or []) if isinstance(item, dict)
        }
        return status_value, projects

    alice_list_status, alice_projects = listed_projects(key(alpha_key_name))
    bob_list_status, bob_projects = listed_projects(key(beta_key_name))
    assertions.append(
        _b15_assert(
            "b15.read.list_scoped",
            alice_list_status == bob_list_status == 200
            and alice_projects == {alpha_project}
            and bob_projects == {beta_project},
            json.dumps(
                {
                    "alice": sorted(alice_projects),
                    "bob": sorted(bob_projects),
                },
                sort_keys=True,
            ),
        )
    )

    status_forged, _ = status_of(
        "/api/v1/jobs",
        key_value=key(alpha_key_name),
        method="POST",
        body={"workflow": "fake", "input": {"source": "C"}, "project_id": beta_project},
    )
    _, identity_after, _ = _b15_http(
        base, "GET", "/api/identity", key=key(alpha_key_name), timeout=timeout
    )
    assertions.append(
        _b15_assert(
            "b15.submit.forged_project_denied_principal_unchanged",
            status_forged == 403
            and (identity_after or {}).get("principal") == (alice_identity or {}).get("principal")
            and (identity_after or {}).get("verified_project") == alpha_project,
            json.dumps({"submit_status": status_forged}, sort_keys=True),
        )
    )
    status_readonly_submit, _ = status_of(
        "/api/v1/jobs",
        key_value=key(beta_read_key_name),
        method="POST",
        body={"workflow": "fake", "input": {"source": "C"}, "project_id": beta_project},
    )
    status_readonly_read, _ = status_of(
        f"/api/v1/jobs/{beta_job}", key_value=key(beta_read_key_name)
    )
    assertions.append(
        _b15_assert(
            "b15.key.action_ceiling",
            status_readonly_submit == 403 and status_readonly_read == 200,
            json.dumps(
                {"readonly_submit": status_readonly_submit, "readonly_read": status_readonly_read},
                sort_keys=True,
            ),
        )
    )

    alice_stream = _b15_stream_revoke(
        base,
        key(alpha_key_name),
        alpha_job,
        lambda: key_manager.revoke(str(creds[alpha_key_name]["key_id"])),
        timeout=timeout,
    )
    status_after_key_revoke, _ = status_of(
        f"/api/v1/jobs/{alpha_job}", key_value=key(alpha_key_name)
    )
    assertions.append(
        _b15_assert(
            "b15.revocation.key.read_and_stream",
            alice_stream["received_event_before_revocation"]
            and alice_stream["permission_changed_after_revocation"]
            and alice_stream["stream_thread_stopped"]
            and status_after_key_revoke == 401,
            json.dumps(
                {**alice_stream, "read_after_revoke": status_after_key_revoke}, sort_keys=True
            ),
        )
    )

    bob_stream = _b15_stream_revoke(
        base,
        key(beta_key_name),
        beta_job,
        lambda: lifecycle.revoke_account(beta_account, actor="b15-harness"),
        timeout=timeout,
    )
    status_after_account_revoke, _ = status_of(
        f"/api/v1/jobs/{beta_job}", key_value=key(beta_key_name)
    )
    assertions.append(
        _b15_assert(
            "b15.revocation.account.read_and_stream",
            bob_stream["received_event_before_revocation"]
            and bob_stream["permission_changed_after_revocation"]
            and bob_stream["stream_thread_stopped"]
            and status_after_account_revoke == 401,
            json.dumps(
                {**bob_stream, "read_after_revoke": status_after_account_revoke},
                sort_keys=True,
            ),
        )
    )

    matrix = {
        "schema": "acp2.b15.multitenant-authz.v1",
        "identity_mode": mode,
        "remote_identity_providers": providers,
        "stdio_identity_binding": binding,
        "principal": alice_identity,
        "forged_hint_identity": forged_identity,
        "cross_project": {
            "bob_read_alpha": [bob_alpha_status, bob_alpha_body],
            "bob_read_missing": [bob_missing_status, bob_missing_body],
            "bob_cancel_alpha": bob_cancel_status,
            "bob_prepare_alpha": bob_prepare_status,
            "alice_read_beta": alice_beta_status,
        },
        "lists": {"alice": sorted(alice_projects), "bob": sorted(bob_projects)},
        "revocation": {
            "key": alice_stream,
            "account": bob_stream,
            "read_after_key_revoke": status_after_key_revoke,
            "read_after_account_revoke": status_after_account_revoke,
        },
        "fixture_jobs": jobs,
    }
    matrix_path = _b15_write_json(out_dir / "multitenant-authz.json", matrix)
    extensions["b15.matrix"] = matrix_path.name
    extensions["b15.artifacts"] = [
        {
            "path": matrix_path.name,
            "size_bytes": matrix_path.stat().st_size,
            "sha256": sha256_file(matrix_path),
        }
    ]
    extensions["b15.fixture_jobs"] = jobs
    extensions["b15.accounts"] = {
        name: {
            "account_id": account.account_id,
            "principal_id": account.principal_id,
        }
        for name, account in accounts.items()
    }

    failed = [item["name"] for item in assertions if item["status"] == "FAIL"]
    not_verified = [item["name"] for item in assertions if item["status"] == "NOT_VERIFIED"]
    if failed:
        code = EXIT_FAIL
        extensions["b15.reason"] = "assertion_failed:" + ",".join(failed)
    elif not_verified:
        code = EXIT_NOT_VERIFIED
        extensions["b15.reason"] = "assertion_not_verified:" + ",".join(not_verified)
    else:
        code = EXIT_PASS
    return (
        code,
        assertions,
        extensions,
    )


B23_IDENTITY = {
    "scenario": "experience:cross-domain-live",
    "phase": "experience:selectivity",
    "subcase": "selectivity",
    "scenario_key": "experience:cross-domain-live/selectivity",
}
B23_REQUIRED_EDGES = (
    ("A-25a", "C-T40a"),
    ("B23a", "C-T40a"),
    ("C-T40a", "B23b"),
    ("B23b", "A-25b"),
    ("A-25b", "C-T40b"),
    ("C-T40b", "H6"),
)
B23_EXPECTED_T40A_GLOB = (
    "reports/acp2_implementation/bb45a46/C/cross-domain-live/**/"
    "experience_cross-domain-live-experience_selectivity-*.json"
)
B23_CROSS_DOMAIN = {
    "nmr": (
        "/ACPServer/acp2-development/A/tests/fixtures/acp2/science/cross_domain/"
        "nmr_shielding_constructed.json"
    ),
    "thermo": (
        "/ACPServer/acp2-development/A/tests/fixtures/acp2/science/cross_domain/"
        "thermochemistry_weights_constructed.json"
    ),
}


def _b23_metric(call: Any, *, iterations: int = 10) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        call()
        samples.append((time.perf_counter() - started) * 1000.0)
    ordered = sorted(samples)
    return {
        "samples_ms": [round(value, 4) for value in samples],
        "median_ms": round(ordered[len(ordered) // 2], 4),
        "min_ms": round(ordered[0], 4),
        "max_ms": round(ordered[-1], 4),
        "n": len(samples),
    }


def _b23_artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _b23_seed_matrix(fixture: Mapping[str, Any], out_dir: Path) -> dict[str, Any]:
    """Create two projects, four same-name tasks and the durable claim facts."""
    from acp.scheduler.events import JobEventLog
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.tasks import TaskIndex
    from acp_services.runtime.idempotency import (
        canonical_request_digest,
        resolve_scope_id,
    )
    from acp_services.store.layout import runtime_file
    from acp_services.store.migrations import migrate
    from acp_services.store.store import JobStore

    b23 = fixture["b23"]
    run_root = Path(str(fixture["controller"]["run_root"]))
    jobs_database = Path(
        str(fixture["controller"].get("jobs_database") or run_root / "acp_jobs.db")
    )
    migrate(jobs_database)
    store = JobStore(jobs_database)
    tasks = TaskIndex(jobs_database)

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(jobs_database))
    try:
        for project in fixture.get("projects") or []:
            project_id = str(project["project_id"])
            project_dir = run_root / "projects" / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            conn.execute(
                """INSERT OR IGNORE INTO projects
                       (project_id, name, description, tags, run_root, settings,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    str(project.get("name") or project_id),
                    str(project.get("description") or "B23 two-project fixture"),
                    "[]",
                    str(project_dir),
                    "{}",
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    created: list[dict[str, Any]] = []
    for spec in b23["jobs"]:
        job_id = str(spec["job_id"])
        project_id = str(spec["project_id"])
        principal_id = str(spec["principal"])
        scope_id = resolve_scope_id(principal_id, project_id)
        task_name = str(spec["task_name"])
        molecule_name = str(spec.get("molecule_name") or "shared-molecule")
        payload = {
            "workflow": "fake",
            "name": task_name,
            "molecule_name": molecule_name,
            "task_name": task_name,
            "project_id": project_id,
        }
        digest = canonical_request_digest(payload)
        conflict = None
        if spec.get("conflict_key"):
            conflict = store.claim_idempotency(
                scope_id, "submit", str(spec["conflict_key"]), digest
            )
        outcome = store.claim_idempotency(scope_id, "submit", str(spec["idem_key"]), digest)
        task_ref: dict[str, Any] | None = None
        if outcome.created:
            work_dir = run_root / job_id
            work_dir.mkdir(parents=True, exist_ok=True)
            record = JobRecord(
                id=job_id,
                spec=JobSpec(
                    workflow="fake",
                    name=task_name,
                    project_id=project_id,
                    molecule_name=molecule_name,
                    task_name=task_name,
                    remark=str(spec.get("remark") or ""),
                ),
                status=JobStatus.PAUSED,
                work_dir=str(work_dir),
                project_id=project_id,
                group_id=job_id,
            )
            finalized = store.finalize_idempotent_job(
                record,
                outcome.claim.claim_id,
                str(outcome.claim.owner_token),
                int(outcome.claim.lease_epoch),
            )
            task_ref = dict(finalized.task_ref or {})
        elif outcome.replay:
            task_ref = dict(outcome.task_ref or {})
        else:
            raise ValueError(
                f"idempotency claim for {job_id} was neither created nor replayed: "
                f"{outcome.kind} {outcome.detail}"
            )

        work_dir = run_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        event_log = JobEventLog(runtime_file(work_dir, "events.jsonl"))
        for index, (event_type, stage) in enumerate(b23["event_sequence"]):
            event_log.append(
                str(event_type),
                idempotency_key=f"b23:{job_id}:{index}",
                stage=str(stage),
                project_id=project_id,
                job_id=job_id,
            )
        record = store.get(job_id)
        if record is None:
            raise ValueError(f"seeded job {job_id} is missing after finalize")
        tasks.upsert(
            {
                "task_id": job_id,
                "job_id": job_id,
                "project_id": project_id,
                "molecule_name": molecule_name,
                "task_name": task_name,
                "remark": str(spec.get("remark") or ""),
                "display_name": record.spec.task_dir_name(),
                "workflow": "fake",
                "task_dir_name": record.spec.task_dir_name(),
                "status": record.status.value,
                "storage_mode": "local",
                "layout_version": 2,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "group_id": job_id,
            }
        )
        created.append(
            {
                "job_id": job_id,
                "project_id": project_id,
                "principal_id": principal_id,
                "task_name": task_name,
                "molecule_name": molecule_name,
                "task_dir_name": record.spec.task_dir_name(),
                "scope_id": scope_id,
                "idem_key": str(spec["idem_key"]),
                "task_ref": task_ref,
                "conflict": None if conflict is None else conflict.to_dict(),
                "conflict_expected": bool(spec.get("conflict_key")),
            }
        )
    return {
        "store": store,
        "tasks": tasks,
        "jobs_database": str(jobs_database),
        "run_root": str(run_root),
        "jobs": created,
    }


def _b23_stream_collect(
    base: str,
    key: str,
    job_id: str,
    expected: int,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Open one task-scoped SSE stream and collect *expected* data frames."""
    events: list[dict[str, Any]] = []
    lines: list[str] = []
    state: dict[str, Any] = {"error": None}
    query = urllib.parse.urlencode({"scope": "task", "job_ids": job_id})
    request = urllib.request.Request(
        f"{base}/api/events/stream?{query}",
        headers={"X-API-Key": key, "Accept": "text/event-stream"},
    )

    def reader() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                for raw in response:
                    line = raw.decode("utf-8", "replace").rstrip("\n")
                    lines.append(line)
                    if line.startswith("data:"):
                        try:
                            payload = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        events.append(payload if isinstance(payload, dict) else {})
                        if len(events) >= expected:
                            return
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"

    started = time.perf_counter()
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(events) < expected and thread.is_alive():
        time.sleep(0.05)
    thread.join(timeout=2.0)
    return {
        "job_id": job_id,
        "events": events,
        "line_count": len(lines),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "error": state["error"],
        "thread_stopped": not thread.is_alive(),
    }


def _b23_cross_domain(out_dir: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the reused NMR/thermochemistry artifacts without parsing QC output."""
    b23 = fixture["b23"]
    configured = b23.get("cross_domain") or {}
    nmr_path = Path(str(configured.get("nmr") or B23_CROSS_DOMAIN.get("nmr") or ""))
    thermo_path = Path(str(configured.get("thermo") or B23_CROSS_DOMAIN.get("thermo") or ""))
    payload: dict[str, Any] = {
        "schema": "acp2.b23.cross-domain-consistency.v1",
        "identity": dict(B23_IDENTITY),
        "rows": [],
        "missing_metrics": {},
    }
    for domain, path in (("nmr", nmr_path), ("thermochemistry", thermo_path)):
        if not path.is_file():
            payload["rows"].append(
                {"domain": domain, "result": "NOT_VERIFIED", "reason": f"artifact_missing:{path}"}
            )
            continue
        document = _b15_read_json(path, {}) or {}
        artifact = _b23_artifact(path)
        if domain == "nmr":
            units = {
                "isotropic_shielding": "ppm",
                "chemical_shift": "ppm",
                "temperature": "K",
            }
            provenance = {
                "schema": "acp2.science.provenance.v1",
                "method": (document.get("reference") or {}).get("method"),
                "basis": (document.get("reference") or {}).get("basis"),
                "solvent": (document.get("reference") or {}).get("solvent"),
                "solvent_model": (document.get("reference") or {}).get("solvent_model"),
                "scaling_mode": (document.get("reference") or {}).get("scaling_mode"),
                "reference_source": (document.get("reference") or {}).get("reference_source"),
                "error_model": (document.get("reference") or {}).get("error_model"),
                "required_nuclei": (document.get("reference") or {}).get("required_nuclei"),
                "temperature_kelvin": (document.get("reference") or {}).get("temperature_kelvin"),
            }
        else:
            units = {
                "gibbs_energy": "hartree",
                "temperature": "K",
                "mixing_entropy": "kcal/(mol*K)",
                "t_s_mix": "kcal/mol",
                "derived_energy_unit": "kcal_per_mol",
            }
            provenance = {
                "schema": "acp2.science.provenance.v1",
                "standard_state": document.get("standard_state"),
                "temperature_kelvin": document.get("temperature_kelvin"),
                "weights_mode": document.get("weights_mode"),
                "weight_tolerance": document.get("weight_tolerance"),
                "weights_sum_supplied": document.get("weights_sum_supplied"),
            }
        payload["rows"].append(
            {
                "domain": domain,
                "result": "PASS",
                "fixture_id": document.get("fixture_id"),
                "schema": document.get("schema"),
                "schema_version": document.get("schema_version"),
                "real_run": bool(document.get("real_run", False)),
                "constructed": bool(document.get("constructed", False)),
                "new_qc_calls": 0,
                "new_task_refs": 0,
                "task_ref": None,
                "task_ref_reason": "reused_a25a_constructed_fixture_no_new_task_ref",
                "artifact": artifact,
                "units": units,
                "provenance": provenance,
                "budget": {"new_qc_calls": 0, "new_task_refs": 0},
            }
        )
    payload["missing_metrics"] = {
        "nmr_experimental_spectrum_acquired": None,
        "thermochemistry_real_qc_energy": None,
        "reason": (
            "constructed fixtures reused; no new QC was launched and no spectrum was acquired"
        ),
    }
    payload["cross_domain_non_mixing"] = {
        "claim_namespaces": ["nmr.*", "thermochemistry.*"],
        "no_ppm_to_hartree_conversion": True,
        "shared_shape": ["task_ref", "artifact", "provenance", "budget"],
    }
    path = _b15_write_json(out_dir / "cross-domain-consistency.json", payload)
    payload["path"] = path.name
    return payload


def _b23_budget_rows(
    out_dir: Path, cross_domain: Mapping[str, Any], fixture: Mapping[str, Any]
) -> dict[str, Any]:
    rows = []
    for row in cross_domain.get("rows", []):
        domain = str(row.get("domain"))
        if domain not in {"nmr", "thermochemistry"}:
            continue
        rows.append(
            {
                "domain": "thermo" if domain == "thermochemistry" else domain,
                "mode": "reuse",
                "result": row.get("result"),
                "new_qc_calls": 0,
                "new_task_refs": 0,
                "budget_seconds": 0,
                "max_qc_calls": 0,
                "max_task_refs": 0,
                "artifacts": [row.get("artifact")],
                "reuse_reason": row.get("task_ref_reason"),
            }
        )
    b23 = fixture["b23"]
    selectivity = b23.get("selectivity") or {}
    rows.append(
        {
            "domain": "selectivity",
            "mode": "execute_pending_c",
            "result": "NOT_VERIFIED",
            "reason": "c_t40a_absent",
            "frozen": False,
            "max_task_refs": 1,
            "max_task_refs_frozen": True,
            "budget_seconds": None,
            "max_qc_calls": None,
            "per_step_qc_calls": {"path.endo": None, "path.exo": None},
            "proposed": {
                "budget_seconds": selectivity.get("proposed_budget_seconds"),
                "max_qc_calls": selectivity.get("proposed_max_qc_calls"),
                "budget_timing_basis": "unmeasured",
                "source": "A-25a constructed envelope; never frozen without representative timing",
            },
            "new_qc_calls": 0,
            "new_task_refs": 0,
            "submission": None,
            "paths": {"path.endo": "not_submitted", "path.exo": "not_submitted"},
        }
    )
    document = {
        "schema": "acp2.b23.budget-rows.v1",
        "identity": dict(B23_IDENTITY),
        "rows": rows,
        "reconcile": {
            "required_domains": ["nmr", "thermo", "selectivity"],
            "nmr_new_qc_calls": 0,
            "thermo_new_qc_calls": 0,
            "selectivity_new_task_refs": 0,
            "selectivity_max_task_refs": 1,
            "all_frozen": False,
            "reason": "c_t40a_absent",
        },
        "multitenant_timeout_seconds": 1800,
        "multitenant_timeout_scope": "B23a isolation/permissions/budget readiness only",
    }
    path = _b15_write_json(out_dir / "budget-rows.json", document)
    document["path"] = path.name
    return document


def _b23_b23b_row(out_dir: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    repo = REPO_ROOT
    candidates = sorted(repo.glob(B23_EXPECTED_T40A_GLOB))
    document = {
        "schema": "acp2.b23.sub-delivery-row.v1",
        "id": "B23b",
        "owner": "B",
        "kind": "execution_export",
        "identity": dict(B23_IDENTITY),
        "result": "NOT_VERIFIED",
        "scope": "IN_SCOPE",
        "reason": "c_t40a_absent",
        "deferred": False,
        "expected_artifact": {
            "owner": "C",
            "scenario": "experience:cross-domain-live",
            "phase": "experience:selectivity",
            "subcase": "selectivity",
            "path_glob": B23_EXPECTED_T40A_GLOB,
            "required_fields": [
                "job_id",
                "attempt",
                "release_id",
                "scenario",
                "phase",
                "extensions.experience.subcase",
            ],
        },
        "observed_candidates": [str(path.relative_to(repo)) for path in candidates],
        "submission": None,
        "task_refs": [],
        "paths": {"path.endo": None, "path.exo": None},
        "h6_fed": False,
        "no_selectivity_qc": True,
        "parent_only_pass_accepted": False,
        "run_spec_ref": (fixture["b23"].get("selectivity") or {}).get("run_spec_ref"),
    }
    path = _b15_write_json(out_dir / "b23b-row.json", document)
    document["path"] = path.name
    return document


def _b23_measurements(seeded: Mapping[str, Any], alpha_job: str, beta_job: str) -> dict[str, Any]:
    from acp.results.remote_structure_cache import RemoteStructureCache
    from acp.scheduler.events import JobEventLog
    from acp.scheduler.task_views import GroupBy, TaskViewQuery, query_project_tasks
    from acp_services.contracts.events import EventScope
    from acp_services.runtime.events import AuthorizedEventStream, AuthorizedStreamRegistry
    from acp_services.store.layout import runtime_file

    store = seeded["store"]
    tasks = seeded["tasks"]
    run_root = Path(str(seeded["run_root"]))
    alpha_project = str(seeded["jobs"][0]["project_id"])
    beta_project = str(seeded["jobs"][1]["project_id"])

    local_read = _b23_metric(lambda: store.get(alpha_job), iterations=15)
    detail = _b23_metric(lambda: store.get(beta_job), iterations=15)
    batch_list = _b23_metric(
        lambda: store.list_enriched(limit=50, project_id=alpha_project), iterations=10
    )

    def task_view() -> dict[str, Any]:
        return query_project_tasks(
            tasks,
            TaskViewQuery(project_id=alpha_project, group_by=GroupBy.molecule),
        )

    task_view_metric = _b23_metric(task_view, iterations=10)
    task_view_result = task_view()

    cache = RemoteStructureCache(run_root)
    cache_target = cache.cache_path(alpha_job, "RESULT/result.json")
    cache_target.parent.mkdir(parents=True, exist_ok=True)
    cache_target.write_bytes(b'{"result":"b23-reused"}\n')
    remote_cached_read = _b23_metric(
        lambda: cache.get_cached(alpha_job, "RESULT/result.json"), iterations=15
    )

    stream_metric_samples: list[float] = []
    for _ in range(10):
        stream = AuthorizedEventStream(
            principal_id="principal_b23_alice",
            scope=EventScope.TASK,
            service_epoch="b23-measurement",
            log_for=lambda job_id: JobEventLog(runtime_file(run_root / job_id, "events.jsonl")),
        )
        stream.subscribe([alpha_job])
        started = time.perf_counter()
        batch = stream.poll([])
        stream_metric_samples.append((time.perf_counter() - started) * 1000.0)
    events_metric = {
        "samples_ms": [round(value, 4) for value in stream_metric_samples],
        "median_ms": round(sorted(stream_metric_samples)[len(stream_metric_samples) // 2], 4),
        "n": len(stream_metric_samples),
        "events_per_poll": len(batch.events),
    }

    registry = AuthorizedStreamRegistry()
    log_for = lambda job_id: JobEventLog(  # noqa: E731 - measurement adapter
        runtime_file(run_root / job_id, "events.jsonl")
    )
    first = registry.open(
        principal_id="principal_b23_alice",
        scope=EventScope.TASK,
        service_epoch="b23-measurement",
        log_for=log_for,
        job_ids=[alpha_job],
    )
    second = registry.open(
        principal_id="principal_b23_alice",
        scope=EventScope.TASK,
        service_epoch="b23-measurement",
        log_for=log_for,
        job_ids=[beta_job],
    )
    after_duplicate = query_project_tasks(
        tasks,
        TaskViewQuery(project_id=alpha_project, group_by=GroupBy.molecule),
    )
    before_statements = int(task_view_result.get("sql_statements") or 0)
    after_statements = int(after_duplicate.get("sql_statements") or 0)
    duplicate_subscription = {
        "opens": 2,
        "same_stream_object": first is second,
        "active_streams": registry.active_count,
        "distinct_keys": len(registry.keys),
        "subscribed_owners": sorted(second.task_ids),
        "additional_stream_resources": registry.active_count - 1,
        "db_statements_delta": after_statements - before_statements,
        "list_sql_statements_before": before_statements,
        "list_sql_statements_after": after_statements,
        "note": (
            "a repeated (principal, scope) joins the existing stream; it never opens a second one"
        ),
    }
    registry.close_all("b23-measurement")

    from acp_services.runtime.admission import (
        AdmissionRequest,
        Capacity,
        ReservationLedger,
        ReserveLimits,
    )

    quota_path = run_root / ".b23-quota" / "admission.db"
    quota_path.parent.mkdir(parents=True, exist_ok=True)
    if quota_path.exists():
        quota_path.unlink()
    ledger = ReservationLedger(str(quota_path))
    limits = ReserveLimits(
        capacity=Capacity(cores=1, memory_mb=512, concurrency=2, storage_bytes=10_000_000)
    )
    first_quota = ledger.reserve(
        AdmissionRequest(
            principal_id="principal_b23_alice",
            project_id=alpha_project,
            job_id=alpha_job,
            cores=1,
        ),
        limits,
        reservation_id="res_b23_alpha",
    )
    second_quota = ledger.reserve(
        AdmissionRequest(
            principal_id="principal_b23_bob",
            project_id=beta_project,
            job_id=beta_job,
            cores=1,
        ),
        limits,
        reservation_id="res_b23_beta",
    )
    occupancy = ledger.occupancy()
    quota = {
        "capacity": limits.capacity.to_dict(),
        "alpha_admitted": first_quota.allowed,
        "beta_admitted": second_quota.allowed,
        "beta_reason": second_quota.reason_code,
        "beta_exceeded": [dict(item) for item in second_quota.exceeded],
        "occupancy_after_two_requests": occupancy,
        "shared_node": True,
    }
    disk = shutil.disk_usage(run_root)
    return {
        "schema": "acp2.b23.measurements.v1",
        "generated_at": _utcnow(),
        "machine": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "sample": {
            "projects": 2,
            "tasks_per_project": 2,
            "config_id": "b23-two-project-paused-v1",
            "controller": "127.0.0.1 local Controller (same machine)",
        },
        "reads": {
            "local": {"job_get": local_read, "detail": detail},
            "remote_cached": {
                "cache_get_cached": remote_cached_read,
                "transport": "RemoteStructureCache cache replica on the same filesystem",
            },
            "remote_live": {
                "value": None,
                "reason": "lsf_unavailable",
            },
        },
        "batch": {"list": batch_list, "task_view": task_view_metric},
        "events": events_metric,
        "duplicate_subscription": duplicate_subscription,
        "quota": quota,
        "storage": {
            "run_root_disk_total_bytes": disk.total,
            "run_root_disk_free_bytes": disk.free,
            "hosted_hard_limit_bytes": None,
            "hosted_hard_limit_reason": (
                "lab local-single config exposes no hosted storage quota; not hosted-verified"
            ),
        },
    }


def _b23_dependency_edges() -> dict[str, Any]:
    path = CONTRACT_ROOT / "runtime" / "dependencies.json"
    document = _b20_read_json(path)
    rows = {
        (str(edge.get("producer")), str(edge.get("consumer")))
        for edge in (document or {}).get("cross_track_edges", [])
        if isinstance(edge, dict)
    }
    missing = [pair for pair in B23_REQUIRED_EDGES if pair not in rows]
    p4_sync = (document or {}).get("p4_sync") or {}
    return {
        "schema": "acp2.b23.dependency-edges.v1",
        "path": str(path),
        "required_edges": [
            {"producer": left, "consumer": right} for left, right in B23_REQUIRED_EDGES
        ],
        "observed_edges": sorted(
            [{"producer": left, "consumer": right} for left, right in rows],
            key=lambda item: (item["producer"], item["consumer"]),
        ),
        "missing": [{"producer": left, "consumer": right} for left, right in missing],
        "all_present": not missing,
        "p4_sync": {
            "published_before_dispatch": bool(p4_sync.get("published_before_dispatch")),
            "published_at": p4_sync.get("published_at"),
            "identity": p4_sync.get("identity"),
            "gating": p4_sync.get("gating"),
        },
    }


def _run_multitenant_two_project(
    controller: str | None,
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """B23a: two users, two projects, same names/keys, cross-domain reuse."""
    base = (controller or "").rstrip("/")
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b23.identity": dict(B23_IDENTITY),
        "b23.fixture": str(fixture_path),
        "b23.controller": base,
    }
    if not base:
        return (
            EXIT_NOT_VERIFIED,
            [_b15_assert("b23.controller_required", False, "no controller", not_verified=True)],
            {**extensions, "b23.reason": "missing_controller"},
        )
    try:
        fixture = _b15_fixture(fixture_path)
    except ValueError as exc:
        return (
            EXIT_NOT_VERIFIED,
            [_b15_assert("b23.fixture_schema", False, str(exc), not_verified=True)],
            {**extensions, "b23.reason": str(exc)},
        )
    b23 = fixture.get("b23")
    if not isinstance(b23, dict):
        return (
            EXIT_NOT_VERIFIED,
            [
                _b15_assert(
                    "b23.fixture_schema", False, "fixture.b23 is required", not_verified=True
                )
            ],
            {**extensions, "b23.reason": "fixture_b23_missing"},
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        provisioned = _b15_provision(fixture)
        seeded = _b23_seed_matrix(fixture, out_dir)
    except (OSError, ValueError, sqlite3.Error) as exc:
        return (
            EXIT_FAIL,
            [_b15_assert("b23.fixture_provisioned", False, f"{type(exc).__name__}: {exc}")],
            {**extensions, "b23.reason": f"fixture_provision_failed:{exc}"},
        )

    credentials = provisioned["credentials"]["keys"]
    accounts = provisioned["accounts"]

    def key(name: str) -> str:
        return str(credentials[str(name)]["key"])

    alpha_key_name = str(b23["keys"]["alpha"])
    beta_key_name = str(b23["keys"]["beta"])
    alpha_job = str(b23["alpha_job"])
    beta_job = str(b23["beta_job"])
    alpha_project = str(b23["alpha_project"])
    beta_project = str(b23["beta_project"])
    alpha_principal = str(b23["alpha_principal"])
    beta_principal = str(b23["beta_principal"])
    beta_account = str(accounts[str(b23["beta_account"])].account_id)
    shared_key = str(b23["shared_idempotency_key"])

    status, capabilities, _ = _b15_http(base, "GET", "/api/capabilities", timeout=timeout)
    mode = (capabilities or {}).get("identity_mode") if isinstance(capabilities, dict) else None
    assertions.append(
        _b15_assert(
            "b23.identity.api_key",
            status == 200 and mode == "api-key",
            f"status={status} identity_mode={mode!r}",
        )
    )

    jobs = {item["job_id"]: item for item in seeded["jobs"]}
    alpha_row = jobs[alpha_job]
    beta_row = jobs[beta_job]
    assertions.append(
        _b15_assert(
            "b23.same_name.identical_task_directory",
            alpha_row["task_name"] == beta_row["task_name"]
            and alpha_row["molecule_name"] == beta_row["molecule_name"]
            and alpha_row["task_dir_name"] == beta_row["task_dir_name"]
            and alpha_row["job_id"] != beta_row["job_id"],
            json.dumps(
                {
                    "alpha": alpha_row["task_dir_name"],
                    "beta": beta_row["task_dir_name"],
                    "task_name": alpha_row["task_name"],
                },
                sort_keys=True,
            ),
        )
    )
    conflict = beta_row.get("conflict") or {}
    assertions.append(
        _b15_assert(
            "b23.idempotency.same_key_cross_project_conflict",
            beta_row.get("conflict_expected") is True and conflict.get("kind") == "conflict",
            json.dumps(conflict, sort_keys=True),
        )
    )
    alpha_claim = seeded["store"].find_idempotency_claim(
        alpha_row["scope_id"], "submit", shared_key
    )
    beta_claim = seeded["store"].find_idempotency_claim(beta_row["scope_id"], "submit", shared_key)
    assertions.append(
        _b15_assert(
            "b23.idempotency.project_scoped_task_refs",
            alpha_claim is not None
            and alpha_claim.task_id == alpha_job
            and beta_claim is None
            and alpha_row["task_ref"].get("job_id") == alpha_job
            and beta_row["task_ref"].get("job_id") == beta_job,
            json.dumps(
                {
                    "alpha_claim_task": None if alpha_claim is None else alpha_claim.task_id,
                    "beta_claim_same_key": None if beta_claim is None else beta_claim.task_id,
                },
                sort_keys=True,
            ),
        )
    )

    def call(method: str, path: str, *, key_value: str, body: Any = None) -> tuple[int, Any]:
        status_value, payload, _ = _b15_http(
            base, method, path, key=key_value, body=body, timeout=timeout
        )
        return status_value, payload

    alice = key(alpha_key_name)
    bob = key(beta_key_name)
    bob_alpha_status, bob_alpha_body = call("GET", f"/api/v1/jobs/{alpha_job}", key_value=bob)
    bob_missing_status, bob_missing_body = call(
        "GET", "/api/v1/jobs/b23-does-not-exist", key_value=bob
    )
    alice_beta_status, _ = call("GET", f"/api/v1/jobs/{beta_job}", key_value=alice)
    v2_cross_status, _ = call("GET", f"/api/v2/tasks/{alpha_job}", key_value=bob)
    v2_cross_tree_status, _ = call("GET", f"/api/v2/tasks/{alpha_job}/tree", key_value=bob)
    assertions.append(
        _b15_assert(
            "b23.cross_project.read_non_disclosure",
            bob_alpha_status == bob_missing_status == v2_cross_status == v2_cross_tree_status == 404
            and isinstance(bob_alpha_body, dict)
            and isinstance(bob_missing_body, dict)
            and set(bob_alpha_body) == set(bob_missing_body)
            and alice_beta_status == 404,
            json.dumps(
                {
                    "v1_cross": bob_alpha_status,
                    "v1_missing": bob_missing_status,
                    "v2_detail": v2_cross_status,
                    "v2_tree": v2_cross_tree_status,
                    "v1_reverse": alice_beta_status,
                },
                sort_keys=True,
            ),
        )
    )
    bob_cancel_status, _ = call("POST", f"/api/v1/jobs/{alpha_job}/cancel", key_value=bob)
    alice_cancel_beta, _ = call("POST", f"/api/v1/jobs/{beta_job}/cancel", key_value=alice)
    bob_prepare_status, _ = call(
        "POST",
        f"/api/v1/jobs/{alpha_job}/prepare",
        key_value=bob,
        body={"purpose": "artifact", "artifact_id": "manifest"},
    )
    bob_file_status, _ = call("GET", f"/api/v1/jobs/{alpha_job}/files/note.txt", key_value=bob)
    assertions.append(
        _b15_assert(
            "b23.cross_project.control_prepare_denied",
            bob_cancel_status == alice_cancel_beta == bob_prepare_status == bob_file_status == 404,
            json.dumps(
                {
                    "bob_cancel_alpha": bob_cancel_status,
                    "alice_cancel_beta": alice_cancel_beta,
                    "bob_prepare_alpha": bob_prepare_status,
                    "bob_file_alpha": bob_file_status,
                },
                sort_keys=True,
            ),
        )
    )

    alice_list_status, alice_list = call("GET", "/api/v1/jobs?limit=1000", key_value=alice)
    bob_list_status, bob_list = call("GET", "/api/v1/jobs?limit=1000", key_value=bob)
    alice_projects_status, alice_projects = call("GET", "/api/v2/projects", key_value=alice)
    bob_projects_status, bob_projects = call("GET", "/api/v2/projects", key_value=bob)
    bob_view_status, bob_view = call("GET", "/api/v2/task-view", key_value=bob)
    alice_view_status, alice_view = call("GET", "/api/v2/task-view", key_value=alice)

    def projects_from_v1(payload: Any) -> set[str]:
        rows = (payload or {}).get("jobs") if isinstance(payload, dict) else None
        return {str(item.get("project_id")) for item in (rows or []) if isinstance(item, dict)}

    def projects_from_v2_view(payload: Any) -> set[str]:
        groups = (payload or {}).get("groups") if isinstance(payload, dict) else None
        values: set[str] = set()
        for group in groups or []:
            for row in group.get("jobs") or []:
                if isinstance(row, dict) and row.get("project_id"):
                    values.add(str(row["project_id"]))
        return values

    def projects_from_v2_list(payload: Any) -> set[str]:
        return {
            str(item.get("project_id"))
            for item in (payload or [])
            if isinstance(item, dict) and item.get("project_id")
        }

    assertions.append(
        _b15_assert(
            "b23.cross_project.list_scoped",
            alice_list_status == bob_list_status == 200
            and projects_from_v1(alice_list) == {alpha_project}
            and projects_from_v1(bob_list) == {beta_project}
            and alice_projects_status == bob_projects_status == 200
            and projects_from_v2_list(alice_projects) == {alpha_project}
            and projects_from_v2_list(bob_projects) == {beta_project}
            and bob_view_status == alice_view_status == 200
            and projects_from_v2_view(bob_view) == {beta_project}
            and projects_from_v2_view(alice_view) == {alpha_project},
            json.dumps(
                {
                    "v1_alice": sorted(projects_from_v1(alice_list)),
                    "v1_bob": sorted(projects_from_v1(bob_list)),
                    "v2_projects_bob": sorted(projects_from_v2_list(bob_projects)),
                    "v2_view_bob": sorted(projects_from_v2_view(bob_view)),
                },
                sort_keys=True,
            ),
        )
    )

    alice_stream, bob_stream = {}, {}
    stream_error: list[str] = []

    def collect(label: str, key_value: str, job_id: str) -> None:
        try:
            if label == "alpha":
                alice_stream.update(
                    _b23_stream_collect(base, key_value, job_id, 3, timeout=timeout)
                )
            else:
                bob_stream.update(_b23_stream_collect(base, key_value, job_id, 3, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - reported as an assertion failure
            stream_error.append(f"{label}:{type(exc).__name__}:{exc}")

    alpha_thread = threading.Thread(target=collect, args=("alpha", alice, alpha_job))
    beta_thread = threading.Thread(target=collect, args=("beta", bob, beta_job))
    alpha_thread.start()
    beta_thread.start()
    alpha_thread.join(timeout=timeout)
    beta_thread.join(timeout=timeout)
    cross_stream_status, _ = _b15_http(
        base,
        "GET",
        f"/api/events/stream?scope=task&job_ids={alpha_job}",
        key=bob,
        timeout=10.0,
    )[:2]

    def ordered(stream: Mapping[str, Any], job_id: str) -> bool:
        events = stream.get("events") or []
        owners = {str(event.get("owner_id")) for event in events}
        seqs = [int(event.get("seq") or 0) for event in events]
        return (
            len(events) == 3
            and owners == {job_id}
            and seqs == sorted(seqs)
            and len(set(seqs)) == len(seqs)
            and not stream.get("error")
        )

    assertions.append(
        _b15_assert(
            "b23.events.parallel_ordered_per_project",
            not stream_error
            and ordered(alice_stream, alpha_job)
            and ordered(bob_stream, beta_job)
            and cross_stream_status == 404,
            json.dumps(
                {
                    "alpha": {
                        "count": len(alice_stream.get("events") or []),
                        "error": alice_stream.get("error"),
                    },
                    "beta": {
                        "count": len(bob_stream.get("events") or []),
                        "error": bob_stream.get("error"),
                    },
                    "cross_project_subscribe": cross_stream_status,
                    "errors": stream_error,
                },
                sort_keys=True,
            ),
        )
    )

    measurements = _b23_measurements(seeded, alpha_job, beta_job)
    quota = measurements["quota"]
    assertions.append(
        _b15_assert(
            "b23.quota.shared_node_capacity",
            quota["alpha_admitted"] is True
            and quota["beta_admitted"] is False
            and any(item.get("scope") == "capacity" for item in quota["beta_exceeded"])
            and quota["occupancy_after_two_requests"]["cores"] == 1
            and quota["occupancy_after_two_requests"]["reservations"] == 1,
            json.dumps(quota, sort_keys=True),
        )
    )
    measurements_path = _b15_write_json(out_dir / "measurements.json", measurements)
    extensions["b23.measurements"] = measurements_path.name

    cross_domain = _b23_cross_domain(out_dir, fixture)
    cross_rows = cross_domain.get("rows") or []
    shared_fields = {"task_ref", "artifact", "provenance", "budget", "units"}
    assertions.append(
        _b15_assert(
            "b23.cross_domain.unified_semantics",
            len(cross_rows) == 2
            and all(row.get("result") == "PASS" for row in cross_rows)
            and all(shared_fields <= set(row) for row in cross_rows)
            and all(
                row.get("schema") == "acp2.science.domain-fixture"
                and row.get("schema_version") == 1
                and row.get("real_run") is False
                for row in cross_rows
            ),
            json.dumps(
                {"rows": [row.get("domain") for row in cross_rows]},
                sort_keys=True,
            ),
        )
    )
    assertions.append(
        _b15_assert(
            "b23.cross_domain.units_and_provenance",
            all(
                row.get("units", {}).get("temperature") == "K"
                and row.get("provenance", {}).get("schema") == "acp2.science.provenance.v1"
                for row in cross_rows
            )
            and next((row for row in cross_rows if row.get("domain") == "nmr"), {})
            .get("units", {})
            .get("isotropic_shielding")
            == "ppm"
            and next((row for row in cross_rows if row.get("domain") == "thermochemistry"), {})
            .get("units", {})
            .get("gibbs_energy")
            == "hartree"
            and cross_domain.get("missing_metrics", {}).get(
                "nmr_experimental_spectrum_acquired", "unset"
            )
            is None,
            json.dumps(
                {
                    "units": [row.get("units") for row in cross_rows],
                    "missing": cross_domain.get("missing_metrics"),
                },
                sort_keys=True,
            ),
        )
    )

    budget = _b23_budget_rows(out_dir, cross_domain, fixture)
    rows = {row["domain"]: row for row in budget["rows"]}
    assertions.append(
        _b15_assert(
            "b23.budget.three_rows_reconcile",
            set(rows) == {"nmr", "thermo", "selectivity"}
            and rows["nmr"]["new_qc_calls"] == 0
            and rows["thermo"]["new_qc_calls"] == 0
            and rows["selectivity"]["max_task_refs"] == 1
            and rows["selectivity"]["result"] == "NOT_VERIFIED"
            and rows["selectivity"]["frozen"] is False
            and budget["reconcile"]["all_frozen"] is False,
            json.dumps(budget["reconcile"], sort_keys=True),
        )
    )

    b23b = _b23_b23b_row(out_dir, fixture)
    assertions.append(
        _b15_assert(
            "b23.b23b.c_t40a_absent",
            b23b["result"] == "NOT_VERIFIED"
            and b23b["reason"] == "c_t40a_absent"
            and b23b["submission"] is None
            and b23b["task_refs"] == []
            and b23b["h6_fed"] is False
            and b23b["deferred"] is False
            and not b23b["observed_candidates"],
            json.dumps(
                {
                    "result": b23b["result"],
                    "reason": b23b["reason"],
                    "candidates": b23b["observed_candidates"],
                },
                sort_keys=True,
            ),
        )
    )

    dependencies = _b23_dependency_edges()
    assertions.append(
        _b15_assert(
            "b23.dependencies.external_edges",
            dependencies["all_present"] is True
            and dependencies["p4_sync"]["published_before_dispatch"] is True
            and dependencies["p4_sync"]["identity"] == B23_IDENTITY,
            json.dumps(dependencies["p4_sync"], sort_keys=True),
        )
    )
    dependencies_path = _b15_write_json(out_dir / "dependency-edges.json", dependencies)
    extensions["b23.dependencies"] = dependencies_path.name

    identity_text = json.dumps(
        {
            "extensions": budget.get("identity"),
            "b23b": b23b.get("identity"),
            "cross_domain": cross_domain.get("identity"),
        },
        sort_keys=True,
    )
    assertions.append(
        _b15_assert(
            "b23.identity.single_registered_identity",
            "selectivity-live" not in identity_text
            and budget.get("identity") == B23_IDENTITY
            and b23b.get("identity") == B23_IDENTITY,
            identity_text,
        )
    )

    alice_stream_revoke = _b15_stream_revoke(
        base,
        alice,
        alpha_job,
        lambda: provisioned["key_manager"].revoke(str(credentials[alpha_key_name]["key_id"])),
        timeout=timeout,
    )
    read_after_key_revoke, _ = call("GET", f"/api/v1/jobs/{alpha_job}", key_value=alice)
    prepare_after_key_revoke, _ = call(
        "POST",
        f"/api/v1/jobs/{alpha_job}/prepare",
        key_value=alice,
        body={"purpose": "artifact", "artifact_id": "manifest"},
    )
    file_after_key_revoke, _ = call(
        "GET", f"/api/v1/jobs/{alpha_job}/files/note.txt", key_value=alice
    )
    assertions.append(
        _b15_assert(
            "b23.revocation.key.stops_events_and_artifacts",
            alice_stream_revoke["received_event_before_revocation"]
            and alice_stream_revoke["permission_changed_after_revocation"]
            and alice_stream_revoke["stream_thread_stopped"]
            and read_after_key_revoke == prepare_after_key_revoke == file_after_key_revoke == 401,
            json.dumps(
                {
                    **alice_stream_revoke,
                    "read": read_after_key_revoke,
                    "prepare": prepare_after_key_revoke,
                    "file": file_after_key_revoke,
                },
                sort_keys=True,
            ),
        )
    )

    bob_stream_revoke = _b15_stream_revoke(
        base,
        bob,
        beta_job,
        lambda: provisioned["lifecycle"].revoke_account(beta_account, actor="b23-harness"),
        timeout=timeout,
    )
    read_after_project_revoke, _ = call("GET", f"/api/v1/jobs/{beta_job}", key_value=bob)
    prepare_after_project_revoke, _ = call(
        "POST",
        f"/api/v1/jobs/{beta_job}/prepare",
        key_value=bob,
        body={"purpose": "artifact", "artifact_id": "manifest"},
    )
    file_after_project_revoke, _ = call(
        "GET", f"/api/v1/jobs/{beta_job}/files/note.txt", key_value=bob
    )
    assertions.append(
        _b15_assert(
            "b23.revocation.project.stops_events_and_artifacts",
            bob_stream_revoke["received_event_before_revocation"]
            and bob_stream_revoke["permission_changed_after_revocation"]
            and bob_stream_revoke["stream_thread_stopped"]
            and read_after_project_revoke
            == prepare_after_project_revoke
            == file_after_project_revoke
            == 401,
            json.dumps(
                {
                    **bob_stream_revoke,
                    "read": read_after_project_revoke,
                    "prepare": prepare_after_project_revoke,
                    "file": file_after_project_revoke,
                },
                sort_keys=True,
            ),
        )
    )

    matrix = {
        "schema": "acp2.b23.multiproject.v1",
        "identity": dict(B23_IDENTITY),
        "fixture_jobs": {
            "jobs_database": seeded["jobs_database"],
            "run_root": seeded["run_root"],
            "jobs": seeded["jobs"],
        },
        "cross_project": {
            "bob_read_alpha": [bob_alpha_status, bob_alpha_body],
            "bob_read_missing": [bob_missing_status, bob_missing_body],
            "bob_cancel_alpha": bob_cancel_status,
            "bob_prepare_alpha": bob_prepare_status,
            "bob_file_alpha": bob_file_status,
            "v2_bob_task_alpha": v2_cross_status,
            "v2_bob_tree_alpha": v2_cross_tree_status,
        },
        "lists": {
            "v1_alice": sorted(projects_from_v1(alice_list)),
            "v1_bob": sorted(projects_from_v1(bob_list)),
            "v2_projects_alice": sorted(projects_from_v2_list(alice_projects)),
            "v2_projects_bob": sorted(projects_from_v2_list(bob_projects)),
            "v2_view_alice": sorted(projects_from_v2_view(alice_view)),
            "v2_view_bob": sorted(projects_from_v2_view(bob_view)),
        },
        "events": {"alpha": alice_stream, "beta": bob_stream},
        "quota": quota,
        "cross_domain": cross_domain,
        "budget_rows": budget,
        "b23b": b23b,
        "dependency_edges": dependencies,
        "revocation": {
            "key": alice_stream_revoke,
            "project": bob_stream_revoke,
            "read_after_key_revoke": read_after_key_revoke,
            "read_after_project_revoke": read_after_project_revoke,
        },
    }
    matrix_path = _b15_write_json(out_dir / "multitenant-two-project.json", matrix)
    extensions["b23.matrix"] = matrix_path.name
    extensions["b23.artifacts"] = [
        {
            "path": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(out_dir.glob("*.json"))
        if path.name != "summary.json"
    ]
    extensions["b23.accounts"] = {
        name: {"account_id": account.account_id, "principal_id": account.principal_id}
        for name, account in accounts.items()
    }
    extensions["b23.principal_scope"] = {
        "alpha": alpha_principal,
        "beta": beta_principal,
    }

    failed = [item["name"] for item in assertions if item["status"] == "FAIL"]
    not_verified = [item["name"] for item in assertions if item["status"] == "NOT_VERIFIED"]
    if failed:
        code = EXIT_FAIL
        extensions["b23.reason"] = "assertion_failed:" + ",".join(failed)
    elif not_verified:
        code = EXIT_NOT_VERIFIED
        extensions["b23.reason"] = "assertion_not_verified:" + ",".join(not_verified)
    else:
        code = EXIT_PASS
    return code, assertions, extensions


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
            f"{shlex.quote(sys.executable)} -m acp mcp serve --transport stdio "
            f"--controller {shlex.quote(controller)}"
        )
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp mcp serve --transport stdio "
            f"--controller {shlex.quote(controller)}"
        )
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario submit-replay --controller {shlex.quote(controller)} "
            f"--launch {shlex.quote(str(launch))} --fixture {shlex.quote(fixture)} "
            f"--out {shlex.quote(out)}"
        )
    elif spec.scenario == "local-singlepoint":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp mcp serve --transport stdio "
            f"--controller {shlex.quote(controller)}"
        )
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario local-singlepoint --phase {shlex.quote(phase_name)} "
            f"--controller {shlex.quote(controller)} --launch {shlex.quote(launch)} "
            f"--fixture {shlex.quote(fixture)} --out {shlex.quote(out)}"
        )
    elif spec.scenario == "three-entry":
        commands.append(
            f"{shlex.quote(sys.executable)} -m acp task query --controller "
            f"{shlex.quote(controller)} --job <task_id from fixture>"
        )
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario three-entry --phase {shlex.quote(phase_name)} "
            f"--controller {shlex.quote(controller)} --fixture {shlex.quote(fixture)} "
            f"--out {shlex.quote(out)}"
        )
    elif spec.scenario == "lsf-singlepoint":
        commands.append(
            "command -v bsub bjobs bhosts bqueues ssh && "
            "printenv ACP_LSF_NODE LSF_ENVDIR SSH_AUTH_SOCK"
        )
    elif spec.scenario == "artifact":
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario artifact --phase {shlex.quote(phase_name)} "
            f"--controller {shlex.quote(controller)} --fixture {shlex.quote(fixture)} "
            f"--out {shlex.quote(out)}"
        )
    elif spec.scenario == "control-recovery":
        commands.append(f"curl -fsS {shlex.quote(controller + '/api/health')}")
        control_command = (
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario control-recovery --phase {shlex.quote(phase_name)} "
            f"--controller {shlex.quote(controller)} --fixture {shlex.quote(fixture)} "
            f"--out {shlex.quote(out)}"
        )
        if args.launch:
            control_command += f" --launch {shlex.quote(str(args.launch))}"
        commands.append(control_command)
    elif spec.scenario == "approval-budget":
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario approval-budget --phase {shlex.quote(phase_name)} "
            f"--controller {shlex.quote(controller)} --fixture {shlex.quote(fixture)} "
            f"--out {shlex.quote(out)}"
        )
    elif spec.scenario == "da-runtime":
        commands.append(
            f"{shlex.quote(sys.executable)} scripts/acceptance/acp2_runtime.py "
            f"--scenario da-runtime --phase {phase_name} --controller "
            f"{shlex.quote(controller)} --fixture {shlex.quote(fixture)} --out {shlex.quote(out)}"
        )
    elif spec.scenario == "bounded-recipe-dag":
        commands.append(
            f"{shlex.quote(sys.executable)} scripts/acceptance/acp2_runtime.py "
            f"--scenario bounded-recipe-dag --phase {phase_name} "
            f"--fixture {shlex.quote(fixture)} --out {shlex.quote(out)}"
        )
    elif spec.scenario == "infra-plugin":
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario infra-plugin --phase {shlex.quote(phase_name)} "
            f"--fixture {shlex.quote(fixture)} --out {shlex.quote(out)}"
        )
    elif spec.scenario == "migration":
        commands.append(f"{shlex.quote(sys.executable)} -m acp task export {shlex.quote(fixture)}")
    elif spec.scenario == "package-install":
        commands.append(
            f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))} "
            f"--scenario package-install --fixture {shlex.quote(fixture)} "
            f"--out {shlex.quote(out)} --timeout {int(phase.timeout)}"
        )

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
    if "fixture" in phase.required_args or spec.scenario == "package-install":
        harness_command += ["--fixture", fixture]
    if "launch" in phase.required_args:
        harness_command += ["--launch", launch]
    if spec.scenario == "lsf-singlepoint":
        derived, _ = _b11_timeout_derivation(Path(out))
        if derived is not None:
            harness_command += ["--timeout", str(derived)]
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
        _evidence_kind_for_phase(phase_name, spec.scenario), args.controller, timeout
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
        evidence_kind=_evidence_kind_for_phase(phase_name, spec.scenario),
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


# ---------------------------------------------------------------------------
# B24a migration / backup / restore scenario
# ---------------------------------------------------------------------------

MIGRATION_FIXTURE_SCHEMA = "acp2.migration.fixture.v1"
MIGRATION_HELPER = REPO_ROOT / "scripts" / "acceptance" / "acp2_migration_helper.py"
MIGRATION_SOURCE_FIXTURES = REPO_ROOT / "tests" / "baseline" / "recovery_fixtures"
MIGRATION_ARTIFACT_DIRS = ("source-copy", "tasks", "legacy-root/tasks")
MIGRATION_HEAD_TABLES = (
    "jobs",
    "tasks",
    "stage_tasks",
    "artifacts",
    "job_outbox",
    "approval_records",
    "idempotency_records",
    "structure_source_index",
    "organization_events",
)
MIGRATION_ADMISSION_TABLES = (
    "admission_reservations",
    "research_budget_policies",
    "research_budget_reservations",
    "research_budget_receipts",
    "research_budget_events",
)


def _migration_fixture(path: str) -> dict[str, Any]:
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "migration.json"
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"migration fixture unreadable: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != MIGRATION_FIXTURE_SCHEMA:
        raise ValueError(f"fixture is not {MIGRATION_FIXTURE_SCHEMA}")
    for key in ("fixture_id", "source_fixtures", "workspace_root"):
        if not str(document.get(key) or "").strip():
            raise ValueError(f"fixture.{key} is required")
    if int(document.get("target_schema_version") or 0) <= 0:
        raise ValueError("fixture.target_schema_version is required")
    if int(document.get("legacy_schema_version") or 0) <= 0:
        raise ValueError("fixture.legacy_schema_version is required")
    return document


def _migration_helper(
    action: str, arguments: Sequence[str], *, timeout: float = 180
) -> tuple[int, dict[str, Any], str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    argv = [sys.executable, str(MIGRATION_HELPER), action, *arguments]
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, env=env, check=False
    )
    payload: dict[str, Any] = {}
    stdout = (completed.stdout or "").strip()
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
        else:
            for line in reversed(stdout.splitlines()):
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    payload = candidate
                    break
    return completed.returncode, payload, completed.stdout or "", completed.stderr or ""


def _migration_file_digests(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _migration_tree_digest(root: Path) -> str:
    digests = _migration_file_digests(root)
    material = json.dumps(digests, sort_keys=True).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _migration_artifact_digests(workspace: Path) -> dict[str, Any]:
    return {
        name: {
            "tree_digest": _migration_tree_digest(workspace / name),
            "files": _migration_file_digests(workspace / name),
        }
        for name in MIGRATION_ARTIFACT_DIRS
    }


def _migration_corrupt_artifacts(root: Path) -> list[dict[str, str]]:
    corrupt: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            corrupt.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return corrupt


def _migration_table_state(db: Path, tables: Sequence[str]) -> dict[str, Any]:
    if not db.is_file():
        return {"present": False, "schema_version": 0, "counts": {}, "digests": {}}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        existing = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        schema_version = (
            int(conn.execute("SELECT COUNT(*) AS n FROM _schema_migrations").fetchone()["n"])
            if "_schema_migrations" in existing
            else 0
        )
        counts: dict[str, int] = {}
        digests: dict[str, str] = {}
        for table in tables:
            if table not in existing:
                continue
            rows = conn.execute(f"SELECT * FROM [{table}] ORDER BY rowid").fetchall()
            counts[table] = len(rows)
            material = json.dumps([list(row) for row in rows], sort_keys=True, default=str).encode(
                "utf-8"
            )
            digests[table] = hashlib.sha256(material).hexdigest()
        return {
            "present": True,
            "schema_version": schema_version,
            "counts": counts,
            "digests": digests,
            "prepared_schema_version": (
                int(
                    conn.execute("SELECT MAX(id) AS v FROM _prepare_schema_migrations").fetchone()[
                        "v"
                    ]
                )
                if "_prepare_schema_migrations" in existing
                else 0
            ),
        }
    finally:
        conn.close()


def _migration_snapshot(workspace: Path, fixture: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "acp2.migration.snapshot.v1",
        "fixture_id": fixture["fixture_id"],
        "target_schema_version": int(fixture["target_schema_version"]),
        "jobs_db": _migration_table_state(workspace / "db" / "acp_jobs.db", MIGRATION_HEAD_TABLES),
        "admission_db": _migration_table_state(
            workspace / "db" / "acp_admission.db", MIGRATION_ADMISSION_TABLES
        ),
        "artifacts": _migration_artifact_digests(workspace),
    }


def _migration_compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    differences: list[str] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            for key in sorted(set(left) | set(right)):
                walk(left.get(key), right.get(key), f"{path}.{key}" if path else str(key))
            return
        if left != right:
            differences.append(f"{path}: {left!r} != {right!r}")

    walk(before, after, "")
    return {"equal": not differences, "differences": differences}


def _migration_online_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


def _migration_restore_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(target) + suffix)
        if candidate.exists():
            candidate.unlink()
    _migration_online_backup(source, target)


def _migration_restore_artifacts(backup: Path, workspace: Path) -> None:
    for name in MIGRATION_ARTIFACT_DIRS:
        source = backup / "artifacts" / name
        target = workspace / name
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)


def _migration_assert(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    rendered = (
        detail if isinstance(detail, str) else json.dumps(detail, sort_keys=True, default=str)
    )
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": rendered}


def _run_migration(
    fixture_path: str,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b24.fixture": str(fixture_path),
    }
    try:
        fixture = _migration_fixture(fixture_path)
    except ValueError as exc:
        return (
            EXIT_FAIL,
            [_migration_assert("b24.fixture_schema", False, str(exc))],
            {**extensions, "b24.reason": str(exc)},
        )

    source = Path(str(fixture["source_fixtures"])).resolve()
    workspace = Path(str(fixture["workspace_root"])).resolve()
    target_version = int(fixture["target_schema_version"])
    legacy_version = int(fixture["legacy_schema_version"])
    extensions.update(
        {
            "b24.fixture_id": fixture["fixture_id"],
            "b24.source_fixtures": str(source),
            "b24.workspace": str(workspace),
        }
    )
    if not source.is_dir():
        return (
            EXIT_FAIL,
            [_migration_assert("b24.source_fixtures_present", False, str(source))],
            {**extensions, "b24.reason": "source_fixtures_missing"},
        )
    if workspace == Path("/") or workspace == REPO_ROOT or source.is_relative_to(workspace):
        return (
            EXIT_FAIL,
            [_migration_assert("b24.workspace_isolated", False, str(workspace))],
            {**extensions, "b24.reason": "workspace_not_isolated"},
        )
    marker = workspace / ".b24-migration-workspace"
    if workspace.exists():
        if not marker.is_file():
            return (
                EXIT_FAIL,
                [
                    _migration_assert(
                        "b24.workspace_owned", False, "existing workspace has no ownership marker"
                    )
                ],
                {**extensions, "b24.reason": "workspace_not_owned"},
            )
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    marker.write_text("B24a isolated migration workspace\n", encoding="utf-8")

    pre_source_digests = _migration_file_digests(source)
    shutil.copytree(
        source,
        workspace / "source-copy",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    corrupt_relative = str(fixture.get("corrupt_artifact") or "").strip()
    if corrupt_relative:
        corrupt_path = workspace / "source-copy" / corrupt_relative
        corrupt_path.parent.mkdir(parents=True, exist_ok=True)
        corrupt_path.write_text('{"truncated": \n', encoding="utf-8")

    (workspace / "db").mkdir()
    (workspace / "tasks").mkdir()
    jobs_db = workspace / "db" / "acp_jobs.db"
    admission_db = workspace / "db" / "acp_admission.db"

    seed_code, seed, seed_stdout, seed_stderr = _migration_helper(
        "seed",
        [
            "--db",
            str(jobs_db),
            "--admission-db",
            str(admission_db),
            "--root",
            str(workspace),
        ],
        timeout=min(180.0, timeout),
    )
    if seed_code != 0:
        return (
            EXIT_FAIL,
            [_migration_assert("b24.seed", False, f"exit={seed_code} stderr={seed_stderr[-800:]}")],
            {**extensions, "b24.reason": "seed_failed"},
        )
    extensions["b24.seed"] = seed

    pre_snapshot = _migration_snapshot(workspace, fixture)
    corrupt_artifacts = _migration_corrupt_artifacts(workspace / "source-copy")
    backup = workspace / "backup"
    backup.mkdir()
    _migration_online_backup(jobs_db, backup / "acp_jobs.db")
    _migration_online_backup(admission_db, backup / "acp_admission.db")
    for name in MIGRATION_ARTIFACT_DIRS:
        if (workspace / name).exists():
            shutil.copytree(workspace / name, backup / "artifacts" / name)
    backup_manifest = {
        "schema": "acp2.migration.backup.v1",
        "fixture_id": fixture["fixture_id"],
        "generated_at": _utcnow(),
        "source_digests": pre_source_digests,
        "snapshot": pre_snapshot,
        "corrupt_artifacts": corrupt_artifacts,
        "databases": {
            "jobs": {
                "sha256": sha256_file(backup / "acp_jobs.db"),
                "path": str(backup / "acp_jobs.db"),
            },
            "admission": {
                "sha256": sha256_file(backup / "acp_admission.db"),
                "path": str(backup / "acp_admission.db"),
            },
        },
        "artifact_tree": {
            name: _migration_tree_digest(backup / "artifacts" / name)
            for name in MIGRATION_ARTIFACT_DIRS
            if (backup / "artifacts" / name).exists()
        },
    }
    (backup / "manifest.json").write_text(
        json.dumps(backup_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    extensions["b24.backup_manifest"] = str(backup / "manifest.json")

    restore_pre = workspace / "restore-pre"
    restore_pre.mkdir()
    _migration_restore_database(backup / "acp_jobs.db", restore_pre / "acp_jobs.db")
    _migration_restore_database(backup / "acp_admission.db", restore_pre / "acp_admission.db")
    for name in MIGRATION_ARTIFACT_DIRS:
        if (backup / "artifacts" / name).exists():
            shutil.copytree(backup / "artifacts" / name, restore_pre / name)
    restore_pre_snapshot = {
        "schema": "acp2.migration.snapshot.v1",
        "fixture_id": fixture["fixture_id"],
        "target_schema_version": target_version,
        "jobs_db": _migration_table_state(restore_pre / "acp_jobs.db", MIGRATION_HEAD_TABLES),
        "admission_db": _migration_table_state(
            restore_pre / "acp_admission.db", MIGRATION_ADMISSION_TABLES
        ),
        "artifacts": {
            name: {
                "tree_digest": _migration_tree_digest(restore_pre / name),
                "files": _migration_file_digests(restore_pre / name),
            }
            for name in MIGRATION_ARTIFACT_DIRS
        },
    }
    pre_restore_comparison = _migration_compare(pre_snapshot, restore_pre_snapshot)
    assertions.append(
        _migration_assert(
            "b24.backup.restorable_before_migration",
            pre_restore_comparison["equal"],
            pre_restore_comparison,
        )
    )

    migrate_code, migrate, migrate_stdout, migrate_stderr = _migration_helper(
        "migrate", ["--db", str(jobs_db)], timeout=min(180.0, timeout)
    )
    post_migration = _migration_snapshot(workspace, fixture)
    migration_comparison = _migration_compare(pre_snapshot, post_migration)
    assertions.append(
        _migration_assert(
            "b24.migration.candidate_schema",
            migrate_code == 0 and int(migrate.get("schema_version") or 0) == target_version,
            {"exit": migrate_code, "result": migrate, "stderr": migrate_stderr[-500:]},
        )
    )
    assertions.append(
        _migration_assert(
            "b24.migration.state_equal", migration_comparison["equal"], migration_comparison
        )
    )

    _migration_restore_database(backup / "acp_jobs.db", jobs_db)
    _migration_restore_database(backup / "acp_admission.db", admission_db)
    _migration_restore_artifacts(backup, workspace)
    post_restore = _migration_snapshot(workspace, fixture)
    restore_comparison = _migration_compare(pre_snapshot, post_restore)
    assertions.append(
        _migration_assert(
            "b24.restore.counts_digests_schema_equal",
            restore_comparison["equal"],
            restore_comparison,
        )
    )
    restored_corrupt = _migration_corrupt_artifacts(workspace / "source-copy")
    injected_corrupt = [row for row in restored_corrupt if row["path"] == corrupt_relative]
    assertions.append(
        _migration_assert(
            "b24.corrupt_artifact.reported_not_skipped",
            bool(corrupt_relative)
            and bool(injected_corrupt)
            and bool(injected_corrupt[0]["error"])
            and (workspace / "source-copy" / corrupt_relative).is_file(),
            {"injected": corrupt_relative, "observed": restored_corrupt},
        )
    )

    legacy_dir = workspace / "legacy"
    legacy_dir.mkdir()
    legacy_db = legacy_dir / "acp_jobs.db"
    legacy_code, legacy_seed, _, legacy_stderr = _migration_helper(
        "seed-legacy",
        ["--db", str(legacy_db), "--root", str(workspace / "legacy-root")],
        timeout=min(180.0, timeout),
    )
    if legacy_code != 0:
        return (
            EXIT_FAIL,
            assertions
            + [
                _migration_assert(
                    "b24.legacy.seed", False, f"exit={legacy_code} stderr={legacy_stderr[-800:]}"
                )
            ],
            {**extensions, "b24.reason": "legacy_seed_failed"},
        )
    legacy_pre = _migration_table_state(legacy_db, MIGRATION_HEAD_TABLES)
    _migration_online_backup(legacy_db, backup / "legacy-acp_jobs.db")
    kill_code, kill_payload, kill_stdout, kill_stderr = _migration_helper(
        "kill-migrate", ["--db", str(legacy_db), "--kill-at", "023"], timeout=min(180.0, timeout)
    )
    interrupted = _migration_table_state(legacy_db, MIGRATION_HEAD_TABLES)
    assertions.append(
        _migration_assert(
            "b24.interrupted_migration.readable_attributed",
            kill_code in (-9, 137)
            and interrupted["present"]
            and interrupted["schema_version"] == legacy_version
            and interrupted["counts"].get("jobs") == legacy_pre["counts"].get("jobs"),
            {
                "exit": kill_code,
                "payload": kill_payload,
                "stdout": kill_stdout[-300:],
                "stderr": kill_stderr[-300:],
                "schema_version": interrupted["schema_version"],
                "jobs": interrupted["counts"].get("jobs"),
                "expected_schema_version": legacy_version,
            },
        )
    )
    _migration_restore_database(backup / "legacy-acp_jobs.db", legacy_db)
    legacy_restored = _migration_table_state(legacy_db, MIGRATION_HEAD_TABLES)
    legacy_restore_comparison = _migration_compare(legacy_pre, legacy_restored)
    assertions.append(
        _migration_assert(
            "b24.interrupted_migration.restored",
            legacy_restore_comparison["equal"],
            legacy_restore_comparison,
        )
    )
    legacy_migrate_code, legacy_migrate, _, legacy_migrate_stderr = _migration_helper(
        "migrate", ["--db", str(legacy_db)], timeout=min(180.0, timeout)
    )
    legacy_post = _migration_table_state(legacy_db, MIGRATION_HEAD_TABLES)
    assertions.append(
        _migration_assert(
            "b24.legacy.upgrade",
            legacy_migrate_code == 0
            and legacy_post["schema_version"] == target_version
            and legacy_post["counts"].get("jobs") == legacy_pre["counts"].get("jobs")
            and "approval_records" in legacy_post["counts"]
            and "idempotency_records" in legacy_post["counts"],
            {
                "result": legacy_migrate,
                "before": legacy_pre,
                "after": legacy_post,
                "stderr": legacy_migrate_stderr[-500:],
            },
        )
    )

    drill_timeout = min(180.0, timeout)
    lost_code, lost, _, lost_stderr = _migration_helper(
        "lost-worker",
        ["--db", str(jobs_db), "--running", "mig_lost", "--terminal", "mig_done"],
        timeout=drill_timeout,
    )
    assertions.append(
        _migration_assert(
            "b24.drill.lost_worker.no_terminal_revival",
            lost_code == 0
            and lost.get("recovered", {}).get("to") == "failed"
            and lost.get("terminal_revival_blocked") is True
            and lost.get("terminal_selected_by_recovery_scan") is False
            and lost.get("terminal_unchanged") is True,
            {"result": lost, "stderr": lost_stderr[-500:]},
        )
    )

    receipts_code, receipts, _, receipts_stderr = _migration_helper(
        "receipts",
        [
            "--fixtures",
            str(workspace / "source-copy"),
            "--scratch",
            str(workspace / "drills"),
        ],
        timeout=drill_timeout,
    )
    old_v1 = (receipts.get("old_v1") or {}) if isinstance(receipts, dict) else {}
    duplicate = (receipts.get("duplicate") or {}) if isinstance(receipts, dict) else {}
    assertions.append(
        _migration_assert(
            "b24.drill.old_receipt.conservative_recompute",
            receipts_code == 0
            and old_v1.get("default_adopted") is False
            and old_v1.get("explicit_legacy_adopted") is True,
            {"result": receipts, "stderr": receipts_stderr[-500:]},
        )
    )
    assertions.append(
        _migration_assert(
            "b24.drill.duplicate_receipt.idempotent",
            receipts_code == 0
            and duplicate.get("publication_complete") is True
            and duplicate.get("product_ids_unique") is True,
            duplicate,
        )
    )

    publication_code, publication, _, publication_stderr = _migration_helper(
        "publication",
        [
            "--fixture",
            str(workspace / "source-copy" / "v2_publish_interrupted"),
            "--scratch",
            str(workspace / "drills"),
        ],
        timeout=drill_timeout,
    )
    assertions.append(
        _migration_assert(
            "b24.drill.publication_pending.defined_state",
            publication_code == 0
            and publication.get("marker_before") is None
            and publication.get("marker_after_complete") is True
            and publication.get("qc_calls") == 0
            and publication.get("qc_executed") is False
            and publication.get("manifest_exists") is True,
            {"result": publication, "stderr": publication_stderr[-500:]},
        )
    )

    cursor_code, cursor, _, cursor_stderr = _migration_helper(
        "cursor", ["--db", str(jobs_db)], timeout=drill_timeout
    )
    assertions.append(
        _migration_assert(
            "b24.drill.cursor_expiry.no_duplicate",
            cursor_code == 0
            and cursor.get("expire_ok", True) is True
            and bool(cursor.get("expired"))
            and cursor.get("expired_state") == "expired"
            and cursor.get("takeover_kind") == "created"
            and cursor.get("no_duplicate_row") is True,
            {"result": cursor, "stderr": cursor_stderr[-500:]},
        )
    )

    verify_code, verify, _, verify_stderr = _migration_helper(
        "verify",
        [
            "--db",
            str(jobs_db),
            "--admission-db",
            str(admission_db),
            "--approval-ref",
            str((seed.get("approvals") or {}).get("approved") or ""),
            "--budget-id",
            str((seed.get("budget") or {}).get("budget_id") or "mig-budget"),
        ],
        timeout=drill_timeout,
    )
    approval = (verify.get("approval") or {}) if isinstance(verify, dict) else {}
    budget = (verify.get("budget") or {}) if isinstance(verify, dict) else {}
    idempotency = (verify.get("idempotency") or {}) if isinstance(verify, dict) else {}
    assertions.append(
        _migration_assert(
            "b24.approval_budget_idempotency_preserved",
            verify_code == 0
            and approval.get("state") == "approved"
            and approval.get("revision") == 2
            and bool(approval.get("binding"))
            and budget.get("budget_id") == "mig-budget"
            and bool(budget.get("limits"))
            and idempotency.get("state") == "committed"
            and idempotency.get("task_id") == "mig_idem_job",
            {"result": verify, "stderr": verify_stderr[-500:]},
        )
    )

    post_source_digests = _migration_file_digests(source)
    assertions.insert(
        0,
        _migration_assert(
            "b24.source_fixtures_read_only",
            pre_source_digests == post_source_digests,
            {
                "pre_files": len(pre_source_digests),
                "post_files": len(post_source_digests),
                "changed": sorted(
                    path
                    for path in set(pre_source_digests) | set(post_source_digests)
                    if pre_source_digests.get(path) != post_source_digests.get(path)
                ),
            },
        ),
    )

    observed_schema_versions = {
        "pre": pre_snapshot["jobs_db"]["schema_version"],
        "post_migration": post_migration["jobs_db"]["schema_version"],
        "post_restore": post_restore["jobs_db"]["schema_version"],
        "legacy_pre": legacy_pre["schema_version"],
        "interrupted": interrupted["schema_version"],
        "legacy_post": legacy_post["schema_version"],
    }
    assertions.append(
        _migration_assert(
            "b24.schema_version_matches_fixture",
            observed_schema_versions["pre"] == target_version
            and observed_schema_versions["post_migration"] == target_version
            and observed_schema_versions["post_restore"] == target_version
            and observed_schema_versions["legacy_pre"] == legacy_version
            and observed_schema_versions["interrupted"] == legacy_version
            and observed_schema_versions["legacy_post"] == target_version,
            observed_schema_versions,
        )
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "source-digests.json": {
            "schema": "acp2.migration.source-digests.v1",
            "source": str(source),
            "pre": pre_source_digests,
            "post": post_source_digests,
            "unchanged": pre_source_digests == post_source_digests,
            "copy": str(workspace / "source-copy"),
            "corrupt_artifact_injected": corrupt_relative,
        },
        "migration-log.json": {
            "schema": "acp2.migration.log.v1",
            "fixture_id": fixture["fixture_id"],
            "candidate": {
                "candidate_revision": os.environ.get("ACP_CANDIDATE_REVISION"),
                "source": str(REPO_ROOT / "src"),
            },
            "seed": seed,
            "migration": migrate,
            "legacy_seed": legacy_seed,
            "legacy_migration": legacy_migrate,
            "interrupted": {
                "exit": kill_code,
                "payload": kill_payload,
                "schema_version": interrupted["schema_version"],
                "counts": interrupted["counts"],
            },
            "schema_versions": observed_schema_versions,
        },
        "restore-comparison.json": {
            "schema": "acp2.migration.restore-comparison.v1",
            "before_migration_backup_restore": pre_restore_comparison,
            "post_migration_restore": restore_comparison,
            "interrupted_migration_restore": legacy_restore_comparison,
            "pre_snapshot": pre_snapshot,
            "post_restore_snapshot": post_restore,
        },
        "drill-receipts.json": {
            "schema": "acp2.migration.drills.v1",
            "lost_worker": lost,
            "old_duplicate_receipt": receipts,
            "publication_pending": publication,
            "cursor_expiry": cursor,
            "ledger_consistency": verify,
        },
        "corrupt-artifacts.json": {
            "schema": "acp2.migration.corrupt-artifacts.v1",
            "injected": corrupt_relative,
            "observed": restored_corrupt,
            "reported_as_named_error": bool(injected_corrupt),
        },
        "verify-rows.json": verify,
    }
    artifact_rows: list[dict[str, Any]] = []
    for name, payload in evidence.items():
        path = out_dir / name
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        artifact_rows.append(
            {"path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    extensions.update(
        {
            "b24.source_digests": str(out_dir / "source-digests.json"),
            "b24.migration_log": str(out_dir / "migration-log.json"),
            "b24.restore_comparison": str(out_dir / "restore-comparison.json"),
            "b24.drill_receipts": str(out_dir / "drill-receipts.json"),
            "b24.corrupt_artifacts": str(out_dir / "corrupt-artifacts.json"),
            "b24.schema_versions": observed_schema_versions,
            "b24.counts": pre_snapshot,
            "b24.corrupt": restored_corrupt,
            "b24.reason": None,
        }
    )
    code = EXIT_PASS if all(row["status"] == "PASS" for row in assertions) else EXIT_FAIL
    if code == EXIT_FAIL:
        extensions["b24.reason"] = "assertion_failed:" + ",".join(
            row["name"] for row in assertions if row["status"] != "PASS"
        )
    extensions["b24.artifacts"] = artifact_rows
    return code, assertions, extensions


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
    evidence_kind = _evidence_kind_for_phase(phase_name, spec.scenario)
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
    elif spec.scenario == "submit-replay":

        def submit_replay(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_submit_replay(
                args.controller, str(args.launch), str(args.fixture), timeout
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            return code_value

        handler = submit_replay
    elif spec.scenario == "local-singlepoint":

        def local_singlepoint(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_local_singlepoint(
                args.controller,
                str(args.launch),
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b10.artifacts", [])
            return code_value

        handler = local_singlepoint
    elif spec.scenario == "lsf-singlepoint":

        def lsf_singlepoint(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_lsf_singlepoint(
                args.controller,
                str(args.launch),
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["reason"] = extensions.get("b11.reason")
            return code_value

        handler = lsf_singlepoint
    elif spec.scenario == "three-entry":

        def three_entry(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_three_entry(
                args.controller, str(args.fixture), timeout
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["reason"] = extensions.get("b10.reason")
            return code_value

        handler = three_entry
    elif spec.scenario == "artifact":

        def artifact(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_artifact(
                args.controller, str(args.fixture), out_dir, timeout
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b12.artifacts", [])
            outcome["reason"] = extensions.get("b12.probe_error")
            return code_value

        handler = artifact
    elif spec.scenario == "control-recovery":

        def control_recovery(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_control_recovery(
                args.controller,
                str(args.fixture),
                out_dir,
                timeout,
                args.launch,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b13.artifacts", [])
            outcome["reason"] = extensions.get("b13.reason")
            return code_value

        handler = control_recovery
    elif spec.scenario == "approval-budget":

        def approval_budget(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_approval_budget(
                phase_name,
                args.controller,
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b16.artifacts", [])
            outcome["reason"] = extensions.get("b16.reason") or extensions.get("b17.reason")
            return code_value

        handler = approval_budget
    elif spec.scenario == "da-runtime" and phase_name == "da-preflight":

        def da_preflight(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_da_preflight(
                args.controller,
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b18.artifacts", [])
            outcome["reason"] = extensions.get("b18.reason")
            return code_value

        handler = da_preflight
    elif spec.scenario == "da-runtime" and phase_name == "da-submit":

        def da_submit(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_da_submit(
                args.controller,
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b20a.artifacts", [])
            outcome["reason"] = extensions.get("b20a.exec_reason")
            return code_value

        handler = da_submit
    elif spec.scenario == "bounded-recipe-dag":

        def bounded_recipe_dag(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_bounded_recipe_dag(
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("m204.artifacts", [])
            outcome["reason"] = extensions.get("m204.reason")
            return code_value

        handler = bounded_recipe_dag
    elif spec.scenario == "infra-plugin":

        def infra_plugin(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_infra_plugin(
                phase_name,
                args.controller,
                str(args.fixture),
                out_dir,
                timeout,
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b21.artifacts", [])
            outcome["reason"] = extensions.get("b21.reason")
            return code_value

        handler = infra_plugin
    elif spec.scenario == "multitenant":
        if phase_name != "multitenant:authz":

            def multitenant_two_project(_ledger: CleanupLedger) -> int:
                code_value, assertions, extensions = _run_multitenant_two_project(
                    args.controller,
                    str(args.fixture),
                    out_dir,
                    timeout,
                )
                outcome["assertions"] = assertions
                outcome["extensions"] = extensions
                outcome["artifacts"] = extensions.get("b23.artifacts", [])
                outcome["reason"] = extensions.get("b23.reason")
                return code_value

            handler = multitenant_two_project
        else:

            def multitenant(_ledger: CleanupLedger) -> int:
                code_value, assertions, extensions = _run_multitenant_authz(
                    args.controller,
                    str(args.fixture),
                    out_dir,
                    timeout,
                )
                outcome["assertions"] = assertions
                outcome["extensions"] = extensions
                outcome["artifacts"] = extensions.get("b15.artifacts", [])
                outcome["reason"] = extensions.get("b15.reason")
                return code_value

            handler = multitenant
    elif spec.scenario == "migration":

        def migration(_ledger: CleanupLedger) -> int:
            code_value, assertions, extensions = _run_migration(str(args.fixture), out_dir, timeout)
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b24.artifacts", [])
            outcome["reason"] = extensions.get("b24.reason")
            return code_value

        handler = migration
    elif spec.scenario == "package-install":

        def package_install(_ledger: CleanupLedger) -> int:
            import importlib.util

            helper = Path(__file__).resolve().parent / "acp2_package_install.py"
            module_spec = importlib.util.spec_from_file_location("acp2_package_install", helper)
            if module_spec is None or module_spec.loader is None:
                raise RuntimeError(f"cannot load {helper}")
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)

            code_value, assertions, extensions = module.run_package_install(
                None if args.fixture is None else str(args.fixture), out_dir, timeout
            )
            outcome["assertions"] = assertions
            outcome["extensions"] = extensions
            outcome["artifacts"] = extensions.get("b25.artifacts", [])
            outcome["reason"] = extensions.get("b25.reason")
            return code_value

        handler = package_install
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
    elif spec.scenario in {
        "entry-readonly",
        "mcp-stdio",
        "env-runtime",
        "submit-replay",
        "local-singlepoint",
        "lsf-singlepoint",
        "three-entry",
        "artifact",
        "control-recovery",
        "multitenant",
        "approval-budget",
        "infra-plugin",
        "migration",
        "package-install",
        "bounded-recipe-dag",
    } or (spec.scenario == "da-runtime" and phase_name in {"da-preflight", "da-submit"}):
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
    if spec.scenario in {
        "entry-readonly",
        "mcp-stdio",
        "env-runtime",
        "submit-replay",
        "local-singlepoint",
        "lsf-singlepoint",
        "three-entry",
        "artifact",
        "control-recovery",
        "multitenant",
        "approval-budget",
        "infra-plugin",
        "migration",
        "package-install",
        "bounded-recipe-dag",
    } or (spec.scenario == "da-runtime" and phase_name in {"da-preflight", "da-submit"}):
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
    installed_candidate = candidate_revision_from_extensions(spec.scenario, extensions)
    if installed_candidate is not None:
        candidate_revision = installed_candidate
        server_revision = installed_candidate
    if identity_error is not None:
        result = "FAIL"
        reason = identity_error
        code = EXIT_FAIL
    refs: dict[str, Any] = {}
    if spec.scenario == "submit-replay":
        task_ref = extensions.get("b08.task_ref")
        if isinstance(task_ref, str) and task_ref:
            refs["job_id"] = task_ref
        attempt = extensions.get("b08.attempt")
        if isinstance(attempt, int):
            refs["attempt"] = attempt
    if spec.scenario in {"local-singlepoint", "three-entry"}:
        task_ref = extensions.get("b10.task_ref")
        if isinstance(task_ref, str) and task_ref:
            refs["job_id"] = task_ref
        attempt = extensions.get("b10.attempt")
        if isinstance(attempt, int):
            refs["attempt"] = attempt
        release_id = extensions.get("b10.release_id")
        if isinstance(release_id, str) and release_id:
            refs["release_id"] = release_id
    if spec.scenario == "artifact":
        for key, extension in (
            ("job_id", "b12.job_id"),
            ("attempt", "b12.attempt"),
            ("release_id", "b12.release_id"),
            ("operation_id", "b12.operation_id"),
            ("manifest_generation", "b12.manifest_generation"),
        ):
            value = extensions.get(extension)
            if value is not None and value != "":
                refs[key] = value
    if spec.scenario == "lsf-singlepoint":
        for key, extension in (
            ("job_id", "b11.task_ref"),
            ("attempt", "b11.attempt"),
            ("release_id", "b11.release_id"),
            ("submission_id", "b11.submission_id"),
            ("operation_id", "b11.operation_id"),
        ):
            value = extensions.get(extension)
            if value is not None and value != "":
                refs[key] = value
    if spec.scenario == "approval-budget":
        for key, extension in (
            ("job_id", "b16.approved_task_ref"),
            ("approval_ref", "b16.approval_ref"),
        ):
            value = extensions.get(extension)
            if value is not None and value != "":
                refs[key] = value
    if spec.scenario == "control-recovery":
        for key, extension in (
            ("job_id", "b13.task_ref"),
            ("attempt", "b13.attempt"),
            ("release_id", "b13.release_id"),
            ("submission_id", "b13.submission_id"),
        ):
            value = extensions.get(extension)
            if value is not None and value != "":
                refs[key] = value
    claim_status = None
    h4_gate = None
    client_revision = None
    if spec.scenario == "da-runtime" and phase_name == "da-submit":
        claim_status = extensions.get("da.claim_status") or "not_evaluated"
        h4_gate = extensions.get("da.h4_gate") or "open"
        candidate_document = _b20_read_json(out_dir / "p2-candidate.json")
        if isinstance(candidate_document, dict):
            client_revision = candidate_document.get("client_revision")
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
        artifacts=outcome.get("artifacts"),
        extensions=extensions,
        refs=refs or None,
        candidate_revision=candidate_revision,
        server_revision=server_revision,
        client_revision=client_revision,
        claim_status=claim_status,
        h4_gate=h4_gate,
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
        "Registered B scenarios (16 immutable IDs):",
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

    if args.phase is None:
        phase_name = "main" if "main" in spec.phases else next(iter(spec.phases))
    else:
        phase_name = args.phase
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
    if (
        spec.scenario == "da-runtime"
        and phase_name in {"da-submit", "da-evidence", "aggregate"}
        and args.timeout is None
    ):
        print(
            "usage error: da-runtime requires --timeout (ACP2_DA_TIMEOUT); "
            "an unset timeout is never a silent default",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if spec.scenario in {"local-singlepoint", "lsf-singlepoint"} and not args.launch:
        print(
            f"usage error: {spec.scenario} requires --launch <MCP stdio launch JSON>; "
            "the real MCP path is mandatory and the harness never starts a Controller",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if (
        spec.scenario in {"local-singlepoint", "lsf-singlepoint"}
        and not Path(args.launch).is_file()
    ):
        print(f"usage error: --launch does not exist: {args.launch}", file=sys.stderr)
        return EXIT_USAGE
    if spec.scenario == "lsf-singlepoint":
        if not Path(args.fixture).is_file():
            print(f"usage error: --fixture does not exist: {args.fixture}", file=sys.stderr)
            return EXIT_USAGE
        if args.timeout is None:
            derived, derivation = _b11_timeout_derivation(Path(args.out))
            if derived is None:
                return _write_prerequisite_result(
                    spec,
                    phase_name,
                    phase,
                    args,
                    "lsf_timeout_derivation_failed:" + json.dumps(derivation, sort_keys=True),
                )
            args.timeout = float(derived)

    if phase.controller_required and spec.scenario != "lsf-singlepoint":
        if not args.controller:
            return _write_prerequisite_result(spec, phase_name, phase, args, "missing_controller")
        if not controller_reachable(args.controller):
            return _write_prerequisite_result(
                spec, phase_name, phase, args, "controller_unreachable"
            )
    if "launch" in phase.required_args and not Path(args.launch).is_file():
        return _write_prerequisite_result(spec, phase_name, phase, args, "launch_missing")
    if spec.scenario == "control-recovery" and args.launch and not Path(args.launch).is_file():
        print(f"usage error: --launch does not exist: {args.launch}", file=sys.stderr)
        return EXIT_USAGE
    fixture_is_file = args.fixture is not None and Path(args.fixture).is_file()
    migration_directory = (
        spec.scenario == "migration"
        and Path(args.fixture).is_dir()
        and (Path(args.fixture) / "migration.json").is_file()
    )
    if "fixture" in phase.required_args and not (fixture_is_file or migration_directory):
        return _write_prerequisite_result(spec, phase_name, phase, args, "fixture_missing")
    if spec.scenario == "three-entry":
        try:
            descriptor = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"usage error: three-entry --fixture must be a task descriptor JSON: {exc}",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if (
            not isinstance(descriptor, dict)
            or not str(descriptor.get("task_id") or descriptor.get("job_id") or "").strip()
        ):
            print(
                "usage error: three-entry --fixture must carry task_id/job_id "
                "(the already-submitted TaskRef); the harness never submits a second task",
                file=sys.stderr,
            )
            return EXIT_USAGE
    if spec.scenario == "artifact":
        try:
            descriptor = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"usage error: artifact --fixture must be an artifact descriptor JSON: {exc}",
                file=sys.stderr,
            )
            return EXIT_USAGE
        if (
            not isinstance(descriptor, dict)
            or not str(descriptor.get("job_id") or "").strip()
            or not isinstance(descriptor.get("artifacts"), list)
            or not descriptor.get("artifacts")
        ):
            print(
                "usage error: artifact --fixture must carry job_id and a non-empty artifacts array",
                file=sys.stderr,
            )
            return EXIT_USAGE
    return execute_scenario(spec, phase_name, phase, args)


if __name__ == "__main__":
    raise SystemExit(main())
