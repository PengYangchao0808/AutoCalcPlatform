/*
 * ACP Workbench — Scientific evidence view (T-31 / C21): honest claim states
 * next to execution status, expandable provenance, standard ACV reuse.
 *
 * Owner: frontend/js/workbench/features/** (OpenCode). Version: v20261023.1
 *
 * Contracts (plan C21 / acp2-plan-c-rph-workbench todo 31):
 *  - execution status and the claim state (supported/refuted/inconclusive/
 *    not_evaluated) are shown NEXT TO each other and never conflated: a
 *    COMPLETED execution is never rendered as mechanism-correct, and a single
 *    imaginary frequency is never IRC connectivity (the IRC claim stays its
 *    own row with its own status);
 *  - every claim expands to raw artifact refs, validator (id/version/kind),
 *    method/version, missing validation and the RPH decision source;
 *  - all data is read-only normalized products: platform reads go through
 *    ACPClient (science/research query modules) only — this module owns NO
 *    fetch, never parses large ORCA outputs and computes no thermochemistry;
 *  - the standard ACV renderers are reused via frontend/js/visualization/*
 *    (scene/framing/energy chart; IRC + vibration projections render through
 *    the same scene/energy modules). The energy chart exposes ONLY
 *    "save as candidate" — it never submits a task (ANTI #22);
 *  - lifecycle: mount / hide / switch task / dispose are all safe and
 *    idempotent; after dispose() no tick, callback, animation or scene call
 *    fires again (销毁无旧回调);
 *  - `inconclusive` renders a visible "live not verified" state and is never
 *    styled as green success.
 *
 * Fixture seam: when window.ACP_EVIDENCE_BUNDLE_FIXTURE is present at mount
 * (contracts/fixtures/evidence-bundle.js, injected by tests/acceptance), the
 * bundle is adopted and the panel shows the 演示数据 badge. Without it the
 * panel stays in its empty "no evidence loaded" state — nothing is fabricated.
 */
