"""Smoke tests for ACP CLI entry points."""

from __future__ import annotations

import subprocess
import sys


def test_acp_help_exits_zero():
    """``acp --help`` exits with code 0."""
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Auto-Calc Platform" in result.stdout or "acp" in result.stdout


def test_acp_run_mechanism_help():
    """``acp run mechanism --help`` shows real mechanism workflow options."""
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "mechanism", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--input" in result.stdout
    assert "--output" in result.stdout


def test_acp_run_serve_help():
    """``acp run serve --help`` shows real server workflow options."""
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "serve", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--host" in result.stdout
    assert "--port" in result.stdout


def test_acp_no_command_shows_help():
    """``acp`` without arguments shows help."""
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli"],
        capture_output=True,
        text=True,
    )
    # argparse with required=True subparser returns error code
    assert result.returncode != 0 or "usage" in result.stderr.lower() or "usage" in result.stdout.lower()


def test_acp_run_nmr_help_shows_bruker():
    """``acp run nmr --help`` shows P3 Bruker options."""
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "nmr", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--bruker" in result.stdout
    assert "--bruker-ref" in result.stdout


def test_acp_run_nmr_spectrum_bruker_mutual_exclusion():
    """Passing both --spectrum and --bruker fails fast with exit code 1."""
    result = subprocess.run(
        [
            sys.executable, "-m", "acp.cli", "run", "nmr",
            "--input", "CCO",
            "--spectrum", "C: 40.0(C1)",
            "--bruker", "/tmp/nonexistent",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "exactly one" in result.stderr.lower()

