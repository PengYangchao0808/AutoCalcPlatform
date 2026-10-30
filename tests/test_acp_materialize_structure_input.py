"""The shared materializer must honour every structure input the contract accepts.

astra round-4 review: a bare ``input.xyz_text`` passed the contract (and the
scheduler read it) but fell through to the SMILES parser at materialization,
so the job could never produce a structure file.  These tests stop at the real
artifact: the written XYZ content, not just a JobSpec field.
"""

from __future__ import annotations

from collections.abc import Mapping
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


def test_every_published_example_materializes(tmp_path: Path) -> None:
    """The published example must itself be an executable submission.

    astra round-4 review: a bare multi-line XYZ in ``input.input`` passes the
    validator and then gets handed to the runner as a ``--input`` *path*
    (the runner's materializer returns None), so every agent copying the
    example would fail after queueing.  The example shape and the materializer
    are therefore checked together.
    """
    from acp.api.workflow_contracts import ACTIVE_WORKFLOWS, describe_workflow
    from acp.scheduler.runner import _materialize_single_input

    _STRUCTURE_KEYS = ("input", "source", "smiles", "input_artifact", "xyz_text")
    checked = 0
    for workflow in ACTIVE_WORKFLOWS:
        for example in describe_workflow(workflow).get("examples") or []:
            inp = dict(example.get("input") or {})
            if not any(key in inp for key in _STRUCTURE_KEYS):
                continue  # source-reference entries (irc/tsmode) carry no structure
            source_value = inp.get("source")
            if isinstance(source_value, Mapping):
                # structured source envelopes (PESsearch/scan scan payloads) are
                # materialized by their own workflow path, not the simple input one
                continue
            if not isinstance(source_value, str) and not isinstance(
                inp.get("xyz_text"), str
            ):
                continue
            dest = _materialize_single_input(inp, tmp_path / workflow / "INPUT", tmp_path)
            assert dest is not None, f"{workflow}: example input does not materialize"
            assert dest.is_file(), workflow
            content = dest.read_text(encoding="utf-8")
            assert content.strip(), workflow
            # a path or a SMILES string written into the .xyz would be the bug
            assert "\n" in content, f"{workflow}: materialized content is not XYZ text"
            checked += 1
    assert checked >= 4


def test_mixed_payload_writes_the_xyz_not_the_smiles(tmp_path: Path) -> None:
    """A mixed payload (source=SMILES + xyz_text) must materialize the XYZ."""
    from acp.scheduler.runner import _materialize_single_input

    xyz = "2\nH2\nH 0 0 0\nH 0 0 0.74\n"
    dest = _materialize_single_input(
        {"source": "CCO", "xyz_text": xyz}, tmp_path / "INPUT", tmp_path
    )
    assert dest is not None and dest.is_file()
    assert "H 0 0 0.74" in dest.read_text(encoding="utf-8")
