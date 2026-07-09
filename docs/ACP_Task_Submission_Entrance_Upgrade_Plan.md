# ACP Task Submission Entrance Upgrade Plan

**Date:** 2026-06-28
**Status:** Design proposal for implementation
**Scope:** Upgrade the Workbench "new job" entrance from a SMILES-only modal into a chemistry-aware task submission center.
**Primary files impacted:** `frontend/ACP_Workbench_v2.html`, `src/acp/api/v1_routes.py`, `src/acp/api/v1_schemas.py`, `src/acp/scheduler/jobs.py`, `src/acp/scheduler/runner.py`, new `src/acp/intake/` and workflow catalog modules.

---

## 1. Current Problems

The current job modal is useful as a smoke-test entrance, but not as a real computational chemistry workbench entrance.

Observed frontend limitations:

- Structure input is a single SMILES text box only.
- No file upload, drag/drop, paste XYZ, multi-structure SDF/XYZ, Gaussian input, ORCA input, or project-file reuse.
- Workflow selection is a flat select with only demo/conformer/NMR/benchmark.
- Protocol selection is a flat select, disconnected from workflow-specific method settings.
- Resource fields expose only `nproc` and `mem`.
- There is no review/dry-run step before creating many jobs.

Observed backend limitations:

- `JobSpec.input`, `JobSpec.method`, and `JobSpec.resources` are generic dicts, but the API does not yet expose structured input parsing or method schemas.
- `/api/v1/workflows` only exposes high-level workflow names.
- `/api/v1/protocols` only exposes protocol names from `ALL_PROTOCOLS`.
- The runner only maps a few workflow names into fixed CLI commands.
- There is no uploaded-file registry, input batch model, or workflow-template model.

Target direction:

> A user should be able to upload or paste molecular structures, preview/validate them, choose either a simple calculation, a preset workflow, or a custom task sequence, then edit method details and submit one or many jobs with clear provenance.

---

## 2. Target UX: New Calculation Center

Replace the current compact modal with a wider wizard-style "New Calculation" dialog.

### 2.1 Wizard Steps

1. **Input Structures**
   - SMILES / InChI
   - Paste XYZ / MOL / SDF / Gaussian input / ORCA input
   - File upload
   - Project files
   - ChemDraw export or image-assisted parsing in later phases

2. **Select Task**
   - Simple calculations
   - Preset workflows
   - Custom task sequence

3. **Method And Protocol**
   - Context-sensitive method editor based on selected task/workflow.
   - Quick presets plus advanced settings.

4. **Resources And Queue**
   - Global resource defaults.
   - Per-stage overrides for advanced workflows.
   - Batch expansion policy.

5. **Review And Submit**
   - List structures and jobs to be created.
   - Validate input, methods, binaries, and output paths.
   - Submit only after a successful dry-run summary.

### 2.2 Layout Sketch

```text
+------------------------------------------------------------------+
| New Calculation                                                   |
+--------------------+---------------------------------------------+
| 1 Input            |  Input mode: [SMILES] [Paste] [Upload] ...   |
| 2 Task             |                                             |
| 3 Method           |  Drop zone / text editor / project picker    |
| 4 Resources        |                                             |
| 5 Review           |  Structure preview table                     |
|                    |  name | format | atoms | charge | spin | OK |
+--------------------+---------------------------------------------+
| Cancel                                           Validate | Submit |
+------------------------------------------------------------------+
```

The modal should be large enough for repeated operational use. Avoid hiding critical method details inside tiny selects.

---

## 3. Input Structure Upgrade

### 3.1 Supported Input Modes

P0:

- SMILES single molecule.
- Multi-line SMILES list.
- Paste XYZ text.
- Upload `.xyz`, `.sdf`, `.mol`, `.gjf`, `.com`, `.inp`.
- Multi-structure SDF.
- Multi-frame XYZ as either one trajectory/ensemble or as split structures, chosen by user.

P1:

- `.mol2`, `.pdb`, `.cif` where coordinates are available.
- `.zip` containing supported structure files.
- `.csv` / `.tsv` batch table with columns such as `name`, `smiles`, `file`, `charge`, `multiplicity`, `tags`.
- Reuse structures from existing project artifacts.

P2:

- ChemDraw `.cdx` / `.cdxml` import.
- Ketcher/ChemDraw-style 2D editor export to molfile.

P3:

