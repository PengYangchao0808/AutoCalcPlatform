"""
Tests for acp.core.registry and acp.core.workflow
===================================================
"""

from pathlib import Path

import pytest

from acp.core.models import StructureEnsemble
from acp.core.registry import Registry
from acp.core.state import WorkflowState
from acp.core.workflow import (
    Stage,
    WorkflowContext,
    WorkflowResult,
    WorkflowRunner,
    WorkflowSpec,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_get(self):
        r: Registry[str] = Registry()
        r.register("foo", "bar")
        assert r.get("foo") == "bar"

    def test_get_case_insensitive(self):
        r: Registry[int] = Registry()
        r.register("AbC", 42)
        assert r.get("abc") == 42
        assert r.get("ABC") == 42
        assert r.get("AbC") == 42

    def test_has(self):
        r: Registry[str] = Registry()
        r.register("x", "y")
        assert r.has("x") is True
        assert r.has("missing") is False

    def test_has_case_insensitive(self):
        r: Registry[str] = Registry()
        r.register("Hello", "world")
        assert r.has("hello") is True
        assert r.has("HELLO") is True

    def test_list_all(self):
        r: Registry[int] = Registry()
        assert r.list_all() == []
        r.register("a", 1)
        r.register("b", 2)
        assert sorted(r.list_all()) == ["a", "b"]

    def test_unregister(self):
        r: Registry[str] = Registry()
        r.register("keep", "this")
        r.register("remove", "that")
        r.unregister("remove")
        assert r.has("keep") is True
        assert r.has("remove") is False

    def test_unregister_case_insensitive(self):
        r: Registry[str] = Registry()
        r.register("Foo", "bar")
        r.unregister("foo")
        assert r.has("Foo") is False

    def test_unregister_missing_is_noop(self):
        r: Registry[int] = Registry()
        r.unregister("nonexistent")  # should not raise

    def test_get_missing_raises_keyerror(self):
        r: Registry[str] = Registry()
        with pytest.raises(KeyError, match="not registered"):
            r.get("missing")

    def test_get_missing_message_includes_available(self):
        r: Registry[str] = Registry()
        r.register("a", "1")
        r.register("b", "2")
        with pytest.raises(KeyError) as exc:
            r.get("c")
        msg = str(exc.value)
        assert "a" in msg
        assert "b" in msg

    def test_typed_registry(self):
        r: Registry[int] = Registry()
        r.register("one", 1)
        r.register("two", 2)
        assert r.get("one") + r.get("two") == 3


# ---------------------------------------------------------------------------
# WorkflowRunner
# ---------------------------------------------------------------------------


def _stage_double(ctx: WorkflowContext, data: StructureEnsemble) -> StructureEnsemble:
    """Append 'doubled' to data list."""
    items = list(data.data)
    items.append("doubled")
    return StructureEnsemble(data=items)


def _stage_append(ctx: WorkflowContext, data: StructureEnsemble, suffix: str = "") -> StructureEnsemble:
    """Append suffix to data list."""
    items = list(data.data)
    items.append(suffix)
    return StructureEnsemble(data=items)


def _stage_fail(ctx: WorkflowContext, data: StructureEnsemble) -> StructureEnsemble:
    """Always raises."""
    msg = "intentional failure"
    raise RuntimeError(msg)


class TestWorkflowRunner:
    def test_empty_spec(self):
        ctx = WorkflowContext(
            work_dir=Path("/tmp"),
            state=WorkflowState(),
            config={},
            backends={},
        )
        runner = WorkflowRunner(ctx)
        spec = WorkflowSpec(name="empty", stages=[])
        result = runner.run(spec)
        assert result.status == "completed"
        assert result.stages_completed == []
        assert result.error is None

    def test_single_stage(self):
        ctx = WorkflowContext(
            work_dir=Path("/tmp"),
            state=WorkflowState(),
            config={},
            backends={},
        )
        runner = WorkflowRunner(ctx)
        spec = WorkflowSpec(name="single", stages=[Stage(name="double", func=_stage_double)])
        result = runner.run(spec)
        assert result.status == "completed"
        assert result.stages_completed == ["double"]
        assert result.ensemble is not None
        assert result.ensemble.data == ["doubled"]

    def test_two_stages(self):
        ctx = WorkflowContext(
            work_dir=Path("/tmp"),
            state=WorkflowState(),
            config={},
            backends={},
        )
        runner = WorkflowRunner(ctx)
        spec = WorkflowSpec(
            name="two-stage",
            stages=[
                Stage(name="first", func=_stage_double),
                Stage(name="second", func=_stage_append, params={"suffix": "done"}),
            ],
        )
        result = runner.run(spec)
        assert result.status == "completed"
        assert result.stages_completed == ["first", "second"]
        assert result.ensemble is not None
        assert result.ensemble.data == ["doubled", "done"]

    def test_state_tracks_stages(self):
        ctx = WorkflowContext(
            work_dir=Path("/tmp"),
            state=WorkflowState(),
            config={},
            backends={},
        )
        runner = WorkflowRunner(ctx)
        spec = WorkflowSpec(
            name="track",
            stages=[
                Stage(name="a", func=_stage_double),
                Stage(name="b", func=_stage_append, params={"suffix": "x"}),
            ],
        )
        runner.run(spec)
        assert ctx.state.completed_stages == ["a", "b"]
        assert ctx.state.failed_stages == {}

    def test_middle_stage_failure(self):
        ctx = WorkflowContext(
            work_dir=Path("/tmp"),
            state=WorkflowState(),
            config={},
            backends={},
        )
        runner = WorkflowRunner(ctx)
        spec = WorkflowSpec(
            name="fail-middle",
            stages=[
                Stage(name="ok1", func=_stage_append, params={"suffix": "first"}),
                Stage(name="fail", func=_stage_fail),
                Stage(name="ok2", func=_stage_append, params={"suffix": "never"}),
            ],
        )
        result = runner.run(spec)
        assert result.status == "failed"
        assert result.stages_completed == ["ok1"]
        assert "fail" in ctx.state.failed_stages
        assert "intentional failure" in ctx.state.failed_stages["fail"]
        assert result.error is not None
        assert "intentional failure" in result.error
        # ok2 should NOT have run
        assert "ok2" not in result.stages_completed

    def test_result_metadata(self):
        ctx = WorkflowContext(
            work_dir=Path("/tmp"),
            state=WorkflowState(),
            config={},
            backends={},
        )
        runner = WorkflowRunner(ctx)
        spec = WorkflowSpec(name="meta", stages=[])
        result = runner.run(spec)
        assert isinstance(result, WorkflowResult)
        assert result.metadata == {}
