# M4-02 real browser rows

Two registered C experience scenarios executed for real through
`scripts/acceptance/acp2_experience.py`:

* `same-task-browser-live` — the bundled Chromium (operator) submits a real
  singlepoint job and reads the TaskRef, the system Microsoft Edge (reviewer)
  reads the *same* TaskRef, and a third read-only identity (auditor) is
  allowed to read the job but refused a submit.
* `approval-budget-live` — two acting identities approve / reject / revoke
  real approvals in the browser, a read-only identity is refused, and a submit
  above the approved budget range is refused by the platform.

Each row directory holds its `acp2.acceptance.v1` receipt (written and
validated by B's runtime), the screenshots, `browser-console.json`,
`browser-network.json` and the platform re-reads.  A screenshot shows what
the page displayed; the receipt and the platform TaskRef/attempt/release are
the authority.

The page's own bootstrap asks for its `demo-project` scope before the row
binds the real project, so a 404 on `science/capabilities?project_id=demo-project`
appears in every browser's console log; the rows assert no page errors and
no console errors inside the driven window.
