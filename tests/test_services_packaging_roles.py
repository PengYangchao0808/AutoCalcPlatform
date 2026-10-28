"""Services packaging role matrix (plan-2 todo 40 — B40a/B40b, 评审 P0-3).

Locks the role-dependency matrix of the release configuration:

1. **Role matrix** — core deps = scientific minimal install; ``api``/``remote``
   (Web/IAM/billing/transport) and ``nmr`` (science-optional) stay in extras;
   candidate components (PostgreSQL/object storage/Slurm/Podman/Lago… per plan
   line 71) never join default deps nor ``requirements-node.txt``.
2. **Import purity** — the science minimal entry points import in a clean
   interpreter with ``fastapi``/``uvicorn``/``paramiko``/``nmrglue``/… blocked
   at the meta path (extras are lazily unreachable for the science CLI path).
3. **Discovery/ownership** — ``include`` keeps ``acp_services*``; the recorded
   wheel namelist manifest (``tests/baseline/services-evidence/
   wheel_namelist_manifest.json``) lists the expected top-level packages with
   zero duplicate paths, and the C39b future-split ownership rules cover every
   current package exactly once (no file installed by two wheels).
4. **Negative control** — dropping ``acp_services*`` from a *copied* pyproject
   makes the inclusion assertion fail (the check has teeth).

``include`` matching does NOT prove role isolation — the independent-release
claim is verified separately by install-artifact checks (plan todos 72/77).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # py3.10 CI — tomllib is 3.11+
    tomllib = pytest.importorskip("tomli")  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "tests" / "baseline" / "services-evidence"
MANIFEST = RECORDS / "wheel_namelist_manifest.json"

#: role → frozen package-name set (extras `[project.optional-dependencies]`)
ROLE_MATRIX: dict[str, set[str]] = {
    "api": {"fastapi", "uvicorn", "python-multipart", "zipstream-ng"},
    "remote": {"paramiko"},
    "nmr": {"nmrglue", "openbabel", "qml"},
    "dev": {"pytest", "pytest-cov", "ruff", "mypy", "pre-commit", "httpx"},
    "browser": {"playwright"},
}
#: scientific minimal install — must hold in core deps
_CORE_REQUIRED = {"numpy", "rdkit", "pyyaml", "scipy", "matplotlib", "openpyxl"}
#: Web/IAM/billing/infra CANDIDATE components — never default deps (plan 71 / todo 40)
_CANDIDATE_COMPONENT_DEPS = {
    # Web (api extra only, never core / never on nodes)
    "fastapi",
    "uvicorn",
    "python-multipart",
    "zipstream-ng",
    # remote transport (remote extra only)
    "paramiko",
    # IAM candidates
    "pyjwt",
    "python-jose",
    "authlib",
    "passlib",
    "bcrypt",
    # billing candidates (Lago / payment adapters)
    "lago",
    "stripe",
    # infra candidates (PostgreSQL / object storage / batch schedulers)
    "psycopg",
    "psycopg2",
    "psycopg2-binary",
    "asyncpg",
    "boto3",
    "minio",
    "pyslurm",
}
#: extras packages that the science minimal import path must not pull in
_BLOCKED_IMPORT_ROOTS = (
    "fastapi",
    "uvicorn",
    "multipart",
    "zipstream",  # Web/api
    "paramiko",  # remote/LSF
    "nmrglue",
    "qml",
    "openbabel",  # nmr extra (optional science)
)
#: modules probed with the importer blocker (science CLI / core layers)
_PROBE_MODULES = (
    "cccp.calculation",
    "cccp.backends",
    "cccp.qc",
    "acp.calculations.primitives",
    "acp.cli",
    "acp.nmr",
    "acp.workflows.simple",
    "acp.catalog",
)


def _load_pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _names(specs: list[str]) -> set[str]:
    out: set[str] = set()
    for spec in specs:
        match = re.match(r"([A-Za-z0-9._-]+)", spec.strip())
        assert match, f"unparseable requirement: {spec!r}"
        out.add(match.group(1).lower().replace("_", "-"))
    return out


def _core_names() -> set[str]:
    return _names(_load_pyproject()["project"]["dependencies"])


def _extra_names(extra: str) -> set[str]:
    return _names(_load_pyproject()["project"]["optional-dependencies"][extra])


def _node_names() -> set[str]:
    out: set[str] = set()
    for line in (ROOT / "requirements-node.txt").read_text(encoding="utf-8").splitlines():
        payload = line.split("#", 1)[0].strip()
        if payload:
            out |= _names([payload])
    return out


def _assert_services_discovery(include: list[str]) -> None:
    """The Services package must be discoverable by the build (single-wheel M1)."""
    assert "acp_services*" in include, (
        "tool.setuptools.packages.find.include must contain 'acp_services*' — "
        "dropping it silently strips the Services wheel content"
    )


def _load_manifest() -> dict:
    assert MANIFEST.is_file(), (
        f"recorded wheel namelist manifest missing: {MANIFEST} — re-run the B40a "
        "out-of-tree build and regenerate it (packaging_rollout_receipts.md)"
    )
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Role-dependency matrix (which extras belong to which role)
# ---------------------------------------------------------------------------


def test_role_matrix_extras_are_exactly_the_declared_roles() -> None:
    """Extras keys and their package sets match the frozen role matrix."""
    extras = _load_pyproject()["project"]["optional-dependencies"]
    assert set(extras) == set(ROLE_MATRIX), (
        f"extras set drifted: {sorted(extras)} != {sorted(ROLE_MATRIX)} — "
        "update ROLE_MATRIX consciously (评审 P0-3 role matrix), never silently"
    )
    for role, expected in ROLE_MATRIX.items():
        got = _names(extras[role])
        assert got == expected, f"role '{role}' deps drifted: {sorted(got)} != {sorted(expected)}"


def test_scientific_minimal_core_is_science_only() -> None:
    """Core deps = scientific minimal install: required science present, no extras."""
    core = _core_names()
    assert _CORE_REQUIRED <= core, f"science core dep missing: {_CORE_REQUIRED - core}"
    forbidden = ROLE_MATRIX["api"] | ROLE_MATRIX["remote"] | ROLE_MATRIX["nmr"]
    overlap = core & forbidden
    assert not overlap, (
        f"Web/IAM/remote/nmr-extra packages leaked into core deps: {sorted(overlap)} — "
        "the scientific minimal install must not require api/remote/nmr extras"
    )
    assert not (core & _CANDIDATE_COMPONENT_DEPS), (
        f"candidate components in default deps: {sorted(core & _CANDIDATE_COMPONENT_DEPS)}"
    )


def test_candidate_components_absent_from_node_requirements() -> None:
    """Web/IAM/billing candidates never ship to compute nodes either."""
    node = _node_names()
    assert not (node & _CANDIDATE_COMPONENT_DEPS), (
        f"candidate components in requirements-node.txt: {sorted(node & _CANDIDATE_COMPONENT_DEPS)}"
    )
    # node runtime = core science subset (+ nmrglue by design), no api/remote/web
    web_remote = ROLE_MATRIX["api"] | ROLE_MATRIX["remote"]
    assert not (node & web_remote), f"web/remote deps on nodes: {sorted(node & web_remote)}"


def test_console_scripts_stay_acp_only() -> None:
    """Single CLI entry; never add a `cccp` console script or `python -m cccp`."""
    scripts = _load_pyproject()["project"]["scripts"]
    assert scripts == {"acp": "acp.cli:main"}, (
        f"[project.scripts] drifted: {scripts} — only 'acp = acp.cli:main' is sanctioned"
    )


# ---------------------------------------------------------------------------
# 2. Science minimal import probe (extras blocked at the meta path)
# ---------------------------------------------------------------------------


def test_science_minimal_imports_without_extras() -> None:
    """Science CLI/core layers import with Web/remote/nmr-extra modules blocked.

    Runs in a subprocess so pre-imported modules cached in this pytest process
    cannot mask an eager dependency.
    """
    blocked = ", ".join(repr(r) for r in _BLOCKED_IMPORT_ROOTS)
    modules = ", ".join(repr(m) for m in _PROBE_MODULES)
    script = textwrap.dedent(
        f"""
        import importlib, sys
        BLOCKED = {{{blocked}}}

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise ImportError(f"science-minimal import pulled extra: {{name}}")
                return None

        sys.meta_path.insert(0, Blocker())
        failures = []
        for mod in ({modules},):
            try:
                importlib.import_module(mod)
            except Exception as exc:  # noqa: BLE001 — probe collects every failure
                failures.append(f"{{mod}}: {{type(exc).__name__}}: {{exc}}")
        if failures:
            print("FAILURES:\\n" + "\\n".join(failures))
            sys.exit(1)
        print("science-minimal imports OK:", {len(_PROBE_MODULES)})
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        "science minimal install must import without api/remote/nmr extras:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


