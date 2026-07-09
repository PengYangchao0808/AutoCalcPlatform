"""
CLI Package
===========

Command-line interface for ConformerSearch.

Author: QCcalc Team
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Mapping, Optional, List
import json
from datetime import datetime

from conformer_search import __version__
from conformer_search.config import load_config, save_config
from conformer_search.io import MolecularInputHandler, load_batch_inputs
from conformer_search.core import ConformerEngine


ALL_PROTOCOLS = [
    'ext', 'full', 'lite', 'zero', 'benchmark',
    'censo-zero', 'censo-lite', 'censo-full',
    'censo-full-safe', 'allopt', 'reference-sp',
    
]


def _guard_reference_sp_input(protocol: str, input_source: Optional[str]) -> None:
    """Reject SMILES-style reference-sp input before the legacy engine runs."""
    if protocol != 'reference-sp' or not input_source:
        return
    if Path(input_source).is_file():
        return

    print(
        'reference-sp requires an existing conformer ensemble. Use censo-full or allopt first, then: '
        'conformer-search --input <output>/final_ensemble.xyz --protocol reference-sp',
        file=sys.stderr,
    )
    sys.exit(1)


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None):
    """
    Setup logging configuration.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file path
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=handlers
    )


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parse command-line arguments.

    Args:
        args: Arguments to parse (defaults to sys.argv)

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        prog='conformer-search',
        description='Automated conformer search pipeline for organic molecules',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single molecule from SMILES
  conformer-search --input "CCO" --output ./result

  # Single molecule from XYZ file
  conformer-search --input molecule.xyz --protocol ext

  # Batch processing from file
  conformer-search --batch-file molecules.txt --output ./batch_results

  # With custom configuration
  conformer-search --input "CCO" --config my_config.yaml --nproc 32
        """
    )

    parser.add_argument(
        '--version',
        action='version',
        version=f'ConformerSearch {__version__}'
    )

    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        '--input',
        type=str,
        help='SMILES string or input file path (XYZ, GJF, LOG, OUT)'
    )
    input_group.add_argument(
        '--batch-file',
        type=str,
        help='File containing multiple inputs (one per line)'
    )

    parser.add_argument(
        '--output',
        type=str,
        default='./conformer_output',
        help='Output directory (default: ./conformer_output)'
    )

    parser.add_argument(
        '--config',
        type=str,
        help='Configuration YAML file'
    )

    parser.add_argument(
        '--protocol',
        type=str,
        choices=ALL_PROTOCOLS,
        default='ext',
        help='Conformer search protocol (default: ext)'
    )

    parser.add_argument(
        '--nproc',
        type=int,
        help='Number of CPU cores (overrides config)'
    )

    parser.add_argument(
        '--mem',
        type=str,
        help='Memory limit (e.g., 32GB, 4096MB, overrides config)'
    )

    parser.add_argument(
        '--log-level',
        type=str,
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )

    parser.add_argument(
        '--log-file',
        type=str,
        help='Log file path'
    )

    parser.add_argument(
        '--name',
        type=str,
        help='Molecule name (auto-generated if not specified)'
    )

    parser.add_argument(
        '--charge',
        type=int,
        help='Molecular charge (auto-detected if not specified)'
    )

    parser.add_argument(
        '--multiplicity',
        type=int,
        help='Spin multiplicity (auto-detected if not specified)'
    )

    parser.add_argument(
        '--save-config',
        type=str,
        help='Save effective configuration to this file'
    )

    return parser.parse_args(args)


def run_single_molecule(
    input_source: str,
    output_dir: Path,
    config: dict[str, object],
    protocol: str,
    args: argparse.Namespace
) -> Mapping[str, object]:
    """
    Run conformer search for a single molecule.

    Args:
        input_source: SMILES string or file path
        output_dir: Output directory
        config: Configuration dictionary
        protocol: Protocol name
        args: Parsed command-line arguments

    Returns:
        Results dictionary
    """
    logger = logging.getLogger(__name__)

    logger.info(f"Processing input: {input_source}")

    handler = MolecularInputHandler()
    molecular_input = handler.from_source(
        input_source,
        name=args.name,
        charge=args.charge,
        multiplicity=args.multiplicity
    )

    logger.info(f"  Molecule: {molecular_input.name}")
    logger.info(f"  Atoms: {molecular_input.n_atoms}")
    logger.info(f"  Charge: {molecular_input.charge}")
    logger.info(f"  Multiplicity: {molecular_input.multiplicity}")
    logger.info(f"  Format: {molecular_input.source_format.value}")

    engine = ConformerEngine(
        config=config,
        work_dir=output_dir,
        molecule_name=molecular_input.name,
        protocol=protocol
    )

    global_min_xyz, energy, metadata = engine.run(molecular_input)

    logger.info(f"  Global minimum: {global_min_xyz}")
    if energy is not None:
        logger.info(f"  Energy: {energy:.6f} Ha")
    else:
        logger.warning(f"  Energy: N/A (thermo not computed)")

    return {
        'success': True,
        'molecule_name': molecular_input.name,
        'global_min_xyz': str(global_min_xyz),
        'energy_hartree': energy,
        'n_conformers': metadata.get('n_conformers', 0),
        'protocol': protocol
    }


def run_batch(
    batch_file: Path,
    output_dir: Path,
    config: dict[str, object],
    protocol: str,
    args: argparse.Namespace
) -> Mapping[str, object]:
    """
    Run conformer search for multiple molecules.

    Args:
        batch_file: Batch input file
        output_dir: Output directory
        config: Configuration dictionary
        protocol: Protocol name
        args: Parsed command-line arguments

    Returns:
        Results dictionary
    """
    logger = logging.getLogger(__name__)

    logger.info(f"Loading batch file: {batch_file}")

    inputs = load_batch_inputs(
        batch_file,
        charge=args.charge,
        multiplicity=args.multiplicity
    )

    logger.info(f"  Found {len(inputs)} molecules to process")

    results = []
    errors = []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_file = output_dir / f"batch_summary_{timestamp}.json"

    for i, molecular_input in enumerate(inputs, 1):
        logger.info(f"\n[{i}/{len(inputs)}] Processing {molecular_input.name}")

        # ConformerEngine appends molecule_name to work_dir internally,
        # so pass output_dir directly to avoid double nesting.
        try:
            result = run_single_molecule(
                str(molecular_input.source_path or molecular_input.metadata.get('smiles', '')),
                output_dir,
                config,
                protocol,
                args
            )
            results.append(result)
        except Exception as e:
            logger.error(f"  Failed: {e}")
            errors.append({
                'molecule': molecular_input.name,
                'error': str(e)
            })

    summary = {
        'timestamp': timestamp,
        'total': len(inputs),
        'successful': len(results),
        'failed': len(errors),
        'protocol': protocol,
        'results': results,
        'errors': errors
    }

    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)

    logger.info(f"\nBatch complete: {len(results)}/{len(inputs)} successful")
    logger.info(f"Summary saved to: {summary_file}")

    return summary


def main(args: Optional[List[str]] = None):
    """
    Main entry point.

    Args:
        args: Command-line arguments (defaults to sys.argv)
    """
    parsed_args = parse_args(args)

    setup_logging(level=parsed_args.log_level, log_file=Path(parsed_args.log_file) if parsed_args.log_file else None)

    logger = logging.getLogger(__name__)
    logger.info(f"ConformerSearch v{__version__}")

    config = load_config(
        config_path=Path(parsed_args.config) if parsed_args.config else None
    )

    if parsed_args.nproc:
        config.setdefault('resources', {})['nproc'] = parsed_args.nproc
        logger.info(f"Using nproc from command line: {parsed_args.nproc}")

    if parsed_args.mem:
        config.setdefault('resources', {})['mem'] = parsed_args.mem
        logger.info(f"Using mem from command line: {parsed_args.mem}")

    protocol = parsed_args.protocol
    _guard_reference_sp_input(protocol, parsed_args.input)
    config.setdefault('protocols', {})['default'] = protocol

    if parsed_args.save_config:
        save_config(config, Path(parsed_args.save_config))
        logger.info(f"Configuration saved to: {parsed_args.save_config}")

    output_dir = Path(parsed_args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if parsed_args.batch_file:
            result = run_batch(
                Path(parsed_args.batch_file),
                output_dir,
                config,
                protocol,
                parsed_args
            )
            sys.exit(0 if result['failed'] == 0 else 1)
        else:
            result = run_single_molecule(
                parsed_args.input,
                output_dir,
                config,
                protocol,
                parsed_args
            )
            sys.exit(0)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
