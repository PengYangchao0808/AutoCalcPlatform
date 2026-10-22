from pathlib import Path
from tempfile import TemporaryDirectory
import json, os, subprocess, sys, time
from acp_services.runtime.attempt_freeze import AttemptLockExecutionRight, attempt_lock_path, read_attempt_lock
from tests.test_services_chain_switch import _envelope

with TemporaryDirectory(prefix='acp-orphan-') as d:
    root=Path(d)
    envelope=_envelope()
    (root/'env.json').write_text(json.dumps(envelope.to_dict()))
    script=r'''
from pathlib import Path
import json, os, subprocess, sys
from acp_services.contracts.envelope import ExecutionEnvelope
from acp_services.runtime.attempt_freeze import AttemptLockExecutionRight, attempt_lock_path
root=Path(sys.argv[1])
env=ExecutionEnvelope.from_dict(json.loads((root/'env.json').read_text()))
right=AttemptLockExecutionRight(attempt_lock_path(root))
assert right.acquire(env).held
# Scientific subprocess can survive abrupt worker death.  No process-group
# cleanup runs after SIGKILL/os._exit; this models an active QC child.
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
(root/'child.pid').write_text(str(child.pid))
os._exit(0)
'''
    proc=subprocess.Popen([sys.executable,'-c',script,str(root)])
    proc.wait(timeout=10)
    child_pid=int((root/'child.pid').read_text())
    try:
        os.kill(child_pid,0)
        child_alive=True
        prior=read_attempt_lock(attempt_lock_path(root))
        acquired=AttemptLockExecutionRight(attempt_lock_path(root)).acquire(envelope)
        print(json.dumps({'original_worker_exited':proc.returncode,'old_holder_pid':prior.pid,'orphan_qc_child_alive':child_alive,'orphan_qc_pid':child_pid,'successor_acquired':acquired.held,'successor_state':acquired.state.value}))
    finally:
        try: os.kill(child_pid,15)
        except ProcessLookupError: pass
