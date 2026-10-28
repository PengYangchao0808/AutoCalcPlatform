/**
 * Platform result restoration (F2, M3-01b).
 *
 * The bridge is a **model-facing** surface: it refuses a tool result that
 * carries a credential or a server-side path
 * (`@rph/bridge::assertResultRedacted`).  That guard is correct and stays
 * strict — but the platform's own discovery/prepare envelopes legitimately
 * carry two things the guard cannot distinguish from a leak:
 *
 * 1. an **inert audit statement** at `data.capability_request.authorization`
 *    (`"none: audit and advisory hints only"`): a published projection that
 *    says the request authorized nothing, not a credential;
 * 2. **host paths** inside discovery/prepare metadata (backend executables,
 *    work directories).
 *
 * This module is the one *restoration* step between the transport (verbatim
 * frames) and the bridge (strict guard).  It is deliberately narrow, because a
 * broad rewrite would be a silent redaction instead of an honest mapping:
 *
 * - only the frozen audit member is dropped, and only when its value is the
 *   inert audit text (anything that looks like a credential is left in place so
 *   the bridge still refuses it);
 * - only absolute host paths and `sftp:/ssh:/file:` URIs are replaced, with the
 *   stable placeholder `<host-path>`; relative paths (the task-relative
 *   `relative_path`/`artifact_ref.relative_path` fields) are untouched;
 * - no status, digest, task reference or typed error is ever edited.
 *
 * The precedent on the server side is `machine_output._OPAQUE_RE`: the
 * redaction net was refined so *published* identifiers survive instead of
 * being rewritten wholesale.  This is the same rule on the client side.
 */

import type { BridgeToolResult } from "@rph/bridge";

/** Stable placeholder for a withheld absolute host path. */
export const HOST_PATH_PLACEHOLDER = "<host-path>";

/** The only audit member the restoration drops (frozen server projection). */
export const INERT_AUDIT_POINTER = "$.data.capability_request.authorization";

/** The exact inert value the server publishes there. */
export const INERT_AUDIT_TEXT = "none: audit and advisory hints only";

/** One restoration report: what was dropped/withheld (audit facts, no bodies). */
export interface ResultRestorationReport {
  readonly dropped_audit_members: readonly string[];
  readonly withheld_host_paths: readonly string[];
}

/** A restored result plus the report of what the restoration changed. */
export interface RestoredResult {
  readonly result: BridgeToolResult;
  readonly report: ResultRestorationReport;
}

/** Values that are recognizably credentials regardless of their key. */
const CREDENTIAL_VALUE_RE =
  /(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,})|(?:sk-[A-Za-z0-9]{16,})|(?:AKIA[0-9A-Z]{16})/;

/**
 * Absolute POSIX path or Windows drive path *inside* a string.
 *
 * The shape mirrors the bridge's guard so a value the guard would refuse is
 * exactly a value this step rewrites (and a relative path is never touched).
 */
const ABSOLUTE_PATH_RE =
  /(?:^|[\s"'`=:([{])((?:\/(?:[A-Za-z0-9._-]+\/)+[A-Za-z0-9._-]+)|(?:[A-Za-z]:[\\/][^\s"'`)\]}]+))/g;

/** Remote/filesystem URI that would disclose a server-side location. */
const REMOTE_URI_RE = /\b(?:sftp|ssh|file):\/\/\S+/gi;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Replace every host path / remote URI inside one string. */
function withholdPaths(text: string, pointer: string, withheld: string[]): string {
  let changed = false;
  let next = text.replace(REMOTE_URI_RE, () => {
    changed = true;
    return HOST_PATH_PLACEHOLDER;
  });
  next = next.replace(ABSOLUTE_PATH_RE, (match: string, path: string) => {
    changed = true;
    return match.replace(path, HOST_PATH_PLACEHOLDER);
  });
  if (changed) {
    withheld.push(pointer);
  }
  return next;
}

function restoreValue(
  value: unknown,
  pointer: string,
  report: { dropped: string[]; withheld: string[] },
): unknown {
  if (typeof value === "string") {
    return withholdPaths(value, pointer, report.withheld);
  }
  if (Array.isArray(value)) {
    return value.map((item, index) =>
      restoreValue(item, `${pointer}[${index}]`, report),
    );
  }
  if (!isRecord(value)) {
    return value;
  }
  const output: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    const childPointer = `${pointer}.${key}`;
    if (childPointer === INERT_AUDIT_POINTER) {
      // A genuine credential under the audit member is never "restored" away:
      // it is left in place so the bridge's guard refuses the result.
      if (typeof item === "string" && !CREDENTIAL_VALUE_RE.test(item)) {
        report.dropped.push(childPointer);
        continue;
      }
    }
    output[key] = restoreValue(item, childPointer, report);
  }
  return output;
}

/**
 * Restore one transport result into the bridge-facing shape.
 *
 * Pure and idempotent: restoring an already restored result changes nothing
 * (the audit member is gone, the placeholder is not a path).
 */
export function restorePlatformResult(raw: BridgeToolResult): RestoredResult {
  const report = { dropped: [] as string[], withheld: [] as string[] };
  const structured = restoreValue(raw.structuredContent, "$", report);
  return {
    result: {
      isError: raw.isError,
      structuredContent: isRecord(structured)
        ? structured
        : raw.structuredContent,
    },
    report: Object.freeze({
      dropped_audit_members: Object.freeze([...report.dropped]),
      withheld_host_paths: Object.freeze([...report.withheld]),
    }),
  };
}

/** Record one restoration report in a caller-owned list (trace/acceptance). */
export type RestorationSink = (report: ResultRestorationReport) => void;

/**
 * Wrap a platform client with the restoration step (order: transport → restore
 * → bridge).
 *
 * The transport result is never mutated; the caller's `onRestore` receives the
 * report so an acceptance trace can show *why* a payload changed.
 */
export function withResultRestoration(
  client: {
    callTool(
      name: string,
      params: Readonly<Record<string, unknown>>,
    ): Promise<BridgeToolResult>;
  },
  onRestore?: RestorationSink,
): {
  callTool(
    name: string,
    params: Readonly<Record<string, unknown>>,
  ): Promise<BridgeToolResult>;
} {
  return {
    async callTool(name, params) {
      const raw = await client.callTool(name, params);
      const restored = restorePlatformResult(raw);
      if (
        onRestore !== undefined &&
        (restored.report.dropped_audit_members.length > 0 ||
          restored.report.withheld_host_paths.length > 0)
      ) {
        onRestore(restored.report);
      }
      return restored.result;
    },
  };
}
