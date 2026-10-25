# A/C evidence observed at integration time (not re-signed by B)

B14a records what each track published on its own branch/worktree. B does not sign
A's or C's scope; the rows below are the other tracks' receipts and artifacts as
found, and they are not promoted to B's PASS.

## A — `acp2/a-science` @ `9977a106445177708e929557f9460e86914787ad`

* No `reports/acp2_implementation/bb45a46/A/` evidence root exists on A's branch.
* A published its science contracts under
  `docs/reference/next-change/acp2-contracts/science/` (`handoff-H1/H2/H3.json`,
  capability/question/reaction/recipe/provenance/evidence schemas, task-binding,
  cross-track dependency ledger) and its science tests under
  `tests/test_acp2_science_*.py` with fixtures in `tests/fixtures/acp2/science/`.
* A's worktree had one uncommitted edit (`scripts/acceptance/acp2_science.py`,
  +383 lines) and one untracked `science/handoff-H4.json`; neither is part of the
  delivered `243d2b7` SHA and neither is included in this candidate.
* A's `cross-track-dependencies.json` marks every B- or C-produced edge
  `NOT_VERIFIED` with an explicit gap; B does not upgrade any of them here.

## C — `acp2/c-experience` @ `35614fd83f616005a0546af35631c477151e7dde`

C's worktree evidence root (outside git) contains these receipts:

| Receipt | Subject | C status |
|---|---|---|
| `.omo/evidence/acp2-plan-c-rph-workbench/task-6-...md` | T-06 / C03a experience harness CLI, frozen scenarios, evidence write | DONE |
| `.../task-8-...md` | T-08 / C05 isolated Chromium install and real headless launch | DONE |
| `.../task-9-...md` | T-09 / C06a research-semantics bridge over official MCP tools | DONE |
| `.../task-15-...md` | T-15 / C10a science and research client query modules | DONE |

These are C-authored receipts. B14a only records that they exist and that C's
included code is the `e03a9dd` content; no B acceptance row is derived from them.
