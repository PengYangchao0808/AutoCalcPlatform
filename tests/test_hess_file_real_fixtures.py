"""Real ORCA 6.1.1 ``.hess`` fixtures — integrity + D1 defect regressions (T02).

Expected values come from the audit's independent decode
(``docs/reports/ACP_Real_QC_Gap_Audit_20261006.json`` section ``D1``) — never
from the parser under test. Fixtures are byte-identical freezes of real ORCA
6.1.1 output (see ``tests/fixtures/qc/orca61/README.md``).

xfail discipline: only assertions that currently fail carry
``xfail(strict=True)`` — the parser rejects the real block format with
``HessFileError: Hessian is not symmetric`` (audit D1). When T04 lands the fix
these markers must be removed; strict XPASS turns any leftover marker red.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from cccp.qc.interfaces.hess_file import parse_orca_hess_file

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "qc" / "orca61"

# sha256 pins verified against the /tmp capture sources at freeze time
# (source == destination byte-identical; audit "evidence_hashes" / T02 contract).
EXPECTED_SHA256 = {
    "casscf_water.out": "d1a07e81ccf578853bfea5ccb46ec88fe300f9dcad6a1a3f0c1055907aa21d95",
    "scf_without_casscf.out": "5642a5d20bf7108c9fb50ac2cd8f039bf5fd554414fcb900a3ae96170f7d6031",
    "ts_freq.hess": "351947b14fdb75739565e40e9df04635f4483dffdab6393d41e1bf2889e259a0",
    "ts_opt.hess": "7dd266577567c38bb2419aa5293a10cfe0e77d99ab8acdf4eaf9510874501334",
    "ts_opt.out": "95d4392ac33e4c7ee436b0e8e7ca8b28a44009968763957ee69aa2a2f8a28775",
    "ts_opt.xyz": "5319d4ce17c6013afd1902cc9f9ea5e8b1c543a6a4ebd53e17ae973aab4839ba",
    "water_freq.hess": "3d4459d8c69abe661fb05f816d05a58a67b0f2f53e6a357ac05d8de44875c42b",
}

D1_HESS_PARSE_REASON = "D1 real-format hessian parse broken — see T04"

# Printed frequencies, audit D1 "printed_frequencies" (exact floats — the
# fixture decimal tokens round-trip to these doubles).
WATER_PRINTED_FREQUENCIES = [
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    1638.5873873880607,
    3865.2889845814966,
    3987.4664767421837,
]
TS_PRINTED_FREQUENCIES = [
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,
    -464.71990158819756,
    1950.5489968017332,
    2799.906692985535,
]

# Independent decode of the fixture ``$atoms`` sections (symbol mass x y z,
# coordinates in Bohr as written by ORCA; see fixture README).
WATER_SYMBOLS = ["O", "H", "H"]
WATER_MASSES_AMU = [15.999, 1.008, 1.008]
WATER_COORDS_BOHR = [
    [0.0, 0.0, 0.124028972808],
    [0.0, 1.430900628605, -0.984295404737],
    [0.0, -1.430900628605, -0.984295404737],
]
TS_SYMBOLS = ["C", "N", "H"]
TS_MASSES_AMU = [12.011, 14.007, 1.008]
TS_COORDS_BOHR = [
    [-1.217574895108, -0.073246909305, 0.0],
    [1.050096465597, -0.073246909305, 0.0],
    [-0.083739214756, 1.890613180850, 0.0],
]


@pytest.mark.parametrize("name,expected", sorted(EXPECTED_SHA256.items()))
def test_frozen_fixture_sha256(name: str, expected: str) -> None:
    """Frozen fixtures must stay byte-identical to the /tmp capture (stale-state gate)."""
    digest = hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest()
    assert digest == expected, f"{name} sha256 drifted: {digest} != {expected}"


def test_deduped_freq_hess_not_duplicated() -> None:
    """ts_opt_m0/freq.hess is the same bytes as ts_freq.hess — no second copy."""
    assert not (FIXTURE_DIR / "freq.hess").exists()
    assert (FIXTURE_DIR / "ts_opt.hess").is_file()


def test_ts_opt_bundle_description_loads() -> None:
    """The frozen bundle JSON must satisfy the tsmode CLI shape check."""
    from acp.workflows.tsmode import load_source_bundle_description

    payload = load_source_bundle_description(FIXTURE_DIR / "ts_opt_bundle.json")
    assert payload["schema_version"] == "tsmode_bundle_v1"
    assert payload["files"] == {
        "output": "ts_opt.out",
        "hessian": "ts_opt.hess",
        "geometry": "ts_opt.xyz",
    }


@pytest.mark.xfail(strict=True, reason=D1_HESS_PARSE_REASON)
def test_water_freq_hess_real_format() -> None:
    """water_freq.hess: dimension, printed frequencies, $atoms geometry/masses.

    Currently the flat token read misaligns the real block format and
    ``parse_orca_hess_file`` raises ``HessFileError: Hessian is not
    symmetric`` before any assertion runs (audit D1).
    """
    data = parse_orca_hess_file(FIXTURE_DIR / "water_freq.hess")
    assert data.dimension == 9
    assert data.symbols == WATER_SYMBOLS
    assert data.vibrational_frequencies == WATER_PRINTED_FREQUENCIES
    np.testing.assert_allclose(data.masses_amu, WATER_MASSES_AMU, atol=1e-9)
    np.testing.assert_allclose(data.coordinates_bohr, WATER_COORDS_BOHR, atol=1e-12)


@pytest.mark.xfail(strict=True, reason=D1_HESS_PARSE_REASON)
def test_ts_freq_hess_real_format() -> None:
    """ts_freq.hess: dimension, printed frequencies (real TS, mode 0 < 0), geometry.

    Same D1 defect as the water fixture: ``HessFileError: Hessian is not
    symmetric`` on the real block format.
    """
    data = parse_orca_hess_file(FIXTURE_DIR / "ts_freq.hess")
    assert data.dimension == 9
    assert data.symbols == TS_SYMBOLS
    assert data.vibrational_frequencies == TS_PRINTED_FREQUENCIES
    np.testing.assert_allclose(data.masses_amu, TS_MASSES_AMU, atol=1e-9)
    np.testing.assert_allclose(data.coordinates_bohr, TS_COORDS_BOHR, atol=1e-12)
