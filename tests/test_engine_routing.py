"""
Tests for Engine Routing
========================

Tests for opt/freq engine selection refactor.
"""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
import numpy as np

from conformer_search.core.engine import ConformerEngine
from conformer_search.core.protocols import (
    ProtocolSpec,
    resolve_protocol_spec,
    FunnelPolicy,
    HandoffPolicy,
)
from conformer_search.qc.interfaces import GaussianInterface, ORCAInterface
from conformer_search.qc.interfaces.base import QCResult


# ---------------------------------------------------------------------------
# Minimal valid config for ConformerEngine construction (no subprocess calls)
# ---------------------------------------------------------------------------

def _make_min_config(**overrides):
    """Return a minimal valid config dict that won't trigger subprocess calls."""
    config = {
        'executables': {
            'gaussian': {'path': 'g16'},
            'orca': {'path': 'orca'},
            'crest': {'path': 'crest'},
            'xtb': {'path': 'xtb'},
            'isostat': {'path': 'isostat'},
            'shermo': {'path': 'Shermo'},
        },
        'resources': {'nproc': 1, 'mem': '1GB'},
        'theory': {
            'optimization': {
                'engine': 'gaussian',
                'method': 'B3LYP',
                'basis': 'def2-SVP',
                'dispersion': 'GD3BJ',
            },
            'frequency': {'engine': 'gaussian'},
            'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
            'preoptimization': {'gfn_level': 2},
        },
        'thermo': {'temperature_k': 298.15},
        'protocols': {
            'ext': {
                'two_stage_enabled': True,
                'ngeom_default': 1,
                'ngeom_max': 1,
                'funnel': {
                    'search_mode': 'crest_gfn2',
                    'clustering_mode': 'isostat',
                    'prescreen_mode': 'none',
                    'rerank_mode': 'none',
                },
                'handoff': {
                    'enabled': True,
                    'mode': 'optimize_rank1',
                    'ranking_after_handoff': 'final_sp_minimum',
                },
            }
        },
    }
    for key, val in overrides.items():
        if isinstance(val, dict) and isinstance(config.get(key), dict):
            config[key].update(val)
        else:
            config[key] = val
    return config


def _make_engine(tmp_path, config=None, protocol='ext', **kwargs):
    """Create a ConformerEngine with a minimal config."""
    cfg = config if config is not None else _make_min_config()
    return ConformerEngine(
        config=cfg,
        work_dir=tmp_path,
        molecule_name='test_mol',
        protocol=protocol,
        **kwargs,
    )


# ===========================================================================
# Test _create_qc_interface() — engine string → interface class
# ===========================================================================

class TestCreateQcInterface:
    """Test _create_qc_interface() returns correct interface class per engine."""

    def _make_config_with_executables(self, gaussian_path='g16', orca_path='orca'):
        return _make_min_config(
            executables={
                'gaussian': {'path': gaussian_path},
                'orca': {'path': orca_path},
                'crest': {'path': 'crest'},
                'xtb': {'path': 'xtb'},
                'isostat': {'path': 'isostat'},
                'shermo': {'path': 'Shermo'},
            },
        )

    def test_create_qc_interface_returns_gaussian_for_gaussian_key(self, tmp_path):
        """_create_qc_interface('gaussian') returns a GaussianInterface."""
        config = self._make_config_with_executables()
        engine = _make_engine(tmp_path, config=config)
        result = engine._create_qc_interface('gaussian', {
            'method': 'B3LYP', 'basis': 'def2-SVP',
        })
        assert isinstance(result, GaussianInterface)

    def test_create_qc_interface_returns_orca_for_orca_key(self, tmp_path):
        """_create_qc_interface('orca') returns an ORCAInterface."""
        config = self._make_config_with_executables()
        engine = _make_engine(tmp_path, config=config)
        result = engine._create_qc_interface('orca', {
            'method': 'B3LYP', 'basis': 'def2-SVP',
        })
        assert isinstance(result, ORCAInterface)

    def test_create_qc_interface_unknown_engine_raises_valueerror(self, tmp_path):
        """_create_qc_interface('unknown') raises ValueError."""
        config = self._make_config_with_executables()
        engine = _make_engine(tmp_path, config=config)
        with pytest.raises(ValueError, match="Unknown engine"):
            engine._create_qc_interface('nonexistent', {})


