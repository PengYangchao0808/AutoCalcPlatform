"""Standalone contracts for IRC endpoint results and classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, cast

from acp.calculations.contracts import JsonValue


class IrcEndpointArtifact(Protocol):
    """Path-shaped endpoint artifact accepted by IRC results."""

    @property
    def path(self) -> str: ...

    @property
    def sha256(self) -> str: ...

    @property
    def kind(self) -> str: ...


@dataclass(frozen=True, slots=True)
class _IrcEndpointArtifactValue:
    path: str
    sha256: str = ""
    kind: str = "irc_endpoint"


def _endpoint_to_dict(endpoint: IrcEndpointArtifact) -> dict[str, JsonValue]:
    return {"path": endpoint.path, "sha256": endpoint.sha256, "kind": endpoint.kind}


def _endpoint_from_value(value: JsonValue) -> IrcEndpointArtifact | None:
    if not isinstance(value, dict):
        return None
    return _IrcEndpointArtifactValue(
        path=str(value.get("path") or ""),
        sha256=str(value.get("sha256") or ""),
        kind=str(value.get("kind") or "irc_endpoint"),
    )


def _json_object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


@dataclass(frozen=True, slots=True)
class IrcResult:
    """Connectivity result shared by the standalone IRC task and legacy readers."""

    irc_id: str
    ts_id: str
    success: bool
    complete: bool = True
    forward_endpoint: IrcEndpointArtifact | None = None
    reverse_endpoint: IrcEndpointArtifact | None = None
    evidence: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "irc_id": self.irc_id,
            "ts_id": self.ts_id,
            "success": self.success,
            "complete": self.complete,
            "forward_endpoint": (
                _endpoint_to_dict(self.forward_endpoint)
                if self.forward_endpoint is not None
                else None
            ),
            "reverse_endpoint": (
                _endpoint_to_dict(self.reverse_endpoint)
                if self.reverse_endpoint is not None
                else None
            ),
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> IrcResult:
        forward_data = data.get("forward_endpoint")
        reverse_data = data.get("reverse_endpoint")
        return cls(
            irc_id=str(data.get("irc_id") or ""),
            ts_id=str(data.get("ts_id") or ""),
            success=bool(data.get("success", False)),
            complete=bool(data.get("complete", False)),
            forward_endpoint=_endpoint_from_value(forward_data),
            reverse_endpoint=_endpoint_from_value(reverse_data),
            evidence=_json_object(data.get("evidence")),
        )


EndpointVerdict = Literal["MATCH_EXISTING", "NEW_STATE", "AMBIGUOUS", "FAILED"]


@dataclass(frozen=True, slots=True)
class EndpointMatchResult:
    """Endpoint classification verdict for a refined transition state."""

    verdict: EndpointVerdict
    state_id: str | None = None
    evidence: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "verdict": self.verdict,
            "state_id": self.state_id,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> EndpointMatchResult:
        return cls(
            verdict=cast_endpoint_verdict(data.get("verdict")),
            state_id=(str(data.get("state_id")) if data.get("state_id") is not None else None),
            evidence=_json_object(data.get("evidence")),
        )


def cast_endpoint_verdict(value: JsonValue) -> EndpointVerdict:
    """Normalize a serialized endpoint verdict."""
    verdict = str(value or "FAILED").upper()
    if verdict in {"MATCH_EXISTING", "NEW_STATE", "AMBIGUOUS", "FAILED"}:
        return cast(EndpointVerdict, verdict)
    return "FAILED"


__all__ = [
    "EndpointMatchResult",
    "EndpointVerdict",
    "IrcEndpointArtifact",
    "IrcResult",
]
