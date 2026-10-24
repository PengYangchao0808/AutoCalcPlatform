"""Run the unchanged real-browser assertions with an explicit launch flag."""
from playwright.sync_api import BrowserType
import pytest

launch = BrowserType.launch

def launch_with_explicit_flag(self, *args, **kwargs):
    flags = list(kwargs.get('args') or [])
    if '--no-sandbox' not in flags:
        flags.append('--no-sandbox')
    kwargs['args'] = flags
    return launch(self, *args, **kwargs)

BrowserType.launch = launch_with_explicit_flag
raise SystemExit(pytest.main(['-p','no:cacheprovider','tests/test_acp_frontend_browser.py',
    '-q','--disable-warnings','--junitxml=/tmp/acp-review-20261021-browser-explicit.xml']))
