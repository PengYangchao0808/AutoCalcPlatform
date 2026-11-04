/* ACP native DSH docking tab with a window-level workbench owner.
 *
 * DSH still owns the outer geometry: the ACP workbench is one native keyed tab
 * body, the splitter and fullscreen stay native. What changed here is that the
 * ACP workbench is a single window-level instance with a stable DOM root that
 * is only ever *moved* between native tab bodies. Switching conversations
 * therefore keeps the project, structure, tasks, drafts, camera and scroll
 * state because the runtime is created once and never rebuilt.
 *
 * Three identities stay separate:
 *   - workbench instance : this module-level singleton (display lifetime only)
 *   - dockedSessionId    : the DSH conversation whose native body currently
 *                          hosts the root (follows the host)
 *   - linkedSessionId    : the conversation the Agent/focus bridge/research
 *                          references really belong to (pinned keeps it)
 */
window.__ModuleLoader__.load({
    id: "@acp/dsh-panels",
    factory: function (require) {
        "use strict";
        var React = require("react");
        /* Font/emoji-free ES5: hosts may ship an older React without layout
         * effects, and the fake-DOM tests only need one effect hook. */
        var useLayoutEffect = React.useLayoutEffect || React.useEffect;
        var PANEL_ID = "acp-workbench";
        var SLOT_TAB = "sidebar.right.pane.tab";
        var SLOT_TITLE = "sidebar.right.pane.tab.title";
        var SLOT_HEADER = "conversation.session.header.utilities";
        var TITLE = "ACP-RPH";
        var URI_PREFIX = "dsh-resource://acp-workbench/";
        var WORKBENCH_CLASS = "acp-dsh-workbench";
        var BAR_CLASS = "acp-dsh-workbench-bar";
        var MOUNT_CLASS = "acp-dsh-workbench-mount";
        var STORE_MODE = "acp-dsh:workbench.mode";
        var STORE_LINKED = "acp-dsh:workbench.linked";
        var STORE_INTENT = "acp-dsh:workbench.intent";
        var MODE_PINNED = "pinned";
        var MODE_FOLLOW = "follow";
        var DOCK_ATTEMPTS = 10;
        var DOCK_DELAY_MS = 80;
        var NO_SURFACE_RE = /no session surface is mounted/i;

        var owner = createOwner();

        function win() { return typeof window !== "undefined" ? window : null; }
        function message(key) {
            var w = win();
            var zh = !!(w && w.document && /^zh/i.test(w.document.documentElement.lang || ""));
            var texts = {
                missing: zh ? "ACP 运行时尚未加载。" : "ACP runtime is not loaded yet.",
                failed: zh ? "ACP 工作台加载失败" : "ACP workbench failed to load",
                open: zh ? "在右侧工作区打开 ACP" : "Open ACP in the right workspace",
                retry: zh ? "重试" : "Retry",
                pinned: zh ? "保持工作台" : "Keep workbench",
                follow: zh ? "跟随对话" : "Follow conversation",
                pinnedShort: zh ? "保持" : "Keep",
                followShort: zh ? "跟随" : "Follow",
                chat: zh ? "对话 " : "Chat ",
                chatUnlinked: zh ? "对话未关联" : "Chat not linked",
                toPinned: zh ? "切换为保持工作台" : "Switch to keep workbench",
                toFollow: zh ? "切换为跟随对话" : "Switch to follow conversation",
                linked: zh ? "关联研究：对话 " : "Linked research: conversation ",
                unlinked: zh ? "关联研究：未关联" : "Linked research: not linked",
                browsing: zh ? "当前浏览对话 " : "Browsing conversation ",
                associate: zh ? "关联当前对话" : "Link current conversation",
                associating: zh ? "正在关联当前对话…" : "Linking the current conversation…",
                associateFailed: zh ? "关联失败" : "Link failed"
            };
            return texts[key];
        }
        function runtime() { var w = win(); return w && w.ACPDSH; }

        function createOwner() {
            return {
                ctx: null,
                doc: null,
                root: null,
                mountPoint: null,
                bar: null,
                barMode: null,
                barLink: null,
                barDiff: null,
                barAssociate: null,
                container: null,
                revision: 0,
                linkedSessionId: null,
                dockedSessionId: null,
                mounted: false,
                everAttached: false,
                visible: false,
                /* Captured when the docked conversation changes: whether the
                 * workbench was in the foreground at that moment. The brief
                 * gap between the old body detaching and the new one attaching
                 * must not be mistaken for the user hiding the workbench. */
                dockIntent: true,
                mode: MODE_PINNED,
                intentOpen: true,
                initialised: false,
                disposed: false,
                error: null,
                errorSink: null,
                mountTimers: [],
                dockTimers: []
            };
        }

        /* --------------------------------------------------------- storage */

        function storage() {
            var w = win();
            try { return w && w.localStorage ? w.localStorage : null; } catch (err) { return null; }
        }
        function readPref(key) {
            var store = storage();
            if (!store) return null;
            try { return store.getItem(key); } catch (err) { return null; }
        }
        function writePref(key, value) {
            var store = storage();
            if (!store) return;
            try {
                if (value === null || value === undefined || value === "") store.removeItem(key);
                else store.setItem(key, String(value));
            } catch (err) { /* private mode / disabled storage: preference stays in memory */ }
        }
        function ensureInit() {
            if (owner.initialised) return;
            owner.initialised = true;
            if (readPref(STORE_MODE) === MODE_FOLLOW) owner.mode = MODE_FOLLOW;
            var linked = readPref(STORE_LINKED);
            if (linked) owner.linkedSessionId = linked;
            if (readPref(STORE_INTENT) === "0") owner.intentOpen = false;
        }

        /* ------------------------------------------------------- utilities */

        function removeNode(node) {
            if (!node) return false;
            try {
                if (node.parentNode && typeof node.parentNode.removeChild === "function") {
                    node.parentNode.removeChild(node);
                    return true;
                }
                if (typeof node.remove === "function") {
                    node.remove();
                    return true;
                }
            } catch (err) { /* best effort */ }
            return false;
        }
        function clearList(list) {
            var i;
            for (i = 0; i < list.length; i++) {
                try { clearTimeout(list[i]); } catch (err) { /* best effort */ }
            }
            list.length = 0;
        }
        /* Closing a tab only cancels late-runtime probes for that attachment;
         * a pending auto-dock retry must survive so the next conversation can
         * still get its native surface once the store is minted. */
        function clearMountTimers() { clearList(owner.mountTimers); }
        function clearDockTimers() { clearList(owner.dockTimers); }
        function clearAllTimers() { clearMountTimers(); clearDockTimers(); }
        function later(bucket, fn, delay) {
            var timer = setTimeout(fn, delay);
            bucket.push(timer);
            return timer;
        }
        function labelOf(sessionId) {
            if (!sessionId) return "";
            var text = String(sessionId);
            return text.length > 6 ? text.slice(text.length - 6) : text;
        }
        function notifyError(code) {
            owner.error = code || null;
            if (typeof owner.errorSink === "function") {
                try { owner.errorSink(owner.error); } catch (err) { /* render is best effort */ }
            }
        }

        /* Critical geometry is owned by the native body and by this root, even
         * while an older stylesheet remains cached during a live replacement. */
        function sizeHost(scope) {
            if (!scope || !scope.querySelector) return;
            var host = scope.querySelector(".acp-dsh-host");
            if (host && host.style) {
                host.style.flex = "1";
                host.style.minHeight = "0";
                host.style.minWidth = "0";
                host.style.overflow = "hidden";
                host.style.position = "relative";
            }
        }

        /* ------------------------------------------------------ title bar */

        function element(doc, tag, className, text) {
            var el = doc.createElement(tag);
            if (className) el.className = className;
            if (text !== undefined) el.textContent = text;
            return el;
        }
        function renderBar() {
            if (!owner.bar) return;
            var linked = owner.linkedSessionId;
            var docked = owner.dockedSessionId;
            var differs = !!(linked && docked && linked !== docked);
            var display = docked || linked;
            owner.bar.setAttribute("data-acp-workbench-mode", owner.mode);
            owner.bar.setAttribute("data-acp-workbench-linked-state", linked ? "linked" : "unlinked");
            owner.bar.setAttribute("data-acp-workbench-differs", differs ? "true" : "false");
            if (owner.barMode) {
                owner.barMode.textContent = owner.mode === MODE_FOLLOW ? message("followShort") : message("pinnedShort");
                owner.barMode.setAttribute("data-acp-workbench-mode", owner.mode);
                owner.barMode.setAttribute("aria-pressed", owner.mode === MODE_PINNED ? "true" : "false");
                owner.barMode.title = owner.mode === MODE_FOLLOW ? message("toPinned") : message("toFollow");
            }
            if (owner.barLink) {
                owner.barLink.textContent = display ? message("chat") + labelOf(display) : message("chatUnlinked");
                owner.barLink.title = linked ? message("linked") + labelOf(linked) + (docked && docked !== linked ? " · " + message("browsing") + labelOf(docked) : "") : message("unlinked");
                owner.barLink.style.display = "";
            }
            if (owner.barDiff) {
                owner.barDiff.textContent = differs
                    ? message("browsing") + labelOf(docked) + " · " + message("linked") + labelOf(linked)
                    : "";
                owner.barDiff.style.display = differs ? "" : "none";
            }
            if (owner.barAssociate) owner.barAssociate.style.display = differs ? "" : "none";
            if (owner.root) {
                owner.root.setAttribute("data-acp-workbench-mode", owner.mode);
                owner.root.setAttribute("data-acp-workbench-linked", linked || "");
                owner.root.setAttribute("data-acp-workbench-docked", docked || "");
                owner.root.setAttribute("data-acp-workbench-intent", owner.intentOpen ? "open" : "closed");
            }
            dockSessionBar();
        }
        function toggleMode() {
            owner.mode = owner.mode === MODE_FOLLOW ? MODE_PINNED : MODE_FOLLOW;
            writePref(STORE_MODE, owner.mode);
            renderBar();
            if (owner.mode === MODE_FOLLOW && owner.dockedSessionId) followSession(owner.dockedSessionId);
        }
        function followSession(sessionId) {
            if (!sessionId || owner.linkedSessionId === sessionId) return;
            var api = runtime();
            if (!api || typeof api.reassociate !== "function") return;
            var pending;
            try { pending = api.reassociate(sessionId); } catch (err) { return; }
            Promise.resolve(pending).then(function (outcome) {
                if (!outcome || outcome.ok === false) return;
                owner.linkedSessionId = outcome.sessionId || sessionId;
                writePref(STORE_LINKED, owner.linkedSessionId);
                renderBar();
            }, function () { /* follow is best effort; the explicit action reports */ });
        }
        function associateCurrent() {
            var docked = owner.dockedSessionId;
            var api = runtime();
            if (!docked || !api || typeof api.reassociate !== "function") return;
            var pending;
            try { pending = api.reassociate(docked); } catch (err) { notifyError("reassociate_threw"); return; }
            Promise.resolve(pending).then(function (outcome) {
                if (!outcome || outcome.ok === false) {
                    notifyError(outcome && outcome.code ? outcome.code : "reassociate_failed");
                    renderBar();
                    return;
                }
                if (outcome.changed !== false) {
                    owner.linkedSessionId = outcome.sessionId || docked;
                    writePref(STORE_LINKED, owner.linkedSessionId);
                }
                notifyError(null);
                renderBar();
            }, function () { notifyError("reassociate_failed"); });
        }

        /* --------------------------------------------------- root creation */

        function ensureRoot(container) {
            if (owner.root) return owner.root;
            var doc = (container && container.ownerDocument) || (win() && win().document);
            if (!doc || typeof doc.createElement !== "function") return null;
            owner.doc = doc;

            var root = element(doc, "div", WORKBENCH_CLASS);
            root.setAttribute("data-acp-workbench-root", "1");
            root.style.height = "100%";
            root.style.width = "100%";
            root.style.display = "flex";
            root.style.flexDirection = "column";
            root.style.flex = "1";
            root.style.minHeight = "0";
            root.style.minWidth = "0";
            root.style.overflow = "hidden";
            root.style.position = "relative";

            var bar = element(doc, "div", BAR_CLASS);
            bar.setAttribute("data-acp-workbench-bar", "1");
            bar.style.flex = "none";
            bar.style.display = "none";
            bar.style.alignItems = "center";
            bar.style.gap = "6px";
            bar.style.minHeight = "0";
            bar.style.overflow = "hidden";

            var sessionIcon = element(doc, "span", "acp-dsh-session-icon", "");
            sessionIcon.setAttribute("aria-hidden", "true");
            var link = element(doc, "span", "acp-dsh-workbench-linked", message("chatUnlinked"));
            link.setAttribute("data-acp-workbench-linked-info", "1");
            var diff = element(doc, "span", "acp-dsh-workbench-diff", "");
            diff.setAttribute("data-acp-workbench-diff", "1");
            diff.style.display = "none";
            var modeBtn = element(doc, "button", "acp-dsh-workbench-mode", message("pinnedShort"));
            modeBtn.type = "button";
            modeBtn.setAttribute("data-acp-workbench-mode-button", "1");
            modeBtn.onclick = toggleMode;
            var associate = element(doc, "button", "acp-dsh-workbench-associate", message("associate"));
            associate.type = "button";
            associate.setAttribute("data-acp-workbench-associate", "1");
            associate.style.display = "none";
            associate.onclick = associateCurrent;

            bar.appendChild(sessionIcon);
            bar.appendChild(link);
            bar.appendChild(diff);
            bar.appendChild(modeBtn);
            bar.appendChild(associate);

            var mountPoint = element(doc, "div", MOUNT_CLASS);
            mountPoint.setAttribute("data-acp-workbench-mount", "1");
            mountPoint.style.flex = "1";
            mountPoint.style.minHeight = "0";
            mountPoint.style.minWidth = "0";
            mountPoint.style.display = "flex";
            mountPoint.style.flexDirection = "column";
            mountPoint.style.overflow = "hidden";
            mountPoint.style.position = "relative";

            root.appendChild(bar);
            root.appendChild(mountPoint);

            owner.root = root;
            owner.bar = bar;
            owner.barMode = modeBtn;
            owner.barLink = link;
            owner.barDiff = diff;
            owner.barAssociate = associate;
            owner.mountPoint = mountPoint;
            renderBar();
            return root;
        }

        /* The ACP shell owns the single topbar. The legacy window bar is moved
         * into its dedicated slot after the shell mounts; it is never a second
         * row. Before the slot exists it stays hidden (old skeleton: no second
         * bar rather than a duplicate title row). */
        function dockSessionBar() {
            if (!owner.bar) return false;
            var doc = owner.doc || (win() && win().document);
            var slot = doc && typeof doc.getElementById === "function" ? doc.getElementById("acp-dsh-session-slot") : null;
            if (!slot || typeof slot.appendChild !== "function") return false;
            /* Only the real ACP topbar may adopt the bar. A bare fixture id is
             * not enough: moving it into arbitrary markup would reintroduce a
             * second row. */
            var inTopbar = slot.closest ? slot.closest(".acp-shell-topbar") : null;
            if (!inTopbar) return false;
            if (owner.bar.parentNode !== slot) {
                try { slot.appendChild(owner.bar); } catch (err) { return false; }
            }
            owner.bar.style.display = "flex";
            slot.setAttribute("data-acp-session-ready", "true");
            return true;
        }

        /* ------------------------------------------------------ attaching */

        function ensureMounted(fallbackSessionId) {
            var api = runtime();
            if (!api || typeof api.mount !== "function") {
                owner.mounted = false;
                scheduleMountRetry(fallbackSessionId);
                return;
            }
            owner.mounted = true;
            var sessionId = owner.linkedSessionId || fallbackSessionId;
            var revision = owner.revision;
            var pending;
            try {
                pending = api.mount(owner.mountPoint, { sessionId: sessionId });
            } catch (err) {
                owner.mounted = false;
                notifyError("mount_threw");
                return;
            }
            Promise.resolve(pending).then(function (outcome) {
                if (owner.disposed) return;
                if (outcome && outcome.ok === false) {
                    owner.mounted = false;
                    notifyError(outcome.code || "mount_failed");
                    return;
                }
                if (revision !== owner.revision && owner.container === null) return;
                notifyError(null);
                dockSessionBar();
                if (owner.container) sizeHost(owner.container);
            }, function () {
                owner.mounted = false;
                notifyError("mount_failed");
            });
        }
        function scheduleMountRetry(fallbackSessionId) {
            var attempts = 0;
            function tick() {
                if (owner.disposed || !owner.root || owner.container === null) return;
                var api = runtime();
                if (api && typeof api.mount === "function") {
                    ensureMounted(fallbackSessionId);
                    return;
                }
                attempts = attempts + 1;
                if (attempts >= 30) {
                    notifyError("runtime_timeout");
                    return;
                }
                later(owner.mountTimers, tick, 500);
            }
            later(owner.mountTimers, tick, 500);
        }

        /* Attach the single window root to a native tab body. Moving the node
         * keeps every child, listener, Viewer and internal scope intact. */
        function attach(container, sessionId) {
            ensureInit();
            if (!container || typeof container.appendChild !== "function") return owner.revision;
            var root = ensureRoot(container);
            if (!root) return owner.revision;
            var node = root;

            owner.revision = owner.revision + 1;
            var revision = owner.revision;
            owner.container = container;
            owner.visible = true;
            owner.everAttached = true;
            if (sessionId) owner.dockedSessionId = sessionId;
            if (!owner.linkedSessionId && sessionId) {
                owner.linkedSessionId = sessionId;
                writePref(STORE_LINKED, owner.linkedSessionId);
            }
            try {
                container.appendChild(node);
            } catch (err) {
                notifyError("attach_failed");
                return revision;
            }
            if (!owner.mounted) {
                if (owner.mode === MODE_FOLLOW && sessionId && owner.linkedSessionId !== sessionId) {
                    owner.linkedSessionId = sessionId;
                    writePref(STORE_LINKED, owner.linkedSessionId);
                }
                ensureMounted(sessionId);
            } else {
                if (owner.mode === MODE_FOLLOW && sessionId) followSession(sessionId);
                sizeHost(container);
            }
            dockSessionBar();
            renderBar();
            return revision;
        }

        /* Only the currently attached container may remove the root. A late
         * cleanup from an old native body must never detach or dispose the
         * root that has already moved to a new one. */
        function detach(container, revision) {
            if (owner.container !== container) return false;
            if (typeof revision === "number" && revision !== owner.revision) return false;
            if (owner.root && owner.root.parentNode === container) removeNode(owner.root);
            owner.container = null;
            owner.visible = false;
            clearMountTimers();
            return true;
        }

        function dispose() {
            owner.disposed = true;
            clearAllTimers();
            var api = runtime();
            if (api && typeof api.unmount === "function" && (owner.mounted || owner.everAttached)) {
                try { api.unmount(); } catch (err) { /* teardown is best effort */ }
            }
            if (owner.root && owner.root.parentNode) removeNode(owner.root);
            owner = createOwner();
        }

        /* -------------------------------------------------- native docking */

        function readMounted(source) {
            if (!source) return null;
            var value = null;
            try {
                if (typeof source === "function") value = source();
                else if (typeof source.getSnapshot === "function") value = source.getSnapshot();
            } catch (err) { value = null; }
            if (!value) return null;
            if (typeof value === "string") return value;
            if (typeof value === "object") {
                if (typeof value.sessionId === "string" && value.sessionId) return value.sessionId;
                if (typeof value.id === "string" && value.id) return value.id;
                if (value.session && typeof value.session.id === "string" && value.session.id) return value.session.id;
            }
            return null;
        }
        function readList(source) {
            if (!source) return null;
            try {
                if (typeof source.getSnapshot === "function") return source.getSnapshot();
            } catch (err) { return null; }
            return null;
        }
        function tabMatches(tab, sessionId) {
            if (!tab) return false;
            var kind = tab.kind || tab.type || tab.id || tab.tabId || "";
            var resource = tab.resource || tab.address || tab.uri || tab.href || "";
            var isWorkbench = kind === PANEL_ID || String(resource).indexOf(URI_PREFIX) === 0;
            if (!isWorkbench) return false;
            var tabSession = tab.sessionId || tab.session || tab.dockedSessionId || "";
            if (tabSession) return tabSession === sessionId;
            var text = String(resource);
            return text.indexOf(encodeURIComponent(sessionId)) >= 0 || text.indexOf(sessionId) >= 0;
        }
        function hasWorkbenchTab(sr, sessionId) {
            var list = readList(sr && sr.openTabs);
            if (!list || typeof list.length !== "number") return false;
            var i;
            for (i = 0; i < list.length; i++) {
                if (tabMatches(list[i], sessionId)) return true;
            }
            return false;
        }

        /* Bounded retry: the sidebar store may not be minted yet when the new
         * conversation surface appears. Never loop blindly. */
        function autoDock(sessionId) {
            var sr = owner.ctx && owner.ctx.sidebarRight;
            if (owner.disposed || !sr || typeof sr.openResource !== "function" || !sessionId) return;
            if (hasWorkbenchTab(sr, sessionId)) return;
            var attempts = 0;
            function attempt() {
                if (owner.disposed) return;
                if (owner.mode !== MODE_PINNED) return;
                if (!owner.intentOpen) return;
                if (!owner.dockIntent) return;
                var current = readMounted(sr.mounted);
                if (current && current !== sessionId) return;
                var outcome;
                try {
                    outcome = sr.openResource(URI_PREFIX + encodeURIComponent(sessionId));
                } catch (err) {
                    retry(err);
                    return;
                }
                if (outcome && typeof outcome.then === "function") {
                    outcome.then(null, retry);
                }
            }
            function retry(err) {
                if (attempts >= DOCK_ATTEMPTS) return;
                if (!NO_SURFACE_RE.test(String((err && err.message) || err))) return;
                attempts = attempts + 1;
                later(owner.dockTimers, attempt, DOCK_DELAY_MS);
            }
            attempt();
        }

        function handleDocked(sessionId) {
            if (!sessionId) return;
            var previous = owner.dockedSessionId;
            owner.dockedSessionId = sessionId;
            /* Record the foreground intent before any native body cleanup can
             * flip owner.visible to false. */
            owner.dockIntent = owner.visible || !owner.everAttached;
            if (previous !== sessionId) renderBar();
            if (owner.mode === MODE_FOLLOW) {
                followSession(sessionId);
                return;
            }
            if (!owner.intentOpen) return;
            if (!owner.dockIntent) return;
            autoDock(sessionId);
        }

        /* -------------------------------------------------------- AcpBody */

        function AcpBody(props) {
            var info = props.useTabInfo();
            var visible = readVisible(info);
            var sessionId = props.sessionId;
            var hostRef = React.useRef(null);
            var pair = React.useState(0), retry = pair[0], setRetry = pair[1];
            var errors = React.useState(null), error = errors[0], setError = errors[1];

            useLayoutEffect(function () {
                var container = hostRef.current;
                if (!container) return undefined;
                if (!visible || !sessionId) {
                    detach(container);
                    return undefined;
                }
                owner.errorSink = setError;
                var revision = attach(container, sessionId);
                if (owner.error) setError(owner.error);
                return function () {
                    if (owner.errorSink === setError) owner.errorSink = null;
                    detach(container, revision);
                };
            }, [visible, sessionId, retry]);

            return React.createElement("div", {
                className: "acp-dsh-panel-root",
                "data-acp-native-tab": "1",
                "data-acp-session": sessionId || "",
                "data-acp-visible": visible ? "true" : "false",
                style: { height: "100%", width: "100%", minHeight: 0, minWidth: 0,
                    display: "flex", flexDirection: "column", position: "relative", overflow: "hidden", contain: "layout paint" }
            }, [React.createElement("div", { key: "host", className: "acp-dsh-mount", ref: hostRef,
                style: { display: "flex", flexDirection: "column", flex: "1", minHeight: 0, minWidth: 0, overflow: "hidden" } }),
                error ? React.createElement("div", { key: "error", className: "acp-dsh-message", role: "alert" }, [
                    message("failed") + " [" + error + "] ",
                    React.createElement("button", { key: "retry", type: "button", onClick: function () { setRetry(retry + 1); } }, message("retry"))
                ]) : null]);
        }

        /* Defensive: the exact shape of useTabInfo() beyond tab.visible has not
         * been verified on the real host. Only known booleans are folded in. */
        function readVisible(info) {
            if (!info || !info.tab || !info.tab.visible) return false;
            var sidebar = info.sidebar;
            if (sidebar && typeof sidebar === "object") {
                if (typeof sidebar.expanded === "boolean") return sidebar.expanded;
                if (typeof sidebar.open === "boolean") return sidebar.open;
                if (typeof sidebar.visible === "boolean") return sidebar.visible;
                if (typeof sidebar.collapsed === "boolean") return !sidebar.collapsed;
            }
            return true;
        }

        function AcpTitle() { return React.createElement("span", null, TITLE); }
        function apply(ctx) {
            ensureInit();
            owner.ctx = ctx;
            ctx.effect(function () {
                return ctx.sidebarRightTabs.register({
                    id: PANEL_ID, kind: PANEL_ID, keepMounted: true,
                    patterns: ["dsh-resource://acp-workbench/**"],
                    canOpen: function (address) { return /^dsh-resource:\/\/acp-workbench\/[^/?#]+$/.test(address); },
                    title: function () { return TITLE; }
                });
            });
            ctx.effect(function () {
                return ctx.slots.inject(SLOT_TAB, function () {
                    return ctx.slots.register({ name: SLOT_TAB, key: PANEL_ID }, AcpBody);
                });
            });
            ctx.effect(function () {
                return ctx.slots.inject(SLOT_TITLE, function () {
                    return ctx.slots.register({ name: SLOT_TITLE, key: PANEL_ID }, AcpTitle);
                });
            });
            ctx.effect(function () {
                return ctx.slots.inject(SLOT_HEADER, function () {
                    return ctx.slots.register({ name: SLOT_HEADER, id: PANEL_ID, order: 10 }, function (props) {
                        return React.createElement("button", {
                            type: "button", className: "acp-dsh-header-toggle", title: message("open"),
                            "data-acp-open": "native", disabled: !props.sessionId,
                            onClick: function () {
                                if (!props.sessionId) return;
                                owner.intentOpen = true;
                                writePref(STORE_INTENT, "1");
                                renderBar();
                                if (ctx.sidebarRight && typeof ctx.sidebarRight.openResource === "function") {
                                    ctx.sidebarRight.openResource(URI_PREFIX + encodeURIComponent(props.sessionId));
                                }
                            }
                        }, TITLE);
                    });
                });
            });
            /* Foreground conversation source: the only place that knows which
             * DSH session the native right surface currently belongs to. */
            ctx.effect(function () {
                var sr = ctx.sidebarRight;
                if (!sr || !sr.mounted || typeof sr.mounted.subscribe !== "function") return undefined;
                function onChange() { handleDocked(readMounted(sr.mounted)); }
                var unsubscribe = sr.mounted.subscribe(onChange);
                onChange();
                return function () {
                    if (typeof unsubscribe === "function") unsubscribe();
                };
            });
            /* Closing the ACP tab records intent; the runtime is only detached,
             * so reopening restores the same instance and its context. */
            ctx.effect(function () {
                var sr = ctx.sidebarRight;
                if (!sr || typeof sr.registerCloseHandler !== "function") return undefined;
                return sr.registerCloseHandler(PANEL_ID, function () {
                    owner.intentOpen = false;
                    writePref(STORE_INTENT, "0");
                    renderBar();
                });
            });
            ctx.effect(function () { return function () { dispose(); }; });
        }
        return { inject: ["slots", "sidebarRightTabs", "sidebarRight"], apply: apply };
    }
});