# ---------------------------------------------------------------------------
# 3. Discovery + recorded wheel namelist (B40a evidence, no rebuild in pytest)
# ---------------------------------------------------------------------------


def test_include_discovers_services_and_science_packages() -> None:
    """`[tool.setuptools.packages.find].include` keeps Services + science discovery."""
    conf = _load_pyproject()["tool"]["setuptools"]["packages"]["find"]
    _assert_services_discovery(conf["include"])
    for pattern in ("acp*", "cccp*"):
        assert pattern in conf["include"], (
            f"include drifted, missing {pattern!r}: {conf['include']}"
        )


def test_recorded_wheel_namelist_has_expected_packages_no_duplicates() -> None:
    manifest = _load_manifest()
    assert set(manifest["top_level_packages"]) == {"acp", "acp_services", "cccp"}, (
        f"wheel top-level packages drifted: {manifest['top_level_packages']}"
    )
    assert manifest["duplicate_record_paths"] == [], (
        f"duplicate file ownership inside the wheel: {manifest['duplicate_record_paths']}"
    )
    assert manifest["stale_acp_nmr_models_entries"] == [], (
        "stale acp/nmr/models entries — T55 relocated the assets to cccp/"
    )
    assert manifest["nmr_model_assets"]["files"] == 8, "8 DP5/FCHL assets must ship"
    assert manifest["acp_services_python_files"] > 0, "Services package stripped from wheel"
    assert manifest["console_scripts"] == {"acp": "acp.cli:main"}
    # manifest stays consistent with the live build config
    proj = _load_pyproject()["project"]
    assert manifest["build"]["wheel"].startswith(f"{proj['name']}-{proj['version']}-"), (
        "recorded wheel does not match live pyproject name/version — regenerate the manifest"
    )


