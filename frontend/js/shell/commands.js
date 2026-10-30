/*
 * ACP Next Shell — navigation commands (UI-01).
 * Authority: UI-00 module recipe contract (embedded) + Shell Design §3.3.
 *
 * Commands are READ-ONLY navigation: they never submit, never mutate a task,
 * never call job-commands. Each command:
 *   1. builds a payload carrying only verifiable refs + a `source` tag;
 *   2. resolves the target panel — a mounted panel whose module descriptor
 *      `provides` the capability OR `binds` the referenced context field;
 *   3. switches workspace when the target lives in another one;
 *   4. calls the instance method when present (viewer.loadStructure /
 *      explorer.openTask / …), else falls back to context.setFocus so every
 *      follow-focus panel reacts.
 *
 * API (all return a structured result, never throw):
 *   openStructure(ref) / openTask(ref) / openEvidence(ref) / openProposal(ref)
 *   → { ok, targetInstanceId|null, workspace, via: "instance"|"context" }
 */
window.ACPShell = window.ACPShell || {};
ACPShell.commands = (function () {
  "use strict";

  // Injected by bootstrap; both may be absent in unit tests.
  var context = null;
  var layout = null;

  function init(deps) {
    deps = deps || {};
    if (deps.context) context = deps.context;
    if (deps.layout) layout = deps.layout;
  }

  function result(ok, extra) {
    return Object.assign({ ok: !!ok }, extra || {});
  }

  /**
   * Pick the target panel for a ref: prefer a panel that binds the context
   * field, then one that provides the capability tag. Only panels of the
   * CURRENT recipe are considered (design §3.3: focus flows within what the
   * user can see; dormant panels get the context update on resume).
   */
  function resolvePanel(bindsField, providesTag) {
    if (!layout || typeof layout.findPanelFor !== "function") return null;
    return layout.findPanelFor(bindsField, providesTag) || null;
  }

  function navigate(target, workspaceId) {
    if (!layout) return;
    if (workspaceId && typeof layout.switchWorkspace === "function") {
      layout.switchWorkspace(workspaceId, { focusTarget: target ? target.instanceId : null });
    } else if (target && typeof layout.focusPanel === "function") {
      layout.focusPanel(target.instanceId);
    }
  }

  function deliver(target, method, ref, patch, source) {
    // Context write is ALWAYS performed so the focus chip + follow-focus
    // panels stay authoritative even when no panel is mounted yet.
    var applied = null;
    if (context && typeof context.setFocus === "function") {
      applied = context.setFocus(patch, source);
    }
    if (target && target.instance && typeof target.instance[method] === "function") {
      try {
        target.instance[method](ref);
      } catch (error) {
        if (typeof console !== "undefined" && console.error) {
          console.error("Command delivery to " + target.instanceId + " failed", error);
        }
        return result(true, { targetInstanceId: target.instanceId, workspace: target.workspaceId, via: "context", contextRevision: applied ? applied.revision : null });
      }
      return result(true, { targetInstanceId: target.instanceId, workspace: target.workspaceId, via: "instance", contextRevision: applied ? applied.revision : null });
    }
    return result(true, { targetInstanceId: target ? target.instanceId : null, workspace: target ? target.workspaceId : null, via: "context", contextRevision: applied ? applied.revision : null });
  }

  /** ref: { source, structureId|relativePath, jobId?, attempt?, projectId? } */
  function openStructure(ref) {
    if (!ref || typeof ref !== "object") return result(false, { error: "ref must be an object" });
    var payload = {
      source: typeof ref.source === "string" && ref.source ? ref.source : "shell.commands.openStructure",
      structureRef: ref
    };
    var target = resolvePanel("structureRef", "structure.render");
    navigate(target, target ? target.workspaceId : null);
    return deliver(target, "loadStructure", ref, { structureRef: ref }, payload.source);
  }

  /** ref: { source, jobId, attempt?, taskId? } */
  function openTask(ref) {
    if (!ref || typeof ref !== "object" || typeof ref.jobId !== "string" || !ref.jobId) {
      return result(false, { error: "task ref requires jobId" });
    }
    var taskRef = { jobId: ref.jobId, attempt: typeof ref.attempt === "number" ? ref.attempt : null };
    if (ref.taskId) taskRef.taskId = ref.taskId;
    var source = typeof ref.source === "string" && ref.source ? ref.source : "shell.commands.openTask";
    var target = resolvePanel("taskRef", "tasks.list");
    navigate(target, target ? target.workspaceId : null);
    return deliver(target, "openTask", taskRef, { taskRef: taskRef }, source);
  }

  /** ref: { source, jobId?, attempt?, nodeId? } — network browsing only. */
  function openNetwork(ref) {
    if (!ref || typeof ref !== "object") return result(false, { error: "ref must be an object" });
    var source = typeof ref.source === "string" && ref.source ? ref.source : "shell.commands.openNetwork";
    var patch = {};
    if (ref.jobId) patch.taskRef = { jobId: ref.jobId, attempt: typeof ref.attempt === "number" ? ref.attempt : null };
    if (ref.nodeId) patch.networkNodeId = String(ref.nodeId);
    var target = resolvePanel("networkNodeId", "network.read");
    navigate(target, target ? target.workspaceId : null);
    return deliver(target, "openNetwork", ref, patch, source);
  }

  /** ref: { source, jobId, attempt?, artifactId? } — evidence browsing only. */
  function openEvidence(ref) {
    if (!ref || typeof ref !== "object") return result(false, { error: "ref must be an object" });
    var source = typeof ref.source === "string" && ref.source ? ref.source : "shell.commands.openEvidence";
    var patch = {};
    if (ref.jobId) patch.taskRef = { jobId: ref.jobId, attempt: typeof ref.attempt === "number" ? ref.attempt : null };
    var target = resolvePanel("taskRef", "research.evidence");
    navigate(target, target ? target.workspaceId : null);
    return deliver(target, "openEvidence", ref, patch, source);
  }

  /** ref: { source, proposalId, revision? } — proposal/evidence browsing only. */
  function openProposal(ref) {
    if (!ref || typeof ref !== "object") return result(false, { error: "ref must be an object" });
    var source = typeof ref.source === "string" && ref.source ? ref.source : "shell.commands.openProposal";
    var patch = {};
    if (ref.proposalId) {
      patch.researchRef = { proposalId: ref.proposalId, revision: typeof ref.revision === "number" ? ref.revision : null };
    }
    var target = resolvePanel("researchRef", "research.summary");
    navigate(target, target ? target.workspaceId : null);
    return deliver(target, "openProposal", ref, patch, source);
  }

  return {
    init: init,
    openStructure: openStructure,
    openTask: openTask,
    openNetwork: openNetwork,
    openEvidence: openEvidence,
    openProposal: openProposal
  };
})();
