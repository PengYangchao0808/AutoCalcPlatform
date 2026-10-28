/*
 * ACP Workbench — read-only research evidence view (M1-06 / M3-05a).
 *
 * Owner: frontend/js/workbench/features/** (OpenCode). Consumer of the single
 * platform projection `acp2.runtime.evidence-projection.v1` read through
 * ACPClient.research.fetchEvidence. This module owns **no transport**: there is
 * no fetch, no URL and no submit/decide method. It renders what the platform
 * projected and never derives a claim status.
 *
 * Display rules (frozen by the evidence contract):
 *   - execution status and claim status are two separate rows and never merge
 *     into one verdict;
 *   - only `complete` + `supported` + no blocking missing check renders as
 *     "supported"; every other combination renders as "needs review";
 *   - a fifth claim status is refused by the contract validator before it
 *     reaches this renderer;
 *   - a blocking missing check is always visible.
 *
 * Version: v20261027.1
 */
window.ACPResearchEvidence = (function () {
  "use strict";

  var ROOT_ID = "research-evidence-body";
  var rows = [];
  var mounted = null;

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  /** Pure projection: the exact rows the view shows (no DOM, no network). */
  function project(view) {
    if (!isObject(view)) {
      return { ok: false, problem: "evidence view is not an object", rows: [] };
    }
    if (!isObject(view.research) || !isObject(view.task_ref) || !isObject(view.report)) {
      return { ok: false, problem: "evidence view is missing research/task_ref/report", rows: [] };
    }
    var blocking = Array.isArray(view.blocking_missing_checks)
      ? view.blocking_missing_checks.slice()
      : [];
    var supported = view.evidence_status === "complete" &&
      view.overall_claim_status === "supported" && blocking.length === 0;
    var out = [
      { label: "research", value: String(view.research.research_id || "") },
      { label: "round", value: String(view.research.round) },
      { label: "task", value: String(view.task_ref.task_id || "") + "#" + String(view.task_ref.attempt) },
      { label: "release", value: String((view.task_ref.release || {}).release_id || "") },
      { label: "execution", value: String(view.execution_terminal_state || "") },
      { label: "evidence", value: String(view.evidence_status || "") },
      { label: "claims", value: String(view.overall_claim_status || "") },
      { label: "report", value: String(view.report.report_id || "") },
      { label: "projection", value: String(view.projection_digest || "") },
      { label: "verdict", value: supported ? "supported" : "needs_review" },
    ];
    var claims = Array.isArray(view.claims) ? view.claims : [];
    claims.forEach(function (claim, index) {
      out.push({
        label: "claim[" + index + "]",
        value: String(claim.claim_id || "") + ": " + String(claim.status || ""),
      });
    });
    blocking.forEach(function (check, index) {
      out.push({ label: "blocking[" + index + "]", value: String(check) });
    });
    return { ok: true, problem: null, rows: out, supported: supported };
  }

  function cell(doc, tag, text, className) {
    var element = doc.createElement(tag);
    element.textContent = String(text); // textContent only: no HTML injection
    if (className) element.className = className;
    return element;
  }

  /** Mount the view; `options.view` is the last platform projection read. */
  function mount(doc, options) {
    options = options || {};
    var root = doc.getElementById(ROOT_ID);
    if (!root) {
      throw new Error("research-evidence-view: #" + ROOT_ID + " is missing");
    }
    mounted = root;
    root.textContent = "";
    var projected = project(options.view || null);
    rows = projected.rows;
    if (!projected.ok) {
      root.appendChild(cell(doc, "div", projected.problem, "research-evidence-empty"));
      return { ok: false, rows: rows };
    }
    projected.rows.forEach(function (row) {
      var line = doc.createElement("div");
      line.className = "research-evidence-row";
      line.appendChild(cell(doc, "span", row.label, "research-evidence-label"));
      line.appendChild(cell(doc, "span", row.value, "research-evidence-value"));
      root.appendChild(line);
    });
    return { ok: true, rows: rows };
  }

  function dispose() {
    if (mounted) {
      mounted.textContent = "";
    }
    mounted = null;
    rows = [];
  }

  return {
    readOnly: true,
    ROOT_ID: ROOT_ID,
    project: project,
    mount: mount,
    dispose: dispose,
    rows: function () { return rows.slice(); },
  };
})();
