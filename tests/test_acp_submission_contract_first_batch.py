"""First-batch submission contracts: PESsearch + scan (plan 2026-10-30 P1a slice).

The two new staged contracts extend the shared validator without touching the
frozen Confsearch/BatchOptimize semantics.  Positive examples come from the
published ``describe_workflow`` blocks (the projection cannot publish an
example the validator rejects); negatives pin the typed rules.
"""

from __future__ import annotations

import pytest

from acp.api.submission_contract import (
    WORKFLOW_CONTRACTS,
    SubmissionContractError,
    validate_workflow_submission,
)

_XYZ = "3\n\nO 0.0 0.0 0.0\nH 0.957 0.0 0.0\nH -0.24 0.927 0.0"


def _pes_input() -> dict[str, object]:
    return {
        "source": {"source_type": "xyz_text", "xyz_text": _XYZ, "charge": 0, "multiplicity": 1},
        "coordinate": {
            "kind": "distance",
            "atoms": [0, 1],
            "start": 0.957,
            "end": 2.4,
            "n_points": 12,
        },
        "coordinates": [
            {"kind": "distance", "atoms": [0, 1], "start": 0.957, "end": 2.4, "n_points": 12},
            {"kind": "distance", "atoms": [0, 2], "start": 0.957, "end": 2.4, "n_points": 12},
        ],
        "selection": {"mode": "functional"},
        "protocol": {
            "scan_driver": {"software": "orca", "mode": "relaxed_scan"},
            "scan_optimizer": {"method": "GFN2-xTB"},
            "single_point": {"enabled": True, "software": "orca", "method": "B97-3c"},
        },
    }


def _scan_input() -> dict[str, object]:
    return {"source_type": "xyz_text", "source": _XYZ, "charge": 0, "multiplicity": 1}


# --------------------------------------------------------------------------- #
# PESsearch
# --------------------------------------------------------------------------- #


def test_pessearch_wizard_metadata_fields_pass() -> None:
    """The Workbench wizard sends schema_id/profile_id with every submit."""
    validate_workflow_submission(
        "PESsearch",
        input=_pes_input(),
        method={
            "mode": "bond_length_scan",
            "schema_id": "pes_bond_scan",
            "profile_id": "default",
        },
        resources=None,
    )


def test_pessearch_bond_scan_positive_passes() -> None:
    validate_workflow_submission(
        "PESsearch", input=_pes_input(), method={"mode": "bond_length_scan"}, resources={"nproc": 4}
    )
    # the manifest-path method knobs stay legal without mode
    validate_workflow_submission(
        "PESsearch",
        input={
            "source_job_id": "20261030_001_Confsearch",
            "from_artifact": "RESULT/confsearch_manifest.json",
        },
        method={"strategy": "guided_scan", "select": "rank1"},
        resources=None,
    )


def test_pessearch_unknown_method_and_input_keys_are_refused() -> None:
    inp = _pes_input()
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "PESsearch", input=inp, method={"mode": "bond_length_scan", "foo": 1}, resources=None
        )
    assert excinfo.value.params() == {**excinfo.value.params(), "field": "method.foo"}
    assert excinfo.value.params()["rule"] == "unknown_field"
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "PESsearch",
            input={**inp, "bar": 1},
            method={"mode": "bond_length_scan"},
            resources=None,
        )
    assert excinfo.value.params()["field"] == "input.bar"


def test_pessearch_enums_are_closed() -> None:
    inp = _pes_input()
    for bad in ({"mode": "surface_scan"}, {"strategy": "voodoo"}, {"mode": ""}):
        if bad == {"mode": ""}:
            continue  # empty string is the documented "manifest path" default
        with pytest.raises(SubmissionContractError) as excinfo:
            validate_workflow_submission("PESsearch", input=inp, method=bad, resources=None)
        assert excinfo.value.params()["rule"] == "invalid_enum"


def test_pessearch_required_input_any_is_an_or_list() -> None:
    # Any single declared source satisfies the or-list; the bond-scan branch's
    # source+coordinate joint requirement is enforced by the pydantic model
    # downstream (create-now-refuse-later is not widened here).
    validate_workflow_submission(
        "PESsearch",
        input={"smiles": "CCO"},
        method={"strategy": "guided_scan"},
        resources=None,
    )
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "PESsearch", input={"selection": {"mode": "functional"}}, method={}, resources=None
        )
    assert excinfo.value.params()["rule"] == "missing_input"


# --------------------------------------------------------------------------- #
# scan
# --------------------------------------------------------------------------- #


def test_scan_positive_accepts_coordinates_in_method_and_input() -> None:
    validate_workflow_submission(
        "scan",
        input=_scan_input(),
        method={"coordinate": "0,1,0.957,2.4", "scan_points": 12},
        resources=None,
    )
    validate_workflow_submission(
        "scan",
        input={**_scan_input(), "scan_coordinates": [{"atoms": [0, 1], "start": 1.0, "end": 2.4}]},
        method={"levels": {"scan": {"engine": "orca"}}},
        resources=None,
    )


def test_scan_scants_is_opt_in_and_levels_are_schema_bound() -> None:
    validate_workflow_submission(
        "scan",
        input=_scan_input(),
        method={"coordinate": "0,1,0.957,2.4", "scan_use_scants": True},
        resources=None,
    )
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "scan",
            input=_scan_input(),
            method={"coordinate": "0,1,0.957,2.4", "levels": {"bogus": {}}},
            resources=None,
        )
    assert excinfo.value.params()["rule"] == "unknown_field"


