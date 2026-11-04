"""Unit tests for the Services project-file safety layer.

The module under test is stdlib-only; these tests pin path normalization,
project-root containment, symlink/reparse refusal, bounded listings and bounded
UTF-8 reads with an explicit digest scope.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from acp_services.data import project_files as pf


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    platform = tmp_path / "run_root"
    platform.mkdir()
    project = platform / "project"
    project.mkdir()
    return platform, project


def _symlink_or_skip(link: Path, target: Path, *, directory: bool = False) -> None:
    """Create a symlink or record the platform limitation explicitly."""
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform gate
        pytest.skip(f"symbolic links unavailable on this filesystem: {exc}")


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


def test_project_root_must_be_direct_child_of_platform_root(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    assert pf.resolve_project_root(project, platform) == project.resolve()

    with pytest.raises(pf.ProjectRootUnavailableError):
        pf.resolve_project_root(platform, platform)

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(pf.ProjectRootUnavailableError):
        pf.resolve_project_root(outside, platform)

    nested = project / "nested"
    nested.mkdir()
    with pytest.raises(pf.ProjectRootUnavailableError):
        pf.resolve_project_root(nested, platform)

    with pytest.raises(pf.ProjectRootUnavailableError):
        pf.resolve_project_root(tmp_path / "missing", platform)


def test_project_root_symlink_is_refused(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    link = platform / "linked_project"
    _symlink_or_skip(link, project, directory=True)
    with pytest.raises(pf.ProjectRootUnavailableError):
        pf.resolve_project_root(link, platform)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        ".",
        "..",
        "../outside",
        "/etc/passwd",
        "//server/share",
        "C:/Windows/win.ini",
        "C:relative",
        r"a\b",
        "a//b",
        "./a",
        "a/./b",
        "a/../b",
        "a:b",
        "a/\x00b",
        "a/\x1fb",
        "a/\x7fb",
        ".hidden",
        "a/.hidden",
        ".env",
        "credentials.json",
        "id_rsa",
        "server.pem",
        "cache.sqlite",
        "acp_jobs.db",
        "state.db-wal",
        "CON",
        "con.txt",
        "COM1",
        "LPT9.log",
        "trailing.",
        "trailing ",
    ],
)
def test_invalid_relative_paths_are_rejected(raw: str) -> None:
    with pytest.raises(pf.InvalidRelativePathError):
        pf.normalize_relative_path(raw)


def test_verification_budget_is_a_413_error() -> None:
    with pytest.raises(pf.VerificationBudgetExceededError) as excinfo:
        pf.normalize_relative_path("a" * (pf.MAX_PATH_CHARS + 1))
    assert excinfo.value.http_status == 413
    assert excinfo.value.code == "verification_budget_exceeded"

    too_deep = "/".join(["d"] * (pf.MAX_PATH_SEGMENTS + 1))
    with pytest.raises(pf.VerificationBudgetExceededError):
        pf.normalize_relative_path(too_deep)


def test_valid_relative_path_is_preserved() -> None:
    assert pf.normalize_relative_path("RESULT/structures/opt.xyz") == "RESULT/structures/opt.xyz"
    assert pf.normalize_relative_path("", allow_empty=True) == ""


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def _seed_visible_and_hidden(project: Path) -> None:
    (project / "a.txt").write_bytes(b"alpha\n")
    (project / "README").write_bytes(b"readme\n")
    (project / "dir").mkdir()
    (project / "dir" / "nested.txt").write_bytes(b"nested\n")
    (project / ".hidden").write_text("hidden\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (project / "credentials.json").write_text("{}", encoding="utf-8")
    (project / "id_rsa").write_text("key", encoding="utf-8")
    (project / "cache.sqlite").write_text("db", encoding="utf-8")
    _symlink_or_skip(project / "link.txt", project / "a.txt")
    _symlink_or_skip(project / "linkdir", project / "dir", directory=True)


def test_tree_skips_hidden_sensitive_and_links(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    _seed_visible_and_hidden(project)

    tree = pf.list_project_directory("p1", project, platform)
    assert tree.project_id == "p1"
    assert tree.path == ""
    assert [entry.path for entry in tree.entries] == ["dir", "a.txt", "README"]
    assert [entry.type for entry in tree.entries] == ["directory", "file", "file"]
    assert tree.entries[0].size == 0
    assert tree.entries[1].size == len("alpha\n")
    assert all("run_root" not in entry.path for entry in tree.entries)
    assert tree.truncated is False
    assert tree.truncation_reason == ""


def test_tree_path_is_one_level_and_relative(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    _seed_visible_and_hidden(project)

    tree = pf.list_project_directory("p1", project, platform, path="dir")
    assert tree.path == "dir"
    assert [entry.path for entry in tree.entries] == ["dir/nested.txt"]
    assert all(not entry.path.startswith("/") for entry in tree.entries)


def test_tree_entry_limit_truncates(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    for index in range(5):
        (project / f"file_{index}.txt").write_text(str(index), encoding="utf-8")

    tree = pf.list_project_directory("p1", project, platform, limit=2)
    assert len(tree.entries) == 2
    assert tree.truncated is True
    assert tree.truncation_reason == "entry_limit"


def test_tree_scan_limit_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform, project = _layout(tmp_path)
    monkeypatch.setattr(pf, "DIRECTORY_SCAN_LIMIT", 2)
    for index in range(4):
        (project / f".hidden_{index}").write_text("x", encoding="utf-8")

    tree = pf.list_project_directory("p1", project, platform)
    assert tree.entries == ()
    assert tree.truncated is True
    assert tree.truncation_reason == "scan_limit"


def test_tree_refuses_hidden_request_path(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / ".secret").mkdir()
    with pytest.raises(pf.InvalidRelativePathError):
        pf.list_project_directory("p1", project, platform, path=".secret")


# ---------------------------------------------------------------------------
# File reads
# ---------------------------------------------------------------------------


def test_read_utf8_content_and_scope(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    target = project / "notes.txt"
    target.write_bytes(b"hello\nworld\n")

    result = pf.read_project_file("p1", project, platform, path="notes.txt")
    assert result.content == "hello\nworld\n"
    assert result.encoding == "utf-8"
    assert result.size == target.stat().st_size
    assert result.returned_bytes == len(result.content.encode("utf-8"))
    assert result.returned_lines == 2
    assert result.truncated is False
    assert result.truncation_reason == ""
    assert result.sha256 == hashlib.sha256(result.content.encode("utf-8")).hexdigest()


def test_read_accepts_and_strips_utf8_bom(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "bom.txt").write_bytes(b"\xef\xbb\xbfabc")

    result = pf.read_project_file("p1", project, platform, path="bom.txt")
    assert result.content == "abc"
    assert result.returned_bytes == 3
    assert result.size == 6
    assert result.sha256 == hashlib.sha256(b"abc").hexdigest()
    assert result.truncated is False


def test_read_max_lines_truncates(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "lines.txt").write_bytes(b"a\nb\nc\n")

    result = pf.read_project_file("p1", project, platform, path="lines.txt", max_lines=2)
    assert result.content == "a\nb\n"
    assert result.returned_lines == 2
    assert result.truncated is True
    assert result.truncation_reason == "max_lines"
    assert result.sha256 == hashlib.sha256(b"a\nb\n").hexdigest()


def test_read_max_bytes_truncates_on_utf8_boundary(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "wide.txt").write_text("\u00e9\u00e9\u00e9", encoding="utf-8")

    result = pf.read_project_file("p1", project, platform, path="wide.txt", max_bytes=3)
    assert result.content == "\u00e9"
    assert result.returned_bytes == 2
    assert result.size == 6
    assert result.truncated is True
    assert result.truncation_reason == "max_bytes"
    assert result.sha256 == hashlib.sha256(result.content.encode()).hexdigest()


def test_read_rejects_nul_bytes(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "binary.bin").write_bytes(b"abc\x00def")

    with pytest.raises(pf.BinaryFileError) as excinfo:
        pf.read_project_file("p1", project, platform, path="binary.bin")
    assert excinfo.value.code == "binary"
    assert excinfo.value.http_status == 415


def test_read_rejects_invalid_utf8(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "bad.txt").write_bytes(b"\xff\xfe")

    with pytest.raises(pf.ContentRejectedError) as excinfo:
        pf.read_project_file("p1", project, platform, path="bad.txt")
    assert excinfo.value.code == "unsupported_encoding"


def test_read_rejects_directory(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "dir").mkdir()

    with pytest.raises(pf.NotRegularFileError) as excinfo:
        pf.read_project_file("p1", project, platform, path="dir")
    assert excinfo.value.code == "not_regular_file"
    assert excinfo.value.http_status == 415


def test_read_refuses_symlink_file_and_parent(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "target.txt").write_text("target\n", encoding="utf-8")
    _symlink_or_skip(project / "link.txt", project / "target.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.txt").write_text("outside\n", encoding="utf-8")
    _symlink_or_skip(project / "linkdir", outside, directory=True)

    with pytest.raises(pf.ProjectFileNotFoundError):
        pf.read_project_file("p1", project, platform, path="link.txt")
    with pytest.raises(pf.ProjectFileNotFoundError):
        pf.read_project_file("p1", project, platform, path="linkdir/outside.txt")


def test_read_refuses_hidden_and_sensitive_paths(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (project / "credentials.json").write_text("{}", encoding="utf-8")

    for raw in (".env", "credentials.json", "dir/../secret"):
        with pytest.raises(pf.InvalidRelativePathError):
            pf.read_project_file("p1", project, platform, path=raw)


def test_read_detects_a_file_changed_during_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    platform, project = _layout(tmp_path)
    target = project / "changing.txt"
    target.write_bytes(b"before\n")
    original = pf._read_bounded

    def _mutating_read(fd: int, max_bytes: int) -> tuple[bytes, bool]:
        with target.open("ab") as handle:
            handle.write(b"after\n")
        return original(fd, max_bytes)

    monkeypatch.setattr(pf, "_read_bounded", _mutating_read)
    with pytest.raises(pf.FileChangedError) as excinfo:
        pf.read_project_file("p1", project, platform, path="changing.txt")
    assert excinfo.value.code == "file_changed"
    assert excinfo.value.http_status == 409


def test_read_never_returns_more_than_the_byte_budget(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    (project / "large.txt").write_text("x" * 10_000, encoding="utf-8")

    result = pf.read_project_file("p1", project, platform, path="large.txt", max_bytes=64)
    assert len(result.content.encode("utf-8")) <= 64
    assert result.returned_bytes == 64
    assert result.size == 10_000
    assert result.truncated is True
    assert result.truncation_reason == "max_bytes"


def test_read_missing_file_and_unknown_project_root(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    with pytest.raises(pf.ProjectFileNotFoundError):
        pf.read_project_file("p1", project, platform, path="missing.txt")
    with pytest.raises(pf.ProjectRootUnavailableError):
        pf.read_project_file("p1", platform, platform, path="missing.txt")


def test_oserror_text_is_not_exposed(tmp_path: Path) -> None:
    platform, project = _layout(tmp_path)
    with pytest.raises(pf.ProjectFileNotFoundError) as excinfo:
        pf.read_project_file("p1", project, platform, path="does-not-exist.txt")
    assert str(project) not in excinfo.value.message
    assert str(tmp_path) not in excinfo.value.message


def test_platform_detection_does_not_follow_posix_links() -> None:
    """POSIX-only capability marker: the implementation must use dir_fd there."""
    if os.name != "posix":  # pragma: no cover - platform gate
        pytest.skip("POSIX dir_fd traversal is not available on this platform")
    assert pf._HAS_DIR_FD is True
