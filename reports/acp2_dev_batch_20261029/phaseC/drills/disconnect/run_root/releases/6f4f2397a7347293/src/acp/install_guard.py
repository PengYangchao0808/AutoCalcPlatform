"""Mutually exclusive ACP release distributions (M5-03 finding F2).

The root ``acp`` distribution owns ``cccp`` + ``acp`` + ``acp_services`` in one
wheel; the three role distributions (``cccp``, ``acp-worker``, ``acp-web``) are
its **mutually exclusive** alternative.  ``pip`` happily installs both sets side
by side because the shared modules are byte-identical: the second install
silently overwrites the first one's files, which leaves a tree whose ownership
no ``pip uninstall``/upgrade can resolve.  The M5-01 acceptance only refused
this in the *acceptance script*; the product itself never checked, so an
operator could run a mixed environment believing the release guard existed.

This module is the single implementation point for the runtime refusal.  It is
called once per process start by the three product entry points:

* ``acp`` CLI ``main`` (covers ``run``/``doctor``/``init``/``task``/``mcp``),
* ``acp.api.server.create_app`` (covers ``acp run serve`` and any ASGI host),
* the MCP stdio adapter entry (``acp mcp serve``).

The refusal is *typed*: a :class:`MutuallyExclusiveDistributions` exception with
the stable code ``mutually_exclusive_distributions`` and exit code 78
(``EX_CONFIG``) for CLI entry points.  Nothing here imports a distribution
module: only ``importlib.metadata`` names are read, so the check costs one
site-packages scan and cannot trigger optional extras.
"""

from __future__ import annotations

import importlib.metadata as importlib_metadata
import sys
from typing import Final, TextIO

#: ``sysexits.h`` EX_CONFIG — the installed distribution set is not a valid
#: configuration for this product.
EXIT_CODE: Final[int] = 78
#: Stable machine-readable refusal code (also present in every message).
ERROR_CODE: Final[str] = "mutually_exclusive_distributions"

#: Root distribution -> the modules it owns; roles -> the modules they own.
#: Names are compared after ``lower()`` + ``_``/``.`` -> ``-`` normalisation so
#: the check is insensitive to importlib.metadata's spelling.
DISTRIBUTION_GROUPS: Final[dict[str, frozenset[str]]] = {
    "root": frozenset({"acp"}),
    "roles": frozenset({"cccp", "acp-worker", "acp-web"}),
}

_MESSAGE = (
    f"{ERROR_CODE}: the root 'acp' distribution and the role distributions "
    "({roles}) are mutually exclusive release sets, but this environment has "
    "both installed (root: {root_found}; roles: {roles_found}). "
    "The two sets ship byte-identical copies of the shared modules, so a mixed "
    "install silently overwrites files and breaks maintainability and the "
    "upgrade path (neither pip uninstall nor an upgrade can restore a "
    "consistent tree). Install either 'acp' or the three roles in a fresh "
    "environment; uninstall the other set first (pip uninstall acp acp-web "
    "acp-worker cccp)."
)


def _normalize(name: str) -> str:
    """PEP 503-style normalisation for distribution names."""
    return name.strip().lower().replace("_", "-").replace(".", "-")


class MutuallyExclusiveDistributions(RuntimeError):  # noqa: N818 - domain term
    """Typed refusal: root ``acp`` and the role set are installed together.

    Attributes:
        code: Stable machine-readable code (``mutually_exclusive_distributions``).
        exit_code: Process exit code used by CLI entry points (78).
        groups: Installed names per release group, e.g.
            ``{"root": ["acp"], "roles": ["acp-web", "acp-worker", "cccp"]}``.
    """

    code: Final[str] = ERROR_CODE
    exit_code: Final[int] = EXIT_CODE

    def __init__(self, groups: dict[str, list[str]]) -> None:
        self.groups = {name: sorted(values) for name, values in groups.items()}
        self.message = _MESSAGE.format(
            roles=", ".join(sorted(DISTRIBUTION_GROUPS["roles"])),
            root_found=", ".join(self.groups.get("root", [])) or "none",
            roles_found=", ".join(self.groups.get("roles", [])) or "none",
        )
        super().__init__(self.message)


def installed_distribution_names() -> set[str]:
    """Every installed distribution name, normalised (metadata only)."""
    names: set[str] = set()
    for distribution in importlib_metadata.distributions():
        try:
            raw = distribution.metadata["Name"]
        except (KeyError, TypeError):  # malformed dist-info: not our decision
            continue
        if raw:
            names.add(_normalize(str(raw)))
    return names


def installed_release_groups() -> dict[str, list[str]]:
    """Installed release groups (only groups with at least one member)."""
    names = installed_distribution_names()
    return {
        group: sorted(names & members)
        for group, members in DISTRIBUTION_GROUPS.items()
        if names & members
    }


def check_install() -> MutuallyExclusiveDistributions | None:
    """Return the typed refusal when both release sets are installed."""
    groups = installed_release_groups()
    if len(groups) > 1:
        return MutuallyExclusiveDistributions(groups)
    return None


def ensure_exclusive_install() -> None:
    """Raise :class:`MutuallyExclusiveDistributions` on a mixed install.

    Raises:
        MutuallyExclusiveDistributions: root ``acp`` and any role distribution
            are installed in the same environment.
    """
    refusal = check_install()
    if refusal is not None:
        raise refusal


def cli_guard(stream: TextIO | None = None) -> int | None:
    """CLI entry helper: print the refusal and return its exit code, else None."""
    refusal = check_install()
    if refusal is None:
        return None
    print(refusal.message, file=stream or sys.stderr)
    return EXIT_CODE


__all__ = [
    "DISTRIBUTION_GROUPS",
    "ERROR_CODE",
    "EXIT_CODE",
    "MutuallyExclusiveDistributions",
    "check_install",
    "cli_guard",
    "ensure_exclusive_install",
    "installed_distribution_names",
    "installed_release_groups",
]
