"""Read-only ORCA parser pilot capability (plan T44 capability template).

Chosen template: :mod:`cccp.analysis.orca_parser` (plan T18), the single
read-only ORCA-output aggregate parse.  It is the natural pilot because it

* launches no program (path in → data out), so the built-in task layer
  remains the only execution station;
* has a clear Analysis seam (``OrcaOutputParser.parse`` / ``parse_text``);
* already delegates frequency science to
  :mod:`cccp.calculation.frequency_parse` instead of mirroring it.

The plugin is a thin adapter that calls the built-in object.  It never
replaces it, shadows it, or re-implements it; enabling the pilot only makes
the seam reachable through the plugin host.  It is disabled by default.

Author: QCcalc Team
"""

from __future__ import annotations

from pathlib import Path

from cccp.analysis.orca_parser import OrcaCalculation, OrcaOutputParser
from cccp.plugin.discovery import PluginCandidate
from cccp.plugin.metadata import (
    ApiCompatibility,
    ErrorSemantics,
    PluginCapability,
    PluginMetadata,
    ResourceRequirements,
    ScienceSoftwareVersion,
)
from cccp.version import __version__

ORCA_READONLY_PARSER_NAME = "orca_readonly_parser"
ORCA_READONLY_PARSER_SCHEMA = "orca_calculation"
ORCA_READONLY_PARSER_SCHEMA_VERSION = 1


def readonly_parser_metadata() -> PluginMetadata:
    """Return the frozen pilot metadata record.

    ``science_software[0].verified`` is ``False``: the ORCA version window is
    declared but not confirmed with a real binary in this environment
    (NOT_VERIFIED, never a pass).
    """
    return PluginMetadata(
        name=ORCA_READONLY_PARSER_NAME,
        distribution="cccp",
        version=__version__,
        api=ApiCompatibility(),
        capabilities=(
            PluginCapability(
                name="readonly_parser",
                schema=ORCA_READONLY_PARSER_SCHEMA,
                schema_version=ORCA_READONLY_PARSER_SCHEMA_VERSION,
            ),
        ),
        science_software=(
            ScienceSoftwareVersion(software="orca", version_range=">=5.0,<7", verified=False),
        ),
        resources=ResourceRequirements(requires_subprocess=False, requires_network=False),
        license="MIT",
        dependencies=(),
        error_semantics=ErrorSemantics(
            pre_launch_typed=True,
            partial_output_tolerant=True,
            never_overrides_builtin=True,
        ),
        default_enabled=False,
    )


class OrcaReadonlyParserPlugin:
    """Thin read-only adapter over the built-in :class:`OrcaOutputParser`."""

    def __init__(self) -> None:
        self._parser = OrcaOutputParser()

    def parse(self, path: Path) -> OrcaCalculation:
        """Parse *path* with the built-in parser (no program launch)."""
        return self._parser.parse(path)

    def parse_text(self, text: str) -> OrcaCalculation:
        """Parse ORCA output *text* with the built-in parser."""
        return self._parser.parse_text(text)


def _build_pilot() -> OrcaReadonlyParserPlugin:
    return OrcaReadonlyParserPlugin()


#: The pilot candidate.  Its loader is only called after an explicit
#: ``PluginHost.enable(ORCA_READONLY_PARSER_NAME)`` + ``load``.
PILOT_CANDIDATE = PluginCandidate(metadata=readonly_parser_metadata(), loader=_build_pilot)


__all__ = [
    "ORCA_READONLY_PARSER_NAME",
    "ORCA_READONLY_PARSER_SCHEMA",
    "ORCA_READONLY_PARSER_SCHEMA_VERSION",
    "OrcaReadonlyParserPlugin",
    "PILOT_CANDIDATE",
    "readonly_parser_metadata",
]
