/*
 * ACP Next Shell — layout manager (UI-01).
 * Authority: Shell Design §1 (skeleton/panel chrome), §2 (four states +
 * error boundaries), §3 (recipes / focus), §5 (keyboard + a11y).
 *
 * Owns:
 *   - applyRecipe(id): declarative panel rendering into workspace slots;
 *   - panel chrome (title / instance chip / follow-focus dot / reload /
 *     hidden close placeholder / slot-collapse toggle);
 *   - panel lifecycle: mount → updateContext → (suspend/resume) → dispose,
 *     all through the ModuleInstance interface, with a per-panel generation
 *     that makes late results no-ops (no zombie remounts);
 *   - four states via data-acp-state="loading|empty|error|denied|ready"
 *     (test hook) with panel-local error boundary (inset red + role=alert +
 *     retry that invalidates the loader cache);
 *   - workspace switching (tabs, Alt+1/2/3, roving tabindex, focus restore),
 *     focus chip rendering, follow-focus dot pulses, document.title suffix;
 *   - A-63 keyboard accessibility: skip link retargeted to the active
 *     workspace, tablist ARIA + arrow/Enter/Space activation, a documented
 *     focus-order walk (workspace tabs → panel titles → panel content) with
 *     `data-acp-focus-*` metadata and a polite live region, and the Escape
 *     ladder (panel body → panel title → workspace tab);
 *   - viewer SINGLE active instance: recipes share instanceId `shared-viewer`
 *     and the live panel element is MOVED between slots — no remount, no
 *     flash (design §3.1).
 *
 * Unknown / pending modules render as placeholder panels and never block
 * sibling panels (design §3.1 未知模块占位).
 */
