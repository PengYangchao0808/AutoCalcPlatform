// ── Q9/Q10: queue operations (pause / unpause / continue / rerun / purge) ──
// All POST to the new v1 endpoints; 409 responses carry the server's guidance
// text (detail), which api() surfaces as e.message and we show inline.
//
// R3: the endpoint calls live in ACPClient.jobCommands (js/client/job-commands.js);
// the functions below are thin delegators that keep the page-side use-case
// behavior (action toasts, post-action refreshes, rename fan-out, purge
// selection clearing). v1/v2 bind the page transports — compat/api.js is
// evaluated before this file. invalidateJobDetail resolves lazily: the hook
// only dereferences it when a command receipt fires, and job-detail.js has
// loaded by then.
ACPClient.jobCommands.init({
  v1: api,
  v2: apiV2,
  onInvalidate: function (jobId) { if (jobId) invalidateJobDetail(jobId); }
});

async function pauseJob(jobId) {
  if (!jobId) return;
  try {
    await ACPClient.jobCommands.pause(jobId);
    setDetailActionMessage(jobId, t("job.pause_ok"), "ok");
    await refreshJobDetailAfterAction(jobId);
  } catch (e) {
    setDetailActionMessage(jobId, t("job.pause_failed", { message: describeJobOpError(e) }), "err");
  }
}

async function unpauseJob(jobId) {
  if (!jobId) return;
  try {
    await ACPClient.jobCommands.unpause(jobId);
    setDetailActionMessage(jobId, t("job.resume_ok"), "ok");
    await refreshJobDetailAfterAction(jobId);
  } catch (e) {
    setDetailActionMessage(jobId, t("job.resume_failed", { message: describeJobOpError(e) }), "err");
  }
}

// T12: optional "continue to node" override in the job-detail action bar.
// The blank option means "return to the source node" (server default when
// the request carries no body) — the override field is only sent when the
// user explicitly picks a node, so a coded 400 can only come from a real
// choice. Options come from the cached /nodes list (offline nodes skipped —
// the server would reject them anyway).
function populateContinueNodeOptions(sel) {
  if (!sel) return;
  var current = sel.value || "";
  sel.innerHTML = "";
  var srcOpt = document.createElement("option");
  srcOpt.value = "";
  srcOpt.textContent = t("nodes.continue_source") + (lastRemoteNode ? " (" + lastRemoteNode + ")" : "");
  sel.appendChild(srcOpt);
  var localOpt = document.createElement("option");
  localOpt.value = "local";
  localOpt.textContent = t("nodes.opt.local");
  sel.appendChild(localOpt);
  nodesCache.forEach(function(node) {
    if (!node || !node.name || node.status === "offline") return;
    var opt = document.createElement("option");
    opt.value = node.name;
    opt.textContent = node.name + (node.host ? "  " + node.host : "");
    sel.appendChild(opt);
  });
  sel.value = current;
  if (sel.selectedIndex < 0) sel.value = "";
}

async function continueJob(jobId) {
  if (!jobId) return;
  var nodeSel = document.getElementById("detail-continue-node-select");
  var targetNode = nodeSel && nodeSel.value ? String(nodeSel.value) : "";
  // T6 contract, kept visible at the call site: only an explicitly picked
  // node is sent as target_node — the blank option stays a no-body POST
  // (= back to source). jobCommands.continueJob derives this exact body
  // from targetNode (Node-pinned); the shape below is the page-side record
  // of the wire contract the delegator relies on.
  var opts = { method: "POST" };
  if (targetNode) {
    opts.body = JSON.stringify({ target_node: targetNode });
  }
  try {
    await ACPClient.jobCommands.continueJob(jobId, targetNode);
    setDetailActionMessage(jobId, t("job.continue_ok"), "ok");
    await refreshJobDetailAfterAction(jobId);
  } catch (e) {
    setDetailActionMessage(jobId, t("job.continue_failed", { message: translateNodeError(e) || describeJobOpError(e) }), "err");
  }
}

// Rerun dedup lives in the client (jobCommands rerunInFlight set). The
// pre-check below preserves the live guard's caller behavior: a second call
// while the first is pending returns silently — no error toast, button left
// untouched.
async function rerunJob(jobId, button) {
  if (!jobId || ACPClient.jobCommands.isRerunInFlight(jobId)) return;
  if (button) button.disabled = true;
  try {
    await ACPClient.jobCommands.rerun(jobId);
    setDetailActionMessage(jobId, t("job.rerun_ok"), "ok");
    await refreshJobDetailAfterAction(jobId);
  } catch (e) {
    setDetailActionMessage(jobId, t("job.rerun_failed", { message: describeJobOpError(e) }), "err");
  } finally {
    if (button) button.disabled = false;
  }
}

// Q10: batch purge multi-select bookkeeping.
function pruneQueueSelection() {
  const valid = new Set(jobsCache.map(function(entry) { return String(entry.id || entry.job_id || ""); }));
  Array.from(queueSelectedJobs).forEach(function(id) {
    if (!valid.has(id)) queueSelectedJobs.delete(id);
  });
}

function updateQueueBatchBar() {
  const count = queueSelectedJobs.size;
  ["queue-batch-purge", "mobile-queue-batch-purge"].forEach(function(id) {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.style.display = count ? "" : "none";
    if (count) btn.textContent = t("purge.selected", { count: count });
  });
  var batchBar = document.getElementById("queue-batch-bar");
  if (batchBar) {
    batchBar.style.display = count > 0 ? "" : "none";
    ["batch-add-tags", "batch-remove-tags", "batch-archive", "batch-unarchive", "batch-set-molecule"].forEach(function(id) {
      var el = document.getElementById(id);
      if (el) el.style.display = count > 0 ? "" : "none";
    });
    var tagInput = document.getElementById("batch-tag-input");
    var molInput = document.getElementById("batch-molecule-input");
    if (tagInput) tagInput.style.display = count > 0 ? "" : "none";
    if (molInput) molInput.style.display = count > 0 ? "" : "none";
  }
}

function _getSelectedProjectId() {
  return selectedProjectId || null;
}

async function _batchOp(op, payload) {
  var ids = Array.from(queueSelectedJobs);
  if (!ids.length) return;
  try {
    var resp = await ACPClient.jobCommands.batchOp({ taskIds: ids, op: op, payload: payload });
    var ok = 0, fail = 0, errors = [];
    if (resp && Array.isArray(resp.results)) {
      resp.results.forEach(function(r) {
        if (r && r.ok !== false && !r.error) ok++;
        else { fail++; if (r && r.error) errors.push(r.task_id + ": " + r.error); }
      });
    } else if (resp && typeof resp.updated === "number") {
      ok = resp.updated;
    }
    if (fail) {
      appendLogLine("warn", t("queue.view.batch_op_partial", { ok: ok, total: ids.length }));
      if (errors.length) appendLogLine("stderr", errors.slice(0, 5).join("\n"));
    } else {
      appendLogLine("done", t("queue.view.batch_op_success", { count: ok }));
    }
    ids.forEach(function(id) { invalidateJobDetail(id); });
    await refreshJobs();
  } catch (e) {
    appendLogLine("stderr", "batch-ops " + op + " failed: " + (e.message || e));
  }
}

async function _batchAddTags() {
  var input = document.getElementById("batch-tag-input");
  if (!input) return;
  var raw = input.value.trim();
  if (!raw) return;
  var tags = raw.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
  if (!tags.length) return;
  await _batchOp("add_tags", { tags: tags });
  input.value = "";
}

async function _batchRemoveTags() {
  var input = document.getElementById("batch-tag-input");
  if (!input) return;
  var raw = input.value.trim();
  if (!raw) return;
  var tags = raw.split(",").map(function(s) { return s.trim(); }).filter(Boolean);
  if (!tags.length) return;
  await _batchOp("remove_tags", { tags: tags });
  input.value = "";
}

async function _batchArchive() {
  await _batchOp("archive", {});
}

async function _batchUnarchive() {
  await _batchOp("unarchive", {});
}

async function _batchSetMolecule() {
  var input = document.getElementById("batch-molecule-input");
  if (!input) return;
  var name = input.value.trim();
  if (!name) return;
  await _batchOp("set_molecule_name", { molecule_name: name });
  input.value = "";
}

function _toggleExportMenu() {
  var menu = document.getElementById("task-view-export-menu");
  if (!menu) return;
  menu.classList.toggle("open");
  if (menu.classList.contains("open")) {
    setTimeout(function() {
      document.addEventListener("click", _closeExportMenuOutside, true);
    }, 0);
  }
}

function _closeExportMenuOutside(e) {
  var wrap = document.querySelector(".export-menu-wrap");
  if (wrap && !wrap.contains(e.target)) {
    var menu = document.getElementById("task-view-export-menu");
    if (menu) menu.classList.remove("open");
    document.removeEventListener("click", _closeExportMenuOutside, true);
  }
}

function _flattenTaskViewRows() {
  var rows = [];
  if (!taskViewCache || !Array.isArray(taskViewCache.groups)) return rows;
  taskViewCache.groups.forEach(function(g) {
    (g.jobs || []).forEach(function(r) { rows.push(r); });
  });
  return rows;
}

