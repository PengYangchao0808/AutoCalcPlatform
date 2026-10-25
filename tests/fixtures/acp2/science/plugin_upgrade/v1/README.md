# Constructed ACP2 plugin upgrade pair — v1 (1.0.0)

This directory is one half of a **constructed** v1/v2 distribution pair used by
A-23 to prove plugin upgrade safety and historical read-only artifact access.
It is not a production plugin: the capability validates a fixed JSON
single-point record and never runs a QC program.

- distribution: `acp2-upgrade-science-validator` 1.0.0
- entry-point group: `cccp.science_plugins` (version 1)
- entry point: `acp2_upgrade_validator = acp2_upgrade_fixture.plugin:PLUGIN`
- capability: `acp2_upgrade_native_singlepoint` (`pure_analysis`, schema version 1)
- trust: `untrusted`; installation or enablement never promotes it

Build offline with `python -m build --wheel --no-isolation --outdir dist .` or
`pip wheel --no-deps --no-index --no-build-isolation -w dist .`. The build
backend recomputes both integrity digests from the source tree.
