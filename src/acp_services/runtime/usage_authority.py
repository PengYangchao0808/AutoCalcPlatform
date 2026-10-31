"""Trusted usage producer authority (M0-07 / G26).

Settlement used to accept a caller-supplied ``trusted_source`` string, which
made the receipt's *authority* a request field.  That is not an authorization
fact: a project-scoped ``write`` API key could claim ``worker-receipt`` and post
arbitrary usage against a budget reservation.  This module is the one place
that decides **who may produce trusted usage**, and it derives the trusted
source from a server-side producer instead of trusting the wire.

Rules
-----

* A producer is one of :class:`UsageProducer` — ``worker`` / ``service`` /
  ``proxy`` for external execution evidence, ``internal`` for the Controller's
  own terminal receipt (``controller-terminal-receipt``).
* External producers are granted through a **dedicated credential capability**
  (``usage.produce.<producer>``) stored on the service credential.  It is
  additive metadata next to the existing ``AccessAction`` ceiling: granting or
  withholding it never changes read/write/control semantics.
* ``controller-terminal-receipt`` is **internal-only**.  It is not an
  assignable capability, so no HTTP credential (and no ordinary project write
  key) can select it; :func:`normalize_usage_capabilities` refuses to store it.
* The trusted source recorded on a receipt is always
  :func:`source_for_producer` of the resolved producer — never a body field.

Dependency direction: stdlib only.  This module must stay importable from
``workspace/keys.py`` (identity DB) and ``runtime/admission.py`` (settlement
ledger) without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "CAPABILITY_PREFIX",
    "CONTROLLER_TERMINAL_SOURCE",
    "UsageAuthority",
    "UsageProducer",
    "UsageSource",
    "UntrustedUsageProducerError",
    "ASSIGNABLE_CAPABILITIES",
    "authority_for_capabilities",
    "capability_for_producer",
    "is_assignable_capability",
    "normalize_usage_capabilities",
    "producer_for_capability",
    "require_producer_capability",
    "resolve_authority",
    "source_for_producer",
]


class UsageProducer(str):
    """Who produced a usage observation.

    ``INTERNAL`` is the Controller's own terminal observation.  It is not
    reachable from a credential: the settlement ledger must be driven by the
    Controller process itself for that producer.
    """

    WORKER = "worker"
    SERVICE = "service"
    PROXY = "proxy"
    INTERNAL = "internal"

    EXTERNAL = frozenset({WORKER, SERVICE, PROXY})
    ALL = frozenset({WORKER, SERVICE, PROXY, INTERNAL})


class UsageSource(str):
    """Trusted receipt sources recorded on a usage/budget receipt row."""

    WORKER_RECEIPT = "worker-receipt"
    SERVICE_RECEIPT = "service-receipt"
    PROXY_USAGE = "c-proxy-usage"
    CONTROLLER_TERMINAL = "controller-terminal-receipt"

    EXTERNAL = frozenset({WORKER_RECEIPT, SERVICE_RECEIPT, PROXY_USAGE})
    INTERNAL = frozenset({CONTROLLER_TERMINAL})
    ALL = EXTERNAL | INTERNAL


#: ``controller-terminal-receipt`` is the internal source; it is intentionally
#: absent from the assignable capability set.
CONTROLLER_TERMINAL_SOURCE = UsageSource.CONTROLLER_TERMINAL

#: Dedicated capability metadata prefix for external producers.
CAPABILITY_PREFIX = "usage.produce."

_PRODUCER_SOURCES: dict[str, str] = {
    UsageProducer.WORKER: UsageSource.WORKER_RECEIPT,
    UsageProducer.SERVICE: UsageSource.SERVICE_RECEIPT,
    UsageProducer.PROXY: UsageSource.PROXY_USAGE,
    UsageProducer.INTERNAL: UsageSource.CONTROLLER_TERMINAL,
}

#: Producer -> capability token.  ``internal`` has **no** token by design.
_PRODUCER_CAPABILITIES: dict[str, str] = {
    UsageProducer.WORKER: CAPABILITY_PREFIX + UsageProducer.WORKER,
    UsageProducer.SERVICE: CAPABILITY_PREFIX + UsageProducer.SERVICE,
    UsageProducer.PROXY: CAPABILITY_PREFIX + UsageProducer.PROXY,
}

_CAPABILITY_PRODUCERS: dict[str, str] = {
    capability: producer for producer, capability in _PRODUCER_CAPABILITIES.items()
}

#: Capabilities that may be stored on a service credential.  Internal is never
#: assignable (this is the ``controller-terminal-receipt`` prohibition).
ASSIGNABLE_CAPABILITIES: frozenset[str] = frozenset(_PRODUCER_CAPABILITIES.values())


class UntrustedUsageProducerError(ValueError):
    """A producer/capability tuple may not write trusted usage (fail closed)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class UsageAuthority:
    """The server-derived settlement authority of one caller.

    ``internal`` is ``True`` only for the Controller's own terminal receipt; it
    is never derived from a credential capability.
    """

    producer: str
    trusted_source: str
    capability: str = ""
    internal: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "producer": self.producer,
            "trusted_source": self.trusted_source,
            "capability": self.capability,
            "internal": self.internal,
        }


