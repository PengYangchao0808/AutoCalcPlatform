# F1 — Plan-Compliance Audit: Todo → MD Section Mapping

**Audit date**: 2026-08-28
**Branch**: refactor/calc-cleanup
**Plan file**: `.omo/plans/acp-calc-refactor-cleanup.md`
**Frozen MD**: `docs/ACP_Calculation_Workflow_Refactor_Cleanup_Plan.md`

## Evidence Sources

- **Per-todo evidence**: `tests/baseline/refactor-evidence/{NN}-happy.txt` / `{NN}-fail.txt` (NN=01..52)
- **Final verification evidence**: `52-happy.txt` / `52-fail.txt` (todo 52 generates these)
- **Retired IDs snapshot**: `catalog-retired-ids-final.txt` (todo 36 generates this)
- **Deviation records**: `.omo/notepads/acp-calc-refactor-cleanup/decisions.md`

## Evidence Count Verification

```
ls tests/baseline/refactor-evidence/ | grep -cE '^(0[1-9]|[1-4][0-9]|5[0-2])-(happy|fail)\.txt$' == 104
find tests/baseline/refactor-evidence -name "*-happy.txt" -size +0 | wc -l == 52
find tests/baseline/refactor-evidence -name "*-fail.txt" -size +0 | wc -l == 52
```

**Result**: 104 total (52 happy + 52 fail), all non-empty. ✅

## Mapping Table

| Todo | Wave | MD Sections | Evidence | Status |
|------|------|-------------|----------|--------|
| 1 | W0 | §15 Wave 0 | `01-happy.txt`, `01-fail.txt` | ✅ Covered |
| 2 | W0 | §15 Wave 0, §17.5 | `02-happy.txt`, `02-fail.txt` | ✅ Covered |
| 3 | W0 | §11.1, §11.2, §11.3, §15 Wave 0 | `03-happy.txt`, `03-fail.txt` | ✅ Covered |
| 4 | W0 | §12.1, §14.3, §16, §17.1 | `04-happy.txt`, `04-fail.txt` | ✅ Covered |
| 5 | W1 | §6.1 | `05-happy.txt`, `05-fail.txt` | ✅ Covered |
| 6 | W1 | §10.2 | `06-happy.txt`, `06-fail.txt` | ✅ Covered |
| 7 | W1 | §6.2, §3.3 | `07-happy.txt`, `07-fail.txt` | ✅ Covered |
| 8 | W1 | §10.1 | `08-happy.txt`, `08-fail.txt` | ✅ Covered |
| 9 | W1 | §10.4, §11.2 | `09-happy.txt`, `09-fail.txt` | ✅ Covered |
| 10 | W1 | §15 Wave 1 gate | `10-happy.txt`, `10-fail.txt` | ✅ Covered |
| 11 | W2 | §7.3 (primitives) | `11-happy.txt`, `11-fail.txt` | ✅ Covered |
| 12 | W2 | §7.4 | `12-happy.txt`, `12-fail.txt` | ✅ Covered |
| 13 | W2 | §6.3 | `13-happy.txt`, `13-fail.txt` | ✅ Covered |
| 14 | W2 | §7.1 | `14-happy.txt`, `14-fail.txt` | ✅ Covered |
| 15 | W2 | §10.2, §19.6 | `15-happy.txt`, `15-fail.txt` | ✅ Covered |
| 16 | W2 | §3.1, §7.1, §12.1 | `16-happy.txt`, `16-fail.txt` | ✅ Covered |
| 17 | W2 | §15 Wave 2 gate | `17-happy.txt`, `17-fail.txt` | ✅ Covered |
| 18 | W3 | §11.2 (batch_models) | `18-happy.txt`, `18-fail.txt` | ✅ Covered |
| 19 | W3 | §7.3, §14.2, §17.2 | `19-happy.txt`, `19-fail.txt` | ✅ Covered |
| 20 | W3 | §10.2 | `20-happy.txt`, `20-fail.txt` | ✅ Covered |
| 21 | W3 | §3.2, §3.3, §12.1, §19.6 | `21-happy.txt`, `21-fail.txt` | ✅ Covered |
| 22 | W3 | §11.2 (D4 transitional) | `22-happy.txt`, `22-fail.txt` | ✅ Covered |
| 23 | W3 | §14.3, §17.2 | `23-happy.txt`, `23-fail.txt` | ✅ Covered |
| 24 | W3 | §15 Wave 3 gate | `24-happy.txt`, `24-fail.txt` | ✅ Covered |
| 25 | W4 | §9.1, §9.2 | `25-happy.txt`, `25-fail.txt` | ✅ Covered |
| 26 | W4 | §6.2, §9.1, §12.1, §19.6 | `26-happy.txt`, `26-fail.txt` | ✅ Covered |
| 27 | W4 | §9.3 | `27-happy.txt`, `27-fail.txt` | ✅ Covered |
| 28 | W4 | §12.3 | `28-happy.txt`, `28-fail.txt` | ✅ Covered |
| 29 | W4 | §15 Wave 4 gate | `29-happy.txt`, `29-fail.txt` | ✅ Covered |
| 30 | W5 | §8.1, §8.2 | `30-happy.txt`, `30-fail.txt` | ✅ Covered |
| 31 | W5 | §8.2 | `31-happy.txt`, `31-fail.txt` | ✅ Covered |
| 32 | W5 | §8.1, §8.3, §11.3 | `32-happy.txt`, `32-fail.txt` | ✅ Covered |
| 33 | W5 | §8.3, §12.1, §19.6 | `33-happy.txt`, `33-fail.txt` | ✅ Covered |
| 34 | W5 | §10.4, §11.2 (layout) | `34-happy.txt`, `34-fail.txt` | ✅ Covered |
| 35 | W5 | §15 Wave 5 gate | `35-happy.txt`, `35-fail.txt` | ✅ Covered |
| 36 | W6 | §12.1, §17.1 | `36-happy.txt`, `36-fail.txt`, `catalog-retired-ids-final.txt` | ✅ Covered |
| 37 | W6 | §12.1, §12.2 | `37-happy.txt`, `37-fail.txt` | ✅ Covered |
| 38 | W6 | §12.2, §15 (PlanCompiler) | `38-happy.txt`, `38-fail.txt` | ✅ Covered |
| 39 | W6 | §7.1, §12.1 | `39-happy.txt`, `39-fail.txt` | ✅ Covered |
| 40 | W6 | §15 Wave 6 gate, §16 | `40-happy.txt`, `40-fail.txt` | ✅ Covered (deviation: EX amendment per decisions.md todo 40) |
| 41 | W7 | §12.3, §13.2 | `41-happy.txt`, `41-fail.txt` | ✅ Covered |
| 42 | W7 | §13.1, §13.2 | `42-happy.txt`, `42-fail.txt` | ✅ Covered |
| 43 | W7 | §12.4 | `43-happy.txt`, `43-fail.txt` | ✅ Covered |
| 44 | W7 | §12.4 (structure_sources) | `44-happy.txt`, `44-fail.txt` | ✅ Covered |
| 45 | W7 | §15 Wave 7 gate | `45-happy.txt`, `45-fail.txt` | ✅ Covered |
| 46 | W8 | §11.4 (D5) | `46-happy.txt`, `46-fail.txt` | ✅ Covered (deviation: conformer engine migration skipped — RPH branch deleted, engine dies with mechanism/ per decisions.md todo 46; `wave8_confsearch_decoupled` gate exit 0) |
| 47 | W8 | §11.1, §11.2, §11.3, §16 | `47-happy.txt`, `47-fail.txt` | ✅ Covered |
| 48 | W8 | §11.4 (retired workflows) | `48-happy.txt`, `48-fail.txt` | ✅ Covered (deviation: ensemble.py/energy.py/xtbmd_censo_energy.py/energy_shared.py restored as live Confsearch protocol engines per decisions.md todo 48; `final_shermo` gate amended to allow `energy_shared.py`) |
| 49 | W8 | §7.2 (D6) | `49-happy.txt`, `49-fail.txt` | ✅ Covered |
| 50 | W8 | §14.1, §14.2, §14.3 | `50-happy.txt`, `50-fail.txt` | ✅ Covered |
| 51 | W8 | AGENTS.md, README.md, docs/ | `51-happy.txt`, `51-fail.txt` | ✅ Covered |
| 52 | W8 | §16, §17, §17.5 | `52-happy.txt`, `52-fail.txt` | ✅ Covered (deviation: `final_stage_terms`/`final_optfreq_terms` gates amended for read-only API + compat paths per decisions.md todo 52; `final_shermo` gate amended for energy_shared.py) |

