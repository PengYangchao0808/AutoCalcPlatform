import json
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from acp.calculations.attempt_freeze import _build_singlepoint_request
from acp.calculations.attempt_publication import AttemptResultPublication
from acp.calculations.result_publication import load_scientific_result
from acp.results.structure_viewer import build_structure_viewer_payload
from acp.scheduler.jobs import JobRecord, JobSpec
from acp_services.runtime.worker import CccpTaskExecutor, ExecutionScope, FrozenPlan, read_task_result
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from tests.test_services_chain_switch import _T25_XYZ, _envelope, _t28_attempt


def probe_lost_science(root):
    work, snapshot, _ = _t28_attempt(root)
    (work / 'input.xyz').write_text(_T25_XYZ)
    doc = read_task_result(work)
    # The existing formal fixture supplies an actual energy (-76) and geometry.
    assert doc is not None
    assert 'energy_hartree' not in json.dumps(doc)
    assert 'coordinates' not in doc
    report = AttemptResultPublication(workflow='singlepoint').publish(snapshot)
    scientific = load_scientific_result(work / 'RESULT')
    viewer = build_structure_viewer_payload(work, job_id=snapshot.job_id,
        workflow='singlepoint', job_status='completed')
    assert scientific is not None
    assert 'energy_hartree' not in scientific.summary
    assert viewer.entries and viewer.entries[0].energy.value is None
    print(json.dumps({'case':'lost_scientific_result','input_result_energy':-76.0,
        'stored_fields':sorted(doc),
        'published_products':[(p.id, str(p.kind)) for p in report.manifest.products],
        'scientific_summary_has_energy':'energy_hartree' in scientific.summary,
        'viewer_energy':viewer.entries[0].energy.value}))


def probe_config_resources(root):
    root.mkdir()
    cfg = root / 'custom.yaml'
    cfg.write_text('resources:\n  nproc: 3\n  mem: 2GB\nexecutables:\n  orca:\n    path: /tmp/acp-review-custom-orca\n')
    inp = root / 'input.xyz'
    inp.write_text(_T25_XYZ)
    record = JobRecord(id='review-config', spec=JobSpec(workflow='singlepoint',
        name='review-config', input={'charge':0,'multiplicity':1},
        config_path=str(cfg), resources={'nproc':3,'mem':'2GB'}))
    context, request, task = _build_singlepoint_request(record, root, inp)
    assert context.config['resources']['nproc'] == 3
    assert context.config['executables']['orca']['path'] == '/tmp/acp-review-custom-orca'
    captured = {}
    def scientific_runner(request, *, context):
        captured['config'] = context.config
        captured['resources'] = request.resources.to_dict()
        return TaskResult(task=TaskKind.SINGLEPOINT,status='completed',complete=True,
            energy_hartree=-76.0,coordinates=((0.,0.,0.),(.96,0.,0.),(-.24,.93,0.)),
            symbols=('O','H','H'))
    scope = ExecutionScope(envelope=_envelope(), plan=FrozenPlan(payload=task.to_dict()),
        work_dir=root)
    with patch('cccp.calculation.run_singlepoint', scientific_runner):
        outcome = CccpTaskExecutor().execute(scope)
    assert captured['config'] is None
    assert captured['resources'].get('nproc') is None
    assert captured['resources'].get('mem') is None
    print(json.dumps({'case':'config_resources_lost','requested_resources':record.spec.resources,
        'builder_config_resolved':True,'worker_context_config':captured['config'],
        'worker_task_resources':captured['resources'],'receipt_state':outcome.state}))


root = Path(tempfile.mkdtemp(prefix='acp-controller-review-'))
probe_lost_science(root)
probe_config_resources(root / 'config-case')
