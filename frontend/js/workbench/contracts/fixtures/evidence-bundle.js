/*
 * ACP Workbench — frozen EvidenceReport/ViewModel fixture bundle (T-31 / C21).
 *
 * Owner: frontend/js/workbench/contracts/** (OpenCode). Version: v20261023.1
 *
 * One frozen, read-only projection bundle consumed by
 * `workbench/features/scientific-evidence.js` (module tests + the
 * evidence-browser-live fixture acceptance). Nothing here is computed at
 * runtime: every value is a literal copied from the frozen A products
 * (science/evidence.schema.json EvidenceReport, science/provenance.schema.json
 * ScientificProvenance) and the frozen C view-model.schema.json projection.
 *
 * Honesty pins (plan C21 failure assertions):
 *   - `execution.terminal_state = "completed"` while `claim_mechanism_pathway`
 *     is `inconclusive` and `claim_free_energy_barrier` is `not_evaluated`
 *     (execution COMPLETED is never mechanism-correct);
 *   - the single imaginary mode claim is `supported` ONLY for "exactly one
 *     imaginary mode" — IRC connectivity is a separate `refuted` claim
 *     (a single imaginary frequency is never IRC connectivity);
 *   - `claim_mechanism_pathway` stays `inconclusive` so the UI must show the
 *     visible "live not verified" state (never green success styling).
 *
 * This file assigns `window.ACP_EVIDENCE_BUNDLE_FIXTURE` and performs no
 * network access, no DOM access and no computation.
 */