window.ACPScientificEvidence = (function () {
  "use strict";

  var CLAIM_STATES = ["supported", "refuted", "inconclusive", "not_evaluated"];
  var EXEC_STATES = ["completed", "failed", "cancelled", "not_started"];
  var CLAIM_STATE_LABEL = {
    supported: "supported",
    refuted: "refuted",
    inconclusive: "inconclusive",
    not_evaluated: "not_evaluated",
  };
  var EXEC_STATE_LABEL = {
    completed: "COMPLETED",
    failed: "FAILED",
    cancelled: "CANCELLED",
    not_started: "NOT_STARTED",
  };
  var LIVE_UNVERIFIED_TEXT = "live not verified";
  var DEMO_BADGE_TEXT = "演示数据";
  var ENERGY_ACTIONS = ["save-candidate"]; // the ONLY energy action (ANTI #22)

  var deps = {
    science: null,     // ACPClient.science (read-only)
    research: null,    // ACPClient.research (read-only, RPH decision source)
    sceneFactory: null, // TEST-ONLY injection seam (never used by the page)
    onSaveCandidate: null,
  };

  var state = {
    mounted: false,
    hidden: false,
    disposed: false,
    fixture: false,
    taskRef: null,
    bundle: null,
    claims: [],
    executionState: "not_started",
    expanded: {},
    tickCallbacks: [],
    callbacksFired: 0,
    framesRendered: 0,
    scenes: [],
    framingStates: [],
    savedCandidates: [],
    energyActions: ENERGY_ACTIONS.slice(),
    status: "",
  };

  var domDoc = null;
  var rootEl = null;
  var energyChartInitialized = false;
  var frameHandle = null;

  // ---------------------------------------------------------------------
  // helpers
  // ---------------------------------------------------------------------

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function text(value, fallback) {
    if (value == null || value === "") return fallback == null ? "—" : fallback;
    return String(value);
  }

  function shortDigest(digest) {
    if (isObject(digest) && typeof digest.value === "string") {
      return digest.value.slice(0, 12) + "…";
    }
    if (typeof digest === "string" && digest) return digest.slice(0, 12) + "…";
    return "—";
  }

  function nowLabel() {
    try {
      return new Date().toISOString();
    } catch (e) {
      return "";
    }
  }

  function sceneApi() {
    var ns = window.ACPVisualization;
    return ns && ns.scene ? ns.scene : null;
  }

  function framingApi() {
    var ns = window.ACPVisualization;
    return ns && ns.framing ? ns.framing : null;
  }

  function energyChartApi() {
    var ns = window.ACPVisualization;
    return ns && ns.energyChart ? ns.energyChart : null;
  }

  // ---------------------------------------------------------------------
  // claim-bundle intake (normalized products only)
  // ---------------------------------------------------------------------

  function normalizeClaim(item) {
    if (!isObject(item)) return null;
    if (CLAIM_STATES.indexOf(item.status) === -1) return null; // fifth state refused
    var sourceRefs = Array.isArray(item.source_refs) ? item.source_refs.slice() : [];
    var evidenceRefs = Array.isArray(item.evidence_refs) ? item.evidence_refs.slice() : [];
    if (!sourceRefs.length && evidenceRefs.length) {
      // View-model claims carry evidence_refs only; surface them as raw refs.
      sourceRefs = evidenceRefs.map(function (path) {
        return { artifact_path: String(path), digest: null, quantity: null, role: "evidence_ref", units: null };
      });
    }
    return {
      claim_id: String(item.claim_id || ""),
      statement: String(item.statement || ""),
      status: item.status,
      scope: isObject(item.scope) ? item.scope : null,
      source_refs: sourceRefs,
      missing_checks: Array.isArray(item.missing_checks) ? item.missing_checks.slice() : [],
      validator: isObject(item.validator) ? item.validator : null,
      limitations: Array.isArray(item.limitations) ? item.limitations.slice() : [],
      reason: item.reason == null ? null : String(item.reason),
      evidence_refs: evidenceRefs,
    };
  }

  function refuse(reason) {
    state.status = reason;
    state.claims = [];
    state.expanded = {};
    render();
    return getState();
  }

  /** Adopts a read-only bundle. The claim status is copied verbatim from the
   * EvidenceReport/ViewModel — this UI never computes or upgrades it. */
  function setEvidence(bundle) {
    if (state.disposed) return getState();
    if (!isObject(bundle)) return refuse("evidence_bundle_invalid");
    var report = isObject(bundle.evidence_report) ? bundle.evidence_report : null;
    var view = isObject(bundle.view) ? bundle.view : null;
    var rawClaims = report && Array.isArray(report.claims) && report.claims.length
      ? report.claims
      : (view && Array.isArray(view.claims) ? view.claims : []);
    var claims = [];
    for (var i = 0; i < rawClaims.length; i += 1) {
      var normalized = normalizeClaim(rawClaims[i]);
      if (!normalized) return refuse("claim_state_refused");
      claims.push(normalized);
    }
    var execution = report && isObject(report.execution) ? report.execution : null;
    state.bundle = {
      view: view,
      evidence_report: report,
      provenance: isObject(bundle.provenance) ? bundle.provenance : null,
      rph_decision: isObject(bundle.rph_decision) ? bundle.rph_decision : null,
      projections: isObject(bundle.projections) ? bundle.projections : null,
      report_limitations: report && Array.isArray(report.limitations) ? report.limitations.slice() : [],
      fixture: bundle.fixture === true,
    };
    state.claims = claims;
    state.executionState = execution && EXEC_STATES.indexOf(execution.terminal_state) !== -1
      ? execution.terminal_state
      : "not_started";
    state.expanded = {};
    state.fixture = bundle.fixture === true;
    state.taskRef = view && isObject(view.task_ref) ? Object.assign({}, view.task_ref) : null;
    state.status = "evidence_loaded";
    render();
    renderProjections();
    return getState();
  }

  function adoptViewClaims(view) {
    var rawClaims = view && Array.isArray(view.claims) ? view.claims : [];
    var claims = [];
    for (var i = 0; i < rawClaims.length; i += 1) {
      var normalized = normalizeClaim(rawClaims[i]);
      if (!normalized) return false;
      claims.push(normalized);
    }
    state.claims = claims;
    state.expanded = {};
    return true;
  }

  /** Live path: read the normalized view + RPH decision through ACPClient only.
   * This module never fetches; the adapters live in the client layer. */
  function loadViaClient(refs, scope, opts) {
    refs = refs || {};
    opts = opts || {};
    if (state.disposed) return Promise.resolve(getState());
    if (!deps.science || typeof deps.science.fetchView !== "function") {
      return Promise.reject(new Error("scientific-evidence: ACPClient.science not configured"));
    }
    return deps.science.fetchView(refs.view_ref, scope, opts).then(function (view) {
      var next = isObject(state.bundle) ? state.bundle : {};
      next.view = view;
      next.fixture = false;
      state.taskRef = view && isObject(view.task_ref) ? Object.assign({}, view.task_ref) : null;
      if (!adoptViewClaims(view)) return refuse("claim_state_refused");
      state.fixture = false;
      if (refs.record_ref && deps.research && typeof deps.research.readRound === "function") {
        return deps.research.readRound(refs.record_ref, scope, opts).then(function (round) {
          next.rph_decision = {
            source: "research_record:" + String(round.research_id || "") + "@round=" + String(round.round),
            research_id: round.research_id,
            round: round.round,
            decision: round.decision,
            record_revision: round.record_revision == null ? null : round.record_revision,
            approval_ref: round.approval_ref == null ? null : round.approval_ref,
            task_ref: state.taskRef,
          };
          state.bundle = next;
          state.status = "evidence_loaded";
          render();
          return getState();
        });
      }
      state.bundle = next;
      state.status = "evidence_loaded";
      render();
      return getState();
    });
  }

  // ---------------------------------------------------------------------
  // rendering — claim rows, expansion, projections
  // ---------------------------------------------------------------------

  function claimRowMarkup(claim) {
    var expanded = !!state.expanded[claim.claim_id];
    var liveUnverified = claim.status === "inconclusive";
    var markup = "";
    markup += '<li class="se-claim" data-claim-id="' + esc(claim.claim_id) + '" data-claim-status="' + esc(claim.status) + '">';
    markup += '<button type="button" class="se-claim-toggle" data-se-claim-toggle="' + esc(claim.claim_id) + '" aria-expanded="' + (expanded ? "true" : "false") + '">';
    markup += '<span class="se-claim-statement">' + esc(claim.statement) + "</span>";
    /* Execution status and claim state are rendered side by side and carry
     * separate, differently-named classes — never one merged verdict badge. */
    markup += '<span class="se-badge se-exec" data-exec-state="' + esc(state.executionState) + '">执行: ' + esc(EXEC_STATE_LABEL[state.executionState] || state.executionState) + "</span>";
    markup += '<span class="se-badge se-state se-state-' + esc(claim.status) + '" data-claim-state-badge="' + esc(claim.status) + '">命题: ' + esc(CLAIM_STATE_LABEL[claim.status] || claim.status) + "</span>";
    if (liveUnverified) {
      markup += '<span class="se-live-unverified" data-se-live-unverified="1">' + LIVE_UNVERIFIED_TEXT + "（实时未验证）</span>";
    }
    markup += "</button>";
    markup += '<div class="se-claim-detail" data-se-detail="' + esc(claim.claim_id) + '"' + (expanded ? "" : " hidden") + ">";
    markup += claimDetailMarkup(claim);
    markup += "</div></li>";
    return markup;
  }

  function claimDetailMarkup(claim) {
    var out = "";

    /* 1. raw artifact refs */
    out += '<div class="se-detail-block" data-se-detail-block="artifacts"><h4>原始工件 / raw artifacts</h4>';
    if (!claim.source_refs.length) {
      out += '<p class="se-empty">无原始工件引用</p>';
    } else {
      out += '<ul class="se-artifact-list">';
      claim.source_refs.forEach(function (ref) {
        ref = isObject(ref) ? ref : {};
        out += '<li class="se-artifact-row" data-se-artifact-path="' + esc(ref.artifact_path) + '">';
        out += '<code class="se-artifact-path">' + esc(text(ref.artifact_path)) + "</code>";
        out += '<span class="se-artifact-role">' + esc(text(ref.role)) + "</span>";
        out += '<span class="se-artifact-digest">sha256:' + esc(shortDigest(ref.digest)) + "</span>";
        out += '<span class="se-artifact-units">单位: ' + esc(text(ref.units, "null")) + "</span>";
        if (ref.quantity) out += '<span class="se-artifact-quantity">量: ' + esc(ref.quantity) + "</span>";
        out += "</li>";
      });
      out += "</ul>";
    }
    out += "</div>";

    /* 2. validator */
    out += '<div class="se-detail-block" data-se-detail-block="validator"><h4>validator / 验证器</h4>';
    if (claim.validator) {
      out += '<p class="se-validator-line" data-se-validator-id="' + esc(claim.validator.validator_id) + '">';
      out += '<code>' + esc(text(claim.validator.validator_id)) + "</code>";
      out += '<span class="se-validator-version">v' + esc(text(claim.validator.validator_version)) + "</span>";
      out += '<span class="se-validator-kind">' + esc(text(claim.validator.kind)) + "</span>";
      out += "</p>";
      if (claim.validator.description) {
        out += '<p class="se-validator-desc">' + esc(claim.validator.description) + "</p>";
      }
    } else {
      out += '<p class="se-empty" data-se-validator-id="none">无验证器（不得视为通过）</p>';
    }
    out += "</div>";

    /* 3. method / version (from the frozen provenance product, never recomputed) */
    out += '<div class="se-detail-block" data-se-detail-block="method"><h4>方法 / 版本</h4>';
    out += methodMarkup();
    out += "</div>";

    /* 4. missing validation */
    out += '<div class="se-detail-block" data-se-detail-block="missing"><h4>缺失验证 / missing validation</h4>';
    if (!claim.missing_checks.length) {
      out += '<p class="se-empty">无缺失检查记录</p>';
    } else {
      out += '<ul class="se-missing-list">';
      claim.missing_checks.forEach(function (check) {
        check = isObject(check) ? check : {};
        out += '<li class="se-missing-row" data-se-check-id="' + esc(check.check_id) + '" data-se-check-blocking="' + (check.blocking ? "1" : "0") + '">';
        out += '<code>' + esc(text(check.check_id)) + "</code>";
        out += '<span class="se-missing-reason">' + esc(text(check.reason)) + "</span>";
        out += '<span class="se-missing-blocking">' + (check.blocking ? "blocking" : "non-blocking") + "</span>";
        out += "</li>";
      });
      out += "</ul>";
    }
    out += "</div>";

    /* 5. RPH decision source */
    out += '<div class="se-detail-block" data-se-detail-block="decision"><h4>RPH 决策来源 / decision source</h4>';
    out += decisionMarkup();
    out += "</div>";

    /* 6. limitations attached to the claim */
    if (claim.limitations.length || (state.bundle && Array.isArray(state.bundle.report_limitations) && state.bundle.report_limitations.length)) {
      out += '<div class="se-detail-block" data-se-detail-block="limitations"><h4>限制 / limitations</h4><ul class="se-limitation-list">';
      claim.limitations.forEach(function (id) {
        out += '<li data-se-limitation-id="' + esc(id) + '">' + esc(id) + "</li>";
      });
      (state.bundle && Array.isArray(state.bundle.report_limitations) ? state.bundle.report_limitations : []).forEach(function (item) {
        item = isObject(item) ? item : {};
        if (Array.isArray(item.affected_claim_ids) && item.affected_claim_ids.indexOf(claim.claim_id) === -1) return;
        out += '<li data-se-limitation-id="' + esc(item.limitation_id) + '">' + esc(text(item.description, item.limitation_id)) + "</li>";
      });
      out += "</ul></div>";
    }

    if (claim.reason) {
      out += '<p class="se-claim-reason">' + esc(claim.reason) + "</p>";
    }
    return out;
  }

  function methodMarkup() {
    var prov = state.bundle && state.bundle.provenance;
    if (!prov) return '<p class="se-empty">无 provenance（方法/版本未知）</p>';
    var method = isObject(prov.method) ? prov.method : {};
    var software = isObject(prov.software) ? prov.software : {};
    var basis = isObject(prov.basis) ? prov.basis : {};
    var out = '<dl class="se-method-list">';
    out += '<div class="se-method-row" data-se-method-field="method"><dt>method</dt><dd>' + esc(text(method.method_id)) + "</dd></div>";
    out += '<div class="se-method-row" data-se-method-field="software"><dt>software</dt><dd>' + esc(text(software.software_id)) + " " + esc(text(software.software_version)) + "</dd></div>";
    out += '<div class="se-method-row" data-se-method-field="basis"><dt>basis</dt><dd>' + esc(text(basis.basis_id)) + "</dd></div>";
    var conditions = isObject(prov.conditions) ? prov.conditions : {};
    out += '<div class="se-method-row" data-se-method-field="conditions"><dt>conditions</dt><dd>' +
      esc(text(conditions.temperature_kelvin, "null")) + " K · " +
      esc(text(conditions.standard_state, "null")) + " · " +
      esc(text(conditions.reference_state, "null")) + "</dd></div>";
    out += "</dl>";
    return out;
  }

  function decisionMarkup() {
    var decision = state.bundle && state.bundle.rph_decision;
    if (!decision) return '<p class="se-empty" data-se-decision-source="none">无 RPH 决策记录（不得推断审批）</p>';
    var out = '<dl class="se-decision-list">';
    out += '<div class="se-decision-row" data-se-decision-field="source"><dt>source</dt><dd data-se-decision-source="' + esc(text(decision.source)) + '">' + esc(text(decision.source)) + "</dd></div>";
    out += '<div class="se-decision-row" data-se-decision-field="research"><dt>research</dt><dd>' + esc(text(decision.research_id)) + " · round " + esc(text(decision.round)) + "</dd></div>";
    out += '<div class="se-decision-row" data-se-decision-field="decision"><dt>decision</dt><dd>' + esc(text(decision.decision)) + "</dd></div>";
    out += '<div class="se-decision-row" data-se-decision-field="revision"><dt>record_revision</dt><dd>' + esc(text(decision.record_revision)) + "</dd></div>";
    out += '<div class="se-decision-row" data-se-decision-field="approval"><dt>approval_ref</dt><dd>' + esc(text(decision.approval_ref, "null")) + "</dd></div>";
    out += "</dl>";
    return out;
  }

  function render() {
    if (state.disposed || !rootEl) return;
    state.framesRendered += 1;
    var out = "";
    out += '<div class="se-root" data-se-root="1">';
    out += '<div class="se-toolbar">';
    out += '<span class="se-task-ref" data-se-task-ref="' + esc(state.taskRef ? String(state.taskRef.job_id) + "/" + String(state.taskRef.attempt) : "none") + '">' +
      esc(state.taskRef ? String(state.taskRef.job_id) + " · attempt " + String(state.taskRef.attempt) : "未选择任务") + "</span>";
    out += '<span class="se-demo-badge"' + (state.fixture ? "" : " hidden") + ">" + DEMO_BADGE_TEXT + "</span>";
    out += '<span class="se-kind-badge" data-se-evidence-kind="' + (state.fixture ? "fixture" : "live") + '">' + (state.fixture ? "fixture evidence" : "live evidence") + "</span>";
    out += "</div>";
    out += '<p class="se-legend" data-se-legend="1">执行状态与命题状态并列显示；执行完成（COMPLETED）不等于机制正确。</p>';

    if (!state.claims.length) {
      out += '<p class="se-empty" data-se-empty="1">未加载证据（等待 EvidenceReport/ViewModel 投影）</p>';
    } else {
      out += '<ul class="se-claims" data-se-claims="1">';
      state.claims.forEach(function (claim) {
        out += claimRowMarkup(claim);
      });
      out += "</ul>";
    }

    out += '<div class="se-projections" data-se-projections="1">' + projectionMarkup() + "</div>";
    out += "</div>";
    rootEl.innerHTML = out;
    wireDom();
  }

  function projectionMarkup() {
    var projections = state.bundle && state.bundle.projections;
    if (!projections) return "";
    var out = "";
    if (projections.energy) {
      out += '<div class="se-projection" data-se-projection="energy">';
      out += '<h4>能量剖面 / energy</h4>';
      out += '<div class="se-energy-chart" data-se-energy-chart="1">' + energySvgMarkup(projections.energy) + "</div>";
      /* ANTI #22: the energy chart's ONLY action is save-as-candidate. */
      out += '<div class="se-energy-actions" data-se-energy-actions="1">';
      out += '<button type="button" class="se-save-candidate-btn" data-se-action="save-candidate">保存为候选 / save as candidate</button>';
      out += "</div></div>";
    }
    if (projections.irc) {
      out += '<div class="se-projection" data-se-projection="irc">';
      out += '<h4>IRC / 路径</h4>';
      out += '<div class="se-energy-chart" data-se-irc-chart="1">' + energySvgMarkup(projections.irc) + "</div>";
      out += '<div class="se-scene-host" data-se-scene-host="irc"></div>';
      out += '<p class="se-note" data-se-irc-note="1">单虚频 ≠ IRC 连通；连通性以对应 claim 状态为准。</p>';
      out += "</div>";
    }
    if (projections.vibration) {
      out += '<div class="se-projection" data-se-projection="vibration">';
      out += '<h4>振动模式 / vibration</h4>';
      out += '<div class="se-scene-host" data-se-scene-host="vibration"></div>';
      out += '<p class="se-note" data-se-vibration-note="1">' + esc(projections.vibration.connectivity_note || "") + "</p>";
      out += "</div>";
    }
    return out;
  }

  function energySvgMarkup(projection) {
    var chart = energyChartApi();
    if (!chart || typeof chart.energyGraphBuildSvg !== "function") {
      return '<p class="se-empty">能量渲染器不可用（只读投影保留）</p>';
    }
    try {
      ensureEnergyChartInit(chart);
      return chart.energyGraphBuildSvg(projection) || "";
    } catch (e) {
      return '<p class="se-empty">能量渲染失败: ' + esc(e && e.message) + "</p>";
    }
  }

  function ensureEnergyChartInit(chart) {
    if (energyChartInitialized) return;
    var localGraphState = {
      xAxisMode: "scanned_coordinate", selectedNodeId: null, layout: null,
      xDomain: null, yDomain: null, seriesId: null, jobId: null, currentDomain: null,
      candidateListFilter: "all",
    };
    var localS2 = { selectionLocked: true, candidates: [], dirty: false };
    chart.init({
      t: function (key) { return key; },
      energyGraphState: localGraphState,
      s2scanState: localS2,
      currentLang: "zh-CN",
      translateStatus: function (value) { return String(value); },
      escapeHtml: esc,
      energyGenCandidateName: function () { return "candidate"; },
      energyGraphFrameCandidateFor: function () { return null; },
      energyInspectorMetadataRows: function () { return ""; },
      renderTrajectoryAnnotations: function () { return { svg: "", bandSvg: "", bandHeight: 0, plotSvg: "" }; },
    });
    energyChartInitialized = true;
  }

  /** Scene reuse through visualization/scene_instance.js + framing.js only. */
  function renderProjections() {
    if (state.disposed) return;
    disposeScenes();
    var projections = state.bundle && state.bundle.projections;
    if (!projections || !rootEl || typeof rootEl.querySelector !== "function") return;
    if (projections.irc && Array.isArray(projections.irc.structures)) {
      var ircHost = rootEl.querySelector('[data-se-scene-host="irc"]');
      if (ircHost) mountScene(ircHost, projections.irc.structures, false);
    }
    if (projections.vibration && Array.isArray(projections.vibration.atoms)) {
      var vibHost = rootEl.querySelector('[data-se-scene-host="vibration"]');
      if (vibHost) mountScene(vibHost, [{ xyz: atomsToXyz(projections.vibration.atoms) }], true, projections.vibration);
    }
  }

  function atomsToXyz(atoms) {
    var lines = [String(atoms.length), "vibration frame"];
    atoms.forEach(function (atom) {
      atom = isObject(atom) ? atom : {};
      lines.push([text(atom.elem, "C"), text(atom.x, "0"), text(atom.y, "0"), text(atom.z, "0")].join("  "));
    });
    return lines.join("\n");
  }

  function createSceneInstance(container) {
    if (typeof deps.sceneFactory === "function") return deps.sceneFactory(container);
    var scene = sceneApi();
    if (!scene || typeof scene.create !== "function") return null;
    return scene.create({ container: container, options: {} });
  }

  function mountScene(container, structures, animate, vibration) {
    var instance = createSceneInstance(container);
    if (!instance) return;
    state.scenes.push(instance);
    structures.forEach(function (entry) {
      entry = isObject(entry) ? entry : {};
      var token = instance.addModel(String(entry.xyz || ""), { format: "xyz" });
      if (token && typeof token.setStyle === "function") {
        token.setStyle({}, { stick: {}, sphere: { scale: 0.3 } });
      }
    });
    var framing = framingApi();
    if (framing && typeof framing.scheduleViewerFraming === "function") {
      var framingState = { timer: null, epoch: 0, observer: null, pending: false, settled: false, lastW: 0, lastH: 0 };
      state.framingStates.push(framingState);
      framing.scheduleViewerFraming(container, framingState, function () {
        return state.disposed ? null : instance;
      }, {});
    }
    if (animate && vibration) {
      var base = Array.isArray(vibration.atoms) ? vibration.atoms : [];
      var displacement = Array.isArray(vibration.displacement) ? vibration.displacement : [];
      var phase = 0;
      var frameToken = null;
      registerTick(function () {
        phase += 0.35;
        var scale = Math.sin(phase) * 1.6;
        var moved = base.map(function (atom, index) {
          var d = displacement[index] || [0, 0, 0];
          return {
            elem: atom.elem,
            x: Number(atom.x) + Number(d[0] || 0) * scale,
            y: Number(atom.y) + Number(d[1] || 0) * scale,
            z: Number(atom.z) + Number(d[2] || 0) * scale,
          };
        });
        if (frameToken && typeof instance.removeModel === "function") {
          instance.removeModel(frameToken);
        }
        frameToken = instance.addModel(atomsToXyz(moved), { format: "xyz" });
      });
      scheduleFrame();
    }
    if (typeof instance.setHidden === "function") instance.setHidden(state.hidden);
  }

  function disposeScenes() {
    if (frameHandle !== null && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(frameHandle);
    }
    frameHandle = null;
    state.framingStates.forEach(function (framingState) {
      var framing = framingApi();
      if (framing && typeof framing.disposeViewerFraming === "function") {
        framing.disposeViewerFraming(framingState);
      }
    });
    state.framingStates.length = 0;
    state.scenes.forEach(function (instance) {
      if (instance && typeof instance.dispose === "function") instance.dispose();
    });
    state.scenes.length = 0;
    state.tickCallbacks.length = 0;
  }

  function scheduleFrame() {
    if (state.disposed || state.hidden) return;
    if (frameHandle !== null) return;
    if (typeof requestAnimationFrame !== "function") return;
    frameHandle = requestAnimationFrame(function () {
      frameHandle = null;
      if (state.disposed || state.hidden) return;
      tick();
      scheduleFrame();
    });
  }

  // ---------------------------------------------------------------------
  // lifecycle — mount / hide / show / switchTask / dispose / tick
  // ---------------------------------------------------------------------

  function wireDom() {
    if (!rootEl || typeof rootEl.addEventListener !== "function") return;
    if (rootEl.getAttribute && rootEl.getAttribute("data-se-wired") === "1") return;
    rootEl.addEventListener("click", function (event) {
      if (state.disposed) return;
      var target = event && event.target;
      var attr = null;
      if (target && typeof target.getAttribute === "function") {
        attr = target.getAttribute("data-se-claim-toggle") || target.getAttribute("data-se-action");
      }
      if (!attr && target && target.closest && typeof target.closest === "function") {
        var toggleBtn = target.closest("[data-se-claim-toggle]");
        var actionBtn = target.closest("[data-se-action]");
        attr = toggleBtn ? toggleBtn.getAttribute("data-se-claim-toggle")
          : (actionBtn ? actionBtn.getAttribute("data-se-action") : null);
      }
      if (!attr) return;
      if (attr === "save-candidate") {
        saveEnergyCandidate();
        return;
      }
      toggleClaim(attr);
    });
    if (rootEl.setAttribute) rootEl.setAttribute("data-se-wired", "1");
  }

  function mount(doc, options) {
    options = options || {};
    domDoc = doc || (typeof document !== "undefined" ? document : null);
    if (!domDoc) return getState();
    if (typeof options.science === "object" && options.science) deps.science = options.science;
    if (typeof options.research === "object" && options.research) deps.research = options.research;
    if (typeof options.sceneFactory === "function") deps.sceneFactory = options.sceneFactory;
    if (typeof options.onSaveCandidate === "function") deps.onSaveCandidate = options.onSaveCandidate;
    if (!deps.science && window.ACPClient && window.ACPClient.science) {
      deps.science = window.ACPClient.science;
    }
    if (!deps.research && window.ACPClient && window.ACPClient.research) {
      deps.research = window.ACPClient.research;
    }
    rootEl = typeof options.container === "object" && options.container
      ? options.container
      : domDoc.getElementById("scientific-evidence-body");
    state.mounted = !!rootEl;
    state.disposed = false; // re-mount after dispose is a fresh lifecycle
    if (!rootEl) return getState();
    render();
    if (window.ACP_EVIDENCE_BUNDLE_FIXTURE) {
      setEvidence(window.ACP_EVIDENCE_BUNDLE_FIXTURE);
    }
    return getState();
  }

  function hide() {
    if (state.disposed) return getState();
    state.hidden = true;
    if (frameHandle !== null && typeof cancelAnimationFrame === "function") {
      cancelAnimationFrame(frameHandle);
    }
    frameHandle = null;
    state.scenes.forEach(function (instance) {
      if (instance && typeof instance.setHidden === "function") instance.setHidden(true);
      if (instance && typeof instance.pausePlayback === "function") instance.pausePlayback();
    });
    return getState();
  }

  function show() {
    if (state.disposed) return getState();
    state.hidden = false;
    state.scenes.forEach(function (instance) {
      if (instance && typeof instance.setHidden === "function") instance.setHidden(false);
      if (instance && typeof instance.resumePlayback === "function") instance.resumePlayback();
    });
    scheduleFrame();
    return getState();
  }

  /** Task switch: every claim/expanded/projection of the previous task is
   * cleared before the new task renders — stale claim state never survives. */
  function switchTask(taskRef) {
    if (state.disposed) return getState();
    disposeScenes();
    state.claims = [];
    state.expanded = {};
    state.bundle = null;
    state.executionState = "not_started";
    state.taskRef = taskRef ? Object.assign({}, taskRef) : null;
    state.status = "task_switched";
    render();
    return getState();
  }

  /** Idempotent teardown. After this returns, tick()/render()/setEvidence()
   * are no-ops and no scene, timer or callback may fire (销毁无旧回调). */
  function dispose() {
    if (state.disposed) return getState();
    state.disposed = true;
    state.hidden = false;
    disposeScenes();
    state.tickCallbacks.length = 0;
    state.claims = [];
    state.expanded = {};
    state.bundle = null;
    if (rootEl && typeof rootEl.removeEventListener === "function") {
      rootEl.removeEventListener("click", function () {});
    }
    if (rootEl) rootEl.innerHTML = "";
    rootEl = null;
    domDoc = null;
    state.mounted = false;
    return getState();
  }

  function registerTick(fn) {
    if (state.disposed || typeof fn !== "function") return null;
    var token = { fn: fn, alive: true };
    state.tickCallbacks.push(token);
    return token;
  }

  /** One animation frame. Runs at most the registered callbacks, and only
   * while mounted, visible and not disposed. Returns how many ran. */
  function tick() {
    if (state.disposed || state.hidden) return 0;
    var ran = 0;
    var pending = state.tickCallbacks.slice();
    for (var i = 0; i < pending.length; i += 1) {
      if (state.disposed) break;
      var token = pending[i];
      if (!token.alive) continue;
      token.fn();
      state.callbacksFired += 1;
      ran += 1;
    }
    return ran;
  }

  function toggleClaim(claimId) {
    if (state.disposed) return false;
    state.expanded[claimId] = !state.expanded[claimId];
    render();
    return !!state.expanded[claimId];
  }

  /** The energy chart's single allowed action (ANTI #22: never submit). */
  function saveEnergyCandidate(payload) {
    if (state.disposed) return null;
    var record = Object.assign({
      saved_at: nowLabel(),
      task_ref: state.taskRef ? Object.assign({}, state.taskRef) : null,
    }, isObject(payload) ? payload : {});
    state.savedCandidates.push(record);
    if (typeof deps.onSaveCandidate === "function") deps.onSaveCandidate(record);
    return record;
  }

  function getState() {
    return {
      mounted: state.mounted,
      hidden: state.hidden,
      disposed: state.disposed,
      fixture: state.fixture,
      status: state.status,
      taskRef: state.taskRef ? Object.assign({}, state.taskRef) : null,
      executionState: state.executionState,
      claims: state.claims.map(function (claim) {
        return { claim_id: claim.claim_id, status: claim.status, statement: claim.statement };
      }),
      claimStates: state.claims.map(function (claim) { return claim.status; }),
      expanded: Object.assign({}, state.expanded),
      callbacksFired: state.callbacksFired,
      framesRendered: state.framesRendered,
      sceneCount: state.scenes.length,
      savedCandidates: state.savedCandidates.slice(),
      energyActions: state.energyActions.slice(),
    };
  }

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("DOMContentLoaded", function () {
      try {
        if (!document.getElementById || !document.getElementById("scientific-evidence-body")) return;
        mount(document);
      } catch (error) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("scientific-evidence mount skipped:", error);
        }
      }
    });
  }

  return {
    CLAIM_STATES: CLAIM_STATES,
    EXEC_STATES: EXEC_STATES,
    ENERGY_ACTIONS: ENERGY_ACTIONS,
    LIVE_UNVERIFIED_TEXT: LIVE_UNVERIFIED_TEXT,
    mount: mount,
    setEvidence: setEvidence,
    loadViaClient: loadViaClient,
    switchTask: switchTask,
    hide: hide,
    show: show,
    dispose: dispose,
    tick: tick,
    registerTick: registerTick,
    toggleClaim: toggleClaim,
    saveEnergyCandidate: saveEnergyCandidate,
    render: render,
    getState: getState,
  };
})();

window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.features = ACPWorkbench.features || {};
ACPWorkbench.features.scientificEvidence = window.ACPScientificEvidence;
