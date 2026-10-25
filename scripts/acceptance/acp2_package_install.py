"""B25b package-install scenario: real clean-environment installation.

The scenario installs the four release distributions (``cccp``, ``acp-worker``,
``acp-web`` and the supplemental root ``acp`` build) into fresh virtual
environments outside the repository, then proves:

* the release manifest digests match the bytes on disk;
* the installed modules resolve to the venv site-packages, not this checkout;
* the CCCP controlled science sample runs with no Controller and no ``acp``
  import;
* the standalone CLI works, the Lab server starts on an isolated loopback port
  and an isolated run root, and the stdio MCP entry negotiates ``2025-06-18``
  and lists the eight P1 tools;
* wheel assets/notices/licenses/model assets/sample config/schema/SDK/docs are
  present;
* the ``acp`` command and the compatibility imports are preserved;
* A/C raw install evidence is either present (aggregated, not re-signed) or
  recorded as ``NOT_VERIFIED`` with its expected path;
* the C static-asset manifest mount is either validated or recorded as
  ``NOT_VERIFIED(c_static_assets_absent)``.

Only the harness-owned venvs/processes are touched.  No production service,
database or run root is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2

FIXTURE_SCHEMA = "acp2.package-install.fixture.v1"
TOOL_NAMES = (
    "acp_analyze",
    "acp_cancel",
    "acp_capabilities",
    "acp_preflight",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_status",
    "acp_submit",
)
ROLE_WHEELS = {
    "cccp": "cccp-{version}-py3-none-any.whl",
    "worker": "acp_worker-{version}-py3-none-any.whl",
    "web": "acp_web-{version}-py3-none-any.whl",
}
DEFAULT_A_C_EVIDENCE = (
    {
        "id": "A-27b",
        "path": "<server-root>/acp2-evidence/a-science-20261022/A/A-27/a27b-20261023T042054Z",
    },
    {
        "id": "A-28",
        "path": "<server-root>/acp2-evidence/a-science-20261022/A/A-28/20261023T044051Z",
    },
    {
        "id": "C33",
        "path": "<acp2-dev>/evidence/C/clean-install-live",
    },
    {
        "id": "C34",
        "path": "<acp2-dev>/evidence/C/operator-runbook-live",
    },
)
DEFAULT_STATIC_MANIFEST = (
    "docs/reference/next-change/acp2-contracts/experience/static-assets.manifest.json"
)
CCCP_SAMPLE = """\
from __future__ import annotations

import json
import sys


def main() -> int:
    import cccp
    from cccp.calculation.context import TaskContext
    from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest
    from cccp.calculation.tasks.singlepoint import run_singlepoint
    from cccp.qc.interfaces.base import QCResult

    class ControlledBackend:
        name = "orca"

        def single_point(self, coordinates, symbols, **kwargs):
            assert kwargs["method"] == "HF"
            return QCResult(success=True, energy=-1.0, symbols=list(symbols), converged=True)

    result = run_singlepoint(
        TaskRequest(
            task=TaskKind.SINGLEPOINT,
            structure=StructureInput(coordinates=((0.0, 0.0, 0.0),), symbols=("He",)),
            level=MethodSpec(method="HF", basis="def2-SVP"),
        ),
        context=TaskContext(backend=ControlledBackend()),
    )
    assert result.complete and result.energy_hartree == -1.0
    assert "acp" not in sys.modules and "acp_services" not in sys.modules
    print(json.dumps({"complete": result.complete, "energy": result.energy_hartree,
                      "cccp": cccp.__file__, "version": cccp.__version__}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _fixture(path: str | None) -> dict[str, Any]:
    if not path:
        raise ValueError("fixture_missing: --fixture is required for package-install")
    candidate = Path(path)
    if candidate.is_dir():
        candidate = candidate / "package-install.json"
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"fixture_unreadable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"fixture_not_json: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != FIXTURE_SCHEMA:
        raise ValueError(f"fixture_schema_mismatch: expected {FIXTURE_SCHEMA}")
    for key in ("release_dir", "expected_version"):
        if not str(document.get(key) or "").strip():
            raise ValueError(f"fixture.{key} is required")
    return document


_CANDIDATE_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def candidate_revision_from_release(release_dir: Path) -> tuple[str | None, str]:
    """Return the installed release's source revision and where it came from.

    The release manifest is produced by ``scripts/build_release.py`` from the
    exact tree that became the wheels.  Its ``source.revision`` is therefore
    the candidate identity, unlike the harness baseline constant.  Falls back
    to the adjacent ``source.json`` for older release directories.
    """
    manifest_path = Path(release_dir) / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    source = manifest.get("source") if isinstance(manifest, dict) else None
    revision = source.get("revision") if isinstance(source, dict) else None
    if isinstance(revision, str) and _CANDIDATE_REVISION_RE.fullmatch(revision):
        return revision, "release_manifest.source.revision"
    source_path = Path(release_dir) / "source.json"
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        document = {}
    revision = document.get("source_revision") if isinstance(document, dict) else None
    if isinstance(revision, str) and _CANDIDATE_REVISION_RE.fullmatch(revision):
        return revision, "source.json.source_revision"
    return None, "unavailable"


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None = None,
    log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean_env.pop("PYTHONHOME", None)
    if env:
        clean_env.update(env)
    completed = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=clean_env,
        check=False,
    )
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            "$ " + " ".join(argv) + "\n"
            f"exit={completed.returncode}\n"
            f"--- stdout ---\n{completed.stdout}\n"
            f"--- stderr ---\n{completed.stderr}\n",
            encoding="utf-8",
        )
    return completed


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        document = json.loads(response.read().decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"non-object response from {url}")
    return document