# ===========================================================================
# Test _setup_qc_interfaces() — protocol_spec → opt_interface / freq_interface
# ===========================================================================

class TestSetupQcInterfaces:
    """Test _setup_qc_interfaces() sets correct interfaces from protocol_spec."""

    def _make_config_theory_engine(self, opt_engine='gaussian', freq_engine='gaussian'):
        return _make_min_config(
            theory={
                'optimization': {
                    'engine': opt_engine,
                    'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
                },
                'frequency': {'engine': freq_engine},
                'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
                'preoptimization': {'gfn_level': 2},
            },
        )

    def test_protocol_opt_gaussian_creates_gaussian_opt_interface(self, tmp_path):
        """When protocol_spec.opt_engine='gaussian', opt_interface is GaussianInterface."""
        spec = ProtocolSpec(
            name='ext',
            opt_engine='gaussian',
            freq_engine='gaussian',
            ngeom_max=1,
            ngeom_default=1,
            two_stage_enabled=False,
            funnel_policy=FunnelPolicy(),
            handoff_policy=HandoffPolicy(),
        )
        config = self._make_config_theory_engine('gaussian', 'gaussian')
        engine = _make_engine(tmp_path, config=config, protocol_spec=spec)
        assert isinstance(engine.opt_interface, GaussianInterface)

    def test_protocol_opt_orca_creates_orca_opt_interface(self, tmp_path):
        """When protocol_spec.opt_engine='orca', opt_interface is ORCAInterface."""
        spec = ProtocolSpec(
            name='ext',
            opt_engine='orca',
            freq_engine='orca',
            ngeom_max=1,
            ngeom_default=1,
            two_stage_enabled=False,
            funnel_policy=FunnelPolicy(),
            handoff_policy=HandoffPolicy(),
        )
        config = self._make_config_theory_engine('gaussian', 'gaussian')
        engine = _make_engine(tmp_path, config=config, protocol_spec=spec)
        assert isinstance(engine.opt_interface, ORCAInterface)
        assert isinstance(engine.freq_interface, ORCAInterface)

    def test_protocol_mixed_engines_opt_gaussian_freq_orca(self, tmp_path):
        """Mixed: protocol opt=gaussian freq=orca results in correct mixed interfaces."""
        spec = ProtocolSpec(
            name='ext',
            opt_engine='gaussian',
            freq_engine='orca',
            ngeom_max=1,
            ngeom_default=1,
            two_stage_enabled=False,
            funnel_policy=FunnelPolicy(),
            handoff_policy=HandoffPolicy(),
        )
        config = self._make_config_theory_engine('gaussian', 'gaussian')
        engine = _make_engine(tmp_path, config=config, protocol_spec=spec)
        assert isinstance(engine.opt_interface, GaussianInterface)
        assert isinstance(engine.freq_interface, ORCAInterface)

    def test_protocol_mixed_engines_opt_orca_freq_gaussian(self, tmp_path):
        """Mixed: protocol opt=orca freq=gaussian results in correct mixed interfaces."""
        spec = ProtocolSpec(
            name='ext',
            opt_engine='orca',
            freq_engine='gaussian',
            ngeom_max=1,
            ngeom_default=1,
            two_stage_enabled=False,
            funnel_policy=FunnelPolicy(),
            handoff_policy=HandoffPolicy(),
        )
        config = self._make_config_theory_engine('gaussian', 'gaussian')
        engine = _make_engine(tmp_path, config=config, protocol_spec=spec)
        assert isinstance(engine.opt_interface, ORCAInterface)
        assert isinstance(engine.freq_interface, GaussianInterface)


