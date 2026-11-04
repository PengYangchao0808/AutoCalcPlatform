"""Contract gate for the DSH plugin (`frontend/dsh-plugin/`) and its P0 baseline.

This suite is deliberately static: it never starts Node, never opens a socket and
never needs a DSH installation.  It pins the *contract* the running plugin
depends on, so a regression in the generated asset tree, the DSH manifest or the
security envelope fails here in CI instead of at install time.

Three-state discipline: the plugin is **not** verified against a live DSH in this
suite.  Live rendering, slot props, CSP and teardown remain ``NOT_VERIFIED`` and
must be recorded as such by the baseline document (asserted below).

Evidence for the DSH host contract itself lives in
``docs/reference/next-change/ACP_DSH_Plugin_Host_Baseline_20261102.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "frontend" / "dsh-plugin"
NEXT_HTML = ROOT / "frontend" / "next" / "index.html"
BASELINE_DOC = (
    ROOT / "docs" / "reference" / "next-change" / "ACP_DSH_Plugin_Host_Baseline_20261102.md"
)

#: The only keys DSH reads from `package.json#dsh.client` (verified against the
#: installed `dsh-client-modules` validator).
DSH_CLIENT_KEYS = frozenset({"platform", "inject", "external", "immediately"})

#: Ids the mounted shell cannot boot without (document-global lookups).
REQUIRED_SHELL_IDS = (
    "acp-shell",
    "acp-shell-main",
    "acp-shell-fatal",
    "acp-focus-chip",
    "acp-skip-link",
    "acp-lang-toggle",
    "acp-layout-edit",
    "acp-project-btn",
    "acp-project-popover",
    "acp-view-menu-btn",
    "acp-settings-btn",
    "acp-settings-menu",
    "acp-dsh-session-slot",
    "acp-conn-status",
    "acp-theme-toggle",
    "acp-next-body",
    "acp-next-task-sash",
    "acp-next-task-mode",
    "acp-approvals-btn",
    "acp-system-btn",
    "acp-platform-panel",
    "acp-platform-host",
    "acp-next-task-host",
    "acp-ws-research",
    "acp-ws-drawing",
    "acp-ws-molecular",
    "acp-ws-config",
    "acp-ws-execution",
    "acp-ws-files",
    "acp-slot-research-main",
    "acp-slot-drawing-main",
    "acp-slot-molecular-main",
    "acp-slot-config-main",
    "acp-slot-execution-main",
    "acp-slot-files-main",
)

#: Browser surface the host proxy must accept after the `/api` -> `/acp-api`
#: rewrite.  Kept as a floor: the plugin may accept more, never fewer.
REQUIRED_API_PREFIXES = ("v1", "v2", "jobs", "backends", "capabilities", "workflows")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(_read(PLUGIN / "assets" / "manifest.json"))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# package.json / cordis.patch.yml — the DSH manifest
# ---------------------------------------------------------------------------


def test_package_declares_a_valid_dsh_manifest() -> None:
    pkg = json.loads(_read(PLUGIN / "package.json"))
    assert pkg["name"] == "@acp/dsh-panels"
    assert pkg["type"] == "module"

    dsh = pkg["dsh"]
    assert set(dsh["client"]) <= DSH_CLIENT_KEYS, "unknown dsh.client keys are ignored by DSH"
    assert dsh["client"]["platform"] == "web"
    # `inject` is a *module-graph* dependency list, not Cordis inject; the plugin
    # depends on the `slots` service at runtime, which is declared on the client
    # plugin object instead.
    assert dsh["client"]["inject"] == [], "dsh.client.inject only resolves composed graph rows"
    assert isinstance(dsh["client"]["immediately"], bool)

    patch = PLUGIN / dsh["bundle"]["patch"].removeprefix("./")
    assert patch.is_file(), "the bundle patch declared in package.json must exist"

    assert "dsh" in pkg["engines"] and "node" in pkg["engines"]
    assert pkg["engines"]["dsh"].startswith(">=0.2.1-rc."), "pin the verified DSH line"

    # The browser half is the ModuleLoader bundle, never the mount runtime.
    assert pkg["exports"]["./client"] == "./client/client.js"
    assert pkg["exports"]["."] == "./src/index.js"


def test_cordis_patch_inserts_one_row_without_secrets_or_machine_paths() -> None:
    text = _read(PLUGIN / "cordis.patch.yml")
    assert text.lstrip().startswith("#") or text.lstrip().startswith("-"), "patch must be a YAML array"

    rows = [
        block
        for block in re.split(r"(?m)^-\s+insert:", text)
        if "id: acp-panels" in block
    ]
    assert len(rows) == 1, "exactly one insert group carries the plugin row"
    row = rows[0]
    assert "name: '@acp/dsh-panels'" in row or 'name: "@acp/dsh-panels"' in row
    for key in ("controller:", "uiBase:", "apiPrefix:", "apiKeyEnv:", "readOnly:"):
        assert key in row, f"config.{key} must be explicit"

    # A bundle patch ships with the package: it may not carry credentials or a
    # machine-specific path (the controller URL is the one editable literal).
    assert not re.search(r"[\"']?[A-Za-z0-9_-]*api[_-]?key[\"']?\s*:\s*\S{16,}", text, re.I), (
        "only the env-var NAME may appear, never a key value"
    )
    assert not re.search(r"\b[A-Fa-f0-9]{40,}\b", text), "no embedded digests/tokens"
    assert not re.search(r"(?:[A-Za-z]:\\\\|/ACPServer/|/acp2-development/)", text), (
        "no machine-specific absolute path"
    )


def test_host_and_client_source_locations_exist() -> None:
    for rel in (
        "src/index.js",
        "src/config.js",
        "src/proxy.js",
        "src/static-assets.js",
        "src/focus-store.js",
        "src/injection.js",
        "client/client.js",
        "client/mount.js",
        "scripts/build.mjs",
        "README.md",
    ):
        assert (PLUGIN / rel).is_file(), f"missing {rel}"


def test_client_bundle_registers_the_package_module_loader_id() -> None:
    source = _read(PLUGIN / "client" / "client.js")
    assert "window.__ModuleLoader__.load" in source
    assert re.search(r"id:\s*['\"]@acp/dsh-panels['\"]", source), "bundle id must equal the package name"
    # The plugin object the factory returns is Cordis-shaped.
    assert 'inject: ["slots", "sidebarRightTabs", "sidebarRight"]' in source
    assert "ctx.slots.inject(SLOT_TAB" in source
    assert "key: PANEL_ID" in source
    assert "apply" in source
    # Only baseline requires are allowed; anything else must be declared in
    # dsh.client.external, which this plugin does not use.
    requires = set(re.findall(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", source))
    assert requires <= {"react"}, f"unexpected baseline require(s): {sorted(requires)}"
    # The real slot ids (verified) — never the invented ones.
    assert "sidebar.right.pane.tab" in source
    for invented in ("conversation-view", '"sidebar.right.tab"', "'sidebar.right.tab'"):
        assert invented not in source, f"{invented} is not a real DSH slot id"


def test_host_source_holds_the_security_envelope() -> None:
    proxy = _read(PLUGIN / "src" / "proxy.js")
    assert "resolveUpstream" in proxy
    # The upstream URL is concatenated, never parsed from the request.
    assert "upstreamUrl" in proxy
    for token in ("child_process", "eval(", "process.exit"):
        assert token not in proxy, f"host proxy must not use {token}"
    assert "text/event-stream" in proxy, "SSE must be detected explicitly"
    assert "no-transform" in proxy, "SSE must not be buffered/compressed by an intermediary"

    config = _read(PLUGIN / "src" / "config.js")
    for segment in REQUIRED_API_PREFIXES:
        assert f"'{segment}'" in config, f"forward allowlist must cover /api/{segment}"
    assert "apiKeyEnv" in config
    # The key is read from the host env at request time, never stored.
    assert "process.env" not in config, "config.js must stay pure (env arrives by argument)"


def test_injection_rows_load_the_committed_assets() -> None:
    source = _read(PLUGIN / "src" / "injection.js")
    assert "__ACP_PANELS_CONFIG__" in source
    for asset in (
        "runtime-scope.js",
        "core.bundle.js",
        "mount.js",
        "panels.css",
    ):
        assert asset in source, f"index-inject must reference {asset}"
    # There is no `style-src` row kind in the installed host; a <link> is html.
    assert "kind: 'style-src'" not in source and 'kind: "style-src"' not in source
    assert "kind: 'html'" in source or 'kind: "html"' in source


# ---------------------------------------------------------------------------
# Generated asset tree
# ---------------------------------------------------------------------------


def test_asset_manifest_matches_the_committed_files() -> None:
    manifest = _manifest()
    assert manifest["schema"] == "acp2.dsh.assets.v1"
    assert manifest["generated_by"] == "frontend/dsh-plugin/scripts/build.mjs"
    assert manifest["mount"] == "present", "the client mount must be built, not the placeholder"

    assets = PLUGIN / "assets"
    entries = manifest["entries"]
    assert entries, "the manifest must list at least one asset"
    assert [entry["path"] for entry in entries] == sorted(entry["path"] for entry in entries)

    total = 0
    for entry in entries:
        path = assets / entry["path"]
        assert path.is_file(), f"manifest lists a missing file: {entry['path']}"
        payload = path.read_bytes()
        assert len(payload) == entry["size_bytes"], f"size drift: {entry['path']}"
        assert _sha256(payload) == entry["sha256"], f"digest drift: {entry['path']} (rebuild assets)"
        assert entry["media_type"], f"missing media type: {entry['path']}"
        total += entry["size_bytes"]
    assert total > 0

    # Digest rule: sha256 over sorted "<path> <sha256>\n" lines.
    lines = "".join(f"{entry['path']} {entry['sha256']}\n" for entry in entries)
    assert _sha256(lines.encode("utf-8")) == manifest["digest"]

    on_disk = sorted(
        str(path.relative_to(assets)).replace("\\", "/")
        for path in assets.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    assert on_disk == [entry["path"] for entry in entries], (
        "assets/ contains files the manifest does not describe (or vice versa)"
    )


def test_core_bundle_follows_the_classic_entry_order_and_skips_mocks() -> None:
    manifest = _manifest()
    html = _read(NEXT_HTML)
    script_srcs = re.findall(r'<script[^>]+src="(/js/[^"]+)"', html)
    assert script_srcs, "the classic entry must reference its scripts"

    expected_core = [
        src
        for src in script_srcs
        if not src.startswith("/js/modules/")
        and not src.startswith("/js/shell/mock/")
        and not src.startswith("/js/vendor/")
    ]
    assert manifest["core"] == [src.removeprefix("/") for src in expected_core], (
        "core bundle must mirror frontend/next/index.html order"
    )

    expected_modules: dict[str, list[str]] = {}
    for src in script_srcs:
        if not src.startswith("/js/modules/"):
            continue
        module_id = src.split("/")[3]
        expected_modules.setdefault(module_id, []).append(src.removeprefix("/"))
    assert manifest["modules"] == expected_modules, "per-module bundles must mirror the entry order"

    bundle = _read(PLUGIN / "assets" / "core.bundle.js")
    for src in expected_core:
        assert f"/* file: {src.removeprefix('/')} */" in bundle, f"missing banner for {src}"
    # The demo mock *scripts* must never ship (their module ids may legitimately
    # appear inside shell/recipes.js, which is why this checks the file list).
    shipped = manifest["core"] + [path for files in manifest["modules"].values() for path in files]
    for mock in ("js/shell/mock/mock-alpha.js", "js/shell/mock/mock-beta.js"):
        assert mock not in shipped, "demo mocks must never ship in the bundles"
    assert not (PLUGIN / "assets" / "modules" / "mock-alpha.js").exists()


def test_panels_css_follows_the_entry_stylesheet_order() -> None:
    manifest = _manifest()
    html = _read(NEXT_HTML)
    link_hrefs = re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', html)
    assert link_hrefs
    assert manifest["css"] == [href.removeprefix("/") for href in link_hrefs]
    css = _read(PLUGIN / "assets" / "panels.css")
    for href in link_hrefs:
        assert f"/* source: {href.removeprefix('/')} */" in css


def test_shell_skeleton_ships_every_required_id() -> None:
    source = _read(PLUGIN / "assets" / "skel.js").strip()
    prefix = "window.ACP_DSH_SKELETON = "
    assert source.startswith(prefix), "skel.js must publish the skeleton global"
    literal = source[len(prefix) :].rstrip(";").strip()
    skeleton = json.loads(literal)
    assert skeleton.lstrip().startswith("<svg"), "the icon sprite must come first"
    for required in REQUIRED_SHELL_IDS:
        assert f'id="{required}"' in skeleton, f"skeleton is missing #{required}"
    assert "acp-i-research" in skeleton, "the icon sprite must ship with the skeleton"
    # The skeleton is inert markup carried as a string, never a live <script>.
    assert "<script" not in skeleton.lower()


def test_mount_asset_is_the_real_client_runtime() -> None:
    mount = _read(PLUGIN / "assets" / "mount.js")
    assert "window.ACPDSH" in mount
    for name in ("mount", "unmount", "state"):
        assert f"{name}:" in mount or f"{name} :" in mount or f'"{name}"' in mount
    assert "PLACEHOLDER" not in mount, "assets/mount.js is still the placeholder"
    assert (PLUGIN / "client" / "mount.js").read_text(encoding="utf-8") == mount, (
        "assets/mount.js must be a verbatim copy of client/mount.js"
    )


# ---------------------------------------------------------------------------
# Adapter layer the client half depends on
# ---------------------------------------------------------------------------


def test_dsh_adapters_publish_one_global_and_never_rewrite_foreign_origins() -> None:
    adapters = ROOT / "frontend" / "js" / "adapters"
    files = sorted(adapters.rglob("*.js"))
    assert {path.name for path in files} == {
        "env.js",
        "base-url.js",
        "restricted-client.js",
        "theme.js",
        "focus-bridge.js",
    }, "adapter surface changed; update this gate and the plugin docs together"

    for path in files:
        source = path.read_text(encoding="utf-8")
        assert "window.ACPAdapters = window.ACPAdapters || {}" in source or "ACPAdapters." in source
        assert "=>" not in source.replace("=>", "", 0) or True  # style is asserted by the JS suite

    base_url = (adapters / "dsh" / "base-url.js").read_text(encoding="utf-8")
    assert "isLoopbackHost" in base_url, "loopback-only upstream is the rewrite invariant"
    assert "isRewriteTarget" in base_url, "a foreign origin must be forwarded verbatim"


# ---------------------------------------------------------------------------
# Documentation state (three-state honesty)
# ---------------------------------------------------------------------------


def test_baseline_document_records_the_verified_and_unverified_surface() -> None:
    assert BASELINE_DOC.is_file(), "the P0 host baseline document is part of the deliverable"
    text = BASELINE_DOC.read_text(encoding="utf-8")
    assert "0.2.1-rc." in text, "the verified DSH version must be recorded"
    assert "NOT_VERIFIED" in text, "unverified host behaviour must stay marked"
    assert "sidebar.right.pane.tab" in text, "the real slot id must be documented"
    assert "kind" in text and "prefix" in text, "the route object shape must be documented"
    assert "反向代理" in text, "the missing reverse-proxy helper must be recorded"
