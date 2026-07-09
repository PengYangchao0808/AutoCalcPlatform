ACP Molecular Workbench v3 -- Interactive Molecular Editor Plan (reviewed)
==========================================================================

**Date:** 2026-06-27 (revised after code review)
**Status:** Design specification, ready for implementation
**Scope:** Upgrade 3D canvas from "display-only viewer" to "interactive molecular workbench"
**File:** Modifies `frontend/ACP_Workbench_v2.html` (additive)
**Constraint:** Single HTML file, vanilla JS, 3Dmol.js from CDN. No npm/React.

---

## Review Corrections

| Issue | Root Cause | Fix |
|-------|-----------|-----|
| Status bar shows `{host}` literal | `t()` interpolation regex over-escaped: `"\\\\{"` matches literal `\{` not `{` (line 1419 of v2) | **P0 #1**: Replace regex with `value.split("{" + name + "}").join(String(vars[name]))` |
| molDoc.atoms vs molDoc.frames conflict | Plan defined both but rendering reads frames, selection reads atoms | **Unified model**: no top-level `atoms`. Always use `currentFrame().atoms`. See Section 1. |
| `zoomTo()` on every render breaks camera | Called at end of `renderMolDoc()` | **Only call `zoomTo()` on initial load or explicit Fit button.** `rerender()` preserves camera. |
| P0 included undo/dirty/edit | Scope mismatch with P1 | **P0 is view+select+measure+style+frames ONLY.** Undo/dirty/edit/submit moved to P1. |
| Submit via `input.xyz` | Runner only reads `input.source` (runner.py:256) | **P1**: Save structure as temp file via new endpoint, then submit with `input.source`. |
| Encoding not in acceptance | "Chinese labels present" too weak | **Acceptance**: "All Chinese UI renders correctly in browser, HTML saved as UTF-8, verified via screenshot." |

---

## 1. molDoc Data Model (Unified)

No top-level `atoms`. The current frame is the only atom source.

```javascript
const molDoc = {
  frames: [],          // [{atoms: [{id, elem, x, y, z}], comment, energy}]
  currentFrame: 0,
  selection: new Set(),
  style: "ball-stick",
  bgColor: "0x0e1419",
  measures: [],        // [{type, atoms: [ids], value, shape, label}]
  mode: "view",
  meta: {
    source: "",
    formula: "",
    charge: 0,
    multiplicity: 1,
  },
};

function currentFrame() {
  return molDoc.frames[molDoc.currentFrame] || null;
}

function currentAtoms() {
  const f = currentFrame();
  return f ? f.atoms : [];
}
```

ALL code that needs atom data calls `currentAtoms()`. Selection, rendering, measurement, style -- everything reads from the same source.

---

## 2. Camera Preservation

`renderMolDoc()` has two variants:

```javascript
function renderMolDoc(options) {
  options = options || {};
  if (!viewer) initViewer();
  viewer.removeAllModels();
  viewer.removeAllShapes();
  viewer.removeAllLabels();

  const atoms = currentAtoms();
  if (!atoms.length) return;

  if (molDoc.frames.length > 1) {
    viewer.addModelsAsFrames(framesToXYZ(molDoc.frames), "xyz");
    viewer.setFrame(molDoc.currentFrame);
  } else {
    viewer.addModel(atomsToXYZ(atoms), "xyz");
  }

  applyStylePreset(molDoc.style);
  applySelectionHighlight();
  renderMeasurements();

  if (options.zoomTo) {
    viewer.zoomTo();
  }
  viewer.render();
}
```

- **Initial load**: `renderMolDoc({zoomTo: true})` -- camera fits the molecule.
- **Style change, selection, measurement, frame switch**: `renderMolDoc()` -- camera preserved.
- **Fit button / F key**: `viewer.zoomTo(); viewer.render();`

---

## 3. i18n Interpolation Fix (P0 #1)

Current broken code (v2 line 1419):
```javascript
value = value.replace(new RegExp("\\\\{" + name + "\\\\}", "g"), String(vars[name]));
```
The `"\\\\{"` produces regex `\\{` which matches literal backslash-brace, NOT `{`.

Fixed code:
```javascript
function t(key, vars) {
  const dict = I18N[currentLang] || I18N["zh-CN"];
  let value = dict[key] || I18N["zh-CN"][key] || key;
  if (vars) {
    Object.keys(vars).forEach(function(name) {
      value = value.split("{" + name + "}").join(String(vars[name]));
    });
  }
  return value;
}
```

Using `split().join()` instead of regex avoids all escaping issues.

---

## 4. Style Presets

Remove `viewer.setViewStyle({ style: "outline" })` from `initViewer()`.