- ChemDraw image-assisted parsing.
- This should be optional and review-gated. Image-to-structure is not deterministic enough to submit directly into QC jobs.
- Preferred engines: MolScribe or OSRA as optional plugins/backends.

### 3.2 Important Rule For ChemDraw Images

Do not treat a ChemDraw screenshot as a trusted structure. The pipeline must be:

1. Image upload.
2. Parse candidate structure.
3. Display 2D/3D preview.
4. User confirms or edits.
5. Only then create a calculation input.

For practical reliability, support ChemDraw export formats (`.cdx`, `.cdxml`, MOL/SDF copied from ChemDraw) before image recognition.

### 3.3 Backend Intake Model

Add a new package:

```text
src/acp/intake/
  __init__.py
  models.py
  parsers.py
  registry.py
  storage.py
  validation.py
  chemdraw.py        # P2/P3 optional
```

Core models:

```python
StructureAsset:
  asset_id: str
  project_id: str
  name: str
  source_type: "smiles" | "file" | "paste" | "project_artifact" | "image"
  original_format: str
  canonical_format: "xyz" | "sdf" | "mol"
  file_path: str | None
  xyz: str | None
  molfile: str | None
  has_3d: bool
  charge: int
  multiplicity: int
  atom_count: int
  formula: str
  warnings: list[str]
  errors: list[str]

InputBatch:
  batch_id: str
  project_id: str
  structures: list[StructureAsset]
  created_at: str
  source_summary: dict
```

Parser requirements:

- Never silently convert failed input into a different molecule.
- Preserve original uploaded file.
- Store normalized XYZ/MOL only after validation.
- Extract charge/multiplicity from GJF/COM/INP where possible.
- For ambiguous input, return warnings and require user confirmation.

### 3.4 API Additions

P0 endpoints:

```text
POST /api/v1/uploads
POST /api/v1/structures/parse
POST /api/v1/input-batches
GET  /api/v1/input-batches/{batch_id}
POST /api/v1/jobs/batch
```

P1 endpoints:

```text
GET  /api/v1/projects/{project_id}/structures
GET  /api/v1/structures/{asset_id}
POST /api/v1/structures/{asset_id}/embed
POST /api/v1/structures/{asset_id}/validate
```

P2/P3 endpoints:

```text
POST /api/v1/structures/from-chemdraw
POST /api/v1/structures/from-image
```

Upload security:

- Store uploads under project-controlled directories only.
- Reject path traversal and executable files.
- Enforce size limits.
- Preserve original filename separately from storage path.
- Return parse errors without creating jobs.

---

## 4. Workflow Catalog Upgrade

The current workflow select should become a searchable task catalog.

### 4.1 Task Categories

Simple calculations:

- Single Point Energy
- Geometry Optimization
- Frequency Calculation
- Optimization + Frequency
- xTB Optimization
- CREST Conformer Search
- NMR Shielding
- Thermochemistry
- Scan / relaxed scan

Preset workflows:

- Confsearch
- Confsearch + DFT refinement
- Confsearch + NMR
- NMR from existing conformer set
- Benchmark
- TS guess / mechanism workflow
- Reaction path / IRC workflow

Custom workflows:

- Linear task sequence builder in P1.
- DAG workflow builder in P2.

### 4.2 Workflow Catalog Schema

Add a richer endpoint:

```text
GET /api/v1/workflow-catalog
```

Example schema:

```json
{
  "workflows": [
    {
      "id": "singlepoint",
      "label": "Single Point Energy",
      "category": "simple",
      "input_requirements": ["3d_structure"],
      "method_schema_id": "dft_singlepoint",
      "default_backend": "gaussian",
      "requires_binaries": ["gaussian"],
      "outputs": ["energy", "log", "chk"]
    }
  ]
}
```

Do not remove `/api/v1/workflows` immediately. Keep it as a compatibility endpoint and have the new frontend use `/api/v1/workflow-catalog`.

### 4.3 Backend Execution Strategy

P0:

- Keep existing `workflow` names for actual execution.
- Add richer frontend catalog but map selected cards to current workflows where possible.
- For unsupported simple tasks, mark as "planned" in UI or route to a dry-run-only mode.

P1:

- Add first-class scheduler workflow names:
  - `singlepoint`
  - `optimize`
  - `frequency`
  - `optfreq`
  - `xtb_optimize`

P2:

- Add `custom_sequence` workflow.
- Runner executes a validated ordered list of task nodes.

---

## 5. Custom Task Sequence Builder

