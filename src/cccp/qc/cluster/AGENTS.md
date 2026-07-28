# qc/cluster/ — Cluster Adapters

## OVERVIEW
Job execution environment adapters. Factory pattern: abstract base + concrete implementations (local, LSF). 4 files, ~530 lines.

## STRUCTURE
```
__init__.py   (51  lines)  — Re-exports + create_cluster_adapter() factory.
                            Sole remaining __init__.py with implementation
                            after Phase 1 cleanup.
base.py       (103 lines)  — ClusterAdapterBase ABC, JobStatus dataclass.
                            Contract: submit_job, get_status, cancel_job,
                            wait_for_completion.
local.py      (169 lines)  — LocalClusterAdapter. Direct subprocess execution.
                            Default adapter. Tracks processes via Popen dict.
lsf.py        (207 lines)  — LSFClusterAdapter. bsub/bjobs/bkill wrappers.
                            Generates #BSUB preamble scripts. Placeholder.
```

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Add SLURM/PBS adapter | Create + register in `__init__.py` | Follow `ClusterAdapterBase` contract |
| Debug local exec | `local.py` | Shell=True, scripts in /tmp |
| LSF integration | `lsf.py` | Stub — needs production hardening |

## ANTI-PATTERNS
- **`__init__.py` has factory code** — `create_cluster_adapter()` is the last implementation in any `__init__.py` post-Phase 1. Belongs in a separate module.
- **LSF adapter is placeholder** — Hardcoded /tmp paths, no retry logic, no error recovery, shell=True with bsub redirect.
- **SLURM/PBS missing** — Only 2 of 4 expected adapters exist. Codebase references SLURM/PBS config keys that have no implementation.
- **local.py writes to /tmp with predictable names** — Race condition under concurrent usage.
- **No connection pooling or reuse** — Every submission creates a fresh subprocess.
