"""Schema-driven capability forms — the frozen ``acp2.ui-subset.v1`` profile (M3-06).

Discovery already answers *what a capability is*
(:mod:`acp.api.science_catalog` forwards the frozen capability descriptors).  It
deliberately does **not** answer *what a UI may ask the user*.  A
``CapabilityDescriptor.input_schema`` is a :class:`SchemaReference` (a contract
pointer such as ``cccp.calculation.requests#TaskRequest``), never a JSON Schema,
and the closed capability schema has no UI fields.  M3-06 therefore adds a
separate, versioned form contract next to it:

* ``acp2.ui-subset.v1`` — an **explicit keyword whitelist** for the executable
  form subset.  Every keyword the browser may evaluate is listed with its value
  domain; ``$ref``, the combinators (``allOf``/``anyOf``/``oneOf``/``not``),
  conditionals and the remaining 2020-12 vocabulary are **rejected** as a typed
  ``unavailable`` state, never ignored and never submitted.
* ``acp2.ui.form.v1`` — one form document per capability: a ``request`` template
  (the submit surface + base body) and a ``form_schema`` (the whitelisted subset)
  whose leaf fields carry an ``x-acp`` binding into the frozen binding table.
* the **binding table** — one row per form field: the ``TaskRequest`` path the
  value becomes, the equivalent submit/preflight body location, the value type
  and the unit.  A field without a binding is a contract error, so a form can
  never grow a dead input.
* **digests** — ``subset_digest`` (over the whitelist/binding/unit tables) and
  ``form_digest`` (over one form document).  Both travel with the served
  document and with the proposal provenance the Workbench writes.

Authorities and directions:

* built-in capability forms are platform-owned data in this module (the UI
  vocabulary is a platform decision, not a capability's);
* a **third-party** capability may ship its own ``cccp_ui_form.json`` inside the
  installed distribution's ``.dist-info``.  It is read metadata-only — exactly
  like ``cccp_science_manifest.json`` — never by importing plugin code, and it
  is validated against the same whitelist before it can reach a browser;
* a capability that is no longer active (plugin disabled/uninstalled, or a
  version upgrade that changed the descriptor digest) is projected
  ``read_only`` with the frozen descriptor digest from the M2-05 enablement
  ledger.  The Workbench only consumes that read-only view; it never re-enables
  a form locally.

Everything here is read-only: no task, no prepare, no transfer, no reservation,
no plugin import, no subprocess.  The module never imports the scheduler.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ALLOWED_KEYWORDS",
    "ANNOTATIONS",
    "BINDINGS",
    "BINDING_TABLE",
    "CapabilityFormError",
    "FORMATS",
    "FormDocument",
    "PLATFORM_FORMS",
    "REJECTED_KEYWORDS",
    "UI_FORM_SCHEMA",
    "UI_FORMS_SCHEMA",
    "UI_SUBSET_MAJOR",
    "UI_SUBSET_SCHEMA",
    "UI_SUBSET_VERSION",
    "capability_forms",
    "form_digest",
    "platform_form",
    "plugin_form",
    "read_form_document",
    "subset_digest",
    "subset_table",
    "validate_form_document",
]

logger = logging.getLogger(__name__)

#: Served document schema id (``GET /api/v2/science/capability-forms``).
UI_FORMS_SCHEMA = "acp2.ui.capability-forms.v1"
#: Frozen subset contract document schema id.
UI_SUBSET_SCHEMA = "acp2.ui.subset.v1"
#: Frozen subset version string every participant pins.
UI_SUBSET_VERSION = "acp2.ui-subset.v1"
#: Supported subset major (an unknown major is a typed refusal).
UI_SUBSET_MAJOR = 1
#: Single form document schema id.
UI_FORM_SCHEMA = "acp2.ui.form.v1"
#: Filename a third-party distribution may ship in its ``.dist-info``.
PLUGIN_FORM_FILENAME = "cccp_ui_form.json"

#: Keywords whose value domain is frozen below.  Only these are executable.
ALLOWED_KEYWORDS: Mapping[str, str] = {
    "type": "string|array of the frozen type names",
    "properties": "object of property name -> form schema node",
    "required": "array of declared property names",
    "additionalProperties": "false only",
    "enum": "non-empty array of scalars",
    "const": "scalar",
    "minimum": "number",
    "maximum": "number",
    "exclusiveMinimum": "number",
    "exclusiveMaximum": "number",
    "multipleOf": "number > 0",
    "minLength": "integer >= 0",
    "maxLength": "integer >= 0",
    "pattern": "ECMA-262 regular expression string",
    "format": "one of the frozen format names",
    "items": "one form schema node",
    "minItems": "integer >= 0",
    "maxItems": "integer >= 0",
    "uniqueItems": "boolean",
    "title": "plain text annotation (never markup)",
    "description": "plain text annotation (never markup)",
    "default": "scalar or array annotation",
    "deprecated": "boolean annotation",
    "examples": "array annotation",
    "x-acp": "ACP binding/widget block",
}

#: Purely descriptive keywords: never evaluated as a constraint.
ANNOTATIONS: tuple[str, ...] = (
    "title",
    "description",
    "default",
    "deprecated",
    "examples",
)

#: Frozen scalar types (``null`` is only legal inside a type array).
TYPE_NAMES: tuple[str, ...] = (
    "object",
    "array",
    "string",
    "number",
    "integer",
    "boolean",
    "null",
)

#: Frozen formats.  An unlisted format is a contract error, not a no-op.
FORMATS: tuple[str, ...] = ("date-time", "uri")

#: The 2020-12 vocabulary a form may **not** use.  These are rejected with a
#: typed refusal; ignoring them and submitting anyway is forbidden (SCOPE-OUT-02:
#: no open JSON Schema 2020-12 evaluator).
REJECTED_KEYWORDS: tuple[str, ...] = (
    "$schema",
    "$id",
    "$ref",
    "$defs",
    "$definitions",
    "$anchor",
    "$dynamicRef",
    "$dynamicAnchor",
    "$recursiveRef",
    "$vocabulary",
    "$comment",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "if",
    "then",
    "else",
    "dependentSchemas",
    "dependentRequired",
    "dependencies",
    "patternProperties",
    "propertyNames",
    "unevaluatedProperties",
    "unevaluatedItems",
    "contains",
    "prefixItems",
    "contentEncoding",
    "contentMediaType",
    "contentSchema",
)

#: Frozen widget names a form may request (rendering hint only).
WIDGETS: tuple[str, ...] = ("text", "textarea", "number", "select", "checkbox")

#: Physical quantity of each unit a binding may declare.
UNITS: Mapping[str, str] = {
    "second": "time",
    "kelvin": "temperature",
    "angstrom": "length",
    "hartree": "energy",
}

#: Served document projection states.
STATE_AVAILABLE = "available"
STATE_READ_ONLY = "read_only"
STATE_UNAVAILABLE = "unavailable"

#: Typed refusal codes (the browser branches on these, never on a message).
ERROR_CODES: Mapping[str, str] = {
    "ui_subset_unknown_major": "the form declares an unknown acp2.ui-subset major",
    "ui_form_unknown_major": "the form declares an unknown acp2.ui.form major",
    "ui_keyword_rejected": "the form uses a keyword outside the frozen subset",
    "ui_keyword_unknown": "the form uses a keyword the platform does not know",
    "ui_format_unknown": "the form declares an unknown format",
    "ui_binding_unknown": "the form field names an unknown TaskRequest binding",
    "ui_binding_missing": "a form field carries no TaskRequest binding",
    "ui_binding_duplicate": "two form fields bind the same TaskRequest path",
    "ui_binding_type_mismatch": "the field type disagrees with its binding",
    "ui_unit_mismatch": "the field unit disagrees with its binding",
    "ui_required_undeclared": "required names a property the form does not declare",
    "ui_form_malformed": "the form document is not a valid acp2.ui.form.v1 object",
    "ui_form_missing": "the capability declares no form",
    "ui_form_descriptor_mismatch": "the form is pinned to a different descriptor digest",
    "ui_form_unreadable": "the installed distribution's form document is unreadable",
    "ui_form_digest_mismatch": "the form body does not match its declared digest",
    "ui_subset_drift": "the served subset tables differ from the frozen ones",
}

_PROPERTY_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CapabilityFormError(Exception):
    """A typed form-contract refusal (``code`` is the machine branch)."""

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.params: dict[str, Any] = dict(params or {})

    def to_dict(self) -> dict[str, Any]:
        """Return the wire shape of the refusal."""
        payload: dict[str, Any] = {"code": self.code, "message": str(self)}
        if self.params:
            payload["params"] = dict(self.params)
        return payload


def _digest(document: Mapping[str, Any]) -> str:
    """Return ``sha256:`` over the canonical sorted JSON bytes of *document*."""
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Binding table (frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Binding:
    """One form field -> TaskRequest path -> wire body path.

    Attributes:
        id: Stable binding id a form field names in ``x-acp.binding``.
        task_request: Dotted ``TaskRequest`` path the value becomes.
        request_body: Dotted location in the preflight/submit body.
        value_type: Frozen scalar type of the value.
        unit: Unit id (``None`` for a dimensionless value).
        required: Whether the submit surface requires it.
        note: One-line authority note.
    """

    id: str
    task_request: str
    request_body: str
    value_type: str
    unit: str | None
    required: bool
    note: str

    def to_dict(self) -> dict[str, Any]:
        """Return the wire shape of the binding row."""
        return {
            "binding": self.id,
            "task_request": self.task_request,
            "request_body": self.request_body,
            "value_type": self.value_type,
            "unit": self.unit,
            "required": self.required,
            "note": self.note,
        }


#: The frozen binding table.  Every row names a ``TaskRequest`` path that exists
#: in ``cccp.calculation.requests`` and the wire location the v1 preflight/submit
#: bodies actually read (``V1JobCreateRequest``/``V1PreflightRequest``).
BINDINGS: tuple[Binding, ...] = (
    Binding(
        id="task.structure.source",
        task_request="structure.source",
        request_body="input.source",
        value_type="string",
        unit=None,
        required=True,
        note="Inline structure text (xyz/…) consumed by the submit surface.",
    ),
    Binding(
        id="task.charge",
        task_request="charge",
        request_body="input.charge",
        value_type="integer",
        unit=None,
        required=False,
        note="Total charge in elementary charge units.",
    ),
    Binding(
        id="task.multiplicity",
        task_request="multiplicity",
        request_body="input.multiplicity",
        value_type="integer",
        unit=None,
        required=False,
        note="Spin multiplicity (2S+1).",
    ),
    Binding(
        id="task.level.method",
        task_request="level.method",
        request_body="method.levels.single_point.functional",
        value_type="string",
        unit=None,
        required=False,
        note="Level-of-theory method/functional name.",
    ),
    Binding(
        id="task.level.basis",
        task_request="level.basis",
        request_body="method.levels.single_point.basis",
        value_type="string",
        unit=None,
        required=False,
        note="Basis set identifier.",
    ),
    Binding(
        id="task.resources.nproc",
        task_request="resources.nproc",
        request_body="resources.nproc",
        value_type="integer",
        unit=None,
        required=False,
        note="Worker process count (per-task quota).",
    ),
    Binding(
        id="task.resources.mem",
        task_request="resources.mem",
        request_body="resources.mem",
        value_type="string",
        unit=None,
        required=False,
        note="Total task memory budget (e.g. '2GB' or an MB integer).",
    ),
)

#: Binding id -> row (the only accepted ``x-acp.binding`` values).
BINDING_TABLE: Mapping[str, Binding] = {row.id: row for row in BINDINGS}


def subset_table() -> dict[str, Any]:
    """Return the frozen whitelist/binding/unit tables (the digest payload)."""
    return {
        "subset_version": UI_SUBSET_VERSION,
        "contract_version": UI_SUBSET_MAJOR,
        "keywords": dict(ALLOWED_KEYWORDS),
        "annotations": list(ANNOTATIONS),
        "rejected_keywords": list(REJECTED_KEYWORDS),
        "type_names": list(TYPE_NAMES),
        "formats": list(FORMATS),
        "widgets": list(WIDGETS),
        "units": dict(UNITS),
        "bindings": [row.to_dict() for row in BINDINGS],
        "error_codes": dict(ERROR_CODES),
    }


def subset_digest() -> str:
    """Return ``sha256:`` over :func:`subset_table` (the frozen subset digest)."""
    return _digest(subset_table())


# ---------------------------------------------------------------------------
# Form documents
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormDocument:
    """One capability form declaration.

    Attributes:
        capability_id: Capability the form belongs to.
        form: The validated ``acp2.ui.form.v1`` document (digest included).
        descriptor_digest_pin: Optional exact descriptor digest the form was
            reviewed against.  A mismatch is a typed ``unavailable`` state.
        origin: ``platform`` or ``plugin:<name>``.
    """

    capability_id: str
    form: Mapping[str, Any]
    descriptor_digest_pin: str | None = None
    origin: str = "platform"

    @property
    def digest(self) -> str:
        """Return the frozen form digest."""
        return str(self.form["form_digest"])


def _form_body(
    capability_id: str, request: Mapping[str, Any], form_schema: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "schema": UI_FORM_SCHEMA,
        "schema_version": 1,
        "subset_version": UI_SUBSET_VERSION,
        "capability_id": capability_id,
        "request": dict(request),
        "form_schema": dict(form_schema),
    }


def _sealed_form(
    capability_id: str,
    request: Mapping[str, Any],
    form_schema: Mapping[str, Any],
    *,
    title: str,
    description: str,
) -> dict[str, Any]:
    form = _form_body(capability_id, request, form_schema)
    form["title"] = title
    form["description"] = description
    form["form_digest"] = _digest(form)
    return form


def _job_request(workflow: str, template: Mapping[str, Any]) -> dict[str, Any]:
    return {"surface": "job_submit", "workflow": workflow, "template": dict(template)}


#: Platform-owned forms for the built-in capabilities.  A capability without a
#: row here and without a plugin-carried form is ``unavailable`` (``ui_form_missing``).
_BUILTIN_FORM_SPECS: tuple[tuple[str, dict[str, Any], dict[str, Any], str, str], ...] = (
    (
        "electronic.energy",
        _job_request(
            "singlepoint",
            {
                "input": {"source_type": "xyz_text", "source": ""},
                "method": {
                    "schema_id": "dft_singlepoint",
                    "levels": {"single_point": {"engine": "orca"}},
                },
                "resources": {},
            },
        ),
        {
            "type": "object",
            "required": ["source"],
            "additionalProperties": False,
            "properties": {
                "source": {
                    "type": "string",
                    "title": "Structure",
                    "description": "Inline structure text handed to the calculation.",
                    "minLength": 4,
                    "x-acp": {"binding": "task.structure.source", "widget": "textarea"},
                },
                "method": {
                    "type": "string",
                    "title": "Method",
                    "minLength": 2,
                    "maxLength": 64,
                    "x-acp": {"binding": "task.level.method", "widget": "text"},
                },
                "basis": {
                    "type": "string",
                    "title": "Basis set",
                    "minLength": 2,
                    "maxLength": 64,
                    "x-acp": {"binding": "task.level.basis", "widget": "text"},
                },
                "charge": {
                    "type": "integer",
                    "title": "Charge",
                    "default": 0,
                    "minimum": -10,
                    "maximum": 10,
                    "x-acp": {"binding": "task.charge", "widget": "number"},
                },
                "multiplicity": {
                    "type": "integer",
                    "title": "Multiplicity",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 10,
                    "x-acp": {"binding": "task.multiplicity", "widget": "number"},
                },
                "nproc": {
                    "type": "integer",
                    "title": "Processes",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 128,
                    "x-acp": {"binding": "task.resources.nproc", "widget": "number"},
                },
            },
        },
        "Single-point energy",
        "One frozen single-point request; every field maps onto a TaskRequest path.",
    ),
    (
        "geometry.optimize",
        _job_request(
            "optimize",
            {
                "input": {"source_type": "xyz_text", "source": ""},
                "method": {"levels": {"opt": {"engine": "orca"}}},
                "resources": {},
            },
        ),
        {
            "type": "object",
            "required": ["source"],
            "additionalProperties": False,
            "properties": {
                "source": {
                    "type": "string",
                    "title": "Structure",
                    "minLength": 4,
                    "x-acp": {"binding": "task.structure.source", "widget": "textarea"},
                },
                "method": {
                    "type": "string",
                    "title": "Method",
                    "minLength": 2,
                    "maxLength": 64,
                    "x-acp": {"binding": "task.level.method", "widget": "text"},
                },
                "basis": {
                    "type": "string",
                    "title": "Basis set",
                    "minLength": 2,
                    "maxLength": 64,
                    "x-acp": {"binding": "task.level.basis", "widget": "text"},
                },
                "charge": {
                    "type": "integer",
                    "title": "Charge",
                    "default": 0,
                    "minimum": -10,
                    "maximum": 10,
                    "x-acp": {"binding": "task.charge", "widget": "number"},
                },
            },
        },
        "Geometry optimization",
        "Optimization request bound to the optimize workflow.",
    ),
    (
        "vibration.analyze",
        _job_request(
            "frequency",
            {
                "input": {"source_type": "xyz_text", "source": ""},
                "method": {"levels": {"freq": {"engine": "orca"}}},
                "resources": {},
            },
        ),
        {
            "type": "object",
            "required": ["source"],
            "additionalProperties": False,
            "properties": {
                "source": {
                    "type": "string",
                    "title": "Structure",
                    "minLength": 4,
                    "x-acp": {"binding": "task.structure.source", "widget": "textarea"},
                },
                "method": {
                    "type": "string",
                    "title": "Method",
                    "minLength": 2,
                    "maxLength": 64,
                    "x-acp": {"binding": "task.level.method", "widget": "text"},
                },
                "mem": {
                    "type": "string",
                    "title": "Memory budget",
                    "pattern": "^[0-9]+(?:MB|GB)$",
                    "x-acp": {"binding": "task.resources.mem", "widget": "text"},
                },
            },
        },
        "Vibration analysis",
        "Frequency request whose result carries the vibrational analysis product.",
    ),
)

#: Frozen platform form documents (validated once at import).
PLATFORM_FORMS: tuple[FormDocument, ...] = tuple(
    FormDocument(
        capability_id=capability_id,
        form=_sealed_form(
            capability_id, request, form_schema, title=title, description=description
        ),
    )
    for capability_id, request, form_schema, title, description in _BUILTIN_FORM_SPECS
)

_PLATFORM_FORMS_BY_ID: Mapping[str, FormDocument] = {
    document.capability_id: document for document in PLATFORM_FORMS
}


def platform_form(capability_id: str) -> FormDocument | None:
    """Return the platform form of *capability_id* (``None`` when absent)."""
    return _PLATFORM_FORMS_BY_ID.get(capability_id)


def form_digest(form: Mapping[str, Any]) -> str:
    """Return the frozen digest of one form document body."""
    body = {key: value for key, value in form.items() if key != "form_digest"}
    return _digest(body)


# ---------------------------------------------------------------------------
# Whitelist validation
# ---------------------------------------------------------------------------


def _reject_node_key(key: str, path: str) -> CapabilityFormError:
    if key in REJECTED_KEYWORDS:
        return CapabilityFormError(
            "ui_keyword_rejected",
            f"{path}: keyword {key!r} is outside {UI_SUBSET_VERSION}",
            params={"keyword": key, "path": path, "rule": "not_in_ui_subset"},
        )
    return CapabilityFormError(
        "ui_keyword_unknown",
        f"{path}: unknown keyword {key!r}",
        params={"keyword": key, "path": path, "rule": "unknown_keyword"},
    )


def _require_number(value: Any, key: str, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}.{key} must be a number",
            params={"path": f"{path}.{key}", "rule": "number"},
        )
    return float(value)


def _require_int(value: Any, key: str, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}.{key} must be an integer >= 0",
            params={"path": f"{path}.{key}", "rule": "integer_minimum"},
        )
    return int(value)


def _check_type(value: Any, path: str) -> None:
    names = value if isinstance(value, list) else [value]
    if not names or not all(isinstance(item, str) and item in TYPE_NAMES for item in names):
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}.type must name frozen scalar types",
            params={"path": f"{path}.type", "allowed": list(TYPE_NAMES)},
        )


def _check_binding(node: Mapping[str, Any], path: str, seen: dict[str, str]) -> None:
    block = node.get("x-acp")
    if block is None:
        raise CapabilityFormError(
            "ui_binding_missing",
            f"{path}: every form field must declare x-acp.binding",
            params={"path": path, "rule": "binding_required"},
        )
    if not isinstance(block, Mapping):
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}.x-acp must be an object",
            params={"path": f"{path}.x-acp"},
        )
    unknown = sorted(str(key) for key in block if key not in {"binding", "unit", "widget"})
    if unknown:
        raise CapabilityFormError(
            "ui_keyword_unknown",
            f"{path}.x-acp: unknown key {unknown[0]!r}",
            params={"keyword": unknown[0], "path": f"{path}.x-acp"},
        )
    binding_id = block.get("binding")
    if not isinstance(binding_id, str) or binding_id not in BINDING_TABLE:
        raise CapabilityFormError(
            "ui_binding_unknown",
            f"{path}: binding {binding_id!r} is not in the frozen table",
            params={
                "field": path,
                "binding": binding_id,
                "allowed": sorted(BINDING_TABLE),
            },
        )
    binding = BINDING_TABLE[binding_id]
    declared_unit = block.get("unit")
    if declared_unit != binding.unit:
        raise CapabilityFormError(
            "ui_unit_mismatch",
            f"{path}: unit {declared_unit!r} disagrees with binding {binding_id!r} "
            f"({binding.unit!r})",
            params={
                "field": path,
                "binding": binding_id,
                "unit": declared_unit,
                "expected_unit": binding.unit,
            },
        )
    widget = block.get("widget")
    if widget is not None and (not isinstance(widget, str) or widget not in WIDGETS):
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}.x-acp.widget must be one of the frozen widgets",
            params={"path": f"{path}.x-acp.widget", "allowed": list(WIDGETS)},
        )
    if binding_id in seen:
        raise CapabilityFormError(
            "ui_binding_duplicate",
            f"{path}: binding {binding_id!r} is already used by {seen[binding_id]!r}",
            params={"binding": binding_id, "path": path, "first_path": seen[binding_id]},
        )
    seen[binding_id] = path
    declared_type = node.get("type")
    if declared_type in ("object", "array") or (
        isinstance(declared_type, list) and set(declared_type) & {"object", "array"}
    ):
        raise CapabilityFormError(
            "ui_binding_type_mismatch",
            f"{path}: a bound field must be a scalar, not {declared_type!r}",
            params={"field": path, "binding": binding_id},
        )
    if isinstance(declared_type, str) and declared_type != binding.value_type:
        raise CapabilityFormError(
            "ui_binding_type_mismatch",
            f"{path}: type {declared_type!r} disagrees with binding {binding_id!r} "
            f"({binding.value_type!r})",
            params={
                "field": path,
                "binding": binding_id,
                "type": declared_type,
                "expected_type": binding.value_type,
            },
        )


def _walk(node: Any, path: str, seen: dict[str, str], *, root: bool = False) -> None:
    if not isinstance(node, Mapping):
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path} must be an object",
            params={"path": path},
        )
    for key in node:
        if key not in ALLOWED_KEYWORDS:
            raise _reject_node_key(str(key), path)
    if "type" in node:
        _check_type(node["type"], path)
    if "format" in node:
        value = node["format"]
        if value not in FORMATS:
            raise CapabilityFormError(
                "ui_format_unknown",
                f"{path}: format {value!r} is not supported",
                params={"path": path, "format": value, "allowed": list(FORMATS)},
            )
    if "pattern" in node:
        value = node["pattern"]
        if not isinstance(value, str):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.pattern must be a string", params={"path": path}
            )
        try:
            re.compile(value)
        except re.error as exc:
            raise CapabilityFormError(
                "ui_form_malformed",
                f"{path}.pattern is not a valid regular expression: {exc}",
                params={"path": f"{path}.pattern"},
            ) from exc
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"):
        if key in node:
            value = _require_number(node[key], key, path)
            if key == "multipleOf" and value <= 0:
                raise CapabilityFormError(
                    "ui_form_malformed",
                    f"{path}.multipleOf must be > 0",
                    params={"path": f"{path}.multipleOf"},
                )
    if "minimum" in node and "maximum" in node:
        if float(node["minimum"]) > float(node["maximum"]):
            raise CapabilityFormError(
                "ui_form_malformed",
                f"{path}: minimum exceeds maximum",
                params={"path": path},
            )
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in node:
            _require_int(node[key], key, path)
    if "minLength" in node and "maxLength" in node:
        if int(node["minLength"]) > int(node["maxLength"]):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}: minLength exceeds maxLength", params={"path": path}
            )
    if "minItems" in node and "maxItems" in node:
        if int(node["minItems"]) > int(node["maxItems"]):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}: minItems exceeds maxItems", params={"path": path}
            )
    if "enum" in node:
        value = node["enum"]
        if not isinstance(value, (list, tuple)) or not value:
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.enum must be a non-empty array", params={"path": path}
            )
    if "const" in node:
        value = node["const"]
        if isinstance(value, (dict, list)) or value is None:
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.const must be a scalar", params={"path": path}
            )
    if "additionalProperties" in node and node["additionalProperties"] is not False:
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}.additionalProperties is frozen to false",
            params={"path": f"{path}.additionalProperties"},
        )
    for key in ("uniqueItems", "deprecated"):
        if key in node and not isinstance(node[key], bool):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.{key} must be a boolean", params={"path": path}
            )
    for key in ("title", "description"):
        if key in node and not isinstance(node[key], str):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.{key} must be plain text", params={"path": path}
            )
    if "examples" in node and not isinstance(node["examples"], list):
        raise CapabilityFormError(
            "ui_form_malformed", f"{path}.examples must be an array", params={"path": path}
        )
    properties = node.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.properties must be an object", params={"path": path}
            )
        for name, child in properties.items():
            if not isinstance(name, str) or _PROPERTY_NAME_RE.fullmatch(name) is None:
                raise CapabilityFormError(
                    "ui_form_malformed",
                    f"{path}.properties: invalid field name {name!r}",
                    params={"path": f"{path}.properties"},
                )
            _walk(child, f"{path}.properties.{name}", seen)
    required = node.get("required")
    if required is not None:
        if not isinstance(required, (list, tuple)) or not all(
            isinstance(item, str) for item in required
        ):
            raise CapabilityFormError(
                "ui_form_malformed", f"{path}.required must be an array", params={"path": path}
            )
        declared = properties if isinstance(properties, Mapping) else {}
        for name in required:
            if name not in declared:
                raise CapabilityFormError(
                    "ui_required_undeclared",
                    f"{path}.required names undeclared property {name!r}",
                    params={"path": f"{path}.required", "field": name},
                )
    if "items" in node:
        _walk(node["items"], f"{path}.items", seen)
    has_children = isinstance(node.get("properties"), Mapping) or "items" in node
    if has_children and "x-acp" in node:
        raise CapabilityFormError(
            "ui_form_malformed",
            f"{path}: a container node cannot carry x-acp (bindings live on leaves)",
            params={"path": path, "rule": "binding_on_container"},
        )
    if not root and not has_children:
        _check_binding(node, path, seen)


def validate_form_document(form: Any) -> Mapping[str, Any]:
    """Validate one ``acp2.ui.form.v1`` document against the frozen subset.

    Raises:
        CapabilityFormError: The document is malformed, declares an unknown
            major/version, uses a rejected or unknown keyword, names an unknown
            binding, disagrees with a binding's unit/type, or its body does not
            match its declared ``form_digest``.
    """
    if not isinstance(form, Mapping):
        raise CapabilityFormError(
            "ui_form_malformed", "a form document must be an object", params={"path": "$"}
        )
    schema = form.get("schema")
    if schema != UI_FORM_SCHEMA:
        raise CapabilityFormError(
            "ui_form_malformed",
            f"form schema {schema!r} is not {UI_FORM_SCHEMA!r}",
            params={"schema": schema},
        )
    version = form.get("schema_version")
    if version != 1:
        raise CapabilityFormError(
            "ui_form_unknown_major",
            f"unsupported form schema version {version!r}; supported: 1",
            params={"schema_version": version, "supported": 1},
        )
    subset_version = form.get("subset_version")
    if subset_version != UI_SUBSET_VERSION:
        raise CapabilityFormError(
            "ui_subset_unknown_major",
            f"unsupported subset {subset_version!r}; supported: {UI_SUBSET_VERSION}",
            params={"subset_version": subset_version, "supported": UI_SUBSET_VERSION},
        )
    declared = form.get("form_digest")
    if declared is not None and (
        not isinstance(declared, str) or _DIGEST_RE.fullmatch(declared) is None
    ):
        raise CapabilityFormError(
            "ui_form_malformed",
            "form_digest must be 'sha256:<64 hex>'",
            params={"path": "form_digest"},
        )
    capability_id = form.get("capability_id")
    if not isinstance(capability_id, str) or not capability_id.strip():
        raise CapabilityFormError(
            "ui_form_malformed", "capability_id is required", params={"path": "capability_id"}
        )
    request = form.get("request")
    if not isinstance(request, Mapping):
        raise CapabilityFormError(
            "ui_form_malformed", "request must be an object", params={"path": "request"}
        )
    unknown_request = sorted(
        str(key) for key in request if key not in {"surface", "workflow", "template"}
    )
    if unknown_request:
        raise _reject_node_key(unknown_request[0], "request")
    surface = request.get("surface")
    if surface not in {"job_submit", "analyze"}:
        raise CapabilityFormError(
            "ui_form_malformed",
            f"request.surface {surface!r} is not frozen (job_submit|analyze)",
            params={"path": "request.surface"},
        )
    workflow = request.get("workflow")
    if surface == "job_submit" and (not isinstance(workflow, str) or not workflow.strip()):
        raise CapabilityFormError(
            "ui_form_malformed",
            "request.workflow is required for a job_submit form",
            params={"path": "request.workflow"},
        )
    template = request.get("template")
    if not isinstance(template, Mapping):
        raise CapabilityFormError(
            "ui_form_malformed",
            "request.template must be an object",
            params={"path": "request.template"},
        )
    form_schema = form.get("form_schema")
    if not isinstance(form_schema, Mapping):
        raise CapabilityFormError(
            "ui_form_malformed", "form_schema must be an object", params={"path": "form_schema"}
        )
    if form_schema.get("type") != "object":
        raise CapabilityFormError(
            "ui_form_malformed",
            "form_schema.type must be 'object'",
            params={"path": "form_schema.type"},
        )
    _walk(form_schema, "form_schema", {}, root=True)
    if declared is not None and form_digest(form) != declared:
        raise CapabilityFormError(
            "ui_form_digest_mismatch",
            "the form body does not match its declared form_digest",
            params={"declared": declared, "computed": form_digest(form)},
        )
    return form


for _document in PLATFORM_FORMS:  # fail closed at import: a bad built-in form
    validate_form_document(_document.form)  # pragma: no cover - import guard


# ---------------------------------------------------------------------------
# Third-party form reading (metadata only)
# ---------------------------------------------------------------------------

_FORM_CACHE: dict[tuple[str, str, str], FormDocument | CapabilityFormError] = {}
_FORM_CACHE_LOCK = threading.Lock()


def read_form_document(
    distribution_name: str,
    *,
    reader: Callable[[str], str | None] | None = None,
) -> str | None:
    """Return the raw ``cccp_ui_form.json`` text of an installed distribution.

    Metadata only: ``importlib.metadata`` reads a file from the distribution's
    ``.dist-info``; plugin code is never imported.  A missing file is ``None``
    (the platform form, if any, still applies), an unreadable one is a typed
    ``ui_form_unreadable`` refusal.
    """
    if reader is not None:
        return reader(distribution_name)
    import importlib.metadata as metadata

    try:
        distribution = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return None
    try:
        return distribution.read_text(PLUGIN_FORM_FILENAME)
    except OSError as exc:  # pragma: no cover - unreadable dist-info
        raise CapabilityFormError(
            "ui_form_unreadable",
            f"{distribution_name}: {exc}",
            params={"distribution": distribution_name},
        ) from exc


def plugin_form(
    distribution_name: str,
    capability_id: str,
    *,
    reader: Callable[[str], str | None] | None = None,
    descriptor_digest: str | None = None,
) -> FormDocument | None:
    """Return the validated form a distribution ships for *capability_id*.

    The document is cached per (distribution, capability, descriptor digest) —
    an upgrade changes the descriptor digest and therefore re-reads the form.
    A declared ``descriptor_digest_pin`` that disagrees with the *current*
    descriptor is a typed refusal (the UI must be reviewed for the new version).
    """
    key = (distribution_name, capability_id, descriptor_digest or "")
    with _FORM_CACHE_LOCK:
        cached = _FORM_CACHE.get(key)
    if isinstance(cached, FormDocument):
        return cached
    if isinstance(cached, CapabilityFormError):
        raise cached
    try:
        raw = read_form_document(distribution_name, reader=reader)
        if raw is None:
            return None
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CapabilityFormError(
                "ui_form_unreadable",
                f"{distribution_name}/{PLUGIN_FORM_FILENAME} is not JSON: {exc}",
                params={"distribution": distribution_name},
            ) from exc
        if not isinstance(document, Mapping):
            raise CapabilityFormError(
                "ui_form_malformed",
                f"{distribution_name}/{PLUGIN_FORM_FILENAME} must be an object",
                params={"distribution": distribution_name},
            )
        form = validate_form_document(document)
        if str(form.get("capability_id")) != capability_id:
            raise CapabilityFormError(
                "ui_form_malformed",
                f"{distribution_name} declares a form for "
                f"{form.get('capability_id')!r}, not {capability_id!r}",
                params={
                    "distribution": distribution_name,
                    "capability_id": capability_id,
                    "form_capability_id": form.get("capability_id"),
                },
            )
        pin = form.get("descriptor_digest_pin")
        if pin is not None:
            if not isinstance(pin, str) or descriptor_digest != pin:
                raise CapabilityFormError(
                    "ui_form_descriptor_mismatch",
                    "the form is pinned to a different descriptor digest "
                    "(the capability version changed; the form must be re-reviewed)",
                    params={"pinned": pin, "descriptor_digest": descriptor_digest},
                )
        result = FormDocument(
            capability_id=capability_id,
            form=form,
            descriptor_digest_pin=pin if isinstance(pin, str) else None,
            origin=f"plugin:{distribution_name}",
        )
    except CapabilityFormError as exc:
        with _FORM_CACHE_LOCK:
            _FORM_CACHE[key] = exc
        raise
    with _FORM_CACHE_LOCK:
        _FORM_CACHE[key] = result
    return result


def clear_form_cache() -> None:
    """Drop the per-process form cache (tests and an enablement change)."""
    with _FORM_CACHE_LOCK:
        _FORM_CACHE.clear()


# ---------------------------------------------------------------------------
# Served projection
# ---------------------------------------------------------------------------


def _distribution_name(entry: Mapping[str, Any]) -> str:
    plugin = entry.get("plugin")
    if isinstance(plugin, Mapping):
        for key in ("distribution", "name"):
            value = plugin.get(key)
            if isinstance(value, str) and value:
                return value
    value = entry.get("plugin_name")
    return str(value) if isinstance(value, str) and value else ""


def _available_entry(
    entry: Mapping[str, Any],
    document: FormDocument,
) -> dict[str, Any]:
    capability_id = str(entry.get("capability_id") or document.capability_id)
    descriptor_digest = entry.get("descriptor_digest")
    return {
        "capability_id": capability_id,
        "state": STATE_AVAILABLE,
        "capability_source": str(entry.get("capability_source") or "builtin"),
        "descriptor_digest": descriptor_digest if isinstance(descriptor_digest, str) else None,
        "descriptor_digest_pin": document.descriptor_digest_pin,
        "form_digest": document.digest,
        "form_origin": document.origin,
        "form": dict(document.form),
    }


def _unavailable_entry(
    capability_id: str,
    error: CapabilityFormError,
    *,
    capability_source: str = "builtin",
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "state": STATE_UNAVAILABLE,
        "capability_source": capability_source,
        "error": error.to_dict(),
    }


def capability_forms(
    *,
    descriptors: Sequence[Mapping[str, Any]],
    enablements: Sequence[Any] = (),
    etag: str = "",
    form_reader: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Project the served capability-form document.

    Args:
        descriptors: The merged discovery descriptors (built-in + domain +
            authorised plugin entries) — the same objects
            ``GET /api/v2/science/capabilities`` forwards.
        enablements: The M2-05 ledger records (enabled *and* disabled).  A
            capability that is no longer active but has a frozen record is
            projected ``read_only`` with the frozen descriptor digest.
        etag: The catalog etag the descriptors came from (cache identity).
        form_reader: Injected third-party form reader (tests).

    Returns:
        The ``acp2.ui.capability-forms.v1`` document: the frozen subset tables
        with their digest, one entry per discovered capability
        (``available``/``unavailable``), and one ``read_only`` entry per
        historical enablement that discovery no longer lists.
    """
    forms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in descriptors:
        capability_id = entry.get("capability_id")
        if not isinstance(capability_id, str) or not capability_id:
            continue
        if capability_id in seen:
            continue
        seen.add(capability_id)
        source = str(entry.get("capability_source") or "builtin")
        try:
            document: FormDocument | None = None
            if source == "science_plugin":
                distribution = _distribution_name(entry)
                descriptor_digest = entry.get("descriptor_digest")
                if distribution:
                    document = plugin_form(
                        distribution,
                        capability_id,
                        reader=form_reader,
                        descriptor_digest=descriptor_digest
                        if isinstance(descriptor_digest, str)
                        else None,
                    )
            if document is None:
                document = platform_form(capability_id)
            if document is None:
                raise CapabilityFormError(
                    "ui_form_missing",
                    f"{capability_id} declares no {UI_SUBSET_VERSION} form",
                    params={"capability_id": capability_id},
                )
            forms.append(_available_entry(entry, document))
        except CapabilityFormError as exc:
            forms.append(_unavailable_entry(capability_id, exc, capability_source=source))

    read_only: list[dict[str, Any]] = []
    for record in enablements:
        capability_id = str(getattr(record, "capability_id", "") or "")
        if not capability_id or capability_id in seen:
            continue
        seen.add(capability_id)
        frozen = getattr(record, "descriptor", None)
        version = getattr(record, "version", None)
        read_only.append(
            {
                "capability_id": capability_id,
                "state": STATE_READ_ONLY,
                "capability_source": "science_plugin",
                "version": version if isinstance(version, str) else None,
                "descriptor_digest": getattr(record, "descriptor_digest", None),
                "enabled": bool(getattr(record, "enabled", False)),
                "frozen_descriptor_schema": (
                    frozen.get("schema") if isinstance(frozen, Mapping) else None
                ),
                "reason": "capability_not_active",
            }
        )

    return {
        "schema": UI_FORMS_SCHEMA,
        "schema_version": UI_SUBSET_MAJOR,
        "subset_version": UI_SUBSET_VERSION,
        "subset_digest": subset_digest(),
        "subset": subset_table(),
        "catalog_etag": etag,
        "forms": forms,
        "read_only": read_only,
    }
