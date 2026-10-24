# ACP2 Plan A — Runtime Fixture Request: installed singlepoint (A-09b)

Status: **open**. This is A's request to the platform/runtime owner (Plan B,
B01/B07) for the one real execution of the frozen singlepoint fixture. It is a
request for a run key, a budget reservation and a `TaskRef`; it is **not** an
MCP submit body and it is **not** a task submission by A.

A does not send MCP JSON-RPC, does not create a Controller submission and does
not start a QC process from this document. B owns the submit body, the run key
and the `TaskRef`; A consumes the returned evidence.

## 1. Fixture identity and digests

| Field | Value |
|---|---|
| Fixture id | `singlepoint-water-wb97xd4-def2-svp` |
| Fixture root | `tests/fixtures/acp2/science/singlepoint/` |
| Manifest | `fixture-manifest.json` |
| Input | `input.xyz` — water `O,H,H`, 3 atoms, original coordinates, angstrom |
| Scientific parameters | charge `0`, multiplicity `1`, 10 electrons, closed shell; `wB97X-D4` / `def2-SVP`; energy unit `hartree` |
| Expected scenario id | **`science:installed-singlepoint`** |
| Expected harness phase | `main` |
| Energy state now | `not_measured`; `reference_energy: null` |
| Availability now | `verified_available: false` (host `/usr/bin/orca` is the GNOME screen reader, not ORCA QC) |

Digests (SHA-256, lowercase, no prefix):

| File | SHA-256 |
|---|---|
| `input.xyz` (the **fixture input digest**) | `8745d09f651973bc3acbf2d638baf1d46e28b1f0892a1b8c30becbd6de04ed1a` |
| `request.json` | `8023003a2cde5183a5a6c3797de6cc4d60d89499f5c2a470234ff87173186518` |
| `expected-science.json` | `3286884e0c6d4d824a8f39528603715f3a716b4e70d363c0964655ee1d8b9fc6` |
| `fixture-manifest.json` | `18655285de3f22ca934a2bd0a099389e689156fd424a37b89d5ef2bfe86ac186` |
| `invalid_electrons.json` (negative) | `a310564a9af91a9c693de3b19e6350cca3321d52aaf3e585f983214e5d05aad8` |
| `missing_structure.json` (negative) | `5d5ea6a3fd44b9a8c43016adbb1ae0e4a2490ffbcde2fa5b8b8028199e7504dc` |

`fixture-manifest.json::file_digests` and `::input.sha256` carry the same
input digest. The contract test
`tests/test_acp2_science_fixtures.py -k singlepoint` recomputes every digest
from the bytes and pins the input digest independently.

## 2. Run-key request

B01 has not published `acceptance-result.schema.json` / `acp2.acceptance.v1` or
the `(owner, scenario, phase, run_id)` registry, so **no run key exists yet**.
A does not invent one. A requests the following key components from B01/B07:

| Component | Requested value / rule |
|---|---|
| `owner` | `A` (science track); execution operator remains B |
| `scenario` | `science:installed-singlepoint` |
| `phase` | `main` |
| `run_id` | B-issued; not known to A |
| `fixture_id` | `singlepoint-water-wb97xd4-def2-svp` |
| `input_sha256` | `8745d09f651973bc3acbf2d638baf1d46e28b1f0892a1b8c30becbd6de04ed1a` |
| `request_sha256` | `8023003a2cde5183a5a6c3797de6cc4d60d89499f5c2a470234ff87173186518` |
| `backend` | `orca` preferred, `xtb` fallback, as frozen in the manifest; no silent substitution |
| `scheduler` | `local` or `lsf`/`remote`; one scheduler per run key |
| `budget_ref` | B-issued reservation id (see §3) |
| `evidence_owner` | `A` consumes; B owns the raw execution record |
| Reuse | **one** submission; Local, LSF, CLI and MCP/DSH consumers read the same `TaskRef` |

If B cannot issue a run key, the execution stays `NOT_VERIFIED` with the exact
gap. A will not fill `run_key` with a placeholder.

## 3. Budget request

A requests the following caps. The vendor values are A's proposal for this
small fixture; B07 owns the final budget and may only tighten them. The names
match the acceptance harness contract (`scripts/acceptance/acp2_science.py`).

