"""T38 (C07-s6) — science-exception narrowing regression pins.

Guards the bounded narrowing delivered by plan item 38:

* ``cccp.qc.runners.run_shermo`` / ``_parse_sum_file`` — broad ``except
  Exception`` replaced by the expected OS/subprocess/parse failures.  Those
  boundary failures still return the structured ``None`` result; an unexpected
  programming error is no longer masked.
* ``cccp.utils.geometry_tools.LogParser`` — the two structured-error fallbacks
  narrow to parse/OS failures while keeping the file-boundary ``OSError``.
* ``cccp.calculation.tasks.optimize._finalize_local_trajectory`` — the
  best-effort trajectory rebuild is an explicit boundary duty and stays broad
  (a rebuild failure must never fail the calculation).

The before/after probe output lives in
``.omo/evidence/acp-parallel-plan-3-cccp/task-38-behavior-{before,after}.txt``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# ── run_shermo / _parse_sum_file ─────────────────────────────────────────────


def _shermo_kwargs(tmp_path: Path) -> dict[str, object]:
    (tmp_path / "freq.out").write_text("freq output", encoding="utf-8")
    return dict(
        freq_output=tmp_path / "freq.out",
        sp_energy=-74.0,
        output_dir=tmp_path,
        shermo_bin="Shermo",
        output_file=tmp_path / "out.sum",
    )


def _patch_shermo(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "cccp.qc.runners.resolve_executable",
        lambda name, configured_path=None: "Shermo",
    )


def test_run_shermo_returns_none_on_missing_binary(monkeypatch, tmp_path: Path) -> None:
    """OSError from the subprocess boundary stays a structured ``None``."""
    from cccp.qc.runners import run_shermo

    _patch_shermo(monkeypatch, tmp_path)

    def _boom(*args, **kwargs):
        raise FileNotFoundError("shermo vanished")

    monkeypatch.setattr("cccp.qc.runners.subprocess.run", _boom)
    assert run_shermo(**_shermo_kwargs(tmp_path)) is None


def test_run_shermo_returns_none_on_subprocess_error(monkeypatch, tmp_path: Path) -> None:
    """``subprocess.SubprocessError`` stays contained (structured ``None``)."""
    from cccp.qc.runners import run_shermo

    _patch_shermo(monkeypatch, tmp_path)

    def _boom(*args, **kwargs):
        raise subprocess.TimeoutExpired("shermo", 1)

    monkeypatch.setattr("cccp.qc.runners.subprocess.run", _boom)
    assert run_shermo(**_shermo_kwargs(tmp_path)) is None


def test_run_shermo_surfaces_unexpected_errors(monkeypatch, tmp_path: Path) -> None:
    """An unexpected programming error must NOT be silently swallowed."""
    from cccp.qc.runners import run_shermo

    _patch_shermo(monkeypatch, tmp_path)

    class UnexpectedBugError(RuntimeError):
        pass

    def _boom(*args, **kwargs):
        raise UnexpectedBugError("bug in wrapper")

    monkeypatch.setattr("cccp.qc.runners.subprocess.run", _boom)
    with pytest.raises(UnexpectedBugError):
        run_shermo(**_shermo_kwargs(tmp_path))


def test_parse_sum_file_returns_none_on_missing_file(tmp_path: Path) -> None:
    from cccp.qc.runners import _parse_sum_file

    assert _parse_sum_file(tmp_path / "nope.sum") is None


def test_parse_sum_file_returns_none_on_non_numeric(tmp_path: Path) -> None:
    from cccp.qc.runners import _parse_sum_file

    bad = tmp_path / "bad.sum"
    bad.write_text("Total S: not-a-number\n", encoding="utf-8")
    assert _parse_sum_file(bad) is None


def test_parse_sum_file_success_unchanged(tmp_path: Path) -> None:
    from cccp.qc.runners import _parse_sum_file

    good = tmp_path / "good.sum"
    good.write_text(
        "Sum of electronic energy and thermal correction to G: -1.234567\n",
        encoding="utf-8",
    )
    assert _parse_sum_file(good) == {"g_sum": -1.234567}


# ── LogParser structured fallbacks ───────────────────────────────────────────


def test_extract_from_text_malformed_is_structured() -> None:
    from cccp.utils.geometry_tools import LogParser

    coords, symbols, error = LogParser.extract_from_text("not a log", engine_type="orca")
    assert coords is None and symbols is None
    assert error is not None and "No coordinates found" in error


def test_extract_last_converged_coords_preserves_oserror(tmp_path: Path) -> None:
    """The file boundary keeps ``OSError`` as a structured parse error."""
    from cccp.utils.geometry_tools import LogParser

    as_dir = tmp_path / "not_a_file"
    as_dir.mkdir()
    coords, symbols, error = LogParser.extract_last_converged_coords(as_dir, engine_type="orca")
    assert coords is None and symbols is None
    assert error is not None and error.startswith("Parse error:")


def test_extract_last_converged_coords_missing_file(tmp_path: Path) -> None:
    from cccp.utils.geometry_tools import LogParser

    coords, symbols, error = LogParser.extract_last_converged_coords(tmp_path / "gone.out")
    assert coords is None and symbols is None
    assert error is not None and error.startswith("Log file not found:")


def test_extract_last_converged_coords_malformed(tmp_path: Path) -> None:
    from cccp.utils.geometry_tools import LogParser

    malformed = tmp_path / "malformed.out"
    malformed.write_text("random text without coordinates", encoding="utf-8")
    coords, symbols, error = LogParser.extract_last_converged_coords(malformed, engine_type="orca")
    assert coords is None and symbols is None
    assert error is not None and "No coordinates found" in error


# ── preserved best-effort boundary ───────────────────────────────────────────


def test_finalize_local_trajectory_boundary_never_fails(monkeypatch, tmp_path: Path) -> None:
    """Trajectory rebuild is explicit best-effort: it must never raise."""
    import cccp.calculation.tasks.optimize as opt

    def _boom(*args, **kwargs):
        raise RuntimeError("rebuild exploded")

    monkeypatch.setattr(opt, "finalize_optimization_trajectory", _boom)
    # No exception escapes — the calculation must not fail because of this.
    assert opt._finalize_local_trajectory(tmp_path, "orca") is None


# ── no new bare ``except Exception`` in the narrowed modules ─────────────────


def test_narrowed_modules_have_no_bare_except_exception() -> None:
    import inspect

    import cccp.calculation.tasks.optimize as opt
    import cccp.qc.runners as runners
    from cccp.utils.geometry_tools import LogParser

    for func in (
        runners.run_shermo,
        runners._parse_sum_file,
        LogParser.extract_from_text,
        LogParser.extract_last_converged_coords,
    ):
        assert "except Exception" not in inspect.getsource(func), func.__qualname__
    # The preserved boundary keeps its documented broad catch.
    assert "except Exception" in inspect.getsource(opt._finalize_local_trajectory)