def test_future_split_ownership_covers_each_package_exactly_once() -> None:
    """C39b ruling: every current package maps to exactly ONE future owner.

    This is the no-duplicate-ownership gate between the current single wheel
    and the future split (B40b target, not yet applied — gate stays open).
    """
    manifest = _load_manifest()
    split = manifest["future_split_ownership"]
    assert split["applied"] is False, (
        "future_split_ownership.applied flipped to True — B40b gate must be closed by "
        "the full-C08 integration with rebuild evidence, not by editing this manifest"
    )
    rules = [(r["prefix"], r["owner"]) for r in split["rules"]]
    # prefix disjointness (no rule prefix is a path-prefix of another)
    for i, (p1, _) in enumerate(rules):
        for p2, _ in rules[i + 1 :]:
            assert not p1.startswith(p2) and not p2.startswith(p1), (
                f"overlapping ownership rules: {p1!r} vs {p2!r}"
            )
    # exactly one owner per current wheel package
    for pkg in manifest["top_level_packages"]:
        hits = [owner for prefix, owner in rules if f"{pkg}/".startswith(prefix)]
        assert len(hits) == 1, (
            f"package {pkg!r} owned by {hits} (want exactly 1) — multi-wheel "
            "duplicate install would result"
        )


def test_negative_dropping_acp_services_fails_the_inclusion_assertion() -> None:
    """Negative control: the inclusion assertion must fail without acp_services*."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    mutated = re.sub(
        r"include\s*=\s*\[([^\]]*)\]",
        lambda m: (
            "include = ["
            + ", ".join(p for p in re.findall(r'"[^"]+"', m.group(1)) if p != '"acp_services*"')
            + "]"
        ),
        text,
        count=1,
    )
    assert '"acp_services*"' not in mutated.split("[project.scripts]")[0], (
        "test setup failed: could not strip acp_services* from the copied pyproject"
    )
    conf = tomllib.loads(mutated)["tool"]["setuptools"]["packages"]["find"]
    with pytest.raises(AssertionError, match="acp_services"):
        _assert_services_discovery(conf["include"])
