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
        default="lite",
        help="Conformer search protocol (default: lite)",
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
  acp run nmr --input molecule.xyz --protocol full --backend gaussian
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
        choices=["gaussian", "orca"],
        help="NMR backend engine (default: theory.nmr.engine from config)",
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
        help="Maximum concurrently running jobs (default: 1)",
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
        default=0,
        help="Molecular charge (default: 0)",
    )
    bench.add_argument(
        "--multiplicity",
        type=int,
        default=1,
        help="Spin multiplicity (default: 1)",
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
    log_level = getattr(args, "log_level", "INFO").lower()
    no_browser = getattr(args, "no_browser", False)

    run_root_path = Path(run_root).resolve()
    run_root_path.mkdir(parents=True, exist_ok=True)

    os.environ["ACP_RUN_ROOT"] = str(run_root_path)
    os.environ["ACP_HOST"] = host
    os.environ["ACP_PORT"] = str(port)
    os.environ["ACP_MAX_RUNNING"] = str(max_running)

    url = f"http://{host}:{port}"
    print(f"ACP Workbench starting at {url}")
    print(f"  Run root     : {run_root_path}")
    print(f"  Max running  : {max_running}")
    print(f"  Docs (Swagger): {url}/docs")
    print(f"  Reload       : {'on' if reload_flag else 'off'}")
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
        "nmr": _handle_nmr,
        "mechanism": _handle_mechanism,
        "serve": _handle_serve,
    }

    handler = dispatch.get(args.workflow)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
