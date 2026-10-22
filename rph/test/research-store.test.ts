import assert from "node:assert/strict";
import { test } from "node:test";

import {
  P1_TOOL_NAMES,
  P2_OPERATION_MAP,
} from "@rph/bridge";
import {
  RESEARCH_P2_OPERATIONS,
  RESEARCH_P2_TOOLS,
  ResearchStore,
  approvalBindingDigest,
  approvalBindingMatches,
  computePlanDefinitionDigest,
  freezeProposal,
  validateProposal,
  validateResearchRecord,
  type ApprovalBinding,
  type Proposal,
  type ResearchRecord,
} from "@rph/research";

import { FixturePlatformClient } from "./research-fixture-client.js";
import { TEST_OPTIONS } from "./harness.js";

const PROJECT = "proj-diels-alder";
const OTHER_PROJECT = "proj-other";
const DIGEST = `sha256:${"a".repeat(64)}`;
const OTHER_DIGEST = `sha256:${"b".repeat(64)}`;
const NOW = "2026-10-23T00:00:00Z";

type ProposalDraft = Omit<Proposal, "plan_definition_digest">;

function baseDraft(): ProposalDraft {
  return {
    proposal_id: "prop-diels-alder-r0",
    project_id: PROJECT,
    question_ref: "question-diels-alder",
    question_revision: 1,
    reaction_ref: "reaction-diels-alder",
    reaction_revision: 1,
    hypothesis: "The endo pathway is preferred under the frozen conditions.",
    recipe_digest: DIGEST,
    science: {
      capability_steps: [
        {
          step_index: 0,
          capability: "geometry_optimization",
          version: "1.0.0",
          parameters: { max_cycles: 100 },
        },
      ],
      initial_inputs: [
        { input_id: "diene", sha256: DIGEST, format: "xyz" },
      ],
      method: "r2SCAN-3c",
      mapping: "atom-map-1",
      charge: 0,
      multiplicity: 1,
      allowed_substitutions: [],
    },
    budget: {
      model_requests: 2,
      input_tokens: 1000,
      output_tokens: 500,
      task_refs: 1,
      recipe_steps: 1,
      qc_invocations: 1,
      wall_seconds: 600,
      queue_seconds: 60,
      unit_basis: "per_round",
      notes: "display metadata excluded from the digest",
    },
    approval_ref: "approval-1",
    allowed_change_scope: { mode: "none", finite_substitutions: [] },
    round: 0,
    decision: "proposed",
    created_at: NOW,
    human_edits: [],
  };
}

function makeProposal(draft: ProposalDraft = baseDraft()): Proposal {
  return freezeProposal(draft);
}

function baseRecord(
  project: string = PROJECT,
  round = 0,
  overrides: Partial<ResearchRecord> = {},
): ResearchRecord {
  return {
    research_id: "res-diels-alder",
    project_id: project,
    round,
    decision: "accept",
    plan_definition_digest: DIGEST,
    step_identity_refs: [
      {
        step_index: 0,
        step_identity: "step-0-opt",
        implementation_version: "release-1",
        upstream_digests: [DIGEST],
        materialized_parameters_digest: DIGEST,
      },
    ],
    task_refs: [
      {
        project_id: project,
        job_id: "20261023_001_Optimize",
        attempt: 1,
        release_id: "release-1",
      },
    ],
    evidence_refs: [
      {
        evidence_id: "ev-1",
        kind: "result_manifest",
        sha256: DIGEST,
        relative_path: "result/manifest.json",
      },
    ],
    missing_validation: [],
    human_edits: [],
    approval_ref: "approval-1",
    created_at: NOW,
    record_revision: 1,
    ...overrides,
  };
}

function build(): { store: ResearchStore; client: FixturePlatformClient } {
  const client = new FixturePlatformClient();
  return { store: new ResearchStore(client), client };
}