The user should be able to compose reusable calculation pipelines.

### 5.1 P1 Linear Builder

Use an ordered list before building a full graph UI.

Available blocks:

- Input normalization
- RDKit embed
- xTB optimize
- CREST search
- conformer cluster
- DFT optimize
- DFT frequency
- DFT single point
- NMR shielding
- Shermo thermo
- export report

Each block declares:

```json
{
  "node_id": "dft_opt_1",
  "type": "dft_optimize",
  "backend": "gaussian",
  "inputs": ["structure"],
  "outputs": ["optimized_structure", "energy", "log"],
  "method": {
    "functional": "wB97X-D",
    "basis": "def2-SVP",
    "dispersion": "D4",
    "solvent": "water"
  },
  "resources": {
    "nproc": 16,
    "mem": "32GB"
  }
}
```

### 5.2 P2 Graph Builder

Only after the linear builder is stable:

- Drag blocks onto canvas.
- Connect outputs to inputs.
- Validate DAG before submission.
- Save as workflow template.
- Reuse template across projects.

API:

```text
GET  /api/v1/task-blocks
POST /api/v1/workflow-templates
GET  /api/v1/workflow-templates
POST /api/v1/workflow-templates/{id}/validate
POST /api/v1/jobs/from-template
```

---

## 6. Method And Protocol Editor

The current protocol select is too shallow. Protocol editing must depend on the chosen workflow.

### 6.1 Method Catalog

Add:

```text
GET /api/v1/method-catalog
```

It should include:

- Backends: Gaussian, ORCA, xTB, CREST.
- Functionals: B3LYP, PBE0, M06-2X, wB97X-D, wB97X-D4, r2SCAN-3c, etc.
- Basis sets: def2-SVP, def2-TZVP, def2-TZVPP, def2-TZVPPD, 6-31G(d), cc-pVTZ, etc.
- Dispersion: D3BJ, D4, none.
- Solvation models: CPCM, SMD, ALPB/GBSA for xTB where applicable.
- Grids, SCF convergence, max cycles, frequency options.
- Backend capability flags.

### 6.2 Workflow-Specific Panels

Single point:

- Backend
- Functional
- Basis
- Dispersion
- Solvent
- Charge/multiplicity
- SCF settings

Geometry optimization:

- All single point fields
- Optimization convergence
- Max steps
- Constraint options in later phase

Frequency:

- Method/basis
- Temperature/pressure
- Frequency scale factor
- Imaginary frequency handling
- Thermochemistry output

Confsearch:

- xTB pre-optimization level
- CREST settings
  - GFN level
  - solvent
  - energy window
  - RMSD threshold
  - conformer count limit
- DFT optimization stage
  - backend
  - functional
  - basis
  - dispersion
  - solvent
- Single point refinement stage
  - functional
  - basis
  - DLPNO/CCSD(T) options where applicable
- Thermochemistry stage
  - Shermo settings
  - temperature/pressure

NMR:

- conformer source
- shielding backend
- GIAO settings
- reference calibration
- Boltzmann threshold
- report output format

### 6.3 Presets Plus Advanced

Every workflow should expose:

- A compact preset selector for routine use.
- An "Advanced" tab that shows concrete method fields.
- A final method summary before submit.

Example:

```text
Preset: censo-lite
Advanced:
  CREST: GFN2-xTB, ALPB solvent
  DFT opt: wB97X-D/def2-SVP
  SP: wB97X-D4/def2-TZVPPD
  Thermo: Shermo, 298.15 K
```

---

## 7. Batch Submission Semantics

Batch upload must be explicit and predictable.

### 7.1 Batch Modes

For an input batch containing N structures:

- One job per structure.
- One parent batch with N child jobs.
- Optional "single workflow consumes ensemble" only when workflow supports it.

Default:

```text
N structures -> N jobs
```

The review screen should say:

```text
This submission will create 24 jobs:
  24 x conformer workflow
  Project: Mechanism_A
  Max concurrent jobs: 2
```

### 7.2 JobSpec V2 Proposal

Do not overload `input.source` forever. Add a structured input payload:

```json
{
  "workflow": "conformer",
  "name": "batch_confsearch",
  "input": {
    "source_type": "input_batch",
    "batch_id": "batch_20260628_001",
    "structure_ids": ["str_001", "str_002"],
    "expansion": "one_job_per_structure"
  },
  "method": {
    "profile_id": "censo-lite",
    "stages": {}
  },
  "resources": {
    "nproc": 16,
    "mem": "32GB",
    "max_concurrent": 2
  }
}
```

