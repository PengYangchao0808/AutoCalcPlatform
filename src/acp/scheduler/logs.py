"""
Scheduler Logs
==============

Read/tail helpers for per-job ``stdout.log`` / ``stderr.log``.

Both functions avoid reading the entire file into memory.
``read_log_tail`` accumulates 64 KiB chunks backward from EOF until
enough newlines are found; ``read_log_range`` streams forward line-by-line.
"""

from __future__ import annotations

import os
from pathlib import Path

_CHUNK_SIZE = 65536  # 64 KiB


def read_log_tail(path: Path | str, lines: int = 300) -> list[str]:
    """Return up to ``lines`` trailing lines of a log file (best effort).

    Reads backward from the end of the file in 64 KiB chunks, accumulating
    raw bytes until at least ``lines`` newlines are found.  Never loads the
    entire file into memory for large inputs.

    Returns ``[]`` when the file is missing or an ``OSError`` occurs.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            file_size = fh.tell()
            if file_size == 0:
                return []

            # Accumulate raw bytes from the end until we have enough
            # newline-terminated lines.  Storing chunks in a list and
            # joining once at the end avoids repeated bytes concatenation.
            chunks: list[bytes] = []
            nl_count = 0
            pos = file_size
            while pos > 0 and nl_count < lines:
                read_size = min(_CHUNK_SIZE, pos)
                pos -= read_size
                fh.seek(pos)
                chunk = fh.read(read_size)
                chunks.append(chunk)
                nl_count += chunk.count(b"\n")

            # We may have stopped mid-line.  Check if the byte just before
            # the accumulated region is a newline (line boundary) or not.
            if pos > 0:
                fh.seek(pos - 1)
                if fh.read(1) != b"\n":
                    # Mid-line: read backward until we find a newline or
                    # the start of the file so the full first line is included.
                    while pos > 0:
                        read_size = min(_CHUNK_SIZE, pos)
                        pos -= read_size
                        fh.seek(pos)
                        chunk = fh.read(read_size)
                        chunks.append(chunk)
                        nl_pos = chunk.rfind(b"\n")
                        if nl_pos >= 0:
                            chunks[-1] = chunk[nl_pos + 1 :]
                            break

            # Join in file order and decode once — avoids mojibake from
            # multi-byte characters split across chunk boundaries.
            raw = b"".join(reversed(chunks))
            all_lines = raw.decode("utf-8", errors="replace").splitlines()
            return all_lines[-lines:] if len(all_lines) > lines else all_lines
    except OSError:
        return []


def _splitlines_bytes(data: bytes) -> tuple[list[bytes], bytes]:
    """Split *data* on newlines (\\n, \\r\\n, \\r).

    Returns ``(lines, remainder)`` where each *lines* entry is a
    line-ending-free bytes object and *remainder* is the trailing
    partial line (no newline terminator).
    """
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    parts = data.split(b"\n")
    remainder = parts.pop()
    return parts, remainder


def read_log_range(path: Path | str, offset: int = 0, max_lines: int = 2000) -> list[str]:
    """Return log lines starting at ``offset`` (line-indexed).

    Streams forward in 64 KiB chunks, skipping the first ``offset`` lines
    and collecting up to ``max_lines``.  Never materialises the whole file.

    Returns ``[]`` when the file is missing or an ``OSError`` occurs.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        result: list[str] = []
        lines_to_skip = offset
        lines_to_collect = max_lines
        skipping = offset > 0
        remainder = b""

        with open(p, "rb") as fh:
            while True:
                raw = os.read(fh.fileno(), _CHUNK_SIZE)
                if not raw:
                    if remainder and not skipping:
                        result.append(remainder.decode("utf-8", errors="replace"))
                    break

                data = remainder + raw
                lines_bytes, remainder = _splitlines_bytes(data)

                if skipping:
                    n = len(lines_bytes)
                    if n <= lines_to_skip:
                        lines_to_skip -= n
                    else:
                        start = lines_to_skip
                        lines_to_skip = 0
                        skipping = False
                        for s in lines_bytes[start:]:
                            if len(result) >= lines_to_collect:
                                break
                            result.append(s.decode("utf-8", errors="replace"))
                        if len(result) >= lines_to_collect:
                            break
                else:
                    for s in lines_bytes:
                        if len(result) >= lines_to_collect:
                            break
                        result.append(s.decode("utf-8", errors="replace"))
                    if len(result) >= lines_to_collect:
                        break

        return result
    except OSError:
        return []


__all__ = ["read_log_tail", "read_log_range"]
