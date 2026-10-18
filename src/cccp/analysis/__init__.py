"""CCCP pure-algorithm analysis helpers.

Modules here hold platform-agnostic scientific algorithms with **no** ACP
imports (``cccp`` must never import ``acp``).  The ACP workflow/planning layer
consumes them and re-exports the same objects for compatibility.
"""
