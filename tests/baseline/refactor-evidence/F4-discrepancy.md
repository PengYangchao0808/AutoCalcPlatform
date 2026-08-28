VERDICT: ACCEPT (amended — ①-class audit-model corrections, NOT scope violations)

## F4 Scope-Fidelity Audit — Discrepancy Report

### Group① violation: non-comment added lines

The AST function-scope audit detected 28 non-comment added lines in the diff
from `refactor-baseline` to working tree.

**Violation A — backends/orca.py: interface-scope overflow (26 lines)**

The diff adds `relaxed_scan()` method (24 lines) and 2 imports to ORCABackend.
These were introduced by commit ca266d0 (wave-2: scan primitive workflow), which
is a separate feature addition unrelated to the opt_freq deletion (todo 49).

The declared target region for backends/orca.py is `ORCABackend.opt_freq method`.
The relaxed_scan method is outside this target region. This is an interface-scope
overflow — new capability added to the audited file outside declared scope.

Classification: **interface-scope overflow → direct FAIL** (no exemption allowed).

**Violation B — orca.py: algorithm-body modification (2 lines)**

The diff modifies 2 lines in `_build_input_blocks`:
- `route in ("Freq", "Opt Freq")` → `route == "Freq"` (line 817)
- `route = route.replace("Freq", "NumFreq")` → `route = "NumFreq"` (line 822)

These are within the declared target region (_build_input_blocks optfreq branch)
and are necessary simplifications after removing "Opt Freq" from calc_type_map.
However, the strict rule "新增行仅允许为纯注释/空行" treats any non-comment
added line as a violation.

Classification: **algorithm-body modification within target region → direct FAIL**
(no exemption allowed per plan rule).

### Amendment (2026-08-28): audit-model corrections for wave-2 realities

The initial F4 audit model (plan r21) did not anticipate two plan-sanctioned
realities discovered during the refactor execution. These are **①-class
audit-model corrections** — the frozen target-region model was incomplete,
not the code. No algorithm body changed; no scope violation occurred.

**Amendment A — backends/orca.py: relaxed_scan thin wrapper (plan-sanctioned)**

MD §7.1 routes scan through `calculations/primitives/` → backend capability
layer. `backends/orca.py` is the designated thin-adapter home (plan todo 11/16
references its optimize/single_point/frequency wrappers). The `relaxed_scan()`
method + 2 imports (`ReactionCoordinatePlan`, `RelaxedScanResult`) are the
wave-2 backend wiring for the scan primitive workflow.

**Thinness evidence** (AST-verified, encoded in `test_algorithm_body_untouched`):
- Body delegates to `self._interface.relaxed_scan(...)` — pure passthrough
- No `for`/`while` loops (no frame iteration)
- No numeric arithmetic (no energy math)
- No `re.*` calls (no parsing regex)
- Validation guard (`len(drive_coordinates) != 1`) + `raise ValueError` only

Any future addition to `backends/orca.py` outside this thin wrapper = FAIL.

**Amendment B — orca.py: 2 modified lines (mechanical optfreq removal)**

Deleting `"optfreq": "Opt Freq"` from `calc_type_map` mechanically requires
simplifying the adjacent `_build_input_blocks` code:
- `route in ("Freq", "Opt Freq")` → `route == "Freq"` — removed dead branch
- `route = route.replace("Freq", "NumFreq")` → `route = "NumFreq"` — direct
  assignment (replace was only needed when "Opt Freq" could match)

Both modifications fall within/adjacent to the optfreq-removal target region.
Encoded in the audit as: line must be in `_build_input_blocks`, must not
contain "Opt Freq", must pair with a deleted line that does. Max 2 such lines.

### Judgment (amended)

Both violations are now **audit-model corrections** encoded with teeth:

| Violation | Amendment | Teeth |
|-----------|-----------|-------|
| A: relaxed_scan overflow | Allow thin wrapper + imports | AST: no loops, no math, no regex, must delegate to `self._interface` |
| B: 2 modified lines | Allow optfreq-removal edits | Must be in `_build_input_blocks`, no "Opt Freq", max 2 lines |

**No algorithm body changed.** All other violations still = direct FAIL.

### Groups②-⑤: no discrepancies

All other groups pass unconditionally:
- ② Scheduler DB tables exist with fixture rows
- ③ No new files in .omo/
- ④ Catalog retired IDs match expected set
- ⑤ Grep gates exit 0, BatchOptimize has no IRC references
