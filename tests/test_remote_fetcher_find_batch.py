"""W-03 — batched remote directory enumeration (one ``find`` instead of N SFTP round trips).

The fetcher's recursive listing used to cost one ``sftp.listdir_attr`` per
directory.  ``RemoteResultFetcher._find_remote_entries`` now asks the node for
the whole tree in ONE ``find`` command (NUL-separated ``%y``/``%s``/``%T@``/``%p``
records) and the callers fall back to the original SFTP recursion whenever the
batch path is unavailable (no GNU find, permission errors, odd output).

Covered here:
- special characters in filenames (space / tab / newline)
- directory ``size=0`` + ``is_dir`` semantics preserved
- include/exclude filtering on the batch path
- ``max_entries`` truncation semantics (``len(entries) >= max_entries``)
- fallback when ``find`` is missing / fails / emits stderr / emits garbage
- ``walk_remote_files`` still returns a generator
- the ``find`` command is shell-quoted (no injection through the base dir)

Run with: PYTHONPATH=src python3.11 -m pytest tests/test_remote_fetcher_find_batch.py -q
"""

from __future__ import annotations

import contextlib
import inspect
import shlex

import pytest

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.remote.config import RemoteExecutionConfig, RemoteNode
from acp.scheduler.remote.fetcher import NotARemoteJobError, RemoteResultFetcher
from acp.scheduler.remote.sftp import FileStager
from tests.test_remote_phase4 import FakeSFTP, make_node, make_remote_record

REMOTE_DIR = "/scratch/test/acp_jobs/job1"


# ====================================================================== #
# Fake SSH pool — the fetcher only needs ``execute`` + ``sftp_session``
# ====================================================================== #


class FakePool:
    """Minimal pool: records commands, returns a scripted ``execute`` result."""

    def __init__(self, sftp: FakeSFTP | None = None, result=None):
        self.sftp = sftp if sftp is not None else FakeSFTP()
        self.commands: list[str] = []
        self.sftp_sessions = 0
        self._result = result

    def execute(self, node, command, timeout=30):
        self.commands.append(command)
        if isinstance(self._result, BaseException):
            raise self._result
        if self._result is not None:
            return self._result
        raise RuntimeError("exec_command unavailable in this fake")

    @contextlib.contextmanager
    def sftp_session(self, node):
        self.sftp_sessions += 1
        yield self.sftp


def make_fetcher(pool: FakePool, node: RemoteNode | None = None) -> RemoteResultFetcher:
    node = node or make_node()
    config = RemoteExecutionConfig(execution_mode="remote", nodes=[node])
    return RemoteResultFetcher(ssh_pool=pool, stager=FileStager(pool), remote_config=config)


def find_stdout(records: list[tuple[str, int, str, str]]) -> str:
    """Build the exact NUL-separated ``find -printf`` payload."""
    return "".join(
        f"{kind}\x00{size}\x00{mtime}\x00{path}\x00" for kind, size, mtime, path in records
    )


def record_for(remote_dir: str = REMOTE_DIR) -> JobRecord:
    return make_remote_record(remote_dir=remote_dir)


# ====================================================================== #
# Batch path — parsing / semantics
# ====================================================================== #


def test_find_batch_handles_special_character_filenames():
    pool = FakePool(
        result=(
            0,
            find_stdout(
                [
                    ("d", 4096, "1762222222.0", f"{REMOTE_DIR}/sub dir"),
                    ("f", 12, "1762222222.5", f"{REMOTE_DIR}/sub dir/a b.xyz"),
                    ("f", 7, "1762222223.0", f"{REMOTE_DIR}/tab\tname.log"),
                    ("f", 9, "1762222224.25", f"{REMOTE_DIR}/line\nbreak.json"),
                ]
            ),
            "",
        )
    )
    fetcher = make_fetcher(pool)

    files = list(fetcher.walk_remote_files(record_for()))

    by_rel = {rel: info for rel, info in files}
    assert set(by_rel) == {
        "sub dir/a b.xyz",
        "tab\tname.log",
        "line\nbreak.json",
    }
    assert by_rel["sub dir/a b.xyz"].size == 12
    assert by_rel["sub dir/a b.xyz"].name == "a b.xyz"
    assert by_rel["tab\tname.log"].size == 7
    assert by_rel["line\nbreak.json"].mtime == pytest.approx(1762222224.25)
    # One batch command, no SFTP session at all.
    assert len(pool.commands) == 1
    assert pool.sftp_sessions == 0
    print("  [OK] find batch: spaces / tabs / newlines survive NUL framing")


