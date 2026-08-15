# qc/cluster/ — Cluster Adapters

## OVERVIEW
Job execution environment adapters. Factory pattern: abstract base + concrete implementations (local, LSF). **Consolidated into a single `__init__.py`** (12990 B) — the last remaining implementation-in-`__init__` module in cccp (root ANTI-PATTERN #5).

## STRUCTURE
```
__init__.py   (12990 B) — create_cluster_adapter() factory + ClusterAdapterBase ABC + JobStatus dataclass + LocalClusterAdapter + LSFClusterAdapter — ALL in one file
```
**Removed**: `base.py` / `local.py` / `lsf.py` were consolidated into `__init__.py` (post-Phase-1 cleanup, confirmed on-disk 2026-08-12). Earlier docs listing 4 files are stale.

## WHERE TO LOOK
| Task | File | Notes |
|------|------|-------|
| Add SLURM/PBS adapter | `__init__.py` | Follow `ClusterAdapterBase` contract (submit_job/get_status/cancel_job/wait_for_completion) |
| Factory | `__init__.py` | `create_cluster_adapter()` — returns Local or LSF adapter |
| Local execution | `__init__.py` | `LocalClusterAdapter` — direct subprocess, default adapter |
| LSF integration | `__init__.py` | `LSFClusterAdapter` — bsub/bjobs/bkill wrappers, `#BSUB` preamble scripts |

## CONVENTIONS
- Contract: `submit_job`, `get_status`, `cancel_job`, `wait_for_completion` on `ClusterAdapterBase`
- Remote LSF for the scheduler does NOT go through this layer — it uses `acp/scheduler/remote/` (SSH/SFTP + bsub) directly

## ANTI-PATTERNS
- **`__init__.py` has factory + all implementations** — single-file module violates the "no implementation in `__init__.py`" rule (root ANTI-PATTERN #5)
- **LSF adapter is placeholder** — hardcoded /tmp paths, no retry logic, no error recovery
- **SLURM/PBS missing** — codebase references SLURM/PBS config keys with no adapter implementation
- **local.py historical issues** (shell=True, /tmp predictable names) — resolved by consolidation; single __init__.py now the only surface