test(
  "record: creates a ResearchRecord through the P2 mapping and reads it back",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const record = baseRecord();
    const created = await store.createRecord(record);
    assert.equal(created.ok, true);
    if (!created.ok) return;
    assert.equal(created.value.created, true);
    assert.equal(created.value.ref.recordId, "res-diels-alder:round-0");
    assert.equal(created.value.ref.revision, 1);
    assert.equal(client.recordCount(), 1);

    const read = await store.readRecord(created.value.ref);
    assert.equal(read.ok, true);
    if (!read.ok) return;
    assert.deepEqual(read.value, record);
    assert.equal(
      client.calls[0]?.name,
      RESEARCH_P2_TOOLS["proposal.create"],
    );
    assert.equal(
      client.calls[1]?.name,
      RESEARCH_P2_TOOLS["proposal.read"],
    );
  },
);

test(
  "record: same round idempotency key does not create a second record",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const record = baseRecord();
    const first = await store.createRecord(record);
    const second = await store.createRecord(record);
    assert.equal(first.ok, true);
    assert.equal(second.ok, true);
    if (!first.ok || !second.ok) return;
    assert.equal(second.value.created, false);
    assert.deepEqual(second.value.ref, first.value.ref);
    assert.equal(client.recordCount(), 1);
    assert.equal(client.updateCasCallCount(), 0);
  },
);

test(
  "record: duplicate round with a different body returns duplicate_round",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const first = await store.createRecord(baseRecord());
    assert.equal(first.ok, true);
    const conflicting = baseRecord(PROJECT, 0, { decision: "revise" });
    const second = await store.createRecord(conflicting);
    assert.equal(second.ok, false);
    if (second.ok) return;
    assert.equal(second.error.code, "duplicate_round");
    assert.equal(client.recordCount(), 1);
  },
);

test(
  "record: concurrent rounds cannot overwrite; one gets revision_conflict",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const record = baseRecord();
    const created = await store.createRecord(record);
    assert.equal(created.ok, true);
    if (!created.ok) return;
    const ref = created.value.ref;
    const nextA: ResearchRecord = {
      ...record,
      decision: "accept",
      record_revision: 2,
    };
    const nextB: ResearchRecord = {
      ...record,
      decision: "revise",
      record_revision: 2,
    };
    const [left, right] = await Promise.all([
      store.compareAndSwap(ref, 1, nextA),
      store.compareAndSwap(ref, 1, nextB),
    ]);
    const successes = [left, right].filter((item) => item.ok);
    const conflicts = [left, right].filter(
      (item) => !item.ok && item.error.code === "revision_conflict",
    );
    assert.equal(successes.length, 1);
    assert.equal(conflicts.length, 1);
    assert.equal(client.updateCasCallCount(), 2);
    assert.equal(client.recordCount(), 1);
  },
);

test(
  "record: stale revision overwrite is rejected with stale_reference",
  TEST_OPTIONS,
  async () => {
    const { store } = build();
    const record = baseRecord();
    const created = await store.createRecord(record);
    assert.equal(created.ok, true);
    if (!created.ok) return;
    const promoted: ResearchRecord = { ...record, record_revision: 2 };
    const update = await store.compareAndSwap(created.value.ref, 1, promoted);
    assert.equal(update.ok, true);

    const stale = await store.compareAndSwap(created.value.ref, 1, {
      ...record,
      decision: "revise",
      record_revision: 2,
    });
    assert.equal(stale.ok, false);
    if (stale.ok) return;
    assert.equal(stale.error.code, "stale_reference");
    assert.equal(stale.error.params["latest_revision"], 2);
  },
);

test(
  "record: wrong project write is rejected with project_mismatch",
  TEST_OPTIONS,
  async () => {
    const { store } = build();
    const record = baseRecord();
    const created = await store.createRecord(record);
    assert.equal(created.ok, true);
    if (!created.ok) return;
    const wrongProject = {
      ...record,
      project_id: OTHER_PROJECT,
      task_refs: [
        {
          ...record.task_refs[0]!,
          project_id: OTHER_PROJECT,
        },
      ],
      record_revision: 2,
    } as ResearchRecord;
    const result = await store.compareAndSwap(
      created.value.ref,
      1,
      wrongProject,
    );
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.equal(result.error.code, "project_mismatch");
  },
);

