// ── Mechanism Builder: reaction-coordinate plan assembly ────────────────
// Reuses the existing measurement machinery (2/3/4-atom click selection):
// each measurement row becomes a CoordinateSpec; the user picks start/end
// targets and a role (drive / freeze / monitor) before submit.

function currentMechanismAtoms() {
  // The mechanism module owns reaction-input binding. Do not infer a role
  // from the generic structure preview or from insertion order here.
  var reactant = reactionDefinition && reactionDefinition.reactant ? reactionDefinition.reactant : null;
  if (reactant && reactant.xyz) {
    return parseXyzAtoms(reactant.xyz);
  }
  return currentAtoms();
}

function createReactionDefinitionState() {
  return {
    reactant: null,
    product: null,
    tsGuess: null,
    locked: false,
    lockData: null,
    previewData: null,
    configHash: "",
    draftCreated: false,
    selectedCandidate: null,
    previewHash: "",
    showCandidatePicker: false,
    lockedPayload: null,
    editMode: false,   // manual bond edit mode (reactant-view atom picking)
    manualEdits: [],   // [{atoms:[i,j], type:'form'|'break'}] — 0-based reactant indices
    allowZeroChanges: false, // manual mode: "no bond changes in this reaction" ack
  };
}

function ensureReactionDefinitionState() {
  if (!reactionDefinition) reactionDefinition = createReactionDefinitionState();
  return reactionDefinition;
}

function getStructureAtomLabels(structure) {
  var lines;
  var countLine;
  var atomCount;
  var out;
  var i;
  var seg;
  if (!structure) return [];
  if (structure.xyz) {
    return parseXyzAtoms(structure.xyz).map(function(atom) { return atom.elem || "?"; });
  }
  if (structure.molfile) {
    lines = structure.molfile.split("\n");
    countLine = lines[3] || "";
    atomCount = parseInt(countLine.slice(0, 3), 10);
    if (!isNaN(atomCount) && atomCount > 0) {
      out = [];
      for (i = 4; i < 4 + atomCount && i < lines.length; i++) {
        seg = lines[i] || "";
        out.push((seg.slice(31, 34).trim() || "?").replace(/\s+/g, ""));
      }
      return out;
    }
  }
  return [];
}

function formatStructureAtom(structure, atomIndex) {
  var labels = getStructureAtomLabels(structure);
  var elem = labels[atomIndex] || "?";
  return String(atomIndex) + " (" + elem + ")";
}

function formatAtomList(structure, atoms) {
  return (atoms || []).map(function(idx) { return formatStructureAtom(structure, idx); }).join(", ");
}

function formatBondChangePair(change, reactantStructure, productStructure) {
  var reactants = (change.reactant_atoms || []).map(function(idx) { return formatStructureAtom(reactantStructure, idx); });
  var products = Array.isArray(change.product_atoms)
    ? change.product_atoms.map(function(idx) { return formatStructureAtom(productStructure, idx); })
    : [];
  // Space-aware manual entries carry only ONE side's pair — render exactly
  // the side that is set (product-side entries show the product atom pair).
  if (reactants.length && products.length) {
    return reactants.join(" – ") + " → " + products.join(" – ");
  }
  if (products.length) return products.join(" – ");
  return reactants.join(" – ");
}

function formatPreviewMappingStatus(status) {
  return t("mech.changes.mapping." + (status || "unique"));
}

// ── Manual bond edit mode (P2/P3: space-aware manual bond definition) ──
// Users pick atom pairs in either side's 3D scene: breaking bonds on the
// reactant view (default type "break"), forming bonds on the product view
// (default type "form" — the product conformation sits closer to the TS,
// mirroring S2 chemistry). Entries are side-keyed so reactant:1-5 and
// product:1-5 coexist; they replace auto bond_changes sharing the same pair
// in the same space and are confirmed as manual_bond_changes carrying
// EITHER reactant_atoms OR product_atoms.

function manualPairKey(atoms, side) {
  var a = Number(atoms[0]);
  var b = Number(atoms[1]);
  var space = side === "product" ? "product" : "reactant";
  return space + ":" + (a < b ? a + "-" + b : b + "-" + a);
}

function hasManualBondEdits(state) {
  return !!(state && Array.isArray(state.manualEdits) && state.manualEdits.length);
}

// Pure manual bond-editing mode: the preview response carries
// manual_mode === true (backend did no auto mapping, returns empty
// bond_changes / candidates). All bond changes come from user clicks.
function isManualBondMode(state) {
  return !!(state && state.previewData && state.previewData.manual_mode === true);
}

// Manual-mode display list: manual entries ONLY — never auto/mapping data.
function getManualBondChangeEntries(state) {
  var edits = state && Array.isArray(state.manualEdits) ? state.manualEdits : [];
  return edits.map(function(edit) { return buildManualChangeEntry(state, edit); });
}

