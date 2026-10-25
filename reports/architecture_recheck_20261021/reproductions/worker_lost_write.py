from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from acp_services.worker_bootstrap import assemble_worker, freeze_frozen_plan
from acp_services.runtime.execution_right import ExecutionRightState
from acp_services.runtime.worker import CccpTaskExecutor, FrozenStep, task_result_path
from cccp.calculation.requests import TaskKind
from cccp.calculation.results import TaskResult
from tests.test_services_worker_bootstrap import _envelope,_binding,_FakeExecutionRight
with TemporaryDirectory(prefix='acp-lost-write-') as d:
    root=Path(d)
    right=_FakeExecutionRight(acquire=ExecutionRightState.HELD,verify=ExecutionRightState.LOST)
    plan = freeze_frozen_plan(payload={'task':'singlepoint','charge':0,'multiplicity':1,
        'structure':{'coordinates':[[0.,0.,0.],[.96,0.,0.],[-.24,.93,0.]],'symbols':['O','H','H']}},
        steps=(FrozenStep(kind='singlepoint',payload={'task':'singlepoint'}),),
        item_symbols=('O','H','H'),plan_identity='v2:review-singlepoint',step_identities=('v2:review-step',))
    runtime=assemble_worker(envelope=_envelope(plan),plan=plan,work_dir=root,receipt_path=root/'execution_receipt.json',storage_binding=_binding(),executor=CccpTaskExecutor(),execution_right=right)
    result = TaskResult(task=TaskKind.SINGLEPOINT,status='completed',complete=True,
        energy_hartree=-76.,coordinates=((0.,0.,0.),(.96,0.,0.),(-.24,.93,0.)),symbols=('O','H','H'))
    with patch('cccp.calculation.run_singlepoint', return_value=result):
        outcome=runtime.run()
    print({'result_type':type(outcome).__name__,'result_code':outcome.code,'claim_checks':right.calls,'receipt_exists':runtime.receipt_path.exists(),'authoritative_task_result_exists':task_result_path(root).exists()})
