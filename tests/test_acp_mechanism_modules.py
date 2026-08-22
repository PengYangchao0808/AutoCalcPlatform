"""Tests for the standalone mechanism module layer (M1-M4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.mechanism.modules.module_confirm import run_confirm_module
from acp.mechanism.modules.module_conformer import run_conformer_module
from acp.mechanism.modules.module_step import run_step_module
from acp.mechanism.modules.schema import (
    ELEMENTARY_STEP_FILENAME,
    MANIFEST_FILENAME,
    read_elementary_step_manifest,
)
from acp.mechanism.providers.contracts import RefinementManifest
from acp.mechanism.providers.fake import (
    FakeEndpointProvider,
    FakeEnsembleProvider,
    FakePathSearchStrategy,
    FakeRefinementProvider,
)

_XYZ = """\
3
test
C 0.00000000 0.00000000 0.00000000
H 1.00000000 0.00000000 0.00000000
H 0.00000000 1.00000000 0.00000000
"""

_PLAN = {
    "coordinates": [{"id": "rc1", "kind": "distance", "atoms": [0, 1], "start": 2.0, "end": 1.0}],
    "points": 5,
}


class _NullRefinementProvider(FakeRefinementProvider):
    """Refinement provider that never produces a canonical winner."""

    def refine(self, requests, fidelity):  # noqa: ANN001, ANN201
        manifest = super().refine(requests, fidelity)
        return RefinementManifest(
            manifest_id=manifest.manifest_id,
            canonical_winner=None,
            attempts=manifest.attempts,
            fidelity=manifest.fidelity,
            metadata=manifest.metadata,
        )


def _write_xyz(path: Path) -> Path:
    path.write_text(_XYZ, encoding="utf-8")
    return path


def _fake_step_providers() -> dict[str, object]:
    return {
        "path_strategy": FakePathSearchStrategy(),
        "refinement_provider": FakeRefinementProvider(),
        "endpoint_provider": FakeEndpointProvider(),
    }


def test_conformer_module_with_fake_provider(tmp_path: Path) -> None:
    manifest = run_conformer_module(
        "CCO",
        output_dir=tmp_path,
        ensemble_provider=FakeEnsembleProvider(),
    )
    assert manifest.status == "validated"
    assert manifest.phase == "conformer"
    assert (tmp_path / MANIFEST_FILENAME).exists()
    representative = Path(manifest.output["representative_xyz"])
    assert representative.exists()
    ensemble_manifest = Path(manifest.output["ensemble_manifest"])
    assert ensemble_manifest.exists()
    payload = json.loads(ensemble_manifest.read_text(encoding="utf-8"))
    assert payload["n_records"] >= 1
    assert manifest.provenance["provider"] == "acp-native-censo-lite"
    assert manifest.provenance["profile_id"] == "censo-lite"


def test_conformer_module_failure_returns_failed_manifest(tmp_path: Path) -> None:
    class _BoomProvider:
        def generate(self, state, profile):  # noqa: ANN001, ANN202
            raise RuntimeError("boom")

    manifest = run_conformer_module(
        "CCO",
        output_dir=tmp_path,
        ensemble_provider=_BoomProvider(),
    )
    assert manifest.status == "failed"
    assert manifest.failure is not None
    assert manifest.failure.stage == "conformer"
    assert (tmp_path / MANIFEST_FILENAME).exists()


def test_step_module_end_to_end_with_fakes(tmp_path: Path) -> None:
    source = _write_xyz(tmp_path / "source.xyz")
    target = _write_xyz(tmp_path / "target.xyz")
    out = tmp_path / "step"
    manifest = run_step_module(
        str(source),
        dict(_PLAN),
        target_xyz=str(target),
        strategy="rph-reverse",
        fidelity="s3",
        output_dir=out,
        providers=_fake_step_providers(),
    )
    assert manifest.status == "validated"
    assert manifest.gates == {"G2": "PASS", "G3": "PASS", "G4": "PASS"}
    assert manifest.transition_state is not None
    assert manifest.transition_state["canonical_id"]
    assert Path(manifest.transition_state["xyz"]).exists()
    assert manifest.irc is not None
    endpoints = manifest.irc["endpoints"]
    assert set(endpoints) == {"forward", "reverse"}
    roles = {endpoints[d]["role"] for d in endpoints}
    assert roles == {"source", "sink"}
    sink = next(d for d in endpoints if endpoints[d]["role"] == "sink")
    assert endpoints[sink]["match_verdict"] == "NEW_STATE"
    assert endpoints[sink]["matched_state_id"] == "state_int"
    assert (out / ELEMENTARY_STEP_FILENAME).exists()
    reloaded = read_elementary_step_manifest(out / ELEMENTARY_STEP_FILENAME)
    assert reloaded.status == "validated"
    assert reloaded.irc is not None


def test_step_module_partial_when_no_canonical_ts(tmp_path: Path) -> None:
    source = _write_xyz(tmp_path / "source.xyz")
    providers = _fake_step_providers()
    providers["refinement_provider"] = _NullRefinementProvider()
    manifest = run_step_module(
        str(source),
        dict(_PLAN),
        strategy="guided-scan",
        output_dir=tmp_path / "step",
        providers=providers,
    )
    assert manifest.status == "partial"
    assert manifest.furthest_stage == "refinement"
    assert manifest.failure is not None
    assert manifest.failure.stage == "refinement"
    assert manifest.failure.reason == "no_canonical_stationary_point"
    assert manifest.failure.recoverable is True
    assert manifest.suggested_actions == [
        "retry_refinement",
        "change_seed",
        "manual_takeover",
    ]
    assert manifest.gates["G3"] == "FAIL"


def test_confirm_module_with_fake_refinement(tmp_path: Path) -> None:
    source = _write_xyz(tmp_path / "source.xyz")
    step_dir = tmp_path / "step"
    step_manifest = run_step_module(
        str(source),
        dict(_PLAN),
        strategy="guided-scan",
        output_dir=step_dir,
        providers=_fake_step_providers(),
    )
    assert step_manifest.status == "validated"
    confirm_manifest = run_confirm_module(
        step_dir / ELEMENTARY_STEP_FILENAME,
        select="ts:canonical",
        output_dir=tmp_path / "confirm",
        refinement_provider=FakeRefinementProvider(),
    )
    assert confirm_manifest.status == "validated"
    assert confirm_manifest.phase == "confirmation"
    assert Path(confirm_manifest.output["canonical_xyz"]).exists()
    assert Path(confirm_manifest.output["stationary_manifest"]).exists()
    assert confirm_manifest.output["s3_s4_consistency"] == []


def test_chain_two_fake_steps(tmp_path: Path) -> None:
    from acp.mechanism.chain import run_chain

    chain_config = {
        "steps": [
            {
                "module": "mech-conf",
                "args": {
                    "structure_source": "CCO",
                    "output_dir": str(tmp_path / "conf"),
                },
            },
            {
                "module": "mech-step",
                "args": {
                    "source_xyz": "${prev.output.representative_xyz}",
                    "coordinate_plan": dict(_PLAN),
                    "strategy": "guided-scan",
                    "output_dir": str(tmp_path / "step"),
                },
            },
        ]
    }
    records = run_chain(
        chain_config,
        providers={
            "ensemble_provider": FakeEnsembleProvider(),
            "step_providers": _fake_step_providers(),
        },
    )
    assert [r["module"] for r in records] == ["mech-conf", "mech-step"]
    assert all(r["status"] == "validated" for r in records)
    assert Path(records[0]["manifest_path"]).exists()
    assert Path(records[1]["manifest_path"]).exists()


def test_chain_from_yaml(tmp_path: Path) -> None:
    import yaml

    from acp.mechanism.chain import run_chain_from_yaml

    chain_path = tmp_path / "chain.yaml"
    chain_path.write_text(
        yaml.safe_dump(
            {
                "steps": [
                    {
                        "module": "mech-conf",
                        "args": {
                            "structure_source": "CCO",
                            "output_dir": str(tmp_path / "conf"),
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    records = run_chain_from_yaml(
        chain_path, providers={"ensemble_provider": FakeEnsembleProvider()}
    )
    assert len(records) == 1
    assert records[0]["status"] == "validated"


def test_cli_mech_subcommands_parse() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    conf = parser.parse_args(["run", "mech-conf", "--input", "CCO"])
    assert conf.workflow == "mech-conf"
    step = parser.parse_args(["run", "mech-step", "--source", "a.xyz", "--plan", "{}"])
    assert step.workflow == "mech-step"
    alias = parser.parse_args(["run", "mech-sr", "--source", "a.xyz", "--plan", "{}"])
    assert alias.workflow == "mech-step"
    confirm = parser.parse_args(["run", "mech-confirm", "--from", "step.json"])
    assert confirm.workflow == "mech-confirm"
    chain = parser.parse_args(["run", "mech-chain", "--config", "chain.yaml"])
    assert chain.workflow == "mech-chain"


@pytest.mark.parametrize(
    "subcommand",
    ["mech-conf", "mech-step", "mech-confirm", "mech-chain"],
)
def test_cli_mech_help_exits_zero(subcommand: str) -> None:
    from acp.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["run", subcommand, "--help"])
    assert exc_info.value.code == 0
