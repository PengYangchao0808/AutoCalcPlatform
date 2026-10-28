# Execution isolation capability matrix — B09-3 / todo 50 (MU-06)

**Created:** 2026-10-19. **Owner:** B lane (`src/acp_services/runtime/isolation.py`).
**Plan:** `.omo/plans/acp-parallel-plan-2-services.md` todo 50; main plan §22.5 / MU-06 / OS-06.
**Parsed by:** `tests/test_services_isolation.py::test_capability_matrix_file_matches_code`
(rows below must stay identical to the `capability_matrix()` of the code adapters).

Three-state evidence rule: PASS / FAIL / **NOT_VERIFIED** are distinct. `measured` means the
capability was actually probed/exercised in a real environment; `configured` means present but the
enforcement path was not exercised; `not-verified` / `unavailable` mean the environment could not
demonstrate it and the capability is **not** counted toward enablement.

## Table (adapter × mechanism × status × method)

| adapter | mechanism | status | method |
|---|---|---|---|
| local | os-identity | measured | identity map present (per-principal restricted uid/gid) or privileged broker create_os_identity; NOT_VERIFIED without a configured map |
| local | cgroup-v2 | measured | cgroup v2 detected at `/sys/fs/cgroup` and delegated subtree writable (mkdir/rmdir probe); limits bound to envelope process group via cgroup.procs |
| local | sandbox | not-verified | controlled sandbox runtime configured but start not exercised; unavailable when no runtime on PATH |
| local | minimal-mounts | measured | `minimal_mount_policy()` rejects broad/secrets mounts; workdir rw, code release ro, data bindings ro |
| local | same-uid-directory | unavailable | same UID with per-directory roots — NOT isolation (never counted) |
| local | omp-num-threads | unavailable | OMP_NUM_THREADS only — NOT isolation (never counted) |
| local | login-page | unavailable | login page only — NOT isolation (never counted) |
| lsf | lsf-identity | configured | persistent principal→account/queue/resource-policy map; no arbitrary client-supplied username; live cluster enforcement NOT_VERIFIED |
| lsf | minimal-mounts | measured | `minimal_mount_policy()` persisted in the binding; workdir rw, code release ro |
| lsf | lsf-stop-scope | measured | `StopScope.permits_command()` — numeric LSF id + own account only; foreign id/account, superstring id and path-based id rejected |

## Per-tier measured record

* **local-single** — no isolation required (`NO_ISOLATION_REQUIRED`). Local adapter reports
  `minimal-mounts` measured and (on a cgroup v2 host) `cgroup-v2` measured/configured. ENABLED.
* **lab** — requires a restricted execution identity (`os-identity` or `lsf-identity`).
  In this environment no identity map / privileged broker is configured (os-identity
  `not-verified`) and no live LSF cluster is attached (lsf-identity `not-verified`), so the gate
  **REFUSES** Lab enablement (`identity_isolation_required`) — no silent downgrade.
* **hosted** — requires identity ∧ (cgroup-v2 ∨ sandbox) ∧ minimal-mounts. Refused here for the
  same missing-identity reason plus missing sandbox; refused even when cgroup-v2 is present without
  identity (`identity_isolation_required`). Resource control alone is **not** a hosted isolation
  gate.

## Explicit non-isolation (must never be counted)

Separating two principals into per-directory roots while they share one Unix UID, setting only
`OMP_NUM_THREADS`, or placing a login page in front of the service are **pseudo-isolation**
(`IsolationLevel.PSEUDO`). They can never satisfy a tier requirement and yield
`pseudo_isolation_rejected`. Same-UID directories are verified with
`audit_cross_user_access(...)` (`same_uid → isolated=False`).

## Verified negatives (tests)

* Cross-user read/write: same UID never certified; distinct UID requires restrictive mode.
* Stop scope: `bkill` / `bstop` / `bresume` address only this job's own numeric id + account; the
  live `RemoteJobMonitor` commands satisfy `StopScope.permits_shell_command(...)`.
* Refusal: missing capability ⇒ `IsolationRefusedError`; the decision reports the requested tier
  and grants no substitute tier.