function _exportCSV() {
  var rows = _flattenTaskViewRows();
  var header = t("queue.view.export_columns");
  var headerKeys = ["id", "molecule_name", "task_name", "remark", "workflow", "status", "tags", "created_at", "completed_at"];
  var headerLabels = header.split(",");
  var lines = [headerLabels.map(function(h) { return '"' + h.replace(/"/g, '""') + '"'; }).join(",")];
  rows.forEach(function(r) {
    var vals = headerKeys.map(function(k) {
      var v = r[k];
      if (k === "tags" && Array.isArray(v)) v = v.join(";");
      if (v == null) v = "";
      return '"' + String(v).replace(/"/g, '""') + '"';
    });
    lines.push(vals.join(","));
  });
  var csv = "\uFEFF" + lines.join("\n");
  var blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "task_export_" + new Date().toISOString().slice(0, 10) + ".csv";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  var menu = document.getElementById("task-view-export-menu");
  if (menu) menu.classList.remove("open");
}

function _exportJSON() {
  var rows = _flattenTaskViewRows();
  var json = JSON.stringify(rows, null, 2);
  var blob = new Blob([json], { type: "application/json" });
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url;
  a.download = "task_export_" + new Date().toISOString().slice(0, 10) + ".json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  var menu = document.getElementById("task-view-export-menu");
  if (menu) menu.classList.remove("open");
}

// T10: Saved views menu
function _toggleViewsMenu() {
  var menu = document.getElementById("task-view-views-menu");
  if (!menu) return;
  _renderSavedViewsList();
  menu.classList.toggle("open");
  if (menu.classList.contains("open")) {
    setTimeout(function() {
      document.addEventListener("click", _closeViewsMenuOutside, true);
    }, 0);
  }
}
function _closeViewsMenuOutside(e) {
  var wrap = document.querySelector(".views-menu-wrap");
  if (wrap && !wrap.contains(e.target)) {
    var menu = document.getElementById("task-view-views-menu");
    if (menu) menu.classList.remove("open");
    document.removeEventListener("click", _closeViewsMenuOutside, true);
  }
}
function _getSavedViews() {
  var proj = projectsCache.find(function(p) { return String(p.project_id || "") === String(selectedProjectId || ""); });
  if (proj && proj.settings && Array.isArray(proj.settings.saved_views)) return proj.settings.saved_views;
  return [];
}
function _renderSavedViewsList() {
  var list = document.getElementById("task-view-views-list");
  if (!list) return;
  var views = _getSavedViews();
  if (!views.length) {
    list.innerHTML = '<div class="views-menu-empty" data-i18n="queue.view.views_empty">' + t("queue.view.views_empty") + '</div>';
    return;
  }
  list.innerHTML = "";
  views.forEach(function(v) {
    var row = document.createElement("div");
    row.className = "views-menu-item";
    var nameSpan = document.createElement("span");
    nameSpan.textContent = v.name || v.id;
    row.appendChild(nameSpan);
    var delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "views-delete-btn";
    delBtn.textContent = "✕";
    delBtn.title = t("queue.view.views_delete");
    delBtn.addEventListener("click", function(e) {
      e.stopPropagation();
      _deleteSavedView(v.id);
    });
    row.appendChild(delBtn);
    row.addEventListener("click", function() { _applySavedView(v); });
    list.appendChild(row);
  });
  var saveBtn = document.getElementById("task-view-views-save");
  if (saveBtn) {
    saveBtn.disabled = !selectedProjectId;
    saveBtn.style.opacity = selectedProjectId ? "1" : "0.4";
  }
}
async function _saveCurrentView() {
  if (!selectedProjectId) {
    alert(t("queue.view.views_no_project"));
    return;
  }
  var name = prompt(t("queue.view.views_name_label"));
  if (!name || !name.trim()) return;
  var p = taskViewPrefs || DEFAULT_TASK_VIEW;
  var view = {
    id: "sv_" + Date.now().toString(36),
    name: name.trim(),
    query: {
      group_by: p.groupBy || "molecule",
      sort: p.sort || "created_desc",
      statuses: (p.filters && p.filters.status) || [],
      workflows: (p.filters && p.filters.workflow) || [],
      molecule_keys: (p.filters && p.filters.molecule) || [],
      tags: (p.filters && p.filters.tag) || [],
      batch_ids: (p.filters && p.filters.batch) || [],
      remarks: (p.filters && p.filters.remark) || [],
      search: p.q || "",
      archived: p.archived || "exclude",
      running_first: !!p.runningFirst
    },
    created_at: new Date().toISOString()
  };
  var views = _getSavedViews();
  views.push(view);
  try {
    await api("/projects/" + encodeURIComponent(selectedProjectId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: { saved_views: views } })
    });
    _refreshProjectsCache();
    var menu = document.getElementById("task-view-views-menu");
    if (menu) menu.classList.remove("open");
  } catch (e) {
    console.error("Save view failed:", e);
  }
}
function _applySavedView(view) {
  if (!view || !view.query) return;
  var q = view.query;
  var next = _clonePrefs();
  next.groupBy = q.group_by || next.groupBy;
  next.sort = q.sort || next.sort;
  next.q = q.search || "";
  next.archived = q.archived || "exclude";
  next.runningFirst = !!q.running_first;
  next.filters = next.filters || {};
  next.filters.status = q.statuses || [];
  next.filters.workflow = q.workflows || [];
  next.filters.molecule = q.molecule_keys || [];
  next.filters.tag = q.tags || [];
  next.filters.batch = q.batch_ids || [];
  next.filters.remark = q.remarks || [];
  taskViewPrefs = next;
  saveTaskViewPrefs();
  refreshJobs();
  var menu = document.getElementById("task-view-views-menu");
  if (menu) menu.classList.remove("open");
}
async function _deleteSavedView(viewId) {
  if (!selectedProjectId) return;
  var views = _getSavedViews().filter(function(v) { return v.id !== viewId; });
  try {
    await api("/projects/" + encodeURIComponent(selectedProjectId), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: { saved_views: views } })
    });
    _refreshProjectsCache();
    _renderSavedViewsList();
  } catch (e) {
    console.error("Delete view failed:", e);
  }
}
async function _refreshProjectsCache() {
  try {
    var resp = await api("/projects");
    if (resp && resp.projects) projectsCache = resp.projects;
  } catch (e) {}
}

var _mergeSuggestionDismissed = {};

function _renderMergeSuggestions(groups) {
  if (!Array.isArray(groups) || groups.length < 2) return;
  var container = document.getElementById("queue-list");
  if (!container) return;
  var projectTags = _mergeSuggestionCache;
  if (!Array.isArray(projectTags) || !projectTags.length) return;
  var visibleKeys = {};
  groups.forEach(function(g) { if (g && g.key) visibleKeys[g.key] = true; });
  projectTags.forEach(function(pair) {
    if (!pair || !pair.a || !pair.b) return;
    var dismissKey = pair.a + "::" + pair.b;
    if (_mergeSuggestionDismissed[dismissKey]) return;
    if (!visibleKeys[pair.a] && !visibleKeys[pair.b]) return;
    var banner = document.createElement("div");
    banner.className = "merge-suggestion-banner";
    banner.innerHTML = "<span>" + _escHtml(pair.a) + " / " + _escHtml(pair.b) + " " + t("queue.view.merge_suggestion") + "</span>";
    var action = document.createElement("span");
    action.className = "merge-suggestion-action";
    action.textContent = t("queue.view.merge_to");
    action.addEventListener("click", function() { _executeMergeSuggestion(pair); });
    banner.appendChild(action);
    var dismiss = document.createElement("span");
    dismiss.className = "merge-suggestion-dismiss";
    dismiss.textContent = "✕";
    dismiss.addEventListener("click", function() {
      _mergeSuggestionDismissed[dismissKey] = true;
      banner.remove();
    });
    banner.appendChild(dismiss);
    container.insertBefore(banner, container.firstChild);
  });
}

var _mergeSuggestionCache = [];
var _mergeSuggestionProject = "";
var _mergeSuggestionLoadedAt = 0;

async function _loadMergeSuggestions() {
  var pid = _getSelectedProjectId();
  if (!pid) { _mergeSuggestionCache = []; return; }
  if (pid === _mergeSuggestionProject && Date.now() - _mergeSuggestionLoadedAt < 30000) return;
  try {
    var resp = await apiV2("/projects/" + encodeURIComponent(pid) + "/molecule-groups/suggestions");
    if (pid !== _getSelectedProjectId()) return;
    _mergeSuggestionProject = pid;
    _mergeSuggestionLoadedAt = Date.now();
    _mergeSuggestionCache = Array.isArray(resp) ? resp : (resp && Array.isArray(resp.suggestions) ? resp.suggestions : []);
  } catch (e) {
    _mergeSuggestionCache = [];
  }
}

async function _executeMergeSuggestion(pair) {
  if (!pair || !pair.a || !pair.b) return;
  var target = pair.a;
  var aliasKeys = [pair.b];
  var pid = _getSelectedProjectId();
  if (!pid) return;
  try {
    await apiV2("/projects/" + encodeURIComponent(pid) + "/molecule-groups/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias_keys: aliasKeys, target_key: target })
    });
    _mergeSuggestionDismissed[pair.a + "::" + pair.b] = true;
    appendLogLine("done", pair.a + " ← " + pair.b + " merged");
    await refreshJobs();
  } catch (e) {
    appendLogLine("stderr", "merge failed: " + (e.message || e));
  }
}

async function _openTagManagement() {
  var pid = _getSelectedProjectId();
  if (!pid) { window.alert("No project selected"); return; }
  var body = document.getElementById("tag-mgmt-list");
  var empty = document.getElementById("tag-mgmt-empty");
  if (body) body.innerHTML = "";
  document.getElementById("tag-mgmt-modal").style.display = "flex";
  _switchMgmtTab("tags");
  try {
    var tags = await apiV2("/projects/" + encodeURIComponent(pid) + "/tags");
    var tagList = Array.isArray(tags) ? tags : (tags && Array.isArray(tags.tags) ? tags.tags : []);
    if (!tagList.length) {
      if (empty) empty.style.display = "";
      return;
    }
    if (empty) empty.style.display = "none";
    tagList.forEach(function(item) {
      var row = document.createElement("div");
      row.className = "tag-mgmt-item";
      var nameEl = document.createElement("span");
      nameEl.className = "tag-mgmt-name";
      nameEl.textContent = item.tag || item.name || "";
      row.appendChild(nameEl);
      var countEl = document.createElement("span");
      countEl.className = "tag-mgmt-count";
      countEl.textContent = item.count || 0;
      row.appendChild(countEl);
      var actions = document.createElement("span");
      actions.className = "tag-mgmt-actions";
      var renameBtn = document.createElement("button");
      renameBtn.className = "mini-btn";
      renameBtn.textContent = t("queue.view.tag_rename");
      renameBtn.addEventListener("click", function() { _renameTag(pid, item.tag || item.name); });
      actions.appendChild(renameBtn);
      var mergeBtn = document.createElement("button");
      mergeBtn.className = "mini-btn";
      mergeBtn.textContent = t("queue.view.tag_merge");
      mergeBtn.addEventListener("click", function() { _mergeTag(pid, item.tag || item.name); });
      actions.appendChild(mergeBtn);
      var deleteBtn = document.createElement("button");
      deleteBtn.className = "mini-btn";
      deleteBtn.style.color = "var(--red)";
      deleteBtn.textContent = t("queue.view.tag_delete");
      deleteBtn.addEventListener("click", function() { _deleteTag(pid, item.tag || item.name); });
      actions.appendChild(deleteBtn);
      row.appendChild(actions);
      body.appendChild(row);
    });
  } catch (e) {
    if (empty) { empty.style.display = ""; empty.textContent = "Error: " + (e.message || e); }
  }
}