```javascript
const STYLE_PRESETS = {
  "ball-stick": {
    label_zh: "球棍", label_en: "Ball-Stick",
    style: { stick: { radius: 0.15 }, sphere: { scale: 0.28 } },
    bg: "0x0e1419",
  },
  "stick": {
    label_zh: "骨架", label_en: "Stick",
    style: { stick: { radius: 0.15 } },
    bg: "0x0e1419",
  },
  "spacefill": {
    label_zh: "空间填充", label_en: "Spacefill",
    style: { sphere: { scale: 1.0 } },
    bg: "0x0e1419",
  },
  "wireframe": {
    label_zh: "线框", label_en: "Wireframe",
    style: { line: {} },
    bg: "0x0e1419",
  },
  "publication": {
    label_zh: "出版级", label_en: "Publication",
    style: { stick: { radius: 0.12 }, sphere: { scale: 0.22 } },
    bg: "white",
  },
};

function applyStylePreset(name) {
  const preset = STYLE_PRESETS[name] || STYLE_PRESETS["ball-stick"];
  molDoc.style = name;
  viewer.setStyle({}, preset.style);
  viewer.setBackgroundColor(preset.bg);
}
```

---

## 5. Atom Selection

```javascript
function setupClickable() {
  viewer.setClickable({}, true, function(atom, viewerInstance, event) {
    if (molDoc.mode !== "select" && molDoc.mode !== "measure") return;

    if (molDoc.mode === "select") {
      if (event && event.shiftKey) {
        molDoc.selection.has(atom.index)
          ? molDoc.selection.delete(atom.index)
          : molDoc.selection.add(atom.index);
      } else {
        molDoc.selection.clear();
        molDoc.selection.add(atom.index);
      }
      renderMolDoc();
      updateInfoPanel();
    }

    if (molDoc.mode === "measure") {
      handleMeasureClick(atom.index);
    }
  });
}
```

Highlight selected atoms with yellow overlay:
```javascript
function applySelectionHighlight() {
  const baseStyle = STYLE_PRESETS[molDoc.style].style;
  viewer.setStyle({}, baseStyle);
  molDoc.selection.forEach(function(idx) {
    viewer.addStyle({index: idx}, {
      sphere: { scale: 0.35, color: "yellow" },
    });
  });
}
```

INFO panel when atoms are selected:
- Single atom: element, index, x/y/z coordinates
- Multiple atoms: count, element list, center of mass
- Empty: job info (as before)

---

## 6. Measurement System

### Distance (2 clicks)

```javascript
function measureDistance(idA, idB) {
  const atoms = currentAtoms();
  const a = atoms[idA], b = atoms[idB];
  const va = new $3Dmol.Vector3(a.x, a.y, a.z);
  const vb = new $3Dmol.Vector3(b.x, b.y, b.z);
  const dist = va.distanceTo(vb);
  const shape = viewer.addCylinder({
    dashed: true, start: va, end: vb,
    radius: 0.05, color: "magenta",
    fromCap: 1, toCap: 1,
  });
  const mid = va.clone().add(vb).multiplyScalar(0.5);
  const label = viewer.addLabel(dist.toFixed(3) + " A", {
    position: mid, fontSize: 12, fontColor: "magenta",
    backgroundColor: "rgba(0,0,0,0.7)",
  });
  addMeasure("distance", [idA, idB], dist, shape, label);
}
```

### Angle (3 clicks: A-B-C, angle at B)

```javascript
function measureAngle(idA, idB, idC) {
  const atoms = currentAtoms();
  const ba = vec3(atoms[idA]).sub(vec3(atoms[idB]));
  const bc = vec3(atoms[idC]).sub(vec3(atoms[idB]));
  const angle = Math.acos(ba.dot(bc) / (ba.length() * bc.length())) * 180 / Math.PI;
  // Draw label near atom B
  const label = viewer.addLabel(angle.toFixed(1) + " deg", {
    position: vec3(atoms[idB]), fontSize: 12, fontColor: "cyan",
    backgroundColor: "rgba(0,0,0,0.7)",
  });
  addMeasure("angle", [idA, idB, idC], angle, null, label);
}
```

### Dihedral (4 clicks: A-B-C-D)

```javascript
function measureDihedral(idA, idB, idC, idD) {
  // Standard torsion angle calculation from 4 atom positions
  // b1 = cross(BA, BC), b2 = cross(BC, CD)
  // angle = atan2(dot(BC, cross(b1,b2)), dot(b1,b2))
  const angle = computeTorsion(atoms[idA], atoms[idB], atoms[idC], atoms[idD]);
  const label = viewer.addLabel(angle.toFixed(1) + " deg", {
    position: midpoint(atoms[idB], atoms[idC]),
    fontSize: 12, fontColor: "orange",
    backgroundColor: "rgba(0,0,0,0.7)",
  });
  addMeasure("dihedral", [idA, idB, idC, idD], angle, null, label);
}
```

### Measurement click handler

