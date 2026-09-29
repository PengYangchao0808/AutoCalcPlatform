"""Keyword/parser parity regression tests — the BatchOptimize keyword-case incident lock.

Author: QCcalc Team

Production incident (2026-09): the scheduler emitted a catalog-cased keyword
(``batchoptimize_method_flags({"opt_convergence": "Tight"})`` →
``--opt-convergence Tight``) while the BatchOptimize CLI parser declared
lowercase ``choices``, so ``argparse`` rejected the job's own persisted
``job.json`` configuration at re-parse time. The fix folds case at the parser
boundary (``build_parser`` → ``_normalize_string_choices`` →
``acp.core.keywords.make_case_insensitive_type``) so every catalog-declared
option survives the emit → parse round trip in ANY casing.

These tests drive the REAL scheduler flag emitter, the REAL catalog
(``FIELD_DEFINITIONS`` + ``get_method_schema``), and the REAL CLI parser
against each other, so an emitter/parser spelling drift fails here instead of
in production.
"""

from __future__ import annotations

import pytest

from acp.catalog import (
    FIELD_DEFINITIONS,
    get_method_schema,
    method_levels_to_cli_flags,
)
from acp.cli import build_parser
from acp.scheduler.jobs import (
    _BATCHOPTIMIZE_SCALAR_FLAGS,
    batchoptimize_method_flags,
)

# Profile ids accepted by both the emitter (_BATCHOPTIMIZE_PROFILES) and the
# BatchOptimize parser's --profile choices.
_BATCH_PROFILES = ["opt_only", "opt_freq", "opt_freq_sp", "opt_freq_sp_thermo"]


def _option_backed_batch_fields() -> dict[str, list[str]]:
    """Map batch-level scalar fields to their catalog option lists.

    A field qualifies when it is emitted by ``batchoptimize_method_flags``
    (key of ``_BATCHOPTIMIZE_SCALAR_FLAGS``) AND ``FIELD_DEFINITIONS`` gives
    it a non-empty, all-string ``options`` list (enumerated keyword).
    """
    fields = get_method_schema("batch_optimize")["method_levels"][0]["fields"]
    covered: dict[str, list[str]] = {}
    for field in fields:
        if field not in _BATCHOPTIMIZE_SCALAR_FLAGS:
            continue
        options = FIELD_DEFINITIONS.get(field, {}).get("options")
        if not options or not all(isinstance(option, str) for option in options):
            continue
        covered[field] = list(options)
    return covered


def test_every_catalog_option_parses_through_emitter_and_cli() -> None:
    """Given catalog-declared options, when emitted then parsed, no case spelling is rejected."""
    covered = _option_backed_batch_fields()
    # Non-vacuous guard: the incident fields must be covered and the set must
    # stay substantial, so catalog reorganization cannot silently empty this test.
    assert {"opt_convergence", "scf_convergence"} <= covered.keys()
    assert len(covered) >= 7

    parser = build_parser()
    for field, options in sorted(covered.items()):
        for option in options:
            flags = batchoptimize_method_flags({field: option})
            argv = ["run", "BatchOptimize", "--items-file", "x.xyz", *flags]
            # Must NOT raise — this is exactly what failed in production when
            # catalog-cased "Tight" was emitted as --opt-convergence Tight.
            ns = parser.parse_args(argv)
            # Parser canonicalizes to its declared spelling; the value must
            # agree with the catalog option case-insensitively. (dest == field
            # name for all role fields.)
            assert str(getattr(ns, field)).lower() == option.lower(), (
                f"{field}={option!r}: emitted {flags} but parsed "
                f"{getattr(ns, field)!r}"
            )


@pytest.mark.parametrize("profile", _BATCH_PROFILES)
def test_batch_profile_flags_parse(profile: str) -> None:
    """Given a BatchOptimize profile id, the emitted --profile flag round-trips."""
    flags = batchoptimize_method_flags({"profile": profile})
    ns = build_parser().parse_args(
        ["run", "BatchOptimize", "--items-file", "x.xyz", *flags]
    )
    assert ns.profile == profile


def test_simple_workflow_emits_title_case_and_parser_folds_it() -> None:
    """Given a catalog-level config, method_levels_to_cli_flags emits Title-case
    and the simple-workflow parser still accepts it (case folding)."""
    flags = method_levels_to_cli_flags({"opt": {"opt_convergence": "VeryTight"}})
    assert flags == ["--opt-convergence", "VeryTight"]
    ns = build_parser().parse_args(["run", "optimize", "--input", "x.xyz", *flags])
    assert ns.opt_convergence.lower() == "verytight"


def test_production_incident_shape_parses() -> None:
    """The exact keyword shape of the failed production job.json parses."""
    flags = batchoptimize_method_flags(
        {
            "profile": "opt_freq_sp_thermo",
            "opt_convergence": "Tight",
            "scf_convergence": "Tight",
        }
    )
    ns = build_parser().parse_args(
        ["run", "BatchOptimize", "--items-file", "x.xyz", *flags]
    )
    assert ns.profile == "opt_freq_sp_thermo"
    assert ns.opt_convergence.lower() == "tight"
    assert ns.scf_convergence.lower() == "tight"


def test_free_form_values_preserve_case_exactly() -> None:
    """Free-form method/basis names are never case-folded by the parser pass."""
    ns = build_parser().parse_args(
        [
            "run",
            "BatchOptimize",
            "--items-file",
            "x.xyz",
            "--method",
            "wB97X-D4",
            "--basis",
            "def2-TZVPPD",
        ]
    )
    # --method/--basis declare dest="optimization_method"/"optimization_basis"
    assert ns.optimization_method == "wB97X-D4"
    assert ns.optimization_basis == "def2-TZVPPD"
