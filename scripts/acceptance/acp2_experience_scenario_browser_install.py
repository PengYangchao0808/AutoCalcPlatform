"""``browser-install`` — prove a real isolated headless Chromium can launch.

This body is the C05 environment proof, not a product-page acceptance: it
launches the Chromium installed under ``$PLAYWRIGHT_BROWSERS_PATH`` with the
Playwright that lives in ``$ACP2_BROWSER_PYTHON``, opens one page, sets a titled
blank document, asserts the title, captures a screenshot and records the
browser version.

The scenario module itself is import-safe in the C interpreter: Playwright is
only reached by spawning ``$ACP2_BROWSER_PYTHON`` as a subprocess.  A missing
Chromium is ``NOT_VERIFIED`` (exit 2); a launch that runs and fails is ``FAIL``
(exit 1) and can never be promoted to PASS.

Isolation: the browser profile, caches, HOME, TMPDIR and working directory all
live under ``$ACP2_DATA_ROOT`` (falling back to ``$ACP2_TOOLS``).  Nothing is
written into the production run root.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from acp2_experience_scenarios import (
    EXIT_FAIL,
    _add_artifact,
    _assertion,
    _not_verified,
    _out_dir,
    _run,
    _truncate,
    _write_json,
    _write_text,
    register,
)

SCENARIO_ID = "browser-install"
PAGE_TITLE = "ACP2 Chromium launch probe"
SCREENSHOT_NAME = "chromium-page.png"
LAUNCH_LOG_NAME = "chromium-launch.log"
LAUNCH_JSON_NAME = "browser-launch.json"

_EXECUTABLE_PROBE = """
import json
from importlib.metadata import version

from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    print(json.dumps({
        "executable_path": playwright.chromium.executable_path,
        "playwright_version": version("playwright"),
    }))
"""

# Executed by ``$ACP2_BROWSER_PYTHON``.  It prints one machine-readable line and
# exits non-zero unless the launch, title and screenshot checks all succeeded.
_LAUNCH_PROBE = r"""
import json
import os
import sys
import traceback
from pathlib import Path

result = {"ok": False, "pages": 0}
try:
    from playwright.sync_api import sync_playwright

    screenshot = Path(os.environ["ACP2_BROWSER_SCREENSHOT"])
    expected_title = os.environ["ACP2_BROWSER_EXPECTED_TITLE"]
    user_data = Path(os.environ["ACP2_BROWSER_USER_DATA"])

    print(f"interpreter={sys.executable}", flush=True)
    print(f"playwright={__import__('playwright').__file__}", flush=True)
    print(f"PLAYWRIGHT_BROWSERS_PATH={os.environ.get('PLAYWRIGHT_BROWSERS_PATH')}", flush=True)
    print(f"cwd={os.getcwd()}", flush=True)
    print(f"user_data_dir={user_data}", flush=True)
    print(f"CACHE={os.environ.get('XDG_CACHE_HOME')}", flush=True)

    with sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
        print(f"chromium_executable={executable}", flush=True)
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data),
            headless=True,
            viewport={"width": 1280, "height": 720},
        )
        try:
            browser_version = context.browser.version if context.browser else ""
            print(f"browser_version={browser_version}", flush=True)
            # A persistent context opens an about:blank tab.  Close it so the
            # probe owns exactly one freshly created page.
            for existing in list(context.pages):
                existing.close()
            page = context.new_page()
            page.set_content(
                "<!doctype html><html><head><title>"
                + expected_title
                + "</title></head><body><h1>ACP2 browser environment proof</h1></body></html>"
            )
            title = page.title()
            pages = len(context.pages)
            page.screenshot(path=str(screenshot), full_page=True)
            screenshot_bytes = screenshot.stat().st_size if screenshot.is_file() else 0
            result.update(
                {
                    "ok": True,
                    "title": title,
                    "title_ok": title == expected_title,
                    "browser_version": browser_version,
                    "pages": pages,
                    "screenshot_bytes": screenshot_bytes,
                    "executable_path": executable,
                    "interpreter": sys.executable,
                    "user_data_dir": str(user_data),
                    "cwd": os.getcwd(),
                }
            )
            print(f"title={title!r} pages={pages} screenshot_bytes={screenshot_bytes}", flush=True)
        finally:
            context.close()
except Exception as exc:  # noqa: BLE001 - every launch failure must be FAIL, not PASS
    result["error"] = f"{type(exc).__name__}: {exc}"
    traceback.print_exc()
