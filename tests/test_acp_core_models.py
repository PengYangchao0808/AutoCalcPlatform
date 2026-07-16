"""
Tests for ACP Core Models and State
==================================
"""

import json
from pathlib import Path

import numpy as np

from acp.core.models import Structure, StructureEnsemble, StructureRecord
from acp.core.state import EventLog, WorkflowState
from conformer_search.core import CandidateSet, ConformerCandidate


def test_structure_converts_to_legacy_candidate():
    """A generic Structure can be converted into a legacy ConformerCandidate."""
    structure = Structure(
        id='test',
        charge=1,
        multiplicity=2,
        symbols=['C'],
        coordinates=np.array([[0.0, 0.0, 0.0]]),
        metadata={'label': 'seed'},
    )

    candidate = structure.to_conformer_candidate(index=7, energy=-1.23)

    assert candidate.index == 7
    assert candidate.energy == -1.23
    assert candidate.metadata['label'] == 'seed'
    assert np.allclose(candidate.coordinates, [[0.0, 0.0, 0.0]])


def test_conformer_candidate_round_trips_through_structure_record():
    """Legacy candidates round-trip through the generic ACP record model."""
    candidate = ConformerCandidate(
        index=3,
        coordinates=np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        symbols=['C', 'H'],
        energy=-40.123456,
        weight=0.75,
        source_file=Path('conf_003.xyz'),
        rank=1,
        metadata={'charge': -1, 'multiplicity': 2, 'tag': 'legacy'},
        gibbs_energy=-40.100000,
        gibbs_correction=0.0123,
        h_correction=0.0456,
        u_correction=0.0789,
        s_total=0.0012,
        g_conc=-40.200000,
    )

    record = StructureRecord.from_conformer_candidate(candidate)
    rebuilt = record.to_conformer_candidate()

    assert record.structure.id == 'conf_003'
    assert rebuilt.index == candidate.index
    assert rebuilt.rank == candidate.rank
    assert rebuilt.source_file == candidate.source_file
    assert rebuilt.metadata == candidate.metadata
    assert rebuilt.gibbs_energy == candidate.gibbs_energy
    assert rebuilt.g_conc == candidate.g_conc
    assert np.allclose(rebuilt.coordinates, candidate.coordinates)


def test_candidate_set_round_trips_through_structure_ensemble():
    """Legacy candidate sets interoperate with the generic ACP ensemble model."""
    candidate_set = CandidateSet(
        candidates=[
            ConformerCandidate(
                index=0,
                coordinates=np.zeros((1, 3)),
                symbols=['C'],
                energy=-10.0000,
            ),
            ConformerCandidate(
                index=1,
                coordinates=np.ones((1, 3)),
                symbols=['C'],
                energy=-9.9990,
            ),
            ConformerCandidate(
                index=2,
                coordinates=np.full((1, 3), 2.0),
                symbols=['C'],
                energy=-9.9900,
            ),
        ],
        reference_energy=-10.0000,
        temperature=310.0,
    )

    ensemble = StructureEnsemble.from_candidate_set(candidate_set)
    restored = ensemble.to_candidate_set()
    selected = ensemble.window_select(energy_window_kcal=1.0)

    assert len(ensemble.records) == 3
    assert restored.reference_energy == -10.0000
    assert restored.temperature == 310.0
    assert [candidate.energy for candidate in restored.candidates] == [-10.0000, -9.9990, -9.9900]
    assert len(selected) == 2


def test_workflow_state_lifecycle(tmp_path):
    """WorkflowState supports initialize → stage updates → reload."""
    state = WorkflowState(tmp_path, 'test-job')
    state.initialize(input_source='CCO')
    state.set_stage('embed')
    state.complete_stage('embed', {'n_atoms': 3})

    reloaded = WorkflowState(tmp_path, 'test-job')
    loaded_state = reloaded.load()

    assert loaded_state is not None
    assert reloaded.is_stage_completed('embed')
    assert reloaded.get_stage_result('embed') == {'n_atoms': 3}

    reloaded.mark_completed()
    summary = reloaded.get_summary()
    assert summary['status'] == 'completed'
    assert summary['completed'] is True


def test_event_log_appends_jsonl_records(tmp_path):
    """EventLog appends timestamped JSONL events."""
    log_path = tmp_path / 'events.jsonl'
    log = EventLog(log_path)
    log.append({'event': 'started'})
    log.append({'event': 'finished'})

    events = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines()]

    assert [event['event'] for event in events] == ['started', 'finished']
    assert all('timestamp' in event for event in events)
