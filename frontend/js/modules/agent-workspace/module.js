/*
 * ACP Next — agent-workspace module (UI-03A read-only first slice).
 *
 * Owner: frontend/js/modules/agent-workspace/** (OpenCode). Classic script,
 * no build, no ESM. Registers into window.ACPNextModules under the id
 * "agent-workspace".
 *
 * Read-only by construction: this slice renders three cards (research
 * summary / plan+steps timeline / evidence) from the ACPClient read
 * projections only. There is NO submit, approve, decide, edit or any other
 * write surface here — approval actions belong to a later phase behind one
 * unified entry. All navigation buttons only route (openTask /
 * openStructure / openEvidence via the injected commands).
 *
 * Data honesty rules (design spec §4.5/§4.6, plan §4.6):
 *   * "model explanation" and "verified fact" are visually and structurally
 *     separated — model text ONLY ever renders inside .acp-model-note
 *     (dashed neutral container); validation conclusions render inside
 *     .acp-verified-fact / .acp-tag--pass|fail|warn with the shield icon,
 *     and only from service-authoritative data.
 *   * a ViewModel that fails validateViewModel renders as an explicit
 *     "数据不合规" error card — never a half-rendered card.
 *   * a `not_evaluated` claim never renders as pass/green.
 *   * missing data renders as `—` + acp-tag--missing; nothing is inferred
 *     or invented.
 *   * the tool-call timeline is an explicit disabled placeholder (DSH not
 *     integrated yet) — no fake data.
 *
 * Fixture seam: when window.__ACP_NEXT_AGENT_FIXTURE__ is set (tests and
 * demos ONLY — never in production wiring) the module reads
 * record/proposal/taskStatus/view from it instead of the network, and marks
 * itself with data-acp-fixture="true" plus a visible demo badge so fixture
 * data can never be mistaken for live data.
 * Version: v20261030.1
 */
