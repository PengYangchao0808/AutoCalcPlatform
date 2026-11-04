/* file: js/modules/files-workspace/module.js */
/* Files workspace — read-only project files and an explicitly unavailable terminal.
 * All data goes through ACPClient.projectFiles (injected v2 transport). No fetch,
 * no polling, no project-root path construction, no fabricated files. */
(function () {
  "use strict";
  var MAX_BYTES = 65536;
  var MAX_LINES = 1000;
  var TREE_LIMIT = 200;
  var TERM_MIN = 200;
  var TERM_MAX = 240;
  var TERM_KEY = "acp-next-files-terminal-v1";

  function create(deps) {
    deps = deps || {};
    var doc = deps.doc || document;
    var i18n = deps.i18n || (window.ACPShell && window.ACPShell.i18n);
    var host = null, root = null, mainEl = null, toolbar = null, crumbEl = null, reader = null;
    var readerStatus = null, readerTitle = null, readerBody = null, lineNums = null, pre = null, readNote = null;
    var treeEl = null, treeToggle = null, treeClose = null, treeList = null, treeStatus = null, treeSearch = null, treeRefresh = null;
    var terminal = null, termHandle = null;
    var snapshot = {}, projectId = null, generation = 0, readToken = 0, disposed = false, suspended = false;
    var treeState = { loading: false, error: null, children: Object.create(null), expanded: Object.create(null), loadingDirs: Object.create(null), errors: Object.create(null), rootMeta: null, truncated: null };
    var selectedPath = "", focusedPath = "", query = "", rows = Object.create(null), visiblePaths = [], treeOpen = false, termHeight = 200;
    var globalListener = null;

    function el(tag, cls, text) {
      var n = doc.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;
      return n;
    }
    function t(key, vars) {
      var out = key;
      try { if (i18n && i18n.t) out = i18n.t(key, vars); } catch (e) { out = key; }
      if (out === key) {
        var fallback = FALLBACK[lang()] && FALLBACK[lang()][key];
        out = fallback != null ? fallback : key;
      }
      if (vars) Object.keys(vars).forEach(function (k) { out = out.split("{" + k + "}").join(String(vars[k])); });
      return out;
    }
    function lang() { try { return i18n && i18n.getLang ? i18n.getLang() : "zh-CN"; } catch (e) { return "zh-CN"; } }
    function client() { return deps.client || (typeof window !== "undefined" ? window.ACPClient : null); }
    function filesApi() { var c = client(); return c && c.projectFiles ? c.projectFiles : null; }
    function storeGet() { try { return window.ACPShell && ACPShell.env ? ACPShell.env.storage.get(TERM_KEY) : null; } catch (e) { return null; } }
    function storeSet(value) { try { if (window.ACPShell && ACPShell.env) ACPShell.env.storage.set(TERM_KEY, String(value)); } catch (e) { /* preference is non-critical */ } }
    function restoreTerminal() { var n = Number(storeGet()); if (isFinite(n)) termHeight = Math.max(TERM_MIN, Math.min(TERM_MAX, n)); }
    function setTerminalHeight(value, persist) {
      termHeight = Math.max(TERM_MIN, Math.min(TERM_MAX, Math.round(Number(value) || TERM_MIN)));
      if (root) root.style.setProperty("--files-term-h", termHeight + "px");
      if (termHandle) termHandle.setAttribute("aria-valuenow", String(termHeight));
      if (persist !== false) storeSet(termHeight);
    }
    function formatBytes(value) {
      var n = Number(value);
      if (!isFinite(n) || n < 0) return "";
      if (n < 1024) return n + " B";
      if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + " KiB";
      return (n / (1024 * 1024)).toFixed(1) + " MiB";
    }
    function formatTime(value) {
      var numeric = Number(value);
      var ms = isFinite(numeric) ? (numeric > 1e12 ? numeric : numeric * 1000) : Date.parse(String(value));
      if (!isFinite(ms) || ms <= 0) return "";
      var d = new Date(ms);
      if (isNaN(d.getTime())) return "";
      try { return d.toLocaleString(lang() === "en-US" ? "en-US" : "zh-CN"); } catch (e) { return d.toISOString(); }
    }
    function statusText(err, key) {
      if (!err) return t(key, { message: "" });
      var code = err.status || err.statusCode;
      if (code === 401 || code === 403) return t("next.files.forbidden");
      if (code === 404) return t("next.files.not_found");
      if (code === 413) return t("next.files.too_large", { message: err.message || "" });
      if (code === 415) return t("next.files.binary", { message: err.message || "" });
      return t(key, { message: err.message || String(err) });
    }
    function isDir(entry) { return entry && (entry.type === "dir" || entry.type === "directory"); }
    function entryName(entry) { return entry && (entry.name || String(entry.path || "").split("/").pop()) || ""; }
    function pathDepth(path) { return path ? String(path).split("/").length : 0; }

    function setReaderState(kind, text) {
      if (!readerStatus) return;
      readerStatus.hidden = !text;
      readerStatus.textContent = text || "";
      readerStatus.setAttribute("data-files-state", kind || "");
      readerStatus.setAttribute("role", kind === "error" ? "alert" : "status");
      if (readerBody) readerBody.hidden = kind !== "content";
      if (readNote) readNote.hidden = true;
    }
    function renderCrumbs() {
      if (!crumbEl) return;
      crumbEl.replaceChildren();
      var rootC = el("span", "acp-file-crumb", t("next.files.crumb_root"));
      crumbEl.appendChild(rootC);
      if (!selectedPath) return;
      var parts = selectedPath.split("/").filter(Boolean);
      parts.forEach(function (part, index) {
        var sep = el("span", "acp-file-crumb-sep", "/");
        sep.setAttribute("aria-hidden", "true");
        crumbEl.appendChild(sep);
        var node = el("span", "acp-file-crumb" + (index === parts.length - 1 ? " is-current" : ""), part);
        if (index === parts.length - 1) node.setAttribute("aria-current", "page");
        crumbEl.appendChild(node);
      });
    }
    function setTreeStatus(text, kind) {
      if (!treeStatus) return;
      treeStatus.textContent = text || "";
      treeStatus.hidden = !text;
      treeStatus.setAttribute("data-files-state", kind || "");
      treeStatus.setAttribute("role", kind === "error" ? "alert" : "status");
    }
    function setTreeOpen(open, focus) {
      treeOpen = !!open;
      if (!root) return;
      root.classList.toggle("is-tree-open", treeOpen);
      if (treeToggle) treeToggle.setAttribute("aria-expanded", treeOpen ? "true" : "false");
      if (!treeOpen && focus && treeToggle) treeToggle.focus();
      if (treeOpen && focus) {
        var target = treeSearch || treeList;
        if (target && target.focus) target.focus();
      }
    }
    function canNarrow() { return root && root.clientWidth > 0 && root.clientWidth <= 1024; }

    function renderTerminal() {
      terminal = el("section", "acp-files-terminal");
      terminal.setAttribute("aria-label", t("next.files.terminal"));
      var head = el("header", "acp-files-terminal-head");
      head.appendChild(el("strong", null, t("next.files.terminal")));
      var badge = el("span", "acp-files-terminal-badge", t("next.files.terminal_unavailable_short"));
      head.appendChild(badge);
      termHandle = el("button", "acp-files-terminal-resize");
      termHandle.type = "button";
      termHandle.setAttribute("role", "slider");
      termHandle.setAttribute("aria-label", t("next.files.terminal_height"));
      termHandle.setAttribute("aria-valuemin", String(TERM_MIN));
      termHandle.setAttribute("aria-valuemax", String(TERM_MAX));
      termHandle.setAttribute("aria-valuenow", String(termHeight));
      termHandle.setAttribute("title", t("next.files.terminal_height_hint"));
      head.appendChild(termHandle);
      terminal.appendChild(head);
      var body = el("div", "acp-files-terminal-body");
      var unavailable = el("div", "acp-files-terminal-disabled");
      unavailable.setAttribute("role", "status");
      unavailable.appendChild(el("strong", null, t("next.files.terminal_unavailable")));
      unavailable.appendChild(el("span", null, t("next.files.terminal_reason")));
      body.appendChild(unavailable);
      terminal.appendChild(body);
      termHandle.addEventListener("keydown", function (e) {
        var next = null;
        if (e.key === "ArrowUp" || e.key === "ArrowRight") next = termHeight + 10;
        else if (e.key === "ArrowDown" || e.key === "ArrowLeft") next = termHeight - 10;
        else if (e.key === "Home") next = TERM_MIN;
        else if (e.key === "End") next = TERM_MAX;
        if (next != null) { e.preventDefault(); setTerminalHeight(next); }
      });
      termHandle.addEventListener("pointerdown", function (e) {
        e.preventDefault();
        var startY = e.clientY, start = termHeight;
        if (termHandle.setPointerCapture && e.pointerId != null) termHandle.setPointerCapture(e.pointerId);
        var move = function (ev) { setTerminalHeight(start + startY - ev.clientY, false); };
        var up = function () { doc.removeEventListener("pointermove", move); doc.removeEventListener("pointerup", up); storeSet(termHeight); };
        doc.addEventListener("pointermove", move);
        doc.addEventListener("pointerup", up);
      });
      setTerminalHeight(termHeight, false);
    }

    function renderTreeRows() {
      treeList.replaceChildren();
      rows = Object.create(null);
      visiblePaths = [];
      if (!projectId) {
        setTreeStatus(t("next.files.pick_project"), "empty");
        return;
      }
      if (treeState.error) { setTreeStatus(statusText(treeState.error, "next.files.tree_error"), "error"); return; }
      if (treeState.loading) { setTreeStatus(t("next.files.tree_loading"), "loading"); return; }
      var entries = treeState.children[""] || [];
      if (!entries.length) { setTreeStatus(t("next.files.tree_empty"), "empty"); return; }
      if (query) {
        var matches = collectLoadedEntries().filter(function (entry) {
          return (entry.path + "/" + entry.name).toLowerCase().indexOf(query.toLowerCase()) >= 0;
        });
        var searchText = matches.length ? t("next.files.search_loaded", { count: matches.length }) : t("next.files.search_empty");
        if (treeState.truncated) searchText = t("next.files.tree_truncated", { reason: treeState.truncated.reason || "limit" }) + " · " + searchText;
        setTreeStatus(searchText, matches.length ? (treeState.truncated ? "note" : "") : "empty");
        matches.forEach(function (entry) { treeList.appendChild(makeTreeRow(entry, 1, "")); });
      } else {
        renderTreeLevel("", 1);
        if (treeState.truncated) setTreeStatus(t("next.files.tree_truncated", { reason: treeState.truncated.reason || "limit" }), "note");
        else setTreeStatus("", "");
      }
      restoreTreeFocus();
    }
    function collectLoadedEntries() {
      var out = [];
      function walk(path) {
        (treeState.children[path] || []).forEach(function (entry) {
          out.push(entry);
          if (isDir(entry) && treeState.children[entry.path]) walk(entry.path);
        });
      }
      walk("");
      return out;
    }
    function renderTreeLevel(path, level) {
      (treeState.children[path] || []).forEach(function (entry) {
        treeList.appendChild(makeTreeRow(entry, level, path));
        if (isDir(entry) && treeState.expanded[entry.path]) {
          if (treeState.loadingDirs[entry.path]) {
            var load = el("div", "acp-files-tree-note", t("next.files.dir_loading"));
            load.style.paddingLeft = (8 + level * 14) + "px";
            treeList.appendChild(load);
          } else if (treeState.errors[entry.path]) {
            var err = el("div", "acp-files-tree-note is-error", treeState.errors[entry.path]);
            err.style.paddingLeft = (8 + level * 14) + "px";
            treeList.appendChild(err);
          } else {
            renderTreeLevel(entry.path, level + 1);
          }
        }
      });
    }
    function makeTreeRow(entry, level, parent) {
      var button = el("button", "acp-files-tree-row" + (entry.path === selectedPath ? " is-selected" : ""));
      button.type = "button";
      button.setAttribute("role", "treeitem");
      button.setAttribute("aria-level", String(level));
      button.setAttribute("data-files-path", entry.path);
      button.setAttribute("data-files-type", entry.type);
      var firstTabbable = !focusedPath && visiblePaths.length === 0;
      if (firstTabbable) focusedPath = entry.path;
      button.setAttribute("tabindex", (entry.path === focusedPath) ? "0" : "-1");
      if (isDir(entry)) {
        button.setAttribute("aria-expanded", treeState.expanded[entry.path] ? "true" : "false");
        button.appendChild(el("span", "acp-files-tree-arrow", treeState.expanded[entry.path] ? "▾" : "▸"));
      } else {
        button.setAttribute("aria-selected", entry.path === selectedPath ? "true" : "false");
        button.appendChild(el("span", "acp-files-tree-arrow", ""));
      }
      button.appendChild(el("span", "acp-files-tree-name", entryName(entry)));
      button.setAttribute("title", entry.path + (entry.size != null ? " · " + formatBytes(entry.size) : ""));
      if (!isDir(entry) && entry.size != null) button.appendChild(el("span", "acp-files-tree-size", formatBytes(entry.size)));
      button.style.paddingLeft = (8 + (level - 1) * 14) + "px";
      button.addEventListener("click", function () { treeActivate(entry); });
      rows[entry.path] = button;
      visiblePaths.push(entry.path);
      return button;
    }
    function restoreTreeFocus() {
      if (!treeList) return;
      var target = rows[focusedPath];
      if (target) target.setAttribute("tabindex", "0");
    }
    function treeActivate(entry) {
      focusedPath = entry.path;
      if (isDir(entry)) {
        treeState.expanded[entry.path] = !treeState.expanded[entry.path];
        if (treeState.expanded[entry.path] && !treeState.children[entry.path]) loadDir(entry.path);
        else renderTreeRows();
        return;
      }
      selectFile(entry.path);
    }
    function focusPath(path) {
      var button = rows[path];
      if (!button) return;
      focusedPath = path;
      Object.keys(rows).forEach(function (key) { rows[key].setAttribute("tabindex", key === path ? "0" : "-1"); });
      button.focus();
    }
    function treeKeydown(e) {
      var index = visiblePaths.indexOf(focusedPath);
      if (e.key === "ArrowDown") { e.preventDefault(); focusPath(visiblePaths[Math.min(visiblePaths.length - 1, index + 1)]); }
      else if (e.key === "ArrowUp") { e.preventDefault(); focusPath(visiblePaths[Math.max(0, index - 1)]); }
      else if (e.key === "Home") { e.preventDefault(); focusPath(visiblePaths[0]); }
      else if (e.key === "End") { e.preventDefault(); focusPath(visiblePaths[visiblePaths.length - 1]); }
      else if (e.key === "ArrowRight" && index >= 0) {
        var entry = findLoadedEntry(focusedPath);
        if (entry && isDir(entry) && !treeState.expanded[entry.path]) { e.preventDefault(); treeActivate(entry); }
      } else if (e.key === "ArrowLeft" && index >= 0) {
        var current = findLoadedEntry(focusedPath);
        if (current && isDir(current) && treeState.expanded[current.path]) { e.preventDefault(); treeState.expanded[current.path] = false; renderTreeRows(); focusPath(current.path); }
        else {
          var parts = String(focusedPath || "").split("/");
          if (parts.length > 1) { e.preventDefault(); focusPath(parts.slice(0, -1).join("/")); }
        }
      } else if (e.key === "Enter" || e.key === " ") {
        var chosen = findLoadedEntry(focusedPath);
        if (chosen) { e.preventDefault(); treeActivate(chosen); }
      } else if (e.key === "Escape" && canNarrow()) {
        e.preventDefault(); setTreeOpen(false, true);
      }
    }
    function findLoadedEntry(path) { return collectLoadedEntries().filter(function (entry) { return entry.path === path; })[0] || null; }

    function loadRoot() {
      var api = filesApi();
      var gen = ++generation;
      treeState.loading = true; treeState.error = null; treeState.children = Object.create(null); treeState.expanded = Object.create(null);
      treeState.loadingDirs = Object.create(null); treeState.errors = Object.create(null); treeState.rootMeta = null; treeState.truncated = null;
      selectedPath = ""; focusedPath = ""; renderCrumbs(); setReaderState("empty", t("next.files.select_file"));
      if (readerTitle) { readerTitle.textContent = t("next.files.no_file"); readerTitle.removeAttribute("title"); }
      renderTreeRows();
      if (!projectId) return;
      if (!api) { treeState.loading = false; treeState.error = new Error(t("next.files.client_unavailable")); renderTreeRows(); return; }
      api.list(projectId, "", TREE_LIMIT).then(function (body) {
        if (disposed || gen !== generation) return;
        treeState.loading = false; treeState.rootMeta = body; treeState.children[""] = body.entries || [];
        treeState.expanded[""] = true;
        treeState.truncated = body.truncated ? { path: "", reason: body.truncation_reason } : null;
        renderTreeRows();
      }).catch(function (err) { if (disposed || gen !== generation) return; treeState.loading = false; treeState.error = err; renderTreeRows(); });
    }
    function loadDir(path) {
      var api = filesApi();
      if (!api) return;
      var gen = generation;
      treeState.loadingDirs[path] = true; delete treeState.errors[path]; renderTreeRows();
      api.list(projectId, path, TREE_LIMIT).then(function (body) {
        if (disposed || gen !== generation) return;
        treeState.loadingDirs[path] = false; treeState.children[path] = body.entries || [];
        if (body.truncated) treeState.truncated = { path: path, reason: body.truncation_reason };
        renderTreeRows();
      }).catch(function (err) {
        if (disposed || gen !== generation) return;
        treeState.loadingDirs[path] = false; treeState.errors[path] = statusText(err, "next.files.dir_error"); renderTreeRows();
      });
    }
    function selectFile(path) {
      var api = filesApi();
      var gen = generation, token = ++readToken;
      selectedPath = path; focusedPath = path; renderCrumbs(); renderTreeRows();
      if (readerTitle) readerTitle.textContent = path.split("/").pop();
      if (!api) { setReaderState("error", t("next.files.client_unavailable")); return; }
      setReaderState("loading", t("next.files.file_loading"));
      api.read(projectId, path, MAX_BYTES, MAX_LINES).then(function (body) {
        if (disposed || gen !== generation || token !== readToken) return;
        showFile(body);
      }).catch(function (err) {
        if (disposed || gen !== generation || token !== readToken) return;
        setReaderState("error", statusText(err, "next.files.file_error"));
      });
    }
    function showFile(body) {
      if (!body || body.content === "") { setReaderState("empty", t("next.files.file_empty")); return; }
      if (body.encoding && /^(binary|base64|application\/octet-stream)$/i.test(String(body.encoding))) {
        setReaderState("error", t("next.files.binary"));
        return;
      }
      var lines = String(body.content).split("\n");
      if (lines.length && lines[lines.length - 1] === "") lines.pop();
      var nums = [];
      for (var i = 1; i <= lines.length; i += 1) nums.push(String(i));
      lineNums.textContent = nums.join("\n");
      pre.textContent = body.content;
      pre.setAttribute("data-returned-lines", String(body.returned_lines != null ? body.returned_lines : lines.length));
      pre.setAttribute("data-returned-bytes", String(body.returned_bytes != null ? body.returned_bytes : ""));
      var meta = [];
      if (body.size != null) meta.push(formatBytes(body.size));
      if (body.returned_lines != null) meta.push(t("next.files.lines", { count: body.returned_lines }));
      if (body.encoding) meta.push(String(body.encoding));
      if (body.mtime) meta.push(formatTime(body.mtime));
      if (readerTitle) readerTitle.title = selectedPath + (meta.length ? " · " + meta.join(" · ") : "");
      if (readNote) {
        readNote.hidden = false;
        readNote.textContent = body.truncated
          ? t("next.files.file_truncated", { reason: body.truncation_reason || "limit" })
          : meta.join(" · ");
        readNote.setAttribute("data-files-state", body.truncated ? "truncated" : "meta");
        if (body.sha256) readNote.setAttribute("title", "sha256 (" + (body.sha256_scope || "returned_bytes") + "): " + body.sha256);
      }
      readerStatus.hidden = true;
      readerBody.hidden = false;
      readerBody.scrollTop = 0;
      if (canNarrow() && treeOpen) {
        setTreeOpen(false, false);
        if (pre && pre.focus) pre.focus();
      }
    }
    function renderToolbar() {
      toolbar = el("header", "acp-files-toolbar");
      treeToggle = el("button", "acp-files-tree-toggle");
      treeToggle.type = "button";
      treeToggle.setAttribute("aria-controls", "acp-files-tree");
      treeToggle.setAttribute("aria-expanded", "false");
      treeToggle.setAttribute("title", t("next.files.tree_toggle"));
      treeToggle.appendChild(el("span", null, t("next.files.tree")));
      treeToggle.addEventListener("click", function () { setTreeOpen(!treeOpen, true); });
      toolbar.appendChild(treeToggle);
      crumbEl = el("nav", "acp-files-crumbs");
      crumbEl.setAttribute("aria-label", t("next.files.breadcrumb"));
      toolbar.appendChild(crumbEl);
      mainEl.appendChild(toolbar);
    }
    function renderReader() {
      reader = el("section", "acp-file-reader");
      reader.setAttribute("aria-labelledby", "acp-files-reader-title");
      var head = el("header", "acp-file-reader-head");
      readerTitle = el("h3", "acp-file-reader-title", t("next.files.no_file"));
      readerTitle.id = "acp-files-reader-title";
      head.appendChild(readerTitle);
      reader.appendChild(head);
      readerStatus = el("p", "acp-files-reader-status", t("next.files.pick_project"));
      readerStatus.setAttribute("role", "status");
      reader.appendChild(readerStatus);
      readNote = el("p", "acp-files-read-note");
      readNote.hidden = true;
      reader.appendChild(readNote);
      readerBody = el("div", "acp-files-reader-body");
      readerBody.hidden = true;
      lineNums = el("div", "acp-files-linenums");
      lineNums.setAttribute("aria-hidden", "true");
      pre = el("pre", "acp-files-pre");
      pre.setAttribute("tabindex", "0");
      pre.setAttribute("aria-label", t("next.files.content"));
      readerBody.appendChild(lineNums);
      readerBody.appendChild(pre);
      reader.appendChild(readerBody);
      mainEl.appendChild(reader);
    }
    function renderTree() {
      treeEl = el("aside", "acp-files-tree");
      treeEl.id = "acp-files-tree";
      treeEl.setAttribute("aria-label", t("next.files.tree"));
      var head = el("header", "acp-files-tree-head");
      head.appendChild(el("strong", null, t("next.files.tree_title")));
      treeRefresh = el("button", "acp-files-tree-action");
      treeRefresh.type = "button";
      treeRefresh.textContent = t("next.files.refresh");
      treeRefresh.addEventListener("click", function () { loadRoot(); });
      head.appendChild(treeRefresh);
      treeClose = el("button", "acp-files-tree-action");
      treeClose.type = "button";
      treeClose.textContent = t("next.files.close");
      treeClose.addEventListener("click", function () { setTreeOpen(false, true); });
      head.appendChild(treeClose);
      treeEl.appendChild(head);
      var searchWrap = el("label", "acp-files-search");
      treeSearch = doc.createElement("input");
      treeSearch.type = "search";
      treeSearch.setAttribute("placeholder", t("next.files.search"));
      treeSearch.setAttribute("aria-label", t("next.files.search"));
      treeSearch.autocomplete = "off";
      treeSearch.addEventListener("input", function () { query = treeSearch.value.trim(); focusedPath = ""; renderTreeRows(); });
      searchWrap.appendChild(treeSearch);
      treeEl.appendChild(searchWrap);
      treeStatus = el("div", "acp-files-tree-status");
      treeStatus.hidden = true;
      treeEl.appendChild(treeStatus);
      treeList = el("div", "acp-files-tree-list");
      treeList.setAttribute("role", "tree");
      treeList.setAttribute("aria-label", t("next.files.tree"));
      treeList.addEventListener("keydown", treeKeydown);
      treeEl.appendChild(treeList);
      root.appendChild(treeEl);
      setTreeOpen(false, false);
    }
    function render() {
      root.replaceChildren();
      root.className = "acp-files-workspace" + (treeOpen ? " is-tree-open" : "");
      mainEl = el("section", "acp-files-main");
      renderToolbar();
      renderReader();
      root.appendChild(mainEl);
      renderTree();
      renderTerminal();
      root.appendChild(terminal);
      host.appendChild(root);
      setTerminalHeight(termHeight, false);
      updateProject();
    }
    function updateProject() {
      var next = snapshot && snapshot.projectId ? String(snapshot.projectId) : null;
      projectId = next;
      treeState.loading = false; treeState.error = null; treeState.children = Object.create(null); treeState.rootMeta = null;
      query = ""; if (treeSearch) treeSearch.value = "";
      if (!projectId) {
        renderCrumbs(); setReaderState("empty", t("next.files.pick_project")); renderTreeRows();
        if (readerTitle) readerTitle.textContent = t("next.files.no_file");
        return;
      }
      loadRoot();
    }
    function update(next) {
      snapshot = next || {};
      var pid = snapshot.projectId ? String(snapshot.projectId) : null;
      if (pid !== projectId) { readToken += 1; selectedPath = ""; if (root) updateProject(); }
    }
    function mount(h) {
      host = h;
      restoreTerminal();
      root = el("div", "acp-files-workspace");
      root.style.setProperty("--files-term-h", termHeight + "px");
      render();
      globalListener = function () { render(); };
      doc.addEventListener("acp:langchange", globalListener);
      update(deps.context && deps.context.getSnapshot ? deps.context.getSnapshot() : snapshot);
    }
    function dispose() {
      disposed = true; generation += 1; readToken += 1;
      if (globalListener) doc.removeEventListener("acp:langchange", globalListener);
      if (host) host.replaceChildren();
    }
    return {
      mount: mount,
      updateContext: update,
      suspend: function () { suspended = true; },
      resume: function () { suspended = false; if (root && treeList) renderTreeRows(); },
      dispose: dispose,
      _internals: { formatBytes: formatBytes, statusText: function (e, k) { return statusText(e, k); }, setTerminalHeight: setTerminalHeight, MAX_BYTES: MAX_BYTES, MAX_LINES: MAX_LINES }
    };
  }

  var FALLBACK = {
    "zh-CN": {
      "next.files.title": "文件", "next.files.crumb_root": "项目根目录", "next.files.pick_project": "请先在顶栏选择项目。",
      "next.files.tree_loading": "正在读取项目文件…", "next.files.tree_error": "项目文件读取失败：{message}", "next.files.tree_empty": "此目录为空。",
      "next.files.tree_truncated": "目录已截断：{reason}", "next.files.search": "过滤已加载文件", "next.files.search_loaded": "已加载项中匹配 {count} 个（不搜索未展开目录）。",
      "next.files.search_empty": "已加载项中没有匹配文件。", "next.files.tree": "项目文件", "next.files.tree_title": "当前项目文件", "next.files.refresh": "刷新",
      "next.files.close": "关闭", "next.files.tree_toggle": "打开项目文件树", "next.files.select_file": "从右侧文件树选择文件。",
      "next.files.no_file": "未选择文件", "next.files.file_loading": "正在读取文件…", "next.files.file_empty": "文件为空。",
      "next.files.file_error": "文件读取失败：{message}", "next.files.file_truncated": "内容已截断：{reason}",
      "next.files.binary": "该文件为二进制或不支持文本预览（HTTP 415）：{message}", "next.files.too_large": "文件超过只读预览上限（HTTP 413）：{message}",
      "next.files.forbidden": "无权读取该项目的文件。", "next.files.not_found": "文件或目录不存在。",
      "next.files.dir_loading": "正在读取目录…", "next.files.dir_error": "目录读取失败：{message}", "next.files.lines": "{count} 行",
      "next.files.terminal": "命令行", "next.files.terminal_unavailable": "命令行不可用", "next.files.terminal_unavailable_short": "宿主能力不可用",
      "next.files.terminal_reason": "宿主未提供可核验终端能力。未启动 PTY，也不会伪造提示符。", "next.files.terminal_height": "命令行高度",
      "next.files.terminal_height_hint": "拖拽或用方向键调整高度（200–240px）", "next.files.client_unavailable": "项目文件客户端不可用。",
      "next.files.content": "只读文件内容", "next.files.breadcrumb": "文件路径"
    },
    "en-US": {
      "next.files.title": "Files", "next.files.crumb_root": "Project root", "next.files.pick_project": "Select a project in the top bar first.",
      "next.files.tree_loading": "Loading project files…", "next.files.tree_error": "Could not load project files: {message}", "next.files.tree_empty": "This directory is empty.",
      "next.files.tree_truncated": "Directory truncated: {reason}", "next.files.search": "Filter loaded files", "next.files.search_loaded": "{count} matches among loaded entries (collapsed directories are not searched).",
      "next.files.search_empty": "No loaded entry matches.", "next.files.tree": "Project files", "next.files.tree_title": "Current project files", "next.files.refresh": "Refresh",
      "next.files.close": "Close", "next.files.tree_toggle": "Open project file tree", "next.files.select_file": "Choose a file from the tree.",
      "next.files.no_file": "No file selected", "next.files.file_loading": "Loading file…", "next.files.file_empty": "The file is empty.",
      "next.files.file_error": "Could not read file: {message}", "next.files.file_truncated": "Content truncated: {reason}",
      "next.files.binary": "This file is binary or not supported for text preview (HTTP 415): {message}", "next.files.too_large": "The file exceeds the read-only preview limit (HTTP 413): {message}",
      "next.files.forbidden": "You cannot read files in this project.", "next.files.not_found": "File or directory not found.",
      "next.files.dir_loading": "Loading directory…", "next.files.dir_error": "Could not read directory: {message}", "next.files.lines": "{count} lines",
      "next.files.terminal": "Command line", "next.files.terminal_unavailable": "Command line unavailable", "next.files.terminal_unavailable_short": "Host capability unavailable",
      "next.files.terminal_reason": "The host provides no verifiable terminal capability. No PTY is started and no prompt is fabricated.", "next.files.terminal_height": "Command line height",
      "next.files.terminal_height_hint": "Drag or use arrow keys to resize (200–240px)", "next.files.client_unavailable": "Project file client is unavailable.",
      "next.files.content": "Read-only file content", "next.files.breadcrumb": "File path"
    }
  };

  window.ACPNextModules = window.ACPNextModules || {};
  window.ACPNextModules["files-workspace"] = {
    descriptor: { id: "files-workspace", interfaceVersion: 1, version: "0.1.0", provides: ["project.files"], binds: ["projectId"], hosts: ["acp-web"], supportedInstancing: "single" },
    factory: create,
    _internals: { MAX_BYTES: MAX_BYTES, MAX_LINES: MAX_LINES, TREE_LIMIT: TREE_LIMIT }
  };
})();

