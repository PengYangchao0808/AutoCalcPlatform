/* global window */
/*
 * ACP x DSH mounting runtime (browser half) -- classic script, ES5 style.
 *
 * Publishes exactly one global:
 *   window.ACPDSH = { mount, unmount, suspend, resume, reassociate, state, version }
 *
 * mount(container, options) returns a plain result object for the synchronous
 * guard failures (invalid_container / already_mounted / host_config_unavailable)
 * and a Promise for the asynchronous bootstrap phase (health probe, asset
 * loading, env install, shell mount, boot, focus bridge). The Promise never
 * rejects: every failure resolves to { ok: false, code, message, ... } and a
 * visible message is rendered inside the supplied container.
 *
 * options = { sessionId, readOnly, workspace }
 *   sessionId  forwarded to ACP_ENV.sessionId (see resolveSessionId below).
 *   readOnly   defaults to window.__ACP_PANELS_CONFIG__.readOnly !== false.
 *   workspace  reserved; the ACP shell has no verified workspace binding
 *              contract, so it is currently NOT forwarded (see NOT VERIFIED).
 *
 * Only one native mount is active; retained tabs keep inert private scopes.
 * Runtime state is private to each native DOM mount. The generated factory
 * includes the shared shell, all real modules and the five DSH adapters.
 * Timers/listeners/fetches are owned by runtime-scope.js and disposed on close.
 * The normal DSH slot passes the real session id; the per-tab fallback exists
 * only for hosts that do not supply it. HMR and concurrent ACP panels remain
 * outside the verified single-active-panel contract.
 */
