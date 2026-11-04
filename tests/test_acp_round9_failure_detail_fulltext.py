"""Round-9 M14: a failure message stays short but is never the only home.

The external agent saw an IRC failure whose message was clipped mid-sentence
(``… Inspect the run log for the endpoint file fo…``) and there was nowhere
else to read the diagnostic.  Round-8 bounded the agent-facing summary at 200
characters; round-9 adds the second home:
``metadata["failure_details"]["diagnostic_message"]``.

Contract pinned here:

* a clipped message ends with the pointer
  ``… full text in failure_details.diagnostic_message`` and the message (or its
  ``[<class>]: <summary>`` part) stays ≤200 chars;
* the full text is published verbatim under ``diagnostic_message`` — bounded by
  4000 chars, no ellipsis in the middle, host paths reduced to basenames;
* a message that never clipped carries no pointer and the diagnostic equals it;
* ``reason_lines`` keep the round-8 bounds (≤5 lines, ≤200 chars each);
* neither surface leaks a host absolute path;
* the pointer adds no token that would misroute the rescue classifier.

Every fixture is **synthetic** (no real failed IRC/TS log exists in the repo):
the tails are ORCA-wording excerpts and the IRC frames are generated XYZs.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cccp.calculation.tasks.optimize import derive_failure_type
from cccp.qc.interfaces.orca import (
    _DIAGNOSTIC_MESSAGE_MAX_CHARS,
    _FAILURE_DETAILS_POINTER,
    _MESSAGE_UNSAFE_SUBSTRINGS,
    ORCAInterface,
    _failure_details,
    _failure_message,
    _failure_summary,
    diagnostic_message_pair,
)

TS_COORDINATES = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
)
TS_SYMBOLS = ["H", "C", "H"]

#: Two printed reason lines: each fits the per-line bound (<200 chars), the
#: joined summary does not — the round-8 clip therefore has to fire.
LONG_REASON_ONE = (
    "ERROR: the endpoint file format was not recognized while the reader "
    "expected one complete frame but found a multi-frame trajectory file"
)
LONG_REASON_TWO = (
    "ERROR: the Hessian handoff was missing so the walk aborted before any "
    "formal endpoint was written; inspect the run log for the endpoint format"
)
LONG_TAIL = "ORCA finished by error termination\n" + LONG_REASON_ONE + "\n" + LONG_REASON_TWO + "\n"

SHORT_TAIL = "ERROR: cannot open /home/alice/work/opt.out\n"

_MESSAGE_PREFIX = "ORCA transition-state optimization failed"


def _summary_of(message: str, prefix: str) -> str:
    """The ``[<class>]: <summary>`` tail of an orca failure message."""
    assert message.startswith(prefix)
    marker = message.find("]: ")
    assert marker >= 0, message
    return message[marker + 3 :]


# ── orca layer: summary clips, full text survives ───────────────────────


def test_long_orca_diagnostic_keeps_the_full_text_behind_the_pointer() -> None:
    details = _failure_details(LONG_TAIL)
    summary = _failure_summary("error_termination", details)
    full = details["diagnostic_message"]

    assert len(summary) <= 200
    assert summary.endswith(_FAILURE_DETAILS_POINTER)
    # The full text is the unclipped builder output: verbatim, no inner "…".
    assert len(full) > 200
    assert "…" not in full
    assert full.startswith(LONG_REASON_ONE)
    assert LONG_REASON_TWO in full
    assert full.endswith("terminal=error_termination")
    # The clip happened on a word boundary, not inside one.
    head = summary[: -len(_FAILURE_DETAILS_POINTER)].rstrip()
    assert full.startswith(head)
    assert full[len(head)] == " "

    message = _failure_message(_MESSAGE_PREFIX, "error_termination", details)
    assert _FAILURE_DETAILS_POINTER in message
    assert len(_summary_of(message, _MESSAGE_PREFIX)) <= 200


def test_short_orca_diagnostic_has_no_pointer_and_equals_the_message() -> None:
    details = _failure_details(SHORT_TAIL)
    summary = _failure_summary("error_termination", details)
    full = details["diagnostic_message"]
    message = _failure_message(_MESSAGE_PREFIX, "error_termination", details)

    assert _FAILURE_DETAILS_POINTER not in summary
    assert _FAILURE_DETAILS_POINTER not in message
    assert summary == full  # the short failure's diagnostic *is* the message body
    assert message == f"{_MESSAGE_PREFIX} [error_termination]: {summary}"
    assert "opt.out" in full


def test_orca_details_always_publish_diagnostic_message() -> None:
    """The key exists even when there is nothing to say (shape stability)."""
    details = _failure_details("")
    assert details["diagnostic_message"] == ""
    assert _failure_summary("unknown", details) == ""
    assert _failure_message(_MESSAGE_PREFIX, "unknown", details) == (f"{_MESSAGE_PREFIX} [unknown]")


def test_reason_lines_keep_the_round8_bounds() -> None:
    long_line = "ERROR: " + "x" * 500
    tail = "\n".join(f"{long_line}{index}" for index in range(10))
    details = _failure_details(tail)

    assert len(details["reason_lines"]) <= 5
    assert all(len(line) <= 200 for line in details["reason_lines"])


def _run_ts_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
    tail: str,
):
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file, output_file) -> bool:
        _ = Path(output_file).write_text(tail, encoding="utf-8")
        return False

    monkeypatch.setattr(interface, "_run_orca", _fake_run)
    return interface.transition_state_opt(
        TS_COORDINATES,
        TS_SYMBOLS,
        output_dir=tmp_path,
        output_name="ts_full",
    )


def test_ts_failure_metadata_carries_the_full_diagnostic(
    tmp_path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, object]
) -> None:
    result = _run_ts_failure(tmp_path, monkeypatch, sample_config, LONG_TAIL)

    assert result.success is False
    message = result.error_message or ""
    summary = _summary_of(message, _MESSAGE_PREFIX)
    assert len(summary) <= 200
    assert summary.endswith(_FAILURE_DETAILS_POINTER)

    details = result.metadata["failure_details"]
    full = details["diagnostic_message"]
    assert LONG_REASON_TWO in full
    assert "…" not in full
    assert "/home/" not in message and "/tmp/" not in message
    assert "/home/" not in full and "/tmp/" not in full


# ── shared builder used by the IRC task ─────────────────────────────────


def test_bounded_diagnostic_never_cuts_mid_word() -> None:
    text = "IRC diagnostics " + " ".join(f"frame{index:04d}" for index in range(2000))
    short, full = diagnostic_message_pair(text)

    assert len(full) <= _DIAGNOSTIC_MESSAGE_MAX_CHARS
    assert full.endswith("…")
    body = full[:-1]
    assert text.startswith(body)
    assert text[len(body)] == " "  # the ellipsis follows a complete word
    assert len(short) <= 200
    assert short.endswith(_FAILURE_DETAILS_POINTER)
    head = short[: -len(_FAILURE_DETAILS_POINTER)].rstrip()
    assert text.startswith(head)
    assert text[len(head)] == " "


def test_diagnostic_surfaces_never_carry_a_host_absolute_path() -> None:
    text = (
        "IRC produced no endpoint geometries (the reader rejected "
        "/home/alice/ACPServer/runs/task/WORK/03_IRC/ts_IRC_F.xyz and the fallback "
        "/tmp/scratch/ts.hess was missing, so no formal endpoint exists. Inspect the "
        "run log for the endpoint file format, or rerun with a Hessian handoff.)"
    )
    short, full = diagnostic_message_pair(text)

    assert _FAILURE_DETAILS_POINTER in short
    assert "/home/" not in short and "/tmp/" not in short
    assert "/home/" not in full and "/tmp/" not in full
    assert "ts_IRC_F.xyz" in full and "ts.hess" in full
    assert "Inspect the run log for the endpoint file format" in full


def test_pointer_adds_no_rescue_misrouting_token() -> None:
    pointer = _FAILURE_DETAILS_POINTER.lower()
    assert not any(token in pointer for token in _MESSAGE_UNSAFE_SUBSTRINGS)

    details = _failure_details(LONG_TAIL)
    message = _failure_message(_MESSAGE_PREFIX, "error_termination", details)
    assert _FAILURE_DETAILS_POINTER in message
    # Round-8 invariant: a non-SCF/non-timeout tail keeps the geometry chain
    # even with the round-9 pointer appended.
    assert derive_failure_type(message) == "geometry_not_converged"


# ── IRC task: the trailing diagnostic sentence is never lost ────────────


def _irc_failure(tmp_path: Path, frames: int, *, suffix: str = ""):
    from tests.test_cccp_task_irc import (
        _qc_result,
        _RecordingBackend,
        _run,
        _write_trj,
    )

    target_dir = tmp_path / f"irc_work{suffix}"
    target_dir.mkdir()
    if frames:
        _write_trj(
            target_dir / "ts_IRC_F_trj.xyz",
            "forward",
            tuple(-77.20 - 0.05 * index for index in range(frames)),
        )
    backend = _RecordingBackend([_qc_result(success=True)])
    return _run(backend, target_dir=target_dir)


def test_irc_frames_without_endpoints_keeps_the_trailing_sentence(tmp_path: Path) -> None:
    result = _irc_failure(tmp_path, frames=3)

    assert result.status == "failed"
    message = result.errors[0]
    assert message.startswith("IRC produced no endpoint geometries")
    assert len(message) <= 200
    assert message.endswith(_FAILURE_DETAILS_POINTER)

    full = result.metadata["failure_details"]["diagnostic_message"]
    assert len(full) > 200
    # The sentence the round-8 clip used to eat survives verbatim.
    assert (
        "Inspect the run log for the endpoint file format, or rerun with a Hessian handoff." in full
    )
    assert "3 trajectory frame(s) captured for forward" in full
    assert "…" not in full
    head = message[: -len(_FAILURE_DETAILS_POINTER)].rstrip()
    assert full.startswith(head)
    assert full[len(head)] == " "

    for surface in (message, full):
        assert "/home/" not in surface
        assert "/tmp/" not in surface


def test_irc_short_failure_has_no_pointer_and_equals_the_diagnostic(tmp_path: Path) -> None:
    result = _irc_failure(tmp_path, frames=0)

    assert result.status == "failed"
    message = result.errors[0]
    assert len(message) <= 200
    assert _FAILURE_DETAILS_POINTER not in message
    full = result.metadata["failure_details"]["diagnostic_message"]
    assert full == message
    assert "no trajectory frames were captured" in full


def test_irc_backend_exception_keeps_the_full_text(tmp_path: Path) -> None:
    from tests.test_cccp_task_irc import _RecordingBackend, _run

    target_dir = tmp_path / "irc_work_exc"
    target_dir.mkdir()
    detail = (
        "ORCA IRC crashed while reading /home/alice/work/irc/ts_IRC_F.xyz: the "
        "endpoint file was truncated mid-frame, so the reader discarded every "
        "frame, no formal endpoint was published, and the forward walk has to be "
        "rerun after the truncated frame is removed from the trajectory"
    )
    result = _run(_RecordingBackend([RuntimeError(detail)]), target_dir=target_dir)

    assert result.status == "failed"
    message = result.errors[0]
    assert len(message) <= 200
    assert _FAILURE_DETAILS_POINTER in message
    full = result.metadata["failure_details"]["diagnostic_message"]
    assert "no formal endpoint was published" in full
    assert "ts_IRC_F.xyz" in full
    assert "/home/" not in full
    assert "…" not in full