function manualEditDistance(state, edit) {
  var structure = edit.side === "product" ? state.product : state.reactant;
  if (!structure || !structure.xyz) return null;
  var atoms = parseXyzAtoms(structure.xyz);
  var a = atoms[edit.atoms[0]];
  var b = atoms[edit.atoms[1]];
  if (!a || !b) return null;
  var dx = a.x - b.x;
  var dy = a.y - b.y;
  var dz = a.z - b.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

// Shape a manual edit takes when merged into the display list: the entry
// lives in ONE coordinate space (side "reactant" → reactant_atoms set,
// side "product" → product_atoms set). Distances are computed client-side
// from that side's coordinates; bond orders stay unknown.
function buildManualChangeEntry(state, edit) {
  var space = edit.side === "product" ? "product" : "reactant";
  return {
    change_type: edit.type,
    reactant_atoms: space === "reactant" ? [edit.atoms[0], edit.atoms[1]] : null,
    product_atoms: space === "product" ? [edit.atoms[0], edit.atoms[1]] : null,
    confidence: null,
    manual: true,
    bond_order_before: null,
    bond_order_after: null,
    distance_before: space === "reactant" ? manualEditDistance(state, edit) : null,
    distance_after: space === "product" ? manualEditDistance(state, edit) : null,
  };
}

// Effective (merged) bond-change list: auto entries whose pair (in either
// space) is overridden by a manual edit of the SAME side are replaced by the
// manual one; manual pairs absent from the auto list are appended.
function getEffectiveBondChanges(state) {
  var preview = state.previewData;
  var auto = preview && Array.isArray(preview.bond_changes) ? preview.bond_changes : [];
  var manual = Array.isArray(state.manualEdits) ? state.manualEdits : [];
  if (!manual.length) return auto;
  var manualByKey = {};
  manual.forEach(function(edit) {
    if (Array.isArray(edit.atoms) && edit.atoms.length >= 2) manualByKey[manualPairKey(edit.atoms, edit.side)] = edit;
  });
  var consumedManualKeys = {};
  var out = [];
  auto.forEach(function(change) {
    var rSide = change.reactant_atoms;
    if (Array.isArray(rSide) && rSide.length >= 2) {
      var rKey = manualPairKey(rSide, "reactant");
      if (manualByKey[rKey]) {
        consumedManualKeys[rKey] = true;
        out.push(buildManualChangeEntry(state, manualByKey[rKey]));
        return;
      }
    }
    var pSide = change.product_atoms;
    if (Array.isArray(pSide) && pSide.length >= 2) {
      var pKey = manualPairKey(pSide, "product");
      if (manualByKey[pKey]) {
        consumedManualKeys[pKey] = true;
        out.push(buildManualChangeEntry(state, manualByKey[pKey]));
        return;
      }
    }
    out.push(change);
  });
  manual.forEach(function(edit) {
    var key = manualPairKey(edit.atoms, edit.side);
    if (!consumedManualKeys[key]) out.push(buildManualChangeEntry(state, edit));
  });
  return out;
}

// Space-aware payload: each entry carries EITHER reactant_atoms (side
// "reactant") OR product_atoms (side "product") plus its change_type.
function buildManualBondChangesPayload(state) {
  if (!hasManualBondEdits(state)) return null;
  return state.manualEdits.map(function(edit) {
    var entry = { change_type: edit.type };
    if (edit.side === "product") entry.product_atoms = [edit.atoms[0], edit.atoms[1]];
    else entry.reactant_atoms = [edit.atoms[0], edit.atoms[1]];
    return entry;
  });
}

function toggleReactionEditMode() {
  var state = ensureReactionDefinitionState();
  if (state.locked || !state.previewData) return;
  if (!state.editMode) {
    // Space-aware editing: the reactant view defines breaking bonds, the
    // product view forming ones — no view is forced, the reactant/product
    // toggle stays usable while editing.
    state.editMode = true;
  } else {
    state.editMode = false;
    reactionManualPick = null;
  }
  updateReactionEditControls();
  renderReactionChanges3D();
}

function clearReactionManualEdits() {
  var state = ensureReactionDefinitionState();
  if (state.locked) return;
  state.manualEdits = [];
  reactionManualPick = null;
  renderReactionChangesPanel();
}

function addReactionManualEdit(state, atomIndexA, atomIndexB, side) {
  if (!Array.isArray(state.manualEdits)) state.manualEdits = [];
  var space = side === "product" ? "product" : "reactant";
  // Side defaults mirror the chemistry: the bond exists in the picked
  // structure, so reactant picks default to "break" and product picks to
  // "form" (cycling still reaches the other type per entry).
  var defaultType = space === "product" ? "form" : "break";
  var key = manualPairKey([atomIndexA, atomIndexB], space);
  for (var i = 0; i < state.manualEdits.length; i++) {
    if (manualPairKey(state.manualEdits[i].atoms, state.manualEdits[i].side) === key) {
      // Re-picking an existing pair on the SAME side resets it to that
      // side's default type; the same numeric pair on the OTHER side has a
      // distinct key and creates its own entry.
      state.manualEdits[i].type = defaultType;
      state.manualEdits[i].side = space;
      return;
    }
  }
  state.manualEdits.push({ atoms: [atomIndexA, atomIndexB], type: defaultType, side: space });
}

// form → break → removed, cycled by clicking a manual pair's cylinder. The
// entry's side is preserved through the cycle.
function cycleReactionManualEdit(atoms, side) {
  var state = ensureReactionDefinitionState();
  if (!state.editMode || state.locked) return;
  var key = manualPairKey(atoms, side);
  for (var i = 0; i < state.manualEdits.length; i++) {
    if (manualPairKey(state.manualEdits[i].atoms, state.manualEdits[i].side) === key) {
      reactionManualCycleAt = Date.now();
      if (state.manualEdits[i].type === "form") state.manualEdits[i].type = "break";
      else state.manualEdits.splice(i, 1);
      reactionPreserveViewNext = true;
      renderReactionChangesPanel();
      return;
    }
  }
}

// Two-click pair flow on the ACTIVE side's model (mirrors the SR-mode
// precedent but keeps fully separate state). The current view mode becomes
// the new entry's side: reactant view → breaking bonds (red), product view →
// forming bonds (green).
function handleReactionEditAtomClick(atom) {
  var state = ensureReactionDefinitionState();
  if (!state.editMode || state.locked) return;
  if (typeof atom.index !== "number") return;
  // Ignore atom picks for a short window after a cylinder cycle click so the
  // two interactions cannot fire on the same spot of overlapping geometry.
  if (Date.now() - reactionManualCycleAt < 300) return;
  if (reactionManualPick === null) {
    reactionManualPick = atom.index;
  } else if (reactionManualPick === atom.index) {
    reactionManualPick = null; // clicking the locked atom again cancels
  } else {
    addReactionManualEdit(state, reactionManualPick, atom.index, reactionChangesViewMode);
    reactionManualPick = null;
  }
  reactionPreserveViewNext = true;
  renderReactionChangesPanel();
}

function handleReactionAtomHoverStart(atom) {
  var state = ensureReactionDefinitionState();
  if (!state.editMode || !reactionViewer || reactionViewer.isDisposed()) return;
  if (!atom || typeof atom.index !== "number") return;
  if (reactionHoverShape) {
    reactionViewer.removeShape(reactionHoverShape);
    reactionHoverShape = null;
  }
  reactionHoverShape = reactionViewer.addSphere({
    center: { x: atom.x, y: atom.y, z: atom.z },
    radius: 0.42,
    color: "#f1c40f",
    opacity: 0.35,
  });
  reactionViewer.render();
}

function handleReactionAtomHoverEnd() {
  if (!reactionViewer || reactionViewer.isDisposed()) return;
  if (reactionHoverShape) {
    reactionViewer.removeShape(reactionHoverShape);
    reactionHoverShape = null;
  }
  reactionViewer.render();
}

// T11: shape-spec callbacks (addCylinder hover/click) pass through the scene
// instance verbatim — unlike the model click/hover wrappers they do NOT die
// with the scene generation. Each render captures its model token + instance
// here so the closure goes inert on clear / wizard close / replace / dispose
// (pinned by module_tests/reaction_viewer.test.js).
function reactionSceneGuard(instance, modelToken) {
  return function () {
    if (!instance || instance !== reactionViewer || instance.isDisposed()) return false;
    if (!modelToken || modelToken !== reactionModel || !instance.getModelCount()) return false;
    return true;
  };
}

// Toolbar/banner/hint state for edit mode + manual edits. Runs on every
// reaction-panel render so it stays in sync with i18n switches too.
function updateReactionEditControls() {
  var state = ensureReactionDefinitionState();
  var manualActive = hasManualBondEdits(state);
  var manualMode = isManualBondMode(state);
  var editBtn = document.getElementById("mech-edit-mapping");
  if (editBtn) {
    editBtn.disabled = !!state.locked || !state.previewData;
    editBtn.classList.toggle("edit-active", !!state.editMode);
    // In pure manual mode editing is the primary flow: the toggle flips
    // between "editing" (default, amber active state) and browsing.
    if (manualMode) {
      editBtn.textContent = t(state.editMode ? "mech.changes.manual_browse_exit" : "mech.changes.manual_enter_edit");
    } else {
      editBtn.textContent = t(state.editMode ? "mech.changes.edit_mapping_exit" : "mech.changes.edit_mapping");
    }
  }
  var clearBtn = document.getElementById("mech-clear-manual");
  if (clearBtn) {
    clearBtn.style.display = manualActive ? "" : "none";
    clearBtn.disabled = !!state.locked;
  }
  var bannerEl = document.getElementById("mech-manual-banner");
  if (bannerEl) {
    bannerEl.style.display = manualActive ? "" : "none";
    if (manualActive) {
      bannerEl.textContent = manualMode
        ? t("mech.changes.manual_count_banner", { count: state.manualEdits.length })
        : t("mech.changes.custom_mapping_banner", { count: state.manualEdits.length });
    }
  }
  var manualNoteEl = document.getElementById("mech-manual-mode-note");
  if (manualNoteEl) {
    var showManualNote = manualMode && !state.locked;
    manualNoteEl.style.display = showManualNote ? "" : "none";
    if (showManualNote) manualNoteEl.textContent = t("mech.changes.manual_mode_banner");
  }
  var hintEl = document.getElementById("mech-edit-hint");
  if (hintEl) {
    // Space-aware picking works on BOTH views — the hint follows the active
    // side so users know what a pair click defines there.
    var showHint = !!state.editMode && !state.locked;
    hintEl.style.display = showHint ? "" : "none";
    if (showHint) {
      if (reactionManualPick !== null) {
        hintEl.textContent = t("mech.changes.edit_pick_second", {
          atom: formatStructureAtom(reactionChangesViewMode === "product" ? state.product : state.reactant, reactionManualPick),
        });
      } else {
        hintEl.textContent = t(reactionChangesViewMode === "product" ? "mech.changes.edit_hint_product" : "mech.changes.edit_hint_reactant");
      }
    }
  }
}

function getMechanismStructurePayload(ns, chargeVal, multVal) {
  if (!ns) return null;
  var p;
  // P1 index-alignment fix: prefer xyz_text over smiles so backend
  // bond-change indices line up 1:1 with the coordinates rendered in the
  // 3D reaction viewer (re-embedding SMILES on the server may reorder atoms).
  if (ns.xyz) {
    p = { source_type: "xyz_text", source: ns.xyz };
  } else if (ns.source_type === "smiles" && ns.smiles) {
    p = { source_type: "smiles", source: ns.smiles };
  } else if (ns.normalized_path) {
    p = { source_type: "structure_asset", asset_id: ns.asset_id, source: ns.normalized_path };
  } else if (ns.smiles) {
    p = { source_type: "smiles", source: ns.smiles };
  } else {
    return null;
  }
  p.charge = ns.charge !== undefined && ns.charge !== null ? ns.charge : chargeVal;
  p.multiplicity = ns.multiplicity !== undefined && ns.multiplicity !== null ? ns.multiplicity : multVal;
  return p;
}

function renderReactionChangesPanel() {
  var emptyEl = document.getElementById("mech-reaction-empty");
  var bodyEl = document.getElementById("mech-reaction-body");
  if (!emptyEl || !bodyEl) return;
  var state = ensureReactionDefinitionState();
  var preview = state.previewData;
  if (!preview) {
    emptyEl.style.display = "block";
    bodyEl.innerHTML = "";
    renderReactionChanges3D();
    return;
  }
  emptyEl.style.display = "none";
  var manualMode = isManualBondMode(state);
  var manualActive = hasManualBondEdits(state);
  // Manual mode list = manual entries ONLY (auto/mapping data never shown).
  var bondChanges = manualMode ? getManualBondChangeEntries(state) : getEffectiveBondChanges(state);
  var html = '';
  html += '<div class="mech-actions" style="margin:0 0 8px 0;">';
  if (manualMode) {
    // Manual-mode badge replaces the (meaningless) auto mapping-status one.
    html += '<span class="badge waiting_review">' + escapeHtml(t('mech.changes.manual_mode_badge')) + '</span>';
  } else {
    html += '<span class="badge ' + (preview.status === 'ok' ? 'completed' : 'waiting_review') + '">' + escapeHtml(t('mech.changes.mapping_status', { status: formatPreviewMappingStatus(preview.mapping_status) })) + '</span>';
    if (manualActive) {
      html += '<span class="badge waiting_review">' + escapeHtml(t('mech.changes.custom_mapping_banner', { count: state.manualEdits.length })) + '</span>';
    }
    if (preview.status === 'confirmation_required' && !manualActive) {
      html += '<span class="mech-inline-note">' + escapeHtml(t('mech.changes.confirmation_required')) + '</span>';
    }
  }
  html += '</div>';
  if (bondChanges.length) {
    var listHtml = '';
    listHtml += '<div class="mech-change-list">';
    bondChanges.forEach(function(change) {
      var isManual = !!change.manual;
      var confidence = Number(change.confidence || 0);
      var cls = confidence >= 0.8 ? 'high' : (confidence >= 0.5 ? 'mid' : 'low');
      var beforeDist = change.distance_before !== undefined && change.distance_before !== null ? Number(change.distance_before).toFixed(2) : '--';
      var afterDist = change.distance_after !== undefined && change.distance_after !== null ? Number(change.distance_after).toFixed(2) : '--';
      listHtml += '<div class="mech-change-row' + (isManual ? ' manual-pair' : '') + '">';
      listHtml += '<div class="mech-change-top">';
      listHtml += '<span class="badge ' + (change.change_type === 'form' || change.change_type === 'order_up' ? 'completed' : change.change_type === 'break' ? 'failed' : 'queued') + '">' + escapeHtml(t('mech.bc.' + change.change_type)) + '</span>';
      listHtml += '<span class="mech-change-atompair">' + escapeHtml(t('mech.changes.atom_pair', { pair: formatBondChangePair(change, state.reactant, state.product) })) + '</span>';
      if (isManual) {
        listHtml += '<span class="mech-manual-flag">' + escapeHtml(t('mech.changes.manual_badge')) + '</span>';
      } else {
        listHtml += '<span class="mech-confidence ' + cls + '">' + escapeHtml(t('common.percent', { value: Math.round(confidence * 100) })) + '</span>';
      }
      listHtml += '</div>';
      listHtml += '<div class="mech-change-meta">' + escapeHtml(t('mech.changes.change_meta', {
        before: change.bond_order_before != null ? change.bond_order_before : '--',
        after: change.bond_order_after != null ? change.bond_order_after : '--',
        distBefore: beforeDist,
        distAfter: afterDist,
      })) + '</div>';
      listHtml += '</div>';
    });
    listHtml += '</div>';
    // P1: the 3D viewer is the primary surface for bond changes; keep the
    // full text list available but collapsed inside <details>.
    html += '<details class="mech-changes-details" id="mech-changes-details">';
    html += '<summary>' + escapeHtml(t('mech.changes.details_toggle')) + ' (' + bondChanges.length + ')</summary>';
    html += listHtml;
    html += '</details>';
  }
  // Manual mode skips ALL mapping-derived blocks: candidate picker,
  // unmatched atoms and warnings — bond changes come only from clicks.
  if (!manualMode && Array.isArray(preview.candidates) && preview.candidates.length && (preview.status === 'confirmation_required' || state.showCandidatePicker)) {
    html += '<div class="mech-panel-block" style="margin-top:8px;margin-bottom:0;">';
    html += '<h4>' + escapeHtml(t('mech.changes.candidates')) + '</h4>';
    html += '<div class="mech-candidate-list">';
    preview.candidates.forEach(function(candidate) {
      var checked = Number(state.selectedCandidate) === Number(candidate.index) || (state.selectedCandidate == null && Number(preview.selected_candidate) === Number(candidate.index));
      html += '<div class="mech-candidate-row">';
      html += '<label><input type="radio" name="mech-candidate" value="' + escapeHtml(candidate.index) + '"' + (checked ? ' checked' : '') + (manualActive ? ' disabled' : '') + '> ';
      html += '<strong>' + escapeHtml(t('mech.changes.candidate_label', { index: candidate.index })) + '</strong>';
      html += '<span class="mech-candidate-meta">' + escapeHtml(t('mech.changes.candidate_meta', { confidence: t('common.percent', { value: Math.round(Number(candidate.confidence || 0) * 100) }), count: candidate.symmetric_alternatives || 0 })) + '</span>';
      html += '</label>';
      html += '</div>';
    });
    html += '</div>';
    if (manualActive) {
      html += '<div class="mech-candidate-disabled-note">' + escapeHtml(t('mech.changes.custom_mapping_note')) + '</div>';
    }
    html += '</div>';
  }
  if (!manualMode && ((preview.unmatched_reactant_atoms || []).length || (preview.unmatched_product_atoms || []).length)) {
    html += '<div class="mech-panel-block" style="margin-top:8px;margin-bottom:0;">';
    html += '<h4>' + escapeHtml(t('mech.changes.unmatched_title')) + '</h4>';
    html += '<div class="mech-unmatched-list">';
    if ((preview.unmatched_reactant_atoms || []).length) {
      html += '<div class="mech-unmatched-row">' + escapeHtml(t('mech.changes.unmatched_reactant', { atoms: formatAtomList(state.reactant, preview.unmatched_reactant_atoms) })) + '</div>';
    }
    if ((preview.unmatched_product_atoms || []).length) {
      html += '<div class="mech-unmatched-row">' + escapeHtml(t('mech.changes.unmatched_product', { atoms: formatAtomList(state.product, preview.unmatched_product_atoms) })) + '</div>';
    }
    html += '<div class="mech-inline-note">' + escapeHtml(t('mech.changes.unmatched_hint')) + '</div>';
    html += '</div></div>';
  }
  if (!manualMode && Array.isArray(preview.warnings) && preview.warnings.length) {
    html += '<div class="mech-panel-block" style="margin-top:8px;margin-bottom:0;">';
    html += '<h4>' + escapeHtml(t('mech.changes.warnings')) + '</h4>';
    html += '<div class="mech-warning-list">' + preview.warnings.map(function(msg) {
      return '<div class="mech-warning-row">' + escapeHtml(msg) + '</div>';
    }).join('') + '</div></div>';
  }
  // Preserve the <details> list expansion across real-time manual-edit
  // re-renders so the text list never snaps shut mid-editing.
  var prevDetails = document.getElementById('mech-changes-details');
  var detailsWasOpen = !!(prevDetails && prevDetails.open);
  bodyEl.innerHTML = html;
  var freshDetails = document.getElementById('mech-changes-details');
  if (freshDetails && detailsWasOpen) freshDetails.open = true;
  renderReactionChanges3D();
}

// ── Reaction-changes 3D viewer (P1: 3D-view-first bond change overlays) ──────
// Renders the active-side structure (reactant or product) with breaking bonds
// as red solid cylinders + endpoint spheres and forming bonds as green dashed
// cylinders + endpoint spheres; unmatched atoms get gray spheres. The full
// text list stays available inside the collapsed <details> block above.
// All indices come straight from previewData and are 0-based row indices into
// the XYZ text, matching 3Dmol {index: N} selectors directly.

function initReactionViewer() {
  if (reactionViewer) return;
  var container = document.getElementById("mech-reaction-3d");
  if (!container) return;
  // Bounded scene instance (restricted API v2), never a raw 3Dmol handle.
  // scene.create returns null when 3Dmol is missing/failed —
  // renderReactionChanges3D guards on the falsy handle after this call.
  reactionViewer = ACPVisualization.scene.create({ container: container, options: { backgroundColor: "0x0e1419", antialias: true } });
}

function clearReactionViewerContents() {
  if (!reactionViewer) return;
  reactionViewer.clearScene();
  reactionModel = null;
  reactionHoverShape = null;
}

function updateReactionViewToggleButtons() {
  document.querySelectorAll(".mech-view-toggle[data-view-mode]").forEach(function(btn) {
    btn.classList.toggle("active", btn.getAttribute("data-view-mode") === reactionChangesViewMode);
  });
}

function setReactionChangesViewMode(mode) {
  if (mode !== "reactant" && mode !== "product") return;
  if (reactionChangesViewMode === mode) return;
  reactionChangesViewMode = mode;
  // A half-finished two-click pick lives in the previous view's atom space —
  // cancel it rather than mixing indices across spaces.
  reactionManualPick = null;
  updateReactionViewToggleButtons();
  renderReactionChanges3D();
}

// Collect the atom indices (active view side) for one stats-bar group:
// "break" = break + order_down, "form" = form + order_up, "unmatched" =
// the active side's unmatched_* list. Used for zoomTo on chip click.
function reactionStatGroupIndices(group) {
  var state = ensureReactionDefinitionState();
  var preview = state.previewData;
  if (!preview) return [];
  var manualMode = isManualBondMode(state);
  var isProduct = reactionChangesViewMode === "product";
  var out = [];
  var seen = {};
  function pushIdx(idx) {
    if (typeof idx === "number" && !seen[idx]) { seen[idx] = true; out.push(idx); }
  }
  if (group === "unmatched") {
    // Unmatched atoms are a mapping artifact — manual mode never has them.
    if (manualMode) return out;
    var list = isProduct ? preview.unmatched_product_atoms : preview.unmatched_reactant_atoms;
    (Array.isArray(list) ? list : []).forEach(pushIdx);
    return out;
  }
  var changes = manualMode ? getManualBondChangeEntries(state) : getEffectiveBondChanges(state);
  changes.forEach(function(change) {
    var isForm = change.change_type === "form" || change.change_type === "order_up";
    if (group === "form" && !isForm) return;
    if (group === "break" && isForm) return;
    // Space-aware: indices must reference the ACTIVE view's model. Manual
    // entries carry their pair in exactly one space (the other is null), so
    // entries defined on the other side are skipped here.
    var side = isProduct ? change.product_atoms : change.reactant_atoms;
    (Array.isArray(side) ? side : []).forEach(pushIdx);
  });
  return out;
}

function renderReactionStatsBar() {
  var state = ensureReactionDefinitionState();
  var preview = state.previewData;
  var barEl = document.getElementById("mech-change-stats");
  if (!barEl) return;
  if (!preview) { barEl.innerHTML = ""; return; }
  var manualMode = isManualBondMode(state);
  var bondChanges = manualMode ? getManualBondChangeEntries(state) : getEffectiveBondChanges(state);
  var breakCount = 0;
  var formCount = 0;
  bondChanges.forEach(function(change) {
    if (change.change_type === "form" || change.change_type === "order_up") formCount++;
    else breakCount++;
  });
  var isProduct = reactionChangesViewMode === "product";
  var unmatchedCount = manualMode ? 0 : ((isProduct ? preview.unmatched_product_atoms : preview.unmatched_reactant_atoms) || []).length;
  function chip(group, cls, label, count) {
    return '<button type="button" class="mech-stat-chip ' + cls + (count > 0 ? '' : ' zero') + '" data-stat-group="' + group + '"' + (count > 0 ? '' : ' disabled') + '>' + escapeHtml(label) + '</button>';
  }
  var html = "";
  html += chip("break", "break", t("mech.changes.stats_break", { count: breakCount }), breakCount);
  html += chip("form", "form", t("mech.changes.stats_form", { count: formCount }), formCount);
  // Manual mode shows ONLY manual counts — no unmapped/mapping chips.
  if (!manualMode) {
    html += chip("unmatched", "unmapped", t("mech.changes.stats_unmatched", { count: unmatchedCount }), unmatchedCount);
  }
  barEl.innerHTML = html;
  barEl.querySelectorAll("[data-stat-group]").forEach(function(btn) {
    btn.addEventListener("click", function() {
      if (!reactionViewer) return;
      var indices = reactionStatGroupIndices(this.getAttribute("data-stat-group"));
      if (!indices.length) {
        // The group's entries live in the other coordinate space — switch
        // the view (the re-render is synchronous) and zoom on that model.
        setReactionChangesViewMode(reactionChangesViewMode === "product" ? "reactant" : "product");
        indices = reactionStatGroupIndices(this.getAttribute("data-stat-group"));
      }
      if (!indices.length) return;
      reactionViewer.zoomTo({ index: indices }, 500);
      reactionViewer.render();
    });
  });
}

function renderReactionChanges3D() {
  var state = ensureReactionDefinitionState();
  var preview = state.previewData;
  var manualMode = isManualBondMode(state);
  var wrapEl = document.getElementById("mech-reaction-3d-wrap");
  if (!wrapEl) return;
  var productNoteEl = document.getElementById("mech-product-view-note");
  if (productNoteEl) productNoteEl.style.display = "none";
  // Manual mode keeps the viewer ALWAYS visible — the structure plus user
  // overlays is the editing surface, even before any pair is defined.
  var hasChanges = manualMode || (!!preview && (
    (Array.isArray(preview.bond_changes) && preview.bond_changes.length) ||
    (Array.isArray(preview.unmatched_reactant_atoms) && preview.unmatched_reactant_atoms.length) ||
    (Array.isArray(preview.unmatched_product_atoms) && preview.unmatched_product_atoms.length) ||
    // Manual edits keep the viewer (and its clear/banner controls) alive even
    // if a refreshed preview reports no auto changes at all.
    (Array.isArray(state.manualEdits) && state.manualEdits.length)
  ));
  updateReactionViewToggleButtons();
  if (!hasChanges) {
    wrapEl.style.display = "none";
    var hiddenNoteEl = document.getElementById("mech-manual-mode-note");
    if (hiddenNoteEl) hiddenNoteEl.style.display = "none";
    clearReactionViewerContents();
    renderReactionStatsBar();
    return;
  }
  wrapEl.style.display = "";
  var isProduct = reactionChangesViewMode === "product";
  var structure = isProduct ? state.product : state.reactant;
  var xyzText = structure && structure.xyz ? structure.xyz : "";
  var empty3dEl = document.getElementById("mech-reaction-3d-empty");
  updateReactionEditControls();
  if (!xyzText) {
    // Active side has no embedded coordinates — show the hint and clear any
    // stale overlays rendered for the other side.
    if (empty3dEl) empty3dEl.style.display = "grid";
    clearReactionViewerContents();
    renderReactionStatsBar();
    return;
  }
  if (empty3dEl) empty3dEl.style.display = "none";
  initReactionViewer();
  if (!reactionViewer) return;
  // Keep the camera steady across edit-mode re-renders (pair adds / cycles);
  // full zoomTo still applies on view switches and fresh previews.
  var preservedView = null;
  if (reactionPreserveViewNext) {
    reactionPreserveViewNext = false;
    preservedView = reactionViewer.getView();
  }
  clearReactionViewerContents();
  var atoms = parseXyzAtoms(xyzText);
  reactionModel = reactionViewer.addModel(xyzText, "xyz");
  var live = reactionSceneGuard(reactionViewer, reactionModel);
  // De-emphasize the molecular backbone so reaction-site overlays stand out.
  reactionViewer.setStyle({}, { stick: { radius: 0.14, opacity: 0.4 }, sphere: { scale: 0.26, opacity: 0.4 } });
  var manualEdits = Array.isArray(state.manualEdits) ? state.manualEdits : [];
  var manualOverrides = {};
  manualEdits.forEach(function(edit) {
    if (Array.isArray(edit.atoms) && edit.atoms.length >= 2) manualOverrides[manualPairKey(edit.atoms, edit.side)] = edit;
  });
  var autoChanges = Array.isArray(preview.bond_changes) ? preview.bond_changes : [];
  // Manual mode guard: NEVER run auto (mapping-derived) overlay code, even
  // if the payload were to carry bond_changes — user clicks are the only
  // source of change visuals.
  if (!manualMode) autoChanges.forEach(function(change) {
    // Manual edits replace auto entries sharing the same pair in the SAME
    // space on BOTH views, so a superseded auto overlay never lingers on
    // either side.
    var rSide = change.reactant_atoms;
    if (Array.isArray(rSide) && rSide.length >= 2 && manualOverrides[manualPairKey(rSide, "reactant")]) return;
    var pSideSkip = change.product_atoms;
    if (Array.isArray(pSideSkip) && pSideSkip.length >= 2 && manualOverrides[manualPairKey(pSideSkip, "product")]) return;
    // reactant side uses reactant_atoms; product side uses product_atoms and
    // skips changes whose product_atoms is null (no counterpart on that side).
    var side = isProduct ? change.product_atoms : change.reactant_atoms;
    if (!Array.isArray(side) || side.length < 2) return;
    var atomA = atoms[side[0]];
    var atomB = atoms[side[1]];
    if (!atomA || !atomB) return;
    var isForm = change.change_type === "form" || change.change_type === "order_up";
    var color = isForm ? "#2ecc71" : "#e74c3c";
    var opacity = 0.5 + 0.5 * Number(change.confidence || 0);
    var mid = {
      x: (atomA.x + atomB.x) / 2,
      y: (atomA.y + atomB.y) / 2,
      z: (atomA.z + atomB.z) / 2,
    };
    var tooltip = t("mech.changes.change_meta", {
      before: change.bond_order_before != null ? change.bond_order_before : "--",
      after: change.bond_order_after != null ? change.bond_order_after : "--",
      distBefore: change.distance_before != null && change.distance_before !== undefined ? Number(change.distance_before).toFixed(2) : "--",
      distAfter: change.distance_after != null && change.distance_after !== undefined ? Number(change.distance_after).toFixed(2) : "--",
    });
    var hoverLabel = null;
    reactionViewer.addCylinder({
      start: { x: atomA.x, y: atomA.y, z: atomA.z },
      end: { x: atomB.x, y: atomB.y, z: atomB.z },
      radius: 0.18,
      color: color,
      dashed: isForm,
      dashLength: 0.15,
      gapLength: 0.1,
      fromCap: 1,
      toCap: 1,
      opacity: opacity,
      hoverable: true,
      hover_callback: function() {
        if (!live()) return;
        if (hoverLabel) reactionViewer.removeLabel(hoverLabel);
        hoverLabel = reactionViewer.addLabel(tooltip, { position: mid, fontSize: 12, fontColor: "#fff", backgroundColor: "rgba(0,0,0,0.75)" });
        reactionViewer.render();
      },
      unhover_callback: function() {
        if (!live()) return;
        if (hoverLabel) {
          reactionViewer.removeLabel(hoverLabel);
          hoverLabel = null;
          reactionViewer.render();
        }
      },
    });
    reactionViewer.addSphere({ center: { x: atomA.x, y: atomA.y, z: atomA.z }, radius: 0.28, color: color, opacity: 0.95 });
    reactionViewer.addSphere({ center: { x: atomB.x, y: atomB.y, z: atomB.z }, radius: 0.28, color: color, opacity: 0.95 });
    // Restore full-opacity backbone at the reaction site.
    reactionViewer.addStyle({ index: [side[0], side[1]] }, { stick: { radius: 0.14, opacity: 1.0 }, sphere: { scale: 0.26, opacity: 1.0 } });
  });
  // Manual pair overlays are space-aware: each view draws only the entries
  // defined in its own coordinate space (reactant view ↔ side "reactant",
  // product view ↔ side "product"; coordinates come from the active model's
  // parsed XYZ above — product space for the product view).
  var viewSide = isProduct ? "product" : "reactant";
  manualEdits.forEach(function(edit) {
    if ((edit.side === "product" ? "product" : "reactant") !== viewSide) return;
    var atomA = atoms[edit.atoms[0]];
    var atomB = atoms[edit.atoms[1]];
    if (!atomA || !atomB) return;
    var isForm = edit.type === "form";
    var color = isForm ? "#2ecc71" : "#e74c3c";
    var mid = {
      x: (atomA.x + atomB.x) / 2,
      y: (atomA.y + atomB.y) / 2,
      z: (atomA.z + atomB.z) / 2,
    };
    var cycleTip = t("mech.changes.manual_cycle_hint", { type: t("mech.bc." + (isForm ? "form" : "break")) });
    var editable = state.editMode && !state.locked;
    var manualHoverLabel = null;
    reactionViewer.addCylinder({
      start: { x: atomA.x, y: atomA.y, z: atomA.z },
      end: { x: atomB.x, y: atomB.y, z: atomB.z },
      radius: 0.18,
      color: color,
      dashed: isForm,
      dashLength: 0.15,
      gapLength: 0.1,
      fromCap: 1,
      toCap: 1,
      opacity: 0.95,
      hoverable: true,
      hover_callback: function() {
        if (!live()) return;
        if (manualHoverLabel) reactionViewer.removeLabel(manualHoverLabel);
        manualHoverLabel = reactionViewer.addLabel(cycleTip, { position: mid, fontSize: 12, fontColor: "#fff", backgroundColor: "rgba(0,0,0,0.78)" });
        reactionViewer.render();
      },
      unhover_callback: function() {
        if (!live()) return;
        if (manualHoverLabel) {
          reactionViewer.removeLabel(manualHoverLabel);
          manualHoverLabel = null;
          reactionViewer.render();
        }
      },
      clickable: editable,
      callback: editable ? function() {
        if (!live()) return;
        cycleReactionManualEdit(edit.atoms, edit.side);
      } : undefined,
    });
    reactionViewer.addSphere({ center: { x: atomA.x, y: atomA.y, z: atomA.z }, radius: 0.28, color: color, opacity: 0.95 });
    reactionViewer.addSphere({ center: { x: atomB.x, y: atomB.y, z: atomB.z }, radius: 0.28, color: color, opacity: 0.95 });
    reactionViewer.addStyle({ index: [edit.atoms[0], edit.atoms[1]] }, { stick: { radius: 0.14, opacity: 1.0 }, sphere: { scale: 0.26, opacity: 1.0 } });
  });
  // Unmatched-atom spheres are a mapping artifact — never drawn in manual
  // mode (mapping is disabled backend-side).
  if (!manualMode) {
    var unmatched = isProduct ? preview.unmatched_product_atoms : preview.unmatched_reactant_atoms;
    (Array.isArray(unmatched) ? unmatched : []).forEach(function(idx) {
      var atom = atoms[idx];
      if (!atom) return;
      reactionViewer.addSphere({ center: { x: atom.x, y: atom.y, z: atom.z }, radius: 0.3, color: "#95a5a6", opacity: 0.9 });
    });
  }
  // Manual mode product view while NOT editing: with no product-side entries
  // there is nothing to overlay — surface the structure-only note. In edit
  // mode (or once product-side entries exist) the product-side edit hint /
  // overlays take over instead.
  var hasProductSideEdits = manualEdits.some(function(edit) { return edit.side === "product"; });
  if (manualMode && isProduct && !state.editMode && !hasProductSideEdits && productNoteEl) {
    productNoteEl.style.display = "";
    productNoteEl.textContent = t("mech.changes.product_view_note");
  }
  // Edit-mode atom wiring: hover halo + two-click pair picking on the ACTIVE
  // side's model, plus the yellow lock marker for the picked atom (always in
  // the active view's coordinate space).
  if (state.editMode && !state.locked && reactionModel) {
    reactionModel.setClickable({}, true, handleReactionEditAtomClick);
    reactionModel.setHoverable({}, true, handleReactionAtomHoverStart, handleReactionAtomHoverEnd);
    if (reactionManualPick !== null && atoms[reactionManualPick]) {
      reactionViewer.addStyle({ index: reactionManualPick }, { sphere: { scale: 0.4, color: "yellow", opacity: 0.7 } });
    }
  }
  if (preservedView) {
    if (!reactionViewer.resize() || !reactionViewer.setView(preservedView)) frame3DViewer(reactionViewer);
  } else {
    reactionViewer.render();
    scheduleViewerFraming(document.getElementById("mech-reaction-3d"), reactionViewerFraming, function() { return reactionViewer; });
    renderReactionStatsBar();
    return;
  }
  reactionViewer.render();
  renderReactionStatsBar();
}

function renderReactionDefinitionPanel() {
  renderReactionChangesPanel();
}

function addCoordinateFromMeasurement(m) {
  var kind = m.type === "distance" ? "distance" : m.type === "angle" ? "angle" : "dihedral";
  var startVal = m.value !== undefined && m.value !== null ? m.value : 0;
  var spec = {
    id: "rc" + (mechCoordinatePlans.length + 1),
    kind: kind,
    atoms: m.atoms.slice(),
    role: "drive",
    start: Math.round(startVal * 100) / 100,
    end: Math.round(startVal * 100) / 100,
    source: "manual",
  };
  mechCoordinatePlans.push(spec);
  return spec;
}

function removeCoordinatePlan(index) {
  if (index >= 0 && index < mechCoordinatePlans.length) mechCoordinatePlans.splice(index, 1);
  renderMechanismPlanPanel();
}

function updateCoordinatePlan(index, field, value) {
  if (index < 0 || index >= mechCoordinatePlans.length) return;
  var spec = mechCoordinatePlans[index];
  if (field === "role") spec.role = value;
  else if (field === "start") spec.start = parseFloat(value);
  else if (field === "end") spec.end = parseFloat(value);
  renderMechanismPlanPanel();
}

function cloneMechanismCoordinateSpec(spec) {
  var cloned = {
    id: spec && spec.id ? String(spec.id) : "rc",
    kind: spec && spec.kind ? spec.kind : "distance",
    atoms: Array.isArray(spec && spec.atoms) ? spec.atoms.slice() : [],
    role: spec && spec.role ? spec.role : "drive",
    start: spec && spec.start !== undefined && spec.start !== null ? Number(spec.start) : null,
    end: spec && spec.end !== undefined && spec.end !== null ? Number(spec.end) : null,
  };
  if (spec && spec.source) cloned.source = spec.source;
  if (spec && spec.force_constant !== undefined && spec.force_constant !== null) {
    cloned.force_constant = Number(spec.force_constant);
  }
  return cloned;
}

function hasMechanismDriveCoordinate(specs) {
  return (specs || []).some(function(spec) {
    return !!(spec && spec.role === "drive" && Array.isArray(spec.atoms) && spec.atoms.length);
  });
}

function buildDirectTsCoordinatePlan() {
  var atoms = currentMechanismAtoms();
  if (!atoms || atoms.length < 2) return null;
  var dx = Number(atoms[0].x) - Number(atoms[1].x);
  var dy = Number(atoms[0].y) - Number(atoms[1].y);
  var dz = Number(atoms[0].z) - Number(atoms[1].z);
  var distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
  var target = Math.round((distance > 0 ? distance : 1.5) * 100) / 100;
  return {
    coordinates: [{
      id: "rc1",
      kind: "distance",
      atoms: [0, 1],
      role: "drive",
      start: target,
      end: target,
    }],
    points: 2,
    coupling: "synchronous",
    start_from: "reactant",
  };
}

function generateMechanismStudyId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function(ch) {
    var rand = Math.random() * 16 | 0;
    var value = ch === "x" ? rand : (rand & 0x3 | 0x8);
    return value.toString(16);
  });
}

