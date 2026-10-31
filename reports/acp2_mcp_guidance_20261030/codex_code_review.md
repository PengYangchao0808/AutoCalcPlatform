codex_consult: review in /ACPServer/AutoCalcPlatform (timeout 900s)
codex_consult: target --base main
OpenAI Codex v0.163.0
--------
workdir: /ACPServer/AutoCalcPlatform
model: gpt-6-astra
provider: openai
approval: never
sandbox: workspace-write [workdir, /tmp, $TMPDIR]
reasoning effort: high
reasoning summaries: none
session id: 01a18a81-2686-7d23-807e-a47c8fbdece4
--------
user
... (exploration transcript omitted; see session 01a18a81-2686-7d23-807e-a47c8fbdece4) ...

codex
codex
The patch blocks existing Workbench PESsearch submissions and introduces MCP information-loss and artifact-reading regressions. The new scan discovery contract also advertises the wrong coordinate precedence.

Full review comments:

- [P1] Accept the metadata sent by the PESsearch wizard — /ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:141-148
  Every `submitPESsearchTask()` request includes `method.schema_id` and `method.profile_id`, but neither appears in this new allowlist. Consequently, existing Workbench PESsearch submissions now fail with HTTP 422 (`unknown_field`) before reaching scan preparation. Preserve these supported UI metadata fields and test the actual wizard payload, as required by the [UI compatibility rule](src/acp/api/AGENTS.md#L9).

- [P2] Handle UTF-8 characters split by artifact truncation — /ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:3002-3009
  When `max_bytes` splits a multibyte character, `resolve_artifact_read()` successfully checks the complete UTF-8 body, then slices its bytes; `ArtifactRead.to_dict()` correctly encodes that fragment as base64. This new decoding check nevertheless rejects the fragment as an unverifiable binary artifact. For example, reading the first byte of `é` now fails through MCP. Truncate at a character boundary after full-body validation, preserving the [encoding and content-check invariant](src/acp_services/AGENTS.md#L14).

- [P2] Forward artifact uncertainty through the MCP status projection — /ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:5071-5073
  For a remote job with a cold cache, HTTP now returns `artifacts=[]` and `artifacts_unknown_reason="remote_cache_only"`, but `_ControllerStatus.get()` passes this through `project_task_status()`, whose field allowlist omits the new reason. MCP therefore presents an empty artifact list without explaining that discovery is unavailable. Add the field to the shared projection and test the serialized MCP response, following the [DTO-to-MCP propagation requirement](src/acp/api/AGENTS.md#L11-L12).

- [P2] Preserve public guidance identifiers during MCP serialization — /ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:503-504
  Reading `workflow.XtbConstrainedOpt` through MCP replaces `pes2ts_xtb_constrained_opt_request_v1` in this instruction with `<redacted>`: `MachineResult.to_dict()` mistakes the public identifier for a secret. HTTP returns the original section, while MCP returns altered content under the same digest and byte count, losing the request identifier agents need. Preserve reviewed public identifiers without disabling secret protection, and verify actual wire content as required by the [guidance projection rule](src/acp/api/AGENTS.md#L12).

- [P2] Publish the actual scan coordinate precedence — /ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:212-212
  When coordinates occur in both mappings, `scan_method_flags()` and `_submission_scan_coordinates()` inspect input before method—the opposite of this published contract. An agent following discovery to override an existing input coordinate through `method` will silently scan the original coordinate instead. Describe the actual input-first precedence or consistently reject conflicting carriers, following the [consumer-equivalence rule](src/acp/api/AGENTS.md#L9).
The patch blocks existing Workbench PESsearch submissions and introduces MCP information-loss and artifact-reading regressions. The new scan discovery contract also advertises the wrong coordinate precedence.

Full review comments:

- [P1] Accept the metadata sent by the PESsearch wizard — /ACPServer/AutoCalcPlatform/src/acp/api/submission_contract.py:141-148
  Every `submitPESsearchTask()` request includes `method.schema_id` and `method.profile_id`, but neither appears in this new allowlist. Consequently, existing Workbench PESsearch submissions now fail with HTTP 422 (`unknown_field`) before reaching scan preparation. Preserve these supported UI metadata fields and test the actual wizard payload, as required by the [UI compatibility rule](src/acp/api/AGENTS.md#L9).

- [P2] Handle UTF-8 characters split by artifact truncation — /ACPServer/AutoCalcPlatform/src/acp_services/entrypoints/mcp.py:3002-3009
  When `max_bytes` splits a multibyte character, `resolve_artifact_read()` successfully checks the complete UTF-8 body, then slices its bytes; `ArtifactRead.to_dict()` correctly encodes that fragment as base64. This new decoding check nevertheless rejects the fragment as an unverifiable binary artifact. For example, reading the first byte of `é` now fails through MCP. Truncate at a character boundary after full-body validation, preserving the [encoding and content-check invariant](src/acp_services/AGENTS.md#L14).

- [P2] Forward artifact uncertainty through the MCP status projection — /ACPServer/AutoCalcPlatform/src/acp/api/v1_routes.py:5071-5073
  For a remote job with a cold cache, HTTP now returns `artifacts=[]` and `artifacts_unknown_reason="remote_cache_only"`, but `_ControllerStatus.get()` passes this through `project_task_status()`, whose field allowlist omits the new reason. MCP therefore presents an empty artifact list without explaining that discovery is unavailable. Add the field to the shared projection and test the serialized MCP response, following the [DTO-to-MCP propagation requirement](src/acp/api/AGENTS.md#L11-L12).

- [P2] Preserve public guidance identifiers during MCP serialization — /ACPServer/AutoCalcPlatform/src/acp/api/guidance_pack.py:503-504
  Reading `workflow.XtbConstrainedOpt` through MCP replaces `pes2ts_xtb_constrained_opt_request_v1` in this instruction with `<redacted>`: `MachineResult.to_dict()` mistakes the public identifier for a secret. HTTP returns the original section, while MCP returns altered content under the same digest and byte count, losing the request identifier agents need. Preserve reviewed public identifiers without disabling secret protection, and verify actual wire content as required by the [guidance projection rule](src/acp/api/AGENTS.md#L12).

- [P2] Publish the actual scan coordinate precedence — /ACPServer/AutoCalcPlatform/src/acp/api/workflow_contracts.py:212-212
  When coordinates occur in both mappings, `scan_method_flags()` and `_submission_scan_coordinates()` inspect input before method—the opposite of this published contract. An agent following discovery to override an existing input coordinate through `method` will silently scan the original coordinate instead. Describe the actual input-first precedence or consistently reject conflicting carriers, following the [consumer-equivalence rule](src/acp/api/AGENTS.md#L9).
EXIT:0