test(
  "record: task_ref from another project is rejected with project_mismatch",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const crossProject = baseRecord(PROJECT, 0, {
      task_refs: [
        {
          project_id: OTHER_PROJECT,
          job_id: "20261023_999",
          attempt: 1,
          release_id: "release-1",
        },
      ],
    });
    const result = await store.createRecord(crossProject);
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.equal(result.error.code, "project_mismatch");
    assert.equal(client.recordCount(), 0);
  },
);

test(
  "proposal: create and read round-trip keeps revision and digest",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const proposal = makeProposal();
    const created = await store.createProposal(proposal);
    assert.equal(created.ok, true);
    if (!created.ok) return;
    assert.equal(created.value.revision, 1);
    assert.equal(created.value.created, true);
    assert.equal(created.value.proposal.plan_definition_digest,
      computePlanDefinitionDigest(proposal));
    assert.equal(client.proposalCount(), 1);

    const read = await store.readProposal(
      { projectId: PROJECT, proposalId: proposal.proposal_id },
      1,
    );
    assert.equal(read.ok, true);
    if (!read.ok) return;
    assert.deepEqual(read.value.proposal, proposal);
    assert.equal(read.value.revision, 1);
    assert.equal(
      client.calls.at(-1)?.name,
      RESEARCH_P2_TOOLS["proposal.read"],
    );
  },
);

test(
  "proposal: plan digest ignores approval_ref, approval_status and display_name",
  TEST_OPTIONS,
  async () => {
    const draft = baseDraft();
    const baseline = computePlanDefinitionDigest(draft);
    const presentation: ProposalDraft = {
      ...draft,
      approval_ref: "approval-2",
      approval_status: "approved",
      display_name: "Diels-Alder r0 (display only)",
      updated_at: "2026-10-23T01:00:00Z",
    };
    assert.equal(computePlanDefinitionDigest(presentation), baseline);

    const appended = {
      ...draft,
      task_refs: [{ job_id: "later", attempt: 1 }],
      evidence_refs: [{ evidence_id: "later" }],
    } as unknown as ProposalDraft;
    assert.equal(computePlanDefinitionDigest(appended), baseline);
  },
);

test(
  "proposal: plan digest changes on method or mapping",
  TEST_OPTIONS,
  async () => {
    const draft = baseDraft();
    const baseline = computePlanDefinitionDigest(draft);
    const method: ProposalDraft = {
      ...draft,
      science: { ...draft.science, method: "wB97X-D4" },
    };
    const mapping: ProposalDraft = {
      ...draft,
      science: { ...draft.science, mapping: "atom-map-2" },
    };
    assert.notEqual(computePlanDefinitionDigest(method), baseline);
    assert.notEqual(computePlanDefinitionDigest(mapping), baseline);
  },
);

test(
  "proposal: plan digest changes on initial input bytes or budget",
  TEST_OPTIONS,
  async () => {
    const draft = baseDraft();
    const baseline = computePlanDefinitionDigest(draft);
    const inputs: ProposalDraft = {
      ...draft,
      science: {
        ...draft.science,
        initial_inputs: [
          { ...draft.science.initial_inputs[0]!, sha256: OTHER_DIGEST },
        ],
      },
    };
    const budget: ProposalDraft = {
      ...draft,
      budget: { ...draft.budget, model_requests: 3, wall_seconds: 900 },
    };
    const charge: ProposalDraft = {
      ...draft,
      science: { ...draft.science, charge: 1, multiplicity: 2 },
    };
    assert.notEqual(computePlanDefinitionDigest(inputs), baseline);
    assert.notEqual(computePlanDefinitionDigest(budget), baseline);
    assert.notEqual(computePlanDefinitionDigest(charge), baseline);
  },
);

