/* file: js/modules/task-wizard/module.js */
/*
 * ACP 3.0 module — task-wizard (Track A / A-01 … A-09).
 *
 * Classic script, no build, no ESM. Registers exactly one global key,
 * `window.ACPNextModules["task-wizard"]` = { descriptor, factory, _internals },
 * and starts no timer, no polling loop and no 3D viewer.
 *
 * Scope:
 *   A-01 — wizard skeleton + step navigation. The workflow list and the
 *          method/basis projection come from the server catalog
 *          (`GET /api/v1/workflow-catalog`, `GET /api/v1/method-catalog`);
 *          only entries with `status:"active"` and `visible !== false` are
 *          offered and the initial selection is the first such catalog entry
 *          (never a hard-coded id, never a retired entry as a fallback).
 *   A-02 — molecular input. SMILES is resolved + embedded
 *          (`POST /api/v1/molecule/resolve` → `POST /api/v1/molecule/embed`);
 *          pasted text and files go through `POST /api/v1/structures/parse`
 *          and fall back to `POST /api/v1/molecule/embed` when the parsed
 *          asset carries no 3D geometry. A parse/validation failure shows a
 *          visible reason and never advances the wizard.
 *   A-03 — charge / spin multiplicity with the ΣZ − charge electron-count
 *          parity check. Odd/even mismatches, an over-high charge and an
 *          impossible multiplicity are explicit visible errors; the raw typed
 *          value is never normalized and an invalid state blocks `next`.
 *   A-04 — resources (memory / cores / queue). Memory and cores defaults come
 *          from the server's published admission view
 *          (`GET /api/capabilities?detail=summary` → `capacity`);
 *          `memory_input` is validated against the single server memory
 *          grammar (unit required); queue names are a read-only projection of
 *          the node catalog (`GET /api/v1/nodes`). No default is hard-coded:
 *          when the server publishes nothing the module leaves the fields
 *          empty (server defaults apply) and says so.
 *   A-05 — method validation + preflight through the existing
 *          `POST /api/v1/preflight`. The request is the same frozen
 *          workflow/input/method/resources body a submit would carry; the
 *          response `plan_digest` is shown as the preflight fingerprint.
 *          HTTP 400/422 envelopes are mapped to the field they name, a
 *          stale digest after any edit blocks the step, and no task is
 *          created by preflight.
 *   A-06 — submit + receipt through the existing `POST /api/v1/jobs` (the
 *          same route the Workbench/CLI use). The body is the frozen A-05
 *          plan plus its `preflight_fingerprint`, the `Idempotency-Key`
 *          header is generated once per plan identity and reused on every
 *          retry of that plan, and the receipt consumes
 *          `job_id`/`status`/`attempt`/`plan_digest` (+ `idempotent_replay`).
 *          Success navigates only through the injected `commands.openTask`
 *          navigation command. A 400/409/422/5xx/transport rejection is
 *          mapped to readable copy; a lost response is "unconfirmed", never
 *          success.
 *   A-08 — submit check. A pre-submit dialog summarises the local validation
 *          (workflow/schema, structure, electronic state, resources) and the
 *          preflight result as genuine blockers plus server advisories as
 *          warnings. Confirm sends the submit; cancel does not. A blocked
 *          check disables confirm and can never send a request (no false
 *          positives: optional resources, an empty queue projection and
 *          advisories never block).
 *   A-09 — create-task drafts. The wizard snapshots its own presentation /
 *          input state (workflow, method selections, typed SMILES / text /
 *          file name, raw charge & multiplicity, cores & memory, scan atoms,
 *          frozen payload, current step) into the existing
 *          `POST/PUT /api/v1/drafts` document, debounced (never per keystroke),
 *          restores it behind an explicit banner action, deletes it on an
 *          explicit discard and after a successful submit, and handles a 409
 *          `revision_conflict` by stopping autosave and offering
 *          load-latest / save-as-new-draft. The snapshot NEVER contains a
 *          task reference (source job/product/mode, receipt job id), an
 *          idempotency key, a preflight body/digest, a resolved structure or
 *          any credential: those are derived/plan state, not presentation
 *          state. A draft delete failure after a successful submit is
 *          non-fatal and surfaced — the receipt stays valid.
 *   A-07 — the remaining active workflows. Every entry the workflow catalog
 *          marks `status:"active"` is configurable in this one panel: the
 *          method schema (levels, profile values and FIELD_DEFINITIONS widgets)
 *          is projected from `GET /api/v1/method-catalog`; the per-workflow
 *          input contract comes from `GET /api/capabilities?workflow=<id>`
 *          (`acp.api.workflow_contracts.describe_workflow`, the same projection
 *          the MCP discovery surface publishes). Workflows whose schema only
 *          carries the established generic single-structure fields keep the
 *          A-01 minimal projection and wire body byte-for-byte; any schema with
 *          an extra field gets the full A-07 panel. The contract selects one of
 *          five input adapters (structure | scan | batch | source | payload):
 *          `scan` builds the simple relaxed-scan coordinate, PES builds
 *          `input.coordinate` + `input.protocol` (method.levels is refused for
 *          that workflow), batch builds a one-item `batch_structures` envelope,
 *          source workflows bind an existing task product, and frozen payload
 *          workflows ship the upstream request verbatim. A missing/empty
 *          method schema, a failing contract read or invalid typed field is an
 *          explicit local block — never a silently disabled generic form.
 *
 * Deliberately NOT implemented here: A-10+ edit/recalculate (the edit draft is
 * the job-edit module's surface). This module owns no second preflight
 * implementation and no second submission copy; A-09 drafts use only the
 * existing `/api/v1/drafts` routes and are the single persisted copy of the
 * wizard's presentation state (there is no second cache and no browser-storage
 * layer).
 *
 * Data flows only through an injected client seam:
 *   deps.client.request / deps.client (function) / deps.client.transport.request
 *   / deps.request / window.ACPClient.transport.request.
 * The module performs no direct network call of its own and needs no global
 * besides the registry table.
 *
 * Four module states live on the root `data-acp-module-state`:
 *   loading | ready | error | empty (catalog lifecycle).
 * The molecular-input surface carries its own four states on
 * `data-acp-structure-state`: empty | loading | ready | error; the electronic
 * state on `data-acp-electronic-state`: empty | ready | error; resource
 * defaults on `data-acp-resource-state`: empty | loading | ready | error; the
 * read-only queue projection on `data-acp-queue-state`: empty | loading | ready
 * | error; the A-07 workflow-contract surface on `data-acp-contract-state`:
 * idle | loading | ready | error; preflight on `data-acp-preflight-state`:
 * idle | loading | ready | error (with `data-acp-preflight-stale` /
 * `data-acp-preflight-valid`); submit on `data-acp-submit-state`: idle |
 * loading | submitted | error, and the A-08 pre-submit check on
 * `data-acp-submit-check`: ok | blocked. A-09 adds the draft surface:
 * `data-acp-draft-state`: empty | loading | ready | error | conflict (with the
 * detailed `data-acp-draft-status` and `data-acp-draft-busy`), counted in
 * `data-acp-busy` as `draft` while a draft request is in flight.
 * Every async result is guarded by the mount generation plus a per-action
 * token, so dispose / a newer input / a stale catalog or contract response can
 * never write a result into the DOM, and a late submit can never navigate. One
 * state object owns everything — no second cache.
 */
