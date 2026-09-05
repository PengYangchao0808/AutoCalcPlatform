"""Regression tests for Shermo input-path handling.

Shermo 2.6 (Fortran) truncates input paths at 200 characters and then fails
with "Error: Unable to find <path>" (see _SHERMO_INPUT_PATH_LIMIT). These
tests pin the run_shermo workaround: relative paths when the frequency file
lives under the output dir, short-name copy fallback otherwise.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cccp.qc.runners import _shermo_input_path, run_shermo


def _fake_run_shermo(monkeypatch, tmp_path, freq_path):
    captured: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(stdout="", stderr="")

    monkeypatch.setattr("cccp.qc.runners.subprocess.run", fake_run)
    monkeypatch.setattr(
        "cccp.qc.runners.resolve_executable",
        lambda name, configured_path=None: "Shermo",
    )
    monkeypatch.setattr(
        "cccp.qc.runners._parse_sum_file",
        lambda sum_file: {"g_sum": -1.0},
    )
    result = run_shermo(
        freq_output=freq_path,
        sp_energy=-670.8,
        output_dir=tmp_path,
        shermo_bin="Shermo",
        output_file=tmp_path / "out.sum",
    )
    assert result == {"g_sum": -1.0}
    return captured


def test_input_under_output_dir_uses_relative_path(monkeypatch, tmp_path):
    freq = tmp_path / "conf_000_freq.out"
    freq.write_text("freq output")

    captured = _fake_run_shermo(monkeypatch, tmp_path, freq)

    assert captured["cmd"][1] == "conf_000_freq.out"


def test_input_outside_output_dir_short_absolute_kept(monkeypatch, tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    freq = source_dir / "molecule.log"
    freq.write_text("freq output")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    captured = _fake_run_shermo(monkeypatch, out_dir, freq)

    assert captured["cmd"][1] == str(freq)
    assert not (out_dir / "molecule.log").exists()


def test_input_outside_output_dir_long_path_copied_short(monkeypatch, tmp_path):
    source_dir = tmp_path
    for _ in range(12):
        source_dir = source_dir / "very_long_directory_component"
    source_dir.mkdir(parents=True)
    freq = source_dir / "molecule.log"
    freq.write_text("freq output")
    assert len(str(freq)) > 200

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    captured = _fake_run_shermo(monkeypatch, out_dir, freq)

    assert captured["cmd"][1] == "molecule.log"
    assert (out_dir / "molecule.log").read_text() == "freq output"


def test_shermo_input_path_returns_basename_when_already_short(tmp_path):
    freq = tmp_path / "freq.out"
    freq.write_text("x")

    assert _shermo_input_path(freq, tmp_path) == "freq.out"


@pytest.mark.parametrize("length", [199, 200, 201, 240])
def test_path_limit_boundary(tmp_path, length):
    base = tmp_path
    target = base
    while len(str(target)) < length - len("f.xyz") - 1:
        target = target / ("p" * 40)
    target.mkdir(parents=True, exist_ok=True)
    freq = target / "f.xyz"
    freq.write_text("x")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    arg = _shermo_input_path(freq, out_dir)

    assert len(arg) <= 200
