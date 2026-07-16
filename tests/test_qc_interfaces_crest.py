"""Tests for the CREST legacy QC interface."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from conformer_search.qc.interfaces.crest import CRESTInterface
from tests.conftest import requires_crest

COORDINATES = np.array([[0.0, 0.0, 0.0]])
SYMBOLS = ["H"]

CREST_ENSEMBLE = """1
conf-1
H 0.0000000000 0.0000000000 0.0000000000
1
conf-2
H 0.0000000000 0.0000000000 0.5000000000
"""


def test_crest_interface_instantiates_with_minimal_config(
    sample_config: dict[str, object],
) -> None:
    interface = CRESTInterface(sample_config)

    assert interface.exe_path == Path("crest")
    assert interface.xtb_path == Path("xtb")
    assert interface.gfn_level == 2
    assert interface.threads == 1


@pytest.mark.slow
@pytest.mark.integration
@requires_crest
def test_crest_binary_smoke_check(sample_config: dict[str, object]) -> None:
    interface = CRESTInterface(sample_config)

    assert shutil.which(str(interface.exe_path)) is not None
