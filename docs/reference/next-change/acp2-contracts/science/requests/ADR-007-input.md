# ACP2 Plan A — ADR-007 Input: External Science Plugin Trust, Manifest and Rollback (A-21a)

Status: **input for the platform owner's shared ADR-007.** A (science track)
owns this manifest/trust text; the platform owner owns the ADR decision and
any platform capability beyond the CCCP plugin boundary. This document does
not install, enable or execute a plugin, and it does not claim a real QC run.

Reference rows: `Plan A §A-21/A-22/A-23`, `GAP-7`, and the frozen ACP2 science
contracts (`capability.schema.json`, `recipe.schema.json`, `task-binding.schema.json`,
`handoff-H2.json`). The implementation is the existing manifest module
`src/cccp/plugin/metadata.py`; A-21a does **not** create a second manifest module.

## 1. Boundary this manifest serves

The plugin host is an **in-process, metadata-first seam**, not a sandbox. It
does not create a second execution station, does not bypass the fifteen
`TaskKind` task cores and does not grant process, permission or queue limits
(those belong to the platform owner).

| Capability kind | Meaning | Execution |
|---|---|---|
| `pure_analysis` | Reads existing artifacts and returns a `TaskResult`. | In-process handler; no subprocess, no task core. |
| `compute` | Names exactly one of the fifteen existing task kinds. | Only the built-in `cccp.calculation` task layer executes it; the plugin host never invokes a compute handler. |

A manifest whose capabilities are all `pure_analysis` must declare
`resources.requires_subprocess=false`. A `compute` capability must declare
`delegates_to_task_kind` from the frozen fifteen-kind inventory and must not
be invoked through `invoke_capability`.

## 2. Manifest fields (one manifest, backward-compatible)

`PluginMetadata.to_dict()` / `PluginMetadata.from_dict()` are the canonical
manifest adapter. A-21a adds these fields; every one has a conservative
default so a legacy manifest still loads.

| Field | Type | Default | Rule |
|---|---|---|---|
| `capabilities[].kind` | `compute` \| `pure_analysis` | `pure_analysis` | Kind is explicit and cross-checked with delegation/subprocess. |
| `capabilities[].implementation_id` | dotted id | `""` (legacy) | New manifests should name the implementation (e.g. `vendor.science.validator`). |
| `capabilities[].delegates_to_task_kind` | task kind \| null | `null` | Required for `compute`; forbidden for `pure_analysis`. |
| `capabilities[].recipe_refs` | list of `{recipe_id, schema_version, digest}` | `[]` | Declarative only; no execution and no auto-selection. |
| `capabilities[].validator_refs` | list of `{validator_id, validator_version, required}` | `[]` | Uses the recipe validator vocabulary (`check.`, `software.`, `human.`). |
| `science_version_range` | range string | `">=1,<2"` | Host ACP2 science version must fall inside it. |
| `entry_point_group_version` | integer | `1` | Only version 1 of `cccp.science_plugins` is read. |
| `distribution_digest` | `sha256:<64 hex>` \| null | `null` | Digest of the installed distribution artifact; evidence only. |
| `fixture_evidence` | list of `{fixture_id, digest, verified, description}` | `[]` | A digest is mandatory for each record. |
| `trust_level` | `untrusted` \| `declared` \| `trusted` | `untrusted` | `trusted` requires verified fixture evidence with a digest. |
| `license_policy` | `{accepted_licenses, unknown_policy}` | platform default | `unknown_policy` is frozen to `reject`. |

Legacy manifests that lack these fields load through
`PluginMetadata.from_dict` as `untrusted`, with no distribution digest and no
fixture evidence. Unknown historical fields are ignored; they never grant
trust, execution or verification eligibility.

## 3. Trust level semantics

- `untrusted` is the lowest level and the only default. Installation,
  version compatibility, a valid API range, a declared validator or a
  `default_enabled` flag never promote a plugin.
- `declared` records an explicit operator declaration without fixture
  evidence. It still cannot claim `verified`.
- `trusted` requires at least one `fixture_evidence` record with
  `verified=true` and a real `sha256:<64 hex>` digest. A bare
  `trust_level="trusted"` manifest is rejected before discovery completes.
- `validated` and `verified` are accepted spelling aliases for `trusted` and
  serialise as `trusted`; they do not bypass the evidence requirement.
