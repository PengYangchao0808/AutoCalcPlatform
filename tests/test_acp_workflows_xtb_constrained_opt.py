"""Unit tests for the XtbConstrainedOpt workflow contract (no xtb binary needed).

Covers the G2-AB3 WP-1 acceptance essentials:

* request validation is typed and never defaults the recipe;
* log parsing distinguishes geometry convergence, geometry non-convergence
  (which still exits 0 and writes xtbopt.xyz), and SCC failures;
* energy evidence separates the restraint-including TOTAL ENERGY from an
  unrestrained single point.
"""

from __future__ import annotations

import pytest

from acp.workflows.xtb_constrained_opt import (
    XTC_SCHEMA,
    XtbConstrainedOptInputError,
    parse_opt_log,
    validate_request,
    _fix_block,
)


def _base_request() -> dict:
    return {
        "schema_version": XTC_SCHEMA,
        "reaction_id": "RXN_TEST",
        "source": {
            "geometry": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
            "elements": ["H", "C", "O"],
            "charge": 0,
            "multiplicity": 1,
        },
        "recipe": {
            "gfn_level": 2,
            "constraints": [
                {"kind": "distance", "atoms": [0, 1], "target": 1.1, "unit": "angstrom"}
            ],
            "force_constant": 50.0,
            "opt_level": "normal",
            "max_cycle": 500,
            "physical_sp": True,
            "threads": 4,
            "timeout_seconds": 300,
        },
        "provenance": {"point_id": "lam_0.05", "request_sha256": "ab" * 32},
    }


class TestValidateRequest:
    def test_accepts_valid_request(self):
        request = validate_request(_base_request())
        assert request.reaction_id == "RXN_TEST"
        assert request.constraints[0].kind == "distance"
        assert request.constraints[0].atoms == (0, 1)
        assert request.force_constant == 50.0
        assert request.physical_sp is True
        assert request.fix_atoms is None

    def test_wrong_schema_is_typed_error(self):
        payload = _base_request()
        payload["schema_version"] = "something_else"
        with pytest.raises(XtbConstrainedOptInputError, match="XTC_E_SCHEMA"):
            validate_request(payload)

    def test_geometry_row_count_must_match_elements(self):
        payload = _base_request()
        payload["source"]["geometry"] = payload["source"]["geometry"][:2]
        with pytest.raises(XtbConstrainedOptInputError, match="expected"):
            validate_request(payload)

    def test_atom_index_out_of_range_is_rejected(self):
        payload = _base_request()
        payload["recipe"]["constraints"][0]["atoms"] = [0, 5]
        with pytest.raises(XtbConstrainedOptInputError, match="out of range"):
            validate_request(payload)

    def test_distance_requires_angstrom_unit(self):
        payload = _base_request()
        payload["recipe"]["constraints"][0]["unit"] = "bohr"
        with pytest.raises(XtbConstrainedOptInputError, match="unit"):
            validate_request(payload)

    def test_empty_constraints_rejected(self):
        payload = _base_request()
        payload["recipe"]["constraints"] = []
        with pytest.raises(XtbConstrainedOptInputError, match="non-empty"):
            validate_request(payload)

    def test_fix_atoms_validated_and_sorted(self):
        payload = _base_request()
        payload["recipe"]["fix_atoms"] = [2, 0, 1, 1]
        request = validate_request(payload)
        assert request.fix_atoms == (0, 1, 2)

    def test_fix_atom_out_of_range_rejected(self):
        payload = _base_request()
        payload["recipe"]["fix_atoms"] = [0, 9]
        with pytest.raises(XtbConstrainedOptInputError, match="out of range"):
            validate_request(payload)

    def test_bad_opt_level_rejected(self):
        payload = _base_request()
        payload["recipe"]["opt_level"] = "ludicrous"
        with pytest.raises(XtbConstrainedOptInputError, match="opt_level"):
            validate_request(payload)


class TestFixBlock:
    def test_contiguous_and_scattered(self):
        assert _fix_block((0, 1, 2, 5)) == "$fix\n  atoms: 1-3, 6\n$end\n"

    def test_single_atom(self):
        assert _fix_block((3,)) == "$fix\n  atoms: 4\n$end\n"

    def test_empty_is_empty(self):
        assert _fix_block(()) == ""


CONVERGED_LOG = """
   * total energy :  -33.3464041 Eh    change  -0.13E+01
   * total energy :  -33.8098037 Eh    change  -0.57E-03
   *** GEOMETRY OPTIMIZATION CONVERGED AFTER 27 ITERATIONS ***
         :: total energy             -33.811067821211 Eh    ::
          | TOTAL ENERGY              -33.811067821211 Eh   |
          | GRADIENT NORM               0.000568756903 Eh/α |
normal termination of xtb
"""

NOT_CONVERGED_LOG = """
   * total energy :  -33.3464041 Eh    change  -0.13E+01
   *** convergence criteria satisfied after 13 iterations ***
   *** FAILED TO CONVERGE GEOMETRY OPTIMIZATION IN 3 ITERATIONS ***
          | TOTAL ENERGY              -33.888973158288 Eh   |
normal termination of xtb
"""

SCC_FAIL_LOG = """
   * total energy :  -33.34 Eh
WARNING: SCC is not converged after 500 iterations!
          | TOTAL ENERGY              -33.5 Eh   |
abnormal termination of xtb
"""


class TestParseOptLog:
    def test_converged(self):
        evidence = parse_opt_log(CONVERGED_LOG)
        assert evidence["converged_geometry"] is True
        assert evidence["failed_to_converge"] is False
        assert evidence["convergence_marker"] == "GEOMETRY_OPTIMIZATION_CONVERGED_AFTER_27_ITERATIONS"
        assert evidence["n_geometry_iterations"] == 27
        assert evidence["normal_termination"] is True
        assert evidence["n_optimization_cycles_logged"] == 2
        assert evidence["energy_effective_hartree"] == pytest.approx(-33.811067821211)

    def test_not_converged_still_normal_termination(self):
        evidence = parse_opt_log(NOT_CONVERGED_LOG)
        assert evidence["converged_geometry"] is False
        assert evidence["failed_to_converge"] is True
        assert evidence["convergence_marker"] == "FAILED_TO_CONVERGE_GEOMETRY_OPTIMIZATION_IN_3_ITERATIONS"
        # The SCC line ("convergence criteria satisfied") must not be mistaken
        # for geometry convergence.
        assert evidence["normal_termination"] is True

    def test_scc_failure_detected(self):
        evidence = parse_opt_log(SCC_FAIL_LOG)
        assert evidence["scc_failure_detected"] is True
        assert evidence["converged_geometry"] is False
        assert evidence["normal_termination"] is False

    def test_empty_log_is_all_unknown(self):
        evidence = parse_opt_log("")
        assert evidence["converged_geometry"] is False
        assert evidence["convergence_marker"] is None
        assert evidence["energy_effective_hartree"] is None
        assert evidence["n_optimization_cycles_logged"] is None
