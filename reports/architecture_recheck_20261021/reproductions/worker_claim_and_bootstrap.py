from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib, json, multiprocessing as mp, os
from acp_services.worker_bootstrap import main
from acp_services.runtime import attempt_freeze as af
from acp_services.runtime.worker import build_receipt, TaskExecutionOutcome, write_receipt_atomic
from acp_services.runtime.execution_right import ExecutionRightState
from tests.test_services_worker_bootstrap import _plan, _envelope, _FakeExecutionRight

def contender(path, envelope, seen, go, result, finish):
    original = af._holder_alive
    def held(claim):
        verdict = original(claim)
        seen.set()
        go.wait(10)
        return verdict
    af._holder_alive = held
    port = af.AttemptLockExecutionRight(Path(path))
    verdict = port.acquire(envelope)
    result.put({'pid': os.getpid(), 'verdict': verdict.state.value})
    finish.wait(10)

if __name__ == '__main__':
  with TemporaryDirectory(prefix='acp-worker-review-') as d:
    root = Path(d)
    env = _envelope(_plan())
    receipt = root / 'execution_receipt.json'
    write_receipt_atomic(receipt, build_receipt(env, TaskExecutionOutcome(state='completed', result_digest='existing-valid-result')))
    before = hashlib.sha256(receipt.read_bytes()).hexdigest()
    right = _FakeExecutionRight(acquire=ExecutionRightState.NOT_HELD)
    rc = main(['--envelope',str(root/'absent'/'envelope.json'),'--plan',str(root/'absent'/'plan.json'),'--work-dir',str(root),'--receipt',str(receipt),'--require-frozen-group','--attempt-lock'], execution_right=right)
    print('UNBOUND_OVERWRITE', {'exit':rc,'receipt_changed':before!=hashlib.sha256(receipt.read_bytes()).hexdigest(),'execution_right_calls':right.calls,'receipt':json.loads(receipt.read_text())})
    lock = root/'claim.lock'
    old = af.AttemptLockClaim.for_envelope(env, lease_seconds=10).to_dict()
    old['pid'] = 2147483647
    old['pid_starttime'] = ''
    lock.write_text(json.dumps(old))
    ctx = mp.get_context('fork')
    seenA,seenB,goA,goB,finish = [ctx.Event() for _ in range(5)]
    out = ctx.Queue()
    a = ctx.Process(target=contender,args=(str(lock),env,seenA,goA,out,finish))
    b = ctx.Process(target=contender,args=(str(lock),env,seenB,goB,out,finish))
    a.start(); b.start()
    assert seenA.wait(10) and seenB.wait(10)
    goA.set()
    first = out.get(timeout=10)
    goB.set()
    second = out.get(timeout=10)
    print('RECOVERY_RACE', {'first':first,'second':second,'first_alive':a.is_alive(),'second_alive':b.is_alive(),'final_lock_pid':af.read_attempt_lock(lock).pid})
    finish.set(); a.join(); b.join()