def test_find_batch_command_shape_and_quoting():
    base = "/scratch/job dir/it's"
    pool = FakePool(result=(0, "", ""))
    fetcher = make_fetcher(pool)

    entries = fetcher._find_remote_entries(make_node(), base)

    assert entries == []
    assert len(pool.commands) == 1
    command = pool.commands[0]
    assert command.startswith("LC_ALL=C find ")
    assert shlex.quote(base) in command
    assert "-mindepth 1" in command
    assert r"%y\0%s\0%T@\0%p\0" in command
    print("  [OK] find batch: single quoted GNU find -printf command")


def test_find_batch_empty_dir_is_empty_list_not_fallback():
    pool = FakePool(result=(0, "", ""))
    fetcher = make_fetcher(pool)

    assert fetcher._find_remote_entries(make_node(), REMOTE_DIR) == []
    assert list(fetcher.walk_remote_files(record_for())) == []
    # No SFTP fallback for a genuinely empty tree.
    assert pool.sftp_sessions == 0
    print("  [OK] find batch: empty directory is [] (no SFTP fallback)")


def test_find_batch_directory_size_semantics():
    pool = FakePool(
        result=(
            0,
            find_stdout(
                [
                    ("d", 4096, "1762222222.0", f"{REMOTE_DIR}/finalDFT"),
                    ("f", 34, "1762222223.0", f"{REMOTE_DIR}/finalDFT/energy.out"),
                ]
            ),
            "",
        )
    )
    fetcher = make_fetcher(pool)

    entries, truncated = fetcher.list_files_recursive(record_for())

    assert truncated is False
    by_name = {entry.name: entry for entry in entries}
    assert by_name["finalDFT"].is_dir is True
    assert by_name["finalDFT"].size == 0
    assert by_name["finalDFT/energy.out"].is_dir is False
    assert by_name["finalDFT/energy.out"].size == 34
    print("  [OK] find batch: directories keep is_dir=True and size=0")


def test_find_batch_include_exclude_filtering():
    pool = FakePool(
        result=(
            0,
            find_stdout(
                [
                    ("f", 1, "1762222222.0", f"{REMOTE_DIR}/stdout.log"),
                    ("f", 2, "1762222222.0", f"{REMOTE_DIR}/result.xyz"),
                    ("f", 3, "1762222222.0", f"{REMOTE_DIR}/scratch.tmp"),
                    ("f", 4, "1762222222.0", f"{REMOTE_DIR}/nested/job.rwf"),
                ]
            ),
            "",
        )
    )
    fetcher = make_fetcher(pool)

    included = {
        rel for rel, _ in fetcher.walk_remote_files(record_for(), include=["*.log", "*.xyz"])
    }
    excluded = {
        rel
        for rel, _ in fetcher.walk_remote_files(
            record_for(), include=["*.log", "*.xyz"], exclude=["stdout*"]
        )
    }

    assert included == {"stdout.log", "result.xyz"}
    assert excluded == {"result.xyz"}
    print("  [OK] find batch: include/exclude globs filter relative paths")


# ====================================================================== #
# max_entries truncation (list_files_recursive)
# ====================================================================== #


def _five_file_tree() -> str:
    return find_stdout(
        [("f", index, "1762222222.0", f"{REMOTE_DIR}/f{index}.log") for index in range(5)]
    )


@pytest.mark.parametrize(
    ("max_entries", "expected_count", "expected_truncated"),
    [
        (10, 5, False),
        (5, 5, True),  # exact fit still counts as truncated (historical semantics)
        (3, 3, True),
        (0, 0, True),
    ],
)
def test_list_files_recursive_truncation(max_entries, expected_count, expected_truncated):
    pool = FakePool(result=(0, _five_file_tree(), ""))
    fetcher = make_fetcher(pool)

    entries, truncated = fetcher.list_files_recursive(record_for(), max_entries=max_entries)

    assert len(entries) == expected_count
    assert truncated is expected_truncated
    assert pool.sftp_sessions == 0
    print(f"  [OK] find batch: max_entries={max_entries} -> {expected_count} / {truncated}")