(function () {
  "use strict";

  var FIXTURE_GLOBAL = "__ACP_NEXT_AGENT_FIXTURE__";

  /* Frozen enums mirrored from experience_contract.js for display mapping
   * only — the client/contract validators remain the acceptance gate. */
  var PROPOSAL_STATE = {
    proposed: { state: "is-candidate", tag: "acp-tag--candidate", key: "agent.plan.state.candidate", tone: null },
    deferred: { state: "is-pending", tag: "acp-tag--pending", key: "agent.plan.state.pending", tone: null },
    approved: { state: "is-submitted", tag: "acp-tag--submitted", key: "agent.plan.state.submitted", tone: null },
    rejected: { state: "is-result", tag: "acp-tag--result", key: "agent.plan.state.result", tone: "fail" },
    superseded: { state: "is-result", tag: "acp-tag--result", key: "agent.plan.state.result", tone: "warn" },
  };
  /* A `proposed` proposal that already carries an approval reference is
   * waiting on the service-side approval, not a bare candidate. */
  var RECORD_STATE = {
    accept: { state: "is-result", tag: "acp-tag--result", key: "agent.plan.state.result", tone: "pass" },
    revise: { state: "is-result", tag: "acp-tag--result", key: "agent.plan.state.result", tone: "warn" },
    reject: { state: "is-result", tag: "acp-tag--result", key: "agent.plan.state.result", tone: "fail" },
    defer: { state: "is-pending", tag: "acp-tag--pending", key: "agent.plan.state.pending", tone: null },
    escalate: { state: "is-pending", tag: "acp-tag--pending", key: "agent.plan.state.pending", tone: null },
  };
  /* Job lifecycle statuses (server-authoritative strings). The UI maps a
   * display state only; it never rewrites or upgrades the status itself. */
  var TASK_STATE = {
    COMPLETED: { state: "is-result", tag: "acp-tag--result", tone: "pass" },
    FAILED: { state: "is-result", tag: "acp-tag--result", tone: "fail" },
    CANCELLED: { state: "is-result", tag: "acp-tag--result", tone: "warn" },
    WAITING_REVIEW: { state: "is-pending", tag: "acp-tag--pending", tone: null },
    QUEUED: { state: "is-submitted", tag: "acp-tag--submitted", tone: null },
    RUNNING: { state: "is-submitted", tag: "acp-tag--submitted", tone: null },
    PAUSED: { state: "is-submitted", tag: "acp-tag--submitted", tone: null },
  };
  var CLAIM_TAG = {
    supported: { cls: "acp-verified-fact is-pass", tag: "acp-tag--pass", key: "agent.verify.pass", evaluated: true },
    refuted: { cls: "acp-verified-fact is-fail", tag: "acp-tag--fail", key: "agent.verify.fail", evaluated: true },
    inconclusive: { cls: "acp-verified-fact is-warn", tag: "acp-tag--warn", key: "agent.verify.warn", evaluated: true },
    /* not_evaluated is NOT a verified fact: it renders as a plain missing
     * row, never inside a verified-fact container and never green. */
    not_evaluated: { cls: "acp-claim-row is-not-evaluated", tag: "acp-tag--missing", key: "agent.verify.not_evaluated", evaluated: false },
  };
  var STRUCTURE_EXT = /\.(xyz|sdf|mol2?|pdb|cif|genpos?)$/i;

  function esc(value) {
    return String(value == null ? "" : value)
      .split("&").join("&amp;").split("<").join("&lt;")
      .split(">").join("&gt;").split('"').join("&quot;");
  }

  function messageOf(error) {
    if (!error) return "unknown error";
    return error.message ? String(error.message) : String(error);
  }

  function refKey(ref) {
    if (!ref) return "";
    if (ref.recordId) return "record:" + ref.recordId;
    if (ref.proposalId) return "proposal:" + ref.proposalId;
    return "";
  }

  function taskKey(ref) {
    if (!ref || ref.jobId == null) return "";
    return String(ref.jobId) + "#" + String(ref.attempt == null ? 1 : ref.attempt);
  }

  /* Local read-only projections, mirroring ACPClient.research.summarize*.
   * Used only when the injected client does not expose the summarizer. */
  function localSummarizeRecord(record) {
    record = record || {};
    return {
      research_id: record.research_id,
      round: record.round,
      decision: record.decision,
      approval_ref: record.approval_ref == null ? null : record.approval_ref,
      evidence_refs: (record.evidence_refs || []).map(function (item) {
        return { evidence_id: item.evidence_id, kind: item.kind, sha256: item.sha256 };
      }),
    };
  }

  function localSummarizeProposal(proposal) {
    proposal = proposal || {};
    return {
      proposal_id: proposal.proposal_id,
      round: proposal.round,
      decision: proposal.decision,
      approval_ref: proposal.approval_ref == null ? null : proposal.approval_ref,
      plan_definition_digest: proposal.plan_definition_digest,
    };
  }

  function factory(deps) {
    deps = deps || {};
    var context = deps.context || null;
    var commands = deps.commands || {};

    var host = null;
    var clickHandler = null;
    var unsubscribe = null;
    var generation = 0;
    var disposed = false;
    var snapshot = { projectId: null, permission: "read", researchRef: null, taskRef: null };

    var state = {
      summary: { status: "idle", kind: null, summary: null, body: null },
      planCandidate: { status: "idle", items: [] },
      planExecution: { status: "idle", taskStatus: null },
      evidence: { status: "idle", view: null, errorKind: null },
    };

    // ------------------------------------------------------------------
    // environment accessors (all late-bound: shell/client/fixture may load
    // after this module's script, and tests inject each independently)
    // ------------------------------------------------------------------

    function client() {
      if (deps.client) return deps.client;
      return (typeof window !== "undefined" && window.ACPClient) || null;
    }

    function contracts() {
      return (typeof window !== "undefined" && window.ACPWorkbench &&
        window.ACPWorkbench.contracts && window.ACPWorkbench.contracts.experience) || null;
    }

    function fixture() {
      return (typeof window !== "undefined" && window[FIXTURE_GLOBAL]) || null;
    }

    function shellT() {
      return (typeof window !== "undefined" && window.ACPShell &&
        window.ACPShell.i18n && typeof window.ACPShell.i18n.t === "function")
        ? window.ACPShell.i18n.t.bind(window.ACPShell.i18n) : null;
    }

    function scope() {
      return {
        projectId: snapshot.projectId,
        permission: snapshot.permission || "read",
      };
    }

    function current(gen) {
      return !disposed && gen === generation;
    }

    function renderIf(gen) {
      if (current(gen)) render();
    }

    // ------------------------------------------------------------------
    // loaders (read-only; every one is generation-guarded)
    // ------------------------------------------------------------------

    function loadResearch(gen) {
      var ref = snapshot.researchRef;
      if (!refKey(ref)) {
        state.summary = { status: "empty", kind: null, summary: null, body: null };
        state.planCandidate = { status: "empty", items: [] };
        renderIf(gen);
        return Promise.resolve();
      }
      if (!snapshot.projectId) {
        state.summary = { status: "error", kind: null, summary: null, body: null, error: "scope_missing" };
        state.planCandidate = { status: "error", items: [], error: "scope_missing" };
        renderIf(gen);
        return Promise.resolve();
      }
      state.summary = { status: "loading", kind: null, summary: null, body: null };
      state.planCandidate = { status: "loading", items: [] };
      renderIf(gen);

      var isRecord = !!ref.recordId;
      var fx = fixture();
      var request;
      if (fx) {
        request = Promise.resolve(isRecord ? fx.record : fx.proposal);
      } else {
        var c = client();
        if (!c || !c.research ||
            (isRecord && typeof c.research.fetchRecord !== "function") ||
            (!isRecord && typeof c.research.fetchProposal !== "function")) {
          state.summary = { status: "error", kind: null, summary: null, body: null, error: "no_client" };
          state.planCandidate = { status: "error", items: [], error: "no_client" };
          renderIf(gen);
          return Promise.resolve();
        }
        request = isRecord
          ? c.research.fetchRecord(ref.recordId, scope())
          : c.research.fetchProposal(ref.proposalId, scope());
      }
      return request.then(function (body) {
        if (!current(gen)) return;
        if (!body) {
          state.summary = { status: "empty", kind: null, summary: null, body: null };
          state.planCandidate = { status: "empty", items: [] };
          renderIf(gen);
          return;
        }
        var c = client();
        var sum;
        if (isRecord) {
          sum = (c && c.research && typeof c.research.summarizeRecord === "function")
            ? c.research.summarizeRecord(body) : localSummarizeRecord(body);
          state.summary = { status: "ready", kind: "record", summary: sum, body: body };
          state.planCandidate = { status: "ready", items: candidateItems(null, body) };
        } else {
          sum = (c && c.research && typeof c.research.summarizeProposal === "function")
            ? c.research.summarizeProposal(body) : localSummarizeProposal(body);
          state.summary = { status: "ready", kind: "proposal", summary: sum, body: body };
          state.planCandidate = { status: "ready", items: candidateItems(body, null) };
        }
        renderIf(gen);
      }, function (error) {
        if (!current(gen)) return;
        state.summary = { status: "error", kind: null, summary: null, body: null, error: messageOf(error) };
        state.planCandidate = { status: "error", items: [], error: messageOf(error) };
        renderIf(gen);
      });
    }

    function loadTask(gen) {
      var ref = snapshot.taskRef;
      if (!ref || ref.jobId == null) {
        state.planExecution = { status: "empty", taskStatus: null };
        renderIf(gen);
        return Promise.resolve();
      }
      state.planExecution = { status: "loading", taskStatus: state.planExecution.taskStatus || null };
      renderIf(gen);
      var fx = fixture();
      var request;
      if (fx) {
        request = Promise.resolve(fx.taskStatus || null);
      } else {
        if (!scope().projectId) {
          // The science read contract requires a project scope. With "all
          // projects" selected there is nothing to read, and calling the
          // adapter would throw out of updateContext.
          state.planExecution = { status: "empty", taskStatus: null };
          renderIf(gen);
          return Promise.resolve();
        }
        var c = client();
        if (!c || !c.science || typeof c.science.fetchTaskStatus !== "function") {
          state.planExecution = { status: "error", taskStatus: null, error: "no_client" };
          renderIf(gen);
          return Promise.resolve();
        }
        request = c.science.fetchTaskStatus(
          { jobId: ref.jobId, attempt: ref.attempt == null ? 1 : ref.attempt }, scope());
      }
      return request.then(function (status) {
        if (!current(gen)) return;
        state.planExecution = status
          ? { status: "ready", taskStatus: status }
          : { status: "empty", taskStatus: null };
        renderIf(gen);
      }, function (error) {
        if (!current(gen)) return;
        state.planExecution = { status: "error", taskStatus: null, error: messageOf(error) };
        renderIf(gen);
      });
    }

    function loadEvidence(gen) {
      var ref = snapshot.taskRef;
      if (!ref || ref.jobId == null) {
        state.evidence = { status: "empty", view: null, errorKind: null };
        renderIf(gen);
        return Promise.resolve();
      }
      state.evidence = { status: "loading", view: null, errorKind: null };
      renderIf(gen);
      var fx = fixture();
      var request;
      if (fx) {
        request = Promise.resolve(fx.view || null);
      } else {
        if (!scope().projectId) {
          state.evidence = { status: "empty", view: null, errorKind: null };
          renderIf(gen);
          return Promise.resolve();
        }
        var c = client();
        if (!c || !c.science || typeof c.science.fetchView !== "function") {
          state.evidence = { status: "error", view: null, errorKind: "no_client" };
          renderIf(gen);
          return Promise.resolve();
        }
        request = c.science.fetchView(
          { jobId: ref.jobId, attempt: ref.attempt == null ? 1 : ref.attempt }, scope());
      }
      return request.then(function (view) {
        if (!current(gen)) return;
        if (!view) {
          state.evidence = { status: "empty", view: null, errorKind: null };
          renderIf(gen);
          return;
        }
        /* Contract gate: a body that fails validateViewModel renders as an
         * explicit error card, never a half-rendered card. Fail closed if
         * the validator itself is unavailable. */
        var contractsApi = contracts();
        var check = contractsApi && typeof contractsApi.validateViewModel === "function"
          ? contractsApi.validateViewModel(view) : { ok: false, problems: ["validator unavailable"] };
        if (!check.ok) {
          state.evidence = { status: "error", view: null, errorKind: "invalid_data" };
        } else {
          state.evidence = { status: "ready", view: view, errorKind: null };
        }
        renderIf(gen);
      }, function (error) {
        if (!current(gen)) return;
        state.evidence = { status: "error", view: null, errorKind: messageOf(error) };
        renderIf(gen);
      });
    }

    function loadAll(gen) {
      return Promise.all([loadResearch(gen), loadTask(gen), loadEvidence(gen)]);
    }

    // ------------------------------------------------------------------
    // view-model shaping (pure; no DOM)
    // ------------------------------------------------------------------

    function mapProposalDecision(proposal) {
      var mapped = PROPOSAL_STATE[proposal.decision] || null;
      if (!mapped) {
        return { state: "is-candidate", tag: "acp-tag--candidate", key: "agent.plan.state.candidate", tone: null };
      }
      if (proposal.decision === "proposed" && proposal.approval_ref) {
        return { state: "is-pending", tag: "acp-tag--pending", key: "agent.plan.state.pending", tone: null };
      }
      return mapped;
    }

    function candidateItems(proposal, record) {
      var items = [];
      if (proposal) {
        var mapped = mapProposalDecision(proposal);
        var steps = (proposal.science && proposal.science.capability_steps) || [];
        var meta = String(proposal.proposal_id || "") +
          (proposal.round != null ? " · round " + proposal.round : "");
        if (steps.length) {
          steps.forEach(function (step) {
            var detail = step.capability || step.tool || "step";
            if (step.parameters && step.parameters.optimizer) detail += " · " + step.parameters.optimizer;
            items.push({
              title: detail, meta: meta + " · step " + step.step_index,
              state: mapped.state, tag: mapped.tag, key: mapped.key, tone: mapped.tone,
            });
          });
        } else {
          items.push({
            title: String(proposal.proposal_id || "—"), meta: meta,
            state: mapped.state, tag: mapped.tag, key: mapped.key, tone: mapped.tone,
          });
        }
      }
      if (record) {
        var recMapped = RECORD_STATE[record.decision] ||
          { state: "is-pending", tag: "acp-tag--pending", key: "agent.plan.state.pending", tone: null };
        items.push({
          title: String(record.research_id || "—"),
          meta: "round " + (record.round == null ? "—" : record.round) +
            " · record r" + (record.record_revision == null ? "—" : record.record_revision),
          state: recMapped.state, tag: recMapped.tag, key: recMapped.key, tone: recMapped.tone,
        });
      }
      return items;
    }

    function executionItem(status) {
      var raw = status && status.status ? String(status.status) : null;
      var mapped = (raw && TASK_STATE[raw]) || { state: "is-submitted", tag: "acp-tag--submitted", tone: null };
      var ref = status && status.task_ref ? status.task_ref : {};
      return {
        title: raw || "—",
        meta: String(ref.job_id || "—") + " · attempt " + (ref.attempt == null ? "—" : ref.attempt),
        state: mapped.state, tag: mapped.tag, key: null, tone: mapped.tone, raw: true,
      };
    }

    function structureArtifactOf(view) {
      var artifacts = (view && view.artifacts) || [];
      for (var i = 0; i < artifacts.length; i += 1) {
        var item = artifacts[i];
        if (item.availability === "readable" && item.relative_path &&
            STRUCTURE_EXT.test(item.relative_path)) return item;
      }
      return null;
    }

    function overallClaimVerdict(view) {
      var claims = (view && view.claims) || [];
      if (!claims.length) return null;
      var hasRefuted = false, hasWarn = false, hasMissing = false;
      for (var i = 0; i < claims.length; i += 1) {
        var status = claims[i].status;
        if (status === "refuted") hasRefuted = true;
        else if (status === "inconclusive") hasWarn = true;
        else if (status === "not_evaluated") hasMissing = true;
      }
      if (hasRefuted) return { tag: "acp-tag--fail", key: "agent.verify.fail" };
      if (hasWarn || hasMissing) return { tag: "acp-tag--warn", key: "agent.verify.warn" };
      return { tag: "acp-tag--pass", key: "agent.verify.pass" };
    }

    // ------------------------------------------------------------------
    // rendering (innerHTML strings — testable in the zero-DOM harness; the
    // shell panel chrome wraps this root)
    // ------------------------------------------------------------------

    function tagHtml(tagClass, key, text, extraAttrs) {
      return '<span class="acp-tag ' + tagClass + '"' + (extraAttrs || "") + ">" +
        (key ? '<span data-i18n="' + key + '">' + esc(text) + "</span>" : esc(text)) + "</span>";
    }

    function shieldTagHtml(tagClass, key, text) {
      return '<span class="acp-tag ' + tagClass + '">' +
        '<svg width="12" height="12" aria-hidden="true"><use href="#acp-i-shield"/></svg>' +
        '<span data-i18n="' + key + '">' + esc(text) + "</span></span>";
    }

    function skeletonHtml() {
      return '<div class="acp-card-skel" aria-hidden="true">' +
        '<span></span><span></span><span></span></div>';
    }

    function cardErrorHtml(retryTarget, message) {
      return '<div class="acp-state acp-state-error" role="alert">' +
        '<div class="acp-state-copy">' +
        '<p class="acp-state-title" data-i18n="agent.error.load_failed">加载失败</p>' +
        '<p class="acp-state-msg mono">' + esc(message) + "</p></div>" +
        '<div class="acp-state-actions">' +
        '<button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="retry" data-retry="' +
        retryTarget + '" data-i18n="agent.action.retry">重试</button></div></div>';
    }

    function errorText(errorKind) {
      if (errorKind === "no_client") return "agent.error.no_client: read-only client unavailable";
      if (errorKind === "scope_missing") return "agent.error.scope_missing: projectId missing in context";
      if (errorKind === "invalid_data") return "agent.error.invalid_data: view-model failed contract validation";
      return String(errorKind || "unknown error");
    }

    function timelineItemHtml(item, withOpenTask) {
      var toneAttr = item.tone ? ' data-tone="' + item.tone + '"' : "";
      var tagHtmlStr = item.raw
        ? tagHtml(item.tag, null, item.title)
        : tagHtml(item.tag, item.key, item.key === "agent.plan.state.candidate" ? "候选"
          : item.key === "agent.plan.state.pending" ? "待审批"
          : item.key === "agent.plan.state.submitted" ? "已提交" : "结果");
      return '<li class="acp-timeline-item ' + item.state + '" data-state="' + item.state + '"' + toneAttr + ">" +
        '<span class="acp-timeline-node" aria-hidden="true"></span>' +
        '<div class="acp-timeline-content"><div class="acp-timeline-row">' +
        '<span class="acp-timeline-title">' + esc(item.title) + "</span>" + tagHtmlStr +
        "</div>" +
        '<p class="acp-timeline-meta mono">' + esc(item.meta) + "</p>" +
        (withOpenTask
          ? '<div class="acp-timeline-actions"><button class="acp-btn acp-btn--ghost acp-btn--sm" ' +
            'data-cmd="openTask" data-job-id="' + esc(snapshot.taskRef && snapshot.taskRef.jobId) +
            '" data-attempt="' + esc((snapshot.taskRef && snapshot.taskRef.attempt) || 1) + '" ' +
            'data-i18n="agent.action.open_task">打开任务</button></div>'
          : "") +
        "</div></li>";
    }

    function summaryCardHtml() {
      var s = state.summary;
      var body;
      if (s.status === "loading") body = skeletonHtml();
      else if (s.status === "empty") {
        body = '<div class="acp-state acp-state-empty" role="status">' +
          '<p class="acp-state-title" data-i18n="agent.empty.no_record">暂无研究记录</p>' +
          '<p class="acp-state-msg" data-i18n="agent.empty.no_record_hint">' +
          "在执行工作区选择任务后，此处显示关联研究</p></div>";
      } else if (s.status === "error") body = cardErrorHtml("summary", errorText(s.error));
      else {
        var sum = s.summary || {};
        var raw = s.body || {};
        var rows = "";
        // goal: proposal hypothesis only — a record carries no goal field
        var goal = s.kind === "proposal" ? raw.hypothesis : null;
        rows += rowHtml("agent.summary.goal", "研究目标",
          goal ? esc(goal) : '<span class="acp-missing">—</span>');
        // input structures: proposal science.initial_inputs only
        var inputs = s.kind === "proposal" && raw.science && Array.isArray(raw.science.initial_inputs)
          ? raw.science.initial_inputs : [];
        rows += rowHtml("agent.summary.input_structures", "输入结构",
          inputs.length
            ? inputs.map(function (item) {
              return '<span class="acp-tag acp-tag--neutral mono">' + esc(item.input_id || item.role || "—") + "</span>";
            }).join(" ")
            : '<span data-i18n="agent.summary.none">无</span>');
        // record version
        rows += rowHtml("agent.summary.record_version", "记录版本",
          s.kind === "record" && raw.record_revision != null
            ? '<span class="mono">record r' + esc(raw.record_revision) + "</span>"
            : '<span class="acp-missing">—</span>');
        // decision / approval_ref / plan digest
        rows += rowHtml("agent.summary.decision", "决策", esc(sum.decision || "—"));
        rows += rowHtml("agent.summary.approval_ref", "审批引用",
          sum.approval_ref
            ? '<span class="mono">' + esc(sum.approval_ref) + "</span>"
            : '<span class="acp-missing">—</span>');
        var digest = s.kind === "proposal" ? sum.plan_definition_digest : raw.plan_definition_digest;
        rows += rowHtml("agent.summary.plan_digest", "计划摘要",
          digest ? '<span class="mono">' + esc(digest) + "</span>" : '<span class="acp-missing">—</span>');
        // related tasks: record.task_refs (+ context.taskRef fallback)
        var taskRefs = (raw.task_refs || []).slice();
        if (!taskRefs.length && snapshot.taskRef && snapshot.taskRef.jobId) {
          taskRefs = [{ job_id: snapshot.taskRef.jobId, attempt: snapshot.taskRef.attempt || 1 }];
        }
        rows += rowHtml("agent.summary.related_tasks", "关联任务",
          taskRefs.length
            ? taskRefs.map(function (item) {
              var statusBadge = taskStatusBadgeFor(item);
              return '<span class="acp-tag acp-tag--neutral mono">' + esc(item.job_id) +
                " · attempt " + esc(item.attempt) + "</span>" + statusBadge;
            }).join(" ")
            : '<span data-i18n="agent.summary.none">无</span>');

        var meta = s.kind === "record" ? String(sum.research_id || "")
          : String(sum.proposal_id || "") + (sum.round != null ? " · round " + sum.round : "");
        var canOpenTask = taskRefs.length > 0;
        var structure = structureArtifactOf(state.evidence.view);
        body = '<dl class="acp-card-rows">' + rows + "</dl>" +
          '<footer class="acp-card-actions">' +
          (canOpenTask
            ? '<button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openTask" ' +
              'data-job-id="' + esc(taskRefs[0].job_id) + '" data-attempt="' + esc(taskRefs[0].attempt) + '" ' +
              'data-i18n="agent.action.open_task">打开任务</button>'
            : '<button class="acp-btn acp-btn--ghost acp-btn--sm" disabled ' +
              'title="无可用结构引用" data-i18n="agent.action.open_task">打开任务</button>') +
          (structure
            ? '<button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openStructure" ' +
              'data-artifact-id="' + esc(structure.artifact_id) + '" ' +
              'data-relative-path="' + esc(structure.relative_path) + '" ' +
              'data-i18n="agent.action.open_structure">打开结构</button>'
            : '<button class="acp-btn acp-btn--ghost acp-btn--sm" disabled ' +
              'title="无可用结构引用" data-i18n="agent.action.open_structure">打开结构</button>') +
          "</footer>" +
          modelNoteHtml();
        return cardShellHtml("acp-card-summary", "agent.summary.title", "研究摘要", esc(meta), body);
      }
      return cardShellHtml("acp-card-summary", "agent.summary.title", "研究摘要", "", body);
    }

    function rowHtml(key, zh, ddHtml) {
      return '<div class="acp-card-row"><dt data-i18n="' + key + '">' + zh + "</dt>" +
        "<dd>" + ddHtml + "</dd></div>";
    }

    function taskStatusBadgeFor(taskRefItem) {
      var status = state.planExecution.taskStatus;
      if (!status || !status.task_ref) return "";
      if (String(status.task_ref.job_id) !== String(taskRefItem.job_id) ||
          Number(status.task_ref.attempt) !== Number(taskRefItem.attempt)) return "";
      var mapped = TASK_STATE[status.status] || { tag: "acp-tag--neutral" };
      return " " + tagHtml(mapped.tag, null, String(status.status));
    }

    function modelNoteHtml() {
      var fx = fixture();
      var note = fx && fx.modelNote ? String(fx.modelNote) : null;
      if (!note) return "";
      return '<div class="acp-model-note" data-acp-source="model">' +
        '<span class="acp-tag acp-tag--model">' +
        '<svg width="12" height="12" aria-hidden="true"><use href="#acp-i-spark"/></svg>' +
        '<span data-i18n="agent.note.model">模型解释</span></span>' +
        '<p class="acp-model-note-text">' + esc(note) + "</p></div>";
    }

    function planCardHtml() {
      var body = "";
      body += '<p class="acp-agent-section" data-i18n="agent.plan.section.candidate">候选计划</p>';
      if (state.planCandidate.status === "loading") body += skeletonHtml();
      else if (state.planCandidate.status === "error") {
        body += cardErrorHtml("plan", errorText(state.planCandidate.error));
      } else if (!state.planCandidate.items.length) {
        body += '<div class="acp-state acp-state-empty" role="status">' +
          '<p class="acp-state-title" data-i18n="agent.plan.empty">暂无计划步骤</p></div>';
      } else {
        body += '<ol class="acp-timeline">' + state.planCandidate.items.map(function (item) {
          return timelineItemHtml(item, false);
        }).join("") + "</ol>";
      }

      body += '<p class="acp-agent-section" data-i18n="agent.plan.section.execution">实际执行</p>';
      if (state.planExecution.status === "loading") body += skeletonHtml();
      else if (state.planExecution.status === "error") {
        body += cardErrorHtml("plan", errorText(state.planExecution.error));
      } else if (state.planExecution.status === "ready" && state.planExecution.taskStatus) {
        body += '<ol class="acp-timeline">' +
          timelineItemHtml(executionItem(state.planExecution.taskStatus), true) + "</ol>";
      } else {
        body += '<div class="acp-state acp-state-empty" role="status">' +
          '<p class="acp-state-title" data-i18n="agent.plan.empty_task">未选择任务</p>' +
          '<p class="acp-state-msg" data-i18n="agent.empty.no_record_hint">' +
          "在执行工作区选择任务后，此处显示关联研究</p></div>";
      }

      // Tool-call timeline: explicit disabled placeholder, NO fake data.
      body += '<ol class="acp-timeline"><li class="acp-timeline-item is-disabled" ' +
        'data-acp-tools-placeholder="true">' +
        '<span class="acp-timeline-node" aria-hidden="true"></span>' +
        '<div class="acp-timeline-content"><div class="acp-timeline-row">' +
        '<span class="acp-timeline-title acp-timeline-title--muted" ' +
        'data-i18n="agent.plan.tools.pending">工具调用时间线将在 DSH 接入后提供</span>' +
        "</div></div></li></ol>";

      return cardShellHtml("acp-card-plan", "agent.plan.title", "计划与步骤", "", body);
    }

    function evidenceCardHtml() {
      var e = state.evidence;
      var body;
      var meta = "";
      if (e.status === "loading") body = skeletonHtml();
      else if (e.status === "empty") {
        body = '<div class="acp-state acp-state-empty" role="status">' +
          '<p class="acp-state-title" data-i18n="agent.plan.empty_task">未选择任务</p>' +
          '<p class="acp-state-msg" data-i18n="agent.empty.no_record_hint">' +
          "在执行工作区选择任务后，此处显示关联研究</p></div>";
      } else if (e.status === "error") body = cardErrorHtml("evidence", errorText(e.errorKind));
      else {
        var view = e.view || {};
        var ref = view.task_ref || {};
        meta = String(ref.job_id || "—") + " · attempt " + (ref.attempt == null ? "—" : ref.attempt);
        var prov = view.provenance || {};
        var rows = "";
        rows += rowHtml("agent.evidence.source", "来源",
          '<span class="mono">' + esc(ref.job_id || "—") + " / attempt " + esc(ref.attempt == null ? "—" : ref.attempt) +
          "</span>" + (prov.source ? ' <span class="mono">' + esc(prov.source) + "</span>" : ""));
        rows += rowHtml("agent.evidence.version", "版本",
          '<span class="mono">manifest g' + esc(view.manifest_generation == null ? "—" : view.manifest_generation) + "</span>" +
          (prov.digest ? ' <span class="mono">' + esc(prov.digest) + "</span>" : '<span class="acp-missing">—</span>'));
        var verdict = overallClaimVerdict(view);
        rows += rowHtml("agent.evidence.report", "验证报告",
          (verdict ? shieldTagHtml(verdict.tag, verdict.key,
            verdict.key === "agent.verify.pass" ? "验证通过"
              : verdict.key === "agent.verify.fail" ? "验证未通过" : "部分验证") : '<span class="acp-missing">—</span>') +
          ' <button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openEvidence" ' +
          'data-i18n="agent.evidence.open_report">查看报告</button>');
        // missing artifacts
        var missing = (view.artifacts || []).filter(function (item) {
          return item.availability !== "readable";
        });
        rows += rowHtml("agent.evidence.missing", "缺失项",
          missing.length
            ? missing.map(function (item) {
              return tagHtml("acp-tag--missing", null, String(item.role || item.artifact_id || "—")) +
                (item.reason ? ' <span class="acp-missing-reason mono" title="' + esc(item.reason) + '">' +
                  esc(item.reason) + "</span>" : "");
            }).join(" ")
            : '<span data-i18n="agent.summary.none">无</span>');
        body = '<dl class="acp-card-rows">' + rows + "</dl>";

        // claims: evaluated → verified-fact containers; not_evaluated →
        // plain missing row (never pass, never green).
        var claims = view.claims || [];
        if (claims.length) {
          body += '<p class="acp-agent-section" data-i18n="agent.evidence.claims">验证结论</p>';
          body += claims.map(function (claim) {
            var mapped = CLAIM_TAG[claim.status] || CLAIM_TAG.not_evaluated;
            var zh = claim.status === "supported" ? "验证通过"
              : claim.status === "refuted" ? "验证未通过"
              : claim.status === "inconclusive" ? "部分验证" : "尚未评估";
            return '<div class="' + mapped.cls + '" data-claim-status="' + esc(claim.status) + '" ' +
              'data-claim-id="' + esc(claim.claim_id) + '">' +
              shieldTagHtml(mapped.tag, mapped.key, zh) +
              '<p class="acp-claim-statement mono">' + esc(claim.statement) + "</p>" +
              (claim.reason ? '<p class="acp-claim-reason mono">' + esc(claim.reason) + "</p>" : "") +
              "</div>";
          }).join("");
        }

        var structure = structureArtifactOf(view);
        body += '<footer class="acp-card-actions">' +
          (structure
            ? '<button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openStructure" ' +
              'data-artifact-id="' + esc(structure.artifact_id) + '" ' +
              'data-relative-path="' + esc(structure.relative_path) + '" ' +
              'data-i18n="agent.action.open_structure">打开结构</button>'
            : '<button class="acp-btn acp-btn--ghost acp-btn--sm" disabled ' +
              'title="无可用结构引用" data-i18n="agent.action.open_structure">打开结构</button>') +
          '<button class="acp-btn acp-btn--ghost acp-btn--sm" data-cmd="openEvidence" ' +
          'data-i18n="agent.action.open_artifact">查看工件</button>' +
          "</footer>";
      }
      return cardShellHtml("acp-card-evidence", "agent.evidence.title", "证据", esc(meta), body);
    }

    function cardShellHtml(extraClass, key, zh, metaHtml, bodyHtml) {
      return '<article class="acp-card ' + extraClass + '">' +
        '<header class="acp-card-head"><h3 class="acp-card-title" data-i18n="' + key + '">' + zh + "</h3>" +
        (metaHtml ? '<span class="acp-card-meta mono">' + metaHtml + "</span>" : "") +
        "</header>" + bodyHtml + "</article>";
    }

    function overallState() {
      if (!refKey(snapshot.researchRef) && !(snapshot.taskRef && snapshot.taskRef.jobId)) return "empty";
      var statuses = [state.summary.status, state.planCandidate.status,
        state.planExecution.status, state.evidence.status];
      if (statuses.indexOf("loading") !== -1 || statuses.indexOf("idle") !== -1) return "loading";
      if (statuses.every(function (item) { return item === "error"; })) return "error";
      return "ready";
    }

    function render() {
      if (!host) return;
      var fx = fixture();
      var html = '<div class="acp-agent-root" data-acp-module-state="' + overallState() + '" data-acp-fixture="' +
        (fx ? "true" : "false") + '">';
      // connection semantics: no DSH/model session is a NORMAL state here
      // (chip is acp-tag--conn — connection status is NOT model-explanation
      // semantics; acp-tag--model stays for model-output chips only).
      html += '<div class="acp-agent-banner">' +
        '<span class="acp-tag acp-tag--conn">' +
        '<svg width="12" height="12" aria-hidden="true"><use href="#acp-i-signal"/></svg>' +
        '<span data-i18n="agent.connect.no_model">只读 · 未连接模型</span></span>' +
        (fx ? '<span class="acp-tag acp-tag--warn" data-acp-demo-badge="true" ' +
          'data-i18n="agent.fixture.badge">演示数据（fixture）</span>' : "") +
        "</div>";
      if (overallState() === "empty") {
        html += '<div class="acp-state acp-state-empty" role="status">' +
          '<p class="acp-state-title" data-i18n="agent.empty.no_record">暂无研究记录</p>' +
          '<p class="acp-state-msg" data-i18n="agent.empty.no_record_hint">' +
          "在执行工作区选择任务后，此处显示关联研究</p></div>";
      } else {
        html += summaryCardHtml() + planCardHtml() + evidenceCardHtml();
      }
      html += "</div>";
      host.innerHTML = html;
      applyI18n();
    }

    /* Shell i18n is optional: without it the zh literals rendered above stay
     * as-is (never a hard dependency). */
    function applyI18n() {
      var t = shellT();
      if (!t || !host || !host.querySelectorAll) return;
      var nodes = host.querySelectorAll("[data-i18n]");
      for (var i = 0; i < nodes.length; i += 1) {
        var key = nodes[i].getAttribute("data-i18n");
        var text = t(key);
        if (text && text !== key) nodes[i].textContent = text;
      }
    }

    // ------------------------------------------------------------------
    // commands (navigation only — this slice has no write commands)
    // ------------------------------------------------------------------

    function onClick(event) {
      if (disposed || !host) return;
      var btn = event && event.target && typeof event.target.closest === "function"
        ? event.target.closest("[data-cmd]") : null;
      if (!btn || typeof btn.getAttribute !== "function") return;
      if (host.contains && typeof host.contains === "function" && !host.contains(btn)) return;
      var cmd = btn.getAttribute("data-cmd");
      if (cmd === "retry") {
        generation += 1;
        loadAll(generation);
        return;
      }
      if (cmd === "openTask") {
        if (typeof commands.openTask === "function") {
          commands.openTask({
            jobId: btn.getAttribute("data-job-id"),
            attempt: Number(btn.getAttribute("data-attempt")) || 1,
          });
        }
        return;
      }
      if (cmd === "openStructure") {
        if (typeof commands.openStructure === "function") {
          var taskRef = snapshot.taskRef || {};
          commands.openStructure({
            jobId: taskRef.jobId,
            attempt: taskRef.attempt == null ? 1 : taskRef.attempt,
            artifactId: btn.getAttribute("data-artifact-id"),
            relativePath: btn.getAttribute("data-relative-path"),
          });
        }
        return;
      }
      if (cmd === "openEvidence") {
        if (typeof commands.openEvidence === "function") {
          var ref = snapshot.taskRef || {};
          commands.openEvidence({
            jobId: ref.jobId,
            attempt: ref.attempt == null ? 1 : ref.attempt,
          });
        }
      }
    }

    // ------------------------------------------------------------------
    // ModuleInstance lifecycle
    // ------------------------------------------------------------------

    function normalizeSnapshot(next) {
      next = next || {};
      return {
        projectId: next.projectId != null ? next.projectId : null,
        permission: next.permission != null ? next.permission : "read",
        researchRef: next.researchRef || null,
        taskRef: next.taskRef || null,
      };
    }

    function mount(hostEl, initial) {
      host = hostEl;
      disposed = false;
      snapshot = normalizeSnapshot(initial);
      generation += 1;
      var gen = generation;
      if (context && typeof context.subscribe === "function" && !unsubscribe) {
        unsubscribe = context.subscribe(function () {
          var next = context.getSnapshot ? context.getSnapshot() : null;
          if (next) updateContext(next);
        });
      }
      if (host && typeof host.addEventListener === "function" && !clickHandler) {
        clickHandler = onClick;
        host.addEventListener("click", clickHandler);
      }
      render();
      return loadAll(gen);
    }

    function updateContext(next) {
      if (disposed) return;
      next = next || {};
      var prevResearch = refKey(snapshot.researchRef);
      var prevTask = taskKey(snapshot.taskRef);
      snapshot = {
        projectId: next.projectId != null ? next.projectId : snapshot.projectId,
        permission: next.permission != null ? next.permission : snapshot.permission,
        researchRef: "researchRef" in next ? next.researchRef : snapshot.researchRef,
        taskRef: "taskRef" in next ? next.taskRef : snapshot.taskRef,
      };
      var researchChanged = refKey(snapshot.researchRef) !== prevResearch;
      var taskChanged = taskKey(snapshot.taskRef) !== prevTask;
      if (!researchChanged && !taskChanged) {
        render();
        return;
      }
      // A→B→A and rerun attempt changes bump the generation, so every
      // in-flight response from an older load is discarded, not rendered.
      generation += 1;
      var gen = generation;
      var loads = [];
      if (researchChanged) loads.push(loadResearch(gen));
      if (taskChanged) {
        loads.push(loadTask(gen));
        loads.push(loadEvidence(gen));
      }
      render();
      return Promise.all(loads);
    }

    function dispose() {
      if (disposed) return;
      disposed = true;
      generation += 1; // invalidates every in-flight load
      if (typeof unsubscribe === "function") {
        try { unsubscribe(); } catch (error) { /* subscriber already gone */ }
      }
      unsubscribe = null;
      if (host && typeof host.removeEventListener === "function" && clickHandler) {
        host.removeEventListener("click", clickHandler);
      }
      clickHandler = null;
      if (host) host.innerHTML = "";
      host = null;
    }

    return {
      mount: mount,
      updateContext: updateContext,
      dispose: dispose,
    };
  }

  /* -------------------------------------------------------------------- */

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["agent-workspace"] = {
    descriptor: {
      id: "agent-workspace",
      interfaceVersion: 1,
      dependencies: [],
      // Top-level provides/binds are what the Shell findPanelFor reads
      // (commands.js resolves "research.summary"/"research.evidence" / binds researchRef).
      provides: ["research.summary", "research.plan", "research.evidence"],
      binds: ["researchRef"],
      capabilities: {
        requires: [],
        provides: ["research.summary", "research.plan", "research.evidence"],
      },
      hosts: ["acp-web"],
      supportedInstancing: "single",
      configSchemaVersion: 0,
      error: {
        titleKey: "agent.workspace.errorTitle",
        messageKey: "agent.workspace.errorMessage",
      },
    },
    factory: factory,
  };
})();