def capability_for_producer(producer: str) -> str:
    """Return the credential capability token for an external producer."""
    token = str(producer or "").strip().lower()
    capability = _PRODUCER_CAPABILITIES.get(token)
    if capability is None:
        raise UntrustedUsageProducerError(
            "usage_producer_untrusted",
            f"producer {producer!r} has no assignable usage capability",
        )
    return capability


def producer_for_capability(capability: str) -> str | None:
    """Return the producer a capability grants, or ``None`` for an unknown token."""
    return _CAPABILITY_PRODUCERS.get(str(capability or "").strip().lower())


def is_assignable_capability(capability: str) -> bool:
    """True only for a capability that may be stored on a service credential."""
    return str(capability or "").strip().lower() in ASSIGNABLE_CAPABILITIES


def normalize_usage_capabilities(values: Iterable[str] | str | None) -> tuple[str, ...]:
    """Normalise and validate credential usage capabilities (fail closed).

    Unknown tokens and the internal ``controller-terminal-receipt`` source are
    refused rather than dropped silently, so a misconfigured grant can never be
    mistaken for "no grant".
    """
    if values is None:
        return ()
    items = [values] if isinstance(values, str) else list(values)
    ordered: list[str] = []
    for item in items:
        token = str(item or "").strip().lower()
        if not token:
            continue
        if token == UsageSource.CONTROLLER_TERMINAL:
            raise UntrustedUsageProducerError(
                "usage_capability_not_assignable",
                "controller-terminal-receipt is an internal-only source and cannot be assigned",
            )
        if not is_assignable_capability(token):
            raise UntrustedUsageProducerError(
                "usage_capability_unknown",
                f"{token!r} is not an assignable usage capability; "
                f"allowed: {sorted(ASSIGNABLE_CAPABILITIES)}",
            )
        if token not in ordered:
            ordered.append(token)
    return tuple(ordered)


def source_for_producer(producer: str) -> str:
    """Return the trusted source derived for *producer* (never a wire value)."""
    token = str(producer or "").strip().lower()
    source = _PRODUCER_SOURCES.get(token)
    if source is None:
        raise UntrustedUsageProducerError(
            "usage_producer_untrusted", f"unknown usage producer {producer!r}"
        )
    return source


def resolve_authority(*, producer: str) -> UsageAuthority:
    """Resolve a producer into its server-side authority (source + capability)."""
    token = str(producer or "").strip().lower()
    if token not in UsageProducer.ALL:
        raise UntrustedUsageProducerError(
            "usage_producer_untrusted",
            f"unknown usage producer {producer!r}; trusted producers are "
            f"{sorted(UsageProducer.ALL)}",
        )
    source = source_for_producer(token)
    if token == UsageProducer.INTERNAL:
        return UsageAuthority(producer=token, trusted_source=source, internal=True)
    return UsageAuthority(
        producer=token,
        trusted_source=source,
        capability=_PRODUCER_CAPABILITIES[token],
    )


def authority_for_capabilities(capabilities: Iterable[str] | None) -> UsageAuthority | None:
    """Resolve a credential's capability tuple into its usage authority.

    Returns ``None`` when the credential carries no usage capability.  More than
    one producer capability is ambiguous for a single receipt and is refused
    (fail closed) instead of guessed.  The internal source is unreachable here.
    """
    producers: list[str] = []
    for capability in capabilities or ():
        producer = producer_for_capability(capability)
        if producer is not None and producer not in producers:
            producers.append(producer)
    if not producers:
        return None
    if len(producers) > 1:
        raise UntrustedUsageProducerError(
            "usage_capability_ambiguous",
            f"credential carries several usage producers {sorted(producers)}; "
            "a receipt must come from exactly one",
        )
    return resolve_authority(producer=producers[0])


def require_producer_capability(
    capabilities: Iterable[str] | None, producer: str
) -> UsageAuthority:
    """Return the producer's authority or raise when the credential lacks it."""
    authority = resolve_authority(producer=producer)
    if authority.internal:
        raise UntrustedUsageProducerError(
            "usage_producer_untrusted",
            "controller-terminal-receipt is internal-only and cannot be selected by a caller",
        )
    held = {str(item).strip().lower() for item in capabilities or ()}
    if authority.capability not in held:
        raise UntrustedUsageProducerError(
            "usage_capability_missing",
            f"credential lacks {authority.capability!r}; a project write key cannot "
            "write trusted usage",
        )
    return authority
