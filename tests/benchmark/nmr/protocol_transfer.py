"""Protocol-transfer A/B harness (todo 52 / gap §10.3 trial B).

Gap §10.3 trial B asks a different question than trial A (implementation
parity): on a **fixed candidate set and experimental spectrum**, what changes
when exactly one computational-protocol setting is swapped?  Geometry
optimization level, population energy level, solvation model, QC engine and
automatic-assignment strategy each become an *axis*:

    AXES = ("geometry", "energy", "solvent", "engine", "assignment")

Only the deviation of a protocol axis is measured; a single success never
proves that a model/protocol is suitable (see the runbook section below).

Single-variable discipline
--------------------------
Every comparison changes EXACTLY one axis.  :func:`assert_single_axis_change`
rejects a variant that differs on two or more axes with
:class:`MultiAxisChangeError`, and a no-op comparison with
:class:`NoAxisChangeError`; both are raised before any runner executes.  The
side manifests must also keep the fixed candidate set and experimental
spectrum byte-identically (identity/observed-ppm fingerprints): a runner that
moves them raises :class:`FixedInputsChangedError`, so a drifting input can
never masquerade as a protocol effect.

Impact table
------------
:func:`run_ab_comparison` runs a caller-supplied :class:`ProtocolRunner` for
the baseline and the variant settings and evaluates both resulting dataset
manifests through the todo-50 harness (``run_harness`` with
``thresholds_path=None``, ``include_spectra=False``, ``measure_resources=False``
and a pinned ``now``) — the metric vocabulary is
``thresholds.KNOWN_METRIC_KEYS`` extracted with ``thresholds.metric_value``,
never a forked copy.  Each axis row carries:

* ``baseline`` / ``variant`` provenance: axis value, full settings +
  fingerprint, protocol id, model ids (shielding/DP5/error-model), T50 dataset
  id/hash, canonical manifest file sha256, fixed candidate-set sha256,
  experimental-spectrum sha256, seed, status;
* per-metric rows: ``baseline`` / ``variant`` / ``delta`` (variant minus
  baseline) with an explicit ``measured`` / ``not_verified`` status;
* ``paired_metrics``: molecule-clustered paired bootstrap
  (``paired_clustered_bootstrap_ci``) over per-molecule absolute shift
  residuals for each nucleus — atoms/conformers of one molecule are never
  independent samples.

:func:`build_impact_table` assembles several axis rows into one canonical
JSON document (``acp-nmr-protocol-transfer-impact-v1``) with base-manifest
provenance and a status summary; :func:`write_impact_table` persists it with
the todo-48 canonical atomic writer.  With ``now`` pinned and a deterministic
runner the whole table is byte-identical across processes (sorted keys,
seeded bootstrap, no wall-clock reads).

Three-state discipline
----------------------
A real axis that cannot run (missing binary/assets) raises
:class:`ProtocolUnavailableError` from the runner; the comparison becomes
``status="not_verified"`` with reasons carrying the literal ``NOT_VERIFIED``,
and its summary never counts as measured.  The automated suite exercises this
path with mock runners and **never starts a QC subprocess**.

Real-run runbook (operator-supplied runner)
-------------------------------------------
The harness itself never launches QC.  To run an axis against the real
pipeline, export the configured binaries first (todo-51 smoke conventions;
``~/.cccp.yaml`` is NOT on ``PATH``)::

    export CONFSEARCH_ORCA_PATH=/home/<user>/orca611/orca
    export CONFSEARCH_CREST_PATH=/home/<user>/crest/crest
    export CONFSEARCH_XTB_PATH=/home/<user>/xtb-dist/bin/xtb
    export CONFSEARCH_CENSO_PATH=/home/<user>/anaconda3/bin/censo
    PYTHONPATH=src python3.11 -m pytest --run-slow --run-integration \\
        tests/test_acp_nmr_qc_smoke.py -q      # connectivity gate first

then drive the harness from a script that supplies a ``ProtocolRunner`` which
runs the ACP NMR workflow with ``settings`` and emits a todo-50 dataset
manifest (``tests/benchmark/nmr/schema.py`` contract; layers 2-5 may be
validated first with ``tests/benchmark/nmr/loaders.py``).  Change exactly one
axis per comparison:

======================  ==========================  =========================================
Axis                    Baseline example            Variant example (one at a time)
======================  ==========================  =========================================
``geometry``            ``censo-light``             ``censo-default`` / ``censo-zero``
                        conformer geometry level    (``NmrMethodConfig.conformer_preset``)
``energy``              ``xtb-gfn2``                ``dft-sp`` — population-energy level
                        GFN2-xTB ensemble energies  for Boltzmann weights (window gated by
                                                    ``NmrMethodConfig.ewin``)
``solvent``             ``cpcm/chloroform``         ``smd/dmso`` / ``none`` (gas phase) —
                        ``solvent_model/solvent``   explicit none is never overwritten
``engine``              ``orca``                    another GIAO-capable implementation id
                        GIAO engine implementation  (keyword-registry implementation;
                                                    ORCA is the only sanctioned one today)
``assignment``          ``two-phase-hungarian``     ``locked-only`` — automatic assignment
                        locked labels + Hungarian   strategy in ``acp.nmr.assignment``
======================  ==========================  =========================================

Record the impact table with ``now`` pinning and, for real evidence, copy it
plus the raw logs into ``.omo/evidence/``.  **A single successful real run is
an applicability illustration, NOT evidence of model suitability**: protocol
transfer only bounds where deviations come from.  Accuracy/calibration claims
require the layered benchmark campaign (todo 53 datasets, todo 54
reference-validation) with molecule-clustered paired statistics and the
pre-registered thresholds — never a lone successful run.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from tests.benchmark.nmr.bootstrap import (
    DEFAULT_BOOTSTRAP_SEED,
    DEFAULT_N_RESAMPLES,
    BootstrapError,
    paired_clustered_bootstrap_ci,
)
from tests.benchmark.nmr.harness import run_harness
from tests.benchmark.nmr.schema import NUCLEI, load_dataset_manifest, validate_dataset_manifest
from tests.benchmark.nmr.thresholds import KNOWN_METRIC_KEYS, metric_value
from tests.nmr_spectra_benchmark import NOT_VERIFIED, canonical_json_bytes, write_metrics

PROTOCOL_TRANSFER_SCHEMA = "acp-nmr-protocol-transfer-impact-v1"

#: The five protocol axes, in canonical order (single-axis vocabulary).
AXES: tuple[str, ...] = ("geometry", "energy", "solvent", "engine", "assignment")

_RUN_STATUSES: tuple[str, ...] = ("measured", "not_verified")


class ProtocolTransferError(ValueError):
    """Base class for typed protocol-transfer failures."""


class UnknownAxisError(ProtocolTransferError):
    """An axis name outside :data:`AXES` was requested."""


class AxisIsolationError(ProtocolTransferError):
    """The single-variable discipline was violated (before any run)."""


class MultiAxisChangeError(AxisIsolationError):
    """More than one axis differs between baseline and variant."""


class NoAxisChangeError(AxisIsolationError):
    """No axis differs — there is nothing to compare."""


class FixedInputsChangedError(ProtocolTransferError):
    """The fixed candidate set / experimental spectrum moved between runs."""


class ProtocolRunError(ProtocolTransferError):
    """A runner returned a malformed or mismatched :class:`ProtocolRun`."""


class ProtocolUnavailableError(ProtocolTransferError):
    """A real axis cannot run (missing binary/assets) — becomes NOT_VERIFIED."""


@dataclass(frozen=True)
class AxisSpec:
    """Documentation + real-knob mapping for one protocol axis.

    ``method_config_fields`` names the :class:`acp.nmr.method_config.NmrMethodConfig`
    fields that carry the axis when one exists (empty for axes that live
    outside the resolved method config, e.g. engine/assignment strategy).
    ``example_values`` lists real spellings; the default settings value of an
    axis must appear there.
    """

    axis: str
    title: str
    real_knob: str
    example_values: tuple[str, ...]
    method_config_fields: tuple[str, ...] = ()


AXIS_SPECS: Mapping[str, AxisSpec] = {
    "geometry": AxisSpec(
        axis="geometry",
        title="geometry optimization level",
        real_knob=(
            "conformer geometry level via the CREST/CENSO preset (NmrMethodConfig.conformer_preset)"
        ),
        example_values=("censo-light", "censo-default", "censo-zero"),
        method_config_fields=("conformer_preset",),
    ),
    "energy": AxisSpec(
        axis="energy",
        title="population energy level",
        real_knob=(
            "level of theory of the conformer energies used for Boltzmann weights "
            "(CENSO refinement / population-energy method); the window is gated by "
            "NmrMethodConfig.ewin"
        ),
        example_values=("xtb-gfn2", "dft-sp"),
        method_config_fields=("ewin",),
    ),
    "solvent": AxisSpec(
        axis="solvent",
        title="solvent model",
        real_knob=(
            "ORCA solvation model + solvent name (NmrMethodConfig.solvent_model / "
            "NmrMethodConfig.solvent); an explicit 'none' is gas phase"
        ),
        example_values=("cpcm/chloroform", "smd/dmso", "none"),
        method_config_fields=("solvent_model", "solvent"),
    ),
    "engine": AxisSpec(
        axis="engine",
        title="QC engine",
        real_knob=(
            "QC engine implementation backing the GIAO level (keyword-registry "
            "implementation); ORCA is the only sanctioned GIAO engine today"
        ),
        example_values=("orca",),
        method_config_fields=(),
    ),
    "assignment": AxisSpec(
        axis="assignment",
        title="automatic assignment strategy",
        real_knob=(
            "automatic atom-to-peak assignment strategy in acp.nmr.assignment "
            "(two-phase locked + Hungarian vs locked-only diagnostics)"
        ),
        example_values=("two-phase-hungarian", "locked-only"),
        method_config_fields=(),
    ),
}


@dataclass(frozen=True)
class ProtocolSettings:
    """One full protocol configuration; build variants with :meth:`with_axis`."""

    geometry: str = "censo-light"
    energy: str = "xtb-gfn2"
    solvent: str = "cpcm/chloroform"
    engine: str = "orca"
    assignment: str = "two-phase-hungarian"

    def __post_init__(self) -> None:
        for axis in AXES:
            value = getattr(self, axis)
            if not isinstance(value, str) or not value.strip():
                raise ProtocolTransferError(
                    f"axis {axis!r} must be a non-empty string, got {value!r}"
                )

    def axis_value(self, axis: str) -> str:
        """Value of *axis* (:class:`UnknownAxisError` when unknown)."""
        if axis not in AXES:
            raise UnknownAxisError(f"unknown protocol axis {axis!r}; known axes: {AXES}")
        return str(getattr(self, axis))

    def to_dict(self) -> dict[str, str]:
        """JSON-safe view in canonical :data:`AXES` order."""
        return {axis: str(getattr(self, axis)) for axis in AXES}

    def fingerprint(self) -> str:
        """Stable sha256 over the canonical settings payload."""
        return hashlib.sha256(canonical_json_bytes(self.to_dict())).hexdigest()

    def with_axis(self, axis: str, value: str) -> ProtocolSettings:
        """Copy with exactly one axis replaced (validated)."""
        if axis not in AXES:
            raise UnknownAxisError(f"unknown protocol axis {axis!r}; known axes: {AXES}")
        return replace(self, **{axis: value})


DEFAULT_SETTINGS = ProtocolSettings()


def changed_axes(baseline: ProtocolSettings, variant: ProtocolSettings) -> tuple[str, ...]:
    """Axes whose values differ, in canonical :data:`AXES` order."""
    return tuple(axis for axis in AXES if getattr(baseline, axis) != getattr(variant, axis))


def assert_single_axis_change(
    axis: str, baseline: ProtocolSettings, variant: ProtocolSettings
) -> None:
    """Enforce exactly one changed axis; raises typed errors before any run.

    Raises:
        UnknownAxisError: ``axis`` is not in :data:`AXES`.
        NoAxisChangeError: baseline and variant are identical.
        MultiAxisChangeError: ``axis`` is not the only differing axis.
    """
    if axis not in AXES:
        raise UnknownAxisError(f"unknown protocol axis {axis!r}; known axes: {AXES}")
    changed = changed_axes(baseline, variant)
    if not changed:
        raise NoAxisChangeError(
            f"comparison on axis {axis!r} has identical baseline/variant settings — "
            "nothing changes and no protocol effect can be measured"
        )
    if changed != (axis,):
        raise MultiAxisChangeError(
            f"axis isolation violated: requested comparison on {axis!r} but {changed} "
            "differ; change exactly one axis per comparison"
        )


@dataclass(frozen=True)
class ProtocolRun:
    """One side's run delivered by a :class:`ProtocolRunner`.

    A ``measured`` run must carry the todo-50 dataset manifest mapping; a
    ``not_verified`` run must carry reasons beginning with the literal
    ``NOT_VERIFIED`` (three-state rule — a missing axis is never a pass).
    """

    settings: ProtocolSettings
    protocol_id: str
    model_ids: Mapping[str, str]
    manifest: Mapping[str, Any] | None = None
    status: str = "measured"
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.settings, ProtocolSettings):
            raise ProtocolRunError(
                f"settings must be ProtocolSettings, got {type(self.settings).__name__}"
            )
        if not isinstance(self.protocol_id, str) or not self.protocol_id.strip():
            raise ProtocolRunError("protocol_id must be a non-empty string")
        if not isinstance(self.model_ids, Mapping):
            raise ProtocolRunError("model_ids must be a mapping of non-empty id strings")
        model_ids: dict[str, str] = {}
        for key, value in self.model_ids.items():
            if not isinstance(key, str) or not key.strip():
                raise ProtocolRunError(f"model_ids key {key!r} must be a non-empty string")
            if not isinstance(value, str) or not value.strip():
                raise ProtocolRunError(f"model_ids[{key!r}] must be a non-empty string")
            model_ids[str(key)] = str(value)
        if self.status not in _RUN_STATUSES:
            raise ProtocolRunError(f"status {self.status!r} not in {_RUN_STATUSES}")
        reasons = tuple(str(reason) for reason in self.reasons)
        if self.status == "measured":
            if not isinstance(self.manifest, Mapping):
                raise ProtocolRunError(
                    "a measured run must carry the todo-50 dataset manifest mapping"
                )
        elif not reasons or any(not reason.startswith(NOT_VERIFIED) for reason in reasons):
            raise ProtocolRunError(
                f"a not_verified run requires reasons starting with {NOT_VERIFIED!r}"
            )
        object.__setattr__(self, "model_ids", dict(sorted(model_ids.items())))
        object.__setattr__(self, "reasons", reasons)


class ProtocolRunner(Protocol):
    """Callable producing one :class:`ProtocolRun` for one settings value.

    The runner is supplied by the operator/test; the harness never launches
    QC itself.  ``base_manifest`` is the fixed candidate/spectrum manifest
    path; ``seed`` is the run seed recorded in the provenance.
    """

    def __call__(
        self, settings: ProtocolSettings, base_manifest: Path, *, seed: int
    ) -> ProtocolRun: ...


@dataclass(frozen=True)
class AxisComparison:
    """One requested single-axis comparison (baseline vs variant)."""

    axis: str
    baseline: ProtocolSettings
    variant: ProtocolSettings

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise UnknownAxisError(f"unknown protocol axis {self.axis!r}; known axes: {AXES}")


# ---------------------------------------------------------------------------
# fixed-input fingerprints (candidate identity + experimental spectrum)
# ---------------------------------------------------------------------------


def _candidate_identity_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "datasets": [
            {
                "dataset_id": str(dataset["dataset_id"]),
                "items": [
                    {
                        "item_id": str(item["item_id"]),
                        "molecule_id": str(item["molecule_id"]),
                        "true_structure_present": bool(item["true_structure_present"]),
                        "true_structure_candidate_id": item["true_structure_candidate_id"],
                        "candidates": [
                            {
                                "candidate_id": str(candidate["candidate_id"]),
                                "is_true_structure": bool(candidate["is_true_structure"]),
                            }
                            for candidate in item["candidates"]
                        ],
                    }
                    for item in dataset["items"]
                ],
            }
            for dataset in manifest["datasets"]
        ]
    }


def candidate_set_sha256(manifest: Mapping[str, Any]) -> str:
    """sha256 of the fixed candidate identity (ids/structure flags only).

    Predictions, probabilities and candidate statuses are protocol OUTPUTS
    and deliberately excluded; the candidate set itself must not move.
    """
    return hashlib.sha256(canonical_json_bytes(_candidate_identity_payload(manifest))).hexdigest()


def experimental_spectrum_sha256(manifest: Mapping[str, Any]) -> str:
    """sha256 of the fixed experimental spectrum (per item, canonical)."""
    payload = {
        "datasets": [
            {
                "dataset_id": str(dataset["dataset_id"]),
                "items": [
                    {"item_id": str(item["item_id"]), "experimental": item["experimental"]}
                    for item in dataset["items"]
                ],
            }
            for dataset in manifest["datasets"]
        ]
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _input_fingerprints(manifest: Mapping[str, Any]) -> dict[str, str]:
    return {
        "candidate_set_sha256": candidate_set_sha256(manifest),
        "experimental_spectrum_sha256": experimental_spectrum_sha256(manifest),
    }


# ---------------------------------------------------------------------------
# execution + evaluation plumbing
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_timestamp(now: str | None) -> str:
    if now is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not isinstance(now, str) or not now.strip():
        raise ProtocolTransferError("now must be null or a non-empty ISO timestamp string")
    return now


@contextmanager
def _working_directory(work_dir: str | Path | None):
    if work_dir is not None:
        path = Path(work_dir)
        path.mkdir(parents=True, exist_ok=True)
        yield path
    else:
        with tempfile.TemporaryDirectory(prefix="acp-nmr-protocol-transfer-") as temporary:
            yield Path(temporary)


def _execute_side(
    runner: ProtocolRunner,
    settings: ProtocolSettings,
    base_path: Path,
    seed: int,
) -> ProtocolRun:
    try:
        run = runner(settings, base_path, seed=seed)
    except ProtocolUnavailableError as exc:
        return ProtocolRun(
            settings=settings,
            protocol_id="unavailable",
            model_ids={},
            status="not_verified",
            reasons=(f"{NOT_VERIFIED}: {exc}",),
        )
    if not isinstance(run, ProtocolRun):
        raise ProtocolRunError(f"runner returned {type(run).__name__}, expected ProtocolRun")
    if run.settings != settings:
        raise ProtocolRunError(
            "runner returned a ProtocolRun for different settings: expected "
            f"{settings.to_dict()}, got {run.settings.to_dict()}"
        )
    return run


def _evaluate_manifest(
    manifest_path: Path, *, seed: int, n_resamples: int, now: str
) -> dict[str, Any]:
    payload = run_harness(
        manifest_path,
        thresholds_path=None,
        include_spectra=False,
        now=now,
        measure_resources=False,
        n_resamples=n_resamples,
        seed=seed,
    )
    blocks = payload["datasets"]
    if len(blocks) != 1:
        raise ProtocolRunError(
            "protocol-transfer comparisons require exactly one dataset per side, "
            f"found {len(blocks)}; split multi-dataset manifests per comparison"
        )
    return blocks[0]


def _side_provenance(
    axis: str,
    settings: ProtocolSettings,
    run: ProtocolRun,
    *,
    seed: int,
    measured_details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "axis_value": settings.axis_value(axis),
        "settings": settings.to_dict(),
        "settings_fingerprint": settings.fingerprint(),
        "protocol_id": run.protocol_id,
        "model_ids": dict(run.model_ids),
        "dataset_id": None,
        "layer": None,
        "dataset_hash": None,
        "manifest_file_sha256": None,
        "candidate_set_sha256": None,
        "experimental_spectrum_sha256": None,
        "seed": seed,
        "status": run.status,
        "reasons": list(run.reasons),
    }
    if measured_details is not None:
        record.update(measured_details)
    return record


def _metric_entries(
    baseline_block: Mapping[str, Any], variant_block: Mapping[str, Any]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key in KNOWN_METRIC_KEYS:
        baseline_value, baseline_reason = metric_value(baseline_block, key)
        variant_value, variant_reason = metric_value(variant_block, key)
        reasons: list[str] = []
        if baseline_value is None and baseline_reason:
            reasons.append(f"baseline:{baseline_reason}")
        if variant_value is None and variant_reason:
            reasons.append(f"variant:{variant_reason}")
        measured = baseline_value is not None and variant_value is not None
        entries.append(
            {
                "key": key,
                "status": "measured" if measured else "not_verified",
                "baseline": baseline_value,
                "variant": variant_value,
                "delta": (variant_value - baseline_value) if measured else None,
                "reasons": reasons,
            }
        )
    return entries


def _abs_residual_clusters(block: Mapping[str, Any], nucleus: str) -> dict[str, list[float]]:
    clusters: dict[str, list[float]] = {}
    for item in block["items"]:
        records = item["residuals"].get(nucleus, ())
        if not records:
            continue
        clusters.setdefault(str(item["molecule_id"]), []).extend(
            abs(float(record["residual_ppm"])) for record in records
        )
    return clusters


def _paired_metrics(
    baseline_block: Mapping[str, Any],
    variant_block: Mapping[str, Any],
    *,
    seed: int,
    n_resamples: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for nucleus in sorted(NUCLEI):
        clusters_a = _abs_residual_clusters(baseline_block, nucleus)
        clusters_b = _abs_residual_clusters(variant_block, nucleus)
        unresolved: dict[str, Any] = {
            "status": "not_verified",
            "a": "baseline",
            "b": "variant",
            "unit": "absolute shift residual (ppm), molecule-clustered",
            "result": None,
        }
        if not clusters_a or not clusters_b:
            result[nucleus] = {**unresolved, "reasons": ["no_paired_residual_rows"]}
            continue
        if set(clusters_a) != set(clusters_b) or len(clusters_a) < 2:
            result[nucleus] = {**unresolved, "reasons": ["insufficient_molecule_clusters"]}
            continue
        try:
            paired = paired_clustered_bootstrap_ci(
                clusters_a, clusters_b, seed=seed, n_resamples=n_resamples
            )
        except BootstrapError as exc:
            result[nucleus] = {**unresolved, "reasons": [str(exc)]}
            continue
        result[nucleus] = {
            "status": "measured",
            "a": "baseline",
            "b": "variant",
            "unit": "absolute shift residual (ppm), molecule-clustered",
            "result": paired.as_dict(),
        }
    return result


# ---------------------------------------------------------------------------
# public comparisons
# ---------------------------------------------------------------------------


def run_ab_comparison(
    axis: str,
    baseline_settings: ProtocolSettings,
    variant_settings: ProtocolSettings,
    base_manifest: str | Path,
    runner: ProtocolRunner,
    *,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    now: str | None = None,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run one single-axis A/B comparison and return its impact row.

    Args:
        axis: The one axis under test (must be the only differing axis).
        baseline_settings: Reference protocol configuration.
        variant_settings: Variant differing on ``axis`` only.
        base_manifest: Fixed candidate-set + experimental-spectrum manifest.
        runner: Operator/test-supplied :class:`ProtocolRunner` (no QC here).
        seed: Bootstrap/run seed recorded on both sides.
        n_resamples: Molecule-clustered paired bootstrap resamples.
        now: Pinned ISO timestamp for deterministic replay.
        work_dir: Where side manifests are persisted (temp dir when omitted).

    Raises:
        UnknownAxisError / NoAxisChangeError / MultiAxisChangeError: axis
            isolation violated before any runner executes.
        FixedInputsChangedError: a side moved the fixed candidate set or
            experimental spectrum.
        BenchmarkManifestError: side manifest violates the todo-50 schema.
        ProtocolRunError: runner returned malformed/mismatched output.
    """
    assert_single_axis_change(axis, baseline_settings, variant_settings)
    base_path = Path(base_manifest)
    base_doc = load_dataset_manifest(base_path)
    base_inputs = _input_fingerprints(base_doc)
    timestamp = _resolve_timestamp(now)
    axis_change = {
        "from": baseline_settings.axis_value(axis),
        "to": variant_settings.axis_value(axis),
    }

    with _working_directory(work_dir) as work:
        baseline_run = _execute_side(runner, baseline_settings, base_path, seed)
        variant_run = _execute_side(runner, variant_settings, base_path, seed)
        if baseline_run.status != "measured" or variant_run.status != "measured":
            return {
                "axis": axis,
                "axis_change": axis_change,
                "status": "not_verified",
                "reasons": list(baseline_run.reasons) + list(variant_run.reasons),
                "baseline": _side_provenance(axis, baseline_settings, baseline_run, seed=seed),
                "variant": _side_provenance(axis, variant_settings, variant_run, seed=seed),
                "metrics": None,
                "paired_metrics": None,
            }

        baseline_manifest = validate_dataset_manifest(baseline_run.manifest)
        variant_manifest = validate_dataset_manifest(variant_run.manifest)
        baseline_inputs = _input_fingerprints(baseline_manifest)
        variant_inputs = _input_fingerprints(variant_manifest)
        if baseline_inputs != base_inputs or variant_inputs != base_inputs:
            raise FixedInputsChangedError(
                "the fixed candidate set / experimental spectrum changed between the base "
                f"manifest and a run (base={base_inputs}, baseline={baseline_inputs}, "
                f"variant={variant_inputs}); only the {axis!r} axis may differ"
            )

        baseline_path = work / f"baseline-{baseline_settings.fingerprint()[:16]}.json"
        variant_path = work / f"variant-{variant_settings.fingerprint()[:16]}.json"
        write_metrics(baseline_manifest, baseline_path)
        write_metrics(variant_manifest, variant_path)
        baseline_block = _evaluate_manifest(
            baseline_path, seed=seed, n_resamples=n_resamples, now=timestamp
        )
        variant_block = _evaluate_manifest(
            variant_path, seed=seed, n_resamples=n_resamples, now=timestamp
        )
        baseline_details = {
            "dataset_id": baseline_block["dataset_id"],
            "layer": baseline_block["layer"],
            "dataset_hash": baseline_block["hash"],
            "manifest_file_sha256": _sha256_file(baseline_path),
            "candidate_set_sha256": baseline_inputs["candidate_set_sha256"],
            "experimental_spectrum_sha256": baseline_inputs["experimental_spectrum_sha256"],
        }
        variant_details = {
            "dataset_id": variant_block["dataset_id"],
            "layer": variant_block["layer"],
            "dataset_hash": variant_block["hash"],
            "manifest_file_sha256": _sha256_file(variant_path),
            "candidate_set_sha256": variant_inputs["candidate_set_sha256"],
            "experimental_spectrum_sha256": variant_inputs["experimental_spectrum_sha256"],
        }
        return {
            "axis": axis,
            "axis_change": axis_change,
            "status": "measured",
            "reasons": [],
            "baseline": _side_provenance(
                axis, baseline_settings, baseline_run, seed=seed, measured_details=baseline_details
            ),
            "variant": _side_provenance(
                axis, variant_settings, variant_run, seed=seed, measured_details=variant_details
            ),
            "metrics": _metric_entries(baseline_block, variant_block),
            "paired_metrics": _paired_metrics(
                baseline_block, variant_block, seed=seed, n_resamples=n_resamples
            ),
        }


