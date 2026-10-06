"""
Benchmark subpackage (todo 50 / gap G §10.3).

Harness for the ACP NMR benchmark: dataset-manifest schema with typed
provenance errors, layered metrics (per-nucleus MAE/RMSE, assignment
accuracy, Top-1, true-structure-absence behavior, DP5 Brier/log-loss and
calibration, failure/refusal, CPU/memory), molecule-clustered paired
bootstrap confidence intervals, and a pre-registered threshold file whose
content hash is verified before any comparison.

The spectra-processing layer reuses todo 48's interface
(``tests.nmr_spectra_benchmark``); this package wraps it without
re-implementing it.
"""
