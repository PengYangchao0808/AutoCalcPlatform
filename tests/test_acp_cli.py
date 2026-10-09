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


def test_acp_run_mechanism_is_retired():
    """The removed mechanism entry cannot be used as a new task entry."""
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "mechanism", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "已退役" in result.stdout + result.stderr


def test_acp_run_removed_workflows_reject_with_retired_message():
    for workflow in ("optfreq", "optfreqsp", "Lowconfirm", "Highconfirm"):
        result = subprocess.run(
            [sys.executable, "-m", "acp.cli", "run", workflow],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "已退役" in result.stdout + result.stderr


def test_acp_run_lowconfirm_help_is_retired():
    result = subprocess.run(
        [sys.executable, "-m", "acp.cli", "run", "Lowconfirm", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


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
    assert (
        result.returncode != 0
        or "usage" in result.stderr.lower()
        or "usage" in result.stdout.lower()
    )


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
    assert "--solvent-model" in result.stdout
    assert "--max-conformers" in result.stdout


def test_acp_run_nmr_parser_accepts_solvent_model_and_max_conformers():
    """G06: the nmr parser accepts the resolver-forwarded CLI flags."""
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "nmr",
            "--input",
            "CCO",
            "--spectrum",
            "x",
            "--solvent-model",
            "none",
            "--max-conformers",
            "5",
        ]
    )
    assert args.solvent_model == "none"
    assert args.max_conformers == 5


def test_handle_nmr_propagates_solvent_model_and_max_conformers(monkeypatch, tmp_path):
    """_handle_nmr resolves the method payload and forwards it to the analysis."""
    from types import SimpleNamespace

    from acp.cli import _handle_nmr, build_parser

    captured: dict = {}

    def _fake_run_nmr_analysis(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(status="failed", metadata={}, error="stop-here")

    monkeypatch.setattr("acp.workflows.nmr.run_nmr_analysis", _fake_run_nmr_analysis)

    args = build_parser().parse_args(
        [
            "run",
            "nmr",
            "--input",
            "CCO",
            "--spectrum",
            "C: 40.0(C1)",
            "--output",
            str(tmp_path / "out"),
            "--solvent-model",
            "none",
            "--max-conformers",
            "5",
        ]
    )
    rc = _handle_nmr(args)

    assert rc == 1  # fake result status "failed" → existing failure exit code
    assert captured["solvent_model"] == "none"
    assert captured["max_conformers"] == 5
    assert captured["solvent"] == ""  # gas phase (solvent_model none) — T17 contract
    assert captured["nmr_method"] == "mPW1PW91"
    assert captured["nuclei"] == ["1H", "13C"]
    assert captured["error_model"] == "goodman-legacy"
    assert captured["conformer_preset"] == "censo-light"
    assert captured["ewin"] == 6.0


def test_handle_nmr_tolerates_none_dp5_in_summary(monkeypatch, tmp_path):
    """Placeholder error model yields dp5=None — the report line must not crash."""
    from types import SimpleNamespace

    from acp.cli import _handle_nmr, build_parser

    def _fake_run_nmr_analysis(**kwargs):
        return SimpleNamespace(
            status="completed",
            metadata={
                "winner": {"index": 0, "label": "candidate_1", "dp4": 1.0, "dp5": None},
                "n_candidates": 1,
            },
            error=None,
        )

    monkeypatch.setattr("acp.workflows.nmr.run_nmr_analysis", _fake_run_nmr_analysis)
    args = build_parser().parse_args(
        [
            "run",
            "nmr",
            "--input",
            "CCO",
            "--spectrum",
            "C: 40.0(C1)",
            "--output",
            str(tmp_path / "out"),
            "--nmr-method",
            "B3LYP",
            "--nmr-basis",
            "def2-TZVPP",
            "--error-model",
            "placeholder-student-t",
        ]
    )
    assert _handle_nmr(args) == 0


def test_handle_nmr_rejects_unknown_method_via_resolver(tmp_path):
    """G06 rejection: a non-METHOD_META functional fails fast with exit 1."""
    from acp.cli import _handle_nmr, build_parser

    args = build_parser().parse_args(
        [
            "run",
            "nmr",
            "--input",
            "CCO",
            "--spectrum",
            "C: 40.0(C1)",
            "--output",
            str(tmp_path / "out"),
            "--nmr-method",
            "definitely-not-a-functional",
        ]
    )
    assert _handle_nmr(args) == 1


def test_acp_run_nmr_spectrum_bruker_mutual_exclusion():
    """Passing both --spectrum and --bruker fails fast with exit code 1."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "nmr",
            "--input",
            "CCO",
            "--spectrum",
            "C: 40.0(C1)",
            "--bruker",
            "/tmp/nonexistent",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "exactly one" in result.stderr.lower()
