# ACP 2.0 release version list (B25b, 2026-10-23)

Single version `0.3.0` across the four authoritative locations, the release manifest and the four installed distributions.

## Four version locations

| location | version |
|---|---|
| `pyproject.toml` | `0.3.0` |
| `src/acp/__init__.py` | `0.3.0` |
| `src/cccp/__init__.py` | `0.3.0` |
| `src/cccp/version.py` | `0.3.0` |

Installed verification: `reports/acp2_implementation/bb45a46/B/B25/version-locations.json` (all four `0.3.0`).

## Role artifacts (corrected B25b build)

| artifact | sha256 | bytes |
|---|---|---|
| `acp-web-0.3.0.tar.gz` | `c1798bda7df1f62d4647af5afd61d635504efe6175f84cfc08d3d36cf629dec5` | 2044660 |
| `acp-worker-0.3.0.tar.gz` | `a3ea29ea025719e404c7f36c83d193f0e59d05d02799ba478c5555347b978acc` | 580176 |
| `acp_web-0.3.0-py3-none-any.whl` | `55ecb5ea83d0fb0a4f911eb7c0d114495ee0cb8cb367eabee232248cf6907846` | 2230317 |
| `acp_worker-0.3.0-py3-none-any.whl` | `7d93873f4db823e4bd1edaa7be34be8558d8623cfb37cf4a311368110b2fa0c3` | 613821 |
| `cccp-0.3.0-py3-none-any.whl` | `70749f7673abd40ce99702700b779f109d4d6ff57e7af2891be4f695f6654ad3` | 44570219 |
| `cccp-0.3.0.tar.gz` | `5cb1f202dba608182be7e38a919d282faa63f3b781e1d6ea59ea0e46b3b32825` | 44429611 |
| `acp-0.3.0-py3-none-any.whl` (root) | `7cba73083e444f8e2be12176eebcbe16012aa4954a4d5052606d83ea6eaf484d` | 46401627 |

Original B25a artifacts are retained under `release-b25a-original/` and `root-build-b25a-original/`;
they are superseded because the first `acp_web` build could not start the Lab server (`OriginPolicy` mismatch).

## Node imports

No new `acp run` runtime import was added by B25b; `requirements-node.txt` remains authoritative for LSF nodes.