def test_scan_unknown_keys_refused() -> None:
    with pytest.raises(SubmissionContractError):
        validate_workflow_submission(
            "scan",
            input=_scan_input(),
            method={"coordinate": "0,1,0.9,2.4", "max_cycles": 99},
            resources=None,
        )
    with pytest.raises(SubmissionContractError):
        validate_workflow_submission(
            "scan",
            input={**_scan_input(), "peak": 1},
            method={"coordinate": "0,1,0.9,2.4"},
            resources=None,
        )


# --------------------------------------------------------------------------- #
# Regression: the frozen Confsearch/BatchOptimize semantics are untouched
# --------------------------------------------------------------------------- #


def test_confsearch_and_batch_contracts_unchanged() -> None:
    assert set(WORKFLOW_CONTRACTS) == {
        "Confsearch",
        "BatchOptimize",
        "PESsearch",
        "scan",
        "XtbPathSearch",
        "OrcaGradient",
        "XtbConstrainedOpt",
        # batch B: the six previously uncontracted workflows
        "singlepoint",
        "optimize",
        "frequency",
        "xtb_optimize",
        "irc",
        "tsmode",
    }
    validate_workflow_submission(
        "Confsearch",
        input={"source_type": "smiles", "source": "CCO"},
        method={"protocol": "xtb-crest", "refinement_policy": "screen"},
        resources=None,
    )
    with pytest.raises(SubmissionContractError):
        validate_workflow_submission(
            "Confsearch",
            input={"source_type": "smiles", "source": "CCO"},
            method={"protocol": "xtb-crest", "max_conformers": 5},
            resources=None,
        )


def test_workflows_outside_staged_coverage_pass_through() -> None:
    # Workflows without a published contract (casscf/nmr + the PES2TS consumer
    # surfaces beyond the contracted three) still pass the validator unchanged:
    # this is the documented pass-through, not a published schema.
    validate_workflow_submission(
        "casscf", input={"input": "ts.xyz"}, method={"whatever": 1}, resources=None
    )


# --------------------------------------------------------------------------- #
# Frozen-payload PES2TS entries (local-only, upstream-owned request objects)
# --------------------------------------------------------------------------- #


def test_frozen_payload_entries_require_their_request_object() -> None:
    for workflow, key in (
        ("XtbPathSearch", "path_request"),
        ("OrcaGradient", "gradient_request"),
        ("XtbConstrainedOpt", "constrained_opt_request"),
    ):
        validate_workflow_submission(
            workflow,
            input={key: {"request_version": "see-the-upstream-contract"}},
            method={},
            resources={"nproc": 2, "mem": "4GB"},
        )
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "XtbPathSearch", input={"input": "molecule.xyz"}, method={}, resources=None
        )
    assert excinfo.value.params()["rule"] == "missing_input"


def test_frozen_payload_entries_reject_undeclared_top_level_keys() -> None:
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "OrcaGradient",
            input={"gradient_request": {}, "surprise": 1},
            method={},
            resources=None,
        )
    assert excinfo.value.params()["rule"] == "unknown_field"


# --------------------------------------------------------------------------- #
# Batch B: six simple/step workflows from the shared semantic authority
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("workflow", "input_payload", "method_payload"),
    [
        ("singlepoint", {"input": "CCO"}, {"levels": {"single_point": {"functional": "wB97X-D4"}}}),
        ("optimize", {"input": "CCO"}, {"levels": {"optimize": {"functional": "r2SCAN-3c"}}}),
        ("frequency", {"input": "CCO"}, {"levels": {"frequency": {"functional": "B97-3c"}}}),
        ("xtb_optimize", {"input": "CCO"}, {"levels": {"xtb_opt": {"gfn": 2}}}),
        (
            "irc",
            {
                "source_job_id": "20260101_001_BatchOptimize",
                "source_product_id": "ts_candidate",
                "ts_source": {"schema": "irc_ts_source_v1"},
                "directions": ["forward", "reverse"],
            },
            {"maxpoints": 40},
        ),
        ("tsmode", {"source_job_id": "20260101_002_frequency", "source_mode_index": 6}, None),
    ],
)
def test_batch_b_workflows_validate(
    workflow: str, input_payload: dict, method_payload: dict | None
) -> None:
    validate_workflow_submission(
        workflow, input=input_payload, method=method_payload, resources=None
    )


def test_batch_b_wrong_level_ids_and_undeclared_methods_are_typed() -> None:
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "singlepoint",
            input={"input": "CCO"},
            method={"levels": {"wrong_level": {}}},
            resources=None,
        )
    assert excinfo.value.params()["rule"] == "unknown_field"

    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "tsmode",
            input={"source_job_id": "j"},
            method={"levels": {}},
            resources=None,
        )
    assert excinfo.value.params()["rule"] == "unknown_field"


def test_batch_b_required_sources_are_enforced() -> None:
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission("irc", input={"charge": 0}, method=None, resources=None)
    assert excinfo.value.params()["rule"] == "missing_input"

    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission("tsmode", input={"input": "CCO"}, method=None, resources=None)
    assert excinfo.value.params()["rule"] == "missing_input"


def test_schema_aliases_are_the_same_catalog_object() -> None:
    """A reviewed alias must be the same schema object, never a diverging table."""
    from acp.api.submission_contract import _SCHEMA_ALIASES
    from acp.catalog import METHOD_SCHEMAS, get_workflow_by_id

    for workflow, aliases in _SCHEMA_ALIASES.items():
        default_schema = str(
            (get_workflow_by_id(workflow) or {}).get("method_schema_id") or ""
        )
        assert default_schema in METHOD_SCHEMAS, workflow
        for alias in aliases:
            assert alias in METHOD_SCHEMAS, (workflow, alias)
            assert METHOD_SCHEMAS[alias] is METHOD_SCHEMAS[default_schema], (
                workflow,
                alias,
            )
