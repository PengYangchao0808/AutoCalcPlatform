"""TDD tests for acp.init_wizard.sniff_local — local sniffing + manual spec.

Covers plan task 4 acceptance criteria on a NON-DEFAULT tmp target:
(a) 2 resolved + 1 missing → scripted manual path → target YAML gains
``executables.xtb.path`` AND the config dict passed into the (monkeypatched)
discover layer contains that path (anti-vacuity: proves the
target→config-view plumbing) → re-sniff marks xtb resolved source="config";
(b) empty input skips the software (absent from YAML);
(c) nonexistent/non-executable path rejected with re-prompt, then accepted.
"""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest
import yaml

from acp.init_wizard.sniff_local import (
    PromptBundle,
    apply_local_spec,
    manual_spec_local,
    render_sniff_table,
    sniff_local,
    versions_via,
)
from cccp.software import SoftwareDiscovery

# ---------------------------------------------------------------------------
# Fixtures and fakes
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate the 6-source merge from the runner's environment: point ~ at
    tmp_path (kills source-2 ``~/.cccp.yaml``) and chdir to tmp_path (kills
    source-3 ``./cccp.yaml`` — the repo root carries a real xtb path).
    Defaults still resolve via the ``__file__``-based fallback, and source 4
    (the wizard target) keeps overriding — keeps the "xtb initially missing"
    premise hermetic on dev machines (Wave-1 test-pollution note)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def _make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_bins(tmp_path: Path) -> dict[str, Path]:
    """Deterministic stand-in executables for orca/crest/xtb."""
    return {
        "orca": _make_executable(tmp_path / "fake_orca"),
        "crest": _make_executable(tmp_path / "fake_crest"),
        "xtb": _make_executable(tmp_path / "fake_xtb"),
    }


def _script_input(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    """Feed a scripted answer sequence to builtins.input."""
    it = iter(answers)
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(it))


def _install_fake_discover(
    monkeypatch: pytest.MonkeyPatch,
    fake_bins: dict[str, Path],
    captured: list[dict[str, Any] | None],
) -> None:
    """Replace cccp.software.discover_all_detailed, CAPTURING the config kwarg.

    Deterministic picture: orca + crest always resolved (source="path");
    xtb resolves ONLY from an explicit valid ``executables.xtb.path`` in the
    passed config (source="config") — so the initial sniff shows
    2 resolved + 1 missing and the re-sniff flips xtb to source="config".
    """
    from cccp import software as cccp_software

    def fake_discover_all_detailed(
        config: dict[str, Any] | None = None,
    ) -> dict[str, SoftwareDiscovery]:
        captured.append(config)
        executables = (config or {}).get("executables", {})
        entries: dict[str, SoftwareDiscovery] = {}
        for name in ("orca", "crest", "xtb"):
            if name == "xtb":
                configured = (executables.get("xtb") or {}).get("path")
                resolved = Path(configured) if configured and Path(configured).is_file() else None
                source = "config" if resolved is not None else None
            else:
                resolved = fake_bins[name]
                source = "path"
            entries[name] = SoftwareDiscovery(name=name, resolved=resolved, source=source)
        return entries

    monkeypatch.setattr(cccp_software, "discover_all_detailed", fake_discover_all_detailed)


def _install_fake_versions(monkeypatch: pytest.MonkeyPatch, versions: dict[str, str]) -> None:
    """Replace cccp.software.version_cached with a deterministic probe."""
    from cccp import software as cccp_software

    def fake_version_cached(name: str, executable: Path | None) -> str:
        if executable is None:
            return ""
        return versions.get(name, "")

    monkeypatch.setattr(cccp_software, "version_cached", fake_version_cached)


# ---------------------------------------------------------------------------
# sniff_local: target → config-view plumbing (anti-vacuity core)
# ---------------------------------------------------------------------------