| Cap | Limit | Used at request |
|---|---|---|
| `caps.total` | 1 | 0 |
| `caps.phase` (`main`) | 1 | 0 |
| `caps.scenario` (`science:installed-singlepoint`) | 1 | 0 |
| `caps.per_unit` | 1 | 0 |

| Unit count | Local | LSF |
|---|---|---|
| `counts.task_ref` | 1 | 1 |
| `counts.recipe_step` | 0 (inactive) | 0 (inactive) |
| `counts.qc_invocation` | 1 | 1 |
| `counts.attempt` | 1 | 1 |
| `counts.bsub` | 0 (inactive) | 1 |

| Deadline | Seconds |
|---|---|
| `deadlines.queue_s` | 600 |
| `deadlines.compute_s` | 900 |
| `deadlines.client_s` | 1200 |
| `deadlines.total_s` | 1800 |

The budget is consumed once. A rerun/continue of the same fixture is a new
budget decision by B, not an implicit second allowance.

## 4. Required interpreter and software fields

For the run spec and the execution record, B must supply these fields (the
first block is enforced by the harness `_validate_run_spec`):

| Field | Required content |
|---|---|
| `run_key` | B-issued non-empty key; no placeholder |
| `interpreter` | absolute path to the isolated interpreter; must report `sys.version` and the source/wheel provenance |
| `native_work_root` | absolute path on a native filesystem (not 9p/NFS/CIFS) |
| `software.name` | exactly `ORCA` or `xTB` (the binary actually executed) |
| `software.version` | exact version reported by that binary |
| `software.executable` | absolute path to the executed binary |
| `source_digest` or `wheel_digest` | `sha256:<64 lowercase hex>` of the executed source/wheel |
| `scheduler` | `local`, `lsf` or `remote` |
| `budget` | the object in §3 (`caps`, `counts`, `deadlines`) |

A also requests the platform fields that bound the execution, even though the
harness does not parse them: `resources.nproc`, `resources.maxcore`,
`resources.mem`, `resources.timeout_s`, queue/account/walltime (LSF only), and
the release root `release_id`.

## 5. What B must return

B returns a record that lets A decide the scientific scenario without rerunning
it. The minimum is:

1. **Run key** — the B01-issued `run_id` plus the registry row, or the exact
   `NOT_VERIFIED` gap.
2. **`TaskRef`** — persistent task/attempt identity, storage location, and the
   release/execution version. A `fake` task id or a path-only response is not
   acceptable.
3. **Actual environment** — backend, `software.name/version/executable`,
   interpreter, `native_work_root`, source/wheel digest and `release_id`.
4. **Budget record** — reservation id and actual consumption of every unit in
   §3; refusal before submission must say which cap/scheduler rule fired.
5. **Raw execution evidence** — the executed `TaskRequest`, native output
   (ORCA `.out`; xTB output when that fallback is used), the normalized
   `TaskResult`, the command argv array, `cwd`, `started_at`/`finished_at`, and
   `artifacts[{path,size_bytes,sha256}]`.
6. **Outcome** — terminal status; if not `completed`, the typed error/status
   reason. A normal exit code alone is not a scientific PASS.
7. **Receipt** — once B01 publishes `acp2.acceptance.v1`, the receipt for
   `science:installed-singlepoint` under that schema and no competing schema.
8. **Honesty marker** — an explicit statement of whether a real QC binary ran.
   A missing binary, a screen-reader false positive, a mock or a reused old
   output must be reported as such, never as a new measurement.

A will not accept an energy copied from another run or a `reference_energy`
invented before the run. `expected-science.json` keeps `reference_energy: null`
until B returns a real, attributable result; only then does the fixture gain a
measured reference with its tolerances.

## 6. Boundary statement

- `request.json` is a `cccp.calculation.requests.TaskRequest` fixture, **not**
  an MCP submit body. B maps it through the Controller-owned submission path;
  A does not define MCP JSON-RPC, task envelopes or submit payloads.
- A does not start the scheduler, a Controller, a QC process or an SSH/LSF
  session from this request.
- A's contract test (`tests/test_acp2_science_fixtures.py -k singlepoint`)
  proves the static fixture contract only. Every real-computation claim stays
  `NOT_VERIFIED` until B returns items 1–8 above.