Compatibility:

- Keep accepting `input.source` for old frontend and API clients.
- New frontend should submit structured input.

---

## 8. Frontend Implementation Plan

### 8.1 P0: Replace Current Modal With Intake Wizard

Tasks:

- Replace SMILES-only modal body.
- Add segmented input mode control.
- Add drag/drop upload zone.
- Add paste editor for XYZ/GJF/INP/MOL/SDF.
- Add structure preview table.
- Add parse/validate button before submit.
- Add multi-structure detection and per-row charge/spin/name editing.
- Keep submission routed to existing workflows initially.

P0 does not need full custom workflow execution.

### 8.2 P1: Task Catalog And Method Editor

Tasks:

- Replace workflow select with cards grouped by category.
- Add method editor panel driven by workflow type.
- Add detailed Confsearch editor.
- Add simple task types to backend where feasible.
- Add `/api/v1/workflow-catalog` and `/api/v1/method-catalog`.

### 8.3 P2: Custom Sequence Builder

Tasks:

- Add ordered task list builder.
- Add task block schema and validation.
- Add save/load workflow templates.
- Add backend `custom_sequence` runner.

### 8.4 P3: ChemDraw And Image-Assisted Input

Tasks:

- Support `.cdx` / `.cdxml` through an optional parser.
- Support image recognition only with an explicit review gate.
- Add manual correction UI before submission.

---

## 9. Backend Implementation Plan

### 9.1 New Intake Package

Add parser functions:

- `parse_xyz_text`
- `parse_sdf_text`
- `parse_mol_text`
- `parse_gjf_text`
- `parse_orca_inp_text`
- `parse_smiles_table`
- `parse_upload`

Each parser returns `StructureAsset` records plus warnings/errors.

### 9.2 Upload Storage

Store under:

```text
ACP_runs/{project_id}/_uploads/{upload_id}/original/
ACP_runs/{project_id}/_uploads/{upload_id}/normalized/
```

The normalized folder can hold `structure.xyz`, `structure.mol`, parse metadata JSON, and thumbnails later.

### 9.3 Database Tables

Add:

- `uploads`
- `structure_assets`
- `input_batches`
- `input_batch_items`
- `workflow_templates`

Use the existing scheduler migration pattern.

### 9.4 Runner Changes

P0:

- Resolve structured input payloads into existing file paths before invoking current CLI workflows.

P1:

- Add simple workflow commands for singlepoint/optimize/frequency.

P2:

- Add custom sequence executor.

---

## 10. Acceptance Criteria

P0:

- User can submit a job from SMILES as before.
- User can upload `.xyz`, `.sdf`, `.mol`, `.gjf`, `.com`, `.inp`.
- Uploaded files are parsed into a preview table before submission.
- Multi-structure SDF creates multiple rows.
- Multi-frame XYZ prompts user to choose "trajectory/ensemble" or "split into structures".
- No job is created when parsing fails.
- Backend stores original upload and normalized structure.
- Old `input.source` submission remains compatible.

P1:

- Workflow catalog shows simple calculations and preset workflows.
- Confsearch exposes method-level controls for xTB/CREST/DFT/SP/thermo stages.
- Single point, optimize, frequency, and opt+freq have concrete method forms.
- Review screen shows exact number of jobs to create.

P2:

- User can create a linear custom task sequence.
- Backend validates task compatibility before submission.
- Templates can be saved and reused.

P3:

- ChemDraw CDX/CDXML import works.
- Image-assisted parsing requires user confirmation before job creation.

---

## 11. Risks And Guardrails

- Do not silently reinterpret failed structure input.
- Do not submit image-derived structures without review.
- Do not overload "protocol" with hidden method details.
- Do not make fake/demo artifacts look like scientific results.
- Do not allow uploaded paths to escape project storage.
- Keep old API clients working while the new structured input matures.

---

## 12. Recommended Immediate Next Step

Implement P0 as a narrow vertical slice:

1. Add upload/parse endpoints.
2. Add frontend input wizard with file upload and preview table.
3. Submit parsed single `.xyz` and `.sdf` structures into the existing conformer workflow.
4. Add strict tests for invalid file, multi-structure SDF, Gaussian input charge/multiplicity extraction, and old SMILES compatibility.

Only after this works should the work move to custom workflow building and ChemDraw image recognition.