function _closeTagManagement() {
  document.getElementById("tag-mgmt-modal").style.display = "none";
}

async function _renameTag(pid, tagName) {
  var newName = window.prompt("Rename '" + tagName + "' to:", tagName);
  if (!newName || newName === tagName) return;
  try {
    await apiV2("/projects/" + encodeURIComponent(pid) + "/tags/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: tagName, target: newName })
    });
    appendLogLine("done", tagName + " → " + newName);
    await _openTagManagement();
    await refreshJobs();
  } catch (e) {
    appendLogLine("stderr", "rename failed: " + (e.message || e));
  }
}

async function _mergeTag(pid, tagName) {
  var target = window.prompt("Merge '" + tagName + "' into (target tag name):");
  if (!target || target === tagName) return;
  try {
    await apiV2("/projects/" + encodeURIComponent(pid) + "/tags/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sources: [tagName], target: target })
    });
    appendLogLine("done", tagName + " → " + target + " merged");
    await _openTagManagement();
    await refreshJobs();
  } catch (e) {
    appendLogLine("stderr", "merge failed: " + (e.message || e));
  }
}

async function _deleteTag(pid, tagName) {
  if (!window.confirm(t("queue.view.tag_delete_warn") + "\n\n'" + tagName + "'")) return;
  try {
    await apiV2("/projects/" + encodeURIComponent(pid) + "/tags/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag: tagName })
    });
    appendLogLine("done", tagName + " deleted");
    await _openTagManagement();
    await refreshJobs();
  } catch (e) {
    appendLogLine("stderr", "delete failed: " + (e.message || e));
  }
}

var _autoTagRulesCache = [];

function _switchMgmtTab(tab) {
  var tagsPanel = document.getElementById("tag-mgmt-panel-tags");
  var autoPanel = document.getElementById("tag-mgmt-panel-auto");
  var tagsTab = document.getElementById("tag-mgmt-tab-tags");
  var autoTab = document.getElementById("tag-mgmt-tab-auto");
  if (tab === "tags") {
    if (tagsPanel) tagsPanel.style.display = "";
    if (autoPanel) autoPanel.style.display = "none";
    if (tagsTab) tagsTab.classList.add("active");
    if (autoTab) autoTab.classList.remove("active");
  } else {
    if (tagsPanel) tagsPanel.style.display = "none";
    if (autoPanel) autoPanel.style.display = "";
    if (tagsTab) tagsTab.classList.remove("active");
    if (autoTab) autoTab.classList.add("active");
    _loadAutoTagRules();
  }
}

async function _loadAutoTagRules() {
  var pid = _getSelectedProjectId();
  if (!pid) return;
  var listEl = document.getElementById("auto-tag-rules-list");
  var emptyEl = document.getElementById("auto-tag-rules-empty");
  if (listEl) listEl.innerHTML = "";
  try {
    var project = await api("/projects/" + encodeURIComponent(pid));
    var settings = project.settings || {};
    var rules = settings.auto_tag_rules || [];
    _autoTagRulesCache = rules;
    if (!rules.length) {
      if (emptyEl) emptyEl.style.display = "";
      return;
    }
    if (emptyEl) emptyEl.style.display = "none";
    rules.forEach(function(rule, idx) {
      var row = document.createElement("div");
      row.className = "auto-tag-rule";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.className = "rule-toggle";
      cb.checked = rule.enabled !== false;
      cb.addEventListener("change", function() { _toggleAutoTagRule(pid, idx, cb.checked); });
      row.appendChild(cb);
      var desc = document.createElement("span");
      desc.className = "rule-desc";
      var fieldLabel = t("queue.view.auto_tag_field_" + (rule.field === "molecule_name" ? "molecule" : rule.field));
      var opLabel = t("queue.view.auto_tag_op_" + rule.op);
      desc.textContent = fieldLabel + " " + opLabel + " \"" + (rule.value || "") + "\"";
      var tagSpan = document.createElement("span");
      tagSpan.className = "rule-tag";
      tagSpan.textContent = " → " + (rule.tag || "");
      desc.appendChild(tagSpan);
      row.appendChild(desc);
      var actions = document.createElement("span");
      actions.className = "tag-mgmt-actions";
      var delBtn = document.createElement("button");
      delBtn.className = "mini-btn";
      delBtn.style.color = "var(--red)";
      delBtn.textContent = "×";
      delBtn.addEventListener("click", function() { _deleteAutoTagRule(pid, idx); });
      actions.appendChild(delBtn);
      row.appendChild(actions);
      listEl.appendChild(row);
    });
  } catch (e) {
    if (emptyEl) { emptyEl.style.display = ""; emptyEl.textContent = "Error: " + (e.message || e); }
  }
}

async function _addAutoTagRule(pid) {
  var field = document.getElementById("auto-tag-field").value;
  var op = document.getElementById("auto-tag-op").value;
  var value = document.getElementById("auto-tag-value").value.trim();
  var tag = document.getElementById("auto-tag-tag").value.trim();
  if (!value || !tag) { window.alert(t("queue.view.auto_tag_empty")); return; }
  var rules = (_autoTagRulesCache || []).slice();
  rules.push({ id: "atr_" + Date.now().toString(36), field: field, op: op, value: value, tag: tag, enabled: true });
  try {
    await api("/projects/" + encodeURIComponent(pid), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: { auto_tag_rules: rules } })
    });
    document.getElementById("auto-tag-value").value = "";
    document.getElementById("auto-tag-tag").value = "";
    await _loadAutoTagRules();
  } catch (e) {
    appendLogLine("stderr", "add rule failed: " + (e.message || e));
  }
}

async function _toggleAutoTagRule(pid, idx, enabled) {
  var rules = (_autoTagRulesCache || []).slice();
  if (!rules[idx]) return;
  rules[idx] = Object.assign({}, rules[idx], { enabled: enabled });
  try {
    await api("/projects/" + encodeURIComponent(pid), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: { auto_tag_rules: rules } })
    });
    await _loadAutoTagRules();
  } catch (e) {
    appendLogLine("stderr", "toggle rule failed: " + (e.message || e));
  }
}

async function _deleteAutoTagRule(pid, idx) {
  var rules = (_autoTagRulesCache || []).slice();
  if (!rules[idx]) return;
  rules.splice(idx, 1);
  try {
    await api("/projects/" + encodeURIComponent(pid), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: { auto_tag_rules: rules } })
    });
    await _loadAutoTagRules();
  } catch (e) {
    appendLogLine("stderr", "delete rule failed: " + (e.message || e));
  }
}

async function _applyAutoTagRulesBackfill() {
  var pid = _getSelectedProjectId();
  if (!pid) return;
  if (!window.confirm(t("queue.view.auto_tag_confirm_apply"))) return;
  try {
    var result = await apiV2("/projects/" + encodeURIComponent(pid) + "/auto-tag-rules/apply", { method: "POST" });
    var count = result && result.updated ? result.updated : 0;
    appendLogLine("done", t("queue.view.auto_tag_applied", { count: count }));
    await refreshJobs();
  } catch (e) {
    appendLogLine("stderr", "backfill failed: " + (e.message || e));
  }
}

var _lineageCache = {};

async function _loadAndRenderLineage(jobId) {
  var body = document.getElementById("lineage-body");
  if (!body) return;
  body.innerHTML = "";
  if (_lineageCache[jobId]) {
    _renderLineageData(body, _lineageCache[jobId]);
    return;
  }
  try {
    var data = await apiV2("/tasks/" + encodeURIComponent(jobId) + "/lineage");
    _lineageCache[jobId] = data;
    _renderLineageData(body, data);
  } catch (e) {
    body.innerHTML = '<div style="font-size:11px;color:var(--text-tertiary);padding:4px 0;">Error loading lineage</div>';
  }
}

function _renderLineageData(container, data) {
  var upstream = (data && Array.isArray(data.upstream)) ? data.upstream : [];
  var downstream = (data && Array.isArray(data.downstream)) ? data.downstream : [];
  if (!upstream.length && !downstream.length) {
    container.innerHTML = "";
    return;
  }
  if (upstream.length) {
    var subTitle = document.createElement("div");
    subTitle.className = "lineage-subtitle";
    subTitle.textContent = t("queue.view.lineage_upstream");
    container.appendChild(subTitle);
    upstream.forEach(function(node) {
      container.appendChild(_buildLineageNode(node));
    });
  }
  if (downstream.length) {
    var subTitle2 = document.createElement("div");
    subTitle2.className = "lineage-subtitle";
    subTitle2.style.marginTop = "8px";
    subTitle2.textContent = t("queue.view.lineage_downstream");
    container.appendChild(subTitle2);
    downstream.forEach(function(node) {
      container.appendChild(_buildLineageNode(node));
    });
  }
}

function _buildLineageNode(node) {
  var el = document.createElement("div");
  el.className = "lineage-node";
  el.style.paddingLeft = ((node.depth || 0) * 16 + 4) + "px";
  var depthSpan = document.createElement("span");
  depthSpan.className = "lineage-depth";
  depthSpan.textContent = node.depth != null ? "D" + node.depth : "";
  el.appendChild(depthSpan);
  var nameSpan = document.createElement("span");
  nameSpan.className = "lineage-name";
  nameSpan.textContent = node.molecule_name || node.task_name || node.task_id || "";
  nameSpan.title = node.task_id || "";
  el.appendChild(nameSpan);
  if (node.relation) {
    var relSpan = document.createElement("span");
    relSpan.className = "lineage-relation";
    relSpan.textContent = node.relation;
    el.appendChild(relSpan);
  }
  if (node.status) {
    var statusSpan = document.createElement("span");
    statusSpan.className = "lineage-status";
    statusSpan.textContent = node.status;
    el.appendChild(statusSpan);
  }
  el.addEventListener("click", function() {
    if (node.task_id) selectJob(node.task_id);
  });
  return el;
}

