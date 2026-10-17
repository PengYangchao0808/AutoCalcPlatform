/*
 * ACP Client — job event stream (SSE lifecycle).
 *
 * Owner: frontend/js/client/** (OpenCode).
 *
 * Solves:
 *   - Explicit connection generation guards against stale reconnects and cross-task event leakage.
 *   - Closes old EventSource instance cleanly before opening a new one.
 *   - Handles reconnect backoff (3s) and terminal event transitions to 'closed'.
 *   - Subscribes to all server event types and notifies via injected callbacks.
 *   - Stream URL base is injected once via init({ baseUrl }) from the page
 *     (compat/api.js owns API_BASE) — the client module never hardcodes a prefix.
 *
 * URL contract (mirrors the retired page-inline openSSE fallback verbatim):
 *   first connect  : {base}/jobs/{enc(jid)}/events?history=200
 *   resume (seq>0) : {base}/jobs/{enc(jid)}/events?after_seq={seq}
 * after_seq REPLACES history — the cursor only skips already-seen events, it is
 * not a persistent replay (§42.4).
 *
 * State machine (mirrors the retired page-inline openSSE):
 *   open            -> "connecting"   (emitted synchronously; legacy initial state)
 *   onopen / event  -> "connected"
 *   onerror         -> "reconnecting" (auto-reconnect after backoff; no-op when closed)
 *   terminal / close -> "closed"      (never reconnects afterwards)
 */
window.ACPClient = window.ACPClient || {};
ACPClient.events = (function () {
  "use strict";

  var injectedBaseUrl = "";
  var reconnectDelayMs = 3000;
  var activeSSE = null;
  var reconnectTimer = null;
  var sseState = "closed";
  var connectionSerial = 0;
  var lastSeq = 0;
  var currentJobId = null;

  var EVENT_TYPES = [
    "job.created",
    "job.started",
    "job.status",
    "stage.started",
    "stage.progress",
    "stage.completed",
    "stage.failed",
    "log.stdout",
    "log.stderr",
    "log",
    "job.completed",
    "job.failed",
    'job.cancelled',
    "job.paused",
    "job.resumed",
    "done",
    "error",
  ];

  function init(opts) {
    opts = opts || {};
    if (opts.baseUrl != null) injectedBaseUrl = String(opts.baseUrl);
    if (opts.reconnectDelayMs != null) reconnectDelayMs = Number(opts.reconnectDelayMs) || 0;
  }

  function resolveBaseUrl(opts) {
    // init({ baseUrl }) is the page-level injection and wins over per-call opts.
    if (injectedBaseUrl) return injectedBaseUrl;
    if (opts && opts.baseUrl != null) return String(opts.baseUrl);
    return "";
  }

  function buildStreamUrl(baseUrl, jid, seq) {
    var url = baseUrl + "/jobs/" + encodeURIComponent(jid) + "/events";
    if (seq > 0) url += "?after_seq=" + seq;
    else url += "?history=200";
    return url;
  }

  function getState() {
    return sseState;
  }

  function getCurrentJobId() {
    return currentJobId;
  }

  function clearReconnectTimer() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function closeStream() {
    connectionSerial++;
    clearReconnectTimer();
    if (activeSSE) {
      try {
        activeSSE.close();
      } catch (e) {}
      activeSSE = null;
    }
    sseState = "closed";
    currentJobId = null;
  }

  function openJobStream(jobId, callbacks, opts) {
    opts = opts || {};
    callbacks = callbacks || {};
    // Caller-supplied subscription list (page keeps the literal for its
    // contract pin); defaults to the module's EVENT_TYPES.
    var subscribedTypes = (Array.isArray(opts.types) && opts.types.length)
      ? opts.types : EVENT_TYPES;
    var EventSourceClass = opts.EventSourceClass || window.EventSource;
    if (!EventSourceClass) {
      console.warn("EventSource is not supported in this environment");
      return { close: function () {} };
    }

    var jid = String(jobId || "");
    if (!jid) {
      closeStream();
      return { close: function () {} };
    }

    // Clean up any existing connection
    closeStream();

    currentJobId = jid;
    var connGeneration = ++connectionSerial;
    lastSeq = 0;

    function updateState(newState) {
      if (connectionSerial !== connGeneration) return;
      sseState = newState;
      if (callbacks.onStateChange) callbacks.onStateChange(newState);
    }

    function connect() {
      if (connectionSerial !== connGeneration) return;
      var url = buildStreamUrl(resolveBaseUrl(opts), jid, lastSeq);

      try {
        activeSSE = new EventSourceClass(url);
      } catch (e) {
        updateState("closed");
        if (callbacks.onError) callbacks.onError(e);
        return;
      }

      subscribedTypes.forEach(function (type) {
        activeSSE.addEventListener(type, function (event) {
          if (connectionSerial !== connGeneration) return;

          if (event.lastEventId) {
            var parsed = parseInt(event.lastEventId, 10);
            if (!isNaN(parsed)) lastSeq = parsed;
          }

          var payload = {};
          try {
            payload = event.data ? JSON.parse(event.data) : {};
          } catch (e) {
            payload = { message: event.data || "" };
          }

          if (type === "error") {
            if (callbacks.onLog) {
              callbacks.onLog("error", payload.message || "unknown error");
            }
            return;
          }

          var message = payload.message || payload.stage || payload.status || JSON.stringify(payload);
          if (callbacks.onLog) {
            callbacks.onLog(type, message);
          }

          // Terminal events: job finished, closed or cancelled
          if (type === "job.completed" || type === "job.failed" || type === 'job.cancelled' || type === "done") {
            if (callbacks.onTerminalEvent) {
              callbacks.onTerminalEvent(type, payload, jid);
            }
            updateState("closed");
            if (activeSSE) {
              activeSSE.close();
              activeSSE = null;
            }
            return;
          }

          updateState("connected");
        });
      });

      activeSSE.onopen = function () {
        if (connectionSerial !== connGeneration) return;
        updateState("connected");
      };

      activeSSE.onerror = function () {
        if (connectionSerial !== connGeneration) return;
        if (activeSSE) {
          try {
            activeSSE.close();
          } catch (e) {}
          activeSSE = null;
        }
        if (sseState === "closed") return; // terminal/close: never reconnect
        updateState("reconnecting");
        // Presentation (log line for the reconnect) belongs to the page via
        // onStateChange("reconnecting") — the Client does not fabricate text.
        clearReconnectTimer();
        reconnectTimer = setTimeout(function () {
          if (connectionSerial === connGeneration && sseState !== "closed") {
            connect();
          }
        }, reconnectDelayMs);
      };
    }

    // Initial state announced synchronously — identical to the retired
    // page-inline fallback whose indicator started at "connecting".
    updateState("connecting");
    connect();

    return {
      close: function () {
        if (connectionSerial === connGeneration) {
          closeStream();
        }
      },
      getJobId: function () {
        return jid;
      },
      getState: function () {
        return sseState;
      },
      getGeneration: function () {
        return connGeneration;
      },
    };
  }

  return {
    init: init,
    openJobStream: openJobStream,
    closeStream: closeStream,
    getState: getState,
    getCurrentJobId: getCurrentJobId,
    EVENT_TYPES: EVENT_TYPES,
  };
})();
