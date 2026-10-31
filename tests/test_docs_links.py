"""Guard: documentation links and ``docs/...`` path references stay resolvable.

Companion to ``scripts/check_docs_links.py``. The checker is a consumer of the
docs tree: it never edits docs. Its scope is deliberately narrow (see the
script docstring) — frozen archives and the hash-pinned ``acp2-contracts``
bundle are excluded, and every permitted absent reference is listed with a
reason instead of being silently skipped.

The negative control below matters as much as the green run: it proves the
checker actually fails on a dangling link, so a passing run is evidence.
"""

from __future__ import annotations

import scripts.check_docs_links as checker


def test_docs_links_resolve() -> None:
    assert checker.check(verbose=False) == 0


def test_checker_flags_a_dangling_relative_link(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "index.md").write_text("[gone](missing.md)\n", encoding="utf-8")
    (tmp_path / "fine.md").write_text("[ok](index.md)\n", encoding="utf-8")
    monkeypatch.setattr(checker, "DOCS", tmp_path)

    assert checker.check(verbose=False) == 1
    output = capsys.readouterr().out
    assert "missing.md" in output
    # the resolvable sibling must not be reported as broken
    assert output.count("unresolved reference") == 1


def test_checker_skips_fenced_blocks(tmp_path, monkeypatch) -> None:
    (tmp_path / "pasted.md").write_text(
        "```\n?? docs/reference/next-change/does-not-exist.md\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(checker, "DOCS", tmp_path)
    assert checker.check(verbose=False) == 0
