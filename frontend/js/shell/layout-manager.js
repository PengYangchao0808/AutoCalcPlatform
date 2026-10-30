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

  var WORKSPACE_IDS = ["research", "molecular", "execution"];
  var SLOT_IDS = ["main", "side", "bottom"];
  var COLLAPSE_KEY = "acp-next-collapse";

  var deps = { context: null, loader: null, registry: null, i18n: null };
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
  var contextUnsub = null;
  var started = false;

  // ── small DOM helpers (stub-friendly: no innerHTML, no closest) ──

  function doc() { return window.document; }

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

  // ── collapse persistence ──

  function readCollapse() {
    try {
      var raw = window.localStorage && window.localStorage.getItem(COLLAPSE_KEY);
      var parsed = raw ? JSON.parse(raw) : null;
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      return {};
    }
  }

  function writeCollapse(state) {
    try {
      window.localStorage && window.localStorage.setItem(COLLAPSE_KEY, JSON.stringify(state));
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
    var title = el("h2", {
      "class": "acp-panel-title",
      "id": "acp-panel-" + record.instanceId + "-title",
      "tabindex": "-1",
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

    var body = el("div", { "class": "acp-panel-body" + (record.spec.flush ? " is-flush" : "") });

    section.appendChild(head);
    section.appendChild(body);

    record.el = section;
    record.headEl = head;
    record.titleEl = title;
    record.dotEl = dot;
    record.statusEl = status;
    record.bodyEl = body;
    return section;
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
   */
  function applyRecipe(recipeId) {
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
    showWorkspace(recipe.workspaceId);
    return { ok: true, recipeId: recipe.id, workspaceId: recipe.workspaceId };
  }

  // ── workspace switching ──

  function showWorkspace(wsId) {
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
      tab.setAttribute("aria-selected", activeTab ? "true" : "false");
      tab.setAttribute("tabindex", activeTab ? "0" : "-1");
    });
    currentWorkspace = wsId;
    if (window.ACPShell.preferences) ACPShell.preferences.setActive(wsId);
    if (shellRoot) shellRoot.setAttribute("data-workspace", wsId);
    document.title = t("shell.app.title") + " · " + t("shell.workspace." + wsId);
    restoreFocus(wsId);
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
    if (window.ACPShell.recipes.get(recipeId)) applyRecipe(recipeId);
    else showWorkspace(wsId);
    if (opts.focusTarget) focusPanel(opts.focusTarget);
  }

  function focusPanel(instanceId) {
    var record = panels[instanceId];
    if (record && record.titleEl && typeof record.titleEl.focus === "function") {
      try { record.titleEl.focus(); } catch (e) { /* ignore */ }
    }
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
      var idx = { "1": 0, "2": 1, "3": 2 }[String(event.key)];
      if (idx != null && WORKSPACE_IDS[idx]) {
        if (typeof event.preventDefault === "function") event.preventDefault();
        switchWorkspace(WORKSPACE_IDS[idx]);
        return;
      }
    }
  }

  function bindTabKeys() {
    WORKSPACE_IDS.forEach(function (wsId, index) {
      var tab = tabEls[wsId];
      if (!tab || !tab.addEventListener) return;
      tab.addEventListener("keydown", function (event) {
        var key = event && event.key;
        var next = null;
        if (key === "ArrowRight" || key === "ArrowDown") next = (index + 1) % WORKSPACE_IDS.length;
        else if (key === "ArrowLeft" || key === "ArrowUp") next = (index - 1 + WORKSPACE_IDS.length) % WORKSPACE_IDS.length;
        else if (key === "Home") next = 0;
        else if (key === "End") next = WORKSPACE_IDS.length - 1;
        if (next == null) return;
        if (typeof event.preventDefault === "function") event.preventDefault();
        var targetWs = WORKSPACE_IDS[next];
        switchWorkspace(targetWs);
        var targetTab = tabEls[targetWs];
        if (targetTab && typeof targetTab.focus === "function") {
          try { targetTab.focus(); } catch (e) { /* ignore */ }
        }
      });
    });
  }

  function bindTabs() {
    WORKSPACE_IDS.forEach(function (wsId) {
      var tab = tabEls[wsId];
      if (!tab || !tab.addEventListener) return;
      tab.addEventListener("click", function () { switchWorkspace(wsId); });
    });
  }

  function onLangChange() {
    WORKSPACE_IDS.forEach(function (wsId) {
      var tab = tabEls[wsId];
      if (tab) {
        var label = tab.querySelector ? tab.querySelector("span[data-i18n]") : null;
        if (label) label.textContent = t("shell.workspace." + wsId);
      }
    });
    Object.keys(panels).forEach(function (id) {
      var record = panels[id];
      if (record.titleEl) record.titleEl.textContent = t(record.spec.titleKey || record.moduleId);
      if (record.state !== "ready") setPanelState(record, record.state, record.lastError || undefined);
    });
    renderFocusChip(deps.context.getSnapshot());
    document.title = t("shell.app.title") + " · " + t("shell.workspace." + currentWorkspace);
  }

  // ── init ──

  function init(options) {
    options = options || {};
    deps.context = options.context || null;
    deps.loader = options.loader || null;
    deps.registry = options.registry || null;
    deps.i18n = options.i18n || null;

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

    bindTabs();
    bindTabKeys();
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

  return {
    init: init,
    applyRecipe: applyRecipe,
    switchWorkspace: switchWorkspace,
    showWorkspace: showWorkspace,
    reloadPanel: reloadPanel,
    disposePanel: function (instanceId) {
      var record = panels[instanceId];
      disposePanel(record);
    },
    disposeAll: disposeAll,
    findPanelFor: findPanelFor,
    getPanel: getPanel,
    listPanels: listPanels,
    focusPanel: focusPanel,
    setPanelState: function (instanceId, state, detail) {
      var record = panels[instanceId];
      if (record) setPanelState(record, state, detail);
    },
    setCollapsed: setCollapsed,
    getCurrentWorkspace: function () { return currentWorkspace; }
  };
})();