function literalMechanismDefaultsForFidelity(fidelity) {
  return fidelity === "s4"
    ? { fidelity: "s4", scan_points: 25, irc_points: 40 }
    : { fidelity: "s3", scan_points: 21, irc_points: 30 };
}

function getCurrentMethodSchema() {
  var schemaId = wizardState && wizardState.workflow ? wizardState.workflow.schema_id : "";
  if (!schemaId) return null;
  return (methodCatalogCache.method_schemas || {})[schemaId] || null;
}

function getMethodProfileById(profileId) {
  if (!profileId) return null;
  var schema = getCurrentMethodSchema();
  var profiles = (schema && schema.profiles) || [];
  for (var i = 0; i < profiles.length; i++) {
    if (profiles[i].profile_id === profileId) return profiles[i];
  }
  return null;
}

// Profile titles are ALWAYS resolved from profile_id against the current
// catalog — never from the mutable wizardState.method.profile_label, which
// goes stale after edit-draft hydration restores a non-default profile
// (the "仅优化" bug). Language switches re-resolve via updateConfigCards.
function resolveCurrentProfile() {
  return getMethodProfileById(wizardState.method.profile_id);
}

function resolveCurrentProfileLabel() {
  if (wizardState.method.profile_id === "__custom__") {
    return t("modal.custom") || "Custom";
  }
  var profile = resolveCurrentProfile();
  if (!profile) return wizardState.method.profile_id || (t("modal.custom") || "Custom");
  return currentLang === "zh-CN"
    ? (profile.label_zh || profile.label)
    : profile.label;
}