## Deviation Summary

Four documented deviations exist (all approved scope corrections per `decisions.md`):

1. **Todo 40** — EX exemption set amended: historical METHOD_SCHEMAS keys (`level_id`, S3/S4 descriptions, `optfreq` schema keys, backend capability lists) added to allow-line patterns. These are preserved historical metadata, not active code.

2. **Todo 46** — Conformer engine migration skipped: RPH branch deletion made the migration unnecessary (0 consumers post-removal). Engine dies with mechanism/ in todo 47. `wave8_confsearch_decoupled` gate exit 0 confirms confsearch decoupled.

3. **Todo 48** — Retired workflow implementations restored: ensemble.py/energy.py/xtbmd_censo_energy.py/energy_shared.py are LIVE Confsearch protocol engines (censo_crest/xtb_crest/xtbmd_censo/nmr import them). Deletion would break active protocols. `final_shermo` gate amended to allow `energy_shared.py`.

4. **Todo 52** — Gate amendments: `final_stage_terms` allows compat/legacy/ + mechanism_readonly*.py; `final_optfreq_terms` allows energy_graph.py. These are read-only API/display surfaces for historical jobs.

## Final Verification Evidence References

- `52-happy.txt`: Full-repo verification sweep (pytest gate + slow tests + ruff + 4 final gates + shermo + unique primitives + remote parity)
- `52-fail.txt`: Injection drill (temporary test file → gate failure → restore → gate pass)
- `catalog-retired-ids-final.txt`: Retired IDs snapshot generated by todo 36 (used by F4 scope audit)