window.ACP_EVIDENCE_BUNDLE_FIXTURE = (function () {
  "use strict";

  var ZERO64 = "0000000000000000000000000000000000000000000000000000000000000000";

  function digest(hex) {
    return { algorithm: "sha256", value: hex };
  }

  /* 64-hex digests (content-frozen literals; not computed here). */
  var D = {
    freq: "1f0a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8",
    irc_fwd: "2a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f9",
    irc_rev: "3b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a",
    ts_xyz: "4c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b",
    sp_log: "5d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c",
    prov: "6e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d",
  };

  var CLAIM_TS_MODE = {
    claim_id: "claim_ts_mode",
    statement: "过渡态结构在该方法下恰有一个虚频（n_imaginary = 1）",
    status: "supported",
    scope: {
      subject: "imaginary_mode_count",
      applicability: "condition_specific",
      conditions: "r2SCAN-3c/def2-mTZVP, 298.15 K",
      not_in_scope: ["IRC 连通性", "多步机理"],
    },
    source_refs: [
      {
        artifact_path: "work/freq_orca/freq_output.log",
        digest: digest(D.freq),
        quantity: "n_imaginary",
        role: "native_output",
        units: null,
      },
      {
        artifact_path: "work/freq_orca/ts_optimized.xyz",
        digest: digest(D.ts_xyz),
        quantity: null,
        role: "standardized_product",
        units: "angstrom",
      },
    ],
    missing_checks: [],
    validator: {
      validator_id: "check.freq.imaginary_mode_count",
      validator_version: "1.2.0",
      kind: "deterministic",
      description: "统计频率输出中的虚频个数并断言 n_imaginary = 1",
    },
    limitations: ["lim_sampling_coverage"],
  };

  var CLAIM_IRC_CONNECTIVITY = {
    claim_id: "claim_irc_connectivity",
    statement: "双向 IRC 将该过渡态分别连接到声明的反应物与产物（端点映射一致）",
    status: "refuted",
    scope: {
      subject: "irc_endpoint_mapping",
      applicability: "system_specific",
      conditions: "r2SCAN-3c/def2-mTZVP, mass-weighted IRC",
      not_in_scope: ["溶液相路径"],
    },
    source_refs: [
      {
        artifact_path: "work/irc/irc_forward.trj",
        digest: digest(D.irc_fwd),
        quantity: null,
        role: "irc_forward",
        units: "angstrom",
      },
      {
        artifact_path: "work/irc/irc_reverse.trj",
        digest: digest(D.irc_rev),
        quantity: null,
        role: "irc_reverse",
        units: "angstrom",
      },
    ],
    missing_checks: [
      {
        check_id: "irc_not_bidirectional",
        reason: "反向端点映射到副产物异构体，未与声明产物一致",
        blocking: true,
      },
    ],
    validator: {
      validator_id: "check.irc.endpoint_mapping",
      validator_version: "2.0.1",
      kind: "deterministic",
      description: "比较 IRC 双向端点几何与声明的反应物/产物原子映射",
    },
    limitations: ["lim_numeric_tolerance"],
  };

  var CLAIM_MECHANISM_PATHWAY = {
    claim_id: "claim_mechanism_pathway",
    statement: "该路径构成基元步骤机理（TS 连通 + 全局能量可比）",
    status: "inconclusive",
    scope: {
      subject: "elementary_step_mechanism",
      applicability: "system_specific",
      conditions: null,
      not_in_scope: ["多步机理", "催化循环"],
    },
    source_refs: [
      {
        artifact_path: "work/sp_orca/sp_output.log",
        digest: digest(D.sp_log),
        quantity: "electronic_energy",
        role: "native_output",
        units: "hartree",
      },
    ],
    missing_checks: [
      {
        check_id: "missing_irc",
        reason: "IRC 端点映射未通过，连通性证据不完整",
        blocking: true,
      },
      {
        check_id: "standard_state_unspecified",
        reason: "标准态未声明，能量可比性未定",
        blocking: false,
      },
    ],
    validator: {
      validator_id: "check.mechanism.elementary_step",
      validator_version: "0.9.0",
      kind: "deterministic",
      description: "机理判据合成检查；证据不足时不升级状态",
    },
    limitations: ["lim_sampling_coverage", "lim_numeric_tolerance"],
  };

  var CLAIM_FREE_ENERGY_BARRIER = {
    claim_id: "claim_free_energy_barrier",
    statement: "该步骤的自由能垒 ΔG‡ 已按 298.15 K / 1 bar 完整评估",
    status: "not_evaluated",
    scope: {
      subject: "free_energy_barrier",
      applicability: "condition_specific",
      conditions: "298.15 K, 1 bar（尚未满足）",
      not_in_scope: ["电子能垒以外的推断"],
    },
    source_refs: [],
    missing_checks: [
      {
        check_id: "frequency_not_computed",
        reason: "产物侧频率计算缺失，热化学不完整",
        blocking: true,
      },
      {
        check_id: "temperature_unspecified",
        reason: "参考温度未在输入中声明",
        blocking: false,
      },
    ],
    validator: null,
    limitations: [],
  };

  var view = {
    snapshot_revision: 4,
    project_id: "fixture-project",
    task_ref: { job_id: "fixture_da_0001", attempt: 1, task_id: "task_fixture_da_0001" },
    manifest_generation: 2,
    artifacts: [
      {
        artifact_id: "fixture_da_0001/work/freq_orca/freq_output.log",
        role: "native_output",
        relative_path: "work/freq_orca/freq_output.log",
        availability: "readable",
        generation: 2,
        sha256: D.freq,
        size_bytes: 184320,
        reason: null,
      },
      {
        artifact_id: "fixture_da_0001/work/irc/irc_forward.trj",
        role: "irc_forward",
        relative_path: "work/irc/irc_forward.trj",
        availability: "readable",
        generation: 2,
        sha256: D.irc_fwd,
        size_bytes: 20480,
        reason: null,
      },
      {
        artifact_id: "fixture_da_0001/work/irc/irc_reverse.trj",
        role: "irc_reverse",
        relative_path: "work/irc/irc_reverse.trj",
        availability: "failed",
        generation: 2,
        sha256: null,
        size_bytes: null,
        reason: "拉取中断：远端文件校验失败",
      },
      {
        artifact_id: "fixture_da_0001/work/thermo/ensemble_thermo.json",
        role: "standardized_product",
        relative_path: "work/thermo/ensemble_thermo.json",
        availability: "preparing",
        generation: 2,
        sha256: null,
        size_bytes: null,
        reason: null,
      },
      {
        artifact_id: "fixture_da_0001/RESULT/sp_energy.json",
        role: "standardized_product",
        relative_path: "RESULT/sp_energy.json",
        availability: "unprepared",
        generation: 1,
        sha256: null,
        size_bytes: null,
        reason: null,
      },
    ],
    claims: [
      {
        claim_id: CLAIM_TS_MODE.claim_id,
        statement: CLAIM_TS_MODE.statement,
        status: CLAIM_TS_MODE.status,
        evidence_refs: ["work/freq_orca/freq_output.log", "work/freq_orca/ts_optimized.xyz"],
        reason: "validator check.freq.imaginary_mode_count@1.2.0 通过",
      },
      {
        claim_id: CLAIM_IRC_CONNECTIVITY.claim_id,
        statement: CLAIM_IRC_CONNECTIVITY.statement,
        status: CLAIM_IRC_CONNECTIVITY.status,
        evidence_refs: ["work/irc/irc_forward.trj", "work/irc/irc_reverse.trj"],
        reason: "反向端点映射不一致",
      },
      {
        claim_id: CLAIM_MECHANISM_PATHWAY.claim_id,
        statement: CLAIM_MECHANISM_PATHWAY.statement,
        status: CLAIM_MECHANISM_PATHWAY.status,
        evidence_refs: ["work/sp_orca/sp_output.log"],
        reason: "执行完成但连通性证据不足；live not verified",
      },
      {
        claim_id: CLAIM_FREE_ENERGY_BARRIER.claim_id,
        statement: CLAIM_FREE_ENERGY_BARRIER.statement,
        status: CLAIM_FREE_ENERGY_BARRIER.status,
        evidence_refs: [],
        reason: "缺少产物侧频率与温度声明",
      },
    ],
    clarifications: [],
    provenance: {
      source: "fixture:evidence-browser-live",
      producer_revision: "0000000000000000000000000000000000000000",
      produced_at: "2026-10-23T00:00:00Z",
      digest: "sha256:" + ZERO64,
    },
    read_only: true,
    not_authoritative_for_science: true,
  };

  var evidence_report = {
    schema_version: 1,
    report_id: "evr-fixture-da-0001",
    subject: {
      subject_id: "res-fixture-da-0001",
      subject_kind: "research_record",
      description: "丁二烯 + 乙烯 DA 单步反应（冻结 fixture）",
      science_identity: null,
      input_digests: [digest(D.ts_xyz), digest(D.sp_log)],
    },
    execution: {
      terminal_state: "completed",
      argv: ["orca", "ts_freq.inp"],
      exit_code: 0,
      started_at: "2026-10-22T18:00:00Z",
      finished_at: "2026-10-22T18:42:00Z",
      stdout_path: "work/sp_orca/sp_output.log",
      stderr_path: null,
    },
    claims: [CLAIM_TS_MODE, CLAIM_IRC_CONNECTIVITY, CLAIM_MECHANISM_PATHWAY, CLAIM_FREE_ENERGY_BARRIER],
    limitations: [
      {
        limitation_id: "lim_sampling_coverage",
        description: "构象/采样覆盖不足：仅评估 3 个候选，未达饱和判据",
        affected_claim_ids: ["claim_ts_mode", "claim_mechanism_pathway"],
      },
      {
        limitation_id: "lim_numeric_tolerance",
        description: "端点映射比较使用 0.05 Å 几何容差",
        affected_claim_ids: ["claim_irc_connectivity"],
      },
    ],
    provenance_digest: digest(D.prov),
  };

  var provenance = {
    schema_version: 1,
    provenance_id: "prov-fixture-da-0001",
    result_kind: "electronic_structure",
    science_identity: null,
    input: {
      input_digests: [digest(D.ts_xyz)],
      geometry_digest: digest(D.ts_xyz),
    },
    method: {
      method_id: "r2SCAN-3c",
      method_family: "composite",
      dispersion: "D4",
      hamiltonian: null,
    },
    basis: {
      basis_id: "def2-mTZVP",
      basis_family: "def2",
      auxiliary_basis_id: "def2/J",
    },
    software: {
      software_id: "orca",
      software_version: "6.0.1",
      distribution: null,
      executable_path: null,
    },
    conditions: {
      temperature_kelvin: 298.15,
      standard_state: "1 bar",
      reference_state: "ideal_gas",
      solvent: null,
      phase: null,
      pressure_pascal: 100000,
    },
    derivation: {
      kind: "electronic_structure",
      method_ref: "prov-fixture-da-0001",
    },
    limitations: ["lim_sampling_coverage", "lim_numeric_tolerance"],
  };

  var rph_decision = {
    source: "research_record:res-fixture-da-0001@record_revision=3",
    research_id: "res-fixture-da-0001",
    round: 2,
    decision: "revise",
    record_revision: 3,
    approval_ref: "apr-fixture-da-0001",
    task_ref: { job_id: "fixture_da_0001", attempt: 1 },
  };

  /* Frozen view projections for the standard ACV renderers
   * (visualization/energy_chart_renderer.js S0 node/series contract). */
  var projections = {
    energy: {
      view_type: "energy",
      metadata: { default_x_axis_mode: "reaction_coordinate", label: "S0 相对能量" },
      nodes: [
        { id: "n_r", frame_index: 1, energy: 0.0, status: "ok", scanned_coordinate: 0.0 },
        { id: "n_ts", frame_index: 2, energy: 24.6, status: "ok", scanned_coordinate: 1.0 },
        { id: "n_p", frame_index: 3, energy: -12.3, status: "failed", scanned_coordinate: 2.0 },
      ],
      series: [
        {
          id: "relative_energy",
          label: "相对能量",
          unit: "kcal/mol",
          values: [0.0, 24.6, -12.3],
          x_values: [0.0, 1.0, 2.0],
        },
      ],
      annotations: [{ id: "ann_ts_2", type: "ts", frame_index: 2, y: 24.6 }],
    },
    irc: {
      view_type: "irc",
      metadata: { label: "IRC 路径（正向/反向）" },
      nodes: [
        { id: "irc_fwd_end", frame_index: 1, energy: -1.2, status: "ok", scanned_coordinate: -3.0 },
        { id: "irc_ts", frame_index: 5, energy: 24.6, status: "ok", scanned_coordinate: 0.0 },
        { id: "irc_rev_end", frame_index: 9, energy: -0.4, status: "ok", scanned_coordinate: 3.0 },
      ],
      series: [
        {
          id: "irc_profile",
          label: "IRC 能量剖面",
          unit: "kcal/mol",
          values: [-1.2, 24.6, -0.4],
          x_values: [-3.0, 0.0, 3.0],
        },
      ],
      annotations: [],
      structures: [
        {
          node_id: "irc_fwd_end",
          xyz: "3\nirc forward endpoint\nC  0.000000  0.000000  0.000000\nC  1.340000  0.000000  0.000000\nH  1.700000  0.940000  0.000000",
        },
        {
          node_id: "irc_rev_end",
          xyz: "3\nirc reverse endpoint\nC  0.000000  0.000000  0.000000\nC  1.420000  0.000000  0.000000\nH  1.800000 -0.900000  0.000000",
        },
      ],
    },
    vibration: {
      view_type: "vibration",
      metadata: { label: "虚频模式 #7（-412.3 cm-1）" },
      mode_index: 7,
      frequency_cm1: -412.3,
      imaginary: true,
      atoms: [
        { elem: "C", x: 0.0, y: 0.0, z: 0.0 },
        { elem: "C", x: 1.4, y: 0.0, z: 0.0 },
        { elem: "H", x: 1.8, y: 0.9, z: 0.0 },
      ],
      displacement: [
        [0.01, 0.0, 0.0],
        [-0.012, 0.004, 0.0],
        [0.002, -0.01, 0.0],
      ],
      /* Single imaginary mode ≠ IRC connectivity: the claim stays refuted
       * (claim_irc_connectivity); this projection only visualizes the mode. */
      connectivity_note: "单虚频不代表 IRC 连通；连通性以 claim_irc_connectivity 为准",
    },
  };

  return {
    fixture: true,
    bundle_id: "evidence-bundle-fixture-v1",
    view: view,
    evidence_report: evidence_report,
    provenance: provenance,
    rph_decision: rph_decision,
    projections: projections,
  };
})();
