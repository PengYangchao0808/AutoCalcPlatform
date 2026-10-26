# H2 C-side ten-row summary (from H2.json, itself copied from source result files)

aggregate: result=NOT_VERIFIED exit_code=2 run_id=h2-c-closeout-20261022T223404Z-704517
reason: h2_c_side_open: 3 of 10 P0 scenarios not PASS: mcp-live=NOT_VERIFIED(controller_unavailable); dsh-discovery-live=NOT_VERIFIED(controller_unavailable); model-live=NOT_VERIFIED(no_model_credential|controller_unavailable:no_approved_provider_configured;GET http://127.0.0.1:18765/api/health -> URLError: <urlopen error [Errno 111] Connection refused>). H2 C side stays open until every P0 scenario is PASS; total H2 sign-off belongs to B.

| scenario | result | exit | interpreter | test_revision | reason |
|---|---|---|---|---|---|
| baseline | PASS | 0 | c | 68586db4 |  |
| contracts | PASS | 0 | c | 68586db4 |  |
| workspace | PASS | 0 | c | 68586db4 |  |
| harness-selftest | PASS | 0 | c | 2b56235a |  |
| node-dsh-install | PASS | 0 | c | 68586db4 |  |
| browser-install | PASS | 0 | c | 51a71641 |  |
| bridge-contract | PASS | 0 | c | 4fa39bb3 |  |
| mcp-live | NOT_VERIFIED | 2 | integration | aecaea61 | controller_unavailable |
| dsh-discovery-live | NOT_VERIFIED | 2 | integration | 712af267 | controller_unavailable |
| model-live | NOT_VERIFIED | 2 | integration | 51a71641 | no_model_credential|controller_unavailable |

Every row additionally carries scope/command/cwd/candidate/started_at/finished_at/artifacts+sha256/usage/
product_import/harness_path, copied verbatim from its receipt_path.
