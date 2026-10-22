"""Reproducible Chromium graphics settings for real browser acceptance."""

from __future__ import annotations

import os
import sys
from typing import Any


def chromium_launch_options(*, no_sandbox: bool = False) -> dict[str, Any]:
    """Use the tested Vulkan software path on Linux, preserving real WebGL.

    Chromium 153's default ANGLE path can crash in its GPU shader compiler on
    this headless Linux host. Vulkan + the bundled SwiftShader driver renders
    the unchanged 3Dmol shaders, pixels, animation and exports successfully.
    This selects a real graphics backend; no canvas or viewer is substituted.
    """
    args = ["--use-angle=vulkan", "--use-vulkan=swiftshader"] if sys.platform == "linux" else []
    if no_sandbox:
        args.append("--no-sandbox")
    options: dict[str, Any] = {"headless": True, "args": args}
    if sys.platform == "linux":
        options["env"] = {**os.environ, "LIBGL_ALWAYS_SOFTWARE": "1"}
    return options
