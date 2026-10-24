# Browser environment (C05 / T-08)

Frozen: 2026-10-22. Owner: C track. Scope: the **environment proof** only — a
real, isolated headless Chromium launches and renders one titled page. This is
not product-page acceptance; product pages are exercised by later browser
scenarios.

## Transaction

| Item | Value |
| --- | --- |
| Browser interpreter | `$ACP2_BROWSER_PYTHON` = `<acp2-dev>/venvs/browser/bin/python` |
| Playwright | `1.63.0` (`importlib.metadata.version("playwright")`) |
| `PLAYWRIGHT_BROWSERS_PATH` | `$ACP2_TOOLS/playwright-browsers` = `<server-root>/acp2-tools/playwright-browsers` |
| Installed build | `chromium-1243` (`Chrome Headless Shell 153.0.8010.12`) |
| Chromium executable | `<server-root>/acp2-tools/playwright-browsers/chromium-1243/chrome-linux64/chrome` |
| `browser.version` | `153.0.8010.12` |
| Disk after install | `658 MiB` (`chromium-1243` 393 MiB, `chromium_headless_shell-1243` 261 MiB, `ffmpeg-1011` 4.9 MiB) |
| Install command | `PLAYWRIGHT_BROWSERS_PATH="$ACP2_TOOLS/playwright-browsers" "$ACP2_BROWSER_PYTHON" -m playwright install chromium` |
| Install exit | `0` (no `--with-deps`, no sudo, no OS package install) |

Playwright is installed only in the browser role venv. The C development venv
and the C harness interpreter are never used for Playwright.

Install log: `<server-root>/acp2-tools/browser-install-logs/chromium-install-20261022T142128Z.log`
(SHA-256 `1e9a39c0205b65fe601b3d525091177f5a1eb0f6f7f1394298efee256330bfbc`).
The host is not an officially supported Playwright distro, so Playwright
downloaded the `ubuntu24.04-x64` fallback build; the browser launched anyway.
No missing system libraries were reported, so `requests/B-req-03.md` is not
needed.

## Isolation proof

Every mutable browser path is created under `$ACP2_DATA_ROOT`, never in the
production ACP run root, the project checkout, or a user home:

```
$ACP2_DATA_ROOT/browser-runtime/<run_id>/
  user-data/   # Chromium profile (launch_persistent_context)
  cache/       # XDG_CACHE_HOME
  home/        # HOME
  tmp/         # TMPDIR
  cwd/         # subprocess working directory
  config/      # XDG_CONFIG_HOME
  data/        # XDG_DATA_HOME
```

The positive result records the exact paths in
`extensions["experience.browser_user_data_dir"]`, `.browser_cwd` and
`.browser_cache_home`, and the scenario asserts that every path is inside
`$ACP2_DATA_ROOT`/`$ACP2_TOOLS` **and outside** `$ACP2_PROJECT_ROOT`. The run
directory is deleted after the receipt is written, so no profile, cookie or
cache survives a run. The only files left behind are the three evidence
artifacts inside `$ACP2_EVIDENCE/C/browser-install/<run_id>/<phase>/`.

## Scenario contract — `browser-install`

Registered by `scripts/acceptance/acp2_experience_scenario_browser_install.py`.
The module is import-safe in the C interpreter: it imports Playwright only by
spawning `$ACP2_BROWSER_PYTHON`; the probe source is written to the isolated run
directory and executed there.

Flow:

1. Fail closed when `PLAYWRIGHT_BROWSERS_PATH` is unset/absent, when
   `$ACP2_BROWSER_PYTHON` is absent, or when the resolved Chromium executable
   does not exist → `NOT_VERIFIED` (exit 2) with `chromium_not_installed`.
2. Ask the browser interpreter for `playwright.chromium.executable_path` and
   require it to live inside `$PLAYWRIGHT_BROWSERS_PATH`.
3. Launch **one** Chromium instance with `launch_persistent_context` under the
   isolated `user-data` directory, close the context in a `finally` block,
   close the initial about:blank tab, create exactly one new page,
   `set_content` a document titled `ACP2 Chromium launch probe`, assert the
   title, take `chromium-page.png`, and require a non-empty screenshot.
4. Any launch/runtime error is `FAIL` (exit 1), never `PASS`. A zero-assertion
   PASS is impossible.

Recorded extension fields (namespaced, B01 schema):

| Key | Meaning |
| --- | --- |
| `experience.browser_interpreter` | `$ACP2_BROWSER_PYTHON` (the interpreter that ran Playwright) |
| `experience.browser_browsers_path` | frozen `PLAYWRIGHT_BROWSERS_PATH` |
| `experience.browser_executable` | resolved Chromium binary |
| `experience.browser_version` | `browser.version` |
| `experience.browser_pages` | must be `1` |
| `experience.browser_screenshot_bytes` | must be `> 0` |
| `experience.browser_user_data_dir`, `.browser_cwd`, `.browser_cache_home` | isolation proof |

`experience.interpreter` remains the harness process interpreter; the browser
role interpreter is the separate `experience.browser_interpreter` field.

Artifacts: `chromium-launch.log`, `chromium-page.png`, `browser-launch.json`.
The executable-probe log is additionally kept when resolution fails.

## Scenario module discovery contract (T-08 and later)

`scripts/acceptance/acp2_experience_scenarios.py` registers the five P0 bodies
and then imports, in sorted filename order, every sibling
`acp2_experience_scenario_*.py` in the same directory through `importlib`.
A sibling:

* imports shared helpers with `from acp2_experience_scenarios import ...` and
  calls `register("<scenario>", handler)` at import time;
* must be import-safe without optional third-party packages (lazy imports
  inside the handler);
* must register at least one scenario;
* must not reuse a scenario ID — duplicate registration raises `ValueError`.

Later todos therefore add exactly one new module and never edit the registry.

## Verified runs

| Run | Result | Evidence |
| --- | --- | --- |
| `browser-install-20261022T142824Z-208277` (final, committed code) | `PASS` (exit 0), `browser_version=153.0.8010.12`, `pages=1`, `screenshot_bytes=11202`, `--verify-receipt` exit 0 | `$ACP2_EVIDENCE/C/browser-install/browser-install-20261022T142824Z-208277/main/` |
| `browser-install-20261022T142553Z-200868` (first PASS) | `PASS` (exit 0), same fields; install/negative-QA evidence attached under `install/` and `qa/` | `$ACP2_EVIDENCE/C/browser-install/browser-install-20261022T142553Z-200868/` |
| `browser-install-20261022T142617Z-202805` (negative: empty temp `PLAYWRIGHT_BROWSERS_PATH`) | `NOT_VERIFIED` (exit 2), reason `chromium_not_installed:...` | `$ACP2_EVIDENCE/C/browser-install/browser-install-20261022T142617Z-202805/main/` |

The earlier attempt `browser-install-20261022T142534Z-199307` is retained as a
real `FAIL` example: the persistent context opened an extra about:blank tab,
`pages=2` failed the assertion, and the receipt was `FAIL` (exit 1). It was not
promoted to PASS.
