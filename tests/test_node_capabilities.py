"""
Node capability declaration + queue-override parsing tests (plan W1-T1).

Covers the acceptance criteria of todo 1 in
``.omo/plans/node-selection-at-submission.md`` (design §1.1, D3/D8/D13):

1. ``RemoteNode.from_config_dict`` parses ``capabilities.software``
   (deduplicated, order-preserving), ``capabilities.tags`` and the per-node
   ``queue`` override.
2. A missing ``capabilities`` block yields ``None`` (generic-node sentinel);
   unknown software names are dropped with a ``logger.warning`` and never
   abort configuration loading.
3. Drift lock: ``DECLARED_SOFTWARE_NAMES`` equals the software names probed
   by ``_DOCTOR_SOFTWARE_SCRIPT`` — extracted from the script's source text
   at test time, not from a hardcoded copy.
"""

from __future__ import annotations

import dataclasses
import logging
import re

import pytest

from acp.scheduler.remote.config import (
    DECLARED_SOFTWARE_NAMES,
    NodeCapabilities,
    RemoteExecutionConfig,
    RemoteNode,
)
from acp.scheduler.remote.node_manager import _DOCTOR_SOFTWARE_SCRIPT

_CONFIG_LOGGER = "acp.scheduler.remote.config"

_BASE_NODE: dict[str, object] = {
    "name": "compute-01",
    "host": "10.0.0.1",
    "username": "acp",
    "remote_work_dir": "/scratch/acp/acp_jobs",
    "remote_code_dir": "/home/acp/acp_code",
}


def _node(**extra: object) -> RemoteNode:
    """Build a RemoteNode from the minimal valid mapping plus *extra* keys."""
    return RemoteNode.from_config_dict({**_BASE_NODE, **extra})


# ---------------------------------------------------------------------- #
# Acceptance 1 — parse capabilities + queue
# ---------------------------------------------------------------------- #


def test_parse_capabilities_dedup_order_preserving_and_queue() -> None:
    """software dedup keeps first-seen order; queue override parsed verbatim."""
    node = _node(
        queue="bigmem",
        capabilities={"software": ["orca", "orca", "xtb"], "tags": ["gpu"]},
    )
    assert node.queue == "bigmem"
    assert node.capabilities is not None
    assert node.capabilities.software == ("orca", "xtb")
    assert node.capabilities.tags == ("gpu",)


def test_execution_config_end_to_end_parses_capabilities() -> None:
    """RemoteExecutionConfig.from_config_dict threads the new node fields."""
    cfg = RemoteExecutionConfig.from_config_dict(
        {
            "execution_mode": "remote",
            "queue": "normal",
            "nodes": [
                {
                    **_BASE_NODE,
                    "queue": "bigmem",
                    "capabilities": {
                        "software": ["orca", "orca", "xtb"],
                        "tags": ["gpu"],
                    },
                },
                {
                    "name": "compute-02",
                    "host": "10.0.0.2",
                    "username": "acp",
                    "remote_work_dir": "/w",
                    "remote_code_dir": "/c",
                },
            ],
        }
    )
    n1, n2 = cfg.nodes
    assert n1.queue == "bigmem"
    assert n1.capabilities is not None
    assert n1.capabilities.software == ("orca", "xtb")
    assert n1.capabilities.tags == ("gpu",)
    assert n2.queue is None
    assert n2.capabilities is None


# ---------------------------------------------------------------------- #
# Acceptance 2 — sentinel None + unknown-name drop with warning
# ---------------------------------------------------------------------- #


def test_missing_capabilities_and_queue_default_to_none() -> None:
    """No capabilities/queue keys → None sentinel on both parse and direct build."""
    node = _node()
    assert node.capabilities is None
    assert node.queue is None

    plain = RemoteNode(name="n", host="h", username="u", remote_work_dir="/w", remote_code_dir="/c")
    assert plain.capabilities is None
    assert plain.queue is None


def test_unknown_software_dropped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Unknown name "foo" is dropped, loading succeeds, and a warning is logged."""
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        node = _node(capabilities={"software": ["orca", "foo", "xtb"]})
    assert node.capabilities is not None
    assert node.capabilities.software == ("orca", "xtb")
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("foo" in r.getMessage() for r in warnings)
    # Warning must guide the user to the valid enum.
    assert any("molclus" in r.getMessage() for r in warnings)


def test_case_variant_software_dropped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Enum is exact-lowercase (parity with the probe script); "ORCA" is dropped."""
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        node = _node(capabilities={"software": ["ORCA", "orca"]})
    assert node.capabilities is not None
    assert node.capabilities.software == ("orca",)
    assert any("ORCA" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


# ---------------------------------------------------------------------- #
# Acceptance 3 — drift lock
# ---------------------------------------------------------------------- #


def test_declared_enum_matches_doctor_probe_script() -> None:
    """DECLARED_SOFTWARE_NAMES == names probed by _DOCTOR_SOFTWARE_SCRIPT.

    The probe names are extracted from the script's source text so the two
    sites cannot silently drift (plan todo 1, acceptance 3).
    """
    match = re.search(r"names\s*=\s*\[([^\]]*)\]", _DOCTOR_SOFTWARE_SCRIPT)
    assert match, "could not locate the names list in _DOCTOR_SOFTWARE_SCRIPT"
    probed = re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))
    assert probed, "probe-script names extraction returned nothing"
    assert len(probed) == len(set(probed)), "probe-script names list contains duplicates"
    assert DECLARED_SOFTWARE_NAMES == frozenset(probed)


# ---------------------------------------------------------------------- #
# Robustness — malformed input must never raise
# ---------------------------------------------------------------------- #


def test_blank_queue_treated_as_none() -> None:
    """Empty/whitespace queue values behave like an omitted override."""
    assert _node(queue="").queue is None
    assert _node(queue="   ").queue is None


def test_malformed_capabilities_block_never_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-mapping capabilities block → None sentinel (generic) + warning."""
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        node = _node(capabilities=["orca"])
    assert node.capabilities is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_non_list_software_and_tags_tolerated(caplog: pytest.LogCaptureFixture) -> None:
    """Scalar software/tags produce empty tuples instead of raising."""
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        node = _node(capabilities={"software": "orca", "tags": "gpu"})
    assert node.capabilities is not None
    assert node.capabilities.software == ()
    assert node.capabilities.tags == ()
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_non_string_items_dropped_and_values_stripped() -> None:
    """Non-str list entries are dropped; surrounding whitespace is stripped."""
    node = _node(
        capabilities={
            "software": ["orca", 3, None, " xtb "],
            "tags": ["gpu", 7, "gpu", ""],
        }
    )
    assert node.capabilities is not None
    assert node.capabilities.software == ("orca", "xtb")
    assert node.capabilities.tags == ("gpu",)


def test_tags_deduplicated_order_preserving() -> None:
    node = _node(capabilities={"software": [], "tags": ["gpu", "fast-scratch", "gpu"]})
    assert node.capabilities == NodeCapabilities(software=(), tags=("gpu", "fast-scratch"))


def test_empty_capabilities_block_is_declared_empty() -> None:
    """``capabilities: {}`` (block present) → empty declaration, not None."""
    node = _node(capabilities={})
    assert node.capabilities == NodeCapabilities(software=(), tags=())


def test_node_capabilities_is_frozen() -> None:
    caps = NodeCapabilities(software=("orca",), tags=("gpu",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.software = ()  # type: ignore[misc]