function extractMechanismSettingsFromProfile(profile) {
  if (!profile || !profile.levels) return null;
  var scanLevel = profile.levels.scan || {};
  var ircLevel = profile.levels.irc || {};
  var settings = {};
  if (scanLevel.fidelity) settings.fidelity = scanLevel.fidelity;
  var scanPoints = parseInt(scanLevel.scan_points, 10);
  var ircPoints = parseInt(ircLevel.irc_points, 10);
  if (scanPoints >= 2) settings.scan_points = scanPoints;
  if (ircPoints >= 1) settings.irc_points = ircPoints;
  return Object.keys(settings).length ? settings : null;
}

function getMechanismDefaultsForFidelity(fidelity) {
  var target = fidelity || "s3";
  var schema = getCurrentMethodSchema();
  var profiles = (schema && schema.profiles) || [];
  for (var i = 0; i < profiles.length; i++) {
    var settings = extractMechanismSettingsFromProfile(profiles[i]);
    if (settings && settings.fidelity === target && settings.scan_points && settings.irc_points) {
      return settings;
    }
  }
  return literalMechanismDefaultsForFidelity(target);
}

function refreshMechanismPanelControls() {
  var fidelitySel = document.getElementById("mech-fidelity");
  if (fidelitySel) fidelitySel.value = mechFidelity || "s3";
  var scanPointsInput = document.getElementById("mech-scan-points");
  if (scanPointsInput) scanPointsInput.value = String(mechScanPoints >= 5 ? mechScanPoints : 21);
  var ircPointsInput = document.getElementById("mech-irc-points");
  if (ircPointsInput) ircPointsInput.value = String(mechIrcPoints >= 4 ? mechIrcPoints : 30);
}

function applyMechanismDefaultsForFidelity(fidelity) {
  var defaults = getMechanismDefaultsForFidelity(fidelity || mechFidelity || "s3");
  mechFidelity = defaults.fidelity || fidelity || mechFidelity || "s3";
  if (!mechScanPointsDirty) mechScanPoints = defaults.scan_points >= 5 ? defaults.scan_points : 21;
  if (!mechIrcPointsDirty) mechIrcPoints = defaults.irc_points >= 4 ? defaults.irc_points : 30;
  refreshMechanismPanelControls();
}

function syncMechanismPanelFromProfile(profile) {
  var fidelitySel = document.getElementById("mech-fidelity");
  var isPreset = !!(profile && profile.profile_id && profile.profile_id !== "__custom__");
  if (isPreset) {
    var settings = extractMechanismSettingsFromProfile(profile) || {};
    if (settings.fidelity) mechFidelity = settings.fidelity;
    if (!mechScanPointsDirty && settings.scan_points >= 5) mechScanPoints = settings.scan_points;
    if (!mechIrcPointsDirty && settings.irc_points >= 4) mechIrcPoints = settings.irc_points;
    if (fidelitySel) fidelitySel.disabled = true;
  } else {
    if (fidelitySel) fidelitySel.disabled = false;
  }
  refreshMechanismPanelControls();
}

function buildMechanismRoutes() {
  // Compile the current plan list into route dicts for JobSpec.input.routes.
  var strategy = mechStrategy || "guided-scan";
  var coordinates = mechCoordinatePlans.map(cloneMechanismCoordinateSpec);
  var suggestedPlan = reactionDefinition && reactionDefinition.previewData && reactionDefinition.previewData.suggested_plan
    ? reactionDefinition.previewData.suggested_plan
    : null;
  var plan = null;
  if (coordinates.length) {
    plan = {
      coordinates: coordinates,
      points: (suggestedPlan && suggestedPlan.points) || mechScanPoints || 21,
      coupling: (suggestedPlan && suggestedPlan.coupling) || "synchronous",
      start_from: (suggestedPlan && suggestedPlan.start_from) || (strategy === "rph-reverse" ? "product" : "reactant"),
    };
  } else if (strategy === "direct-ts") {
    plan = buildDirectTsCoordinatePlan();
  }
  if (!plan) return [];
  var route = {
    route_id: "route-1",
    coordinate_plan: plan,
    path_strategy: strategy,
    fidelity: mechFidelity || "s3",
  };
  if ((reactionDefinition && reactionDefinition.tsGuess) || mechTsGuessStructure) route.ts_guess_id = "ts_guess_01";
  return [route];
}

function renderMechanismPlanPanel() {
  var panel = document.getElementById("mech-plan-panel");
  if (!panel) return;
  if (!mechCoordinatePlans.length) {
    panel.innerHTML = '<div class="muted">' +
      (t("mech.no_coords") || "使用下方的测量工具点击原子构建反应坐标（距离 2 / 角度 3 / 二面角 4 个原子）") +
      "</div>";
    return;
  }
  var html = "";
  mechCoordinatePlans.forEach(function(spec, i) {
    var typeLabel = spec.kind === "distance" ? "距离" : spec.kind === "angle" ? "角度" : "二面角";
    var roleOpts = '<select data-idx="' + i + '" data-field="role">' +
      '<option value="drive"' + (spec.role === "drive" ? " selected" : "") + '>Drive</option>' +
      '<option value="freeze"' + (spec.role === "freeze" ? " selected" : "") + '>Freeze</option>' +
      '<option value="monitor"' + (spec.role === "monitor" ? " selected" : "") + '>Monitor</option>' +
      "</select>";
    var sourceChip = spec.source === "auto"
      ? '<span class="mech-auto-chip">' + escapeHtml(t("mech.plan.auto")) + '</span>'
      : '<span class="mech-manual-chip">' + escapeHtml(t("mech.plan.manual")) + '</span>';
    html += '<div class="measure-row">';
    html += '<span class="measure-type">' + spec.id + " " + typeLabel + " (" + spec.atoms.map(function(a){return a+1;}).join(",") + ") " + sourceChip + '</span>';
    html += '<span>start <input type="number" step="0.01" data-idx="' + i + '" data-field="start" value="' + spec.start + '" style="width:70px"></span>';
    html += '<span>end <input type="number" step="0.01" data-idx="' + i + '" data-field="end" value="' + spec.end + '" style="width:70px"></span>';
    html += roleOpts;
    html += '<button class="measure-del" data-idx="' + i + '">x</button>';
    html += "</div>";
  });
  panel.innerHTML = html;
  panel.querySelectorAll("input,select").forEach(function(el) {
    el.addEventListener("change", function() {
      updateCoordinatePlan(parseInt(this.getAttribute("data-idx"), 10), this.getAttribute("data-field"), this.value);
    });
  });
  panel.querySelectorAll(".measure-del").forEach(function(btn) {
    btn.addEventListener("click", function() {
      removeCoordinatePlan(parseInt(this.getAttribute("data-idx"), 10));
    });
  });
}

function hookMechanismBuilder(options) {
  // New workflow selection starts clean; draft hydration reuses the restored
  // model state while wiring controls and rebuilding its panels.
  if (!(options && options.preserveState)) {
    mechCoordinatePlans = [];
    mechStrategy = "guided-scan";
    mechFidelity = "s3";
    mechConformerMode = "censo-lite";
    mechMaxSteps = 3;
    mechPromotionPolicy = "all_confirmed";
    mechScanPoints = 21;
    mechIrcPoints = 30;
    mechScanPointsDirty = false;
    mechIrcPointsDirty = false;
    reactionDefinition = createReactionDefinitionState();
    reactionModel = null;
    reactionManualPick = null;
    reactionHoverShape = null;
    reactionPreserveViewNext = false;
    reactionManualCycleAt = 0;
  }
  var addBtn = document.getElementById("mech-add-coord");
  if (addBtn && !addBtn.dataset.hooked) {
    addBtn.dataset.hooked = "1";
    addBtn.addEventListener("click", function() {
      var store = window.ACPGeometryStore;
      var measures = store ? store.state.measurements : [];
      if (!measures.length) {
        window.alert(t("mech.measure_first") || "请先在 3D 模型中测量一段距离/角度/二面角");
        return;
      }
      // bridge the most recent measurement into a coordinate plan
      var last = measures[measures.length - 1];
      addCoordinateFromMeasurement(last);
      renderMechanismPlanPanel();
    });
  }
  var strategySel = document.getElementById("mech-strategy");
  if (strategySel && !strategySel.dataset.hooked) {
    strategySel.dataset.hooked = "1";
    strategySel.addEventListener("change", function() {
      mechStrategy = this.value;
      if (this.value === "rph-reverse") {
        if (!mechProductStructure) window.alert(t("mech.product_required") || "请在机理计算模块中提供产物结构");
      }
    });
  }
  if (strategySel) strategySel.value = mechStrategy;
  var fidelitySel = document.getElementById("mech-fidelity");
  if (fidelitySel && !fidelitySel.dataset.hooked) {
    fidelitySel.dataset.hooked = "1";
    fidelitySel.addEventListener("change", function() {
      mechFidelity = this.value;
      applyMechanismDefaultsForFidelity(mechFidelity);
    });
  }
  if (fidelitySel) fidelitySel.value = mechFidelity;
  var conformerSel = document.getElementById("mech-conformer-mode");
  if (conformerSel && !conformerSel.dataset.hooked) {
    conformerSel.dataset.hooked = "1";
    conformerSel.addEventListener("change", function() { mechConformerMode = this.value; });
  }
  if (conformerSel) conformerSel.value = mechConformerMode;
  var scanPointsInput = document.getElementById("mech-scan-points");
  if (scanPointsInput && !scanPointsInput.dataset.hooked) {
    scanPointsInput.dataset.hooked = "1";
    var syncMechScanPoints = function() {
      mechScanPointsDirty = true;
      var defaults = getMechanismDefaultsForFidelity(mechFidelity || "s3");
      var fallback = defaults.scan_points >= 5 ? defaults.scan_points : 21;
      var parsed = parseInt(this.value, 10);
      mechScanPoints = parsed >= 5 ? parsed : fallback;
      this.value = String(mechScanPoints);
    };
    scanPointsInput.addEventListener("input", syncMechScanPoints);
    scanPointsInput.addEventListener("change", syncMechScanPoints);
  }
  if (scanPointsInput) scanPointsInput.value = String(mechScanPoints);
  var ircPointsInput = document.getElementById("mech-irc-points");
  if (ircPointsInput && !ircPointsInput.dataset.hooked) {
    ircPointsInput.dataset.hooked = "1";
    var syncMechIrcPoints = function() {
      mechIrcPointsDirty = true;
      var defaults = getMechanismDefaultsForFidelity(mechFidelity || "s3");
      var fallback = defaults.irc_points >= 4 ? defaults.irc_points : 30;
      var parsed = parseInt(this.value, 10);
      mechIrcPoints = parsed >= 4 ? parsed : fallback;
      this.value = String(mechIrcPoints);
    };
    ircPointsInput.addEventListener("input", syncMechIrcPoints);
    ircPointsInput.addEventListener("change", syncMechIrcPoints);
  }
  if (ircPointsInput) ircPointsInput.value = String(mechIrcPoints);
  var maxStepsInput = document.getElementById("mech-max-steps");
  if (maxStepsInput && !maxStepsInput.dataset.hooked) {
    maxStepsInput.dataset.hooked = "1";
    var syncMechMaxSteps = function() {
      var parsed = parseInt(this.value, 10);
      mechMaxSteps = parsed >= 1 ? parsed : 3;
      this.value = String(mechMaxSteps);
    };
    maxStepsInput.addEventListener("input", syncMechMaxSteps);
    maxStepsInput.addEventListener("change", syncMechMaxSteps);
  }
  if (maxStepsInput) maxStepsInput.value = String(mechMaxSteps);
  var promotionSel = document.getElementById("mech-promotion-policy");
  if (promotionSel && !promotionSel.dataset.hooked) {
    promotionSel.dataset.hooked = "1";
    promotionSel.addEventListener("change", function() { mechPromotionPolicy = this.value; });
  }
  if (promotionSel) promotionSel.value = mechPromotionPolicy;
  var mechProfile = wizardState.method && wizardState.method.profile_id && wizardState.method.profile_id !== "__custom__"
    ? getMethodProfileById(wizardState.method.profile_id)
    : null;
  if (mechProfile) {
    syncMechanismPanelFromProfile(mechProfile);
  } else {
    applyMechanismDefaultsForFidelity(mechFidelity);
    syncMechanismPanelFromProfile(null);
  }
  // Reaction-changes 3D viewer toggle (P1): [reactant]/[product] view switch.
  document.querySelectorAll(".mech-view-toggle[data-view-mode]").forEach(function(btn) {
    if (!btn.dataset.hooked) {
      btn.dataset.hooked = "1";
      btn.addEventListener("click", function() {
        setReactionChangesViewMode(this.getAttribute("data-view-mode"));
      });
    }
  });
  // Manual bond edit mode (P2): edit toggle + clear-manual-edits buttons.
  var editMappingBtn = document.getElementById("mech-edit-mapping");
  if (editMappingBtn && !editMappingBtn.dataset.hooked) {
    editMappingBtn.dataset.hooked = "1";
    editMappingBtn.addEventListener("click", toggleReactionEditMode);
  }
  var clearManualBtn = document.getElementById("mech-clear-manual");
  if (clearManualBtn && !clearManualBtn.dataset.hooked) {
    clearManualBtn.dataset.hooked = "1";
    clearManualBtn.addEventListener("click", clearReactionManualEdits);
  }
  updateReactionViewToggleButtons();
  renderMechanismPlanPanel();
  renderReactionDefinitionPanel();
}

function parseXyzAtoms(xyzText) {
  var lines = xyzText.split("\n");
  var atoms = [];
  for (var i = 2; i < lines.length; i++) {
    var parts = lines[i].trim().split(/\s+/);
    if (parts.length >= 4) {
      atoms.push({ elem: parts[0], x: parseFloat(parts[1]), y: parseFloat(parts[2]), z: parseFloat(parts[3]), id: i - 2 });
    }
  }
  return atoms;
}

function renderMeasurementList() {
  const panel = document.getElementById("measure-list");
  const badge = document.getElementById("measure-count");
  var store = window.ACPGeometryStore;
  var measures = store ? store.state.measurements : [];
  if (badge) {
    badge.textContent = measures.length || "";
    badge.style.display = measures.length ? "" : "none";
  }
  if (!panel) return;
  if (!measures.length) {
    panel.innerHTML = '<span class="muted">' + escapeHtml(t("measure.empty")) + '</span>';
    return;
  }
  let html = "";
  measures.forEach(function(m) {
    const typeLabel = m.type === "distance" ? t("measure.distance") : m.type === "angle" ? t("measure.angle") : t("measure.dihedral");
    html += '<div class="measure-row">';
    html += '<span class="measure-type">' + escapeHtml(typeLabel) + '</span>';
    html += '<span class="measure-value">' + escapeHtml(formatMeasurementDisplay(m.type, m.value)) + '</span>';
    html += '<button class="measure-del" data-mid="' + escapeHtml(m.id) + '">x</button>';
    html += '</div>';
  });
  panel.innerHTML = html;
  panel.querySelectorAll(".measure-del").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var mid = this.getAttribute("data-mid");
      if (store) store.removeMeasurement(mid);
      renderMeasurements();
      renderMeasurementList();
      viewer.render();
    });
  });
}

