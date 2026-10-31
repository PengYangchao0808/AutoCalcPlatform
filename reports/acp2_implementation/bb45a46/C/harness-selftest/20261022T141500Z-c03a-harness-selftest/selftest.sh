#!/usr/bin/env bash
set +e
source /ACPServer/acp2-development/data/acceptance-env-c.sh >/dev/null 2>&1
cd "$ACP2_C_SOURCE" || exit 1
RUN_ID="20261022T141500Z-c03a-harness-selftest"
EVIDENCE="$ACP2_EVIDENCE/C/harness-selftest/$RUN_ID"
rm -rf "$EVIDENCE"
mkdir -p "$EVIDENCE"
RESULTS="$EVIDENCE/results.tsv"
: > "$RESULTS"
printf '%s\n' "$0" > "$EVIDENCE/argv.txt"
printf '%s\n' "$PWD" > "$EVIDENCE/cwd.txt"
printf '%s\n' "$ACP2_PYTHON" > "$EVIDENCE/interpreter.txt"
printf 'RUN_ID=%s\nACP2_EVIDENCE=%s\nACP2_PROJECT_ROOT=%s\n' "$RUN_ID" "$ACP2_EVIDENCE" "$ACP2_PROJECT_ROOT" > "$EVIDENCE/env.txt"
export ACP2_RUN_ID="$RUN_ID"

run_case() {
  local name="$1" expected="$2" cmd="$3"
  bash -c "$cmd" >"$EVIDENCE/$name.out" 2>"$EVIDENCE/$name.err"
  local rc=$?
  local verdict="ok"
  [ "$rc" = "$expected" ] || verdict="MISMATCH"
  printf '%s\t%s\t%s\t%s\n' "$name" "$expected" "$rc" "$verdict" >> "$RESULTS"
}

H="$ACP2_PYTHON scripts/acceptance/acp2_experience.py"

run_case help 0 "$H --help"
run_case py_compile 0 "$ACP2_PYTHON -m py_compile scripts/acceptance/acp2_experience.py scripts/acceptance/acp2_experience_scenarios.py"
run_case unknown_scenario 3 "$H --scenario does-not-exist --out $EVIDENCE/neg-unknown --timeout 60"
run_case baseline_live_forbidden 3 "$H --scenario baseline --mode live --out $EVIDENCE/neg-live --timeout 60"
run_case ebl_live_dry_run 0 "$H --scenario evidence-browser-live --mode live --dry-run --out $EVIDENCE/ebl-live --timeout 60"
run_case bogus_subcase_a 3 "$H --scenario cross-domain-live --subcase control --dry-run --out $EVIDENCE/neg-sub-a --timeout 60"
run_case bogus_subcase_b 3 "$H --scenario baseline --subcase nmr --dry-run --out $EVIDENCE/neg-sub-b --timeout 60"
run_case unknown_namespaced_phase 3 "$H --scenario baseline --phase experience:fixture --dry-run --out $EVIDENCE/neg-phase --timeout 60"
run_case missing_out 3 "$H --scenario baseline --timeout 60 --dry-run"
run_case missing_timeout 3 "$H --scenario baseline --out $EVIDENCE/neg-timeout --dry-run"
run_case mutually_exclusive_modes 3 "$H --scenario baseline --verify-receipt $EVIDENCE/nope.json --out $EVIDENCE/neg-mode --timeout 60"

cat > "$EVIDENCE/manifest.json" <<JSON
{"schema":"acp2.experience.manifest-extension.v1","schema_version":"1.0.0","candidate_id":"450f2cb8fe1f5b16264b3fe878a021e77f02eba9","evidence_root":"$ACP2_EVIDENCE","paths":{"c_source":"$ACP2_C_SOURCE"},"fixtures":[],"extensions":{}}
JSON
run_case baseline_dry_run 0 "$H --scenario baseline --out $EVIDENCE/dry-run-baseline --timeout 60 --dry-run --manifest $EVIDENCE/manifest.json"
run_case unimplemented_non_dry 2 "$H --scenario baseline --out $EVIDENCE/unimplemented --timeout 60"

