"""Regression tests: string-valued argparse ``choices=`` are case-insensitive.

Locks the single post-processing choke point (``build_parser`` →
``_normalize_string_choices``): any case spelling of an enumerated keyword is
accepted and normalised to the canonical spelling declared by the argument,
while free-form arguments (no ``choices``) and actions that already declare a
``type`` are left untouched.
"""

from __future__ import annotations

import pytest

from acp.cli import build_parser


def parse(argv: list[str]) -> object:
    """Parse *argv* through the real ACP parser (Given/When collapsed)."""
    return build_parser().parse_args(argv)


class TestEnumeratedKeywordsFoldToCanonical:
    def test_batch_optimize_convergence_accepts_catalog_case(self) -> None:
        # Scheduler emits catalog-cased values; the production incident.
        ns = parse(
            [
                "run", "BatchOptimize", "--items-file", "x.xyz",
                "--opt-convergence", "Tight", "--scf-convergence", "VeryTight",
            ]
        )
        assert ns.opt_convergence == "tight"
        assert ns.scf_convergence == "verytight"

    def test_simple_optimize_folds_to_title_case_canonical(self) -> None:
        # Mirror direction: simple-workflow choices are Title-case.
        ns = parse(
            ["run", "optimize", "--input", "x.xyz", "--opt-convergence", "verytight"]
        )
        assert ns.opt_convergence == "VeryTight"

    def test_confsearch_enum_arguments_fold_to_canonical_ids(self) -> None:
        ns = parse(
            [
                "run", "Confsearch", "--input", "CCO",
                "--protocol", "XTB-CREST", "--profile", "LIGHT",
                "--refinement-policy", "SCREEN", "--preset", "CENSO-LIGHT",
            ]
        )
        assert ns.protocol == "xtb-crest"
        assert ns.profile == "light"
        assert ns.refinement_policy == "screen"
        assert ns.preset == "censo-light"

    def test_xtb_optimize_level_and_solvent_fold_to_lowercase(self) -> None:
        ns = parse(
            [
                "run", "xtb_optimize", "--input", "x.xyz",
                "--opt-level", "NORMAL", "--solvent-model", "GBSA",
            ]
        )
        assert ns.opt_level == "normal"
        assert ns.solvent_model == "gbsa"


class TestFreeFormPassthrough:
    def test_method_and_basis_without_choices_keep_user_case(self) -> None:
        ns = parse(
            [
                "run", "BatchOptimize", "--items-file", "x.xyz",
                "--method", "wB97X-D4", "--basis", "def2-TZVPPD",
            ]
        )
        assert ns.optimization_method == "wB97X-D4"
        assert ns.optimization_basis == "def2-TZVPPD"


class TestInvalidValuesStillRejected:
    def test_unknown_convergence_value_exits(self) -> None:
        with pytest.raises(SystemExit):
            parse(
                [
                    "run", "BatchOptimize", "--items-file", "x.xyz",
                    "--opt-convergence", "SuperTight",
                ]
            )

    def test_already_typed_action_is_not_wrapped(self) -> None:
        # ``serve --log-level`` already declares ``type=str``: the helper must
        # leave it alone, so off-canonical case is still rejected.
        with pytest.raises(SystemExit):
            parse(["run", "serve", "--log-level", "debug"])