// ----------------------------------------------------------------------
// Phase 6: remote node selector + status panel
// ----------------------------------------------------------------------

async function loadNodes() {
  const list = document.getElementById("sysres-node-list");
  if (!list) return;
  try {
    const data = await api("/nodes");
    nodesCache = data.nodes || [];
    renderNodePanel();
  } catch (e) {
    nodesCache = [];
    if (String(e.message).indexOf("not configured") >= 0) {
      list.innerHTML = '<div class="muted">' + escapeHtml(t("nodes.empty")) + '</div>';
    } else {
      list.innerHTML = '<div class="node-error">' + escapeHtml(t("nodes.load_error") + ": " + e.message) + '</div>';
    }
  }
}

function renderNodePanel() {
  const list = document.getElementById("sysres-node-list");
  if (!list) return;
  list.innerHTML = "";
  if (!nodesCache.length) {
    list.innerHTML = '<div class="muted">' + escapeHtml(t("nodes.empty")) + '</div>';
    return;
  }
  nodesCache.forEach(function(node) {
    const row = document.createElement("div");
    row.className = "node-row" + (node.status === "offline" ? " offline" : "");
    const dot = document.createElement("span");
    dot.className = "node-dot " + (node.status || "offline");
    const info = document.createElement("div");
    info.className = "node-info";
    const name = document.createElement("div");
    name.className = "node-name";
    name.textContent = (node.name || "--") + "  " + (node.host || "");
    const meta = document.createElement("div");
    meta.className = "node-meta";
    meta.textContent = t("nodes.status." + (node.status || "offline")) + " · " + (node.running_jobs || 0) + "/" + (node.max_jobs || 0) + " · DISK " + (node.disk_usage_pct || 0) + "%";
    info.appendChild(name);
    info.appendChild(meta);
    if (node.error) {
      const err = document.createElement("div");
      err.className = "node-error";
      err.textContent = node.error;
      info.appendChild(err);
    }
    const actions = document.createElement("div");
    actions.className = "node-actions";
    const ping = document.createElement("button");
    ping.className = "node-btn";
    ping.textContent = t("nodes.ping");
    ping.addEventListener("click", function(event) {
      event.stopPropagation();
      pingNode(node.name);
    });
    actions.appendChild(ping);
    row.appendChild(dot);
    row.appendChild(info);
    row.appendChild(actions);
    list.appendChild(row);
  });
}

async function pingNode(name) {
  try {
    const data = await api("/nodes/" + encodeURIComponent(name) + "/ping", { method: "POST" });
    const status = data.status || "offline";
    const msg = data.reachable ? t("nodes.status.online") : (data.error || t("nodes.status.offline"));
    window.alert((data.reachable ? "OK: " : "FAIL: ") + name + " - " + msg);
    await loadNodes();
  } catch (e) {
    window.alert("Ping failed: " + e.message);
  }
}

// ----------------------------------------------------------------------
// Submit-wizard compute-node selector (node-selection-at-submission T11).
// Data flow: wizard workflow/method/profile/tag changes (debounced 300 ms)
// → POST /api/v1/nodes/matching → render. The backend is the single
// authority on capability derivation — the frontend only renders the
// response (satisfies/missing_*/degraded/capability_state) and never
// re-implements the derivation table. T12 wires the submit payload
// (target_node/node_tags) off this control's state.
// ----------------------------------------------------------------------

var nodeMatchingState = {
  response: null,      // last applied NodeMatchingResponse (null = not loaded)
  error: null,         // last refresh error message (null when healthy)
  selectedTags: [],    // checked tag chips (AND semantics, enforced server-side)
  timer: null,         // debounce timer
  seq: 0,              // race token — only the newest response may render
  userTouched: false,  // true once the user changes the dropdown (a stage
                       // preselect applied programmatically never sets this)
  preselectSourceId: "", // source job id the current mirror was derived from
  preselectNode: "",   // stage mirror: source job's explicit spec.target_node
  preselectHost: ""    // stage mirror: source job's host (display only)
};

function scheduleNodeMatchingRefresh() {
  if (nodeMatchingState.timer) clearTimeout(nodeMatchingState.timer);
  nodeMatchingState.timer = setTimeout(function() {
    nodeMatchingState.timer = null;
    refreshNodeMatching();
  }, 300);
}

function buildNodeMatchingRequest() {
  var stages = (wizardState.method && wizardState.method.stages) || {};
  var levels = {};
  Object.keys(stages).forEach(function(lid) {
    var st = stages[lid];
    if (!st || !st.engine || st._disabled === true) return;
    var copy = JSON.parse(JSON.stringify(st));
    delete copy._disabled;
    levels[lid] = copy;
  });
  var method = {
    schema_id: (wizardState.workflow && wizardState.workflow.schema_id) || "",
    profile_id: (wizardState.method && wizardState.method.profile_id) || "",
    levels: levels
  };
  // Confsearch protocol/policy hoisting mirrors the submit payload (the
  // backend resolves protocol from method.protocol, then profile_id), so
  // the preview judges the job that would actually be submitted.
  var csStage = levels.confsearch || {};
  if (csStage.protocol) method.protocol = csStage.protocol;
  if (csStage.refinement_policy) method.refinement_policy = csStage.refinement_policy;
  if (!method.protocol && method.profile_id && method.profile_id !== "__custom__") {
    method.protocol = method.profile_id;
  }
  return {
    workflow: (wizardState.workflow && wizardState.workflow.id) || "",
    method: method,
    node_tags: nodeMatchingState.selectedTags.slice()
  };
}

async function refreshNodeMatching() {
  var modal = document.getElementById("job-modal");
  if (!modal || modal.style.display === "none") return;
  var reqBody = buildNodeMatchingRequest();
  if (!reqBody.workflow) return;
  var seq = ++nodeMatchingState.seq;
  try {
    var data = await api("/nodes/matching", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reqBody)
    });
    if (seq !== nodeMatchingState.seq) return;
    nodeMatchingState.response = data;
    nodeMatchingState.error = null;
  } catch (e) {
    if (seq !== nodeMatchingState.seq) return;
    nodeMatchingState.response = null;
    nodeMatchingState.error = (e && e.message) || String(e);
  }
  renderNodeSelector();
}

function nodeOptionStatusGlyph(status) {
  if (status === "online") return "🟢";
  if (status === "degraded") return "🟡";
  return "🔴";
}

function nodeOptionLabel(node) {
  var status = node.status || "offline";
  var parts = [
    nodeOptionStatusGlyph(status),
    node.name,
    (node.running_jobs || 0) + "/" + (node.max_jobs || 0)
  ];
  if (node.queue) parts.push(node.queue);
  parts.push(t("nodes.status." + status));
  var label = parts.join("  ");
  if (node.degraded) label += "  [" + t("nodes.badge.degraded") + "]";
  if (node.capability_state === "probe-inferred") label += "  [" + t("nodes.badge.probe_inferred") + "]";
  else if (node.capability_state === "unknown") label += "  [" + t("nodes.badge.unknown") + "]";
  if (node.declared_ok === false) label += "  [" + t("nodes.badge.mismatch") + "]";
  return label;
}

function nodeDisabledReason(node) {
  var reasons = [];
  if ((node.status || "offline") === "offline") reasons.push(t("nodes.reason.offline"));
  if (node.missing_software && node.missing_software.length) {
    reasons.push(t("nodes.reason.missing_software", { software: node.missing_software.join(", ") }));
  }
  if (node.missing_tags && node.missing_tags.length) {
    reasons.push(t("nodes.reason.missing_tags", { tags: node.missing_tags.join(", ") }));
  }
  if (!reasons.length && node.reasons && node.reasons.length) {
    reasons.push(node.reasons.join("; "));
  }
  return reasons.join("; ");
}

function appendNodeBadge(container, show, text, infoStyle) {
  if (!show) return;
  var badge = document.createElement("span");
  badge.className = "node-badge" + (infoStyle ? " node-badge-info" : "");
  badge.textContent = text;
  container.appendChild(badge);
}

function nodeMatchingTagUniverse(nodes) {
  var seen = {};
  var tags = [];
  (nodes || []).forEach(function(node) {
    (node.tags || []).forEach(function(tag) {
      if (!seen[tag]) { seen[tag] = true; tags.push(tag); }
    });
  });
  return tags.sort();
}

function renderNodeTagChips(nodes) {
  var box = document.getElementById("modal-node-tags");
  if (!box) return;
  var tags = nodeMatchingTagUniverse(nodes);
  nodeMatchingState.selectedTags = nodeMatchingState.selectedTags.filter(function(tag) {
    return tags.indexOf(tag) >= 0;
  });
  if (!tags.length) { box.style.display = "none"; box.innerHTML = ""; return; }
  box.style.display = "flex";
  box.innerHTML = "";
  tags.forEach(function(tag) {
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "node-tag-chip" + (nodeMatchingState.selectedTags.indexOf(tag) >= 0 ? " active" : "");
    chip.textContent = tag;
    chip.setAttribute("data-node-tag", tag);
    chip.setAttribute("aria-pressed", nodeMatchingState.selectedTags.indexOf(tag) >= 0 ? "true" : "false");
    chip.title = t("nodes.tags_hint");
    chip.addEventListener("click", function() {
      var idx = nodeMatchingState.selectedTags.indexOf(tag);
      if (idx >= 0) nodeMatchingState.selectedTags.splice(idx, 1);
      else nodeMatchingState.selectedTags.push(tag);
      scheduleNodeMatchingRefresh();
      renderNodeTagChips((nodeMatchingState.response && nodeMatchingState.response.nodes) || []);
    });
    box.appendChild(chip);
  });
}

function renderNodeSelectDetail() {
  var sel = document.getElementById("modal-node-select");
  var detail = document.getElementById("modal-node-detail");
  if (!sel || !detail) return;
  var nodes = (nodeMatchingState.response && nodeMatchingState.response.nodes) || [];
  var node = null;
  for (var i = 0; i < nodes.length; i++) {
    if (nodes[i].name === sel.value) { node = nodes[i]; break; }
  }
  if (!node && nodeMatchingState.preselectNode && !nodeMatchingState.userTouched) {
    // Stage mirror whose pin is not selectable for the child workflow
    // (offline/incapable/not in the matching list): still show where the
    // chain came from; the server validates the inherited pin at creation.
    detail.style.display = "flex";
    detail.innerHTML = "";
    var pin = document.createElement("span");
    pin.className = "node-meta";
    pin.textContent = t("nodes.source_pin") + ": " + nodeMatchingState.preselectNode +
      (nodeMatchingState.preselectHost ? "  " + nodeMatchingState.preselectHost : "");
    detail.appendChild(pin);
    return;
  }
  if (!node) { detail.style.display = "none"; detail.innerHTML = ""; return; }
  detail.style.display = "flex";
  detail.innerHTML = "";
  var dot = document.createElement("span");
  dot.className = "node-dot " + (node.status || "offline");
  detail.appendChild(dot);
  var meta = document.createElement("span");
  meta.className = "node-meta";
  meta.textContent = (node.host || "") + "  " + (node.running_jobs || 0) + "/" + (node.max_jobs || 0) +
    "  DISK " + (node.disk_usage_pct || 0) + "%" + (node.queue ? "  " + node.queue : "");
  detail.appendChild(meta);
  appendNodeBadge(detail, node.degraded, t("nodes.badge.degraded"), false);
  appendNodeBadge(detail, node.capability_state === "probe-inferred", t("nodes.badge.probe_inferred"), true);
  appendNodeBadge(detail, node.capability_state === "unknown", t("nodes.badge.unknown"), true);
  appendNodeBadge(detail, node.declared_ok === false, t("nodes.badge.mismatch"), false);
}

function updateNodeSelectorHints() {
  var sel = document.getElementById("modal-node-select");
  var hint = document.getElementById("modal-node-auto-hint");
  var note = document.getElementById("modal-node-note");
  if (!sel || !hint || !note) return;
  var resp = nodeMatchingState.response;
  var required = (resp && resp.required_software) || [];
  var showAutoHint = sel.value === "" && !!resp && resp.local_satisfies === false && required.length > 0;
  hint.style.display = showAutoHint ? "block" : "none";
  if (showAutoHint) hint.textContent = t("nodes.auto_remote_hint");
  // m11: the current pick may be a disabled option kept visible by
  // renderNodeSelector (a refresh made the picked node unsatisfying).
  // Surface that loudly instead of silently falling back to auto.
  var selectedOpt = sel.selectedOptions && sel.selectedOptions[0];
  var selectedUnavailable = !!(sel.value && selectedOpt && selectedOpt.disabled);
  if (nodeMatchingState.error) {
    note.style.display = "block";
    note.classList.add("node-note-error");
    note.textContent = t("nodes.matching_error") + ": " + nodeMatchingState.error;
  } else if (selectedUnavailable) {
    note.style.display = "block";
    note.classList.add("node-note-error");
    note.textContent = t("nodes.selected_unavailable");
  } else if (resp && !(resp.nodes || []).length) {
    note.style.display = "block";
    note.classList.remove("node-note-error");
    note.textContent = t("nodes.empty");
  } else {
    note.style.display = "none";
  }
}

function renderNodeSelector() {
  var sel = document.getElementById("modal-node-select");
  if (!sel) return;
  var resp = nodeMatchingState.response;
  var nodes = (resp && resp.nodes) || [];
  var previousValue = sel.value;

  sel.innerHTML = "";
  var autoOpt = document.createElement("option");
  autoOpt.value = "";
  autoOpt.textContent = t("nodes.opt.auto");
  sel.appendChild(autoOpt);
  var localOpt = document.createElement("option");
  localOpt.value = "local";
  localOpt.textContent = t("nodes.opt.local");
  localOpt.setAttribute("data-node-local", "true");
  sel.appendChild(localOpt);

  nodes.forEach(function(node) {
    var opt = document.createElement("option");
    opt.value = node.name;
    opt.textContent = nodeOptionLabel(node);
    opt.setAttribute("data-node-name", node.name);
    opt.setAttribute("data-node-satisfies", node.satisfies ? "true" : "false");
    if (!node.satisfies) {
      opt.disabled = true;
      opt.title = nodeDisabledReason(node);
    }
    sel.appendChild(opt);
  });

  var stillThere = false;
  Array.prototype.forEach.call(sel.options, function(opt) {
    if (opt.value === previousValue) stillThere = true;
  });
  sel.value = stillThere ? previousValue : "";

  // T12 stage mirror: an untouched selector follows the source job's
  // explicit pin (a manual pick always wins over the mirror).
  if (!nodeMatchingState.userTouched && nodeMatchingState.preselectNode) {
    applyNodePreselect(sel, nodeMatchingState.preselectNode);
  }

  // m11: a refresh may have disabled the option the user already picked
  // (node went offline / lost a required capability). KEEP the pick
  // visible — a disabled option can stay the selected value; the warning
  // renders in updateNodeSelectorHints, the submit still carries the pick,
  // and the server's coded 400 (localized via formatSubmitError) is the
  // backstop. Never silently fall back to auto here.
  updateNodeSelectorHints();
  renderNodeTagChips(nodes);
  renderNodeSelectDetail();
}

