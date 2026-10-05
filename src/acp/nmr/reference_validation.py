"""Reference-validation mode: pinned upstream settings vs ACP migration (todo 31).

The ``reference_validation`` protocol mode (gap G04 / §10.3 试验 A) is only
claimable when REAL pinned upstream reference data is attached
(:func:`attach_reference_segment` sets ``ReferenceSegment.
reference_data_present``) and can be compared item-by-item against the ACP
ORCA migration result:

* :class:`PinnedUpstreamSettings` records the selected paper/SI settings
  (opt / SP / NMR level + key assets) — every value carries an explicit
  ``DevDoc §8.0`` / asset ``NOTICE`` citation, archived from the verified
  tables (conflicting older ``6-31G(d)``/``6-31G**`` notes are not pinned);
* :class:`ReferenceDataset` carries the raw reference values with provenance
  (source, structure identity, units, optional source-asset sha256);
* :func:`compare_reference_vs_migration` produces side-by-side rows with
  ``difference = migrated - reference`` per item plus aggregate statistics
  (n, mean, MAE, RMSE, max |difference|) computed from the raw floats —
  never display-rounded, never approximated;
* a missing/empty reference returns a typed ``unavailable`` outcome with a
  reason; migrated or placeholder values never stand in for references;
* :func:`asset_hashes` records the sha256 of the NOTICE-listed
  ``src/acp/nmr/models/`` assets (missing files degrade to ``None``).

Nothing in this module re-runs QC: it compares records that already exist.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, Literal, cast

from acp.calculations.identity import identity_fingerprint
from acp.nmr.protocol import NmrProtocolSpec, ReferenceSegment

logger = logging.getLogger(__name__)

__all__ = [
    "ASSET_FILES",
    "PINNED_GOODMAN_UPSTREAM",
    "REFERENCE_UNAVAILABLE_REASONS",
    "ComparisonRow",
    "PinnedSetting",
    "PinnedUpstreamSettings",
    "ReferenceAvailability",
    "ReferenceComparison",
    "ReferenceDataset",
    "ReferenceRecord",
    "apply_reference_validation",
    "asset_hashes",
    "assess_reference_dataset",
    "attach_reference_segment",
    "compare_reference_vs_migration",
    "pinned_goodman_upstream",
]

#: NOTICE-listed DP5 assets in ``src/acp/nmr/models/`` (missing → ``None``).
ASSET_FILES: Final[tuple[str, ...]] = (
    "atomic_reps.gz",
    "c_w_kde_mean_s_0.025.p",
    "folded_scaled_errors.p",
    "frag_reps.gz",
    "i_w_kde_mean_s_0.025.p",
    "tms_references.txt",
)

#: Closed vocabulary of typed unavailability reasons (never a placeholder).
REFERENCE_UNAVAILABLE_REASONS: Final[tuple[str, ...]] = (
    "missing_reference",
    "empty_reference",
)

ReferenceValueKind = Literal["shift", "shielding"]
ReferenceAvailabilityStatus = Literal["available", "unavailable"]
ComparisonStatus = Literal["computed", "unavailable"]

_DEV_DOC: Final = "docs/ACP_NMR_DP4_DevDoc.md §8.0"
_NOTICE: Final = "src/acp/nmr/models/NOTICE.md"
_TMS_ASSET: Final = "src/acp/nmr/models/tms_references.txt (NOTICE-listed DP5 asset)"


# ---------------------------------------------------------------------------
# Pinned upstream settings (value + citation for every pin)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PinnedSetting:
    """One pinned upstream setting: the value + the citation it came from."""

    key: str
    value: str | float | int
    source: str

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("pinned setting key must be non-blank")
        if not self.source.strip():
            raise ValueError(f"pinned setting {self.key!r} has no source citation")

    def as_dict(self) -> dict[str, object]:
        return {"value": self.value, "source": self.source}


@dataclass(frozen=True)
class PinnedUpstreamSettings:
    """Frozen pinned upstream (Goodman DP4/DP5) settings with citations.

    Values are archived from DevDoc §8.0 (verified against the fixed upstream
    ``PyDP4.py``/``Gaussian.py`` sources) and the asset NOTICE — one
    :class:`PinnedSetting` per field, and constructing a pin without a
    citation raises. The conflicting older ``6-31G(d)``/``6-31G**``
    descriptions are deliberately not copied.
    """

    settings: tuple[PinnedSetting, ...]

    def __post_init__(self) -> None:
        if not self.settings:
            raise ValueError("pinned upstream settings must not be empty")
        keys = [setting.key for setting in self.settings]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate pinned setting key(s): {duplicates}")

    def get(self, key: str) -> PinnedSetting:
        """Return the pinned setting for *key* (``KeyError`` when absent)."""
        for setting in self.settings:
            if setting.key == key:
                return setting
        raise KeyError(key)

    def value(self, key: str) -> str | float | int:
        """Return the pinned value for *key*."""
        return self.get(key).value

    def as_dict(self) -> dict[str, dict[str, object]]:
        """JSON-safe ``{key: {"value": ..., "source": ...}}`` record."""
        return {setting.key: setting.as_dict() for setting in self.settings}

    @property
    def nmr_method(self) -> str:
        return str(self.value("nmr_method"))

    @property
    def nmr_basis(self) -> str:
        return str(self.value("nmr_basis"))

    @property
    def opt_method(self) -> str:
        return str(self.value("opt_method"))

    @property
    def opt_basis(self) -> str:
        return str(self.value("opt_basis"))

    @property
    def energy_method(self) -> str:
        return str(self.value("energy_method"))

    @property
    def energy_basis(self) -> str:
        return str(self.value("energy_basis"))


PINNED_GOODMAN_UPSTREAM: Final[PinnedUpstreamSettings] = PinnedUpstreamSettings(
    settings=(
        PinnedSetting(
            "nmr_method",
            "mPW1PW91",
            f"{_DEV_DOC} NMR layer (PyDP4.py Settings + Gaussian.py, verified)",
        ),
        PinnedSetting("nmr_basis", "6-311G(d)", f"{_DEV_DOC} NMR layer"),
        PinnedSetting(
            "nmr_solvent_model",
            "PCM (Gaussian scrf)",
            f"{_DEV_DOC} GIAO input; ACP records the solvent model it actually executes",
        ),
        PinnedSetting("giao", "GIAO (Gaussian nmr=giao)", f"{_DEV_DOC} GIAO input"),
        PinnedSetting(
            "opt_method",
            "B3LYP",
            f"{_DEV_DOC} Opt layer (upstream only — ACP substitutes CENSO)",
        ),
        PinnedSetting(
            "opt_basis",
            "6-31G(d,p)",
            f"{_DEV_DOC} Opt layer table; conflicting 6-31G(d)/6-31G** notes not pinned",
        ),
        PinnedSetting(
            "energy_method",
            "M062X",
            f"{_DEV_DOC} Energy layer (upstream only — ACP substitutes CENSO free energies)",
        ),
        PinnedSetting("energy_basis", "def2-TZVP", f"{_DEV_DOC} Energy layer"),
        PinnedSetting("solvent", "chloroform", f"{_DEV_DOC} + {_TMS_ASSET} chloroform row"),
        PinnedSetting(
            "tms_reference_13c_chloroform",
            188.452125,
            f"{_TMS_ASSET} mPW1PW91/6-311G(d)/chloroform",
        ),
        PinnedSetting(
            "tms_reference_1h_chloroform",
            32.1243166667,
            f"{_TMS_ASSET} mPW1PW91/6-311G(d)/chloroform",
        ),
        PinnedSetting(
            "tms_reference_13c_gas",
            188.029225,
            f"{_TMS_ASSET} mPW1PW91/6-311G(d)/none",
        ),
        PinnedSetting(
            "tms_reference_1h_gas",
            32.1352666667,
            f"{_TMS_ASSET} mPW1PW91/6-311G(d)/none",
        ),
        PinnedSetting(
            "error_model",
            "goodman-legacy",
            f"{_NOTICE} DP4 parameters (DP4.py:17-21) — trained for this NMR level",
        ),
        PinnedSetting("dp4_sigma_13c", 2.269372270818724, f"{_NOTICE} DP4 σ_C"),
        PinnedSetting("dp4_sigma_1h", 0.18731058105269952, f"{_NOTICE} DP4 σ_H"),
        PinnedSetting(
            "dp5_source_revision",
            "b6cf559007a5d13fe79654f37daf945ee1661a23",
            f"{_NOTICE} Source revision (fetched 2023-07-15)",
        ),
        PinnedSetting(
            "dp5_folded_scaled_residuals",
            106416,
            f"{_NOTICE} folded_scaled_errors.p (DP5.py:98)",
        ),
    )
)


def pinned_goodman_upstream() -> PinnedUpstreamSettings:
    """Return the frozen pinned upstream settings (citation for every value)."""
    return PINNED_GOODMAN_UPSTREAM


# ---------------------------------------------------------------------------
# Reference data (values + provenance)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReferenceRecord:
    """One pinned reference observable of the pinned structure."""

    item_id: str
    nucleus: str
    value: float
    #: Per-record provenance (SI table row / synthetic-fixture marker).
    source: str = ""

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("reference item_id must be non-blank")
        if not self.nucleus.strip():
            raise ValueError(f"reference nucleus for {self.item_id!r} must be non-blank")
        if not math.isfinite(float(self.value)):
            raise ValueError(f"reference value for {self.item_id!r} is not finite")

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "nucleus": self.nucleus,
            "value": self.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class ReferenceDataset:
    """Pinned reference dataset with full provenance (todo 31 / §10.3 试验 A).

    ``value_kind`` separates chemical shifts (``exp`` ppm, the typical SI
    table) from raw shieldings; comparisons are only meaningful within one
    kind. ``asset_sha256`` records the source asset hash when the dataset was
    extracted from a file (``asset_hashes()`` covers the models/ assets).
    """

    dataset_id: str
    source: str
    structure_identity: str
    units: str = "ppm"
    value_kind: ReferenceValueKind = "shift"
    records: tuple[ReferenceRecord, ...] = ()
    asset_path: str | None = None
    asset_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.dataset_id.strip():
            raise ValueError("reference dataset_id must be non-blank")
        if not self.source.strip():
            raise ValueError("reference dataset source must be non-blank")
        if not self.structure_identity.strip():
            raise ValueError("reference dataset structure_identity must be non-blank")
        if not self.units.strip():
            raise ValueError("reference dataset units must be non-blank")
        if self.value_kind not in ("shift", "shielding"):
            raise ValueError(
                f"unknown reference value_kind {self.value_kind!r}; expected 'shift' or 'shielding'"
            )
        ids = [record.item_id for record in self.records]
        duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate reference item_id(s): {duplicates}")

    def item_ids(self) -> tuple[str, ...]:
        """Item ids in record order."""
        return tuple(record.item_id for record in self.records)

    def values(self) -> dict[str, float]:
        """``{item_id: value}`` view of the pinned values."""
        return {record.item_id: record.value for record in self.records}

    def fingerprint(self) -> str:
        """Recomputable dataset identity over the recorded values."""
        return identity_fingerprint(
            {"scope": "acp_nmr_reference_dataset", "dataset": self.as_dict()}
        )

    def as_dict(self) -> dict[str, object]:
        """JSON-safe dataset record (``None`` stays ``None``)."""
        return {
            "dataset_id": self.dataset_id,
            "source": self.source,
            "structure_identity": self.structure_identity,
            "units": self.units,
            "value_kind": self.value_kind,
            "n_records": len(self.records),
            "asset_path": self.asset_path,
            "asset_sha256": self.asset_sha256,
            "records": [record.as_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ReferenceDataset:
        """Rebuild a dataset from :meth:`as_dict` output."""
        raw_records = payload.get("records") or ()
        records = tuple(
            ReferenceRecord(
                item_id=str(raw["item_id"]),
                nucleus=str(raw["nucleus"]),
                value=float(raw["value"]),
                source=str(raw.get("source", "")),
            )
            for raw in raw_records
        )
        raw_kind = str(payload.get("value_kind", "shift"))
        if raw_kind not in ("shift", "shielding"):
            raise ValueError(f"unknown reference value_kind {raw_kind!r}")
        raw_asset_path = payload.get("asset_path")
        raw_asset_sha256 = payload.get("asset_sha256")
        return cls(
            dataset_id=str(payload["dataset_id"]),
            source=str(payload["source"]),
            structure_identity=str(payload["structure_identity"]),
            units=str(payload.get("units", "ppm")),
            value_kind=cast(ReferenceValueKind, raw_kind),
            records=records,
            asset_path=str(raw_asset_path) if raw_asset_path is not None else None,
            asset_sha256=str(raw_asset_sha256) if raw_asset_sha256 is not None else None,
        )


@dataclass(frozen=True)
class ReferenceAvailability:
    """Typed availability of a reference dataset (never a placeholder value)."""

    status: ReferenceAvailabilityStatus
    reason: str | None
    dataset_id: str | None
    n_records: int

    def __post_init__(self) -> None:
        if self.status not in ("available", "unavailable"):
            raise ValueError(f"unknown reference availability status {self.status!r}")
        if self.status == "available" and self.reason is not None:
            raise ValueError("an available reference must not carry a reason")
        if self.status == "unavailable" and not self.reason:
            raise ValueError("an unavailable reference must carry a reason")
        if self.reason is not None and self.reason not in REFERENCE_UNAVAILABLE_REASONS:
            raise ValueError(f"unknown reference unavailability reason {self.reason!r}")

    @property
    def available(self) -> bool:
        return self.status == "available"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "dataset_id": self.dataset_id,
            "n_records": self.n_records,
        }


def assess_reference_dataset(dataset: ReferenceDataset | None) -> ReferenceAvailability:
    """Typed availability of *dataset* — an explicit reason when unavailable.

    ``None`` → ``missing_reference``; an empty dataset → ``empty_reference``.
    Callers must never substitute placeholder/blocked values for either case.
    """
    if dataset is None:
        return ReferenceAvailability(
            status="unavailable",
            reason="missing_reference",
            dataset_id=None,
            n_records=0,
        )
    if not dataset.records:
        return ReferenceAvailability(
            status="unavailable",
            reason="empty_reference",
            dataset_id=dataset.dataset_id,
            n_records=0,
        )
    return ReferenceAvailability(
        status="available",
        reason=None,
        dataset_id=dataset.dataset_id,
        n_records=len(dataset.records),
    )


# ---------------------------------------------------------------------------
# Side-by-side comparison (difference = migrated - reference)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComparisonRow:
    """One side-by-side row: reference, migrated value and their difference."""

    item_id: str
    nucleus: str
    reference_value: float
    migrated_value: float | None
    #: ``migrated - reference``; ``None`` when the item has no migrated value.
    difference: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "nucleus": self.nucleus,
            "reference_value": self.reference_value,
            "migrated_value": self.migrated_value,
            "difference": self.difference,
        }


@dataclass(frozen=True)
class ReferenceComparison:
    """Comparison outcome: side-by-side rows + aggregate statistics.

    ``status="unavailable"`` carries an explicit ``reason`` and NO rows — a
    missing reference is never filled with a migrated/placeholder value.
    Statistics are computed over the items present on BOTH sides only
    (``n_matched``); reference items missing from the migration are listed in
    ``missing_item_ids`` with a ``None`` difference, and migrated items with
    no reference counterpart are recorded in ``extra_migrated_item_ids``
    instead of being silently compared.
    """

    status: ComparisonStatus
    reason: str | None
    dataset_id: str | None
    dataset_fingerprint: str | None
    dataset_asset_sha256: str | None
    units: str | None
    value_kind: str | None
    n_reference: int
    n_matched: int
    n_missing_migrated: int
    missing_item_ids: tuple[str, ...]
    extra_migrated_item_ids: tuple[str, ...]
    mean_difference: float | None
    mae: float | None
    rmse: float | None
    max_abs_difference: float | None
    rows: tuple[ComparisonRow, ...]

    def as_dict(self) -> dict[str, object]:
        """JSON-safe comparison record (``None`` stays JSON ``null``)."""
        return {
            "status": self.status,
            "reason": self.reason,
            "dataset_id": self.dataset_id,
            "dataset_fingerprint": self.dataset_fingerprint,
            "dataset_asset_sha256": self.dataset_asset_sha256,
            "units": self.units,
            "value_kind": self.value_kind,
            "n_reference": self.n_reference,
            "n_matched": self.n_matched,
            "n_missing_migrated": self.n_missing_migrated,
            "missing_item_ids": list(self.missing_item_ids),
            "extra_migrated_item_ids": list(self.extra_migrated_item_ids),
            "mean_difference": self.mean_difference,
            "mae": self.mae,
            "rmse": self.rmse,
            "max_abs_difference": self.max_abs_difference,
            "rows": [row.as_dict() for row in self.rows],
        }


def _coerce_migrated_values(migrated: Mapping[str, float] | None) -> dict[str, float]:
    """Validate/coerce the migrated mapping (finite floats, non-blank ids)."""
    out: dict[str, float] = {}
    for raw_id, raw_value in (migrated or {}).items():
        item_id = str(raw_id)
        if not item_id.strip():
            raise ValueError("migrated item ids must be non-blank")
        if isinstance(raw_value, bool):
            raise ValueError(f"migrated value for {item_id!r} must be a finite number, got bool")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError(f"migrated value for {item_id!r} is not finite: {raw_value!r}")
        out[item_id] = value
    return out


def compare_reference_vs_migration(
    reference: ReferenceDataset | None,
    migrated: Mapping[str, float] | None,
) -> ReferenceComparison:
    """Side-by-side comparison of pinned reference vs migrated ACP values.

    Per item ``difference = migrated_value - reference_value`` — computed
    from the raw floats, never display-rounded. Aggregate statistics (mean
    difference, MAE, RMSE, max |difference|) use the matched pairs only.

    Args:
        reference: Pinned reference dataset, or ``None`` when unavailable.
        migrated: ACP ORCA migration values keyed by the same item ids
            (``None`` = nothing migrated yet; every row then carries a
            ``None`` difference).

    Returns:
        :class:`ReferenceComparison`; ``status="unavailable"`` with an explicit
        reason when no reference data is available (the migrated mapping is
        never consulted as a reference substitute).
    """
    availability = assess_reference_dataset(reference)
    if reference is None or not availability.available:
        return ReferenceComparison(
            status="unavailable",
            reason=availability.reason,
            dataset_id=availability.dataset_id,
            dataset_fingerprint=None,
            dataset_asset_sha256=None,
            units=None,
            value_kind=None,
            n_reference=0,
            n_matched=0,
            n_missing_migrated=0,
            missing_item_ids=(),
            extra_migrated_item_ids=(),
            mean_difference=None,
            mae=None,
            rmse=None,
            max_abs_difference=None,
            rows=(),
        )

    migrated_values = _coerce_migrated_values(migrated)
    rows: list[ComparisonRow] = []
    missing_item_ids: list[str] = []
    differences: list[float] = []
    for record in reference.records:
        migrated_value = migrated_values.get(record.item_id)
        if migrated_value is None:
            missing_item_ids.append(record.item_id)
            difference: float | None = None
        else:
            difference = migrated_value - float(record.value)
            differences.append(difference)
        rows.append(
            ComparisonRow(
                item_id=record.item_id,
                nucleus=record.nucleus,
                reference_value=record.value,
                migrated_value=migrated_value,
                difference=difference,
            )
        )

    reference_ids = set(reference.item_ids())
    extra_item_ids = tuple(
        sorted(item_id for item_id in migrated_values if item_id not in reference_ids)
    )
    n_matched = len(differences)
    if n_matched:
        mean_difference: float | None = math.fsum(differences) / n_matched
        mae: float | None = math.fsum(abs(value) for value in differences) / n_matched
        rmse: float | None = math.sqrt(
            math.fsum(value * value for value in differences) / n_matched
        )
        max_abs_difference: float | None = max(abs(value) for value in differences)
    else:
        mean_difference = None
        mae = None
        rmse = None
        max_abs_difference = None

    return ReferenceComparison(
        status="computed",
        reason=None,
        dataset_id=reference.dataset_id,
        dataset_fingerprint=reference.fingerprint(),
        dataset_asset_sha256=reference.asset_sha256,
        units=reference.units,
        value_kind=reference.value_kind,
        n_reference=len(reference.records),
        n_matched=n_matched,
        n_missing_migrated=len(missing_item_ids),
        missing_item_ids=tuple(missing_item_ids),
        extra_migrated_item_ids=extra_item_ids,
        mean_difference=mean_difference,
        mae=mae,
        rmse=rmse,
        max_abs_difference=max_abs_difference,
        rows=tuple(rows),
    )


# ---------------------------------------------------------------------------
# Protocol integration (reference_validation gate)
# ---------------------------------------------------------------------------


def attach_reference_segment(
    segment: ReferenceSegment,
    dataset: ReferenceDataset | None,
) -> ReferenceSegment:
    """Bind a reference dataset to a protocol segment (todo 31 gate).

    ``reference_data_present`` is set to ``True`` ONLY for a real, non-empty
    dataset; an unavailable outcome (missing/empty) records the request
    (``reference_validation_requested=True``) while keeping the presence flag
    ``False``. The spec then stays ``exploratory`` / ``unvalidated_protocol``
    with the explicit ``missing_reference`` issue — it can never claim
    ``reference_validation`` without actual reference data.
    """
    availability = assess_reference_dataset(dataset)
    return replace(
        segment,
        reference_data_present=availability.available,
        reference_validation_requested=True,
    )


def apply_reference_validation(
    spec: NmrProtocolSpec,
    dataset: ReferenceDataset | None,
) -> NmrProtocolSpec:
    """Attach *dataset* to *spec* and re-derive mode/issues from the facts."""
    return replace(spec, reference=attach_reference_segment(spec.reference, dataset)).derived()


# ---------------------------------------------------------------------------
# Asset hashes (NOTICE-listed models/ assets)
# ---------------------------------------------------------------------------


def asset_hashes(models_dir: Path | None = None) -> dict[str, str | None]:
    """sha256 for the NOTICE-listed ``src/acp/nmr/models/`` assets.

    Missing files are recorded as ``None`` (graceful skip — never a
    fabricated hash); unreadable files are recorded as ``None`` with a
    warning. The result is deterministic for unchanged files.
    """
    root = models_dir if models_dir is not None else Path(__file__).resolve().parent / "models"
    hashes: dict[str, str | None] = {}
    for name in ASSET_FILES:
        path = root / name
        if not path.is_file():
            hashes[name] = None
            continue
        try:
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            logger.warning(
                "reference_validation: cannot hash asset %s — recording None",
                path,
                exc_info=True,
            )
            hashes[name] = None
            continue
        hashes[name] = digest.hexdigest()
    return hashes
