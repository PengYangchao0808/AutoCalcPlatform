"""M5-01 clean-room install: the whole product from the artifact store only.

Every artifact is taken from ``--store`` (the BOM's component URIs); the source
checkout is never on ``sys.path`` and the working directory is outside the
repository. The receipt proves, in fresh virtual environments:

* root ``acp`` distribution — imports resolve inside the venv, ``acp doctor``,
  the stdio MCP handshake (protocol ``2025-06-18`` + the eight tools) and the
  packaged Workbench (HTML/JS/CSS, one ``/api/capabilities`` call, read-only
  mounts, traversal/unknown 404, startup digest verification);
* the three role distributions — the mutually exclusive alternative install;
* the RPH self-contained offline install surface — ``pnpm install --offline
  --frozen-lockfile`` in a copied directory, the shipped ``verify-install.mjs``,
  the ``rph`` bin and the static ``rph doctor``;
* the M4-05 third-party plugin wheel — installable and discoverable through the
  ``cccp.science_plugins`` entry point, with nothing from a package index.

No QC binary and no model is invoked: the scientific side stops at service
startup, a version line and protocol handshakes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BOM_REL = "docs/reference/next-change/acp2-contracts/release/bom.current.json"
DEFAULT_STORE = (
    Path(os.environ.get("ACP2_ARTIFACT_STORE", "/ACPServer/acp2-artifacts/remediation")) / "m5-01"
)
DEFAULT_WORK = Path("/ACPServer/acp2-runs/remediation/m5-01-install")
DEFAULT_OUT = REPO / "reports" / "acp2_remediation_20261026" / "m5-01"
SCHEMA = "acp2.m5-01.install-receipt.v1"
MCP_TOOLS = (
    "acp_analyze",
    "acp_cancel",
    "acp_capabilities",
    "acp_get_guidance",
    "acp_preflight",
    "acp_prepare_artifact",
    "acp_read_artifact",
    "acp_status",
    "acp_submit",
)
VERSION = "0.3.0"
#: F2 (M5-03): every product entry point must refuse a mixed root/roles install
#: with this typed exit code (``sysexits.h`` EX_CONFIG) and message marker.
MUTUAL_EXCLUSION_EXIT = 78
MUTUAL_EXCLUSION_CODE = "mutually_exclusive_distributions"


def offline_pip_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment that *proves* pip cannot reach a package index (F1).

    ``--no-index`` already forbids index access; the dead-loopback proxies turn
    any accidental network attempt into a hard failure instead of a silent
    online install, so the receipt's "offline" claim is observed, not assumed.
    """
    dead = "http://127.0.0.1:9"
    env = clean_env(
        {
            "PIP_NO_INDEX": "1",
            "PIP_NO_BUILD_ISOLATION": "1",
            "http_proxy": dead,
            "https_proxy": dead,
            "HTTP_PROXY": dead,
            "HTTPS_PROXY": dead,
            "no_proxy": "",
            "NO_PROXY": "",
        }
    )
    if extra:
        env.update(extra)
    return env


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Receipt:
    """Collect step results; every check records what it actually observed."""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: Any = "") -> None:
        self.steps.append({"name": name, "status": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            raise SystemExit(f"STEP FAILED: {name}: {detail}")

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [step for step in self.steps if step["status"] == "FAIL"]


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "ACP_CONTROLLER_URL", "ACP2_CONTROLLER_URL"):
        env.pop(name, None)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    if extra:
        env.update(extra)
    return env


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Path,
    timeout: float = 900.0,
    accept: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False
    )
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "$ "
        + " ".join(argv)
        + "\n\n"
        + (completed.stdout or "")
        + (("\n[stderr]\n" + completed.stderr) if completed.stderr else "")
        + f"\n[exit {completed.returncode}]\n",
        encoding="utf-8",
    )
    if completed.returncode not in accept:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{completed.stderr[-4000:]}"
        )
    return completed


def venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def venv_exe(venv: Path, name: str) -> Path:
    return venv / "bin" / name


def create_venv(path: Path, log_dir: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [sys.executable, "-m", "venv", str(path)],
        cwd=path.parent,
        env=clean_env(),
        log=log_dir / f"venv-{path.name}.log",
        timeout=300,
    )
    return venv_python(path)


def pip_install(
    python: Path,
    argv: list[str],
    *,
    log: Path,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    install_env = env or clean_env()
    run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", *argv],
        cwd=cwd,
        env=install_env,
        log=log,
        timeout=1800,
    )
    freeze = run(
        [str(python), "-m", "pip", "freeze"],
        cwd=cwd,
        env=install_env,
        log=log.with_name(log.stem + "-freeze.log"),
        timeout=300,
    ).stdout.splitlines()
    return {"argv": argv, "installed": freeze, "seconds": None, "offline": True}


def installed_distributions(python: Path, *, cwd: Path) -> set[str]:
    script = (
        "import json, importlib.metadata as m;"
        "print(json.dumps(sorted({d.metadata['Name'] for d in m.distributions() "
        "if d.metadata['Name']})))"
    )
    completed = run(
        [str(python), "-c", script],
        cwd=cwd,
        env=clean_env(),
        log=cwd / "distributions.log",
        timeout=120,
    )
    return set(json.loads(completed.stdout))


DISTRIBUTION_GROUPS = {
    "root": {"acp"},
    "roles": {"acp-web", "acp-worker", "cccp"},
}


def assert_exclusive(python: Path, group: str, *, cwd: Path) -> None:
    """Refuse to mix the root ``acp`` distribution with the role bundle."""
    present = installed_distributions(python, cwd=cwd)
    installed = {name for name, members in DISTRIBUTION_GROUPS.items() if present & members}
    if installed and installed != {group}:
        raise RuntimeError(
            "release contract forbids mixing distributions: "
            f"{sorted(installed)} already installed, refusing the {group!r} set"
        )


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def http_get(url: str, *, timeout: float = 20.0, method: str = "GET") -> tuple[int, dict, bytes]:
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.status, headers, response.read()
    except urllib.error.HTTPError as exc:
        headers = {key.lower(): value for key, value in (exc.headers or {}).items()}
        return exc.code, headers, exc.read()


