import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ResearchMemory,
  assertEvidenceReport,
  isEvidenceReport,
  memoryEntryFromRecord,
  memoryHumanEdit,
  validateEvidenceReport,
  validateMemoryEntry,
  type EvidenceReport,
  type MemoryHumanEdit,
  type MemoryPrincipal,
  type Proposal,
  type ResearchRecord,
} from "@rph/research";

import { TEST_OPTIONS } from "./harness.js";

const PROJECT = "proj-diels-alder";
const OTHER_PROJECT = "proj-other";
const TENANT = "tenant-local";
const DIGEST = `sha256:${"a".repeat(64)}`;
const NOW = "2026-10-23T00:00:00Z";

function principal(overrides: Partial<MemoryPrincipal> = {}): MemoryPrincipal {
  return {
    principal_id: "principal-alice",
    kind: "user",
    tenant_id: TENANT,
    project_ids: [PROJECT],
    actions: ["read"],
    ...overrides,
  };
}

function baseRecord(
  project: string = PROJECT,
  revision = 1,
  overrides: Partial<ResearchRecord> = {},
): ResearchRecord {
  return {
    research_id: "res-diels-alder",
    project_id: project,
    round: revision - 1,
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
    record_revision: revision,
    ...overrides,
  };
}

function proposal(project: string = PROJECT): Proposal {
  return {
    proposal_id: "prop-diels-alder-r0",
    project_id: project,
    question_ref: "question-diels-alder",
    question_revision: 1,
    reaction_ref: "reaction-diels-alder",
    reaction_revision: 1,
    hypothesis: "The endo pathway is preferred.",
    recipe_digest: DIGEST,
    plan_definition_digest: DIGEST,
    science: {
      capability_steps: [],
      initial_inputs: [],
      method: "r2SCAN-3c",
      mapping: "atom-map-1",
      charge: 0,
      multiplicity: 1,
      allowed_substitutions: [],
    },
    budget: {
      model_requests: 1,
      input_tokens: 1000,
      output_tokens: 500,
      task_refs: 1,
      recipe_steps: 1,
      qc_invocations: 1,
      wall_seconds: 600,
      queue_seconds: 60,
    },
    approval_ref: null,
    allowed_change_scope: { mode: "none", finite_substitutions: [] },
    round: 0,
    decision: "proposed",
    created_at: NOW,
    human_edits: [],
  };
}

function cleanReport(): EvidenceReport {
  return {
    report_id: "rep-diels-alder",
    project_id: PROJECT,
    claims: [
      {
        claim_id: "claim-ts-order",
        status: "inconclusive",
        validator_id: "validate.stationary_point",
        validator_version: "1.0.0",
        source_refs: ["result/manifest.json"],
        evidence: [
          { source_kind: "result_manifest", sha256: DIGEST, ref: "result/manifest.json" },
        ],
      },
    ],
  };
}

test(
  "memory: same-project retrieval hits authorized records",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    memory.indexRecord(baseRecord(PROJECT, 1));

    const result = memory.retrieve({
      principal: principal(),
      project_id: PROJECT,
      now: NOW,
    });

    assert.equal(result.allowed, true);
    assert.equal(result.hits.length, 1);
    assert.equal(result.hits[0]?.project_id, PROJECT);
    assert.equal(result.hits[0]?.source, "research_record");
    assert.equal(result.audit.action, "retrieve");
    assert.equal(result.audit.allowed, true);
    assert.equal(result.audit.hits, 1);
  },
);

test(
  "memory: cross-project query returns zero hits and a denied audit entry",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    memory.indexRecord(baseRecord(PROJECT, 1));
    memory.indexRecord(baseRecord(OTHER_PROJECT, 1));

    const result = memory.retrieve({
      principal: principal(),
      project_id: OTHER_PROJECT,
      now: NOW,
    });

    assert.equal(result.allowed, false);
    assert.equal(result.hits.length, 0);
    assert.equal(result.audit.action, "denied");
    assert.equal(result.audit.allowed, false);
    assert.equal(result.audit.reason, "scope_mismatch");
    assert.equal(result.audit.hits, 0);
    assert.equal(
      result.hits.some((entry) => entry.project_id === PROJECT),
      false,
    );
    assert.equal(
      memory
        .auditEntries()
        .some((entry) => entry.action === "denied" && entry.project_id === OTHER_PROJECT),
      true,
    );

    const tenantMismatch = memory.retrieve({
      principal: principal(),
      project_id: PROJECT,
      tenant_id: "tenant-other",
      now: NOW,
    });
    assert.equal(tenantMismatch.allowed, false);
    assert.equal(tenantMismatch.audit.reason, "tenant_mismatch");
  },
);

test(
  "memory: revision filter excludes stale revisions",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    memory.indexRecord(baseRecord(PROJECT, 1));
    memory.indexRecord(baseRecord(PROJECT, 2));

    const exact = memory.retrieve({
      principal: principal(),
      project_id: PROJECT,
      revision: 2,
      now: NOW,
    });
    assert.equal(exact.hits.length, 1);
    assert.equal(exact.hits[0]?.revision, 2);

    const stale = memory.retrieve({
      principal: principal(),
      project_id: PROJECT,
      revision: 1,
      now: NOW,
    });
    assert.equal(stale.hits.length, 1);
    assert.equal(stale.hits[0]?.revision, 1);

    const floor = memory.retrieve({
      principal: principal(),
      project_id: PROJECT,
      min_revision: 2,
      now: NOW,
    });
    assert.equal(floor.hits.length, 1);
    assert.equal(floor.hits[0]?.revision, 2);
    assert.equal(floor.audit.before, 2);
    assert.equal(floor.audit.after, 2);
  },
);

