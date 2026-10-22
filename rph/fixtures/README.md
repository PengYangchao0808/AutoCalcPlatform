# rph fixtures

Consumption fixtures for the experience client.

Rules:

- every fixture records the **producer revision and digest** it was copied from;
- fixtures are read-only inputs for `test/`, never a second editable authority;
- no credentials, absolute remote paths, or private DSH source may appear;
- authoritative contracts stay in
  `docs/reference/next-change/acp2-contracts/experience/` — this directory
  holds concrete samples that reference them, not redefinitions.

No fixtures are added by the scaffold task (T-05). Bridge contract fixtures are
introduced by T-09/T-10.
