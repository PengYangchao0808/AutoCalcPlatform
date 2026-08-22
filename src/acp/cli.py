# ruff: noqa: E501
# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnusedCallResult=false, reportUnknownVariableType=false, reportUnknownLambdaType=false, reportImplicitStringConcatenation=false, reportUnusedFunction=false, reportImplicitOverride=false, reportUnknownParameterType=false, reportMissingTypeArgument=false, reportPrivateUsage=false, reportUnusedVariable=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportMissingImports=false
"""ACP CLI — Unified command-line interface for Auto-Calc Platform.

Subcommands:
    run ensemble    Ensemble generation (CREST → CENSO)
    run energy      Conformer energy ranking
    run mechanism   Mechanism study workflow
    run serve       Start the ACP web dashboard (FastAPI + uvicorn)

Usage:
    acp run ensemble --input "CCO" --output ./result
    acp run energy --help
    acp run mechanism --help
    acp run serve --help
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with the given level."""
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

    config = legacy_load(config_path=Path(args.config) if args.config else None)

    if getattr(args, "nproc", None) is not None:
        config.setdefault("resources", {})["nproc"] = args.nproc
        config.setdefault("executables", {}).setdefault("orca", {})["nproc"] = args.nproc

    if getattr(args, "mem", None) is not None:
        config.setdefault("resources", {})["mem"] = args.mem

    return config


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
    """Register 5 simple ORCA workflow subcommand parsers + xtb_optimize."""
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
            "optfreq",
            "Opt + Freq",
            "Run ORCA Opt+Freq as single job",
            "Examples:\n  acp run optfreq --input mol.xyz --output ./out\n  acp run optfreq --input mol.gjf --method r2SCAN-3c",
        ),
        (
            "optfreqsp",
            "Opt+Freq+SP+Thermo",
            "Full pipeline: opt -> freq -> SP -> Shermo",
            "Examples:\n  acp run optfreqsp --input mol.xyz --output ./out\n  acp run optfreqsp --input mol.xyz --method r2SCAN-3c --sp-method wB97M-V",
        ),
    ]:
        p = run_sub.add_parser(
            wf, help=wf_desc, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=wf_epilog
        )
        p.set_defaults(workflow=wf)
        _add_simple_workflow_args(p, wf)

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
    parser.add_argument("--mem", type=str, help="Memory limit (e.g. 32GB, 4096MB)")
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

    if wf in ("singlepoint", "optimize", "frequency", "optfreq", "optfreqsp"):
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

    if wf in ("optimize", "optfreq", "optfreqsp"):
        parser.add_argument(
            "--geom-maxiter",
            type=int,
            help="Max geometry iterations (maps to MaxIter in %%geom block)",
        )
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

    if wf == "optfreqsp":
        parser.add_argument(
            "--temperature", type=float, default=298.15, help="Temperature in K (default: 298.15)"
        )
        parser.add_argument(
            "--pressure", type=float, default=1.0, help="Pressure in atm (default: 1.0)"
        )
        parser.add_argument(
            "--scale-factor",
            type=float,
            default=0.9905,
            help="Frequency scale factor for ZPE/thermo (default: 0.9905)",
        )

    if wf == "optfreqsp":
        parser.add_argument(
            "--sp-method", default="wB97M-V", help="SP functional (default: wB97M-V)"
        )
        parser.add_argument(
            "--sp-basis", default="def2-TZVPP", help="SP basis set (default: def2-TZVPP)"
        )
        parser.add_argument(
            "--sp-aux-j-basis",
            default="AutoAux",
            metavar="BASIS",
            help="SP auxiliary /J basis for RI-J fitting (default: AutoAux). Common: AutoAux, def2/J.",
        )
        parser.add_argument(
            "--sp-aux-c-basis",
            default="AutoAux",
            metavar="BASIS",
            help="SP auxiliary /C basis for RI-MP2 correlation (default: AutoAux). "
            "Common: AutoAux, def2-TZVPP/C. Only used by double-hybrid functionals (PWPB95) and DLPNO.",
        )
        parser.add_argument("--sp-aux-basis", default=None, help=argparse.SUPPRESS)
        parser.add_argument(
            "--sp-ri-approximation",
            default="RIJCOSX",
            choices=["none", "RI", "RIJCOSX", "RIJK"],
            help="SP RI approximation (default: RIJCOSX)",
        )
        parser.add_argument("--sp-dispersion", default="none", help="SP dispersion correction")
        parser.add_argument(
            "--sp-solvent", default="", help="SP solvent name (e.g. water; defaults to --solvent)"
        )
        parser.add_argument(
            "--sp-solvent-model", default="", help="SP solvent model (defaults to --solvent-model)"
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


def _add_mechanism_module_parsers(run_sub: argparse._SubParsersAction) -> None:
    """Register standalone mechanism module subcommands (M1-M4)."""
    conf = run_sub.add_parser(
        "mech-conf",
        help="Mechanism conformer search for one stable state (module M1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run mech-conf --input "CCO" --output ./conf_out
  acp run mech-conf --input mol.xyz --mode xtb-fast --output ./conf_out
        """,
    )
    conf.set_defaults(workflow="mech-conf")
    conf.add_argument("--input", "-i", required=True, help="SMILES string or XYZ file path")
    conf.add_argument("--output", "-o", default="./mech_conf_out", help="Output directory")
    conf.add_argument(
        "--mode",
        default="censo-lite",
        choices=["censo-lite", "xtb-fast"],
        help="Ensemble provider mode (default: censo-lite)",
    )
    conf.add_argument("--charge", type=int, help="Molecular charge (default: 0)")
    conf.add_argument("--multiplicity", type=int, help="Spin multiplicity (default: 1)")
    conf.add_argument("--name", type=str, help="Molecule name")
    conf.add_argument("--label", type=str, help="Module label recorded in the manifest")
    conf.add_argument("--config", type=str, help="Configuration YAML file")
    conf.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    step = run_sub.add_parser(
        "mech-step",
        aliases=["mech-sr"],
        help="Mechanism elementary step: path -> refine -> IRC -> endpoints (module M2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run mech-step --source reactant.xyz --target product.xyz \\
      --plan '{"coordinates":[{"id":"rc1","kind":"distance","atoms":[0,1],"start":3.0,"end":1.5}],"points":21}' \\
      --strategy rph-reverse --fidelity s3 --output ./step_out
  acp run mech-sr --source reactant.xyz --plan plan.json --strategy guided-scan
        """,
    )
    step.set_defaults(workflow="mech-step")
    step.add_argument("--source", required=True, help="Source-state XYZ file")
    step.add_argument("--target", help="Target-state XYZ file (required by rph-reverse)")
    step.add_argument(
        "--plan",
        required=True,
        help="Coordinate plan as a JSON string or path to a JSON file",
    )
    step.add_argument(
        "--strategy",
        default="rph-reverse",
        choices=["guided-scan", "rph-reverse"],
        help="Path-search strategy (default: rph-reverse)",
    )
    step.add_argument(
        "--fidelity",
        default="s3",
        choices=["s3", "s4"],
        help="Refinement fidelity (default: s3)",
    )
    step.add_argument(
        "--endpoint-method",
        default="irc",
        choices=["irc"],
        help="Endpoint resolution method (default: irc)",
    )
    step.add_argument("--output", "-o", default="./mech_step_out", help="Output directory")
    step.add_argument("--charge", type=int, default=0, help="Molecular charge (default: 0)")
    step.add_argument(
        "--multiplicity", type=int, default=1, help="Spin multiplicity (default: 1)"
    )
    step.add_argument("--label", type=str, help="Module label recorded in the manifest")
    step.add_argument("--config", type=str, help="Configuration YAML file")
    step.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    confirm = run_sub.add_parser(
        "mech-confirm",
        help="High-fidelity confirmation of one mech-step artifact (module M3)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run mech-confirm --from ./step_out/elementary_step_manifest.json --select ts:canonical
  acp run mech-confirm --from ./step_out/elementary_step_manifest.json --select endpoint:sink
        """,
    )
    confirm.set_defaults(workflow="mech-confirm")
    confirm.add_argument(
        "--from",
        dest="step_manifest",
        required=True,
        help="Path to an elementary_step_manifest.json produced by mech-step",
    )
    confirm.add_argument(
        "--select",
        default="ts:canonical",
        choices=["ts:canonical", "endpoint:sink"],
        help="Which artifact to confirm (default: ts:canonical)",
    )
    confirm.add_argument(
        "--fidelity",
        default="s4",
        choices=["s3", "s4"],
        help="Confirmation fidelity (default: s4)",
    )
    confirm.add_argument("--output", "-o", default="./mech_confirm_out", help="Output directory")
    confirm.add_argument("--label", type=str, help="Module label recorded in the manifest")
    confirm.add_argument("--config", type=str, help="Configuration YAML file")
    confirm.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )

    chain = run_sub.add_parser(
        "mech-chain",
        help="Run a declarative chain of mechanism modules (module M4)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run mech-chain --config chain.yaml
        """,
    )
    chain.set_defaults(workflow="mech-chain")
    chain.add_argument("--config", required=True, help="Chain definition YAML file")
    chain.add_argument(
        "--output",
        "-o",
        default="./mech_chain_out",
        help="Base output directory for steps that omit output_dir",
    )
    chain.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )


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

    mechanism_tools = subparsers.add_parser("mechanism", help="Mechanism-study utilities")
    mechanism_tools_sub = mechanism_tools.add_subparsers(dest="mechanism_command", required=True)
    mechanism_resume = mechanism_tools_sub.add_parser(
        "resume",
        help="Resume a persisted mechanism study",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp mechanism resume --study study_001
  acp mechanism resume --study study_001 --study-root ./mechanism_output
  acp mechanism resume --study study_001 --decision decision_001=continue
        """,
    )
    mechanism_resume.add_argument(
        "--study",
        required=True,
        help="Study identifier under <study-root>/mechanism_study/<study>",
    )
    mechanism_resume.add_argument(
        "--study-root",
        default="./mechanism_output",
        help="Mechanism-study root directory (default: ./mechanism_output)",
    )
    mechanism_resume.add_argument(
        "--decision",
        action="append",
        default=[],
        metavar="ID=RESOLUTION",
        help="Decision resolution (repeatable; resolution may be plain text or JSON)",
    )
    mechanism_resume.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
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
        help="Memory limit, e.g. 32GB, 4096MB (overrides config)",
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
        help="Memory limit, e.g. 32GB, 4096MB (overrides config)",
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
        "--ewin",
        type=float,
        help="CREST energy window in kcal/mol (default: 6.0)",
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
    nmr.add_argument("--mem", type=str, help="Memory limit (e.g. 32GB)")
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
        help="Memory limit, e.g. 32GB, 4096MB (overrides config)",
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

    # -- simple workflows (singlepoint / optimize / frequency / optfreq / optfreqsp) --
    _add_simple_workflow_parsers(run_sub)

    # -- run mechanism -------------------------------------------------------
    mechanism = run_sub.add_parser(
        "mechanism",
        help="Mechanism study (S0→S4 reaction-network exploration)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run mechanism --input "CCO" --output ./mechanism_results
  acp run mechanism --input reaction.xyz --name substrate
  acp run mechanism --input "C=C" --product "CC" --preset rph-s3 --output ./mech_out
  acp run mechanism --input "C=C" --strategy guided-scan --fidelity s3 \\
      --routes '[{"route_id":"r1","coordinate_plan":{"coordinates":[{"id":"rc1","kind":"distance","atoms":[0,1],"start":3.2,"end":1.55}],"points":21},"path_strategy":"guided-scan","fidelity":"s3"}]'
        """,
    )
    mechanism.add_argument(
        "--input",
        type=str,
        help=(
            "Reactant SMILES string or input file path (XYZ, GJF, LOG, OUT); "
            "optional when --mechanism-config provides roles.reactant.path"
        ),
    )
    mechanism.add_argument(
        "--mechanism-config",
        type=str,
        help="Path to a scheduler-generated mechanism_config.json handoff file",
    )
    mechanism.add_argument(
        "--product",
        type=str,
        help="Product SMILES string or file path (optional for guided-scan)",
    )
    mechanism.add_argument(
        "--ts-guess",
        type=str,
        help="TS-guess SMILES string or file path (used by direct-ts strategy)",
    )
    mechanism.add_argument(
        "--preset",
        type=str,
        choices=_mechanism_preset_ids(),
        help="RPH fidelity preset from the mechanism catalog (rph-s3 = B97-3c → "
        "r2SCAN-3c, rph-s4 = M062X → wB97M-V)",
    )
    mechanism.add_argument(
        "--strategy",
        type=str,
        choices=["guided-scan", "rph-reverse", "direct-ts"],
        help="Path-search strategy (default: guided-scan)",
    )
    mechanism.add_argument(
        "--fidelity",
        type=str,
        choices=["s3", "s4"],
        help="Refinement fidelity (default: s3; rph-s3/rph-s4 presets set this)",
    )
    mechanism.add_argument(
        "--routes",
        type=str,
        help="JSON string: list of route dicts (coordinate plans + strategy + fidelity)",
    )
    mechanism.add_argument(
        "--scan-points",
        type=int,
        help="Override relaxed-scan frame count (range: 5-100; default profile value: 21)",
    )
    mechanism.add_argument(
        "--irc-points",
        type=int,
        help="Override IRC MaxIter point count (range: 5-200; default profile value: 30)",
    )
    mechanism.add_argument(
        "--study-id",
        type=str,
        help="Mechanism-study identifier (checkpoint/resume id; auto-generated when omitted)",
    )
    mechanism.add_argument(
        "--conformer-mode",
        type=str,
        default=None,
        choices=["auto", "censo-lite", "xtb-fast"],
        help="Stable-state ensemble mode for study orchestration (default: auto)",
    )
    mechanism.add_argument(
        "--max-elementary-steps",
        type=int,
        default=None,
        help="Maximum elementary steps to confirm in study mode (default: 3)",
    )
    mechanism.add_argument(
        "--int-extension",
        action="store_true",
        default=False,
        help="Allow recursive intermediate extension in study mode",
    )
    mechanism.add_argument(
        "--promotion-policy",
        type=str,
        default=None,
        choices=["all_confirmed", "rate_relevant", "user_selected"],
        help="Study promotion policy for downstream confirmation (default: all_confirmed)",
    )
    mechanism.add_argument(
        "--auto-converge",
        action="store_true",
        default=False,
        help="Automatically resolve waiting review decisions with the default policy",
    )
    mechanism.add_argument(
        "--output",
        type=str,
        default="./mechanism_output",
        help="Output directory (default: ./mechanism_output)",
    )
    mechanism.add_argument(
        "--config",
        type=str,
        help="Configuration YAML file",
    )
    mechanism.add_argument(
        "--nproc",
        type=int,
        help="Number of CPU cores (overrides config)",
    )
    mechanism.add_argument(
        "--mem",
        type=str,
        help="Memory limit, e.g. 32GB, 4096MB (overrides config)",
    )
    mechanism.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    mechanism.add_argument(
        "--name",
        type=str,
        help="Molecule name (auto-generated if not specified)",
    )
    mechanism.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    mechanism.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )

    _add_mechanism_module_parsers(run_sub)

    # -- run serve (FastAPI server) -----------------------------------------
    serve_parser = run_sub.add_parser(
        "serve",
        help="Start the ACP web dashboard (FastAPI + uvicorn)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run serve
  acp run serve --host 127.0.0.1 --port 8765
  acp run serve --run-root ./ACP_runs --reload
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
        default="./ACP_runs",
        help="Root directory for job work directories and the SQLite index (default: ./ACP_runs)",
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

    return parser


# ---------------------------------------------------------------------------
# Workflow handlers
# ---------------------------------------------------------------------------


def _mechanism_preset_ids() -> list[str]:
    from acp.mechanism.presets import mechanism_profile_ids

    return list(mechanism_profile_ids())


def _load_mechanism_config(path_value: str | None) -> tuple[dict[str, Any] | None, Path | None]:
    if not path_value:
        return None, None

    config_path = Path(path_value)
    if not config_path.exists():
        raise FileNotFoundError(f"Mechanism config file not found: {config_path}")

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read mechanism config file: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"Mechanism config file is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("Mechanism config file must be a JSON object")
    return payload, config_path


def _mechanism_config_dict(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _mechanism_config_levels(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    method = _mechanism_config_dict(payload, "method")
    levels = method.get("levels")
    return dict(levels) if isinstance(levels, dict) else None


def _mechanism_role_config(payload: dict[str, Any] | None, role: str) -> dict[str, Any]:
    roles = _mechanism_config_dict(payload, "roles")
    role_value = roles.get(role)
    return dict(role_value) if isinstance(role_value, dict) else {}


def _mechanism_config_scalar(section: dict[str, Any], key: str) -> Any:
    value = section.get(key)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _mechanism_config_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mechanism_role_path(role_cfg: dict[str, Any], config_path: Path | None) -> str | None:
    raw_path = _mechanism_config_scalar(role_cfg, "path")
    if raw_path is None:
        return None
    path = Path(str(raw_path))
    if not path.is_absolute() and config_path is not None:
        path = config_path.parent / path
    return str(path)


def _handle_mechanism(args: argparse.Namespace) -> int:
    """Execute the mechanism-study workflow."""
    setup_logging(args.log_level)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        try:
            mechanism_config, mechanism_config_path = _load_mechanism_config(
                getattr(args, "mechanism_config", None)
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.error("%s", exc)
            return 1

        cfg = _build_config(args)
        from acp.mechanism.study_runner import (
            read_review_handoff,
            resume_mechanism_study,
            run_mechanism_study,
            waiting_study_exists,
            write_review_payload,
        )
        from acp.scheduler.jobs import EXIT_WAITING_REVIEW

        config_resolved = _mechanism_config_dict(mechanism_config, "resolved")
        config_levels = _mechanism_config_levels(mechanism_config)
        reactant_cfg = _mechanism_role_config(mechanism_config, "reactant")
        product_cfg = _mechanism_role_config(mechanism_config, "product")
        ts_guess_cfg = _mechanism_role_config(mechanism_config, "ts_guess")
        config_resources = _mechanism_config_dict(mechanism_config, "resources")

        if getattr(args, "nproc", None) is None:
            config_nproc = _mechanism_config_int(config_resources.get("nproc"))
            if config_nproc is not None:
                cfg.setdefault("resources", {})["nproc"] = config_nproc
                cfg.setdefault("executables", {}).setdefault("orca", {})["nproc"] = config_nproc
        if getattr(args, "mem", None) is None:
            config_mem = _mechanism_config_scalar(config_resources, "mem")
            if config_mem is not None:
                cfg.setdefault("resources", {})["mem"] = str(config_mem)

        routes: list[dict[str, object]] | None = None
        if getattr(args, "routes", None):
            try:
                parsed_routes = json.loads(args.routes)
                if isinstance(parsed_routes, list):
                    routes = [dict(r) for r in parsed_routes]
            except (TypeError, ValueError) as exc:
                logger.error("Invalid --routes JSON: %s", exc)
                return 1

        preset = getattr(args, "preset", None) or _mechanism_config_scalar(config_resolved, "preset")
        strategy = getattr(args, "strategy", None)
        fidelity = getattr(args, "fidelity", None)
        input_source = getattr(args, "input", None) or _mechanism_role_path(
            reactant_cfg,
            mechanism_config_path,
        )
        if not input_source:
            logger.error(
                "Mechanism study requires --input or a mechanism config with roles.reactant.path"
            )
            return 1
        product_source = getattr(args, "product", None) or _mechanism_role_path(
            product_cfg,
            mechanism_config_path,
        )
        ts_guess_source = getattr(args, "ts_guess", None) or _mechanism_role_path(
            ts_guess_cfg,
            mechanism_config_path,
        )
        charge = (
            getattr(args, "charge", None)
            if getattr(args, "charge", None) is not None
            else _mechanism_config_int(reactant_cfg.get("charge"))
        )
        multiplicity = (
            getattr(args, "multiplicity", None)
            if getattr(args, "multiplicity", None) is not None
            else _mechanism_config_int(reactant_cfg.get("multiplicity"))
        )

        # Scheduler restart handoff: the JobManager mirrors job state to
        # <output>/job.json. When a study paused at a review gate, reuse
        # the persisted study id + review resolutions instead of starting
        # a fresh study (the derived id is timestamped and not stable).
        study_id = getattr(args, "study_id", None) or _mechanism_config_scalar(
            config_resolved,
            "study_id",
        )
        handed_off_study_id, review_decisions = read_review_handoff(output_dir)
        if not study_id and handed_off_study_id:
            study_id = handed_off_study_id

        if study_id and waiting_study_exists(output_dir, study_id):
            summary = resume_mechanism_study(
                study_id=study_id,
                study_root=output_dir,
                decision_resolutions=review_decisions,
            )
        else:
            summary = run_mechanism_study(
                input_source=input_source,
                output_dir=output_dir,
                config=cfg,
                name=args.name,
                charge=charge,
                multiplicity=multiplicity,
                product_source=product_source,
                ts_guess_source=ts_guess_source,
                routes=routes,
                preset=preset,
                strategy=strategy,
                fidelity=fidelity,
                scan_points=getattr(args, "scan_points", None),
                irc_points=getattr(args, "irc_points", None),
                study_id=study_id,
                conformer_mode=getattr(args, "conformer_mode", None),
                max_elementary_steps=getattr(args, "max_elementary_steps", None),
                int_extension=bool(getattr(args, "int_extension", False)),
                promotion_policy=getattr(args, "promotion_policy", None),
                auto_converge=bool(getattr(args, "auto_converge", False)),
                config_resolved=config_resolved,
                method_levels=config_levels,
                mechanism_config_path=mechanism_config_path,
            )
        status = str(summary.get("status") or "unknown")
        logger.info("Mechanism study %s", status)
        logger.info("  Study ID            : %s", summary.get("study_id", "N/A"))
        logger.info("  Study dir           : %s", summary.get("study_dir", "N/A"))
        logger.info("  Network size        : %s", summary.get("network_size", {}))
        logger.info("  Gates               : %s", summary.get("gates_summary", {}))
        pending = summary.get("pending_decisions", [])
        if pending:
            logger.info("  Pending decisions   : %s", pending)
        if status == "waiting":
            write_review_payload(output_dir, summary)
            return EXIT_WAITING_REVIEW
        return 0 if status in {"completed", "waiting", "running"} else 1
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1


def _parse_decision_resolutions(values: list[str]) -> dict[str, Any]:
    resolutions: dict[str, Any] = {}
    for raw in values:
        decision_id, separator, payload = raw.partition("=")
        decision_key = decision_id.strip()
        if not separator or not decision_key:
            raise ValueError(f"Invalid --decision value: {raw!r}; expected ID=RESOLUTION")
        payload_text = payload.strip()
        try:
            resolutions[decision_key] = json.loads(payload_text)
        except json.JSONDecodeError:
            resolutions[decision_key] = payload_text
    return resolutions


def _handle_mechanism_resume(args: argparse.Namespace) -> int:
    """Resume a persisted mechanism study."""
    setup_logging(args.log_level)
    try:
        from acp.mechanism.study_runner import resume_mechanism_study

        resolutions = _parse_decision_resolutions(list(getattr(args, "decision", []) or []))
        summary = resume_mechanism_study(
            study_id=args.study,
            study_root=args.study_root,
            decision_resolutions=resolutions,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    status = str(summary.get("status") or "unknown")
    logger.info("Mechanism study %s", status)
    logger.info("  Study ID            : %s", summary.get("study_id", "N/A"))
    logger.info("  Study dir           : %s", summary.get("study_dir", "N/A"))
    logger.info("  Network size        : %s", summary.get("network_size", {}))
    logger.info("  Gates               : %s", summary.get("gates_summary", {}))
    pending = summary.get("pending_decisions", [])
    if pending:
        logger.info("  Pending decisions   : %s", pending)
    if status == "waiting":
        from acp.mechanism.study_runner import write_review_payload
        from acp.scheduler.jobs import EXIT_WAITING_REVIEW

        write_review_payload(Path(args.study_root), summary)
        return EXIT_WAITING_REVIEW
    return 0 if status in {"completed", "waiting", "running"} else 1


def _module_exit_code(status: str) -> int:
    if status in {"validated", "partial"}:
        if status == "partial":
            logger.warning("Module finished with status=partial (recoverable evidence kept)")
        return 0
    return 1


def _handle_mech_conf(args: argparse.Namespace) -> int:
    from acp.mechanism.modules.module_conformer import run_conformer_module

    setup_logging(args.log_level)
    cfg = _build_config(args)
    try:
        manifest = run_conformer_module(
            args.input,
            output_dir=Path(args.output),
            mode=args.mode,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            config=cfg,
            label=args.label,
        )
    except KeyboardInterrupt:
        logger.warning("mech-conf interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("mech-conf failed: %s", exc)
        return 1
    logger.info("mech-conf status=%s output=%s", manifest.status, args.output)
    return _module_exit_code(manifest.status)


def _handle_mech_step(args: argparse.Namespace) -> int:
    from acp.mechanism.modules.module_step import run_step_module

    setup_logging(args.log_level)
    cfg = _build_config(args)
    try:
        plan = _parse_levels(args.plan)
        if plan is None:
            logger.error("mech-step requires a coordinate plan via --plan")
            return 1
        manifest = run_step_module(
            args.source,
            plan,
            target_xyz=getattr(args, "target", None),
            strategy=args.strategy,
            fidelity=args.fidelity,
            endpoint_method=args.endpoint_method,
            output_dir=Path(args.output),
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            label=args.label,
        )
    except KeyboardInterrupt:
        logger.warning("mech-step interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("mech-step failed: %s", exc)
        return 1
    logger.info("mech-step status=%s gates=%s", manifest.status, manifest.gates)
    return _module_exit_code(manifest.status)


def _handle_mech_confirm(args: argparse.Namespace) -> int:
    from acp.mechanism.modules.module_confirm import run_confirm_module

    setup_logging(args.log_level)
    cfg = _build_config(args)
    try:
        manifest = run_confirm_module(
            args.step_manifest,
            select=args.select,
            fidelity=args.fidelity,
            output_dir=Path(args.output),
            config=cfg,
            label=args.label,
        )
    except KeyboardInterrupt:
        logger.warning("mech-confirm interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("mech-confirm failed: %s", exc)
        return 1
    logger.info("mech-confirm status=%s output=%s", manifest.status, args.output)
    return _module_exit_code(manifest.status)


def _handle_mech_chain(args: argparse.Namespace) -> int:
    from acp.mechanism.chain import run_chain

    setup_logging(args.log_level)
    try:
        chain_config_path = Path(args.config)
        import yaml

        raw = yaml.safe_load(chain_config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            logger.error("Chain config must be a mapping: %s", chain_config_path)
            return 1
        base_output = Path(args.output)
        for step in raw.get("steps") or []:
            step_args = step.get("args") if isinstance(step, dict) else None
            if isinstance(step_args, dict) and "output_dir" not in step_args:
                module = str(step.get("module") or "step")
                step_args["output_dir"] = str(base_output / module)
        records = run_chain(raw)
    except KeyboardInterrupt:
        logger.warning("mech-chain interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("mech-chain failed: %s", exc)
        return 1
    for record in records:
        logger.info("  %s -> %s (%s)", record["module"], record["status"], record["manifest_path"])
    failed = [r for r in records if r["status"] not in {"validated", "partial"}]
    return 1 if failed else 0


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
    return kwargs


def _handle_singlepoint(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_singlepoint

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    try:
        result = run_singlepoint(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
        )
    except KeyboardInterrupt:
        logger.warning("Singlepoint interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Singlepoint failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("Single-point calculation completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        return 0
    logger.error("Single-point calculation failed: %s", result.error)
    return 1


def _handle_optimize(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_optimize

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    try:
        result = run_optimize(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
        )
    except KeyboardInterrupt:
        logger.warning("Optimization interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Optimization failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("Geometry optimization completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        return 0
    logger.error("Optimization failed: %s", result.error)
    return 1


def _handle_frequency(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_frequency

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    try:
        result = run_frequency(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
        )
    except KeyboardInterrupt:
        logger.warning("Frequency calculation interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Frequency calculation failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("Frequency calculation completed")
        logger.info("  Modes: %s", result.metadata.get("n_frequencies", "N/A"))
        return 0
    logger.error("Frequency calculation failed: %s", result.error)
    return 1


def _handle_optfreq(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_optfreq

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_simple_method_kwargs(args)
    try:
        result = run_optfreq(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
        )
    except KeyboardInterrupt:
        logger.warning("Opt+Freq interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Opt+Freq failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("Opt+Freq calculation completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        logger.info("  Modes: %s", result.metadata.get("n_frequencies", "N/A"))
        return 0
    logger.error("Opt+Freq failed: %s", result.error)
    return 1


def _handle_optfreqsp(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_optfreqsp

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)

    optfreq_kwargs = _build_simple_method_kwargs(args)
    sp_kwargs: dict[str, Any] = {
        "method": args.sp_method,
        "basis": args.sp_basis,
        "ri_approximation": args.sp_ri_approximation,
    }
    sp_aux_j = getattr(args, "sp_aux_j_basis", None)
    sp_aux_legacy = getattr(args, "sp_aux_basis", None)
    if sp_aux_legacy and sp_aux_j in (None, "AutoAux"):
        sp_aux_j = sp_aux_legacy
    if sp_aux_j and sp_aux_j != "AutoAux":
        sp_kwargs["aux_j_basis"] = sp_aux_j
    sp_aux_c = getattr(args, "sp_aux_c_basis", None)
    if sp_aux_c and sp_aux_c != "AutoAux":
        sp_kwargs["aux_c_basis"] = sp_aux_c
    if args.sp_dispersion and args.sp_dispersion != "none":
        sp_kwargs["dispersion"] = args.sp_dispersion
    sp_solvent = args.sp_solvent or args.solvent
    sp_solvent_model = args.sp_solvent_model or args.solvent_model
    if sp_solvent_model and sp_solvent_model != "none":
        sp_kwargs["solvent_model"] = sp_solvent_model
    if sp_solvent:
        sp_kwargs["solvent"] = sp_solvent
    sp_extras = getattr(args, "route_extras", None)
    if sp_extras:
        sp_kwargs["route_extras"] = [x.strip() for x in sp_extras.split(",") if x.strip()]
    thermo_kwargs: dict[str, Any] = {
        "temperature": args.temperature,
        "pressure": args.pressure,
        "scale_factor": args.scale_factor,
    }
    try:
        result = run_optfreqsp(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            optfreq_kwargs=optfreq_kwargs,
            sp_kwargs=sp_kwargs,
            thermo_kwargs=thermo_kwargs,
        )
    except KeyboardInterrupt:
        logger.warning("Opt+Freq+SP+Thermo interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Opt+Freq+SP+Thermo failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("Opt+Freq+SP+Thermo completed")
        logger.info("  SP energy: %s Hartree", result.metadata.get("sp_energy", "N/A"))
        logger.info("  Free energy: %s Hartree", result.metadata.get("free_energy_hartree", "N/A"))
        return 0
    logger.error("Opt+Freq+SP+Thermo failed: %s", result.error)
    return 1


def _build_xtb_method_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in ("gfn", "opt_level", "solvent_model", "solvent", "max_steps"):
        val = getattr(args, key, None)
        if val is not None:
            kwargs[key] = val
    return kwargs


def _handle_xtb_optimize(args: argparse.Namespace) -> int:
    from acp.workflows.simple import run_xtb_optimize

    setup_logging(args.log_level)
    cfg = _build_config(args)
    out = Path(args.output)
    method_kwargs = _build_xtb_method_kwargs(args)
    try:
        result = run_xtb_optimize(
            input_source=args.input,
            output_dir=out,
            config=cfg,
            charge=args.charge,
            multiplicity=args.multiplicity,
            name=args.name,
            method_kwargs=method_kwargs,
        )
    except KeyboardInterrupt:
        logger.warning("xTB optimization interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("xTB optimization failed: %s", exc)
        return 1
    if result.status == "completed":
        logger.info("xTB geometry optimization completed")
        logger.info("  Energy: %s Hartree", result.metadata.get("energy", "N/A"))
        return 0
    logger.error("xTB optimization failed: %s", result.error)
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
    run_root = getattr(args, "run_root", "./ACP_runs")
    max_running = getattr(args, "max_running", 1)
    poll_interval = getattr(args, "poll_interval", 15)
    log_level = getattr(args, "log_level", "INFO").lower()
    no_browser = getattr(args, "no_browser", False)

    run_root_path = Path(run_root).resolve()
    run_root_path.mkdir(parents=True, exist_ok=True)

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


def _handle_ensemble(args: argparse.Namespace) -> int:
    """Execute the ensemble generation workflow."""
    setup_logging(args.log_level)

    if not args.input and not args.batch_file:
        print("Error: --input or --batch-file is required", file=sys.stderr)
        return 1

    cfg = _build_config(args)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.save_config:
        from cccp.config import save_config as save_cfg

        save_cfg(cfg, Path(args.save_config))
        logger.info("Configuration saved to: %s", args.save_config)

    if args.batch_file:
        return _handle_ensemble_batch(args, cfg, output_dir)

    try:
        from acp.workflows.ensemble import run_ensemble_generation

        result = run_ensemble_generation(
            input_source=args.input,
            output_dir=str(output_dir),
            preset=args.preset,
            config=cfg,
            name=args.name,
            charge=args.charge,
            multiplicity=args.multiplicity,
            solvent=args.solvent,
            nproc=args.nproc,
            keep_all=True if getattr(args, "keep_all", False) else None,
            ewin=getattr(args, "ewin", None),
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    if result.status == "completed":
        meta = result.metadata or {}
        logger.info("Ensemble generation completed successfully")
        logger.info("  Conformers         : %s", meta.get("n_conformers", "N/A"))
        logger.info("  Ensemble XYZ       : %s", meta.get("ensemble_xyz", "N/A"))
        logger.info("  Ensemble JSON      : %s", meta.get("ensemble_json", "N/A"))
        return 0

    logger.error("Ensemble generation failed: %s", result.error)
    return 1


def _handle_ensemble_batch(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    output_dir: Path,
) -> int:
    """Run ensemble generation for multiple molecules (batch mode)."""
    from acp.workflows.ensemble import run_ensemble_generation
    from cccp.io import load_batch_inputs

    logger.info("ACP ensemble workflow — batch mode")
    batch_file = Path(args.batch_file)
    inputs = load_batch_inputs(batch_file)

    logger.info("Found %d molecules to process", len(inputs))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i, mi in enumerate(inputs, start=1):
        source = str(mi.source_path or mi.metadata.get("smiles", ""))
        logger.info("[%d/%d] Processing %s", i, len(inputs), mi.name)
        try:
            r = run_ensemble_generation(
                input_source=source,
                output_dir=str(output_dir),
                preset=args.preset,
                config=cfg,
                name=mi.name,
                charge=getattr(args, "charge", None),
                multiplicity=getattr(args, "multiplicity", None),
                solvent=args.solvent,
                nproc=args.nproc,
                keep_all=True if getattr(args, "keep_all", False) else None,
                ewin=getattr(args, "ewin", None),
            )
            if r.status == "completed":
                results.append({"molecule": mi.name, "status": "completed", "metadata": r.metadata})
            else:
                errors.append({"molecule": mi.name, "error": str(r.error)})
        except Exception as exc:
            logger.error("  Failed: %s", exc)
            errors.append({"molecule": mi.name, "error": str(exc)})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = output_dir / f"batch_summary_{timestamp}.json"
    summary = {
        "timestamp": timestamp,
        "total": len(inputs),
        "successful": len(results),
        "failed": len(errors),
        "preset": args.preset,
        "results": results,
        "errors": errors,
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    logger.info(
        "Batch complete: %d/%d successful — summary saved to %s",
        len(results),
        len(inputs),
        summary_path,
    )
    return 0 if not errors else 1


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


def _handle_energy(args: argparse.Namespace) -> int:
    """Execute the conformer energy workflow."""
    setup_logging(args.log_level)

    if not args.input and not args.batch_file:
        print("Error: --input or --batch-file is required", file=sys.stderr)
        return 1

    levels = _parse_levels_json(args.levels)
    if args.levels and levels is None:
        return 1

    if levels is None:
        levels = {}
    if args.threshold != 0.99:
        levels["refinement_threshold"] = args.threshold
    else:
        levels.setdefault("refinement_threshold", 0.99)

    cfg = _build_config(args)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.save_config:
        from cccp.config import save_config as save_cfg

        save_cfg(cfg, Path(args.save_config))
        logger.info("Configuration saved to: %s", args.save_config)

    if args.batch_file:
        return _handle_energy_batch(args, cfg, output_dir, levels)

    try:
        from acp.workflows.energy import run_conformer_energy

        result = run_conformer_energy(
            input_source=args.input,
            output_dir=str(output_dir),
            preset=args.preset,
            config=cfg,
            name=args.name,
            charge=args.charge,
            multiplicity=args.multiplicity,
            solvent=args.solvent,
            nproc=args.nproc,
            no_opt=args.no_opt,
            rank1_only=not args.full_ensemble,
            levels=levels,
            ewin=getattr(args, "ewin", None),
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    if result.status == "completed":
        meta = result.metadata or {}
        logger.info("Conformer energy workflow completed successfully")
        logger.info("  Conformers         : %s", meta.get("n_conformers", "N/A"))
        logger.info("  Global minimum     : %s", meta.get("global_min_xyz", "N/A"))
        logger.info("  Thermo CSV         : %s", meta.get("thermo_csv", "N/A"))
        logger.info(
            "  Total G(ensemble)  : %s kcal/mol",
            meta.get("total_gibbs_kcal_mol", "N/A"),
        )
        return 0

    logger.error("Conformer energy workflow failed: %s", result.error)
    return 1


def _handle_energy_batch(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    output_dir: Path,
    levels: dict[str, Any] | None,
) -> int:
    """Run the conformer energy workflow for multiple molecules (batch mode)."""
    from acp.workflows.energy import run_conformer_energy
    from cccp.io import load_batch_inputs

    logger.info("ACP energy workflow — batch mode")
    batch_file = Path(args.batch_file)
    inputs = load_batch_inputs(batch_file)

    logger.info("Found %d molecules to process", len(inputs))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i, mi in enumerate(inputs, start=1):
        source = str(mi.source_path or mi.metadata.get("smiles", ""))
        logger.info("[%d/%d] Processing %s", i, len(inputs), mi.name)
        try:
            r = run_conformer_energy(
                input_source=source,
                output_dir=str(output_dir),
                preset=args.preset,
                config=cfg,
                name=mi.name,
                charge=getattr(args, "charge", None),
                multiplicity=getattr(args, "multiplicity", None),
                solvent=args.solvent,
                nproc=args.nproc,
                no_opt=args.no_opt,
                rank1_only=not args.full_ensemble,
                levels=levels,
                ewin=getattr(args, "ewin", None),
            )
            if r.status == "completed":
                results.append({"molecule": mi.name, "status": "completed", "metadata": r.metadata})
            else:
                errors.append({"molecule": mi.name, "error": str(r.error)})
        except Exception as exc:
            logger.error("  Failed: %s", exc)
            errors.append({"molecule": mi.name, "error": str(exc)})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = output_dir / f"batch_summary_{timestamp}.json"
    summary = {
        "timestamp": timestamp,
        "total": len(inputs),
        "successful": len(results),
        "failed": len(errors),
        "preset": args.preset,
        "results": results,
        "errors": errors,
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    logger.info(
        "Batch complete: %d/%d successful — summary saved to %s",
        len(results),
        len(inputs),
        summary_path,
    )
    return 0 if not errors else 1


def _handle_xtbmd_censo_energy(args: argparse.Namespace) -> int:
    """Execute the xTB-MD conformer-search free-energy workflow."""
    setup_logging(args.log_level)

    if not args.input and not args.batch_file:
        print("Error: --input or --batch-file is required", file=sys.stderr)
        return 1

    levels = _parse_levels_json(args.levels)
    if args.levels and levels is None:
        return 1
    if levels is None:
        levels = {}
    if args.threshold != 0.99:
        levels["refinement_threshold"] = args.threshold
    else:
        # Match energy handler semantics: an explicit --levels
        # refinement_threshold survives; only the default is backfilled.
        levels.setdefault("refinement_threshold", 0.99)

    cfg = _build_config(args)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.save_config:
        from cccp.config import save_config as save_cfg

        save_cfg(cfg, Path(args.save_config))
        logger.info("Configuration saved to: %s", args.save_config)

    if args.batch_file:
        return _handle_xtbmd_censo_energy_batch(args, cfg, output_dir, levels)

    try:
        from acp.workflows.xtbmd_censo_energy import run_xtbmd_censo_energy

        result = run_xtbmd_censo_energy(
            input_source=args.input,
            output_dir=str(output_dir),
            preset=args.preset,
            config=cfg,
            name=args.name,
            charge=args.charge,
            multiplicity=args.multiplicity,
            solvent=args.solvent,
            nproc=args.nproc,
            no_opt=args.no_opt,
            levels=levels,
            threshold=None,  # refinement_threshold already merged into levels
            ewin=args.ewin,
            rank1_only=args.rank1_only,
            resume=args.resume,
            md_temperature=args.md_temp,
            md_time_ps=args.md_time,
            md_dump_fs=args.md_dump,
            md_step_fs=args.md_step,
            md_hmass=args.md_hmass,
            md_shake=not args.md_no_shake,
            md_nvt=args.md_nvt,
            md_seed=args.md_seed,
            md_seeds=args.md_seeds,
            md_method=args.md_method,
            md_timeout=getattr(args, "md_timeout", None),
            conv_check=args.conv_check,
            conv_novelty_max=args.conv_novelty_max,
            conv_rmsd=args.conv_rmsd,
            max_frames=args.max_frames,
            opt_gfn_level=args.opt_gfn,
            opt_level=args.opt_level,
            opt_timeout=args.opt_timeout,
            keep_frames=args.keep_frames,
            edis=args.edis,
            gdis=args.gdis,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    if result.status == "completed":
        meta = result.metadata or {}
        logger.info("xTB-MD CENSO energy workflow completed successfully")
        logger.info("  Conformers         : %s", meta.get("n_conformers", "N/A"))
        logger.info("  Global minimum     : %s", meta.get("global_min_xyz", "N/A"))
        logger.info("  Thermo CSV         : %s", meta.get("thermo_csv", "N/A"))
        logger.info(
            "  Frames (raw/kept)  : %s / %s",
            meta.get("n_frames_raw", "N/A"),
            meta.get("n_frames", "N/A"),
        )
        logger.info(
            "  Batch opt ok/fail  : %s / %s",
            meta.get("n_ok", "N/A"),
            meta.get("n_failed", "N/A"),
        )
        logger.info(
            "  Total G(ensemble)  : %s kcal/mol",
            meta.get("total_gibbs_kcal_mol", "N/A"),
        )
        return 0

    logger.error("xTB-MD CENSO energy workflow failed: %s", result.error)
    return 1


def _handle_xtbmd_censo_energy_batch(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    output_dir: Path,
    levels: dict[str, Any] | None,
) -> int:
    """Run the xTB-MD CENSO energy workflow for multiple molecules (batch mode)."""
    from acp.workflows.xtbmd_censo_energy import run_xtbmd_censo_energy
    from cccp.io import load_batch_inputs

    logger.info("ACP xtbmd_censo_energy workflow — batch mode")
    batch_file = Path(args.batch_file)
    inputs = load_batch_inputs(batch_file)

    logger.info("Found %d molecules to process", len(inputs))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i, mi in enumerate(inputs, start=1):
        source = str(mi.source_path or mi.metadata.get("smiles", ""))
        logger.info("[%d/%d] Processing %s", i, len(inputs), mi.name)
        try:
            r = run_xtbmd_censo_energy(
                input_source=source,
                output_dir=str(output_dir),
                preset=args.preset,
                config=cfg,
                name=mi.name,
                charge=getattr(args, "charge", None),
                multiplicity=getattr(args, "multiplicity", None),
                solvent=args.solvent,
                nproc=args.nproc,
                no_opt=args.no_opt,
                levels=levels,
                threshold=None,  # refinement_threshold already merged into levels
                ewin=args.ewin,
                rank1_only=args.rank1_only,
                resume=args.resume,
                md_temperature=args.md_temp,
                md_time_ps=args.md_time,
                md_dump_fs=args.md_dump,
                md_step_fs=args.md_step,
                md_hmass=args.md_hmass,
                md_shake=not args.md_no_shake,
                md_nvt=args.md_nvt,
                md_seed=args.md_seed,
                md_seeds=args.md_seeds,
                md_method=args.md_method,
                md_timeout=getattr(args, "md_timeout", None),
                conv_check=args.conv_check,
                conv_novelty_max=args.conv_novelty_max,
                conv_rmsd=args.conv_rmsd,
                max_frames=args.max_frames,
                opt_gfn_level=args.opt_gfn,
                opt_level=args.opt_level,
                opt_timeout=args.opt_timeout,
                keep_frames=args.keep_frames,
                edis=args.edis,
                gdis=args.gdis,
            )
            if r.status == "completed":
                results.append({"molecule": mi.name, "status": "completed", "metadata": r.metadata})
            else:
                errors.append({"molecule": mi.name, "error": str(r.error)})
        except Exception as exc:
            logger.error("  Failed: %s", exc)
            errors.append({"molecule": mi.name, "error": str(exc)})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_path = output_dir / f"batch_summary_{timestamp}.json"
    summary = {
        "timestamp": timestamp,
        "total": len(inputs),
        "successful": len(results),
        "failed": len(errors),
        "preset": args.preset,
        "results": results,
        "errors": errors,
    }
    summary_path.write_text(json.dumps(summary, indent=2))
    logger.info(
        "Batch complete: %d/%d successful — summary saved to %s",
        len(results),
        len(inputs),
        summary_path,
    )
    return 0 if not errors else 1


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

    try:
        from acp.workflows.nmr import run_nmr_analysis

        result = run_nmr_analysis(
            input_sources=args.input,
            spectrum=args.spectrum,
            output_dir=str(output_dir),
            config=cfg,
            nuclei=nuclei,
            nmr_method=args.nmr_method,
            nmr_basis=args.nmr_basis,
            solvent=args.solvent,
            charge=args.charge,
            multiplicity=args.multiplicity,
            nproc=args.nproc,
            boltzmann_temp=args.boltzmann_temp,
            tms_1h=args.tms_1h,
            tms_13c=args.tms_13c,
            error_model=args.error_model,
            conformer_preset=args.preset,
            ewin=args.ewin,
            enumerate_stereoisomers=bool(args.enumerate),
            stereocenters=args.stereocenters,
            bruker=args.bruker,
            bruker_references=bruker_references,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    if result.status == "completed":
        meta = result.metadata or {}
        logger.info("NMR + DP4/DP5 workflow completed successfully")
        winner = meta.get("winner")
        if winner:
            logger.info(
                "  Winner: candidate %s (%s) — DP4=%.3f, DP5=%.3f",
                winner.get("index"),
                winner.get("label"),
                float(winner.get("dp4", 0.0)),
                float(winner.get("dp5", 0.0)),
            )
        logger.info("  Candidates        : %s", meta.get("n_candidates", "N/A"))
        logger.info("  Report JSON       : %s", meta.get("report_json", "N/A"))
        logger.info("  Report XLSX       : %s", meta.get("report_xlsx", "N/A"))
        logger.info("  Error model       : %s", meta.get("error_model", "N/A"))
        if meta.get("note"):
            logger.warning("  NOTE: %s", meta["note"])
        return 0

    logger.error("NMR workflow failed: %s", result.error)
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
    from cccp.software import ENV_VARS, resolve_executable

    entry = get_workflow_entry(workflow)
    if entry is None or not entry.requires_binaries:
        return
    for name in entry.requires_binaries:
        if resolve_executable(name) is None:
            env_var = ENV_VARS.get(name, f"CONFSEARCH_{name.upper()}_PATH")
            logger.warning(
                "Preflight: executable '%s' (required by '%s') was not found. "
                "Add it to PATH, set %s, or configure executables.%s.path.",
                name,
                workflow,
                env_var,
                name,
            )


def main(argv: list[str] | None = None) -> int:
    """
    ACP CLI entry point.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        dispatch: dict[str, Callable[[argparse.Namespace], int]] = {
            "ensemble": _handle_ensemble,
            "energy": _handle_energy,
            "nmr": _handle_nmr,
            "xtbmd_censo_energy": _handle_xtbmd_censo_energy,
            "mechanism": _handle_mechanism,
            "mech-conf": _handle_mech_conf,
            "mech-step": _handle_mech_step,
            "mech-confirm": _handle_mech_confirm,
            "mech-chain": _handle_mech_chain,
            "serve": _handle_serve,
            "singlepoint": _handle_singlepoint,
            "optimize": _handle_optimize,
            "frequency": _handle_frequency,
            "optfreq": _handle_optfreq,
            "optfreqsp": _handle_optfreqsp,
            "xtb_optimize": _handle_xtb_optimize,
        }
        handler = dispatch.get(args.workflow)
        if handler is None:
            parser.print_help()
            return 1
        _preflight_workflow(args.workflow)
        return handler(args)

    if args.command == "mechanism":
        dispatch = {"resume": _handle_mechanism_resume}
        handler = dispatch.get(args.mechanism_command)
        if handler is None:
            parser.print_help()
            return 1
        return handler(args)

    if args.command != "run":
        parser.print_help()
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
