"""Run installed CCCP with real ORCA, rejecting platform imports."""
from __future__ import annotations

import importlib.abc
import importlib.util
import json
from pathlib import Path
import sys


class RejectPlatform(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'acp', 'acp_services'}:
            raise ImportError(f'Platform import forbidden: {fullname}')
        return None


assert importlib.util.find_spec('acp') is None
assert importlib.util.find_spec('acp_services') is None
sys.meta_path.insert(0, RejectPlatform())

import cccp
from cccp.calculation.context import TaskContext
from cccp.calculation.requests import MethodSpec, StructureInput, TaskKind, TaskRequest, TaskResources
from cccp.calculation import run_singlepoint
from cccp.config import load_config

workdir = Path('/tmp/acp2-acceptance-20261024/cccp-real')
workdir.mkdir(parents=True, exist_ok=True)
config = load_config()
config.setdefault('executables', {})['orca'] = {'path': '/home/xieningke/orca611/orca'}
request = TaskRequest(
    task=TaskKind.SINGLEPOINT,
    structure=StructureInput(coordinates=((0, 0, 0), (0, 0, 0.74)), symbols=('H', 'H')),
    level=MethodSpec(method='HF', basis='STO-3G'),
    resources=TaskResources(nproc=1, timeout_s=120),
    backend='orca', output_dir=workdir,
)
result = run_singlepoint(request, context=TaskContext(config=config, workdir=workdir, timeout_s=120))
facts = {
    'package': cccp.__file__, 'version': cccp.__version__,
    'complete': result.complete, 'energy_hartree': result.energy_hartree,
    'platform_modules': [n for n in sys.modules if n.split('.')[0] in {'acp', 'acp_services'}],
    'request': request.to_dict(), 'result': result.to_dict(),
}
Path(sys.argv[1]).write_text(json.dumps(facts, indent=2, default=str)+'\n')
assert result.complete and result.energy_hartree is not None
assert abs(result.energy_hartree - (-1.116759307204)) < 1e-7
assert not facts['platform_modules']
print('PASS: installed CCCP real ORCA H2, no ACP/Services installed or imported')