function onNodeSelectChange() {
  // The change event only fires on user interaction — a stage preselect
  // applied via .value stays "untouched" so the stage payload omits it and
  // the server-side source inheritance (T6) remains the authority.
  nodeMatchingState.userTouched = true;
  updateNodeSelectorHints();
  renderNodeSelectDetail();
}

function resetNodeSelector() {
  nodeMatchingState.selectedTags = [];
  nodeMatchingState.error = null;
  nodeMatchingState.userTouched = false;
  nodeMatchingState.preselectSourceId = "";
  nodeMatchingState.preselectNode = "";
  nodeMatchingState.preselectHost = "";
  renderNodeSelector();
}

// ----------------------------------------------------------------------
// T12: submit payload wiring (target_node / node_tags), stage source-node
// preselect, and the job-detail continue override. The selector's "local"
// value maps directly to target_node="local" — a first-class local pin in
// the v1 model (LOCAL_NODE_NAME), never capability-checked server-side.
// Auto ("") omits target_node entirely so the server default applies.
// ----------------------------------------------------------------------

function applyNodeSelectionToBody(body, opts) {
  opts = opts || {};
  var sel = document.getElementById("modal-node-select");
  // requireUserChoice (stage chains): only an explicit user pick is sent.
  // The server already inherits the source job's explicit pin when the
  // request omits the field, so the preselect mirror stays a UX hint.
  if (sel && sel.value && (!opts.requireUserChoice || nodeMatchingState.userTouched)) {
    body.target_node = sel.value;
  }
  if (nodeMatchingState.selectedTags.length) {
    body.node_tags = nodeMatchingState.selectedTags.slice();
  }
  return body;
}

// Select the pinned node when its option exists and is enabled; a pin that
// is missing or capability-disabled for the child workflow stays visible as
// a detail-line note instead (the server validates the inherited value).
function applyNodePreselect(sel, pinned) {
  var found = null;
  Array.prototype.forEach.call(sel.options, function(opt) {
    if (opt.value === pinned) found = opt;
  });
  if (found && !found.disabled) {
    sel.value = pinned;
    return true;
  }
  return false;
}

// Stage wizards mirror the source job's explicit node pin as a dropdown
// preselection. Only explicit spec.target_node values are mirrored — an
// auto source leaves the child auto. Idempotent per source id: repeated
// configureStageSourcePanel passes for the same source never clobber a
// manual override made in between. userTouched semantics (m10):
//   * A NEW source id is a new decision point — a manual pick made under
//     the previous source is superseded by the new source's pin, so
//     userTouched resets SYNCHRONOUSLY when the id changes (pre-fetch).
//   * A manual pick made DURING the fetch window sets userTouched again;
//     when the response arrives the pin is only recorded (source-pin
//     detail line) — never applied, and userTouched is never reset, so
//     the in-flight pick always wins.
async function preselectNodeFromSourceJob(sourceJobId) {
  sourceJobId = String(sourceJobId || "").trim();
  if (sourceJobId === nodeMatchingState.preselectSourceId) return;
  nodeMatchingState.preselectSourceId = sourceJobId;
  nodeMatchingState.preselectNode = "";
  nodeMatchingState.preselectHost = "";
  var sel = document.getElementById("modal-node-select");
  if (!sourceJobId) {
    // Source cleared / left a stage workflow: drop the programmatic mirror.
    // A node the user explicitly picked survives the switch.
    if (!nodeMatchingState.userTouched && sel) sel.value = "";
    renderNodeSelector();
    return;
  }
  // New decision point (see header): the new source's pin may override a
  // manual pick made under the previous source. Resetting here — before
  // the fetch — is what protects a pick made during the fetch window.
  nodeMatchingState.userTouched = false;
  try {
    var data = await api("/jobs/" + encodeURIComponent(sourceJobId));
    // Stale-response guard: the source field moved on while fetching.
    if (nodeMatchingState.preselectSourceId !== sourceJobId) return;
    var srcSpec = (data && data.spec) || {};
    if (srcSpec.target_node) {
      // Always record the pin for the source-pin detail line. Whether it
      // is APPLIED is decided by renderNodeSelector(): applyNodePreselect
      // only fires for an untouched selector, so a manual pick made while
      // this fetch was in flight keeps the dropdown value. Never reset
      // userTouched here — that was the m10 clobber race.
      nodeMatchingState.preselectNode = String(srcSpec.target_node);
      nodeMatchingState.preselectHost = String((data && data.host) || "");
    } else if (!nodeMatchingState.userTouched && sel) {
      sel.value = "";
    }
  } catch (e) {
    // Unknown/unreachable source id — leave auto; the submit surfaces the
    // server's source-resolution error.
  }
  renderNodeSelector();
}

// D12: submission-time target errors carry a machine-readable code in the
// 400 body ({code, missing_software, missing_tags}); map codes to localized
// text instead of surfacing the server's English message.
function translateNodeError(err) {
  var msg = (err && err.message) || String(err || "");
  var parsed = null;
  try {
    var candidate = JSON.parse(msg);
    if (candidate && typeof candidate === "object" && candidate.code) parsed = candidate;
  } catch (e) { /* not a coded error body */ }
  if (!parsed) return null;
  var key = "nodes.error." + parsed.code;
  var translated = t(key);
  if (translated === key) translated = t("nodes.error.execution_target_error");
  var extras = [];
  if (parsed.code === "no_capable_node" && parsed.remote_nodes_configured !== undefined &&
      parsed.remote_nodes_configured !== null) {
    if (parsed.local_missing_software && parsed.local_missing_software.length) {
      extras.push(t("nodes.reason.local_missing_software", {
        software: parsed.local_missing_software.join(", ")
      }));
    }
    if (parsed.remote_nodes_configured === false) {
      extras.push(t("nodes.reason.no_remote_nodes"));
    } else if (parsed.missing_software && parsed.missing_software.length) {
      extras.push(t("nodes.reason.remote_missing_software", {
        software: parsed.missing_software.join(", ")
      }));
    }
  } else if (parsed.missing_software && parsed.missing_software.length) {
    extras.push(t("nodes.reason.missing_software", { software: parsed.missing_software.join(", ") }));
  }
  if (parsed.missing_tags && parsed.missing_tags.length) {
    extras.push(t("nodes.reason.missing_tags", { tags: parsed.missing_tags.join(", ") }));
  }
  return extras.length ? translated + " (" + extras.join("; ") + ")" : translated;
}

function formatSubmitError(err) {
  return translateNodeError(err) || (err && err.message) || String(err);
}

// ----------------------------------------------------------------------
// Local QC backends panel (GET /api/backends + /api/v1/software/discovery).
// Version badge + multi-install warning are display-only; the discovery
// endpoint is best-effort and degrades silently when unavailable.
// ----------------------------------------------------------------------

async function loadBackends() {
  const list = document.getElementById("sysres-backend-list");
  if (!list) return;
  try {
    const data = await apiRequest("/api/backends");
    backendsCache = data.backends || [];
  } catch (e) {
    backendsCache = [];
    list.innerHTML = '<div class="node-error">' + escapeHtml(t("backend.load_error")) + '</div>';
    return;
  }
  try {
    const ddata = await api("/software/discovery");
    if (ddata) {
      const map = {};
      (ddata.software || []).forEach(function(sw) {
        if (sw && sw.name) map[String(sw.name)] = sw;
      });
      softwareDiscoveryCache = map;
    }
  } catch (e) { /* silent: version badges still render without discovery */ }
  renderBackendPanel();
}

function renderBackendPanel() {
  const list = document.getElementById("sysres-backend-list");
  if (!list) return;
  list.innerHTML = "";
  if (!backendsCache.length) {
    list.innerHTML = '<div class="muted">' + escapeHtml(t("backend.empty")) + '</div>';
    return;
  }
  backendsCache.forEach(function(b) {
    const name = String(b.name || "");
    const discovery = softwareDiscoveryCache[name] || null;
    const remoteNodes = Array.isArray(b.remote_nodes) ? b.remote_nodes : [];
    const isRemote = b.remote_available === true && remoteNodes.length > 0;
    const effective = b.available || isRemote;
    const version = String(b.version || b.remote_version || (discovery && discovery.version) || "").trim();
    const resolvedPath = String(b.path || b.remote_path || (discovery && discovery.resolved) || "").trim();

    const row = document.createElement("div");
    row.className = "node-row" + (effective ? "" : " offline");

    const dot = document.createElement("span");
    dot.className = "node-dot " + (effective ? "online" : "offline");

    const info = document.createElement("div");
    info.className = "node-info";

    const nameEl = document.createElement("div");
    nameEl.className = "node-name";
    nameEl.textContent = name || "--";
    if (version) {
      const badge = document.createElement("span");
      badge.className = "backend-version";
      badge.title = t("backend.version");
      badge.textContent = version;
      nameEl.appendChild(badge);
    }
    if (isRemote) {
      const rbadge = document.createElement("span");
      rbadge.className = "backend-version";
      rbadge.title = remoteNodes.join(", ");
      rbadge.textContent = t("backend.remote") + ": " + remoteNodes.join(", ");
      nameEl.appendChild(rbadge);
    }
    if (discovery && discovery.multiple === true && Array.isArray(discovery.candidates) && discovery.candidates.length) {
      nameEl.appendChild(buildMultiInstallDetails(discovery, resolvedPath));
    }
    info.appendChild(nameEl);

    if (resolvedPath) {
      const meta = document.createElement("div");
      meta.className = "node-meta backend-path";
      meta.textContent = resolvedPath;
      info.appendChild(meta);
    }

    row.appendChild(dot);
    row.appendChild(info);
    list.appendChild(row);
  });
}

function buildMultiInstallDetails(discovery, resolvedPath) {
  const details = document.createElement("details");
  details.className = "backend-multi";
  const summary = document.createElement("summary");
  summary.title = t("backend.multi_badge_title");
  summary.textContent = "⚠";
  details.appendChild(summary);

  const body = document.createElement("div");
  body.className = "backend-multi-detail";
  discovery.candidates.forEach(function(cand) {
    const line = document.createElement("div");
    const candPath = String((cand && cand.path) || "");
    const isActive = !!resolvedPath && candPath === resolvedPath;
    line.className = "backend-candidate" + (isActive ? " active" : "");
    let text = candPath || "--";
    const candVersion = String((cand && cand.version) || "").trim();
    if (candVersion) text += " · v" + candVersion;
    const candSource = String((cand && cand.source) || "").trim();
    if (candSource) text += " · " + candSource;
    line.textContent = text;
    if (isActive) {
      const tag = document.createElement("span");
      tag.className = "backend-candidate-tag";
      tag.textContent = t("backend.active");
      line.appendChild(tag);
    }
    body.appendChild(line);
  });
  const hint = document.createElement("div");
  hint.className = "backend-multi-hint";
  hint.textContent = t("backend.multi_install", { name: String(discovery.name || "") });
  body.appendChild(hint);
  details.appendChild(body);
  return details;
}

function _updateMeasureStatus() {
  var el = document.getElementById("measure-mode-status");
  if (!el) return;
  if (molDoc.mode !== "measure") { el.textContent = ""; return; }
  var typeLabels = { distance: t("measure.distance"), angle: t("measure.angle"), dihedral: t("measure.dihedral") };
  var needed = molDoc.measureType === "distance" ? 2 : molDoc.measureType === "angle" ? 3 : 4;
  var label = typeLabels[molDoc.measureType] || molDoc.measureType;
  var store = window.ACPGeometryStore;
  var selectedCount = store ? store.state.selection.length : 0;
  el.textContent = t("structure.measure.status_label") + label + " \u00b7 " + t("structure.measure.status_selected") + " " + selectedCount + "/" + needed;
}

function setMode(mode) {
  const previousMode = molDoc.mode;
  molDoc.mode = mode;
  if (mode !== "measure" && window.ACPGeometryStore) window.ACPGeometryStore.clearSelection();
  document.querySelectorAll(".tool-icon").forEach(function(btn) { btn.classList.remove("active"); });
  if (mode === "select") document.getElementById("btn-mode-select").classList.add("active");
  if (mode === "measure") {
    if (molDoc.measureType === "distance") document.getElementById("btn-measure-distance").classList.add("active");
    if (molDoc.measureType === "angle") document.getElementById("btn-measure-angle").classList.add("active");
    if (molDoc.measureType === "dihedral") document.getElementById("btn-measure-dihedral").classList.add("active");
  }
  _updateMeasureStatus();
  updateSrPickButtons();
  // Refresh overlays when entering/leaving SR mode so dashed pair cylinders
  // appear/disappear immediately (select/measure behaviour unchanged).
  if (previousMode !== mode && (previousMode === "sr" || mode === "sr") && currentAtoms().length) {
    renderMolDoc();
  }
}

function updateFrameController() {
  const controller = document.getElementById("frame-controller");
  const total = molDoc.frames.length;
  const current = total ? molDoc.currentFrame + 1 : 0;
  const activeTab = document.querySelector(".canvas-tab.active");
  document.getElementById("frame-current").textContent = String(current || 1);
  document.getElementById("frame-total").textContent = String(total || 1);
  document.getElementById("frame-slider").max = String(Math.max(total - 1, 0));
  document.getElementById("frame-slider").value = String(Math.max(molDoc.currentFrame, 0));
  const frame = currentFrame();
  document.getElementById("frame-energy").textContent = frame && frame.energy !== null ? String(frame.energy) : "";
  controller.style.display = total > 1 && activeTab && activeTab.getAttribute("data-tab") === "structure" ? "flex" : "none"; /* the frame controller is visible only on the structure tab; tab ids live at lines 3613-3616 */
}

function setFrame(index) {
  if (!molDoc.frames.length) return;
  molDoc.currentFrame = Math.max(0, Math.min(index, molDoc.frames.length - 1));
  // Load the new frame into the store (clears selection/measurements per frame revision)
  var frame = molDoc.frames[molDoc.currentFrame];
  if (frame && window.ACPGeometryStore) {
    var a = frame.atoms;
    window.ACPGeometryStore.load({ entryId: molDoc.meta.source || "frame-" + molDoc.currentFrame, symbols: a.map(function(at){ return at.elem; }), coordinates: a.map(function(at){ return [at.x, at.y, at.z]; }) });
  }
  renderMolDoc();
  updateFrameController();
  updateInfoPanel(lastSelectedJob);
}

function stopFramePlayback() {
  if (framePlayTimer) {
    clearInterval(framePlayTimer);
    framePlayTimer = null;
    document.getElementById("frame-play").textContent = t("frame.play");
  }
}

function toggleFramePlayback() {
  if (framePlayTimer) {
    stopFramePlayback();
    return;
  }
  if (molDoc.frames.length <= 1) return;
  document.getElementById("frame-play").textContent = "Stop";
  framePlayTimer = setInterval(function() {
    setFrame((molDoc.currentFrame + 1) % molDoc.frames.length);
  }, 900);
}

