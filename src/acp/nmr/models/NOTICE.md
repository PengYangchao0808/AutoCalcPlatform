# NMR Error-Model Assets

These files are redistributed from the **Goodman-lab/DP5** repository
(https://github.com/Goodman-lab/DP5), MIT-licensed (see `LICENSE-DP5`).

## Files

| File | Size | Source | Purpose |
|------|------|--------|---------|
| `folded_scaled_errors.p` | 851 KB | DP5 repo | 106 416 folded scaled residuals → per-atom DP5 KDE (DP5.py:98) |
| `c_w_kde_mean_s_0.025.p` | 80 KB | DP5 repo | "correct-assignment" weighted KDE (bandwidth 0.025) for `Rescale_DP5` |
| `i_w_kde_mean_s_0.025.p` | 24 MB | DP5 repo | "incorrect-assignment" weighted KDE (bandwidth 0.025) for `Rescale_DP5` |
| `tms_references.txt` | 4.6 KB | DP5 repo (`TMSdata`) | TMS ¹³C/¹H reference shieldings per (method, basis, solvent) |
| `atomic_reps.gz` | 22 MB | DP5 repo | 53 208 precomputed training-set atom FCHL19 representations → per-atom FCHL-weighted KDE (DP5.py:59,85-108). Used for molecules < 86 atoms. |
| `frag_reps.gz` | 18 MB | DP5 repo | Fragmented training-set FCHL representations (radius-3 fragments, `max_size=54`) for molecules ≥ 86 atoms (DP5.py:63-67,277-302). Requires openbabel. |

## Source revision

Fetched from `Goodman-lab/DP5` commit
`b6cf559007a5d13fe79654f37daf945ee1661a23` (2023-07-15). SHA-256:
`atomic_reps.gz` = `bb8f798c1dd898811801a9fdd9f4bf037e434968f66e050acf3fb616a7652a65`,
`frag_reps.gz` = `e5daae0f7b525632beed6cbd9b972b0be466f33e28c5cdb7efd1f1caaafb6c64`.

## FCHL runtime dependency

The FCHL-weighted DP5 path (`dp5_mode=fchl`) computes per-atom similarities with
`qml.fchl.get_atomic_kernels` and builds representations with
`qml.Compound(...).generate_fchl_representation`. Both require the **`qml`**
package (QMLKit, von Lilienfeld group), which is *not* a build dependency of
ACP. When `qml` is unavailable, DP5 silently degrades to the unweighted global
KDE fallback (`dp5_mode=fallback`) — see `acp.nmr.error_model`.

Note: `qml` currently only builds against `numpy<2` (it relies on the removed
`numpy.distutils` and needs a Fortran compiler). Verify availability on the
compute node before enabling the FCHL path.

## Loading

The `.p` files are `scipy.stats.gaussian_kde` pickles saved with an older
scipy version. They are **rebuilt at load time** from their stored
`dataset` + `weights` + `factor` attributes (see
`acp.nmr.error_model._rebuild_kde`), so the platform is robust to scipy
internal-API changes.

## DP4 parameters (from DP4.py:17-21)

The DP4 probability uses a **Gaussian** distribution (not Student-t):

```
σ_C = 2.269372270818724 ppm
σ_H = 0.18731058105269952 ppm
mean = 0.0
P(error) = 2 * Φ(-|error / σ|)
```

## Attribution

If you use DP4/DP5 probabilities in published work, cite:
- Smith & Goodman, *JACS* 2010, 132, 12946 (DP4)
- Howarth, Ermanis & Goodman, *Chem. Sci.* 2021 (DP5)