function _escHtml(s) {
  var d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// Keep the desktop and mobile twin checkboxes of one job in sync.
function syncQueueRowCheckboxes(jobId, checked) {
  document.querySelectorAll(".queue-row").forEach(function(row) {
    if (row.getAttribute("data-job-id") === String(jobId)) {
      const cb = row.querySelector(".queue-check");
      if (cb) cb.checked = checked;
    }
  });
}

function openPurgeModal() {
  if (!queueSelectedJobs.size) {
    window.alert(t("purge.none_selected"));
    return;
  }
  const promptEl = document.getElementById("purge-prompt");
  if (promptEl) promptEl.textContent = t("purge.prompt", { count: queueSelectedJobs.size });
  document.getElementById("purge-data").checked = true;
  const err = document.getElementById("purge-err");
  if (err) err.style.display = "none";
  document.getElementById("purge-modal").style.display = "flex";
}

function closePurgeModal() {
  document.getElementById("purge-modal").style.display = "none";
}

// Tolerant per-job report parser: accepts a results[] of {job_id, ok|success|
// status, error|reason} entries, {purged|deleted|count} counters, or an
// unknown shape (assumes the requested ids were purged).
function summarizePurgeReport(resp, requestedIds) {
  let okCount = 0;
  let failCount = 0;
  const errors = [];
  let results = null;
  if (Array.isArray(resp)) results = resp;
  else if (resp && Array.isArray(resp.results)) results = resp.results;
  else if (resp && Array.isArray(resp.report)) results = resp.report;
  else if (resp && Array.isArray(resp.jobs)) results = resp.jobs;
  if (results) {
    results.forEach(function(entry) {
      if (!entry || typeof entry !== "object") return;
      const st = String(entry.status || "").toLowerCase();
      const okFlag = entry.ok === true || entry.success === true ||
        st === "purged" || st === "deleted" || st === "ok" || st === "completed";
      if (okFlag) { okCount += 1; return; }
      failCount += 1;
      const reason = entry.error || entry.reason || entry.message || "";
      if (reason) errors.push(String(reason));
    });
  } else if (resp && (typeof resp.purged === "number" || typeof resp.deleted === "number" || typeof resp.count === "number")) {
    okCount = resp.purged != null ? resp.purged : (resp.deleted != null ? resp.deleted : resp.count);
    failCount = Math.max(0, requestedIds.length - okCount);
  } else {
    okCount = requestedIds.length;
  }
  let text = failCount ? t("purge.report", { ok: okCount, fail: failCount }) : t("purge.report_ok", { count: okCount });
  if (errors.length) text += "\n" + errors.slice(0, 3).join("\n");
  return { okCount: okCount, failCount: failCount, text: text };
}

async function submitPurgeModal() {
  const ids = Array.from(queueSelectedJobs);
  if (!ids.length) return;
  const deleteData = document.getElementById("purge-data").checked;
  const errEl = document.getElementById("purge-err");
  const submitBtn = document.getElementById("purge-modal-submit");
  submitBtn.disabled = true;
  try {
    const resp = await ACPClient.jobCommands.purge({ jobIds: ids, deleteData: deleteData === true });
    closePurgeModal();
    queueSelectedJobs.clear();
    updateQueueBatchBar();
    ids.forEach(function(id) { invalidateJobDetail(id); });
    if (selectedJobId && ids.indexOf(String(selectedJobId)) !== -1) clearStaleJobSelection();
    const report = summarizePurgeReport(resp, ids);
    if (report.failCount) window.alert(report.text);
    else appendLogLine("done", report.text);
    await refreshJobs();
  } catch (e) {
    if (errEl) {
      errEl.textContent = t("purge.failed", { message: e.message });
      errEl.style.display = "block";
    }
  } finally {
    submitBtn.disabled = false;
  }
}

function makeQueueIconButton(glyph, titleText, extraClass, onClick) {
  const btn = document.createElement("button");
  btn.className = "mini-btn icon-btn" + (extraClass ? " " + extraClass : "");
  btn.textContent = glyph;
  btn.title = titleText;
  btn.addEventListener("click", function(event) {
    event.stopPropagation();
    onClick(btn);
  });
  return btn;
}

// Inline row buttons: at most 1–2 high-frequency actions per status
// (running: pause + cancel; paused: resume; terminal: rerun). The cached
// recovery matrix wins when present; status-based defaults otherwise.
// Rename pencil: visible on hover/focus-within (CSS), all statuses.
function appendQueueInlineActions(actions, job) {
  const status = String(job.status || "").toLowerCase();
  const jobId = String(job.id || job.job_id || "");
  // Rename button — always available, shown on hover/focus-within via CSS
  actions.appendChild(makeQueueIconButton("\u270e", t("queue.rename"), "btn-rename-task", function() { openTaskRenameModal(job); }));
  const recovery = resolveJobRecovery(job);
  // GAP-9: terminal rows never show active-only actions (pause/resume/cancel)
  // — the floor matches server _compute_recovery (active flags only for
  // active statuses) and only diverges from a stale cache.  A fresh server
  // recovery still decides rerun/continue/purge capability.
  const activeOnly = !isTerminalJobStatus(status);
  if (activeOnly && recovery.can_pause) {
    actions.appendChild(makeQueueIconButton("‖", t("job.pause"), "warn", function() { pauseJob(jobId); }));
  }
  if (activeOnly && recovery.can_unpause) {
    actions.appendChild(makeQueueIconButton("▶", t("job.resume"), "", function() { unpauseJob(jobId); }));
  }
  if (activeOnly && status === "running" && recovery.can_cancel) {
    actions.appendChild(makeQueueIconButton("✕", t("job.cancel"), "danger", function() { cancelJob(jobId); }));
  }
  if (recovery.can_continue) {
    actions.appendChild(makeQueueIconButton("\u23f5", t("job.continue"), "", function() { continueJob(jobId); }));
  }
  if (recovery.can_rerun) {
    actions.appendChild(makeQueueIconButton("↻", t("job.rerun"), "", function(btn) {
      openRerunActionMenu(job, btn);
    }));
  }
}

// ↻ opens the lightweight rerun action menu (直接重跑 / 修改参数后重算 /
// 复制为新任务) — server capability projection decides availability inside
// ACPJobEditor.openRerunMenu; keyboard and touch both reach it via the
// button's click path, and menu clicks never select/expand the card.
function openRerunActionMenu(job, btn) {
  if (window.ACPJobEditor && ACPJobEditor.openRerunMenu) {
    ACPJobEditor.openRerunMenu(job, btn);
  } else {
    rerunJob(String(job.id || job.job_id || ""), btn);
  }
}

// ── Task rename modal ─────────────────────────────────────────────────
var _renameState = { job: null, triggerEl: null, revision: 0 };

function openTaskRenameModal(job) {
  if (!job) return;
  var jobId = String(job.id || job.job_id || "");
  if (!jobId) return;
  _renameState.job = job;
  _renameState.triggerEl = document.activeElement;
  _renameState.revision = job.name_revision || 0;
  var input = document.getElementById("task-rename-input");
  var current = job.custom_name || "";
  input.value = current;
  var defName = job.default_name || canonicalTaskName(job) || "";
  var defEl = document.getElementById("task-rename-default");
  defEl.textContent = t("task.rename.default_label", { name: defName });
  defEl.style.display = defName ? "" : "none";
  var restoreBtn = document.getElementById("task-rename-restore");
  restoreBtn.disabled = !current;
  var saveBtn = document.getElementById("task-rename-save");
  saveBtn.disabled = true;
  var errEl = document.getElementById("task-rename-err");
  errEl.style.display = "none";
  errEl.textContent = "";
  document.getElementById("task-rename-modal").style.display = "flex";
  setTimeout(function() { input.focus(); input.select(); }, 50);
}

function closeTaskRenameModal() {
  document.getElementById("task-rename-modal").style.display = "none";
  if (_renameState.triggerEl && _renameState.triggerEl.isConnected) {
    try { _renameState.triggerEl.focus(); } catch (e) { /* ignore */ }
  }
  _renameState.job = null;
}

function _validateRenameInput(raw) {
  var v = String(raw || "").trim();
  if (!v || v.length > 200) return null;
  if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\n\r]/.test(v)) return null;
  return v;
}

function _updateRenameSaveState() {
  var input = document.getElementById("task-rename-input");
  var saveBtn = document.getElementById("task-rename-save");
  var job = _renameState.job;
  if (!job) { saveBtn.disabled = true; return; }
  var trimmed = String(input.value || "").trim();
  var current = job.custom_name || "";
  var valid = _validateRenameInput(input.value) !== null || trimmed === "";
  // Enable save when: valid text differs from current, OR "restore default" (empty when current is set)
  saveBtn.disabled = !valid || (trimmed === current);
}

async function submitTaskRenameNow(customName) {
  var job = _renameState.job;
  if (!job) return;
  var jobId = String(job.id || job.job_id || "");
  var errEl = document.getElementById("task-rename-err");
  var saveBtn = document.getElementById("task-rename-save");
  var restoreBtn = document.getElementById("task-rename-restore");
  saveBtn.disabled = true;
  restoreBtn.disabled = true;
  errEl.style.display = "none";
  errEl.textContent = "";
  try {
    var result = await ACPClient.jobCommands.rename(jobId, customName, _renameState.revision);
    // Update in-memory job object so re-render shows new name without reload
    _applyRenameResult(job, result);
    closeTaskRenameModal();
    // Re-render affected UI in place
    _refreshAfterRename(job);
  } catch (e) {
    var msg = String(e.message || "");
    // 409 conflict: refresh revision from error detail
    if (msg.indexOf("name_revision_conflict") !== -1 || msg.indexOf("409") !== -1) {
      try {
        var parsed = JSON.parse(msg.substring(msg.indexOf("{")));
        if (parsed && parsed.current) {
          _renameState.revision = parsed.current.name_revision || 0;
          _renameState.job.name_revision = parsed.current.name_revision;
          _renameState.job.custom_name = parsed.current.custom_name || "";
          _renameState.job.resolved_name = parsed.current.resolved_name || "";
          _renameState.job.default_name = parsed.current.default_name || "";
        }
      } catch (e2) { /* ignore parse failure */ }
      errEl.textContent = t("task.rename.conflict");
    } else {
      errEl.textContent = t("task.rename.failed", { message: msg });
    }
    errEl.style.display = "block";
    saveBtn.disabled = false;
    restoreBtn.disabled = !(_renameState.job && _renameState.job.custom_name);
  }
}