def raw_http(port: int, target: str, *, method: str = "GET") -> tuple[int, bytes]:
    """Send *target* verbatim (urllib would normalise ``..`` away)."""
    with socket.create_connection(("127.0.0.1", port), timeout=20) as connection:
        request = (
            f"{method} {target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        chunks = []
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    payload = b"".join(chunks)
    status = int(payload.split(b" ", 2)[1])
    return status, payload


def _json(completed: subprocess.CompletedProcess) -> dict[str, Any]:
    return json.loads(completed.stdout)


@contextlib.contextmanager
def controller(venv: Path, *, work: Path, log_dir: Path, name: str) -> Any:
    """Start the real Controller from the installed venv; yield its base URL."""
    run_root = work / f"run-root-{name}"
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    port = free_port()
    log = log_dir / f"{name}-controller.log"
    env = clean_env(
        {
            "ACP_RUN_ROOT": str(run_root),
            "ACP_HOST": "127.0.0.1",
            "ACP_PORT": str(port),
            "HOME": str(_isolated_home(work)),
        }
    )
    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                str(venv_python(venv)),
                "-m",
                "uvicorn",
                "acp.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=work,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            wait_for(base + "/api/capabilities", process=process, log=log)
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)


def mcp_handshake(venv: Path, controller: str, *, log: Path) -> dict[str, Any]:
    """Real stdio JSON-RPC: initialize, notifications/initialized, tools/list."""
    process = subprocess.Popen(
        [
            str(venv_exe(venv, "acp")),
            "mcp",
            "serve",
            "--transport",
            "stdio",
            "--controller",
            controller,
            "--log-level",
            "WARNING",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=clean_env(),
    )
    transcript: list[dict[str, Any]] = []

    def exchange(payload: dict[str, Any], *, expect: bool = True) -> dict[str, Any]:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        if not expect:
            return {}
        line = process.stdout.readline()
        response = json.loads(line) if line.strip() else {}
        transcript.append(response)
        return response

    try:
        pre = exchange({"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}})
        initialized = exchange(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "acp2-m5-01-install", "version": "1"},
                },
            }
        )
        exchange({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect=False)
        listed = exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
        stderr = process.stderr.read() if process.stderr is not None else ""
        log.write_text(
            json.dumps(
                {"pre_initialize": pre, "initialize": initialized, "tools": listed},
                indent=2,
            )
            + "\n[stderr]\n"
            + stderr,
            encoding="utf-8",
        )
    tools = [tool["name"] for tool in listed["result"]["tools"]]
    return {
        "protocol_version": initialized["result"]["protocolVersion"],
        "server": initialized["result"].get("serverInfo"),
        "tools": sorted(tools),
        "pre_initialize_error": (pre.get("error") or {}).get("message"),
        "pre_initialize_code": ((pre.get("error") or {}).get("data") or {}).get("code"),
    }


def wait_for(url: str, *, process: subprocess.Popen, log: Path, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, _ = http_get(url)
            if status == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{log.read_text()[-4000:]}")
        time.sleep(0.1)
    raise RuntimeError(f"server did not answer {url} in {timeout}s:\n{log.read_text()[-4000:]}")


def workbench_probe(
    *,
    venv: Path,
    work: Path,
    log_dir: Path,
    store_static_manifest: dict[str, Any],
) -> dict[str, Any]:
    python = venv_python(venv)
    run_root = work / "run-root-web"
    if run_root.exists():
        shutil.rmtree(run_root)
    run_root.mkdir(parents=True)
    port = free_port()
    log = log_dir / "workbench-server.log"
    env = clean_env(
        {
            "ACP_RUN_ROOT": str(run_root),
            "ACP_HOST": "127.0.0.1",
            "ACP_PORT": str(port),
            "HOME": str(_isolated_home(work)),
        }
    )
    with log.open("w") as stream:
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "acp.api.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=work,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        facts: dict[str, Any] = {}
        try:
            base = f"http://127.0.0.1:{port}"
            wait_for(base + "/", process=process, log=log)
            assets = Path(
                run(
                    [
                        str(python),
                        "-c",
                        "from acp.api.server import _FRONTEND_DIR; print(_FRONTEND_DIR)",
                    ],
                    cwd=work,
                    env=env,
                    log=log_dir / "frontend-dir.log",
                    timeout=120,
                ).stdout.strip()
            )
            status, headers, body = http_get(base + "/")
            html = (assets / "ACP_Workbench_v2.html").read_bytes()
            facts["index"] = {
                "status": status,
                "matches_packaged_html": body == html,
                "frontend_dir": str(assets),
                "served_from_venv": str(assets).startswith(str(venv)),
                "checkout_absent": str(REPO / "frontend") != str(assets),
            }
            js_rel = next(
                path.relative_to(assets).as_posix()
                for path in sorted((assets / "js").rglob("*.js"))
            )
            status, headers, body = http_get(f"{base}/{js_rel}")
            facts["asset"] = {
                "path": js_rel,
                "status": status,
                "matches_bytes": body == (assets / js_rel).read_bytes(),
                "cache_control": headers.get("cache-control"),
            }
            status, headers, body = http_get(base + "/api/capabilities")
            capabilities = json.loads(body)
            facts["capabilities"] = {
                "status": status,
                "keys": sorted(capabilities),
                "workflows": len(capabilities.get("workflows") or []),
                "tools": sorted(capabilities.get("tools") or []),
                "protocol_version": capabilities.get("protocol_version"),
            }
            status, _, body = http_get(base + "/api/v2/science/capabilities")
            facts["science_capabilities"] = {
                "status": status,
                "keys": sorted(json.loads(body)),
            }
            status, _, _ = http_get(
                base + "/api/v2/research/proposals/prop_m501_unknown?project_id=proj_m501_probe"
            )
            facts["research_read_unknown"] = {"status": status}
            status, _, _ = http_get(base + "/js/definitely-not-a-file.js")
            facts["unknown_asset"] = {"status": status}
            status, _ = raw_http(port, "/js/../../../../etc/passwd")
            facts["traversal_literal"] = {"status": status}
            status, _ = raw_http(port, "/js/%2e%2e%2f%2e%2e%2fetc%2fpasswd")
            facts["traversal_encoded"] = {"status": status}
            status, _, _ = http_get(base + "/css/workbench/base.css", method="POST")
            facts["post_to_static"] = {"status": status}
            status, _, _ = http_get(f"{base}/{js_rel}", method="HEAD")
            facts["head_asset"] = {"status": status}
        finally:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)

    verify_script = (
        "import json, sys; from acp.api.static_assets import verify_manifest;"
        "print(json.dumps(verify_manifest(sys.argv[1])))"
    )
    verified = run(
        [str(python), "-c", verify_script, str(assets)],
        cwd=work,
        env=clean_env(),
        log=log_dir / "static-digest.log",
        timeout=300,
    )
    packaged = json.loads(verified.stdout)
    facts["digest"] = {
        "verified": True,
        "digest": packaged["digest"],
        "matches_bom": packaged["digest"] == store_static_manifest["digest"],
        "files": packaged["counts"]["files"],
        "release_id": packaged["release_id"],
    }
    tampered = work / "tampered-assets"
    if tampered.exists():
        shutil.rmtree(tampered)
    shutil.copytree(assets, tampered)
    target = tampered / js_rel
    target.write_bytes(target.read_bytes() + b"\n// tampered\n")
    tamper = run(
        [str(python), "-c", verify_script, str(tampered)],
        cwd=work,
        env=clean_env(),
        log=log_dir / "static-digest-tampered.log",
        timeout=300,
        accept=(0, 1),
    )
    facts["digest"]["tamper_rejected"] = tamper.returncode != 0 and "mismatch" in (
        tamper.stderr + tamper.stdout
    )
    facts["digest"]["tamper_exit"] = tamper.returncode
    return facts


def rph_install_probe(*, store: Path, work: Path, log_dir: Path) -> dict[str, Any]:
    source = store / "rph" / "install-surface"
    target = work / "rph-install"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    env = clean_env()
    install = run(
        ["pnpm", "install", "--offline", "--frozen-lockfile", "--ignore-scripts"],
        cwd=target,
        env=env,
        log=log_dir / "rph-install.log",
        timeout=900,
    )
    verify = run(
        ["node", "verify-install.mjs"],
        cwd=target,
        env=env,
        log=log_dir / "rph-verify-install.log",
        timeout=300,
    )
    bin_path = target / "node_modules" / ".bin" / "rph"
    elsewhere = work / "rph-elsewhere"
    elsewhere.mkdir(exist_ok=True)
    version = run(
        [str(bin_path), "--version"],
        cwd=elsewhere,
        env=env,
        log=log_dir / "rph-version.log",
        timeout=120,
    )
    doctor = run(
        [str(bin_path), "doctor", "--json"],
        cwd=elsewhere,
        env=env,
        log=log_dir / "rph-doctor.log",
        timeout=120,
        accept=(0, 1),
    )
    report = _json(doctor)
    return {
        "surface": str(source),
        "installed_at": str(target),
        "pnpm_install_exit": install.returncode,
        "verify_exit": verify.returncode,
        "verify_stdout_tail": verify.stdout.strip().splitlines()[-5:],
        "bin": str(bin_path),
        "bin_version": version.stdout.strip(),
        "doctor_schema": report.get("schema"),
        "doctor_mode": report.get("mode"),
        "doctor_status": report.get("status"),
        "doctor_checks": len(report.get("checks") or []),
        "packages_present": {
            name: (target / "node_modules" / "@rph" / name).is_dir()
            for name in ("research", "bridge", "dsh-bundle", "cli")
        },
    }


def plugin_install_probe(*, store: Path, work: Path, log_dir: Path) -> dict[str, Any]:
    wheel = next((store / "plugins").glob("acp2_external_science_validator-*.whl"))
    venv = work / "venvs" / "plugin"
    python = create_venv(venv, log_dir)
    installed = pip_install(
        python,
        ["--no-deps", "--no-index", str(wheel)],
        log=log_dir / "pip-plugin.log",
        cwd=work,
    )
    script = (
        "import json, importlib.metadata as m;"
        "d = m.distribution('acp2-external-science-validator');"
        "eps = [e for e in d.entry_points if e.group == 'cccp.science_plugins'];"
        "print(json.dumps({'name': d.metadata['Name'], 'version': d.version,"
        " 'entry_points': [e.name for e in eps]}))"
    )
    probe = run(
        [str(python), "-c", script],
        cwd=work,
        env=clean_env(),
        log=log_dir / "plugin-probe.log",
        timeout=120,
    )
    facts = _json(probe)
    facts["wheel"] = wheel.name
    facts["wheel_sha256"] = sha256(wheel)
    facts["install_argv"] = installed["argv"]
    return facts


def _version_import_probe(venv: Path, work: Path, log_dir: Path) -> dict[str, Any]:
    """Import the installed distributions and report where they resolved from."""
    script = (
        "import json, sys, importlib.util, importlib.metadata as m,"
        " acp, cccp, acp_services;"
        "spec = importlib.util.find_spec('acp_web_assets');"
        "paths = {'acp': acp.__file__, 'cccp': cccp.__file__,"
        "'acp_services': acp_services.__file__};"
        "names = {d.metadata['Name'] for d in m.distributions()};"
        "worker = 'acp-worker' if 'acp-worker' in names else 'acp';"
        "print(json.dumps({"
        "'acp': acp.__version__, 'cccp': cccp.__version__,"
        "'acp_services': m.version(worker),"
        "'paths': paths,"
        "'web_assets': None if spec is None else str(spec.origin),"
        "'sys_path': [p for p in sys.path if p]}))"
    )
    completed = run(
        [str(venv_python(venv)), "-c", script],
        cwd=work,
        env=clean_env(),
        log=log_dir / f"imports-{venv.name}.log",
        timeout=300,
    )
    facts = _json(completed)
    facts["resolved_inside_venv"] = all(
        path.startswith(str(venv)) for path in facts["paths"].values()
    )
    facts["repo_on_sys_path"] = any(str(REPO) in path for path in facts["sys_path"])
    return facts


def _isolated_home(work: Path) -> Path:
    """A clean host has no developer ``~/.config/cccp`` profile."""
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    return home


OPTIONAL_SCIENCE = ("censo", "isostat", "molclus")
CORE_DOCTOR_ROWS = (
    "software:orca",
    "software:xtb",
    "software:crest",
    "software:shermo",
    "run_root",
    "store",
    "config",
)


def _answers_yaml(work: Path, scientific: list[dict[str, Any]]) -> str:
    """The ``acp init`` answer file: the same plan path an operator uses."""
    lines = [
        "version: 1",
        "usage: local",
        "tier: standalone",
        "components: [science-core, local-identity, local-storage]",
        "resources:",
        "  software:",
    ]
    for entry in scientific:
        if entry.get("verified") != "PASS":
            continue
        path = str(entry["path"]).split(" (source=")[0]
        lines.append(f"    {entry['name'].lower()}: {path}")
    lines.extend(
        [
            "  cluster: {type: local}",
            "params:",
            f"  run_root: {work / 'run-root-installed'}",
            "",
        ]
    )
    return "\n".join(lines)


def _doctor_rows(text: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {"PASS": [], "FAIL": [], "NOT_VERIFIED": []}
    current = ""
    for line in text.splitlines():
        for state in rows:
            if line.strip().startswith(f"{state}（"):
                current = state
                break
        else:
            stripped = line.strip()
            if current and stripped.startswith("- ") and not stripped.startswith("- （"):
                rows[current].append(stripped[2:].split(" — ")[0].strip())
            continue
    return rows


def _cli_probe(
    venv: Path,
    work: Path,
    log_dir: Path,
    name: str,
    scientific: list[dict[str, Any]],
) -> dict[str, Any]:
    home = _isolated_home(work)
    env = clean_env({"HOME": str(home), "ACP_RUN_ROOT": str(work / f"run-root-{name}")})
    help_page = run(
        [str(venv_exe(venv, "acp")), "--help"],
        cwd=work,
        env=env,
        log=log_dir / f"cli-help-{name}.log",
        timeout=180,
    )
    version = run(
        [str(venv_exe(venv, "acp")), "--version"],
        cwd=work,
        env=env,
        log=log_dir / f"cli-version-{name}.log",
        timeout=180,
    )
    answers = home / f"answers-{name}.yaml"
    answers.write_text(_answers_yaml(work, scientific), encoding="utf-8")
    init = run(
        [str(venv_exe(venv, "acp")), "init", "--answers", str(answers)],
        cwd=work,
        env=env,
        log=log_dir / f"cli-init-{name}.log",
        timeout=300,
    )
    doctor = run(
        [str(venv_exe(venv, "acp")), "doctor"],
        cwd=work,
        env=env,
        log=log_dir / f"cli-doctor-{name}.log",
        timeout=300,
        accept=(0, 1),
    )
    rows = _doctor_rows(doctor.stdout)
    optional_failures = sorted(
        row for row in rows["FAIL"] if row.removeprefix("software:") in OPTIONAL_SCIENCE
    )
    unexpected_failures = sorted(set(rows["FAIL"]) - set(f"software:{n}" for n in OPTIONAL_SCIENCE))
    return {
        "help_has_subcommands": all(
            command in help_page.stdout for command in ("run", "doctor", "init", "task", "mcp")
        ),
        "version_line": version.stdout.strip(),
        "init_exit": init.returncode,
        "config_written": (home / ".cccp.yaml").is_file(),
        "answers": str(answers),
        "doctor_exit": doctor.returncode,
        "doctor_mode": "local" if "Local capability report" in doctor.stdout else "remote-node",
        "doctor_pass_rows": rows["PASS"],
        "doctor_fail_rows": rows["FAIL"],
        "optional_science_not_registered": optional_failures,
        "unexpected_failures": unexpected_failures,
        "doctor_stdout_tail": doctor.stdout.strip().splitlines()[-8:],
    }


def _cli_ok(probe: dict[str, Any]) -> bool:
    return (
        probe["help_has_subcommands"]
        and probe["version_line"] == f"acp {VERSION}"
        and probe["init_exit"] == 0
        and probe["config_written"]
        and probe["doctor_mode"] == "local"
        and not probe["unexpected_failures"]
        and set(CORE_DOCTOR_ROWS) <= set(probe["doctor_pass_rows"])
    )


def verify_store(bom: dict[str, Any]) -> dict[str, Any]:
    checked = 0
    total = 0
    for entry in bom["artifacts"]:
        path = Path(entry["uri"])
        if not path.is_file():
            raise SystemExit(f"artifact not retrievable: {entry['id']} -> {path}")
        total += path.stat().st_size
        if sha256(path) != entry["sha256"] or path.stat().st_size != entry["size_bytes"]:
            raise SystemExit(f"artifact digest/size mismatch: {entry['id']} -> {path}")
        checked += 1
    return {"artifacts": checked, "bytes": total, "bom": True}


def verify_wheelhouse(bom: dict[str, Any]) -> dict[str, Any]:
    """Verify the offline dependency wheelhouse against its shipped manifest."""
    entry = next((item for item in bom["artifacts"] if item["id"] == "python-wheelhouse"), None)
    if entry is None:
        raise SystemExit("BOM has no python-wheelhouse component (M5-03 F1 gap)")
    manifest_path = Path(entry["uri"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    directory = Path(manifest.get("directory") or "")
    if not directory.is_dir():
        raise SystemExit(f"wheelhouse directory is missing: {directory}")
    checked = 0
    total = 0
    for record in manifest["wheels"]:
        path = directory / record["name"]
        if not path.is_file():
            raise SystemExit(f"wheelhouse file is missing: {path}")
        if sha256(path) != record["sha256"] or path.stat().st_size != record["size_bytes"]:
            raise SystemExit(f"wheelhouse digest/size mismatch: {record['name']}")
        checked += 1
        total += record["size_bytes"]
    sums = {
        line.split("  ", 1)[1]: line.split("  ", 1)[0]
        for line in (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if sums != {record["name"]: record["sha256"] for record in manifest["wheels"]}:
        raise SystemExit("wheelhouse SHA256SUMS does not match the manifest")
    platforms = list(manifest["platforms"])
    per_platform = {
        tag: sum(1 for record in manifest["wheels"] if tag in record["platforms"])
        for tag in platforms
    }
    direct = {tag: int(manifest["coverage"][tag]["direct_requirements"]) for tag in platforms}
    if any(count != len(manifest["requirements"]) for count in direct.values()):
        raise SystemExit(f"wheelhouse does not cover every requirement per platform: {direct}")
    return {
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "directory": str(directory),
        "wheels": checked,
        "bytes": total,
        "platforms": platforms,
        "files_per_platform": per_platform,
        "direct_requirements_per_platform": direct,
        "requirements": manifest["requirements"],
        "offline": True,
    }


def offline_find_links(store: Path, distribution_dir: Path) -> list[str]:
    """pip argv that can only resolve from the store (no index, no network)."""
    return [
        "--no-index",
        "--find-links",
        str(store / "python" / "wheelhouse"),
        "--find-links",
        str(distribution_dir),
    ]


def _wheel_member_digest(wheel: Path, member: str) -> str:
    with zipfile.ZipFile(wheel) as archive:
        return hashlib.sha256(archive.read(member)).hexdigest()


def mixed_install_probe(*, store: Path, work: Path, log_dir: Path) -> dict[str, Any]:
    """Really install root + roles together and prove all three entries refuse."""
    root_dir = store / "python" / "root"
    roles_dir = store / "python" / "roles"
    venv = work / "venvs" / "mixed"
    python = create_venv(venv, log_dir)
    env = offline_pip_env()
    pip_install(
        python,
        [*offline_find_links(store, root_dir), f"acp[api,remote]=={VERSION}"],
        log=log_dir / "pip-mixed-root.log",
        cwd=work,
        env=env,
    )
    pip_install(
        python,
        [*offline_find_links(store, roles_dir), f"acp-web=={VERSION}"],
        log=log_dir / "pip-mixed-roles.log",
        cwd=work,
        env=env,
    )
    installed = installed_distributions(python, cwd=work)
    base_env = clean_env(
        {"HOME": str(_isolated_home(work)), "ACP_RUN_ROOT": str(work / "run-root-mixed")}
    )
    cli = run(
        [str(venv_exe(venv, "acp")), "--version"],
        cwd=work,
        env=base_env,
        log=log_dir / "mixed-cli-version.log",
        timeout=180,
        accept=(MUTUAL_EXCLUSION_EXIT,),
    )
    api = run(
        [
            str(python),
            "-c",
            "from acp.api.server import create_app; create_app()",
        ],
        cwd=work,
        env=base_env,
        log=log_dir / "mixed-api-create-app.log",
        timeout=180,
        accept=(1,),
    )
    mcp = run(
        [str(venv_exe(venv, "acp")), "mcp", "serve", "--transport", "stdio"],
        cwd=work,
        env=base_env,
        log=log_dir / "mixed-mcp-serve.log",
        timeout=180,
        accept=(MUTUAL_EXCLUSION_EXIT,),
    )
    root_wheel = next(root_dir.glob("acp-*.whl"))
    web_wheel = next(roles_dir.glob("acp_web-*.whl"))
    return {
        "venv": str(venv),
        "distributions": sorted(
            name for name in installed if name in {"acp", "acp-web", "acp-worker", "cccp"}
        ),
        "entries": {
            "cli": {
                "exit": cli.returncode,
                "typed": MUTUAL_EXCLUSION_CODE in (cli.stderr + cli.stdout),
            },
            "api_create_app": {
                "exit": api.returncode,
                "typed": MUTUAL_EXCLUSION_CODE in (api.stderr + api.stdout),
            },
            "mcp_serve": {
                "exit": mcp.returncode,
                "typed": MUTUAL_EXCLUSION_CODE in (mcp.stderr + mcp.stdout),
            },
        },
        "all_entries_refused": all(
            item["exit"] == MUTUAL_EXCLUSION_EXIT and item["typed"]
            for item in (
                {
                    "exit": cli.returncode,
                    "typed": MUTUAL_EXCLUSION_CODE in (cli.stderr + cli.stdout),
                },
                {
                    "exit": mcp.returncode,
                    "typed": MUTUAL_EXCLUSION_CODE in (mcp.stderr + mcp.stdout),
                },
            )
        )
        and api.returncode != 0
        and MUTUAL_EXCLUSION_CODE in (api.stderr + api.stdout),
        "shared_paths_byte_identical": (
            _wheel_member_digest(root_wheel, "acp/__init__.py")
            == _wheel_member_digest(web_wheel, "acp/__init__.py")
        ),
        "exit_code": MUTUAL_EXCLUSION_EXIT,
    }


def no_remote_doctor_probe(*, store: Path, work: Path, log_dir: Path) -> dict[str, Any]:
    """A root install without ``[remote]`` must not crash ``acp doctor`` (F4)."""
    venv = work / "venvs" / "root-no-remote"
    python = create_venv(venv, log_dir)
    env = offline_pip_env()
    pip_install(
        python,
        [*offline_find_links(store, store / "python" / "root"), f"acp=={VERSION}"],
        log=log_dir / "pip-root-no-remote.log",
        cwd=work,
        env=env,
    )
    installed = installed_distributions(python, cwd=work)
    run_env = clean_env(
        {"HOME": str(_isolated_home(work)), "ACP_RUN_ROOT": str(work / "run-root-no-remote")}
    )
    version = run(
        [str(venv_exe(venv, "acp")), "--version"],
        cwd=work,
        env=run_env,
        log=log_dir / "no-remote-cli-version.log",
        timeout=180,
    )
    doctor = run(
        [str(venv_exe(venv, "acp")), "doctor"],
        cwd=work,
        env=run_env,
        log=log_dir / "no-remote-doctor.log",
        timeout=300,
        accept=(0, 1),
    )
    output = doctor.stdout + doctor.stderr
    return {
        "paramiko_installed": "paramiko" in {name.lower() for name in installed},
        "version_line": version.stdout.strip(),
        "doctor_exit": doctor.returncode,
        "doctor_not_verified": "NOT_VERIFIED" in output,
        "doctor_names_paramiko": "paramiko" in output,
        "doctor_crashed": "ModuleNotFoundError" in output or "Traceback" in output,
    }


def render_readme(summary: dict[str, Any], bom: dict[str, Any]) -> str:
    """One human-readable index for the machine receipts in this directory."""
    kinds: dict[str, int] = {}
    for entry in bom["artifacts"]:
        kinds[entry["kind"]] = kinds.get(entry["kind"], 0) + 1
    rows = "\n".join(f"| `{kind}` | {count} |" for kind, count in sorted(kinds.items()))
    steps = "\n".join(f"| `{step['name']}` | {step['status']} |" for step in summary["steps"])
    return f"""# M5-01 — full-component artifacts and install entry

Generated by `scripts/acceptance/acp2_m501_install.py`; every number below is
read back from the receipts in this directory.

- result: **{summary["result"]}**
- BOM: `{summary["bom"]}` (sha256 `{summary["bom_sha256"]}`)
- artifact store: `{summary["store"]}`
- components verified by digest/size: {summary["store_components"]["artifacts"]}
  ({summary["store_components"]["bytes"]} bytes)
- offline dependency wheelhouse: {summary["wheelhouse"]["wheels"]} wheels
  ({summary["wheelhouse"]["bytes"]} bytes), platforms
  {", ".join(summary["wheelhouse"]["platforms"])} — every direct requirement
  covered on each platform; installs run with `--no-index` and dead proxies
- mixed root+roles install runtime guard:
  {summary["matrix"]["mixed"]["status"]} (CLI / create_app / mcp serve all
  typed `mutually_exclusive_distributions`, exit 78)
- root without `[remote]`: doctor exit {summary["no_remote"]["doctor_exit"]},
  NOT_VERIFIED row {summary["no_remote"]["doctor_not_verified"]}, no crash
  ({not summary["no_remote"]["doctor_crashed"]})
- source revision: `{bom["source"]["revision"]}` (tree
  `{bom["source"]["tree"]}`, clean={bom["source"]["clean"]})
- contract {bom["evidence"]["contract_version"]} digest
  `{bom["evidence"]["contract_digest"]}`; coverage sha256
  `{bom["evidence"]["coverage_sha256"]}`
- evidence reuse rule: `{bom["evidence"]["reuse_rule"]}`

## Components

| kind | count |
| --- | --- |
{rows}

## Install steps

| step | status |
| --- | --- |
{steps}

## Install matrix

| case | status |
| --- | --- |
| root `acp` only | {summary["matrix"]["root_only"]["status"]} |
| `cccp`+`acp-worker`+`acp-web` | {summary["matrix"]["roles_only"]["status"]} |
| mixed root + roles | {summary["matrix"]["mixed"]["status"]} |
| web research read surface | {summary["matrix"]["web_research_surface"]["status"]} |

MCP handshake: protocol `{summary["mcp"]["protocol_version"]}`,
{len(summary["mcp"]["tools"])} tools, pre-initialize `{summary["mcp"]["pre_initialize_code"]}`.

Workbench: `{summary["workbench"]["release_id"]}`, {summary["workbench"]["files"]}
files, static digest `{summary["workbench"]["static_digest"]}`
(matches BOM: {summary["workbench"]["digest_matches_bom"]}; tamper rejected:
{summary["workbench"]["tamper_rejected"]}).

RPH: `{summary["rph"]["bin_version"]}`, doctor `{summary["rph"]["doctor_schema"]}`
({summary["rph"]["doctor_mode"]}/{summary["rph"]["doctor_status"]}), offline verify
exit {summary["rph"]["verify_exit"]}.

## Files

| file | content |
| --- | --- |
| `bom-summary.json` | BOM digest table, toolchain, scientific software, limitations |
| `digest-verification.json` | `check_release_artifacts.py` traversal receipt |
| `install-receipt.json` | every install/startup step with observed facts |
| `install-matrix.json` | root/roles/mixed matrix + web research surface |
| `static-assets-service.json` | served files, mounts, traversal/404/405, digest tamper |
| `rph-install.json` | RPH offline surface install + `verify-install.mjs` + doctor |
| `summary.json` | aggregate machine summary |
| `logs/` | raw command logs (pip, pnpm, uvicorn, doctor, MCP) |

## Not verified / limitations

{chr(10).join(f"- {item}" for item in summary["not_verified"])}
{chr(10).join(f"- {item}" for item in summary["limitations"])}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--bom", type=Path, default=REPO / BOM_REL)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--reuse-venvs",
        action="store_true",
        help="reuse existing venvs when the installed wheel set is unchanged",
    )
    args = parser.parse_args(argv)
    store = args.store.resolve()
    work = args.work.resolve()
    out = args.out.resolve()
    log_dir = out / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    bom = json.loads(args.bom.read_text(encoding="utf-8"))
    receipt = Receipt()
    started = time.monotonic()

    store_facts = verify_store(bom)
    receipt.add("store.digests", True, store_facts)
    wheelhouse = verify_wheelhouse(bom)
    receipt.add(
        "store.wheelhouse",
        wheelhouse["wheels"] > 0
        and len(wheelhouse["platforms"]) >= 2
        and all(count > 0 for count in wheelhouse["files_per_platform"].values()),
        wheelhouse,
    )
    python_wheels = {
        entry["name"]: entry for entry in bom["artifacts"] if entry["kind"] == "python-wheel"
    }
    root_dir = Path(next(e for n, e in python_wheels.items() if n.startswith("acp-"))["uri"]).parent
    roles_dir = Path(
        next(e for n, e in python_wheels.items() if n.startswith("acp_web-"))["uri"]
    ).parent
    static_entry = next(
        entry for entry in bom["artifacts"] if entry["kind"] == "static-asset-bundle"
    )
    static_manifest = json.loads(Path(static_entry["uri"]).read_text(encoding="utf-8"))

    # --- root acp distribution ------------------------------------------------
    root_venv = work / "venvs" / "root"
    root_python = (
        create_venv(root_venv, log_dir)
        if not args.reuse_venvs
        else (
            venv_python(root_venv)
            if venv_python(root_venv).is_file()
            else create_venv(root_venv, log_dir)
        )
    )
    if not args.reuse_venvs:
        pip_install(
            root_python,
            # The root distribution keeps the historical extras split: a
            # complete product install always declares api+remote. Every
            # dependency comes from the store wheelhouse; --no-index plus the
            # dead-loopback proxies prove no network is reachable (M5-03 F1).
            [*offline_find_links(store, root_dir), f"acp[api,remote]=={VERSION}"],
            log=log_dir / "pip-root.log",
            cwd=work,
            env=offline_pip_env(),
        )
    root_imports = _version_import_probe(root_venv, work, log_dir)
    receipt.add(
        "root.imports",
        root_imports["resolved_inside_venv"]
        and not root_imports["repo_on_sys_path"]
        and root_imports["acp"] == VERSION,
        root_imports,
    )
    root_dists = installed_distributions(root_python, cwd=work)
    receipt.add(
        "root.distribution_set",
        "acp" in root_dists and "acp-web" not in root_dists,
        sorted(name for name in root_dists if name in {"acp", "acp-web", "acp-worker", "cccp"}),
    )
    science = bom["scientific_software"]
    root_cli = _cli_probe(root_venv, work, log_dir, "root", science)
    receipt.add("root.cli", _cli_ok(root_cli), root_cli)
    with controller(root_venv, work=work, log_dir=log_dir, name="mcp") as base_url:
        mcp = mcp_handshake(root_venv, base_url, log=log_dir / "mcp-handshake.json")
    mcp["controller"] = base_url
    receipt.add(
        "root.mcp_handshake",
        mcp["protocol_version"] == "2025-06-18"
        and mcp["tools"] == sorted(MCP_TOOLS)
        and mcp["pre_initialize_code"] == "not_initialized",
        mcp,
    )
    # --- role distributions ---------------------------------------------------
    roles_venv = work / "venvs" / "roles"
    roles_python = create_venv(roles_venv, log_dir)
    pip_install(
        roles_python,
        [
            *offline_find_links(store, roles_dir),
            f"cccp=={VERSION}",
            f"acp-worker=={VERSION}",
            f"acp-web=={VERSION}",
        ],
        log=log_dir / "pip-roles.log",
        cwd=work,
        env=offline_pip_env(),
    )
    roles_imports = _version_import_probe(roles_venv, work, log_dir)
    receipt.add(
        "roles.imports",
        roles_imports["resolved_inside_venv"]
        and not roles_imports["repo_on_sys_path"]
        and roles_imports["cccp"] == VERSION,
        roles_imports,
    )
    roles_dists = installed_distributions(roles_python, cwd=work)
    receipt.add(
        "roles.distribution_set",
        {"cccp", "acp-worker", "acp-web"} <= roles_dists and "acp" not in roles_dists,
        sorted(name for name in roles_dists if name in {"acp", "acp-web", "acp-worker", "cccp"}),
    )
    roles_cli = _cli_probe(roles_venv, work, log_dir, "roles", science)
    receipt.add("roles.cli", _cli_ok(roles_cli), roles_cli)
    with controller(roles_venv, work=work, log_dir=log_dir, name="mcp-roles") as roles_base:
        roles_mcp = mcp_handshake(roles_venv, roles_base, log=log_dir / "mcp-handshake-roles.json")
    roles_mcp["controller"] = roles_base
    receipt.add(
        "roles.mcp_handshake",
        roles_mcp["protocol_version"] == "2025-06-18" and roles_mcp["tools"] == sorted(MCP_TOOLS),
        {"protocol_version": roles_mcp["protocol_version"], "tools": roles_mcp["tools"]},
    )
    workbench = workbench_probe(
        venv=roles_venv,
        work=work / "workbench",
        log_dir=log_dir,
        store_static_manifest=static_manifest,
    )
    receipt.add(
        "web.workbench.static",
        workbench["index"]["status"] == 200
        and workbench["index"]["matches_packaged_html"]
        and workbench["index"]["served_from_venv"]
        and workbench["asset"]["matches_bytes"]
        and workbench["asset"]["cache_control"] == "no-cache",
        {"index": workbench["index"], "asset": workbench["asset"]},
    )
    receipt.add(
        "web.workbench.capabilities",
        workbench["capabilities"]["status"] == 200
        and workbench["capabilities"]["workflows"] > 0
        and workbench["science_capabilities"]["status"] == 200,
        {
            "capabilities": workbench["capabilities"],
            "science": workbench["science_capabilities"],
            "research_unknown": workbench["research_read_unknown"],
        },
    )
    receipt.add(
        "web.workbench.read_only_mounts",
        workbench["unknown_asset"]["status"] == 404
        and workbench["traversal_literal"]["status"] in (400, 404)
        and workbench["traversal_encoded"]["status"] in (400, 404)
        and workbench["post_to_static"]["status"] in (405, 404)
        and workbench["head_asset"]["status"] == 200,
        {
            "unknown": workbench["unknown_asset"],
            "traversal_literal": workbench["traversal_literal"],
            "traversal_encoded": workbench["traversal_encoded"],
            "post": workbench["post_to_static"],
            "head": workbench["head_asset"],
        },
    )
    receipt.add(
        "web.workbench.digest_startup",
        workbench["digest"]["verified"]
        and workbench["digest"]["matches_bom"]
        and workbench["digest"]["tamper_rejected"],
        workbench["digest"],
    )

    no_remote = no_remote_doctor_probe(store=store, work=work, log_dir=log_dir)
    mixed = mixed_install_probe(store=store, work=work, log_dir=log_dir)
    matrix = {
        "root_only": {"distributions": ["acp"], "status": "PASS"},
        "roles_only": {"distributions": ["cccp", "acp-worker", "acp-web"], "status": "PASS"},
        "mixed": {
            "status": "REFUSED" if mixed["all_entries_refused"] else "FAIL",
            "reason": (
                "the product itself refuses root acp + roles coexistence "
                f"({MUTUAL_EXCLUSION_CODE}, exit {MUTUAL_EXCLUSION_EXIT}) at the "
                "CLI, FastAPI create_app and MCP stdio entries"
            ),
            "entries": mixed["entries"],
            "distributions_observed": mixed["distributions"],
            "shared_paths_byte_identical": mixed["shared_paths_byte_identical"],
        },
        "root_without_remote_extra": {
            "paramiko_installed": no_remote["paramiko_installed"],
            "doctor_exit": no_remote["doctor_exit"],
            "doctor_not_verified": no_remote["doctor_not_verified"],
            "doctor_crashed": no_remote["doctor_crashed"],
            "status": "PASS"
            if not no_remote["paramiko_installed"]
            and not no_remote["doctor_crashed"]
            and no_remote["doctor_not_verified"]
            else "FAIL",
        },
        "web_research_surface": {
            "science_capabilities": workbench["science_capabilities"]["status"],
            "research_read_refusal": workbench["research_read_unknown"]["status"],
            "status": "PASS"
            if workbench["science_capabilities"]["status"] == 200
            and workbench["research_read_unknown"]["status"] in (400, 403, 404)
            else "FAIL",
        },
    }
    receipt.add("install.matrix", matrix["mixed"]["status"] == "REFUSED", matrix)

    # --- RPH offline install ---------------------------------------------------
    rph = rph_install_probe(store=store, work=work, log_dir=log_dir)
    receipt.add(
        "rph.offline_install",
        rph["pnpm_install_exit"] == 0
        and rph["verify_exit"] == 0
        and rph["bin_version"].startswith("rph ")
        and rph["doctor_schema"] == "acp2.rph.doctor.v1"
        and rph["doctor_mode"] == "static"
        and rph["doctor_status"] == "ok"
        and all(rph["packages_present"].values()),
        rph,
    )

    # --- third-party plugin wheel ---------------------------------------------
    plugin = plugin_install_probe(store=store, work=work, log_dir=log_dir)
    receipt.add(
        "plugin.wheel_install",
        plugin["name"] == "acp2-external-science-validator"
        and plugin["version"] == "1.0.0"
        and plugin["entry_points"] == ["acp2_external_validator"],
        plugin,
    )

    facts = {
        "schema": SCHEMA,
        "task_id": "M5-01",
        "generated_at": now(),
        "result": "FAIL" if receipt.failed else "PASS",
        "store": str(store),
        "bom": str(args.bom),
        "bom_sha256": sha256(args.bom),
        "python": sys.version.split()[0],
        "work": str(work),
        "seconds": round(time.monotonic() - started, 1),
        "steps": receipt.steps,
        "matrix": matrix,
        "wheelhouse": wheelhouse,
        "mixed": mixed,
        "no_remote": no_remote,
        "rph": rph,
        "plugin": plugin,
        "workbench": workbench,
        "mcp": mcp,
        "imports": {"root": root_imports, "roles": roles_imports},
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "install-receipt.json").write_text(
        json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "install-matrix.json").write_text(
        json.dumps(
            {"schema": "acp2.m5-01.install-matrix.v1", "generated_at": now(), "matrix": matrix},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (out / "static-assets-service.json").write_text(
        json.dumps(
            {
                "schema": "acp2.m5-01.static-assets-service.v1",
                "generated_at": now(),
                "manifest": str(static_entry["uri"]),
                "manifest_sha256": static_entry["sha256"],
                "workbench": workbench,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (out / "rph-install.json").write_text(
        json.dumps(
            {"schema": "acp2.m5-01.rph-install.v1", "generated_at": now(), "rph": rph},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    by_kind: dict[str, int] = {}
    by_kind_bytes: dict[str, int] = {}
    for entry in bom["artifacts"]:
        by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
        by_kind_bytes[entry["kind"]] = by_kind_bytes.get(entry["kind"], 0) + entry["size_bytes"]
    (out / "bom-summary.json").write_text(
        json.dumps(
            {
                "schema": "acp2.m5-01.bom-summary.v1",
                "generated_at": now(),
                "bom": str(args.bom),
                "bom_sha256": sha256(args.bom),
                "task_id": bom["task_id"],
                "owner": bom["owner"],
                "source": bom["source"],
                "evidence": bom["evidence"],
                "store": str(store),
                "store_verified": store_facts,
                "components": {
                    "total": len(bom["artifacts"]),
                    "by_kind": by_kind,
                    "bytes_by_kind": by_kind_bytes,
                },
                "artifacts": [
                    {
                        "id": entry["id"],
                        "kind": entry["kind"],
                        "name": entry["name"],
                        "version": entry["version"],
                        "sha256": entry["sha256"],
                        "size_bytes": entry["size_bytes"],
                        "uri": entry["uri"],
                    }
                    for entry in bom["artifacts"]
                ],
                "toolchain": bom["toolchain"],
                "scientific_software": bom["scientific_software"],
                "limitations": bom["limitations"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    not_verified = [
        f"scientific software not registered: {entry['name']}"
        for entry in bom["scientific_software"]
        if entry["verified"] != "PASS"
    ]
    not_verified.extend(
        f"doctor optional science binary not registered: {row}"
        for row in root_cli["optional_science_not_registered"]
    )
    not_verified.append(
        "versioned /experience/<release_id>/ mount: not implemented (packaged /,/js,/css "
        "mounts are verified read-only with startup digest verification)"
    )
    not_verified.append("full G01-G26 evidence walk and independent operator run are M5-02/M5-03")
    summary = {
        "schema": "acp2.m5-01.summary.v1",
        "task_id": "M5-01",
        "generated_at": now(),
        "result": facts["result"],
        "bom_sha256": sha256(args.bom),
        "bom": str(args.bom),
        "store": str(store),
        "store_components": store_facts,
        "wheelhouse": wheelhouse,
        "steps": [{"name": step["name"], "status": step["status"]} for step in receipt.steps],
        "commands": {
            "build": "python scripts/release/build_release_bom.py",
            "artifact_check": (
                "PYTHONPATH=src python scripts/acceptance/check_release_artifacts.py"
            ),
            "install": (
                f"python scripts/acceptance/acp2_m501_install.py --store {store} --out {out}"
            ),
            "rph_package": "pnpm --dir rph package",
            "tests": (
                "pytest tests/test_acp2_m501_release_artifacts.py "
                "tests/test_packaging_assets.py tests/test_acp2_rph_install_surface.py "
                "-q --run-slow"
            ),
        },
        "matrix": matrix,
        "no_remote": no_remote,
        "mixed": mixed["entries"],
        "imports": {
            "root": {
                "distributions": ["acp"],
                "resolved_inside_venv": root_imports["resolved_inside_venv"],
                "repo_on_sys_path": root_imports["repo_on_sys_path"],
            },
            "roles": {
                "distributions": ["cccp", "acp-worker", "acp-web"],
                "resolved_inside_venv": roles_imports["resolved_inside_venv"],
                "repo_on_sys_path": roles_imports["repo_on_sys_path"],
            },
        },
        "mcp": {
            "protocol_version": mcp["protocol_version"],
            "tools": mcp["tools"],
            "pre_initialize_code": mcp["pre_initialize_code"],
        },
        "workbench": {
            "release_id": workbench["digest"]["release_id"],
            "static_digest": workbench["digest"]["digest"],
            "files": workbench["digest"]["files"],
            "digest_matches_bom": workbench["digest"]["matches_bom"],
            "tamper_rejected": workbench["digest"]["tamper_rejected"],
        },
        "rph": {
            "bin_version": rph["bin_version"],
            "doctor_schema": rph["doctor_schema"],
            "doctor_mode": rph["doctor_mode"],
            "doctor_status": rph["doctor_status"],
            "verify_exit": rph["verify_exit"],
        },
        "not_verified": not_verified,
        "limitations": bom["limitations"],
        "seconds": facts["seconds"],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out / "README.md").write_text(render_readme(summary, bom), encoding="utf-8")
    print(
        json.dumps(
            {"result": facts["result"], "steps": len(receipt.steps), "out": str(out)}, indent=2
        )
    )
    return 0 if facts["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