```javascript
let measureBuffer = [];

function handleMeasureClick(atomIndex) {
  measureBuffer.push(atomIndex);
  const needed = molDoc.measureType === "distance" ? 2
               : molDoc.measureType === "angle" ? 3 : 4;

  if (measureBuffer.length >= needed) {
    if (molDoc.measureType === "distance") measureDistance(measureBuffer[0], measureBuffer[1]);
    if (molDoc.measureType === "angle") measureAngle(measureBuffer[0], measureBuffer[1], measureBuffer[2]);
    if (molDoc.measureType === "dihedral") measureDihedral(measureBuffer[0], measureBuffer[1], measureBuffer[2], measureBuffer[3]);
    measureBuffer = [];
    molDoc.selection.clear();
    renderMolDoc();
    renderMeasurementList();
  } else {
    molDoc.selection.add(atomIndex);
    renderMolDoc();
  }
}
```

### Measurement list panel

Right panel section showing all measurements:
```
测量
-----------------------------------
距离  C1-C2    1.529 A    [x]
角度  C1-O-C2  108.5 deg  [x]
二面角 C1-C2-O-H  -60.2 deg  [x]
```

Delete button removes the shape/label and re-renders.

---

## 7. Multi-Frame XYZ

Parse multi-frame XYZ:
```javascript
function parseMultiFrameXYZ(text) {
  const lines = text.trim().split("\n");
  const frames = [];
  let i = 0;
  while (i < lines.length) {
    const n = parseInt(lines[i]);
    if (isNaN(n) || i + 1 + n >= lines.length) break;
    const comment = lines[i + 1] || "";
    const atoms = [];
    for (let j = 0; j < n; j++) {
      const parts = (lines[i + 2 + j] || "").trim().split(/\s+/);
      atoms.push({ id: j, elem: parts[0], x: +parts[1], y: +parts[2], z: +parts[3] });
    }
    frames.push({ atoms, comment, energy: parseEnergy(comment) });
    i += 2 + n;
  }
  return frames;
}

function parseEnergy(comment) {
  const m = comment.match(/(-?\d+\.\d+)/);
  return m ? parseFloat(m[1]) : null;
}
```

Frame controller bar (appears only when `frames.length > 1`):
```html
<div class="frame-controller" id="frame-controller" style="display:none;">
  <button class="frame-btn" id="frame-prev">&lt;</button>
  <span class="frame-info">帧 <span id="frame-current">1</span> / <span id="frame-total">1</span></span>
  <button class="frame-btn" id="frame-next">&gt;</button>
  <button class="frame-btn" id="frame-play">播放</button>
  <input type="range" class="frame-slider" id="frame-slider" min="0" max="0" value="0">
  <span class="frame-energy" id="frame-energy"></span>
</div>
```

Frame switch preserves camera (no zoomTo):
```javascript
function switchFrame(idx) {
  molDoc.currentFrame = idx;
  renderMolDoc();
  updateFrameInfo();
}
```

---

## 8. Tool Dock Redesign

Replace current emoji buttons with grouped SVG icon toolbar.

### Groups

```
[View]   Fit | Reset | Style dropdown | BG toggle | Screenshot
[Select] Select mode | Clear
[Measure] Distance | Angle | Dihedral
[Edit]   (all P1 -- disabled with tooltip)
[Compute] (all P1 -- disabled with tooltip)
```

### SVG icon examples (inline, 20x20, stroke=currentColor)

```html
<!-- Fit (expand arrows) -->
<svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.5">
  <path d="M3 3v5M3 3h5M17 3v5M17 3h-5M3 17v-5M3 17h5M17 17v-5M17 17h-5"/>
</svg>

<!-- Reset (circular arrow) -->
<svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.5">
  <path d="M3 10a7 7 0 1 1 7 7M3 10V5M3 10h5"/>
</svg>

<!-- Screenshot (camera) -->
<svg viewBox="0 0 20 20" width="16" height="16" stroke="currentColor" fill="none" stroke-width="1.5">
  <rect x="3" y="5" width="14" height="11" rx="1"/>
  <circle cx="10" cy="10" r="3"/>
</svg>
```

All icons: 16x16 px, `stroke="currentColor"`, `fill="none"`, `stroke-width="1.5"`, geometric paths. No emoji, no Unicode symbols.

### Active mode indicator

Current mode highlighted with accent-colored underline:
```css
.tool-group.active {
  border-bottom: 2px solid var(--accent);
}
```

---

## 9. Keyboard Shortcuts

| Key | Action | Mode |
|-----|--------|------|
| F | Fit view (`zoomTo`) | Any |
| R | Reset view (reset camera to default) | Any |
| Esc | Clear selection / cancel measurement | Select/Measure |
| 1 | Switch to View mode | Any |
| 2 | Switch to Select mode | Any |
| 3 | Switch to Measure-Distance | Any |
| 4 | Switch to Measure-Angle | Any |
| 5 | Switch to Measure-Dihedral | Any |