# ===========================================================================
# Test backward compatibility — defaults to gaussian
# ===========================================================================

class TestBackwardCompatibility:
    """Test backward compatibility: default engine is gaussian."""

    def test_default_opt_is_gaussian_when_no_engine_specified(self, tmp_path):
        """When neither config theory nor protocol specifies engine, defaults to gaussian."""
        config = _make_min_config()
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'gaussian'
        assert spec.freq_engine == 'gaussian'

    def test_opt_falls_back_to_theory_engine(self, tmp_path):
        """When protocol has no opt_engine, resolve_protocol_spec uses theory engine."""
        config = _make_min_config(theory={
            'optimization': {
                'engine': 'orca',
                'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
            },
            'frequency': {'engine': 'orca'},
            'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
            'preoptimization': {'gfn_level': 2},
        })
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'orca'
        assert spec.freq_engine == 'orca'

    def test_hardcoded_gaussian_when_nothing_specified(self, tmp_path):
        """When neither protocol nor theory config has engine, hardcoded 'gaussian'."""
        config = _make_min_config(theory={})
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'gaussian'
        assert spec.freq_engine == 'gaussian'


# ===========================================================================
# Test protocol override precedence
# ===========================================================================

class TestProtocolOverride:
    """Test protocol-level engine override takes precedence."""

    def test_protocol_opt_engine_overrides_theory_level(self, tmp_path):
        """Protocol opt_engine='orca' overrides config theory.optimization.engine='gaussian'."""
        config = _make_min_config(
            theory={
                'optimization': {
                    'engine': 'gaussian',
                    'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
                },
                'frequency': {'engine': 'gaussian'},
                'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
                'preoptimization': {'gfn_level': 2},
            },
            protocols={
                'ext': {
                    'opt_engine': 'orca',
                    'freq_engine': 'orca',
                    'two_stage_enabled': True,
                    'ngeom_default': 1,
                    'ngeom_max': 1,
                    'funnel': {
                        'search_mode': 'crest_gfn2',
                        'clustering_mode': 'isostat',
                        'prescreen_mode': 'none',
                        'rerank_mode': 'none',
                    },
                    'handoff': {
                        'enabled': True,
                        'mode': 'optimize_rank1',
                        'ranking_after_handoff': 'final_sp_minimum',
                    },
                }
            },
        )
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'orca'
        assert spec.freq_engine == 'orca'

    def test_protocol_freq_engine_overrides_theory_level(self, tmp_path):
        """Protocol freq_engine='orca' overrides config theory.frequency.engine='gaussian'."""
        config = _make_min_config(
            theory={
                'optimization': {
                    'engine': 'gaussian',
                    'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
                },
                'frequency': {'engine': 'gaussian'},
                'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
                'preoptimization': {'gfn_level': 2},
            },
            protocols={
                'ext': {
                    'opt_engine': 'gaussian',
                    'freq_engine': 'orca',
                    'two_stage_enabled': True,
                    'ngeom_default': 1,
                    'ngeom_max': 1,
                    'funnel': {
                        'search_mode': 'crest_gfn2',
                        'clustering_mode': 'isostat',
                        'prescreen_mode': 'none',
                        'rerank_mode': 'none',
                    },
                    'handoff': {
                        'enabled': True,
                        'mode': 'optimize_rank1',
                        'ranking_after_handoff': 'final_sp_minimum',
                    },
                }
            },
        )
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'gaussian'
        assert spec.freq_engine == 'orca'


# ===========================================================================
# Test opt failure recovery — candidate still appended with opt energy
# ===========================================================================

