/*
 * ACP Workbench contracts — BatchOptimize static INT/TS role defaults factory
 * (pure, no DOM/network). FE-06: single authoritative source shared by
 * wizard openMethodConfig (window batchRoleStaticDefaults / _BATCH_ROLE_DEFAULTS)
 * and ACPJobEditor (edit-draft batch_roles hydration) — previously duplicated
 * verbatim in wizard.js and js/job_editor.js.
 *
 * Style follows task_view_contract.js: init-guarded window.ACPWorkbench
 * namespace, no shared mutable state (every call returns a fresh object).
 *
 * Load order: must be evaluated BEFORE js/job_editor.js and
 * js/workbench/features/wizard.js (guarded by
 * tests/test_frontend_integration.py::test_default_page_loads_modules_in_dependency_order).
 */
window.ACPWorkbench = window.ACPWorkbench || {};
window.ACPWorkbench.contracts = window.ACPWorkbench.contracts || {};

// Static INT/TS role defaults, mirroring backend ROLE_DEFAULTS +
// BatchMethodOptions. Method-dependent keys (method/basis/sp_*/dispersion/
// aux_j_basis) are patched per-context by callers. Shared by openMethodConfig
// and ACPJobEditor (edit-draft batch_roles hydration). Values are pinned
// against backend ROLE_PRODUCT_DEFAULTS by
// tests/test_frontend_sync.py::test_batch_engine_defaults_match_backend —
// do not change any literal without the backend counterpart.
window.ACPWorkbench.contracts.batchRoleStaticDefaults = function batchRoleStaticDefaults() {
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
};
