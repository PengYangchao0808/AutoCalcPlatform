/**
 * ACPJobEditor — 全任务「修改参数后重算」编辑链
 * (docs/ACP_Edit_And_Recalculate_Plan.md §8)。
 *
 * 职责：editorContext 管理、草稿加载（GET /jobs/{id}/edit-draft）、
 * 工作流适配 hydrate/serialize、基线脏状态、差异展示、预览与提交
 * （POST /jobs/{id}/edit-recalculate/preview → /edit-recalculate）。
 *
 * 设计原则（单一工作草稿 / 单一来源状态 / 单一提交入口）：
 * - 数据权威顺序：JobRecord.spec（originalSpec）→ effective_config 快照
 *   （仅展示/解析继承值）→ catalog 默认（仅旧任务缺字段时回退并标注）
 *   → 用户本次修改。
 * - serialize(hydrate(original)) ≡ original：未修改时预览差异必须为零。
 *   方法/资源一律「原值 + 用户实际修改字段的 patch」，绝不整体重建。
 * - 来源状态只有 editorContext.sourceSelection.kind 一个权威；界面只用
 *   任务结构/结构输入/上传三项，原输入、末次结构与其他任务结果投影为
 *   同一 StructureSourceItem 并共用 wizardStructures/previewViewer。
 * - 提交入口只有弹窗底部 #modal-submit 一个；编辑模式下由
 *   handleModalSubmit 分发到 checkAndSubmit。
 * - 共用 Workbench 现有创建表单与参数收集函数（提交请求经 api() 拦截
 *   进入本模块，不复制一套 HTML 表单）。
 * - 异步响应受 requestToken 保护，旧响应不得覆盖当前表单。
 */
