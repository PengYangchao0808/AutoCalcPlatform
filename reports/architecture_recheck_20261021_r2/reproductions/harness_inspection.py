"""Inspect the frontend checker with controlled HTTP responses, without a browser."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "review_four_combinations", root / "scripts/acceptance/four_combinations.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

broken = "this is deliberately invalid JavaScript {"


def response(base, path):
    if path == "/":
        return 200, '<html><script src="/broken.js"></script></html>'
    return 200, broken


with patch.object(module, "_get", response):
    result = module._probe_frontend_load("http://controlled-boundary.test")
print(json.dumps({
    "case": "R12_frontend_load_does_not_execute_js",
    "boundary": "controlled HTTP responses; no browser execution",
    "javascript": broken,
    "probe_result": result,
}, indent=2))
