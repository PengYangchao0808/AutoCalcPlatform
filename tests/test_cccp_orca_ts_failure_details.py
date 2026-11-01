"""Round-8 三.3: an ORCA TS failure must say *what* failed.

The external agent saw ``ORCA transition-state optimization failed [unknown]``
three times in a row: the classifier only knew four home-grown patterns and the
message carried no reason, no cycle counts and no imaginary-mode count, so the
only way to learn anything was to grep the ORCA output.

These tests pin the round-8 contract:

* the classifier still returns the legacy classes for the legacy wording, and
  additionally recognizes real ORCA phrasing (Hessian not positive definite,
  maximum-cycle wording, generic convergence failure, ``error termination``);
* ``_failure_details`` reports ``reason_lines`` (≤5, host paths reduced to
  basenames), ``scf_cycles``, ``opt_cycles``, ``imaginary_count`` and
  ``terminal`` — absent facts stay ``None``, a real 0 stays 0;
* ``TsOptResult.error_message`` is ``failed [<class>]: <summary>`` (summary
  ≤200 chars, no absolute host path) and ``metadata["failure_details"]``
  carries the same structure;
* a genuinely unknown failure keeps the bare ``[unknown]`` message;
* the message never misroutes the downstream rescue classifier
  (``derive_failure_type``), which scans the message text for tokens such as
  ``scf``/``imaginary``/``timeout``.

Every ORCA tail below is a **synthetic** excerpt written from ORCA 6's printed
wording; no repository fixture with a real failed OptTS log exists.
"""

from __future__ import annotations

import numpy as np
import pytest

from cccp.qc.interfaces.orca import (
    ORCAInterface,
    _failure_details,
    _failure_summary,
    _redact_host_paths,
    classify_orca_failure,
)

TS_COORDINATES = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ]
)
TS_SYMBOLS = ["H", "C", "H"]

#: Synthetic OptTS failure tails (ORCA 6 wording).
SCF_NOT_CONVERGED_TAIL = """\
----------------
SCF ITERATIONS
----------------
SCF CONVERGED AFTER  12 CYCLES
------------------------------------------------------------
ERROR: SCF NOT CONVERGED AFTER 500 CYCLES
   file    ... /home/alice/ACPServer/runs/task/WORK/03_OPT/ts/opt.out
ORCA finished by error termination in SCF
"""

HESSIAN_NOT_POSITIVE_DEFINITE_TAIL = """\
VIBRATIONAL FREQUENCIES
-----------------------
   0:      -312.45 cm**-1 ***imaginary mode***
   1:       -87.12 cm**-1 ***imaginary mode***
   2:       102.33 cm**-1
   3:       144.02 cm**-1
GEOMETRY OPTIMIZATION CYCLE   7
Hessian is not positive definite
Aborting the optimization
****ORCA TERMINATED NORMALLY****
"""

OPT_MAXITER_TAIL = """\
GEOMETRY OPTIMIZATION CYCLE   1
GEOMETRY OPTIMIZATION CYCLE   2
Maximum number of optimization cycles reached
ORCA finished by error termination
   the last geometry was written to /home/bob/work/ts/ts_opt.xyz
"""

GENERIC_CONVERGENCE_TAIL = """\
The optimization did not converge
CONVERGENCE FAILURE
"""

GENUINELY_UNKNOWN_TAIL = """\
ORCA calculation finished but produced no usable geometry
"""

PLAIN_TERMINATION_TAIL = "****ORCA TERMINATED NORMALLY****\n"


# ── classifier ──────────────────────────────────────────────────────────


def _classify_text(tmp_path, text: str) -> str:
    path = tmp_path / "ts.out"
    path.write_text(text, encoding="utf-8")
    return classify_orca_failure(path)


def test_legacy_classes_keep_their_meaning(tmp_path) -> None:
    assert (
        _classify_text(tmp_path, "SCF NOT CONVERGED AFTER 300 ITERATIONS\n") == "scf_failure"
    )
    assert (
        _classify_text(tmp_path, "THE OPTIMIZATION HAS NOT CONVERGED AFTER 100 CYCLES\n")
        == "geometry_not_converged"
    )
    assert (
        _classify_text(tmp_path, "ERROR: cannot allocate 4096 MB of memory\n")
        == "memory_failure"
    )
    assert _classify_text(tmp_path, GENUINELY_UNKNOWN_TAIL) == "unknown"
    assert _classify_text(tmp_path, "") == "unknown"


