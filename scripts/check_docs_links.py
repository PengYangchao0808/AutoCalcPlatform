#!/usr/bin/env python3
"""Check that documentation links and ``docs/...`` path references resolve.

Scope and exclusions are deliberate:

- Markdown relative links and ``docs/...`` repo-relative references are checked.
- Fenced code blocks are skipped (pasted command output is evidence, not a link).
- ``docs/archive/**`` is frozen: archived text keeps the paths of its own batch.
- ``docs/reference/next-change/acp2-contracts/**`` is a hash-pinned contract
  bundle whose cross-references are snapshots, not navigation.
- Targets carrying ``:`` line suffixes, URL schemes and pure anchors are skipped.

Exit code 0 means every checked reference resolved.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"

LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^)\s]+))\s*\)")
DOCS_PATH_RE = re.compile(r"(?<![\w./-])(docs/[A-Za-z0-9_./-]+\.(?:md|txt|html|json|py))")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
LINE_SUFFIX_RE = re.compile(r":\d+$")
FENCE_RE = re.compile(r"^(```|~~~)")

SKIP_DIR_PARTS = {"archive", "acp2-contracts"}

# References kept on purpose even though the target does not exist. Each entry
# is an audit fact, not a waiver: the path is either explicitly NOT_VERIFIED in
# AGENTS.md or was a planned artifact that was never produced. Keep this list
# small and never add an entry to silence a real broken link.
#
# Empty as of 2026-10-31: the two entries that used to live here
# (acp2-contracts/experience/static-assets.manifest.json and
# reports/architecture_repair_20261022/REPORT.md) both landed on origin/main, so
# keeping them would mask a future break at those paths.
ALLOWED_ABSENT: dict[str, str] = {}


def _strip_fences(text: str) -> str:
    """Replace fenced blocks with blank lines so offsets stay stable."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if FENCE_RE.match(line.strip()):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def _iter_docs() -> list[Path]:
    files = []
    for path in DOCS.rglob("*.md"):
        rel_parts = path.relative_to(DOCS).parts
        if SKIP_DIR_PARTS & set(rel_parts):
            continue
        files.append(path)
    return sorted(files)


def check(verbose: bool) -> int:
    broken: list[tuple[str, str, str]] = []
    allowed: list[tuple[str, str]] = []
    checked = 0

    def resolve(rel_file: str, target: str, resolved: Path, kind: str) -> None:
        nonlocal checked
        checked += 1
        if resolved.is_relative_to(REPO_ROOT):
            repo_rel = resolved.relative_to(REPO_ROOT).as_posix()
        else:
            repo_rel = ""
        if resolved.exists():
            if verbose:
                print(f"OK    {rel_file}: {target}")
        elif repo_rel in ALLOWED_ABSENT:
            allowed.append((rel_file, repo_rel))
        else:
            broken.append((rel_file, target, kind))

    for path in _iter_docs():
        try:
            rel_file = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:  # a docs root outside the repo (used by the guard test)
            rel_file = path.as_posix()
        text = _strip_fences(path.read_text(encoding="utf-8"))

        for match in LINK_RE.finditer(text):
            target = match.group(1) or match.group(2) or ""
            if target.startswith("#") or SCHEME_RE.match(target):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part or LINE_SUFFIX_RE.search(path_part):
                continue
            resolve(rel_file, target, (path.parent / path_part).resolve(), "relative link")

        for match in DOCS_PATH_RE.findall(text):
            if LINE_SUFFIX_RE.search(match):
                continue
            resolve(rel_file, match, (REPO_ROOT / match).resolve(), "docs path")

    print(f"\nchecked {checked} reference(s) in {len(_iter_docs())} file(s)")
    for rel_file, repo_rel in allowed:
        print(f"NOTED {rel_file}: {repo_rel} — {ALLOWED_ABSENT[repo_rel]}")
    if broken:
        print(f"FAIL: {len(broken)} unresolved reference(s)\n")
        for rel_file, target, kind in broken:
            print(f"  {rel_file}\n      {kind}: {target}")
        return 1
    print(f"PASS: 0 unresolved reference(s) ({len(allowed)} documented-absent)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="print every resolved reference")
    args = parser.parse_args()
    return check(args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
