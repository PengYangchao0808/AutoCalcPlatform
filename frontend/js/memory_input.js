(function (global) {
  "use strict";

  var UNIT_ALIASES = { M: "MB", G: "GB", T: "TB" };
  var VALID_UNITS = { MB: true, GB: true, TB: true };

  function normalizeUnit(unit) {
    var value = String(unit || "GB").trim().toUpperCase();
    value = UNIT_ALIASES[value] || value;
    return VALID_UNITS[value] ? value : "GB";
  }

  function parse(value, defaultUnit) {
    if (value === null || value === undefined) return null;
    var text = String(value).trim().replace(/\s+/g, "");
    var match = text.match(/^((?:\d+(?:\.\d*)?|\.\d+))(TB|GB|MB|T|G|M)?$/i);
    if (!match) return null;
    var amount = Number(match[1]);
    if (!Number.isFinite(amount) || amount <= 0) return null;
    return {
      amount: match[1],
      unit: normalizeUnit(match[2] || defaultUnit),
    };
  }

  function normalize(value, defaultUnit) {
    var parsed = parse(value, defaultUnit || "GB");
    return parsed ? parsed.amount + parsed.unit : null;
  }

  function format(inputId, unitId) {
    var input = document.getElementById(inputId);
    var unit = document.getElementById(unitId);
    if (!input) return null;
    return normalize(input.value, unit ? unit.value : "GB");
  }

  function read(inputId, unitId) {
    var input = document.getElementById(inputId);
    var value = document.getElementById(unitId);
    if (!input) return null;
    input.setCustomValidity("");
    if (!input.checkValidity()) {
      input.reportValidity();
      return null;
    }
    var normalized = normalize(input.value, value ? value.value : "GB");
    if (!normalized) {
      input.reportValidity();
      return null;
    }
    return normalized;
  }

  function set(inputId, unitId, value) {
    var input = document.getElementById(inputId);
    var unit = document.getElementById(unitId);
    if (!input) return;
    // Older task records may have a bare number. Treat it as GB, consistent
    // with the new default, and emit a unit-suffixed value on the next save.
    var parsed = parse(value, "GB");
    if (!parsed) {
      input.value = "";
      if (unit) unit.value = "GB";
      return;
    }
    input.value = parsed.amount;
    if (unit) unit.value = parsed.unit;
  }

  global.ACPMemoryInput = {
    format: format,
    normalize: normalize,
    read: read,
    set: set,
  };
})(window);
