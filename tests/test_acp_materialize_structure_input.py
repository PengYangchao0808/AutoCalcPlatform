"""The shared materializer must honour every structure input the contract accepts.

astra round-4 review: a bare ``input.xyz_text`` passed the contract (and the
scheduler read it) but fell through to the SMILES parser at materialization,
so the job could never produce a structure file.  These tests stop at the real
artifact: the written XYZ content, not just a JobSpec field.
"""

from __future__ import annotations

from pathlib import Path


def test_bare_xyz_text_materializes_as_xyz(tmp_path: Path) -> None:
    from acp.scheduler.runner import _materialize_single_input

    dest = _materialize_single_input(
        {"xyz_text": "1\nH\nH 0 0 0.74\n"}, tmp_path / "INPUT", tmp_path
    )
    assert dest is not None and dest.is_file()
    content = dest.read_text(encoding="utf-8")
    assert "H 0 0 0.74" in content
    assert content.splitlines()[0].strip() == "1"


def test_typed_xyz_text_still_materializes(tmp_path: Path) -> None:
    from acp.scheduler.runner import _materialize_single_input

    dest = _materialize_single_input(
        {"source_type": "xyz_text", "source": "2\nH2\nH 0 0 0\nH 0 0 0.74\n"},
        tmp_path / "INPUT",
        tmp_path,
    )
    assert dest is not None and dest.is_file()
    assert "H 0 0 0.74" in dest.read_text(encoding="utf-8")


def test_bare_smiles_still_materializes(tmp_path: Path) -> None:
    from acp.scheduler.runner import _materialize_single_input

    dest = _materialize_single_input({"smiles": "CCO"}, tmp_path / "INPUT", tmp_path)
    assert dest is not None and dest.is_file()
    assert dest.read_text(encoding="utf-8").strip()
