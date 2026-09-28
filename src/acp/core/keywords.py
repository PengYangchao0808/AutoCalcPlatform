"""Case-insensitive keyword translation for ACP CLI and method payloads.

Established computational-chemistry software treats enumerated input keywords
case-insensitively (Gaussian: "Input is free-format and case-insensitive";
ORCA: "The ORCA input is NOT case sensitive"; Q-Chem: "The entire Q-Chem input
is case-insensitive"; Psi4: "All PSIfour keyword names and values are
insensitive to case"; CREST lowercases keys at its ingest boundary).

ACP keeps exactly one canonical spelling per keyword field and folds case only
at the boundaries where user input is interpreted:

* the CLI argument parser (``acp.cli.build_parser``);
* catalog method normalization (``acp.catalog``);
* scheduler flag emission (defence in depth for remote nodes).

Free-form values — method and basis names, solvent names, titles, route extras
and file paths — are never folded; only enumerated ``choices`` are. This mirrors
the documented escape hatches of ORCA (file names are case-sensitive) and Psi4
(``add_str_i`` / ``kwargs_lower`` denylist).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence

__all__ = [
    "canonical_choice",
    "fold_keyword",
    "make_case_insensitive_type",
]


def fold_keyword(value: object) -> str:
    """Return the case/whitespace-folded comparison key for *value*.

    Args:
        value: Any scalar keyword value (str, int, ...).

    Returns:
        The stripped, lower-cased string form used for comparisons.
    """
    return str(value).strip().lower()


def canonical_choice(value: object, choices: Iterable[str]) -> str | None:
    """Resolve *value* to its canonical spelling within *choices*.

    Matching is case-insensitive and whitespace-insensitive; the returned value
    is always one of *choices* verbatim.

    Args:
        value: User-provided keyword spelling (any case).
        choices: Canonical spellings declared by the field/argument.

    Returns:
        The matching canonical choice, or ``None`` when there is no match.
    """
    key = fold_keyword(value)
    for choice in choices:
        if fold_keyword(choice) == key:
            return choice
    return None


def make_case_insensitive_type(
    choices: Sequence[str],
    *,
    field: str | None = None,
) -> Callable[[str], str]:
    """Build an ``argparse`` ``type=`` callable that folds case to a choice.

    ``argparse`` applies ``type`` conversion before validating ``choices``, so
    every case spelling of a valid choice is accepted and normalised to the
    canonical spelling declared by the argument.

    Args:
        choices: Canonical spellings for the argument.
        field: Optional label used in the error message (defaults to "value").

    Returns:
        A callable suitable for ``ArgumentParser.add_argument(type=...)``.
    """
    accepted = tuple(choices)
    label = field or "value"

    def convert(value: str) -> str:
        match = canonical_choice(value, accepted)
        if match is None:
            allowed = ", ".join(repr(choice) for choice in accepted)
            raise argparse.ArgumentTypeError(
                f"{label}: invalid choice: {value!r} (choose from {allowed})"
            )
        return match

    return convert
