"""Docs contract tests — manual/README/AGENTS examples must match the fixed CLI semantics.

real-qc-gap-remediation todo 13 (T12/D7).  These are grep-style assertions that
mechanically pin the post-fix documentation contract:

(a) ``--ts-provenance-json`` appears ONLY in inline-JSON semantic contexts in
    manual+README; file-path examples use ``--ts-provenance``;
(b) every runnable IRC example carries one provenance group member AND
    ``--input-role transition_state``; tsmode examples contain no provenance flag;
(c) runnable scan/irc examples contain no ``--step`` (compat-note mentions are
    fine — only command lines are checked);
(d) scan default description carries the ScanTS opt-in wording;
(e) live ``acp run <wf> --help`` output matches the documented flags/wordings.

Assertions are regex/substring based (no brittle full-text equality).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANUAL = REPO_ROOT / "docs" / "ACP_Function_Test_Manual.md"
README = REPO_ROOT / "README.md"
ROOT_AGENTS = REPO_ROOT / "AGENTS.md"

DOC_FILES = (MANUAL, README, ROOT_AGENTS)

INLINE_JSON_MARKERS = re.compile(r"内联|inline|调度器|scheduler", re.IGNORECASE)
PROVENANCE_FILE_FLAG = re.compile(r"--ts-provenance(?!-json)")  # file form only
PROVENANCE_ANY_FLAG = re.compile(r"--ts-provenance(?:-json)?\b")
STEP_FLAG = re.compile(r"(?<![\w-])--step\b")  # not --max-steps / --scan-points


def _read(path: Path) -> str:
    assert path.exists(), f"missing doc file: {path}"
    return path.read_text(encoding="utf-8")


def _fenced_blocks(text: str) -> list[str]:
    """Return all fenced code blocks (``` ... ```), content only."""
    return re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)


def _commands(text: str, workflow: str) -> list[str]:
    """Extract each `acp run <workflow>` command with its backslash continuations.

    Command granularity (not whole block) so adjacent commands in one fenced
    block cannot contaminate each other's flags.
    """
    commands: list[str] = []
    for block in _fenced_blocks(text):
        lines = block.splitlines()
        starts = [i for i, ln in enumerate(lines) if re.match(rf"\s*acp run {workflow}\b", ln)]
        for start in starts:
            chunk = [lines[start].rstrip()]
            for ln in lines[start + 1 :]:
                if chunk[-1].endswith("\\"):
                    chunk.append(ln.rstrip())
                else:
                    break
            commands.append("\n".join(chunk))
    return commands


def _strip_emphasis(text: str) -> str:
    """Remove markdown emphasis/backtick noise so wording assertions stay robust."""
    return text.replace("*", "").replace("`", "")


# --- (a) provenance flag semantics in manual + README -----------------------


def test_ts_provenance_json_only_in_inline_contexts() -> None:
    for path in (MANUAL, README):
        for lineno, line in enumerate(_read(path).splitlines(), start=1):
            if "--ts-provenance-json" in line:
                assert INLINE_JSON_MARKERS.search(line), (
                    f"{path.name}:{lineno} mentions --ts-provenance-json without "
                    "inline-JSON semantics (内联/调度器); file paths must use --ts-provenance"
                )


def test_irc_runnable_examples_use_file_provenance_and_input_role() -> None:
    for path in DOC_FILES:
        irc_commands = _commands(_read(path), "irc")
        assert irc_commands, f"{path.name} has no runnable IRC example"
        for command in irc_commands:
            assert PROVENANCE_FILE_FLAG.search(command), (
                f"{path.name} IRC example lacks the required --ts-provenance file flag: {command}"
            )
            assert "--ts-provenance-json" not in command, (
                f"{path.name} IRC example misuses --ts-provenance-json "
                "(inline form is scheduler-internal, never a file path): {command}"
            )
            assert re.search(r"--input-role[ =]transition_state", command), (
                f"{path.name} IRC example lacks --input-role transition_state: {command}"
            )


def test_tsmode_examples_have_no_provenance_flag() -> None:
    for path in DOC_FILES:
        for command in _commands(_read(path), "tsmode"):
            assert not PROVENANCE_ANY_FLAG.search(command), (
                f"{path.name} tsmode example must not carry a provenance flag "
                "(tsmode takes --source-bundle only): {command}"
            )


# --- (c) runnable scan/irc examples must not use the no-op --step -----------


@pytest.mark.parametrize("workflow", ["irc", "scan"])
def test_runnable_examples_have_no_step_flag(workflow: str) -> None:
    for path in DOC_FILES:
        for command in _commands(_read(path), workflow):
            assert not STEP_FLAG.search(command), (
                f"{path.name} runnable {workflow} example uses --step "
                "(ignored by ORCA; keep it only in compat warning notes): {command}"
            )


# --- (d) scan default = no ScanTS unless --scants ---------------------------


def test_scan_default_description_mentions_scants_opt_in() -> None:
    for path in (MANUAL, README):
        clean = _strip_emphasis(_read(path))
        assert re.search(r"(不启用|无)\s*ScanTS", clean), (
            f"{path.name} does not state that scan defaults to no ScanTS"
        )
        assert "--scants" in clean, f"{path.name} does not document the --scants opt-in"


# --- (e) live --help output matches documented semantics ---------------------

WF_HELP_CASES = {
    "scan": [
        "--coordinate",
        "--scan-points",
        "--scants",
        "ScanTS",
        "Off by default",
    ],
    "irc": [
        "--ts-provenance",
        "--ts-provenance-json",
        "--input-role",
        "--maxpoints",
        "compatibility",  # --step is documented as a no-op compat flag
    ],
    "nmr": [
        "--spectrum",
        "--bruker",
        "--nmr-method",
        "--nmr-basis",
    ],
    "Confsearch": [
        "--protocol",
        "--refinement-policy",
        "Pure-xTB",
        "no DFT refinement",
    ],
}


def _run_help(workflow: str) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + (
        ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    proc = subprocess.run(
        [sys.executable, "-m", "acp", "run", workflow, "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"acp run {workflow} --help failed: {proc.stderr}"
    return proc.stdout + proc.stderr


@pytest.mark.parametrize("workflow", sorted(WF_HELP_CASES))
def test_help_output_matches_docs(workflow: str) -> None:
    help_text = _run_help(workflow)
    for needle in WF_HELP_CASES[workflow]:
        assert needle in help_text, (
            f"`acp run {workflow} --help` output lacks documented wording: {needle!r}"
        )
    if workflow == "nmr":
        assert "--reference" not in help_text, (
            "nmr --help must not offer the retired --reference flag"
        )


def test_irc_help_declares_required_provenance_group() -> None:
    help_text = _run_help("irc")
    usage_lines: list[str] = []
    in_usage = False
    for line in help_text.splitlines():
        if line.startswith("usage:"):
            in_usage = True
        elif in_usage and not line.startswith(" "):
            break
        if in_usage:
            usage_lines.append(line)
    usage = re.sub(r"\s+", " ", " ".join(part.strip() for part in usage_lines))
    assert "--ts-provenance TS_PROVENANCE" in usage
    assert "--ts-provenance-json TS_PROVENANCE_JSON" in usage
    assert "|" in usage  # mutually-exclusive group in the usage line


def test_parser_rejects_irc_without_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    from acp.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["run", "irc", "--input", "ts.xyz", "--input-role", "transition_state"])
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "one of the arguments --ts-provenance --ts-provenance-json is required" in err


def test_parser_accepts_irc_file_provenance() -> None:
    from acp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "irc",
            "--input",
            "ts.xyz",
            "--input-role",
            "transition_state",
            "--ts-provenance",
            "prov.json",
        ]
    )
    assert args.ts_provenance == "prov.json"
    assert args.input_role == "transition_state"
