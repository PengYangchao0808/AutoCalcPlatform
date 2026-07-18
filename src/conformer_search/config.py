"""
Configuration Loader
====================

YAML configuration loading and validation.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "conformer_search.yaml"
USER_CONFIG_NAME = ".conformer_search.yaml"


def _find_project_root() -> Path | None:
    """
    Find the project root directory by searching upward for config/defaults.yaml.
    
    Returns:
        Path to project root, or None if not found
    """
    current = Path(__file__).resolve()
    for parent in [current] + list(current.parents):
        if (parent / 'config' / 'defaults.yaml').exists():
            return parent
        if (parent / 'defaults.yaml').exists():
            return parent
    return None


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
    defaults_path: Path | None = None
) -> dict[str, Any]:
    """
    Load configuration from multiple sources, merged in order (latest wins):

    1. Built-in defaults (config/defaults.yaml or _get_default_config())
    2. User home config (~/.conformer_search.yaml)
    3. Local directory config (./conformer_search.yaml)
    4. Explicit config file (--config)
    5. Environment variable overrides (CONFSEARCH_*)
    6. Command-line parameter overrides

    Args:
        config_path: Explicit config file path (--config)
        overrides: Command-line parameter overrides (e.g., --nproc, --mem)
        defaults_path: Path to defaults.yaml (auto-detected if None)

    Returns:
        Merged configuration dictionary
    """
    config = {}

    # Auto-detect defaults.yaml if not explicitly provided
    if defaults_path is None:
        project_root = _find_project_root()
        possible_defaults = []
        if project_root:
            possible_defaults.append(project_root / 'config' / 'defaults.yaml')
        possible_defaults.extend([
            Path(__file__).resolve().parent.parent.parent / 'config' / 'defaults.yaml',
            Path.cwd() / 'config' / 'defaults.yaml',
            Path.home() / '.config' / 'conformer_search' / 'defaults.yaml',
        ])
        for p in possible_defaults:
            if p.exists():
                defaults_path = p
                break

    if defaults_path and Path(defaults_path).exists():
        with open(defaults_path, encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
        logger.info(f"Loaded defaults from: {defaults_path}")
    else:
        config = _get_default_config()
        logger.debug("Using built-in default configuration")

    user_home = Path.home()
    user_config_path = user_home / USER_CONFIG_NAME
    if user_config_path.exists():
        with open(user_config_path, encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
        config = _merge_configs(config, user_config)

    local_config = Path.cwd() / DEFAULT_CONFIG_NAME
    if local_config.exists():
        with open(local_config, encoding='utf-8') as f:
            local_cfg = yaml.safe_load(f) or {}
        config = _merge_configs(config, local_cfg)

    if config_path and Path(config_path).exists():
        with open(config_path, encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
        config = _merge_configs(config, user_config)
        logger.info(f"Loaded explicit config from: {config_path}")

    config = _apply_env_overrides(config)

    if overrides:
        config = _merge_configs(config, overrides)

    config = _validate_config(config)

    return config


def _get_default_config() -> dict[str, Any]:
    """Get built-in default configuration."""
    return {
        'executables': {
            'orca': {
                'path': '/opt/orca/orca',
                'ld_library_path': '/opt/openmpi/lib:/opt/orca',
                'nproc': 10,
                'maxcore': None
            },
            'xtb': {
                'path': 'xtb',
                'fallback_paths': ['/opt/xtb/bin/xtb', '/usr/local/bin/xtb']
            },
            'crest': {
                'path': 'crest',
                'gfn_level': 2
            },
            'isostat': {
                'path': 'isostat'
            },
            'shermo': {
                'path': 'Shermo'
            }
        },
        'resources': {
            'nproc': 16,
            'mem': '30GB',
            'orca_maxcore_safety': 0.8,
            'isostat_gdis': 1.0,
            'isostat_intermediate_gdis': 0.5,
            'isostat_energy_window_kcal': 3.0,
            'isostat_intermediate_energy_window_kcal': 10.0,
        },
        'theory': {
            'optimization': {
                'engine': 'orca',
                'method': 'r2SCAN-3c',
                'basis': '',
                'solvent': 'toluene',
                'solvent_model': 'none'
            },
            'frequency': {
                'engine': 'orca'
            },
            'single_point': {
                'method': 'wB97M-V',
                'basis': 'def2-TZVPP',
                'solvent': 'toluene',
                'solvent_model': 'none',
                'engine': 'orca'
            },
            'preoptimization': {
                'gfn_level': 2,
                'solvent': 'toluene',
                'solvent_model': 'none'
            },
            'nmr': {
                'engine': 'orca',
                'method': 'B3LYP',
                'basis': 'def2-TZVPP',
                'solvent': None,
                'solvent_model': 'smd'
            }
        },
        'thermo': {
            'temperature_k': 298.15,
            'pressure_atm': 1.0,
            'scl_zpe': 0.9905,
            'shermo_ilowfreq': 2,
            'shermo_imagreal': 0,
            'shermo_conc': 1.0
        },
        'mrrho_settings': {
            'gfn_level': 2,
            'sthr': 50.0,
            'imagthr': -100.0,
            'temperature_k': 298.15,
            'max_parallel': 4,
        },
        'censo': {
            'preset': 'censo-light',
            'solvent': None,
            'temperature': 298.15,
            'scale': 1.0,
            'keep_all': False,
            'refinement_threshold': 0.99,
            'refinement_func': 'wb97m-v',
            'refinement_basis': 'def2-tzvpp',
            'optimization': {
                'enabled': True,
                'functional': 'r2SCAN-3c',
                'threshold': 3.0,
                'optlevel': 'normal',
                'maxcyc': 200,
                'optcycles': 8,
                'macrocycles': True,
                'xtb_opt': True,
            },
            'presets': {},
            'levels': {},
        },
        'nmr': {
            'temperature_k': 298.15,
            'energy_window_kcal': 3.0,
            'max_conformers': 10,
            'references': {
                '1H': 31.88,
                '13C': 186.10,
                '15N': None,
                '19F': None,
                '31P': None,
            }
        },
        'protocols': {
            'default': 'lite',
            'benchmark': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': True,
                    'frequency': True,
                    'single_point': True,
                    'shermo': True,
                },
                'two_stage_enabled': True,
                'ngeom_default': 3,
                'ngeom_max': 6,
                'funnel': {
                    'search_mode': 'crest_two_stage_gfn0_to_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'none',
                    'rerank_mode': 'none',
                },
                'handoff': {
                    'mode': 'optimize_all_candidates',
                    'ranking_after_handoff': 'final_sp_minimum',
                },
                'final_opt_sp': {
                    'final_sp_method': 'DLPNO-CCSD(T)',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'ext': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': True,
                    'frequency': True,
                    'single_point': True,
                    'shermo': True,
                },
                'two_stage_enabled': True,
                'ngeom_default': 3,
                'ngeom_max': 6,
                'funnel': {
                    'search_mode': 'crest_two_stage_gfn0_to_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'none',
                    'rerank_mode': 'none',
                },
                'handoff': {
                    'mode': 'optimize_all_candidates',
                    'ranking_after_handoff': 'final_sp_minimum',
                },
                'final_opt_sp': {
                    'final_sp_method': 'wB97M-V',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'full': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': True,
                    'frequency': True,
                    'single_point': True,
                    'shermo': True,
                },
                'two_stage_enabled': False,
                'ngeom_default': 12,
                'ngeom_max': 24,
                'funnel': {
                    'search_mode': 'crest_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'low_cost_dft_sp',
                    'rerank_mode': 'r2scan3c_sp_plus_mrrho',
                    'prescreen_window_kcal': 4.0,
                    'screening_window_kcal': 3.5,
                    'survivor_window_kcal': 3.0,
                },
                'handoff': {
                    'mode': 'optimize_all_survivors_within_window',
                    'ranking_after_handoff': 'final_sp_plus_boltzmann',
                },
                'final_opt_sp': {
                    'final_sp_method': 'wB97M-V',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'lite': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': True,
                    'frequency': True,
                    'single_point': True,
                    'shermo': True,
                },
                'two_stage_enabled': False,
                'ngeom_default': 4,
                'ngeom_max': 6,
                'funnel': {
                    'search_mode': 'crest_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'none',
                    'rerank_mode': 'r2scan3c_sp',
                    'use_mrrho_like_correction': True,
                    'optimize_limit': 1,
                    'top2_fallback_enabled': True,
                    'boltzmann_cutoff': 0.90,
                },
                'handoff': {
                    'mode': 'optimize_rank1',
                    'fallback_mode': 'optimize_top2_if_gap_small',
                    'small_gap_kcal': 1.0,
                    'ranking_after_handoff': 'final_sp_minimum',
                },
                'final_opt_sp': {
                    'final_sp_method': 'wB97M-V',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'zero': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': True,
                    'frequency': True,
                    'single_point': True,
                    'shermo': True,
                },
                'two_stage_enabled': False,
                'ngeom_default': 1,
                'ngeom_max': 3,
                'funnel': {
                    'search_mode': 'crest_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'none',
                    'rerank_mode': 'none',
                    'narrow_window_kcal': 0.5,
                    'optimize_limit': 1,
                },
                'handoff': {
                    'mode': 'optimize_rank1',
                    'fallback_mode': 'optimize_all_within_0p5_kcal',
                    'ranking_after_handoff': 'final_sp_minimum',
                },
                'final_opt_sp': {},
            },
        },
        'cluster': {
            'enabled': False,
            'execution_mode': 'local',
            'poll_interval': 30,
            'retention_days': 180,
            'auto_sync': True,
            'type': 'local',
            'queue': 'normal',
            'walltime': '24:00',
            'extra_flags': '',
            'nodes': [],
            # Local run_root lifecycle management (Phase 5B). MUST stay in
            # sync with config/defaults.yaml cluster.local_retention.
            'local_retention': {
                'enabled': True,
                'completed_days': 30,
                'failed_days': 90,
                'cancelled_days': 30,
                'db_record_days': 365,
                'cleanup_interval_hours': 6,
                'disk_cleanup_threshold': 90,
                'disk_skip_threshold': 95,
                'max_dirs_per_sweep': 200,
                'vacuum_after_db_cleanup': False,
            },
        },
        'optimization_control': {
            'recalc_hess': 10,  # Hessian recalculation interval for geometry optimization
            'timeout': {
                'default_seconds': 864000
            }
        }
    }


def _merge_configs(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge two configuration dictionaries.
    
    Args:
        base: Base configuration
        override: Override configuration
        
    Returns:
        Merged configuration
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_configs(result[key], value)
        else:
            result[key] = value

    return result


def _set_nested(config: dict[str, Any], keys: list[str], value: Any) -> None:
    """Set a value at a nested key path, creating intermediate dicts as needed.
    
    Args:
        config: The configuration dictionary to modify in place.
        keys: List of string keys forming the path.
        value: The value to set at the leaf.
    """
    for k in keys[:-1]:
        config = config.setdefault(k, {})
    config[keys[-1]] = value


def _apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """
    Apply environment variable overrides.
    
    Environment variables:
    - CONFSEARCH_NPROC
    - CONFSEARCH_MEM
    - CONFSEARCH_ORCA_PATH
    - CONFSEARCH_XTB_PATH
    - CONFSEARCH_CREST_PATH
    - CONFSEARCH_ISOSTAT_PATH
    - CONFSEARCH_SHERMO_PATH

    Args:
        config: Configuration dictionary

    Returns:
        Configuration with environment overrides applied
    """
    env_mappings = {
        'CONFSEARCH_NPROC': ('resources', 'nproc', int),
        'CONFSEARCH_MEM': ('resources', 'mem', str),
        'CONFSEARCH_ORCA_PATH': ('executables', ['orca', 'path'], str),
        'CONFSEARCH_XTB_PATH': ('executables', ['xtb', 'path'], str),
        'CONFSEARCH_CREST_PATH': ('executables', ['crest', 'path'], str),
        'CONFSEARCH_ISOSTAT_PATH': ('executables', ['isostat', 'path'], str),
        'CONFSEARCH_SHERMO_PATH': ('executables', ['shermo', 'path'], str),
        'CONFSEARCH_PROTOCOL': ('protocols', 'default', str),
        'CONFSEARCH_MRRHO_STHR': ('mrrho_settings', 'sthr', float),
        'CONFSEARCH_MRRHO_IMAGTHR': ('mrrho_settings', 'imagthr', float),
    }

    for env_var, (section, key, value_type) in env_mappings.items():
        env_value = os.environ.get(env_var)
        if env_value:
            section_dict = config.setdefault(section, {})
            if isinstance(key, list):
                _set_nested(section_dict, key, value_type(env_value))
                logger.debug(f"Applied env override: {env_var} -> {section}.{'.'.join(key)}")
            else:
                section_dict[key] = value_type(env_value)
                logger.debug(f"Applied env override: {env_var} -> {section}.{key}")

    if os.environ.get('CONFSEARCH_NPROC'):
        config.setdefault('executables', {}).setdefault('orca', {})['nproc'] = int(os.environ['CONFSEARCH_NPROC'])

    return config


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """
    Validate configuration and apply defaults.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Validated configuration
    """
    if 'resources' not in config:
        config['resources'] = {}

    if config['resources'].get('nproc', 0) <= 0:
        import multiprocessing
        config['resources']['nproc'] = min(multiprocessing.cpu_count(), 32)
        logger.warning(f"Invalid nproc, using {config['resources']['nproc']}")

    if not config['resources'].get('mem'):
        config['resources']['mem'] = '32GB'

    protocol = config.get('protocols', {}).get('default', 'ext')
    if protocol not in ('ext', 'full', 'lite', 'zero', 'benchmark'):
        logger.warning(f"Unknown protocol '{protocol}', using 'ext'")
        config['protocols']['default'] = 'ext'

    valid_engines = ('orca',)
    theory = config.setdefault('theory', {})

    opt_engine = theory.setdefault('optimization', {}).get('engine', 'orca')
    if opt_engine == 'gaussian':
        logger.warning(
            "Optimization engine 'gaussian' is no longer supported; using 'orca'"
        )
        theory['optimization']['engine'] = 'orca'
    elif opt_engine not in valid_engines:
        logger.warning(f"Invalid optimization engine '{opt_engine}', defaulting to 'orca'")
        theory['optimization']['engine'] = 'orca'

    freq_engine = theory.setdefault('frequency', {}).get('engine', 'orca')
    if freq_engine == 'gaussian':
        logger.warning(
            "Frequency engine 'gaussian' is no longer supported; using 'orca'"
        )
        theory['frequency']['engine'] = 'orca'
    elif freq_engine not in valid_engines:
        logger.warning(f"Invalid frequency engine '{freq_engine}', defaulting to 'orca'")
        theory['frequency']['engine'] = 'orca'

    nmr_engine = theory.setdefault('nmr', {}).get('engine', 'orca')
    if nmr_engine == 'gaussian':
        logger.warning("NMR engine 'gaussian' is no longer supported; using 'orca'")
        theory['nmr']['engine'] = 'orca'
    elif nmr_engine not in valid_engines:
        logger.warning(f"Invalid NMR engine '{nmr_engine}', defaulting to 'orca'")
        theory['nmr']['engine'] = 'orca'

    nmr = config.setdefault('nmr', {})

    temperature_k = nmr.get('temperature_k', 298.15)
    if isinstance(temperature_k, bool) or not isinstance(temperature_k, (int, float)) or temperature_k <= 0:
        logger.warning(f"Invalid NMR temperature_k '{temperature_k}', defaulting to 298.15")
        temperature_k = 298.15
    nmr['temperature_k'] = float(temperature_k)

    energy_window_kcal = nmr.get('energy_window_kcal', 3.0)
    if (
        isinstance(energy_window_kcal, bool)
        or not isinstance(energy_window_kcal, (int, float))
        or energy_window_kcal < 0
    ):
        logger.warning(
            f"Invalid NMR energy_window_kcal '{energy_window_kcal}', defaulting to 3.0"
        )
        energy_window_kcal = 3.0
    nmr['energy_window_kcal'] = float(energy_window_kcal)

    max_conformers = nmr.get('max_conformers', 10)
    if max_conformers is not None and (
        isinstance(max_conformers, bool)
        or not isinstance(max_conformers, int)
        or max_conformers < 1
    ):
        logger.warning(
            f"Invalid NMR max_conformers '{max_conformers}', defaulting to 10"
        )
        max_conformers = 10
    nmr['max_conformers'] = max_conformers

    references = nmr.setdefault('references', {})
    default_references = {
        '1H': 31.88,
        '13C': 186.10,
        '15N': None,
        '19F': None,
        '31P': None,
    }
    for nucleus, default_value in default_references.items():
        value = references.get(nucleus, default_value)
        if value is None:
            references[nucleus] = None
            continue
        if not isinstance(value, bool) and isinstance(value, (int, float)):
            references[nucleus] = float(value)
            continue
        logger.warning(
            f"Invalid NMR reference '{nucleus}={value}', defaulting to {default_value}"
        )
        references[nucleus] = default_value

    _validate_local_retention(config)

    return config


def _validate_local_retention(config: dict[str, Any]) -> None:
    """Validate the ``cluster.local_retention`` section (Phase 5B).

    Ensures retention windows are positive integers and that the disk
    cleanup threshold stays below the skip threshold.  Coerces/repairs
    bad values in place with a warning rather than failing hard — config
    load must never crash the server.
    """
    cluster = config.setdefault('cluster', {})
    section = cluster.setdefault('local_retention', {})

    def _positive_int(key: str, default: int) -> None:
        value = section.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            logger.warning(
                f"Invalid cluster.local_retention.{key}={value!r}, defaulting to {default}"
            )
            value = default
        section[key] = value

    _positive_int('completed_days', 30)
    _positive_int('failed_days', 90)
    _positive_int('cancelled_days', 30)
    _positive_int('db_record_days', 365)
    _positive_int('cleanup_interval_hours', 6)
    _positive_int('max_dirs_per_sweep', 200)

    cleanup_thr = section.get('disk_cleanup_threshold', 90)
    skip_thr = section.get('disk_skip_threshold', 95)
    for key, default in (('disk_cleanup_threshold', 90), ('disk_skip_threshold', 95)):
        value = section.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 100):
            logger.warning(
                f"Invalid cluster.local_retention.{key}={value!r}, defaulting to {default}"
            )
            section[key] = default
    cleanup_thr = section['disk_cleanup_threshold']
    skip_thr = section['disk_skip_threshold']
    if cleanup_thr >= skip_thr:
        logger.warning(
            f"cluster.local_retention.disk_cleanup_threshold ({cleanup_thr}) must be "
            f"strictly below disk_skip_threshold ({skip_thr}); resetting to 90/95"
        )
        section['disk_cleanup_threshold'] = 90
        section['disk_skip_threshold'] = 95

    if not isinstance(section.get('enabled', True), bool):
        section['enabled'] = True
    if not isinstance(section.get('vacuum_after_db_cleanup', False), bool):
        section['vacuum_after_db_cleanup'] = False


def save_config(config: dict[str, Any], output_path: Path):
    """
    Save configuration to YAML file.
    
    Args:
        config: Configuration dictionary
        output_path: Output file path
    """
    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
