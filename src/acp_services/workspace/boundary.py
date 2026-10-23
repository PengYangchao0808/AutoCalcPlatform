"""Local-single security boundary — Origin/CSRF decision + tier capability gate.

B01-5 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 10).  Two
independent, *pure* decisions live here; the HTTP wiring that consumes them
stays in :mod:`acp.api.server`:

1. :func:`evaluate_request` / :class:`OriginPolicy` — the Origin + CSRF guard for
   **state-changing** HTTP methods.  It must never break the three legitimate
   write channels of the ``local-single`` tier:

   * a **legacy same-origin page** (the current Workbench JS is not upgraded but
     still writes — browsers send a same-origin ``Origin`` whose host matches the
     ``Host`` header, so it is allowed);
   * an **authorized no-Origin CLI/machine request** (``curl``, a scheduler
     client, an integration test — no browser, so no forged ``Origin``; allowed
     only when the request is recognisably local/authorized);
   * an **event (SSE) connection** (a ``GET`` — always non-state-changing and
     therefore never blocked).

   A cross-site / forged ``Origin`` is denied with a 403-mappable code.  There is
   **no CORS widening** here: this module only ever *denies*, it never emits
   ``Access-Control-Allow-*``.

2. :func:`require_tier_capabilities` / :func:`evaluate_tier_request` — refuse to
   enable the ``lab`` / ``hosted`` deployment tiers when the required
   authorisation/isolation capabilities are absent, with typed error codes ready
   for the B08 error contract.  The deployment tier is a **process/config**
   fact: a request can never downgrade (or otherwise override) it.

Dependency direction
--------------------
``src/acp_services/**`` must never import ``acp`` (see
``tests/baseline/services-evidence/public_allowlist.md``).  This module imports
only the stdlib plus the frozen :mod:`acp_services.contracts.access` vocabulary
(itself stdlib-only).  It contains no HTTP framework import — it is the pure
decision core the server calls.

Deferred (NOT implemented here)
-------------------------------
Full login/sessions and CSRF token issuance belong to B09 / todo 73.  This slice
supplies the stateless Origin boundary only; there is no session store and no
per-session anti-CSRF token.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from acp_services.contracts.access import (
    DeploymentTier,
    is_loopback_host,
)

__all__ = [
    # Boundary vocabulary
    "BoundaryReason",
    "BoundaryDecision",
    "OriginPolicy",
    "evaluate_request",
    "origin_matches_host",
    "DENY_HTTP_STATUS",
    "ALLOW_HTTP_STATUS",
    "SAFE_METHODS",
    "LOCAL_CLI_HEADER",
    "AUTHORIZATION_HEADER",
    "API_KEY_HEADER",
    # Tier capability gate
    "TierCapability",
    "TierErrorCode",
    "TierVerdict",
    "TierCapabilityError",
    "require_tier_capabilities",
    "evaluate_tier_request",
    "tier_capability_verdict",
    "tier_is_downgrade",
]

# ---------------------------------------------------------------------------
# Origin / CSRF boundary
# ---------------------------------------------------------------------------

ALLOW_HTTP_STATUS = 200
DENY_HTTP_STATUS = 403

#: HTTP methods that are non-state-changing and therefore CSRF-safe.  ``GET``
#: covers the SSE event stream; ``OPTIONS`` covers browser preflight probes even
#: though this slice adds no CORS.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

#: Header a local CLI / scheduler client sets to mark an intentional no-Origin
#: write (e.g. ``X-ACP-CLI: 1``).  A browser page cannot set this on a
#: cross-site request without a CORS preflight, so it is a trustworthy local
#: marker.
LOCAL_CLI_HEADER = "x-acp-cli"

AUTHORIZATION_HEADER = "authorization"
API_KEY_HEADER = "x-api-key"

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


class BoundaryReason(str):
    """Typed reason codes for an Origin/CSRF decision.

    Values are plain strings so they round-trip through JSON and compare
    directly; the deny values are the ``code`` handed to the B08 error contract.
    """

    # allow
    SAFE_METHOD = "safe_method"
    EVENT_STREAM = "event_stream"
    SAME_ORIGIN = "same_origin"
    NO_ORIGIN_AUTHORIZED = "no_origin_authorized"
    # deny
    CROSS_ORIGIN = "cross_origin"
    NULL_ORIGIN = "null_origin"
    ORIGIN_MALFORMED = "origin_malformed"
    NO_ORIGIN_UNAUTHORIZED = "no_origin_unauthorized"
    ALL = frozenset(
        {
            SAFE_METHOD,
            EVENT_STREAM,
            SAME_ORIGIN,
            NO_ORIGIN_AUTHORIZED,
            CROSS_ORIGIN,
            NULL_ORIGIN,
            ORIGIN_MALFORMED,
            NO_ORIGIN_UNAUTHORIZED,
        }
    )


@dataclass(frozen=True)
class BoundaryDecision:
    """One Origin/CSRF verdict.

    ``allowed`` is the gate result; ``http_status`` is the mappable HTTP status
    (``200`` allow / ``403`` deny) and ``reason_code`` the typed B08-ready code.
    """

    allowed: bool
    reason_code: str
    http_status: int = ALLOW_HTTP_STATUS
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "http_status": self.http_status,
            "detail": self.detail,
        }


def _allow(reason: str, detail: str = "") -> BoundaryDecision:
    return BoundaryDecision(True, reason, ALLOW_HTTP_STATUS, detail)


def _deny(reason: str, detail: str = "") -> BoundaryDecision:
    return BoundaryDecision(False, reason, DENY_HTTP_STATUS, detail)


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_host_header(host: str) -> tuple[str | None, int | None]:
    """Split a ``Host`` header into ``(hostname, port)``; port ``None`` = default.

    Handles bracketed IPv6 literals (``[::1]:8765``).
    """
    text = host.strip()
    if not text:
        return None, None
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return None, None
        hostname = text[1:end].lower()
        rest = text[end + 1 :]
        if rest.startswith(":"):
            return hostname, _safe_int(rest[1:])
        return hostname, None
    if text.count(":") == 1:
        name, _, port_text = text.partition(":")
        return name.lower(), _safe_int(port_text)
    return text.lower(), None


def origin_matches_host(origin: str | None, host: str | None, server_scheme: str = "http") -> bool:
    """Return whether *origin* denotes the same origin as the server.

    A missing/opaque (``null``) or malformed origin never matches.  The origin
    scheme must equal *server_scheme*, and both ports are normalised through the
    scheme default, so ``http://h`` matches a bare ``Host: h`` while an explicit
    ``:8765`` must match the Host port exactly.
    """
    if not origin:
        return False
    text = origin.strip()
    if not text or text.lower() == "null":
        return False
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    if not parsed.scheme or not parsed.hostname:
        return False
    scheme = server_scheme.strip().lower() or "http"
    if parsed.scheme.lower() != scheme:
        return False
    try:
        origin_port = parsed.port
    except ValueError:
        return False
    if origin_port is None:
        origin_port = _DEFAULT_PORTS.get(scheme)
    host_name, host_port = _split_host_header(host or "")
    if host_name is None:
        return False
    if host_port is None:
        host_port = _DEFAULT_PORTS.get(scheme)
    return parsed.hostname.lower() == host_name and origin_port == host_port


def _is_authorized_machine(
    *,
    has_auth_header: bool,
    local_cli_marker: bool,
    client_host: str | None,
    server_is_loopback: bool,
    tier: str,
) -> bool:
    """Whether a no-Origin request is a recognised authorized CLI/machine call.

    Documented detection, most-specific first:

    * an explicit auth header (API key / bearer) or the local CLI marker; then
    * a loopback client address; then
    * the ``local-single`` tier bound to loopback — by construction only local
      machine clients can reach it, and browsers issue ``Origin`` on
      state-changing requests, so a no-Origin write is a CLI/machine caller.
    """
    if local_cli_marker or has_auth_header:
        return True
    if is_loopback_host(client_host):
        return True
    return str(tier) == DeploymentTier.LOCAL_SINGLE and server_is_loopback


def evaluate_request(
    *,
    method: str,
    origin: str | None,
    host: str | None,
    is_event_stream: bool = False,
    has_auth_header: bool = False,
    local_cli_marker: bool = False,
    client_host: str | None = None,
    server_is_loopback: bool = True,
    server_scheme: str = "http",
    tier: str = DeploymentTier.LOCAL_SINGLE,
) -> BoundaryDecision:
    """Decide whether one request may proceed (Origin/CSRF guard).

    Args:
        method: HTTP method (case-insensitive).
        origin: ``Origin`` header value (``None`` when absent).
        host: ``Host`` header value (``None`` when absent).
        is_event_stream: True for an SSE connection (diagnostic reason only;
            SSE is a ``GET`` and thus already safe).
        has_auth_header: Any recognised auth credential is present.
        local_cli_marker: The explicit local CLI marker header is present.
        client_host: Remote peer address (loopback detection).
        server_is_loopback: The server is bound to a loopback interface.
        server_scheme: The scheme the request arrived on (same-origin compare).
        tier: The **configured** deployment tier (never request-supplied).

    Returns:
        A :class:`BoundaryDecision`; denies carry :data:`DENY_HTTP_STATUS`.
    """
    verb = (method or "").strip().upper()
    if verb in SAFE_METHODS:
        return _allow(
            BoundaryReason.EVENT_STREAM if is_event_stream else BoundaryReason.SAFE_METHOD,
            f"{verb} is a non-state-changing method",
        )

    if origin is None or not origin.strip():
        if _is_authorized_machine(
            has_auth_header=has_auth_header,
            local_cli_marker=local_cli_marker,
            client_host=client_host,
            server_is_loopback=server_is_loopback,
            tier=tier,
        ):
            return _allow(
                BoundaryReason.NO_ORIGIN_AUTHORIZED,
                "no Origin on an authorized local CLI/machine request",
            )
        return _deny(
            BoundaryReason.NO_ORIGIN_UNAUTHORIZED,
            "no Origin on an unauthorized request to a state-changing endpoint",
        )

    if origin.strip().lower() == "null":
        return _deny(
            BoundaryReason.NULL_ORIGIN,
            "opaque 'null' Origin cannot be trusted for a state-changing request",
        )

    if origin_matches_host(origin, host, server_scheme):
        return _allow(BoundaryReason.SAME_ORIGIN, "Origin matches Host")

    # Reject a syntactically broken Origin explicitly so the code is actionable.
    try:
        parsed = urlsplit(origin.strip())
    except ValueError:
        return _deny(BoundaryReason.ORIGIN_MALFORMED, "unparseable Origin header")
    if not parsed.scheme or not parsed.hostname:
        return _deny(BoundaryReason.ORIGIN_MALFORMED, "Origin lacks scheme/host")

    return _deny(
        BoundaryReason.CROSS_ORIGIN,
        "Origin does not match the server Host (cross-site write rejected)",
    )


@dataclass(frozen=True)
class OriginPolicy:
    """Configured Origin/CSRF policy bound to one deployed server.

    The tier and loopback binding are process/config facts fixed at startup;
    :meth:`evaluate` only classifies an incoming request against them.
    ``public_origin`` pins a reverse-proxy deployment to its browser-facing
    origin; leaving it unset compares against the direct request's Host/scheme.
    """

    tier: str = DeploymentTier.LOCAL_SINGLE
    server_is_loopback: bool = True
    server_scheme: str = "http"
    public_origin: str | None = None

    def __post_init__(self) -> None:
        if self.public_origin is None:
            return
        try:
            parsed = urlsplit(self.public_origin)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and not any(char.isspace() for char in self.public_origin)
            )
            parsed.port  # Validate the configured port, including its range.
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "ACP_PUBLIC_ORIGIN must be an http(s) origin, e.g. https://acp.example"
            )

    def evaluate(
        self,
        *,
        method: str,
        origin: str | None,
        host: str | None,
        is_event_stream: bool = False,
        has_auth_header: bool = False,
        local_cli_marker: bool = False,
        client_host: str | None = None,
        server_scheme: str | None = None,
    ) -> BoundaryDecision:
        # TLS-terminating proxies can change the scheme and Host seen here.
        # Only deployment configuration may supply the external origin; never
        # infer it from Origin or untrusted X-Forwarded-* request headers.
        if self.public_origin is not None:
            public = urlsplit(self.public_origin)
            host = public.netloc
            server_scheme = public.scheme
        return evaluate_request(
            method=method,
            origin=origin,
            host=host,
            is_event_stream=is_event_stream,
            has_auth_header=has_auth_header,
            local_cli_marker=local_cli_marker,
            client_host=client_host,
            server_is_loopback=self.server_is_loopback,
            server_scheme=(self.server_scheme if server_scheme is None else server_scheme),
            tier=self.tier,
        )


# ---------------------------------------------------------------------------
# Deployment-tier capability gate
# ---------------------------------------------------------------------------


class TierCapability(str):
    """Capabilities a deployment tier may require before it may be enabled."""

    AUTHENTICATION = "authentication"
    ISOLATION = "isolation"
    ALL = frozenset({AUTHENTICATION, ISOLATION})


class TierErrorCode(str):
    """Typed tier-gate errors, ready for the B08 error contract."""

    CAPABILITY_MISSING = "tier_capability_missing"
    TIER_UNKNOWN = "tier_unknown"
    TIER_DOWNGRADE_ATTEMPT = "tier_downgrade_attempt"
    TIER_OVERRIDE_ATTEMPT = "tier_override_attempt"
    ALL = frozenset(
        {
            CAPABILITY_MISSING,
            TIER_UNKNOWN,
            TIER_DOWNGRADE_ATTEMPT,
            TIER_OVERRIDE_ATTEMPT,
        }
    )


#: ``local-single`` is the trusted single-user tier: no auth/isolation
#: capability is required.  ``lab`` / ``hosted`` are publicly reachable and must
#: not be enabled until both authorisation and isolation exist.
_REQUIRED_TIER_CAPABILITIES: dict[str, frozenset[str]] = {
    DeploymentTier.LOCAL_SINGLE: frozenset(),
    DeploymentTier.LAB: frozenset({TierCapability.AUTHENTICATION, TierCapability.ISOLATION}),
    DeploymentTier.HOSTED: frozenset({TierCapability.AUTHENTICATION, TierCapability.ISOLATION}),
}

_TIER_RANK: dict[str, int] = {
    DeploymentTier.LOCAL_SINGLE: 0,
    DeploymentTier.LAB: 1,
    DeploymentTier.HOSTED: 2,
}


@dataclass(frozen=True)
class TierVerdict:
    """Result of a tier capability / downgrade evaluation."""

    allowed: bool
    tier: str
    effective_tier: str
    missing_capabilities: tuple[str, ...] = ()
    error_code: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "tier": self.tier,
            "effective_tier": self.effective_tier,
            "missing_capabilities": list(self.missing_capabilities),
            "error_code": self.error_code,
            "detail": self.detail,
        }


class TierCapabilityError(ValueError):
    """Raised when a tier may not be enabled (missing capability / unknown tier).

    Carries the machine-readable :attr:`code` (a :class:`TierErrorCode`) plus the
    :attr:`tier` and :attr:`missing` capabilities so the B08 error contract can
    map it without string parsing.
    """

    def __init__(
        self,
        tier: str,
        missing: tuple[str, ...] = (),
        *,
        code: str = TierErrorCode.CAPABILITY_MISSING,
        detail: str = "",
    ) -> None:
        self.tier = str(tier)
        self.missing = tuple(missing)
        self.code = code
        self.detail = detail or self._default_detail()
        super().__init__(self.detail)

    def _default_detail(self) -> str:
        if self.code == TierErrorCode.TIER_UNKNOWN:
            return f"unknown deployment tier {self.tier!r} cannot be enabled"
        if self.code == TierErrorCode.TIER_DOWNGRADE_ATTEMPT:
            return f"request attempted to downgrade deployment tier {self.tier!r}"
        if self.code == TierErrorCode.TIER_OVERRIDE_ATTEMPT:
            return f"request attempted to override deployment tier {self.tier!r}"
        joined = ", ".join(self.missing) or "required capabilities"
        return f"deployment tier {self.tier!r} refused: missing {joined}"

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "tier": self.tier,
            "missing_capabilities": list(self.missing),
            "detail": self.detail,
        }


def tier_capability_verdict(tier: str, capabilities: object = ()) -> TierVerdict:
    """Non-raising form of :func:`require_tier_capabilities`.

    An unknown tier is denied ``tier_unknown``; a known tier with missing
    required capabilities is denied ``tier_capability_missing``.
    """
    tier_name = str(tier)
    provided = {str(cap) for cap in (capabilities or ())}
    required = _REQUIRED_TIER_CAPABILITIES.get(tier_name)
    if required is None:
        return TierVerdict(
            allowed=False,
            tier=tier_name,
            effective_tier=tier_name,
            error_code=TierErrorCode.TIER_UNKNOWN,
            detail=f"unknown deployment tier {tier_name!r}",
        )
    missing = tuple(sorted(required - provided))
    if missing:
        return TierVerdict(
            allowed=False,
            tier=tier_name,
            effective_tier=tier_name,
            missing_capabilities=missing,
            error_code=TierErrorCode.CAPABILITY_MISSING,
            detail=f"deployment tier {tier_name!r} requires capabilities: {', '.join(missing)}",
        )
    return TierVerdict(allowed=True, tier=tier_name, effective_tier=tier_name)


def require_tier_capabilities(tier: str, capabilities: object = ()) -> None:
    """Refuse to enable *tier* unless its required capabilities are present.

    ``local-single`` requires nothing; ``lab`` / ``hosted`` require both
    :data:`TierCapability.AUTHENTICATION` and :data:`TierCapability.ISOLATION`.
    An unknown tier is always refused.

    Raises:
        TierCapabilityError: with a typed :attr:`~TierCapabilityError.code`.
    """
    verdict = tier_capability_verdict(tier, capabilities)
    if not verdict.allowed:
        raise TierCapabilityError(
            verdict.tier,
            verdict.missing_capabilities,
            code=verdict.error_code or TierErrorCode.CAPABILITY_MISSING,
            detail=verdict.detail,
        )


def tier_is_downgrade(requested_tier: str, configured_tier: str) -> bool:
    """Whether *requested_tier* ranks below *configured_tier*."""
    requested_rank = _TIER_RANK.get(str(requested_tier))
    configured_rank = _TIER_RANK.get(str(configured_tier))
    if requested_rank is None or configured_rank is None:
        return False
    return requested_rank < configured_rank


def evaluate_tier_request(
    *,
    configured_tier: str,
    requested_tier: str | None = None,
    capabilities: object = (),
) -> TierVerdict:
    """Evaluate the configured tier, refusing request-supplied overrides.

    The deployment tier is a process/config fact: a request may never downgrade
    it (or change it at all).  Any mismatch is denied before the capability
    check, so a request cannot smuggle a lower tier past the capability gate.
    """
    configured = str(configured_tier)
    if requested_tier is not None and str(requested_tier) != configured:
        downgrade = tier_is_downgrade(str(requested_tier), configured)
        code = (
            TierErrorCode.TIER_DOWNGRADE_ATTEMPT
            if downgrade
            else TierErrorCode.TIER_OVERRIDE_ATTEMPT
        )
        detail = (
            "request attempted to downgrade the configured deployment tier "
            f"{configured!r} to {requested_tier!r}; the tier is process/config-only"
            if downgrade
            else f"request attempted to override the configured deployment tier {configured!r}"
        )
        return TierVerdict(
            allowed=False,
            tier=configured,
            effective_tier=configured,
            error_code=code,
            detail=detail,
        )
    return tier_capability_verdict(configured, capabilities)
