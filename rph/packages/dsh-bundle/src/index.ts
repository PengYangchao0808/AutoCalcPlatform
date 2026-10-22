/**
 * DSH bundle surface.
 *
 * Only public DSH interface *strings* are named here. DSH itself is installed
 * outside this workspace at `$ACP2_TOOLS/deepseek-harness`; this package must
 * never import DSH private source paths or vendor a DSH fork.
 */

import { BRIDGE_OPERATIONS, type BridgeOperation } from "@rph/bridge";

/** Public DSH MCP client plugin package name (official interface). */
export const DSH_MCP_CLIENT_PLUGIN = "@deepseek-ai/dsh-mcp-client";

/** Public DSH profile names used by the experience client. */
export const DSH_PROFILE_WEB = "web";
export const DSH_PROFILE_HEADLESS = "headless";

/** Public profile union. */
export type BundleProfileName =
  | typeof DSH_PROFILE_WEB
  | typeof DSH_PROFILE_HEADLESS;

/** All profiles this bundle knows how to describe. */
export const BUNDLE_PROFILE_NAMES: readonly BundleProfileName[] = Object.freeze([
  DSH_PROFILE_WEB,
  DSH_PROFILE_HEADLESS,
]);

/** Declarative bundle profile; no DSH private class is referenced. */
export interface BundleProfile {
  readonly name: BundleProfileName;
  readonly plugin: typeof DSH_MCP_CLIENT_PLUGIN;
  readonly bridgeOperations: readonly BridgeOperation[];
  readonly localOnly: boolean;
}

/**
 * Describe one bundle profile.
 *
 * `web` binds loopback only; `headless` is for CI. Both use the same official
 * plugin package and the same bridge operation surface.
 */
export function bundleProfile(name: BundleProfileName): BundleProfile {
  if (name !== DSH_PROFILE_WEB && name !== DSH_PROFILE_HEADLESS) {
    throw new TypeError(`unknown DSH profile: ${String(name)}`);
  }
  return Object.freeze({
    name,
    plugin: DSH_MCP_CLIENT_PLUGIN,
    bridgeOperations: Object.freeze([...BRIDGE_OPERATIONS]),
    localOnly: true,
  });
}

// Research skills and the single Supervisor live at the package root
// (`skills/`) so the contract is readable outside `src/`.
export * from "../skills/index.js";