async function openJobFile(jobId, node) {
  if (!node || node.type === "folder") return;
  selectedFileKey = String(jobId) + "::" + node.path;
  renderFileTree(lastFileListing, jobId);
  if (selectedJobIsRemote) {
    await openRemoteFilePreview(jobId, node);
    return;
  }
  if (Number(node.size || 0) > 10485760) {
    appendLogLine("file-large", t("log.file_large", { path: node.path }));
    return;
  }
  const ext = node.name.includes(".") ? node.name.split(".").pop().toLowerCase() : "";
  if (node.path.endsWith(".xyz") || node.path.endsWith(".sdf") || node.path.endsWith(".mol")) {
    await loadStructureFile(jobId, node.path);
    setViewerTab("structure");
    appendLogLine("file-loaded", t("log.file_loaded", { path: node.path }));
    // Structure viewer: inject as manual_file entry so it appears in the list.
    if (window.ACPStructureViewer && window.ACPStructureViewer.injectManualEntry) {
      window.ACPStructureViewer.injectManualEntry(node.path, node.name);
    }
    return;
  }
  const pathUrl = "/jobs/" + encodeURIComponent(jobId) + "/files/" + node.path.split("/").map(encodeURIComponent).join("/");
  if (["log", "out"].indexOf(ext) >= 0) {
    const text = await api(pathUrl);
    resetLogs();
    appendLogHtml('<span class="text-panel">' + escapeHtml(String(text)) + '</span>');
    return;
  }
  if (ext === "json") {
    const text = await api(pathUrl);
    resetLogs();
    const pretty = typeof text === "string" ? (function() {
      try { return JSON.stringify(JSON.parse(text), null, 2); } catch (e) { return text; }
    })() : JSON.stringify(text, null, 2);
    appendLogHtml('<span class="text-panel">' + escapeHtml(pretty) + '</span>');
    return;
  }
  resetLogs();
  appendLogHtml('<span class="download-line">' + escapeHtml(t("logs.download_ready")) + '</span><span class="download-line"><a href="' + escapeHtml(API_BASE + pathUrl) + '" target="_blank" rel="noopener">' + escapeHtml(t("files.download")) + '</a></span>');
}

async function openRemoteFilePreview(jobId, node) {
  if (!node || node.type === "folder") return;
  const ext = node.name.includes(".") ? node.name.split(".").pop().toLowerCase() : "";
  const isStructure = ["xyz", "sdf", "mol"].indexOf(ext) >= 0;
  if (isStructure) {
    try {
      await loadRemoteStructureFile(jobId, node.path);
      setViewerTab("structure");
      appendLogLine("file-loaded", t("log.file_loaded", { path: node.path }));
    } catch (e) {
      appendLogLine("error", "Preview failed: " + String(e.message || e));
    }
    return;
  }
  const previewUrl = "/jobs/" + encodeURIComponent(jobId) + "/remote-files/" + node.path.split("/").map(encodeURIComponent).join("/") + "/preview?mode=auto";
  try {
    const resp = await api(previewUrl);
    if (resp && resp.mode === "report" && resp.content && typeof resp.content === "object") {
      resetLogs();
      appendLogHtml('<span class="text-panel">' + escapeHtml(JSON.stringify(resp.content, null, 2)) + '</span>');
      return;
    }
    const text = (resp && typeof resp.content === "string") ? resp.content : String(resp);
    resetLogs();
    appendLogHtml('<span class="text-panel">' + escapeHtml(text) + '</span>');
    if (resp && resp.truncated) {
      appendLogLine("file-large", t("log.file_large", { path: node.path }));
    }
  } catch (e) {
    appendLogLine("error", "Preview failed: " + String(e.message || e));
  }
}

function openSSE(jobId) {
  if (activeSSE) { activeSSE.close(); activeSSE = null; }
  resetLogs();
  var jid = String(jobId);

  function updateSSEState(state) {
    var indicator = document.getElementById("sse-indicator");
    if (indicator) {
      indicator.className = "sse-indicator " + state;
      indicator.textContent = t("logs.sse_" + state) || state;
    }
  }

  // Subscription contract (kept literal for the sync pin): the terminal
  // condition includes job.cancelled; the list is handed to the Client
  // event stream, which owns connection/reconnect/close lifecycle (R2/R6).
  var types = [
    "job.created", "job.started", "job.status",
    "stage.started", "stage.progress", "stage.completed", "stage.failed",
    "log.stdout", "log.stderr", "log",
    "job.completed", "job.failed", "job.cancelled", "job.paused", "job.resumed",
    "done", "error"
  ];

  if (!(window.ACPClient && window.ACPClient.events)) {
    // Single-owner policy: the events module is a pinned load-order
    // dependency — fail loudly instead of silently losing the stream.
    console.error("openSSE: window.ACPClient.events is missing — job event stream unavailable");
    updateSSEState("error");
    return;
  }

  activeSSE = ACPClient.events.openJobStream(jid, {
    onLog: function(type, message) {
      if (type === "error") {
        appendLogLine("error", t("log.event_error", { message: message || "unknown" }));
      } else {
        appendLogLine(type, t("log.event", { type: type, message: message }));
      }
    },
    onTerminalEvent: function(type, payload, id) {
      // Terminal events: final refresh then close SSE and mark job terminal
      if (type === "job.completed" || type === "job.failed" || type === "job.cancelled" || type === "done") {
        if (String(selectedJobId || "") === jid) {
          refreshLoadedFolders(jid);
          refreshSelectedJobDetail({ force: true });
          refreshJobs();
          // SSE terminal event is the authoritative signal: force bypasses
          // the possibly-stale state.payload.job_status guard.
          if (window.ACPStructureViewer && window.ACPStructureViewer.refreshPending) {
            window.ACPStructureViewer.refreshPending(jid, { force: true });
          }
        }
        updateSSEState("closed");
        if (type === "done") appendLogLine("done", t("logs.sse_done"));
      }
    },
    onStateChange: function(state) {
      updateSSEState(state);
      // Legacy fidelity: the reconnect warning line is page presentation.
      if (state === "reconnecting") {
        appendLogLine("warning", t("logs.sse_reconnecting"));
      }
    },
  }, { types: types });
}

async function selectJob(jobId) {
  // R1: the selection lifecycle (detail → files → panels → viewer → events
  // → list → catalog → auto-expand) is owned by the taskSession controller.
  // Every select() mints a monotonic session token — even for the same job —
  // so A→B→A and same-job reselect (rerun) can never adopt a superseded
  // session's late detail/files/failure; the token subsumes the three raw
  // selectedJobId string compares that guarded the former inline sequence.
  // The page global selectedJobId stays the single authoritative selection
  // and is still written first, inside the resetForSelection hook below.
  // The hook → former-inline-step mapping is documented in the header of
  // controllers/task_session_controller.js.
  var loadedJob = null;   // step 9b result (job row projected from detail/list)
  var loadedFiles = null; // step 10 result (root file listing)
  ACPWorkbench.controllers.taskSession.init({
    hooks: {
      // Steps 1-8: selection identity + folder/mechanism/energy/s2scan panel
      // resets, viewer teardown, placeholders, candidate-review reset.
      resetForSelection: function() {
        selectedJobId = jobId;
        folderCache = {};
        folderState = {};
        mechanismViewerState.jobId = String(jobId || "");
        mechanismViewerState.studyId = "";
        mechanismViewerState.pathDetail = "";
        mechanismViewerState.energyDetail = "";
        energyGraphState.jobId = String(jobId || "");
        energyGraphState.data = null;
        energyGraphState.dataRevision = "";
        energyGraphState.seriesId = "";
        energyGraphState.energySeriesId = "relative_energy";
        energyGraphState.liveFollow = true;
        energyGraphState.selectedNodeId = "";
        energyGraphState.selectionOrigin = "none";
        energyGraphState.selectedAnnotationId = "";
        energyGraphState.xDomain = null;
        energyGraphState.yDomain = null;
        energyGraphState.annotationMode = "all";
        energyGraphDestroyViewer();
        renderMechanismPlaceholder();
        // Candidate review state resets via its single owner (R4);
        // s2scanState no longer carries review fields.
        if (window.ACPWorkbench && window.ACPWorkbench.controllers && window.ACPWorkbench.controllers.candidateReview) {
          ACPWorkbench.controllers.candidateReview.reset();
        }
      },
      // Step 9: Q9 rich detail projection (stages/artifacts/error_detail/
      // recovery). fetchJobDetail transparently falls back to the legacy
      // record on older servers and returns null when the response was
      // rejected as late/stale (GAP-9) — the list row carries the panel
      // until a fresh detail arrives.
      loadDetail: function() {
        return fetchJobDetail(jobId);
      },
      // Step 9b: job row + remote-node projection.
      applyDetail: function(detail) {
        loadedJob = (detail && detail.job) || findJobRowInCache(jobId);
        if (!loadedJob) return null;
        lastRemoteNode = jobRemoteNodeName(loadedJob);
        selectedJobIsRemote = !!lastRemoteNode;
        return loadedJob;
      },
      // Step 10: file listing — remote endpoints use the 60s apiRemote()
      // budget; the remote listing is remapped to the local file shape.
      loadFiles: async function() {
        var filesResp;
        if (selectedJobIsRemote) {
          filesResp = await apiRemote("/jobs/" + encodeURIComponent(jobId) + "/remote-files");
          filesResp.files = (filesResp.files || []).map(function(f) {
            return {
              path: f.name,
              size: f.size,
              modified: f.mtime,
              is_dir: f.is_dir
            };
          });
        } else {
          filesResp = await api("/jobs/" + encodeURIComponent(jobId) + "/files");
        }
        loadedFiles = filesResp;
        return filesResp;
      },
      // Step 11: root listing + Q1 pinned result products (only when the job
      // wrote result_summary.json).
      applyFiles: function(filesResp) {
        folderCache[""] = filesResp.files || [];
        lastPinnedItems = Array.isArray(filesResp.pinned) ? filesResp.pinned : [];
      },
      // Step 12: info panel, file tree, NMR report (ordering as before).
      afterPanels: function() {
        updateInfoPanel(loadedJob);
        renderFileTree(loadedFiles.files || [], jobId);
        refreshNmrReport(loadedJob);
      },
      // Step 13: reload the active path|energy viewer tab.
      reloadViewerTab: function() {
        if (activeViewerTab === "path" || activeViewerTab === "energy") {
          return setViewerTab(activeViewerTab);
        }
      },
      // Step 14: queue row highlight.
      highlight: function() {
        highlightQueueRow(jobId);
      },
      // Step 15: close the previous event stream and open the new one.
      openEvents: function() {
        if (activeSSE) {
          activeSSE.close();
          activeSSE = null;
        }
        openSSE(jobId);
      },
      // Step 16 (Q3-P0): render the real stdout tail first; SSE status lines
      // append on top.
      primeLogs: function() {
        pollLogTail(jobId);
      },
      // Step 17: refresh the queue list so badges/recovery converge.
      refreshList: function() {
        return refreshJobs();
      },
      // Step 18: structure viewer auto-loads catalog + default entry geometry.
      onStructureCatalog: function() {
        if (window.ACPStructureViewer && window.ACPStructureViewer.onJobSelected) {
          window.ACPStructureViewer.onJobSelected(String(jobId));
        }
      },
      // Steps 19+20: mechanism jobs auto-expand to the v2 WORK stage tree
      // (T7) so the search/path/opt computation directories are visible
      // without manual clicks; expandPath stops silently when WORK/ does not
      // exist (historical jobs keep their legacy nested layout). v2 layout
      // (design doc §10): RESULT/ is expanded by default, WORK/ stays
      // collapsed — only when the root listing actually contains a RESULT
      // dir, so legacy (v1) jobs render exactly as before. The late study
      // fetch is token-guarded so a stale expand cannot pollute a newer
      // session's folder cache.
      afterSelection: function(session) {
        if (loadedJob.spec && loadedJob.spec.workflow === "mechanism") {
          expandPath(jobId, "WORK");
          fetchMechanismStudiesForJob(jobId).then(function(studies) {
            if (!ACPWorkbench.controllers.taskSession.isCurrent(session)) return;
            if (studies && studies.length) {
              var studyId = String(studies[0].id || studies[0].study_id || "");
              if (studyId) expandPath(jobId, "mechanism_study/" + studyId + "/calc");
            }
          });
        }
        var rootHasResult = (loadedFiles.files || []).some(function(f) {
          return f.is_dir && (f.name === "RESULT" || f.path === "RESULT");
        });
        if (rootHasResult && folderCache["RESULT"] === undefined) {
          expandPath(jobId, "RESULT");
        }
      },
    },
  });
  return ACPWorkbench.controllers.taskSession.select(jobId);
}

let wizardStructures = [];
let wizardParseWarnings = [];
let wizardInputMode = "structure";
let wizardSelectedStructureIndex = 0;
let wizardUploadFile = null;
function isAcceptedStructureFile(file) {
  if (!file || !file.name) return false;
  var ext = file.name.lastIndexOf(".") >= 0 ? file.name.slice(file.name.lastIndexOf(".")).toLowerCase() : "";
  return STRUCTURE_UPLOAD_EXTS.indexOf(ext) >= 0;
}
function rejectUnsupportedFile(file) {
  var name = file && file.name ? file.name : String(file);
  window.alert((t("modal.unsupported_ext") || "不支持的文件格式") + ": " + name);
}
let workflowCatalogCache = [];
let methodCatalogCache = {};
// Page-global preview viewer — EXT owner per T16 evidence §2.5: declared
// here, written by wizard.js (a bounded scene instance since T10; previewModel
// is its opaque model token), consumed by wizard.js / job_editor.js /
// bootstrap.js / wizard/upload-parse.js. Keep this the page's single
// declaration for the shared binding.
let previewViewer = null;
var PREVIEW_ZOOM_FACTOR = 0.82;
var previewViewerFraming = { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 };
let previewModel = null;
let previewModelStructureKey = "";
let wizardAtomLabelsOn = false;
let pesSelectionState = {
  mode: "functional",
  active: false,
  selectionKind: "bond_stretch",
  coordinateKind: "distance",
  selectedAtoms: [],
  structureKey: "",
  shapes: [],
};
let scanSelectionState = {
  active: false,
  selectionKind: "distance",
  selectedAtoms: [],
  structureKey: "",
  shapes: [],
};
let reactionViewer = null;              // mechanism builder: bounded scene instance for the reaction-changes 3D canvas (T11)
var reactionViewerFraming = { pending: false, settled: false, observer: null, timer: null, lastW: 0, lastH: 0, epoch: 0 };
let reactionChangesViewMode = "reactant"; // active side for the reaction-changes 3D viewer
// Manual bond edit mode (P2): opaque scene model token for click/hover wiring,
// locked first atom of the two-click pair flow, transient hover shape token,
// camera-preserving re-render flag and a debounce timestamp separating
// cylinder-cycle clicks from atom-pick clicks on overlapping geometry.
let reactionModel = null;
let reactionManualPick = null;
let reactionHoverShape = null;
let reactionPreserveViewNext = false;
let reactionManualCycleAt = 0;

