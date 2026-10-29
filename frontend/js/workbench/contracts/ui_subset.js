/*
 * ACP Workbench contracts — capability form subset validator (M3-06).
 *
 * Owner: frontend/js/workbench/contracts/** (OpenCode). Consumers: the
 * capability-form feature and the Node module tests.
 *
 * The platform never ships an open JSON Schema 2020-12 evaluator to the
 * browser (release SCOPE-OUT-02). A capability form is evaluated against ONE
 * explicit whitelist, `acp2.ui-subset.v1`:
 *
 *   * only the keywords below are executable, each with its frozen value
 *     domain (see docs/reference/next-change/acp2-contracts/science/
 *     ui-subset.v1.json, mirrored from src/acp/api/capability_forms.py);
 *   * `$ref`, the combinators (allOf/anyOf/oneOf/not), conditionals and the
 *     rest of the 2020-12 vocabulary are REJECTED — an unknown/rejected
 *     keyword is a typed `unavailable`, never silently ignored and never
 *     submitted;
 *   * every leaf field must carry `x-acp.binding` naming a row of the frozen
 *     binding table (TaskRequest path + submit-body path + unit), so a form can
 *     never grow an input that has no execution meaning.
 *
 * The served document carries the same tables and their digest. `tablesMatch`
 * refuses a served table that drifted from this file, so a UI built against a
 * different subset can never apply a stale interpretation.
 *
 * Nothing here touches the DOM or the network: it is pure data + validation.
 *
 * Version: v20261027.1
 */