def test_sniff_local_initial_shows_two_resolved_one_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bins: dict[str, Path]
) -> None:
    captured: list[dict[str, Any] | None] = []
    _install_fake_discover(monkeypatch, fake_bins, captured)
    _install_fake_versions(monkeypatch, {"orca": "5.0.4", "crest": "1.1"})

    target = tmp_path / "wizard" / "non_default_cccp.yaml"
    entries = sniff_local(target)

    assert entries["orca"].resolved == fake_bins["orca"]
    assert entries["crest"].resolved == fake_bins["crest"]
    assert entries["xtb"].resolved is None
    assert entries["xtb"].source is None
    # a missing target is simply not merged as source 4 — no crash
    assert captured[0] is not None


def test_sniff_local_passes_target_as_explicit_config_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bins: dict[str, Path]
) -> None:
    """Anti-vacuity: the config arg reaching the discover layer must contain
    the path written to the NON-DEFAULT target file (source-4 merge)."""
    captured: list[dict[str, Any] | None] = []
    _install_fake_discover(monkeypatch, fake_bins, captured)
    _install_fake_versions(monkeypatch, {"xtb": "6.6.1"})

    target = tmp_path / "non_default_cccp.yaml"
    target.write_text(
        yaml.dump({"executables": {"xtb": {"path": str(fake_bins["xtb"])}}}),
        encoding="utf-8",
    )

    entries = sniff_local(target)

    config_arg = captured[0]
    assert config_arg is not None
    assert config_arg["executables"]["xtb"]["path"] == str(fake_bins["xtb"])
    assert entries["xtb"].source == "config"
    assert entries["xtb"].resolved == fake_bins["xtb"]


# ---------------------------------------------------------------------------
# versions_via + render_sniff_table
# ---------------------------------------------------------------------------


def test_versions_via_decorates_entries(
    monkeypatch: pytest.MonkeyPatch, fake_bins: dict[str, Path]
) -> None:
    _install_fake_versions(monkeypatch, {"orca": "5.0.4"})
    entries = {
        "orca": SoftwareDiscovery(name="orca", resolved=fake_bins["orca"], source="path"),
        "xtb": SoftwareDiscovery(name="xtb", resolved=None, source=None),
    }
    assert versions_via(entries) == {"orca": "5.0.4", "xtb": ""}


def test_render_sniff_table_plain_text_columns(
    monkeypatch: pytest.MonkeyPatch, fake_bins: dict[str, Path]
) -> None:
    _install_fake_versions(monkeypatch, {"orca": "5.0.4", "crest": "1.1"})
    entries = {
        "orca": SoftwareDiscovery(name="orca", resolved=fake_bins["orca"], source="path"),
        "xtb": SoftwareDiscovery(name="xtb", resolved=None, source=None),
    }
    table = render_sniff_table(entries)
    for token in ("软件", "解析路径", "版本", "来源", "orca", "5.0.4", "path"):
        assert token in table
    assert "xtb" in table
    assert "未找到" in table


# ---------------------------------------------------------------------------
# manual_spec_local (D10): validate + version display + empty-input skip
# ---------------------------------------------------------------------------


def test_manual_spec_accepts_valid_path_and_shows_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_bins: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cccp import software as cccp_software

    monkeypatch.setattr(cccp_software, "detect_version", lambda name, executable: "6.6.1")
    _script_input(monkeypatch, [str(fake_bins["xtb"])])

    specs = manual_spec_local(["xtb"], PromptBundle())

    assert specs == {"xtb": str(fake_bins["xtb"])}
    out = capsys.readouterr().out
    assert "6.6.1" in out  # version probe result displayed as confirmation


def test_manual_spec_shows_unknown_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_bins: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cccp import software as cccp_software

    def _boom(name: str, executable: Path | None) -> str | None:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(cccp_software, "detect_version", _boom)
    _script_input(monkeypatch, [str(fake_bins["xtb"])])

    specs = manual_spec_local(["xtb"], PromptBundle())

    # probe failure must NOT abort — operator's explicit path wins (D10)
    assert specs == {"xtb": str(fake_bins["xtb"])}
    assert "未知" in capsys.readouterr().out