class TestOptFailureRecovery:
    """Test that opt failure still appends a candidate (no crash)."""

    def test_opt_failure_appends_candidate_with_opt_energy(self, tmp_path):
        """When optimize() fails, candidate appended with opt_energy, no thermo."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        symbols = ['C', 'H', 'H']

        failed_opt = QCResult(
            success=False,
            energy=-40.0,
            coordinates=coords,
            symbols=symbols,
            log_file=tmp_path / 'opt.log',
        )
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = failed_opt

        engine.freq_interface = MagicMock()
        engine.sp_interface = MagicMock()
        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                candidate_set = engine._run_shared_dft_handoff([fake_path])

        assert len(candidate_set.candidates) == 1
        cand = candidate_set.candidates[0]
        assert cand.energy == -40.0
        assert cand.gibbs_energy is None
        assert cand.gibbs_correction is None
        assert cand.h_correction is None
        assert cand.u_correction is None
        assert cand.s_total is None
        assert cand.g_conc is None
        engine.freq_interface.frequency.assert_not_called()
        engine.sp_interface.single_point.assert_not_called()

    def test_opt_failure_does_not_crash_with_empty_energy(self, tmp_path):
        """Opt failure with None energy still appends candidate (energy=inf sentinel)."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0]])
        symbols = ['C']

        failed_opt = QCResult(success=False, energy=None, coordinates=coords, symbols=symbols)
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = failed_opt
        engine.freq_interface = MagicMock()
        engine.sp_interface = MagicMock()
        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                candidate_set = engine._run_shared_dft_handoff([fake_path])

        assert len(candidate_set.candidates) == 1
        cand = candidate_set.candidates[0]
        assert cand.energy == float('inf')


# ===========================================================================
# Test freq failure recovery — None thermo fields, candidate still appended
# ===========================================================================

class TestFreqFailureRecovery:
    """Test that freq failure produces None thermo fields but still appends candidate."""

    def test_freq_failure_yields_none_thermo_fields(self, tmp_path):
        """When frequency() fails, thermo fields are None but candidate appended."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        symbols = ['C', 'H', 'H']

        opt_ok = QCResult(
            success=True,
            energy=-40.5,
            coordinates=coords,
            symbols=symbols,
            log_file=tmp_path / 'opt.log',
        )
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = opt_ok

        freq_fail = QCResult(success=False, error_message='freq failed')
        engine.freq_interface = MagicMock()
        engine.freq_interface.frequency.return_value = freq_fail

        sp_fail = QCResult(success=False, energy=None)
        engine.sp_interface = MagicMock()
        engine.sp_interface.single_point.return_value = sp_fail

        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                candidate_set = engine._run_shared_dft_handoff([fake_path])

        assert len(candidate_set.candidates) == 1
        cand = candidate_set.candidates[0]
        assert cand.energy == -40.5
        assert cand.gibbs_energy is None
        assert cand.gibbs_correction is None
        assert cand.h_correction is None
        assert cand.u_correction is None
        assert cand.s_total is None
        assert cand.g_conc is None

    def test_freq_failure_shermo_not_called(self, tmp_path):
        """When freq fails, single_point is called but Shermo is not."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0]])
        symbols = ['C']

        opt_ok = QCResult(
            success=True,
            energy=-40.5,
            coordinates=coords,
            symbols=symbols,
            log_file=tmp_path / 'opt.log',
        )
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = opt_ok

        freq_fail = QCResult(success=False)
        engine.freq_interface = MagicMock()
        engine.freq_interface.frequency.return_value = freq_fail

        sp_fail = QCResult(success=False, energy=None)
        engine.sp_interface = MagicMock()
        engine.sp_interface.single_point.return_value = sp_fail

        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                with patch('conformer_search.core.engine.run_shermo') as mock_shermo:
                    engine._run_shared_dft_handoff([fake_path])

        engine.sp_interface.single_point.assert_called_once()
        mock_shermo.assert_not_called()


# ===========================================================================
# Test full success path — all stages pass
# ===========================================================================

