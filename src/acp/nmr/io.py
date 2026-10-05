# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Experimental NMR input parsing (DevDoc §6.2).

Parses the human-readable text format:

    # 13C (ppm), optional atom assignments in parentheses
    C: 167.33(C1), 59.58(C2), 24.50(C3), 157.42(C8)

    # 1H (ppm), optional assignments
    H: 4.81(H4), 7.18(H5), 3.09(H6)

    # equivalence groups (one per line)
    EQ: C10,C12
    EQ: H15,H16

    # atoms to omit (e.g. labile protons)
    OMIT: H19,H51

For unassigned spectra the parenthesized atom labels are omitted and an
optional multiplicity annotation ``2.95(3)`` declares an integral of 3
(e.g. CH3). When omitted, multiplicity defaults to 1.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path

from acp.nmr.models import ExperimentalNmr, ExperimentalPeak, ParseIssue, normalize_symbol

logger = logging.getLogger(__name__)


# token := shift [ '(' annotation ')' ] [ '(' annotation ')' ]
# annotation := all-digits (multiplicity) | label [(', '|' or ')label]* (candidates)
_TOKEN_RE = re.compile(r"^([-+]?\d+(?:\.\d+)?)\s*(?:\(([^()]*)\))?\s*(?:\(([^()]*)\))?$")
_EQ_LINE_RE = re.compile(r"^(?:EQ|EQUIV)\s*:\s*(.+)$", re.IGNORECASE)
_OMIT_LINE_RE = re.compile(r"^OMIT\s*:\s*(.+)$", re.IGNORECASE)
_NUCLEUS_LINE_RE = re.compile(r"^([A-Za-z]{1,2})\s*:\s*(.+)$")
_LABEL_RE = re.compile(r"^[A-Za-z]{1,2}\d+$")
_LABEL_ELEMENT_RE = re.compile(r"^[A-Za-z]{1,2}")
_LABEL_SPLIT_RE = re.compile(r"\s*(?:,|\bor\b)\s*", re.IGNORECASE)


def _parse_atom_label(raw: str) -> str:
    """Normalize an atom label like ``"c1"`` → ``"C1"``."""
    s = raw.strip()
    if not s:
        return s
    return s[:1].upper() + s[1:].lower() if len(s) == 1 else s[:1].upper() + s[1:]


def _element_of_label(label: str) -> str:
    """Return the element symbol prefix of an atom label (``"H32"`` → ``"H"``)."""
    m = _LABEL_ELEMENT_RE.match(label)
    return normalize_symbol(m.group(0)) if m else ""


def _split_peak_tokens(body: str) -> list[str]:
    """Split a nucleus-line body on commas that are not inside parentheses.

    Falls back to a naive split when parentheses are unbalanced so that
    legacy (malformed) input still yields per-token diagnostics instead of
    swallowing everything after an open paren.
    """
    depth = 0
    parts: list[str] = []
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    if depth != 0:
        return body.split(",")
    return parts


def _parse_peak_token(
    token: str, element: str, issues: list[ParseIssue]
) -> ExperimentalPeak | None:
    """Parse one ``shift[(label-or-mult)]`` token; record issues, never drop silently."""
    m = _TOKEN_RE.match(token)
    if not m:
        issues.append(ParseIssue("unknown_token", "unparseable peak token", token))
        return None

    shift = float(m.group(1))
    atom_label: str | None = None
    candidates: tuple[str, ...] | None = None
    multiplicity = 1
    multiplicity_seen = False

    for group in (m.group(2), m.group(3)):
        if group is None:
            continue
        content = group.strip()
        if not content:
            continue
        if content.isdigit():
            if multiplicity_seen:
                issues.append(
                    ParseIssue("unknown_token", "multiple multiplicity annotations", token)
                )
                continue
            multiplicity = int(content)
            multiplicity_seen = True
            continue
        if candidates is not None:
            issues.append(ParseIssue("unknown_token", "multiple label annotations", token))
            continue
        parts = [p.strip() for p in _LABEL_SPLIT_RE.split(content) if p.strip()]
        if not parts or any(not _LABEL_RE.match(p) for p in parts):
            issues.append(
                ParseIssue("unknown_token", f"unrecognized label annotation {content!r}", token)
            )
            continue
        labels = tuple(_parse_atom_label(p) for p in parts)
        if len(labels) == 1:
            atom_label = labels[0]
            candidates = labels
        else:
            candidates = labels
            issues.append(
                ParseIssue(
                    "ambiguous_label",
                    f"peak may belong to {' or '.join(labels)}; kept as candidate set",
                    token,
                )
            )

    if atom_label is not None:
        label_element = _element_of_label(atom_label)
        if label_element and label_element != element:
            issues.append(
                ParseIssue(
                    "unmatched_peak",
                    f"label {atom_label} ({label_element}) does not belong to "
                    f"nucleus section {element}",
                    token,
                )
            )

    return ExperimentalPeak(
        shift_ppm=shift,
        element=element,
        atom_label=atom_label,
        multiplicity=multiplicity,
        label_candidates=candidates,
    )