printf '{"schema":"acp2.experience.manifest-extension.v1","schema_version":"1.0.0"}\n' > "$EVIDENCE/manifest-missing-candidate-id.json"
run_case manifest_missing_candidate_id 2 "$H --scenario baseline --out $EVIDENCE/neg-manifest --timeout 60 --manifest $EVIDENCE/manifest-missing-candidate-id.json"

rm -rf "$EVIDENCE/four-tuple"
mkdir -p "$EVIDENCE/four-tuple"
run_case four_tuple_ebl_fixture 0 "$H --scenario evidence-browser-live --mode fixture --dry-run --out $EVIDENCE/four-tuple/ebl-fixture --timeout 60"
run_case four_tuple_ebl_live 0 "$H --scenario evidence-browser-live --mode live --dry-run --out $EVIDENCE/four-tuple/ebl-live --timeout 60"
run_case four_tuple_rl_control 0 "$H --scenario research-loop-live --subcase control --dry-run --out $EVIDENCE/four-tuple/rl-control --timeout 60"
run_case four_tuple_rl_search 0 "$H --scenario research-loop-live --subcase search --dry-run --out $EVIDENCE/four-tuple/rl-search --timeout 60"
run_case four_tuple_cd_nmr 0 "$H --scenario cross-domain-live --subcase nmr --dry-run --out $EVIDENCE/four-tuple/cd-nmr --timeout 60"
run_case four_tuple_cd_thermo 0 "$H --scenario cross-domain-live --subcase thermo --dry-run --out $EVIDENCE/four-tuple/cd-thermo --timeout 60"
run_case four_tuple_cd_selectivity 0 "$H --scenario cross-domain-live --subcase selectivity --dry-run --out $EVIDENCE/four-tuple/cd-selectivity --timeout 60"

"$ACP2_PYTHON" - "$EVIDENCE" "$RUN_ID" <<'PY'
import glob, json, sys
from pathlib import Path
root = Path(sys.argv[1])
run_id = sys.argv[2]
rows = []
for path in glob.glob(str(root / "four-tuple" / "**" / "experience_*.json"), recursive=True):
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    rows.append(
        {
            "owner": document["owner"],
            "scenario": document["scenario"],
            "phase": document["phase"],
            "run_id": document["run_id"],
            "receipt": str(Path(path).relative_to(root)),
        }
    )
