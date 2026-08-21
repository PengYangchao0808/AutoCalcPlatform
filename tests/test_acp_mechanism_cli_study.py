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


def test_mechanism_study_routing_when_study_flag_present(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run_mechanism_study(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "study_id": "study_001",
            "study_dir": str(tmp_path / "mechanism_study" / "study_001"),
            "status": "completed",
            "network_size": {"states": 2, "elementary_steps": 1},
            "gates_summary": {"G0": "pass"},
            "pending_decisions": [],
        }

    monkeypatch.setattr("acp.mechanism.study_runner.run_mechanism_study", fake_run_mechanism_study)

    exit_code = _handle_mechanism(
        _mechanism_args(output=str(tmp_path), study_id="study_001", conformer_mode="xtb-fast")
    )

    assert exit_code == 0
    assert captured["study_id"] == "study_001"
    assert captured["conformer_mode"] == "xtb-fast"
    assert captured["max_elementary_steps"] is None


def test_mechanism_defaults_to_study_routing_without_study_flags(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run_mechanism_study(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "study_id": "study_default",
            "study_dir": str(tmp_path / "mechanism_study" / "study_default"),
            "status": "completed",
            "network_size": {"states": 2, "elementary_steps": 1},
            "gates_summary": {"G0": "pass"},
            "pending_decisions": [],
        }

    monkeypatch.setattr("acp.mechanism.study_runner.run_mechanism_study", fake_run_mechanism_study)

    exit_code = _handle_mechanism(_mechanism_args(output=str(tmp_path)))

    assert exit_code == 0
    assert captured["input_source"] == "C=C"
    assert captured["product_source"] == "CC"
    assert captured["study_id"] is None
    assert captured["conformer_mode"] is None
    assert captured["max_elementary_steps"] is None
    assert captured["promotion_policy"] is None


def test_mechanism_config_only_run_loads_paths_and_resources(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    reactant = tmp_path / "reactant.xyz"
    product = tmp_path / "product.xyz"
    reactant.write_text("1\nR\nH 0 0 0\n", encoding="utf-8")
    product.write_text("1\nP\nH 0 0 0\n", encoding="utf-8")
    mechanism_config = tmp_path / "mechanism_config.json"
    mechanism_config.write_text(
        json.dumps(
            {
                "version": 1,
                "method": {
                    "levels": {
                        "scan": {"scan_points": 25, "conformer_mode": "xtb-fast"},
                        "sp": {"functional": "wB97M-V", "basis": "def2-TZVPP"},
                    }
                },
                "resolved": {
                    "preset": "rph-s3",
                    "strategy": "guided-scan",
                    "fidelity": "s3",
                    "scan_points": 21,
                    "irc_points": 30,
                    "study_id": "study_cfg",
                },
                "roles": {
                    "reactant": {
                        "path": "reactant.xyz",
                        "charge": 0,
                        "multiplicity": 1,
                    },
                    "product": {"path": "product.xyz", "charge": 0, "multiplicity": 1},
                    "ts_guess": None,
                },
                "resources": {"nproc": 8, "mem": "16GB"},
            }
        ),
        encoding="utf-8",
    )

    def fake_run_mechanism_study(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "study_id": "study_cfg",
            "study_dir": str(tmp_path / "mechanism_study" / "study_cfg"),
            "status": "completed",
            "network_size": {"states": 2, "elementary_steps": 1},
            "gates_summary": {"G0": "pass"},
            "pending_decisions": [],
        }

    monkeypatch.setattr("acp.mechanism.study_runner.run_mechanism_study", fake_run_mechanism_study)

    exit_code = _handle_mechanism(
        _mechanism_args(
            input=None,
            product=None,
            output=str(tmp_path),
            mechanism_config=str(mechanism_config),
        )
    )

    assert exit_code == 0
    assert captured["input_source"] == str(reactant)
    assert captured["product_source"] == str(product)
    assert captured["charge"] == 0
    assert captured["multiplicity"] == 1
    assert captured["preset"] == "rph-s3"
    assert captured["study_id"] == "study_cfg"
    assert captured["mechanism_config_path"] == mechanism_config
    config = captured["config"]
    assert isinstance(config, dict)
    assert config["resources"]["nproc"] == 8
    assert config["resources"]["mem"] == "16GB"


def test_mechanism_config_cli_fidelity_overrides_file(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    reactant = tmp_path / "reactant.xyz"
    reactant.write_text("1\nR\nH 0 0 0\n", encoding="utf-8")
    mechanism_config = tmp_path / "mechanism_config.json"
    mechanism_config.write_text(
        json.dumps(
            {
                "version": 1,
                "method": {},
                "resolved": {"fidelity": "s3"},
                "roles": {
                    "reactant": {"path": str(reactant), "charge": 0, "multiplicity": 1},
                    "product": None,
                    "ts_guess": None,
                },
                "resources": {},
            }
        ),
        encoding="utf-8",
    )

    def fake_run_mechanism_study(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "study_id": "study_override",
            "study_dir": str(tmp_path / "mechanism_study" / "study_override"),
            "status": "completed",
            "network_size": {"states": 1, "elementary_steps": 0},
            "gates_summary": {},
            "pending_decisions": [],
        }

    monkeypatch.setattr("acp.mechanism.study_runner.run_mechanism_study", fake_run_mechanism_study)

    exit_code = _handle_mechanism(
        _mechanism_args(
            input=None,
            product=None,
            fidelity="s4",
            output=str(tmp_path),
            mechanism_config=str(mechanism_config),
        )
    )

    assert exit_code == 0
    assert captured["fidelity"] == "s4"


def test_mechanism_config_missing_file_returns_error(tmp_path: Path) -> None:
    exit_code = _handle_mechanism(
        _mechanism_args(
            input=None,
            product=None,
            output=str(tmp_path),
            mechanism_config=str(tmp_path / "missing.json"),
        )
    )

    assert exit_code == 1


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


def test_mechanism_study_waiting_exits_with_review_code(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    from acp.scheduler.jobs import EXIT_WAITING_REVIEW

    waiting_summary = {
        "study_id": "study_gate",
        "study_dir": str(tmp_path / "mechanism_study" / "study_gate"),
        "status": "waiting",
        "network_size": {"states": 2, "elementary_steps": 0},
        "gates_summary": {"G3": "review"},
        "pending_decisions": ["decision_1"],
    }
    monkeypatch.setattr(
        "acp.mechanism.study_runner.run_mechanism_study",
        lambda **_kwargs: waiting_summary,
    )

    exit_code = _handle_mechanism(
        _mechanism_args(output=str(tmp_path), study_id="study_gate")
    )

    assert exit_code == EXIT_WAITING_REVIEW
    payload_path = tmp_path / "review_payload.json"
    assert payload_path.exists()
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["study_id"] == "study_gate"
    assert payload["status"] == "waiting"
    assert payload["pending_decisions"] == ["decision_1"]


def test_mechanism_study_restart_resumes_waiting_checkpoint(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    study_dir = tmp_path / "mechanism_study" / "study_gate"
    study_dir.mkdir(parents=True)
    (study_dir / "study.json").write_text(
        json.dumps({"study_id": "study_gate", "status": "waiting", "metadata": {}}),
        encoding="utf-8",
    )
    (tmp_path / "job.json").write_text(
        json.dumps(
            {
                "id": "job-x",
                "result": {
                    "review_payload": {"study_id": "study_gate", "status": "waiting"},
                    "review_resolution": {"requeue": True, "decisions": {"decision_1": "approve"}},
                },
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_resume(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "study_id": "study_gate",
            "study_dir": str(study_dir),
            "status": "running",
            "network_size": {"states": 2, "elementary_steps": 1},
            "gates_summary": {"G0": "pass"},
            "pending_decisions": [],
        }

    monkeypatch.setattr("acp.mechanism.study_runner.resume_mechanism_study", fake_resume)

    exit_code = _handle_mechanism(_mechanism_args(output=str(tmp_path)))

    assert exit_code == 0
    assert captured["study_id"] == "study_gate"
    assert Path(str(captured["study_root"])) == tmp_path
    assert captured["decision_resolutions"] == {"decision_1": "approve"}
