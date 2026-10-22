# Shared change request (template)

Use this template when A or C needs a change to a B-owned shared file
(`pyproject.toml`, `requirements-node.txt`, CI, the four version locations,
root/nested AGENTS, `scripts/install_systemd.sh`, Services ledgers, or the
contract manifest). B reviews and applies the change; A/C do not write these
files directly.

```yaml
requester: A | C
requested_at: 2026-10-22T00:00:00Z
dependent_task: A-NN | C-TNN
dependency_class: science-node | control | DSH | browser | docs | ledger
shared_files:
  - path: pyproject.toml
reason: >
  Why the change is required and which frozen contract or acceptance row consumes it.
exact_diff: |
  diff --git a/path b/path
  ...
validation:
  commands:
    - command: "pytest tests/... -q"
      expected: "exit 0"
  owner_evidence: reports/acp2_implementation/<candidate>/<A|C>/...
rollback: >
  Exact reverse diff or "revert commit <sha>"; state whether installed assets or
  generated files remain.
impact:
  version_bump: none | patch | minor | major
  contract_version_change: none | <old> -> <new>
  consumers_to_reconfirm: []
```
