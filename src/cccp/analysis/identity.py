"""Content-bound request identity v2 (D06) — pure fingerprint algorithm.

Sunk from ``acp.calculations.identity`` (T53) as the single implementation of
the fingerprint primitives: canonical JSON normalisation, content digests,
the ``"v2:" + sha256(canonical_json)[:32]`` fingerprint, and attempt-metadata
config digests.  This module is **contract-free** — it never imports ``acp``.

The ACP-side contract-coupled orchestration (``compute_identity`` over
``CalculationPlan`` / ``CalculationStep``) stays in
``acp.calculations.identity`` and consumes these primitives; that module
re-exports every public name defined here so the old import path returns the
same objects.

``identity_fingerprint = "v2:" + sha256(canonical_json)[:32]``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "IDENTITY_SCHEMA",
    "FINGERPRINT_PREFIX",
    "IdentityInputMissing",
    "ScientificIdentity",
    "config_digest",
    "content_sha256",
    "current_config_digest",
    "identity_fingerprint",
    "inputs_not_newer_than",
    "resolve_stored_location",
]

IDENTITY_SCHEMA: Final = 2
FINGERPRINT_PREFIX: Final = "v2:"

#: Resource/spec keys whose value is a file LOCATION → replaced by content sha256.
_IDENTITY_ARTIFACT_KEYS: Final[frozenset[str]] = frozenset(
    {"freq_log_path", "geometry_file", "hessian_file", "coordinates_file"}
)

#: Execution-domain keys never part of the science identity.
_EXECUTION_DOMAIN_KEYS: Final[frozenset[str]] = frozenset(
    {"output_dir", "config", "nproc", "mem", "maxcore", "timeout_s"}
)


class IdentityInputMissing(ValueError):  # noqa: N818 — name pinned by D06 plan contract
    """An identity input file (item structure or declared artifact) is unreadable."""


# ── content binding ──────────────────────────────────────────────────────


def content_sha256(path: Path | str, *, location_root: Path | str | None = None) -> str:
    """Return the sha256 hex digest of *path* content (no location binding)."""
    target = Path(path)
    if not target.is_absolute() and location_root is not None:
        target = Path(location_root) / target
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise IdentityInputMissing(f"identity input not readable: {target}") from exc
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path, *, location_root: Path | str | None = None) -> bytes:
    target = path if path.is_absolute() or location_root is None else Path(location_root) / path
    try:
        return target.read_bytes()
    except OSError as exc:
        raise IdentityInputMissing(f"identity input not readable: {target}") from exc


# ── canonicalisation (sorted keys, None omitted, no str()/repr) ──────────


def _canonical(value: Any, *, location_root: Path | str | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        raise IdentityInputMissing(
            f"path value {value!s} must be declared in _IDENTITY_ARTIFACT_KEYS "
            "or _EXECUTION_DOMAIN_KEYS — file locations never enter the hash"
        )
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, raw in sorted(((str(k), v) for k, v in value.items()), key=lambda kv: kv[0]):
            if raw is None or key in _EXECUTION_DOMAIN_KEYS:
                continue
            if key in _IDENTITY_ARTIFACT_KEYS and isinstance(raw, str):
                out[key] = {"content_sha256": content_sha256(raw, location_root=location_root)}
            else:
                out[key] = _canonical(raw, location_root=location_root)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonical(entry, location_root=location_root) for entry in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(entry, location_root=location_root) for entry in value), key=str)
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value), location_root=location_root)
    raise IdentityInputMissing(
        f"cannot canonicalise {type(value).__name__} for identity — repr/str hashing is forbidden"
    )


def identity_fingerprint(payload: Mapping[str, Any]) -> str:
    """``"v2:" + sha256(canonical_json)[:32]`` over *payload*."""
    blob = json.dumps(
        _canonical(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return FINGERPRINT_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


# ── identity object (payload shape; plan/step computation stays in ACP) ──


@dataclass(frozen=True)
class ScientificIdentity:
    """Two-layer science identity for one calculation plan."""

    plan_identity: str
    step_identities: tuple[str, ...]
    items: tuple[dict[str, Any], ...]
    steps: tuple[dict[str, Any], ...]
    path_remaps: tuple[dict[str, Any], ...]

    def payload(self) -> dict[str, Any]:
        """Full JSON-safe identity record (checkpoint / step_result binding)."""
        return {
            "identity_schema": IDENTITY_SCHEMA,
            "plan_identity": self.plan_identity,
            "step_identities": list(self.step_identities),
            "items": [dict(item) for item in self.items],
            "steps": [dict(step) for step in self.steps],
            "path_remaps": [dict(remap) for remap in self.path_remaps],
        }


# ── attempt metadata (NOT part of the science hash) ──────────────────────


def config_digest(config: Mapping[str, Any] | None) -> str | None:
    """Digest of a resolved config — attempt metadata, never science hash."""
    if config is None:
        return None
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def current_config_digest() -> str | None:
    """Digest of the currently resolved SCIENCE config (``None`` if unreadable).

    Attempt metadata only, so it must describe the science an attempt ran —
    never the platform domain (``cluster``: remote nodes, credentials, queue,
    execution mode).  ``cluster`` is passively merged, owned by the Services
    layer and orthogonal to a calculation's science (see
    ``cccp.config.split_config_domains``); hashing it made adoption flap on a
    credential rotation or a local platform overlay (same science, different
    digest) and forced a needless conservative recompute.
    """
    from cccp.config import load_science_config

    try:
        return config_digest(load_science_config())
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        logger.warning("identity: config digest unavailable; treating as unknown", exc_info=True)
        return None


# ── location resolution / legacy reuse preconditions ─────────────────────


def resolve_stored_location(stored: str | None, fallback: str | None) -> str | None:
    """Prefer the checkpoint-stored location; else the current record fallback.

    The fallback is the location re-resolved from the current
    ``record.work_dir`` (batch) or the current item path (executor).
    """
    if isinstance(stored, str) and stored:
        return stored
    if isinstance(fallback, str) and fallback:
        return fallback
    return None


def inputs_not_newer_than(
    checkpoint_file: Path | str,
    input_locations: Sequence[Path | str],
) -> bool:
    """Legacy-reuse precondition: every input's mtime <= the checkpoint's.

    Missing inputs fail closed (not reusable).  Used together with
    ``load_checkpoint(..., allow_legacy_fingerprint=True)`` before adopting
    a schema=1 checkpoint whose completed artifacts are still present.
    """
    try:
        checkpoint_mtime = Path(checkpoint_file).stat().st_mtime
    except OSError:
        return False
    for location in input_locations:
        try:
            input_mtime = Path(location).stat().st_mtime
        except OSError:
            return False
        if input_mtime > checkpoint_mtime:
            return False
    return True