class TestFullSuccessPath:
    """Test full success path: opt → freq → sp → shermo → candidate with thermo."""

    def test_full_success_populates_all_fields(self, tmp_path):
        """When all steps succeed, candidate has full thermo data."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        symbols = ['C', 'H', 'H']

        opt_ok = QCResult(
            success=True,
            energy=-40.5,
            coordinates=coords,
            symbols=symbols,
            log_file=tmp_path / 'opt.log',
        )
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = opt_ok

        freq_ok = QCResult(
            success=True,
            log_file=tmp_path / 'freq.log',
        )
        engine.freq_interface = MagicMock()
        engine.freq_interface.frequency.return_value = freq_ok

        sp_ok = QCResult(
            success=True,
            energy=-40.55,
            log_file=tmp_path / 'sp.out',
        )
        engine.sp_interface = MagicMock()
        engine.sp_interface.single_point.return_value = sp_ok

        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        shermo_returns = {
            'g_sum': -40.35,
            'g_conc': -40.40,
            'h_sum': -40.38,
            'u_sum': -40.39,
            's_total': 0.01,
        }

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                with patch('conformer_search.core.engine.run_shermo', return_value=shermo_returns):
                    candidate_set = engine._run_shared_dft_handoff([fake_path])

        assert len(candidate_set.candidates) == 1
        cand = candidate_set.candidates[0]
        assert cand.energy == -40.55
        assert cand.gibbs_energy == -40.40
        assert cand.gibbs_correction == pytest.approx(-40.35, abs=1e-6)
        assert cand.h_correction == -40.38
        assert cand.u_correction == -40.39
        assert cand.s_total == 0.01
        assert cand.g_conc == -40.40

    def test_shermo_returns_none_no_thermo_fields(self, tmp_path):
        """When run_shermo returns None, thermo fields remain None."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0]])
        symbols = ['C']

        opt_ok = QCResult(
            success=True, energy=-40.0, coordinates=coords,
            symbols=symbols, log_file=tmp_path / 'opt.log',
        )
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = opt_ok

        freq_ok = QCResult(success=True, log_file=tmp_path / 'freq.log')
        engine.freq_interface = MagicMock()
        engine.freq_interface.frequency.return_value = freq_ok

        sp_ok = QCResult(success=True, energy=-40.1, log_file=tmp_path / 'sp.out')
        engine.sp_interface = MagicMock()
        engine.sp_interface.single_point.return_value = sp_ok

        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                with patch('conformer_search.core.engine.run_shermo', return_value=None):
                    candidate_set = engine._run_shared_dft_handoff([fake_path])

        assert len(candidate_set.candidates) == 1
        cand = candidate_set.candidates[0]
        assert cand.energy == -40.1
        assert cand.gibbs_energy is None
        assert cand.gibbs_correction is None
        assert cand.h_correction is None

    def test_sp_failure_falls_back_to_opt_energy(self, tmp_path):
        """When SP fails, candidate energy is from opt, thermo fields None."""
        config = _make_min_config()
        engine = _make_engine(tmp_path, config=config)
        engine._current_charge = 0
        engine._current_multiplicity = 1

        coords = np.array([[0.0, 0.0, 0.0]])
        symbols = ['C']

        opt_ok = QCResult(
            success=True, energy=-40.0, coordinates=coords,
            symbols=symbols, log_file=tmp_path / 'opt.log',
        )
        engine.opt_interface = MagicMock()
        engine.opt_interface.optimize.return_value = opt_ok

        freq_ok = QCResult(success=True, log_file=tmp_path / 'freq.log')
        engine.freq_interface = MagicMock()
        engine.freq_interface.frequency.return_value = freq_ok

        sp_fail = QCResult(success=False, energy=None)
        engine.sp_interface = MagicMock()
        engine.sp_interface.single_point.return_value = sp_fail

        engine.state_manager.set_stage = MagicMock()
        engine.state_manager.complete_stage = MagicMock()

        fake_path = tmp_path / 'fake.xyz'
        fake_path.write_text('')

        with patch('conformer_search.core.engine.read_xyz', return_value=(coords, symbols)):
            with patch('conformer_search.core.engine.ensure_dir'):
                candidate_set = engine._run_shared_dft_handoff([fake_path])

        assert len(candidate_set.candidates) == 1
        cand = candidate_set.candidates[0]
        assert cand.energy == -40.0
        assert cand.gibbs_energy is None
        assert cand.gibbs_correction is None


