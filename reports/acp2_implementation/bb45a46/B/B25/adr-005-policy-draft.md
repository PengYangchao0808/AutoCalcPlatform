# ADR-005 policy draft — Local Runtime licensing and the independent RPH boundary

Status: **DRAFT FOR USER DECISION — no project license change, no open-source release claim.**
Owner of the decision: the user. Author of this draft: B25a (platform packaging).
This document records the current facts and the options; it is not an approval and it does
not relicense any file.

## 1. Facts at the packaged revision

| Fact | Value |
|---|---|
| Repository license | `LICENSE` = MIT (unchanged by B25a) |
| Python distributions | root `acp`, plus the separated roles `cccp`, `acp-worker`, `acp-web` (B25a) |
| NMR/DP5 assets | `src/cccp/analysis/nmr/model_assets/NOTICE.md` + `LICENSE-DP5` ship inside the `cccp` wheel |
| Science dependencies | numpy/rdkit/pyyaml/scipy/matplotlib/openpyxl/typing-extensions (science-node), optional nmrglue/openbabel/qml |
| Control dependencies | fastapi/uvicorn/python-multipart/zipstream-ng/paramiko (control plane only) |
| DSH/browser | DSH is an external Node/pnpm workspace, not a Python dependency; `playwright` is a root-only `browser` extra and is absent from `cccp` |
| RPH workspace | private pnpm workspace (`private:true`), no root `package.json`, no public registry publication |
| Wheel/sdist publication | B25a artifacts exist for independent install/verification; publishing them to a public index is **not** part of B25a |

## 2. Decision required

ADR-005 asks whether the Local Runtime should be released as open source or as a free
closed-source product, and where the independent RPH client boundary sits. The options below
are mutually exclusive; none is selected by this draft.

| Option | Meaning | Required approvals before it can be claimed |
|---|---|---|
| **A. Open-source release** | Publish source + the three Python distributions under an OSI license; keep RPH/DSH private | Explicit user approval; third-party license audit incl. DP5 notice, optional `openbabel`/`qml`; publication scope and trademark review |
| **B. Free, closed-source distribution** | Publish binaries/wheels without source under a proprietary EULA | Explicit user approval; a real license text replacing MIT (B25a must not do this silently); legal review |
| **C. Current conservative posture (default)** | Keep the existing MIT source and B25a artifacts for authorized local install; do not publish RPH/DSH; make no open-source-release claim | None beyond this draft; remains the state until the user decides |

## 3. Recommendation while the decision is open

1. Keep `LICENSE` = MIT and keep the existing notice/license files in the `cccp` wheel.
2. Treat B25a wheels/sdists as **reviewable release candidates for A/C install evidence**, not as
   a public open-source release.
3. Keep `rph/` private and outside the Python wheels; keep DSH a separately installed Node
   runtime. The Workbench may serve only the static assets listed by C's versioned manifest.
4. Do not state or imply "fully open source" anywhere until the user selects option A and the
   third-party audit is complete.

## 4. Independent RPH runtime boundary (unchanged)

- RPH is a client: it may discover tools, start/recover research through the one Controller, and
  render Workbench views; it must not start a second Controller or carry a QC shell.
- The Controller/worker/science packages remain B/A-owned Python distributions; RPH packages are
  C-owned and are not dependencies of `cccp` or `acp-worker`.
- Static assets are mounted read-only at a `release_id`-versioned root with a digest manifest;
  unknown/unlisted paths return 404. The mount implementation is B25b scope (B-req-06).

## 5. Explicit non-actions in B25a

- `LICENSE` unchanged; no new license text was added.
- No public package-index upload.
- No "open source" badge, README claim, or release note added.
- No DSH/browser dependency added to `cccp`.

## 6. User decision record (to fill on approval)

```json
{
  "adr": "ADR-005",
  "decision": null,
  "approved_by": null,
  "approved_at": null,
  "publication_scope": null,
  "third_party_audit": "NOT_VERIFIED",
  "notes": "Fill only after explicit user approval; until then B25a stays option C."
}
```
