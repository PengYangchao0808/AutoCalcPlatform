# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnusedCallResult=false, reportUnknownVariableType=false, reportUnknownLambdaType=false, reportImplicitStringConcatenation=false
"""ACP CLI — Unified command-line interface for Auto-Calc Platform.

Subcommands:
    run conformer   Conformer search pipeline
    run nmr         NMR prediction workflow
    run mechanism   Mechanism analysis workflow
    run serve       Start the ACP web dashboard (FastAPI + uvicorn)
    benchmark       Multi-protocol benchmark meta-workflow

Usage:
    acp run conformer --input "CCO" --output ./result
    acp run nmr --help
    acp run mechanism --help
    acp run serve --help
    acp benchmark --input molecule.xyz --output ./benchmark_results
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
from typing import Any, cast

logger = logging.getLogger(__name__)

ALL_PROTOCOLS = [
    "censo-lite",
    "ext",
    "full",
    "lite",
    "zero",
    "benchmark",
]

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
    """Merge optional --config file and --nproc/--mem overrides."""
    config: dict[str, Any] = {}

    if args.config:
        from conformer_search.config import load_config as legacy_load

        config = legacy_load(config_path=Path(args.config))

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


def _parse_nmr_references(values: list[str]) -> dict[str, float] | None:
    """Parse repeated ``--reference NUCLEUS=VALUE`` arguments."""
    if not values:
        return None

    references: dict[str, float] = {}
    for item in values:
        nucleus, separator, raw_value = item.partition("=")
        if separator != "=" or not nucleus.strip() or not raw_value.strip():
            raise ValueError(f"Invalid NMR reference '{item}'. Expected the form NUCLEUS=VALUE.")

        try:
            references[nucleus.strip()] = float(raw_value)
        except ValueError as exc:
            raise ValueError(f"Invalid NMR reference '{item}'. VALUE must be a float.") from exc

    return references


def _apply_nmr_config_overrides(
    config: dict[str, Any],
    args: argparse.Namespace,
    references: dict[str, float] | None,
) -> dict[str, Any]:
    """Project CLI NMR options onto the effective configuration."""
    theory = config.setdefault("theory", {})
    theory_nmr = theory.setdefault("nmr", {})
    nmr_config = config.setdefault("nmr", {})

    if args.backend is not None:
        theory_nmr["engine"] = args.backend
    if args.temperature is not None:
        nmr_config["temperature_k"] = float(args.temperature)
    if args.energy_window_kcal is not None:
        nmr_config["energy_window_kcal"] = float(args.energy_window_kcal)
    if args.max_conformers is not None:
        nmr_config["max_conformers"] = int(args.max_conformers)
    if references is not None:
        merged_references = dict(nmr_config.get("references", {}))
        merged_references.update(references)
        nmr_config["references"] = merged_references

    return config


def _render_protocol_info(spec: Any) -> str:
    """Render a human-readable protocol summary."""
    from acp.workflows.conformer import get_protocol_stages
    from conformer_search.core.protocols import ProtocolSpec

    assert isinstance(spec, ProtocolSpec)

    lines = [
        f"Protocol: {spec.name}",
        f"Two-stage CREST: {spec.two_stage_enabled}",
        f"Ngeom default:   {spec.ngeom_default}",
        f"Opt engine:      {spec.opt_engine}",
        f"SP engine:       {spec.sp_engine}",
        "Stages:",
    ]
    try:
        stages = get_protocol_stages(spec.name)
        for i, stage in enumerate(stages, start=1):
            lines.append(f"  {i}. {stage.name}")
    except Exception:
        lines.append("  (unable to resolve stages)")

    lines.append(f"Final SP: {spec.final_sp_method}/{spec.final_sp_basis}")
    return "\n".join(lines)


def _handle_protocol(args: argparse.Namespace) -> int:
    """Execute protocol introspection subcommands."""
    if args.protocol_action == "list":
        print("\n".join(ALL_PROTOCOLS))
        return 0

    if args.protocol_action == "info":
        try:
            from conformer_search.config import load_config
            from conformer_search.core.protocols import resolve_protocol_spec

            cfg = load_config()
            spec = resolve_protocol_spec(cfg, args.name)
        except KeyError:
            print(f"Unknown protocol: {args.name}", file=sys.stderr)
            return 1
        print(_render_protocol_info(spec))
        return 0


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


def _add_simple_workflow_parsers(run_sub: argparse._SubParsersAction) -> None:
    """Register 5 simple ORCA workflow subcommand parsers + xtb_optimize."""
    for wf, wf_label, wf_desc, wf_epilog in [
        ("singlepoint", "Single Point", "Run ORCA single-point energy calculation",
         "Examples:\n  acp run singlepoint --input mol.xyz --output ./out\n  acp run singlepoint --input mol.inp --method wB97M-V --basis def2-TZVPP"),
        ("optimize", "Optimization", "Run ORCA geometry optimization",
         "Examples:\n  acp run optimize --input mol.xyz --output ./out\n  acp run optimize --input mol.gjf --method r2SCAN-3c --geom-maxiter 200"),
        ("frequency", "Frequency", "Run ORCA vibrational frequency calculation",
         "Examples:\n  acp run frequency --input mol.xyz --output ./out\n  acp run frequency --input mol.inp --method wB97M-V --basis def2-TZVPP"),
        ("optfreq", "Opt + Freq", "Run ORCA Opt+Freq as single job",
         "Examples:\n  acp run optfreq --input mol.xyz --output ./out\n  acp run optfreq --input mol.gjf --method r2SCAN-3c"),
        ("optfreqsp", "Opt+Freq+SP+Thermo", "Full pipeline: opt -> freq -> SP -> Shermo",
         "Examples:\n  acp run optfreqsp --input mol.xyz --output ./out\n  acp run optfreqsp --input mol.xyz --method r2SCAN-3c --sp-method wB97M-V"),
    ]:
        p = run_sub.add_parser(wf, help=wf_desc, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=wf_epilog)
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
    parser.add_argument("--input", "-i", required=True, help="Input structure file (XYZ, GJF, COM, ORCA .inp)")
    parser.add_argument("--output", "-o", default="./out", help="Output directory")
    parser.add_argument("--charge", type=int, help="Molecular charge (auto-detected if not specified)")
    parser.add_argument("--multiplicity", type=int, help="Spin multiplicity (auto-detected if not specified)")
    parser.add_argument("--name", type=str, help="Molecule name")
    parser.add_argument("--method", default="r2SCAN-3c", help="DFT functional (default: r2SCAN-3c)")
    parser.add_argument("--basis", default="", help="Basis set (default: empty, composite method)")
    parser.add_argument("--dispersion", default="none", help="Dispersion correction (e.g. D3BJ, D4, none)")
    parser.add_argument("--solvent-model", default="none", type=str.lower, choices=["smd", "cpcm", "none"], help="Solvent model (default: none)")
    parser.add_argument("--solvent", default="", help="Solvent name (e.g. water, methanol)")
    parser.add_argument("--nproc", type=int, help="Number of CPU cores")
    parser.add_argument("--mem", type=str, help="Memory limit (e.g. 32GB, 4096MB)")
    parser.add_argument("--config", type=str, help="Configuration YAML file")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO", help="Logging level (default: INFO)")

    parser.add_argument("--route-extras", type=str, help="Comma-separated ORCA route extras (e.g. SlowConv,NoFinalGrid)")

    if wf in ("singlepoint", "optimize", "frequency", "optfreq", "optfreqsp"):
        parser.add_argument("--aux-j-basis", default="AutoAux", metavar="BASIS",
            help="Auxiliary /J basis for RI-J fitting (default: AutoAux). "
                 "Common: AutoAux, def2/J.")
        parser.add_argument("--aux-c-basis", default="AutoAux", metavar="BASIS",
            help="Auxiliary /C basis for RI-MP2 correlation (default: AutoAux). "
                 "Common: AutoAux, def2-TZVPP/C, cc-pVTZ/C. Only used by "
                  "double-hybrid functionals (PWPB95) and DLPNO.")
        parser.add_argument("--ri-approximation", default="RIJCOSX", choices=["none", "RI", "RIJCOSX", "RIJK"], help="RI approximation (default: RIJCOSX)")
        parser.add_argument("--aux-basis", dest="aux_j_basis_legacy", default=None, help=argparse.SUPPRESS)

    if wf in ("optimize", "optfreq", "optfreqsp"):
        parser.add_argument("--geom-maxiter", type=int, help="Max geometry iterations (maps to MaxIter in %%geom block)")
        parser.add_argument("--opt-convergence", default="Tight", choices=["Loose", "Normal", "Tight", "VeryTight"], help="Optimization convergence (default: Tight)")
        parser.add_argument("--recalc-hess", type=int, default=None, help="Hessian recalculation interval in %%geom block (Recalc_Hess N; default: 10 from config)")

    if wf == "optfreqsp":
        parser.add_argument("--temperature", type=float, default=298.15, help="Temperature in K (default: 298.15)")
        parser.add_argument("--pressure", type=float, default=1.0, help="Pressure in atm (default: 1.0)")
        parser.add_argument("--scale-factor", type=float, default=0.9905, help="Frequency scale factor for ZPE/thermo (default: 0.9905)")

    if wf == "optfreqsp":
        parser.add_argument("--sp-method", default="wB97M-V", help="SP functional (default: wB97M-V)")
        parser.add_argument("--sp-basis", default="def2-TZVPP", help="SP basis set (default: def2-TZVPP)")
        parser.add_argument("--sp-aux-j-basis", default="AutoAux", metavar="BASIS",
            help="SP auxiliary /J basis for RI-J fitting (default: AutoAux). Common: AutoAux, def2/J.")
        parser.add_argument("--sp-aux-c-basis", default="AutoAux", metavar="BASIS",
            help="SP auxiliary /C basis for RI-MP2 correlation (default: AutoAux). "
                 "Common: AutoAux, def2-TZVPP/C. Only used by double-hybrid functionals (PWPB95) and DLPNO.")
        parser.add_argument("--sp-aux-basis", default=None, help=argparse.SUPPRESS)
        parser.add_argument("--sp-ri-approximation", default="RIJCOSX", choices=["none", "RI", "RIJCOSX", "RIJK"], help="SP RI approximation (default: RIJCOSX)")
        parser.add_argument("--sp-dispersion", default="none", help="SP dispersion correction")
        parser.add_argument("--sp-solvent", default="", help="SP solvent name (e.g. water; defaults to --solvent)")
        parser.add_argument("--sp-solvent-model", default="", help="SP solvent model (defaults to --solvent-model)")


def _add_xtb_optimize_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments for the xTB optimization subcommand."""
    parser.add_argument("--input", "-i", required=True, help="Input structure file (XYZ, GJF, COM, ORCA .inp)")
    parser.add_argument("--output", "-o", default="./out", help="Output directory")
    parser.add_argument("--charge", type=int, help="Molecular charge (auto-detected if not specified)")
    parser.add_argument("--multiplicity", type=int, help="Spin multiplicity (auto-detected if not specified)")
    parser.add_argument("--name", type=str, help="Molecule name")
    parser.add_argument("--gfn", type=int, default=2, choices=[0, 1, 2], help="GFN-xTB Hamiltonian level (default: 2)")
    parser.add_argument(
        "--opt-level", default="normal",
        choices=["crude", "sloppy", "loose", "normal", "tight", "vtight", "extreme"],
        help="xTB optimization convergence level (default: normal)",
    )
    parser.add_argument("--max-steps", type=int, help="Maximum number of optimization cycles (xTB xcontrol maxcycle)")
    parser.add_argument(
        "--solvent-model", default="none", type=str.lower, choices=["gbsa", "alpb", "none"],
        help="xTB solvation model (default: none; GBSA or ALPB)",
    )
    parser.add_argument("--solvent", default="", help="Solvent name (e.g. water, methanol)")
    parser.add_argument("--nproc", type=int, help="Number of CPU cores")
    parser.add_argument("--mem", type=str, help="Memory limit (accepted for compatibility; xTB manages memory via nproc)")
    parser.add_argument("--config", type=str, help="Configuration YAML file")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO", help="Logging level (default: INFO)")


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

    # -- run conformer -------------------------------------------------------
    conf = run_sub.add_parser(
        "conformer",
        help="Conformer search pipeline (CREST → DFT → SP → thermo)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run conformer --input "CCO" --output ./result
  acp run conformer --input molecule.xyz --protocol ext
  acp run conformer --batch-file molecules.txt --output ./batch_results
  acp run conformer --input "CCO" --nproc 32 --mem 64GB
        """,
    )
    # Input group (mutually exclusive: --input | --batch-file)
    # Not required so that --list-protocols / --show-protocol can work solo.
    conf_input = conf.add_mutually_exclusive_group(required=False)
    conf_input.add_argument(
        "--input",
        type=str,
        help="SMILES string or input file path (XYZ, GJF, LOG, OUT)",
    )
    conf_input.add_argument(
        "--batch-file",
        type=str,
        help="File containing multiple inputs (one per line)",
    )
    conf.add_argument(
        "--output",
        type=str,
        default="./conformer_output",
        help="Output directory (default: ./conformer_output)",
    )
    conf.add_argument(
        "--config",
        type=str,
        help="Configuration YAML file",
    )
    conf.add_argument(
        "--levels",
        type=str,
        help=(
            "JSON string or path to JSON file with per-stage method overrides "
            '(e.g. \'{"optimization": {"method": "B3LYP", "solvent": "methanol"}}\')'
        ),
    )
    conf.add_argument(
        "--protocol",
        type=str,
        choices=ALL_PROTOCOLS,
        default="censo-lite",
        help="Conformer search protocol (default: censo-lite)",
    )
    conf.add_argument(
        "--list-protocols",
        action="store_true",
        help="List all available protocols and exit",
    )
    conf.add_argument(
        "--show-protocol",
        type=str,
        metavar="PROTOCOL",
        help="Show details of a specific protocol and exit",
    )
    conf.add_argument(
        "--nproc",
        type=int,
        help="Number of CPU cores (overrides config)",
    )
    conf.add_argument(
        "--mem",
        type=str,
        help="Memory limit, e.g. 32GB, 4096MB (overrides config)",
    )
    conf.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    conf.add_argument(
        "--log-file",
        type=str,
        help="Log file path",
    )
    conf.add_argument(
        "--name",
        type=str,
        help="Molecule name (auto-generated if not specified)",
    )
    conf.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    conf.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )
    conf.add_argument(
        "--save-config",
        type=str,
        help="Save effective configuration to this file",
    )

    # -- run nmr --------------------------------------------------------------
    nmr = run_sub.add_parser(
        "nmr",
        help="NMR workflow (conformer selection → GIAO shielding → report)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run nmr --input "CCO" --output ./nmr_results
  acp run nmr --input molecule.xyz --protocol full --backend orca
  acp run nmr --input "CCO" --reference 1H=31.88 --reference 13C=186.10
        """,
    )
    nmr.add_argument(
        "--input",
        type=str,
        required=True,
        help="SMILES string or input file path (XYZ, GJF, LOG, OUT)",
    )
    nmr.add_argument(
        "--output",
        type=str,
        default="./nmr_output",
        help="Output directory (default: ./nmr_output)",
    )
    nmr.add_argument(
        "--config",
        type=str,
        help="Configuration YAML file",
    )
    nmr.add_argument(
        "--nproc",
        type=int,
        help="Number of CPU cores (overrides config)",
    )
    nmr.add_argument(
        "--mem",
        type=str,
        help="Memory limit, e.g. 32GB, 4096MB (overrides config)",
    )
    nmr.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    nmr.add_argument(
        "--name",
        type=str,
        help="Molecule name (auto-generated if not specified)",
    )
    nmr.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    nmr.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )
    nmr.add_argument(
        "--save-config",
        type=str,
        help="Save effective configuration to this file",
    )
    nmr.add_argument(
        "--protocol",
        type=str,
        choices=ALL_PROTOCOLS,
        default="ext",
        help="Conformer pre-step protocol (default: ext)",
    )
    nmr.add_argument(
        "--backend",
        type=str,
        choices=["orca"],
        help="NMR backend engine (only ORCA is supported)",
    )
    nmr.add_argument(
        "--temperature",
        type=float,
        help="Boltzmann weighting temperature in K",
    )
    nmr.add_argument(
        "--energy-window-kcal",
        type=float,
        help="Energy window in kcal/mol for conformer selection",
    )
    nmr.add_argument(
        "--max-conformers",
        type=int,
        help="Maximum number of conformers to evaluate",
    )
    nmr.add_argument(
        "--reference",
        action="append",
        default=[],
        metavar="NUCLEUS=VALUE",
        help="Override an NMR reference value; repeatable",
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

    # -- simple workflows (singlepoint / optimize / frequency / optfreq / optfreqsp) --
    _add_simple_workflow_parsers(run_sub)

    # -- run mechanism -------------------------------------------------------
    mechanism = run_sub.add_parser(
        "mechanism",
        help="Mechanism analysis (reactant/product → TS → IRC → energy profile)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp run mechanism --input "CCO" --output ./mechanism_results
  acp run mechanism --input reaction.xyz --name substrate
        """,
    )
    mechanism.add_argument(
        "--input",
        type=str,
        required=True,
        help="SMILES string or input file path (XYZ, GJF, LOG, OUT)",
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

    # -- benchmark -----------------------------------------------------------
    bench = subparsers.add_parser(
        "benchmark",
        help="Run multiple conformer search protocols and compare results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp benchmark --input molecule.xyz --output ./benchmark_results
  acp benchmark --input molecule.xyz --benchmark-level standard
  acp benchmark --input molecule.xyz --protocols zero,lite,full
        """,
    )
    bench.add_argument("--input", type=str, required=True, help="Input file path (XYZ)")
    bench.add_argument("--output", type=str, default="./benchmark_results", help="Output directory")
    bench.add_argument(
        "--benchmark-level",
        type=str,
        default="standard",
        choices=["quick", "standard", "strict"],
        help="Preset protocol suite (default: standard)",
    )
    bench.add_argument(
        "--protocols",
        type=str,
        default=None,
        help="Comma-separated protocol list (overrides --benchmark-level)",
    )
    bench.add_argument(
        "--charge",
        type=int,
        help="Molecular charge (auto-detected if not specified)",
    )
    bench.add_argument(
        "--multiplicity",
        type=int,
        help="Spin multiplicity (auto-detected if not specified)",
    )
    bench.add_argument("--nproc", type=int, default=None)
    bench.add_argument("--mem", type=str, default=None)
    bench.add_argument("--config", type=str, default=None)
    bench.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    # -- protocol ------------------------------------------------------------
    proto = subparsers.add_parser(
        "protocol",
        help="Protocol introspection tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  acp protocol list
  acp protocol info full
        """,
    )
    proto_sub = proto.add_subparsers(dest="protocol_action", required=True)
    proto_sub.add_parser("list", help="List all available protocols")
    proto_info = proto_sub.add_parser("info", help="Show protocol details")
    proto_info.add_argument("name", type=str, help="Protocol name")

    return parser


# ---------------------------------------------------------------------------
# Workflow handlers
# ---------------------------------------------------------------------------


def _handle_conformer(args: argparse.Namespace) -> int:
    """Execute the conformer search workflow."""

    # --list-protocols: print all protocol names and exit
    if args.list_protocols:
        print("\n".join(ALL_PROTOCOLS))
        return 0

    # --show-protocol: look up spec and print summary
    if args.show_protocol:
        try:
            from conformer_search.config import load_config
            from conformer_search.core.protocols import resolve_protocol_spec

            cfg = load_config()
            levels = _parse_levels(getattr(args, "levels", None))
            spec = resolve_protocol_spec(cfg, args.show_protocol, levels=levels)
        except KeyError:
            print(f"Unknown protocol: {args.show_protocol}", file=sys.stderr)
            return 1
        print(_render_protocol_info(spec))
        return 0

    # Normal execution path requires an input source
    if not args.input and not args.batch_file:
        print("Error: --input or --batch-file is required", file=sys.stderr)
        return 1

    setup_logging(args.log_level)
    cfg = _build_config(args)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.batch_file:
            return _handle_conformer_batch(args, cfg, output_dir)
        return _handle_conformer_single(args, cfg, output_dir)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1


def _handle_conformer_single(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    output_dir: Path,
) -> int:
    """Run conformer search for a single molecule."""
    from acp.workflows.conformer import run_conformer_search

    logger.info("ACP conformer workflow — single molecule")

    # Save effective config if requested
    if args.save_config:
        from conformer_search.config import save_config as save_cfg

        save_cfg(cfg, Path(args.save_config))
        logger.info("Configuration saved to: %s", args.save_config)

    result = run_conformer_search(
        input_source=args.input,
        output_dir=str(output_dir),
        protocol=args.protocol,
        config=cfg,
        name=args.name,
        levels=_parse_levels(getattr(args, "levels", None)),
    )

    if result.status == "completed":
        meta = result.metadata or {}
        logger.info("Conformer search completed successfully")
        logger.info("  Global minimum XYZ : %s", meta.get("global_min_xyz", "N/A"))
        logger.info("  Energy (Ha)        : %s", meta.get("global_min_energy", "N/A"))
        logger.info("  Conformers         : %s", meta.get("n_conformers", "N/A"))
        return 0

    logger.error("Conformer search failed: %s", result.error)
    return 1


def _handle_conformer_batch(
    args: argparse.Namespace,
    cfg: dict[str, Any],
    output_dir: Path,
) -> int:
    """Run conformer search for multiple molecules (batch mode)."""
    from acp.workflows.conformer import run_conformer_search
    from conformer_search.io import load_batch_inputs

    logger.info("ACP conformer workflow — batch mode")
    batch_file = Path(args.batch_file)
    inputs = load_batch_inputs(batch_file)

    logger.info("Found %d molecules to process", len(inputs))
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i, mi in enumerate(inputs, start=1):
        source = str(mi.source_path or mi.metadata.get("smiles", ""))
        logger.info("[%d/%d] Processing %s", i, len(inputs), mi.name)
        try:
            r = run_conformer_search(
                input_source=source,
                output_dir=str(output_dir),
                protocol=args.protocol,
                config=cfg,
                name=mi.name,
                levels=_parse_levels(getattr(args, "levels", None)),
            )
            if r.status == "completed":
                results.append(
                    {
                        "molecule": mi.name,
                        "status": "completed",
                        "metadata": r.metadata,
                    }
                )
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
        "protocol": args.protocol,
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
    """Execute the NMR workflow."""
    setup_logging(args.log_level)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        cfg = _build_config(args)
        references = _parse_nmr_references(args.reference)
        _apply_nmr_config_overrides(cfg, args, references)

        if args.save_config:
            from conformer_search.config import save_config as save_cfg

            save_cfg(cfg, Path(args.save_config))
            logger.info("Configuration saved to: %s", args.save_config)

        from acp.workflows.nmr import run_nmr_calculation

        result = run_nmr_calculation(
            input_source=args.input,
            output_dir=output_dir,
            conformer_protocol=args.protocol,
            config=cfg,
            name=args.name,
            backend_name=args.backend,
            charge=args.charge,
            multiplicity=args.multiplicity,
            references=references,
            temperature=args.temperature,
            energy_window_kcal=args.energy_window_kcal,
            max_conformers=args.max_conformers,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    if result.status == "completed":
        logger.info("NMR workflow completed successfully")
        logger.info("  Backend            : %s", result.metadata.get("backend", "N/A"))
        logger.info("  Selected conformers: %s", result.metadata.get("selected_conformers", "N/A"))
        logger.info("  JSON report        : %s", result.metadata.get("nmr_report", "N/A"))
        logger.info("  XLSX report        : %s", result.metadata.get("nmr_report_xlsx", "N/A"))
        return 0

    logger.error("NMR workflow failed: %s", result.error)
    return 1


def _handle_mechanism(args: argparse.Namespace) -> int:
    """Execute the mechanism workflow."""
    setup_logging(args.log_level)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        cfg = _build_config(args)
        from acp.workflows.mechanism import run_mechanism_analysis

        result = run_mechanism_analysis(
            input_source=args.input,
            output_dir=output_dir,
            config=cfg,
            name=args.name,
            charge=args.charge,
            multiplicity=args.multiplicity,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    if result.status == "completed":
        raw_energy_profile = result.metadata.get("energy_profile", {})
        energy_profile = cast(
            dict[str, object],
            raw_energy_profile if isinstance(raw_energy_profile, dict) else {},
        )
        logger.info("Mechanism workflow completed successfully")
        logger.info("  Structures          : %s", result.metadata.get("n_structures", "N/A"))
        logger.info(
            "  Forward barrier     : %s", energy_profile.get("forward_barrier_kcal_mol", "N/A")
        )
        logger.info(
            "  Reaction energy     : %s", energy_profile.get("reaction_energy_kcal_mol", "N/A")
        )
        return 0

    logger.error("Mechanism workflow failed: %s", result.error)
    return 1


def _build_simple_method_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in ("method", "basis", "dispersion", "solvent_model", "solvent",
                "aux_j_basis", "aux_c_basis", "ri_approximation", "geom_maxiter", "opt_convergence",
                "recalc_hess"):
        val = getattr(args, key, None)
        if val is not None:
            kwargs[key] = val
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
        from conformer_search.config import save_config as save_cfg
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
    from conformer_search.io import load_batch_inputs

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
        len(results), len(inputs), summary_path,
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
        from conformer_search.config import save_config as save_cfg
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
    from conformer_search.io import load_batch_inputs

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
        len(results), len(inputs), summary_path,
    )
    return 0 if not errors else 1


def _handle_benchmark(args: argparse.Namespace) -> int:
    """Execute the benchmark meta-protocol."""
    from acp.workflows.benchmark import BENCHMARK_LEVELS, BenchmarkRunner

    setup_logging(args.log_level)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        return 1

    if args.protocols:
        protocols = [p.strip() for p in args.protocols.split(",") if p.strip()]
    else:
        protocols = BENCHMARK_LEVELS.get(args.benchmark_level, BENCHMARK_LEVELS["standard"])

    if not protocols:
        print("Error: no benchmark protocols selected", file=sys.stderr)
        return 1

    unknown_protocols = [protocol for protocol in protocols if protocol not in ALL_PROTOCOLS]
    if unknown_protocols:
        print(
            "Error: unknown benchmark protocol(s): " + ", ".join(unknown_protocols),
            file=sys.stderr,
        )
        return 1

    try:
        runner = BenchmarkRunner(
            config=_build_config(args),
            protocols=protocols,
            output_dir=Path(args.output),
        )
        summary = runner.run(
            input_path,
            charge=args.charge,
            multiplicity=args.multiplicity,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Fatal error: %s", exc)
        return 1

    print(runner.format_summary_table(summary))
    return 0 if any(data.get("success") for data in summary["protocols"].values()) else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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

    if args.command == "benchmark":
        return _handle_benchmark(args)

    if args.command == "protocol":
        return _handle_protocol(args)

    if args.command != "run":
        parser.print_help()
        return 1

    dispatch: dict[str, Callable[[argparse.Namespace], int]] = {
        "conformer": _handle_conformer,
        "ensemble": _handle_ensemble,
        "energy": _handle_energy,
        "nmr": _handle_nmr,
        "mechanism": _handle_mechanism,
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

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