// Shallow-copy rename fields onto one target. MUST stay non-recursive:
// _applyRenameResult fans out to job / jobsCache / lastSelectedJob itself —
// self-recursion there was the bb3cdb7 RangeError regression (fixed 2026-09-19).
function _applyRenameFields(target, result) {
  if ("custom_name" in result) target.custom_name = result.custom_name;
  if ("resolved_name" in result) target.resolved_name = result.resolved_name;
  if ("default_name" in result) target.default_name = result.default_name;
  if ("name_revision" in result) target.name_revision = result.name_revision;
  if ("name_updated_at" in result) target.name_updated_at = result.name_updated_at;
}

function _applyRenameResult(job, result) {
  if (!result || typeof result !== "object") return;
  _applyRenameFields(job, result);
  // Also update in jobsCache
  var jobId = String(job.id || job.job_id || "");
  for (var i = 0; i < jobsCache.length; i++) {
    if (String(jobsCache[i].id || jobsCache[i].job_id || "") === jobId) {
      _applyRenameFields(jobsCache[i], result);
      break;
    }
  }
  // Update lastSelectedJob if same
  if (lastSelectedJob && String(lastSelectedJob.id || lastSelectedJob.job_id || "") === jobId) {
    _applyRenameFields(lastSelectedJob, result);
  }
}

function _refreshAfterRename(job) {
  var jobId = String(job.id || job.job_id || "");
  invalidateJobDetail(jobId);
  fetchJobDetail(jobId).catch(function() { /* status defaults carry the row until refetched */ });
  // Update detail panel title if this job is selected
  var vTaskName = document.getElementById("v-task-name");
  if (vTaskName && String(selectedJobId) === jobId) {
    vTaskName.textContent = displayTaskName(job) || String(job.id || "");
    vTaskName.title = vTaskName.textContent;
  }
  // Update file tree panel title
  updateFileTreeTaskName(jobId);
  // Re-render queue without full reload (preserves scroll/selection)
  var queueList = document.getElementById("queue-list");
  if (queueList) renderQueueList(queueList);
  var mobileList = document.getElementById("mobile-queue-list");
  if (mobileList) renderQueueList(mobileList);
}

// Wire up modal events
(function() {
  var modal = document.getElementById("task-rename-modal");
  if (!modal) return;
  var input = document.getElementById("task-rename-input");
  var saveBtn = document.getElementById("task-rename-save");
  var cancelBtn = document.getElementById("task-rename-cancel");
  var closeBtn = document.getElementById("task-rename-modal-close");
  var _renameRestoreBtn = document.getElementById("task-rename-restore");

  input.addEventListener("input", _updateRenameSaveState);
  input.addEventListener("keydown", function(event) {
    if (event.key === "Enter" && !saveBtn.disabled) {
      event.preventDefault();
      var trimmed = String(input.value || "").trim();
      if (trimmed === "" && _renameState.job && _renameState.job.custom_name) {
        submitTaskRenameNow(null);
      } else if (trimmed !== "") {
        submitTaskRenameNow(trimmed);
      }
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeTaskRenameModal();
    }
  });
  saveBtn.addEventListener("click", function() {
    var trimmed = String(input.value || "").trim();
    if (trimmed === "" && _renameState.job && _renameState.job.custom_name) {
      submitTaskRenameNow(null);
    } else if (trimmed !== "") {
      submitTaskRenameNow(trimmed);
    }
  });
  cancelBtn.addEventListener("click", closeTaskRenameModal);
  closeBtn.addEventListener("click", closeTaskRenameModal);
  _renameRestoreBtn.addEventListener("click", function() {
    submitTaskRenameNow(null);
  });
  // Close on overlay click
  modal.addEventListener("click", function(event) {
    if (event.target === modal) closeTaskRenameModal();
  });
  // Esc on modal itself
  modal.addEventListener("keydown", function(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeTaskRenameModal();
    }
  });
})();

async function submitTargetProjectModal() {
  if (!targetProjectJobId) return;
  const select = document.getElementById("target-project-select");
  const projectId = select.value;
  const action = targetProjectAction;
  const submitBtn = document.getElementById("target-project-modal-submit");
  submitBtn.disabled = true;
    try {
      if (action === "clone") {
        await ACPClient.jobCommands.clone(targetProjectJobId, projectId);
      } else {
        await ACPClient.jobCommands.move(targetProjectJobId, projectId);
      }
      closeTargetProjectModal();
      await refreshJobs();
    } catch (e) {
    window.alert(t(action === "clone" ? "clone.failed" : "move.failed", { message: e.message }) || (action === "clone" ? "复制失败" : "移动失败") + ": " + e.message);
  } finally {
    submitBtn.disabled = false;
  }
}

async function submitProjectModal() {
  const nameInput = document.getElementById("project-modal-name");
  const descInput = document.getElementById("project-modal-desc");
  const errEl = document.getElementById("project-modal-name-err");
  const name = nameInput.value.trim();

  if (!name) {
    errEl.textContent = t("project.name_required") || "请输入项目名称";
    errEl.style.display = "block";
    nameInput.focus();
    return;
  }
  if (projectsCache.some(function(p) { return p.name === name; })) {
    errEl.textContent = t("project.name_exists") || "项目名称已存在";
    errEl.style.display = "block";
    nameInput.focus();
    return;
  }
  errEl.style.display = "none";

  const submitBtn = document.getElementById("project-modal-submit");
  submitBtn.disabled = true;
  try {
    const result = await api("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        description: descInput.value.trim()
      })
    });
    closeProjectModal();
    await loadProjects();
    if (result && result.project_id) {
      selectedProjectId = result.project_id;
      document.getElementById("project-select").value = selectedProjectId;
      await refreshJobs();
    }
  } catch (e) {
    errEl.textContent = e.message;
    errEl.style.display = "block";
  } finally {
    submitBtn.disabled = false;
  }
}

async function loadProjects() {
  try {
    const body = await api("/projects");
    projectsCache = body.projects || [];
    projectsLoaded = true;
    renderProjects();
    setWorkbenchLoadError("Projects", null);
  } catch (e) {
    if (!projectsLoaded) {
      var select = document.getElementById("project-select");
      select.options[0].textContent = t("status.offline");
    }
    setWorkbenchLoadError("Projects", e, loadProjects);
    throw e;
  }
}

var taskListControllerReady = false;
function configureTaskListController() {
  if (taskListControllerReady) return;
  ACPWorkbench.controllers.taskList.init({
    client: ACPClient.taskView,
    contract: ACPWorkbench.contracts.taskView,
    getPrefs: function () { return taskViewPrefs || DEFAULT_TASK_VIEW; },
    getSelectedProjectId: function () { return selectedProjectId; },
    getLanguage: function () { return currentLang; },
    detailCacheRevision: function () { return jobDetailCacheRevision; },
    renderKeyOf: JSON.stringify,
    views: {
      onError: function (error) { setWorkbenchLoadError("Tasks", error, refreshJobs); },
      onEmptyError: function () {
        ["queue-counts", "mobile-queue-counts"].forEach(function (id) {
          var host = document.getElementById(id);
          if (host) host.textContent = t("queue.load_failed");
        });
      },
      sortGroups: function (body, prefs) {
        body.groups.forEach(function (group) { group.jobs = sortTaskRows(group.jobs); });
        var sortMode = prefs.sort || "created_desc";
        if (sortMode === "name_asc" || sortMode === "name_desc") {
          body.groups.sort(function (a, b) {
            var c = _taskCollator.compare(a.display_name || a.key || "", b.display_name || b.key || "");
            if (sortMode === "name_desc") c = -c;
            return c || String(a.key || "").localeCompare(String(b.key || ""));
          });
        }
      },
      onRows: function (rows, body) {
        taskViewCache = body;
        jobsCache = rows;
        taskViewOffset = body.offset;
      },
      pruneDetailsAgainstList: pruneJobDetailCacheAgainstList,
      renderQueue: renderQueue,
      renderPagination: renderTaskPagination,
      afterRender: function (body, isCurrent) {
        if (selectedJobId) highlightQueueRow(selectedJobId);
        updateMobileStatusBar();
        _applyTaskViewPrefsToControls();
        renderChips();
        if ((taskViewPrefs || DEFAULT_TASK_VIEW).groupBy === "molecule") {
          _loadMergeSuggestions().then(function () {
            if (isCurrent()) _renderMergeSuggestions(body.groups);
          }).catch(function (error) { console.error("Merge suggestions failed", error); });
        }
      },
    },
  });
  taskListControllerReady = true;
}

async function refreshJobs(nextOffset) {
  loadTaskViewPrefs(selectedProjectId);
  configureTaskListController();
  try {
    return await (typeof nextOffset === "number"
      ? ACPWorkbench.controllers.taskList.setOffset(nextOffset)
      : ACPWorkbench.controllers.taskList.refresh());
  } catch (error) {
    if (!ACPWorkbench.controllers.taskList.isSilentCancel(error)) console.error("refreshJobs failed:", error);
  }
}

function renderTaskPagination(body) {
  ["queue-pagination", "mobile-queue-pagination"].forEach(function(id) {
    var host = document.getElementById(id);
    if (!host) return;
    host.replaceChildren();
    if (!body || body.total <= 200) return;
    var label = document.createElement("span");
    label.textContent = t("queue.page", { page: Math.floor(body.offset / 200) + 1, total: body.total });
    host.appendChild(label);
    [["queue.previous", body.offset > 0, Math.max(0, body.offset - 200)],
     ["queue.next", body.next_offset != null, body.next_offset]].forEach(function(spec) {
      var btn = document.createElement("button");
      btn.className = "mini-btn";
      btn.textContent = t(spec[0]);
      btn.disabled = !spec[1];
      btn.addEventListener("click", function() { refreshJobs(spec[2]); });
      host.appendChild(btn);
    });
  });
}

// --- T5: Task view toolbar, filter panel, chips ---
var _taskViewSearchTimer = null;