Undo/redo (Ctrl+Z/Y) deferred to P1.

---

## 10. Backend API (P1 only)

| Endpoint | Purpose | Why P1 |
|----------|---------|--------|
| `POST /api/v1/molecule/validate` | Check valence, charge/spin | Editing needs it |
| `POST /api/v1/molecule/optimize` | RDKit UFF/MMFF | Editing needs it |
| `POST /api/v1/projects/{id}/structures` | Save edited XYZ | Editing needs it |

**Submit from structure (P1):** Runner only reads `input.source` (runner.py:256). To submit from edited structure: save XYZ to temp file via new endpoint, then `POST /jobs` with `input.source` pointing to that file.

---

## 11. Implementation Phases

### P0: Interactive Viewer and Measurement (this round)

| # | Task | Priority |
|---|------|----------|
| 1 | Fix i18n interpolation: replace regex with `split().join()` | Critical |
| 2 | Verify all Chinese renders correctly (no mojibake, UTF-8) | Critical |
| 3 | Implement `molDoc` with `currentFrame()` / `currentAtoms()` | Critical |
| 4 | Refactor `loadStructure()` to parse into molDoc then render | Critical |
| 5 | Remove `setViewStyle({style:"outline"})` from initViewer | High |
| 6 | Implement camera-preserving `renderMolDoc(options)` | Critical |
| 7 | 5 style presets + dropdown selector | High |
| 8 | Atom click selection (`setClickable`) + yellow highlight | High |
| 9 | Shift+click multi-select, Esc clear | High |
| 10 | INFO panel: show selection atom properties | Medium |
| 11 | Distance measurement (2-click + dashed cylinder + label) | High |
| 12 | Angle measurement (3-click + label) | High |
| 13 | Dihedral measurement (4-click + label) | High |
| 14 | Measurement list in right panel with delete buttons | Medium |
| 15 | Multi-frame XYZ parsing + frame controller bar | High |
| 16 | Tool dock: replace emoji with SVG icon groups | High |
| 17 | Screenshot via `pngURI()` | Medium |
| 18 | BG color toggle (dark/light) | Medium |
| 19 | Keyboard shortcuts (F/R/Esc/1-5) | Medium |
| 20 | Atom labels toggle (element/index/none) | Medium |
| 21 | Fix status bar placeholder rendering (covered by #1) | Critical |
| 22 | i18n additions for all new labels | Medium |

### P1: Structural Editing

| # | Task |
|---|------|
| 23 | Undo/redo stack (molDoc snapshot-based) |
| 24 | Delete selected atoms |
| 25 | Change element of selected atom |
| 26 | Add hydrogen to selected atom |
| 27 | Clean structure via `POST /molecule/optimize` |
| 28 | Export XYZ from molDoc |
| 29 | Coordinate table editor in INFO panel |
| 30 | Save current structure as project file |
| 31 | Submit calculation from current structure (via temp file) |
| 32 | `POST /molecule/validate` backend endpoint |

### P2: Advanced Visualization

| # | Task |
|---|------|
| 33 | Conformer player with energy sort + RMSD alignment |
| 34 | IRC/NEB trajectory animation |
| 35 | Cube isosurface (HOMO/LUMO, density, ESP) |
| 36 | Publication-quality export |
| 37 | ORTEP thermal ellipsoids |

---

## 12. Acceptance Criteria (P0)

- [ ] `t()` interpolation works: `{host}`, `{port}`, `{queued}`, `{running}` all replaced correctly in status bar
- [ ] All Chinese UI text renders correctly in browser (no mojibake), HTML saved as UTF-8, verified via screenshot
- [ ] `molDoc` uses `currentFrame().atoms` exclusively -- no separate top-level `atoms` array
- [ ] Loading a `.xyz` file parses into molDoc frames, then renders
- [ ] `renderMolDoc()` preserves camera position; only `renderMolDoc({zoomTo: true})` or Fit button resets camera
- [ ] No heavy black outline on atoms (outline removed from initViewer)
- [ ] 5 style presets available via dropdown
- [ ] Click atom in Select mode -> highlights yellow
- [ ] Shift+click adds to selection; Esc clears
- [ ] Distance: click 2 atoms -> dashed line + label in Angstroms
- [ ] Angle: click 3 atoms -> label in degrees
- [ ] Dihedral: click 4 atoms -> label in degrees
- [ ] Measurement list in right panel with delete buttons
- [ ] Multi-frame XYZ shows frame controller (prev/next/play/slider)
- [ ] Tool dock uses SVG icons (no emoji, no Unicode)
- [ ] Screenshot saves PNG
- [ ] F=fit, R=reset, Esc=clear, 1-5=mode switch
- [ ] All existing tests pass
