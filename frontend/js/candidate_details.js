/**
 * ACP Candidate Details — right-column details/management panel for wizard step-1.
 * @version 0.2.0
 *
 * Namespace: window.ACPCandidateDetails
 *
 * Exposes:
 *   - mount(hostEl, { t, candidateApi }) -> instance
 *   - destroy()
 *
 * Instance methods:
 *   - setEntry(entry | null)   switch the currently displayed entry
 *   - refresh()                re-fetch from server
 *   - destroy()                tear down
 */
(function () {
  "use strict";

  var VERSION = "0.2.0";

  /* ── Valid assessment conclusions (backend enum) ──────────────────── */
  var VALID_CONCLUSIONS = ["unreviewed", "recommended", "review", "not_recommended"];

  /* ── Per-conclusion reason options ─────────────────────────────────── */
  var REASON_OPTIONS = {
    recommended: [
      { code: "geometry_reasonable", i18n: "candidate.inspector.reason.geometry_reasonable" },
      { code: "energy_favorable", i18n: "candidate.inspector.reason.energy_favorable" },
      { code: "experiment_consistent", i18n: "candidate.inspector.reason.experiment_consistent" },
      { code: "irc_verified", i18n: "candidate.inspector.reason.irc_verified" },
      { code: "other", i18n: "candidate.inspector.reason.other" }
    ],
    review: [
      { code: "reaction_mode_uncertain", i18n: "candidate.inspector.reason.reaction_mode_uncertain" },
      { code: "optimization_other_channel", i18n: "candidate.inspector.reason.optimization_other_channel" },
      { code: "frequency_insufficient", i18n: "candidate.inspector.reason.frequency_insufficient" },
      { code: "geometry_questionable", i18n: "candidate.inspector.reason.geometry_questionable" },
      { code: "other", i18n: "candidate.inspector.reason.other" }
    ],
    not_recommended: [
      { code: "geometry_unreasonable", i18n: "candidate.inspector.reason.geometry_unreasonable" },
      { code: "wrong_reaction_mode", i18n: "candidate.inspector.reason.wrong_reaction_mode" },
      { code: "duplicate_candidate", i18n: "candidate.inspector.reason.duplicate_candidate" },
      { code: "experiment_unsupported", i18n: "candidate.inspector.reason.experiment_unsupported" },
      { code: "irc_not_connected", i18n: "candidate.inspector.reason.irc_not_connected" },
      { code: "other", i18n: "candidate.inspector.reason.other" }
    ]
  };

  /* ── Utility ──────────────────────────────────────────────────────── */
  function esc(s) {
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(String(s == null ? "" : s)));
    return d.innerHTML;
  }

  function _(inst, key, fallback) {
    try { var v = inst._t(key); return v === key ? (fallback || key) : v; }
    catch (_) { return fallback || key; }
  }

  /* ── mount ────────────────────────────────────────────────────────── */
  function mount(hostEl, options) {
    if (!hostEl) throw new Error("ACPCandidateDetails.mount: hostEl required");
    var _host = hostEl;
    var _opts = options || {};
    var _t = _opts.t || function (k) { return k; };
    var _api = _opts.candidateApi;
    if (!_api) throw new Error("ACPCandidateDetails.mount: candidateApi required");

    var _destroyed = false;
    var _entry = null;           // last snapshot passed to setEntry
    var _detail = null;          // enriched detail from server (candidate detail endpoint)
    var _editing = false;        // true when user has unsaved edits
    var _saving = false;         // true during a save request
    var _conflict = false;       // true when409was received
    var _conflictProjection = null;
    var _abort = null;           // AbortController for in-flight requests
    var _requestToken = 0;       // rejects late responses after switching entries

    /* ── Dirty tracking ──────────────────────────────────────────────── */
    var _dirtyFields = {};       // { conclusion, reason_code, scope, note, custom_name, add_tags, remove_tags }

    function _isDirty() {
      return _editing && Object.keys(_dirtyFields).some(function (k) {
        var v = _dirtyFields[k];
        if (Array.isArray(v)) return v.length > 0;
        return v !== undefined && v !== null && v !== "";
      });
    }

    /* ── Render ──────────────────────────────────────────────────────── */
    function _render() {
      if (_destroyed) return;
      if (!_entry) {
        _host.hidden = true;
        _host.innerHTML = "";
        return;
      }
      _host.hidden = false;
      var e = _entry;
      var d = _detail || {};
      var name = e.resolved_name || e.custom_name || e.default_name || e.candidate_id || e.source_uid || "";
      var role = e.role || "";
      var sourceTask = e.job_resolved_name || e.job_name || "";
      var candidateId = e.candidate_id || "";
      var frame = e.version_id || "";
      var assessment = e.assessment || "unreviewed";
      var tags = Array.isArray(e.tags) ? e.tags : [];
      var notes = d.assessments && d.assessments.length ? (d.assessments[0].note || "") : "";
      var latestAssessment = d.assessments && d.assessments.length ? d.assessments[0] : null;

      var html = "";

      /* Conflict banner */
      if (_conflict) {
        html += '<div class="cd-conflict" role="alert">' +
          esc(_(inst, "candidate.inspector.conflict_banner", "该候选已在其他窗口被修改")) +
          ' <button type="button" class="btn ghost cd-conflict-reload" aria-label="' +
          esc(_(inst, "cd.reload", "刷新")) + '">' +
          esc(_(inst, "cd.reload", "刷新")) + '</button></div>';
      }

      /* Name row (copyable) */
      html += '<div class="cd-row cd-name-row">' +
        '<label class="cd-label">' + esc(_(inst, "cd.name", "名称")) + '</label>' +
        '<div class="cd-value cd-name-value" title="' + esc(name) + '">' +
        '<span class="cd-name-text">' + esc(name) + '</span>' +
        '<button type="button" class="btn ghost cd-copy-btn" data-copy="' + esc(name) + '" ' +
        'aria-label="' + esc(_(inst, "cd.copy_name", "复制名称")) + '" title="' +
        esc(_(inst, "cd.copy_name", "复制名称")) + '">&#x2398;</button>' +
        '</div></div>';

      html += '<div class="cd-summary-actions">' +
        '<button type="button" class="btn ghost cd-edit-btn">' +
        esc(_(inst, "candidate.inspector.edit", "编辑")) + '</button>' +
        (typeof _opts.onMoveToTrash === "function" ? '<button type="button" class="btn danger cd-trash-btn">' +
          esc(_(inst, "trash.row_menu.move_to_trash", "移入垃圾箱")) + '</button>' : '') +
        '</div>';

      /* Editable name */
      html += '<div class="cd-row cd-edit-only"' + (_editing ? '' : ' hidden') + '>' +
        '<label class="cd-label" for="cd-edit-name">' + esc(_(inst, "cd.rename", "重命名")) + '</label>' +
        '<div class="cd-value"><input id="cd-edit-name" class="cd-input" type="text" ' +
        'value="' + esc(e.custom_name || "") + '" placeholder="' + esc(name) + '" ' +
        'aria-label="' + esc(_(inst, "cd.rename", "重命名")) + '" /></div></div>';

      /* Role */
      html += '<div class="cd-row cd-role-row">' +
        '<label class="cd-label">' + esc(_(inst, "cd.role", "角色")) + '</label>' +
        '<div class="cd-value"><span class="cd-badge cd-role-' + esc(role.toLowerCase()) + '">' +
        esc(role || _(inst, "cd.not_provided", "未提供")) + '</span></div></div>';

      /* Source task */
      html += '<div class="cd-row cd-source-row">' +
        '<label class="cd-label">' + esc(_(inst, "cd.source_task", "来源任务")) + '</label>' +
        '<div class="cd-value">' + (sourceTask
          ? '<a href="#" class="cd-task-link" data-job-id="' + esc(e.job_id || "") + '">' + esc(sourceTask) + '</a>'
          : '<span class="cd-muted">' + esc(_(inst, "cd.not_provided", "未提供")) + '</span>'
        ) + '</div></div>';

      /* Candidate ID / frame */
      html += '<div class="cd-row cd-candidate-row">' +
        '<label class="cd-label">' + esc(_(inst, "cd.candidate_id", "候选 ID")) + '</label>' +
        '<div class="cd-value">' + (candidateId
          ? esc(candidateId) + (frame ? ' <span class="cd-muted">(' + esc(_(inst, "cd.version", "版本")) + ': ' + esc(frame) + ')</span>' : '')
          : '<span class="cd-muted">' + esc(_(inst, "cd.not_provided", "未提供")) + '</span>'
        ) + '</div></div>';

      /* Assessment badge */
      var conclusionLabel = _(inst, "picker.assessment." + assessment, assessment);
      var badgeClass = assessment === "recommended" ? "ci-badge-good" :
        assessment === "not_recommended" ? "ci-badge-err" :
        assessment === "review" ? "ci-badge-warn" : "ci-badge-muted";
      html += '<div class="cd-row">' +
        '<label class="cd-label">' + esc(_(inst, "candidate.inspector.current_assessment", "当前评价")) + '</label>' +
        '<div class="cd-value"><span class="ci-badge ' + esc(badgeClass) + '">' + esc(conclusionLabel) + '</span></div></div>';

      /* Assessment edit form */
      html += '<div class="cd-section cd-assessment-form cd-edit-only"' + (_editing ? '' : ' hidden') + '>' +
        '<div class="cd-section-title">' + esc(_(inst, "candidate.inspector.detail_title", "评价详情")) + '</div>';

      /* Conclusion select */
      html += '<div class="cd-row"><label class="cd-label" for="cd-conclusion">' +
        esc(_(inst, "candidate.inspector.conclusion_label", "评价结论")) + '</label>' +
        '<div class="cd-value"><select id="cd-conclusion" class="cd-select" aria-label="' +
        esc(_(inst, "candidate.inspector.conclusion_label", "评价结论")) + '">';
      for (var ci = 0; ci < VALID_CONCLUSIONS.length; ci++) {
        var c = VALID_CONCLUSIONS[ci];
        var sel = (latestAssessment && latestAssessment.conclusion === c) ? " selected" : (!latestAssessment && c === "unreviewed" ? " selected" : "");
        html += '<option value="' + esc(c) + '"' + sel + '>' +
          esc(_(inst, "picker.assessment." + c, c === "unreviewed" ? "未评估" : c)) + '</option>';
      }
      html += '</select></div></div>';

      /* Reason select */
      html += '<div class="cd-row"><label class="cd-label" for="cd-reason">' +
        esc(_(inst, "candidate.inspector.reason_label", "原因")) +
        ' <span class="cd-required">*</span></label>' +
        '<div class="cd-value"><select id="cd-reason" class="cd-select" aria-label="' +
        esc(_(inst, "candidate.inspector.reason_label", "原因")) + '">' +
        '<option value="">' + esc(_(inst, "candidate.inspector.placeholder_select", "请选择")) + '</option>';
      var currentConclusion = (latestAssessment && latestAssessment.conclusion) || "unreviewed";
      var reasonOpts = REASON_OPTIONS[currentConclusion] || [];
      for (var ri = 0; ri < reasonOpts.length; ri++) {
        var r = reasonOpts[ri];
        var rSel = (latestAssessment && latestAssessment.reason_code === r.code) ? " selected" : "";
        html += '<option value="' + esc(r.code) + '"' + rSel + '>' +
          esc(_(inst, r.i18n, r.code)) + '</option>';
      }
      html += '</select><div class="cd-error" id="cd-reason-error" style="display:none">' +
        esc(_(inst, "candidate.inspector.reason_required", "必须填写原因")) + '</div></div></div>';

      /* Scope */
      html += '<div class="cd-row"><label class="cd-label" for="cd-scope">' +
        esc(_(inst, "candidate.inspector.scope_label", "适用范围")) + '</label>' +
        '<div class="cd-value"><input id="cd-scope" class="cd-input" type="text" value="' +
        esc((latestAssessment && latestAssessment.scope) || "project") + '" placeholder="project" ' +
        'aria-label="' + esc(_(inst, "candidate.inspector.scope_label", "适用范围")) + '" /></div></div>';

      /* Note */
      html += '<div class="cd-row"><label class="cd-label" for="cd-note">' +
        esc(_(inst, "candidate.inspector.note_label", "说明")) + '</label>' +
        '<div class="cd-value"><textarea id="cd-note" class="cd-textarea" rows="3" placeholder="' +
        esc(_(inst, "candidate.inspector.note_placeholder", "记录条件、判断理由及证据任务 ID")) + '" ' +
        'aria-label="' + esc(_(inst, "candidate.inspector.note_label", "说明")) + '">' +
        esc(notes) + '</textarea></div></div>';
      html += '</div>'; // .cd-assessment-form

      /* Tags */
      html += '<div class="cd-row cd-tags-row"><label class="cd-label">' +
        esc(_(inst, "cd.tags", "标签")) + '</label><div class="cd-value cd-tags-list">';
      if (tags.length) {
        for (var ti = 0; ti < tags.length; ti++) {
          html += '<span class="cd-tag">' + esc(tags[ti]) +
            (_editing ? ' <button type="button" class="btn ghost cd-tag-remove" data-tag="' + esc(tags[ti]) + '" ' +
            'aria-label="' + esc(_(inst, "cd.remove_tag", "移除标签")) + ': ' + esc(tags[ti]) + '">&times;</button></span>'
              : '</span>');
        }
      } else {
        html += '<span class="cd-muted">' + esc(_(inst, "cd.no_tags", "无标签")) + '</span>';
      }
      html += '</div></div>';

      /* Add tag input */
      html += '<div class="cd-row cd-edit-only"' + (_editing ? '' : ' hidden') + '><label class="cd-label" for="cd-add-tag">' +
        esc(_(inst, "cd.add_tag", "添加标签")) + '</label>' +
        '<div class="cd-value cd-tag-input-row"><input id="cd-add-tag" class="cd-input" type="text" ' +
        'placeholder="' + esc(_(inst, "cd.tag_placeholder", "标签名称")) + '" ' +
        'aria-label="' + esc(_(inst, "cd.add_tag", "添加标签")) + '" />' +
        '<button type="button" class="btn ghost cd-tag-add-btn" aria-label="' +
        esc(_(inst, "cd.add_tag", "添加标签")) + '">+</button></div></div>';

      /* Save caption */
      html += '<div class="cd-save-caption cd-edit-only"' + (_editing ? '' : ' hidden') + '>' +
        esc(_(inst, "candidate.inspector.save_caption", "此修改会立即更新候选记录；取消新建任务不会撤销已保存的评价。")) +
        '</div>';

      /* Action buttons */
      html += '<div class="cd-actions cd-edit-only"' + (_editing ? '' : ' hidden') + '>';
      html += '<button type="button" class="btn primary cd-save-btn" ' +
        (_saving ? "disabled" : "") + ' aria-label="' +
        esc(_(inst, "candidate.inspector.save_btn", "保存到候选库")) + '">' +
        (_saving ? esc(_(inst, "cd.saving", "保存中…")) : esc(_(inst, "candidate.inspector.save_btn", "保存"))) +
        '</button>';
      html += '<button type="button" class="btn ghost cd-cancel-btn" ' +
        (!_isDirty() ? "disabled" : "") + ' aria-label="' +
        esc(_(inst, "candidate.inspector.cancel_btn", "取消")) + '">' +
        esc(_(inst, "candidate.inspector.cancel_btn", "取消")) + '</button>';
      html += '</div>';

      /* Aria-live region for save results */
      html += '<div class="cd-live-region" aria-live="polite" role="status" id="cd-live-status"></div>';

      _host.innerHTML = html;
      _bindEvents();
    }

    /* ── Bind events ─────────────────────────────────────────────────── */
    function _bindEvents() {
      var editBtn = _host.querySelector(".cd-edit-btn");
      if (editBtn) editBtn.addEventListener("click", function () {
        _editing = true;
        _render();
        var first = _host.querySelector("#cd-edit-name");
        if (first) first.focus();
      });
      var trashBtn = _host.querySelector(".cd-trash-btn");
      if (trashBtn) trashBtn.addEventListener("click", function () {
        if (typeof _opts.onMoveToTrash === "function") _opts.onMoveToTrash(_entry);
      });

      /* Copy name */
      var copyBtns = _host.querySelectorAll(".cd-copy-btn");
      for (var i = 0; i < copyBtns.length; i++) {
        copyBtns[i].addEventListener("click", function () {
          var text = this.getAttribute("data-copy") || "";
          try { navigator.clipboard.writeText(text); } catch (_) { /* ignore */ }
        });
      }

      /* Task link */
      var taskLinks = _host.querySelectorAll(".cd-task-link");
      for (var j = 0; j < taskLinks.length; j++) {
        taskLinks[j].addEventListener("click", function (e) {
          e.preventDefault();
          var jobId = this.getAttribute("data-job-id");
          if (jobId && typeof _opts.onOpenJob === "function") _opts.onOpenJob(jobId);
        });
      }

      /* Dirty tracking on inputs */
      var inputs = _host.querySelectorAll(".cd-input, .cd-select, .cd-textarea");
      for (var k = 0; k < inputs.length; k++) {
        inputs[k].addEventListener("input", _markDirty);
        inputs[k].addEventListener("change", _markDirty);
      }

      /* Conclusion change → update reason options */
      var conclusionEl = _host.querySelector("#cd-conclusion");
      if (conclusionEl) {
        conclusionEl.addEventListener("change", function () {
          _updateReasonOptions(this.value);
          _markDirty();
        });
      }

      /* Tag remove */
      var tagRemoveBtns = _host.querySelectorAll(".cd-tag-remove");
      for (var m = 0; m < tagRemoveBtns.length; m++) {
        tagRemoveBtns[m].addEventListener("click", function () {
          var tag = this.getAttribute("data-tag");
          if (tag) {
            if (!_dirtyFields.remove_tags) _dirtyFields.remove_tags = [];
            _dirtyFields.remove_tags.push(tag);
            _markDirty();
            _render();
          }
        });
      }

      /* Tag add */
      var tagAddBtn = _host.querySelector(".cd-tag-add-btn");
      var tagInput = _host.querySelector("#cd-add-tag");
      if (tagAddBtn && tagInput) {
        function _doAddTag() {
          var val = (tagInput.value || "").trim();
          if (!val) return;
          if (!_dirtyFields.add_tags) _dirtyFields.add_tags = [];
          _dirtyFields.add_tags.push(val);
          tagInput.value = "";
          _markDirty();
          _render();
        }
        tagAddBtn.addEventListener("click", _doAddTag);
        tagInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter") { e.preventDefault(); _doAddTag(); }
        });
      }

      /* Save */
      var saveBtn = _host.querySelector(".cd-save-btn");
      if (saveBtn) saveBtn.addEventListener("click", _save);

      /* Cancel */
      var cancelBtn = _host.querySelector(".cd-cancel-btn");
      if (cancelBtn) cancelBtn.addEventListener("click", _cancelEdits);

      /* Conflict reload */
      var reloadBtn = _host.querySelector(".cd-conflict-reload");
      if (reloadBtn) reloadBtn.addEventListener("click", function () { _conflict = false; refresh(); });

      /* Esc closes inline dialogs (focus on host) */
      _host.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && _isDirty()) {
          e.preventDefault();
          _cancelEdits();
        }
      });
    }

    function _markDirty(event) {
      _editing = true;
      var targetId = event && event.target ? event.target.id : "";
      if (targetId === "cd-edit-name") _dirtyFields.custom_name = event.target.value.trim();
      if (targetId === "cd-conclusion") _dirtyFields.conclusion = event.target.value;
      if (targetId === "cd-reason") _dirtyFields.reason_code = event.target.value;
      if (targetId === "cd-scope") _dirtyFields.scope = event.target.value.trim();
      if (targetId === "cd-note") _dirtyFields.note = event.target.value.trim();

      /* Enable/disable save button */
      var saveBtn = _host.querySelector(".cd-save-btn");
      if (saveBtn) saveBtn.disabled = _saving;
      var cancelBtn = _host.querySelector(".cd-cancel-btn");
      if (cancelBtn) cancelBtn.disabled = false;
    }

    function _updateReasonOptions(conclusion) {
      var reasonEl = _host.querySelector("#cd-reason");
      if (!reasonEl) return;
      var opts = REASON_OPTIONS[conclusion] || [];
      var html = '<option value="">' + esc(_(inst, "candidate.inspector.placeholder_select", "请选择")) + '</option>';
      for (var i = 0; i < opts.length; i++) {
        html += '<option value="' + esc(opts[i].code) + '">' +
          esc(_(inst, opts[i].i18n, opts[i].code)) + '</option>';
      }
      reasonEl.innerHTML = html;
    }

    function _cancelEdits() {
      _editing = false;
      _dirtyFields = {};
      _conflict = false;
      _conflictProjection = null;
      _render();
    }

    /* ── Save ────────────────────────────────────────────────────────── */
    async function _save() {
      if (_saving || !_entry) return;

      var assessmentDirty = _dirtyFields.conclusion !== undefined ||
        _dirtyFields.reason_code !== undefined || _dirtyFields.scope !== undefined ||
        _dirtyFields.note !== undefined;
      /* Validate the reason only when an assessment is actually changing. */
      var reasonEl = _host.querySelector("#cd-reason");
      var reasonErr = _host.querySelector("#cd-reason-error");
      var reasonCode = reasonEl ? reasonEl.value : "";
      var conclusionValue = (_host.querySelector("#cd-conclusion") || {}).value || "unreviewed";
      if (assessmentDirty && conclusionValue !== "unreviewed" && !reasonCode) {
        if (reasonErr) reasonErr.style.display = "block";
        if (reasonEl) reasonEl.focus();
        return;
      }
      if (reasonErr) reasonErr.style.display = "none";

      _saving = true;
      _conflict = false;
      _conflictProjection = null;
      _render();

      var uid = _entry.source_uid;
      var liveEl = _host.querySelector("#cd-live-status");
      try {
        /* Metadata name + tag changes share one revision-checked request. */
        var customName = _dirtyFields.custom_name;
        var hasRename = customName !== undefined && customName !== (_entry.custom_name || "");
        var addTags = _dirtyFields.add_tags || [];
        var removeTags = _dirtyFields.remove_tags || [];
        if (hasRename || addTags.length || removeTags.length) {
          var tagPayload = { expected_revision: _entry.metadata_revision || 0 };
          if (hasRename) tagPayload.custom_name = customName || null;
          if (addTags.length) tagPayload.add_tags = addTags;
          if (removeTags.length) tagPayload.remove_tags = removeTags;
          var tagResult = await _api("/structure-sources/" + encodeURIComponent(uid) + "/metadata", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(tagPayload)
          });
          /* Update local revision from response */
          if (tagResult && tagResult.metadata_revision !== undefined) {
            _entry.metadata_revision = tagResult.metadata_revision;
          }
        }

        /* Assessment is independent; metadata-only edits never create one. */
        var conclusion = conclusionValue;
        if (assessmentDirty && conclusion !== "unreviewed" && reasonCode) {
          var assessPayload = {
            conclusion: conclusion,
            reason_code: reasonCode,
            scope: _dirtyFields.scope || "project",
            note: _dirtyFields.note || "",
            version_id: _entry.version_id || null
          };
          await _api("/structure-sources/" + encodeURIComponent(uid) + "/candidate/assessments", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(assessPayload)
          });
        }

        /* Success */
        _editing = false;
        _dirtyFields = {};
        _conflict = false;
        if (liveEl) liveEl.textContent = _(inst, "candidate.inspector.save_success", "评价已保存");
        await refresh();
      } catch (err) {
        if (err && err.status === 409) {
          _conflict = true;
          _conflictProjection = (err.message && err.message.indexOf("{") >= 0) ? err.message : null;
          if (liveEl) liveEl.textContent = _(inst, "candidate.inspector.conflict_banner", "该候选已在其他窗口被修改");
        } else {
          if (liveEl) liveEl.textContent = _(inst, "candidate.inspector.save_failed", "保存失败") + ": " + ((err && err.message) || String(err));
        }
      } finally {
        _saving = false;
        _render();
      }
    }

    /* ── Refresh from server ─────────────────────────────────────────── */
    async function refresh() {
      if (_destroyed || !_entry) return;
      var uid = _entry.source_uid;
      if (!uid) return;
      var token = ++_requestToken;
      try {
        if (_abort) { try { _abort.abort(); } catch (_) {} }
        _abort = new AbortController();
        var detail = await _api("/structure-sources/" + encodeURIComponent(uid) + "/candidate", { signal: _abort.signal });
        if (_destroyed || token !== _requestToken || !_entry || _entry.source_uid !== uid) return;
        _detail = detail;
        /* Update entry metadata from detail response */
        if (detail) {
          _entry.metadata_revision = detail.metadata_revision !== undefined ? detail.metadata_revision : _entry.metadata_revision;
          _entry.assessment = detail.assessment || _entry.assessment;
          _entry.tags = detail.tags || _entry.tags;
          _entry.custom_name = detail.custom_name !== undefined ? detail.custom_name : _entry.custom_name;
          _entry.resolved_name = detail.resolved_name || _entry.resolved_name;
        }
        _conflict = false;
        _render();
      } catch (err) {
        if (_destroyed || token !== _requestToken) return;
        /* Non-fatal: keep last known state */
      }
    }

    /* ── Instance ────────────────────────────────────────────────────── */
    var inst = {
      VERSION: VERSION,
      _t: _t,

      setEntry: function (entry) {
        if (_destroyed) return false;
        if (_saving) return false;
        /* Guard: prompt if dirty */
        if (_isDirty()) {
          var msg = _(inst, "candidate.inspector.discard_confirm", "当前评价尚未保存，切换候选将放弃修改。");
          if (!window.confirm(msg)) return false;
        }
        _requestToken++;
        if (_abort) { try { _abort.abort(); } catch (_) {} }
        _entry = entry || null;
        _detail = null;
        _editing = false;
        _dirtyFields = {};
        _conflict = false;
        _conflictProjection = null;
        _render();
        if (_entry && _entry.source_uid) refresh();
        return true;
      },

      refresh: refresh,

      destroy: function () {
        _destroyed = true;
        _requestToken++;
        if (_abort) { try { _abort.abort(); } catch (_) {} }
        _host.innerHTML = "";
        _host.hidden = true;
        _entry = null;
        _detail = null;
      }
    };

    /* Initial empty state */
    _render();

    return inst;
  }

  /* ── Static destroy ──────────────────────────────────────────────── */
  function destroy() {}

  window.ACPCandidateDetails = {
    VERSION: VERSION,
    VALID_CONCLUSIONS: VALID_CONCLUSIONS,
    mount: mount,
    destroy: destroy
  };
})();