function scheduleTaskViewSearch() {
  if (_taskViewSearchTimer) clearTimeout(_taskViewSearchTimer);
  _taskViewSearchTimer = setTimeout(function() {
    _taskViewSearchTimer = null;
    var el = document.getElementById("task-view-search");
    var q = el ? el.value.trim() : "";
    var next = _clonePrefs();
    next.q = q;
    taskViewPrefs = next;
    saveTaskViewPrefs();
    refreshJobs();
  }, 300);
}

function _clonePrefs() {
  return JSON.parse(JSON.stringify(taskViewPrefs || DEFAULT_TASK_VIEW));
}

function _applyTaskViewPrefsToControls() {
  var p = taskViewPrefs || DEFAULT_TASK_VIEW;
  var searchEl = document.getElementById("task-view-search");
  if (searchEl && document.activeElement !== searchEl) searchEl.value = p.q || "";
  var groupEl = document.getElementById("task-view-group");
  if (groupEl) groupEl.value = p.groupBy || "molecule";
  var sortEl = document.getElementById("task-view-sort");
  if (sortEl) sortEl.value = p.sort || "created_desc";
  var archivedEl = document.getElementById("task-view-archived");
  if (archivedEl) archivedEl.value = p.archived || "exclude";
  _updateFilterBadge();
}

function _activeFilterCount() {
  var p = taskViewPrefs || DEFAULT_TASK_VIEW;
  var f = p.filters || {};
  var n = 0;
  ["status", "workflow", "molecule", "tag", "batch", "remark"].forEach(function(dim) {
    if (f[dim] && f[dim].length) n += f[dim].length;
  });
  return n;
}

function _updateFilterBadge() {
  var badge = document.getElementById("task-view-filter-badge");
  if (!badge) return;
  var n = _activeFilterCount();
  if (n > 0) {
    badge.textContent = n;
    badge.style.display = "";
  } else {
    badge.style.display = "none";
  }
}

var _TASK_VIEW_FILTER_DIMS = [
  { key: "status", labelKey: "queue.view.filter_status", facetKey: "statuses" },
  { key: "workflow", labelKey: "queue.view.filter_workflow", facetKey: "workflows" },
  { key: "molecule", labelKey: "queue.view.filter_molecule", facetKey: "molecules", nameField: "name" },
  { key: "tag", labelKey: "queue.view.filter_tag", facetKey: "tags", facetItemKey: "tag" },
  { key: "batch", labelKey: "queue.view.filter_batch", facetKey: "batches", facetItemKey: "batch_id" }
];

function renderFilterPanel() {
  var body = document.getElementById("task-view-filter-body");
  if (!body) return;
  body.innerHTML = "";
  var facets = (taskViewCache && taskViewCache.facets) || {};
  var prefs = taskViewPrefs || DEFAULT_TASK_VIEW;
  var hasAny = false;

  _TASK_VIEW_FILTER_DIMS.forEach(function(dim) {
    var facetArr = facets[dim.facetKey];
    if (!facetArr || !facetArr.length) return;
    hasAny = true;
    var group = document.createElement("div");
    group.className = "filter-group";
    var title = document.createElement("div");
    title.className = "filter-group-title";
    title.textContent = t(dim.labelKey);
    group.appendChild(title);

    facetArr.forEach(function(item) {
      var value = dim.facetItemKey ? item[dim.facetItemKey] : (item.key || item);
      var count = item.count || 0;
      var displayName = dim.nameField ? (item[dim.nameField] || value) : value;
      var label = document.createElement("label");
      label.className = "filter-group-item";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = (prefs.filters[dim.key] || []).indexOf(value) >= 0;
      cb.dataset.filterDim = dim.key;
      cb.dataset.filterValue = value;
      cb.addEventListener("change", function() {
        var next = _clonePrefs();
        var arr = next.filters[dim.key] || [];
        if (cb.checked) {
          if (arr.indexOf(value) < 0) arr.push(value);
        } else {
          arr = arr.filter(function(v) { return v !== value; });
        }
        next.filters[dim.key] = arr;
        taskViewPrefs = next;
        saveTaskViewPrefs();
        _updateFilterBadge();
        renderChips();
        refreshJobs();
      });
      label.appendChild(cb);
      var nameSpan = document.createElement("span");
      nameSpan.textContent = displayName;
      label.appendChild(nameSpan);
      var countSpan = document.createElement("span");
      countSpan.className = "filter-count";
      countSpan.textContent = count;
      label.appendChild(countSpan);
      group.appendChild(label);
    });
    body.appendChild(group);
  });

  if (!hasAny) {
    var empty = document.createElement("div");
    empty.className = "filter-no-facets";
    empty.setAttribute("data-i18n", "queue.view.filter_no_facets");
    empty.textContent = t("queue.view.filter_no_facets");
    body.appendChild(empty);
  }

  var mgmtLink = document.createElement("div");
  mgmtLink.className = "tag-mgmt-link";
  mgmtLink.textContent = t("queue.view.manage_tags");
  mgmtLink.addEventListener("click", function(e) {
    e.stopPropagation();
    _closeFilterPanel();
    _openTagManagement();
  });
  body.appendChild(mgmtLink);
}

function _toggleFilterPanel() {
  var panel = document.getElementById("task-view-filter-panel");
  if (!panel) return;
  if (panel.style.display === "none") {
    renderFilterPanel();
    panel.style.display = "";
    // Close on outside click
    setTimeout(function() {
      document.addEventListener("click", _closeFilterPanelOutside, true);
      document.addEventListener("keydown", _closeFilterPanelEsc, true);
    }, 0);
  } else {
    _closeFilterPanel();
  }
}

function _closeFilterPanel() {
  var panel = document.getElementById("task-view-filter-panel");
  if (panel) panel.style.display = "none";
  document.removeEventListener("click", _closeFilterPanelOutside, true);
  document.removeEventListener("keydown", _closeFilterPanelEsc, true);
}

function _closeFilterPanelOutside(e) {
  var panel = document.getElementById("task-view-filter-panel");
  var btn = document.getElementById("task-view-filter-btn");
  if (panel && !panel.contains(e.target) && btn && !btn.contains(e.target)) {
    _closeFilterPanel();
  }
}

function _closeFilterPanelEsc(e) {
  if (e.key === "Escape") _closeFilterPanel();
}

var _CHIP_DIM_LABELS = {
  status: function(v) { return translateStatus(v); },
  workflow: function(v) { return v; },
  molecule: function(v) { return v; },
  tag: function(v) { return v; },
  batch: function(v) { return v; },
  remark: function(v) { return v; }
};

function renderChips() {
  var container = document.getElementById("task-view-chips");
  if (!container) return;
  container.innerHTML = "";
  var prefs = taskViewPrefs || DEFAULT_TASK_VIEW;
  var f = prefs.filters || {};
  var hasAny = false;

  ["status", "workflow", "molecule", "tag", "batch", "remark"].forEach(function(dim) {
    var arr = f[dim] || [];
    arr.forEach(function(val) {
      hasAny = true;
      var chip = document.createElement("span");
      chip.className = "task-view-chip";
      var labelFn = _CHIP_DIM_LABELS[dim] || (function(v) { return v; });
      chip.textContent = labelFn(val);
      var remove = document.createElement("span");
      remove.className = "chip-remove";
      remove.textContent = "×";
      remove.dataset.chipDim = dim;
      remove.dataset.chipValue = val;
      remove.addEventListener("click", function() {
        var next = _clonePrefs();
        next.filters[dim] = (next.filters[dim] || []).filter(function(v) { return v !== val; });
        taskViewPrefs = next;
        saveTaskViewPrefs();
        _updateFilterBadge();
        renderChips();
        refreshJobs();
      });
      chip.appendChild(remove);
      container.appendChild(chip);
    });
  });

  if (hasAny) {
    var clear = document.createElement("span");
    clear.className = "chip-clear";
    clear.setAttribute("data-i18n", "queue.view.chip_clear");
    clear.textContent = t("queue.view.chip_clear");
    clear.addEventListener("click", function() {
      var next = _clonePrefs();
      next.filters = { status: [], workflow: [], molecule: [], tag: [], batch: [], remark: [] };
      taskViewPrefs = next;
      saveTaskViewPrefs();
      _updateFilterBadge();
      renderChips();
      refreshJobs();
    });
    container.appendChild(clear);
    container.style.display = "";
  } else {
    container.style.display = "none";
  }
}

function _bindTaskViewToolbar() {
  var searchEl = document.getElementById("task-view-search");
  if (searchEl) searchEl.addEventListener("input", scheduleTaskViewSearch);
  var groupEl = document.getElementById("task-view-group");
  if (groupEl) groupEl.addEventListener("change", function() {
    var next = _clonePrefs();
    next.groupBy = groupEl.value;
    taskViewPrefs = next;
    saveTaskViewPrefs();
    refreshJobs();
  });
  var sortEl = document.getElementById("task-view-sort");
  if (sortEl) sortEl.addEventListener("change", function() {
    var next = _clonePrefs();
    next.sort = sortEl.value;
    taskViewPrefs = next;
    saveTaskViewPrefs();
    refreshJobs();
  });
  var filterBtn = document.getElementById("task-view-filter-btn");
  if (filterBtn) filterBtn.addEventListener("click", function(e) { e.stopPropagation(); _toggleFilterPanel(); });
  var filterClose = document.getElementById("task-view-filter-close");
  if (filterClose) filterClose.addEventListener("click", function() { _closeFilterPanel(); });
  // Apply initial prefs to controls
  _applyTaskViewPrefsToControls();
}

function getQueueCounts(countsArg) {
  const counts = countsArg || {};
  return { running: counts.running || 0, queued: counts.queued || 0, completed: counts.completed || 0, failed: counts.failed || 0, paused: counts.paused || 0 };
}

function renderQueue(countsArg) {
  const counts = getQueueCounts(countsArg || (lastStatus && lastStatus.queue) || {});
  let summary = t("queue.summary", counts);
  if (counts.paused > 0) summary += " · " + t("queue.paused_count", { count: counts.paused });
  document.getElementById("queue-counts").textContent = summary;
  document.getElementById("mobile-queue-counts").textContent = summary;
  pruneQueueSelection();
  renderQueueList(document.getElementById("queue-list"));
  renderQueueList(document.getElementById("mobile-queue-list"));
  updateQueueBatchBar();
}