# ===========================================================================
# Test resolve_protocol_spec engine resolution edge cases
# ===========================================================================

class TestProtocolResolutionEngine:
    """Test resolve_protocol_spec engine resolution edge cases."""

    def test_protocol_level_explicit_engine_blocks_theory_fallback(self, tmp_path):
        """Protocol-level opt_engine='gaussian' is used even when theory says 'orca'."""
        config = _make_min_config(
            theory={
                'optimization': {
                    'engine': 'orca',
                    'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
                },
                'frequency': {'engine': 'orca'},
                'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
                'preoptimization': {'gfn_level': 2},
            },
            protocols={
                'ext': {
                    'opt_engine': 'gaussian',
                    'freq_engine': 'gaussian',
                    'two_stage_enabled': True,
                    'ngeom_default': 1,
                    'ngeom_max': 1,
                    'funnel': {
                        'search_mode': 'crest_gfn2',
                        'clustering_mode': 'isostat',
                        'prescreen_mode': 'none',
                        'rerank_mode': 'none',
                    },
                    'handoff': {
                        'enabled': True,
                        'mode': 'optimize_rank1',
                        'ranking_after_handoff': 'final_sp_minimum',
                    },
                }
            },
        )
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'gaussian'
        assert spec.freq_engine == 'gaussian'

    def test_freq_engine_missing_uses_theory_frequency_engine(self, tmp_path):
        """When protocol has no freq_engine, theory.frequency.engine is used."""
        config = _make_min_config(
            theory={
                'optimization': {
                    'engine': 'gaussian',
                    'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
                },
                'frequency': {'engine': 'orca'},
                'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
                'preoptimization': {'gfn_level': 2},
            },
            protocols={
                'ext': {
                    'opt_engine': 'gaussian',
                    'two_stage_enabled': True,
                    'ngeom_default': 1,
                    'ngeom_max': 1,
                    'funnel': {
                        'search_mode': 'crest_gfn2',
                        'clustering_mode': 'isostat',
                        'prescreen_mode': 'none',
                        'rerank_mode': 'none',
                    },
                    'handoff': {
                        'enabled': True,
                        'mode': 'optimize_rank1',
                        'ranking_after_handoff': 'final_sp_minimum',
                    },
                }
            },
        )
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.freq_engine == 'orca'

    def test_both_engines_missing_hardcoded_gaussian(self, tmp_path):
        """When neither protocol nor theory has engine, both hardcoded to 'gaussian'."""
        config = _make_min_config(theory={})
        spec = resolve_protocol_spec(config, 'ext')
        assert spec.opt_engine == 'gaussian'
        assert spec.freq_engine == 'gaussian'

    def test_reference_sp_protocol_default_engines_are_gaussian(self, tmp_path):
        """reference-sp protocol defaults to gaussian when nothing specified."""
        config = _make_min_config(
            theory={
                'optimization': {
                    'engine': 'gaussian',
                    'method': 'B3LYP', 'basis': 'def2-SVP', 'dispersion': 'GD3BJ',
                },
                'frequency': {'engine': 'gaussian'},
                'single_point': {'method': 'M062X', 'basis': 'def2-TZVPP'},
                'preoptimization': {'gfn_level': 2},
            },
        )
        spec = resolve_protocol_spec(config, 'reference-sp')
        assert spec.opt_engine == 'gaussian'
        assert spec.freq_engine == 'gaussian'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
