/*
 * ACP 3.0 — view-model contract `acp.view.v1` (Track A / A-00).
 *
 * Classic script, no build, no network, no DOM. Panels consume ONLY the
 * normalized envelope this module returns; raw backend shapes (energy-graph,
 * structure-viewer, files, …) are mapped by view_source.js and never leak
 * into a panel.
 *
 * Frozen rules (docs/reference/next-change/ACP_3.0_Frontend_Plan_20261031.md §3):
 *   1. four data states: ready / empty / denied / error (loading is UI-owned);
 *   2. source:"fixture" is visible in the envelope (`fixture:true`) so panels
 *      can render the demo badge and tests can assert it;
 *   3. missing data is declared in warnings[]; values are NEVER zero-filled,
 *      invented or silently dropped;
 *   4. energy/frame views are read-only (no submit/command surface here).
 *
 * The module also owns the pure structure-ref / XYZ helpers so the viewer and
 * the source adapter share one parser (no second implementation).
 */
window.ACPView = window.ACPView || {};
ACPView.model = (function () {
  "use strict";

  var SCHEMA = "acp.view.v1";
  var FAMILIES = [
    "structure", "frames", "vibration", "overlay", "energy",
    "spectrum", "network", "table", "artifact", "evidence", "catalog"
  ];
  var SOURCES = ["live", "fixture"];
  var AVAILABILITIES = ["ready", "empty", "denied", "error"];

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
  }

  function asString(value) {
    return typeof value === "string" ? value : "";
  }

  function safeClone(value) {
    if (value == null) return value;
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (e) {
      return undefined;
    }
  }

  function addWarning(out, seen, code, message) {
    var safeCode = String(code || "W_UNKNOWN");
    var safeMessage = String(message || safeCode);
    var key = safeCode + "\u0000" + safeMessage;
    if (seen[key]) return;
    seen[key] = true;
    out.push({ code: safeCode, message: safeMessage });
  }

  function normalizeWarnings(rawWarnings, out, seen) {
    if (rawWarnings == null) return;
    if (!Array.isArray(rawWarnings)) {
      addWarning(out, seen, "W_WARNINGS_SHAPE", "warnings 必须是数组");
      return;
    }
    rawWarnings.forEach(function (item) {
      if (typeof item === "string") {
        addWarning(out, seen, "W_RAW", item);
        return;
      }
      if (isObject(item)) {
        addWarning(out, seen, item.code, item.message);
        return;
      }
      addWarning(out, seen, "W_RAW_SHAPE", "无法识别的 warning 项");
    });
  }

  function normalizeObjects(rawObjects, add) {
    var objects = {};
    if (rawObjects == null) return objects;
    if (!isObject(rawObjects)) {
      add("W_OBJECTS_SHAPE", "objects 必须是对象");
      return objects;
    }
    Object.keys(rawObjects).forEach(function (key) {
      var item = rawObjects[key];
      if (typeof item === "string") {
        objects[key] = { format: "xyz", text: item };
        return;
      }
      if (!isObject(item) || typeof item.text !== "string") {
        add("W_OBJECT_INVALID", "对象 " + key + " 缺少 text");
        return;
      }
      var normalized = {
        format: asString(item.format) || "xyz",
        text: item.text
      };
      if (asString(item.encoding)) normalized.encoding = asString(item.encoding);
      objects[key] = normalized;
    });
    return objects;
  }

  function normalizeFrames(rawFrames, objects, family, add) {
    var frames = [];
    if (rawFrames == null) return frames;
    if (!Array.isArray(rawFrames)) {
      add("W_FRAMES_SHAPE", "frames 必须是数组");
      return frames;
    }
    rawFrames.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_FRAME_SHAPE", "第 " + i + " 帧不是对象");
        return;
      }
      var index = isFiniteNumber(item.index) && item.index >= 0 ? Math.floor(item.index) : i;
      if (isFiniteNumber(item.index) && item.index < 0) {
        add("W_FRAME_INDEX", "第 " + i + " 帧 index 非法，已按顺序号处理");
      }
      var geometryRef = asString(item.geometryRef);
      var frame = {
        index: index,
        label: asString(item.label) || ("Frame " + (index + 1)),
        energy: isFiniteNumber(item.energy) ? item.energy : null,
        geometryRef: geometryRef
      };
      if (asString(item.status)) frame.status = asString(item.status);
      if (family === "structure" && !geometryRef) {
        add("W_FRAME_NO_GEOMETRY", "第 " + index + " 帧缺少 geometryRef");
      }
      if (geometryRef && family === "structure" && !objects[geometryRef]) {
        add("W_MISSING_GEOMETRY", "geometryRef 未在 objects 中提供: " + geometryRef);
      }
      frames.push(frame);
    });
    return frames;
  }

  function normalizePoints(rawPoints, seriesId, add) {
    var points = [];
    if (rawPoints == null) return points;
    if (!Array.isArray(rawPoints)) {
      add("W_POINTS_SHAPE", "series " + seriesId + " 的 points 必须是数组");
      return points;
    }
    rawPoints.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_POINT_SHAPE", "series " + seriesId + " 第 " + i + " 个点不是对象");
        return;
      }
      var x = isFiniteNumber(item.x) ? item.x : null;
      var y = isFiniteNumber(item.y) ? item.y : null;
      if (x === null || y === null) {
        add("W_POINT_MISSING", "series " + seriesId + " 第 " + i + " 个点缺少 x/y（不补 0）");
      }
      var point = { x: x, y: y };
      if (isFiniteNumber(item.frameIndex)) point.frameIndex = Math.floor(item.frameIndex);
      if (asString(item.nodeId)) point.nodeId = asString(item.nodeId);
      points.push(point);
    });
    return points;
  }

  function normalizeSeries(rawSeries, add) {
    var series = [];
    if (rawSeries == null) return series;
    if (!Array.isArray(rawSeries)) {
      add("W_SERIES_SHAPE", "series 必须是数组");
      return series;
    }
    rawSeries.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_SERIES_ITEM_SHAPE", "第 " + i + " 条曲线不是对象");
        return;
      }
      var id = asString(item.id);
      if (!id) {
        id = "series-" + i;
        add("W_SERIES_ID", "第 " + i + " 条曲线缺少 id，已使用顺序 id");
      }
      var out = {
        id: id,
        label: asString(item.label) || id,
        unit: asString(item.unit),
        points: normalizePoints(item.points, id, add)
      };
      if (asString(item.axis)) out.axis = asString(item.axis);
      series.push(out);
    });
    return series;
  }

  function normalizeAxes(rawAxes, add) {
    if (rawAxes == null) return null;
    if (!isObject(rawAxes)) {
      add("W_AXES_SHAPE", "axes 必须是对象");
      return null;
    }
    var axes = {};
    ["x", "y"].forEach(function (key) {
      var item = rawAxes[key];
      if (item == null) return;
      if (!isObject(item)) {
        add("W_AXIS_SHAPE", "axes." + key + " 必须是对象");
        return;
      }
      axes[key] = { label: asString(item.label), unit: asString(item.unit) };
    });
    return Object.keys(axes).length ? axes : null;
  }

  function normalizeAnnotations(rawAnnotations, add) {
    var annotations = [];
    if (rawAnnotations == null) return annotations;
    if (!Array.isArray(rawAnnotations)) {
      add("W_ANNOTATIONS_SHAPE", "annotations 必须是数组");
      return annotations;
    }
    rawAnnotations.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_ANNOTATION_SHAPE", "第 " + i + " 个标注不是对象");
        return;
      }
      var out = {
        id: asString(item.id) || ("annotation-" + i),
        label: asString(item.label),
        kind: asString(item.kind) || asString(item.type),
        x: isFiniteNumber(item.x) ? item.x : null,
        y: isFiniteNumber(item.y) ? item.y : null,
        payload: isObject(item.payload) ? safeClone(item.payload) : null
      };
      annotations.push(out);
    });
    return annotations;
  }

  function normalizeGroups(rawGroups, add) {
    var groups = [];
    if (rawGroups == null) return groups;
    if (!Array.isArray(rawGroups)) {
      add("W_GROUPS_SHAPE", "groups 必须是数组");
      return groups;
    }
    rawGroups.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_GROUP_SHAPE", "第 " + i + " 个分组不是对象");
        return;
      }
      var id = asString(item.id);
      if (!id) {
        add("W_GROUP_ID", "第 " + i + " 个分组缺少 id，已跳过");
        return;
      }
      groups.push({
        id: id,
        label: asString(item.label) || id,
        kind: asString(item.kind)
      });
    });
    return groups;
  }

  function normalizeEntryEnergy(rawEnergy, add, entryId) {
    if (rawEnergy == null) return null;
    if (!isObject(rawEnergy)) {
      add("W_ENTRY_ENERGY_SHAPE", "条目 " + entryId + " 的 energy 不是对象");
      return null;
    }
    return {
      value: isFiniteNumber(rawEnergy.value) ? rawEnergy.value : null,
      unit: asString(rawEnergy.unit) || "hartree",
      kind: asString(rawEnergy.kind) || "electronic",
      temperatureK: isFiniteNumber(rawEnergy.temperatureK) ? rawEnergy.temperatureK : null
    };
  }

  function normalizeEntries(rawEntries, add) {
    var entries = [];
    if (rawEntries == null) return entries;
    if (!Array.isArray(rawEntries)) {
      add("W_ENTRIES_SHAPE", "entries 必须是数组");
      return entries;
    }
    rawEntries.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_ENTRY_SHAPE", "第 " + i + " 个条目不是对象");
        return;
      }
      var id = asString(item.id);
      if (!id) {
        add("W_ENTRY_ID", "第 " + i + " 个条目缺少 id，已跳过");
        return;
      }
      var source = isObject(item.source) ? item.source : {};
      var badges = [];
      if (Array.isArray(item.badges)) {
        badges = item.badges.filter(function (badge) { return typeof badge === "string"; });
      } else if (item.badges != null) {
        add("W_ENTRY_BADGES", "条目 " + id + " 的 badges 必须是字符串数组");
      }
      entries.push({
        id: id,
        groupId: asString(item.groupId) || asString(item.group_id),
        label: asString(item.label) || id,
        role: asString(item.role),
        status: asString(item.status),
        energy: normalizeEntryEnergy(item.energy, add, id),
        relativeEnergyKcal: isFiniteNumber(item.relativeEnergyKcal) ? item.relativeEnergyKcal :
          (isFiniteNumber(item.relative_energy_kcal) ? item.relative_energy_kcal : null),
        boltzmannWeight: isFiniteNumber(item.boltzmannWeight) ? item.boltzmannWeight :
          (isFiniteNumber(item.boltzmann_weight) ? item.boltzmann_weight : null),
        badges: badges,
        geometryRef: asString(item.geometryRef) || asString(source.geometry_ref),
        frameIndex: isFiniteNumber(item.frameIndex) ? Math.floor(item.frameIndex) :
          (isFiniteNumber(source.frame_index) ? Math.floor(source.frame_index) : null),
        sourceKind: asString(item.sourceKind) || asString(source.kind)
      });
    });
    return entries;
  }

  function normalizeOverlay(rawOverlay, add) {
    if (rawOverlay == null) return null;
    if (!isObject(rawOverlay)) {
      add("W_OVERLAY_SHAPE", "overlay 必须是对象");
      return null;
    }
    var mapping = null;
    if (rawOverlay.mapping != null) {
      if (Array.isArray(rawOverlay.mapping)) {
        mapping = rawOverlay.mapping.filter(function (pair) {
          return Array.isArray(pair) && pair.length === 2 &&
            isFiniteNumber(pair[0]) && isFiniteNumber(pair[1]);
        });
      } else {
        add("W_OVERLAY_MAPPING", "overlay.mapping 必须是 [[i,j], …]");
      }
    }
    var maxDisplacement = null;
    if (isObject(rawOverlay.maxDisplacement)) {
      maxDisplacement = {
        i: isFiniteNumber(rawOverlay.maxDisplacement.i) ? rawOverlay.maxDisplacement.i : null,
        j: isFiniteNumber(rawOverlay.maxDisplacement.j) ? rawOverlay.maxDisplacement.j : null,
        distance: isFiniteNumber(rawOverlay.maxDisplacement.distance) ? rawOverlay.maxDisplacement.distance : null
      };
    }
    return {
      ok: !!rawOverlay.ok,
      entryA: asString(rawOverlay.entryA),
      entryB: asString(rawOverlay.entryB),
      mapping: mapping,
      rmsd: isFiniteNumber(rawOverlay.rmsd) ? rawOverlay.rmsd : null,
      maxDisplacement: maxDisplacement,
      nMapped: isFiniteNumber(rawOverlay.nMapped) ? Math.floor(rawOverlay.nMapped) : 0,
      reason: asString(rawOverlay.reason) || "unproven"
    };
  }

  function normalizeModes(rawModes, add) {
    var modes = [];
    if (rawModes == null) return modes;
    if (!Array.isArray(rawModes)) {
      add("W_MODES_SHAPE", "modes 必须是数组");
      return modes;
    }
    rawModes.forEach(function (item, i) {
      if (!isObject(item)) {
        add("W_MODE_SHAPE", "第 " + i + " 个振动模式不是对象");
        return;
      }
      var modeIndex = isFiniteNumber(item.modeIndex) ? Math.floor(item.modeIndex) :
        (isFiniteNumber(item.mode_index) ? Math.floor(item.mode_index) : null);
      if (modeIndex == null) {
        add("W_MODE_INDEX", "第 " + i + " 个振动模式缺少 mode_index，已跳过");
        return;
      }
      var vectors = null;
      if (Array.isArray(item.vectors)) {
        vectors = item.vectors.filter(function (vector) {
          return Array.isArray(vector) && vector.length >= 3 &&
            isFiniteNumber(vector[0]) && isFiniteNumber(vector[1]) && isFiniteNumber(vector[2]);
        }).map(function (vector) { return [vector[0], vector[1], vector[2]]; });
        if (vectors.length !== item.vectors.length) {
          add("W_MODE_VECTOR", "模式 " + modeIndex + " 含无效位移向量");
        }
      } else if (item.vectors != null) {
        add("W_MODE_VECTOR_SHAPE", "模式 " + modeIndex + " 的 vectors 必须是数组");
      }
      modes.push({
        modeIndex: modeIndex,
        frequencyCm1: isFiniteNumber(item.frequencyCm1) ? item.frequencyCm1 :
          (isFiniteNumber(item.frequency_cm1) ? item.frequency_cm1 : null),
        imaginary: item.imaginary === true ||
          (isFiniteNumber(item.frequency_cm1) && item.frequency_cm1 < 0),
        irIntensity: isFiniteNumber(item.irIntensity) ? item.irIntensity :
          (isFiniteNumber(item.ir_intensity) ? item.ir_intensity : null),
        vectors: vectors
      });
    });
    return modes;
  }

  function normalizeVibrationMeta(rawVibration, modes, add) {
    if (rawVibration == null) return null;
    if (!isObject(rawVibration)) {
      add("W_VIBRATION_SHAPE", "vibration 必须是对象");
      return null;
    }
    var available = rawVibration.available === true;
    if (!available && modes.length) {
      add("W_VIBRATION_INCONSISTENT", "vibration.available=false 但存在 modes");
    }
    return {
      available: available,
      reason: asString(rawVibration.reason),
      atomCount: isFiniteNumber(rawVibration.atomCount) ? Math.floor(rawVibration.atomCount) :
        (isFiniteNumber(rawVibration.atom_count) ? Math.floor(rawVibration.atom_count) : 0),
      imaginaryCount: isFiniteNumber(rawVibration.imaginaryCount) ? Math.floor(rawVibration.imaginaryCount) :
        (isFiniteNumber(rawVibration.imaginary_count) ? Math.floor(rawVibration.imaginary_count) : null),
      source: asString(rawVibration.source),
      geometryProductId: asString(rawVibration.geometryProductId) ||
        asString(rawVibration.geometry_product_id) || null
    };
  }

  function normalizeThermo(rawThermo, add) {
    if (rawThermo == null) return null;
    if (!isObject(rawThermo)) {
      add("W_THERMO_SHAPE", "thermo 必须是对象");
      return null;
    }
    var thermo = {
      totalGibbsKcalMol: isFiniteNumber(rawThermo.totalGibbsKcalMol) ? rawThermo.totalGibbsKcalMol :
        (isFiniteNumber(rawThermo.total_gibbs_kcal_mol) ? rawThermo.total_gibbs_kcal_mol : null),
      totalGibbsHartree: isFiniteNumber(rawThermo.totalGibbsHartree) ? rawThermo.totalGibbsHartree :
        (isFiniteNumber(rawThermo.total_gibbs_hartree) ? rawThermo.total_gibbs_hartree : null),
      rank1Weight: isFiniteNumber(rawThermo.rank1Weight) ? rawThermo.rank1Weight :
        (isFiniteNumber(rawThermo.rank1_weight) ? rawThermo.rank1_weight : null),
      tSMixKcalPerMol: isFiniteNumber(rawThermo.tSMixKcalPerMol) ? rawThermo.tSMixKcalPerMol :
        (isFiniteNumber(rawThermo.t_s_mix_kcal_per_mol) ? rawThermo.t_s_mix_kcal_per_mol : null),
      populationCoverage: isFiniteNumber(rawThermo.populationCoverage) ? rawThermo.populationCoverage :
        (isFiniteNumber(rawThermo.population_coverage) ? rawThermo.population_coverage : null),
      temperatureK: isFiniteNumber(rawThermo.temperatureK) ? rawThermo.temperatureK :
        (isFiniteNumber(rawThermo.temperature_k) ? rawThermo.temperature_k : null)
    };
    var any = Object.keys(thermo).some(function (key) { return thermo[key] != null; });
    if (!any) {
      add("W_THERMO_EMPTY", "thermo 未提供任何有限数值");
      return null;
    }
    return thermo;
  }

  function normalizeProvenance(rawProvenance, add) {
    if (rawProvenance == null) return {};
    if (!isObject(rawProvenance)) {
      add("W_PROVENANCE_SHAPE", "provenance 必须是对象");
      return {};
    }
    var cloned = safeClone(rawProvenance);
    return isObject(cloned) ? cloned : {};
  }

  /**
   * Normalize a panel-facing view envelope.
   *
   * @param {Object|null} raw family-specific payload (already mapped by the
   *   source adapter; never a raw backend response).
   * @param {Object} [meta] `{ family, source, viewId }`; meta wins over raw.
   * @returns {Object} frozen `acp.view.v1` envelope.
   */
  function normalize(raw, meta) {
    meta = isObject(meta) ? meta : {};
    var warnings = [];
    var seen = Object.create(null);
    function add(code, message) {
      addWarning(warnings, seen, code, message);
    }

    var family = asString(meta.family) || asString(raw && raw.family);
    if (FAMILIES.indexOf(family) < 0) {
      add("E_FAMILY", "未知视图族: " + (family || "(空)"));
    }

    var source = asString(meta.source) || asString(raw && raw.source) || "live";
    if (SOURCES.indexOf(source) < 0) {
      add("W_SOURCE", "未知数据来源: " + source + "，按 live 处理");
      source = "live";
    }

    if (!raw || !isObject(raw)) {
      return {
        schema: SCHEMA,
        viewId: asString(meta.viewId),
        family: family,
        source: source,
        availability: "empty",
        fixture: source === "fixture",
        provenance: {},
        axes: null,
        series: [],
        frames: [],
        annotations: [],
        objects: {},
        groups: [],
        entries: [],
        overlay: null,
        modes: [],
        vibration: null,
        thermo: null,
        warnings: warnings
      };
    }

    normalizeWarnings(raw.warnings, warnings, seen);
    var objects = normalizeObjects(raw.objects, add);
    var frames = normalizeFrames(raw.frames, objects, family, add);
    var series = normalizeSeries(raw.series, add);
    var axes = normalizeAxes(raw.axes, add);
    var annotations = normalizeAnnotations(raw.annotations, add);
    var groups = normalizeGroups(raw.groups, add);
    var entries = normalizeEntries(raw.entries, add);
    var overlay = normalizeOverlay(raw.overlay, add);
    var modes = normalizeModes(raw.modes, add);
    var vibration = normalizeVibrationMeta(raw.vibration, modes, add);
    var thermo = normalizeThermo(raw.thermo || raw.ensembleThermo, add);
    var provenance = normalizeProvenance(raw.provenance, add);

    var explicitAvailability = null;
    if (raw.availability != null) {
      if (AVAILABILITIES.indexOf(raw.availability) >= 0) {
        explicitAvailability = raw.availability;
      } else {
        add("W_AVAILABILITY", "未知 availability: " + raw.availability);
      }
    }

    var hasData = series.some(function (item) { return item.points.length > 0; }) ||
      frames.length > 0 ||
      entries.length > 0 ||
      overlay != null ||
      modes.length > 0 ||
      Object.keys(objects).some(function (key) { return objects[key].text.trim().length > 0; });
    if (overlay && !overlay.ok) {
      add("W_OVERLAY_UNPROVEN", "叠合映射未证实: " + (overlay.reason || "unproven"));
    }
    if (vibration && !vibration.available) {
      add("W_VIBRATION_UNAVAILABLE", "振动数据不可用: " + (vibration.reason || "unavailable"));
    }

    var availability = explicitAvailability || (hasData ? "ready" : "empty");
    if (availability === "ready" && !hasData) {
      add("W_READY_WITHOUT_DATA", "availability=ready 但没有可渲染数据，已降级为 empty");
      availability = "empty";
    }
    if (availability === "error" && warnings.length === 0) {
      add("E_UNKNOWN", "视图错误未提供原因");
    }
    if (availability === "denied" && warnings.length === 0) {
      add("E_DENIED", "无权访问该视图");
    }
    if (series.length && !series.some(function (item) {
      return item.points.some(function (point) { return point.x !== null && point.y !== null; });
    })) {
      add("W_NO_DRAWABLE_POINTS", "曲线没有可绘制的点");
    }

    return {
      schema: SCHEMA,
      viewId: asString(meta.viewId) || asString(raw.viewId),
      family: family,
      source: source,
      availability: availability,
      fixture: source === "fixture",
      provenance: provenance,
      axes: axes,
      series: series,
      frames: frames,
      annotations: annotations,
      objects: objects,
      groups: groups,
      entries: entries,
      overlay: overlay,
      modes: modes,
      vibration: vibration,
      thermo: thermo,
      warnings: warnings
    };
  }

  function emptyView(meta) {
    return normalize({ availability: "empty" }, meta);
  }

  function errorView(meta, code, message) {
    return normalize({
      availability: "error",
      warnings: [{ code: code || "E_VIEW", message: message || "视图不可用" }]
    }, meta);
  }

  /** Map a data availability onto the panel's four-state vocabulary. */
  function stateOf(view) {
    if (!view || AVAILABILITIES.indexOf(view.availability) < 0) return "error";
    return view.availability === "denied" ? "error" : view.availability;
  }

  /* ── shared structure helpers (single parser for source + viewer) ─────── */

  function parseEnergyFromComment(comment) {
    var match = String(comment || "").match(/(-?\d+\.\d+)/);
    return match ? parseFloat(match[1]) : null;
  }

  /** Parse one or more XYZ frames into `{index, atoms, comment, energy}`. */
  function parseXyzFrames(text) {
    var lines = String(text == null ? "" : text).trim().split(/\r?\n/);
    var frames = [];
    var i = 0;
    while (i < lines.length) {
      while (i < lines.length && !String(lines[i] || "").trim()) i += 1;
      if (i >= lines.length) break;
      var count = parseInt(String(lines[i] || "").trim(), 10);
      if (isNaN(count) || count < 1 || i + 1 + count >= lines.length) break;
      var comment = lines[i + 1] || "";
      var atoms = [];
      for (var j = 0; j < count; j += 1) {
        var parts = String(lines[i + 2 + j] || "").trim().split(/\s+/);
        if (parts.length >= 4) {
          atoms.push({
            id: j,
            elem: parts[0],
            x: +parts[1],
            y: +parts[2],
            z: +parts[3]
          });
        }
      }
      frames.push({
        index: frames.length,
        atoms: atoms,
        comment: comment,
        energy: parseEnergyFromComment(comment)
      });
      i += 2 + count;
    }
    return frames;
  }

  function atomsToXyz(atoms) {
    var out = atoms.length + "\n\n";
    for (var i = 0; i < atoms.length; i += 1) {
      var atom = atoms[i];
      out += atom.elem + " " + atom.x.toFixed(6) + " " + atom.y.toFixed(6) + " " + atom.z.toFixed(6) + "\n";
    }
    return out;
  }

  /** Stable change-detection key for a structureRef (order-insensitive). */
  function structureRefKey(ref) {
    if (ref == null) return null;
    if (typeof ref === "string") return "s:" + ref;
    if (typeof ref !== "object") return "x:" + String(ref);
    var kind = asString(ref.kind);
    if (kind === "inline-xyz") {
      return ["inline-xyz", String(ref.frameIndex == null ? 0 : ref.frameIndex), String(ref.text || "")].join("|");
    }
    if (kind === "structure-entry") {
      return ["structure-entry", String(ref.jobId || ""), String(ref.entryId || ""),
        String(ref.frameIndex == null ? 0 : ref.frameIndex)].join("|");
    }
    return [
      kind,
      String(ref.jobId || ""),
      String(ref.path || ""),
      String(ref.frameIndex == null ? 0 : ref.frameIndex)
    ].join("|");
  }

  /** Normalize a structureRef into a concrete request, or an invalid marker. */
  function normalizeStructureRef(ref) {
    if (ref == null) return null;
    if (typeof ref === "string") {
      var text = String(ref).trim();
      if (!text) return null;
      try {
        return normalizeStructureRef(JSON.parse(text));
      } catch (e) {
        return { kind: "invalid", reason: "structureRef 字符串需为 JSON 对象（模块 README 冻结形状）" };
      }
    }
    if (typeof ref !== "object") {
      return { kind: "invalid", reason: "structureRef 类型不支持: " + typeof ref };
    }
    var kind = asString(ref.kind);
    var frameIndex = typeof ref.frameIndex === "number" && ref.frameIndex >= 0 ? Math.floor(ref.frameIndex) : 0;
    if (kind === "inline-xyz") {
      if (typeof ref.text !== "string" || !ref.text.trim()) {
        return { kind: "invalid", reason: "inline-xyz 需要非空 text" };
      }
      return { kind: kind, text: ref.text, frameIndex: frameIndex, entryId: "inline-xyz" };
    }
    if (kind === "job-file" || kind === "job-remote-file") {
      if (typeof ref.jobId !== "string" || !ref.jobId) {
        return { kind: "invalid", reason: kind + " 需要非空 jobId" };
      }
      if (typeof ref.path !== "string" || !ref.path) {
        return { kind: "invalid", reason: kind + " 需要非空 path" };
      }
      return {
        kind: kind,
        jobId: ref.jobId,
        path: ref.path,
        frameIndex: frameIndex,
        entryId: ref.jobId + ":" + ref.path
      };
    }
    if (kind === "structure-entry") {
      if (typeof ref.jobId !== "string" || !ref.jobId) {
        return { kind: "invalid", reason: "structure-entry 需要非空 jobId" };
      }
      if (typeof ref.entryId !== "string" || !ref.entryId) {
        return { kind: "invalid", reason: "structure-entry 需要非空 entryId" };
      }
      return {
        kind: kind,
        jobId: ref.jobId,
        entryId: ref.entryId,
        frameIndex: frameIndex,
        storeEntryId: ref.jobId + ":entry:" + ref.entryId
      };
    }
    return { kind: "invalid", reason: "未知 structureRef.kind: " + (kind || "(空)") };
  }

  return {
    SCHEMA: SCHEMA,
    FAMILIES: FAMILIES,
    SOURCES: SOURCES,
    AVAILABILITIES: AVAILABILITIES,
    normalize: normalize,
    emptyView: emptyView,
    errorView: errorView,
    stateOf: stateOf,
    parseEnergyFromComment: parseEnergyFromComment,
    parseXyzFrames: parseXyzFrames,
    atomsToXyz: atomsToXyz,
    structureRefKey: structureRefKey,
    normalizeStructureRef: normalizeStructureRef
  };
})();
