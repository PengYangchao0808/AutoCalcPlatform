"""
RemoteNode.type field + to_config_dict round-trip + openlava factory tests.

Covers the acceptance criteria of todo 1 in ``.omo/plans/acp-init.md``
(design D2/D17):

1. ``RemoteNode.type`` defaults to ``"lsf"`` when absent; ``"lsf"`` and
   ``"openlava"`` parse verbatim; any other value (including non-strings)
   logs a warning and falls back to ``"lsf"`` without raising — the
   WARN-AND-DEFAULT contract required because
   ``RemoteExecutionConfig.from_config_dict`` runs at API-server startup
   and in ``acp doctor``.
2. ``RemoteNode.to_config_dict`` serializes the whole node so
   ``from_config_dict(to_config_dict())`` round-trips to an equal node
   (schema owner for the init wizard's node persistence, D17).
3. ``create_cluster_adapter`` maps ``openlava`` (bsub-compatible) onto
   ``LSFClusterAdapter`` without altering the ``enabled`` gate or the
   unknown-type local fallback.
"""

from __future__ import annotations

import logging

import pytest

from acp.scheduler.remote.config import (
    RemoteExecutionConfig,
    RemoteNode,
)
from cccp.qc.cluster import LSFClusterAdapter, create_cluster_adapter

_CONFIG_LOGGER = "acp.scheduler.remote.config"

_BASE_NODE: dict[str, object] = {
    "name": "compute-01",
    "host": "10.0.0.1",
    "username": "acp",
    "remote_work_dir": "/scratch/acp/acp_jobs",
    "remote_code_dir": "/home/acp/acp_code",
}

#: Keys ``to_config_dict`` must always emit (persisted schema, D17).
_ALWAYS_KEYS = (
    "name",
    "host",
    "username",
    "remote_work_dir",
    "remote_code_dir",
    "type",
    "enabled",
)

#: Keys ``to_config_dict`` omits when the field equals its parse default.
_CONDITIONAL_KEYS = (
    "port",
    "password",
    "key_file",
    "python_executable",
    "bin_symlinks",
    "max_concurrent_jobs",
    "host_key_policy",
    "queue",
    "capabilities",
)


def _node(**extra: object) -> RemoteNode:
    """Build a RemoteNode from the minimal valid mapping plus *extra* keys."""
    return RemoteNode.from_config_dict({**_BASE_NODE, **extra})


# ---------------------------------------------------------------------- #
# Acceptance (a) — full round-trip
# ---------------------------------------------------------------------- #


def test_round_trip_full_featured_node() -> None:
    """Every persisted field survives to_config_dict → from_config_dict."""
    node = _node(
        type="openlava",
        port=2222,
        password="secret",
        key_file="/home/acp/.ssh/id_rsa",
        python_executable="/opt/acp/venv/bin/python",
        bin_symlinks={"Shermo": "/opt/shermo/Shermo", "xtb": "/opt/xtb/bin/xtb"},
        max_concurrent_jobs=8,
        enabled=False,
        host_key_policy="warn",
        queue="bigmem",
        capabilities={"software": ["orca", "orca", "xtb"], "tags": ["gpu", "gpu"]},
    )
    restored = RemoteNode.from_config_dict(node.to_config_dict())
    assert restored == node


def test_round_trip_minimal_node() -> None:
    """A defaults-only node round-trips with just the always-emitted keys."""
    node = _node()
    data = node.to_config_dict()
    assert sorted(data.keys()) == sorted(_ALWAYS_KEYS)
    assert RemoteNode.from_config_dict(data) == node


def test_to_config_dict_capability_values_are_plain_lists() -> None:
    """capabilities serializes to plain lists reconstructed from the tuples."""
    node = _node(capabilities={"software": ["orca", "xtb"], "tags": ["gpu"]})
    caps = node.to_config_dict()["capabilities"]
    assert caps == {"software": ["orca", "xtb"], "tags": ["gpu"]}
    assert isinstance(caps["software"], list)
    assert isinstance(caps["tags"], list)


# ---------------------------------------------------------------------- #
# Acceptance (b) — absent type defaults to "lsf"
# ---------------------------------------------------------------------- #


def test_absent_type_defaults_to_lsf() -> None:
    assert _node().type == "lsf"


def test_lsf_and_openlava_parse_verbatim() -> None:
    assert _node(type="lsf").type == "lsf"
    assert _node(type="openlava").type == "openlava"


# ---------------------------------------------------------------------- #
# Acceptance (c) — invalid type warns and defaults, never raises
# ---------------------------------------------------------------------- #


def test_invalid_type_warns_and_defaults_to_lsf(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        node = _node(type="slurm")
    assert node.type == "lsf"
    assert any(
        "slurm" in record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )


def test_non_string_type_warns_and_defaults_to_lsf(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        node = _node(type=123)
    assert node.type == "lsf"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------- #
# Acceptance (d) — openlava factory branch
# ---------------------------------------------------------------------- #


def test_factory_openlava_branch_returns_lsf_adapter() -> None:
    adapter = create_cluster_adapter({"cluster": {"enabled": True, "type": "openlava"}})
    assert isinstance(adapter, LSFClusterAdapter)


def test_factory_lsf_branch_still_returns_lsf_adapter() -> None:
    adapter = create_cluster_adapter({"cluster": {"enabled": True, "type": "lsf"}})
    assert isinstance(adapter, LSFClusterAdapter)


def test_factory_unknown_type_still_falls_back_to_local(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from cccp.qc.cluster import LocalClusterAdapter

    with caplog.at_level(logging.WARNING, logger="cccp.qc.cluster"):
        adapter = create_cluster_adapter({"cluster": {"enabled": True, "type": "slurm"}})
    assert isinstance(adapter, LocalClusterAdapter)
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


# ---------------------------------------------------------------------- #
# Acceptance (e) — full RemoteExecutionConfig tolerates a bad node type
# ---------------------------------------------------------------------- #


def test_execution_config_tolerates_bad_node_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cluster: dict[str, object] = {
        "execution_mode": "remote",
        "queue": "normal",
        "nodes": [{**_BASE_NODE, "type": "slurm"}],
    }
    with caplog.at_level(logging.WARNING, logger=_CONFIG_LOGGER):
        cfg = RemoteExecutionConfig.from_config_dict(cluster)
    assert len(cfg.nodes) == 1
    assert cfg.nodes[0].type == "lsf"
    assert any(record.levelno >= logging.WARNING for record in caplog.records)
