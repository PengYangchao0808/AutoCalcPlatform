"""
Configuration Loader
====================

YAML configuration loading and validation.

Authoritative source: _get_default_config() below is the SINGLE source of truth
for default values. The config/defaults.yaml file is provided as a user reference
only and may diverge from these built-in defaults.

Author: QCcalc Team
"""

import os
import tempfile
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_NAME = "conformer_search.yaml"
USER_CONFIG_NAME = ".conformer_search.yaml"


def _find_project_root() -> Optional[Path]:
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
    config_path: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
    defaults_path: Optional[Path] = None
) -> Dict[str, Any]:
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
        with open(defaults_path, 'r', encoding='utf-8') as f:
            yaml_config = yaml.safe_load(f) or {}
        config = _merge_configs(_get_default_config(), yaml_config)
        logger.info(f"Loaded defaults from: {defaults_path}")
    else:
        config = _get_default_config()
        logger.debug("Using built-in default configuration")

    user_home = Path.home()
    user_config_path = user_home / USER_CONFIG_NAME
    if user_config_path.exists():
        with open(user_config_path, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
        config = _merge_configs(config, user_config)

    local_config = Path.cwd() / DEFAULT_CONFIG_NAME
    if local_config.exists():
        with open(local_config, 'r', encoding='utf-8') as f:
            local_cfg = yaml.safe_load(f) or {}
        config = _merge_configs(config, local_cfg)

    if config_path and Path(config_path).exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            user_config = yaml.safe_load(f) or {}
        config = _merge_configs(config, user_config)

    config = _apply_env_overrides(config)

    if overrides:
        config = _merge_configs(config, overrides)

    config = _validate_config(config)

    return config


def _get_default_config() -> Dict[str, Any]:
    """Get built-in default configuration."""
    return {
        'executables': {
            'gaussian': {
                'path': 'g16',
                'use_wrapper': True,
                'wrapper_path': './scripts/run_g16_worker.sh'
            },
            'orca': {
                'path': '/opt/software/orca/orca',
                'ld_library_path': '/opt/software/openmpi/lib:/opt/software/orca',
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
            'nproc': 20,
            'mem': '30GB',
            'orca_maxcore_safety': 0.8
        },
        'theory': {
            'optimization': {
                'engine': 'gaussian',
                'method': 'B3LYP',
                'basis': 'def2-SVP',
                'dispersion': 'GD3BJ',
                'solvent': None,
                'solvent_model': 'smd'
            },
            'frequency': {
                'engine': 'gaussian'
            },
            'low_cost_sp': {
                'method': 'r2scan3c',
                'basis': None,
                'solvent': None,
                'solvent_model': 'smd'
            },
            'single_point': {
                'method': 'wB97X-D4',
                'basis': 'def2-TZVPP',
                'solvent': None,
                'solvent_model': 'smd'
            },
            'final_sp': {
                'method': 'wB97X-D4',
                'basis': 'def2-TZVPP',
                'solvent': None,
                'solvent_model': 'smd'
            },
            'preoptimization': {
                'gfn_level': 2,
                'solvent': None
            },
            'nmr': {
                'engine': 'gaussian',
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
            'default': 'ext',
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
                    'final_sp_method': 'wB97X-D4',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'censo-zero': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': False,
                    'frequency': False,
                    'single_point': True,
                    'shermo': False,
                },
                'two_stage_enabled': False,
                'ngeom_default': 1,
                'ngeom_max': 3,
                'funnel': {
                    'search_mode': 'crest_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'none',
                    'rerank_mode': 'none',
                    'survivor_window_kcal': 1.0,
                },
                'handoff': {
                    'mode': 'optimize_rank1',
                    'ranking_after_handoff': 'xtb_energy',
                },
                'final_opt_sp': {
                    'final_sp_method': 'wB97X-D4',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'censo-lite': {
                'stages': {
                    'crest': True,
                    'clustering': True,
                    'optimization': False,
                    'frequency': False,
                    'single_point': True,
                    'shermo': False,
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
                    'final_sp_method': 'wB97X-D4',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
            'censo-full': {
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
                    'final_sp_method': 'wB97X-D4',
                    'final_sp_basis': 'def2-TZVPP',
                },
            },
        },
        'cluster': {
            'enabled': False,
            'type': 'local',
            'queue': 'normal',
            'walltime': '24:00',
            'extra_flags': ''
        },
        'optimization_control': {
            'timeout': {
                'default_seconds': 86400
            }
        }
    }


def _merge_configs(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
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


def _set_nested(config: Dict[str, Any], keys: List[str], value: Any) -> None:
    """Set a value at a nested key path, creating intermediate dicts as needed.
    
    Args:
        config: The configuration dictionary to modify in place.
        keys: List of string keys forming the path.
        value: The value to set at the leaf.
    """
    for k in keys[:-1]:
        config = config.setdefault(k, {})
    config[keys[-1]] = value


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply environment variable overrides.
    
    Environment variables:
    - CONFSEARCH_NPROC
    - CONFSEARCH_MEM
    - CONFSEARCH_GAUSSIAN_PATH
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
        'CONFSEARCH_GAUSSIAN_PATH': ('executables', ['gaussian', 'path'], str),
        'CONFSEARCH_ORCA_PATH': ('executables', ['orca', 'path'], str),
        'CONFSEARCH_XTB_PATH': ('executables', ['xtb', 'path'], str),
        'CONFSEARCH_CREST_PATH': ('executables', ['crest', 'path'], str),
        'CONFSEARCH_ISOSTAT_PATH': ('executables', ['isostat', 'path'], str),
        'CONFSEARCH_SHERMO_PATH': ('executables', ['shermo', 'path'], str),
        'CONFSEARCH_PROTOCOL': ('protocols', 'default', str),
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
    
    return config


def _validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
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
    if protocol not in (
        'ext',
        'censo-zero',
        'censo-lite',
        'censo-full',
        'censo-full-safe',
        'allopt',
        'reference-sp',
    ):
        logger.warning(f"Unknown protocol '{protocol}', using 'ext'")
        config['protocols']['default'] = 'ext'
    
    valid_engines = ('gaussian', 'orca')
    theory = config.setdefault('theory', {})
    
    opt_engine = theory.setdefault('optimization', {}).get('engine', 'gaussian')
    if opt_engine not in valid_engines:
        logger.warning(f"Invalid optimization engine '{opt_engine}', defaulting to 'gaussian'")
        theory['optimization']['engine'] = 'gaussian'
    
    freq_engine = theory.setdefault('frequency', {}).get('engine', 'gaussian')
    if freq_engine not in valid_engines:
        logger.warning(f"Invalid frequency engine '{freq_engine}', defaulting to 'gaussian'")
        theory['frequency']['engine'] = 'gaussian'

    nmr_engine = theory.setdefault('nmr', {}).get('engine', 'gaussian')
    if nmr_engine not in valid_engines:
        logger.warning(f"Invalid NMR engine '{nmr_engine}', defaulting to 'gaussian'")
        theory['nmr']['engine'] = 'gaussian'

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

    return config


def save_config(config: Dict[str, Any], output_path: Path):
    """
    Save configuration to YAML file atomically.
    
    Args:
        config: Configuration dictionary
        output_path: Output file path
    """
    try:
        tmp = tempfile.NamedTemporaryFile(
            dir=str(output_path.parent),
            suffix='.tmp',
            delete=False,
            mode='w',
            encoding='utf-8'
        )
        yaml.dump(config, tmp, default_flow_style=False, sort_keys=False)
        tmp.close()
        os.replace(tmp.name, str(output_path))
    except Exception as e:
        logger.warning(f"Failed to save config: {e}")