- Trust is reported as a separate projection. `described`, `installed`,
  `enabled` and `verified` are never collapsed into one boolean.

Decision requested: the owner should define who verifies fixture digests and
where the verification record lives. The manifest can only carry a claim; it
cannot verify itself.

## 4. Licence policy

The default accepted set is the SPDX set frozen in
`metadata.DEFAULT_ACCEPTED_LICENSES` (MIT, Apache-2.0, BSD-2-Clause,
BSD-3-Clause, ISC, MPL-2.0, LGPL-2.1-only, LGPL-3.0-only, GPL-2.0-only,
GPL-3.0-only, AGPL-3.0-only, Unlicense, CC0-1.0).

- Empty licences and unknown identifiers raise the typed
  `PluginLicenseError` before any loader exists.
- `PluginLicensePolicy.narrowed(excluded=...)` lets the owner accept fewer
  licences; the manifest cannot widen the policy itself.
- The policy is part of the manifest record and is serialised with it, so a
  later reader can tell which policy admitted the plugin.

Decision requested: confirm the accepted set (in particular whether
copyleft and/or proprietary licences are in scope) and whether the owner
wants a per-project override or a single platform policy.

## 5. Entry-point discovery (group version 1)

- Group: `cccp.science_plugins`; manifest file: `cccp_science_plugin.json`.
- Scanning uses `importlib.metadata.entry_points()` and
  `Distribution.read_text("cccp_science_plugin.json")`. **No plugin module is
  imported during discovery.**
- The entry point is wrapped in a lazy loader. `entry_point.load()` runs only
  after the operator explicitly enables the plugin and calls
  `PluginHost.load`. `default_enabled` is declarative metadata and never
  auto-enables anything.
- Missing or malformed manifest, unsupported group version, reserved/
  duplicate name, reserved/duplicate capability, incompatible API/science
  range, empty/unknown licence: all fail closed with a typed error.
- A disabled plugin raises `PluginDisabledError`; an unknown plugin or
  undeclared capability/validator raises `PluginUnavailableError` or
  `PluginContractViolationError`.

## 6. Compatibility

| Contract | Accepted now | Unknown behaviour |
|---|---|---|
| Plugin API `api_version` | `>=1,<2` | `PluginVersionMismatchError`; never load. |
| Manifest schema version | `1` | Reject unknown major versions. |
| ACP2 science version | `>=1,<2` | `PluginVersionMismatchError`; never load. |
| Entry-point group version | `1` | Reject; do not guess a group layout. |
| Historical manifest fields | read-only | Preserve, but grant no execution/recovery eligibility. |

The adapter is intentionally one-way conservative: an old manifest can load
as `untrusted`, but a new manifest cannot become `trusted` merely because an
old reader ignored the field.

## 7. Rollback input

If the owner rejects the trust model or the entry-point layout:

1. **Disable, do not delete.** Remove the plugin from the enabled set; keep
   the manifest and any produced artifacts for audit. A disabled plugin is
   not loaded and cannot be silently substituted by a built-in result.
2. **Pin group version.** A future layout must use a new group version;
   version 1 readers reject it instead of half-reading it.
3. **Preserve old manifests.** The adapter keeps old records readable as
   `untrusted`; rollback does not require rewriting historical evidence.
4. **Keep evidence immutable.** Never edit `fixture_evidence` digests in
   place. A changed fixture is a new evidence record (new `fixture_id`),
   not a silent digest swap.
5. **No built-in regression.** Reverting the extension leaves the built-in
   catalog and the fifteen task cores untouched; no core source is edited by
   an external plugin install or uninstall.
6. **Re-run the gate.** After rollback, run
   `tests/test_cccp_plugin_pilot.py`, `tests/test_cccp_plugin_sdk.py`,
   `tests/test_acp2_science_plugin_registry.py` (A-21b) and
   `tests/test_architecture_invariants.py`.

Decision requested: state the rollback trigger (owner rejection, failed
fixture verification, licence change, or incompatible distribution upgrade)
and whether historical plugin-produced artifacts remain readable when the
plugin is disabled.

## 8. Non-goals

- No process-level sandbox, no permission/queue/account model, no remote
  execution policy.
- No second manifest module, no second capability registry and no plugin
  auto-selection.
- No claim that a static manifest test is a real plugin install or a real QC
  result; A-22 owns the isolated install/invocation evidence.
