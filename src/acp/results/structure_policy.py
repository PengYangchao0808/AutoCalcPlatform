"""Shared single-geometry and reusable-product policy."""
from __future__ import annotations

import hashlib
import math
from typing import Any

POLICY_VERSION = 1
STRUCTURE_KINDS = frozenset({"structure", "xyz", "irc_endpoint"})
# Historical multi-frame products that predate the ``auto_reusable`` marker.
COLLECTION_PRODUCT_IDS = frozenset({"all_conformers", "confsearch_final_conformers"})
_ELEMENTS = frozenset("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())

def single_geometry(text: str) -> dict[str, Any] | None:
    """Validate one complete XYZ frame; never truncate a trajectory."""
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    try:
        count = int(lines[0].strip())
        if count <= 0 or len(lines) < count + 2 or any(line.strip() for line in lines[count + 2:]):
            return None
        symbols = []
        for line in lines[2:count + 2]:
            fields = line.split()
            if len(fields) != 4 or fields[0] not in _ELEMENTS:
                return None
            if not all(math.isfinite(float(v)) for v in fields[1:]):
                return None
            symbols.append(fields[0])
        return {"atom_count": count, "symbols": symbols,
                "content_checksum": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()}
    except (ValueError, IndexError, TypeError):
        return None

def collection_product(product: dict[str, Any]) -> str | None:
    """Return the skip reason when *product* is a multi-frame collection.

    The single policy covers historical no-metadata collections (id convention)
    and the new writer semantics (``metadata.auto_reusable is False``).  It is
    deliberately *not* derived from a ``single_geometry()`` failure: a corrupt
    declared single-frame product must still block a destructive recalc.
    Accepts both manifest products (``id`` + ``metadata``) and the flattened
    ``resolve_previous_outputs`` output projection.
    """
    metadata = product.get("metadata")
    auto_reusable = product.get("auto_reusable")
    if auto_reusable is None and isinstance(metadata, dict):
        auto_reusable = metadata.get("auto_reusable")
    if auto_reusable is False:
        return "marked collection (auto_reusable=false)"
    identity = str(
        product.get("id") or product.get("product_id") or product.get("entry_id") or ""
    ).strip().lower()
    if identity in COLLECTION_PRODUCT_IDS:
        return "historical conformer collection"
    return None

def reusable_product(product: dict[str, Any]) -> bool:
    """Apply explicit stage facts and exclude known collections."""
    if product.get("kind") not in STRUCTURE_KINDS:
        return False
    if collection_product(product) is not None:
        return False
    metadata = product.get("metadata") or {}
    if metadata.get("selection_source") == "manual_frame":
        return True
    if metadata.get("optimization_status") in {"failed", "unconverged", "unknown"}:
        return False
    identity = str(product.get("id") or "").lower()
    if identity.startswith(("ts_guess_", "int_guess_", "s2_candidate_")) and metadata.get("selection_source") not in {"manual", "manual_frame"}:
        return False
    if identity in {"irc_forward_path", "irc_reverse_path"}:
        return False
    return True

__all__ = [
    "COLLECTION_PRODUCT_IDS",
    "POLICY_VERSION",
    "STRUCTURE_KINDS",
    "collection_product",
    "single_geometry",
    "reusable_product",
]

# Display priorities do not relax any workflow submission gate.
WORKFLOW_SOURCE_ROLES = {
    "Confsearch": ("INT", "", "TS"), "PESsearch": ("INT", "", "TS"),
    "BatchOptimize": ("TS", "INT", ""), "XtbPathSearch": ("INT", "", "TS"),
    "OrcaGradient": ("INT", "", "TS"),
    "irc": ("TS", "", "INT"),
    "scan": ("INT", "", "TS"), "tsmode": ("TS", "", "INT"),
    "nmr": ("INT", "", "TS"), "singlepoint": ("INT", "TS", ""),
    "optimize": ("INT", "", "TS"), "frequency": ("INT", "TS", ""),
    "xtb-optimize": ("INT", "", "TS"),
    "xtb_optimize": ("INT", "", "TS"), "casscf": ("INT", "TS", ""),
}
