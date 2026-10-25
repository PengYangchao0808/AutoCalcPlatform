"""B1-1/B1-5 — per-file verified release facts for the science view.

:func:`attempt_release_facts` merges the frozen envelope and the receipt
blindly, so a mixed cache (attempt-1 envelope + attempt-2 receipt) leaks stale
``release_digest`` into the current attempt's projection.  The science view
instead uses :func:`verified_release_facts`, which discards **each file**
whose own ``attempt`` field does not attest the requested attempt:

* both files at the requested attempt → facts merged, envelope before receipt;
* stale envelope + current receipt → only the receipt contributes;
* every file stale / missing / attempt-less / malformed → ``{}``;
* ``read_root=None`` or ``attempt<1`` → ``{}`` without raising;
* first **non-empty** string per key wins (empty strings never overwrite).
"""

from __future__ import annotations

import json
from pathlib import Path

from acp_services.entrypoints.status_projection import verified_release_facts

ENVELOPE_REL = Path("WORK/00_RUNTIME/frozen/envelope.json")
RECEIPT_REL = Path("execution_receipt.json")


def _write(root: Path, relative: Path, payload: object) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_both_files_at_requested_attempt_merge(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ENVELOPE_REL,
        {
            "attempt": 2,
            "release_id": "rel-2",
            "release_digest": "sha256:env-2",
        },
    )
    _write(
        tmp_path,
        RECEIPT_REL,
        {"attempt": 2, "plan_digest": "plan-2", "submission_id": "sub-2"},
    )
    assert verified_release_facts(tmp_path, 2) == {
        "release_id": "rel-2",
        "release_digest": "sha256:env-2",
        "plan_digest": "plan-2",
        "submission_id": "sub-2",
    }


def test_stale_envelope_discarded_receipt_only(tmp_path: Path) -> None:
    _write(
        tmp_path,
        ENVELOPE_REL,
        {
            "attempt": 1,
            "release_id": "rel-1",
            "release_digest": "sha256:stale",
        },
    )
    _write(
        tmp_path,
        RECEIPT_REL,
        {"attempt": 2, "release_id": "rel-2", "release_digest": "sha256:fresh"},
    )
    facts = verified_release_facts(tmp_path, 2)
    assert facts == {"release_id": "rel-2", "release_digest": "sha256:fresh"}
    assert facts.get("release_digest") != "sha256:stale"


def test_all_files_stale_returns_empty(tmp_path: Path) -> None:
    _write(tmp_path, ENVELOPE_REL, {"attempt": 1, "release_id": "rel-1"})
    _write(tmp_path, RECEIPT_REL, {"attempt": 1, "plan_digest": "plan-1"})
    assert verified_release_facts(tmp_path, 2) == {}


def test_missing_files_return_empty(tmp_path: Path) -> None:
    assert verified_release_facts(tmp_path, 2) == {}
    assert verified_release_facts(tmp_path / "nope", 1) == {}


def test_none_read_root_and_attempt_below_one(tmp_path: Path) -> None:
    _write(tmp_path, ENVELOPE_REL, {"attempt": 2, "release_id": "rel-2"})
    assert verified_release_facts(None, 2) == {}
    assert verified_release_facts(tmp_path, 0) == {}
    assert verified_release_facts(tmp_path, -1) == {}


def test_envelope_without_attempt_key_discarded(tmp_path: Path) -> None:
    _write(tmp_path, ENVELOPE_REL, {"release_id": "rel-1", "release_digest": "old"})
    _write(tmp_path, RECEIPT_REL, {"attempt": 2, "plan_digest": "plan-2"})
    assert verified_release_facts(tmp_path, 2) == {"plan_digest": "plan-2"}


def test_first_non_empty_string_wins(tmp_path: Path) -> None:
    # Empty envelope value must not shadow the receipt's non-empty value.
    _write(tmp_path, ENVELOPE_REL, {"attempt": 2, "release_id": ""})
    _write(tmp_path, RECEIPT_REL, {"attempt": 2, "release_id": "from-receipt"})
    assert verified_release_facts(tmp_path, 2) == {"release_id": "from-receipt"}

    # Conversely the envelope's non-empty value wins over a later receipt one.
    _write(tmp_path, ENVELOPE_REL, {"attempt": 2, "release_id": "from-envelope"})
    _write(tmp_path, RECEIPT_REL, {"attempt": 2, "release_id": "from-receipt"})
    assert verified_release_facts(tmp_path, 2) == {"release_id": "from-envelope"}


def test_malformed_and_non_mapping_payloads_skipped(tmp_path: Path) -> None:
    envelope = tmp_path / ENVELOPE_REL
    envelope.parent.mkdir(parents=True, exist_ok=True)
    envelope.write_text("{not json", encoding="utf-8")
    _write(tmp_path, RECEIPT_REL, ["not", "a", "mapping"])
    assert verified_release_facts(tmp_path, 2) == {}


def test_non_int_coercible_file_attempt_discarded(tmp_path: Path) -> None:
    _write(tmp_path, ENVELOPE_REL, {"attempt": "two", "release_id": "rel-x"})
    _write(tmp_path, RECEIPT_REL, {"attempt": 2, "plan_digest": "plan-2"})
    assert verified_release_facts(tmp_path, 2) == {"plan_digest": "plan-2"}
