"use strict";
/* V7 parity gate (machine-checkable).
 *
 * 24 faces from the plan §2.4. 23 are implemented and have a focused Node vm
 * test; P17 is ADR-retired for its plan-confirm action only (ADR-009), while
 * its read-only modules/tests remain. The test fails when a face claims
 * implementation without a test, when the ADR disappears, or when the matrix
 * silently loses a face. */
const fs = require("fs");
const path = require("path");

module.exports.register = async function (h) {
  const { test, assert, assertEq } = h;
  const projectRoot = path.resolve(__dirname, "../../..");
  const frontend = path.join(projectRoot, "frontend");
  const adr = path.join(projectRoot, "docs/reference/next-change/adr/ADR-009-mechanism-plan-retirement.md");

  const FACES = [
    { id: "P01", status: "implemented", tests: ["acp2_task_wizard.test.js"], modules: ["task-wizard"] },
    { id: "P02", status: "implemented", tests: ["acp2_task_wizard.test.js"], modules: ["task-wizard"] },
    { id: "P03", status: "implemented", tests: ["acp2_job_edit.test.js"], modules: ["job-edit"] },
    { id: "P04", status: "implemented", tests: ["acp2_module_task_config.test.js"], modules: ["task-config"] },
    { id: "P05", status: "implemented", tests: ["acp2_tsmode_editor.test.js"], modules: ["tsmode-editor"] },
    { id: "P06", status: "implemented", tests: ["acp2_module_task_explorer.test.js"], modules: ["task-explorer"] },
    { id: "P07", status: "implemented", tests: ["acp2_module_task_explorer.test.js"], modules: ["task-explorer"] },
    { id: "P08", status: "implemented", tests: ["acp2_module_exec_detail.test.js"], modules: ["exec-detail"] },
    { id: "P09", status: "implemented", tests: ["acp2_module_exec_detail.test.js"], modules: ["exec-detail"] },
    { id: "P10", status: "implemented", tests: ["acp2_node_status.test.js"], modules: ["node-status"] },
    { id: "P11", status: "implemented", tests: ["acp2_module_viewer.test.js"], modules: ["molecule-viewer"] },
    { id: "P12", status: "implemented", tests: ["acp2_energy_profile.test.js"], modules: ["energy-profile"] },
    { id: "P13", status: "implemented", tests: ["acp2_module_viewer.test.js"], modules: ["molecule-viewer"] },
    { id: "P14", status: "implemented", tests: ["acp2_module_viewer.test.js"], modules: ["molecule-viewer"] },
    { id: "P15", status: "implemented", tests: ["acp2_energy_profile.test.js"], modules: ["energy-profile"] },
    { id: "P16", status: "implemented", tests: ["acp2_s2_scan.test.js"], modules: ["s2-scan"] },
    { id: "P17", status: "adr-retired", tests: ["acp2_mechanism_plan.test.js", "acp2_path_view.test.js", "acp2_pes_handoff.test.js"], modules: ["mechanism-plan", "path-view", "pes-handoff"] },
    { id: "P18", status: "implemented", tests: ["acp2_nmr_report.test.js"], modules: ["nmr-report"] },
    { id: "P19", status: "implemented", tests: ["acp2_research_record.test.js"], modules: ["research-record"] },
    { id: "P20", status: "implemented", tests: ["acp2_research_record.test.js"], modules: ["research-record"] },
    { id: "P21", status: "implemented", tests: ["acp2_research_record.test.js"], modules: ["research-record"] },
    { id: "P22", status: "implemented", tests: ["acp2_approval_card.test.js"], modules: ["approval-card"] },
    { id: "P23", status: "implemented", tests: ["acp2_capability_form_module.test.js"], modules: ["capability-form"] },
    { id: "P24", status: "implemented", tests: ["acp2_i18n_new_modules.test.js", "acp2_theme_narrow.test.js", "acp2_shell_a11y.test.js"], modules: [] },
  ];

  await test("V7 parity: 24/24 faces are accounted for (23 implemented + 1 ADR-retired)", function () {
    assertEq(FACES.length, 24);
    const implemented = FACES.filter((f) => f.status === "implemented").length;
    const retired = FACES.filter((f) => f.status === "adr-retired").length;
    assertEq(implemented, 23);
    assertEq(retired, 1);
    assertEq(FACES.find((f) => f.status === "adr-retired").id, "P17");
  });

  await test("V7 parity: every implemented face has its focused test and module", function () {
    FACES.filter((f) => f.status === "implemented").forEach(function (face) {
      face.tests.forEach(function (file) {
        const full = path.join(frontend, "js/module_tests", file);
        assert(fs.existsSync(full), face.id + " test " + file);
        const source = fs.readFileSync(full, "utf8");
        assert(source.length > 1000, face.id + " test is non-empty");
      });
      face.modules.forEach(function (id) {
        assert(fs.existsSync(path.join(frontend, "js/modules", id, "module.js")), face.id + " module " + id);
      });
    });
  });

  await test("V7 parity: P17 is retired by ADR-009, not marked implemented", function () {
    assert(fs.existsSync(adr), "ADR-009 exists");
    const text = fs.readFileSync(adr, "utf8");
    assert(text.indexOf("410") !== -1, "ADR records the backend 410");
    assert(text.indexOf("P17") !== -1, "ADR names P17");
    assert(text.indexOf("ADR-retired") !== -1);
    const p17 = FACES.find((f) => f.id === "P17");
    assertEq(p17.status, "adr-retired");
    p17.tests.forEach(function (file) {
      assert(fs.existsSync(path.join(frontend, "js/module_tests", file)), "P17 kept test " + file);
    });
  });
};
