"""Interactive ``acp init`` wizard subpackage.

Re-exports only (anti-pattern #5: no implementation here).  The wizard
guides first-time setup of the user cccp config (local executables +
remote cluster nodes); import this package freely on core-only installs
— remote-stack imports stay function-local inside flow modules.
"""

from __future__ import annotations

from acp.init_wizard.answers import load_answers, plan_from_answers
from acp.init_wizard.capabilities import (
    CapabilityReport,
    CheckState,
    collect_capability_report,
    render_capability_report,
)
from acp.init_wizard.flows import run_init
from acp.init_wizard.lab_doctor import (
    collect_lab_capability_report,
    lab_environment_from_plan,
    render_lab_capability_report,
)
from acp.init_wizard.newnode import FlowResult
from acp.init_wizard.options import (
    COMPONENT_SPECS,
    TIER_SPECS,
    UsageMode,
    tier_enablement,
)
from acp.init_wizard.persist import InitAbort
from acp.init_wizard.plan import InitPlan, apply_plan, build_plan, render_plan
from acp.init_wizard.prompts import (
    WizardAborted,
    ask,
    ask_local_path,
    ask_remote_dir,
    ask_secret,
    menu,
)

__all__ = [
    "COMPONENT_SPECS",
    "CapabilityReport",
    "CheckState",
    "FlowResult",
    "InitAbort",
    "InitPlan",
    "TIER_SPECS",
    "UsageMode",
    "WizardAborted",
    "apply_plan",
    "ask",
    "ask_local_path",
    "ask_remote_dir",
    "ask_secret",
    "build_plan",
    "collect_capability_report",
    "collect_lab_capability_report",
    "lab_environment_from_plan",
    "load_answers",
    "menu",
    "plan_from_answers",
    "render_capability_report",
    "render_lab_capability_report",
    "render_plan",
    "run_init",
    "tier_enablement",
]