def test_manual_spec_empty_input_skips(
    monkeypatch: pytest.MonkeyPatch, fake_bins: dict[str, Path]
) -> None:
    _script_input(monkeypatch, [""])
    specs = manual_spec_local(["xtb"], PromptBundle())
    assert specs == {}


def test_manual_spec_rejects_invalid_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_bins: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cccp import software as cccp_software

    monkeypatch.setattr(cccp_software, "detect_version", lambda name, exe: None)
    plain = tmp_path / "plain.txt"
    plain.write_text("not executable", encoding="utf-8")
    plain.chmod(0o644)

    _script_input(
        monkeypatch,
        [str(tmp_path / "missing_binary"), str(plain), str(fake_bins["xtb"])],
    )

    specs = manual_spec_local(["xtb"], PromptBundle())

    assert specs == {"xtb": str(fake_bins["xtb"])}
    out = capsys.readouterr().out
    assert "可执行文件" in out  # validator rejection message shown twice


def test_manual_spec_via_injected_fake_bundle(fake_bins: dict[str, Path]) -> None:
    """PromptBundle contract: tests inject fakes as PromptBundle(ask=fake)."""
    seen_prompts: list[str] = []

    def fake_ask(
        prompt: str,
        default: str | None = None,
        validate: Any = None,
        allow_empty: bool = False,
    ) -> str:
        seen_prompts.append(prompt)
        assert allow_empty is True  # skip-ability contract (ask_local_path has none)
        assert validate is not None
        assert validate("/nonexistent/binary") is not None
        assert validate(str(fake_bins["xtb"])) is None
        return str(fake_bins["xtb"])

    specs = manual_spec_local(["xtb"], PromptBundle(ask=fake_ask))

    assert specs == {"xtb": str(fake_bins["xtb"])}
    assert "xtb" in seen_prompts[0]


# ---------------------------------------------------------------------------
# apply_local_spec: persist + re-sniff end-to-end (acceptance a + b)
# ---------------------------------------------------------------------------


def test_apply_local_spec_writes_target_and_resniffs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bins: dict[str, Path]
) -> None:
    captured: list[dict[str, Any] | None] = []
    _install_fake_discover(monkeypatch, fake_bins, captured)
    _install_fake_versions(monkeypatch, {"xtb": "6.6.1"})
    _script_input(monkeypatch, [str(fake_bins["xtb"])])

    target = tmp_path / "wizard" / "non_default_cccp.yaml"
    target.parent.mkdir()
    data: dict[str, Any] = {"nproc": 4}

    specs = manual_spec_local(["xtb"], PromptBundle())
    entries = apply_local_spec(target, data, specs)

    # target YAML gains executables.xtb.path; unrelated keys preserved
    saved = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert saved["executables"]["xtb"]["path"] == str(fake_bins["xtb"])
    assert saved["nproc"] == 4
    # anti-vacuity: the LAST config arg passed to the discover layer
    # (the re-sniff) carries the freshly written path
    last_config = captured[-1]
    assert last_config is not None
    assert last_config["executables"]["xtb"]["path"] == str(fake_bins["xtb"])
    # re-sniff marks xtb resolved with source="config"
    assert entries["xtb"].source == "config"
    assert entries["xtb"].resolved == fake_bins["xtb"]
    assert entries["orca"].resolved == fake_bins["orca"]
    assert entries["crest"].resolved == fake_bins["crest"]


def test_apply_local_spec_empty_specs_leaves_no_executables_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bins: dict[str, Path]
) -> None:
    """Acceptance (b): skipped software absent from YAML."""
    captured: list[dict[str, Any] | None] = []
    _install_fake_discover(monkeypatch, fake_bins, captured)
    _install_fake_versions(monkeypatch, {})
    _script_input(monkeypatch, [""])  # skip

    specs = manual_spec_local(["xtb"], PromptBundle())
    assert specs == {}

    target = tmp_path / "wizard" / "non_default_cccp.yaml"
    target.parent.mkdir()
    entries = apply_local_spec(target, {}, specs)

    saved = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert "executables" not in saved
    # re-sniff still shows xtb missing
    assert entries["xtb"].resolved is None
    assert entries["xtb"].source is None