(function () {
  "use strict";

  window.ACPNextModules = window.ACPNextModules || {};

  var MODULE_ID = "task-wizard";
  var API = "/api/v1";

  /* Existing routes only (verified in src/acp/api/v1_routes.py and
   * src/acp/api/routes.py). The resolve and embed routes live under
   * `/molecule`, not `/structures`; only `parse` is under `/structures`.
   * `capabilities` is the Controller discovery read (its `capacity` block is
   * the only published cores/memory authority); `nodes` is the node catalog
   * carrying LSF queue names; `preflight` is the zero-side-effect dry run of
   * the same request a submit would carry (A-05). */
  var ENDPOINTS = {
    workflowCatalog: API + "/workflow-catalog",
    methodCatalog: API + "/method-catalog",
    /* A-07: the workflow input/method contract (`acp.api.workflow_contracts`
     * via the existing `/api/capabilities?workflow=` discovery read). One
     * request per selected extended workflow; never for retired entries. */
    workflowContract: "/api/capabilities?workflow=",
    resolve: API + "/molecule/resolve",
    embed: API + "/molecule/embed",
    parse: API + "/structures/parse",
    capabilities: "/api/capabilities?detail=summary",
    nodes: API + "/nodes",
    preflight: API + "/preflight",
    /* A-06: the one existing task-creation route (`create_job`). Reachable only
     * through the injected client seam; no second submit path. */
    jobs: API + "/jobs",
    /* A-09: the existing Workbench draft CRUD (`src/acp/api/drafts.py`):
     * GET /drafts (list, newest first), POST /drafts (create),
     * GET /drafts/{id} (summary + snapshot), PUT /drafts/{id}
     * (expected_revision CAS, 409 revision_conflict), DELETE /drafts/{id}.
     * No new route is invented here. */
    drafts: API + "/drafts"
  };

  var STEP_IDS = ["workflow", "input", "review"];
  var STEP_KEYS = {
    workflow: "next.wizard.step_workflow",
    input: "next.wizard.step_input",
    review: "next.wizard.step_review"
  };
  var INPUT_MODES = ["smiles", "paste", "file"];
  var MODE_KEYS = {
    smiles: "next.wizard.mode_smiles",
    paste: "next.wizard.mode_paste",
    file: "next.wizard.mode_file"
  };
  /* Only method/basis projection belongs to A-01. Other schema fields are
   * intentionally left to their own tasks. */
  var PROJECTED_FIELDS = ["functional", "basis", "gfn"];
  /* A-07 classification: a schema whose every level field belongs to the
   * established generic single-structure surface keeps the A-01 minimal
   * projection and wire body. Any schema that introduces another catalog field
   * (scan coordinates, PES protocol layers, IRC points, TS-mode bundles, CAS
   * active space, NMR nuclei, batch roles, …) gets the full schema-driven
   * panel. This is a field-set policy, never a workflow-id allowlist: a new
   * catalog workflow automatically lands on the right side of it. */
  var MINIMAL_SCHEMA_FIELDS = {
    functional: true,
    basis: true,
    gfn: true,
    dispersion: true,
    ri_approximation: true,
    aux_j_basis: true,
    aux_c_basis: true,
    solvent_model: true,
    solvent: true,
    grid: true,
    scf_convergence: true,
    max_steps: true,
    opt_convergence: true,
    recalc_hess: true,
    opt_level: true,
    electronic_state: true
  };
  /* Contract adapters. `structure` is the A-02 envelope; `scan` adds the
   * relaxed-scan coordinate (PES additionally carries input.protocol);
   * `batch` wraps the single structure as one batch item; `source` binds an
   * existing task product; `payload` ships a frozen upstream request. */
  var ADAPTER_STRUCTURE = "structure";
  var ADAPTER_SCAN = "scan";
  var ADAPTER_BATCH = "batch";
  var ADAPTER_SOURCE = "source";
  var ADAPTER_PAYLOAD = "payload";
  var METHOD_EMIT_LEVELS = "levels";
  var METHOD_EMIT_PROTOCOL = "protocol";
  var METHOD_EMIT_NONE = "none";
  /* Structure parsing/embedding is a long server-side operation; the classic
   * parse slice uses the same "no transport timeout" budget. */
  var REQUEST_TIMEOUT_MS = 0;
  /* A-09: the draft document is a small SQLite row. The debounce keeps a burst
   * of keystrokes down to one write; the request budget keeps a hung save from
   * pinning the draft surface forever. Both are overridable by injected deps
   * (`draftDebounceMs`, `setTimeout` / `clearTimeout`) so tests never sleep. */
  var DRAFT_KIND = "task-wizard";
  var DRAFT_VERSION = 1;
  var DRAFT_DEBOUNCE_MS = 1200;
  var DRAFT_TIMEOUT_MS = 8000;
  var DRAFT_MAX_LEVELS = 32;

  var DESCRIPTOR = {
    id: MODULE_ID,
    interfaceVersion: 1,
    version: "0.1.0",
    provides: ["task.wizard"],
    binds: [],
    capabilities: { requires: [], provides: ["task.wizard"] },
    hosts: ["acp-web"],
    supportedInstancing: "single",
    configSchemaVersion: 1,
    error: { titleKey: "next.wizard.errorTitle", messageKey: "next.wizard.errorMessage" }
  };

  /* zh fallbacks: consulted only when the shell dictionary has no entry yet.
   * Static chrome also carries data-i18n so a language switch retranslates it. */
  var ZH = {
    "next.wizard.title": "新建计算向导",
    "next.wizard.subtitle": "A-01 / A-02：选择计算类型，载入分子结构",
    "next.wizard.steps_label": "向导步骤",
    "next.wizard.step_workflow": "计算类型与方法",
    "next.wizard.step_input": "分子输入",
    "next.wizard.step_review": "确认",
    "next.wizard.back": "上一步",
    "next.wizard.next": "下一步",
    "next.wizard.catalog_loading": "正在载入工作流与方法目录…",
    "next.wizard.catalog_ready": "目录已载入：{count} 个可用计算类型。",
    "next.wizard.catalog_error": "目录加载失败：{message}",
    "next.wizard.catalog_empty": "服务端目录没有可用的计算类型（没有 active 且可见的工作流），无法继续。",
    "next.wizard.retry": "重试",
    "next.wizard.workflow_title": "计算类型",
    "next.wizard.workflow_label": "工作流（来自服务端目录）",
    "next.wizard.workflow_desc_empty": "从服务端工作流目录选择计算类型。",
    "next.wizard.method_title": "方法与基组",
    "next.wizard.method_profile": "方法预设",
    "next.wizard.method_source": "选项来自服务端方法目录；本步骤只做目录投影，不在此校验方法参数。",
    "next.wizard.method_empty": "当前层级在方法目录中没有可投影的方法/基组选项。",
    "next.wizard.level_engine": "引擎：{engine}",
    "next.wizard.field_functional": "电子结构方法",
    "next.wizard.field_basis": "基组",
    "next.wizard.field_gfn": "GFN 方法",
    "next.wizard.input_title": "分子结构",
    "next.wizard.mode_smiles": "SMILES",
    "next.wizard.mode_paste": "粘贴文本",
    "next.wizard.mode_file": "文件",
    "next.wizard.smiles_label": "SMILES 文本",
    "next.wizard.smiles_placeholder": "例如 CCO（服务端校验并生成 3D 坐标）",
    "next.wizard.smiles_action": "解析 SMILES",
    "next.wizard.paste_label": "XYZ / MOL / SDF / GJF / ORCA 输入",
    "next.wizard.paste_placeholder": "粘贴 XYZ、molfile 或多格式结构文本",
    "next.wizard.paste_action": "解析文本",
    "next.wizard.file_label": "选择结构文件",
    "next.wizard.file_hint": "支持 .xyz / .mol / .sdf / .gjf / .inp / .log / .out / .smi / .smiles。文件在浏览器内读取后交给服务端解析，不写入独立上传端点。",
    "next.wizard.file_name": "已选择：{name}",
    "next.wizard.file_none": "尚未选择文件。",
    "next.wizard.structure_empty": "尚未载入结构。",
    "next.wizard.structure_loading": "正在解析结构…",
    "next.wizard.structure_ready": "结构已载入：{formula} · {atoms} 个原子。",
    "next.wizard.structure_failed": "结构载入失败。",
    "next.wizard.structure_summary": "{formula} · {atoms} 个原子 · 电荷 {charge} · 自旋多重度 {multiplicity}",
    "next.wizard.unknown": "—",
    "next.wizard.review_title": "确认输入",
    "next.wizard.review_workflow": "计算类型",
    "next.wizard.review_method": "方法 / 基组",
    "next.wizard.review_structure": "分子结构",
    "next.wizard.review_missing": "—",
    "next.wizard.hint_catalog_loading": "目录仍在载入，请稍候。",
    "next.wizard.hint_catalog_error": "目录加载失败，请先重试。",
    "next.wizard.hint_no_workflow": "服务端目录没有可用计算类型。",
    "next.wizard.hint_structure_loading": "正在解析结构，请稍候。",
    "next.wizard.hint_structure_error": "结构载入失败，请修正输入后重试。",
    "next.wizard.err.workflow_required": "请选择一个计算类型。",
    "next.wizard.err.schema_missing": "计算类型 {workflow} 在方法目录中没有 schema，无法投影方法与基组。",
    "next.wizard.err.structure_required": "请先载入有效的分子结构。",
    "next.wizard.err.smiles_required": "请先输入 SMILES 文本。",
    "next.wizard.err.text_required": "请先粘贴结构文本。",
    "next.wizard.err.file_empty": "文件内容为空。",
    "next.wizard.err.file_unsupported": "当前环境无法读取本地文件（缺少 FileReader）。",
    "next.wizard.err.file_read_failed": "文件读取失败：{message}",
    "next.wizard.err.resolve_failed": "SMILES 解析失败：{message}",
    "next.wizard.err.resolve_empty": "服务端没有返回可用的 SMILES 结果。",
    "next.wizard.err.embed_failed": "3D 坐标生成失败：{message}",
    "next.wizard.err.embed_no_geometry": "服务端没有返回 3D 坐标。",
    "next.wizard.err.parse_failed": "结构解析失败：{message}",
    "next.wizard.err.parse_no_structures": "服务端没有返回可用结构。",
    "next.wizard.err.no_geometry": "解析结果没有 3D 坐标，也没有可用于坐标生成（embed）的 molfile/SMILES。",
    "next.wizard.errorTitle": "新建计算向导不可用",
    "next.wizard.errorMessage": "task-wizard 模块加载失败，请查看控制台。",
    "next.wizard.client_missing": "结构客户端未注入（deps.client.request / ACPClient.transport），无法解析结构。",
    /* A-03/A-04/A-05 copy reuses the shared A-61 dictionary keys that already
     * exist in BOTH shell languages (`frontend/js/shell/i18n.js`). The shell
     * dictionaries are owned by the i18n batch and are outside this module's
     * file boundary, so this module adds NO private `next.wizard.*` key: every
     * string it can emit resolves in zh + en today and the
     * `acp2_i18n_new_modules` completeness guard stays green. See README §5. */
    "next.config.charge": "电荷",
    "next.config.multiplicity": "自旋多重度",
    "next.config.nproc": "核数",
    "next.config.mem": "内存",
    "next.config.errors": "需要修正",
    "next.config.retry": "重试",
    "next.config.validate": "预检",
    "next.config.preflighting": "预检中…",
    "next.config.preflight_failed": "预检未通过",
    "next.config.preflight_retry": "重试预检",
    "next.config.stale": "配置已修改，请重新预检。",
    "next.config.receipt_digest": "计划摘要 {digest}",
    /* A-06 submit / receipt + A-08 submit check. Every key below already exists
     * in BOTH shell dictionaries; the module adds no private `next.*` key. */
    "next.config.review": "4 · 预检与提交",
    "next.config.submit": "提交任务",
    "next.config.submitting": "提交中…",
    "next.config.submit_blocked": "预检通过后才能提交",
    "next.config.receipt": "已提交",
    "next.config.receipt_body": "任务 {jobId} · 状态 {status} · 第 {attempt} 次尝试",
    "next.config.open_task": "在任务面板打开",
    "next.config.submit_failed": "提交失败：{message}",
    "next.config.plan_stale": "预检之后配置发生变化，请重新预检。",
    "next.config.forbidden": "当前身份无权提交任务。",
    "next.tasks.dialog.cancel": "取消",
    "next.capabilityForm.submit.unconfirmed": "提交结果未确认：{message}。请先查询任务状态，勿盲目重复提交。",
    "next.capabilityForm.submit.conflict": "预检计划或审批已在服务端变更（409）。已刷新预检并解除审批绑定；请重新预检、重新审批。",
    "next.capabilityForm.code.validation_failed": "提交内容未通过服务端校验",
    "next.capabilityForm.code.submit_busy": "已有预检或提交正在进行中",
    "next.s2scan.receipt_replay": "幂等重放：返回的是同一任务的既有回执。",
    "next.config.load_error": "目录加载失败：{message}",
    "next.config.error.structure_required": "请先载入结构。",
    "next.config.error.charge_integer": "电荷必须是整数。",
    "next.config.error.multiplicity_integer": "自旋多重度必须是 ≥1 的整数。",
    "next.config.error.multiplicity_parity": "电子数为 {electrons}，自旋多重度奇偶不匹配。",
    "next.config.error.nproc": "核数必须是 1–256 的整数。",
    "next.s2scan.parity_ok": "电荷/多重度奇偶检查通过（{electrons} 个电子）。",
    "next.s2scan.parity_unverifiable": "元素不在内置周期表子集内，奇偶检查跳过（不误报）。",
    "next.s2scan.preflight_idle": "尚未预检。预检由服务端零副作用执行。",
    "next.s2scan.preflight_ok": "预检通过：{digest}",
    "next.s2scan.preflight_failed": "预检未通过",
    "next.s2scan.preflight_error": "预检请求失败：{message}",
    "next.s2scan.preflight_blocked": "参数未通过校验，未发送预检。",
    "next.s2scan.preflight_edited": "配置已修改，请重新预检。",
    "next.s2scan.advisories": "预检提示",
    "next.capabilityForm.preflight_fingerprint": "预检指纹",
    "next.capabilityForm.preflight.stale": "配置已变更，请重新预检。",
    "next.capabilityForm.code.preflight_stale": "预检已过期",
    "next.capabilityForm.code.ui_value_format": "格式不符合要求",
    "next.nodes.meta_queue": "队列",
    "next.nodes.section_error": "{section}加载失败：{message}",
    "next.nodes.not_configured": "未配置远程节点（服务端未启用远程执行）",
    "next.approval.plan.resources": "资源",
    /* A-09 draft chrome. All of these are shared A-61 dictionary keys that
     * already exist in BOTH shell languages; the module adds no private
     * `next.*` key (the `next.wizard.local.*` fallback stays reserved for the
     * zh-only LOCAL copy, see README §10). */
    "next.config.draft_found": "发现保存的草稿（{when}）",
    "next.config.draft_restore": "恢复",
    "next.config.draft_discard": "丢弃",
    "next.config.draft_saving": "正在保存草稿…",
    "next.config.draft_saved": "草稿已保存",
    "next.config.draft_conflict": "草稿已在其他地方更新",
    "next.config.draft_reload": "载入最新",
    "next.config.draft_fork": "另存为新草稿",
    "next.config.draft_error": "草稿保存失败：{message}",
    "next.tasks.action_failed": "{action}失败：{message}",
    "next.approval.status.draft": "草稿"
  };

  /* zh-only literals that have no shared dictionary key yet (the shell zh/en
   * dictionaries are owned by the i18n batch). Kept in one place, never a
   * `next.*` key, so the completeness guard sees no missing dictionary entry.
   * README §5 lists them as the pending A-61 key request. */
  var LOCAL = {
    electronic_title: "电子状态（电荷 / 自旋多重度）",
    charge_too_high: "电荷过高：没有剩余电子。",
    multiplicity_too_high: "自旋多重度 {multiplicity} 超过当前电子数允许的上限 {limit}。",
    resources_loading: "正在载入资源默认值…",
    resources_empty: "服务端没有发布资源默认值；留空表示使用服务端默认值。",
    resources_unavailable: "资源默认值不可用。",
    cores_too_many: "核数 {cores} 超过服务端上限 {max}。",
    memory_too_large: "内存 {memory} 超过服务端上限 {max}。",
    memory_format: "内存需要带单位：例如 8GB、512MB、2T。",
    queue_hint: "队列来自服务端节点配置，只读；本模块不发送队列字段。",
    queue_empty: "没有可显示的队列。",
    /* A-07: schema-driven workflow input. All of these are zh-only local
     * strings (the shell zh/en dictionaries are outside this file's boundary);
     * field labels themselves come from the server catalog. */
    workflow_input_title: "工作流专用输入",
    workflow_input_hint: "字段来自服务端工作流输入契约；缺失或失败时本模块显式阻塞，不回退到通用表单。",
    contract_loading: "正在读取工作流输入契约…",
    contract_error: "工作流输入契约读取失败：{message}",
    contract_not_provided: "服务端尚未发布该工作流的输入契约；方法层级仍来自方法目录。",
    contract_coverage: "输入契约覆盖：{coverage}",
    contract_required: "扩展工作流需要先读取服务端输入契约。",
    contract_retry: "重试",
    payload_label: "冻结请求 JSON（{key}）",
    payload_hint: "上游冻结请求原样提交；本模块不补科学默认值。",
    payload_placeholder: "{\n  \"request_version\": \"...\"\n}",
    payload_required: "请粘贴 {key} 的 JSON 请求。",
    payload_invalid: "{key} 不是合法 JSON 对象：{message}",
    payload_summary: "冻结请求 {key}（{count} 个键）",
    source_job: "源任务 Job ID",
    source_job_placeholder: "例如 20261031_001_BatchOptimize",
    source_product: "源产物 / TS 候选 ID",
    source_product_placeholder: "例如 ts_candidate",
    source_mode_index: "频率模式原始序号",
    source_mode_hint: "使用 ORCA 输出的原始模式编号，不是列表位置。",
    source_required: "请填写源任务 Job ID。",
    source_mode_invalid: "频率模式序号必须是非负整数。",
    source_summary: "源任务 {jobId}",
    scan_atoms: "扫描原子（0 基，逗号分隔）",
    scan_atoms_placeholder: "例如 0,1",
    scan_atoms_hint: "按坐标类型输入 2 / 3 / 4 个原子序号；本模块不自动选点。",
    scan_atoms_required: "请填写扫描原子。",
    scan_atoms_invalid: "扫描原子必须是 {count} 个互不相同的非负整数。",
    scan_atoms_out_of_range: "原子序号 {atom} 超出结构范围（共 {count} 个原子）。",
    batch_note: "BatchOptimize 每个任务只提交一个结构（单结构批次）。",
    method_schema_empty: "该方法 schema 没有可配置层级。",
    method_invalid_number: "{label} 必须是数字。",
    method_invalid_integer: "{label} 必须是整数。",
    method_out_of_range: "{label} 超出目录范围 {min}–{max}。",
    method_invalid_option: "{label} 的值不在目录选项中。",
    field_not_consumed: "该字段不进入本工作流的提交体（目录保留，服务端未消费）。",
    /* A-09 draft literals without a shared dictionary key yet (the shell
     * zh/en dictionaries are owned by the A-61 batch and outside this module's
     * file boundary). Everything else the draft surface emits reuses the
     * shared `next.config.draft_*` / `next.tasks.action_failed` /
     * `next.nodes.section_error` keys, so only these two are zh-only. */
    draft_restored: "草稿已恢复",
    draft_deleting: "正在删除草稿…"
  };

  /* ── shared helpers ─────────────────────────────────────────────── */

  function formatText(value, vars) {
    var text = value == null ? "" : String(value);
    if (!vars) return text;
    Object.keys(vars).forEach(function (name) {
      text = text.split("{" + name + "}").join(String(vars[name]));
    });
    return text;
  }

  function t(key, vars) {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    var value = key;
    if (shell && typeof shell.t === "function") {
      var translated = shell.t(key, vars);
      if (translated && translated !== key) return translated;
    }
    value = Object.prototype.hasOwnProperty.call(ZH, key) ? ZH[key] : key;
    return formatText(value, vars);
  }

  /** zh-only literal fallback; shell dictionary wins when the key is present. */
  function lt(name, vars) {
    var key = "next.wizard.local." + name;
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.has === "function" && shell.has(key) &&
        typeof shell.t === "function") {
      return shell.t(key, vars);
    }
    return formatText(Object.prototype.hasOwnProperty.call(LOCAL, name) ? LOCAL[name] : name, vars);
  }

  function lang() {
    var shell = typeof window !== "undefined" && window.ACPShell ? window.ACPShell.i18n : null;
    if (shell && typeof shell.getLang === "function") return shell.getLang() === "en-US" ? "en" : "zh";
    return "zh";
  }

  function en() {
    return lang() === "en";
  }

  function el(doc, tag, cls, text) {
    var node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function i18nEl(doc, tag, cls, key, vars) {
    var node = el(doc, tag, cls, t(key, vars));
    node.setAttribute("data-i18n", key);
    return node;
  }

  function clear(node) {
    if (!node) return;
    if (typeof node.replaceChildren === "function") node.replaceChildren();
    else node.textContent = "";
  }

  function describeError(error) {
    if (!error) return "unknown error";
    var body = error.body;
    if (body && typeof body === "object") {
      var detail = body.detail;
      if (detail && typeof detail === "object") {
        var code = detail.code ? String(detail.code) : "";
        var message = detail.message ? String(detail.message) : "";
        if (code || message) return (code ? code + ": " : "") + (message || JSON.stringify(detail));
      }
      if (typeof detail === "string" && detail) return detail;
    }
    return error.message ? String(error.message) : String(error);
  }

  function textOf(value) {
    return value == null ? "" : String(value);
  }

  function optionLabel(entry) {
    if (!entry) return "";
    return en() ? textOf(entry.label || entry.id) : textOf(entry.labelZh || entry.label || entry.id);
  }

  function optionDescription(entry) {
    if (!entry) return "";
    return en() ? textOf(entry.description) : textOf(entry.descriptionZh || entry.description);
  }

  /* ── electronic state (pure) ────────────────────────────────────── */

  /* Symbols 1..103 (index + 1 == atomic number). A local display/validation
   * constant only: an element outside the table makes the parity check
   * unverifiable — never a guessed atomic number and never a false error. */
  var ELEMENT_SYMBOLS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni " +
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe " +
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg " +
    "Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
  ).split(" ");

  var INTEGER_RE = /^[+-]?\d+$/;
  var MEMORY_RE = /^((?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)(TB|GB|MB|T|G|M)$/i;
  var MEMORY_FACTORS = { MB: 1, GB: 1024, TB: 1024 * 1024 };

  function atomicNumber(symbol) {
    var key = textOf(symbol).trim();
    if (!key) return null;
    var lookup = key.charAt(0).toUpperCase() + key.slice(1).toLowerCase();
    var index = ELEMENT_SYMBOLS.indexOf(lookup);
    return index < 0 ? null : index + 1;
  }

  /** Exact integer text -> number, or null. Never coerces "2.0"/"2e0". */
  function integerText(value) {
    var raw = textOf(value).trim();
    if (!INTEGER_RE.test(raw)) return null;
    var number = parseInt(raw, 10);
    return isFinite(number) ? number : null;
  }

  /**
   * Parse a Hill-style formula ("C2H6O") into symbol counts, or null when any
   * token is not a plain element/count pair (charges, dots, parentheses make
   * the electron count unverifiable instead of guessed).
   */
  function parseFormula(formula) {
    var text = textOf(formula).trim();
    if (!text) return null;
    var counts = {};
    var index = 0;
    while (index < text.length) {
      var match = /^([A-Z][a-z]?)(\d*)/.exec(text.slice(index));
      if (!match) return null;
      var count = match[2] ? parseInt(match[2], 10) : 1;
      if (!isFinite(count) || count <= 0) return null;
      counts[match[1]] = (counts[match[1]] || 0) + count;
      index += match[0].length;
    }
    return index === text.length && Object.keys(counts).length ? counts : null;
  }

  /**
   * Electron count = ΣZ − charge, or null when the formula is missing,
   * unparseable or contains an element outside the local table. The charge is
   * applied exactly as typed (no clamping, no silent re-signing).
   */
  function electronCount(formula, charge) {
    var counts = parseFormula(formula);
    if (!counts) return null;
    var total = 0;
    var symbols = Object.keys(counts);
    for (var i = 0; i < symbols.length; i += 1) {
      var z = atomicNumber(symbols[i]);
      if (z === null) return null;
      total += z * counts[symbols[i]];
    }
    return total - (Number(charge) || 0);
  }

  function errorText(err) {
    if (!err) return "";
    if (err.text) return formatText(err.text, err.vars);
    return t(err.key, err.vars);
  }

  /**
   * Validate charge / multiplicity against the parsed formula.
   *
   * Rules (in order): the charge must be an integer; the multiplicity must be
   * an integer ≥ 1; then, when the electron count is computable, electrons
   * must be ≥ 0, the multiplicity may not exceed electrons + 1, and its parity
   * must match the electron parity (even e⁻ → odd multiplicity). The typed
   * value is never rewritten.
   *
   * @returns {{ok:boolean, errors:Array<{field:string,key?:string,text?:string,vars?:object}>,
   *            electrons:(number|null), parityChecked:boolean}}
   */
  function validateElectronic(chargeRaw, multiplicityRaw, structure) {
    var errors = [];
    var chargeText = textOf(chargeRaw).trim();
    var charge = integerText(chargeText);
    if (chargeText === "" || charge === null) {
      errors.push({ field: "charge", key: "next.config.error.charge_integer" });
    }
    var multText = textOf(multiplicityRaw).trim();
    var mult = integerText(multText);
    if (multText === "" || mult === null || mult < 1) {
      errors.push({ field: "multiplicity", key: "next.config.error.multiplicity_integer" });
    }
    var electrons = null;
    var parityChecked = false;
    if (!errors.length && structure) {
      electrons = electronCount(structure.formula, charge);
      if (electrons !== null) {
        if (electrons < 0) {
          errors.push({ field: "charge", text: lt("charge_too_high") });
        } else {
          parityChecked = true;
          var limit = electrons + 1;
          if (mult > limit) {
            errors.push({
              field: "multiplicity",
              text: lt("multiplicity_too_high", { multiplicity: mult, limit: limit })
            });
          } else {
            var expectedParity = electrons % 2 === 0 ? 1 : 0;
            if (mult % 2 !== expectedParity) {
              errors.push({
                field: "multiplicity",
                key: "next.config.error.multiplicity_parity",
                vars: { electrons: electrons }
              });
            }
          }
        }
      }
    }
    return { ok: errors.length === 0, errors: errors, electrons: electrons, parityChecked: parityChecked };
  }

  /* ── resources (pure) ───────────────────────────────────────────── */

  function finiteNumber(value) {
    if (value == null || value === "" || typeof value === "boolean") return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function firstPositive() {
    for (var i = 0; i < arguments.length; i += 1) {
      var number = finiteNumber(arguments[i]);
      if (number !== null && number > 0) return number;
    }
    return null;
  }

  /** Server memory in MB -> the single unit-suffixed display form ("24576MB"). */
  function formatMemoryMb(mb) {
    var number = finiteNumber(mb);
    if (number === null || number <= 0) return "";
    var rounded = Math.round(number);
    if (Math.abs(number - rounded) < 1e-9) return String(rounded) + "MB";
    return String(number) + "MB";
  }

  /**
   * Project the admission-view capacity block into resource defaults.
   *
   * The only authority is `GET /api/capabilities`'s `capacity` block
   * (`acp.api.capacity.build_capacity_projection`): `available` (free now),
   * then `pool` (total), then `limits` (hard ceilings), in that order — all
   * numbers come from the server. Nothing is invented: an absent block yields
   * empty defaults and `published:false`.
   *
   * @returns {{cores:string, coresMax:(number|null), memoryMb:string,
   *            memoryMaxMb:(number|null), unknown:string[], published:boolean}}
   */
  function projectResourceDefaults(capabilitiesBody) {
    var out = {
      cores: "",
      coresMax: null,
      memoryMb: "",
      memoryMaxMb: null,
      unknown: [],
      published: false
    };
    var body = capabilitiesBody && typeof capabilitiesBody === "object" ? capabilitiesBody : {};
    var capacity = body.capacity && typeof body.capacity === "object" ? body.capacity : null;
    if (!capacity) return out;
    var limits = capacity.limits && typeof capacity.limits === "object" ? capacity.limits : {};
    var pool = capacity.pool && typeof capacity.pool === "object" ? capacity.pool : {};
    var available = capacity.available && typeof capacity.available === "object" ? capacity.available : {};
    var topLimits = body.limits && typeof body.limits === "object" ? body.limits : {};
    var maxCores = finiteNumber(limits.max_cores);
    if (maxCores === null) maxCores = finiteNumber(topLimits.max_cores);
    if (maxCores !== null && maxCores > 0) out.coresMax = maxCores;
    var maxMemory = finiteNumber(limits.max_memory_mb);
    if (maxMemory !== null && maxMemory > 0) out.memoryMaxMb = maxMemory;
    var cores = firstPositive(available.cores, pool.cores, out.coresMax);
    if (cores !== null) {
      out.cores = String(Math.round(cores));
      out.published = true;
    }
    var memoryMb = firstPositive(available.memory_mb, pool.memory_mb, out.memoryMaxMb);
    if (memoryMb !== null) {
      var formatted = formatMemoryMb(memoryMb);
      if (formatted) {
        out.memoryMb = formatted;
        out.published = true;
      }
    }
    out.unknown = Array.isArray(capacity.unknown)
      ? capacity.unknown.map(function (name) { return textOf(name); })
      : [];
    return out;
  }

  /** Node catalog -> the read-only queue projection (server order, deduped). */
  function projectQueues(nodesBody) {
    var body = nodesBody && typeof nodesBody === "object" ? nodesBody : {};
    var nodes = Array.isArray(body.nodes) ? body.nodes : [];
    var rows = [];
    nodes.forEach(function (node) {
      if (!node || typeof node !== "object") return;
      var queue = textOf(node.queue).trim();
      rows.push({
        name: textOf(node.name),
        status: textOf(node.status),
        queue: queue
      });
    });
    return rows;
  }

  /**
   * Normalize a memory input to the server's unit-suffixed form.
   *
   * Mirrors `cccp.utils.resource_utils` (whitespace stripped, decimal/exponent
   * amount, TB/GB/MB/T/G/M, positive finite) with one deliberate UI rule: the
   * unit is REQUIRED here, so "16" is refused instead of relying on the
   * server's unitless=GB fallback. The unit resolves visibly ("16gb"→"16GB").
   *
   * @returns {(string|null)} e.g. "8GB", "512MB", "2TB"
   */
  function normalizeMemory(value) {
    var text = textOf(value).trim().replace(/\s+/g, "");
    if (!text) return null;
    var match = MEMORY_RE.exec(text);
    if (!match) return null;
    var amount = Number(match[1]);
    if (!isFinite(amount) || amount <= 0) return null;
    var unit = match[2].toUpperCase();
    if (unit.length === 1) unit += "B";
    if (!Object.prototype.hasOwnProperty.call(MEMORY_FACTORS, unit)) return null;
    return match[1] + unit;
  }

  function memoryToMb(normalized) {
    var match = MEMORY_RE.exec(textOf(normalized));
    if (!match) return null;
    var unit = match[2].toUpperCase();
    if (unit.length === 1) unit += "B";
    var factor = MEMORY_FACTORS[unit];
    if (!factor) return null;
    return Number(match[1]) * factor;
  }

  /**
   * Validate the optional resource inputs. Empty means "unset": the server
   * default applies, which is not an error. A supplied value must parse and
   * stay within the ceilings the server published.
   *
   * @returns {{ok:boolean, errors:Array, cores:(number|null), mem:(string|null)}}
   */
  function validateResources(coresRaw, memoryRaw, limits) {
    limits = limits || {};
    var errors = [];
    var cores = null;
    var mem = null;
    var coresText = textOf(coresRaw).trim();
    if (coresText !== "") {
      var parsedCores = integerText(coresText);
      if (parsedCores === null || parsedCores < 1) {
        errors.push({ field: "cores", key: "next.config.error.nproc" });
      } else if (limits.coresMax != null && parsedCores > limits.coresMax) {
        errors.push({
          field: "cores",
          text: lt("cores_too_many", { cores: parsedCores, max: limits.coresMax })
        });
      } else {
        cores = parsedCores;
      }
    }
    var memoryText = textOf(memoryRaw).trim();
    if (memoryText !== "") {
      var normalized = normalizeMemory(memoryText);
      if (!normalized) {
        errors.push({ field: "memory", key: "next.capabilityForm.code.ui_value_format" });
      } else {
        var memoryMb = memoryToMb(normalized);
        if (limits.memoryMaxMb != null && memoryMb !== null && memoryMb > limits.memoryMaxMb) {
          errors.push({
            field: "memory",
            text: lt("memory_too_large", {
              memory: normalized,
              max: formatMemoryMb(limits.memoryMaxMb)
            })
          });
        } else {
          mem = normalized;
        }
      }
    }
    return { ok: errors.length === 0, errors: errors, cores: cores, mem: mem };
  }

  /* ── preflight request + error mapping (pure) ───────────────────── */

  function stableStringify(value) {
    if (value === undefined) return "null";
    if (value === null || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) {
      return "[" + value.map(function (item) { return stableStringify(item); }).join(",") + "]";
    }
    var keys = Object.keys(value).sort();
    return "{" + keys.map(function (key) {
      return JSON.stringify(key) + ":" + stableStringify(value[key]);
    }).join(",") + "}";
  }

  /** Canonical decimal text for a float field (the digest forbids JSON floats). */
  function canonicalFloatText(value) {
    if (value === "" || value === null || value === undefined || typeof value === "boolean") {
      return value;
    }
    var number = Number(value);
    if (!isFinite(number)) return textOf(value);
    var text = String(number);
    if (/e/i.test(text)) {
      text = number.toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
    }
    return text;
  }

  /** One level dict per schema level id: engine + the projected field rows. */
  function preflightLevels(method) {
    var levels = {};
    var definitions = method && Array.isArray(method.levels) ? method.levels : [];
    definitions.forEach(function (level) {
      if (!level || !level.levelId) return;
      var out = {};
      if (level.engine) out.engine = level.engine;
      (level.rows || []).forEach(function (row) {
        if (!row || !row.field) return;
        var value = row.value;
        if (value === "" || value == null || value === undefined) return;
        if (Array.isArray(value) && !value.length) return;
        if (row.type === "int") {
          var integer = numberFieldValue(value);
          if (integer === null) return;
          value = integer;
        } else if (row.type === "float") {
          value = canonicalFloatText(value);
        }
        out[row.field] = value;
      });
      if (Object.keys(out).length) levels[level.levelId] = out;
    });
    return levels;
  }

  /**
   * Build the frozen preflight body: the same workflow/input/method/resources
   * shape a submit would carry (the server runs the identical field policy).
   * `resources` is omitted when the user left both fields empty so the server
   * default applies instead of a client-invented number.
   */
  function buildPreflightRequest(input) {
    input = input || {};
    if (input.adapter) {
      var built = buildWorkflowRequest(input);
      return built && built.request ? built.request : null;
    }
    var structure = input.structure;
    var entry = input.entry;
    if (!entry || !structure || !structure.xyz) return null;
    var charge = integerText(input.charge);
    var multiplicity = integerText(input.multiplicity);
    if (charge === null || multiplicity === null) return null;
    var request = {
      schema_version: 1,
      workflow: textOf(entry.id),
      input: {
        source_type: "xyz_text",
        source: textOf(structure.xyz),
        charge: charge,
        multiplicity: multiplicity
      },
      method: {
        schema_id: textOf(input.method && input.method.schemaId),
        levels: preflightLevels(input.method)
      }
    };
    var resources = {};
    if (input.cores != null) resources.nproc = input.cores;
    if (input.mem) resources.mem = input.mem;
    if (Object.keys(resources).length) request.resources = resources;
    if (input.projectId) request.project_id = textOf(input.projectId);
    return request;
  }

  /** Identity of the frozen plan a preflight digest is bound to. */
  function planIdentity(request) {
    if (!request) return "";
    return stableStringify({
      workflow: request.workflow,
      input: request.input,
      method: request.method,
      resources: request.resources || {}
    });
  }

  function reasonFromDetail(detail) {
    if (!detail || typeof detail !== "object" || Array.isArray(detail)) return null;
    var params = detail.params && typeof detail.params === "object" ? detail.params : {};
    return {
      code: textOf(detail.code),
      message: textOf(detail.message),
      field: textOf(params.field),
      rule: textOf(params.rule)
    };
  }

  /** Bucket reasons by their declared field; untagged reasons go under "". */
  function fieldErrorsFrom(reasons) {
    var errors = {};
    (reasons || []).forEach(function (reason) {
      if (!reason) return;
      var message = textOf(reason.message) || textOf(reason.code);
      if (!message) return;
      var field = textOf(reason.field);
      if (errors[field]) errors[field] = errors[field] + "；" + message;
      else errors[field] = message;
    });
    return errors;
  }

  /**
   * Map a preflight rejection onto the field it names.
   *
   * Handles the three bodies the API can return: the application envelope
   * `{code,message,params:{field,rule}}` (400/422 from `_command_error`), the
   * FastAPI request-validation list (`detail:[{loc,msg,type}]`, 422), and a
   * plain string detail. The HTTP status is preserved so callers can tell a
   * 400 shape error from a 422 field-policy error.
   *
   * @returns {{status:(number|null), code:string, message:string, field:string,
   *            rule:string, reasons:Array, fieldErrors:object}}
   */
  function parsePreflightError(error) {
    var out = {
      status: null,
      code: "",
      message: "",
      field: "",
      rule: "",
      reasons: [],
      fieldErrors: {}
    };
    if (!error) {
      out.message = t("next.s2scan.preflight_error", { message: "unknown error" });
      out.reasons = [{ code: "", message: out.message, field: "", rule: "" }];
      out.fieldErrors = { "": out.message };
      return out;
    }
    if (typeof error.status === "number") out.status = error.status;
    out.message = describeError(error);
    var detail = error.body && typeof error.body === "object" ? error.body.detail : null;
    if (Array.isArray(detail)) {
      detail.forEach(function (item) {
        if (!item || typeof item !== "object") return;
        var loc = Array.isArray(item.loc) ? item.loc.slice() : [];
        if (loc[0] === "body") loc = loc.slice(1);
        out.reasons.push({
          code: textOf(item.type),
          message: textOf(item.msg) || JSON.stringify(item),
          field: loc.join("."),
          rule: textOf(item.type)
        });
      });
    } else if (detail && typeof detail === "object") {
      var reason = reasonFromDetail(detail);
      if (reason) out.reasons.push(reason);
    } else if (typeof detail === "string" && detail) {
      out.reasons.push({ code: "", message: detail, field: "", rule: "" });
    }
    if (!out.reasons.length) {
      out.reasons.push({ code: "", message: out.message, field: "", rule: "" });
    }
    out.code = textOf(out.reasons[0].code);
    out.field = textOf(out.reasons[0].field);
    out.rule = textOf(out.reasons[0].rule);
    out.fieldErrors = fieldErrorsFrom(out.reasons);
    return out;
  }

  function normalizeReasons(reasons) {
    if (!Array.isArray(reasons)) return [];
    return reasons.map(function (reason) {
      if (!reason || typeof reason !== "object") {
        return { code: "", message: textOf(reason), field: "", rule: "" };
      }
      var params = reason.params && typeof reason.params === "object" ? reason.params : {};
      return {
        code: textOf(reason.code),
        message: textOf(reason.message) || textOf(reason.code),
        field: textOf(reason.field || params.field),
        rule: textOf(reason.rule || params.rule)
      };
    });
  }

  function normalizeAdvisories(advisories) {
    if (!Array.isArray(advisories)) return [];
    return advisories.map(function (item) {
      if (item && typeof item === "object") {
        return textOf(item.message) || textOf(item.code) || JSON.stringify(item);
      }
      return textOf(item);
    }).filter(function (text) { return !!text; });
  }

  /* ── submit check + submit error mapping (pure, A-06/A-08) ──────── */

  /**
   * Freeze the submit body: exactly the preflighted plan plus the digest it was
   * preflighted under. The server compares `preflight_fingerprint` with the
   * digest of the canonical request (`create_job`), so the body must be the
   * frozen A-05 request, never a re-derived later state.
   *
   * @returns {(object|null)} null when there is no plan or no digest.
   */
  function buildSubmitBody(request, fingerprint) {
    if (!request || typeof request !== "object" || Array.isArray(request)) return null;
    var digest = textOf(fingerprint).trim();
    if (!digest) return null;
    var body = JSON.parse(JSON.stringify(request));
    body.preflight_fingerprint = digest;
    return body;
  }

  /**
   * Normalize `POST /jobs`'s `V1JobCreatedResponse` into the receipt the UI
   * owns. A response without `job_id` is not a receipt (never success); the
   * four fields the receipt consumes are copied verbatim.
   */
  function normalizeReceipt(body) {
    if (!body || typeof body !== "object" || Array.isArray(body)) return null;
    var jobId = textOf(body.job_id).trim();
    if (!jobId) return null;
    var attemptNumber = Number(body.attempt);
    var attempt = isFinite(attemptNumber) && attemptNumber >= 1 ? Math.floor(attemptNumber) : 1;
    return {
      jobId: jobId,
      status: textOf(body.status),
      attempt: attempt,
      planDigest: textOf(body.plan_digest),
      replay: body.idempotent_replay === true,
      idempotencyKey: textOf(body.idempotency_key)
    };
  }

  function errorDetailOf(error) {
    var body = error && error.body;
    var detail = body && typeof body === "object" ? body.detail : null;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) return detail;
    return null;
  }

  function submitErrorCode(error) {
    var detail = errorDetailOf(error);
    if (!detail) return "";
    var code = textOf(detail.code);
    return code || textOf(detail.error);
  }

  /**
   * Map a `POST /jobs` rejection to readable copy and a stable machine branch.
   *
   * Handles `{detail:{code,message,params:{field,rule}}}` (the application
   * envelope from `_command_error`), the FastAPI validation list, a plain
   * string detail, and a transport failure with no status (an unknown outcome:
   * "unconfirmed", never success and never a definite failure).
   *
   * @returns {{status:(number|null), code:string, field:string, rule:string,
   *            message:string, key:string, vars:object, text:string,
   *            retryable:boolean}}
   */
  function mapSubmitError(error) {
    var detail = errorDetailOf(error);
    var params = detail && detail.params && typeof detail.params === "object" ? detail.params : {};
    var status = error && typeof error.status === "number" ? error.status : null;
    var code = submitErrorCode(error);
    var serverMessage = detail ? textOf(detail.message) : "";
    var described = describeError(error);
    var message = serverMessage || described;
    var out = {
      status: status,
      code: code,
      field: textOf(params.field),
      rule: textOf(params.rule),
      message: message,
      key: "next.config.submit_failed",
      vars: { message: message },
      retryable: false,
      text: ""
    };
    if (status === null) {
      /* No HTTP answer: the submit outcome is unknown. Reusing the same
       * Idempotency-Key on the retry is what makes the retry safe. */
      out.key = "next.capabilityForm.submit.unconfirmed";
      out.vars = { message: described };
      out.retryable = true;
    } else if (status === 400 && code === "preflight_stale") {
      out.key = "next.config.plan_stale";
      out.vars = {};
    } else if (status === 400) {
      out.key = "next.config.submit_failed";
    } else if (status === 401 || status === 403) {
      out.key = "next.config.forbidden";
      out.vars = {};
    } else if (status === 409) {
      if (code === "idempotency_in_progress") {
        out.key = "next.capabilityForm.code.submit_busy";
        out.vars = {};
        out.retryable = true;
      } else {
        out.key = "next.capabilityForm.submit.conflict";
        out.vars = {};
      }
    } else if (status === 422) {
      out.key = "next.capabilityForm.code.validation_failed";
      out.vars = {};
    } else if (status >= 500) {
      out.key = "next.config.submit_failed";
      out.vars = { message: message };
      out.retryable = true;
    }
    var templated = out.key === "next.config.submit_failed" ||
      out.key === "next.capabilityForm.submit.unconfirmed";
    out.text = templated
      ? t(out.key, out.vars)
      : (message ? t(out.key, out.vars) + " " + message : t(out.key, out.vars));
    return out;
  }

  /** One Idempotency-Key per plan identity; stable across retries of it. */
  function newIdempotencyKey() {
    try {
      if (typeof crypto !== "undefined" && crypto && typeof crypto.randomUUID === "function") {
        return "next-wizard-" + crypto.randomUUID();
      }
    } catch (error) { /* fall through to the timestamp/random key */ }
    return "next-wizard-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  /**
   * A-08 pre-submit check: real blockers vs server warnings.
   *
   * Blockers are only conditions that genuinely prevent a correct submit:
   * missing workflow/schema, no usable structure, an invalid electronic state,
   * a resource input the server would reject, and a missing / loading /
   * infeasible / failed / stale preflight. Warnings are the preflight
   * advisories — they are shown but can never block. Optional resources
   * (`""`), an empty queue projection and an absent resource authority never
   * block (no false positives).
   *
   * @returns {{ok:boolean, blockers:Array<{code:string,field:string,key:string,
   *            text:string,vars:(object|null)}>, warnings:Array<{code:string,
   *            message:string}>}}
   */
  function submitCheck(input) {
    input = input || {};
    var blockers = [];
    var warnings = [];

    function block(code, field, error) {
      error = error || {};
      blockers.push({
        code: textOf(code),
        field: textOf(field),
        key: textOf(error.key),
        text: textOf(error.text),
        vars: error.vars && typeof error.vars === "object" ? error.vars : null
      });
    }

    var entry = input.entry;
    if (!entry) {
      block("workflow_required", "workflow", { key: "next.wizard.err.workflow_required" });
    } else if (!input.method || !input.method.schema) {
      block("schema_missing", "workflow", {
        key: "next.wizard.err.schema_missing",
        vars: { workflow: textOf(input.workflowLabel || entry.id) }
      });
    } else {
      var methodCheck = validateMethodSchema(input.method);
      methodCheck.errors.forEach(function (err) {
        block("method_invalid", err.field, { text: err.text });
      });
    }

    var adapterKind = textOf(input.adapterKind);
    if (adapterKind === "loading" || adapterKind === "error") {
      block(adapterKind === "error" ? "contract_error" : "contract_loading", "workflow", {
        text: input.contractError || lt(adapterKind === "error" ? "contract_error" : "contract_loading", {
          message: textOf(input.contractError)
        })
      });
    }
    var structureOptional = adapterKind === "source" || adapterKind === "payload";
    var structure = input.structure;
    if (!structureOptional) {
      if (input.structureStatus !== "ready" || !structure) {
        if (input.structureStatus === "loading") {
          block("structure_loading", "structure", { key: "next.wizard.hint_structure_loading" });
        } else if (input.structureStatus === "error") {
          block("structure_error", "structure", { key: "next.wizard.hint_structure_error" });
        } else {
          block("structure_required", "structure", { key: "next.wizard.err.structure_required" });
        }
      } else {
        var electronic = validateElectronic(input.charge, input.multiplicity, structure);
        electronic.errors.forEach(function (err) {
          block(err.field === "charge" ? "charge_invalid" : "multiplicity_invalid", err.field, err);
        });
      }
    }
    (input.inputErrors || []).forEach(function (error) {
      block(textOf(error.code) || "input_invalid", textOf(error.field), {
        text: textOf(error.text)
      });
    });

    if (input.resourcesStatus === "loading") {
      block("resources_loading", "resources", { text: lt("resources_loading") });
    } else if (input.resourcesStatus === "error") {
      block("resources_error", "resources", {
        text: t("next.nodes.section_error", {
          section: t("next.approval.plan.resources"),
          message: describeError(input.resourcesError)
        })
      });
    }
    var resources = validateResources(input.cores, input.memory, input.limits || {});
    resources.errors.forEach(function (err) {
      block("resources_invalid", err.field === "memory" ? "memory" : "cores", err);
    });

    var pre = input.preflight || {};
    var status = textOf(pre.status);
    var valid = pre.valid == null ? (status === "ok" && !!pre.digest) : !!pre.valid;
    if (!status || status === "idle") {
      block("preflight_required", "preflight", { key: "next.config.submit_blocked" });
    } else if (status === "loading") {
      block("preflight_busy", "preflight", { key: "next.config.preflighting" });
    } else if (status === "blocked") {
      block("preflight_blocked", "preflight", { key: "next.s2scan.preflight_blocked" });
    } else if (status === "infeasible") {
      var reasons = normalizeReasons(pre.reasons);
      if (!reasons.length) {
        block("preflight_infeasible", "preflight", { key: "next.config.preflight_failed" });
      } else {
        reasons.forEach(function (reason) {
          block("preflight_infeasible", reason.field, { text: reason.message || reason.code });
        });
      }
    } else if (status === "error") {
      block("preflight_error", "preflight", {
        key: "next.s2scan.preflight_error",
        vars: { message: textOf(pre.error) || textOf(pre.errorCode) }
      });
    } else if (status !== "ok" || !valid || !pre.digest) {
      block("preflight_stale", "preflight", { key: "next.capabilityForm.preflight.stale" });
    }

    normalizeAdvisories(pre.advisories).forEach(function (message) {
      warnings.push({ code: "advisory", message: textOf(message) });
    });
    return { ok: blockers.length === 0, blockers: blockers, warnings: warnings };
  }

  /* ── A-09 drafts (pure) ─────────────────────────────────────────── */

  /** The existing draft document path; the id is encoded exactly once. */
  function draftPath(draftId) {
    return ENDPOINTS.drafts + "/" + encodeURIComponent(textOf(draftId));
  }

  /** Server `updated_at` (ISO) -> the stable "YYYY-MM-DD HH:MM" banner stamp. */
  function draftWhen(value) {
    var text = textOf(value).trim();
    if (text.length < 16) return "";
    return text.slice(0, 10) + " " + text.slice(11, 16);
  }

  function plainObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function draftLevels(method) {
    var out = {};
    var levels = method && Array.isArray(method.levels) ? method.levels : [];
    levels.slice(0, DRAFT_MAX_LEVELS).forEach(function (level) {
      if (!level || !level.levelId) return;
      var values = {};
      (level.rows || []).forEach(function (row) {
        if (!row || !row.field) return;
        var value = row.value;
        if (value === "" || value === null || value === undefined) return;
        if (Array.isArray(value)) {
          if (!value.length) return;
          values[row.field] = value.map(function (item) { return textOf(item); });
          return;
        }
        if (row.type === "bool") {
          values[row.field] = value === true;
          return;
        }
        if (typeof value === "object") return;
        values[row.field] = textOf(value);
      });
      if (Object.keys(values).length) out[level.levelId] = values;
    });
    return out;
  }

  /**
   * Build the persisted draft snapshot from the wizard's presentation state.
   *
   * The snapshot is exactly the operator's presentation / input state:
   * workflow choice, method profile + field values, the typed input text(s),
   * raw charge/multiplicity, resource inputs, scan atoms, frozen payload text
   * and the current step. It NEVER contains a task reference
   * (`source_job_id` / `source_product_id` / `source_mode_index`, receipt job
   * id), the `Idempotency-Key`, a preflight body/digest, a resolved structure
   * (server-derived; re-parse after a restore) or any credential — see the
   * README §A-09 exclusions. The result is JSON-safe and directly usable as
   * the `snapshot` member of `POST/PUT /api/v1/drafts`.
   */
  function draftSnapshot(input) {
    input = input || {};
    var method = input.method || {};
    var inputs = plainObject(input.input);
    var resources = plainObject(input.resources);
    var extras = plainObject(input.extras);
    var step = integerText(input.step);
    if (step === null || step < 0) step = 0;
    if (step > STEP_IDS.length - 1) step = STEP_IDS.length - 1;
    return {
      kind: DRAFT_KIND,
      version: DRAFT_VERSION,
      step: step,
      workflow_id: input.entry ? textOf(input.entry.id) : "",
      method: {
        profile_id: textOf(method.profileId),
        levels: draftLevels(method)
      },
      input: {
        mode: textOf(inputs.mode),
        smiles: textOf(inputs.smiles),
        text: textOf(inputs.text),
        file_name: textOf(inputs.fileName),
        charge: textOf(inputs.charge),
        multiplicity: textOf(inputs.multiplicity)
      },
      resources: {
        cores: textOf(resources.cores),
        memory: textOf(resources.memory)
      },
      scan_atoms: textOf(extras.scanAtoms),
      payload: textOf(extras.payload)
    };
  }

  function draftFieldValue(raw) {
    if (typeof raw === "boolean") return raw;
    if (Array.isArray(raw)) {
      var list = [];
      raw.forEach(function (item) {
        if (typeof item === "string" || typeof item === "number" || typeof item === "boolean") {
          list.push(textOf(item));
        }
      });
      return list;
    }
    if (typeof raw === "string" || typeof raw === "number") return textOf(raw);
    return "";
  }

  /**
   * Sanitize one remote snapshot into the module's own wire shape.
   *
   * A snapshot that is not this module's (`kind`/`version`) is refused
   * entirely — a legacy classic-wizard draft has a different shape and must
   * never be hydrated as if it were this one. Unknown keys are dropped and
   * field values are restricted to JSON scalars / string lists, so a stale or
   * foreign document can never smuggle a task ref, a nested plan or a second
   * state blob into the instance.
   *
   * @returns {(object|null)} the normalized snapshot (same shape as
   *   `draftSnapshot`) or null when the document is not a task-wizard draft.
   */
  function normalizeDraftSnapshot(raw) {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
    if (textOf(raw.kind) !== DRAFT_KIND) return null;
    if (integerText(raw.version) !== DRAFT_VERSION) return null;
    var method = plainObject(raw.method);
    var levels = {};
    var rawLevels = plainObject(method.levels);
    Object.keys(rawLevels).slice(0, DRAFT_MAX_LEVELS).forEach(function (levelId) {
      var values = {};
      var rows = plainObject(rawLevels[levelId]);
      Object.keys(rows).forEach(function (field) {
        var value = draftFieldValue(rows[field]);
        if (value === "" || (Array.isArray(value) && !value.length)) return;
        values[field] = value;
      });
      if (Object.keys(values).length) levels[levelId] = values;
    });
    var inputs = plainObject(raw.input);
    var resources = plainObject(raw.resources);
    var step = integerText(raw.step);
    if (step === null || step < 0) step = 0;
    if (step > STEP_IDS.length - 1) step = STEP_IDS.length - 1;
    return {
      kind: DRAFT_KIND,
      version: DRAFT_VERSION,
      step: step,
      workflow_id: textOf(raw.workflow_id),
      method: {
        profile_id: textOf(method.profile_id),
        levels: levels
      },
      input: {
        mode: textOf(inputs.mode),
        smiles: textOf(inputs.smiles),
        text: textOf(inputs.text),
        file_name: textOf(inputs.file_name),
        charge: textOf(inputs.charge),
        multiplicity: textOf(inputs.multiplicity)
      },
      resources: {
        cores: textOf(resources.cores),
        memory: textOf(resources.memory)
      },
      scan_atoms: textOf(raw.scan_atoms),
      payload: textOf(raw.payload)
    };
  }

  /**
   * The `POST/PUT /api/v1/drafts` body. `expected_revision` is present only for
   * a PUT of an existing draft (the server rejects a missing revision with 422
   * and a stale one with 409 `revision_conflict`). `name`/`project_id`/
   * `workflow` are the row metadata the list view reads, never plan data.
   */
  function draftBody(snapshot, options) {
    options = options || {};
    var body = { snapshot: snapshot };
    var name = textOf(options.name).trim();
    if (name) body.name = name;
    var workflow = textOf(options.workflow).trim() ||
      (snapshot && typeof snapshot === "object" ? textOf(snapshot.workflow_id) : "");
    if (workflow) body.workflow = workflow;
    body.project_id = textOf(options.projectId);
    var revision = integerText(options.expectedRevision);
    if (options.expectedRevision !== null && options.expectedRevision !== undefined && revision !== null) {
      body.expected_revision = revision;
    }
    return body;
  }

  /**
   * Map a draft route rejection to a stable machine branch + readable copy.
   *
   * The draft router answers with a plain string detail:
   * `revision_conflict` (409, stale `expected_revision`), `draft_not_found`
   * (404), `expected_revision_required` / `snapshot_must_be_object` (422),
   * `draft_too_large` (413), or `draft_not_found` for an invalid id.
   */
  function mapDraftError(error) {
    var status = error && typeof error.status === "number" ? error.status : null;
    var detail = errorDetailOf(error);
    var code = detail ? (textOf(detail.code) || textOf(detail.error)) : "";
    var message = describeError(error);
    if (!code && message) {
      ["revision_conflict", "draft_not_found", "expected_revision_required",
        "snapshot_must_be_object", "draft_too_large"].forEach(function (name) {
        if (!code && message === name) code = name;
      });
    }
    return {
      status: status,
      code: code,
      message: message,
      conflict: status === 409 || code === "revision_conflict",
      missing: status === 404 || code === "draft_not_found",
      retryable: status === null || status >= 500
    };
  }

  /* ── catalog projection (pure) ──────────────────────────────────── */

  /**
   * Project `GET /workflow-catalog` into actionable entries, in catalog order.
   * The server catalog is the only source: retired entries and entries marked
   * hidden are dropped, and nothing is injected when the list is empty.
   */
  function filterWorkflows(catalogBody) {
    var raw = catalogBody && Array.isArray(catalogBody.workflows) ? catalogBody.workflows : [];
    var out = [];
    raw.forEach(function (entry) {
      if (!entry || entry.status !== "active" || entry.visible === false) return;
      var id = textOf(entry.id);
      if (!id) return;
      out.push({
        id: id,
        label: textOf(entry.label || id),
        labelZh: textOf(entry.label_zh || entry.label || id),
        description: textOf(entry.description),
        descriptionZh: textOf(entry.description_zh || entry.description),
        schemaId: textOf(entry.method_schema_id),
        backend: textOf(entry.default_backend),
        category: textOf(entry.category)
      });
    });
    return out;
  }

  function findWorkflow(entries, workflowId) {
    var list = entries || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i] && list[i].id === workflowId) return list[i];
    }
    return null;
  }

  /** First active catalog entry — never a hard-coded or retired fallback. */
  function pickDefaultWorkflow(entries) {
    return entries && entries.length ? entries[0] : null;
  }

  function schemaFor(methodCatalog, schemaId) {
    var schemas = methodCatalog && methodCatalog.method_schemas;
    if (!schemas || typeof schemas !== "object") return null;
    return schemas[schemaId] || null;
  }

  function profilesOf(schema) {
    return schema && Array.isArray(schema.profiles) ? schema.profiles.slice() : [];
  }

  function profileById(schema, profileId) {
    var profiles = profilesOf(schema);
    for (var i = 0; i < profiles.length; i += 1) {
      if (profiles[i] && textOf(profiles[i].profile_id) === profileId) return profiles[i];
    }
    return null;
  }

  function pickDefaultProfile(schema) {
    var profiles = profilesOf(schema);
    for (var i = 0; i < profiles.length; i += 1) {
      if (profiles[i] && textOf(profiles[i].profile_id) === "default") return profiles[i];
    }
    return profiles.length ? profiles[0] : null;
  }

  function fieldDefinition(methodCatalog, field) {
    var defs = methodCatalog && methodCatalog.field_definitions;
    if (!defs || typeof defs !== "object") return null;
    return defs[field] || null;
  }

  function defaultFor(def) {
    if (!def || !def.default || typeof def.default !== "object") return "";
    if (Object.prototype.hasOwnProperty.call(def.default, "*")) return textOf(def.default["*"]);
    var keys = Object.keys(def.default);
    return keys.length ? textOf(def.default[keys[0]]) : "";
  }

  /** Typed catalog default: profile values and defaults keep their JSON type. */
  function defaultRawFor(def) {
    if (!def || !def.default || typeof def.default !== "object") return "";
    if (Object.prototype.hasOwnProperty.call(def.default, "*")) return def.default["*"];
    var keys = Object.keys(def.default);
    return keys.length ? def.default[keys[0]] : "";
  }

  /**
   * Resolve one field's option list from the method catalog. Never invents a
   * method or basis: an absent catalog option list yields an empty list and
   * the row is not rendered.
   */
  function fieldOptions(methodCatalog, field, engine) {
    var def = fieldDefinition(methodCatalog, field);
    var catalog = methodCatalog || {};
    if (field === "basis") {
      var basis = catalog.basis_catalog;
      if (Array.isArray(basis) && basis.length) return basis.slice();
    }
    var perBackend = def && def.per_backend && typeof def.per_backend === "object"
      ? def.per_backend[engine]
      : null;
    if (Array.isArray(perBackend) && perBackend.length) return perBackend.slice();
    if (def && Array.isArray(def.options) && def.options.length) return def.options.slice();
    if (field === "functional") {
      var meta = catalog.method_meta;
      if (meta && typeof meta === "object" && !Array.isArray(meta)) return Object.keys(meta).sort();
    }
    return [];
  }

  function fieldLabel(def, field) {
    if (def) {
      if (en()) return textOf(def.label || field);
      return textOf(def.label_zh || def.label || field);
    }
    var fallback = {
      functional: "next.wizard.field_functional",
      basis: "next.wizard.field_basis",
      gfn: "next.wizard.field_gfn"
    };
    return t(fallback[field] || field);
  }

  function fieldHelp(def) {
    if (!def) return "";
    return en() ? textOf(def.help) : textOf(def.help_zh || def.help);
  }

  /** Display label of one catalog option (zh label map first, then the value). */
  function optionLabelFor(def, value) {
    if (!def) return textOf(value);
    var labels = en() ? def.option_labels : (def.option_labels_zh || def.option_labels);
    if (labels && typeof labels === "object" && Object.prototype.hasOwnProperty.call(labels, value)) {
      return textOf(labels[value]);
    }
    return textOf(value);
  }

  /**
   * Describe one schema field as a typed widget.
   *
   * The catalog `field_definitions` entry is the widget authority: `type`
   * (select/int/float/bool/module), `options`/`per_backend`, `multi`, `min`,
   * `max`, `step`, `unit`, `default` and `advanced`. A field with no definition
   * still renders as a text input (the schema names it, so the server will see
   * exactly that key); no option or default is invented.
   */
  function fieldControl(methodCatalog, field, engine, preset, hasPreset) {
    var def = fieldDefinition(methodCatalog, field);
    if (!def && !hasPreset) {
      /* Schema-only field: render a text control, never a guessed select. */
      return {
        field: field,
        type: "text",
        def: null,
        options: [],
        multi: false,
        value: "",
        initialValue: "",
        advanced: false,
        min: null,
        max: null,
        step: null,
        unit: ""
      };
    }
    var type = def && def.type ? textOf(def.type) : "text";
    if (type === "module") return null;
    var multi = !!(def && def.multi);
    var options = fieldOptions(methodCatalog, field, engine);
    if (multi) type = "multi";
    else if (type === "select" && !options.length) type = "text";
    var raw = hasPreset && preset !== undefined && preset !== null
      ? preset
      : defaultRawFor(def);
    if (raw === null || raw === undefined) raw = "";
    if (type === "bool") {
      raw = raw === true || raw === "true" || raw === 1 || raw === "1";
    } else if (type === "multi") {
      if (Array.isArray(raw)) raw = raw.slice();
      else if (raw === "" || raw === null || raw === undefined) raw = [];
      else raw = [raw];
      raw = raw.map(function (value) {
        for (var i = 0; i < options.length; i += 1) {
          if (textOf(options[i]).toLowerCase() === textOf(value).toLowerCase()) return options[i];
        }
        return textOf(value);
      });
    } else if (type === "int" || type === "float") {
      raw = raw === "" ? "" : raw;
    } else {
      raw = textOf(raw);
    }
    if (type === "select" && raw !== "") {
      var matched = null;
      for (var index = 0; index < options.length; index += 1) {
        if (textOf(options[index]).toLowerCase() === textOf(raw).toLowerCase()) {
          matched = options[index];
          break;
        }
      }
      if (matched !== null) raw = textOf(matched);
      else options = [raw].concat(options);
    }
    return {
      field: field,
      type: type,
      def: def,
      options: options,
      multi: multi,
      value: raw,
      initialValue: Array.isArray(raw) ? raw.slice() : raw,
      advanced: !!(def && def.advanced),
      min: def && def.min != null ? def.min : null,
      max: def && def.max != null ? def.max : null,
      step: def && def.step != null ? def.step : null,
      unit: def ? textOf(def.unit) : ""
    };
  }

  function sameInitialValue(row) {
    var value = row.value;
    var initial = row.initialValue;
    if (Array.isArray(value) || Array.isArray(initial)) {
      var left = Array.isArray(value) ? value : [];
      var right = Array.isArray(initial) ? initial : [];
      return left.length === right.length && left.every(function (item, index) {
        return textOf(item) === textOf(right[index]);
      });
    }
    return textOf(value) === textOf(initial);
  }

  function optionMatches(def, options, value) {
    if (!options || !options.length) return true;
    var text = textOf(value);
    for (var i = 0; i < options.length; i += 1) {
      if (textOf(options[i]) === text) return true;
      if (textOf(options[i]).toLowerCase() === text.toLowerCase()) return true;
    }
    /* Custom-valued catalog fields (e.g. a user basis) accept an explicit
     * string; the option list is a suggestion, not the server contract. */
    return !!(def && def.supports_custom && text !== "");
  }

  function schemaLevels(schema) {
    return schema && Array.isArray(schema.method_levels) ? schema.method_levels : [];
  }

  /**
   * A-07 projection mode: schemas composed only of the established generic
   * single-structure fields keep the minimal A-01 projection; any schema with
   * another field (or with no fields at all — frozen-payload workflows) gets
   * the full catalog-driven panel. This is a field-set policy and never a
   * workflow-id allowlist.
   */
  function isExtendedSchema(schema) {
    var levels = schemaLevels(schema);
    if (!levels.length) return false;
    var anyField = false;
    for (var i = 0; i < levels.length; i += 1) {
      var fields = levels[i] && Array.isArray(levels[i].fields) ? levels[i].fields : [];
      for (var j = 0; j < fields.length; j += 1) {
        anyField = true;
        if (!MINIMAL_SCHEMA_FIELDS[fields[j]]) return true;
      }
    }
    /* No fields at all: the request payload is owned by a frozen upstream
     * contract (or the schema really is empty). Extended mode keeps it an
     * explicit, separately projected input instead of a generic method form. */
    return !anyField;
  }

  function methodSupport(method) {
    if (!method || !method.schema) return "schema_missing";
    var levels = Array.isArray(method.levels) ? method.levels : null;
    if (levels === null) return "";
    if (!levels.length) return "schema_empty";
    return "";
  }

  function methodLevel(method, levelId) {
    var levels = method && Array.isArray(method.levels) ? method.levels : [];
    for (var i = 0; i < levels.length; i += 1) {
      if (levels[i] && levels[i].levelId === levelId) return levels[i];
    }
    return null;
  }

  function methodRow(level, field) {
    if (!level || !Array.isArray(level.rows)) return null;
    for (var i = 0; i < level.rows.length; i += 1) {
      if (level.rows[i] && level.rows[i].field === field) return level.rows[i];
    }
    return null;
  }

  function methodValue(method, levelId, field, fallback) {
    var row = methodRow(methodLevel(method, levelId), field);
    if (!row || row.value === "" || row.value == null) return fallback;
    return row.value;
  }

  function intValue(value) {
    return integerText(value);
  }

  function floatValue(value) {
    if (value == null || value === "" || typeof value === "boolean") return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function boolValue(value) {
    if (value === true) return true;
    if (value === false) return false;
    var text = textOf(value).trim().toLowerCase();
    return text === "true" || text === "1" || text === "yes" || text === "on";
  }

  /**
   * Local schema validation (A-07). Every catalog-declared type, option list
   * and min/max is enforced before a preflight is sent, so an invalid typed
   * field is an explicit blocker rather than a server 422 later. Unknown/text
   * fields pass through unchanged (the server remains the final authority).
   */
  function validateMethodSchema(method) {
    var errors = [];
    var support = methodSupport(method);
    if (support === "schema_missing") {
      errors.push({ field: "workflow", levelId: "", text: t("next.wizard.err.schema_missing", {
        workflow: ""
      }) });
      return { ok: false, errors: errors };
    }
    if (support === "schema_empty") {
      errors.push({ field: "method", levelId: "", text: lt("method_schema_empty") });
      return { ok: false, errors: errors };
    }
    if (!Array.isArray(method.levels)) return { ok: true, errors: errors };
    method.levels.forEach(function (level) {
      (level.rows || []).forEach(function (row) {
        var label = fieldLabel(row.def || null, row.field);
        var value = row.value;
        if (row.type === "int" || row.type === "float") {
          if (value === "" || value === null || value === undefined) return;
          var number = Number(value);
          if (!isFinite(number)) {
            errors.push({ field: row.field, levelId: level.levelId, text: lt("method_invalid_number", { label: label }) });
            return;
          }
          if (row.type === "int" && !Number.isInteger(number)) {
            errors.push({ field: row.field, levelId: level.levelId, text: lt("method_invalid_integer", { label: label }) });
            return;
          }
          if ((row.min != null && number < row.min) || (row.max != null && number > row.max)) {
            errors.push({
              field: row.field,
              levelId: level.levelId,
              text: lt("method_out_of_range", { label: label, min: row.min, max: row.max })
            });
          }
        } else if (row.type === "select") {
          if (value === "" || value === null || value === undefined) return;
          if (sameInitialValue(row)) return;
          if (!optionMatches(row.def, row.options, value)) {
            errors.push({ field: row.field, levelId: level.levelId, text: lt("method_invalid_option", { label: label }) });
          }
        } else if (row.type === "multi") {
          var values = Array.isArray(value) ? value : [];
          if (!row.options.length) return;
          if (sameInitialValue(row)) return;
          for (var i = 0; i < values.length; i += 1) {
            if (!optionMatches(row.def, row.options, values[i])) {
              errors.push({ field: row.field, levelId: level.levelId, text: lt("method_invalid_option", { label: label }) });
              return;
            }
          }
        }
      });
    });
    return { ok: errors.length === 0, errors: errors };
  }

  /**
   * Project one workflow's method schema into editable levels.
   *
   * The established generic schemas keep the exact A-01 projection and value
   * seeding (`functional`/`basis`/`gfn`). A-07 schemas project every declared
   * level field with the catalog widget metadata, and only the levels the
   * selected profile actually activates (a profile that names a subset must
   * not imply the hidden optional levels).
   *
   * `profileId` re-seeds values from that profile; otherwise the catalog's
   * default profile (or its first profile) wins. Profile-less schemas are
   * seeded from `field_definitions.default` — both sources are server data.
   */
  function projectMethod(workflow, methodCatalog, options) {
    options = options || {};
    var result = {
      schemaId: "",
      schema: null,
      profiles: [],
      profileId: "",
      levels: [],
      error: "",
      extended: false
    };
    if (!workflow) return result;
    result.schemaId = textOf(workflow.schemaId);
    var schema = schemaFor(methodCatalog, result.schemaId);
    if (!schema) {
      result.error = "schema_missing";
      return result;
    }
    result.schema = schema;
    var definitions = schemaLevels(schema);
    if (!definitions.length) {
      result.error = "schema_empty";
      return result;
    }
    result.extended = isExtendedSchema(schema);
    var profiles = profilesOf(schema);
    result.profiles = profiles.map(function (profile) {
      return {
        id: textOf(profile.profile_id),
        label: textOf(profile.label || profile.profile_id)
      };
    });
    var profile = options.profileId ? profileById(schema, options.profileId) : null;
    if (!profile) profile = pickDefaultProfile(schema);
    result.profileId = profile ? textOf(profile.profile_id) : "";
    var profileLevels = profile && profile.levels && typeof profile.levels === "object"
      ? profile.levels
      : null;
    result.levels = definitions.filter(function (definition) {
      /* A-01 minimal projection keeps every schema level (the historical
       * behavior). A-07 profiles select the active levels: a profile that
       * names a subset must not imply the hidden optional levels. */
      if (!result.extended || !profileLevels) return true;
      var keys = Object.keys(profileLevels);
      if (!keys.length) return true;
      return Object.prototype.hasOwnProperty.call(profileLevels, textOf(definition.level_id));
    }).map(function (definition) {
      var levelId = textOf(definition.level_id);
      var presetRaw = profileLevels ? profileLevels[levelId] : null;
      var preset = presetRaw && typeof presetRaw === "object" ? presetRaw : {};
      var engines = Array.isArray(definition.allowed_engines) ? definition.allowed_engines : [];
      var engine = textOf(preset.engine || engines[0] || workflow.backend);
      var rows = [];
      var fields = Array.isArray(definition.fields) ? definition.fields : [];
      fields.forEach(function (field) {
        if (result.extended) {
          var control = fieldControl(
            methodCatalog,
            field,
            engine,
            Object.prototype.hasOwnProperty.call(preset, field) ? preset[field] : undefined,
            Object.prototype.hasOwnProperty.call(preset, field)
          );
          if (!control) return;
          rows.push(control);
          return;
        }
        if (PROJECTED_FIELDS.indexOf(field) < 0) return;
        var optionList = fieldOptions(methodCatalog, field, engine);
        if (!optionList.length) return;
        var def = fieldDefinition(methodCatalog, field);
        var value = preset[field] == null ? defaultFor(def) : textOf(preset[field]);
        if (!value) value = optionList[0];
        if (optionList.indexOf(value) < 0) value = optionList[0];
        rows.push({
          field: field,
          options: optionList,
          value: value,
          initialValue: value,
          type: "select",
          def: def
        });
      });
      return {
        levelId: levelId,
        label: textOf(definition.label || levelId),
        labelZh: textOf(definition.label_zh || definition.label || levelId),
        required: definition.required !== false,
        engine: engine,
        rows: rows
      };
    });
    return result;
  }

  function emptyMethod() {
    return {
      schemaId: "",
      schema: null,
      profiles: [],
      profileId: "",
      levels: [],
      error: "",
      extended: false
    };
  }

  function levelLabel(level) {
    return en() ? level.label : level.labelZh;
  }

  /* ── A-07 workflow contract projection (pure) ───────────────────── */

  function contractOf(contractState) {
    var body = contractState && contractState.body;
    var contract = body && typeof body === "object" ? body.workflow_contract : null;
    return contract && typeof contract === "object" && !Array.isArray(contract) ? contract : null;
  }

  function objectOf(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function stringList(value) {
    if (!Array.isArray(value)) return [];
    var out = [];
    for (var i = 0; i < value.length; i += 1) {
      var item = textOf(value[i]);
      if (item) out.push(item);
    }
    return out;
  }

  /** True when the schema itself declares a scan-coordinate surface. */
  function schemaHasScanFields(schema) {
    var levels = schemaLevels(schema);
    for (var i = 0; i < levels.length; i += 1) {
      var fields = levels[i] && Array.isArray(levels[i].fields) ? levels[i].fields : [];
      for (var j = 0; j < fields.length; j += 1) {
        if (textOf(fields[j]).indexOf("scan_coordinate") === 0) return true;
      }
    }
    return false;
  }

  function findLevelByField(method, field) {
    var levels = method && Array.isArray(method.levels) ? method.levels : [];
    for (var i = 0; i < levels.length; i += 1) {
      if (methodRow(levels[i], field)) return levels[i];
    }
    return null;
  }

  /**
   * Project the server workflow contract into one input adapter.
   *
   * The contract (`workflow_contract` from `/api/capabilities?workflow=`) is
   * the authority for which input keys exist and whether `method.levels` is
   * accepted. Five adapters cover every active catalog workflow without a
   * workflow-id list:
   *   payload  — a frozen upstream request object (`*_request` required key);
   *   source   — an existing task product (`source_job_id` required);
   *   scan     — relaxed scan: PES carries input.coordinate/protocol, the
   *              simple scan keeps method.levels plus input.scan_coordinates;
   *   batch    — a one-item `batch_structures` envelope;
   *   structure— the A-02 chemistry envelope.
   * The returned object is a pure projection; the instance owns requests.
   */
  function projectInputAdapter(entry, method, contractState) {
    var adapter = {
      kind: ADAPTER_STRUCTURE,
      extended: !!(method && method.extended),
      coverage: "not_provided",
      contract: null,
      contractStatus: contractState ? textOf(contractState.status) : "idle",
      methodEmit: METHOD_EMIT_LEVELS,
      payloadKey: "",
      pes: false,
      sourceFields: [],
      requiredAny: [],
      inputProperties: {},
      methodProperties: {},
      methodEnums: {}
    };
    if (!adapter.extended) return adapter;
    if (adapter.contractStatus === "idle" || adapter.contractStatus === "loading") {
      adapter.kind = "loading";
      return adapter;
    }
    if (adapter.contractStatus === "error") {
      adapter.kind = "error";
      return adapter;
    }
    var contract = contractOf(contractState);
    adapter.contract = contract;
    adapter.coverage = contract ? textOf(contract.coverage || "not_provided") : "not_provided";
    if (!contract || !contract.input || !contract.method) {
      /* A `not_provided` contract is explicit: the method catalog still owns
       * the levels, and the generic structure envelope remains valid. */
      adapter.kind = ADAPTER_STRUCTURE;
      return adapter;
    }
    adapter.inputProperties = objectOf(contract.input.properties);
    adapter.methodProperties = objectOf(contract.method.properties);
    adapter.methodEnums = objectOf(contract.method.enums);
    adapter.requiredAny = stringList(contract.input.required_any);
    var payloadKey = "";
    var i;
    for (i = 0; i < adapter.requiredAny.length; i += 1) {
      if (/_request$/.test(adapter.requiredAny[i])) {
        payloadKey = adapter.requiredAny[i];
        break;
      }
    }
    if (!payloadKey) {
      var inputKeys = Object.keys(adapter.inputProperties);
      for (i = 0; i < inputKeys.length; i += 1) {
        if (/_request$/.test(inputKeys[i])) {
          payloadKey = inputKeys[i];
          break;
        }
      }
    }
    if (payloadKey) {
      adapter.kind = ADAPTER_PAYLOAD;
      adapter.payloadKey = payloadKey;
      adapter.methodEmit = METHOD_EMIT_NONE;
      return adapter;
    }
    if (schemaHasScanFields(method && method.schema)) {
      adapter.kind = ADAPTER_SCAN;
      adapter.pes = !adapter.methodProperties.levels;
      adapter.methodEmit = adapter.pes ? METHOD_EMIT_PROTOCOL : METHOD_EMIT_LEVELS;
      return adapter;
    }
    if (adapter.requiredAny.indexOf("source_job_id") >= 0 ||
        (adapter.inputProperties.source_job_id && adapter.requiredAny.length &&
         adapter.requiredAny.indexOf("source_ref") >= 0)) {
      adapter.kind = ADAPTER_SOURCE;
      adapter.methodEmit = adapter.methodProperties.levels ? METHOD_EMIT_LEVELS : METHOD_EMIT_NONE;
      ["source_job_id", "source_product_id", "source_id", "source_mode_index", "directions"].forEach(function (field) {
        if (adapter.inputProperties[field]) adapter.sourceFields.push(field);
      });
      return adapter;
    }
    if (adapter.inputProperties.items && adapter.requiredAny.indexOf("items") >= 0) {
      adapter.kind = ADAPTER_BATCH;
      return adapter;
    }
    return adapter;
  }

  /**
   * Parse the operator-provided 0-based scan atom indices.
   *
   * The wizard never picks scan atoms itself: the operator enters exactly the
   * count the coordinate kind requires (2 distance / 3 angle / 4 dihedral).
   *
   * @returns {{ok:boolean, code:string, atoms:Array<number>, count:number,
   *            atom:(number|null), expected:number}}
   */
  function parseScanAtoms(raw, expected, atomCount) {
    var text = textOf(raw).trim();
    var out = {
      ok: false,
      code: "required",
      atoms: [],
      count: 0,
      atom: null,
      expected: expected
    };
    if (!text) return out;
    var parts = text.split(/[,\s]+/).filter(function (part) { return part !== ""; });
    out.count = parts.length;
    if (expected && parts.length !== expected) {
      out.code = "count";
      return out;
    }
    var atoms = [];
    var seen = {};
    for (var i = 0; i < parts.length; i += 1) {
      var atom = integerText(parts[i]);
      if (atom === null || atom < 0) {
        out.code = "integer";
        return out;
      }
      if (atomCount != null && atom >= atomCount) {
        out.code = "range";
        out.atom = atom;
        return out;
      }
      if (seen[String(atom)]) {
        out.code = "distinct";
        out.atom = atom;
        return out;
      }
      seen[String(atom)] = true;
      atoms.push(atom);
    }
    out.ok = true;
    out.code = "";
    out.atoms = atoms;
    return out;
  }

  function scanAtomError(parsed, atomCount) {
    if (!parsed || parsed.ok) return "";
    if (parsed.code === "required") return lt("scan_atoms_required");
    if (parsed.code === "range") {
      return lt("scan_atoms_out_of_range", { atom: parsed.atom, count: atomCount });
    }
    if (parsed.code === "distinct" || parsed.code === "integer") {
      return lt("scan_atoms_invalid", { count: parsed.expected || parsed.count });
    }
    return lt("scan_atoms_invalid", { count: parsed.expected || parsed.count });
  }

  function expectedScanAtomCount(kind) {
    if (kind === "angle") return 3;
    if (kind === "dihedral") return 4;
    return 2;
  }

  function formatScanValue(value) {
    var number = floatValue(value);
    return number === null ? "" : String(number);
  }

  function numberFieldValue(value) {
    var number = intValue(value);
    return number === null ? null : number;
  }

  function jsonObject(text) {
    var raw = textOf(text).trim();
    if (!raw) return { ok: false, code: "required", value: null };
    try {
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return { ok: false, code: "object", value: null };
      }
      return { ok: true, code: "", value: parsed };
    } catch (error) {
      return { ok: false, code: "syntax", value: null, message: textOf(error && error.message) };
    }
  }

  /**
   * Map the schema's scan fields into the documented PES protocol object.
   *
   * The field names are the catalog's; the target keys are
   * `acp.calculations.pes.contracts.ScanDriver/ScanOptimizer/SinglePointSpec`
   * (`scan_driver.*`, `scan_optimizer.*`, `single_point.*`). No value is
   * invented: an absent row is omitted and the server default applies.
   */
  function buildPesProtocol(method, charge, multiplicity) {
    var driverLevel = findLevelByField(method, "scan_mode");
    var optimizerLevel = findLevelByField(method, "scan_optimizer_method");
    var singlePointLevel = findLevelByField(method, "single_point_resume");
    if (!singlePointLevel) singlePointLevel = methodLevel(method, "single_point");
    var driver = {};
    function put(target, key, value, keepFalse) {
      if (value === "" || value === null || value === undefined) return;
      if (value === false && !keepFalse) return;
      target[key] = value;
    }
    if (driverLevel) {
      put(driver, "software", textOf(driverLevel.engine), true);
      put(driver, "mode", methodRow(driverLevel, "scan_mode") && methodRow(driverLevel, "scan_mode").value, true);
      var reuse = methodRow(driverLevel, "scan_reuse_previous_geometry");
      if (reuse) driver.reuse_previous_geometry = boolValue(reuse.value);
      var full = methodRow(driverLevel, "scan_full_scan");
      if (full) driver.full_scan = boolValue(full.value);
      var scants = methodRow(driverLevel, "scan_use_scants");
      if (scants) driver.use_scants = boolValue(scants.value);
      var maxIter = methodRow(optimizerLevel, "scan_optimizer_max_iterations");
      if (maxIter) {
        var parsedMax = numberFieldValue(maxIter.value);
        if (parsedMax !== null) driver.max_iterations = parsedMax;
      }
      put(driver, "failure_policy", methodRow(driverLevel, "scan_failure_policy") && methodRow(driverLevel, "scan_failure_policy").value, true);
      var retry = methodRow(driverLevel, "scan_retry_count");
      if (retry) {
        var parsedRetry = numberFieldValue(retry.value);
        if (parsedRetry !== null) driver.retry_count = parsedRetry;
      }
    }
    var optimizer = {};
    if (optimizerLevel) {
      var optimizerMap = {
        scan_optimizer_method: "method",
        scan_optimizer_basis: "basis",
        scan_optimizer_dispersion: "dispersion",
        scan_optimizer_solvent_model: "solvent_model",
        scan_optimizer_solvent: "solvent",
        scan_optimizer_grid: "grid",
        scan_optimizer_scf_convergence: "scf_convergence",
        scan_optimizer_scf_max_iterations: "scf_max_iterations",
        scan_optimizer_ri_approximation: "ri_approximation",
        scan_optimizer_max_iterations: "max_iterations",
        scan_optimizer_convergence: "convergence",
        scan_optimizer_retries: "retry_count",
        scan_optimizer_retry_strategy: "retry_strategy"
      };
      Object.keys(optimizerMap).forEach(function (field) {
        var row = methodRow(optimizerLevel, field);
        if (!row || row.value === "" || row.value === null || row.value === undefined) return;
        var value = row.value;
        if (row.type === "int") {
          var parsed = numberFieldValue(value);
          if (parsed === null) return;
          value = parsed;
        }
        optimizer[optimizerMap[field]] = value;
      });
    }
    var singlePoint = {};
    if (singlePointLevel) {
      singlePoint.enabled = true;
      if (singlePointLevel.engine) singlePoint.software = textOf(singlePointLevel.engine);
      var spMap = {
        functional: "method",
        basis: "basis",
        dispersion: "dispersion",
        ri_approximation: "ri_approximation",
        aux_j_basis: "aux_j_basis",
        aux_c_basis: "aux_c_basis",
        solvent_model: "solvent_model",
        solvent: "solvent",
        grid: "grid",
        scf_convergence: "scf_convergence"
      };
      Object.keys(spMap).forEach(function (field) {
        var row = methodRow(singlePointLevel, field);
        if (!row || row.value === "" || row.value === null || row.value === undefined) return;
        singlePoint[spMap[field]] = row.value;
      });
      var resume = methodRow(singlePointLevel, "single_point_resume");
      if (resume) singlePoint.resume = boolValue(resume.value);
    }
    if (charge !== null) singlePoint.charge = charge;
    if (multiplicity !== null) singlePoint.multiplicity = multiplicity;
    var protocol = { scan_driver: driver };
    if (Object.keys(optimizer).length) protocol.scan_optimizer = optimizer;
    if (Object.keys(singlePoint).length) protocol.single_point = singlePoint;
    return protocol;
  }

  function scanCoordinateLevel(method) {
    return findLevelByField(method, "scan_coordinate_kind") ||
      findLevelByField(method, "scan_coordinate_start");
  }

  /**
   * Build the A-07 workflow request body (the A-05/A-06 frozen plan).
   *
   * Returns `{request, errors}`; `errors` are local, explicit blockers (never a
   * partially built generic body). The returned `request` is exactly what
   * `/preflight` validates and what A-06 later freezes.
   */
  function buildWorkflowRequest(input) {
    input = input || {};
    var errors = [];
    function fail(field, text) {
      errors.push({ field: textOf(field), text: textOf(text) });
    }
    var entry = input.entry;
    var method = input.method || emptyMethod();
    var adapter = input.adapter || projectInputAdapter(entry, method, input.contract);
    if (!entry) {
      fail("workflow", t("next.wizard.err.workflow_required"));
      return { request: null, errors: errors, adapter: adapter };
    }
    var support = methodSupport(method);
    if (support === "schema_missing") {
      fail("workflow", t("next.wizard.err.schema_missing", { workflow: optionLabel(entry) }));
      return { request: null, errors: errors, adapter: adapter };
    }
    if (support === "schema_empty") {
      fail("method", lt("method_schema_empty"));
      return { request: null, errors: errors, adapter: adapter };
    }
    if (adapter.kind === "loading" || adapter.kind === "error") {
      fail("workflow_contract", adapter.kind === "error"
        ? lt("contract_error", { message: describeError(input.contract && input.contract.error) })
        : lt("contract_loading"));
      return { request: null, errors: errors, adapter: adapter };
    }
    var methodCheck = validateMethodSchema(method);
    if (!methodCheck.ok) {
      methodCheck.errors.forEach(function (error) {
        fail(error.field, error.text);
      });
      return { request: null, errors: errors, adapter: adapter };
    }
    var extras = input.extras || {};
    var structure = input.structure;
    var structureRequired = adapter.kind === ADAPTER_STRUCTURE ||
      adapter.kind === ADAPTER_SCAN || adapter.kind === ADAPTER_BATCH;
    if (structureRequired && (!structure || !structure.xyz)) {
      fail("structure", t("next.wizard.err.structure_required"));
      return { request: null, errors: errors, adapter: adapter };
    }
    var chargeText = textOf(input.charge).trim();
    var multiplicityText = textOf(input.multiplicity).trim();
    var charge = chargeText === "" ? null : integerText(chargeText);
    var multiplicity = multiplicityText === "" ? null : integerText(multiplicityText);
    if (structureRequired) {
      if (charge === null) fail("charge", t("next.config.error.charge_integer"));
      if (multiplicity === null || multiplicity < 1) {
        fail("multiplicity", t("next.config.error.multiplicity_integer"));
      }
    }
    if (errors.length) return { request: null, errors: errors, adapter: adapter };
    var inputData = {};
    var methodData = null;
    var adapterKind = adapter.kind;
    if (adapterKind === ADAPTER_PAYLOAD) {
      var payload = jsonObject(extras.payload);
      if (!payload.ok) {
        if (payload.code === "required") {
          fail("payload", lt("payload_required", { key: adapter.payloadKey }));
        } else {
          fail("payload", lt("payload_invalid", {
            key: adapter.payloadKey,
            message: payload.message || payload.code
          }));
        }
        return { request: null, errors: errors, adapter: adapter };
      }
      inputData[adapter.payloadKey] = payload.value;
      methodData = null;
    } else if (adapterKind === ADAPTER_SOURCE) {
      var sourceJobId = textOf(extras.sourceJobId).trim();
      if (!sourceJobId) fail("source_job_id", lt("source_required"));
      inputData.source_job_id = sourceJobId;
      var sourceProductId = textOf(extras.sourceProductId).trim();
      if (sourceProductId) {
        if (adapter.inputProperties.source_product_id) inputData.source_product_id = sourceProductId;
        else if (adapter.inputProperties.source_id) inputData.source_id = sourceProductId;
      }
      if (adapter.inputProperties.source_mode_index) {
        var modeText = textOf(extras.sourceModeIndex).trim();
        var mode = integerText(modeText);
        if (mode === null || mode < 0) fail("source_mode_index", lt("source_mode_invalid"));
        else inputData.source_mode_index = mode;
      }
      if (adapter.methodEmit === METHOD_EMIT_NONE) {
        /* The runner consumes no method keys for this contract shape; the
         * schema fields that do have input consumers are mapped explicitly. */
        (method.levels || []).forEach(function (level) {
          (level.rows || []).forEach(function (row) {
            if (row.value === "" || row.value === null || row.value === undefined) return;
            if (adapter.inputProperties[row.field]) {
              inputData[row.field] = row.type === "float"
                ? canonicalFloatText(row.value)
                : row.value;
            } else if (row.field === "max_steps" && adapter.inputProperties.max_iterations) {
              var maxIterations = numberFieldValue(row.value);
              if (maxIterations !== null) inputData.max_iterations = maxIterations;
            }
          });
        });
        methodData = null;
      }
    } else if (adapterKind === ADAPTER_BATCH) {
      inputData = {
        source_type: "batch_structures",
        items: [{
          name: textOf(structure.name),
          xyz: textOf(structure.xyz),
          charge: charge === null ? 0 : charge,
          multiplicity: multiplicity === null ? 1 : multiplicity
        }]
      };
      if (!structure.name) delete inputData.items[0].name;
      if (charge !== null) inputData.charge = charge;
      if (multiplicity !== null) inputData.multiplicity = multiplicity;
    } else if (adapterKind === ADAPTER_SCAN) {
      var coordinateLevel = scanCoordinateLevel(method);
      var kindRow = coordinateLevel ? methodRow(coordinateLevel, "scan_coordinate_kind") : null;
      var kindOptions = kindRow && Array.isArray(kindRow.options) ? kindRow.options : [];
      var kind = textOf(kindRow && kindRow.value) || textOf(kindOptions[0]);
      if (!kind) fail("scan_coordinate_kind", lt("method_invalid_option", {
        label: fieldLabel(kindRow && kindRow.def, "scan_coordinate_kind")
      }));
      var expected = expectedScanAtomCount(kind);
      var atomCount = structure && structure.atomCount != null ? structure.atomCount : null;
      var parsedAtoms = parseScanAtoms(extras.scanAtoms, expected, atomCount);
      if (!parsedAtoms.ok) fail("scan_atoms", scanAtomError(parsedAtoms, atomCount));
      var startRow = coordinateLevel ? methodRow(coordinateLevel, "scan_coordinate_start") : null;
      var endRow = coordinateLevel ? methodRow(coordinateLevel, "scan_coordinate_end") : null;
      var pointRow = coordinateLevel ? methodRow(coordinateLevel, "scan_coordinate_points") : null;
      var start = formatScanValue(startRow && startRow.value);
      var end = formatScanValue(endRow && endRow.value);
      var points = pointRow ? numberFieldValue(pointRow.value) : null;
      if (!start) fail("scan_coordinate_start", lt("method_invalid_number", {
        label: fieldLabel(startRow && startRow.def, "scan_coordinate_start")
      }));
      if (!end) fail("scan_coordinate_end", lt("method_invalid_number", {
        label: fieldLabel(endRow && endRow.def, "scan_coordinate_end")
      }));
      if (adapter.pes) {
        inputData.source = {
          source_type: "xyz_text",
          xyz_text: textOf(structure.xyz),
          charge: charge === null ? 0 : charge,
          multiplicity: multiplicity === null ? 1 : multiplicity
        };
        if (parsedAtoms.ok) {
          var coordinate = {
            kind: kind,
            atoms: parsedAtoms.atoms,
            unit: kind === "distance" ? "angstrom" : "degree",
            start: start,
            end: end
          };
          if (points !== null) coordinate.n_points = points;
          var bondTypeRow = methodRow(coordinateLevel, "scan_bond_type");
          if (bondTypeRow && bondTypeRow.value) coordinate.bond_type = textOf(bondTypeRow.value);
          inputData.coordinate = coordinate;
        }
        inputData.protocol = buildPesProtocol(method, charge, multiplicity);
        var modeValues = Array.isArray(adapter.methodEnums.mode) ? adapter.methodEnums.mode : [];
        methodData = {
          mode: modeValues.length ? modeValues[0] : "",
          schema_id: textOf(method.schemaId)
        };
        if (method.profileId) methodData.profile_id = method.profileId;
      } else {
        inputData = {
          source_type: "xyz_text",
          source: textOf(structure.xyz),
          charge: charge === null ? 0 : charge,
          multiplicity: multiplicity === null ? 1 : multiplicity
        };
        if (parsedAtoms.ok) {
          inputData.scan_coordinates = [parsedAtoms.atoms.join(",") + "," + start + "," + end];
        }
      }
    } else {
      inputData = {
        source_type: "xyz_text",
        source: textOf(structure.xyz),
        charge: charge === null ? 0 : charge,
        multiplicity: multiplicity === null ? 1 : multiplicity
      };
    }
    if (!methodData && adapter.methodEmit === METHOD_EMIT_LEVELS) {
      methodData = {
        schema_id: textOf(method.schemaId),
        levels: preflightLevels(method)
      };
    } else if (!methodData && adapter.methodEmit === METHOD_EMIT_PROTOCOL) {
      var modes = Array.isArray(adapter.methodEnums.mode) ? adapter.methodEnums.mode : [];
      methodData = { mode: modes.length ? modes[0] : "", schema_id: textOf(method.schemaId) };
      if (method.profileId) methodData.profile_id = method.profileId;
    }
    if (errors.length) return { request: null, errors: errors, adapter: adapter };
    var request = {
      schema_version: 1,
      workflow: textOf(entry.id),
      input: inputData
    };
    if (methodData) request.method = methodData;
    var resources = {};
    if (input.cores != null) resources.nproc = input.cores;
    if (input.mem) resources.mem = input.mem;
    if (Object.keys(resources).length) request.resources = resources;
    if (input.projectId) request.project_id = textOf(input.projectId);
    return { request: request, errors: [], adapter: adapter };
  }

  /* ── structure projection (pure) ───────────────────────────────── */

  function numberOrNull(value) {
    if (value == null || value === "") return null;
    var number = Number(value);
    return isFinite(number) ? number : null;
  }

  function structureFromAsset(asset, source, filename) {
    asset = asset || {};
    return {
      source: textOf(source),
      name: textOf(asset.name || filename),
      xyz: textOf(asset.xyz),
      formula: textOf(asset.formula),
      atomCount: numberOrNull(asset.atom_count),
      charge: numberOrNull(asset.charge),
      multiplicity: numberOrNull(asset.multiplicity),
      smiles: textOf(asset.smiles)
    };
  }

  function structureFromResolve(resolveBody, embedBody) {
    resolveBody = resolveBody || {};
    embedBody = embedBody || {};
    return {
      source: "smiles",
      name: textOf(embedBody.smiles || resolveBody.smiles),
      xyz: textOf(embedBody.xyz),
      formula: textOf(embedBody.formula || resolveBody.formula),
      atomCount: numberOrNull(embedBody.num_atoms),
      charge: numberOrNull(resolveBody.charge),
      multiplicity: numberOrNull(resolveBody.multiplicity),
      smiles: textOf(resolveBody.smiles)
    };
  }

  function structureFromEmbedded(asset, embedBody, source, filename) {
    asset = asset || {};
    embedBody = embedBody || {};
    return {
      source: textOf(source),
      name: textOf(asset.name || filename || embedBody.smiles),
      xyz: textOf(embedBody.xyz),
      formula: textOf(embedBody.formula || asset.formula),
      atomCount: numberOrNull(embedBody.num_atoms != null ? embedBody.num_atoms : asset.atom_count),
      charge: numberOrNull(asset.charge),
      multiplicity: numberOrNull(asset.multiplicity),
      smiles: textOf(embedBody.smiles || asset.smiles)
    };
  }

  function unknown() {
    return t("next.wizard.unknown");
  }

  function structureSummaryText(structure) {
    if (!structure) return "";
    return t("next.wizard.structure_summary", {
      formula: structure.formula || unknown(),
      atoms: structure.atomCount == null ? unknown() : String(structure.atomCount),
      charge: structure.charge == null ? unknown() : String(structure.charge),
      multiplicity: structure.multiplicity == null ? unknown() : String(structure.multiplicity)
    });
  }

  /* ── module instance ───────────────────────────────────────────── */

  function createInstance(deps) {
    deps = deps || {};
    var doc = deps.doc || (typeof window !== "undefined" ? window.document : null);
    var context = deps.context || null;

    var state = {
      generation: 0,
      mounted: false,
      disposed: false,
      suspended: false,
      host: null,
      root: null,
      client: null,
      commands: null,
      snapshot: null,
      projectId: "",
      unsubscribe: null,
      /* Bumped on every edit that changes the frozen plan; the preflight
       * fingerprint and the memoized plan key are bound to it. */
      revision: 0,
      planCache: { revision: -1, key: "" },
      catalog: { status: "empty", error: null, token: 0 },
      workflows: [],
      methodCatalog: null,
      entry: null,
      method: emptyMethod(),
      step: 0,
      maxReached: 0,
      navHint: "",
      input: {
        mode: "smiles",
        status: "empty",
        token: 0,
        pending: "",
        error: null,
        smiles: "",
        text: "",
        fileName: "",
        structure: null,
        /* Raw typed strings — never normalized to a number here. */
        charge: "",
        multiplicity: ""
      },
      /* A-04: server-published resource defaults + the two optional inputs. */
      resources: {
        status: "empty",
        error: null,
        token: 0,
        defaults: null,
        cores: "",
        memory: "",
        touched: false,
        loaded: false
      },
      /* A-04: read-only queue projection of the node catalog. */
      queue: { status: "empty", error: null, token: 0, rows: [], loaded: false },
      /* A-07: the selected workflow's input contract (one request, one
       * workflow; never a second method/catalog cache). */
      contract: { status: "idle", token: 0, body: null, error: null, workflowId: "" },
      /* A-07: operator-provided workflow input that is not the A-02 structure
       * (source task binding, frozen payload, scan atom indices). */
      extras: {
        sourceJobId: "",
        sourceProductId: "",
        sourceModeIndex: "",
        scanAtoms: "",
        payload: ""
      },
      /* A-05: preflight lifecycle, digest and field-mapped rejections. */
      preflight: {
        status: "idle",
        token: 0,
        digest: "",
        planKey: "",
        revision: -1,
        /* The exact body that produced `digest`; A-06 submits this frozen copy. */
        request: null,
        error: null,
        errorStatus: null,
        errorCode: "",
        reasons: [],
        fieldErrors: {},
        advisories: [],
        attempts: 0
      },
      /* A-06: submit lifecycle, the plan-bound idempotency key and the receipt. */
      submit: {
        status: "idle",
        token: 0,
        idempotencyKey: "",
        keyPlan: "",
        receipt: null,
        receiptKey: "",
        error: null,
        errorStatus: null,
        errorCode: "",
        errorField: "",
        errorRule: "",
        attempts: 0
      },
      /* A-08: the pre-submit check dialog is open/closed (content is derived). */
      dialog: { open: false },
      /* A-09: the draft slice. One persisted presentation snapshot, its row
       * metadata, one debounce timer and the async guards — no second cache
       * (a loaded snapshot is held only until it is applied or discarded). */
      draft: {
        status: "idle",
        token: 0,
        saveToken: 0,
        meta: null,
        snapshot: null,
        lastSaved: "",
        timer: null,
        busy: false,
        queued: false,
        deleteQueued: null,
        restoreQueued: false,
        error: null,
        errorCode: "",
        notice: "",
        noticeKind: "",
        savedAt: "",
        writes: 0
      },
      ui: {}
    };

    var cleanup = [];

    function on(node, type, handler) {
      if (!node || typeof node.addEventListener !== "function") return;
      node.addEventListener(type, handler);
      cleanup.push(function () {
        if (typeof node.removeEventListener === "function") node.removeEventListener(type, handler);
      });
    }

    function resetInput() {
      var token = state.input && state.input.token ? state.input.token + 1 : 1;
      state.input = {
        mode: "smiles",
        status: "empty",
        token: token,
        pending: "",
        error: null,
        smiles: "",
        text: "",
        fileName: "",
        structure: null,
        charge: "",
        multiplicity: ""
      };
    }

    function resetResources() {
      state.resources = {
        status: "empty",
        error: null,
        token: state.resources.token + 1,
        defaults: null,
        cores: "",
        memory: "",
        touched: false,
        loaded: false
      };
      state.queue = {
        status: "empty",
        error: null,
        token: state.queue.token + 1,
        rows: [],
        loaded: false
      };
    }

    function resetExtras() {
      state.extras = {
        sourceJobId: "",
        sourceProductId: "",
        sourceModeIndex: "",
        scanAtoms: "",
        payload: ""
      };
    }

    function resetContract() {
      state.contract = {
        status: "idle",
        token: state.contract ? state.contract.token + 1 : 1,
        body: null,
        error: null,
        workflowId: ""
      };
    }

    function resetPreflight() {
      state.preflight = {
        status: "idle",
        token: state.preflight.token + 1,
        digest: "",
        planKey: "",
        revision: -1,
        request: null,
        error: null,
        errorStatus: null,
        errorCode: "",
        reasons: [],
        fieldErrors: {},
        advisories: [],
        attempts: 0
      };
    }

    function resetSubmit() {
      state.submit = {
        status: "idle",
        token: state.submit ? state.submit.token + 1 : 1,
        idempotencyKey: "",
        keyPlan: "",
        receipt: null,
        receiptKey: "",
        error: null,
        errorStatus: null,
        errorCode: "",
        errorField: "",
        errorRule: "",
        attempts: 0
      };
      state.dialog = { open: false };
    }

    /* ── A-09: draft timer + slice lifecycle ──────────────────────── */

    function draftDebounceMs() {
      var value = finiteNumber(deps.draftDebounceMs);
      if (value !== null && value >= 0) return value;
      return DRAFT_DEBOUNCE_MS;
    }

    function timerSet(fn, ms) {
      if (typeof deps.setTimeout === "function") return deps.setTimeout(fn, ms);
      if (typeof setTimeout === "function") return setTimeout(fn, ms);
      return null;
    }

    function timerClear(id) {
      if (id === null || id === undefined) return;
      if (typeof deps.clearTimeout === "function") deps.clearTimeout(id);
      else if (typeof clearTimeout === "function") clearTimeout(id);
    }

    function cancelDraftTimer() {
      if (!state.draft) return;
      if (state.draft.timer !== null && state.draft.timer !== undefined) {
        timerClear(state.draft.timer);
      }
      state.draft.timer = null;
    }

    function clearDraftMeta() {
      state.draft.meta = null;
      state.draft.snapshot = null;
      state.draft.lastSaved = "";
      state.draft.savedAt = "";
    }

    function resetDraft() {
      cancelDraftTimer();
      state.draft = {
        status: "idle",
        token: (state.draft ? state.draft.token : 0) + 1,
        saveToken: (state.draft ? state.draft.saveToken : 0) + 1,
        meta: null,
        snapshot: null,
        lastSaved: "",
        timer: null,
        busy: false,
        queued: false,
        deleteQueued: null,
        restoreQueued: false,
        error: null,
        errorCode: "",
        notice: "",
        noticeKind: "",
        savedAt: "",
        writes: 0
      };
    }

    /** Any plan-affecting edit: bumps the revision and invalidates a result. */
    function bumpRevision() {
      state.revision += 1;
      var status = state.preflight.status;
      if (status === "ok" || status === "infeasible" || status === "error" || status === "blocked") {
        state.preflight.status = "stale";
        state.preflight.reasons = [];
        state.preflight.fieldErrors = {};
        state.preflight.advisories = [];
        state.preflight.error = null;
        state.preflight.request = null;
      }
    }

    /* ── injected client ───────────────────────────────────────────– */

    function resolveClient() {
      var client = deps.client || null;
      if (typeof client === "function") return { request: client };
      if (client && typeof client.request === "function") return client;
      if (client && client.transport && typeof client.transport.request === "function") {
        return {
          request: function (path, opts) { return client.transport.request(path, opts); }
        };
      }
      if (typeof deps.request === "function") return { request: deps.request };
      if (typeof window !== "undefined" && window.ACPClient && window.ACPClient.transport &&
          typeof window.ACPClient.transport.request === "function") {
        return {
          request: function (path, opts) { return window.ACPClient.transport.request(path, opts); }
        };
      }
      return null;
    }

    function call(path, opts) {
      try {
        return Promise.resolve(state.client.request(path, opts));
      } catch (error) {
        return Promise.reject(error);
      }
    }

    function getJson(path) {
      return call(path, { method: "GET" });
    }

    function postJson(path, body) {
      return call(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        timeoutMs: REQUEST_TIMEOUT_MS
      });
    }

    /* ── guards ──────────────────────────────────────────────────── */

    function alive(gen) {
      return !state.disposed && state.mounted && state.generation === gen;
    }

    function catalogAlive(gen, token) {
      return alive(gen) && state.catalog.token === token;
    }

    function inputAlive(gen, token) {
      return alive(gen) && state.input.token === token;
    }

    function resourceAlive(gen, token) {
      return alive(gen) && state.resources.token === token;
    }

    function queueAlive(gen, token) {
      return alive(gen) && state.queue.token === token;
    }

    function contractAlive(gen, token) {
      return alive(gen) && state.contract.token === token;
    }

    function preflightAlive(gen, token) {
      return alive(gen) && state.preflight.token === token;
    }

    function submitAlive(gen, token) {
      return alive(gen) && state.submit.token === token;
    }

    function draftAlive(gen, token) {
      return alive(gen) && !!state.draft && state.draft.token === token;
    }

    function draftStateName() {
      var status = state.draft ? state.draft.status : "idle";
      if (status === "loading") return "loading";
      if (status === "conflict") return "conflict";
      if (status === "error" || (state.draft && state.draft.notice)) return "error";
      if (status === "found" || status === "saving" || status === "deleting" ||
          status === "saved" || status === "restored") {
        return "ready";
      }
      return "empty";
    }

    /* ── derived checks ──────────────────────────────────────────── */

    function electronicCheck() {
      return validateElectronic(state.input.charge, state.input.multiplicity, state.input.structure);
    }

    function electronicStateName() {
      if (state.input.status !== "ready") return "empty";
      return electronicCheck().ok ? "ready" : "error";
    }

    function currentAdapter() {
      return projectInputAdapter(state.entry, state.method, state.contract);
    }

    function contractErrorText() {
      if (!state.contract || state.contract.status !== "error") return "";
      return lt("contract_error", { message: describeError(state.contract.error) });
    }

    function inputCheck() {
      var adapter = currentAdapter();
      var built = buildWorkflowRequest({
        entry: state.entry,
        method: state.method,
        adapter: adapter,
        contract: state.contract,
        structure: state.input.structure,
        charge: state.input.charge,
        multiplicity: state.input.multiplicity,
        cores: resourceCheck().cores,
        mem: resourceCheck().mem,
        projectId: state.projectId,
        extras: state.extras
      });
      return {
        ok: built.errors.length === 0,
        errors: built.errors,
        request: built.request,
        adapter: adapter
      };
    }

    function scanAtomsValid() {
      var adapter = currentAdapter();
      if (adapter.kind !== ADAPTER_SCAN) return true;
      var coordinateLevel = scanCoordinateLevel(state.method);
      var kindRow = coordinateLevel ? methodRow(coordinateLevel, "scan_coordinate_kind") : null;
      var kind = textOf(kindRow && kindRow.value);
      if (!kind) return false;
      var atomCount = state.input.structure && state.input.structure.atomCount != null
        ? state.input.structure.atomCount
        : null;
      return parseScanAtoms(state.extras.scanAtoms, expectedScanAtomCount(kind), atomCount).ok;
    }

    function resourceCheck() {
      var defaults = state.resources.defaults || {};
      return validateResources(state.resources.cores, state.resources.memory, {
        coresMax: defaults.coresMax,
        memoryMaxMb: defaults.memoryMaxMb
      });
    }

    function currentPreflightRequest() {
      var check = inputCheck();
      return check.request;
    }

    function currentInputErrors() {
      return inputCheck().errors || [];
    }

    /** Memoized canonical identity of the current plan (revision-bound). */
    function currentPlanKey() {
      if (state.planCache.revision === state.revision) return state.planCache.key;
      var key = planIdentity(currentPreflightRequest());
      state.planCache = { revision: state.revision, key: key };
      return key;
    }

    function preflightValid() {
      if (state.preflight.status !== "ok" || !state.preflight.digest) return false;
      var key = currentPlanKey();
      return !!key && state.preflight.planKey === key;
    }

    function preflightStale() {
      if (!state.preflight.digest) return false;
      if (state.preflight.status === "stale") return true;
      if (state.preflight.status !== "ok") return false;
      return state.preflight.planKey !== currentPlanKey();
    }

    /** Four-state preflight surface value: idle | loading | ready | error. */
    function preflightStateName() {
      var status = state.preflight.status;
      if (status === "loading") return "loading";
      if (status === "ok") return preflightValid() ? "ready" : "error";
      if (status === "idle") return "idle";
      return "error";
    }

    /** A-08: the live pre-submit check for the current plan. */
    function currentSubmitCheck() {
      var defaults = state.resources.defaults || {};
      var adapter = currentAdapter();
      var duplicateFields = {
        structure: true,
        charge: true,
        multiplicity: true,
        workflow: true,
        method: true,
        workflow_contract: true
      };
      var methodErrorFields = {};
      validateMethodSchema(state.method).errors.forEach(function (error) {
        methodErrorFields[textOf(error.field)] = true;
      });
      var inputErrors = currentInputErrors().filter(function (error) {
        var field = textOf(error.field);
        return !duplicateFields[field] && !methodErrorFields[field];
      });
      return submitCheck({
        entry: state.entry,
        workflowLabel: state.entry ? optionLabel(state.entry) : "",
        method: state.method,
        adapterKind: adapter.kind,
        contractError: contractErrorText(),
        inputErrors: inputErrors,
        structureStatus: state.input.status,
        structure: state.input.structure,
        charge: state.input.charge,
        multiplicity: state.input.multiplicity,
        resourcesStatus: state.resources.status,
        resourcesError: state.resources.error,
        cores: state.resources.cores,
        memory: state.resources.memory,
        limits: { coresMax: defaults.coresMax, memoryMaxMb: defaults.memoryMaxMb },
        preflight: {
          status: state.preflight.status,
          digest: state.preflight.digest,
          planKey: state.preflight.planKey,
          valid: preflightValid(),
          error: state.preflight.error,
          errorCode: state.preflight.errorCode,
          reasons: state.preflight.reasons,
          advisories: state.preflight.advisories
        }
      });
    }

    function submittedForCurrentPlan() {
      return !!(state.submit.receipt && state.submit.receiptKey &&
        state.submit.receiptKey === currentPlanKey());
    }

    /** A request may leave only for a checked plan on the last step. */
    function canSubmit() {
      if (!state.mounted || state.disposed) return false;
      if (!state.client) return false;
      if (state.step !== STEP_IDS.length - 1) return false;
      if (state.submit.status === "loading") return false;
      if (submittedForCurrentPlan()) return false;
      return currentSubmitCheck().ok;
    }

    /* ── step validity ───────────────────────────────────────────── */

    function stepValid(index) {
      if (index === 0) {
        return state.catalog.status === "ready" && !!state.entry && !!state.method.schema &&
          validateMethodSchema(state.method).ok;
      }
      if (index === 1) {
        if (state.resources.status === "error" || state.resources.status === "loading" ||
            !resourceCheck().ok) {
          return false;
        }
        if (!validateMethodSchema(state.method).ok) return false;
        var adapter = currentAdapter();
        if (adapter.kind === "loading" || adapter.kind === "error") return false;
        if (adapter.kind === "source" || adapter.kind === "payload") {
          return inputCheck().ok;
        }
        return state.input.status === "ready" && electronicCheck().ok &&
          inputCheck().ok && scanAtomsValid();
      }
      /* Review: a failed / stale preflight blocks; an unrun preflight does not
       * (running it is the step's own action). */
      return preflightStateName() !== "error";
    }

    function stepReason(index) {
      if (index === 0) {
        if (state.catalog.status === "loading") return t("next.wizard.hint_catalog_loading");
        if (state.catalog.status === "error") return t("next.wizard.hint_catalog_error");
        if (state.catalog.status === "empty") return t("next.wizard.hint_no_workflow");
        if (!state.entry) return t("next.wizard.err.workflow_required");
        if (!state.method.schema) {
          return t("next.wizard.err.schema_missing", { workflow: optionLabel(state.entry) });
        }
        var methodCheck = validateMethodSchema(state.method);
        if (!methodCheck.ok) return methodCheck.errors[0].text;
        return "";
      }
      if (index === 1) {
        var adapter = currentAdapter();
        if (adapter.kind === "loading") return lt("contract_loading");
        if (adapter.kind === "error") return contractErrorText();
        var structureRequired = adapter.kind !== "source" && adapter.kind !== "payload";
        if (structureRequired) {
          if (state.input.status !== "ready") {
            if (state.input.status === "loading") return t("next.wizard.hint_structure_loading");
            if (state.input.status === "error") return t("next.wizard.hint_structure_error");
            return t("next.wizard.err.structure_required");
          }
          var electronic = electronicCheck();
          if (!electronic.ok) return errorText(electronic.errors[0]);
        }
        var input = inputCheck();
        if (!input.ok) return input.errors[0].text;
        if (state.resources.status === "loading") return lt("resources_loading");
        if (state.resources.status === "error") {
          return t("next.nodes.section_error", {
            section: t("next.approval.plan.resources"),
            message: describeError(state.resources.error)
          });
        }
        var resource = resourceCheck();
        if (!resource.ok) return errorText(resource.errors[0]);
        return "";
      }
      if (index === 2) {
        if (preflightStale()) return t("next.capabilityForm.preflight.stale");
        if (state.preflight.status === "infeasible") return t("next.config.preflight_failed");
        if (state.preflight.status === "error") {
          return t("next.s2scan.preflight_error", { message: state.preflight.error || "" });
        }
        if (state.preflight.status === "blocked") return t("next.s2scan.preflight_blocked");
        return "";
      }
      return "";
    }

    function canNext() {
      return state.mounted && !state.disposed && state.step < STEP_IDS.length - 1 && stepValid(state.step);
    }

    function lockDownstream(stepIndex) {
      if (state.step > stepIndex) state.step = stepIndex;
      if (state.maxReached > stepIndex) state.maxReached = stepIndex;
    }

    /* ── DOM ─────────────────────────────────────────────────────── */

    function buildDom() {
      state.ui.panels = {};
      var root = el(doc, "div", "acp-wizard");
      root.setAttribute("data-module", MODULE_ID);
      root.setAttribute("data-acp-module-state", "loading");
      root.setAttribute("data-acp-step", STEP_IDS[0]);
      root.setAttribute("data-acp-step-index", "0");
      root.setAttribute("data-acp-step-count", String(STEP_IDS.length));
      root.setAttribute("data-acp-max-step", "0");
      root.setAttribute("data-acp-step-valid", "false");
      root.setAttribute("data-acp-can-next", "false");
      root.setAttribute("data-acp-input-mode", state.input.mode);
      root.setAttribute("data-acp-structure-state", state.input.status);
      root.setAttribute("data-acp-electronic-state", "empty");
      root.setAttribute("data-acp-resource-state", state.resources.status);
      root.setAttribute("data-acp-queue-state", state.queue.status);
      root.setAttribute("data-acp-contract-state", state.contract.status);
      root.setAttribute("data-acp-input-adapter", ADAPTER_STRUCTURE);
      root.setAttribute("data-acp-method-projection", state.method.extended ? "extended" : "minimal");
      root.setAttribute("data-acp-method-support", "");
      root.setAttribute("data-acp-preflight-state", "idle");
      root.setAttribute("data-acp-preflight-valid", "false");
      root.setAttribute("data-acp-preflight-stale", "false");
      root.setAttribute("data-acp-can-preflight", "false");
      root.setAttribute("data-acp-plan-digest", "");
      root.setAttribute("data-acp-preflight-fingerprint", "");
      root.setAttribute("data-acp-submit-state", "idle");
      root.setAttribute("data-acp-submit-check", "blocked");
      root.setAttribute("data-acp-can-submit", "false");
      root.setAttribute("data-acp-submit-error-status", "");
      root.setAttribute("data-acp-submit-error-code", "");
      root.setAttribute("data-acp-submit-error-field", "");
      root.setAttribute("data-acp-submit-error-rule", "");
      root.setAttribute("data-acp-receipt-job", "");
      root.setAttribute("data-acp-draft-state", "empty");
      root.setAttribute("data-acp-draft-status", "idle");
      root.setAttribute("data-acp-draft-busy", "false");
      root.setAttribute("data-acp-busy", "none");

      var head = el(doc, "header", "acp-wizard-head");
      head.appendChild(i18nEl(doc, "h2", "acp-wizard-title", "next.wizard.title"));
      head.appendChild(i18nEl(doc, "p", "acp-wizard-sub", "next.wizard.subtitle"));
      root.appendChild(head);

      root.appendChild(buildDraftBanner());
      root.appendChild(buildSteps());
      root.appendChild(buildCatalogRow());
      root.appendChild(buildBody());
      root.appendChild(buildActions());
      root.appendChild(buildSubmitDialog());

      state.root = root;
      state.host.appendChild(root);
      on(doc, "acp:langchange", function () { renderAll(); });
    }

    function buildSteps() {
      var nav = el(doc, "nav", "acp-wizard-steps");
      nav.setAttribute("data-role", "steps");
      nav.setAttribute("aria-label", t("next.wizard.steps_label"));
      nav.setAttribute("data-i18n-aria", "next.wizard.steps_label");
      state.ui.stepButtons = [];
      STEP_IDS.forEach(function (id, index) {
        var button = el(doc, "button", "acp-wizard-step");
        button.type = "button";
        button.setAttribute("data-step-nav", String(index));
        button.setAttribute("data-step-state", "locked");
        button.appendChild(el(doc, "span", "acp-wizard-step-index", String(index + 1)));
        button.appendChild(i18nEl(doc, "span", "acp-wizard-step-label", STEP_KEYS[id]));
        on(button, "click", function () { goToStep(index); });
        nav.appendChild(button);
        state.ui.stepButtons.push(button);
      });
      return nav;
    }

    /* A-09: the restore / save banner. It is the only draft surface: a status
     * line, a non-blocking failure notice and the four explicit actions
     * (restore / discard / load-latest / save-as-new-draft). Buttons are
     * derived on every render, so a missing draft can never show an action. */
    function buildDraftBanner() {
      var banner = el(doc, "div", "acp-wizard-draft");
      banner.setAttribute("data-role", "draft");
      banner.setAttribute("data-acp-draft-state", "empty");
      banner.setAttribute("data-acp-draft-status", "idle");
      banner.setAttribute("data-acp-draft-busy", "false");
      banner.hidden = true;
      state.ui.draft = banner;

      var status = el(doc, "p", "acp-wizard-draft-text", "");
      status.setAttribute("data-role", "draft-status");
      status.setAttribute("aria-live", "polite");
      banner.appendChild(status);
      state.ui.draftStatus = status;

      var notice = el(doc, "p", "acp-wizard-error", "");
      notice.setAttribute("data-role", "draft-notice");
      notice.setAttribute("role", "alert");
      notice.hidden = true;
      banner.appendChild(notice);
      state.ui.draftNotice = notice;

      var actions = el(doc, "div", "acp-wizard-draft-actions");
      state.ui.draftActions = actions;

      function draftButton(role, key, handler) {
        var button = el(doc, "button", "acp-wizard-btn", t(key));
        button.type = "button";
        button.setAttribute("data-action", role);
        button.setAttribute("data-i18n", key);
        button.hidden = true;
        on(button, "click", handler);
        actions.appendChild(button);
        return button;
      }

      state.ui.draftRestore = draftButton("draft-restore", "next.config.draft_restore", function () {
        restoreDraft();
      });
      state.ui.draftDiscard = draftButton("draft-discard", "next.config.draft_discard", function () {
        discardDraft();
      });
      state.ui.draftReload = draftButton("draft-reload", "next.config.draft_reload", function () {
        reloadDraft();
      });
      state.ui.draftFork = draftButton("draft-fork", "next.config.draft_fork", function () {
        forkDraft();
      });
      banner.appendChild(actions);
      return banner;
    }

    function buildCatalogRow() {
      var row = el(doc, "div", "acp-wizard-catalog");
      var status = el(doc, "p", "acp-wizard-status", t("next.wizard.catalog_loading"));
      status.setAttribute("data-role", "catalog-status");
      status.setAttribute("data-acp-catalog-state", "loading");
      status.setAttribute("aria-live", "polite");
      row.appendChild(status);
      state.ui.catalogStatus = status;
      var retry = el(doc, "button", "acp-wizard-btn", t("next.wizard.retry"));
      retry.type = "button";
      retry.setAttribute("data-action", "retry-catalog");
      retry.setAttribute("data-i18n", "next.wizard.retry");
      retry.hidden = true;
      on(retry, "click", function () { retryCatalog(); });
      row.appendChild(retry);
      state.ui.retry = retry;
      return row;
    }

    function buildBody() {
      var body = el(doc, "div", "acp-wizard-body");
      body.appendChild(buildWorkflowPanel());
      body.appendChild(buildInputPanel());
      body.appendChild(buildReviewPanel());
      return body;
    }

    function registerPanel(id, node) {
      state.ui.panels[id] = node;
      node.setAttribute("data-step-panel", id);
      return node;
    }

    function buildWorkflowPanel() {
      var section = registerPanel("workflow", el(doc, "section", "acp-wizard-panel"));
      section.appendChild(i18nEl(doc, "h3", "acp-wizard-heading", "next.wizard.workflow_title"));

      var label = i18nEl(doc, "label", "acp-wizard-label", "next.wizard.workflow_label");
      var select = el(doc, "select", "acp-wizard-select");
      select.setAttribute("data-role", "workflow");
      label.appendChild(select);
      section.appendChild(label);
      state.ui.workflowSelect = select;
      on(select, "change", function () {
        var entry = findWorkflow(state.workflows, select.value);
        if (entry) {
          selectWorkflow(entry);
          scheduleDraftSave();
        }
      });

      var desc = el(doc, "p", "acp-wizard-note", t("next.wizard.workflow_desc_empty"));
      desc.setAttribute("data-role", "workflow-desc");
      section.appendChild(desc);
      state.ui.workflowDesc = desc;

      var error = el(doc, "p", "acp-wizard-error");
      error.setAttribute("data-role", "workflow-error");
      error.setAttribute("role", "alert");
      error.hidden = true;
      section.appendChild(error);
      state.ui.workflowError = error;

      section.appendChild(i18nEl(doc, "h4", "acp-wizard-subheading", "next.wizard.method_title"));

      var profileWrap = el(doc, "div", "acp-wizard-field");
      profileWrap.setAttribute("data-role", "profile-wrap");
      var profileLabel = i18nEl(doc, "label", "acp-wizard-label", "next.wizard.method_profile");
      var profileSelect = el(doc, "select", "acp-wizard-select");
      profileSelect.setAttribute("data-role", "profile");
      profileLabel.appendChild(profileSelect);
      profileWrap.appendChild(profileLabel);
      profileWrap.hidden = true;
      section.appendChild(profileWrap);
      state.ui.profileWrap = profileWrap;
      state.ui.profileSelect = profileSelect;
      on(profileSelect, "change", function () { selectProfile(profileSelect.value); });

      var levels = el(doc, "div", "acp-wizard-levels");
      levels.setAttribute("data-role", "method-levels");
      section.appendChild(levels);
      state.ui.methodLevels = levels;
      on(levels, "change", onMethodFieldChange);
      on(levels, "input", onMethodFieldChange);

      var methodError = fieldErrorEl("method");
      section.appendChild(methodError);
      state.ui.methodFieldError = methodError;

      section.appendChild(i18nEl(doc, "p", "acp-wizard-note", "next.wizard.method_source"));
      return section;
    }

    function buildInputPanel() {
      var section = registerPanel("input", el(doc, "section", "acp-wizard-panel"));
      section.setAttribute("data-acp-structure-state", "empty");
      section.setAttribute("data-acp-input-mode", state.input.mode);
      state.ui.inputPanel = section;
      section.appendChild(i18nEl(doc, "h3", "acp-wizard-heading", "next.wizard.input_title"));

      /* A-07: one dynamic block for the workflow-specific input contract. It is
       * hidden for the A-02-only workflows and never replaces the structure
       * panel for the adapters that still consume a structure. */
      section.appendChild(buildWorkflowInputBlock());

      var tabs = el(doc, "div", "acp-wizard-tabs");
      tabs.setAttribute("role", "tablist");
      state.ui.modeButtons = {};
      INPUT_MODES.forEach(function (mode) {
        var button = el(doc, "button", "acp-wizard-tab" + (mode === state.input.mode ? " is-active" : ""), t(MODE_KEYS[mode]));
        button.type = "button";
        button.setAttribute("data-input-mode", mode);
        button.setAttribute("data-i18n", MODE_KEYS[mode]);
        button.setAttribute("role", "tab");
        button.setAttribute("aria-selected", mode === state.input.mode ? "true" : "false");
        on(button, "click", function () { setInputMode(mode); });
        tabs.appendChild(button);
        state.ui.modeButtons[mode] = button;
      });
      section.appendChild(tabs);

      section.appendChild(buildSmilesPanel());
      section.appendChild(buildPastePanel());
      section.appendChild(buildFilePanel());

      var status = el(doc, "p", "acp-wizard-status", t("next.wizard.structure_empty"));
      status.setAttribute("data-role", "structure-status");
      status.setAttribute("aria-live", "polite");
      section.appendChild(status);
      state.ui.structureStatus = status;

      var error = el(doc, "p", "acp-wizard-error");
      error.setAttribute("data-role", "structure-error");
      error.setAttribute("role", "alert");
      error.hidden = true;
      section.appendChild(error);
      state.ui.structureError = error;

      var summary = el(doc, "p", "acp-wizard-summary");
      summary.setAttribute("data-role", "structure-summary");
      summary.hidden = true;
      section.appendChild(summary);
      state.ui.structureSummary = summary;

      /* A-03: charge / spin multiplicity with the ΣZ − charge parity check. */
      section.appendChild(buildElectronicBlock());

      /* A-04: resources from the server-published admission view + the
       * read-only queue projection of the node catalog. */
      section.appendChild(buildResourcesBlock());
      section.appendChild(buildQueueBlock());
      return section;
    }

    function fieldErrorEl(role) {
      var node = el(doc, "p", "acp-wizard-error");
      node.setAttribute("data-role", "field-error");
      node.setAttribute("data-field", role);
      node.setAttribute("role", "alert");
      node.hidden = true;
      return node;
    }

    function buildElectronicBlock() {
      var block = el(doc, "div", "acp-wizard-subpanel acp-wizard-state");
      block.setAttribute("data-role", "electronic");
      block.setAttribute("data-acp-electronic-state", "empty");
      state.ui.electronicBlock = block;
      block.appendChild(el(doc, "h4", "acp-wizard-subheading", lt("electronic_title")));

      var grid = el(doc, "div", "acp-wizard-grid");
      var chargeLabel = i18nEl(doc, "label", "acp-wizard-label", "next.config.charge");
      var charge = el(doc, "input", "acp-wizard-input");
      charge.type = "text";
      charge.setAttribute("inputmode", "numeric");
      charge.setAttribute("data-role", "charge");
      chargeLabel.appendChild(charge);
      grid.appendChild(chargeLabel);
      state.ui.chargeInput = charge;
      on(charge, "input", function () {
        state.input.charge = textOf(charge.value);
        bumpRevision();
        scheduleDraftSave();
        renderElectronic();
        renderRoot();
        renderReview();
        renderSubmit();
        renderActions();
      });

      var multLabel = i18nEl(doc, "label", "acp-wizard-label", "next.config.multiplicity");
      var mult = el(doc, "input", "acp-wizard-input");
      mult.type = "text";
      mult.setAttribute("inputmode", "numeric");
      mult.setAttribute("data-role", "multiplicity");
      multLabel.appendChild(mult);
      grid.appendChild(multLabel);
      state.ui.multiplicityInput = mult;
      on(mult, "input", function () {
        state.input.multiplicity = textOf(mult.value);
        bumpRevision();
        scheduleDraftSave();
        renderElectronic();
        renderRoot();
        renderReview();
        renderSubmit();
        renderActions();
      });
      block.appendChild(grid);

      var parity = el(doc, "p", "acp-wizard-note");
      parity.setAttribute("data-role", "parity-hint");
      parity.hidden = true;
      block.appendChild(parity);
      state.ui.parityHint = parity;

      var chargeError = fieldErrorEl("charge");
      block.appendChild(chargeError);
      state.ui.chargeError = chargeError;

      var multError = fieldErrorEl("multiplicity");
      block.appendChild(multError);
      state.ui.multiplicityError = multError;

      var generic = fieldErrorEl("structure");
      block.appendChild(generic);
      state.ui.structureFieldError = generic;
      return block;
    }

    function buildResourcesBlock() {
      var block = el(doc, "div", "acp-wizard-subpanel acp-wizard-state");
      block.setAttribute("data-role", "resources");
      block.setAttribute("data-acp-resource-state", state.resources.status);
      state.ui.resourcesBlock = block;
      block.appendChild(i18nEl(doc, "h4", "acp-wizard-subheading", "next.approval.plan.resources"));

      var status = el(doc, "p", "acp-wizard-status", "");
      status.setAttribute("data-role", "resource-status");
      status.setAttribute("aria-live", "polite");
      status.hidden = true;
      block.appendChild(status);
      state.ui.resourceStatus = status;

      var grid = el(doc, "div", "acp-wizard-grid");
      var coresLabel = i18nEl(doc, "label", "acp-wizard-label", "next.config.nproc");
      var cores = el(doc, "input", "acp-wizard-input");
      cores.type = "text";
      cores.setAttribute("inputmode", "numeric");
      cores.setAttribute("data-role", "cores");
      coresLabel.appendChild(cores);
      grid.appendChild(coresLabel);
      state.ui.coresInput = cores;
      on(cores, "input", function () {
        state.resources.cores = textOf(cores.value);
        state.resources.touched = true;
        bumpRevision();
        scheduleDraftSave();
        renderResources();
        renderRoot();
        renderReview();
        renderSubmit();
        renderActions();
      });

      var memoryLabel = i18nEl(doc, "label", "acp-wizard-label", "next.config.mem");
      var memory = el(doc, "input", "acp-wizard-input");
      memory.type = "text";
      memory.setAttribute("data-role", "memory");
      memory.setAttribute("placeholder", "8GB");
      memoryLabel.appendChild(memory);
      grid.appendChild(memoryLabel);
      state.ui.memoryInput = memory;
      on(memory, "input", function () {
        state.resources.memory = textOf(memory.value);
        state.resources.touched = true;
        bumpRevision();
        scheduleDraftSave();
        renderResources();
        renderRoot();
        renderReview();
        renderSubmit();
        renderActions();
      });
      block.appendChild(grid);

      block.appendChild(el(doc, "p", "acp-wizard-note", lt("memory_format")));

      var retry = el(doc, "button", "acp-wizard-btn", t("next.config.retry"));
      retry.type = "button";
      retry.setAttribute("data-action", "retry-resources");
      retry.setAttribute("data-i18n", "next.config.retry");
      retry.hidden = true;
      on(retry, "click", function () { retryResources(); });
      block.appendChild(retry);
      state.ui.resourceRetry = retry;

      var coresError = fieldErrorEl("cores");
      block.appendChild(coresError);
      state.ui.coresError = coresError;

      var memoryError = fieldErrorEl("memory");
      block.appendChild(memoryError);
      state.ui.memoryError = memoryError;

      var groupError = fieldErrorEl("resources");
      block.appendChild(groupError);
      state.ui.resourcesFieldError = groupError;
      return block;
    }

    function buildQueueBlock() {
      var block = el(doc, "div", "acp-wizard-subpanel acp-wizard-state");
      block.setAttribute("data-role", "queue");
      block.setAttribute("data-acp-queue-state", state.queue.status);
      state.ui.queueBlock = block;
      block.appendChild(i18nEl(doc, "h5", "acp-wizard-level-title", "next.nodes.meta_queue"));
      var list = el(doc, "div", "acp-wizard-queue");
      list.setAttribute("data-role", "resource-queue");
      block.appendChild(list);
      state.ui.queueList = list;
      var note = el(doc, "p", "acp-wizard-note", lt("queue_hint"));
      block.appendChild(note);
      state.ui.queueNote = note;
      return block;
    }

    /* A-07: workflow-specific input (source binding / frozen payload / scan
     * atoms). Every control is derived from the contract adapter; nothing is
     * shown for a workflow whose adapter is the plain A-02 structure. */
    function buildWorkflowInputBlock() {
      var block = el(doc, "div", "acp-wizard-subpanel acp-wizard-workflow-input");
      block.setAttribute("data-role", "workflow-input");
      block.setAttribute("data-acp-contract-state", state.contract.status);
      block.hidden = true;
      state.ui.workflowInputBlock = block;
      block.appendChild(el(doc, "h4", "acp-wizard-subheading", lt("workflow_input_title")));
      var hint = el(doc, "p", "acp-wizard-note", lt("workflow_input_hint"));
      block.appendChild(hint);

      var contractStatus = el(doc, "p", "acp-wizard-status", "");
      contractStatus.setAttribute("data-role", "contract-status");
      contractStatus.setAttribute("aria-live", "polite");
      contractStatus.hidden = true;
      block.appendChild(contractStatus);
      state.ui.contractStatus = contractStatus;

      var retry = el(doc, "button", "acp-wizard-btn", lt("contract_retry"));
      retry.type = "button";
      retry.setAttribute("data-action", "retry-contract");
      retry.hidden = true;
      on(retry, "click", function () { retryContract(); });
      block.appendChild(retry);
      state.ui.contractRetry = retry;

      /* Frozen upstream request (payload adapter). */
      var payloadWrap = el(doc, "div", "acp-wizard-field");
      payloadWrap.setAttribute("data-role", "workflow-payload-wrap");
      payloadWrap.hidden = true;
      var payloadLabel = el(doc, "label", "acp-wizard-label", "");
      payloadLabel.setAttribute("data-role", "workflow-payload-label");
      var payloadText = el(doc, "span", "acp-wizard-label-text", "");
      payloadLabel.appendChild(payloadText);
      var payload = el(doc, "textarea", "acp-wizard-text");
      payload.setAttribute("rows", "8");
      payload.setAttribute("data-role", "workflow-payload");
      payload.setAttribute("placeholder", lt("payload_placeholder"));
      payloadLabel.appendChild(payload);
      payloadWrap.appendChild(payloadLabel);
      payloadWrap.appendChild(el(doc, "p", "acp-wizard-note", lt("payload_hint")));
      var payloadError = fieldErrorEl("payload");
      payloadWrap.appendChild(payloadError);
      block.appendChild(payloadWrap);
      state.ui.payloadWrap = payloadWrap;
      state.ui.payloadText = payloadText;
      state.ui.payloadInput = payload;
      state.ui.payloadError = payloadError;
      on(payload, "input", function () {
        state.extras.payload = textOf(payload.value);
        bumpRevision();
        scheduleDraftSave();
        renderWorkflowInput();
        renderRoot();
        renderReview();
        renderPreflight();
        renderSubmit();
        renderActions();
      });

      /* Existing task product (source adapter). */
      var sourceWrap = el(doc, "div", "acp-wizard-field");
      sourceWrap.setAttribute("data-role", "workflow-source-wrap");
      sourceWrap.hidden = true;
      var grid = el(doc, "div", "acp-wizard-grid");
      var jobLabel = el(doc, "label", "acp-wizard-label", lt("source_job"));
      var job = el(doc, "input", "acp-wizard-input");
      job.type = "text";
      job.setAttribute("data-role", "source-job");
      job.setAttribute("placeholder", lt("source_job_placeholder"));
      jobLabel.appendChild(job);
      grid.appendChild(jobLabel);
      state.ui.sourceJobInput = job;
      on(job, "input", function () {
        state.extras.sourceJobId = textOf(job.value);
        bumpRevision();
        renderWorkflowInput();
        renderRoot();
        renderReview();
        renderPreflight();
        renderSubmit();
        renderActions();
      });

      var productLabel = el(doc, "label", "acp-wizard-label", lt("source_product"));
      var product = el(doc, "input", "acp-wizard-input");
      product.type = "text";
      product.setAttribute("data-role", "source-product");
      product.setAttribute("placeholder", lt("source_product_placeholder"));
      productLabel.appendChild(product);
      grid.appendChild(productLabel);
      state.ui.sourceProductInput = product;
      on(product, "input", function () {
        state.extras.sourceProductId = textOf(product.value);
        bumpRevision();
        renderWorkflowInput();
        renderRoot();
        renderReview();
        renderPreflight();
        renderSubmit();
        renderActions();
      });

      var modeLabel = el(doc, "label", "acp-wizard-label", lt("source_mode_index"));
      var mode = el(doc, "input", "acp-wizard-input");
      mode.type = "text";
      mode.setAttribute("inputmode", "numeric");
      mode.setAttribute("data-role", "source-mode-index");
      modeLabel.appendChild(mode);
      grid.appendChild(modeLabel);
      state.ui.sourceModeInput = mode;
      on(mode, "input", function () {
        state.extras.sourceModeIndex = textOf(mode.value);
        bumpRevision();
        renderWorkflowInput();
        renderRoot();
        renderReview();
        renderPreflight();
        renderSubmit();
        renderActions();
      });
      sourceWrap.appendChild(grid);
      sourceWrap.appendChild(el(doc, "p", "acp-wizard-note", lt("source_mode_hint")));
      var jobError = fieldErrorEl("source_job_id");
      sourceWrap.appendChild(jobError);
      var modeError = fieldErrorEl("source_mode_index");
      sourceWrap.appendChild(modeError);
      block.appendChild(sourceWrap);
      state.ui.sourceWrap = sourceWrap;
      state.ui.sourceJobError = jobError;
      state.ui.sourceModeError = modeError;

      /* Scan atom indices (scan adapter). */
      var scanWrap = el(doc, "div", "acp-wizard-field");
      scanWrap.setAttribute("data-role", "workflow-scan-wrap");
      scanWrap.hidden = true;
      var scanLabel = el(doc, "label", "acp-wizard-label", lt("scan_atoms"));
      var atoms = el(doc, "input", "acp-wizard-input");
      atoms.type = "text";
      atoms.setAttribute("data-role", "scan-atoms");
      atoms.setAttribute("placeholder", lt("scan_atoms_placeholder"));
      scanLabel.appendChild(atoms);
      scanWrap.appendChild(scanLabel);
      scanWrap.appendChild(el(doc, "p", "acp-wizard-note", lt("scan_atoms_hint")));
      var scanError = fieldErrorEl("scan_atoms");
      scanWrap.appendChild(scanError);
      block.appendChild(scanWrap);
      state.ui.scanWrap = scanWrap;
      state.ui.scanAtomsInput = atoms;
      state.ui.scanAtomsError = scanError;
      on(atoms, "input", function () {
        state.extras.scanAtoms = textOf(atoms.value);
        bumpRevision();
        scheduleDraftSave();
        renderWorkflowInput();
        renderRoot();
        renderReview();
        renderPreflight();
        renderSubmit();
        renderActions();
      });

      var batchNote = el(doc, "p", "acp-wizard-note", lt("batch_note"));
      batchNote.setAttribute("data-role", "workflow-batch-note");
      batchNote.hidden = true;
      block.appendChild(batchNote);
      state.ui.batchNote = batchNote;
      return block;
    }

    function buildSmilesPanel() {
      var panel = el(doc, "div", "acp-wizard-subpanel");
      panel.setAttribute("data-role", "panel-smiles");
      state.ui.smilesPanel = panel;
      var label = i18nEl(doc, "label", "acp-wizard-label", "next.wizard.smiles_label");
      var input = el(doc, "input", "acp-wizard-input");
      input.type = "text";
      input.setAttribute("data-role", "smiles");
      input.setAttribute("data-i18n-ph", "next.wizard.smiles_placeholder");
      input.setAttribute("placeholder", t("next.wizard.smiles_placeholder"));
      label.appendChild(input);
      panel.appendChild(label);
      state.ui.smilesInput = input;
      on(input, "input", function () {
        state.input.smiles = textOf(input.value);
        state.input.mode = "smiles";
        invalidateEditedInput();
        scheduleDraftSave();
      });
      var action = el(doc, "button", "acp-wizard-btn", t("next.wizard.smiles_action"));
      action.type = "button";
      action.setAttribute("data-action", "resolve-smiles");
      action.setAttribute("data-i18n", "next.wizard.smiles_action");
      on(action, "click", function () { resolveSmiles(); });
      panel.appendChild(action);
      state.ui.resolveBtn = action;
      return panel;
    }

    function buildPastePanel() {
      var panel = el(doc, "div", "acp-wizard-subpanel");
      panel.setAttribute("data-role", "panel-paste");
      panel.hidden = true;
      state.ui.pastePanel = panel;
      var label = i18nEl(doc, "label", "acp-wizard-label", "next.wizard.paste_label");
      var text = el(doc, "textarea", "acp-wizard-text");
      text.setAttribute("rows", "6");
      text.setAttribute("data-role", "structure-text");
      text.setAttribute("data-i18n-ph", "next.wizard.paste_placeholder");
      text.setAttribute("placeholder", t("next.wizard.paste_placeholder"));
      label.appendChild(text);
      panel.appendChild(label);
      state.ui.textInput = text;
      on(text, "input", function () {
        state.input.text = textOf(text.value);
        state.input.mode = "paste";
        invalidateEditedInput();
        scheduleDraftSave();
      });
      var action = el(doc, "button", "acp-wizard-btn", t("next.wizard.paste_action"));
      action.type = "button";
      action.setAttribute("data-action", "parse-text");
      action.setAttribute("data-i18n", "next.wizard.paste_action");
      on(action, "click", function () { parseText(); });
      panel.appendChild(action);
      state.ui.parseBtn = action;
      return panel;
    }

    function buildFilePanel() {
      var panel = el(doc, "div", "acp-wizard-subpanel");
      panel.setAttribute("data-role", "panel-file");
      panel.hidden = true;
      state.ui.filePanel = panel;
      var label = i18nEl(doc, "label", "acp-wizard-label", "next.wizard.file_label");
      var input = el(doc, "input", "acp-wizard-file");
      input.type = "file";
      input.setAttribute("data-role", "structure-file");
      input.setAttribute("accept", ".xyz,.mol,.sdf,.gjf,.inp,.log,.out,.smi,.smiles,text/plain");
      label.appendChild(input);
      panel.appendChild(label);
      state.ui.fileInput = input;
      on(input, "change", function () { readChosenFile(); });
      panel.appendChild(i18nEl(doc, "p", "acp-wizard-note", "next.wizard.file_hint"));
      var name = el(doc, "p", "acp-wizard-note", t("next.wizard.file_none"));
      name.setAttribute("data-role", "file-name");
      panel.appendChild(name);
      state.ui.fileName = name;
      return panel;
    }

    function buildReviewPanel() {
      var section = registerPanel("review", el(doc, "section", "acp-wizard-panel"));
      section.appendChild(i18nEl(doc, "h3", "acp-wizard-heading", "next.wizard.review_title"));
      var list = el(doc, "dl", "acp-wizard-review");
      state.ui.review = {};
      [
        { role: "review-workflow", key: "next.wizard.review_workflow" },
        { role: "review-method", key: "next.wizard.review_method" },
        { role: "review-structure", key: "next.wizard.review_structure" }
      ].forEach(function (item) {
        var row = el(doc, "div", "acp-wizard-review-row");
        row.appendChild(i18nEl(doc, "dt", "acp-wizard-label", item.key));
        var dd = el(doc, "dd", "acp-wizard-review-value", t("next.wizard.review_missing"));
        dd.setAttribute("data-role", item.role);
        row.appendChild(dd);
        list.appendChild(row);
        state.ui.review[item.role] = dd;
      });
      section.appendChild(list);
      var stateLine = el(doc, "p", "acp-wizard-note");
      stateLine.setAttribute("data-role", "review-state");
      section.appendChild(stateLine);
      state.ui.reviewState = stateLine;
      var resourcesLine = el(doc, "p", "acp-wizard-note");
      resourcesLine.setAttribute("data-role", "review-resources");
      section.appendChild(resourcesLine);
      state.ui.reviewResources = resourcesLine;
      section.appendChild(buildPreflightBlock());
      section.appendChild(buildSubmitBlock());
      return section;
    }

    /* A-06: the submit row + receipt of the frozen A-05 plan. The submit button
     * only opens the A-08 check dialog; no request leaves from a click here. */
    function buildSubmitBlock() {
      var block = el(doc, "div", "acp-wizard-subpanel acp-wizard-submit");
      block.setAttribute("data-role", "submit");
      block.appendChild(i18nEl(doc, "h4", "acp-wizard-subheading", "next.config.review"));

      var row = el(doc, "div", "acp-wizard-submit-row");
      var submit = el(doc, "button", "acp-wizard-btn acp-wizard-btn--primary", t("next.config.submit"));
      submit.type = "button";
      submit.setAttribute("data-action", "submit");
      submit.setAttribute("data-i18n", "next.config.submit");
      submit.hidden = true;
      on(submit, "click", function () { openSubmitCheck(); });
      row.appendChild(submit);
      state.ui.submitBtn = submit;

      var status = el(doc, "p", "acp-wizard-submit-status");
      status.setAttribute("data-role", "submit-status");
      status.setAttribute("aria-live", "polite");
      row.appendChild(status);
      state.ui.submitStatus = status;
      block.appendChild(row);

      block.appendChild(buildReceiptBlock());
      return block;
    }

    function buildReceiptBlock() {
      var receipt = el(doc, "div", "acp-wizard-receipt");
      receipt.setAttribute("data-role", "receipt");
      receipt.setAttribute("data-acp-receipt-state", "idle");
      receipt.setAttribute("role", "status");
      receipt.hidden = true;
      receipt.appendChild(i18nEl(doc, "h5", "acp-wizard-level-title", "next.config.receipt"));

      var body = el(doc, "p", "acp-wizard-receipt-body");
      body.setAttribute("data-role", "receipt-body");
      receipt.appendChild(body);
      state.ui.receiptBody = body;

      var digest = el(doc, "p", "acp-wizard-note acp-wizard-digest");
      digest.setAttribute("data-role", "receipt-digest");
      receipt.appendChild(digest);
      state.ui.receiptDigest = digest;

      var replay = i18nEl(doc, "p", "acp-wizard-note", "next.s2scan.receipt_replay");
      replay.setAttribute("data-role", "receipt-replay");
      replay.hidden = true;
      receipt.appendChild(replay);
      state.ui.receiptReplay = replay;

      var open = el(doc, "button", "acp-wizard-btn", t("next.config.open_task"));
      open.type = "button";
      open.setAttribute("data-action", "open-task");
      open.setAttribute("data-i18n", "next.config.open_task");
      open.hidden = true;
      on(open, "click", function () { openReceiptTask(); });
      receipt.appendChild(open);
      state.ui.openTaskBtn = open;

      state.ui.receipt = receipt;
      return receipt;
    }

    /* A-08: modal submit-check dialog. Content is derived on every render; the
     * element itself only owns the open/closed flag. */
    function buildSubmitDialog() {
      var dialog = el(doc, "div", "acp-wizard-dialog");
      dialog.setAttribute("data-role", "submit-check");
      dialog.setAttribute("data-acp-check-state", "blocked");
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.hidden = true;
      state.ui.dialog = dialog;

      var panel = el(doc, "div", "acp-wizard-dialog-panel");
      dialog.appendChild(panel);

      var head = el(doc, "div", "acp-wizard-dialog-head");
      head.appendChild(i18nEl(doc, "h4", "acp-wizard-subheading", "next.config.review"));
      panel.appendChild(head);

      var summary = el(doc, "dl", "acp-wizard-review");
      state.ui.check = {};
      [
        { role: "check-workflow", key: "next.wizard.review_workflow" },
        { role: "check-method", key: "next.wizard.review_method" },
        { role: "check-structure", key: "next.wizard.review_structure" },
        { role: "check-state", key: "next.config.charge" },
        { role: "check-resources", key: "next.approval.plan.resources" },
        { role: "check-fingerprint", key: "next.capabilityForm.preflight_fingerprint" }
      ].forEach(function (item) {
        var row = el(doc, "div", "acp-wizard-review-row");
        row.appendChild(i18nEl(doc, "dt", "acp-wizard-label", item.key));
        var dd = el(doc, "dd", "acp-wizard-review-value", unknown());
        dd.setAttribute("data-role", item.role);
        row.appendChild(dd);
        summary.appendChild(row);
        state.ui.check[item.role] = dd;
      });
      panel.appendChild(summary);

      var blockers = el(doc, "div", "acp-wizard-reasons");
      blockers.setAttribute("data-role", "submit-check-blockers");
      blockers.setAttribute("role", "alert");
      panel.appendChild(blockers);
      state.ui.checkBlockers = blockers;

      var warnings = el(doc, "div", "acp-wizard-reasons");
      warnings.setAttribute("data-role", "submit-check-warnings");
      panel.appendChild(warnings);
      state.ui.checkWarnings = warnings;

      var footer = el(doc, "div", "acp-wizard-dialog-actions");
      var hint = el(doc, "p", "acp-wizard-note");
      hint.setAttribute("data-role", "submit-check-hint");
      footer.appendChild(hint);
      state.ui.checkHint = hint;

      var cancel = el(doc, "button", "acp-wizard-btn", t("next.tasks.dialog.cancel"));
      cancel.type = "button";
      cancel.setAttribute("data-action", "submit-check-cancel");
      cancel.setAttribute("data-i18n", "next.tasks.dialog.cancel");
      on(cancel, "click", function () { closeSubmitCheck(); });
      footer.appendChild(cancel);
      state.ui.checkCancel = cancel;

      var confirm = el(doc, "button", "acp-wizard-btn acp-wizard-btn--primary", t("next.config.submit"));
      confirm.type = "button";
      confirm.setAttribute("data-action", "submit-check-confirm");
      confirm.setAttribute("data-i18n", "next.config.submit");
      on(confirm, "click", function () { confirmSubmit(); });
      footer.appendChild(confirm);
      state.ui.checkConfirm = confirm;
      panel.appendChild(footer);
      return dialog;
    }

    function buildPreflightBlock() {
      var block = el(doc, "div", "acp-wizard-subpanel acp-wizard-preflight");
      block.setAttribute("data-role", "preflight");
      block.setAttribute("data-acp-preflight-state", "idle");
      state.ui.preflightBlock = block;
      block.appendChild(i18nEl(doc, "h4", "acp-wizard-subheading", "next.config.validate"));

      var status = el(doc, "p", "acp-wizard-status", t("next.s2scan.preflight_idle"));
      status.setAttribute("data-role", "preflight-status");
      status.setAttribute("aria-live", "polite");
      block.appendChild(status);
      state.ui.preflightStatus = status;

      var run = el(doc, "button", "acp-wizard-btn acp-wizard-btn--primary", t("next.config.validate"));
      run.type = "button";
      run.setAttribute("data-action", "preflight");
      run.setAttribute("data-i18n", "next.config.validate");
      on(run, "click", function () { runPreflight(); });
      block.appendChild(run);
      state.ui.preflightBtn = run;

      var digestRow = el(doc, "dl", "acp-wizard-review");
      var digestLabel = i18nEl(doc, "dt", "acp-wizard-label", "next.capabilityForm.preflight_fingerprint");
      var digest = el(doc, "dd", "acp-wizard-review-value acp-wizard-digest", unknown());
      digest.setAttribute("data-role", "preflight-fingerprint");
      digestRow.appendChild(digestLabel);
      digestRow.appendChild(digest);
      block.appendChild(digestRow);
      state.ui.preflightFingerprint = digest;

      var reasons = el(doc, "div", "acp-wizard-reasons");
      reasons.setAttribute("data-role", "preflight-reasons");
      reasons.setAttribute("role", "alert");
      block.appendChild(reasons);
      state.ui.preflightReasons = reasons;

      var advisories = el(doc, "div", "acp-wizard-reasons");
      advisories.setAttribute("data-role", "preflight-advisories");
      block.appendChild(advisories);
      state.ui.preflightAdvisories = advisories;
      return block;
    }

    function buildActions() {
      var footer = el(doc, "footer", "acp-wizard-actions");
      var back = el(doc, "button", "acp-wizard-btn", t("next.wizard.back"));
      back.type = "button";
      back.setAttribute("data-action", "back");
      back.setAttribute("data-i18n", "next.wizard.back");
      back.disabled = true;
      on(back, "click", function () { backStep(); });
      var hint = el(doc, "p", "acp-wizard-hint");
      hint.setAttribute("data-role", "nav-hint");
      hint.setAttribute("aria-live", "polite");
      var next = el(doc, "button", "acp-wizard-btn acp-wizard-btn--primary", t("next.wizard.next"));
      next.type = "button";
      next.setAttribute("data-action", "next");
      next.setAttribute("data-i18n", "next.wizard.next");
      next.disabled = true;
      on(next, "click", function () { nextStep(); });
      footer.appendChild(back);
      footer.appendChild(hint);
      footer.appendChild(next);
      state.ui.back = back;
      state.ui.navHint = hint;
      state.ui.next = next;
      return footer;
    }

    /* ── render ──────────────────────────────────────────────────── */

    function renderAll() {
      if (!state.root || state.disposed || state.suspended) return;
      renderRoot();
      renderCatalog();
      renderWorkflow();
      renderMethod();
      renderStepNav();
      renderStepPanels();
      renderWorkflowInput();
      renderInput();
      renderElectronic();
      renderResources();
      renderQueue();
      renderReview();
      renderPreflight();
      renderSubmit();
      renderDraft();
      renderActions();
    }

    function renderRoot() {
      var check = state.root ? currentSubmitCheck() : null;
      var adapter = currentAdapter();
      state.root.setAttribute("data-acp-module-state", state.catalog.status);
      state.root.setAttribute("data-acp-step", STEP_IDS[state.step]);
      state.root.setAttribute("data-acp-step-index", String(state.step));
      state.root.setAttribute("data-acp-max-step", String(state.maxReached));
      state.root.setAttribute("data-acp-step-valid", stepValid(state.step) ? "true" : "false");
      state.root.setAttribute("data-acp-can-next", canNext() ? "true" : "false");
      state.root.setAttribute("data-acp-input-mode", state.input.mode);
      state.root.setAttribute("data-acp-structure-state", state.input.status);
      state.root.setAttribute("data-acp-electronic-state", electronicStateName());
      state.root.setAttribute("data-acp-resource-state", state.resources.status);
      state.root.setAttribute("data-acp-queue-state", state.queue.status);
      state.root.setAttribute("data-acp-contract-state", state.contract.status);
      state.root.setAttribute("data-acp-input-adapter", adapter.kind);
      state.root.setAttribute("data-acp-method-projection", state.method.extended ? "extended" : "minimal");
      state.root.setAttribute("data-acp-method-support", methodSupport(state.method) || "ok");
      state.root.setAttribute("data-acp-preflight-state", preflightStateName());
      state.root.setAttribute("data-acp-preflight-valid", preflightValid() ? "true" : "false");
      state.root.setAttribute("data-acp-preflight-stale", preflightStale() ? "true" : "false");
      state.root.setAttribute("data-acp-preflight-status", state.preflight.status);
      state.root.setAttribute("data-acp-preflight-status-code", state.preflight.errorStatus == null ? "" : String(state.preflight.errorStatus));
      state.root.setAttribute("data-acp-preflight-error-code", state.preflight.errorCode || "");
      state.root.setAttribute("data-acp-can-preflight", canPreflight() ? "true" : "false");
      state.root.setAttribute("data-acp-plan-digest", state.preflight.digest || "");
      state.root.setAttribute("data-acp-preflight-fingerprint", state.preflight.digest || "");
      state.root.setAttribute("data-acp-submit-state", state.submit.status);
      state.root.setAttribute("data-acp-submit-check", check && check.ok ? "ok" : "blocked");
      state.root.setAttribute("data-acp-can-submit", canSubmit() ? "true" : "false");
      state.root.setAttribute("data-acp-submit-error-status", state.submit.errorStatus == null ? "" : String(state.submit.errorStatus));
      state.root.setAttribute("data-acp-submit-error-code", state.submit.errorCode || "");
      state.root.setAttribute("data-acp-submit-error-field", state.submit.errorField || "");
      state.root.setAttribute("data-acp-submit-error-rule", state.submit.errorRule || "");
      state.root.setAttribute("data-acp-receipt-job", state.submit.receipt ? state.submit.receipt.jobId : "");
      state.root.setAttribute("data-acp-busy", busyName());
    }

    function busyName() {
      if (state.input.pending) return "structure";
      if (state.submit.status === "loading") return "submit";
      if (state.resources.status === "loading") return "resources";
      if (state.queue.status === "loading") return "queue";
      if (state.preflight.status === "loading") return "preflight";
      if (state.draft.busy) return "draft";
      return "none";
    }

    function canPreflight() {
      return state.mounted && !state.disposed && state.step === STEP_IDS.length - 1 &&
        stepValid(1) && state.preflight.status !== "loading";
    }

    function renderCatalog() {
      var status = state.catalog.status;
      var node = state.ui.catalogStatus;
      if (!node) return;
      node.setAttribute("data-acp-catalog-state", status);
      if (status === "loading") {
        node.textContent = t("next.wizard.catalog_loading");
      } else if (status === "error") {
        node.textContent = t("next.wizard.catalog_error", { message: describeError(state.catalog.error) });
      } else if (status === "empty") {
        node.textContent = t("next.wizard.catalog_empty");
      } else {
        node.textContent = t("next.wizard.catalog_ready", { count: state.workflows.length });
      }
      state.ui.retry.hidden = status !== "error" && status !== "empty";
    }

    function renderWorkflow() {
      var select = state.ui.workflowSelect;
      clear(select);
      state.workflows.forEach(function (entry) {
        var option = el(doc, "option", "", optionLabel(entry));
        option.value = entry.id;
        select.appendChild(option);
      });
      select.disabled = state.catalog.status !== "ready" || !state.workflows.length;
      select.value = state.entry ? state.entry.id : "";
      state.ui.workflowDesc.textContent = state.entry
        ? optionDescription(state.entry)
        : t("next.wizard.workflow_desc_empty");
      var missing = !!state.entry && !state.method.schema;
      var emptySchema = !!state.entry && state.method.error === "schema_empty";
      var methodCheck = state.method.schema ? validateMethodSchema(state.method) : { ok: true, errors: [] };
      var mapped = groupFieldMessage("workflow");
      var text = missing
        ? t("next.wizard.err.schema_missing", { workflow: optionLabel(state.entry) })
        : (emptySchema ? lt("method_schema_empty")
          : (methodCheck.ok ? mapped : methodCheck.errors[0].text));
      state.ui.workflowError.hidden = !text;
      state.ui.workflowError.textContent = text || "";
      state.ui.workflowError.setAttribute("data-acp-method-support", methodSupport(state.method) || "ok");
    }

    /** Preflight rejection text bucketed onto one form group. */
    function groupFieldMessage(group) {
      var errors = state.preflight.fieldErrors || {};
      var parts = [];
      Object.keys(errors).forEach(function (field) {
        var message = textOf(errors[field]);
        if (!message) return;
        if (!fieldMatches(group, field)) return;
        parts.push(field ? field + "：" + message : message);
      });
      return parts.join("；");
    }

    function fieldMatches(group, field) {
      var name = textOf(field);
      if (group === "workflow") return name === "workflow";
      if (group === "charge") return name === "charge" || name === "input.charge";
      if (group === "multiplicity") {
        return name === "multiplicity" || name === "input.multiplicity";
      }
      if (group === "structure") {
        return name.indexOf("input.") === 0 && !fieldMatches("charge", name) &&
          !fieldMatches("multiplicity", name);
      }
      if (group === "method") return name.indexOf("method") === 0;
      if (group === "resources") return name === "resources" || name.indexOf("resources.") === 0;
      return false;
    }

    function renderFieldError(node, group) {
      if (!node) return;
      var message = groupFieldMessage(group);
      node.hidden = !message;
      node.textContent = message || "";
    }

    function methodControl(doc, level, row, adapter) {
      var field = el(doc, "div", "acp-wizard-field" + (row.advanced ? " acp-wizard-field--advanced" : ""));
      if (row.advanced) field.setAttribute("data-acp-advanced", "true");
      var label = el(doc, row.type === "multi" ? "div" : "label", "acp-wizard-label",
        fieldLabel(row.def || fieldDefinition(state.methodCatalog, row.field), row.field));
      var input;
      if (row.type === "select") {
        input = el(doc, "select", "acp-wizard-select");
        if (row.value === "" || row.value == null) {
          var empty = el(doc, "option", "", t("next.wizard.review_missing"));
          empty.value = "";
          input.appendChild(empty);
        }
        row.options.forEach(function (value) {
          var option = el(doc, "option", "", optionLabelFor(row.def, value));
          option.value = value;
          input.appendChild(option);
        });
        input.value = row.value == null ? "" : textOf(row.value);
      } else if (row.type === "bool") {
        input = el(doc, "input", "acp-wizard-check");
        input.type = "checkbox";
        input.checked = row.value === true;
      } else if (row.type === "multi") {
        input = el(doc, "div", "acp-wizard-multi");
        row._inputs = [];
        var selected = Array.isArray(row.value) ? row.value : [];
        row.options.forEach(function (value) {
          var optionLabel = el(doc, "label", "acp-wizard-check-label");
          var checkbox = el(doc, "input", "acp-wizard-check");
          checkbox.type = "checkbox";
          checkbox.checked = selected.indexOf(value) >= 0;
          checkbox.setAttribute("data-role", "method-" + row.field);
          checkbox.setAttribute("data-field", row.field);
          checkbox.setAttribute("data-level", level.levelId);
          checkbox.setAttribute("data-kind", row.type);
          checkbox.setAttribute("data-option", value);
          optionLabel.appendChild(checkbox);
          optionLabel.appendChild(el(doc, "span", "acp-wizard-check-text", optionLabelFor(row.def, value)));
          input.appendChild(optionLabel);
          row._inputs.push(checkbox);
        });
      } else {
        input = el(doc, "input", "acp-wizard-input");
        input.type = "text";
        if (row.type === "int" || row.type === "float") input.setAttribute("inputmode", "decimal");
        input.value = row.value == null ? "" : textOf(row.value);
      }
      if (input.setAttribute && row.type !== "multi") {
        input.setAttribute("data-role", "method-" + row.field);
        input.setAttribute("data-field", row.field);
        input.setAttribute("data-level", level.levelId);
        input.setAttribute("data-kind", row.type);
      } else if (input.setAttribute) {
        input.setAttribute("data-role", "method-" + row.field + "-group");
        input.setAttribute("data-level", level.levelId);
      }
      var unconsumed = adapter && adapter.methodEmit === METHOD_EMIT_NONE &&
        !adapter.inputProperties[row.field] &&
        !(row.field === "max_steps" && adapter.inputProperties.max_iterations);
      if (unconsumed) {
        input.disabled = true;
        field.setAttribute("data-acp-field-consumed", "false");
        field.appendChild(el(doc, "p", "acp-wizard-note", lt("field_not_consumed")));
      } else {
        field.setAttribute("data-acp-field-consumed", "true");
      }
      label.appendChild(input);
      field.appendChild(label);
      var help = fieldHelp(row.def || fieldDefinition(state.methodCatalog, row.field));
      if (help) field.appendChild(el(doc, "p", "acp-wizard-note", help));
      if (row.unit) field.appendChild(el(doc, "p", "acp-wizard-note", row.unit));
      return field;
    }

    function renderMethod() {
      var method = state.method;
      var container = state.ui.methodLevels;
      var adapter = currentAdapter();
      clear(container);
      container.setAttribute("data-acp-method-projection", method.extended ? "extended" : "minimal");
      container.setAttribute("data-acp-method-support", methodSupport(method) || "ok");
      clear(state.ui.profileSelect);
      var showProfile = !!method.schema && method.profiles.length > 1;
      state.ui.profileWrap.hidden = !showProfile;
      if (showProfile) {
        method.profiles.forEach(function (profile) {
          var option = el(doc, "option", "", profile.label || profile.id);
          option.value = profile.id;
          state.ui.profileSelect.appendChild(option);
        });
        state.ui.profileSelect.value = method.profileId;
      }
      if (method.error === "schema_empty") {
        container.appendChild(el(doc, "p", "acp-wizard-error", lt("method_schema_empty")));
        return;
      }
      method.levels.forEach(function (level) {
        var wrap = el(doc, "div", "acp-wizard-level");
        wrap.setAttribute("data-level", level.levelId);
        var head = el(doc, "div", "acp-wizard-level-head");
        head.appendChild(el(doc, "h5", "acp-wizard-level-title", levelLabel(level)));
        if (level.engine) {
          head.appendChild(el(doc, "span", "acp-wizard-chip", t("next.wizard.level_engine", { engine: level.engine })));
        }
        wrap.appendChild(head);
        if (!level.rows.length) {
          wrap.appendChild(el(doc, "p", "acp-wizard-note", t("next.wizard.method_empty")));
        } else if (method.extended) {
          var grid = el(doc, "div", "acp-wizard-grid");
          level.rows.forEach(function (row) {
            grid.appendChild(methodControl(doc, level, row, adapter));
          });
          wrap.appendChild(grid);
        } else {
          var stableGrid = el(doc, "div", "acp-wizard-grid");
          level.rows.forEach(function (row) {
            var field = el(doc, "div", "acp-wizard-field");
            var label = el(doc, "label", "acp-wizard-label", fieldLabel(fieldDefinition(state.methodCatalog, row.field), row.field));
            var select = el(doc, "select", "acp-wizard-select");
            select.setAttribute("data-role", "method-" + row.field);
            select.setAttribute("data-field", row.field);
            select.setAttribute("data-level", level.levelId);
            row.options.forEach(function (value) {
              var option = el(doc, "option", "", value);
              option.value = value;
              select.appendChild(option);
            });
            select.value = row.value;
            label.appendChild(select);
            field.appendChild(label);
            stableGrid.appendChild(field);
          });
          wrap.appendChild(stableGrid);
        }
        container.appendChild(wrap);
      });
    }

    function renderStepNav() {
      STEP_IDS.forEach(function (id, index) {
        var button = state.ui.stepButtons[index];
        var stateName;
        if (index === state.step) stateName = stepValid(index) ? "current" : "invalid";
        else if (index > state.maxReached) stateName = "locked";
        else if (index < state.step) stateName = "complete";
        else stateName = "available";
        button.setAttribute("data-step-state", stateName);
        button.disabled = index > state.maxReached;
        button.setAttribute("aria-disabled", index > state.maxReached ? "true" : "false");
        button.setAttribute("aria-current", index === state.step ? "step" : "false");
      });
    }

    function renderStepPanels() {
      STEP_IDS.forEach(function (id, index) {
        var panel = state.ui.panels[id];
        if (panel) panel.hidden = index !== state.step;
      });
    }

    function renderWorkflowInput() {
      var block = state.ui.workflowInputBlock;
      if (!block) return;
      var adapter = currentAdapter();
      var extended = !!adapter.extended;
      block.hidden = !extended;
      block.setAttribute("data-acp-contract-state", state.contract.status);
      block.setAttribute("data-acp-input-adapter", adapter.kind);
      var status = state.contract.status;
      var statusNode = state.ui.contractStatus;
      if (!extended) {
        statusNode.hidden = true;
        statusNode.textContent = "";
        state.ui.contractRetry.hidden = true;
      } else if (status === "loading" || status === "idle") {
        statusNode.hidden = false;
        statusNode.textContent = lt("contract_loading");
        state.ui.contractRetry.hidden = true;
      } else if (status === "error") {
        statusNode.hidden = false;
        statusNode.textContent = contractErrorText();
        state.ui.contractRetry.hidden = false;
      } else {
        statusNode.hidden = false;
        statusNode.textContent = adapter.coverage === "not_provided"
          ? lt("contract_not_provided")
          : lt("contract_coverage", { coverage: adapter.coverage });
        state.ui.contractRetry.hidden = true;
      }

      var payloadVisible = adapter.kind === ADAPTER_PAYLOAD;
      state.ui.payloadWrap.hidden = !payloadVisible;
      if (payloadVisible) {
        state.ui.payloadText.textContent = lt("payload_label", { key: adapter.payloadKey });
        if (state.ui.payloadInput.value !== state.extras.payload) {
          state.ui.payloadInput.value = state.extras.payload;
        }
      }

      var sourceVisible = adapter.kind === ADAPTER_SOURCE;
      state.ui.sourceWrap.hidden = !sourceVisible;
      if (sourceVisible) {
        if (state.ui.sourceJobInput.value !== state.extras.sourceJobId) {
          state.ui.sourceJobInput.value = state.extras.sourceJobId;
        }
        if (state.ui.sourceProductInput.value !== state.extras.sourceProductId) {
          state.ui.sourceProductInput.value = state.extras.sourceProductId;
        }
        if (state.ui.sourceModeInput.value !== state.extras.sourceModeIndex) {
          state.ui.sourceModeInput.value = state.extras.sourceModeIndex;
        }
        state.ui.sourceJobInput.parentNode.hidden = adapter.sourceFields.indexOf("source_job_id") < 0;
        state.ui.sourceProductInput.parentNode.hidden =
          adapter.sourceFields.indexOf("source_product_id") < 0 &&
          adapter.sourceFields.indexOf("source_id") < 0;
        state.ui.sourceModeInput.parentNode.hidden =
          adapter.sourceFields.indexOf("source_mode_index") < 0;
      }

      var scanVisible = adapter.kind === ADAPTER_SCAN;
      state.ui.scanWrap.hidden = !scanVisible;
      if (scanVisible && state.ui.scanAtomsInput.value !== state.extras.scanAtoms) {
        state.ui.scanAtomsInput.value = state.extras.scanAtoms;
      }
      state.ui.batchNote.hidden = adapter.kind !== ADAPTER_BATCH;

      var errors = inputCheck().errors || [];
      function messageFor(field) {
        for (var i = 0; i < errors.length; i += 1) {
          if (textOf(errors[i].field) === field) return textOf(errors[i].text);
        }
        return "";
      }
      var payloadError = messageFor("payload");
      state.ui.payloadError.hidden = !payloadVisible || !payloadError;
      state.ui.payloadError.textContent = payloadError;
      var jobError = messageFor("source_job_id");
      state.ui.sourceJobError.hidden = !sourceVisible || !jobError;
      state.ui.sourceJobError.textContent = jobError;
      var modeError = messageFor("source_mode_index");
      state.ui.sourceModeError.hidden = !sourceVisible || !modeError;
      state.ui.sourceModeError.textContent = modeError;
      var scanError = messageFor("scan_atoms") || messageFor("scan_coordinate_start") ||
        messageFor("scan_coordinate_end");
      state.ui.scanAtomsError.hidden = !scanVisible || !scanError;
      state.ui.scanAtomsError.textContent = scanError;
    }

    function renderInput() {
      var section = state.ui.inputPanel;
      if (!section) return;
      var adapter = currentAdapter();
      var structureVisible = adapter.kind === ADAPTER_STRUCTURE ||
        adapter.kind === ADAPTER_SCAN || adapter.kind === ADAPTER_BATCH;
      section.setAttribute("data-acp-input-adapter", adapter.kind);
      section.setAttribute("data-acp-contract-state", state.contract.status);
      section.setAttribute("data-acp-structure-state", structureVisible ? state.input.status : "empty");
      section.setAttribute("data-acp-input-mode", state.input.mode);
      INPUT_MODES.forEach(function (mode) {
        var button = state.ui.modeButtons[mode];
        button.className = "acp-wizard-tab" + (mode === state.input.mode ? " is-active" : "");
        button.setAttribute("aria-selected", mode === state.input.mode ? "true" : "false");
        button.hidden = !structureVisible;
      });
      state.ui.smilesPanel.hidden = !structureVisible || state.input.mode !== "smiles";
      state.ui.pastePanel.hidden = !structureVisible || state.input.mode !== "paste";
      state.ui.filePanel.hidden = !structureVisible || state.input.mode !== "file";
      state.ui.electronicBlock.hidden = !structureVisible;
      state.ui.structureStatus.hidden = !structureVisible;
      state.ui.structureError.hidden = !structureVisible || state.input.status !== "error";
      state.ui.structureSummary.hidden = !structureVisible || state.input.status !== "ready";
      if (!structureVisible) {
        state.ui.structureStatus.textContent = "";
        state.ui.structureError.textContent = "";
        state.ui.structureSummary.textContent = "";
      }
      if (state.ui.smilesInput && state.ui.smilesInput.value !== state.input.smiles) {
        state.ui.smilesInput.value = state.input.smiles;
      }
      if (state.ui.textInput && state.ui.textInput.value !== state.input.text) {
        state.ui.textInput.value = state.input.text;
      }
      state.ui.fileName.textContent = state.input.fileName
        ? t("next.wizard.file_name", { name: state.input.fileName })
        : t("next.wizard.file_none");
      var status = state.input.status;
      if (status === "loading") {
        state.ui.structureStatus.textContent = t("next.wizard.structure_loading");
      } else if (status === "ready" && state.input.structure) {
        state.ui.structureStatus.textContent = t("next.wizard.structure_ready", {
          formula: state.input.structure.formula || unknown(),
          atoms: state.input.structure.atomCount == null ? unknown() : String(state.input.structure.atomCount)
        });
      } else if (status === "error") {
        state.ui.structureStatus.textContent = t("next.wizard.structure_failed");
      } else {
        state.ui.structureStatus.textContent = t("next.wizard.structure_empty");
      }
      state.ui.structureError.hidden = !structureVisible || status !== "error";
      state.ui.structureError.textContent = status === "error" ? textOf(state.input.error) : "";
      if (structureVisible && status === "ready" && state.input.structure) {
        state.ui.structureSummary.hidden = false;
        state.ui.structureSummary.textContent = structureSummaryText(state.input.structure);
        state.ui.structureSummary.setAttribute("data-acp-structure-source", textOf(state.input.structure.source));
      } else {
        state.ui.structureSummary.hidden = true;
        state.ui.structureSummary.textContent = "";
        state.ui.structureSummary.setAttribute("data-acp-structure-source", "");
      }
      state.ui.resolveBtn.disabled = state.input.pending === "resolve";
      state.ui.parseBtn.disabled = state.input.pending === "parse";
      state.ui.fileInput.disabled = state.input.pending === "file";
    }

    function renderElectronic() {
      var block = state.ui.electronicBlock;
      if (!block) return;
      var check = electronicCheck();
      var stateName = electronicStateName();
      block.setAttribute("data-acp-electronic-state", stateName);
      if (state.ui.chargeInput && state.ui.chargeInput.value !== state.input.charge) {
        state.ui.chargeInput.value = state.input.charge;
      }
      if (state.ui.multiplicityInput && state.ui.multiplicityInput.value !== state.input.multiplicity) {
        state.ui.multiplicityInput.value = state.input.multiplicity;
      }
      var chargeError = "";
      var multError = "";
      check.errors.forEach(function (err) {
        if (err.field === "charge") chargeError = errorText(err);
        else if (err.field === "multiplicity") multError = errorText(err);
      });
      var chargeMapped = groupFieldMessage("charge");
      var multMapped = groupFieldMessage("multiplicity");
      state.ui.chargeError.hidden = !(chargeError || chargeMapped);
      state.ui.chargeError.textContent = chargeError || chargeMapped || "";
      state.ui.multiplicityError.hidden = !(multError || multMapped);
      state.ui.multiplicityError.textContent = multError || multMapped || "";
      renderFieldError(state.ui.structureFieldError, "structure");
      var hint = state.ui.parityHint;
      if (state.input.status !== "ready") {
        hint.hidden = true;
        hint.textContent = "";
      } else if (check.parityChecked) {
        hint.hidden = false;
        hint.textContent = t("next.s2scan.parity_ok", { electrons: check.electrons });
        hint.setAttribute("data-acp-parity", "checked");
      } else if (check.ok) {
        hint.hidden = false;
        hint.textContent = t("next.s2scan.parity_unverifiable");
        hint.setAttribute("data-acp-parity", "unverifiable");
      } else {
        hint.hidden = true;
        hint.textContent = "";
        hint.setAttribute("data-acp-parity", "invalid");
      }
    }

    function renderResources() {
      var block = state.ui.resourcesBlock;
      if (!block) return;
      var status = state.resources.status;
      block.setAttribute("data-acp-resource-state", status);
      if (state.ui.coresInput && state.ui.coresInput.value !== state.resources.cores) {
        state.ui.coresInput.value = state.resources.cores;
      }
      if (state.ui.memoryInput && state.ui.memoryInput.value !== state.resources.memory) {
        state.ui.memoryInput.value = state.resources.memory;
      }
      var statusNode = state.ui.resourceStatus;
      if (status === "loading") {
        statusNode.hidden = false;
        statusNode.textContent = lt("resources_loading");
      } else if (status === "error") {
        statusNode.hidden = false;
        statusNode.textContent = t("next.nodes.section_error", {
          section: t("next.approval.plan.resources"),
          message: describeError(state.resources.error)
        });
      } else if (status === "empty") {
        statusNode.hidden = false;
        statusNode.textContent = lt("resources_empty");
      } else {
        statusNode.hidden = true;
        statusNode.textContent = "";
      }
      state.ui.resourceRetry.hidden = status !== "error";
      state.ui.coresInput.disabled = status === "loading";
      state.ui.memoryInput.disabled = status === "loading";
      var check = resourceCheck();
      var coresError = "";
      var memoryError = "";
      check.errors.forEach(function (err) {
        if (err.field === "cores") coresError = errorText(err);
        if (err.field === "memory") memoryError = errorText(err);
      });
      var coresFields = ["resources.nproc", "resources.ncores", "resources.cores"];
      var memoryFields = ["resources.mem", "resources.memory_gb"];
      var coresMapped = mappedFieldMessage(coresFields);
      var memoryMapped = mappedFieldMessage(memoryFields);
      state.ui.coresError.hidden = !(coresError || coresMapped);
      state.ui.coresError.textContent = coresError || coresMapped || "";
      state.ui.memoryError.hidden = !(memoryError || memoryMapped);
      state.ui.memoryError.textContent = memoryError || memoryMapped || "";
      var residual = groupFieldMessageExcept("resources", coresFields.concat(memoryFields));
      state.ui.resourcesFieldError.hidden = !residual;
      state.ui.resourcesFieldError.textContent = residual || "";
    }

    function mappedFieldMessage(fields) {
      var errors = state.preflight.fieldErrors || {};
      var parts = [];
      (fields || []).forEach(function (field) {
        var message = textOf(errors[field]);
        if (message) parts.push(field + "：" + message);
      });
      return parts.join("；");
    }

    function groupFieldMessageExcept(group, excluded) {
      var errors = state.preflight.fieldErrors || {};
      var skip = excluded || [];
      var parts = [];
      Object.keys(errors).forEach(function (field) {
        if (!fieldMatches(group, field)) return;
        if (skip.indexOf(field) >= 0) return;
        var message = textOf(errors[field]);
        if (!message) return;
        parts.push(field ? field + "：" + message : message);
      });
      return parts.join("；");
    }

    function renderQueue() {
      var block = state.ui.queueBlock;
      if (!block) return;
      var status = state.queue.status;
      block.setAttribute("data-acp-queue-state", status);
      var list = state.ui.queueList;
      clear(list);
      if (status === "ready" && state.queue.rows.length) {
        state.queue.rows.forEach(function (row) {
          var chip = el(doc, "span", "acp-wizard-chip");
          chip.setAttribute("data-queue-node", row.name);
          chip.setAttribute("data-queue-name", row.queue);
          var label = row.name || unknown();
          chip.textContent = row.queue
            ? label + " · " + row.queue
            : label + " · " + unknown();
          list.appendChild(chip);
        });
      } else if (status === "loading") {
        list.appendChild(el(doc, "span", "acp-wizard-note", lt("resources_loading")));
      } else if (status === "error") {
        list.appendChild(el(doc, "span", "acp-wizard-note", t("next.nodes.section_error", {
          section: t("next.nodes.meta_queue"),
          message: describeError(state.queue.error)
        })));
      } else {
        list.appendChild(el(doc, "span", "acp-wizard-note",
          state.queue.rows.length ? lt("queue_empty") : t("next.nodes.not_configured")));
      }
    }

    function rowValueText(row) {
      if (Array.isArray(row.value)) return row.value.join(", ");
      if (row.value === true) return "true";
      if (row.value === false) return "false";
      if (row.value && typeof row.value === "object") return JSON.stringify(row.value);
      return textOf(row.value);
    }

    function methodSummaryText() {
      var method = state.method;
      if (!method.schema) return t("next.wizard.review_missing");
      var parts = [];
      if (method.profileId) {
        var label = method.profileId;
        method.profiles.forEach(function (profile) {
          if (profile.id === method.profileId && profile.label) label = profile.label;
        });
        parts.push(label);
      }
      var deferred = 0;
      method.levels.forEach(function (level) {
        level.rows.forEach(function (row) {
          if (method.extended && row.advanced) {
            deferred += 1;
            return;
          }
          parts.push(fieldLabel(row.def || fieldDefinition(state.methodCatalog, row.field), row.field) +
            " " + rowValueText(row));
        });
      });
      if (deferred) parts.push("+" + deferred);
      return parts.length ? parts.join(" · ") : t("next.wizard.review_missing");
    }

    function inputSummaryText() {
      var adapter = currentAdapter();
      if (adapter.kind === ADAPTER_SOURCE) {
        return lt("source_summary", {
          jobId: textOf(state.extras.sourceJobId).trim() || unknown()
        });
      }
      if (adapter.kind === ADAPTER_PAYLOAD) {
        var parsed = jsonObject(state.extras.payload);
        var count = parsed.ok ? Object.keys(parsed.value).length : 0;
        return lt("payload_summary", {
          key: adapter.payloadKey || "payload",
          count: count
        });
      }
      if (state.input.status === "ready" && state.input.structure) {
        return structureSummaryText(state.input.structure);
      }
      return t("next.wizard.review_missing");
    }

    function stateSummaryText() {
      var check = electronicCheck();
      var parts = [
        t("next.config.charge") + " " + (textOf(state.input.charge).trim() || unknown()),
        t("next.config.multiplicity") + " " + (textOf(state.input.multiplicity).trim() || unknown())
      ];
      if (check.electrons != null) parts.push("ΣZ − charge = " + String(check.electrons));
      return parts.join(" · ");
    }

    function resourceSummaryText() {
      var cores = textOf(state.resources.cores).trim();
      var memory = textOf(state.resources.memory).trim();
      var parts = [
        t("next.config.nproc") + " " + (cores || unknown()),
        t("next.config.mem") + " " + (memory || unknown())
      ];
      if (state.queue.rows.length) {
        var queues = [];
        state.queue.rows.forEach(function (row) {
          if (row.queue && queues.indexOf(row.queue) < 0) queues.push(row.queue);
        });
        if (queues.length) parts.push(t("next.nodes.meta_queue") + " " + queues.join(" / "));
      }
      return parts.join(" · ");
    }

    function renderReview() {
      state.ui.review["review-workflow"].textContent = state.entry
        ? optionLabel(state.entry)
        : t("next.wizard.review_missing");
      state.ui.review["review-method"].textContent = methodSummaryText();
      state.ui.review["review-structure"].textContent = inputSummaryText();
      state.ui.reviewState.textContent = stateSummaryText();
      state.ui.reviewResources.textContent = resourceSummaryText();
    }

    function renderPreflight() {
      var block = state.ui.preflightBlock;
      if (!block) return;
      var status = state.preflight.status;
      block.setAttribute("data-acp-preflight-state", preflightStateName());
      block.setAttribute("data-acp-preflight-status", status);
      var button = state.ui.preflightBtn;
      button.disabled = !canPreflight();
      button.textContent = status === "loading"
        ? t("next.config.preflighting")
        : (state.preflight.digest || status === "stale" || status === "error"
          ? t("next.config.preflight_retry")
          : t("next.config.validate"));
      var statusNode = state.ui.preflightStatus;
      if (status === "loading") {
        statusNode.textContent = t("next.config.preflighting");
      } else if (status === "ok" && preflightValid()) {
        statusNode.textContent = t("next.s2scan.preflight_ok", {
          digest: shortDigest(state.preflight.digest)
        });
      } else if (status === "ok") {
        statusNode.textContent = t("next.capabilityForm.preflight.stale");
      } else if (status === "stale") {
        statusNode.textContent = t("next.s2scan.preflight_edited");
      } else if (status === "infeasible") {
        statusNode.textContent = t("next.config.preflight_failed");
      } else if (status === "blocked") {
        statusNode.textContent = t("next.s2scan.preflight_blocked");
      } else if (status === "error") {
        statusNode.textContent = t("next.s2scan.preflight_error", {
          message: state.preflight.error || state.preflight.errorCode || ""
        });
      } else {
        statusNode.textContent = t("next.s2scan.preflight_idle");
      }
      var fingerprint = state.ui.preflightFingerprint;
      fingerprint.textContent = state.preflight.digest
        ? state.preflight.digest
        : unknown();
      fingerprint.setAttribute("data-plan-digest", state.preflight.digest || "");
      fingerprint.setAttribute("data-preflight-fingerprint", state.preflight.digest || "");
      var reasons = state.ui.preflightReasons;
      clear(reasons);
      (state.preflight.reasons || []).forEach(function (reason) {
        var row = el(doc, "p", "acp-wizard-error", reasonLabel(reason));
        row.setAttribute("data-role", "preflight-reason");
        row.setAttribute("data-field", textOf(reason.field));
        row.setAttribute("data-code", textOf(reason.code));
        reasons.appendChild(row);
      });
      var advisories = state.ui.preflightAdvisories;
      clear(advisories);
      if (state.preflight.advisories && state.preflight.advisories.length) {
        advisories.appendChild(el(doc, "p", "acp-wizard-note", t("next.s2scan.advisories")));
        state.preflight.advisories.forEach(function (text) {
          advisories.appendChild(el(doc, "p", "acp-wizard-note", textOf(text)));
        });
      }
      renderFieldError(state.ui.methodFieldError, "method");
    }

    function blockerLabel(blocker) {
      if (!blocker) return "";
      var message = textOf(blocker.text) ||
        (blocker.key ? t(blocker.key, blocker.vars || undefined) : "");
      if (!message) message = textOf(blocker.code);
      var field = textOf(blocker.field);
      if (field && message) return field + "：" + message;
      return message;
    }

    /* A-06/A-08: submit row, check dialog content and the receipt. Content is
     * always derived from state, so a late edit or a stale preflight immediately
     * turns the confirm button off instead of sending a stale plan. */
    function renderSubmit() {
      if (!state.ui.submitBtn) return;
      var check = currentSubmitCheck();
      var status = state.submit.status;
      var loading = status === "loading";
      var submitted = submittedForCurrentPlan();
      var button = state.ui.submitBtn;
      /* No injected client means no submit surface: the module never falls back
       * to a global transport for a task mutation. */
      button.hidden = !state.client;
      button.disabled = loading || submitted || !state.client;
      button.textContent = loading
        ? t("next.config.submitting")
        : (submitted ? t("next.config.receipt") : t("next.config.submit"));

      var statusNode = state.ui.submitStatus;
      var checkState = check.ok ? "ok" : "blocked";
      var line = "";
      if (loading) line = t("next.config.submitting");
      else if (submitted) line = t("next.config.receipt");
      else if (!check.ok) line = blockerLabel(check.blockers[0]);
      statusNode.textContent = line;
      statusNode.setAttribute("data-acp-submit-check", checkState);

      var dialog = state.ui.dialog;
      var open = !!state.dialog.open;
      dialog.hidden = !open;
      dialog.setAttribute("data-acp-dialog", open ? "open" : "closed");
      dialog.setAttribute("data-acp-check-state", checkState);
      state.ui.check["check-workflow"].textContent = state.entry
        ? optionLabel(state.entry)
        : t("next.wizard.review_missing");
      state.ui.check["check-method"].textContent = methodSummaryText();
      state.ui.check["check-structure"].textContent = inputSummaryText();
      state.ui.check["check-state"].textContent = state.input.status === "ready"
        ? stateSummaryText()
        : t("next.wizard.review_missing");
      state.ui.check["check-resources"].textContent = resourceSummaryText();
      state.ui.check["check-fingerprint"].textContent = state.preflight.digest || unknown();

      var blockers = state.ui.checkBlockers;
      clear(blockers);
      check.blockers.forEach(function (blocker) {
        var row = el(doc, "p", "acp-wizard-error", blockerLabel(blocker));
        row.setAttribute("data-role", "submit-check-blocker");
        row.setAttribute("data-code", textOf(blocker.code));
        row.setAttribute("data-field", textOf(blocker.field));
        blockers.appendChild(row);
      });

      var warnings = state.ui.checkWarnings;
      clear(warnings);
      if (check.warnings.length) {
        warnings.appendChild(el(doc, "p", "acp-wizard-note", t("next.s2scan.advisories")));
        check.warnings.forEach(function (warning) {
          var row = el(doc, "p", "acp-wizard-note", textOf(warning.message));
          row.setAttribute("data-role", "submit-check-warning");
          warnings.appendChild(row);
        });
      }

      state.ui.checkConfirm.disabled = !check.ok || loading || !state.client;
      state.ui.checkHint.textContent = check.ok
        ? t("next.config.preflight_ok", { digest: shortDigest(state.preflight.digest) })
        : blockerLabel(check.blockers[0]);

      var receipt = state.ui.receipt;
      var mapped = state.submit.error;
      if (state.submit.receipt) {
        receipt.hidden = false;
        receipt.setAttribute("data-acp-receipt-state", "submitted");
        state.ui.receiptBody.textContent = t("next.config.receipt_body", {
          jobId: state.submit.receipt.jobId,
          status: state.submit.receipt.status || unknown(),
          attempt: String(state.submit.receipt.attempt)
        });
        var digestText = state.submit.receipt.planDigest
          ? t("next.config.receipt_digest", { digest: state.submit.receipt.planDigest })
          : "";
        state.ui.receiptDigest.textContent = digestText;
        state.ui.receiptDigest.hidden = !digestText;
        state.ui.receiptReplay.hidden = !state.submit.receipt.replay;
        state.ui.openTaskBtn.hidden = !(state.commands && typeof state.commands.openTask === "function");
      } else if (mapped) {
        receipt.hidden = false;
        receipt.setAttribute("data-acp-receipt-state", "error");
        state.ui.receiptBody.textContent = mapped.text || "";
        var detail = mapped.status == null ? "" : "HTTP " + mapped.status +
          (mapped.code ? " · " + mapped.code : "") +
          (mapped.field ? " · " + mapped.field : "") +
          (mapped.rule ? " · " + mapped.rule : "");
        state.ui.receiptDigest.textContent = detail;
        state.ui.receiptDigest.hidden = !detail;
        state.ui.receiptReplay.hidden = true;
        state.ui.openTaskBtn.hidden = true;
      } else {
        receipt.hidden = true;
        receipt.setAttribute("data-acp-receipt-state", "idle");
        state.ui.receiptBody.textContent = "";
        state.ui.receiptDigest.textContent = "";
        state.ui.receiptDigest.hidden = true;
        state.ui.receiptReplay.hidden = true;
        state.ui.openTaskBtn.hidden = true;
      }
    }

    /* A-09: the draft banner / root attributes. Derived from state on every
     * render; a hidden banner means "no draft exists for this module". */
    function renderDraft() {
      if (!state.root || state.disposed || state.suspended) return;
      var status = state.draft.status;
      var busy = !!state.draft.busy;
      var stateName = draftStateName();
      state.root.setAttribute("data-acp-draft-state", stateName);
      state.root.setAttribute("data-acp-draft-status", status);
      state.root.setAttribute("data-acp-draft-busy", busy ? "true" : "false");
      state.root.setAttribute("data-acp-busy", busyName());
      var banner = state.ui.draft;
      if (!banner) return;
      var visible = status === "found" || status === "restored" || status === "saving" ||
        status === "deleting" || status === "saved" || status === "conflict" || status === "error" ||
        !!state.draft.notice;
      banner.hidden = !visible;
      banner.setAttribute("data-acp-draft-state", stateName);
      banner.setAttribute("data-acp-draft-status", status);
      banner.setAttribute("data-acp-draft-busy", busy ? "true" : "false");
      banner.setAttribute("data-acp-draft-notice", state.draft.noticeKind || "");

      var text = "";
      if (status === "found") {
        text = t("next.config.draft_found", {
          when: draftWhen(state.draft.meta && state.draft.meta.updatedAt) || unknown()
        });
      } else if (status === "saving") {
        text = t("next.config.draft_saving");
      } else if (status === "deleting") {
        text = lt("draft_deleting");
      } else if (status === "saved") {
        text = t("next.config.draft_saved");
      } else if (status === "restored") {
        text = lt("draft_restored");
      } else if (status === "conflict") {
        text = t("next.config.draft_conflict");
      } else if (status === "error" && !state.draft.noticeKind) {
        text = t("next.config.draft_error", {
          message: (state.draft.error && state.draft.error.message) || ""
        });
      }
      state.ui.draftStatus.textContent = text;
      state.ui.draftStatus.hidden = !text;

      var notice = state.draft.notice || "";
      state.ui.draftNotice.hidden = !notice;
      state.ui.draftNotice.textContent = notice;

      var hasMeta = !!state.draft.meta;
      state.ui.draftRestore.hidden = status !== "found";
      state.ui.draftDiscard.hidden = !hasMeta;
      state.ui.draftReload.hidden = status !== "conflict" || !hasMeta;
      state.ui.draftFork.hidden = status !== "conflict";
      [state.ui.draftRestore, state.ui.draftDiscard, state.ui.draftReload, state.ui.draftFork]
        .forEach(function (button) {
          if (button) button.disabled = busy;
        });
    }

    function reasonLabel(reason) {
      if (!reason) return "";
      var message = textOf(reason.message) || textOf(reason.code);
      var field = textOf(reason.field);
      if (field && message) return field + "：" + message;
      return message;
    }

    function shortDigest(digest) {
      var text = textOf(digest);
      if (text.length <= 23) return text || unknown();
      return text.slice(0, 23) + "…";
    }

    function reviewHint() {
      var status = state.preflight.status;
      if (status === "loading") return t("next.config.preflighting");
      if (status === "ok" && preflightValid()) {
        return t("next.s2scan.preflight_ok", { digest: shortDigest(state.preflight.digest) });
      }
      if (preflightStale()) return t("next.capabilityForm.preflight.stale");
      if (status === "blocked") return t("next.s2scan.preflight_blocked");
      if (status === "infeasible") {
        var first = (state.preflight.reasons || [])[0];
        return t("next.config.preflight_failed") + (first ? "：" + reasonLabel(first) : "");
      }
      if (status === "error") {
        return t("next.s2scan.preflight_error", {
          message: state.preflight.error || state.preflight.errorCode || ""
        });
      }
      return t("next.s2scan.preflight_idle");
    }

    function renderActions() {
      state.ui.back.disabled = state.step <= 0;
      state.ui.next.disabled = !canNext();
      var hint = "";
      if (state.step === STEP_IDS.length - 1) hint = reviewHint();
      else if (!stepValid(state.step)) hint = stepReason(state.step);
      else if (state.navHint) hint = state.navHint;
      state.ui.navHint.textContent = hint;
      state.ui.navHint.hidden = hint === "";
    }

    /* ── catalog loading ─────────────────────────────────────────── */

    /**
     * A-07: read the selected workflow's input contract from the existing
     * discovery read. One request per extended workflow; the token guards a
     * workflow switch / dispose, and a failure is an explicit local block (the
     * input step and preflight both wait for it).
     */
    function loadContract() {
      if (!state.mounted || state.disposed) return;
      if (!state.entry || !state.method.extended) return;
      var gen = state.generation;
      var workflowId = state.entry.id;
      state.contract.token += 1;
      var token = state.contract.token;
      state.contract.status = "loading";
      state.contract.body = null;
      state.contract.error = null;
      state.contract.workflowId = workflowId;
      renderAll();
      var pending;
      try {
        pending = getJson(ENDPOINTS.workflowContract + encodeURIComponent(workflowId));
      } catch (error) {
        pending = Promise.reject(error);
      }
      Promise.resolve(pending).then(function (body) {
        if (!contractAlive(gen, token)) return;
        if (!state.entry || state.entry.id !== workflowId) return;
        state.contract.status = "ready";
        state.contract.body = body && typeof body === "object" ? body : {};
        state.contract.error = null;
        renderAll();
      }, function (error) {
        if (!contractAlive(gen, token)) return;
        state.contract.status = "error";
        state.contract.error = error;
        state.contract.body = null;
        renderAll();
      });
    }

    function retryContract() {
      if (!state.mounted || state.disposed) return;
      loadContract();
    }

    function loadCatalogs() {
      if (!state.mounted || state.disposed) return;
      var gen = state.generation;
      state.catalog.token += 1;
      var token = state.catalog.token;
      state.catalog.status = "loading";
      state.catalog.error = null;
      state.catalog.token = token;
      state.workflows = [];
      state.entry = null;
      state.method = emptyMethod();
      resetContract();
      resetExtras();
      renderAll();
      var pending;
      try {
        pending = Promise.all([
          getJson(ENDPOINTS.workflowCatalog),
          getJson(ENDPOINTS.methodCatalog)
        ]);
      } catch (error) {
        applyCatalogFailure(gen, token, error);
        return;
      }
      Promise.resolve(pending).then(function (results) {
        if (!catalogAlive(gen, token)) return;
        var methodCatalog = results[1];
        state.methodCatalog = methodCatalog && typeof methodCatalog === "object" && !Array.isArray(methodCatalog)
          ? methodCatalog
          : {};
        state.workflows = filterWorkflows(results[0]);
        if (!state.workflows.length) {
          state.catalog.status = "empty";
          state.catalog.error = null;
          state.entry = null;
          state.method = emptyMethod();
          state.step = 0;
          state.maxReached = 0;
          resetContract();
          resetExtras();
          renderAll();
          return;
        }
        state.catalog.status = "ready";
        state.catalog.error = null;
        selectWorkflow(pickDefaultWorkflow(state.workflows), { silent: true });
        maybeRestoreQueuedDraft();
        renderAll();
      }, function (error) {
        applyCatalogFailure(gen, token, error);
      });
    }

    function applyCatalogFailure(gen, token, error) {
      if (!catalogAlive(gen, token)) return;
      state.catalog.status = "error";
      state.catalog.error = error;
      state.workflows = [];
      state.entry = null;
      state.method = emptyMethod();
      state.step = 0;
      state.maxReached = 0;
      state.navHint = "";
      resetContract();
      resetExtras();
      renderAll();
    }

    function retryCatalog() {
      if (!state.mounted || state.disposed) return;
      state.step = 0;
      state.maxReached = 0;
      state.navHint = "";
      loadCatalogs();
    }

    function selectWorkflow(entry, options) {
      options = options || {};
      state.entry = entry || null;
      if (typeof deps.onWorkflowChange === "function") {
        try { deps.onWorkflowChange(state.entry ? state.entry.id : "", { silent: !!options.silent }); } catch (e) { /* host callback is advisory */ }
      }
      state.method = projectMethod(state.entry, state.methodCatalog);
      resetContract();
      resetExtras();
      bumpRevision();
      if (state.method.extended) loadContract();
      if (!options.silent) {
        state.navHint = "";
        renderAll();
      }
    }

    function selectProfile(profileId) {
      if (!state.entry) return;
      state.method = projectMethod(state.entry, state.methodCatalog, { profileId: profileId });
      bumpRevision();
      scheduleDraftSave();
      renderMethod();
      renderReview();
      renderPreflight();
      renderSubmit();
      renderRoot();
      renderActions();
    }

    function onMethodFieldChange(event) {
      var target = event && event.target;
      if (!target || typeof target.getAttribute !== "function") return;
      var field = target.getAttribute("data-field");
      var levelId = target.getAttribute("data-level");
      if (!field || levelId == null) return;
      var kind = textOf(target.getAttribute("data-kind"));
      var levels = state.method.levels;
      for (var i = 0; i < levels.length; i += 1) {
        if (levels[i].levelId !== levelId) continue;
        for (var j = 0; j < levels[i].rows.length; j += 1) {
          var row = levels[i].rows[j];
          if (row.field !== field) continue;
          if (kind === "bool") {
            row.value = target.checked === true;
          } else if (kind === "multi") {
            var values = [];
            (row._inputs || []).forEach(function (checkbox) {
              if (checkbox.checked === true) values.push(textOf(checkbox.getAttribute("data-option")));
            });
            row.value = values;
          } else {
            row.value = textOf(target.value);
          }
        }
      }
      bumpRevision();
      scheduleDraftSave();
      renderReview();
      renderPreflight();
      renderSubmit();
      renderRoot();
      renderActions();
    }

    /* ── navigation ──────────────────────────────────────────────── */

    function ensureResources() {
      if (state.step !== 1) return;
      if (!state.resources.loaded && state.resources.status !== "loading") loadResources();
      if (!state.queue.loaded && state.queue.status !== "loading") loadQueues();
    }

    function nextStep() {
      if (!state.mounted || state.disposed) return;
      if (state.step >= STEP_IDS.length - 1) return;
      if (!stepValid(state.step)) {
        state.navHint = stepReason(state.step);
        renderAll();
        return;
      }
      state.navHint = "";
      state.step += 1;
      if (state.step > state.maxReached) state.maxReached = state.step;
      if (state.step === 1) ensureResources();
      renderAll();
    }

    function backStep() {
      if (!state.mounted || state.disposed || state.step <= 0) return;
      state.navHint = "";
      state.step -= 1;
      renderAll();
    }

    function goToStep(index) {
      if (!state.mounted || state.disposed) return;
      if (index < 0 || index >= STEP_IDS.length || index > state.maxReached) return;
      state.navHint = "";
      state.step = index;
      if (state.step === 1) ensureResources();
      renderAll();
    }

    /* ── molecular input (A-02) ──────────────────────────────────── */

    function beginInput(mode, action) {
      state.input.token += 1;
      state.input.mode = mode;
      state.input.status = "loading";
      state.input.pending = action;
      state.input.error = null;
      state.input.structure = null;
      state.input.charge = "";
      state.input.multiplicity = "";
      lockDownstream(1);
      renderAll();
      return state.input.token;
    }

    function applyStructure(structure) {
      state.input.status = "ready";
      state.input.pending = "";
      state.input.error = null;
      state.input.structure = structure;
      /* Seed the electronic state from the server facts, never from a literal:
       * a missing value stays empty and blocks until the user fills it in. */
      state.input.charge = structure && structure.charge != null ? String(structure.charge) : "";
      state.input.multiplicity = structure && structure.multiplicity != null
        ? String(structure.multiplicity)
        : "";
      bumpRevision();
      renderAll();
    }

    function failInput(message) {
      state.input.status = "error";
      state.input.pending = "";
      state.input.error = message || t("next.wizard.structure_failed");
      state.input.structure = null;
      renderAll();
    }

    function rejectInput(mode, message) {
      if (!state.mounted || state.disposed) return;
      state.input.mode = mode;
      state.input.token += 1;
      state.input.pending = "";
      state.input.status = "error";
      state.input.error = message;
      state.input.structure = null;
      state.input.charge = "";
      state.input.multiplicity = "";
      lockDownstream(1);
      renderAll();
    }

    function invalidateEditedInput() {
      if (!state.mounted || state.disposed) return;
      state.input.token += 1;
      state.input.pending = "";
      state.input.status = "empty";
      state.input.error = null;
      state.input.structure = null;
      state.input.charge = "";
      state.input.multiplicity = "";
      bumpRevision();
      lockDownstream(1);
      renderInput();
      renderElectronic();
      renderRoot();
      renderReview();
      renderPreflight();
      renderSubmit();
      renderActions();
    }

    function runInput(mode, action, builder) {
      if (!state.mounted || state.disposed) return;
      var gen = state.generation;
      var token = beginInput(mode, action);
      var guard = function () { return inputAlive(gen, token); };
      var pending;
      try {
        pending = builder(guard);
      } catch (error) {
        pending = Promise.reject(error);
      }
      Promise.resolve(pending).then(function (structure) {
        if (!guard() || !structure) return;
        applyStructure(structure);
      }, function (error) {
        if (!guard()) return;
        failInput(describeError(error));
      });
    }

    function resolveSmiles() {
      if (!state.mounted || state.disposed) return;
      var smiles = textOf(state.ui.smilesInput && state.ui.smilesInput.value).trim();
      state.input.smiles = smiles;
      state.input.mode = "smiles";
      if (!smiles) {
        rejectInput("smiles", t("next.wizard.err.smiles_required"));
        return;
      }
      runInput("smiles", "resolve", function (guard) {
        return postJson(ENDPOINTS.resolve, { smiles: smiles }).then(function (resolveBody) {
          if (!guard()) return null;
          if (!resolveBody || resolveBody.valid !== true) {
            throw new Error(t("next.wizard.err.resolve_failed", {
              message: (resolveBody && resolveBody.error) || t("next.wizard.err.resolve_empty")
            }));
          }
          var canonical = resolveBody.smiles ? textOf(resolveBody.smiles) : smiles;
          return postJson(ENDPOINTS.embed, { smiles: canonical }).then(function (embedBody) {
            if (!guard()) return null;
            if (!embedBody || embedBody.error || !embedBody.xyz) {
              throw new Error(t("next.wizard.err.embed_failed", {
                message: (embedBody && embedBody.error) || t("next.wizard.err.embed_no_geometry")
              }));
            }
            return structureFromResolve(resolveBody, embedBody);
          });
        });
      });
    }

    function parseText() {
      if (!state.mounted || state.disposed) return;
      var content = textOf(state.ui.textInput && state.ui.textInput.value);
      state.input.text = content;
      state.input.mode = "paste";
      if (!content.trim()) {
        rejectInput("paste", t("next.wizard.err.text_required"));
        return;
      }
      runInput("paste", "parse", function (guard) {
        return parseContent(content, "", guard, "paste");
      });
    }

    function parseContent(content, filename, guard, source) {
      return postJson(ENDPOINTS.parse, {
        content: content,
        format: "auto",
        filename: filename
      }).then(function (body) {
        if (!guard()) return null;
        if (!body || body.ok !== true || !Array.isArray(body.structures) || !body.structures.length) {
          var reason = body && Array.isArray(body.errors) && body.errors.length
            ? textOf(body.errors[0])
            : t("next.wizard.err.parse_no_structures");
          throw new Error(t("next.wizard.err.parse_failed", { message: reason }));
        }
        var asset = body.structures[0];
        if (asset && typeof asset.xyz === "string" && asset.xyz) {
          return structureFromAsset(asset, source, filename);
        }
        var embedPayload = null;
        if (asset && typeof asset.molfile === "string" && asset.molfile) {
          embedPayload = { molfile: asset.molfile };
        } else if (asset && typeof asset.smiles === "string" && asset.smiles) {
          embedPayload = { smiles: asset.smiles };
        }
        if (!embedPayload) throw new Error(t("next.wizard.err.no_geometry"));
        return postJson(ENDPOINTS.embed, embedPayload).then(function (embedBody) {
          if (!guard()) return null;
          if (!embedBody || embedBody.error || !embedBody.xyz) {
            throw new Error(t("next.wizard.err.embed_failed", {
              message: (embedBody && embedBody.error) || t("next.wizard.err.embed_no_geometry")
            }));
          }
          return structureFromEmbedded(asset, embedBody, source, filename);
        });
      });
    }

    function readFile(file) {
      if (typeof deps.readFile === "function") {
        try {
          return Promise.resolve(deps.readFile(file));
        } catch (error) {
          return Promise.reject(error);
        }
      }
      if (file && typeof file.text === "function") {
        try {
          return Promise.resolve(file.text());
        } catch (error) {
          return Promise.reject(error);
        }
      }
      if (typeof FileReader !== "undefined") {
        return new Promise(function (resolve, reject) {
          var reader = new FileReader();
          reader.onload = function () { resolve(textOf(reader.result)); };
          reader.onerror = function () { reject(new Error("FileReader error")); };
          reader.readAsText(file);
        });
      }
      return Promise.reject(new Error(t("next.wizard.err.file_unsupported")));
    }

    function readChosenFile() {
      if (!state.mounted || state.disposed) return;
      var file = state.ui.fileInput && state.ui.fileInput.files && state.ui.fileInput.files[0]
        ? state.ui.fileInput.files[0]
        : null;
      if (!file) return;
      state.input.mode = "file";
      state.input.fileName = textOf(file.name);
      scheduleDraftSave();
      runInput("file", "file", function (guard) {
        return readFile(file).catch(function (error) {
          throw new Error(t("next.wizard.err.file_read_failed", { message: describeError(error) }));
        }).then(function (text) {
          if (!guard()) return null;
          var content = textOf(text);
          state.input.text = content;
          if (!content.trim()) throw new Error(t("next.wizard.err.file_empty"));
          return parseContent(content, state.input.fileName, guard, "file");
        });
      });
    }

    function setInputMode(mode) {
      if (!state.mounted || state.disposed) return;
      if (INPUT_MODES.indexOf(mode) < 0) return;
      state.input.mode = mode;
      scheduleDraftSave();
      renderAll();
    }

    /* ── resources (A-04) ────────────────────────────────────────── */

    function loadResources() {
      if (!state.mounted || state.disposed) return;
      var gen = state.generation;
      state.resources.token += 1;
      var token = state.resources.token;
      state.resources.status = "loading";
      state.resources.error = null;
      renderAll();
      var pending;
      try {
        pending = getJson(ENDPOINTS.capabilities);
      } catch (error) {
        pending = Promise.reject(error);
      }
      Promise.resolve(pending).then(function (body) {
        if (!resourceAlive(gen, token)) return;
        var defaults = projectResourceDefaults(body);
        state.resources.defaults = defaults;
        state.resources.loaded = true;
        if (defaults.published) {
          state.resources.status = "ready";
          if (!state.resources.touched) {
            state.resources.cores = defaults.cores;
            state.resources.memory = defaults.memoryMb;
          }
        } else {
          state.resources.status = "empty";
        }
        renderAll();
      }, function (error) {
        if (!resourceAlive(gen, token)) return;
        state.resources.status = "error";
        state.resources.error = error;
        state.resources.loaded = false;
        renderAll();
      });
    }

    function loadQueues() {
      if (!state.mounted || state.disposed) return;
      var gen = state.generation;
      state.queue.token += 1;
      var token = state.queue.token;
      state.queue.status = "loading";
      state.queue.error = null;
      renderAll();
      var pending;
      try {
        pending = getJson(ENDPOINTS.nodes);
      } catch (error) {
        pending = Promise.reject(error);
      }
      Promise.resolve(pending).then(function (body) {
        if (!queueAlive(gen, token)) return;
        state.queue.rows = projectQueues(body);
        state.queue.loaded = true;
        state.queue.status = state.queue.rows.length ? "ready" : "empty";
        renderAll();
      }, function (error) {
        if (!queueAlive(gen, token)) return;
        state.queue.status = "error";
        state.queue.error = error;
        state.queue.loaded = false;
        renderAll();
      });
    }

    function retryResources() {
      if (!state.mounted || state.disposed) return;
      loadResources();
      loadQueues();
    }

    /* ── preflight (A-05) ────────────────────────────────────────── */

    function applyPreflightSuccess(body, planKey, request, gen, token) {
      if (!preflightAlive(gen, token)) return { ok: false };
      state.preflight.status = "ok";
      state.preflight.digest = textOf(body.plan_digest);
      state.preflight.planKey = planKey;
      state.preflight.revision = state.revision;
      /* Freeze the exact body this digest was computed on: A-06 submits this
       * copy, never a later re-derivation from possibly edited inputs. */
      state.preflight.request = request ? JSON.parse(JSON.stringify(request)) : null;
      state.preflight.error = null;
      state.preflight.errorStatus = null;
      state.preflight.errorCode = "";
      state.preflight.reasons = [];
      state.preflight.fieldErrors = {};
      state.preflight.advisories = normalizeAdvisories(body.advisories);
      renderAll();
      return { ok: true, digest: state.preflight.digest, planKey: planKey };
    }

    function applyPreflightInfeasible(body, gen, token) {
      if (!preflightAlive(gen, token)) return { ok: false };
      state.preflight.status = "infeasible";
      state.preflight.digest = "";
      state.preflight.planKey = "";
      state.preflight.request = null;
      state.preflight.reasons = normalizeReasons(body && body.reasons);
      if (!state.preflight.reasons.length) {
        state.preflight.reasons = [{
          code: "",
          message: t("next.config.preflight_failed"),
          field: "",
          rule: ""
        }];
      }
      state.preflight.fieldErrors = fieldErrorsFrom(state.preflight.reasons);
      state.preflight.advisories = normalizeAdvisories(body && body.advisories);
      renderAll();
      return { ok: false };
    }

    function applyPreflightFailure(error, gen, token) {
      if (!preflightAlive(gen, token)) return { ok: false };
      var parsed = parsePreflightError(error);
      state.preflight.status = "error";
      state.preflight.digest = "";
      state.preflight.planKey = "";
      state.preflight.request = null;
      state.preflight.error = parsed.message;
      state.preflight.errorStatus = parsed.status;
      state.preflight.errorCode = parsed.code;
      state.preflight.reasons = parsed.reasons;
      state.preflight.fieldErrors = parsed.fieldErrors;
      state.preflight.advisories = [];
      renderAll();
      return { ok: false, status: parsed.status, code: parsed.code };
    }

    function blockPreflight(errors) {
      state.preflight.status = "blocked";
      state.preflight.digest = "";
      state.preflight.planKey = "";
      state.preflight.request = null;
      var list = Array.isArray(errors) ? errors : [];
      state.preflight.reasons = list.length
        ? list.map(function (error) {
            return {
              code: "input_invalid",
              message: textOf(error.text) || t("next.s2scan.preflight_blocked"),
              field: textOf(error.field),
              rule: ""
            };
          })
        : [{
            code: "",
            message: t("next.s2scan.preflight_blocked"),
            field: "",
            rule: ""
          }];
      state.preflight.fieldErrors = fieldErrorsFrom(state.preflight.reasons);
      renderAll();
      return { ok: false, blocked: true };
    }

    /**
     * Run the zero-side-effect preflight for the current plan. Never creates a
     * task: the only request is `POST /api/v1/preflight`, and its digest is
     * frozen against the exact plan identity (any later edit marks it stale).
     */
    function runPreflight() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      if (state.step !== STEP_IDS.length - 1) return Promise.resolve({ ok: false });
      if (state.preflight.status === "loading") return Promise.resolve({ ok: false });
      var checked = inputCheck();
      if (!checked.ok) return Promise.resolve(blockPreflight(checked.errors));
      if (!stepValid(1)) {
        var reason = stepReason(1);
        return Promise.resolve(blockPreflight([{ field: "", text: reason || t("next.s2scan.preflight_blocked") }]));
      }
      var request = checked.request;
      if (!request) return Promise.resolve(blockPreflight([{
        field: "", text: t("next.s2scan.preflight_blocked")
      }]));
      var planKey = planIdentity(request);
      var gen = state.generation;
      state.preflight.token += 1;
      var token = state.preflight.token;
      state.preflight.status = "loading";
      state.preflight.digest = "";
      state.preflight.planKey = "";
      state.preflight.request = null;
      state.preflight.error = null;
      state.preflight.errorStatus = null;
      state.preflight.errorCode = "";
      state.preflight.reasons = [];
      state.preflight.fieldErrors = {};
      state.preflight.advisories = [];
      state.preflight.attempts += 1;
      renderAll();
      var pending;
      try {
        pending = postJson(ENDPOINTS.preflight, request);
      } catch (error) {
        pending = Promise.reject(error);
      }
      return Promise.resolve(pending).then(function (body) {
        if (!preflightAlive(gen, token)) return { ok: false };
        if (!body || body.feasible !== true || !body.plan_digest) {
          return applyPreflightInfeasible(body, gen, token);
        }
        return applyPreflightSuccess(body, planKey, request, gen, token);
      }, function (error) {
        return applyPreflightFailure(error, gen, token);
      });
    }

    /* ── submit + receipt (A-06) / submit check (A-08) ───────────── */

    function openSubmitCheck() {
      if (!state.mounted || state.disposed) return;
      state.dialog.open = true;
      renderSubmit();
    }

    function closeSubmitCheck() {
      if (state.disposed) return;
      state.dialog.open = false;
      renderSubmit();
    }

    /** Navigation is the shell's `commands.openTask` and nothing else. */
    function openReceiptTask() {
      var commands = state.commands;
      var receipt = state.submit.receipt;
      if (!commands || typeof commands.openTask !== "function" || !receipt || !receipt.jobId) {
        return false;
      }
      try {
        commands.openTask({
          jobId: receipt.jobId,
          attempt: receipt.attempt,
          source: MODULE_ID
        });
        return true;
      } catch (error) {
        return false;
      }
    }

    function applySubmitReceipt(response, planKey, gen, token) {
      if (!submitAlive(gen, token)) return { ok: false, stale: true };
      var receipt = normalizeReceipt(response);
      if (!receipt) {
        /* A 2xx without a job_id is not a receipt: the outcome is unknown. */
        return applySubmitFailure({
          message: "missing job_id in the submit response"
        }, gen, token);
      }
      state.submit.status = "submitted";
      state.submit.receipt = receipt;
      state.submit.receiptKey = planKey;
      state.submit.error = null;
      state.submit.errorStatus = null;
      state.submit.errorCode = "";
      state.submit.errorField = "";
      state.submit.errorRule = "";
      state.dialog.open = false;
      renderAll();
      openReceiptTask();
      /* A-09: the plan is submitted, so its draft is removed. The delete is
       * fire-and-forget and never changes the receipt: a failure is surfaced as
       * a retryable notice (non-fatal). */
      deleteDraftAfterSubmit();
      return { ok: true, receipt: receipt };
    }

    function applySubmitFailure(error, gen, token) {
      if (!submitAlive(gen, token)) return { ok: false, stale: true };
      var mapped = mapSubmitError(error);
      state.submit.status = "error";
      state.submit.error = mapped;
      state.submit.errorStatus = mapped.status;
      state.submit.errorCode = mapped.code;
      state.submit.errorField = mapped.field;
      state.submit.errorRule = mapped.rule;
      /* A fingerprint mismatch means the frozen plan no longer matches what the
       * server would submit: the preflight must be re-run, never silently
       * re-fingerprinted. */
      if (mapped.status === 400 && mapped.code === "preflight_stale") {
        state.preflight.token += 1;
        state.preflight.status = "stale";
        state.preflight.digest = "";
        state.preflight.planKey = "";
        state.preflight.request = null;
        state.preflight.advisories = [];
        state.preflight.error = mapped.message;
        state.preflight.errorStatus = mapped.status;
        state.preflight.errorCode = mapped.code;
      }
      state.dialog.open = false;
      renderAll();
      return { ok: false, error: mapped };
    }

    /**
     * A-08 confirm: send the frozen A-05 plan exactly once per plan identity.
     *
     * The check is re-evaluated here (a stale dialog can never submit), a busy
     * submit ignores the click, and the Idempotency-Key is memoized on the plan
     * identity so a retry after a 5xx/transport failure is a server replay, not
     * a second task. A plan that already produced a receipt is never re-sent.
     */
    function confirmSubmit() {
      if (!state.mounted || state.disposed || !state.client) {
        return Promise.resolve({ ok: false });
      }
      var check = currentSubmitCheck();
      if (!check.ok) {
        state.dialog.open = true;
        renderSubmit();
        return Promise.resolve({ ok: false, blocked: true, check: check });
      }
      if (state.submit.status === "loading") {
        return Promise.resolve({ ok: false, busy: true });
      }
      var planKey = currentPlanKey();
      if (state.submit.receipt && state.submit.receiptKey === planKey) {
        state.dialog.open = false;
        renderSubmit();
        return Promise.resolve({ ok: true, receipt: state.submit.receipt, replayed: true });
      }
      var request = state.preflight.request || currentPreflightRequest();
      var body = buildSubmitBody(request, state.preflight.digest);
      if (!body) {
        state.dialog.open = true;
        renderSubmit();
        return Promise.resolve({ ok: false, blocked: true, check: check });
      }
      /* A receipt for a different plan is not this submit's outcome; clearing it
       * lets the new plan's error/success be the one the receipt block shows. */
      if (state.submit.receipt && state.submit.receiptKey !== planKey) {
        state.submit.receipt = null;
        state.submit.receiptKey = "";
      }
      if (!state.submit.idempotencyKey || state.submit.keyPlan !== planKey) {
        state.submit.idempotencyKey = newIdempotencyKey();
        state.submit.keyPlan = planKey;
      }
      var gen = state.generation;
      state.submit.token += 1;
      var token = state.submit.token;
      state.submit.status = "loading";
      state.submit.error = null;
      state.submit.errorStatus = null;
      state.submit.errorCode = "";
      state.submit.errorField = "";
      state.submit.errorRule = "";
      state.submit.attempts += 1;
      state.dialog.open = false;
      renderAll();
      var pending;
      try {
        pending = call(ENDPOINTS.jobs, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": state.submit.idempotencyKey
          },
          body: JSON.stringify(body),
          timeoutMs: REQUEST_TIMEOUT_MS
        });
      } catch (error) {
        pending = Promise.reject(error);
      }
      return Promise.resolve(pending).then(function (response) {
        return applySubmitReceipt(response, planKey, gen, token);
      }, function (error) {
        return applySubmitFailure(error, gen, token);
      });
    }

    /* ── drafts (A-09) ───────────────────────────────────────────── */

    /** Draft row label: module id + the catalog label (server catalog copy). */
    function draftName() {
      var label = state.entry ? optionLabel(state.entry) : "";
      return label ? MODULE_ID + " · " + label : MODULE_ID;
    }

    function currentDraftSnapshot() {
      return draftSnapshot({
        entry: state.entry,
        method: state.method,
        input: state.input,
        resources: state.resources,
        extras: state.extras,
        step: state.step
      });
    }

    /** One draft-route request through the injected client seam (never fetch). */
    function draftRequest(path, options) {
      options = options || {};
      var opts = {
        method: options.method || "GET",
        timeoutMs: DRAFT_TIMEOUT_MS
      };
      if (options.body !== undefined) {
        opts.headers = { "Content-Type": "application/json" };
        opts.body = JSON.stringify(options.body);
      }
      return call(path, opts);
    }

    /**
     * Read at most ONE draft on mount: the newest row from `GET /drafts`, then
     * its snapshot from `GET /drafts/{id}`. The list summary carries no kind,
     * so the snapshot document itself decides whether this module owns it
     * (`kind`/`version`); a foreign/legacy document is left untouched and no
     * banner is offered. There is no scan of older rows and no second cache.
     */
    function loadDraft() {
      if (!state.mounted || state.disposed || !state.client) return Promise.resolve(null);
      var gen = state.generation;
      state.draft.token += 1;
      var token = state.draft.token;
      state.draft.status = "loading";
      state.draft.error = null;
      state.draft.errorCode = "";
      state.draft.notice = "";
      state.draft.noticeKind = "";
      renderDraft();
      function fail(error) {
        if (!draftAlive(gen, token)) return;
        var mapped = mapDraftError(error);
        state.draft.status = "error";
        state.draft.error = mapped;
        state.draft.errorCode = mapped.code;
        state.draft.notice = t("next.nodes.section_error", {
          section: t("next.approval.status.draft"),
          message: mapped.message
        });
        state.draft.noticeKind = "lookup";
        renderAll();
      }
      var pendingList;
      try {
        pendingList = draftRequest(ENDPOINTS.drafts, { method: "GET" });
      } catch (error) {
        pendingList = Promise.reject(error);
      }
      return Promise.resolve(pendingList).then(function (body) {
        if (!draftAlive(gen, token)) return null;
        var items = body && Array.isArray(body.items) ? body.items : [];
        var first = items.length && items[0] && typeof items[0] === "object" ? items[0] : null;
        var draftId = first ? textOf(first.draft_id).trim() : "";
        if (!draftId) {
          state.draft.status = "none";
          renderAll();
          return null;
        }
        var pendingRead;
        try {
          pendingRead = draftRequest(draftPath(draftId), { method: "GET" });
        } catch (error) {
          pendingRead = Promise.reject(error);
        }
        return Promise.resolve(pendingRead).then(function (detail) {
          if (!draftAlive(gen, token)) return null;
          var snapshot = normalizeDraftSnapshot(detail && detail.snapshot);
          if (!snapshot) {
            state.draft.status = "none";
            renderAll();
            return null;
          }
          var revision = integerText(detail.revision);
          state.draft.meta = {
            draftId: textOf(detail.draft_id) || draftId,
            revision: revision === null ? 0 : revision,
            updatedAt: textOf(detail.updated_at),
            createdAt: textOf(detail.created_at)
          };
          state.draft.snapshot = snapshot;
          state.draft.status = "found";
          state.draft.notice = "";
          state.draft.noticeKind = "";
          maybeRestoreQueuedDraft();
          renderAll();
          return snapshot;
        }, function (error) {
          if (!draftAlive(gen, token)) return null;
          var mapped = mapDraftError(error);
          if (mapped.missing) {
            state.draft.status = "none";
            renderAll();
            return null;
          }
          fail(error);
          return null;
        });
      }, function (error) {
        fail(error);
        return null;
      });
    }

    /** Apply the workflow's saved method profile + field values (catalog types). */
    function applySavedMethod(snapshot) {
      if (!state.entry || !state.method || !state.method.schema) return;
      var saved = snapshot.method || {};
      var profileId = textOf(saved.profile_id);
      if (profileId && profileId !== state.method.profileId) {
        var projected = projectMethod(state.entry, state.methodCatalog, { profileId: profileId });
        if (projected && projected.schema) state.method = projected;
      }
      var values = saved.levels && typeof saved.levels === "object" ? saved.levels : {};
      Object.keys(values).forEach(function (levelId) {
        (state.method.levels || []).forEach(function (level) {
          if (!level || level.levelId !== levelId) return;
          var savedValues = values[levelId];
          (level.rows || []).forEach(function (row) {
            if (!row || !Object.prototype.hasOwnProperty.call(savedValues, row.field)) return;
            var value = savedValues[row.field];
            if (row.type === "bool") {
              row.value = value === true || value === "true" || value === 1 || value === "1";
            } else if (row.type === "multi") {
              row.value = Array.isArray(value) ? value.map(function (item) {
                return textOf(item);
              }) : [];
            } else {
              row.value = textOf(value);
            }
          });
        });
      });
    }

    /**
     * Apply one normalized snapshot to the live wizard. Only presentation /
     * input state moves: workflow, method profile + values, typed input texts,
     * raw charge/multiplicity, resource inputs, scan atoms, frozen payload and
     * the step (clamped to the input step — the review step needs a fresh
     * preflight that is never persisted). The resolved structure is cleared;
     * a restored draft always requires an explicit re-parse.
     */
    function applyDraftSnapshot(snapshot) {
      if (!snapshot) return false;
      state.draft.restoreQueued = false;
      var entry = findWorkflow(state.workflows, snapshot.workflow_id);
      if (!entry) {
        state.draft.notice = t("next.tasks.action_failed", {
          action: t("next.config.draft_restore"),
          message: snapshot.workflow_id || unknown()
        });
        state.draft.noticeKind = "restore";
        renderAll();
        return false;
      }
      selectWorkflow(entry, { silent: true });
      applySavedMethod(snapshot);
      var inputs = snapshot.input || {};
      state.input.mode = INPUT_MODES.indexOf(inputs.mode) >= 0 ? inputs.mode : "smiles";
      state.input.smiles = inputs.smiles || "";
      state.input.text = inputs.text || "";
      state.input.fileName = inputs.file_name || "";
      state.input.charge = inputs.charge || "";
      state.input.multiplicity = inputs.multiplicity || "";
      state.input.status = "empty";
      state.input.pending = "";
      state.input.error = null;
      state.input.structure = null;
      var resources = snapshot.resources || {};
      state.resources.cores = resources.cores || "";
      state.resources.memory = resources.memory || "";
      state.resources.touched = !!(state.resources.cores || state.resources.memory);
      state.extras.scanAtoms = snapshot.scan_atoms || "";
      state.extras.payload = snapshot.payload || "";
      /* Source task refs are never drafted: the source adapter is re-bound by
       * the operator after a restore. */
      state.extras.sourceJobId = "";
      state.extras.sourceProductId = "";
      state.extras.sourceModeIndex = "";
      var step = integerText(snapshot.step);
      if (step === null || step < 0) step = 0;
      if (step > 1) step = 1;
      state.step = step;
      state.maxReached = step;
      state.draft.lastSaved = stableStringify(snapshot);
      state.draft.snapshot = null;
      state.draft.status = "restored";
      state.draft.error = null;
      state.draft.errorCode = "";
      state.draft.notice = "";
      state.draft.noticeKind = "";
      bumpRevision();
      if (state.step === 1) ensureResources();
      renderAll();
      return true;
    }

    /** Explicit "恢复": waits for the catalog, then applies the snapshot. */
    function restoreDraft() {
      if (!state.mounted || state.disposed) return false;
      if (state.draft.status !== "found") return false;
      if (state.catalog.status !== "ready" || !state.methodCatalog) {
        state.draft.restoreQueued = true;
        renderDraft();
        return false;
      }
      var snapshot = state.draft.snapshot;
      if (!snapshot) return false;
      return applyDraftSnapshot(snapshot);
    }

    function maybeRestoreQueuedDraft() {
      if (!state.draft || !state.draft.restoreQueued) return;
      if (state.catalog.status !== "ready" || !state.methodCatalog) return;
      state.draft.restoreQueued = false;
      if (state.draft.snapshot) applyDraftSnapshot(state.draft.snapshot);
    }

    /** Arm the debounce. A burst of edits produces exactly one save request. */
    function scheduleDraftSave() {
      if (!state.mounted || state.disposed || !state.client) return false;
      if (state.draft.status === "conflict") return false;
      cancelDraftTimer();
      var gen = state.generation;
      state.draft.timer = timerSet(function () {
        state.draft.timer = null;
        if (!alive(gen)) return;
        saveDraft();
      }, draftDebounceMs());
      return true;
    }

    /** Serialize one draft write (POST for the first, PUT + CAS afterwards). */
    function saveDraft() {
      if (!state.mounted || state.disposed || !state.client) return Promise.resolve({ ok: false });
      if (state.draft.status === "conflict") return Promise.resolve({ ok: false, conflict: true });
      if (state.draft.busy) {
        state.draft.queued = true;
        return Promise.resolve({ ok: false, busy: true });
      }
      var snapshot = currentDraftSnapshot();
      if (!snapshot.workflow_id) return Promise.resolve({ ok: false, empty: true });
      var serialized = stableStringify(snapshot);
      if (state.draft.meta && serialized === state.draft.lastSaved) {
        state.draft.status = "saved";
        renderDraft();
        return Promise.resolve({ ok: true, skipped: true });
      }
      var meta = state.draft.meta;
      var path = meta ? draftPath(meta.draftId) : ENDPOINTS.drafts;
      var method = meta ? "PUT" : "POST";
      var body = draftBody(snapshot, {
        name: draftName(),
        projectId: state.projectId,
        expectedRevision: meta ? meta.revision : null
      });
      var gen = state.generation;
      state.draft.saveToken += 1;
      var token = state.draft.saveToken;
      state.draft.status = "saving";
      state.draft.busy = true;
      state.draft.notice = "";
      state.draft.noticeKind = "";
      renderDraft();
      var pending;
      try {
        pending = draftRequest(path, { method: method, body: body });
      } catch (error) {
        pending = Promise.reject(error);
      }
      return Promise.resolve(pending).then(function (response) {
        if (!alive(gen) || !state.draft || state.draft.saveToken !== token) {
          return { ok: false, stale: true };
        }
        state.draft.busy = false;
        var draftId = response && response.draft_id ? textOf(response.draft_id) : "";
        if (!draftId) {
          return applyDraftSaveFailure(
            { message: "missing draft_id in the draft response" }, gen, token
          );
        }
        var revision = integerText(response.revision);
        state.draft.meta = {
          draftId: draftId,
          revision: revision === null ? (meta && meta.revision != null ? meta.revision : 0) : revision,
          updatedAt: textOf(response.updated_at),
          createdAt: textOf(response.created_at) || (meta ? meta.createdAt : "")
        };
        state.draft.lastSaved = serialized;
        state.draft.savedAt = textOf(response.updated_at);
        state.draft.status = "saved";
        state.draft.error = null;
        state.draft.errorCode = "";
        state.draft.notice = "";
        state.draft.noticeKind = "";
        state.draft.writes += 1;
        renderDraft();
        if (state.draft.deleteQueued) {
          runQueuedDraftDelete();
        } else {
          flushQueuedDraftSave(gen);
        }
        return { ok: true, meta: state.draft.meta };
      }, function (error) {
        if (!alive(gen) || !state.draft || state.draft.saveToken !== token) {
          return { ok: false, stale: true };
        }
        state.draft.busy = false;
        return applyDraftSaveFailure(error, gen, token);
      });
    }

    function applyDraftSaveFailure(error, gen, token) {
      if (!alive(gen) || !state.draft) return { ok: false, stale: true };
      var mapped = mapDraftError(error);
      state.draft.busy = false;
      state.draft.queued = false;
      state.draft.error = mapped;
      state.draft.errorCode = mapped.code;
      if (mapped.conflict) {
        /* Autosave stops here: the other window's revision must win until the
         * operator reloads it or forks the local state into a new draft. */
        state.draft.status = "conflict";
        state.draft.notice = "";
        state.draft.noticeKind = "";
        cancelDraftTimer();
      } else if (mapped.missing) {
        /* The remote draft vanished: never silently recreate it from a failed
         * PUT; the next explicit edit opens a fresh draft. */
        state.draft.meta = null;
        state.draft.lastSaved = "";
        state.draft.status = "error";
        state.draft.notice = t("next.nodes.section_error", {
          section: t("next.approval.status.draft"),
          message: mapped.message
        });
        state.draft.noticeKind = "lookup";
      } else {
        state.draft.status = "error";
        state.draft.notice = "";
        state.draft.noticeKind = "";
      }
      if (state.draft.deleteQueued) runQueuedDraftDelete();
      renderAll();
      return { ok: false, error: mapped };
    }

    function flushQueuedDraftSave(gen) {
      if (!state.draft || !state.draft.queued) return;
      state.draft.queued = false;
      if (!alive(gen) || state.draft.status === "conflict") return;
      saveDraft();
    }

    /** Explicit "丢弃": delete the remote row (and never resurrect it here). */
    function discardDraft() {
      return deleteDraft({ reason: "discard" });
    }

    /**
     * Delete one draft. A missing row (404 / `draft_not_found`) is the same
     * outcome as a successful delete; any other failure keeps the row metadata
     * and surfaces a non-blocking notice so the action can be retried.
     */
    function deleteDraft(options) {
      options = options || {};
      if (!state.mounted || state.disposed || !state.client) return Promise.resolve({ ok: false });
      cancelDraftTimer();
      state.draft.restoreQueued = false;
      if (state.draft.busy) {
        /* A save may be creating the row right now: delete after it settles so
         * a submitted plan can never leave an orphan draft behind. */
        state.draft.deleteQueued = { reason: textOf(options.reason) };
        return Promise.resolve({ ok: false, queued: true });
      }
      var meta = state.draft.meta;
      if (!meta) {
        clearDraftMeta();
        state.draft.status = "none";
        state.draft.error = null;
        state.draft.errorCode = "";
        state.draft.notice = "";
        state.draft.noticeKind = "";
        renderAll();
        return Promise.resolve({ ok: true, skipped: true });
      }
      var gen = state.generation;
      state.draft.token += 1;
      var token = state.draft.token;
      var draftId = meta.draftId;
      state.draft.busy = true;
      state.draft.status = "deleting";
      state.draft.notice = "";
      state.draft.noticeKind = "";
      renderDraft();
      var pending;
      try {
        pending = draftRequest(draftPath(draftId), { method: "DELETE" });
      } catch (error) {
        pending = Promise.reject(error);
      }
      return Promise.resolve(pending).then(function () {
        if (!draftAlive(gen, token)) return { ok: false, stale: true };
        state.draft.busy = false;
        clearDraftMeta();
        state.draft.status = "none";
        state.draft.error = null;
        state.draft.errorCode = "";
        state.draft.notice = "";
        state.draft.noticeKind = "";
        renderAll();
        runQueuedDraftDelete();
        return { ok: true, deleted: draftId };
      }, function (error) {
        if (!draftAlive(gen, token)) return { ok: false, stale: true };
        state.draft.busy = false;
        var mapped = mapDraftError(error);
        if (mapped.missing) {
          clearDraftMeta();
          state.draft.status = "none";
          state.draft.error = null;
          state.draft.errorCode = "";
          state.draft.notice = "";
          state.draft.noticeKind = "";
          renderAll();
          return { ok: true, deleted: draftId, missing: true };
        }
        state.draft.status = "error";
        state.draft.error = mapped;
        state.draft.errorCode = mapped.code;
        state.draft.notice = t("next.tasks.action_failed", {
          action: t("next.config.draft_discard"),
          message: mapped.message
        });
        state.draft.noticeKind = "delete";
        renderAll();
        if (state.draft.deleteQueued) runQueuedDraftDelete();
        return { ok: false, error: mapped };
      });
    }

    function runQueuedDraftDelete() {
      var queued = state.draft ? state.draft.deleteQueued : null;
      if (!state.draft) return;
      state.draft.deleteQueued = null;
      if (!queued || !state.mounted || state.disposed) return;
      if (state.draft.status === "conflict") {
        /* The queued save hit a revision conflict: never delete the other
         * window's draft implicitly. Surface the conflict instead. */
        state.draft.notice = t("next.config.draft_conflict");
        state.draft.noticeKind = "delete";
        renderDraft();
        return;
      }
      deleteDraft({ reason: queued.reason });
    }

    /**
     * A-06 success hook: the submitted plan's draft is removed. A failed delete
     * is non-fatal (the receipt stays valid) and is surfaced as a retryable
     * discard notice. A conflicting draft is never deleted — it may hold the
     * other window's newer edits.
     */
    function deleteDraftAfterSubmit() {
      if (!state.mounted || state.disposed) return Promise.resolve({ ok: false });
      cancelDraftTimer();
      if (state.draft.status === "conflict") {
        state.draft.notice = t("next.config.draft_conflict");
        state.draft.noticeKind = "delete";
        renderDraft();
        return Promise.resolve({ ok: false, conflict: true });
      }
      return deleteDraft({ reason: "submitted" });
    }

    /** Conflict "载入最新": adopt the server row (revision + snapshot), then apply. */
    function reloadDraft() {
      if (!state.mounted || state.disposed || !state.client) return Promise.resolve({ ok: false });
      var meta = state.draft.meta;
      if (!meta) return loadDraft();
      if (state.draft.busy) return Promise.resolve({ ok: false, busy: true });
      var gen = state.generation;
      state.draft.token += 1;
      var token = state.draft.token;
      var draftId = meta.draftId;
      state.draft.busy = true;
      state.draft.status = "loading";
      renderDraft();
      var pending;
      try {
        pending = draftRequest(draftPath(draftId), { method: "GET" });
      } catch (error) {
        pending = Promise.reject(error);
      }
      return Promise.resolve(pending).then(function (detail) {
        if (!draftAlive(gen, token)) return { ok: false, stale: true };
        state.draft.busy = false;
        var snapshot = normalizeDraftSnapshot(detail && detail.snapshot);
        if (!snapshot) {
          state.draft.status = "conflict";
          state.draft.notice = t("next.tasks.action_failed", {
            action: t("next.config.draft_reload"),
            message: t("next.config.draft_conflict")
          });
          state.draft.noticeKind = "lookup";
          renderAll();
          return { ok: false };
        }
        var revision = integerText(detail.revision);
        state.draft.meta = {
          draftId: textOf(detail.draft_id) || draftId,
          revision: revision === null ? meta.revision : revision,
          updatedAt: textOf(detail.updated_at),
          createdAt: textOf(detail.created_at) || meta.createdAt
        };
        state.draft.snapshot = snapshot;
        state.draft.error = null;
        state.draft.errorCode = "";
        var applied = applyDraftSnapshot(snapshot);
        renderAll();
        return { ok: applied };
      }, function (error) {
        if (!draftAlive(gen, token)) return { ok: false, stale: true };
        state.draft.busy = false;
        var mapped = mapDraftError(error);
        if (mapped.missing) {
          clearDraftMeta();
          state.draft.status = "none";
          state.draft.error = null;
          state.draft.errorCode = "";
          state.draft.notice = t("next.nodes.section_error", {
            section: t("next.approval.status.draft"),
            message: mapped.message
          });
          state.draft.noticeKind = "lookup";
          renderAll();
          return { ok: false, missing: true };
        }
        state.draft.status = "conflict";
        state.draft.notice = t("next.tasks.action_failed", {
          action: t("next.config.draft_reload"),
          message: mapped.message
        });
        state.draft.noticeKind = "lookup";
        renderAll();
        return { ok: false, error: mapped };
      });
    }

    /** Conflict "另存为新草稿": keep the local edits as a fresh row (no CAS). */
    function forkDraft() {
      if (!state.mounted || state.disposed || !state.client) return Promise.resolve({ ok: false });
      if (state.draft.busy) return Promise.resolve({ ok: false, busy: true });
      cancelDraftTimer();
      var snapshot = currentDraftSnapshot();
      if (!snapshot.workflow_id) return Promise.resolve({ ok: false, empty: true });
      var gen = state.generation;
      state.draft.saveToken += 1;
      var token = state.draft.saveToken;
      var body = draftBody(snapshot, { name: draftName(), projectId: state.projectId });
      state.draft.busy = true;
      state.draft.status = "saving";
      state.draft.notice = "";
      state.draft.noticeKind = "";
      renderDraft();
      var pending;
      try {
        pending = draftRequest(ENDPOINTS.drafts, { method: "POST", body: body });
      } catch (error) {
        pending = Promise.reject(error);
      }
      return Promise.resolve(pending).then(function (response) {
        if (!alive(gen) || !state.draft || state.draft.saveToken !== token) {
          return { ok: false, stale: true };
        }
        state.draft.busy = false;
        var draftId = response && response.draft_id ? textOf(response.draft_id) : "";
        if (!draftId) {
          return applyDraftSaveFailure(
            { message: "missing draft_id in the draft response" }, gen, token
          );
        }
        var revision = integerText(response.revision);
        state.draft.meta = {
          draftId: draftId,
          revision: revision === null ? 1 : revision,
          updatedAt: textOf(response.updated_at),
          createdAt: textOf(response.created_at)
        };
        state.draft.lastSaved = stableStringify(snapshot);
        state.draft.status = "saved";
        state.draft.error = null;
        state.draft.errorCode = "";
        state.draft.notice = "";
        state.draft.noticeKind = "";
        state.draft.writes += 1;
        renderAll();
        return { ok: true, meta: state.draft.meta };
      }, function (error) {
        if (!alive(gen) || !state.draft || state.draft.saveToken !== token) {
          return { ok: false, stale: true };
        }
        state.draft.busy = false;
        var mapped = mapDraftError(error);
        state.draft.status = "error";
        state.draft.error = mapped;
        state.draft.errorCode = mapped.code;
        state.draft.notice = "";
        state.draft.noticeKind = "";
        renderAll();
        return { ok: false, error: mapped };
      });
    }

    /* ── lifecycle ───────────────────────────────────────────────── */

    function subscribeContext() {
      if (!context || typeof context.subscribe !== "function") return;
      try {
        state.unsubscribe = context.subscribe(function (snapshot) { updateContext(snapshot); });
      } catch (error) {
        state.unsubscribe = null;
      }
    }

    function updateContext(snapshot) {
      state.snapshot = snapshot || {};
      state.projectId = snapshot && snapshot.projectId ? textOf(snapshot.projectId) : "";
    }

    function mount(hostEl, api) {
      api = api || {};
      hostEl = hostEl || (api.panel && api.panel.bodyEl);
      if (!hostEl || typeof hostEl.appendChild !== "function") {
        return Promise.reject(new TypeError("task-wizard: mount host missing"));
      }
      if (!doc || typeof doc.createElement !== "function") {
        return Promise.reject(new TypeError("task-wizard: document missing"));
      }
      if (state.mounted) {
        return Promise.reject(new Error("task-wizard: already mounted (dispose() first)"));
      }
      var client = resolveClient();
      if (!client) {
        return Promise.reject(new Error("task-wizard: structure client missing (" + t("next.wizard.client_missing") + ")"));
      }
      state.client = client;
      state.commands = deps.commands && typeof deps.commands === "object" ? deps.commands : null;
      state.generation += 1;
      state.mounted = true;
      state.disposed = false;
      state.suspended = false;
      state.host = hostEl;
      state.root = null;
      state.ui = {};
      state.snapshot = {};
      state.projectId = "";
      state.workflows = [];
      state.methodCatalog = null;
      state.entry = null;
      state.method = emptyMethod();
      state.step = 0;
      state.maxReached = 0;
      state.navHint = "";
      state.revision = 0;
      state.planCache = { revision: -1, key: "" };
      state.catalog = { status: "loading", error: null, token: state.catalog.token + 1 };
      resetInput();
      resetResources();
      resetContract();
      resetExtras();
      resetPreflight();
      resetSubmit();
      resetDraft();
      cleanup.splice(0);
      if (state.unsubscribe) {
        try { state.unsubscribe(); } catch (error) { /* previous context is gone */ }
        state.unsubscribe = null;
      }
      subscribeContext();
      buildDom();
      loadCatalogs();
      loadDraft();
      return Promise.resolve();
    }

    function suspend() {
      state.suspended = true;
    }

    function resume() {
      if (state.disposed) return;
      state.suspended = false;
      renderAll();
    }

    function dispose() {
      if (state.disposed) return;
      state.disposed = true;
      state.mounted = false;
      state.suspended = false;
      state.generation += 1;
      state.catalog.token += 1;
      state.input.token += 1;
      state.input.pending = "";
      state.resources.token += 1;
      state.resources.status = "empty";
      state.queue.token += 1;
      state.queue.status = "empty";
      state.contract.token += 1;
      state.contract.status = "idle";
      state.contract.body = null;
      state.contract.error = null;
      resetExtras();
      state.preflight.token += 1;
      state.preflight.status = "idle";
      state.submit.token += 1;
      state.submit.status = "idle";
      state.dialog.open = false;
      cancelDraftTimer();
      state.draft.token += 1;
      state.draft.saveToken += 1;
      state.draft.busy = false;
      state.draft.deleteQueued = null;
      cleanup.splice(0).forEach(function (fn) {
        try { fn(); } catch (error) { /* listener already gone */ }
      });
      if (state.unsubscribe) {
        try { state.unsubscribe(); } catch (error) { /* context already gone */ }
        state.unsubscribe = null;
      }
      if (state.root && state.root.parentNode && typeof state.root.parentNode.removeChild === "function") {
        try { state.root.parentNode.removeChild(state.root); } catch (error) { /* detached */ }
      }
      state.root = null;
      state.host = null;
      state.ui = {};
      state.workflows = [];
      state.methodCatalog = null;
      state.entry = null;
      state.method = emptyMethod();
      state.catalog.status = "empty";
      state.catalog.error = null;
      resetInput();
      resetResources();
      resetPreflight();
      resetSubmit();
      resetDraft();
      state.client = null;
      state.commands = null;
    }

    function loadStructure(ref) {
      if (!ref || ref.kind !== "inline-xyz" || typeof ref.text !== "string" || !ref.text.trim()) return { ok: false, error: "inline-xyz required" };
      beginInput("paste", "");
      applyStructure(structureFromAsset({ xyz: ref.text }, "structure-import"));
      return { ok: true };
    }

    return {
      mount: mount,
      loadStructure: loadStructure,
      updateContext: updateContext,
      suspend: suspend,
      resume: resume,
      dispose: dispose
    };
  }

  window.ACPNextModules[MODULE_ID] = {
    descriptor: DESCRIPTOR,
    factory: createInstance,
    _internals: {
      MODULE_ID: MODULE_ID,
      ENDPOINTS: ENDPOINTS,
      STEP_IDS: STEP_IDS,
      INPUT_MODES: INPUT_MODES,
      PROJECTED_FIELDS: PROJECTED_FIELDS,
      MINIMAL_SCHEMA_FIELDS: MINIMAL_SCHEMA_FIELDS,
      ADAPTERS: {
        structure: ADAPTER_STRUCTURE,
        scan: ADAPTER_SCAN,
        batch: ADAPTER_BATCH,
        source: ADAPTER_SOURCE,
        payload: ADAPTER_PAYLOAD
      },
      METHOD_EMIT: {
        levels: METHOD_EMIT_LEVELS,
        protocol: METHOD_EMIT_PROTOCOL,
        none: METHOD_EMIT_NONE
      },
      LOCAL: LOCAL,
      filterWorkflows: filterWorkflows,
      findWorkflow: findWorkflow,
      pickDefaultWorkflow: pickDefaultWorkflow,
      schemaFor: schemaFor,
      fieldOptions: fieldOptions,
      fieldControl: fieldControl,
      isExtendedSchema: isExtendedSchema,
      methodSupport: methodSupport,
      validateMethodSchema: validateMethodSchema,
      parseScanAtoms: parseScanAtoms,
      projectInputAdapter: projectInputAdapter,
      projectMethod: projectMethod,
      buildWorkflowRequest: buildWorkflowRequest,
      buildPesProtocol: buildPesProtocol,
      jsonObject: jsonObject,
      structureFromAsset: structureFromAsset,
      structureFromResolve: structureFromResolve,
      structureSummaryText: structureSummaryText,
      describeError: describeError,
      parseFormula: parseFormula,
      electronCount: electronCount,
      validateElectronic: validateElectronic,
      normalizeMemory: normalizeMemory,
      validateResources: validateResources,
      projectResourceDefaults: projectResourceDefaults,
      projectQueues: projectQueues,
      buildPreflightRequest: buildPreflightRequest,
      preflightLevels: preflightLevels,
      planIdentity: planIdentity,
      parsePreflightError: parsePreflightError,
      buildSubmitBody: buildSubmitBody,
      normalizeReceipt: normalizeReceipt,
      mapSubmitError: mapSubmitError,
      submitCheck: submitCheck,
      newIdempotencyKey: newIdempotencyKey,
      stableStringify: stableStringify,
      /* A-09 draft seams */
      DRAFT_KIND: DRAFT_KIND,
      DRAFT_VERSION: DRAFT_VERSION,
      DRAFT_DEBOUNCE_MS: DRAFT_DEBOUNCE_MS,
      draftPath: draftPath,
      draftWhen: draftWhen,
      draftSnapshot: draftSnapshot,
      normalizeDraftSnapshot: normalizeDraftSnapshot,
      draftBody: draftBody,
      mapDraftError: mapDraftError
    }
  };
})();

