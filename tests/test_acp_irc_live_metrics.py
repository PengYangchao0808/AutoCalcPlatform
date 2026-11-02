"""Round-6 #5: the IRC progress sink publishes live metrics (not just detail)."""

from __future__ import annotations

from acp.calculations.primitives.irc import _IrcReporterSink


class _Reporter:
    def __init__(self) -> None:
        self.stage: str | None = None
        self.detail: str | None = None
        self.metrics: list = []

    def start_stage(self, name: str) -> None:
        self.stage = name

    def complete_stage(self, name: str) -> None:
        self.stage = None

    def fail_stage(self, name: str, message: str) -> None:
        self.stage = None

    def set_stage_detail(self, name: str, detail: str) -> None:
        self.detail = detail

    def update_live_metrics(self, metrics) -> None:
        self.metrics = list(metrics)


def test_irc_sink_publishes_forward_and_reverse_counts() -> None:
    from cccp.calculation.progress import ProgressEvent, ProgressEventKind

    reporter = _Reporter()
    sink = _IrcReporterSink(reporter)
    sink.emit(ProgressEvent(kind=ProgressEventKind.STAGE_STARTED, stage="irc_forward"))
    sink.emit(
        ProgressEvent(kind=ProgressEventKind.METRIC, stage="irc_forward", metric="irc_forward_points", value=80)
    )
    assert [metric.key for metric in reporter.metrics] == ["irc_forward_points"]
    assert reporter.metrics[0].value == "80"
    assert reporter.metrics[0].kind == "count"
    sink.emit(
        ProgressEvent(kind=ProgressEventKind.METRIC, stage="irc_forward", metric="irc_reverse_points", value=3)
    )
    assert {metric.key for metric in reporter.metrics} == {
        "irc_forward_points",
        "irc_reverse_points",
    }
