/**
 * M3-03: cross-end D06 `plan_definition_digest` vectors.
 *
 * The platform implementation is
 * `acp_services.runtime.canonical_digest.proposal_plan_definition_digest`; the
 * RPH implementation is `computePlanDefinitionDigest` in `@rph/research`.  Both
 * consume the *same* fixture and must reproduce the stored expected digest
 * byte-for-byte.  The Python counterpart is
 * `tests/test_acp2_plan_digest_vectors.py`.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { computePlanDefinitionDigest } from "@rph/research";

import { TEST_OPTIONS } from "./harness.js";

type Vector = {
  readonly name: string;
  readonly digest: string;
  readonly proposal: Parameters<typeof computePlanDefinitionDigest>[0];
};

const VECTORS = new URL("../fixtures/plan-definition-digest-vectors.v1.json", import.meta.url);

function loadVectors(): { readonly vectors: readonly Vector[] } {
  const payload = JSON.parse(readFileSync(VECTORS, "utf8")) as {
    readonly schema?: string;
    readonly vectors?: readonly Vector[];
  };
  assert.equal(payload.schema, "acp2.test.plan-definition-digest-vectors.v1");
  assert.ok(Array.isArray(payload.vectors) && payload.vectors.length >= 6);
  return { vectors: payload.vectors };
}

test(
  "plan definition: RPH reproduces every cross-end platform vector",
  TEST_OPTIONS,
  () => {
    for (const vector of loadVectors().vectors) {
      assert.equal(
        computePlanDefinitionDigest(vector.proposal),
        vector.digest,
        vector.name,
      );
    }
  },
);

test(
  "plan definition: charged/open-shell, non-default method and multi-input are covered",
  TEST_OPTIONS,
  () => {
    const byName = new Map(loadVectors().vectors.map((vector) => [vector.name, vector]));
    const charged = byName.get("charged-doublet-nondefault-method");
    assert.ok(charged);
    assert.equal(charged.proposal.science.charge, 1);
    assert.equal(charged.proposal.science.multiplicity, 2);
    assert.equal(charged.proposal.science.method, "wB97X-D4");
    assert.equal(charged.proposal.science.mapping, "atom-map-2");

    const multi = byName.get("multi-input-ts");
    assert.ok(multi);
    assert.deepEqual(
      multi.proposal.science.initial_inputs.map((item) => item.role),
      ["reactant_complex", "transition_state"],
    );

    // D06: different input bytes or a different budget are a different identity.
    const baseline = byName.get("singlet-neutral")?.digest;
    assert.ok(baseline);
    assert.notEqual(byName.get("changed-input-bytes")?.digest, baseline);
    assert.notEqual(byName.get("changed-budget")?.digest, baseline);
  },
);