test(
  "proposal: approval binding rejects a different project, budget or scope",
  TEST_OPTIONS,
  async () => {
    const { store } = build();
    const proposal = makeProposal();
    const binding: ApprovalBinding = {
      approvalRef: "approval-1",
      planDefinitionDigest: proposal.plan_definition_digest,
      projectId: PROJECT,
      budget: proposal.budget,
      allowedChangeScope: proposal.allowed_change_scope,
    };
    assert.equal(approvalBindingMatches(proposal, binding), true);
    assert.equal(store.verifyApprovalBinding(proposal, binding).ok, true);
    assert.match(approvalBindingDigest(binding), /^sha256:[0-9a-f]{64}$/);

    const variants: ApprovalBinding[] = [
      { ...binding, projectId: OTHER_PROJECT },
      { ...binding, budget: { ...binding.budget, wall_seconds: 601 } },
      {
        ...binding,
        allowedChangeScope: {
          mode: "finite_list",
          finite_substitutions: [
            { field_path: "science.method", allowed_values: ["wB97X-D4"] },
          ],
        },
      },
      { ...binding, planDefinitionDigest: OTHER_DIGEST },
    ];
    for (const variant of variants) {
      assert.equal(approvalBindingMatches(proposal, variant), false);
      const result = store.verifyApprovalBinding(proposal, variant);
      assert.equal(result.ok, false);
      if (result.ok) continue;
      assert.equal(result.error.code, "approval_binding_mismatch");
    }
  },
);

test(
  "record: event idempotency key prevents a duplicate CAS write",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const record = baseRecord();
    const created = await store.createRecord(record);
    assert.equal(created.ok, true);
    if (!created.ok) return;
    const next: ResearchRecord = { ...record, record_revision: 2 };
    const first = await store.compareAndSwap(
      created.value.ref,
      1,
      next,
      { eventKey: "round-0:decision" },
    );
    assert.equal(first.ok, true);
    const replay = await store.compareAndSwap(
      created.value.ref,
      1,
      { ...record, decision: "revise", record_revision: 2 },
      { eventKey: "round-0:decision" },
    );
    assert.equal(replay.ok, true);
    assert.equal(client.updateCasCallCount(), 1);
    if (first.ok && replay.ok) {
      assert.deepEqual(replay.value, first.value);
    }
  },
);

test(
  "record: every call uses only the T-09 P2 allowlisted tools",
  TEST_OPTIONS,
  async () => {
    const allowed = new Set<string>([
      ...P1_TOOL_NAMES,
      ...Object.values(P2_OPERATION_MAP).map((mapping) => mapping.tool),
    ]);
    assert.equal(allowed.size, 18);
    for (const operation of RESEARCH_P2_OPERATIONS) {
      assert.equal(
        RESEARCH_P2_TOOLS[operation],
        P2_OPERATION_MAP[operation].tool,
        `bridge/research drift for ${operation}`,
      );
    }

    const { store, client } = build();
    const created = await store.createRecord(baseRecord());
    assert.equal(created.ok, true);
    if (!created.ok) return;
    await store.readRecord(created.value.ref);
    await store.compareAndSwap(created.value.ref, 1, {
      ...baseRecord(),
      record_revision: 2,
    });
    assert.ok(client.calls.length >= 3);
    for (const call of client.calls) {
      assert.equal(allowed.has(call.name), true, `unlisted tool ${call.name}`);
      assert.equal(
        Object.values(RESEARCH_P2_TOOLS).includes(call.name),
        true,
        `store used a non-research tool ${call.name}`,
      );
    }
  },
);

test(
  "record: chat/session fields cannot be record authority",
  TEST_OPTIONS,
  async () => {
    const malformed = { ...baseRecord(), session_id: "ses-1" };
    const issues = validateResearchRecord(malformed);
    assert.ok(issues.some((item) => item.code === "session_authority"));
    const { store, client } = build();
    const result = await store.createRecord(malformed as unknown as ResearchRecord);
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.equal(result.error.code, "invalid_record");
    assert.equal(client.calls.length, 0);
  },
);

test(
  "proposal: task state-machine fields are rejected",
  TEST_OPTIONS,
  async () => {
    const { store, client } = build();
    const malformed = { ...makeProposal(), job_id: "20261023_001" };
    const issues = validateProposal(malformed);
    assert.ok(issues.some((item) => item.code === "task_state_field"));
    const result = await store.createProposal(malformed as Proposal);
    assert.equal(result.ok, false);
    if (result.ok) return;
    assert.equal(result.error.code, "invalid_proposal");
    assert.equal(client.calls.length, 0);
  },
);
