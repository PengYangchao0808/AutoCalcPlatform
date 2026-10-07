"""ANTI #11 guard: scheduler root-write audit vs ``_SCHEDULER_MARKERS``.

Root ANTI #11 (``AGENTS.md``): ``_SCHEDULER_MARKERS`` must list the COMPLETE
set of files the scheduler pre-creates (or preserves) at the task directory
root.  ``_resolve_output_dir`` reuses the task root only while
``contents <= _SCHEDULER_MARKERS``; any unregistered scheduler-created root
file silently redirects the run to a ``<work_dir>_1/`` sibling and the job
still reports COMPLETED — products "disappear" downstream (BUG-1, GAP-1).

Two complementary guards (behavioral, not vibes):

1. A real ``tmp_path`` tree containing ``job.json`` + ``task.json`` + EVERY
   marker from the literal snapshot below (including ``.structure_history``)
   must be REUSED by the real ``_resolve_output_dir`` — no ``_1`` sibling.
   Before BUG-1(a) this failed: ``.structure_history`` was preserved across
   rerun/edit but unregistered, so the directory redirected.
2. A literal snapshot pinning the COMPLETE marker set.  Any future change to
   the set must consciously update this snapshot in the same commit and state
   the reason (which scheduler root write was added/removed, and why it is or
   is not a marker) in the test docstring below.
"""

from __future__ import annotations

from pathlib import Path

from acp.workflows.simple import _SCHEDULER_MARKERS, _resolve_output_dir

# Literal snapshot of the complete marker set — AGENTS.md ANTI #11.
# Updating this frozenset is a CONTRACT change, not a style edit: do it in the
# same commit as the ``_SCHEDULER_MARKERS`` change and record the reason
# (which scheduler root write entered/left the set and why) in this docstring.
_EXPECTED_SCHEDULER_MARKERS: frozenset[str] = frozenset(
    {
        # Zone-A scheduler files (job lifecycle / logs).
        "submit.lsf",
        ".exit_code",
        "events.jsonl",
        "job.json",
        "stdout.log",
        "stderr.log",
        # Sidecars written by workflow/scheduler before or during a run.
        "mechanism_config.json",
        "metrics.json",
        "path_config.json",
        "gradient_config.json",
        # Task layout scaffolding + identity.
        "WORK",
        "RESULT",
        "INPUT",
        "input.xyz",
        "task.json",
        "input_source.json",
        # Checkpoint-continue receipt (registered by 55262df, same bug class).
        "resume_source.json",
        # BUG-1(a) audit (T1): preserved by rerun/edit reset, written by
        # structure_snapshots.preserve_outputs — was missing → `<dir>_1`.
        ".structure_history",
        # T1 runner root-write audit: written at the task root by the runner
        # BEFORE the workflow subprocess starts (present at resolve time).
        "electronic_state.json",
        "input.com",
        "input.inp",
    }
)

# Entries that are directories in a real task tree, not files.
_MARKER_DIRS: frozenset[str] = frozenset({"WORK", "RESULT", "INPUT", ".structure_history"})


def _materialize_marker_tree(base: Path, *, include_identity: bool = True) -> None:
    """Create *base* holding job.json + task.json + every snapshot marker."""
    base.mkdir(parents=True)
    for name in sorted(_EXPECTED_SCHEDULER_MARKERS):
        if not include_identity and name in ("job.json", "task.json"):
            continue
        path = base / name
        if name in _MARKER_DIRS:
            path.mkdir()
        else:
            path.write_text("placeholder\n", encoding="utf-8")


def test_resolve_output_dir_reuses_task_root_with_all_markers(tmp_path: Path) -> None:
    """Given a scheduler task dir with job.json+task.json+ALL markers
    (including ``.structure_history``), ``_resolve_output_dir`` must reuse the
    base directory — never redirect to a ``<dir>_1`` sibling (ANTI #11).

    This is the BUG-1(a) repro: a rerun/edit-preserved ``.structure_history``
    used to be unregistered, so the real resolver redirected the run.
    """
    base = tmp_path / "task"
    _materialize_marker_tree(base)

    resolved = _resolve_output_dir(base)

    assert resolved == base.resolve(), (
        f"expected base reuse, got {resolved} "
        "(unregistered scheduler root file redirects to a _1 sibling)"
    )
    assert not (tmp_path / "task_1").exists()
    assert not (tmp_path / "task_2").exists()


def test_resolve_output_dir_still_redirects_unknown_extra_file(tmp_path: Path) -> None:
    """Negative control: an UNREGISTERED non-marker file still redirects to
    ``_1`` when the directory has NO scheduler identity (``job.json`` /
    ``task.json`` absent).

    BUG-1(b) added a positive scheduler-identity layer that reuses any dir
    carrying ``job.json`` + ``task.json`` (covered by the test above and by
    ``test_acp_workflows_simple``'s identity branch).  This control therefore
    drops identity so it remains a true negative: the whitelist plus identity
    are the two guards, and absent both, redirection still happens.
    """
    base = tmp_path / "task"
    _materialize_marker_tree(base, include_identity=False)
    (base / "stray_product.xyz").write_text("1\n\nC 0 0 0\n", encoding="utf-8")

    resolved = _resolve_output_dir(base)

    assert resolved != base.resolve()
    assert resolved.name == "task_1"
    assert resolved.exists()


def test_scheduler_markers_snapshot_matches_literal() -> None:
    """Snapshot: ``_SCHEDULER_MARKERS`` == the literal frozenset above.

    ANY divergence means the scheduler root-write set changed.  To update
    consciously: change the literal here AND ``_SCHEDULER_MARKERS`` in
    ``src/acp/workflows/simple.py`` in the same commit, and add a line to this
    docstring stating the reason (which root write was added/removed and why
    it is/isn't exempt per the ANTI #11 audit — see
    ``.omo/evidence/acp-legacy-bug-remediation/task-1-acp-legacy-bug-remediation.log``).
    """
    assert _SCHEDULER_MARKERS == _EXPECTED_SCHEDULER_MARKERS, (
        "marker set drifted from the pinned snapshot — update both together "
        "with a stated reason (AGENTS.md ANTI #11)"
    )
