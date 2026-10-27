"""M2-04d — ``ExecutionResourcePrecheck``: binaries/versions/assets before dispatch.

``step_requirements`` is deliberately *only* the upstream StepKind/product
prerequisite table.  It must never pretend to be a binary check.  This module is
the missing execution-resource gate, and it runs in the worker composition root
**before** a step is dispatched:

* ``cccp.software.resolve_executable`` resolves every required binary (an
  unresolvable one is ``blocked`` with ``missing_binary`` — never a late
  subprocess ``FileNotFoundError``);
* ``cccp.software.detect_version`` records the actual version; a required but
  unprobeable version is ``blocked`` with ``software_version_missing`` (ADR-008
  §7: a missing version makes the criterion *not evaluated*, never a default
  pass);
* the release manifest is verified against the frozen envelope (``release_id``
  and content digest) — a mismatched or absent manifest is
  ``release_manifest_mismatch`` / ``release_manifest_missing``;
* declared lab capabilities (``runtime.lab_capabilities``) must be available for
  the step, otherwise ``lab_capability_unavailable``.

A blocked result is a **dispatch refusal**: the expensive call is never started
and the step is recorded ``blocked`` (not ``failed``): no science ran.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "PRECHECK_SCHEMA",
    "ResourceBlocker",
    "ResourcePrecheckBlockedError",
    "ResourcePrecheckResult",
    "ResourceRequirement",
    "ExecutionResourcePrecheck",
]

PRECHECK_SCHEMA: Final = "acp2.runtime.execution-resource-precheck.v1"

#: Stable blocker codes (each is a refused dispatch, never a late failure).
BLOCK_MISSING_BINARY: Final = "missing_binary"
BLOCK_VERSION_MISSING: Final = "software_version_missing"
BLOCK_RELEASE_MISSING: Final = "release_manifest_missing"
BLOCK_RELEASE_MISMATCH: Final = "release_manifest_mismatch"
BLOCK_ASSET_MISSING: Final = "asset_missing"
BLOCK_LAB_CAPABILITY: Final = "lab_capability_unavailable"


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    """What one step needs on this node before it may be dispatched."""

    step_id: str
    executables: tuple[str, ...] = ()
    require_version: bool = False
    assets: tuple[str, ...] = ()
    lab_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "executables", tuple(str(item) for item in self.executables if str(item))
        )
        object.__setattr__(self, "assets", tuple(str(item) for item in self.assets if str(item)))
        object.__setattr__(
            self,
            "lab_capabilities",
            tuple(str(item) for item in self.lab_capabilities if str(item)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "executables": list(self.executables),
            "require_version": self.require_version,
            "assets": list(self.assets),
            "lab_capabilities": list(self.lab_capabilities),
        }


@dataclass(frozen=True, slots=True)
class ResourceBlocker:
    """One refused dispatch with a stable code and the offending step."""

    code: str
    step_id: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "step_id": self.step_id, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ResourcePrecheckResult:
    """The precheck verdict (``ok`` only when there is no blocker)."""

    blockers: tuple[ResourceBlocker, ...] = ()
    executables: Mapping[str, str] = field(default_factory=dict)
    versions: Mapping[str, str] = field(default_factory=dict)
    schema: str = PRECHECK_SCHEMA

    @property
    def ok(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "blockers": [blocker.to_dict() for blocker in self.blockers],
            "executables": dict(self.executables),
            "versions": dict(self.versions),
        }


class ResourcePrecheckBlockedError(RuntimeError):
    """A required resource is missing: the dispatch never starts."""

    def __init__(self, result: ResourcePrecheckResult) -> None:
        codes = ", ".join(f"{item.code}@{item.step_id}" for item in result.blockers)
        super().__init__(f"execution resource precheck blocked the dispatch: {codes}")
        self.result = result

    @property
    def blockers(self) -> tuple[ResourceBlocker, ...]:
        return self.result.blockers


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionResourcePrecheck:
    """Pre-dispatch binary/version/asset/lab-capability verification.

    All seams are injectable so a test never spawns a version probe; the
    defaults are the real CCCP resolvers, so production uses one resolution
    authority (``cccp.software``).
    """

    config: Mapping[str, Any] | None = None
    release_dir: Path | None = None
    release_manifest: Mapping[str, Any] | None = None
    expected_release_id: str = ""
    expected_release_digest: str = ""
    lab_report: Mapping[str, Any] | None = None
    resolver: Callable[[str, Any], Path | None] | None = None
    version_probe: Callable[[str, Path | None], str | None] | None = None

    # -- seams -----------------------------------------------------------

    def _resolve(self, name: str) -> Path | None:
        if self.resolver is not None:
            return self.resolver(name, None)
        from cccp.software import get_configured_path, resolve_executable

        configured = get_configured_path(dict(self.config or {}), name)
        return resolve_executable(name, configured_path=configured)

    def _version(self, name: str, executable: Path | None) -> str | None:
        if self.version_probe is not None:
            return self.version_probe(name, executable)
        from cccp.software import detect_version

        return detect_version(name, executable)

    # -- release ---------------------------------------------------------

    def _release_blockers(self) -> list[ResourceBlocker]:
        blockers: list[ResourceBlocker] = []
        manifest = self.release_manifest
        release_dir = Path(self.release_dir) if self.release_dir is not None else None
        if manifest is None and release_dir is not None:
            candidate = release_dir / "release_manifest.json"
            if candidate.is_file():
                try:
                    loaded = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    blockers.append(
                        ResourceBlocker(
                            BLOCK_RELEASE_MISMATCH, "*", f"release manifest unreadable: {exc}"
                        )
                    )
                    return blockers
                manifest = loaded if isinstance(loaded, Mapping) else None
        if self.expected_release_id and manifest is None:
            blockers.append(
                ResourceBlocker(
                    BLOCK_RELEASE_MISSING,
                    "*",
                    f"no release manifest for release {self.expected_release_id!r}",
                )
            )
            return blockers
        if manifest is None:
            return blockers
        observed_id = str(manifest.get("release_id") or "")
        if self.expected_release_id and observed_id != self.expected_release_id:
            blockers.append(
                ResourceBlocker(
                    BLOCK_RELEASE_MISMATCH,
                    "*",
                    f"release_id {observed_id!r} != frozen {self.expected_release_id!r}",
                )
            )
        if self.expected_release_digest:
            observed_digest = str(
                manifest.get("release_digest")
                or manifest.get("digest")
                or manifest.get("content_digest")
                or ""
            )
            if observed_digest and observed_digest != self.expected_release_digest:
                blockers.append(
                    ResourceBlocker(
                        BLOCK_RELEASE_MISMATCH,
                        "*",
                        f"release digest {observed_digest!r} != frozen "
                        f"{self.expected_release_digest!r}",
                    )
                )
        return blockers

    # -- assets / lab ----------------------------------------------------

    def _asset_blockers(self, requirement: ResourceRequirement) -> list[ResourceBlocker]:
        blockers: list[ResourceBlocker] = []
        root = Path(self.release_dir) if self.release_dir is not None else None
        for asset in requirement.assets:
            candidate = Path(asset)
            if not candidate.is_absolute():
                if root is None:
                    blockers.append(
                        ResourceBlocker(
                            BLOCK_ASSET_MISSING,
                            requirement.step_id,
                            f"asset {asset!r} needs a release root to resolve",
                        )
                    )
                    continue
                candidate = root / candidate
            if not candidate.exists():
                blockers.append(
                    ResourceBlocker(
                        BLOCK_ASSET_MISSING,
                        requirement.step_id,
                        f"asset {candidate} is missing",
                    )
                )
        return blockers

    def _lab_blockers(self, requirement: ResourceRequirement) -> list[ResourceBlocker]:
        if not requirement.lab_capabilities:
            return []
        report = self.lab_report or {}
        capabilities = report.get("capabilities") if isinstance(report, Mapping) else None
        blockers: list[ResourceBlocker] = []
        for name in requirement.lab_capabilities:
            entry = None
            if isinstance(capabilities, Mapping):
                entry = capabilities.get(name)
            elif isinstance(capabilities, Sequence) and not isinstance(capabilities, (str, bytes)):
                for item in capabilities:
                    if isinstance(item, Mapping) and str(item.get("name") or "") == name:
                        entry = item
                        break
            state = ""
            if isinstance(entry, Mapping):
                state = str(entry.get("state") or "")
            elif isinstance(entry, str):
                state = entry
            if state not in {"available", "verified", "present"}:
                blockers.append(
                    ResourceBlocker(
                        BLOCK_LAB_CAPABILITY,
                        requirement.step_id,
                        f"lab capability {name!r} is {state or 'absent'} on this node",
                    )
                )
        return blockers

    # -- public API ------------------------------------------------------

    def check(self, requirements: Sequence[ResourceRequirement]) -> ResourcePrecheckResult:
        """Verify every requirement; return the blockers (never raise here)."""
        blockers: list[ResourceBlocker] = []
        executables: dict[str, str] = {}
        versions: dict[str, str] = {}
        blockers.extend(self._release_blockers())
        for requirement in requirements:
            blockers.extend(self._asset_blockers(requirement))
            blockers.extend(self._lab_blockers(requirement))
            for name in requirement.executables:
                resolved = self._resolve(name)
                if resolved is None:
                    blockers.append(
                        ResourceBlocker(
                            BLOCK_MISSING_BINARY,
                            requirement.step_id,
                            f"executable {name!r} cannot be resolved on this node",
                        )
                    )
                    continue
                executables[name] = str(resolved)
                version = self._version(name, resolved)
                if version:
                    versions[name] = str(version)
                elif requirement.require_version:
                    blockers.append(
                        ResourceBlocker(
                            BLOCK_VERSION_MISSING,
                            requirement.step_id,
                            f"executable {name!r} at {resolved} reported no version",
                        )
                    )
        return ResourcePrecheckResult(
            blockers=tuple(blockers), executables=executables, versions=versions
        )

    def require(self, requirements: Sequence[ResourceRequirement]) -> ResourcePrecheckResult:
        """Like :meth:`check` but raise :class:`ResourcePrecheckBlockedError`."""
        result = self.check(requirements)
        if not result.ok:
            raise ResourcePrecheckBlockedError(result)
        return result

    def verify_assets(self, paths: Sequence[Path]) -> ResourcePrecheckResult:
        """Verify already-resolved absolute asset paths (release payload check)."""
        blockers = tuple(
            ResourceBlocker(BLOCK_ASSET_MISSING, "*", f"asset {path} is missing")
            for path in paths
            if not Path(path).exists()
        )
        return ResourcePrecheckResult(blockers=blockers)
