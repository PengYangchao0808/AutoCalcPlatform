# Task 38 — B-fix-6 credential hygiene

- Date: 2026-10-24
- Plan: `.omo/plans/acp2-parallel-plan-b-runtime.md` row 38
- HEAD at fix: `324d2234911919458724df1f80c94a7756d527a4`
- Scope: redact the plaintext node-157 SSH credential from local evidence and the ignored operator config; no product code, no remote credential rotation, no production service.
- Secret handling: the credential value is not reproduced in this file, the scan output, the JSON receipt, any command, or the commit.

## 1. Problem

F4 found the node-157 SSH password in plaintext in two places:

1. `.omo/evidence/F3-acp2-parallel-plan-b-runtime.md` lines 115 and 533 — the remote release check and the cleanup transcript passed the password directly as the `sshpass` password argument.
2. The ignored local `cccp.yaml` — the `cluster.nodes[0]` entry for `node-157` stored a plaintext password field.

`git ls-files cccp.yaml` is empty; the file is local and untracked.

## 2. Redaction

### F3 evidence (findings and verdict unchanged)

Both invocations now use SSH env indirection without a literal password argument:

```bash
SSHPASS="$ACP_REMOTE_PASSWORD_NODE_157" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 <user>@192.168.1.100 \
  'python3 -c "import json,glob; p=glob.glob(\"/home/<user>/acp_code/releases/1c79785a657c6614/.complete\")[0]; d=json.load(open(p)); print(d.get(\"release_id\")); print(len(d.get(\"files\",{})))"'
```

```bash
SSHPASS="$ACP_REMOTE_PASSWORD_NODE_157" sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 <user>@192.168.1.100 'rm -rf /home/<user>/acp_code_f3r /home/<user>/acp_jobs_f3r /home/<user>/acp_code/releases/.staging/7698b06589bb30c2.7167f157be10; rm -f /home/<user>/acp_code/releases/.refs/7698b06589bb30c2.5436479f6050.ref'
```

The command structure, target host, and observed results (`release_id=1c79785a657c6614`, `files=510`) are preserved. `ACP_REMOTE_PASSWORD_NODE_157` is the documented `RemoteNode.resolved_password()` convention (`src/acp/scheduler/remote/config.py`).

### F4 evidence

Literal password-flag and password-key references in the review prose and row-38 acceptance text were reworded to secret-free references. The recorded scan command uses the equivalent POSIX regex form so the evidence file itself cannot self-match.

### Local `cccp.yaml`

The plaintext password field was removed and replaced with a comment naming the env reference:

```yaml
    # SSH credential via env ACP_REMOTE_PASSWORD_NODE_157 (plaintext never stored)
```

YAML parses; the `node-157` entry has no `password` key. Runtime lookup falls back to `ACP_REMOTE_PASSWORD_NODE_157`, which is where the credential must live for acceptance runs. The file remains untracked and was not committed.

## 3. Scan

Full transcript: `reports/acp2_implementation/bb45a46/B/Bfix6/credential-scan.txt`
Machine receipt: `reports/acp2_implementation/bb45a46/B/Bfix6/credential-redaction.json`

```bash
$ grep -rn "sshpass[[:space:]]-p" .omo/evidence reports
# no matches (exit 1; match_lines=0; recorded in the scan file as the equivalent regex "sshpass[[:space:]]-p")

$ grep -c "password[[:space:]]*:" cccp.yaml
0

$ git ls-files cccp.yaml
# empty (tracked_count=0)

$ grep -rn "sshpass" .omo/evidence reports --exclude=credential-scan.txt
# 5 references: 2 in F3 are the secret-free SSH env-indirection form (sshpass -e);
# 3 in F4 are review prose / the recorded scan command. No credential value.

$ grep -rn "password[[:space:]]*:" .omo/evidence reports --exclude=credential-scan.txt
# no matches (exit 1; match_lines=0)
```

Counts: 2 F3 command invocations redacted; 3 F4 literal flag references and 3 password-key references rewritten; 1 plaintext field removed from `cccp.yaml`; 0 secret occurrences remain in evidence/reports; 0 plaintext password fields remain in `cccp.yaml`.

## 4. B05 credential scan

`reports/acp2_implementation/bb45a46/B/B05/launch-credential-check.txt` is unchanged and still reports:

```text
credential_problems= []
PASS: launch descriptor contains no credential key/value (constraint flags are not secrets)
```

`PYTHONPATH=src <acp2-dev>/venvs/integration/bin/python -m pytest tests/test_services_mcp.py -q` → `39 passed`.

## 5. Rotation note

The node-157 test credential was exposed in local evidence (and in the local ignored config). The credential owner should rotate it. Rotation, remote credential change, and any production `acp.service`/8765 action are outside this repository task and were not performed.

## 6. Commit

`docs(evidence): redact node-157 credential material` — evidence and plan only; `cccp.yaml` excluded.