def _parse_peaks_in_line(
    element: str, body: str, issues: list[ParseIssue]
) -> list[ExperimentalPeak]:
    """Parse the comma-separated peak list on a ``C:`` / ``H:`` line."""
    peaks: list[ExperimentalPeak] = []
    sym = normalize_symbol(element)
    for raw in _split_peak_tokens(body):
        token = raw.strip()
        if not token:
            continue
        peak = _parse_peak_token(token, sym, issues)
        if peak is not None:
            peaks.append(peak)
    return peaks


def parse_experimental_nmr(content: str | Path) -> ExperimentalNmr:
    """Parse the DevDoc §6.2 text format into :class:`ExperimentalNmr`.

    Every problem found on the way (unknown tokens/lines, missing or
    duplicate labels, unmatched peaks/atoms, ambiguous labels) is recorded
    in :attr:`ExperimentalNmr.parse_errors` — nothing is dropped silently.

    Args:
        content: Raw text or a path to a file.

    Raises:
        ValueError: When no ``C:`` / ``H:`` / ... nucleus section is found.
    """
    if isinstance(content, Path):
        text = content.read_text(encoding="utf-8")
    else:
        text = str(content)

    peaks: dict[str, list[ExperimentalPeak]] = {}
    equivalence_groups: list[list[str]] = []
    omit_atoms: list[str] = []
    issues: list[ParseIssue] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        eq_match = _EQ_LINE_RE.match(line)
        if eq_match:
            labels = [_parse_atom_label(t) for t in eq_match.group(1).split(",") if t.strip()]
            if labels:
                equivalence_groups.append(labels)
            continue

        omit_match = _OMIT_LINE_RE.match(line)
        if omit_match:
            for token in omit_match.group(1).split(","):
                token = token.strip()
                if token:
                    omit_atoms.append(_parse_atom_label(token))
            continue

        nuc_match = _NUCLEUS_LINE_RE.match(line)
        if nuc_match:
            element = normalize_symbol(nuc_match.group(1))
            new_peaks = _parse_peaks_in_line(element, nuc_match.group(2), issues)
            if new_peaks:
                peaks.setdefault(element, []).extend(new_peaks)
            continue

        issues.append(ParseIssue("unknown_token", "unrecognized input line", line))

    if not peaks:
        raise ValueError(
            "Experimental NMR input is empty — expected at least one 'C:' / 'H:' nucleus section."
        )

    for element, group in peaks.items():
        for idx, peak in enumerate(group):
            if peak.index != idx:
                group[idx] = replace(peak, index=idx)

    issues.extend(_duplicate_label_issues(peaks))
    issues.extend(_missing_label_issues(peaks))
    issues.extend(_unmatched_atom_issues(equivalence_groups, omit_atoms, peaks))

    if issues:
        logger.warning("Experimental NMR input parsed with %d issue(s)", len(issues))

    # G02: legacy whole-spectrum flag — True only when EVERY peak is
    # assigned; a single labeled peak must no longer flip it.
    assigned = all(peak.assigned for group in peaks.values() for peak in group)

    return ExperimentalNmr(
        peaks=peaks,
        equivalence_groups=equivalence_groups,
        omit_atoms=omit_atoms,
        assigned=assigned,
        parse_errors=issues,
    )


def _duplicate_label_issues(peaks: dict[str, list[ExperimentalPeak]]) -> list[ParseIssue]:
    issues: list[ParseIssue] = []
    first_seen: dict[str, ExperimentalPeak] = {}
    for group in peaks.values():
        for peak in group:
            if peak.atom_label is None:
                continue
            previous = first_seen.get(peak.atom_label)
            if previous is None:
                first_seen[peak.atom_label] = peak
                continue
            issues.append(
                ParseIssue(
                    "duplicate_label",
                    f"label {peak.atom_label} attached to peaks at "
                    f"{previous.shift_ppm:g} and {peak.shift_ppm:g} ppm",
                    peak.atom_label,
                )
            )
    return issues


def _missing_label_issues(peaks: dict[str, list[ExperimentalPeak]]) -> list[ParseIssue]:
    issues: list[ParseIssue] = []
    for element, group in peaks.items():
        n_assigned = sum(1 for peak in group if peak.assigned)
        if not 0 < n_assigned < len(group):
            continue
        for peak in group:
            if peak.atom_label is None and peak.label_candidates is None:
                issues.append(
                    ParseIssue(
                        "missing_label",
                        f"{element} peak {peak.shift_ppm:g} ppm has no atom label "
                        "although the nucleus is partially assigned",
                        f"{peak.shift_ppm:g}",
                    )
                )
    return issues


def _unmatched_atom_issues(
    equivalence_groups: list[list[str]],
    omit_atoms: list[str],
    peaks: dict[str, list[ExperimentalPeak]],
) -> list[ParseIssue]:
    issues: list[ParseIssue] = []
    flagged: set[str] = set()
    referenced = [label for group in equivalence_groups for label in group] + omit_atoms
    for label in referenced:
        if label in flagged:
            continue
        flagged.add(label)
        element = _element_of_label(label)
        if element and element not in peaks:
            issues.append(
                ParseIssue(
                    "unmatched_atom",
                    f"label {label} references element {element} but no "
                    f"'{element}:' nucleus section was parsed",
                    label,
                )
            )
    return issues


__all__ = ["parse_experimental_nmr"]
