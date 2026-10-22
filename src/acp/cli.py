# ruff: noqa: E501
# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnusedCallResult=false, reportUnknownVariableType=false, reportUnknownLambdaType=false, reportImplicitStringConcatenation=false, reportUnusedFunction=false, reportImplicitOverride=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false, reportUnusedVariable=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportMissingImports=false
"""ACP CLI — Unified command-line interface for Auto-Calc Platform.

Subcommands:
    run Confsearch  Conformer search and energy refinement
    run PESsearch   PES scan and candidate extraction
    run BatchOptimize  Multi-structure calculation plan
    run XtbPathSearch  GFN2-xTB PATH metadynamics (--path-config)
    run OrcaGradient   ORCA single-point gradient EnGrad (--gradient-config)
    run irc         Standalone IRC calculation
    run serve       Start the ACP web dashboard (FastAPI + uvicorn)

Usage:
    acp run Confsearch --input "CCO" --output ./result
    acp run BatchOptimize --items-file structures.xyz --profile opt_freq
    acp run XtbPathSearch --path-config request.json --output ./path_out
    acp run OrcaGradient --gradient-config request.json --output ./grad_out
    acp run irc --input ts.xyz --output ./irc_result
    acp run serve --help
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Final

from acp.core.keywords import make_case_insensitive_type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO", *, structured: bool | None = None) -> None:
    """Configure the root logger with the given level.

    The legacy text format is preserved by default.  Structured (JSON-line,
    redacted) logging is opt-in via ``ACP_LOG_FORMAT=json`` or an explicit
    ``structured=True``; it writes to stderr so machine JSON envelopes on stdout
    stay clean (B08-6 / todo 47).  Logging is observation only — never a
    settlement or recovery authority.
    """
    if structured is None:
        from acp_services.entrypoints.diagnostics import structured_logging_enabled

        structured = structured_logging_enabled()
    if structured:
        from acp_services.entrypoints.diagnostics import configure_structured_logging

        configure_structured_logging(level=level)
        return
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _build_config(args: argparse.Namespace) -> dict[str, Any]:
    """Merge optional --config file and --nproc/--mem overrides.

    Always starts from the full 6-source merge so user pins
    (``executables.orca.path``/``ld_library_path`` from ``~/.cccp.yaml``)
    survive scheduler submissions that omit ``--config``.
    """
    from cccp.config import load_config as legacy_load

    overrides: dict[str, Any] = {}

    if getattr(args, "nproc", None) is not None:
        overrides.setdefault("resources", {})["nproc"] = args.nproc
        overrides.setdefault("executables", {}).setdefault("orca", {})["nproc"] = args.nproc

    if getattr(args, "mem", None) is not None:
        overrides.setdefault("resources", {})["mem"] = args.mem

    try:
        return legacy_load(
            config_path=Path(args.config) if args.config else None,
            overrides=overrides,
        )
    except ValueError as exc:
        parser = getattr(args, "_argument_parser", None)
        if parser is not None:
            parser.error(f"Invalid configuration: {exc}")
        raise


def _parse_levels(levels_value: str | None) -> dict[str, Any] | None:
    """Parse ``--levels`` argument: either a JSON string or a JSON file path."""
    if not levels_value:
        return None

    text = levels_value.strip()

    # If it looks like JSON, parse it directly. This avoids treating a long JSON
    # string as a file path, which can fail with "File name too long" on pathlib.
    if not (text.startswith("{") or text.startswith("[")):
        candidate = Path(text)
        if candidate.exists() and candidate.is_file():
            text = candidate.read_text(encoding="utf-8")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--levels must be valid JSON or a path to a JSON file: {exc}")

    if not isinstance(parsed, dict):
        raise ValueError("--levels must be a JSON object mapping stage names to settings")

    return parsed


def _parse_calc_hess_arg(value: str) -> int | str:
    """argparse ``type=`` callable for ``--calc-hess [N|auto]``.

    Accepts ``auto`` (returns the string ``"auto"``) or an integer in
    ``1..1000``. ``0``, negatives, floats, and any other string raise
    :class:`argparse.ArgumentTypeError` — callers wanting "initial
    Hessian only" must use ``--no-calc-hess`` instead (plan §9.2).
    """
    text = str(value).strip().lower()
    if text == "auto":
        return "auto"
    if text.isdigit():
        n = int(text)
        if n == 0:
            raise argparse.ArgumentTypeError(
                "--calc-hess 0 is not valid; use --no-calc-hess to skip "
                "exact Hessian computation entirely."
            )
        if n > 1000:
            raise argparse.ArgumentTypeError("--calc-hess interval must be <= 1000")
        return n
    raise argparse.ArgumentTypeError(
        "--calc-hess expects 'auto' or an integer 1-1000 "
        f"(got {value!r}); use --no-calc-hess to disable exact Hessian computation."
    )


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def _add_simple_workflow_parsers(run_sub: argparse._SubParsersAction) -> None:
    """Register simple ORCA workflow subcommand parsers + xtb_optimize."""
    for wf, wf_label, wf_desc, wf_epilog in [
        (
            "singlepoint",
            "Single Point",
            "Run ORCA single-point energy calculation",
            "Examples:\n  acp run singlepoint --input mol.xyz --output ./out\n  acp run singlepoint --input mol.inp --method wB97M-V --basis def2-TZVPP",
        ),
        (
            "optimize",
            "Optimization",
            "Run ORCA geometry optimization",
            "Examples:\n  acp run optimize --input mol.xyz --output ./out\n  acp run optimize --input mol.gjf --method r2SCAN-3c --geom-maxiter 200",
        ),
        (
            "frequency",
            "Frequency",
            "Run ORCA vibrational frequency calculation",
            "Examples:\n  acp run frequency --input mol.xyz --output ./out\n  acp run frequency --input mol.inp --method wB97M-V --basis def2-TZVPP",
        ),
        (
            "scan",
            "Relaxed Scan",
            "Run an ORCA relaxed internal-coordinate scan (no ScanTS unless --scants)",
            "Examples:\n"
            "  acp run scan --input mol.xyz --coordinate 0,1,1.0,2.0 --output ./out\n"
            "  acp run scan --input mol.xyz --coordinate 0,1,1.0,2.0 --scants --output ./out",
        ),
    ]:
        p = run_sub.add_parser(
            wf, help=wf_desc, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=wf_epilog
        )
        p.set_defaults(workflow=wf)
        _add_simple_workflow_args(p, wf)

    p = run_sub.add_parser(
        "irc",
        help="Run an independent IRC from a transition-state structure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples (one of --ts-provenance FILE / --ts-provenance-json INLINE\n"
            "is required; file paths go to --ts-provenance, --ts-provenance-json\n"
            "is reserved for scheduler-supplied inline JSON):\n"
            "  acp run irc --input ts.xyz --input-role transition_state \\\n"
            "      --ts-provenance prov.json\n"
            "  acp run irc --input ts.xyz --direction forward --output ./out \\\n"
            "      --ts-provenance prov.json"
        ),
    )
    p.set_defaults(workflow="irc")
    p.add_argument("--input", "-i", required=True, help="Verified transition-state XYZ snapshot")
    proof = p.add_mutually_exclusive_group(required=True)
    proof.add_argument(
        "--ts-provenance", help="Verified upstream TS provenance FILE (irc_ts_source_v1 JSON)"
    )
    proof.add_argument(
        "--ts-provenance-json",
        help="Scheduler-supplied TS provenance as INLINE JSON text (not a file path)",
    )
    p.add_argument("--input-role", choices=["transition_state"], help="Explicit input role")
    p.add_argument(
        "--direction",
        choices=["both", "forward", "reverse"],
        default="both",
        help="IRC direction (default: both)",
    )
    p.add_argument("--output", "-o", default="./irc_output", help="Output directory")
    p.add_argument("--method", default="", help="Inherited TS method (scheduler supplied)")
    p.add_argument("--basis", default="", help="Basis set (default: empty)")
    p.add_argument("--maxpoints", "--max-points", dest="maxpoints", type=int, default=100)
    p.add_argument(
        "--step",
        type=float,
        default=0.1,
        help=(
            "Accepted for compatibility only: never reaches the ORCA %%irc block "
            "(no effect on the run; an unused-kwarg warning is logged)"
        ),
    )
    p.add_argument("--charge", type=int, default=None)
    p.add_argument("--multiplicity", type=int, default=None)
    p.add_argument("--name", type=str, help="Task name")
    p.add_argument("--nproc", type=int, help="Number of CPU cores")
    p.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    p.add_argument("--config", type=str, help="Configuration YAML file")
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    # TS Mode directed optimization (source-bundle driven, plan §8)
    p = run_sub.add_parser(
        "tsmode",
        help="Directed TS optimization along a chosen imaginary mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  acp run tsmode --source-bundle bundle.json --source-mode-index 7 --output ./out\n"
            "\n"
            "bundle.json references validated frequency-source files:\n"
            '  {"files": {"output": "freq.out", "hessian": "freq.hess"},\n'
            '   "charge": 0, "multiplicity": 1,\n'
            '   "level": {"method": "r2SCAN-3c", "basis": ""},\n'
            '   "origin": {"kind": "files"}}'
        ),
    )
    p.set_defaults(workflow="tsmode")
    p.add_argument(
        "--source-bundle",
        required=True,
        help="Frequency-source bundle description (bundle.json)",
    )
    p.add_argument(
        "--source-mode-index",
        type=int,
        required=True,
        help="Native printed mode index of the target imaginary frequency",
    )
    p.add_argument("--output", "-o", default="./tsmode_output", help="Output directory")
    p.add_argument("--name", type=str, help="Task name")
    p.add_argument("--max-steps", type=int, help="Geometry optimization MaxIter")
    p.add_argument("--recalc-hess", type=int, help="Recalc Hessian every N steps (0=off)")
    p.add_argument("--trust-radius", type=float, help="Initial trust radius")
    p.add_argument(
        "--retry-limit",
        type=int,
        default=2,
        help="SCF rescue attempts that preserve the target (default: 2)",
    )
    p.add_argument(
        "--no-final-frequency",
        action="store_true",
        help="Skip the fixed final frequency verification stage",
    )
    p.add_argument(
        "--allow-unverified-mapping",
        action="store_true",
        help=(
            "Proceed although the TS_Mode eigenvalue-rank mapping is not yet "
            "verified against real ORCA samples (plan §6, milestone P0)"
        ),
    )
    p.add_argument("--nproc", type=int, help="Number of CPU cores")
    p.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    p.add_argument("--config", type=str, help="Configuration YAML file")
    p.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    # xTB optimization (separate parser — different solvent models & params)
    p = run_sub.add_parser(
        "xtb_optimize",
        help="Run xTB (GFN-xTB) semi-empirical geometry optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run xtb_optimize --input mol.xyz --output ./out
  acp run xtb_optimize --input mol.xyz --gfn 2 --opt-level tight
  acp run xtb_optimize --input mol.xyz --solvent water --solvent-model gbsa
        """,
    )
    p.set_defaults(workflow="xtb_optimize")
    _add_xtb_optimize_args(p)


def _add_simple_workflow_args(parser: argparse.ArgumentParser, wf: str) -> None:
    """Add common arguments for simple workflows."""
    parser.add_argument(
        "--input", "-i", required=True, help="Input structure file (XYZ, GJF, COM, ORCA .inp)"
    )
    parser.add_argument("--output", "-o", default="./out", help="Output directory")
    parser.add_argument(
        "--charge", type=int, help="Molecular charge (auto-detected if not specified)"
    )
    parser.add_argument(
        "--multiplicity", type=int, help="Spin multiplicity (auto-detected if not specified)"
    )
    parser.add_argument("--name", type=str, help="Molecule name")
    parser.add_argument("--method", default="r2SCAN-3c", help="DFT functional (default: r2SCAN-3c)")
    parser.add_argument("--basis", default="", help="Basis set (default: empty, composite method)")
    parser.add_argument(
        "--dispersion", default="none", help="Dispersion correction (e.g. D3BJ, D4, none)"
    )
    parser.add_argument(
        "--solvent-model",
        default="none",
        type=str.lower,
        choices=["smd", "cpcm", "none"],
        help="Solvent model (default: none)",
    )
    parser.add_argument("--solvent", default="", help="Solvent name (e.g. water, methanol)")
    parser.add_argument("--nproc", type=int, help="Number of CPU cores")
    parser.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    parser.add_argument("--config", type=str, help="Configuration YAML file")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    parser.add_argument(
        "--route-extras",
        type=str,
        help="Comma-separated ORCA route extras (e.g. SlowConv,NoFinalGrid)",
    )
    parser.add_argument(
        "--spin-preset",
        type=str,
        help="Built-in electronic-state preset id (e.g. bs_singlet_flipspin, singlet_triplet_pair)",
    )
    parser.add_argument(
        "--spin-config",
        type=str,
        help="Electronic-state module YAML/JSON file (authoritative for multi-state configurations)",
    )

    if wf in ("singlepoint", "optimize", "frequency", "scan"):
        parser.add_argument("--grid", help="ORCA integration grid (e.g. DEFGRID2)")
        parser.add_argument(
            "--scf-convergence",
            choices=["Loose", "Normal", "Tight", "VeryTight"],
            help="SCF convergence threshold",
        )
        parser.add_argument(
            "--aux-j-basis",
            default="AutoAux",
            metavar="BASIS",
            help="Auxiliary /J basis for RI-J fitting (default: AutoAux). Common: AutoAux, def2/J.",
        )
        parser.add_argument(
            "--aux-c-basis",
            default="AutoAux",
            metavar="BASIS",
            help="Auxiliary /C basis for RI-MP2 correlation (default: AutoAux). "
            "Common: AutoAux, def2-TZVPP/C, cc-pVTZ/C. Only used by "
            "double-hybrid functionals (PWPB95) and DLPNO.",
        )
        parser.add_argument(
            "--ri-approximation",
            default="RIJCOSX",
            choices=["none", "RI", "RIJCOSX", "RIJK"],
            help="RI approximation (default: RIJCOSX)",
        )
        parser.add_argument(
            "--aux-basis", dest="aux_j_basis_legacy", default=None, help=argparse.SUPPRESS
        )

    if wf in ("optimize", "scan"):
        parser.add_argument(
            "--geom-maxiter",
            type=int,
            help="Max geometry iterations (maps to MaxIter in %%geom block)",
        )

    if wf == "optimize":
        parser.add_argument(
            "--opt-convergence",
            default="Tight",
            choices=["Loose", "Normal", "Tight", "VeryTight"],
            help="Optimization convergence (default: Tight)",
        )
        # Hessian policy (plan §9): mutually-exclusive group replaces the
        # legacy --recalc-hess flag. Omit all three to follow config.
        hess_group = parser.add_mutually_exclusive_group()
        hess_group.add_argument(
            "--calc-hess",
            nargs="?",
            const="auto",
            default=None,
            metavar="N|auto",
            type=_parse_calc_hess_arg,
            help=(
                "Recalculate Hessian every N steps (1-1000), or use 'auto' "
                "(infer from elements: light=off / others=10) "
                "when N is omitted. Use --no-calc-hess to skip exact "
                "Hessian computation entirely. NOTE: --calc-hess 0 is rejected."
            ),
        )
        hess_group.add_argument(
            "--no-calc-hess",
            action="store_true",
            default=False,
            help=(
                "Never compute the exact Hessian; ORCA uses an approximate "
                "initial Hessian with BFGS updates throughout."
            ),
        )

    if wf == "scan":
        parser.add_argument(
            "--coordinate",
            action="append",
            required=True,
            metavar="ATOMS,START,END",
            help=(
                "Zero-based scan coordinate: 2 atoms (distance), 3 (angle), "
                "4 (dihedral) as atom1[,atom2[,atom3[,atom4]]],start,end; "
                "repeat for coupled coordinates"
            ),
        )
        parser.add_argument(
            "--scan-points",
            type=int,
            default=21,
            help="Number of scan frames including both endpoints (default: 21)",
        )
        parser.add_argument(
            "--scants",
            action="store_true",
            default=False,
            help=(
                "Use the ORCA ScanTS route (transition-state-oriented scans). "
                "Off by default; unavailable for synchronous multi-coordinate "
                "or explicit-grid scans."
            ),
        )

    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit a machine-readable JSON result envelope on stdout (additive mode).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help=(
            "Plan/validate/estimate only: emit a JSON envelope without launching "
            "any scientific program, transfer or core-hour metering."
        ),
    )


