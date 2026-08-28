VERDICT: REJECT

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

### Judgment

Per plan: "算法体变更与接口范围超限不存在'记录放行'——直接 FAIL"

Both violations are classified as direct FAILs. No documented ALLOWED exemption
applies (only ⑤-class grep gates may have exemptions).

### Groups②-⑤: no discrepancies

All other groups pass unconditionally:
- ② Scheduler DB tables exist with fixture rows
- ③ No new files in .omo/
- ④ Catalog retired IDs match expected set
- ⑤ Grep gates exit 0, BatchOptimize has no IRC references
