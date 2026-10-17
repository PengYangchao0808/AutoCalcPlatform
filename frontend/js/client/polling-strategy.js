/*
 * ACP Client — polling strategy orchestrator (4 lanes with generation guards).
 *
 * Owner: frontend/js/client/** (OpenCode).
 *
 * Solves:
 *   - Lane 1: Jobs list (4s active / 15s idle, 60s background).
 *   - Lane 2: Selected job lightweight summary (2.5s active, 30s background) + 5s energy/structure.
 *   - Lane 3: Log tail polling (5s foreground).
 *   - Lane 4: Slow lane (30s foreground: disk, nodes/backends, file tree).
 *   - Generation guard on every tick ensures stopping or restarting immediately disarms old timers.
 */
window.ACPClient = window.ACPClient || {};
ACPClient.polling = (function () {
  "use strict";

  var pollGeneration = 0;
  var timers = {
    jobs: null,
    summary: null,
    log: null,
    slow: null,
  };
  var handlers = null;
  var context = null;

  function isHidden() {
    if (context && typeof context.isHidden === "function") return context.isHidden();
    return typeof document !== "undefined" && !!document.hidden;
  }

  // Test hook ONLY: context.delays overrides the fixed lane cadences
  // (§42.4 red line — production defaults below must stay 4s/15s/60s,
  // 2.5s/30s, 5s, 30s, 15s; changing real frequencies needs measurement
  // evidence). Absent overrides resolve to the production constants.
  function delayFor(name, fallback) {
    if (context && context.delays && context.delays[name] != null) return context.delays[name];
    return fallback;
  }

  function clearAllTimers() {
    if (timers.jobs) {
      clearTimeout(timers.jobs);
      timers.jobs = null;
    }
    if (timers.summary) {
      clearTimeout(timers.summary);
      timers.summary = null;
    }
    if (timers.log) {
      clearTimeout(timers.log);
      timers.log = null;
    }
    if (timers.slow) {
      clearTimeout(timers.slow);
      timers.slow = null;
    }
    // Also clear legacy timer names on window if present
    if (typeof window !== "undefined") {
      if (window._jobsPollTimer) {
        clearTimeout(window._jobsPollTimer);
        window._jobsPollTimer = null;
      }
      if (window._midPollTimer) {
        clearTimeout(window._midPollTimer);
        window._midPollTimer = null;
      }
      if (window._logPollTimer) {
        clearTimeout(window._logPollTimer);
        window._logPollTimer = null;
      }
      if (window._slowPollTimer) {
        clearTimeout(window._slowPollTimer);
        window._slowPollTimer = null;
      }
    }
  }

  function init(injectedHandlers, injectedContext) {
    clearAllTimers();
    pollGeneration++;
    handlers = injectedHandlers || {};
    context = injectedContext || {};
  }

  function stop() {
    clearAllTimers();
    pollGeneration++;
  }

  function isActive() {
    return !!(timers.jobs || timers.summary || timers.log || timers.slow);
  }

  function getGeneration() {
    return pollGeneration;
  }

  function start(skipInitialJobs) {
    if (!handlers) {
      throw new Error("ACPClient.polling: init(handlers, context) required before start");
    }
    clearAllTimers();
    var generation = ++pollGeneration;

    // --- Lane 1: Jobs list ---
    var jobsInFlight = false;
    async function pollJobsLane() {
      if (generation !== pollGeneration || jobsInFlight) return;
      jobsInFlight = true;
      try {
        if (handlers.pollJobsList) await handlers.pollJobsList();
      } catch (e) {
        console.error("Jobs poll error:", e);
      } finally {
        jobsInFlight = false;
        if (generation !== pollGeneration) return;
        var hasActive = context.hasActiveJobs ? context.hasActiveJobs() : false;
        var delay = isHidden()
          ? delayFor("jobsHidden", 60000)
          : hasActive
            ? delayFor("jobsActive", 4000)
            : delayFor("jobsIdle", 15000);
        timers.jobs = setTimeout(pollJobsLane, delay);
        if (typeof window !== "undefined") window._jobsPollTimer = timers.jobs;
      }
    }

    // --- Lane 2: Selected job summary + energy/mech/structure ---
    var summaryTick = 0;
    async function pollSummaryLane() {
      if (generation !== pollGeneration) return;
      var tick = ++summaryTick;
      try {
        if (!isHidden()) {
          var selectedId = context.getSelectedJobId ? context.getSelectedJobId() : null;
          var drawerOpen = context.isDetailDrawerOpen ? context.isDetailDrawerOpen() : false;

          if (selectedId && !drawerOpen && handlers.pollSelectedSummary) {
            await handlers.pollSelectedSummary(selectedId);
          }

          // Every 2nd tick (5s)
          if (tick % 2 === 0) {
            var activeTab = context.getActiveViewerTab ? context.getActiveViewerTab() : "";
            var lastJob = context.getLastSelectedJob ? context.getLastSelectedJob() : null;
            if (activeTab === "energy" && handlers.pollEnergyGraph) {
              await handlers.pollEnergyGraph(lastJob);
            }
            var section = context.getAppSection ? context.getAppSection() : "";
            if (section === "mechanism" && handlers.pollMechanismSection) {
              await handlers.pollMechanismSection();
            }
            if (handlers.pollStructureViewer) {
              handlers.pollStructureViewer();
            }
          }
        }
      } catch (e) {
        console.error("Summary poll error:", e);
      } finally {
        if (generation === pollGeneration) {
          var delay = isHidden() ? delayFor("summaryHidden", 30000) : delayFor("summaryActive", 2500);
          timers.summary = setTimeout(pollSummaryLane, delay);
          if (typeof window !== "undefined") window._midPollTimer = timers.summary;
        }
      }
    }

    // --- Lane 3: Log tail ---
    var logInFlight = false;
    async function pollLogLane() {
      if (generation !== pollGeneration) return;
      var selectedId = context.getSelectedJobId ? context.getSelectedJobId() : null;
      if (isHidden() || !selectedId || logInFlight) {
        if (generation === pollGeneration) {
          timers.log = setTimeout(pollLogLane, delayFor("log", 5000));
          if (typeof window !== "undefined") window._logPollTimer = timers.log;
        }
        return;
      }
      logInFlight = true;
      try {
        if (handlers.pollLogTail) await handlers.pollLogTail(selectedId);
      } catch (e) {
      } finally {
        logInFlight = false;
        if (generation === pollGeneration) {
          timers.log = setTimeout(pollLogLane, delayFor("log", 5000));
          if (typeof window !== "undefined") window._logPollTimer = timers.log;
        }
      }
    }

    // --- Lane 4: Slow lane (disk, nodes, file tree) ---
    async function pollSlowLane() {
      if (generation !== pollGeneration) return;
      try {
        if (!isHidden()) {
          if (handlers.pollDiskUsage) await handlers.pollDiskUsage();
          var sysresOpen = context.isSysresDrawerOpen ? context.isSysresDrawerOpen() : false;
          if (sysresOpen) {
            if (handlers.pollNodes) await handlers.pollNodes();
            if (handlers.pollBackends) await handlers.pollBackends();
          }
          if (handlers.pollFileTree) await handlers.pollFileTree();
        }
      } catch (e) {
        console.error("Slow poll error:", e);
      } finally {
        if (generation === pollGeneration) {
          timers.slow = setTimeout(pollSlowLane, delayFor("slow", 30000));
          if (typeof window !== "undefined") window._slowPollTimer = timers.slow;
        }
      }
    }

    if (skipInitialJobs) {
      timers.jobs = setTimeout(pollJobsLane, delayFor("jobsInitial", 15000));
      if (typeof window !== "undefined") window._jobsPollTimer = timers.jobs;
    } else {
      pollJobsLane();
    }
    pollSummaryLane();
    pollLogLane();
    pollSlowLane();
  }

  return {
    init: init,
    start: start,
    stop: stop,
    isActive: isActive,
    getGeneration: getGeneration,
  };
})();
