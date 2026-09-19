/**
 * ACP Structure Source Picker — reusable structure-source selection widget
 * @version 0.1.0
 *
 * Namespace: window.ACPSourcePicker
 *
 * Exposes:
 *   - mount(container, options)  → picker instance
 *     options: { mode, projectId, onChanged, onLoadItem, onLoadSelection,
 *                loadLabel, allowBatchTags, initialFilters, virtualItems,
 *                density }
 *   Instance methods:
 *     refresh()
 *     setProject(projectId|null)
 *     getSelection() → array of item snapshots
 *     clearSelection()
 *     destroy()
 *
 * Internal:
 *   - _fetchImpl  (default: window.fetch; tests inject a fake)
 *   - _esc        (XSS-safe text insertion)
 *   - _t          (i18n helper, reads from app t() with fallback)
 *   - _debounce   (standard debounce)
 *
 * i18n: picker.* keys in I18N dictionaries (zh-CN + en-US).
 */
(function () {
  "use strict";

  var VERSION = "0.1.0";
  var PAGE_LIMIT = 50;
  var MAX_SELECTION = 200;
  var SEARCH_DEBOUNCE_MS = 300;
  var PREFS_KEY = "acp.picker.view.";

  /* ---- internal helpers ---- */

  /** XSS-safe text insertion. */
  function _esc(s) {
    var d = document.createElement("div");
    d.appendChild(document.createTextNode(String(s == null ? "" : s)));
    return d.innerHTML;
  }

  /** i18n helper: try app's t(), fall back to key. */
  function _t(key, vars) {
    try {
      if (typeof window.t === "function") return window.t(key, vars);
    } catch (e) {}
    return key;
  }

  /** Simple debounce. */
  function _debounce(fn, ms) {
    var timer = null;
    return function () {
      var ctx = this;
      var args = arguments;
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        timer = null;
        fn.apply(ctx, args);
      }, ms);
    };
  }

  /** Fetch wrapper (test-overridable). */
  var _fetchImpl = function (url, opts) {
    return window.fetch(url, opts);
  };

  /** apiV2 helper — fetches /api/v2 + path. */
  async function _apiV2(path, opts) {
    opts = opts || {};
    if (!opts.signal && typeof AbortController !== "undefined") {
      var ac = new AbortController();
      var timer = setTimeout(function () {
        ac.abort();
      }, 8000);
      opts.signal = ac.signal;
    }
    var resp = await _fetchImpl("/api/v2" + path, opts);
    if (timer) clearTimeout(timer);
    if (!resp.ok) {
      var msg = "HTTP " + resp.status;
      try {
        var body = await resp.json();
        if (body && body.detail) {
          var d = body.detail;
          if (typeof d === "string") {
            msg = d;
          } else if (Array.isArray(d)) {
            msg = d
              .map(function (item) {
                if (item && typeof item === "object") {
                  var loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
                  return (loc ? loc + ": " : "") + (item.msg || JSON.stringify(item));
                }
                return String(item);
              })
              .join("; ");
          } else {
            msg = JSON.stringify(d);
          }
        }
      } catch (e) {}
      var err = new Error(msg);
      err.status = resp.status;
      err.body = body;
      throw err;
    }
    var ct = resp.headers.get("content-type") || "";
    return ct.includes("application/json") ? resp.json() : resp.text();
  }

  /** v1 API helper — fetches /api/v1 + path. */
  async function _apiV1(path, opts) {
    opts = opts || {};
    if (!opts.signal && typeof AbortController !== "undefined") {
      var ac = new AbortController();
      var timer = setTimeout(function () {
        ac.abort();
      }, 8000);
      opts.signal = ac.signal;
    }
    var resp = await _fetchImpl("/api/v1" + path, opts);
    if (timer) clearTimeout(timer);
    if (!resp.ok) {
      var msg = "HTTP " + resp.status;
      try {
        var body = await resp.json();
        if (body && body.detail) {
          var d = body.detail;
          if (typeof d === "string") msg = d;
          else if (Array.isArray(d))
            msg = d
              .map(function (i) {
                return i && typeof i === "object" ? i.msg || JSON.stringify(i) : String(i);
              })
              .join("; ");
          else msg = JSON.stringify(d);
        }
      } catch (e) {}
      throw new Error(msg);
    }
    var ct = resp.headers.get("content-type") || "";
    return ct.includes("application/json") ? resp.json() : resp.text();
  }

  /** Short date format. */
  function _shortDate(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      var mm = String(d.getMonth() + 1).padStart(2, "0");
      var dd = String(d.getDate()).padStart(2, "0");
      var hh = String(d.getHours()).padStart(2, "0");
      var mi = String(d.getMinutes()).padStart(2, "0");
      return d.getFullYear() + "-" + mm + "-" + dd + " " + hh + ":" + mi;
    } catch (e) {
      return "";
    }
  }

  /** Format date very short (MM-DD HH:mm). */
  function _shortDateBrief(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return "";
      var mm = String(d.getMonth() + 1).padStart(2, "0");
      var dd = String(d.getDate()).padStart(2, "0");
      var hh = String(d.getHours()).padStart(2, "0");
      var mi = String(d.getMinutes()).padStart(2, "0");
      return mm + "-" + dd + " " + hh + ":" + mi;
    } catch (e) {
      return "";
    }
  }

  /** Build source_id for legacy v1 detail loading. */
  function _sourceIdFromItem(item) {
    return item.source_id || "";
  }

  /* ---- Sort / group option maps ---- */

  var SORT_OPTIONS = [
    { value: "produced_desc", labelKey: "picker.sort.produced_desc" },
    { value: "produced_asc", labelKey: "picker.sort.produced_asc" },
    { value: "name_asc", labelKey: "picker.sort.name_asc" },
    { value: "name_desc", labelKey: "picker.sort.name_desc" },
    { value: "organized_desc", labelKey: "picker.sort.organized_desc" },
  ];

  var GROUP_OPTIONS = [
    { value: "none", labelKey: "picker.group.none" },
    { value: "job", labelKey: "picker.group.job" },
    { value: "tag", labelKey: "picker.group.tag" },
    { value: "role", labelKey: "picker.group.role" },
  ];

  var ROLE_OPTIONS = [
    { value: "", labelKey: "picker.role.all" },
    { value: "TS", labelKey: "picker.role.ts" },
    { value: "INT", labelKey: "picker.role.int" },
    { value: "__unlabeled__", labelKey: "picker.role.unlabeled" },
  ];

  /* ---- Preferences helpers ---- */

  function _loadPrefs() {
    try {
      var raw = localStorage.getItem(PREFS_KEY + "global");
      if (raw) {
        var p = JSON.parse(raw);
        if (p && typeof p === "object") return p;
      }
    } catch (e) {}
    return {};
  }

  function _savePrefs(prefs) {
    try {
      localStorage.setItem(PREFS_KEY + "global", JSON.stringify(prefs));
    } catch (e) {}
  }

  /* ---- Instance factory ---- */

  function _createInstance(container, options) {
    var opts = options || {};
    var mode = opts.mode === "multi" ? "multi" : "single";
    var projectId = opts.projectId || null;
    var onChanged = typeof opts.onChanged === "function" ? opts.onChanged : null;
    var onLoadItem = typeof opts.onLoadItem === "function" ? opts.onLoadItem : null;
    var onLoadSelection = typeof opts.onLoadSelection === "function" ? opts.onLoadSelection : null;
    var loadLabel = opts.loadLabel || _t("picker.load");
    var allowBatchTags = opts.allowBatchTags != null ? !!opts.allowBatchTags : mode === "multi";
    var virtualItems = Array.isArray(opts.virtualItems) ? opts.virtualItems.slice() : [];
    var density = opts.density === "editor" ? "editor" : "default";
    var toolbarLabel = opts.toolbarLabel || "";

    // State
    var legacyMode = false;
    var destroyed = false;
    var loading = false;
    var items = [];
    var total = 0;
    var nextCursor = null;
    var prevCursors = [];
    var currentCursor = null;
    var groups = [];
    var indexing = {};
    var facets = {};
    var projects = [];

    // Filters
    var prefs = _loadPrefs();
    var filterQ = "";
    var filterProject = projectId;
    var filterRole = "";
    var filterTags = [];
    var filterTagMatch = prefs.tag_match || "any";
    var filterSort = prefs.sort || "produced_desc";
    var filterGroupBy = density === "editor"
      ? "none"
      : (mode === "multi" ? prefs.group_by || "job" : prefs.group_by || "none");
    var filterLimit = density === "editor" ? 8 : prefs.limit || PAGE_LIMIT;

    // Selection (multi mode)
    var selectedUids = new Set();
    var selectedSnapshots = {};

    // Search timer
    var searchTimer = null;

    // Abort controller for in-flight requests
    var currentAbort = null;

    // DOM references
    var rootEl = null;
    var searchInput = null;
    var projectSelect = null;
    var roleSelect = null;
    var tagContainer = null;
    var tagMatchToggle = null;
    var sortSelect = null;
    var groupSelect = null;
    var listEl = null;
    var paginationEl = null;
    var indexingEl = null;
    var selectionBarEl = null;
    var selectAllCheckbox = null;

    // Dialog state
    var activeDialog = null;
    var activeDialogUid = null;

    /* ---- Build DOM ---- */

    function _buildUI() {
      rootEl = document.createElement("div");
      rootEl.className = "sp-root" + (density === "editor" ? " sp-density-editor" : "");

      var toolbarRow = density === "editor" ? _el("div", "sp-toolbar") : null;
      if (toolbarRow && toolbarLabel) {
        var toolbarTitle = _el("span", "sp-toolbar-title");
        toolbarTitle.textContent = toolbarLabel;
        toolbarRow.appendChild(toolbarTitle);
      }

      // Search row
      var searchRow = _el("div", "sp-search-row");
      searchInput = _el("input", "sp-search-input");
      searchInput.type = "text";
      searchInput.placeholder = _t("picker.search_ph");
      searchInput.setAttribute("aria-label", _t("picker.search_ph"));
      searchRow.appendChild(searchInput);
      (toolbarRow || rootEl).appendChild(searchRow);

      // Filter row
      var filterRow = _el("div", "sp-filter-row");

      // Project select
      projectSelect = _el("select", "sp-filter-select sp-project-select");
      projectSelect.setAttribute("aria-label", _t("picker.filter.project"));
      filterRow.appendChild(projectSelect);

      // Role select
      roleSelect = _el("select", "sp-filter-select sp-role-select");
      roleSelect.setAttribute("aria-label", _t("picker.filter.role"));
      ROLE_OPTIONS.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = _t(opt.labelKey);
        roleSelect.appendChild(o);
      });
      filterRow.appendChild(roleSelect);

      // Tag container
      tagContainer = _el("div", "sp-tag-container");
      tagContainer.style.display = "none";
      filterRow.appendChild(tagContainer);

      // Tag match toggle
      tagMatchToggle = _el("button", "sp-tag-match-toggle sp-btn-sm");
      tagMatchToggle.type = "button";
      tagMatchToggle.textContent = _t("picker.filter.tag_match_any");
      tagMatchToggle.title = _t("picker.filter.tag_match_toggle");
      tagMatchToggle.style.display = "none";
      filterRow.appendChild(tagMatchToggle);

      // Options row (sort + group)
      var optRow = _el("div", "sp-opt-row");

      var sortWrap = _el("label", "sp-opt-label");
      sortWrap.textContent = density === "editor" ? "" : _t("picker.filter.sort") + " ";
      sortSelect = _el("select", "sp-filter-select sp-sort-select");
      sortSelect.setAttribute("aria-label", _t("picker.filter.sort"));
      SORT_OPTIONS.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = _t(opt.labelKey);
        sortSelect.appendChild(o);
      });
      sortSelect.value = filterSort;
      if (density === "editor") {
        filterRow.appendChild(sortSelect);
      } else {
        sortWrap.appendChild(sortSelect);
        optRow.appendChild(sortWrap);
      }

      var groupWrap = _el("label", "sp-opt-label");
      groupWrap.textContent = _t("picker.filter.group") + " ";
      groupSelect = _el("select", "sp-filter-select sp-group-select");
      GROUP_OPTIONS.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = _t(opt.labelKey);
        groupSelect.appendChild(o);
      });
      groupSelect.value = filterGroupBy;
      groupWrap.appendChild(groupSelect);
      if (density !== "editor") optRow.appendChild(groupWrap);

      if (density !== "editor") rootEl.appendChild(optRow);

      if (toolbarRow) {
        toolbarRow.appendChild(filterRow);
        var refreshButton = _el("button", "sp-btn-sm sp-refresh-btn");
        refreshButton.type = "button";
        refreshButton.textContent = "↻";
        refreshButton.setAttribute("aria-label", _t("picker.refresh"));
        refreshButton.title = _t("picker.refresh");
        refreshButton.addEventListener("click", refresh);
        toolbarRow.appendChild(refreshButton);
        rootEl.appendChild(toolbarRow);
      } else {
        rootEl.insertBefore(filterRow, optRow);
      }

      var listShell = _el("div", "sp-list-shell");

      // Legacy mode hint
      var legacyHint = _el("div", "sp-legacy-hint");
      legacyHint.style.display = "none";
      legacyHint.textContent = _t("picker.legacy_mode");
      listShell.appendChild(legacyHint);

      // Indexing status
      indexingEl = _el("div", "sp-indexing");
      indexingEl.style.display = "none";
      listShell.appendChild(indexingEl);

      // List
      listEl = _el("div", "sp-list");
      listShell.appendChild(listEl);
      rootEl.appendChild(listShell);

      // Pagination
      paginationEl = _el("div", "sp-pagination");
      rootEl.appendChild(paginationEl);

      // Selection bar (multi mode)
      if (mode === "multi") {
        selectionBarEl = _el("div", "sp-selection-bar");
        selectionBarEl.style.display = "none";
        rootEl.appendChild(selectionBarEl);
      }

      container.appendChild(rootEl);
    }

    function _el(tag, cls) {
      var e = document.createElement(tag);
      if (cls) e.className = cls;
      return e;
    }

    /* ---- Event wiring ---- */

    function _wireEvents() {
      searchInput.addEventListener(
        "input",
        _debounce(function () {
          filterQ = searchInput.value.trim();
          _resetAndFetch();
        }, SEARCH_DEBOUNCE_MS)
      );

      projectSelect.addEventListener("change", function () {
        filterProject = projectSelect.value || null;
        _resetAndFetch();
      });

      roleSelect.addEventListener("change", function () {
        filterRole = roleSelect.value;
        _resetAndFetch();
      });

      sortSelect.addEventListener("change", function () {
        filterSort = sortSelect.value;
        _savePrefsFor("sort", filterSort);
        _resetAndFetch();
      });

      groupSelect.addEventListener("change", function () {
        filterGroupBy = groupSelect.value;
        _savePrefsFor("group_by", filterGroupBy);
        _resetAndFetch();
      });

      tagMatchToggle.addEventListener("click", function () {
        filterTagMatch = filterTagMatch === "any" ? "all" : "any";
        _savePrefsFor("tag_match", filterTagMatch);
        _updateTagMatchToggle();
        _resetAndFetch();
      });

      if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener("change", function () {
          _selectAllOnPage(selectAllCheckbox.checked);
        });
      }
    }

    function _savePrefsFor(key, val) {
      prefs[key] = val;
      _savePrefs(prefs);
    }

    function _updateTagMatchToggle() {
      if (!tagMatchToggle) return;
      tagMatchToggle.textContent =
        filterTagMatch === "all"
          ? _t("picker.filter.tag_match_all")
          : _t("picker.filter.tag_match_any");
    }

    /* ---- Project select population ---- */

    function _populateProjects() {
      // Clear
      projectSelect.innerHTML = "";

      // "Target project" option
      if (projectId) {
        var opt = document.createElement("option");
        opt.value = projectId;
        opt.textContent = _t("picker.filter.target_project");
        projectSelect.appendChild(opt);
      }

      // "All projects" option
      if (!projectId) {
        var allOpt = document.createElement("option");
        allOpt.value = "";
        allOpt.textContent = _t("picker.filter.all_projects");
        projectSelect.appendChild(allOpt);
      } else {
        // If projectId specified, also allow "all" if caller didn't restrict
        var allOpt2 = document.createElement("option");
        allOpt2.value = "__all__";
        allOpt2.textContent = _t("picker.filter.all_projects");
        projectSelect.appendChild(allOpt2);
      }

      // Additional projects from cache
      projects.forEach(function (p) {
        if (String(p.id) === String(projectId)) return;
        var o = document.createElement("option");
        o.value = String(p.id);
        o.textContent = p.name || "Project " + p.id;
        projectSelect.appendChild(o);
      });

      // Set current value
      if (filterProject) {
        projectSelect.value = filterProject;
      } else if (projectId) {
        projectSelect.value = projectId;
      } else {
        projectSelect.value = "";
      }
    }

    /* ---- Fetch & render ---- */

    function _resetAndFetch() {
      prevCursors = [];
      currentCursor = null;
      nextCursor = null;
      _fetchPage();
    }

    async function _fetchPage() {
      if (destroyed || loading) return;
      loading = true;
      _showLoading();

      // Abort previous request
      if (currentAbort) {
        try {
          currentAbort.abort();
        } catch (e) {}
      }
      currentAbort = new AbortController();

      try {
        if (legacyMode) {
          await _fetchLegacy();
        } else {
          await _fetchV2(currentAbort.signal);
        }
      } catch (e) {
        if (destroyed) return;
        // Detect legacy fallback
        if (e && (e.status === 404 || e.status === 405) && !legacyMode) {
          legacyMode = true;
          _updateLegacyHint();
          try {
            await _fetchLegacy();
          } catch (e2) {
            _showError(e2);
          }
        } else {
          _showError(e);
        }
      } finally {
        loading = false;
      }
    }

    async function _fetchV2(signal) {
      var params = new URLSearchParams();
      if (filterQ) params.set("q", filterQ);

      // Project filter
      var projVal = projectSelect ? projectSelect.value : "";
      if (projVal === "__all__" || (!projectId && projVal === "")) {
        params.set("all_projects", "true");
      } else if (projVal) {
        params.set("project_id", projVal);
      } else if (projectId) {
        params.set("project_id", projectId);
      }

      // Role filter
      if (filterRole === "__unlabeled__") {
        params.set("role", "");
      } else if (filterRole) {
        params.set("role", filterRole);
      }

      // Tags
      if (filterTags.length > 0) {
        params.set("tags", filterTags.join(","));
        params.set("tag_match", filterTagMatch);
      }

      // Sort & group
      params.set("sort", filterSort);
      params.set("group_by", filterGroupBy);
      params.set("limit", String(filterLimit));

      // Cursor
      if (currentCursor) params.set("cursor", currentCursor);

      var qs = params.toString();
      var path = "/structure-sources" + (qs ? "?" + qs : "");

      var body = await _apiV2(path, { signal: signal });
      if (destroyed) return;

      items = body.items || [];
      total = body.total || 0;
      nextCursor = body.next_cursor || null;
      groups = body.groups || [];
      indexing = body.indexing || {};

      _renderList();
      _renderPagination();
      _renderIndexing();
      _renderSelectionBar();
    }

    async function _fetchLegacy() {
      var params = new URLSearchParams();
      params.set("limit", "50");
      params.set("_t", String(Date.now()));

      var projVal = projectSelect ? projectSelect.value : "";
      if (projVal === "__all__" || (!projectId && projVal === "")) {
        params.set("all_projects", "true");
      } else if (projVal) {
        params.set("project_id", projVal);
      } else if (projectId) {
        params.set("project_id", projectId);
      }

      var body = await _apiV1("/structure-sources/recent?" + params.toString());
      if (destroyed) return;

      var sources = (body && body.sources) || [];

      // Client-side filtering
      if (filterQ) {
        var q = filterQ.toLowerCase();
        sources = sources.filter(function (s) {
          var hay = [
            s.molecule_name || "",
            s.label || "",
            s.job_name || "",
            s.formula || "",
            s.candidate_id || "",
            s.workflow || "",
          ]
            .join(" ")
            .toLowerCase();
          return hay.indexOf(q) >= 0;
        });
      }

      // Map to v2-like shape
      items = sources.map(function (s) {
        return {
          source_uid: s.source_id || "",
          source_id: s.source_id || "",
          custom_name: null,
          default_name: s.molecule_name || s.label || s.job_name || "",
          resolved_name: s.molecule_name || s.label || s.job_name || "",
          role: s.tag === "TS" ? "TS" : "",
          role_evidence: "",
          tags: [],
          metadata_revision: 0,
          job_id: s.job_id || "",
          job_name: s.job_name || "",
          job_resolved_name: s.job_name || "",
          molecule_name: s.molecule_name || "",
          workflow: s.workflow || "",
          project_id: s.project_id || "",
          project_name: "",
          source_kind: "",
          formula: s.formula || "",
          atom_count: s.atom_count || 0,
          charge: s.charge,
          multiplicity: s.multiplicity || 1,
          has_3d: true,
          remote: !!s.remote,
          availability: "available",
          candidate_id: s.candidate_id || "",
          produced_at: s.available_at || s.completed_at || "",
          job_status: s.job_status || "",
        };
      });

      total = items.length;
      nextCursor = null;
      groups = [];
      indexing = {};

      _renderList();
      _renderPagination();
      _renderIndexing();
      _renderSelectionBar();
    }

    function _showLoading() {
      listEl.innerHTML =
        '<div class="sp-list-note">' + _esc(_t("picker.loading")) + "</div>";
    }

    function _showError(e) {
      listEl.innerHTML =
        '<div class="sp-list-note sp-err">' +
        _esc(_t("picker.error") + ": " + (e.message || String(e))) +
        "</div>";
    }

    function _updateLegacyHint() {
      var hint = rootEl.querySelector(".sp-legacy-hint");
      if (hint) {
        hint.style.display = legacyMode ? "block" : "none";
      }
      // Hide facets-related controls in legacy mode
      if (legacyMode) {
        if (tagContainer) tagContainer.style.display = "none";
        if (tagMatchToggle) tagMatchToggle.style.display = "none";
      }
    }

    /* ---- Render list ---- */

    function _renderList() {
      if (!items.length && !virtualItems.length) {
        listEl.innerHTML =
          '<div class="sp-list-note">' + _esc(_t("picker.empty")) + "</div>";
        if (mode === "multi" && selectAllCheckbox) {
          selectAllCheckbox.checked = false;
          selectAllCheckbox.disabled = true;
        }
        return;
      }

      var html = "";

      if (virtualItems.length) {
        html += '<div class="sp-group-label">' + _esc(_t("picker.current_task")) + "</div>";
        virtualItems.forEach(function (item) { html += _renderRow(item); });
        if (items.length) {
          html += '<div class="sp-group-label">' + _esc(_t("picker.other_tasks")) + "</div>";
        }
      }

      if (filterGroupBy !== "none" && groups.length > 0) {
        // Grouped rendering
        groups.forEach(function (group) {
          html +=
            '<div class="sp-group-label">' +
            _esc(group.label || group.key) +
            ' <span class="sp-group-count">(' +
            group.count +
            ")</span></div>";
          // Items in this group — since server already paginates per-group,
          // we render all items (they belong to the current page's groups)
          var groupItems = items.filter(function (it) {
            return _itemGroupKey(it) === group.key;
          });
          groupItems.forEach(function (item) {
            html += _renderRow(item);
          });
        });
        // Items not in any group
        var groupedKeys = new Set(groups.map(function (g) { return g.key; }));
        var ungrouped = items.filter(function (it) {
          return !groupedKeys.has(_itemGroupKey(it));
        });
        ungrouped.forEach(function (item) {
          html += _renderRow(item);
        });
      } else {
        items.forEach(function (item) {
          html += _renderRow(item);
        });
      }

      listEl.innerHTML = html;
      _wireRowEvents();

      if (mode === "multi" && selectAllCheckbox) {
        selectAllCheckbox.disabled = false;
        var allSelected = items.every(function (it) {
          return selectedUids.has(it.source_uid);
        });
        selectAllCheckbox.checked = allSelected;
      }
    }

    function _itemGroupKey(item) {
      if (filterGroupBy === "job") return String(item.job_id || "");
      if (filterGroupBy === "role") return item.role || "__unlabeled__";
      if (filterGroupBy === "tag") {
        return item.tags && item.tags.length ? item.tags[0] : "__untagged__";
      }
      return "";
    }

    function _renderRow(item) {
      if (density === "editor") return _renderEditorRow(item);

      var uid = item.source_uid;
      var name = item.resolved_name || item.default_name || "--";
      var isAvailable = item.availability === "available";
      var isPendingSync = item.availability === "pending_sync";
      var isPendingFetch = item.availability === "pending_fetch";
      var isUnavailable = item.availability === "unavailable";
      var secondaryParts = [];
      if (item.candidate_id) secondaryParts.push(_esc(item.candidate_id));
      var jobName = item.job_resolved_name || item.job_name || "";
      if (jobName) secondaryParts.push(_esc(_t("picker.source_label")) + " " + _esc(jobName));
      if (item.produced_at) secondaryParts.push(_esc(_shortDateBrief(item.produced_at)));
      if (item.formula) secondaryParts.push(_esc(item.formula));

      var html = '<div class="sp-row" data-uid="' + _esc(uid) + '">';

      // Main line
      html += '<div class="sp-row-main">';

      // Checkbox (multi)
      if (mode === "multi") {
        var checked = selectedUids.has(uid) ? " checked" : "";
        html +=
          '<input type="checkbox" class="sp-row-cb" data-uid="' +
          _esc(uid) +
          '"' +
          checked +
          ' aria-label="' +
          _esc(_t("picker.select_item", { name: name })) +
          '">';
      }

      // Name
      html +=
        '<span class="sp-row-name" title="' + _esc(name) + '">' + _esc(name) + "</span>";
      if (density === "editor") {
        html += '<span class="sp-row-secondary">' + secondaryParts.join(" · ") + "</span>";
      }

      // Role badge
      if (item.role === "TS") {
        html += '<span class="sp-badge sp-badge-ts">TS</span>';
      } else if (item.role === "INT") {
        html += '<span class="sp-badge sp-badge-int">INT</span>';
      }

      // Availability badge
      if (isPendingSync) {
        html +=
          '<span class="sp-badge sp-badge-warn">' +
          _esc(_t("picker.availability.pending_sync")) +
          "</span>";
        html +=
          '<button type="button" class="sp-btn-sm sp-retry-btn" data-action="retry" aria-label="' +
          _esc(_t("picker.retry")) +
          '">' +
          _esc(_t("picker.retry")) +
          "</button>";
      } else if (isPendingFetch) {
        html +=
          '<span class="sp-badge sp-badge-warn">' +
          _esc(_t("picker.availability.pending_fetch")) +
          "</span>";
      } else if (isUnavailable) {
        html +=
          '<span class="sp-badge sp-badge-err">' +
          _esc(_t("picker.availability.unavailable")) +
          "</span>";
      }

      // Remote badge
      if (item.remote) {
        html +=
          '<span class="sp-badge sp-badge-remote">' +
          _esc(_t("picker.remote")) +
          "</span>";
      }

      // Actions (single mode)
      if (mode === "single") {
        var disabled = !isAvailable ? " disabled" : "";
        var title = !isAvailable ? ' title="' + _esc(_t("picker.load_disabled_reason")) + '"' : "";
        html +=
          '<button type="button" class="sp-btn-sm sp-load-btn" data-action="load" data-uid="' +
          _esc(uid) +
          '"' +
          disabled +
          title +
          ">" +
          _esc(loadLabel) +
          "</button>";
      }

      // Per-item actions (multi mode)
      if (mode === "multi") {
        html +=
          '<button type="button" class="sp-btn-sm sp-rename-btn" data-action="rename" data-uid="' +
          _esc(uid) +
          '" aria-label="' +
          _esc(_t("picker.rename.title")) +
          '">' +
          _esc(_t("picker.rename.short")) +
          "</button>";
        html +=
          '<button type="button" class="sp-btn-sm sp-tags-btn" data-action="tags" data-uid="' +
          _esc(uid) +
          '" aria-label="' +
          _esc(_t("picker.tags.title")) +
          '">' +
          _esc(_t("picker.tags.short")) +
          "</button>";
      }

      html += "</div>"; // .sp-row-main

      // Secondary line
      if (density !== "editor") {
        html += '<div class="sp-row-secondary">' + secondaryParts.join(" · ") + "</div>";
      }

      // Tags line
      if (density !== "editor" && item.tags && item.tags.length) {
        html += '<div class="sp-row-tags">';
        item.tags.forEach(function (tag) {
          html += '<span class="sp-tag-chip">' + _esc(tag) + "</span>";
        });
        html += "</div>";
      }

      html += "</div>"; // .sp-row
      return html;
    }

    function _renderEditorRow(item) {
      var uid = item.source_uid;
      var name = item.resolved_name || item.default_name || "--";
      var jobName = item.job_resolved_name || item.job_name || "";
      var sourceParts = [];
      if (item.candidate_id) sourceParts.push(item.candidate_id);
      if (jobName) sourceParts.push(jobName);
      var sourceText = sourceParts.join(" · ") || "--";
      var metaText = _shortDateBrief(item.produced_at) || item.formula || "--";
      var isAvailable = item.availability === "available";
      var role = item.role === "TS" || item.role === "INT" ? item.role : "--";
      var roleClass = item.role === "TS" ? " sp-badge-ts" : item.role === "INT" ? " sp-badge-int" : "";
      var html = '<div class="sp-row" data-uid="' + _esc(uid) + '"><div class="sp-row-main">';
      html += '<span class="sp-row-name" title="' + _esc(name) + '">' + _esc(name) + "</span>";
      html += '<span class="sp-row-source" title="' + _esc(sourceText) + '">' + _esc(sourceText) + "</span>";
      html += '<span class="sp-row-meta" title="' + _esc(metaText) + '">' + _esc(metaText) + "</span>";
      html += '<span class="sp-row-role"><span class="sp-badge' + roleClass + '">' + _esc(role) + "</span></span>";
      html += '<span class="sp-row-action">';
      if (item.availability === "pending_sync") {
        html += '<button type="button" class="sp-btn-sm sp-retry-btn" data-action="retry" aria-label="' +
          _esc(_t("picker.retry")) + '">' + _esc(_t("picker.retry")) + "</button>";
      } else {
        var disabled = !isAvailable ? " disabled" : "";
        var title = !isAvailable ? ' title="' + _esc(_t("picker.load_disabled_reason")) + '"' : "";
        html += '<button type="button" class="sp-btn-sm sp-load-btn" data-action="load" data-uid="' +
          _esc(uid) + '"' + disabled + title + ">" + _esc(loadLabel) + "</button>";
      }
      html += "</span></div></div>";
      return html;
    }

    function _wireRowEvents() {
      // Load buttons (single mode)
      listEl.querySelectorAll(".sp-load-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var uid = btn.getAttribute("data-uid");
          if (uid) _handleLoad(uid);
        });
      });

      // Checkboxes (multi mode)
      listEl.querySelectorAll(".sp-row-cb").forEach(function (cb) {
        cb.addEventListener("change", function () {
          var uid = cb.getAttribute("data-uid");
          if (!uid) return;
          if (cb.checked) {
            _selectUid(uid);
          } else {
            _deselectUid(uid);
          }
          _renderSelectionBar();
          _notifyChanged();
        });
      });

      // Rename buttons
      listEl.querySelectorAll(".sp-rename-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var uid = btn.getAttribute("data-uid");
          if (uid) _openRenameDialog(uid);
        });
      });

      // Tag buttons
      listEl.querySelectorAll(".sp-tags-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          var uid = btn.getAttribute("data-uid");
          if (uid) _openTagDialog(uid);
        });
      });

      // Retry buttons
      listEl.querySelectorAll(".sp-retry-btn").forEach(function (btn) {
        btn.addEventListener("click", function (e) {
          e.stopPropagation();
          refresh();
        });
      });
    }

    /* ---- Selection model ---- */

    function _selectUid(uid) {
      if (selectedUids.size >= MAX_SELECTION) {
        alert(_t("picker.selection_max", { max: MAX_SELECTION }));
        return;
      }
      selectedUids.add(uid);
      // Snapshot
      var item = _findItem(uid);
      if (item) selectedSnapshots[uid] = _snapshotItem(item);
    }

    function _deselectUid(uid) {
      selectedUids.delete(uid);
      delete selectedSnapshots[uid];
    }

    function _selectAllOnPage(checked) {
      items.forEach(function (item) {
        if (checked) {
          _selectUid(item.source_uid);
        } else {
          _deselectUid(item.source_uid);
        }
      });
      // Update checkboxes visually
      listEl.querySelectorAll(".sp-row-cb").forEach(function (cb) {
        var uid = cb.getAttribute("data-uid");
        if (uid) cb.checked = selectedUids.has(uid);
      });
      _renderSelectionBar();
      _notifyChanged();
    }

    function _findItem(uid) {
      for (var v = 0; v < virtualItems.length; v++) {
        if (virtualItems[v].source_uid === uid) return virtualItems[v];
      }
      for (var i = 0; i < items.length; i++) {
        if (items[i].source_uid === uid) return items[i];
      }
      return null;
    }

    function _snapshotItem(item) {
      return {
        source_uid: item.source_uid,
        source_id: item.source_id || "",
        resolved_name: item.resolved_name || "",
        default_name: item.default_name || "",
        custom_name: item.custom_name,
        role: item.role || "",
        tags: item.tags ? item.tags.slice() : [],
        metadata_revision: item.metadata_revision || 0,
        job_id: item.job_id || "",
        job_name: item.job_name || "",
        job_resolved_name: item.job_resolved_name || "",
        molecule_name: item.molecule_name || "",
        workflow: item.workflow || "",
        project_id: item.project_id || "",
        formula: item.formula || "",
        atom_count: item.atom_count || 0,
        charge: item.charge,
        multiplicity: item.multiplicity || 1,
        availability: item.availability || "",
        candidate_id: item.candidate_id || "",
        produced_at: item.produced_at || "",
        source_kind: item.source_kind || "",
        remote: !!item.remote,
      };
    }

    /* ---- Pagination ---- */

    function _renderPagination() {
      if (!paginationEl) return;

      if (legacyMode) {
        paginationEl.innerHTML =
          '<span class="sp-page-info">' +
          _esc(_t("picker.total_count", { total: total })) +
          "</span>";
        return;
      }

      var html = '<span class="sp-page-info">';
      html += _esc(_t("picker.total_count", { total: total }));
      html += "</span>";

      // Prev button
      html +=
        '<button type="button" class="sp-btn-sm sp-page-btn" data-action="prev"' +
        (prevCursors.length ? "" : " disabled") +
        ">" +
        _esc(_t("picker.prev_page")) +
        "</button>";

      var currentPage = prevCursors.length + 1;
      var pageCount = Math.max(1, Math.ceil(total / filterLimit));
      html += '<span class="sp-page-current" aria-live="polite">' + currentPage + "/" + pageCount + "</span>";

      // Next button
      html +=
        '<button type="button" class="sp-btn-sm sp-page-btn" data-action="next"' +
        (nextCursor ? "" : " disabled") +
        ">" +
        _esc(_t("picker.next_page")) +
        "</button>";

      // Select all checkbox (multi)
      if (mode === "multi") {
        html +=
          '<label class="sp-select-all-label"><input type="checkbox" class="sp-select-all-cb"> ' +
          _esc(_t("picker.select_page")) +
          "</label>";
      }

      paginationEl.innerHTML = html;

      // Wire pagination buttons
      paginationEl.querySelectorAll(".sp-page-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var action = btn.getAttribute("data-action");
          if (action === "next" && nextCursor) {
            prevCursors.push(currentCursor);
            currentCursor = nextCursor;
            _fetchPage();
          } else if (action === "prev" && prevCursors.length) {
            currentCursor = prevCursors.pop();
            _fetchPage();
          }
        });
      });

      // Wire select-all checkbox
      selectAllCheckbox = paginationEl.querySelector(".sp-select-all-cb");
      if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener("change", function () {
          _selectAllOnPage(selectAllCheckbox.checked);
        });
      }
    }

    /* ---- Indexing status ---- */

    function _renderIndexing() {
      if (!indexingEl) return;
      if (legacyMode) {
        indexingEl.style.display = "none";
        return;
      }

      var state = indexing.indexing_state || "";
      var pending = indexing.pending_jobs || 0;

      if (state === "running" || pending > 0) {
        indexingEl.style.display = "block";
        indexingEl.textContent = _t("picker.indexing", { count: pending });
      } else {
        indexingEl.style.display = "none";
      }
    }

    /* ---- Selection bar ---- */

    function _renderSelectionBar() {
      if (!selectionBarEl || mode !== "multi") return;

      var count = selectedUids.size;
      if (count === 0) {
        selectionBarEl.style.display = "none";
        return;
      }

      selectionBarEl.style.display = "flex";

      // Count items not in current view
      var currentUids = new Set(items.map(function (it) { return it.source_uid; }));
      var hiddenCount = 0;
      selectedUids.forEach(function (uid) {
        if (!currentUids.has(uid)) hiddenCount++;
      });

      var html = '<span class="sp-sel-count">';
      html += _esc(_t("picker.selected_count", { count: count }));
      if (hiddenCount > 0) {
        html += "（" + _esc(_t("picker.hidden_selected", { count: hiddenCount })) + "）";
      }
      html += "</span>";

      if (allowBatchTags) {
        html +=
          '<button type="button" class="sp-btn-sm sp-batch-btn" data-action="batch-add-tags">' +
          _esc(_t("picker.batch.add_tags")) +
          "</button>";
        html +=
          '<button type="button" class="sp-btn-sm sp-batch-btn" data-action="batch-remove-tags">' +
          _esc(_t("picker.batch.remove_tags")) +
          "</button>";
      }

      html +=
        '<button type="button" class="sp-btn-sm sp-batch-btn" data-action="batch-clear">' +
        _esc(_t("picker.batch.clear")) +
        "</button>";

      if (mode === "multi" && onLoadSelection) {
        html +=
          '<button type="button" class="sp-btn-sm sp-batch-btn sp-batch-load" data-action="batch-load">' +
          _esc(_t("picker.batch.load")) +
          "</button>";
      }

      selectionBarEl.innerHTML = html;

      // Wire batch buttons
      selectionBarEl.querySelectorAll(".sp-batch-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var action = btn.getAttribute("data-action");
          if (action === "batch-clear") {
            selectedUids.clear();
            selectedSnapshots = {};
            _renderList();
            _renderSelectionBar();
            _notifyChanged();
          } else if (action === "batch-add-tags") {
            _openBatchTagDialog("add");
          } else if (action === "batch-remove-tags") {
            _openBatchTagDialog("remove");
          } else if (action === "batch-load") {
            _handleBatchLoad();
          }
        });
      });
    }

    /* ---- Load handlers ---- */

    function _handleLoad(uid) {
      var item = _findItem(uid);
      if (!item) return;
      if (item.availability !== "available") return;
      if (onLoadItem) {
        onLoadItem(_snapshotItem(item));
      }
    }

    function _handleBatchLoad() {
      if (!onLoadSelection) return;
      var snapshots = [];
      var seen = {};
      selectedUids.forEach(function (uid) {
        if (snapshots.length >= MAX_SELECTION) return;
        if (seen[uid]) return;
        seen[uid] = true;
        // Use snapshot if available, else try to find in current items
        var snap = selectedSnapshots[uid];
        if (!snap) {
          var item = _findItem(uid);
          if (item) snap = _snapshotItem(item);
        }
        if (snap && snap.availability === "available") {
          snapshots.push(snap);
        }
      });
      if (snapshots.length >= MAX_SELECTION) {
        alert(_t("picker.batch.max_notice", { max: MAX_SELECTION }));
      }
      if (snapshots.length > 0) {
        onLoadSelection(snapshots);
      }
    }

    function _notifyChanged() {
      if (onChanged) onChanged(getSelection());
    }

    /* ---- Rename dialog ---- */

    function _openRenameDialog(uid) {
      _closeActiveDialog();
      var item = _findItem(uid);
      if (!item) {
        // Try snapshot
        item = selectedSnapshots[uid];
      }
      if (!item) return;

      activeDialogUid = uid;
      var currentName = item.custom_name || "";
      var defaultName = item.default_name || "";

      var dialog = document.createElement("div");
      dialog.className = "sp-dialog sp-rename-dialog";
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-label", _t("picker.rename.title"));

      var html = '<div class="sp-dialog-title">' + _esc(_t("picker.rename.title")) + "</div>";
      html +=
        '<div class="sp-dialog-default">' +
        _esc(_t("picker.rename.default_label", { name: defaultName })) +
        "</div>";
      html +=
        '<input type="text" class="sp-dialog-input sp-rename-input" maxlength="200" value="' +
        _esc(currentName) +
        '" placeholder="' +
        _esc(defaultName) +
        '">';
      html +=
        '<div class="sp-dialog-hint">' + _esc(_t("picker.rename.hint")) + "</div>";
      html += '<div class="sp-dialog-err sp-rename-err" style="display:none"></div>';
      html += '<div class="sp-dialog-actions">';
      html +=
        '<button type="button" class="sp-btn-sm sp-dialog-restore">' +
        _esc(_t("picker.rename.restore")) +
        "</button>";
      html +=
        '<button type="button" class="sp-btn-sm sp-dialog-cancel">' +
        _esc(_t("picker.rename.cancel")) +
        "</button>";
      html +=
        '<button type="button" class="sp-btn-sm sp-dialog-save" disabled>' +
        _esc(_t("picker.rename.save")) +
        "</button>";
      html += "</div>";

      dialog.innerHTML = html;

      // Position near the row
      var row = listEl.querySelector('[data-uid="' + CSS.escape(uid) + '"]');
      if (row) {
        row.style.position = "relative";
        row.appendChild(dialog);
      } else {
        listEl.appendChild(dialog);
      }

      activeDialog = dialog;

      var input = dialog.querySelector(".sp-rename-input");
      var saveBtn = dialog.querySelector(".sp-dialog-save");
      var cancelBtn = dialog.querySelector(".sp-dialog-cancel");
      var restoreBtn = dialog.querySelector(".sp-dialog-restore");
      var errEl = dialog.querySelector(".sp-rename-err");

      // Focus input
      setTimeout(function () {
        if (input) input.focus();
      }, 0);

      // Update save state
      function _updateSave() {
        var val = (input.value || "").trim();
        saveBtn.disabled = val === currentName.trim() || val.length === 0;
      }
      if (input) input.addEventListener("input", _updateSave);

      // Save
      if (saveBtn) {
        saveBtn.addEventListener("click", async function () {
          var newName = (input.value || "").trim();
          if (!newName || newName.length > 200) {
            errEl.textContent = _t("picker.rename.invalid");
            errEl.style.display = "block";
            return;
          }
          saveBtn.disabled = true;
          errEl.style.display = "none";
          try {
            var revision =
              item.metadata_revision != null ? item.metadata_revision : 0;
            await _apiV2(
              "/structure-sources/" + encodeURIComponent(uid) + "/metadata",
              {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  custom_name: newName,
                  expected_revision: revision,
                }),
              }
            );
            // Update local
            item.custom_name = newName;
            item.resolved_name = newName;
            item.metadata_revision = (revision || 0) + 1;
            if (selectedSnapshots[uid]) {
              selectedSnapshots[uid].custom_name = newName;
              selectedSnapshots[uid].resolved_name = newName;
              selectedSnapshots[uid].metadata_revision = item.metadata_revision;
            }
            _closeActiveDialog();
            _renderList();
          } catch (e) {
            if (e && e.status === 409) {
              errEl.textContent = _t("picker.rename.conflict");
              errEl.style.display = "block";
              // Refresh revision from conflict response
              try {
                var body = e.body;
                if (body && body.detail && body.detail.current) {
                  item.metadata_revision =
                    body.detail.current.metadata_revision || item.metadata_revision;
                }
              } catch (e2) {}
            } else {
              errEl.textContent = _t("picker.rename.failed", {
                message: e.message || String(e),
              });
              errEl.style.display = "block";
            }
            saveBtn.disabled = false;
          }
        });
      }

      // Restore default
      if (restoreBtn) {
        restoreBtn.addEventListener("click", async function () {
          restoreBtn.disabled = true;
          errEl.style.display = "none";
          try {
            var revision =
              item.metadata_revision != null ? item.metadata_revision : 0;
            await _apiV2(
              "/structure-sources/" + encodeURIComponent(uid) + "/metadata",
              {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  custom_name: null,
                  expected_revision: revision,
                }),
              }
            );
            item.custom_name = null;
            item.resolved_name = item.default_name || "";
            item.metadata_revision = (revision || 0) + 1;
            if (selectedSnapshots[uid]) {
              selectedSnapshots[uid].custom_name = null;
              selectedSnapshots[uid].resolved_name = item.resolved_name;
              selectedSnapshots[uid].metadata_revision = item.metadata_revision;
            }
            _closeActiveDialog();
            _renderList();
          } catch (e) {
            if (e && e.status === 409) {
              errEl.textContent = _t("picker.rename.conflict");
              errEl.style.display = "block";
              try {
                var body = e.body;
                if (body && body.detail && body.detail.current) {
                  item.metadata_revision =
                    body.detail.current.metadata_revision || item.metadata_revision;
                }
              } catch (e2) {}
            } else {
              errEl.textContent = _t("picker.rename.failed", {
                message: e.message || String(e),
              });
              errEl.style.display = "block";
            }
            restoreBtn.disabled = false;
          }
        });
      }

      // Cancel
      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          _closeActiveDialog();
        });
      }

      // Esc to close
      dialog.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          e.preventDefault();
          _closeActiveDialog();
        } else if (e.key === "Enter" && !saveBtn.disabled) {
          e.preventDefault();
          saveBtn.click();
        }
      });
    }

    /* ---- Tag dialog ---- */

    function _openTagDialog(uid) {
      _closeActiveDialog();
      var item = _findItem(uid);
      if (!item) item = selectedSnapshots[uid];
      if (!item) return;

      activeDialogUid = uid;
      var currentTags = item.tags ? item.tags.slice() : [];

      var dialog = document.createElement("div");
      dialog.className = "sp-dialog sp-tag-dialog";
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-label", _t("picker.tags.title"));

      var html = '<div class="sp-dialog-title">' + _esc(_t("picker.tags.title")) + "</div>";
      html += '<div class="sp-tag-current"></div>';
      html +=
        '<div class="sp-tag-input-row"><input type="text" class="sp-dialog-input sp-tag-input" maxlength="32" placeholder="' +
        _esc(_t("picker.tags.input_ph")) +
        '"><button type="button" class="sp-btn-sm sp-tag-add-btn">' +
        _esc(_t("picker.tags.add")) +
        "</button></div>";
      html += '<div class="sp-dialog-hint">' + _esc(_t("picker.tags.hint")) + "</div>";
      html += '<div class="sp-dialog-err sp-tag-err" style="display:none"></div>';
      html += '<div class="sp-dialog-actions">';
      html +=
        '<button type="button" class="sp-btn-sm sp-dialog-cancel">' +
        _esc(_t("picker.tags.close")) +
        "</button>";
      html += "</div>";

      dialog.innerHTML = html;

      // Render current tags
      var tagListEl = dialog.querySelector(".sp-tag-current");
      _renderTagChips(tagListEl, currentTags, uid, item);

      // Position
      var row = listEl.querySelector('[data-uid="' + CSS.escape(uid) + '"]');
      if (row) {
        row.style.position = "relative";
        row.appendChild(dialog);
      } else {
        listEl.appendChild(dialog);
      }

      activeDialog = dialog;

      var input = dialog.querySelector(".sp-tag-input");
      var addBtn = dialog.querySelector(".sp-tag-add-btn");
      var errEl = dialog.querySelector(".sp-tag-err");

      setTimeout(function () {
        if (input) input.focus();
      }, 0);

      function _doAdd() {
        var tag = (input.value || "").trim();
        if (!tag) return;
        if (tag.length > 32) {
          errEl.textContent = _t("picker.tags.too_long");
          errEl.style.display = "block";
          return;
        }
        if (currentTags.length >= 20) {
          errEl.textContent = _t("picker.tags.max_count");
          errEl.style.display = "block";
          return;
        }
        // Deduplicate
        var exists = currentTags.some(function (t) {
          return t.toLowerCase() === tag.toLowerCase();
        });
        if (exists) {
          input.value = "";
          return;
        }
        currentTags.push(tag);
        input.value = "";
        errEl.style.display = "none";

        // Patch to server
        _patchTags(uid, item, { add_tags: [tag] }, function (err) {
          if (err) {
            // Rollback
            currentTags.pop();
            _renderTagChips(tagListEl, currentTags, uid, item);
            errEl.textContent = _t("picker.tags.failed", { message: err.message || String(err) });
            errEl.style.display = "block";
          } else {
            _renderTagChips(tagListEl, currentTags, uid, item);
          }
        });
      }

      if (addBtn) addBtn.addEventListener("click", _doAdd);
      if (input) {
        input.addEventListener("keydown", function (e) {
          if (e.key === "Enter") {
            e.preventDefault();
            _doAdd();
          }
        });
      }

      var cancelBtn = dialog.querySelector(".sp-dialog-cancel");
      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          _closeActiveDialog();
        });
      }

      dialog.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          e.preventDefault();
          _closeActiveDialog();
        }
      });
    }

    function _renderTagChips(container, tags, uid, item) {
      if (!container) return;
      var html = "";
      tags.forEach(function (tag, idx) {
        html +=
          '<span class="sp-tag-chip sp-tag-removable">' +
          _esc(tag) +
          ' <button type="button" class="sp-tag-remove" data-idx="' +
          idx +
          '" aria-label="' +
          _esc(_t("picker.tags.remove")) +
          '">×</button></span>';
      });
      if (!tags.length) {
        html =
          '<span class="sp-tag-empty">' + _esc(_t("picker.tags.none")) + "</span>";
      }
      container.innerHTML = html;

      container.querySelectorAll(".sp-tag-remove").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var idx = parseInt(btn.getAttribute("data-idx"), 10);
          var tag = tags[idx];
          if (tag == null) return;
          tags.splice(idx, 1);

          _patchTags(uid, item, { remove_tags: [tag] }, function (err) {
            if (err) {
              // Rollback
              tags.splice(idx, 0, tag);
              _renderTagChips(container, tags, uid, item);
            } else {
              _renderTagChips(container, tags, uid, item);
            }
          });
        });
      });
    }

    async function _patchTags(uid, item, patchBody, cb) {
      try {
        var revision =
          item.metadata_revision != null ? item.metadata_revision : 0;
        var body = Object.assign({}, patchBody, {
          expected_revision: revision,
        });
        await _apiV2(
          "/structure-sources/" + encodeURIComponent(uid) + "/metadata",
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
        );
        item.metadata_revision = (revision || 0) + 1;
        if (item.tags && patchBody.add_tags) {
          patchBody.add_tags.forEach(function (t) {
            if (item.tags.indexOf(t) < 0) item.tags.push(t);
          });
        }
        if (item.tags && patchBody.remove_tags) {
          patchBody.remove_tags.forEach(function (t) {
            var idx = item.tags.indexOf(t);
            if (idx >= 0) item.tags.splice(idx, 1);
          });
        }
        // Update snapshot
        if (selectedSnapshots[uid]) {
          selectedSnapshots[uid].tags = item.tags ? item.tags.slice() : [];
          selectedSnapshots[uid].metadata_revision = item.metadata_revision;
        }
        if (cb) cb(null);
      } catch (e) {
        if (cb) cb(e);
      }
    }

    /* ---- Batch tag dialog ---- */

    function _openBatchTagDialog(action) {
      _closeActiveDialog();

      var dialog = document.createElement("div");
      dialog.className = "sp-dialog sp-batch-tag-dialog";
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute(
        "aria-label",
        action === "add"
          ? _t("picker.batch.add_tags")
          : _t("picker.batch.remove_tags")
      );

      var titleText =
        action === "add"
          ? _t("picker.batch.add_tags")
          : _t("picker.batch.remove_tags");

      var html = '<div class="sp-dialog-title">' + _esc(titleText) + "</div>";
      html +=
        '<div class="sp-dialog-hint">' +
        _esc(_t("picker.batch.tag_hint", { count: selectedUids.size })) +
        "</div>";
      html +=
        '<input type="text" class="sp-dialog-input sp-batch-tag-input" maxlength="32" placeholder="' +
        _esc(_t("picker.tags.input_ph")) +
        '">';
      html += '<div class="sp-dialog-err sp-batch-tag-err" style="display:none"></div>';
      html += '<div class="sp-dialog-actions">';
      html +=
        '<button type="button" class="sp-btn-sm sp-dialog-cancel">' +
        _esc(_t("picker.rename.cancel")) +
        "</button>";
      html +=
        '<button type="button" class="sp-btn-sm sp-dialog-save">' +
        _esc(_t("picker.batch.apply")) +
        "</button>";
      html += "</div>";

      dialog.innerHTML = html;
      listEl.appendChild(dialog);
      activeDialog = dialog;

      var input = dialog.querySelector(".sp-batch-tag-input");
      var applyBtn = dialog.querySelector(".sp-dialog-save");
      var cancelBtn = dialog.querySelector(".sp-dialog-cancel");
      var errEl = dialog.querySelector(".sp-batch-tag-err");

      setTimeout(function () {
        if (input) input.focus();
      }, 0);

      if (applyBtn) {
        applyBtn.addEventListener("click", async function () {
          var tag = (input.value || "").trim();
          if (!tag || tag.length > 32) {
            errEl.textContent = _t("picker.tags.too_long");
            errEl.style.display = "block";
            return;
          }
          applyBtn.disabled = true;
          errEl.style.display = "none";

          var uids = [];
          selectedUids.forEach(function (u) { uids.push(u); });

          try {
            await _batchPatchTags(uids, action, tag);
            _closeActiveDialog();
            _renderList();
          } catch (e) {
            errEl.textContent = e.message || String(e);
            errEl.style.display = "block";
            applyBtn.disabled = false;
          }
        });
      }

      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          _closeActiveDialog();
        });
      }

      dialog.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          e.preventDefault();
          _closeActiveDialog();
        } else if (e.key === "Enter" && applyBtn && !applyBtn.disabled) {
          e.preventDefault();
          applyBtn.click();
        }
      });
    }

    async function _batchPatchTags(uids, action, tag) {
      // Fetch fresh revisions for each uid (parallel, cap 200)
      var capped = uids.slice(0, MAX_SELECTION);
      var freshItems = await Promise.all(
        capped.map(function (uid) {
          return _apiV2("/structure-sources/" + encodeURIComponent(uid))
            .then(function (body) {
              return { uid: uid, item: body, error: null };
            })
            .catch(function (e) {
              return { uid: uid, item: null, error: e };
            });
        })
      );

      // Build batch payload
      var batchItems = [];
      var failedFetches = [];
      freshItems.forEach(function (entry) {
        if (entry.error || !entry.item) {
          failedFetches.push(entry);
          return;
        }
        var revision = entry.item.metadata_revision || 0;
        var patch = {
          source_uid: entry.uid,
          expected_revision: revision,
        };
        if (action === "add") {
          patch.add_tags = [tag];
        } else {
          patch.remove_tags = [tag];
        }
        batchItems.push(patch);
      });

      if (!batchItems.length) {
        throw new Error(_t("picker.batch.all_failed"));
      }

      var result = await _apiV2("/structure-sources/batch-metadata", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: batchItems }),
      });

      // Handle results
      var succeeded = (result && result.succeeded) || 0;
      var failed = (result && result.failed) || [];
      var conflicts = (result && result.conflicts) || [];

      // Update local items for successes
      batchItems.forEach(function (patch) {
        var uid = patch.source_uid;
        var item = _findItem(uid);
        if (!item) item = selectedSnapshots[uid];
        if (!item) return;
        // Check if this uid is in failures/conflicts
        var isFailed = failed.some(function (f) { return f.source_uid === uid; });
        var isConflict = conflicts.some(function (c) { return c.source_uid === uid; });
        if (isFailed || isConflict) return;
        if (action === "add" && item.tags && item.tags.indexOf(tag) < 0) {
          item.tags.push(tag);
        }
        if (action === "remove" && item.tags) {
          var idx = item.tags.indexOf(tag);
          if (idx >= 0) item.tags.splice(idx, 1);
        }
        item.metadata_revision = (item.metadata_revision || 0) + 1;
        if (selectedSnapshots[uid]) {
          selectedSnapshots[uid].tags = item.tags ? item.tags.slice() : [];
          selectedSnapshots[uid].metadata_revision = item.metadata_revision;
        }
      });

      // Report partial failures
      var totalFailed = failed.length + conflicts.length + failedFetches.length;
      if (totalFailed > 0) {
        throw new Error(
          _t("picker.batch.partial_fail", {
            succeeded: succeeded,
            failed: totalFailed,
          })
        );
      }
    }

    function _closeActiveDialog() {
      if (activeDialog && activeDialog.parentNode) {
        activeDialog.parentNode.removeChild(activeDialog);
      }
      activeDialog = null;
      activeDialogUid = null;
    }

    /* ---- Facets loading ---- */

    async function _loadFacets() {
      if (legacyMode) return;
      try {
        var params = new URLSearchParams();
        if (filterQ) params.set("q", filterQ);
        var projVal = projectSelect ? projectSelect.value : "";
        if (projVal === "__all__" || (!projectId && projVal === "")) {
          params.set("all_projects", "true");
        } else if (projVal) {
          params.set("project_id", projVal);
        } else if (projectId) {
          params.set("project_id", projectId);
        }
        if (filterRole) params.set("role", filterRole);

        var qs = params.toString();
        var body = await _apiV2(
          "/structure-sources/facets" + (qs ? "?" + qs : "")
        );
        if (destroyed) return;
        facets = body || {};
        _renderTagFilter();
      } catch (e) {
        // Facets are optional; don't break the picker
      }
    }

    function _renderTagFilter() {
      if (!tagContainer) return;
      if (legacyMode) {
        tagContainer.style.display = "none";
        tagMatchToggle.style.display = "none";
        return;
      }

      var tagFacets = facets.tags || [];
      if (!tagFacets.length && !filterTags.length) {
        tagContainer.style.display = "none";
        tagMatchToggle.style.display = "none";
        return;
      }

      tagContainer.style.display = "flex";
      tagMatchToggle.style.display = "inline-flex";

      var html = "";
      // Selected tags as removable chips
      filterTags.forEach(function (tag) {
        html +=
          '<span class="sp-tag-chip sp-tag-removable sp-filter-tag">' +
          _esc(tag) +
          ' <button type="button" class="sp-tag-remove" data-tag="' +
          _esc(tag) +
          '" aria-label="' +
          _esc(_t("picker.tags.remove")) +
          '">×</button></span>';
      });

      // Available tags as clickable chips
      tagFacets.forEach(function (tf) {
        if (filterTags.indexOf(tf.tag) >= 0) return;
        html +=
          '<span class="sp-tag-chip sp-filter-tag-available" data-tag="' +
          _esc(tf.tag) +
          '">' +
          _esc(tf.tag) +
          " (" +
          tf.count +
          ")</span>";
      });

      tagContainer.innerHTML = html;

      // Wire remove buttons
      tagContainer.querySelectorAll(".sp-tag-remove").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var tag = btn.getAttribute("data-tag");
          filterTags = filterTags.filter(function (t) { return t !== tag; });
          _resetAndFetch();
        });
      });

      // Wire available tag clicks
      tagContainer
        .querySelectorAll(".sp-filter-tag-available")
        .forEach(function (chip) {
          chip.addEventListener("click", function () {
            var tag = chip.getAttribute("data-tag");
            if (tag && filterTags.indexOf(tag) < 0) {
              filterTags.push(tag);
              _resetAndFetch();
            }
          });
        });
    }

    /* ---- Projects loading ---- */

    async function _loadProjects() {
      try {
        var body = await _apiV1("/projects");
        if (destroyed) return;
        projects = (body && body.projects) || [];
        _populateProjects();
      } catch (e) {
        // Projects are optional
        _populateProjects();
      }
    }

    /* ---- Public API ---- */

    function refresh() {
      _fetchPage();
      _loadFacets();
    }

    function setProject(pid) {
      projectId = pid || null;
      filterProject = projectId;
      _populateProjects();
      _resetAndFetch();
      _loadFacets();
    }

    function getSelection() {
      var result = [];
      var seen = {};
      selectedUids.forEach(function (uid) {
        if (seen[uid]) return;
        seen[uid] = true;
        var snap = selectedSnapshots[uid];
        if (snap) {
          result.push(JSON.parse(JSON.stringify(snap)));
        }
      });
      return result;
    }

    function clearSelection() {
      selectedUids.clear();
      selectedSnapshots = {};
      _renderList();
      _renderSelectionBar();
      _notifyChanged();
    }

    function setVirtualItems(nextItems) {
      virtualItems = Array.isArray(nextItems) ? nextItems.slice() : [];
      _renderList();
    }

    function destroy() {
      destroyed = true;
      _closeActiveDialog();
      if (currentAbort) {
        try {
          currentAbort.abort();
        } catch (e) {}
      }
      if (searchTimer) clearTimeout(searchTimer);
      if (rootEl && rootEl.parentNode) {
        rootEl.parentNode.removeChild(rootEl);
      }
    }

    /* ---- Init ---- */

    function _init() {
      _buildUI();
      _wireEvents();
      _loadProjects();
      _fetchPage();
      _loadFacets();
    }

    _init();

    return {
      refresh: refresh,
      setProject: setProject,
      getSelection: getSelection,
      clearSelection: clearSelection,
      setVirtualItems: setVirtualItems,
      destroy: destroy,
    };
  }

  /* ---- Public namespace ---- */

  var ACPSourcePicker = {
    VERSION: VERSION,
    mount: function (container, options) {
      if (!container) throw new Error("ACPSourcePicker.mount: container required");
      return _createInstance(container, options);
    },
    _fetchImpl: _fetchImpl,
  };

  // Expose for tests
  Object.defineProperty(ACPSourcePicker, "_fetchImpl", {
    get: function () {
      return _fetchImpl;
    },
    set: function (fn) {
      _fetchImpl = fn;
    },
    enumerable: true,
    configurable: true,
  });

  window.ACPSourcePicker = ACPSourcePicker;
})();