test(
  "memory: audit entry keeps actor, revision, before and after",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    memory.indexRecord(baseRecord(PROJECT, 1));

    const result = memory.retrieve({
      principal: principal(),
      project_id: PROJECT,
      min_revision: 1,
      now: NOW,
    });

    assert.equal(result.audit.actor, "principal-alice");
    assert.equal(Object.hasOwn(result.audit, "revision"), true);
    assert.equal(Object.hasOwn(result.audit, "before"), true);
    assert.equal(Object.hasOwn(result.audit, "after"), true);
    assert.equal(result.audit.before, 1);
    assert.equal(result.audit.after, 1);

    const edit: MemoryHumanEdit = memoryHumanEdit({
      actor: "principal-alice",
      revision: 2,
      field_path: "science.method",
      before: "r2SCAN-3c",
      after: "wB97X-D4",
      edited_at: NOW,
    });
    const entry = memory.indexRecord(baseRecord(PROJECT, 2), [edit]);
    assert.equal(entry.human_edits.length, 1);
    assert.deepEqual(entry.human_edits[0], edit);
    assert.equal(entry.human_edits[0]?.actor, "principal-alice");
    assert.equal(entry.human_edits[0]?.revision, 2);
    assert.equal(entry.human_edits[0]?.before, "r2SCAN-3c");
    assert.equal(entry.human_edits[0]?.after, "wB97X-D4");
  },
);

test(
  "memory: a model assumption cannot become evidence",
  TEST_OPTIONS,
  () => {
    const clean = cleanReport();
    assert.deepEqual(validateEvidenceReport(clean), []);
    assert.equal(isEvidenceReport(clean), true);
    assert.equal(assertEvidenceReport(clean).report_id, clean.report_id);

    const modelSource = {
      ...clean,
      claims: [
        {
          ...clean.claims[0]!,
          evidence: [
            { source_kind: "model_assumption", ref: "the model believes the TS is valid" },
          ],
        },
      ],
    };
    assert.equal(
      validateEvidenceReport(modelSource).some(
        (item) => item.code === "model_assumption_not_evidence",
      ),
      true,
    );
    assert.equal(isEvidenceReport(modelSource), false);
    assert.throws(() => assertEvidenceReport(modelSource), /model_assumption_not_evidence/);

    const assumptionKey = {
      ...clean,
      claims: [
        {
          ...clean.claims[0]!,
          evidence: [
            { source_kind: "result_manifest", assumption: "model belief" },
          ],
        },
      ],
    };
    assert.equal(
      validateEvidenceReport(assumptionKey).some(
        (item) => item.code === "model_assumption_not_evidence",
      ),
      true,
    );
  },
);

test(
  "memory: prompt and chain-of-thought archives are rejected",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    const entry = memoryEntryFromRecord(baseRecord(PROJECT, 1));
    assert.deepEqual(validateMemoryEntry(entry), []);

    const withCot = { ...entry, chain_of_thought: "step 1: reason about orbitals" };
    assert.equal(
      validateMemoryEntry(withCot).some(
        (item) => item.code === "prompt_archive_forbidden",
      ),
      true,
    );
    assert.throws(
      () => memory.indexEntry(withCot as unknown as typeof entry),
      /prompt_archive_forbidden/,
    );

    const withPrompt = { ...entry, prompt: "you are a helpful chemist" };
    assert.equal(
      validateMemoryEntry(withPrompt).some(
        (item) => item.code === "prompt_archive_forbidden",
      ),
      true,
    );

    const reportWithPrompt = { ...cleanReport(), prompt: "raw system prompt" };
    assert.equal(
      validateEvidenceReport(reportWithPrompt).some(
        (item) => item.code === "prompt_archive_forbidden",
      ),
      true,
    );
  },
);

test(
  "memory: anonymous or write-only principals cannot read memory",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    memory.indexRecord(baseRecord(PROJECT, 1));

    const anonymous = memory.retrieve({
      principal: principal({ kind: "anonymous", principal_id: "anon" }),
      project_id: PROJECT,
      now: NOW,
    });
    assert.equal(anonymous.allowed, false);
    assert.equal(anonymous.hits.length, 0);
    assert.equal(anonymous.audit.reason, "no_grant");

    const writeOnly = memory.retrieve({
      principal: principal({ actions: ["write"] }),
      project_id: PROJECT,
      now: NOW,
    });
    assert.equal(writeOnly.allowed, false);
    assert.equal(writeOnly.audit.reason, "action_not_granted");
  },
);

test(
  "memory: entries from another project never leak into a project query",
  TEST_OPTIONS,
  () => {
    const memory = new ResearchMemory();
    memory.indexRecord(baseRecord(PROJECT, 1));
    memory.indexRecord(baseRecord(OTHER_PROJECT, 1));
    memory.indexProposal(proposal(PROJECT), 1);

    const multiProject = principal({ project_ids: [PROJECT, OTHER_PROJECT] });
    const result = memory.retrieve({
      principal: multiProject,
      project_id: PROJECT,
      now: NOW,
    });
    assert.equal(result.hits.length, 2);
    assert.equal(
      result.hits.every((entry) => entry.project_id === PROJECT),
      true,
    );
    assert.equal(
      result.hits.some((entry) => entry.project_id === OTHER_PROJECT),
      false,
    );

    const proposalsOnly = memory.retrieve({
      principal: multiProject,
      project_id: PROJECT,
      source: "proposal",
      now: NOW,
    });
    assert.equal(proposalsOnly.hits.length, 1);
    assert.equal(proposalsOnly.hits[0]?.source, "proposal");
    assert.equal(proposalsOnly.hits[0]?.kind, "proposal");
  },
);
