# Bridge mutant proof

- source: `rph/packages/bridge/src/index.ts`
- sha256_before: `d74190cfdbcc9062c6c2cf6f85600eb64abc59db614ce19fa7e798711b9ffb92`
- sha256_after: `d74190cfdbcc9062c6c2cf6f85600eb64abc59db614ce19fa7e798711b9ffb92`
- restored: true
- expected_mutants: 6
- red_mutants: 6

| mutant | guard | test_exit | red | restored |
| --- | --- | --- | --- | --- |
| raw_prefix | raw-prefix rejection | 1 | true | true |
| iserror_preserved | isError preservation | 1 | true | true |
| read_before_prepare | read-before-prepare | 1 | true | true |
| operation_required_matrix | per-operation required matrix | 1 | true | true |
| artifact_ownership | project/attempt ownership | 1 | true | true |
| credential_absolute_path | credential/absolute-path refusal | 1 | true | true |