def _add_xtb_optimize_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for the xTB optimization subcommand."""
    parser.add_argument(
        "--input", "-i", required=True, help="Input structure file (XYZ, GJF, COM, ORCA .inp)"
    )
    parser.add_argument("--output", "-o", default="./out", help="Output directory")
    parser.add_argument(
        "--charge", type=int, help="Molecular charge (auto-detected if not specified)"
    )
    parser.add_argument(
        "--multiplicity", type=int, help="Spin multiplicity (auto-detected if not specified)"
    )
    parser.add_argument("--name", type=str, help="Molecule name")
    parser.add_argument(
        "--gfn",
        type=int,
        default=2,
        choices=[0, 1, 2],
        help="GFN-xTB Hamiltonian level (default: 2)",
    )
    parser.add_argument(
        "--opt-level",
        default="normal",
        choices=["crude", "sloppy", "loose", "normal", "tight", "vtight", "extreme"],
        help="xTB optimization convergence level (default: normal)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        help="Maximum number of optimization cycles (xTB xcontrol maxcycle)",
    )
    parser.add_argument(
        "--solvent-model",
        default="none",
        type=str.lower,
        choices=["gbsa", "alpb", "none"],
        help="xTB solvation model (default: none; GBSA or ALPB)",
    )
    parser.add_argument("--solvent", default="", help="Solvent name (e.g. water, methanol)")
    parser.add_argument("--nproc", type=int, help="Number of CPU cores")
    parser.add_argument(
        "--mem",
        type=str,
        help="Memory limit (accepted for compatibility; xTB manages memory via nproc)",
    )
    parser.add_argument("--config", type=str, help="Configuration YAML file")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )


def _add_stage_workflow_parsers(run_sub: argparse._SubParsersAction) -> None:
    """Register the active staged workflow subcommands."""
    conf = run_sub.add_parser(
        "Confsearch",
        help="Unified conformer search + energies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run Confsearch --input "CCO" --protocol xtb-crest --refinement-policy screen --output ./out
  acp run Confsearch --input "CCO" --protocol xtbmd-censo --profile light \\
      --refinement-policy rank1 --output ./out
        """,
    )
    conf.set_defaults(workflow="Confsearch")
    conf_input = conf.add_mutually_exclusive_group(required=True)
    conf_input.add_argument("--input", "-i", type=str, help="SMILES string or input file path")
    conf_input.add_argument(
        "--batch-file", type=str, help="Batch input file (one molecule per line)"
    )
    conf.add_argument("--output", "-o", default="./confsearch_out", help="Output directory")
    conf.add_argument(
        "--protocol",
        default="censo-crest",
        choices=["xtb-crest", "xtb-md", "censo-crest", "xtbmd-censo"],
        help="Sampling/energy protocol (default: censo-crest)",
    )
    conf.add_argument(
        "--profile",
        default="default",
        choices=["light", "default", "high"],
        help="Quality profile inside the protocol (default: default)",
    )
    conf.add_argument(
        "--refinement-policy",
        default="screen",
        choices=["screen", "rank1", "cumulative-99", "all"],
        help=(
            "Fine-refinement scope (default: screen). Pure-xTB protocols "
            "(xtb-crest/xtb-md) run no DFT refinement under ANY policy; screen "
            "selects zero refinement candidates everywhere (selection returns [])."
        ),
    )
    conf.add_argument(
        "--backend",
        default="native",
        choices=["native"],
        help="Execution backend (default: native; rph-parity retired 2026-08)",
    )
    conf.add_argument(
        "--preset",
        choices=["censo-light", "censo-default", "censo-zero"],
        help="CENSO preset override (censo-crest / xtbmd-censo only)",
    )
    conf.add_argument("--levels", type=str, help="Method level overrides as JSON")
    conf.add_argument("--solvent", type=str, help="Solvent (gas phase if omitted)")
    conf.add_argument("--ewin", type=float, help="Energy window in kcal/mol")
    conf.add_argument("--temperature", type=float, help="Temperature in K (Boltzmann/MD)")
    conf.add_argument("--md-temp", type=float, help="MD temperature in K (xtb-md / xtbmd-censo)")
    conf.add_argument("--md-time", type=float, help="MD length in ps")
    conf.add_argument("--md-seeds", type=int, help="MD replica count")
    conf.add_argument("--max-frames", type=int, help="Batch-opt frame cap")
    conf.add_argument("--charge", type=int, help="Molecular charge (default: 0)")
    conf.add_argument("--multiplicity", type=int, help="Spin multiplicity (default: 1)")
    conf.add_argument("--name", type=str, help="Molecule name")
    conf.add_argument("--nproc", type=int, help="Number of processors")
    conf.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    conf.add_argument("--config", type=str, help="Configuration YAML file")
    conf.add_argument("--save-config", type=str, help="Save the merged configuration to YAML")
    conf.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    pes = run_sub.add_parser(
        "PESsearch",
        help="PES scan + candidate structure extraction",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run PESsearch --xyz-text @molecule.xyz --scan-atoms 0,1 \\
      --scan-start 1.0 --scan-end 3.0 --scan-points 21 --output ./pes_out
        """,
    )
    pes.set_defaults(workflow="PESsearch")
    pes.add_argument(
        "--mode",
        choices=["path", "bond_length_scan"],
        default="bond_length_scan",
        help=(
            "Search mode (default: bond_length_scan). 'path' is retained only "
            "for legacy result manifests."
        ),
    )
    pes.add_argument(
        "--from",
        dest="from_manifest",
        help="Direct path to a confsearch_manifest.json (path mode) or a structure/xyz source",
    )
    pes.add_argument("--from-job", help="Source Confsearch job id (resolved via the jobs root)")
    pes.add_argument(
        "--from-artifact",
        default=None,
        help="Artifact path relative to the source job directory (default: RESULT/confsearch/confsearch_manifest.json)",
    )
    pes.add_argument(
        "--strategy",
        default="guided-scan",
        choices=["guided-scan", "reverse-peb", "direct-ts"],
        help="Path-search strategy (default: guided-scan)",
    )
    pes.add_argument(
        "--plan",
        help="Reaction coordinate plan as a JSON string or path to a JSON file",
    )
    pes.add_argument("--product", help="Product XYZ file path")
    pes.add_argument("--product-manifest", help="Product-side confsearch_manifest.json path")
    pes.add_argument("--reactant-conf", help="Reactant conformer id (default: rank 1)")
    pes.add_argument("--product-conf", help="Product conformer id (default: rank 1)")
    pes.add_argument("--ts-guess", help="TS guess XYZ (required by direct-ts)")
    # ---------------------------------------------------------------------------
    # bond_length_scan mode
    # ---------------------------------------------------------------------------
    pes.add_argument(
        "--source-type",
        choices=["task_artifact", "structure_asset", "xyz_text"],
        help="Structure source for bond_length_scan (default: xyz_text)",
    )
    pes.add_argument(
        "--xyz-text",
        help="Pasted XYZ text (or @file path) for source_type=xyz_text",
    )
    pes.add_argument(
        "--asset-path",
        help="Resolved structure-asset file path for source_type=structure_asset",
    )
    pes.add_argument(
        "--from-frame",
        type=int,
        help="frame_index selector into a task_artifact structure list (0-based)",
    )
    pes.add_argument(
        "--scan-config",
        help="Bond-length-scan request as a JSON string or path to a JSON file",
    )
    pes.add_argument(
        "--scan-kind",
        choices=["distance", "angle", "dihedral"],
        help="Scanned coordinate kind (default: distance)",
    )
    pes.add_argument(
        "--selection-kind",
        choices=["bond_stretch", "angle", "dihedral", "double_bond_scan"],
        help=(
            "Generic atom-selection function: bond_stretch, angle, dihedral, or double_bond_scan"
        ),
    )
    pes.add_argument(
        "--scan-bond-type",
        choices=["auto", "single", "double", "multiple", "aromatic"],
        help="Bond type metadata for distance scans (default: auto)",
    )
    pes.add_argument(
        "--scan-atoms",
        help="Comma-separated 0-based atom pair for the scan, e.g. '0,1'",
    )
    pes.add_argument("--scan-start", type=float, help="Scan start distance (Angstrom)")
    pes.add_argument("--scan-end", type=float, help="Scan end distance (Angstrom)")
    pes.add_argument("--scan-points", type=int, help="Scan point count (3–101)")
    pes.add_argument("--scan-method", help="Scan optimisation method (default: GFN2-xTB)")
    pes.add_argument("--scan-basis", help="Scan optimisation basis (3c methods: built-in)")
    pes.add_argument(
        "--scan-dispersion",
        help="Scan optimisation dispersion correction (none/D3/D3BJ/D4; 3c methods: built-in)",
    )
    pes.add_argument(
        "--scan-solvent-model",
        choices=["none", "CPCM", "SMD"],
        help="Scan optimisation solvation model (default: none)",
    )
    pes.add_argument("--scan-solvent", help="Scan optimisation solvent (e.g. water)")
    pes.add_argument(
        "--scan-grid",
        choices=["DefGrid1", "DefGrid2", "DefGrid3"],
        help="Scan optimisation ORCA integration grid (default: ORCA default)",
    )
    pes.add_argument(
        "--scan-scf-convergence",
        choices=["normal", "tight", "verytight"],
        help="Scan optimisation SCF convergence (default: ORCA default)",
    )
    pes.add_argument(
        "--scan-scf-max-iter",
        type=int,
        help="Scan optimisation SCF max iterations (default: 200)",
    )
    pes.add_argument(
        "--scan-ri-approximation",
        choices=["none", "RI", "RIJCOSX", "RIJK"],
        help="Scan optimisation RI approximation (3c methods: locked)",
    )
    pes.add_argument("--sp-method", help="Single-point method (default: B97-3c)")
    pes.add_argument("--sp-basis", help="Single-point basis (composite methods: none)")
    pes.add_argument("--no-sp", action="store_true", help="Disable the single-point refinement")
    pes.add_argument("--max-iterations", type=int, help="Max optimisation iterations per point")
    pes.add_argument(
        "--input", "-i", dest="input_xyz", help="Input XYZ file for direct bond-length scan"
    )
    pes.add_argument(
        "--coordinate",
        action="append",
        dest="coordinates",
        metavar="A1,A2,START,END",
        help=(
            "Scan coordinate as comma-separated atom indices + range "
            "(repeatable, e.g. --coordinate '0,1,1.0,3.0')"
        ),
    )
    pes.add_argument("--points", type=int, help="Number of scan points (3-101)")
    pes.add_argument("--reaction", help="Path to a reaction.json definition (optional)")
    pes.add_argument("--output", "-o", default="./pes_search_out", help="Output directory")
    pes.add_argument("--charge", type=int, help="Molecular charge override")
    pes.add_argument("--multiplicity", type=int, help="Spin multiplicity override")
    pes.add_argument("--nproc", type=int, help="Number of CPU cores (overrides config)")
    pes.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    pes.add_argument("--config", type=str, help="Configuration YAML file")
    pes.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    xtb_path = run_sub.add_parser(
        "XtbPathSearch",
        help="GFN2-xTB PATH metadynamics (frozen pes2ts_xtb_path_request_v1 payload)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run XtbPathSearch --path-config request.json --output ./path_out
  acp run XtbPathSearch --path-config request.json --output ./out --nproc 8 --mem 16
  acp run XtbPathSearch --path-config request.json --output ./out --register

--path-config ships the complete frozen request (source xyz pair, charge/
multiplicity, path_inp_text, gfn_level, uhf, threads, timeout, seed, extra
args) exactly like PESsearch --scan-config; recipe knobs are never defaulted.
--register makes the finished CLI run visible in the Workbench (job list +
/api/v1/jobs/{id}/s2/profile); the output dir is registered as-is.
        """,
    )
    xtb_path.set_defaults(workflow="XtbPathSearch")
    xtb_path.add_argument(
        "--path-config",
        required=True,
        help=(
            "Frozen pes2ts_xtb_path_request_v1 request as a JSON file path "
            "(or an inline JSON string)"
        ),
    )
    xtb_path.add_argument("--output", "-o", default="./xtb_path_out", help="Output directory")
    xtb_path.add_argument("--nproc", type=int, help="Number of CPU cores")
    xtb_path.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    xtb_path.add_argument("--config", type=str, help="Configuration YAML file")
    xtb_path.add_argument(
        "--register",
        action="store_true",
        help=(
            "After a successful run, register the output directory as a "
            "COMPLETED job in the ACP jobs store (acp_jobs.db under "
            "ACP_RUN_ROOT) so the Workbench job list and "
            "/api/v1/jobs/{id}/s2/profile can resolve it. Off by default."
        ),
    )
    xtb_path.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    xtb_constopt = run_sub.add_parser(
        "XtbConstrainedOpt",
        help="xTB single-geometry constrained optimization (frozen pes2ts_xtb_constrained_opt_request_v1 payload)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run XtbConstrainedOpt --opt-config request.json --output ./opt_out
  acp run XtbConstrainedOpt --opt-config request.json --output ./out --nproc 8 --mem 16
  acp run XtbConstrainedOpt --opt-config request.json --output ./out --register

--opt-config ships the complete frozen request (geometry + elements + charge/
multiplicity, distance/angle/dihedral constraints with 0-based atom positions,
force constant, opt level, optional $fix freeze set, optional physical
single point). The optimization runs entirely inside one xTB process; the
evidence product separates xTB's GEOMETRY OPTIMIZATION CONVERGED status and
the restraint-including TOTAL ENERGY from the unrestrained physical energy.
Geometry non-convergence is reported in the evidence, not silently accepted.
        """,
    )
    xtb_constopt.set_defaults(workflow="XtbConstrainedOpt")
    xtb_constopt.add_argument(
        "--opt-config",
        required=True,
        help=(
            "Frozen pes2ts_xtb_constrained_opt_request_v1 request as a JSON file path "
            "(or an inline JSON string)"
        ),
    )
    xtb_constopt.add_argument(
        "--output", "-o", default="./xtb_constopt_out", help="Output directory"
    )
    xtb_constopt.add_argument("--nproc", type=int, help="Number of CPU cores")
    xtb_constopt.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    xtb_constopt.add_argument("--config", type=str, help="Configuration YAML file")
    xtb_constopt.add_argument(
        "--register",
        action="store_true",
        help=(
            "After a successful run, register the output directory as a "
            "COMPLETED job in the ACP jobs store (acp_jobs.db under "
            "ACP_RUN_ROOT) so the Workbench job list can resolve it. Off by default."
        ),
    )
    xtb_constopt.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    orca_gradient = run_sub.add_parser(
        "OrcaGradient",
        help="ORCA single-point gradient EnGrad (frozen pes2ts_orca_gradient_request_v1 payload)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run OrcaGradient --gradient-config request.json --output ./grad_out
  acp run OrcaGradient --gradient-config request.json --output ./out --nproc 8 --mem 16
  acp run OrcaGradient --gradient-config request.json --output ./out --register

--gradient-config ships the complete frozen request (xyz or geometry+elements,
method, basis, charge, multiplicity, optional route extras / timeout / nproc /
extra blocks) exactly like XtbPathSearch --path-config; request knobs are
never defaulted by ACP. Gradient values are the ORCA-printed energy gradient
dE/dX in Hartree/bohr (not forces).
--register makes the finished CLI run visible in the Workbench (job list +
/api/v1/jobs/{id}/s2/profile); the output dir is registered as-is.
        """,
    )
    orca_gradient.set_defaults(workflow="OrcaGradient")
    orca_gradient.add_argument(
        "--gradient-config",
        required=True,
        help=(
            "Frozen pes2ts_orca_gradient_request_v1 request as a JSON file path "
            "(or an inline JSON string)"
        ),
    )
    orca_gradient.add_argument(
        "--output", "-o", default="./orca_gradient_out", help="Output directory"
    )
    orca_gradient.add_argument("--nproc", type=int, help="Number of CPU cores")
    orca_gradient.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    orca_gradient.add_argument("--config", type=str, help="Configuration YAML file")
    orca_gradient.add_argument(
        "--register",
        action="store_true",
        help=(
            "After a successful run, register the output directory as a "
            "COMPLETED job in the ACP jobs store (acp_jobs.db under "
            "ACP_RUN_ROOT) so the Workbench job list and "
            "/api/v1/jobs/{id}/s2/profile can resolve it. Off by default."
        ),
    )
    orca_gradient.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )


