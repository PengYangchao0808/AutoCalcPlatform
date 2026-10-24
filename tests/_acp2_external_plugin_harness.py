"""Shared helpers for the A-22 external plugin fixture tests (not collected).

Builds the fixture wheel with the platform build tool, creates an isolated
virtual environment, installs the wheel with ``--no-deps --no-index`` and
runs the metadata-only discovery / explicit invocation probe in that venv.

No helper installs anything into the shared integration environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "acp2" / "science" / "external_plugin"
SOURCE_ROOT = REPO_ROOT / "src"
PROBE_SCRIPT = Path(__file__).with_name("_acp2_external_plugin_probe.py")

DISTRIBUTION_NAME = "acp2-external-science-validator"
MODULE_NAME = "acp2_fixture_validator"
WHEEL_FILENAME = "acp2_external_science_validator-1.0.0-py3-none-any.whl"
ENTRY_POINT_GROUP = "cccp.science_plugins"
ENTRY_POINT_NAME = "acp2_external_validator"
PLUGIN_NAME = "acp2_external.native_singlepoint"
CAPABILITY_NAME = "acp2_external_native_singlepoint"
SCHEMA = "acp2.science.external.native_singlepoint"
FIXED_INPUT = FIXTURE_ROOT / "fixtures" / "native_singlepoint.acp2n"
EXPECTED_OUTPUT = FIXTURE_ROOT / "fixtures" / "expected_output.json"
MANIFEST = FIXTURE_ROOT / "cccp_science_plugin.json"


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def build_fixture_wheel(
    outdir: Path, *, fixture_root: Path = FIXTURE_ROOT
) -> tuple[Path, list[str], str]:
    """Build the fixture wheel offline and return ``(wheel, argv, log)``.

    Uses ``python -m build --wheel --no-isolation`` when the ``build`` module
    is importable (the platform build tool), otherwise falls back to
    ``pip wheel --no-deps --no-index --no-build-isolation``.  ``fixture_root``
    selects the distribution source tree (the A-22 fixture by default, the
    A-23 upgrade pair when supplied).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    if _has_module("build"):
        argv = [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(outdir),
            str(fixture_root),
        ]
    else:
        argv = [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-index",
            "--no-build-isolation",
            "-w",
            str(outdir),
            str(fixture_root),
        ]
    result = _run(argv, env=env)
    log = result.stdout + result.stderr
    if result.returncode != 0:
        raise AssertionError(f"fixture build failed ({result.returncode}):\n{log}")
    wheel = outdir / WHEEL_FILENAME
    if not wheel.is_file():
        candidates = sorted(outdir.glob("*.whl"))
        if len(candidates) != 1:
            raise AssertionError(f"expected one wheel in {outdir}, found {candidates}:\n{log}")
        wheel = candidates[0]
    return wheel, argv, log


def _has_module(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def venv_python(venv_root: Path) -> Path:
    """Return the interpreter path for *venv_root* (POSIX layout)."""
    return venv_root / "bin" / "python"


def create_isolated_venv(root: Path) -> tuple[Path, list[str]]:
    """Create a fresh venv with system site packages (no network install)."""
    venv_root = root / "venv"
    argv = [sys.executable, "-m", "venv", "--system-site-packages", str(venv_root)]
    result = _run(argv)
    if result.returncode != 0:
        raise AssertionError(
            f"venv creation failed ({result.returncode}):\n{result.stdout}{result.stderr}"
        )
    python = venv_python(venv_root)
    if not python.is_file():
        raise AssertionError(f"venv interpreter missing at {python}")
    return python, argv


def install_wheel(python: Path, wheel: Path) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    """Install *wheel* into the isolated venv with ``--no-deps --no-index``."""
    argv = [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)]
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    result = _run(argv, env=env)
    if result.returncode != 0:
        raise AssertionError(
            f"wheel install failed ({result.returncode}):\n{result.stdout}{result.stderr}"
        )
    return argv, result


def uninstall_distribution(
    python: Path, name: str = DISTRIBUTION_NAME
) -> tuple[list[str], subprocess.CompletedProcess[str]]:
    """Uninstall *name* from the isolated venv (QA failure scenario)."""
    argv = [str(python), "-m", "pip", "uninstall", "-y", name]
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INPUT"] = "1"
    result = _run(argv, env=env)
    if result.returncode != 0:
        raise AssertionError(
            f"uninstall failed ({result.returncode}):\n{result.stdout}{result.stderr}"
        )
    return argv, result


def run_python_script(python: Path, script: Path, *args: object) -> dict[str, object]:
    """Run *script* in the isolated venv and return its JSON payload."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SOURCE_ROOT) if not existing else f"{SOURCE_ROOT}{os.pathsep}{existing}"
    argv = [str(python), str(script), *(str(arg) for arg in args)]
    result = _run(argv, env=env)
    if result.returncode != 0:
        raise AssertionError(f"probe failed ({result.returncode}):\n{result.stdout}{result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"probe did not emit JSON:\n{result.stdout}{result.stderr}") from exc


def run_probe(python: Path, *args: object) -> dict[str, object]:
    """Run the A-22 probe in the isolated venv and return its JSON payload."""
    return run_python_script(python, PROBE_SCRIPT, *args)


__all__ = [
    "CAPABILITY_NAME",
    "DISTRIBUTION_NAME",
    "ENTRY_POINT_GROUP",
    "ENTRY_POINT_NAME",
    "EXPECTED_OUTPUT",
    "FIXED_INPUT",
    "FIXTURE_ROOT",
    "MANIFEST",
    "MODULE_NAME",
    "PLUGIN_NAME",
    "PROBE_SCRIPT",
    "REPO_ROOT",
    "SCHEMA",
    "SOURCE_ROOT",
    "WHEEL_FILENAME",
    "build_fixture_wheel",
    "create_isolated_venv",
    "install_wheel",
    "run_probe",
    "run_python_script",
    "uninstall_distribution",
    "venv_python",
]
