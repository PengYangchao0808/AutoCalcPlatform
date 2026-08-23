# pyright: reportAny=false, reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false
"""CLI and study-routing tests for mechanism-study mode."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from acp.catalog import METHOD_SCHEMAS, normalize_and_validate_method_config
from acp.cli import _handle_mechanism, build_parser, main


def _mechanism_args(**overrides: object) -> Namespace:
    data: dict[str, object] = {
        "input": "C=C",
        "mechanism_config": None,
        "product": "CC",
        "ts_guess": None,
        "preset": None,
        "strategy": None,
        "fidelity": None,
        "routes": None,
        "scan_points": None,
        "irc_points": None,
        "study_id": None,
        "conformer_mode": None,
        "max_elementary_steps": None,
        "promotion_policy": None,
        "int_extension": False,
        "auto_converge": False,
        "output": "./out",
        "config": None,
        "nproc": None,
        "mem": None,
        "log_level": "INFO",
        "name": None,
        "charge": None,
        "multiplicity": None,
    }
    data.update(overrides)
    return Namespace(**data)


def test_mechanism_parser_accepts_study_flags() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "run",
            "mechanism",
            "--input",
            "C=C",
            "--product",
            "CC",
            "--study-id",
            "study_001",
            "--conformer-mode",
            "xtb-fast",
            "--max-elementary-steps",
            "5",
            "--int-extension",
            "--promotion-policy",
            "rate_relevant",
            "--auto-converge",
        ]
    )

    assert args.study_id == "study_001"
    assert args.conformer_mode == "xtb-fast"
    assert args.max_elementary_steps == 5
    assert args.int_extension is True
    assert args.promotion_policy == "rate_relevant"
    assert args.auto_converge is True


def test_mechanism_parser_accepts_config_only_invocation() -> None:
    parser = build_parser()

    args = parser.parse_args(["run", "mechanism", "--mechanism-config", "./mechanism_config.json"])

    assert args.input is None
    assert args.mechanism_config == "./mechanism_config.json"


def test_mechanism_cli_retired_rejects_study_flag_invocation(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Confsearch v1.0: the one-shot study CLI rejects new runs (rc=2)."""
    called = {"n": 0}

    def fake_run_mechanism_study(**kwargs: object) -> dict[str, object]:
        called["n"] += 1
        return {"status": "completed"}

    monkeypatch.setattr("acp.mechanism.study_runner.run_mechanism_study", fake_run_mechanism_study)

    assert (
        _handle_mechanism(
            _mechanism_args(output=str(tmp_path), study_id="study_001", conformer_mode="xtb-fast")
        )
        == 2
    )
    assert called["n"] == 0


def test_mechanism_cli_retired_rejects_default_invocation(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    called = {"n": 0}

    def fake_run_mechanism_study(**kwargs: object) -> dict[str, object]:
        called["n"] += 1
        return {"status": "completed"}

    monkeypatch.setattr("acp.mechanism.study_runner.run_mechanism_study", fake_run_mechanism_study)

    assert _handle_mechanism(_mechanism_args(output=str(tmp_path))) == 2
    assert called["n"] == 0


def test_mechanism_cli_retired_rejects_config_only_invocation(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "mechanism_config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "method": {},
                "resolved": {},
                "roles": {
                    "reactant": {"path": str(tmp_path / "reactant.xyz")},
                    "product": {"path": str(tmp_path / "product.xyz")},
                    "ts_guess": None,
                },
                "resources": {"nproc": 12, "mem": "32GB"},
            }
        ),
        encoding="utf-8",
    )
    assert (
        _handle_mechanism(_mechanism_args(output=str(tmp_path), mechanism_config=str(config_path)))
        == 2
    )


def test_mechanism_cli_retired_rejects_fidelity_overrides(monkeypatch: MonkeyPatch) -> None:
    assert (
        _handle_mechanism(
            _mechanism_args(output="./out", strategy="rph-reverse", fidelity="s4")
        )
        == 2
    )


def test_mechanism_cli_retired_rejects_missing_config_file(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    assert _handle_mechanism(_mechanism_args(output=str(tmp_path), mechanism_config=str(missing))) == 2


def test_mechanism_resume_parser_and_dispatch(monkeypatch: MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_handle(args: Namespace) -> int:
        captured["study"] = args.study
        captured["study_root"] = args.study_root
        captured["decision"] = args.decision
        return 0

    monkeypatch.setattr("acp.cli._handle_mechanism_resume", fake_handle)

    exit_code = main(
        [
            "mechanism",
            "resume",
            "--study",
            "study_001",
            "--study-root",
            "./mechanism_output",
            "--decision",
            "decision_001=continue",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "study": "study_001",
        "study_root": "./mechanism_output",
        "decision": ["decision_001=continue"],
    }


def test_catalog_mechanism_profile_and_new_fields_validate() -> None:
    schema = METHOD_SCHEMAS["mechanism"]
    profile_ids = {profile["profile_id"] for profile in schema["profiles"]}

    assert "guided-scan-fast" in profile_ids

    levels, errors = normalize_and_validate_method_config(
        {
            "levels": {
                "scan": {
                    "engine": "xtb",
                    "path_strategy": "guided-scan",
                    "fidelity": "s3",
                    "scan_points": 21,
                    "conformer_mode": "xtb-fast",
                    "max_elementary_steps": 4,
                    "int_extension": True,
                    "promotion_policy": "rate_relevant",
                    "auto_converge": True,
                },
                "ts_opt": {"engine": "orca", "functional": "B97-3c", "basis": ""},
                "freq": {"engine": "orca", "functional": "B97-3c", "basis": ""},
                "sp": {"engine": "orca", "functional": "r2SCAN-3c", "basis": ""},
                "irc": {"engine": "orca", "functional": "B97-3c", "basis": "", "irc_points": 30},
            }
        },
        schema,
    )

    assert not errors
    assert levels["scan"]["conformer_mode"] == "xtb-fast"
    assert levels["scan"]["max_elementary_steps"] == 4
    assert levels["scan"]["int_extension"] is True
    assert levels["scan"]["promotion_policy"] == "rate_relevant"
    assert levels["scan"]["auto_converge"] is True


def test_mechanism_cli_retired_waiting_flow_rejected(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """The retired CLI rejects runs that would previously enter review wait."""
    monkeypatch.setattr(
        "acp.mechanism.study_runner.waiting_study_exists", lambda root, sid: False
    )
    assert _handle_mechanism(_mechanism_args(output=str(tmp_path))) == 2


def test_mechanism_cli_retired_restart_rejected(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """A waiting checkpoint no longer re-enters the (retired) CLI study flow."""
    monkeypatch.setattr(
        "acp.mechanism.study_runner.waiting_study_exists", lambda root, sid: True
    )
    monkeypatch.setattr(
        "acp.mechanism.study_runner.read_review_handoff",
        lambda root: ("study_restart", []),
    )
    assert _handle_mechanism(_mechanism_args(output=str(tmp_path))) == 2


