"""Round-7 P1: raw XtbPathSearch ``path_inp_text`` must be constraint-validated."""

from __future__ import annotations

from pathlib import Path

from cccp.qc.interfaces.xtb_path import XTBPathInterface


def _write_xyz(path: Path) -> Path:
    path.write_text("3\nwater\nO 0 0 0\nH 0.957 0 0\nH -0.24 0.927 0\n", encoding="utf-8")
    return path


def test_malformed_constrain_text_fails_before_spawning(tmp_path: Path) -> None:
    interface = XTBPathInterface({})
    start = _write_xyz(tmp_path / "start.xyz")
    end = _write_xyz(tmp_path / "end.xyz")
    output = tmp_path / "out"
    output.mkdir()
    result = interface.path_search(
        start,
        end,
        output,
        path_inp_text=(
            "$constrain\n"
            "  distance: 3, 4, 1.95, auto\n"
            "$end\n"
            "$path\n  nrun=1\n$end\n"
        ),
    )
    assert result.success is False
    assert "invalid xTB constraint syntax" in (result.error_message or "")


def test_well_formed_constrain_text_passes_the_syntax_gate() -> None:
    """A correct directive is not refused by the syntax gate."""
    from cccp.qc.interfaces.xtb_scan import validate_xcontrol_text

    text = (
        "$constrain\n"
        "  distance: 3, 4, 1.95\n"
        "  force constant=0.5\n"
        "$end\n"
        "$path\n  nrun=1\n$end\n"
    )
    validate_xcontrol_text(text)
