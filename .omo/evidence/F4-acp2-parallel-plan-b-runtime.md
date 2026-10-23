# F4 — Ideal-state fidelity walk (ACP 2.0 Parallel Plan B runtime)

- Reviewer: independent F4 (read-only on product code and other reviewers' evidence; appended follow-up rows 38-41 to the plan only).
- Plan: `.omo/plans/acp2-parallel-plan-b-runtime.md` (HEAD `324d223`; four new `- [ ] 38.` through `- [ ] 41.` rows appended after row 37).
- HEAD: `324d2234911919458724df1f80c94a7756d527a4`, tree `913033f653ab5b275aa324dda4601da6347c4dc8`.
- Release candidate: `reports/acp2_implementation/bb45a46/B/B25/release-fix5`, source `6ec01ec96fa81b89bec8ff321c38e3db2dc154d2`, tree `e2b23771a47e7553738ef902cc4c1b62528fe508`, version `0.3.0`.
- Final index: `reports/acp2_implementation/bb45a46/integration/h7-index.json`; aggregate receipt `reports/acp2_implementation/bb45a46/integration/summary.json`.
- Prior gates: F1 `.omo/evidence/F1-acp2-parallel-plan-b-runtime.md` APPROVE; F2 `.omo/evidence/F2-acp2-parallel-plan-b-runtime.md` APPROVE; F3 `.omo/evidence/F3-acp2-parallel-plan-b-runtime.md` APPROVE.
- No product code, test, systemd unit, production data root or other reviewer's evidence was modified. No Controller was started. Production `acp.service`/8765 was observed only.
- Date: 2026-10-24.

**VERDICT: APPROVE**

The IS-1..IS-13 walk is complete. Five rows are fully satisfied. Eight are partial only because of an external prerequisite or a new, explicitly tracked row; none hides a FAIL or a false PASS. Four follow-up rows (38-41) were appended to the plan: credential redaction, B24b overlay attribution, B24 S0 failure semantics, and final-index landed-head reconciliation. H4/H6/H7 remain OPEN; no A/C scope was signed; the production service was not deployed or restarted.

---

## 0. Review commands (read-only)

```bash
git log --oneline -3
git show --no-patch --format='%H %T %s' 324d223 6ec01ec
git rev-parse HEAD HEAD^{tree}
git diff --shortstat 6ec01ec..324d223 -- src
git diff --name-only 6ec01ec..324d223 -- src
systemctl show acp -p ActiveState -p ActiveEnterTimestamp -p ExecMainStartTimestamp
curl -sS -m 5 http://127.0.0.1:8765/api/health
sha256sum reports/acp2_implementation/bb45a46/B/B25/release-fix5/release-manifest.json reports/acp2_implementation/bb45a46/B/B25/release-fix5/source.json
/usr/bin/python3.11 scripts/acceptance/acp2_runtime.py --check-only reports/acp2_implementation/bb45a46/integration/summary.json
PYTHONPATH=src /ACPServer/acp2-development/venvs/integration/bin/python -m pytest tests/test_acp2_runtime_h7_index.py -q
grep -rn "sshpass[[:space:]]-p" .omo/evidence reports
grep -c "credential-key:" cccp.yaml
git ls-files cccp.yaml | wc -l
git status --short src/acp_services/workspace/boundary.py src/acp/calculations/batch/options.py
git diff --stat 6ec01ec -- src/acp/calculations/batch/options.py
python3 -c "import json; d=json.load(open('reports/acp2_implementation/bb45a46/integration/h7-index.json')); print(d['source_candidate']['landed_head']); print(d['source_candidate']['landed_head_tree']); print(d['source_candidate']['landed_head_relation'])"
```

Observed key results:

```text
324d2234911919458724df1f80c94a7756d527a4 913033f653ab5b275aa324dda4601da6347c4dc8 fix(release): make HEAD self-contained for the final index
6ec01ec96fa81b89bec8ff321c38e3db2dc154d2 e2b23771a47e7553738ef902cc4c1b62528fe508 fix(release): close F2/F3 blocking defects
 9 files changed, 815 insertions(+), 157 deletions(-)
ExecMainStartTimestamp=Thu 2026-10-22 23:19:37 CST
ActiveState=active
ActiveEnterTimestamp=Thu 2026-10-22 23:19:37 CST
{"schema_version":1,"status":"ok","candidate_revision":"ab492cc8be9e46125fa2d2fae69a3ecaac64a548","contract_version":"1.3.0","contract_digest":"1fb215f52dae942c8a19b993ac4cfc8e841069efd3ecd3f70d9c99532030738c","contract_digest_scope":"delivered-contract-set","read_only":true,"started_at":"2026-10-22T15:19:38.960726Z"}
VALID: reports/acp2_implementation/bb45a46/integration/summary.json (tool check only; this is not a scenario PASS)
8 passed in 0.55s
```

Artifact digest check (full command, no placeholder):

```bash
python3 - <<'PY'
import json, hashlib, os
base = "reports/acp2_implementation/bb45a46/B/B25/release-fix5"
manifest = json.load(open(os.path.join(base, "release-manifest.json")))
source = json.load(open(os.path.join(base, "source.json")))
ok = True
for name, digest in manifest["artifacts"].items():
    got = hashlib.sha256(open(os.path.join(base, name), "rb").read()).hexdigest()
    ok = ok and got == digest
for name, meta in source["artifacts"].items():
    path = os.path.join(base, name)
    got = hashlib.sha256(open(path, "rb").read()).hexdigest()
    ok = ok and got == meta["sha256"] and os.path.getsize(path) == meta["size_bytes"]
print("ALL_DIGESTS_OK", ok)
PY
```

Observed: `ALL_DIGESTS_OK True`. `release-manifest.json` sha256 `380b4915cf44cbd94e279e7e0ee6dfb1e27891da654dae09e0c7990335740a58`; `source.json` sha256 `4b674072c27e45f5f725898556dfb122c750c9e0d5acf4b5964278202ecc86c6`.

The credential scan matched only `.omo/evidence/F3-acp2-parallel-plan-b-runtime.md` lines 115 and 533 (the secret is not reproduced here); `grep -c "credential-key:" cccp.yaml` returned `1`; `git ls-files cccp.yaml` returned no path. This is the basis for new row 38.

---

## 1. IS-1..IS-13 table

Legend: `SATISFIED` = delivered behavior plus proving evidence; `PARTIAL` = delivered core with a named external prerequisite or a newly tracked shortfall; `NOT_VERIFIED` = no delivered behavior/evidence. Every partial row names where the gap is tracked.

| IS | Result | Delivered evidence (exact paths / receipts) | Gap and tracking |
|---|---|---|---|
| IS-1 Baseline/candidate attribution | **PARTIAL** | `B/B00/manifest.json` freezes historical `096cdff`/`ab492cc`, `final_debug_candidate=sha256:bb45a46…`, and the patch bundle; `B/B02/summary.json` materializes commit `a0185b15c546fbd07919326d581fab13e05000fa` in a clean worktree with 1906/1906 per-file matches and `worktree_clean_after_commit=true`; `B/B25/release-fix5/{release-manifest.json,source.json}` name clean source `6ec01ec`/tree `e2b2377`, version `0.3.0`; all eight artifact digests recomputed from disk (`ALL_DIGESTS_OK True`). | The release source is immutable and resolvable, but `integration/h7-index.json` still says `landed_head=09fc41d…`/`landed_head_tree=b0718da…` and claims the landed tree differs only by report/evidence files. At HEAD `324d223`, nine `src/` files differ from `6ec01ec` (+815/−157). New row **41** tracks the reconcile. |
| IS-2 Versioned contracts and fixtures | **PARTIAL** | `B/B01/help.exit=0`, `B/B01/validate-samples.exit=0` (25/25 sample expectations), `B/B03/validator-happy.txt` exit 0, `B/B03/pytest.log` 28 passed, `runtime/{application-api.v1.json, science-binding.v1.json, control-and-data.v1.json, admission-approval.v1.json, acceptance-result.schema.json}` delivered; `runtime/entry-call-table.v1.md` records one application command per entry. | A's science cross-track dependency fixture and a C-authored sample are absent, so H1 is OPEN (`a_science_contract_fixture_absent;c_authored_sample_absent`). Tracked by H1; no new row. |
| IS-3 One Controller / three-entry equivalence | **PARTIAL** | `B/B04/second-controller-exit.txt` `SECOND_EXIT=3`, `B/B04/lock-refusal-*`, `B/B04/entry-readonly-*.json` PASS; `B/B05/task-count-before.json` = `B/B05/task-count-after.json`; `B/B07/topology` two adapters; `B/B10/three-entry-matrix.json`; `B/Bfix5/three-entry-compare.json` (`all_equal: true` for release_id/release_digest/plan_digest/submission_id/attempt/state/revision); F3 T2.7/T2.10 local and LSF three-entry rows. | CLI, HTTP and MCP observe the same TaskRef/release; a real DSH observer is absent (H2/H3 DSH rows). Tracked by H2/H3 and the DSH gap; no new row. |
| IS-4 Real stdio MCP process | **SATISFIED** | `B/B05/probe.exit=0`; `B/B05/probe/wire.jsonl` initialize → notifications/initialized → tools/list with exactly eight tools; `B/B05/negotiation-matrix.log` (same, different-supported, no-common); `B/B05/launch-schema-validation.log` PASS; F3 T1.1 real subprocess `protocolVersion=2025-06-18`, stdout-only JSON-RPC, stderr diagnostics, `A/B05` task count unchanged. | None. |
| IS-5 One application use case per entry | **SATISFIED** | `B/B03/call-table-audit.md`; `B/B06/summary.json` tool→command map; submit returned a durable TaskRef in 0.1868 s while state was `queued`; status/read side-effect counts unchanged; `prepare` operation ref; `analyze` without `explicit=true` returns `input_invalid`, unknown reference returns `resource_not_ready`; F3 T2.1-T2.9. | None. |
| IS-6 Persistent idempotency / remote reconciliation | **PARTIAL** | `B/B08/submit-replay-main-20261022T161353Z-449619-4d977737.json` PASS: two MCP subprocesses → same TaskRef, exactly one dispatch, changed payload 409, changed scope rejected; `B/B08/restart-replay.json`; four crash-window JSONs; F3 T2.3/T5.1/T5.2 (same TaskRef after process restart; `idempotent_replay=true`). | `B/B08/remote-window.json` says `remote_bsub: NOT_VERIFIED` (no bsub); `B/B11/live-gap.json` records no LSF prerequisite. Remote unknown-submission reconciliation is therefore unproven. This is the already-open LSF/B11 dimension (H3 remains OPEN; H7 does not claim it); no new row. |
| IS-7 Server-side identity/admission | **SATISFIED** | `B/B09/summary.json`: over-cores/over-jobs 429, cross-project read/cancel/prepare 404, claim-then-deny, replay one claim/one reservation, usage receipt once; `B/B15` authz; `B/B17/pre-dispatch-refusal.json` 403 with zero job/QC/bsub delta; `src/acp/api/v1_routes.py::create_job` calls `_require_submit_access` and the server identity before normalization/submit; `AdmissionRefusedError` maps to the structured denial. | None for the delivered local-single mode. Multi-tenant security is explicitly `false` in capabilities; B15/B23 cover the authorization subset. |
| IS-8 Real Local and LSF execution | **PARTIAL** | F3 T4.1 Local ORCA 6.1.1 `-1.116773191779 Ha`; T4.2 optimize converged; T4.3 frequency `5038.6 cm-1`; T4.4 xTB 6.7.1; T4.5 real LSF job `1766` terminal `completed`, `release_id=1c79785a657c6614`, `submission_id=sub_a65baa96e5b08c47`; `B/Bfix5/lsf/job-terminal.json` real LSF job `1765`; F3 T5.1 Controller restart same task, T5.3 cancel, T5.4 publication-only retry; `B/B13` Local control rows PASS. | F3 T4.7 `bstop`/`bresume` not run; `B/B13` root `control-recovery` is `NOT_VERIFIED`/exit 2 because LSF is absent; B11 unknown-submission row is `NOT_VERIFIED`. Tracked by B11/B13 and the open LSF dimension; no new row. |
| IS-9 Artifact reference identity | **SATISFIED** | `B/B12/artifact-main-20261022T212957Z-677819-c7cfdc67.json` PASS with 19 assertions; `prepare-op-ref.json` persistent `op_3557c658…`; `read-digest-generation.json` requested=served digest and generation 1/1; `cold-cache-eviction.json` cold read 409 `artifact_body_missing` then explicit reprepare; `frequency-artifact.json` own digest; `old_generation` 409, `path_traversal` 400, zero task side effects; F3 T2.8 prepare/read 725 bytes matching on-disk sha256. | None. |
| IS-10 Approvals/budgets as separate facts | **SATISFIED** | `B/B16/approval-rows.json`, `restart.json` (same approval after restart, repeat approve idempotent), `invalidation-matrix.json` (method/plan/proposal/budget changes rejected), `unapproved-no-dispatch.json` (0 job rows); `B/B17/settlement.json`, `boundary.json`, `pre-dispatch-refusal.json`, `concurrent-last-unit.json` (one 201/one 403, duplicate settle idempotent, restart preserves); `JobStatus` still has the existing 10 states and `runtime/approvals.py` explicitly does not reuse `WAITING_REVIEW`. | F3 T5.5 did not re-run P2 in its bounded window; this is a coverage note, not a delivery gap. B16/B17 raw receipts are immutable evidence (not an independent F3 rerun). |
| IS-11 Events/recovery | **PARTIAL** | `B/B19/summary.json`: B19a contract digest + 17 fixture tests; B19b assertions `duplicate_event_is_noop`, `stale_event_cannot_overwrite_terminal`, `exactly_one_approval/submit`, `new_session_resumed_from_cursor`, `task_left_running_after_simulated_c_exit`; F3 T5.1 Controller restart same task; `B/B13` `dsh-disconnect.json`. | A real DSH session termination/new session is absent (`B/B19/dsh-not-verified.json`; F3 T3.2 `NOT_VERIFIED`), so H2/H3/H7 DSH rows stay open. Tracked by DSH; no new row. |
| IS-12 P3-P5 adapters/migration/packaging | **PARTIAL** | `B/B21/infra_install-*.json` PASS; `B/B22/infra_upgrade-*.json` PASS with old-task read-only and named uninstall errors; `B/B23/multitenant-*.json` two-project isolation PASS + `cross-project-non-disclosure.json`; `B/B24/migration-main-…json` PASS, `restore-comparison.json` equal, `drill-receipts.json`; `B/B25/package-install-…json` PASS (four fresh venvs, CCCP sample without Controller, Lab, eight-tool MCP, assets/notices/licenses); version `0.3.0` in all four locations (F2 §6). | B23b selectivity is `NOT_VERIFIED(c_t40a_absent)`; C33/C34 install evidence is absent — already tracked by B23/H7. New: B24b H6 rows use the uncommitted runnable overlay (row **39**) and relabel the executed S0 FAILs (row **40**). H6 stays OPEN. |
| IS-13 Evidence vocabulary | **PARTIAL** | `B/B26a/reproduction-matrix.json` retains the original `overall_result: FAIL` in `result_history` and the B-fix-4 row PASSes; `B/B13/intermediate-failed-run-classification.json` keeps the intermediate `FAIL` and its artifact inventory; `B/B26/check-only.txt` has cwd/command/stdout/stderr/exit and exit 0; `B/B01` validator rejects invalid samples. | Two real vocabulary/evidence defects: F3 evidence contains a plaintext SSH credential (row **38**), and B24b relabels the executed `F0/S0`/`F1/S0` `FAIL` cells as `NOT_VERIFIED` (row **40**). No executed FAIL was relabeled PASS. |

Counts: **SATISFIED 5** (IS-4, IS-5, IS-7, IS-9, IS-10); **PARTIAL 8** (IS-1, IS-2, IS-3, IS-6, IS-8, IS-11, IS-12, IS-13); **NOT_VERIFIED 0**.

---

## 2. Evidence behind the non-obvious verdicts

### 2.1 IS-1 — release source is resolvable, final index metadata is not

- The historical baseline and patch bundle are not the release source. `B/B00/manifest.json` records `delivery_revision=096cdff…`, `code_candidate_revision=ab492cc…`, `final_debug_candidate=sha256:bb45a46…`, and `pending_fix_bundle=…/final-debug-patch/`.
- `B/B02/summary.json` records the clean materialization: `materialized_commit=a0185b15c546fbd07919326d581fab13e05000fa`, `per_file_verification=1906/1906 PASS`, `worktree_clean_after_commit=true`.
- `B/B25/release-fix5/source.json` and `release-manifest.json` record clean source `6ec01ec`, tree `e2b2377`, version `0.3.0`. All six role artifacts and the two root-build artifacts match the recorded SHA-256 and size (recomputed here).
- The only IS-1 defect is metadata: `h7-index.json` says `landed_head=09fc41d` and “landed tree differs only by report/evidence files”. That is false for HEAD `324d223` (`9 files changed, 815 insertions(+), 157 deletions(-)` under `src/`). Row 41 addresses it without changing the release source or digests.

### 2.2 IS-6 / IS-8 — local recovery is proven; remote reconciliation is not

- Local: `B/B08` proves same key/same payload → same TaskRef, exactly one dispatch, changed payload 409, changed scope rejected, restart replay, and four crash-window receipts. F3 re-executes the two-process replay and a fresh-process restart (`logs/mcp-submit-2.log`, `logs/mcp-status-lsf.json`).
- Remote: `B/B08/remote-window.json` explicitly says `remote_bsub: NOT_VERIFIED` with “LSF is absent in this environment”; `B/B11/live-gap.json` records `exit_code=2`, `no_bsub_invoked=true`. F3’s real LSF run is normal completion only; F3 T4.7 `bstop`/`bresume` is `NOT_VERIFIED`. The H3/H7 note that the LSF prerequisite is closed refers to normal completion (B-fix-5 job 1765), not to unknown-submission reconciliation or control. This is the already-open LSF/B11 dimension; no new row was added.

### 2.3 IS-13 — executed FAILs and evidence secrecy

- `B/B24/four-combination-all-combos-raw/summary.json` is `FAIL` with `F0/S0=FAIL`, `F1/S0=FAIL`, `F0/S1=PASS`, `F1/S1=PASS`.
- `B/B24b/four-combination-run-log.json` sets `s0_cells` to `NOT_VERIFIED(b24b_scope_s1_only)`; `B/B24b/h6-receipt.json` repeats that for both S0 cells. That is a vocabulary relabel, not a missing environment. Row 40 requires `FAIL` or `NOT_APPLICABLE` with the scope reason, while preserving the raw FAIL.
- F3’s own transcript records a literal `sshpass` credential invocation at lines 115 and 533, and the local ignored `cccp.yaml` has a plaintext password field. This violates the plan’s Must NOT for evidence and launch material. Row 38 requires redaction/rotation and a scan; this F4 evidence does not reproduce the secret.

---

## 3. Success criteria table check

Every IS row has a delivering todo, a proving QA scenario and at least one existing evidence root. All named directory roots were checked (`test -d`): `B00 B01 B03 B04 B05 B06 B07 B08 B09 B10 B11 B12 B13 B14 B14b B15 B16 B17 B19 B21 B22 B23 B24 B24b B25 B26` — none missing.

| IS | Delivering todos | Proving QA | Evidence root exists | Note |
|---|---|---|---|---|
| IS-1 | 1 B00 | `git show` both SHAs + manifest validator | yes (`B/B00`) | row 41 adds final-index landed-head reconciliation |
| IS-2 | 2 B01, 4 B03 | sample validator + harness `--help` | yes (`B/B01`, `B/B03`) | H1 still OPEN on A/C samples |
| IS-3 | 5, 6, 8, 11 | `entry-readonly`, two-adapter topology, probe task count, `three-entry` | yes (`B/B04`, `B/B05`, `B/B07`, `B/B10`) | the final three-entry PASS is in `B/Bfix5`; the table predates B-fix-5 but the artifact exists |
| IS-4 | 6 B05 | real subprocess probe + `test_acp2_runtime_mcp_process.py` | yes (`B/B05`) | none |
| IS-5 | 4 B03, 7 B06 | call table + preflight/submit/status/read | yes (`B/B03`, `B/B06`) | none |
| IS-6 | 9 B08 | concurrent replay + restart + diff payload | yes (`B/B08`) | remote LSF window is `NOT_VERIFIED` |
| IS-7 | 10 B09, 17 B15, 19 B17 | admission/authz negative fixtures + `approval-budget` | yes (`B/B09`, `B/B15`, `B/B17`) | local-single only |
| IS-8 | 11 B10, 12 B11, 14 B13 | real Local/LSF + control-recovery | yes (`B/B10`, `B/B11`, `B/B13`) | LSF control/unknown remain `NOT_VERIFIED` |
| IS-9 | 13 B12 | prepare/read digest/generation + negative matrix | yes (`B/B12`) | none |
| IS-10 | 18 B16, 19 B17 | approval restart + budget settlement + no new JobStatus | yes (`B/B16`, `B/B17`) | F3 did not rerun P2; B16/B17 receipts are the proof |
| IS-11 | 21 B19 | DSH termination/new session + Controller restart | yes (`B/B19`) | real DSH is `NOT_VERIFIED` |
| IS-12 | 25-31 | adapter v1/v2, multi-project, migration/backup, clean install | yes (`B/B21`, `B/B22`, `B/B23`, `B/B24`, `B/B24b`, `B/B25`) | B23b/C33/C34 tracked; rows 39/40 added |
| IS-13 | 2 B01, 16 B14b, 33 B26b | vocabulary validator + final `--check-only` | yes (`B/B01`, `B/B14`, `B/B26`) | rows 38/40 add the two vocabulary/evidence defects |

No IS row lacks a proving artifact. The only table-level staleness is that IS-3’s/IS-13’s evidence columns predate B-fix-5/B-fix-4; the later artifacts exist (`B/Bfix5/three-entry-compare.json`, `B/B26/check-only.txt`).

---

## 4. Explicit confirmations

| Item | Confirmation | Evidence |
|---|---|---|
| Production deployment/restart | **None.** `acp.service` active since `2026-10-22 23:19:37 CST`, before the 2026-10-23/24 release work; health still reports the pre-release `candidate_revision=ab492cc…` and `read_only=true`. `h7-index.json` has `production_deployed: false`; `integration/cleanup.json` has `production_service_restarted: false`. | `systemctl show acp …`; `curl http://127.0.0.1:8765/api/health`; `integration/cleanup.json`; F3 §8 |
| H4/H6/H7 | **OPEN.** H4 `NOT_VERIFIED` (A-20/C25/B20a-exec/B20a-search absent); H6 `NOT_VERIFIED` (DSH/A/C readability/selectivity/real-DA); H7 `NOT_VERIFIED` (A-29/C35/independent operator/C33/C34/DSH). H1/H2/H3 are also OPEN. H0/H5 PASS/CLOSED. | `integration/h7-index.json rows/open_rows` |
| A/C signatures | **None.** All H rows have `signed_by: ["B"]`; `absent_external_rows` lists A-20, A-29, C24a, C35; B26a `independent_operator: false`. | `integration/h7-index.json`; `B/B26a/independent-operator.json` |
| Final release candidate reviewable | **Yes.** `release-fix5` source `6ec01ec`, tree `e2b2377`, version `0.3.0`; 8/8 artifact digests and sizes match the manifest/source; `release-manifest.json` sha256 `380b4915…`; `source.json` sha256 `4b674072…`; `--check-only integration/summary.json` exit 0; `tests/test_acp2_runtime_h7_index.py` 8 passed. | §0; `integration/release-candidate.json` |
| B26A-D1/D2 | **Recorded and fixed.** `B/B26a/reproduction-matrix.json` `defects` carries both with `status: FIXED_VERIFIED_IN_BFIX4` and fix evidence; `B/Bfix4/summary.json` records installed workbench HTTP/CLI PASS, package-install scheduler task PASS, and B15 authorization PASS (12 assertions, assertion not weakened). | `B/B26a/reproduction-matrix.json`; `B/Bfix4/summary.json` |
| No A/C scope signed | **Confirmed.** No A/C row is PASS-signed by B; C35 is `c35_absent`, not `C35_INPUT_CYCLE`. | `integration/h7-index.json`; `B/B26/h7-receipt.json` |

---

## 5. New rows appended to the plan

Four real top-level rows were appended after row 37 (the plan is the only pre-existing file modified by F4; this evidence file and the append-only notepad entries are F4's outputs):

| Row | Shortfall | Closing condition |
|---|---|---|
| **38. B-fix-6** | Plaintext node-157 SSH credential in F3 evidence (lines 115 and 533) and in the ignored local `cccp.yaml` — violates the plan Must NOT on credentials in evidence. | `grep -rn "sshpass[[:space:]]-p" .omo/evidence reports` returns no matches; `grep -c "password[[:space:]]*:" cccp.yaml` returns `0`; `git ls-files cccp.yaml` empty; F3 transcript rewritten with a secret-free reference; B05 launch credential scan still PASS; post-rotation LSF check does not echo the secret. |
| **39. B-fix-7** | B24b H6 four-combination/legacy-reader PASSes use `candidate-runnable-overlay.patch`, which changes `src/acp/calculations/batch/options.py` (8+/4−) on top of `f32c670`; `git diff --stat 6ec01ec -- …/options.py` shows 12 lines and the file is still `M` in the working tree. The H6 B-owned rows are not attributable to one frozen source. | Either commit the overlay and rerun the B24b four-combination/legacy-reader rows from a clean full SHA, or downgrade those B-owned rows to `NOT_VERIFIED`; H6 stays OPEN. |
| **40. B-fix-8** | B24 raw executed `F0/S0` and `F1/S0` are `FAIL`; B24b relabels them `NOT_VERIFIED(b24b_scope_s1_only)`, contradicting IS-13. | Keep the raw FAIL; use `FAIL` or `NOT_APPLICABLE` with the scope reason in B24b; do not claim S0 closure. |
| **41. B-fix-9** | `h7-index.json` `landed_head=09fc41d…`/`landed_head_tree=b0718da…` and “differs only by report/evidence files” are false at HEAD `324d223`; nine `src/` files differ from `6ec01ec` (+815/−157). | Update the three fields to the fix-commit HEAD/tree and name the `6ec01ec..HEAD -- src` delta, or mark landed-head historical; release digests unchanged; H7 remains OPEN; `test_acp2_runtime_h7_index.py` 8 passed. |

These rows do not duplicate the already-tracked H4/H6/H7 external gaps, A/C signatures, LSF/DSH/model, selectivity/B23b, or independent-operator gap. They are new B-owned evidence/attribution defects.

---

## 6. Remaining residuals not promoted to rows

- **F1 P3 residuals** (stale `.omo/task-30/32/33` narratives, H2 reason still carrying `lsf_prerequisite_missing`, narrow `missing_external` arrays, `77ac1de` not an ancestor, four-of-six B08 crash windows, B11/B20a/B10b rows without a PASS-level receipt) are documentation/attribution hygiene. None changes an IS result: H2 remains OPEN on C DSH; B11/B20a/B10b are already `NOT_VERIFIED` with reasons; the B08 remote window is the LSF gap. The S0 and landed-head items that do change an IS result were promoted to rows 40/41.
- **F2 LOW residuals** (F2R2-L1 landed-head, L2 doc-sync path blindness, L3 `status_projection` docstring drift, L4 package-install harness-echo identity) are covered by F2. F2R2-L1 is promoted to row 41 because it affects the final index at HEAD; L2-L4 remain F2 code-quality follow-ups with no IS-1..IS-13 behavioral impact.
- **B26a documentation gaps** (`GAP-B26A-01` third-party plugin install procedure, `GAP-B26A-03` historical read command, `GAP-B26A-04` B15/B17 harness invocation) are already tracked with closing conditions in `B/B26a/documentation-gaps.json`; H7 remains OPEN. No duplicate row.
- **C-owned gaps** (C24a/C25/C33/C34/C35, real DSH/model, selectivity B23b) are unchanged and not signed by B.

---

## 7. Final assessment

The delivered behavior is internally coherent and honestly evidenced:

- One Controller with CLI/HTTP/MCP equivalence, real stdio MCP, explicit prepare/analyze, server-side admission, persistent local idempotency, real Local/LSF singlepoint, publication-only retry, and a release candidate with verified digests.
- The open work is correctly attributed: H4/H6/H7 (and H1/H2/H3) are OPEN, A/C scope is unsigned, production is untouched, and the B26A-D1/D2 defects are retained with their fix evidence.
- The four new rows are follow-ups, not reasons to reopen an approved gate: they close credential hygiene, H6 evidence attribution, executed-FAIL vocabulary, and final-index landed-head truthfulness. The walk is complete; every shortfall is either satisfied or tracked as a real `- [ ] N.` row.

**VERDICT: APPROVE**