def test_real_orca_ts_wording_is_classified(tmp_path) -> None:
    assert (
        _classify_text(tmp_path, HESSIAN_NOT_POSITIVE_DEFINITE_TAIL)
        == "hessian_not_positive_definite"
    )
    assert _classify_text(tmp_path, OPT_MAXITER_TAIL) == "opt_maxiter"
    assert (
        _classify_text(tmp_path, GENERIC_CONVERGENCE_TAIL) == "geometry_not_converged"
    )
    assert _classify_text(tmp_path, SCF_NOT_CONVERGED_TAIL) == "scf_failure"


def test_scf_not_converged_wording_gets_its_own_class(tmp_path) -> None:
    """Legacy SCF wording keeps ``scf_failure``; the other phrasing gets a name."""
    assert (
        _classify_text(tmp_path, "SCF NOT CONVERGED AFTER 300 ITERATIONS\n")
        == "scf_failure"
    )
    assert (
        _classify_text(tmp_path, "The SCF did not converge after 200 iterations\n")
        == "scf_not_converged"
    )
    # Both project onto the SCF rescue chain downstream (token "scf").
    from acp.calculations.primitives.optimize import derive_failure_type

    assert derive_failure_type("failed [scf_not_converged]: SCF did not converge") == (
        "scf_failure"
    )


def test_hessian_class_does_not_mask_a_terminal_not_converged_message(tmp_path) -> None:
    """An early Hessian warning must not hide the terminal failure wording."""
    text = (
        "WARNING: Hessian is not positive definite, using model Hessian\n"
        "THE OPTIMIZATION HAS NOT CONVERGED AFTER 150 CYCLES\n"
    )
    assert _classify_text(tmp_path, text) == "geometry_not_converged"


def test_error_termination_alone_is_still_named(tmp_path) -> None:
    text = "something exploded\nORCA finished by error termination\n"
    assert _classify_text(tmp_path, text) == "error_termination"


# ── failure details ─────────────────────────────────────────────────────


def test_details_shape_and_counters() -> None:
    details = _failure_details(HESSIAN_NOT_POSITIVE_DEFINITE_TAIL)
    assert set(details) == {
        "reason_lines",
        "scf_cycles",
        "opt_cycles",
        "imaginary_count",
        "terminal",
    }
    assert details["opt_cycles"] == 7
    assert details["imaginary_count"] == 2
    assert details["scf_cycles"] is None
    assert details["terminal"] == "aborted"
    assert details["reason_lines"]
    assert any("positive definite" in line.lower() for line in details["reason_lines"])
    assert len(details["reason_lines"]) <= 5


def test_imaginary_count_is_zero_when_the_block_has_none() -> None:
    text = """\
VIBRATIONAL FREQUENCIES
-----------------------
   0:       102.33 cm**-1
   1:       144.02 cm**-1
"""
    details = _failure_details(text)
    assert details["imaginary_count"] == 0


def test_scf_cycles_are_reported_for_scf_failures() -> None:
    details = _failure_details(SCF_NOT_CONVERGED_TAIL)
    assert details["scf_cycles"] == 500
    assert details["terminal"] == "error_termination"


def test_missing_facts_stay_none() -> None:
    details = _failure_details(GENUINELY_UNKNOWN_TAIL)
    assert details["scf_cycles"] is None
    assert details["opt_cycles"] is None
    assert details["imaginary_count"] is None
    assert details["terminal"] is None


def test_reason_lines_never_carry_an_absolute_host_path() -> None:
    text = (
        "ERROR: cannot open /home/alice/ACPServer/runs/task/WORK/03_OPT/ts/opt.out\n"
        "ERROR: unable to read C:\\Users\\bob\\work\\ts\\opt.inp\n"
        "ERROR: missing ~/scratch/ts/freq.hess\n"
    )
    details = _failure_details(text)
    blob = "\n".join(details["reason_lines"])
    assert "/home/" not in blob
    assert "C:\\" not in blob
    assert "~/scratch" not in blob
    assert "opt.out" in blob
    assert "opt.inp" in blob
    assert "freq.hess" in blob


def test_redaction_keeps_non_path_slashes() -> None:
    text = "and/or E/D 0.0/1.0 in (/tmp/a.out)"
    assert _redact_host_paths(text) == "and/or E/D 0.0/1.0 in (a.out)"


# ── TsOptResult message + metadata ──────────────────────────────────────


