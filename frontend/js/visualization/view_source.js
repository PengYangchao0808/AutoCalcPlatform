/*
 * ACP 3.0 — data source adapter for `acp.view.v1` (Track A / A-00).
 *
 * Classic script, no build. The ONLY place that knows backend wire shapes:
 * panels ask for a view and receive a normalized envelope from view_model.js.
 *
 * Modes (plan §4):
 *   live    — existing v1/v2 endpoints, mapped per family today;
 *   fixture — deterministic fixtures under frontend/js/visualization/fixtures;
 *   v3      — reserved; create({mode:"v3"}) is explicit and load() rejects
 *             with E_NOT_IMPLEMENTED. Auto-selection (?view=v3 or storage)
 *             falls back to live with a console warning, never silently.
 *
 * Selection order: URL `?view=` > localStorage `acp.view.source` >
 * config.mode > "live". Fixture data is always marked `fixture:true` by the
 * model so the UI badge cannot be forgotten.
 */
window.ACPView = window.ACPView || {};
ACPView.source = (function () {
  "use strict";

  var MODES = ["live", "fixture", "v3"];
  var STORAGE_KEY = "acp.view.source";
  var DEFAULT_API_BASE = "/api/v1";

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function asString(value) {
    return typeof value === "string" ? value : "";
  }

  function warn(message) {
    if (typeof console !== "undefined" && console && typeof console.warn === "function") {
      console.warn("[acp.view.source] " + message);
    }
  }

  function modelApi() {
    var model = window.ACPView && window.ACPView.model;
    if (!model || typeof model.normalize !== "function") {
      throw new Error("ACPView.model 未加载（检查 view_model.js 脚本顺序）");
    }
    return model;
  }

  function resolveFetch(config) {
    if (config && typeof config.fetch === "function") {
      var configured = config.fetch;
      return function (url, opts) { return configured.call(config, url, opts); };
    }
    if (typeof window !== "undefined" && typeof window.fetch === "function") {
      var globalFetch = window.fetch;
      return function (url, opts) { return globalFetch.call(window, url, opts); };
    }
    return null;
  }

  function defaultBaseUrl(config) {
    if (config && typeof config.baseUrl === "string" && config.baseUrl) return config.baseUrl;
    if (typeof window !== "undefined" && window.API_BASE) return String(window.API_BASE);
    return DEFAULT_API_BASE;
  }

  function encodeFilePath(path) {
    return String(path).split("/").map(encodeURIComponent).join("/");
  }

  function buildStructureUrl(req, baseUrl) {
    var base = baseUrl || DEFAULT_API_BASE;
    var jobId = encodeURIComponent(req.jobId);
    if (req.kind === "structure-entry") {
      return base + "/jobs/" + jobId + "/structure-viewer/entries/" +
        encodeURIComponent(req.entryId) + "/geometry";
    }
    var encoded = encodeFilePath(req.path);
    if (req.kind === "job-file") {
      return base + "/jobs/" + jobId + "/files/" + encoded;
    }
    return base + "/jobs/" + jobId + "/remote-files/" + encoded + "/preview?mode=structure";
  }

  function buildOverlayUrl(jobId, entryA, entryB, baseUrl) {
    var base = baseUrl || DEFAULT_API_BASE;
    return base + "/jobs/" + encodeURIComponent(jobId) +
      "/structure-viewer/overlay?entry_a=" + encodeURIComponent(entryA) +
      "&entry_b=" + encodeURIComponent(entryB);
  }

  function unwrapRemoteContent(body) {
    if (body == null) throw new Error("空响应体");
    if (typeof body.content === "string") return body.content;
    throw new Error("响应缺少 content 字段");
  }

  /** Accept a fetch Response, a pre-parsed api()-style body, or raw text. */
  function readResponse(resp, kind) {
    if (resp == null) throw new Error("空响应");
    if (typeof resp === "string") return Promise.resolve(resp);
    if (isObject(resp) && resp.ok === false) {
      throw new Error("HTTP " + (resp.status || "") + (resp.statusText ? " " + resp.statusText : ""));
    }
    if (kind === "job-remote-file") {
      if (typeof resp.json === "function") return Promise.resolve(resp.json()).then(unwrapRemoteContent);
      return Promise.resolve(unwrapRemoteContent(resp));
    }
    if (typeof resp.text === "function") return Promise.resolve(resp.text());
    if (typeof resp.content === "string") return Promise.resolve(resp.content);
    throw new Error("不支持的响应形状");
  }

  function readJson(resp) {
    if (resp == null) throw new Error("空响应");
    if (typeof resp === "string") return Promise.resolve(JSON.parse(resp));
    if (isObject(resp) && resp.ok === false) {
      throw new Error("HTTP " + (resp.status || "") + (resp.statusText ? " " + resp.statusText : ""));
    }
    if (typeof resp.json === "function") return Promise.resolve(resp.json());
    if (typeof resp.text === "function") {
      return Promise.resolve(resp.text()).then(function (text) {
        return JSON.parse(text);
      });
    }
    if (isObject(resp)) return Promise.resolve(resp);
    throw new Error("不支持的响应形状");
  }

  function modeFromSearch(search) {
    var match = String(search || "").match(/[?&]view=([^&]+)/);
    if (!match) return null;
    try {
      return decodeURIComponent(match[1]);
    } catch (e) {
      return match[1];
    }
  }

  function modeFromStorage(storage) {
    try {
      if (!storage || typeof storage.getItem !== "function") return null;
      var value = storage.getItem(STORAGE_KEY);
      return typeof value === "string" && value ? value : null;
    } catch (e) {
      return null;
    }
  }

  /**
   * Resolve the effective mode for UI selection. Explicit v3 (or an unknown
   * value) falls back to live WITH a console warning — never silently.
   */
  function resolveMode(config) {
    config = config || {};
    var location = config.location || (typeof window !== "undefined" ? window.location : null);
    var storage = config.storage || (typeof window !== "undefined" ? window.localStorage : null);
    var requested = modeFromSearch(location && location.search) ||
      modeFromStorage(storage) ||
      config.mode ||
      "live";
    if (requested === "v3") {
      warn("v3 adapter is not implemented; falling back to live");
      return "live";
    }
    if (MODES.indexOf(requested) < 0) {
      warn("unknown view source '" + requested + "'; falling back to live");
      return "live";
    }
    return requested;
  }

  function hasFixtures(fixtures) {
    return isObject(fixtures) && Object.keys(fixtures).length > 0;
  }

  function structureView(req, text, meta) {
    var model = modelApi();
    var frames = model.parseXyzFrames(text);
    if (!frames.length) {
      if (!String(text || "").trim()) return model.emptyView(meta);
      return model.errorView(meta, "E_XYZ_PARSE", "结构解析失败（无有效 XYZ 帧）");
    }
    var key = req.storeEntryId || req.entryId || model.structureRefKey(req);
    var objects = {};
    objects[key] = { format: "xyz", text: String(text) };
    var rawFrames = frames.map(function (frame) {
      return {
        index: frame.index,
        label: "Frame " + (frame.index + 1),
        energy: frame.energy,
        geometryRef: key
      };
    });
    return model.normalize({
      availability: "ready",
      provenance: { jobId: req.jobId, entryId: key },
      objects: objects,
      frames: rawFrames
    }, meta);
  }

  function loadStructure(ref, meta, ctx) {
    var model = modelApi();
    var normalized = model.normalizeStructureRef(ref.structureRef || ref);
    if (!normalized) return Promise.resolve(model.emptyView(meta));
    if (normalized.kind === "invalid") {
      return Promise.resolve(model.errorView(meta, "E_STRUCT_REF", normalized.reason));
    }
    if (normalized.kind === "inline-xyz") {
      return Promise.resolve(structureView(normalized, normalized.text, meta));
    }
    if (!ctx.fetchFn) {
      return Promise.resolve(model.errorView(meta, "E_NO_FETCH", "无可用 fetch（注入 deps.fetch 或 window.fetch）"));
    }
    var url = buildStructureUrl(normalized, ctx.baseUrl);
    return Promise.resolve()
      .then(function () { return ctx.fetchFn(url, {}); })
      .then(function (resp) { return readResponse(resp, normalized.kind); })
      .then(function (text) { return structureView(normalized, text, meta); })
      .catch(function (error) {
        return model.errorView(meta, "E_FETCH", error && error.message ? error.message : String(error));
      });
  }

  function mapEnergyGraph(raw, meta) {
    var model = modelApi();
    var nodes = Array.isArray(raw && raw.nodes) ? raw.nodes : [];
    var sourceSeries = Array.isArray(raw && raw.series) ? raw.series : [];
    if (!sourceSeries.length) {
      return model.normalize({
        availability: "empty",
        provenance: { jobId: raw && raw.job_id },
        warnings: [{ code: "W_NO_SERIES", message: "energy-graph 未提供 series，无法绘制曲线" }]
      }, meta);
    }

    var series = sourceSeries.map(function (item, i) {
      var values = Array.isArray(item && item.values) ? item.values : [];
      var xValues = Array.isArray(item && item.x_values) ? item.x_values : [];
      var points = values.map(function (value, pointIndex) {
        var node = nodes[pointIndex] || {};
        var x = isFiniteNumber(xValues[pointIndex]) ? xValues[pointIndex] :
          (isFiniteNumber(node.x) ? node.x :
            (isFiniteNumber(node.frame_index) ? node.frame_index : pointIndex));
        var point = {
          x: isFiniteNumber(x) ? x : null,
          y: isFiniteNumber(value) ? value : null
        };
        if (isFiniteNumber(node.frame_index)) point.frameIndex = Math.floor(node.frame_index);
        if (asString(node.id)) point.nodeId = asString(node.id);
        return point;
      });
      return {
        id: asString(item && item.id) || ("series-" + i),
        label: asString(item && item.label) || asString(item && item.id) || ("series-" + i),
        unit: asString(item && item.unit),
        points: points
      };
    });

    var frames = nodes.map(function (node, i) {
      var frame = {
        index: isFiniteNumber(node.frame_index) ? Math.floor(node.frame_index) : i,
        label: asString(node.label) || asString(node.type) || ("Frame " + (i + 1)),
        energy: isFiniteNumber(node.energy) ? node.energy : null,
        geometryRef: asString(node.geometry_ref)
      };
      if (asString(node.status)) frame.status = asString(node.status);
      return frame;
    });

    var annotations = (Array.isArray(raw.annotations) ? raw.annotations : []).map(function (item, i) {
      var payloadKeys = [
        "frame_index", "geometry_ref", "candidate_id", "status", "selected",
        "active", "saved", "recommended_type", "selection_source", "confidence", "reason"
      ];
      var payload = {};
      payloadKeys.forEach(function (key) {
        if (item && item[key] !== undefined) payload[key] = item[key];
      });
      return {
        id: asString(item && item.id) || ("annotation-" + i),
        label: asString(item && item.label),
        kind: asString(item && item.type) || asString(item && item.kind),
        x: isFiniteNumber(item && item.x) ? item.x : null,
        y: isFiniteNumber(item && item.y) ? item.y : null,
        payload: payload
      };
    });

    var xAxis = isObject(raw.x_axis) ? raw.x_axis : {};
    var first = series[0] || {};
    var provenance = isObject(raw.provenance) ? Object.assign({}, raw.provenance) : {};
    if (raw.job_id || (meta && meta.jobId)) provenance.jobId = raw.job_id || meta.jobId;
    provenance.viewType = asString(raw.view_type) || provenance.viewType || "energy";
    provenance.xAxisMode = asString(xAxis.default_mode) ||
      asString(raw.metadata && raw.metadata.default_x_axis_mode) ||
      provenance.xAxisMode ||
      "scanned_coordinate";
    if (asString(raw.default_series)) provenance.defaultSeries = asString(raw.default_series);
    var itemId = asString(raw.item_id) || asString(raw.metadata && raw.metadata.item_id);
    if (itemId) provenance.itemId = itemId;
    var ensemble = isObject(raw && raw.ensemble_thermo) ? raw.ensemble_thermo : null;
    var thermo = ensemble ? {
      totalGibbsKcalMol: ensemble.total_gibbs_kcal_mol,
      totalGibbsHartree: ensemble.total_gibbs_hartree,
      rank1Weight: ensemble.rank1_weight,
      tSMixKcalPerMol: ensemble.t_s_mix_kcal_per_mol,
      populationCoverage: ensemble.population_coverage,
      temperatureK: ensemble.temperature_k
    } : null;

    return model.normalize({
      availability: series.some(function (item) {
        return item.points.some(function (point) { return point.x !== null && point.y !== null; });
      }) ? "ready" : "empty",
      provenance: provenance,
      axes: {
        x: { label: asString(xAxis.label) || "帧", unit: asString(xAxis.unit) },
        y: { label: first.label || "能量", unit: first.unit || "" }
      },
      series: series,
      frames: frames,
      annotations: annotations,
      thermo: thermo
    }, meta);
  }

  function loadEnergy(ref, meta, ctx) {
    var model = modelApi();
    var jobId = asString(ref.jobId) || asString(ref.taskRef && ref.taskRef.jobId);
    if (!jobId) {
      return Promise.resolve(model.errorView(meta, "E_REF", "energy 视图需要 jobId"));
    }
    if (!ctx.fetchFn) {
      return Promise.resolve(model.errorView(meta, "E_NO_FETCH", "无可用 fetch（注入 deps.fetch 或 window.fetch）"));
    }
    var url = ctx.baseUrl + "/jobs/" + encodeURIComponent(jobId) + "/energy-graph";
    return Promise.resolve()
      .then(function () { return ctx.fetchFn(url, {}); })
      .then(readJson)
      .then(function (payload) {
        return mapEnergyGraph(payload, {
          family: meta.family,
          source: meta.source,
          viewId: meta.viewId,
          jobId: jobId
        });
      })
      .catch(function (error) {
        return model.errorView(meta, "E_FETCH", error && error.message ? error.message : String(error));
      });
  }

  function mapStructureCatalog(raw, meta) {
    var model = modelApi();
    var sourceGroups = Array.isArray(raw && raw.groups) ? raw.groups : [];
    var sourceEntries = Array.isArray(raw && raw.entries) ? raw.entries : [];
    var warnings = [];
    var availability = "ready";

    if (raw && raw.availability === "pending_fetch") {
      availability = "empty";
      warnings.push({ code: "W_PENDING_FETCH", message: "远程任务的结构目录尚未取回" });
    } else if (raw && raw.availability === "unavailable") {
      availability = "error";
      var first = Array.isArray(raw.warnings) && raw.warnings.length ? String(raw.warnings[0]) : "";
      warnings.push({ code: "E_CATALOG_UNAVAILABLE", message: first || "结构目录不可用" });
    } else if (!sourceEntries.length) {
      availability = "empty";
    }

    var groups = sourceGroups.map(function (group) {
      return {
        id: asString(group && group.id),
        label: asString(group && group.label),
        kind: asString(group && group.kind)
      };
    });
    var entries = sourceEntries.map(function (entry) {
      var source = isObject(entry && entry.source) ? entry.source : {};
      var energy = isObject(entry && entry.energy) ? entry.energy : null;
      return {
        id: asString(entry && entry.id),
        groupId: asString(entry && entry.group_id),
        label: asString(entry && entry.label),
        role: asString(entry && entry.role),
        status: asString(entry && entry.status),
        energy: energy ? {
          value: energy.value,
          unit: energy.unit,
          kind: energy.kind,
          temperatureK: isFiniteNumber(energy.temperature_k) ? energy.temperature_k : null
        } : null,
        relativeEnergyKcal: isFiniteNumber(entry && entry.relative_energy_kcal) ? entry.relative_energy_kcal : null,
        boltzmannWeight: isFiniteNumber(entry && entry.boltzmann_weight) ? entry.boltzmann_weight : null,
        badges: Array.isArray(entry && entry.badges) ? entry.badges : [],
        geometryRef: asString(source.geometry_ref),
        frameIndex: isFiniteNumber(source.frame_index) ? Math.floor(source.frame_index) : null,
        sourceKind: asString(source.kind)
      };
    });

    (Array.isArray(raw && raw.warnings) ? raw.warnings : []).forEach(function (item) {
      var message = String(item || "").trim();
      if (!message || message === "pending_fetch") return;
      warnings.push({ code: "W_CATALOG", message: message });
    });

    var ensemble = isObject(raw && raw.ensemble_thermo) ? raw.ensemble_thermo : null;
    var thermo = ensemble ? {
      totalGibbsKcalMol: ensemble.total_gibbs_kcal_mol,
      totalGibbsHartree: ensemble.total_gibbs_hartree,
      rank1Weight: ensemble.rank1_weight,
      tSMixKcalPerMol: ensemble.t_s_mix_kcal_per_mol,
      populationCoverage: ensemble.population_coverage,
      temperatureK: ensemble.temperature_k
    } : null;

    return model.normalize({
      availability: availability,
      provenance: {
        jobId: raw && raw.job_id ? String(raw.job_id) : (meta && meta.jobId),
        revision: asString(raw && raw.revision),
        workflow: asString(raw && raw.workflow),
        jobStatus: asString(raw && raw.job_status)
      },
      groups: groups,
      entries: entries,
      thermo: thermo,
      warnings: warnings
    }, meta);
  }

  function loadCatalog(ref, meta, ctx) {
    var model = modelApi();
    var jobId = asString(ref.jobId) || asString(ref.taskRef && ref.taskRef.jobId);
    if (!jobId) {
      return Promise.resolve(model.errorView(meta, "E_REF", "结构目录需要 jobId"));
    }
    if (!ctx.fetchFn) {
      return Promise.resolve(model.errorView(meta, "E_NO_FETCH", "无可用 fetch（注入 deps.fetch 或 window.fetch）"));
    }
    var url = ctx.baseUrl + "/jobs/" + encodeURIComponent(jobId) + "/structure-viewer";
    return Promise.resolve()
      .then(function () { return ctx.fetchFn(url, {}); })
      .then(readJson)
      .then(function (payload) {
        return mapStructureCatalog(payload, {
          family: meta.family,
          source: meta.source,
          viewId: meta.viewId,
          jobId: jobId
        });
      })
      .catch(function (error) {
        return model.errorView(meta, "E_FETCH", error && error.message ? error.message : String(error));
      });
  }

  function mapOverlayPayload(data, entryA, entryB, xyzB, geometryError, meta) {
    var model = modelApi();
    if (!isObject(data)) return model.errorView(meta, "E_OVERLAY", "overlay 响应不是对象");
    var max = isObject(data.max_displacement) ? {
      i: isFiniteNumber(data.max_displacement.i) ? data.max_displacement.i : null,
      j: isFiniteNumber(data.max_displacement.j) ? data.max_displacement.j : null,
      distance: isFiniteNumber(data.max_displacement.distance) ? data.max_displacement.distance : null
    } : null;
    var objects = {};
    if (typeof xyzB === "string" && xyzB.trim()) {
      objects[entryB] = { format: "xyz", text: xyzB };
    }
    var warnings = [];
    if (geometryError) warnings.push({ code: "W_OVERLAY_GEOMETRY", message: "叠合几何加载失败: " + geometryError });
    return model.normalize({
      availability: "ready",
      provenance: { jobId: meta && meta.jobId },
      objects: objects,
      overlay: {
        ok: !!data.ok,
        entryA: entryA,
        entryB: entryB,
        mapping: Array.isArray(data.mapping) ? data.mapping : null,
        rmsd: isFiniteNumber(data.rmsd) ? data.rmsd : null,
        maxDisplacement: max,
        nMapped: isFiniteNumber(data.n_mapped) ? data.n_mapped : 0,
        reason: asString(data.reason) || "unproven"
      },
      warnings: warnings
    }, meta);
  }

  function loadOverlay(jobId, entryA, entryB, meta, ctx) {
    var model = modelApi();
    if (!jobId || !entryA || !entryB) {
      return Promise.resolve(model.errorView(meta, "E_OVERLAY_REF", "叠合需要 jobId / entryA / entryB"));
    }
    if (!ctx.fetchFn) {
      return Promise.resolve(model.errorView(meta, "E_NO_FETCH", "无可用 fetch（注入 deps.fetch 或 window.fetch）"));
    }
    var overlayUrl = buildOverlayUrl(jobId, entryA, entryB, ctx.baseUrl);
    var geometryUrl = buildStructureUrl({ kind: "structure-entry", jobId: jobId, entryId: entryB }, ctx.baseUrl);
    return Promise.resolve()
      .then(function () { return ctx.fetchFn(overlayUrl, {}); })
      .then(readJson)
      .then(function (payload) {
        return Promise.resolve()
          .then(function () { return ctx.fetchFn(geometryUrl, {}); })
          .then(function (resp) { return readResponse(resp, "structure-entry"); })
          .then(function (xyzB) { return mapOverlayPayload(payload, entryA, entryB, xyzB, null, meta); })
          .catch(function (error) {
            return mapOverlayPayload(payload, entryA, entryB, null,
              error && error.message ? error.message : String(error), meta);
          });
      })
      .catch(function (error) {
        return model.errorView(meta, "E_FETCH", error && error.message ? error.message : String(error));
      });
  }

  function buildVibrationsUrl(jobId, entryId, baseUrl) {
    var base = baseUrl || DEFAULT_API_BASE;
    return base + "/jobs/" + encodeURIComponent(jobId) +
      "/structure-viewer/entries/" + encodeURIComponent(entryId) + "/vibrations";
  }

  function mapVibrationPayload(payload, meta) {
    var model = modelApi();
    if (!isObject(payload)) return model.errorView(meta, "E_VIBRATION", "vibrations 响应不是对象");
    var modes = (Array.isArray(payload.modes) ? payload.modes : []).map(function (mode) {
      return {
        modeIndex: mode && mode.mode_index,
        frequencyCm1: mode && mode.frequency_cm1,
        imaginary: !!(mode && mode.imaginary),
        irIntensity: mode && mode.ir_intensity,
        vectors: mode && mode.vectors
      };
    });
    var available = payload.available === true;
    return model.normalize({
      availability: available && modes.length ? "ready" : "empty",
      provenance: { jobId: meta && meta.jobId },
      modes: modes,
      vibration: {
        available: available,
        reason: asString(payload.reason),
        atomCount: isFiniteNumber(payload.atom_count) ? payload.atom_count : 0,
        imaginaryCount: isFiniteNumber(payload.imaginary_count) ? payload.imaginary_count : null,
        source: asString(payload.source),
        geometryProductId: asString(payload.geometry_product_id) || null
      }
    }, meta);
  }

  function loadVibrations(jobId, entryId, meta, ctx) {
    var model = modelApi();
    if (!jobId || !entryId) {
      return Promise.resolve(model.errorView(meta, "E_VIBRATION_REF", "振动数据需要 jobId / entryId"));
    }
    if (!ctx.fetchFn) {
      return Promise.resolve(model.errorView(meta, "E_NO_FETCH", "无可用 fetch（注入 deps.fetch 或 window.fetch）"));
    }
    var url = buildVibrationsUrl(jobId, entryId, ctx.baseUrl);
    return Promise.resolve()
      .then(function () { return ctx.fetchFn(url, {}); })
      .then(readJson)
      .then(function (payload) { return mapVibrationPayload(payload, meta); })
      .catch(function (error) {
        return model.errorView(meta, "E_FETCH", error && error.message ? error.message : String(error));
      });
  }

  function loadLive(ref, meta, ctx) {
    if (meta.family === "structure") return loadStructure(ref, meta, ctx);
    if (meta.family === "energy") return loadEnergy(ref, meta, ctx);
    if (meta.family === "catalog") return loadCatalog(ref, meta, ctx);
    return Promise.resolve(modelApi().errorView(
      meta,
      "E_FAMILY_NOT_IMPLEMENTED",
      "live 适配器尚未接入视图族: " + meta.family
    ));
  }

  function fixtureLoad(ref, meta, ctx) {
    var model = modelApi();
    var fixture = ctx.fixtures[meta.family];
    if (fixture == null) {
      return Promise.resolve(model.errorView(meta, "E_FIXTURE_MISSING", "fixture 未注册: " + meta.family));
    }
    try {
      var raw = typeof fixture === "function" ? fixture(ref) : fixture;
      return Promise.resolve(model.normalize(raw, meta));
    } catch (error) {
      return Promise.resolve(model.errorView(meta, "E_FIXTURE_THROW", error && error.message ? error.message : String(error)));
    }
  }

  function v3Error() {
    var error = new Error("acp.view.v1 v3 适配器尚未实现（BE-GAP-01）");
    error.code = "E_NOT_IMPLEMENTED";
    error.acpView = { code: "E_NOT_IMPLEMENTED", mode: "v3" };
    return error;
  }

  function create(config) {
    config = config || {};
    var explicitMode = config.mode != null;
    var mode = explicitMode ? config.mode : resolveMode(config);
    var baseUrl;
    var fixtures;
    var fetchFn;

    if (mode === "v3") {
      return {
        mode: "v3",
        requestedMode: "v3",
        baseUrl: defaultBaseUrl(config),
        load: function () { return Promise.reject(v3Error()); },
        health: function () { return { mode: "v3", ok: false, reason: "not-implemented" }; }
      };
    }

    if (MODES.indexOf(mode) < 0) {
      warn("unknown view source '" + mode + "'; falling back to live");
      mode = "live";
    }
    baseUrl = defaultBaseUrl(config);
    fixtures = config.fixtures || (typeof window !== "undefined" ? window.ACPViewFixtures : null) || {};
    fetchFn = resolveFetch(config);
    var ctx = { baseUrl: baseUrl, fixtures: fixtures, fetchFn: fetchFn };

    var adapter = {
      mode: mode,
      requestedMode: explicitMode ? config.mode : mode,
      baseUrl: baseUrl,
      health: function () {
        if (mode === "live" && !fetchFn) return { mode: mode, ok: false, reason: "no-fetch" };
        if (mode === "fixture" && !hasFixtures(fixtures)) return { mode: mode, ok: false, reason: "no-fixtures" };
        return { mode: mode, ok: true };
      },
      loadOverlay: function (jobId, entryA, entryB) {
        var meta = { family: "overlay", source: mode, viewId: "overlay:" + jobId + ":" + entryA + ":" + entryB, jobId: jobId };
        if (mode === "fixture") {
          return Promise.resolve(modelApi().errorView(meta, "E_FIXTURE_OVERLAY", "fixture 模式不支持叠合"));
        }
        return loadOverlay(jobId, entryA, entryB, meta, ctx);
      },
      loadVibrations: function (jobId, entryId) {
        var meta = { family: "vibration", source: mode, viewId: "vibration:" + jobId + ":" + entryId, jobId: jobId };
        if (mode === "fixture") {
          return Promise.resolve(modelApi().errorView(meta, "E_FIXTURE_VIBRATION", "fixture 模式不支持振动数据"));
        }
        return loadVibrations(jobId, entryId, meta, ctx);
      },
      load: function (viewId, ref) {
        ref = ref || {};
        var family = asString(ref.family) || "structure";
        var meta = { family: family, source: mode, viewId: asString(viewId) };
        try {
          if (mode === "fixture") return fixtureLoad(ref, meta, ctx);
          return loadLive(ref, meta, ctx);
        } catch (error) {
          return Promise.resolve(modelApi().errorView(meta, "E_VIEW", error && error.message ? error.message : String(error)));
        }
      }
    };

    return adapter;
  }

  return {
    MODES: MODES,
    STORAGE_KEY: STORAGE_KEY,
    create: create,
    resolveMode: resolveMode,
    modeFromSearch: modeFromSearch,
    modeFromStorage: modeFromStorage,
    buildStructureUrl: buildStructureUrl,
    buildOverlayUrl: buildOverlayUrl,
    buildVibrationsUrl: buildVibrationsUrl,
    encodeFilePath: encodeFilePath,
    mapEnergyGraph: mapEnergyGraph,
    mapStructureCatalog: mapStructureCatalog,
    mapOverlayPayload: mapOverlayPayload,
    mapVibrationPayload: mapVibrationPayload,
    readResponse: readResponse,
    readJson: readJson
  };
})();
