/**
 * ACPJobEditor — 全任务「修改参数后重算」编辑链
 * (docs/ACP_Edit_And_Recalculate_Plan.md §8)。
 *
 * 职责：editorContext 管理、草稿加载（GET /jobs/{id}/edit-draft）、
 * 工作流适配 hydrate/serialize、差异展示、预览与提交
 * （POST /jobs/{id}/edit-recalculate/preview → /edit-recalculate）。
 *
 * 设计约束：
 * - 共用 Workbench 现有创建表单与参数收集函数（submitJobModal 的提交
 *   请求经 api() 拦截进入本模块，不复制一套 HTML 表单）。
 * - 打开/取消编辑不写数据库、不启动计算；创建模式与编辑模式切换必须
 *   完全重置（openModal 全量 reset 后再 hydrate）。
 * - 异步响应受 requestToken 保护，旧响应不得覆盖当前表单。
 */
(function () {
  "use strict";

  var editorContext = null;
  var tokenCounter = 0;
  var menuEl = null;

  function isActive() { return !!editorContext; }
  function currentMode() { return editorContext ? editorContext.mode : null; }
  function sourceJobId() { return editorContext ? editorContext.sourceJobId : ""; }

  function deepCopy(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function inputKind(inp) {
    if (!inp || typeof inp !== "object") return "plain";
    if (inp.scan_request && typeof inp.scan_request === "object") return "scan_request";
    var st = String(inp.source_type || "");
    if (st) return st;
    if (inp.source_job_id || inp.from_artifact) return "stage_artifact";
    return "structured";
  }

  // 原地可复用的"简单结构"输入：表单重建等价于原输入。
  function isSimpleStructureInput(inp) {
    var kind = inputKind(inp);
    return kind === "xyz_text" || kind === "smiles" || kind === "plain" ||
      kind === "structure_asset";
  }

  // passthrough 工作流：提交时输入以原 spec 覆盖，不经向导重建。
  function isPassthroughWorkflow(workflow, inp) {
    if (workflow === "PESsearch") return true;
    if (workflow === "BatchOptimize") return true;
    if (inputKind(inp) === "stage_artifact") return true;
    if (inputKind(inp) === "scan_request") return true;
    return false;
  }

  function cleanLevels(stages) {
    var out = {};
    Object.keys(stages || {}).forEach(function (lid) {
      var st = stages[lid];
      if (!st || st._disabled === true) return;
      out[lid] = deepCopy(st);
      delete out[lid]._disabled;
    });
    return out;
  }

  // ---------------------------------------------------------------------------
  // 入口：重算菜单（队列 ↻ 按钮与详情页共用）
  // ---------------------------------------------------------------------------

  function closeRerunMenu() {
    if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
    menuEl = null;
    document.removeEventListener("click", closeRerunMenu, true);
    document.removeEventListener("keydown", onMenuKey, true);
  }

  function onMenuKey(ev) {
    if (ev.key === "Escape") { closeRerunMenu(); return; }
    if (!menuEl) return;
    var focusables = Array.prototype.slice.call(menuEl.querySelectorAll("button"));
    if (!focusables.length) return;
    var idx = focusables.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      focusables[(idx + 1 + focusables.length) % focusables.length].focus();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      focusables[(idx - 1 + focusables.length) % focusables.length].focus();
    }
  }

  function openRerunMenu(job, anchor) {
    if (!job) return;
    closeRerunMenu();
    var jobId = String(job.id || job.job_id || "");
    var recovery = (typeof resolveJobRecovery === "function")
      ? resolveJobRecovery(job) : { can_rerun: true };
    menuEl = document.createElement("div");
    menuEl.className = "rerun-action-menu";
    menuEl.setAttribute("role", "menu");
    [
      {
        key: "edit.menu.rerun_direct",
        icon: "↻",
        enabled: !!recovery.can_rerun,
        reason: "edit.menu.rerun_direct_disabled",
        action: function () { closeRerunMenu(); rerunJob(jobId, null); },
      },
      {
        key: "edit.menu.edit_recalculate",
        icon: "✎",
        enabled: true,
        reason: "",
        action: function () { closeRerunMenu(); openJobEditor(job, "edit_recalculate"); },
      },
      {
        key: "edit.menu.new_from_job",
        icon: "⧉",
        enabled: true,
        reason: "",
        action: function () { closeRerunMenu(); openJobEditor(job, "new_from_job"); },
      },
    ].forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.innerHTML = '<span class="rerun-menu-icon">' + item.icon + "</span><span>" +
        t(item.key) + "</span>";
      if (!item.enabled) {
        btn.disabled = true;
        btn.title = item.reason ? t(item.reason) : "";
      } else {
        btn.addEventListener("click", function (ev) { ev.stopPropagation(); item.action(); });
      }
      menuEl.appendChild(btn);
    });
    var rect = anchor.getBoundingClientRect();
    menuEl.style.left = Math.max(8, Math.min(window.innerWidth - 230, rect.left)) + "px";
    menuEl.style.top = (rect.bottom + 4 + window.scrollY) + "px";
    menuEl.style.position = "absolute";
    document.body.appendChild(menuEl);
    // 菜单打开不触发卡片选择/展开：容器级捕获关闭 + stopPropagation。
    menuEl.addEventListener("click", function (ev) { ev.stopPropagation(); });
    setTimeout(function () {
      document.addEventListener("click", closeRerunMenu, true);
      document.addEventListener("keydown", onMenuKey, true);
      var first = menuEl.querySelector("button:not([disabled])");
      if (first) first.focus();
    }, 0);
  }

  // ---------------------------------------------------------------------------
  // 打开编辑器：openModal 全量重置 → 拉草稿 → hydrate
  // ---------------------------------------------------------------------------

  function openJobEditor(job, mode) {
    var jobId = String(job.id || job.job_id || "");
    var token = ++tokenCounter;
    editorContext = {
      mode: mode,
      sourceJobId: jobId,
      sourceJob: job,
      requestToken: token,
      draft: null,
      executionMode: mode === "new_from_job" ? "new_job" : "in_place",
      inputMode: "original",
      fingerprint: null,
      submitting: false,
      collecting: false,
      collectedBodies: null,
      pendingPreviewBody: null,
      itemIncludes: null,
    };
    openModal();
    renderBanner("loading");
    api("/jobs/" + encodeURIComponent(jobId) + "/edit-draft").then(function (draft) {
      if (!editorContext || editorContext.requestToken !== token) return;
      editorContext.draft = draft;
      hydrate(draft);
      renderBanner("ready");
      updateBannerState();
      startBadgeWatch();
    }, function (err) {
      if (!editorContext || editorContext.requestToken !== token) return;
      renderBanner("error", (err && err.message) || String(err));
    });
  }

  function cancelEditor(force) {
    if (!editorContext) return;
    if (!force && countLocalChanges() > 0) {
      if (!window.confirm(t("edit.discard_confirm"))) return;
    }
    editorContext = null;
    stopBadgeWatch();
    renderBanner("hidden");
    closeModal();
  }

  // ---------------------------------------------------------------------------
  // hydrate：把草稿回填进现有表单（restoring 后只做一次派生 UI 更新）
  // ---------------------------------------------------------------------------

  function hydrate(draft) {
    var spec = draft.editable_spec || {};
    var inp = spec.input || {};
    // 1) 工作流预选走现有 pendingNewTask 通道（含 catalog 自愈）。
    window._pendingNewTask = { workflow: spec.workflow, projectId: spec.project_id || "" };
    applyPendingNewTask();
    // 2) 方法档位与 levels（覆盖 openModal 载入的当前默认值）。
    var m = spec.method || {};
    if (m.profile_id || m.profile) {
      wizardState.method.profile_id = m.profile_id || m.profile;
    }
    if (m.levels && Object.keys(m.levels).length) {
      wizardState.method.stages = deepCopy(m.levels);
    }
    updateConfigCards();
    // 3) 表单公共字段。
    document.getElementById("modal-remark").value = spec.remark || "";
    document.getElementById("modal-task-name").value =
      spec.task_name && spec.task_name !== spec.workflow ? spec.task_name : "";
    var res = spec.resources || {};
    if (res.nproc) document.getElementById("modal-nproc").value = String(res.nproc);
    if (res.mem) document.getElementById("modal-mem").value = String(res.mem);
    if (res.parallelism && document.getElementById("modal-parallelism")) {
      document.getElementById("modal-parallelism").value = String(res.parallelism);
    }
    var charge = inp.charge, mult = inp.multiplicity;
    if (inputKind(inp) === "scan_request" && inp.scan_request && inp.scan_request.source) {
      charge = inp.scan_request.source.charge; mult = inp.scan_request.source.multiplicity;
    }
    if (charge !== undefined && charge !== null) document.getElementById("modal-charge").value = String(charge);
    if (mult !== undefined && mult !== null) document.getElementById("modal-mult").value = String(mult);
    // 4) 项目与节点。
    var ps = document.getElementById("modal-project-select");
    if (ps && spec.project_id) {
      var hasOption = Array.prototype.some.call(ps.options, function (o) { return o.value === spec.project_id; });
      if (hasOption) ps.value = spec.project_id;
    }
    if (spec.node_tags && spec.node_tags.length && typeof nodeMatchingState !== "undefined") {
      nodeMatchingState.selectedTags = spec.node_tags.slice();
    }
    // 5) 输入回填：单结构进结构框并解析（得到 3D 预览），复杂输入走摘要。
    hydrateInput(draft);
    // 6) NMR 实验谱（assigned 文本可恢复；bruker 资产失效要求替换）。
    if (spec.workflow === "nmr" && inp.experiment && inp.experiment.mode === "assigned" &&
        typeof nmrExperimentMode !== "undefined") {
      nmrExperimentMode = "assigned";
      var ta = document.getElementById("nmr-experiment-text");
      if (ta && inp.experiment.content) ta.value = String(inp.experiment.content);
    }
    // 7) 批量条目 include 状态。
    if (inp.items && Array.isArray(inp.items)) {
      editorContext.itemIncludes = inp.items.map(function () { return true; });
    }
  }

  function hydrateInput(draft) {
    var spec = draft.editable_spec || {};
    var inp = spec.input || {};
    var kind = inputKind(inp);
    var box = document.getElementById("modal-structure-input");
    var previewText = "";
    if (kind === "xyz_text" || kind === "plain") {
      previewText = String(inp.source || inp.input_artifact || inp.smiles || "");
    } else if (kind === "smiles") {
      previewText = String(inp.source || "");
    } else if (kind === "scan_request") {
      var src = (inp.scan_request && inp.scan_request.source) || {};
      previewText = String(src.xyz_text || src.source || "");
    } else if (kind === "batch_structures") {
      var items = Array.isArray(inp.items) ? inp.items : [];
      previewText = items.length ? String(items[0].xyz || "") : "";
    } else if (kind === "candidates") {
      var cands = Array.isArray(inp.candidates) ? inp.candidates : [];
      previewText = cands.length ? String(cands[0].source || "") : "";
    } else if (kind === "stage_artifact") {
      previewText = "";
    }
    if (previewText && box) {
      box.value = previewText;
      if (typeof scheduleParseStructures === "function") scheduleParseStructures();
    }
  }

  // ---------------------------------------------------------------------------
  // 序列化：以原 spec 为基座，叠加表单可编辑字段（复用页面收集函数）
  // ---------------------------------------------------------------------------

  function serializeFromCollectedBody(body) {
    var ctx = editorContext;
    var spec = ctx.draft.editable_spec;
    var workflow = spec.workflow;
    var method = deepCopy(spec.method || {});
    var stages = cleanLevels(wizardState.method.stages);
    if (Object.keys(stages).length) method.levels = stages;
    if (wizardState.method.profile_id) method.profile_id = wizardState.method.profile_id;
    if (workflow === "Confsearch") {
      var cs = (wizardState.method.stages || {}).confsearch || {};
      if (cs.protocol) method.protocol = cs.protocol;
      if (cs.confsearch_profile) method.profile = cs.confsearch_profile;
      if (cs.refinement_policy) method.refinement_policy = cs.refinement_policy;
      if (cs.ewin !== undefined && cs.ewin !== "") method.ewin = cs.ewin;
    }
    if (workflow === "BatchOptimize" && typeof applyBatchOptimizeMethodFields === "function") {
      applyBatchOptimizeMethodFields(method);
    }
    var out = {
      mode: ctx.executionMode,
      workflow: workflow,
      input: resolveInput(body),
      method: method,
      resources: sanitizeResources(body && body.resources),
      molecule_name: (body && body.molecule_name) || spec.molecule_name || "",
      task_name: (body && body.task_name) || "",
      remark: (body && body.remark) || "",
      tags: (body && body.tags) || [],
      request_id: newRequestId(),
      expected_source_revision: ctx.draft.source_revision,
    };
    if (ctx.executionMode === "new_job") {
      out.project_id = (body && body.project_id) || spec.project_id;
      if (body && body.name) out.name = String(body.name);
    }
    if (typeof applyNodeSelectionToBody === "function") applyNodeSelectionToBody(out);
    if (ctx.fingerprint) out.preview_fingerprint = ctx.fingerprint;
    return out;
  }

  function sanitizeResources(resources) {
    var r = deepCopy(resources) || {};
    delete r.batch_id;
    delete r.batch_index;
    delete r.batch_total;
    return r;
  }

  function resolveInput(body) {
    var ctx = editorContext;
    var spec = ctx.draft.editable_spec;
    var orig = spec.input || {};
    if (ctx.inputMode === "replace") return (body && body.input) || deepCopy(orig);
    if (ctx.inputMode === "last_structure") {
      var ls = (ctx.draft.input_refs || {}).last_structure;
      if (!ls || !ls.xyz_text) throw new Error(t("edit.last_structure_missing"));
      return {
        source_type: "xyz_text",
        source: ls.xyz_text,
        charge: orig.charge,
        multiplicity: orig.multiplicity,
        edit_input_origin: { mode: "last_structure", entry_id: ls.entry_id },
      };
    }
    if (isSimpleStructureInput(orig)) return (body && body.input) || deepCopy(orig);
    var overlay = deepCopy(orig);
    if (inputKind(orig) === "batch_structures" && Array.isArray(overlay.items) &&
        ctx.itemIncludes) {
      // 批量任务默认重算完整清单；显式勾选部分结构时不自动只取失败项。
      var kept = [];
      overlay.items.forEach(function (item, i) {
        var include = ctx.itemIncludes[i] !== false;
        if (include) {
          var copy = deepCopy(item);
          copy.include = true;
          kept.push(copy);
        }
      });
      if (!kept.length) throw new Error(t("edit.batch_items_empty"));
      overlay.items = kept;
    }
    return overlay;
  }

  function newRequestId() {
    return "ed_" + editorContext.sourceJobId + "_" + Date.now().toString(36) +
      "_" + Math.random().toString(36).slice(2, 8);
  }

  // ---------------------------------------------------------------------------
  // api() 拦截：编辑模式下 POST /jobs 进入收集/预览，而不是真创建
  // ---------------------------------------------------------------------------

  function interceptJobCreate(body) {
    var ctx = editorContext;
    if (!ctx || !ctx.collecting) {
      return Promise.reject(new Error(t("edit.create_blocked_in_editor")));
    }
    ctx.collectedBodies.push(deepCopy(body));
    return Promise.resolve({ job_id: "__edit_collected__", status: "collected", __editCollected: true });
  }

  // ---------------------------------------------------------------------------
  // 检查并提交 → 服务端预览 → 摘要确认 → 最终提交
  // ---------------------------------------------------------------------------

  function withModalHeld(fn) {
    var original = window.closeModal;
    window.closeModal = function () { /* held during collection */ };
    return Promise.resolve().then(fn).then(function (v) {
      window.closeModal = original;
      return v;
    }, function (e) {
      window.closeModal = original;
      throw e;
    });
  }

  function checkAndSubmit() {
    var ctx = editorContext;
    if (!ctx || ctx.submitting) return;
    if (!ctx.draft) return;
    ctx.collecting = true;
    ctx.collectedBodies = [];
    var submitButton = document.getElementById("edit-recalc-submit");
    if (submitButton) submitButton.disabled = true;
    withModalHeld(function () { return submitJobModal(); }).then(function () {
      ctx.collecting = false;
      if (!editorContext || editorContext.requestToken !== ctx.requestToken) return;
      if (!ctx.collectedBodies.length) {
        throw new Error(t("edit.no_payload_collected"));
      }
      if (ctx.collectedBodies.length > 1) {
        throw new Error(t("edit.multi_structure_not_supported"));
      }
      return runPreview(ctx.collectedBodies[0]);
    }).then(function (preview) {
      if (!editorContext || editorContext.requestToken !== ctx.requestToken) return;
      ctx.pendingPreviewBody = ctx.collectedBodies[0];
      ctx.fingerprint = preview.preview_fingerprint;
      showSummary(preview);
    }).catch(function (err) {
      ctx.collecting = false;
      window.alert(t("edit.preview_failed") + ": " + ((err && err.message) || err));
    }).then(function () {
      if (submitButton) submitButton.disabled = false;
    });
  }

  function runPreview(collectedBody) {
    var ctx = editorContext;
    var payload = serializeFromCollectedBody(collectedBody);
    return api("/jobs/" + encodeURIComponent(ctx.sourceJobId) + "/edit-recalculate/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (preview) {
      if (preview && preview.blocking_reasons && preview.blocking_reasons.length) {
        throw new Error(preview.blocking_reasons.join("\n"));
      }
      return preview;
    });
  }

  function finalSubmit() {
    var ctx = editorContext;
    if (!ctx || !ctx.pendingPreviewBody || ctx.submitting) return;
    ctx.submitting = true;
    var okBtn = document.getElementById("edit-summary-confirm");
    if (okBtn) okBtn.disabled = true;
    var payload = serializeFromCollectedBody(ctx.pendingPreviewBody);
    api("/jobs/" + encodeURIComponent(ctx.sourceJobId) + "/edit-recalculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (result) {
      editorContext = null;
      stopBadgeWatch();
      hideSummary();
      renderBanner("hidden");
      closeModal();
      if (typeof setDetailActionMessage === "function") {
        setDetailActionMessage(
          result.job_id,
          t(result.operation === "new_job" ? "edit.submit_new_ok" : "edit.submit_inplace_ok",
            { attempt: String(result.attempt) }),
          "ok"
        );
      }
      return refreshJobs();
    }, function (err) {
      // 请求失败保留草稿与错误（计划 §4.2）。
      if (okBtn) okBtn.disabled = false;
      ctx.submitting = false;
      var el = document.getElementById("edit-summary-error");
      if (el) {
        el.style.display = "block";
        el.textContent = t("edit.submit_failed") + ": " + ((err && err.message) || err);
      } else {
        window.alert(t("edit.submit_failed") + ": " + ((err && err.message) || err));
      }
    });
  }

  // ---------------------------------------------------------------------------
  // 本地差异计数（显示"已修改 N 项"；权威差异以服务端预览为准）
  // ---------------------------------------------------------------------------

  function currentProjection() {
    var ctx = editorContext;
    var spec = ctx.draft.editable_spec;
    var nproc = parseInt(document.getElementById("modal-nproc").value, 10) || 0;
    var mem = String(document.getElementById("modal-mem").value || "");
    return {
      remark: String(document.getElementById("modal-remark").value || ""),
      task_name: String(document.getElementById("modal-task-name").value || "").trim(),
      resources: { nproc: nproc || (spec.resources || {}).nproc, mem: mem },
      method_levels: cleanLevels(wizardState.method.stages),
      input_mode: ctx.inputMode,
      item_count: (ctx.itemIncludes || []).filter(function (v) { return v !== false; }).length,
    };
  }

  function countLocalChanges() {
    var ctx = editorContext;
    if (!ctx || !ctx.draft) return 0;
    var spec = ctx.draft.editable_spec;
    var proj = currentProjection();
    var count = 0;
    if ((proj.remark || "") !== (spec.remark || "")) count++;
    if (proj.task_name !== (spec.task_name && spec.task_name !== spec.workflow ? spec.task_name : "")) count++;
    var res = spec.resources || {};
    if (proj.resources.nproc && String(res.nproc || "") !== String(proj.resources.nproc)) count++;
    if (proj.resources.mem && String(res.mem || "") !== proj.resources.mem) count++;
    if (JSON.stringify(proj.method_levels) !== JSON.stringify(cleanLevels(spec.method && spec.method.levels))) count++;
    if (proj.input_mode !== "original") count++;
    if (ctx.itemIncludes && ctx.draft.editable_spec.input &&
        Array.isArray(ctx.draft.editable_spec.input.items) &&
        ctx.itemIncludes.some(function (v) { return v === false; })) count++;
    return count;
  }

  // ---------------------------------------------------------------------------
  // Banner / summary / diff 渲染
  // ---------------------------------------------------------------------------

  function bannerEl() { return document.getElementById("edit-recalc-banner"); }

  function renderBanner(state, errorMessage) {
    var el = bannerEl();
    if (!el) return;
    if (state === "hidden") { el.style.display = "none"; el.innerHTML = ""; return; }
    el.style.display = "block";
    var ctx = editorContext;
    if (state === "loading") {
      el.innerHTML = '<div class="edit-banner edit-banner-loading">' + t("edit.loading_draft") + "</div>";
      return;
    }
    if (state === "error") {
      el.innerHTML = '<div class="edit-banner edit-banner-error">' +
        t("edit.load_failed", { message: errorMessage || "" }) +
        ' <button type="button" class="btn ghost" id="edit-banner-close">' + t("modal.cancel") + "</button></div>";
      bindClose(el);
      return;
    }
    var d = ctx.draft;
    var caps = d.capabilities || {};
    if (!caps.can_edit) {
      renderViewOnlyBanner(d);
      return;
    }
    var sourceName = (ctx.sourceJob && (ctx.sourceJob.resolved_name || ctx.sourceJob.custom_name)) ||
      ctx.sourceJobId;
    var htmlParts = [];
    htmlParts.push('<div class="edit-banner edit-banner-ready">');
    htmlParts.push('<div class="edit-banner-source">' +
      t("edit.banner_source", {
        name: sourceName,
        status: t("status." + d.job_status) || d.job_status,
        attempt: String(d.attempt),
      }) + "</div>");
    if (d.migration_hint) {
      htmlParts.push('<div class="edit-banner-hint">' + t("edit.migration_hint", { hint: d.migration_hint }) + "</div>");
    }
    if ((d.missing_fields || []).length) {
      htmlParts.push('<div class="edit-banner-hint edit-warn">' +
        t("edit.missing_fields", { fields: d.missing_fields.join(", ") }) + "</div>");
    }
    // 输入模式单选：原始输入 / 最后有效结构（可用时）/ 替换输入。
    htmlParts.push('<div class="edit-banner-row"><span class="edit-row-label">' + t("edit.input_mode") + "</span>");
    var ls = (d.input_refs || {}).last_structure;
    var modes = [["original", "edit.input_original", true]];
    if (ls && ls.available) modes.push(["last_structure", "edit.input_last_structure", caps.can_edit]);
    modes.push(["replace", "edit.input_replace", caps.can_edit]);
    modes.forEach(function (m) {
      htmlParts.push('<label class="edit-radio' + (m[2] ? "" : " disabled") + '">' +
        '<input type="radio" name="edit-input-mode" value="' + m[0] + '"' +
        (ctx.inputMode === m[0] ? " checked" : "") + (m[2] ? "" : " disabled") + "> " + t(m[1]) + "</label>");
    });
    htmlParts.push("</div>");
    // 执行方式单选：原地重算 / 创建新任务。
    htmlParts.push('<div class="edit-banner-row"><span class="edit-row-label">' + t("edit.execution_mode") + "</span>");
    htmlParts.push('<label class="edit-radio' + (caps.can_in_place ? "" : " disabled") + '">' +
      '<input type="radio" name="edit-exec-mode" value="in_place"' +
      (ctx.executionMode === "in_place" ? " checked" : "") +
      (caps.can_in_place ? "" : " disabled") + "> " + t("edit.exec_in_place") + "</label>");
    htmlParts.push('<label class="edit-radio"><input type="radio" name="edit-exec-mode" value="new_job"' +
      (ctx.executionMode === "new_job" ? " checked" : "") + "> " + t("edit.exec_new_job") + "</label>");
    htmlParts.push("</div>");
    // 批量条目清单（BatchOptimize）：默认全选，可显式勾选部分。
    var items = (d.editable_spec.input || {}).items;
    if (Array.isArray(items) && items.length) {
      htmlParts.push('<details class="edit-items"><summary>' +
        t("edit.batch_items_summary", { total: String(items.length) }) +
        '</summary><div class="edit-items-list">');
      items.forEach(function (item, i) {
        var name = item.name || item.candidate_id || ("#" + (i + 1));
        var tag = item.tag ? " [" + item.tag + "]" : "";
        htmlParts.push('<label class="edit-radio"><input type="checkbox" data-edit-item="' + i + '"' +
          (ctx.itemIncludes && ctx.itemIncludes[i] === false ? "" : " checked") + "> " + name + tag + "</label>");
      });
      htmlParts.push("</div></details>");
    }
    htmlParts.push('<div class="edit-banner-footer">');
    htmlParts.push('<span class="edit-changed-badge" id="edit-changed-badge"></span>');
    htmlParts.push('<span class="flex-spacer"></span>');
    htmlParts.push('<button type="button" class="btn ghost" id="edit-banner-close">' + t("modal.cancel") + "</button>");
    htmlParts.push('<button type="button" class="btn primary" id="edit-recalc-submit">' +
      t("edit.check_and_submit") + "</button>");
    htmlParts.push("</div></div>");
    el.innerHTML = htmlParts.join("");
    bindClose(el);
    var submitBtn = el.querySelector("#edit-recalc-submit");
    if (submitBtn) submitBtn.addEventListener("click", function () { checkAndSubmit(); });
    el.querySelectorAll('input[name="edit-input-mode"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (radio.checked) { ctx.inputMode = radio.value; updateBannerState(); }
      });
    });
    el.querySelectorAll('input[name="edit-exec-mode"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (radio.checked) { ctx.executionMode = radio.value; updateBannerState(); }
      });
    });
    el.querySelectorAll("input[data-edit-item]").forEach(function (cb) {
      cb.addEventListener("change", function () {
        var i = parseInt(cb.getAttribute("data-edit-item"), 10);
        if (ctx.itemIncludes) ctx.itemIncludes[i] = cb.checked;
        updateBannerState();
      });
    });
    updateBannerState();
  }

  function bindClose(el) {
    var closeBtn = el.querySelector("#edit-banner-close");
    if (closeBtn) closeBtn.addEventListener("click", function () { cancelEditor(false); });
  }

  // 历史退役/planned 任务：识别 + 查看旧参数 + 解释兼容性（计划 §5.1），
  // 不重新开放已退休的执行入口。
  function renderViewOnlyBanner(d) {
    var el = bannerEl();
    if (!el) return;
    var spec = d.editable_spec || {};
    var methodKeys = (d.preserved_fields || []).join(", ") || "—";
    var reasons = (d.capabilities.disabled_reasons || [])
      .map(function (r) { return '<div class="edit-banner-hint edit-warn">' + escapeHtml(r) + "</div>"; })
      .join("");
    el.style.display = "block";
    el.innerHTML =
      '<div class="edit-banner edit-banner-ready"><div class="edit-banner-source">' +
      escapeHtml(t("edit.view_only_title", { workflow: d.workflow })) + "</div>" + reasons +
      '<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.view_only_params")) + ":</b> " +
      escapeHtml(methodKeys) + "</div>" +
      '<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.view_only_input")) + ":</b> " +
      escapeHtml(t("edit.view_only_input_kind", { kind: inputKind(spec.input) })) + "</div>" +
      (d.migration_hint ? '<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.migration_hint", { hint: d.migration_hint })) + "</b></div>" : "") +
      '<div class="edit-banner-footer"><span class="flex-spacer"></span>' +
      '<button type="button" class="btn ghost" id="edit-banner-close">' + escapeHtml(t("modal.cancel")) + "</button></div></div>";
    bindClose(el);
  }

  function updateBannerState() {
    var ctx = editorContext;
    if (!ctx || !ctx.draft) return;
    var badge = document.querySelector("#edit-changed-badge");
    if (badge) {
      var n = countLocalChanges();
      badge.textContent = t("edit.changed_n", { n: String(n) });
      badge.classList.toggle("has-changes", n > 0);
    }
    var submitBtn = document.getElementById("edit-recalc-submit");
    if (submitBtn) {
      var caps = ctx.draft.capabilities || {};
      var blocked = ctx.executionMode === "in_place" && !caps.can_in_place;
      submitBtn.disabled = blocked;
      submitBtn.title = blocked ? t("edit.in_place_blocked") : "";
    }
  }

  // Badge refresh: cheap interval while the editor is open — covers every
  // mutation path (resource inputs, method-config-modal saves, structure
  // re-parse) without hooking each page collector individually.
  var badgeTimer = null;

  function startBadgeWatch() {
    stopBadgeWatch();
    badgeTimer = setInterval(function () {
      if (!editorContext) { stopBadgeWatch(); return; }
      updateBannerState();
    }, 800);
  }

  function stopBadgeWatch() {
    if (badgeTimer !== null) { clearInterval(badgeTimer); badgeTimer = null; }
  }

  function showSummary(preview) {
    hideSummary();
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.id = "edit-summary-overlay";
    var diffRows = (preview.diff || []).map(function (entry) {
      var oldV = typeof entry.old === "object" ? JSON.stringify(entry.old) : String(entry.old);
      var newV = typeof entry.new === "object" ? JSON.stringify(entry.new) : String(entry.new);
      return '<tr><td class="edit-diff-path">' + escapeHtml(entry.path) + "</td><td>" +
        escapeHtml(oldV) + "</td><td>" + escapeHtml(newV) + "</td><td>" + escapeHtml(entry.kind) + "</td></tr>";
    }).join("");
    var warns = (preview.warnings || []).map(function (w) {
      return '<div class="edit-banner-hint edit-warn">' + escapeHtml(w) + "</div>";
    }).join("");
    var isRemote = editorContext && editorContext.executionMode === "new_job";
    overlay.innerHTML =
      '<div class="modal-dialog"><div class="modal-header"><h2>' + t("edit.summary_title") +
      '</h2><button class="modal-close" id="edit-summary-x">X</button></div><div class="modal-body">' +
      '<div class="edit-summary-meta">' +
      "<div><b>" + t("edit.summary_workflow") + ":</b> " + escapeHtml(preview.workflow) + "</div>" +
      "<div><b>" + t("edit.summary_mode") + ":</b> " +
      t(isRemote ? "edit.exec_new_job" : "edit.exec_in_place") + "</div>" +
      (isRemote ? "" : '<div class="edit-warn">' + t("edit.summary_cleanup_warning") + "</div>") +
      "</div>" + warns +
      (diffRows ? '<table class="edit-diff-table"><thead><tr><th>' + t("edit.diff_field") +
        "</th><th>" + t("edit.diff_old") + "</th><th>" + t("edit.diff_new") + "</th><th>" +
        t("edit.diff_kind") + "</th></tr></thead><tbody>" + diffRows + "</tbody></table>"
        : '<div class="edit-banner-hint">' + t("edit.no_changes") + "</div>") +
      '<div class="edit-summary-error" id="edit-summary-error" style="display:none;"></div>' +
      '</div><div class="modal-footer">' +
      '<button type="button" class="btn ghost" id="edit-summary-cancel">' + t("edit.summary_back") + "</button>" +
      '<button type="button" class="btn primary" id="edit-summary-confirm">' +
      t(isRemote ? "edit.confirm_new_job" : "edit.confirm_in_place") + "</button></div></div>";
    overlay.style.display = "flex";
    document.body.appendChild(overlay);
    overlay.querySelector("#edit-summary-cancel").addEventListener("click", hideSummary);
    overlay.querySelector("#edit-summary-x").addEventListener("click", hideSummary);
    overlay.querySelector("#edit-summary-confirm").addEventListener("click", finalSubmit);
  }

  function hideSummary() {
    var el = document.getElementById("edit-summary-overlay");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function escapeHtml(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // 导出：Workbench 只保留入口绑定（计划 §8）。
  window.ACPJobEditor = {
    isActive: isActive,
    currentMode: currentMode,
    sourceJobId: sourceJobId,
    openJobEditor: openJobEditor,
    openRerunMenu: openRerunMenu,
    closeRerunMenu: closeRerunMenu,
    interceptJobCreate: interceptJobCreate,
    cancelEditor: cancelEditor,
  };
})();