def _read_version_locations(source_root: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for relpath in ("src/cccp/__init__.py", "src/cccp/version.py"):
        text = (source_root / relpath).read_text(encoding="utf-8")
        match = re.search(r'^[ \t]*__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if not match:
            raise ValueError(f"{relpath} has no literal __version__")
        versions[relpath] = match.group(1)
    acp_text = (source_root / "src/acp/__init__.py").read_text(encoding="utf-8")
    acp_versions = re.findall(
        r'^[ \t]*__version__\s*=\s*["\']([^"\']+)["\']', acp_text, re.MULTILINE
    )
    if not acp_versions:
        raise ValueError("src/acp/__init__.py has no literal __version__")
    versions["src/acp/__init__.py"] = acp_versions[-1]
    project = tomllib.loads((source_root / "pyproject.toml").read_text(encoding="utf-8"))
    versions["pyproject.toml"] = str(project["project"]["version"])
    return versions


def _http_request(
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = int(exc.code)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    return status, payload


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _package_install_scheduler_probe(
    *,
    base: str,
    web_python: str,
    lab_root: Path,
    out_dir: Path,
    logs: Path,
    task: Mapping[str, Any],
    timeout: float,
) -> tuple[bool, dict[str, Any]]:
    """Submit one real H2/STO-3G singlepoint through the installed Lab."""
    evidence_dir = out_dir / "scheduler-task"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    facts: dict[str, Any] = {"schema": "acp2.package-install.scheduler-task.v1"}
    commands: list[dict[str, Any]] = []

    identity_code = """
import json
from pathlib import Path
import acp, acp_services, cccp
import acp.scheduler.runner
from acp.calculations.attempt_freeze import local_release_identity
root = Path(acp.scheduler.runner.__file__).resolve().parents[3]
identity = local_release_identity(root)
print(json.dumps({
    "modules": {"acp": acp.__file__, "acp_services": acp_services.__file__, "cccp": cccp.__file__},
    "runner_root": str(root),
    "release_id": identity.release_id,
    "release_digest": identity.release_digest,
    "file_count": len(identity.files),
    "prefixes": sorted({key.split("/", 1)[0] for key in identity.files}),
    "keys_are_absolute": any(key.startswith("/") for key in identity.files),
    "contains_repo_path": any("<server-root>/" in key for key in identity.files),
}))
"""
    identity_run = _run(
        [web_python, "-c", identity_code],
        cwd=lab_root,
        timeout=min(timeout, 120),
        log=logs / "scheduler-identity.log",
    )
    commands.append(
        {
            "label": "installed release identity",
            "argv": [web_python, "-c", "local_release_identity(<runner_root>)"],
            "exit": identity_run.returncode,
        }
    )
    identity_facts: dict[str, Any] = {}
    try:
        identity_facts = json.loads(identity_run.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        identity_facts["stderr"] = identity_run.stderr[-2000:]
    facts["installed_identity"] = identity_facts
    _write_json(evidence_dir / "installed-identity.json", identity_facts)
    identity_ok = bool(
        identity_run.returncode == 0
        and identity_facts.get("prefixes") == ["acp", "acp_services", "cccp"]
        and identity_facts.get("keys_are_absolute") is False
        and identity_facts.get("contains_repo_path") is False
        and all(
            "/site-packages/" in str(path)
            for path in (identity_facts.get("modules") or {}).values()
        )
        and len(str(identity_facts.get("release_id") or "")) == 16
        and len(str(identity_facts.get("release_digest") or "")) == 64
    )

    workflow = str(task.get("workflow") or "singlepoint")
    xyz = str(task.get("xyz") or "2\nH2\nH 0 0 0\nH 0 0 0.74\n")
    submit = {
        "workflow": workflow,
        "name": "b25-scheduler-smoke",
        "input": {
            "source_type": "xyz_text",
            "source": xyz,
            "charge": int(task.get("charge") or 0),
            "multiplicity": int(task.get("multiplicity") or 1),
        },
        "method": dict(
            task.get("method")
            or {"method": "HF", "basis": "STO-3G", "charge": 0, "multiplicity": 1}
        ),
        "resources": dict(task.get("resources") or {"nproc": 1, "mem": "1024MB"}),
    }
    status, created = _http_request(base + "/api/v1/jobs", method="POST", body=submit, timeout=60)
    commands.append(
        {
            "label": "POST /api/v1/jobs",
            "argv": ["POST", base + "/api/v1/jobs", json.dumps(submit)],
            "exit": status,
        }
    )
    created = created if isinstance(created, dict) else {}
    job_id = str(created.get("job_id") or created.get("id") or "")
    facts["submit"] = {
        "status": status,
        "job_id": job_id,
        "release_id": created.get("release_id"),
        "release_digest": created.get("release_digest"),
    }
    terminal = {"completed", "failed", "cancelled"}
    job: dict[str, Any] = {}
    deadline = time.monotonic() + min(timeout, 900)
    while job_id and time.monotonic() < deadline:
        _, job_payload = _http_request(base + f"/api/v1/jobs/{job_id}", timeout=min(timeout, 60))
        job = job_payload if isinstance(job_payload, dict) else {}
        if str(job.get("status") or "").lower() in terminal:
            break
        time.sleep(0.5)
    final_status = str(job.get("status") or "").lower()
    work_dir = Path(str(job.get("work_dir") or ""))
    facts["job"] = {
        "status": final_status,
        "attempt": job.get("attempt"),
        "error": job.get("error"),
        "work_dir": str(work_dir),
    }
    commands.append(
        {
            "label": "poll terminal status",
            "argv": ["GET", base + f"/api/v1/jobs/{job_id}"],
            "exit": 0 if final_status in terminal else 1,
        }
    )

    task_result = _read_json_file(work_dir / "RESULT" / "task_result.json")
    manifest = _read_json_file(work_dir / "RESULT" / "result_manifest.json")
    receipt = _read_json_file(work_dir / "execution_receipt.json")
    envelope = _read_json_file(work_dir / "WORK" / "00_RUNTIME" / "frozen" / "envelope.json")
    task_result = task_result if isinstance(task_result, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    receipt = receipt if isinstance(receipt, dict) else {}
    envelope = envelope if isinstance(envelope, dict) else {}
    result = task_result.get("result") if isinstance(task_result.get("result"), dict) else {}
    products = manifest.get("products") if isinstance(manifest.get("products"), list) else []
    receipt_release = str(receipt.get("release_id") or "")
    envelope_release = str(envelope.get("release_id") or "")
    orca_text = ""
    orca_output = work_dir / "sp.out"
    if orca_output.is_file():
        orca_text = orca_output.read_text(encoding="utf-8", errors="replace")
    energies = re.findall(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)", orca_text)
    orca_energy = float(energies[-1]) if energies else None
    energy = result.get("energy_hartree", orca_energy)
    task_ok = bool(
        final_status == "completed"
        and work_dir.is_dir()
        and task_result.get("status") == "completed"
        and task_result.get("complete") is True
        and isinstance(energy, (int, float))
        and float(energy) < 0
        and manifest.get("status") == "completed"
        and bool(products)
        and receipt.get("state") == "completed"
        and receipt_release
        and receipt_release == envelope_release
        and receipt_release != "unversioned-shared"
        and receipt_release == identity_facts.get("release_id")
        and len(str(envelope.get("release_digest") or "")) == 64
    )
    facts["artifacts"] = {
        "task_result": {
            "status": task_result.get("status"),
            "complete": task_result.get("complete"),
            "energy_hartree": energy,
        },
        "manifest": {
            "status": manifest.get("status"),
            "products": len(products),
        },
        "receipt": {
            "state": receipt.get("state"),
            "release_id": receipt_release,
        },
        "envelope": {
            "release_id": envelope_release,
            "release_digest": envelope.get("release_digest"),
        },
        "release_id_matches_installed_identity": receipt_release
        == identity_facts.get("release_id"),
    }
    for relative in (
        "RESULT/task_result.json",
        "RESULT/result_manifest.json",
        "execution_receipt.json",
        "WORK/00_RUNTIME/frozen/envelope.json",
        "job.json",
        "sp.inp",
        "sp.out",
    ):
        source = work_dir / relative
        if source.is_file():
            target = evidence_dir / relative.replace("/", "__")
            shutil.copy2(source, target)
    facts["commands"] = commands
    facts["installed_identity_ok"] = identity_ok
    facts["task_ok"] = task_ok
    facts["ok"] = bool(identity_ok and task_ok)
    _write_json(evidence_dir / "scheduler-task.json", facts)
    return bool(facts["ok"]), facts


def run_package_install(
    fixture_path: str | None,
    out_dir: Path,
    timeout: float,
) -> tuple[int, list[dict[str, Any]], dict[str, Any]]:
    """Execute the B25b clean-install scenario and return harness facts."""

    out_dir.mkdir(parents=True, exist_ok=True)
    logs = out_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    extensions: dict[str, Any] = {
        "acceptance.operation_class": "control",
        "acceptance.target": "local",
        "b25.fixture": str(fixture_path or ""),
        "b25.artifacts": artifacts,
    }

    def add(name: str, status: str, detail: str) -> None:
        assertions.append({"name": name, "status": status, "detail": detail})

    def artifact(path: Path) -> None:
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(out_dir).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )

    try:
        fixture = _fixture(fixture_path)
    except ValueError as exc:
        add("b25.fixture_schema", "NOT_VERIFIED", str(exc))
        extensions["b25.reason"] = str(exc)
        return EXIT_NOT_VERIFIED, assertions, extensions

    version = str(fixture["expected_version"])
    release_dir = Path(str(fixture["release_dir"])).resolve()
    root_wheel_dir = Path(str(fixture.get("root_wheel_dir") or release_dir)).resolve()
    python = str(fixture.get("python") or "/usr/bin/python3.11")
    extensions.update(
        {
            "b25.release_dir": str(release_dir),
            "b25.expected_version": version,
            "b25.python": python,
        }
    )
    if not release_dir.is_dir():
        add("b25.release_dir", "FAIL", f"missing release directory: {release_dir}")
        extensions["b25.reason"] = "release_dir_missing"
        return EXIT_FAIL, assertions, extensions

    # 1. Release manifest and bytes.
    manifest_path = release_dir / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        add("b25.release_manifest", "FAIL", f"unreadable manifest: {exc}")
        extensions["b25.reason"] = "release_manifest_unreadable"
        return EXIT_FAIL, assertions, extensions
    candidate_revision, candidate_revision_source = candidate_revision_from_release(release_dir)
    extensions["b25.candidate_revision"] = candidate_revision
    extensions["b25.candidate_revision_source"] = candidate_revision_source
    if candidate_revision is None:
        add(
            "b25.candidate_revision",
            "FAIL",
            "release manifest/source carries no 40-hex source revision",
        )
        extensions["b25.reason"] = "candidate_revision_unavailable"
        return EXIT_FAIL, assertions, extensions
    add(
        "b25.candidate_revision",
        "PASS",
        f"{candidate_revision} from {candidate_revision_source}",
    )
    digest_rows: list[dict[str, Any]] = []
    failing: list[str] = []
    for name, expected in sorted(manifest.get("artifacts", {}).items()):
        candidate = release_dir / name
        actual = _sha256(candidate) if candidate.is_file() else None
        ok = actual == expected
        digest_rows.append({"artifact": name, "expected": expected, "actual": actual, "ok": ok})
        if not ok:
            failing.append(name)
    root_name = str(fixture.get("root_wheel") or f"acp-{version}-py3-none-any.whl")
    root_wheel = root_wheel_dir / root_name
    root_expected = str(fixture.get("root_wheel_sha256") or "").strip()
    root_actual = _sha256(root_wheel) if root_wheel.is_file() else None
    root_ok = bool(root_actual) and (not root_expected or root_actual == root_expected)
    if not root_ok:
        failing.append(root_name)
    digest_document = _write_json(
        out_dir / "release-digests.json",
        {
            "manifest": str(manifest_path),
            "artifacts": digest_rows,
            "root_wheel": {
                "path": str(root_wheel),
                "expected": root_expected or None,
                "actual": root_actual,
                "ok": root_ok,
            },
            "ok": not failing,
        },
    )
    artifact(digest_document)
    add(
        "b25.release_digests",
        "PASS" if not failing else "FAIL",
        "all manifest digests match" if not failing else "digest mismatch: " + ",".join(failing),
    )
    if failing:
        extensions["b25.reason"] = "release_digest_mismatch"
        return EXIT_FAIL, assertions, extensions

    # 2. Fresh virtual environments and wheel installs.
    venv_root = out_dir / "venvs"
    if venv_root.exists():
        shutil.rmtree(venv_root)
    venv_root.mkdir(parents=True)
    install_rows: list[dict[str, Any]] = []
    wheel_by_role = {
        role: release_dir / pattern.format(version=version) for role, pattern in ROLE_WHEELS.items()
    }
    wheel_by_role["root"] = root_wheel
    install_failed = False
    for role, wheel in wheel_by_role.items():
        venv = venv_root / role
        create = _run(
            [python, "-m", "venv", str(venv)],
            cwd=out_dir,
            timeout=min(timeout, 300),
            log=logs / f"venv-{role}.log",
        )
        row: dict[str, Any] = {
            "role": role,
            "venv": str(venv),
            "wheel": str(wheel),
            "venv_exit": create.returncode,
            "install_exit": None,
            "ok": False,
        }
        if create.returncode == 0:
            command = [
                str(_venv_python(venv)),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--find-links",
                str(release_dir),
                str(wheel),
            ]
            if role == "root":
                command.extend(["--find-links", str(root_wheel_dir)])
            install = _run(
                command,
                cwd=out_dir,
                timeout=max(timeout, 600),
                log=logs / f"install-{role}.log",
            )
            row["install_exit"] = install.returncode
            row["ok"] = install.returncode == 0
        install_rows.append(row)
        if not row["ok"]:
            install_failed = True
    install_document = _write_json(out_dir / "install-results.json", install_rows)
    artifact(install_document)
    add(
        "b25.clean_install",
        "PASS" if not install_failed else "FAIL",
        "four fresh venvs installed"
        if not install_failed
        else "install failed: see install-results.json",
    )
    if install_failed:
        extensions["b25.reason"] = "clean_install_failed"
        return EXIT_FAIL, assertions, extensions

    # 3. Installed-location and version probes.
    probe_code = """
import hashlib, importlib.util, json, os, sys
from importlib import metadata
role = sys.argv[1]
expected = sys.argv[2]
facts = {"role": role, "python": sys.executable, "cwd": os.getcwd()}
if role == "cccp":
    import cccp
    facts["modules"] = {"cccp": cccp.__file__}
    facts["version"] = cccp.__version__
    facts["forbidden"] = {
        name: importlib.util.find_spec(name) is None for name in ("acp", "acp_services", "fastapi")
    }
elif role == "worker":
    import acp_services
    facts["modules"] = {"acp_services": acp_services.__file__}
    facts["version"] = metadata.version("acp-worker")
    facts["forbidden"] = {
        name: importlib.util.find_spec(name) is None for name in ("acp", "fastapi")
    }
elif role == "web":
    import acp, acp_services, acp_web_assets
    facts["modules"] = {
        "acp": acp.__file__,
        "acp_services": acp_services.__file__,
        "acp_web_assets": acp_web_assets.__file__,
    }
    facts["version"] = acp.__version__
    facts["forbidden"] = {}
else:
    import acp, acp_services, cccp
    facts["modules"] = {
        "acp": acp.__file__,
        "acp_services": acp_services.__file__,
        "cccp": cccp.__file__,
    }
    facts["version"] = acp.__version__
    facts["forbidden"] = {}
facts["site_packages"] = all("/site-packages/" in path for path in facts["modules"].values())
facts["version_ok"] = facts["version"] == expected
facts["forbidden_absent"] = all(facts["forbidden"].values())
print(json.dumps(facts))
"""
    probe_rows: dict[str, Any] = {}
    probes_ok = True
    for role in ("cccp", "worker", "web", "root"):
        completed = _run(
            [str(_venv_python(venv_root / role)), "-c", probe_code, role, version],
            cwd=out_dir,
            timeout=min(timeout, 120),
            log=logs / f"probe-{role}.log",
        )
        document: dict[str, Any] = {"exit": completed.returncode}
        if completed.returncode == 0:
            try:
                document.update(json.loads(completed.stdout.strip().splitlines()[-1]))
            except (json.JSONDecodeError, IndexError):
                document["error"] = "probe output not JSON"
        else:
            document["error"] = completed.stderr[-2000:]
        probe_rows[role] = document
        if not (
            completed.returncode == 0
            and document.get("site_packages")
            and document.get("version_ok")
            and document.get("forbidden_absent")
        ):
            probes_ok = False
    probe_document = _write_json(out_dir / "installed-probes.json", probe_rows)
    artifact(probe_document)
    add(
        "b25.installed_paths_and_versions",
        "PASS" if probes_ok else "FAIL",
        "all modules resolve under venv site-packages at the expected version"
        if probes_ok
        else "installed module/version probe failed",
    )
    if not probes_ok:
        extensions["b25.reason"] = "installed_probe_failed"
        return EXIT_FAIL, assertions, extensions

    # 4. CCCP sample without a Controller or ACP import.
    sample = out_dir / "cccp-sample.py"
    sample.write_text(CCCP_SAMPLE, encoding="utf-8")
    sample_run = _run(
        [str(_venv_python(venv_root / "cccp")), str(sample)],
        cwd=out_dir,
        timeout=min(timeout, 180),
        log=logs / "cccp-sample.log",
    )
    sample_facts: dict[str, Any] = {"exit": sample_run.returncode}
    try:
        sample_facts.update(json.loads(sample_run.stdout.strip().splitlines()[-1]))
    except (json.JSONDecodeError, IndexError):
        sample_facts["stderr"] = sample_run.stderr[-2000:]
    sample_document = _write_json(out_dir / "cccp-sample.json", sample_facts)
    artifact(sample_document)
    cccp_sample_ok = bool(
        sample_run.returncode == 0
        and sample_facts.get("complete") is True
        and sample_facts.get("energy") == -1.0
        and "/site-packages/" in str(sample_facts.get("cccp", ""))
    )
    add(
        "b25.cccp_sample_without_controller",
        "PASS" if cccp_sample_ok else "FAIL",
        "controlled CCCP singlepoint ran without acp/acp_services"
        if cccp_sample_ok
        else "CCCP independent sample failed",
    )
    if not cccp_sample_ok:
        extensions["b25.reason"] = "cccp_sample_failed"
        return EXIT_FAIL, assertions, extensions

    # 5. Standalone CLI and compatibility imports.
    web_python = str(_venv_python(venv_root / "web"))
    root_python = str(_venv_python(venv_root / "root"))
    cli_rows: dict[str, Any] = {}
    for role, executable in (("web", web_python), ("root", root_python)):
        help_run = _run(
            [executable, "-m", "acp", "--help"],
            cwd=out_dir,
            timeout=min(timeout, 120),
            log=logs / f"cli-{role}-help.log",
        )
        cli_rows[role] = {
            "exit": help_run.returncode,
            "has_run": "run" in help_run.stdout and "doctor" in help_run.stdout,
        }
    compat_code = """
import importlib, json
from acp.calculations import primitives
from acp import backends
modules = ["acp.calculations.primitives", "acp.backends", "acp.compat.legacy.manifests"]
facts = {"modules": {}}
for name in modules:
    facts["modules"][name] = importlib.import_module(name).__file__
facts["store_identity"] = (
    importlib.import_module("acp.scheduler.store").JobStore
    is importlib.import_module("acp_services.store.store").JobStore
)
facts["contracts_identity"] = (
    importlib.import_module("acp.calculations.contracts").CalculationPlan
    is importlib.import_module("acp_services.runtime.plan_contracts").CalculationPlan
)
print(json.dumps(facts))
"""
    compat_run = _run(
        [web_python, "-c", compat_code],
        cwd=out_dir,
        timeout=min(timeout, 120),
        log=logs / "compat-imports.log",
    )
    compat_facts: dict[str, Any] = {"exit": compat_run.returncode}
    try:
        compat_facts.update(json.loads(compat_run.stdout.strip().splitlines()[-1]))
    except (json.JSONDecodeError, IndexError):
        compat_facts["stderr"] = compat_run.stderr[-2000:]
    cli_rows["compat"] = compat_facts
    cli_document = _write_json(out_dir / "cli-and-compat.json", cli_rows)
    artifact(cli_document)
    cli_ok = all(row["exit"] == 0 for row in (cli_rows["web"], cli_rows["root"])) and bool(
        cli_rows["web"]["has_run"]
    )
    compat_ok = bool(
        compat_facts.get("exit") == 0
        and compat_facts.get("store_identity")
        and compat_facts.get("contracts_identity")
        and len(compat_facts.get("modules", {})) == 3
    )
    add("b25.standalone_cli", "PASS" if cli_ok else "FAIL", "acp --help in web and root role venvs")
    add(
        "b25.compat_imports",
        "PASS" if compat_ok else "FAIL",
        "primitives/backends/compat.legacy import and identity forwarders hold",
    )
    if not (cli_ok and compat_ok):
        extensions["b25.reason"] = "cli_or_compat_failed"
        return EXIT_FAIL, assertions, extensions

    # 6. Lab server and stdio MCP on isolated loopback resources.
    port = _free_port()
    lab_root = out_dir / "lab-run-root"
    if lab_root.exists():
        shutil.rmtree(lab_root)
    lab_root.mkdir(parents=True)
    scheduler_task = fixture.get("scheduler_task")
    scheduler_task = scheduler_task if isinstance(scheduler_task, dict) else None
    lab_env = {
        "ACP_RUN_ROOT": str(lab_root),
        "ACP_CANDIDATE_REVISION": str(candidate_revision),
    }
    if scheduler_task is not None:
        orca_path = str(scheduler_task.get("orca_path") or "").strip()
        if orca_path:
            lab_env["CONFSEARCH_ORCA_PATH"] = orca_path
        orca_ld = str(scheduler_task.get("orca_ld_library_path") or "").strip()
        if orca_ld:
            lab_env["CONFSEARCH_ORCA_LD_LIBRARY_PATH"] = orca_ld
    lab_log = logs / "lab-server.log"
    with lab_log.open("w", encoding="utf-8") as stream:
        lab = subprocess.Popen(
            [
                str(_venv_python(venv_root / "web")),
                "-m",
                "acp",
                "run",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--run-root",
                str(lab_root),
                "--no-browser",
            ],
            cwd=str(lab_root),
            env={**os.environ, **lab_env, "PYTHONPATH": ""},
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    health: dict[str, Any] = {}
    lab_ok = False
    mcp_ok = False
    scheduler_ok = False
    scheduler_facts: dict[str, Any] = {}
    try:
        deadline = time.monotonic() + min(timeout, 120)
        last_error = ""
        while time.monotonic() < deadline:
            if lab.poll() is not None:
                last_error = "process exited before health check"
                break
            try:
                health = _http_json(f"http://127.0.0.1:{port}/api/health", 2)
                lab_ok = health.get("status") == "ok"
                if lab_ok:
                    break
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last_error = str(exc)
            time.sleep(0.2)
        if not lab_ok:
            health["error"] = last_error
        _write_json(out_dir / "lab-health.json", health)
        artifact(out_dir / "lab-health.json")
        health_revision = health.get("candidate_revision")
        health_identity_ok = lab_ok and health_revision == candidate_revision
        add(
            "b25.health_candidate_revision",
            "PASS" if health_identity_ok else "FAIL",
            f"installed Lab health candidate_revision={health_revision!r}"
            if lab_ok
            else "installed Lab health unavailable",
        )
        add(
            "b25.lab_server",
            "PASS" if lab_ok else "FAIL",
            f"health on 127.0.0.1:{port} isolated run root"
            if lab_ok
            else f"Lab server did not become healthy: {health.get('error')}",
        )
        if lab_ok and not health_identity_ok:
            extensions["b25.reason"] = "health_candidate_revision_mismatch"
            return EXIT_FAIL, assertions, extensions
        if lab_ok:
            mcp_code = """
import json, subprocess, sys
argv = [sys.argv[1], "mcp", "serve", "--controller", sys.argv[2]]
proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def send(obj):
    proc.stdin.write((json.dumps(obj) + "\\n").encode()); proc.stdin.flush()
def recv():
    line = proc.stdout.readline()
    if not line:
        raise SystemExit("eof")
    return json.loads(line)
try:
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "b25b-package-install", "version": "1"}}})
    init = recv()
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = recv()
    names = sorted(item["name"] for item in tools["result"]["tools"])
    print(json.dumps({"protocol": init["result"]["protocolVersion"], "tools": names}))
finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    err = proc.stderr.read().decode(errors="replace")
    open(sys.argv[3], "w", encoding="utf-8").write(err)
"""
            mcp_run = _run(
                [
                    web_python,
                    "-c",
                    mcp_code,
                    str(venv_root / "web" / "bin" / "acp"),
                    f"http://127.0.0.1:{port}",
                    str(logs / "mcp-server.stderr.log"),
                ],
                cwd=out_dir,
                timeout=min(timeout, 120),
                log=logs / "mcp-client.log",
            )
            mcp_facts: dict[str, Any] = {"exit": mcp_run.returncode}
            try:
                mcp_facts.update(json.loads(mcp_run.stdout.strip().splitlines()[-1]))
            except (json.JSONDecodeError, IndexError):
                mcp_facts["stderr"] = mcp_run.stderr[-2000:]
            _write_json(out_dir / "mcp-tools.json", mcp_facts)
            artifact(out_dir / "mcp-tools.json")
            mcp_ok = bool(
                mcp_run.returncode == 0
                and mcp_facts.get("protocol") == "2025-06-18"
                and mcp_facts.get("tools") == list(TOOL_NAMES)
            )
            add(
                "b25.stdio_mcp_eight_tools",
                "PASS" if mcp_ok else "FAIL",
                "initialize 2025-06-18 and eight P1 tools"
                if mcp_ok
                else "stdio MCP discovery failed",
            )
        if lab_ok and scheduler_task is not None:
            scheduler_ok, scheduler_facts = _package_install_scheduler_probe(
                base=f"http://127.0.0.1:{port}",
                web_python=web_python,
                lab_root=lab_root,
                out_dir=out_dir,
                logs=logs,
                task=scheduler_task,
                timeout=timeout,
            )
            for path in sorted((out_dir / "scheduler-task").glob("*")):
                if path.is_file():
                    artifact(path)
            extensions["b25.scheduler_task"] = scheduler_facts
            add(
                "b25.scheduler_task",
                "PASS" if scheduler_ok else "FAIL",
                "installed Lab completed one real H2/STO-3G singlepoint with "
                "task_result/result_manifest/receipt release identity"
                if scheduler_ok
                else "installed Lab scheduler task did not complete with valid artifacts",
            )
    finally:
        if lab.poll() is None:
            lab.terminate()
            try:
                lab.wait(timeout=20)
            except subprocess.TimeoutExpired:
                lab.kill()
                lab.wait(timeout=20)
    extensions["b25.lab_port"] = port
    if not lab_ok:
        extensions["b25.reason"] = "lab_server_failed"
        return EXIT_FAIL, assertions, extensions
    if not mcp_ok:
        extensions["b25.reason"] = "mcp_stdio_failed"
        return EXIT_FAIL, assertions, extensions
    if scheduler_task is None:
        add(
            "b25.scheduler_task",
            "NOT_VERIFIED",
            "fixture has no scheduler_task block (ORCA path); installed scheduler "
            "execution was not exercised",
        )
    elif not scheduler_ok:
        extensions["b25.reason"] = "scheduler_task_failed"
        return EXIT_FAIL, assertions, extensions
    # 7. Assets, notices, licenses, sample config, schema, SDK, docs.
    assets_code = """
import json, pathlib, sys
from importlib import metadata
facts = {"dist_info_license": {}, "model_assets": {}, "missing": []}
for dist in ("acp-web", "acp-worker"):
    try:
        info = metadata.distribution(dist)
    except metadata.PackageNotFoundError:
        facts["missing"].append(f"dist:{dist}")
        continue
    license_files = [
        str(path)
        for path in (info.files or [])
        if str(path).endswith(("LICENSE", "LICENSE.txt", "LICENSE.md"))
    ]
    facts["dist_info_license"][dist] = license_files
    if not license_files:
        facts["missing"].append(f"license:{dist}")
import cccp
assets = pathlib.Path(cccp.__file__).parent / "analysis/nmr/model_assets"
for name in ("NOTICE.md", "LICENSE-DP5", "atomic_reps.gz", "frag_reps.gz"):
    path = assets / name
    facts["model_assets"][name] = path.is_file()
    if not path.is_file():
        facts["missing"].append(f"asset:{name}")
facts["py_typed"] = (pathlib.Path(cccp.__file__).parent / "py.typed").is_file()
facts["agents_docs"] = [
    str(path) for path in (pathlib.Path(cccp.__file__).parent).rglob("AGENTS.md")
]
if not facts["agents_docs"]:
    facts["missing"].append("docs:cccp/AGENTS.md")
import cccp.plugin.sdk
facts["sdk"] = hasattr(cccp.plugin.sdk, "PluginOutputSpec") and hasattr(
    cccp.plugin.sdk, "invoke_capability"
)
if not facts["sdk"]:
    facts["missing"].append("sdk:cccp.plugin.sdk")
import acp.api.schemas
facts["schema_module"] = True
print(json.dumps(facts))
"""
    assets_run = _run(
        [web_python, "-c", assets_code],
        cwd=out_dir,
        timeout=min(timeout, 120),
        log=logs / "assets.log",
    )
    assets_facts: dict[str, Any] = {"exit": assets_run.returncode}
    try:
        assets_facts.update(json.loads(assets_run.stdout.strip().splitlines()[-1]))
    except (json.JSONDecodeError, IndexError):
        assets_facts["stderr"] = assets_run.stderr[-2000:]
    sample_config = Path(
        str(fixture.get("sample_config") or (release_dir / "docs" / "sample-config.yaml"))
    )
    schema_files = [Path(str(item)) for item in fixture.get("schema_files", [])]
    doc_files = [Path(str(item)) for item in fixture.get("doc_files", [])]
    for path in (sample_config, *schema_files, *doc_files):
        if not path.is_file():
            assets_facts.setdefault("missing", []).append(f"file:{path}")
    root_license = sorted((venv_root / "root").glob("**/acp-*.dist-info/LICENSE"))
    assets_facts["root_acp_license"] = [str(path) for path in root_license]
    if not root_license:
        assets_facts.setdefault("missing", []).append("license:acp")
    assets_facts["sample_config"] = str(sample_config)
    _write_json(out_dir / "assets-audit.json", assets_facts)
    artifact(out_dir / "assets-audit.json")
    assets_ok = bool(
        assets_run.returncode == 0
        and not assets_facts.get("missing")
        and assets_facts.get("py_typed")
        and assets_facts.get("schema_module")
        and assets_facts.get("sdk")
    )
    add(
        "b25.assets_notices_licenses_config_schema_sdk_docs",
        "PASS" if assets_ok else "FAIL",
        "assets/notices/licenses/model assets/py.typed/sample config/schema/SDK/docs present"
        if assets_ok
        else "missing: " + ",".join(assets_facts.get("missing", [])),
    )
    if not assets_ok:
        extensions["b25.reason"] = "asset_audit_failed"
        return EXIT_FAIL, assertions, extensions

    # 8. Four version locations stay on the expected release version.
    source_root = Path(str(fixture.get("source_root") or "")).resolve()
    try:
        locations = _read_version_locations(source_root)
    except (OSError, ValueError, KeyError) as exc:
        add("b25.four_version_locations", "NOT_VERIFIED", f"source_root unusable: {exc}")
        extensions["b25.version_locations"] = {"error": str(exc)}
    else:
        version_ok = set(locations.values()) == {version}
        _write_json(out_dir / "version-locations.json", locations)
        artifact(out_dir / "version-locations.json")
        add(
            "b25.four_version_locations",
            "PASS" if version_ok else "FAIL",
            f"four version locations == {version}" if version_ok else f"mismatch: {locations}",
        )
        if not version_ok:
            extensions["b25.reason"] = "version_locations_mismatch"
            return EXIT_FAIL, assertions, extensions

    # 9. Aggregate A/C raw evidence without re-signing it.
    evidence_rows: list[dict[str, Any]] = []
    for row in fixture.get("a_c_evidence", DEFAULT_A_C_EVIDENCE):
        identifier = str(row.get("id") or "")
        path = Path(str(row.get("path") or ""))
        present = path.exists()
        evidence_rows.append(
            {
                "id": identifier,
                "path": str(path),
                "present": present,
                "status": "PASS" if present else "NOT_VERIFIED",
                "note": "raw evidence present; scientific/experience sign-off stays with its owner"
                if present
                else "evidence absent at the registered path",
            }
        )
        add(
            f"b25.aggregate_{identifier}",
            "PASS" if present else "NOT_VERIFIED",
            f"raw evidence at {path}" if present else f"expected path absent: {path}",
        )
    _write_json(out_dir / "a-c-evidence.json", evidence_rows)
    artifact(out_dir / "a-c-evidence.json")
    extensions["b25.a_c_evidence"] = evidence_rows

    # 10. C static-asset manifest mount (B-req-06).
    static_path = Path(str(fixture.get("static_assets_manifest") or DEFAULT_STATIC_MANIFEST))
    if not static_path.is_absolute():
        static_path = source_root / static_path
    if not static_path.is_file():
        static_row = {
            "status": "NOT_VERIFIED(c_static_assets_absent)",
            "expected_path": str(static_path),
            "mount": "/experience/<release_id>/",
            "note": "C has not published experience/static-assets.manifest.json; "
            "no mount was fabricated",
        }
        add(
            "b25.static_assets_mount",
            "NOT_VERIFIED",
            f"NOT_VERIFIED(c_static_assets_absent) expected {static_path}",
        )
    else:
        try:
            manifest_document = json.loads(static_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            static_row = {
                "status": "FAIL",
                "expected_path": str(static_path),
                "error": str(exc),
            }
            add("b25.static_assets_mount", "FAIL", f"manifest unreadable: {exc}")
            extensions["b25.static_assets"] = static_row
            extensions["b25.reason"] = "static_assets_manifest_invalid"
            return EXIT_FAIL, assertions, extensions
        digest_rows = []
        for entry in manifest_document.get("assets", []):
            relative = str(entry.get("path") or "")
            expected = str(entry.get("sha256") or "")
            candidate = static_path.parent / relative
            actual = _sha256(candidate) if candidate.is_file() else None
            digest_rows.append(
                {
                    "path": relative,
                    "expected": expected,
                    "actual": actual,
                    "ok": bool(actual) and actual == expected,
                }
            )
        static_row = {
            "status": "PASS" if digest_rows and all(row["ok"] for row in digest_rows) else "FAIL",
            "expected_path": str(static_path),
            "mount": "/experience/<release_id>/",
            "digests": digest_rows,
        }
        _write_json(out_dir / "static-assets.json", static_row)
        artifact(out_dir / "static-assets.json")
        add(
            "b25.static_assets_mount",
            static_row["status"],
            f"{len(digest_rows)} asset digests verified read-only"
            if static_row["status"] == "PASS"
            else "static asset digest mismatch",
        )
    _write_json(out_dir / "static-assets-status.json", static_row)
    artifact(out_dir / "static-assets-status.json")
    extensions["b25.static_assets"] = static_row

    # 11. Cleanup harness-owned venvs after evidence is captured.
    shutil.rmtree(venv_root, ignore_errors=True)
    shutil.rmtree(lab_root, ignore_errors=True)
    add("b25.cleanup", "PASS", "harness venvs/processes removed; production untouched")

    failed = [row["name"] for row in assertions if row["status"] == "FAIL"]
    if failed:
        extensions["b25.reason"] = "assertion_failed:" + ",".join(failed)
        return EXIT_FAIL, assertions, extensions
    return EXIT_PASS, assertions, extensions
