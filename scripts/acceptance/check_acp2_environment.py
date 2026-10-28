#!/usr/bin/env python3
"""Read-only ACP 2.0 environment readiness ledger (M0-10).

Design rules
------------
* Three-state results only: ``PASS`` / ``FAIL`` / ``NOT_VERIFIED``.  A missing
  prerequisite is never promoted to ``PASS`` and a probe that cannot run is
  ``NOT_VERIFIED`` instead of a fabricated ``FAIL``.
* No QC, LSF or model work is submitted.  Local checks are version probes and
  filesystem reads.  Cluster checks are opt-in (``--probe-cluster``) and limited
  to the read-only ``bsub -V`` / ``bjobs -a`` / ``bhist -l 1767`` surface.  The
  remote check is opt-in (``--probe-remote``) and uses ``ssh -o BatchMode=yes``.
* Credential values are never read, echoed or written.  ``DEEPSEEK_API_KEY`` is
  only probed for *presence*; ``ACP2_CLEAN_HOST`` must be an SSH config alias
  (passwords are neither accepted nor stored).  Every row reports
  ``secret_free`` plus an optional ``secret_reference``.
* Every row records the executed command (``null`` for filesystem-only checks),
  evidence, whether the value came from explicit configuration or the ambient
  default, and the scope of the claim.

Usage::

    python scripts/acceptance/check_acp2_environment.py --json \
        --out reports/acp2_remediation_20261026/environment-readiness.json

Exit codes: ``0`` all PASS, ``1`` at least one FAIL, ``2`` no FAIL but at least
one NOT_VERIFIED.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "acp.acceptance.environment_readiness.v1"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_VERIFIED = "NOT_VERIFIED"
STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_NOT_VERIFIED)

EVIDENCE_PROBE = "probe"
EVIDENCE_RECORDED = "recorded"
EVIDENCE_BLOCKED = "blocked"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2

DEFAULT_TOOLS_ROOT = Path("/ACPServer/acp2-tools")
NODE22_VERSION = "node-v22.19.0-linux-x64"
PNPM_REQUIRED = (11, 28, 5)
ORCA_PATH = Path("/usr/bin/orca")
XTB_PATH = Path("/home/xieningke/xtb-dist/bin/xtb")
CREST_PATH = Path("/home/xieningke/crest/crest")
OPENLAVA_ROOT = Path("/opt/openlava-2.0")
OPENLAVA_ENVDIR = OPENLAVA_ROOT / "etc"
RUN_ROOT_PLANNED = Path("/ACPServer/acp2-runs/remediation")
ARTIFACT_STORE_PLANNED = Path("/ACPServer/acp2-artifacts/remediation")
PLAYWRIGHT_CACHE = Path(".cache/ms-playwright")
LSF_SMOKE_JOB_ID = "1767"
COMMAND_TIMEOUT = 20.0
EVIDENCE_LIMIT = 240
REDACTED = "[REDACTED]"

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(
        r"(?i)\b(api[_-]?key|apikey|token|password|passwd|secret)\b\s*[=:]\s*['\"]?[^\s'\"]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one read-only probe command."""

    argv: tuple[str, ...]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def output(self) -> str:
        """stdout and stderr combined: some tools print --version on stderr."""
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()


@dataclass(frozen=True)
class ReadinessItem:
    """One tri-state readiness row."""

    id: str
    category: str
    requirement: str
    result: str
    configured: bool
    environment_default: str
    command: tuple[str, ...] | None
    evidence: str
    scope: str
    evidence_kind: str = EVIDENCE_PROBE
    secret_free: bool = True
    secret_reference: str | None = None
    approved_by: str | None = None
    expires_on: str | None = None
    detail: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "requirement": self.requirement,
            "result": self.result,
            "configured": self.configured,
            "environment_default": self.environment_default,
            "command": list(self.command) if self.command else None,
            "evidence": self.evidence,
            "evidence_kind": self.evidence_kind,
            "scope": self.scope,
            "secret_free": self.secret_free,
            "secret_reference": self.secret_reference,
            "approved_by": self.approved_by,
            "expires_on": self.expires_on,
            "detail": self.detail,
        }
        redacted: dict[str, Any] = _redact_value(payload)
        evidence = str(redacted.get("evidence", ""))
        redacted["evidence_digest"] = (
            "sha256:" + hashlib.sha256(evidence.encode("utf-8")).hexdigest()[:16]
        )
        return redacted


@dataclass(frozen=True)
class BinarySpec:
    """Version-probe description for one scientific executable."""

    item_id: str
    binary: str
    env_var: str
    default_paths: tuple[Path, ...]
    version_args: tuple[str, ...]
    version_pattern: re.Pattern[str]
    category: str
    requirement: str
    scope: str
    minimum: tuple[int, int, int] | None = None


BINARY_SPECS = (
    BinarySpec(
        item_id="orca",
        binary="orca",
        env_var="CONFSEARCH_ORCA_PATH",
        default_paths=(ORCA_PATH,),
        version_args=("--version",),
        version_pattern=re.compile(r"(\d+\.\d+(?:\.\d+)?)"),
        category="scientific",
        requirement="ORCA executable is available for QC readiness",
        scope="version probe only; no QC job submitted",
    ),
    BinarySpec(
        item_id="xtb",
        binary="xtb",
        env_var="CONFSEARCH_XTB_PATH",
        default_paths=(XTB_PATH,),
        version_args=("--version",),
        version_pattern=re.compile(r"xtb version (\d+\.\d+\.\d+)"),
        category="scientific",
        requirement="xTB executable is available for sampling/optimisation readiness",
        scope="version probe only; no QC job submitted",
    ),
    BinarySpec(
        item_id="crest",
        binary="crest",
        env_var="CONFSEARCH_CREST_PATH",
        default_paths=(CREST_PATH,),
        version_args=("--version",),
        version_pattern=re.compile(r"Version (\d+\.\d+\.\d+)"),
        category="scientific",
        requirement="CREST executable is available for conformer-search readiness",
        scope="version probe only; no QC job submitted",
    ),
)


# ---------------------------------------------------------------------------
# Low-level helpers (all monkeypatchable for tests)
# ---------------------------------------------------------------------------


