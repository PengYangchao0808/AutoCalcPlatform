"""Level-1 real-QC smoke for the NMR workchain (plan todos 49/51).

Gating (plan todo 49)
---------------------
Every case is gated twice and may only end in PASS, FAIL or ``NOT_VERIFIED``:

- ``@pytest.mark.slow`` + ``@pytest.mark.integration``: not executed unless
  ``--run-slow``/``--run-integration`` is passed.
- ``@requires_orca`` / ``@requires_crest`` / ``@requires_censo`` (add
  ``@requires_xtb`` when a case needs it): detection is ``shutil.which`` at
  ``tests/conftest`` import over ``CONFSEARCH_<NAME>_PATH`` or the bare
  binary name.

A skip is ``NOT_VERIFIED`` — never a green pass — and the literal token is
carried in every binary-gate skip reason (``pytest -rs`` shows it).

Real-run environment: this host configures binaries in ``~/.cccp.yaml``,
which is NOT on ``PATH``, so export the real paths first (ORCA/CREST/XTB/
CENSO correspond to ``executables.{orca,crest,xtb,censo}.path`` in that
file):

    export CONFSEARCH_ORCA_PATH=/home/<user>/orca611/orca
    export CONFSEARCH_CREST_PATH=/home/<user>/crest/crest
    export CONFSEARCH_XTB_PATH=/home/<user>/xtb-dist/bin/xtb
    export CONFSEARCH_CENSO_PATH=/home/<user>/anaconda3/bin/censo

    PYTHONPATH=src python3.11 -m pytest --run-slow --run-integration \\
        tests/test_acp_nmr_qc_smoke.py -q

Level-1 conclusions are connectivity/parse-only: no accuracy or calibration
claim may be derived from this file, and no mock may stand in for a real run.

Wave-7 handoff: todo 51 replaces the placeholder body below with the real
GIAO chain for 3-5 small molecules (rigid/flexible, H/C, multi-conformer),
writing evidence JSON/logs under ``.omo/evidence/``.
"""

from __future__ import annotations

import pytest

from tests.conftest import NOT_VERIFIED, requires_censo, requires_crest, requires_orca


@pytest.mark.slow
@pytest.mark.integration
@requires_orca
@requires_crest
@requires_censo
def test_nmr_level1_real_qc_smoke_placeholder() -> None:
    """Gating skeleton; todo 51 implements the real GIAO connectivity chain."""
    pytest.skip(
        f"{NOT_VERIFIED}: level-1 NMR real-QC smoke body is implemented in todo 51 "
        "(this skeleton only pins the gating + NOT_VERIFIED contract)"
    )