(function () {
  "use strict";

  var editorContext = null;
  var tokenCounter = 0;
  var menuEl = null;

  function isActive() { return !!editorContext; }
  function currentMode() { return editorContext ? editorContext.mode : null; }
  function sourceJobId() { return editorContext ? editorContext.sourceJobId : ""; }

  // ---------------------------------------------------------------------------
  // 纯工具（无 DOM 依赖；经 ACPJobEditorInternals 暴露给回归测试）
  // ---------------------------------------------------------------------------

  function deepCopy(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function deepEqual(a, b) {
    if (a === b) return true;
    if (a === null || b === null || typeof a !== "object" || typeof b !== "object") {
      return false;
    }
    try {
      return JSON.stringify(a) === JSON.stringify(b);
    } catch (e) {
      return false;
    }
  }

  // 「键是否存在」判断 —— 判断字段必须使用它，不能用 ||，否则破坏
  // null / false / 0 / "" 的语义。
  function has(obj, key) {
    return !!obj && typeof obj === "object" &&
      Object.prototype.hasOwnProperty.call(obj, key) && obj[key] !== undefined;
  }

  // 仅复制源对象中存在的键（保留 false/0/""/null）。
  function presenceCopy(src, keys) {
    var out = {};
    (keys || Object.keys(src || {})).forEach(function (k) {
      if (has(src, k)) out[k] = deepCopy(src[k]);
    });
    return out;
  }

  function inputKind(inp) {
    if (!inp || typeof inp !== "object") return "plain";
    if (inp.scan_request && typeof inp.scan_request === "object") return "scan_request";
    var st = String(inp.source_type || "");
    if (st) return st;
    if (inp.source_job_id || inp.from_artifact) return "stage_artifact";
    return "structured";
  }

  function structureSourceToWizard(item) {
    var xyz = String((item && item.xyz_text) || "");
    var available = !!xyz && (!item.geometry_status || item.geometry_status === "available");
    var declaredAtoms = parseInt((xyz.split(/\r?\n/, 1)[0] || "0").trim(), 10) || 0;
    return {
      source_id: item.source_id || "",
      source_kind: item.source_kind || "original_input",
      item_id: item.item_id || "",
      name: item.name || item.item_id || "structure",
      molecule_name: item.name || item.item_id || "structure",
      tag: item.tag || "",
      xyz: xyz,
      molfile: "",
      has_3d: available,
      charge: has(item, "charge") ? item.charge : 0,
      multiplicity: has(item, "multiplicity") ? item.multiplicity : 1,
      atom_count: item.atom_count || declaredAtoms,
      formula: "",
      warnings: [],
      errors: available ? [] : [item.geometry_error || "该来源没有可用几何"],
      geometry_ref: deepCopy(item.geometry_ref || {}),
    };
  }

  function hydrateTaskStructures(ctx, draft) {
    var refs = draft.input_refs || {};
    var items = Array.isArray(refs.structure_items) ? refs.structure_items.slice() : [];
    var last = refs.last_structure;
    if (last && last.available && last.xyz_text) {
      items.push({
        source_id: "edit-last:" + ctx.sourceJobId + ":" + (last.entry_id || "last"),
        source_kind: "last_structure",
        item_id: last.entry_id || "last_structure",
        name: last.label || "上次有效结构",
        tag: "",
        atom_count: 0,
        charge: ((ctx.originalSpec || {}).input || {}).charge,
        multiplicity: ((ctx.originalSpec || {}).input || {}).multiplicity,
        geometry_status: "available",
        geometry_ref: { kind: "inline_xyz", item_id: last.entry_id || "last_structure" },
        xyz_text: last.xyz_text,
      });
    }
    ctx.taskStructureItems = items;
    if (typeof wizardStructures !== "undefined") {
      wizardStructures = items.map(structureSourceToWizard);
      wizardSelectedStructureIndex = 0;
      wizardParseWarnings = [];
      if (typeof renderStructurePreview === "function") {
        renderStructurePreview(wizardStructures, wizardParseWarnings);
      }
    }
    var first = items[0] || null;
    ctx.sourceSelection = {
      kind: first ? (first.source_kind || "original_input") : "original_input",
      payload: first,
    };
  }

  // 原地可复用的"简单结构"输入：表单重建等价于原输入。
  function isSimpleStructureInput(inp) {
    var kind = inputKind(inp);
    return kind === "xyz_text" || kind === "smiles" || kind === "plain" ||
      kind === "structure_asset";
  }

  function cleanLevels(stages) {
    var out = {};
    Object.keys(stages || {}).forEach(function (lid) {
      var st = stages[lid];
      if (!st || st._disabled === true) return;
      out[lid] = deepCopy(st);
      delete out[lid]._disabled;
    });
    return out;
  }

  // ---------------------------------------------------------------------------
  // BatchOptimize 纯逻辑：角色默认、旧版扁平字段重建、patch 序列化
  // ---------------------------------------------------------------------------

  // 与 ACP_Workbench_v2.html applyBatchOptimizeMethodFields 的 ROLE_KEYS 对齐。
  var BATCH_ROLE_KEYS = [
    "method", "basis", "sp_method", "sp_basis",
    "dispersion", "aux_j_basis", "aux_c_basis", "ri_approximation",
    "opt_max_iter", "opt_convergence", "opt_trust_radius", "opt_initial_hessian", "opt_recalc_hess",
    "scf_max_iter", "scf_convergence", "scf_strategy", "scf_orbital_inherit",
    "scf_damp", "scf_damp_fac", "scf_shift", "scf_shift_fac",
    "opt_rescue_policy", "opt_max_rescue",
    "temperature", "pressure", "scale_factor"
  ];

  // 静态角色默认（与 openMethodConfig 内 _BATCH_ROLE_DEFAULTS 静态部分一致；
  // method/basis 等方法相关默认由页面补齐，这里仅作为缺失键的最后回退）。
  function batchRoleStaticDefaults() {
    return {
      int: {
        method: "", basis: null, sp_method: null, sp_basis: null,
        dispersion: "none", aux_j_basis: "", aux_c_basis: "", ri_approximation: "none",
        opt_max_iter: null, opt_convergence: "tight",
        opt_trust_radius: null, opt_initial_hessian: "auto", opt_recalc_hess: "auto",
        scf_max_iter: 300, scf_convergence: "tight", scf_strategy: "normal",
        scf_orbital_inherit: true, scf_damp: false, scf_damp_fac: 0.50,
        scf_shift: false, scf_shift_fac: 0.30,
        opt_rescue_policy: "adaptive", opt_max_rescue: 2,
        temperature: 298.15, pressure: 1.0, scale_factor: 0.9905,
      },
      ts: {
        method: "", basis: null, sp_method: null, sp_basis: null,
        dispersion: "none", aux_j_basis: "", aux_c_basis: "", ri_approximation: "none",
        opt_max_iter: null, opt_convergence: "tight",
        opt_trust_radius: 0.3, opt_initial_hessian: "calculate", opt_recalc_hess: 5,
        scf_max_iter: 300, scf_convergence: "tight", scf_strategy: "normal",
        scf_orbital_inherit: true, scf_damp: false, scf_damp_fac: 0.50,
        scf_shift: false, scf_shift_fac: 0.30,
        opt_rescue_policy: "adaptive", opt_max_rescue: 2,
        temperature: 298.15, pressure: 1.0, scale_factor: 0.9905,
      },
    };
  }

  // 旧版公共扁平字段 → 角色键（公共：同时落到 int/ts）。
  var BATCH_FLAT_COMMON = {
    optimization_method: "method",
    optimization_basis: "basis",
    single_point_method: "sp_method",
    single_point_basis: "sp_basis",
    temperature: "temperature",
    pressure: "pressure",
    scale_factor: "scale_factor",
    dispersion: "dispersion",
    aux_j_basis: "aux_j_basis",
    aux_c_basis: "aux_c_basis",
    ri_approximation: "ri_approximation",
    scf_orbital_inherit: "scf_orbital_inherit",
  };

  // 旧版顶层 opt_*/scf_* 同样作用于两个角色。
  var BATCH_FLAT_SHARED = {
    opt_max_iter: "opt_max_iter",
    opt_convergence: "opt_convergence",
    opt_trust_radius: "opt_trust_radius",
    opt_initial_hessian: "opt_initial_hessian",
    opt_recalc_hess: "opt_recalc_hess",
    scf_max_iter: "scf_max_iter",
    scf_convergence: "scf_convergence",
    scf_strategy: "scf_strategy",
    opt_rescue_policy: "opt_rescue_policy",
    opt_max_rescue: "opt_max_rescue",
  };

  // 旧版角色前缀扁平字段。
  var BATCH_FLAT_ROLE = {
    int: {
      minimum_opt_max_iter: "opt_max_iter",
      minimum_opt_convergence: "opt_convergence",
      minimum_opt_trust_radius: "opt_trust_radius",
      minimum_opt_initial_hessian: "opt_initial_hessian",
      minimum_opt_recalc_hess: "opt_recalc_hess",
      minimum_scf_max_iter: "scf_max_iter",
      minimum_scf_convergence: "scf_convergence",
      minimum_scf_strategy: "scf_strategy",
      minimum_opt_rescue_policy: "opt_rescue_policy",
      minimum_opt_max_rescue: "opt_max_rescue",
    },
    ts: {
      transition_state_opt_max_iter: "opt_max_iter",
      transition_state_opt_convergence: "opt_convergence",
      transition_state_opt_trust_radius: "opt_trust_radius",
      transition_state_opt_initial_hessian: "opt_initial_hessian",
      transition_state_opt_recalc_hess: "opt_recalc_hess",
      transition_state_scf_max_iter: "scf_max_iter",
      transition_state_scf_convergence: "scf_convergence",
      transition_state_scf_strategy: "scf_strategy",
      transition_state_opt_rescue_policy: "opt_rescue_policy",
      transition_state_opt_max_rescue: "opt_max_rescue",
    },
  };

  /**
   * 合并优先级：profile/静态默认 → 旧版公共扁平字段（含角色前缀）→
   * method.batch_roles.int/ts（键存在才覆盖）。全部使用存在性判断。
   */
  function mergeBatchRoles(method, staticDefaults) {
    var m = method || {};
    var defaults = staticDefaults || batchRoleStaticDefaults();
    var roles = { int: deepCopy(defaults.int), ts: deepCopy(defaults.ts) };
    Object.keys(BATCH_FLAT_COMMON).forEach(function (flat) {
      if (!has(m, flat)) return;
      roles.int[BATCH_FLAT_COMMON[flat]] = deepCopy(m[flat]);
      roles.ts[BATCH_FLAT_COMMON[flat]] = deepCopy(m[flat]);
    });
    Object.keys(BATCH_FLAT_SHARED).forEach(function (flat) {
      if (!has(m, flat)) return;
      roles.int[BATCH_FLAT_SHARED[flat]] = deepCopy(m[flat]);
      roles.ts[BATCH_FLAT_SHARED[flat]] = deepCopy(m[flat]);
    });
    ["int", "ts"].forEach(function (role) {
      var map = BATCH_FLAT_ROLE[role];
      Object.keys(map).forEach(function (flat) {
        if (has(m, flat)) roles[role][map[flat]] = deepCopy(m[flat]);
      });
    });
    var br = m.batch_roles;
    if (br && typeof br === "object") {
      ["int", "ts"].forEach(function (role) {
        var src = br[role];
        if (!src || typeof src !== "object") return;
        if (!roles[role]) roles[role] = {};
        BATCH_ROLE_KEYS.forEach(function (k) {
          if (has(src, k)) roles[role][k] = deepCopy(src[k]);
        });
      });
    }
    return roles;
  }

  /**
   * BatchOptimize 方法 patch 序列化：原 method + 用户实际修改的字段。
   * unmodified（current ≡ baseline）时由调用方直接返回原 method 副本。
   */
  function buildBatchMethodFromPatch(originalMethod, baselineRoles, currentRoles,
    baselineProfileId, currentProfileId, staticDefaults) {
    var m = deepCopy(originalMethod || {});
    if (currentProfileId && currentProfileId !== baselineProfileId) {
      m.profile_id = currentProfileId;
      m.profile = currentProfileId;
    }
    var origRoles = (m.batch_roles && typeof m.batch_roles === "object" &&
      (m.batch_roles.int || m.batch_roles.ts))
      ? m.batch_roles
      : mergeBatchRoles(m, staticDefaults);
    var patched = deepCopy(origRoles);
    patched.int = patched.int || {};
    patched.ts = patched.ts || {};
    var intFlatPatched = {};
    ["int", "ts"].forEach(function (role) {
      var cur = (currentRoles || {})[role] || {};
      var base = (baselineRoles || {})[role] || {};
      BATCH_ROLE_KEYS.forEach(function (k) {
        if (!deepEqual(cur[k], base[k])) {
          patched[role][k] = has(cur, k) ? deepCopy(cur[k]) : null;
          if (role === "int") intFlatPatched[k] = true;
        }
      });
    });
    m.batch_roles = patched;
    // 旧版消费者兼容字段：仅当对应 INT 角色键被修改时同步。
    if (intFlatPatched.method) m.optimization_method = patched.int.method;
    if (intFlatPatched.basis) m.optimization_basis = patched.int.basis;
    if (intFlatPatched.sp_method) m.single_point_method = patched.int.sp_method;
    if (intFlatPatched.sp_basis) m.single_point_basis = patched.int.sp_basis;
    return m;
  }

  // ---------------------------------------------------------------------------
  // 工作流适配器：hydrateMethod / hydrateInput / methodProjection /
  // buildMethod / patchOriginalInput / preCollect / describe
  // ---------------------------------------------------------------------------

  function methodProfileById(profileId) {
    if (!profileId || profileId === "__custom__") return null;
    if (typeof getMethodProfileById === "function") return getMethodProfileById(profileId);
    return null;
  }

  // 通用 method 回填：profile_id（存在性判断）+ levels（原值优先，缺省回退
  // 到 catalog 中该 profile 的 levels；再缺失才保留当前默认并标注）。
  function hydrateMethodBase(ctx, draft) {
    var spec = draft.editable_spec || {};
    var m = spec.method || {};
    if (has(m, "profile_id")) wizardState.method.profile_id = m.profile_id;
    else if (has(m, "profile")) wizardState.method.profile_id = m.profile;
    var lv = m.levels;
    if (lv && typeof lv === "object" && Object.keys(lv).length) {
      wizardState.method.stages = deepCopy(lv);
    } else {
      var prof = methodProfileById(wizardState.method.profile_id);
      if (prof && prof.levels && Object.keys(prof.levels).length) {
        wizardState.method.stages = deepCopy(prof.levels);
      } else {
        ctx.methodBackfilled = true;
      }
    }
  }

  function baseMethodProjection() {
    return {
      profile_id: wizardState.method.profile_id || "",
      stages: cleanLevels(wizardState.method.stages),
    };
  }

  // 通用 buildMethod：原 method + levels/profile patch。
  function baseBuildMethod(ctx, body, current) {
    var m = deepCopy(ctx.originalSpec.method || {});
    if (current.profile_id && current.profile_id !== ctx.methodBaseline.profile_id) {
      m.profile_id = current.profile_id;
    }
    var stages = cleanLevels(current.stages);
    if (Object.keys(stages).length) m.levels = stages;
    return m;
  }

  function makeBaseAdapter() {
    return {
      hydrateMethod: function (ctx, draft) { hydrateMethodBase(ctx, draft); },
      hydrateInput: function (ctx, draft) { hydrateSimpleInput(draft); },
      methodProjection: baseMethodProjection,
      buildMethod: baseBuildMethod,
      patchOriginalInput: patchOriginalChargeMult,
      preCollect: null,
      describe: function (ctx) { return inputKind((ctx.originalSpec || {}).input || {}); },
    };
  }

  var batchOptimizeAdapter = {
    hydrateMethod: function (ctx, draft) {
      var spec = draft.editable_spec || {};
      var m = spec.method || {};
      if (has(m, "profile_id")) wizardState.method.profile_id = m.profile_id;
      else if (has(m, "profile")) wizardState.method.profile_id = m.profile;
      // steps/levels：以 catalog 中该 profile 的 levels 为准（未存进 spec）。
      var prof = methodProfileById(wizardState.method.profile_id);
      if (prof && prof.levels && Object.keys(prof.levels).length) {
        wizardState.method.stages = deepCopy(prof.levels);
      } else if (m.levels && Object.keys(m.levels).length) {
        wizardState.method.stages = deepCopy(m.levels);
      } else {
        ctx.methodBackfilled = true;
      }
      // batch_roles：默认 → 旧扁平 → 原 batch_roles（存在性合并）。
      var staticDefaults = (typeof batchRoleStaticDefaults === "function")
        ? batchRoleStaticDefaults()
        : (window.batchRoleStaticDefaults ? window.batchRoleStaticDefaults() : null);
      wizardState.method.stages.batch_roles = mergeBatchRoles(m, staticDefaults);
    },
    hydrateInput: function (ctx, draft) {
      // 完整批量结构清单（顺序、名称、INT/TS 标签、电子态）进入批量面板；
      // 不把第一项 XYZ 塞进结构文本框。
      var spec = draft.editable_spec || {};
      var inp = spec.input || {};
      var items = Array.isArray(inp.items) ? inp.items : [];
      if (typeof batchPreviewItems !== "undefined") {
        batchPreviewItems = items.map(function (item, i) {
          return {
            key: "edit_" + i + "_" + Math.random().toString(36).slice(2, 8),
            include: item.include !== false,
            name: item.name || item.candidate_id || "structure",
            tag: item.tag || "",
            tagAuto: !item.tag,
            xyz: item.xyz || "",
            charge: has(item, "charge") ? item.charge : 0,
            multiplicity: has(item, "multiplicity") ? item.multiplicity : 1,
            atomCount: 0,
            formula: "",
            sourceType: "upload",
            sourceRef: "",
          };
        });
        if (typeof renderStageBatchPreview === "function") renderStageBatchPreview();
      }
    },
    methodProjection: function () {
      return {
        profile_id: wizardState.method.profile_id || "",
        batch_roles: deepCopy((wizardState.method.stages || {}).batch_roles || {}),
      };
    },
    buildMethod: function (ctx, body, current) {
      var staticDefaults = (typeof batchRoleStaticDefaults === "function")
        ? batchRoleStaticDefaults()
        : (window.batchRoleStaticDefaults ? window.batchRoleStaticDefaults() : null);
      return buildBatchMethodFromPatch(
        ctx.originalSpec.method || {},
        (ctx.methodBaseline || {}).batch_roles || {},
        current.batch_roles || {},
        (ctx.methodBaseline || {}).profile_id || "",
        current.profile_id || "",
        staticDefaults
      );
    },
    patchOriginalInput: function (ctx, overlay, body) {
      // 批量条目：include/名称/标签/电子态以批量面板当前值为准（仅 patch
      // 与基线不同的条目；未改动条目逐字保留）。
      var cur = (typeof batchPreviewItems !== "undefined") ? batchPreviewItems : [];
      var base = ctx.baselineBatchItems || [];
      if (Array.isArray(overlay.items)) {
        var kept = [];
        overlay.items.forEach(function (item, i) {
          var curItem = cur[i];
          if (curItem && curItem.include === false) return;
          if (!curItem && i >= base.length) return; // 新增行未提供 → 丢弃
          var copy = deepCopy(item);
          copy.include = true;
          if (curItem) {
            var baseItem = base[i] || {};
            if (!deepEqual(batchItemProjection(curItem), batchItemProjection(baseItem))) {
              if (has(curItem, "name")) copy.name = curItem.name;
              if (has(curItem, "tag")) copy.tag = curItem.tag;
              if (has(curItem, "charge")) copy.charge = curItem.charge;
              if (has(curItem, "multiplicity")) copy.multiplicity = curItem.multiplicity;
            }
          }
          kept.push(copy);
        });
        if (!kept.length) throw new Error(t("edit.batch_items_empty"));
        overlay.items = kept;
      }
      return patchOriginalChargeMult(ctx, overlay, body);
    },
    preCollect: function (ctx) {
      // 替换来源 + 已解析新结构：让收集器从新解析结构重建批量清单。
      var kind = ctx.sourceSelection.kind;
      if (kind === "original_input" || kind === "last_structure") return;
      if (typeof batchPreviewItems === "undefined" || typeof wizardStructures === "undefined") return;
      if (!wizardStructures.length) return;
      if (deepEqual(batchItemsProjection(), ctx.baselineBatchItems)) {
        batchPreviewItems = [];
      }
    },
    describe: function (ctx) {
      var inp = (ctx.originalSpec || {}).input || {};
      var items = Array.isArray(inp.items) ? inp.items : [];
      return "batch_structures(" + items.length + ")";
    },
  };

  var confsearchAdapter = {
    hydrateMethod: function (ctx, draft) { hydrateMethodBase(ctx, draft); },
    hydrateInput: function (ctx, draft) { hydrateSimpleInput(draft); },
    methodProjection: baseMethodProjection,
    buildMethod: function (ctx, body, current) {
      var m = baseBuildMethod(ctx, body, current);
      var cs = ((current.stages || {}).confsearch) || {};
      if (has(cs, "protocol")) m.protocol = cs.protocol;
      if (has(cs, "confsearch_profile")) m.profile = cs.confsearch_profile;
      if (has(cs, "refinement_policy")) m.refinement_policy = cs.refinement_policy;
      if (has(cs, "ewin") && cs.ewin !== "") m.ewin = cs.ewin;
      return m;
    },
    patchOriginalInput: patchOriginalChargeMult,
    preCollect: null,
    describe: function (ctx) { return inputKind((ctx.originalSpec || {}).input || {}); },
  };

  var nmrAdapter = {
    hydrateMethod: function (ctx, draft) { hydrateMethodBase(ctx, draft); },
    hydrateInput: function (ctx, draft) {
      // assigned 文本可恢复；bruker 资产失效要求替换。
      var spec = draft.editable_spec || {};
      var inp = spec.input || {};
      if (inp.experiment && inp.experiment.mode === "assigned" &&
        typeof nmrExperimentMode !== "undefined") {
        nmrExperimentMode = "assigned";
        var ta = document.getElementById("nmr-experiment-text");
        if (ta && inp.experiment.content) ta.value = String(inp.experiment.content);
      }
    },
    methodProjection: baseMethodProjection,
    buildMethod: baseBuildMethod,
    patchOriginalInput: function (ctx, overlay, body) {
      overlay = patchOriginalChargeMult(ctx, overlay, body);
      if (overlay.experiment && overlay.experiment.mode === "assigned" &&
        typeof nmrExperimentMode !== "undefined" && nmrExperimentMode === "assigned") {
        var ta = document.getElementById("nmr-experiment-text");
        var text = ta ? String(ta.value || "") : "";
        if (text && text !== (ctx.baselineFormState || {}).nmr_experiment_text) {
          overlay.experiment = { mode: "assigned", content: text };
        }
      }
      return overlay;
    },
    preCollect: null,
    describe: function (ctx) {
      var inp = (ctx.originalSpec || {}).input || {};
      var cands = Array.isArray(inp.candidates) ? inp.candidates : [];
      return "candidates(" + cands.length + ")";
    },
  };

  var pesSearchAdapter = {
    hydrateMethod: function (ctx, draft) { hydrateMethodBase(ctx, draft); },
    hydrateInput: function (ctx, draft) {
      var spec = draft.editable_spec || {};
      var inp = spec.input || {};
      if (inputKind(inp) === "stage_artifact") {
        setFieldValue("stage-source-job", inp.source_job_id);
        setFieldValue("stage-artifact", inp.from_artifact);
        setFieldValue("stage-product", inp.product);
        setFieldValue("stage-ts-guess", inp.ts_guess);
        if (inp.coordinate_plan) {
          setFieldValue("stage-plan", JSON.stringify(inp.coordinate_plan));
        }
        var m = spec.method || {};
        if (Array.isArray(m.select)) setFieldValue("stage-select", m.select.join(","));
      }
    },
    methodProjection: baseMethodProjection,
    buildMethod: baseBuildMethod,
    patchOriginalInput: function (ctx, overlay, body) {
      var m = ctx.originalSpec.method || {};
      if (Array.isArray(m.select) && has(overlay, "select") === false && m.select.length) {
        overlay.select = deepCopy(m.select);
      }
      return overlay;
    },
    preCollect: null,
    describe: function (ctx) { return inputKind((ctx.originalSpec || {}).input || {}); },
  };

  var workflowAdapters = {
    BatchOptimize: batchOptimizeAdapter,
    Confsearch: confsearchAdapter,
    PESsearch: pesSearchAdapter,
    nmr: nmrAdapter,
    scan: makeBaseAdapter(),
    default: makeBaseAdapter(),
  };

  function adapterFor(workflow) {
    return workflowAdapters[workflow] || workflowAdapters.default;
  }

  // ---------------------------------------------------------------------------
  // 输入回填辅助
  // ---------------------------------------------------------------------------

  function setFieldValue(id, value) {
    if (value === undefined || value === null) return;
    var el = document.getElementById(id);
    if (el) el.value = String(value);
  }

  // 简单结构输入：单结构进结构框并解析（3D 预览）。复杂输入不塞文本框，
  // 由「上次输入」只读摘要面板展示。
  function hydrateSimpleInput(draft) {
    var spec = draft.editable_spec || {};
    var inp = spec.input || {};
    var kind = inputKind(inp);
    var box = document.getElementById("modal-structure-input");
    var previewText = "";
    if (kind === "xyz_text" || kind === "plain") {
      previewText = String(has(inp, "source") ? inp.source :
        (has(inp, "input_artifact") ? inp.input_artifact :
          (has(inp, "smiles") ? inp.smiles : "")));
    } else if (kind === "smiles") {
      previewText = String(has(inp, "source") ? inp.source : "");
    } else if (kind === "structure_asset") {
      previewText = String(has(inp, "source") ? inp.source : "");
    } else if (kind === "scan_request") {
      var src = (inp.scan_request && inp.scan_request.source) || {};
      previewText = String(src.xyz_text || src.source || "");
    }
    if (previewText && box) {
      box.value = previewText;
      if (typeof scheduleParseStructures === "function") scheduleParseStructures();
    }
  }

  // kind=original 时 charge/multiplicity 的最小 patch（仅当用户实际改动）。
  function patchOriginalChargeMult(ctx, overlay, body) {
    var bi = (body && body.input) || {};
    var base = ctx.baselineFormState || {};
    if (has(bi, "charge") && String(bi.charge) !== String(base.charge) &&
      overlay && !Array.isArray(overlay.items)) {
      overlay.charge = parseInt(bi.charge, 10);
    }
    if (has(bi, "multiplicity") && String(bi.multiplicity) !== String(base.mult) &&
      overlay && !Array.isArray(overlay.items)) {
      overlay.multiplicity = parseInt(bi.multiplicity, 10);
    }
    return overlay;
  }

  function batchItemProjection(item) {
    if (!item) return null;
    return {
      name: item.name || "",
      tag: item.tag || "",
      charge: item.charge,
      multiplicity: item.multiplicity,
      include: item.include !== false,
      xyz: item.xyz || "",
    };
  }

  function batchItemsProjection() {
    if (typeof batchPreviewItems === "undefined") return [];
    return batchPreviewItems.map(batchItemProjection);
  }

  // ---------------------------------------------------------------------------
  // 入口：重算菜单（队列 ↻ 按钮与详情页共用）
  // ---------------------------------------------------------------------------

  function closeRerunMenu() {
    if (menuEl && menuEl.parentNode) menuEl.parentNode.removeChild(menuEl);
    menuEl = null;
    document.removeEventListener("click", closeRerunMenu, true);
    document.removeEventListener("keydown", onMenuKey, true);
  }

  function onMenuKey(ev) {
    if (ev.key === "Escape") { closeRerunMenu(); return; }
    if (!menuEl) return;
    var focusables = Array.prototype.slice.call(menuEl.querySelectorAll("button"));
    if (!focusables.length) return;
    var idx = focusables.indexOf(document.activeElement);
    if (ev.key === "ArrowDown") {
      ev.preventDefault();
      focusables[(idx + 1 + focusables.length) % focusables.length].focus();
    } else if (ev.key === "ArrowUp") {
      ev.preventDefault();
      focusables[(idx - 1 + focusables.length) % focusables.length].focus();
    }
  }

  function openRerunMenu(job, anchor) {
    if (!job) return;
    closeRerunMenu();
    var jobId = String(job.id || job.job_id || "");
    var recovery = (typeof resolveJobRecovery === "function")
      ? resolveJobRecovery(job) : { can_rerun: true };
    menuEl = document.createElement("div");
    menuEl.className = "rerun-action-menu";
    menuEl.setAttribute("role", "menu");
    [
      {
        key: "edit.menu.rerun_direct",
        icon: "↻",
        enabled: !!recovery.can_rerun,
        reason: "edit.menu.rerun_direct_disabled",
        action: function () { closeRerunMenu(); rerunJob(jobId, null); },
      },
      {
        key: "edit.menu.edit_recalculate",
        icon: "✎",
        enabled: true,
        reason: "",
        action: function () { closeRerunMenu(); openJobEditor(job, "edit_recalculate"); },
      },
      {
        key: "edit.menu.new_from_job",
        icon: "⧉",
        enabled: true,
        reason: "",
        action: function () { closeRerunMenu(); openJobEditor(job, "new_from_job"); },
      },
    ].forEach(function (item) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.innerHTML = '<span class="rerun-menu-icon">' + item.icon + "</span><span>" +
        t(item.key) + "</span>";
      if (!item.enabled) {
        btn.disabled = true;
        btn.title = item.reason ? t(item.reason) : "";
      } else {
        btn.addEventListener("click", function (ev) { ev.stopPropagation(); item.action(); });
      }
      menuEl.appendChild(btn);
    });
    var rect = anchor.getBoundingClientRect();
    menuEl.style.left = Math.max(8, Math.min(window.innerWidth - 230, rect.left)) + "px";
    menuEl.style.top = (rect.bottom + 4 + window.scrollY) + "px";
    menuEl.style.position = "absolute";
    document.body.appendChild(menuEl);
    // 菜单打开不触发卡片选择/展开：容器级捕获关闭 + stopPropagation。
    menuEl.addEventListener("click", function (ev) { ev.stopPropagation(); });
    setTimeout(function () {
      document.addEventListener("click", closeRerunMenu, true);
      document.addEventListener("keydown", onMenuKey, true);
      var first = menuEl.querySelector("button:not([disabled])");
      if (first) first.focus();
    }, 0);
  }

  // ---------------------------------------------------------------------------
  // 打开编辑器：openModal 全量重置 → 并行等待草稿与目录 → hydrate → 基线
  // ---------------------------------------------------------------------------

  function ensureCatalogsLoaded() {
    var tasks = [];
    if (typeof loadWorkflowCatalog === "function" &&
      typeof workflowCatalogCache !== "undefined" && !workflowCatalogCache.length) {
      tasks.push(loadWorkflowCatalog().catch(function () { /* 目录失败不阻断草稿 */ }));
    }
    if (typeof loadMethodCatalog === "function" &&
      typeof methodCatalogCache !== "undefined" && !methodCatalogCache.method_schemas) {
      tasks.push(loadMethodCatalog().catch(function () { /* 同上 */ }));
    }
    return Promise.all(tasks);
  }

  function openJobEditor(job, mode) {
    var jobId = String(job.id || job.job_id || "");
    var token = ++tokenCounter;
    editorContext = {
      mode: mode,
      sourceJobId: jobId,
      sourceJob: job,
      requestToken: token,
      draft: null,
      originalSpec: {},
      effectiveConfig: null,
      adapter: null,
      methodBaseline: null,
      baselineFormState: null,
      baselineBatchItems: [],
      methodBackfilled: false,
      sourceSelection: { kind: "original_input", payload: null },
      executionMode: mode === "new_from_job" ? "new_job" : "in_place",
      fingerprint: null,
      submitting: false,
      collecting: false,
      collectedBodies: null,
      pendingPreviewBody: null,
    };
    openModal();
    renderBanner("loading");
    hideFooter();
    updateModalSubmitButton();
    Promise.all([
      api("/jobs/" + encodeURIComponent(jobId) + "/edit-draft"),
      ensureCatalogsLoaded(),
    ]).then(function (results) {
      if (!editorContext || editorContext.requestToken !== token) return;
      var draft = results[0];
      editorContext.draft = draft;
      hydrate(draft);
      renderBanner("ready");
      renderFooter();
      updateEditorUiState();
      startBadgeWatch();
    }, function (err) {
      if (!editorContext || editorContext.requestToken !== token) return;
      renderBanner("error", (err && err.message) || String(err));
    });
  }

  function cancelEditor(force) {
    if (!editorContext) return;
    if (!force && countLocalChanges() > 0) {
      if (!window.confirm(t("edit.discard_confirm"))) return;
    }
    editorContext = null;
    stopBadgeWatch();
    hideSummary();
    renderBanner("hidden");
    hideFooter();
    hideOriginalPanelExtras();
    restoreBatchItems([]);  // 清空编辑态批量镜像（新建语义由下一次 openModal 重置）
    updateModalSubmitButton();
    closeModal();
  }

  // ---------------------------------------------------------------------------
  // hydrate：挂载工作流表单 → 回填参数 → 回填输入 → 建立基线 → 刷新摘要
  // ---------------------------------------------------------------------------

  function hydrate(draft) {
    var ctx = editorContext;
    var spec = draft.editable_spec || {};
    ctx.originalSpec = spec;
    ctx.effectiveConfig = draft.effective_config || null;
    ctx.adapter = adapterFor(spec.workflow);
    ctx.methodBackfilled = false;
    // 1) 工作流预选走现有 pendingNewTask 通道（含 catalog 自愈 + 默认档位）。
    window._pendingNewTask = { workflow: spec.workflow, projectId: spec.project_id || "" };
    applyPendingNewTask();
    // 2) 工作流适配器回填方法参数（覆盖 openModal 载入的当前默认值）。
    ctx.adapter.hydrateMethod(ctx, draft);
    // 3) 表单公共字段。
    document.getElementById("modal-remark").value = spec.remark || "";
    document.getElementById("modal-task-name").value =
      spec.task_name && spec.task_name !== spec.workflow ? spec.task_name : "";
    var res = spec.resources || {};
    if (res.nproc) document.getElementById("modal-nproc").value = String(res.nproc);
    if (res.mem) document.getElementById("modal-mem").value = String(res.mem);
    if (res.parallelism && document.getElementById("modal-parallelism")) {
      document.getElementById("modal-parallelism").value = String(res.parallelism);
    }
    var inp = spec.input || {};
    var charge = inp.charge, mult = inp.multiplicity;
    if (inputKind(inp) === "scan_request" && inp.scan_request && inp.scan_request.source) {
      charge = inp.scan_request.source.charge; mult = inp.scan_request.source.multiplicity;
    }
    if (Array.isArray(inp.items) && inp.items.length &&
      has(inp.items[0], "charge")) {
      charge = inp.items[0].charge;
      mult = has(inp.items[0], "multiplicity") ? inp.items[0].multiplicity : mult;
    }
    if (charge !== undefined && charge !== null) document.getElementById("modal-charge").value = String(charge);
    if (mult !== undefined && mult !== null) document.getElementById("modal-mult").value = String(mult);
    // 4) 项目与节点。
    var ps = document.getElementById("modal-project-select");
    if (ps && spec.project_id) {
      var hasOption = Array.prototype.some.call(ps.options, function (o) { return o.value === spec.project_id; });
      if (hasOption) ps.value = spec.project_id;
    }
    if (spec.node_tags && spec.node_tags.length && typeof nodeMatchingState !== "undefined") {
      nodeMatchingState.selectedTags = spec.node_tags.slice();
    }
    // 5) 工作流适配器回填输入。
    ctx.adapter.hydrateInput(ctx, draft);
    ctx.baselineBatchItems = batchItemsProjection();
    // 6) 所有原输入结构进入共享 wizardStructures/previewViewer；默认选中
    //    第一项（BatchOptimize 保留完整输入顺序），界面只显示「任务结构」。
    hydrateTaskStructures(ctx, draft);
    setWizardInputMode("task");
    updateEditModeTabs();
    updateConfigCards();
    ctx.methodBaseline = ctx.adapter.methodProjection();
    ctx.baselineFormState = formProjection();
  }

  // ---------------------------------------------------------------------------
  // 序列化：originalSpec + 用户实际修改字段的 patch（复用页面收集函数）
  // ---------------------------------------------------------------------------

  function serializeFromCollectedBody(body) {
    var ctx = editorContext;
    var spec = ctx.originalSpec;
    var workflow = spec.workflow;
    var method = serializeMethod(ctx, body);
    var out = {
      mode: ctx.executionMode,
      workflow: workflow,
      input: serializeInput(ctx, body),
      method: method,
      resources: serializeResources(ctx, body),
      // molecule_name 不是表单可编辑字段：原值优先，保证未修改 diff 为零。
      molecule_name: spec.molecule_name || (body && body.molecule_name) || "",
      task_name: (body && body.task_name) || "",
      remark: (body && body.remark) || "",
      tags: (body && body.tags && body.tags.length) ? body.tags : deepCopy(spec.tags || []),
      request_id: newRequestId(),
      expected_source_revision: ctx.draft.source_revision,
    };
    if (ctx.executionMode === "new_job") {
      out.project_id = (body && body.project_id) || spec.project_id;
      if (body && body.name) out.name = String(body.name);
    }
    if (typeof applyNodeSelectionToBody === "function") applyNodeSelectionToBody(out);
    if (ctx.fingerprint) out.preview_fingerprint = ctx.fingerprint;
    return out;
  }

  // 未修改 → 原 method 逐字返回；修改 → 原 method + patch。
  function serializeMethod(ctx, body) {
    var current = ctx.adapter.methodProjection();
    if (deepEqual(current, ctx.methodBaseline)) {
      return deepCopy(ctx.originalSpec.method || {});
    }
    return ctx.adapter.buildMethod(ctx, body, current);
  }

  // 资源同理：未修改 → 原资源逐字返回（防止表单未覆盖的键被误删）。
  function serializeResources(ctx, body) {
    var original = deepCopy((ctx.originalSpec.resources || {}));
    var r = deepCopy(body && body.resources) || {};
    delete r.batch_id;
    delete r.batch_index;
    delete r.batch_total;
    var base = ctx.baselineFormState || {};
    var changed = false;
    ["nproc", "mem", "parallelism"].forEach(function (k) {
      var cur = (k === "nproc" || k === "parallelism")
        ? String(parseInt(String(r[k] !== undefined ? r[k] : ""), 10) || "")
        : String(r[k] !== undefined ? r[k] : "");
      if (cur && cur !== String(base[k] !== undefined ? base[k] : "")) changed = true;
    });
    if (!changed) return original;
    var out = original;
    if (has(r, "nproc")) out.nproc = parseInt(String(r.nproc), 10) || original.nproc;
    if (has(r, "mem")) out.mem = r.mem;
    if (has(r, "parallelism")) out.parallelism = parseInt(String(r.parallelism), 10) || original.parallelism;
    return out;
  }

  function serializeInput(ctx, body) {
    var spec = ctx.originalSpec;
    var orig = spec.input || {};
    var kind = ctx.sourceSelection.kind;
    if (kind === "last_structure") {
      var ls = ((ctx.draft && ctx.draft.input_refs) || {}).last_structure;
      if (!ls || !ls.xyz_text) throw new Error(t("edit.last_structure_missing"));
      return {
        source_type: "xyz_text",
        source: ls.xyz_text,
        charge: orig.charge,
        multiplicity: orig.multiplicity,
        edit_input_origin: { mode: "last_structure", entry_id: ls.entry_id },
      };
    }
    if (kind === "original_input") {
      var overlay = deepCopy(orig);
      if (ctx.adapter.patchOriginalInput) {
        overlay = ctx.adapter.patchOriginalInput(ctx, overlay, body);
      }
      return overlay;
    }
    // structure / results / upload → 替换输入：使用新解析/选择的载荷。
    if (!(body && body.input)) throw new Error(t("edit.no_payload_collected"));
    return body.input;
  }

  function newRequestId() {
    return "ed_" + editorContext.sourceJobId + "_" + Date.now().toString(36) +
      "_" + Math.random().toString(36).slice(2, 8);
  }

  // ---------------------------------------------------------------------------
  // api() 拦截：编辑模式下 POST /jobs 进入收集/预览，而不是真创建。
  // 正常用户路径（#modal-submit → checkAndSubmit）总是 collecting=true；
  // 此拦截保留为安全防护。
  // ---------------------------------------------------------------------------

  function interceptJobCreate(body) {
    var ctx = editorContext;
    if (!ctx || !ctx.collecting) {
      return Promise.reject(new Error(t("edit.create_blocked_in_editor")));
    }
    ctx.collectedBodies.push(deepCopy(body));
    return Promise.resolve({ job_id: "__edit_collected__", status: "collected", __editCollected: true });
  }

  // ---------------------------------------------------------------------------
  // 检查并提交（唯一提交入口，由 #modal-submit 分发）→ 预览 → 摘要确认 → 提交
  // ---------------------------------------------------------------------------

  function withModalHeld(fn) {
    var original = window.closeModal;
    window.closeModal = function () { /* held during collection */ };
    return Promise.resolve().then(fn).then(function (v) {
      window.closeModal = original;
      return v;
    }, function (e) {
      window.closeModal = original;
      throw e;
    });
  }

  function modalSubmitBtn() { return document.getElementById("modal-submit"); }

  function setFooterBusy(busy) {
    var btn = modalSubmitBtn();
    if (btn) btn.disabled = !!busy;
  }

  // kind=original / last_structure 的收集通道：serializeInput 不消费
  // body.input（原样透传或服务端快照），页面收集器的结构前置校验
  // （解析结构 / PES 选原子 / NMR 实验谱）不应阻断无修改重算 —— 表单
  // 公共字段直接读取，不路由 submitJobModal。
  function collectFormOnlyBody() {
    var spec = editorContext.originalSpec;
    var taskName = fieldVal("modal-task-name").trim()
      .replace(/[/\\:*?"<>|]/g, "")
      .replace(/\s+/g, "_")
      .replace(/^_+|_+$/g, "");
    var body = {
      workflow: spec.workflow,
      remark: fieldVal("modal-remark").trim(),
      task_name: taskName,
      molecule_name: spec.molecule_name || "",
      input: {},
      resources: {
        nproc: parseInt(fieldVal("modal-nproc"), 10) || 4,
        mem: fieldVal("modal-mem") || "8GB",
      },
      tags: [],
    };
    var parEl = document.getElementById("modal-parallelism");
    if (parEl) body.resources.parallelism = Math.max(1, parseInt(fieldVal("modal-parallelism"), 10) || 1);
    var ps = document.getElementById("modal-project-select");
    if (ps && ps.value) body.project_id = ps.value;
    return body;
  }

  function collectViaPageCollectors(ctx) {
    return withModalHeld(function () {
      if (ctx.adapter.preCollect) ctx.adapter.preCollect(ctx);
      return submitJobModal();
    }).then(function () {
      ctx.collecting = false;
      if (!editorContext || editorContext.requestToken !== ctx.requestToken) return null;
      if (!ctx.collectedBodies.length) {
        throw new Error(t("edit.no_payload_collected"));
      }
      if (ctx.collectedBodies.length > 1) {
        throw new Error(t("edit.multi_structure_not_supported"));
      }
      return ctx.collectedBodies[0];
    });
  }

  function checkAndSubmit() {
    var ctx = editorContext;
    if (!ctx || ctx.submitting || ctx.collecting) return;
    if (!ctx.draft) return;
    var passiveKind = ctx.sourceSelection.kind === "original_input" ||
      ctx.sourceSelection.kind === "last_structure";
    var collecting = passiveKind ? Promise.resolve(collectFormOnlyBody()) : null;
    if (passiveKind) {
      ctx.collecting = false;
    } else {
      ctx.collecting = true;
      ctx.collectedBodies = [];
    }
    setFooterBusy(true);
    var bodyPromise = passiveKind ? collecting : collectViaPageCollectors(ctx);
    bodyPromise.then(function (collectedBody) {
      if (!editorContext || editorContext.requestToken !== ctx.requestToken) return;
      if (!collectedBody) return;
      return runPreview(collectedBody);
    }).then(function (preview) {
      if (!editorContext || editorContext.requestToken !== ctx.requestToken) return;
      if (!preview) return;
      ctx.pendingPreviewBody = passiveKind ? collectFormOnlyBody() : ctx.collectedBodies[0];
      ctx.fingerprint = preview.preview_fingerprint;
      showSummary(preview);
    }).catch(function (err) {
      ctx.collecting = false;
      window.alert(t("edit.preview_failed") + ": " + ((err && err.message) || err));
    }).then(function () {
      if (editorContext && editorContext.requestToken === ctx.requestToken && !ctx.submitting) {
        setFooterBusy(false);
        updateEditorUiState();
      }
    });
  }

  function runPreview(collectedBody) {
    var ctx = editorContext;
    var payload = serializeFromCollectedBody(collectedBody);
    return api("/jobs/" + encodeURIComponent(ctx.sourceJobId) + "/edit-recalculate/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (preview) {
      if (preview && preview.blocking_reasons && preview.blocking_reasons.length) {
        throw new Error(preview.blocking_reasons.join("\n"));
      }
      return preview;
    });
  }

  function finalSubmit() {
    var ctx = editorContext;
    if (!ctx || !ctx.pendingPreviewBody || ctx.submitting) return;
    ctx.submitting = true;
    setFooterBusy(true);
    var okBtn = document.getElementById("edit-summary-confirm");
    if (okBtn) okBtn.disabled = true;
    var payload = serializeFromCollectedBody(ctx.pendingPreviewBody);
    api("/jobs/" + encodeURIComponent(ctx.sourceJobId) + "/edit-recalculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (result) {
      editorContext = null;
      stopBadgeWatch();
      hideSummary();
      renderBanner("hidden");
      hideFooter();
      hideOriginalPanelExtras();
      updateModalSubmitButton();
      closeModal();
      if (typeof setDetailActionMessage === "function") {
        setDetailActionMessage(
          result.job_id,
          t(result.operation === "new_job" ? "edit.submit_new_ok" : "edit.submit_inplace_ok",
            { attempt: String(result.attempt) }),
          "ok"
        );
      }
      return refreshJobs();
    }, function (err) {
      // 请求失败保留草稿与错误（计划 §4.2）。
      ctx.submitting = false;
      setFooterBusy(false);
      if (okBtn) okBtn.disabled = false;
      var el = document.getElementById("edit-summary-error");
      if (el) {
        el.style.display = "block";
        el.textContent = t("edit.submit_failed") + ": " + ((err && err.message) || err);
      } else {
        window.alert(t("edit.submit_failed") + ": " + ((err && err.message) || err));
      }
    });
  }

  // ---------------------------------------------------------------------------
  // 脏状态：回填完成后的表单基线 vs 当前表单（权威差异以服务端预览为准）
  // ---------------------------------------------------------------------------

  function fieldVal(id) {
    var el = document.getElementById(id);
    return el ? String(el.value || "") : "";
  }

  function formProjection() {
    var ctx = editorContext;
    var nmrText = "";
    if (ctx && ctx.originalSpec && ctx.originalSpec.workflow === "nmr") {
      var ta = document.getElementById("nmr-experiment-text");
      nmrText = ta ? String(ta.value || "") : "";
    }
    // BatchOptimize 电子态按条目在批量表中维护（batch_items 已覆盖）；
    // 全局 charge/mult 只是"应用到全部"的辅助输入，单独改动不构成脏状态。
    var isBatch = !!(ctx && ctx.originalSpec &&
      ctx.originalSpec.workflow === "BatchOptimize");
    return {
      remark: fieldVal("modal-remark"),
      task_name: fieldVal("modal-task-name").trim(),
      nproc: fieldVal("modal-nproc"),
      mem: fieldVal("modal-mem"),
      parallelism: fieldVal("modal-parallelism"),
      charge: isBatch ? null : fieldVal("modal-charge"),
      mult: isBatch ? null : fieldVal("modal-mult"),
      project_id: fieldVal("modal-project-select"),
      method: ctx && ctx.adapter ? ctx.adapter.methodProjection() : null,
      source_kind: ctx ? ctx.sourceSelection.kind : null,
      batch_items: ctx ? batchItemsProjection() : null,
      nmr_experiment_text: nmrText,
    };
  }

  function countLocalChanges() {
    var ctx = editorContext;
    if (!ctx || !ctx.draft || !ctx.baselineFormState) return 0;
    var base = ctx.baselineFormState;
    var proj = formProjection();
    var count = 0;
    Object.keys(proj).forEach(function (key) {
      if (key === "nmr_experiment_text") {
        if (proj[key] && proj[key] !== base[key]) count++;
        return;
      }
      if (key === "batch_items") {
        if (!deepEqual(proj[key], ctx.baselineBatchItems)) count++;
        return;
      }
      if (!deepEqual(proj[key], base[key])) count++;
    });
    return count;
  }

  // ---------------------------------------------------------------------------
  // 来源页签联动：界面始终只有任务结构 / 结构输入 / 上传三个入口。
  // ---------------------------------------------------------------------------

  var SOURCE_TAB_KINDS = { task: 1, structure: 1, upload: 1 };

  function onSourceTabChange(kind) {
    var ctx = editorContext;
    if (!ctx || !SOURCE_TAB_KINDS[kind]) return;
    var prev = ctx.sourceSelection.kind;
    if (kind === "task") {
      hydrateTaskStructures(ctx, ctx.draft);
    } else {
      ctx.sourceSelection.kind = kind === "structure" ? "manual_input" : "upload";
      ctx.sourceSelection.payload = null;
    }
    // BatchOptimize：在原任务结构与替换来源之间保存/恢复批量清单镜像。
    if (ctx.originalSpec.workflow === "BatchOptimize" &&
      typeof batchPreviewItems !== "undefined") {
      if (kind === "task" && prev !== "original_input" && prev !== "last_structure") {
        restoreBatchItems(ctx.baselineBatchItems);
      } else if (kind !== "task" && (prev === "original_input" || prev === "last_structure")) {
        if (typeof wizardStructures !== "undefined" && wizardStructures.length) {
          batchPreviewItems = [];  // 收集器将用新解析结构重建
          if (typeof renderStageBatchPreview === "function") renderStageBatchPreview();
        }
      }
    }
    updateEditorUiState();
  }

  function onPreviewStructureSelected(structure) {
    var ctx = editorContext;
    if (!ctx || !structure) return;
    var kind = structure.source_kind || "";
    if (kind === "last_structure") {
      ctx.sourceSelection = { kind: "last_structure", payload: structure };
    } else if (kind === "job_result") {
      ctx.sourceSelection = { kind: "job_result", payload: structure };
    } else if (wizardInputMode === "task") {
      ctx.sourceSelection = { kind: "original_input", payload: structure };
    }
    updateEditorUiState();
  }

  function onJobResultLoaded(structure) {
    var ctx = editorContext;
    if (!ctx) return;
    if (structure) structure.source_kind = "job_result";
    ctx.sourceSelection = { kind: "job_result", payload: structure || null };
    updateEditorUiState();
  }

  function restoreBatchItems(projection) {
    if (typeof batchPreviewItems === "undefined") return;
    batchPreviewItems = (projection || []).map(function (p) {
      return {
        key: "edit_" + Math.random().toString(36).slice(2, 10),
        include: p.include !== false,
        name: p.name,
        tag: p.tag,
        tagAuto: false,
        xyz: p.xyz,
        charge: p.charge !== undefined ? p.charge : 0,
        multiplicity: p.multiplicity !== undefined ? p.multiplicity : 1,
        atomCount: 0,
        formula: "",
        sourceType: "upload",
        sourceRef: "",
      };
    });
    if (typeof renderStageBatchPreview === "function") renderStageBatchPreview();
  }

  function updateEditModeTabs() {
    var ctx = editorContext;
    var modal = document.getElementById("job-modal");
    if (modal) modal.classList.toggle("edit-mode", !!ctx);
    var copyButton = document.getElementById("edit-original-copy");
    if (copyButton) copyButton.style.display = ctx ? "" : "none";
    var title = document.getElementById("job-modal-title");
    if (!title) return;
    if (ctx) {
      title.removeAttribute("data-i18n");
      title.textContent = t("edit.title");
    } else {
      title.setAttribute("data-i18n", "modal.title");
      title.textContent = t("modal.title");
    }
  }

  function hideOriginalPanelExtras() {
    updateEditModeTabs();
  }

  // 「上次输入」只读摘要面板。
  function renderOriginalInputPanel() {
    var ctx = editorContext;
    if (!ctx || !ctx.draft) return;
    var panel = document.getElementById("edit-original-summary");
    if (!panel) return;
    var spec = ctx.originalSpec;
    var inp = spec.input || {};
    var kind = inputKind(inp);
    var html = [];
    html.push('<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.input_original")) +
      "</b> · " + escapeHtml(kind) + "</div>");
    if (kind === "batch_structures" && Array.isArray(inp.items)) {
      var int = 0, ts = 0;
      inp.items.forEach(function (item) {
        if (item.tag === "TS") ts++;
        else int++;
      });
      html.push('<div class="edit-banner-hint">' +
        escapeHtml(t("edit.original_items_summary", {
          total: String(inp.items.length), int: String(int), ts: String(ts),
        })) + "</div>");
      html.push('<div class="edit-items"><details open><summary>' +
        escapeHtml(t("edit.batch_items_summary", { total: String(inp.items.length) })) +
        '</summary><div class="edit-items-list">');
      inp.items.forEach(function (item, i) {
        var name = item.name || item.candidate_id || ("#" + (i + 1));
        var tag = item.tag ? " [" + item.tag + "]" : "";
        var cm = (has(item, "charge") || has(item, "multiplicity"))
          ? " · q" + (has(item, "charge") ? item.charge : 0) +
            "/m" + (has(item, "multiplicity") ? item.multiplicity : 1)
          : "";
        html.push('<div class="edit-radio">' + escapeHtml(name + tag + cm) + "</div>");
      });
      html.push("</div></details></div>");
      html.push('<div class="edit-banner-hint">' +
        escapeHtml(t("edit.batch_panel_hint")) + "</div>");
    } else if (kind === "stage_artifact") {
      html.push('<div class="edit-banner-hint edit-banner-source">source_job_id: ' +
        escapeHtml(String(inp.source_job_id || "—")) + "</div>");
      html.push('<div class="edit-banner-hint">from_artifact: ' +
        escapeHtml(String(inp.from_artifact || "—")) + "</div>");
    } else if (kind === "candidates" && Array.isArray(inp.candidates)) {
      html.push('<div class="edit-banner-hint">candidates: ' +
        String(inp.candidates.length) + "</div>");
      if (inp.experiment) {
        html.push('<div class="edit-banner-hint">experiment: ' +
          escapeHtml(String(inp.experiment.mode || "")) + "</div>");
      }
    } else if (kind === "scan_request" && inp.scan_request) {
      html.push('<div class="edit-banner-hint">scan_request · source: ' +
        escapeHtml(String((inp.scan_request.source || {}).source_type ||
          inputKind(inp.scan_request.source))) + "</div>");
    } else {
      var text = String(has(inp, "source") ? inp.source :
        (has(inp, "smiles") ? inp.smiles : ""));
      if (text) {
        var truncated = text.length > 600 ? text.slice(0, 600) + "\n…" : text;
        html.push('<pre class="edit-original-preview">' + escapeHtml(truncated) + "</pre>");
      }
      if (has(inp, "charge") || has(inp, "multiplicity")) {
        html.push('<div class="edit-banner-hint">charge: ' +
          (has(inp, "charge") ? String(inp.charge) : "—") +
          " · multiplicity: " +
          (has(inp, "multiplicity") ? String(inp.multiplicity) : "—") + "</div>");
      }
    }
    panel.innerHTML = html.join("");
  }

  // 「复制到结构输入并修改」：原始 XYZ 复制进结构框并切换为替换来源。
  function copyOriginalToStructureInput() {
    var ctx = editorContext;
    if (!ctx || !ctx.draft) return;
    var box = document.getElementById("modal-structure-input");
    var selected = (typeof wizardStructures !== "undefined")
      ? (wizardStructures[wizardSelectedStructureIndex] || wizardStructures[0]) : null;
    var text = selected ? String(selected.xyz || "") : "";
    if (!text || !box) return;
    box.value = text;
    setWizardInputMode("structure");
    if (typeof scheduleParseStructures === "function") scheduleParseStructures();
  }

  // ---------------------------------------------------------------------------
  // Banner（只保留上下文）/ Footer（执行方式 + 修改计数）
  // ---------------------------------------------------------------------------

  function bannerEl() { return document.getElementById("edit-recalc-banner"); }
  function footerEl() { return document.getElementById("edit-recalc-footer"); }

  function renderBanner(state, errorMessage) {
    var el = bannerEl();
    if (!el) return;
    if (state === "hidden") { el.style.display = "none"; el.innerHTML = ""; return; }
    el.style.display = "block";
    var ctx = editorContext;
    if (state === "loading") {
      el.innerHTML = '<div class="edit-banner edit-banner-loading">' + t("edit.loading_draft") + "</div>";
      return;
    }
    if (state === "error") {
      el.innerHTML = '<div class="edit-banner edit-banner-error">' +
        t("edit.load_failed", { message: errorMessage || "" }) + "</div>";
      return;
    }
    var d = ctx.draft;
    var caps = d.capabilities || {};
    if (!caps.can_edit) {
      renderViewOnlyBanner(d);
      return;
    }
    var sourceName = (ctx.sourceJob && (ctx.sourceJob.resolved_name || ctx.sourceJob.custom_name)) ||
      ctx.sourceJobId;
    var htmlParts = ['<div class="edit-banner edit-banner-ready">'];
    htmlParts.push('<div class="edit-banner-source">' +
      t("edit.banner_source", {
        name: sourceName,
        status: t("status." + d.job_status) || d.job_status,
        attempt: String(d.attempt),
      }));
    var effKey = effectiveConfigHintKey(ctx.effectiveConfig);
    if (effKey) htmlParts.push('<span class="edit-context-meta">' + t(effKey) + "</span>");
    htmlParts.push("</div>");
    if (d.migration_hint) {
      htmlParts.push('<div class="edit-banner-hint">' + t("edit.migration_hint", { hint: d.migration_hint }) + "</div>");
    }
    if ((d.missing_fields || []).length) {
      htmlParts.push('<div class="edit-banner-hint edit-warn">' +
        t("edit.missing_fields", { fields: d.missing_fields.join(", ") }) + "</div>");
    }
    if (ctx.methodBackfilled) {
      htmlParts.push('<div class="edit-banner-hint edit-warn">' +
        t("edit.method_backfilled") + "</div>");
    }
    htmlParts.push("</div>");
    el.innerHTML = htmlParts.join("");
  }

  function effectiveConfigHintKey(effectiveConfig) {
    if (!effectiveConfig || !effectiveConfig.status) return "";
    if (effectiveConfig.status === "snapshot") return "edit.effective_snapshot";
    if (effectiveConfig.status === "recomputed") return "edit.effective_recomputed";
    return "";
  }

  function hideFooter() {
    var el = footerEl();
    if (el) { el.style.display = "none"; el.innerHTML = ""; }
  }

  // 执行方式与修改计数移入弹窗底部区域（提交/取消用标准 footer 按钮）。
  function renderFooter() {
    var ctx = editorContext;
    var el = footerEl();
    if (!el || !ctx || !ctx.draft) return;
    var caps = ctx.draft.capabilities || {};
    var htmlParts = ['<div class="edit-banner-row"><span class="edit-row-label">' +
      t("edit.execution_mode") + "</span>"];
    htmlParts.push('<label class="edit-radio' + (caps.can_in_place ? "" : " disabled") + '">' +
      '<input type="radio" name="edit-exec-mode" value="in_place"' +
      (ctx.executionMode === "in_place" ? " checked" : "") +
      (caps.can_in_place ? "" : " disabled") + "> " + t("edit.exec_in_place") + "</label>");
    htmlParts.push('<label class="edit-radio"><input type="radio" name="edit-exec-mode" value="new_job"' +
      (ctx.executionMode === "new_job" ? " checked" : "") + "> " + t("edit.exec_new_job") + "</label>");
    htmlParts.push('<span class="flex-spacer"></span>');
    htmlParts.push('<span class="edit-changed-badge" id="edit-changed-badge"></span>');
    htmlParts.push("</div>");
    el.innerHTML = htmlParts.join("");
    el.style.display = "block";
    el.querySelectorAll('input[name="edit-exec-mode"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (radio.checked) { ctx.executionMode = radio.value; updateEditorUiState(); }
      });
    });
  }

  // 历史退役/planned 任务：识别 + 查看旧参数 + 解释兼容性（计划 §5.1），
  // 不重新开放已退休的执行入口。
  function renderViewOnlyBanner(d) {
    var el = bannerEl();
    if (!el) return;
    var spec = d.editable_spec || {};
    var methodKeys = (d.preserved_fields || []).join(", ") || "—";
    var reasons = (d.capabilities.disabled_reasons || [])
      .map(function (r) { return '<div class="edit-banner-hint edit-warn">' + escapeHtml(r) + "</div>"; })
      .join("");
    el.style.display = "block";
    el.innerHTML =
      '<div class="edit-banner edit-banner-ready"><div class="edit-banner-source">' +
      escapeHtml(t("edit.view_only_title", { workflow: d.workflow })) + "</div>" + reasons +
      '<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.view_only_params")) + ":</b> " +
      escapeHtml(methodKeys) + "</div>" +
      '<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.view_only_input")) + ":</b> " +
      escapeHtml(t("edit.view_only_input_kind", { kind: inputKind(spec.input) })) + "</div>" +
      (d.migration_hint ? '<div class="edit-banner-hint"><b>' + escapeHtml(t("edit.migration_hint", { hint: d.migration_hint })) + "</b></div>" : "") +
      "</div>";
  }

  function updateEditorUiState() {
    var ctx = editorContext;
    if (!ctx || !ctx.draft) return;
    var badge = document.querySelector("#edit-changed-badge");
    if (badge) {
      var n = countLocalChanges();
      badge.textContent = t("edit.changed_n", { n: String(n) });
      badge.classList.toggle("has-changes", n > 0);
    }
    var btn = modalSubmitBtn();
    if (btn && !ctx.collecting && !ctx.submitting) {
      var caps = ctx.draft.capabilities || {};
      btn.disabled = ctx.executionMode === "in_place" && !caps.can_in_place;
      btn.title = btn.disabled ? t("edit.in_place_blocked") : "";
    }
  }

  // 编辑模式下底部主按钮文案 = 检查并提交；取消/提交后恢复「提交」。
  // 移除 data-i18n 以防 applyI18n 语言切换时覆盖回「提交」。
  function updateModalSubmitButton() {
    var btn = modalSubmitBtn();
    if (!btn) return;
    if (editorContext) {
      btn.removeAttribute("data-i18n");
      btn.textContent = t("edit.check_and_submit");
    } else {
      btn.setAttribute("data-i18n", "modal.submit");
      btn.textContent = t("modal.submit");
      btn.disabled = false;
      btn.title = "";
    }
  }

  // 语言切换后的编辑器 UI 刷新（由 applyI18n 调用）。
  function refreshUi() {
    if (!editorContext) return;
    updateEditModeTabs();
    updateModalSubmitButton();
    renderFooter();
    updateEditorUiState();
    renderBanner("ready");
    renderOriginalInputPanel();
  }

  // Badge refresh: cheap interval while the editor is open — covers every
  // mutation path (resource inputs, method-config-modal saves, structure
  // re-parse) without hooking each page collector individually.
  var badgeTimer = null;

  function startBadgeWatch() {
    stopBadgeWatch();
    badgeTimer = setInterval(function () {
      if (!editorContext) { stopBadgeWatch(); return; }
      updateEditorUiState();
    }, 800);
  }

  function stopBadgeWatch() {
    if (badgeTimer !== null) { clearInterval(badgeTimer); badgeTimer = null; }
  }

  function showSummary(preview) {
    hideSummary();
    var overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.id = "edit-summary-overlay";
    var diffRows = (preview.diff || []).map(function (entry) {
      var oldV = typeof entry.old === "object" ? JSON.stringify(entry.old) : String(entry.old);
      var newV = typeof entry.new === "object" ? JSON.stringify(entry.new) : String(entry.new);
      return '<tr><td class="edit-diff-path">' + escapeHtml(entry.path) + "</td><td>" +
        escapeHtml(oldV) + "</td><td>" + escapeHtml(newV) + "</td><td>" + escapeHtml(entry.kind) + "</td></tr>";
    }).join("");
    var warns = (preview.warnings || []).map(function (w) {
      return '<div class="edit-banner-hint edit-warn">' + escapeHtml(w) + "</div>";
    }).join("");
    var isRemote = editorContext && editorContext.executionMode === "new_job";
    overlay.innerHTML =
      '<div class="modal-dialog"><div class="modal-header"><h2>' + t("edit.summary_title") +
      '</h2><button class="modal-close" id="edit-summary-x">X</button></div><div class="modal-body">' +
      '<div class="edit-summary-meta">' +
      "<div><b>" + t("edit.summary_workflow") + ":</b> " + escapeHtml(preview.workflow) + "</div>" +
      "<div><b>" + t("edit.summary_mode") + ":</b> " +
      t(isRemote ? "edit.exec_new_job" : "edit.exec_in_place") + "</div>" +
      (isRemote ? "" : '<div class="edit-warn">' + t("edit.summary_cleanup_warning") + "</div>") +
      "</div>" + warns +
      (diffRows ? '<table class="edit-diff-table"><thead><tr><th>' + t("edit.diff_field") +
        "</th><th>" + t("edit.diff_old") + "</th><th>" + t("edit.diff_new") + "</th><th>" +
        t("edit.diff_kind") + "</th></tr></thead><tbody>" + diffRows + "</tbody></table>"
        : '<div class="edit-banner-hint">' + t("edit.no_changes") + "</div>") +
      '<div class="edit-summary-error" id="edit-summary-error" style="display:none;"></div>' +
      '</div><div class="modal-footer">' +
      '<button type="button" class="btn ghost" id="edit-summary-cancel">' + t("edit.summary_back") + "</button>" +
      '<button type="button" class="btn primary" id="edit-summary-confirm">' +
      t(isRemote ? "edit.confirm_new_job" : "edit.confirm_in_place") + "</button></div></div>";
    overlay.style.display = "flex";
    document.body.appendChild(overlay);
    overlay.querySelector("#edit-summary-cancel").addEventListener("click", hideSummary);
    overlay.querySelector("#edit-summary-x").addEventListener("click", hideSummary);
    overlay.querySelector("#edit-summary-confirm").addEventListener("click", finalSubmit);
  }

  function hideSummary() {
    var el = document.getElementById("edit-summary-overlay");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function escapeHtml(text) {
    return String(text === undefined || text === null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // 导出：Workbench 只保留入口绑定（计划 §8）。
  window.ACPJobEditor = {
    isActive: isActive,
    currentMode: currentMode,
    sourceJobId: sourceJobId,
    openJobEditor: openJobEditor,
    openRerunMenu: openRerunMenu,
    closeRerunMenu: closeRerunMenu,
    interceptJobCreate: interceptJobCreate,
    cancelEditor: cancelEditor,
    checkAndSubmit: checkAndSubmit,
    onSourceTabChange: onSourceTabChange,
    onPreviewStructureSelected: onPreviewStructureSelected,
    onJobResultLoaded: onJobResultLoaded,
    copyOriginalToStructureInput: copyOriginalToStructureInput,
    updateEditModeTabs: updateEditModeTabs,
    refreshUi: refreshUi,
  };

  // 纯逻辑测试钩子（node 回归测试；生产代码不应依赖）。
  window.ACPJobEditorInternals = {
    deepCopy: deepCopy,
    deepEqual: deepEqual,
    has: has,
    presenceCopy: presenceCopy,
    inputKind: inputKind,
    isSimpleStructureInput: isSimpleStructureInput,
    cleanLevels: cleanLevels,
    batchRoleStaticDefaults: batchRoleStaticDefaults,
    mergeBatchRoles: mergeBatchRoles,
    buildBatchMethodFromPatch: buildBatchMethodFromPatch,
    adapterFor: adapterFor,
  };
})();
