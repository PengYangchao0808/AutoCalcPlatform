# C-owned DSH overlay drop

`acceptance-env-c.sh::dsh_acceptance()` passes every `*.yml` in this directory
to the frozen DSH launcher as a `--patch` overlay, after the profile's own
patch and the home-level patch.

This directory is owned by the C track (`experience/**`). T-05/C02 populates it
from `rph/packages/dsh-bundle`; no DSH core source is forked or copied here.
Overlays must not embed credentials or absolute paths outside `$ACP2_*`.
