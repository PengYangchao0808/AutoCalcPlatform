"""Round-7 #7a — a constraint xTB rejects must FAIL, never report COMPLETED.

xTB's worst failure mode: a malformed ``$constrain`` directive only prints a
warning ("not enough arguments to constrain a distance"), then optimizes
WITHOUT the constraint and exits 0.  The platform reported COMPLETED for an
unconstrained geometry.

Locked here:

1. the typed builder only emits syntax xTB honors (``kind: i, j, value`` plus a
   block-level ``force constant=`` line) and refuses malformed typed input;
2. raw ``.xcontrol`` text is validated before execution (submit-time seam for
   the raw path payloads) with a typed ``rule="constraint_syntax_invalid"``;
3. the task core scans the xTB log and turns a refused constraint into a
   FAILED task with ``rule="constraints_not_honored"`` — never COMPLETED.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cccp.calculation.context import TaskContext
from cccp.calculation.requests import (
    StructureInput,
    TaskKind,
    TaskRequest,
    XtbConstrainedOptConstraint,
    XtbConstrainedOptOptions,
)
from cccp.calculation.results import ErrorKind
from cccp.calculation.tasks.xtb_constrained_opt import run_xtb_constrained_opt
from cccp.qc.interfaces.constraints import AngleConstraint, DistanceConstraint
from cccp.qc.interfaces.xtb import XTBInterface
from cccp.qc.interfaces.xtb_scan import (
    XtbConstraintSyntaxError,
    detect_constraint_rejection,
    parse_constraint_evidence,
    parse_xcontrol_constraints,
    validate_xcontrol_text,
    xcontrol_constraint_block,
)

_START = ((0.0, 0.0, 0.0), (1.3, 0.0, 0.0), (2.5, 0.0, 0.0))
_SYMBOLS = ("H", "C", "O")

#: What xTB 6.7.1 writes when it drops a constraint (then exits 0).
REJECTED_LOG = """\
   * total energy :  -33.3464041 Eh    change  -0.13E+01
WARNING: not enough arguments to constrain a distance
   * total energy :  -33.8098037 Eh    change  -0.57E-03
   *** GEOMETRY OPTIMIZATION CONVERGED AFTER 27 ITERATIONS ***
           | TOTAL ENERGY              -33.811067821211 Eh   |
normal termination of xtb
"""

CLEAN_LOG = """\
   * total energy :  -33.3464041 Eh    change  -0.13E+01
   *** GEOMETRY OPTIMIZATION CONVERGED AFTER 27 ITERATIONS ***
           | TOTAL ENERGY              -33.811067821211 Eh   |
