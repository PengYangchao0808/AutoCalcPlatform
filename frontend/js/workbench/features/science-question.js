/*
 * ACP Workbench — Manual Research entry (T-17 / C11): a model-free science
 * question form mounted inside the existing Workbench page.
 *
 * Contracts (plan C11 / acp2-plan-c-rph-workbench todo 17):
 *  - no bare fetch calls anywhere; every platform call goes through ACPClient
 *    (discover/preflight/view via ACPClient.science, upload/preview via
 *    ACPClient.wizardEndpoints, submit via the page api() POST /jobs path);
 *  - NO second job payload: the submit body is built by the wizard's frozen
 *    envelope builder ACPWizardSubmitEdit.buildStructureJobBody (the submit
 *    slice stays the single payload owner — this feature only supplies values);
 *  - question defaults (method/charge/multiplicity/solvent) come from the
 *    capability metadata and each shows its source line;
 *  - discover -> preflight -> submit in that order; a failed preflight
 *    disables submit and no submit adapter call is ever made;
 *  - task detail references the SAME (job_id, attempt) returned by submit and
 *    shows structure, raw artifact refs and limitations;
 *  - works with no model configured: fixture data is embedded here and every
 *    fixture run shows the visible 演示数据 badge;
 *  - ANTI #20: question -> workflow resolution only accepts catalog entries
 *    with status "active" (resolved through the workflow catalog helpers);
 *    a stale/retired id self-heals to an active entry and never becomes a
 *    default.
 */