def build_impact_table(
    base_manifest: str | Path,
    comparisons: Sequence[AxisComparison],
    runner: ProtocolRunner,
    *,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    n_resamples: int = DEFAULT_N_RESAMPLES,
    now: str | None = None,
    work_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble several single-axis comparisons into one canonical impact table.

    With ``now`` pinned and a deterministic runner the returned document is
    byte-identical across processes (``write_impact_table`` persists it with
    the todo-48 canonical atomic writer).
    """
    comparison_list = list(comparisons)
    if not comparison_list:
        raise ProtocolTransferError(
            "at least one AxisComparison is required (an empty impact table proves nothing)"
        )
    base_path = Path(base_manifest)
    base_doc = load_dataset_manifest(base_path)
    timestamp = _resolve_timestamp(now)
    entries = [
        run_ab_comparison(
            comparison.axis,
            comparison.baseline,
            comparison.variant,
            base_path,
            runner,
            seed=seed,
            n_resamples=n_resamples,
            now=timestamp,
            work_dir=work_dir,
        )
        for comparison in comparison_list
    ]
    statuses = Counter(entry["status"] for entry in entries)
    summary = {
        "n_comparisons": len(entries),
        "n_measured": statuses.get("measured", 0),
        "n_not_verified": statuses.get("not_verified", 0),
        "statuses": dict(sorted(statuses.items())),
        "axes_covered": sorted({entry["axis"] for entry in entries}),
    }
    return {
        "schema": PROTOCOL_TRANSFER_SCHEMA,
        "axes": list(AXES),
        "timestamp": timestamp,
        "seed": seed,
        "n_resamples": n_resamples,
        "base_manifest": {
            "schema": base_doc["schema"],
            "sha256": _sha256_file(base_path),
            "dataset_ids": [str(dataset["dataset_id"]) for dataset in base_doc["datasets"]],
            "candidate_set_sha256": candidate_set_sha256(base_doc),
            "experimental_spectrum_sha256": experimental_spectrum_sha256(base_doc),
        },
        "comparisons": entries,
        "summary": summary,
    }


def write_impact_table(table: Mapping[str, Any], path: str | Path) -> Path:
    """Canonically and atomically persist an impact table; returns the path."""
    return write_metrics(table, path)


__all__ = [
    "AXES",
    "AXIS_SPECS",
    "DEFAULT_SETTINGS",
    "PROTOCOL_TRANSFER_SCHEMA",
    "AxisComparison",
    "AxisIsolationError",
    "AxisSpec",
    "FixedInputsChangedError",
    "MultiAxisChangeError",
    "NoAxisChangeError",
    "ProtocolRun",
    "ProtocolRunError",
    "ProtocolRunner",
    "ProtocolSettings",
    "ProtocolTransferError",
    "ProtocolUnavailableError",
    "UnknownAxisError",
    "assert_single_axis_change",
    "build_impact_table",
    "candidate_set_sha256",
    "changed_axes",
    "experimental_spectrum_sha256",
    "run_ab_comparison",
    "write_impact_table",
]
