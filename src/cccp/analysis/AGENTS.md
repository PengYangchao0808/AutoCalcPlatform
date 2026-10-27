# analysis/ — Science-Analysis Home (ACP 2.0, 2026-10)

## OVERVIEW
The science-analysis subpackage (**87 .py, 47.9k lines**) — created in ACP 2.0 as the single home for domain science that is *not* subprocess execution: NMR/DP4/DP5/FCHL (moved here from `acp/nmr/`, which is now a forwarder surface), research evidence/provenance/question/reaction contracts, PES path analysis, ensemble thermochemistry, bounded science products, structure intake types, confsearch selection science, tsmode bundles, xTB-MD sampling, retired xtbmd helpers. `__init__.py` is docstring-only (no exports — root `cccp/__init__.py` same exception). Never imports `acp`.

## STRUCTURE
```
analysis/
├── identity.py           # ScientificIdentity two-layer fingerprint; identity_fingerprint = "v2:"+sha256[:32]
├── orca_parser.py        # OrcaOutputParser / OrcaCalculation (frequency science delegates to cccp.calculation.frequency_parse)
├── frequency_products.py # build_normal_modes_product / build_frequency_report
├── nmr/                  # 27 .py + model_assets/ — FULL DP4/DP5/FCHL home (see below)
├── research/             # 11 .py + contracts/ corpus — evidence/provenance/question/reaction/selectivity (see below); science_criteria (ADR-008 M1-05 冻结判据表+评估器)
├── pes/                  # 8: path profiles, atom mapping, bond changes, seed selection, topology guards
├── ensemble/             # 8: retired-engine ensemble science + A-24b science-protocol binding (science.py)
├── bounded/              # 7: chunked/subsampled/multi-resolution science products
├── structure/            # 6: six-format intake + RDKit embedding + Structure base types + NMR topology capture
├── confsearch/           # 5: Boltzmann weights, refinement-policy selection, manifest entry builders
├── tsmode/               # 5: frequency-source bundles, mode mapping, saddle validation
├── sampling/             # 4: xTB-MD sampling_history_v1 (traj parse, equilibration, basins, MDS, saturation)
└── xtbmd/                # 3: retired xtbmd_censo_energy pure helpers + GFN1 energy filter
```