tuples = [(row["owner"], row["scenario"], row["phase"], row["run_id"]) for row in rows]
payload = {
    "run_id": run_id,
    "rows": rows,
    "count": len(rows),
    "unique_count": len(set(tuples)),
    "all_unique": len(rows) == len(set(tuples)),
    "expected_counts": {"evidence_browser_live": 2, "research_loop_live": 2, "cross_domain_live": 3},
}
(root / "four-tuples.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
verdict = "ok" if payload["all_unique"] and len(rows) == 7 else "MISMATCH"
with (root / "results.tsv").open("a", encoding="utf-8") as handle:
    handle.write(f"four_tuple_uniqueness\t1\t{len(rows)}-rows/{payload['unique_count']}-unique\t{verdict}\n")
PY

POS="$ACP2_PROJECT_ROOT/docs/reference/next-change/acp2-contracts/runtime/samples/acceptance/positive/b-artifact-read.json"
NEG="$ACP2_PROJECT_ROOT/docs/reference/next-change/acp2-contracts/runtime/samples/acceptance/negative/corrupt-digest.json"
run_case verify_receipt_positive 0 "$H --verify-receipt $POS"
run_case verify_receipt_corrupt_digest 1 "$H --verify-receipt $NEG"
run_case verify_receipt_own_result 0 "$H --verify-receipt $(ls $EVIDENCE/dry-run-baseline/experience_*.json)"

SELF="$EVIDENCE/verify-receipt/self-reference.json"
mkdir -p "$EVIDENCE/verify-receipt"
cp "$(ls $EVIDENCE/dry-run-baseline/experience_*.json)" "$SELF"
cp "$EVIDENCE/dry-run-baseline/dry-run.txt" "$EVIDENCE/verify-receipt/dry-run.txt"
"$ACP2_PYTHON" - "$SELF" <<'PY'
import hashlib, json, os, sys
path = sys.argv[1]
document = json.loads(open(path, encoding="utf-8").read())
digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
document["cwd"] = os.path.dirname(path)
for artifact in document["artifacts"]:
    artifact["path"] = os.path.basename(artifact["path"])
document["artifacts"].append(
    {"path": os.path.basename(path), "size_bytes": os.path.getsize(path), "sha256": digest}
)
open(path, "w", encoding="utf-8").write(json.dumps(document, indent=2, sort_keys=True) + "\n")
PY
run_case verify_receipt_self_reference 1 "$H --verify-receipt $SELF"

printf '# Clean\n\nNo placeholder here.\n\n```\nplaceholder inside fence is ignored\n```\n\n<!-- hidden comment is ignored -->\n' > "$EVIDENCE/placeholder-clean.md"
printf '# Dirty\n\nUse a real scenario ID, not a placeholder like the one below.\n\nUse a placeholder here: body text\n' > "$EVIDENCE/placeholder-dirty.md"
printf '# Dirty body\n\nUse <scenario> here.\n' > "$EVIDENCE/placeholder-dirty.md"
run_case check_doc_placeholders_clean 0 "$H --check-doc-placeholders $EVIDENCE/placeholder-clean.md"
run_case check_doc_placeholders_dirty 1 "$H --check-doc-placeholders $EVIDENCE/placeholder-dirty.md"
run_case check_doc_placeholders_missing 3 "$H --check-doc-placeholders $EVIDENCE/does-not-exist.md"

mkdir -p "$EVIDENCE/redaction"
cat > "$EVIDENCE/redaction/manifest.secret.json" <<JSON
{"schema":"acp2.experience.manifest-extension.v1","schema_version":"1.0.0","candidate_id":"450f2cb8fe1f5b16264b3fe878a021e77f02eba9","evidence_root":"$ACP2_EVIDENCE","api_key":"SUPERSECRET","credential_sources":{"model":"Bearer abcdef123456"},"model":{"endpoint":"http://127.0.0.1/probe","token":"SUPERSECRET"}}
JSON
run_case redaction_dry_run 0 "$H --scenario baseline --out $EVIDENCE/redaction/result --timeout 60 --dry-run --manifest $EVIDENCE/redaction/manifest.secret.json"
if grep -R "SUPERSECRET" "$EVIDENCE/redaction/result" >/dev/null 2>&1; then
  printf 'redaction_raw_absent\t0\tfound\tMISMATCH\n' >> "$RESULTS"
else
  printf 'redaction_raw_absent\t0\tabsent\tok\n' >> "$RESULTS"
fi
if grep -R '\*\*\*' "$EVIDENCE/redaction/result" >/dev/null 2>&1; then
  printf 'redaction_mask_present\t0\tpresent\tok\n' >> "$RESULTS"
else
  printf 'redaction_mask_present\t0\tmissing\tMISMATCH\n' >> "$RESULTS"
fi

run_case append_only_first 0 "$H --scenario baseline --out $EVIDENCE/append-only --timeout 60 --dry-run"
run_case append_only_second 3 "$H --scenario baseline --out $EVIDENCE/append-only --timeout 60 --dry-run"

"$ACP2_PYTHON" - "$RESULTS" "$EVIDENCE" <<'PY'
import json, sys
from pathlib import Path
results_path = Path(sys.argv[1])
root = Path(sys.argv[2])
rows = []
for line in results_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    name, expected, actual, verdict = line.split("\t")
    rows.append(
        {
            "name": name,
            "expected_exit": expected,
            "actual_exit": actual,
            "verdict": verdict,
        }
    )
payload = {
    "schema": "acp2.experience.harness-selftest.v1",
    "owner": "C",
    "scenario": "harness-selftest",
    "run_id": Path(root).name,
    "candidate_revision": "450f2cb8fe1f5b16264b3fe878a021e77f02eba9",
    "interpreter": __import__("sys").executable,
    "checks": rows,
    "all_ok": all(row["verdict"] == "ok" for row in rows),
    "note": "T-06 harness self-check evidence; scenario bodies and P0 PASS rows belong to T-07.",
}
(root / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2, sort_keys=True))
PY

unset ACP2_RUN_ID
