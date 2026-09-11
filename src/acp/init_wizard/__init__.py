"""Interactive ``acp init`` wizard subpackage.

Re-exports only (anti-pattern #5: no implementation here).  The wizard
guides first-time setup of the user cccp config (local executables +
remote cluster nodes); import this package freely on core-only installs
— remote-stack imports stay function-local inside flow modules.
"""

from __future__ import annotations

from acp.init_wizard.prompts import (
    WizardAborted,
    ask,
    ask_local_path,
    ask_remote_dir,
    ask_secret,
    menu,
)

__all__ = [
    "WizardAborted",
    "ask",
    "ask_local_path",
    "ask_remote_dir",
    "ask_secret",
    "menu",
]