// Q2: group the flat job list by project_id, then by group_id (self-rooted —
// equals the job id for standalone jobs). Order of first appearance is kept.
function groupJobsForQueue(jobs) {
  const projects = [];
  const byProject = {};
  (jobs || []).forEach(function(job) {
    const pid = job.project_id ? String(job.project_id) : "";
    if (!byProject[pid]) {
      byProject[pid] = { id: pid, name: typeof job.project_name === "string" ? job.project_name : "", jobs: [], groups: [], byGroup: {} };
      projects.push(byProject[pid]);
    }
    byProject[pid].jobs.push(job);
  });
  projects.forEach(function(pg) {
    pg.jobs.forEach(function(job) {
      const gid = job.group_id ? String(job.group_id) : String(job.id);
      if (!pg.byGroup[gid]) {
        pg.byGroup[gid] = { id: gid, jobs: [] };
        pg.groups.push(pg.byGroup[gid]);
      }
      pg.byGroup[gid].jobs.push(job);
    });
  });
  return projects;
}

// Aggregate badge for a multi-job group: first match wins.
function aggregateGroupStatus(jobs) {
  const order = ["running", "paused", "queued", "waiting_review", "failed", "cancelled", "completed"];
  const statuses = (jobs || []).map(function(job) { return String(job.status || "").toLowerCase(); });
  for (let i = 0; i < order.length; i += 1) {
    if (statuses.indexOf(order[i]) >= 0) return order[i];
  }
  return statuses[0] || "queued";
}

function buildQueueProjectHeader(pg) {
  const head = document.createElement("div");
  head.className = "queue-project-head";
  // Count statuses
  var statusCounts = { running: 0, queued: 0, completed: 0, failed: 0 };
  (pg.jobs || []).forEach(function(job) {
    var s = String(job.status || "").toLowerCase();
    if (s === "running" || s === "paused") statusCounts.running++;
    else if (s === "queued" || s === "pending") statusCounts.queued++;
    else if (s === "completed") statusCounts.completed++;
    else if (s === "failed" || s === "cancelled") statusCounts.failed++;
  });
  const name = document.createElement("span");
  name.className = "queue-project-name";
  name.textContent = pg.name || (currentLang === "zh-CN" ? "未分类" : "Uncategorized");
  name.title = name.textContent;
  const summary = document.createElement("span");
  summary.className = "queue-project-count";
  summary.textContent = t("queue.project_summary", {
    name: pg.name || (currentLang === "zh-CN" ? "未分类" : "Uncategorized"),
    total: pg.jobs.length,
    running: statusCounts.running,
    queued: statusCounts.queued,
    completed: statusCounts.completed,
    failed: statusCounts.failed
  });
  summary.title = summary.textContent;
  head.appendChild(summary);
  return head;
}

function buildQueueGroup(group) {
  const wrap = document.createElement("div");
  wrap.className = "queue-group";
  const root = group.jobs[0];
  const rootName = displayTaskName(root) || String(group.id);

  const head = document.createElement("div");
  head.className = "queue-group-head";
  const isCollapsed = queueCollapsedGroups[group.id] === true;
  const toggle = document.createElement("button");
  toggle.className = "queue-group-toggle" + (isCollapsed ? "" : " open");
  toggle.innerHTML = folderToggleSvg(!isCollapsed);
  const name = document.createElement("span");
  name.className = "queue-group-name";
  name.textContent = rootName;
  name.title = rootName;
  const count = document.createElement("span");
  count.className = "queue-group-count";
  count.textContent = t("queue.group_members", { count: group.jobs.length });
  head.appendChild(toggle);
  head.appendChild(name);
  head.appendChild(count);
  head.appendChild(createStatusBadge(aggregateGroupStatus(group.jobs)));

  const members = document.createElement("div");
  members.className = "queue-group-members" + (isCollapsed ? " collapsed" : "");
  group.jobs.forEach(function(job) { members.appendChild(buildQueueRow(job)); });

  head.addEventListener("click", function() {
    queueCollapsedGroups[group.id] = !isCollapsed;
    renderQueueList(document.getElementById("queue-list"));
    renderQueueList(document.getElementById("mobile-queue-list"));
  });
  wrap.appendChild(head);
  wrap.appendChild(members);
  return wrap;
}

function buildQueueRow(job) {
  var cacheKey, signature;
  if (queueRowRenderContext) {
    var id = String(job.id);
    var occurrence = queueRowRenderContext.occurrences[id] || 0;
    queueRowRenderContext.occurrences[id] = occurrence + 1;
    cacheKey = id + ":" + occurrence;
    // GAP-9: the recovery matrix lives outside the row payload — include it
    // in the signature so recovery-only changes rebuild the row buttons.
    signature = JSON.stringify([job, currentLang, getJobRecovery(job)]);
    var cached = queueRowRenderContext.previous.get(cacheKey);
    if (cached && cached.signature === signature) {
      cached.node.classList.toggle("active", id === String(selectedJobId));
      cached.node.querySelector(".queue-check").checked = queueSelectedJobs.has(id);
      queueRowRenderContext.next.set(cacheKey, cached);
      return cached.node;
    }
  }
  var groupBy = job._groupBy || "";
  const row = document.createElement("div");
  row.className = "queue-row" + (String(job.id) === String(selectedJobId) ? " active" : "") + (job.archived ? " archived-row" : "");
  row.setAttribute("data-job-id", String(job.id));
  row.addEventListener("click", function() { selectJob(job.id); });

  const head = document.createElement("div");
  head.className = "queue-row-head";

  // Q10: batch purge multi-select checkbox (desktop + mobile rows).
  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "queue-check";
  check.checked = queueSelectedJobs.has(String(job.id));
  check.title = t("purge.title");
  check.addEventListener("click", function(event) { event.stopPropagation(); });
  check.addEventListener("change", function() {
    const id = String(job.id);
    if (check.checked) queueSelectedJobs.add(id);
    else queueSelectedJobs.delete(id);
    syncQueueRowCheckboxes(id, check.checked);
    updateQueueBatchBar();
  });
  head.appendChild(check);

  // T6 slim card (c): task name — single-line ellipsis + title attribute.
  // When groupBy=molecule, do NOT repeat molecule name; prefer task_name+remark.
  // Custom name (resolved_name/custom_name) always takes precedence per plan §2.
  const name = document.createElement("div");
  name.className = "queue-name";
  const strong = document.createElement("strong");
  var primaryLabel;
  if (groupBy === "molecule") {
    // Inside molecule group: prefer resolved/custom name, then task_name+remark, fallback
    var dn = (job.resolved_name || job.custom_name || "").trim();
    if (dn) {
      primaryLabel = dn;
    } else {
      var tn = (job.task_name || (job.spec && job.spec.task_name) || "").trim();
      var rm = (job.remark || (job.spec && job.spec.remark) || "").trim();
      primaryLabel = tn ? (rm ? tn + " — " + rm : tn) : (canonicalTaskName(job) || String(job.id));
    }
  } else {
    primaryLabel = displayTaskName(job) || String(job.id);
  }
  strong.textContent = primaryLabel;
  strong.title = primaryLabel;
  const span = document.createElement("span");
  var queueMetaText = (job.spec && job.spec.workflow ? job.spec.workflow : "") + " \u00b7 " + translateStatus(job.status);
  if (job.stage_detail) queueMetaText += " " + job.stage_detail;
  span.textContent = queueMetaText;
  name.appendChild(strong);
  name.appendChild(span);

  const actions = document.createElement("div");
  actions.className = "queue-row-actions";
  actions.appendChild(createStatusBadge(job.status));
  appendQueueInlineActions(actions, job);
  const view = document.createElement("button");
  view.className = "mini-btn";
  view.textContent = t("queue.view");
  view.addEventListener("click", function(event) {
    event.stopPropagation();
    selectJob(job.id);
  });
  actions.appendChild(view);
  head.appendChild(name);
  head.appendChild(actions);
  row.appendChild(head);

  // T6 slim card (c): progress bar ONLY for active statuses.
  // Terminal + queued rows show status text badge only — no progress bar.
  if (isActiveJobStatus(job.status)) {
    var progressIndeterminate = isProgressIndeterminate(job);
    const progress = document.createElement("div");
    progress.className = "queue-progress";
    const track = document.createElement("div");
    track.className = "progress-track";
    const fill = document.createElement("div");
    if (progressIndeterminate) {
      fill.className = "progress-fill indeterminate";
      fill.style.width = "100%";
    } else {
      fill.className = "progress-fill";
      fill.style.width = normalizedProgress(job) + "%";
    }
    track.appendChild(fill);
    progress.appendChild(track);
    const meta = document.createElement("div");
    meta.className = "progress-meta";
    const left = document.createElement("span");
    var stageText = translateStageName(job.current_stage, job.stage_label) || (progressIndeterminate ? t("common.computing") : "");
    if (job.stage_detail) stageText += " " + job.stage_detail;
    else if (job.stage_index && job.stage_total) stageText += " " + job.stage_index + "/" + job.stage_total;
    left.textContent = stageText;
    const right = document.createElement("span");
    var rightText = progressIndeterminate ? t("common.computing") : t("common.percent", { value: normalizedProgress(job) });
    var relTime = formatRelativeTime(job.updated_at);
    if (relTime) rightText += " \u00b7 " + relTime;
    right.textContent = rightText;
    meta.appendChild(left);
    meta.appendChild(right);
    progress.appendChild(meta);
    row.appendChild(progress);
  }

  // T6 slim card (c): tags display (max 2 + +N overflow).
  var tags = job.tags;
  if (Array.isArray(tags) && tags.length > 0) {
    var tagsEl = document.createElement("div");
    tagsEl.className = "queue-row-tags";
    var maxShow = 2;
    var shown = tags.slice(0, maxShow);
    shown.forEach(function(tag) {
      var chip = document.createElement("span");
      chip.className = "queue-tag-chip";
      chip.textContent = tag;
      tagsEl.appendChild(chip);
    });
    if (tags.length > maxShow) {
      var overflow = document.createElement("span");
      overflow.className = "queue-tag-overflow";
      overflow.textContent = "+" + (tags.length - maxShow);
      overflow.title = tags.join(", ");
      tagsEl.appendChild(overflow);
    }
    row.appendChild(tagsEl);
  }

  if (queueRowRenderContext) queueRowRenderContext.next.set(cacheKey, { signature: signature, node: row });
  return row;
}