window.ACPScienceQuestion = (function () {
  "use strict";

  var DEMO_BADGE_TEXT = "演示数据";
  var DEMO_PROJECT_ID = "demo-project";
  var DEFAULT_FIELDS = ["method", "charge", "multiplicity", "solvent", "temperature", "standardState"];

  // Fixture capability catalog + run artifacts. Used only when the platform
  // is unreachable (no model / no service configured) or when a fixture
  // capability body is served; every fixture run carries the 演示数据 badge.
  var FIXTURES = {
    capabilities: {
      protocol_version: "acp-science/1",
      tools: [
        "acp_capabilities", "acp_preflight", "acp_submit", "acp_status",
        "acp_cancel", "acp_prepare_artifact", "acp_read_artifact", "acp_analyze",
      ],
      capabilities: {
        fixture: true,
        questions: [
          {
            question_id: "single_point_energy",
            label: "单点能",
            workflow: "singlepoint",
            method_schema_id: "dft_singlepoint",
            level_id: "single_point",
            engine: "orca",
            basis: "def2-TZVPP",
            defaults: {
              method: { value: "wB97M-V", source: "capability:single_point_energy.defaults.method" },
              charge: { value: 0, source: "capability:single_point_energy.defaults.charge" },
              multiplicity: { value: 1, source: "capability:single_point_energy.defaults.multiplicity" },
              solvent: { value: "none", source: "capability:single_point_energy.defaults.solvent" },
            },
            limitations: [
              "演示数据：fixture 入口，不代表真实平台提交",
              "单点能不含几何优化与热校正",
            ],
          },
          {
            question_id: "geometry_optimization",
            label: "几何优化",
            workflow: "optimize",
            method_schema_id: "dft_optimize",
            level_id: "optimize",
            engine: "orca",
            basis: "",
            defaults: {
              method: { value: "r2SCAN-3c", source: "capability:geometry_optimization.defaults.method" },
              charge: { value: 0, source: "capability:geometry_optimization.defaults.charge" },
              multiplicity: { value: 1, source: "capability:geometry_optimization.defaults.multiplicity" },
              solvent: { value: "none", source: "capability:geometry_optimization.defaults.solvent" },
            },
            limitations: ["演示数据：fixture 入口，不代表真实平台提交"],
          },
          {
            question_id: "frequency_analysis",
            label: "频率分析",
            workflow: "frequency",
            method_schema_id: "dft_frequency",
            level_id: "frequency",
            engine: "orca",
            basis: "",
            defaults: {
              method: { value: "r2SCAN-3c", source: "capability:frequency_analysis.defaults.method" },
              charge: { value: 0, source: "capability:frequency_analysis.defaults.charge" },
              multiplicity: { value: 1, source: "capability:frequency_analysis.defaults.multiplicity" },
              solvent: { value: "none", source: "capability:frequency_analysis.defaults.solvent" },
            },
            limitations: ["演示数据：fixture 入口，不代表真实平台提交"],
          },
        ],
      },
    },
    workflowCatalog: [
      { id: "singlepoint", label: "Singlepoint", status: "active", visible: true, method_schema_id: "dft_singlepoint" },
      { id: "optimize", label: "Optimize", status: "active", visible: true, method_schema_id: "dft_optimize" },
      { id: "frequency", label: "Frequency", status: "active", visible: true, method_schema_id: "dft_frequency" },
    ],
    structure: {
      name: "demo_water",
      xyz: "3\nwater demo\nO  0.000000  0.000000  0.117300\nH  0.000000  0.757200 -0.469200\nH  0.000000 -0.757200 -0.469200\n",
    },
    parsePreview: {
      ok: true,
      detected_format: "xyz",
      errors: [],
      warnings: [],
      structures: [{ name: "demo_water", atom_count: 3, charge: 0, multiplicity: 1 }],
    },
    submit: { job_id: "manual_demo_0001", attempt: 1 },
    preflight: {
      executed: false,
      metering_consumed: false,
      transfers_attempted: false,
      plan: { question_id: "single_point_energy", workflow: "singlepoint" },
      estimate: { feasible: true, queue_seconds: 60 },
    },
    view: {
      snapshot_revision: 1,
      project_id: DEMO_PROJECT_ID,
      task_ref: { job_id: "manual_demo_0001", attempt: 1 },
      manifest_generation: 1,
      artifacts: [
        { artifact_id: "manual_demo_0001/sp_output.log", role: "raw_output", generation: 1, availability: "unprepared", sha256: null, size_bytes: null },
        { artifact_id: "manual_demo_0001/structure_final.xyz", role: "structure", generation: 1, availability: "unprepared", sha256: null, size_bytes: null },
      ],
      claims: [],
      clarifications: [],
      read_only: true,
      not_authoritative_for_science: true,
      provenance: {
        source: "fixture:manual-browser-fixtures",
        producer_revision: "0000000000000000000000000000000000000000",
        produced_at: "2026-10-22T00:00:00Z",
        digest: "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      },
    },
  };

  var deps = {
    science: null,
    wizardEndpoints: null,
    submit: null,
    buildJobBody: null,
    catalog: null,
  };

  var state = {
    demo: false,
    discovered: false,
    phase: "idle",
    projectId: DEMO_PROJECT_ID,
    questions: [],
    question: null,
    defaults: { values: {}, sources: {} },
    structure: { name: "", xyz: "" },
    preview: null,
    preflight: { ran: false, ok: false, error: null, body: null },
    taskRef: null,
    detail: null,
    detailError: null,
    submitCalls: 0,
    status: "",
    // T-23 / C17a QuestionSpec binding: frozen P2 input range, conditions,
    // human confirmations/approximations and the input revision. Identity
    // values are never silently filled — absent stays null ("未指定").
    spec: {
      inputFormat: "structure_upload",
      inputFormatStatus: "accepted",
      conditions: { solvent: null, temperature: null, standardState: null },
      humanConfirmations: [],
      approximations: [],
      inputRevision: 1,
      reactionView: null,
      parseError: null,
    },
  };

  var domDoc = null;

  function init(next) {
    next = next || {};
    if (next.science) deps.science = next.science;
    if (next.wizardEndpoints) deps.wizardEndpoints = next.wizardEndpoints;
    if (typeof next.submit === "function") deps.submit = next.submit;
    if (typeof next.buildJobBody === "function") deps.buildJobBody = next.buildJobBody;
    if (typeof next.catalog === "function") deps.catalog = next.catalog;
    if (next.projectId) state.projectId = String(next.projectId);
    return getState();
  }

  function scope() {
    return { projectId: state.projectId || DEMO_PROJECT_ID, permission: "read" };
  }

  function tr(key, fallback, vars) {
    if (typeof t === "function") {
      var value = t(key, vars);
      if (value && value !== key) return value;
    }
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        fallback = fallback.split("{" + name + "}").join(String(vars[name]));
      });
    }
    return fallback;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .split("&").join("&amp;")
      .split("<").join("&lt;")
      .split(">").join("&gt;")
      .split('"').join("&quot;");
  }

  function findQuestion(questionId) {
    var found = null;
    state.questions.forEach(function (question) {
      if (!found && question && question.question_id === questionId) found = question;
    });
    return found;
  }

  function activeWorkflowEntries(catalog) {
    return (Array.isArray(catalog) ? catalog : []).filter(function (entry) {
      return !!entry && typeof entry.id === "string" && entry.status === "active";
    });
  }

  // ANTI #20: only catalog entries with status "active" can be selected.
  // A question pointing at a stale/retired id self-heals to the first active
  // entry; the retired id itself is never returned and never becomes a default.
  function resolveQuestionWorkflow(questionId, catalog) {
    var question = findQuestion(questionId);
    if (!question) return null;
    var entries = activeWorkflowEntries(catalog);
    var match = null;
    entries.forEach(function (entry) {
      if (!match && entry.id === question.workflow) match = entry;
    });
    return match || (entries.length ? entries[0] : null);
  }

  function workflowCatalog() {
    if (typeof deps.catalog === "function") {
      var injected = deps.catalog();
      if (Array.isArray(injected) && injected.length) return injected;
    }
    if (typeof workflowCatalogCache !== "undefined" && Array.isArray(workflowCatalogCache) && workflowCatalogCache.length) {
      return workflowCatalogCache;
    }
    return FIXTURES.workflowCatalog;
  }

  function defaultsFor(question) {
    var values = {};
    var sources = {};
    DEFAULT_FIELDS.forEach(function (field) {
      var def = ((question && question.defaults) || {})[field] || {};
      values[field] = def.value;
      sources[field] = def.source || "";
    });
    return { values: values, sources: sources, originSources: Object.assign({}, sources) };
  }

  function sourceLineFor(field) {
    var sources = state.defaults.sources || {};
    return tr("manual.default_source", "来源：{source}", { source: sources[field] || "-" });
  }

  function sourceLineHtml(field) {
    return escapeHtml(sourceLineFor(field))
      .replace(/([:.])(?=[A-Za-z0-9_])/g, "$1<wbr>");
  }

  function fieldSpecs() {
    var values = state.defaults.values;
    var sources = state.defaults.sources;
    return {
      method: {
        label: tr("manual.method", "方法"), required: true, visible: true,
        value: values.method, source: sources.method,
      },
      charge: {
        label: tr("manual.charge", "电荷"), required: true, visible: true,
        value: values.charge, source: sources.charge,
      },
      multiplicity: {
        label: tr("manual.multiplicity", "自旋多重度"), required: true, visible: true,
        value: values.multiplicity, source: sources.multiplicity,
      },
      solvent: {
        label: tr("manual.solvent", "溶剂"), required: true, visible: true,
        value: values.solvent, source: sources.solvent,
      },
      temperature: {
        label: tr("manual.temperature", "温度"), required: false, visible: true,
        value: values.temperature, source: sources.temperature,
      },
      standardState: {
        label: tr("manual.standard_state", "标准态"), required: false, visible: true,
        value: values.standardState, source: sources.standardState,
      },
    };
  }

  function isIntLike(value, minimum) {
    var number = Number(value);
    return isFinite(number) && Math.floor(number) === number && number >= minimum;
  }

  function canSubmit() {
    if (!state.preflight.ok) return false;
    if (!state.question) return false;
    if (!resolveQuestionWorkflow(state.question.question_id, workflowCatalog())) return false;
    if (!state.structure.xyz) return false;
    if (state.spec.inputFormatStatus === "DEFERRED") return false;
    if (state.spec.parseError) return false;
    if (reactionViewParseErrors().length) return false;
    if (openReactionClarifications().length) return false;
    if (state.defaults.values.method == null || state.defaults.values.method === "") return false;
    if (!isIntLike(state.defaults.values.charge, -1000)) return false;
    if (!isIntLike(state.defaults.values.multiplicity, 1)) return false;
    return true;
  }

  function adoptCapabilities(body, demo) {
    var contracts = (window.ACPWorkbench && ACPWorkbench.contracts && ACPWorkbench.contracts.experience) || null;
    if (contracts && typeof contracts.validateCapabilities === "function") {
      var check = contracts.validateCapabilities(body);
      if (!check.ok) {
        throw new Error("science-question: capabilities rejected: " + check.problems.join("; "));
      }
    }
    state.demo = demo === true;
    state.questions = ((body && body.capabilities && body.capabilities.questions) || []).slice();
    state.discovered = true;
    state.phase = "ready";
    selectQuestion(state.questions.length ? state.questions[0].question_id : null);
    render();
    return getState();
  }

  function discover() {
    state.phase = "discovering";
    state.status = tr("manual.discover_running", "能力发现中…");
    render();
    var attempt;
    if (!deps.science || typeof deps.science.fetchCapabilities !== "function") {
      attempt = Promise.reject(new Error("science-question: init({science}) required"));
    } else {
      attempt = deps.science.fetchCapabilities(scope(), { generation: 0 });
    }
    return attempt.then(
      function (body) {
        var fixture = !!(body && body.capabilities && body.capabilities.fixture === true);
        return adoptCapabilities(body, fixture);
      },
      function () {
        return adoptCapabilities(FIXTURES.capabilities, true);
      }
    );
  }

  function selectQuestion(questionId) {
    var question = questionId ? findQuestion(questionId) : null;
    state.question = question;
    state.defaults = question
      ? defaultsFor(question)
      : { values: {}, sources: {}, originSources: {} };
    state.preflight = { ran: false, ok: false, error: null, body: null };
    render();
    return getState();
  }

  function setValue(field, value) {
    if (DEFAULT_FIELDS.indexOf(field) === -1) return getState();
    state.defaults.values[field] = value;
    if (!state.defaults.originSources) state.defaults.originSources = {};
    if (!(field in state.defaults.originSources)) {
      state.defaults.originSources[field] = state.defaults.sources[field] || "";
    }
    var origin = state.defaults.originSources[field];
    state.defaults.sources[field] = origin
      ? tr("manual.source_user_edit_from", "用户输入（已修改，原：{source}）", { source: origin })
      : tr("manual.source_user_edit", "用户输入（已修改）");
    var srcEl = domDoc ? domDoc.getElementById("sq-source-" + field) : null;
    if (srcEl) srcEl.innerHTML = sourceLineHtml(field);
    if (field === "solvent") state.spec.conditions.solvent = value;
    if (field === "temperature") state.spec.conditions.temperature = value;
    if (field === "standardState") state.spec.conditions.standardState = value;
    state.spec.inputRevision += 1;
    state.preflight = { ran: false, ok: false, error: null, body: null };
    var submitEl = domDoc ? domDoc.getElementById("sq-submit") : null;
    if (submitEl) submitEl.disabled = true;
    return getState();
  }

  function setStructure(structure) {
    structure = structure || {};
    state.structure = {
      name: String(structure.name || ""),
      xyz: String(structure.xyz || structure.xyzText || ""),
    };
    state.preview = null;
    state.spec.parseError = null;
    state.preflight = { ran: false, ok: false, error: null, body: null };
    render();
    return getState();
  }

  function loadDemoStructure() {
    return setStructure(FIXTURES.structure);
  }

  function classifyInputFormat(format) {
    if (typeof window.classifyReactionInputFormat === "function") {
      return window.classifyReactionInputFormat(format);
    }
    var key = String(format == null ? "" : format).toLowerCase();
    if (key === "cdxml") {
      return {
        format: key,
        status: "DEFERRED",
        supported: false,
        claim: "not_supported_until_A-req-02_and_B-req-09",
      };
    }
    return { format: key, status: "unverified", supported: false, claim: "p2_policy_module_absent" };
  }

  function setInputFormat(format) {
    var classification = classifyInputFormat(format);
    state.spec.inputFormat = classification.format;
    state.spec.inputFormatStatus = classification.status;
    if (classification.status === "DEFERRED") state.preview = null;
    state.spec.inputRevision += 1;
    state.preflight = { ran: false, ok: false, error: null, body: null };
    render();
    return getState();
  }

  function recordHumanConfirmation(item) {
    item = item || {};
    var record = {
      item: item.item != null ? String(item.item) : "",
      value: item.value != null ? String(item.value) : "",
      note: item.note != null ? String(item.note) : "",
      inputRevision: state.spec.inputRevision,
    };
    state.spec.humanConfirmations.push(record);
    state.spec.inputRevision += 1;
    render();
    return record;
  }

  function recordApproximation(item) {
    item = item || {};
    var record = {
      item: item.item != null ? String(item.item) : "",
      approximation: item.approximation != null ? String(item.approximation) : "",
      note: item.note != null ? String(item.note) : "",
      inputRevision: state.spec.inputRevision,
    };
    state.spec.approximations.push(record);
    state.spec.inputRevision += 1;
    render();
    return record;
  }

  function setReactionSpecView(view) {
    state.spec.reactionView = view ? Object.assign({}, view) : null;
    render();
    return getState();
  }

  function reactionViewParseErrors() {
    var view = state.spec.reactionView;
    return (view && Array.isArray(view.parseErrors)) ? view.parseErrors.slice() : [];
  }

  // A clarification is only ever closed by a VISIBLE user decision. A
  // resolved item whose confirmation record is omitted or whose approximation
  // was hidden is treated as open again — hidden approximations never unblock
  // submit (T-24).
  function openReactionClarifications() {
    var view = state.spec.reactionView;
    if (!view || !Array.isArray(view.clarifications)) return [];
    var confirmations = Array.isArray(view.humanConfirmations) ? view.humanConfirmations : [];
    var approximations = Array.isArray(view.approximations) ? view.approximations : [];
    return view.clarifications.filter(function (item) {
      if (!item) return false;
      if (item.status === "open" || item.answer == null) return true;
      if (item.status === "waived") {
        return !approximations.some(function (record) {
          return record && record.item === item.field_path && record.approximation != null && String(record.approximation) !== "";
        });
      }
      return !confirmations.some(function (record) {
        return record && record.item === item.field_path && record.value != null && String(record.value) !== "";
      });
    });
  }

  // Proposal payload writer for the question side: delegates to the single
  // frozen-shape builder in reaction-builder (no parallel shape). The merged
  // records bag carries both the question-side and the bound reaction-side
  // confirmations/approximations.
  function proposalInput(base) {
    if (typeof window.buildReactionSpecProposalInput !== "function") {
      throw new Error("science-question: buildReactionSpecProposalInput (reaction-builder.js) required");
    }
    var view = state.spec.reactionView;
    var bag = {
      humanConfirmations: state.spec.humanConfirmations.concat((view && view.humanConfirmations) || []),
      approximations: state.spec.approximations.concat((view && view.approximations) || []),
      inputRevision: state.spec.inputRevision,
      reactionInputRevision: view && view.inputRevision != null ? view.inputRevision : null,
    };
    return window.buildReactionSpecProposalInput(bag, base);
  }

  function previewStructure() {
    if (state.spec.inputFormatStatus === "DEFERRED") {
      return Promise.reject(new Error(
        "science-question: input format " + state.spec.inputFormat +
        " is DEFERRED (A-req-02 / B-req-09) — never parsed into a structure"
      ));
    }
    if (!deps.wizardEndpoints || typeof deps.wizardEndpoints.parseStructure !== "function") {
      return Promise.reject(new Error("science-question: init({wizardEndpoints}) required"));
    }
    return Promise.resolve(deps.wizardEndpoints.parseStructure({
      content: state.structure.xyz,
      format: "xyz",
      filename: (state.structure.name || "manual_input") + ".xyz",
    })).then(function (body) {
      var first = body && Array.isArray(body.structures) ? body.structures[0] : null;
      var errors = (body && Array.isArray(body.errors)) ? body.errors.slice() : [];
      var atomCount = first && first.atom_count != null ? first.atom_count : null;
      var parseFailed = !(body && body.ok === true) || errors.length > 0 || atomCount == null || Number(atomCount) === 0;
      if (parseFailed) {
        // A parse failure is surfaced as an ERROR and blocks submit. It must
        // never degrade into an empty structure (T-24).
        var message = errors.length ? errors.join("; ") : "structure parse failed (no atoms)";
        state.spec.parseError = { message: message, errors: errors };
        state.preview = {
          ok: false, atomCount: null, moleculeName: null, charge: null, multiplicity: null,
        };
        state.status = tr("manual.parse_failed", "结构解析失败：{message}", { message: message });
        render();
        return state.preview;
      }
      state.spec.parseError = null;
      state.preview = {
        ok: true,
        atomCount: atomCount,
        moleculeName: first && first.name ? first.name : null,
        charge: first && first.charge != null ? first.charge : null,
        multiplicity: first && first.multiplicity != null ? first.multiplicity : null,
      };
      render();
      return state.preview;
    });
  }

  // Preflight plan: the discovery/preflight request document, NOT a job
  // payload. The only job payload in this feature is built by the wizard
  // envelope builder at submit time.
  function preflightPlan() {
    var entry = state.question
      ? resolveQuestionWorkflow(state.question.question_id, workflowCatalog())
      : null;
    return {
      question_id: state.question ? state.question.question_id : null,
      workflow: entry ? entry.id : null,
      input: {
        source_type: "xyz_text",
        charge: Number(state.defaults.values.charge),
        multiplicity: Number(state.defaults.values.multiplicity),
      },
      method: {
        method: state.defaults.values.method,
        solvent: state.defaults.values.solvent,
      },
    };
  }

  function runPreflight() {
    state.preflight = { ran: true, ok: false, error: null, body: null };
    state.status = tr("manual.preflight_running", "预检中…");
    render();
    return Promise.resolve()
      .then(function () {
        if (!deps.science || typeof deps.science.preflight !== "function") {
          throw new Error("science-question: init({science}) required");
        }
        return deps.science.preflight(preflightPlan(), scope(), {
          identity: "manual-" + (state.question ? state.question.question_id : "plan"),
        });
      })
      .then(function (body) {
        var feasible = !(body && body.estimate && body.estimate.feasible === false);
        var cause = feasible
          ? null
          : String((body && body.estimate && body.estimate.reason) || "estimate.feasible=false");
        state.preflight = {
          ran: true,
          ok: feasible,
          error: cause,
          body: body || null,
        };
        state.status = feasible
          ? tr("manual.preflight_ok", "预检通过，可以提交")
          : tr("manual.preflight_failed", "预检失败，提交已禁用");
        render();
        return state.preflight;
      })
      .catch(function (error) {
        state.preflight = {
          ran: true,
          ok: false,
          error: String((error && error.message) || error),
          body: null,
        };
        state.status = tr("manual.preflight_failed", "预检失败，提交已禁用");
        render();
        return state.preflight;
      });
  }

  function jobBodySpec(entry) {
    var question = state.question;
    var solvent = String(
      state.defaults.values.solvent == null ? "none" : state.defaults.values.solvent
    );
    var level = {};
    level[question.level_id] = {
      engine: question.engine,
      functional: String(state.defaults.values.method),
      basis: question.basis || "",
      solvent_model: solvent === "none" ? "none" : "cpcm",
      solvent: solvent === "none" ? "" : solvent,
    };
    return {
      workflow: entry.id,
      moleculeName: state.structure.name || "manual_input",
      remark: "manual-research",
      input: {
        source_type: "xyz_text",
        source: state.structure.xyz,
        charge: Number(state.defaults.values.charge),
        multiplicity: Number(state.defaults.values.multiplicity),
      },
      method: {
        schema_id: entry.method_schema_id || question.method_schema_id || "",
        profile_id: "manual-research",
        levels: level,
      },
      resources: {
        nproc: 4,
        mem: "4GB",
        parallelism: 1,
        batch_id: "manual_batch",
        batch_index: 0,
        batch_total: 1,
      },
      projectId: scope().projectId,
      taskName: "",
    };
  }

  function submit() {
    if (!canSubmit()) {
      return Promise.reject(new Error("science-question: submit disabled (preflight or required fields)"));
    }
    if (typeof deps.buildJobBody !== "function" || typeof deps.submit !== "function") {
      return Promise.reject(new Error("science-question: init({buildJobBody, submit}) required"));
    }
    var entry = resolveQuestionWorkflow(state.question.question_id, workflowCatalog());
    var body = deps.buildJobBody(jobBodySpec(entry));
    state.submitCalls += 1;
    return Promise.resolve(deps.submit(body)).then(function (response) {
      state.taskRef = {
        job_id: response && (response.job_id != null ? response.job_id : response.id),
        attempt: (response && response.attempt) || 1,
      };
      state.phase = "submitted";
      state.status = tr("manual.submitted", "已提交，任务详情如下");
      render();
      return openTaskDetail();
    });
  }

  function limitationList() {
    var items = ((state.question && state.question.limitations) || []).slice();
    if (state.demo) {
      items.unshift(tr("manual.demo_notice", "演示数据：fixture 提交，不代表真实平台任务"));
    }
    return items;
  }

  // Read-only detail of the SAME (job_id, attempt) the submit returned.
  function openTaskDetail() {
    if (!state.taskRef) return Promise.resolve(null);
    return Promise.resolve()
      .then(function () {
        if (!deps.science || typeof deps.science.fetchView !== "function") {
          throw new Error("science-question: init({science}) required");
        }
        return deps.science.fetchView(state.taskRef, scope(), { generation: 0 });
      })
      .then(function (view) {
        state.detail = {
          taskRef: { job_id: state.taskRef.job_id, attempt: state.taskRef.attempt },
          structure: {
            name: state.structure.name || "",
            charge: state.defaults.values.charge,
            multiplicity: state.defaults.values.multiplicity,
            method: state.defaults.values.method,
            solvent: state.defaults.values.solvent,
          },
          artifacts: (view && Array.isArray(view.artifacts) ? view.artifacts : []).map(function (item) {
            return {
              artifact_id: item.artifact_id,
              role: item.role,
              generation: item.generation,
              availability: item.availability,
              sha256: item.sha256 == null ? null : item.sha256,
            };
          }),
          limitations: limitationList(),
        };
        state.detailError = null;
        render();
        return state.detail;
      })
      .catch(function (error) {
        state.detailError = String((error && error.message) || error);
        render();
        return null;
      });
  }

  function getState() {
    return {
      demo: state.demo,
      discovered: state.discovered,
      phase: state.phase,
      projectId: state.projectId,
      question: state.question
        ? { question_id: state.question.question_id, label: state.question.label, workflow: state.question.workflow }
        : null,
      defaults: {
        values: Object.assign({}, state.defaults.values),
        sources: Object.assign({}, state.defaults.sources),
      },
      fields: fieldSpecs(),
      structure: Object.assign({}, state.structure),
      preview: state.preview ? Object.assign({}, state.preview) : null,
      preflight: {
        ran: state.preflight.ran,
        ok: state.preflight.ok,
        error: state.preflight.error,
      },
      submitEnabled: canSubmit(),
      submitCalls: state.submitCalls,
      taskRef: state.taskRef ? Object.assign({}, state.taskRef) : null,
      detail: state.detail
        ? {
          taskRef: Object.assign({}, state.detail.taskRef),
          structure: Object.assign({}, state.detail.structure),
          artifacts: state.detail.artifacts.map(function (item) { return Object.assign({}, item); }),
          limitations: state.detail.limitations.slice(),
        }
        : null,
      detailError: state.detailError,
      status: state.status,
      spec: {
        inputFormat: state.spec.inputFormat,
        inputFormatStatus: state.spec.inputFormatStatus,
        conditions: Object.assign({}, state.spec.conditions),
        humanConfirmations: state.spec.humanConfirmations.map(function (item) { return Object.assign({}, item); }),
        approximations: state.spec.approximations.map(function (item) { return Object.assign({}, item); }),
        inputRevision: state.spec.inputRevision,
        reactionView: state.spec.reactionView ? Object.assign({}, state.spec.reactionView) : null,
        parseError: state.spec.parseError ? Object.assign({}, state.spec.parseError) : null,
        openClarifications: openReactionClarifications().map(function (item) { return Object.assign({}, item); }),
      },
    };
  }

  function demoBadgeVisible() {
    return state.demo === true;
  }

  // ── DOM rendering (mounted panel in ACP_Workbench_v2.html) ─────────────
  function render() {
    if (!domDoc) return;
    var badge = domDoc.getElementById("science-question-demo-badge");
    if (badge) {
      badge.textContent = DEMO_BADGE_TEXT;
      if (state.demo) badge.removeAttribute("hidden");
      else badge.setAttribute("hidden", "hidden");
    }
    var root = domDoc.getElementById("science-question-body");
    if (!root) return;
    root.innerHTML = renderForm();
    wireForm();
  }

  function renderSpecSummary() {
    var spec = state.spec;
    var view = spec.reactionView;
    var e = escapeHtml;
    function unspecified(value) {
      return value == null ? tr("manual.unspecified", "未指定 (unspecified)") : String(value);
    }
    var html = '<div class="science-question-section" id="sq-spec-title">' +
      tr("manual.spec_title", "反应/问题规格 (QuestionSpec / ReactionSpec)") + "</div>";
    html += '<div class="science-question-note" id="sq-spec-input-policy">' +
      e(tr("manual.input_policy", "P2 输入范围 (frozen): MOL / SDF / SMILES + 结构上传；CDXML: DEFERRED（未交付，不支持 ChemDraw 起点）")) +
      " · " + e(tr("manual.input_format", "当前输入格式: {format} ({status})", {
        format: spec.inputFormat, status: spec.inputFormatStatus,
      })) + "</div>";
    if (view) {
      html += '<div class="science-question-note" id="sq-spec-kind">' +
        e(tr("manual.reaction_kind", "反应类型: {kind}", { kind: view.kindLabel || view.kind || "-" })) +
        " · " + e(view.kindDistinction || "") + "</div>";
      var steps = Array.isArray(view.candidateSteps) ? view.candidateSteps : [];
      html += '<div class="science-question-note" id="sq-spec-steps">' +
        e(tr("manual.candidate_steps", "候选基元步骤 (candidate elementary steps): {count}", { count: steps.length })) +
        "</div>";
      var mapping = view.atomMapping || { status: "-", stale: false };
      html += '<div class="science-question-note" id="sq-spec-mapping">' +
        e(tr("manual.atom_mapping", "原子映射: {status}", { status: mapping.status })) +
        (mapping.stale ? " · STALE" : "") + "</div>";
      html += '<div class="science-question-note" id="sq-spec-charge-spin">' +
        e(tr("manual.charge_spin", "电荷/自旋 (charge/spin): ")) +
        e((view.chargeSpin || []).map(function (item) {
          return (item.ref || item.label || "?") + "=" + unspecified(item.charge) + "/" + unspecified(item.multiplicity);
        }).join(" · ") || "-") + "</div>";
      html += '<div class="science-question-note" id="sq-spec-stereo">' +
        e(tr("manual.stereo", "立体 (stereo): ")) +
        e((view.stereo || []).map(function (item) {
          return (item.ref || item.label || "?") + "=" + unspecified(item.stereo);
        }).join(" · ") || "-") + "</div>";
    }
    html += '<div class="science-question-note" id="sq-spec-conditions">' +
      e(tr("manual.conditions", "溶剂/温度/标准态 (solvent/temperature/standard state): ")) +
      e("solvent " + unspecified(spec.conditions.solvent) +
        " · temperature " + unspecified(spec.conditions.temperature) +
        " · standard state " + unspecified(spec.conditions.standardState)) + "</div>";
    var confirmations = spec.humanConfirmations.map(function (item) {
      return "confirmed " + item.item + "=" + item.value + " @rev " + item.inputRevision;
    }).concat(spec.approximations.map(function (item) {
      return "approximation " + item.item + "=" + item.approximation + " @rev " + item.inputRevision;
    }));
    if (view) {
      confirmations = confirmations.concat((view.humanConfirmations || []).map(function (item) {
        return "confirmed " + item.item + "=" + item.value + " @rev " + item.inputRevision;
      })).concat((view.approximations || []).map(function (item) {
        return "approximation " + item.item + "=" + item.approximation + " @rev " + item.inputRevision;
      }));
    }
    html += '<div class="science-question-note" id="sq-spec-confirmations">' +
      e(tr("manual.confirmations", "人工确认/近似 (human confirmations/approximations): ")) +
      e(confirmations.join(" · ") || tr("manual.none", "无")) + "</div>";
    var clarifications = (view && Array.isArray(view.clarifications)) ? view.clarifications : [];
    html += '<div class="science-question-note" id="sq-spec-clarifications">' +
      e(tr("manual.clarifications", "化学歧义澄清 (chemistry clarifications): ")) +
      e(clarifications.map(function (item) {
        return item.field_path + "=" + item.status + (item.answer == null ? "" : " (" + item.answer + ")");
      }).join(" · ") || tr("manual.none", "无")) + "</div>";
    if (spec.parseError) {
      html += '<div class="science-question-err" id="sq-parse-error">' +
        e(tr("manual.parse_error", "解析错误 (parse error): {message}", {
          message: spec.parseError.message,
        })) + "</div>";
    }
    html += '<div class="science-question-note" id="sq-spec-input-revision">' +
      e(tr("manual.input_revision", "输入版本 (input revision): {revision}", {
        revision: spec.inputRevision,
      })) + "</div>";
    return html;
  }

  function renderForm() {
    var question = state.question;
    var fields = fieldSpecs();
    var options = state.questions.map(function (item) {
      var selected = question && item.question_id === question.question_id ? " selected" : "";
      return '<option value="' + escapeHtml(item.question_id) + '"' + selected + ">" +
        escapeHtml(item.label || item.question_id) + "</option>";
    }).join("");
    var fieldRow = function (id, spec, extra) {
      return '<div class="science-question-row" id="sq-row-' + id + '">' +
        '<label class="science-question-label" for="sq-' + id + '">' + escapeHtml(spec.label) +
        (spec.required ? ' <span class="science-question-required">*</span>' : "") + "</label>" +
        (extra || "") +
        '<div class="science-question-source" id="sq-source-' + id + '">' +
        sourceLineHtml(id) +
        "</div></div>";
    };
    var detailHtml = "";
    if (state.taskRef) {
      var detail = state.detail;
      var artifacts = (detail ? detail.artifacts : []).map(function (item) {
        return "<li>" + escapeHtml(item.artifact_id) + " — " + escapeHtml(item.role) +
          " [" + escapeHtml(item.availability) + "]" +
          (item.sha256 ? " sha256:" + escapeHtml(item.sha256) : "") + "</li>";
      }).join("");
      var limits = (detail ? detail.limitations : limitationList()).map(function (item) {
        return "<li>" + escapeHtml(item) + "</li>";
      }).join("");
      detailHtml = '<div class="science-question-detail" id="sq-detail">' +
        '<h3 id="sq-detail-title">' + escapeHtml(tr("manual.detail_title", "任务详情（只读）")) + "</h3>" +
        '<div id="sq-detail-ref">' + escapeHtml(tr("manual.detail_task_ref",
          "任务引用：{job_id} · attempt {attempt}",
          { job_id: state.taskRef.job_id, attempt: state.taskRef.attempt })) + "</div>" +
        '<div id="sq-detail-structure">' + escapeHtml(tr("manual.detail_structure", "结构与参数")) + ": " +
        escapeHtml((detail && detail.structure && detail.structure.name) || state.structure.name || "-") +
        " · charge " + escapeHtml(state.defaults.values.charge) +
        " · multiplicity " + escapeHtml(state.defaults.values.multiplicity) +
        " · " + escapeHtml(state.defaults.values.method) + "</div>" +
        '<div class="science-question-block" id="sq-detail-artifacts"><h4>' +
        escapeHtml(tr("manual.detail_artifacts", "原始工件引用")) + "</h4><ul>" + artifacts + "</ul></div>" +
        '<div class="science-question-block" id="sq-detail-limitations"><h4>' +
        escapeHtml(tr("manual.detail_limitations", "限制")) + "</h4><ul>" + limits + "</ul></div>" +
        "</div>";
      if (state.detailError) {
        detailHtml += '<div class="science-question-err" id="sq-detail-error">' +
          escapeHtml(state.detailError) + "</div>";
      }
    }
    return (
      '<div class="science-question-note" id="sq-no-model-note">' +
      escapeHtml(tr("manual.no_model_note", "本入口不依赖模型配置")) + "</div>" +
      '<div class="science-question-row" id="sq-row-question">' +
      '<label class="science-question-label" for="sq-question">' +
      escapeHtml(tr("manual.question", "科学问题")) + "</label>" +
      '<select id="sq-question" class="modal-select">' + options + "</select></div>" +
      '<div class="science-question-row" id="sq-row-structure">' +
      '<label class="science-question-label" for="sq-structure">' +
      escapeHtml(tr("manual.structure", "输入结构 (XYZ)")) + "</label>" +
      '<textarea id="sq-structure" class="modal-input" rows="10" spellcheck="false">' +
      escapeHtml(state.structure.xyz) + "</textarea>" +
      '<div class="science-question-hint">' +
      escapeHtml(tr("manual.structure_hint", "粘贴 XYZ 文本，或使用演示结构")) + "</div>" +
      '<div class="science-question-actions">' +
      '<button type="button" class="btn ghost" id="sq-load-demo">' +
      escapeHtml(tr("manual.load_demo", "载入演示结构")) + "</button>" +
      '<button type="button" class="btn ghost" id="sq-preview">' +
      escapeHtml(tr("manual.preview", "预览结构")) + "</button></div>" +
      '<div class="science-question-source" id="sq-preview-state">' +
      (state.preview
        ? escapeHtml(state.preview.ok
          ? (state.preview.moleculeName || "structure") + " · atoms " + state.preview.atomCount
          : tr("manual.preview_failed", "结构预览失败"))
        : "") + "</div></div>" +
      '<div class="science-question-section" id="sq-defaults">' +
      escapeHtml(tr("manual.defaults", "可解释默认值")) + "</div>" +
      fieldRow("method", fields.method,
        '<input id="sq-method" class="modal-input" type="text" required value="' +
        escapeHtml(fields.method.value) + '">') +
      fieldRow("charge", fields.charge,
        '<input id="sq-charge" class="modal-input" type="number" step="1" required value="' +
        escapeHtml(fields.charge.value) + '">') +
      fieldRow("multiplicity", fields.multiplicity,
        '<input id="sq-multiplicity" class="modal-input" type="number" step="1" min="1" required value="' +
        escapeHtml(fields.multiplicity.value) + '">') +
      fieldRow("solvent", fields.solvent,
        '<input id="sq-solvent" class="modal-input" type="text" required value="' +
        escapeHtml(fields.solvent.value) + '">') +
      fieldRow("temperature", fields.temperature,
        '<input id="sq-temperature" class="modal-input" type="text" value="' +
        escapeHtml(fields.temperature.value == null ? "" : fields.temperature.value) + '">') +
      fieldRow("standardState", fields.standardState,
        '<input id="sq-standardState" class="modal-input" type="text" value="' +
        escapeHtml(fields.standardState.value == null ? "" : fields.standardState.value) + '">') +
      renderSpecSummary() +
      '<div class="science-question-actions" id="sq-actions">' +
      '<button type="button" class="btn ghost" id="sq-discover">' +
      escapeHtml(tr("manual.discover", "发现能力")) + "</button>" +
      '<button type="button" class="btn ghost" id="sq-preflight">' +
      escapeHtml(tr("manual.preflight", "预检")) + "</button>" +
      '<button type="button" class="btn primary" id="sq-submit"' +
      (canSubmit() ? "" : " disabled") + ">" +
      escapeHtml(tr("manual.submit", "提交任务")) + "</button></div>" +
      '<div class="science-question-status" id="sq-status">' + escapeHtml(state.status) +
      (state.preflight.ran && !state.preflight.ok
        ? (state.preflight.error
          ? '<div class="science-question-status-cause" id="sq-status-cause">' +
            escapeHtml(tr("manual.preflight_cause", "原因：{reason}", { reason: state.preflight.error })) +
            "</div>"
          : "") +
          '<button type="button" class="btn ghost" id="sq-preflight-retry">' +
          escapeHtml(tr("manual.preflight_retry", "重试预检")) + "</button>"
        : "") +
      "</div>" +
      detailHtml
    );
  }

  function wireForm() {
    function on(id, event, handler) {
      var el = domDoc.getElementById(id);
      if (el) el.addEventListener(event, handler);
    }
    on("sq-question", "change", function (event) {
      selectQuestion(event.target.value);
    });
    on("sq-structure", "input", function (event) {
      state.structure.xyz = String(event.target.value || "");
      state.preflight = { ran: false, ok: false, error: null, body: null };
      var submitEl = domDoc.getElementById("sq-submit");
      if (submitEl) submitEl.disabled = true;
    });
    ["method", "charge", "multiplicity", "solvent", "temperature", "standardState"].forEach(function (field) {
      on("sq-" + field, "input", function (event) {
        setValue(field, event.target.value);
      });
    });
    on("sq-load-demo", "click", function () {
      loadDemoStructure();
    });
    on("sq-preview", "click", function () {
      previewStructure().catch(function (error) {
        state.preview = { ok: false, atomCount: null, moleculeName: null, charge: null, multiplicity: null };
        state.status = String((error && error.message) || error);
        render();
      });
    });
    on("sq-discover", "click", function () {
      discover();
    });
    on("sq-preflight", "click", function () {
      return runPreflight();
    });
    on("sq-preflight-retry", "click", function () {
      return runPreflight();
    });
    on("sq-submit", "click", function () {
      submit().catch(function (error) {
        state.status = String((error && error.message) || error);
        render();
      });
    });
  }

  function mount(doc, options) {
    options = options || {};
    domDoc = doc || (typeof document !== "undefined" ? document : null);
    if (!domDoc) return getState();
    if (typeof deps.buildJobBody !== "function" &&
        window.ACPWizardSubmitEdit && typeof window.ACPWizardSubmitEdit.buildStructureJobBody === "function") {
      deps.buildJobBody = window.ACPWizardSubmitEdit.buildStructureJobBody;
    }
    if (!deps.science && window.ACPClient && window.ACPClient.science) {
      deps.science = window.ACPClient.science;
    }
    if (!deps.wizardEndpoints && window.ACPClient && window.ACPClient.wizardEndpoints) {
      deps.wizardEndpoints = window.ACPClient.wizardEndpoints;
    }
    if (typeof deps.submit !== "function" && typeof api === "function") {
      deps.submit = function (body) {
        return api("/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      };
    }
    render();
    if (options.autoDiscover !== false) {
      discover().catch(function (error) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("science-question discover failed:", error);
        }
      });
    }
    return getState();
  }

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("DOMContentLoaded", function () {
      try {
        if (window.ACPClient && window.ACPClient.science && typeof apiV2 === "function" && typeof api === "function") {
          ACPClient.science.init({ v1: api, v2: apiV2 });
        }
        mount(document);
      } catch (error) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("science-question mount skipped:", error);
        }
      }
    });
  }

  return {
    FIXTURES: FIXTURES,
    init: init,
    mount: mount,
    discover: discover,
    selectQuestion: selectQuestion,
    setValue: setValue,
    setStructure: setStructure,
    loadDemoStructure: loadDemoStructure,
    previewStructure: previewStructure,
    runPreflight: runPreflight,
    canSubmit: canSubmit,
    submit: submit,
    openTaskDetail: openTaskDetail,
    resolveQuestionWorkflow: resolveQuestionWorkflow,
    preflightPlan: preflightPlan,
    jobBodySpec: jobBodySpec,
    getState: getState,
    demoBadgeVisible: demoBadgeVisible,
    render: render,
    classifyInputFormat: classifyInputFormat,
    setInputFormat: setInputFormat,
    recordHumanConfirmation: recordHumanConfirmation,
    recordApproximation: recordApproximation,
    setReactionSpecView: setReactionSpecView,
    openReactionClarifications: openReactionClarifications,
    reactionViewParseErrors: reactionViewParseErrors,
    proposalInput: proposalInput,
  };
})();

window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.features = ACPWorkbench.features || {};
ACPWorkbench.features.scienceQuestion = window.ACPScienceQuestion;
