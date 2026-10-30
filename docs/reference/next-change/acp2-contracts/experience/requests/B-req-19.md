# B-req-19 — Serve the frontend experimental entry at /next/

- **Request ID:** `B-req-19` (semantic name: `static_next_entry_route`)
- **Owner:** B (owner of `src/acp/**` — the ACP Web service surface)
- **Producer:** C (experience/RPH lane; minimal server-side increment committed,
  awaiting B sign-off — **C 车道已提交最小实现，待 B 签收**)
- **Consumer step:** C's next-generation workbench lives in `frontend/next/`
  (C-owned glob). It must be reachable through the same ACP Web service that
  serves `/` and `/legacy/`, without a second server or a dev-only toolchain.
- **Consumer track:** C (`acp2-plan-c-rph-workbench`), experimental entry
- **Status:** open — minimal implementation already in the tree (see
  "Committed minimal implementation"); B to review/sign off or request changes.

## Requested change (owner B applies/accepts in `src/acp/api/server.py`)

1. **Static mount.** When `<frontend>/next/` exists, mount it at prefix
   `/next` with `_RevalidatingStaticFiles` (same `Cache-Control: no-cache`
   200-forcing behaviour as `/js` and `/css`). When the directory is absent,
   skip the mount silently — no startup failure, no change to any other route.
2. **Entry routes.** `GET /next/` and `GET /next/index.html` read
   `<frontend>/next/index.html` and answer `200 text/html`; file missing →
   `404` (no exception raised). Implementation style mirrors `GET /`.
3. **Identity exemption.** Add `"/next/"` to `_IDENTITY_EXEMPT_PREFIXES`, so
   the experimental entry is reachable without a credential exactly like `/`
   (entry-surface parity; the workbench HTML itself carries no secrets).

## Motivation

`frontend/next/` is C's experimental entry point (next-gen workbench shell).
Without a `/next/` route it can only be opened via `file://` or an ad-hoc
static server, which bypasses the ACP origin/identity boundary and diverges
from how `/` and `/legacy/` are served. Serving it from the same app keeps one
origin, one revalidation policy, one boundary.

## Compatibility requirement — purely additive

- `/`, `/legacy/`, `/js`, `/css` mounts: **unchanged** (verified by the
  existing `tests/test_acp_api.py` assertions, which stay green).
- No API route, scheduler surface, or identity rule is modified beyond the
  one new exempt prefix `"/next/"` (which cannot match any existing path —
  no current route starts with `/next/`).
- The mount is conditional on the directory existing, so packaged installs
  without `frontend/next/` behave exactly as before.

## Positive examples

```http
GET /next/            -> 200 text/html   (frontend/next/index.html)
GET /next/index.html  -> 200 text/html
GET /next/app.js      -> 200 application/javascript, Cache-Control: no-cache
                        (any asset under frontend/next/, via the mount)
GET /                 -> 200 text/html   (unchanged workbench)
```

With multi-user identity enabled, `GET /next/` with **no** credential is
`200` (exempt), matching `GET /`.

## Negative examples / error codes

```http
GET /next/            -> 404  when frontend/next/index.html is absent
                         (file missing; no exception, no stack in response)
GET /next/index.html  -> 404  same condition
GET /next/anything    -> 404  when frontend/next/ dir is absent (no mount);
                         server boots normally, / and /legacy/ unaffected
```

No new error codes are introduced; the route answers plain `404` (FastAPI
default `{"detail":"Not Found"}` does not apply — these are HTMLResponse
routes, the 404 body is a minimal HTML stub).

## Rollback

Remove, in `src/acp/api/server.py`:

1. the `next_dir = _FRONTEND_DIR / "next"` mount block, and
2. the `"/next/"` entry in `_IDENTITY_EXEMPT_PREFIXES`, and
3. the `GET /next/` + `GET /next/index.html` route definitions.

No data, schema, or DB migration is involved; `frontend/next/` (C-owned) may
remain in the tree inert. Rollback leaves zero trace on `/`, `/legacy/`,
`/js`, `/css`.

## Committed minimal implementation (pending B sign-off)

- `src/acp/api/server.py` — conditional `/next` mount + two entry routes +
  `_IDENTITY_EXEMPT_PREFIXES` += `"/next/"`.
- `frontend/next/index.html` — placeholder shell (`#acp-next-root`,
  "实验入口施工中"); C will overwrite wholesale.
- `tests/test_acp_api_next_entry.py` — entry routes, identity-exempt parity
  with `/`, revalidation header, directory-missing graceful degradation.

Evidence: `python -m pytest tests/test_acp_api.py tests/test_acp_api_next_entry.py -q`
green — `test_acp_api.py` unchanged, proving `/`, `/legacy/`, `/js`, `/css`
behaviour is untouched.