def _add_batch_optimize_parser(run_sub: argparse._SubParsersAction) -> None:
    batch = run_sub.add_parser(
        "BatchOptimize",
        help="Batch optimization with optional frequency, single-point, and thermochemistry steps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run BatchOptimize --from-artifact ./pes_job --profile opt_freq
  acp run BatchOptimize --items-file structures.xyz --profile opt_freq_sp --select ts_001
        """,
    )
    batch.set_defaults(workflow="BatchOptimize")
    source = batch.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--from-job",
        help="Source PESsearch job id (reads <job_dir>/RESULT/result_manifest.json)",
    )
    source.add_argument(
        "--from-artifact",
        help="Task directory or result_manifest.json containing structure products",
    )
    source.add_argument(
        "--items-file",
        help="Batch request JSON or multi-frame XYZ file",
    )
    batch.add_argument(
        "--profile",
        default="opt_freq",
        choices=["opt_only", "opt_freq", "opt_freq_sp", "opt_freq_sp_thermo"],
        help="Batch calculation profile (default: opt_freq)",
    )
    batch.add_argument(
        "--layout-mode",
        choices=["batch", "single_flat"],
        default="batch",
        help=(
            "On-disk layout: batch isolates multiple items under batch/<item_id>; "
            "single_flat is for one scheduler task (default: batch)"
        ),
    )
    batch.add_argument("--select", help="Comma-separated item or candidate ids")
    batch.add_argument(
        "--electronic-state-json",
        help="Job-level electronic-state module as JSON (used by the scheduler)",
    )
    batch.add_argument("--spin-preset", help="Built-in electronic-state preset id")
    batch.add_argument("--spin-config", help="Electronic-state module YAML/JSON file")
    batch.add_argument(
        "--method",
        "--optimization-method",
        dest="optimization_method",
        default="",
        help="Shared ORCA method for optimization and frequency",
    )
    batch.add_argument(
        "--basis",
        "--optimization-basis",
        dest="optimization_basis",
        default="",
        help="Shared ORCA basis for optimization and frequency",
    )
    batch.add_argument(
        "--sp-method", dest="single_point_method", default="", help="ORCA method for single-point"
    )
    batch.add_argument(
        "--sp-basis", dest="single_point_basis", default="", help="ORCA basis for single-point"
    )
    batch.add_argument(
        "--temperature",
        type=float,
        default=298.15,
        help="Thermochemistry temperature in K (default: 298.15)",
    )
    batch.add_argument(
        "--pressure",
        type=float,
        default=1.0,
        help="Thermochemistry pressure in atm (default: 1.0)",
    )
    batch.add_argument(
        "--scale-factor",
        type=float,
        default=0.9905,
        help="Frequency scale factor for thermochemistry (default: 0.9905)",
    )
    batch.add_argument(
        "--opt-max-iter",
        type=int,
        default=None,
        help="Max geometry-optimization iterations (None = ORCA default)",
    )
    batch.add_argument(
        "--opt-convergence",
        choices=["loose", "normal", "tight", "verytight"],
        default=None,
        help="Geometry-optimization convergence level",
    )
    batch.add_argument(
        "--opt-trust-radius",
        type=float,
        default=None,
        help="Geometry-optimization trust radius in Bohr",
    )
    batch.add_argument(
        "--opt-initial-hessian",
        choices=["auto", "model", "calculate"],
        default=None,
        help="Initial Hessian construction strategy",
    )
    batch.add_argument(
        "--opt-recalc-hess",
        default=None,
        help="Hessian recalculation: auto / off / integer interval",
    )
    # ── per-role optimization overrides ──────────────────────────────────
    batch.add_argument(
        "--minimum-opt-trust-radius",
        "--int-opt-trust-radius",
        type=float,
        default=None,
        help="INT role trust radius override in Bohr",
    )
    batch.add_argument(
        "--minimum-opt-initial-hessian",
        "--int-opt-initial-hessian",
        choices=["auto", "model", "calculate"],
        default=None,
        help="INT role initial Hessian strategy",
    )
    batch.add_argument(
        "--minimum-opt-recalc-hess",
        "--int-opt-recalc-hess",
        default=None,
        help="INT role Hessian recalculation: auto / off / integer interval",
    )
    batch.add_argument(
        "--transition-state-opt-trust-radius",
        "--ts-opt-trust-radius",
        type=float,
        default=None,
        help="TS role trust radius override in Bohr (default 0.3)",
    )
    batch.add_argument(
        "--transition-state-opt-initial-hessian",
        "--ts-opt-initial-hessian",
        choices=["auto", "model", "calculate"],
        default=None,
        help="TS role initial Hessian strategy (default calculate)",
    )
    batch.add_argument(
        "--transition-state-opt-recalc-hess",
        "--ts-opt-recalc-hess",
        default=None,
        help="TS role Hessian recalculation: auto / off / integer interval (default 5)",
    )
    # ── per-role optimizer / SCF / rescue overrides ──────────────────────
    for _role_flag_prefix, _role_dest_prefix, _role_label in (
        ("--minimum", "minimum_", "INT"),
        ("--transition-state", "transition_state_", "TS"),
    ):
        batch.add_argument(
            f"{_role_flag_prefix}-opt-max-iter",
            type=int,
            default=None,
            help=f"{_role_label} role max optimization iterations override",
        )
        batch.add_argument(
            f"{_role_flag_prefix}-opt-convergence",
            type=str.lower,
            choices=["loose", "normal", "tight", "verytight"],
            default=None,
            help=f"{_role_label} role optimization convergence level",
        )
        batch.add_argument(
            f"{_role_flag_prefix}-scf-max-iter",
            type=int,
            default=None,
            help=f"{_role_label} role max SCF iterations override",
        )
        batch.add_argument(
            f"{_role_flag_prefix}-scf-convergence",
            type=str.lower,
            choices=["loose", "tight", "verytight"],
            default=None,
            help=f"{_role_label} role SCF convergence level",
        )
        batch.add_argument(
            f"{_role_flag_prefix}-scf-strategy",
            type=str.lower,
            choices=["normal", "slowconv", "soscf"],
            default=None,
            help=f"{_role_label} role SCF convergence strategy",
        )
        batch.add_argument(
            f"{_role_flag_prefix}-opt-rescue-policy",
            choices=["off", "adaptive"],
            default=None,
            help=f"{_role_label} role rescue strategy override",
        )
        batch.add_argument(
            f"{_role_flag_prefix}-opt-max-rescue",
            type=int,
            default=None,
            help=f"{_role_label} role maximum rescue attempts override",
        )
    batch.add_argument(
        "--opt-rescue-policy",
        choices=["off", "adaptive"],
        default=None,
        help="Optimization rescue strategy for failed steps",
    )
    batch.add_argument(
        "--opt-max-rescue",
        type=int,
        default=None,
        help="Maximum rescue attempts per optimization (0-10)",
    )
    batch.add_argument(
        "--scf-max-iter",
        type=int,
        default=None,
        help="Maximum SCF iterations",
    )
    batch.add_argument(
        "--scf-convergence",
        type=str.lower,
        choices=["normal", "tight", "verytight"],
        default=None,
        help="SCF convergence level (case-insensitive)",
    )
    batch.add_argument(
        "--scf-strategy",
        type=str.lower,
        choices=["normal", "slowconv", "soscf"],
        default=None,
        help="SCF convergence accelerator strategy (case-insensitive)",
    )
    scf_inherit = batch.add_mutually_exclusive_group()
    scf_inherit.add_argument(
        "--scf-orbital-inherit",
        dest="scf_orbital_inherit",
        action="store_true",
        default=None,
        help="Inherit orbitals from previous calculation (default)",
    )
    scf_inherit.add_argument(
        "--no-scf-orbital-inherit",
        dest="scf_orbital_inherit",
        action="store_false",
        help="Do not inherit orbitals from previous calculation",
    )
    batch.add_argument("--minimum-method", help="ORCA method override for minimum structures")
    batch.add_argument("--minimum-basis", help="ORCA basis override for minimum structures")
    batch.add_argument(
        "--transition-state-method",
        help="ORCA method override for transition-state structures",
    )
    batch.add_argument(
        "--transition-state-basis",
        help="ORCA basis override for transition-state structures",
    )
    batch.add_argument("--output", "-o", default="./batch_optimize_out", help="Output directory")
    batch.add_argument("--charge", type=int, default=0, help="Default molecular charge")
    batch.add_argument("--multiplicity", type=int, default=1, help="Default spin multiplicity")
    batch.add_argument("--nproc", type=int, help="Number of CPU cores (overrides config)")
    batch.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    batch.add_argument("--config", type=str, help="Configuration YAML file")
    batch.add_argument(
        "--batch-roles-json",
        type=str,
        default=None,
        help='New-style per-role config as JSON string (e.g. \'{"int":{...},"ts":{...}}\')',
    )
    batch.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )


def _load_plan_argument(plan_arg: str | None) -> dict[str, Any] | None:
    """Parse --plan as a JSON string or a path to a JSON file."""
    if not plan_arg:
        return None
    text = plan_arg
    # Guard: stat'ing inline JSON longer than NAME_MAX raises OSError(ENAMETOOLONG);
    # only probe the filesystem for non-JSON values, path errors fall through to json.loads.
    if not plan_arg.lstrip().startswith("{"):
        try:
            plan_path = Path(plan_arg)
            if plan_path.is_file():
                text = plan_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            pass
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid --plan JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--plan must be a JSON object")
    return parsed


def _parse_select(select_arg: str | None) -> list[str] | None:
    if not select_arg:
        return None
    return [part.strip() for part in select_arg.split(",") if part.strip()]


def _handle_confsearch(args: argparse.Namespace) -> int:
    """Execute the unified Confsearch workflow."""
    setup_logging(args.log_level)

    levels = _parse_levels_json(args.levels)
    if args.levels and levels is None:
        return 1

    cfg = _build_config(args)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.save_config:
        from cccp.config import save_config as save_cfg

        save_cfg(cfg, Path(args.save_config))
        logger.info("Configuration saved to: %s", args.save_config)

    md_params: dict[str, Any] = {}
    for cli_key, param_key in (
        ("md_temp", "md_temperature"),
        ("md_time", "md_time_ps"),
        ("md_seeds", "md_seeds"),
        ("max_frames", "max_frames"),
    ):
        value = getattr(args, cli_key, None)
        if value is not None:
            md_params[param_key] = value

    from acp.calculations.progress import ProgressReporter
    from acp.confsearch import ConfsearchEngine, ConfsearchRequest
    from acp.confsearch.engine import CONFSEARCH_STAGES

    def _run_one(source: str, name: str | None) -> int:
        reporter = ProgressReporter(
            output_dir,
            job_name="Confsearch",
            stages=list(CONFSEARCH_STAGES),
        )
        request = ConfsearchRequest(
            input_source=source,
            output_dir=output_dir,
            protocol=args.protocol,
            profile=args.profile,
            refinement_policy=args.refinement_policy,
            backend=args.backend,
            name=name,
            charge=args.charge,
            multiplicity=args.multiplicity,
            solvent=args.solvent,
            nproc=args.nproc,
            preset=args.preset,
            levels=levels,
            temperature=args.temperature,
            energy_window=args.ewin,
            md_params=md_params,
            config=cfg,
        )
        try:
            result = ConfsearchEngine().run(request, progress_reporter=reporter)
        except KeyboardInterrupt:
            logger.warning("Interrupted by user")
            reporter.fail("interrupted")
            return 130
        except ValueError as exc:
            logger.error("%s", exc)
            return 1
        if result.status != "completed":
            logger.error("Confsearch failed: %s", result.error)
            return 1
        logger.info("Confsearch completed (%s + %s)", result.protocol, result.refinement_policy)
        logger.info("  Conformers  : %d", len(result.conformers))
        logger.info("  Manifest    : %s", result.manifest_path)
        logger.info("  G1 gate     : %s", result.quality_gates.get("G1"))
        return 0

    if args.batch_file:
        from cccp.io import load_batch_inputs

        inputs = load_batch_inputs(Path(args.batch_file))
        failures = 0
        for index, molecule in enumerate(inputs, start=1):
            source = str(molecule.source_path or molecule.metadata.get("smiles", ""))
            logger.info("[%d/%d] Confsearch %s", index, len(inputs), molecule.name)
            if _run_one(source, molecule.name) != 0:
                failures += 1
        return 1 if failures else 0
    return _run_one(args.input, args.name)


def _handle_pessearch(args: argparse.Namespace) -> int:
    """Execute the PESsearch workflow."""
    setup_logging(args.log_level)
    cfg = _build_config(args)

    from acp.calculations.pes.scan import PES_SCAN_STAGES
    from acp.calculations.progress import ProgressReporter

    pes_stages: list[str] = [str(stage) for stage in PES_SCAN_STAGES]
    reporter = ProgressReporter(Path(args.output), job_name="PESsearch", stages=pes_stages)

    error_msg: str | None = None
    rc = 0
    result: Any | None = None

    try:
        # -- resolve reaction definition (optional, form ④) --
        reaction_path = getattr(args, "reaction", None)
        reaction: dict[str, Any] | None = None
        if reaction_path:
            from acp.compat.legacy.manifests import read_reaction_definition

            reaction = read_reaction_definition(Path(reaction_path))

        # -- determine input form --
        input_xyz_str = getattr(args, "input_xyz", None)
        from_artifact = getattr(args, "from_artifact", None)
        from_job = getattr(args, "from_job", None)
        from_manifest = getattr(args, "from_manifest", None)
        _mode = getattr(args, "mode", "bond_length_scan")

        if _mode == "bond_length_scan" and _has_bond_scan_source(args):
            # Form ⑤: scheduler --scan-config / direct bond-scan source args
            from acp.workflows.pes_search import run_bond_length_scan

            scan_request = _build_bond_scan_request(args)
            strict_errors = _strict_method_config_errors(scan_request, args)
            if strict_errors:
                # T14: plain-CLI new submissions hard-reject (no QC work queued).
                for err_message in strict_errors:
                    print(f"[method-config] ERROR: {err_message}", file=sys.stderr)
                error_msg = "invalid method config: " + "; ".join(strict_errors)
                logger.error("%s", error_msg)
                rc = 2
            else:
                result = run_bond_length_scan(
                    scan_request=scan_request,
                    output_dir=Path(args.output),
                    config=cfg,
                    progress_reporter=reporter,
                )
                _report_method_config_warnings(Path(args.output), result)
        elif input_xyz_str:
            # Form ③: direct XYZ + coordinate
            input_xyz = Path(input_xyz_str).expanduser().resolve()
            if not input_xyz.is_file():
                logger.error("PES_E_MANIFEST: Input file not found: %s", input_xyz)
                error_msg = f"PES_E_MANIFEST: Input file not found: {input_xyz}"
                rc = 2
            else:
                coord_strings = getattr(args, "coordinates", None)
                coordinate = _parse_pes_coordinates(coord_strings, args)

                from acp.workflows.pes_search import run_pes_search

                result = run_pes_search(
                    input_xyz=input_xyz,
                    coordinate=coordinate,
                    strategy=getattr(args, "strategy", None),
                    reaction=reaction,
                    charge=getattr(args, "charge", 0) or 0,
                    multiplicity=getattr(args, "multiplicity", 1) or 1,
                    output_dir=Path(args.output),
                    config=cfg,
                    progress_reporter=reporter,
                )
        elif from_manifest or from_job or from_artifact:
            # Forms ①/②: manifest-based
            manifest_path: Path | None = None
            if from_manifest:
                manifest_path = Path(from_manifest).expanduser().resolve()
            elif from_job:
                manifest_path = _resolve_pes_manifest_from_job(from_job, from_artifact)
            elif from_artifact:
                manifest_path = Path(from_artifact).expanduser().resolve()

            if manifest_path is None or not manifest_path.is_file():
                code = "PES_E_MANIFEST"
                logger.error("%s: Manifest not found: %s", code, manifest_path)
                error_msg = f"{code}: Manifest not found: {manifest_path}"
                rc = 2
            else:
                from acp.workflows.pes_search import run_pes_search

                result = run_pes_search(
                    confsearch_manifest=manifest_path,
                    strategy=getattr(args, "strategy", None),
                    reaction=reaction,
                    charge=getattr(args, "charge", 0) or 0,
                    multiplicity=getattr(args, "multiplicity", 1) or 1,
                    output_dir=Path(args.output),
                    config=cfg,
                    progress_reporter=reporter,
                )
        else:
            logger.error(
                "PESsearch requires --input, --from-artifact, --from-job, "
                "or a bond-scan source (--scan-config / --xyz-text)"
            )
            error_msg = (
                "PESsearch requires --input, --from-artifact, --from-job, "
                "or a bond-scan source (--scan-config / --xyz-text)"
            )
            rc = 2

        if error_msg is None and result is not None:
            if result.status != "completed":
                error_str = result.error or ""
                for code in ("PES_E_MANIFEST", "PES_E_COORD", "PES_E_STRATEGY"):
                    if code in error_str:
                        logger.error("PESsearch failed: %s", error_str)
                        error_msg = error_str
                        rc = 2
                        break
                else:
                    logger.error("PESsearch failed: %s", error_str)
                    error_msg = error_str
                    rc = 1
            else:
                meta = result.metadata
                logger.info(
                    "PESsearch completed: %d TS + %d INT candidates",
                    meta.get("ts_candidates", 0),
                    meta.get("int_candidates", 0),
                )
                logger.info("  Manifest    : %s", meta.get("manifest_path"))
                reporter.complete()

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        error_msg = "interrupted"
        rc = 130
    except Exception as exc:
        error_str = str(exc)
        # Extract error code prefix for exit code 2
        for code in ("PES_E_MANIFEST", "PES_E_COORD", "PES_E_STRATEGY"):
            if code in error_str:
                logger.error("%s", exc)
                error_msg = error_str
                rc = 2
                break
        else:
            logger.exception("PESsearch failed: %s", exc)
            error_msg = error_str
            rc = 1
    finally:
        if error_msg is not None:
            reporter.fail(error_msg)

    return rc


def _resolve_pes_manifest_from_job(job_id: str, from_artifact: str | None) -> Path:
    """Resolve a confsearch manifest path from a job ID."""
    from acp.core.paths import resolve_run_root
    from acp.scheduler.store import JobStore

    store = JobStore(resolve_run_root() / "acp_jobs.db")
    record = store.get(job_id)
    if record is None:
        raise FileNotFoundError(f"Job not found: {job_id}")
    work_dir = Path(record.work_dir)
    relative = from_artifact or "RESULT/confsearch/confsearch_manifest.json"
    artifact = work_dir / relative
    if not artifact.is_file():
        raise FileNotFoundError(f"Source artifact not found: {artifact}")
    return artifact.resolve()


def _resolve_batch_artifact_from_job(job_id: str, from_artifact: str | None) -> Path:
    """Resolve a BatchOptimize source path (task dir or manifest) from a job ID."""
    from acp.core.paths import resolve_run_root
    from acp.scheduler.store import JobStore

    store = JobStore(resolve_run_root() / "acp_jobs.db")
    record = store.get(job_id)
    if record is None:
        raise FileNotFoundError(f"Job not found: {job_id}")
    work_dir = Path(record.work_dir)
    if not work_dir.is_dir():
        raise FileNotFoundError(f"Job work dir not found: {work_dir}")
    relative = from_artifact or "RESULT/result_manifest.json"
    artifact = work_dir / relative
    if not artifact.is_file():
        raise FileNotFoundError(f"Result artifact not found: {artifact}")
    return artifact.resolve()


def _parse_pes_coordinates(
    coord_strings: list[str] | None,
    args: argparse.Namespace,
) -> Any:
    """Parse --coordinate arguments into a ScanCoordinate."""
    from acp.calculations.pes.contracts import ScanCoordinate

    n_points = getattr(args, "points", None) or 21

    if coord_strings:
        parts = [float(x.strip()) for x in coord_strings[0].split(",") if x.strip()]
        if len(parts) != 4:
            from acp.workflows.pes_search import PES_E_COORD

            raise ValueError(
                f"[{PES_E_COORD}] --coordinate requires exactly 4 values: "
                "a1,a2,start,end (e.g. '0,1,1.0,3.0')"
            )
        a1, a2, start, end = int(parts[0]), int(parts[1]), parts[2], parts[3]
        return ScanCoordinate(
            kind="distance",
            atoms=(a1, a2),
            start=start,
            end=end,
            n_points=n_points,
        )

    # Fallback to legacy scan args
    atoms_str = getattr(args, "scan_atoms", None)
    if atoms_str:
        atom_parts = [int(x.strip()) for x in str(atoms_str).split(",") if x.strip()]
        start = getattr(args, "scan_start", None) or 1.0
        end = getattr(args, "scan_end", None) or 3.0
        points = getattr(args, "scan_points", None) or n_points
        kind = getattr(args, "scan_kind", None) or "distance"
        return ScanCoordinate(
            kind=kind,
            atoms=tuple(atom_parts),
            start=start,
            end=end,
            n_points=points,
        )

    # Default coordinate
    return None


def _has_bond_scan_source(args: argparse.Namespace) -> bool:
    """Return whether an explicit bond-length-scan source is present.

    Covers the scheduler contract (``--scan-config`` ships the full request
    including the source) and the direct CLI source forms (``--source-type``
    / ``--xyz-text`` / ``--asset-path``). Plain ``--input`` stays on the
    engine path.
    """
    return any(
        [
            getattr(args, "scan_config", None),
            getattr(args, "source_type", None),
            getattr(args, "xyz_text", None),
            getattr(args, "asset_path", None),
        ]
    )


def _build_bond_scan_request(args: argparse.Namespace) -> dict[str, Any]:
    """Assemble the bond-length-scan request dict from CLI args."""
    scan_config = _load_plan_argument(getattr(args, "scan_config", None)) or {}

    explicit_source = any(
        [
            getattr(args, "source_type", None),
            getattr(args, "xyz_text", None),
            getattr(args, "asset_path", None),
            getattr(args, "from_manifest", None),
            getattr(args, "from_job", None),
        ]
    )
    config_source = scan_config.get("source")

    if not explicit_source and isinstance(config_source, dict) and config_source.get("source_type"):
        # Scheduler path: the full scan_request (source included) ships in scan_config.
        source = dict(config_source)
    else:
        source_type = getattr(args, "source_type", None) or (
            "task_artifact"
            if (getattr(args, "from_manifest", None) or getattr(args, "from_job", None))
            else "xyz_text"
        )

        from_frame = getattr(args, "from_frame", None)
        if source_type == "task_artifact":
            source = {
                "source_type": "task_artifact",
                "source_job_id": getattr(args, "from_job", None),
                "artifact_path": getattr(args, "from_manifest", None),
                "structure_selector": {
                    "kind": "frame_index" if from_frame is not None else "final_structure",
                    "frame_index": from_frame,
                },
            }
        elif source_type == "structure_asset":
            source = {
                "source_type": "structure_asset",
                "asset_path": getattr(args, "asset_path", None),
                "asset_id": getattr(args, "from_job", None),
            }
        else:
            xyz_text = getattr(args, "xyz_text", None)
            if xyz_text and xyz_text.startswith("@"):
                xyz_path = Path(xyz_text[1:])
                if not xyz_path.is_file():
                    raise ValueError(f"--xyz-text file not found: {xyz_path}")
                xyz_text = xyz_path.read_text(encoding="utf-8")
            source = {"source_type": "xyz_text", "xyz_text": xyz_text}
    if getattr(args, "charge", None) is not None:
        source["charge"] = args.charge
    if getattr(args, "multiplicity", None) is not None:
        source["multiplicity"] = args.multiplicity

    coordinate = dict(scan_config.get("coordinate") or {})
    coordinates = scan_config.get("coordinates")
    selection = dict(scan_config.get("selection") or {})
    protocol = dict(scan_config.get("protocol") or {})

    if getattr(args, "scan_kind", None):
        coordinate["kind"] = args.scan_kind
    if getattr(args, "scan_bond_type", None):
        coordinate["bond_type"] = args.scan_bond_type
    if getattr(args, "selection_kind", None):
        selection["kind"] = args.selection_kind

    atoms = getattr(args, "scan_atoms", None)
    if atoms:
        parts = [int(part) for part in str(atoms).split(",") if part.strip()]
        selection_kind = str(selection.get("kind") or "")
        expected_atoms = {
            "bond_stretch": 2,
            "distance": 2,
            "angle": 3,
            "dihedral": 4,
            "double_bond_scan": 4,
        }.get(selection_kind or str(coordinate.get("kind") or "distance"), 2)
        if len(parts) != expected_atoms:
            raise ValueError(
                f"--scan-atoms must contain {expected_atoms} indices for "
                f"{selection_kind or coordinate.get('kind') or 'distance'} scan"
            )
        if selection_kind == "double_bond_scan":
            coordinate["kind"] = "distance"
            coordinate["atoms"] = parts[:2]
            coordinates = [
                dict(coordinate, atoms=parts[:2]),
                dict(coordinate, atoms=parts[2:4]),
            ]
        else:
            coordinate["atoms"] = parts
    if getattr(args, "scan_start", None) is not None:
        coordinate["start"] = args.scan_start
    if getattr(args, "scan_end", None) is not None:
        coordinate["end"] = args.scan_end
    if getattr(args, "scan_points", None) is not None:
        coordinate["n_points"] = args.scan_points
    if getattr(args, "scan_method", None):
        protocol.setdefault("scan_optimizer", {})["method"] = args.scan_method
    # DFT scan-level flags (2026-09): setdefault semantics — a scheduler
    # --scan-config payload always wins over CLI flags.
    _scan_level_flags = {
        "scan_basis": "basis",
        "scan_dispersion": "dispersion",
        "scan_solvent_model": "solvent_model",
        "scan_solvent": "solvent",
        "scan_grid": "grid",
        "scan_scf_convergence": "scf_convergence",
        "scan_scf_max_iter": "scf_max_iterations",
        "scan_ri_approximation": "ri_approximation",
    }
    for arg_name, protocol_key in _scan_level_flags.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            protocol.setdefault("scan_optimizer", {}).setdefault(protocol_key, value)
    if getattr(args, "sp_method", None):
        protocol.setdefault("single_point", {})["method"] = args.sp_method
    if getattr(args, "sp_basis", None):
        protocol.setdefault("single_point", {})["basis"] = args.sp_basis
    if getattr(args, "no_sp", False):
        protocol.setdefault("single_point", {})["enabled"] = False
    if getattr(args, "max_iterations", None) is not None:
        protocol.setdefault("scan_driver", {})["max_iterations"] = args.max_iterations
        protocol.setdefault("scan_optimizer", {})["max_iterations"] = args.max_iterations

    result: dict[str, Any] = {
        "mode": "bond_length_scan",
        "source": source,
        "coordinate": coordinate,
        "protocol": protocol,
    }
    if isinstance(coordinates, list):
        result["coordinates"] = coordinates
    if selection:
        result["selection"] = selection
    return result


def _strict_method_config_errors(
    scan_request: dict[str, Any], args: argparse.Namespace
) -> list[str]:
    """Strict new-submission gate for direct CLI bond-scan sources (T14).

    A ``--scan-config`` document is the scheduler/recompute contract — its
    levels resolve through the migration lane inside :func:`run_pes_scan`
    (warn + canonicalize, never hard-reject).  Direct source flags
    (``--xyz-text`` / ``--asset-path`` / ``--source-type``) are a plain-CLI
    NEW submission, so user-EXPLICIT conflicts reject here with actionable
    errors before any QC work starts.
    """
    if getattr(args, "scan_config", None):
        return []
    from acp.calculations.levels import LevelEntryContext
    from acp.calculations.pes.scan import resolve_scan_protocol_levels

    _protocol, _warnings, errors = resolve_scan_protocol_levels(
        scan_request.get("protocol"), context=LevelEntryContext.STRICT
    )
    return errors


def _append_method_config_events(output_dir: Path, warnings: list[str]) -> None:
    """Append one ``method_validation_warning`` event on scheduler task dirs."""
    from acp.storage.layout import runtime_file
    from acp.workflows._helpers import is_scheduler_task_dir

    if not is_scheduler_task_dir(output_dir):
        return
    try:
        from acp.scheduler.events import JobEventLog

        JobEventLog(runtime_file(output_dir, "events.jsonl")).append(
            "method_validation_warning",
            source="pes_bond_scan_runner",
            warnings=list(warnings),
        )
    except OSError:
        logger.debug("method-config warning event append failed", exc_info=True)


def _report_method_config_warnings(output_dir: Path, result: Any) -> None:
    """Surface runner-lane migration warnings on stderr + the event stream.

    The CLI logging handler writes to stdout, so the warnings are printed
    to stderr EXPLICITLY (T14: the CLI warning channel must be stderr).
    """
    metadata = getattr(result, "metadata", None) or {}
    warnings = [str(message) for message in (metadata.get("level_warnings") or [])]
    if not warnings:
        return
    for message in warnings:
        print(f"[method-config] WARNING: {message}", file=sys.stderr)
    _append_method_config_events(output_dir, warnings)


def _handle_batch_optimize(args: argparse.Namespace) -> int:
    from acp.calculations.batch.engine import batch_stage_names
    from acp.calculations.batch.options import BatchMethodOptions
    from acp.calculations.progress import ProgressReporter
    from acp.chem.composition import normalize_recalc_hess
    from acp.workflows.batch_optimize import BatchOptimizeInputError, run_batch_optimize

    setup_logging(args.log_level)
    output_dir = Path(args.output)
    reporter = ProgressReporter(
        output_dir,
        job_name="BatchOptimize",
        stages=batch_stage_names(args.profile),
    )
    from_job = getattr(args, "from_job", None)
    if from_job:
        source = _resolve_batch_artifact_from_job(from_job, args.from_artifact)
    else:
        source = args.items_file or args.from_artifact

    electronic_state = None
    if args.electronic_state_json:
        try:
            electronic_state = json.loads(args.electronic_state_json)
        except json.JSONDecodeError as exc:
            logger.error("Invalid --electronic-state-json: %s", exc)
            return 2
        if not isinstance(electronic_state, dict):
            logger.error("--electronic-state-json must be a JSON object")
            return 2
    else:
        electronic_state = _resolve_spin_flags(args.spin_preset, args.spin_config)

    batch_roles_json = getattr(args, "batch_roles_json", None)
    if batch_roles_json is not None:
        try:
            parsed_roles = json.loads(batch_roles_json)
        except json.JSONDecodeError as exc:
            logger.error("Invalid --batch-roles-json: %s", exc)
            return 2
        method_kwargs: dict[str, Any] = {"batch_roles": parsed_roles}
    else:
        method_kwargs = {
            "optimization_method": args.optimization_method,
            "optimization_basis": args.optimization_basis,
            "single_point_method": args.single_point_method,
            "single_point_basis": args.single_point_basis,
            "temperature": args.temperature,
            "pressure": args.pressure,
            "scale_factor": args.scale_factor,
            "minimum_method": args.minimum_method or "",
            "minimum_basis": args.minimum_basis or "",
            "transition_state_method": args.transition_state_method or "",
            "transition_state_basis": args.transition_state_basis or "",
        }
        if args.opt_max_iter is not None:
            method_kwargs["opt_max_iter"] = args.opt_max_iter
        if args.opt_convergence is not None:
            method_kwargs["opt_convergence"] = args.opt_convergence
        if args.opt_trust_radius is not None:
            method_kwargs["opt_trust_radius"] = args.opt_trust_radius
        if args.opt_initial_hessian is not None:
            method_kwargs["opt_initial_hessian"] = args.opt_initial_hessian
        if args.opt_recalc_hess is not None:
            method_kwargs["opt_recalc_hess"] = normalize_recalc_hess(args.opt_recalc_hess)
        if getattr(args, "minimum_opt_trust_radius", None) is not None:
            method_kwargs["minimum_opt_trust_radius"] = args.minimum_opt_trust_radius
        if getattr(args, "minimum_opt_initial_hessian", None) is not None:
            method_kwargs["minimum_opt_initial_hessian"] = args.minimum_opt_initial_hessian
        if getattr(args, "minimum_opt_recalc_hess", None) is not None:
            method_kwargs["minimum_opt_recalc_hess"] = normalize_recalc_hess(
                args.minimum_opt_recalc_hess
            )
        if getattr(args, "transition_state_opt_trust_radius", None) is not None:
            method_kwargs["transition_state_opt_trust_radius"] = (
                args.transition_state_opt_trust_radius
            )
        if getattr(args, "transition_state_opt_initial_hessian", None) is not None:
            method_kwargs["transition_state_opt_initial_hessian"] = (
                args.transition_state_opt_initial_hessian
            )
        if getattr(args, "transition_state_opt_recalc_hess", None) is not None:
            method_kwargs["transition_state_opt_recalc_hess"] = normalize_recalc_hess(
                args.transition_state_opt_recalc_hess
            )
        for _prefix in ("minimum_", "transition_state_"):
            for _name in (
                "opt_max_iter",
                "opt_convergence",
                "scf_max_iter",
                "scf_convergence",
                "scf_strategy",
                "opt_rescue_policy",
                "opt_max_rescue",
            ):
                _value = getattr(args, f"{_prefix}{_name}", None)
                if _value is not None:
                    method_kwargs[f"{_prefix}{_name}"] = _value
        if args.opt_rescue_policy is not None:
            method_kwargs["opt_rescue_policy"] = args.opt_rescue_policy
        if args.opt_max_rescue is not None:
            method_kwargs["opt_max_rescue"] = args.opt_max_rescue
        if args.scf_max_iter is not None:
            method_kwargs["scf_max_iter"] = args.scf_max_iter
        if args.scf_convergence is not None:
            method_kwargs["scf_convergence"] = args.scf_convergence
        if args.scf_strategy is not None:
            method_kwargs["scf_strategy"] = args.scf_strategy
        if args.scf_orbital_inherit is not None:
            method_kwargs["scf_orbital_inherit"] = args.scf_orbital_inherit

    try:
        result = run_batch_optimize(
            source,
            profile=args.profile,
            output_dir=output_dir,
            config=_build_config(args),
            charge=args.charge,
            multiplicity=args.multiplicity,
            select=_parse_select(args.select),
            methods=BatchMethodOptions.from_method_dict(method_kwargs),
            electronic_state=electronic_state,
            layout_mode=args.layout_mode,
            progress_reporter=reporter,
        )
    except BatchOptimizeInputError as exc:
        logger.error("BatchOptimize input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except ValueError as exc:
        logger.error("BatchOptimize input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except KeyboardInterrupt:
        logger.warning("BatchOptimize interrupted by user")
        reporter.fail("interrupted")
        return 130

    if result.status != "completed":
        logger.error("BatchOptimize failed: %s", result.error)
        reporter.fail(result.error or "BatchOptimize failed")
        return 1
    logger.info("BatchOptimize completed: profile=%s", args.profile)
    logger.info("  Manifest: %s", result.metadata.get("manifest_path"))
    reporter.complete()
    return 0


def _handle_xtb_path_search(args: argparse.Namespace) -> int:
    """Execute the GFN2-xTB PATH metadynamics workflow from ``--path-config``.

    Thin dispatch layer: load the frozen ``pes2ts_xtb_path_request_v1``
    payload, call :func:`acp.workflows.xtb_path.run_xtb_path_search`, and
    translate typed workflow errors into exit codes (input → 2, execution → 1).
    """
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.xtb_path import (
        XTB_PATH_STAGES,
        XtbPathInputError,
        XtbPathSearchError,
        run_xtb_path_search,
    )

    setup_logging(args.log_level)
    try:
        path_request = _load_plan_argument(args.path_config)
    except ValueError as exc:
        logger.error("XtbPathSearch --path-config error: %s", exc)
        return 2
    if not isinstance(path_request, dict):
        logger.error("XtbPathSearch --path-config must be a JSON object request payload")
        return 2

    cfg = _build_config(args)
    output_dir = Path(args.output)
    reporter = ProgressReporter(output_dir, job_name="XtbPathSearch", stages=list(XTB_PATH_STAGES))
    try:
        result = run_xtb_path_search(
            path_request,
            output_dir=output_dir,
            config=cfg,
            progress_reporter=reporter,
        )
    except XtbPathInputError as exc:
        logger.error("XtbPathSearch input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except XtbPathSearchError as exc:
        logger.error("XtbPathSearch failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        logger.warning("XtbPathSearch interrupted by user")
        reporter.fail("interrupted")
        return 130
    if result.status != "completed":
        logger.error("XtbPathSearch failed: %s", result.error)
        reporter.fail(result.error or "XtbPathSearch failed")
        return 1
    logger.info("XtbPathSearch completed")
    logger.info("  Frames      : %s", result.metadata.get("frames_count", "N/A"))
    logger.info("  Manifest    : %s", result.metadata.get("result_manifest_path", "N/A"))
    logger.info("  PES profile : %s", result.metadata.get("pes_profile_path", "N/A"))
    reporter.complete()
    if getattr(args, "register", False):
        return _register_xtb_path_cli_job(args, output_dir, path_request, result)
    return 0


def _handle_xtb_constrained_opt(args: argparse.Namespace) -> int:
    """Execute the xTB single-geometry constrained optimization workflow.

    Thin dispatch layer: load the frozen ``pes2ts_xtb_constrained_opt_request_v1``
    payload, call :func:`acp.workflows.xtb_constrained_opt.run_xtb_constrained_opt`,
    and translate typed workflow errors into exit codes (input → 2, execution → 1).
    A normally-terminated xTB run whose geometry optimization did not converge
    is still a completed workflow (the evidence records converged_geometry=false).
    """
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.xtb_constrained_opt import (
        XTC_STAGES,
        XtbConstrainedOptError,
        XtbConstrainedOptInputError,
        run_xtb_constrained_opt,
    )

    setup_logging(args.log_level)
    try:
        opt_request = _load_plan_argument(args.opt_config)
    except ValueError as exc:
        logger.error("XtbConstrainedOpt --opt-config error: %s", exc)
        return 2
    if not isinstance(opt_request, dict):
        logger.error("XtbConstrainedOpt --opt-config must be a JSON object request payload")
        return 2

    cfg = _build_config(args)
    output_dir = Path(args.output)
    reporter = ProgressReporter(output_dir, job_name="XtbConstrainedOpt", stages=list(XTC_STAGES))
    try:
        result = run_xtb_constrained_opt(
            opt_request,
            output_dir=output_dir,
            config=cfg,
            progress_reporter=reporter,
        )
    except XtbConstrainedOptInputError as exc:
        logger.error("XtbConstrainedOpt input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except XtbConstrainedOptError as exc:
        logger.error("XtbConstrainedOpt failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        logger.warning("XtbConstrainedOpt interrupted by user")
        reporter.fail("interrupted")
        return 130
    if result.status != "completed":
        logger.error("XtbConstrainedOpt failed: %s", result.error)
        reporter.fail(result.error or "XtbConstrainedOpt failed")
        return 1
    logger.info("XtbConstrainedOpt completed")
    logger.info("  Converged   : %s", result.metadata.get("converged_geometry", "N/A"))
    logger.info("  E_effective : %s", result.metadata.get("energy_effective_hartree", "N/A"))
    logger.info("  E_physical  : %s", result.metadata.get("energy_physical_hartree", "N/A"))
    logger.info("  Manifest    : %s", result.metadata.get("result_manifest_path", "N/A"))
    logger.info("  Evidence    : %s", result.metadata.get("evidence_path", "N/A"))
    reporter.complete()
    if getattr(args, "register", False):
        return _register_xtb_path_cli_job(args, output_dir, opt_request, result)
    return 0


def job_runtime_commands(manager: Any | None = None) -> Any:
    """Return the Runtime command surface for CLI job submit/control entries.

    CLI job submit/control entries must go through this single boundary and
    never call the legacy ``JobManager``/store directly (single execution owner,
    todo 25). The composition root injects adapters over the live manager; with
    no manager (the ``--register`` path, which must not start the scheduler) the
    completed-job registration port stays wired while control/submit/edit are
    unattached placeholders.
    """
    from acp.services_bootstrap import build_runtime_commands

    return build_runtime_commands(manager)


def job_control_command(
    command: str,
    job_id: str,
    *,
    manager: Any | None = None,
    project_id: str | None = None,
    target_node: str | None = None,
) -> Any:
    """Route one local job-control verb through the shared Runtime use case.

    The CLI and the Web API must not each grow their own task state machine:
    both enter the *same* :class:`~acp_services.runtime.commands.RuntimeCommands`
    surface (todo 25), which forwards exactly once to the injected scheduler
    port. ``command`` is one of ``pause`` / ``unpause`` / ``cancel`` /
    ``continue`` / ``rerun``; the port's outcome is returned untouched.

    This is the shared control entry prepared for the public CLI subcommands.
    Wiring those subparsers to it is todo 74 (the full-CLI entry owner) and is
    deliberately **not** done in this equivalence-only slice, so no argument
    surface changes here.
    """
    commands = job_runtime_commands(manager)
    verb = command.strip().lower()
    if verb == "pause":
        return commands.pause(job_id)
    if verb == "unpause":
        return commands.unpause(job_id)
    if verb == "cancel":
        return commands.cancel(job_id)
    if verb == "continue":
        return commands.continue_job(job_id, target_node=target_node)
    if verb == "rerun":
        return commands.rerun(job_id, project_id=project_id)
    raise ValueError(
        f"unknown job control command {command!r}; expected one of: "
        "pause, unpause, cancel, continue, rerun"
    )


# ---------------------------------------------------------------------------
# Public local task management (B07-8 / todo 74)
# ---------------------------------------------------------------------------
# Reuse the Services use cases (RuntimeCommands / RuntimeRecovery) and the
# persistent run_root store so a fresh process finds and controls the original
# task; never a browser/Web path or a second task implementation.

_TASK_STEP_KINDS: Final[dict[str, str]] = {
    "singlepoint": "singlepoint",
    "optimize": "optimize",
    "frequency": "frequency",
    "scan": "scan",
    "casscf": "casscf",
}

_TASK_CONTROL_VERBS: Final[tuple[str, ...]] = (
    "pause",
    "unpause",
    "continue",
    "cancel",
    "rerun",
)


def _add_task_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``acp task`` subcommands (persistent controller/worker entry)."""
    task_parser = subparsers.add_parser(
        "task",
        help="Local task management against the persistent run_root store",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp task ingest --xyz-text "3\\nwater\\nO 0 0 0\\nH 0 0 0.96\\nH 0 0.93 -0.24" --task-dir ./t
  acp task execute --workflow fake --input "CCO" --name demo
  acp task query --job 20261019_001_demo
  acp task pause --job 20261019_001_demo
  acp task export --job 20261019_001_demo
        """,
    )
    task_sub = task_parser.add_subparsers(dest="task_command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-root", default=None, help="Data run root (default: ACP_RUN_ROOT)")
    common.add_argument(
        "--controller",
        default=None,
        metavar="URL",
        help=(
            "Target an already-running ACP Controller (e.g. http://127.0.0.1:18765) "
            "instead of the local store/manager; never starts a Controller"
        ),
    )
    common.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    ingest = task_sub.add_parser("ingest", parents=[common], help="Parse an input structure")
    ingest.add_argument("--input", "-i", default=None, help="Structure file (XYZ/GJF/COM/... )")
    ingest.add_argument("--smiles", default=None, help="SMILES string")
    ingest.add_argument("--xyz-text", default=None, help="Inline XYZ text")
    ingest.add_argument("--task-dir", required=True, help="Task directory to write input.xyz into")
    ingest.add_argument("--name", default=None, help="Molecule name")

    plan = task_sub.add_parser("plan", parents=[common], help="Build a calculation plan")
    plan.add_argument("--task-dir", required=True, help="Task directory holding input.xyz")
    plan.add_argument(
        "--workflow", "-w", default="singlepoint", help="Workflow (default: singlepoint)"
    )
    plan.add_argument("--method", default="r2SCAN-3c", help="Method (default: r2SCAN-3c)")
    plan.add_argument("--profile", default="default", help="Plan profile (default: default)")
    plan.add_argument(
        "--config", default=None, help="Explicit config file (merged over user/project)"
    )

    execute = task_sub.add_parser(
        "execute", parents=[common], help="Submit a job to the local scheduler"
    )
    execute.add_argument(
        "--workflow", "-w", required=True, help="Workflow id (e.g. fake, singlepoint)"
    )
    execute.add_argument("--input", "-i", required=True, help="Input source (SMILES or file path)")
    execute.add_argument("--name", default=None, help="Task/molecule name")
    execute.add_argument("--method", default="", help="Method override")
    execute.add_argument(
        "--follow",
        action="store_true",
        help="Wait for the job to reach a terminal state (default: return once the worker starts)",
    )

    query = task_sub.add_parser("query", parents=[common], help="Query persisted job records")
    query.add_argument("--job", default=None, help="Single job id")
    query.add_argument("--status", default=None, help="Filter by status")
    query.add_argument("--limit", type=int, default=50, help="Max rows (default: 50)")

    for verb in _TASK_CONTROL_VERBS:
        control = task_sub.add_parser(verb, parents=[common], help=f"{verb} a persisted job")
        control.add_argument("--job", required=True, help="Job id")
        control.add_argument("--project", default=None, help="Project id (rerun only)")
        control.add_argument(
            "--target-node", default=None, help="Execution node override (continue)"
        )

    recover = task_sub.add_parser(
        "recover", parents=[common], help="Reuse valid results for a task"
    )
    recover.add_argument("--task-dir", required=True, help="Task directory holding input.xyz")
    recover.add_argument(
        "--workflow",
        "-w",
        default=None,
        help="Workflow (default: persisted job workflow, else singlepoint)",
    )
    recover.add_argument(
        "--method",
        default=None,
        help="Method override (default: persisted job method, else r2SCAN-3c)",
    )
    recover.add_argument("--profile", default="default", help="Plan profile (default: default)")
    recover.add_argument(
        "--config",
        default=None,
        help="Explicit config file (merged over user/project config)",
    )

    export = task_sub.add_parser("export", parents=[common], help="Read the task result manifest")
    export.add_argument("--job", default=None, help="Job id (resolves the task dir)")
    export.add_argument("--task-dir", default=None, help="Task directory (alternative to --job)")


def job_submit_command(spec: Any, *, manager: Any | None = None) -> Any:
    """Submit a job through the shared Runtime use case (single execution owner)."""
    commands = job_runtime_commands(manager)
    return commands.submit(spec)


def _task_run_root(args: argparse.Namespace) -> Path:
    from acp.core.paths import resolve_run_root

    return resolve_run_root(getattr(args, "run_root", None))


def _task_manager(run_root: Path) -> Any:
    from acp.scheduler.manager import JobManager

    run_root.mkdir(parents=True, exist_ok=True)
    return JobManager(run_root=run_root, reconnect_local_workers=True)


def _task_store(run_root: Path) -> Any:
    from acp_services.store.store import JobStore

    return JobStore(run_root / "acp_jobs.db")


def _live_controller_pid(run_root: Path) -> int | None:
    """Return the PID of a live Controller owning *run_root*, else ``None``.

    Reuses the existing ``.manager.lock`` ownership record and the existing
    liveness/identity check (``pid_is_alive`` + ACP/uvicorn cmdline) — there is
    no second lock.  A stale lock never blocks a local command.
    """
    lock_path = run_root / ".manager.lock"
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    owner = payload.get("pid") if isinstance(payload, dict) else None
    if not isinstance(owner, int) or owner <= 0:
        return None
    from acp.scheduler.processctl import pid_is_alive, read_cmdline

    if not pid_is_alive(owner):
        return None
    cmdline = read_cmdline(owner).lower()
    if "acp" not in cmdline and "uvicorn" not in cmdline:
        return None
    return owner


def _refuse_local_ownership(args: argparse.Namespace, run_root: Path) -> int | None:
    """Refuse a local manager when a live Controller already owns the run root."""
    owner = _live_controller_pid(run_root)
    if owner is None:
        return None
    _emit_task(
        {
            "command": args.task_command,
            "status": "failed",
            "error": "controller_running_use_--controller",
            "reason": "controller_running_use_--controller",
            "controller_pid": owner,
        }
    )
    return 1


def _task_controller_client(controller_url: str) -> Any:
    from acp_services.entrypoints.controller_client import ControllerClient

    return ControllerClient(controller_url)


def _emit_task(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str))


def _task_structure_elements(xyz_text: str) -> list[str]:
    elements: list[str] = []
    for line in xyz_text.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 4:
            elements.append(parts[0])
    return elements


def _task_config(args: argparse.Namespace) -> dict[str, Any]:
    """Load the full merged configuration for a ``task`` subcommand."""
    from cccp.config import load_config

    overrides: dict[str, Any] = {}
    if getattr(args, "nproc", None) is not None:
        overrides.setdefault("resources", {})["nproc"] = args.nproc
        overrides.setdefault("executables", {}).setdefault("orca", {})["nproc"] = args.nproc
    if getattr(args, "mem", None) is not None:
        overrides.setdefault("resources", {})["mem"] = args.mem
    config_path = getattr(args, "config", None)
    return load_config(
        config_path=Path(config_path) if config_path else None,
        overrides=overrides or None,
    )


def _persisted_task_spec(task_dir: Path) -> dict[str, Any]:
    """Read the original workflow/method/resources from a persisted task."""
    spec: dict[str, Any] = {}
    for name in ("job.json", "task.json"):
        path = task_dir / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        raw_spec = payload.get("spec")
        if isinstance(raw_spec, dict):
            workflow = raw_spec.get("workflow")
            if isinstance(workflow, str) and workflow:
                spec.setdefault("workflow", workflow)
            method = raw_spec.get("method")
            if isinstance(method, dict):
                spec.setdefault("method", dict(method))
            resources = raw_spec.get("resources")
            if isinstance(resources, dict):
                spec.setdefault("resources", dict(resources))
            config_path = raw_spec.get("config_path")
            if config_path:
                spec.setdefault("config_path", str(config_path))
        workflow = payload.get("workflow")
        if isinstance(workflow, str) and workflow:
            spec.setdefault("workflow", workflow)
    return spec


def _build_task_plan(
    task_dir: Path,
    workflow: str,
    method: str,
    profile: str,
    *,
    config: Mapping[str, Any] | None = None,
    resources: Mapping[str, Any] | None = None,
    method_flags: Mapping[str, Any] | None = None,
) -> Any:
    from acp.calculations.contracts import (
        CalculationPlan,
        CalculationRequest,
        CalculationStep,
        StepKind,
        StructureArtifact,
        StructureRole,
    )
    from acp.calculations.plans import build_simple_plan

    kind_value = _TASK_STEP_KINDS.get(workflow)
    if kind_value is None:
        raise ValueError(
            f"workflow {workflow!r} has no simple plan; supported: {sorted(_TASK_STEP_KINDS)}"
        )
    input_path = task_dir / "input.xyz"
    if not input_path.is_file():
        raise FileNotFoundError(f"missing {input_path}; run `acp task ingest` first")
    xyz_text = input_path.read_text(encoding="utf-8")
    artifact = StructureArtifact(
        path=input_path,
        elements=_task_structure_elements(xyz_text),
        role=StructureRole.MINIMUM,
        source="cli-task",
    )
    output_dir = task_dir / "WORK" / f"05_{kind_value.upper()}"
    if config is None:
        from cccp.config import load_config

        config = load_config()
    step_resources: dict[str, Any] = dict(resources or {})
    step_resources["output_dir"] = str(output_dir)
    step_resources["config"] = dict(config)
    step_spec = dict(step_resources)
    step_spec.update(method_flags or {})
    step_spec["method"] = method
    request = CalculationRequest(
        input_artifact=artifact,
        method=method,
        resources=step_resources,
        workflow=workflow,
        profile=profile,
    )
    base_plan = build_simple_plan(StepKind(kind_value), request)
    first_step = next(step for step in base_plan.steps if isinstance(step, CalculationStep))
    return CalculationPlan(
        workflow=base_plan.workflow,
        profile=base_plan.profile,
        items=list(base_plan.items),
        steps=[CalculationStep(kind=first_step.kind, mode=first_step.mode, spec=step_spec)],
    )


def _task_input_payload(source: str) -> dict[str, Any]:
    candidate = Path(source)
    if candidate.is_file():
        return {"source": str(candidate), "source_type": "file"}
    return {"source": source, "source_type": "smiles"}


def _wait_for_task(
    manager: Any,
    job_id: str,
    *,
    terminal: bool,
    timeout: float = 120.0,
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = manager.store.get(job_id)
        if record is None:
            return None
        if record.status.is_terminal:
            return record
        if not terminal:
            proc = manager.runner._processes.get(job_id)
            if proc is not None and proc.poll() is None:
                return record
        time.sleep(0.1)
    return manager.store.get(job_id)


def _handle_task_ingest(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    task_dir = Path(args.task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    from acp.intake import detect_and_parse

    if args.input:
        source_path = Path(args.input)
        text = source_path.read_text(encoding="utf-8")
        filename = source_path.name
    elif args.xyz_text:
        text = args.xyz_text
        filename = "input.xyz"
    elif args.smiles:
        text = args.smiles
        filename = "input.xyz"
    else:
        _emit_task(
            {
                "command": "ingest",
                "status": "failed",
                "error": "one of --input/--smiles/--xyz-text required",
            }
        )
        return 2
    fmt, parsed = detect_and_parse(text, filename=filename)
    if not parsed.ok or not parsed.structures:
        _emit_task({"command": "ingest", "status": "failed", "errors": parsed.errors})
        return 2
    asset = parsed.structures[0]
    xyz_out = text if fmt == "xyz" else (asset.xyz or text)
    (task_dir / "input.xyz").write_text(xyz_out, encoding="utf-8")
    _emit_task(
        {
            "command": "ingest",
            "status": "ok",
            "format": fmt,
            "atom_count": asset.atom_count,
            "formula": asset.formula,
            "task_dir": str(task_dir),
        }
    )
    return 0


def _handle_task_plan(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    try:
        plan = _build_task_plan(
            Path(args.task_dir),
            args.workflow,
            args.method,
            args.profile,
            config=_task_config(args),
        )
    except (FileNotFoundError, ValueError) as exc:
        _emit_task({"command": "plan", "status": "failed", "error": str(exc)})
        return 2
    _emit_task(
        {
            "command": "plan",
            "status": "ok",
            "workflow": plan.workflow,
            "steps": [step.kind.value for step in plan.steps],
            "task_dir": str(Path(args.task_dir)),
        }
    )
    return 0


def _handle_task_execute(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    run_root = _task_run_root(args)
    refusal = _refuse_local_ownership(args, run_root)
    if refusal is not None:
        return refusal
    from acp.scheduler.jobs import JobSpec

    method: dict[str, Any] = {}
    if args.method:
        method["method"] = args.method
    spec = JobSpec(
        workflow=args.workflow,
        name=args.name or "",
        input=_task_input_payload(args.input),
        method=method,
    )
    manager = _task_manager(run_root)
    try:
        record = job_submit_command(spec, manager=manager)
        follow = bool(args.follow) or args.workflow == "fake"
        fresh = _wait_for_task(manager, record.id, terminal=follow)
        if fresh is None:
            _emit_task({"command": "execute", "status": "failed", "error": "job vanished"})
            return 1
        _emit_task(
            {
                "command": "execute",
                "status": fresh.status.value,
                "job_id": fresh.id,
                "work_dir": fresh.work_dir,
            }
        )
        return 0 if not fresh.status.is_terminal or fresh.status.value == "completed" else 1
    except (ValueError, RuntimeError) as exc:
        _emit_task({"command": "execute", "status": "failed", "error": str(exc)})
        return 1
    finally:
        manager.shutdown()


def _handle_task_query(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    run_root = _task_run_root(args)
    store = _task_store(run_root)
    if args.job:
        record = store.get(args.job)
        if record is None:
            _emit_task({"command": "query", "status": "not_found", "job_id": args.job})
            return 1
        _emit_task({"command": "query", "status": "ok", "job": record.to_dict()})
        return 0
    records = store.list(status=args.status, limit=args.limit)
    _emit_task(
        {
            "command": "query",
            "status": "ok",
            "count": len(records),
            "jobs": [
                {
                    "id": row.id,
                    "status": row.status.value,
                    "workflow": row.spec.workflow,
                    "work_dir": row.work_dir,
                    "attempt": row.attempt,
                }
                for row in records
            ],
        }
    )
    return 0


def _handle_task_control(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    run_root = _task_run_root(args)
    refusal = _refuse_local_ownership(args, run_root)
    if refusal is not None:
        return refusal
    manager = _task_manager(run_root)
    try:
        record = job_control_command(
            args.task_command,
            args.job,
            manager=manager,
            project_id=getattr(args, "project", None),
            target_node=getattr(args, "target_node", None),
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        _emit_task({"command": args.task_command, "status": "failed", "error": str(exc)})
        return 1
    finally:
        manager.shutdown()
    if record is None:
        _emit_task({"command": args.task_command, "status": "not_found", "job_id": args.job})
        return 1
    _emit_task(
        {
            "command": args.task_command,
            "status": record.status.value,
            "job_id": record.id,
            "attempt": record.attempt,
        }
    )
    return 0


def _handle_task_recover(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    run_root = _task_run_root(args)
    run_root.mkdir(parents=True, exist_ok=True)
    task_dir = Path(args.task_dir)
    persisted = _persisted_task_spec(task_dir)
    workflow = args.workflow or str(persisted.get("workflow") or "singlepoint")
    persisted_method = persisted.get("method") or {}
    method_flags = dict(persisted_method) if isinstance(persisted_method, Mapping) else {}
    method = args.method or str(method_flags.get("method") or "r2SCAN-3c")
    method_flags["method"] = method
    resources = persisted.get("resources") or {}
    try:
        config_path = (
            Path(args.config)
            if getattr(args, "config", None)
            else (Path(persisted["config_path"]) if persisted.get("config_path") else None)
        )
        from cccp.config import load_config

        config = load_config(config_path=config_path)
        plan = _build_task_plan(
            task_dir,
            workflow,
            method,
            args.profile,
            config=config,
            resources=resources if isinstance(resources, Mapping) else None,
            method_flags=method_flags,
        )
    except (FileNotFoundError, ValueError) as exc:
        _emit_task({"command": "recover", "status": "failed", "error": str(exc)})
        return 2
    from acp.services_bootstrap import build_runtime_recovery

    recovery = build_runtime_recovery(run_root)
    result = recovery.execute(plan, task_root=task_dir)
    completed = bool(getattr(result, "is_completed", False))
    _emit_task(
        {
            "command": "recover",
            "status": getattr(result, "status", "unknown"),
            "completed": completed,
            "workflow": workflow,
            "method": method,
            "task_dir": str(task_dir),
        }
    )
    return 0 if completed else 1


def _handle_task_export(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    run_root = _task_run_root(args)
    task_dir: Path | None = Path(args.task_dir) if args.task_dir else None
    if task_dir is None and args.job:
        record = _task_store(run_root).get(args.job)
        if record is None:
            _emit_task({"command": "export", "status": "not_found", "job_id": args.job})
            return 1
        task_dir = Path(record.work_dir)
    if task_dir is None:
        _emit_task(
            {"command": "export", "status": "failed", "error": "--job or --task-dir required"}
        )
        return 2
    result_dir = task_dir / "RESULT"
    if not result_dir.is_dir():
        _emit_task({"command": "export", "status": "no_result", "task_dir": str(task_dir)})
        return 1
    from acp.storage.manifest import MANIFEST_FILENAME, ResultManifest

    if not (result_dir / MANIFEST_FILENAME).is_file():
        _emit_task({"command": "export", "status": "no_result", "task_dir": str(task_dir)})
        return 1
    manifest = ResultManifest.read(result_dir)
    _emit_task(
        {
            "command": "export",
            "status": manifest.status,
            "task_dir": str(task_dir),
            "products": len(manifest.products or []),
        }
    )
    return 0


def _controller_error(args: argparse.Namespace, exc: Any) -> int:
    _emit_task(
        {
            "command": args.task_command,
            "status": "failed",
            "error": str(exc),
            "code": getattr(exc, "code", "unknown_error"),
            "retryable": bool(getattr(exc, "retryable", False)),
        }
    )
    return 1


def _handle_task_query_controller(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    from acp_services.entrypoints.controller_client import ControllerClientError

    client = _task_controller_client(args.controller)
    try:
        if args.job:
            snapshot = client.status(args.job)
            _emit_task({"command": "query", "status": "ok", "job": dict(snapshot.raw)})
            return 0
        payload = client.list_tasks(status=args.status, limit=args.limit)
    except ControllerClientError as exc:
        return _controller_error(args, exc)
    jobs = payload.get("jobs") if isinstance(payload, Mapping) else None
    rows = jobs if isinstance(jobs, list) else []
    _emit_task(
        {
            "command": "query",
            "status": "ok",
            "count": len(rows),
            "jobs": [
                {
                    "id": row.get("id") or row.get("task_id"),
                    "status": row.get("status") or row.get("state"),
                    "workflow": row.get("workflow"),
                    "work_dir": row.get("work_dir"),
                    "attempt": row.get("attempt"),
                }
                for row in rows
                if isinstance(row, Mapping)
            ],
        }
    )
    return 0


def _handle_task_execute_controller(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    from acp_services.entrypoints.controller_client import ControllerClientError

    client = _task_controller_client(args.controller)
    method: dict[str, Any] = {}
    if args.method:
        method["method"] = args.method
    request = {
        "workflow": args.workflow,
        "name": args.name or "",
        "input": _task_input_payload(args.input),
        "method": method,
    }
    try:
        record = client.submit(request)
        job_id = str(record.get("job_id") or record.get("task_id") or "")
        if not job_id:
            _emit_task(
                {
                    "command": "execute",
                    "status": "failed",
                    "error": "Controller returned no task id",
                }
            )
            return 1
        state = str(record.get("status") or record.get("state") or "queued")
        if args.follow:
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                snapshot = client.status(job_id)
                state = snapshot.state
                if state in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.2)
    except ControllerClientError as exc:
        return _controller_error(args, exc)
    _emit_task(
        {
            "command": "execute",
            "status": state,
            "job_id": job_id,
            "work_dir": record.get("work_dir"),
        }
    )
    return 0 if state in {"queued", "pending", "starting", "running", "completed"} else 1


def _handle_task_control_controller(args: argparse.Namespace) -> int:
    setup_logging(args.log_level)
    from acp_services.entrypoints.controller_client import ControllerClientError

    client = _task_controller_client(args.controller)
    task_ref = {"task_id": args.job, "attempt": 1}
    try:
        if args.task_command == "cancel":
            record = client.cancel(task_ref)
        else:
            record = client.control(
                args.task_command,
                task_ref,
                project_id=getattr(args, "project", None),
                target_node=getattr(args, "target_node", None),
            )
    except ControllerClientError as exc:
        return _controller_error(args, exc)
    _emit_task(
        {
            "command": args.task_command,
            "status": str(record.get("status") or record.get("state") or "unknown"),
            "job_id": str(record.get("id") or record.get("task_id") or args.job),
            "attempt": record.get("attempt"),
        }
    )
    return 0


_TASK_CONTROLLER_HANDLERS: Final[dict[str, Callable[[argparse.Namespace], int]]] = {
    "query": _handle_task_query_controller,
    "execute": _handle_task_execute_controller,
    "pause": _handle_task_control_controller,
    "unpause": _handle_task_control_controller,
    "continue": _handle_task_control_controller,
    "cancel": _handle_task_control_controller,
    "rerun": _handle_task_control_controller,
}


_TASK_HANDLERS: Final[dict[str, Callable[[argparse.Namespace], int]]] = {
    "ingest": _handle_task_ingest,
    "plan": _handle_task_plan,
    "execute": _handle_task_execute,
    "query": _handle_task_query,
    "pause": _handle_task_control,
    "unpause": _handle_task_control,
    "continue": _handle_task_control,
    "cancel": _handle_task_control,
    "rerun": _handle_task_control,
    "recover": _handle_task_recover,
    "export": _handle_task_export,
}


def _dispatch_task(args: argparse.Namespace) -> int:
    if getattr(args, "controller", None):
        controller_handler = _TASK_CONTROLLER_HANDLERS.get(args.task_command)
        if controller_handler is None:
            _emit_task(
                {
                    "command": args.task_command,
                    "status": "failed",
                    "error": "unsupported_controller_command",
                    "reason": "unsupported_controller_command",
                }
            )
            return 2
        return controller_handler(args)
    handler = _TASK_HANDLERS.get(args.task_command)
    if handler is None:
        return 1
    return handler(args)


def _register_xtb_path_cli_job(
    args: argparse.Namespace,
    output_dir: Path,
    path_request: dict[str, Any],
    result: Any,
) -> int:
    """Persist a finished ``XtbPathSearch`` CLI run as a completed job (X1′-D).

    Opt-in via ``--register``: binds ``--output`` (as-is) as the job's
    ``work_dir`` through the store-layer registration API so the Workbench
    job list and ``GET /api/v1/jobs/{id}/s2/profile`` resolve.  Skipped
    with a warning when the output dir is already a scheduler task dir —
    those runs are registered by ``JobManager.submit`` and re-registering
    would duplicate the row.

    Returns:
        ``0`` when registered (or intentionally skipped), ``1`` when
        ``--register`` was requested but registration failed.
    """
    from acp.scheduler.registration import CliJobRegistrationError
    from acp.workflows._helpers import is_scheduler_task_dir

    if is_scheduler_task_dir(output_dir):
        logger.warning(
            "XtbPathSearch --register skipped: %s is a scheduler task dir "
            "(already registered by the job manager)",
            output_dir,
        )
        return 0
    resources: dict[str, Any] = {}
    if getattr(args, "nproc", None) is not None:
        resources["nproc"] = args.nproc
    if getattr(args, "mem", None) is not None:
        resources["mem"] = args.mem
    metadata = dict(getattr(result, "metadata", None) or {})
    try:
        record = job_runtime_commands().register_completed_job(
            workflow="XtbPathSearch",
            work_dir=output_dir,
            input_payload={"path_request": path_request},
            resources=resources,
            config_path=getattr(args, "config", None),
            result={
                "registration": "cli",
                "frames_count": metadata.get("frames_count"),
                "pes_profile_path": metadata.get("pes_profile_path"),
                "result_manifest_path": metadata.get("result_manifest_path"),
            },
            required_files=(
                "RESULT/result_manifest.json",
                "RESULT/pes_search/pes_profile.json",
            ),
        )
    except CliJobRegistrationError as exc:
        logger.error("XtbPathSearch --register failed: %s", exc)
        return 1
    logger.info("  Registered job: %s (status=%s)", record.id, record.status.value)
    return 0


def _handle_orca_gradient(args: argparse.Namespace) -> int:
    """Execute the ORCA single-point gradient workflow from ``--gradient-config``.

    Thin dispatch layer: load the frozen ``pes2ts_orca_gradient_request_v1``
    payload, call :func:`acp.workflows.orca_gradient.run_orca_gradient`, and
    translate typed workflow errors into exit codes (input → 2, execution → 1).
    """
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.orca_gradient import (
        ORCA_GRADIENT_STAGES,
        OrcaGradientError,
        OrcaGradientInputError,
        run_orca_gradient,
    )

    setup_logging(args.log_level)
    try:
        gradient_request = _load_plan_argument(args.gradient_config)
    except ValueError as exc:
        logger.error("OrcaGradient --gradient-config error: %s", exc)
        return 2
    if not isinstance(gradient_request, dict):
        logger.error("OrcaGradient --gradient-config must be a JSON object request payload")
        return 2

    cfg = _build_config(args)
    output_dir = Path(args.output)
    reporter = ProgressReporter(
        output_dir, job_name="OrcaGradient", stages=list(ORCA_GRADIENT_STAGES)
    )
    try:
        result = run_orca_gradient(
            request=gradient_request,
            output_dir=output_dir,
            config=cfg,
            progress_reporter=reporter,
        )
    except OrcaGradientInputError as exc:
        logger.error("OrcaGradient input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except OrcaGradientError as exc:
        logger.error("OrcaGradient failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    except KeyboardInterrupt:
        logger.warning("OrcaGradient interrupted by user")
        reporter.fail("interrupted")
        return 130
    if result.status != "completed":
        logger.error("OrcaGradient failed: %s", result.error)
        reporter.fail(result.error or "OrcaGradient failed")
        return 1
    logger.info("OrcaGradient completed")
    logger.info("  Energy      : %s Eh", result.metadata.get("energy_hartree", "N/A"))
    logger.info(
        "  Gradient    : %s (%s)",
        result.metadata.get("gradient_source", "N/A"),
        result.metadata.get("gradient_unit", "N/A"),
    )
    logger.info("  Manifest    : %s", result.metadata.get("result_manifest_path", "N/A"))
    reporter.complete()
    if getattr(args, "register", False):
        return _register_orca_gradient_cli_job(args, output_dir, gradient_request, result)
    return 0


def _register_orca_gradient_cli_job(
    args: argparse.Namespace,
    output_dir: Path,
    gradient_request: dict[str, Any],
    result: Any,
) -> int:
    """Persist a finished ``OrcaGradient`` CLI run as a completed job (X4′-A).

    Opt-in via ``--register``: binds ``--output`` (as-is) as the job's
    ``work_dir`` through the store-layer registration API. Skipped with a
    warning when the output dir is already a scheduler task dir.

    Returns:
        ``0`` when registered (or intentionally skipped), ``1`` when
        ``--register`` was requested but registration failed.
    """
    from acp.scheduler.registration import CliJobRegistrationError
    from acp.workflows._helpers import is_scheduler_task_dir

    if is_scheduler_task_dir(output_dir):
        logger.warning(
            "OrcaGradient --register skipped: %s is a scheduler task dir "
            "(already registered by the job manager)",
            output_dir,
        )
        return 0
    resources: dict[str, Any] = {}
    if getattr(args, "nproc", None) is not None:
        resources["nproc"] = args.nproc
    if getattr(args, "mem", None) is not None:
        resources["mem"] = args.mem
    metadata = dict(getattr(result, "metadata", None) or {})
    try:
        record = job_runtime_commands().register_completed_job(
            workflow="OrcaGradient",
            work_dir=output_dir,
            input_payload={"gradient_request": gradient_request},
            resources=resources,
            config_path=getattr(args, "config", None),
            result={
                "registration": "cli",
                "energy_hartree": metadata.get("energy_hartree"),
                "gradient_source": metadata.get("gradient_source"),
                "result_manifest_path": metadata.get("result_manifest_path"),
            },
            required_files=(
                "RESULT/result_manifest.json",
                "RESULT/gradient/gradient.json",
            ),
        )
    except CliJobRegistrationError as exc:
        logger.error("OrcaGradient --register failed: %s", exc)
        return 1
    logger.info("  Registered job: %s (status=%s)", record.id, record.status.value)
    return 0


def _normalize_string_choices(parser: argparse.ArgumentParser) -> None:
    """Make every string-valued ``choices=`` argument case-insensitive.

    Recursively walks the parser's actions (descending into subparsers);
    for each action whose choices are all strings and which declares no
    ``type``, installs ``make_case_insensitive_type`` so any case spelling
    is accepted and normalised to the canonical spelling. Actions that
    already declare a ``type`` (e.g. int choices) are left untouched.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                _normalize_string_choices(subparser)
            continue
        choices = action.choices
        if not choices or action.type is not None:
            continue
        if not all(isinstance(choice, str) for choice in choices):
            continue
        canonical = list(choices)
        action.choices = canonical
        action.type = make_case_insensitive_type(canonical)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ACP argument parser."""
    parser = argparse.ArgumentParser(
        prog="acp",
        description="Auto-Calc Platform (ACP) — Computational chemistry workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # -- run -----------------------------------------------------------------
    run_parser = subparsers.add_parser("run", help="Run a computational workflow")
    run_sub = run_parser.add_subparsers(dest="workflow", required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Deployment self-check for remote compute nodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp doctor                Check every enabled node in the cluster config
  acp doctor --node compute-01
        """,
    )
    doctor_parser.add_argument(
        "--node",
        default=None,
        help="Check a single node by name (default: all enabled nodes)",
    )
    doctor_parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO)",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Interactive setup wizard for the user config (software, remote nodes, web access)",
    )
    init_parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / ".cccp.yaml",
        help="target user config file to create/update (default ~/.cccp.yaml)",
    )
    init_parser.add_argument(
        "--answers",
        type=Path,
        default=None,
        help="non-interactive answer file (versioned schema); runs without prompts",
    )
    init_parser.add_argument(
        "--plan",
        action="store_true",
        help="render the initialization plan only; do not apply it",
    )
    init_parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO)",
    )

    # -- run ensemble -------------------------------------------------------
    ens = run_sub.add_parser(
        "ensemble",
        help="Ensemble generation (CREST → CENSO prescreening+screening)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run ensemble --input "CCO" --output ./result
  acp run ensemble --input molecule.xyz --preset censo-default
  acp run ensemble --batch-file molecules.txt --output ./batch_results
        """,
    )
    ens_input = ens.add_mutually_exclusive_group(required=False)
    ens_input.add_argument(
        "--input",
        type=str,
        help="SMILES string or input file path (XYZ)",
    )
    ens_input.add_argument(
        "--batch-file",
        type=str,
        help="File containing multiple inputs (one per line)",
    )
    ens.add_argument(
        "--output",
        type=str,
        default="./ensemble_output",
        help="Output directory (default: ./ensemble_output)",
    )
    ens.add_argument(
        "--config",
        type=str,
        help="Configuration YAML file",
    )
    ens.add_argument(
        "--preset",
        type=str,
        default="censo-light",
        choices=["censo-light", "censo-default", "censo-zero"],
        help="CENSO preset (default: censo-light)",
    )
    ens.add_argument(
        "--solvent",
        type=str,
        help="Solvent name (e.g. dcm, water); omit for gas phase",
    )
    ens.add_argument(
        "--keep-all",
        action="store_true",
        help="Do not truncate the ensemble at CENSO part thresholds",
    )
    ens.add_argument(
        "--ewin",
        type=float,
        help="CREST energy window in kcal/mol (default: censo.ewin config, 6.0)",
    )
    ens.add_argument(
        "--nproc",
        type=int,
        help="Number of CPU cores (overrides config)",
    )
    ens.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    ens.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    ens.add_argument(
        "--log-file",
        type=str,
        help="Log file path",
    )
    ens.add_argument(
        "--name",
        type=str,
        help="Molecule name (auto-generated if not specified)",
    )
    ens.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    ens.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )
    ens.add_argument(
        "--save-config",
        type=str,
        help="Save effective configuration to this file",
    )

    # -- run energy ----------------------------------------------------------
    energy = run_sub.add_parser(
        "energy",
        help="Conformer energy (ensemble + rank1 refinement, CENSO-light semantics)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run energy --input "CCO" --output ./result
  acp run energy --input "CCO" --preset censo-zero
  acp run energy --input "CCO" --no-opt
  acp run energy --input "CCO" --full-ensemble
  acp run energy --input prev/ensemble/ensemble.xyz
  acp run energy --input "CCO" --preset censo-default
        """,
    )
    energy_input = energy.add_mutually_exclusive_group(required=False)
    energy_input.add_argument(
        "--input",
        type=str,
        help="SMILES string or input file path (XYZ; multi-frame XYZ skips CREST)",
    )
    energy_input.add_argument(
        "--batch-file",
        type=str,
        help="File containing multiple inputs (one per line)",
    )
    energy.add_argument(
        "--output",
        type=str,
        default="./energy_output",
        help="Output directory (default: ./energy_output)",
    )
    energy.add_argument(
        "--config",
        type=str,
        help="Configuration YAML file",
    )
    energy.add_argument(
        "--preset",
        type=str,
        default="censo-light",
        choices=["censo-light", "censo-default", "censo-zero"],
        help="CENSO preset (default: censo-light)",
    )
    energy.add_argument(
        "--no-opt",
        action="store_true",
        help="Disable high-accuracy rank1 geometry optimization (cheap RSH//xTB path)",
    )
    energy.add_argument(
        "--rank1-only",
        action="store_true",
        help=(
            "Only refine the CENSO/xTB rank1 conformer and compute the ensemble "
            "total free energy G_total = G1 + RT*ln(p1) from the screening "
            "Boltzmann table (default: enabled; use --full-ensemble to disable)"
        ),
    )
    energy.add_argument(
        "--full-ensemble",
        action="store_true",
        help=(
            "Compute the full cumulative-Boltzmann ensemble (>=99%% population, "
            "v15 semantics) instead of the default rank1-only fine DFT"
        ),
    )
    energy.add_argument(
        "--threshold",
        type=float,
        default=0.99,
        help="Cumulative Boltzmann population threshold (0<value<=1, default: 0.99)",
    )
    energy.add_argument(
        "--levels",
        type=str,
        help=(
            "JSON method-level overrides, e.g. "
            '\'{"refinement_sp":{"functional":"DLPNO-CCSD(T)","basis":"def2-TZVPP"},'
            '"thermo":{"scale_factor":0.98},"refinement_threshold":0.99}\''
        ),
    )
    energy.add_argument(
        "--solvent",
        type=str,
        help="Solvent name (e.g. dcm, water); omit for gas phase",
    )
    energy.add_argument(
        "--ewin",
        type=float,
        help="CREST energy window in kcal/mol (default: censo.ewin config, 6.0)",
    )
    energy.add_argument(
        "--nproc",
        type=int,
        help="Number of CPU cores (overrides config)",
    )
    energy.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    energy.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    energy.add_argument(
        "--log-file",
        type=str,
        help="Log file path",
    )
    energy.add_argument(
        "--name",
        type=str,
        help="Molecule name (auto-generated if not specified)",
    )
    energy.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    energy.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )
    energy.add_argument(
        "--save-config",
        type=str,
        help="Save effective configuration to this file",
    )

    # -- run nmr --------------------------------------------------------
    # NMR + DP4/DP5 stereochemistry-assignment workflow (DevDoc
    # ACP_NMR_DP4_DevDoc.md): CREST+CENSO conformers → mPW1PW91/6-311G(d)
    # GIAO NMR → Boltzmann averaging + DP4/DP5 probability.
    # Multi-candidate input (--input may repeat): one DP4 comparison set.
    nmr = run_sub.add_parser(
        "nmr",
        help="NMR + DP4/DP5 stereochemistry assignment (GIAO + Bayesian probability)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run nmr --input "CCO" --spectrum experiment.txt --output ./nmr_out
  acp run nmr --input a.xyz --input b.xyz --spectrum exp.txt
  acp run nmr --input "CCO" --spectrum exp.txt --solvent chloroform
  acp run nmr --input "CCO" --spectrum exp.txt --nmr-method mPW1PW91 --nmr-basis "6-311G(d)"
  acp run nmr --input "CCO" --bruker ./nmr_data --bruker-ref H=7.26

Spectrum file format (DevDoc §6.2):
  C: 167.33(C1), 59.58(C2)
  H: 4.81(H4), 7.18(H5), 3.09(H6)
  EQ: C10,C12
  OMIT: H19
        """,
    )
    nmr.add_argument(
        "--input",
        type=str,
        action="append",
        required=True,
        help="Candidate input (SMILES or XYZ path); repeat once per candidate",
    )
    nmr.add_argument(
        "--spectrum",
        type=str,
        help="Path to experimental-spectrum text (DevDoc §6.2) or literal text",
    )
    nmr.add_argument("--candidate-metadata-json", help=argparse.SUPPRESS)
    nmr.add_argument(
        "--bruker",
        type=str,
        help=(
            "Bruker raw-data directory (DevDoc §6.3: Proton/ and/or Carbon/ "
            "subdirs, or numbered expno dirs) or a .zip of one. Processed "
            "into an unassigned peak list (stage 0a); mutually exclusive "
            "with --spectrum. Requires nmrglue (pip install 'acp[nmr]')."
        ),
    )
    nmr.add_argument(
        "--bruker-ref",
        type=str,
        action="append",
        metavar="NUC=PPM",
        help=(
            "Manual ppm reference for Bruker peak calibration, e.g. "
            "'H=7.26' (CDCl3 residual). Repeatable; the tallest peak "
            "within the search window is anchored to the given value."
        ),
    )
    nmr.add_argument(
        "--output",
        type=str,
        default="./nmr_output",
        help="Output directory (default: ./nmr_output)",
    )
    nmr.add_argument("--config", type=str, help="Configuration YAML file")
    nmr.add_argument(
        "--nuclei",
        type=str,
        help="Comma-separated target nuclei (default: 1H,13C)",
    )
    nmr.add_argument(
        "--nmr-method",
        type=str,
        help="GIAO DFT method (default mPW1PW91 — must match the error model)",
    )
    nmr.add_argument(
        "--nmr-basis",
        type=str,
        help="GIAO basis set (default 6-311G(d) — must match the error model)",
    )
    nmr.add_argument(
        "--solvent",
        type=str,
        help="Solvent name (applied to both conformer generation and GIAO NMR)",
    )
    nmr.add_argument(
        "--solvent-model",
        type=str.lower,
        help=(
            "ORCA solvation model for the GIAO level: none | cpcm | smd "
            "(none = gas phase; default: cpcm). Validated by "
            "acp.nmr.method_config.resolve_nmr_method."
        ),
    )
    nmr.add_argument(
        "--ewin",
        type=float,
        help="CREST energy window in kcal/mol (default: 6.0)",
    )
    nmr.add_argument(
        "--max-conformers",
        type=int,
        help="Maximum conformers retained per candidate (default: 10)",
    )
    nmr.add_argument(
        "--boltzmann-temp",
        type=float,
        help="Boltzmann-weight temperature in K (default: 298.15)",
    )
    nmr.add_argument(
        "--tms-1h",
        type=float,
        help="Override TMS 1H reference shielding (ppm)",
    )
    nmr.add_argument(
        "--tms-13c",
        type=float,
        help="Override TMS 13C reference shielding (ppm)",
    )
    nmr.add_argument(
        "--error-model",
        type=str,
        help="Error-model id (default goodman-legacy; placeholder-student-t for P1a)",
    )
    nmr.add_argument(
        "--preset",
        type=str,
        default="censo-light",
        choices=["censo-light", "censo-default", "censo-zero"],
        help="Conformer-generation CENSO preset (default: censo-light)",
    )
    nmr.add_argument(
        "--enumerate",
        action="store_true",
        help=(
            "Expand a single under-specified candidate into all distinct "
            "diastereomers (enantiomer pairs collapse — DP4 cannot "
            "distinguish them). Requires one --input only."
        ),
    )
    nmr.add_argument(
        "--stereocenters",
        type=str,
        help=(
            "With --enumerate: atom-label whitelist restricting enumeration "
            "(e.g. 'C5,C8'); centres outside the list keep their input "
            "configuration."
        ),
    )
    nmr.add_argument("--nproc", type=int, help="Number of CPU cores")
    nmr.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    nmr.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    nmr.add_argument("--log-file", type=str, help="Log file path")
    nmr.add_argument("--charge", type=int, help="Molecular charge")
    nmr.add_argument("--multiplicity", type=int, help="Spin multiplicity")
    nmr.add_argument("--save-config", type=str, help="Save effective configuration to this file")

    # -- run xtbmd_censo_energy -----------------------------------------
    # xTB-MD conformer search + CENSO free-energy workflow (DevDoc
    # ACP_xTBMD_CENSO_Energy_DevDoc.html): GFN-FF MD sampling → GFN1
    # batch opt → ISOSTAT dedup → GFN1 energy window → CENSO → fine DFT.
    # NOTE: --ewin here is the GFN1 post-optimization energy window
    # (kcal/mol), distinct from the CREST sampling window of
    # `acp run energy` (same name, different object — DevDoc §9).
    xtbmd = run_sub.add_parser(
        "xtbmd_censo_energy",
        help="xTB-MD conformer search + CENSO free energy "
        "(GFN-FF MD → GFN1 → isostat → CENSO → DFT)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run xtbmd_censo_energy --input "CCO" --output ./result
  acp run xtbmd_censo_energy --input molecule.xyz --preset censo-default
  acp run xtbmd_censo_energy --input "CCO" --md-temp 400 --md-time 100 --md-seeds 3
  acp run xtbmd_censo_energy --input "CCO" --rank1-only --resume
        """,
    )
    xtbmd_input = xtbmd.add_mutually_exclusive_group(required=False)
    xtbmd_input.add_argument(
        "--input",
        type=str,
        help="SMILES string or input file path (XYZ, SDF, MOL, INP, GJF)",
    )
    xtbmd_input.add_argument(
        "--batch-file",
        type=str,
        help="File containing multiple inputs (one per line)",
    )
    xtbmd.add_argument(
        "--output",
        type=str,
        default="./xtbmd_censo_energy_output",
        help="Output directory (default: ./xtbmd_censo_energy_output)",
    )
    xtbmd.add_argument(
        "--config",
        type=str,
        help="Configuration YAML file",
    )
    xtbmd.add_argument(
        "--preset",
        type=str,
        default="censo-light",
        choices=["censo-light", "censo-default", "censo-zero"],
        help="CENSO preset (default: censo-light)",
    )
    xtbmd.add_argument(
        "--no-opt",
        action="store_true",
        help="Disable the fine DFT handoff (CENSO/xTB refinement is final)",
    )
    xtbmd.add_argument(
        "--rank1-only",
        action="store_true",
        help=(
            "Only refine the CENSO/xTB rank1 conformer and compute the "
            "ensemble total free energy G_total = G1 + RT*ln(p1) from the "
            "screening Boltzmann table (default: full ≥99%% ensemble)"
        ),
    )
    xtbmd.add_argument(
        "--threshold",
        type=float,
        default=0.99,
        help="Cumulative Boltzmann population threshold (0<value<=1, default: 0.99)",
    )
    xtbmd.add_argument(
        "--levels",
        type=str,
        help=(
            "JSON method-level overrides, e.g. "
            '\'{"refinement_sp":{"functional":"DLPNO-CCSD(T)","basis":"def2-TZVPP"},'
            '"thermo":{"scale_factor":0.98},"refinement_threshold":0.99}\''
        ),
    )
    xtbmd.add_argument(
        "--solvent",
        type=str,
        help="Solvent name (e.g. dcm, water); applied consistently to MD, GFN1 batch opt and CENSO",
    )
    xtbmd.add_argument(
        "--ewin",
        type=float,
        default=None,
        help=(
            "GFN1 energy window in kcal/mol (default: censo.ewin config, 6.0) — "
            "post-optimization relative-energy cutoff before CENSO. NOTE: unlike "
            "`acp run energy`, this window acts on the GFN1 batch-opt results, "
            "not CREST sampling."
        ),
    )
    xtbmd.add_argument(
        "--nproc",
        type=int,
        help="Number of CPU cores (overrides config)",
    )
    xtbmd.add_argument(
        "--mem",
        type=str,
        help="Memory limit (bare numbers default to GB; use MB/GB/TB suffixes)",
    )
    xtbmd.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    xtbmd.add_argument(
        "--log-file",
        type=str,
        help="Log file path",
    )
    xtbmd.add_argument(
        "--name",
        type=str,
        help="Molecule name (auto-generated if not specified)",
    )
    xtbmd.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    xtbmd.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )
    xtbmd.add_argument(
        "--save-config",
        type=str,
        help="Save effective configuration to this file",
    )
    # -- MD sampling control group --------------------------------------
    xtbmd.add_argument(
        "--md-temp",
        type=float,
        default=400.0,
        help="MD target temperature in K (default: 400)",
    )
    xtbmd.add_argument(
        "--md-time",
        type=float,
        default=100.0,
        help="MD length in ps (default: 100)",
    )
    xtbmd.add_argument(
        "--md-dump",
        type=float,
        default=100.0,
        help="MD trajectory dump interval in fs (default: 100)",
    )
    xtbmd.add_argument(
        "--md-step",
        type=float,
        default=1.0,
        help="MD integration time step in fs (default: 1.0)",
    )
    xtbmd.add_argument(
        "--md-hmass",
        type=float,
        default=1.0,
        help="Hydrogen mass scaling (default: 1.0)",
    )
    xtbmd.add_argument(
        "--md-no-shake",
        action="store_true",
        help="Disable SHAKE X–H bond constraints (enabled by default)",
    )
    xtbmd.add_argument(
        "--md-nvt",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the NVT ensemble (default: on; pass --no-md-nvt for NPT)",
    )
    xtbmd.add_argument(
        "--md-seed",
        type=int,
        default=42,
        help="Base random seed (default: 42; replica seeds increment)",
    )
    xtbmd.add_argument(
        "--md-seeds",
        type=int,
        default=1,
        help=(
            "Number of replica MD trajectories (default: 1; each replica "
            "starts from a distinct RDKit embedding when >1; >=3 recommended "
            "for flexible molecules)"
        ),
    )
    xtbmd.add_argument(
        "--md-method",
        type=str,
        default="gfnff",
        choices=["gfnff", "gfn0", "gfn1", "gfn2"],
        help="MD Hamiltonian (default: gfnff)",
    )
    xtbmd.add_argument(
        "--md-timeout",
        type=int,
        default=None,
        help="Per-MD subprocess timeout in seconds (default: auto-estimated from --md-time)",
    )
    # -- batch-opt / convergence / isostat control group ----------------
    xtbmd.add_argument(
        "--conv-check",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the sampling-convergence diagnostics "
        "(default: on; pass --no-conv-check to disable)",
    )
    xtbmd.add_argument(
        "--conv-novelty-max",
        type=float,
        default=0.10,
        help="Population-weighted second-half novelty cap "
        "(default: 0.10; over-limit is a warning only)",
    )
    xtbmd.add_argument(
        "--conv-rmsd",
        type=float,
        default=0.5,
        help="Conv-check dedup RMSD threshold in Å (default: 0.5; decoupled from --gdis)",
    )
    xtbmd.add_argument(
        "--max-frames",
        type=int,
        default=500,
        help="Batch-opt frame cap (default: 500; 0 = unlimited; uniform subsampling when exceeded)",
    )
    xtbmd.add_argument(
        "--opt-gfn",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="GFN level for the per-frame batch optimization (default: 1)",
    )
    xtbmd.add_argument(
        "--opt-level",
        type=str,
        default="normal",
        choices=["crude", "normal", "tight", "verytight"],
        help="xTB optimization level for the batch optimization (default: normal)",
    )
    xtbmd.add_argument(
        "--opt-timeout",
        type=int,
        default=300,
        help="Per-frame xTB optimization timeout in seconds (default: 300; 0 = unlimited)",
    )
    xtbmd.add_argument(
        "--edis",
        type=float,
        default=0.5,
        help="ISOSTAT energy dedup threshold in kcal/mol (default: 0.5)",
    )
    xtbmd.add_argument(
        "--gdis",
        type=float,
        default=0.25,
        help="ISOSTAT structure RMSD dedup threshold in Å (default: 0.25)",
    )
    xtbmd.add_argument(
        "--keep-frames",
        action="store_true",
        help="Keep per-frame optimization working directories (default: cleaned up)",
    )
    xtbmd.add_argument(
        "--resume",
        action="store_true",
        help="Resume from stage checkpoints (traj/isomers/cluster; fingerprint-validated)",
    )

    # -- simple workflows (singlepoint / optimize / frequency / scan / irc) --
    _add_simple_workflow_parsers(run_sub)
    _add_casscf_parser(run_sub)

    _add_stage_workflow_parsers(run_sub)
    _add_batch_optimize_parser(run_sub)

    # -- run serve (FastAPI server) -----------------------------------------
    serve_parser = run_sub.add_parser(
        "serve",
        help="Start the ACP web dashboard (FastAPI + uvicorn)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run serve
  acp run serve --host 127.0.0.1 --port 8765
  acp run serve --run-root /var/lib/acp/runs --reload
        """,
    )
    serve_parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Bind host (use 0.0.0.0 for LAN access)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port (default: 8765)",
    )
    serve_parser.add_argument(
        "--run-root",
        type=str,
        default=None,
        help=(
            "Root directory for job work directories and the SQLite index. "
            "Falls back to $ACP_RUN_ROOT, then the platform default "
            "(~/.local/share/acp/runs, or /var/lib/acp/runs for root). "
            "Must be on a native filesystem."
        ),
    )
    serve_parser.add_argument(
        "--max-running",
        type=int,
        default=1,
        help="(Deprecated) No longer limits concurrency — jobs are submitted immediately to the cluster. Use --poll-interval instead.",
    )
    serve_parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between status checks for running jobs (default: 30)",
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (development mode)",
    )
    serve_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open a browser on start",
    )
    serve_parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Server log level (default: INFO)",
    )
    serve_parser.add_argument(
        "--execution-mode",
        type=str,
        choices=["local", "remote"],
        default=None,
        help="Server default execution target for jobs that don't specify one "
        "(overrides cluster.execution_mode; per-job execution_mode/target_node win)",
    )

    _add_task_parser(subparsers)

    _normalize_string_choices(parser)

    return parser


# ---------------------------------------------------------------------------
# Workflow handlers
# ---------------------------------------------------------------------------


def _build_simple_method_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in (
        "method",
        "basis",
        "dispersion",
        "solvent_model",
        "solvent",
        "aux_j_basis",
        "aux_c_basis",
        "ri_approximation",
        "grid",
        "scf_convergence",
        "geom_maxiter",
        "opt_convergence",
    ):
        val = getattr(args, key, None)
        if val is not None:
            kwargs[key] = val
    # Hessian policy (plan §9.3): translate the mutually-exclusive CLI
    # group into the canonical ``recalc_hess`` kwarg consumed by the
    # ORCA interface / catalog resolver.
    no_calc_hess = getattr(args, "no_calc_hess", False)
    calc_hess = getattr(args, "calc_hess", None)
    if no_calc_hess:
        kwargs["recalc_hess"] = 0
    elif calc_hess is not None:
        kwargs["recalc_hess"] = calc_hess
    legacy_aux = getattr(args, "aux_j_basis_legacy", None)
    if legacy_aux and kwargs.get("aux_j_basis") in (None, "AutoAux"):
        kwargs["aux_j_basis"] = legacy_aux
    extras = getattr(args, "route_extras", None)
    if extras:
        kwargs["route_extras"] = [x.strip() for x in extras.split(",") if x.strip()]
    electronic_state = _resolve_spin_flags(
        getattr(args, "spin_preset", None), getattr(args, "spin_config", None)
    )
    if electronic_state is not None:
        kwargs["electronic_state"] = electronic_state
    return kwargs


def _resolve_spin_flags(spin_preset: str | None, spin_config: str | None) -> dict[str, Any] | None:
    """Resolve --spin-config / --spin-preset into an electronic-state module.

    ``--spin-config`` wins when both are given; YAML/JSON files may carry
    either the bare module object or the ``{"electronic_state": {...}}``
    envelope (design doc §14.2).
    """
    if spin_config:
        import yaml

        path = Path(spin_config)
        if not path.is_file():
            raise FileNotFoundError(f"spin config file not found: {spin_config}")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"spin config must be a mapping: {spin_config}")
        module = payload.get("electronic_state", payload)
        if not isinstance(module, dict):
            raise ValueError(f"spin config electronic_state must be a mapping: {spin_config}")
        return module
    if spin_preset:
        from acp.catalog import ELECTRONIC_STATE_PRESETS, get_electronic_state_preset

        preset = get_electronic_state_preset(spin_preset)
        if preset is None:
            known = ", ".join(sorted(ELECTRONIC_STATE_PRESETS))
            raise ValueError(f"unknown --spin-preset {spin_preset!r}; available: {known}")
        return {"preset_id": spin_preset, **preset}
    return None


def _add_casscf_parser(run_sub: argparse._SubParsersAction) -> None:
    """Register the CASSCF / NEVPT2 subcommand (design doc §11.3)."""
    parser = run_sub.add_parser(
        "casscf",
        help="CASSCF / NEVPT2 single-point calculation",
        description="Run a CASSCF (optionally SC-/FIC-NEVPT2) single point on one structure",
        epilog=(
            "Examples:\n"
            "  acp run casscf --input mol.xyz --active-electrons 6 --active-orbitals 6\n"
            "  acp run casscf --input mol.xyz --nel 4 --norb 4 --dynamic-correlation sc_nevpt2\n"
            "  acp run casscf --input ts.xyz --nel 6 --norb 6 --spin-preset bs_singlet_flipspin"
        ),
    )
    _add_simple_workflow_args(parser, "casscf")
    parser.add_argument(
        "--active-electrons",
        "--nel",
        type=int,
        required=True,
        help="Number of active-space electrons (mandatory, §19 item 12)",
    )
    parser.add_argument(
        "--active-orbitals",
        "--norb",
        type=int,
        required=True,
        help="Number of active-space orbitals (mandatory, §19 item 12)",
    )
    parser.add_argument(
        "--nroots",
        type=int,
        default=1,
        help="Number of roots; >1 performs state-averaged CASSCF (default: 1)",
    )
    parser.add_argument(
        "--dynamic-correlation",
        choices=["none", "sc_nevpt2", "fic_nevpt2"],
        default="none",
        help="Post-CASSCF dynamic correlation (default: none)",
    )
    parser.add_argument(
        "--orbital-source",
        type=str,
        default=None,
        help="Existing .gbw used as initial orbitals (MORead)",
    )
    parser.add_argument(
        "--frozen-core/--no-frozen-core",
        dest="frozen_core",
        default=True,
        help="Keep core orbitals frozen (default: frozen)",
    )


def _handle_casscf(args: argparse.Namespace) -> int:
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.simple import run_casscf

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    method_kwargs.pop("method", None)
    method_kwargs["casscf"] = {
        "active_electrons": args.active_electrons,
        "active_orbitals": args.active_orbitals,
        "nroots": args.nroots,
        "dynamic_correlation": args.dynamic_correlation,
        "frozen_core": args.frozen_core,
    }
    if args.orbital_source:
        method_kwargs["casscf"]["orbital_source"] = args.orbital_source
    if args.nroots > 1:
        method_kwargs["casscf"]["state_weights"] = [1.0 / args.nroots] * args.nroots
    reporter = ProgressReporter(out, job_name="casscf", stages=["casscf"])
    try:
        result = run_casscf(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
            progress_reporter=reporter,
        )
    except KeyboardInterrupt:
        logger.warning("CASSCF interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("CASSCF failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    if result.status == "completed":
        logger.info("CASSCF calculation completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        reporter.complete()
        return 0
    logger.error("CASSCF calculation failed: %s", result.error)
    reporter.fail(result.error or "CASSCF calculation failed")
    return 1


def _handle_singlepoint(args: argparse.Namespace) -> int:
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.simple import run_singlepoint

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    reporter = ProgressReporter(out, job_name="singlepoint", stages=["single_point"])
    try:
        result = run_singlepoint(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
            progress_reporter=reporter,
        )
    except KeyboardInterrupt:
        logger.warning("Singlepoint interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("Singlepoint failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    if result.status == "completed":
        logger.info("Single-point calculation completed")
        logger.info(
            "  Energy: %s Hartree",
            result.metadata.get("energy") or result.metadata.get("sp_energy") or "N/A",
        )
        reporter.complete()
        return 0
    logger.error("Single-point calculation failed: %s", result.error)
    reporter.fail(result.error or "Single-point calculation failed")
    return 1


def _handle_optimize(args: argparse.Namespace) -> int:
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.simple import run_optimize

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    reporter = ProgressReporter(out, job_name="optimize", stages=["optimize"])
    try:
        result = run_optimize(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
            progress_reporter=reporter,
        )
    except KeyboardInterrupt:
        logger.warning("Optimization interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("Optimization failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    if result.status == "completed":
        logger.info("Geometry optimization completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        reporter.complete()
        return 0
    logger.error("Optimization failed: %s", result.error)
    reporter.fail(result.error or "Optimization failed")
    return 1


def _handle_frequency(args: argparse.Namespace) -> int:
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.simple import run_frequency

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    reporter = ProgressReporter(out, job_name="frequency", stages=["frequency"])
    try:
        result = run_frequency(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
            progress_reporter=reporter,
        )
    except KeyboardInterrupt:
        logger.warning("Frequency calculation interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("Frequency calculation failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    if result.status == "completed":
        logger.info("Frequency calculation completed")
        logger.info("  Modes: %s", result.metadata.get("n_frequencies", "N/A"))
        reporter.complete()
        return 0
    logger.error("Frequency calculation failed: %s", result.error)
    reporter.fail(result.error or "Frequency calculation failed")
    return 1


def _handle_scan(args: argparse.Namespace) -> int:
    """Execute a relaxed internal-coordinate scan."""
    from acp.calculations.contracts import CalculationRequest, StructureArtifact
    from acp.calculations.primitives.scan import ScanCoordinateError
    from acp.calculations.progress import ProgressReporter
    from acp.storage.layout import TaskStorage
    from acp.workflows.simple import (
        _calc_subdir,
        _resolve_output_dir,
        prepare_scan_input,
        run_scan,
    )

    setup_logging(args.log_level)
    reporter = ProgressReporter(Path(args.output), job_name="scan", stages=["scan"])
    try:
        input_plan = prepare_scan_input(
            args.input,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
        )
        cfg = _build_config(args)
        output_root = _resolve_output_dir(Path(args.output))
        calc_dir = _calc_subdir(output_root, args.name, args.input, "scan")
        storage = TaskStorage(calc_dir)
        storage.ensure_layout(stages=["07_PATH"], categories=["structures", "trajectories"])
        input_path = input_plan.materialize(storage)

        method_kwargs = _build_simple_method_kwargs(args)
        method_kwargs.pop("method", None)
        resources: dict[str, Any] = dict(method_kwargs)
        resources.setdefault("geom_maxiter", 200)
        resources.update(
            {
                "backend": "orca",
                "config": cfg,
                "output_dir": str(storage.stage_dir("07_PATH", "ORCA")),
                "result_dir": str(storage.result_dir()),
                "scan_coordinates": list(args.coordinate),
                "scan_points": args.scan_points,
                "use_scants": bool(args.scants),
            }
        )
        effective_charge = args.charge if args.charge is not None else input_plan.charge
        effective_multiplicity = (
            args.multiplicity if args.multiplicity is not None else input_plan.multiplicity
        )
        if effective_charge is not None:
            resources["charge"] = effective_charge
        if effective_multiplicity is not None:
            resources["multiplicity"] = effective_multiplicity

        result = run_scan(
            CalculationRequest(
                input_artifact=StructureArtifact(
                    path=input_path,
                    elements=list(input_plan.symbols),
                    source="smiles" if input_plan.is_smiles else "cli",
                ),
                method=args.method,
                resources=resources,
                workflow="scan",
                profile="default",
            ),
            progress_reporter=reporter,
        )
    except ScanCoordinateError as exc:
        logger.error("Scan coordinate validation failed: %s", exc)
        reporter.fail(str(exc))
        return 2
    except KeyboardInterrupt:
        logger.warning("Relaxed scan interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("Relaxed scan failed: %s", exc)
        reporter.fail(str(exc))
        return 1

    if result.status == "completed":
        logger.info("Relaxed scan completed")
        logger.info("  Frames: %s", result.metadata.get("frame_count", "N/A"))
        logger.info("  Output: %s", result.metadata.get("output_dir", args.output))
        reporter.complete()
        return 0
    logger.error("Relaxed scan failed: %s", "; ".join(result.errors))
    reporter.fail("; ".join(result.errors) or "Relaxed scan failed")
    return 1


def _handle_irc(args: argparse.Namespace) -> int:
    """Execute an independent IRC request from one transition-state artifact."""
    import hashlib

    from acp.calculations.contracts import StructureArtifact
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.irc import run_irc_workflow

    setup_logging(args.log_level)
    directions = ("forward", "reverse") if args.direction == "both" else (args.direction,)
    stages = ["preparing"]
    if "forward" in directions:
        stages.append("irc_forward")
    if "reverse" in directions:
        stages.append("irc_backward")
    stages.append("validating")
    reporter = ProgressReporter(Path(args.output), job_name="irc", stages=stages)
    try:
        proof_text = args.ts_provenance_json or Path(args.ts_provenance).read_text(encoding="utf-8")
        proof = json.loads(proof_text)
        if not isinstance(proof, dict) or proof.get("schema") != "irc_ts_source_v1":
            raise ValueError("IRC requires verified TS source provenance")
        xyz_hash = hashlib.sha256(Path(args.input).read_bytes()).hexdigest()
        if xyz_hash != proof.get("geometry_sha256"):
            raise ValueError("IRC TS geometry differs from its verified source")
        method = str(proof.get("method") or "")
        basis = str(proof.get("basis") or "")
        charge = int(proof["charge"])
        multiplicity = int(proof["multiplicity"])
        if (
            not method
            or (args.method and args.method != method)
            or (args.basis and args.basis != basis)
        ):
            raise ValueError("IRC method and basis must match the verified TS source")
        if args.charge is not None and args.charge != charge:
            raise ValueError("IRC charge must match the verified TS source")
        if args.multiplicity is not None and args.multiplicity != multiplicity:
            raise ValueError("IRC multiplicity must match the verified TS source")
        result = run_irc_workflow(
            input_artifact=StructureArtifact(path=Path(args.input), source="cli"),
            directions=directions,
            output_dir=Path(args.output),
            config=_build_config(args),
            method=method,
            basis=basis,
            maxpoints=args.maxpoints,
            step=args.step,
            input_role=args.input_role,
            charge=charge,
            multiplicity=multiplicity,
            progress_reporter=reporter,
            source_provenance=proof,
        )
    except ValueError as exc:
        logger.error("IRC input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except KeyboardInterrupt:
        logger.warning("IRC interrupted by user")
        reporter.fail("interrupted")
        return 130
    except (OSError, RuntimeError, TypeError) as exc:
        logger.exception("IRC failed: %s", exc)
        reporter.fail(str(exc))
        return 1

    if result.status == "completed":
        logger.info("IRC completed")
        logger.info("  Report: %s", result.metadata.get("report_path", "N/A"))
        reporter.complete()
        return 0
    logger.error("IRC failed: %s", result.error)
    reporter.fail(result.error or "IRC failed")
    return 1


def _handle_tsmode(args: argparse.Namespace) -> int:
    """Run the TS Mode directed optimization workflow from a bundle file."""
    from acp.calculations.progress import ProgressReporter
    from acp.calculations.tsmode import TsmodeError
    from acp.workflows.tsmode import run_tsmode

    setup_logging(args.log_level)
    reporter = ProgressReporter(
        Path(args.output),
        job_name="tsmode",
        stages=[
            "prepare_source",
            "resolve_target",
            "optimize_ts",
            "frequency_final",
            "validate_ts",
            "publish_results",
        ],
    )
    try:
        result = run_tsmode(
            args.source_bundle,
            args.source_mode_index,
            output_dir=args.output,
            config=_build_config(args),
            name=args.name,
            optimization_overrides={
                "max_iterations": args.max_steps,
                "recalc_hess": args.recalc_hess,
                "trust_radius": args.trust_radius,
                "retry_limit": args.retry_limit,
                "final_frequency": (not args.no_final_frequency) or None,
                "allow_unverified_mapping": args.allow_unverified_mapping or None,
            },
            resources={"nproc": args.nproc, "mem": args.mem},
            progress_reporter=reporter,
        )
    except TsmodeError as exc:
        logger.error("TS Mode %s: %s", exc.error_code, exc.detail)
        reporter.fail(f"{exc.error_code}: {exc.detail}")
        return 2
    except (ValueError, FileNotFoundError) as exc:
        logger.error("TS Mode input error: %s", exc)
        reporter.fail(str(exc))
        return 2
    except KeyboardInterrupt:
        logger.warning("TS Mode interrupted by user")
        reporter.fail("interrupted")
        return 130
    except (OSError, RuntimeError, TypeError) as exc:
        logger.exception("TS Mode failed: %s", exc)
        reporter.fail(str(exc))
        return 1

    if result.status == "completed":
        logger.info("TS Mode completed")
        reporter.complete()
        return 0
    logger.error("TS Mode failed: %s", result.error)
    reporter.fail(result.error or "tsmode failed")
    return 1


def _build_xtb_method_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in ("gfn", "opt_level", "solvent_model", "solvent", "max_steps"):
        val = getattr(args, key, None)
        if val is not None:
            kwargs[key] = val
    return kwargs


def _handle_xtb_optimize(args: argparse.Namespace) -> int:
    from acp.calculations.progress import ProgressReporter
    from acp.workflows.simple import run_xtb_optimize

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_xtb_method_kwargs(args)
    reporter = ProgressReporter(out, job_name="xtb_optimize", stages=["xtb_optimize"])
    try:
        result = run_xtb_optimize(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
            progress_reporter=reporter,
        )
    except KeyboardInterrupt:
        logger.warning("xTB optimization interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("xTB optimization failed: %s", exc)
        reporter.fail(str(exc))
        return 1
    if result.status == "completed":
        logger.info("xTB geometry optimization completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        reporter.complete()
        return 0
    logger.error("xTB optimization failed: %s", result.error)
    reporter.fail(result.error or "xTB optimization failed")
    return 1


def _handle_serve(args: argparse.Namespace) -> int:
    """Start the FastAPI web dashboard server."""
    try:
        import uvicorn
    except ImportError:
        print(
            'ERROR: uvicorn is not installed. Run: pip install -e ".[api]"',
            file=sys.stderr,
        )
        return 1

    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", 8765)
    reload_flag = getattr(args, "reload", False)
    run_root = getattr(args, "run_root", None)
    max_running = getattr(args, "max_running", 1)
    poll_interval = getattr(args, "poll_interval", 15)
    log_level = getattr(args, "log_level", "INFO").lower()
    no_browser = getattr(args, "no_browser", False)

    from acp.core.paths import check_run_root_safety, resolve_run_root

    run_root_path = resolve_run_root(run_root)
    run_root_path.mkdir(parents=True, exist_ok=True)
    for safety_message in check_run_root_safety(run_root_path):
        print(f"WARNING: {safety_message}", file=sys.stderr)

    os.environ["ACP_RUN_ROOT"] = str(run_root_path)
    os.environ["ACP_HOST"] = host
    os.environ["ACP_PORT"] = str(port)
    os.environ["ACP_MAX_RUNNING"] = str(max_running)
    os.environ["ACP_POLL_INTERVAL"] = str(poll_interval)
    execution_mode = getattr(args, "execution_mode", None)
    if execution_mode:
        os.environ["ACP_EXECUTION_MODE"] = execution_mode

    url = f"http://{host}:{port}"
    print(f"ACP Workbench starting at {url}")
    print(f"  Run root      : {run_root_path}")
    print(f"  Poll interval : {poll_interval}s")
    print(f"  Docs (Swagger): {url}/docs")
    print(f"  Reload        : {'on' if reload_flag else 'off'}")
    if _is_wsl_environment():
        print()
        print("  Running under WSL — open this URL in Windows Chrome:")
        print(f"    {url}")
        print("  If Windows cannot reach 127.0.0.1, try `wsl hostname -I` and use that IP.")
    print()

    if not no_browser and not reload_flag:
        _try_open_browser(url)

    uvicorn.run(
        "acp.api.server:app",
        host=host,
        port=port,
        reload=reload_flag,
        log_level=log_level,
    )
    return 0


def _is_wsl_environment() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version") as f:
            content = f.read().lower()
        return "microsoft" in content or "wsl" in content
    except OSError:
        return False


def _try_open_browser(url: str) -> None:
    import shutil as _shutil
    import subprocess as _sp

    for opener in ("wslview", "xdg-open", "explorer.exe"):
        if _shutil.which(opener):
            try:
                _sp.Popen([opener, url], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                return
            except OSError:
                continue


_RETIRED_WORKFLOW_MESSAGE = (
    "The workflow has been retired.\n"
    "该工作流已退役，不能用于新任务。\n"
    "Use Confsearch, PESsearch, BatchOptimize or the standalone irc workflow.\n"
    "Mapping: ensemble -> Confsearch + censo-crest + screen; "
    "energy -> Confsearch + censo-crest + rank1/cumulative-99; "
    "xtbmd_censo_energy -> Confsearch + xtbmd-censo; "
    "mechanism/mech-* -> PESsearch + BatchOptimize + irc."
)

_CLI_REMOVED_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {
        "ensemble",
        "energy",
        "xtbmd_censo_energy",
        "mechanism",
        "mech-conf",
        "mech-step",
        "mech-confirm",
        "mech-chain",
        "optfreq",
        "optfreqsp",
        "Lowconfirm",
        "Highconfirm",
    }
)


def _reject_retired_workflow(workflow: str) -> int:
    setup_logging("INFO")
    print(f"Error: '{workflow}' is no longer available for new runs.", file=sys.stderr)
    print(_RETIRED_WORKFLOW_MESSAGE, file=sys.stderr)
    return 2


def _handle_ensemble(_args: argparse.Namespace) -> int:
    return _reject_retired_workflow("ensemble")


def _parse_levels_json(levels_str: str | None) -> dict[str, Any] | None:
    """Parse the --levels JSON string; returns None on error or empty input."""
    if not levels_str:
        return None
    try:
        parsed = json.loads(levels_str)
    except json.JSONDecodeError as exc:
        print(f"Error: invalid --levels JSON: {exc}", file=sys.stderr)
        return None
    if not isinstance(parsed, dict):
        print("Error: --levels must be a JSON object", file=sys.stderr)
        return None
    return parsed


def _handle_energy(_args: argparse.Namespace) -> int:
    return _reject_retired_workflow("energy")


def _handle_xtbmd_censo_energy(_args: argparse.Namespace) -> int:
    return _reject_retired_workflow("xtbmd_censo_energy")


def _handle_nmr(args: argparse.Namespace) -> int:
    """Execute the NMR + DP4/DP5 workflow."""
    setup_logging(args.log_level)

    if not args.input:
        print("Error: at least one --input candidate is required", file=sys.stderr)
        return 1
    if bool(args.spectrum) == bool(args.bruker):
        print(
            "Error: exactly one of --spectrum / --bruker is required",
            file=sys.stderr,
        )
        return 1

    candidate_metadata = None
    if getattr(args, "candidate_metadata_json", None):
        try:
            candidate_metadata = json.loads(args.candidate_metadata_json)
        except json.JSONDecodeError as exc:
            logger.error("Invalid NMR candidate metadata: %s", exc)
            return 1
        if (
            not isinstance(candidate_metadata, list)
            or len(candidate_metadata) != len(args.input)
            or not all(isinstance(item, dict) for item in candidate_metadata)
        ):
            logger.error("NMR candidate metadata must contain one object per input")
            return 1

    bruker_references: dict[str, float] | None = None
    if args.bruker_ref:
        bruker_references = {}
        for token in args.bruker_ref:
            if "=" not in token:
                print(
                    f"Error: --bruker-ref expects NUC=PPM (got {token!r})",
                    file=sys.stderr,
                )
                return 1
            key, _, value = token.partition("=")
            try:
                bruker_references[key.strip()] = float(value)
            except ValueError:
                print(
                    f"Error: --bruker-ref ppm must be numeric (got {token!r})",
                    file=sys.stderr,
                )
                return 1

    nuclei: list[str] | None = None
    if args.nuclei:
        nuclei = [n.strip() for n in args.nuclei.split(",") if n.strip()]
        if not nuclei:
            nuclei = None

    cfg = _build_config(args)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.save_config:
        from cccp.config import save_config as save_cfg

        save_cfg(cfg, Path(args.save_config))
        logger.info("Configuration saved to: %s", args.save_config)

    from acp.calculations.progress import ProgressReporter
    from acp.nmr.method_config import NmrMethodConfigError, resolve_nmr_method
    from acp.workflows.nmr import NMR_STAGES, run_nmr_analysis

    reporter = ProgressReporter(
        output_dir,
        job_name="nmr",
        stages=list(NMR_STAGES),
    )

    # G06: CLI flags → typed method payload → resolve_nmr_method so catalog
    # functional/basis/solvent_model/nuclei all flow to the analysis call.
    method: dict[str, Any] = {
        "nmr_method": args.nmr_method,
        "nmr_basis": args.nmr_basis,
        "solvent": args.solvent,
        "solvent_model": args.solvent_model,
        "nuclei": nuclei,
        "boltzmann_temp": args.boltzmann_temp,
        "tms_shielding_h": args.tms_1h,
        "tms_shielding_c": args.tms_13c,
        "ewin": args.ewin,
        "max_conformers": args.max_conformers,
        "error_model": args.error_model,
        "conformer_preset": args.preset,
    }
    try:
        resolved = resolve_nmr_method(method, cfg)
    except NmrMethodConfigError as exc:
        logger.error("NMR method resolution failed: %s", exc)
        return 1

    try:
        result = run_nmr_analysis(
            input_sources=args.input,
            candidate_metadata=candidate_metadata,
            spectrum=args.spectrum,
            output_dir=str(output_dir),
            config=cfg,
            nuclei=list(resolved.nuclei),
            nmr_method=resolved.nmr_method,
            nmr_basis=resolved.nmr_basis,
            solvent=resolved.solvent,
            solvent_model=resolved.solvent_model,
            charge=args.charge,
            multiplicity=args.multiplicity,
            nproc=args.nproc,
            boltzmann_temp=resolved.boltzmann_temp,
            tms_1h=resolved.tms_1h,
            tms_13c=resolved.tms_13c,
            error_model=resolved.error_model,
            conformer_preset=resolved.conformer_preset,
            ewin=resolved.ewin,
            max_conformers=resolved.max_conformers,
            enumerate_stereoisomers=bool(args.enumerate),
            stereocenters=args.stereocenters,
            bruker=args.bruker,
            bruker_references=bruker_references,
            progress_reporter=reporter,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        reporter.fail("interrupted")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        reporter.fail(str(exc))
        return 1

    if result.status == "completed":
        meta = result.metadata or {}
        logger.info("NMR + DP4/DP5 workflow completed successfully")
        winner = meta.get("winner")
        if winner:
            dp4 = winner.get("dp4")
            dp5 = winner.get("dp5")
            logger.info(
                "  Winner: candidate %s (%s) — DP4=%s, DP5=%s",
                winner.get("index"),
                winner.get("label"),
                f"{float(dp4):.3f}" if dp4 is not None else "N/A",
                f"{float(dp5):.3f}" if dp5 is not None else "N/A",
            )
        logger.info("  Candidates        : %s", meta.get("n_candidates", "N/A"))
        logger.info("  Report JSON       : %s", meta.get("report_json", "N/A"))
        logger.info("  Report XLSX       : %s", meta.get("report_xlsx", "N/A"))
        logger.info("  Error model       : %s", meta.get("error_model", "N/A"))
        if meta.get("note"):
            logger.warning("  NOTE: %s", meta["note"])
        reporter.complete()
        return 0

    logger.error("NMR workflow failed: %s", result.error)
    reporter.fail(result.error or "NMR workflow failed")
    return 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _preflight_workflow(workflow: str) -> None:
    """Warn about unresolvable binaries a workflow normally requires.

    Runs the centralized resolver once per required binary so the user
    learns about missing QC software before the workflow starts. Warning-only:
    partial workflows (e.g. ``energy --no-opt``) may legitimately skip some
    binaries; the interfaces raise a friendly error at the exact stage that
    actually needs one.
    """
    from acp.workflows.registry import get_workflow_entry
    from cccp.config import load_config
    from cccp.software import ENV_VARS, resolve_executable

    entry = get_workflow_entry(workflow)
    if entry is None or not entry.requires_binaries:
        return
    try:
        configured_executables = load_config().get("executables") or {}
    except Exception:
        configured_executables = {}
    for name in entry.requires_binaries:
        configured = configured_executables.get(name)
        configured_path = configured.get("path") if isinstance(configured, dict) else None
        if resolve_executable(name, configured_path=configured_path) is None:
            env_var = ENV_VARS.get(name, f"CONFSEARCH_{name.upper()}_PATH")
            logger.warning(
                "Preflight: executable '%s' (declared by '%s'; engine "
                "configurations that never call it are unaffected) was not "
                "found. Add it to PATH, set %s, or configure executables.%s.path.",
                name,
                workflow,
                env_var,
                name,
            )


def _doctor_binary_sanity(name: str, path: Path) -> str | None:
    """Return a rejection reason when *path* is not a plausible QC binary."""
    try:
        head = Path(path).read_bytes()[:4096]
    except OSError as exc:
        return f"不可读取：{exc}"
    if not head:
        return "空文件"
    lowered = head.lower()
    if b"igalia" in lowered or b"screen reader" in lowered:
        return "GNOME Orca 屏幕阅读器（非 QC 二进制）"
    if head.startswith(b"\x7fELF"):
        return None
    if head.startswith(b"#!"):
        first_line = head.split(b"\n", 1)[0].decode("utf-8", "replace").lower()
        if name == "orca" and "python" in first_line:
            return f"Python 脚本，不是 QC ORCA（{first_line.strip()}）"
        return None
    if name == "orca":
        return "不是 QC ORCA 二进制（缺少 ELF 文件头）"
    return None


def _doctor_software_checks(cfg: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """Resolve every QC binary without ever hiding a configured-but-invalid pin."""
    from cccp.software import EXECUTABLES, resolve_executable_with_source

    executables = cfg.get("executables")
    if not isinstance(executables, Mapping):
        executables = {}
    checks: list[tuple[str, str, str]] = []
    for name in EXECUTABLES:
        entry = executables.get(name)
        configured: str | None = None
        if isinstance(entry, Mapping):
            raw_path = entry.get("path")
            if raw_path is not None and not isinstance(raw_path, bool):
                text = str(raw_path).strip()
                if text:
                    configured = text
        resolved, source = resolve_executable_with_source(name, configured_path=configured)
        label = f"software:{name}"
        if configured is not None and source != "config":
            checks.append(
                (
                    label,
                    "FAIL",
                    f"已配置路径不可用：{configured}（拒绝 PATH 回落：{resolved}）",
                )
            )
            continue
        if resolved is None:
            if configured is not None:
                checks.append((label, "FAIL", f"已配置路径不可用：{configured}"))
            else:
                checks.append((label, "NOT_VERIFIED", "未配置；PATH 中未找到，科学能力尚未验证"))
            continue
        reason = _doctor_binary_sanity(name, resolved)
        if reason is not None:
            state = "FAIL" if configured is not None else "NOT_VERIFIED"
            checks.append((label, state, f"{resolved} — {reason}"))
            continue
        checks.append((label, "PASS", f"{resolved}（source={source}）"))
    return checks


def _is_local_deployment(cfg: Mapping[str, Any]) -> bool:
    deployment = cfg.get("deployment")
    if not isinstance(deployment, Mapping):
        return False
    usage = str(deployment.get("usage") or "").strip().lower()
    tier = str(deployment.get("tier") or "").strip().lower()
    return usage == "local" or tier in {"local-single", "standalone", "local"}


def _run_local_doctor(cfg: Mapping[str, Any]) -> int:
    """Report local capability (PASS/FAIL/NOT_VERIFIED) for a local tier."""
    from acp.core.paths import check_run_root_safety, resolve_run_root

    deployment = cfg.get("deployment")
    deployment_map = deployment if isinstance(deployment, Mapping) else {}
    checks = _doctor_software_checks(cfg)
    root: Path | None = None
    try:
        root = resolve_run_root(None)
        warnings = check_run_root_safety(root)
        target = root if root.exists() else root.parent
        if root.exists() and not root.is_dir():
            checks.append(("run_root", "FAIL", f"{root} 不是目录"))
        elif not os.access(target, os.W_OK):
            checks.append(("run_root", "FAIL", f"{root} 不可写"))
        else:
            detail = "；".join(warnings) if warnings else "就绪"
            checks.append(("run_root", "PASS", f"{root} — {detail}"))
    except (OSError, ValueError) as exc:
        checks.append(("run_root", "FAIL", str(exc)))
    if root is not None:
        try:
            from acp_services.store.store import JobStore

            db_path = root / "acp_jobs.db"
            JobStore(db_path)
            checks.append(("store", "PASS", str(db_path)))
        except (OSError, RuntimeError, ValueError) as exc:
            checks.append(("store", "FAIL", str(exc)))
    checks.append(("config", "PASS", "merged configuration loaded"))

    print(
        "\nLocal capability report "
        f"(usage={deployment_map.get('usage', '?')}, tier={deployment_map.get('tier', '?')})"
    )
    failures = 0
    for state in ("PASS", "FAIL", "NOT_VERIFIED"):
        entries = [check for check in checks if check[1] == state]
        print(f"  {state}（{len(entries)}）：")
        if not entries:
            print("    - （无）")
        for name, _state, detail in entries:
            if state == "FAIL":
                failures += 1
            print(f"    - {name} — {detail}")
    print(
        "\nLocal doctor finished: "
        + ("all checks passed" if failures == 0 else f"{failures} failure(s) found")
    )
    return 1 if failures else 0


def _handle_doctor(args: argparse.Namespace) -> int:
    """``acp doctor`` — deployment self-check for remote nodes and local tiers.

    Remote clusters keep the existing node/binary self-check.  A local
    (standalone) deployment with zero remote nodes reports local capability
    instead of failing solely because ``cluster.nodes`` is empty; an
    explicitly configured-but-invalid executable is always FAIL, never a
    silent PATH fallback PASS.
    """
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    from acp.scheduler.remote.config import RemoteExecutionConfig
    from acp.scheduler.remote.node_manager import doctor_node
    from acp.scheduler.remote.ssh import SSHConnectionPool
    from cccp.config import load_config

    try:
        cfg = load_config()
    except ValueError as exc:
        print(f"FAIL config — {exc}")
        return 1
    cluster = cfg.get("cluster") or {}
    remote_cfg = RemoteExecutionConfig.from_config_dict(cluster)
    if not remote_cfg.nodes:
        if args.node is None and _is_local_deployment(cfg):
            print(
                "No remote nodes configured — running local capability checks "
                "(local/standalone deployment)."
            )
            return _run_local_doctor(cfg)
        print("No remote nodes configured (cluster.nodes is empty).")
        return 1

    if args.node is not None:
        node = remote_cfg.get_node(args.node)
        if node is None:
            print(f"Unknown node {args.node!r}. Configured: {[n.name for n in remote_cfg.nodes]}")
            return 1
        nodes = [node]
    else:
        nodes = remote_cfg.enabled_nodes

    pool = SSHConnectionPool()
    failures = 0
    try:
        for node in nodes:
            report = doctor_node(pool, node)
            print(f"\n== node {report.node} ({report.host}) ==")
            if not report.reachable:
                print(f"  UNREACHABLE: {report.error}")
                failures += 1
                continue
            if report.python is None:
                print(
                    "  Python: NO usable Python 3.10+ interpreter "
                    "(configure cluster.nodes[].python_executable)"
                )
                failures += 1
            else:
                print(
                    f"  Python: {report.python.python_executable} (version {report.python.version})"
                )
            for name, info in sorted(report.software.items()):
                resolved = info.get("resolved")
                if resolved:
                    version = f" [version: {info.get('version')}]" if info.get("version") else ""
                    print(f"  {name:8s} -> {resolved}{version}")
                else:
                    print(f"  {name:8s} -> MISSING (configured: {info.get('configured')!r})")
                    failures += 1
            for name, state in sorted(report.symlinks.items()):
                print(f"  ~/bin/{name}: {state}")
            if report.error:
                print(f"  note: {report.error}")
    finally:
        pool.close()
    print(
        "\nDoctor finished: "
        + ("all checks passed" if failures == 0 else f"{failures} failure(s) found")
    )
    return 1 if failures else 0


def _handle_init(args: argparse.Namespace) -> int:
    """``acp init`` — unified setup wizard for the user config.

    Delegates to :func:`acp.init_wizard.flows.run_init` (exit-code mapping
    stays in the CLI layer; the wizard module itself stays import-safe on
    core-only installs — remote-stack imports live inside its flows).  The
    default call keeps the single-argument signature for compatibility.
    """
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    from acp.init_wizard.flows import run_init

    answers_path = getattr(args, "answers", None)
    plan_only = bool(getattr(args, "plan", False))
    if answers_path is not None or plan_only:
        return run_init(Path(args.config), answers_path=answers_path, plan_only=plan_only)
    return run_init(Path(args.config))


# ---------------------------------------------------------------------------
# Unified machine output (B08-1 / todo 42) — additive ``--json`` / ``--dry-run``
# ---------------------------------------------------------------------------

#: Simple workflows that expose the machine-output flags (added by
#: ``_add_simple_workflow_args``).  Text mode for every workflow is unchanged.
_MACHINE_OUTPUT_WORKFLOWS: Final[frozenset[str]] = frozenset(
    {"singlepoint", "optimize", "frequency", "scan", "casscf"}
)

#: Dry-run step names per workflow (planning only — never executed).
_DRY_RUN_STEPS: Final[dict[str, tuple[str, ...]]] = {
    "singlepoint": ("single_point",),
    "optimize": ("optimize",),
    "frequency": ("frequency",),
    "scan": ("scan",),
    "casscf": ("casscf",),
}


def _machine_output_requested(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "dry_run", False))


def _dry_run_plan(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build the dry-run plan/estimate from parsed args only (zero side effects).

    Nothing here touches the filesystem, launches ``cccp.calculation.run_*`` or
    reserves core-hours; the identity is a pure hash of the non-path plan
    fields so it is host-independent.
    """
    workflow = str(args.workflow)
    plan: dict[str, Any] = {
        "workflow": workflow,
        "steps": list(_DRY_RUN_STEPS.get(workflow, (workflow,))),
        "method": getattr(args, "method", None) or None,
        "basis": getattr(args, "basis", None) or None,
        "charge": getattr(args, "charge", None),
        "multiplicity": getattr(args, "multiplicity", None),
        "output_dir": str(getattr(args, "output", "") or ""),
    }
    identity_material = {key: value for key, value in plan.items() if key != "output_dir"}
    digest = hashlib.sha256(
        json.dumps(identity_material, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]
    estimate: dict[str, Any] = {
        "requested_nproc": getattr(args, "nproc", None),
        "requested_mem": getattr(args, "mem", None),
        "executes_science": False,
        "consumes_core_hours": False,
    }
    return plan, estimate, f"v2:{digest}"


def _dispatch_machine_output(
    args: argparse.Namespace, handler: Callable[[argparse.Namespace], int]
) -> int:
    """Emit the unified JSON envelope for ``--json`` / ``--dry-run``.

    ``--dry-run`` never calls *handler* (no science, no transfer, no metering).
    ``--json`` runs the handler with stdout redirected to stderr so stdout
    carries only the envelope; the handler's exit code is preserved.
    """
    from acp_services.entrypoints import machine_output as mo

    operation = str(getattr(args, "workflow", "unknown"))
    if bool(getattr(args, "dry_run", False)):
        plan, estimate, identity = _dry_run_plan(args)
        result = mo.dry_run_result(
            operation, plan=plan, estimate=estimate, operation_identity=identity
        )
        print(result.to_json(indent=2))
        return int(result.exit_code)
    with redirect_stdout(sys.stderr):
        exit_code = int(handler(args))
    if exit_code == mo.ExitCode.OK:
        result = mo.MachineResult(operation=operation, status="ok", exit_code=exit_code)
    else:
        result = mo.MachineResult(
            operation=operation,
            status="error",
            exit_code=exit_code,
            error=mo.default_error_for_exit(exit_code),
        )
    print(result.to_json(indent=2))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """
    ACP CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code.
    """
    argv_values = list(sys.argv[1:] if argv is None else argv)
    if (
        len(argv_values) > 1
        and argv_values[0] == "run"
        and argv_values[1] in _CLI_REMOVED_WORKFLOWS
    ):
        return _reject_retired_workflow(argv_values[1])
    parser = build_parser()
    args = parser.parse_args(argv_values)
    args._argument_parser = parser

    if args.command == "run":
        dispatch: dict[str, Callable[[argparse.Namespace], int]] = {
            "Confsearch": _handle_confsearch,
            "PESsearch": _handle_pessearch,
            "BatchOptimize": _handle_batch_optimize,
            "XtbPathSearch": _handle_xtb_path_search,
            "OrcaGradient": _handle_orca_gradient,
            "XtbConstrainedOpt": _handle_xtb_constrained_opt,
            "ensemble": _handle_ensemble,
            "energy": _handle_energy,
            "nmr": _handle_nmr,
            "xtbmd_censo_energy": _handle_xtbmd_censo_energy,
            "serve": _handle_serve,
            "singlepoint": _handle_singlepoint,
            "optimize": _handle_optimize,
            "frequency": _handle_frequency,
            "scan": _handle_scan,
            "irc": _handle_irc,
            "tsmode": _handle_tsmode,
            "casscf": _handle_casscf,
            "xtb_optimize": _handle_xtb_optimize,
        }
        handler = dispatch.get(args.workflow)
        if handler is None:
            parser.print_help()
            return 1
        if _machine_output_requested(args) and args.workflow in _MACHINE_OUTPUT_WORKFLOWS:
            return _dispatch_machine_output(args, handler)
        _preflight_workflow(args.workflow)
        return handler(args)

    if args.command == "doctor":
        return _handle_doctor(args)

    if args.command == "init":
        return _handle_init(args)

    if args.command == "task":
        return _dispatch_task(args)

    if args.command != "run":
        parser.print_help()
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