// T6: client-side sort + running-first partition for task-view rows.
// Time sorts trust server order; name sorts use _taskCollator.
// runningFirst: stable partition (active first, preserving relative order).
function sortTaskRows(rows) {
  if (!rows || !rows.length) return rows;
  var prefs = taskViewPrefs || DEFAULT_TASK_VIEW;
  var sort = prefs.sort || "created_desc";
  if (sort === "name_asc" || sort === "name_desc") {
    rows.sort(function(a, b) {
      var na = displayTaskName(a) || "";
      var nb = displayTaskName(b) || "";
      var c = _taskCollator.compare(na, nb);
      if (sort === "name_desc") c = -c;
      if (c !== 0) return c;
      return String(a.id || "").localeCompare(String(b.id || ""));
    });
  }
  if (prefs.runningFirst) {
    var active = [];
    var other = [];
    for (var i = 0; i < rows.length; i++) {
      (isActiveJobStatus(rows[i].status) ? active : other).push(rows[i]);
    }
    rows = active.concat(other);
  }
  return rows;
}

// T6: sentinel display-name resolution for group headers.
function _resolveGroupDisplayName(group, groupBy) {
  var key = group.key || "";
  var display = group.display_name || "";
  if (key === "__unassigned__" || display === "" || display === "__unassigned__") {
    return groupBy === "remark" ? t("queue.view.unassigned_remark") : t("queue.view.unassigned_molecule");
  }
  if (key === "__singles__") return t("queue.view.singles");
  if (key === "__untagged__") return t("queue.view.untagged");
  return display;
}

// T6: render a group-level status summary line ("运行 x · 排队 x · 完成 x · 失败 x").
function _renderGroupStatusLine(group) {
  var r = 0, q = 0, c = 0, f = 0;
  (group.jobs || []).forEach(function(row) {
    var s = String(row.status || "").toLowerCase();
    if (s === "running") r++;
    else if (s === "queued" || s === "starting" || s === "pending") q++;
    else if (s === "completed") c++;
    else if (s === "failed" || s === "cancelled") f++;
  });
  var parts = [];
  if (r > 0) parts.push(t("queue.view.status_running", { count: r }));
  if (q > 0) parts.push(t("queue.view.status_queued", { count: q }));
  if (c > 0) parts.push(t("queue.view.status_completed", { count: c }));
  if (f > 0) parts.push(t("queue.view.status_failed", { count: f }));
  if (!parts.length) return document.createElement("span");
  var el = document.createElement("span");
  el.className = "queue-group-status-line";
  el.textContent = parts.join(" · ");
  return el;
}

// T6: one-time hint chip for tag-group views.
function _renderTagGroupHint() {
  var el = document.createElement("div");
  el.className = "queue-tag-hint";
  el.textContent = t("queue.view.tag_group_hint");
  return el;
}

// T6: retired badge for retired workflow groups.
function _renderRetiredBadge() {
  var el = document.createElement("span");
  el.className = "queue-retired-badge";
  el.textContent = t("queue.view.retired");
  return el;
}

function renderQueueList(container) {
  if (!container) return;
  var parentContext = queueRowRenderContext;
  var context = { previous: container._acpRows || new Map(), next: new Map(), occurrences: {} };
  queueRowRenderContext = context;
  try {
    renderQueueListContents(container);
  } finally {
    container._acpRows = context.next;
    queueRowRenderContext = parentContext;
  }
}

function renderQueueListContents(container) {
  container.innerHTML = "";
  if (!jobsCache.length) {
    const empty = document.createElement("div");
    empty.className = "placeholder";
    empty.textContent = t("queue.none");
    container.appendChild(empty);
    return;
  }
  var prefs = taskViewPrefs || DEFAULT_TASK_VIEW;
  var groupBy = prefs.groupBy || "molecule";
  // T6: use taskViewCache.groups when available (server-grouped mode).
  var cache = taskViewCache;
  if (cache && cache.groups) {
    var groups = cache.groups;
    // group_by=none → flat list (single __all__ group rendered without header)
    if (groupBy === "none") {
      var rows = groups.length ? (groups[0].jobs || []) : [];
      rows.forEach(function(job) { container.appendChild(buildQueueRow(job)); });
      return;
    }
    // Cross-project scope: wrap groups by project when multiple projects present.
    var selectedPid = selectedProjectId;
    var showProjectHeaders = !selectedPid;
    if (showProjectHeaders) {
      var byProject = {};
      var projectOrder = [];
      groups.forEach(function(g) {
        (g.jobs || []).forEach(function(row) {
          var pid = row.project_id ? String(row.project_id) : "";
          if (!byProject[pid]) {
            var pname = row.project_name || "";
            byProject[pid] = { name: pname, jobs: [], groups: [] };
            projectOrder.push(pid);
          }
          byProject[pid].jobs.push(row);
        });
      });
      // Map groups to projects by scanning their first job's project_id
      groups.forEach(function(g) {
        var firstJob = (g.jobs && g.jobs.length) ? g.jobs[0] : null;
        var pid = firstJob && firstJob.project_id ? String(firstJob.project_id) : "";
        if (byProject[pid]) byProject[pid].groups.push(g);
        else if (projectOrder.length) byProject[projectOrder[0]].groups.push(g);
      });
      projectOrder.forEach(function(pid) {
        var pg = byProject[pid];
        if (!pg) return;
        // Build project header (reuse existing function with synthetic pg shape)
        var pgObj = { id: pid, name: pg.name, jobs: pg.jobs, groups: [], byGroup: {} };
        container.appendChild(buildQueueProjectHeader(pgObj));
        pg.groups.forEach(function(g) { container.appendChild(_buildTaskViewGroup(g, groupBy)); });
      });
      return;
    }
    // Single project scope: render groups directly.
    groups.forEach(function(g) { container.appendChild(_buildTaskViewGroup(g, groupBy)); });
    return;
  }
  // Fallback: legacy groupJobsForQueue path (no taskViewCache).
  const projects = groupJobsForQueue(jobsCache);
  const showLegacyHeaders = projects.length > 1 || !!projects[0].name;
  projects.forEach(function(pg) {
    if (showLegacyHeaders) container.appendChild(buildQueueProjectHeader(pg));
    pg.groups.forEach(function(group) {
      if (group.jobs.length === 1) {
        container.appendChild(buildQueueRow(group.jobs[0]));
      } else {
        container.appendChild(buildQueueGroup(group));
      }
    });
  });
}

// T6: build a collapsible group section from taskViewCache.groups entry.
function _buildTaskViewGroup(group, groupBy) {
  var wrap = document.createElement("div");
  wrap.className = "queue-group";
  var key = group.key || "__unknown__";
  var collapseKey = "view:" + groupBy + ":" + key;
  var isCollapsed = queueCollapsedGroups[collapseKey] === true;

  // Group header
  var head = document.createElement("div");
  head.className = "queue-group-head";

  // Per-group select-all checkbox (d)
  var selectAll = document.createElement("input");
  selectAll.type = "checkbox";
  selectAll.className = "queue-group-select-all";
  selectAll.title = t("queue.view.select_all_group");
  selectAll.addEventListener("click", function(event) { event.stopPropagation(); });
  selectAll.addEventListener("change", function() {
    var ids = (group.jobs || []).map(function(row) { return String(row.id); });
    ids.forEach(function(id) {
      if (selectAll.checked) queueSelectedJobs.add(id);
      else queueSelectedJobs.delete(id);
    });
    syncQueueRowCheckboxes_bulk(ids, selectAll.checked);
    updateQueueBatchBar();
  });
  head.appendChild(selectAll);

  // Toggle arrow
  var toggle = document.createElement("button");
  toggle.className = "queue-group-toggle" + (isCollapsed ? "" : " open");
  toggle.innerHTML = folderToggleSvg(!isCollapsed);
  head.appendChild(toggle);

  // Display name (sentinel resolution)
  var displayName = _resolveGroupDisplayName(group, groupBy);
  var name = document.createElement("span");
  name.className = "queue-group-name";
  name.textContent = displayName;
  name.title = displayName;
  head.appendChild(name);

  // Task count
  var count = document.createElement("span");
  count.className = "queue-group-count";
  count.textContent = t("queue.view.group_tasks_count", { count: group.count || (group.jobs || []).length });
  head.appendChild(count);

  // Status summary line (hide zero segments, never empty string)
  head.appendChild(_renderGroupStatusLine(group));

  // Retired badge for workflow groups
  if (group.retired) head.appendChild(_renderRetiredBadge());

  // Collapse toggle handler
  head.addEventListener("click", function() {
    queueCollapsedGroups[collapseKey] = !isCollapsed;
    renderQueueList(document.getElementById("queue-list"));
    renderQueueList(document.getElementById("mobile-queue-list"));
  });

  // Members container
  var members = document.createElement("div");
  members.className = "queue-group-members" + (isCollapsed ? " collapsed" : "");

  // Tag-group hint chip (one-time per view, rendered once at top of container)
  if (groupBy === "tag") {
    members.appendChild(_renderTagGroupHint());
  }

  // Render rows with _groupBy annotation for slim-card molecule dedup
  (group.jobs || []).forEach(function(row) {
    row._groupBy = groupBy;
    members.appendChild(buildQueueRow(row));
  });

  wrap.appendChild(head);
  wrap.appendChild(members);
  return wrap;
}

// T6: bulk checkbox sync helper (set multiple row checkboxes at once).
function syncQueueRowCheckboxes_bulk(jobIds, checked) {
  var idSet = new Set(jobIds);
  document.querySelectorAll(".queue-row").forEach(function(row) {
    if (idSet.has(row.getAttribute("data-job-id"))) {
      var cb = row.querySelector(".queue-check");
      if (cb) cb.checked = checked;
    }
  });
}

function highlightQueueRow(jobId) {
  document.querySelectorAll(".queue-row").forEach(function(row) {
    row.classList.toggle("active", row.getAttribute("data-job-id") === String(jobId));
  });
}

