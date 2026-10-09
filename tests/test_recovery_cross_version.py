"""Cross-version recovery contract: cache version / reuse + plan fingerprint.

Plan todo 40 (A8).  The Wave 0 fixtures under
``tests/baseline/recovery_fixtures/`` freeze pre-migration on-disk state; the
smoke test proves the *readers* still load it.  This module pins the rules
that decide whether pre-migration state may be **reused** (not merely read) by
post-migration code, so ``continue`` never recomputes completed work, never
loses artifacts, and never honors an incompatible cache:

* **Legacy ACP SP cache** (``acp.backends.batch``) — format version ``0`` /
  ``legacy-geometry-key``: the filename is the sha256 *geometry key* over
  symbols + 10-decimal coordinates + charge + multiplicity + method + basis +
  solvent; the payload is ``{energy_hartree, output_ref}`` with no explicit
  version field.  Reuse conditions: the key-named file loads, ``energy_hartree``
  is numeric, ``output_ref`` is a string, and the referenced output file
  exists.  Any spec/geometry change changes the key, so an incompatible
  specification can never collide with an old entry.
* **cccp generic batch cache** (``cccp.calculation.batch``) — explicit
  ``CACHE_SCHEMA_VERSION`` (currently ``1``) with a full
  ``CacheRecord`` (identity digest / schema / complete / software version /
  artifacts / payload).  ``_cache_lookup`` enforces schema + completeness +
  identity + version policy + artifact presence/hash.  A legacy geometry-key
  record carries none of those fields, so it is an **explicit miss** for the
  new engine (recompute, never an incompatible reuse).  Bumping
  ``CACHE_SCHEMA_VERSION`` invalidates every older-format record.
* **Plan fingerprint** — scheme ``sha256-16``: the first 16 hex characters of
  sha256 over canonical JSON (``sort_keys``) whose step values embed
  ``str(step.spec)``.  This is the checkpoint identity; the frozen fixture
  fingerprint must still validate, and any spec change must change it.  (The
  spec is embedded as a *string*, so spec-key insertion order is part of the
  identity — callers must build specs deterministically.)

Read-only over the fixtures; mutating cases copy to ``tmp_path`` first.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from acp.backends.batch import _geometry_cache_key, _read_cache
from acp.calculations.contracts import (
    CalculationPlan,
    CalculationStep,
    OptimizationMode,
    StepKind,
)
from acp.calculations.executor import _plan_fingerprint
from cccp.calculation.batch import (
    CACHE_SCHEMA_VERSION,
    ArtifactRecord,
    CacheRecord,
    CacheVersionPolicy,
    FileSystemCacheStore,
    MemoryCacheStore,
    _cache_lookup,
    cache_identity,
    resolve_effective_params,
)
from cccp.calculation.errors import TaskInputError

FIXTURES = Path(__file__).resolve().parent / "baseline" / "recovery_fixtures"
SP_CACHE_ROOT = FIXTURES / "batch_sp_cache"


def _sp_cache_input() -> dict[str, object]:
    return json.loads((SP_CACHE_ROOT / "cache_input.json").read_text(encoding="utf-8"))


def _sp_coordinates(meta: dict[str, object]) -> np.ndarray:
    return np.asarray(meta["coordinates"], dtype=float)


# ── Legacy geometry-key SP cache: version + reuse conditions ────────────────


def test_legacy_sp_cache_format_version_and_reuse_conditions() -> None:
    """Legacy SP cache = geometry-key format v0; hit requires key+output on disk."""
    meta = _sp_cache_input()
    cache_path = SP_CACHE_ROOT / f"{meta['cache_key']}.json"
    payload = json.loads(cache_path.read_text(encoding="utf-8"))

    # Format version 0: no explicit version/schema fields — the geometry key
    # and two-field payload are the entire format contract.
    assert set(payload) == {"energy_hartree", "output_ref"}
    assert isinstance(payload["energy_hartree"], (int, float))
    assert isinstance(payload["output_ref"], str)

    record = _read_cache(cache_path, 0, _sp_coordinates(meta), SP_CACHE_ROOT)
    assert record is not None
    assert record.cache_hit is True
    assert record.energy_hartree == -76.4
    assert record.output_path == SP_CACHE_ROOT / "frames" / "frame_000" / "sp_output.log"
    assert record.output_path.is_file()


def test_legacy_sp_cache_geometry_key_is_spec_sensitive() -> None:
    """An incompatible spec/geometry can never collide with a cached entry."""
    meta = _sp_cache_input()
    symbols = list(meta["symbols"])
    coords = _sp_coordinates(meta)

    base = _geometry_cache_key(symbols, coords, 0, 1, meta["method"], None, None)
    assert base == meta["cache_key"]

    changed = {
        "method": _geometry_cache_key(symbols, coords, 0, 1, "wB97X-D4", None, None),
        "basis": _geometry_cache_key(symbols, coords, 0, 1, meta["method"], "def2-SVP", None),
        "solvent": _geometry_cache_key(symbols, coords, 0, 1, meta["method"], None, "water"),
        "charge": _geometry_cache_key(symbols, coords, 1, 1, meta["method"], None, None),
        "multiplicity": _geometry_cache_key(symbols, coords, 0, 3, meta["method"], None, None),
    }
    for field, key in changed.items():
        assert key != base, f"changed {field} must change the geometry cache key"

    shifted = coords.copy()
    shifted[0, 0] += 1.0
    assert _geometry_cache_key(symbols, shifted, 0, 1, meta["method"], None, None) != base


def test_legacy_sp_cache_rejected_when_output_missing(tmp_path: Path) -> None:
    """A key match alone is not enough: a missing artifact forces recompute."""
    root = tmp_path / "batch_sp_cache"
    shutil.copytree(SP_CACHE_ROOT, root)
    meta = _sp_cache_input()
    cache_path = root / f"{meta['cache_key']}.json"
    assert _read_cache(cache_path, 0, _sp_coordinates(meta), root) is not None

    (root / "frames" / "frame_000" / "sp_output.log").unlink()
    assert _read_cache(cache_path, 0, _sp_coordinates(meta), root) is None


# ── cccp cache: explicit schema version + reuse matrix ──────────────────────


def test_cccp_cache_schema_version_defined() -> None:
    assert isinstance(CACHE_SCHEMA_VERSION, int) and not isinstance(CACHE_SCHEMA_VERSION, bool)
    assert CACHE_SCHEMA_VERSION == 1


def test_legacy_geometry_cache_is_explicit_miss_for_new_engine() -> None:
    """The new engine never reuses the incompatible legacy geometry-key cache."""
    meta = _sp_cache_input()
    cache_path = SP_CACHE_ROOT / f"{meta['cache_key']}.json"
    legacy_payload = json.loads(cache_path.read_text(encoding="utf-8"))

    # Strict parse refuses the legacy shape ("cannot prove" -> recompute).
    with pytest.raises(TaskInputError):
        CacheRecord.from_dict(legacy_payload)

    store = FileSystemCacheStore(SP_CACHE_ROOT)
    assert store.read(meta["cache_key"]) is None


def _fresh_identity_and_record(tmp_path: Path) -> tuple[object, CacheRecord]:
    request = {
        "task": "singlepoint",
        "backend": "orca",
        "method": "wB97X-D4",
        "charge": 0,
        "multiplicity": 1,
        "inputs": {"primary": "geometry"},
    }
    params = resolve_effective_params(request, run_config={"basis": "def2-SVP"})
    identity = cache_identity(params)
    artifact = tmp_path / "sp_output.log"
    artifact.write_text("synthetic single-point output", encoding="utf-8")
    record = CacheRecord(
        identity_digest=identity.digest,
        schema_version=CACHE_SCHEMA_VERSION,
        complete=True,
        software_version="6.0.1",
        artifacts=(
            ArtifactRecord(
                name="sp_output",
                path=str(artifact),
                sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            ),
        ),
        result_payload={"energy_hartree": -76.4},
    )
    return identity, record


class _FixedStore:
    """Cache store returning a fixed record regardless of the lookup digest."""

    def __init__(self, record: CacheRecord | None) -> None:
        self.record = record

    def read(self, digest: str) -> CacheRecord | None:
        return self.record

    def write(self, record: CacheRecord) -> None:
        self.record = record


def test_cccp_cache_reuse_conditions(tmp_path: Path) -> None:
    """A new-format hit requires schema+complete+identity+version+artifacts."""
    identity, record = _fresh_identity_and_record(tmp_path)
    store = _FixedStore(record)

    hit, reason = _cache_lookup(store, identity, CacheVersionPolicy.REQUIRE_RECORDED)
    assert hit is not None and reason == ""

    def _lookup(candidate: CacheRecord) -> str:
        store.record = candidate
        found, why = _cache_lookup(store, identity, CacheVersionPolicy.REQUIRE_RECORDED)
        assert found is None
        return why

    assert _lookup(replace(record, schema_version=CACHE_SCHEMA_VERSION + 1)) == "schema_mismatch"
    assert _lookup(replace(record, complete=False)) == "incomplete_record"
    assert _lookup(replace(record, identity_digest="0" * 64)) == "identity_mismatch"
    assert _lookup(replace(record, software_version=None)) == "version_incompatible"
    assert (
        _lookup(
            replace(
                record,
                artifacts=(replace(record.artifacts[0], path=str(tmp_path / "missing.log")),),
            )
        )
        == "artifact_missing"
    )
    assert (
        _lookup(replace(record, artifacts=(replace(record.artifacts[0], sha256="f" * 64),)))
        == "artifact_hash_mismatch"
    )

    empty_found, empty_reason = _cache_lookup(
        MemoryCacheStore(), identity, CacheVersionPolicy.REQUIRE_RECORDED
    )
    assert empty_found is None and empty_reason == "no_record"


# ── Plan fingerprint: scheme + compatibility ────────────────────────────────


def _fixture_plan() -> CalculationPlan:
    return CalculationPlan(
        workflow="BatchOptimize",
        profile="opt_freq",
        items=[{"path": "structures/input.xyz"}],
        steps=[
            CalculationStep(
                kind=StepKind.OPTIMIZE,
                mode=OptimizationMode.UNCONSTRAINED,
                spec={"max_cycles": 5},
            ),
            CalculationStep(
                kind=StepKind.FREQUENCY,
                mode=OptimizationMode.UNCONSTRAINED,
                spec=None,
            ),
        ],
    )


def test_plan_fingerprint_scheme_and_frozen_fixture_compatibility() -> None:
    """Scheme sha256-16 over ``str(step.spec)``; frozen fingerprint still matches."""
    meta = json.loads(
        (FIXTURES / "checkpoint_mixed" / "plan_fingerprint.json").read_text(encoding="utf-8")
    )
    fingerprint = _plan_fingerprint(_fixture_plan())
    assert fingerprint == meta["plan_fingerprint"]
    # Explicit scheme: 16 lowercase hex chars (sha256 truncated).
    assert len(fingerprint) == 16
    assert all(ch in "0123456789abcdef" for ch in fingerprint)
    # Deterministic for identical content.
    assert _plan_fingerprint(_fixture_plan()) == fingerprint


def test_plan_fingerprint_changes_on_spec_change() -> None:
    """A spec/defaults change is a NEW identity (documented, not silent reuse)."""
    base = _plan_fingerprint(_fixture_plan())
    changed_spec = _fixture_plan()
    changed_spec.steps[0] = CalculationStep(
        kind=StepKind.OPTIMIZE,
        mode=OptimizationMode.UNCONSTRAINED,
        spec={"max_cycles": 6},
    )
    assert _plan_fingerprint(changed_spec) != base

    changed_profile = replace(_fixture_plan(), profile="opt_freq_sp")
    assert _plan_fingerprint(changed_profile) != base

    changed_workflow = replace(_fixture_plan(), workflow="singlepoint")
    assert _plan_fingerprint(changed_workflow) != base