def _run_ts_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
    tail: str,
):
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file, output_file) -> bool:
        _ = output_file.write_text(tail, encoding="utf-8")
        return False

    monkeypatch.setattr(interface, "_run_orca", _fake_run)
    return interface.transition_state_opt(
        TS_COORDINATES,
        TS_SYMBOLS,
        output_dir=tmp_path,
        output_name="ts_failed",
    )


@pytest.mark.parametrize(
    ("tail", "expected_class"),
    [
        (HESSIAN_NOT_POSITIVE_DEFINITE_TAIL, "hessian_not_positive_definite"),
        (OPT_MAXITER_TAIL, "opt_maxiter"),
        (SCF_NOT_CONVERGED_TAIL, "scf_failure"),
        (GENERIC_CONVERGENCE_TAIL, "geometry_not_converged"),
    ],
)
def test_ts_failure_message_names_the_class_and_the_reason(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
    tail: str,
    expected_class: str,
) -> None:
    result = _run_ts_failure(tmp_path, monkeypatch, sample_config, tail)

    assert result.success is False
    message = result.error_message or ""
    assert message.startswith(
        f"ORCA transition-state optimization failed [{expected_class}]"
    )
    summary = message.split("]: ", 1)[1] if "]: " in message else ""
    assert summary, "a recognized failure must carry a summary"
    assert len(summary) <= 200
    assert "/home/" not in message
    assert "C:\\" not in message
    # The printed reason line survives (not just a bare class token).
    assert "convergence" in message.lower() or "cycle" in message.lower()

    details = result.metadata.get("failure_details")
    assert isinstance(details, dict)
    assert set(details) == {
        "reason_lines",
        "scf_cycles",
        "opt_cycles",
        "imaginary_count",
        "terminal",
    }


def test_unknown_ts_failure_keeps_the_bare_message(
    tmp_path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, object]
) -> None:
    result = _run_ts_failure(tmp_path, monkeypatch, sample_config, "")

    assert result.success is False
    assert result.error_message == "ORCA transition-state optimization failed [unknown]"
    details = result.metadata.get("failure_details")
    assert details is not None
    assert details["reason_lines"] == []


def test_normal_termination_without_geometry_is_explained(
    tmp_path, monkeypatch: pytest.MonkeyPatch, sample_config: dict[str, object]
) -> None:
    """A normal exit that printed no geometry still says what the log shows."""
    interface = ORCAInterface(sample_config)

    def _fake_run(_input_file, output_file) -> bool:
        _ = output_file.write_text(PLAIN_TERMINATION_TAIL, encoding="utf-8")
        return True

    monkeypatch.setattr(interface, "_run_orca", _fake_run)
    result = interface.transition_state_opt(
        TS_COORDINATES,
        TS_SYMBOLS,
        output_dir=tmp_path,
        output_name="ts_terminated",
    )

    assert result.success is False
    message = result.error_message or ""
    assert message.startswith("ORCA transition-state optimization failed [unknown]")
    assert "terminal=normal" in message
    assert result.metadata["failure_details"]["terminal"] == "normal"


# ── downstream rescue classifier must not be misrouted ──────────────────


@pytest.mark.parametrize(
    ("tail", "expected_rescue"),
    [
        (HESSIAN_NOT_POSITIVE_DEFINITE_TAIL, "geometry_not_converged"),
        (OPT_MAXITER_TAIL, "geometry_not_converged"),
        (GENERIC_CONVERGENCE_TAIL, "geometry_not_converged"),
        (SCF_NOT_CONVERGED_TAIL, "scf_failure"),
    ],
)
def test_message_tokens_do_not_misroute_the_rescue_classifier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    sample_config: dict[str, object],
    tail,
    expected_rescue,
) -> None:
    """``derive_failure_type`` scans the message: no foreign family tokens.

    A Hessian/max-cycle TS failure must keep the geometry rescue chain; an SCF
    failure must keep the SCF chain.  The imaginary count is therefore spelled
    ``nimag=`` and the SCF counter only appears for SCF classes.
    """
    from acp.calculations.primitives.optimize import derive_failure_type

    result = _run_ts_failure(tmp_path, monkeypatch, sample_config, tail)
    assert derive_failure_type(result.error_message or "") == expected_rescue


def test_summary_is_bounded() -> None:
    long_line = "ERROR: " + "x" * 500
    details = _failure_details(long_line)
    summary = _failure_summary("error_termination", details)
    assert len(summary) <= 200