normal termination of xtb
"""


# ── typed builder ───────────────────────────────────────────────────────


def test_typed_builder_emits_the_syntax_xtb_honors() -> None:
    block = xcontrol_constraint_block(
        [DistanceConstraint(atoms=(2, 3), target=1.95, force_constant=0.5)]
    )
    assert block == (
        "$constrain\n"
        "  force constant=0.5000\n"
        "  distance: 3, 4, 1.950000\n"
        "$end"
    )
    # The 4th per-line token is never emitted (xTB ignores it).
    assert ", 0.5000" not in block


def test_typed_builder_keeps_mixed_force_constants_numeric() -> None:
    block = xcontrol_constraint_block(
        [
            DistanceConstraint(atoms=(0, 1), target=1.2, force_constant=0.5),
            AngleConstraint(atoms=(0, 1, 2), target=104.5, force_constant=0.25),
        ]
    )
    lines = block.splitlines()
    assert lines[0] == "$constrain"
    assert "force constant=" not in block
    assert lines[1] == "  distance: 1, 2, 1.200000, 0.5000"
    assert lines[2] == "  angle: 1, 2, 3, 104.500000, 0.2500"


def test_typed_builder_refuses_wrong_atom_count() -> None:
    with pytest.raises(XtbConstraintSyntaxError) as excinfo:
        xcontrol_constraint_block([DistanceConstraint(atoms=(0, 1, 2), target=1.0)])
    assert excinfo.value.params["rule"] == "constraint_syntax_invalid"
    assert "requires 2 atom indices" in str(excinfo.value)


def test_typed_builder_refuses_non_finite_target() -> None:
    with pytest.raises(XtbConstraintSyntaxError):
        xcontrol_constraint_block([DistanceConstraint(atoms=(0, 1), target=float("nan"))])


# ── raw xcontrol text validation ────────────────────────────────────────


def test_validate_accepts_the_documented_syntax() -> None:
    parsed = validate_xcontrol_text(
        "$constrain\n"
        "  distance: 3, 4, 1.95\n"
        "  force constant=0.5\n"
        "$end\n"
        "$fix\n"
        "  atoms: 1-3\n"
        "$end\n"
    )
    assert parsed["directives"] == [
        {
            "kind": "distance",
            "atoms": (3, 4),
            "target": 1.95,
            "line": "distance: 3, 4, 1.95",
        }
    ]
    assert parsed["force_constant"] == pytest.approx(0.5)
    assert parsed["blocks"] == 1


def test_validate_rejects_fourth_per_line_token() -> None:
    with pytest.raises(XtbConstraintSyntaxError) as excinfo:
        validate_xcontrol_text("$constrain\n  distance: 3, 4, 1.95, auto\n$end\n")
    error = excinfo.value
    assert error.params["rule"] == "constraint_syntax_invalid"
    assert error.params["field"] == "constraints"
    assert "distance: 3, 4, 1.95, auto" in error.params["line"]
    message = str(error)
    assert "not enough arguments to constrain a distance" in message
    assert "force constant=0.5" in message


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("  distance: 3, 4\n", "fields"),
        ("  distance: 3, 4, auto\n", "not numeric"),
        ("  angle: 1, 2, 104.5\n", "fields"),
        ("  dihedral: 1, 2, 3, 4\n", "fields"),
        ("  bond: 1, 2, 1.5\n", "unknown"),
        ("  distance: 0, 4, 1.5\n", "1-based"),
        ("  distance: a, 4, 1.5\n", "integer"),
        ("  something else\n", "unrecognised"),
    ],
)
def test_validate_rejects_malformed_directives(body: str, fragment: str) -> None:
    with pytest.raises(XtbConstraintSyntaxError) as excinfo:
        validate_xcontrol_text(f"$constrain\n{body}$end\n")
    assert fragment in str(excinfo.value)


def test_validate_rejects_unterminated_block() -> None:
    with pytest.raises(XtbConstraintSyntaxError, match=r"\$end"):
        validate_xcontrol_text("$constrain\n  distance: 3, 4, 1.95\n")


def test_parse_ignores_other_xcontrol_blocks() -> None:
    parsed = parse_xcontrol_constraints(
        "$fix\n  atoms: 1-3\n$end\n$opt\n  maxcycle=200\n$end\n"
    )
    assert parsed == {"directives": [], "force_constant": None, "blocks": 0}


def test_interface_refuses_to_write_a_rejected_constraint() -> None:
    """The composed xcontrol text is validated BEFORE the xTB process."""
    iface = XTBInterface.__new__(XTBInterface)
    iface.nproc = 1
    iface.gfn_level = 2
    iface._require_executable = lambda: "/bin/true"  # type: ignore[method-assign]
    iface._solvent_args = lambda: []  # type: ignore[method-assign]

    with pytest.raises(XtbConstraintSyntaxError):
        iface.constrained_optimize(
            [[0.0, 0.0, 0.0], [1.3, 0.0, 0.0]],
            ["H", "C"],
            Path("/tmp/acp-round7-never-written"),
            constraints=[DistanceConstraint(atoms=(0, 1), target=1.2)],
            extra_control_text="$constrain\n  distance: 3, 4, 1.95, auto\n$end\n",
        )


# ── runtime rejection evidence ──────────────────────────────────────────


def test_detect_constraint_rejection_matches_xtb_wording() -> None:
    lines = detect_constraint_rejection(REJECTED_LOG)
    assert lines == ("WARNING: not enough arguments to constrain a distance",)


def test_parse_constraint_evidence_reports_rule_and_field() -> None:
    evidence = parse_constraint_evidence(REJECTED_LOG, requested=1)
    assert evidence["rejected"] is True
    assert evidence["rule"] == "constraints_not_honored"
    assert evidence["field"] == "constraints"
    assert evidence["constraints_requested"] == 1
    clean = parse_constraint_evidence(CLEAN_LOG, requested=1)
    assert clean["rejected"] is False
    assert clean["rule"] == ""


# ── task core: FAILED, never COMPLETED ─────────────────────────────────


class _QCResult:
    def __init__(self, **fields: Any) -> None:
        self.success = fields.get("success", True)
        self.coordinates = fields.get("coordinates")
        self.energy = fields.get("energy")
        self.error_message = fields.get("error_message")


class _FakeXtbBackend:
    name = "xtb"

    def __init__(self, log_text: str) -> None:
        self.log_text = log_text
        self.opt_calls: list[dict[str, Any]] = []
        self.sp_calls: list[dict[str, Any]] = []

    def constrained_optimize(
        self,
        coordinates: Any,
        symbols: Any,
        output_dir: Any,
        constraints: Any = None,
        **kwargs: Any,
    ) -> Any:
        target = Path(output_dir)
        self.opt_calls.append({"output_dir": target, "constraints": list(constraints or ())})
        target.mkdir(parents=True, exist_ok=True)
        (target / "xtb.log").write_text(self.log_text, encoding="utf-8")
        (target / ".xcontrol").write_text(
            "$constrain\n  distance: 3, 4, 1.950000\n$end\n", encoding="utf-8"
        )
        return _QCResult(
            success=True,
            coordinates=((0.0, 0.0, 0.0), (1.1, 0.0, 0.0), (2.2, 0.0, 0.0)),
        )

    def single_point(self, coordinates: Any, symbols: Any, output_dir: Any, **kwargs: Any) -> Any:
        self.sp_calls.append({"output_dir": Path(output_dir), **kwargs})
        return _QCResult(success=True, energy=-33.9)


def _options() -> XtbConstrainedOptOptions:
    return XtbConstrainedOptOptions(
        constraints=(
            XtbConstrainedOptConstraint(
                kind="distance", atoms=(1, 2), target=1.1, unit="angstrom"
            ),
        ),
        force_constant=50.0,
        gfn_level=2,
        opt_level="normal",
        max_steps=500,
        physical_sp=True,
    )


def _request(tmp_path: Path) -> TaskRequest:
    return TaskRequest(
        task=TaskKind.XTB_CONSTRAINED_OPT,
        structure=StructureInput(coordinates=_START, symbols=_SYMBOLS),
        options=_options(),
        output_dir=tmp_path / "run",
    )


def test_task_fails_when_xtb_rejects_the_constraint(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(REJECTED_LOG)
    result = run_xtb_constrained_opt(
        _request(tmp_path),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )

    assert result.status == "failed"
    assert result.complete is False
    assert result.error_kind is ErrorKind.INVALID_INPUT
    assert any("not enough arguments to constrain" in error for error in result.errors)
    assert any("UNCONSTRAINED" in error for error in result.errors)
    evidence = result.metadata["constraint_evidence"]
    assert evidence["rejected"] is True
    assert evidence["rule"] == "constraints_not_honored"
    assert evidence["field"] == "constraints"
    # An unconstrained geometry must never produce a "physical SP" answer.
    assert backend.sp_calls == []
    assert result.payload is None


def test_task_still_completes_for_a_clean_constrained_log(tmp_path: Path) -> None:
    backend = _FakeXtbBackend(CLEAN_LOG)
    result = run_xtb_constrained_opt(
        _request(tmp_path),
        context=TaskContext(backend=backend, workdir=tmp_path),
    )

    assert result.status == "completed"
    assert result.metadata["constraint_evidence"]["rejected"] is False
    assert result.metadata["constraint_evidence"]["constraints_requested"] == 1
    assert len(backend.sp_calls) == 1