window.ACPShell = window.ACPShell || {};
ACPShell.layout = (function () {
  "use strict";

  var WORKSPACE_IDS = ["research", "drawing", "molecular", "config", "execution", "files"];
  var SLOT_IDS = ["main", "side", "bottom"];
  var COLLAPSE_KEY = "acp-next-collapse";

  // A-63 focus-order walk: panels are stepped in slot order, so the keyboard
  // route always reads main → side → bottom, matching the visual grid.
  var SLOT_RANK = { main: 0, side: 1, bottom: 2 };
  var FOCUSABLE_TAGS = { A: true, BUTTON: true, SELECT: true, INPUT: true, TEXTAREA: true };

  var deps = { context: null, loader: null, registry: null, i18n: null, request: null };
  var panels = Object.create(null);        // instanceId -> record
  var workspaceEls = Object.create(null);  // wsId -> section el
  var slotEls = Object.create(null);       // wsId -> { main, side, bottom }
  var tabEls = Object.create(null);        // wsId -> button el
  var recipeByWorkspace = Object.create(null); // wsId -> applied recipe id
  var currentWorkspace = "research";
  var focusMemory = new Map();
  var shellRoot = null;
  var mainEl = null;
  var fatalEl = null;
  var focusChipEl = null;
  var skipLinkEl = null;
  var liveEl = null;
  var contextUnsub = null;
  var started = false;

  // ── small DOM helpers (stub-friendly: no innerHTML, no closest) ──

  function doc() { return ACPShell.env ? ACPShell.env.doc() : window.document; }

  // Host seam (js/shell/host-env.js): an embedding host may namespace storage
  // and own documentElement/title. Without it these are today's exact calls.
  function storageGet(key) {
    if (ACPShell.env) return ACPShell.env.storage.get(key);
    try { return window.localStorage && window.localStorage.getItem(key); } catch (e) { return null; }
  }
  function storageSet(key, value) {
    if (ACPShell.env) { ACPShell.env.storage.set(key, value); return; }
    try {
      window.localStorage && window.localStorage.setItem(key, value);
    } catch (e) { /* storage unavailable — session-only state */ }
  }
  function titleControlled() { return !ACPShell.env || ACPShell.env.isDomControlled(); }

  function el(tag, attrs) {
    var node = doc().createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (name) {
        if (name === "class") node.setAttribute("class", attrs[name]);
        else node.setAttribute(name, attrs[name]);
      });
    }
    return node;
  }

  function svgIcon(symbolId, extraClass, size) {
    var tag = "svg";
    var svg = typeof doc().createElementNS === "function"
      ? doc().createElementNS("http://www.w3.org/2000/svg", tag)
      : doc().createElement(tag);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("width", String(size || 16));
    svg.setAttribute("height", String(size || 16));
    svg.setAttribute("viewBox", "0 0 20 20");
    if (extraClass) svg.setAttribute("class", extraClass);
    var use = typeof doc().createElementNS === "function"
      ? doc().createElementNS("http://www.w3.org/2000/svg", "use")
      : doc().createElement("use");
    use.setAttribute("href", "#" + symbolId);
    svg.appendChild(use);
    return svg;
  }

  function clearChildren(node) {
    // Element.children is a read-only getter in real DOMs (Chromium throws on
    // assignment); the removeChild loop above is the actual clearing step.
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function t(key, vars) {
    return deps.i18n ? deps.i18n.t(key, vars) : key;
  }

  function isWorkspaceVisible(wsId) {
    var wsEl = workspaceEls[wsId];
    return !!(wsEl && !wsEl.hidden);
  }

  // ── A-63 focus utilities (stub-friendly: no closest, no complex selectors) ──

  function focusNode(node) {
    if (!node || typeof node.focus !== "function") return false;
    try { node.focus(); return true; } catch (e) { return false; }
  }

  function isTabbable(node) {
    if (!node || typeof node.tagName !== "string") return false;
    // Skip anything inside a closed drawer / hidden workspace ancestor.
    var cur = node;
    var guard = 0;
    while (cur && guard < 400) {
      if (cur.hidden) return false;
      cur = cur.parentNode;
      guard += 1;
    }
    var tag = node.tagName.toUpperCase();
    if (typeof node.getAttribute === "function" && node.getAttribute("disabled") != null) return false;
    var explicit = typeof node.getAttribute === "function" ? node.getAttribute("tabindex") : null;
    if (explicit != null) {
      var index = Number(explicit);
      return Number.isFinite(index) && index >= 0;
    }
    if (!FOCUSABLE_TAGS[tag]) return false;
    if (tag === "A" && (!node.getAttribute || node.getAttribute("href") == null)) return false;
    return true;
  }

  /** Depth-first walk collecting tabbable descendants (body/content stepping). */
  function collectTabbable(root) {
    var out = [];
    if (!root) return out;
    (function walk(node) {
      var children = node.children || [];
      for (var i = 0; i < children.length; i++) {
        var child = children[i];
        if (isTabbable(child)) out.push(child);
        walk(child);
      }
    })(root);
    return out;
  }

  function tabFor(wsId) {
    return tabEls[wsId] || null;
  }

  function focusWorkspaceTab(wsId) {
    return focusNode(tabFor(wsId || currentWorkspace));
  }

  /** Visible panels of a workspace in the A-63 walk order (main → side → bottom). */
  function panelRecordsForWorkspace(wsId) {
    var records = [];
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (!record || record.disposed) return;
      if (record.workspaceId !== wsId || record.layoutHidden) return;
      records.push(record);
    });
    records.sort(function (a, b) {
      var delta = (SLOT_RANK[a.slot] || 0) - (SLOT_RANK[b.slot] || 0);
      if (delta !== 0) return delta;
      return a.instanceId < b.instanceId ? -1 : a.instanceId > b.instanceId ? 1 : 0;
    });
    return records;
  }

  /**
   * A-63 focus-order contract. The walk is: the current-view menu items (menu
   * order) followed by, for each visible panel in slot order, the panel title
   * and then the panel content anchor. `step` is the global index used by the
   * `data-acp-focus-step` DOM metadata.
   */
  function focusOrder(wsId) {
    wsId = wsId || currentWorkspace;
    var steps = [];
    WORKSPACE_IDS.forEach(function (id, index) {
      steps.push({ kind: "view", step: index, workspaceId: id, id: "acp-tab-" + id });
    });
    var step = WORKSPACE_IDS.length;
    panelRecordsForWorkspace(wsId).forEach(function (record) {
      steps.push({ kind: "panel-title", step: step, instanceId: record.instanceId, slot: record.slot });
      step += 1;
      steps.push({ kind: "panel-content", step: step, instanceId: record.instanceId, slot: record.slot });
      step += 1;
    });
    return {
      workspaceId: wsId,
      steps: steps,
      order: steps.map(function (item) {
        return item.kind + ":" + (item.instanceId || item.workspaceId);
      })
    };
  }

  /**
   * Write the focus-order metadata onto the DOM so assistive tech / tests can
   * read the walk without calling the JS API. Steps are workspace-scoped (only
   * the active workspace is walkable; hidden workspaces keep their last values)
   * and stale steps of panels that left the recipe are cleared.
   */
  function syncFocusOrder(wsId) {
    wsId = wsId || currentWorkspace;
    WORKSPACE_IDS.forEach(function (id, index) {
      var tab = tabEls[id];
      if (!tab || typeof tab.setAttribute !== "function") return;
      tab.setAttribute("data-acp-focus-step", String(index));
      tab.setAttribute("data-acp-focus-kind", "view");
      tab.setAttribute("aria-keyshortcuts", "Alt+" + (index + 1));
    });
    var records = panelRecordsForWorkspace(wsId);
    var step = WORKSPACE_IDS.length;
    var sequence = [];
    records.forEach(function (record) {
      if (record.titleEl && typeof record.titleEl.setAttribute === "function") {
        record.titleEl.setAttribute("data-acp-focus-step", String(step));
        record.titleEl.setAttribute("data-acp-focus-kind", "panel-title");
      }
      step += 1;
      if (record.bodyEl && typeof record.bodyEl.setAttribute === "function") {
        record.bodyEl.setAttribute("data-acp-focus-step", String(step));
        record.bodyEl.setAttribute("data-acp-focus-kind", "panel-content");
      }
      step += 1;
      sequence.push(record.slot + ":" + record.instanceId);
    });
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (!record || record.disposed || record.workspaceId !== wsId) return;
      if (records.indexOf(record) >= 0) return;
      [record.titleEl, record.bodyEl].forEach(function (node) {
        if (node && typeof node.removeAttribute === "function") node.removeAttribute("data-acp-focus-step");
      });
    });
    var wsEl = workspaceEls[wsId];
    if (wsEl && typeof wsEl.setAttribute === "function") {
      wsEl.setAttribute("data-acp-focus-sequence", sequence.join(","));
    }
    return focusOrder(wsId);
  }

  /** Move focus into the panel content: first tabbable descendant, else the body. */
  function focusPanelContent(instanceId) {
    var record = panels[instanceId];
    if (!record || record.disposed) return false;
    var tabbables = collectTabbable(record.bodyEl);
    return focusNode(tabbables[0] || record.bodyEl);
  }

  function focusFirstPanelTitle(wsId) {
    var records = panelRecordsForWorkspace(wsId || currentWorkspace);
    for (var i = 0; i < records.length; i++) {
      if (focusNode(records[i].titleEl)) return true;
    }
    return focusNode(workspaceEls[wsId || currentWorkspace]);
  }

  function focusPreviousStep(record) {
    var records = panelRecordsForWorkspace(record.workspaceId);
    var index = records.indexOf(record);
    if (index > 0) return focusNode(records[index - 1].titleEl);
    return focusWorkspaceTab(record.workspaceId);
  }

  function syncSkipLink() {
    if (!skipLinkEl || typeof skipLinkEl.setAttribute !== "function") return;
    skipLinkEl.setAttribute("href", "#acp-ws-" + currentWorkspace);
    skipLinkEl.setAttribute("data-acp-skip-target", "acp-ws-" + currentWorkspace);
  }

  function ensureLiveRegion() {
    if (!shellRoot || liveEl) return;
    liveEl = el("div", {
      "class": "acp-visually-hidden",
      "id": "acp-shell-live",
      "role": "status",
      "aria-live": "polite",
      "aria-atomic": "true",
      "data-acp-live": "workspace"
    });
    shellRoot.appendChild(liveEl);
  }

  function announceWorkspace(wsId) {
    if (!liveEl) return;
    liveEl.textContent = t("shell.workspace." + wsId);
  }

  // ── collapse persistence ──

  function readCollapse() {
    try {
      var raw = storageGet(COLLAPSE_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function writeCollapse(state) {
    try {
      storageSet(COLLAPSE_KEY, JSON.stringify(state));
    } catch (e) { /* storage unavailable — session-only collapse */ }
  }

  function setCollapsed(wsId, slotName, collapsed) {
    var slot = slotEls[wsId] && slotEls[wsId][slotName];
    if (!slot) return;
    if (collapsed) slot.classList.add("is-collapsed");
    else slot.classList.remove("is-collapsed");
    // Rail is only visible while the slot is collapsed (2026-10-30 修订 §1.4.2).
    var rail = slot.querySelector ? slot.querySelector(".acp-slot-rail") : null;
    if (rail) {
      if (collapsed) rail.removeAttribute("hidden");
      else rail.setAttribute("hidden", "hidden");
    }
    var state = readCollapse();
    state[wsId] = state[wsId] || {};
    state[wsId][slotName] = !!collapsed;
    writeCollapse(state);
  }

  function applyStoredCollapse() {
    var state = readCollapse();
    WORKSPACE_IDS.forEach(function (wsId) {
      SLOT_IDS.forEach(function (slotName) {
        if (state[wsId] && state[wsId][slotName]) setCollapsed(wsId, slotName, true);
      });
    });
  }

  // ── panel chrome ──

  function buildPanelEl(record) {
    var section = el("section", {
      "class": "acp-panel" + (record.placeholder ? " is-placeholder" : ""),
      "id": "acp-panel-" + record.instanceId,
      "role": "region",
      "aria-labelledby": "acp-panel-" + record.instanceId + "-title",
      "data-instance-id": record.instanceId,
      "data-module-id": record.moduleId,
      "data-slot": record.slot,
      "data-acp-landmark": "panel",
      "data-acp-state": "loading",
      "data-acp-generation": "0"
    });
    var head = el("header", { "class": "acp-panel-head" });
    var group = el("div", { "class": "acp-panel-title-group" });
    var dot = el("span", {
      "class": "acp-panel-focus-dot",
      "data-follow": record.spec.binding === "follow-focus" ? "true" : "false",
      "data-i18n-title": "shell.panel.follow_focus",
      "title": t("shell.panel.follow_focus")
    });
    // A-63: the title is the panel's accessible name AND a focusable heading —
    // it is the "panel title" stop of the tabs → titles → content walk.
    var title = el("h2", {
      "class": "acp-panel-title",
      "id": "acp-panel-" + record.instanceId + "-title",
      "tabindex": "0",
      "data-acp-focus-kind": "panel-title",
      "data-i18n": record.spec.titleKey || ""
    });
    title.textContent = t(record.spec.titleKey || record.moduleId);
    var chip = el("span", {
      "class": "acp-panel-instance mono",
      "title": record.instanceId
    });
    chip.textContent = record.instanceId;
    group.appendChild(dot);
    group.appendChild(title);
    group.appendChild(chip);

    var right = el("div", { "class": "acp-panel-head-right" });
    var status = el("span", { "class": "acp-panel-status", "hidden": "hidden" });
    right.appendChild(status);
    if (!record.placeholder) {
      var reload = el("button", {
        "class": "acp-panel-btn",
        "data-action": "reload",
        "data-i18n-title": "shell.panel.reload",
        "title": t("shell.panel.reload"),
        "type": "button"
      });
      reload.appendChild(svgIcon("acp-i-reload", null, 14));
      reload.addEventListener("click", function () { reloadPanel(record.instanceId); });
      right.appendChild(reload);
    }
    {
      var collapse = el("button", {
        "class": "acp-panel-btn",
        "data-action": "collapse-slot",
        "data-i18n-title": "shell.panel.collapse",
        "title": t("shell.panel.collapse"),
        "type": "button"
      });
      collapse.appendChild(svgIcon("acp-i-chevron", null, 14));
      collapse.hidden = record.slot === "main";
      record.collapseEl = collapse;
      // Head chevron is a TOGGLE both ways (2026-10-30 修订 §1.4.2).
      collapse.addEventListener("click", function () {
        var slot = slotEls[record.workspaceId] && slotEls[record.workspaceId][record.slot];
        var nowCollapsed = !(slot && slot.classList && slot.classList.contains("is-collapsed"));
        setCollapsed(record.workspaceId, record.slot, nowCollapsed);
        var key = nowCollapsed ? "shell.panel.expand" : "shell.panel.collapse";
        collapse.setAttribute("data-i18n-title", key);
        collapse.setAttribute("title", t(key));
      });
      right.appendChild(collapse);
    }
    // Reserved close placeholder — permanently hidden until UI-06 (design §1.4.2).
    var close = el("button", {
      "class": "acp-panel-btn",
      "data-action": "close",
      "data-i18n-title": "shell.panel.close",
      "title": t("shell.panel.close"),
      "hidden": "hidden",
      "type": "button"
    });
    right.appendChild(close);

    head.appendChild(group);
    head.appendChild(right);

    var body = el("div", {
      "class": "acp-panel-body" + (record.spec.flush ? " is-flush" : ""),
      "tabindex": "-1",
      "data-acp-focus-kind": "panel-content"
    });

    section.appendChild(head);
    section.appendChild(body);

    bindPanelKeys(record, title, body);

    record.el = section;
    record.headEl = head;
    record.titleEl = title;
    record.dotEl = dot;
    record.statusEl = status;
    record.bodyEl = body;
    return section;
  }

  /**
   * A-63 Escape ladder + content stepping, scoped to the panel itself so the
   * shell never steals Escape from module controls inside the body:
   *   title: ↓/→/Enter/Space → panel content, ↑/← → previous panel, Esc → tab
   *   body:  Esc (only when the body itself is focused) → panel title
   */
  function bindPanelKeys(record, title, body) {
    function stop(event) {
      if (!event) return;
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (typeof event.stopPropagation === "function") event.stopPropagation();
    }
    if (title && title.addEventListener) {
      title.addEventListener("keydown", function (event) {
        var key = event && event.key;
        if (key === "Escape") {
          stop(event);
          focusWorkspaceTab(record.workspaceId);
          return;
        }
        if (key === "ArrowDown" || key === "ArrowRight" || key === "Enter" || key === " " || key === "Spacebar") {
          stop(event);
          focusPanelContent(record.instanceId);
          return;
        }
        if (key === "ArrowUp" || key === "ArrowLeft") {
          stop(event);
          focusPreviousStep(record);
        }
      });
    }
    if (body && body.addEventListener) {
      body.addEventListener("keydown", function (event) {
        if (!event || event.key !== "Escape") return;
        if (doc().activeElement !== body) return;
        stop(event);
        focusNode(record.titleEl);
      });
    }
  }

  function removeRail(slot) {
    if (!slot || !slot.querySelector) return;
    var rail = slot.querySelector(".acp-slot-rail");
    if (rail && rail.parentNode) rail.parentNode.removeChild(rail);
  }

  function buildRail(record) {
    // Collapsed side rail: vertical title + expand button (design §1.4.2).
    // The rail element lives in the slot at all times but is only VISIBLE
    // while the slot carries .is-collapsed (hidden attribute, 2026-10-30 修订).
    var slot = slotEls[record.workspaceId] && slotEls[record.workspaceId][record.slot];
    if (!slot) return;
    if (slot.querySelector && slot.querySelector(".acp-slot-rail")) return;
    var rail = el("button", {
      "class": "acp-slot-rail",
      "type": "button",
      "title": t("shell.panel.expand"),
      "data-i18n-title": "shell.panel.expand"
    });
    if (!(slot.classList && slot.classList.contains("is-collapsed"))) {
      rail.setAttribute("hidden", "hidden");
    }
    rail.textContent = t(record.spec.titleKey || record.moduleId);
    rail.addEventListener("click", function () {
      setCollapsed(record.workspaceId, record.slot, false);
    });
    slot.appendChild(rail);
  }

  // ── four-state rendering ──

  function skeletonFor(kind) {
    if (kind === "canvas") {
      var wrap = el("div", { "class": "acp-skel--canvas", "aria-hidden": "true" });
      wrap.appendChild(el("div", { "class": "acp-spinner" }));
      var label = el("span", { "class": "acp-visually-hidden" });
      label.textContent = t("shell.state.loading");
      wrap.appendChild(label);
      return wrap;
    }
    if (kind === "card") {
      var cards = el("div", { "class": "acp-skel--card", "aria-hidden": "true" });
      for (var c = 0; c < 4; c++) cards.appendChild(el("div", { "class": "acp-skel" }));
      var cards2 = el("div", { "class": "acp-skel--card", "aria-hidden": "true" });
      for (var c2 = 0; c2 < 4; c2++) cards2.appendChild(el("div", { "class": "acp-skel" }));
      var frag = el("div", {});
      frag.appendChild(cards);
      frag.appendChild(cards2);
      return frag;
    }
    var list = el("div", { "class": "acp-skel--list", "aria-hidden": "true" });
    for (var i = 0; i < 6; i++) list.appendChild(el("div", { "class": "acp-skel" }));
    return list;
  }

  function setPanelState(record, state, detail) {
    if (!record || record.disposed) return;
    record.state = state;
    record.el.setAttribute("data-acp-state", state);
    var body = record.bodyEl;
    // Shell owns the body whenever it is not "ready"; "ready" hands the body
    // to the module (we only clear shell-rendered blocks).
    if (state === "ready") {
      if (record.shellOwned) clearChildren(body);
      record.shellOwned = false;
      return;
    }
    record.shellOwned = true;
    clearChildren(body);
    body.setAttribute("aria-busy", state === "loading" ? "true" : "false");

    if (state === "loading") {
      body.setAttribute("role", "status");
      body.setAttribute("aria-live", "polite");
      body.appendChild(skeletonFor(record.spec.loading || "list"));
      return;
    }
    if (state === "empty") {
      body.setAttribute("role", "status");
      var empty = el("div", { "class": "acp-state acp-state-empty" });
      var et = el("p", { "class": "acp-state-title" });
      et.textContent = detail && detail.title ? detail.title : t("shell.focus.empty");
      empty.appendChild(et);
      if (detail && detail.hint) {
        var eh = el("p", { "class": "acp-state-msg" });
        eh.textContent = detail.hint;
        empty.appendChild(eh);
      }
      body.appendChild(empty);
      return;
    }
    if (state === "denied") {
      body.setAttribute("role", "status");
      var denied = el("div", { "class": "acp-state acp-state-denied" });
      denied.appendChild(svgIcon("acp-i-lock", "acp-state-icon", 20));
      var dcopy = el("div", { "class": "acp-state-copy" });
      var dt = el("p", { "class": "acp-state-title" });
      dt.textContent = t("shell.state.denied_title");
      dcopy.appendChild(dt);
      var dm = el("p", { "class": "acp-state-msg" });
      dm.textContent = t("shell.state.denied_body", { capability: (detail && detail.capability) || "—" });
      dcopy.appendChild(dm);
      var dh = el("p", { "class": "acp-state-msg" });
      dh.textContent = t("shell.state.denied_hint");
      dcopy.appendChild(dh);
      denied.appendChild(dcopy);
      body.appendChild(denied);
      return;
    }
    if (state === "error") {
      body.setAttribute("role", "alert");
      var block = el("div", { "class": "acp-state acp-state-error", "role": "alert" });
      block.appendChild(svgIcon("acp-i-error", "acp-state-icon", 20));
      var copy = el("div", { "class": "acp-state-copy" });
      var title = el("p", { "class": "acp-state-title" });
      title.textContent = t("shell.state.error_title");
      copy.appendChild(title);
      var msg = el("p", { "class": "acp-state-msg mono" });
      msg.textContent = (detail && detail.message) || (record.moduleId + ": unknown error");
      copy.appendChild(msg);
      block.appendChild(copy);
      var actions = el("div", { "class": "acp-state-actions" });
      var retry = el("button", {
        "class": "acp-btn acp-btn--primary",
        "data-action": "retry",
        "data-i18n": "shell.state.error_retry",
        "type": "button"
      });
      retry.textContent = t("shell.state.error_retry");
      retry.addEventListener("click", function () { reloadPanel(record.instanceId); });
      var copyBtn = el("button", {
        "class": "acp-btn acp-btn--ghost",
        "data-action": "copy-error",
        "data-i18n": "shell.state.error_copy",
        "type": "button"
      });
      copyBtn.textContent = t("shell.state.error_copy");
      copyBtn.addEventListener("click", function () { copyErrorDetail(record); });
      actions.appendChild(retry);
      actions.appendChild(copyBtn);
      block.appendChild(actions);
      body.appendChild(block);
      record.lastError = {
        message: (detail && detail.message) || "",
        stack: (detail && detail.stack) || "",
        instanceId: record.instanceId,
        generation: record.generation,
        moduleId: record.moduleId
      };
      return;
    }
  }

  function copyErrorDetail(record) {
    var info = record.lastError || {};
    var text = [
      "module: " + (info.moduleId || record.moduleId),
      "instance: " + (info.instanceId || record.instanceId),
      "generation: " + (info.generation != null ? info.generation : record.generation),
      "error: " + (info.message || ""),
      info.stack || ""
    ].join("\n");
    try {
      if (navigator && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text);
        return;
      }
    } catch (e) { /* fall through to selection fallback */ }
    try {
      var msgEl = record.bodyEl.querySelector && record.bodyEl.querySelector(".acp-state-msg");
      if (msgEl && typeof window.getSelection === "function" && doc().createRange) {
        var range = doc().createRange();
        range.selectNodeContents(msgEl);
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      }
    } catch (e2) { /* clipboard unavailable — nothing else to do */ }
  }

  // ── module lifecycle ──

  function hostApi(record) {
    return {
      instanceId: record.instanceId,
      moduleId: record.moduleId,
      slot: record.slot,
      workspaceId: record.workspaceId,
      context: deps.context,
      i18n: deps.i18n,
      request: deps.request,
      commands: window.ACPShell.commands || null,
      panel: {
        setState: function (state, detail) { setPanelState(record, state, detail); },
        reportError: function (err) {
          setPanelState(record, "error", {
            message: record.moduleId + ": " + ((err && err.message) || String(err)),
            stack: (err && err.stack) || ""
          });
        },
        reload: function () { reloadPanel(record.instanceId); },
        el: record.el,
        bodyEl: record.bodyEl,
        statusEl: record.statusEl
      }
    };
  }

  function disposeInstance(instance) {
    if (instance && typeof instance.dispose === "function") {
      try { instance.dispose(); } catch (error) {
        if (typeof console !== "undefined" && console.error) console.error("Panel instance dispose failed", error);
      }
    }
  }

  function suspendPanel(record) {
    if (!record || record.suspended || !record.instance) return;
    record.suspended = true;
    if (typeof record.instance.suspend === "function") {
      try { record.instance.suspend(); } catch (error) {
        if (typeof console !== "undefined" && console.error) console.error("Panel suspend failed", error);
      }
    }
  }

  function resumePanel(record) {
    if (!record || !record.suspended) return;
    record.suspended = false;
    if (record.instance && typeof record.instance.resume === "function") {
      try { record.instance.resume(); } catch (error) {
        if (typeof console !== "undefined" && console.error) console.error("Panel resume failed", error);
      }
    }
  }

  function loadPanelModule(record) {
    record.generation += 1;
    record.el.setAttribute("data-acp-generation", String(record.generation));
    var generation = record.generation;
    setPanelState(record, "loading");
    deps.loader.instantiate(record.moduleId, record.instanceId, hostApi(record)).then(function (res) {
      if (record.disposed || record.generation !== generation) {
        // Late / superseded result: dispose and never remount.
        disposeInstance(res.instance);
        return;
      }
      record.instance = res.instance;
      record.descriptor = res.descriptor;
      if (record.shellOwned) clearChildren(record.bodyEl);
      record.shellOwned = false;
      var mounting = record.instance && typeof record.instance.mount === "function"
        ? record.instance.mount(record.bodyEl, hostApi(record))
        : null;
      Promise.resolve(mounting).then(function () {
        if (record.disposed || record.generation !== generation) {
          disposeInstance(record.instance);
          record.instance = null;
          return;
        }
        setPanelState(record, "ready");
        if (typeof record.instance.updateContext === "function") {
          try { record.instance.updateContext(deps.context.getSnapshot()); } catch (error) {
            if (typeof console !== "undefined" && console.error) console.error("updateContext failed", error);
          }
        }
        if (isWorkspaceVisible(record.workspaceId) && !record.layoutHidden) resumePanel(record);
        else suspendPanel(record);
      }).catch(function (err) {
        if (record.disposed || record.generation !== generation) return;
        showPanelError(record, err);
      });
    }).catch(function (err) {
      if (record.disposed || record.generation !== generation) return;
      showPanelError(record, err);
    });
  }

  function showPanelError(record, err) {
    var info = (err && err.acpShell) || null;
    var message = info && info.message
      ? info.code + ": " + info.message
      : record.moduleId + ": " + ((err && err.message) || String(err));
    setPanelState(record, "error", { message: message, stack: (err && err.stack) || "" });
  }

  /** Retry / reload: invalidate loader cache, dispose old instance, re-mount
   *  (loadPanelModule bumps the generation that gates late results). */
  function reloadPanel(instanceId) {
    var record = panels[instanceId];
    if (!record || record.disposed || record.placeholder) return;
    deps.loader.invalidate(record.moduleId);
    if (record.instance) {
      disposeInstance(record.instance);
      record.instance = null;
    }
    loadPanelModule(record);
  }

  function disposePanel(record) {
    if (!record || record.disposed) return;
    record.disposed = true;
    disposeInstance(record.instance);
    record.instance = null;
    if (record.el && record.el.parentNode) {
      removeRail(record.el.parentNode);
      record.el.parentNode.removeChild(record.el);
    }
    delete panels[record.instanceId];
  }

  function disposeAll() {
    Object.keys(panels).forEach(function (id) { disposePanel(panels[id]); });
  }

  // ── recipes ──

  function ensurePanel(recipe, spec) {
    var existing = panels[spec.instanceId];
    if (existing && existing.moduleId === spec.moduleId) {
      existing.spec = spec;
      existing.layoutHidden = false;
      existing.el.hidden = false;
      if (existing.collapseEl) existing.collapseEl.hidden = spec.slot === "main";
      var targetSlot = slotEls[recipe.workspaceId] && slotEls[recipe.workspaceId][spec.slot];
      if (targetSlot && existing.el.parentNode !== targetSlot) {
        // Single-active-instance migration: MOVE the live element (no remount).
        var oldSlot = existing.el.parentNode;
        suspendPanel(existing);
        targetSlot.appendChild(existing.el);
        removeRail(oldSlot);
        existing.slot = spec.slot;
        existing.workspaceId = recipe.workspaceId;
        existing.recipeId = recipe.id;
        existing.el.setAttribute("data-slot", spec.slot);
        if (spec.slot === "side" || spec.slot === "bottom") buildRail(existing);
      }
      return existing;
    }
    if (existing) disposePanel(existing);

    var registryHas = deps.registry && typeof deps.registry.has === "function" && deps.registry.has(spec.moduleId);
    var placeholder = !!spec.pendingPhase || !registryHas;
    var record = {
      instanceId: spec.instanceId,
      moduleId: spec.moduleId,
      slot: spec.slot,
      workspaceId: recipe.workspaceId,
      recipeId: recipe.id,
      spec: spec,
      placeholder: placeholder,
      instance: null,
      descriptor: null,
      generation: 0,
      state: "loading",
      suspended: false,
      disposed: false,
      shellOwned: true,
      lastError: null
    };
    panels[spec.instanceId] = record;
    var slot = slotEls[recipe.workspaceId] && slotEls[recipe.workspaceId][spec.slot];
    if (!slot) return record;
    buildPanelEl(record);
    slot.appendChild(record.el);
    if (spec.slot === "side" || spec.slot === "bottom") buildRail(record);

    if (placeholder) {
      // Placeholder body: neutral chip + fact sentence (design §3.1,
      // 2026-10-30 修订). pending_module = explicit pendingPhase (e.g.
      // exec-detail/mol-config); unknown_module = moduleId absent from the
      // registry entirely. Both dictionary keys stay live, one per case.
      var pending = !!spec.pendingPhase;
      var phase = spec.pendingPhase || "—";
      setPanelState(record, "empty", {
        title: pending ? t("shell.slot.pending_hint", { phase: phase }) : spec.moduleId
      });
      var chip = el("span", { "class": "acp-tag acp-tag--neutral" });
      chip.textContent = t(pending ? "shell.slot.pending_module" : "shell.slot.unknown_module");
      var emptyBlock = record.bodyEl.querySelector(".acp-state-empty");
      if (emptyBlock) emptyBlock.appendChild(chip);
      record.el.setAttribute("data-acp-state", "empty");
      return record;
    }
    loadPanelModule(record);
    return record;
  }

  /**
   * applyRecipe(recipeId): render/migrate panels of the recipe then show its
   * workspace. Idempotent — re-applying never duplicates instances.
   * opts.focus = "tab" | "none" is forwarded to showWorkspace (A-63).
   */
  function applyRecipe(recipeId, opts) {
    var recipe = window.ACPShell.recipes.get(recipeId);
    if (!recipe) return { ok: false, error: "unknown recipe: " + recipeId };
    var wanted = recipe.panels.map(function (p) { return p.instanceId; });
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (record.workspaceId === recipe.workspaceId && wanted.indexOf(id) < 0) {
        record.layoutHidden = true;
        record.el.hidden = true;
        suspendPanel(record);
      }
    });
    var workspace = workspaceEls[recipe.workspaceId];
    if (workspace) {
      ["side", "bottom"].forEach(function (slot) {
        var present = recipe.panels.some(function (p) { return p.slot === slot; });
        workspace.setAttribute("data-has-" + slot, String(present));
        var target = slotEls[recipe.workspaceId][slot];
        if (!target && present) {
          target = el("div", { "class": "acp-slot", "data-slot": slot, "id": "acp-slot-" + recipe.workspaceId + "-" + slot });
          workspace.appendChild(target);
          slotEls[recipe.workspaceId][slot] = target;
        }
        if (target) { target.hidden = !present; removeRail(target); }
      });
      if (recipe.dimensions && workspace.style) {
        workspace.style.setProperty("--shell-side-w", recipe.dimensions.sideWidth + "%");
        workspace.style.setProperty("--shell-bottom-h", recipe.dimensions.bottomHeight + "%");
      }
    }
    recipe.panels.forEach(function (spec) { ensurePanel(recipe, spec); });
    recipe.panels.forEach(function (spec) { if (spec.slot !== "main") buildRail(panels[spec.instanceId]); });
    recipeByWorkspace[recipe.workspaceId] = recipe.id;
    syncFocusOrder(recipe.workspaceId);
    showWorkspace(recipe.workspaceId, opts);
    return { ok: true, recipeId: recipe.id, workspaceId: recipe.workspaceId };
  }

  // ── workspace switching ──

  /**
   * showWorkspace(wsId, opts).
   * opts.focus: "tab" keeps focus on the activated tab (mouse/Enter/Space tab
   * activation must not be stolen by focus restoration), "none" leaves focus
   * alone (command navigation focuses its own target), otherwise the remembered
   * element / main panel title is restored (design §5.1 focus restore).
   */
  function showWorkspace(wsId, opts) {
    opts = opts || {};
    if (WORKSPACE_IDS.indexOf(wsId) < 0) return;
    var prev = currentWorkspace;
    if (prev !== wsId) {
      var prevEl = workspaceEls[prev];
      var active = doc().activeElement;
      if (prevEl && active && isDescendant(active, prevEl)) focusMemory.set(prev, active);
    }
    WORKSPACE_IDS.forEach(function (id) {
      var wsEl = workspaceEls[id];
      if (!wsEl) return;
      if (id === wsId) {
        wsEl.hidden = false;
        wsEl.classList.add("is-entering");
        var remove = function () { try { wsEl.classList.remove("is-entering"); } catch (e) {} };
        if (typeof setTimeout === "function") setTimeout(remove, 200);
        else remove();
      } else {
        wsEl.hidden = true;
        wsEl.classList.remove("is-entering");
      }
    });
    // Suspend hidden-workspace panels, resume visible ones.
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (record.workspaceId === wsId && !record.layoutHidden) resumePanel(record);
      else suspendPanel(record);
    });
    WORKSPACE_IDS.forEach(function (id) {
      var tab = tabEls[id];
      if (!tab) return;
      var activeTab = id === wsId;
      if (activeTab) tab.classList.add("is-active");
      else tab.classList.remove("is-active");
      tab.setAttribute("aria-checked", activeTab ? "true" : "false");
      if (activeTab) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
      tab.setAttribute("tabindex", activeTab ? "0" : "-1");
    });
    currentWorkspace = wsId;
    syncSkipLink();
    syncFocusOrder(wsId);
    announceWorkspace(wsId);
    if (window.ACPShell.preferences) ACPShell.preferences.setActive(wsId);
    if (window.ACPShell.topbar && ACPShell.topbar.syncView) ACPShell.topbar.syncView(wsId);
    if (window.ACPShell.globalRegions && ACPShell.globalRegions.setFilesView) ACPShell.globalRegions.setFilesView(wsId === "files");
    if (shellRoot) shellRoot.setAttribute("data-workspace", wsId);
    if (titleControlled()) document.title = t("shell.app.title") + " · " + t("shell.workspace." + wsId);
    if (opts.focus === "tab") focusWorkspaceTab(wsId);
    else if (opts.focus !== "none") restoreFocus(wsId);
  }

  function isDescendant(node, ancestor) {
    var cur = node;
    var guard = 0;
    while (cur && guard < 200) {
      if (cur === ancestor) return true;
      cur = cur.parentNode;
      guard += 1;
    }
    return false;
  }

  function restoreFocus(wsId) {
    var remembered = focusMemory.get(wsId);
    if (remembered && remembered.isConnected !== false && typeof remembered.focus === "function") {
      try { remembered.focus(); return; } catch (e) { /* fall through */ }
    }
    // Fallback: focus the main panel title of this workspace.
    var recipeId = recipeByWorkspace[wsId];
    var recipe = recipeId ? window.ACPShell.recipes.get(recipeId) : null;
    if (!recipe) return;
    for (var i = 0; i < recipe.panels.length; i++) {
      if (recipe.panels[i].slot !== "main") continue;
      var record = panels[recipe.panels[i].instanceId];
      if (record && record.titleEl && typeof record.titleEl.focus === "function") {
        try { record.titleEl.focus(); } catch (e) { /* ignore */ }
        return;
      }
    }
  }

  /** Public workspace switch (tabs / Alt+N / commands). Applies the last recipe mapped for the workspace. */
  function switchWorkspace(wsId, opts) {
    opts = opts || {};
    var recipeId = opts.recipeId || recipeByWorkspace[wsId] || wsId;
    // A-63: tab activation keeps focus on the tab; command navigation lands on
    // its explicit focusTarget; shortcut/back-navigation restores focus.
    var focusPolicy = opts.focusTarget ? "none" : (opts.source === "tab" ? "tab" : null);
    var recipeOpts = focusPolicy ? { focus: focusPolicy } : undefined;
    if (window.ACPShell.recipes.get(recipeId)) applyRecipe(recipeId, recipeOpts);
    else showWorkspace(wsId, recipeOpts);
    if (opts.focusTarget) focusPanel(opts.focusTarget);
  }

  function focusPanel(instanceId) {
    var record = panels[instanceId];
    if (record && record.titleEl) focusNode(record.titleEl);
  }

  /** Panel lookup for commands: prefer current workspace, match binds OR provides. */
  function findPanelFor(bindsField, providesTag) {
    var ids = Object.keys(panels);
    var fallback = null;
    for (var i = 0; i < ids.length; i++) {
      var record = panels[ids[i]];
      if (!record || record.disposed || record.layoutHidden) continue;
      var desc = record.descriptor;
      if (!desc) continue;
      var binds = desc.binds || [];
      var provides = desc.provides || [];
      var match = (bindsField && binds.indexOf(bindsField) >= 0) ||
                  (providesTag && provides.indexOf(providesTag) >= 0);
      if (!match) continue;
      if (record.workspaceId === currentWorkspace) return publicPanel(record);
      if (!fallback) fallback = publicPanel(record);
    }
    return fallback;
  }

  function publicPanel(record) {
    return {
      instanceId: record.instanceId,
      moduleId: record.moduleId,
      workspaceId: record.workspaceId,
      slot: record.slot,
      instance: record.instance,
      state: record.state
    };
  }

  function getPanel(instanceId) {
    return panels[instanceId] ? publicPanel(panels[instanceId]) : null;
  }

  function listPanels() {
    return Object.keys(panels).map(function (id) { return publicPanel(panels[id]); });
  }

  // ── focus chip + follow dots ──

  function refName(ref) {
    if (ref == null) return "";
    if (typeof ref === "string") return ref;
    return ref.name || ref.structureId || ref.jobId || ref.proposalId || ref.id || "";
  }

  function renderFocusChip(snapshot) {
    if (!focusChipEl) return;
    clearChildren(focusChipEl);
    var items = [];
    if (snapshot.structureRef) {
      items.push({ type: "structure", icon: "acp-i-structure", name: refName(snapshot.structureRef) });
    }
    if (snapshot.taskRef) {
      items.push({
        type: "task",
        icon: "acp-i-task",
        name: refName(snapshot.taskRef),
        meta: snapshot.taskRef.attempt != null
          ? t("shell.focus.attempt", { n: snapshot.taskRef.attempt })
          : null
      });
    }
    if (!items.length) {
      focusChipEl.setAttribute("data-empty", "true");
      var empty = el("span", { "class": "acp-shell-focus-empty" });
      empty.textContent = t("shell.focus.empty");
      focusChipEl.appendChild(empty);
      return;
    }
    focusChipEl.setAttribute("data-empty", "false");
    items.forEach(function (item, index) {
      if (index > 0) {
        var sep = el("span", { "class": "acp-shell-focus-sep", "aria-hidden": "true" });
        sep.textContent = "›";
        focusChipEl.appendChild(sep);
      }
      var span = el("span", { "class": "acp-shell-focus-item", "data-ref-type": item.type });
      span.appendChild(svgIcon(item.icon, null, 12));
      var name = el("span", { "class": "acp-shell-focus-name mono", "title": item.name });
      name.textContent = item.name;
      span.appendChild(name);
      if (item.meta) {
        var meta = el("span", { "class": "acp-shell-focus-meta" });
        meta.textContent = item.meta;
        span.appendChild(meta);
      }
      focusChipEl.appendChild(span);
    });
  }

  function pulseFocusIndicators() {
    if (focusChipEl && focusChipEl.classList) {
      focusChipEl.classList.add("is-pulse");
      if (typeof setTimeout === "function") {
        setTimeout(function () {
          try { focusChipEl.classList.remove("is-pulse"); } catch (e) { /* ignore */ }
        }, 620);
      }
    }
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (!record.dotEl || record.spec.binding !== "follow-focus") return;
      record.dotEl.classList.add("is-pulse");
      if (typeof setTimeout === "function") {
        setTimeout(function () {
          try { record.dotEl.classList.remove("is-pulse"); } catch (e) { /* ignore */ }
        }, 620);
      }
    });
  }

  function onContextChange(snapshot) {
    renderFocusChip(snapshot);
    pulseFocusIndicators();
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (!record.instance || typeof record.instance.updateContext !== "function") return;
      try { record.instance.updateContext(snapshot); } catch (error) {
        if (typeof console !== "undefined" && console.error) console.error("updateContext failed", error);
      }
    });
  }

  // ── keyboard ──

  function onKeydown(event) {
    if (!event) return;
    if (event.altKey && !event.ctrlKey && !event.metaKey) {
      var idx = { "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5 }[String(event.key)];
      if (idx != null && WORKSPACE_IDS[idx]) {
        if (typeof event.preventDefault === "function") event.preventDefault();
        switchWorkspace(WORKSPACE_IDS[idx]);
        return;
      }
    }
  }

  /** A-63: Enter/Space activate a tab even when the host is not a <button>. */
  function isActivationKey(key) {
    return key === "Enter" || key === " " || key === "Spacebar";
  }

  function bindTabKeys() {
    WORKSPACE_IDS.forEach(function (wsId, index) {
      var tab = tabEls[wsId];
      if (!tab || !tab.addEventListener) return;
      tab.addEventListener("keydown", function (event) {
        var key = event && event.key;
        var stop = function () {
          if (typeof event.preventDefault === "function") event.preventDefault();
          if (typeof event.stopPropagation === "function") event.stopPropagation();
        };
        if (isActivationKey(key)) {
          stop();
          switchWorkspace(wsId, { source: "tab" });
          return;
        }
        var next = null;
        if (key === "ArrowRight" || key === "ArrowDown") next = (index + 1) % WORKSPACE_IDS.length;
        else if (key === "ArrowLeft" || key === "ArrowUp") next = (index - 1 + WORKSPACE_IDS.length) % WORKSPACE_IDS.length;
        else if (key === "Home") next = 0;
        else if (key === "End") next = WORKSPACE_IDS.length - 1;
        if (next == null) return;
        stop();
        var targetWs = WORKSPACE_IDS[next];
        switchWorkspace(targetWs, { source: "tab" });
        focusWorkspaceTab(targetWs);
      });
    });
  }

  function bindTabs() {
    WORKSPACE_IDS.forEach(function (wsId) {
      var tab = tabEls[wsId];
      if (!tab || !tab.addEventListener) return;
      tab.addEventListener("click", function () { switchWorkspace(wsId, { source: "tab" }); });
    });
  }

  /** A-63: Escape/ArrowDown on the workspace landmark (skip-link landing). Only
   *  the landmark itself is handled — module controls keep their own keys. */
  function bindWorkspaceKeys() {
    WORKSPACE_IDS.forEach(function (wsId) {
      var section = workspaceEls[wsId];
      if (!section || !section.addEventListener) return;
      section.addEventListener("keydown", function (event) {
        if (!event || event.defaultPrevented) return;
        var target = event.target || null;
        if (target && target !== section) return;
        if (event.key === "ArrowDown") {
          if (typeof event.preventDefault === "function") event.preventDefault();
          if (typeof event.stopPropagation === "function") event.stopPropagation();
          focusFirstPanelTitle(wsId);
          return;
        }
        if (event.key === "Escape") {
          if (typeof event.preventDefault === "function") event.preventDefault();
          focusWorkspaceTab(wsId);
        }
      });
    });
  }

  /** A-63: the skip link always lands on the ACTIVE workspace landmark. */
  function bindSkipLink() {
    skipLinkEl = doc().getElementById("acp-skip-link");
    if (!skipLinkEl || !skipLinkEl.addEventListener) return;
    skipLinkEl.addEventListener("click", function (event) {
      var target = workspaceEls[currentWorkspace];
      if (!target) return;
      if (event && typeof event.preventDefault === "function") event.preventDefault();
      if (typeof target.getAttribute === "function" && target.getAttribute("tabindex") == null) {
        target.setAttribute("tabindex", "0");
      }
      focusNode(target);
    });
  }

  /** A-63: the tablist/tabpanel wiring is asserted from the DOM, not left implicit. */
  function bindAria() {
    WORKSPACE_IDS.forEach(function (wsId, index) {
      var tab = tabEls[wsId];
      var section = workspaceEls[wsId];
      if (tab && typeof tab.setAttribute === "function") {
        tab.setAttribute("role", "menuitemradio");
        tab.setAttribute("aria-controls", "acp-ws-" + wsId);
        tab.setAttribute("aria-keyshortcuts", "Alt+" + (index + 1));
        tab.setAttribute("aria-checked", tab.getAttribute("aria-checked") == null ? "false" : tab.getAttribute("aria-checked"));
        tab.setAttribute("data-acp-focus-kind", "view");
      }
      if (section && typeof section.setAttribute === "function") {
        section.setAttribute("role", "region");
        section.setAttribute("aria-labelledby", "acp-tab-" + wsId);
        section.setAttribute("data-acp-landmark", "workspace");
      }
    });
    var tablist = doc().querySelector ? doc().querySelector(".acp-shell-switch") : null;
    if (tablist && typeof tablist.getAttribute === "function" && tablist.getAttribute("role") == null) {
      tablist.setAttribute("role", "menu");
    }
  }

  function onLangChange() {
    WORKSPACE_IDS.forEach(function (wsId) {
      var tab = tabEls[wsId];
      if (tab) {
        var label = tab.querySelector ? tab.querySelector("span[data-i18n], strong[data-i18n]") : null;
        if (label) label.textContent = t("shell.workspace." + wsId);
      }
    });
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (record.titleEl) record.titleEl.textContent = t(record.spec.titleKey || record.moduleId);
      if (record.state !== "ready") setPanelState(record, record.state, record.lastError || undefined);
    });
    renderFocusChip(deps.context.getSnapshot());
    if (titleControlled()) document.title = t("shell.app.title") + " · " + t("shell.workspace." + currentWorkspace);
    announceWorkspace(currentWorkspace);
  }

  // ── init ──

  function init(options) {
    options = options || {};
    deps.context = options.context || null;
    deps.loader = options.loader || null;
    deps.registry = options.registry || null;
    deps.i18n = options.i18n || null;
    deps.request = typeof options.request === "function" ? options.request : null;

    shellRoot = doc().getElementById("acp-shell");
    mainEl = doc().getElementById("acp-shell-main");
    fatalEl = doc().getElementById("acp-shell-fatal");
    focusChipEl = doc().getElementById("acp-focus-chip");

    WORKSPACE_IDS.forEach(function (wsId) {
      workspaceEls[wsId] = doc().getElementById("acp-ws-" + wsId);
      tabEls[wsId] = doc().getElementById("acp-tab-" + wsId);
      var slots = Object.create(null);
      SLOT_IDS.forEach(function (slotName) {
        slots[slotName] = doc().getElementById("acp-slot-" + wsId + "-" + slotName);
      });
      slotEls[wsId] = slots;
    });

    bindAria();
    bindTabs();
    bindTabKeys();
    bindWorkspaceKeys();
    bindSkipLink();
    ensureLiveRegion();
    syncSkipLink();
    syncFocusOrder(currentWorkspace);
    if (doc().addEventListener) doc().addEventListener("keydown", onKeydown);
    applyStoredCollapse();

    if (deps.context && typeof deps.context.subscribe === "function") {
      // Re-init (fatal retry) must not double-subscribe.
      if (typeof contextUnsub === "function") {
        try { contextUnsub(); } catch (e) { /* previous context is gone */ }
      }
      renderFocusChip(deps.context.getSnapshot());
      contextUnsub = deps.context.subscribe(onContextChange);
    }
    if (!started && doc().addEventListener) {
      doc().addEventListener("acp:langchange", onLangChange);
      started = true;
    }
    return true;
  }

  function reloadCurrentWorkspace() {
    var recipe = window.ACPShell.recipes ? ACPShell.recipes.get(currentWorkspace) : null;
    if (!recipe) return false;
    recipe.panels.forEach(function (spec) { if (panels[spec.instanceId]) reloadPanel(spec.instanceId); });
    return true;
  }

  return {
    init: init,
    applyRecipe: applyRecipe,
    switchWorkspace: switchWorkspace,
    showWorkspace: showWorkspace,
    reloadPanel: reloadPanel,
    reloadCurrentWorkspace: reloadCurrentWorkspace,
    disposePanel: function (instanceId) {
      var record = panels[instanceId];
      disposePanel(record);
    },
    disposeAll: disposeAll,
    findPanelFor: findPanelFor,
    getPanel: getPanel,
    listPanels: listPanels,
    focusPanel: focusPanel,
    // A-63 keyboard/ARIA surface (also mirrored as data-acp-focus-* metadata).
    focusOrder: focusOrder,
    focusPanelContent: focusPanelContent,
    focusWorkspaceTab: focusWorkspaceTab,
    syncFocusOrder: function () { return syncFocusOrder(currentWorkspace); },
    setPanelState: function (instanceId, state, detail) {
      var record = panels[instanceId];
      if (record) setPanelState(record, state, detail);
    },
    setCollapsed: setCollapsed,
    getCurrentWorkspace: function () { return currentWorkspace; }
  };
})();
