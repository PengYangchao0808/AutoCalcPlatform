# B-req-12 — Split B02a (environment) from B02b (package smoke); B02a must not be accepted on Chromium launch

- **Request ID:** `B-req-12`
- **Owner:** B00 / B03 (dispatcher), B02a (environment deliverer), B02b (package-smoke deliverer)
- **Producer:** B02a + B02b
- **Consumer step:** C/T-01 (role interpreters, tools root), C/T-04 (isolated Node/pnpm/DSH),
  C/T-08 (Chromium install and real launch), C/T-11/T-12 (real MCP/DSH/model)
- **Consumer track:** C (`acp2-plan-c-rph-workbench`)
- **Current schema revision:** C has published only `acp2.experience.dependencies-c.v1`
  at `experience/handoffs/dependencies-c.json`. B has published no
  `runtime/dependencies.json`, no `runtime/baseline-lock.json`, and no B02a/B02b receipt.
  Status: `NOT_VERIFIED(b00_handoff)` + `NOT_VERIFIED(b02a_handoff)`.
- **Expected schema revision:** B-published `runtime/dependencies.json` (v1 or later)
  with separate `B02a` and `B02b` rows; each row carries `producer`, `artifact`,
  `readiness` (`contract_ready|implementation_ready|integration_ready|verified`),
  `delivery_status` (`planned|blocked`), `requires` (explicit AND array), and a
  versioned path + digest. No free-text "or" inside `requires`.

## Why the split is mandatory

B02a installs the environment: tools root, data root, the C/interpreter/browser role
interpreters and the `.[browser]` extra. B02b proves packaging: package smoke,
installability, and artifact contents. These are different failure modes and they
must be accepted independently. If B02a is blocked on Chromium launch, C's entire
environment track stalls behind a browser binary that C T-08 owns.

**B02a must not be accepted on Chromium launch.** Success for B02a is: the three
role interpreters resolve to the declared roots and the `.[browser]` extra is
importable. Chromium installation, `PLAYWRIGHT_BROWSERS_PATH`, and a real headless
launch belong to C/T-08 and enter the H2 aggregate only through C's
`browser-install` scenario. B02b's package smoke is not a T-04 precondition.

## Minimal input

```json
{
  "request": "B-req-12",
  "user": "C/T-01",
  "observed_b02a_state": "absent",
  "observed_b02b_state": "absent",
  "required_rows": ["B02a:tools-root", "B02a:role-interpreters", "B02a:browser-venv", "B02b:package-smoke"],
  "required_separation": "B02a verdict independent of any Chromium launch"
}
```

## Expected success output

A B-published `runtime/dependencies.json` fragment containing at minimum:

```json
{
  "rows": {
    "B02a:tools-root": {"producer": "B02a", "artifact": "/opt/acp2-tools", "readiness": "integration_ready", "delivery_status": "planned", "requires": ["frozen node distribution", "frozen pnpm", "frozen DSH revision"]},
    "B02a:role-interpreters": {"producer": "B02a", "readiness": "verified", "requires": ["ACP2_C_PYTHON resolves to $ACP2_C_SOURCE", "ACP2_INTEGRATION_PYTHON resolves to $ACP2_INTEGRATION_SOURCE"]},
    "B02a:browser-venv": {"producer": "B02a", "readiness": "verified", "requires": [".[browser] extra importable"], "acceptance_excludes": ["chromium launch"]},
    "B02b:package-smoke": {"producer": "B02b", "readiness": "verified", "requires": ["package artifact installs without source PYTHONPATH"]}
  }
}
```

Plus a B02a receipt whose `acceptance` section explicitly does not contain a
Chromium-launch assertion.

## Failure output

Minimum one accepted failure. C must be able to reject a B02a receipt that only
proves `import playwright` by launching Chromium, or that leaves B02a blocked on a
browser binary:

```json
{
  "request": "B-req-12",
  "result": "FAIL",
  "reason": "B02a accepted on Chromium launch",
  "detail": "receipt acceptance lists 'chromium launch' under B02a; role interpreters were never asserted to resolve to $ACP2_C_SOURCE / $ACP2_INTEGRATION_SOURCE",
  "expected": "B02a accepted on interpreter resolution + .[browser] importability only; Chromium launch evaluated under C/T-08"
}
```

Second failure mode: a single combined `B02` row that cannot be accepted when only
environment is delivered:

```json
{
  "request": "B-req-12",
  "result": "FAIL",
  "reason": "B02a and B02b collapsed into one row",
  "detail": "delivery_status=blocked because package smoke is unfinished, even though all interpreters and the browser extra are importable",
  "expected": "separate B02a and B02b rows with independent readiness and delivery_status"
}
```

## Compatibility requirement

- C's `experience/handoffs/dependencies-c.json` row keys (`B02a:tools-root`,
  `B02a:role-interpreters`, `B02a:browser-venv`) are stable and must be consumed
  as-is; B may add fields but must not rename these keys.
- `readiness` stays on the four-value ladder; `planned|blocked` stays in
  `delivery_status`. C never promotes a B row without a producer receipt + digest.
- The browser extra version stays `playwright==1.63.0` (repair baseline); a version
  change requires a new B compatibility-lock revision and a C re-run of T-08.
- C will not consume a B row that has no versioned path and digest.

## Evidence path

- C-side observation: `/opt/acp2-dev/evidence/C/baseline/20261022T191200Z-c00/`
- C dependency ledger: `docs/reference/next-change/acp2-contracts/experience/handoffs/dependencies-c.json`
- B-side expected: `docs/reference/next-change/acp2-contracts/runtime/dependencies.json`
  and the B02a/B02b receipts registered by B00/B03.