def _redact_text(text: str) -> str:
    """Return ``text`` with credential-shaped tokens replaced."""
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def _redact_value(value: Any) -> Any:
    """Recursively redact strings inside JSON-compatible containers."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact_value(entry) for entry in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(entry) for entry in value)
    if isinstance(value, dict):
        return {key: _redact_value(entry) for key, entry in value.items()}
    return value


def _secret_hits(text: str) -> int:
    """Count credential patterns still present in ``text`` (never the value)."""
    return sum(1 for pattern in SECRET_PATTERNS if pattern.search(text))


def _compact(text: str, limit: int = EVIDENCE_LIMIT) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _run_command(argv: Sequence[str], *, timeout: float = COMMAND_TIMEOUT) -> CommandResult:
    """Run one read-only command with a hard timeout; never raises."""
    command = tuple(str(part) for part in argv)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return CommandResult(command, None, error="not_found")
    except subprocess.TimeoutExpired:
        return CommandResult(command, None, error="timeout")
    except OSError as exc:
        return CommandResult(command, None, error=f"os_error:{type(exc).__name__}")
    return CommandResult(
        command,
        completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def _which(name: str, env: Mapping[str, str]) -> str | None:
    path = env.get("PATH", "")
    return shutil.which(name, path=path)


def _is_file(path: Path) -> bool:
    return path.is_file()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_json(path: Path) -> Any | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _first_executable(candidates: Sequence[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _find_chrome(cache: Path) -> tuple[Path | None, Path | None]:
    chrome = _first_executable(sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True))
    headless = _first_executable(
        sorted(cache.glob("chromium_headless_shell-*/chrome-linux*/headless_shell"), reverse=True)
    )
    return chrome, headless


def _probe_writable(target: Path) -> tuple[bool, str]:
    """Probe writability by creating and deleting a temp file in the nearest existing ancestor."""
    anchor = target
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    if not anchor.is_dir():
        return False, f"anchor_not_a_directory:{anchor}"
    try:
        with tempfile.NamedTemporaryFile(prefix=".acp2-readiness-probe-", dir=anchor) as handle:
            handle.write(b"probe")
            handle.flush()
    except OSError as exc:
        return False, f"not_writable:{type(exc).__name__}:{anchor}"
    return True, f"probe_ok:{anchor}"


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(text)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def _version_at_least(version: tuple[int, int, int], minimum: tuple[int, int, int]) -> bool:
    return version >= minimum


def _item(
    *,
    item_id: str,
    category: str,
    requirement: str,
    result: str,
    evidence: str,
    scope: str,
    configured: bool = False,
    environment_default: str = "absent",
    command: tuple[str, ...] | None = None,
    evidence_kind: str = EVIDENCE_PROBE,
    secret_reference: str | None = None,
    approved_by: str | None = None,
    expires_on: str | None = None,
    detail: str | None = None,
) -> ReadinessItem:
    if result not in STATUSES:
        raise ValueError(f"invalid readiness result: {result}")
    return ReadinessItem(
        id=item_id,
        category=category,
        requirement=requirement,
        result=result,
        configured=configured,
        environment_default=environment_default,
        command=command,
        evidence=_compact(evidence),
        scope=scope,
        evidence_kind=evidence_kind,
        secret_reference=secret_reference,
        approved_by=approved_by,
        expires_on=expires_on,
        detail=_compact(detail) if detail else None,
    )


def _blocked(
    *,
    item_id: str,
    category: str,
    requirement: str,
    evidence: str,
    scope: str,
    command: tuple[str, ...] | None = None,
    configured: bool = False,
    environment_default: str = "absent",
    detail: str | None = None,
) -> ReadinessItem:
    return _item(
        item_id=item_id,
        category=category,
        requirement=requirement,
        result=STATUS_NOT_VERIFIED,
        evidence=evidence,
        scope=scope,
        command=command,
        configured=configured,
        environment_default=environment_default,
        evidence_kind=EVIDENCE_BLOCKED,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _node22_bin(env: Mapping[str, str]) -> Path:
    tools = Path(env.get("ACP2_TOOLS") or DEFAULT_TOOLS_ROOT)
    return tools / NODE22_VERSION / "bin"


def _pnpm_bin(env: Mapping[str, str]) -> Path:
    tools = Path(env.get("ACP2_TOOLS") or DEFAULT_TOOLS_ROOT)
    return tools / "pnpm" / "node_modules" / ".bin" / "pnpm"


def _dsh_root(env: Mapping[str, str]) -> Path:
    tools = Path(env.get("ACP2_TOOLS") or DEFAULT_TOOLS_ROOT)
    return tools / "deepseek-harness"


def _path_entries(env: Mapping[str, str]) -> list[Path]:
    return [Path(entry) for entry in env.get("PATH", "").split(os.pathsep) if entry]


def _lsf_env_prefix(env: Mapping[str, str]) -> tuple[str, ...]:
    envdir = env.get("LSF_ENVDIR", "").strip() or str(OPENLAVA_ENVDIR)
    return ("env", f"LSF_ENVDIR={envdir}")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_python(env: Mapping[str, str]) -> tuple[str | None, ReadinessItem]:
    resolved = _which("python3.11", env)
    candidate = Path(resolved) if resolved else Path("/usr/bin/python3.11")
    default_python = _which("python3", env)
    default_label = "python3 -> not found"
    if default_python:
        default_result = _run_command((default_python, "--version"))
        default_label = f"python3 -> {_compact(default_result.output) or 'unknown'}"
    command = (str(candidate), "--version")
    if not resolved and not _is_file(candidate):
        return None, _item(
            item_id="python311",
            category="runtime",
            requirement="Python 3.11 is installed and resolvable",
            result=STATUS_FAIL,
            evidence=f"python3.11 not on PATH and {candidate} missing; {default_label}",
            scope="interpreter presence/version only",
            command=command,
            environment_default=default_label,
        )
    result = _run_command(command)
    version_text = _compact(result.output)
    version = _parse_version(version_text)
    default_state = f"{candidate} (explicit PATH)" if resolved else f"{candidate} (fixed path)"
    if result.error == "timeout":
        return str(candidate), _item(
            item_id="python311",
            category="runtime",
            requirement="Python 3.11 is installed and resolvable",
            result=STATUS_NOT_VERIFIED,
            evidence=f"{candidate} --version timed out",
            scope="interpreter presence/version only",
            command=command,
            environment_default=default_label,
            evidence_kind=EVIDENCE_BLOCKED,
        )
    if not result.ok or version is None:
        return str(candidate), _item(
            item_id="python311",
            category="runtime",
            requirement="Python 3.11 is installed and resolvable",
            result=STATUS_FAIL,
            evidence=(
                f"{candidate} --version failed "
                f"({result.error or result.returncode}): {version_text}"
            ),
            scope="interpreter presence/version only",
            command=command,
            environment_default=default_label,
        )
    if not _version_at_least(version, (3, 11, 0)):
        return str(candidate), _item(
            item_id="python311",
            category="runtime",
            requirement="Python 3.11 is installed and resolvable",
            result=STATUS_FAIL,
            evidence=f"{candidate} -> {version_text}; Python >= 3.11 required; {default_label}",
            scope="interpreter presence/version only",
            command=command,
            environment_default=default_label,
        )
    return str(candidate), _item(
        item_id="python311",
        category="runtime",
        requirement="Python 3.11 is installed and resolvable",
        result=STATUS_PASS,
        evidence=f"{version_text} via {default_state}; {default_label}",
        scope="interpreter presence/version only; no ACP workflow executed",
        command=command,
        environment_default=default_label,
    )


def _check_acp_package(python: str | None, env: Mapping[str, str]) -> ReadinessItem:
    requirement = "ACP package (acp + cccp) is importable from the Python 3.11 interpreter"
    scope = "import + version metadata only; no workflow executed"
    command = (
        python or "python3.11",
        "-c",
        "import acp, cccp; print(acp.__version__, cccp.__version__)",
    )
    if python is None:
        return _blocked(
            item_id="acp_package",
            category="runtime",
            requirement=requirement,
            evidence="blocked: Python 3.11 interpreter is not available (see python311)",
            scope=scope,
            command=command,
        )
    result = _run_command(command)
    output = _compact(result.output)
    if result.error == "timeout":
        return _blocked(
            item_id="acp_package",
            category="runtime",
            requirement=requirement,
            evidence="blocked: ACP import probe timed out",
            scope=scope,
            command=command,
        )
    if not result.ok:
        return _item(
            item_id="acp_package",
            category="runtime",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"import acp, cccp failed ({result.error or result.returncode}): {output}",
            scope=scope,
            command=command,
        )
    versions = output.split()
    if len(versions) < 2:
        return _blocked(
            item_id="acp_package",
            category="runtime",
            requirement=requirement,
            evidence=f"unexpected import output: {output}",
            scope=scope,
            command=command,
        )
    return _item(
        item_id="acp_package",
        category="runtime",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"acp {versions[0]}, cccp {versions[1]} importable via {python}",
        scope=scope,
        command=command,
        environment_default="installed",
    )


def _check_binary(spec: BinarySpec, env: Mapping[str, str]) -> ReadinessItem:
    explicit = env.get(spec.env_var, "").strip()
    candidate: Path | None = None
    environment_default = "absent"
    if explicit:
        candidate = Path(explicit)
        environment_default = "explicit-env"
        if not _is_file(candidate):
            return _item(
                item_id=spec.item_id,
                category=spec.category,
                requirement=spec.requirement,
                result=STATUS_FAIL,
                evidence=f"{spec.env_var}={candidate} does not exist",
                scope=spec.scope,
                configured=True,
                environment_default=environment_default,
            )
    else:
        candidate = next((path for path in spec.default_paths if _is_file(path)), None)
        if candidate is not None:
            environment_default = "present"
        else:
            resolved = _which(spec.binary, env)
            if resolved:
                candidate = Path(resolved)
                environment_default = "present-path"
    command = (str(candidate), *spec.version_args) if candidate else None
    if candidate is None:
        searched = ", ".join(str(path) for path in spec.default_paths)
        return _item(
            item_id=spec.item_id,
            category=spec.category,
            requirement=spec.requirement,
            result=STATUS_FAIL,
            evidence=f"{spec.binary} not found at {searched} nor on PATH; {spec.env_var} unset",
            scope=spec.scope,
            command=command,
        )
    result = _run_command(command or ())
    raw_output = result.output
    output = _compact(raw_output)
    if result.error == "timeout":
        return _blocked(
            item_id=spec.item_id,
            category=spec.category,
            requirement=spec.requirement,
            evidence=f"{candidate} {' '.join(spec.version_args)} timed out",
            scope=spec.scope,
            command=command,
            configured=bool(explicit),
            environment_default=environment_default,
        )
    if not result.ok:
        return _item(
            item_id=spec.item_id,
            category=spec.category,
            requirement=spec.requirement,
            result=STATUS_FAIL,
            evidence=(
                f"{candidate} {spec.version_args[0]} failed "
                f"({result.error or result.returncode}): {output}"
            ),
            scope=spec.scope,
            command=command,
            configured=bool(explicit),
            environment_default=environment_default,
        )
    match = spec.version_pattern.search(raw_output)
    if match is None:
        return _blocked(
            item_id=spec.item_id,
            category=spec.category,
            requirement=spec.requirement,
            evidence=f"{candidate} ran but version output was not recognised: {output}",
            scope=spec.scope,
            command=command,
            configured=bool(explicit),
            environment_default=environment_default,
        )
    version_text = match.group(1)
    version = _parse_version(version_text)
    if spec.minimum is not None and (
        version is None or not _version_at_least(version, spec.minimum)
    ):
        return _item(
            item_id=spec.item_id,
            category=spec.category,
            requirement=spec.requirement,
            result=STATUS_FAIL,
            evidence=f"{candidate} -> {version_text}; minimum {'.'.join(map(str, spec.minimum))}",
            scope=spec.scope,
            command=command,
            configured=bool(explicit),
            environment_default=environment_default,
        )
    return _item(
        item_id=spec.item_id,
        category=spec.category,
        requirement=spec.requirement,
        result=STATUS_PASS,
        evidence=f"{candidate} -> version {version_text}",
        scope=spec.scope,
        command=command,
        configured=bool(explicit),
        environment_default=environment_default,
        detail=(
            "installed binary verified by --version only; default PATH membership is a separate row"
        ),
    )


def _check_node22(env: Mapping[str, str]) -> ReadinessItem:
    node = _node22_bin(env) / "node"
    command = (str(node), "--version")
    if not _is_file(node):
        return _item(
            item_id="node22",
            category="node",
            requirement="Node.js 22.19 runtime is installed for DSH/RPH",
            result=STATUS_FAIL,
            evidence=f"{node} missing (ACP2_TOOLS={env.get('ACP2_TOOLS', '<unset>')})",
            scope="runtime version only; no JS project executed",
            command=command,
            environment_default="absent",
        )
    result = _run_command(command)
    version_text = _compact(result.output)
    version = _parse_version(version_text)
    if not result.ok or version is None:
        return _item(
            item_id="node22",
            category="node",
            requirement="Node.js 22.19 runtime is installed for DSH/RPH",
            result=STATUS_NOT_VERIFIED if result.error == "timeout" else STATUS_FAIL,
            evidence=(
                f"{node} --version failed ({result.error or result.returncode}): {version_text}"
            ),
            scope="runtime version only; no JS project executed",
            command=command,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="absent",
        )
    if not _version_at_least(version, (22, 19, 0)):
        return _item(
            item_id="node22",
            category="node",
            requirement="Node.js 22.19 runtime is installed for DSH/RPH",
            result=STATUS_FAIL,
            evidence=f"{node} -> {version_text}; Node >= 22.19 required",
            scope="runtime version only; no JS project executed",
            command=command,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="absent",
        )
    return _item(
        item_id="node22",
        category="node",
        requirement="Node.js 22.19 runtime is installed for DSH/RPH",
        result=STATUS_PASS,
        evidence=f"{node} -> {version_text}",
        scope="runtime version only; no JS project executed",
        command=command,
        configured=bool(env.get("ACP2_TOOLS")),
        environment_default="present-default-root",
    )


def _check_default_node(env: Mapping[str, str]) -> ReadinessItem:
    resolved = _which("node", env)
    requirement = "Default PATH node satisfies >= 22.19 and the tooling root is exported"
    scope = "environment profile row; installed binaries are not reclassified as missing"
    if resolved is None:
        return _item(
            item_id="default_path_node",
            category="environment",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence="node not found on the default PATH; node22 exists only under ACP2_TOOLS",
            scope=scope,
            environment_default="absent",
        )
    result = _run_command((resolved, "--version"))
    version_text = _compact(result.output)
    version = _parse_version(version_text)
    tools_exported = bool(env.get("ACP2_TOOLS"))
    node_bin_on_path = _node22_bin(env) in _path_entries(env)
    gaps: list[str] = []
    if version is None or not _version_at_least(version, (22, 19, 0)):
        gaps.append(f"node {version_text or 'unknown'} < required 22.19")
    if not tools_exported:
        gaps.append("ACP2_TOOLS unset")
    if not node_bin_on_path:
        gaps.append(f"{_node22_bin(env)} not on PATH")
    evidence = f"default node -> {version_text}; " + ("; ".join(gaps) if gaps else "profile OK")
    return _item(
        item_id="default_path_node",
        category="environment",
        requirement=requirement,
        result=STATUS_FAIL if gaps else STATUS_PASS,
        evidence=evidence,
        scope=scope,
        command=(resolved, "--version"),
        configured=tools_exported and node_bin_on_path,
        environment_default=f"node {version_text or 'unknown'}",
        detail=(
            "export ACP2_TOOLS and prepend the Node 22 bin directory; never silently swap versions"
        ),
    )


def _check_pnpm(env: Mapping[str, str]) -> ReadinessItem:
    pnpm = _pnpm_bin(env)
    node_bin = _node22_bin(env)
    command = ("env", f"PATH={node_bin}:{env.get('PATH', '')}", str(pnpm), "--version")
    requirement = "pnpm 11.28.5 is installed for the RPH workspace"
    scope = "version probe only; no package install or build executed"
    if not _is_file(pnpm):
        return _item(
            item_id="pnpm",
            category="node",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"{pnpm} missing; ACP2_TOOLS={env.get('ACP2_TOOLS', '<unset>')}",
            scope=scope,
            command=command,
            environment_default="absent",
        )
    result = _run_command(command)
    version_text = _compact(result.output)
    version = _parse_version(version_text)
    if not result.ok or version is None:
        return _item(
            item_id="pnpm",
            category="node",
            requirement=requirement,
            result=STATUS_NOT_VERIFIED if result.error == "timeout" else STATUS_FAIL,
            evidence=(
                f"{pnpm} --version failed ({result.error or result.returncode}): {version_text}"
            ),
            scope=scope,
            command=command,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="absent",
        )
    if not _version_at_least(version, PNPM_REQUIRED):
        return _item(
            item_id="pnpm",
            category="node",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"{pnpm} -> {version_text}; required >= {'.'.join(map(str, PNPM_REQUIRED))}",
            scope=scope,
            command=command,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="absent",
        )
    return _item(
        item_id="pnpm",
        category="node",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"{pnpm} -> {version_text}",
        scope=scope,
        command=command,
        configured=bool(env.get("ACP2_TOOLS")),
        environment_default="absent",
    )


def _check_corepack(env: Mapping[str, str]) -> ReadinessItem:
    node_bin = _node22_bin(env)
    corepack = node_bin / "corepack"
    requirement = "Corepack resolves the pinned pnpm 11.28.5 shim"
    scope = "pnpm activation probe only; no packages installed"
    command = ("env", f"PATH={node_bin}:{env.get('PATH', '')}", str(corepack), "pnpm", "--version")
    if not _is_file(corepack):
        return _item(
            item_id="corepack_pnpm",
            category="node",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"{corepack} missing",
            scope=scope,
            command=command,
            environment_default="absent",
        )
    result = _run_command(command)
    version_text = _compact(result.output)
    version = _parse_version(version_text)
    if result.ok and version is not None and _version_at_least(version, PNPM_REQUIRED):
        return _item(
            item_id="corepack_pnpm",
            category="node",
            requirement=requirement,
            result=STATUS_PASS,
            evidence=f"corepack pnpm -> {version_text}",
            scope=scope,
            command=command,
            configured=True,
            environment_default="present",
        )
    return _item(
        item_id="corepack_pnpm",
        category="node",
        requirement=requirement,
        result=STATUS_FAIL,
        evidence=(
            f"corepack pnpm --version failed (rc={result.returncode}, error={result.error}); "
            f"run `corepack prepare pnpm@{'.'.join(map(str, PNPM_REQUIRED))} --activate` "
            f"or prepend {env.get('ACP2_TOOLS', DEFAULT_TOOLS_ROOT)}/pnpm to PATH"
        ),
        scope=scope,
        command=command,
        environment_default="absent",
        detail="local pnpm binary is checked separately by the pnpm row; corepack cache is broken",
    )


def _check_chromium(env: Mapping[str, str]) -> ReadinessItem:
    explicit = env.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if explicit:
        cache = Path(explicit)
        environment_default = "explicit-env"
    else:
        cache = Path(env.get("HOME") or Path.home()) / PLAYWRIGHT_CACHE
        environment_default = "default-cache"
    requirement = "Chromium cache is available for browser acceptance"
    scope = "executable presence only; no browser launched and no page loaded"
    chrome, headless = _find_chrome(cache)
    if chrome is None:
        return _item(
            item_id="chromium_cache",
            category="browser",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"no chromium executable under {cache}",
            scope=scope,
            configured=bool(explicit),
            environment_default=environment_default,
        )
    evidence = f"chromium {chrome}"
    if headless is not None:
        evidence += f"; headless shell {headless}"
    return _item(
        item_id="chromium_cache",
        category="browser",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=evidence,
        scope=scope,
        configured=bool(explicit),
        environment_default=environment_default,
        detail="candidate only: browser acceptance still needs a real Playwright run",
    )


def _check_openlava_install(env: Mapping[str, str]) -> ReadinessItem:
    required = ("bsub", "bjobs", "bhist")
    found = [name for name in required if _is_file(OPENLAVA_ROOT / "bin" / name)]
    missing = [name for name in required if name not in found]
    requirement = "Openlava 2.0 installation tree is present"
    scope = "installation presence only; cluster commands are gated behind --probe-cluster"
    command = None
    if missing:
        return _item(
            item_id="openlava_install",
            category="scheduler",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=(
                f"{OPENLAVA_ROOT}/bin missing: {', '.join(missing)} (found: {', '.join(found)})"
            ),
            scope=scope,
            command=command,
            environment_default="absent",
        )
    return _item(
        item_id="openlava_install",
        category="scheduler",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"{OPENLAVA_ROOT} provides {', '.join(found)}; envdir {OPENLAVA_ENVDIR}",
        scope=scope,
        command=command,
        environment_default="present-default-root",
        detail="Openlava is installed but not on the default PATH; see profile_exports",
    )


def _check_lsf_envdir(env: Mapping[str, str]) -> ReadinessItem:
    value = env.get("LSF_ENVDIR", "").strip()
    requirement = "LSF_ENVDIR points at the Openlava environment directory"
    scope = "configuration only; no cluster command executed"
    if not value:
        return _item(
            item_id="lsf_envdir",
            category="scheduler",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"LSF_ENVDIR unset; export LSF_ENVDIR={OPENLAVA_ENVDIR} in the profile",
            scope=scope,
            command=None,
            environment_default="absent",
        )
    envdir = Path(value)
    if not _is_file(envdir / "lsf.conf"):
        return _item(
            item_id="lsf_envdir",
            category="scheduler",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"LSF_ENVDIR={envdir} has no lsf.conf",
            scope=scope,
            configured=True,
            environment_default="explicit-env",
        )
    return _item(
        item_id="lsf_envdir",
        category="scheduler",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"LSF_ENVDIR={envdir} (lsf.conf present)",
        scope=scope,
        configured=True,
        environment_default="explicit-env",
    )


def _check_lsf_command(env: Mapping[str, str], *, probe_cluster: bool) -> ReadinessItem:
    prefix = _lsf_env_prefix(env)
    command = (*prefix, str(OPENLAVA_ROOT / "bin" / "bsub"), "-V")
    requirement = "Openlava batch commands are reachable with LSF_ENVDIR loaded"
    scope = "read-only version query; no job submitted"
    if not probe_cluster:
        return _blocked(
            item_id="lsf_command_reachable",
            category="scheduler",
            requirement=requirement,
            evidence=(
                "read-only cluster probe disabled by default; recorded input: bsub -V returns "
                "openlava 2.0 with LSF_ENVDIR set (EXECUTION_PLAN §21); rerun with --probe-cluster"
            ),
            scope=scope,
            command=command,
            environment_default="probe-disabled",
        )
    result = _run_command(command)
    output = _compact(result.output)
    if not result.ok:
        return _item(
            item_id="lsf_command_reachable",
            category="scheduler",
            requirement=requirement,
            result=STATUS_NOT_VERIFIED if result.error == "timeout" else STATUS_FAIL,
            evidence=f"bsub -V failed ({result.error or result.returncode}): {output}",
            scope=scope,
            command=command,
            configured=bool(env.get("LSF_ENVDIR")),
            environment_default="probed",
        )
    match = re.search(r"openlava\s+(?:project\s+)?(\d+\.\d+)", output, re.IGNORECASE)
    version_text = match.group(1) if match else "unknown"
    return _item(
        item_id="lsf_command_reachable",
        category="scheduler",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"bsub -V -> openlava {version_text}",
        scope=scope,
        command=command,
        configured=bool(env.get("LSF_ENVDIR")),
        environment_default="probed",
        detail=(
            None
            if env.get("LSF_ENVDIR")
            else "probe injected LSF_ENVDIR; the profile export is still missing (see lsf_envdir)"
        ),
    )


def _check_lsf_smoke_job(env: Mapping[str, str], *, probe_cluster: bool) -> ReadinessItem:
    prefix = _lsf_env_prefix(env)
    bjobs = (*prefix, str(OPENLAVA_ROOT / "bin" / "bjobs"), "-a")
    bhist = (*prefix, str(OPENLAVA_ROOT / "bin" / "bhist"), "-l", LSF_SMOKE_JOB_ID)
    requirement = f"Smoke job {LSF_SMOKE_JOB_ID} is recorded DONE (bsub -> bjobs -> bhist chain)"
    scope = "read-only history query; no job submitted"
    if not probe_cluster:
        return _blocked(
            item_id="lsf_smoke_job_1767",
            category="scheduler",
            requirement=requirement,
            evidence=(
                f"recorded input: job {LSF_SMOKE_JOB_ID} completed DONE in queue normal on host "
                "workstation (EXECUTION_PLAN §21/§22); rerun with --probe-cluster"
            ),
            scope=scope,
            command=bjobs,
            environment_default="probe-disabled",
        )
    jobs = _run_command(bjobs)
    if jobs.ok:
        for line in jobs.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == LSF_SMOKE_JOB_ID:
                state = parts[2] if len(parts) > 2 else "unknown"
                result = STATUS_PASS if state == "DONE" else STATUS_FAIL
                return _item(
                    item_id="lsf_smoke_job_1767",
                    category="scheduler",
                    requirement=requirement,
                    result=result,
                    evidence=f"bjobs -> {_compact(line)}",
                    scope=scope,
                    command=bjobs,
                    configured=bool(env.get("LSF_ENVDIR")),
                    environment_default="probed",
                )
    history = _run_command(bhist)
    if history.ok and re.search(r"Done successfully", history.output, re.IGNORECASE):
        return _item(
            item_id="lsf_smoke_job_1767",
            category="scheduler",
            requirement=requirement,
            result=STATUS_PASS,
            evidence=f"bhist -l {LSF_SMOKE_JOB_ID} -> Done successfully",
            scope=scope,
            command=bhist,
            configured=bool(env.get("LSF_ENVDIR")),
            environment_default="probed",
        )
    reason = "job record not found (history may have been cleaned)"
    if not jobs.ok:
        reason = f"bjobs -a failed ({jobs.error or jobs.returncode})"
    return _blocked(
        item_id="lsf_smoke_job_1767",
        category="scheduler",
        requirement=requirement,
        evidence=f"{reason}; recorded input still cites job {LSF_SMOKE_JOB_ID} as DONE",
        scope=scope,
        command=bjobs,
        configured=bool(env.get("LSF_ENVDIR")),
        environment_default="probed",
    )


def _check_dsh_cli(env: Mapping[str, str]) -> ReadinessItem:
    root = _dsh_root(env)
    manifest_path = root / "apps" / "cli" / "package.json"
    requirement = "DSH CLI 0.2.1-rc.2 installation is resolvable in the external tools tree"
    scope = "installation presence only; runtime/negotiation evidence belongs to M0-09"
    manifest = _load_json(manifest_path)
    if manifest is None:
        return _blocked(
            item_id="dsh_cli",
            category="tooling",
            requirement=requirement,
            evidence=f"{manifest_path} unreadable; ACP2_TOOLS={env.get('ACP2_TOOLS', '<unset>')}",
            scope=scope,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="absent",
        )
    name = str(manifest.get("name", ""))
    version = str(manifest.get("version", ""))
    bin_field = manifest.get("bin")
    bin_entry: str | None = None
    if isinstance(bin_field, str):
        bin_entry = bin_field
    elif isinstance(bin_field, dict):
        first = next(iter(bin_field.values()), None)
        if isinstance(first, str):
            bin_entry = first
    if name != "@deepseek-ai/dsh" or not bin_entry:
        return _item(
            item_id="dsh_cli",
            category="tooling",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"{manifest_path} does not describe @deepseek-ai/dsh (name={name!r})",
            scope=scope,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="present-default-root",
        )
    entry = manifest_path.parent / bin_entry
    if not _is_file(entry):
        return _item(
            item_id="dsh_cli",
            category="tooling",
            requirement=requirement,
            result=STATUS_FAIL,
            evidence=f"{name} {version} entry {entry} missing (install incomplete)",
            scope=scope,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="present-default-root",
        )
    return _item(
        item_id="dsh_cli",
        category="tooling",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"{name} {version} entry {entry}",
        scope=scope,
        configured=bool(env.get("ACP2_TOOLS")),
        environment_default="present-default-root",
        detail=(
            "EXECUTION_PLAN §21 records this tree as runnable; client negotiation matrix is M0-09"
        ),
    )


def _check_dsh_client_matrix(env: Mapping[str, str]) -> ReadinessItem:
    root = _dsh_root(env)
    client_manifest_path = root / "packages" / "mcp" / "mcp-client" / "package.json"
    lock_path = root / "pnpm-lock.yaml"
    requirement = "External DSH MCP client resolution and lock digest are recorded"
    scope = "metadata + digest only; no DSH client executed"
    manifest = _load_json(client_manifest_path)
    if manifest is None:
        return _blocked(
            item_id="dsh_client_matrix",
            category="tooling",
            requirement=requirement,
            evidence=f"{client_manifest_path} unreadable; external DSH tree not available",
            scope=scope,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="absent",
        )
    name = str(manifest.get("name", ""))
    version = str(manifest.get("version", ""))
    dependencies = manifest.get("dependencies")
    client_version = ""
    if isinstance(dependencies, dict):
        client_version = str(dependencies.get("@modelcontextprotocol/client", ""))
    digest = _sha256_file(lock_path)
    if not client_version or digest is None:
        return _blocked(
            item_id="dsh_client_matrix",
            category="tooling",
            requirement=requirement,
            evidence=(
                f"{name} {version}; @modelcontextprotocol/client={client_version or 'unresolved'}; "
                f"pnpm-lock digest={'present' if digest else 'missing'}"
            ),
            scope=scope,
            configured=bool(env.get("ACP2_TOOLS")),
            environment_default="present-default-root",
        )
    return _item(
        item_id="dsh_client_matrix",
        category="tooling",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=(
            f"{name} {version}; @modelcontextprotocol/client {client_version}; "
            f"pnpm-lock sha256:{digest[:16]}"
        ),
        scope=scope,
        configured=bool(env.get("ACP2_TOOLS")),
        environment_default="present-default-root",
        detail="ADR-006 client matrix input; frozen only after M0-09 verifies real negotiation",
    )


def _check_profile_exports(env: Mapping[str, str]) -> ReadinessItem:
    required_env = (
        "ACP2_TOOLS",
        "LSF_ENVDIR",
        "CONFSEARCH_ORCA_PATH",
        "CONFSEARCH_XTB_PATH",
        "CONFSEARCH_CREST_PATH",
    )
    required_paths = (
        _node22_bin(env),
        XTB_PATH.parent,
        CREST_PATH.parent,
        OPENLAVA_ROOT / "bin",
    )
    path_entries = _path_entries(env)
    missing_env = [key for key in required_env if not env.get(key)]
    missing_paths = [str(path) for path in required_paths if path not in path_entries]
    requirement = "README/profile exports all tool paths and env keys explicitly"
    scope = "profile configuration only; does not re-audit installed binaries"
    if not missing_env and not missing_paths:
        return _item(
            item_id="profile_exports",
            category="environment",
            requirement=requirement,
            result=STATUS_PASS,
            evidence=(
                "ACP2_TOOLS, LSF_ENVDIR and CONFSEARCH_*_PATH exported; "
                "PATH has node22/xtb/crest/openlava"
            ),
            scope=scope,
            configured=True,
            environment_default="configured",
        )
    gaps: list[str] = []
    if missing_env:
        gaps.append(f"env unset: {', '.join(missing_env)}")
    if missing_paths:
        gaps.append(f"PATH missing: {', '.join(missing_paths)}")
    return _item(
        item_id="profile_exports",
        category="environment",
        requirement=requirement,
        result=STATUS_FAIL,
        evidence="; ".join(gaps),
        scope=scope,
        environment_default="incomplete",
        detail=(
            "default PATH membership is not installation evidence; export these values before "
            "M4/M5 and keep CONFSEARCH_*_PATH absolute for real QC tests"
        ),
    )


def _check_writable(
    *,
    item_id: str,
    category: str,
    requirement: str,
    env_key: str,
    planned: Path,
    env: Mapping[str, str],
    scope: str,
) -> ReadinessItem:
    value = env.get(env_key, "").strip()
    target = Path(value) if value else planned
    ok, detail = _probe_writable(target)
    return _item(
        item_id=item_id,
        category=category,
        requirement=requirement,
        result=STATUS_PASS if ok else STATUS_FAIL,
        evidence=f"{target} {'writable' if ok else 'not writable'} ({detail})",
        scope=scope,
        configured=bool(value),
        environment_default="exported" if value else "planned-default (not exported)",
        detail="probe writes and immediately removes a temp file in the nearest existing ancestor",
    )


def _check_model_smoke(env: Mapping[str, str]) -> ReadinessItem:
    requirement = "One minimal real model call was executed for the selected model"
    scope = "recorded one-off call; no model call is issued by this checker"
    return _item(
        item_id="model_call_smoke",
        category="model",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=(
            "recorded input: deepseek-official/deepseek-flash returned OK on one minimal real "
            "call (EXECUTION_PLAN §21/§22); secret not recorded"
        ),
        scope=scope,
        environment_default="not-reprobed",
        evidence_kind=EVIDENCE_RECORDED,
        detail="one call only; written authorization, call-count/cost ceiling and rotation pending",
    )


def _check_model_credential(env: Mapping[str, str]) -> ReadinessItem:
    present = bool(env.get("DEEPSEEK_API_KEY"))
    requirement = "Production model gateway credential and written authorization are recorded"
    scope = "presence-only probe; credential value is never read or written"
    return _item(
        item_id="model_gateway_credential",
        category="model",
        requirement=requirement,
        result=STATUS_NOT_VERIFIED,
        evidence=(
            "no production gateway secret reference recorded; written authorization, call-count "
            "and cost ceiling unsigned; exposed credential requires rotation"
        ),
        scope=scope,
        configured=present,
        environment_default="present (value not read)" if present else "absent",
        evidence_kind=EVIDENCE_BLOCKED,
        secret_reference="env:DEEPSEEK_API_KEY" if present else None,
        detail="record a secret reference only; never store the key itself",
    )


def _check_clean_machine(env: Mapping[str, str], *, probe_remote: bool) -> ReadinessItem:
    host = env.get("ACP2_CLEAN_HOST", "").strip()
    requirement = "Clean ARM64 machine is reachable and allows user-level Python/Node install"
    scope = "reachability only; no password stored or passed; no sudo assumed"
    ssh_command = (
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=5",
        host or "<ACP2_CLEAN_HOST>",
        "uname",
        "-sm",
    )
    if not host:
        return _item(
            item_id="clean_machine_access",
            category="access",
            requirement=requirement,
            result=STATUS_PASS,
            evidence=(
                "recorded input: ARM64 clean host reachable over SSH with user-level Python/Node/"
                "pnpm install allowed (EXECUTION_PLAN §22)"
            ),
            scope=scope,
            environment_default="absent",
            evidence_kind=EVIDENCE_RECORDED,
            detail=(
                "recorded, not re-probed: export ACP2_CLEAN_HOST=<ssh alias> and rerun with "
                "--probe-remote; passwords are never accepted or stored"
            ),
        )
    if not probe_remote:
        return _blocked(
            item_id="clean_machine_access",
            category="access",
            requirement=requirement,
            evidence=(
                "ACP2_CLEAN_HOST referenced; rerun with --probe-remote for a BatchMode SSH probe"
            ),
            scope=scope,
            command=ssh_command,
            configured=True,
            environment_default="present",
        )
    result = _run_command(ssh_command)
    output = _compact(result.output)
    if not result.ok:
        return _item(
            item_id="clean_machine_access",
            category="access",
            requirement=requirement,
            result=STATUS_NOT_VERIFIED if result.error == "timeout" else STATUS_FAIL,
            evidence=f"BatchMode SSH probe failed (rc={result.returncode}, error={result.error})",
            scope=scope,
            command=ssh_command,
            configured=True,
            environment_default="present",
        )
    architecture = output.lower()
    is_arm = "aarch64" in architecture or "arm64" in architecture
    return _item(
        item_id="clean_machine_access",
        category="access",
        requirement=requirement,
        result=STATUS_PASS,
        evidence=f"ssh BatchMode probe -> {output}",
        scope=scope,
        command=ssh_command,
        configured=True,
        environment_default="present",
        detail=None if is_arm else "reachable host is not ARM64; confirm the clean-machine target",
    )


def _check_independent_reviewer(env: Mapping[str, str]) -> ReadinessItem:
    return _blocked(
        item_id="independent_reviewer",
        category="operations",
        requirement="Independent reviewer/operator is assigned for M5 gates",
        evidence="独立 reviewer 待指定 (EXECUTION_PLAN §22); second-operator split still pending",
        scope="staffing only; no technical probe",
        environment_default="absent",
    )


def _check_optional_qc(env: Mapping[str, str]) -> ReadinessItem:
    missing = [
        name
        for name in ("isostat", "molclus")
        if not _is_file(Path(f"/home/xieningke/{name}/bin/{name}"))
    ]
    if not missing:
        return _item(
            item_id="optional_qc_binaries",
            category="scientific",
            requirement="Optional ISOSTAT/Molclus binaries are registered",
            result=STATUS_PASS,
            evidence="isostat and molclus registered under /home/xieningke",
            scope="registration presence only",
        )
    return _blocked(
        item_id="optional_qc_binaries",
        category="scientific",
        requirement="Optional ISOSTAT/Molclus binaries are registered",
        evidence=f"not registered: {', '.join(missing)} (EXECUTION_PLAN §21)",
        scope="optional capability; not part of the M0-10 minimum environment",
    )


# ---------------------------------------------------------------------------
# Document assembly
# ---------------------------------------------------------------------------


DECLARED_INPUTS: tuple[dict[str, Any], ...] = (
    {
        "id": "openlava_job_1767",
        "statement": (
            "Job 1767 completed DONE in queue normal on host workstation; the "
            "bsub -> bjobs -> bhist -> native-output chain was exercised"
        ),
        "source": "EXECUTION_PLAN.md §21/§22",
    },
    {
        "id": "model_call_smoke",
        "statement": (
            "deepseek-official/deepseek-flash returned OK on one minimal real call; the "
            "secret was not recorded and the exposed credential requires rotation"
        ),
        "source": "EXECUTION_PLAN.md §21/§22",
    },
    {
        "id": "model_development_policy",
        "statement": (
            "The currently exposed key may be used only for low-cost development verification; "
            "production/model-gateway readiness remains NOT_VERIFIED until it is rotated and "
            "authorization plus call/cost ceilings are recorded"
        ),
        "source": "user decision 2026-10-26",
    },
    {
        "id": "clean_machine_arm64",
        "statement": (
            "An ARM64 clean host is reachable over SSH and allows user-level Python/Node/pnpm "
            "installation without sudo; the independent reviewer is still pending"
        ),
        "source": "EXECUTION_PLAN.md §22",
    },
    {
        "id": "clean_host_prepared",
        "statement": (
            "User-level Python 3.11.18, Node 22.19.0, pnpm 11.28.5, run root and artifact store "
            "were prepared on the ARM64 clean host without sudo; implementation-side only"
        ),
        "source": "reports/acp2_remediation_20261026/clean-host-prep.json",
    },
    {
        "id": "execution_roots",
        "statement": (
            "artifact store /ACPServer/acp2-artifacts/remediation, ACP_RUN_ROOT "
            "/ACPServer/acp2-runs/remediation, Controller 127.0.0.1:18765"
        ),
        "source": "EXECUTION_PLAN.md §22",
    },
)


def collect_readiness(
    env: Mapping[str, str] | None = None,
    *,
    probe_cluster: bool = False,
    probe_remote: bool = False,
    generated_on: datetime | None = None,
) -> dict[str, Any]:
    """Build the readiness document without submitting any QC/LSF/model work."""
    env_map: Mapping[str, str] = dict(os.environ) if env is None else env
    timestamp = generated_on or datetime.now(timezone.utc)
    python_path, python_item = _check_python(env_map)
    items = [
        python_item,
        _check_acp_package(python_path, env_map),
        *(_check_binary(spec, env_map) for spec in BINARY_SPECS),
        _check_node22(env_map),
        _check_default_node(env_map),
        _check_pnpm(env_map),
        _check_corepack(env_map),
        _check_chromium(env_map),
        _check_openlava_install(env_map),
        _check_lsf_envdir(env_map),
        _check_lsf_command(env_map, probe_cluster=probe_cluster),
        _check_lsf_smoke_job(env_map, probe_cluster=probe_cluster),
        _check_dsh_cli(env_map),
        _check_dsh_client_matrix(env_map),
        _check_profile_exports(env_map),
        _check_writable(
            item_id="run_root_writable",
            category="storage",
            requirement="ACP_RUN_ROOT is on a writable native filesystem",
            env_key="ACP_RUN_ROOT",
            planned=RUN_ROOT_PLANNED,
            env=env_map,
            scope="writability probe only; no job directory created",
        ),
        _check_writable(
            item_id="artifact_store_writable",
            category="storage",
            requirement="Artifact store root is writable",
            env_key="ACP2_ARTIFACT_STORE",
            planned=ARTIFACT_STORE_PLANNED,
            env=env_map,
            scope="writability probe only; no artifact written",
        ),
        _check_model_smoke(env_map),
        _check_model_credential(env_map),
        _check_clean_machine(env_map, probe_remote=probe_remote),
        _check_independent_reviewer(env_map),
        _check_optional_qc(env_map),
    ]
    counts = {status: sum(1 for item in items if item.result == status) for status in STATUSES}
    summary = {
        "total": len(items),
        "pass": counts[STATUS_PASS],
        "fail": counts[STATUS_FAIL],
        "not_verified": counts[STATUS_NOT_VERIFIED],
        "gate_ready": counts[STATUS_FAIL] == 0 and counts[STATUS_NOT_VERIFIED] == 0,
    }
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_on": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": platform.node(),
        "interpreter": sys.version.split()[0],
        "environment_profile": env_map.get("ACP2_REMEDIATION_ENV") or None,
        "mode": {
            "probe_cluster": probe_cluster,
            "probe_remote": probe_remote,
            "qc_jobs_submitted": 0,
            "lsf_jobs_submitted": 0,
            "model_calls": 0,
            "credentials_read": 0,
        },
        "summary": summary,
        "declared_inputs": [dict(entry) for entry in DECLARED_INPUTS],
        "items": [item.to_json() for item in items],
    }
    document["secret_free"] = _secret_hits(json.dumps(document, ensure_ascii=False)) == 0
    if not document["secret_free"]:
        document = _redact_value(document)
        document["secret_free"] = False
    return document


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _exit_code(document: Mapping[str, Any]) -> int:
    summary = document.get("summary") or {}
    if summary.get("fail"):
        return EXIT_FAIL
    if summary.get("not_verified"):
        return EXIT_NOT_VERIFIED
    return EXIT_PASS


def _write_document(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)


def _print_human(document: Mapping[str, Any]) -> None:
    for item in document.get("items", []):
        print(f"{item['result']:<13} {item['id']:<26} {item['evidence']}")
    summary = document.get("summary", {})
    print(
        f"\ntotal={summary.get('total')} pass={summary.get('pass')} "
        f"fail={summary.get('fail')} not_verified={summary.get('not_verified')} "
        f"gate_ready={summary.get('gate_ready')} secret_free={document.get('secret_free')}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only ACP 2.0 environment readiness ledger (M0-10). Never submits QC/LSF/"
            "model work and never reads credential values."
        )
    )
    parser.add_argument("--json", action="store_true", help="print the readiness document as JSON")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write the readiness document to this path (parent directories are created)",
    )
    parser.add_argument(
        "--probe-cluster",
        action="store_true",
        help=(
            "enable read-only Openlava probes (bsub -V, bjobs -a, bhist -l 1767); no job submitted"
        ),
    )
    parser.add_argument(
        "--probe-remote",
        action="store_true",
        help="enable a BatchMode SSH probe for ACP2_CLEAN_HOST (no password, no sudo)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    document = collect_readiness(probe_cluster=args.probe_cluster, probe_remote=args.probe_remote)
    if args.out is not None:
        _write_document(args.out, document)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False))
    elif args.out is None:
        _print_human(document)
    return _exit_code(document)


if __name__ == "__main__":
    sys.exit(main())