# ====================================================================== #
# Fallbacks — the batch path must never raise, and must return None
# ====================================================================== #


def _tree_with_dirs() -> FakeSFTP:
    sftp = FakeSFTP()
    sftp.files[REMOTE_DIR + "/stdout.log"] = b"ok\n"
    sftp.files[REMOTE_DIR + "/finalDFT/energy.out"] = b"-154.94\n"
    sftp.dirs.add(REMOTE_DIR + "/finalDFT")
    return sftp


def test_walk_falls_back_when_exec_is_unavailable():
    pool = FakePool(sftp=_tree_with_dirs(), result=RuntimeError("no exec_command"))
    fetcher = make_fetcher(pool)

    files = list(fetcher.walk_remote_files(record_for()))

    assert {rel for rel, _ in files} == {"stdout.log", "finalDFT/energy.out"}
    assert len(pool.commands) == 1  # tried once, then fell back
    assert pool.sftp_sessions == 1
    print("  [OK] fallback: exec unavailable -> SFTP recursion")


def test_walk_falls_back_on_nonzero_exit_or_stderr():
    for result in (
        (127, "", "find: command not found"),
        (0, find_stdout([("f", 1, "1.0", f"{REMOTE_DIR}/stdout.log")]), "Permission denied"),
        (1, "", ""),
    ):
        pool = FakePool(sftp=_tree_with_dirs(), result=result)
        fetcher = make_fetcher(pool)

        files = list(fetcher.walk_remote_files(record_for()))

        assert {rel for rel, _ in files} == {"stdout.log", "finalDFT/energy.out"}
        assert pool.sftp_sessions == 1
    print("  [OK] fallback: non-zero exit / non-empty stderr -> SFTP recursion")


@pytest.mark.parametrize(
    "stdout",
    [
        "a\x00b\x00c",  # field count not a multiple of 4
        find_stdout([("f", 1, "1.0", "/etc/passwd")]),  # outside base_dir
        find_stdout([("f", 1, "1.0", f"{REMOTE_DIR}/../escape.txt")]),  # traversal
        find_stdout([("f", "not-a-size", "1.0", f"{REMOTE_DIR}/x.log")]),  # bad size
        find_stdout([("f", 1, "not-a-time", f"{REMOTE_DIR}/x.log")]),  # bad mtime
        find_stdout([("f", 1, "1.0", f"{REMOTE_DIR}/bad\ufffdname.log")]),  # undecodable name
    ],
)
def test_find_batch_rejects_unusable_output(stdout):
    pool = FakePool(result=(0, stdout, ""))
    fetcher = make_fetcher(pool)

    assert fetcher._find_remote_entries(make_node(), REMOTE_DIR) is None
    print("  [OK] find batch: unusable output -> None")


def test_list_files_recursive_falls_back_on_unparseable_output():
    pool = FakePool(sftp=_tree_with_dirs(), result=(0, "garbage", ""))
    fetcher = make_fetcher(pool)

    entries, truncated = fetcher.list_files_recursive(record_for())

    assert {entry.name for entry in entries} == {"stdout.log", "finalDFT", "finalDFT/energy.out"}
    assert truncated is False
    assert pool.sftp_sessions == 1
    print("  [OK] fallback: list_files_recursive degrades to SFTP recursion")


def test_walk_remote_files_is_still_a_generator():
    pool = FakePool(result=(0, _five_file_tree(), ""))
    fetcher = make_fetcher(pool)

    stream = fetcher.walk_remote_files(record_for())

    assert inspect.isgenerator(stream)
    assert pool.commands == []  # nothing executed until iteration starts
    assert len(list(stream)) == 5
    print("  [OK] walk_remote_files: still lazy (generator)")


def test_walk_remote_files_uses_job_metadata():
    record = JobRecord(
        id="20260713_001_local",
        spec=JobSpec(workflow="singlepoint", input={"source": "CCO"}),
        status=JobStatus.COMPLETED,
        work_dir="/tmp/fake",
        result={},
    )
    fetcher = make_fetcher(FakePool())

    with pytest.raises(NotARemoteJobError):
        list(fetcher.walk_remote_files(record))
    print("  [OK] walk_remote_files: local job still rejected before any batch call")