(function (root) {
    "use strict";

    var VERSION = "0.1.0";

    var HOST_CLASS = "acp-dsh-host";
    var HOST_ATTR = "data-acp-host";
    var BANNER_ATTR = "data-acp-dsh-banner";
    var CSS_MARKER = "data-acp-dsh-css";
    var SESSION_KEY = "acp-dsh-session";
    var STORAGE_PREFIX = "acp-dsh:";
    var GENERATION = Date.now();
    var HEALTH_TIMEOUT_MS = 2500;

    /* Ids the ACP shell skeleton must provide in the live document before
     * ACPShell.bootstrap.boot() is allowed to run. The tab and slot ids are
     * dynamic, so only their prefixes can be checked (see REQUIRED_ID_PREFIXES).
     */
    var REQUIRED_IDS = [
        "acp-shell",
        "acp-shell-main",
        "acp-shell-fatal",
        "acp-focus-chip",
        "acp-skip-link",
        "acp-lang-toggle",
        "acp-layout-edit",
        "acp-project-btn",
        "acp-project-popover",
        "acp-view-menu-btn",
        "acp-settings-btn",
        "acp-settings-menu",
        "acp-dsh-session-slot",
        "acp-conn-status",
        "acp-theme-toggle",
        "acp-next-body",
        "acp-next-task-sash",
        "acp-next-task-mode",
        "acp-approvals-btn",
        "acp-system-btn",
        "acp-platform-panel",
        "acp-platform-host",
        "acp-next-task-host",
        "acp-ws-research",
        "acp-ws-drawing",
        "acp-ws-molecular",
        "acp-ws-config",
        "acp-ws-execution",
        "acp-ws-files"
    ];

    var REQUIRED_ID_PREFIXES = ["acp-tab-", "acp-slot-"];

    /* The five adapter files the host injects; loaded here when absent. */
    var ADAPTER_FILES = [
        "/adapters/env.js",
        "/adapters/base-url.js",
        "/adapters/restricted-client.js",
        "/adapters/theme.js",
        "/adapters/focus-bridge.js"
    ];

    /* Tiny local dictionary: the runtime must never depend on the shell i18n
     * bundle for its own failure/degraded messaging.
     */
    var TEXT = {
        zh: {
            degraded: "ACP Controller 未连接",
            alreadyMounted: "ACP 面板已在本页挂载，请使用现有实例。",
            hostConfigUnavailable: "ACP 宿主配置不可用，面板无法挂载。",
            skeletonIncomplete: "ACP 界面骨架不完整，面板无法挂载。",
            skeletonUnavailable: "ACP 界面骨架资源缺失，面板无法挂载。",
            assetLoadFailed: "ACP 前端资源加载失败。",
            adaptersUnavailable: "ACP 适配层不可用，面板无法挂载。",
            bootFailed: "ACP 界面启动失败。",
            mountFailed: "ACP 面板挂载失败。",
            invalidContainer: "ACP 挂载目标不是有效的 DOM 容器。"
        },
        en: {
            degraded: "ACP Controller unreachable",
            alreadyMounted: "ACP panel is already mounted on this page.",
            hostConfigUnavailable: "ACP host configuration is unavailable; the panel cannot mount.",
            skeletonIncomplete: "ACP shell skeleton is incomplete; the panel cannot mount.",
            skeletonUnavailable: "ACP shell skeleton asset is missing; the panel cannot mount.",
            assetLoadFailed: "ACP front-end assets failed to load.",
            adaptersUnavailable: "ACP adapter layer is unavailable; the panel cannot mount.",
            bootFailed: "ACP shell failed to boot.",
            mountFailed: "ACP panel failed to mount.",
            invalidContainer: "The ACP mount target is not a valid DOM container."
        }
    };

    /* Module state. _session is the single live mount, if any. */
    var _session = null;
    /* Retained native tab bodies are inert until explicitly resumed. */
    var _retained = [];
    var _state = {
        mounted: false,
        readOnly: true,
        degraded: false,
        sessionId: null,
        namespaces: [],
        booted: false
    };

    /* Per-src cache so a script src is never loaded twice by this runtime. */
    var _scriptCache = {};

    /* ---------------------------------------------------------------- utils */

    function has(obj, key) {
        return Object.prototype.hasOwnProperty.call(obj, key);
    }

    function textOf(err) {
        if (!err) {
            return "";
        }
        if (typeof err === "string") {
            return err;
        }
        return String(err.message || err);
    }

    function copyList(list) {
        var out = [];
        var i;
        if (!list) {
            return out;
        }
        for (i = 0; i < list.length; i++) {
            out.push(list[i]);
        }
        return out;
    }

    function langOf(win) {
        var doc = win && win.document ? win.document : null;
        var html = doc && doc.documentElement ? doc.documentElement : null;
        var lang = html && html.lang ? String(html.lang) : "";
        if (!lang) {
            return "en";
        }
        return /^zh/i.test(lang) ? "zh" : "en";
    }

    function t(win, key) {
        var dict = TEXT[langOf(win)] || TEXT.en;
        if (dict && has(dict, key)) {
            return dict[key];
        }
        return TEXT.en[key] || key;
    }

    function removeNode(node) {
        if (!node) {
            return false;
        }
        try {
            if (node.parentNode && typeof node.parentNode.removeChild === "function") {
                node.parentNode.removeChild(node);
                return true;
            }
            if (typeof node.remove === "function") {
                node.remove();
                return true;
            }
        } catch (err) {
            /* best effort */
        }
        return false;
    }

    function showMessage(win, container, message, attrs) {
        var doc = win && win.document ? win.document : null;
        var el;
        var key;
        if (!doc || !container || typeof container.appendChild !== "function") {
            return null;
        }
        try {
            el = doc.createElement("div");
        } catch (err) {
            return null;
        }
        el.className = "acp-dsh-message";
        try {
            el.textContent = message;
        } catch (err2) {
            /* best effort */
        }
        if (attrs) {
            for (key in attrs) {
                if (has(attrs, key)) {
                    try {
                        el.setAttribute(key, String(attrs[key]));
                    } catch (err3) {
                        /* best effort */
                    }
                }
            }
        }
        try {
            container.appendChild(el);
        } catch (err4) {
            return null;
        }
        return el;
    }

    function createBanner(win) {
        var doc = win && win.document ? win.document : null;
        var el;
        if (!doc) {
            return null;
        }
        try {
            el = doc.createElement("div");
        } catch (err) {
            return null;
        }
        el.className = "acp-dsh-banner";
        try {
            el.setAttribute(BANNER_ATTR, "degraded");
            el.setAttribute("role", "status");
            el.setAttribute("aria-live", "polite");
        } catch (err2) {
            /* best effort */
        }
        try {
            el.textContent = t(win, "degraded");
        } catch (err3) {
            /* best effort */
        }
        return el;
    }

    /* --------------------------------------------------------- session id */

    /*
     * Per-tab placeholder session id. The real DSH session identity binding is
     * NOT VERIFIED: no host-side contract was confirmed for passing the DSH
     * conversation/session id into the ACP shell. The value is stable for the
     * lifetime of the tab (sessionStorage) and is only used to correlate focus
     * bridge snapshots.
     */
    function resolveSessionId(win, options) {
        var store = null;
        var key = SESSION_KEY;
        var value = null;
        var generated;
        if (options && typeof options.sessionId === "string" && options.sessionId) {
            return options.sessionId;
        }
        try {
            store = win.sessionStorage || null;
        } catch (err) {
            store = null;
        }
        if (store) {
            try {
                value = store.getItem(key);
            } catch (err2) {
                value = null;
            }
            if (value) {
                return value;
            }
        }
        generated = "dsh-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
        if (store) {
            try {
                store.setItem(key, generated);
            } catch (err3) {
                /* storage may be disabled; the id stays in memory for this mount */
            }
        }
        return generated;
    }

    /* ------------------------------------------------------------ health */

    function probeHealth(win, config) {
        return new Promise(function (resolve) {
            var settled = false;
            var timer = null;

            function finish(result) {
                if (settled) {
                    return;
                }
                settled = true;
                if (timer !== null && typeof win.clearTimeout === "function") {
                    try {
                        win.clearTimeout(timer);
                    } catch (err) {
                        /* best effort */
                    }
                }
                timer = null;
                resolve(result);
            }

            var endpoint = config && config.healthEndpoint ? config.healthEndpoint : null;
            if (!endpoint) {
                finish({ ok: false, code: "health_endpoint_missing" });
                return;
            }
            if (typeof win.fetch !== "function") {
                finish({ ok: false, code: "fetch_unavailable" });
                return;
            }
            if (typeof win.setTimeout === "function") {
                timer = win.setTimeout(function () {
                    finish({ ok: false, code: "health_timeout" });
                }, HEALTH_TIMEOUT_MS);
            }

            var pending;
            try {
                pending = win.fetch(endpoint, {
                    method: "GET",
                    credentials: "same-origin",
                    headers: { accept: "application/json" }
                });
            } catch (err) {
                finish({ ok: false, code: "health_unreachable", message: textOf(err) });
                return;
            }

            Promise.resolve(pending).then(function (resp) {
                if (!resp) {
                    finish({ ok: false, code: "health_unreachable" });
                    return;
                }
                if (resp.ok === false) {
                    /* HTTP-level failure (non 2xx) is treated as unreachable too. */
                    finish({ ok: false, code: "health_unreachable", status: resp.status });
                    return;
                }
                var body = null;
                try {
                    body = typeof resp.json === "function" ? resp.json() : null;
                } catch (err) {
                    body = null;
                }
                Promise.resolve(body).then(function (parsed) {
                    if (parsed && (parsed.ok === false || (parsed.upstream && (parsed.upstream.code || parsed.upstream.status >= 400)))) {
                        finish({ ok: false, code: "health_not_ok", body: parsed });
                        return;
                    }
                    finish({ ok: true, status: resp.status, body: parsed || null });
                }, function () {
                    finish({ ok: true, status: resp.status });
                });
            }, function (err) {
                finish({ ok: false, code: "health_unreachable", message: textOf(err) });
            });
        });
    }

    /* ------------------------------------------------------- asset loading */

    function loadScript(win, src) {
        if (has(_scriptCache, src)) {
            return _scriptCache[src];
        }
        var promise = new Promise(function (resolve) {
            var doc = win && win.document ? win.document : null;
            var el;
            var parent;
            if (!doc || typeof doc.createElement !== "function") {
                resolve({ ok: false, code: "asset_load_failed", src: src, message: "no document" });
                return;
            }
            try {
                el = doc.createElement("script");
            } catch (err) {
                resolve({ ok: false, code: "asset_load_failed", src: src, message: textOf(err) });
                return;
            }
            el.onload = function () {
                resolve({ ok: true, src: src });
            };
            el.onerror = function () {
                resolve({ ok: false, code: "asset_load_failed", src: src });
            };
            try {
                el.async = false;
                el.src = src;
            } catch (err2) {
                resolve({ ok: false, code: "asset_load_failed", src: src, message: textOf(err2) });
                return;
            }
            parent = doc.head || doc.body || doc.documentElement;
            if (!parent || typeof parent.appendChild !== "function") {
                resolve({ ok: false, code: "asset_load_failed", src: src, message: "no head" });
                return;
            }
            try {
                parent.appendChild(el);
            } catch (err3) {
                resolve({ ok: false, code: "asset_load_failed", src: src, message: textOf(err3) });
            }
        });
        _scriptCache[src] = promise;
        return promise;
    }

    function loadScripts(win, srcs) {
        var index = 0;

        function step() {
            var src;
            if (index >= srcs.length) {
                return { ok: true };
            }
            src = srcs[index];
            index = index + 1;
            return loadScript(win, src).then(function (res) {
                if (res && res.ok === false) {
                    return res;
                }
                return step();
            });
        }

        return step();
    }

    function ensureAssets(win, session) {
        var base = session.assetBase;
        var result = { ok: true };

        if (!win.ACP_DSH_SKELETON) {
            result = loadScript(win, base + "/skel.js");
        }

        return Promise.resolve(result).then(function (res) {
            if (res && res.ok === false) {
                return res;
            }
            if (win.ACP_DSH_CREATE_RUNTIME || win.ACPShell) {
                return { ok: true };
            }
            return loadScript(win, base + "/core.bundle.js");
        }).then(function (res) {
            if (res && res.ok === false) {
                return res;
            }
            if (win.ACP_DSH_CREATE_RUNTIME || win.ACPAdapters) {
                return { ok: true };
            }
            var srcs = [];
            var i;
            for (i = 0; i < ADAPTER_FILES.length; i++) {
                srcs.push(base + ADAPTER_FILES[i]);
            }
            return loadScripts(win, srcs);
        }).then(function (res) {
            if (res && res.ok === false) {
                return res;
            }
            if (!win.ACP_DSH_SKELETON) {
                return { ok: false, code: "skeleton_unavailable", message: t(win, "skeletonUnavailable") };
            }
            return { ok: true };
        });
    }

    /* ------------------------------------------------- install before boot */

    function installRuntime(win, session) {
        var adapters = win.ACPAdapters;
        var dsh = adapters && adapters.dsh ? adapters.dsh : null;
        var apiPrefix = session.apiPrefix;
        var envOpts;
        var links;
        var described;

        if (!dsh) {
            return {
                ok: false,
                code: "adapters_unavailable",
                message: t(win, "adaptersUnavailable")
            };
        }

        envOpts = {
            apiPrefix: apiPrefix,
            assetBase: session.assetBase,
            sessionId: session.sessionId,
            generation: session.generation,
            focusEndpoint: session.focusEndpoint,
            storagePrefix: STORAGE_PREFIX,
            manageDocument: false
        };

        if (dsh.env && typeof dsh.env.install === "function") {
            try {
                dsh.env.install(win, envOpts);
                win.ACP_ENV.degraded = session.degraded;
            } catch (err) {
                return {
                    ok: false,
                    code: "env_install_failed",
                    message: textOf(err) || t(win, "mountFailed")
                };
            }
        }

        if (dsh.baseUrl && typeof dsh.baseUrl.installAll === "function") {
            try {
                dsh.baseUrl.installAll(win, { apiPrefix: apiPrefix });
            } catch (err) {
                return {
                    ok: false,
                    code: "base_url_install_failed",
                    message: textOf(err) || t(win, "mountFailed")
                };
            }
        }

        /* Read-only enforcement: only replace the client when read-only. */
        if (session.readOnly && dsh.restrictedClient && typeof dsh.restrictedClient.create === "function") {
            try {
                session.client = dsh.restrictedClient.create(win.ACPClient, {
                    allowWrite: session.readOnly === false
                });
                win.ACPClient = session.client;
            } catch (err) {
                return {
                    ok: false,
                    code: "restricted_client_failed",
                    message: textOf(err) || t(win, "mountFailed")
                };
            }
        }

        if (dsh.restrictedClient && typeof dsh.restrictedClient.describe === "function") {
            try {
                described = dsh.restrictedClient.describe(win.ACPClient);
                if (described && described.namespaces) {
                    session.namespaces = copyList(described.namespaces);
                }
            } catch (err) {
                /* describe is diagnostic only; never fatal */
            }
        }
        _state.namespaces = copyList(session.namespaces);

        if (dsh.theme) {
            if (typeof dsh.theme.linkStyles === "function") {
                try {
                    links = dsh.theme.linkStyles(win.document, {
                        base: session.assetBase,
                        hrefs: ["/panels.css"],
                        marker: CSS_MARKER
                    });
                    if (links && typeof links.length === "number" && links.length > 0) {
                        session.styleLinks = links;
                    }
                } catch (err) {
                    /* fallback stylesheet is best effort */
                }
            }
            if (typeof dsh.theme.apply === "function") {
                try {
                    dsh.theme.apply(win.document, { target: session.container, fallback: true });
                } catch (err) {
                    /* theme mapping is best effort */
                }
            }
        }

        return { ok: true };
    }

    /* -------------------------------------------------------- mount shell */

    function collectIds(skeleton) {
        var ids = [];
        var re = /id="([^"]+)"/g;
        var match = re.exec(skeleton);
        while (match !== null) {
            ids.push(match[1]);
            match = re.exec(skeleton);
        }
        return ids;
    }

    function findMissingIds(doc, skeleton) {
        var missing = [];
        var i;
        var id;
        for (i = 0; i < REQUIRED_IDS.length; i++) {
            id = REQUIRED_IDS[i];
            if (!doc || typeof doc.getElementById !== "function" || !doc.getElementById(id)) {
                missing.push(id);
            }
        }
        for (i = 0; i < REQUIRED_ID_PREFIXES.length; i++) {
            if (skeleton.indexOf("id=\"" + REQUIRED_ID_PREFIXES[i]) === -1) {
                missing.push(REQUIRED_ID_PREFIXES[i] + "*");
            }
        }
        return missing;
    }

    function mountShell(win, session) {
        var doc = win.document;
        var container = session.container;
        var skeleton = win.ACP_DSH_SKELETON;
        var host;
        var missing;

        if (!skeleton) {
            return {
                ok: false,
                code: "skeleton_unavailable",
                message: t(win, "skeletonUnavailable")
            };
        }
        if (!doc || typeof doc.createElement !== "function") {
            return { ok: false, code: "no_document", message: t(win, "mountFailed") };
        }

        /* The degraded banner goes in first so it renders above the shell. */
        if (session.banner) {
            try {
                container.appendChild(session.banner);
            } catch (err) {
                /* banner is best effort */
            }
        }

        try {
            host = doc.createElement("div");
            host.className = HOST_CLASS;
            host.setAttribute(HOST_ATTR, "dsh");
        } catch (err2) {
            return { ok: false, code: "host_create_failed", message: textOf(err2) };
        }

        try {
            container.appendChild(host);
        } catch (err3) {
            return { ok: false, code: "host_append_failed", message: textOf(err3) };
        }
        session.host = host;

        /* The shell boots against document-global ids, so the markup must be
         * attached to the live document before boot. */
        try {
            host.insertAdjacentHTML("beforeend", skeleton);
        } catch (err4) {
            return { ok: false, code: "skeleton_inject_failed", message: textOf(err4) };
        }

        missing = findMissingIds(doc, skeleton);
        if (missing.length > 0) {
            return {
                ok: false,
                code: "skeleton_incomplete",
                missing: missing,
                message: t(win, "skeletonIncomplete")
            };
        }

        return { ok: true };
    }

    function bootShell(win, session) {
        var shell = win.ACPShell;
        if (!shell || !shell.bootstrap || typeof shell.bootstrap.boot !== "function") {
            return { ok: false, code: "shell_unavailable", message: t(win, "bootFailed") };
        }
        try {
            var result = shell.bootstrap.boot();
            if (result && result.ok === false) {
                var fatal = win.document.getElementById("acp-shell-fatal");
                return { ok: false, code: "boot_failed", message: (fatal && fatal.textContent) || t(win, "bootFailed") };
            }
        } catch (err) {
            return { ok: false, code: "boot_failed", message: textOf(err) || t(win, "bootFailed") };
        }
        session.booted = true;
        return { ok: true };
    }

    function attachBridge(win, session, leased) {
        var activation = session.activation || 0;
        if (session.scope && !leased) {
            var endpoint = session.focusEndpoint;
            return win.fetch(endpoint + "?session=" + encodeURIComponent(session.sessionId)).then(function (response) {
                if (response.status === 404) return null;
                if (!response.ok) throw new Error("focus_lookup_failed");
                return response.json().then(function (body) {
                    session.restoreSnapshot = body.focus.snapshot;
                    return body.focus.generation;
                });
            }).then(function (generation) {
                if (session.cancelled || _session !== session || activation !== (session.activation || 0)) throw new Error("mount_cancelled");
                return win.fetch(endpoint, {
                    method: "POST", headers: { "content-type": "application/json" },
                    body: JSON.stringify({ operation: "acquire", session: session.sessionId, expectedGeneration: generation })
                });
            }).then(function (response) {
                if (!response.ok) throw new Error("focus_owner_changed");
                return response.json();
            }).then(function (lease) {
                if (session.cancelled || _session !== session || activation !== (session.activation || 0)) return { ok: false, code: "mount_cancelled" };
                session.generation = lease.generation;
                if (win.ACP_ENV) win.ACP_ENV.generation = lease.generation;
                var context = win.ACPShell && win.ACPShell.context;
                if (context && context.getSnapshot && context.setFocus &&
                    context.getSnapshot().revision === 0 && session.restoreSnapshot) {
                    var patch = {};
                    ["projectId", "researchRef", "taskRef", "structureRef", "networkNodeId"].forEach(function (key) {
                        if (has(session.restoreSnapshot, key)) patch[key] = session.restoreSnapshot[key];
                    });
                    context.setFocus(patch, "dsh-native-tab-restore");
                }
                session.restoreSnapshot = null;
                return attachBridge(win, session, true);
            }).catch(function (error) {
                if (!session.cancelled && _session === session && activation === (session.activation || 0)) session.bridgeError = textOf(error);
                return { ok: true };
            });
        }
        var adapters = win.ACPAdapters;
        var factory = adapters && adapters.dsh ? adapters.dsh.focusBridge : null;
        var env = win.ACP_ENV || {};
        var endpoint;
        var transport = null;
        var bridge;

        if (!factory || typeof factory.create !== "function") {
            session.bridgeError = "focus_bridge_unavailable";
            return { ok: true };
        }

        endpoint = session.focusEndpoint || env.focusEndpoint || (session.apiPrefix + "/ui/focus");
        if (win.ACPClient && win.ACPClient.transport && typeof win.ACPClient.transport.request === "function") {
            transport = win.ACPClient.transport.request;
        }

        try {
            bridge = factory.create({
                endpoint: endpoint,
                sessionId: session.sessionId,
                generation: session.generation,
                transport: transport
            });
            if (bridge && typeof bridge.attach === "function" && win.ACPShell && win.ACPShell.context) {
                bridge.attach(win.ACPShell.context);
                var snapshot = win.ACPShell.context.getSnapshot && win.ACPShell.context.getSnapshot();
                if (snapshot && snapshot.revision > 0 && typeof bridge.publish === "function") {
                    bridge.publish({ sessionId: session.sessionId, generation: session.generation,
                        revision: snapshot.revision, snapshot: snapshot });
                }
            }
            session.bridge = bridge || null;
        } catch (err) {
            /* the focus bridge must never break the mount */
            session.bridge = null;
            session.bridgeError = textOf(err);
        }
        return { ok: true };
    }

    /* ------------------------------------------------------------- teardown */

    function removeStyleLinks(win, session) {
        var removed = 0;
        var links = session && session.styleLinks ? session.styleLinks : [];
        var extra = [];
        var i;
        var node;
        for (i = 0; i < links.length; i++) {
            if (removeNode(links[i])) {
                removed = removed + 1;
            }
        }
        try {
            if (win && win.document && typeof win.document.querySelectorAll === "function") {
                extra = win.document.querySelectorAll("[" + CSS_MARKER + "]");
            }
        } catch (err) {
            extra = [];
        }
        for (i = 0; i < extra.length; i++) {
            node = extra[i];
            if (removeNode(node)) {
                removed = removed + 1;
            }
        }
        return removed;
    }

    function disposeSession(win, session) {
        win = session.runtime || win;
        session.cancelled = true;
        var disposed = [];
        if (win.ACPShell && win.ACPShell.globalRegions && win.ACPShell.globalRegions.dispose) {
            win.ACPShell.globalRegions.dispose();
            disposed.push("shell.globalRegions.dispose");
        }
        if (win.ACPShell && win.ACPShell.layout && win.ACPShell.layout.disposeAll) {
            win.ACPShell.layout.disposeAll();
            disposed.push("shell.layout.disposeAll");
        }
        if (win.ACPShell && win.ACPShell.toolDrawer && win.ACPShell.toolDrawer.close) win.ACPShell.toolDrawer.close();
        if (session.scope) { session.scope.dispose(); disposed.push("scope.dispose"); }

        if (session.bridge && typeof session.bridge.detach === "function") {
            try {
                session.bridge.detach();
                disposed.push("focus_bridge.detach");
            } catch (err) {
                /* best effort */
            }
        }

        var closed = false;
        var candidates = [win.ACPClient, session.originalClient];
        var i;
        for (i = 0; i < candidates.length && !closed; i++) {
            try {
                if (candidates[i] && candidates[i].events && typeof candidates[i].events.closeStream === "function") {
                    candidates[i].events.closeStream();
                    disposed.push("client.events.closeStream");
                    closed = true;
                }
            } catch (err2) {
                /* best effort */
            }
        }

        if (removeNode(session.host)) {
            disposed.push("host.remove");
        }
        if (session.banner && removeNode(session.banner)) {
            disposed.push("banner.remove");
        }

        var links = removeStyleLinks(win, session);
        if (links > 0) {
            disposed.push("stylesheets.remove");
        }

        if (win.ACP_ENV) {
            win.ACP_ENV = null;
            disposed.push("ACP_ENV.reset");
        }

        if (has(session, "originalClient")) {
            win.ACPClient = session.originalClient;
            disposed.push("ACPClient.restore");
        }

        return disposed;
    }

    function resetState(session) {
        if (_session !== session) return;
        _session = null;
        _state = {
            mounted: false,
            readOnly: session ? session.readOnly : true,
            degraded: false,
            sessionId: null,
            namespaces: [],
            booted: false
        };
    }

    function abort(session, failure) {
        var result = {
            ok: false,
            code: failure && failure.code ? failure.code : "mount_failed",
            message: (failure && failure.message) || t(root, "mountFailed")
        };
        if (failure && failure.missing) {
            result.missing = failure.missing;
        }
        if (failure && failure.status) {
            result.status = failure.status;
        }
        try {
            disposeSession(root, session);
        } catch (err) {
            /* teardown is best effort; never mask the original failure */
        }
        resetState(session);
        showMessage(root, session.container, result.message, { "data-acp-dsh-error": result.code });
        return result;
    }

    /* ------------------------------------------------------------- pipeline */

    function sequence(stages) {
        var index = 0;

        function next(value) {
            var stage;
            var output;
            if (value && value.ok === false) {
                return value;
            }
            if (index >= stages.length) {
                return value;
            }
            stage = stages[index];
            index = index + 1;
            try {
                output = stage(value);
            } catch (err) {
                return { ok: false, code: "mount_failed", message: textOf(err) };
            }
            return Promise.resolve(output).then(next);
        }

        return Promise.resolve({ ok: true }).then(next);
    }

    function runMount(session) {
        var win = root;
        var stages = [
            function () {
                return probeHealth(win, session.config).then(function (health) {
                    if (session.cancelled || _session !== session) return { ok: false, code: "mount_cancelled" };
                    session.degraded = health.ok !== true;
                    session.healthCode = health.code || null;
                    _state.degraded = session.degraded;
                    if (session.degraded && !session.banner) {
                        session.banner = createBanner(win);
                    }
                    return { ok: true };
                });
            },
            function () {
                return ensureAssets(win, session);
            },
            function () {
                if (root.ACP_DSH_CREATE_RUNTIME) {
                    if (!root.ACPDSHScope) return { ok: false, code: "scope_unavailable" };
                    session.scope = root.ACPDSHScope.create(root, session.container);
                    win = session.scope.window;
                    session.runtime = win;
                    root.ACP_DSH_CREATE_RUNTIME(win);
                }
                return installRuntime(win, session);
            },
            function () {
                return mountShell(win, session);
            },
            function () {
                return bootShell(win, session);
            },
            function () {
                return attachBridge(win, session);
            },
            function () {
                _state.booted = session.booted === true;
                return {
                    ok: true,
                    degraded: session.degraded === true,
                    readOnly: session.readOnly === true,
                    sessionId: session.sessionId,
                    namespaces: copyList(session.namespaces),
                    booted: session.booted === true,
                    bridgeError: session.bridgeError,
                    generation: session.generation
                };
            }
        ];

        stages = stages.map(function (stage) {
            return function () {
                if (session.cancelled || _session !== session) return { ok: false, code: "mount_cancelled" };
                return stage();
            };
        });
        return sequence(stages).then(function (result) {
            if (session.cancelled || _session !== session) return { ok: false, code: "mount_cancelled" };
            if (result && result.ok === false) {
                return abort(session, result);
            }
            return result;
        }, function (err) {
            return abort(session, { code: "mount_failed", message: textOf(err) });
        });
    }

    /* ---------------------------------------------------------------- API */

    function mount(container, options) {
        var win = root;
        var config;
        var readOnly;
        var sessionId;
        var session;
        var hostCode;
        var field;
        var message;

        options = options || {};

        if (!container || typeof container.appendChild !== "function") {
            return {
                ok: false,
                code: "invalid_container",
                message: t(win, "invalidContainer")
            };
        }

        if (_state.mounted && _session) {
            message = t(win, "alreadyMounted");
            showMessage(win, container, message, { "data-acp-dsh-error": "already_mounted" });
            return { ok: false, code: "already_mounted", message: message };
        }

        config = win.__ACP_PANELS_CONFIG__;
        if (!config || config.ok === false) {
            hostCode = config && config.code ? config.code : "missing";
            field = config && config.field ? config.field : "";
            message = t(win, "hostConfigUnavailable");
            showMessage(win, container, message + (field ? " (" + field + ")" : ""), {
                "data-acp-dsh-error": "host_config_unavailable",
                "data-acp-dsh-host-code": hostCode,
                "data-acp-dsh-host-field": field
            });
            return {
                ok: false,
                code: "host_config_unavailable",
                hostCode: hostCode,
                field: field,
                message: message
            };
        }

        if (typeof options.readOnly === "boolean") {
            readOnly = config.readOnly !== false || options.readOnly;
        } else {
            readOnly = config.readOnly !== false;
        }

        sessionId = resolveSessionId(win, options);

        session = {
            container: container,
            host: null,
            banner: null,
            bridge: null,
            bridgeError: null,
            styleLinks: [],
            client: null,
            originalClient: win.ACPClient,
            sessionId: sessionId,
            readOnly: readOnly,
            degraded: false,
            config: config,
            booted: false,
            namespaces: [],
            generation: ++GENERATION,
            apiPrefix: config.apiPrefix || "/acp-api",
            assetBase: config.assetBase || config.uiBase || "/acp",
            focusEndpoint: config.focusEndpoint || ((config.apiPrefix || "/acp-api") + "/ui/focus")
        };

        _session = session;
        _state = {
            mounted: true,
            readOnly: readOnly,
            degraded: false,
            sessionId: sessionId,
            namespaces: [],
            booted: false
        };

        return runMount(session);
    }

    function unmount(container) {
        var session = _session;
        var disposed;
        var i;
        for (i = _retained.length - 1; i >= 0; i--) {
            if (!container || _retained[i].container === container) {
                disposeSession(root, _retained[i]);
                _retained.splice(i, 1);
            }
        }
        if (!session || (container && container !== session.container)) {
            return { ok: true, disposed: [] };
        }
        disposed = disposeSession(root, session);
        resetState(session);
        return { ok: true, disposed: disposed };
    }

    function modulesVisible(session, visible) {
        var shell = (session.runtime || root).ACPShell;
        if (!shell) return;
        var layout = shell.layout;
        var current = layout && layout.getCurrentWorkspace ? layout.getCurrentWorkspace() : null;
        var panels = layout && layout.listPanels ? layout.listPanels() : [];
        panels.forEach(function (panel) {
            var instance = panel.instance;
            if (!instance || (visible && panel.workspaceId !== current)) return;
            var method = visible ? "resume" : "suspend";
            if (typeof instance[method] === "function") instance[method]();
            if (visible && typeof instance.resize === "function") instance.resize();
        });
        if (shell.globalRegions && shell.globalRegions.setActive) shell.globalRegions.setActive(visible);
    }

    function suspend(container) {
        var session = _session;
        if (!session || session.container !== container) return { ok: true };
        /* Incomplete mounts have no stable view state to retain. */
        if (!session.booted || !session.scope) return unmount(container);
        session.activation = (session.activation || 0) + 1;
        modulesVisible(session, false);
        if (session.bridge && session.bridge.detach) session.bridge.detach();
        session.bridge = null;
        session.scope.pause();
        _retained.push(session);
        resetState(session);
        return { ok: true, retained: true };
    }

    function resume(container) {
        if (_session && _session.container === container) return { ok: true };
        if (_session) return { ok: false, code: "already_mounted" };
        var index = -1;
        for (var i = 0; i < _retained.length; i++) if (_retained[i].container === container) index = i;
        if (index < 0) return { ok: false, code: "not_retained" };
        var session = _retained.splice(index, 1)[0];
        _session = session;
        session.activation = (session.activation || 0) + 1;
        var activation = session.activation;
        _state = { mounted: true, readOnly: session.readOnly, degraded: session.degraded,
            sessionId: session.sessionId, namespaces: session.namespaces, booted: session.booted };
        session.scope.resume();
        return Promise.resolve(attachBridge(session.runtime, session)).then(function () {
            if (_session !== session || activation !== session.activation) return { ok: false, code: "mount_cancelled" };
            modulesVisible(session, true);
            return { ok: true, sessionId: session.sessionId, retained: true, generation: session.generation };
        });
    }

    /*
     * Link the live instance to another DSH conversation without rebuilding
     * the shell, the canvas, the Viewer or the mounted DOM. Only the identity
     * used by the focus bridge / research references changes: the old bridge
     * is released, a new generation is negotiated (CAS acquire) and state()
     * reports the new id. A missing or non-string id is refused.
     */
    function reassociate(sessionId) {
        var session = _session;
        var win;
        var previous;
        var activation;
        var pending;
        if (!session) {
            return { ok: false, code: "not_mounted" };
        }
        if (typeof sessionId !== "string" || !sessionId) {
            return { ok: false, code: "bad_session_id" };
        }
        if (session.sessionId === sessionId) {
            return { ok: true, changed: false, sessionId: sessionId };
        }
        previous = session.sessionId;
        activation = (session.activation || 0) + 1;
        session.activation = activation;
        session.sessionId = sessionId;
        /* The previous conversation's display snapshot must not be replayed
         * onto the newly linked conversation. */
        session.restoreSnapshot = null;
        session.bridgeError = null;
        win = session.runtime || root;
        if (win && win.ACP_ENV) {
            win.ACP_ENV.sessionId = sessionId;
        }
        if (root.ACP_ENV && root.ACP_ENV !== (win && win.ACP_ENV)) {
            root.ACP_ENV.sessionId = sessionId;
        }
        _state.sessionId = sessionId;
        if (session.bridge && typeof session.bridge.detach === "function") {
            try {
                session.bridge.detach();
            } catch (err) {
                /* the focus bridge must never break reassociation */
            }
        }
        session.bridge = null;
        try {
            pending = attachBridge(win, session);
        } catch (err2) {
            pending = { ok: true };
        }
        return Promise.resolve(pending).then(function () {
            if (_session !== session || activation !== (session.activation || 0)) {
                return { ok: false, code: "mount_cancelled", sessionId: previous };
            }
            return {
                ok: true,
                changed: true,
                sessionId: sessionId,
                previousSessionId: previous,
                generation: session.generation
            };
        });
    }

    function state() {
        return {
            mounted: _state.mounted === true,
            readOnly: _state.readOnly === true,
            degraded: _state.degraded === true,
            sessionId: _state.sessionId,
            namespaces: copyList(_state.namespaces),
            booted: _state.booted === true
        };
    }

    root.ACPDSH = {
        mount: mount,
        unmount: unmount,
        suspend: suspend,
        resume: resume,
        reassociate: reassociate,
        state: state,
        version: VERSION
    };
}(typeof window !== "undefined" ? window : this));