## WHERE TO LOOK
| Concern | Location | Notes |
|---|---|---|
| **NMR experimental models** | `nmr/models.py` (2.2k L) | `ExperimentalNmr`, `ProcessedSpectrum` 4 layers (Acquisition→ProcessingProvenance/Quality→SpectralLine→ResonanceSignal), `CandidateResult`, `NmrReport`, `check_digital_filter`, `assess_processing` |
| FCHL representation | `nmr/fchl.py` (1.5k L) | pure-numpy FCHL19: `generate_fchl_representation`, `get_atomic_kernels_numpy`, `atom_probability_fchl`, `FchlKernelAvailability`, `_BoundedKernelCache` |
| DP4/DP5 error models | `nmr/error_model.py` | `GoodmanErrorModel` (Gaussian DP4, σ_C 2.269372 / σ_H 0.187311), `GoodmanDP5Model` (KDE over folded_scaled_errors), `Dp5ProbabilityRecord` |
| DP4/DP5 probability | `nmr/probability.py` | `compute_dp4`, `normalize_dp4_gated`, `compute_dp5_goodman` |
| Assignment / averaging | `nmr/{assignment,iterative_assignment,equivalence,averaging}.py` | two-phase Hungarian lock+match; G11 seeded assignment↔calibration iteration; symmetry groups; Boltzmann averaging |
| Line processing | `nmr/{carbon_processor,proton_processor}.py` | layer-3 line fitting / multiplet deconvolution |
| Atomic diagnostics | `nmr/atomic_diagnostics.py` (825 L) | `Dp4Decomposition`, `ConflictMatrix`, `LeaveOneSignalOut` |
| NMR protocol (A-24) | `nmr/protocol.py` | 6 segments (Sampling/Geometry/PopulationEnergy/Shielding/Reference/StatisticalModel); `build_protocol_spec`; `method_config.py::resolve_nmr_method` = single param resolver |
| NMR shielding comparison | `nmr/comparison.py` (1.3k L, A-24a) | `compare_nmr_shielding`, `NmrComparisonOutcome`; validator ids NMR_MAPPING/REFERENCE/COVERAGE/CALIBRATION/MODEL_ASSET/EXPERIMENT_SOURCE |
| DP5q isolation | `nmr/dp5q_stub.py` | `Dp5qStubPredictor` — never auto-registers |
| **Model assets** | `nmr/assets.py` | **single locator**: `asset_dir()`, `resolve_asset()`, `MODEL_ASSETS_DIRNAME`; data dir `nmr/model_assets/` (8 files: atomic_reps.gz, frag_reps.gz, 3×*.p, tms_references.txt, NOTICE.md, LICENSE-DP5) — no other access path |
| Evidence contracts | `research/evidence.py` (1.7k L) | `EvidenceReport`, `Claim` (4-valued status), `ClaimDraft`, `assemble_claim/evidence_report`, `reject_model_conclusion_as_evidence`, `ModelNarrative`, `verify_artifact_digest` |
| Provenance | `research/provenance.py` | `ScientificProvenance`, Method/Basis/ElectronicState identity, `PureAnalysisDerivation` (QC fields forbidden), `PluginIdentity` |
| Deterministic validators | `research/validators.py` (4.7k L) | ~16 validators: opt convergence, frequency membership/evidence, method/state consistency, TS frequency-count/mapped-mode/connectivity, IRC bidirectional/connectivity, **energy + activation-energy comparability** (thermochemistry comparability), stationary-point checks |
| Reaction/question | `research/{reaction,question}.py` | `Reaction`, `AtomMapping` (bijection), `BondChange`, `Stereochemistry`, `InputUncertainty`; `Question`, `ComparisonConditions`, `ElectronicStateRef` + read_historical_* readers |
| Selectivity audit | `research/selectivity.py` (A-25b) | `build_selectivity_audit`, `CrossDomainBudgetTable` — consumes B23b artifacts, never submits jobs |
| Artifact manifest | `research/artifact_manifest.py` (A-12a) | `load_verified_snapshot`, `ArtifactSnapshot/Ref`, `ArtifactScienceIdentity`, path-escape/digest errors |
| **Contract corpus** | `research/contracts.py` + `research/contracts/` | read-only access (`contract_root/list_contracts/read_contract`); packaged corpus byte-identical to `docs/reference/next-change/acp2-contracts/science/` (7 schemas + evidence-semantics.md + semantic-rules.md + examples); drift-guarded by `tests/test_acp2_science_installed_package.py` |
| Ensemble thermochemistry | `ensemble/science.py` (A-24b) | `build_ensemble_thermochemistry`, weight modes raw/normalized; wraps `ensemble/thermo.py` (ensemble_total_gibbs, mixing_entropy) + `qc/thermo_normalize` |
| Canonical JSON/errors | `research/_common.py` | stdlib-only `ScienceContractError`, `canonical_json`, `reject_forbidden_keys` |

## CONVENTIONS
- **acp/nmr is a forwarder surface**: 11 modules there are `sys.modules` aliases to `cccp.analysis.nmr.*`; NMR science is implemented HERE only — never re-implement acp-side (root ANTI #30/#34)
- **Assets single locator**: all model-asset access goes through `nmr/assets.py`; hardcoded `model_assets` paths are forbidden
- **Contract corpus is frozen**: `research/contracts/` must stay byte-identical to the docs copy; changes go through both + drift guard
- **Evidence ≠ narrative**: validators are deterministic; `model`/`llm`-kind evidence is rejected (`reject_model_conclusion_as_evidence`); claims carry a 4-valued status (PASS/FAIL/NOT_VERIFIED semantics)
- **Frozen dataclasses** for contracts; `PureAnalysisDerivation` must not carry QC method/basis fields
- `nmr/__init__.py` (579 L) is additive re-export only — no implementation (ANTI #5)

## ANTI-PATTERNS
- **Importing acp** — hard fail (root ANTI #17/#29 mirror)
- **Second digest implementation** — artifact/provenance digests use the corpus canonical form; do not fork
- **Guessing science defaults** — analysis layer consumes published recipes/evidence by id+digest; defaults live in `cccp.calculation`
- **Historical readers are read-only** — `read_historical_{reaction,question,evidence,recipe}` never mutate or re-validate as current