print("ACP2_BROWSER_PROBE_JSON=" + json.dumps(result), flush=True)
ok = (
    result.get("ok") is True
    and result.get("title_ok") is True
    and result.get("pages") == 1
    and int(result.get("screenshot_bytes") or 0) > 0
    and bool(result.get("browser_version"))
)
sys.exit(0 if ok else 1)
"""


def _isolated_root() -> Path | None:
    """Root that owns every browser profile/cache/cwd for this scenario."""

    for variable in ("ACP2_DATA_ROOT", "ACP2_TOOLS"):
        value = os.environ.get(variable)
        if value:
            return Path(value).resolve()
    return None


def _safe_run_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-._" else "_" for char in value)
    return cleaned[:120] or "run"


def _resolve_executable(interpreter: str, env: dict[str, str], timeout: float) -> tuple[str, str]:
    """Ask the browser interpreter where Chromium lives (no browser launch)."""

    result = _run([interpreter, "-c", _EXECUTABLE_PROBE], timeout=min(timeout, 120.0), env=env)
    log = _truncate((result.stdout or "") + (result.stderr or ""), 4000)
    if result.returncode != 0:
        return "", log
    try:
        document = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return "", log
    executable = document.get("executable_path")
    return (executable if isinstance(executable, str) else ""), log


def _probe_payload(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith("ACP2_BROWSER_PROBE_JSON="):
            try:
                payload = json.loads(line.split("=", 1)[1])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def scenario_browser_install(context: Any) -> int:
    out = _out_dir(context)
    timeout = max(30.0, float(context.timeout))

    browsers_path_text = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not browsers_path_text:
        return _not_verified(context, "chromium_not_installed:PLAYWRIGHT_BROWSERS_PATH_unset")
    browsers_path = Path(browsers_path_text).resolve()
    if not browsers_path.is_dir():
        return _not_verified(
            context, f"chromium_not_installed:PLAYWRIGHT_BROWSERS_PATH_absent:{browsers_path}"
        )

    interpreter = os.environ.get("ACP2_BROWSER_PYTHON")
    if not interpreter:
        return _not_verified(context, "browser_python_absent:ACP2_BROWSER_PYTHON_unset")
    interpreter_path = Path(interpreter)
    if not interpreter_path.is_file():
        return _not_verified(context, f"browser_python_absent:{interpreter_path}")

    root = _isolated_root()
    if root is None:
        return _not_verified(context, "isolation_root_absent:ACP2_DATA_ROOT/ACP2_TOOLS")
    run_dir = root / "browser-runtime" / _safe_run_id(str(context.run_id))
    profile = run_dir / "user-data"
    cache = run_dir / "cache"
    home = run_dir / "home"
    tmp = run_dir / "tmp"
    cwd = run_dir / "cwd"
    for directory in (profile, cache, home, tmp, cwd):
        directory.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "PLAYWRIGHT_BROWSERS_PATH": str(browsers_path),
            "HOME": str(home),
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(run_dir / "config"),
            "XDG_DATA_HOME": str(run_dir / "data"),
            "TMPDIR": str(tmp),
            "ACP2_BROWSER_USER_DATA": str(profile),
            "ACP2_BROWSER_SCREENSHOT": str(out / SCREENSHOT_NAME),
            "ACP2_BROWSER_EXPECTED_TITLE": PAGE_TITLE,
        }
    )

    executable_text, executable_log = _resolve_executable(interpreter, env, timeout)
    if not executable_text:
        _write_text(
            out,
            "chromium-executable-probe.log",
            f"interpreter={interpreter}\nbrowsers_path={browsers_path}\n"
            f"executable=<unresolved>\n{executable_log}\n",
        )
        _add_artifact(context, "chromium-executable-probe.log")
        shutil.rmtree(run_dir, ignore_errors=True)
        return _not_verified(
            context,
            f"browser_python_playwright_unavailable:{executable_text or 'executable_unresolved'}",
        )
    executable = Path(executable_text)
    if not executable.is_file():
        _write_text(
            out,
            "chromium-executable-probe.log",
            f"interpreter={interpreter}\nbrowsers_path={browsers_path}\n"
            f"executable={executable_text}\n{executable_log}\n",
        )
        _add_artifact(context, "chromium-executable-probe.log")
        shutil.rmtree(run_dir, ignore_errors=True)
        return _not_verified(context, f"chromium_not_installed:{executable_text}")

    # An installed browser outside the frozen browsers root would silently
    # consume a shared cache; refuse it instead of reporting readiness.
    try:
        executable.resolve().relative_to(browsers_path)
        inside_browsers_root = True
    except ValueError:
        inside_browsers_root = False
    _assertion(
        context,
        "browser.executable_inside_browsers_path",
        inside_browsers_root,
        str(executable_text),
    )

    probe_path = run_dir / "chromium_probe.py"
    probe_path.write_text(_LAUNCH_PROBE, encoding="utf-8")
    launch = _run([interpreter, str(probe_path)], cwd=cwd, timeout=timeout, env=env)
    combined = f"$ {interpreter} {probe_path}\n" + (launch.stdout or "") + (launch.stderr or "")
    _write_text(out, LAUNCH_LOG_NAME, _truncate(combined, 16000))
    _add_artifact(context, LAUNCH_LOG_NAME)

    payload = _probe_payload(launch.stdout or "")
    screenshot = out / SCREENSHOT_NAME
    screenshot_bytes = screenshot.stat().st_size if screenshot.is_file() else 0

    _assertion(
        context,
        "browser.launch",
        launch.returncode == 0 and payload is not None and payload.get("ok") is True,
        f"rc={launch.returncode} payload={(payload or {}).get('error', 'present')}",
    )
    title = (payload or {}).get("title", "")
    _assertion(context, "browser.page_title", title == PAGE_TITLE, f"title={title!r}")
    pages = int((payload or {}).get("pages") or 0)
    _assertion(context, "browser.pages", pages == 1, f"pages={pages}")
    _assertion(
        context,
        "browser.browser_version",
        bool((payload or {}).get("browser_version")),
        str((payload or {}).get("browser_version", "")),
    )
    _assertion(
        context,
        "browser.screenshot_nonempty",
        screenshot_bytes > 0,
        f"{SCREENSHOT_NAME} bytes={screenshot_bytes}",
    )
    if screenshot.is_file():
        _add_artifact(context, SCREENSHOT_NAME)

    expected_paths = {
        "user_data_dir": profile,
        "cwd": cwd,
        "cache_home": cache,
        "home": home,
        "tmpdir": tmp,
    }
    inside_root = all(path.resolve().is_relative_to(root) for path in expected_paths.values())
    project_root = os.environ.get("ACP2_PROJECT_ROOT")
    outside_project = True
    if project_root:
        project = Path(project_root).resolve()
        outside_project = all(
            not path.resolve().is_relative_to(project) for path in expected_paths.values()
        )
    _assertion(
        context,
        "browser.isolation_under_tools_or_data_root",
        inside_root and outside_project,
        json.dumps({key: str(value) for key, value in expected_paths.items()}, sort_keys=True),
    )

    document = {
        "interpreter": interpreter,
        "playwright_executable_probe_log": executable_log,
        "browsers_path": str(browsers_path),
        "executable_path": str(executable),
        "executable_inside_browsers_path": inside_browsers_root,
        "user_data_dir": str(profile),
        "cwd": str(cwd),
        "cache_home": str(cache),
        "home": str(home),
        "tmpdir": str(tmp),
        "launch": payload or {},
        "launch_returncode": launch.returncode,
        "screenshot_bytes": screenshot_bytes,
        "pages": pages,
        "browser_version": (payload or {}).get("browser_version", ""),
    }
    _write_json(out, LAUNCH_JSON_NAME, document)
    _add_artifact(context, LAUNCH_JSON_NAME)

    context.extensions.update(
        {
            "experience.browser_interpreter": interpreter,
            "experience.browser_browsers_path": str(browsers_path),
            "experience.browser_executable": str(executable),
            "experience.browser_version": str((payload or {}).get("browser_version", "")),
            "experience.browser_pages": pages,
            "experience.browser_screenshot_bytes": screenshot_bytes,
            "experience.browser_user_data_dir": str(profile),
            "experience.browser_cwd": str(cwd),
            "experience.browser_cache_home": str(cache),
        }
    )

    # The profile is disposable environment state; the evidence keeps only the
    # screenshot, logs and JSON.  Remove it so repeated runs cannot inherit a
    # logged-in profile and no browser state survives the receipt.
    shutil.rmtree(run_dir, ignore_errors=True)

    failures = [item for item in context.assertions if item.get("status") == "FAIL"]
    if failures:
        context.reason = "browser_launch_failed"
        return EXIT_FAIL
    return 0


register(SCENARIO_ID, scenario_browser_install)
