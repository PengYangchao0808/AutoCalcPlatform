"""Tests for seek-based ``read_log_tail`` / ``read_log_range``.

Property-style: each generated case is compared against a naive
whole-file reference implementation.  A separate test asserts the
new implementation never opens a file for *text* reading (i.e. never
calls ``read_text``), proving it does not load the whole file.
"""

from __future__ import annotations

import builtins
from pathlib import Path

from acp.scheduler import logs

# ---------------------------------------------------------------------------
# Naive reference implementations (whole-file read, for comparison only)
# ---------------------------------------------------------------------------


def _ref_tail(path: Path, lines: int = 300) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines()
    return all_lines[-lines:] if len(all_lines) > lines else all_lines


def _ref_range(path: Path, offset: int = 0, max_lines: int = 2000) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines()
    return all_lines[offset : offset + max_lines]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CJK = "中文行内容 日本語テスト 한국어테스트 αβγδεζηθ Συμβολοσειρά "


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# read_log_tail — property tests
# ---------------------------------------------------------------------------


class TestReadLogTail:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert logs.read_log_tail(tmp_path / "nope.log") == []

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.log"
        _write(p, "")
        assert logs.read_log_tail(p) == []

    def test_single_line(self, tmp_path: Path) -> None:
        p = tmp_path / "one.log"
        _write(p, "hello\n")
        assert logs.read_log_tail(p) == ["hello"]

    def test_single_line_no_trailing_newline(self, tmp_path: Path) -> None:
        p = tmp_path / "one.log"
        _write(p, "hello")
        assert logs.read_log_tail(p) == ["hello"]

    def test_exact_300_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "300.log"
        _write(p, "\n".join(f"line-{i}" for i in range(300)) + "\n")
        result = logs.read_log_tail(p)
        assert result == _ref_tail(p)

    def test_10k_lines_tail_300(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        _write(p, "\n".join(f"L{i:05d}" for i in range(10_000)) + "\n")
        assert logs.read_log_tail(p) == _ref_tail(p)

    def test_10k_lines_tail_50(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        _write(p, "\n".join(f"L{i:05d}" for i in range(10_000)) + "\n")
        assert logs.read_log_tail(p, lines=50) == _ref_tail(p, lines=50)

    def test_no_trailing_newline(self, tmp_path: Path) -> None:
        p = tmp_path / "notrail.log"
        _write(p, "\n".join(f"line-{i}" for i in range(50)))
        assert logs.read_log_tail(p) == _ref_tail(p)

    def test_long_lines_over_chunk(self, tmp_path: Path) -> None:
        """Lines longer than 64 KiB."""
        p = tmp_path / "long.log"
        big = "A" * 80_000
        content = f"short1\n{big}\nshort2\n"
        _write(p, content)
        assert logs.read_log_tail(p) == _ref_tail(p)

    def test_multiple_long_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "mlong.log"
        lines = [f"{'X' * 70_000}-{i}" for i in range(5)]
        _write(p, "\n".join(lines) + "\n")
        assert logs.read_log_tail(p, lines=5) == _ref_tail(p, lines=5)

    def test_cjk_content(self, tmp_path: Path) -> None:
        p = tmp_path / "cjk.log"
        content = "\n".join(f"line-{i} {CJK}" for i in range(100)) + "\n"
        _write(p, content)
        assert logs.read_log_tail(p) == _ref_tail(p)

    def test_cjk_long_line(self, tmp_path: Path) -> None:
        p = tmp_path / "cjk_long.log"
        content = CJK * 5000 + "\n"
        _write(p, content)
        assert logs.read_log_tail(p) == _ref_tail(p)

    def test_boundary_exactly_n_lines(self, tmp_path: Path) -> None:
        """When the file has exactly ``lines`` lines."""
        p = tmp_path / "bound.log"
        _write(p, "\n".join(f"L{i}" for i in range(10)) + "\n")
        assert logs.read_log_tail(p, lines=10) == _ref_tail(p, lines=10)

    def test_file_smaller_than_chunk(self, tmp_path: Path) -> None:
        p = tmp_path / "small.log"
        _write(p, "aaa\nbbb\nccc\n")
        assert logs.read_log_tail(p) == _ref_tail(p)

    def test_only_newlines(self, tmp_path: Path) -> None:
        p = tmp_path / "nl.log"
        _write(p, "\n\n\n\n\n")
        assert logs.read_log_tail(p, lines=3) == _ref_tail(p, lines=3)

    def test_mixed_line_endings(self, tmp_path: Path) -> None:
        p = tmp_path / "mixed.log"
        _write(p, "a\nb\r\nc\rd\n")
        assert logs.read_log_tail(p) == _ref_tail(p)


# ---------------------------------------------------------------------------
# read_log_range — property tests
# ---------------------------------------------------------------------------


class TestReadLogRange:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert logs.read_log_range(tmp_path / "nope.log") == []

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.log"
        _write(p, "")
        assert logs.read_log_range(p) == []

    def test_offset_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "off0.log"
        _write(p, "a\nb\nc\n")
        assert logs.read_log_range(p, offset=0) == _ref_range(p, offset=0)

    def test_offset_5(self, tmp_path: Path) -> None:
        p = tmp_path / "off5.log"
        _write(p, "\n".join(f"L{i}" for i in range(20)) + "\n")
        assert logs.read_log_range(p, offset=5) == _ref_range(p, offset=5)

    def test_max_lines_3(self, tmp_path: Path) -> None:
        p = tmp_path / "max3.log"
        _write(p, "\n".join(f"L{i}" for i in range(20)) + "\n")
        assert logs.read_log_range(p, max_lines=3) == _ref_range(p, max_lines=3)

    def test_offset_and_max(self, tmp_path: Path) -> None:
        p = tmp_path / "offmax.log"
        _write(p, "\n".join(f"L{i}" for i in range(50)) + "\n")
        assert logs.read_log_range(p, offset=10, max_lines=5) == _ref_range(
            p, offset=10, max_lines=5
        )

    def test_offset_past_end(self, tmp_path: Path) -> None:
        p = tmp_path / "past.log"
        _write(p, "a\nb\nc\n")
        assert logs.read_log_range(p, offset=100) == []

    def test_max_lines_zero(self, tmp_path: Path) -> None:
        p = tmp_path / "zero.log"
        _write(p, "a\nb\nc\n")
        assert logs.read_log_range(p, max_lines=0) == []

    def test_no_trailing_newline(self, tmp_path: Path) -> None:
        p = tmp_path / "notrail.log"
        _write(p, "\n".join(f"line-{i}" for i in range(50)))
        assert logs.read_log_range(p, offset=45) == _ref_range(p, offset=45)

    def test_10k_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        _write(p, "\n".join(f"L{i:05d}" for i in range(10_000)) + "\n")
        assert logs.read_log_range(p, offset=9900, max_lines=200) == _ref_range(
            p, offset=9900, max_lines=200
        )

    def test_long_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "long.log"
        content = "A" * 80_000 + "\nshort\n" + "B" * 80_000 + "\n"
        _write(p, content)
        assert logs.read_log_range(p, offset=1, max_lines=1) == _ref_range(p, offset=1, max_lines=1)

    def test_cjk_content(self, tmp_path: Path) -> None:
        p = tmp_path / "cjk.log"
        content = "\n".join(f"L{i} {CJK}" for i in range(100)) + "\n"
        _write(p, content)
        assert logs.read_log_range(p, offset=50, max_lines=10) == _ref_range(
            p, offset=50, max_lines=10
        )

    def test_file_smaller_than_chunk(self, tmp_path: Path) -> None:
        p = tmp_path / "small.log"
        _write(p, "aaa\nbbb\nccc\n")
        assert logs.read_log_range(p, offset=1) == _ref_range(p, offset=1)

    def test_offset_zero_no_trailing(self, tmp_path: Path) -> None:
        p = tmp_path / "notrail.log"
        _write(p, "x\ny\nz")
        assert logs.read_log_range(p, offset=0) == _ref_range(p, offset=0)


# ---------------------------------------------------------------------------
# No-whole-file-read assertion
# ---------------------------------------------------------------------------


class TestNoWholeFileRead:
    """The new implementation must never open a file in text mode (``read_text``)."""

    def test_read_log_tail_succeeds_without_text_open(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        _write(p, "\n".join(f"L{i:05d}" for i in range(5_000)) + "\n")

        _original_open = builtins.open

        def _guard(*args, **kwargs):
            mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
            if "b" not in mode:
                raise AssertionError("read_log_tail must not open files in text mode")
            return _original_open(*args, **kwargs)

        builtins.open = _guard  # type: ignore[assignment]
        try:
            result = logs.read_log_tail(p)
        finally:
            builtins.open = _original_open  # type: ignore[assignment]

        assert result == _ref_tail(p)

    def test_read_log_range_succeeds_without_text_open(self, tmp_path: Path) -> None:
        p = tmp_path / "big.log"
        _write(p, "\n".join(f"L{i:05d}" for i in range(5_000)) + "\n")

        _original_open = builtins.open

        def _guard(*args, **kwargs):
            mode = kwargs.get("mode", args[1] if len(args) > 1 else "r")
            if "b" not in mode:
                raise AssertionError("read_log_range must not open files in text mode")
            return _original_open(*args, **kwargs)

        builtins.open = _guard  # type: ignore[assignment]
        try:
            result = logs.read_log_range(p, offset=100, max_lines=200)
        finally:
            builtins.open = _original_open  # type: ignore[assignment]

        assert result == _ref_range(p, offset=100, max_lines=200)


# ---------------------------------------------------------------------------
# OSError swallowing
# ---------------------------------------------------------------------------


class TestOSError:
    def test_tail_on_unreadable_file(self, tmp_path: Path) -> None:
        """read_log_tail returns [] when open raises OSError."""
        p = tmp_path / "unreadable.log"
        _write(p, "data\n")

        _original_open = builtins.open

        def _raise(*args, **kwargs):
            raise OSError("permission denied")

        builtins.open = _raise  # type: ignore[assignment]
        try:
            assert logs.read_log_tail(p) == []
        finally:
            builtins.open = _original_open  # type: ignore[assignment]

    def test_range_on_unreadable_file(self, tmp_path: Path) -> None:
        """read_log_range returns [] when open raises OSError."""
        p = tmp_path / "unreadable.log"
        _write(p, "data\n")

        _original_open = builtins.open

        def _raise(*args, **kwargs):
            raise OSError("permission denied")

        builtins.open = _raise  # type: ignore[assignment]
        try:
            assert logs.read_log_range(p) == []
        finally:
            builtins.open = _original_open  # type: ignore[assignment]
