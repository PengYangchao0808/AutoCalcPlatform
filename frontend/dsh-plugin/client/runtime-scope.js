/* Native DOM embedding, with private ACP globals and owned browser resources.
 * This is lifecycle isolation, not a security boundary. No eval or iframe.
 */
window.ACPDSHScope = {
    create: function (host, container) {
        "use strict";
        var cleanups = [];
        var listeners = [];
        var timers = new Map();
        var requests = new Set();
        var frames = new Set();
        var active = true;
        var paused = false;
        var timerSequence = 0;
        var own = Object.create(null);
        var doc = host.document;

        function listen(target, type, callback, options) {
            var guarded = function (event) { if (active && !paused) callback.call(this, event); };
            target.addEventListener(type, guarded, options);
            listeners.push({ target: target, type: type, callback: callback, guarded: guarded, options: options });
            cleanups.push(function () { target.removeEventListener(type, guarded, options); });
        }
        function unlisten(target, type, callback, options) {
            listeners = listeners.filter(function (record) {
                if (record.target !== target || record.type !== type || record.callback !== callback) return true;
                target.removeEventListener(type, record.guarded, options);
                return false;
            });
        }
        function schedule(id, record) {
            record.due = Date.now() + record.remaining;
            record.native = host.setTimeout(function () {
                record.native = null;
                if (!active || paused) return;
                if (!record.repeat) timers.delete(id);
                try { record.callback.apply(null, record.args); }
                finally {
                    if (record.repeat && timers.has(id) && active && !paused) {
                        record.remaining = record.delay;
                        schedule(id, record);
                    }
                }
            }, record.remaining);
        }
        function timer(repeat, callback, delay) {
            if (!active) return null;
            var args = Array.prototype.slice.call(arguments, 3);
            var id = ++timerSequence;
            var record = { repeat: repeat, callback: callback, args: args, delay: Number(delay) || 0, remaining: Number(delay) || 0, native: null };
            timers.set(id, record);
            if (!paused) schedule(id, record);
            return id;
        }
        function clear(id) {
            if (!timers.has(id)) return;
            host.clearTimeout(timers.get(id).native);
            timers.delete(id);
        }
        var document = new Proxy({}, {
            get: function (_, key) {
                if (key === "addEventListener") return function (type, cb, opts) { listen(doc, type, cb, opts); };
                if (key === "removeEventListener") return function (type, cb, opts) { unlisten(doc, type, cb, opts); };
                if (key === "getElementById") return function (id) {
                    return container.querySelector("[id=" + JSON.stringify(String(id)) + "]");
                };
                if (key === "querySelector" || key === "querySelectorAll") return container[key].bind(container);
                var value = doc[key];
                return typeof value === "function" ? value.bind(doc) : value;
            }
        });
        own.document = document;
        own.setTimeout = timer.bind(null, false);
        own.setInterval = timer.bind(null, true);
        own.clearTimeout = clear;
        own.clearInterval = clear;
        own.addEventListener = function (type, cb, opts) { listen(host, type, cb, opts); };
        own.removeEventListener = function (type, cb, opts) { unlisten(host, type, cb, opts); };
        own.fetch = function (input, init) {
            if (!active || paused) return Promise.reject(new Error("ACP mount inactive"));
            var controller = new host.AbortController();
            var signal = init && init.signal;
            var abort = function () { controller.abort(); };
            if (signal) {
                if (signal.aborted) abort();
                else signal.addEventListener("abort", abort, { once: true });
            }
            requests.add(controller);
            return host.fetch(input, Object.assign({}, init, { signal: controller.signal })).finally(function () {
                requests.delete(controller);
                if (signal) signal.removeEventListener("abort", abort);
            });
        };
        own.EventSource = host.EventSource;
        own.ACP_DSH_SKELETON = host.ACP_DSH_SKELETON;
        own.requestAnimationFrame = function (callback) {
            if (!active || paused) return null;
            var id = host.requestAnimationFrame(function (time) {
                frames.delete(id);
                if (active && !paused) callback(time);
            });
            frames.add(id); return id;
        };
        own.cancelAnimationFrame = function (id) { frames.delete(id); host.cancelAnimationFrame(id); };
        var scope = new Proxy(own, {
            get: function (target, key) {
                if (Object.prototype.hasOwnProperty.call(target, key)) return target[key];
                // ACP namespaces must never resolve to another mount's state.
                if (typeof key === "string" && /^(ACP|_.*PollTimer)/.test(key)) return undefined;
                var value = host[key];
                return typeof value === "function" && /^(getComputedStyle|getSelection|matchMedia|requestAnimationFrame|cancelAnimationFrame)$/.test(key)
                    ? value.bind(host) : value;
            },
            set: function (target, key, value) { target[key] = value; return true; }
        });
        own.window = scope;
        own.self = scope;
        return {
            window: scope,
            pause: function () {
                if (!active || paused) return;
                paused = true;
                timers.forEach(function (record) {
                    if (record.native !== null) {
                        host.clearTimeout(record.native);
                        record.native = null;
                        record.remaining = Math.max(0, record.due - Date.now());
                    }
                });
                requests.forEach(function (controller) { controller.abort(); });
                frames.forEach(function (id) { host.cancelAnimationFrame(id); });
                frames.clear();
            },
            resume: function () {
                if (!active || !paused) return;
                paused = false;
                timers.forEach(function (record, id) { schedule(id, record); });
            },
            dispose: function () {
                active = false;
                timers.forEach(function (_, id) { clear(id); });
                requests.forEach(function (controller) { controller.abort(); });
                frames.forEach(function (id) { host.cancelAnimationFrame(id); });
                frames.clear();
                cleanups.splice(0).reverse().forEach(function (fn) { fn(); });
            }
        };
    }
};
