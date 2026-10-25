import json
import pytest


def pytest_runtest_setup(item):
    module = item.module
    original = module.load_viewer_stack
    def observed_stack(harness, *args, **kwargs):
        original(harness, *args, **kwargs)
        harness.page.evaluate("""() => {
            window.__creationErrors = [];
            const original = $3Dmol.createViewer;
            $3Dmol.createViewer = function(...args) {
                try { return original.apply(this, args); }
                catch(e) { __creationErrors.push({message:String(e), stack:e.stack}); throw e; }
            };
        }""")
    module.load_viewer_stack = observed_stack


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != 'call':
        return
    harness = item.funcargs.get('frontend_page')
    if harness is None:
        return
    try:
        extra = harness.page.evaluate("""() => {
            const canvas = document.createElement('canvas');
            return {creationErrors: window.__creationErrors || [],
                    webgl: !!canvas.getContext('webgl'),
                    scripts: Array.from(document.scripts, script => script.src)};
        }""")
    except Exception as exc:
        extra = {'probe_exception': str(exc)}
    with open('/tmp/acp-review-r2-20261021-pytest-browser-diagnostic.json', 'w') as stream:
        json.dump({'outcome':report.outcome, 'page_errors':harness.page_errors,
                   'console_errors':harness.console_errors, 'facts':extra}, stream, indent=2)