window.ACPWorkbench = window.ACPWorkbench || {};
ACPWorkbench.contracts = ACPWorkbench.contracts || {};
ACPWorkbench.contracts.uiSubset = (function () {
  "use strict";

  var SUBSET_VERSION = "acp2.ui-subset.v1";
  var SUBSET_MAJOR = 1;
  var FORM_SCHEMA = "acp2.ui.form.v1";
  var FORMS_SCHEMA = "acp2.ui.capability-forms.v1";

  var ALLOWED_KEYWORDS = [
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "format", "items", "minItems",
    "maxItems", "uniqueItems", "title", "description", "default", "deprecated",
    "examples", "x-acp",
  ];
  var ANNOTATIONS = ["title", "description", "default", "deprecated", "examples"];
  var REJECTED_KEYWORDS = [
    "$schema", "$id", "$ref", "$defs", "$definitions", "$anchor", "$dynamicRef",
    "$dynamicAnchor", "$recursiveRef", "$vocabulary", "$comment", "allOf",
    "anyOf", "oneOf", "not", "if", "then", "else", "dependentSchemas",
    "dependentRequired", "dependencies", "patternProperties", "propertyNames",
    "unevaluatedProperties", "unevaluatedItems", "contains", "prefixItems",
    "contentEncoding", "contentMediaType", "contentSchema",
  ];
  var TYPE_NAMES = ["object", "array", "string", "number", "integer", "boolean", "null"];
  var FORMATS = ["date-time", "uri"];
  var WIDGETS = ["text", "textarea", "number", "select", "checkbox"];

  var UNITS = {
    second: "time",
    kelvin: "temperature",
    angstrom: "length",
    hartree: "energy",
  };

  var BINDINGS = [
    {
      binding: "task.structure.source", task_request: "structure.source",
      request_body: "input.source", value_type: "string", unit: null,
      required: true,
    },
    {
      binding: "task.charge", task_request: "charge",
      request_body: "input.charge", value_type: "integer", unit: null,
      required: false,
    },
    {
      binding: "task.multiplicity", task_request: "multiplicity",
      request_body: "input.multiplicity", value_type: "integer", unit: null,
      required: false,
    },
    {
      binding: "task.level.method", task_request: "level.method",
      request_body: "method.levels.single_point.functional",
      value_type: "string", unit: null, required: false,
    },
    {
      binding: "task.level.basis", task_request: "level.basis",
      request_body: "method.levels.single_point.basis",
      value_type: "string", unit: null, required: false,
    },
    {
      binding: "task.resources.nproc", task_request: "resources.nproc",
      request_body: "resources.nproc", value_type: "integer", unit: null,
      required: false,
    },
    {
      binding: "task.resources.mem", task_request: "resources.mem",
      request_body: "resources.mem", value_type: "string", unit: null,
      required: false,
    },
  ];

  var ERROR_CODES = {
    ui_subset_unknown_major: "unknown acp2.ui-subset major",
    ui_form_unknown_major: "unknown acp2.ui.form major",
    ui_keyword_rejected: "keyword outside the frozen subset",
    ui_keyword_unknown: "unknown keyword",
    ui_format_unknown: "unknown format",
    ui_binding_unknown: "unknown TaskRequest binding",
    ui_binding_missing: "field carries no TaskRequest binding",
    ui_binding_duplicate: "two fields bind the same TaskRequest path",
    ui_binding_type_mismatch: "field type disagrees with its binding",
    ui_unit_mismatch: "field unit disagrees with its binding",
    ui_required_undeclared: "required names an undeclared property",
    ui_form_malformed: "malformed form document",
    ui_form_missing: "capability declares no form",
    ui_form_descriptor_mismatch: "form pinned to a different descriptor digest",
    ui_form_unreadable: "installed form document is unreadable",
    ui_form_digest_mismatch: "form body does not match its declared digest",
    ui_subset_drift: "served subset tables differ from the frozen ones",
    ui_value_required: "required value missing",
    ui_value_type: "value has the wrong type",
    ui_value_enum: "value is outside the frozen enum",
    ui_value_bounds: "value is outside the frozen numeric bounds",
    ui_value_length: "value is outside the frozen length bounds",
    ui_value_pattern: "value does not match the frozen pattern",
    ui_value_format: "value does not match the frozen format",
    ui_value_unknown_field: "value names a field the form does not declare",
  };
  var BINDING_BY_ID = {};
  BINDINGS.forEach(function (row) { BINDING_BY_ID[row.binding] = row; });

  function isObject(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }
  function hasOwn(object, key) {
    return Object.prototype.hasOwnProperty.call(object, key);
  }
  function isInteger(value) {
    return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
  }

  /** Canonical (sorted-key) JSON of a value; used for table/digest equality. */
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (isObject(value)) {
      var out = {};
      Object.keys(value).sort().forEach(function (key) {
        if (value[key] !== undefined) out[key] = canonical(value[key]);
      });
      return out;
    }
    return value;
  }
  function sameJson(a, b) {
    return JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
  }

  function problem(code, path, message) {
    return { code: code, path: path, message: message };
  }
  function result(problems) {
    var list = problems || [];
    return {
      ok: list.length === 0,
      code: list.length ? list[0].code : null,
      problems: list,
    };
  }

  function subsetTable() {
    return {
      subset_version: SUBSET_VERSION,
      contract_version: SUBSET_MAJOR,
      keywords: ALLOWED_KEYWORDS.reduce(function (acc, name) {
        acc[name] = true;
        return acc;
      }, {}),
      annotations: ANNOTATIONS.slice(),
      rejected_keywords: REJECTED_KEYWORDS.slice(),
      type_names: TYPE_NAMES.slice(),
      formats: FORMATS.slice(),
      widgets: WIDGETS.slice(),
      units: Object.assign({}, UNITS),
      bindings: BINDINGS.map(function (row) { return Object.assign({}, row); }),
      error_codes: Object.assign({}, ERROR_CODES),
    };
  }

  function bindingTable() {
    return BINDING_BY_ID;
  }

  /** Compare a served `subset` block with the frozen local tables.
   *
   * The platform serves the tables it validated the forms with; a mismatch
   * means this UI build speaks a different subset and must refuse to render
   * (`ui_subset_drift`) instead of interpreting a form it does not know.
   */
  function tablesMatch(served) {
    if (!isObject(served)) return false;
    var local = subsetTable();
    if (served.subset_version !== local.subset_version) return false;
    if (served.contract_version !== local.contract_version) return false;
    var exact = ["annotations", "rejected_keywords", "type_names", "formats",
      "widgets", "units"];
    for (var i = 0; i < exact.length; i += 1) {
      if (!sameJson(served[exact[i]], local[exact[i]])) return false;
    }
    if (!isObject(served.keywords)) return false;
    if (!sameJson(Object.keys(served.keywords).sort(), Object.keys(local.keywords).sort())) {
      return false;
    }
    // Binding rows: the platform serves a `note` the browser does not need, so
    // compare the executable columns only (never the whole row).
    if (!Array.isArray(served.bindings) || served.bindings.length !== local.bindings.length) {
      return false;
    }
    var columns = ["binding", "task_request", "request_body", "value_type", "unit", "required"];
    for (var row = 0; row < served.bindings.length; row += 1) {
      var servedRow = served.bindings[row];
      var localRow = local.bindings[row];
      if (!isObject(servedRow)) return false;
      for (var column = 0; column < columns.length; column += 1) {
        if (servedRow[columns[column]] !== localRow[columns[column]]) return false;
      }
    }
    // Error codes: every code the platform can emit must be known here. The
    // browser owns a few extra value-level codes of its own, which is fine.
    if (isObject(served.error_codes)) {
      var servedCodes = Object.keys(served.error_codes);
      for (var code = 0; code < servedCodes.length; code += 1) {
        if (!hasOwn(local.error_codes, servedCodes[code])) return false;
      }
    }
    return true;
  }

  // ------------------------------------------------------------------
  // schema validation (whitelist only)
  // ------------------------------------------------------------------

  function checkTypeBlock(node, path, problems) {
    var value = node.type;
    var names = Array.isArray(value) ? value : [value];
    if (!names.length || !names.every(function (name) {
      return typeof name === "string" && TYPE_NAMES.indexOf(name) !== -1;
    })) {
      problems.push(problem("ui_form_malformed", path + ".type",
        "type must name the frozen scalar types"));
    }
  }

  function numberOf(value) {
    return typeof value === "number" && isFinite(value) ? value : null;
  }

  function checkBindingNode(node, path, seen, problems) {
    var block = node["x-acp"];
    if (block === undefined) {
      problems.push(problem("ui_binding_missing", path,
        "every form field must declare x-acp.binding"));
      return;
    }
    if (!isObject(block)) {
      problems.push(problem("ui_form_malformed", path + ".x-acp",
        "x-acp must be an object"));
      return;
    }
    Object.keys(block).forEach(function (key) {
      if (["binding", "unit", "widget"].indexOf(key) === -1) {
        problems.push(problem("ui_keyword_unknown", path + ".x-acp",
          "unknown x-acp key " + JSON.stringify(key)));
      }
    });
    var bindingId = block.binding;
    var row = typeof bindingId === "string" ? BINDING_BY_ID[bindingId] : null;
    if (!row) {
      problems.push(problem("ui_binding_unknown", path,
        "binding " + JSON.stringify(String(bindingId)) + " is not in the frozen table"));
      return;
    }
    var unit = block.unit === undefined ? null : block.unit;
    if (unit !== row.unit) {
      problems.push(problem("ui_unit_mismatch", path,
        "unit " + JSON.stringify(unit) + " disagrees with binding " +
        JSON.stringify(bindingId) + " (" + JSON.stringify(row.unit) + ")"));
    }
    if (block.widget !== undefined && WIDGETS.indexOf(block.widget) === -1) {
      problems.push(problem("ui_form_malformed", path + ".x-acp.widget",
        "widget must be one of the frozen widgets"));
    }
    if (hasOwn(seen, bindingId)) {
      problems.push(problem("ui_binding_duplicate", path,
        "binding " + JSON.stringify(bindingId) + " is already used by " +
        JSON.stringify(seen[bindingId])));
    } else {
      seen[bindingId] = path;
    }
    var declared = node.type;
    if (declared === "object" || declared === "array" ||
        (Array.isArray(declared) && (declared.indexOf("object") !== -1 ||
          declared.indexOf("array") !== -1))) {
      problems.push(problem("ui_binding_type_mismatch", path,
        "a bound field must be a scalar, not " + JSON.stringify(declared)));
    } else if (typeof declared === "string" && declared !== row.value_type) {
      problems.push(problem("ui_binding_type_mismatch", path,
        "type " + JSON.stringify(declared) + " disagrees with binding " +
        JSON.stringify(bindingId) + " (" + JSON.stringify(row.value_type) + ")"));
    }
  }

  function walkNode(node, path, seen, problems, isRoot) {
    if (!isObject(node)) {
      problems.push(problem("ui_form_malformed", path, "form schema node must be an object"));
      return;
    }
    Object.keys(node).forEach(function (key) {
      if (ALLOWED_KEYWORDS.indexOf(key) === -1) {
        var code = REJECTED_KEYWORDS.indexOf(key) !== -1
          ? "ui_keyword_rejected" : "ui_keyword_unknown";
        problems.push(problem(code, path,
          "keyword " + JSON.stringify(key) + " is outside " + SUBSET_VERSION));
      }
    });
    if (hasOwn(node, "type")) checkTypeBlock(node, path, problems);
    if (hasOwn(node, "format") && FORMATS.indexOf(node.format) === -1) {
      problems.push(problem("ui_format_unknown", path,
        "format " + JSON.stringify(String(node.format)) + " is not supported"));
    }
    if (hasOwn(node, "pattern")) {
      if (typeof node.pattern !== "string") {
        problems.push(problem("ui_form_malformed", path + ".pattern",
          "pattern must be a string"));
      } else {
        try {
          new RegExp(node.pattern);
        } catch (error) {
          problems.push(problem("ui_form_malformed", path + ".pattern",
            "pattern is not a valid regular expression"));
        }
      }
    }
    ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"]
      .forEach(function (key) {
        if (!hasOwn(node, key)) return;
        var value = numberOf(node[key]);
        if (value === null) {
          problems.push(problem("ui_form_malformed", path + "." + key,
            "must be a number"));
        } else if (key === "multipleOf" && value <= 0) {
          problems.push(problem("ui_form_malformed", path + ".multipleOf",
            "must be > 0"));
        }
      });
    if (numberOf(node.minimum) !== null && numberOf(node.maximum) !== null &&
        node.minimum > node.maximum) {
      problems.push(problem("ui_form_malformed", path, "minimum exceeds maximum"));
    }
    ["minLength", "maxLength", "minItems", "maxItems"].forEach(function (key) {
      if (!hasOwn(node, key)) return;
      if (!isInteger(node[key]) || node[key] < 0) {
        problems.push(problem("ui_form_malformed", path + "." + key,
          "must be an integer >= 0"));
      }
    });
    if (isInteger(node.minLength) && isInteger(node.maxLength) &&
        node.minLength > node.maxLength) {
      problems.push(problem("ui_form_malformed", path, "minLength exceeds maxLength"));
    }
    if (isInteger(node.minItems) && isInteger(node.maxItems) &&
        node.minItems > node.maxItems) {
      problems.push(problem("ui_form_malformed", path, "minItems exceeds maxItems"));
    }
    if (hasOwn(node, "enum")) {
      if (!Array.isArray(node.enum) || !node.enum.length) {
        problems.push(problem("ui_form_malformed", path + ".enum",
          "enum must be a non-empty array"));
      }
    }
    if (hasOwn(node, "const")) {
      var constValue = node["const"];
      if (isObject(constValue) || Array.isArray(constValue) || constValue === null) {
        problems.push(problem("ui_form_malformed", path + ".const",
          "const must be a scalar"));
      }
    }
    if (hasOwn(node, "additionalProperties") && node.additionalProperties !== false) {
      problems.push(problem("ui_form_malformed", path + ".additionalProperties",
        "additionalProperties is frozen to false"));
    }
    ["uniqueItems", "deprecated"].forEach(function (key) {
      if (hasOwn(node, key) && typeof node[key] !== "boolean") {
        problems.push(problem("ui_form_malformed", path + "." + key,
          "must be a boolean"));
      }
    });
    ["title", "description"].forEach(function (key) {
      if (hasOwn(node, key) && typeof node[key] !== "string") {
        problems.push(problem("ui_form_malformed", path + "." + key,
          "must be plain text"));
      }
    });
    if (hasOwn(node, "examples") && !Array.isArray(node.examples)) {
      problems.push(problem("ui_form_malformed", path + ".examples",
        "examples must be an array"));
    }
    var properties = node.properties;
    if (properties !== undefined) {
      if (!isObject(properties)) {
        problems.push(problem("ui_form_malformed", path + ".properties",
          "properties must be an object"));
      } else {
        Object.keys(properties).forEach(function (name) {
          if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) {
            problems.push(problem("ui_form_malformed", path + ".properties",
              "invalid field name " + JSON.stringify(name)));
            return;
          }
          walkNode(properties[name], path + ".properties." + name, seen, problems, false);
        });
      }
    }
    if (hasOwn(node, "required")) {
      if (!Array.isArray(node.required) ||
          !node.required.every(function (name) { return typeof name === "string"; })) {
        problems.push(problem("ui_form_malformed", path + ".required",
          "required must be an array"));
      } else {
        var declared = isObject(properties) ? properties : {};
        node.required.forEach(function (name) {
          if (!hasOwn(declared, name)) {
            problems.push(problem("ui_required_undeclared", path + ".required",
              "required names undeclared property " + JSON.stringify(name)));
          }
        });
      }
    }
    if (hasOwn(node, "items")) {
      walkNode(node.items, path + ".items", seen, problems, false);
    }
    var hasChildren = isObject(node.properties) || hasOwn(node, "items");
    if (hasChildren && hasOwn(node, "x-acp")) {
      problems.push(problem("ui_form_malformed", path,
        "a container node cannot carry x-acp (bindings live on leaves)"));
    }
    if (!isRoot && !hasChildren) checkBindingNode(node, path, seen, problems);
  }

  /** Validate one served form document against the frozen subset. */
  function validateFormDocument(form) {
    var problems = [];
    if (!isObject(form)) {
      return result([problem("ui_form_malformed", "$", "a form document must be an object")]);
    }
    if (form.schema !== FORM_SCHEMA) {
      problems.push(problem("ui_form_malformed", "$",
        "form schema " + JSON.stringify(String(form.schema)) + " is not " + FORM_SCHEMA));
    }
    if (form.schema_version !== 1) {
      problems.push(problem("ui_form_unknown_major", "schema_version",
        "unsupported form schema version " + JSON.stringify(form.schema_version)));
    }
    if (form.subset_version !== SUBSET_VERSION) {
      problems.push(problem("ui_subset_unknown_major", "subset_version",
        "unsupported subset " + JSON.stringify(String(form.subset_version)) +
        "; supported: " + SUBSET_VERSION));
    }
    if (typeof form.capability_id !== "string" || !form.capability_id) {
      problems.push(problem("ui_form_malformed", "capability_id", "capability_id is required"));
    }
    var request = form.request;
    if (!isObject(request)) {
      problems.push(problem("ui_form_malformed", "request", "request must be an object"));
    } else {
      Object.keys(request).forEach(function (key) {
        if (["surface", "workflow", "template"].indexOf(key) === -1) {
          problems.push(problem("ui_keyword_unknown", "request",
            "unknown request key " + JSON.stringify(key)));
        }
      });
      if (request.surface !== "job_submit" && request.surface !== "analyze") {
        problems.push(problem("ui_form_malformed", "request.surface",
          "surface is not frozen (job_submit|analyze)"));
      }
      if (request.surface === "job_submit" &&
          (typeof request.workflow !== "string" || !request.workflow)) {
        problems.push(problem("ui_form_malformed", "request.workflow",
          "workflow is required for a job_submit form"));
      }
      if (!isObject(request.template)) {
        problems.push(problem("ui_form_malformed", "request.template",
          "template must be an object"));
      }
    }
    var formSchema = form.form_schema;
    if (!isObject(formSchema)) {
      problems.push(problem("ui_form_malformed", "form_schema",
        "form_schema must be an object"));
    } else {
      if (formSchema.type !== "object") {
        problems.push(problem("ui_form_malformed", "form_schema.type",
          "form_schema.type must be 'object'"));
      }
      walkNode(formSchema, "form_schema", {}, problems, true);
    }
    return result(problems);
  }

  // ------------------------------------------------------------------
  // value validation + request building (whitelist only)
  // ------------------------------------------------------------------

  function formatOk(name, value) {
    if (typeof value !== "string") return false;
    if (name === "date-time") return !isNaN(Date.parse(value));
    if (name === "uri") return /^[A-Za-z][A-Za-z0-9+.-]*:\S+$/.test(value);
    return false;
  }

  function typeOk(name, value) {
    if (name === "null") return value === null;
    if (name === "object") return isObject(value);
    if (name === "array") return Array.isArray(value);
    if (name === "string") return typeof value === "string";
    if (name === "boolean") return typeof value === "boolean";
    if (name === "integer") return isInteger(value);
    return typeof value === "number" && isFinite(value);
  }

  function checkScalar(node, path, value, problems) {
    if (hasOwn(node, "type")) {
      var names = Array.isArray(node.type) ? node.type : [node.type];
      var ok = names.some(function (name) { return typeOk(name, value); });
      if (!ok) {
        problems.push(problem("ui_value_type", path,
          "value has the wrong type (expected " + names.join("|") + ")"));
        return;
      }
    }
    if (hasOwn(node, "const") && value !== node["const"]) {
      problems.push(problem("ui_value_enum", path, "value must equal const"));
    }
    if (Array.isArray(node.enum) && node.enum.indexOf(value) === -1) {
      problems.push(problem("ui_value_enum", path, "value is outside the frozen enum"));
    }
    if (typeof value === "number") {
      if (numberOf(node.minimum) !== null && value < node.minimum) {
        problems.push(problem("ui_value_bounds", path, "value is below minimum"));
      }
      if (numberOf(node.maximum) !== null && value > node.maximum) {
        problems.push(problem("ui_value_bounds", path, "value is above maximum"));
      }
      if (numberOf(node.exclusiveMinimum) !== null && value <= node.exclusiveMinimum) {
        problems.push(problem("ui_value_bounds", path, "value must exceed exclusiveMinimum"));
      }
      if (numberOf(node.exclusiveMaximum) !== null && value >= node.exclusiveMaximum) {
        problems.push(problem("ui_value_bounds", path, "value must be below exclusiveMaximum"));
      }
      if (numberOf(node.multipleOf) !== null && node.multipleOf > 0 &&
          Math.abs(value / node.multipleOf - Math.round(value / node.multipleOf)) > 1e-9) {
        problems.push(problem("ui_value_bounds", path, "value is not a multipleOf step"));
      }
    }
    if (typeof value === "string") {
      if (isInteger(node.minLength) && value.length < node.minLength) {
        problems.push(problem("ui_value_length", path, "value is shorter than minLength"));
      }
      if (isInteger(node.maxLength) && value.length > node.maxLength) {
        problems.push(problem("ui_value_length", path, "value is longer than maxLength"));
      }
      if (typeof node.pattern === "string") {
        var pattern = null;
        try {
          pattern = new RegExp(node.pattern);
        } catch (error) {
          pattern = null;
        }
        if (pattern && !pattern.test(value)) {
          problems.push(problem("ui_value_pattern", path, "value does not match the pattern"));
        }
      }
      if (typeof node.format === "string" && FORMATS.indexOf(node.format) !== -1 &&
          !formatOk(node.format, value)) {
        problems.push(problem("ui_value_format", path,
          "value does not match format " + node.format));
      }
    }
    if (Array.isArray(value)) {
      if (isInteger(node.minItems) && value.length < node.minItems) {
        problems.push(problem("ui_value_length", path, "array is shorter than minItems"));
      }
      if (isInteger(node.maxItems) && value.length > node.maxItems) {
        problems.push(problem("ui_value_length", path, "array is longer than maxItems"));
      }
      if (node.uniqueItems === true) {
        var seen = {};
        value.forEach(function (item) {
          var key = JSON.stringify(canonical(item));
          if (hasOwn(seen, key)) {
            problems.push(problem("ui_value_enum", path, "array items must be unique"));
          }
          seen[key] = true;
        });
      }
      if (isObject(node.items)) {
        value.forEach(function (item, index) {
          checkScalar(node.items, path + "[" + index + "]", item, problems);
        });
      }
    }
  }

  /** Validate + default the user's values against the form's subset schema. */
  function validateValues(form, values) {
    var problems = [];
    var schema = isObject(form) && isObject(form.form_schema) ? form.form_schema : null;
    if (!schema) {
      return { ok: false, code: "ui_form_malformed", problems: [
        problem("ui_form_malformed", "form_schema", "form_schema is missing"),
      ], values: {} };
    }
    var input = isObject(values) ? values : {};
    var properties = isObject(schema.properties) ? schema.properties : {};
    Object.keys(input).forEach(function (name) {
      if (!hasOwn(properties, name)) {
        problems.push(problem("ui_value_unknown_field", name,
          "the form does not declare this field"));
      }
    });
    var required = Array.isArray(schema.required) ? schema.required : [];
    var out = {};
    Object.keys(properties).forEach(function (name) {
      var node = properties[name];
      if (!isObject(node)) {
        problems.push(problem("ui_form_malformed", "form_schema.properties." + name,
          "field schema must be an object"));
        return;
      }
      var raw = input[name];
      var present = hasOwn(input, name) && raw !== undefined && raw !== null && raw !== "";
      if (!present) {
        if (required.indexOf(name) !== -1) {
          problems.push(problem("ui_value_required", name, "a required value is missing"));
        } else if (hasOwn(node, "default")) {
          out[name] = node.default;
        }
        return;
      }
      checkScalar(node, name, raw, problems);
      out[name] = raw;
    });
    return {
      ok: problems.length === 0,
      code: problems.length ? problems[0].code : null,
      problems: problems,
      values: out,
    };
  }

  function setPath(target, path, value) {
    var segments = String(path).split(".");
    var cursor = target;
    for (var i = 0; i < segments.length - 1; i += 1) {
      var key = segments[i];
      if (!isObject(cursor[key])) cursor[key] = {};
      cursor = cursor[key];
    }
    cursor[segments[segments.length - 1]] = value;
    return target;
  }

  function bindingOf(form, name) {
    var schema = isObject(form) && isObject(form.form_schema) ? form.form_schema : {};
    var properties = isObject(schema.properties) ? schema.properties : {};
    var node = properties[name];
    if (!isObject(node) || !isObject(node["x-acp"])) return null;
    var id = node["x-acp"].binding;
    return typeof id === "string" && hasOwn(BINDING_BY_ID, id) ? BINDING_BY_ID[id] : null;
  }

  /** Build the preflight/submit body from form values, using only bindings.
   *
   * Returns the bound field table as well: the caller writes it into the
   * proposal provenance together with the descriptor/form digests.
   */
  function buildFormOutcome(form, values) {
    var validated = validateValues(form, values);
    if (!validated.ok) return Object.assign({}, validated, { body: null, fields: [] });
    var request = isObject(form.request) ? form.request : {};
    var body = isObject(request.template) ? JSON.parse(JSON.stringify(request.template)) : {};
    if (request.surface === "job_submit") body.workflow = request.workflow;
    var fields = [];
    Object.keys(validated.values).forEach(function (name) {
      var row = bindingOf(form, name);
      if (!row) return;
      setPath(body, row.request_body, validated.values[name]);
      fields.push({
        field: name,
        binding: row.binding,
        task_request: row.task_request,
        request_body: row.request_body,
        value_type: row.value_type,
        unit: row.unit,
        value: validated.values[name],
      });
    });
    return { ok: true, code: null, problems: [], values: validated.values, body: body, fields: fields };
  }

  /** Derive the render model (label/type/unit/default) from the form schema. */
  function fieldModel(form) {
    var schema = isObject(form) && isObject(form.form_schema) ? form.form_schema : {};
    var properties = isObject(schema.properties) ? schema.properties : {};
    var required = Array.isArray(schema.required) ? schema.required : [];
    return Object.keys(properties).map(function (name) {
      var node = isObject(properties[name]) ? properties[name] : {};
      var block = isObject(node["x-acp"]) ? node["x-acp"] : {};
      var row = typeof block.binding === "string" ? BINDING_BY_ID[block.binding] : null;
      var kind = Array.isArray(node.type) ? node.type[0] : node.type;
      return {
        name: name,
        title: typeof node.title === "string" ? node.title : name,
        description: typeof node.description === "string" ? node.description : "",
        type: kind === "integer" || kind === "number" ? "number" : (kind || "string"),
        schema_type: kind || "string",
        widget: typeof block.widget === "string" ? block.widget : "text",
        enum: Array.isArray(node.enum) ? node.enum.slice() : null,
        default: hasOwn(node, "default") ? node["default"] : null,
        minimum: typeof node.minimum === "number" ? node.minimum : null,
        maximum: typeof node.maximum === "number" ? node.maximum : null,
        required: required.indexOf(name) !== -1,
        binding: row ? row.binding : null,
        task_request: row ? row.task_request : null,
        unit: row ? row.unit : null,
        unit_quantity: row && row.unit ? UNITS[row.unit] || null : null,
      };
    });
  }

  return {
    SUBSET_VERSION: SUBSET_VERSION,
    SUBSET_MAJOR: SUBSET_MAJOR,
    FORM_SCHEMA: FORM_SCHEMA,
    FORMS_SCHEMA: FORMS_SCHEMA,
    ALLOWED_KEYWORDS: ALLOWED_KEYWORDS,
    ANNOTATIONS: ANNOTATIONS,
    REJECTED_KEYWORDS: REJECTED_KEYWORDS,
    TYPE_NAMES: TYPE_NAMES,
    FORMATS: FORMATS,
    WIDGETS: WIDGETS,
    UNITS: UNITS,
    BINDINGS: BINDINGS,
    ERROR_CODES: ERROR_CODES,
    subsetTable: subsetTable,
    bindingTable: bindingTable,
    tablesMatch: tablesMatch,
    validateFormDocument: validateFormDocument,
    validateValues: validateValues,
    buildFormOutcome: buildFormOutcome,
    fieldModel: fieldModel,
  };
})();
