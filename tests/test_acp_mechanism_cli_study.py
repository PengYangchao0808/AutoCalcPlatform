# pyright: reportAny=false, reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnannotatedClassAttribute=false
"""CLI and study-routing tests for mechanism-study mode."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

from _pytest.monkeypatch import MonkeyPatch

from acp.catalog import METHOD_SCHEMAS, normalize_and_validate_method_config
from acp.cli import _handle_mechanism, build_parser, main


def _mechanism_args(**overrides: object) -> Namespace:
    data: dict[str, object] = {
        "input": "C=C",
        "product": "CC",
        "ts_guess": None,
        "preset": None,
        "strategy": None,
        "fidelity": None,
        "routes": None,
        "scan_points": None,
        "irc_points": None,
        "study_id": None,
        "conformer_mode": "auto",
        "conformer_mode_explicit": False,
        "max_elementary_steps": 3,
        "max_elementary_steps_explicit": False,
        "promotion_policy": "all_confirmed",
        "promotion_policy_explicit": False,
        "int_extension": False,
        "int_extension_explicit": False,
        "auto_converge": False,
        "auto_converge_explicit": False,
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
    assert args.conformer_mode_explicit is True
    assert args.max_elementary_steps == 5
    assert args.max_elementary_steps_explicit is True
    assert args.int_extension is True
    assert args.int_extension_explicit is True
    assert args.promotion_policy == "rate_relevant"
    assert args.promotion_policy_explicit is True
    assert args.auto_converge is True
    assert args.auto_converge_explicit is True


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
        _mechanism_args(
            output=str(tmp_path),
            study_id="study_001",
            conformer_mode="xtb-fast",
            conformer_mode_explicit=True,
        )
    )

    assert exit_code == 0
    assert captured["study_id"] == "study_001"
    assert captured["conformer_mode"] == "xtb-fast"
    assert captured["max_elementary_steps"] == 3


def test_mechanism_legacy_routing_without_study_flags(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    called: dict[str, object] = {}

    def fake_run_mechanism_analysis(**kwargs: object) -> SimpleNamespace:
        called.update(kwargs)
        return SimpleNamespace(
            status="completed",
            error=None,
            metadata={
                "n_structures": 1,
                "energy_profile": {
                    "forward_barrier_kcal_mol": 1.0,
                    "reaction_energy_kcal_mol": -2.0,
                },
            },
        )

    monkeypatch.setattr(
        "acp.workflows.mechanism.run_mechanism_analysis",
        fake_run_mechanism_analysis,
    )

    exit_code = _handle_mechanism(_mechanism_args(output=str(tmp_path)))

    assert exit_code == 0
    assert called["input_source"] == "C=C"
    assert called["product_source"] == "CC"
    assert "study_id" not in called


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
        _mechanism_args(
            output=str(tmp_path),
            study_id="study_gate",
            conformer_mode_explicit=True,
        )
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

    exit_code = _handle_mechanism(
        _mechanism_args(output=str(tmp_path), conformer_mode_explicit=True)
    )

    assert exit_code == 0
    assert captured["study_id"] == "study_gate"
    assert Path(str(captured["study_root"])) == tmp_path
    assert captured["decision_resolutions"] == {"decision_1": "approve"}