// ── Mechanism Builder state (reaction-coordinate plans) ─────────────────
// Reaction-input binding belongs to the mechanism module. The generic parse
// preview must not assign reactant/product/TS semantics by position.
// mechCoordinatePlans holds the user-built coordinate plans
// ({coordinates, points, start_from}).
let mechProductStructure = null;
let mechTsGuessStructure = null;
let mechCoordinatePlans = [];
let mechStudyId = "";
let mechStrategy = "guided-scan";
let mechFidelity = "s3";
let mechConformerMode = "censo-lite";
let mechMaxSteps = 3;
let mechPromotionPolicy = "all_confirmed";
let mechScanPoints = 21;
let mechIrcPoints = 30;
let mechScanPointsDirty = false;
let mechIrcPointsDirty = false;
let reactionDefinition = null;


// ── Mechanism research section ──
// ── Mechanism-research section (topbar switcher + mech-* module launchers +
//    study management). Reuses the existing mechanism review modal, report
//    SVG builders and summary markup; only the list fetch gets a job-less
//    wrapper (fetchMechanismStudiesForJob stays job-scoped).
const APP_SECTION_KEY = "acp.section";
let appSection = "workbench";
let mechSectionStudiesCache = [];
const MECH_MODULE_IDS = ["conf", "step", "confirm", "chain"];
const MECH_MODULE_WORKFLOWS = { conf: "mech-conf", step: "mech-step", confirm: "mech-confirm", chain: "mech-chain" };

function loadAppSection() {
  // The standalone mechanism-management surface is retired from the UI.
  // Keep the legacy storage key harmless so an older browser session cannot
  // reopen the removed page after an upgrade.
  try { localStorage.removeItem(APP_SECTION_KEY); } catch (e) { /* ignore */ }
  return "workbench";
}

function saveAppSectionNow() {
  try { localStorage.setItem(APP_SECTION_KEY, appSection); } catch (e) { /* ignore */ }
}

function applyAppSection(section) {
  appSection = section || "workbench";
  const wb = document.querySelector(".workbench");
  const mechSec = document.getElementById("mechanism-section");
  if (wb) wb.style.display = appSection === "workbench" ? "" : "none";
  if (mechSec) {
    mechSec.style.display = appSection === "mechanism" ? "" : "none";
    if (appSection === "mechanism") fetchMechProjects();
  }
  document.querySelectorAll(".section-switch-btn").forEach(function(btn) {
    btn.classList.toggle("active", btn.getAttribute("data-section") === appSection);
  });
  if (viewer && typeof viewer.resize === "function") {
    if (mainViewerFraming.pending) return;
    viewer.resize();
    viewer.render();
  }
}

function setAppSection(section) {
  if (section && section !== appSection) {
    applyAppSection(section);
  }
}

function mechSectionVal(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || "").trim() : "";
}

function detectMechInputSourceType(value) {
  if (/\r?\n/.test(value)) return "xyz_text";
  if (/[\/\\]/.test(value) || /\.(xyz|gjf|com|sdf|mol|log|out|inp|yaml|yml|json)$/i.test(value)) return "file_path";
  return "smiles";
}

function buildMechModuleJobBody(moduleId) {
  const pfx = "mech-section-" + moduleId + "-";
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const name = MECH_MODULE_WORKFLOWS[moduleId] + "_" + stamp;
  const nproc = parseInt(mechSectionVal(pfx + "nproc"), 10) || 4;
  const mem = ACPMemoryInput.normalize(mechSectionVal(pfx + "mem"), "GB") || "8GB";
  const output = mechSectionVal(pfx + "output");
  const method = { schema_id: MECH_MODULE_WORKFLOWS[moduleId] };
  let input = null;
  if (moduleId === "conf") {
    const source = mechSectionVal(pfx + "input");
    if (!source) { window.alert(t("mechsec.input_required")); return null; }
    input = { source_type: detectMechInputSourceType(source), source: source };
    method.mode = mechSectionVal(pfx + "mode") || "censo-lite";
    method.charge = parseInt(mechSectionVal(pfx + "charge"), 10) || 0;
    method.multiplicity = parseInt(mechSectionVal(pfx + "mult"), 10) || 1;
  } else if (moduleId === "step") {
    const source = mechSectionVal(pfx + "source");
    if (!source) { window.alert(t("mechsec.source_required")); return null; }
    input = { source_type: "file_path", source: source };
    const target = mechSectionVal(pfx + "target");
    if (target) method.target = target;
    const planText = mechSectionVal(pfx + "plan");
    if (planText) {
      try { method.plan = JSON.parse(planText); } catch (e) { window.alert(t("mechsec.plan_invalid")); return null; }
    }
    method.strategy = mechSectionVal(pfx + "strategy") || "guided-scan";
    method.fidelity = mechSectionVal(pfx + "fidelity") || "s3";
  } else if (moduleId === "confirm") {
    const manifest = mechSectionVal(pfx + "manifest");
    if (!manifest) { window.alert(t("mechsec.manifest_required")); return null; }
    input = { source_type: "file_path", source: manifest };
    method.select = mechSectionVal(pfx + "select") || "ts:canonical";
    method.fidelity = mechSectionVal(pfx + "fidelity") || "s4";
  } else if (moduleId === "chain") {
    const configFile = mechSectionVal(pfx + "configfile");
    const configYaml = mechSectionVal(pfx + "configyaml");
    if (configFile) input = { source_type: "file_path", source: configFile };
    else if (configYaml) input = { source_type: "yaml_text", source: configYaml };
    else { window.alert(t("mechsec.config_required")); return null; }
  }
  if (!input) return null;
  if (output) method.output = output;
  const body = {
    workflow: MECH_MODULE_WORKFLOWS[moduleId],
    name: name,
    input: input,
    method: method,
    resources: { nproc: nproc, mem: mem },
  };
  if (selectedProjectId) body.project_id = selectedProjectId;
  return body;
}

async function submitMechModule(moduleId) {
  const body = buildMechModuleJobBody(moduleId);
  if (!body) return;
  const btn = document.getElementById("mech-section-submit-" + moduleId);
  const status = document.getElementById("mech-section-status-" + moduleId);
  if (btn) btn.disabled = true;
  if (status) status.textContent = t("mechsec.submitting");
  try {
    const result = await api("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (status) status.textContent = t("mechsec.submit_ok", { jobId: result && result.job_id ? result.job_id : "" });
    await refreshJobs();
  } catch (e) {
    if (status) status.textContent = t("mechsec.submit_failed") + ": " + (e && e.message ? e.message : e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function fetchAllMechanismStudies(force) {
  if (!force && mechSectionStudiesCache.length) return mechSectionStudiesCache;
  try {
    const body = await api("/mechanism-studies");
    mechSectionStudiesCache = Array.isArray(body) ? body : (body && Array.isArray(body.studies) ? body.studies : []);
  } catch (e) { /* keep previous cache so the list doesn't flicker away */ }
  return mechSectionStudiesCache;
}

async function refreshMechSectionStudies(force) {
  if (appSection !== "mechanism") return;
  const studies = await fetchAllMechanismStudies(force);
  renderMechSectionStudyList(studies);
}

function mechSectionStudyBadgeHtml(study) {
  const us = String(study && study.unified_status || "");
  if (us) {
    return '<span class="badge ' + getStudyStatusClass(us) + '" title="' + escapeHtml(us) + '">' + escapeHtml(translateStudyStatus(us)) + '</span>';
  }
  return '<span class="badge ' + getStatusClass(study && study.status) + '">' + escapeHtml(translateStatus(study && study.status)) + '</span>';
}

function renderMechSectionStudyList(studies) {
  const box = document.getElementById("mech-section-study-list");
  if (!box) return;
  if (!studies || !studies.length) {
    box.innerHTML = '<div class="viewer-mechanism-empty">' + escapeHtml(t("mechsec.study.empty")) + '</div>';
    return;
  }
  box.innerHTML = studies.map(function(study) {
    const id = String(study.id || "");
    const active = String(mechanismViewerState.studyId || "") === id ? " active" : "";
    const pending = Number(study.n_decisions_pending || 0);
    const pendingChip = pending > 0
      ? '<span class="badge waiting_review">' + escapeHtml(t("mechsec.study.pending", { count: pending })) + '</span>'
      : "";
    return '<div class="review-card mech-section-study-card' + active + '" data-study-id="' + escapeHtml(id) + '">' +
      '<div class="review-card-head"><div><div class="review-card-title">' + escapeHtml(id || "study") + '</div><div class="review-card-meta">' +
      escapeHtml(t("mechsec.study.meta", {
        states: study.n_states != null ? study.n_states : 0,
        edges: study.n_edges != null ? study.n_edges : 0,
        updated: formatDateTime(study.updated_at || study.created_at),
      })) + (study.job_id ? ' · job ' + escapeHtml(String(study.job_id)) : "") +
      '</div></div><div style="display:flex;gap:6px;align-items:center;">' + mechSectionStudyBadgeHtml(study) + pendingChip + '</div></div>' +
      '<div class="review-actions">' +
      '<button class="btn" data-study-action="view" data-study-id="' + escapeHtml(id) + '">' + escapeHtml(t("mechsec.study.view")) + '</button>' +
      '</div></div>';
  }).join("");
  box.querySelectorAll("[data-study-action]").forEach(function(btn) {
    btn.addEventListener("click", function() {
      const id = String(btn.getAttribute("data-study-id") || "");
      const study = (mechSectionStudiesCache || []).filter(function(s) { return String(s && s.id || "") === id; })[0];
      if (!study) return;
      openMechSectionStudy(study);
    });
  });
}

async function openMechSectionStudy(study) {
  if (!study || !study.id) return;
  const studyId = String(study.id);
  const jobId = study.job_id ? String(study.job_id) : "";
  if (jobId) {
    mechanismViewerState.jobId = jobId;
    await fetchMechanismStudiesForJob(jobId, true);
  }
  mechanismViewerState.studyId = studyId;
  mechanismViewerState.pathDetail = "";
  mechanismViewerState.energyDetail = "";
  await fetchMechanismStudyDetail(studyId, true);
  await fetchMechanismStudyReport(studyId);
  renderMechSectionStudyDetail();
  renderMechSectionStudyList(mechSectionStudiesCache);
}

function bindMechSectionDetailInteractions(container) {
  container.querySelectorAll("[data-edge-index]").forEach(function(el) {
    el.addEventListener("click", function() {
      const index = parseInt(el.getAttribute("data-edge-index"), 10);
      const report = mechanismStudyReportsById[mechanismViewerState.studyId] || null;
      const network = report && report.reaction_network ? report.reaction_network : null;
      const edge = network && Array.isArray(network.edges) ? network.edges[index] : null;
      if (!edge) return;
      mechanismViewerState.pathDetail = t("mech.network.edge_detail", {
        stepId: edge.step_id || "step",
        fidelity: edge.fidelity || t("common.none"),
        strategy: edge.path_strategy || t("common.none"),
        status: translateStatus(edge.status || t("common.unknown")),
      });
      const panel = el.closest(".viewer-mechanism-panel");
      const detailEl = panel ? panel.querySelector(".viewer-mechanism-detail") : null;
      if (detailEl) detailEl.textContent = mechanismViewerState.pathDetail;
    });
  });
}

function renderMechSectionStudyDetail() {
  const block = document.getElementById("mech-section-detail-block");
  const box = document.getElementById("mech-section-detail");
  if (!block || !box) return;
  const studyId = mechanismViewerState.studyId;
  if (!studyId) { block.style.display = "none"; box.innerHTML = ""; return; }
  const report = mechanismStudyReportsById[String(studyId)] || null;
  const detail = mechanismStudyDetailsById[String(studyId)] || null;
  let html = buildMechanismSummaryMarkup(report, detail, studyId);
  if (!report) {
    html += '<div class="viewer-mechanism-empty">' + escapeHtml(t("mechsec.study.no_report")) + '</div>';
  } else {
    const network = report.reaction_network || {};
    const netSvg = buildReactionNetworkSvg(report);
    if (netSvg) {
      html += buildMechanismPanelMarkup("path", t("mech.network.title"), t("mech.network.meta", {
        states: network.nodes ? network.nodes.length : 0,
        edges: network.edges ? network.edges.length : 0,
      }), netSvg, mechanismViewerState.pathDetail || t("review.detail_hint"), t("mech.network.empty"), "");
    }
    const engSvg = buildEnergyProfileSvg(report);
    if (engSvg) {
      const routeCount = report.mechanism_profile && report.mechanism_profile.routes ? report.mechanism_profile.routes.length : 0;
      html += buildMechanismPanelMarkup("energy", t("mech.energy.title"), t("mech.energy.meta", { routes: routeCount }), engSvg, mechanismViewerState.energyDetail || t("mech.energy.axis_y"), t("mech.energy.empty"), "");
    }
    if (!netSvg && !engSvg) {
      html += '<div class="viewer-mechanism-empty">' + escapeHtml(t("mechsec.study.no_report")) + '</div>';
    }
  }
  box.innerHTML = html;
  block.style.display = "";
  bindMechSectionDetailInteractions(box);
}

// Four-phase mechanism project panel retired.

function getProjectStatusClass(status) {
  var s = String(status || "").toLowerCase();
  if (s === "completed") return "completed";
  if (s.indexOf("ready") >= 0) return "waiting_review";
  if (s === "blocked") return "failed";
  return "idle";
}

function getProjectStatusLabel(status) {
  var s = String(status || "").toLowerCase();
  var map = { created: "已创建", completed: "已完成", blocked: "已阻塞" };
  return map[s] || status || "未知";
}

function renderMechProjectCard(project) {
  var id = String(project.project_id || project.id || "");
  var name = escapeHtml(project.name || id);
  var status = String(project.status || "created");
  var charge = project.charge != null ? project.charge : 0;
  var mult = project.multiplicity != null ? project.multiplicity : 1;
  var created = project.created_at ? formatDateTime(project.created_at) : "";
  // Read-only: no stage chips or action buttons (mechanism write actions retired).
  return '<div class="mech-project-card" data-project-id="' + escapeHtml(id) + '">' +
    '<div class="mech-project-head">' +
    '<div><div class="mech-project-name">' + name + '</div>' +
    '<div class="mech-project-meta">' + escapeHtml(id) + ' · 电荷 ' + charge + ' · 多重度 ' + mult + (created ? ' · ' + created : '') + '</div></div>' +
    '<span class="badge ' + getProjectStatusClass(status) + '">' + escapeHtml(getProjectStatusLabel(status)) + '</span>' +
    '</div>' +
    '</div>';
}

async function fetchMechProjects(force) {
  var box = document.getElementById("mech-project-list");
  if (!box) return;
  try {
    var resp = await api("/mechanism-projects");
    var projects = Array.isArray(resp) ? resp : (resp && Array.isArray(resp.projects) ? resp.projects : []);
    if (!projects.length) {
      box.innerHTML = '<div class="mech-project-empty">暂无项目</div>';
      return;
    }
    box.innerHTML = projects.map(renderMechProjectCard).join("");
  } catch (e) {
    box.innerHTML = '<div class="mech-project-empty">暂无项目（API 未就绪）</div>';
  }
}

function setupMechanismSectionListeners() {
  var refreshBtn = document.getElementById("mech-project-refresh");
  if (refreshBtn) refreshBtn.addEventListener("click", function() { fetchMechProjects(true); });
}

